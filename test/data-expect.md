# `data-expect` attribute format

The `data-expect` attribute on `<td>`, `<span>`, and `<dd>` elements in `test/index.html` describes the expected HarfBuzz shaping output for the Senior Sans font. The test runner (`test_shaping.py`) parses these attributes and verifies glyph selection and cursive attachment.

The `data-expect-noncanonically` attribute uses the exact same syntax and test semantics as `data-expect`. It marks noncanonical Senior Quikscript joins that are valuable to test but are not found in Read's manual.

## Glyph tokens

| Syntax        | Meaning                                          | Example  |
| ------------- | ------------------------------------------------ | -------- |
| `·LetterName` | Base Quikscript letter (maps to `qsLetterName`)  | `·Bay`   |
| `·-ing`       | Special case for `qsIng`                         | `·-ing`  |
| `\X`          | Literal character (via glyph names or `uniXXXX`) | `\.`     |
| `◊name`       | Special glyph by name (`◊space`, `◊ZWNJ`)        | `◊space` |

## Variant assertions

Append dot-separated modifiers to assert properties of the selected glyph
variant:

| Modifier    | Matches glyph names containing | Example         |
| ----------- | ------------------------------ | --------------- |
| `.alt`      | `alt`                          | `·No.alt`       |
| `.half`     | `half`                         | `·Pea.half`     |
| `.extended` | `extended`                     | `·Roe.extended` |

Prefix a modifier with `!` to assert the glyph name does **not** contain that string:

| Modifier     | Rejects glyph names containing | Example          |
| ------------ | ------------------------------ | ---------------- |
| `.!alt`      | `alt`                          | `·No.!alt`       |
| `.!extended` | `extended`                     | `·Roe.!extended` |

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

## Duplicates

Three levels of duplicate exist between two elements that both carry a `data-expect` attribute:

| Level             | Same text content | Same assertions | Same whitespace |
| ----------------- | ----------------- | --------------- | --------------- |
| Content duplicate | yes               | no              | —               |
| Total duplicate   | yes               | yes             | no              |
| Exact duplicate   | yes               | yes             | yes             |

"Same assertions" means the `data-expect` values are identical after collapsing runs of whitespace to a single space and trimming leading/trailing whitespace.

When the same word (text content) appears more than once in the test corpus, only one occurrence should carry the `data-expect` attribute — preferably the earliest. Later occurrences keep their text content but lose the attribute (and bare `<span>` wrappers are unwrapped).

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
