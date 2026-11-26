# `data-expect` attribute format

The `data-expect` attribute on `<td>`, `<span>`, and `<dd>` elements in the test corpus HTML files (`site/index.html`, `site/the-manual.html`, and `site/extra-senior-words.html`) describes the expected HarfBuzz shaping output. `parse_expect` in `rebuild/validation/expect.py` is the grammar's authority, and `rebuild/validation/check.py` is the checker. The root `conftest.py` collects each attribute as a pytest item and checks it against the rebuilt fonts in `rebuild/out/fonts/`, which the session builds first; the build's pin gate (`rebuild/pipeline/manual_pins.py`) checks the same pins against the font it has just compiled. Text is shaped with the Senior Sans font, except text inside a `force-junior` span, which is shaped with the Junior font and checked for glyph identity only.

The `data-expect-noncanonically` attribute has the same syntax and test behavior as `data-expect`. It marks Senior Quikscript joins that are worth testing but do not appear in Read’s manual.

## Glyph tokens

| Syntax        | Meaning                                          | Example  |
| ------------- | ------------------------------------------------ | -------- |
| `·LetterName` | Base Quikscript letter (maps to `qsLetterName`)  | `·Bay`   |
| `·-ing`       | Special case for `qsIng`                         | `·-ing`  |
| `\X`          | Literal character (via glyph names or `uniXXXX`) | `\.`     |
| `◊name`       | Special glyph by name (`◊space`, `◊ZWNJ`)        | `◊space` |

`◊ZWNJ` and `◊space` both stand for the `space` token (`LOZENGE_MAP` in `rebuild/validation/expect.py`), which matches the shaped `space` glyph or the ZWNJ's own glyph, `uni200C` (`check.BOUNDARY_GLYPH_EQUIVALENTS`).

A token's glyph is the first glyph of the cluster that covers the token's first character, so a variation selector after a letter belongs to that letter's token.

## Variant assertions

Append dot-separated modifiers to assert properties of the selected glyph. A glyph's name is its cell's label (`settle.cell_label`: rune, stance, entry and exit heights, adjustments) or the name of a raw cmap glyph or one of its copies, and the checker reads it with `check.parse_glyph_name`. What a name does not carry comes from the glyph-facts sidecar the build writes beside the Senior (`rebuild/pipeline/glyph_facts.py`).

| Modifier                                                         | Holds when the glyph                                                               | Read from | Example                   |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------- | --------- | ------------------------- |
| `.half`, `.alt`                                                  | is a stance with that trait                                                        | sidecar   | `·No.alt`                 |
| `.entry`, `.exit`                                                | has an entry, an exit                                                              | name      | `·Roe.exit`               |
| `.en-yN`, `.ex-yN`                                               | enters, exits at height N                                                          | name      | `·Roe.!ex-y0`             |
| `.extended`                                                      | carries some `en-ext-N` or `ex-ext-N` adjustment                                   | name      | `·Fee.exit.extended`      |
| `.en-ext-N`, `.ex-ext-N`, `.en-con-N`, `.ex-con-N`, `.en-trim-N` | carries that adjustment                                                            | name      | `·Ye ~b~ ·Roe.en-ext-1`   |
| `.locked`                                                        | is the ZWNJ-locked form: a cell's `locked` adjustment or a raw glyph's locked copy | name      | `·Utter.locked`           |
| `.<bitmap>`                                                      | draws that bound bitmap, a `bitmaps:` sibling of its stance                        | sidecar   | `·Utter.reaches-way-back` |
| `.∅`                                                             | is the isolated cell's glyph or the bare cmap glyph                                | sidecar   | `·May.∅`                  |

Prefix a modifier with `!` to assert that it does **not** hold: `.!half`, `.!alt`, `.!ex-y0`.

`check.MODIFIER_FORMS` lists the same vocabulary. A modifier outside it is an error, never a pass, and a pending pin does not excuse one (see [Pending pins](#pending-pins)). The `.∅` assertion uses U+2205 EMPTY SET and cannot be combined with other variant assertions.

Variant assertions are optional. Without them, any glyph of the base letter satisfies the check.

## Ligature assertions

Use `+` to assert that two letters are shaped as a single ligature glyph:

```text
·Day+Utter
```

This expects one output glyph whose rune is the ligature `qsDay_qsUtter`.

Variant assertions go at the end of the ligature token, after the last letter name. For example, `·Day+Utter.half` asserts a ·Day+·Utter ligature with the `half` trait, even though the half stance belongs to the first letter of the pair.

### Maybe-ligature assertions

Use `+?` or `+|` when the font may or may not ligate two letters:

| Syntax         | Meaning                                                     |
| -------------- | ----------------------------------------------------------- |
| `·Day+Utter`   | Must ligate into one glyph                                  |
| `·Day+?Utter`  | May ligate; if separate, connection between them unasserted |
| `·Day+\|Utter` | May ligate; if separate, assert a break between them        |

The checker tries both interpretations (ligated and separated) and passes if either matches. Variant assertions on the token apply to the ligature glyph in the ligated interpretation and are dropped in the separated one.

## Connection assertions

Connection operators between tokens say whether and how adjacent glyphs attach:

| Operator    | Meaning                                  |
| ----------- | ---------------------------------------- |
| (adjacency) | Joined, height unasserted                |
| `~x~`       | Joined at x-height (y = 5)               |
| `~b~`       | Joined at baseline (y = 0)               |
| `~t~`       | Joined at top (y = 8)                    |
| `~6~`       | Joined at y = 6                          |
| `\|`        | Break (no cursive connection)            |
| `\|?\|`     | Break, with shape isolation NOT asserted |
| `?`         | Maybe connects, or doesn’t               |

A join is read from the font's GPOS `curs` lookups, one per height: two adjacent glyphs join at a height when the left one has an exit and the right one an entry in that height's lookup. A break means they join at no height. A maybe skips the connection assertion, so the test passes whether or not the glyphs join. Use it where the source material is ambiguous, such as an accidental pen-lift in the original manuscript.

### Break-isolation invariant

When `|` separates two Quikscript letter tokens, or `?` separates a pair that does not join, the checker also asserts that **neither letter influences the other’s shape choice**. It shapes the run whole and split at each such break in separate HarfBuzz buffers, both with the `kern` feature off, and checks that the glyphs of each token beside the break have the same outlines and the same position records (offsets and advances) in both. A glyph with a different name but the same outline and position passes. A difference means a contextual lookup is reaching across the non-join, and the failure message names the whole-run and split glyphs. The `kern` feature is off because a kern pair is the space the font puts between the two letters at the break, which no split reproduces; the conformance sweep checks every break's kern. The check runs only on text shaped with the Senior font (`_check_break_isolation` in `rebuild/validation/check.py`).

So there is no need to write `.!half`, `.!alt`, and so on beside a `|` to assert that one glyph was not chosen because of the other. The checker checks this for letter-to-letter pairs only. Boundary tokens such as `◊space`, `◊ZWNJ`, and escaped punctuation are excluded, because they exist to influence the shape of their neighbors.

### When the font legitimately leaks across a non-join: `|?|`

A rule may intentionally change a letter because of its neighbor even when the two do not join. In real text such a rule fires only on literal adjacency, so a space or ZWNJ between two words stops it. In a test, `|` inserts no character, because the checker concatenates the code points, so the rule still fires. If the change alters the glyph’s outline or position, the isolation check reports it.

Use `|?|` instead of `|` for these cases. It still asserts that the two glyphs do not cursive-attach, but skips the isolation check. Use it only when the change is intended and a space or ZWNJ between the letters in real text would stop the rule from firing. A change of glyph name alone, with the same outline and position, already passes `|`, so `|?|` is needed only when the outline or position differs.

## Stylistic sets

An element's `data-stylistic-set` (`"03"`, or several separated by spaces) turns those sets on for its whole text, and a descendant's turns them on for the run it starts. A run that turns on a set the font lacks fails, so a pin cannot pass by naming a feature the font ignores.

## Pending pins

A pin the rebuild disagrees with, pending the author's ruling, keeps the author's text and carries `data-expect-pending` with a short reason:

```html
<dd data-expect="·Owe ~x~ ·Fee | ·It | ·She ~b~ ·Low" data-expect-pending="the rebuild regroups this as ·Owe | ·Fee ~x~ ·It">…</dd>
```

The suite reports a pending pin that fails as an expected failure, and fails a pending pin that passes, so the attribute cannot outlive the disagreement it records. The build's pin gate counts pending pins in its summary (`manual_pins_summary.json`) and does not fail on them. `WHATNEXT.md` points at the pins awaiting a ruling; the attribute on each is the list.

## Duplicates

Three levels of duplicate exist between two elements that both carry a `data-expect` attribute:

| Level             | Same text content | Same assertions | Same whitespace |
| ----------------- | ----------------- | --------------- | --------------- |
| Content duplicate | yes               | no              | —               |
| Total duplicate   | yes               | yes             | no              |
| Exact duplicate   | yes               | yes             | yes             |

“Same assertions” means the `data-expect` values are identical after collapsing runs of whitespace to a single space and trimming leading/trailing whitespace.

When the same word (text content) appears more than once in the test corpus, only one occurrence should carry the `data-expect` attribute, preferably the earliest. Later occurrences keep their text but lose the attribute, and a `<span>` left with no attributes is unwrapped.

Feature context is part of the test case. The same text under a different `data-stylistic-set`, inner feature span, or mixed Senior/Junior run is not automatically redundant. Remove the later assertion only when the shaping context and the expected result are the same.

## Full examples

```text
·Bay ~b~ ·Roe
```

·Bay followed by ·Roe, joined at the baseline.

```text
·No ~x~ ·Owe
```

·No followed by ·Owe, joined at the x-height.

```text
·Tea ~b~ ·See
```

·Tea followed by ·See, joined at the baseline.

```text
·Day+Utter | ·Low
```

A ·Day+·Utter ligature, then a break, then ·Low.

```text
·Low ~x~ ·Day+?Utter ~x~ ·Roe
```

Ligated reading (3 glyphs): ·Low joined at the x-height to the ·Day+·Utter ligature, which joins ·Roe at the x-height. Separated reading (4 glyphs): ·Low joined at the x-height to ·Day, the ·Day·Utter connection unasserted, and ·Utter joined at the x-height to ·Roe.
