from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from quikscript_ir import JoinGlyph
from quikscript_join_analysis import JoinReachability


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
