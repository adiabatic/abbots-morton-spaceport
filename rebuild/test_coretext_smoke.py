"""Tests for the CoreText smoke's summary write (`rebuild/pipeline/coretext_smoke.py`)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rebuild.pipeline import coretext_smoke

LONG_AGO_NS = 1_000_000_000


class _Names:
    def getDebugName(self, index: int) -> str:
        return "M1-Regular"


class _Font:
    def __getitem__(self, table: str) -> _Names:
        return _Names()

    def getGlyphName(self, gid: int) -> str:
        return f"glyph{gid}"


class _Shaper:
    upem = 1000

    def __init__(self, font_path: Path) -> None:
        self.tt = _Font()

    def shape(
        self, codepoints: list[int], feature_tags: tuple[str, ...]
    ) -> list[coretext_smoke.HarfBuzzGlyph]:
        return [coretext_smoke.HarfBuzzGlyph(gid=5, name="glyph5", cluster=0, x_advance=500, x=0, y=0)]


def _coretext(
    binary: Path, font_path: Path, codepoints: list[int], feature_tags: tuple[str, ...]
) -> tuple[float, list[coretext_smoke.CoreTextGlyph]]:
    return 5.0, [
        coretext_smoke.CoreTextGlyph(
            gid=5, string_index=0, x_points=0.0, y_points=0.0, postscript_name="M1-Regular"
        )
    ]


def test_a_second_identical_smoke_leaves_its_summary_in_place(monkeypatch, tmp_path, capsys):
    """A second smoke over the same font and sequences writes the same `coretext_summary.json`, so the file stays in place, keeping its inode and its modification time, here set long ago so that any write would move it, and the run says it left it."""
    font = tmp_path / "M1.otf"
    font.write_bytes(b"font")
    sequences = tmp_path / "sequences.txt"
    sequences.write_text("E650  # ·Pea\n")
    monkeypatch.setattr(coretext_smoke, "compile_binary", lambda: tmp_path / "coretext_smoke")
    monkeypatch.setattr(coretext_smoke, "HarfBuzzShaper", _Shaper)
    monkeypatch.setattr(coretext_smoke, "run_coretext", _coretext)
    monkeypatch.setattr(sys, "argv", ["coretext_smoke", "--font", str(font), "--sequences", str(sequences)])
    summary = tmp_path / "coretext_summary.json"

    assert coretext_smoke.main() == 0
    assert f"summary written to {summary}" in capsys.readouterr().out
    os.utime(summary, ns=(LONG_AGO_NS, LONG_AGO_NS))
    inode = summary.stat().st_ino
    assert coretext_smoke.main() == 0
    assert f"summary unchanged, left in place at {summary}" in capsys.readouterr().out
    assert (summary.stat().st_ino, summary.stat().st_mtime_ns) == (inode, LONG_AGO_NS)
