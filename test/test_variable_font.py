"""Tests for variable font structure (wght axis, named instances, bold metrics)."""

from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"

VARIABLE_FONTS = [
    "AbbotsMortonSpaceportSansJunior.otf",
    "AbbotsMortonSpaceportSansSenior.otf",
]

STATIC_FONTS = [
    "AbbotsMortonSpaceportMono.otf",
]


@pytest.fixture(params=VARIABLE_FONTS)
def varfont(request):
    return TTFont(TEST_DIR / request.param)


@pytest.fixture(params=STATIC_FONTS)
def staticfont(request):
    return TTFont(TEST_DIR / request.param)


class TestVariableFontStructure:
    def test_has_fvar(self, varfont):
        assert "fvar" in varfont

    def test_has_cff2(self, varfont):
        assert "CFF2" in varfont
        assert "CFF " not in varfont

    def test_has_hvar(self, varfont):
        assert "HVAR" in varfont

    def test_wght_axis(self, varfont):
        axes = varfont["fvar"].axes
        assert len(axes) == 1
        axis = axes[0]
        assert axis.axisTag == "wght"
        assert axis.minValue == 200
        assert axis.defaultValue == 400
        assert axis.maxValue == 800

    def test_named_instances(self, varfont):
        instances = varfont["fvar"].instances
        assert len(instances) == 3
        coords = [inst.coordinates for inst in instances]
        assert {"wght": 200} in coords
        assert {"wght": 400} in coords
        assert {"wght": 800} in coords

    def test_instance_names(self, varfont):
        name_table = varfont["name"]
        instances = varfont["fvar"].instances
        names = [name_table.getDebugName(inst.subfamilyNameID) for inst in instances]
        assert "ExtraLight" in names
        assert "Regular" in names
        assert "Bold" in names


class TestStaticFontNotVariable:
    def test_no_fvar(self, staticfont):
        assert "fvar" not in staticfont

    def test_has_cff_not_cff2(self, staticfont):
        assert "CFF " in staticfont
        assert "CFF2" not in staticfont
