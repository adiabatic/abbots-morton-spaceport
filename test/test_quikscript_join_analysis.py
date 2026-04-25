from pathlib import Path
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from quikscript_ir import JoinGlyph
from quikscript_join_analysis import JoinReachability, validate_join_consistency


_DEFAULTS: dict[str, Any] = {
    "name": "qsTest",
    "base_name": "qsTest",
    "family": "qsTest",
    "sequence": (),
    "traits": frozenset(),
    "modifiers": (),
    "compat_assertions": frozenset(),
    "entry": (),
    "entry_curs_only": (),
    "exit": (),
    "after": (),
    "before": (),
    "not_after": (),
    "not_before": (),
    "reverse_upgrade_from": (),
    "preferred_over": (),
    "word_final": False,
    "is_contextual": False,
    "is_entry_variant": False,
    "entry_suffix": None,
    "exit_suffix": None,
    "extended_entry_suffix": None,
    "extended_exit_suffix": None,
    "entry_restriction_y": None,
    "is_noentry": False,
    "bitmap": (),
    "y_offset": 0,
    "advance_width": None,
    "extend_entry_after": None,
    "extend_exit_before": None,
    "noentry_after": (),
    "extend_exit_no_entry": False,
}


def _make_glyph(**overrides: Any) -> JoinGlyph:
    return JoinGlyph(**(_DEFAULTS | overrides))


def test_single_family_one_form_populates_base_and_fwd_replacement():
    qs_bay = _make_glyph(
        name="qsBay",
        base_name="qsBay",
        family="qsBay",
        exit=((4, 0),),
    )
    glyph_meta = {"qsBay": qs_bay}

    reach = JoinReachability.from_join_glyphs(glyph_meta)

    assert reach.base_to_variants == {"qsBay": frozenset({"qsBay"})}
    assert reach.fwd_replacements == {"qsBay": {0: "qsBay"}}
    assert reach.bk_replacements == {}
    assert reach.pair_overrides == {}
    assert reach.fwd_pair_overrides == {}
    assert reach.gated_pair_overrides == {}
    assert reach.gated_fwd_pair_overrides == {}
    assert reach.ligatures == ()
    assert reach.word_final_pairs == {}
    assert reach.entry_classes == {}


def test_pair_with_forward_sub_routes_override_to_fwd_pair_overrides():
    qs_pea = _make_glyph(
        name="qsPea",
        base_name="qsPea",
        family="qsPea",
        exit=((4, 5),),
    )
    qs_pea_before_tea = _make_glyph(
        name="qsPea.before-tea",
        base_name="qsPea",
        family="qsPea",
        modifiers=("before-tea",),
        exit=((4, 0),),
        before=("qsTea",),
    )
    glyph_meta = {"qsPea": qs_pea, "qsPea.before-tea": qs_pea_before_tea}

    reach = JoinReachability.from_join_glyphs(glyph_meta)

    assert reach.fwd_replacements == {"qsPea": {5: "qsPea"}}
    assert reach.fwd_pair_overrides == {
        "qsPea": (("qsPea.before-tea", ("qsTea",), ()),)
    }
    assert "qsPea.before-tea" not in {
        name for ys in reach.fwd_replacements.values() for name in ys.values()
    }
    assert reach.base_to_variants == {
        "qsPea": frozenset({"qsPea", "qsPea.before-tea"})
    }
    assert reach.gated_fwd_pair_overrides == {}


def test_ligature_consuming_second_component_is_recorded():
    qs_out = _make_glyph(
        name="qsOut",
        base_name="qsOut",
        family="qsOut",
        exit=((4, 0),),
    )
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 0),),
        exit=((4, 5),),
    )
    qs_out_qs_tea = _make_glyph(
        name="qsOut_qsTea",
        base_name="qsOut_qsTea",
        family=None,
        sequence=("qsOut", "qsTea"),
        exit=((4, 5),),
    )
    glyph_meta = {
        "qsOut": qs_out,
        "qsTea": qs_tea,
        "qsOut_qsTea": qs_out_qs_tea,
    }

    reach = JoinReachability.from_join_glyphs(glyph_meta)

    assert reach.ligatures == (("qsOut_qsTea", ("qsOut", "qsTea")),)
    assert reach.base_to_variants["qsOut"] == frozenset({"qsOut"})
    assert reach.base_to_variants["qsTea"] == frozenset({"qsTea"})
    assert reach.base_to_variants["qsOut_qsTea"] == frozenset({"qsOut_qsTea"})
    assert reach.fwd_replacements["qsOut"] == {0: "qsOut"}
    assert reach.fwd_replacements["qsTea"] == {5: "qsTea"}
    assert reach.bk_replacements["qsTea"] == {0: "qsTea"}
    assert reach.entry_classes[0] == frozenset({"qsTea"})


def test_reachability_view_is_immutable():
    qs_bay = _make_glyph(
        name="qsBay",
        base_name="qsBay",
        family="qsBay",
        exit=((4, 0),),
    )
    reach = JoinReachability.from_join_glyphs({"qsBay": qs_bay})

    try:
        reach.base_to_variants["qsBay"].add("intruder")  # pyright: ignore[reportAttributeAccessIssue]
    except AttributeError:
        pass
    else:
        raise AssertionError("frozenset should reject .add()")

    try:
        reach.glyph_meta["qsBay"] = qs_bay  # pyright: ignore[reportIndexIssue]
    except TypeError:
        pass
    else:
        raise AssertionError("MappingProxyType should reject item assignment")


def test_validator_accepts_known_good_pair():
    qs_pea = _make_glyph(
        name="qsPea",
        base_name="qsPea",
        family="qsPea",
        exit=((4, 0),),
    )
    qs_pea_before_tea = _make_glyph(
        name="qsPea.before-tea",
        base_name="qsPea",
        family="qsPea",
        modifiers=("before-tea",),
        exit=((4, 0),),
        before=("qsTea",),
    )
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 0),),
        exit=((4, 5),),
    )

    validate_join_consistency(
        {
            "qsPea": qs_pea,
            "qsPea.before-tea": qs_pea_before_tea,
            "qsTea": qs_tea,
        }
    )


def test_forward_intent_with_no_matching_backward_entry_raises():
    qs_pea = _make_glyph(
        name="qsPea",
        base_name="qsPea",
        family="qsPea",
        exit=((4, 5),),
    )
    qs_pea_before_tea = _make_glyph(
        name="qsPea.before-tea",
        base_name="qsPea",
        family="qsPea",
        modifiers=("before-tea",),
        exit=((4, 0),),
        before=("qsTea",),
    )
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 5),),
        exit=((4, 5),),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsPea": qs_pea,
                "qsPea.before-tea": qs_pea_before_tea,
                "qsTea": qs_tea,
            }
        )
    message = str(exc_info.value)
    assert "qsPea.before-tea" in message
    assert "y=0" in message
    assert "qsTea" in message


def test_backward_intent_with_no_matching_forward_exit_raises():
    qs_pea = _make_glyph(
        name="qsPea",
        base_name="qsPea",
        family="qsPea",
        exit=((4, 5),),
    )
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 5),),
        exit=((4, 5),),
    )
    qs_tea_after_pea = _make_glyph(
        name="qsTea.after-pea",
        base_name="qsTea",
        family="qsTea",
        modifiers=("after-pea",),
        entry=((0, 0),),
        exit=((4, 5),),
        after=("qsPea",),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsPea": qs_pea,
                "qsTea": qs_tea,
                "qsTea.after-pea": qs_tea_after_pea,
            }
        )
    message = str(exc_info.value)
    assert "qsTea.after-pea" in message
    assert "qsPea" in message
    assert "y=0" in message


def test_ligature_only_path_with_mismatched_anchor_raises():
    qs_out = _make_glyph(
        name="qsOut",
        base_name="qsOut",
        family="qsOut",
        exit=((4, 5),),
    )
    qs_out_before_tea = _make_glyph(
        name="qsOut.before-tea",
        base_name="qsOut",
        family="qsOut",
        modifiers=("before-tea",),
        exit=((4, 0),),
        before=("qsTea",),
    )
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 5),),
        exit=((4, 5),),
    )
    qs_out_qs_tea = _make_glyph(
        name="qsOut_qsTea",
        base_name="qsOut_qsTea",
        family=None,
        sequence=("qsOut", "qsTea"),
        entry=((0, 5),),
        exit=((4, 5),),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsOut": qs_out,
                "qsOut.before-tea": qs_out_before_tea,
                "qsTea": qs_tea,
                "qsOut_qsTea": qs_out_qs_tea,
            }
        )
    message = str(exc_info.value)
    assert "qsOut.before-tea" in message
    assert "y=0" in message
    assert "qsTea" in message


def test_noentry_after_strip_creates_a_mismatch():
    """When the only entry-bearing variant of T loses its entry under F,
    the validator must surface the resulting mismatch."""
    qs_see = _make_glyph(
        name="qsSee",
        base_name="qsSee",
        family="qsSee",
        exit=((4, 0),),
    )
    qs_see_before_out_tea = _make_glyph(
        name="qsSee.before-out-tea",
        base_name="qsSee",
        family="qsSee",
        modifiers=("before-out-tea",),
        exit=((4, 0),),
        before=("qsOut_qsTea",),
    )
    qs_out_qs_tea = _make_glyph(
        name="qsOut_qsTea",
        base_name="qsOut_qsTea",
        family="qsOut_qsTea",
        sequence=("qsOut", "qsTea"),
        entry=((0, 0),),
        exit=((4, 5),),
        noentry_after=("qsSee",),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsSee": qs_see,
                "qsSee.before-out-tea": qs_see_before_out_tea,
                "qsOut_qsTea": qs_out_qs_tea,
            }
        )
    message = str(exc_info.value)
    assert "qsSee.before-out-tea" in message
    assert "qsOut_qsTea" in message


def test_ss_gated_swap_adds_a_mismatch():
    qs_pea = _make_glyph(
        name="qsPea",
        base_name="qsPea",
        family="qsPea",
        exit=((4, 0),),
    )
    qs_pea_before_tea = _make_glyph(
        name="qsPea.before-tea",
        base_name="qsPea",
        family="qsPea",
        modifiers=("before-tea",),
        exit=((4, 0),),
        before=("qsTea",),
    )
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 0),),
        exit=((4, 5),),
    )
    qs_tea_after_pea_ss03 = _make_glyph(
        name="qsTea.after-pea-ss03",
        base_name="qsTea",
        family="qsTea",
        modifiers=("after-pea-ss03",),
        entry=(),
        exit=((4, 5),),
        after=("qsPea",),
        gate_feature="ss03",
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsPea": qs_pea,
                "qsPea.before-tea": qs_pea_before_tea,
                "qsTea": qs_tea,
                "qsTea.after-pea-ss03": qs_tea_after_pea_ss03,
            }
        )
    message = str(exc_info.value)
    assert "ss03" in message
    assert "qsPea.before-tea" in message
    assert "qsTea" in message


# Regression-witness tests — each fixture distills the kind of steady-state
# join mismatch that the named historical commit was working around. For the
# four FEA-only fixes (every commit below except 075d485, which mutated YAML),
# the pre-fix YAML was structurally consistent and the bug lived in the FEA
# emitter; these fixtures express the hypothetical YAML where the FEA-side
# guard had not yet been introduced. The validator is designed to make that
# situation impossible.


def test_regression_075d485_fee_exits_xheight_before_utter():
    """075d485 — Fix ·Fee→·Utter and ·See→·At cursive connections.

    Pre-fix: qsFee.exit-xheight declared ``before: qsUtter`` and exits at y=5,
    but qsUtter's only y=5 entry-bearing variant is a backward-pair override
    (``after: qsAh, qsTea``) that cannot select after qsFee. Modeled here as
    qsUtter having no y=5 entry at all — the validator is family-level and
    surfaces the same missing-y mismatch.
    """
    qs_fee = _make_glyph(
        name="qsFee",
        base_name="qsFee",
        family="qsFee",
        exit=((4, 0),),
    )
    qs_fee_exit_xheight = _make_glyph(
        name="qsFee.exit-xheight",
        base_name="qsFee",
        family="qsFee",
        modifiers=("exit-xheight",),
        exit=((4, 5),),
        before=("qsUtter",),
    )
    qs_utter = _make_glyph(
        name="qsUtter",
        base_name="qsUtter",
        family="qsUtter",
        entry=((0, 0),),
        exit=((4, 0),),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsFee": qs_fee,
                "qsFee.exit-xheight": qs_fee_exit_xheight,
                "qsUtter": qs_utter,
            }
        )
    message = str(exc_info.value)
    assert "qsFee.exit-xheight" in message
    assert "qsUtter" in message
    assert "y=5" in message


def test_regression_8c7c486_no_alt_after_it_and_vie_overreaches():
    """8c7c486 — Fix backward after matching for incompatible joins.

    Pre-fix (FEA-only): qsNo.alt.after-it-and-vie was selected even when the
    predecessor's exit didn't actually match its entry y, because backward
    ``after:`` expansion was family-level. Fixture: qsNo.alt.after-it-and-vie
    enters at y=0 listing ``after: [qsIt, qsVie]``, but neither family has
    any reachable exit at y=0.
    """
    qs_it = _make_glyph(
        name="qsIt",
        base_name="qsIt",
        family="qsIt",
        exit=((4, 5),),
    )
    qs_vie = _make_glyph(
        name="qsVie",
        base_name="qsVie",
        family="qsVie",
        exit=((4, 5),),
    )
    qs_no = _make_glyph(
        name="qsNo",
        base_name="qsNo",
        family="qsNo",
        entry=((0, 5),),
        exit=((4, 5),),
    )
    qs_no_alt_after_it_and_vie = _make_glyph(
        name="qsNo.alt.after-it-and-vie",
        base_name="qsNo",
        family="qsNo",
        traits=frozenset({"alt"}),
        modifiers=("after-it-and-vie",),
        entry=((0, 0),),
        exit=((4, 5),),
        after=("qsIt", "qsVie"),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsIt": qs_it,
                "qsVie": qs_vie,
                "qsNo": qs_no,
                "qsNo.alt.after-it-and-vie": qs_no_alt_after_it_and_vie,
            }
        )
    message = str(exc_info.value)
    assert "qsNo.alt.after-it-and-vie" in message
    assert "qsIt" in message or "qsVie" in message
    assert "y=0" in message


def test_regression_d641641_tea_x_must_not_pick_joining_x():
    """d641641 — ·Tea·X shouldn't pick a joining X when they don't join anyway.

    Pre-fix (FEA-only): qsTea.exit-baseline could preselect an X variant that,
    after later substitutions, no longer carried the matching y=0 entry.
    Fixture: qsTea.exit-baseline exits y=0 listing ``before: qsExample``, but
    qsExample's only variant has ``noentry_after: [qsTea]``, so the entry is
    stripped specifically when qsTea is the predecessor — no reachable y=0
    entry remains.
    """
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 0),),
        exit=((4, 5),),
    )
    qs_tea_exit_baseline = _make_glyph(
        name="qsTea.exit-baseline",
        base_name="qsTea",
        family="qsTea",
        modifiers=("exit-baseline",),
        entry=((0, 0),),
        exit=((4, 0),),
        before=("qsExample",),
    )
    qs_example = _make_glyph(
        name="qsExample",
        base_name="qsExample",
        family="qsExample",
        entry=((0, 0),),
        exit=((4, 5),),
        noentry_after=("qsTea",),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsTea": qs_tea,
                "qsTea.exit-baseline": qs_tea_exit_baseline,
                "qsExample": qs_example,
            }
        )
    message = str(exc_info.value)
    assert "qsTea.exit-baseline" in message
    assert "qsExample" in message
    assert "y=0" in message


def test_regression_714a2d5_tea_oy_ligature_after_tea():
    """714a2d5 — ·Tea·Oy also counts as a ·Tea you can't join to at the baseline.

    Pre-fix (FEA-only): the qsTea_qsOy ligature consumes qsTea, and the
    ligature's effective entry sits at x-height while predecessors that joined
    a bare baseline qsTea expected y=0. Fixture: qsX.before-tea-oy exits y=0
    listing the ligature directly as the right context, but the only reachable
    variant of family qsTea_qsOy enters at y=5.
    """
    qs_x = _make_glyph(
        name="qsX",
        base_name="qsX",
        family="qsX",
        exit=((4, 5),),
    )
    qs_x_before_tea_oy = _make_glyph(
        name="qsX.before-tea-oy",
        base_name="qsX",
        family="qsX",
        modifiers=("before-tea-oy",),
        exit=((4, 0),),
        before=("qsTea_qsOy",),
    )
    qs_tea = _make_glyph(
        name="qsTea",
        base_name="qsTea",
        family="qsTea",
        entry=((0, 0),),
        exit=((4, 5),),
    )
    qs_oy = _make_glyph(
        name="qsOy",
        base_name="qsOy",
        family="qsOy",
        entry=((0, 5),),
    )
    qs_tea_qs_oy = _make_glyph(
        name="qsTea_qsOy",
        base_name="qsTea_qsOy",
        family="qsTea_qsOy",
        sequence=("qsTea", "qsOy"),
        entry=((0, 5),),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsX": qs_x,
                "qsX.before-tea-oy": qs_x_before_tea_oy,
                "qsTea": qs_tea,
                "qsOy": qs_oy,
                "qsTea_qsOy": qs_tea_qs_oy,
            }
        )
    message = str(exc_info.value)
    assert "qsX.before-tea-oy" in message
    assert "qsTea_qsOy" in message
    assert "y=0" in message


def test_regression_77ca573_ing_before_may_thaw_ligature():
    """77ca573 — Have ·May·Thaw look right after ·-ing.

    Pre-fix (FEA-only): forward calt on qsIng targeting the qsMay+qsThaw
    ligature didn't agree on entry height. Fixture: qsIng.exit-extended exits
    y=5 listing ``before: qsMay_qsThaw``, but the May+Thaw ligature has only a
    y=0 entry — no y=5 entry on any reachable candidate.
    """
    qs_ing = _make_glyph(
        name="qsIng",
        base_name="qsIng",
        family="qsIng",
        exit=((4, 0),),
    )
    qs_ing_exit_extended = _make_glyph(
        name="qsIng.exit-extended",
        base_name="qsIng",
        family="qsIng",
        modifiers=("exit-extended",),
        exit=((4, 5),),
        before=("qsMay_qsThaw",),
    )
    qs_may = _make_glyph(
        name="qsMay",
        base_name="qsMay",
        family="qsMay",
        entry=((0, 0),),
        exit=((4, 0),),
    )
    qs_thaw = _make_glyph(
        name="qsThaw",
        base_name="qsThaw",
        family="qsThaw",
        entry=((0, 0),),
    )
    qs_may_qs_thaw = _make_glyph(
        name="qsMay_qsThaw",
        base_name="qsMay_qsThaw",
        family="qsMay_qsThaw",
        sequence=("qsMay", "qsThaw"),
        entry=((0, 0),),
    )

    with pytest.raises(ValueError, match="Join consistency mismatches") as exc_info:
        validate_join_consistency(
            {
                "qsIng": qs_ing,
                "qsIng.exit-extended": qs_ing_exit_extended,
                "qsMay": qs_may,
                "qsThaw": qs_thaw,
                "qsMay_qsThaw": qs_may_qs_thaw,
            }
        )
    message = str(exc_info.value)
    assert "qsIng.exit-extended" in message
    assert "qsMay_qsThaw" in message
    assert "y=5" in message
