"""Tests for the standing-approval fill: all four delta shapes — the two structural pattern matches, being the ligature shape (pivot glyph, seams into and out of it, follower family, post-ligature seam, flank-seam identity) and the extension-dropped shape (pivot glyph carrying the named exit extension, the seam it exits into holding its height, the full after-cell identity of pivot and follower, every other seam standing still, nothing ligating anywhere, and the unit's own judgment fields agreeing that this seam is the question), the ink-exact ink-delta shape (the unit's persisted per-config digests being a nonempty subset of the ones the rule blesses, so an ink-identical window matches nothing and one unlisted delta under one config fails the whole unit closed, and a surface predating the field refuses the run outright), and the rendered-pixel slide shape, whose preconditions are read off the index record before anything is shaped (a nonempty `ink_deltas` holding one distinct digest whose keys are exactly the unit's config set, and a pivot-prefix name among the recorded before glyphs) and whose geometry is then re-derived in a purpose-built font pair, where the pivot keeps its exact ink with its own-frame origin displaced by the declared column count and every span's union of ink slides cumulatively — so a union-invisible name-grain re-spelling to the pivot's right rides along, while one stray pixel anywhere in the window, or a font pair that never settles into the named pivot, fails the match closed — the except_left guard, which reads a ligature's trailing left component and refuses the whole unit rather than the one position, blankness against the verdicts file (parked skip verdicts are not blank), the non-winning manifest stamp on every emitted record, and rules-file validation, which admits exactly one shape per rule and checks that shape's own coherence."""

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

DELTA_A = "d-14c0f8d9cc8c"
DELTA_B = "d-9b8a7c6d5e4f"
UNLISTED_DELTA = "d-000000000001"

INK_RULE = {
    "id": "may-entry-stub-dropped",
    "verdict": "approve",
    "note": "the ·May has lost its left-side stub pixel and nothing else moved",
    "match": {
        "after": {"ink_deltas": [DELTA_A, DELTA_B]},
        "except_left": [],
    },
}

SLIDE_DELTA = "d-aaaaaaaaaaaa"

SLIDE_RULE = {
    "id": "see-grounded-left-column-dropped",
    "verdict": "approve",
    "note": "the grounded ·See sits a column closer to what precedes it and everything after it slides over",
    "match": {
        "before": {"pivots": ["qsSee.ex-y0"]},
        "after": {"pivots": ["qsSee.straighter"], "slide": -1},
        "except_left": [],
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
    configs=("ss03",),
    ink_deltas=None,
    batch=0,
):
    return {
        "id": uid,
        "batch": batch,
        "no_verdict": no_verdict,
        "render_groups": [{"configs": ["ss03"]} for _ in range(groups)],
        "codepoints": (
            ":".join(["E000"] * sum(sv._components(sv._family(name)) for name in glyphs))
            if codepoints is None
            else codepoints
        ),
        "configs": list(configs),
        "ink_deltas": ink_deltas,
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


def ink_delta_unit(uid="i-1", glyphs=("qsRoe.ex-y0", "qsMay.en-y0"), deltas=None):
    made = unit(
        uid,
        list(glyphs),
        ["y0"] * (len(glyphs) - 1),
        [f"{sv._family(name)}/full/None/None/" for name in glyphs],
        ["y0"] * (len(glyphs) - 1),
        pair={"left": 0, "right": 1},
    )
    made["ink_deltas"] = {"default": DELTA_A, "ss03": DELTA_A} if deltas is None else deltas
    return made


def guarding(rule, families):
    match = json.loads(json.dumps(rule["match"]))
    match["except_left"] = list(families)
    return match


def test_a_window_whose_whole_ink_change_is_blessed_matches():
    assert sv._matches(INK_RULE["match"], ink_delta_unit())


def test_a_unit_may_show_a_strict_subset_of_a_multi_flavor_rule():
    assert sv._matches(INK_RULE["match"], ink_delta_unit(deltas={"default": DELTA_B}))
    assert sv._matches(INK_RULE["match"], ink_delta_unit(deltas={"default": DELTA_A, "ss03": DELTA_B}))


def test_one_unlisted_delta_under_one_config_defeats_the_match():
    strayed = ink_delta_unit(deltas={"default": DELTA_A, "ss03": UNLISTED_DELTA})
    assert not sv._matches(INK_RULE["match"], strayed)
    assert not sv._matches(INK_RULE["match"], ink_delta_unit(deltas={"default": UNLISTED_DELTA}))


def test_a_unit_that_predates_the_ink_delta_field_does_not_match():
    bare = ink_delta_unit()
    del bare["ink_deltas"]
    assert not sv._matches(INK_RULE["match"], bare)


def test_an_ink_identical_unit_does_not_match():
    assert not sv._matches(INK_RULE["match"], ink_delta_unit(deltas={}))


def test_an_ink_deltas_field_that_is_not_a_mapping_does_not_match():
    assert not sv._matches(INK_RULE["match"], ink_delta_unit(deltas=[DELTA_A]))
    assert not sv._matches(INK_RULE["match"], ink_delta_unit(deltas=DELTA_A))


def test_except_left_holds_the_guarded_family_on_the_ink_delta_shape():
    held = ink_delta_unit(glyphs=("qsOut.ex-y0", "qsMay.en-y0"))
    assert not sv._matches(guarding(INK_RULE, ["qsOut"]), held)
    assert sv._matches(guarding(INK_RULE, ["qsOut"]), held, guard=False)


def test_a_ligature_trailing_left_component_is_guarded_on_the_ink_delta_shape():
    held = ink_delta_unit(glyphs=("qsPea", "qsDay_qsMay.alt", "qsIt"))
    assert not sv._matches(guarding(INK_RULE, ["qsMay"]), held)
    assert sv._matches(guarding(INK_RULE, ["qsMay"]), held, guard=False)
    assert sv._matches(guarding(INK_RULE, ["qsDay"]), held)


def test_the_ink_delta_guard_reads_the_whole_window_and_not_a_pivots_left():
    held = ink_delta_unit()
    assert not sv._matches(guarding(INK_RULE, ["qsMay"]), held)
    assert sv._matches(guarding(INK_RULE, ["qsMay"]), held, guard=False)


def test_neither_shape_reads_the_other_shapes_units():
    assert not sv._matches(RULE["match"], tea_i())
    assert not sv._matches(EXT_RULE["match"], canonical())


def test_the_ink_delta_shape_and_the_structural_shapes_do_not_read_each_others_units():
    assert not sv._matches(INK_RULE["match"], canonical())
    assert not sv._matches(INK_RULE["match"], tea_i())
    assert not sv._matches(RULE["match"], ink_delta_unit())
    assert not sv._matches(EXT_RULE["match"], ink_delta_unit())


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


def test_an_ink_delta_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [INK_RULE]))
    assert rule["verdict"] == "approve"
    assert rule["note"]
    assert rule["match"] == {"after": {"ink_deltas": [DELTA_A, DELTA_B]}, "except_left": []}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["after"].pop("ink_deltas"),
        lambda rule: rule["match"]["after"].update(ink_deltas=DELTA_A),
        lambda rule: rule["match"]["after"].update(ink_deltas=[]),
        lambda rule: rule["match"]["after"].update(ink_deltas=["x-14c0f8d9cc8c"]),
        lambda rule: rule["match"]["after"].update(ink_deltas=["14c0f8d9cc8c"]),
        lambda rule: rule["match"]["after"].update(ink_deltas=["d-14c0f8d9cc"]),
        lambda rule: rule["match"]["after"].update(ink_deltas=["d-14c0f8d9cc8cab"]),
        lambda rule: rule["match"]["after"].update(ink_deltas=["d-14C0F8D9CC8C"]),
        lambda rule: rule["match"]["after"].update(ink_deltas=["d-14c0f8d9cc8g"]),
        lambda rule: rule["match"]["after"].update(ink_deltas=[DELTA_A, None]),
        lambda rule: rule["match"].update(except_left="qsMay"),
    ],
)
def test_malformed_ink_delta_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(INK_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_an_ink_delta_rule_carrying_a_before_block_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(INK_RULE))
    rule["match"]["before"] = {"pivot": "qsMay", "seam_into": "y0"}
    with pytest.raises(SystemExit, match="carries no match.before block"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_an_empty_before_block_is_refused_too(tmp_path):
    rule = json.loads(json.dumps(INK_RULE))
    rule["match"]["before"] = {}
    with pytest.raises(SystemExit, match="carries no match.before block"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_repeated_digest_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(INK_RULE))
    rule["match"]["after"]["ink_deltas"] = [DELTA_A, DELTA_B, DELTA_A]
    with pytest.raises(SystemExit, match="repeats a digest"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_the_empty_delta_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(INK_RULE))
    rule["match"]["after"]["ink_deltas"] = [DELTA_A, sv.EMPTY_DELTA_DIGEST]
    with pytest.raises(SystemExit, match="never needs a rule"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_the_ink_delta_and_ligature_shapes_at_once_is_refused(tmp_path):
    rule = json.loads(json.dumps(INK_RULE))
    rule["match"]["after"]["ligature"] = "qsTea_qsOy"
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_both_shapes_load_from_one_rules_file(tmp_path):
    rules = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [RULE, EXT_RULE]))
    assert [rule["id"] for rule in rules] == [RULE["id"], EXT_RULE["id"]]


def test_all_four_shapes_load_from_one_rules_file(tmp_path):
    rules = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [RULE, EXT_RULE, INK_RULE, SLIDE_RULE]))
    assert [rule["id"] for rule in rules] == [
        RULE["id"],
        EXT_RULE["id"],
        INK_RULE["id"],
        SLIDE_RULE["id"],
    ]


def test_duplicate_rule_ids_are_refused(tmp_path):
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [RULE, RULE]))


def _rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


TWO_COLUMNS = (_rect(0, 0, 100, 150),)
GROUNDED_SEE = (_rect(100, 0, 200, 150),)
STRAIGHTER_SEE = (_rect(50, 0, 150, 150),)
TUCKED_FOLLOWER = (_rect(50, 0, 100, 150),)
TWO_COLUMNS_AND_A_PIXEL = (((0, 0), (100, 0), (100, 150), (50, 150), (50, 200), (0, 200)),)
TUCKED_FOLLOWER_AND_A_PIXEL = (((50, 0), (150, 0), (150, 50), (100, 50), (100, 150), (50, 150)),)

BEFORE_GLYPHS = {
    "qsL": (TWO_COLUMNS, 100),
    "qsSee.ex-y0": (GROUNDED_SEE, 250),
    "qsF1": (TWO_COLUMNS, 50),
    "qsF2": (TWO_COLUMNS, 100),
}
AFTER_GLYPHS = {
    "qsL": (TWO_COLUMNS, 100),
    "qsSee.straighter": (STRAIGHTER_SEE, 200),
    "qsF1": (TWO_COLUMNS, 50),
    "qsF2": (TUCKED_FOLLOWER, 100),
    "qsOther": (TWO_COLUMNS, 100),
}
BEFORE_CMAP = {
    0xE001: "qsL",
    0xE002: "qsSee.ex-y0",
    0xE003: "qsF1",
    0xE004: "qsF2",
    0xE005: "qsSee.ex-y0",
}
AFTER_CMAP = {
    0xE001: "qsL",
    0xE002: "qsSee.straighter",
    0xE003: "qsF1",
    0xE004: "qsF2",
    0xE005: "qsOther",
}

SLIDE_FONTS = {
    "before": (BEFORE_GLYPHS, BEFORE_CMAP),
    "after": (AFTER_GLYPHS, AFTER_CMAP),
    "after-extra-prefix-pixel": ({**AFTER_GLYPHS, "qsL": (TWO_COLUMNS_AND_A_PIXEL, 100)}, AFTER_CMAP),
    "after-extra-follower-pixel": (
        {**AFTER_GLYPHS, "qsF2": (TUCKED_FOLLOWER_AND_A_PIXEL, 100)},
        AFTER_CMAP,
    ),
}

FOUNDING_GLYPHS = ["qsL", "qsSee.ex-y0", "qsF1", "qsF2"]
FOUNDING_CODEPOINTS = "E001:E002:E003:E004"


def _build_font(path, glyphs, cmap):
    """A tiny TTF whose every coordinate and advance is a whole number of PIXEL_SIZE columns: one rectilinear outline per named glyph, the codepoints cmapped straight onto the names the run has to shape into, and each glyph's left sidebearing set to its own leftmost point — which is load-bearing rather than tidy, because fontTools' TrueType glyph set translates an outline by `lsb - xMin` on the way out and would otherwise pull every inset glyph back to x=0, erasing the own-frame origin the slide shape reads."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", *glyphs]
    outlines = {}
    metrics = {}
    for name in order:
        contours, advance = glyphs.get(name, ((), 500))
        pen = TTGlyphPen(None)
        for contour in contours:
            pen.moveTo(contour[0])
            for point in contour[1:]:
                pen.lineTo(point)
            pen.closePath()
        outlines[name] = pen.glyph()
        columns = [x for contour in contours for x, _y in contour]
        metrics[name] = (advance, min(columns) if columns else 0)
    builder = FontBuilder(1000)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "SlideTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 0  # pyright: ignore[reportAttributeAccessIssue]
    builder.font["head"].modified = 0
    builder.save(str(path))
    return path


@pytest.fixture(scope="session")
def slide_fonts(tmp_path_factory):
    root = tmp_path_factory.mktemp("slide-fonts")
    return {
        name: _build_font(root / f"{name}.ttf", glyphs, cmap) for name, (glyphs, cmap) in SLIDE_FONTS.items()
    }


@pytest.fixture
def slide_context(slide_fonts):
    def build(after="after"):
        return sv.SlideContext(slide_fonts["before"], slide_fonts[after])

    return build


def slide_unit(uid, glyphs, codepoints, *, configs=("default",), deltas=None):
    return unit(
        uid,
        list(glyphs),
        ["y0"] * (len(glyphs) - 1),
        [f"{sv._family(name)}/full/None/None/" for name in glyphs],
        ["y0"] * (len(glyphs) - 1),
        codepoints=codepoints,
        configs=configs,
        ink_deltas={config: SLIDE_DELTA for config in configs} if deltas is None else deltas,
    )


def founding_window(uid="s-1"):
    return slide_unit(uid, FOUNDING_GLYPHS, FOUNDING_CODEPOINTS)


def test_a_slide_rule_reads_no_unit_without_ink_deltas():
    assert not sv._matches(
        SLIDE_RULE["match"], slide_unit("s-2", FOUNDING_GLYPHS, FOUNDING_CODEPOINTS, deltas={})
    )
    bare = founding_window()
    del bare["ink_deltas"]
    assert not sv._matches(SLIDE_RULE["match"], bare)
    listed = slide_unit("s-3", FOUNDING_GLYPHS, FOUNDING_CODEPOINTS, deltas=[SLIDE_DELTA])
    assert not sv._matches(SLIDE_RULE["match"], listed)


def test_a_window_diverging_two_ways_is_refused_before_any_shaping():
    split = slide_unit(
        "s-4",
        FOUNDING_GLYPHS,
        FOUNDING_CODEPOINTS,
        configs=("default", "ss03"),
        deltas={"default": SLIDE_DELTA, "ss03": UNLISTED_DELTA},
    )
    assert not sv._matches(SLIDE_RULE["match"], split)


def test_delta_keys_that_are_not_the_units_configs_are_refused_before_any_shaping():
    partial = slide_unit(
        "s-5",
        FOUNDING_GLYPHS,
        FOUNDING_CODEPOINTS,
        configs=("default", "ss03"),
        deltas={"default": SLIDE_DELTA},
    )
    assert not sv._matches(SLIDE_RULE["match"], partial)
    elsewhere = slide_unit(
        "s-6", FOUNDING_GLYPHS, FOUNDING_CODEPOINTS, configs=("ss03",), deltas={"default": SLIDE_DELTA}
    )
    assert not sv._matches(SLIDE_RULE["match"], elsewhere)


def test_a_window_with_no_pivot_prefix_glyph_is_refused_before_any_shaping():
    assert not sv._matches(SLIDE_RULE["match"], slide_unit("s-7", ["qsL", "qsF1"], "E001:E003"))
    lookalike = slide_unit("s-8", ["qsL", "qsSee.ex-y0x", "qsF1"], "E001:E002:E003")
    assert not sv._matches(SLIDE_RULE["match"], lookalike)


def test_a_matchable_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(SLIDE_RULE["match"], founding_window())


def test_a_pure_slide_matches(slide_context):
    window = slide_unit("s-9", ["qsL", "qsSee.ex-y0", "qsF1"], "E001:E002:E003")
    assert sv._matches(SLIDE_RULE["match"], window, context=slide_context())


def test_a_union_invisible_respelling_rides_along_with_the_slide(slide_context):
    assert sv._matches(SLIDE_RULE["match"], founding_window(), context=slide_context())


def test_one_extra_pixel_before_the_pivot_defeats_the_match(slide_context):
    context = slide_context("after-extra-prefix-pixel")
    assert not sv._matches(SLIDE_RULE["match"], founding_window(), context=context)


def test_one_extra_pixel_after_the_pivot_defeats_the_match(slide_context):
    context = slide_context("after-extra-follower-pixel")
    assert not sv._matches(SLIDE_RULE["match"], founding_window(), context=context)


def test_the_wrong_column_count_defeats_the_match(slide_context):
    two_columns = json.loads(json.dumps(SLIDE_RULE["match"]))
    two_columns["after"]["slide"] = -2
    assert not sv._matches(two_columns, founding_window(), context=slide_context())


def test_a_window_that_never_settles_into_the_named_pivot_is_refused(slide_context):
    stranded = slide_unit("s-10", ["qsL", "qsSee.ex-y0"], "E001:E005")
    assert not sv._matches(SLIDE_RULE["match"], stranded, context=slide_context())


def test_recorded_glyphs_disagreeing_with_the_shaped_run_defeat_the_match(slide_context):
    misrecorded = slide_unit("s-11", ["qsL", "qsSee.ex-y0", "qsF1", "qsF9"], FOUNDING_CODEPOINTS)
    assert not sv._matches(SLIDE_RULE["match"], misrecorded, context=slide_context())


def test_two_pivots_in_one_window_slide_cumulatively(slide_context):
    context = slide_context()
    twice = slide_unit(
        "s-12",
        ["qsL", "qsSee.ex-y0", "qsF1", "qsSee.ex-y0", "qsF1"],
        "E001:E002:E003:E002:E003",
    )
    assert sv._matches(SLIDE_RULE["match"], twice, context=context)
    two_columns = json.loads(json.dumps(SLIDE_RULE["match"]))
    two_columns["after"]["slide"] = -2
    assert not sv._matches(two_columns, twice, context=context)


def test_except_left_holds_the_guarded_family_on_the_slide_shape(slide_context):
    context = slide_context()
    held = guarding(SLIDE_RULE, ["qsL"])
    assert not sv._matches(held, founding_window(), context=context)
    assert sv._matches(held, founding_window(), guard=False, context=context)


def test_the_slide_shape_and_the_other_shapes_do_not_read_each_others_units(slide_context):
    context = slide_context()
    assert not sv._matches(SLIDE_RULE["match"], canonical(), context=context)
    assert not sv._matches(SLIDE_RULE["match"], tea_i(), context=context)
    assert not sv._matches(RULE["match"], founding_window(), context=context)
    assert not sv._matches(EXT_RULE["match"], founding_window(), context=context)
    assert not sv._matches(INK_RULE["match"], founding_window(), context=context)


def test_a_slide_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [SLIDE_RULE]))
    assert rule["match"]["before"] == {"pivots": ["qsSee.ex-y0"]}
    assert rule["match"]["after"] == {"pivots": ["qsSee.straighter"], "slide": -1}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].update(pivots=[]),
        lambda rule: rule["match"]["before"].update(pivots="qsSee.ex-y0"),
        lambda rule: rule["match"]["before"].update(pivots=[""]),
        lambda rule: rule["match"]["before"].update(pivots=["qsSee/grounded/None/baseline/"]),
        lambda rule: rule["match"]["before"].update(pivot="qsSee.ex-y0"),
        lambda rule: rule["match"]["after"].update(pivots=[]),
        lambda rule: rule["match"]["after"].update(pivots=["qsSee/straighter/None/baseline/"]),
        lambda rule: rule["match"]["after"].update(slide="-1"),
        lambda rule: rule["match"]["after"].update(slide=True),
        lambda rule: rule["match"]["after"].update(slide=None),
        lambda rule: rule["match"].update(except_left="qsL"),
    ],
)
def test_malformed_slide_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(SLIDE_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_slide_that_moves_nothing_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(SLIDE_RULE))
    rule["match"]["after"]["slide"] = 0
    with pytest.raises(SystemExit, match="machine-approved already"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_pivot_lists_spanning_two_families_are_refused_at_load(tmp_path):
    within = json.loads(json.dumps(SLIDE_RULE))
    within["match"]["after"]["pivots"] = ["qsSee.straighter", "qsZoo.straighter"]
    with pytest.raises(SystemExit, match="speaks for one letter"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [within]))
    across = json.loads(json.dumps(SLIDE_RULE))
    across["match"]["before"]["pivots"] = ["qsZoo.ex-y0"]
    with pytest.raises(SystemExit, match="speaks for one letter"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [across]))


def test_a_slide_rule_missing_its_before_block_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(SLIDE_RULE))
    rule["match"].pop("before")
    with pytest.raises(SystemExit, match="needs match.before to be exactly"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_the_slide_and_ink_delta_shapes_at_once_is_refused(tmp_path):
    rule = json.loads(json.dumps(SLIDE_RULE))
    rule["match"]["after"]["ink_deltas"] = [DELTA_A]
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def _surface(tmp_path, units):
    surface = tmp_path / "review"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": STAMP, "classes": [{"id": "all", "shards": ["units/all.json"]}]})
    )
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


def test_main_never_fills_a_unit_outside_the_human_workload(tmp_path, monkeypatch):
    """A machine-approved unit carries batch null, and a picture-identical one still carries the nonempty ink_deltas the ink-delta and slide shapes read — so the candidate filter has to read the workload split itself rather than infer it from an empty delta field."""
    units = [canonical("u-1"), canonical("u-2")]
    units[1]["batch"] = None
    payload = _run_main(tmp_path, monkeypatch, units, [])
    assert {record["unit"] for record in payload["verdicts"]} == {"u-1"}


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


def test_main_fills_only_the_blank_matching_ink_delta_units(tmp_path, monkeypatch):
    units = [
        ink_delta_unit("i-1"),
        ink_delta_unit("i-2"),
        ink_delta_unit("i-3"),
        ink_delta_unit("i-4", deltas={"default": DELTA_A, "ss03": UNLISTED_DELTA}),
        ink_delta_unit("i-5", glyphs=("qsOut.ex-y0", "qsMay.en-y0")),
        ink_delta_unit("i-6"),
    ]
    units[5]["no_verdict"] = True
    verdicts = [
        {"unit": "i-2", "verdict": "reject", "note": "", "at": "2026-07-11T00:00:00Z"},
        {"unit": "i-3", "verdict": "skip", "note": "[parked]", "at": "2026-07-11T00:00:00Z"},
    ]
    rule = json.loads(json.dumps(INK_RULE))
    rule["match"]["except_left"] = ["qsOut"]
    payload = _run_main(tmp_path, monkeypatch, units, verdicts, rules_list=(rule,))
    assert payload["manifest_generated_at"] == STAMP
    assert [record["unit"] for record in payload["verdicts"]] == ["i-1"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "approve"
    assert record["at"] == STAMP
    assert record["note"] == f"[standing: {INK_RULE['id']}] {INK_RULE['note']}"


def test_main_fills_all_three_shapes_from_one_rules_file(tmp_path, monkeypatch):
    units = [canonical("u-1"), tea_i("u-2"), ink_delta_unit("i-1")]
    payload = _run_main(tmp_path, monkeypatch, units, [], rules_list=(RULE, EXT_RULE, INK_RULE))
    by_unit = {record["unit"]: record for record in payload["verdicts"]}
    assert set(by_unit) == {"u-1", "u-2", "i-1"}
    assert by_unit["i-1"]["note"].startswith(f"[standing: {INK_RULE['id']}]")


def test_main_refuses_a_surface_that_predates_the_ink_delta_field(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="predates the ink-delta and slide shapes"):
        _run_main(tmp_path, monkeypatch, [canonical("u-1")], [], rules_list=(RULE, INK_RULE))


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


def test_main_refuses_a_surface_that_carries_no_font_pair(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="fonts/before.otf"):
        _run_main(tmp_path, monkeypatch, [founding_window()], [], rules_list=(SLIDE_RULE,))
