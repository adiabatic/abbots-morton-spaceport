"""Tests for the review-app build CLI: the §7 contract checker over rebuild/review/fixtures/ (the same checker `build_m1` runs over its own output, so fixtures and real output can never drift), the config-note badge vocabulary, the app shell and its shipped scripts, the export round-trip, and the table-diff build.

What is asserted against the *live* surface is deliberately short, because `build_m1` proves the per-unit and per-shard contracts over every unit it writes and fails the build on any violation — re-walking 1.9 GB of shards here to restate one of them bought nothing but twelve seconds and eight gigabytes apiece. What no build check can make is a claim tying a *persisted* value back to a fresh re-shape of the fonts, so those stay: the shipped ink-delta digests and cluster ids against the comparator recipe (sampled from the smallest shards), the two worked examples of the seam census and the ink-duplicate fold (looked up by codepoint rather than by parsing every shard), and the manifest's own fingerprint, feature descriptions, and sidebar order.

The built surface comes from `built_review_surface` in rebuild/conftest.py — the artifact cycle's own rebuild/out/review, read-only, refused rather than rebuilt when it is stale.
"""

import copy
import hashlib
import json
import multiprocessing
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import fingerprint
from rebuild.review import build as review_build
from rebuild.review import unit_index
from rebuild.review.audit import ACCEPTANCE_CONFIGS, load_workload, machine_approved
from rebuild.review.build import (
    FEATURE_DESCRIPTIONS,
    STATIC_DIR,
    _prune_orphan_shards,
    _write_json,
    _write_shard,
    build_table_diff,
    check_manifest,
    check_output_dir,
    check_shards,
    check_unit,
    config_badge,
    config_gate,
    config_note,
)
from rebuild.review.enrich import LETTERS
from rebuild.review.export import _triage_projection, build_triage, load_units, load_verdicts
from rebuild.review.ink import IDENTITY_DIFF, InkComparator, delta_digest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
MINI = FIXTURES / "mini"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"


@pytest.fixture(scope="module")
def built(built_review_surface):
    return built_review_surface


def _load_fixture_units():
    units = []
    for shard in sorted((FIXTURES / "units").glob("*.json")):
        units.extend(json.loads(shard.read_text(encoding="utf-8")))
    return units


def test_fixture_manifest_passes_the_contract_checker():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert check_manifest(manifest) == []


def test_fixture_units_pass_the_contract_checker():
    units = _load_fixture_units()
    assert len(units) == 6
    for unit in units:
        assert check_unit(unit, "m1-audit") == []


def test_fixture_units_exercise_the_contract_branches():
    units = _load_fixture_units()
    assert any(len(unit["configs"]) > 1 for unit in units)
    assert any("&#x200C;" in (unit["text_entities"] or "") for unit in units)
    assert any("&#x00B7;" in (unit["text_entities"] or "") for unit in units)
    assert any("ligation" in unit["kinds"] for unit in units)
    assert any(unit["pair"] is None for unit in units)
    assert any(unit["drafts"]["pin"]["duplicate_of"] for unit in units)
    assert any(
        seam["home"] for unit in units for seam in unit.get("secondary_seams") or ()
    ), "a fixture unit must exercise the homed secondary-seam branch"
    assert any(isinstance(unit["cluster"], str) for unit in units)
    assert any(unit["cluster"] is None for unit in units)
    echoes_by_cluster = {}
    for unit in units:
        if unit["cluster"]:
            echoes_by_cluster.setdefault(unit["cluster"], set()).add(unit["echo"])
    assert any(
        len(echoes) > 1 for echoes in echoes_by_cluster.values()
    ), "a fixture cluster must span echo groups"
    assert any(unit["ink_deltas"] for unit in units)
    assert any(unit["ink_deltas"] == {} for unit in units)
    assert any(
        unit["ink_deltas"] and set(unit["ink_deltas"]) < set(unit["configs"]) for unit in units
    ), "a fixture unit must exercise the ink_deltas branch where only some configs diverge"


def test_fixture_sources_derive_the_checked_in_shards():
    """The fixture's checked-in sources really are its shards' sources: `load_workload` over fixture-audit.tsv and fixture-ledger.yaml reproduces every unit the shards ship, every class the manifest lists, and every count it declares. The manifest's per-class and total row counts are only checkable against something here — `check_shards` compares them against each other, never against rows — so hand-growing the fixture can no longer leave the totals describing a workload the TSV doesn't hold.

    Two bindings are deliberately looser than equality. Unit ids are not part of it: `build_units` numbers units in triage order — ledger class, then lead-family-pair group, then codepoints — which is not the order the shards' hand-assigned ids run in, so each derived unit is matched to its shard unit by window. Config order is compared as a multiset for a related reason: the fixture's ss02-era vocabulary sits outside ACCEPTANCE_CONFIGS, so `build_units` sorts those configs behind the ranked ones while the shards keep the hand-written order.
    """
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    workload = load_workload(FIXTURES / "fixture-audit.tsv", FIXTURES / "fixture-ledger.yaml", dict(LETTERS))
    shipped = {unit["codepoints"]: unit for unit in _load_fixture_units()}
    assert len(shipped) == 6
    assert {unit.codepoints for unit in workload.units} == set(shipped)

    for derived in workload.units:
        unit = shipped[derived.codepoints]
        assert derived.class_id == unit["class"]
        assert derived.group == unit["group"]
        assert derived.kinds == tuple(unit["kinds"])
        assert sorted(derived.configs) == sorted(unit["configs"])
        assert derived.exemplar == unit["exemplar"]
        assert derived.baseline == tuple(unit["before"]["glyphs"])
        assert derived.new == tuple(unit["after"]["cells"])

    for entry, meta in zip(workload.ledger, manifest["classes"], strict=True):
        assert entry.id == meta["id"]
        assert entry.status == meta["status"]
        assert entry.why == meta["why"]
        assert entry.ink_identical == meta["ink_identical"]
        assert entry.no_verdict == meta["no_verdict"]

    assert [entry.id for entry in workload.classes_present] == [meta["id"] for meta in manifest["classes"]]
    by_class = workload.units_by_class()
    for meta in manifest["classes"]:
        members = by_class[meta["id"]]
        assert len(members) == meta["unit_count"]
        assert sum(len(member.rows) for member in members) == meta["row_count"]
    assert len(workload.units) == manifest["totals"]["units"]
    assert workload.row_count == manifest["totals"]["rows"]


def _fixture_unit(*, ink_identical: bool) -> dict:
    """A deep copy of a fixture unit that passes the contract checker as shipped, so a test can break one field, watch the checker complain, and put it back."""
    return copy.deepcopy(
        next(unit for unit in _load_fixture_units() if unit["ink_identical"] is ink_identical)
    )


def test_check_unit_requires_a_well_formed_ink_deltas_map():
    """The persisted per-config delta identity is contract-checked like every other shipped field: present, a mapping, keys drawn from the unit's own configs, values `d-` plus twelve lowercase hex digits. Every break is repaired before the next one, and the repaired unit passes, so no complaint here can be an artifact of a unit that was already failing."""
    unit = _fixture_unit(ink_identical=False)
    assert check_unit(unit, "m1-audit") == []
    good = unit["ink_deltas"]
    config = next(iter(good))

    missing = {key: value for key, value in unit.items() if key != "ink_deltas"}
    assert any("ink_deltas" in error for error in check_unit(missing, "m1-audit"))

    for not_a_map in ([[config, good[config]]], good[config], None, 7):
        unit["ink_deltas"] = not_a_map
        assert any(
            "ink_deltas must be a mapping" in error for error in check_unit(unit, "m1-audit")
        ), not_a_map

    for malformed in ("d-nothex000000", "d-ABCDEF012345", "d-abc", good[config][2:], "", None):
        unit["ink_deltas"] = {config: malformed}
        assert any("d- delta digests" in error for error in check_unit(unit, "m1-audit")), malformed

    unit["ink_deltas"] = {"": good[config]}
    assert any("d- delta digests" in error for error in check_unit(unit, "m1-audit"))

    unit["ink_deltas"] = {**good, "ss99": good[config]}
    assert any("subset of configs" in error for error in check_unit(unit, "m1-audit"))

    unit["ink_deltas"] = good
    assert check_unit(unit, "m1-audit") == []


def test_check_unit_ties_ink_deltas_emptiness_to_ink_identical():
    """The map and the flag are two views of one fact, so the checker refuses to ship them disagreeing: a machine-approved ink-identical unit records no delta at all, and a unit whose ink moved records at least one."""
    identical = _fixture_unit(ink_identical=True)
    assert check_unit(identical, "m1-audit") == []
    assert identical["ink_deltas"] == {}
    identical["ink_deltas"] = {identical["configs"][0]: "d-0123456789ab"}
    assert any("ink-identical units" in error for error in check_unit(identical, "m1-audit"))
    identical["ink_deltas"] = {}
    assert check_unit(identical, "m1-audit") == []

    changed = _fixture_unit(ink_identical=False)
    assert check_unit(changed, "m1-audit") == []
    good = changed["ink_deltas"]
    changed["ink_deltas"] = {}
    assert any("nonempty ink_deltas" in error for error in check_unit(changed, "m1-audit"))
    changed["ink_deltas"] = good
    assert check_unit(changed, "m1-audit") == []


def test_check_unit_admits_one_machine_channel_at_most():
    """The channels are tried in precedence order and each is asked only where the earlier ones refused, so a unit carrying two of them was built by nothing this checker knows."""
    unit = _fixture_unit(ink_identical=True)
    assert check_unit(unit, "m1-audit") == []
    unit["picture_identical"] = True
    assert any("at most one machine channel" in error for error in check_unit(unit, "m1-audit"))


def test_check_unit_nulls_batch_on_picture_identical_units():
    """A picture-identical unit leaves the human workload exactly as an ink-identical one does, while keeping the nonempty ink_deltas its name-grain change records."""
    unit = _fixture_unit(ink_identical=False)
    unit["picture_identical"] = True
    assert any("batch null" in error for error in check_unit(unit, "m1-audit"))
    unit["batch"] = None
    unit["echo"] = None
    unit["cluster"] = None
    unit["secondary_seams"] = None
    assert check_unit(unit, "m1-audit") == []
    assert unit["ink_deltas"]


def test_check_manifest_requires_the_three_machine_channels():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert not any("machine channels" in error for error in check_manifest(manifest))
    del manifest["machine_approved"]["channels"]["picture_identical"]
    assert any("three machine channels" in error for error in check_manifest(manifest))


def test_check_unit_leaves_ink_deltas_out_of_the_table_diff_contract():
    """The table-diff surface diffs TSV rows rather than rendered ink, so its units carry no per-config deltas; the field is m1-audit's contract alone and its absence must draw no complaint. test_table_diff_build runs the whole checker over a real table-diff build, where every unit lacks it."""
    unit = _fixture_unit(ink_identical=False)
    without = {key: value for key, value in unit.items() if key != "ink_deltas"}
    assert not any("ink_deltas" in error for error in check_unit(without, "table-diff"))
    assert check_unit(without, "table-diff") == check_unit(unit, "table-diff")


def test_manifest_carries_the_inputs_fingerprint(built, live_artifacts):
    _out_dir, manifest = built
    inputs = manifest["inputs_fingerprint"]
    assert set(inputs) == set(fingerprint.COMPONENTS)
    recorded = fingerprint.read_stage_a(live_artifacts.m1) or {
        key: None for key in fingerprint.STAGE_A_COMPONENTS
    }
    for key in fingerprint.STAGE_A_COMPONENTS:
        assert inputs[key] == recorded[key]
    for key in fingerprint.STAGE_B_COMPONENTS:
        assert isinstance(inputs[key], str)


def test_check_manifest_flags_a_malformed_inputs_fingerprint():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    manifest["inputs_fingerprint"] = {"data": "x"}
    assert any("inputs_fingerprint" in error for error in check_manifest(manifest))
    manifest["inputs_fingerprint"] = {key: 7 for key in fingerprint.COMPONENTS}
    assert any("inputs_fingerprint" in error for error in check_manifest(manifest))


@pytest.mark.parametrize(
    "human_unit_ids",
    ("u-0000", ["u-0000", ["u-0001"]], ["not-a-unit"], ["u-0000", "u-0000"]),
)
def test_check_manifest_flags_malformed_human_unit_ids(human_unit_ids):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    manifest["human_unit_ids"] = human_unit_ids
    assert any("human_unit_ids" in error for error in check_manifest(manifest))


def test_check_shards_flags_human_unit_ids_that_do_not_match_batches():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    shards = {
        meta["id"]: [
            unit
            for part in unit_index.class_shards(meta)
            for unit in json.loads((FIXTURES / part).read_text(encoding="utf-8"))
        ]
        for meta in manifest["classes"]
    }
    manifest["human_unit_ids"].pop()
    assert any("human_unit_ids" in error for error in check_shards(manifest, shards))


def _shard_parts(out_dir, manifest):
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            yield out_dir / part


def _units_with_codepoints(out_dir, manifest, wanted):
    """The units of the named windows, gathered without parsing shard parts that cannot hold one. A part's codepoints appear verbatim in its JSON text, so a substring test over the raw bytes rules most of the surface out for the price of a read — which matters, because parsing the whole corpus costs twelve seconds and eight gigabytes where a worked example wants two units."""
    found = {}
    for path in _shard_parts(out_dir, manifest):
        raw = path.read_text(encoding="utf-8")
        if not any(f'"{codepoints}"' in raw for codepoints in wanted):
            continue
        for unit in json.loads(raw):
            if unit["codepoints"] in wanted:
                found.setdefault(unit["codepoints"], []).append(unit)
    return found


def _unit_by_id(out_dir, manifest, unit_id):
    for path in _shard_parts(out_dir, manifest):
        raw = path.read_text(encoding="utf-8")
        if f'"{unit_id}"' not in raw:
            continue
        for unit in json.loads(raw):
            if unit["id"] == unit_id:
                return unit
    raise AssertionError(f"{unit_id} is in no shard")


def _non_ss10_units(units):
    """A window's units outside its ss10-only sibling: under ss10 every letter keeps its own cluster, so the same codepoints settle into a second, seamless unit that the worked examples below never mean."""
    return [unit for unit in units if unit["configs"] != ["ss10"]]


def test_known_secondary_seam_homes_at_the_shorter_primary(built):
    """The worked example: ·May·No·No's trailing ·No·No seam is a secondary divergence whose home is the ·No·No unit, where that same join is the primary (amber-band) judgment. (The pre-IT1 example, ·Pea·Pea·It·It, dissolved when ·It stopped joining itself — its seam is now the homeless ·Pea·It one.) The window settles into two units — the ss10-only one, where the overlay leaves the seam, and its ink-identical sibling under every other config — and only the seam-bearing one is the example."""
    out_dir, manifest = built
    units = _units_with_codepoints(out_dir, manifest, {"E665:E666:E666"})["E665:E666:E666"]
    (unit,) = [candidate for candidate in units if candidate["secondary_seams"]]
    assert unit["pair"] == {"left": 0, "right": 1}
    (seam,) = unit["secondary_seams"]
    assert seam["pair"] == {"left": 1, "right": 2}
    home = _unit_by_id(out_dir, manifest, seam["home"])
    assert home["codepoints"] == "E666:E666"
    assert home["pair"] == {"left": 0, "right": 1}
    for side in ("before", "after"):
        assert seam[side]["x_min"] <= seam[side]["x_max"] <= seam[side]["advance_total"]


def _smallest_shards_first(manifest):
    """The classes in ascending unit_count, so a three-unit sample parses tens of megabytes instead of whichever largest shard happens to sort first."""
    return sorted(manifest["classes"], key=lambda meta: meta["unit_count"])


def test_built_ink_deltas_match_the_comparator_recipe(built):
    """Locks the shipped digests to delta_digest over the same config_diff the cluster signature is built from, so a persisted value really is the delta's identity and a digest blessed once in rebuild/standing-approvals.yaml keeps matching after a rebuild. Sampled like test_cluster_id_recipe_matches_the_docket_tool, since re-shaping every window here would duplicate the build."""
    out_dir, manifest = built
    comparator = InkComparator(
        out_dir / manifest["fonts"]["before"]["file"], out_dir / manifest["fonts"]["after"]["file"]
    )
    sampled = 0
    for meta in _smallest_shards_first(manifest):
        units = json.loads((out_dir / unit_index.class_shards(meta)[0]).read_text(encoding="utf-8"))
        unit = next((entry for entry in units if entry["ink_deltas"]), None)
        if unit is None:
            continue
        text = "".join(chr(int(part, 16)) for part in unit["codepoints"].split(":"))
        expected = {}
        for config in unit["configs"]:
            diff = comparator.config_diff(text, config)
            if diff != IDENTITY_DIFF:
                expected[config] = delta_digest(diff)
        assert unit["ink_deltas"] == expected, unit["id"]
        sampled += 1
        if sampled == 3:
            break
    assert sampled == 3


def test_ink_duplicate_siblings_fold_in_the_built_output(built):
    """The worked example of the ink-duplicate merge: the old font's ss04 lookups rename word-initial ·It in ·It·Day·Tea·No (E670:E653:E652:E666) without moving any ink, which used to split the window into a default-configs unit and an ss04-only sibling asking the identical visual question twice. The build folds them: one unit, every non-ss10 config, one render group, no config badge."""
    out_dir, manifest = built
    (unit,) = _non_ss10_units(
        _units_with_codepoints(out_dir, manifest, {"E670:E653:E652:E666"})["E670:E653:E652:E666"]
    )
    assert unit["configs"] == ["default", "ss03", "ss04", "ss05", "ss03+ss05"]
    assert unit["render_groups"] == [{"configs": unit["configs"]}]
    assert unit["config_note"] is None
    assert unit["config_gate"] is None


def test_cluster_id_recipe_matches_the_docket_tool(built):
    """Locks the signature recipe to the one rebuild/tools/review_docket.py always used — sha1 of repr((configs, class, per-config ink diffs)) — so shipped cluster ids can never silently drift from the historical docket ids that recorded verdict notes and recommendations reference."""
    out_dir, manifest = built
    comparator = InkComparator(
        out_dir / manifest["fonts"]["before"]["file"], out_dir / manifest["fonts"]["after"]["file"]
    )
    sampled = 0
    for meta in _smallest_shards_first(manifest):
        units = json.loads((out_dir / unit_index.class_shards(meta)[0]).read_text(encoding="utf-8"))
        unit = next((entry for entry in units if entry["batch"] is not None), None)
        if unit is None:
            continue
        text = "".join(chr(int(part, 16)) for part in unit["codepoints"].split(":"))
        diffs = tuple(comparator.config_diff(text, config) for config in unit["configs"])
        key = (tuple(unit["configs"]), unit["class"], diffs)
        assert unit["cluster"] == "c-" + hashlib.sha1(repr(key).encode()).hexdigest()[:8], unit["id"]
        sampled += 1
        if sampled == 3:
            break
    assert sampled == 3


def test_config_note_covers_the_general_gated_excluded_overlay_and_fallback_cases():
    full = ACCEPTANCE_CONFIGS
    non_ss10 = tuple(config for config in full if config != "ss10")
    assert config_note(non_ss10, full) is None
    assert config_note(full, full) is None
    assert config_note(("ss03", "ss03+ss05"), full) == "only when ss03 is on"
    assert config_note(("default", "ss04", "ss05"), full) == "only when ss03 is off"
    assert config_note(("ss10",), full) == "only under ss10"
    assert config_note(("ss04", "ss10"), full) == "only under: ss04, ss10"


def test_config_badge_caches_list_and_tuple_equivalents_together():
    full = ACCEPTANCE_CONFIGS
    from_lists = config_badge(["ss03"], list(full))
    from_tuples = config_badge(("ss03",), full)
    assert from_lists is from_tuples


def test_config_gate_pins_a_narrower_set_than_one_feature_can_describe():
    """A set narrower than "every config with ss03 on" is still entirely about a feature conjunction — a unit can diverge under ss03 alone because turning ss05 on changes the render into a different unit. Such a set resolves to the conjunction that actually pins it, so the badge names the features in their own colors instead of falling back to a config list the reviewer has to decode."""
    full = ACCEPTANCE_CONFIGS
    assert config_note(("ss03",), full) == "only when ss03 is on and ss05 is off"
    assert config_note(("ss05",), full) == "only when ss05 is on and ss03 is off"
    assert config_note(("ss03+ss05",), full) == "only when ss03 is on and ss05 is on"
    assert config_note(("default", "ss04"), full) == "only when ss03 is off and ss05 is off"
    assert config_note(("default", "ss05"), full) == "only when ss03 is off and ss04 is off"
    assert config_note(("default",), full) == "only when ss03 is off and ss04 is off and ss05 is off"


def test_config_gate_leaves_the_literal_fallback_to_sets_no_conjunction_pins():
    """The fallback survives for a genuine disjunction (ss04 *or* ss10, which no conjunction selects — every all-off conjunction admits default instead). The other fallback shape, a set needing more constraints than GATE_CONSTRAINT_CAP, is unreachable while only three joining features exist (the cap covers them all); it returns when the feature roster grows past the cap."""
    full = ACCEPTANCE_CONFIGS
    assert config_gate(("ss04", "ss10"), full) is None
    assert config_note(("ss04", "ss10"), full) == "only under: ss04, ss10"
    assert config_gate(("ss03", "ss03+ss05", "ss10"), full) is None
    assert config_note(("default", "ss03"), full) == "only when ss04 is off and ss05 is off"


def test_config_gate_clauses_carry_their_own_prose_and_the_note_is_their_join():
    """The clause `text` fields are the single home for the badge's prose: the app renders them verbatim as one chip each, and config_note is exactly their join, so no second copy of the phrasing exists to drift. On-constraints lead, which puts the lit chip at the head of the badge."""
    full = ACCEPTANCE_CONFIGS
    gate = config_gate(("ss03",), full)
    assert gate is not None
    assert gate == [
        {"feature": "ss03", "state": "on", "text": "only when ss03 is on"},
        {"feature": "ss05", "state": "off", "text": "and ss05 is off"},
    ]
    assert config_note(("ss03",), full) == " ".join(clause["text"] for clause in gate)
    assert config_gate(("ss10",), full) == [{"feature": "ss10", "state": "on", "text": "only under ss10"}]


def test_feature_descriptions_keys_match_the_readme_stylistic_set_list():
    """FEATURE_DESCRIPTIONS is a hand-mirror of README's "Stylistic sets" section (the wording is trimmed for the badge, so only the set of keys is pinned). If the author adds or retires a stylistic set in the README, this fails until the build map is updated, so the glowing badge can never silently lack — or invent — a set."""
    import re

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Stylistic sets", 1)[1].split("\n## ", 1)[0]
    readme_sets = set(re.findall(r"^- `(ss\d+)`:", section, re.MULTILINE))
    assert readme_sets, "no `ssNN` bullets found under README's Stylistic sets heading"
    assert set(FEATURE_DESCRIPTIONS) == readme_sets


def test_manifest_carries_feature_descriptions(built):
    """The glowing config-note badge appends what each stylistic set is for, so the manifest ships the feature→description map (mirrored from README's "Stylistic sets"). That the built gates all resolve against it is test_every_built_gate_clause_resolves_to_a_feature_description's job."""
    _, manifest = built
    descriptions = manifest["feature_descriptions"]
    assert set(descriptions) == {"ss02", "ss03", "ss04", "ss05", "ss06", "ss07", "ss10"}
    assert all(isinstance(text, str) and text for text in descriptions.values())


def test_built_classes_keep_ledger_order_then_families(built):
    """The sidebar order: the present ledger classes in ledger-file order, then the verdict families in FAMILY_ORDER. Families sort strictly last so clean-unit ids stay stable across a fresh build. Each ledger class carries its ledger why; each family carries its FAMILY_WHY. (The ledger `count` field is not asserted — it is the oracle's static bookkeeping, not maintained against the live audit, so row_count is only required positive.)"""
    from rebuild.review import families

    _out_dir, manifest = built
    ledger = yaml.safe_load((REPO_ROOT / "rebuild" / "m1-divergences.yaml").read_text())
    by_id = {entry["id"]: entry for entry in ledger}
    present = [meta["id"] for meta in manifest["classes"]]
    ledger_ids = [meta["id"] for meta in manifest["classes"] if meta["status"] != "unmatched"]
    family_ids = [fid for fid in families.FAMILY_ORDER if fid in present]
    assert present == ledger_ids + family_ids
    assert ledger_ids == [entry["id"] for entry in ledger if entry["id"] in set(ledger_ids)]
    for meta in manifest["classes"]:
        assert meta["row_count"] > 0
        if meta["status"] == "unmatched":
            assert meta["why"] == families.FAMILY_WHY[meta["id"]]
        else:
            assert meta["why"] == by_id[meta["id"]].get("why", "").strip()


class _HtmlSanity(HTMLParser):
    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.counts = {"main": 0, "h1": 0}
        self.references: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.counts:
            self.counts[tag] += 1
        attr_dict = dict(attrs)
        for key in ("href", "src"):
            value = attr_dict.get(key)
            if value:
                self.references.append(value)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"close </{tag}> with empty stack")
            return
        if self.stack[-1] != tag:
            self.errors.append(f"close </{tag}> but open is <{self.stack[-1]}>")
        else:
            self.stack.pop()


def test_index_html_sanity():
    """The app shell, checked at its source rather than through a build: `copy_static` copies rebuild/review/static/ verbatim, so the page a reviewer loads is these bytes and the local references it makes resolve within this directory."""
    parser = _HtmlSanity()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    assert parser.errors == []
    assert parser.stack == []
    assert parser.counts["main"] == 1
    assert parser.counts["h1"] == 1
    for reference in parser.references:
        if "//" in reference or reference.startswith(("#", "mailto:", "data:")):
            continue
        target = STATIC_DIR / reference.split("#")[0].split("?")[0]
        assert target.exists(), f"dangling reference {reference}"


def test_node_check_passes_on_every_shipped_script():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed on this machine")
    scripts = sorted(STATIC_DIR.rglob("*.js"))
    assert scripts
    for script in scripts:
        result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def _padded(count: int, filler: int = 0) -> list[dict]:
    return [{"id": f"u-{index:04d}", "pad": "x" * filler} for index in range(count)]


def test_write_shard_keeps_a_class_under_the_cap_in_one_bare_file(tmp_path):
    """A class small enough to fit keeps `units/<class-id>.json`, so the small classes, the checked-in fixtures, and every archived surface stay exactly where their readers already look — and its bytes are still the one-shot dumps' bytes."""
    fragments = _padded(4, 40)
    assert _write_shard(tmp_path, "small", fragments) == ["units/small.json"]
    path = tmp_path / "units" / "small.json"
    assert path.read_bytes() == (json.dumps(fragments, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    assert sorted(entry.name for entry in (tmp_path / "units").iterdir()) == ["small.json"]


def test_write_shard_splits_a_class_that_outgrows_the_cap(tmp_path, monkeypatch):
    """Past the cap the class is written as contiguous three-digit parts numbered from zero with no bare file beside them, each part within the cap, each part's bytes the bytes one `json.dumps` of that part would have produced, and the parts concatenating to the class in order."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 512)
    fragments = _padded(24, 60)
    parts = _write_shard(tmp_path, "big", fragments)
    assert len(parts) > 1
    assert parts == [f"units/big.{index:03d}.json" for index in range(len(parts))]
    assert not (tmp_path / "units" / "big.json").exists()
    seen: list[dict] = []
    for part in parts:
        raw = (tmp_path / part).read_bytes()
        assert len(raw) <= 512, part
        units = json.loads(raw)
        assert raw == (json.dumps(units, indent=1, ensure_ascii=True) + "\n").encode("utf-8"), part
        seen.extend(units)
    assert seen == fragments


def test_write_shard_gives_an_oversized_fragment_a_part_of_its_own(tmp_path, monkeypatch):
    """Nothing here can make a single unit smaller, so a fragment past the cap is written alone rather than split — and it does not drag its neighbors over with it."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 128)
    fragments = [{"id": "u-0000"}, {"id": "u-0001", "pad": "x" * 400}, {"id": "u-0002"}]
    parts = _write_shard(tmp_path, "big", fragments)
    assert [json.loads((tmp_path / part).read_text(encoding="utf-8")) for part in parts] == [
        [fragments[0]],
        [fragments[1]],
        [fragments[2]],
    ]


def test_write_shard_writes_an_empty_class_as_one_empty_array(tmp_path):
    assert _write_shard(tmp_path, "empty", []) == ["units/empty.json"]
    assert (tmp_path / "units" / "empty.json").read_bytes() == b"[]\n"


def test_write_shard_leaves_no_staging_file_behind_when_serializing_fails(tmp_path):
    """A part is staged and renamed for the reason `_write_json` stages: an encode that fails partway through must leave the previous build's units alone rather than a truncated part or a stray `.partial`."""
    with pytest.raises(TypeError):
        _write_shard(tmp_path, "big", [{"id": "u-0000"}, {"id": object()}])
    assert list((tmp_path / "units").iterdir()) == []


def test_prune_orphan_shards_removes_only_unreferenced_json(tmp_path):
    """Every part the manifest lists survives, and both spellings of a name it no longer lists go: the bare file a class left behind when it grew into parts, and a numbered part a shrinking class no longer fills."""
    units = tmp_path / "units"
    units.mkdir()
    for name in ("a.json", "b.json", "big.json", "big.000.json", "big.001.json", "big.002.json"):
        (units / name).write_text("[]", encoding="utf-8")
    (units / "stray.txt").write_text("keep me", encoding="utf-8")
    manifest = {
        "classes": [
            {"shards": ["units/a.json"]},
            {"shards": ["units/big.000.json", "units/big.001.json"]},
        ]
    }
    removed = _prune_orphan_shards(tmp_path, manifest)
    assert removed == ["b.json", "big.002.json", "big.json"]
    assert (units / "a.json").exists()
    assert (units / "big.000.json").exists()
    assert (units / "big.001.json").exists()
    assert (units / "stray.txt").exists()
    assert not (units / "b.json").exists()


def test_prune_orphan_shards_no_units_dir_is_noop(tmp_path):
    assert _prune_orphan_shards(tmp_path, {"classes": []}) == []


def test_a_pooled_surface_build_files_its_per_worker_peaks(tmp_path, monkeypatch):
    """SURFACE_WORKER_BYTES is the divisor the surface build's fan-out width comes out of, and the only thing that can price it is a worker that ran: a step peak maxes over the process tree instead of summing it, so it reads the parent and never the pool. So a pooled build files the same kind:"pool" record an xdist controller files, under a unit name of its own, and `make job-costs` reads it beside the constant. The journal is redirected here because `record_pool` resolves its path at call time for exactly that purpose."""
    journal = tmp_path / "cycle-timings.ndjson"
    monkeypatch.setattr("rebuild.tools.cycle_timings.JOURNAL", journal)
    review_build._record_surface_pool(2, {"w0": 1_000_000, "w1": 2_000_000})
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert (record["kind"], record["unit"], record["width"]) == ("pool", "surface", 2)
    assert list(record["worker_peak_rss_bytes"]) == ["w0", "w1"]
    assert record["worker_peak_rss_bytes"]["w1"] == 2_000_000


def test_a_serial_surface_build_files_no_pool_record(tmp_path, monkeypatch):
    """At width one there is no worker to price, so there is nothing to file — and a record claiming a pool of zero workers would be an observation of nothing that `make job-costs` would then have to learn to ignore."""
    journal = tmp_path / "cycle-timings.ndjson"
    monkeypatch.setattr("rebuild.tools.cycle_timings.JOURNAL", journal)
    review_build._record_surface_pool(0, {})
    assert not journal.exists()


def test_close_finds_the_peak_behind_an_unconsumed_phase_reply(tmp_path, monkeypatch):
    """`close()` runs from a `finally`, so its hardest caller is a build that is already failing: a phase raises from inside its own recv loop, and every conn after the failing one still holds that phase's `("ok", …)` reply. The shutdown reply therefore carries its own `peak` tag and `close()` drains past whatever is queued ahead of it — reading a phase's payload as a peak would raise out of the `finally`, displace the worker traceback the caller came to see, and leave the join loop below unrun with spawn workers still alive."""
    journal = tmp_path / "cycle-timings.ndjson"
    monkeypatch.setattr("rebuild.tools.cycle_timings.JOURNAL", journal)

    class _StubProc:
        def __init__(self) -> None:
            self.joined = False

        def join(self, timeout=None) -> None:
            self.joined = True

        def is_alive(self) -> bool:
            return False

    runner = review_build._FreshRunner(
        [],
        1,
        tmp_path,
        tmp_path / "before.otf",
        tmp_path / "after.otf",
        tmp_path / "junior.otf",
        tmp_path,
    )
    parent, child = multiprocessing.Pipe()
    child.send(("ok", ["projection-a", "projection-b"]))
    child.send(("peak", 1_500_000))
    proc = _StubProc()
    runner._conns = [parent]
    runner._procs = [proc]
    try:
        runner.close()
    finally:
        child.close()
    assert proc.joined
    record = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
    assert record["worker_peak_rss_bytes"] == {"w0": 1_500_000}


@pytest.mark.parametrize(
    "payload",
    (
        [],
        {},
        [{}, [], [{}]],
        [1, -2, 3.5, True, False, None, "x"],
        [{"a": {"b": [1, {"c": [[]]}]}}, {"a": []}],
        [{"letter": "·Pea", "raw": 'line\nbreak\ttab "quote" back\\slash \x1f'}],
        {"format": 3, "classes": [{"id": "a", "shards": ["units/a.json"]}], "why": None},
    ),
)
def test_write_json_matches_one_shot_dumps_byte_for_byte(tmp_path, payload):
    """`_write_json` streams a shard element by element rather than building the whole string, so the framing and the depth-1 indent that used to fall out of one `json.dumps` call are now assembled around per-element calls. The bytes are the contract — the manifest's sha256 is the stamp on units-index.ndjson.gz and the unit store, and the unit cache's incremental-vs-from-scratch check is a file comparison — so pin them against the one-shot form over the shapes a shard and a manifest can hold, empty collections and escaped non-ASCII included, which is where hand-held framing drifts first."""
    target = tmp_path / "payload.json"
    _write_json(target, payload)
    assert target.read_bytes() == (json.dumps(payload, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_write_json_reproduces_the_checked_in_fixture_shards(tmp_path):
    """The same pin over real shards and a real manifest instead of hand-made shapes: re-serializing the checked-in fixture files reproduces them byte for byte, which is exactly what a build re-writing an unchanged surface has to do."""
    sources = (FIXTURES / "manifest.json", *sorted((FIXTURES / "units").glob("*.json")))
    assert len(sources) > 1
    for source in sources:
        target = tmp_path / source.name
        _write_json(target, json.loads(source.read_text(encoding="utf-8")))
        assert target.read_bytes() == source.read_bytes(), source.name


def test_write_json_leaves_the_previous_file_alone_when_serializing_fails(tmp_path):
    """Serializing element by element means the encoder can fail partway through a shard it has already started writing, so the write is staged and renamed rather than truncating the target first. Nothing downstream tolerates a half-written surface file — `check_output_dir` and every manifest reader parse straight off disk, and the shards go out before the manifest that stamps them — so a failed write has to leave the last good build in place and no staging file behind."""
    shard, manifest = tmp_path / "shard.json", tmp_path / "manifest.json"
    _write_json(shard, [{"id": "u-0000"}])
    _write_json(manifest, {"at": "old"})
    intact = {path: path.read_bytes() for path in (shard, manifest)}
    with pytest.raises(TypeError):
        _write_json(shard, [{"id": "u-0000"}, {"id": object()}])
    with pytest.raises(TypeError):
        _write_json(manifest, {"at": object()})
    assert {path: path.read_bytes() for path in (shard, manifest)} == intact
    assert sorted(path.name for path in tmp_path.iterdir()) == ["manifest.json", "shard.json"]


def _export_surface():
    """A hermetic stand-in for a built surface, assembled from the checked-in fixture units so `build_triage` sees every shape it discriminates on. The fixture ships six real units but no exempt one and none without a policy draft, so three more are cloned in: a no-verdict unit whose verdict must be inert history, a reject with no mechanical draft, and one more plain approvable — which is also what makes the four ids the test reaches for past `ids[4:]` exist at all. The manifest is the fixture's with its totals and human-id list recomputed over the enlarged set; the machine-approved block is left alone, because the one machine-approved unit is untouched and `machine_approved_section` re-derives its own copy from the units to be compared against it.

    The units come back projected, because that is the only shape `build_triage` is ever handed in the CLI and handing it whole fixture units here meant nothing tested that the projection is sufficient for what the export reads.
    """
    manifest = copy.deepcopy(json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8")))
    units = {unit["id"]: unit for unit in _load_fixture_units()}
    template = units["u-0004"]

    def clone(unit_id, **changes):
        clone = copy.deepcopy(template)
        clone["id"] = unit_id
        clone.update(changes)
        units[unit_id] = clone

    clone("u-0006", no_verdict=True, batch=None)
    clone("u-0007", drafts={**copy.deepcopy(template["drafts"]), "policy": None})
    clone("u-0008")
    manifest["totals"]["units"] = len(units)
    manifest["human_unit_ids"] = sorted(
        (unit["id"] for unit in units.values() if unit["batch"] is not None),
        key=lambda unit_id: int(unit_id[2:]),
    )
    return manifest, {
        unit_id: _triage_projection(unit, "the export fixture") for unit_id, unit in units.items()
    }


def test_export_skips_verdicts_landing_on_picture_identical_units():
    """The third channel leaves the human workload exactly as the other two do, so a verdict recorded against a unit it approved — the ones judged by hand before the channel existed — is counted as inert history and drafts nothing."""
    manifest, units = _export_surface()
    unit_id = manifest["human_unit_ids"][-1]
    unit = units[unit_id]
    unit.update(picture_identical=True, batch=None)
    manifest["human_unit_ids"] = [uid for uid in manifest["human_unit_ids"] if uid != unit_id]
    manifest["machine_approved"]["units"] += 1
    by_class = manifest["machine_approved"]["by_class"]
    by_class[unit["class"]] = by_class.get(unit["class"], 0) + 1
    verdicts = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "verdicts": [{"unit": unit_id, "verdict": "approve", "note": "", "at": "2026-06-10T18:21:09Z"}],
    }
    triage = build_triage(manifest, units, verdicts)
    counts = triage["review"]["counts"]
    assert counts["approve"] == 0
    assert counts["skipped_machine_approved"] == 1
    assert counts["human_units_total"] == len(manifest["human_unit_ids"])
    assert triage["pins"] == []
    assert triage["machine_approved"]["count"] == manifest["machine_approved"]["units"]
    assert triage["machine_approved"]["by_class"] == by_class


def test_load_units_keeps_exactly_the_fields_the_triage_export_reads():
    """The set is named here rather than derived from `TRIAGE_KEYS`, exactly as `test_the_index_covers_every_field_the_plumbing_reads` names the sidecar's: spelling it against the constant it is meant to police proves only that a dict comprehension keeps the keys it was given, and every field the export reads could be dropped from the projection under a green suite. Named, adding one is a deliberate act and dropping one fails here rather than as a null in a hand-placed triage YAML."""
    manifest, units = load_units(FIXTURES)
    fixture_units = {unit["id"]: unit for unit in _load_fixture_units()}
    assert set(units) == set(fixture_units)
    assert manifest == json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for unit_id, projected in units.items():
        assert set(projected) == {
            "id",
            "class",
            "batch",
            "no_verdict",
            "configs",
            "ink_identical",
            "picture_identical",
            "junior_equivalent",
            "codepoints",
            "text_entities",
            "notation",
            "provenance",
            "drafts",
        }
        assert projected == {key: fixture_units[unit_id][key] for key in projected}


def test_load_units_refuses_a_shard_missing_a_field_the_export_reads(tmp_path):
    """A field the triage export reads but the shard does not carry means the surface was built by a version this reader does not know. Read through `.get`, that lands in the YAML as a null the reviewer has no way to tell from a genuine absence — so the loader names the field and stops instead."""
    shutil.copytree(FIXTURES / "units", tmp_path / "units")
    shutil.copy(FIXTURES / "manifest.json", tmp_path / "manifest.json")
    shard = tmp_path / "units" / "fixture-drift.json"
    units = json.loads(shard.read_text(encoding="utf-8"))
    del units[0]["drafts"]
    _write_json(shard, units)
    with pytest.raises(SystemExit) as error:
        load_units(tmp_path)
    assert "drafts" in str(error.value)
    assert units[0]["id"] in str(error.value)


def test_export_round_trip(tmp_path):
    manifest, units = _export_surface()
    ids = sorted(uid for uid, unit in units.items() if not unit["no_verdict"])
    exempt_unit = next(uid for uid in sorted(units) if units[uid]["no_verdict"])
    drafted_reject = next(uid for uid in ids[4:] if units[uid]["drafts"]["policy"])
    manual_reject = next(uid for uid in ids[4:] if units[uid]["drafts"]["policy"] is None)
    identical_unit = next(uid for uid in ids[4:] if uid not in (drafted_reject, manual_reject))
    human_skip = next(uid for uid in ids[4:] if uid not in (drafted_reject, manual_reject, identical_unit))
    machine_unit = next(uid for uid in ids if machine_approved(units[uid]))
    verdicts_path = tmp_path / "verdicts.json"
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": "2026-06-10T18:40:02Z",
        "verdicts": [
            {"unit": ids[0], "verdict": "approve", "note": "", "at": "2026-06-10T18:21:09Z"},
            # A verdict recorded against a no-verdict unit (a stale master, or a misclick on a revealed exempt row) is inert history: skipped, counted, and never drafted.
            {"unit": exempt_unit, "verdict": "reject", "note": "", "at": "2026-06-10T18:21:10Z"},
            {
                "unit": drafted_reject,
                "verdict": "reject",
                # A leftover configs field from a pre-rework export is ignored: verdicts always cover the whole unit.
                "configs": [units[drafted_reject]["configs"][0]],
                "note": "seam looks reached-for",
                "at": "2026-06-10T18:21:40Z",
            },
            {
                "unit": manual_reject,
                "verdict": "reject",
                "note": "",
                "at": "2026-06-10T18:21:50Z",
            },
            {"unit": ids[2], "verdict": "either", "note": "", "at": "2026-06-10T18:22:00Z"},
            {"unit": human_skip, "verdict": "skip", "note": "", "at": "2026-06-10T18:22:10Z"},
            # A verdict on a machine-approved unit is the same inert history as one on a no-verdict unit: skipped, counted under its own key, never drafted.
            {"unit": machine_unit, "verdict": "skip", "note": "", "at": "2026-06-10T18:22:11Z"},
            {
                "unit": ids[1],
                "verdict": "neither",
                "note": "both joins look wrong",
                "at": "2026-06-10T18:22:20Z",
            },
            {
                "unit": identical_unit,
                "verdict": "identical",
                "note": "cannot see the flagged difference",
                "at": "2026-06-10T18:22:30Z",
            },
        ],
    }
    verdicts_path.write_text(json.dumps(payload))
    triage = build_triage(manifest, units, load_verdicts(verdicts_path))

    counts = triage["review"]["counts"]
    assert counts["approve"] == 1
    assert counts["reject"] == 2
    assert counts["either"] == 1
    assert counts["identical"] == 1
    assert counts["neither"] == 1
    assert counts["skip"] == 1
    assert counts["skipped_no_verdict"] == 1
    assert counts["skipped_machine_approved"] == 1
    assert counts["units_total"] == manifest["totals"]["units"]
    assert counts["human_units_total"] == len(manifest["human_unit_ids"])

    machine = triage["machine_approved"]
    assert machine["count"] == manifest["machine_approved"]["units"]
    assert machine["by_class"] == manifest["machine_approved"]["by_class"]
    assert machine["method"]
    assert machine["rows_covered"] == sum(
        len(unit["configs"]) for unit in units.values() if machine_approved(unit)
    )
    expanded = []
    for token in machine["unit_ids"]:
        if ".." in token:
            start, end = token.split("..")
            expanded.extend(range(int(start[2:]), int(end[2:]) + 1))
        else:
            expanded.append(int(token[2:]))
    assert len(expanded) == manifest["machine_approved"]["units"]
    assert {f"u-{number:04d}" for number in expanded} == {
        unit_id for unit_id, unit in units.items() if machine_approved(unit)
    }
    assert counts["rows_covered"] == sum(
        len(units[uid]["configs"])
        for uid in (ids[0], drafted_reject, manual_reject, ids[2], human_skip, ids[1], identical_unit)
    )

    assert len(triage["pins"]) == 1
    pin = triage["pins"][0]
    assert pin["unit"] == ids[0]
    assert pin["validated"]["syntax"] == "pass"

    assert len(triage["policy_edits"]) == 2
    by_unit = {edit["unit"]: edit for edit in triage["policy_edits"]}
    edit = by_unit[drafted_reject]
    assert edit["why_stub"].endswith("seam looks reached-for")
    assert edit["file"].startswith("glyph_data/runes/")
    manual = by_unit[manual_reject]
    assert manual["keypath"] is None
    assert manual["suggested_record"] is None
    assert manual["no_mechanical_draft"]
    assert manual["names_provenance"] == units[manual_reject]["provenance"]

    assert len(triage["any_of"]) == 1
    assert triage["any_of"][0]["realized_as"] == "_assert_expect_any"
    assert all(status == "pass" for status in triage["any_of"][0]["candidates_parse"])

    # The neither section drafts nothing automatic — only the unit's identity, the reviewer's note, and the provenance levers for follow-up authoring.
    assert len(triage["neither"]) == 1
    neither = triage["neither"][0]
    assert neither == {
        "unit": ids[1],
        "codepoints": units[ids[1]]["codepoints"],
        "notation": units[ids[1]]["notation"],
        "note": "both joins look wrong",
        "names_provenance": units[ids[1]]["provenance"],
    }

    # The identical section drafts nothing either — these are claims the flagged difference is invisible, signal for the ink-comparator and highlight tooling.
    assert len(triage["identical"]) == 1
    identical = triage["identical"][0]
    assert identical == {
        "unit": identical_unit,
        "codepoints": units[identical_unit]["codepoints"],
        "notation": units[identical_unit]["notation"],
        "note": "cannot see the flagged difference",
    }

    section_units = {
        "pins": {entry["unit"] for entry in triage["pins"]},
        "policy_edits": {entry["unit"] for entry in triage["policy_edits"]},
        "any_of": {entry["unit"] for entry in triage["any_of"]},
        "neither": {entry["unit"] for entry in triage["neither"]},
        "identical": {entry["unit"] for entry in triage["identical"]},
    }
    assert section_units == {
        "pins": {ids[0]},
        "policy_edits": {drafted_reject, manual_reject},
        "any_of": {ids[2]},
        "neither": {ids[1]},
        "identical": {identical_unit},
    }

    text = yaml.safe_dump(triage, sort_keys=False, allow_unicode=True, width=10**6)
    parsed = yaml.safe_load(text)
    assert set(parsed) == {
        "review",
        "machine_approved",
        "pins",
        "policy_edits",
        "any_of",
        "neither",
        "identical",
    }


def test_export_rejects_bad_format(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "nope", "verdicts": []}))
    with pytest.raises(SystemExit):
        load_verdicts(bad)


def test_table_diff_build(tmp_path):
    """The table-diff mode end to end over the frozen tables under fixtures/mini/: a synthetic one-row edit yields a one-unit surface that passes the contract checker with the edited row's pointer reaching the explain panel. The tables are inputs, not the subject — nothing here is about today's rules — so they are frozen beside the font they were extracted with rather than read live, which is what puts this in the contracts lane."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for name in ("settlement-default.tsv", "treaties-default.tsv"):
        shutil.copyfile(MINI / name, old_dir / name)
        shutil.copyfile(MINI / name, new_dir / name)
    settlement = (new_dir / "settlement-default.tsv").read_text().splitlines()
    settlement[-1] = settlement[-1].rsplit("\t", 2)[0] + "\tjoint\tsynthetic-pointer"
    (new_dir / "settlement-default.tsv").write_text("\n".join(settlement) + "\n")

    out_dir = tmp_path / "out"
    manifest = build_table_diff(
        out_dir,
        old_dir,
        new_dir,
        REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
        MINI / "M1.otf",
        with_witnesses=True,
        witness_depth=2,
    )
    assert manifest["mode"] == "table-diff"
    assert manifest["totals"]["units"] == 1
    assert check_output_dir(out_dir) == []
    shard = json.loads((out_dir / "units" / "changed.json").read_text(encoding="utf-8"))
    assert len(shard) == 1
    assert shard[0]["class"] == "changed"
    assert "ink_deltas" not in shard[0]
    assert check_unit(shard[0], "table-diff") == []
    assert manifest["human_unit_ids"] == [unit["id"] for unit in shard if unit["batch"] is not None]
    assert "synthetic-pointer" in shard[0]["explain"] or "synthetic-pointer" in " ".join(
        shard[0]["provenance"]
    )
