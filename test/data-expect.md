# `data-expect` attribute format

The `data-expect` attribute on `<td>`, `<span>`, and `<dd>` elements in `test/index.html` describes the expected HarfBuzz shaping output for the Senior Sans font. The test runner (`test_shaping.py`) parses these attributes and verifies glyph selection and cursive attachment against compiled glyph metadata.

The `data-expect-noncanonically` attribute uses the exact same syntax and test semantics as `data-expect`. It marks noncanonical Senior Quikscript joins that are valuable to test but are not found in Read's manual.

## Glyph tokens

| Syntax | Meaning | Example |
| ------ | ------- | ------- |
| `·LetterName` | Base Quikscript letter (maps to `qsLetterName`) | `·Bay` |
| `·-ing` | Special case for `qsIng` | `·-ing` |
| `\X` | Literal character (via glyph names or `uniXXXX`) | `\.` |
| `◊name` | Special glyph by name (`◊space`, `◊ZWNJ`) | `◊space` |

## Variant assertions

Append dot-separated modifiers to assert properties of the selected glyph variant. Only `.alt` and `.half` are stable semantic assertions; they map to real Quikscript concepts and are checked via compiled glyph traits.

| Modifier | Meaning | Example |
| -------- | ------- | ------- |
| `.alt` | stable alternate form | `·No.alt` |
| `.half` | stable half form | `·Pea.half` |

Prefix a modifier with `!` to assert the selected glyph does **not** carry that trait or compatibility tag.

| Modifier | Example |
| -------- | ------- |
| `.!alt` | `·No.!alt` |
| `.!half` | `·Pea.!half` |

Other modifiers used by the corpus, such as `entry`, `exit`, `extended`, `noentry`, `entry-baseline`, `exit-baseline`, `exit-xheight`, and `reaches-way-back`, are compatibility-only. They are matched against compiler-provided compatibility metadata, not against glyph-name substrings.

Variant assertions are optional. Without them, any variant of the base letter satisfies the check.

## Ligature assertions

Use `+` to assert that two letters are shaped as a single ligature glyph:

```text
·Day+Utter
```

This expects one output glyph whose compiled metadata sequence records both `qsDay` and `qsUtter`.

Variant assertions go at the end of the ligature token, after the last letter name. For example, `·Day+Utter.half` asserts a Day+Utter ligature with the `half` trait, even though the half form is on the first letter in the pair.

### Maybe-ligature assertions

Use `+?` or `+|` when the font may or may not ligate two letters:

| Syntax          | Meaning                                                      |
| --------------- | ------------------------------------------------------------ |
| `·Day+Utter`    | Must ligate into one glyph                                   |
| `·Day+?Utter`   | May ligate; if separate, connection between them unasserted   |
| `·Day+\|Utter`  | May ligate; if separate, assert a break between them          |

The test runner tries both interpretations (ligated and separated) and passes if either matches. When the token carries variant assertions, those assertions apply to the ligature glyph in the ligated interpretation and are dropped in the separated interpretation.

## Connection assertions

Tokens are separated by connection operators that describe how adjacent glyphs attach or don't:

| Operator | Meaning |
| -------- | ------- |
| (adjacency) | Joined, height unasserted |
| `~x~` | Joined at x-height (y = 5) |
| `~b~` | Joined at baseline (y = 0) |
| `~t~` | Joined at top (y = 8) |
| `~6~` | Joined at y = 6 |
| `\|` | Break (no cursive connection) |
| `\|?\|` | Break, with shape isolation NOT asserted |
| `?` | Maybe connects, or doesn't |

A "join" means the preceding glyph's exit anchor and the following glyph's entry anchor share the specified Y coordinate. A "break" means no matching anchor pair exists. A "maybe" skips the connection assertion entirely — the test passes whether or not the glyphs join. Use this for cases where the source material is ambiguous, such as an accidental pen-lift in the original manuscript.

### Break-isolation invariant

When `|` separates two Quikscript letter tokens — and likewise when `?` separates a pair that turns out not to join — the test runner additionally asserts that **neither letter influences the other's shape choice**. It re-shapes the two sides as separate HarfBuzz buffers and verifies that the glyph chosen for each token matches the in-context glyph. A disagreement means a `calt` lookup is reaching across the non-join, and the test fails with a diagnostic naming both glyph choices.

Concretely: there's no need to spell out `.!half`, `.!alt`, `.!wide`, etc. on either side of a `|` to pin down "this glyph wasn't chosen because of the other one". The runner enforces that automatically, scoped to letter-vs-letter pairs (boundary tokens like `◊space`, `◊ZWNJ`, and escaped punctuation are excluded — those exist precisely to influence neighboring shape).

### When the font legitimately leaks across a non-join: `|?|`

A few font shapes are intentional "looks-better-when-adjacent" rules that fire on glyph-name signature alone — for instance, `qsThaw.noentry-after-tall` removes Thaw's entry stub when a tall is to the left, even though no cursive join could form. In production this is naturally gated to literal adjacency: the `after:` (or `before:`) selector compiles to an OpenType backward (or forward) context lookup whose match list contains only the named families. A real space or ZWNJ between the two words sits in the immediate slot the lookup is checking, so the rule doesn't fire. But in test, `|` doesn't insert any character — the runner just concatenates the codepoints — so the lookup still fires and the isolation check correctly flags the cross-break shape choice.

Use `|?|` instead of `|` for these specific cases. It still asserts that the two glyphs do not cursive-attach (no shared entry/exit Y), but skips the isolation invariant. Reach for `|?|` only when the cross-break shape difference is purely cosmetic / glyph-name-only (same bitmap, same effective cursive position), and adjacent letters in real text would naturally suppress the rule because of the intervening space or ZWNJ glyph.

## Duplicates

Three levels of duplicate exist between two elements that both carry a `data-expect` attribute:

| Level | Same text content | Same assertions | Same whitespace |
| ----- | ----------------- | --------------- | --------------- |
| Content duplicate | yes | no | — |
| Total duplicate | yes | yes | no |
| Exact duplicate | yes | yes | yes |

"Same assertions" means the `data-expect` values are identical after collapsing runs of whitespace to a single space and trimming leading/trailing whitespace.

When the same word (text content) appears more than once in the test corpus, only one occurrence should carry the `data-expect` attribute, preferably the earliest. Later occurrences keep their text content but lose the attribute, and bare `<span>` wrappers are unwrapped.

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

Day-Utter ligature, then a break, then Low.

```text
·Low ~x~ ·Day+?Utter ~x~ ·Roe
```

Ligated path (3 glyphs): Low joined at x-height to Day+Utter ligature, joined at x-height to Roe. Separated path (4 glyphs): Low joined at x-height to Day, connection to Utter unasserted, Utter joined at x-height to Roe.
