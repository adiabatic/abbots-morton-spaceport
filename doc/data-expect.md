# `data-expect` attribute format

The `data-expect` attribute on `<td>`, `<span>`, and `<dd>` elements in the test corpus HTML files (`site/index.html`, `site/the-manual.html`, and `site/extra-senior-words.html`) describes the expected HarfBuzz shaping output. The root `conftest.py` collects each attribute as a pytest item, and `test/test_shaping.py` parses it (`parse_expect`) and checks glyph selection and cursive attachment against the compiled glyph metadata. Text is shaped with the Senior Sans font, except text inside a `force-junior` span, which is shaped with the Junior font and checked for glyph identity only.

The `data-expect-noncanonically` attribute has the same syntax and test behavior as `data-expect`. It marks Senior Quikscript joins that are worth testing but do not appear in Read’s manual.

## Glyph tokens

| Syntax        | Meaning                                          | Example  |
| ------------- | ------------------------------------------------ | -------- |
| `·LetterName` | Base Quikscript letter (maps to `qsLetterName`)  | `·Bay`   |
| `·-ing`       | Special case for `qsIng`                         | `·-ing`  |
| `\X`          | Literal character (via glyph names or `uniXXXX`) | `\.`     |
| `◊name`       | Special glyph by name (`◊space`, `◊ZWNJ`)        | `◊space` |

`◊ZWNJ` matches the `space` glyph in the shaped output (`LOZENGE_MAP` in `test/test_shaping.py`).

## Variant assertions

Append dot-separated modifiers to assert properties of the selected glyph variant. Only `.alt` and `.half` are stable semantic assertions. They name real Quikscript concepts and are checked against the compiled glyph’s traits.

| Modifier | Meaning                                 | Example     |
| -------- | --------------------------------------- | ----------- |
| `.∅`     | exact glyph, with no contextual variant | `·May.∅`    |
| `.alt`   | stable alternate stance                 | `·No.alt`   |
| `.half`  | stable half stance                      | `·Pea.half` |

Prefix a modifier with `!` to assert the selected glyph does **not** carry that trait or compatibility tag.

| Modifier | Example      |
| -------- | ------------ |
| `.!alt`  | `·No.!alt`   |
| `.!half` | `·Pea.!half` |

The `.∅` assertion uses U+2205 EMPTY SET. `·May.∅` passes only when the shaped glyph is named `qsMay`, and fails on `qsMay.noentry`, `qsMay.ex-y0`, or any other variant. It cannot be combined with other variant assertions.

Other modifiers in the corpus, such as `entry`, `exit`, `extended`, `noentry`, `en-y0`, `ex-y0`, `ex-y5`, and `reaches-way-back`, are compatibility tags. They are matched against the compatibility tags the compiler records for each glyph (`compat_assertions`), not against substrings of the glyph name.

Variant assertions are optional. Without them, any variant of the base letter satisfies the check.

## Ligature assertions

Use `+` to assert that two letters are shaped as a single ligature glyph:

```text
·Day+Utter
```

This expects one output glyph whose compiled metadata sequence records both `qsDay` and `qsUtter`.

Variant assertions go at the end of the ligature token, after the last letter name. For example, `·Day+Utter.half` asserts a ·Day+·Utter ligature with the `half` trait, even though the half stance belongs to the first letter of the pair.

### Maybe-ligature assertions

Use `+?` or `+|` when the font may or may not ligate two letters:

| Syntax         | Meaning                                                     |
| -------------- | ----------------------------------------------------------- |
| `·Day+Utter`   | Must ligate into one glyph                                  |
| `·Day+?Utter`  | May ligate; if separate, connection between them unasserted |
| `·Day+\|Utter` | May ligate; if separate, assert a break between them        |

The test runner tries both interpretations (ligated and separated) and passes if either matches. Variant assertions on the token apply to the ligature glyph in the ligated interpretation and are dropped in the separated one.

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

A join means the preceding glyph’s exit anchor and the following glyph’s entry anchor share the specified Y coordinate. A break means no exit and entry share a Y coordinate. A `|` break also fails when the left glyph is a half stance with an exit its base stance lacks and the right letter has an entry at that Y in some variant, since that suggests a forward lookup chose the half stance across the break. A maybe skips the connection assertion, so the test passes whether or not the glyphs join. Use it where the source material is ambiguous, such as an accidental pen-lift in the original manuscript.

### Break-isolation invariant

When `|` separates two Quikscript letter tokens, or `?` separates a pair that does not join, the test runner also asserts that **neither letter influences the other’s shape choice**. It shapes the two sides in separate HarfBuzz buffers and checks that the glyph at each token beside the break has the same outline and the same position record (offsets and advances) as in the full shaping. A glyph with a different name but the same outline and position passes. A difference means a `calt` lookup is reaching across the non-join, and the failure message names the in-context and split glyphs. The check runs only on text shaped with the Senior font (`_check_break_isolation` in `test/test_shaping.py`).

So there is no need to write `.!half`, `.!alt`, `.!wide`, and so on beside a `|` to assert that one glyph was not chosen because of the other. The runner checks this for letter-to-letter pairs only. Boundary tokens such as `◊space`, `◊ZWNJ`, and escaped punctuation are excluded, because they exist to influence the shape of their neighbors.

### When the font legitimately leaks across a non-join: `|?|`

A few rules intentionally change a letter because of its neighbor even when the two do not join. For example, `qsThaw.after-tall` drops ·Thaw’s baseline entry anchor when a Tall letter precedes it. In real text such a rule fires only on literal adjacency: the `after:` (or `before:`) selector compiles to a backward (or forward) context lookup whose match list contains only the named families, so a space or ZWNJ between two words occupies the slot the lookup checks and the rule does not fire. In a test, `|` inserts no character, because the runner concatenates the code points, so the lookup still fires. If the change alters the glyph’s outline or position, the isolation check reports it.

Use `|?|` instead of `|` for these cases. It still asserts that the two glyphs do not cursive-attach (no shared entry and exit Y), but skips the isolation check. Use it only when the change is intended and a space or ZWNJ between the letters in real text would stop the rule from firing. A change of glyph name alone, with the same outline and position, already passes `|`, so `|?|` is needed only when the outline or position differs.

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
