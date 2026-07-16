"""model.py contract tests: the frozen identity tuples, bitmap row addressing, and the shared naming helpers."""

import pytest

from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import (
    Bitmap,
    CellId,
    Settled,
    feature_config_token,
    locked_glyph_name,
    marker_glyph_name,
    parse_adjustment,
)


class TestIdentity:
    def test_cell_ids_are_hashable_value_objects(self):
        a = CellId("qsIt", "hapax", "x-height", "baseline", ("ex-ext-1",))
        b = CellId("qsIt", "hapax", "x-height", "baseline", ("ex-ext-1",))
        assert a == b
        assert hash(a) == hash(b)
        assert len({a, b}) == 1

    def test_settled_carries_seam_and_extension(self):
        settled = Settled(cell=CellId("qsMay", "loop", None, "x-height", ()), seam="x-height", extension=1)
        assert settled.seam == "x-height"
        assert settled.extension == 1


class TestBitmap:
    def test_row_for_y_with_offset(self):
        bitmap = Bitmap(("a", "b", "c"), y_offset=-1)
        assert bitmap.row_for_y(1) == "a"
        assert bitmap.row_for_y(0) == "b"
        assert bitmap.row_for_y(-1) == "c"
        assert bitmap.row_for_y(2) is None

    def test_width_is_the_longest_row(self):
        assert Bitmap(("##", "#")).width == 2


class TestNaming:
    def test_config_tokens(self):
        assert feature_config_token(frozenset()) == "default"
        assert feature_config_token({"ss03", "ss02"}) == "ss02+ss03"

    def test_marker_and_locked_names(self):
        assert marker_glyph_name("qsIt", {"ss04"}) == "qsIt.ss04"
        assert locked_glyph_name("qsTea.ss03") == "qsTea.ss03.noentry"

    def test_adjustment_grammar_is_closed(self):
        with pytest.raises(ValueError):
            parse_adjustment("ss03")
        with pytest.raises(ValueError):
            parse_adjustment("en-noentry")


class TestFixtureSpec:
    def test_all_six_runes_modeled(self):
        spec = mini_spec()
        assert sorted(spec.runes) == ["qsIt", "qsMay", "qsOy", "qsPea", "qsTea", "qsTea_qsOy"]
        assert spec.runes["qsTea_qsOy"].sequence == ("qsTea", "qsOy")
        assert spec.registry.heights == {"baseline": 0, "x-height": 5, "y6": 6, "top": 8}

    def test_bitmaps_match_the_authored_rune_files(self):
        spec = mini_spec()
        assert spec.runes["qsIt"].stances["hapax"].bitmap.rows == ("#",) * 6
        assert spec.runes["qsMay"].stances["loop"].bitmap.rows[0] == "   ##"
        assert spec.runes["qsPea"].stances["half"].bitmaps["half-dips-both-sides"].rows[3] == "#  #"
