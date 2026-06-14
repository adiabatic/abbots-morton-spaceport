"""Geometry unit tests over the four families' real data (via rebuild.pipeline.fixtures), plus synthetic coverage for the adjustment operations the M1 alphabet never reaches (trim, bind at settlement level)."""

import pytest

from rebuild.pipeline import geometry
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId, CellPlan, GlyphRecord, Stub, parse_adjustment


@pytest.fixture(scope="module")
def spec():
    return mini_spec()


def _realize(spec, rune, stance, entry, exit, adjustments=(), **plan_kwargs):
    cell = CellId(rune, stance, entry, exit, tuple(adjustments))
    return geometry.realize(spec, CellPlan(cell=cell, **plan_kwargs))


class TestParseAdjustment:
    def test_tokens(self):
        assert parse_adjustment("locked") == ("locked", None, None)
        assert parse_adjustment("en-ext-1") == ("ext", "en", 1)
        assert parse_adjustment("ex-con-2") == ("con", "ex", 2)
        assert parse_adjustment("en-trim-1") == ("trim", "en", 1)
        assert parse_adjustment("ex-bind-pulled-back") == ("bind", "ex", "pulled-back")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_adjustment("before-day")


class TestDisplayName:
    def test_bare_default_stance(self, spec):
        assert geometry.display_name(spec, CellId("qsIt", "bar", None, None, ())) == "qsIt"

    def test_isolated_cell_keeps_the_base_drawing_exit(self, spec):
        assert geometry.isolated_cell(spec, "qsMay") == CellId("qsMay", "loop", None, "x-height", ())
        assert geometry.isolated_cell(spec, "qsIt") == CellId("qsIt", "bar", None, None, ())
        assert geometry.display_name(spec, CellId("qsMay", "loop", None, "x-height", ())) == "qsMay"

    def test_withdrawn_exit_gets_a_distinct_marker(self, spec):
        assert geometry.display_name(spec, CellId("qsMay", "loop", None, None, ())) == "qsMay.ex-wd"
        assert (
            geometry.display_name(spec, CellId("qsMay", "loop", "x-height", None, ())) == "qsMay.en-y5.ex-wd"
        )

    def test_anchors_render_as_y_values(self, spec):
        name = geometry.display_name(spec, CellId("qsIt", "bar", "x-height", "baseline", ()))
        assert name == "qsIt.en-y5.ex-y0"

    def test_non_default_stance_is_named(self, spec):
        name = geometry.display_name(spec, CellId("qsMay", "grounded-loop", None, "baseline", ()))
        assert name == "qsMay.grounded-loop.ex-y0"

    def test_adjustments_appended(self, spec):
        name = geometry.display_name(spec, CellId("qsMay", "loop", "baseline", "x-height", ("en-ext-1",)))
        assert name == "qsMay.en-y0.ex-y5.en-ext-1"

    def test_long_names_hash_overflow(self, spec):
        cell = CellId(
            "qsMay", "grounded-loop", "x-height", "baseline", tuple(f"en-ext-{n}" for n in range(9))
        )
        name = geometry.display_name(spec, cell)
        assert len(name.encode()) <= 63


class TestRealizeBase:
    def test_bare_may_keeps_base_bitmap_and_intrinsic_exit(self, spec):
        record = geometry.realize(spec, CellPlan(cell=CellId("qsMay", "loop", None, "x-height", ())))
        assert record.bitmap == spec.runes["qsMay"].stances["loop"].bitmap.rows
        assert record.exit == (5, 5)
        assert record.entry is None

    def test_entered_baseline_cell(self, spec):
        record = geometry.realize(spec, CellPlan(cell=CellId("qsMay", "loop", "baseline", "x-height", ())))
        assert record.entry == (0, 0)
        assert record.exit == (5, 5)

    def test_withdrawn_exit_renders_bound_form(self, spec):
        plan = CellPlan(cell=CellId("qsMay", "loop", "x-height", None, ()), bitmap="pulled-back", entry_x=3)
        record = geometry.realize(spec, plan)
        assert record.bitmap == spec.runes["qsMay"].stances["loop"].bitmaps["pulled-back"].rows
        assert record.entry == (3, 5)
        assert record.exit is None

    def test_grounded_joined_anchor_override(self, spec):
        plan = CellPlan(
            cell=CellId("qsMay", "grounded-loop", "x-height", "baseline", ()),
            bitmap="pulled-back-grounded",
        )
        record = geometry.realize(spec, plan)
        assert record.entry == (2, 5)  # joined_x travels with the bound form
        assert record.exit == (4, 0)


class TestStubArithmetic:
    def test_pea_entry_dip_appears_when_live(self, spec):
        plan = CellPlan(
            cell=CellId("qsPea", "full", "x-height", None, ()),
            entry_stub=Stub(cols=(0,), inks_when="joined"),
        )
        record = geometry.realize(spec, plan)
        y5_row = record.bitmap[3]
        assert y5_row == "#  #"
        assert record.entry == (0, 5)

    def test_pea_dip_absent_when_withdrawn(self, spec):
        plan = CellPlan(
            cell=CellId("qsPea", "full", None, "baseline", ()),
            entry_stub=Stub(cols=(0,), inks_when="joined"),
        )
        record = geometry.realize(spec, plan)
        assert record.bitmap[3] == "   #"

    def test_pea_half_exit_dip(self, spec):
        plan = CellPlan(
            cell=CellId("qsPea", "half", None, "x-height", ()),
            exit_stub=Stub(cols=(3,), inks_when="joined"),
            exit_ink_y=6,
        )
        record = geometry.realize(spec, plan)
        assert record.bitmap[3] == "   #"
        assert record.exit == (4, 5)

    def test_pea_both_dips_explicit_cell(self, spec):
        plan = CellPlan(
            cell=CellId("qsPea", "half", "x-height", "x-height", ()),
            bitmap="half-dips-both-sides",
            exit_ink_y=6,
        )
        record = geometry.realize(spec, plan)
        assert record.bitmap[3] == "#  #"
        assert record.entry == (0, 5)
        assert record.exit == (4, 5)

    def test_joined_stub_polarity_removes_ink_when_live(self, spec):
        plan = CellPlan(
            cell=CellId("qsPea", "full", "x-height", None, ()),
            entry_stub=Stub(cols=(3,), inks_when="withdrawn"),
        )
        record = geometry.realize(spec, plan)
        assert record.bitmap[3] == "    "


class TestExtensions:
    def test_exit_extension_adds_connector_and_shifts_anchor(self, spec):
        record = _realize(spec, "qsMay", "loop", None, "x-height", ["ex-ext-1"])
        assert record.bitmap[0] == "   ###"
        assert record.exit == (6, 5)

    def test_entry_extension_prepends_and_shifts_exit(self, spec):
        record = _realize(spec, "qsMay", "loop", "baseline", "x-height", ["en-ext-1"])
        assert record.bitmap[5] == "##### "
        assert record.entry == (0, 0)
        assert record.exit == (6, 5)

    def test_double_extension_matches_prototype_geometry(self, spec):
        record = _realize(spec, "qsMay", "loop", "baseline", "x-height", ["en-ext-1", "ex-ext-1"])
        assert record.bitmap == (
            "    ###",
            "   #   ",
            "   #   ",
            "  #    ",
            "  #    ",
            "#####  ",
            "  #  # ",
            "  #  # ",
            "   ##  ",
        )
        assert record.entry == (0, 0)
        assert record.exit == (7, 5)

    def test_it_exit_extension(self, spec):
        record = _realize(spec, "qsIt", "bar", "x-height", "baseline", ["ex-ext-1"])
        assert record.bitmap == ("# ", "# ", "# ", "# ", "# ", "##")
        assert record.exit == (2, 0)

    def test_contract_removes_connector_and_pulls_anchor(self, spec):
        record = _realize(spec, "qsMay", "loop", None, "x-height", ["ex-con-1"])
        assert record.bitmap[0] == "   # "
        assert record.exit == (4, 5)

    def test_trim_blanks_ink_but_keeps_anchor(self, spec):
        record = _realize(spec, "qsIt", "bar", "x-height", "baseline", ["en-trim-1"])
        assert record.bitmap[0] == " "
        assert record.entry == (0, 5)
        assert "entry" in record.convention_exempt

    def test_bind_substitutes_sibling_and_recomputes_anchors(self, spec):
        record = _realize(spec, "qsMay", "loop", "x-height", None, ["en-bind-pulled-back-stubless"])
        assert record.bitmap == spec.runes["qsMay"].stances["loop"].bitmaps["pulled-back-stubless"].rows
        assert record.entry == (2, 5)

    def test_locked_drops_the_entry(self, spec):
        record = _realize(spec, "qsIt", "bar", "x-height", "baseline", ["locked"])
        assert record.entry is None
        assert record.exit == (1, 0)


class TestSeamGap:
    def test_it_into_may_baseline_gap_zero(self, spec):
        left = _realize(spec, "qsIt", "bar", None, "baseline")
        right = _realize(spec, "qsMay", "loop", "baseline", "x-height")
        assert geometry.seam_gap(left, right, "baseline") == 0

    def test_extended_seam_still_gap_zero(self, spec):
        left = _realize(spec, "qsIt", "bar", "x-height", "baseline", ["ex-ext-1"])
        right = _realize(spec, "qsMay", "loop", "baseline", "x-height")
        assert geometry.seam_gap(left, right, "baseline") == 0

    def test_nonzero_gap_detected(self):
        left = GlyphRecord(name="left", bitmap=("#",), y_offset=0, exit=(1, 0))
        right = GlyphRecord(name="right", bitmap=(" #",), y_offset=0, entry=(0, 0))
        assert geometry.seam_gap(left, right, "baseline") == 1

    def test_overlap_is_negative(self):
        left = GlyphRecord(name="left", bitmap=("##",), y_offset=0, exit=(1, 0))
        right = GlyphRecord(name="right", bitmap=("#",), y_offset=0, entry=(0, 0))
        assert geometry.seam_gap(left, right, "baseline") == -1

    def test_requires_live_sides(self):
        bare = GlyphRecord(name="bare", bitmap=("#",), y_offset=0)
        with pytest.raises(geometry.GeometryError):
            geometry.seam_gap(bare, bare, "baseline")


class TestWithdrawalSafety:
    def test_bar_exit_is_safe(self, spec):
        record = _realize(spec, "qsIt", "bar", None, None)
        assert geometry.verify_withdrawal_safe(record, "exit", "baseline")
        assert geometry.verify_withdrawal_safe(record, "exit", "x-height")

    def test_may_connector_is_unsafe(self, spec):
        record = geometry.realize(spec, CellPlan(cell=CellId("qsMay", "loop", None, "x-height", ())))
        assert not geometry.verify_withdrawal_safe(record, "exit", "x-height")

    def test_empty_row_is_safe(self, spec):
        record = geometry.realize(spec, CellPlan(cell=CellId("qsPea", "half", None, None, ())))
        assert geometry.verify_withdrawal_safe(record, "exit", "x-height")

    def test_pea_half_y6_exit_is_safe(self, spec):
        record = geometry.realize(spec, CellPlan(cell=CellId("qsPea", "half", None, "y6", ())))
        assert geometry.verify_withdrawal_safe(record, "exit", "y6")
