# Abbots Morton Spaceport Mono developer guide

This document explains how to extend and modify the Abbots Morton Spaceport Mono pixel font.

## Build system

The font is built from YAML glyph definitions using fonttools:

```bash
make all
```

Dependencies are managed with `uv` and defined in `pyproject.toml`.

## Glyph definitions

Glyphs are defined in `glyph_data.yaml` using bitmap patterns:

```yaml
glyphs:
  A:
    advance_width: 12      # width in pixels
    y_offset: -1           # vertical shift (negative = below baseline)
    bitmap:
      - "     ##    "
      - "    ####   "
      # ... each row is a string, # = pixel on
```

- `advance_width`: horizontal space the glyph occupies (in pixels)
- `y_offset`: shifts the glyph vertically; use negative values for descenders
- `bitmap`: array of strings where `#` represents an "on" pixel

## Coordinate system

- All coordinates in YAML are in **pixels**
- The build script converts to font units using `pixel_size` (default: 56 units/pixel)
- `y_offset` shifts the entire glyph vertically:
  - `0` = bottom of bitmap sits on baseline
  - `-2` = bottom of bitmap is 2 pixels below baseline (for descenders)
  - `10` = bottom of bitmap is 10 pixels above baseline (for combining marks)

## Testing

Open `test/test.html` in a browser to test the font interactively.
