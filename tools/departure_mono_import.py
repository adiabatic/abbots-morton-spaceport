#!/usr/bin/env python3
"""Import glyphs from the Departure Mono OTF into this pixel-font's in-memory GlyphDef representation.

Departure Mono is a clean monospace pixel font: UPM 550, 50 font units per pixel, every glyph has advance width 350 (7 pixels), and all ink lies on the 50-unit grid inside the cell x in [0, 350]. This module samples each glyph on that fixed cell grid and emits a GlyphDef dict matching `tools/build_font.py`'s `bitmap_to_rectangles` semantics: row 0 is the top row, `y_offset` is the pixel-row index of the bottom row (negative for descenders), and `advance_width` is in pixels.

The self-checks here prove the sampling is faithful so that later-transplanted GPOS anchors line up with the rendered pixels.

Usage:
    uv run python tools/departure_mono_import.py reference/DepartureMono-Regular.otf
"""

import sys
from typing import Any

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen

PIXEL_SIZE = 50
CELL_COUNT = 7
CELL_WIDTH = CELL_COUNT * PIXEL_SIZE


def extract_contours(recording: list[tuple[str, tuple[Any, ...]]]) -> list[list[tuple[float, float]]]:
    contours = []
    current = []
    for op, args in recording:
        if op == "moveTo":
            if current:
                contours.append(current)
            current = [args[0]]
        elif op == "lineTo":
            current.append(args[0])
        elif op == "closePath":
            if current:
                contours.append(current)
            current = []
    return contours


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray casting algorithm for even-odd fill."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _cell_inked(contours: list[list[tuple[float, float]]], x: float, y: float) -> bool:
    return sum(1 for c in contours if point_in_polygon(x, y, c)) % 2 == 1


def _glyph_def_from_contours(
    name: str, contours: list[list[tuple[float, float]]], pixel_size: int
) -> tuple[dict[str, Any], set[tuple[int, int]]]:
    if not contours:
        return {"bitmap": [], "y_offset": 0, "advance_width": CELL_COUNT}, set()

    for c in contours:
        for px, _ in c:
            if px < -1 or px > CELL_WIDTH + 1:
                raise ValueError(
                    f"{name}: contour point x={px} falls outside the {CELL_COUNT}-cell width [0, {CELL_WIDTH}]"
                )

    all_y = [pt[1] for c in contours for pt in c]
    k_lo = int(min(all_y) // pixel_size) - 1
    k_hi = int(max(all_y) // pixel_size) + 1

    inked: set[tuple[int, int]] = set()
    for k in range(k_lo, k_hi + 1):
        cy = k * pixel_size + pixel_size // 2
        for c_idx in range(CELL_COUNT):
            cx = c_idx * pixel_size + pixel_size // 2
            center = _cell_inked(contours, cx, cy)
            # DM is clean pixel art: the center and the four inset corners of a cell must agree, otherwise the glyph is not grid-aligned and our sampling would silently misplace ink relative to the transplanted anchors.
            inset = pixel_size // 4
            corners = [
                _cell_inked(contours, cx - inset, cy - inset),
                _cell_inked(contours, cx + inset, cy - inset),
                _cell_inked(contours, cx - inset, cy + inset),
                _cell_inked(contours, cx + inset, cy + inset),
            ]
            if any(corner != center for corner in corners):
                raise ValueError(
                    f"{name}: cell (col={c_idx}, k={k}) is not grid-aligned; center={center}, corners={corners}"
                )
            if center:
                inked.add((c_idx, k))

    if not inked:
        return {"bitmap": [], "y_offset": 0, "advance_width": CELL_COUNT}, set()

    min_k = min(k for _, k in inked)
    max_k = max(k for _, k in inked)

    bitmap = []
    for k in range(max_k, min_k - 1, -1):
        row = "".join("#" if (c_idx, k) in inked else " " for c_idx in range(CELL_COUNT))
        bitmap.append(row)

    return {"bitmap": bitmap, "y_offset": min_k, "advance_width": CELL_COUNT}, inked


def _render_check(name: str, glyph_def: dict[str, Any], sampled: set[tuple[int, int]]) -> None:
    """Reconstruct the inked-cell set from the produced bitmap and assert it matches the sampled set."""
    bitmap = glyph_def["bitmap"]
    y_offset = glyph_def["y_offset"]
    height = len(bitmap)
    reconstructed: set[tuple[int, int]] = set()
    for row_idx, row in enumerate(bitmap):
        k = y_offset + height - 1 - row_idx
        for c_idx, ch in enumerate(row):
            if ch == "#":
                reconstructed.add((c_idx, k))
    if reconstructed != sampled:
        raise ValueError(
            f"{name}: rendered bitmap does not match sampled cells (sampled={sorted(sampled)}, rendered={sorted(reconstructed)})"
        )


def import_departure_mono(dm_path: str, pixel_size: int = 50) -> tuple[dict[str, dict], dict[str, int]]:
    """Returns (glyph_defs, name_to_codepoint)."""
    font = TTFont(dm_path)
    glyph_set = font.getGlyphSet()

    mark_names: set[str] = set()
    if "GDEF" in font and font["GDEF"].table.GlyphClassDef:
        for gname, cls in font["GDEF"].table.GlyphClassDef.classDefs.items():
            if cls == 3:
                mark_names.add(gname)

    glyph_defs: dict[str, dict] = {}
    for name in font.getGlyphOrder():
        if name == ".notdef":
            continue
        pen = RecordingPen()
        glyph_set[name].draw(pen)
        contours = extract_contours(pen.value)
        glyph_def, sampled = _glyph_def_from_contours(name, contours, pixel_size)
        if name in mark_names:
            glyph_def["is_mark"] = True
        _render_check(name, glyph_def, sampled)
        glyph_defs[name] = glyph_def

    name_to_codepoint: dict[str, int] = {}
    for codepoint, name in (font.getBestCmap() or {}).items():
        name_to_codepoint[name] = codepoint

    return glyph_defs, name_to_codepoint


def _print_sample(name: str, glyph_def: dict[str, Any]) -> None:
    print(
        f"--- {name} (y_offset={glyph_def['y_offset']}, advance_width={glyph_def['advance_width']}, is_mark={glyph_def.get('is_mark', False)})"
    )
    for row in glyph_def["bitmap"]:
        print(f'  "{row}"')
    if not glyph_def["bitmap"]:
        print("  (no ink)")


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "Usage: uv run python tools/departure_mono_import.py <DepartureMono-Regular.otf>", file=sys.stderr
        )
        sys.exit(1)

    dm_path = sys.argv[1]
    glyph_defs, name_to_codepoint = import_departure_mono(dm_path)

    mark_count = sum(1 for g in glyph_defs.values() if g.get("is_mark"))
    print(f"Imported {len(glyph_defs)} glyphs ({mark_count} marks).")
    print(f"Encoded glyphs in cmap: {len(name_to_codepoint)}.")
    print()

    for sample in ["A", "eacute", "gravecomb", "zero", "period", "g", "p", "y"]:
        if sample in glyph_defs:
            _print_sample(sample, glyph_defs[sample])
        else:
            print(f"--- {sample}: not present in font")
        print()

    print("All self-checks passed.")


if __name__ == "__main__":
    main()
