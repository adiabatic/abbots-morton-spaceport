"""Tests for the standing-approval fill: all six delta shapes — the two structural pattern matches, being the ligature shape (pivot glyph, seams into and out of it, follower family, post-ligature seam, flank-seam identity) and the extension-dropped shape (pivot glyph giving up a named stretch of exit — an `ex-ext-N` it carried, in whole or down to a shorter one its named after cell keeps, or an `ex-con-N` its named after cell carries when the before glyph never had an exit extension — the seam it exits into holding its height, the full after-cell identity of pivot and follower, every other seam standing still, nothing ligating anywhere, and the unit's own judgment fields agreeing that this seam is the question), the ink-exact ink-delta shape (the unit's persisted per-config digests being a nonempty subset of the ones the rule blesses, so an ink-identical window matches nothing and one unlisted delta under one config fails the whole unit closed, and a surface predating the field refuses the run outright), and the rendered-pixel slide shape, whose preconditions are read off the index record before anything is shaped (a nonempty `ink_deltas` holding one distinct digest whose keys are exactly the unit's config set, and a pivot-prefix name among the recorded before glyphs) and whose geometry is then re-derived in a purpose-built font pair, where the pivot keeps its exact ink with its own-frame origin displaced by the declared column count and every span's union of ink slides cumulatively — so a union-invisible name-grain re-spelling to the pivot's right rides along, while one stray pixel anywhere in the window, or a font pair that never settles into the named pivot, fails the match closed — the rendered-pixel ink-gain shape, whose preconditions match the slide shape's and whose geometry is the named pivot keeping its placement, height, and own-frame origin while gaining exactly the named cells, every other pixel standing still — the rendered-pixel join-dropped shape, whose preconditions are a named pivot–follower seam dropping from a yK height to a break plus the slide shape's digest-agreement, and whose geometry is both letters keeping their exact picture and own-frame origin with the follower sitting the declared gap further and everything after it sitting the same extra gap away — the composed reading that runs before all six and credits two or more rules for one window — its name-grain pre-gate refusing to shape a window fewer than two rules have a candidate in, its walk carrying a running column displacement across the window so that each span between events must render identically once displaced, its refusal of a redrawn follower, of a pivot contracting off the seam row, of a tail wider than the pivot gave up, and of two rules claiming one position, its judging of a failed candidate as ordinary span ink, its per-shape guard scopes, and its own reporting line, which `main` keeps clear of the per-rule lines — the except_left guard, which reads a ligature's trailing left component and refuses the whole unit rather than the one position, blankness against the verdicts file (parked skip verdicts are not blank), the non-winning manifest stamp on every emitted record, and rules-file validation, which admits exactly one shape per rule and checks that shape's own coherence."""

import json
import pathlib
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

SHORTENED_RULE = {
    "id": "fee-tea-ss03-exit-extension-shortened",
    "verdict": "approve",
    "note": "·Fee reaches ·Tea with one pixel of extension where the old font drew three",
    "match": {
        "before": {
            "pivot": "qsFee",
            "exit_extension": "ex-ext-3",
            "seam_out": "y5",
            "follower": "qsTea",
        },
        "after": {
            "pivot_cells": ["qsFee/loop/None/x-height/ex-ext-1"],
            "follower_cells": ["qsTea/full/x-height/None/", "qsTea/full/x-height/baseline/"],
        },
        "except_left": [],
    },
}

CONTRACTED_RULE = {
    "id": "et-may-exit-contracted",
    "verdict": "approve",
    "note": "·May sits a pixel closer to ·Et",
    "match": {
        "before": {
            "pivot": "qsEt",
            "exit_extension": "ex-con-1",
            "seam_out": "y0",
            "follower": "qsMay",
        },
        "after": {
            "pivot_cells": [
                "qsEt/hapax/None/baseline/ex-con-1",
                "qsEt/hapax/x-height/baseline/ex-con-1",
                "qsEt/hapax/x-height/baseline/en-ext-1+ex-con-1",
            ],
            "follower_cells": [
                "qsMay/loop/baseline/None/",
                "qsMay/loop/baseline/x-height/ex-ext-1",
                "qsMay/loop/baseline/x-height/ex-ext-2",
            ],
        },
        "except_left": [],
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

GAIN_RULE = {
    "id": "roe-baseline-bar-kept-after-it",
    "verdict": "approve",
    "note": "the bottom of ·Roe sits a pixel closer to ·It",
    "match": {
        "before": {"pivots": ["qsRoe.en-ext-1-at-5"]},
        "after": {"pivots": ["qsRoe.hapax"], "gained": [[1, 0]]},
        "except_left": [],
    },
}

JOIN_RULE = {
    "id": "at-it-xheight-join-dropped",
    "verdict": "approve",
    "note": "·It sits a column further from ·At — they no longer join at the x-height",
    "match": {
        "before": {"pivot": "qsAt", "seam_out": "y5", "follower": "qsIt"},
        "after": {"gap": 1},
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


def fee_tea(uid="u-12", follower_cell="qsTea/full/x-height/baseline/"):
    return unit(
        uid,
        ["qsFee.ex-y5.before-may.ex-ext-3", "qsTea.en-y5.ex-y0.after-fee"],
        ["y5"],
        ["qsFee/loop/None/x-height/ex-ext-1", follower_cell],
        ["y5"],
        pair={"left": 0, "right": 1},
    )


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
    swapped = fee_tea()
    assert not sv._matches(swap_rule, swapped)
    dropped = json.loads(json.dumps(swapped))
    dropped["after"]["cells"][0] = "qsFee/loop/None/x-height/"
    assert sv._matches(swap_rule, dropped)


def test_a_rule_naming_the_shorter_extension_the_pivot_keeps_reads_exactly_that_shortening():
    assert sv._matches(SHORTENED_RULE["match"], fee_tea())
    dropped = fee_tea()
    dropped["after"]["cells"][0] = "qsFee/loop/None/x-height/"
    assert not sv._matches(SHORTENED_RULE["match"], dropped)
    less_shortened = fee_tea()
    less_shortened["after"]["cells"][0] = "qsFee/loop/None/x-height/ex-ext-2"
    assert not sv._matches(SHORTENED_RULE["match"], less_shortened)
    kept = fee_tea()
    kept["after"]["cells"][0] = "qsFee/loop/None/x-height/ex-ext-3"
    assert not sv._matches(SHORTENED_RULE["match"], kept)


def test_a_contraction_rule_does_not_read_a_dropped_extension():
    assert not sv._matches(CONTRACTED_RULE["match"], tea_i())
    assert not sv._matches(EXT_RULE["match"], et_may())
    assert sv._matches(CONTRACTED_RULE["match"], et_may())


def test_a_different_follower_family_does_not_match():
    wrong = tea_i()
    wrong["before"]["glyphs"][1] = "qsIt"
    wrong["after"]["cells"][1] = "qsIt/smaller-loop/baseline/None/"
    assert not sv._matches(EXT_RULE["match"], wrong)


JAI_RULE = {
    "id": "jai-exit-extension-dropped",
    "verdict": "approve",
    "note": "·Vie, ·See, ·No and ·Low sit a pixel closer to ·J’ai",
    "match": {
        "before": {
            "pivot": "qsJai",
            "exit_extension": "ex-ext-1",
            "seam_out": "y0",
            "follower": ["qsVie", "qsSee", "qsNo"],
        },
        "after": {
            "pivot_cells": ["qsJai/hapax/None/baseline/"],
            "follower_cells": [
                "qsVie/normal/baseline/None/",
                "qsSee/normal/baseline/None/",
                "qsNo/flipped/baseline/None/",
            ],
        },
        "except_left": [],
    },
}


def jai_before(uid="u-16", follower="qsVie", follower_cell="qsVie/normal/baseline/None/"):
    return unit(
        uid,
        ["qsOoze", "qsJai.en-y5.ex-y0.ex-ext-1", follower],
        ["break", "y0"],
        ["qsOoze/hapax/None/None/", "qsJai/hapax/None/baseline/", follower_cell],
        ["break", "y0"],
        pair={"left": 1, "right": 2},
    )


def test_a_follower_list_matches_any_family_it_names():
    assert sv._matches(JAI_RULE["match"], jai_before())
    assert sv._matches(
        JAI_RULE["match"], jai_before(follower="qsSee", follower_cell="qsSee/normal/baseline/None/")
    )
    assert sv._matches(
        JAI_RULE["match"],
        jai_before(follower="qsNo.alt.en-y0.ex-y0", follower_cell="qsNo/flipped/baseline/None/"),
    )


def test_a_follower_outside_the_list_does_not_match():
    assert not sv._matches(
        JAI_RULE["match"], jai_before(follower="qsLow", follower_cell="qsLow/hapax/baseline/None/")
    )


def test_a_follower_cell_of_another_listed_family_does_not_stand_in():
    crossed = jai_before(follower="qsSee", follower_cell="qsVie/normal/baseline/None/")
    assert not sv._matches(JAI_RULE["match"], crossed)


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
    assert by_id["jai-exit-extension-dropped"]["match"]["before"]["follower"] == [
        "qsVie",
        "qsVie_qsUtter",
        "qsSee",
        "qsNo",
        "qsLow",
    ]
    shortened = by_id["fee-tea-ss03-exit-extension-shortened"]["match"]
    assert shortened["before"]["exit_extension"] == "ex-ext-3"
    assert shortened["after"]["pivot_cells"] == ["qsFee/loop/None/x-height/ex-ext-1"]
    contracted = by_id["et-may-exit-contracted"]["match"]
    assert contracted["before"]["exit_extension"] == "ex-con-1"
    assert contracted["before"]["follower"] == "qsMay"
    assert contracted["after"]["pivot_cells"] == [
        "qsEt/hapax/None/baseline/ex-con-1",
        "qsEt/hapax/x-height/baseline/ex-con-1",
        "qsEt/hapax/x-height/baseline/en-ext-1+ex-con-1",
    ]
    gained = by_id["roe-baseline-bar-kept-after-it"]["match"]
    assert gained["before"]["pivots"] == ["qsRoe.en-ext-1-at-5"]
    assert gained["after"]["pivots"] == ["qsRoe.hapax"]
    assert gained["after"]["gained"] == [[1, 0]]
    dropped = by_id["at-it-xheight-join-dropped"]["match"]
    assert dropped["before"] == {"pivot": "qsAt", "seam_out": "y5", "follower": "qsIt"}
    assert dropped["after"] == {"gap": 1}


def et_may(
    uid="u-18",
    pivot="qsEt",
    pivot_cell="qsEt/hapax/None/baseline/ex-con-1",
    follower="qsMay.en-y0.ex-y5",
    follower_cell="qsMay/loop/baseline/None/",
):
    return unit(
        uid,
        ["qsDay", pivot, follower, "qsDay"],
        ["break", "y0", "y5"],
        ["qsDay/full/None/None/", pivot_cell, follower_cell, "qsDay/full/x-height/None/"],
        ["break", "y0", "y5"],
        pair={"left": 1, "right": 2},
    )


def test_the_checked_in_et_may_rule_reads_the_contraction_and_nothing_wider():
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["et-may-exit-contracted"]["match"]
    assert sv._matches(match, et_may())
    assert sv._matches(match, et_may(follower_cell="qsMay/loop/baseline/x-height/ex-ext-1"))
    assert sv._matches(match, et_may(follower_cell="qsMay/loop/baseline/x-height/ex-ext-2"))
    assert sv._matches(
        match,
        et_may(
            pivot="qsEt.en-ext-1",
            pivot_cell="qsEt/hapax/x-height/baseline/en-ext-1+ex-con-1",
        ),
    )
    regrouped = et_may()
    regrouped["secondary_seams"] = 1
    assert not sv._matches(match, regrouped)
    elsewhere = et_may()
    elsewhere["pair"] = {"left": 0, "right": 1}
    assert not sv._matches(match, elsewhere)
    extended = et_may(pivot="qsEt.ex-ext-1")
    assert not sv._matches(match, extended)
    uncontracted = et_may(pivot_cell="qsEt/hapax/None/baseline/")
    assert not sv._matches(match, uncontracted)
    other_follower = et_may(follower="qsTea.en-y0", follower_cell="qsTea/full/baseline/None/")
    assert not sv._matches(match, other_follower)


def test_the_checked_in_fee_rule_reads_the_ss03_shortening_and_nothing_wider():
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["fee-tea-ss03-exit-extension-shortened"][
        "match"
    ]
    assert sv._matches(match, fee_tea())
    assert sv._matches(match, fee_tea(follower_cell="qsTea/full/x-height/None/"))
    regrouped = unit(
        "u-17",
        ["qsMay", "qsFee.ex-y5.before-may.ex-ext-3", "qsTea.half.ex-y5", "qsJai.en-y5.ex-y0.en-con-1"],
        ["break", "break", "y5"],
        [
            "qsMay/loop/None/None/",
            "qsFee/loop/None/x-height/ex-ext-1",
            "qsTea/full/x-height/None/",
            "qsJai/hapax/None/None/",
        ],
        ["break", "y5", "break"],
        pair={"left": 1, "right": 2},
        secondary_seams=1,
    )
    assert not sv._matches(match, regrouped)
    half = fee_tea(follower_cell="qsTea/half/None/x-height/")
    assert not sv._matches(match, half)
    before_may = fee_tea()
    before_may["before"]["glyphs"][1] = "qsMay.en-y5.ex-y0"
    before_may["after"]["cells"] = ["qsFee/loop/None/x-height/ex-ext-3", "qsMay/loop/x-height/None/"]
    assert not sv._matches(match, before_may)


def test_the_checked_in_jai_rule_reads_the_narrowed_seam_and_nothing_wider():
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["jai-exit-extension-dropped"]["match"]
    assert sv._matches(match, jai_before())
    assert sv._matches(match, jai_before(follower="qsLow", follower_cell="qsLow/hapax/baseline/None/"))
    kept = jai_before(follower="qsTea.en-y0", follower_cell="qsTea/full/baseline/None/")
    assert not sv._matches(match, kept)
    yielded = jai_before()
    yielded["after"]["cells"][1] = "qsJai/hapax/None/None/"
    yielded["after"]["seams"] = ["break", "break"]
    assert not sv._matches(match, yielded)


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


@pytest.mark.parametrize("kept", ["ex-ext-1", "ex-ext-2"])
def test_a_pivot_cell_keeping_an_extension_as_long_as_the_named_one_is_refused_at_load(tmp_path, kept):
    rule = json.loads(json.dumps(EXT_RULE))
    rule["match"]["after"]["pivot_cells"] = [f"qsTea/full/None/baseline/{kept}"]
    with pytest.raises(SystemExit, match="has given up"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_pivot_cell_keeping_a_shorter_extension_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [SHORTENED_RULE]))
    assert rule["match"]["after"]["pivot_cells"] == ["qsFee/loop/None/x-height/ex-ext-1"]
    unshortened = json.loads(json.dumps(SHORTENED_RULE))
    unshortened["match"]["after"]["pivot_cells"] = ["qsFee/loop/None/x-height/ex-ext-3"]
    with pytest.raises(SystemExit, match="keeps an exit extension of 3 columns against the 3"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [unshortened]))


def test_a_contraction_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [CONTRACTED_RULE]))
    assert rule["match"]["before"]["exit_extension"] == "ex-con-1"
    missing = json.loads(json.dumps(CONTRACTED_RULE))
    missing["match"]["after"]["pivot_cells"] = ["qsEt/hapax/None/baseline/"]
    with pytest.raises(SystemExit, match="carries an exit contraction of 0 columns against the 1"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [missing]))
    longer = json.loads(json.dumps(CONTRACTED_RULE))
    longer["match"]["after"]["pivot_cells"] = ["qsEt/hapax/None/baseline/ex-con-2"]
    with pytest.raises(SystemExit, match="carries an exit contraction of 2 columns against the 1"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [longer]))
    mixed = json.loads(json.dumps(CONTRACTED_RULE))
    mixed["match"]["after"]["pivot_cells"] = ["qsEt/hapax/None/baseline/ex-ext-1+ex-con-1"]
    with pytest.raises(SystemExit, match="still carries an exit extension"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [mixed]))


def test_a_cell_belonging_to_another_letter_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(EXT_RULE))
    rule["match"]["after"]["follower_cells"] = ["qsIt/smaller-loop/baseline/None/"]
    with pytest.raises(SystemExit, match="is not a cell of qsI"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_follower_list_rule_loads_and_its_cells_are_held_to_the_list(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [JAI_RULE]))
    assert rule["match"]["before"]["follower"] == ["qsVie", "qsSee", "qsNo"]
    strayed = json.loads(json.dumps(JAI_RULE))
    strayed["match"]["after"]["follower_cells"].append("qsLow/hapax/baseline/None/")
    with pytest.raises(SystemExit, match="is not a cell of qsVie or qsSee or qsNo"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [strayed]))


@pytest.mark.parametrize(
    "follower",
    [[], [""], ["qsVie", "qsVie"], ["qsVie", None], "", None, {"family": "qsVie"}],
)
def test_malformed_follower_lists_are_refused_at_load(tmp_path, follower):
    rule = json.loads(json.dumps(JAI_RULE))
    rule["match"]["before"]["follower"] = follower
    rule["match"]["after"]["follower_cells"] = ["qsVie/normal/baseline/None/"]
    with pytest.raises(SystemExit):
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


def test_all_six_shapes_load_from_one_rules_file(tmp_path):
    rules = sv.load_rules(
        _write_rules(tmp_path / "rules.yaml", [RULE, EXT_RULE, INK_RULE, SLIDE_RULE, GAIN_RULE, JOIN_RULE])
    )
    assert [rule["id"] for rule in rules] == [
        RULE["id"],
        EXT_RULE["id"],
        INK_RULE["id"],
        SLIDE_RULE["id"],
        GAIN_RULE["id"],
        JOIN_RULE["id"],
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
SHORTENED_ROE = (_rect(0, 0, 50, 50), _rect(0, 50, 100, 150))
KEPT_ROE = TWO_COLUMNS
WRONG_CELL_ROE = (_rect(0, 0, 50, 50), _rect(0, 50, 100, 150), _rect(0, 150, 50, 200))
EXTRA_CELL_ROE = (_rect(0, 0, 100, 150), _rect(0, 150, 50, 200))
TUCKED_FOLLOWER_AND_A_PIXEL = (((50, 0), (150, 0), (150, 50), (100, 50), (100, 150), (50, 150)),)
EXTENDED_PIVOT = (((0, 0), (100, 0), (100, 50), (50, 50), (50, 150), (0, 150)),)
WIDE_TAIL_PIVOT = (((0, 0), (150, 0), (150, 50), (50, 50), (50, 150), (0, 150)),)
LONG_TAIL_PIVOT = (((0, 0), (200, 0), (200, 50), (50, 50), (50, 150), (0, 150)),)
CROWNED_PIVOT = (((0, 0), (100, 0), (100, 50), (50, 50), (50, 100), (100, 100), (100, 150), (0, 150)),)
TRIMMED_PIVOT = (_rect(0, 0, 50, 150),)
CONTRACTED_PIVOT = (_rect(0, 0, 50, 100),)

BEFORE_GLYPHS = {
    "qsL": (TWO_COLUMNS, 100),
    "qsSee.ex-y0": (GROUNDED_SEE, 250),
    "qsSee.ex-y0.spare": (GROUNDED_SEE, 250),
    "qsSee.ex-y0.blank": ((), 50),
    "qsF1": (TWO_COLUMNS, 50),
    "qsF2": (TWO_COLUMNS, 100),
    "qsF3": (TWO_COLUMNS, 100),
    "qsM": (TWO_COLUMNS, 100),
    "qsJ.ex-y0.ex-ext-1": (EXTENDED_PIVOT, 100),
    "qsJ.ex-y0.ex-ext-1.wide": (WIDE_TAIL_PIVOT, 150),
    "qsJ.ex-y0.ex-ext-1.crown": (CROWNED_PIVOT, 100),
    "qsJ.ex-y0.ex-ext-3.long": (LONG_TAIL_PIVOT, 200),
    "qsEt": (EXTENDED_PIVOT, 100),
    "qsRoe.en-ext-1-at-5": (SHORTENED_ROE, 100),
    "qsAt": (TWO_COLUMNS, 100),
    "qsIt": (TWO_COLUMNS, 100),
    "space": ((), 50),
}
AFTER_GLYPHS = {
    "qsL": (TWO_COLUMNS, 100),
    "qsSee.straighter": (STRAIGHTER_SEE, 200),
    "qsSee.straighter.blank": ((), 50),
    "qsSee.spare": (GROUNDED_SEE, 250),
    "qsSee.wandered": (TWO_COLUMNS, 250),
    "qsF1": (TWO_COLUMNS, 50),
    "qsF2": (TUCKED_FOLLOWER, 100),
    "qsF3": (TWO_COLUMNS, 100),
    "qsM": (TWO_COLUMNS, 100),
    "qsJ.hapax.ex-y0": (TRIMMED_PIVOT, 50),
    "qsJ.hapax.ex-y0.ex-ext-1": (EXTENDED_PIVOT, 100),
    "qsEt.hapax": (TRIMMED_PIVOT, 50),
    "qsOther": (TWO_COLUMNS, 100),
    "qsRoe.hapax.en-y5.en-ext-1": (KEPT_ROE, 100),
    "qsAt": (TWO_COLUMNS, 150),
    "qsIt": (TWO_COLUMNS, 100),
    "space": ((), 50),
}
BEFORE_CMAP = {
    0x0020: "space",
    0xE001: "qsL",
    0xE002: "qsSee.ex-y0",
    0xE003: "qsF1",
    0xE004: "qsF2",
    0xE005: "qsSee.ex-y0",
    0xE006: "qsJ.ex-y0.ex-ext-1",
    0xE007: "qsF3",
    0xE008: "qsM",
    0xE009: "qsSee.ex-y0.spare",
    0xE00A: "qsJ.ex-y0.ex-ext-1.wide",
    0xE00B: "qsSee.ex-y0.blank",
    0xE00C: "qsJ.ex-y0.ex-ext-1.crown",
    0xE00D: "qsSee.ex-y0.spare",
    0xE00E: "qsJ.ex-y0.ex-ext-3.long",
    0xE00F: "qsJ.ex-y0.ex-ext-3.long",
    0xE010: "qsEt",
    0xE020: "qsRoe.en-ext-1-at-5",
    0xE021: "qsAt",
    0xE022: "qsIt",
}
AFTER_CMAP = {
    0x0020: "space",
    0xE001: "qsL",
    0xE002: "qsSee.straighter",
    0xE003: "qsF1",
    0xE004: "qsF2",
    0xE005: "qsOther",
    0xE006: "qsJ.hapax.ex-y0",
    0xE007: "qsF3",
    0xE008: "qsM",
    0xE009: "qsSee.spare",
    0xE00A: "qsJ.hapax.ex-y0",
    0xE00B: "qsSee.straighter.blank",
    0xE00C: "qsJ.hapax.ex-y0",
    0xE00D: "qsSee.wandered",
    0xE00E: "qsJ.hapax.ex-y0.ex-ext-1",
    0xE00F: "qsJ.hapax.ex-y0",
    0xE010: "qsEt.hapax",
    0xE020: "qsRoe.hapax.en-y5.en-ext-1",
    0xE021: "qsAt",
    0xE022: "qsIt",
}

SLIDE_FONTS = {
    "before": (BEFORE_GLYPHS, BEFORE_CMAP),
    "after": (AFTER_GLYPHS, AFTER_CMAP),
    "after-extra-prefix-pixel": ({**AFTER_GLYPHS, "qsL": (TWO_COLUMNS_AND_A_PIXEL, 100)}, AFTER_CMAP),
    "after-extra-follower-pixel": (
        {**AFTER_GLYPHS, "qsF2": (TUCKED_FOLLOWER_AND_A_PIXEL, 100)},
        AFTER_CMAP,
    ),
    "after-extra-middle-pixel": ({**AFTER_GLYPHS, "qsM": (TWO_COLUMNS_AND_A_PIXEL, 100)}, AFTER_CMAP),
    "after-extra-tail-pixel": ({**AFTER_GLYPHS, "qsF3": (TWO_COLUMNS_AND_A_PIXEL, 100)}, AFTER_CMAP),
    "after-redrawn-follower": ({**AFTER_GLYPHS, "qsF3": (TUCKED_FOLLOWER, 100)}, AFTER_CMAP),
    "after-contracted-pivot": ({**AFTER_GLYPHS, "qsJ.hapax.ex-y0": (CONTRACTED_PIVOT, 50)}, AFTER_CMAP),
    "after-extra-post-follower-pixel": ({**AFTER_GLYPHS, "qsF1": (TWO_COLUMNS_AND_A_PIXEL, 50)}, AFTER_CMAP),
    "after-unshortened-pivot": ({**AFTER_GLYPHS, "qsJ.hapax.ex-y0": (EXTENDED_PIVOT, 100)}, AFTER_CMAP),
    "after-roe-wrong-cell": (
        {**AFTER_GLYPHS, "qsRoe.hapax.en-y5.en-ext-1": (WRONG_CELL_ROE, 100)},
        AFTER_CMAP,
    ),
    "after-roe-extra-cell": (
        {**AFTER_GLYPHS, "qsRoe.hapax.en-y5.en-ext-1": (EXTRA_CELL_ROE, 100)},
        AFTER_CMAP,
    ),
    "after-roe-unmoved": (
        {**AFTER_GLYPHS, "qsRoe.hapax.en-y5.en-ext-1": (SHORTENED_ROE, 100)},
        AFTER_CMAP,
    ),
    "after-join-unmoved": ({**AFTER_GLYPHS, "qsAt": (TWO_COLUMNS, 100)}, AFTER_CMAP),
    "after-join-redrawn-pivot": ({**AFTER_GLYPHS, "qsAt": (TUCKED_FOLLOWER, 150)}, AFTER_CMAP),
    "after-join-redrawn-follower": ({**AFTER_GLYPHS, "qsIt": (TUCKED_FOLLOWER, 100)}, AFTER_CMAP),
    "after-join-regrouped": ({**AFTER_GLYPHS, "qsIt": (TWO_COLUMNS, 50)}, AFTER_CMAP),
    "after-join-extra-prefix-pixel": ({**AFTER_GLYPHS, "qsL": (TWO_COLUMNS_AND_A_PIXEL, 100)}, AFTER_CMAP),
    "after-join-extra-tail-pixel": ({**AFTER_GLYPHS, "qsF1": (TWO_COLUMNS_AND_A_PIXEL, 50)}, AFTER_CMAP),
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


def slide_unit(uid, glyphs, codepoints, *, configs=("default",), deltas=None, pair=None):
    return unit(
        uid,
        list(glyphs),
        ["y0"] * (len(glyphs) - 1),
        [f"{sv._family(name)}/full/None/None/" for name in glyphs],
        ["y0"] * (len(glyphs) - 1),
        codepoints=codepoints,
        configs=configs,
        ink_deltas={config: SLIDE_DELTA for config in configs} if deltas is None else deltas,
        pair=pair,
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


GAIN_GLYPHS = ["qsL", "qsRoe.en-ext-1-at-5", "qsF1"]
GAIN_CODEPOINTS = "E001:E020:E003"


def gain_window(uid="g-1"):
    return slide_unit(uid, GAIN_GLYPHS, GAIN_CODEPOINTS)


def test_a_pure_gain_matches(slide_context):
    assert sv._matches(GAIN_RULE["match"], gain_window(), context=slide_context())


def test_the_checked_in_roe_rule_reads_the_named_cells(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["roe-baseline-bar-kept-after-it"]["match"]
    assert sv._matches(match, gain_window(), context=slide_context())
    assert not sv._matches(match, founding_window(), context=slide_context())


def test_a_wrong_gained_cell_defeats_the_match(slide_context):
    assert not sv._matches(GAIN_RULE["match"], gain_window(), context=slide_context("after-roe-wrong-cell"))


def test_an_unnamed_extra_cell_defeats_the_match(slide_context):
    assert not sv._matches(GAIN_RULE["match"], gain_window(), context=slide_context("after-roe-extra-cell"))


def test_an_unmoved_pivot_defeats_the_gain_match(slide_context):
    assert not sv._matches(GAIN_RULE["match"], gain_window(), context=slide_context("after-roe-unmoved"))


def test_one_extra_pixel_beside_the_gain_defeats_the_match(slide_context):
    assert not sv._matches(
        GAIN_RULE["match"], gain_window(), context=slide_context("after-extra-prefix-pixel")
    )


def test_a_gain_rule_reads_no_unit_without_ink_deltas():
    assert not sv._matches(GAIN_RULE["match"], slide_unit("g-2", GAIN_GLYPHS, GAIN_CODEPOINTS, deltas={}))
    bare = gain_window()
    del bare["ink_deltas"]
    assert not sv._matches(GAIN_RULE["match"], bare)


def test_a_window_with_no_gain_pivot_prefix_glyph_is_refused_before_any_shaping():
    assert not sv._matches(GAIN_RULE["match"], slide_unit("g-3", ["qsL", "qsF1"], "E001:E003"))


def test_a_matchable_gain_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(GAIN_RULE["match"], gain_window())


def test_except_left_holds_the_guarded_family_on_the_ink_gain_shape(slide_context):
    context = slide_context()
    assert not sv._matches(guarding(GAIN_RULE, ["qsL"]), gain_window(), context=context)
    assert sv._matches(guarding(GAIN_RULE, ["qsL"]), gain_window(), guard=False, context=context)


def test_the_ink_gain_shape_and_the_other_shapes_do_not_read_each_others_units(slide_context):
    context = slide_context()
    assert not sv._matches(GAIN_RULE["match"], founding_window(), context=context)
    assert not sv._matches(GAIN_RULE["match"], canonical(), context=context)
    assert not sv._matches(SLIDE_RULE["match"], gain_window(), context=context)
    assert not sv._matches(EXT_RULE["match"], gain_window(), context=context)
    assert not sv._matches(INK_RULE["match"], gain_window())


def test_a_gain_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [GAIN_RULE]))
    assert rule["match"]["before"] == {"pivots": ["qsRoe.en-ext-1-at-5"]}
    assert rule["match"]["after"] == {"pivots": ["qsRoe.hapax"], "gained": [[1, 0]]}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].update(pivots=[]),
        lambda rule: rule["match"]["before"].update(pivots="qsRoe.en-ext-1-at-5"),
        lambda rule: rule["match"]["after"].update(gained=[]),
        lambda rule: rule["match"]["after"].update(gained=[[1, 0], [1, 0]]),
        lambda rule: rule["match"]["after"].update(gained=[[1]]),
        lambda rule: rule["match"]["after"].update(gained=[[1, True]]),
        lambda rule: rule["match"]["after"].update(gained="1,0"),
        lambda rule: rule["match"]["after"].update(pivots=["qsRoe/hapax/x-height/None/en-ext-1"]),
        lambda rule: rule["match"].update(except_left="qsL"),
    ],
)
def test_malformed_gain_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(GAIN_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_gain_pivot_lists_spanning_two_families_are_refused_at_load(tmp_path):
    within = json.loads(json.dumps(GAIN_RULE))
    within["match"]["after"]["pivots"] = ["qsRoe.hapax", "qsSee.hapax"]
    with pytest.raises(SystemExit, match="speaks for one letter"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [within]))
    across = json.loads(json.dumps(GAIN_RULE))
    across["match"]["before"]["pivots"] = ["qsSee.en-ext-1-at-5"]
    with pytest.raises(SystemExit, match="speaks for one letter"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [across]))


def test_a_gain_rule_missing_its_before_block_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(GAIN_RULE))
    rule["match"].pop("before")
    with pytest.raises(SystemExit, match="needs match.before to be exactly"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_the_gain_and_slide_shapes_at_once_is_refused(tmp_path):
    rule = json.loads(json.dumps(GAIN_RULE))
    rule["match"]["after"]["slide"] = -1
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


JOIN_GLYPHS = ["qsL", "qsAt", "qsIt", "qsF1"]
JOIN_CODEPOINTS = "E001:E021:E022:E003"


def join_window(uid="j-1"):
    return unit(
        uid,
        list(JOIN_GLYPHS),
        ["y0", "y5", "y0"],
        ["qsL/full/None/None/", "qsAt/full/None/None/", "qsIt/full/None/None/", "qsF1/full/None/None/"],
        ["y0", "break", "y0"],
        codepoints=JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def test_a_pure_join_drop_matches(slide_context):
    assert sv._matches(JOIN_RULE["match"], join_window(), context=slide_context())


def test_the_checked_in_at_it_rule_reads_the_gap(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["at-it-xheight-join-dropped"]["match"]
    assert sv._matches(match, join_window(), context=slide_context())
    assert not sv._matches(match, founding_window(), context=slide_context())


def test_an_unmoved_follower_defeats_the_join_match(slide_context):
    assert not sv._matches(JOIN_RULE["match"], join_window(), context=slide_context("after-join-unmoved"))


def test_a_redrawn_pivot_defeats_the_join_match(slide_context):
    assert not sv._matches(
        JOIN_RULE["match"], join_window(), context=slide_context("after-join-redrawn-pivot")
    )


def test_a_redrawn_follower_defeats_the_join_match(slide_context):
    assert not sv._matches(
        JOIN_RULE["match"], join_window(), context=slide_context("after-join-redrawn-follower")
    )


def test_a_regrouped_follower_defeats_the_join_match(slide_context):
    assert not sv._matches(JOIN_RULE["match"], join_window(), context=slide_context("after-join-regrouped"))


def test_one_extra_pixel_before_the_join_defeats_the_match(slide_context):
    assert not sv._matches(
        JOIN_RULE["match"], join_window(), context=slide_context("after-join-extra-prefix-pixel")
    )


def test_one_extra_pixel_after_the_join_defeats_the_match(slide_context):
    assert not sv._matches(
        JOIN_RULE["match"], join_window(), context=slide_context("after-join-extra-tail-pixel")
    )


def test_a_seam_that_stays_joined_defeats_the_match(slide_context):
    stayed = join_window()
    stayed["after"]["seams"] = ["y0", "y5", "y0"]
    assert not sv._matches(JOIN_RULE["match"], stayed, context=slide_context())


def test_a_wrong_follower_family_defeats_the_join_match(slide_context):
    other = join_window()
    other["before"]["glyphs"][2] = "qsF1"
    other["after"]["cells"][2] = "qsF1/full/None/None/"
    other["codepoints"] = "E001:E021:E003:E003"
    assert not sv._matches(JOIN_RULE["match"], other, context=slide_context())


def test_a_join_rule_reads_no_unit_without_ink_deltas():
    bare = join_window()
    del bare["ink_deltas"]
    assert not sv._matches(JOIN_RULE["match"], bare)
    empty = join_window()
    empty["ink_deltas"] = {}
    assert not sv._matches(JOIN_RULE["match"], empty)


def test_a_window_with_no_join_pivot_is_refused_before_any_shaping():
    assert not sv._matches(JOIN_RULE["match"], slide_unit("j-3", ["qsL", "qsF1"], "E001:E003"))


def test_a_matchable_join_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(JOIN_RULE["match"], join_window())


def test_except_left_holds_the_guarded_family_on_the_join_dropped_shape(slide_context):
    context = slide_context()
    assert not sv._matches(guarding(JOIN_RULE, ["qsL"]), join_window(), context=context)
    assert sv._matches(guarding(JOIN_RULE, ["qsL"]), join_window(), guard=False, context=context)


def test_the_join_dropped_shape_and_the_other_shapes_do_not_read_each_others_units(slide_context):
    context = slide_context()
    assert not sv._matches(JOIN_RULE["match"], founding_window(), context=context)
    assert not sv._matches(JOIN_RULE["match"], canonical(), context=context)
    assert not sv._matches(JOIN_RULE["match"], gain_window(), context=context)
    assert not sv._matches(SLIDE_RULE["match"], join_window(), context=context)
    assert not sv._matches(EXT_RULE["match"], join_window(), context=context)
    assert not sv._matches(INK_RULE["match"], join_window())
    assert not sv._matches(GAIN_RULE["match"], join_window(), context=context)


def test_a_join_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [JOIN_RULE]))
    assert rule["match"]["before"] == {"pivot": "qsAt", "seam_out": "y5", "follower": "qsIt"}
    assert rule["match"]["after"] == {"gap": 1}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].update(pivot=""),
        lambda rule: rule["match"]["before"].update(seam_out=""),
        lambda rule: rule["match"]["before"].update(follower=""),
        lambda rule: rule["match"]["before"].update(follower=[]),
        lambda rule: rule["match"]["after"].update(gap="1"),
        lambda rule: rule["match"]["after"].update(gap=True),
        lambda rule: rule["match"]["after"].update(gap=None),
        lambda rule: rule["match"].update(except_left="qsL"),
    ],
)
def test_malformed_join_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(JOIN_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_gap_that_moves_nothing_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(JOIN_RULE))
    rule["match"]["after"]["gap"] = 0
    with pytest.raises(SystemExit, match="machine-approved already"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_negative_gap_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(JOIN_RULE))
    rule["match"]["after"]["gap"] = -1
    with pytest.raises(SystemExit, match="further apart"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_break_seam_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(JOIN_RULE))
    rule["match"]["before"]["seam_out"] = "break"
    with pytest.raises(SystemExit, match="not a yK height"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_join_rule_missing_its_before_block_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(JOIN_RULE))
    rule["match"].pop("before")
    with pytest.raises(SystemExit, match="needs match.before to be exactly"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_the_join_and_slide_shapes_at_once_is_refused(tmp_path):
    rule = json.loads(json.dumps(JOIN_RULE))
    rule["match"]["after"]["slide"] = -1
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_main_fills_only_the_blank_matching_join_dropped_units(tmp_path, monkeypatch, slide_fonts):
    units = [join_window("j-1"), join_window("j-2"), founding_window("s-1")]
    payload = _run_main(
        tmp_path,
        monkeypatch,
        units,
        [{"unit": "j-2", "verdict": "approve", "note": "already", "at": STAMP}],
        rules_list=(JOIN_RULE,),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["j-1"]
    assert payload["verdicts"][0]["note"] == f"[standing: {JOIN_RULE['id']}] {JOIN_RULE['note']}"


def test_main_fills_only_the_blank_matching_ink_gain_units(tmp_path, monkeypatch, slide_fonts):
    units = [gain_window("g-1"), gain_window("g-2"), founding_window("s-1")]
    payload = _run_main(
        tmp_path,
        monkeypatch,
        units,
        [{"unit": "g-2", "verdict": "approve", "note": "already", "at": STAMP}],
        rules_list=(GAIN_RULE,),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["g-1"]
    assert payload["verdicts"][0]["note"] == f"[standing: {GAIN_RULE['id']}] {GAIN_RULE['note']}"


def _surface(tmp_path, units, fonts=None):
    surface = tmp_path / "review"
    (surface / "units").mkdir(parents=True)
    (surface / "manifest.json").write_text(
        json.dumps({"generated_at": STAMP, "classes": [{"id": "all", "shards": ["units/all.json"]}]})
    )
    (surface / "units" / "all.json").write_text(json.dumps(units))
    if fonts is not None:
        (surface / "fonts").mkdir()
        for side in ("before", "after"):
            (surface / "fonts" / f"{side}.otf").write_bytes(pathlib.Path(fonts[side]).read_bytes())
    return surface


def _run_main(tmp_path, monkeypatch, units, verdicts, rules_list=(RULE,), fonts=None):
    surface = _surface(tmp_path, units, fonts)
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
    with pytest.raises(SystemExit, match="predates the ink-delta, slide, ink-gain, and join-dropped"):
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


COMPOSED_EXT_RULE = {
    "id": "j-exit-extension-dropped",
    "verdict": "approve",
    "note": "the follower sits a pixel closer to ·J",
    "match": {
        "before": {
            "pivot": "qsJ",
            "exit_extension": "ex-ext-1",
            "seam_out": "y0",
            "follower": "qsF3",
        },
        "after": {
            "pivot_cells": ["qsJ/full/None/None/"],
            "follower_cells": ["qsF3/full/None/None/"],
        },
        "except_left": [],
    },
}

COMPOSABLE_RULES = [SLIDE_RULE, COMPOSED_EXT_RULE]

COMPOSED_GLYPHS = ["qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-1", "qsF3", "qsF1"]
COMPOSED_CODEPOINTS = "E001:E002:E008:E006:E007:E003"


def composed_window(uid="c-1", pair=None):
    return slide_unit(uid, COMPOSED_GLYPHS, COMPOSED_CODEPOINTS, pair=pair)


def guarded_rule(rule, families):
    copied = json.loads(json.dumps(rule))
    copied["match"]["except_left"] = list(families)
    return copied


class _RefusingComparator:
    intern = None

    def named_run(self, *args, **kwargs):
        raise AssertionError("the pre-gate let a window only one rule speaks for reach the fonts")


class _RefusingContext:
    """A SlideContext stand-in whose comparator raises the moment anything asks it to shape, so a test can prove the name-grain pre-gate answered before the fonts were ever consulted."""

    def __init__(self) -> None:
        self.comparator = _RefusingComparator()
        self.memo = {}
        self.composed = {}


def test_a_slide_and_an_extension_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSABLE_RULES, composed_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], COMPOSED_EXT_RULE["id"]: [3]}


def test_main_writes_one_composed_record_and_leaves_the_per_rule_lines(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1")],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["c-1"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "approve"
    assert record["at"] == STAMP
    assert record["note"] == (
        f"[standing: {SLIDE_RULE['id']} + {COMPOSED_EXT_RULE['id']}] "
        f"{SLIDE_RULE['note']}; {COMPOSED_EXT_RULE['note']}"
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {COMPOSED_EXT_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left"
    ) in lines
    assert (
        f"  {SLIDE_RULE['id']} + {COMPOSED_EXT_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


@pytest.mark.parametrize(
    "after",
    [
        "after-extra-prefix-pixel",
        "after-extra-middle-pixel",
        "after-extra-tail-pixel",
        "after-extra-post-follower-pixel",
    ],
)
def test_one_extra_pixel_anywhere_defeats_the_composed_reading(slide_context, after):
    assert sv._composed(COMPOSABLE_RULES, composed_window(), slide_context(after)) is None


def test_a_composed_window_already_verdicted_is_counted_and_not_refilled(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1"), composed_window("c-2")],
        [{"unit": "c-1", "verdict": "reject", "note": "", "at": "2026-07-11T00:00:00Z"}],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["c-2"]
    lines = capsys.readouterr().out.splitlines()
    assert (
        f"  {SLIDE_RULE['id']} + {COMPOSED_EXT_RULE['id']}: 1 filled, 1 already verdicted, "
        "0 held for review by except_left"
    ) in lines


def test_the_composed_memo_never_serves_one_rule_sets_ids_to_another(slide_context):
    context = slide_context()
    renamed = [json.loads(json.dumps(rule)) for rule in COMPOSABLE_RULES]
    for rule in renamed:
        rule["id"] = rule["id"] + "-twin"
    assert set(sv._composed(COMPOSABLE_RULES, composed_window(), context) or ()) == {
        SLIDE_RULE["id"],
        COMPOSED_EXT_RULE["id"],
    }
    assert set(sv._composed(renamed, composed_window(), context) or ()) == {
        SLIDE_RULE["id"] + "-twin",
        COMPOSED_EXT_RULE["id"] + "-twin",
    }


def test_two_composable_rules_refuse_a_surface_that_predates_the_ink_delta_field(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="predates the ink-delta, slide, ink-gain, and join-dropped"):
        _run_main(tmp_path, monkeypatch, [tea_i("u-1")], [], rules_list=(EXT_RULE, COMPOSED_EXT_RULE))


def extension_only_window(uid="e-1"):
    return slide_unit(
        uid, ["qsL", "qsJ.ex-y0.ex-ext-1", "qsF3"], "E001:E006:E007", pair={"left": 1, "right": 2}
    )


def test_a_window_one_rule_explains_is_not_composed(slide_context):
    context = slide_context()
    assert sv._composed(COMPOSABLE_RULES, founding_window(), context) is None
    assert sv._composed(COMPOSABLE_RULES, extension_only_window(), context) is None


def test_main_fills_a_single_shape_window_under_that_shapes_own_line(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [founding_window("s-1"), extension_only_window("e-1")],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
    )
    by_unit = {record["unit"]: record for record in payload["verdicts"]}
    assert set(by_unit) == {"s-1", "e-1"}
    assert by_unit["s-1"]["note"] == f"[standing: {SLIDE_RULE['id']}] {SLIDE_RULE['note']}"
    assert by_unit["e-1"]["note"] == f"[standing: {COMPOSED_EXT_RULE['id']}] {COMPOSED_EXT_RULE['note']}"
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {COMPOSED_EXT_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left"
    ) in lines
    assert not any(" + " in line for line in lines)


def slide_fixture_windows():
    """Every window the slide shape's own fixtures build, refusals included, so the composed walk can be held against `_matches_slide` over the lot."""
    bare = founding_window("s-1b")
    del bare["ink_deltas"]
    return [
        founding_window(),
        bare,
        slide_unit("s-2", FOUNDING_GLYPHS, FOUNDING_CODEPOINTS, deltas={}),
        slide_unit("s-3", FOUNDING_GLYPHS, FOUNDING_CODEPOINTS, deltas=[SLIDE_DELTA]),
        slide_unit(
            "s-4",
            FOUNDING_GLYPHS,
            FOUNDING_CODEPOINTS,
            configs=("default", "ss03"),
            deltas={"default": SLIDE_DELTA, "ss03": UNLISTED_DELTA},
        ),
        slide_unit(
            "s-5",
            FOUNDING_GLYPHS,
            FOUNDING_CODEPOINTS,
            configs=("default", "ss03"),
            deltas={"default": SLIDE_DELTA},
        ),
        slide_unit(
            "s-6", FOUNDING_GLYPHS, FOUNDING_CODEPOINTS, configs=("ss03",), deltas={"default": SLIDE_DELTA}
        ),
        slide_unit("s-7", ["qsL", "qsF1"], "E001:E003"),
        slide_unit("s-8", ["qsL", "qsSee.ex-y0x", "qsF1"], "E001:E002:E003"),
        slide_unit("s-9", ["qsL", "qsSee.ex-y0", "qsF1"], "E001:E002:E003"),
        slide_unit("s-10", ["qsL", "qsSee.ex-y0"], "E001:E005"),
        slide_unit("s-11", ["qsL", "qsSee.ex-y0", "qsF1", "qsF9"], FOUNDING_CODEPOINTS),
        slide_unit("s-12", ["qsL", "qsSee.ex-y0", "qsF1", "qsSee.ex-y0", "qsF1"], "E001:E002:E003:E002:E003"),
    ]


@pytest.mark.parametrize("after", ["after", "after-extra-prefix-pixel", "after-extra-follower-pixel"])
def test_the_composed_walk_credits_the_slide_exactly_where_the_slide_matcher_does(slide_context, after):
    context = slide_context(after)
    for window in slide_fixture_windows():
        credited = set(sv._composed_walk([SLIDE_RULE], window, context) or ())
        assert (credited == {SLIDE_RULE["id"]}) == sv._matches(
            SLIDE_RULE["match"], window, context=context
        ), window["id"]


def test_a_failed_extension_candidate_is_judged_as_span_ink(slide_context):
    context = slide_context("after-unshortened-pivot")
    assert sv._candidates(COMPOSED_EXT_RULE["match"], composed_window()) == [3]
    assert sv._composed_walk(COMPOSABLE_RULES, composed_window(), context) == {SLIDE_RULE["id"]: [1]}
    assert sv._composed(COMPOSABLE_RULES, composed_window(), context) is None


def test_a_redrawn_follower_is_no_event_and_the_window_is_refused(slide_context):
    context = slide_context("after-redrawn-follower")
    assert sv._composed_walk(COMPOSABLE_RULES, composed_window(), context) is None
    assert sv._composed(COMPOSABLE_RULES, composed_window(), context) is None


def test_a_follower_cell_the_rule_does_not_name_is_no_candidate():
    strayed = composed_window()
    strayed["after"]["cells"][4] = "qsF3/tucked/None/None/"
    assert sv._candidates(COMPOSED_EXT_RULE["match"], strayed) == []
    assert sv._candidates(COMPOSED_EXT_RULE["match"], composed_window()) == [3]


def test_a_pivot_whose_after_form_contracts_off_the_seam_row_never_composes(slide_context):
    assert sv._composed(COMPOSABLE_RULES, composed_window(), slide_context("after-contracted-pivot")) is None


def test_a_dropped_cell_off_the_seam_row_never_composes(slide_context):
    crowned = slide_unit(
        "c-crown",
        ["qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-1.crown", "qsF3"],
        "E001:E002:E008:E00C:E007",
    )
    assert sv._candidates(COMPOSED_EXT_RULE["match"], crowned) == [3]
    assert sv._composed(COMPOSABLE_RULES, crowned, slide_context()) is None


def test_a_seam_that_names_no_height_yields_no_extension_candidate():
    match = json.loads(json.dumps(COMPOSED_EXT_RULE["match"]))
    match["before"]["seam_out"] = "break"
    broken = composed_window()
    broken["before"]["seams"] = ["break"] * 4
    broken["after"]["seams"] = ["break"] * 4
    assert sv._candidates(match, broken) == []


SHORTENED_EXT_RULE = {
    "id": "j-exit-extension-shortened",
    "verdict": "approve",
    "note": "·J reaches its follower with one column of extension where it drew three",
    "match": {
        "before": {
            "pivot": "qsJ",
            "exit_extension": "ex-ext-3",
            "seam_out": "y0",
            "follower": "qsF3",
        },
        "after": {
            "pivot_cells": ["qsJ/full/None/None/ex-ext-1"],
            "follower_cells": ["qsF3/full/None/None/"],
        },
        "except_left": [],
    },
}


def shortened_window(uid="c-short", pivot_codepoint="E00E"):
    window = slide_unit(
        uid,
        ["qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-3.long", "qsF3", "qsF1"],
        f"E001:E002:E008:{pivot_codepoint}:E007:E003",
    )
    window["after"]["cells"][3] = "qsJ/full/None/None/ex-ext-1"
    return window


def test_a_slide_and_a_shortened_extension_in_one_window_compose(slide_context):
    assert sv._candidates(SHORTENED_EXT_RULE["match"], shortened_window()) == [3]
    assert sv._composed([SLIDE_RULE, SHORTENED_EXT_RULE], shortened_window(), slide_context()) == {
        SLIDE_RULE["id"]: [1],
        SHORTENED_EXT_RULE["id"]: [3],
    }


CONTRACTED_EXT_RULE = {
    "id": "et-exit-contracted",
    "verdict": "approve",
    "note": "the follower sits a pixel closer to ·Et",
    "match": {
        "before": {
            "pivot": "qsEt",
            "exit_extension": "ex-con-1",
            "seam_out": "y0",
            "follower": "qsF3",
        },
        "after": {
            "pivot_cells": ["qsEt/hapax/None/None/ex-con-1"],
            "follower_cells": ["qsF3/full/None/None/"],
        },
        "except_left": [],
    },
}


def contracted_window(uid="c-con"):
    window = slide_unit(
        uid,
        ["qsL", "qsSee.ex-y0", "qsM", "qsEt", "qsF3", "qsF1"],
        "E001:E002:E008:E010:E007:E003",
    )
    window["after"]["cells"][3] = "qsEt/hapax/None/None/ex-con-1"
    return window


def test_a_slide_and_a_contraction_in_one_window_compose(slide_context):
    assert sv._candidates(CONTRACTED_EXT_RULE["match"], contracted_window()) == [3]
    assert sv._composed([SLIDE_RULE, CONTRACTED_EXT_RULE], contracted_window(), slide_context()) == {
        SLIDE_RULE["id"]: [1],
        CONTRACTED_EXT_RULE["id"]: [3],
    }


def test_a_contraction_rule_has_no_candidate_in_a_dropped_extension_window():
    assert sv._candidates(CONTRACTED_EXT_RULE["match"], composed_window()) == []
    dropped = contracted_window()
    dropped["before"]["glyphs"][3] = "qsEt.ex-ext-1"
    assert sv._candidates(CONTRACTED_EXT_RULE["match"], dropped) == []


def test_a_rule_naming_a_kept_extension_never_composes_over_a_tail_dropped_whole(slide_context):
    whole = shortened_window("c-whole", "E00F")
    assert sv._candidates(SHORTENED_EXT_RULE["match"], whole) == [3]
    assert sv._composed([SLIDE_RULE, SHORTENED_EXT_RULE], whole, slide_context()) is None


def test_a_rule_naming_an_extensionless_pivot_cell_has_no_candidate_in_a_shortened_window():
    dropped_whole = json.loads(json.dumps(SHORTENED_EXT_RULE["match"]))
    dropped_whole["after"]["pivot_cells"] = ["qsJ/full/None/None/"]
    assert sv._candidates(dropped_whole, shortened_window()) == []


def test_a_tail_wider_than_the_named_extension_is_refused(slide_context):
    wide = slide_unit(
        "c-wide",
        ["qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-1.wide", "qsF3"],
        "E001:E002:E008:E00A:E007",
    )
    assert sv._candidates(COMPOSED_EXT_RULE["match"], wide) == [3]
    assert sv._composed(COMPOSABLE_RULES, wide, slide_context()) is None


def test_a_window_only_one_rule_has_a_candidate_in_is_never_shaped():
    assert sv._composed(COMPOSABLE_RULES, founding_window(), _RefusingContext()) is None
    assert sv._composed(COMPOSABLE_RULES, extension_only_window(), _RefusingContext()) is None


def test_markers_ride_through_a_composed_window(slide_context):
    spaced = slide_unit(
        "c-space",
        ["space", "qsL", "qsSee.ex-y0", "space", "qsM", "qsJ.ex-y0.ex-ext-1", "qsF3"],
        "0020:E001:E002:0020:E008:E006:E007",
    )
    assert sv._composed(COMPOSABLE_RULES, spaced, slide_context()) == {
        SLIDE_RULE["id"]: [2],
        COMPOSED_EXT_RULE["id"]: [5],
    }


def test_a_marker_at_a_candidate_position_is_no_event(slide_context):
    context = slide_context()
    blanked = slide_unit(
        "c-blank",
        ["qsL", "qsSee.ex-y0.blank", "qsM", "qsJ.ex-y0.ex-ext-1", "qsF3"],
        "E001:E00B:E008:E006:E007",
    )
    assert sv._candidates(SLIDE_RULE["match"], blanked) == [1]
    assert sv._composed_walk(COMPOSABLE_RULES, blanked, context) == {COMPOSED_EXT_RULE["id"]: [3]}
    assert sv._composed(COMPOSABLE_RULES, blanked, context) is None


def test_two_rules_claiming_one_position_refuse(slide_context):
    twin = json.loads(json.dumps(SLIDE_RULE))
    twin["id"] = "see-grounded-left-column-dropped-again"
    assert sv._composed_walk([SLIDE_RULE, twin], founding_window(), slide_context()) is None


def test_an_extension_whose_follower_is_a_slide_pivot_refuses(slide_context):
    chained = json.loads(json.dumps(COMPOSED_EXT_RULE))
    chained["match"]["before"]["follower"] = ["qsF3", "qsSee"]
    chained["match"]["after"]["follower_cells"] = ["qsF3/full/None/None/", "qsSee/full/None/None/"]
    window = slide_unit("c-chain", ["qsL", "qsJ.ex-y0.ex-ext-1", "qsSee.ex-y0", "qsM"], "E001:E006:E002:E008")
    assert sv._candidates(chained["match"], window) == [1]
    assert sv._candidates(SLIDE_RULE["match"], window) == [2]
    assert sv._composed_walk([SLIDE_RULE, chained], window, slide_context()) is None


def test_the_slide_guard_holds_the_whole_composed_window(slide_context):
    rules = [guarded_rule(SLIDE_RULE, ["qsL"]), COMPOSED_EXT_RULE]
    window = composed_window()
    context = slide_context()
    events = sv._composed(rules, window, context)
    assert events is not None
    assert sv._composed_held(rules, window, events, context)


def test_a_guarded_rule_outside_the_walk_still_holds_a_composed_window(slide_context):
    context = slide_context()
    bystander = {
        "id": "the-whole-change-is-blessed",
        "verdict": "approve",
        "note": "blessed, except after ·L",
        "match": {"after": {"ink_deltas": [SLIDE_DELTA]}, "except_left": []},
    }
    window = composed_window()
    events = sv._composed(COMPOSABLE_RULES, window, context)
    assert events is not None
    assert sv._matches(bystander["match"], window, context=context)
    assert not sv._composed_held([*COMPOSABLE_RULES, bystander], window, events, context)
    assert sv._composed_held([*COMPOSABLE_RULES, guarded_rule(bystander, ["qsL"])], window, events, context)


def test_the_extension_guard_reads_only_the_pivots_left_neighbor(slide_context):
    context = slide_context()
    at_pivot = [SLIDE_RULE, guarded_rule(COMPOSED_EXT_RULE, ["qsM"])]
    elsewhere = [SLIDE_RULE, guarded_rule(COMPOSED_EXT_RULE, ["qsL"])]
    window = composed_window()
    held = sv._composed(at_pivot, window, context)
    assert held is not None and sv._composed_held(at_pivot, window, held, context)
    free = sv._composed(elsewhere, window, context)
    assert free is not None and not sv._composed_held(elsewhere, window, free, context)


def test_main_holds_a_guarded_composed_window_and_hands_it_to_nobody(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    guarded = guarded_rule(COMPOSED_EXT_RULE, ["qsM"])
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1", pair={"left": 3, "right": 4})],
        [],
        rules_list=(SLIDE_RULE, guarded),
        fonts=slide_fonts,
    )
    assert payload["verdicts"] == []
    lines = capsys.readouterr().out.splitlines()
    assert (
        f"  {SLIDE_RULE['id']} + {guarded['id']}: 0 filled, 0 already verdicted, "
        "1 held for review by except_left"
    ) in lines
    assert f"  {guarded['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines


def test_a_credited_either_rule_weakens_the_composed_verdict(tmp_path, monkeypatch, slide_fonts):
    soft = json.loads(json.dumps(COMPOSED_EXT_RULE))
    soft["verdict"] = "either"
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1")],
        [],
        rules_list=(SLIDE_RULE, soft),
        fonts=slide_fonts,
    )
    record = payload["verdicts"][0]
    assert record["verdict"] == "either"
    assert "(either:" not in record["note"]


def test_a_matching_ink_delta_rule_weakens_the_composed_verdict_and_is_named(
    tmp_path, monkeypatch, slide_fonts
):
    soft = {
        "id": "the-window-may-go-either-way",
        "verdict": "either",
        "note": "this whole ink change was blessed either way",
        "match": {"after": {"ink_deltas": [SLIDE_DELTA]}, "except_left": []},
    }
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1")],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE, soft),
        fonts=slide_fonts,
    )
    record = payload["verdicts"][0]
    assert record["verdict"] == "either"
    assert record["note"].endswith(f" (either: {soft['id']})")


def test_a_window_the_extension_rule_fills_today_moves_to_the_composed_line(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    window = composed_window("c-1", pair={"left": 3, "right": 4})
    assert sv._matches(COMPOSED_EXT_RULE["match"], window)
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [window],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["c-1"]
    assert payload["verdicts"][0]["note"].startswith(
        f"[standing: {SLIDE_RULE['id']} + {COMPOSED_EXT_RULE['id']}]"
    )
    lines = capsys.readouterr().out.splitlines()
    assert (
        f"  {COMPOSED_EXT_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left"
    ) in lines
    assert (
        f"  {SLIDE_RULE['id']} + {COMPOSED_EXT_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


def test_a_candidate_whose_contract_fails_is_judged_as_span_ink(slide_context):
    context = slide_context()
    riding = slide_unit(
        "c-ride",
        ["qsSee.ex-y0.spare", "qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-1", "qsF3"],
        "E009:E001:E002:E008:E006:E007",
    )
    assert sv._candidates(SLIDE_RULE["match"], riding) == [0, 2]
    assert sv._composed(COMPOSABLE_RULES, riding, context) == {
        SLIDE_RULE["id"]: [2],
        COMPOSED_EXT_RULE["id"]: [4],
    }
    refusing = slide_unit(
        "c-refuse",
        ["qsSee.ex-y0.spare", "qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-1", "qsF3"],
        "E00D:E001:E002:E008:E006:E007",
    )
    assert sv._candidates(SLIDE_RULE["match"], refusing) == [0, 2]
    assert sv._composed(COMPOSABLE_RULES, refusing, context) is None


COMPOSED_GAIN_GLYPHS = ["qsL", "qsSee.ex-y0", "qsRoe.en-ext-1-at-5", "qsF1"]
COMPOSED_GAIN_CODEPOINTS = "E001:E002:E020:E003"
COMPOSED_GAIN_RULES = [SLIDE_RULE, GAIN_RULE]


def composed_gain_window(uid="cg-1"):
    return slide_unit(uid, COMPOSED_GAIN_GLYPHS, COMPOSED_GAIN_CODEPOINTS)


def test_a_slide_and_an_ink_gain_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_GAIN_RULES, composed_gain_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], GAIN_RULE["id"]: [2]}


def test_a_pure_gain_is_not_composed(slide_context):
    assert sv._composed(COMPOSED_GAIN_RULES, gain_window(), slide_context()) is None
    assert sv._matches(GAIN_RULE["match"], gain_window(), context=slide_context())


def test_main_writes_one_composed_gain_record_and_leaves_the_per_rule_lines(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_gain_window("cg-1")],
        [],
        rules_list=(SLIDE_RULE, GAIN_RULE),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["cg-1"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "approve"
    assert record["note"] == (
        f"[standing: {SLIDE_RULE['id']} + {GAIN_RULE['id']}] " f"{SLIDE_RULE['note']}; {GAIN_RULE['note']}"
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert f"  {GAIN_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {SLIDE_RULE['id']} + {GAIN_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


def test_one_extra_pixel_defeats_the_composed_gain_reading(slide_context):
    assert (
        sv._composed(COMPOSED_GAIN_RULES, composed_gain_window(), slide_context("after-extra-prefix-pixel"))
        is None
    )


def test_a_wrong_gained_cell_defeats_the_composed_gain_reading(slide_context):
    assert (
        sv._composed(COMPOSED_GAIN_RULES, composed_gain_window(), slide_context("after-roe-wrong-cell"))
        is None
    )


def test_the_composed_walk_credits_the_gain_exactly_where_the_gain_matcher_does(slide_context):
    context = slide_context()
    for window in (gain_window(), composed_gain_window(), founding_window()):
        credited = set(sv._composed_walk([GAIN_RULE], window, context) or ())
        assert (credited == {GAIN_RULE["id"]}) == sv._matches(
            GAIN_RULE["match"], window, context=context
        ), window["id"]


def test_the_gain_guard_holds_the_whole_composed_window(slide_context):
    events = sv._composed(COMPOSED_GAIN_RULES, composed_gain_window(), slide_context())
    assert sv._composed_held(
        [guarded_rule(GAIN_RULE, ["qsL"]), SLIDE_RULE],
        composed_gain_window(),
        events,
        slide_context(),
    )


def test_main_fills_a_single_gain_window_under_that_shapes_own_line(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [gain_window("g-1"), composed_gain_window("cg-1")],
        [],
        rules_list=(SLIDE_RULE, GAIN_RULE),
        fonts=slide_fonts,
    )
    by_unit = {record["unit"]: record for record in payload["verdicts"]}
    assert set(by_unit) == {"g-1", "cg-1"}
    assert by_unit["g-1"]["note"] == f"[standing: {GAIN_RULE['id']}] {GAIN_RULE['note']}"
    assert by_unit["cg-1"]["note"].startswith(f"[standing: {SLIDE_RULE['id']} + {GAIN_RULE['id']}]")
    lines = capsys.readouterr().out.splitlines()
    assert f"  {GAIN_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {SLIDE_RULE['id']} + {GAIN_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


COMPOSED_JOIN_GLYPHS = ["qsL", "qsSee.ex-y0", "qsAt", "qsIt"]
COMPOSED_JOIN_CODEPOINTS = "E001:E002:E021:E022"
COMPOSED_JOIN_RULES = [SLIDE_RULE, JOIN_RULE]
EXT_JOIN_GLYPHS = ["qsL", "qsJ.ex-y0.ex-ext-1", "qsF3", "qsAt", "qsIt"]
EXT_JOIN_CODEPOINTS = "E001:E006:E007:E021:E022"
EXT_JOIN_RULES = [COMPOSED_EXT_RULE, JOIN_RULE]


def composed_join_window(uid="cj-1"):
    return unit(
        uid,
        list(COMPOSED_JOIN_GLYPHS),
        ["y0", "y0", "y5"],
        [
            "qsL/full/None/None/",
            "qsSee/full/None/None/",
            "qsAt/full/None/None/",
            "qsIt/full/None/None/",
        ],
        ["y0", "y0", "break"],
        codepoints=COMPOSED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 2, "right": 3},
    )


def extension_join_window(uid="ej-1"):
    return unit(
        uid,
        list(EXT_JOIN_GLYPHS),
        ["y0", "y0", "y0", "y5"],
        [
            "qsL/full/None/None/",
            "qsJ/full/None/None/",
            "qsF3/full/None/None/",
            "qsAt/full/None/None/",
            "qsIt/full/None/None/",
        ],
        ["y0", "y0", "y0", "break"],
        codepoints=EXT_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 3, "right": 4},
    )


def test_a_slide_and_a_join_drop_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_JOIN_RULES, composed_join_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], JOIN_RULE["id"]: [2]}


def test_an_extension_and_a_join_drop_in_one_window_compose(slide_context):
    events = sv._composed(EXT_JOIN_RULES, extension_join_window(), slide_context())
    assert events == {COMPOSED_EXT_RULE["id"]: [1], JOIN_RULE["id"]: [3]}


def test_a_pure_join_drop_is_not_composed(slide_context):
    assert sv._composed(COMPOSED_JOIN_RULES, join_window(), slide_context()) is None


def test_main_writes_one_composed_join_record_and_leaves_the_per_rule_lines(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_join_window("cj-1")],
        [],
        rules_list=(SLIDE_RULE, JOIN_RULE),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["cj-1"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "approve"
    assert record["note"] == (
        f"[standing: {SLIDE_RULE['id']} + {JOIN_RULE['id']}] " f"{SLIDE_RULE['note']}; {JOIN_RULE['note']}"
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert f"  {JOIN_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {SLIDE_RULE['id']} + {JOIN_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


def test_one_extra_pixel_defeats_the_composed_join_reading(slide_context):
    assert (
        sv._composed(COMPOSED_JOIN_RULES, composed_join_window(), slide_context("after-extra-prefix-pixel"))
        is None
    )


def test_a_redrawn_join_follower_defeats_the_composed_reading(slide_context):
    assert (
        sv._composed(
            COMPOSED_JOIN_RULES, composed_join_window(), slide_context("after-join-redrawn-follower")
        )
        is None
    )


def test_the_composed_walk_credits_the_join_exactly_where_the_join_matcher_does(slide_context):
    context = slide_context()
    for window in (join_window(), composed_join_window(), founding_window()):
        credited = set(sv._composed_walk([JOIN_RULE], window, context) or ())
        assert (credited == {JOIN_RULE["id"]}) == sv._matches(
            JOIN_RULE["match"], window, context=context
        ), window["id"]


def test_the_join_guard_holds_the_whole_composed_window(slide_context):
    events = sv._composed(COMPOSED_JOIN_RULES, composed_join_window(), slide_context())
    assert sv._composed_held(
        [guarded_rule(JOIN_RULE, ["qsL"]), SLIDE_RULE],
        composed_join_window(),
        events,
        slide_context(),
    )


def test_main_fills_a_single_join_window_under_that_shapes_own_line(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [join_window("j-1"), composed_join_window("cj-1")],
        [],
        rules_list=(SLIDE_RULE, JOIN_RULE),
        fonts=slide_fonts,
    )
    by_unit = {record["unit"]: record for record in payload["verdicts"]}
    assert set(by_unit) == {"j-1", "cj-1"}
    assert by_unit["j-1"]["note"] == f"[standing: {JOIN_RULE['id']}] {JOIN_RULE['note']}"
    assert by_unit["cj-1"]["note"].startswith(f"[standing: {SLIDE_RULE['id']} + {JOIN_RULE['id']}]")
    lines = capsys.readouterr().out.splitlines()
    assert f"  {JOIN_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {SLIDE_RULE['id']} + {JOIN_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines
