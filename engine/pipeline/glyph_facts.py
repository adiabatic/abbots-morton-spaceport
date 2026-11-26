"""The glyph-facts sidecar the build writes beside the rebuild's Senior (`Senior.glyph-facts.json`): for every glyph the glyph chain mints, the facts its name does not carry, which the `data-expect` checker reads (`engine/validation/check.py`, `read_glyph_facts`).

- `traits`: the stance's `traits` (`half`, `alt`), for a cell, the variation glyph of a stance, and a cosmetic copy of a cell; and the isolated cell's, for the bare cmap glyph and its copies, which draw that cell.
- `bitmap`: the `bitmaps:` sibling the cell draws, as `surface.resolve_cell` binds it (`reaches-way-back` for the cells of ·Utter's `alternate` that enter), and the isolated cell's for the raw glyphs. A cosmetic copy draws its cosmetic record's drawing, so it has none.
- `isolated`: true for the bare cmap glyph and the isolated cell's own glyph (`geometry.isolated_cell`), the two glyphs a pin's `.∅` accepts.

A key is left out where its value is empty or false. Every value is a function of the spec, so the sidecar is a function of the build's inputs and needs no stamp of its own: it is written and replaced with the font (`publish`), and `check_coverage` holds it to the font's glyph order (every glyph that draws a rune, and no glyph the font lacks) before the build publishes it. It stays beside the build when `engine/tools/build_fonts.py` publishes the Senior into site/, and the font suite reads it there (`engine/tools/suite_fonts.py`). `GLYPH_FACTS_FORMAT` and the file name restate `engine/validation/check.py`'s, which the build does not import (`engine/test_build_code_closure.py`), and `engine/test_glyph_facts.py` holds the two to each other.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from fontTools.ttLib import TTFont

from engine.pipeline import geometry, output_sink, readback, surface
from engine.pipeline.model import CellId, GlyphRecord, ResolvedSpec, cosmetic_copies

GLYPH_FACTS_FORMAT = "ams-glyph-facts/1"
FILE_NAME = "Senior.glyph-facts.json"


class GlyphFactsError(readback.ReadbackError):
    """The sidecar does not cover the compiled font. It is a read-back failure, so the build stops red and a kern-only pass publishes nothing."""


def _entry(traits: Iterable[str], bitmap: str | None, isolated: bool) -> dict:
    entry: dict = {}
    traits = sorted(set(traits))
    if traits:
        entry["traits"] = traits
    if bitmap:
        entry["bitmap"] = bitmap
    if isolated:
        entry["isolated"] = True
    return entry


def _cell_entry(spec: ResolvedSpec, cell: CellId, plan_bitmap: str | None, *, isolated: bool) -> dict:
    stance = spec.runes[cell.rune].stances[cell.stance]
    return _entry(stance.traits, plan_bitmap, isolated)


def document(
    spec: ResolvedSpec,
    cell_glyphs: Mapping[CellId, GlyphRecord],
    raw_glyphs: Mapping[CellId, GlyphRecord],
    variation_glyphs: Mapping[CellId, GlyphRecord],
    other_glyphs: Iterable[str],
) -> dict:
    """Return the sidecar's document over the glyph chain's minted glyphs: the settled cells (`cell_glyphs`, keyed by their cells), the raw glyphs (`raw_glyphs`: the bare cmap glyphs, their marker, ss10 and locked copies, and the cosmetic copies, keyed as `run_build.mint_raw_glyphs` and `run_build.mint_cosmetic_glyphs` key them), the glyphs the variation sequences select (`variation_glyphs`, keyed by their unjoined cells), and the names of the minted glyphs that draw no rune (`other_glyphs`, the namer dot's), which carry no facts."""
    glyphs: dict[str, dict] = {}
    isolated_cells = {rune: geometry.isolated_cell(spec, rune) for rune in spec.runes}
    isolated_plans = {rune: surface.resolve_cell(spec, cell) for rune, cell in isolated_cells.items()}
    for cell, record in cell_glyphs.items():
        plan = surface.resolve_cell(spec, cell)
        glyphs[record.name] = _cell_entry(spec, cell, plan.bitmap, isolated=cell == isolated_cells[cell.rune])
    for cell, record in variation_glyphs.items():
        plan, _drawn = surface.variation_plan(spec, cell)
        glyphs[record.name] = _cell_entry(spec, cell, plan.bitmap, isolated=cell == isolated_cells[cell.rune])
    cosmetic = cosmetic_copies(spec, {cell: record.name for cell, record in cell_glyphs.items()})
    for record in raw_glyphs.values():
        if record.name in cosmetic:
            source, _cosmetic = cosmetic[record.name]
            glyphs[record.name] = _entry(spec.runes[source.rune].stances[source.stance].traits, None, False)
            continue
        rune = record.name.split(".", 1)[0]
        if rune not in spec.runes:
            raise GlyphFactsError(f"raw glyph {record.name!r} names no rune")
        cell = isolated_cells[rune]
        glyphs[record.name] = _cell_entry(
            spec, cell, isolated_plans[rune].bitmap, isolated=record.name == rune
        )
    for name in other_glyphs:
        glyphs.setdefault(name, {})
    return {"format": GLYPH_FACTS_FORMAT, "glyphs": dict(sorted(glyphs.items()))}


def check_coverage(glyph_order: Iterable[str], facts: Mapping, runes: Iterable[str]) -> None:
    """Raise `GlyphFactsError` unless the sidecar names every glyph of the font that draws a rune (whose name up to its first dot is one of `runes`) and only glyphs the font holds."""
    order = set(glyph_order)
    named = set(facts["glyphs"])
    rune_names = set(runes)
    missing = sorted(name for name in order if name.split(".", 1)[0] in rune_names and name not in named)
    extra = sorted(named - order)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"no facts for {len(missing)} rune glyph(s) of the font, {missing[:5]}")
        if extra:
            parts.append(f"facts for {len(extra)} glyph(s) the font lacks, {extra[:5]}")
        raise GlyphFactsError("the glyph-facts sidecar does not cover the font: " + "; ".join(parts))


def write(out_dir: Path, facts: Mapping) -> Path:
    """Write the sidecar under `out_dir` as `FILE_NAME`, leaving the file in place when its bytes come out the same, and return its path."""
    path = Path(out_dir) / FILE_NAME
    output_sink.write_output(path, json.dumps(facts, indent=1, sort_keys=True) + "\n", keep_if_equal=True)
    return path


def publish(
    out_dir: Path,
    font_path: Path,
    spec: ResolvedSpec,
    cell_glyphs: Mapping[CellId, GlyphRecord],
    raw_glyphs: Mapping[CellId, GlyphRecord],
    variation_glyphs: Mapping[CellId, GlyphRecord],
    other_glyphs: Iterable[str],
) -> Path:
    """Build the sidecar for the font the glyph chain has just compiled at `font_path` (`document`), hold it to that font's glyph order (`check_coverage`), and write it under `out_dir` (`write`), returning its path. Raises `GlyphFactsError`, a read-back failure, when it does not cover the font."""
    facts = document(spec, cell_glyphs, raw_glyphs, variation_glyphs, other_glyphs)
    check_coverage(TTFont(str(font_path)).getGlyphOrder(), facts, spec.runes)
    return write(out_dir, facts)
