"""Tests for the CoreText smoke's summary write and its check that CoreText draws from the file it was given (`engine/pipeline/coretext_smoke.py`)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from engine.pipeline import coretext_smoke

LONG_AGO_NS = 1_000_000_000
CHECKSUM = 0x1234ABCD


class _Names:
    checkSumAdjustment = CHECKSUM

    def getDebugName(self, index: int) -> str:
        return "Senior-Regular"


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


def _coretext_from(checksum: int | None):
    def run(
        binary: Path, font_path: Path, codepoints: list[int], feature_tags: tuple[str, ...]
    ) -> tuple[float, list[coretext_smoke.CoreTextGlyph]]:
        return 5.0, [
            coretext_smoke.CoreTextGlyph(
                gid=5,
                string_index=0,
                x_points=0.0,
                y_points=0.0,
                postscript_name="Senior-Regular",
                font_checksum=checksum,
            )
        ]

    return run


_coretext = _coretext_from(CHECKSUM)


def _smoke(monkeypatch, tmp_path: Path, coretext) -> Path:
    font = tmp_path / "Senior-Regular.otf"
    font.write_bytes(b"font")
    sequences = tmp_path / "sequences.txt"
    sequences.write_text("E650  # ·Pea\n")
    monkeypatch.setattr(coretext_smoke, "compile_binary", lambda: tmp_path / "coretext_smoke")
    monkeypatch.setattr(coretext_smoke, "HarfBuzzShaper", _Shaper)
    monkeypatch.setattr(coretext_smoke, "run_coretext", coretext)
    monkeypatch.setattr(sys, "argv", ["coretext_smoke", "--font", str(font), "--sequences", str(sequences)])
    return tmp_path / "coretext_summary.json"


def test_a_second_identical_smoke_leaves_its_summary_in_place(monkeypatch, tmp_path, capsys):
    """A second smoke over the same font and sequences writes the same `coretext_summary.json`, so the file stays in place, keeping its inode and its modification time, here set long ago so that any write would move it, and the run says it left it."""
    summary = _smoke(monkeypatch, tmp_path, _coretext)

    assert coretext_smoke.main() == 0
    assert f"summary written to {summary}" in capsys.readouterr().out
    os.utime(summary, ns=(LONG_AGO_NS, LONG_AGO_NS))
    inode = summary.stat().st_ino
    assert coretext_smoke.main() == 0
    assert f"summary unchanged, left in place at {summary}" in capsys.readouterr().out
    assert (summary.stat().st_ino, summary.stat().st_mtime_ns) == (inode, LONG_AGO_NS)


def test_a_run_drawn_from_an_installed_font_of_the_same_name_fails(monkeypatch, tmp_path, capsys):
    """The rebuild's Senior has the PostScript name of the released Senior, which a reviewer may have installed. A run CoreText draws from a font whose `head.checkSumAdjustment` is not the file's fails even though its PostScript name is the expected one, and so does a run whose font gives no `head` table."""
    _smoke(monkeypatch, tmp_path, _coretext_from(CHECKSUM + 1))
    assert coretext_smoke.main() == 1
    assert "an installed font with the same PostScript name" in capsys.readouterr().out
    _smoke(monkeypatch, tmp_path, _coretext_from(None))
    assert coretext_smoke.main() == 1


def test_the_harness_line_carries_the_run_fonts_checksum(monkeypatch, tmp_path):
    """`run_coretext` reads the sixth field of each glyph line as the run font's `head.checkSumAdjustment` in hex, and `-` as none."""
    import subprocess

    output = "WIDTH 12.5\n5\t0\t0.0\t0.0\tSenior-Regular\t1234abcd\n6\t1\t6.0\t0.0\tSenior-Regular\t-\n"

    def run(command, capture_output, text, check):
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(coretext_smoke.subprocess, "run", run)
    width, glyphs = coretext_smoke.run_coretext(
        tmp_path / "harness", tmp_path / "Senior-Regular.otf", [0xE650, 0xE651], ()
    )
    assert width == 12.5
    assert [glyph.font_checksum for glyph in glyphs] == [CHECKSUM, None]
