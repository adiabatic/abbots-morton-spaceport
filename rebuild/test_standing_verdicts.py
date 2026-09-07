"""Tests for the standing-approval fill: the delta shapes — the two structural pattern matches, being the ligature shape (pivot glyph, seams into and out of it, follower family, post-ligature seam, flank-seam identity) and the extension-dropped shape (pivot glyph giving up a named stretch of exit — an `ex-ext-N` it carried, in whole or down to a shorter one its named after cell keeps, or an `ex-con-N` its named after cell carries when the before glyph never had an exit extension — the seam it exits into holding its height, the full after-cell identity of pivot and follower, every other seam standing still, nothing ligating anywhere, and the unit's own judgment fields agreeing that this seam is the question), the ink-exact ink-delta shape (the unit's persisted per-config digests being a nonempty subset of the ones the rule blesses, so an ink-identical window matches nothing and one unlisted delta under one config fails the whole unit closed, and a surface predating the field refuses the run outright), and the rendered-pixel slide shape, whose preconditions are read off the index record before anything is shaped (a nonempty `ink_deltas` holding one distinct digest whose keys are exactly the unit's config set, and a pivot-prefix name among the recorded before glyphs) and whose geometry is then re-derived in a purpose-built font pair, where the pivot keeps its exact ink with its own-frame origin displaced by the declared column count and every span's union of ink slides cumulatively — so a union-invisible name-grain re-spelling to the pivot's right rides along, while one stray pixel anywhere in the window, or a font pair that never settles into the named pivot, fails the match closed — the rendered-pixel ink-gain shape, whose preconditions match the slide shape's and whose geometry is the named pivot keeping its placement, height, and own-frame origin while gaining exactly the named cells, every following span moving by the declared count — the rendered-pixel join-dropped shape, whose preconditions are a named pivot–follower seam dropping from a yK height to a break plus the slide shape's digest-agreement, and whose geometry is both letters keeping their exact picture and own-frame origin with the follower sitting the declared gap further and everything after it sitting the same extra gap away — the rendered-pixel entry-extension-dropped shape, whose preconditions match the slide shape's and whose geometry is the named pivot keeping its placement, height, and own-frame origin while its after picture is the old one compacted left by the declared column count, everything after the pivot sliding closer by that count — the rendered-pixel stub-dropped shape, whose preconditions match the slide shape's and whose geometry is walked position by position because a pivot is a position rather than a name: each position that settles into a named after form is judged as the old picture compacted left by the declared column count with its placement moving right by that count and its origin standing still, every span between pivots rendering identically with no displacement — so a second same-family letter keeping its old form rides as span ink, and one stray pixel anywhere fails the match closed — the rendered-pixel redrawn shape, whose preconditions match the slide shape's and whose geometry is walked position by position because a pivot is a position rather than a name: each position that settles into a named after form is judged as the named cell trade at one common column offset (an entry-extended frame names the same trade one column over), its placement carrying the displacement accumulated so far — or up to the entry contraction its new form names closer than that, which the rest of the window then carries too — every span between pivots rendering identically under it, and the displacement growing by the declared shift and whatever the pivot took at each pivot — so a second same-family letter keeping its old form rides as span ink, a trade that only gives ink up names an empty added set (an exit contraction, which the name-grain extension-dropped shape would speak for too but blindly), and one stray pixel anywhere fails the match closed — the composed reading that runs before all of them and credits two or more rules for one window — its name-grain pre-gate refusing to shape a window fewer than two rules have a candidate in, its walk carrying a running column displacement across the window so that each span between events must render identically once displaced, its chaining of a join-dropped or extension event whose follower is itself the next event, its skipping of an extension's named follower so a named redraw does not block composition, its refusal of a pivot contracting off the seam row, of a tail wider than the pivot gave up, and of two rules claiming one position, its judging of a failed candidate as ordinary span ink, its per-shape guard scopes, and its own reporting line, which `main` keeps clear of the per-rule lines — the except_left guard, which reads a ligature's trailing left component and refuses the whole unit rather than the one position, blankness against the verdicts file (parked skip verdicts are not blank), the non-winning manifest stamp on every emitted record, and rules-file validation, which admits exactly one shape per rule and checks that shape's own coherence."""

import json
import pathlib
import sys

import pytest

from rebuild.tools import standing_verdicts as sv

STAMP = "2026-07-10T00:00:00Z"

RULE = {
    "id": "fixture-ligature",
    "verdict": "approve",
    "note": "never a different opinion unless ·X is ·Out",
    "match": {
        "before": {"pivot": "qsTea.half", "seam_into": "y5", "seam_out": "break", "follower": "qsOy"},
        "after": {"ligature": "qsTea_qsOy", "seam_into": "break"},
        "except_left": ["qsOut"],
    },
}

EXT_RULE = {
    "id": "fixture-extension-dropped",
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
    "id": "fixture-extension-shortened",
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
    "id": "fixture-exit-contracted",
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
    "id": "fixture-ink-delta",
    "verdict": "approve",
    "note": "the ·May has lost its left-side stub pixel and nothing else moved",
    "match": {
        "after": {"ink_deltas": [DELTA_A, DELTA_B]},
        "except_left": [],
    },
}

SLIDE_DELTA = "d-aaaaaaaaaaaa"

SLIDE_RULE = {
    "id": "fixture-slide",
    "verdict": "approve",
    "note": "the grounded ·See sits a column closer to what precedes it and everything after it slides over",
    "match": {
        "before": {"pivots": ["qsSee.ex-y0"]},
        "after": {"pivots": ["qsSee.straighter"], "slide": -1},
        "except_left": [],
    },
}

GAIN_RULE = {
    "id": "fixture-ink-gain",
    "verdict": "approve",
    "note": "the bottom of ·Roe sits a pixel closer to ·It",
    "match": {
        "before": {"pivots": ["qsRoe.en-ext-1-at-5"]},
        "after": {"pivots": ["qsRoe.hapax"], "gained": [[1, 0]], "shift": 0},
        "except_left": [],
    },
}

VERTICAL_GAIN_RULE = {
    "id": "fixture-vertical-ink-gain",
    "verdict": "approve",
    "note": "·Tea keeps the full bar under ss03",
    "match": {
        "before": {"pivots": ["qsTea.half.en-y5.after-xheight-exit"]},
        "after": {
            "pivots": ["qsTea.full"],
            "gained": [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4]],
            "shift": 0,
        },
        "except_left": [],
    },
}

JOIN_RULE = {
    "id": "fixture-join-dropped",
    "verdict": "approve",
    "note": "·It sits a column further from ·At — they no longer join at the x-height",
    "match": {
        "before": {"pivot": "qsAt", "seam_out": "y5", "follower": "qsIt"},
        "after": {"gap": 1},
        "except_left": [],
    },
}

ENTRY_RULE = {
    "id": "fixture-entry-extension-dropped",
    "verdict": "approve",
    "note": "·Low sits a pixel closer to ·See",
    "match": {
        "before": {"pivots": ["qsLow.en-ext-1"]},
        "after": {"pivots": ["qsLow.hapax"], "entry_drop": 1},
        "except_left": [],
    },
}

CONTRACTED_ENTRY_RULE = {
    "id": "fixture-entry-contracted",
    "verdict": "approve",
    "note": "·May sits a pixel closer after ·Bay",
    "match": {
        "before": {"left": "qsBay", "pivots": ["qsMay.en-y0.ex-y5"]},
        "after": {
            "pivots": ["qsMay.loop.en-y0.en-con-1", "qsMay.loop.en-y0.ex-y5.en-con-1"],
            "entry_contraction": 1,
        },
        "except_left": [],
    },
}

PLACED_CONTRACTION_RULE = {
    "id": "fixture-entry-contracted-by-placement",
    "verdict": "approve",
    "note": "·Roe sits a pixel closer after ·Bay, its own frame keeping its origin",
    "match": {
        "before": {"left": "qsBay", "pivots": ["qsRoe.ex-y0"]},
        "after": {
            "pivots": ["qsRoe.hapax.en-y5.ex-y0.en-con-1"],
            "entry_contraction": 1,
        },
        "except_left": [],
    },
}

STUB_RULE = {
    "id": "fixture-stub-dropped",
    "verdict": "approve",
    "note": "the ·May has lost its left-side stub pixel and the rest of the window stays put",
    "match": {
        "before": {"pivots": ["qsMay.en-y5"]},
        "after": {"pivots": ["qsMay.loop"], "stub_drop": 1},
        "except_left": [],
    },
}

RETARGET_RULE = {
    "id": "fixture-join-retargeted",
    "verdict": "approve",
    "note": "·Tea sits as the full bar joining ·No at the baseline",
    "match": {
        "before": {"pivot": "qsTea.half", "seam_out": "y5", "follower": "qsNo"},
        "after": {
            "retarget": "y0",
            "pivot_cells": [
                "qsTea/full/None/baseline/",
                "qsTea/full/x-height/baseline/",
                "qsTea/full/top/baseline/",
            ],
            "receiver_cells": [
                "qsNo/flipped/baseline/None/",
                "qsNo/flipped/baseline/baseline/",
            ],
            "shift": -1,
            "follower_shift": 0,
        },
        "except_left": [],
    },
}

MOVING_RETARGET_RULE = {
    "id": "fixture-join-retargeted-with-a-moving-follower",
    "verdict": "approve",
    "note": "·Tea reaches ·No at the baseline and pulls it a column back into the reach",
    "match": {
        "before": {"pivot": "qsTea.half", "seam_out": "y5", "follower": "qsNo"},
        "after": {
            "retarget": "y0",
            "pivot_cells": ["qsTea/full/None/baseline/"],
            "receiver_cells": [
                "qsNo/flipped/baseline/None/",
                "qsNo/flipped/baseline/baseline/",
            ],
            "shift": -2,
            "follower_shift": -1,
        },
        "except_left": [],
    },
}

CREATED_JOIN_RULE = {
    "id": "fixture-join-created",
    "verdict": "approve",
    "note": "·J joins ·F3 where the old font left a break",
    "match": {
        "before": {"pivot": "qsJ", "seam_out": "break", "follower": "qsF3"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsJ/hapax/None/baseline/ex-ext-1"],
            "receiver_cells": ["qsF3/full/None/None/"],
            "shift": -2,
            "follower_advance": 0,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

RETARGETED_CREATED_JOIN_RULE = {
    "id": "fixture-join-created-behind-a-retarget",
    "verdict": "approve",
    "note": "·No joins ·F3 at the baseline where the old font left a break",
    "match": {
        "before": {"pivot": "qsNo", "seam_out": "break", "follower": "qsF3"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsNo/flipped/baseline/baseline/"],
            "receiver_cells": ["qsF3/full/None/None/"],
            "shift": -2,
            "follower_advance": 0,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

RETARGET_BEHIND_CREATED_JOIN_RULE = {
    "id": "fixture-join-created-before-a-retarget",
    "verdict": "approve",
    "note": "·J joins ·Tea at the baseline where the old font left a break",
    "match": {
        "before": {"pivot": "qsJ", "seam_out": "break", "follower": "qsTea"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsJ/hapax/None/baseline/ex-ext-1"],
            "receiver_cells": ["qsTea/full/None/baseline/"],
            "shift": -2,
            "follower_advance": 0,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

EXTENSION_BEHIND_CREATED_JOIN_RULE = {
    "id": "fixture-join-created-before-an-extension",
    "verdict": "approve",
    "note": "·No joins ·J at the baseline where the old font left a break",
    "match": {
        "before": {"pivot": "qsNo", "seam_out": "break", "follower": "qsJ"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsNo/flipped/baseline/baseline/"],
            "receiver_cells": ["qsJ/full/None/None/"],
            "shift": -1,
            "follower_advance": 0,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

CONTRACTED_REDRAWN_CHAIN_RULE = {
    "id": "fixture-join-created-behind-a-contraction-before-a-redraw",
    "verdict": "approve",
    "note": "·May joins ·Eight at the x-height where the old font left a break",
    "match": {
        "before": {"pivot": "qsMay", "seam_out": "break", "follower": "qsEight"},
        "after": {
            "joined": "y5",
            "pivot_cells": ["qsMay/loop/baseline/x-height/en-con-1"],
            "receiver_cells": ["qsEight/smaller-loop/None/None/"],
            "shift": -2,
            "follower_advance": 0,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

REDRAWN_BEHIND_CREATED_JOIN_RULE = {
    "id": "fixture-join-created-before-a-redraw",
    "verdict": "approve",
    "note": "·No joins ·Eight at the baseline where the old font left a break",
    "match": {
        "before": {"pivot": "qsNo", "seam_out": "break", "follower": "qsEight"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsNo/flipped/baseline/baseline/"],
            "receiver_cells": ["qsEight/smaller-loop/None/None/"],
            "shift": -1,
            "follower_advance": 0,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

WIDENED_CREATED_JOIN_RULE = {
    "id": "fixture-join-created-with-a-widened-follower",
    "verdict": "approve",
    "note": "·J joins ·F3 where the old font left a break, and ·F3 redraws a column wider",
    "match": {
        "before": {"pivot": "qsJ", "seam_out": "break", "follower": "qsF3"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsJ/hapax/None/baseline/ex-ext-1"],
            "receiver_cells": ["qsF3/full/None/None/"],
            "shift": -2,
            "follower_advance": 1,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

RETARGET_BEHIND_WIDENED_JOIN_RULE = {
    "id": "fixture-join-created-widened-before-a-retarget",
    "verdict": "approve",
    "note": "·J joins ·Tea at the baseline where the old font left a break, and ·Tea redraws a column wider",
    "match": {
        "before": {"pivot": "qsJ", "seam_out": "break", "follower": "qsTea"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsJ/hapax/None/baseline/ex-ext-1"],
            "receiver_cells": ["qsTea/full/None/baseline/"],
            "shift": -2,
            "follower_advance": 1,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

REACHING_CREATED_JOIN_RULE = {
    "id": "fixture-join-created-with-a-reaching-follower",
    "verdict": "approve",
    "note": "·J joins ·F3 where the old font left a break, and ·F3 takes the join on a form that reaches back a column",
    "match": {
        "before": {"pivot": "qsJ", "seam_out": "break", "follower": "qsF3"},
        "after": {
            "joined": "y0",
            "pivot_cells": ["qsJ/hapax/None/baseline/ex-ext-1"],
            "receiver_cells": ["qsF3/full/None/None/"],
            "shift": -3,
            "follower_advance": 1,
            "follower_reach": 1,
        },
        "except_left": [],
    },
}

CONTRACTED_CREATED_JOIN_RULE = {
    "id": "fixture-join-created-behind-a-contraction",
    "verdict": "approve",
    "note": "·May joins ·F3 at the x-height where the old font left a break",
    "match": {
        "before": {"pivot": "qsMay", "seam_out": "break", "follower": "qsF3"},
        "after": {
            "joined": "y5",
            "pivot_cells": ["qsMay/loop/baseline/x-height/en-con-1"],
            "receiver_cells": ["qsF3/full/x-height/None/"],
            "shift": -2,
            "follower_advance": 0,
            "follower_reach": 0,
        },
        "except_left": [],
    },
}

REDRAWN_RULE = {
    "id": "fixture-redrawn",
    "verdict": "approve",
    "note": "·Eight's bowl pulls in a column and the rest of the window stays put",
    "match": {
        "before": {"pivots": ["qsEight"]},
        "after": {
            "pivots": ["qsEight.smaller-loop"],
            "dropped": [[1, 2]],
            "added": [[1, 1]],
            "shift": 0,
        },
        "except_left": [],
    },
}

PURE_LOSS_RULE = {
    "id": "fixture-redrawn-pure-loss",
    "verdict": "approve",
    "note": "·Key's foot gives up its terminal pixel and the follower comes a column closer",
    "match": {
        "before": {"pivots": ["qsKey"]},
        "after": {
            "pivots": ["qsKey.hapax.ex-y0.ex-con-1"],
            "dropped": [[1, 0]],
            "added": [],
            "shift": -1,
        },
        "except_left": [],
    },
}

PULLED_REDRAWN_RULE = {
    "id": "fixture-redrawn-pulled-in",
    "verdict": "approve",
    "note": "·Eight's bowl pulls in a column and the letter comes a column closer to ·Bay with it",
    "match": {
        "before": {"pivots": ["qsEight"]},
        "after": {
            "pivots": ["qsEight.pulled-loop.en-con-1"],
            "dropped": [[1, 2]],
            "added": [[1, 1]],
            "shift": 0,
        },
        "except_left": [],
    },
}

REDRAWN_EXT_RULE = {
    "id": "fixture-redrawn-extension-dropped",
    "verdict": "approve",
    "note": "·Eight's bowl pulls in a column, its connector extension goes, and the follower sits a column closer",
    "match": {
        "before": {"pivots": ["qsEight.ex-ext-1"]},
        "after": {
            "pivots": ["qsEight.smaller-loop"],
            "dropped": [[1, 2], [2, 0]],
            "added": [[1, 1]],
            "shift": -1,
        },
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


def test_a_rule_with_an_inert_guard_holds_nothing_by_construction():
    bare = dict(RULE, match=guarding(RULE, []))
    units = [canonical("u-1"), canonical("u-2", left="qsOut.ex-ext-1")]
    run = sv.rule_reach([bare], units, {}, STAMP)
    assert run.reaches[bare["id"]].held == []
    assert not any(
        sv._matches(bare["match"], unit, guard=False) and not sv._matches(bare["match"], unit)
        for unit in units
    )
    assert sorted(run.reaches[bare["id"]].filled) == ["u-1", "u-2"]


def test_a_guarded_rules_held_pass_still_runs_and_names_the_held_unit():
    units = [canonical("u-1"), canonical("u-2", left="qsOut.ex-ext-1")]
    run = sv.rule_reach([RULE], units, {}, STAMP)
    assert run.reaches[RULE["id"]].held == ["u-2"]
    assert run.reaches[RULE["id"]].filled == ["u-1"]


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
    "id": "fixture-follower-list-extension-dropped",
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


def it_may(
    uid="u-19",
    pivot="qsIt.en-y5.ex-y0.ex-ext-1",
    pivot_cell="qsIt/hapax/x-height/baseline/",
    follower="qsMay.en-y0.ex-y5",
    follower_cell="qsMay/loop/baseline/None/",
):
    return unit(
        uid,
        ["qsPea.half.ex-y5", pivot, follower, "qsDay"],
        ["y5", "y0", "y5"],
        ["qsPea/half/None/x-height/", pivot_cell, follower_cell, "qsDay/full/x-height/None/"],
        ["y5", "y0", "y5"],
        pair={"left": 1, "right": 2},
    )


def test_the_checked_in_it_may_rule_reads_the_narrowed_seam_and_nothing_wider():
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["it-may-exit-extension-dropped"]["match"]
    assert sv._matches(match, it_may())
    regrouped = it_may()
    regrouped["secondary_seams"] = 1
    assert not sv._matches(match, regrouped)
    elsewhere = it_may()
    elsewhere["pair"] = {"left": 0, "right": 1}
    assert not sv._matches(match, elsewhere)
    kept = it_may(pivot="qsIt.en-y5.ex-y0")
    assert not sv._matches(match, kept)
    other_follower = it_may(follower="qsSee.en-y0", follower_cell="qsSee/normal/baseline/None/")
    assert not sv._matches(match, other_follower)


def it_ah(
    uid="u-20",
    pivot="qsIt.en-y5.ex-y0.ex-ext-1",
    pivot_cell="qsIt/hapax/None/baseline/",
    follower="qsAh.en-y0",
    follower_cell="qsAh/hapax/baseline/None/",
):
    return unit(
        uid,
        ["qsPea.half.ex-y5", pivot, follower, "qsDay"],
        ["y5", "y0", "y5"],
        ["qsPea/half/None/x-height/", pivot_cell, follower_cell, "qsDay/full/x-height/None/"],
        ["y5", "y0", "y5"],
        pair={"left": 1, "right": 2},
    )


def test_the_checked_in_it_ah_rule_reads_the_narrowed_seam_and_nothing_wider():
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["it-ah-exit-extension-dropped"]["match"]
    assert sv._matches(match, it_ah())
    regrouped = it_ah()
    regrouped["secondary_seams"] = 1
    assert not sv._matches(match, regrouped)
    elsewhere = it_ah()
    elsewhere["pair"] = {"left": 0, "right": 1}
    assert not sv._matches(match, elsewhere)
    kept = it_ah(pivot="qsIt.en-y5.ex-y0")
    assert not sv._matches(match, kept)
    entered = it_ah(pivot_cell="qsIt/hapax/x-height/baseline/")
    assert not sv._matches(match, entered)
    other_follower = it_ah(follower="qsMay.en-y0", follower_cell="qsMay/loop/baseline/None/")
    assert not sv._matches(match, other_follower)


def test_the_checked_in_fee_rule_reads_the_ss03_shortening_and_nothing_wider():
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["fee-tea-ss03-exit-extension-shortened"][
        "match"
    ]
    assert sv._matches(match, fee_tea())
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


def test_every_shape_loads_from_one_rules_file(tmp_path):
    rules = sv.load_rules(
        _write_rules(
            tmp_path / "rules.yaml",
            [
                RULE,
                EXT_RULE,
                INK_RULE,
                SLIDE_RULE,
                GAIN_RULE,
                JOIN_RULE,
                ENTRY_RULE,
                STUB_RULE,
                RETARGET_RULE,
                CREATED_JOIN_RULE,
                REDRAWN_RULE,
            ],
        )
    )
    assert [rule["id"] for rule in rules] == [
        RULE["id"],
        EXT_RULE["id"],
        INK_RULE["id"],
        SLIDE_RULE["id"],
        GAIN_RULE["id"],
        JOIN_RULE["id"],
        ENTRY_RULE["id"],
        STUB_RULE["id"],
        RETARGET_RULE["id"],
        CREATED_JOIN_RULE["id"],
        REDRAWN_RULE["id"],
    ]


def test_every_shape_is_named_in_the_module_docstring():
    """The module docstring is the contract authority — the skill sends a reader there and nowhere else for what a shape proves — so a row whose paragraph was never written leaves that authority a reading short, which is how the stub-dropped shape shipped undocumented."""
    doc = sv.__doc__ or ""
    assert [name for name in sv.SHAPES if name not in doc] == []


def _fixture_rules():
    """Every rule dict this module builds at import, discovered off its own globals rather than listed, so a fixture added later is held to the same standard without anyone remembering to enroll it."""
    return {
        name: value
        for name, value in vars(sys.modules[__name__]).items()
        if isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("match"), dict)
    }


def test_no_fixture_rule_borrows_a_checked_in_rules_id():
    """The corpus here is fiction — synthetic letters, invented cells, geometry drawn to exercise a matcher rather than to describe the font — and an id in the `fixture-` namespace says so at a glance. A fixture wearing a real rule's id reads as documentation of that rule while modeling whatever the test needed, which is how an ink-delta fixture came to carry the stub-dropped rule's id for the whole life of that shape; holding the two sets disjoint is what keeps a reader from taking one for the other."""
    checked_in = {rule["id"] for rule in sv.load_rules(sv.RULES)}
    borrowed = sorted(
        f"{name} ({rule['id']})" for name, rule in _fixture_rules().items() if rule["id"] in checked_in
    )
    assert borrowed == []


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
EXTENDED_ENTRY_LOW = (_rect(0, 0, 50, 50), _rect(50, 0, 150, 150))
CONTRACTED_ENTRY_MAY = (_rect(50, 0, 150, 150),)
CONTRACTED_ENTRY_MAY_EXTRA = (_rect(50, 0, 150, 150), _rect(150, 150, 200, 200))
OVERHANGING_FOLLOWER = (_rect(-50, 0, 100, 150),)
UNSHIFTED_ENTRY_LOW = (_rect(50, 0, 150, 150),)
EXTRA_CELL_LOW = (_rect(0, 0, 100, 150), _rect(0, 150, 50, 200))
EIGHTISH = (_rect(0, 0, 50, 150), _rect(50, 100, 100, 150))
EIGHTISH_SMALLER = (_rect(0, 0, 50, 150), _rect(50, 50, 100, 100))
EIGHTISH_EXTENDED = (_rect(0, 0, 50, 150), _rect(50, 100, 100, 150), _rect(100, 0, 150, 50))
EIGHTISH_ENTRY_EXTENDED = (_rect(0, 0, 50, 50), _rect(50, 0, 100, 150), _rect(100, 100, 150, 150))
EIGHTISH_ENTRY_EXTENDED_SMALLER = (_rect(0, 0, 50, 50), _rect(50, 0, 100, 150), _rect(100, 50, 150, 100))
EIGHTISH_SMALLER_AND_A_PIXEL = (_rect(0, 0, 50, 150), _rect(50, 50, 100, 100), _rect(100, 0, 150, 50))
HALF_TEA_BAR = (_rect(0, 250, 50, 450),)
FULL_TEA_BAR = (_rect(0, 0, 50, 450),)
MISALIGNED_FULL_TEA_BAR = (_rect(0, 50, 50, 500),)
FOOTED_KEY = (_rect(0, 0, 50, 450), _rect(50, 0, 100, 50))
SHORTENED_FOOT_KEY = (_rect(0, 0, 50, 450),)
SHORTENED_FOOT_KEY_AND_A_CROWN = (_rect(0, 0, 50, 450), _rect(50, 400, 100, 450))
PLACED_CONTRACTION_ROE = (_rect(0, 0, 50, 100), _rect(50, 0, 150, 150))
PLACED_CONTRACTION_ROE_PULLED = (_rect(0, 50, 50, 100), _rect(50, 0, 150, 150))

_OUTLINES: dict[str, dict[str, tuple]] = {"before": {}, "after": {}}
_CODEPOINTS: dict[tuple[str, str], int] = {}
_FIRST_CODEPOINT = 0xE001


def register_glyph(side, name, outline, advance):
    """One side's drawing of one glyph name, recorded once. Outlines are held per side rather than per codepoint because the two sides are separate fonts and an after form routinely answers for several before forms — the trimmed pivot stands in for every tail the rebuild gives up — so a glyph and a before→after pair are two registrations, not one call with six arguments."""
    drawn = _OUTLINES[side]
    if name in drawn:
        raise ValueError(f"the {side} font already draws {name}")
    drawn[name] = (outline, advance)


def register_pair(before_name, after_name):
    """The private-use codepoint that spells one before→after change, allocated rather than chosen so a new window costs nobody a reading of the tail of a table. Both names must already be drawn on their own side, and one pair takes one codepoint: two spellings of the same question would only ever be one window's worth of evidence hiding behind another's."""
    for side, name in (("before", before_name), ("after", after_name)):
        if name not in _OUTLINES[side]:
            raise ValueError(f"the {side} font draws no {name}")
    if (before_name, after_name) in _CODEPOINTS:
        raise ValueError(f"{before_name} to {after_name} already has a codepoint")
    codepoint = _FIRST_CODEPOINT + len(_CODEPOINTS)
    _CODEPOINTS[(before_name, after_name)] = codepoint
    return codepoint


def spell(*codepoints):
    """The `codepoints` field of a window spec, written from the pairs the window is made of, so a spec names the changes it shows and never a hex string somebody has to keep in step with the cmaps."""
    return ":".join(f"{codepoint:04X}" for codepoint in codepoints)


register_glyph("before", "qsL", TWO_COLUMNS, 100)
register_glyph("before", "qsSee.ex-y0", GROUNDED_SEE, 250)
register_glyph("before", "qsSee.ex-y0.spare", GROUNDED_SEE, 250)
register_glyph("before", "qsSee.ex-y0.blank", (), 50)
register_glyph("before", "qsF1", TWO_COLUMNS, 50)
register_glyph("before", "qsF2", TWO_COLUMNS, 100)
register_glyph("before", "qsF3", TWO_COLUMNS, 100)
register_glyph("before", "qsF3.narrow", TWO_COLUMNS, 100)
register_glyph("before", "qsM", TWO_COLUMNS, 100)
register_glyph("before", "qsJ.ex-y0.ex-ext-1", EXTENDED_PIVOT, 100)
register_glyph("before", "qsJ.ex-y0.ex-ext-1.wide", WIDE_TAIL_PIVOT, 150)
register_glyph("before", "qsJ.ex-y0.ex-ext-1.crown", CROWNED_PIVOT, 100)
register_glyph("before", "qsJ.ex-y0.ex-ext-3.long", LONG_TAIL_PIVOT, 200)
register_glyph("before", "qsEt", EXTENDED_PIVOT, 100)
register_glyph("before", "qsRoe.en-ext-1-at-5", SHORTENED_ROE, 100)
register_glyph("before", "qsAt", TWO_COLUMNS, 100)
register_glyph("before", "qsIt", TWO_COLUMNS, 100)
register_glyph("before", "qsIt.ex-y5", TWO_COLUMNS, 100)
register_glyph("before", "qsEt.join", TWO_COLUMNS, 100)
register_glyph("before", "qsLow.en-ext-1", EXTENDED_ENTRY_LOW, 150)
register_glyph("before", "qsVie.en-ext-1", EXTENDED_ENTRY_LOW, 150)
register_glyph("before", "qsVie_qsUtter.en-ext-1", EXTENDED_ENTRY_LOW, 150)
register_glyph("before", "qsMay.en-y0.ex-y5.en-ext-1", EXTENDED_ENTRY_LOW, 150)
register_glyph("before", "qsMay.en-y0.ex-y5.contract-fixture", EXTENDED_ENTRY_LOW, 150)
register_glyph("before", "qsRoe.ex-y0.placed-contraction", PLACED_CONTRACTION_ROE, 150)
register_glyph("before", "qsMay.en-y0.ex-y5.unchanged-fixture", TWO_COLUMNS, 100)
register_glyph("before", "qsBay.contract-lead", TWO_COLUMNS, 100)
register_glyph("before", "qsFcovered.en-ext-1", OVERHANGING_FOLLOWER, 100)
register_glyph("before", "qsMay.en-y5", EXTENDED_ENTRY_LOW, 150)
register_glyph("before", "qsK", TWO_COLUMNS, 100)
register_glyph("before", "qsTea.half.ex-y5", (_rect(0, 100, 100, 150),), 100)
register_glyph("before", "qsTea.half.ex-y5.moving-fixture", (_rect(0, 100, 100, 150),), 100)
register_glyph("before", "qsPea.half.ex-y5", (_rect(0, 100, 100, 150),), 100)
register_glyph("before", "qsNo.en-ext-1", TWO_COLUMNS, 100)
register_glyph("before", "qsNo.en-ext-1.chain-fixture", TWO_COLUMNS, 200)
register_glyph("before", "qsNo.en-ext-1.extension-chain-fixture", TWO_COLUMNS, 150)
register_glyph("before", "qsEight", EIGHTISH, 100)
register_glyph("before", "qsEight.ex-ext-1", EIGHTISH_EXTENDED, 150)
register_glyph("before", "qsEight.en-ext-1", EIGHTISH_ENTRY_EXTENDED, 150)
register_glyph("before", "qsEight.smaller-loop", EIGHTISH_SMALLER, 100)
register_glyph("before", "qsTea.half.en-y5.after-xheight-exit", HALF_TEA_BAR, 50)
register_glyph("before", "qsKey", FOOTED_KEY, 100)
register_glyph("before", "space", (), 50)

register_glyph("after", "qsL", TWO_COLUMNS, 100)
register_glyph("after", "qsSee.straighter", STRAIGHTER_SEE, 200)
register_glyph("after", "qsSee.straighter.blank", (), 50)
register_glyph("after", "qsSee.spare", GROUNDED_SEE, 250)
register_glyph("after", "qsSee.wandered", TWO_COLUMNS, 250)
register_glyph("after", "qsF1", TWO_COLUMNS, 50)
register_glyph("after", "qsF2", TUCKED_FOLLOWER, 100)
register_glyph("after", "qsF3", TWO_COLUMNS, 100)
register_glyph("after", "qsF3.wider", TWO_COLUMNS, 150)
register_glyph("after", "qsM", TWO_COLUMNS, 100)
register_glyph("after", "qsJ.hapax.ex-y0", TRIMMED_PIVOT, 50)
register_glyph("after", "qsJ.hapax.ex-y0.ex-ext-1", EXTENDED_PIVOT, 100)
register_glyph("after", "qsEt.hapax", TRIMMED_PIVOT, 50)
register_glyph("after", "qsOther", TWO_COLUMNS, 100)
register_glyph("after", "qsRoe.hapax.en-y5.en-ext-1", KEPT_ROE, 100)
register_glyph("after", "qsRoe.hapax.shifted-gain", KEPT_ROE, 150)
register_glyph("after", "qsAt", TWO_COLUMNS, 150)
register_glyph("after", "qsIt", TWO_COLUMNS, 100)
register_glyph("after", "qsIt.hapax", TWO_COLUMNS, 150)
register_glyph("after", "qsEt.join", TWO_COLUMNS, 100)
register_glyph("after", "qsLow.hapax", TWO_COLUMNS, 100)
register_glyph("after", "qsVie.normal", TWO_COLUMNS, 100)
register_glyph("after", "qsVie_qsUtter.hapax", TWO_COLUMNS, 100)
register_glyph("after", "qsMay.loop", TWO_COLUMNS, 100)
register_glyph("after", "qsMay.loop.en-y0.en-con-1", CONTRACTED_ENTRY_MAY, 150)
register_glyph("after", "qsMay.loop.en-y0.ex-y5.en-con-1", CONTRACTED_ENTRY_MAY, 50)
register_glyph("after", "qsMay.loop.unchanged-fixture", TWO_COLUMNS, 100)
register_glyph("after", "qsBay.contract-lead", TWO_COLUMNS, 50)
register_glyph("after", "qsFcovered.hapax", TWO_COLUMNS, 100)
register_glyph("after", "qsK", TWO_COLUMNS, 150)
register_glyph("after", "qsTea", TWO_COLUMNS, 100)
register_glyph("after", "qsTea.moving-fixture", TWO_COLUMNS, 50)
register_glyph("after", "qsPea", TWO_COLUMNS, 100)
register_glyph("after", "qsNo", TRIMMED_PIVOT, 50)
register_glyph("after", "qsNo.chain-fixture", TRIMMED_PIVOT, 50)
register_glyph("after", "qsNo.extension-chain-fixture", TRIMMED_PIVOT, 50)
register_glyph("after", "qsEight.smaller-loop", EIGHTISH_SMALLER, 100)
register_glyph("after", "qsEight.smaller-loop.en-ext-1", EIGHTISH_ENTRY_EXTENDED_SMALLER, 150)
register_glyph("after", "qsEight.normal-sized-loop", EIGHTISH, 100)
register_glyph("after", "qsEight.pulled-loop.en-con-1", EIGHTISH_SMALLER, 100)
register_glyph("after", "qsTea.full.en-y5", FULL_TEA_BAR, 50)
register_glyph("after", "qsTea.full.en-y5.misaligned", MISALIGNED_FULL_TEA_BAR, 50)
register_glyph("after", "qsKey.hapax.ex-y0.ex-con-1", SHORTENED_FOOT_KEY, 50)
register_glyph("after", "qsRoe.hapax.en-y5.ex-y0.en-con-1", PLACED_CONTRACTION_ROE_PULLED, 150)
register_glyph("after", "space", (), 50)
register_glyph("before", "qsF3.reaching", TUCKED_FOLLOWER, 100)
register_glyph("after", "qsF3.reached", TWO_COLUMNS, 100)

LEAD = register_pair("qsL", "qsL")
SEE = register_pair("qsSee.ex-y0", "qsSee.straighter")
FOLLOWER_1 = register_pair("qsF1", "qsF1")
FOLLOWER_2 = register_pair("qsF2", "qsF2")
FOLLOWER_3 = register_pair("qsF3", "qsF3")
FOLLOWER_3_WIDENED = register_pair("qsF3.narrow", "qsF3.wider")
MIDDLE = register_pair("qsM", "qsM")
MARKER = register_pair("space", "space")
SEE_UNSETTLED = register_pair("qsSee.ex-y0", "qsOther")
SEE_SPARE = register_pair("qsSee.ex-y0.spare", "qsSee.spare")
SEE_WANDERED = register_pair("qsSee.ex-y0.spare", "qsSee.wandered")
SEE_BLANK = register_pair("qsSee.ex-y0.blank", "qsSee.straighter.blank")
PIVOT = register_pair("qsJ.ex-y0.ex-ext-1", "qsJ.hapax.ex-y0")
PIVOT_WIDE_TAIL = register_pair("qsJ.ex-y0.ex-ext-1.wide", "qsJ.hapax.ex-y0")
PIVOT_CROWNED = register_pair("qsJ.ex-y0.ex-ext-1.crown", "qsJ.hapax.ex-y0")
PIVOT_SHORTENED = register_pair("qsJ.ex-y0.ex-ext-3.long", "qsJ.hapax.ex-y0.ex-ext-1")
PIVOT_DROPPED_WHOLE = register_pair("qsJ.ex-y0.ex-ext-3.long", "qsJ.hapax.ex-y0")
PIVOT_CONTRACTED = register_pair("qsEt", "qsEt.hapax")
ROE = register_pair("qsRoe.en-ext-1-at-5", "qsRoe.hapax.en-y5.en-ext-1")
ROE_SHIFTED_GAIN = register_pair("qsRoe.en-ext-1-at-5", "qsRoe.hapax.shifted-gain")
AT = register_pair("qsAt", "qsAt")
IT = register_pair("qsIt", "qsIt")
IT_EXITING = register_pair("qsIt.ex-y5", "qsIt.hapax")
ET_JOIN = register_pair("qsEt.join", "qsEt.join")
LOW = register_pair("qsLow.en-ext-1", "qsLow.hapax")
TEA = register_pair("qsTea.half.ex-y5", "qsTea")
MOVING_TEA = register_pair("qsTea.half.ex-y5.moving-fixture", "qsTea.moving-fixture")
PEA = register_pair("qsPea.half.ex-y5", "qsPea")
NO = register_pair("qsNo.en-ext-1", "qsNo")
NO_CHAINED = register_pair("qsNo.en-ext-1.chain-fixture", "qsNo.chain-fixture")
NO_EXTENSION_CHAINED = register_pair("qsNo.en-ext-1.extension-chain-fixture", "qsNo.extension-chain-fixture")
VIE = register_pair("qsVie.en-ext-1", "qsVie.normal")
VIE_UTTER = register_pair("qsVie_qsUtter.en-ext-1", "qsVie_qsUtter.hapax")
MAY = register_pair("qsMay.en-y0.ex-y5.en-ext-1", "qsMay.loop")
CONTRACTED_MAY = register_pair("qsMay.en-y0.ex-y5.contract-fixture", "qsMay.loop.en-y0.en-con-1")
CONTRACTED_JOINING_MAY = register_pair(
    "qsMay.en-y0.ex-y5.contract-fixture", "qsMay.loop.en-y0.ex-y5.en-con-1"
)
UNCHANGED_MAY = register_pair("qsMay.en-y0.ex-y5.unchanged-fixture", "qsMay.loop.unchanged-fixture")
CONTRACTION_LEAD = register_pair("qsBay.contract-lead", "qsBay.contract-lead")
COVERED_FOLLOWER = register_pair("qsFcovered.en-ext-1", "qsFcovered.hapax")
LEFT_NEIGHBOR = register_pair("qsK", "qsK")
MAY_STUB = register_pair("qsMay.en-y5", "qsMay.loop")
EIGHT = register_pair("qsEight", "qsEight.smaller-loop")
EIGHT_EXTENDED = register_pair("qsEight.ex-ext-1", "qsEight.smaller-loop")
EIGHT_ENTRY_EXTENDED = register_pair("qsEight.en-ext-1", "qsEight.smaller-loop.en-ext-1")
EIGHT_UNCHANGED = register_pair("qsEight", "qsEight.normal-sized-loop")
EIGHT_REVERSED = register_pair("qsEight.smaller-loop", "qsEight.normal-sized-loop")
EIGHT_PULLED_IN = register_pair("qsEight", "qsEight.pulled-loop.en-con-1")
KEY = register_pair("qsKey", "qsKey.hapax.ex-y0.ex-con-1")
TEA_VERTICAL_GAIN = register_pair("qsTea.half.en-y5.after-xheight-exit", "qsTea.full.en-y5")
TEA_VERTICAL_GAIN_MISALIGNED = register_pair(
    "qsTea.half.en-y5.after-xheight-exit", "qsTea.full.en-y5.misaligned"
)
PLACED_CONTRACTION = register_pair("qsRoe.ex-y0.placed-contraction", "qsRoe.hapax.en-y5.ex-y0.en-con-1")
FOLLOWER_3_REACHING = register_pair("qsF3.reaching", "qsF3.reached")

BEFORE_GLYPHS = _OUTLINES["before"]
AFTER_GLYPHS = _OUTLINES["after"]
BEFORE_CMAP = {codepoint: names[0] for names, codepoint in _CODEPOINTS.items()}
AFTER_CMAP = {codepoint: names[1] for names, codepoint in _CODEPOINTS.items()}

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
    "after-low-unmoved": ({**AFTER_GLYPHS, "qsLow.hapax": (EXTENDED_ENTRY_LOW, 150)}, AFTER_CMAP),
    "after-low-unshifted": ({**AFTER_GLYPHS, "qsLow.hapax": (UNSHIFTED_ENTRY_LOW, 150)}, AFTER_CMAP),
    "after-low-extra-cell": ({**AFTER_GLYPHS, "qsLow.hapax": (EXTRA_CELL_LOW, 100)}, AFTER_CMAP),
    "after-contracted-entry-extra-cell": (
        {**AFTER_GLYPHS, "qsMay.loop.en-y0.en-con-1": (CONTRACTED_ENTRY_MAY_EXTRA, 150)},
        AFTER_CMAP,
    ),
    "after-contracted-entry-unmoved-follower": (
        {**AFTER_GLYPHS, "qsMay.loop.en-y0.en-con-1": (CONTRACTED_ENTRY_MAY, 200)},
        AFTER_CMAP,
    ),
    "after-contracted-entry-visible-follower-loss": (
        {**AFTER_GLYPHS, "qsFcovered.hapax": (TUCKED_FOLLOWER, 100)},
        AFTER_CMAP,
    ),
    "after-retarget-unmoved": ({**AFTER_GLYPHS, "qsNo": (TWO_COLUMNS, 100)}, AFTER_CMAP),
    "after-retarget-moved-origin": (
        {**AFTER_GLYPHS, "qsTea": (GROUNDED_SEE, 100)},
        AFTER_CMAP,
    ),
    "after-created-join-unmoved": (
        {**AFTER_GLYPHS, "qsJ.hapax.ex-y0.ex-ext-1": (EXTENDED_PIVOT, 200)},
        AFTER_CMAP,
    ),
    "after-created-join-follower-not-widened": (
        {**AFTER_GLYPHS, "qsF3.wider": (TWO_COLUMNS, 100)},
        AFTER_CMAP,
    ),
    "after-created-join-follower-not-reaching": (
        {**AFTER_GLYPHS, "qsF3.reached": (TUCKED_FOLLOWER, 100)},
        AFTER_CMAP,
    ),
    "after-created-join-widened-follower": (
        {**AFTER_GLYPHS, "qsTea": (TWO_COLUMNS, 150)},
        AFTER_CMAP,
    ),
    "after-chained-created-join-unmoved": (
        {**AFTER_GLYPHS, "qsNo.chain-fixture": (TRIMMED_PIVOT, 150)},
        AFTER_CMAP,
    ),
    "after-retarget-behind-created-join-unmoved": (
        {**AFTER_GLYPHS, "qsNo": (TRIMMED_PIVOT, 100)},
        AFTER_CMAP,
    ),
    "after-extension-behind-created-join-unmoved": (
        {**AFTER_GLYPHS, "qsJ.hapax.ex-y0": (TRIMMED_PIVOT, 100)},
        AFTER_CMAP,
    ),
    "after-stub-companion-pixel": (
        {**AFTER_GLYPHS, "qsMay.loop.unchanged-fixture": (TWO_COLUMNS_AND_A_PIXEL, 100)},
        AFTER_CMAP,
    ),
    "after-redrawn-extra-cell": (
        {**AFTER_GLYPHS, "qsEight.smaller-loop": (EIGHTISH_SMALLER_AND_A_PIXEL, 100)},
        AFTER_CMAP,
    ),
    "after-redrawn-unmoved-follower": (
        {**AFTER_GLYPHS, "qsEight.smaller-loop": (EIGHTISH_SMALLER, 150)},
        AFTER_CMAP,
    ),
    "after-key-crowned": (
        {**AFTER_GLYPHS, "qsKey.hapax.ex-y0.ex-con-1": (SHORTENED_FOOT_KEY_AND_A_CROWN, 50)},
        AFTER_CMAP,
    ),
    "after-placed-contraction-unmoved-pivot": (
        {**AFTER_GLYPHS, "qsBay.contract-lead": (TWO_COLUMNS, 100)},
        AFTER_CMAP,
    ),
    "after-redrawn-overpulled-pivot": (
        {**AFTER_GLYPHS, "qsBay.contract-lead": (TWO_COLUMNS, 0)},
        AFTER_CMAP,
    ),
}

FOUNDING_GLYPHS = ["qsL", "qsSee.ex-y0", "qsF1", "qsF2"]
FOUNDING_CODEPOINTS = spell(LEAD, SEE, FOLLOWER_1, FOLLOWER_2)


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


def test_a_glyph_drawn_twice_on_one_side_is_refused():
    """A second drawing of a name would quietly become the only one, taking every codepoint that already spells the first with it — which is exactly what a dict literal's duplicate key did in silence."""
    with pytest.raises(ValueError, match="already draws"):
        register_glyph("before", "qsL", TWO_COLUMNS, 100)


def test_a_pair_naming_a_glyph_no_font_draws_is_refused():
    with pytest.raises(ValueError, match="draws no qsNowhere"):
        register_pair("qsL", "qsNowhere")
    with pytest.raises(ValueError, match="draws no qsNowhere"):
        register_pair("qsNowhere", "qsL")


def test_a_pair_registered_twice_is_refused():
    with pytest.raises(ValueError, match="already has a codepoint"):
        register_pair("qsL", "qsL")


def test_every_drawn_glyph_is_spelled_by_some_codepoint():
    """An outline no codepoint reaches is ink the fonts carry and no window can ever ask about, so it is either a dead fixture or a pair somebody forgot to register."""
    for side, cmap in (("before", BEFORE_CMAP), ("after", AFTER_CMAP)):
        assert sorted(set(_OUTLINES[side]) - set(cmap.values())) == []


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
    assert not sv._matches(SLIDE_RULE["match"], slide_unit("s-7", ["qsL", "qsF1"], spell(LEAD, FOLLOWER_1)))
    lookalike = slide_unit("s-8", ["qsL", "qsSee.ex-y0x", "qsF1"], spell(LEAD, SEE, FOLLOWER_1))
    assert not sv._matches(SLIDE_RULE["match"], lookalike)


def test_a_matchable_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(SLIDE_RULE["match"], founding_window())


def test_a_pure_slide_matches(slide_context):
    window = slide_unit("s-9", ["qsL", "qsSee.ex-y0", "qsF1"], spell(LEAD, SEE, FOLLOWER_1))
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
    stranded = slide_unit("s-10", ["qsL", "qsSee.ex-y0"], spell(LEAD, SEE_UNSETTLED))
    assert not sv._matches(SLIDE_RULE["match"], stranded, context=slide_context())


def test_recorded_glyphs_disagreeing_with_the_shaped_run_defeat_the_match(slide_context):
    misrecorded = slide_unit("s-11", ["qsL", "qsSee.ex-y0", "qsF1", "qsF9"], FOUNDING_CODEPOINTS)
    assert not sv._matches(SLIDE_RULE["match"], misrecorded, context=slide_context())


def test_two_pivots_in_one_window_slide_cumulatively(slide_context):
    context = slide_context()
    twice = slide_unit(
        "s-12",
        ["qsL", "qsSee.ex-y0", "qsF1", "qsSee.ex-y0", "qsF1"],
        spell(LEAD, SEE, FOLLOWER_1, SEE, FOLLOWER_1),
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
GAIN_CODEPOINTS = spell(LEAD, ROE, FOLLOWER_1)
VERTICAL_GAIN_GLYPHS = ["qsL", "qsTea.half.en-y5.after-xheight-exit", "qsF1"]
VERTICAL_GAIN_CODEPOINTS = spell(LEAD, TEA_VERTICAL_GAIN, FOLLOWER_1)
MISALIGNED_VERTICAL_GAIN_CODEPOINTS = spell(LEAD, TEA_VERTICAL_GAIN_MISALIGNED, FOLLOWER_1)


def gain_window(uid="g-1"):
    return slide_unit(uid, GAIN_GLYPHS, GAIN_CODEPOINTS)


SHIFTED_GAIN_RULE = {
    "id": "fixture-shifted-ink-gain",
    "verdict": "approve",
    "note": "the fuller pivot carries its follower one column right",
    "match": {
        "before": {"pivots": ["qsRoe.en-ext-1-at-5"]},
        "after": {
            "pivots": ["qsRoe.hapax.shifted-gain"],
            "gained": [[1, 0]],
            "shift": 1,
        },
        "except_left": [],
    },
}


def shifted_gain_window(uid="sg-1"):
    return slide_unit(uid, GAIN_GLYPHS, spell(LEAD, ROE_SHIFTED_GAIN, FOLLOWER_1))


def vertical_gain_window(uid="vg-1", *, misaligned=False):
    codepoints = MISALIGNED_VERTICAL_GAIN_CODEPOINTS if misaligned else VERTICAL_GAIN_CODEPOINTS
    return slide_unit(uid, VERTICAL_GAIN_GLYPHS, codepoints)


def test_a_pure_gain_matches(slide_context):
    assert sv._matches(GAIN_RULE["match"], gain_window(), context=slide_context())


def test_a_gain_that_lengthens_the_pivot_moves_the_following_span(slide_context):
    assert sv._matches(SHIFTED_GAIN_RULE["match"], shifted_gain_window(), context=slide_context())


def test_a_gain_rule_with_the_wrong_following_shift_is_refused(slide_context):
    wrong = json.loads(json.dumps(SHIFTED_GAIN_RULE))
    wrong["match"]["after"]["shift"] = 0
    assert not sv._matches(wrong["match"], shifted_gain_window(), context=slide_context())


def test_a_vertical_frame_extension_that_keeps_the_old_pixels_matches(slide_context):
    assert sv._matches(VERTICAL_GAIN_RULE["match"], vertical_gain_window(), context=slide_context())


def test_a_vertical_frame_extension_that_moves_an_old_pixel_is_refused(slide_context):
    assert not sv._matches(
        VERTICAL_GAIN_RULE["match"],
        vertical_gain_window(misaligned=True),
        context=slide_context(),
    )


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
    assert not sv._matches(GAIN_RULE["match"], slide_unit("g-3", ["qsL", "qsF1"], spell(LEAD, FOLLOWER_1)))


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
    assert rule["match"]["after"] == {
        "pivots": ["qsRoe.hapax"],
        "gained": [[1, 0]],
        "shift": 0,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].update(pivots=[]),
        lambda rule: rule["match"]["before"].update(pivots="qsRoe.en-ext-1-at-5"),
        lambda rule: rule["match"]["after"].pop("shift"),
        lambda rule: rule["match"]["after"].update(shift=True),
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
JOIN_CODEPOINTS = spell(LEAD, AT, IT, FOLLOWER_1)


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


IT_ET_JOIN_GLYPHS = ["qsL", "qsIt.ex-y5", "qsEt.join", "qsF1"]
IT_ET_JOIN_CODEPOINTS = spell(LEAD, IT_EXITING, ET_JOIN, FOLLOWER_1)


def it_et_join_window(uid="je-1"):
    return unit(
        uid,
        list(IT_ET_JOIN_GLYPHS),
        ["y0", "y5", "y0"],
        ["qsL/full/None/None/", "qsIt/hapax/None/None/", "qsEt/hapax/None/None/", "qsF1/full/None/None/"],
        ["y0", "break", "y0"],
        codepoints=IT_ET_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def test_the_checked_in_it_et_rule_reads_the_gap_and_nothing_wider(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["it-et-xheight-join-dropped"]["match"]
    assert sv._matches(match, it_et_join_window(), context=slide_context())
    assert not sv._matches(match, join_window(), context=slide_context())
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
    other["codepoints"] = spell(LEAD, AT, FOLLOWER_1, FOLLOWER_1)
    assert not sv._matches(JOIN_RULE["match"], other, context=slide_context())


def test_a_join_rule_reads_no_unit_without_ink_deltas():
    bare = join_window()
    del bare["ink_deltas"]
    assert not sv._matches(JOIN_RULE["match"], bare)
    empty = join_window()
    empty["ink_deltas"] = {}
    assert not sv._matches(JOIN_RULE["match"], empty)


def test_a_window_with_no_join_pivot_is_refused_before_any_shaping():
    assert not sv._matches(JOIN_RULE["match"], slide_unit("j-3", ["qsL", "qsF1"], spell(LEAD, FOLLOWER_1)))


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
    assert not sv._matches(RETARGET_RULE["match"], join_window(), context=context)
    assert not sv._matches(JOIN_RULE["match"], retarget_window(), context=context)


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


def _invoke_main(tmp_path, monkeypatch, units, verdicts, rules_list=(RULE,), fonts=None, extra=()):
    """The CLI as the chain spawns it, returning both its exit code and the fills it wrote. `_run_main` is this with the code dropped, which is what every test predating --require-reach wants."""
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
            *extra,
        ],
    )
    code = sv.main()
    return code, json.loads(out.read_text())


def _run_main(tmp_path, monkeypatch, units, verdicts, rules_list=(RULE,), fonts=None, extra=()):
    _code, payload = _invoke_main(tmp_path, monkeypatch, units, verdicts, rules_list, fonts, extra)
    return payload


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
    assert record["note"].startswith(f"[standing: {RULE['id']}]")


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
    assert by_unit["u-1"]["note"].startswith(f"[standing: {RULE['id']}]")
    assert by_unit["u-2"]["note"].startswith(f"[standing: {EXT_RULE['id']}]")


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
    with pytest.raises(
        SystemExit,
        match=(
            "predates the ink-delta, slide, ink-gain, join-dropped, entry-extension-dropped, "
            "entry-contracted, stub-dropped, redrawn, join-retargeted, and join-created"
        ),
    ):
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


def out_window(uid="o-1"):
    """A two-letter window joining from ·Out that no rule in this file speaks for: the surface carrying a guarded family in a place the guard is never asked about, which is what a live except_left reading zero held looks like."""
    return unit(
        uid,
        ["qsOut.ex-y5", "qsMay"],
        ["y5"],
        ["qsOut/hapax/None/x-height/", "qsMay/loop/x-height/None/"],
        ["y5"],
    )


def test_the_per_rule_line_carries_the_tally(tmp_path, monkeypatch, capsys):
    units = [canonical("u-1"), canonical("u-2"), canonical("u-3", left="qsOut.ex-ext-1")]
    verdicts = [{"unit": "u-2", "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(tmp_path, monkeypatch, units, verdicts)
    lines = capsys.readouterr().out.splitlines()
    assert f"  {RULE['id']}: 1 filled, 1 already verdicted, 1 held for review by except_left" in lines


def test_the_rollup_reads_the_same_numbers_the_per_rule_line_does(tmp_path, monkeypatch, capsys):
    units = [canonical("u-1"), canonical("u-2"), canonical("u-3", left="qsOut.ex-ext-1")]
    verdicts = [{"unit": "u-2", "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(tmp_path, monkeypatch, units, verdicts)
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("  per-rule reach (") for line in lines)
    assert f"    {RULE['id']}: 3 on its own line, 0 credited across 0 composed lines, 3 in all" in lines


def test_a_rule_that_reached_nothing_says_so_and_the_run_still_writes(tmp_path, monkeypatch, capsys):
    payload = _run_main(tmp_path, monkeypatch, [canonical("u-1")], [], rules_list=(RULE, SHORTENED_RULE))
    assert [record["unit"] for record in payload["verdicts"]] == ["u-1"]
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith(f"  REACHED NOTHING: {SHORTENED_RULE['id']} ") for line in lines)
    assert not any(line.startswith(f"  REACHED NOTHING: {RULE['id']} ") for line in lines)


def test_a_matched_unit_verdicted_outside_the_blessed_set_trips_the_warning(tmp_path, monkeypatch, capsys):
    verdicts = [{"unit": "u-2", "verdict": "reject", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(tmp_path, monkeypatch, [canonical("u-1"), canonical("u-2")], verdicts)
    lines = capsys.readouterr().out.splitlines()
    [warning] = [line for line in lines if line.startswith("  WARNING:")]
    assert "a verdict outside approve/either/identical sits on 1 matched unit" in warning
    assert f"u-2 under {RULE['id']} (reject)" in warning


def test_the_warning_is_silent_when_every_matched_verdict_is_blessed(tmp_path, monkeypatch, capsys):
    verdicts = [{"unit": "u-2", "verdict": "either", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(tmp_path, monkeypatch, [canonical("u-1"), canonical("u-2")], verdicts)
    assert not any(line.startswith("  WARNING:") for line in capsys.readouterr().out.splitlines())


def test_an_identical_verdict_never_trips_the_warning(tmp_path, monkeypatch, capsys):
    """`identical` accepts the new rendering exactly as approve and either do — the reviewer merely found the highlighted portion unchanged — so a rule agreeing with one is no accident to report."""
    units = [canonical("u-1"), canonical("u-2")]
    verdicts = [{"unit": "u-2", "verdict": "identical", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(tmp_path / "full", monkeypatch, units, verdicts)
    assert not any(line.startswith("  WARNING:") for line in capsys.readouterr().out.splitlines())
    _run_main(tmp_path / "open", monkeypatch, units, verdicts, extra=("--open-only",))
    assert not any(line.startswith("  WARNING:") for line in capsys.readouterr().out.splitlines())


def test_open_only_writes_the_fills_the_whole_domain_writes(tmp_path, monkeypatch):
    """A fill comes only from a blank and every matcher decision is per-unit pure, so handing the run only the blanks and the disputed units cannot move a single record."""
    units = [
        canonical("u-1"),
        canonical("u-2"),
        canonical("u-3"),
        canonical("u-4", left="qsOut.ex-ext-1"),
        canonical("u-5"),
    ]
    verdicts = [
        {"unit": "u-2", "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"},
        {"unit": "u-3", "verdict": "skip", "note": "[parked]", "at": "2026-07-11T00:00:00Z"},
        {"unit": "u-5", "verdict": "reject", "note": "", "at": "2026-07-11T00:00:00Z"},
    ]
    whole = _run_main(tmp_path / "full", monkeypatch, units, verdicts)
    narrowed = _run_main(tmp_path / "open", monkeypatch, units, verdicts, extra=("--open-only",))
    assert whole == narrowed
    assert [record["unit"] for record in narrowed["verdicts"]] == ["u-1"]


def test_open_only_keeps_the_tripwire_word_for_word(tmp_path, monkeypatch, capsys):
    """Every unit the tripwire can name carries a verdict outside the accepting set, which is precisely what the narrowing keeps."""
    units = [canonical("u-1"), canonical("u-2")]
    verdicts = [{"unit": "u-2", "verdict": "reject", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(tmp_path / "full", monkeypatch, units, verdicts)
    [whole] = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  WARNING:")]
    _run_main(tmp_path / "open", monkeypatch, units, verdicts, extra=("--open-only",))
    [narrowed] = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  WARNING:")]
    assert narrowed == whole


def test_open_only_drops_the_already_verdicted_column_and_the_rollup(tmp_path, monkeypatch, capsys):
    """Both are readings of the store rather than of the fills, and a narrowed run has not read the store. `--require-reach` is how the cycle gets the rollup back — over the whole domain against a blank store, which is what reach means — and the tests below hold it to that."""
    units = [canonical("u-1"), canonical("u-2"), canonical("u-3", left="qsOut.ex-ext-1")]
    verdicts = [{"unit": "u-2", "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(
        tmp_path,
        monkeypatch,
        units,
        verdicts,
        rules_list=(RULE, SHORTENED_RULE),
        extra=("--open-only",),
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {RULE['id']}: 1 filled, 1 held for review by except_left" in lines
    assert not any("already verdicted" in line for line in lines)
    assert not any(line.startswith("  per-rule reach (") for line in lines)
    assert not any(line.startswith("  REACHED NOTHING:") for line in lines)


def test_require_reach_prints_the_rollup_a_narrowed_run_would_have_dropped(tmp_path, monkeypatch, capsys):
    """The narrowing drops the rollup because a narrowed run has read no store; `--require-reach` takes its own pass over the whole domain against a blank one and prints the rollup off that, so the cycle's form keeps both the cheap fills and the full reach reading. What stays dropped is the already-verdicted column, which is a reading of the real store and belongs to the run that made the fills."""
    units = [canonical("u-1"), canonical("u-2")]
    verdicts = [{"unit": "u-2", "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}]
    code, _payload = _invoke_main(
        tmp_path, monkeypatch, units, verdicts, extra=("--open-only", "--require-reach")
    )
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert any(line.startswith("  per-rule reach (") for line in lines)
    assert any(line.startswith(f"    {RULE['id']}: 2 on its own line,") for line in lines)
    assert not any("already verdicted" in line for line in lines)


def test_require_reach_counts_a_rule_whose_windows_are_all_verdicted_as_reaching(
    tmp_path, monkeypatch, capsys
):
    """Reach is a reading of the surface, not of the queue: a rule every one of whose windows a human has already judged has still reached them, and the blank store this judges against is what says so. The narrowed run itself sees none of those units, writes nothing, and must not be what the refusal reads."""
    units = [canonical("u-1")]
    verdicts = [{"unit": "u-1", "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}]
    code, payload = _invoke_main(
        tmp_path, monkeypatch, units, verdicts, extra=("--open-only", "--require-reach")
    )
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert payload["verdicts"] == []
    assert not any(line.startswith("  REACHED NOTHING:") for line in lines)
    assert any(line.startswith(f"    {RULE['id']}: 1 on its own line,") for line in lines)


def test_require_reach_refuses_when_a_rule_reaches_nothing(tmp_path, monkeypatch, capsys):
    """The refusal that holds the checked-in rules to the surface: a checked-in rule matching no window on this surface fails the step, so the plumbing goes red and `make verdict-ready` reads NOT READY. It is a refusal rather than a skip — the fills the reaching rules earned are written first and in full, because the run that produced them is correct and only the rules file is out of date."""
    units = [canonical("u-1"), canonical("u-2")]
    code, payload = _invoke_main(
        tmp_path,
        monkeypatch,
        units,
        [],
        rules_list=(RULE, SHORTENED_RULE),
        extra=("--open-only", "--require-reach"),
    )
    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert [record["unit"] for record in payload["verdicts"]] == ["u-1", "u-2"]
    assert any(line.startswith(f"  REACHED NOTHING: {SHORTENED_RULE['id']} ") for line in lines)
    assert any(
        line.startswith(f"  the plumbing refuses: {SHORTENED_RULE['id']} reached no window") for line in lines
    )
    assert not any(RULE["id"] in line for line in lines if line.startswith("  the plumbing refuses:"))


def test_without_require_reach_a_dead_rule_is_only_reported(tmp_path, monkeypatch, capsys):
    """The bare tool still prints REACHED NOTHING and returns 0, which is what a dry run wants: the author asking what a candidate rule reaches must not be handed a nonzero exit for a rule they have not landed yet."""
    code, _payload = _invoke_main(
        tmp_path, monkeypatch, [canonical("u-1")], [], rules_list=(RULE, SHORTENED_RULE)
    )
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert any(line.startswith(f"  REACHED NOTHING: {SHORTENED_RULE['id']} ") for line in lines)
    assert not any(line.startswith("  the plumbing refuses:") for line in lines)


def test_open_only_reads_the_except_left_vocabulary_off_the_whole_surface(tmp_path, monkeypatch, capsys):
    """Which families the surface's windows join from is a question about the surface, not about the queue, so the narrowing must not answer it off the blanks alone."""
    verdicts = [
        {"unit": uid, "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}
        for uid in ("u-1", "o-1")
    ]
    units = [canonical("u-1"), out_window("o-1")]
    _run_main(tmp_path / "joined", monkeypatch, units, verdicts, extra=("--open-only",))
    assert not any("except_left vocabulary" in line for line in capsys.readouterr().out.splitlines())
    _run_main(tmp_path / "bare", monkeypatch, [canonical("u-1")], verdicts[:1], extra=("--open-only",))
    lines = capsys.readouterr().out.splitlines()
    assert any(
        line.startswith(f"  except_left vocabulary: {RULE['id']} guards against qsOut,") for line in lines
    )


def test_open_only_refuses_explain(tmp_path, monkeypatch):
    """--explain's middle column names the ids a verdict already covers, which a narrowed run was never offered."""
    with pytest.raises(SystemExit):
        _run_main(
            tmp_path,
            monkeypatch,
            [canonical("u-1")],
            [],
            extra=("--explain", RULE["id"], "--open-only"),
        )


def test_an_except_left_family_no_window_joins_from_is_named(tmp_path, monkeypatch, capsys):
    _run_main(tmp_path, monkeypatch, [canonical("u-1")], [])
    lines = capsys.readouterr().out.splitlines()
    assert any(
        line.startswith(f"  except_left vocabulary: {RULE['id']} guards against qsOut,") for line in lines
    )


def test_a_guarded_family_the_surface_carries_but_never_holds_says_nothing(tmp_path, monkeypatch, capsys):
    _run_main(tmp_path, monkeypatch, [canonical("u-1"), out_window("o-1")], [])
    lines = capsys.readouterr().out.splitlines()
    assert f"  {RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert not any("except_left vocabulary" in line for line in lines)


def test_explain_splits_a_rules_matched_ids_into_three_columns(tmp_path, monkeypatch, capsys):
    units = [canonical("u-1"), canonical("u-2"), canonical("u-3", left="qsOut.ex-ext-1")]
    verdicts = [{"unit": "u-2", "verdict": "either", "note": "", "at": "2026-07-11T00:00:00Z"}]
    _run_main(tmp_path, monkeypatch, units, verdicts, extra=("--explain", RULE["id"]))
    lines = capsys.readouterr().out.splitlines()
    assert f"  explain {RULE['id']}:" in lines
    assert "    filled (1): u-1" in lines
    assert "    already verdicted (1): u-2 (either)" in lines
    assert "    held by except_left (1): u-3" in lines


def test_explain_on_a_caught_up_store_puts_the_whole_reach_in_the_verdicted_column(
    tmp_path, monkeypatch, capsys
):
    units = [canonical("u-1"), canonical("u-2")]
    verdicts = [
        {"unit": uid, "verdict": "approve", "note": "", "at": "2026-07-11T00:00:00Z"}
        for uid in ("u-1", "u-2")
    ]
    _run_main(tmp_path, monkeypatch, units, verdicts, extra=("--explain", RULE["id"]))
    lines = capsys.readouterr().out.splitlines()
    assert "    filled (0): none" in lines
    assert "    already verdicted (2): u-1 (approve) u-2 (approve)" in lines
    assert "    held by except_left (0): none" in lines


def test_an_unknown_explain_rule_is_refused(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="not a rule id"):
        _run_main(tmp_path, monkeypatch, [canonical("u-1")], [], extra=("--explain", "no-such-rule"))


COMPOSED_EXT_RULE = {
    "id": "fixture-composed-extension-dropped",
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
COMPOSED_CODEPOINTS = spell(LEAD, SEE, MIDDLE, PIVOT, FOLLOWER_3, FOLLOWER_1)


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


def test_open_only_names_the_composed_pair_without_the_column(tmp_path, monkeypatch, capsys, slide_fonts):
    """A composed reading claims a window on that window's own contents, so a narrowed run credits the same pair and only the middle column goes."""
    _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1")],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
        extra=("--open-only",),
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 0 filled, 0 held for review by except_left" in lines
    assert f"  {COMPOSED_EXT_RULE['id']}: 0 filled, 0 held for review by except_left" in lines
    assert (
        f"  {SLIDE_RULE['id']} + {COMPOSED_EXT_RULE['id']}: 1 filled, " "0 held for review by except_left"
    ) in lines


@pytest.mark.parametrize(
    "after",
    [
        "after-extra-prefix-pixel",
        "after-extra-middle-pixel",
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
    with pytest.raises(
        SystemExit,
        match=(
            "predates the ink-delta, slide, ink-gain, join-dropped, entry-extension-dropped, "
            "entry-contracted, stub-dropped, redrawn, join-retargeted, and join-created"
        ),
    ):
        _run_main(tmp_path, monkeypatch, [tea_i("u-1")], [], rules_list=(EXT_RULE, COMPOSED_EXT_RULE))


def extension_only_window(uid="e-1"):
    return slide_unit(
        uid,
        ["qsL", "qsJ.ex-y0.ex-ext-1", "qsF3"],
        spell(LEAD, PIVOT, FOLLOWER_3),
        pair={"left": 1, "right": 2},
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


def test_the_rollup_adds_a_rules_own_line_to_the_credit_composed_lines_gave_it(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1"), founding_window("s-1")],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"    {SLIDE_RULE['id']}: 1 on its own line, 1 credited across 1 composed line, 2 in all" in lines
    assert (
        f"    {COMPOSED_EXT_RULE['id']}: 0 on its own line, 1 credited across 1 composed line, 1 in all"
    ) in lines


def test_a_rule_that_only_ever_earned_composed_credit_does_not_read_as_dead(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    """The composed pass claims a window before any single rule is asked about it, so a rule whose whole reach is composed shows zero on its own line — the one number the reached-nothing line must not read as a rule that speaks for nothing."""
    _run_main(
        tmp_path,
        monkeypatch,
        [composed_window("c-1")],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"    {SLIDE_RULE['id']}: 0 on its own line, 1 credited across 1 composed line, 1 in all" in lines
    assert (
        f"    {COMPOSED_EXT_RULE['id']}: 0 on its own line, 1 credited across 1 composed line, 1 in all"
    ) in lines
    assert not any(line.startswith("  REACHED NOTHING:") for line in lines)


def test_the_rollup_reads_zero_composed_lines_when_nothing_composed(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    _run_main(
        tmp_path,
        monkeypatch,
        [founding_window("s-1"), extension_only_window("e-1")],
        [],
        rules_list=(SLIDE_RULE, COMPOSED_EXT_RULE),
        fonts=slide_fonts,
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {COMPOSED_EXT_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left"
    ) in lines
    assert (
        f"    {SLIDE_RULE['id']}: 1 on its own line, 0 credited across 0 composed lines, 1 in all"
    ) in lines


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
        slide_unit("s-7", ["qsL", "qsF1"], spell(LEAD, FOLLOWER_1)),
        slide_unit("s-8", ["qsL", "qsSee.ex-y0x", "qsF1"], spell(LEAD, SEE, FOLLOWER_1)),
        slide_unit("s-9", ["qsL", "qsSee.ex-y0", "qsF1"], spell(LEAD, SEE, FOLLOWER_1)),
        slide_unit("s-10", ["qsL", "qsSee.ex-y0"], spell(LEAD, SEE_UNSETTLED)),
        slide_unit("s-11", ["qsL", "qsSee.ex-y0", "qsF1", "qsF9"], FOUNDING_CODEPOINTS),
        slide_unit(
            "s-12",
            ["qsL", "qsSee.ex-y0", "qsF1", "qsSee.ex-y0", "qsF1"],
            spell(LEAD, SEE, FOLLOWER_1, SEE, FOLLOWER_1),
        ),
    ]


def test_a_failed_extension_candidate_is_judged_as_span_ink(slide_context):
    context = slide_context("after-unshortened-pivot")
    assert sv._candidates(COMPOSED_EXT_RULE["match"], composed_window()) == [3]
    assert sv._composed_walk(COMPOSABLE_RULES, composed_window(), context) == {SLIDE_RULE["id"]: [1]}
    assert sv._composed(COMPOSABLE_RULES, composed_window(), context) is None


def test_a_named_extension_follower_may_redraw(slide_context):
    context = slide_context("after-extra-tail-pixel")
    assert sv._composed(COMPOSABLE_RULES, composed_window(), context) == {
        SLIDE_RULE["id"]: [1],
        COMPOSED_EXT_RULE["id"]: [3],
    }


def test_dropped_entry_reads_the_lost_en_ext_and_nothing_else():
    assert (
        sv._dropped_entry("qsMay.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsMay/loop/baseline/x-height/ex-ext-2") == 1
    )
    assert sv._dropped_entry("qsMay.en-ext-1", "qsMay/loop/baseline/None/") == 1
    assert sv._dropped_entry("qsIt.en-y5.ex-y0.ex-ext-1", "qsIt/hapax/None/baseline/") == 0
    assert sv._dropped_entry("qsRoe.en-ext-1-at-5", "qsRoe/hapax/x-height/None/") == 0
    assert sv._dropped_entry("qsMay.en-ext-2", "qsMay/loop/baseline/None/en-ext-1") == 1


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
        spell(LEAD, SEE, MIDDLE, PIVOT_CROWNED, FOLLOWER_3),
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
    "id": "fixture-composed-extension-shortened",
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


def shortened_window(uid="c-short", pivot=PIVOT_SHORTENED):
    window = slide_unit(
        uid,
        ["qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-3.long", "qsF3", "qsF1"],
        spell(LEAD, SEE, MIDDLE, pivot, FOLLOWER_3, FOLLOWER_1),
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
    "id": "fixture-composed-exit-contracted",
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
        spell(LEAD, SEE, MIDDLE, PIVOT_CONTRACTED, FOLLOWER_3, FOLLOWER_1),
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
    whole = shortened_window("c-whole", PIVOT_DROPPED_WHOLE)
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
        spell(LEAD, SEE, MIDDLE, PIVOT_WIDE_TAIL, FOLLOWER_3),
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
        spell(MARKER, LEAD, SEE, MARKER, MIDDLE, PIVOT, FOLLOWER_3),
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
        spell(LEAD, SEE_BLANK, MIDDLE, PIVOT, FOLLOWER_3),
    )
    assert sv._candidates(SLIDE_RULE["match"], blanked) == [1]
    assert sv._composed_walk(COMPOSABLE_RULES, blanked, context) == {COMPOSED_EXT_RULE["id"]: [3]}
    assert sv._composed(COMPOSABLE_RULES, blanked, context) is None


def test_two_rules_claiming_one_position_refuse(slide_context):
    twin = json.loads(json.dumps(SLIDE_RULE))
    twin["id"] = SLIDE_RULE["id"] + "-again"
    assert sv._composed_walk([SLIDE_RULE, twin], founding_window(), slide_context()) is None


def test_an_extension_whose_follower_is_a_slide_pivot_composes(slide_context):
    chained = json.loads(json.dumps(COMPOSED_EXT_RULE))
    chained["match"]["before"]["follower"] = ["qsF3", "qsSee"]
    chained["match"]["after"]["follower_cells"] = ["qsF3/full/None/None/", "qsSee/full/None/None/"]
    window = slide_unit(
        "c-chain", ["qsL", "qsJ.ex-y0.ex-ext-1", "qsSee.ex-y0", "qsM"], spell(LEAD, PIVOT, SEE, MIDDLE)
    )
    assert sv._candidates(chained["match"], window) == [1]
    assert sv._candidates(SLIDE_RULE["match"], window) == [2]
    assert sv._composed([SLIDE_RULE, chained], window, slide_context()) == {
        SLIDE_RULE["id"]: [2],
        chained["id"]: [1],
    }


@pytest.mark.parametrize("family", ["qsL", "qsF3"])
def test_the_slide_guard_holds_the_whole_composed_window(slide_context, family):
    """·L is the slide pivot's own left neighbor, and ·F3 sits three letters past the pivot on the far side of the other credited rule: the slide shape's guard is window-scoped, so either one holds the whole unit."""
    rules = [guarded_rule(SLIDE_RULE, [family]), COMPOSED_EXT_RULE]
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
        spell(SEE_SPARE, LEAD, SEE, MIDDLE, PIVOT, FOLLOWER_3),
    )
    assert sv._candidates(SLIDE_RULE["match"], riding) == [0, 2]
    assert sv._composed(COMPOSABLE_RULES, riding, context) == {
        SLIDE_RULE["id"]: [2],
        COMPOSED_EXT_RULE["id"]: [4],
    }
    refusing = slide_unit(
        "c-refuse",
        ["qsSee.ex-y0.spare", "qsL", "qsSee.ex-y0", "qsM", "qsJ.ex-y0.ex-ext-1", "qsF3"],
        spell(SEE_WANDERED, LEAD, SEE, MIDDLE, PIVOT, FOLLOWER_3),
    )
    assert sv._candidates(SLIDE_RULE["match"], refusing) == [0, 2]
    assert sv._composed(COMPOSABLE_RULES, refusing, context) is None


COMPOSED_GAIN_GLYPHS = ["qsL", "qsSee.ex-y0", "qsRoe.en-ext-1-at-5", "qsF1"]
COMPOSED_GAIN_CODEPOINTS = spell(LEAD, SEE, ROE, FOLLOWER_1)
COMPOSED_GAIN_RULES = [SLIDE_RULE, GAIN_RULE]
COMPOSED_SHIFTED_GAIN_RULES = [SLIDE_RULE, SHIFTED_GAIN_RULE]


def composed_gain_window(uid="cg-1"):
    return slide_unit(uid, COMPOSED_GAIN_GLYPHS, COMPOSED_GAIN_CODEPOINTS)


def composed_shifted_gain_window(uid="csg-1"):
    return slide_unit(
        uid,
        COMPOSED_GAIN_GLYPHS,
        spell(LEAD, SEE, ROE_SHIFTED_GAIN, FOLLOWER_1),
    )


def test_a_slide_and_an_ink_gain_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_GAIN_RULES, composed_gain_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], GAIN_RULE["id"]: [2]}


def test_a_slide_and_a_shifted_ink_gain_carry_the_composed_displacement(slide_context):
    events = sv._composed(
        COMPOSED_SHIFTED_GAIN_RULES,
        composed_shifted_gain_window(),
        slide_context(),
    )
    assert events == {SLIDE_RULE["id"]: [1], SHIFTED_GAIN_RULE["id"]: [2]}


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
COMPOSED_JOIN_CODEPOINTS = spell(LEAD, SEE, AT, IT)
COMPOSED_JOIN_RULES = [SLIDE_RULE, JOIN_RULE]
EXT_JOIN_GLYPHS = ["qsL", "qsJ.ex-y0.ex-ext-1", "qsF3", "qsAt", "qsIt"]
EXT_JOIN_CODEPOINTS = spell(LEAD, PIVOT, FOLLOWER_3, AT, IT)
EXT_JOIN_RULES = [COMPOSED_EXT_RULE, JOIN_RULE]
JOIN_THEN_EXT_JOIN = json.loads(json.dumps(JOIN_RULE))
JOIN_THEN_EXT_JOIN["id"] = "fixture-join-dropped-into-extension"
JOIN_THEN_EXT_JOIN["match"]["before"]["follower"] = "qsJ"
JOIN_THEN_EXT_RULES = [JOIN_THEN_EXT_JOIN, COMPOSED_EXT_RULE]
JOIN_THEN_EXT_GLYPHS = ["qsL", "qsAt", "qsJ.ex-y0.ex-ext-1", "qsF3"]
JOIN_THEN_EXT_CODEPOINTS = spell(LEAD, AT, PIVOT, FOLLOWER_3)


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


def join_then_extension_window(uid="je-1"):
    return unit(
        uid,
        list(JOIN_THEN_EXT_GLYPHS),
        ["y0", "y5", "y0"],
        [
            "qsL/full/None/None/",
            "qsAt/full/None/None/",
            "qsJ/full/None/None/",
            "qsF3/full/None/None/",
        ],
        ["y0", "break", "y0"],
        codepoints=JOIN_THEN_EXT_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def test_a_join_drop_whose_follower_is_an_extension_pivot_composes(slide_context):
    events = sv._composed(JOIN_THEN_EXT_RULES, join_then_extension_window(), slide_context())
    assert events == {JOIN_THEN_EXT_JOIN["id"]: [1], COMPOSED_EXT_RULE["id"]: [2]}


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


ENTRY_GLYPHS = ["qsL", "qsLow.en-ext-1", "qsF1"]
ENTRY_CODEPOINTS = spell(LEAD, LOW, FOLLOWER_1)
COMPOSED_ENTRY_GLYPHS = ["qsL", "qsSee.ex-y0", "qsLow.en-ext-1", "qsF1"]
COMPOSED_ENTRY_CODEPOINTS = spell(LEAD, SEE, LOW, FOLLOWER_1)
COMPOSED_ENTRY_RULES = [SLIDE_RULE, ENTRY_RULE]
CONTRACTED_ENTRY_GLYPHS = ["qsBay.contract-lead", "qsMay.en-y0.ex-y5.contract-fixture", "qsF1"]
CONTRACTED_ENTRY_CODEPOINTS = spell(CONTRACTION_LEAD, CONTRACTED_MAY, FOLLOWER_1)
CONTRACTED_ENTRY_COVERED_GLYPHS = [
    "qsBay.contract-lead",
    "qsMay.en-y0.ex-y5.contract-fixture",
    "qsFcovered.en-ext-1",
]
CONTRACTED_ENTRY_COVERED_CODEPOINTS = spell(CONTRACTION_LEAD, CONTRACTED_MAY, COVERED_FOLLOWER)
COMPOSED_CONTRACTED_ENTRY_GLYPHS = [
    "qsL",
    "qsSee.ex-y0",
    "qsBay.contract-lead",
    "qsMay.en-y0.ex-y5.contract-fixture",
    "qsF1",
]
COMPOSED_CONTRACTED_ENTRY_CODEPOINTS = spell(LEAD, SEE, CONTRACTION_LEAD, CONTRACTED_MAY, FOLLOWER_1)
COMPOSED_CONTRACTED_ENTRY_COVERED_GLYPHS = [
    "qsL",
    "qsSee.ex-y0",
    "qsBay.contract-lead",
    "qsMay.en-y0.ex-y5.contract-fixture",
    "qsFcovered.en-ext-1",
]
COMPOSED_CONTRACTED_ENTRY_COVERED_CODEPOINTS = spell(
    LEAD,
    SEE,
    CONTRACTION_LEAD,
    CONTRACTED_MAY,
    COVERED_FOLLOWER,
)
COMPOSED_CONTRACTED_ENTRY_RULES = [SLIDE_RULE, CONTRACTED_ENTRY_RULE]
VERTICAL_GAIN_CONTRACTED_ENTRY_GLYPHS = [
    "qsTea.half.en-y5.after-xheight-exit",
    "qsBay.contract-lead",
    "qsMay.en-y0.ex-y5.contract-fixture",
]
VERTICAL_GAIN_CONTRACTED_ENTRY_CODEPOINTS = spell(TEA_VERTICAL_GAIN, CONTRACTION_LEAD, CONTRACTED_MAY)
VERTICAL_GAIN_CONTRACTED_ENTRY_RULES = [VERTICAL_GAIN_RULE, CONTRACTED_ENTRY_RULE]
DUPLICATE_MAY_BEFORE_GLYPHS = [
    "qsL",
    "qsMay.en-y0.ex-y5.unchanged-fixture",
    "qsBay.contract-lead",
    "qsMay.en-y0.ex-y5.contract-fixture",
    "qsF1",
]
DUPLICATE_MAY_BEFORE_CODEPOINTS = spell(LEAD, UNCHANGED_MAY, CONTRACTION_LEAD, CONTRACTED_MAY, FOLLOWER_1)
DUPLICATE_MAY_AFTER_GLYPHS = [
    "qsBay.contract-lead",
    "qsMay.en-y0.ex-y5.contract-fixture",
    "qsL",
    "qsMay.en-y0.ex-y5.unchanged-fixture",
    "qsF1",
]
DUPLICATE_MAY_AFTER_CODEPOINTS = spell(CONTRACTION_LEAD, CONTRACTED_MAY, LEAD, UNCHANGED_MAY, FOLLOWER_1)
PLACED_CONTRACTION_GLYPHS = ["qsBay.contract-lead", "qsRoe.ex-y0.placed-contraction", "qsF1"]
PLACED_CONTRACTION_CODEPOINTS = spell(CONTRACTION_LEAD, PLACED_CONTRACTION, FOLLOWER_1)
COMPOSED_PLACED_CONTRACTION_GLYPHS = [
    "qsL",
    "qsSee.ex-y0",
    "qsBay.contract-lead",
    "qsRoe.ex-y0.placed-contraction",
    "qsF1",
]
COMPOSED_PLACED_CONTRACTION_CODEPOINTS = spell(
    LEAD,
    SEE,
    CONTRACTION_LEAD,
    PLACED_CONTRACTION,
    FOLLOWER_1,
)
COMPOSED_PLACED_CONTRACTION_RULES = [SLIDE_RULE, PLACED_CONTRACTION_RULE]


def entry_window(uid="e-1"):
    return slide_unit(uid, ENTRY_GLYPHS, ENTRY_CODEPOINTS)


def composed_entry_window(uid="ce-1"):
    return slide_unit(uid, COMPOSED_ENTRY_GLYPHS, COMPOSED_ENTRY_CODEPOINTS)


def contracted_entry_window(uid="ec-1"):
    window = slide_unit(uid, CONTRACTED_ENTRY_GLYPHS, CONTRACTED_ENTRY_CODEPOINTS)
    window["after"]["cells"][1] = "qsMay/loop/baseline/None/en-con-1"
    return window


def contracted_entry_covered_window(uid="ec-covered-1"):
    window = slide_unit(uid, CONTRACTED_ENTRY_COVERED_GLYPHS, CONTRACTED_ENTRY_COVERED_CODEPOINTS)
    window["after"]["cells"][1] = "qsMay/loop/baseline/None/en-con-1"
    return window


def composed_contracted_entry_window(uid="cec-1"):
    window = slide_unit(
        uid,
        COMPOSED_CONTRACTED_ENTRY_GLYPHS,
        COMPOSED_CONTRACTED_ENTRY_CODEPOINTS,
    )
    window["after"]["cells"][3] = "qsMay/loop/baseline/None/en-con-1"
    return window


def composed_contracted_entry_covered_window(uid="cec-covered-1"):
    window = slide_unit(
        uid,
        COMPOSED_CONTRACTED_ENTRY_COVERED_GLYPHS,
        COMPOSED_CONTRACTED_ENTRY_COVERED_CODEPOINTS,
    )
    window["after"]["cells"][3] = "qsMay/loop/baseline/None/en-con-1"
    return window


def placed_contraction_window(uid="pc-1"):
    window = slide_unit(uid, PLACED_CONTRACTION_GLYPHS, PLACED_CONTRACTION_CODEPOINTS)
    window["after"]["cells"][1] = "qsRoe/hapax/x-height/baseline/en-con-1"
    return window


def composed_placed_contraction_window(uid="cpc-1"):
    window = slide_unit(
        uid,
        COMPOSED_PLACED_CONTRACTION_GLYPHS,
        COMPOSED_PLACED_CONTRACTION_CODEPOINTS,
    )
    window["after"]["cells"][3] = "qsRoe/hapax/x-height/baseline/en-con-1"
    return window


def vertical_gain_contracted_entry_window(uid="vgec-1"):
    window = slide_unit(
        uid,
        VERTICAL_GAIN_CONTRACTED_ENTRY_GLYPHS,
        VERTICAL_GAIN_CONTRACTED_ENTRY_CODEPOINTS,
    )
    window["after"]["cells"][2] = "qsMay/loop/baseline/None/en-con-1"
    return window


def duplicate_may_before_window(uid="ec-before-1"):
    window = slide_unit(uid, DUPLICATE_MAY_BEFORE_GLYPHS, DUPLICATE_MAY_BEFORE_CODEPOINTS)
    window["after"]["cells"][3] = "qsMay/loop/baseline/None/en-con-1"
    return window


def duplicate_may_after_window(uid="ec-after-1"):
    window = slide_unit(uid, DUPLICATE_MAY_AFTER_GLYPHS, DUPLICATE_MAY_AFTER_CODEPOINTS)
    window["after"]["cells"][1] = "qsMay/loop/baseline/None/en-con-1"
    return window


def test_a_pure_entry_drop_matches(slide_context):
    assert sv._matches(ENTRY_RULE["match"], entry_window(), context=slide_context())


def test_a_pure_entry_contraction_matches(slide_context):
    assert sv._matches(CONTRACTED_ENTRY_RULE["match"], contracted_entry_window(), context=slide_context())


def test_a_union_invisible_suffix_respelling_rides_with_an_entry_contraction(slide_context):
    assert sv._matches(
        CONTRACTED_ENTRY_RULE["match"],
        contracted_entry_covered_window(),
        context=slide_context(),
    )


def test_a_visible_suffix_loss_still_defeats_an_entry_contraction(slide_context):
    assert not sv._matches(
        CONTRACTED_ENTRY_RULE["match"],
        contracted_entry_covered_window(),
        context=slide_context("after-contracted-entry-visible-follower-loss"),
    )


def test_an_entry_contraction_can_name_multiple_left_families(slide_context):
    rule = json.loads(json.dumps(CONTRACTED_ENTRY_RULE))
    rule["match"]["before"]["left"] = ["qsKey", "qsBay"]
    assert sv._matches(rule["match"], contracted_entry_window(), context=slide_context())

    rule["match"]["before"]["left"] = ["qsKey", "qsNo"]
    assert not sv._matches(rule["match"], contracted_entry_window(), context=slide_context())


def test_a_multi_left_entry_contraction_composes(slide_context):
    rule = json.loads(json.dumps(CONTRACTED_ENTRY_RULE))
    rule["match"]["before"]["left"] = ["qsKey", "qsBay"]
    events = sv._composed(
        [SLIDE_RULE, rule],
        composed_contracted_entry_window(),
        slide_context(),
    )
    assert events == {SLIDE_RULE["id"]: [1], rule["id"]: [3]}


def test_the_checked_in_bay_may_rule_reads_the_contraction(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["bay-may-entry-contracted"]["match"]
    assert sv._matches(match, contracted_entry_window(), context=slide_context())
    assert not sv._matches(match, entry_window(), context=slide_context())


@pytest.mark.parametrize("window", [duplicate_may_before_window, duplicate_may_after_window])
def test_the_bay_may_contraction_ignores_an_unchanged_may_elsewhere(slide_context, window):
    assert sv._matches(CONTRACTED_ENTRY_RULE["match"], window(), context=slide_context())


def test_an_unrelated_change_beside_the_unchanged_may_still_refuses(slide_context):
    assert not sv._matches(
        CONTRACTED_ENTRY_RULE["match"],
        duplicate_may_before_window(),
        context=slide_context("after-extra-prefix-pixel"),
    )


def test_an_extra_pixel_defeats_the_entry_contraction(slide_context):
    assert not sv._matches(
        CONTRACTED_ENTRY_RULE["match"],
        contracted_entry_window(),
        context=slide_context("after-contracted-entry-extra-cell"),
    )


def test_an_unmoved_follower_defeats_the_entry_contraction(slide_context):
    assert not sv._matches(
        CONTRACTED_ENTRY_RULE["match"],
        contracted_entry_window(),
        context=slide_context("after-contracted-entry-unmoved-follower"),
    )


def test_a_slide_and_an_entry_contraction_in_one_window_compose(slide_context):
    events = sv._composed(
        COMPOSED_CONTRACTED_ENTRY_RULES,
        composed_contracted_entry_window(),
        slide_context(),
    )
    assert events == {SLIDE_RULE["id"]: [1], CONTRACTED_ENTRY_RULE["id"]: [3]}


def test_a_union_invisible_suffix_respelling_rides_in_a_composed_entry_contraction(slide_context):
    events = sv._composed(
        COMPOSED_CONTRACTED_ENTRY_RULES,
        composed_contracted_entry_covered_window(),
        slide_context(),
    )
    assert events == {SLIDE_RULE["id"]: [1], CONTRACTED_ENTRY_RULE["id"]: [3]}


def test_a_contraction_the_placement_carries_matches(slide_context):
    assert sv._matches(
        PLACED_CONTRACTION_RULE["match"],
        placed_contraction_window(),
        context=slide_context(),
    )


def test_an_unmoved_pivot_defeats_a_contraction_the_placement_carries(slide_context):
    assert not sv._matches(
        PLACED_CONTRACTION_RULE["match"],
        placed_contraction_window(),
        context=slide_context("after-placed-contraction-unmoved-pivot"),
    )


def test_a_slide_and_a_placement_carried_contraction_in_one_window_compose(slide_context):
    events = sv._composed(
        COMPOSED_PLACED_CONTRACTION_RULES,
        composed_placed_contraction_window(),
        slide_context(),
    )
    assert events == {SLIDE_RULE["id"]: [1], PLACED_CONTRACTION_RULE["id"]: [3]}


def test_a_vertical_gain_and_an_entry_contraction_in_one_window_compose(slide_context):
    events = sv._composed(
        VERTICAL_GAIN_CONTRACTED_ENTRY_RULES,
        vertical_gain_contracted_entry_window(),
        slide_context(),
    )
    assert events == {VERTICAL_GAIN_RULE["id"]: [0], CONTRACTED_ENTRY_RULE["id"]: [2]}


def test_the_checked_in_see_low_rule_reads_the_drop(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["see-low-entry-extension-dropped"]["match"]
    assert sv._matches(match, entry_window(), context=slide_context())
    assert not sv._matches(match, founding_window(), context=slide_context())


VIE_GLYPHS = ["qsL", "qsVie.en-ext-1", "qsF1"]
VIE_CODEPOINTS = spell(LEAD, VIE, FOLLOWER_1)
VIE_UTTER_GLYPHS = ["qsL", "qsVie_qsUtter.en-ext-1", "qsF1"]
VIE_UTTER_CODEPOINTS = spell(LEAD, VIE_UTTER, FOLLOWER_1)


def vie_window(uid="v-1"):
    return slide_unit(uid, VIE_GLYPHS, VIE_CODEPOINTS)


def vie_utter_window(uid="vu-1"):
    return slide_unit(uid, VIE_UTTER_GLYPHS, VIE_UTTER_CODEPOINTS)


def test_the_checked_in_vie_rule_reads_the_drop_and_nothing_wider(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["vie-entry-extension-dropped"]["match"]
    assert sv._matches(match, vie_window(), context=slide_context())
    assert not sv._matches(match, entry_window(), context=slide_context())
    assert not sv._matches(match, vie_utter_window(), context=slide_context())
    assert not sv._matches(match, founding_window(), context=slide_context())


MAY_GLYPHS = ["qsL", "qsMay.en-y0.ex-y5.en-ext-1", "qsF1"]
MAY_CODEPOINTS = spell(LEAD, MAY, FOLLOWER_1)


def may_window(uid="m-1"):
    return slide_unit(uid, MAY_GLYPHS, MAY_CODEPOINTS)


def test_the_checked_in_may_rule_reads_the_drop_and_nothing_wider(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["may-entry-extension-dropped"]["match"]
    assert sv._matches(match, may_window(), context=slide_context())
    assert not sv._matches(match, vie_window(), context=slide_context())
    assert not sv._matches(match, entry_window(), context=slide_context())
    assert not sv._matches(match, founding_window(), context=slide_context())


STUB_GLYPHS = ["qsK", "qsMay.en-y5", "qsF1"]
STUB_CODEPOINTS = spell(LEFT_NEIGHBOR, MAY_STUB, FOLLOWER_1)


def stub_window(uid="st-1"):
    return slide_unit(uid, STUB_GLYPHS, STUB_CODEPOINTS)


STUB_COMPANION_GLYPHS = ["qsMay.en-y0.ex-y5.unchanged-fixture", "qsK", "qsMay.en-y5", "qsF1"]
STUB_COMPANION_CODEPOINTS = spell(UNCHANGED_MAY, LEFT_NEIGHBOR, MAY_STUB, FOLLOWER_1)


def stub_companion_window(uid="st-2"):
    return slide_unit(uid, STUB_COMPANION_GLYPHS, STUB_COMPANION_CODEPOINTS)


def test_a_pure_stub_drop_matches(slide_context):
    assert sv._matches(STUB_RULE["match"], stub_window(), context=slide_context())


def test_a_second_may_keeping_its_form_rides_as_span_ink(slide_context):
    window = stub_companion_window()
    context = slide_context()
    assert sv._matches(STUB_RULE["match"], window, context=context)
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["may-entry-stub-dropped"]["match"]
    assert sv._matches(match, window, context=context)


def test_a_second_may_that_gains_a_pixel_defeats_the_stub_drop_match(slide_context):
    assert not sv._matches(
        STUB_RULE["match"],
        stub_companion_window(),
        context=slide_context("after-stub-companion-pixel"),
    )


def test_the_checked_in_may_stub_rule_reads_the_drop_and_nothing_wider(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["may-entry-stub-dropped"]["match"]
    assert sv._matches(match, stub_window(), context=slide_context())
    assert not sv._matches(match, may_window(), context=slide_context())
    assert not sv._matches(match, entry_window(), context=slide_context())
    assert not sv._matches(match, founding_window(), context=slide_context())


def test_an_entry_drop_is_not_a_stub_drop(slide_context):
    assert not sv._matches(STUB_RULE["match"], entry_window(), context=slide_context())
    assert not sv._matches(ENTRY_RULE["match"], stub_window(), context=slide_context())


COMPOSED_STUB_GLYPHS = ["qsRoe.en-ext-1-at-5", "qsK", "qsMay.en-y5"]
COMPOSED_STUB_CODEPOINTS = spell(ROE, LEFT_NEIGHBOR, MAY_STUB)
COMPOSED_STUB_RULES = [GAIN_RULE, STUB_RULE]


def composed_stub_window(uid="cs-1"):
    return slide_unit(uid, COMPOSED_STUB_GLYPHS, COMPOSED_STUB_CODEPOINTS)


def test_a_gain_and_a_stub_drop_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_STUB_RULES, composed_stub_window(), slide_context())
    assert events == {GAIN_RULE["id"]: [0], STUB_RULE["id"]: [2]}


def test_the_stub_guard_holds_the_whole_composed_window(slide_context):
    """·Roe opens the window two letters before the stub pivot, so only a window-scoped guard reaches it — the stub shape's is, exactly as its single-rule matcher's is."""
    rules = [GAIN_RULE, guarded_rule(STUB_RULE, ["qsRoe"])]
    window = composed_stub_window()
    context = slide_context()
    events = sv._composed(rules, window, context)
    assert events is not None
    assert sv._composed_held(rules, window, events, context)


def test_the_checked_in_vie_utter_rule_reads_the_drop_and_nothing_wider(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["vie-utter-entry-extension-dropped"][
        "match"
    ]
    assert sv._matches(match, vie_utter_window(), context=slide_context())
    assert not sv._matches(match, vie_window(), context=slide_context())
    assert not sv._matches(match, entry_window(), context=slide_context())


def test_an_unmoved_pivot_defeats_the_entry_drop_match(slide_context):
    assert not sv._matches(ENTRY_RULE["match"], entry_window(), context=slide_context("after-low-unmoved"))


def test_an_unshifted_remainder_defeats_the_entry_drop_match(slide_context):
    assert not sv._matches(ENTRY_RULE["match"], entry_window(), context=slide_context("after-low-unshifted"))


def test_an_unnamed_extra_cell_defeats_the_entry_drop_match(slide_context):
    assert not sv._matches(ENTRY_RULE["match"], entry_window(), context=slide_context("after-low-extra-cell"))


def test_one_extra_pixel_beside_the_entry_drop_defeats_the_match(slide_context):
    assert not sv._matches(
        ENTRY_RULE["match"], entry_window(), context=slide_context("after-extra-prefix-pixel")
    )


def test_an_entry_drop_rule_reads_no_unit_without_ink_deltas():
    assert not sv._matches(ENTRY_RULE["match"], slide_unit("e-2", ENTRY_GLYPHS, ENTRY_CODEPOINTS, deltas={}))
    bare = entry_window()
    del bare["ink_deltas"]
    assert not sv._matches(ENTRY_RULE["match"], bare)


def test_a_window_with_no_entry_pivot_prefix_glyph_is_refused_before_any_shaping():
    assert not sv._matches(ENTRY_RULE["match"], slide_unit("e-3", ["qsL", "qsF1"], spell(LEAD, FOLLOWER_1)))


def test_a_matchable_entry_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(ENTRY_RULE["match"], entry_window())


def test_except_left_holds_the_guarded_family_on_the_entry_drop_shape(slide_context):
    context = slide_context()
    assert not sv._matches(guarding(ENTRY_RULE, ["qsL"]), entry_window(), context=context)
    assert sv._matches(guarding(ENTRY_RULE, ["qsL"]), entry_window(), guard=False, context=context)


def test_the_entry_drop_shape_and_the_other_shapes_do_not_read_each_others_units(slide_context):
    context = slide_context()
    assert not sv._matches(ENTRY_RULE["match"], founding_window(), context=context)
    assert not sv._matches(ENTRY_RULE["match"], canonical(), context=context)
    assert not sv._matches(SLIDE_RULE["match"], entry_window(), context=context)
    assert not sv._matches(GAIN_RULE["match"], entry_window(), context=context)
    assert not sv._matches(INK_RULE["match"], entry_window())
    assert not sv._matches(RETARGET_RULE["match"], entry_window(), context=context)
    assert not sv._matches(ENTRY_RULE["match"], retarget_window(), context=context)


def test_an_entry_drop_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [ENTRY_RULE]))
    assert rule["match"]["before"] == {"pivots": ["qsLow.en-ext-1"]}
    assert rule["match"]["after"] == {"pivots": ["qsLow.hapax"], "entry_drop": 1}


def test_an_entry_contraction_rule_loads_multiple_left_families(tmp_path):
    rule = json.loads(json.dumps(CONTRACTED_ENTRY_RULE))
    rule["match"]["before"]["left"] = ["qsBay", "qsKey"]
    [loaded] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))
    assert loaded["match"]["before"]["left"] == ["qsBay", "qsKey"]


@pytest.mark.parametrize("left", [["qsBay", "qsKey.alt"], ["qsBay", "qsKey/hapax/None/None/"]])
def test_an_entry_contraction_rule_refuses_nonfamily_left_names(tmp_path, left):
    rule = json.loads(json.dumps(CONTRACTED_ENTRY_RULE))
    rule["match"]["before"]["left"] = left
    with pytest.raises(SystemExit, match="bare Quikscript family"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_stub_drop_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [STUB_RULE]))
    assert rule["match"]["before"] == {"pivots": ["qsMay.en-y5"]}
    assert rule["match"]["after"] == {"pivots": ["qsMay.loop"], "stub_drop": 1}


def test_a_matchable_stub_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(STUB_RULE["match"], stub_window())


def test_a_stub_drop_rule_reads_no_unit_without_ink_deltas():
    assert not sv._matches(STUB_RULE["match"], slide_unit("st-2", STUB_GLYPHS, STUB_CODEPOINTS, deltas={}))
    bare = stub_window()
    del bare["ink_deltas"]
    assert not sv._matches(STUB_RULE["match"], bare)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].update(pivots=[]),
        lambda rule: rule["match"]["before"].update(pivots="qsLow.en-ext-1"),
        lambda rule: rule["match"]["after"].update(entry_drop="1"),
        lambda rule: rule["match"]["after"].update(pivots=["qsLow/hapax/baseline/None/"]),
        lambda rule: rule["match"].update(except_left="qsL"),
    ],
)
def test_malformed_entry_drop_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(ENTRY_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_an_entry_drop_that_moves_nothing_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(ENTRY_RULE))
    rule["match"]["after"]["entry_drop"] = 0
    with pytest.raises(SystemExit, match="machine-approved already"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_widening_entry_drop_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(ENTRY_RULE))
    rule["match"]["after"]["entry_drop"] = -1
    with pytest.raises(SystemExit, match="closer together"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_entry_drop_pivot_lists_spanning_two_families_are_refused_at_load(tmp_path):
    within = json.loads(json.dumps(ENTRY_RULE))
    within["match"]["after"]["pivots"] = ["qsLow.hapax", "qsSee.hapax"]
    with pytest.raises(SystemExit, match="speaks for one letter"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [within]))
    across = json.loads(json.dumps(ENTRY_RULE))
    across["match"]["before"]["pivots"] = ["qsSee.en-ext-1"]
    with pytest.raises(SystemExit, match="speaks for one letter"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [across]))


def test_a_slide_and_an_entry_drop_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_ENTRY_RULES, composed_entry_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], ENTRY_RULE["id"]: [2]}


def test_a_pure_entry_drop_is_not_composed(slide_context):
    assert sv._composed(COMPOSED_ENTRY_RULES, entry_window(), slide_context()) is None
    assert sv._matches(ENTRY_RULE["match"], entry_window(), context=slide_context())


def test_main_writes_one_composed_entry_record_and_leaves_the_per_rule_lines(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_entry_window("ce-1")],
        [],
        rules_list=(SLIDE_RULE, ENTRY_RULE),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["ce-1"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "approve"
    assert record["note"] == (
        f"[standing: {SLIDE_RULE['id']} + {ENTRY_RULE['id']}] " f"{SLIDE_RULE['note']}; {ENTRY_RULE['note']}"
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (f"  {ENTRY_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left") in lines
    assert (
        f"  {SLIDE_RULE['id']} + {ENTRY_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


def test_one_extra_pixel_defeats_the_composed_entry_reading(slide_context):
    assert (
        sv._composed(COMPOSED_ENTRY_RULES, composed_entry_window(), slide_context("after-extra-prefix-pixel"))
        is None
    )


def test_the_entry_drop_guard_holds_the_whole_composed_window(slide_context):
    events = sv._composed(COMPOSED_ENTRY_RULES, composed_entry_window(), slide_context())
    assert sv._composed_held(
        [guarded_rule(ENTRY_RULE, ["qsL"]), SLIDE_RULE],
        composed_entry_window(),
        events,
        slide_context(),
    )


def test_main_fills_a_single_entry_window_under_that_shapes_own_line(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [entry_window("e-1"), composed_entry_window("ce-1")],
        [],
        rules_list=(SLIDE_RULE, ENTRY_RULE),
        fonts=slide_fonts,
    )
    by_unit = {record["unit"]: record for record in payload["verdicts"]}
    assert set(by_unit) == {"e-1", "ce-1"}
    assert by_unit["e-1"]["note"] == f"[standing: {ENTRY_RULE['id']}] {ENTRY_RULE['note']}"
    assert by_unit["ce-1"]["note"].startswith(f"[standing: {SLIDE_RULE['id']} + {ENTRY_RULE['id']}]")
    lines = capsys.readouterr().out.splitlines()
    assert f"  {ENTRY_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {SLIDE_RULE['id']} + {ENTRY_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


RETARGET_GLYPHS = ["qsL", "qsTea.half.ex-y5", "qsNo.en-ext-1", "qsF1"]
RETARGET_CODEPOINTS = spell(LEAD, TEA, NO, FOLLOWER_1)
COMPOSED_RETARGET_GLYPHS = ["qsL", "qsSee.ex-y0", "qsTea.half.ex-y5", "qsNo.en-ext-1"]
COMPOSED_RETARGET_CODEPOINTS = spell(LEAD, SEE, TEA, NO)
COMPOSED_RETARGET_RULES = [SLIDE_RULE, RETARGET_RULE]
CREATED_JOIN_GLYPHS = ["qsL", "qsJ.ex-y0.ex-ext-3.long", "qsF3"]
CREATED_JOIN_CODEPOINTS = spell(LEAD, PIVOT_SHORTENED, FOLLOWER_3)
COMPOSED_CREATED_JOIN_GLYPHS = ["qsL", "qsSee.ex-y0", "qsJ.ex-y0.ex-ext-3.long", "qsF3"]
COMPOSED_CREATED_JOIN_CODEPOINTS = spell(LEAD, SEE, PIVOT_SHORTENED, FOLLOWER_3)
COMPOSED_CREATED_JOIN_RULES = [SLIDE_RULE, CREATED_JOIN_RULE]
WIDENED_CREATED_JOIN_GLYPHS = ["qsL", "qsJ.ex-y0.ex-ext-3.long", "qsF3.narrow", "qsF1"]
WIDENED_CREATED_JOIN_CODEPOINTS = spell(LEAD, PIVOT_SHORTENED, FOLLOWER_3_WIDENED, FOLLOWER_1)
COMPOSED_WIDENED_JOIN_GLYPHS = [
    "qsL",
    "qsSee.ex-y0",
    "qsJ.ex-y0.ex-ext-3.long",
    "qsF3.narrow",
    "qsF1",
]
COMPOSED_WIDENED_JOIN_CODEPOINTS = spell(LEAD, SEE, PIVOT_SHORTENED, FOLLOWER_3_WIDENED, FOLLOWER_1)
COMPOSED_WIDENED_JOIN_RULES = [SLIDE_RULE, WIDENED_CREATED_JOIN_RULE]
REACHING_CREATED_JOIN_GLYPHS = ["qsL", "qsJ.ex-y0.ex-ext-3.long", "qsF3.reaching", "qsF1"]
REACHING_CREATED_JOIN_CODEPOINTS = spell(LEAD, PIVOT_SHORTENED, FOLLOWER_3_REACHING, FOLLOWER_1)
COMPOSED_REACHING_JOIN_GLYPHS = [
    "qsL",
    "qsSee.ex-y0",
    "qsJ.ex-y0.ex-ext-3.long",
    "qsF3.reaching",
    "qsF1",
]
COMPOSED_REACHING_JOIN_CODEPOINTS = spell(LEAD, SEE, PIVOT_SHORTENED, FOLLOWER_3_REACHING, FOLLOWER_1)
COMPOSED_REACHING_JOIN_RULES = [SLIDE_RULE, REACHING_CREATED_JOIN_RULE]
RETARGET_BEHIND_WIDENED_JOIN_RULES = [RETARGET_RULE, RETARGET_BEHIND_WIDENED_JOIN_RULE]
CONTRACTED_CREATED_JOIN_GLYPHS = ["qsBay.contract-lead", "qsMay.en-y0.ex-y5.contract-fixture", "qsF3"]
CONTRACTED_CREATED_JOIN_CODEPOINTS = spell(CONTRACTION_LEAD, CONTRACTED_JOINING_MAY, FOLLOWER_3)
CONTRACTED_UNMOVED_JOIN_CODEPOINTS = spell(CONTRACTION_LEAD, CONTRACTED_MAY, FOLLOWER_3)
CONTRACTED_CREATED_JOIN_RULES = [CONTRACTED_ENTRY_RULE, CONTRACTED_CREATED_JOIN_RULE]
JOIN_RETARGET_GLYPHS = ["qsL", "qsAt", "qsIt", "qsTea.half.ex-y5", "qsNo.en-ext-1"]
JOIN_RETARGET_CODEPOINTS = spell(LEAD, AT, IT, TEA, NO)
JOIN_RETARGET_RULES = [JOIN_RULE, RETARGET_RULE]
RETARGETED_CREATED_JOIN_GLYPHS = ["qsL", "qsTea.half.ex-y5", "qsNo.en-ext-1.chain-fixture", "qsF3"]
RETARGETED_CREATED_JOIN_CODEPOINTS = spell(LEAD, TEA, NO_CHAINED, FOLLOWER_3)
RETARGETED_CREATED_JOIN_RULES = [RETARGET_RULE, RETARGETED_CREATED_JOIN_RULE]
MOVING_RETARGET_GLYPHS = ["qsL", "qsTea.half.ex-y5.moving-fixture", "qsNo.en-ext-1", "qsF1"]
MOVING_RETARGET_CODEPOINTS = spell(LEAD, MOVING_TEA, NO, FOLLOWER_1)
MOVING_RETARGETED_CREATED_JOIN_GLYPHS = [
    "qsL",
    "qsTea.half.ex-y5.moving-fixture",
    "qsNo.en-ext-1.chain-fixture",
    "qsF3",
]
MOVING_RETARGETED_CREATED_JOIN_CODEPOINTS = spell(LEAD, MOVING_TEA, NO_CHAINED, FOLLOWER_3)
MOVING_RETARGETED_CREATED_JOIN_RULES = [MOVING_RETARGET_RULE, RETARGETED_CREATED_JOIN_RULE]
RETARGET_BEHIND_CREATED_JOIN_GLYPHS = [
    "qsL",
    "qsJ.ex-y0.ex-ext-3.long",
    "qsTea.half.ex-y5",
    "qsNo.en-ext-1",
    "qsF1",
]
RETARGET_BEHIND_CREATED_JOIN_CODEPOINTS = spell(LEAD, PIVOT_SHORTENED, TEA, NO, FOLLOWER_1)
RETARGET_BEHIND_CREATED_JOIN_RULES = [RETARGET_RULE, RETARGET_BEHIND_CREATED_JOIN_RULE]
EXTENSION_BEHIND_CREATED_JOIN_GLYPHS = ["qsL", "qsNo.en-ext-1", "qsJ.ex-y0.ex-ext-1", "qsF3"]
EXTENSION_BEHIND_CREATED_JOIN_CODEPOINTS = spell(LEAD, NO, PIVOT, FOLLOWER_3)
EXTENSION_BEHIND_CREATED_JOIN_RULES = [COMPOSED_EXT_RULE, EXTENSION_BEHIND_CREATED_JOIN_RULE]
CONTRACTED_REDRAWN_CHAIN_GLYPHS = [
    "qsBay.contract-lead",
    "qsMay.en-y0.ex-y5.contract-fixture",
    "qsEight.ex-ext-1",
    "qsF3",
]
CONTRACTED_REDRAWN_CHAIN_CODEPOINTS = spell(
    CONTRACTION_LEAD, CONTRACTED_JOINING_MAY, EIGHT_EXTENDED, FOLLOWER_3
)
CONTRACTED_REDRAWN_CHAIN_RULES = [CONTRACTED_ENTRY_RULE, REDRAWN_EXT_RULE, CONTRACTED_REDRAWN_CHAIN_RULE]
REDRAWN_BEHIND_CREATED_JOIN_GLYPHS = ["qsL", "qsNo.en-ext-1", "qsEight.ex-ext-1", "qsF3"]
REDRAWN_BEHIND_CREATED_JOIN_CODEPOINTS = spell(LEAD, NO, EIGHT_EXTENDED, FOLLOWER_3)
REDRAWN_BEHIND_CREATED_JOIN_RULES = [REDRAWN_EXT_RULE, REDRAWN_BEHIND_CREATED_JOIN_RULE]
RETARGETED_EXTENSION_CHAIN_GLYPHS = [
    "qsL",
    "qsTea.half.ex-y5",
    "qsNo.en-ext-1.extension-chain-fixture",
    "qsJ.ex-y0.ex-ext-1",
    "qsF3",
]
RETARGETED_EXTENSION_CHAIN_CODEPOINTS = spell(LEAD, TEA, NO_EXTENSION_CHAINED, PIVOT, FOLLOWER_3)
RETARGETED_EXTENSION_CHAIN_RULES = [
    RETARGET_RULE,
    COMPOSED_EXT_RULE,
    EXTENSION_BEHIND_CREATED_JOIN_RULE,
]


def retarget_window(uid="r-1"):
    return unit(
        uid,
        list(RETARGET_GLYPHS),
        ["y0", "y5", "y0"],
        [
            "qsL/full/None/None/",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=RETARGET_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def moving_retarget_window(uid="mr-1"):
    return unit(
        uid,
        list(MOVING_RETARGET_GLYPHS),
        ["y0", "y5", "y0"],
        [
            "qsL/full/None/None/",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=MOVING_RETARGET_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def moving_retargeted_created_join_window(uid="mrcj-1"):
    return unit(
        uid,
        list(MOVING_RETARGETED_CREATED_JOIN_GLYPHS),
        ["y0", "y5", "break"],
        [
            "qsL/full/None/None/",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/baseline/",
            "qsF3/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=MOVING_RETARGETED_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def composed_retarget_window(uid="cr-1"):
    return unit(
        uid,
        list(COMPOSED_RETARGET_GLYPHS),
        ["y0", "y0", "y5"],
        [
            "qsL/full/None/None/",
            "qsSee/full/None/None/",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=COMPOSED_RETARGET_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 2, "right": 3},
    )


def created_join_window(uid="cj-1"):
    return unit(
        uid,
        list(CREATED_JOIN_GLYPHS),
        ["y0", "break"],
        [
            "qsL/full/None/None/",
            "qsJ/hapax/None/baseline/ex-ext-1",
            "qsF3/full/None/None/",
        ],
        ["y0", "y0"],
        codepoints=CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def composed_created_join_window(uid="ccj-1"):
    return unit(
        uid,
        list(COMPOSED_CREATED_JOIN_GLYPHS),
        ["y0", "y0", "break"],
        [
            "qsL/full/None/None/",
            "qsSee/full/None/None/",
            "qsJ/hapax/None/baseline/ex-ext-1",
            "qsF3/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=COMPOSED_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 2, "right": 3},
    )


def widened_created_join_window(uid="wcj-1"):
    return unit(
        uid,
        list(WIDENED_CREATED_JOIN_GLYPHS),
        ["y0", "break", "y0"],
        [
            "qsL/full/None/None/",
            "qsJ/hapax/None/baseline/ex-ext-1",
            "qsF3/full/None/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=WIDENED_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def composed_widened_join_window(uid="cwj-1"):
    return unit(
        uid,
        list(COMPOSED_WIDENED_JOIN_GLYPHS),
        ["y0", "y0", "break", "y0"],
        [
            "qsL/full/None/None/",
            "qsSee/full/None/None/",
            "qsJ/hapax/None/baseline/ex-ext-1",
            "qsF3/full/None/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0", "y0"],
        codepoints=COMPOSED_WIDENED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 3, "right": 4},
    )


def reaching_created_join_window(uid="rcjr-1"):
    return unit(
        uid,
        list(REACHING_CREATED_JOIN_GLYPHS),
        ["y0", "break", "y0"],
        [
            "qsL/full/None/None/",
            "qsJ/hapax/None/baseline/ex-ext-1",
            "qsF3/full/None/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=REACHING_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def composed_reaching_join_window(uid="crjr-1"):
    return unit(
        uid,
        list(COMPOSED_REACHING_JOIN_GLYPHS),
        ["y0", "y0", "break", "y0"],
        [
            "qsL/full/None/None/",
            "qsSee/full/None/None/",
            "qsJ/hapax/None/baseline/ex-ext-1",
            "qsF3/full/None/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0", "y0"],
        codepoints=COMPOSED_REACHING_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 3, "right": 4},
    )


def contracted_created_join_window(uid="ccjc-1", codepoints=CONTRACTED_CREATED_JOIN_CODEPOINTS):
    return unit(
        uid,
        list(CONTRACTED_CREATED_JOIN_GLYPHS),
        ["y0", "break"],
        [
            "qsBay/full/None/None/",
            "qsMay/loop/baseline/x-height/en-con-1",
            "qsF3/full/x-height/None/",
        ],
        ["y0", "y5"],
        codepoints=codepoints,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def join_retarget_window(uid="jr-1"):
    return unit(
        uid,
        list(JOIN_RETARGET_GLYPHS),
        ["y0", "y5", "y0", "y5"],
        [
            "qsL/full/None/None/",
            "qsAt/full/None/None/",
            "qsIt/full/None/None/",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/None/",
        ],
        ["y0", "break", "y0", "y0"],
        codepoints=JOIN_RETARGET_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def retargeted_created_join_window(uid="rcj-1"):
    return unit(
        uid,
        list(RETARGETED_CREATED_JOIN_GLYPHS),
        ["y0", "y5", "break"],
        [
            "qsL/full/None/None/",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/baseline/",
            "qsF3/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=RETARGETED_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def retarget_behind_created_join_window(uid="rbcj-1"):
    return unit(
        uid,
        list(RETARGET_BEHIND_CREATED_JOIN_GLYPHS),
        ["y0", "break", "y5", "y0"],
        [
            "qsL/full/None/None/",
            "qsJ/hapax/None/baseline/ex-ext-1",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0", "y0"],
        codepoints=RETARGET_BEHIND_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def extension_behind_created_join_window(uid="ebcj-1"):
    return unit(
        uid,
        list(EXTENSION_BEHIND_CREATED_JOIN_GLYPHS),
        ["y0", "break", "y0"],
        [
            "qsL/full/None/None/",
            "qsNo/flipped/baseline/baseline/",
            "qsJ/full/None/None/",
            "qsF3/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=EXTENSION_BEHIND_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def contracted_redrawn_chain_window(uid="crc-1"):
    return unit(
        uid,
        list(CONTRACTED_REDRAWN_CHAIN_GLYPHS),
        ["y0", "break", "y0"],
        [
            "qsBay/full/None/None/",
            "qsMay/loop/baseline/x-height/en-con-1",
            "qsEight/smaller-loop/None/None/",
            "qsF3/full/None/None/",
        ],
        ["y0", "y5", "y0"],
        codepoints=CONTRACTED_REDRAWN_CHAIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def redrawn_behind_created_join_window(uid="rdbcj-1"):
    return unit(
        uid,
        list(REDRAWN_BEHIND_CREATED_JOIN_GLYPHS),
        ["y0", "break", "y0"],
        [
            "qsL/full/None/None/",
            "qsNo/flipped/baseline/baseline/",
            "qsEight/smaller-loop/None/None/",
            "qsF3/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=REDRAWN_BEHIND_CREATED_JOIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def retargeted_extension_chain_window(uid="rec-1"):
    return unit(
        uid,
        list(RETARGETED_EXTENSION_CHAIN_GLYPHS),
        ["y0", "y5", "break", "y0"],
        [
            "qsL/full/None/None/",
            "qsTea/full/None/baseline/",
            "qsNo/flipped/baseline/baseline/",
            "qsJ/full/None/None/",
            "qsF3/full/None/None/",
        ],
        ["y0", "y0", "y0", "y0"],
        codepoints=RETARGETED_EXTENSION_CHAIN_CODEPOINTS,
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def test_a_pure_join_retarget_matches(slide_context):
    assert sv._matches(RETARGET_RULE["match"], retarget_window(), context=slide_context())


def test_a_new_join_matches(slide_context):
    assert sv._matches(CREATED_JOIN_RULE["match"], created_join_window(), context=slide_context())


def test_a_pair_that_remains_broken_does_not_match_a_created_join(slide_context):
    broken = created_join_window()
    broken["after"]["seams"][1] = "break"
    assert not sv._matches(CREATED_JOIN_RULE["match"], broken, context=slide_context())


def test_a_created_join_whose_receiver_does_not_move_by_the_declared_shift_is_refused(slide_context):
    assert not sv._matches(
        CREATED_JOIN_RULE["match"],
        created_join_window(),
        context=slide_context("after-created-join-unmoved"),
    )


def test_a_new_join_whose_follower_redraws_wider_matches(slide_context):
    assert sv._matches(
        WIDENED_CREATED_JOIN_RULE["match"], widened_created_join_window(), context=slide_context()
    )


def test_a_created_join_whose_follower_keeps_its_advance_is_refused_by_the_widened_rule(slide_context):
    assert not sv._matches(
        WIDENED_CREATED_JOIN_RULE["match"],
        widened_created_join_window(),
        context=slide_context("after-created-join-follower-not-widened"),
    )


def test_a_created_join_rule_declaring_no_advance_is_refused_by_a_widened_follower(slide_context):
    unwidened = json.loads(json.dumps(WIDENED_CREATED_JOIN_RULE))
    unwidened["match"]["after"]["follower_advance"] = 0
    assert not sv._matches(unwidened["match"], widened_created_join_window(), context=slide_context())


def test_a_new_join_whose_follower_reaches_back_matches(slide_context):
    assert sv._matches(
        REACHING_CREATED_JOIN_RULE["match"], reaching_created_join_window(), context=slide_context()
    )


def test_a_created_join_rule_declaring_no_reach_is_refused_by_a_follower_that_reaches_back(slide_context):
    unreaching = json.loads(json.dumps(REACHING_CREATED_JOIN_RULE))
    unreaching["match"]["after"]["follower_reach"] = 0
    assert not sv._matches(unreaching["match"], reaching_created_join_window(), context=slide_context())


def test_a_created_join_whose_follower_keeps_its_origin_is_refused_by_the_reaching_rule(slide_context):
    assert not sv._matches(
        REACHING_CREATED_JOIN_RULE["match"],
        reaching_created_join_window(),
        context=slide_context("after-created-join-follower-not-reaching"),
    )


def test_a_slide_and_a_reaching_created_join_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_REACHING_JOIN_RULES, composed_reaching_join_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], REACHING_CREATED_JOIN_RULE["id"]: [2]}


def test_a_created_join_rule_reaching_back_a_negative_count_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(REACHING_CREATED_JOIN_RULE))
    rule["match"]["after"]["follower_reach"] = -1
    with pytest.raises(SystemExit, match="a follower reaches back"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def _several_pivots(*families):
    rule = json.loads(json.dumps(CREATED_JOIN_RULE))
    rule["id"] = "fixture-join-created-several-pivots"
    rule["match"]["before"]["pivot"] = list(families)
    rule["match"]["after"]["pivot_cells"] = [f"{family}/hapax/None/baseline/ex-ext-1" for family in families]
    return rule


def test_a_created_join_rule_naming_several_pivots_reaches_one_that_is_not_first(slide_context):
    rule = _several_pivots("qsSee", "qsJ")
    assert sv._matches(rule["match"], created_join_window(), context=slide_context())


def test_a_created_join_rule_whose_pivot_list_omits_the_window_letter_does_not_match(slide_context):
    rule = _several_pivots("qsSee", "qsTea")
    assert not sv._matches(rule["match"], created_join_window(), context=slide_context())


def test_a_created_join_rule_naming_several_pivots_still_composes(slide_context):
    rule = _several_pivots("qsSee", "qsJ")
    events = sv._composed([SLIDE_RULE, rule], composed_created_join_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], rule["id"]: [2]}


def test_a_created_join_pivot_cell_outside_the_pivot_list_is_refused_at_load(tmp_path):
    rule = _several_pivots("qsSee", "qsJ")
    rule["match"]["after"]["pivot_cells"].append("qsMay/loop/None/baseline/ex-ext-1")
    with pytest.raises(SystemExit, match="is not a cell of"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_created_join_pivot_list_that_repeats_a_family_is_refused_at_load(tmp_path):
    rule = _several_pivots("qsJ", "qsJ")
    with pytest.raises(SystemExit, match="distinct family names"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def pea_retarget_window(uid="pr-1"):
    return unit(
        uid,
        ["qsL", "qsPea.half.ex-y5", "qsNo.en-ext-1", "qsF1"],
        ["y0", "y5", "y0"],
        [
            "qsL/full/None/None/",
            "qsPea/full/None/baseline/",
            "qsNo/flipped/baseline/None/",
            "qsF1/full/None/None/",
        ],
        ["y0", "y0", "y0"],
        codepoints=spell(LEAD, PEA, NO, FOLLOWER_1),
        configs=("default",),
        ink_deltas={"default": SLIDE_DELTA},
        pair={"left": 1, "right": 2},
    )


def test_the_checked_in_tea_no_rule_reads_the_retarget(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["tea-no-xheight-join-retargeted"]["match"]
    assert sv._matches(match, retarget_window(), context=slide_context())
    assert not sv._matches(match, founding_window(), context=slide_context())


def test_the_checked_in_pea_no_rule_reads_the_retarget(slide_context):
    match = {rule["id"]: rule for rule in sv.load_rules(sv.RULES)}["pea-no-xheight-join-retargeted"]["match"]
    context = slide_context()
    assert sv._matches(match, pea_retarget_window(), context=context)
    assert not sv._matches(match, retarget_window(), context=context)
    assert not sv._matches(match, founding_window(), context=context)
    unnamed = pea_retarget_window()
    unnamed["after"]["cells"][1] = "qsPea/half/None/x-height/"
    assert not sv._matches(match, unnamed, context=context)


def test_a_retarget_that_brings_its_follower_nearer_matches(slide_context):
    assert sv._matches(MOVING_RETARGET_RULE["match"], moving_retarget_window(), context=slide_context())


def test_a_follower_that_stands_still_defeats_the_moving_retarget_match(slide_context):
    assert not sv._matches(MOVING_RETARGET_RULE["match"], retarget_window(), context=slide_context())


def test_a_follower_that_moves_defeats_the_standing_retarget_match(slide_context):
    assert not sv._matches(RETARGET_RULE["match"], moving_retarget_window(), context=slide_context())


def test_a_created_join_chains_behind_a_retarget_that_moved_its_follower(slide_context):
    events = sv._composed(
        MOVING_RETARGETED_CREATED_JOIN_RULES,
        moving_retargeted_created_join_window(),
        slide_context(),
    )
    assert events == {MOVING_RETARGET_RULE["id"]: [1], RETARGETED_CREATED_JOIN_RULE["id"]: [2]}


def test_an_unmoved_follower_defeats_the_retarget_match(slide_context):
    assert not sv._matches(
        RETARGET_RULE["match"], retarget_window(), context=slide_context("after-retarget-unmoved")
    )


def test_a_moved_origin_defeats_the_retarget_match(slide_context):
    assert not sv._matches(
        RETARGET_RULE["match"], retarget_window(), context=slide_context("after-retarget-moved-origin")
    )


def test_one_extra_pixel_before_the_retarget_defeats_the_match(slide_context):
    assert not sv._matches(
        RETARGET_RULE["match"], retarget_window(), context=slide_context("after-extra-prefix-pixel")
    )


def test_one_extra_pixel_after_the_retarget_defeats_the_match(slide_context):
    assert not sv._matches(
        RETARGET_RULE["match"],
        retarget_window(),
        context=slide_context("after-extra-post-follower-pixel"),
    )


def test_a_seam_that_holds_its_height_defeats_the_retarget_match(slide_context):
    stayed = retarget_window()
    stayed["after"]["seams"] = ["y0", "y5", "y0"]
    assert not sv._matches(RETARGET_RULE["match"], stayed, context=slide_context())


def test_a_wrong_follower_family_defeats_the_retarget_match(slide_context):
    other = retarget_window()
    other["before"]["glyphs"][2] = "qsF1"
    other["after"]["cells"][2] = "qsF1/full/None/None/"
    other["codepoints"] = spell(LEAD, TEA, FOLLOWER_1, FOLLOWER_1)
    assert not sv._matches(RETARGET_RULE["match"], other, context=slide_context())


def test_an_unnamed_after_cell_defeats_the_retarget_match(slide_context):
    other = retarget_window()
    other["after"]["cells"][1] = "qsTea/half/None/x-height/"
    assert not sv._matches(RETARGET_RULE["match"], other, context=slide_context())


def test_a_retarget_rule_reads_no_unit_without_ink_deltas():
    bare = retarget_window()
    del bare["ink_deltas"]
    assert not sv._matches(RETARGET_RULE["match"], bare)
    empty = retarget_window()
    empty["ink_deltas"] = {}
    assert not sv._matches(RETARGET_RULE["match"], empty)


def test_a_window_with_no_retarget_pivot_is_refused_before_any_shaping():
    assert not sv._matches(
        RETARGET_RULE["match"], slide_unit("r-3", ["qsL", "qsF1"], spell(LEAD, FOLLOWER_1))
    )


def test_a_matchable_retarget_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(RETARGET_RULE["match"], retarget_window())


def test_except_left_holds_the_guarded_family_on_the_join_retargeted_shape(slide_context):
    context = slide_context()
    assert not sv._matches(guarding(RETARGET_RULE, ["qsL"]), retarget_window(), context=context)
    assert sv._matches(guarding(RETARGET_RULE, ["qsL"]), retarget_window(), guard=False, context=context)


def test_the_join_retargeted_shape_and_the_other_shapes_do_not_read_each_others_units(slide_context):
    context = slide_context()
    assert not sv._matches(RETARGET_RULE["match"], founding_window(), context=context)
    assert not sv._matches(RETARGET_RULE["match"], canonical(), context=context)
    assert not sv._matches(RETARGET_RULE["match"], join_window(), context=context)
    assert not sv._matches(RETARGET_RULE["match"], gain_window(), context=context)
    assert not sv._matches(SLIDE_RULE["match"], retarget_window(), context=context)
    assert not sv._matches(EXT_RULE["match"], retarget_window(), context=context)
    assert not sv._matches(INK_RULE["match"], retarget_window())
    assert not sv._matches(GAIN_RULE["match"], retarget_window(), context=context)
    assert not sv._matches(JOIN_RULE["match"], retarget_window(), context=context)
    assert not sv._matches(ENTRY_RULE["match"], retarget_window(), context=context)


def test_a_retarget_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [RETARGET_RULE]))
    assert rule["match"]["before"] == {"pivot": "qsTea.half", "seam_out": "y5", "follower": "qsNo"}
    assert rule["match"]["after"]["retarget"] == "y0"
    assert rule["match"]["after"]["shift"] == -1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule.update(note=""),
        lambda rule: rule["match"]["before"].update(pivot=""),
        lambda rule: rule["match"]["before"].update(seam_out=""),
        lambda rule: rule["match"]["before"].update(follower=""),
        lambda rule: rule["match"]["before"].update(follower=[]),
        lambda rule: rule["match"]["after"].update(retarget=""),
        lambda rule: rule["match"]["after"].update(shift="-1"),
        lambda rule: rule["match"]["after"].update(pivot_cells=[]),
        lambda rule: rule["match"]["after"].update(receiver_cells=[]),
        lambda rule: rule["match"].update(except_left="qsL"),
    ],
)
def test_malformed_retarget_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(RETARGET_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_break_retarget_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(RETARGET_RULE))
    rule["match"]["after"]["retarget"] = "break"
    with pytest.raises(SystemExit, match="gap shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_break_before_seam_is_refused_at_load_for_retarget(tmp_path):
    rule = json.loads(json.dumps(RETARGET_RULE))
    rule["match"]["before"]["seam_out"] = "break"
    with pytest.raises(SystemExit, match="not a yK height"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_created_join_rule_loads_with_a_break_before_seam(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [CREATED_JOIN_RULE]))
    assert rule["match"]["before"]["seam_out"] == "break"
    assert rule["match"]["after"]["joined"] == "y0"


def test_a_retarget_that_holds_its_height_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(RETARGET_RULE))
    rule["match"]["after"]["retarget"] = "y5"
    with pytest.raises(SystemExit, match="not a retarget"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_retarget_cell_belonging_to_another_letter_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(RETARGET_RULE))
    rule["match"]["after"]["pivot_cells"] = ["qsDay/full/None/baseline/"]
    with pytest.raises(SystemExit, match="is not a cell of"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_retarget_rule_missing_its_before_block_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(RETARGET_RULE))
    rule["match"].pop("before")
    with pytest.raises(SystemExit, match="needs match.before to be exactly"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_the_retarget_and_slide_shapes_at_once_is_refused(tmp_path):
    rule = json.loads(json.dumps(RETARGET_RULE))
    rule["match"]["after"]["slide"] = -1
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_main_fills_only_the_blank_matching_join_retargeted_units(tmp_path, monkeypatch, slide_fonts):
    units = [retarget_window("r-1"), retarget_window("r-2"), founding_window("s-1")]
    payload = _run_main(
        tmp_path,
        monkeypatch,
        units,
        [{"unit": "r-2", "verdict": "approve", "note": "already", "at": STAMP}],
        rules_list=(RETARGET_RULE,),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["r-1"]
    assert payload["verdicts"][0]["note"] == f"[standing: {RETARGET_RULE['id']}] {RETARGET_RULE['note']}"


def test_a_slide_and_a_join_retarget_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_RETARGET_RULES, composed_retarget_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], RETARGET_RULE["id"]: [2]}


def test_a_slide_and_a_created_join_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_CREATED_JOIN_RULES, composed_created_join_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], CREATED_JOIN_RULE["id"]: [2]}


def test_a_slide_and_a_widened_created_join_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_WIDENED_JOIN_RULES, composed_widened_join_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], WIDENED_CREATED_JOIN_RULE["id"]: [2]}


def test_a_retarget_chained_behind_a_created_join_carries_the_follower_advance(slide_context):
    """A retarget whose pivot is a widened created join's follower reads that letter under the join's shift alone, while its own follower and everything past it carry the advance the wider form gave back."""
    events = sv._composed(
        RETARGET_BEHIND_WIDENED_JOIN_RULES,
        retarget_behind_created_join_window(),
        slide_context("after-created-join-widened-follower"),
    )
    assert events == {RETARGET_BEHIND_WIDENED_JOIN_RULE["id"]: [1], RETARGET_RULE["id"]: [2]}


def test_a_created_join_chains_behind_an_entry_contraction_on_its_pivot(slide_context):
    events = sv._composed(CONTRACTED_CREATED_JOIN_RULES, contracted_created_join_window(), slide_context())
    assert events == {CONTRACTED_ENTRY_RULE["id"]: [1], CONTRACTED_CREATED_JOIN_RULE["id"]: [1]}


def test_neither_rule_alone_reads_a_created_join_behind_a_contraction(slide_context):
    window = contracted_created_join_window()
    for rule in CONTRACTED_CREATED_JOIN_RULES:
        assert not sv._matches(rule["match"], window, context=slide_context())
        assert sv._composed_walk([rule], window, slide_context()) is None


def test_a_created_join_whose_pivot_moved_its_origin_is_no_event_without_the_contraction(slide_context):
    events = sv._composed_walk(
        [SLIDE_RULE, CONTRACTED_CREATED_JOIN_RULE], contracted_created_join_window(), slide_context()
    )
    assert events is None


def test_a_chained_created_join_still_needs_its_follower_moved_by_both_shifts(slide_context):
    window = contracted_created_join_window(codepoints=CONTRACTED_UNMOVED_JOIN_CODEPOINTS)
    assert sv._composed(CONTRACTED_CREATED_JOIN_RULES, window, slide_context()) is None


def test_a_created_join_chains_behind_a_retarget_on_its_follower(slide_context):
    events = sv._composed(RETARGETED_CREATED_JOIN_RULES, retargeted_created_join_window(), slide_context())
    assert events == {RETARGET_RULE["id"]: [1], RETARGETED_CREATED_JOIN_RULE["id"]: [2]}


def test_neither_rule_alone_reads_a_created_join_behind_a_retarget(slide_context):
    window = retargeted_created_join_window()
    for rule in RETARGETED_CREATED_JOIN_RULES:
        assert not sv._matches(rule["match"], window, context=slide_context())
        assert sv._composed_walk([rule], window, slide_context()) is None


def test_a_created_join_chained_behind_a_retarget_still_needs_both_shifts(slide_context):
    events = sv._composed(
        RETARGETED_CREATED_JOIN_RULES,
        retargeted_created_join_window(),
        slide_context("after-chained-created-join-unmoved"),
    )
    assert events is None


def test_a_retarget_chains_behind_a_created_join_on_its_follower(slide_context):
    events = sv._composed(
        RETARGET_BEHIND_CREATED_JOIN_RULES, retarget_behind_created_join_window(), slide_context()
    )
    assert events == {RETARGET_BEHIND_CREATED_JOIN_RULE["id"]: [1], RETARGET_RULE["id"]: [2]}


def test_neither_rule_alone_reads_a_retarget_behind_a_created_join(slide_context):
    window = retarget_behind_created_join_window()
    for rule in RETARGET_BEHIND_CREATED_JOIN_RULES:
        assert not sv._matches(rule["match"], window, context=slide_context())
        assert sv._composed_walk([rule], window, slide_context()) is None


def test_a_retarget_chained_behind_a_created_join_still_needs_both_shifts(slide_context):
    events = sv._composed(
        RETARGET_BEHIND_CREATED_JOIN_RULES,
        retarget_behind_created_join_window(),
        slide_context("after-retarget-behind-created-join-unmoved"),
    )
    assert events is None


def test_an_extension_chains_behind_a_created_join_on_its_follower(slide_context):
    events = sv._composed(
        EXTENSION_BEHIND_CREATED_JOIN_RULES, extension_behind_created_join_window(), slide_context()
    )
    assert events == {EXTENSION_BEHIND_CREATED_JOIN_RULE["id"]: [1], COMPOSED_EXT_RULE["id"]: [2]}


def test_neither_rule_alone_reads_an_extension_behind_a_created_join(slide_context):
    window = extension_behind_created_join_window()
    for rule in EXTENSION_BEHIND_CREATED_JOIN_RULES:
        assert not sv._matches(rule["match"], window, context=slide_context())
        assert sv._composed_walk([rule], window, slide_context()) is None


def test_an_extension_chained_behind_a_created_join_still_needs_both_shifts(slide_context):
    events = sv._composed(
        EXTENSION_BEHIND_CREATED_JOIN_RULES,
        extension_behind_created_join_window(),
        slide_context("after-extension-behind-created-join-unmoved"),
    )
    assert events is None


def test_a_redraw_chains_behind_a_created_join_on_its_follower(slide_context):
    events = sv._composed(
        REDRAWN_BEHIND_CREATED_JOIN_RULES, redrawn_behind_created_join_window(), slide_context()
    )
    assert events == {REDRAWN_BEHIND_CREATED_JOIN_RULE["id"]: [1], REDRAWN_EXT_RULE["id"]: [2]}


def test_neither_rule_alone_reads_a_redraw_behind_a_created_join(slide_context):
    window = redrawn_behind_created_join_window()
    for rule in REDRAWN_BEHIND_CREATED_JOIN_RULES:
        assert not sv._matches(rule["match"], window, context=slide_context())
        assert sv._composed_walk([rule], window, slide_context()) is None


def test_a_redraw_chained_behind_a_created_join_still_needs_both_shifts(slide_context):
    events = sv._composed(
        REDRAWN_BEHIND_CREATED_JOIN_RULES,
        redrawn_behind_created_join_window(),
        slide_context("after-redrawn-unmoved-follower"),
    )
    assert events is None


def test_a_contraction_a_created_join_and_a_redraw_chain_through_one_window(slide_context):
    events = sv._composed(CONTRACTED_REDRAWN_CHAIN_RULES, contracted_redrawn_chain_window(), slide_context())
    assert events == {
        CONTRACTED_ENTRY_RULE["id"]: [1],
        CONTRACTED_REDRAWN_CHAIN_RULE["id"]: [1],
        REDRAWN_EXT_RULE["id"]: [2],
    }


def test_a_redraw_behind_a_chained_created_join_still_needs_every_shift(slide_context):
    events = sv._composed(
        CONTRACTED_REDRAWN_CHAIN_RULES,
        contracted_redrawn_chain_window(),
        slide_context("after-redrawn-unmoved-follower"),
    )
    assert events is None


def test_a_retarget_a_created_join_and_an_extension_chain_through_one_window(slide_context):
    events = sv._composed(
        RETARGETED_EXTENSION_CHAIN_RULES, retargeted_extension_chain_window(), slide_context()
    )
    assert events == {
        RETARGET_RULE["id"]: [1],
        EXTENSION_BEHIND_CREATED_JOIN_RULE["id"]: [2],
        COMPOSED_EXT_RULE["id"]: [3],
    }


def test_a_chain_of_three_still_needs_the_extension_to_deliver_its_column(slide_context):
    events = sv._composed(
        RETARGETED_EXTENSION_CHAIN_RULES,
        retargeted_extension_chain_window(),
        slide_context("after-extension-behind-created-join-unmoved"),
    )
    assert events is None


def test_a_join_drop_and_a_join_retarget_in_one_window_compose(slide_context):
    events = sv._composed(JOIN_RETARGET_RULES, join_retarget_window(), slide_context())
    assert events == {JOIN_RULE["id"]: [1], RETARGET_RULE["id"]: [3]}


def test_a_pure_join_retarget_is_not_composed(slide_context):
    assert sv._composed(COMPOSED_RETARGET_RULES, retarget_window(), slide_context()) is None
    assert sv._matches(RETARGET_RULE["match"], retarget_window(), context=slide_context())


def test_main_writes_one_composed_retarget_record_and_leaves_the_per_rule_lines(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [composed_retarget_window("cr-1")],
        [],
        rules_list=(SLIDE_RULE, RETARGET_RULE),
        fonts=slide_fonts,
    )
    assert [record["unit"] for record in payload["verdicts"]] == ["cr-1"]
    record = payload["verdicts"][0]
    assert record["verdict"] == "approve"
    assert record["note"] == (
        f"[standing: {SLIDE_RULE['id']} + {RETARGET_RULE['id']}] "
        f"{SLIDE_RULE['note']}; {RETARGET_RULE['note']}"
    )
    lines = capsys.readouterr().out.splitlines()
    assert f"  {SLIDE_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left" in lines
    assert (
        f"  {RETARGET_RULE['id']}: 0 filled, 0 already verdicted, 0 held for review by except_left"
    ) in lines
    assert (
        f"  {SLIDE_RULE['id']} + {RETARGET_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


def test_one_extra_pixel_defeats_the_composed_retarget_reading(slide_context):
    assert (
        sv._composed(
            COMPOSED_RETARGET_RULES, composed_retarget_window(), slide_context("after-extra-prefix-pixel")
        )
        is None
    )


def test_the_retarget_guard_holds_the_whole_composed_window(slide_context):
    events = sv._composed(COMPOSED_RETARGET_RULES, composed_retarget_window(), slide_context())
    assert sv._composed_held(
        [guarded_rule(RETARGET_RULE, ["qsL"]), SLIDE_RULE],
        composed_retarget_window(),
        events,
        slide_context(),
    )


def test_main_fills_a_single_retarget_window_under_that_shapes_own_line(
    tmp_path, monkeypatch, capsys, slide_fonts
):
    payload = _run_main(
        tmp_path,
        monkeypatch,
        [retarget_window("r-1"), composed_retarget_window("cr-1")],
        [],
        rules_list=(SLIDE_RULE, RETARGET_RULE),
        fonts=slide_fonts,
    )
    by_unit = {record["unit"]: record for record in payload["verdicts"]}
    assert set(by_unit) == {"r-1", "cr-1"}
    assert by_unit["r-1"]["note"] == f"[standing: {RETARGET_RULE['id']}] {RETARGET_RULE['note']}"
    assert by_unit["cr-1"]["note"].startswith(f"[standing: {SLIDE_RULE['id']} + {RETARGET_RULE['id']}]")
    lines = capsys.readouterr().out.splitlines()
    assert (
        f"  {RETARGET_RULE['id']}: 1 filled, 0 already verdicted, 0 held for review by except_left" in lines
    )
    assert (
        f"  {SLIDE_RULE['id']} + {RETARGET_RULE['id']}: 1 filled, 0 already verdicted, "
        "0 held for review by except_left"
    ) in lines


REDRAWN_GLYPHS = ["qsL", "qsEight", "qsF3"]
REDRAWN_CODEPOINTS = spell(LEAD, EIGHT, FOLLOWER_3)
REDRAWN_EXT_GLYPHS = ["qsL", "qsEight.ex-ext-1", "qsF3"]
REDRAWN_EXT_CODEPOINTS = spell(LEAD, EIGHT_EXTENDED, FOLLOWER_3)
COMPOSED_REDRAWN_RULES = [REDRAWN_RULE, REDRAWN_EXT_RULE, ENTRY_RULE]


def redrawn_window(uid="rd-1"):
    return slide_unit(uid, REDRAWN_GLYPHS, REDRAWN_CODEPOINTS)


def redrawn_ext_window(uid="rd-2"):
    return slide_unit(uid, REDRAWN_EXT_GLYPHS, REDRAWN_EXT_CODEPOINTS)


PULLED_REDRAWN_GLYPHS = ["qsBay.contract-lead", "qsEight", "qsF3"]
PULLED_REDRAWN_CODEPOINTS = spell(CONTRACTION_LEAD, EIGHT_PULLED_IN, FOLLOWER_3)
COMPOSED_PULLED_REDRAWN_GLYPHS = ["qsL", "qsSee.ex-y0", "qsBay.contract-lead", "qsEight", "qsF3"]
COMPOSED_PULLED_REDRAWN_CODEPOINTS = spell(LEAD, SEE, CONTRACTION_LEAD, EIGHT_PULLED_IN, FOLLOWER_3)
COMPOSED_PULLED_REDRAWN_RULES = [SLIDE_RULE, PULLED_REDRAWN_RULE]


def pulled_redrawn_window(uid="rdp-1"):
    return slide_unit(uid, PULLED_REDRAWN_GLYPHS, PULLED_REDRAWN_CODEPOINTS)


def composed_pulled_redrawn_window(uid="rdp-2"):
    return slide_unit(uid, COMPOSED_PULLED_REDRAWN_GLYPHS, COMPOSED_PULLED_REDRAWN_CODEPOINTS)


def composed_redrawn_window(uid="rd-3"):
    return slide_unit(
        uid,
        ["qsL", "qsEight.ex-ext-1", "qsLow.en-ext-1", "qsF3"],
        spell(LEAD, EIGHT_EXTENDED, LOW, FOLLOWER_3),
    )


REDRAWN_REVERSE_RULE = {
    "id": "fixture-redrawn-reversed",
    "verdict": "approve",
    "note": "·Eight's bowl opens back up and the rest of the window stays put",
    "match": {
        "before": {"pivots": ["qsEight.smaller-loop"]},
        "after": {
            "pivots": ["qsEight.normal-sized-loop"],
            "dropped": [[1, 1]],
            "added": [[1, 2]],
            "shift": 0,
        },
        "except_left": [],
    },
}


def test_a_pure_redrawn_trade_matches(slide_context):
    assert sv._matches(REDRAWN_RULE["match"], redrawn_window(), context=slide_context())


def test_a_trade_that_adds_more_than_it_drops_matches_its_own_direction_only(slide_context):
    context = slide_context()
    window = slide_unit(
        "rd-8", ["qsL", "qsEight.smaller-loop", "qsF3"], spell(LEAD, EIGHT_REVERSED, FOLLOWER_3)
    )
    assert sv._matches(REDRAWN_REVERSE_RULE["match"], window, context=context)
    assert not sv._matches(REDRAWN_RULE["match"], window, context=context)
    assert not sv._matches(REDRAWN_REVERSE_RULE["match"], redrawn_window(), context=context)


def test_the_extension_frame_reads_its_own_rule_and_not_the_bare_one(slide_context):
    context = slide_context()
    assert sv._matches(REDRAWN_EXT_RULE["match"], redrawn_ext_window(), context=context)
    assert not sv._matches(REDRAWN_RULE["match"], redrawn_ext_window(), context=context)
    assert not sv._matches(REDRAWN_EXT_RULE["match"], redrawn_window(), context=context)


def test_an_entry_extended_frame_names_the_same_trade_one_column_over(slide_context):
    window = slide_unit(
        "rd-4", ["qsL", "qsEight.en-ext-1", "qsF3"], spell(LEAD, EIGHT_ENTRY_EXTENDED, FOLLOWER_3)
    )
    assert sv._matches(REDRAWN_RULE["match"], window, context=slide_context())


def test_a_second_pivot_family_glyph_keeping_its_form_rides_as_span_ink(slide_context):
    window = slide_unit(
        "rd-5", ["qsL", "qsEight", "qsF3", "qsEight"], spell(LEAD, EIGHT, FOLLOWER_3, EIGHT_UNCHANGED)
    )
    assert sv._matches(REDRAWN_RULE["match"], window, context=slide_context())


def test_an_unnamed_extra_cell_defeats_the_redrawn_match(slide_context):
    assert not sv._matches(
        REDRAWN_RULE["match"], redrawn_window(), context=slide_context("after-redrawn-extra-cell")
    )


def test_an_unmoved_follower_defeats_the_shifted_redrawn_match(slide_context):
    assert not sv._matches(
        REDRAWN_EXT_RULE["match"],
        redrawn_ext_window(),
        context=slide_context("after-redrawn-unmoved-follower"),
    )


def test_a_redrawn_rule_reads_no_unit_without_ink_deltas():
    assert not sv._matches(
        REDRAWN_RULE["match"], slide_unit("rd-6", REDRAWN_GLYPHS, REDRAWN_CODEPOINTS, deltas={})
    )
    bare = redrawn_window()
    del bare["ink_deltas"]
    assert not sv._matches(REDRAWN_RULE["match"], bare)


def test_a_window_with_no_redrawn_pivot_prefix_glyph_is_refused_before_any_shaping():
    assert not sv._matches(
        REDRAWN_RULE["match"], slide_unit("rd-7", ["qsL", "qsF1"], spell(LEAD, FOLLOWER_1))
    )


def test_a_matchable_redrawn_window_with_no_context_refuses_to_guess():
    with pytest.raises(ValueError, match="SlideContext"):
        sv._matches(REDRAWN_RULE["match"], redrawn_window())


def test_except_left_holds_the_guarded_family_on_the_redrawn_shape(slide_context):
    context = slide_context()
    assert not sv._matches(guarding(REDRAWN_RULE, ["qsL"]), redrawn_window(), context=context)
    assert sv._matches(guarding(REDRAWN_RULE, ["qsL"]), redrawn_window(), guard=False, context=context)


def test_the_redrawn_shape_and_the_other_shapes_do_not_read_each_others_units(slide_context):
    context = slide_context()
    assert not sv._matches(REDRAWN_RULE["match"], founding_window(), context=context)
    assert not sv._matches(REDRAWN_RULE["match"], canonical(), context=context)
    assert not sv._matches(SLIDE_RULE["match"], redrawn_window(), context=context)
    assert not sv._matches(EXT_RULE["match"], redrawn_window(), context=context)
    assert not sv._matches(INK_RULE["match"], redrawn_window())


def test_a_redrawn_trade_and_an_entry_drop_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_REDRAWN_RULES, composed_redrawn_window(), slide_context())
    assert events == {REDRAWN_EXT_RULE["id"]: [1], ENTRY_RULE["id"]: [2]}


def test_the_redrawn_guard_holds_the_whole_composed_window(slide_context):
    rules = [REDRAWN_RULE, guarded_rule(REDRAWN_EXT_RULE, ["qsF3"]), ENTRY_RULE]
    window = composed_redrawn_window()
    context = slide_context()
    events = sv._composed(rules, window, context)
    assert events is not None
    assert sv._composed_held(rules, window, events, context)


def test_a_trade_the_pivots_own_placement_carries_matches(slide_context):
    assert sv._matches(PULLED_REDRAWN_RULE["match"], pulled_redrawn_window(), context=slide_context())


def test_a_pivot_pulled_further_than_its_contraction_names_is_refused(slide_context):
    assert not sv._matches(
        PULLED_REDRAWN_RULE["match"],
        pulled_redrawn_window(),
        context=slide_context("after-redrawn-overpulled-pivot"),
    )


def test_a_slide_and_a_placement_carried_trade_in_one_window_compose(slide_context):
    events = sv._composed(COMPOSED_PULLED_REDRAWN_RULES, composed_pulled_redrawn_window(), slide_context())
    assert events == {SLIDE_RULE["id"]: [1], PULLED_REDRAWN_RULE["id"]: [3]}


def test_a_pure_redrawn_window_is_not_composed(slide_context):
    context = slide_context()
    assert sv._composed(COMPOSED_REDRAWN_RULES, redrawn_window(), context) is None
    assert sv._matches(REDRAWN_RULE["match"], redrawn_window(), context=context)


def test_a_redrawn_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [REDRAWN_RULE]))
    assert rule["match"]["before"] == {"pivots": ["qsEight"]}
    assert rule["match"]["after"] == {
        "pivots": ["qsEight.smaller-loop"],
        "dropped": [[1, 2]],
        "added": [[1, 1]],
        "shift": 0,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rule: rule.update(verdict="reject"),
        lambda rule: rule["match"]["before"].update(pivots=[]),
        lambda rule: rule["match"]["before"].update(pivots="qsEight"),
        lambda rule: rule["match"]["after"].update(dropped=[]),
        lambda rule: rule["match"]["after"].update(dropped=[[1, 2], [1, 2]]),
        lambda rule: rule["match"]["after"].update(dropped=[[1]]),
        lambda rule: rule["match"]["after"].update(added=[[1, True]]),
        lambda rule: rule["match"]["after"].update(added="1,1"),
        lambda rule: rule["match"]["after"].update(shift="closer"),
        lambda rule: rule["match"]["after"].update(shift=True),
        lambda rule: rule["match"]["after"].update(pivots=["qsEight/smaller-loop/None/baseline/"]),
        lambda rule: rule["match"].update(except_left="qsL"),
    ],
)
def test_malformed_redrawn_rules_are_refused(tmp_path, mutate):
    rule = json.loads(json.dumps(REDRAWN_RULE))
    mutate(rule)
    with pytest.raises(SystemExit):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_redrawn_pivot_lists_spanning_two_families_are_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(REDRAWN_RULE))
    rule["match"]["after"]["pivots"] = ["qsI.smaller-loop"]
    with pytest.raises(SystemExit, match="speaks for one letter"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_cell_shared_between_dropped_and_added_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(REDRAWN_RULE))
    rule["match"]["after"]["added"] = [[1, 2]]
    with pytest.raises(SystemExit, match="traded for itself"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_redrawn_rule_missing_its_before_block_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(REDRAWN_RULE))
    rule["match"].pop("before")
    with pytest.raises(SystemExit, match="needs match.before to be exactly"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_a_rule_declaring_the_redrawn_and_gain_shapes_at_once_is_refused(tmp_path):
    rule = json.loads(json.dumps(REDRAWN_RULE))
    rule["match"]["after"]["gained"] = [[1, 1]]
    with pytest.raises(SystemExit, match="exactly one delta shape"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def pure_loss_window(uid="pl-1"):
    return slide_unit(uid, ["qsL", "qsKey", "qsF3"], spell(LEAD, KEY, FOLLOWER_3))


def composed_pure_loss_window(uid="pl-2"):
    return slide_unit(uid, ["qsL", "qsKey", "qsLow.en-ext-1", "qsF3"], spell(LEAD, KEY, LOW, FOLLOWER_3))


def test_a_form_that_only_gives_ink_up_is_redrawn(slide_context):
    """An exit contraction has no trade to name — the foot's terminal pixel goes and nothing takes its place — so the added set is empty and the shift is what the follower does about it. The name-grain extension-dropped shape would speak for this seam too, and blindly for whatever else the window did; this one proves the rest of the window stood still."""
    assert sv._matches(PURE_LOSS_RULE["match"], pure_loss_window(), context=slide_context())


def test_a_pure_loss_form_that_also_takes_a_cell_on_is_refused(slide_context):
    assert not sv._matches(
        PURE_LOSS_RULE["match"], pure_loss_window(), context=slide_context("after-key-crowned")
    )


def test_a_pure_loss_and_an_entry_drop_in_one_window_compose(slide_context):
    events = sv._composed([PURE_LOSS_RULE, ENTRY_RULE], composed_pure_loss_window(), slide_context())
    assert events == {PURE_LOSS_RULE["id"]: [1], ENTRY_RULE["id"]: [2]}


def test_a_pure_loss_rule_loads(tmp_path):
    [rule] = sv.load_rules(_write_rules(tmp_path / "rules.yaml", [PURE_LOSS_RULE]))
    assert rule["match"]["after"]["added"] == []


def test_a_redrawn_rule_naming_no_dropped_cell_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(PURE_LOSS_RULE))
    rule["match"]["after"]["dropped"] = []
    with pytest.raises(SystemExit, match="gives nothing up is not redrawn"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


def test_an_ink_gain_rule_naming_no_gained_cell_is_refused_at_load(tmp_path):
    rule = json.loads(json.dumps(GAIN_RULE))
    rule["match"]["after"]["gained"] = []
    with pytest.raises(SystemExit, match="the whole change an ink-gain rule blesses"):
        sv.load_rules(_write_rules(tmp_path / "rules.yaml", [rule]))


SOLO_WINDOWS = {
    "ligature": (RULE, canonical),
    "extension-dropped": (EXT_RULE, tea_i),
    "ink-delta": (INK_RULE, ink_delta_unit),
    "slide": (SLIDE_RULE, founding_window),
    "ink-gain": (GAIN_RULE, gain_window),
    "join-dropped": (JOIN_RULE, join_window),
    "entry-extension-dropped": (ENTRY_RULE, entry_window),
    "entry-contracted": (CONTRACTED_ENTRY_RULE, contracted_entry_window),
    "stub-dropped": (STUB_RULE, stub_window),
    "redrawn": (REDRAWN_RULE, redrawn_window),
    "join-retargeted": (RETARGET_RULE, retarget_window),
    "join-created": (CREATED_JOIN_RULE, created_join_window),
}


@pytest.mark.parametrize("shape_name", list(sv.SHAPES))
def test_a_solo_rule_of_every_shape_fills_its_window_or_names_the_missing_fonts(
    tmp_path, monkeypatch, shape_name
):
    """One rule of one shape, one window it matches, and a surface carrying no font pair: a font-backed shape has to say so and stop, and every other shape has to fill. Parametrized over the rows so a shape left out of `main`'s gates cannot land quietly — a font-backed one that the gate never sees reaches its own matcher with no context and raises mid-run instead of naming the surface — and so a new row without a window to match against fails here rather than going untested."""
    rule, window = SOLO_WINDOWS[shape_name]
    if sv.SHAPES[shape_name].font_backed:
        with pytest.raises(SystemExit, match="carries no fonts/before.otf"):
            _run_main(tmp_path, monkeypatch, [window("x-1")], [], rules_list=(rule,))
        return
    payload = _run_main(tmp_path, monkeypatch, [window("x-1")], [], rules_list=(rule,))
    assert [record["unit"] for record in payload["verdicts"]] == ["x-1"]
    assert payload["verdicts"][0]["note"] == f"[standing: {rule['id']}] {rule['note']}"


COMPOSED_WALK_CORPORA = {
    "slide": (
        SLIDE_RULE,
        slide_fixture_windows,
        ("after", "after-extra-prefix-pixel", "after-extra-follower-pixel"),
    ),
    "ink-gain": (GAIN_RULE, lambda: [gain_window(), composed_gain_window(), founding_window()], ("after",)),
    "join-dropped": (
        JOIN_RULE,
        lambda: [join_window(), composed_join_window(), founding_window()],
        ("after",),
    ),
    "entry-extension-dropped": (
        ENTRY_RULE,
        lambda: [entry_window(), composed_entry_window(), founding_window()],
        ("after",),
    ),
    "entry-contracted": (
        CONTRACTED_ENTRY_RULE,
        lambda: [
            contracted_entry_window(),
            composed_contracted_entry_window(),
            duplicate_may_before_window(),
            duplicate_may_after_window(),
            contracted_entry_covered_window(),
            composed_contracted_entry_covered_window(),
            founding_window(),
        ],
        ("after", "after-contracted-entry-extra-cell", "after-contracted-entry-unmoved-follower"),
    ),
    "join-retargeted": (
        RETARGET_RULE,
        lambda: [retarget_window(), composed_retarget_window(), founding_window()],
        ("after",),
    ),
    "join-created": (
        CREATED_JOIN_RULE,
        lambda: [created_join_window(), composed_created_join_window(), founding_window()],
        ("after", "after-created-join-unmoved"),
    ),
    "join-created-with-a-widened-follower": (
        WIDENED_CREATED_JOIN_RULE,
        lambda: [widened_created_join_window(), composed_widened_join_window(), created_join_window()],
        ("after", "after-created-join-follower-not-widened"),
    ),
    "join-created-with-a-reaching-follower": (
        REACHING_CREATED_JOIN_RULE,
        lambda: [reaching_created_join_window(), composed_reaching_join_window(), created_join_window()],
        ("after", "after-created-join-follower-not-reaching"),
    ),
    "join-created-behind-a-contraction": (
        CONTRACTED_CREATED_JOIN_RULE,
        lambda: [
            contracted_created_join_window(),
            contracted_created_join_window(codepoints=CONTRACTED_UNMOVED_JOIN_CODEPOINTS),
            contracted_entry_window(),
        ],
        ("after",),
    ),
    "redrawn": (
        REDRAWN_RULE,
        lambda: [redrawn_window(), redrawn_ext_window(), composed_redrawn_window()],
        ("after",),
    ),
    "redrawn-under-an-extension-frame": (
        REDRAWN_EXT_RULE,
        lambda: [redrawn_window(), redrawn_ext_window(), composed_redrawn_window()],
        ("after",),
    ),
    "redrawn-as-a-pure-loss": (
        PURE_LOSS_RULE,
        lambda: [pure_loss_window(), composed_pure_loss_window(), redrawn_window()],
        ("after", "after-key-crowned"),
    ),
}


@pytest.mark.parametrize("corpus_name", list(COMPOSED_WALK_CORPORA))
def test_the_composed_walk_credits_a_shape_exactly_where_its_own_matcher_does(slide_context, corpus_name):
    """The walk and the single-rule matcher have to agree about one rule over every window: credit it where the matcher matches, and credit it nowhere else. Every shape asserts the same property over a corpus of its own — the windows it matches, the composed windows it appears in, and windows belonging to some other shape — so the table above is the whole of what a new shape adds, and the slide row's extra after-fonts carry the refusals a stray pixel is supposed to produce."""
    rule, windows, afters = COMPOSED_WALK_CORPORA[corpus_name]
    for after in afters:
        context = slide_context(after)
        for window in windows():
            credited = set(sv._composed_walk([rule], window, context) or ())
            assert (credited == {rule["id"]}) == sv._matches(rule["match"], window, context=context), (
                after,
                window["id"],
            )


def _keyed_unit(uid="k-1", **overrides):
    """A canonical unit carrying the fields the memo key reads: the build's content-key stamp and the ink deltas."""
    stamped = dict(canonical(uid), content_key="c" * 64, ink_deltas={"ss03": DELTA_A})
    stamped.update(overrides)
    return stamped


def test_a_unit_the_build_never_stamped_has_no_memo_key():
    assert sv.unit_key(canonical(), {}) is None
    assert sv.unit_key(_keyed_unit(content_key=""), {}) is None


def test_the_memo_key_moves_with_the_stamp_the_deltas_and_the_families_the_window_names():
    """Three things reach a fill decision past the content-key stamp — the persisted ink deltas the stamp leaves out and the after font's compiled glyphs for every family the after cells name — and each of them moves the key on its own, while a family the window never names does not."""
    digests = {"qsPea": "p" * 64, "qsAh": "a" * 64, "qsTea_qsOy": "t" * 64, "qsMay": "m" * 64}
    key = sv.unit_key(_keyed_unit(), digests)
    assert key is not None and len(key) == 32
    assert sv.unit_key(_keyed_unit(), digests) == key
    assert sv.unit_key(_keyed_unit(content_key="d" * 64), digests) != key
    assert sv.unit_key(_keyed_unit(ink_deltas={"ss03": DELTA_B}), digests) != key
    assert sv.unit_key(_keyed_unit(ink_deltas=None), digests) != key
    assert sv.unit_key(_keyed_unit(), {**digests, "qsAh": "b" * 64}) != key
    assert sv.unit_key(_keyed_unit(), {**digests, "qsTea_qsOy": "u" * 64}) != key
    assert sv.unit_key(_keyed_unit(), {**digests, "qsMay": "n" * 64}) == key


def test_the_memo_stamp_moves_with_the_rules_files_bytes(tmp_path):
    """A rule's `note` is quoted into every fill it lands, so the memo keys on the file's raw bytes rather than the prose-blind digest the rebuild lanes use: a reword drops the memo, exactly as it re-runs the plumbing."""
    surface = _surface(tmp_path, [canonical("u-1")])
    rules = _write_rules(tmp_path / "rules.yaml", [RULE])
    stamp, digests = sv.memo_environment(rules, surface)
    assert digests == {}
    assert sv.memo_environment(rules, surface) == (stamp, {})
    reworded = _write_rules(tmp_path / "reworded.yaml", [dict(RULE, note=RULE["note"] + " (reworded)")])
    assert sv.memo_environment(reworded, surface)[0] != stamp


def test_the_memo_stamp_reads_the_fonts_when_the_surface_carries_them(tmp_path, slide_fonts):
    """With fonts beside the surface the stamp carries the before font wholesale and the after font's family-blind remainder, and the per-family digests the keys cite come back for every family the after font draws."""
    bare = _surface(tmp_path / "bare", [founding_window()])
    with_fonts = _surface(tmp_path / "fonts", [founding_window()], fonts=slide_fonts)
    rules = _write_rules(tmp_path / "rules.yaml", [SLIDE_RULE])
    bare_stamp, bare_digests = sv.memo_environment(rules, bare)
    font_stamp, font_digests = sv.memo_environment(rules, with_fonts)
    assert bare_stamp != font_stamp
    assert bare_digests == {}
    assert "qsSee" in font_digests and all(len(digest) == 64 for digest in font_digests.values())


def _repo_imports(module, path):
    """Every repo module the file names in an import, a package-relative `from .x import y` resolved against the module's own package, since the validation tree spells its sibling imports that way."""
    import ast

    package = module.rsplit(".", 1)[0]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = f"{package}.{node.module}" if node.level else node.module
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return {name for name in found if name.split(".")[0] == "rebuild"}


def test_the_memo_code_roster_is_this_modules_import_closure():
    """The stamp hashes exactly the repo code a decision runs through: this module and everything it reaches by import, less the key side — the pipeline modules, whose edits move the keys or the stamp itself rather than any decision (`fingerprint` cuts the after-font digest the unit keys cite). A module that starts deciding without being on the roster is the miss this catches; one on the roster the module never reaches is the other."""
    from rebuild.test_plumbing_closure import _module_path

    seen = {}
    queue = ["rebuild.tools.standing_verdicts"]
    while queue:
        module = queue.pop()
        if module in seen or module.startswith("rebuild.pipeline"):
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen[module] = path
        queue.extend(_repo_imports(module, path))
    reached = sorted(str(path.relative_to(sv.ROOT)) for path in seen.values() if path.name != "__init__.py")
    assert reached == sorted(sv.MEMO_CODE_MODULES)
    assert all(path.is_file() for path in sv.memo_code_paths())


def test_a_memo_stamped_for_another_environment_is_empty(tmp_path):
    path = tmp_path / "memo.ndjson.gz"
    unit = _keyed_unit()
    memo = sv.Memo(path, "env-1", {})
    decision = sv.Decision(None, frozenset({RULE["id"]}), frozenset())
    memo.fresh[memo.key_for(unit) or ""] = decision
    assert memo.write([unit]) == 1
    assert sv.Memo.open(path, "env-2", {}).entries == {}
    assert sv.Memo.open(path, "env-1", {}, fresh=True).entries == {}
    assert sv.Memo.open(path, "env-1", {}).entries == {memo.key_for(unit): decision}
    path.write_bytes(b"not a memo")
    assert sv.Memo.open(path, "env-1", {}).entries == {}


def test_a_decision_survives_the_memo_round_trip(tmp_path):
    composed = sv.Composed(("a", "b"), False, "either", "[standing: a + b] note")
    decisions = {
        "1" * 64: sv.Decision(composed, frozenset(), frozenset()),
        "2" * 64: sv.Decision(sv.Composed(("a", "b"), True, None, None), frozenset(), frozenset()),
        "3" * 64: sv.Decision(None, frozenset({"a", "b"}), frozenset({"c"})),
    }
    units = [_keyed_unit(f"k-{index}", content_key=stamp) for index, stamp in enumerate(decisions)]
    memo = sv.Memo(tmp_path / "memo.ndjson.gz", "env", {})
    for unit, decision in zip(units, decisions.values()):
        memo.fresh[memo.key_for(unit) or ""] = decision
    assert memo.write(units) == 3
    reopened = sv.Memo.open(tmp_path / "memo.ndjson.gz", "env", {})
    assert [reopened.entries[memo.key_for(unit) or ""] for unit in units] == list(decisions.values())


def test_the_memo_written_back_is_bounded_to_the_surface_and_keeps_what_it_did_not_read(tmp_path):
    """What goes back to disk is one entry per keyed unit on the surface the run was asked about: a unit that left the surface is dropped, and one still on it whose entry the run never needed — a narrowed run decides only the open units — is carried across unread rather than lost."""
    path = tmp_path / "memo.ndjson.gz"
    stays, leaves = _keyed_unit("s-1", content_key="s" * 64), _keyed_unit("l-1", content_key="l" * 64)
    memo = sv.Memo(path, "env", {})
    for unit in (stays, leaves):
        memo.fresh[memo.key_for(unit) or ""] = sv.Decision(None, frozenset(), frozenset())
    assert memo.write([stays, leaves]) == 2
    assert sv.Memo.open(path, "env", {}).write([stays]) == 1
    assert set(sv.Memo.open(path, "env", {}).entries) == {memo.key_for(stays)}


def test_a_served_unit_is_never_evaluated_and_an_unstamped_one_always_is(tmp_path):
    memo = sv.Memo(tmp_path / "memo.ndjson.gz", "env", {})
    served, unstamped = _keyed_unit("k-1"), canonical("u-1")
    memo.entries[memo.key_for(served) or ""] = sv.Decision(None, frozenset({"served"}), frozenset())
    decider = sv.Decider([RULE], None, memo)
    assert decider.decide(served).matched == {"served"}
    assert decider.decide(served).matched == {"served"}
    assert decider.decide(unstamped).matched == {RULE["id"]}
    assert (decider.served, decider.computed, decider.unkeyed) == (1, 0, 1)
    assert memo.fresh == {}
    fresh = _keyed_unit("k-2", content_key="f" * 64)
    assert decider.decide(fresh).matched == {RULE["id"]}
    assert decider.computed == 1 and memo.fresh == {memo.key_for(fresh): decider.decide(fresh)}


def test_fresh_memo_needs_a_memo(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(tmp_path, monkeypatch, [canonical("u-1")], [], extra=("--fresh-memo",))


def test_main_serves_the_second_run_from_the_memo_and_reports_it(tmp_path, monkeypatch, capsys):
    """The bare tool with `--memo`: a cold run computes and stores, the next run over the same surface and rules serves everything stamped, and both write the same fills and the same report but for the memo's own line."""
    units = [_keyed_unit("k-1"), _keyed_unit("k-2", content_key="e" * 64), canonical("u-3")]
    memo = tmp_path / "memo.ndjson.gz"
    cold = _run_main(tmp_path / "cold", monkeypatch, units, [], extra=("--memo", str(memo)))
    cold_lines = capsys.readouterr().out.splitlines()
    warm = _run_main(tmp_path / "warm", monkeypatch, units, [], extra=("--memo", str(memo)))
    warm_lines = capsys.readouterr().out.splitlines()
    assert cold == warm and [record["unit"] for record in cold["verdicts"]] == ["k-1", "k-2", "u-3"]
    assert "  memo: served 0, computed 2, unkeyed 1; memo.ndjson.gz holds 2 entries" in cold_lines
    assert "  memo: served 2, computed 0, unkeyed 1; memo.ndjson.gz holds 2 entries" in warm_lines
    assert [line for line in cold_lines if not line.startswith("  memo:")] == [
        line for line in warm_lines if not line.startswith("  memo:")
    ]


def test_a_reworded_note_drops_the_memo(tmp_path, monkeypatch, capsys):
    units = [_keyed_unit("k-1")]
    memo = tmp_path / "memo.ndjson.gz"
    _run_main(tmp_path / "first", monkeypatch, units, [], extra=("--memo", str(memo)))
    capsys.readouterr()
    reworded = dict(RULE, note=RULE["note"] + " (reworded)")
    payload = _run_main(
        tmp_path / "second", monkeypatch, units, [], rules_list=(reworded,), extra=("--memo", str(memo))
    )
    lines = capsys.readouterr().out.splitlines()
    assert "  memo: served 0, computed 1, unkeyed 0; memo.ndjson.gz holds 1 entry" in lines
    assert payload["verdicts"][0]["note"].endswith("(reworded)")


MINI = pathlib.Path(sv.ROOT) / "rebuild" / "review" / "fixtures" / "mini"


@pytest.fixture(scope="module")
def mini_surface(tmp_path_factory, mini_bundle):
    """One real build of the frozen mini bundle, with the fonts, the index and every unit's content-key stamp the memo keys on."""
    from rebuild.review.build import build_m1

    out = tmp_path_factory.mktemp("standing-memo") / "surface"
    build_m1(
        out,
        audit_path=MINI / "audit.tsv",
        ledger_path=mini_bundle.ledger,
        subset_dir=MINI,
        after_font=MINI / "M1.otf",
        spec_root=mini_bundle.spec_root,
        jobs=1,
    )
    return out


def _human_units(surface):
    return [
        unit
        for unit in sv.load_units(surface)
        if not unit.get("no_verdict") and unit.get("batch") is not None and unit.get("render_groups") == 1
    ]


def _mini_rules(surface, path):
    """The checked-in rules, whose composed reading credits windows of the mini bundle, plus one ink-delta rule blessing the digest the bundle's human units carry most, so the run also writes single-rule fills."""
    from collections import Counter

    digests = Counter(
        digest for unit in _human_units(surface) for digest in (unit.get("ink_deltas") or {}).values()
    )
    mini_rule = {
        "id": "mini-bundle-ink-delta",
        "verdict": "approve",
        "note": "the mini bundle's commonest ink delta",
        "match": {"after": {"ink_deltas": [digests.most_common(1)[0][0]]}, "except_left": []},
    }
    return _write_rules(path, sv.load_rules(sv.RULES) + [mini_rule])


def _run_over_mini(tmp_path, monkeypatch, surface, rules, verdicts, extra):
    """The CLI over the mini surface: its exit code and the fills file's bytes."""
    stamp = json.loads((surface / "manifest.json").read_text())["generated_at"]
    tmp_path.mkdir(parents=True, exist_ok=True)
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(
        json.dumps({"format": "ams-review-verdicts/1", "manifest_generated_at": stamp, "verdicts": verdicts})
    )
    out = tmp_path / "out.json"
    argv = [str(verdicts_path), "--surface", str(surface), "--rules", str(rules), "--out", str(out), *extra]
    monkeypatch.setattr(sys, "argv", ["standing_verdicts.py", *argv])
    return sv.main(), out.read_bytes()


def test_the_mini_bundle_reaches_a_composed_line_and_the_bundle_local_rule(
    tmp_path, monkeypatch, capsys, mini_surface
):
    """What makes the byte-identity proof below worth anything: over a blank store the run lands both kinds of fill, one a composed reading credits and one a single rule's own line writes, so a memo that served either wrong would show."""
    rules = _mini_rules(mini_surface, tmp_path / "rules.yaml")
    _code, fills = _run_over_mini(tmp_path, monkeypatch, mini_surface, rules, [], ())
    capsys.readouterr()
    notes = [record["note"] for record in json.loads(fills)["verdicts"]]
    assert any(note.startswith("[standing: mini-bundle-ink-delta]") for note in notes)
    assert any(" + " in note.partition("]")[0] for note in notes)


@pytest.mark.parametrize("form", [(), ("--open-only", "--require-reach")])
def test_the_memo_serves_the_mini_bundle_byte_for_byte(tmp_path, monkeypatch, capsys, mini_surface, form):
    """The standing proof that the memo changes nothing but the time: over a real build of the frozen mini bundle, under the checked-in rules and one bundle-local ink-delta rule, with a store holding a reject and an approve, the fills file, the exit code and every report line are byte-identical across a run with no memo, a cold run that writes one, a warm run served entirely from it, and a `--fresh-memo` run that ignores and rewrites it. The warm run's own line says it computed nothing, which is the whole of what the memo buys, in both the bare form and the chain's `--open-only --require-reach` form, whose rollup reads off the same decisions the narrowed pass did."""
    rules = _mini_rules(mini_surface, tmp_path / "rules.yaml")
    stamp = json.loads((mini_surface / "manifest.json").read_text())["generated_at"]
    human = [unit["id"] for unit in _human_units(mini_surface)]
    verdicts = [
        {"unit": human[0], "verdict": "reject", "note": "", "at": stamp},
        {"unit": human[-1], "verdict": "approve", "note": "", "at": stamp},
    ]
    memo = tmp_path / "memo.ndjson.gz"
    runs = {}
    for label, extra in (
        ("bare", ()),
        ("cold", ("--memo", str(memo))),
        ("warm", ("--memo", str(memo))),
        ("fresh", ("--memo", str(memo), "--fresh-memo")),
    ):
        code, fills = _run_over_mini(
            tmp_path / label, monkeypatch, mini_surface, rules, verdicts, form + extra
        )
        lines = capsys.readouterr().out.splitlines()
        report = tuple(line for line in lines if not line.startswith("  memo:"))
        runs[label] = ((code, fills, report), [line for line in lines if line.startswith("  memo:")])
    assert len({outcome for outcome, _memo_lines in runs.values()}) == 1
    keyed = len(human)
    assert runs["bare"][1] == []
    assert runs["cold"][1] == [
        f"  memo: served 0, computed {keyed}, unkeyed 0; memo.ndjson.gz holds {keyed} entries"
    ]
    assert runs["warm"][1] == [
        f"  memo: served {keyed}, computed 0, unkeyed 0; memo.ndjson.gz holds {keyed} entries"
    ]
    assert runs["fresh"][1] == runs["cold"][1]
