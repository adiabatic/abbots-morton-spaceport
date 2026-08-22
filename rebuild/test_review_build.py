"""Tests for the review-app build CLI: the §7 contract checker over rebuild/review/fixtures/ (the same checker `build_m1` runs over its own output, so fixtures and real output can never drift), the config-note badge vocabulary, the app shell and its shipped scripts, the export round-trip, and the table-diff build.

What is asserted against the *live* surface is deliberately short, because `build_m1` proves the per-unit and per-shard contracts over every unit it writes and fails the build on any violation — re-walking 1.9 GB of shards here to restate one of them bought nothing but twelve seconds and eight gigabytes apiece. What no build check can make is a claim tying a *persisted* value back to a fresh re-shape of the fonts, so those stay: the shipped ink-delta digests and cluster ids against the comparator recipe (sampled from the smallest shards), the two worked examples of the seam census and the ink-duplicate fold (looked up by codepoint rather than by parsing every shard), and the manifest's own fingerprint, feature descriptions, and sidebar order.

The built surface comes from `built_review_surface` in rebuild/conftest.py — the artifact cycle's own rebuild/out/review, read-only, refused rather than rebuilt when it is stale.
"""

import copy
import hashlib
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import fingerprint
from rebuild.review.audit import ACCEPTANCE_CONFIGS, load_workload
from rebuild.review.build import (
    FEATURE_DESCRIPTIONS,
    STATIC_DIR,
    _prune_orphan_shards,
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
from rebuild.review.export import build_triage, load_verdicts
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
        meta["id"]: json.loads((FIXTURES / meta["shard"]).read_text(encoding="utf-8"))
        for meta in manifest["classes"]
    }
    manifest["human_unit_ids"].pop()
    assert any("human_unit_ids" in error for error in check_shards(manifest, shards))


def _units_with_codepoints(out_dir, manifest, wanted):
    """The units of the named windows, gathered without parsing shards that cannot hold one. A shard's codepoints appear verbatim in its JSON text, so a substring test over the raw bytes rules most of the surface out for the price of a read — which matters, because parsing all 33 shards costs twelve seconds and eight gigabytes where a worked example wants two units."""
    found = {}
    for meta in manifest["classes"]:
        raw = (out_dir / meta["shard"]).read_text(encoding="utf-8")
        if not any(f'"{codepoints}"' in raw for codepoints in wanted):
            continue
        for unit in json.loads(raw):
            if unit["codepoints"] in wanted:
                found.setdefault(unit["codepoints"], []).append(unit)
    return found


def _unit_by_id(out_dir, manifest, unit_id):
    for meta in manifest["classes"]:
        raw = (out_dir / meta["shard"]).read_text(encoding="utf-8")
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
    """The classes in ascending unit_count, so a three-unit sample parses tens of megabytes instead of whichever 450 MB shard happens to sort first."""
    return sorted(manifest["classes"], key=lambda meta: meta["unit_count"])


def test_built_ink_deltas_match_the_comparator_recipe(built):
    """Locks the shipped digests to delta_digest over the same config_diff the cluster signature is built from, so a persisted value really is the delta's identity and a digest blessed once in rebuild/standing-approvals.yaml keeps matching after a rebuild. Sampled like test_cluster_id_recipe_matches_the_docket_tool, since re-shaping every window here would duplicate the build."""
    out_dir, manifest = built
    comparator = InkComparator(
        out_dir / manifest["fonts"]["before"]["file"], out_dir / manifest["fonts"]["after"]["file"]
    )
    sampled = 0
    for meta in _smallest_shards_first(manifest):
        units = json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8"))
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
        units = json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8"))
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


def test_prune_orphan_shards_removes_only_unreferenced_json(tmp_path):
    units = tmp_path / "units"
    units.mkdir()
    (units / "a.json").write_text("[]", encoding="utf-8")
    (units / "b.json").write_text("[]", encoding="utf-8")
    (units / "stray.txt").write_text("keep me", encoding="utf-8")
    manifest = {"classes": [{"shard": "units/a.json"}]}
    removed = _prune_orphan_shards(tmp_path, manifest)
    assert removed == ["b.json"]
    assert (units / "a.json").exists()
    assert (units / "stray.txt").exists()
    assert not (units / "b.json").exists()


def test_prune_orphan_shards_no_units_dir_is_noop(tmp_path):
    assert _prune_orphan_shards(tmp_path, {"classes": []}) == []


def _export_surface():
    """A hermetic stand-in for a built surface, assembled from the checked-in fixture units so `build_triage` sees every shape it discriminates on. The fixture ships six real units but no exempt one and none without a policy draft, so three more are cloned in: a no-verdict unit whose verdict must be inert history, a reject with no mechanical draft, and one more plain approvable — which is also what makes the four ids the test reaches for past `ids[4:]` exist at all. The manifest is the fixture's with its totals and human-id list recomputed over the enlarged set; the machine-approved block is left alone, because the one machine-approved unit is untouched and `machine_approved_section` re-derives its own copy from the units to be compared against it."""
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
    return manifest, units


def test_export_round_trip(tmp_path):
    manifest, units = _export_surface()
    ids = sorted(uid for uid, unit in units.items() if not unit["no_verdict"])
    exempt_unit = next(uid for uid in sorted(units) if units[uid]["no_verdict"])
    drafted_reject = next(uid for uid in ids[4:] if units[uid]["drafts"]["policy"])
    manual_reject = next(uid for uid in ids[4:] if units[uid]["drafts"]["policy"] is None)
    identical_unit = next(uid for uid in ids[4:] if uid not in (drafted_reject, manual_reject))
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
            {"unit": ids[3], "verdict": "skip", "note": "", "at": "2026-06-10T18:22:10Z"},
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
    assert counts["units_total"] == manifest["totals"]["units"]
    assert counts["human_units_total"] == len(manifest["human_unit_ids"])

    machine = triage["machine_approved"]
    assert machine["count"] == manifest["machine_approved"]["units"]
    assert machine["by_class"] == manifest["machine_approved"]["by_class"]
    assert machine["method"]
    assert machine["rows_covered"] == sum(
        len(unit["configs"]) for unit in units.values() if unit["ink_identical"] or unit["junior_equivalent"]
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
        unit_id for unit_id, unit in units.items() if unit["ink_identical"] or unit["junior_equivalent"]
    }
    assert counts["rows_covered"] == sum(
        len(units[uid]["configs"])
        for uid in (ids[0], drafted_reject, manual_reject, ids[2], ids[3], ids[1], identical_unit)
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
