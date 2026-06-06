"""The namer dot (·) drops one pixel to center on the x-height when it begins a word that starts with a short letter. The substitution is gated behind `calt` and only exists in the proportional fonts (Senior and Junior); Mono keeps Departure Mono's single dot. See emit_namer_dot_calt in tools/quikscript_fea.py."""

from functools import cache

import pytest
import uharfbuzz as hb
from fontTools.ttLib import TTFont

from quikscript_shaping_helpers import ROOT

DOT = "·"  # · periodcentered, the namer dot
NO = ""  # ·No (short)
IT = ""  # ·It (short)
PEA = ""  # ·Pea (tall)
BAY = ""  # ·Bay (deep)

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
    # A · wedged between a letter or digit and a short letter is a multiplication dot or Catalan ela geminada, not a namer dot, so it keeps its height.
    names = _shape(variant, prefix + DOT + NO)
    assert LOWERED not in names
    assert names.count(PLAIN) == 1


@pytest.mark.parametrize("variant", _PROPORTIONAL)
@pytest.mark.parametrize("text", [DOT + NO, " " + DOT + NO, "(" + DOT + NO, "‌" + DOT + NO])
def test_namer_dot_lowers_at_word_start(variant: str, text: str) -> None:
    # Start of run, after a space, after punctuation, and after ZWNJ all count as a word start.
    assert _shape(variant, text).count(LOWERED) == 1


@pytest.mark.parametrize("variant", _PROPORTIONAL)
def test_consecutive_names(variant: str) -> None:
    # ·Bay·No: the first dot precedes a deep letter (stays plain); the second precedes a short letter (lowers). A Quikscript letter must not block the following namer dot.
    names = _shape(variant, DOT + BAY + DOT + NO)
    assert names == [PLAIN, "qsBay", LOWERED, "qsNo"]


def test_mono_has_no_lowered_namer_dot() -> None:
    # Mono inherits Departure Mono's single dot and has no calt; the lowered variant is proportional-only.
    assert LOWERED not in _tt("mono").getGlyphOrder()
    names = _shape("mono", DOT + NO)
    assert LOWERED not in names
