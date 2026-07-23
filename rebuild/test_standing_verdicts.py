"""Tests for the standing-approval fill: the structural pattern match (pivot glyph, seams into and out of it, follower family, post-ligature seam, flank-seam identity), the except_left guard including ligature-trailing left components, blankness against the verdicts file (parked skip verdicts are not blank), the non-winning manifest stamp on every emitted record, and rules-file validation."""

import json
import sys

import pytest

from rebuild.tools import standing_verdicts as sv

STAMP = "2026-07-10T00:00:00Z"

RULE = {
    "id": "tea-oy-ligature-break",
    "verdict": "approve",
    "note": "never a different opinion unless ·X is ·Out",
    "match": {
        "before": {"pivot": "qsTea.half", "seam_into": "y5", "seam_out": "break", "follower": "qsOy"},
        "after": {"ligature": "qsTea_qsOy", "seam_into": "break"},
        "except_left": ["qsOut"],
    },
}


def unit(uid, glyphs, seams, cells, after_seams, *, no_verdict=False, groups=1):
    return {
        "id": uid,
        "no_verdict": no_verdict,
        "render_groups": [{"configs": ["ss03"]} for _ in range(groups)],
        "before": {"glyphs": glyphs, "seams": seams},
        "after": {"cells": cells, "seams": after_seams},
    }


def canonical(uid="u-1", left="qsAh.ex-ext-1"):
    return unit(
        uid,
        ["qsPea", left, "qsTea.half.en-y5.after-xheight-exit", "qsOy"],
        ["y0", "y5", "break"],
        ["qsPea/full/None/baseline/", "qsAh/hapax/baseline/None/", "qsTea_qsOy/hapax/None/None/"],
        ["y0", "break"],
    )


def test_canonical_unit_matches():
    assert sv._matches(RULE["match"], canonical())


def test_out_left_is_held_by_the_guard():
    held = canonical(left="qsOut.ex-ext-1")
    assert not sv._matches(RULE["match"], held)
    assert sv._matches(RULE["match"], held, guard=False)


def test_ligature_left_matches_on_its_trailing_component():
    joined = unit(
        "u-2",
        ["qsDay_qsUtter.alt", "qsTea.half.en-y5", "qsOy"],
        ["y5", "break"],
        ["qsDay_qsUtter/alt/None/None/", "qsTea_qsOy/hapax/None/None/"],
        ["break"],
    )
    assert sv._matches(RULE["match"], joined)
    out_lead = unit(
        "u-3",
        ["qsDay_qsOut.alt", "qsTea.half.en-y5", "qsOy"],
        ["y5", "break"],
        ["qsDay_qsOut/alt/None/None/", "qsTea_qsOy/hapax/None/None/"],
        ["break"],
    )
    assert not sv._matches(RULE["match"], out_lead)


def test_a_changed_flank_seam_defeats_the_match():
    drifted = canonical()
    drifted["after"]["seams"] = ["break", "break"]
    assert not sv._matches(RULE["match"], drifted)


def test_wrong_follower_defeats_the_match():
    wrong = canonical()
    wrong["before"]["glyphs"][3] = "qsIt"
    assert not sv._matches(RULE["match"], wrong)


def test_pivot_match_is_name_or_dotted_prefix_only():
    lookalike = canonical()
    lookalike["before"]["glyphs"][2] = "qsTea.halfx"
    assert not sv._matches(RULE["match"], lookalike)


def test_checked_in_rules_file_loads():
    rules = sv.load_rules(sv.RULES)
    by_id = {rule["id"]: rule for rule in rules}
    assert by_id["tea-oy-ligature-break"]["match"]["except_left"] == ["qsOut"]
    assert by_id["tea-oy-ligature-break"]["verdict"] == "approve"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].pop("follower"),
        lambda rule: rule["match"]["after"].pop("ligature"),
        lambda rule: rule["match"].update(except_left="qsOut"),
    ],
)
def test_malformed_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(RULE))
    mutate(rule)
    path = tmp_path / "rules.yaml"
    path.write_text(json.dumps({"format": sv.FORMAT, "rules": [rule]}))
    with pytest.raises(SystemExit):
        sv.load_rules(path)


def test_duplicate_rule_ids_are_refused(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(json.dumps({"format": sv.FORMAT, "rules": [RULE, RULE]}))
    with pytest.raises(SystemExit):
        sv.load_rules(path)


def _surface(tmp_path, units):
    surface = tmp_path / "review"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    (surface / "units" / "all.json").write_text(json.dumps(units))
    return surface


def _run_main(tmp_path, monkeypatch, units, verdicts):
    surface = _surface(tmp_path, units)
    rules = tmp_path / "rules.yaml"
    rules.write_text(json.dumps({"format": sv.FORMAT, "rules": [RULE]}))
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(
        json.dumps({"format": "ams-review-verdicts/1", "manifest_generated_at": STAMP, "verdicts": verdicts})
    )
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "standing_verdicts.py",
            str(verdicts_path),
            "--surface",
            str(surface),
            "--rules",
            str(rules),
            "--out",
            str(out),
        ],
    )
    sv.main()
    return json.loads(out.read_text())


def test_main_fills_only_blank_matching_human_units(tmp_path, monkeypatch):
    units = [
        canonical("u-1"),
        canonical("u-2"),
        canonical("u-3"),
        canonical("u-4", left="qsOut.ex-ext-1"),
        canonical("u-5"),
    ]
    units[4]["no_verdict"] = True
    verdicts = [
        {"unit": "u-2", "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"},
        {"unit": "u-3", "verdict": "skip", "note": "[parked]", "at": "2026-07-11T00:00:00Z"},
    ]
    payload = _run_main(tmp_path, monkeypatch, units, verdicts)
    assert payload["format"] == "ams-review-verdicts/1"
    assert payload["manifest_generated_at"] == STAMP
    filled = {record["unit"] for record in payload["verdicts"]}
    assert filled == {"u-1"}
    record = payload["verdicts"][0]
    assert record["verdict"] == "approve"
    assert record["at"] == STAMP
    assert record["note"].startswith("[standing: tea-oy-ligature-break]")


def test_main_ignores_multi_render_group_units(tmp_path, monkeypatch):
    split = canonical("u-1")
    split["render_groups"] = [{"configs": ["ss03"]}, {"configs": ["ss02+ss03"]}]
    payload = _run_main(tmp_path, monkeypatch, [split], [])
    assert payload["verdicts"] == []


def test_main_refuses_a_stale_stamped_verdicts_file(tmp_path, monkeypatch):
    surface = _surface(tmp_path, [canonical("u-1")])
    rules = tmp_path / "rules.yaml"
    rules.write_text(json.dumps({"format": sv.FORMAT, "rules": [RULE]}))
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(
        json.dumps(
            {
                "format": "ams-review-verdicts/1",
                "manifest_generated_at": "2026-01-01T00:00:00Z",
                "verdicts": [],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "standing_verdicts.py",
            str(verdicts_path),
            "--surface",
            str(surface),
            "--rules",
            str(rules),
            "--out",
            str(tmp_path / "out.json"),
        ],
    )
    with pytest.raises(SystemExit, match="never be joined across manifests"):
        sv.main()
