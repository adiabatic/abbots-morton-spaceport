"""Integration test for `compile_font.build_mini_font`: compile six glyphs of four mini-spec letters and a hand-written settlement lookup through `tools/build_font.build_font`'s `senior_fea=` path, then shape and classify the result. The old pipeline is only read, never modified."""

import threading

import pytest

from rebuild.pipeline import compile_font, emit_gpos, geometry, pack_gsub
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId, CellPlan
from rebuild.tools import console


@pytest.fixture(scope="module")
def inputs():
    spec = mini_spec()
    cells = [
        CellId("qsIt", "sole", None, None, ()),
        CellId("qsIt", "sole", None, "baseline", ()),
        CellId("qsMay", "loop", None, "x-height", ()),
        CellId("qsMay", "loop", "baseline", "x-height", ()),
        CellId("qsPea", "full", "y6", None, ()),
    ]
    glyphs = {cell: geometry.realize(spec, CellPlan(cell=cell)) for cell in cells}
    tea_half = CellId("qsTea", "half", None, "x-height", ())
    glyphs[tea_half] = geometry.realize(spec, CellPlan(cell=tea_half, entry_curs_only=(0, 8)))
    names = {cell: record.name for cell, record in glyphs.items()}
    fea = (
        "lookup t_settle {\n"
        f"    sub qsIt' qsMay by {names[cells[1]]};\n"
        f"    sub {names[cells[1]]} qsMay' by {names[cells[3]]};\n"
        "} t_settle;\n"
        "feature calt {\n    lookup t_settle;\n} calt;\n" + emit_gpos.emit_gpos(glyphs, spec=spec)
    )
    return glyphs, fea, names


@pytest.fixture(scope="module")
def built(inputs, tmp_path_factory):
    glyphs, fea, names = inputs
    out_path = tmp_path_factory.mktemp("m1-font") / "M1Test.otf"
    compile_font.build_mini_font(glyphs, fea, out_path)
    return out_path, names


class TestBuildMiniFont:
    def test_font_and_sidecars_exist(self, built):
        out_path, _names = built
        assert out_path.exists()
        assert out_path.with_suffix(".fea").exists()

    def test_shaping_matches_the_settlement_rules(self, built):
        out_path, names = built
        from rebuild.pipeline.conform import Shaper

        shaper = Shaper(out_path)
        shaped = shaper.shape(chr(0xE670) + chr(0xE665), frozenset())
        got = [glyph["name"] for glyph in shaped]
        assert got == [
            names[CellId("qsIt", "sole", None, "baseline", ())],
            names[CellId("qsMay", "loop", "baseline", "x-height", ())],
        ]

    def test_curs_anchors_close_the_junction(self, built):
        out_path, _names = built
        from rebuild.validation.classify import JunctionClassifier

        classifier = JunctionClassifier(out_path)
        shaped_names = []
        from rebuild.pipeline.conform import Shaper

        shaper = Shaper(out_path)
        for glyph in shaper.shape(chr(0xE670) + chr(0xE665), frozenset()):
            shaped_names.append(glyph["name"])
        assert classifier.classify(shaped_names[0], shaped_names[1]) == "y0"

    def test_a_group_over_the_limit_raises_before_the_save(self, inputs, tmp_path, monkeypatch):
        """A format-2 chained-context subtable whose rule tables pass `GROUP_RULE_BYTES_LIMIT` stops the build between the pack and `font.save`, where hb.repack would raise `RepackerError` and fontTools' fallback serializer would recompile GSUB for many minutes. The pack here is followed by a group of `GROUP_RULE_BYTES_LIMIT // 8` copies of one rule of fourteen bytes (four counts, one lookahead class and one SubstLookupRecord), so the group passes the limit."""
        from fontTools.ttLib import TTFont

        glyphs, fea, _names = inputs
        pack_font = pack_gsub.pack_font

        def pack_and_add_a_group(font, min_subtables=pack_gsub.MIN_SUBTABLES):
            packed = pack_font(font, min_subtables)
            lookup = next(lookup for lookup in font["GSUB"].table.LookupList.Lookup if lookup.LookupType == 6)
            group = pack_gsub._Group()
            group.add(
                pack_gsub.LogicalRule(
                    backtrack=(),
                    input=frozenset({"qsIt"}),
                    lookahead=(frozenset({"qsMay"}),),
                    records=((0, 0),),
                )
            )
            group.rules *= compile_font.GROUP_RULE_BYTES_LIMIT // 8
            order = {glyph: index for index, glyph in enumerate(font.getGlyphOrder())}
            lookup.SubTable.append(pack_gsub._format2_subtable(group, order))
            lookup.SubTableCount = len(lookup.SubTable)
            return packed

        def save(*_args, **_kwargs):
            raise AssertionError("the build reached font.save")

        monkeypatch.setattr(pack_gsub, "pack_font", pack_and_add_a_group)
        monkeypatch.setattr(TTFont, "save", save)
        out_path = tmp_path / "M1Oversize.otf"
        with pytest.raises(compile_font.GroupTooLargeError, match="over the 64,000 bytes"):
            compile_font.build_mini_font(glyphs, fea, out_path)
        assert not out_path.exists()

    def test_another_threads_lines_reach_stdout_while_the_font_compiles(
        self, inputs, tmp_path, monkeypatch, capsys
    ):
        """run_m1's table-only branch writes its `[t]` and `[phase]` lines from its own thread while the glyph chain compiles the font, and `console` resolves `sys.stdout` when each line is written, so such a line reaches stdout only while the compile leaves the process's stdout in place. The probe thread writes its line from inside the `build_font` call, and `build_font(quiet=True)` adds nothing of its own."""
        import build_font

        real_build_font = build_font.build_font

        def build_beside_a_thread(*args, **kwargs):
            thread = threading.Thread(target=console.timing, args=("probe", 0.0))
            thread.start()
            thread.join()
            return real_build_font(*args, **kwargs)

        monkeypatch.setattr(build_font, "build_font", build_beside_a_thread)
        glyphs, fea, _names = inputs
        compile_font.build_mini_font(glyphs, fea, tmp_path / "M1Probe.otf")
        assert capsys.readouterr().out == "[t] probe 0.0s\n"
