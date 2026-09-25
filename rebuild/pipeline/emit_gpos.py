"""GPOS emission: one cursive lookup for each height in `CURS_HEIGHT_YS` that has anchors (M1-PLAN section 5, Group 3).

Each `curs` lookup registers every glyph with an anchor at that height. A glyph that enters or exits at another height gets a NULL anchor on that side. When `spec` is given, every locked twin also gets a NULL/NULL registration at each height where its rune declares an entry row; without `spec` these are skipped and the output says so in a comment. Coordinates are glyph-space pixels × 50, with x shifted by the one-pixel ink offset.
"""

from __future__ import annotations

from typing import Mapping

from rebuild.pipeline.model import CellId, GlyphRecord, ResolvedSpec

PIXEL = 50
INK_X_OFFSET = 1
CURS_HEIGHT_YS = (0, 5, 6, 8)


Anchor = tuple[int, int] | None
Registration = tuple[Anchor, Anchor]


def _units(anchor: tuple[int, int] | None) -> Anchor:
    if anchor is None:
        return None
    x_px, y_px = anchor
    return ((x_px + INK_X_OFFSET) * PIXEL, y_px * PIXEL)


def _anchor(units: Anchor) -> str:
    return "<anchor NULL>" if units is None else f"<anchor {units[0]} {units[1]}>"


def _entry_heights(spec: ResolvedSpec, rune_name: str) -> set[int]:
    rune = spec.runes.get(rune_name)
    if rune is None:
        return set()
    heights: set[int] = set()
    for stance in rune.stances.values():
        for height in stance.surface.entries:
            heights.add(spec.registry.y_of(height))
    return heights


def cursive_registrations(
    glyphs: Mapping[CellId, GlyphRecord], spec: ResolvedSpec | None = None
) -> dict[int, dict[str, Registration]]:
    """Per registered height, every glyph's (entry, exit) anchor pair in font units. `emit_gpos` renders it and `rebuild/pipeline/readback.py` checks the compiled GPOS against it. An absent side is None, and a locked twin's parity registration is (None, None)."""
    per_height: dict[int, dict[str, Registration]] = {y: {} for y in CURS_HEIGHT_YS}
    for cell, record in glyphs.items():
        for y in CURS_HEIGHT_YS:
            entry = None
            if record.entry is not None and record.entry[1] == y:
                entry = record.entry
            elif record.entry_curs_only is not None and record.entry_curs_only[1] == y:
                entry = record.entry_curs_only
            exit_anchor = record.exit if record.exit is not None and record.exit[1] == y else None
            if entry is None and exit_anchor is None:
                continue
            per_height[y][record.name] = (_units(entry), _units(exit_anchor))

    if spec is not None:
        for cell, record in glyphs.items():
            if "locked" not in cell.adjustments:
                continue
            for y in _entry_heights(spec, cell.rune):
                per_height.setdefault(y, {}).setdefault(record.name, (None, None))
    return per_height


def emit_gpos(glyphs: Mapping[CellId, GlyphRecord], spec: ResolvedSpec | None = None) -> str:
    per_height = cursive_registrations(glyphs, spec)
    parity_skipped = spec is None

    blocks: list[str] = []
    for y in CURS_HEIGHT_YS:
        statements = [
            f"        pos cursive {name} {_anchor(entry)} {_anchor(exit)};"
            for name, (entry, exit) in sorted(per_height[y].items())
        ]
        if not statements:
            # A height with no anchors in this glyph set emits no lookup. The M1 runes declare rows at all four heights.
            continue
        blocks.append(
            f"    lookup m1_cursive_y{y} {{\n" + "\n".join(statements) + f"\n    }} m1_cursive_y{y};"
        )

    header = ""
    if parity_skipped:
        header = "# locked-twin NULL/NULL parity skipped: no spec supplied to emit_gpos.\n"
    return header + "feature curs {\n" + "\n".join(blocks) + "\n} curs;\n"
