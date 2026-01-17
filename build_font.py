#!/usr/bin/env python3
"""
Build a pixel font from bitmap glyph definitions.
Uses fonttools FontBuilder to create OTF output.

Usage:
    uv run python build_font.py glyph_data.yaml [output.otf]
"""

import sys
from pathlib import Path

import yaml

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import newTable


def load_glyph_data(yaml_path: Path) -> dict:
    """Load glyph definitions from YAML file."""
    with open(yaml_path) as f:
        return yaml.safe_load(f)


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
                  -2 = bottom of bitmap is 2 pixels below baseline

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


def build_font(glyph_data: dict, output_path: Path):
    """
    Build font from glyph data dictionary.
    Creates a CFF-based OpenType font (.otf).
    """
    metadata = glyph_data.get("metadata", {})
    glyphs_def = glyph_data["glyphs"]

    font_name = metadata["font_name"]
    units_per_em = metadata["units_per_em"]
    pixel_size = metadata["pixel_size"]
    ascender = metadata["ascender"]
    descender = metadata["descender"]
    cap_height = metadata["cap_height"]
    x_height = metadata["x_height"]

    # Build glyph order (must include .notdef first)
    glyph_names = [name for name in glyphs_def.keys() if name not in (".notdef", "space")]
    glyph_order = [".notdef", "space"] + sorted(glyph_names)

    # Build character map (Unicode codepoint -> glyph name)
    cmap = {32: "space"}  # Always include space
    for glyph_name, glyph_def in glyphs_def.items():
        if len(glyph_name) == 1:
            cmap[ord(glyph_name)] = glyph_name
        elif glyph_name.startswith("uni") and len(glyph_name) == 7:
            # Handle uniXXXX naming convention (4 hex digits)
            try:
                codepoint = int(glyph_name[3:], 16)
                cmap[codepoint] = glyph_name
            except ValueError:
                pass  # Not a valid hex code, skip

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

    # Create .notdef glyph (simple rectangle outline)
    pen = T2CharStringPen(width=500, glyphSet=glyph_set)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((450, 700))
    pen.lineTo((450, 0))
    pen.closePath()
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (500, 50)

    # Create space glyph (empty)
    space_def = glyphs_def.get("space", {})
    space_width = space_def.get("advance_width", 4) * pixel_size
    pen = T2CharStringPen(width=space_width, glyphSet=glyph_set)
    charstrings["space"] = pen.getCharString()
    metrics["space"] = (space_width, 0)

    # Create all other glyphs
    for glyph_name in glyph_order:
        if glyph_name in (".notdef", "space"):
            continue

        glyph_def = glyphs_def.get(glyph_name, {})
        bitmap = glyph_def.get("bitmap", [])

        # Validate bitmap width: all rows must be exactly 5 characters wide
        if bitmap:
            for row_idx, row in enumerate(bitmap):
                row_len = len(row)
                if row_len != 5:
                    raise ValueError(
                        f"Glyph '{glyph_name}' row {row_idx} has width {row_len}, expected 5"
                    )

        if not bitmap:
            # Empty glyph
            pen = T2CharStringPen(width=pixel_size * 4, glyphSet=glyph_set)
            charstrings[glyph_name] = pen.getCharString()
            metrics[glyph_name] = (pixel_size * 4, 0)
            continue

        # Parse and convert bitmap
        bitmap = parse_bitmap(bitmap)
        y_offset = glyph_def.get("y_offset", 0)  # negative for descenders

        # Validate bitmap height
        row_count = len(bitmap)
        if y_offset == -3:
            if row_count != 9:
                raise ValueError(
                    f"Glyph '{glyph_name}' has y_offset=-3 but bitmap has {row_count} rows, expected 9"
                )
        else:
            if row_count not in (6, 9):
                raise ValueError(
                    f"Glyph '{glyph_name}' has {row_count} rows, expected 6 or 9"
                )

        rectangles = bitmap_to_rectangles(bitmap, pixel_size, y_offset)

        # Calculate advance width
        advance_width = glyph_def.get("advance_width")
        if advance_width is None:
            # Default to bitmap width + 1 pixel spacing
            max_col = max((len(row) for row in bitmap), default=0)
            advance_width = max_col + 1
        advance_width *= pixel_size

        # Calculate left side bearing (LSB)
        if rectangles:
            lsb = min(r[0] for r in rectangles)
        else:
            lsb = 0

        # Draw glyph
        pen = T2CharStringPen(width=advance_width, glyphSet=glyph_set)
        for x, y, w, h in rectangles:
            pen.moveTo((x, y))
            pen.lineTo((x, y + h))
            pen.lineTo((x + w, y + h))
            pen.lineTo((x + w, y))
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
        "version": "Version 1.0",
    }
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
    )

    # Setup post table
    fb.setupPost()

    # Setup gasp table for pixel-crisp rendering
    gasp = newTable("gasp")
    gasp.gaspRange = {0xFFFF: 0x0001}  # Grid-fit only, no antialiasing
    fb.font["gasp"] = gasp

    # Add head table (required)
    fb.setupHead(unitsPerEm=units_per_em)

    # Save font
    fb.save(str(output_path))
    print(f"Font saved to: {output_path}")
    print(f"  Glyphs: {len(glyph_order)}")
    print(f"  Units per em: {units_per_em}")
    print(f"  Pixel size: {pixel_size} units")


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python build_font.py <glyph_data.yaml> [output.otf]")
        print("\nExample:")
        print("  uv run python build_font.py glyph_data.yaml output/FontName.otf")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])
    else:
        output_path = input_path.with_suffix(".otf")

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    glyph_data = load_glyph_data(input_path)
    build_font(glyph_data, output_path)


if __name__ == "__main__":
    main()
