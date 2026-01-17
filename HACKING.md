# Abbots Morton Spaceport Mono developer guide

## Making it go

```bash
make all
```

Dependencies are managed with `uv` and defined in `pyproject.toml`.

## Coordinate system

- All coordinates in YAML are in **pixels**
- The build script converts to font units using `pixel_size` from the metadata
- `y_offset` shifts the entire glyph vertically:
  - `0` = bottom of bitmap sits on baseline
  - `-2` = bottom of bitmap is 2 pixels below baseline (for descenders)
  - `10` = bottom of bitmap is 10 pixels above baseline (for combining marks)

## Testing

Open `test/test.html` in a browser to test the font interactively.
