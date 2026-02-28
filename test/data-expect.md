# `data-expect` attribute format

The `data-expect` attribute on `<td>` cells in `test/index.html` describes
the expected HarfBuzz shaping output for the Senior Sans font. The test
runner (`test_shaping.py`) parses these attributes and verifies glyph
selection and cursive attachment.

## Glyph tokens

| Syntax        | Meaning                                          | Example |
| ------------- | ------------------------------------------------ | ------- |
| `·LetterName` | Base Quikscript letter (maps to `qsLetterName`)  | `·Bay`  |
| `·-ing`       | Special case for `qsIng`                         | `·-ing` |

## Variant assertions

Append dot-separated modifiers to assert properties of the selected glyph
variant:

| Modifier    | Matches glyph names containing | Example         |
| ----------- | ------------------------------ | --------------- |
| `.alt`      | `alt`                          | `·No.alt`       |
| `.half`     | `half`                         | `·Pea.half`     |
| `.extended` | `extended`                     | `·Roe.extended` |

Variant assertions are optional. Without them, any variant of the base
letter satisfies the check.

## Ligature assertions

Use `+` to assert that two letters are shaped as a single ligature glyph:

```text
·Day+Utter
```

This expects one output glyph whose name contains both `qsDay` and
`qsUtter` (e.g. `qsDay_qsUtter`).

## Connection assertions

Tokens are separated by connection operators that describe how adjacent
glyphs attach (or don't):

| Operator    | Meaning                        |
| ----------- | ------------------------------ |
| (adjacency) | Joined, height unasserted      |
| `~x~`       | Joined at x-height (y = 5)     |
| `~b~`       | Joined at baseline (y = 0)     |
| `~t~`       | Joined at top (y = 8)          |
| `~6~`       | Joined at y = 6                |
| ` \| `      | Break (no cursive connection)  |

A "join" means the preceding glyph's exit anchor and the following glyph's
entry anchor share the specified Y coordinate. A "break" means no matching
anchor pair exists.

## Full examples

```text
·Bay ~b~ ·Roe
```

Bay followed by Roe, connected at the baseline.

```text
·No ~x~ ·Owe
```

No followed by Owe, connected at x-height.

```text
·Tea ~b~ ·See
```

Tea followed by See, connected at the baseline.

```text
·Day+Utter | ·Low
```

Day-Utter ligature (one glyph), then a break, then Low.
