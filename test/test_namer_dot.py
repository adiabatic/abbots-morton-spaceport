"""When the namer dot (·) starts a word whose first letter is Short, it drops one pixel so it is centered on that letter. The substitution is in `calt` and exists only in the proportional fonts (Senior and Junior). Mono keeps Departure Mono's single dot. `emit_namer_dot_calt` in tools/quikscript_fea.py emits the lookup."""

from functools import cache

import pytest
import uharfbuzz as hb
from fontTools.ttLib import TTFont

from quikscript_shaping_helpers import ROOT

DOT = "·"  # · periodcentered, the namer dot
NO = ""  # ·No (Short)
IT = ""  # ·It (Short)
PEA = ""  # ·Pea (Tall)
BAY = ""  # ·Bay (Deep)

LOWERED = "periodcentered.lowered"
PLAIN = "periodcentered"

_FONT_PATHS = {
    "senior": ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
    "junior": ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf",
    "mono": ROOT / "site" / "AbbotsMortonSpaceportMono-Regular.otf",
}

_PROPORTIONAL = ["senior", "junior"]


@cache
def _tt(variant: str) -> TTFont:
    return TTFont(str(_FONT_PATHS[variant]))


@cache
def _hb(variant: str) -> hb.Font:
    blob = hb.Blob.from_file_path(str(_FONT_PATHS[variant]))
    return hb.Font(hb.Face(blob))


def _shape(variant: str, text: str) -> list[str]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(_hb(variant), buf)
    tt = _tt(variant)
    return [tt.getGlyphName(info.codepoint) for info in buf.glyph_infos]


@pytest.mark.parametrize("variant", _PROPORTIONAL)
@pytest.mark.parametrize("short", [NO, IT])
def test_namer_dot_lowers_before_short(variant: str, short: str) -> None:
    assert _shape(variant, DOT + short)[0] == LOWERED


@pytest.mark.parametrize("variant", _PROPORTIONAL)
@pytest.mark.parametrize("tall_or_deep", [PEA, BAY])
def test_namer_dot_unchanged_before_tall_or_deep(variant: str, tall_or_deep: str) -> None:
    assert _shape(variant, DOT + tall_or_deep)[0] == PLAIN


@pytest.mark.parametrize("variant", _PROPORTIONAL)
@pytest.mark.parametrize("prefix", ["a", "1", "Z"])
def test_midword_middot_stays_plain(variant: str, prefix: str) -> None:
    # A · after an orthodox letter or digit is a multiplication dot or a Catalan ela geminada, so it keeps its height.
    names = _shape(variant, prefix + DOT + NO)
    assert LOWERED not in names
    assert names.count(PLAIN) == 1


@pytest.mark.parametrize("variant", _PROPORTIONAL)
@pytest.mark.parametrize("text", [DOT + NO, " " + DOT + NO, "(" + DOT + NO, "‌" + DOT + NO])
def test_namer_dot_lowers_at_word_start(variant: str, text: str) -> None:
    # The start of a run, and a position after a space, punctuation, or ZWNJ, all count as a word start.
    assert _shape(variant, text).count(LOWERED) == 1


@pytest.mark.parametrize("variant", _PROPORTIONAL)
def test_consecutive_names(variant: str) -> None:
    # In ·Bay·No the first dot precedes a Deep letter and stays plain, and the second precedes a Short letter and lowers. A Quikscript letter before a namer dot does not block the substitution.
    names = _shape(variant, DOT + BAY + DOT + NO)
    assert names == [PLAIN, "qsBay", LOWERED, "qsNo"]


def test_mono_has_no_lowered_namer_dot() -> None:
    # Mono uses Departure Mono's dot and has no `calt` feature, so it has no lowered dot.
    assert LOWERED not in _tt("mono").getGlyphOrder()
    names = _shape("mono", DOT + NO)
    assert LOWERED not in names
