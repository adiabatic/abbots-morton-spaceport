from functools import cache
from pathlib import Path

import pytest

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import JoinGlyph

import inspect_join

ROOT = Path(__file__).resolve().parent.parent
TEA_CURS_ONLY_ENTRY = "qsTea.half.ex-y5.ex-con-1"


@cache
def _glyph_meta() -> dict[str, JoinGlyph]:
    return compile_glyph_set(load_glyph_data(ROOT / "glyph_data"), "senior").glyph_meta


def test_pair_through_a_curs_only_entry_reports_the_join_and_gap(capsys: pytest.CaptureFixture[str]):
    meta = _glyph_meta()
    inspect_join._print_pair(meta["qsSee"], meta[TEA_CURS_ONLY_ENTRY])
    out = capsys.readouterr().out
    assert "no anchor pair" not in out
    assert "join y=8" in out
    assert "right entry=(0, 8) (curs-only)" in out
    assert "gap = 0 (touch)" in out


def test_glyph_bitmap_marks_a_curs_only_entry_row(capsys: pytest.CaptureFixture[str]):
    inspect_join._print_glyph(_glyph_meta()[TEA_CURS_ONLY_ENTRY])
    out = capsys.readouterr().out
    assert 'y= 8  "#" <- entry (curs-only)' in out
