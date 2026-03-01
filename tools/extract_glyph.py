#!/usr/bin/env python3
"""
Extract glyph bitmaps from OTF fonts for pixel fonts like Departure Mono.

This tool extracts glyphs from CFF-based OpenType fonts where each "pixel"
is a 50×50 unit square. It outputs YAML-formatted bitmaps or compares
glyphs between two fonts.

Usage:
    # Extract a glyph bitmap
    uv run python tools/extract_glyph.py test/DepartureMono-Regular.otf zero

    # Compare a glyph between two fonts
    uv run python tools/extract_glyph.py --compare zero \\
        test/DepartureMono-Regular.otf \\
        test/AbbotsMortonSpaceportMono.otf
"""

import argparse
import sys

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen

PIXEL_SIZE = 50


def extract_contours(recording):
    """Convert pen recording to list of polygon point lists."""
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


def point_in_polygon(x, y, polygon):
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


def glyph_to_bitmap(font_path, glyph_name, include_padding=True):
    """
    Extract a glyph as a bitmap from an OTF font.

    Args:
        font_path: Path to the font file
        glyph_name: Name of the glyph to extract
        include_padding: If True, include left_side_bearing padding in bitmap

    Returns:
        tuple: (bitmap_rows, y_offset) where bitmap_rows is a list of strings
               using '#' for filled pixels and ' ' for empty, and y_offset
               is the vertical offset in pixels (negative for descenders)
    """
    font = TTFont(font_path)
    glyphset = font.getGlyphSet()

    if glyph_name not in glyphset:
        raise ValueError(f"Glyph '{glyph_name}' not found in font")

    glyph = glyphset[glyph_name]

    pen = RecordingPen()
    glyph.draw(pen)
    contours = extract_contours(pen.value)

    if not contours:
        return [], 0  # Empty glyph (like space)

    # Get bounds
    all_x = [pt[0] for c in contours for pt in c]
    all_y = [pt[1] for c in contours for pt in c]
    x_min, x_max = min(all_x), max(all_x)
    y_min, y_max = min(all_y), max(all_y)

    # Get left_side_bearing for padding
    hmtx = font["hmtx"]
    advance_width, lsb = hmtx[glyph_name]

    # Calculate grid dimensions
    if include_padding:
        # Include left padding based on left_side_bearing
        left_padding = int(lsb / PIXEL_SIZE)
        grid_x_start = 0
        grid_width = int(x_max / PIXEL_SIZE)
    else:
        left_padding = 0
        grid_x_start = int(x_min / PIXEL_SIZE)
        grid_width = int((x_max - x_min) / PIXEL_SIZE)

    grid_height = int((y_max - y_min) / PIXEL_SIZE)

    bitmap = []
    for row in range(grid_height):
        y = y_max - (row * PIXEL_SIZE) - (PIXEL_SIZE // 2)
        row_str = ""
        for col in range(grid_width):
            x = (grid_x_start + col) * PIXEL_SIZE + (PIXEL_SIZE // 2)
            count = sum(1 for c in contours if point_in_polygon(x, y, c))
            row_str += "#" if count % 2 == 1 else " "
        bitmap.append(row_str)

    # Calculate y_offset (how many pixels below baseline)
    y_offset = int(y_min / PIXEL_SIZE)

    return bitmap, y_offset


def get_glyph_metrics(font_path, glyph_name):
    """
    Get detailed metrics for a glyph.

    Returns:
        dict with keys: advance_width, xMin, yMin, xMax, yMax, left_side_bearing
    """
    font = TTFont(font_path)
    glyphset = font.getGlyphSet()

    if glyph_name not in glyphset:
        raise ValueError(f"Glyph '{glyph_name}' not found in font")

    glyph = glyphset[glyph_name]

    # Get advance width from hmtx table
    hmtx = font["hmtx"]
    advance_width, lsb = hmtx[glyph_name]

    # Get bounds by drawing
    pen = RecordingPen()
    glyph.draw(pen)
    contours = extract_contours(pen.value)

    if not contours:
        return {
            "advance_width": advance_width,
            "xMin": 0,
            "yMin": 0,
            "xMax": 0,
            "yMax": 0,
            "left_side_bearing": lsb,
        }

    all_x = [pt[0] for c in contours for pt in c]
    all_y = [pt[1] for c in contours for pt in c]

    return {
        "advance_width": advance_width,
        "xMin": int(min(all_x)),
        "yMin": int(min(all_y)),
        "xMax": int(max(all_x)),
        "yMax": int(max(all_y)),
        "left_side_bearing": lsb,
    }


def print_bitmap_yaml(glyph_name, bitmap, y_offset):
    """Print bitmap in YAML format suitable for glyph_data.yaml."""
    print(f"  {glyph_name}:")
    if y_offset != 0:
        print(f"    y_offset: {y_offset}")
    print("    bitmap:")
    for row in bitmap:
        print(f'      - "{row}"')


def compare_glyphs(glyph_name, font1_path, font2_path):
    """
    Compare a glyph between two fonts.

    Returns:
        bool: True if all metrics match, False otherwise
    """
    metrics1 = get_glyph_metrics(font1_path, glyph_name)
    metrics2 = get_glyph_metrics(font2_path, glyph_name)

    bitmap1, y_offset1 = glyph_to_bitmap(font1_path, glyph_name)
    bitmap2, y_offset2 = glyph_to_bitmap(font2_path, glyph_name)

    # Get font names for display
    font1_name = TTFont(font1_path)["name"].getDebugName(1) or "Font1"
    font2_name = TTFont(font2_path)["name"].getDebugName(1) or "Font2"

    # Truncate names for display
    font1_short = font1_name[:20]
    font2_short = font2_name[:20]

    print(f"Comparing '{glyph_name}' between fonts:\n")
    print(f"{'':25} {font1_short:20} {font2_short:20}")

    all_match = True
    metric_names = ["advance_width", "xMin", "yMin", "xMax", "yMax", "left_side_bearing"]

    for metric in metric_names:
        v1 = metrics1[metric]
        v2 = metrics2[metric]
        match = v1 == v2
        if not match:
            all_match = False
        status = "\u2713" if match else "\u2717"
        print(f"{metric:25} {v1:<20} {v2:<20} {status}")

    # Compare bitmaps
    print("\nBitmap (visual check):")
    for row in bitmap1:
        print(f'  "{row}"')

    if bitmap1 != bitmap2:
        all_match = False
        print("\nBitmap from second font differs:")
        for row in bitmap2:
            print(f'  "{row}"')

    if y_offset1 != y_offset2:
        all_match = False
        print(f"\ny_offset differs: {y_offset1} vs {y_offset2}")

    print()
    if all_match:
        print("Result: \u2713 ALL METRICS MATCH")
    else:
        print("Result: \u2717 METRICS DO NOT MATCH")

    return all_match


def main():
    parser = argparse.ArgumentParser(
        description="Extract glyph bitmaps from OTF fonts"
    )
    parser.add_argument(
        "--compare",
        metavar="GLYPH",
        help="Compare glyph between two fonts instead of extracting",
    )
    parser.add_argument(
        "font_path",
        help="Path to OTF font file",
    )
    parser.add_argument(
        "glyph_or_font2",
        help="Glyph name (extract mode) or second font path (compare mode)",
    )
    parser.add_argument(
        "font2_path",
        nargs="?",
        help="Second font path (compare mode only)",
    )

    args = parser.parse_args()

    if args.compare:
        # Compare mode
        glyph_name = args.compare
        font1_path = args.font_path
        font2_path = args.glyph_or_font2

        if not font2_path:
            print("Error: Compare mode requires two font paths", file=sys.stderr)
            sys.exit(1)

        try:
            match = compare_glyphs(glyph_name, font1_path, font2_path)
            sys.exit(0 if match else 1)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Extract mode
        glyph_name = args.glyph_or_font2
        font_path = args.font_path

        try:
            bitmap, y_offset = glyph_to_bitmap(font_path, glyph_name)
            if not bitmap:
                print(f"Glyph '{glyph_name}' is empty (no contours)")
            else:
                print_bitmap_yaml(glyph_name, bitmap, y_offset)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
