"""Tests for the settle memo's keys in rebuild/pipeline/settle_memo_keys.py: the whole-memo stamp, the per-family keys, and the staleness mask a reader builds over them.

No test here depends on a glyph, so none needs the live build. The rune tree, schema, and registry are copied from the mini bundle into a tmp root that each test may edit, and the spec is `fixtures.mini_spec()`. Like every rebuild test, these must not read `rebuild/out/`.

A per-family key covers only the routes that stay inside one rune file. Every route that crosses the registry (a predicate class gaining a member, a rune-local group, a ligature's declared sequence, a capability unlock, the registry's families and heights, the engine's settlement flags) must move the stamp instead. `specificity::family_set` expands a `class:` reference to its full member set and `compare_axes` ranks two records by whether each axis set of one is a subset of the other's, so a rune joining or leaving a class can change the settlement of a window that names no such rune. Each route is checked to move its own named stamp line and no other.
"""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import conform, fingerprint, fixtures, kernel_exec, settle_memo_keys

PEA = 0xE650
TEA = 0xE652
OY = 0xE679


def _script(root: Path) -> Path:
    return root / "rebuild" / "script.yaml"


def _rewrite_script(root: Path, edit=None) -> None:
    """Round-trip the registry through the YAML loader, applying `edit` to the parsed document. Every variant a stamp test compares is written this way, with sorted keys, so the `data` line, which keeps key order, changes only for the edit."""
    document = yaml.safe_load(_script(root).read_text(encoding="utf-8"))
    if edit is not None:
        edit(document)
    _script(root).write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")


@pytest.fixture
def repo(mini_bundle, tmp_path) -> Path:
    """Return an editable repo root: the mini bundle's runes, schema, and registry copied under `tmp_path`. The registry is already round-tripped, so only a later edit changes its bytes."""
    root = tmp_path / "root"
    shutil.copytree(mini_bundle.spec_root, root)
    _rewrite_script(root)
    return root


def _stamp(root: Path, spec, config: str = "default"):
    return settle_memo_keys.settle_memo_stamp(
        settle_memo_keys.settle_memo_inputs(root), spec, config, conform.features_for_config(config)
    )


def _perturb_rune(path: Path) -> None:
    """Make a geometric edit to a rune file: the first stance with a bitmap gains or loses its top-left pixel. The edit must be geometry, because the rune digest a family key uses is prose-insensitive. A comment, a `ductus` rewrite, or a new `notes` paragraph would leave the key unchanged."""
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
    assert fingerprint.moved_note(base.labels, now.labels) == f"{label} (changed)"


def test_the_stamp_carries_the_configuration_and_its_features(repo):
    """Every settlement configuration's memo is written side by side under one run's keys, so the `config` and `features` lines are what keep one configuration's windows from being served to another."""
    spec = fixtures.mini_spec()
    stamps = {config: _stamp(repo, spec, config) for config in conform.SETTLEMENT_CONFIGS}
    assert len({stamp.value for stamp in stamps.values()}) == len(conform.SETTLEMENT_CONFIGS)
    default, ss03 = stamps["default"].labels, stamps["ss03"].labels
    assert fingerprint.moved_note(default, ss03) == "config (changed), features (changed)"
    assert set(stamps["default"].labels) == {
        "config",
        "features",
        "code",
        "data",
        "spec_structure",
        "capability_features",
        "settlement_flags",
    }


def test_the_stamp_folds_neither_the_rune_files_nor_an_input_no_settlement_reads(repo):
    """The rune files are covered per family, and an input no settlement reads can change without making any memo entry stale, so stamping it would drop the whole memo for nothing. Each such input is checked to be a data input that `stamped_data_paths` actually excludes, not merely a path the stamp does not reach."""
    spec = fixtures.mini_spec()
    base = _stamp(repo, spec)
    (repo / "glyph_data" / "senior_quikscript_kerning.yaml").write_text("pairs: []\n", encoding="utf-8")
    (repo / "rebuild" / "m1-aliases.yaml").write_text("space: boundary\n", encoding="utf-8")
    (repo / "rebuild" / "m1-divergences.yaml").write_text("[]\n", encoding="utf-8")
    stamped = set(settle_memo_keys.stamped_data_paths(repo))
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
    _perturb_rune(repo / "glyph_data" / "runes" / "qsPea.yaml")
    assert _stamp(repo, spec).lines == base.lines


def test_blessing_a_contact_signature_leaves_the_stamp_untouched(repo):
    """No settlement reads the contact allow-list (the defect gate does), and it changes often, so stamping it would drop every memo on a two-line bless. It needs no exclusion because it is outside `fingerprint.data_paths`, and the test asserts that directly instead of relying on an exclusion entry."""
    spec = fixtures.mini_spec()
    allow = repo / fingerprint.CONTACT_ALLOW_LABEL
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y1'}\n", encoding="utf-8")
    base = _stamp(repo, spec)
    assert allow not in fingerprint.data_paths(repo)
    assert allow not in set(settle_memo_keys.stamped_data_paths(repo))
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y2'}\n", encoding="utf-8")
    assert _stamp(repo, spec).lines == base.lines


def test_a_schema_hover_or_a_registry_comment_leaves_the_stamp_untouched(repo):
    """The `data` line hashes the schemas without their `title` and `description` annotations and the registry without its comments (`fingerprint.data_path_lines`), so rewording either keeps every memoized settlement. A keyword edit still moves the stamp."""
    spec = fixtures.mini_spec()
    schema_path = repo / "rebuild" / "schema" / "rune.schema.json"
    base = _stamp(repo, spec)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["description"] = "Reworded."
    schema["$defs"]["stance"]["description"] = "Reworded as well."
    schema_path.write_text(json.dumps(schema, indent=4), encoding="utf-8")
    _script(repo).write_text("# A comment.\n" + _script(repo).read_text(encoding="utf-8"), encoding="utf-8")
    assert _stamp(repo, spec).lines == base.lines
    schema["$defs"]["stance"]["minProperties"] = 1
    schema_path.write_text(json.dumps(schema, indent=4), encoding="utf-8")
    assert fingerprint.moved_note(base.labels, _stamp(repo, spec).labels) == "data (changed)"


# --- the family key's grain --------------------------------------------------------------


def test_the_family_keys_move_per_family(repo):
    """A geometric edit to one rune moves that family's key and no other."""
    spec = fixtures.mini_spec()
    base = settle_memo_keys.settle_family_keys(settle_memo_keys.settle_memo_inputs(repo), spec)
    assert set(base) == set(fingerprint.rune_digests(repo))
    _perturb_rune(repo / "glyph_data" / "runes" / "qsPea.yaml")
    moved = settle_memo_keys.settle_family_keys(settle_memo_keys.settle_memo_inputs(repo), spec)
    assert settle_memo_keys.moved_families(base, moved) == frozenset({"qsPea"})


def test_a_ligature_rune_edit_invalidates_only_masks_carrying_all_its_components():
    """A ligature rune declares a `sequence` and no codepoint, so no codepoint names it, yet `settle.form_ligatures` routes the pair through its file. A key over only the families of a mask's codepoints would therefore miss a ligature edit. The ligature clause fires when all the components' bits are present and ignores order: a text with the components in the other order can never form the ligature but is marked stale anyway. Over-invalidation is the safe direction, and a mask cheap enough to test on every entry cannot check order."""
    spec = fixtures.mini_spec()
    tea_oy = (TEA, OY)
    tea_pea = (TEA, PEA)
    for moved, expected in (
        ({"qsTea_qsOy"}, {tea_oy}),
        ({"qsPea"}, {tea_pea}),
        ({"qsSee"}, set()),
    ):
        mask = settle_memo_keys.StaleMask(spec, moved)
        assert {row for row in (tea_oy, tea_pea) if mask.stale(mask.mask_of(row))} == expected

    ligature = settle_memo_keys.StaleMask(spec, {"qsTea_qsOy"})
    assert not ligature.stale(ligature.mask_of((TEA,)))
    assert not ligature.stale(ligature.mask_of((OY,)))
    assert ligature.stale(ligature.mask_of((OY, TEA)))
    assert "qsTea_qsOy" in ligature.families_of(ligature.mask_of(tea_oy))
    assert "qsTea_qsOy" not in ligature.families_of(ligature.mask_of(tea_pea))


def test_a_ligature_rune_edit_stales_the_windows_carrying_its_formed_label():
    """Settle-memo windows hold formed labels, so a window with a `qsTea_qsOy` label names the ligature rune and neither component, and the component clause alone would serve that window after an edit to `qsTea_qsOy.yaml`. The formed label's bit must read as moved on its own, while a single component's bit must not. An edit to a component must not make stale a window that names only the formed ligature, because the edit did not touch the ligature's file."""
    spec = fixtures.mini_spec()
    ligature = settle_memo_keys.StaleMask(spec, {"qsTea_qsOy"})
    formed = ligature.bit_of("qsTea_qsOy")
    assert formed and ligature.stale(formed)
    assert not ligature.stale(ligature.bit_of("qsTea"))
    assert not ligature.stale(ligature.bit_of("qsOy"))
    assert ligature.stale(ligature.bit_of("qsTea") | ligature.bit_of("qsOy"))
    assert ligature.bit_of("#EDGE") == 0

    component = settle_memo_keys.StaleMask(spec, {"qsTea"})
    assert component.stale(component.bit_of("qsTea"))
    assert not component.stale(component.bit_of("qsTea_qsOy"))


def test_a_moved_family_the_registry_cannot_place_stales_every_entry():
    """A moved name with neither a codepoint nor a sequence, such as a rune file for a family the registry does not list yet, can reach windows in ways the bits cannot describe, so it makes every entry stale."""
    spec = fixtures.mini_spec()
    mask = settle_memo_keys.StaleMask(spec, {"qsNotInTheRegistry"})
    assert mask.everything
    assert mask.stale(mask.mask_of((PEA,)))
    assert mask.stale(0)


def test_a_family_absent_from_the_recorded_keys_is_a_miss():
    """A family present in only one of the two key maps counts as moved. Comparing with `recorded.get(name) == current.get(name)` would treat two absences as equal, and the case that matters is a name present on one side only: a new ligature rune file for two letters already in the alphabet. Reading that as agreement would serve the pre-ligature settlement for every window of the pair."""
    recorded = {"qsPea": "p0", "qsTea": "t0"}
    current = {"qsPea": "p0", "qsTea": "t0", "qsTea_qsOy": "to0"}
    assert settle_memo_keys.moved_families(recorded, current) == frozenset({"qsTea_qsOy"})
    assert settle_memo_keys.moved_families(current, recorded) == frozenset({"qsTea_qsOy"})
    assert settle_memo_keys.moved_families(recorded, recorded) == frozenset()
    assert settle_memo_keys.moved_families({}, {}) == frozenset()
