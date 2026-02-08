#!/usr/bin/env python3
"""
Build a pixel font from bitmap glyph definitions.
Uses fonttools FontBuilder to create OTF output.

Usage:
    uv run python build_font.py <glyph_data.yaml|glyph_data/> [output_dir]

    The first argument can be a single YAML file or a directory of YAML files.
    When a directory is given, all *.yaml files are loaded and merged.

Outputs:
    output_dir/AbbotsMortonSpaceportMono.otf  - Monospace font
    output_dir/AbbotsMortonSpaceportSans.otf  - Proportional font
"""

import sys
from datetime import datetime
from pathlib import Path

import yaml

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import newTable


def load_postscript_glyph_names() -> dict:
    """Load PostScript glyph name to Unicode codepoint mapping from YAML."""
    path = Path(__file__).parent / "inspo" / "postscript_glyph_names.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_glyph_data(path: Path) -> dict:
    """Load glyph definitions from a YAML file or directory of YAML files."""
    if path.is_dir():
        metadata = {}
        glyphs = {}
        kerning_defs = {}
        for yaml_file in sorted(path.glob("*.yaml")):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            if data and "metadata" in data:
                metadata = data["metadata"]
            if data and "glyphs" in data:
                glyphs.update(data["glyphs"])
            if data and "kerning" in data:
                kerning_defs.update(data["kerning"])
        return {"metadata": metadata, "glyphs": glyphs, "kerning": kerning_defs}
    else:
        with open(path) as f:
            return yaml.safe_load(f)


def is_proportional_glyph(glyph_name: str) -> bool:
    """Check if a glyph is a proportional variant."""
    return glyph_name.endswith(".prop")


def get_base_glyph_name(prop_glyph_name: str) -> str:
    """Get the base glyph name from a proportional glyph name."""
    if prop_glyph_name.endswith(".prop"):
        return prop_glyph_name[:-5]
    return prop_glyph_name


def prepare_proportional_glyphs(glyphs_def: dict) -> dict:
    """
    Prepare glyph definitions for the proportional font variant.

    For the proportional font:
    - .prop glyphs are renamed to their base names (e.g., uniE650.prop → uniE650)
    - Base glyphs that have .prop variants are excluded
    - Glyphs without .prop variants remain unchanged
    """
    # Find all base glyph names that have .prop variants
    prop_base_names = set()
    for glyph_name in glyphs_def.keys():
        if is_proportional_glyph(glyph_name):
            prop_base_names.add(get_base_glyph_name(glyph_name))

    # Build new glyph dict
    new_glyphs = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            # Rename .prop glyph to its base name
            base_name = get_base_glyph_name(glyph_name)
            new_glyphs[base_name] = glyph_def
        elif glyph_name in prop_base_names:
            # Skip base glyphs that have .prop variants
            continue
        else:
            # Keep glyphs without .prop variants unchanged
            new_glyphs[glyph_name] = glyph_def

    return new_glyphs


def collect_kerning_groups(glyphs_def: dict) -> dict[str, list[str]]:
    """Build a mapping of {tag_name: [glyph_name, ...]} from kerning tags on glyphs."""
    groups = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        for tag in glyph_def.get("kerning", []):
            groups.setdefault(tag, []).append(glyph_name)
    return groups


def generate_kern_fea(
    kerning_defs: dict,
    kerning_groups: dict[str, list[str]],
    all_glyph_names: list[str],
    pixel_size: int,
) -> str:
    """Generate OpenType feature code for kern feature from kerning definitions."""
    lines = ["feature kern {"]
    for tag_name, definition in kerning_defs.items():
        excluded = set(kerning_groups.get(tag_name, []))
        left_glyphs = [g for g in all_glyph_names if g not in excluded]
        if not left_glyphs:
            continue
        right_glyphs = definition["right"]
        value = definition["value"] * pixel_size
        left = " ".join(sorted(left_glyphs))
        right = " ".join(right_glyphs)
        lines.append(f"    pos [{left}] [{right}] {value};")
    lines.append("} kern;")
    return "\n".join(lines)


def generate_mark_fea(glyphs_def: dict, pixel_size: int) -> str | None:
    """Generate OpenType feature code for mark positioning (combining diacriticals).

    Scans glyphs_def for marks (is_mark: true) and base glyphs with
    top_mark_y / bottom_mark_y anchors, then emits a GPOS 'mark' feature.

    Returns the FEA string, or None if there are no marks.
    """
    # Collect mark glyphs, split into top vs bottom.
    # Marks with base_x_adjust get their own mark class and lookup.
    top_marks = {}       # glyph_name -> (anchor_x, anchor_y)
    bottom_marks = {}
    adjusted_marks = {}  # glyph_name -> (anchor_x, anchor_y, is_top, base_x_adjust)
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or not glyph_def.get("is_mark"):
            continue
        bitmap = glyph_def.get("bitmap", [])
        y_offset = glyph_def.get("y_offset", 0)
        # Mark anchor x = 0 (bitmap is centered on origin by zero-width drawing)
        anchor_x = 0
        base_x_adjust = glyph_def.get("base_x_adjust")
        if y_offset >= 0:
            # Top mark: anchor at the bottom of the drawn pixels
            anchor_y = y_offset * pixel_size
            if base_x_adjust:
                adjusted_marks[glyph_name] = (anchor_x, anchor_y, True, base_x_adjust)
            else:
                top_marks[glyph_name] = (anchor_x, anchor_y)
        else:
            # Bottom mark: anchor at the top of the drawn pixels
            bitmap_height = len(bitmap) if bitmap else 0
            anchor_y = (y_offset + bitmap_height) * pixel_size
            if base_x_adjust:
                adjusted_marks[glyph_name] = (anchor_x, anchor_y, False, base_x_adjust)
            else:
                bottom_marks[glyph_name] = (anchor_x, anchor_y)

    if not top_marks and not bottom_marks and not adjusted_marks:
        return None

    # Collect base glyphs with anchors
    top_bases = {}    # glyph_name -> (anchor_x, anchor_y)
    bottom_bases = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or glyph_def.get("is_mark"):
            continue
        advance_width = glyph_def.get("advance_width")
        if advance_width is not None:
            aw = advance_width * pixel_size
        else:
            bitmap = glyph_def.get("bitmap", [])
            if bitmap:
                max_col = max((len(row) for row in bitmap), default=0)
                aw = (max_col + 2) * pixel_size
            else:
                continue
        base_x = aw // 2
        if "top_mark_y" in glyph_def:
            base_y = glyph_def["top_mark_y"] * pixel_size
            top_bases[glyph_name] = (base_x, base_y)
        if "bottom_mark_y" in glyph_def:
            base_y = glyph_def["bottom_mark_y"] * pixel_size
            bottom_bases[glyph_name] = (base_x, base_y)

    if not top_bases and not bottom_bases:
        return None

    lines = ["feature mark {"]

    # Emit markClass definitions for standard marks
    for glyph_name in sorted(top_marks):
        ax, ay = top_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_top;")
    for glyph_name in sorted(bottom_marks):
        ax, ay = bottom_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_bottom;")
    # Emit markClass definitions for adjusted marks (each gets its own class)
    for glyph_name in sorted(adjusted_marks):
        ax, ay, _, _ = adjusted_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_{glyph_name};")

    # Emit lookup for standard top marks
    if top_marks and top_bases:
        lines.append("")
        lines.append("    lookup mark_top {")
        for glyph_name in sorted(top_bases):
            bx, by = top_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_top;")
        lines.append("    } mark_top;")

    # Emit lookup for standard bottom marks
    if bottom_marks and bottom_bases:
        lines.append("")
        lines.append("    lookup mark_bottom {")
        for glyph_name in sorted(bottom_bases):
            bx, by = bottom_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_bottom;")
        lines.append("    } mark_bottom;")

    # Emit lookups for adjusted marks (per-base anchor overrides)
    for mark_name in sorted(adjusted_marks):
        _, _, is_top, base_x_adjust = adjusted_marks[mark_name]
        bases = top_bases if is_top else bottom_bases
        if not bases:
            continue
        lines.append("")
        lines.append(f"    lookup mark_{mark_name} {{")
        for glyph_name in sorted(bases):
            bx, by = bases[glyph_name]
            adjust = base_x_adjust.get(glyph_name, 0) * pixel_size
            lines.append(f"        pos base {glyph_name} <anchor {bx + adjust} {by}> mark @mark_{mark_name};")
        lines.append(f"    }} mark_{mark_name};")

    lines.append("} mark;")
    return "\n".join(lines)


def parse_bitmap(bitmap: list) -> list[list[int]]:
    """
    Convert bitmap to a 2D array of 0s and 1s.
    Accepts either string rows ("#" = on) or int arrays.
    """
    if not bitmap:
        return []

    if isinstance(bitmap[0], str):
        return [
            [1 if c == '#' or c == '1' else 0 for c in row]
            for row in bitmap
        ]
    return bitmap


def bitmap_to_rectangles(
    bitmap: list[list[int]],
    pixel_size: int,
    y_offset: int = 0
) -> list[tuple[int, int, int, int]]:
    """
    Convert a 2D bitmap array to a list of rectangle coordinates.

    Args:
        bitmap: 2D array of 0s and 1s
        pixel_size: size of each pixel in font units
        y_offset: vertical offset in pixels (negative for descenders)
                  0 = bottom of bitmap on baseline
                  -3 = bottom of bitmap is 3 pixels below baseline

    Returns list of (x, y, width, height) tuples for each "on" pixel.
    Coordinates are in font units, with y=0 at baseline.
    """
    rectangles = []
    height = len(bitmap)

    for row_idx, row in enumerate(bitmap):
        # Flip y-axis: bitmap row 0 is top, font y increases upward
        # y_offset shifts the whole glyph (negative = below baseline)
        y = (y_offset + height - 1 - row_idx) * pixel_size

        for col_idx, pixel in enumerate(row):
            if pixel:  # Pixel is "on"
                x = col_idx * pixel_size
                rectangles.append((x, y, pixel_size, pixel_size))

    return rectangles


def draw_rectangles_to_glyph(rectangles: list[tuple], glyph_set):
    """
    Draw rectangles as a TrueType glyph using T2CharStringPen.
    Returns a T2CharString for CFF/OTF fonts.
    """
    pen = T2CharStringPen(width=0, glyphSet=glyph_set)

    for x, y, w, h in rectangles:
        # Draw counter-clockwise for CFF (outer contour)
        pen.moveTo((x, y))
        pen.lineTo((x, y + h))
        pen.lineTo((x + w, y + h))
        pen.lineTo((x + w, y))
        pen.closePath()

    return pen.getCharString()


def compose_bitmaps(
    base_bitmap: list[list[int]],
    base_y_offset: int,
    accent_bitmap: list[list[int]],
    mark_y: int,
    is_top: bool,
    accent_x_adjust: int = 0,
) -> tuple[list[list[int]], int]:
    """
    Overlay an accent bitmap onto a base bitmap.

    Args:
        base_bitmap: Parsed 2D bitmap of the base glyph
        base_y_offset: y_offset of the base glyph (negative for descenders)
        accent_bitmap: Parsed 2D bitmap of the accent glyph
        mark_y: The anchor y position (in pixels above baseline) from the base glyph
        is_top: True for top accents, False for bottom accents

    Returns:
        (combined_bitmap, combined_y_offset) where combined_bitmap is the
        merged 2D array and combined_y_offset is the y_offset for the result.
    """
    base_h = len(base_bitmap)
    accent_h = len(accent_bitmap)
    base_w = max((len(row) for row in base_bitmap), default=0)
    accent_w = max((len(row) for row in accent_bitmap), default=0)

    canvas_w = max(base_w, accent_w)

    # Base occupies pixel rows [base_y_offset, base_y_offset + base_h)
    # (in font-pixel coordinates where 0 = baseline, positive = up)
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

    # Combined extent in font-pixel coordinates
    combined_bottom = min(base_bottom, accent_bottom)
    combined_top = max(base_top, accent_top)
    combined_h = combined_top - combined_bottom

    # Build canvas (row 0 = top of combined glyph)
    canvas = [[0] * canvas_w for _ in range(combined_h)]

    def blit(bitmap, bm_w, bm_bottom, x_adjust=0):
        """Blit a bitmap onto the canvas, centered horizontally."""
        x_off = (canvas_w - bm_w) // 2 + x_adjust
        bm_h = len(bitmap)
        for row_idx, row in enumerate(bitmap):
            # bitmap row 0 is top; font-pixel y for this row:
            pixel_y = bm_bottom + bm_h - 1 - row_idx
            # canvas row for this pixel_y:
            canvas_row = combined_top - 1 - pixel_y
            for col_idx, val in enumerate(row):
                if val:
                    canvas[canvas_row][x_off + col_idx] = 1

    blit(base_bitmap, base_w, base_bottom)
    blit(accent_bitmap, accent_w, accent_bottom, accent_x_adjust)

    return canvas, combined_bottom


def resolve_composite(
    glyph_name: str,
    glyph_def: dict,
    glyphs_def: dict,
    is_proportional: bool,
) -> tuple[list[list[int]], int]:
    """
    Resolve a composite glyph definition into a bitmap and y_offset.

    Args:
        glyph_name: Name of the composite glyph (for error messages)
        glyph_def: The composite glyph definition (has 'base', 'top'/'bottom')
        glyphs_def: All glyph definitions (to look up base and accent)
        is_proportional: Whether building the proportional variant

    Returns:
        (bitmap, y_offset) for the resolved composite
    """
    base_name = glyph_def["base"]

    # Try .prop variant first if building proportional font
    if is_proportional and base_name + ".prop" in glyphs_def:
        base_ref = base_name + ".prop"
    else:
        base_ref = base_name

    base_glyph = glyphs_def.get(base_ref)
    if base_glyph is None:
        raise ValueError(
            f"Composite glyph '{glyph_name}' references base '{base_name}' which doesn't exist"
        )
    base_bitmap = parse_bitmap(base_glyph.get("bitmap", []))
    base_y_offset = base_glyph.get("y_offset", 0)

    result_bitmap = base_bitmap
    result_y_offset = base_y_offset

    # Build mapping from spacing accent bitmap identity -> base_x_adjust
    # (YAML aliases make the combining mark's bitmap the same object as the
    # spacing accent's bitmap, so we can use 'is' for matching)
    accent_adjusts = {}
    for gn, gd in glyphs_def.items():
        if gd.get("is_mark") and "base_x_adjust" in gd:
            bitmap_obj = gd.get("bitmap")
            if bitmap_obj is not None:
                accent_adjusts[id(bitmap_obj)] = gd["base_x_adjust"]

    if "top" in glyph_def:
        accent_name = glyph_def["top"]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references top accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get("top_mark_y")
        if mark_y is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' needs top_mark_y on base '{base_ref}'"
            )
        x_adj = accent_adjusts.get(id(accent_glyph.get("bitmap")), {})
        accent_x_adjust = x_adj.get(base_name, 0)
        result_bitmap, result_y_offset = compose_bitmaps(
            result_bitmap, result_y_offset, accent_bitmap, mark_y, is_top=True,
            accent_x_adjust=accent_x_adjust,
        )

    if "bottom" in glyph_def:
        accent_name = glyph_def["bottom"]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references bottom accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get("bottom_mark_y")
        if mark_y is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' needs bottom_mark_y on base '{base_ref}'"
            )
        x_adj = accent_adjusts.get(id(accent_glyph.get("bitmap")), {})
        accent_x_adjust = x_adj.get(base_name, 0)
        result_bitmap, result_y_offset = compose_bitmaps(
            result_bitmap, result_y_offset, accent_bitmap, mark_y, is_top=False,
            accent_x_adjust=accent_x_adjust,
        )

    return result_bitmap, result_y_offset


def build_font(glyph_data: dict, output_path: Path, is_proportional: bool = False):
    """
    Build font from glyph data dictionary.
    Creates a CFF-based OpenType font (.otf).

    Args:
        glyph_data: Dictionary containing metadata and glyph definitions
        output_path: Path to write the font file
        is_proportional: If True, build proportional font variant
                        (uses .prop glyphs as defaults, no ss01 feature)
    """
    metadata = glyph_data.get("metadata", {})
    glyphs_def = glyph_data["glyphs"]

    # Filter out .unused glyphs — they're stubs not yet ready for compilation
    glyphs_def = {k: v for k, v in glyphs_def.items() if ".unused" not in k}

    # For proportional font, transform glyphs: .prop becomes default
    if is_proportional:
        glyphs_def = prepare_proportional_glyphs(glyphs_def)

    # Font name differs for proportional variant
    base_font_name = metadata["font_name"]
    if is_proportional:
        font_name = base_font_name + " Sans"
    else:
        font_name = base_font_name + " Mono"
    version = metadata["version"]
    units_per_em = metadata["units_per_em"]
    pixel_size = metadata["pixel_size"]
    ascender = metadata["ascender"]
    descender = metadata["descender"]
    cap_height = metadata["cap_height"]
    x_height = metadata["x_height"]

    # Build glyph order (must include .notdef first)
    # For mono font, exclude .prop glyphs entirely
    glyph_names = [
        name for name in glyphs_def.keys()
        if name not in (".notdef", "space")
        and (is_proportional or not is_proportional_glyph(name))
    ]
    glyph_order = [".notdef", "space"] + sorted(glyph_names)

    # Build character map (Unicode codepoint -> glyph name)
    # Exclude .prop glyphs - they have no direct Unicode mapping
    postscript_glyph_names = load_postscript_glyph_names()
    cmap = {32: "space"}  # Always include space
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            continue  # Proportional variants are accessed via ss01, not cmap
        if len(glyph_name) == 1:
            cmap[ord(glyph_name)] = glyph_name
        elif glyph_name.startswith("uni") and len(glyph_name) == 7:
            # Handle uniXXXX naming convention (4 hex digits)
            try:
                codepoint = int(glyph_name[3:], 16)
                cmap[codepoint] = glyph_name
            except ValueError:
                pass  # Not a valid hex code, skip
        elif glyph_name in postscript_glyph_names:
            cmap[postscript_glyph_names[glyph_name]] = glyph_name

    # Initialize FontBuilder for CFF-based OTF
    fb = FontBuilder(units_per_em, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)

    # Build charstrings and metrics
    charstrings = {}
    metrics = {}

    # Placeholder glyph set for pen (not strictly needed for simple drawing)
    class GlyphSet:
        pass
    glyph_set = GlyphSet()

    # Standard monospace width: 7 pixels (bitmap 5 + 2 spacing)
    mono_width = 7 * pixel_size  # 350 units

    # Create .notdef glyph (simple rectangle, sized to fit mono_width)
    pen = T2CharStringPen(width=mono_width, glyphSet=glyph_set)
    pen.moveTo((50, 0))
    pen.lineTo((50, 250))
    pen.lineTo((250, 250))
    pen.lineTo((250, 0))
    pen.closePath()
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (mono_width, 50)

    # Create space glyph (empty)
    space_def = glyphs_def.get("space", {})
    space_width = space_def["advance_width"] * pixel_size
    pen = T2CharStringPen(width=space_width, glyphSet=glyph_set)
    charstrings["space"] = pen.getCharString()
    metrics["space"] = (space_width, 0)

    # Create all other glyphs
    for glyph_name in glyph_order:
        if glyph_name in (".notdef", "space"):
            continue

        glyph_def = glyphs_def.get(glyph_name, {})

        # Handle composite glyphs (have 'base' key, no 'bitmap')
        if "base" in glyph_def and not glyph_def.get("bitmap"):
            composed_bitmap, composed_y_offset = resolve_composite(
                glyph_name, glyph_def, glyphs_def, is_proportional
            )
            # Inject the resolved bitmap so the rest of the loop handles it normally
            glyph_def = dict(glyph_def)
            glyph_def["bitmap"] = composed_bitmap
            glyph_def["y_offset"] = composed_y_offset
            # Remove 'base'/'top'/'bottom' so they don't confuse later logic
            glyph_def.pop("base", None)
            glyph_def.pop("top", None)
            glyph_def.pop("bottom", None)

        bitmap = glyph_def.get("bitmap", [])

        # Validate bitmap width
        # In proportional font, all glyphs use proportional validation
        # In monospace font, only .prop suffixed glyphs use proportional validation
        is_prop_glyph = is_proportional or is_proportional_glyph(glyph_name)
        if bitmap:
            if is_prop_glyph:
                # Proportional glyphs: all rows must have consistent width
                row_widths = [len(row) for row in bitmap]
                if len(set(row_widths)) > 1:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}"
                    )
            else:
                # Monospace glyphs: check width requirements
                base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
                is_quikscript_glyph = base_name.startswith("uniE6")
                if is_quikscript_glyph:
                    # Quikscript glyphs: all rows must be exactly 5 characters wide
                    for row_idx, row in enumerate(bitmap):
                        row_len = len(row)
                        if row_len != 5:
                            raise ValueError(
                                f"Glyph '{glyph_name}' row {row_idx} has width {row_len}, expected 5"
                            )
                else:
                    # Non-Quikscript glyphs: all rows must have consistent width
                    row_widths = [len(row) for row in bitmap]
                    if len(set(row_widths)) > 1:
                        raise ValueError(
                            f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}"
                        )

        if not bitmap:
            # Empty glyph
            pen = T2CharStringPen(width=mono_width, glyphSet=glyph_set)
            charstrings[glyph_name] = pen.getCharString()
            metrics[glyph_name] = (mono_width, 0)
            continue

        # Parse and convert bitmap
        bitmap = parse_bitmap(bitmap)
        y_offset = glyph_def.get("y_offset", 0)  # negative for descenders

        # Validate bitmap height
        row_count = len(bitmap)

        # Check if this is a Quikscript glyph (uniE6xx or uniE6xx.prop)
        base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
        is_quikscript = base_name.startswith("uniE6")

        if is_quikscript:
            # Strict height validation for Quikscript glyphs
            if glyph_name in ("uniE66E", "uniE66F"):
                if row_count != 12:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has {row_count} rows, expected 12 (angled parenthesis)"
                    )
            elif y_offset == -3:
                if row_count != 9:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has y_offset=-3 but bitmap has {row_count} rows, expected 9"
                    )
            elif row_count not in (6, 9):
                raise ValueError(
                    f"Glyph '{glyph_name}' has {row_count} rows, expected 6 or 9"
                )
        # Non-Quikscript glyphs: no height restrictions

        rectangles = bitmap_to_rectangles(bitmap, pixel_size, y_offset)

        # Calculate advance width
        advance_width = glyph_def.get("advance_width")
        if advance_width is None:
            if is_prop_glyph:
                # Proportional glyphs: bitmap width + 2 pixel spacing
                max_col = max((len(row) for row in bitmap), default=0)
                advance_width = (max_col + 2) * pixel_size
            else:
                # Monospace glyphs: use fixed mono_width
                advance_width = mono_width
        else:
            advance_width *= pixel_size

        # Calculate x_offset: center glyph within advance width
        bitmap_width = max((len(row) for row in bitmap), default=0) * pixel_size
        if advance_width == 0:
            # Zero-width (combining mark): center bitmap on the origin
            x_offset = -(bitmap_width // 2)
        else:
            x_offset = (advance_width - bitmap_width) // 2

        # Calculate left side bearing (LSB) with offset applied
        if advance_width == 0:
            lsb = 0
        elif rectangles:
            lsb = min(r[0] for r in rectangles) + x_offset
        else:
            lsb = x_offset

        # Draw glyph with x_offset applied
        pen = T2CharStringPen(width=advance_width, glyphSet=glyph_set)
        for x, y, w, h in rectangles:
            pen.moveTo((x + x_offset, y))
            pen.lineTo((x + x_offset, y + h))
            pen.lineTo((x + x_offset + w, y + h))
            pen.lineTo((x + x_offset + w, y))
            pen.closePath()

        charstrings[glyph_name] = pen.getCharString()
        metrics[glyph_name] = (advance_width, lsb)

    # Setup CFF table
    ps_name = font_name.replace(" ", "") + "-Regular"
    fb.setupCFF(
        psName=ps_name,
        fontInfo={"FamilyName": font_name, "FullName": f"{font_name} Regular"},
        charStringsDict=charstrings,
        privateDict={}
    )

    # Setup horizontal metrics
    fb.setupHorizontalMetrics(metrics)

    # Setup horizontal header
    fb.setupHorizontalHeader(ascent=ascender, descent=descender)

    # Setup name table
    name_strings = {
        "familyName": {"en": font_name},
        "styleName": {"en": "Regular"},
        "uniqueFontIdentifier": f"FontBuilder:{font_name}.Regular",
        "fullName": {"en": f"{font_name} Regular"},
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

    fb.setupNameTable(name_strings)

    # Setup OS/2 table
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

    # Setup post table
    # Monospace font: isFixedPitch=1, Proportional font: isFixedPitch=0
    fb.setupPost(isFixedPitch=0 if is_proportional else 1)

    # Setup gasp table for pixel-crisp rendering
    gasp = newTable("gasp")
    gasp.gaspRange = {0xFFFF: 0x0001}  # Grid-fit only, no antialiasing
    fb.font["gasp"] = gasp

    # Add head table (required)
    fb.setupHead(unitsPerEm=units_per_em, fontRevision=version)

    # Compile OpenType features into the proportional font only
    fea_code_parts = []

    kerning_defs = glyph_data.get("kerning", {})
    if is_proportional and kerning_defs:
        kerning_groups = collect_kerning_groups(glyphs_def)
        fea_code_parts.append(generate_kern_fea(
            kerning_defs, kerning_groups, list(glyphs_def.keys()), pixel_size
        ))

    if is_proportional:
        mark_fea = generate_mark_fea(glyphs_def, pixel_size)
        if mark_fea:
            fea_code_parts.append(mark_fea)

    if fea_code_parts:
        fea_code = "\n\n".join(fea_code_parts)
        addOpenTypeFeaturesFromString(fb.font, fea_code)

        # Write .fea file alongside the font
        fea_path = output_path.with_suffix(".fea")
        fea_path.write_text(fea_code + "\n")
        print(f"  Feature code saved to: {fea_path}")

    # Save font
    fb.save(str(output_path))

    # Print summary
    variant = "proportional" if is_proportional else "monospace"
    print(f"Font saved to: {output_path}")
    print(f"  Variant: {variant}")
    print(f"  Glyphs: {len(glyph_order)}")
    print(f"  Units per em: {units_per_em}")
    print(f"  Pixel size: {pixel_size} units")


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python build_font.py <glyph_data.yaml|glyph_data/> [output_dir]")
        print("\nOutputs:")
        print("  output_dir/AbbotsMortonSpaceportMono.otf")
        print("  output_dir/AbbotsMortonSpaceportSans.otf")
        print("\nExample:")
        print("  uv run python build_font.py glyph_data/ build/")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if len(sys.argv) > 2:
        output_dir = Path(sys.argv[2])
    else:
        output_dir = Path(".")

    if not input_path.exists():
        print(f"Error: Input path not found: {input_path}")
        sys.exit(1)

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    glyph_data = load_glyph_data(input_path)

    # Build monospace font
    mono_path = output_dir / "AbbotsMortonSpaceportMono.otf"
    build_font(glyph_data, mono_path, is_proportional=False)

    # Build proportional font
    prop_path = output_dir / "AbbotsMortonSpaceportSans.otf"
    build_font(glyph_data, prop_path, is_proportional=True)


if __name__ == "__main__":
    main()
