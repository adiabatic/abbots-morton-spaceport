"""surface unit tests over the real six-rune spec: cell enumeration under feature configurations, binding resolution per the explicit-cells > side-bindings > base order, the side-binding disagreement error, and the E-ANCHOR convention gate."""

import textwrap
import warnings

import pytest

from rebuild.pipeline import surface
from rebuild.pipeline.model import CellId
from rebuild.pipeline.spec_load import SpecError, SpecWarning, load_default_spec
from rebuild.test_spec_load import load_tmp_spec


@pytest.fixture(scope="module")
def spec():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        return load_default_spec()


def cells_as_tuples(spec, rune, features=frozenset()):
    return {(c.stance, c.entry, c.exit) for c in surface.enumerate_cells(spec, rune, features)}


def test_qsit_default_cells(spec):
    assert cells_as_tuples(spec, "qsIt") == {
        ("hapax", "x-height", "baseline"),
        ("hapax", "x-height", None),
        ("hapax", "baseline", "x-height"),
        ("hapax", "baseline", None),
        ("hapax", None, "x-height"),
        ("hapax", None, "baseline"),
        ("hapax", None, None),
    }


def test_qsit_ss04_unlock_grants_pass_through(spec):
    default = cells_as_tuples(spec, "qsIt")
    with_ss04 = cells_as_tuples(spec, "qsIt", frozenset({"ss04"}))
    assert with_ss04 - default == {("hapax", "baseline", "baseline")}
    tagged = dict(surface.enumerate_cells_with_unlocks(spec, "qsIt", frozenset({"ss04"})))
    granted = tagged[CellId("qsIt", "hapax", "baseline", "baseline", ())]
    assert len(granted) == 1
    assert granted[0].feature == "ss04"
    assert granted[0].when is None


def test_qstea_cells_per_configuration(spec):
    default = cells_as_tuples(spec, "qsTea")
    assert default == {
        ("full", "baseline", None),
        ("full", "top", "baseline"),
        ("full", "top", None),
        ("full", None, "baseline"),
        ("full", None, None),
        ("half", "x-height", None),
        ("half", None, "x-height"),
        ("half", None, None),
    }
    assert cells_as_tuples(spec, "qsTea", frozenset({"ss05"})) - default == {("full", "baseline", "baseline")}
    assert cells_as_tuples(spec, "qsTea", frozenset({"ss02"})) - default == {
        ("full", "x-height", "baseline"),
        ("full", "x-height", None),
    }
    tagged = dict(surface.enumerate_cells_with_unlocks(spec, "qsTea", frozenset({"ss03"})))
    half_entry = tagged[CellId("qsTea", "half", "x-height", None, ())]
    assert [unlock.feature for unlock in half_entry] == ["ss03"]


def test_qsmay_and_ligature_cells(spec):
    assert cells_as_tuples(spec, "qsMay") == {
        ("loop", "baseline", "x-height"),
        ("loop", "baseline", None),
        ("loop", "x-height", None),
        ("loop", None, "x-height"),
        ("loop", None, None),
        ("grounded-loop", "x-height", "baseline"),
        ("grounded-loop", "x-height", None),
        ("grounded-loop", None, "baseline"),
        ("grounded-loop", None, None),
    }
    assert cells_as_tuples(spec, "qsTea_qsOy") == {
        ("hapax", None, "baseline"),
        ("hapax", None, None),
    }


def test_qspea_pairings_and_dip_cells(spec):
    cells = cells_as_tuples(spec, "qsPea")
    assert ("half", "x-height", "x-height") in cells
    assert ("half", "x-height", "y6") in cells
    assert ("full", "baseline", "baseline") not in cells
    assert ("half", "y6", "y6") in cells


def test_resolve_explicit_cell_bindings(spec):
    plan = surface.resolve_cell(spec, CellId("qsMay", "loop", "x-height", None, ()))
    assert plan.bitmap == "pulled-back-stubless"
    assert plan.entry_x == 2
    assert plan.entry_stub is None and plan.exit_stub is None
    assert plan.safety_checks == (("exit", "x-height"),)
    plan = surface.resolve_cell(spec, CellId("qsPea", "half", "x-height", "x-height", ()))
    assert plan.bitmap == "half-dips-both-sides"
    assert (plan.entry_x, plan.exit_x) == (0, 4)
    plan = surface.resolve_cell(spec, CellId("qsOy", "hapax", "x-height", "baseline", ()))
    assert plan.bitmap == "open-on-the-left"
    assert (plan.entry_x, plan.exit_x) == (0, 5)


def test_resolve_side_bindings_and_overrides(spec):
    plan = surface.resolve_cell(spec, CellId("qsMay", "grounded-loop", "x-height", "baseline", ()))
    assert plan.bitmap == "pulled-back-grounded"
    assert plan.entry_x == 2
    assert plan.exit_x == 4
    # The token-less exit-none cell is the boundary rendering: the exit was never declined, so the base drawing (connector ink and all) stands. ·May's loop exit no longer withdraws — its pulled-back binding is entry-side only now — so there is no exit ex-bind rendering.
    plan = surface.resolve_cell(spec, CellId("qsMay", "loop", None, None, ()))
    assert plan.bitmap is None
    plan = surface.resolve_cell(spec, CellId("qsMay", "loop", "baseline", "x-height", ()))
    assert plan.bitmap is None
    assert (plan.entry_x, plan.exit_x) == (0, 5)


def test_resolve_withdrawal_safe_obligations(spec):
    plan = surface.resolve_cell(spec, CellId("qsIt", "hapax", "x-height", None, ()))
    assert plan.bitmap is None
    assert plan.safety_checks == (("exit", "baseline"), ("exit", "x-height"))
    plan = surface.resolve_cell(spec, CellId("qsMay", "grounded-loop", "x-height", None, ()))
    assert plan.safety_checks == (("exit", "baseline"),)


def test_resolve_stubs_and_oddities(spec):
    plan = surface.resolve_cell(spec, CellId("qsPea", "half", None, "x-height", ()))
    assert plan.bitmap is None
    assert plan.exit_stub is not None and plan.exit_stub.cols == (3,)
    assert plan.exit_ink_y == 6
    assert plan.safety_checks == (("exit", "y6"),)
    bitmap = surface.resolved_cell_bitmap(spec, plan)
    assert bitmap.row_for_y(5) == "   #"
    plan = surface.resolve_cell(spec, CellId("qsPea", "full", "x-height", "baseline", ()))
    assert plan.entry_stub is not None and plan.entry_stub.cols == (0,)
    assert surface.resolved_cell_bitmap(spec, plan).row_for_y(5) == "#  #"
    plan = surface.resolve_cell(spec, CellId("qsTea", "half", None, "x-height", ()))
    assert plan.entry_curs_only == (0, 8)


def test_unlock_only_cells_resolve_with_their_record(spec):
    plan = surface.resolve_cell(spec, CellId("qsTea", "full", "x-height", None, ()))
    assert plan.entry_x == 0
    assert plan.unlock is not None and plan.unlock.feature == "ss02"
    assert [
        unlock.feature
        for unlock in surface.unlocks_for_cell(spec, CellId("qsTea", "full", "x-height", None, ()))
    ] == [
        "ss02",
        "ss03",
        "ss03",
        "ss03",
    ]
    assert surface.unlocks_for_cell(spec, CellId("qsIt", "hapax", "x-height", "baseline", ())) == ()
    assert len(surface.unlocks_for_cell(spec, CellId("qsIt", "hapax", "baseline", "baseline", ()))) == 1


def test_unknown_cell_rejected(spec):
    with pytest.raises(SpecError, match="never offers"):
        surface.resolve_cell(spec, CellId("qsIt", "hapax", "top", None, ()))
    with pytest.raises(SpecError, match="no feature configuration"):
        surface.unlocks_for_cell(spec, CellId("qsIt", "hapax", "x-height", "x-height", ()))
    assert surface.unlocks_for_cell(spec, CellId("qsIt", "hapax", "x-height", "baseline", ("locked",))) == ()


def test_effective_rows_synthesize_unlock_anchors(spec):
    entries, exits, granted = surface.effective_rows(spec, "qsTea", "full", frozenset({"ss02"}))
    assert entries["x-height"].x == 0
    assert [unlock.feature for unlock in granted[("entry", "x-height")]] == ["ss02"]
    entries, _exits, _granted = surface.effective_rows(spec, "qsTea", "full", frozenset())
    assert "x-height" not in entries


DISAGREEING_RUNE = textwrap.dedent("""\
    rune: qsMay
    codepoint: 0xE665
    ductus:
      hapax: |
        A loop.
    stances:
      hapax:
        way: hapax
        bitmap: ["##", "##", "##", "##", "##", "##"]
        bitmaps:
          entry-form: {bitmap: ["# ", "# ", "# ", "# ", "# ", "##"]}
          exit-form: {bitmap: [" #", " #", " #", " #", " #", "##"]}
        surface:
          entries:
            x-height: {x: 0, joined: entry-form}
          exits:
            baseline: {x: 2, withdrawal: exit-form}
            x-height: {x: 2, withdrawal: safe}
    """)


def test_side_binding_disagreement_is_a_build_error(tmp_path):
    spec = load_tmp_spec(tmp_path, {"qsMay": DISAGREEING_RUNE})
    with pytest.raises(SpecError) as caught:
        surface.resolve_cell(spec, CellId("qsMay", "hapax", "x-height", "x-height", ()))
    message = str(caught.value)
    assert "disagreeing side bindings" in message
    assert "entry-form" in message and "exit-form" in message
    assert "CellId(rune='qsMay', stance='hapax', entry='x-height', exit='x-height'" in message


def test_explicit_cells_row_settles_the_disagreement(tmp_path):
    text = DISAGREEING_RUNE.replace(
        "      exits:",
        "      cells:\n      - {entry: x-height, exit: x-height, bitmap: entry-form}\n      exits:",
    )
    spec = load_tmp_spec(tmp_path, {"qsMay": text})
    plan = surface.resolve_cell(spec, CellId("qsMay", "hapax", "x-height", "x-height", ()))
    assert plan.bitmap == "entry-form"


def test_anchor_conventions_hold_on_real_data(spec):
    assert surface.check_anchor_conventions(spec) == ()


def test_anchor_convention_drift_is_flagged(tmp_path):
    text = textwrap.dedent("""\
        rune: qsIt
        codepoint: 0xE670
        ductus:
          hapax: |
            A vertical stroke.
        stances:
          hapax:
            way: hapax
            bitmap: [" #", " #", " #", " #", " #", " #"]
            surface:
              entries:
                baseline: {x: 0}
              exits:
                baseline: {x: 2, withdrawal: safe}
        """)
    spec = load_tmp_spec(tmp_path, {"qsIt": text})
    issues = surface.check_anchor_conventions(spec)
    assert issues
    assert all("E-ANCHOR" in issue.message for issue in issues)
    assert any("entry anchor x=0 drifts from the convention value 1" in issue.message for issue in issues)
    flagged = text.replace("{x: 0}", "{x: 0, x_off_convention: true}").replace(
        "{x: 2,", "{x: 3, x_off_convention: true,"
    )
    spec = load_tmp_spec(tmp_path, {"qsIt": flagged})
    assert surface.check_anchor_conventions(spec) == ()


def test_cell_binding_watchdog_warns_on_dead_rows(tmp_path):
    text = DISAGREEING_RUNE.replace(
        "      exits:",
        "      pairings:\n"
        "        never: [{entry: x-height, exit: x-height}]\n"
        "      cells:\n"
        "      - {entry: x-height, exit: x-height, bitmap: entry-form}\n"
        "      exits:",
    )
    spec = load_tmp_spec(tmp_path, {"qsMay": text})
    with pytest.warns(SpecWarning, match="matches no enumerable cell"):
        surface.check_cell_bindings(spec)


def test_real_cell_bindings_all_match(spec):
    with warnings.catch_warnings():
        warnings.simplefilter("error", SpecWarning)
        surface.check_cell_bindings(spec)
