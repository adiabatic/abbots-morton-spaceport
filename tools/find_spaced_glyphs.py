#!/usr/bin/env python3
"""
Find punctuation glyphs with empty space on the sides in a pixel font.

This script analyzes an OTF font to find punctuation glyphs whose actual
bitmap content is narrower than the standard monospace width (5 pixels).
These "spaced out" glyphs have padding on the sides.

Usage:
    uv run python tools/find_spaced_glyphs.py test/DepartureMono-Regular.otf
"""

import argparse
import sys
import unicodedata

from fontTools.ttLib import TTFont

from extract_glyph import glyph_to_bitmap


# Punctuation Unicode categories
PUNCTUATION_CATEGORIES = {
    "Pc",  # Connector punctuation
    "Pd",  # Dash punctuation
    "Pe",  # Close punctuation
    "Pf",  # Final punctuation
    "Pi",  # Initial punctuation
    "Po",  # Other punctuation
    "Ps",  # Open punctuation
    "Sm",  # Math symbol
    "Sc",  # Currency symbol
    "Sk",  # Modifier symbol
    "So",  # Other symbol
}


def get_bitmap_width(bitmap):
    """
    Calculate the actual content width of a bitmap (excluding empty columns).

    Returns the width of the narrowest bounding box containing all '#' pixels.
    """
    if not bitmap:
        return 0

    # Find leftmost and rightmost '#' across all rows
    min_col = float("inf")
    max_col = -1

    for row in bitmap:
        for col, char in enumerate(row):
            if char == "#":
                min_col = min(min_col, col)
                max_col = max(max_col, col)

    if max_col == -1:
        return 0  # Empty bitmap

    return max_col - min_col + 1


def get_punctuation_glyphs(font_path):
    """
    Get all punctuation glyph names and their Unicode code points from a font.

    Returns a list of (glyph_name, unicode_codepoint) tuples.
    """
    font = TTFont(font_path)
    cmap = font.getBestCmap()

    punctuation_glyphs = []

    for codepoint, glyph_name in cmap.items():
        try:
            char = chr(codepoint)
            category = unicodedata.category(char)
            if category in PUNCTUATION_CATEGORIES:
                punctuation_glyphs.append((glyph_name, codepoint))
        except (ValueError, OverflowError):
            # Skip invalid codepoints
            pass

    return sorted(punctuation_glyphs, key=lambda x: x[1])


def find_spaced_glyphs(font_path, max_width=4):
    """
    Find punctuation glyphs with bitmap width less than standard monospace.

    Args:
        font_path: Path to the OTF font file
        max_width: Maximum bitmap width to consider "spaced out" (default 4,
                   since standard monospace is 5 pixels)

    Returns:
        List of dicts with keys: name, width, unicode
    """
    punctuation_glyphs = get_punctuation_glyphs(font_path)
    spaced_glyphs = []

    for glyph_name, codepoint in punctuation_glyphs:
        try:
            bitmap, _ = glyph_to_bitmap(font_path, glyph_name)
            width = get_bitmap_width(bitmap)

            if 0 < width <= max_width:
                spaced_glyphs.append({
                    "name": glyph_name,
                    "width": width,
                    "unicode": f"U+{codepoint:04X}",
                })
        except Exception as e:
            print(f"Warning: Could not process glyph '{glyph_name}': {e}",
                  file=sys.stderr)

    return spaced_glyphs


def write_yaml(glyphs, output_path):
    """Write the spaced glyphs to a YAML file."""
    with open(output_path, "w") as f:
        f.write("# Punctuation glyphs with empty side space in DepartureMono\n")
        f.write("# These glyphs have bitmap width < 5 pixels (narrower than standard monospace)\n")
        f.write("\n")
        f.write("glyphs:\n")

        for glyph in glyphs:
            f.write(f"  - name: {glyph['name']}\n")
            f.write(f"    width: {glyph['width']}\n")
            f.write(f"    unicode: \"{glyph['unicode']}\"\n")


def main():
    parser = argparse.ArgumentParser(
        description="Find punctuation glyphs with empty space on sides"
    )
    parser.add_argument(
        "font_path",
        help="Path to OTF font file",
    )
    parser.add_argument(
        "-o", "--output",
        default="SPACED_OUT_GLYPHS.yaml",
        help="Output YAML file (default: SPACED_OUT_GLYPHS.yaml)",
    )
    parser.add_argument(
        "-w", "--max-width",
        type=int,
        default=4,
        help="Maximum bitmap width to consider 'spaced out' (default: 4)",
    )

    args = parser.parse_args()

    print(f"Analyzing font: {args.font_path}")
    spaced_glyphs = find_spaced_glyphs(args.font_path, args.max_width)

    print(f"Found {len(spaced_glyphs)} spaced-out glyphs:")
    for glyph in spaced_glyphs:
        print(f"  {glyph['name']:20} width={glyph['width']}  {glyph['unicode']}")

    write_yaml(spaced_glyphs, args.output)
    print(f"\nResults written to: {args.output}")


if __name__ == "__main__":
    main()
