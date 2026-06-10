"""GPOS emission: four per-height cursive lookups in today's verbatim shape (M1-PLAN section 5, Group 3).

One `curs` lookup per registered height (y6 is live in M1 via ·Pea·Pea, so all four are emitted), NULL anchors for cross-height cells, NULL/NULL coverage-parity registrations for locked twins at every height where the twin's rune declares an entry row, and coordinates in the drawn frame: glyph-space pixels × 50 plus the one-pixel ink-centering offset.

The plan's signature takes only the glyph mapping; the parity registrations need the rune surfaces, so `spec` rides as a keyword argument (recorded signature extension) — left None, parity registration is skipped with a comment.
"""

from __future__ import annotations

from typing import Mapping

from rebuild.pipeline.model import CellId, GlyphRecord, ResolvedSpec

PIXEL = 50
INK_X_OFFSET = 1
CURS_HEIGHT_YS = (0, 5, 6, 8)


def _anchor(x_px: int, y_px: int) -> str:
    return f"<anchor {(x_px + INK_X_OFFSET) * PIXEL} {y_px * PIXEL}>"


def _entry_heights(spec: ResolvedSpec, rune_name: str) -> set[int]:
    rune = spec.runes.get(rune_name)
    if rune is None:
        return set()
    heights: set[int] = set()
    for stance in rune.stances.values():
        for height in stance.surface.entries:
            heights.add(spec.registry.y_of(height))
    return heights


def emit_gpos(glyphs: Mapping[CellId, GlyphRecord], spec: ResolvedSpec | None = None) -> str:
    per_height: dict[int, dict[str, tuple[str, str]]] = {y: {} for y in CURS_HEIGHT_YS}
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
            per_height[y][record.name] = (
                _anchor(*entry) if entry else "<anchor NULL>",
                _anchor(*exit_anchor) if exit_anchor else "<anchor NULL>",
            )

    parity_skipped = spec is None
    if spec is not None:
        for cell, record in glyphs.items():
            if "locked" not in cell.adjustments:
                continue
            for y in _entry_heights(spec, cell.rune):
                per_height.setdefault(y, {}).setdefault(record.name, ("<anchor NULL>", "<anchor NULL>"))

    blocks: list[str] = []
    for y in CURS_HEIGHT_YS:
        statements = [
            f"        pos cursive {name} {entry} {exit};"
            for name, (entry, exit) in sorted(per_height[y].items())
        ]
        if not statements:
            # A height with no anchors in this glyph set emits no lookup (the prototype shape). The real M1 build keeps all four live: y6 via qsPea, top via qsTea's GPOS-parity entry.
            continue
        blocks.append(
            f"    lookup m1_cursive_y{y} {{\n" + "\n".join(statements) + f"\n    }} m1_cursive_y{y};"
        )

    header = ""
    if parity_skipped:
        header = "# locked-twin NULL/NULL parity skipped: no spec supplied to emit_gpos.\n"
    return header + "feature curs {\n" + "\n".join(blocks) + "\n} curs;\n"
