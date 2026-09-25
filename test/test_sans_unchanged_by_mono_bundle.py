"""Check that Departure Mono's glyphs, which the mono font bundles, do not appear in the Sans fonts.

The mono font carries Departure Mono's glyphs and its GDEF, GSUB, and GPOS tables. The Sans fonts (Junior and Senior) build their Latin letters and marks from the `.prop` data and have their own GSUB and GPOS for joining and kerning. So the tests check only that a sample of Departure-only glyph names is absent from Sans, and that Mono has those glyphs and its GDEF, GSUB, and GPOS tables.
"""

from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"

# Departure Mono glyph names that the Sans `.prop` data has no counterpart for.
DEPARTURE_ONLY_GLYPHS = [
    "gravecomb",
    "acutecomb",
    "a.sc",
    "z.sc",
    "ampersand.ss01",
    "zero.numr",
    "zero.dnom",
]

SANS_FONTS = [
    "AbbotsMortonSpaceportSansJunior-Regular.otf",
    "AbbotsMortonSpaceportSansSenior-Regular.otf",
]

MONO_FONT = "AbbotsMortonSpaceportMono-Regular.otf"


def _glyph_order(filename: str) -> set[str]:
    return set(TTFont(SITE_DIR / filename).getGlyphOrder())


@pytest.mark.parametrize("filename", SANS_FONTS)
def test_departure_glyphs_absent_from_sans(filename):
    glyphs = _glyph_order(filename)
    leaked = [name for name in DEPARTURE_ONLY_GLYPHS if name in glyphs]
    assert not leaked, f"Departure-only glyphs leaked into {filename}: {leaked}"


def test_departure_glyphs_present_in_mono():
    glyphs = _glyph_order(MONO_FONT)
    missing = [name for name in DEPARTURE_ONLY_GLYPHS if name not in glyphs]
    assert not missing, f"Departure-only glyphs missing from {MONO_FONT}: {missing}"


def test_mono_has_layout_tables():
    font = TTFont(SITE_DIR / MONO_FONT)
    for table in ("GDEF", "GSUB", "GPOS"):
        assert table in font, f"{MONO_FONT} is missing {table}"
