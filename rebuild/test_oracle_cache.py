"""Tests for the keys of the persisted per-row oracle cache in rebuild/pipeline/oracle_cache.py. They cover whether a row may be served: the whole-store stamp, the per-family key, the staleness mask, the age cap and verification sample that stop a wrong record from being re-served indefinitely, the promotion refusal, and the reader's refusal of any store it cannot parse whole. rebuild/test_conform.py covers what the oracle writes when it serves a row.

No test here depends on a glyph, so none needs the live build. The rune tree, schema, and registry are copied from the mini bundle into a tmp root that each test may edit, and the spec is `fixtures.mini_spec()`. Like every rebuild test, these must not read `rebuild/out/`.

The stamp tests matter most. A per-family key covers only the routes that stay inside one rune file. Every route that crosses the registry (a predicate class gaining a member, a rune-local group, a ligature's declared sequence, a capability unlock, the registry's families and heights, the engine's settlement flags) must move the whole-store stamp instead. `specificity::family_set` expands a `class:` reference to its full member set and `compare_axes` ranks two records by whether each axis set of one is a subset of the other's, so a rune joining or leaving a class can change the settlement of a window that names no such rune. Each route is checked to move its own named stamp line and no other.
"""

import gzip
import shutil
from array import array
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import conform, fingerprint, fixtures, kernel_exec, oracle_cache, run_m1
from rebuild.tools import artifact_cycle
from rebuild.validation.rowmodel import Row

REPO_ROOT = Path(__file__).resolve().parent.parent

# An alias map in the live file's shape, cut down to heads the mini rune tree has a file for, plus the two boundary heads, which have no family key.
ALIAS_MAP = {
    "space": "boundary",
    "periodcentered": "boundary",
    "qsPea": {"rune": "qsPea", "stance": "full"},
    "qsPea.half.ex-y5": {"rune": "qsPea", "stance": "half", "exit": "x-height"},
    "qsTea": {"rune": "qsTea", "stance": "full"},
    "qsTea.en-y0": {"rune": "qsTea", "stance": "full", "entry": "baseline"},
    "qsOy": {"rune": "qsOy", "stance": "loop"},
}

PEA = 0xE650
TEA = 0xE652
OY = 0xE679


def _alias(root: Path) -> Path:
    return root / "rebuild" / "m1-aliases.yaml"


def _write_alias(root: Path, entries: dict) -> Path:
    path = _alias(root)
    path.write_text(yaml.safe_dump(entries, sort_keys=True), encoding="utf-8")
    return path


def _script(root: Path) -> Path:
    return root / "rebuild" / "script.yaml"


def _rewrite_script(root: Path, edit=None) -> None:
    """Round-trip the registry through the YAML loader, applying `edit` to the parsed document. Every variant a stamp test compares is written this way, so the `data` line's raw byte hash changes only for the edit. A text patch compared against the unformatted original would also change the hash through formatting alone."""
    document = yaml.safe_load(_script(root).read_text(encoding="utf-8"))
    if edit is not None:
        edit(document)
    _script(root).write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")


@pytest.fixture
def repo(mini_bundle, tmp_path) -> Path:
    """Return an editable repo root: the mini bundle's runes, schema, and registry copied under `tmp_path`, with an alias map beside them. The registry is already round-tripped, so only a later edit changes its bytes."""
    root = tmp_path / "root"
    shutil.copytree(mini_bundle.spec_root, root)
    _rewrite_script(root)
    _write_alias(root, ALIAS_MAP)
    return root


def _keys(root: Path, spec) -> dict[str, str]:
    return oracle_cache.family_keys(root, spec, _alias(root))


def _stamp(root: Path, spec, config: str = "default") -> oracle_cache.EnvironmentStamp:
    return oracle_cache.environment_stamp(
        root,
        spec,
        config,
        conform.features_for_config(config),
        root / f"baseline-{config}.subset.tsv.gz",
        _alias(root),
        _keys(root, spec).keys(),
    )


def _perturb_rune(path: Path) -> None:
    """Make a geometric edit to a rune file: the first stance with a bitmap gains or loses its top-left pixel. The edit must be geometry, because the rune digest a family key uses is prose-blind. A comment, a `ductus` rewrite, or a new `notes` paragraph would leave the key unchanged."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    stance = next(stance for stance in document["stances"].values() if stance.get("bitmap"))
    row = stance["bitmap"][0]
    stance["bitmap"][0] = (" " if row[0] == "#" else "#") + row[1:]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


# --- the stamp's global routes ----------------------------------------------------------


def _predicate_class(root: Path, spec, monkeypatch):
    classes = dict(spec.registry.predicate_classes)
    classes["talls"] = frozenset(classes["talls"] | {"qsIt"})
    return root, replace(spec, registry=replace(spec.registry, predicate_classes=classes))


def _rune_group(root: Path, spec, monkeypatch):
    rune = spec.runes["qsIt"]
    groups = {name: frozenset(members | {"qsPea"}) for name, members in rune.policy.groups.items()}
    assert groups, "the mini spec's qsIt is the rune-local group route and has lost its groups"
    runes = dict(spec.runes)
    runes["qsIt"] = replace(rune, policy=replace(rune.policy, groups=groups))
    return root, replace(spec, runes=runes)


def _ligature_sequence(root: Path, spec, monkeypatch):
    runes = dict(spec.runes)
    runes["qsTea_qsOy"] = replace(runes["qsTea_qsOy"], sequence=("qsTea", "qsPea"))
    return root, replace(spec, runes=runes)


def _capability_unlock(root: Path, spec, monkeypatch):
    rune = spec.runes["qsTea"]
    stance = rune.stances["full"]
    unlocks = tuple(
        replace(unlock, feature="ss09") if index == 0 else unlock
        for index, unlock in enumerate(stance.surface.unlocks)
    )
    assert unlocks, "the mini spec's qsTea.full is the capability-unlock route and has lost its unlocks"
    stances = dict(rune.stances)
    stances["full"] = replace(stance, surface=replace(stance.surface, unlocks=unlocks))
    runes = dict(spec.runes)
    runes["qsTea"] = replace(rune, stances=stances)
    return root, replace(spec, runes=runes)


def _registry_families(root: Path, spec, monkeypatch):
    _rewrite_script(root, lambda document: document["families"].update({"qsNewcomer": {"codepoint": 59100}}))
    return root, spec


def _registry_height(root: Path, spec, monkeypatch):
    _rewrite_script(root, lambda document: document["heights"].update({"x-height": 4}))
    return root, spec


def _settlement_flags(root: Path, spec, monkeypatch):
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", not kernel_exec.SIMULATED_PROSPECT_DEFAULT)
    return root, spec


SPEC_GLOBAL_ROUTES = {
    "a predicate class gaining a member": (_predicate_class, "spec_structure"),
    "a rune-local policy group": (_rune_group, "spec_structure"),
    "a ligature's declared sequence": (_ligature_sequence, "spec_structure"),
    "a stance's capability unlock": (_capability_unlock, "capability_features"),
    "the registry's families": (_registry_families, "data"),
    "the registry's heights": (_registry_height, "data"),
    "the engine's settlement flags": (_settlement_flags, "settlement_flags"),
}


@pytest.mark.parametrize("route", sorted(SPEC_GLOBAL_ROUTES))
def test_the_stamp_moves_with_each_spec_global_route(route, repo, monkeypatch):
    """Each route a per-family key cannot cover must move its own stamp line and only that line. A route whose own line stopped covering it, but which happens to change a neighboring line, would still move the stamp value today and would stop moving it once the neighbor was narrowed."""
    spec = fixtures.mini_spec()
    base = _stamp(repo, spec)
    assert _stamp(repo, spec).lines == base.lines
    mutate, label = SPEC_GLOBAL_ROUTES[route]
    moved_root, moved_spec = mutate(repo, spec, monkeypatch)
    now = _stamp(moved_root, moved_spec)
    assert now.value != base.value
    assert oracle_cache.moved_note(base.labels, now.labels) == f"{label} (changed)"


def test_the_stamp_carries_the_configuration_and_its_features(repo):
    """The stores for all acceptance configurations are written side by side under one run's keys, so the `config` and `features` lines are what keep one configuration's records from being served to another. They are stamped directly because the only other difference would be the subset table's digest, and the run does not control that file."""
    spec = fixtures.mini_spec()
    stamps = {config: _stamp(repo, spec, config) for config in conform.ACCEPTANCE_CONFIGS}
    assert len({stamp.value for stamp in stamps.values()}) == len(conform.ACCEPTANCE_CONFIGS)
    default, ss03 = stamps["default"].labels, stamps["ss03"].labels
    assert oracle_cache.moved_note(default, ss03) == "config (changed), features (changed)"


def test_the_stamp_folds_none_of_the_inputs_the_comparison_re_reads_every_pass(repo):
    """The stamp must not include an input the comparison re-reads on every pass, because such an input can change without making any record stale, and including it would drop the whole store for nothing. A value comparison cannot detect this. The alias map is covered by the per-family keys and its own `alias_boundary` line, the divergence ledger is re-read by classification, and the kern sidecar is re-read by the position channel. Each is checked to be a data input that `stamped_data_paths` actually excludes, not merely a path the stamp does not reach."""
    spec = fixtures.mini_spec()
    base = _stamp(repo, spec)
    (repo / "glyph_data" / "senior_quikscript_kerning.yaml").write_text("pairs: []\n", encoding="utf-8")
    stamped = set(oracle_cache.stamped_data_paths(repo))
    assert _script(repo) in stamped
    for name in (
        "glyph_data/senior_quikscript_kerning.yaml",
        "rebuild/m1-aliases.yaml",
        "rebuild/m1-divergences.yaml",
    ):
        path = repo / name
        assert path in fingerprint.data_paths(repo), f"{name} is no longer a data input at all"
        assert path not in stamped
    assert not stamped & set(fingerprint.rune_paths(repo))
    assert _stamp(repo, spec).lines == base.lines


def test_blessing_a_contact_signature_leaves_the_whole_store_stamp_untouched(repo):
    """No oracle stage reads the contact allow-list (the defect gate does), and it changes often, so stamping it would drop the whole store on a two-line bless. It needs no exclusion because it is outside `fingerprint.data_paths`, and the test asserts that directly instead of relying on an exclusion entry. An exclusion entry for a path the stamp never reads would keep passing even if the stamp later started reading the file some other way."""
    spec = fixtures.mini_spec()
    allow = repo / fingerprint.CONTACT_ALLOW_LABEL
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y1'}\n", encoding="utf-8")
    base = _stamp(repo, spec)
    assert allow not in fingerprint.data_paths(repo)
    assert allow not in set(oracle_cache.stamped_data_paths(repo))
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y2'}\n", encoding="utf-8")
    assert _stamp(repo, spec).lines == base.lines
    assert oracle_cache.moved_note(base.labels, _stamp(repo, spec).labels) is None


# --- the family key's grain --------------------------------------------------------------


def test_a_ligature_rune_edit_invalidates_only_rows_carrying_all_its_components():
    """A ligature rune declares a `sequence` and no codepoint, so no row's codepoints name it, yet `settle.form_ligatures` routes the pair through its file. A key over only the families of a row's codepoints would therefore miss a ligature edit. The ligature clause fires when all the components' bits are present and ignores order: a row with the components in the other order can never form the ligature but is marked stale anyway. Over-invalidation is the safe direction, and a mask cheap enough to test on every row cannot check order."""
    spec = fixtures.mini_spec()
    tea_oy = (TEA, OY)
    tea_pea = (TEA, PEA)
    for moved, expected in (
        ({"qsTea_qsOy"}, {tea_oy}),
        ({"qsPea"}, {tea_pea}),
        ({"qsSee"}, set()),
    ):
        mask = oracle_cache.StaleMask(spec, moved)
        assert {row for row in (tea_oy, tea_pea) if mask.stale(mask.mask_of(row))} == expected

    ligature = oracle_cache.StaleMask(spec, {"qsTea_qsOy"})
    assert not ligature.stale(ligature.mask_of((TEA,)))
    assert not ligature.stale(ligature.mask_of((OY,)))
    assert ligature.stale(ligature.mask_of((OY, TEA)))
    assert "qsTea_qsOy" in ligature.families_of(ligature.mask_of(tea_oy))
    assert "qsTea_qsOy" not in ligature.families_of(ligature.mask_of(tea_pea))


def test_a_ligature_rune_edit_stales_the_windows_carrying_its_formed_label():
    """Settle-memo windows hold formed labels, so a window with a `qsTea_qsOy` label names the ligature rune and neither component, and the component clause alone would serve that window after an edit to `qsTea_qsOy.yaml`. The formed label's bit must read as moved on its own, while a single component's bit must not. An edit to a component must not make stale a window that names only the formed ligature, because the edit did not touch the ligature's file."""
    spec = fixtures.mini_spec()
    ligature = oracle_cache.StaleMask(spec, {"qsTea_qsOy"})
    formed = ligature.bit_of("qsTea_qsOy")
    assert formed and ligature.stale(formed)
    assert not ligature.stale(ligature.bit_of("qsTea"))
    assert not ligature.stale(ligature.bit_of("qsOy"))
    assert ligature.stale(ligature.bit_of("qsTea") | ligature.bit_of("qsOy"))
    assert ligature.bit_of("#EDGE") == 0

    component = oracle_cache.StaleMask(spec, {"qsTea"})
    assert component.stale(component.bit_of("qsTea"))
    assert not component.stale(component.bit_of("qsTea_qsOy"))


def test_a_moved_family_the_registry_cannot_place_stales_every_row():
    """A moved name with neither a codepoint nor a sequence, such as a rune file for a family the registry does not list yet, can reach rows in ways the bits cannot describe, so it makes every row stale."""
    spec = fixtures.mini_spec()
    mask = oracle_cache.StaleMask(spec, {"qsNotInTheRegistry"})
    assert mask.everything
    assert mask.stale(mask.mask_of((PEA,)))
    assert mask.stale(0)


def test_an_alias_edit_stales_only_the_families_its_keys_name(repo):
    """The alias map is keyed per family, so changing one family's entries re-derives that family's rows and nothing else. The two boundary heads are the exception: they can never reach a verdict and have no family key, so they are stamped by the whole-store stamp's `alias_boundary` line."""
    spec = fixtures.mini_spec()
    before = _keys(repo, spec)
    base = _stamp(repo, spec)

    _write_alias(repo, {**ALIAS_MAP, "qsTea.en-y0": {"rune": "qsTea", "stance": "half", "entry": "y6"}})
    assert oracle_cache.moved_families(before, _keys(repo, spec)) == frozenset({"qsTea"})

    _write_alias(repo, {**ALIAS_MAP, "qsPea.half.ex-y5": "pending"})
    assert oracle_cache.moved_families(before, _keys(repo, spec)) == frozenset({"qsPea"})

    _write_alias(repo, {**ALIAS_MAP, "periodcentered.lowered": "boundary"})
    assert oracle_cache.moved_families(before, _keys(repo, spec)) == frozenset()
    assert oracle_cache.moved_note(base.labels, _stamp(repo, spec).labels) == "alias_boundary (changed)"


def test_an_alias_head_with_no_family_key_raises(repo):
    """An alias head that names a registry family with no rune file must raise. Such a head would pass a check that it names a family, but it has no key, so it could never be reported moved and its alias entries would be stamped by nothing. This check keeps the alias map's heads within the family keys.

    The test picks the family instead of naming one, because a named family stops showing the property once the mini bundle gains a rune file for it. Any registry family without a rune file in the mini tree works. If every registry family has a rune file, `next` raises `StopIteration` and this test must change.
    """
    spec = fixtures.mini_spec()
    unmigrated = next(name for name in sorted(spec.registry.families) if name not in _keys(repo, spec))
    _write_alias(repo, {**ALIAS_MAP, f"{unmigrated}.en-y0": {"rune": unmigrated, "stance": "full"}})
    with pytest.raises(ValueError, match=unmigrated):
        oracle_cache.alias_family_digests(_alias(repo), _keys(repo, spec).keys())
    with pytest.raises(ValueError, match=unmigrated):
        _keys(repo, spec)
    with pytest.raises(ValueError, match=unmigrated):
        _stamp(repo, spec)


def test_a_cited_family_absent_from_the_recorded_keys_is_a_miss(repo, tmp_path):
    """A family present in only one of the two key maps counts as moved. Comparing with `recorded.get(name) == current.get(name)` would treat two absences as equal, and the case that matters is a name present on one side only: a new ligature rune file for two letters already in the alphabet. Reading that as agreement would serve the pre-ligature verdict for every row of the pair."""
    recorded = {"qsPea": "p0", "qsTea": "t0"}
    current = {"qsPea": "p0", "qsTea": "t0", "qsTea_qsOy": "to0"}
    assert oracle_cache.moved_families(recorded, current) == frozenset({"qsTea_qsOy"})
    assert oracle_cache.moved_families(current, recorded) == frozenset({"qsTea_qsOy"})
    assert oracle_cache.moved_families(recorded, recorded) == frozenset()
    assert oracle_cache.moved_families({}, {}) == frozenset()

    spec = fixtures.mini_spec()
    stamp = _stamp(repo, spec)
    path = tmp_path / "store.tsv.gz"
    with oracle_cache.RowWriter(path, stamp, "subset-digest", 0, recorded) as writer:
        for row in ((TEA, OY), (TEA, PEA)):
            writer.append(row, None, 0)
    store = oracle_cache.load_store(path, stamp, "subset-digest", spec, current)
    assert store is not None
    assert store.moved == frozenset({"qsTea_qsOy"})
    assert store.mask.stale(store.mask.mask_of((TEA, OY)))
    assert not store.mask.stale(store.mask.mask_of((TEA, PEA)))


# --- the promotion refusal ---------------------------------------------------------------


def _stage(scratch: Path, stamps, keys) -> None:
    for config in conform.ACCEPTANCE_CONFIGS:
        path = oracle_cache.scratch_store_path(scratch, config)
        with oracle_cache.RowWriter(path, stamps[config], "subset-digest", 0, keys) as writer:
            writer.append((PEA,), None, 0)


def _promoted(out_dir: Path) -> set[str]:
    return {
        config for config in conform.ACCEPTANCE_CONFIGS if oracle_cache.store_path(out_dir, config).is_file()
    }


def test_the_stamp_is_snapshotted_before_the_work_and_reverified_at_promotion(
    repo, tmp_path, monkeypatch, capsys
):
    """An input edited during the run must stop promotion, because otherwise the store would be accepted as current indefinitely. The keys are computed before the first row is compared and again at promotion. If a rune, the alias map, or the registry changed in between, the run's verdicts match nothing on disk, so promotion writes nothing and names what moved. A run whose inputs did not change promotes every acceptance configuration; without that control, "nothing was promoted" would only show that promotion is broken."""
    monkeypatch.setattr(run_m1, "REPO_ROOT", repo)
    monkeypatch.setattr(run_m1, "ALIAS_YAML", _alias(repo))
    spec = fixtures.mini_spec()
    out_dir = tmp_path / "m1"
    keys, stamps = run_m1.oracle_row_cache_keys(spec, out_dir)

    steady = tmp_path / "steady"
    _stage(steady, stamps, keys)
    run_m1._promote_oracle_row_cache(spec, out_dir, steady, keys, stamps)
    assert _promoted(out_dir) == set(conform.ACCEPTANCE_CONFIGS)
    assert "written for" in capsys.readouterr().out

    rune = repo / "glyph_data" / "runes" / "qsPea.yaml"
    original = rune.read_bytes()
    _perturb_rune(rune)
    edited_rune = tmp_path / "edited-rune"
    _stage(edited_rune, stamps, keys)
    run_m1._promote_oracle_row_cache(spec, tmp_path / "m1-rune", edited_rune, keys, stamps)
    message = capsys.readouterr().out
    assert _promoted(tmp_path / "m1-rune") == set()
    assert "not written" in message and "qsPea (changed)" in message
    assert all(
        oracle_cache.scratch_store_path(edited_rune, config).is_file()
        for config in conform.ACCEPTANCE_CONFIGS
    )

    rune.write_bytes(original)
    _rewrite_script(repo, lambda document: document["heights"].update({"y6": 7}))
    edited_registry = tmp_path / "edited-registry"
    _stage(edited_registry, stamps, keys)
    run_m1._promote_oracle_row_cache(spec, tmp_path / "m1-registry", edited_registry, keys, stamps)
    message = capsys.readouterr().out
    assert _promoted(tmp_path / "m1-registry") == set()
    assert "not written" in message and "data (changed)" in message


def test_an_alias_map_edited_into_a_shape_the_guard_refuses_promotes_nothing(
    repo, tmp_path, monkeypatch, capsys
):
    """If an alias head loses its family key during the run, the key computation at promotion raises instead of reporting a change. That raise must end in the same "not written" result, not a traceback that stops the run after the audit has been promoted. An alias map edited into invalid YAML raises `yaml`'s own error instead of `ValueError` and must be handled the same way."""
    monkeypatch.setattr(run_m1, "REPO_ROOT", repo)
    monkeypatch.setattr(run_m1, "ALIAS_YAML", _alias(repo))
    spec = fixtures.mini_spec()
    out_dir = tmp_path / "m1"
    keys, stamps = run_m1.oracle_row_cache_keys(spec, out_dir)
    scratch = tmp_path / "scratch"
    _stage(scratch, stamps, keys)

    _write_alias(repo, {**ALIAS_MAP, "qsBay.en-y0": {"rune": "qsBay", "stance": "full"}})
    run_m1._promote_oracle_row_cache(spec, out_dir, scratch, keys, stamps)
    assert _promoted(out_dir) == set()
    assert "not written" in capsys.readouterr().out

    _alias(repo).write_text("qsPea.half: [unclosed\n", encoding="utf-8")
    run_m1._promote_oracle_row_cache(spec, out_dir, scratch, keys, stamps)
    assert _promoted(out_dir) == set()
    assert "not written" in capsys.readouterr().out


# --- the age cap and the verification sample ---------------------------------------------


def _row(index: int, glyph: str = "g") -> Row:
    return Row(
        codepoints=(0xE650 + index,), glyphs=(glyph,), clusters=(0,), seams=(), positions=((0, 0, 100),)
    )


def test_the_verification_sample_covers_every_serving_family_and_rotates():
    """Every family that served a row is sampled on every pass, which catches an error affecting a whole family with probability one instead of with probability sample size over served rows. A rune edited during the run produces that kind of error. A family with fewer served rows than the cap contributes all of them. The draw is a pure function of the stamp, the family, and the pass ordinal, so the order rows are offered in cannot change it, and seeding on the ordinal makes consecutive passes check different rows.

    Rotation is checked as coverage that accumulates, not as disjoint draws: the draws of eight rows out of a hundred at ordinals 0 and 1 overlap in this case. The requirement is that a row not checked on one pass is checked a few passes later, which the union over ordinals 0 and 2 through 11 shows.
    """
    stamp = "stamp-value"
    served: dict[int, tuple[str, ...]] = {index: ("qsPea", "qsTea") for index in range(100)}
    served.update({100: ("qsSee",), 101: ("qsSee",), 102: ("qsSee",)})
    cap = oracle_cache.VERIFICATION_SAMPLE_PER_FAMILY

    sample = oracle_cache.VerificationSample(stamp, 0)
    for index, families in served.items():
        sample.offer(index, _row(index), families)
    drawn = sample.by_family()
    assert set(drawn) == {"qsPea", "qsTea", "qsSee"}
    assert len(drawn["qsPea"]) == len(drawn["qsTea"]) == cap
    assert drawn["qsSee"] == (100, 101, 102)
    assert sample.indexes() == tuple(sorted({index for kept in drawn.values() for index in kept}))

    shuffled = oracle_cache.VerificationSample(stamp, 0)
    for index in sorted(served, reverse=True):
        shuffled.offer(index, _row(index), served[index])
    assert shuffled.by_family() == drawn

    later = oracle_cache.VerificationSample(stamp, 1)
    for index, families in served.items():
        later.offer(index, _row(index), families)
    rotated = later.by_family()
    assert set(rotated) == set(drawn)
    assert rotated["qsPea"] != drawn["qsPea"]
    assert len(set(rotated["qsPea"]) | set(drawn["qsPea"])) > cap
    assert rotated["qsSee"] == drawn["qsSee"]

    covered = set(drawn["qsPea"])
    for ordinal in range(2, 12):
        pass_sample = oracle_cache.VerificationSample(stamp, ordinal)
        for index, families in served.items():
            pass_sample.offer(index, _row(index), families)
        covered |= set(pass_sample.by_family()["qsPea"])
    assert len(covered) > 4 * cap

    elsewhere = oracle_cache.VerificationSample("another-stamp", 0)
    for index, families in served.items():
        elsewhere.offer(index, _row(index), families)
    assert elsewhere.by_family()["qsPea"] != drawn["qsPea"]


def test_the_verification_sample_hands_back_the_rows_it_drew():
    """Each drawn entry keeps the parsed row it was offered with, so neither verifier re-reads the table. `sampled_rows()` returns the offered objects, once per index however many families drew it, in the order `indexes()` returns. The row does not affect the draw: two samples fed the same indexes with different rows draw the same families. It also never takes part in heap comparisons. `Row` has no ordering, and the distinct index in every `(score, index, row)` entry stops a tuple comparison before it reaches the row."""
    stamp = "stamp-value"
    served: dict[int, tuple[str, ...]] = {index: ("qsPea", "qsTea") for index in range(100)}
    served.update({100: ("qsSee", "qsZoo"), 101: ("qsSee", "qsZoo"), 102: ("qsSee", "qsZoo")})
    rows = {index: _row(index) for index in served}

    sample = oracle_cache.VerificationSample(stamp, 0)
    for index, families in served.items():
        sample.offer(index, rows[index], families)
    picked = sample.sampled_rows()
    assert [index for index, _ in picked] == list(sample.indexes())
    assert all(row is rows[index] for index, row in picked)
    drawn = sample.by_family()
    assert drawn["qsSee"] == drawn["qsZoo"] == (100, 101, 102)
    assert [index for index, _ in picked if index >= 100] == [100, 101, 102]

    relabeled = oracle_cache.VerificationSample(stamp, 0)
    for index, families in served.items():
        relabeled.offer(index, _row(index, glyph="other"), families)
    assert relabeled.by_family() == sample.by_family()
    assert all(row is not rows[index] for index, row in relabeled.sampled_rows())

    with pytest.raises(TypeError):
        _ = (0, 0, rows[0]) < (0, 0, rows[1])
    assert (0, 0, rows[0]) < (0, 1, rows[1])


def _store_at(tmp_path: Path, repo: Path, spec, pass_ordinal: int, ages, name: str) -> oracle_cache.RowStore:
    stamp = _stamp(repo, spec)
    path = tmp_path / f"{name}.tsv.gz"
    keys = _keys(repo, spec)
    with oracle_cache.RowWriter(path, stamp, "subset-digest", pass_ordinal, keys) as writer:
        for index, age in enumerate(ages):
            writer.append((PEA, PEA + index), None, age)
    store = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys)
    assert store is not None
    return store


def test_a_record_older_than_the_age_cap_is_re_derived(repo, tmp_path):
    """No verdict may stand for `MAX_RECORD_AGE` passes without being recomputed, whatever its families did. This bounds how long a wrong record can survive in a store that otherwise re-writes it unchanged on every pass. Two clauses enforce it. The ordinal clause retires one row in `MAX_RECORD_AGE` on every pass, so the whole table is renewed within that many passes and the renewal is spread out. The age clause catches a record whose pass ordinals skipped."""
    spec = fixtures.mini_spec()
    rows = 2 * oracle_cache.MAX_RECORD_AGE
    fresh = [1] * rows

    retired: set[int] = set()
    for ordinal in range(oracle_cache.MAX_RECORD_AGE):
        store = _store_at(tmp_path, repo, spec, ordinal, fresh, f"pass-{ordinal}")
        due = {index for index in range(rows) if store.due(index)}
        assert len(due) == rows // oracle_cache.MAX_RECORD_AGE
        retired |= due
    assert retired == set(range(rows))

    aged = _store_at(tmp_path, repo, spec, oracle_cache.MAX_RECORD_AGE, fresh, "aged")
    assert all(aged.due(index) for index in range(rows))

    mixed = _store_at(tmp_path, repo, spec, 100, [100, 100, 80, 82], "mixed")
    assert mixed.age(0) == 100 and not mixed.due(0)
    assert mixed.due(1)
    assert mixed.due(2)
    assert not mixed.due(3)


def test_a_read_only_pass_rotates_the_slice_it_retires(repo, tmp_path):
    """A read-only pass does not change the ordinal on disk, so without rotation every such pass would retire the same slice of the table and draw the same verification sample. Rotation moves both, and `MAX_RECORD_AGE` rotations still cover the table exactly once. Rotation must not change the age arithmetic: a rotated current ordinal would read every record as past the cap and retire the whole table, which would remove the saving the store provides."""
    spec = fixtures.mini_spec()
    rows = 2 * oracle_cache.MAX_RECORD_AGE
    stamp = _stamp(repo, spec)
    keys = _keys(repo, spec)
    path = tmp_path / "read-only.tsv.gz"
    with oracle_cache.RowWriter(path, stamp, "subset-digest", 3, keys) as writer:
        for index in range(rows):
            writer.append((PEA, PEA + index), None, 3)

    def opened(rotation: int) -> oracle_cache.RowStore:
        store = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, rotation)
        assert store is not None
        assert store.pass_ordinal == 3
        return store

    covered: set[int] = set()
    ordinals: set[int] = set()
    for rotation in range(oracle_cache.MAX_RECORD_AGE):
        store = opened(rotation)
        due = {index for index in range(rows) if store.due(index)}
        assert len(due) == rows // oracle_cache.MAX_RECORD_AGE
        assert due.isdisjoint(covered)
        covered |= due
        ordinals.add(store.coverage_ordinal)
    assert covered == set(range(rows))
    assert len(ordinals) == oracle_cache.MAX_RECORD_AGE

    distant = opened(1_000_003)
    due = {index for index in range(rows) if distant.due(index)}
    assert len(due) == rows // oracle_cache.MAX_RECORD_AGE


def test_a_served_row_keeps_the_age_it_was_derived_at(repo, tmp_path):
    """The age measures how long a verdict has stood, not how old the file is, so a pass that only served a row writes the age it read and not its own ordinal. Writing its own ordinal would give a stale verdict a fresh age on every pass, which the age cap exists to prevent and which the store cannot detect afterward."""
    spec = fixtures.mini_spec()
    first = _store_at(repo=repo, tmp_path=tmp_path, spec=spec, pass_ordinal=0, ages=[0, 0], name="first")
    stamp = _stamp(repo, spec)
    keys = _keys(repo, spec)
    carried = tmp_path / "carried.tsv.gz"
    with oracle_cache.RowWriter(carried, stamp, "subset-digest", 1, keys) as writer:
        writer.append((PEA, PEA), first.serve(0, (PEA, PEA)).row, first.age(0))
        writer.append((PEA, PEA + 1), None, writer.pass_ordinal)
    store = oracle_cache.load_store(carried, stamp, "subset-digest", spec, keys)
    assert store is not None
    assert store.pass_ordinal == 1
    assert (store.age(0), store.age(1)) == (0, 1)


def test_a_segment_writer_writes_records_only_and_the_join_puts_the_frame_around_them(repo, tmp_path):
    """A row range of a cut configuration writes a segment: one gzip member of records with no header and no trailer, so the parent can put a header member before the segments and a trailer member after them without decompressing anything. The joined payload equals the single-member store's payload, `load_store` reads it across members, and the joined header records the pass ordinal the segments were written under."""
    spec = fixtures.mini_spec()
    stamp = _stamp(repo, spec)
    keys = _keys(repo, spec)
    whole = tmp_path / "whole.tsv.gz"
    with oracle_cache.RowWriter(whole, stamp, "subset-digest", 4, keys) as writer:
        for index in range(6):
            writer.append((PEA, PEA + index), None, 4)
    scratch = tmp_path / "scratch"
    for segment, rows in ((0, range(0, 2)), (1, range(2, 6))):
        path = oracle_cache.scratch_store_path(scratch, "default", segment)
        with oracle_cache.RowWriter(path, stamp, "subset-digest", 4, keys, segment=True) as writer:
            for index in rows:
                writer.append((PEA, PEA + index), None, 4)
        payload = gzip.decompress(path.read_bytes())
        assert not payload.startswith(b"{") and oracle_cache.ROW_COUNT_TRAILER.encode() not in payload
        assert payload.count(b"\n") == len(rows)
    joined = oracle_cache.join_store_segments(scratch, "default", 2, stamp, "subset-digest", 4, keys, 6)
    assert joined is not None and joined == oracle_cache.scratch_store_path(scratch, "default")
    assert gzip.decompress(joined.read_bytes()) == gzip.decompress(whole.read_bytes())
    store = oracle_cache.load_store(joined, stamp, "subset-digest", spec, keys)
    assert store is not None and store.rows == 6 and store.pass_ordinal == 4
    assert oracle_cache.next_pass_ordinal(store) == 5
    assert store.serve(5, (PEA, PEA + 5)) == oracle_cache.decode_record(
        gzip.decompress(whole.read_bytes()).decode().splitlines()[6]
    )
    assert oracle_cache.join_store_segments(scratch, "default", 3, stamp, "subset-digest", 4, keys, 6) is None


# --- the reader's refusals --------------------------------------------------------------


def _whole_store(tmp_path: Path, repo: Path, spec, rows: int = 2):
    """Write a well-formed store of `rows` records and return it with the stamp and keys `load_store` needs. The malformation tests below edit this store."""
    stamp = _stamp(repo, spec)
    keys = _keys(repo, spec)
    path = tmp_path / "store.tsv.gz"
    with oracle_cache.RowWriter(path, stamp, "subset-digest", 0, keys) as writer:
        for index in range(rows):
            writer.append((PEA, PEA + index), None, 0)
    return path, stamp, keys


def _rewrite_payload(path: Path, edit) -> None:
    """Recompress `path` after applying `edit` to its decompressed bytes. The member's mtime is not pinned because no test here compares the compressed bytes."""
    rewritten = edit(gzip.decompress(path.read_bytes()))
    with gzip.open(path, "wb") as stream:
        stream.write(rewritten)


def _lines(payload: bytes) -> list[bytes]:
    return payload.split(b"\n")


SLICES = ({}, {"first_row": 0, "stop_row": 1}, {"first_row": 1}, {"first_row": 5})
SLICE_IDS = ("whole", "head", "tail", "past-the-end")


@pytest.mark.parametrize("recompressed", [False, True], ids=["as-written", "recompressed-unedited"])
def test_a_whole_two_record_store_loads_and_serves(repo, tmp_path, recompressed):
    """Control for the refusal tests below: the store loads as written and also after `_rewrite_payload` rewrites it unchanged, so each refusal test fails only because of its malformation and not because of the helper. Together these tests check the refusals `load_store`'s docstring lists."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec)
    if recompressed:
        _rewrite_payload(path, lambda payload: payload)
    payload = gzip.decompress(path.read_bytes())
    assert _lines(payload)[-2] == f"{oracle_cache.ROW_COUNT_TRAILER}\t2".encode()
    store = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys)
    assert store is not None
    assert store.rows == 2
    for index in range(2):
        assert store.serve(index, (PEA, PEA + index)) == oracle_cache.decode_record(
            _lines(payload)[1 + index].decode("utf-8")
        )


@pytest.mark.parametrize("bounds", SLICES, ids=SLICE_IDS)
def test_a_whole_zero_record_store_loads_empty(repo, tmp_path, bounds):
    """A header line and the `#rows\t0` trailer with nothing between them, as left by a writer that appended no row, is a whole store and loads with no rows. It is the only valid body with no newline before the trailer, so a reader that locates the trailer by index alone would refuse it."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec, rows=0)
    payload = gzip.decompress(path.read_bytes())
    assert _lines(payload)[1:] == [f"{oracle_cache.ROW_COUNT_TRAILER}\t0".encode(), b""]
    store = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds)
    assert store is not None
    assert store.rows == 0


@pytest.mark.parametrize("bounds", SLICES, ids=SLICE_IDS)
def test_a_store_with_no_body_loads_as_none(repo, tmp_path, bounds):
    """A header line and nothing after it."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec)
    _rewrite_payload(path, lambda _: oracle_cache.store_header(stamp, "subset-digest", 0, keys))
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds) is None


@pytest.mark.parametrize("bounds", SLICES, ids=SLICE_IDS)
def test_a_store_whose_header_is_the_whole_file_loads_as_none(repo, tmp_path, bounds):
    """The header line with no newline after it, so the file has no body to partition off."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec)
    header = oracle_cache.store_header(stamp, "subset-digest", 0, keys)
    assert header.endswith(b"\n")
    _rewrite_payload(path, lambda _: header[:-1])
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds) is None


@pytest.mark.parametrize("bounds", SLICES, ids=SLICE_IDS)
def test_a_store_missing_its_trailer_loads_as_none(repo, tmp_path, bounds):
    """Two whole records with the row-count trailer line dropped: the last line is a record, not the trailer that confirms the length."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec)

    def drop_trailer(payload: bytes) -> bytes:
        lines = _lines(payload)
        assert lines[-2].startswith(oracle_cache.ROW_COUNT_TRAILER.encode())
        return b"\n".join(lines[:-2] + [b""])

    _rewrite_payload(path, drop_trailer)
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds) is None


@pytest.mark.parametrize("bounds", SLICES, ids=SLICE_IDS)
def test_a_truncated_store_loads_as_none(repo, tmp_path, bounds):
    """A store cut mid-record with no trailer after it, the shape a write that died leaves."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec)

    def cut_tail(payload: bytes) -> bytes:
        cut = payload[:-12]
        assert not cut.endswith(b"\n") and oracle_cache.ROW_COUNT_TRAILER.encode() not in cut
        return cut

    _rewrite_payload(path, cut_tail)
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds) is None


@pytest.mark.parametrize("bounds", SLICES, ids=SLICE_IDS)
def test_a_store_short_a_record_under_its_trailer_loads_as_none(repo, tmp_path, bounds):
    """The trailer counts two records over a body holding one, so the records and the count disagree."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec)

    def drop_record(payload: bytes) -> bytes:
        lines = _lines(payload)
        assert lines[-2] == f"{oracle_cache.ROW_COUNT_TRAILER}\t2".encode()
        return b"\n".join(lines[:1] + lines[2:])

    _rewrite_payload(path, drop_record)
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds) is None


# --- the ranged read ---------------------------------------------------------------------


def _aged_store(tmp_path: Path, repo: Path, spec, rows: int = 7):
    """Write a store of `rows` records under pass ordinal 3 whose row ages and position ages vary by row, so a record served from the wrong offset or an age read from the wrong slot gives a different result."""
    stamp = _stamp(repo, spec)
    keys = _keys(repo, spec)
    path = tmp_path / "aged.tsv.gz"
    with oracle_cache.RowWriter(path, stamp, "subset-digest", 3, keys) as writer:
        for index in range(rows):
            writer.append((PEA, PEA + index), None, 3 - index % 3, None, 3 - index % 2)
    return path, stamp, keys


RANGES = ({"first_row": 0, "stop_row": 3}, {"first_row": 2, "stop_row": 5}, {"first_row": 4})
RANGE_IDS = ("start", "middle", "open-ended-tail")


@pytest.mark.parametrize("bounds", RANGES, ids=RANGE_IDS)
def test_a_sliced_load_serves_its_range_as_a_whole_load_does(repo, tmp_path, bounds):
    """What a range keeps is served the same as the whole load serves it, under the row's absolute ordinal, for a range at the start of the table, one in the middle, and the open-ended last one. The refusal tests above cover the other half: all ranges of one configuration must agree on whether the store loads, so a malformation outside a range refuses the load as one inside it does."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _aged_store(tmp_path, repo, spec)
    whole = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys)
    sliced = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds)
    assert whole is not None and sliced is not None
    first_row = bounds["first_row"]
    stop_row = bounds.get("stop_row") or whole.rows
    assert (sliced.first_row, sliced.stop_row) == (first_row, stop_row)
    assert (whole.first_row, whole.stop_row) == (0, whole.rows)
    for index in range(first_row, stop_row):
        codepoints = (PEA, PEA + index)
        assert sliced.serve(index, codepoints) == whole.serve(index, codepoints)
        assert sliced.age(index) == whole.age(index)
        assert sliced.position_age(index) == whole.position_age(index)
        assert sliced.due(index) == whole.due(index)
        assert sliced.position_due(index) == whole.position_due(index)
    assert sliced.served == stop_row - first_row
    assert {whole.age(index) for index in range(whole.rows)} == {1, 2, 3}
    assert {whole.position_age(index) for index in range(whole.rows)} == {2, 3}


@pytest.mark.parametrize(
    "bounds", RANGES + ({"first_row": 7}, {"first_row": 9}), ids=RANGE_IDS + ("empty", "past-the-end")
)
def test_a_sliced_store_answers_the_tables_count_and_refuses_rows_outside_its_range(repo, tmp_path, bounds):
    """`rows` is the table's row count under every slice, because the oracle treats an index at or above it as fresh and never asks the store about it. An index the whole load treats as fresh is fresh under the slice, and no in-table index the slice treats as fresh is served by the whole load. An in-table index outside the slice raises instead of serving another row's record; without the check, a negative relative index would read the array from its end."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _aged_store(tmp_path, repo, spec)
    whole = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys)
    sliced = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds)
    assert whole is not None and sliced is not None
    assert sliced.rows == whole.rows == 7
    for index in (7, 8, 1_000):
        assert (index >= sliced.rows) is (index >= whole.rows) is True
    outside = [index for index in range(whole.rows) if not bounds["first_row"] <= index < sliced.stop_row]
    assert outside and all(index < sliced.rows for index in outside)
    for index in outside:
        whole.serve(index, (PEA, PEA + index))
        for reader in (sliced.age, sliced.position_age, sliced.due, sliced.position_due):
            with pytest.raises(IndexError):
                reader(index)
        with pytest.raises(IndexError):
            sliced.serve(index, (PEA, PEA + index))
    assert sliced.served == 0


def test_a_wrong_anchor_inside_the_range_still_aborts_the_serve(repo, tmp_path):
    """A misaligned anchor inside a slice aborts the serve as it does in a whole store; it is not a miss. The range changes which rows a store holds, not what a mismatched record means about the table."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _aged_store(tmp_path, repo, spec)
    sliced = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, first_row=2, stop_row=4)
    assert sliced is not None
    assert sliced.serve(3, (PEA, PEA + 3)).row_age == 3
    with pytest.raises(SystemExit, match="misaligned at row 3"):
        sliced.serve(3, (PEA, PEA + 4))


def test_a_store_given_no_count_holds_a_range_that_runs_to_the_tables_end(repo, tmp_path):
    """Without an explicit count, `rows` defaults to the range's end, not the slice's length. A store built at `first_row` with no count holds a range that runs to the table's end, so every row it holds is below `rows` and none is treated as fresh."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _aged_store(tmp_path, repo, spec)
    sliced = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, first_row=4)
    assert sliced is not None
    rebuilt = oracle_cache.RowStore(
        sliced.environment,
        sliced.recorded_lines,
        sliced.recorded_keys,
        sliced.subset_digest,
        sliced.pass_ordinal,
        sliced.mask,
        sliced._blob,
        sliced._offsets,
        sliced._ages,
        position_ages=sliced._position_ages,
        first_row=4,
    )
    assert (rebuilt.first_row, rebuilt.stop_row, rebuilt.rows) == (4, 7, 7)
    assert rebuilt.serve(6, (PEA, PEA + 6)) == sliced.serve(6, (PEA, PEA + 6))


def test_a_range_whose_kept_bytes_outrun_the_offsets_width_refuses_the_load(repo, tmp_path, monkeypatch):
    """The packed offsets are unsigned 32-bit, so a range that keeps more record bytes than they can address raises from the array, not from parsing. That error must also make `load_store` return `None`, which costs one uncached oracle run, and must not stop the build."""

    class Narrow(array):
        def append(self, value: int) -> None:
            if self.typecode == "I" and value > 100:
                raise OverflowError("unsigned int is greater than maximum")
            super().append(value)

    spec = fixtures.mini_spec()
    path, stamp, keys = _aged_store(tmp_path, repo, spec)
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, first_row=6) is not None
    monkeypatch.setattr(oracle_cache, "array", Narrow)
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, first_row=6) is not None
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys) is None


@pytest.mark.parametrize("bounds", SLICES, ids=SLICE_IDS)
def test_a_record_whose_age_will_not_parse_refuses_the_load_from_any_range(repo, tmp_path, bounds):
    """The reader parses the age fields of every record, in range or out, so a record whose age is not an integer refuses the load whether the range holds it, holds only earlier rows, or starts past the table's end."""
    spec = fixtures.mini_spec()
    path, stamp, keys = _whole_store(tmp_path, repo, spec, rows=3)

    def corrupt_last_record(payload: bytes) -> bytes:
        lines = _lines(payload)
        assert lines[-2] == f"{oracle_cache.ROW_COUNT_TRAILER}\t3".encode()
        fields = lines[-3].split(b"\t")
        fields[-2] = b"x"
        return b"\n".join(lines[:-3] + [b"\t".join(fields)] + lines[-2:])

    _rewrite_payload(path, corrupt_last_record)
    assert oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, **bounds) is None


# --- the store is not an artifact --------------------------------------------------------


def test_the_store_is_not_an_m1_artifact(tmp_path):
    """The store sits in `rebuild/out/m1` beside the artifacts but is not one of them. Its name matches neither `M1_ARTIFACT_NAMES` nor the subset-table glob, so it is in neither the run_m1 skip key (`run_m1_skip_lines`) nor the `m1_artifacts_present` check, and deleting it costs only time. The test checks the name against `M1_ARTIFACT_NAMES` and `_subset_tables` directly, not against a fixed string."""
    m1 = tmp_path / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    for name in artifact_cycle.M1_ARTIFACT_NAMES:
        (m1 / name).write_bytes(b"")
    for config in conform.ACCEPTANCE_CONFIGS:
        (m1 / f"baseline-{config}.subset.tsv.gz").write_bytes(b"")
        oracle_cache.store_path(m1, config).write_bytes(b"")

    stores = {oracle_cache.store_path(m1, config).name for config in conform.ACCEPTANCE_CONFIGS}
    assert stores.isdisjoint(artifact_cycle.M1_ARTIFACT_NAMES)
    assert {path.name for path in artifact_cycle._subset_tables(tmp_path)} == {
        f"baseline-{config}.subset.tsv.gz" for config in conform.ACCEPTANCE_CONFIGS
    }
    assert oracle_cache.scratch_store_path(tmp_path, "default").parent.name == oracle_cache.SCRATCH_SUBDIR


# --- the position store ------------------------------------------------------------------

MINI_FONT = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini" / "M1.otf"


def _font_edited(source: Path, target: Path, touches) -> Path:
    """Return a copy of `source` in which every glyph `touches` selects has its advance increased by 37 units, so a family's compiled digest changes through its metrics alone and every outline stays the same. The copy is written through fontTools, so both the shaper and the digest can read it."""
    from fontTools.ttLib import TTFont

    font = TTFont(str(source))
    metrics = font["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
    for name in list(metrics):
        if touches(name):
            advance, bearing = metrics[name]
            metrics[name] = (advance + 37, bearing)
    font.save(str(target))
    return target


def _position(repo: Path, spec, font: Path, kern: Path | None = None):
    return oracle_cache.position_keys(REPO_ROOT, _keys(repo, spec), font, kern)


def test_a_glyph_edit_moves_only_its_family_s_position_key(repo, tmp_path):
    """A family's compiled glyphs (outlines, advances, cursive anchors) move that family's position key and no other, while the font's helper glyphs, cmap, and GPOS wiring move the whole-store position stamp. The edit changes advances because that moves a position without changing a glyph name or a cell, which the row key cannot see."""
    spec = fixtures.mini_spec()
    base_keys, base_stamp = _position(repo, spec, MINI_FONT)
    glyphs, _helpers = fingerprint.after_font_glyph_digests(MINI_FONT)
    assert oracle_cache.position_family_keys(_keys(repo, spec), glyphs) == base_keys

    tea = _font_edited(MINI_FONT, tmp_path / "tea.otf", lambda name: name.split(".")[0] == "qsTea")
    keys, stamp = _position(repo, spec, tea)
    assert oracle_cache.moved_families(base_keys, keys) == frozenset({"qsTea"})
    assert stamp.lines == base_stamp.lines

    helper = _font_edited(MINI_FONT, tmp_path / "space.otf", lambda name: name == "space")
    keys, stamp = _position(repo, spec, helper)
    assert oracle_cache.moved_families(base_keys, keys) == frozenset()
    assert oracle_cache.moved_note(base_stamp.labels, stamp.labels) == "font_helpers (changed)"


def test_the_position_key_embeds_the_row_key(repo):
    """The position key embeds the row key, so a rune edit that moves a family's row key also moves its position key. The keys cover the union of the row-key and glyph-digest families, so a family the font has glyphs for but the rune tree has no file for still has a key."""
    spec = fixtures.mini_spec()
    row_keys = _keys(repo, spec)
    glyphs, _helpers = fingerprint.after_font_glyph_digests(MINI_FONT)
    base = oracle_cache.position_family_keys(row_keys, glyphs)
    assert set(base) == set(row_keys) | set(glyphs)
    _perturb_rune(repo / "glyph_data" / "runes" / "qsPea.yaml")
    assert oracle_cache.moved_families(
        base, oracle_cache.position_family_keys(_keys(repo, spec), glyphs)
    ) == frozenset({"qsPea"})


def test_the_position_stamp_names_the_channel_s_code_the_toolchain_and_the_kern_sidecar(repo, tmp_path):
    """Checks each line of the whole-store position stamp: the position channel's module, the toolchain lock that pins the shaper, and the kern sidecar's bytes each move their own named line and no other. The position stamp repeats nothing from the row stamp except `format`, so a store loads or is dropped on the row stamp alone, and the position stamp decides only whether the stored positions may be served. The classifier's module is copied beside the channel's and edited the same way, and the edit moves no line, so a classifier edit keeps every stored position. Both module edits add a statement, not a blank line, because `fingerprint.code_file_digest` is prose-blind and ignores comments, docstrings, and blank lines."""
    spec = fixtures.mini_spec()
    kern = tmp_path / "kern.yaml"
    kern.write_text("global:\n  value: 0\n", encoding="utf-8")
    module = repo / "rebuild" / "pipeline" / "oracle_positions.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "rebuild" / "pipeline" / "oracle_positions.py", module)
    classifier = repo / "rebuild" / "pipeline" / "oracle.py"
    shutil.copyfile(REPO_ROOT / "rebuild" / "pipeline" / "oracle.py", classifier)
    lock = repo / oracle_cache.TOOLCHAIN_LOCK
    lock.write_text("version = 1\n", encoding="utf-8")
    row_keys = _keys(repo, spec)

    def stamp() -> oracle_cache.EnvironmentStamp:
        return oracle_cache.position_keys(repo, row_keys, MINI_FONT, kern)[1]

    base = stamp()
    assert set(base.labels) == {"format", "position_code", "toolchain", "font_helpers", "kern"}
    assert set(base.labels) & set(_stamp(repo, spec).labels) == {"format"}
    assert stamp().lines == base.lines

    kern.write_text("global:\n  value: 3\n", encoding="utf-8")
    assert oracle_cache.moved_note(base.labels, stamp().labels) == "kern (changed)"
    kern.write_text("global:\n  value: 0\n", encoding="utf-8")

    module.write_text(module.read_text(encoding="utf-8") + "\nPERTURBED = 1\n", encoding="utf-8")
    assert oracle_cache.moved_note(base.labels, stamp().labels) == "position_code (changed)"
    shutil.copyfile(REPO_ROOT / "rebuild" / "pipeline" / "oracle_positions.py", module)

    classifier.write_text(classifier.read_text(encoding="utf-8") + "\nPERTURBED = 1\n", encoding="utf-8")
    assert oracle_cache.moved_note(base.labels, stamp().labels) is None
    assert stamp().lines == base.lines

    lock.write_text("version = 2\n", encoding="utf-8")
    assert oracle_cache.moved_note(base.labels, stamp().labels) == "toolchain (changed)"


def test_a_record_carries_both_verdicts_and_their_ages():
    """Round-trips every form of position verdict (not shaped, shaped with no drift, and drifted with descriptions that contain commas) alongside both forms of row verdict, each with its own age."""
    drifted = oracle_cache.CachedPosition(
        drifts=(
            "slot 1 (qsTea.en-y0): origin want (150, 0), got (100, 0)",
            "total advance: want 500, got 450",
        ),
        kern_attributable=True,
    )
    row = oracle_cache.CachedRow(
        kinds=("cell",),
        position=1,
        new_cells=("qsPea/full", "qsTea/half"),
        new_seams=("y5",),
        phenomena=("stance",),
    )
    for cached, position, ages in (
        (None, oracle_cache.UNSHAPED, (3, 3)),
        (None, None, (3, 4)),
        (row, drifted, (2, 5)),
        (row, oracle_cache.UNSHAPED, (7, 0)),
    ):
        line = oracle_cache.encode_record((PEA, TEA), cached, ages[0], position, ages[1])
        record = oracle_cache.decode_record(line)
        assert record.row == cached
        assert (
            record.position is position if position is oracle_cache.UNSHAPED else record.position == position
        )
        assert (record.row_age, record.position_age) == ages
        assert line.startswith(oracle_cache.row_anchor((PEA, TEA)))
    assert oracle_cache.encode_record((PEA,), None, 1).endswith("\t-\t?\t1\t0")


def test_a_moved_position_stamp_keeps_the_rows_and_retires_every_position(repo):
    """A store loads on the row stamp alone, and each position verdict is served only where the position stamp and every position key the row reaches are unchanged. A moved stamp makes every position stale and no row; a moved key makes stale the positions of the rows that reach that family; a pass with no position keys serves no position. `serve` returns `UNSHAPED` as stored, and the oracle never uses it as a served position."""
    spec = fixtures.mini_spec()
    stamp, keys = _stamp(repo, spec), _keys(repo, spec)
    position_keys, position_stamp = _position(repo, spec, MINI_FONT)
    rows = ((PEA, TEA), (TEA, OY), (PEA,))
    drifted = oracle_cache.CachedPosition(("slot 1 (qsTea): origin want (150, 0), got (100, 0)",), True)
    path = repo / "store.tsv.gz"
    with oracle_cache.RowWriter(
        path, stamp, "subset-digest", 5, keys, position_stamp, position_keys
    ) as writer:
        writer.append(rows[0], None, 5, drifted, 5)
        writer.append(rows[1], None, 5, None, 5)
        writer.append(rows[2], None, 5)

    def opened(environment, current) -> oracle_cache.RowStore:
        store = oracle_cache.load_store(path, stamp, "subset-digest", spec, keys, 0, environment, current)
        assert store is not None
        assert not any(store.due(index) for index in range(len(rows)))
        return store

    def stale(store: oracle_cache.RowStore) -> list[bool]:
        return [store.position_stale(index, store.mask.mask_of(row)) for index, row in enumerate(rows)]

    same = opened(position_stamp, position_keys)
    assert not same.position_mask.everything
    assert [same.serve(index, row).position for index, row in enumerate(rows)] == [
        drifted,
        None,
        oracle_cache.UNSHAPED,
    ]
    assert stale(same) == [False, False, False]

    moved_stamp = oracle_cache.EnvironmentStamp(lines=position_stamp.lines[:-1] + ("kern\tanother",))
    dropped = opened(moved_stamp, position_keys)
    assert dropped.position_mask.everything and not dropped.mask.everything
    assert stale(dropped) == [True, True, True]
    assert not any(dropped.stale(index, dropped.mask.mask_of(row)) for index, row in enumerate(rows))

    partial = opened(position_stamp, {**position_keys, "qsTea": "moved"})
    assert partial.position_mask.moved == frozenset({"qsTea"}) and partial.moved == frozenset()
    assert stale(partial) == [True, True, False]

    assert opened(None, None).position_mask.everything
    assert opened(position_stamp, None).position_mask.everything


def test_the_position_keys_are_reverified_at_promotion(repo, tmp_path, monkeypatch, capsys):
    """A font recompiled or a kern sidecar edited while the oracle was shaping means the positions match nothing on disk, so promotion writes nothing and names what moved, as it does for a rune edited during the run. The control promotes every acceptance configuration when the font and sidecar did not change."""
    monkeypatch.setattr(run_m1, "REPO_ROOT", repo)
    monkeypatch.setattr(run_m1, "ALIAS_YAML", _alias(repo))
    kern = tmp_path / "kern.yaml"
    kern.write_text("global:\n  value: 0\n", encoding="utf-8")
    monkeypatch.setattr(run_m1, "KERN_SIDECAR_YAML", kern)
    spec = fixtures.mini_spec()
    out_dir = tmp_path / "m1"
    out_dir.mkdir()
    shutil.copyfile(MINI_FONT, out_dir / "M1.otf")
    keys, stamps = run_m1.oracle_row_cache_keys(spec, out_dir)
    position_keys, position_stamp = run_m1.oracle_position_keys(keys, out_dir)
    assert position_keys is not None and position_stamp is not None
    assert run_m1.oracle_position_keys(keys, tmp_path / "nowhere") == (None, None)

    steady = tmp_path / "steady"
    _stage(steady, stamps, keys)
    run_m1._promote_oracle_row_cache(spec, out_dir, steady, keys, stamps, position_keys, position_stamp)
    assert _promoted(out_dir) == set(conform.ACCEPTANCE_CONFIGS)
    assert "written for" in capsys.readouterr().out
    promoted = {
        config: oracle_cache.store_path(out_dir, config).read_bytes() for config in conform.ACCEPTANCE_CONFIGS
    }

    _font_edited(MINI_FONT, out_dir / "M1.otf", lambda name: name.split(".")[0] == "qsTea")
    edited_font = tmp_path / "edited-font"
    _stage(edited_font, stamps, keys)
    run_m1._promote_oracle_row_cache(spec, out_dir, edited_font, keys, stamps, position_keys, position_stamp)
    message = capsys.readouterr().out
    assert "not written" in message and "positions qsTea (changed)" in message
    shutil.copyfile(MINI_FONT, out_dir / "M1.otf")

    kern.write_text("global:\n  value: 2\n", encoding="utf-8")
    edited_kern = tmp_path / "edited-kern"
    _stage(edited_kern, stamps, keys)
    run_m1._promote_oracle_row_cache(spec, out_dir, edited_kern, keys, stamps, position_keys, position_stamp)
    message = capsys.readouterr().out
    assert "not written" in message and "positions kern (changed)" in message
    assert {
        config: oracle_cache.store_path(out_dir, config).read_bytes() for config in conform.ACCEPTANCE_CONFIGS
    } == promoted


# --- the settle memo's keys ----------------------------------------------------------------


def test_the_settle_memo_keys_ignore_the_alias_map_and_move_per_family(repo):
    """The memo's per-family key is the row key without the alias line. The walk that fills the memo never reads the alias map, so changing a family's aliases keeps every settlement, while a geometric edit to one rune moves that family's key and no other."""
    spec = fixtures.mini_spec()
    base = oracle_cache.settle_family_keys(oracle_cache.settle_memo_inputs(repo), spec)
    assert set(base) == set(fingerprint.rune_digests(repo))
    _write_alias(repo, {**ALIAS_MAP, "qsTea.en-y0": {"rune": "qsTea", "stance": "half", "entry": "y6"}})
    assert oracle_cache.settle_family_keys(oracle_cache.settle_memo_inputs(repo), spec) == base
    assert oracle_cache.moved_families(_keys(repo, spec), _keys(repo, spec)) == frozenset()
    _perturb_rune(repo / "glyph_data" / "runes" / "qsPea.yaml")
    moved = oracle_cache.settle_family_keys(oracle_cache.settle_memo_inputs(repo), spec)
    assert oracle_cache.moved_families(base, moved) == frozenset({"qsPea"})


def test_the_settle_memo_stamp_moves_with_the_walk_s_routes_and_not_the_comparison_s(repo, monkeypatch):
    """The memo's whole-store stamp is the row stamp without the lines only the comparison reads. It moves with the configuration, its features, the walk's code closure, the non-rune data, the spec structure, and the settlement flags, and not with any edit to the alias map, the ledger, the kern sidecar, or a rune file. Each case is checked by the named line it moves or leaves unchanged."""
    spec = fixtures.mini_spec()
    inputs = oracle_cache.settle_memo_inputs(repo)
    base = oracle_cache.settle_memo_stamp(inputs, spec, "default", frozenset())
    assert set(base.labels) == {
        "config",
        "features",
        "oracle_code",
        "data",
        "spec_structure",
        "capability_features",
        "settlement_flags",
    }
    assert set(base.labels) < set(_stamp(repo, spec).labels)
    ss03 = oracle_cache.settle_memo_stamp(inputs, spec, "ss03", conform.features_for_config("ss03"))
    assert oracle_cache.moved_note(base.labels, ss03.labels) == "config (changed), features (changed)"

    _root, moved_spec = _predicate_class(repo, spec, monkeypatch)
    structure = oracle_cache.settle_memo_stamp(inputs, moved_spec, "default", frozenset())
    assert oracle_cache.moved_note(base.labels, structure.labels) == "spec_structure (changed)"

    (repo / "glyph_data" / "senior_quikscript_kerning.yaml").write_text("pairs: []\n", encoding="utf-8")
    _write_alias(repo, {**ALIAS_MAP, "periodcentered.lowered": "boundary"})
    (repo / "rebuild" / "m1-divergences.yaml").write_text("[]\n", encoding="utf-8")
    _perturb_rune(repo / "glyph_data" / "runes" / "qsPea.yaml")
    same = oracle_cache.settle_memo_stamp(oracle_cache.settle_memo_inputs(repo), spec, "default", frozenset())
    assert same.lines == base.lines

    _rewrite_script(repo, lambda document: document["heights"].update({"x-height": 4}))
    data = oracle_cache.settle_memo_stamp(oracle_cache.settle_memo_inputs(repo), spec, "default", frozenset())
    assert oracle_cache.moved_note(base.labels, data.labels) == "data (changed)"


_POSITION_LOCK = (
    "version = 1\n\n"
    '[[package]]\nname = "abbots-morton-spaceport"\nversion = "16.0.0"\nsource = { virtual = "." }\n\n'
    '[[package]]\nname = "uharfbuzz"\nversion = "0.50.2"\nsource = { registry = "https://pypi.org/simple" }\n'
)


def test_the_position_stamps_toolchain_line_reads_the_lock_by_its_dependency_pins(repo, tmp_path):
    """The lock's project block pins no shaper, so a project version bump leaves the position stamp, and every stored position, unchanged. A changed uharfbuzz pin moves only the `toolchain` line. Neither edit reaches a rune or a glyph, so the per-family keys stay the same in both cases."""
    spec = fixtures.mini_spec()
    kern = tmp_path / "kern.yaml"
    kern.write_text("global:\n  value: 0\n", encoding="utf-8")
    module = repo / "rebuild" / "pipeline" / "oracle_positions.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "rebuild" / "pipeline" / "oracle_positions.py", module)
    lock = repo / oracle_cache.TOOLCHAIN_LOCK
    lock.write_text(_POSITION_LOCK, encoding="utf-8")
    row_keys = _keys(repo, spec)

    def position():
        return oracle_cache.position_keys(repo, row_keys, MINI_FONT, kern)

    base_keys, base = position()
    lock.write_text(_POSITION_LOCK.replace('version = "16.0.0"', 'version = "16.1.0"'), encoding="utf-8")
    keys, stamp = position()
    assert keys == base_keys
    assert stamp.lines == base.lines
    lock.write_text(_POSITION_LOCK.replace('version = "0.50.2"', 'version = "0.51.0"'), encoding="utf-8")
    keys, stamp = position()
    assert keys == base_keys
    assert oracle_cache.moved_note(base.labels, stamp.labels) == "toolchain (changed)"
