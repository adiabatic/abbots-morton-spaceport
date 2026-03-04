#import "../style.typ": *

= Putting it all together: worked example

Use the real sequence from `test/index.html` data-expect:

`·Way+Utter ~x~ ·Roe ~b~ ·Low ~x~ ·Day ~b~ ·Zoo.half`

This corresponds to the word “worlds” in Quikscript phonetic decomposition.

== 1. User input (Unicode code points)

#code_stream((
  "U+E661",
  "U+E67A",
  "U+E668",
  "U+E667",
  "U+E653",
  "U+E65B",
))

== 2. cmap resolves to base glyphs

#code_stream((
  "qsWay",
  "qsUtter",
  "qsRoe",
  "qsLow",
  "qsDay",
  "qsZoo",
))

== 3. `calt` backward phase (entry selection)

`qsZoo` sees preceding exit height `y=0` from `qsDay`, so it is replaced with `qsZoo.half` (baseline-entry half form).

#code_stream((
  "qsWay",
  "qsUtter",
  "qsRoe",
  "qsLow",
  "qsDay",
  "qsZoo.half",
))

== 4. `calt` forward phase (exit selection)

`qsRoe` sees that `qsLow` enters at baseline (`y=0`), so `qsRoe` switches to `qsRoe.exit-baseline`.

#code_stream((
  "qsWay",
  "qsUtter",
  "qsRoe.exit-baseline",
  "qsLow",
  "qsDay",
  "qsZoo.half",
))

== 5. Ligature substitution step

`lookup calt_liga` replaces `qsWay qsUtter` with `qsWay_qsUtter`.

#code_stream((
  "qsWay_qsUtter",
  "qsRoe.exit-baseline",
  "qsLow",
  "qsDay",
  "qsZoo.half",
))

== 6. `curs` attachment positioning

Now GPOS aligns anchors along the chain:

- `qsWay_qsUtter` -> `qsRoe.exit-baseline` joins at `y=5`.
- `qsRoe.exit-baseline` -> `qsLow` joins at `y=0`.
- `qsLow` -> `qsDay` joins at `y=5`.
- `qsDay` -> `qsZoo.half` joins at `y=0`.

== 7. Render

The renderer draws the final positioned stream.

#key_idea([
The visible result is one shaped chain, but internally it is a pipeline of small deterministic substitutions and anchor alignments.
])

