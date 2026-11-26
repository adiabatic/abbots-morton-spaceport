#!/usr/bin/env python3
"""Assemble one pixel font from glyph records with fontTools' FontBuilder: the generic font assembly the rebuild's builds call, `rebuild/pipeline/compile_font.py` for Senior and `rebuild/pipeline/build_family.py` for Mono and Junior.

`build_font` takes a font's records (`glyph_records.GlyphData`, as `load_glyph_data` reads the glyph files and the rebuild hands in its own beside them) and a variant, `mono`, `junior` or `senior`, and returns the unsaved TTFont: the glyph order and cmap, the outlines and metrics, the name, OS/2, post, gasp and head tables, and the layout. Mono bundles Departure Mono's glyphs and copies its GDEF, GSUB and GPOS. Junior and Senior compile the Latin `kern`, `ccmp` and `mark` features the records give, then the caller's Quikscript feature code (`quikscript_fea`), and Senior trims a pixel from the advance of every glyph whose name starts with `qs` or `uniE6` (the Senior tightening).

Each glyph's outline is the union of one rectangle per filled pixel, traced into contours that neither overlap nor share an edge, with outer contours counter-clockwise and holes clockwise (`union_contours`). Bold draws each pixel's rectangle 1.5 pixels wide, extended half a pixel to the right. The pixel grid, advance widths, kerning, and anchors are the same as Regular.
"""

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from departure_mono_import import import_departure_mono
from glyph_records import CompiledGlyphSet, GlyphData, GlyphDef, compile_glyph_records, is_proportional_glyph

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPARTURE_MONO_OTF = _REPO_ROOT / "reference" / "DepartureMono-Regular.otf"
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_postscript_glyph_names() -> dict[str, int]:
    path = Path(__file__).parent.parent / "postscript_glyph_names.yaml"
    with open(path) as f:
        return yaml.load(f, Loader=_SAFE_LOADER)


# The top-level keys a glyph file's document may carry.
_GLYPH_FILE_KEYS = frozenset({"metadata", "glyphs", "kerning"})


def load_glyph_data(path: Path) -> GlyphData:
    """Load the glyph records of a YAML file, or of every `*.yaml` file in a directory, merged in name order: the last `metadata` wins, and `glyphs` and the Latin `kerning` rules merge by name. Each file may hold several `---`-separated documents. Raises ValueError for a document with any other top-level key, which no build reads."""
    metadata: dict[str, Any] = {}
    glyphs: dict[str, Any] = {}
    kerning_defs: dict[str, Any] = {}

    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    for yaml_file in files:
        with open(yaml_file) as f:
            documents = list(yaml.load_all(f, Loader=_SAFE_LOADER))
        for data in documents:
            if not isinstance(data, dict) or not data:
                continue
            unknown = sorted(set(data) - _GLYPH_FILE_KEYS)
            if unknown:
                raise ValueError(
                    f"{yaml_file} has top-level keys no build reads: {', '.join(map(str, unknown))}"
                )
            if "metadata" in data:
                metadata = data["metadata"]
            glyphs.update(data.get("glyphs") or {})
            kerning_defs.update(data.get("kerning") or {})

    return {"metadata": metadata, "glyphs": glyphs, "kerning": kerning_defs}


def _write_if_changed(path: Path, data: bytes) -> bool:
    # The build is byte-deterministic, so rewriting an identical output would only change its mtime, and the review corpus's fixture cache, which keys on mtimes, would treat it as a new build and discard its work. The existing file is hashed in chunks so the comparison does not hold a second copy of the output in memory.
    try:
        with path.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").digest() == hashlib.sha256(data).digest():
                return False
    except FileNotFoundError:
        pass
    path.write_bytes(data)
    return True


def _resolve_codepoint(glyph_name: str, postscript_names: dict[str, int]) -> int | None:
    if len(glyph_name) == 1:
        return ord(glyph_name)
    if glyph_name.startswith("uni") and len(glyph_name) == 7:
        try:
            return int(glyph_name[3:], 16)
        except ValueError:
            return None
    if glyph_name.startswith("u") and not glyph_name.startswith("uni") and len(glyph_name) == 6:
        try:
            cp = int(glyph_name[1:], 16)
        except ValueError:
            return postscript_names.get(glyph_name)
        return cp if cp > 0xFFFF else None
    return postscript_names.get(glyph_name)


def collect_kerning_groups(glyphs_def: dict[str, GlyphDef]) -> dict[str, list[str]]:
    groups = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        for tag in glyph_def.get("kerning", []):
            groups.setdefault(tag, []).append(glyph_name)
    return groups


def generate_kern_fea(
    kerning_defs: dict[str, dict[str, Any]],
    kerning_groups: dict[str, list[str]],
    all_glyph_names: list[str],
    pixel_width: int,
) -> str:
    """Emit the `kern` feature for `kerning_defs`: one lookup per rule, kerning its left glyphs before its right glyphs by `value` pixels. A rule with no left side kerns every glyph that does not carry its tag in a `kerning` list (`collect_kerning_groups`)."""

    def matches_prefix(glyph: str, prefix: str) -> bool:
        return glyph == prefix or glyph.startswith(prefix + ".")

    def expand_prefixes(prefixes: list[str]) -> list[str]:
        return [g for g in all_glyph_names if any(matches_prefix(g, p) for p in prefixes)]

    def subtract_prefixes(glyphs: list[str], prefixes: list[str]) -> list[str]:
        return [g for g in glyphs if not any(matches_prefix(g, p) for p in prefixes)]

    lines = ["feature kern {"]
    for tag_name, definition in kerning_defs.items():
        if "left_family" in definition:
            left_glyphs = expand_prefixes(definition["left_family"])
        elif "left_stance" in definition:
            left_glyphs = expand_prefixes(definition["left_stance"])
        elif "left" in definition:
            left_glyphs = definition["left"]
        else:
            excluded = set(kerning_groups.get(tag_name, []))
            left_glyphs = [g for g in all_glyph_names if g not in excluded]
        if "except_left" in definition:
            left_glyphs = subtract_prefixes(left_glyphs, definition["except_left"])
        if not left_glyphs:
            continue
        if "right_group" in definition:
            suffix = "." + definition["right_group"]
            right_glyphs = [g for g in all_glyph_names if g.endswith(suffix)]
        elif "right_family" in definition:
            right_glyphs = expand_prefixes(definition["right_family"])
        elif "right_stance" in definition:
            right_glyphs = expand_prefixes(definition["right_stance"])
        else:
            right_glyphs = definition["right"]
        if "except_right" in definition and "right_group" not in definition:
            right_glyphs = subtract_prefixes(right_glyphs, definition["except_right"])
        if not right_glyphs:
            continue
        value = definition["value"] * pixel_width
        left = " ".join(sorted(left_glyphs))
        right = " ".join(sorted(right_glyphs))
        lines.append(f"    lookup kern_{tag_name} {{")
        lines.append(f"        pos [{left}] [{right}] {value};")
        lines.append(f"    }} kern_{tag_name};")
    lines.append("} kern;")
    return "\n".join(lines)


def generate_ccmp_fea(glyphs_def: dict[str, GlyphDef]) -> str | None:
    """Generate OpenType feature code for dotted-base substitutions.

    Rewrites dotted lowercase bases to their dotless forms before top combining marks are attached.
    """
    top_marks = [
        glyph_name
        for glyph_name, glyph_def in glyphs_def.items()
        if glyph_def is not None and glyph_def.get("is_mark") and glyph_def.get("y_offset", 0) >= 0
    ]
    if not top_marks:
        return None

    substitutions = [
        (base_name, dotless_name)
        for base_name, dotless_name in (("i", "dotlessi"), ("j", "dotlessj"))
        if base_name in glyphs_def and dotless_name in glyphs_def
    ]
    if not substitutions:
        return None

    lines = ["feature ccmp {"]
    lines.append(f"    @top_marks = [{' '.join(sorted(top_marks))}];")
    for base_name, dotless_name in substitutions:
        lines.append("")
        lines.append(f"    lookup ccmp_{base_name}_before_top_marks {{")
        lines.append(f"        sub {base_name}' @top_marks by {dotless_name};")
        lines.append(f"    }} ccmp_{base_name}_before_top_marks;")
    lines.append("} ccmp;")
    return "\n".join(lines)


def generate_mark_fea(glyphs_def: dict[str, GlyphDef], pixel_width: int, pixel_height: int) -> str | None:
    """Generate OpenType feature code for mark positioning (combining diacriticals).

    Scans glyphs_def for marks (is_mark: true) and base glyphs with top_mark_y / bottom_mark_y anchors, then emits a GPOS 'mark' feature.

    Returns the FEA string, or None if there are no marks.
    """
    top_marks = {}  # glyph_name -> (anchor_x, anchor_y)
    bottom_marks = {}
    adjusted_marks = {}  # glyph_name -> (anchor_x, anchor_y, is_top, base_x_adjust, base_y_adjust)
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or not glyph_def.get("is_mark"):
            continue
        bitmap = glyph_def.get("bitmap", [])
        y_offset = glyph_def.get("y_offset", 0)
        # Mark anchor x = 0 (bitmap is centered on origin by zero-width drawing)
        anchor_x = 0
        base_x_adjust = glyph_def.get("base_x_adjust")
        mark_x_override = glyph_def.get("mark_base_x_adjust")
        if mark_x_override and base_x_adjust:
            base_x_adjust = {**base_x_adjust, **mark_x_override}
        elif mark_x_override:
            base_x_adjust = mark_x_override
        base_y_adjust = glyph_def.get("base_y_adjust")
        mark_y_override = glyph_def.get("mark_base_y_adjust")
        if mark_y_override and base_y_adjust:
            base_y_adjust = {**base_y_adjust, **mark_y_override}
        elif mark_y_override:
            base_y_adjust = mark_y_override
        has_adjustments = base_x_adjust or base_y_adjust
        if y_offset >= 0:
            # Top mark: anchor at the bottom of the drawn pixels
            anchor_y = y_offset * pixel_height
            if has_adjustments:
                adjusted_marks[glyph_name] = (
                    anchor_x,
                    anchor_y,
                    True,
                    base_x_adjust or {},
                    base_y_adjust or {},
                )
            else:
                top_marks[glyph_name] = (anchor_x, anchor_y)
        else:
            # Bottom mark: anchor at the top of the drawn pixels
            bitmap_height = len(bitmap) if bitmap else 0
            anchor_y = (y_offset + bitmap_height) * pixel_height
            if has_adjustments:
                adjusted_marks[glyph_name] = (
                    anchor_x,
                    anchor_y,
                    False,
                    base_x_adjust or {},
                    base_y_adjust or {},
                )
            else:
                bottom_marks[glyph_name] = (anchor_x, anchor_y)

    if not top_marks and not bottom_marks and not adjusted_marks:
        return None

    top_bases = {}  # glyph_name -> (anchor_x, anchor_y)
    bottom_bases = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or glyph_def.get("is_mark"):
            continue
        advance_width = glyph_def.get("advance_width")
        if advance_width is not None:
            aw = advance_width * pixel_width
        else:
            bitmap = glyph_def.get("bitmap", [])
            if bitmap:
                max_col = max((len(row) for row in bitmap), default=0)
                aw = (max_col + 2) * pixel_width
            else:
                continue
        base_x = aw // 2
        if "top_mark_y" in glyph_def:
            top_x = base_x + glyph_def.get("top_mark_x", 0) * pixel_width
            base_y = glyph_def["top_mark_y"] * pixel_height
            top_bases[glyph_name] = (top_x, base_y)
        if "bottom_mark_y" in glyph_def:
            bottom_x = base_x + glyph_def.get("bottom_mark_x", 0) * pixel_width
            base_y = glyph_def["bottom_mark_y"] * pixel_height
            bottom_bases[glyph_name] = (bottom_x, base_y)

    if not top_bases and not bottom_bases:
        return None

    lines = ["feature mark {"]

    for glyph_name in sorted(top_marks):
        ax, ay = top_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_top;")
    for glyph_name in sorted(bottom_marks):
        ax, ay = bottom_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_bottom;")
    for glyph_name in sorted(adjusted_marks):
        ax, ay, _, _, _ = adjusted_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_{glyph_name};")

    if top_marks and top_bases:
        lines.append("")
        lines.append("    lookup mark_top {")
        for glyph_name in sorted(top_bases):
            bx, by = top_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_top;")
        lines.append("    } mark_top;")

    if bottom_marks and bottom_bases:
        lines.append("")
        lines.append("    lookup mark_bottom {")
        for glyph_name in sorted(bottom_bases):
            bx, by = bottom_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_bottom;")
        lines.append("    } mark_bottom;")

    for mark_name in sorted(adjusted_marks):
        _, _, is_top, base_x_adjust, base_y_adjust = adjusted_marks[mark_name]
        bases = top_bases if is_top else bottom_bases
        if not bases:
            continue
        lines.append("")
        lines.append(f"    lookup mark_{mark_name} {{")
        for glyph_name in sorted(bases):
            bx, by = bases[glyph_name]
            x_adj = base_x_adjust.get(glyph_name, 0) * pixel_width
            y_adj = base_y_adjust.get(glyph_name, 0) * pixel_height
            lines.append(
                f"        pos base {glyph_name} <anchor {int(bx + x_adj)} {int(by + y_adj)}> mark @mark_{mark_name};"
            )
        lines.append(f"    }} mark_{mark_name};")

    lines.append("} mark;")
    return "\n".join(lines)


def parse_bitmap(bitmap: list[str] | list[list[int]]) -> list[list[int]]:
    if not bitmap:
        return []

    if isinstance(bitmap[0], str):
        return [[1 if c == "#" or c == "1" else 0 for c in row] for row in bitmap]
    return cast(list[list[int]], bitmap)


def bitmap_to_rectangles(
    bitmap: list[list[int]],
    pixel_width: int,
    pixel_height: int,
    y_offset: int = 0,
) -> list[tuple[int, int, int, int]]:
    """Return an `(x, y, width, height)` rectangle in font units for each filled pixel of *bitmap*, with y=0 at the baseline.

    *y_offset* is in pixels: 0 puts the bitmap's bottom row on the baseline, and -3 puts it 3 pixels below.
    """
    rectangles = []
    height = len(bitmap)

    for row_idx, row in enumerate(bitmap):
        # Bitmap row 0 is the top row, and font y increases upward.
        y = (y_offset + height - 1 - row_idx) * pixel_height

        for col_idx, pixel in enumerate(row):
            if pixel:
                x = col_idx * pixel_width
                rectangles.append((x, y, pixel_width, pixel_height))

    return rectangles


# Unit steps in compressed grid coordinates, in counter-clockwise order, so turning left from step `s` gives step `(s + 1) % 4`.
_STEPS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def union_contours(rectangles: list[tuple[int, int, int, int]]) -> list[list[tuple[int, int]]]:
    """Return the outline of the union of axis-aligned `(x, y, width, height)` *rectangles* as closed contours of corner points, with no two contours overlapping or sharing an edge.

    The union is traced on the grid of the rectangles' own edge coordinates, so it is exact. Each contour keeps the filled area on its left: outer contours run counter-clockwise and holes clockwise, the PostScript direction a CFF font uses. A contour holds only the points where it turns, starts at its lowest, then leftmost point, and does not repeat that point at its end, and the contours are sorted by their first points. Where two filled areas meet only at a corner, as diagonal pixels in Regular do, the trace turns left, so each area keeps a contour of its own and the two contours share that point.
    """
    xs = sorted({edge for x, _y, w, _h in rectangles for edge in (x, x + w)})
    ys = sorted({edge for _x, y, _w, h in rectangles for edge in (y, y + h)})
    column_of = {x: i for i, x in enumerate(xs)}
    row_of = {y: j for j, y in enumerate(ys)}
    filled: set[tuple[int, int]] = set()
    for x, y, w, h in rectangles:
        for i in range(column_of[x], column_of[x + w]):
            for j in range(row_of[y], row_of[y + h]):
                filled.add((i, j))

    # The steps leaving each vertex along a boundary edge of the filled cells, each directed so its filled cell is on its left. Only a vertex where two filled cells meet at their corners alone has two.
    steps_from: dict[tuple[int, int], list[int]] = {}
    for i, j in filled:
        if (i, j - 1) not in filled:
            steps_from.setdefault((i, j), []).append(0)
        if (i + 1, j) not in filled:
            steps_from.setdefault((i + 1, j), []).append(1)
        if (i, j + 1) not in filled:
            steps_from.setdefault((i + 1, j + 1), []).append(2)
        if (i - 1, j) not in filled:
            steps_from.setdefault((i, j + 1), []).append(3)

    def next_edge(vertex: tuple[int, int], step: int) -> tuple[tuple[int, int], int]:
        # Prefer a left turn, then straight on, then a right turn; the boundary never doubles back.
        dx, dy = _STEPS[step]
        end = (vertex[0] + dx, vertex[1] + dy)
        return end, min(steps_from[end], key=lambda option: (step + 1 - option) % 4)

    unvisited = {(vertex, step) for vertex, steps in steps_from.items() for step in steps}
    contours: list[list[tuple[int, int]]] = []
    while unvisited:
        first = min(unvisited)
        cycle = [first]
        edge = next_edge(*first)
        while edge != first:
            cycle.append(edge)
            edge = next_edge(*edge)
        unvisited.difference_update(cycle)
        # A vertex is a corner when the edge leaving it turns away from the edge arriving at it.
        corners = [
            (vertex, step)
            for (vertex, step), (_, arriving) in zip(cycle, cycle[-1:] + cycle[:-1])
            if step != arriving
        ]
        start = min(range(len(corners)), key=lambda k: (corners[k][0][1], corners[k][0][0], corners[k][1]))
        contours.append([(xs[i], ys[j]) for (i, j), _step in corners[start:] + corners[:start]])
    contours.sort(key=lambda contour: (contour[0][1], contour[0][0], contour))
    return contours


def draw_contours(pen: T2CharStringPen, contours: list[list[tuple[int, int]]], x_offset: int = 0) -> None:
    """Draw closed polygonal *contours* with *pen*, each shifted right by *x_offset*."""
    for contour in contours:
        x, y = contour[0]
        pen.moveTo((x + x_offset, y))
        for x, y in contour[1:]:
            pen.lineTo((x + x_offset, y))
        pen.closePath()


def compose_bitmaps(
    base_bitmap: list[list[int]],
    base_y_offset: int,
    accent_bitmap: list[list[int]],
    mark_y: int,
    is_top: bool,
    accent_x_adjust: int = 0,
) -> tuple[list[list[int]], int]:
    """Overlay an accent bitmap on a base bitmap and return `(combined_bitmap, combined_y_offset)`.

    *mark_y* is the base glyph's mark anchor in pixels above the baseline. A top accent's bottom edge sits there, and a bottom accent's top edge. Both bitmaps are centered horizontally on the wider of the two, and *accent_x_adjust* shifts the accent by that many pixels.
    """
    base_h = len(base_bitmap)
    accent_h = len(accent_bitmap)
    base_w = max((len(row) for row in base_bitmap), default=0)
    accent_w = max((len(row) for row in accent_bitmap), default=0)

    canvas_w = max(base_w, accent_w)

    # Pixel rows in font coordinates, where 0 is the baseline and y increases upward.
    base_bottom = base_y_offset
    base_top = base_y_offset + base_h

    if is_top:
        # Top accent: its bottom edge sits at mark_y
        accent_bottom = mark_y
        accent_top = mark_y + accent_h
    else:
        # Bottom accent: its top edge sits at mark_y, extending downward
        accent_top = mark_y
        accent_bottom = mark_y - accent_h

    combined_bottom = min(base_bottom, accent_bottom)
    combined_top = max(base_top, accent_top)
    combined_h = combined_top - combined_bottom

    canvas = [[0] * canvas_w for _ in range(combined_h)]

    def blit(bitmap, bm_w, bm_bottom, x_adjust=0):
        x_off = (canvas_w - bm_w) // 2 + x_adjust
        bm_h = len(bitmap)
        for row_idx, row in enumerate(bitmap):
            # bitmap row 0 is top; font-pixel y for this row:
            pixel_y = bm_bottom + bm_h - 1 - row_idx
            canvas_row = combined_top - 1 - pixel_y
            for col_idx, val in enumerate(row):
                if val:
                    canvas[canvas_row][x_off + col_idx] = 1

    blit(base_bitmap, base_w, base_bottom)
    blit(accent_bitmap, accent_w, accent_bottom, accent_x_adjust)

    return canvas, combined_bottom


def resolve_composite(
    glyph_name: str,
    glyph_def: GlyphDef,
    glyphs_def: dict[str, GlyphDef],
    is_proportional: bool,
) -> tuple[list[list[int]], int]:
    base_name = glyph_def["base"]

    if is_proportional and base_name + ".prop" in glyphs_def:
        base_ref = base_name + ".prop"
    else:
        base_ref = base_name

    base_glyph = glyphs_def.get(base_ref)
    if base_glyph is None:
        raise ValueError(f"Composite glyph '{glyph_name}' references base '{base_name}' which doesn't exist")
    base_bitmap = parse_bitmap(base_glyph.get("bitmap", []))
    base_y_offset = base_glyph.get("y_offset", 0)

    result_bitmap = base_bitmap
    result_y_offset = base_y_offset

    # Map each combining mark's bitmap object to its adjustments. A YAML alias makes a spacing accent's bitmap the same object as its combining mark's, so an `id()` lookup finds the mark's adjustments from the accent.
    accent_x_adjusts = {}
    accent_y_adjusts = {}
    for gn, gd in glyphs_def.items():
        if not gd.get("is_mark"):
            continue
        bitmap_obj = gd.get("bitmap")
        if bitmap_obj is None:
            continue
        if "base_x_adjust" in gd:
            accent_x_adjusts[id(bitmap_obj)] = gd["base_x_adjust"]
        if "base_y_adjust" in gd:
            accent_y_adjusts[id(bitmap_obj)] = gd["base_y_adjust"]

    def _apply_accent(position, result_bitmap, result_y_offset):
        is_top = position == "top"
        mark_key = "top_mark_y" if is_top else "bottom_mark_y"
        accent_name = glyph_def[position]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references {position} accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get(mark_key)
        if mark_y is None:
            raise ValueError(f"Composite glyph '{glyph_name}' needs {mark_key} on base '{base_ref}'")
        bitmap_id = id(accent_glyph.get("bitmap"))
        accent_x_adjust = accent_x_adjusts.get(bitmap_id, {}).get(base_name, 0)
        accent_y_adjust = accent_y_adjusts.get(bitmap_id, {}).get(base_name, 0)
        return compose_bitmaps(
            result_bitmap,
            result_y_offset,
            accent_bitmap,
            mark_y + accent_y_adjust,
            is_top=is_top,
            accent_x_adjust=accent_x_adjust,
        )

    if "top" in glyph_def:
        result_bitmap, result_y_offset = _apply_accent("top", result_bitmap, result_y_offset)

    if "bottom" in glyph_def:
        result_bitmap, result_y_offset = _apply_accent("bottom", result_bitmap, result_y_offset)

    return result_bitmap, result_y_offset


def _is_orthodox_letter_or_digit(codepoint: int | None) -> bool:
    """True for ASCII and Latin letters and digits, the glyphs after which a `·` is a mid-word middot and not a namer dot. The Quikscript letters are excluded, so in back-to-back names like ·Bay·No the second dot is still lowered."""
    if codepoint is None:
        return False
    in_latin_range = (
        0x30 <= codepoint <= 0x39
        or 0x41 <= codepoint <= 0x5A
        or 0x61 <= codepoint <= 0x7A
        or 0xC0 <= codepoint <= 0x24F
    )
    return in_latin_range and chr(codepoint).isalnum()


@dataclass(frozen=True)
class _GlyphInventory:
    """A variant's glyph definitions (with Departure Mono's merged in for mono), glyph order, cmap, and code point for each glyph name."""

    glyphs_def: dict[str, GlyphDef]
    glyph_order: list[str]
    cmap: dict[int, str]
    name_to_codepoint: dict[str, int]


def _glyph_inventory(compiled: CompiledGlyphSet, variant: str, pixel_height: int) -> _GlyphInventory:
    is_proportional = variant != "mono"
    glyphs_def = compiled.glyph_definitions

    # The mono build is Departure Mono plus the Quikscript letters. DM's glyphs win on name collisions such as `zero` and `space`, and its code points go into the cmap and glyph order below.
    if variant == "mono":
        dm_glyphs, dm_codepoints = import_departure_mono(str(_DEPARTURE_MONO_OTF), pixel_height)
        glyphs_def = {**glyphs_def, **dm_glyphs}
    else:
        dm_codepoints = {}

    # `.notdef` must come first in the glyph order. The mono build excludes `.prop` glyphs.
    glyph_names = [
        name
        for name in glyphs_def.keys()
        if name not in (".notdef", "space") and (is_proportional or not is_proportional_glyph(name))
    ]
    postscript_glyph_names = load_postscript_glyph_names()

    name_to_codepoint: dict[str, int] = {}
    for name in glyphs_def:
        cp = _resolve_codepoint(name, postscript_glyph_names)
        if cp is not None:
            name_to_codepoint[name] = cp
    name_to_codepoint.update(dm_codepoints)

    def _sort_key(name):
        cp = name_to_codepoint.get(name)
        if cp is not None:
            return (cp, name)
        base = name.split(".")[0]
        if "_" in base:
            base = base.split("_")[0]
        cp = name_to_codepoint.get(base)
        if cp is not None:
            return (cp, name)
        return (float("inf"), name)

    glyph_order = [".notdef", "space"] + sorted(glyph_names, key=_sort_key)

    # `.prop` glyphs have no code point of their own.
    cmap = {32: "space"}
    for glyph_name in glyphs_def:
        if is_proportional_glyph(glyph_name):
            continue
        cp = name_to_codepoint.get(glyph_name)
        if cp is not None:
            cmap[cp] = glyph_name

    return _GlyphInventory(glyphs_def, glyph_order, cmap, name_to_codepoint)


def _assemble_fea(
    glyph_data: GlyphData,
    inventory: _GlyphInventory,
    variant: str,
    pixel_width: int,
    pixel_height: int,
    quikscript_fea: str | None,
) -> str | None:
    """Return the feature file a variant compiles, or None for mono, which copies Departure Mono's tables. Junior and Senior compile the Latin `kern`, `ccmp` and `mark` features their records give, then `quikscript_fea`, the caller's Quikscript feature code (see `build_font`)."""
    if variant == "mono":
        return None
    glyphs_def = inventory.glyphs_def
    fea_code_parts = []

    kerning_defs = glyph_data.get("kerning", {})
    if kerning_defs:
        kerning_groups = collect_kerning_groups(glyphs_def)
        fea_code_parts.append(
            generate_kern_fea(kerning_defs, kerning_groups, list(glyphs_def.keys()), pixel_width)
        )

    ccmp_fea = generate_ccmp_fea(glyphs_def)
    if ccmp_fea:
        fea_code_parts.append(ccmp_fea)

    mark_fea = generate_mark_fea(glyphs_def, pixel_width, pixel_height)
    if mark_fea:
        fea_code_parts.append(mark_fea)

    if quikscript_fea:
        fea_code_parts.append(quikscript_fea)

    if not fea_code_parts:
        return None
    return "\n\n".join(fea_code_parts)


@dataclass(frozen=True)
class CompiledSeniorOTL:
    """The Senior layout tables as compiled bytes, keyed by tag, with the `usMaxContext` value feaLib computes for them. The font that receives the tables does not run feaLib, so it takes its OS/2 `usMaxContext` from here; rebuild/pipeline/compile_font.py hands Bold the tables Regular compiled this way."""

    tables: dict[str, bytes]
    max_context: int


def build_font(
    glyph_data: GlyphData,
    variant: str = "mono",
    pixel_width: int | None = None,
    bold: bool = False,
    quikscript_fea: str | None = None,
    compiled: CompiledGlyphSet | None = None,
    senior_otl: CompiledSeniorOTL | None = None,
) -> TTFont:
    """Build a CFF OpenType font for one variant and style and return the unsaved TTFont, its feature code in the `_fea_code` attribute (None for Mono). The callers save it and the feature code themselves.

    Args:
        variant: "mono", "junior", or "senior".
        pixel_width: Width of each pixel in font units. Defaults to metadata["pixel_size"], which is always the pixel height.
        bold: Widen each drawn pixel by `pixel_width // 2` units to the right, and name and style-link the font as Bold. The pixel grid, advance widths, and anchors do not change.
        quikscript_fea: The caller's Quikscript feature code, which goes after `mark`: for Senior, the rune FEA (rebuild/pipeline/compile_font.py), and for Junior, the stylistic sets and the namer dot (rebuild/pipeline/build_family.py). Mono ignores it.
        compiled: The variant's records (`glyph_records.compile_glyph_records`). `bold` does not affect them, so a caller building both styles compiles them once. When None, the build compiles its own.
        senior_otl: Senior's GDEF, GSUB and GPOS as another build of the same glyph order compiled them, used only for Senior. When given, the build writes these bytes instead of compiling the feature code.
    """
    metadata = glyph_data.get("metadata", {})
    is_proportional = variant != "mono"
    is_senior = variant == "senior"
    style_name = "Bold" if bold else "Regular"
    if compiled is None:
        compiled = CompiledGlyphSet(compile_glyph_records(glyph_data, variant))

    base_font_name = metadata["font_name"]
    suffixes = {"mono": " Mono", "junior": " Sans Junior", "senior": " Sans Senior"}
    font_name = base_font_name + suffixes[variant]
    version = metadata["version"]
    units_per_em: int = metadata["units_per_em"]
    pixel_height: int = metadata["pixel_size"]
    if pixel_width is None:
        pixel_width = pixel_height

    inventory = _glyph_inventory(compiled, variant, pixel_height)
    glyphs_def = inventory.glyphs_def
    glyph_order = inventory.glyph_order
    ascender = metadata["ascender"]
    descender = metadata["descender"]
    cap_height = metadata["cap_height"]
    x_height = metadata["x_height"]

    fb = FontBuilder(units_per_em, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(inventory.cmap)

    charstrings = {}
    metrics = {}

    glyph_set = None

    # Standard monospace width: 7 pixels (bitmap 5 + 2 spacing)
    mono_width = 7 * pixel_width

    # Bold widens each painted rectangle to the right by this many units. The pixel grid does not change.
    overstrike = pixel_width // 2 if bold else 0

    pen = T2CharStringPen(width=mono_width, glyphSet=glyph_set)
    draw_contours(pen, union_contours([(pixel_width, 0, 4 * pixel_width + overstrike, 5 * pixel_height)]))
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (mono_width, pixel_width)

    space_def = glyphs_def.get("space", {})
    space_width = space_def["advance_width"] * pixel_width
    pen = T2CharStringPen(width=space_width, glyphSet=glyph_set)
    charstrings["space"] = pen.getCharString()
    metrics["space"] = (space_width, 0)

    for glyph_name in glyph_order:
        if glyph_name in (".notdef", "space"):
            continue

        glyph_def = glyphs_def.get(glyph_name, {})

        if "base" in glyph_def and not glyph_def.get("bitmap"):
            composed_bitmap, composed_y_offset = resolve_composite(
                glyph_name, glyph_def, glyphs_def, is_proportional
            )
            glyph_def = dict(glyph_def)
            glyph_def["bitmap"] = composed_bitmap
            glyph_def["y_offset"] = composed_y_offset
            glyph_def.pop("base", None)
            glyph_def.pop("top", None)
            glyph_def.pop("bottom", None)

        bitmap = glyph_def.get("bitmap", [])

        # A proportional font validates every glyph as proportional. The mono font validates only `.prop` glyphs that way.
        is_prop_glyph = is_proportional or is_proportional_glyph(glyph_name)
        if bitmap:
            if is_prop_glyph:
                row_widths = [len(row) for row in bitmap]
                if len(set(row_widths)) > 1:
                    raise ValueError(f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}")
            else:
                base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
                is_quikscript_glyph = base_name.startswith("uniE6") or base_name.startswith("qs")
                if is_quikscript_glyph:
                    # Quikscript glyphs: all rows must be exactly 5 characters wide
                    for row_idx, row in enumerate(bitmap):
                        row_len = len(row)
                        if row_len != 5:
                            raise ValueError(
                                f"Glyph '{glyph_name}' row {row_idx} has width {row_len}, expected 5"
                            )
                else:
                    row_widths = [len(row) for row in bitmap]
                    if len(set(row_widths)) > 1:
                        raise ValueError(f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}")

        if not bitmap:
            width = glyph_def.get("advance_width")
            if width is not None:
                width = int(width * pixel_width)
            else:
                width = mono_width
            pen = T2CharStringPen(width=width, glyphSet=glyph_set)
            charstrings[glyph_name] = pen.getCharString()
            metrics[glyph_name] = (width, 0)
            continue

        bitmap = parse_bitmap(bitmap)
        y_offset = glyph_def.get("y_offset", 0)  # negative for descenders

        row_count = len(bitmap)

        base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
        is_quikscript = base_name.startswith("uniE6") or base_name.startswith("qs")

        if is_quikscript:
            if glyph_name in ("uniE66E", "uniE66F", "qsAngleParenLeft", "qsAngleParenRight"):
                if row_count != 12:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has {row_count} rows, expected 12 (angled parenthesis)"
                    )
            elif y_offset == -3:
                if row_count not in (9, 12):
                    raise ValueError(
                        f"Glyph '{glyph_name}' has y_offset=-3 but bitmap has {row_count} rows, expected 9 or 12"
                    )
            elif row_count not in (6, 9):
                raise ValueError(f"Glyph '{glyph_name}' has {row_count} rows, expected 6 or 9")

        rectangles = bitmap_to_rectangles(bitmap, pixel_width, pixel_height, y_offset)

        advance_width = glyph_def.get("advance_width")
        senior_tighten = False
        if advance_width is None:
            if is_prop_glyph:
                max_col = max((len(row) for row in bitmap), default=0)
                advance_width = (max_col + 2) * pixel_width
                # Senior is set one pixel tighter than Junior: each Quikscript letter without an explicit advance loses a pixel of right sidebearing. Anchors position cursive joins, so this only narrows the gap at non-joining boundaries.
                senior_tighten = is_senior and is_quikscript
            else:
                advance_width = mono_width
        else:
            advance_width *= pixel_width

        bitmap_width = max((len(row) for row in bitmap), default=0) * pixel_width
        if advance_width == 0:
            # Zero-width (combining mark): center bitmap on the origin
            x_offset = -(bitmap_width // 2)
        else:
            x_offset = (advance_width - bitmap_width) // 2

        # Trim the right sidebearing after centering so the ink keeps its pixel-grid position and only the advance shrinks.
        if senior_tighten:
            advance_width -= pixel_width

        if advance_width == 0:
            lsb = 0
        elif rectangles:
            lsb = min(r[0] for r in rectangles) + x_offset
        else:
            lsb = x_offset

        # The glyph's outline is the union of its pixel rectangles, which overlap in Bold (`overstrike` is 0 for Regular), so no two contours overlap or share an edge.
        pen = T2CharStringPen(width=advance_width, glyphSet=glyph_set)
        draw_contours(pen, union_contours([(x, y, w + overstrike, h) for x, y, w, h in rectangles]), x_offset)

        charstrings[glyph_name] = pen.getCharString()
        metrics[glyph_name] = (advance_width, lsb)

    ps_name = f"{font_name.replace(' ', '')}-{style_name}"
    fb.setupCFF(
        psName=ps_name,
        fontInfo={"FamilyName": font_name, "FullName": f"{font_name} {style_name}"},
        charStringsDict=charstrings,
        privateDict={},
    )

    fb.setupHorizontalMetrics(metrics)

    fb.setupHorizontalHeader(ascent=ascender, descent=descender)

    name_strings = {
        "familyName": {"en": font_name},
        "styleName": {"en": style_name},
        "uniqueFontIdentifier": f"FontBuilder:{font_name}.{style_name}",
        "fullName": {"en": f"{font_name} {style_name}"},
        "psName": ps_name,
        "version": f"Version {version}",
    }

    if "copyright" in metadata:
        copyright_str = metadata["copyright"]
        if "© " in copyright_str:
            year = datetime.now().year
            copyright_str = copyright_str.replace("© ", f"© {year} ", 1)
        name_strings["copyright"] = {"en": copyright_str}
    if "license" in metadata:
        name_strings["licenseDescription"] = {"en": metadata["license"]}
    if "license_url" in metadata:
        name_strings["licenseInfoURL"] = {"en": metadata["license_url"]}
    if "sample_text" in metadata:
        name_strings["sampleText"] = {"en": metadata["sample_text"]}
    if "vendor_url" in metadata:
        name_strings["vendorURL"] = {"en": metadata["vendor_url"]}
    if "description" in metadata:
        name_strings["description"] = {"en": metadata["description"]}
    if "designer" in metadata:
        name_strings["designer"] = {"en": metadata["designer"]}
    if "manufacturer" in metadata:
        name_strings["manufacturer"] = {"en": metadata["manufacturer"]}

    fb.setupNameTable(name_strings)

    fb.setupOS2(
        sTypoAscender=ascender,
        sTypoDescender=descender,
        sTypoLineGap=0,
        usWinAscent=ascender,
        usWinDescent=abs(descender),
        sxHeight=x_height,
        sCapHeight=cap_height,
        fsType=0,  # Installable embedding - no restrictions
    )
    # RIBBI style linking: set the BOLD bit on Bold fonts, REGULAR on Regular.
    fb.font["OS/2"].fsSelection = 0x20 if bold else 0x40  # pyright: ignore[reportAttributeAccessIssue]

    fb.setupPost(isFixedPitch=0 if is_proportional else 1)

    gasp = newTable("gasp")
    # Grid-fit only, no antialiasing
    gasp.gaspRange = {0xFFFF: 0x0001}  # pyright: ignore[reportAttributeAccessIssue]
    fb.font["gasp"] = gasp

    fb.setupHead(unitsPerEm=units_per_em, fontRevision=version)
    if bold:
        fb.font["head"].macStyle |= 0x01  # pyright: ignore[reportAttributeAccessIssue]  # Bold bit

    fea_code = _assemble_fea(glyph_data, inventory, variant, pixel_width, pixel_height, quikscript_fea)
    if fea_code is not None:
        if is_senior and senior_otl is not None:
            for tag, data in senior_otl.tables.items():
                table = DefaultTable(tag)
                table.data = data
                fb.font[tag] = table
            fb.font["OS/2"].usMaxContext = (
                senior_otl.max_context
            )  # pyright: ignore[reportAttributeAccessIssue]
        else:
            addOpenTypeFeaturesFromString(fb.font, fea_code)

    # The mono build has no feature code of its own. It copies Departure Mono's layout tables so DM's shaping (its substitutions, such as ccmp and locl, and its mark positioning) works for the DM glyphs. The Quikscript letters are in none of these lookups.
    if variant == "mono":
        assert fea_code is None, "mono build should emit no authored FEA; DM owns its OTL"
        dm_font = TTFont(str(_DEPARTURE_MONO_OTF))
        missing = set(dm_font.getGlyphOrder()) - {".notdef"} - set(glyph_order)
        assert not missing, f"DM glyphs missing from mono glyph order: {sorted(missing)[:10]}"
        for tag in ("GDEF", "GSUB", "GPOS"):
            fb.font[tag] = dm_font[tag]  # pyright: ignore[reportArgumentType]

    font = fb.font
    font._fea_code = fea_code  # pyright: ignore[reportAttributeAccessIssue]
    return font


class _DropCoverageSortMessage(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "Coverage is not sorted by glyph ids" not in record.getMessage()


@contextmanager
def _quiet_dm_coverage_sort_warning() -> Any:
    # Some coverage tables in the copied Departure Mono GSUB and GPOS are not in glyph-id order. fontTools sorts them on write, so the output is correct, but it logs "Coverage is not sorted by glyph ids." for each one. Drop that message while the mono font is written.
    logger = logging.getLogger("fontTools.ttLib.tables.otTables")
    log_filter = _DropCoverageSortMessage()
    logger.addFilter(log_filter)
    try:
        yield
    finally:
        logger.removeFilter(log_filter)
