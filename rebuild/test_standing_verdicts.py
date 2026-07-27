"""Tests for the standing-approval fill: both structural pattern matches — the ligature shape (pivot glyph, seams into and out of it, follower family, post-ligature seam, flank-seam identity) and the extension-dropped shape (pivot glyph carrying the named exit extension, the seam it exits into holding its height, the full after-cell identity of pivot and follower, every other seam standing still, nothing ligating anywhere, and the unit's own judgment fields agreeing that this seam is the question) — the except_left guard, which reads a ligature's trailing left component and refuses the whole unit rather than the one position, blankness against the verdicts file (parked skip verdicts are not blank), the non-winning manifest stamp on every emitted record, and rules-file validation, which admits exactly one shape per rule and checks that shape's own coherence."""

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

EXT_RULE = {
    "id": "tea-i-exit-extension-dropped",
    "verdict": "approve",
    "note": "·Tea gives up its extension before ·I and the seam stays where it was",
    "match": {
        "before": {
            "pivot": "qsTea",
            "exit_extension": "ex-ext-1",
            "seam_out": "y0",
            "follower": "qsI",
        },
        "after": {
            "pivot_cells": ["qsTea/full/None/baseline/", "qsTea/full/x-height/baseline/"],
            "follower_cells": ["qsI/smaller-loop/baseline/None/", "qsI/smaller-loop/baseline/x-height/"],
        },
        "except_left": ["qsMay"],
    },
}


def unit(
    uid,
    glyphs,
    seams,
    cells,
    after_seams,
    *,
    no_verdict=False,
    groups=1,
    pair=None,
    secondary_seams=None,
    codepoints=None,
):
    return {
        "id": uid,
        "no_verdict": no_verdict,
        "render_groups": [{"configs": ["ss03"]} for _ in range(groups)],
        "codepoints": (
            ":".join(["E000"] * sum(sv._components(sv._family(name)) for name in glyphs))
            if codepoints is None
            else codepoints
        ),
        "before": {"glyphs": glyphs, "seams": seams},
        "after": {"cells": cells, "seams": after_seams},
        "pair": pair,
        "secondary_seams": secondary_seams,
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


def test_the_post_ligature_seam_is_required():
    moved = canonical()
    moved["after"]["seams"] = ["y0", "y0"]
    assert not sv._matches(RULE["match"], moved)


def test_the_seams_either_side_of_the_pivot_are_required():
    other_way_in = canonical()
    other_way_in["before"]["seams"] = ["y0", "break", "break"]
    assert not sv._matches(RULE["match"], other_way_in)
    other_way_out = canonical()
    other_way_out["before"]["seams"] = ["y0", "y5", "y5"]
    assert not sv._matches(RULE["match"], other_way_out)


def test_wrong_follower_defeats_the_match():
    wrong = canonical()
    wrong["before"]["glyphs"][3] = "qsIt"
    assert not sv._matches(RULE["match"], wrong)


def test_pivot_match_is_name_or_dotted_prefix_only():
    lookalike = canonical()
    lookalike["before"]["glyphs"][2] = "qsTea.halfx"
    assert not sv._matches(RULE["match"], lookalike)


def ligating_beside_a_guarded_instance(uid="u-4", second_left="qsOut.ex-y5"):
    return unit(
        uid,
        ["qsPea", "qsTea.half.en-y5", "qsOy", second_left, "qsTea.half.en-y5", "qsOy"],
        ["y5", "break", "break", "y5", "break"],
        [
            "qsPea/full/None/x-height/",
            "qsTea_qsOy/hapax/x-height/None/",
            f"{sv._family(second_left)}/hapax/None/x-height/",
            "qsTea/half/x-height/None/",
            "qsOy/hapax/None/None/",
        ],
        ["break", "break", "y5", "break"],
    )


def test_a_guarded_instance_refuses_the_whole_unit_on_the_ligature_shape():
    both = ligating_beside_a_guarded_instance()
    assert sv._matches(RULE["match"], both, guard=False)
    assert not sv._matches(RULE["match"], both)
    assert sv._matches(RULE["match"], ligating_beside_a_guarded_instance(second_left="qsAh.ex-y5"))


def tea_i(uid="u-10"):
    return unit(
        uid,
        ["qsTea.en-y8.ex-y0.ex-ext-1", "qsI"],
        ["y0"],
        ["qsTea/full/None/baseline/", "qsI/smaller-loop/baseline/None/"],
        ["y0"],
        pair={"left": 0, "right": 1},
    )


def medial_tea_i(uid="u-11", left="qsPea", left_cell="qsPea/full/None/None/", seam_into="break"):
    return unit(
        uid,
        [left, "qsTea.en-y8.ex-y0.ex-ext-1", "qsI", "qsTea.en-y5.ex-y0"],
        [seam_into, "y0", "y5"],
        [
            left_cell,
            "qsTea/full/None/baseline/",
            "qsI/smaller-loop/baseline/x-height/",
            "qsTea/full/x-height/None/",
        ],
        [seam_into, "y0", "y5"],
        pair={"left": 1, "right": 2},
    )


def test_word_initial_extension_drop_matches():
    assert sv._matches(EXT_RULE["match"], tea_i())


def test_a_word_initial_pivot_has_no_left_context_for_the_guard_to_hold():
    assert sv._matches(EXT_RULE["match"], tea_i(), guard=True)
    assert sv._matches(EXT_RULE["match"], tea_i(), guard=False)


def test_medial_extension_drop_matches():
    assert sv._matches(EXT_RULE["match"], medial_tea_i())


def test_a_changed_flank_seam_defeats_the_extension_match():
    drifted = medial_tea_i()
    drifted["after"]["seams"] = ["y5", "y0", "y5"]
    assert not sv._matches(EXT_RULE["match"], drifted)


def test_a_seam_that_changes_height_at_the_pivot_defeats_the_match():
    moved = tea_i()
    moved["after"]["seams"] = ["y5"]
    assert not sv._matches(EXT_RULE["match"], moved)


def test_an_unchanged_seam_at_the_wrong_height_defeats_the_match():
    elsewhere = tea_i()
    elsewhere["before"]["seams"] = ["y5"]
    elsewhere["after"]["seams"] = ["y5"]
    assert not sv._matches(EXT_RULE["match"], elsewhere)


def test_an_extension_the_pivot_keeps_is_not_an_extension_dropped():
    kept = tea_i()
    kept["after"]["cells"][0] = "qsTea/full/None/baseline/ex-ext-1"
    assert not sv._matches(EXT_RULE["match"], kept)


def test_a_pivot_that_never_carried_the_extension_does_not_match():
    bare = tea_i()
    bare["before"]["glyphs"][0] = "qsTea.en-y8.ex-y0"
    assert not sv._matches(EXT_RULE["match"], bare)


def test_the_named_pivot_and_follower_cells_are_required():
    other_follower = tea_i()
    other_follower["after"]["cells"][1] = "qsI/loop/baseline/None/"
    assert not sv._matches(EXT_RULE["match"], other_follower)
    other_pivot = tea_i()
    other_pivot["after"]["cells"][0] = "qsTea/half/None/baseline/"
    assert not sv._matches(EXT_RULE["match"], other_pivot)


def test_an_after_cell_naming_another_rune_defeats_the_match():
    renamed = tea_i()
    renamed["after"]["cells"][0] = "qsSee/full/None/baseline/"
    assert not sv._matches(EXT_RULE["match"], renamed)


def test_the_after_cells_pin_the_entry_and_exit_the_stance_alone_would_not():
    other_pivot_entry = tea_i()
    other_pivot_entry["after"]["cells"][0] = "qsTea/full/y6/baseline/"
    assert not sv._matches(EXT_RULE["match"], other_pivot_entry)
    other_follower_exit = tea_i()
    other_follower_exit["after"]["cells"][1] = "qsI/smaller-loop/baseline/y6/"
    assert not sv._matches(EXT_RULE["match"], other_follower_exit)


def test_an_extension_swapped_for_a_shorter_one_is_not_an_extension_dropped():
    swap_rule = {
        "before": {
            "pivot": "qsFee",
            "exit_extension": "ex-ext-3",
            "seam_out": "y5",
            "follower": "qsTea",
        },
        "after": {
            "pivot_cells": ["qsFee/loop/None/x-height/"],
            "follower_cells": ["qsTea/full/x-height/baseline/"],
        },
        "except_left": [],
    }
    swapped = unit(
        "u-12",
        ["qsFee.ex-y5.before-may.ex-ext-3", "qsTea.en-y5.ex-y0.after-fee"],
        ["y5"],
        ["qsFee/loop/None/x-height/ex-ext-1", "qsTea/full/x-height/baseline/"],
        ["y5"],
        pair={"left": 0, "right": 1},
    )
    assert not sv._matches(swap_rule, swapped)
    dropped = json.loads(json.dumps(swapped))
    dropped["after"]["cells"][0] = "qsFee/loop/None/x-height/"
    assert sv._matches(swap_rule, dropped)


def test_a_different_follower_family_does_not_match():
    wrong = tea_i()
    wrong["before"]["glyphs"][1] = "qsIt"
    wrong["after"]["cells"][1] = "qsIt/smaller-loop/baseline/None/"
    assert not sv._matches(EXT_RULE["match"], wrong)


def test_a_before_side_follower_family_alone_defeats_the_match():
    wrong = tea_i()
    wrong["before"]["glyphs"][1] = "qsIt"
    assert not sv._matches(EXT_RULE["match"], wrong)


def test_a_unit_whose_judged_pair_is_another_adjacency_is_refused():
    elsewhere = medial_tea_i()
    elsewhere["pair"] = {"left": 0, "right": 1}
    assert not sv._matches(EXT_RULE["match"], elsewhere)


def test_a_unit_with_no_judged_pair_is_refused():
    unjudged = tea_i()
    unjudged["pair"] = None
    assert not sv._matches(EXT_RULE["match"], unjudged)


def test_a_window_carrying_a_secondary_seam_is_refused():
    noisy = tea_i()
    noisy["secondary_seams"] = [{"pair": {"left": 1, "right": 2}, "home": None}]
    assert not sv._matches(EXT_RULE["match"], noisy)


def fee_tea_i(uid="u-13"):
    return unit(
        uid,
        ["qsFee.ex-y5.before-may.ex-ext-3", "qsTea.en-y5.ex-y0.after-fee.ex-ext-1", "qsI"],
        ["y5", "y0"],
        [
            "qsFee/loop/None/x-height/ex-ext-1",
            "qsTea/full/x-height/baseline/",
            "qsI/smaller-loop/baseline/None/",
        ],
        ["y5", "y0"],
        pair={"left": 0, "right": 1},
        secondary_seams=[{"pair": {"left": 1, "right": 2}, "home": None}],
    )


def test_a_window_whose_real_question_is_another_letters_ink_is_refused():
    assert not sv._matches(EXT_RULE["match"], fee_tea_i())


def ligating(uid="u-14", cells=()):
    return unit(
        uid,
        ["qsTea.en-y8.ex-y0.ex-ext-1", "qsI", "qsTea_qsOy", "qsDay", "qsUtter"],
        ["y0", "y5", "break", "break"],
        list(cells),
        ["y0", "y5", "break", "break"],
        pair={"left": 0, "right": 1},
    )


SAME_MERGES = [
    "qsTea/full/None/baseline/",
    "qsI/smaller-loop/baseline/x-height/",
    "qsTea_qsOy/hapax/x-height/None/",
    "qsDay/full/None/None/",
    "qsUtter/alternate/None/None/",
]

OTHER_MERGES = [
    "qsTea/full/None/baseline/",
    "qsI/smaller-loop/baseline/x-height/",
    "qsTea/full/x-height/None/",
    "qsOy/hapax/None/None/",
    "qsDay_qsUtter/full/None/None/",
]


def test_a_window_whose_two_sides_ligate_differently_is_refused():
    assert sv._matches(EXT_RULE["match"], ligating(cells=SAME_MERGES))
    assert not sv._matches(EXT_RULE["match"], ligating(cells=OTHER_MERGES))


def test_a_window_with_no_codepoints_to_align_against_is_refused():
    unstamped = ligating(cells=SAME_MERGES)
    unstamped["codepoints"] = ""
    assert not sv._matches(EXT_RULE["match"], unstamped)


def test_a_window_whose_names_do_not_account_for_its_codepoints_is_refused():
    unaccounted = tea_i()
    unaccounted["codepoints"] = "E652:E675:E679"
    assert not sv._matches(EXT_RULE["match"], unaccounted)


def test_except_left_holds_the_guarded_left_family_on_the_extension_shape():
    held = medial_tea_i(left="qsMay.ex-y5", left_cell="qsMay/full/None/x-height/", seam_into="y5")
    assert not sv._matches(EXT_RULE["match"], held)
    assert sv._matches(EXT_RULE["match"], held, guard=False)


def test_a_ligature_trailing_left_component_is_guarded_on_the_extension_shape():
    held = medial_tea_i(left="qsDay_qsMay.alt", left_cell="qsDay_qsMay/alt/None/x-height/", seam_into="y5")
    assert not sv._matches(EXT_RULE["match"], held)
    assert sv._matches(EXT_RULE["match"], held, guard=False)


def tea_i_tea_i(uid="u-15"):
    return unit(
        uid,
        ["qsTea.en-y8.ex-y0.ex-ext-1", "qsI", "qsTea.en-y5.ex-y0.ex-ext-1", "qsI"],
        ["y0", "y5", "y0"],
        [
            "qsTea/full/None/baseline/",
            "qsI/smaller-loop/baseline/x-height/",
            "qsTea/full/x-height/baseline/",
            "qsI/smaller-loop/baseline/None/",
        ],
        ["y0", "y5", "y0"],
        pair={"left": 0, "right": 1},
    )


def test_a_guarded_instance_refuses_the_whole_unit_even_beside_an_unguarded_one():
    guard_i = json.loads(json.dumps(EXT_RULE["match"]))
    guard_i["except_left"] = ["qsI"]
    repeated = tea_i_tea_i()
    assert sv._matches(guard_i, repeated, guard=False)
    assert not sv._matches(guard_i, repeated)


def test_neither_shape_reads_the_other_shapes_units():
    assert not sv._matches(RULE["match"], tea_i())
    assert not sv._matches(EXT_RULE["match"], canonical())


def test_checked_in_rules_file_loads():
    rules = sv.load_rules(sv.RULES)
    by_id = {rule["id"]: rule for rule in rules}
    assert by_id["tea-oy-ligature-break"]["match"]["except_left"] == ["qsOut"]
    assert by_id["tea-oy-ligature-break"]["verdict"] == "approve"


def test_the_checked_in_ligature_rule_reads_exactly_what_it_always_did():
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["tea-oy-ligature-break"]["match"]
    assert sv._matches(match, canonical())
    assert not sv._matches(match, canonical(left="qsOut.ex-ext-1"))
    assert sv._matches(match, canonical(left="qsOut.ex-ext-1"), guard=False)
    drifted = canonical()
    drifted["after"]["seams"] = ["break", "break"]
    assert not sv._matches(match, drifted)
    assert not sv._matches(match, tea_i())


def _write_rules(path, rules):
    path.write_text(json.dumps({"format": sv.FORMAT, "rules": rules}))
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].pop("follower"),
        lambda rule: rule["match"]["after"].pop("ligature"),
        lambda rule: rule["match"].update(except_left="qsOut"),
        lambda rule: rule["match"]["before"].update(exit_extension="ex-ext-1"),
    ],
)
def test_malformed_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].pop("exit_extension"),
        lambda rule: rule["match"]["before"].update(exit_extension=""),
        lambda rule: rule["match"]["before"].update(seam_into="y5"),
        lambda rule: rule["match"]["after"].pop("pivot_cells"),
        lambda rule: rule["match"]["after"].update(pivot_cells="qsTea/full/None/baseline/"),
        lambda rule: rule["match"]["after"].update(pivot_cells=[]),
        lambda rule: rule["match"]["after"].update(pivot_cells=["qsTea/full/None/baseline"]),
        lambda rule: rule["match"]["after"].update(pivot_cells=["qsTea//None/baseline/"]),
        lambda rule: rule["match"].update(except_left="qsMay"),
    ],
)
def test_malformed_extension_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(EXT_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_an_entry_side_extension_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(EXT_RULE))
    rule["match"]["before"]["exit_extension"] = "en-ext-1"
    with pytest.raises(SystemExit, match="not an exit-side extension"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_pivot_cell_still_carrying_an_exit_extension_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(EXT_RULE))
    rule["match"]["after"]["pivot_cells"] = ["qsTea/full/None/baseline/ex-ext-1"]
    with pytest.raises(SystemExit, match="has given up"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_cell_belonging_to_another_letter_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(EXT_RULE))
    rule["match"]["after"]["follower_cells"] = ["qsIt/smaller-loop/baseline/None/"]
    with pytest.raises(SystemExit, match="is not a qsI cell"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_both_shapes_is_refused(tmp_path):
    rule = json.loads(json.dumps(EXT_RULE))
    rule["match"]["after"]["ligature"] = "qsTea_qsOy"
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_neither_shape_is_refused(tmp_path):
    rule = json.loads(json.dumps(EXT_RULE))
    rule["match"]["after"].pop("follower_cells")
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_both_shapes_load_from_one_rules_file(tmp_path):
    rules = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [RULE, EXT_RULE]))
    assert [rule["id"] for rule in rules] == [RULE["id"], EXT_RULE["id"]]


def test_duplicate_rule_ids_are_refused(tmp_path):
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [RULE, RULE]))


def _surface(tmp_path, units):
    surface = tmp_path / "review"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(json.dumps({"generated_at": STAMP}))
    (surface / "units" / "all.json").write_text(json.dumps(units))
    return surface


def _run_main(tmp_path, monkeypatch, units, verdicts, rules_list=(RULE,)):
    surface = _surface(tmp_path, units)
    rules = _write_rules(tmp_path / "rules.yaml", list(rules_list))
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


def test_main_fills_both_shapes_from_one_rules_file(tmp_path, monkeypatch):
    units = [
        canonical("u-1"),
        tea_i("u-2"),
        medial_tea_i("u-3", left="qsMay.ex-y5", left_cell="qsMay/full/None/x-height/", seam_into="y5"),
        fee_tea_i("u-4"),
    ]
    payload = _run_main(tmp_path, monkeypatch, units, [], rules_list=(RULE, EXT_RULE))
    by_unit = {record["unit"]: record for record in payload["verdicts"]}
    assert set(by_unit) == {"u-1", "u-2"}
    assert by_unit["u-1"]["note"].startswith("[standing: tea-oy-ligature-break]")
    assert by_unit["u-2"]["note"].startswith("[standing: tea-i-exit-extension-dropped]")


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
