"""Integration tests for `compile_font.build_font`. The mini font: six glyphs of four mini-spec letters and a hand-written settlement lookup compiled through `tools/build_font.build_font`'s `senior_fea=` path, then shaped and classified. The full font: the checked-in runes' raw glyphs and variation glyphs compiled with the non-Quikscript part (`compile_font.load_font_sources`), then compared glyph for glyph with the shipped Senior that `make all` writes under site/, and its Bold with its Regular. The old pipeline is only read, never modified."""

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from rebuild.pipeline import compile_font, emit_gpos, geometry, pack_gsub, readback, run_m1
from rebuild.pipeline.fixtures import mini_spec
from rebuild.pipeline.model import CellId, CellPlan, GlyphRecord
from rebuild.pipeline.spec_load import REPO_ROOT, load_default_spec
from rebuild.tools import console
from rebuild.tools.site_fonts import font_paths

SHIPPED_SENIOR = font_paths(REPO_ROOT)[0]
NAME_IDS = (0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14, 19)
# A rune FEA that substitutes nothing, so both builds carry a GSUB the Latin features are written ahead of.
NOOP_FEA = "lookup t_noop {\n    sub qsPea' qsTea by qsPea;\n} t_noop;\nfeature calt {\n    lookup t_noop;\n} calt;\n"


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
    compile_font.build_font(glyphs, fea, out_path)
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
            compile_font.build_font(glyphs, fea, out_path)
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
        compile_font.build_font(glyphs, fea, tmp_path / "M1Probe.otf")
        assert capsys.readouterr().out == "[t] probe 0.0s\n"


def _open(path) -> Any:
    from fontTools.ttLib import TTFont

    return TTFont(str(path))


def _rune_owned(name: str) -> bool:
    """A glyph the runes draw. The angle parentheses carry the `qs` prefix but no rune: the full font draws them from glyph_data/quikscript.yaml as the old Senior does."""
    return name.startswith(("qs", "uniE6")) and not name.startswith("qsAngleParen")


def _drawing(font, name):
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    glyph_set = font.getGlyphSet()
    pen = DecomposingRecordingPen(glyph_set)
    glyph_set[name].draw(pen)
    return tuple(pen.value), font["hmtx"][name]


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    """The checked-in runes' raw glyphs, the glyphs their variation sequences select, and the namer dot pair, compiled twice over a rune FEA that substitutes nothing: once with the non-Quikscript part and Bold beside it, and once without, as the tests' mini fonts are."""
    spec = load_default_spec()
    bare, _copies, _ss10 = run_m1.mint_raw_glyphs(spec)
    variation = run_m1.mint_variation_glyphs(spec, {})
    glyphs = {**bare, **variation.glyphs, **run_m1.namer_dot_glyphs()}
    sources = compile_font.load_font_sources()
    directory = tmp_path_factory.mktemp("full-font")
    regular = compile_font.build_font(
        glyphs,
        NOOP_FEA,
        directory / "M1.otf",
        sources=sources,
        variation_sequences=variation.sequences,
        bold_path=directory / "M1-Bold.otf",
    )
    mini = compile_font.build_font(glyphs, NOOP_FEA, directory / "Mini.otf")
    return SimpleNamespace(
        spec=spec,
        sources=sources,
        variation=variation,
        regular=regular,
        bold=directory / "M1-Bold.otf",
        mini=mini,
    )


class TestTheFullFont:
    def test_the_cmap_is_the_shipped_seniors(self, full):
        """Every code point the shipped Senior maps, and no other, less the letters an alphabet switch leaves out."""
        shipped = set(_open(SHIPPED_SENIOR).getBestCmap())
        letters = {rune.codepoint for rune in full.spec.runes.values() if rune.codepoint is not None}
        quikscript = {code for code in shipped if 0xE650 <= code <= 0xE67E and code not in (0xE66E, 0xE66F)}
        assert set(_open(full.regular).getBestCmap()) == (shipped - quikscript) | letters

    def test_every_glyph_the_runes_do_not_draw_is_the_shipped_seniors(self, full):
        """The Latin, numeral, punctuation, symbol and Shavian glyphs, the angle parentheses, `space`, `uni200C` and the namer dot pair draw with the outline, advance and side bearing the shipped Senior gives them."""
        shipped, built = _open(SHIPPED_SENIOR), _open(full.regular)
        names = {name for name in built.getGlyphOrder() if not _rune_owned(name)}
        assert names == {name for name in shipped.getGlyphOrder() if not _rune_owned(name)}
        assert [name for name in sorted(names) if _drawing(built, name) != _drawing(shipped, name)] == []
        assert built["hmtx"]["space"][0] == 7 * emit_gpos.PIXEL

    def test_the_name_table_is_the_shipped_seniors(self, full):
        shipped, built = _open(SHIPPED_SENIOR), _open(full.regular)
        assert {i: built["name"].getDebugName(i) for i in NAME_IDS} == {
            i: shipped["name"].getDebugName(i) for i in NAME_IDS
        }

    def test_the_latin_features_come_ahead_of_the_rune_features(self, full):
        """`ccmp`, `kern` and `mark` come from glyph_data/, written ahead of the rune FEA as the old build writes them, so their lookups take the first places in each lookup list; the kern rule that names ·He is left out."""
        font = _open(full.regular)
        features = {
            tag: {
                record.FeatureTag: list(record.Feature.LookupListIndex)
                for record in font[tag].table.FeatureList.FeatureRecord
            }
            for tag in ("GSUB", "GPOS")
        }
        assert set(features["GSUB"]) == {"calt", "ccmp"}
        assert set(features["GPOS"]) == {"kern", "mark"}
        assert max(features["GSUB"]["ccmp"]) < min(features["GSUB"]["calt"])
        assert full.sources.dropped_kerning == ("he-before-noentry",)
        assert "leave-space-for-j" in full.sources.kerning

    def test_a_kern_rule_that_names_a_rune_glyph_is_left_out(self):
        assert compile_font._names_a_rune({"left_family": ["qsHe"], "right_group": "noentry", "value": -3})
        assert compile_font._names_a_rune({"left": ["f"], "right": ["a", "qsPea"], "value": -1})
        assert not compile_font._names_a_rune({"right": ["j"], "value": -1})
        assert not compile_font._names_a_rune(
            {"left": ["f"], "right": ["a"], "except_left": ["qsPea"], "value": -1}
        )

    def test_the_senior_tightening_is_the_mini_fonts(self, full):
        """Each rune glyph advances as it does in a font without the non-Quikscript part, so the one pixel the Senior build trims from a `qs` glyph's advance comes off the same way in both."""
        built, mini = _open(full.regular), _open(full.mini)
        runes = [name for name in mini.getGlyphOrder() if name.startswith("qs")]
        assert runes
        assert [name for name in runes if built["hmtx"][name] != mini["hmtx"][name]] == []

    def test_the_midword_glyphs_are_the_latin_letters_and_digits(self, full):
        midword = set(full.sources.midword_glyphs())
        assert {"a", "Z", "zero", "nine", "aacute", "oslash"} <= midword
        assert not midword & {"qsPea", "periodcentered", "space", "uni200C", "hyphen", "u10450"}

    def test_bold_shares_regulars_layout_and_overstrikes_its_outlines(self, full):
        divergences: list[str] = []
        checked = readback.verify_bold(full.regular, full.bold, divergences=divergences)
        assert divergences == []
        regular, bold = _open(full.regular), _open(full.bold)
        inked = [name for name in regular.getGlyphOrder() if _drawing(regular, name)[0]]
        assert checked["overstruck"] == len(inked) > 400
        assert {tag: regular.reader[tag] for tag in compile_font.LAYOUT_TABLES if tag in regular.reader} == {
            tag: bold.reader[tag] for tag in compile_font.LAYOUT_TABLES if tag in bold.reader
        }
        assert regular["OS/2"].usMaxContext == bold["OS/2"].usMaxContext
        assert (regular["OS/2"].fsSelection, bold["OS/2"].fsSelection) == (0x40, 0x20)
        assert bold["name"].getDebugName(6) == "AbbotsMortonSpaceportSansSenior-Bold"

    def test_bold_s_check_finds_an_outline_that_is_not_overstruck(self, full, tmp_path):
        """A Bold whose ·Pea is drawn as Regular's fails `readback.verify_bold`, as does one whose GSUB differs from Regular's by a byte."""
        regular = _open(full.regular)
        bold = _open(full.bold)
        source = regular["CFF "].cff.topDictIndex[0].CharStrings["qsPea"]
        source.decompile()
        target = bold["CFF "].cff.topDictIndex[0].CharStrings["qsPea"]
        target.program = list(source.program)
        target.bytecode = None
        bold["GSUB"].table.LookupList.Lookup[0].LookupFlag = 8
        corrupted = tmp_path / "M1-Bold.otf"
        bold.save(str(corrupted))
        divergences: list[str] = []
        readback.verify_bold(full.regular, corrupted, divergences=divergences)
        assert "bold: Bold's GSUB is not byte for byte Regular's" in divergences
        assert "bold: Bold's qsPea is not Regular's outline widened 25 units to the right" in divergences

    def test_the_variation_sequences_reach_both_cmaps(self, full):
        for path in (full.regular, full.bold):
            assert readback._cmap14_sequences(_open(path)) == full.variation.sequences

    def test_a_font_source_with_quikscript_families_is_refused(self, tmp_path):
        (tmp_path / "glyph_data").mkdir()
        (tmp_path / "glyph_data" / "extra.yaml").write_text(
            "glyph_families:\n  qsPea: {mono: {bitmap: []}}\n"
        )
        (tmp_path / "glyph_data" / "quikscript.yaml").write_text(
            (REPO_ROOT / "glyph_data" / "quikscript.yaml").read_text()
        )
        with pytest.raises(compile_font.FontSourceError, match="Quikscript families"):
            compile_font.load_font_sources(tmp_path)


class TestTheVariationGlyphs:
    """`run_m1.mint_variation_glyphs` over the checked-in registry and runes."""

    def test_each_sequence_selects_its_stance_unjoined_and_anchorless(self, full):
        drawn = {(row["rune"], row["stance"]): row["drawn"] for row in full.variation.report}
        assert drawn[("qsNo", "flipped")] == "unjoined cell"
        assert drawn[("qsTea", "half")] == "unjoined cell"
        # ·Utter's alternate requires an exit, so it has no unjoined cell, and the sequence takes its base drawing.
        assert drawn[("qsUtter", "alternate")] == "base drawing"
        for cell, record in full.variation.glyphs.items():
            assert (cell.entry, cell.exit, cell.adjustments) == (None, None, ())
            assert (record.entry, record.exit, record.entry_curs_only) == (None, None, None)
        names = {name for mapping in full.variation.sequences.values() for name in mapping.values()}
        assert names == {record.name for record in full.variation.glyphs.values()}
        assert full.variation.sequences[0xFE00][0xE67A] == "qsUtter.alternate"

    def test_a_sequence_whose_cell_settlement_reaches_names_the_settled_glyph(self, full):
        cell = CellId("qsNo", "flipped", None, None, ())
        settled = GlyphRecord(name="qsNo.flipped", bitmap=())
        variation = run_m1.mint_variation_glyphs(full.spec, {cell: settled})
        assert cell not in variation.glyphs
        assert variation.sequences[0xFE00][0xE666] == "qsNo.flipped"
        assert {row["drawn"] for row in variation.report if row["rune"] == "qsNo"} == {"settled cell"}

    def test_only_the_named_font_carries_a_selector(self, full):
        junior = run_m1.mint_variation_glyphs(full.spec, {}, font="junior")
        assert set(junior.sequences) == {0xFE00}
