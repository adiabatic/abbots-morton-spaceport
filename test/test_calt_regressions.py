from pathlib import Path

import uharfbuzz as hb


ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior.otf"


def _shape(text: str) -> list[str]:
    blob = hb.Blob.from_file_path(str(FONT_PATH))
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


def test_qs_see_exit_baseline_selects_qs_ooze_entry_extended_at_baseline():
    assert _shape("\uE65A\uE67E") == [
        "qsSee.exit-baseline",
        "qsOoze.entry-extended-at-baseline",
    ]
