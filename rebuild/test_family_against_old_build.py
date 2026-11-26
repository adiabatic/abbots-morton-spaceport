"""Checks that the Mono and Junior that `rebuild/pipeline/build_family.py` builds from the runes, Regular and Bold, differ from the old build's four fonts in site/ by the differences stage 2 of rebuild/CUTOVER-PLAN.md rules and by nothing else:

- The glyphs the new font drops are exactly the Quikscript glyphs no text reaches in the old one: no cmap entry, no variation sequence and no substitution gives them (`_reachable`, fontTools' subsetter closing over every feature). They are Junior's ligatures, ·Vie+Utter among them, and the alternates' contextual siblings.
- Every other glyph keeps its place in the glyph order, its outline, its advance and side bearing, and its cmap and cmap format 14 entries, under its old name or the rebuild's (`RENAMED`).
- GSUB, GPOS and GDEF are the old tables with those renames and drops, lookup by lookup, except the namer dot's `calt` lookup, whose rules are the old ones behind one more: the dot does not lower across a ZWNJ (`NAMER_ZWNJ_ROW`), as the rebuilt Senior's does not.
- The fonts hold the same tables, and `name` is the old one outside the copyright year, which `tools/build_font.py` takes from the clock.

It reads the fonts `make all` writes, and skips when site/ lacks one. The rebuild suite's audit guard fails a test that reads rebuild/out/, so the build here also shows that Mono and Junior read no settlement table. The test compares the rebuild with the old engine's output, so it goes with the old build in stage 4 of rebuild/CUTOVER-PLAN.md.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest
from fontTools import subset
from fontTools.misc.xmlWriter import XMLWriter
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from rebuild.pipeline import build_family, readback

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "site"
NAMES = tuple(
    build_family.font_file_name(font, style)
    for font in ("mono", "junior")
    for style, _bold in build_family.STYLES
)
# The old engine's names for the variation sequences' glyphs and the gapped ·Owe, and the names the rebuild's Senior gives the same drawings.
RENAMED = {
    "qsNo.alt.en-y0.ex-y0": "qsNo.flipped",
    "qsOwe.gapped": "qsOwe.sole.ss06",
    "qsUtter.alt.ex-y0": "qsUtter.alternate",
}
NAMER_ZWNJ_ROW = readback._ChainRow(
    backtrack=(),
    input=(frozenset({build_family.NAMER_DOT}),),
    lookahead=(frozenset({"uni200C"}),),
    records=(),
)
COPYRIGHT_YEAR = re.compile(r"© \d{4} ")
GLYPH_ATTRIBUTE = re.compile(r'\b(?:value|glyph|in|out)="([^"]+)"')
LAYOUT_TABLES = ("GDEF", "GSUB", "GPOS")


def _renamed(name: str) -> str:
    return RENAMED.get(name, name)


def _quikscript(name: str) -> bool:
    return name.startswith(("qs", "uniE6"))


def _reachable(path: Path) -> set[str]:
    """The glyphs some text reaches in the font at `path`: the subsetter's closure over every code point and variation selector its cmap holds and every layout feature."""
    font = TTFont(path)
    try:
        unicodes: set[int] = set()
        for table in font["cmap"].tables:
            if table.format == 14:
                unicodes |= set(table.uvsDict)
            elif table.isUnicode():
                unicodes |= set(table.cmap)
        options = subset.Options()
        options.layout_features = ["*"]
        options.layout_scripts = ["*"]
        options.glyph_names = True
        options.notdef_outline = True
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=unicodes)
        subsetter.subset(font)
        return set(font.getGlyphOrder())
    finally:
        font.close()


def _renamed_font(path: Path) -> Any:
    """The old font with its glyph order renamed before any layout table is read, so every lookup names a glyph as the new font does."""
    font = TTFont(path)
    font.setGlyphOrder([_renamed(name) for name in font.getGlyphOrder()])
    return font


def _xml(table: Any, font: Any) -> list[str]:
    buffer = io.BytesIO()
    writer = XMLWriter(buffer, newlinestr="\n")
    table.toXML(writer, font)
    return buffer.getvalue().decode().splitlines()


def _without(lines: list[str], dropped: set[str]) -> list[str]:
    """`lines` less every line that names a dropped glyph: a coverage, class or substitution entry of it."""
    return [line for line in lines if not set(GLYPH_ATTRIBUTE.findall(line)) & dropped]


def _namer_lookup(font: Any) -> int | None:
    indices = [
        index
        for record in font["GSUB"].table.FeatureList.FeatureRecord
        if record.FeatureTag == "calt"
        for index in record.Feature.LookupListIndex
    ]
    assert len(indices) <= 1, indices
    return indices[0] if indices else None


def _rows(font: Any, index: int, dropped: set[str]) -> list[readback._ChainRow]:
    rows, problems = readback._chain_rows(
        font["GSUB"].table.LookupList.Lookup[index], frozenset(font.getGlyphOrder())
    )
    assert not problems
    return [
        readback._ChainRow(
            backtrack=tuple(slot - dropped for slot in row.backtrack),
            input=tuple(slot - dropped for slot in row.input),
            lookahead=tuple(slot - dropped for slot in row.lookahead),
            records=row.records,
        )
        for row in rows
    ]


def _layout_differences(new: Any, old: Any, dropped: set[str]) -> list[str]:
    notes: list[str] = []
    namer = _namer_lookup(new) if "GSUB" in new else None
    for tag in LAYOUT_TABLES:
        if (tag in new) != (tag in old):
            notes.append(f"{tag} is in one font only")
            continue
        if tag not in new:
            continue
        if tag == "GDEF":
            if _xml(new[tag], new) != _without(_xml(old[tag], old), dropped):
                notes.append("GDEF differs by more than the drops")
            continue
        new_table, old_table = new[tag].table, old[tag].table
        for part in ("ScriptList", "FeatureList"):
            if _xml(getattr(new_table, part), new) != _xml(getattr(old_table, part), old):
                notes.append(f"{tag} {part} differs")
        new_lookups, old_lookups = new_table.LookupList.Lookup, old_table.LookupList.Lookup
        if len(new_lookups) != len(old_lookups):
            notes.append(f"{tag} holds {len(new_lookups)} lookups against {len(old_lookups)}")
            continue
        for index, (new_lookup, old_lookup) in enumerate(zip(new_lookups, old_lookups)):
            if tag == "GSUB" and index == namer:
                same_kind = (new_lookup.LookupType, new_lookup.LookupFlag) == (
                    old_lookup.LookupType,
                    old_lookup.LookupFlag,
                )
                if not same_kind or _rows(new, index, set()) != [NAMER_ZWNJ_ROW, *_rows(old, index, dropped)]:
                    notes.append(f"GSUB lookup {index}, the namer dot's, differs by more than its ZWNJ row")
            elif _xml(new_lookup, new) != _without(_xml(old_lookup, old), dropped):
                notes.append(f"{tag} lookup {index} differs by more than the renames and drops")
    return notes


def _uvs(font: Any) -> dict[int, list[tuple[int, str | None]]]:
    return {
        selector: sorted(entries)
        for table in font["cmap"].tables
        if table.format == 14
        for selector, entries in table.uvsDict.items()
    }


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
    new, old, renamed = TTFont(new_path), TTFont(old_path), _renamed_font(old_path)
    try:
        notes: list[str] = []
        old_order, new_order = old.getGlyphOrder(), new.getGlyphOrder()
        new_names = set(new_order)
        survivors = [name for name in old_order if _renamed(name) in new_names]
        dropped = set(old_order) - set(survivors)
        if new_order != [_renamed(name) for name in survivors]:
            notes.append("the glyph order is not the old one less the drops")
        unreachable = {name for name in old_order if _quikscript(name)} - _reachable(old_path)
        if dropped != unreachable:
            notes.append(f"dropped {sorted(dropped)}, but no text reaches {sorted(unreachable)}")
        if not dropped:
            notes.append("no glyph is dropped")
        new_glyphs, old_glyphs = new.getGlyphSet(), old.getGlyphSet()
        for name in survivors:
            new_name = _renamed(name)
            new_pen, old_pen = RecordingPen(), RecordingPen()
            new_glyphs[new_name].draw(new_pen)
            old_glyphs[name].draw(old_pen)
            if new_pen.value != old_pen.value:
                notes.append(f"{name} draws differently")
            if new["hmtx"][new_name] != old["hmtx"][name]:
                notes.append(f"{name} advances differently")
        if new.getBestCmap() != {code: _renamed(name) for code, name in (old.getBestCmap() or {}).items()}:
            notes.append("cmap differs by more than the renames")
        old_uvs = {
            selector: sorted((code, None if name is None else _renamed(name)) for code, name in entries)
            for selector, entries in _uvs(old).items()
        }
        if _uvs(new) != old_uvs:
            notes.append("cmap format 14 differs by more than the renames")
        notes += _layout_differences(new, renamed, {_renamed(name) for name in dropped})
        if sorted(new.keys()) != sorted(old.keys()):
            notes.append(f"tables {sorted(new.keys())} against {sorted(old.keys())}")
        if _names(new) != _names(old):
            notes.append("name differs outside the copyright year")
        return notes
    finally:
        new.close()
        old.close()
        renamed.close()


def test_mono_and_junior_from_the_runes_differ_from_the_old_builds_fonts_only_as_ruled(tmp_path):
    missing = [name for name in NAMES if not (SITE / name).is_file()]
    if missing:
        pytest.skip(f"site/ lacks {', '.join(missing)}, which `make all` writes")
    written = build_family.build_mono_and_junior(tmp_path)
    assert [path.name for path in written] == list(NAMES)
    differences = {name: notes for name in NAMES if (notes := _differences(tmp_path / name, SITE / name))}
    assert not differences
