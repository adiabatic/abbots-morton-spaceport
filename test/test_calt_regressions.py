from functools import lru_cache
from pathlib import Path
import uharfbuzz as hb
import sys


ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior.otf"
sys.path.insert(0, str(ROOT / "tools"))

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set


def _shape(text: str) -> list[str]:
    blob = hb.Blob.from_file_path(str(FONT_PATH))
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


@lru_cache(maxsize=1)
def _compiled_meta():
    data = load_glyph_data(ROOT / "glyph_data")
    return compile_glyph_set(data, "senior").glyph_meta


def test_qs_see_exit_baseline_right_before_qs_ooze():
    assert _shape("\uE65A\uE67E") == [
        "qsSee.exit-baseline-right",
        "qsOoze",
    ]


def test_qs_no_alt_requires_a_compatible_it_exit():
    assert _shape("\uE65F\uE670\uE666") == [
        "qsJay",
        "qsIt.exit-xheight",
        "qsNo",
    ]


def test_qs_low_entry_extended_requires_a_compatible_see_exit():
    assert _shape("\uE665\uE670\uE65A\uE667") == [
        "qsMay.exit-extended",
        "qsIt.entry-xheight",
        "qsSee",
        "qsLow",
    ]


def test_zwnj_keeps_qs_it_entryless_while_still_joining_qs_zoo():
    glyphs = _shape("\uE653\u200C\uE670\uE65B\uE675\uE668")

    assert glyphs[0:2] == ["qsDay", "space"]
    assert glyphs[3:] == ["qsZoo", "qsI.exit-extended", "qsRoe"]

    meta = _compiled_meta()
    it_meta = meta[glyphs[2]]
    zoo_meta = meta[glyphs[3]]

    assert not it_meta.entry
    assert not it_meta.entry_curs_only
    assert {anchor[1] for anchor in it_meta.exit} == {5}
    assert {anchor[1] for anchor in it_meta.exit} & {anchor[1] for anchor in zoo_meta.entry} == {5}
