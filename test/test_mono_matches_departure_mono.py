"""Prove that AbbotsMortonSpaceportMono reproduces Departure Mono faithfully.

The mono font bundles the whole of Departure Mono — glyphs plus GDEF/GSUB/GPOS — so shaping any Departure-covered codepoint (with or without OpenType features) should yield byte-for-byte the same glyph names and positions through either font. AMS-Mono's cmap reuses Departure's own glyph names and both fonts share the metrics (UPM 550, every advance 350), so the comparison is a direct equality on (glyph_name, x_advance, x_offset, y_offset).
"""

from pathlib import Path

import pytest
import uharfbuzz as hb
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"

DEPARTURE = ROOT / "reference" / "DepartureMono-Regular.otf"
MONO = SITE_DIR / "AbbotsMortonSpaceportMono-Regular.otf"

ShapedGlyph = tuple[str, int, int, int]


def _hb_font(path: Path) -> hb.Font:
    blob = hb.Blob.from_file_path(str(path))
    return hb.Font(hb.Face(blob))


@pytest.fixture(scope="module")
def departure() -> hb.Font:
    return _hb_font(DEPARTURE)


@pytest.fixture(scope="module")
def mono() -> hb.Font:
    return _hb_font(MONO)


def shape(font: hb.Font, text: str, features: dict[str, bool] | None = None) -> list[ShapedGlyph]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features)
    return [
        (font.glyph_to_string(info.codepoint), pos.x_advance, pos.x_offset, pos.y_offset)
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
    ]


def test_per_codepoint_glyph_fidelity(departure, mono):
    """Every codepoint in Departure's cmap shapes identically through both fonts with no features applied — same glyph name, advance, and placement."""
    cmap = TTFont(DEPARTURE).getBestCmap()
    assert cmap is not None
    mismatches = []
    for codepoint in sorted(cmap):
        char = chr(codepoint)
        expected = shape(departure, char)
        actual = shape(mono, char)
        if expected != actual:
            mismatches.append(f"U+{codepoint:04X} {char!r}: departure={expected} mono={actual}")
    assert not mismatches, "AMS-Mono diverged from Departure Mono on:\n" + "\n".join(mismatches)


# Representative inputs per feature. Where a feature's exact trigger is unknown, the input still exercises the glyphs the feature targets, so the parity assertion holds whether or not a substitution fires.
FEATURE_CORPUS = [
    ("combining_mark", "é", None),
    ("stacked_marks", "ậu", None),
    ("frac", "1⁄4", {"frac": True}),
    ("numr", "123", {"numr": True}),
    ("dnom", "123", {"dnom": True}),
    ("sups", "2", {"sups": True}),
    ("subs", "2", {"subs": True}),
    ("sinf", "2", {"sinf": True}),
    ("smcp", "abc", {"smcp": True}),
    ("c2sc", "ABC", {"c2sc": True}),
    ("ordn", "1o", {"ordn": True}),
    ("locl_nl", "ij", {"locl": True}),
    ("locl_tr", "i", {"locl": True}),
    ("ss01", "&", {"ss01": True}),
    ("ss02", "*", {"ss02": True}),
    ("case", "-", {"case": True}),
    ("lnum", "0123", {"lnum": True}),
    ("onum", "0123", {"onum": True}),
    ("pnum", "0123", {"pnum": True}),
    ("tnum", "0123", {"tnum": True}),
]


@pytest.mark.parametrize(("name", "text", "features"), FEATURE_CORPUS, ids=[c[0] for c in FEATURE_CORPUS])
def test_feature_parity(departure, mono, name, text, features):
    """With each feature on, the shaped sequence is identical through both fonts."""
    assert shape(departure, text, features) == shape(mono, text, features)
