#import "../style.typ": *

= How letters join: the concept

Before OpenType mechanics, the visual model is simple: each glyph may have an entry point (where a pen stroke arrives) and an exit point (where it leaves).

== a. Entry and exit points

In YAML, anchors are pixel coordinates `[x, y]` relative to baseline. Example:

```yaml
qsPea.prop:
  cursive_exit: [5, 0]

qsRoe.prop:
  cursive_entry:
    - [1, 0]
    - [1, 5]
```

`·Roe` can accept connections at baseline and x-height. `·Pea` exits at baseline.

== b. Height matching

A join only works when the left exit Y and right entry Y match.

- Match: exit `y=0` to entry `y=0`.
- Mismatch: exit `y=5` to entry `y=0` (no direct join at that height).

== c. Cursive attachment idea

The engine first picks compatible glyph variants, then in GPOS `curs` it shifts glyphs so matching anchors overlap.

A simple pair:

- `·Pea` exits at baseline (`[5, 0]`).
- `·Low` enters at baseline (`[1, 0]`).

After attachment, `·Low` is positioned so those points coincide.

#key_idea([
Substitution decides *which shape* can connect. Cursive attachment decides *where* it sits.
])

