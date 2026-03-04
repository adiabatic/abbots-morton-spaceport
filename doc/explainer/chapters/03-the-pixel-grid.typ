#import "../style.typ": *

= The pixel grid

Abbots Morton Spaceport is a pixel design encoded as vector rectangles.

== a. Units per em and pixel size

From `glyph_data/metadata.yaml`:

```yaml
units_per_em: 550
pixel_size: 50
ascender: 550
descender: -150
x_height: 300
```

So one em is 11 pixels high (`550 / 50 = 11`).

== b. The three glyph heights

- Short letters: 6 px tall, baseline to x-height.
- Tall letters: 9 px tall, baseline to ascender zone.
- Deep letters: 9 px tall with `y_offset: -3`, so the shape dips three pixels below baseline.

== c. Bitmap representation in YAML

Each glyph is written as a list of rows. `#` means filled pixel. A comment marker in the YAML indicates the row used as the x-height guide when drawing/editing.

== d. `y_offset`

Deep letters shift down with negative offset. Example:

```yaml
qsBay:
  y_offset: -3
  bitmap:
    - "   # "
    - "   # "
    - "   # "
    - "   # "
    - "   # "
    - " ####"
    - "#  # "
    - "#  # "
    - " ##  "
```

Concrete examples:

#two_col(
  [
    #bitmap_figure([`·It` (Short, 6 px)], (
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
    ), guide_rows: (3,))
  ],
  [
    #bitmap_figure([`·Tea` (Tall, 9 px)], (
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
      "  #  ",
    ), guide_rows: (3,))
  ],
)

#bitmap_figure([`·Bay` (Deep, 9 px, `y_offset: -3`)], (
  "   # ",
  "   # ",
  "   # ",
  "   # ",
  "   # ",
  " ####",
  "#  # ",
  "#  # ",
  " ##  ",
), guide_rows: (5,))

