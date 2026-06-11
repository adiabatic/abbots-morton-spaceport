"""Tests for the review-app build CLI: a full M1 build into a temp directory validated by the §7 contract checker (the same checker run over rebuild/review/fixtures/, so fixtures and real output can never drift), font sha256s, the HTML sanity check, node --check over every shipped script, the export round-trip, byte-identical determinism, and the table-diff build."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from rebuild.review.audit import ACCEPTANCE_CONFIGS
from rebuild.review.build import (
    build_m1,
    build_table_diff,
    check_manifest,
    check_output_dir,
    check_unit,
    config_note,
)
from rebuild.review.export import build_triage, load_units, load_verdicts

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "rebuild" / "review" / "fixtures"
M1_DIR = REPO_ROOT / "rebuild" / "out" / "m1"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("review-out")
    manifest = build_m1(out_dir)
    return out_dir, manifest


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


def test_full_build_passes_the_contract_checker(built):
    out_dir, manifest = built
    assert check_output_dir(out_dir) == []
    assert manifest["totals"] == {"units": 2410, "rows": 15525, "batches": 2}
    assert len(manifest["classes"]) == 13
    assert manifest["mode"] == "m1-audit"


def test_machine_approved_histogram_pins_the_census(built):
    """The kern-neutral ink census the rebatching rests on: after the round-1 reverts, 1,871 machine-approved / 539 human units, all machine-approved units inside the three name-grain classes, each of which is fully machine-approved (their visible stragglers differ only in the old font's kerning)."""
    out_dir, manifest = built
    machine = manifest["machine_approved"]
    assert machine["units"] == 1871
    assert manifest["totals"]["units"] - machine["units"] == 539
    assert machine["by_class"] == {
        "zwnj-word-initial-unification": 213,
        "dangling-anchor-dropped": 1520,
        "bare-name-live-join": 138,
    }
    assert isinstance(machine["rows"], int) and 0 < machine["rows"] < manifest["totals"]["rows"]
    assert machine["method"]
    by_id = {meta["id"]: meta for meta in manifest["classes"]}
    for meta in manifest["classes"]:
        expected = machine["by_class"].get(meta["id"], 0)
        assert meta["machine_approved_count"] == expected, meta["id"]
    assert by_id["dangling-anchor-dropped"]["unit_count"] == 1520
    assert by_id["zwnj-word-initial-unification"]["unit_count"] == 213
    assert by_id["bare-name-live-join"]["unit_count"] == 138


def test_secondary_seam_census_pins_the_real_data(built):
    """The secondary-seam resolution census over the post-round-1 M1 workload: 73 units carry visible markers (140 raw secondary seams, of which 63 resolve to an ink-identical home and are suppressed as invisible), 18 seams link to the shorter unit where the same behavior is the primary judgment, and 59 are genuinely context-dependent at the depth-2 horizon (no substring unit reproduces both outcomes with the seam as its primary), so they carry home null and are judged in place."""
    out_dir, manifest = built
    assert manifest["secondary_seams"] == {
        "units_with_markers": 73,
        "seams_homed": 18,
        "seams_homeless": 59,
        "seams_suppressed_invisible": 63,
    }

    units_by_id = {}
    for meta in manifest["classes"]:
        for unit in json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8")):
            units_by_id[unit["id"]] = unit
    homed = 0
    homeless = 0
    units_with = 0
    for unit in units_by_id.values():
        seams = unit.get("secondary_seams")
        if not seams:
            continue
        units_with += 1
        assert unit["ink_identical"] is False, unit["id"]
        primary = (unit["pair"]["left"], unit["pair"]["right"])
        for seam in seams:
            assert (seam["pair"]["left"], seam["pair"]["right"]) != primary, unit["id"]
            if seam["home"] is None:
                homeless += 1
                continue
            homed += 1
            home = units_by_id[seam["home"]]
            tokens = unit["codepoints"].split(":")
            home_tokens = home["codepoints"].split(":")
            assert len(home_tokens) <= len(tokens), unit["id"]
            assert any(
                tokens[offset : offset + len(home_tokens)] == home_tokens
                for offset in range(len(tokens) - len(home_tokens) + 1)
            ), f"{unit['id']}: home {home['id']} is not a substring"
            assert home["pair"] is not None, f"{unit['id']}: home {home['id']} has no primary pair"
            assert home["ink_identical"] is False, f"{unit['id']}: home {home['id']} is machine-approved"
    assert (units_with, homed, homeless) == (73, 18, 59)


def test_known_secondary_seam_homes_at_the_shorter_primary(built):
    """The worked example: ·Pea·Pea·It·It's trailing ·It·It seam is a secondary divergence whose home is the ·Pea·It·It unit, where that same join is the primary (amber-band) judgment."""
    out_dir, manifest = built
    units_by_codepoints = {}
    for meta in manifest["classes"]:
        for unit in json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8")):
            units_by_codepoints[unit["codepoints"]] = unit
    unit = units_by_codepoints["E650:E650:E670:E670"]
    assert unit["pair"] == {"left": 0, "right": 1}
    (seam,) = unit["secondary_seams"]
    assert seam["pair"] == {"left": 2, "right": 3}
    home = units_by_codepoints["E650:E670:E670"]
    assert seam["home"] == home["id"]
    assert home["pair"] == {"left": 1, "right": 2}
    for side in ("before", "after"):
        assert seam[side]["x_min"] <= seam[side]["x_max"] <= seam[side]["advance_total"]


def test_batches_cover_the_human_workload_only(built):
    out_dir, manifest = built
    human_batches = []
    for meta in manifest["classes"]:
        shard = json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8"))
        for unit in shard:
            if unit["ink_identical"]:
                assert unit["batch"] is None, unit["id"]
            else:
                human_batches.append((unit["id"], unit["batch"]))
    human_batches.sort()
    assert len(human_batches) == 539
    assert [batch for _unit_id, batch in human_batches] == [
        index // 300 for index in range(len(human_batches))
    ]
    assert manifest["totals"]["batches"] == 2


def test_every_built_unit_has_one_render_group_and_a_summary(built):
    """The M1 render-group invariant at the output layer: every shipped unit has exactly one group covering all its configs (the dedupe key guarantees it), and every unit carries the always-visible one-line summary."""
    out_dir, manifest = built
    for meta in manifest["classes"]:
        shard = json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8"))
        for unit in shard:
            assert len(unit["render_groups"]) == 1, unit["id"]
            assert unit["render_groups"][0]["configs"] == unit["configs"], unit["id"]
            assert unit["summary"].startswith("New: "), unit["id"]
            assert "decided by" in unit["summary"] or "no policy record" in unit["summary"], unit["id"]


def test_config_note_covers_the_general_gated_excluded_overlay_and_fallback_cases():
    full = ACCEPTANCE_CONFIGS
    non_ss10 = tuple(config for config in full if config != "ss10")
    assert config_note(non_ss10, full) is None
    assert config_note(full, full) is None
    assert config_note(("ss03", "ss02+ss03", "ss02+ss03+ss05"), full) == "only when ss03 is on"
    assert config_note(("default", "ss02", "ss04", "ss05"), full) == "only when ss03 is off"
    assert config_note(("ss10",), full) == "only under ss10"
    assert config_note(("default", "ss03"), full) == "only under: default, ss03"


def test_config_note_distribution_over_the_built_output(built):
    """The M1 facts the badge design rests on: the config-set space collapses to four notes — null for the 2,028 units covering every non-ss10 config, plus the ss03-gated, ss03-excluded, and ss10-only minorities."""
    out_dir, manifest = built
    histogram = {}
    for meta in manifest["classes"]:
        for unit in json.loads((out_dir / meta["shard"]).read_text(encoding="utf-8")):
            histogram[unit["config_note"]] = histogram.get(unit["config_note"], 0) + 1
    assert histogram == {
        None: 2028,
        "only when ss03 is on": 148,
        "only when ss03 is off": 217,
        "only under ss10": 17,
    }


def test_built_classes_keep_ledger_order_and_counts(built):
    _out_dir, manifest = built
    ledger = yaml.safe_load((REPO_ROOT / "rebuild" / "m1-divergences.yaml").read_text())
    ledger_order = [entry["id"] for entry in ledger if entry["id"] != "kern-channel-out-of-scope"]
    assert [meta["id"] for meta in manifest["classes"]] == ledger_order
    by_id = {entry["id"]: entry for entry in ledger}
    for meta in manifest["classes"]:
        assert meta["row_count"] == by_id[meta["id"]]["count"]
        assert meta["why"] == by_id[meta["id"]]["why"].strip()


def test_font_copies_match_recorded_sha256(built):
    import hashlib

    out_dir, manifest = built
    for side in ("before", "after"):
        record = manifest["fonts"][side]
        digest = hashlib.sha256((out_dir / record["file"]).read_bytes()).hexdigest()
        assert digest == record["sha256"]
        source = hashlib.sha256((REPO_ROOT / record["source"]).read_bytes()).hexdigest()
        assert digest == source


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


def test_index_html_sanity(built):
    out_dir, _manifest = built
    parser = _HtmlSanity()
    parser.feed((out_dir / "index.html").read_text(encoding="utf-8"))
    assert parser.errors == []
    assert parser.stack == []
    assert parser.counts["main"] == 1
    assert parser.counts["h1"] == 1
    for reference in parser.references:
        if "//" in reference or reference.startswith(("#", "mailto:", "data:")):
            continue
        target = out_dir / reference.split("#")[0].split("?")[0]
        assert target.exists(), f"dangling reference {reference}"


def test_node_check_passes_on_every_shipped_script(built):
    out_dir, _manifest = built
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed on this machine")
    scripts = sorted(out_dir.rglob("*.js"))
    for script in scripts:
        result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_builds_are_byte_identical(built, tmp_path):
    out_dir, _manifest = built
    second = tmp_path / "again"
    build_m1(second)
    first_manifest = (out_dir / "manifest.json").read_bytes()
    assert first_manifest == (second / "manifest.json").read_bytes()
    for shard in sorted((out_dir / "units").glob("*.json")):
        assert shard.read_bytes() == (second / "units" / shard.name).read_bytes()


def test_export_round_trip(built, tmp_path):
    out_dir, manifest = built
    _manifest, units = load_units(out_dir)
    ids = sorted(units)
    drafted_reject = next(uid for uid in ids[4:] if units[uid]["drafts"]["policy"])
    manual_reject = next(uid for uid in ids[4:] if units[uid]["drafts"]["policy"] is None)
    verdicts_path = tmp_path / "verdicts.json"
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": "2026-06-10T18:40:02Z",
        "verdicts": [
            {"unit": ids[0], "verdict": "approve", "note": "", "at": "2026-06-10T18:21:09Z"},
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
        ],
    }
    verdicts_path.write_text(json.dumps(payload))
    triage = build_triage(manifest, units, load_verdicts(verdicts_path))

    counts = triage["review"]["counts"]
    assert counts["approve"] == 1
    assert counts["reject"] == 2
    assert counts["either"] == 1
    assert counts["neither"] == 1
    assert counts["skip"] == 1
    assert counts["units_total"] == 2410
    assert counts["human_units_total"] == 539

    machine = triage["machine_approved"]
    assert machine["count"] == 1871
    assert machine["by_class"] == {
        "zwnj-word-initial-unification": 213,
        "dangling-anchor-dropped": 1520,
        "bare-name-live-join": 138,
    }
    assert machine["method"]
    assert machine["rows_covered"] == sum(
        len(unit["configs"]) for unit in units.values() if unit["ink_identical"]
    )
    expanded = []
    for token in machine["unit_ids"]:
        if ".." in token:
            start, end = token.split("..")
            expanded.extend(range(int(start[2:]), int(end[2:]) + 1))
        else:
            expanded.append(int(token[2:]))
    assert len(expanded) == 1871
    assert {f"u-{number:04d}" for number in expanded} == {
        unit_id for unit_id, unit in units.items() if unit["ink_identical"]
    }
    assert counts["rows_covered"] == sum(
        len(units[uid]["configs"]) for uid in (ids[0], drafted_reject, manual_reject, ids[2], ids[3], ids[1])
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
    section_units = {
        "pins": {entry["unit"] for entry in triage["pins"]},
        "policy_edits": {entry["unit"] for entry in triage["policy_edits"]},
        "any_of": {entry["unit"] for entry in triage["any_of"]},
        "neither": {entry["unit"] for entry in triage["neither"]},
    }
    assert section_units == {
        "pins": {ids[0]},
        "policy_edits": {drafted_reject, manual_reject},
        "any_of": {ids[2]},
        "neither": {ids[1]},
    }

    text = yaml.safe_dump(triage, sort_keys=False, allow_unicode=True, width=10**6)
    parsed = yaml.safe_load(text)
    assert set(parsed) == {"review", "machine_approved", "pins", "policy_edits", "any_of", "neither"}


def test_export_rejects_bad_format(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "nope", "verdicts": []}))
    with pytest.raises(SystemExit):
        load_verdicts(bad)


def test_table_diff_build(tmp_path):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for name in ("settlement-default.tsv", "treaties-default.tsv"):
        shutil.copyfile(M1_DIR / name, old_dir / name)
        shutil.copyfile(M1_DIR / name, new_dir / name)
    settlement = (new_dir / "settlement-default.tsv").read_text().splitlines()
    settlement[-1] = settlement[-1].rsplit("\t", 2)[0] + "\tjoint\tsynthetic-pointer"
    (new_dir / "settlement-default.tsv").write_text("\n".join(settlement) + "\n")

    out_dir = tmp_path / "out"
    manifest = build_table_diff(
        out_dir,
        old_dir,
        new_dir,
        REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
        M1_DIR / "M1.otf",
        with_witnesses=True,
        witness_depth=2,
    )
    assert manifest["mode"] == "table-diff"
    assert manifest["totals"]["units"] == 1
    assert check_output_dir(out_dir) == []
    shard = json.loads((out_dir / "units" / "changed.json").read_text(encoding="utf-8"))
    assert len(shard) == 1
    assert shard[0]["class"] == "changed"
    assert "synthetic-pointer" in shard[0]["explain"] or "synthetic-pointer" in " ".join(
        shard[0]["provenance"]
    )
