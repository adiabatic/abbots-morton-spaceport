"""Tests for the persisted per-row oracle cache's keys (issue 24; rebuild/pipeline/oracle_cache.py is the contract). What is pinned here is the half of the design that decides *whether* a row may be served — the whole-store stamp, the per-family key, the staleness mask, the anti-laundering clauses and the promotion refusal — while rebuild/test_conform.py owns the half that decides what a served row then writes.

Every claim below is about a key or a store rather than about any glyph, so nothing here needs the live build: the rune tree, schema, and registry come from the frozen mini bundle's pin, materialized into a tmp root that each test may edit, and the spec is the hand-built mini one. This is the contracts lane and must stay in it.

The stamp tests are the load-bearing ones. A per-family key can only decompose the routes that stay inside one rune file; every route that reaches across the registry — a predicate class gaining a member, a rune-local group, a ligature's declared sequence, a capability unlock, the registry's own families and heights, the engine's settlement flags — has to move the whole-store stamp instead, because `specificity::family_set` expands a `class:` reference to its full member set and `compare_axes` ranks by set size, so a rune joining or leaving a class can flip the settlement of a window naming no such rune. Each of those routes is asserted to move a *named* stamp line, not merely to move the value: a route that quietly stops being covered and a route covered twice over look identical from the value alone, and only the first is a cache that serves stale rows in silence.
"""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import conform, fingerprint, fixtures, kernel_exec, oracle_cache, run_m1
from rebuild.tools import artifact_cycle

REPO_ROOT = Path(__file__).resolve().parent.parent

# An alias map in the live file's shape, cut down to heads the pinned rune tree holds a file for, plus the two boundary heads that deliberately have no family key of their own.
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
    """Round-trip the registry through the YAML loader, applying `edit` to the parsed document. Every variant a stamp test compares is written this way, so the `data` line's raw byte hash sees the edit and nothing else — a surgical text patch beside an untouched original would move the digest for its formatting alone and prove nothing."""
    document = yaml.safe_load(_script(root).read_text(encoding="utf-8"))
    if edit is not None:
        edit(document)
    _script(root).write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")


@pytest.fixture
def repo(mini_bundle, tmp_path) -> Path:
    """A repo root this test may edit: the pinned spec root's runes, schema, and registry copied under `tmp_path`, with an alias map written beside them. The registry arrives already round-tripped so a later edit is the only thing that moves it."""
    import shutil

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
    """A real geometric edit to a rune file: the first stance that draws anything gains or loses a pixel at the top left. It has to be geometry, because the digest a family key rides is prose-blind — a comment, a `ductus` rewrite, or a new `notes` paragraph would leave the key exactly where it was, which is the point of that digest and the trap for a test that reaches for the cheapest edit."""
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
    """The honesty test for exactly the effects a per-family key cannot decompose. Each route names the stamp line it is supposed to travel on, and the assertion is that it moves that line and only that line: a route whose own line stopped covering it but which happens to disturb a neighboring digest would still move the stamp today and would stop moving it the first time the neighbor was narrowed."""
    spec = fixtures.mini_spec()
    base = _stamp(repo, spec)
    assert _stamp(repo, spec).lines == base.lines
    mutate, label = SPEC_GLOBAL_ROUTES[route]
    moved_root, moved_spec = mutate(repo, spec, monkeypatch)
    now = _stamp(moved_root, moved_spec)
    assert now.value != base.value
    assert oracle_cache.moved_note(base.labels, now.labels) == f"{label} (changed)"


def test_the_stamp_carries_the_configuration_and_its_features(repo):
    """The stores — one per acceptance configuration — are written side by side under one run's keys, so nothing but these two lines keeps a configuration's records from being served to another. Both are here rather than left to the subset digest, which would be the only other thing separating them and which is a property of a file the run does not control."""
    spec = fixtures.mini_spec()
    stamps = {config: _stamp(repo, spec, config) for config in conform.ACCEPTANCE_CONFIGS}
    assert len({stamp.value for stamp in stamps.values()}) == len(conform.ACCEPTANCE_CONFIGS)
    default, ss03 = stamps["default"].labels, stamps["ss03"].labels
    assert oracle_cache.moved_note(default, ss03) == "config (changed), features (changed)"


def test_the_stamp_folds_none_of_the_inputs_the_comparison_re_reads_every_pass(repo):
    """The other half of the stamp's honesty, and the half a value comparison cannot see: an input the comparison re-reads over every row on every pass can move without staling one record, so folding it in only collapses the store for nothing. The alias map rides per-family keys and its own boundary line, the divergence ledger is re-read by classification, the kern sidecar is re-read by the position channel — and each is asserted to be a data input the exclusion actually reaches, not merely a path the fold happens to miss."""
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
    """The allow-list is the input this stamp reads the least and would pay the most for: no oracle stage opens it — it is the defect gate's — and it moves often enough that stamping it would collapse the whole store on a two-line bless. It needs no exclusion here, because it sits outside `fingerprint.data_paths` outright, which is asserted rather than assumed: an exclusion list naming a path the fold can no longer reach would go on reading as protection long after the fold had grown a second door."""
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
    """A ligature rune declares a `sequence` and no codepoint, so no row's codepoints ever name it — yet `settle.form_ligatures` routes the pair through its file, which is the correctness hole a key over "the families in the row's codepoints" leaves open. The clause fires on the components' bits together and is order-blind: a row holding the components in the other order can never form the ligature, and is stale anyway, because over-invalidation is the safe direction and a mask cheap enough to run on every row cannot afford to know about order."""
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


def test_a_moved_family_the_registry_cannot_place_stales_every_row():
    """The escape hatch under the mask: a moved name carrying neither a codepoint nor a sequence — a rune file appearing for a family the registry has not been taught yet — reaches rows by a route the bits cannot describe, so it stales all of them rather than none."""
    spec = fixtures.mini_spec()
    mask = oracle_cache.StaleMask(spec, {"qsNotInTheRegistry"})
    assert mask.everything
    assert mask.stale(mask.mask_of((PEA,)))
    assert mask.stale(0)


def test_an_alias_edit_stales_only_the_families_its_keys_name(repo):
    """The alias map is read per family, so retitling one family's entries re-derives that family's rows and nothing else. The two boundary heads are the exception the guard is built around: they can never reach a verdict, they have no family key to move, and so they ride the whole-store stamp's own `alias_boundary` line instead."""
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
    """The guard the unmigrated registry families exist to defeat: such a family would pass any "does this head name a family" check, but the rune tree holds no file for it, so it has no key — and a head with no key can never be reported moved, which would leave its entries stamped by nothing at all. The refusal is what keeps the alias map's heads and the family keys the same set.

    The family is chosen rather than named, because naming one makes the test an exemplar that dissolves the day that letter migrates — which is how it dissolved once already. Every registry family the pinned rune tree has no key for does equally well here, and the migration that empties that set is the migration that retires the guard.
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
    """`recorded[name]`, never `recorded.get(name) == current.get(name)` — under `.get` two absences compare equal, and the case that matters is precisely a name present on one side only: a new ligature rune file for two letters already in the alphabet is exactly that, and reading it as agreement serves the pre-ligature verdict for every row of the pair."""
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
    """The mid-run edit, which is the one failure that reads as green forever rather than failing once. The keys are cut before the first row is compared and cut again at promotion; a rune, an alias map, or a registry edited in between means the run built verdicts nothing on disk describes, and the answer is to promote nothing and name what moved. A run whose inputs held still promotes all six — the control, without which "nothing was promoted" proves only that promotion is broken."""
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
    """The same refusal by the other door: an alias head that loses its family key mid-run makes the promotion-time re-read raise rather than report a movement, and a raise out of the key computation must land as the same "nothing was written" rather than as a traceback that takes the whole run down after the audit has already been promoted. A map edited into something no parser will read at all takes the same door, since it arrives as `yaml`'s own error rather than as a `ValueError`."""
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


# --- the anti-laundering clauses ---------------------------------------------------------


def test_the_verification_sample_covers_every_serving_family_and_rotates():
    """Every family that served a row is checked on every pass, which is what catches a family-wide poisoning with probability one instead of with probability sample-over-served — the shape a rune edited mid-run produces. A family with fewer served rows than the cap contributes all of them; the draw is a pure function of the stamp, the family, and the pass ordinal, so the order rows are offered in cannot move it; and seeding on the ordinal makes consecutive passes cover different rows rather than re-proving the same fraction of a percent forever.

    What rotation is asserted as is coverage accumulating, not as disjointness: eight draws out of a hundred collide on this very case, and the claim the design rests on is that a row not checked this pass is checked a few passes later, which is what the union over eleven consecutive ordinals says.
    """
    stamp = "stamp-value"
    served: dict[int, tuple[str, ...]] = {index: ("qsPea", "qsTea") for index in range(100)}
    served.update({100: ("qsSee",), 101: ("qsSee",), 102: ("qsSee",)})
    cap = oracle_cache.VERIFICATION_SAMPLE_PER_FAMILY

    sample = oracle_cache.VerificationSample(stamp, 0)
    for index, families in served.items():
        sample.offer(index, families)
    drawn = sample.by_family()
    assert set(drawn) == {"qsPea", "qsTea", "qsSee"}
    assert len(drawn["qsPea"]) == len(drawn["qsTea"]) == cap
    assert drawn["qsSee"] == (100, 101, 102)
    assert sample.indexes() == tuple(sorted({index for kept in drawn.values() for index in kept}))

    shuffled = oracle_cache.VerificationSample(stamp, 0)
    for index in sorted(served, reverse=True):
        shuffled.offer(index, served[index])
    assert shuffled.by_family() == drawn

    later = oracle_cache.VerificationSample(stamp, 1)
    for index, families in served.items():
        later.offer(index, families)
    rotated = later.by_family()
    assert set(rotated) == set(drawn)
    assert rotated["qsPea"] != drawn["qsPea"]
    assert len(set(rotated["qsPea"]) | set(drawn["qsPea"])) > cap
    assert rotated["qsSee"] == drawn["qsSee"]

    covered = set(drawn["qsPea"])
    for ordinal in range(2, 12):
        pass_sample = oracle_cache.VerificationSample(stamp, ordinal)
        for index, families in served.items():
            pass_sample.offer(index, families)
        covered |= set(pass_sample.by_family()["qsPea"])
    assert len(covered) > 4 * cap

    elsewhere = oracle_cache.VerificationSample("another-stamp", 0)
    for index, families in served.items():
        elsewhere.offer(index, families)
    assert elsewhere.by_family()["qsPea"] != drawn["qsPea"]


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
    """No verdict may stand for `MAX_RECORD_AGE` passes without being recomputed, whatever its families did — the bound on how long a wrong record could survive a store that otherwise re-emits it verbatim forever. Two clauses carry it: the ordinal clause retires one row in the cap every pass, so the whole table is renewed within a cap's worth of passes and the renewal is spread rather than arriving all at once, and the age clause catches a record whose pass ordinals skipped."""
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
    """Nothing a read-only pass does moves the ordinal on disk, so without a rotation every such pass would retire the same twentieth of the table and draw the same verification sample — forever, on the one entry point that serves every row it reads. The rotation moves both, and a cap's worth of them still covers the table exactly once. What it must not touch is the age arithmetic: a rotated `current` would read every record as past the cap and retire the whole table, which is the savings this pass exists for."""
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
    """The age measures how long a verdict has stood, not how long the file has, so a pass that only served a row writes the age it read rather than its own ordinal. Writing its own would hand a stale verdict fresh provenance on every pass, which is the laundering the cap exists to stop and which nothing inside the store can detect after the fact."""
    spec = fixtures.mini_spec()
    first = _store_at(repo=repo, tmp_path=tmp_path, spec=spec, pass_ordinal=0, ages=[0, 0], name="first")
    stamp = _stamp(repo, spec)
    keys = _keys(repo, spec)
    carried = tmp_path / "carried.tsv.gz"
    with oracle_cache.RowWriter(carried, stamp, "subset-digest", 1, keys) as writer:
        writer.append((PEA, PEA), first.serve(0, (PEA, PEA)), first.age(0))
        writer.append((PEA, PEA + 1), None, writer.pass_ordinal)
    store = oracle_cache.load_store(carried, stamp, "subset-digest", spec, keys)
    assert store is not None
    assert store.pass_ordinal == 1
    assert (store.age(0), store.age(1)) == (0, 1)


# --- the store is not an artifact --------------------------------------------------------


def test_the_store_is_not_an_m1_artifact(tmp_path):
    """The store sits in `rebuild/out/m1` beside the artifacts and is not one of them: it rides neither `M1_ARTIFACT_NAMES` nor the subset-table glob, so it enters neither the validators-lane key nor the artifacts-present check and a cycle that deletes it loses time and nothing else. Asserted against the two live readers rather than against the string, because the name is a contract only insofar as those globs miss it."""
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
