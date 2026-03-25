from pathlib import Path

import pytest
import uharfbuzz as hb

ROOT = Path(__file__).resolve().parent.parent

PROPORTIONAL_FONTS = [
    ROOT / "test" / "AbbotsMortonSpaceportSansJunior.otf",
    ROOT / "test" / "AbbotsMortonSpaceportSansSenior.otf",
]


@pytest.fixture(params=PROPORTIONAL_FONTS, ids=lambda path: path.stem)
def font(request):
    blob = hb.Blob.from_file_path(str(request.param))
    face = hb.Face(blob)
    return hb.Font(face)


def shape(font, text):
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [
        {
            "name": font.glyph_to_string(info.codepoint),
            "x_offset": pos.x_offset,
            "y_offset": pos.y_offset,
        }
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
    ]


@pytest.mark.parametrize(
    ("text", "mark_name"),
    [
        ("j\u0301", "uni0301"),
        ("j\u0308", "uni0308"),
        ("\u0135", "uni0302"),
    ],
)
def test_lowercase_j_uses_dotless_base_for_top_marks(font, text, mark_name):
    glyphs = shape(font, text)

    assert [glyph["name"] for glyph in glyphs] == ["dotlessj", mark_name]
    assert (glyphs[1]["x_offset"], glyphs[1]["y_offset"]) != (0, 0)


@pytest.mark.parametrize("text", ["J\u0302", "\u0134"])
def test_uppercase_j_top_marks_are_positioned(font, text):
    glyphs = shape(font, text)

    if len(glyphs) == 1:
        assert glyphs[0]["name"] != "J"
        return

    assert [glyph["name"] for glyph in glyphs] == ["J", "uni0302"]
    assert (glyphs[1]["x_offset"], glyphs[1]["y_offset"]) != (0, 0)
