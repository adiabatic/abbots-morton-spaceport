"""Tests for the Regular and Bold static fonts of all three families.

- The fonts are static CFF, with no `fvar`, `gvar`, `HVAR`, or `CFF2` table.
- RIBBI style linking: `OS/2.fsSelection` sets the Regular bit (0x40) or the Bold bit (0x20) and not the other, and `head.macStyle` sets the Bold bit (0x01) on Bold fonts only.
- The name table gives a family's Regular and Bold the same family name (ID 1), the subfamily names "Regular" and "Bold" (ID 2), and PostScript names ending in the style (ID 6).
- The ink of ·Pea, ·Tea, and ·Ah is `PIXEL_SIZE // 2` font units wider on the right in Bold, at the same height, and every glyph keeps its Regular advance width.
"""

from pathlib import Path

import pytest
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"

FAMILIES = [
    "AbbotsMortonSpaceportMono",
    "AbbotsMortonSpaceportSansJunior",
    "AbbotsMortonSpaceportSansSenior",
]
STYLES = ["Regular", "Bold"]

PIXEL_SIZE = 50
OVERSTRIKE = PIXEL_SIZE // 2  # 25 font units


def _path(family: str, style: str) -> Path:
    return SITE_DIR / f"{family}-{style}.otf"


@pytest.fixture(params=[(f, s) for f in FAMILIES for s in STYLES], ids=lambda p: f"{p[0]}-{p[1]}")
def font_pair(request: pytest.FixtureRequest) -> tuple[str, str, TTFont]:
    family, style = request.param
    return family, style, TTFont(_path(family, style))


@pytest.fixture(params=FAMILIES)
def family(request: pytest.FixtureRequest) -> tuple[str, TTFont, TTFont]:
    family = request.param
    return (
        family,
        TTFont(_path(family, "Regular")),
        TTFont(_path(family, "Bold")),
    )


class TestStaticCFFStructure:
    def test_no_fvar(self, font_pair):
        _, _, font = font_pair
        assert "fvar" not in font

    def test_no_gvar(self, font_pair):
        _, _, font = font_pair
        assert "gvar" not in font

    def test_no_hvar(self, font_pair):
        _, _, font = font_pair
        assert "HVAR" not in font

    def test_has_cff_not_cff2(self, font_pair):
        _, _, font = font_pair
        assert "CFF " in font
        assert "CFF2" not in font


class TestRIBBIStyleLinking:
    def test_fs_selection(self, font_pair):
        _, style, font = font_pair
        fs = font["OS/2"].fsSelection
        if style == "Regular":
            assert fs & 0x40, "Regular should have fsSelection REGULAR bit (0x40)"
            assert not (fs & 0x20), "Regular should not have fsSelection BOLD bit"
        else:
            assert fs & 0x20, "Bold should have fsSelection BOLD bit (0x20)"
            assert not (fs & 0x40), "Bold should not have fsSelection REGULAR bit"

    def test_mac_style(self, font_pair):
        _, style, font = font_pair
        mac = font["head"].macStyle
        if style == "Regular":
            assert not (mac & 0x01), "Regular should not have macStyle Bold bit"
        else:
            assert mac & 0x01, "Bold should have macStyle Bold bit"

    def test_family_name_matches(self, family):
        _, regular, bold = family
        assert regular["name"].getDebugName(1) == bold["name"].getDebugName(1)

    def test_style_name_differs(self, family):
        _, regular, bold = family
        assert regular["name"].getDebugName(2) == "Regular"
        assert bold["name"].getDebugName(2) == "Bold"

    def test_postscript_name_suffix(self, font_pair):
        _, style, font = font_pair
        ps_name = font["name"].getDebugName(6)
        assert ps_name.endswith(f"-{style}")


class TestBoldOverstrikeWidens:
    """Bold draws each pixel `OVERSTRIKE` units wider to the right and leaves the pixel grid, advance widths, and glyph heights unchanged."""

    def _bounds(self, font: TTFont, glyph_name: str):
        glyph_set = font.getGlyphSet()
        pen = BoundsPen(glyph_set)
        glyph_set[glyph_name].draw(pen)
        return pen.bounds  # (xMin, yMin, xMax, yMax) or None

    @pytest.mark.parametrize("glyph_name", ["qsPea", "qsTea", "qsAh"])
    def test_bold_glyph_wider_by_overstrike(self, family, glyph_name):
        _, regular, bold = family
        reg_bounds = self._bounds(regular, glyph_name)
        bold_bounds = self._bounds(bold, glyph_name)
        assert reg_bounds is not None
        assert bold_bounds is not None
        reg_x_min, reg_y_min, reg_x_max, reg_y_max = reg_bounds
        bold_x_min, bold_y_min, bold_x_max, bold_y_max = bold_bounds
        assert bold_x_min == reg_x_min
        assert bold_x_max - reg_x_max == OVERSTRIKE
        assert bold_y_min == reg_y_min
        assert bold_y_max == reg_y_max

    def test_advance_widths_match(self, family):
        _, regular, bold = family
        reg_hmtx = regular["hmtx"].metrics
        bold_hmtx = bold["hmtx"].metrics
        for glyph_name, (reg_aw, _) in reg_hmtx.items():
            bold_aw, _ = bold_hmtx[glyph_name]
            assert reg_aw == bold_aw, (
                f"Advance width differs for {glyph_name}: " f"Regular={reg_aw}, Bold={bold_aw}"
            )
