"""Checks that the Mono and Junior that `rebuild/pipeline/build_family.py` builds from the runes, Regular and Bold, are the old build's four fonts in site/ table by table: every table but `head` and `name` byte for byte, the two `fingerprint.font_content_digest` leaves out, `head` outside its timestamps and checksum adjustment, and `name` outside the copyright year, which `tools/build_font.py` takes from the clock. It reads the fonts `make all` writes, and skips when site/ lacks one. The rebuild suite's audit guard fails a test that reads rebuild/out/, so the build here also shows that Mono and Junior read no settlement table. The test compares the rebuild with the old engine's output, so it goes with the old build in stage 4 of rebuild/CUTOVER-PLAN.md."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
from fontTools.ttLib import TTFont

from rebuild.pipeline import build_family, fingerprint

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "site"
NAMES = tuple(
    build_family.font_file_name(font, style)
    for font in ("mono", "junior")
    for style, _bold in build_family.STYLES
)
HEAD_CLOCK_FIELDS = frozenset({"created", "modified", "checkSumAdjustment"})
COPYRIGHT_YEAR = re.compile(r"© \d{4} ")


def _head(font: Any) -> dict[str, Any]:
    return {key: value for key, value in vars(font["head"]).items() if key not in HEAD_CLOCK_FIELDS}


def _names(font: Any) -> list[tuple]:
    return sorted(
        (
            record.nameID,
            record.platformID,
            record.platEncID,
            record.langID,
            COPYRIGHT_YEAR.sub("© ", record.toUnicode()),
        )
        for record in font["name"].names
    )


def _differences(new_path: Path, old_path: Path) -> list[str]:
    new, old = TTFont(new_path, lazy=True), TTFont(old_path, lazy=True)
    try:
        new_reader, old_reader = cast(Any, new.reader), cast(Any, old.reader)
        new_tags, old_tags = sorted(new_reader.keys()), sorted(old_reader.keys())
        notes = [] if new_tags == old_tags else [f"tables {new_tags} against {old_tags}"]
        for tag in sorted(set(new_tags) & set(old_tags) - fingerprint.FONT_VERSION_TABLES):
            if new_reader[tag] != old_reader[tag]:
                notes.append(f"{tag} bytes differ")
        if _head(new) != _head(old):
            notes.append("head differs outside its timestamps")
        if _names(new) != _names(old):
            notes.append("name differs outside the copyright year")
        return notes
    finally:
        new.close()
        old.close()


def test_mono_and_junior_from_the_runes_are_the_old_builds_fonts(tmp_path):
    missing = [name for name in NAMES if not (SITE / name).is_file()]
    if missing:
        pytest.skip(f"site/ lacks {', '.join(missing)}, which `make all` writes")
    written = build_family.build_mono_and_junior(tmp_path)
    assert [path.name for path in written] == list(NAMES)
    differences = {name: notes for name in NAMES if (notes := _differences(tmp_path / name, SITE / name))}
    assert not differences
