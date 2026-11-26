"""Tests for the review-app build: the §7 contract checker over rebuild/review/fixtures/ (the checker the tests and the table-diff build run over a written corpus, whose predicates the review crate runs over every unit `build_m1` writes), the manifest's class list and copy over a mini build, the config-note badge text, the app shell and its scripts, the assets refresh, the shard writer, `_write_json`, the triage export, the corpus identity's stamps, and the refusal to build into the served corpus while the review server listens.

No test here reads the live corpus. `build_m1` checks the per-unit and per-shard contracts over every unit it writes and fails the build on any violation, and what the manifest writer computes from its own inputs (the fingerprint and the feature descriptions) needs no separate check; the class order and each class's copy and section are tested over a mini build. Two claims about a built corpus are tested elsewhere. The sidecars' byte addressing is tested in `rebuild/test_app_index.py` over a mini build, and the staging in `app_index.write_app_artifacts` and the currency check in `artifact_cycle.corpus_build_skippable` keep a shipped corpus from carrying a sidecar built for other shards. rebuild/review-facts-pins.json records that the ink-duplicate merge ran on the corpus, since its pre-merge `volatile.audit.units` is larger than its post-merge `volatile.manifest.totals.units`. The shipped ink deltas are covered by the build's verification sample, which re-shapes `VERIFICATION_SAMPLE` cached windows on every build.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import fingerprint, row_delta
from rebuild.review import accepted, app_index, facts, unit_cache
from rebuild.review import build as review_build
from rebuild.review import unit_index
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    SECTIONS,
    SLIM_OMITTED_KEYS,
    load_ledger,
    machine_approved,
    slim_fragment,
)
from rebuild.review.build import (
    FEATURE_DESCRIPTIONS,
    STATIC_DIR,
    _prune_orphan_shards,
    _write_json,
    _write_shard,
    check_manifest,
    check_output_dir,
    check_shards,
    check_unit,
    config_badge,
)
from rebuild.review.change_groups import (
    CHANGE_GROUP_ORDER,
    load_change_group_prose,
)
from rebuild.review.export import _triage_projection, build_triage, load_units, load_verdicts

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
MINI = FIXTURES / "mini"


def _load_fixture_units():
    units = []
    for shard in sorted((FIXTURES / "units").glob("*.json")):
        units.extend(json.loads(shard.read_text(encoding="utf-8")))
    return units


def test_fixture_manifest_passes_the_contract_checker():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert check_manifest(manifest) == []


def test_fixture_units_pass_the_contract_checker():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    units = _load_fixture_units()
    assert len(units) == manifest["totals"]["units"]
    for unit in units:
        assert (
            check_unit(unit, "m1-audit") == []
        ), "when an error names the content key or the id, run `uv run python rebuild/review/fixtures/restamp.py`"


def test_fixture_units_exercise_the_contract_branches():
    units = _load_fixture_units()
    assert any(len(unit["configs"]) > 1 for unit in units)
    assert any("&#x200C;" in (unit["text_entities"] or "") for unit in units)
    assert any("&#x00B7;" in (unit["text_entities"] or "") for unit in units)
    assert any("ligation" in unit["kinds"] for unit in units)
    assert any(unit["pair"] is None for unit in units)
    assert any(unit["drafts"] and unit["drafts"]["pin"]["duplicate_of"] for unit in units)
    assert any(
        slim_fragment(unit) and not any(key in unit for key in SLIM_OMITTED_KEYS) for unit in units
    ), "a fixture unit must exercise the slim machine-approved shape"
    assert any(
        junction["primary_unit"] for unit in units for junction in unit.get("secondary_junctions") or ()
    ), "a fixture unit must exercise the branch where a secondary junction has a primary unit"
    assert any(isinstance(unit["cluster"], str) for unit in units)
    assert any(unit["cluster"] is None for unit in units)
    groups_by_cluster = {}
    for unit in units:
        if unit["cluster"]:
            groups_by_cluster.setdefault(unit["cluster"], set()).add(unit["duplicate_group"])
    assert any(
        len(groups) > 1 for groups in groups_by_cluster.values()
    ), "a fixture cluster must span duplicate groups"
    assert any(unit["ink_deltas"] for unit in units)
    assert any(unit["ink_deltas"] == {} for unit in units)
    assert any(
        unit["ink_deltas"] and set(unit["ink_deltas"]) < set(unit["configs"]) for unit in units
    ), "a fixture unit must exercise the ink_deltas branch where only some configs diverge"


def test_fixture_duplicate_groups_and_clusters_follow_the_build_keys():
    """`build_m1` keys a duplicate group on the config set, the judged pair's two code points, the class, and the ink diffs, and a cluster on the same key without the pair. `check_shards` checks only that each group and each cluster holds one class and one config set and that each group lies inside one cluster, so this checks the whole key of every hand-written group and cluster, with the units' per-config `ink_deltas` standing in for the diffs: one key per group or cluster, and no key shared by two. `check_manifest` only type-checks the manifest's `totals.duplicate_groups`, so this also checks it counts the groups."""
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    group_keys: dict[str, set[tuple]] = {}
    cluster_keys: dict[str, set[tuple]] = {}
    for unit in _load_fixture_units():
        key = (tuple(unit["configs"]), unit["class"], tuple(sorted(unit["ink_deltas"].items())))
        if unit["cluster"] is not None:
            cluster_keys.setdefault(unit["cluster"], set()).add(key)
        if unit["duplicate_group"] is not None:
            values = unit["codepoints"].split(":")
            pair = (
                None
                if unit["pair_codepoints"] is None
                else tuple(values[index] for index in unit["pair_codepoints"])
            )
            group_keys.setdefault(unit["duplicate_group"], set()).add((*key, pair))
    for keys_by_id in (group_keys, cluster_keys):
        assert all(len(keys) == 1 for keys in keys_by_id.values()), keys_by_id
        assert len({key for keys in keys_by_id.values() for key in keys}) == len(keys_by_id), keys_by_id
    assert manifest["totals"]["duplicate_groups"] == len(group_keys)


def _fixture_unit(*, ink_identical: bool) -> dict:
    """Return a deep copy of a fixture unit that passes the contract checker, so a test can break one field and restore it."""
    return copy.deepcopy(
        next(unit for unit in _load_fixture_units() if unit["ink_identical"] is ink_identical)
    )


def test_check_unit_requires_a_well_formed_ink_deltas_map():
    """Checks that `ink_deltas` is required and well formed: a mapping whose keys are among the unit's configs and whose values are `d-` plus twelve lowercase hex digits. Each break is repaired before the next, and the repaired unit passes, so every error comes from the field under test."""
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


def test_check_unit_ties_ink_deltas_emptiness_to_the_identity_checks():
    """`ink_deltas` and the identity flags record the same fact, so the checker fails a unit where they disagree: an ink- or picture-identical unit has no deltas, and a unit whose pixels changed has at least one."""
    identical = _fixture_unit(ink_identical=True)
    assert check_unit(identical, "m1-audit") == []
    assert identical["ink_deltas"] == {}
    identical["ink_deltas"] = {identical["configs"][0]: "d-0123456789ab"}
    assert any("empty ink_deltas" in error for error in check_unit(identical, "m1-audit"))
    identical["ink_deltas"] = {}
    assert check_unit(identical, "m1-audit") == []

    changed = _fixture_unit(ink_identical=False)
    assert check_unit(changed, "m1-audit") == []
    good = changed["ink_deltas"]
    changed["ink_deltas"] = {}
    assert any("nonempty ink_deltas" in error for error in check_unit(changed, "m1-audit"))
    changed["ink_deltas"] = good
    assert check_unit(changed, "m1-audit") == []


def test_check_unit_admits_one_machine_check_at_most():
    """The machine checks are tried in precedence order, each only where the earlier ones did not approve, so a unit approved by two checks comes from no build this checker knows."""
    unit = _fixture_unit(ink_identical=True)
    assert check_unit(unit, "m1-audit") == []
    unit["picture_identical"] = True
    assert any("at most one machine check" in error for error in check_unit(unit, "m1-audit"))


def test_check_unit_takes_picture_identical_units_out_of_the_human_units():
    """A picture-identical unit stops being a human unit as an ink-identical one does: no duplicate group, no cluster, the slim fragment shape, and empty `ink_deltas`, because the deltas are read at picture granularity and the flag means they are empty under every config."""
    unit = _fixture_unit(ink_identical=False)
    unit["picture_identical"] = True
    assert any("duplicate_group null" in error for error in check_unit(unit, "m1-audit"))
    unit["duplicate_group"] = None
    unit["cluster"] = None
    unit["secondary_junctions"] = None
    assert any("omit drafts" in error for error in check_unit(unit, "m1-audit"))
    for key in SLIM_OMITTED_KEYS:
        del unit[key]
    assert any("empty ink_deltas" in error for error in check_unit(unit, "m1-audit"))
    unit["ink_deltas"] = {}
    assert check_unit(unit, "m1-audit") == []


def test_check_manifest_requires_the_three_machine_checks():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert not any("machine checks" in error for error in check_manifest(manifest))
    del manifest["machine_approved"]["checks"]["picture_identical"]
    assert any("three machine checks" in error for error in check_manifest(manifest))


def test_check_manifest_requires_each_class_s_copy_status_and_section(mini_corpus):
    """A class needs a nonempty title and summary, a note that is null or nonempty, a ledger status, and the section `audit.class_section` gives its status, exemption and human units, and the classes must be listed by section. The `ledger_classes` lookup must give every entry a title, a summary, a ledger status and a why. Each break below is made on a fresh copy of a manifest that passes."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    assert check_manifest(manifest) == []
    open_question = next(meta["id"] for meta in manifest["classes"] if meta["section"] == "open-questions")
    ledger_id = next(iter(manifest["ledger_classes"]))

    def class_of(edited: dict) -> dict:
        return next(meta for meta in edited["classes"] if meta["id"] == open_question)

    breaks = [
        (lambda edited: class_of(edited).pop("title"), "title must be a nonempty string"),
        (lambda edited: class_of(edited).update(summary=" "), "summary must be a nonempty string"),
        (lambda edited: class_of(edited).update(note=""), "note must be absent, null or a nonempty string"),
        (lambda edited: class_of(edited).update(status="intended"), "status must be one of"),
        (lambda edited: class_of(edited).update(section="elsewhere"), "section must be one of"),
        (lambda edited: class_of(edited).update(section="spot-check"), "section must be the one"),
        (lambda edited: edited["classes"].reverse(), "classes must be listed by section"),
        (lambda edited: edited.pop("ledger_classes"), "ledger_classes must map"),
        (
            lambda edited: edited["ledger_classes"][ledger_id].update(status="intended"),
            f"ledger_classes[{ledger_id}]",
        ),
    ]
    for change, message in breaks:
        edited = copy.deepcopy(manifest)
        change(edited)
        assert any(message in error for error in check_manifest(edited)), message


def test_the_manifest_lists_each_class_by_section_with_its_copy(mini_corpus, mini_bundle):
    """Each class carries the copy the sidebar shows, from its ledger entry or from rebuild/review/change-groups.yaml, and the classes are listed by section, then in ledger order, then in `CHANGE_GROUP_ORDER`, which is the order the triage index walks. The `ledger_classes` lookup holds every ledger entry, reached or not, in ledger order."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    ledger = load_ledger(mini_bundle.ledger)
    prose = load_change_group_prose()
    rank = {entry.id: index for index, entry in enumerate(ledger)}
    rank.update((group, len(ledger) + index) for index, group in enumerate(CHANGE_GROUP_ORDER))
    keys = [(SECTIONS.index(meta["section"]), rank[meta["id"]]) for meta in manifest["classes"]]
    assert keys == sorted(keys)
    assert len({section for section, _rank in keys}) == len(SECTIONS), "the mini corpus fills every section"
    by_id = {entry.id: entry for entry in ledger}
    for meta in manifest["classes"]:
        entry = by_id.get(meta["id"])
        if entry is None:
            group = prose[meta["id"]]
            expected = (group.title, group.summary, None, group.why)
        else:
            expected = (entry.title, entry.summary, entry.note or None, entry.why)
        assert (meta["title"], meta["summary"], meta["note"], meta["why"]) == expected, meta["id"]
    assert list(manifest["ledger_classes"]) == [entry.id for entry in ledger]
    assert all(
        record == {"title": entry.title, "summary": entry.summary, "status": entry.status, "why": entry.why}
        for entry, record in zip(ledger, manifest["ledger_classes"].values(), strict=True)
    )


def test_a_human_unit_of_an_identical_class_is_listed_under_the_change_its_picture_shows(
    mini_corpus, mini_bundle
):
    """Phase 1 moves a unit of an `ink_identical` ledger class that no machine check approves to the change group its picture shows: its fragment's class is the group, its `ledger_class` names the identical class, and it carries no exemplar flag. Every unit left in an identical class is machine-approved or exempt, so a "Looks identical" class lists only windows that draw as the old font does. The mini bundle holds such units in `identical-variant-name`."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    identical = {entry.id for entry in load_ledger(mini_bundle.ledger) if entry.ink_identical}
    rerouted = 0
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            for fragment in json.loads((mini_corpus / part).read_text(encoding="utf-8")):
                if meta["id"] in identical:
                    assert slim_fragment(fragment) and fragment["ledger_class"] is None, fragment["id"]
                elif fragment["ledger_class"] is not None:
                    rerouted += 1
                    assert fragment["ledger_class"] in identical, fragment["id"]
                    assert meta["id"] in CHANGE_GROUP_ORDER, fragment["id"]
                    assert not slim_fragment(fragment) and not fragment["exemplar"], fragment["id"]
    assert rerouted, "the mini corpus must re-route a unit"


def test_an_identical_class_whose_every_unit_is_re_routed_leaves_the_class_list(tmp_path, mini_bundle):
    """A ledger class declared `ink_identical` whose every unit no machine check approves loses all of them to change groups, so the corpus ships no class for it: it leaves `classes`, stays in the `ledger_classes` lookup, and the review facts name it unreached. The mini bundle's `join-switches-sides` units are all human, so declaring that class identical empties it."""
    emptied = "join-switches-sides"
    entries = yaml.safe_load(mini_bundle.ledger.read_text(encoding="utf-8"))
    for entry in entries:
        if entry["id"] == emptied:
            entry["ink_identical"] = True
    ledger_path = tmp_path / "m1-divergences.yaml"
    ledger_path.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True), encoding="utf-8")
    out = tmp_path / "corpus"
    review_build.build_m1(
        out,
        audit_path=MINI / "audit.tsv",
        ledger_path=ledger_path,
        subset_dir=MINI,
        after_font=MINI / "M1.otf",
        spec_root=mini_bundle.spec_root,
        subset_pack=mini_bundle.subset_pack,
        jobs=1,
    )
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert emptied not in {meta["id"] for meta in manifest["classes"]}
    assert emptied in manifest["ledger_classes"]
    rerouted = {
        fragment["ledger_class"]
        for meta in manifest["classes"]
        for part in unit_index.class_shards(meta)
        for fragment in json.loads((out / part).read_text(encoding="utf-8"))
    }
    assert emptied in rerouted
    coverage = facts.ledger_coverage(load_ledger(ledger_path), facts.invariant_group(manifest, {}))
    assert emptied in coverage.unreached


def test_check_unit_leaves_ink_deltas_out_of_the_table_diff_contract():
    """Table-diff units carry no `ink_deltas`, which only m1-audit mode requires. `rebuild/test_review_diff_units.py` runs the whole checker over real table-diff builds."""
    unit = _fixture_unit(ink_identical=False)
    without = {key: value for key, value in unit.items() if key != "ink_deltas"}
    assert not any("ink_deltas" in error for error in check_unit(without, "table-diff"))
    assert check_unit(without, "table-diff") == check_unit(unit, "table-diff")


def test_check_manifest_flags_a_malformed_inputs_fingerprint():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    manifest["inputs_fingerprint"] = {"data": "x"}
    assert any("inputs_fingerprint" in error for error in check_manifest(manifest))
    manifest["inputs_fingerprint"] = {key: 7 for key in fingerprint.COMPONENTS}
    assert any("inputs_fingerprint" in error for error in check_manifest(manifest))


@pytest.mark.parametrize(
    "human_unit_ids",
    (
        "u-0000",
        ["u-BLyK6Z13RtN", ["u-Nc3wVLJaPHB"]],
        ["not-a-unit"],
        ["u-0000"],
        ["u-BLyK6Z13RtN", "u-BLyK6Z13RtN"],
    ),
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


# The configurations a window without ·Owe can hold: every acceptance configuration but ss07, which the oracle compares only on the windows that hold ·Owe (`audit.WINDOW_SCOPED_CONFIGS`).
WITHOUT_OWE = tuple(config for config in ACCEPTANCE_CONFIGS if config != "ss07")


def _note(unit_configs, full_configs):
    return config_badge(unit_configs, full_configs)[1]


def _gate(unit_configs, full_configs):
    return config_badge(unit_configs, full_configs)[0]


def test_a_window_without_owe_badges_as_if_ss07_were_not_on_the_roster():
    """ss07 changes only windows that hold ·Owe, and the oracle compares it nowhere else (`audit.WINDOW_SCOPED_CONFIGS`), so a unit of any other window never holds it. The build resolves a unit's badge against the configurations its window can hold, so over a roster without ss07 a badge reads as it did before ss07 joined the roster, and only an ·Owe window's badge can name ss07."""
    configs = ("default", "ss04", "ss05", "ss10")
    assert _note(configs, WITHOUT_OWE) == "only when ss03 is off"
    assert _note(configs, ACCEPTANCE_CONFIGS) == "only when ss03 is off and ss07 is off"
    assert _note(("default", "ss03", "ss04", "ss05", "ss03+ss05"), WITHOUT_OWE) is None


def test_config_note_covers_the_general_gated_excluded_overlay_and_fallback_cases():
    full = WITHOUT_OWE
    non_ss10 = tuple(config for config in full if config != "ss10")
    assert _note(non_ss10, full) is None
    assert _note(full, full) is None
    assert _note(("ss03", "ss03+ss05"), full) == "only when ss03 is on"
    assert _note(("default", "ss04", "ss05"), full) == "only when ss03 is off"
    assert _note(("ss10",), full) == "only under ss10"
    assert _note(("ss04", "ss10"), full) == "only under: ss04, ss10"


def test_config_badge_caches_list_and_tuple_equivalents_together():
    full = ACCEPTANCE_CONFIGS
    from_lists = config_badge(["ss03"], list(full))
    from_tuples = config_badge(("ss03",), full)
    assert from_lists is from_tuples


def test_config_gate_pins_a_narrower_set_than_one_feature_can_describe():
    """A config set narrower than "every config with ss03 on" can still be described by a feature conjunction: a unit can differ under ss03 alone because turning ss05 on as well produces a different unit. Such a set resolves to the conjunction that selects it, so the badge names the features in their own colors instead of listing configs."""
    full = WITHOUT_OWE
    assert _note(("ss03",), full) == "only when ss03 is on and ss05 is off"
    assert _note(("ss05",), full) == "only when ss05 is on and ss03 is off"
    assert _note(("ss03+ss05",), full) == "only when ss03 is on and ss05 is on"
    assert _note(("default", "ss04"), full) == "only when ss03 is off and ss05 is off"
    assert _note(("default", "ss05"), full) == "only when ss03 is off and ss04 is off"
    assert _note(("default",), full) == "only when ss03 is off and ss04 is off and ss05 is off"


def test_config_gate_leaves_the_literal_fallback_to_sets_no_conjunction_pins():
    """The literal fallback remains for a disjunction such as ss04 or ss10, which no conjunction selects, because every all-off conjunction also admits default. The other fallback case, a set that needs more constraints than `GATE_CONSTRAINT_CAP`, cannot occur while there are only three joining features, since the cap covers all of them."""
    full = WITHOUT_OWE
    assert _gate(("ss04", "ss10"), full) is None
    assert _note(("ss04", "ss10"), full) == "only under: ss04, ss10"
    assert _gate(("ss03", "ss03+ss05", "ss10"), full) is None
    assert _note(("default", "ss03"), full) == "only when ss04 is off and ss05 is off"


def test_config_gate_clauses_carry_their_own_prose_and_the_note_is_their_join():
    """The clause `text` fields are the only copy of the badge's wording: the app renders each as one chip, and the note is their join. On-constraints come first, which puts the lit chip at the head of the badge."""
    full = ACCEPTANCE_CONFIGS
    gate = _gate(("ss03",), full)
    assert gate is not None
    assert gate == [
        {"feature": "ss03", "state": "on", "text": "only when ss03 is on"},
        {"feature": "ss05", "state": "off", "text": "and ss05 is off"},
    ]
    assert _note(("ss03",), full) == " ".join(clause["text"] for clause in gate)
    assert _gate(("ss10",), full) == [{"feature": "ss10", "state": "on", "text": "only under ss10"}]


def test_feature_descriptions_keys_match_the_readme_stylistic_set_list():
    """`FEATURE_DESCRIPTIONS` copies README's "Stylistic sets" section by hand with shorter wording, so only the set of keys is compared. Adding or removing a stylistic set in the README fails this test until the build's map is updated."""
    import re

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Stylistic sets", 1)[1].split("\n## ", 1)[0]
    readme_sets = set(re.findall(r"^- `(ss\d+)`:", section, re.MULTILINE))
    assert readme_sets, "no `ssNN` bullets found under README's Stylistic sets heading"
    assert set(FEATURE_DESCRIPTIONS) == readme_sets
    assert all(isinstance(text, str) and text for text in FEATURE_DESCRIPTIONS.values())


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
    """Checks the app shell at its source: `copy_static` copies the `fingerprint.static_files` listing of rebuild/review/static/ byte for byte, so the page a reviewer loads is this file, and its local references must resolve within that listing."""
    parser = _HtmlSanity()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    assert parser.errors == []
    assert parser.stack == []
    assert parser.counts["main"] == 1
    assert parser.counts["h1"] == 1
    served = fingerprint.static_files(STATIC_DIR)
    for reference in parser.references:
        if "//" in reference or reference.startswith(("#", "mailto:", "data:")):
            continue
        target = STATIC_DIR / reference.split("#")[0].split("?")[0]
        assert target in served, f"dangling reference {reference}"


def test_node_check_passes_on_every_shipped_script():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed on this machine")
    scripts = [path for path in fingerprint.static_files(STATIC_DIR) if path.suffix == ".js"]
    assert scripts
    for script in scripts:
        result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def _seed_refreshable_corpus(
    corpus: Path, inputs_fingerprint: dict, root: Path, mode: str | None = None
) -> dict:
    """Build a corpus holding only the files `_check_output_files` requires, and the unit store an m1-audit corpus requires: an empty manifest, of `mode` when one is given, the per-unit index, both app sidecars and the store stamped for that manifest, and the copied after font, whose bytes match both the sha the manifest records and the M1.otf under `root`. `refresh_assets` writes index.html itself from the static tree it copies. The font record names no `source`, so the check compares the copy with the manifest only and resolves no path inside the fixture tree."""
    corpus.mkdir(parents=True, exist_ok=True)
    font_bytes = b"OTTO-fixture"
    (root / "rebuild" / "out" / "m1").mkdir(parents=True, exist_ok=True)
    (root / "rebuild" / "out" / "m1" / "M1.otf").write_bytes(font_bytes)
    (corpus / "fonts").mkdir(parents=True, exist_ok=True)
    (corpus / "fonts" / "after.otf").write_bytes(font_bytes)
    manifest = {
        **({"format": review_build.MANIFEST_FORMAT, "mode": mode} if mode is not None else {}),
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0" * 40,
        "inputs_fingerprint": dict(inputs_fingerprint),
        "classes": [],
        "fonts": {"after": {"file": "fonts/after.otf", "sha256": hashlib.sha256(font_bytes).hexdigest()}},
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    unit_index.write_index(corpus, [])
    app_index.write_app_artifacts(corpus, {}, {})
    unit_cache.write_store(corpus, "environment", [], parts=[])
    return manifest


def _fake_static_tree(root: Path) -> Path:
    static = root / "rebuild" / "review" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<!DOCTYPE html>\n<html><body><main></main></body></html>\n")
    (static / "app.js").write_text("export const app = 1;\n")
    return static


def test_refresh_assets_restamps_only_the_static_component(tmp_path):
    """Checks everything an assets refresh does: it copies the app shell onto the corpus, rewrites the `static` fingerprint component in place, and changes nothing else. `generated_at` stays, so an open review session keeps its verdict store. The index, both sidecars and the unit store stay current, because the manifest's identity excludes this component. A table-diff corpus, the one the cycle builds, that `corpus_build_skippable` reported as stale before the refresh reports as current after it, so the next pass skips the build."""
    from rebuild.review import status
    from rebuild.tools.artifact_cycle import corpus_build_skippable

    root = tmp_path / "repo"
    static = _fake_static_tree(root)
    m1 = root / "rebuild" / "out" / "m1"
    m1.mkdir(parents=True)
    corpus = root / "rebuild" / "out" / "review"
    before = _seed_refreshable_corpus(
        corpus, {**status.table_diff_inputs(root, m1_out=m1), "static": "OLD"}, root, mode="table-diff"
    )
    assert not corpus_build_skippable(root, corpus)

    copied = review_build.refresh_assets(corpus, root)

    assert sorted(copied) == ["app.js", "index.html"]
    assert (corpus / "app.js").read_text() == (static / "app.js").read_text()
    assert (corpus / "index.html").read_text() == (static / "index.html").read_text()
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    recorded = manifest["inputs_fingerprint"]
    assert recorded["static"] == fingerprint.hash_paths(root, fingerprint.static_paths(root))
    assert recorded["static"] != "OLD"
    assert {name: value for name, value in recorded.items() if name != "static"} == {
        name: value for name, value in before["inputs_fingerprint"].items() if name != "static"
    }
    assert manifest["generated_at"] == before["generated_at"]
    assert unit_index.index_is_current(corpus)
    for name, fmt in app_index.ARTIFACTS:
        assert app_index.artifact_is_current(corpus, name, fmt)
    assert unit_index.store_is_current(corpus)
    assert corpus_build_skippable(root, corpus)


def test_refresh_assets_refuses_a_corpus_it_cannot_restamp(tmp_path):
    """A corpus built before input fingerprinting has no component to rewrite, so the refresh exits instead of adding one; that corpus needs a full rebuild."""
    root = tmp_path / "repo"
    _fake_static_tree(root)
    corpus = root / "rebuild" / "out" / "review"
    corpus.mkdir(parents=True)
    with pytest.raises(SystemExit):
        review_build.refresh_assets(corpus, root)
    (corpus / "manifest.json").write_text(json.dumps({"generated_at": "x", "classes": []}))
    with pytest.raises(SystemExit):
        review_build.refresh_assets(corpus, root)


def test_refresh_assets_puts_the_manifest_back_when_the_corpus_is_broken(tmp_path):
    """The restamp makes the corpus read as current, so a failed contract check must undo it. A manifest left claiming freshness over sidecars that no longer match it would make the next cycle skip the rebuild, which is the only repair."""
    root = tmp_path / "repo"
    _fake_static_tree(root)
    corpus = root / "rebuild" / "out" / "review"
    _seed_refreshable_corpus(corpus, {"static": "OLD"}, root)
    row_delta.head_path(corpus, unit_index.INDEX_NAME).unlink()
    with pytest.raises(SystemExit):
        review_build.refresh_assets(corpus, root)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs_fingerprint"]["static"] == "OLD"


def test_the_refresh_assets_verb_copies_the_shipped_app(tmp_path):
    """Runs the `refresh-assets` subcommand the artifact cycle calls, over the real rebuild/review/static/: the shipped shell is copied onto the corpus, and the `static` component carries the value `fingerprint.stage_b` computes."""
    corpus = tmp_path / "corpus"
    _seed_refreshable_corpus(corpus, {"static": "OLD"}, tmp_path)
    review_build.main(["refresh-assets", "--out", str(corpus)])
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs_fingerprint"]["static"] == fingerprint.hash_paths(
        REPO_ROOT, fingerprint.static_paths(REPO_ROOT)
    )
    assert (corpus / "index.html").read_text(encoding="utf-8") == (STATIC_DIR / "index.html").read_text(
        encoding="utf-8"
    )
    assert unit_index.index_is_current(corpus)


def test_the_app_copy_and_the_static_component_leave_out_dotfiles(tmp_path):
    """A `uv run` started in the app directory, or a `.DS_Store`, neither moves the `static` component nor reaches the served corpus, because `static_paths` and `copy_static` share one listing."""
    root = tmp_path / "repo"
    static = _fake_static_tree(root)
    clean = fingerprint.hash_paths(root, fingerprint.static_paths(root))
    (static / ".DS_Store").write_bytes(b"finder")
    (static / ".uv-cache" / "sdists-v9").mkdir(parents=True)
    (static / ".uv-cache" / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    (static / ".uv-cache" / "sdists-v9" / ".git").write_bytes(b"")

    assert [path.relative_to(static).as_posix() for path in fingerprint.static_paths(root)] == [
        "app.js",
        "index.html",
    ]
    assert fingerprint.hash_paths(root, fingerprint.static_paths(root)) == clean
    corpus = tmp_path / "corpus"
    assert sorted(review_build.copy_static(corpus, static)) == ["app.js", "index.html"]
    assert sorted(path.relative_to(corpus).as_posix() for path in corpus.rglob("*")) == [
        "app.js",
        "index.html",
    ]


def test_the_app_and_font_copies_never_leave_a_truncated_file_in_place(tmp_path, monkeypatch):
    """`copy_static` and `_copy_font` write the running app's files, which a tab can fetch mid-copy, so each copy goes to a `.partial` file that is renamed into place. A copy that fails halfway, or a font whose digest does not match, leaves the served file as it was and no staging file behind."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("new app\n")
    (static / "index.html").write_text("new index\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "app.js").write_text("old app\n")
    (corpus / "index.html").write_text("old index\n")
    real_copyfile = review_build.shutil.copyfile

    def copy_half_of_the_index(source, target):
        if Path(source).name != "index.html":
            return real_copyfile(source, target)
        Path(target).write_bytes(Path(source).read_bytes()[:3])
        raise OSError("disk full")

    monkeypatch.setattr(review_build.shutil, "copyfile", copy_half_of_the_index)
    with pytest.raises(OSError):
        review_build.copy_static(corpus, static)
    assert (corpus / "app.js").read_text() == "new app\n"
    assert (corpus / "index.html").read_text() == "old index\n"
    monkeypatch.setattr(review_build.shutil, "copyfile", real_copyfile)

    font = tmp_path / "M1.otf"
    font.write_bytes(b"a font that moved since the build loaded it")
    (corpus / "fonts").mkdir()
    (corpus / "fonts" / "after.otf").write_bytes(b"the font the units describe")
    with pytest.raises(SystemExit):
        review_build._copy_font(font, corpus, "after.otf", "AMS Review After", tmp_path, "0" * 64)
    assert (corpus / "fonts" / "after.otf").read_bytes() == b"the font the units describe"
    assert not list(corpus.rglob("*.partial"))


def test_a_second_copy_into_the_same_corpus_leaves_the_unchanged_app_files_and_fonts_in_place(tmp_path):
    """`copy_static` and `_copy_font` leave a copy that already holds the bytes they would put there in place, so a corpus build over an unchanged app and unchanged fonts rewrites none of them, and each keeps its inode and modification time, here set long ago so that any write would move it. `_copy_font` compares the copy with the digest the build took at load, so the font it keeps is the one the units describe. A changed app file is copied into place, and the fallback index.html a tree without one gets is kept the same way."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("app\n")
    font_digest = hashlib.sha256((MINI / "M1.otf").read_bytes()).hexdigest()
    corpus = tmp_path / "corpus"
    copies = [corpus / "app.js", corpus / "index.html", corpus / "fonts" / "after.otf"]

    def copy() -> dict:
        review_build.copy_static(corpus, static)
        return review_build._copy_font(
            MINI / "M1.otf", corpus, "after.otf", "AMS Review After", REPO_ROOT, font_digest
        )

    first = copy()
    for path in copies:
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = {path: (path.stat().st_ino, path.stat().st_mtime_ns) for path in copies}
    assert copy() == first
    assert {path: (path.stat().st_ino, path.stat().st_mtime_ns) for path in copies} == before
    (static / "app.js").write_text("new\n")
    copy()
    assert (corpus / "app.js").read_text() == "new\n"
    assert (corpus / "app.js").stat().st_mtime_ns != 1_000_000_000


def _padded(count: int, filler: int = 0) -> list[dict]:
    return [{"id": f"u-{index:04d}", "pad": "x" * filler} for index in range(count)]


def test_write_shard_keeps_a_class_under_the_cap_in_one_bare_file(tmp_path):
    """A class under the size cap keeps one `units/<class-id>.json` file, the path the small classes, the checked-in fixtures, and archived corpora already use, and its bytes match one `json.dumps` call."""
    fragments = _padded(4, 40)
    assert _write_shard(tmp_path, "small", fragments)[0] == ["units/small.json"]
    path = tmp_path / "units" / "small.json"
    assert path.read_bytes() == (json.dumps(fragments, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    assert sorted(entry.name for entry in (tmp_path / "units").iterdir()) == ["small.json"]


def test_write_shard_splits_a_class_that_outgrows_the_cap(tmp_path, monkeypatch):
    """Past the cap, the class is written as parts numbered from `000` with no bare file beside them. Each part is within the cap, each part's bytes match one `json.dumps` of that part, and the parts concatenate to the class in order."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 512)
    fragments = _padded(24, 60)
    parts, _spans = _write_shard(tmp_path, "big", fragments)
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
    """A single fragment larger than the cap is written in a part of its own, not split, and the fragments around it stay in their own parts."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 128)
    fragments = [{"id": "u-0000"}, {"id": "u-0001", "pad": "x" * 400}, {"id": "u-0002"}]
    parts, _spans = _write_shard(tmp_path, "big", fragments)
    assert [json.loads((tmp_path / part).read_text(encoding="utf-8")) for part in parts] == [
        [fragments[0]],
        [fragments[1]],
        [fragments[2]],
    ]


def test_write_shard_writes_an_empty_class_as_one_empty_array(tmp_path):
    assert _write_shard(tmp_path, "empty", []) == (["units/empty.json"], [])
    assert (tmp_path / "units" / "empty.json").read_bytes() == b"[]\n"


def test_write_shard_leaves_no_staging_file_behind_when_serializing_fails(tmp_path):
    """Parts are staged and renamed for the same reason `_write_json` stages: an encode that fails partway must leave the previous build's units in place, with no truncated part or leftover `.partial` file."""
    with pytest.raises(TypeError):
        _write_shard(tmp_path, "big", [{"id": "u-0000"}, {"id": object()}])
    assert list((tmp_path / "units").iterdir()) == []


def test_prune_orphan_shards_removes_only_unreferenced_json(tmp_path):
    """Every part the manifest lists is kept, and both forms of an unlisted name are removed: the bare file a class left when it grew into parts, and a numbered part a shrinking class no longer fills."""
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
    """`_write_json` writes a list one element at a time instead of building the whole string, so it assembles around per-element calls the framing and depth-1 indent that one `json.dumps` call would produce. The bytes must match because rebuild/test_unit_cache.py compares an incremental build's files with a from-scratch build's byte for byte. This test compares against the one-call form over the shapes a shard or manifest can hold, including empty collections and escaped non-ASCII, where hand-built framing is most likely to differ."""
    target = tmp_path / "payload.json"
    _write_json(target, payload)
    assert target.read_bytes() == (json.dumps(payload, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_write_json_reproduces_the_checked_in_fixture_shards(tmp_path):
    """The same comparison over the checked-in fixture shards and manifest: re-serializing them with `_write_json` reproduces them byte for byte."""
    sources = (FIXTURES / "manifest.json", *sorted((FIXTURES / "units").glob("*.json")))
    assert len(sources) > 1
    for source in sources:
        target = tmp_path / source.name
        _write_json(target, json.loads(source.read_text(encoding="utf-8")))
        assert target.read_bytes() == source.read_bytes(), source.name


def test_write_json_leaves_the_previous_file_alone_when_serializing_fails(tmp_path):
    """Because `_write_json` writes element by element, the encoder can fail after the file is started, so the write goes to a staging file that is renamed into place. Nothing downstream tolerates a half-written corpus file (`check_output_dir` and every manifest reader parse the files on disk, and the shards are written before the manifest that stamps them), so a failed write must leave the last good file in place and no staging file behind."""
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


def _export_corpus():
    """Build a stand-in for a built corpus from the checked-in fixture units, so `build_triage` sees every unit shape it handles differently. The fixture's units include no exempt unit and none without a policy draft, so two clones are added: a no-verdict unit whose verdict must be ignored, and a reject with no mechanical draft. The manifest is the fixture's with its totals and human-id list recomputed over the larger set. The machine-approved block is unchanged, because the one machine-approved unit is unchanged and `machine_approved_section` recomputes its own copy from the units.

    The units are returned projected, because the CLI only ever passes `build_triage` the projection, and testing with whole fixture units would not show that the projection holds every field the export reads.
    """
    manifest = copy.deepcopy(json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8")))
    units = {unit["id"]: unit for unit in _load_fixture_units()}
    template = units["u-Xp5KzYAPYK7"]

    def clone(unit_id, **changes):
        clone = copy.deepcopy(template)
        clone["id"] = unit_id
        clone.update(changes)
        units[unit_id] = clone

    clone("u-clone6exempt", no_verdict=True)
    clone("u-clone7nopol", drafts={**copy.deepcopy(template["drafts"]), "policy": None})
    manifest["totals"]["units"] = len(units)
    manifest["human_unit_ids"] = sorted(unit["id"] for unit in units.values() if not slim_fragment(unit))
    positions = {unit_id: position for position, unit_id in enumerate(manifest["human_unit_ids"])}
    return manifest, {
        unit_id: _triage_projection(
            unit,
            "the export fixture",
            batch=None if unit_id not in positions else positions[unit_id] // manifest["batch_size"],
        )
        for unit_id, unit in units.items()
    }


def test_the_machine_approved_classes_are_listed_in_the_manifests_class_order(mini_corpus):
    """`by_class` is keyed in order of first appearance among the machine-approved units in triage order, so its classes follow the manifest's `classes`: by section, then ledger classes in ledger order, then the promoted change groups in `change_groups.CHANGE_GROUP_ORDER`. They must not follow the workload table's load order, where an UNMATCHED unit sits by its lead-family pair without a change-group term, which would list the histogram in an order that follows neither the sidebar nor the ledger. The review-facts pins re-sort it into ledger order (`facts.class_rank`)."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    by_class = list(manifest["machine_approved"]["by_class"])
    classes = [meta["id"] for meta in manifest["classes"]]
    assert by_class and set(by_class) <= set(classes)
    assert by_class == [class_id for class_id in classes if class_id in by_class]
    assert sum(manifest["machine_approved"]["by_class"].values()) == manifest["machine_approved"]["units"]


def test_export_skips_verdicts_on_picture_identical_units():
    """The picture-identical check takes units out of the set of human units as the other two checks do, so a verdict on a unit it approved (one recorded before this check approved the unit) is counted as an ignored verdict and drafts nothing."""
    manifest, units = _export_corpus()
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
    """The field set is written out here rather than derived from `TRIAGE_KEYS`, as `test_the_index_covers_every_field_it_projects` in rebuild/test_unit_index.py does for the sidecar. Deriving it from the constant under test would only show that a dict comprehension keeps the keys it is given, and a field the export reads could be dropped from the projection with every test passing. Written out, adding a field is an explicit change, and dropping one fails here instead of showing up as a null in a triage YAML."""
    manifest, units = load_units(FIXTURES)
    fixture_units = {unit["id"]: unit for unit in _load_fixture_units()}
    assert set(units) == set(fixture_units)
    assert manifest == json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    positions = {unit_id: position for position, unit_id in enumerate(manifest["human_unit_ids"])}
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
        # A fragment carries no batch, so the batch comes from the manifest's index; every other field comes from the fragment.
        expected_batch = None if unit_id not in positions else positions[unit_id] // manifest["batch_size"]
        assert projected == {
            key: expected_batch if key == "batch" else fixture_units[unit_id].get(key) for key in projected
        }


def test_load_units_refuses_a_shard_missing_a_field_the_export_reads(tmp_path):
    """A field the triage export reads but the shard lacks means the corpus was built by a version this reader does not know. Read through `.get`, it would appear in the YAML as a null the reviewer cannot tell from a real absence, so the loader names the field and exits."""
    shutil.copytree(FIXTURES / "units", tmp_path / "units")
    shutil.copy(FIXTURES / "manifest.json", tmp_path / "manifest.json")
    shard = tmp_path / "units" / "fixture-mismatch.json"
    units = json.loads(shard.read_text(encoding="utf-8"))
    del units[0]["drafts"]
    _write_json(shard, units)
    with pytest.raises(SystemExit) as error:
        load_units(tmp_path)
    assert "drafts" in str(error.value)
    assert units[0]["id"] in str(error.value)


def test_export_round_trip(tmp_path):
    manifest, units = _export_corpus()
    ids = sorted(
        uid for uid, unit in units.items() if not unit["no_verdict"] and not machine_approved(units[uid])
    )
    exempt_unit = next(uid for uid in sorted(units) if units[uid]["no_verdict"])
    drafted_reject = next(uid for uid in ids[3:] if units[uid]["drafts"]["policy"])
    manual_reject = next(uid for uid in ids[3:] if units[uid]["drafts"]["policy"] is None)
    identical_unit = next(uid for uid in ids[3:] if uid not in (drafted_reject, manual_reject))
    human_skip = next(uid for uid in ids[3:] if uid not in (drafted_reject, manual_reject, identical_unit))
    machine_unit = next(uid for uid in sorted(units) if machine_approved(units[uid]))
    verdicts_path = tmp_path / "verdicts.json"
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": "2026-06-10T18:40:02Z",
        "verdicts": [
            {"unit": ids[0], "verdict": "approve", "note": "", "at": "2026-06-10T18:21:09Z"},
            # A verdict on a no-verdict unit (from a stale master, or a misclick on a revealed exempt row) is an ignored verdict: skipped, counted, and never drafted.
            {"unit": exempt_unit, "verdict": "reject", "note": "", "at": "2026-06-10T18:21:10Z"},
            {
                "unit": drafted_reject,
                "verdict": "reject",
                # A `configs` field from an older export format is ignored, because a verdict covers the whole unit.
                "configs": [units[drafted_reject]["configs"][0]],
                "note": "junction looks reached-for",
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
            # A verdict on a machine-approved unit is ignored the same way as one on a no-verdict unit: skipped, counted under its own key, never drafted.
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
    assert machine["unit_ids"] == sorted(uid for uid in units if machine_approved(units[uid]))
    assert len(machine["unit_ids"]) == manifest["machine_approved"]["units"]
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
    assert edit["why_stub"].endswith("junction looks reached-for")
    assert edit["file"].startswith("glyph_data/runes/")
    manual = by_unit[manual_reject]
    assert manual["keypath"] is None
    assert manual["suggested_record"] is None
    assert manual["no_mechanical_draft"]
    assert manual["names_provenance"] == units[manual_reject]["provenance"]

    assert len(triage["any_of"]) == 1
    assert triage["any_of"][0]["realized_as"] == "_assert_expect_any"
    assert all(status == "pass" for status in triage["any_of"][0]["candidates_parse"])

    # The neither section drafts nothing automatically: only the unit's identity, the reviewer's note, and the provenance records a follow-up fix could change.
    assert len(triage["neither"]) == 1
    neither = triage["neither"][0]
    assert neither == {
        "unit": ids[1],
        "codepoints": units[ids[1]]["codepoints"],
        "notation": units[ids[1]]["notation"],
        "note": "both joins look wrong",
        "names_provenance": units[ids[1]]["provenance"],
    }

    # The identical section drafts nothing either. These verdicts say the flagged difference is invisible, which is input for the ink comparator and the highlight tooling.
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


def test_export_reads_the_accepted_list_beside_an_export_of_the_stores_records(tmp_path, capsys):
    """An Export from the review app holds the store's records alone, so the triage export reads the accepted list beside it: a listed id on the corpus with no record in the file counts under its mark and drafts with an empty note, a record on a listed id is read in its place, and a listed id off the corpus is passed over without a warning."""
    manifest, units = _export_corpus()
    ids = sorted(
        uid
        for uid, unit in units.items()
        if not uid.startswith("u-clone") and not unit["no_verdict"] and not machine_approved(unit)
    )
    pinned = next(uid for uid in ids if units[uid]["drafts"]["pin"])
    overridden = next(uid for uid in ids if uid != pinned)
    listed = tmp_path / "accepted-units.txt"
    listed.write_bytes(
        accepted.render(
            "2026-11-05T18:00:00Z", {pinned: "approve", overridden: "approve", "u-1ZZZZZZZZZZ": "either"}
        )
    )
    verdicts = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "verdicts": [
            {
                "unit": overridden,
                "verdict": "neither",
                "note": "both look wrong",
                "at": "2026-11-06T09:00:00.000Z",
            }
        ],
    }
    triage = build_triage(
        manifest,
        units,
        verdicts,
        accepted.view_of_file(verdicts, accepted.read(listed), path="verdicts.json"),
    )
    counts = triage["review"]["counts"]
    assert (counts["approve"], counts["either"], counts["neither"]) == (1, 0, 1)
    assert [(pin["unit"], pin["note"]) for pin in triage["pins"]] == [(pinned, "")]
    assert [entry["unit"] for entry in triage["neither"]] == [overridden]
    assert "unknown units" not in capsys.readouterr().err


def test_export_rejects_bad_format(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "nope", "verdicts": []}))
    with pytest.raises(SystemExit):
        load_verdicts(bad)


def test_a_build_into_the_served_corpus_refuses_while_the_review_server_listens(tmp_path, monkeypatch):
    """A build rewrites the files the open tabs read in place, so it refuses to write the served corpus while the review server's port answers, and says to run the cycle, which builds beside it. The refusal holds for a server that names this checkout as its root and for one that names none. A build elsewhere, one with no server up, one beside a server that names another checkout (a worktree's first pass beside the main checkout's server), and one into a served corpus with no manifest (the cycle's first run, which has nothing to land beside) go ahead. The port it checks is the server's."""
    from rebuild.review.serve import PORT

    assert review_build.REVIEW_SERVER_PORT == PORT
    served = tmp_path / "rebuild" / "out" / "review"
    served.mkdir(parents=True)
    monkeypatch.setattr(review_build, "DEFAULT_OUT", served)
    monkeypatch.setattr(review_build, "review_server_root", lambda port=PORT: None)
    monkeypatch.setattr(review_build, "review_server_listening", lambda port=PORT: True)
    review_build.refuse_the_served_corpus(served)
    (served / "manifest.json").write_text("{}")
    monkeypatch.setattr(review_build, "build_m1", lambda *a, **k: pytest.fail("built under a live server"))
    for root in (None, str(review_build.REPO_ROOT)):
        monkeypatch.setattr(review_build, "review_server_root", lambda port=PORT, root=root: root)
        with pytest.raises(SystemExit, match="REFUSING TO BUILD.*make artifact-cycle"):
            review_build.main([])
    monkeypatch.setattr(
        review_build, "review_server_root", lambda port=PORT: str(tmp_path / "other-checkout")
    )
    review_build.refuse_the_served_corpus(review_build.DEFAULT_OUT)
    review_build.refuse_the_served_corpus(tmp_path / "elsewhere")
    monkeypatch.setattr(review_build, "review_server_listening", lambda port=PORT: False)
    review_build.refuse_the_served_corpus(review_build.DEFAULT_OUT)


def test_the_corpus_identity_moves_with_the_audit_s_content_and_not_with_a_fold(tmp_path, monkeypatch):
    """The corpus's `generated_at` takes the audit's time from its head, the time its content last moved (`AuditParts.content_time`), and the ink-signature store's stamp its content generation beside that time (`unit_cache.audit_identity`). A pass that writes a segment moves both; a pass that changes no row writes the head alone and moves neither, and so does a fold, which writes the materialized audit as a new base held as gzip members (#618's change 36), since the content is the one the head already served. A corpus over a single file keeps the file's modification time."""
    from rebuild.pipeline import oracle, row_delta
    from rebuild.review.audit import AuditParts

    out = tmp_path / "m1"
    rows = [f"default\tE65{digit}\tcell\tclass-a\tqsPea\tqsPea/g" for digit in "0123"]

    def write(lines: list[str]) -> None:
        with oracle.AuditWriter(out) as writer:
            writer.feed(("\n".join(lines) + "\n").encode("utf-8"))

    def identity() -> tuple[str, list[int]]:
        parts = AuditParts(out / oracle.AUDIT_NAME)
        try:
            return (
                review_build._generated_at(mtimes=(parts.content_time,)),
                unit_cache.audit_identity(parts.descriptors[0], parts.head),
            )
        finally:
            parts.close()

    single = out / oracle.AUDIT_NAME
    out.mkdir()
    single.write_text("\n".join([oracle.ORACLE_AUDIT_HEADER, *rows]) + "\n", encoding="utf-8")
    earlier = 1_793_491_200_000_000_000
    os.utime(single, ns=(earlier, earlier))
    held = identity()
    assert held[0] == "2026-11-01T00:00:00Z"
    write(rows)
    assert identity()[0] == held[0]
    adopted = identity()
    write(rows)
    assert identity() == adopted
    rows[1] = rows[1].replace("class-a", "UNMATCHED")
    write(rows)
    moved = identity()
    assert moved[0] > adopted[0] and moved[1] != adopted[1]
    monkeypatch.setattr(row_delta, "AUDIT_SEGMENT_CAP", 1)
    write(rows)
    head = row_delta.read_head(out, oracle.AUDIT_NAME)
    assert head is not None and (head.generation, head.segments) == (1, ())
    assert head.base.members is not None
    assert identity() == moved
