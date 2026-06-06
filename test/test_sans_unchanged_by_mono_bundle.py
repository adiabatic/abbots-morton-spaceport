"""Guard that bundling Departure Mono into the mono font didn't leak into the proportional fonts.

The mono build now carries the whole of Departure Mono — its glyphs and its GDEF/GSUB/GPOS. The Sans (Junior/Senior) families derive their own Latin and marks from the `.prop` data instead, so none of Departure's distinctive glyph names should appear in them. The Sans fonts keep their own GSUB/GPOS (cursive joining, kerning), so we assert only that the Departure-specific glyphs are absent — not that Sans lacks layout tables.
"""

from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"

# Glyph names that belong to Departure Mono and have no counterpart in the Sans `.prop`-derived design.
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
