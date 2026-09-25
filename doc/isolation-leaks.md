# Generating the isolation-leaks list

The isolation-leaks section of `site/check.html` lists shaping leaks. A leak is a non-joining adjacent pair (across a pen-lift) whose chosen glyphs differ between shaping the pair together and shaping each half in its own HarfBuzz buffer. The halves are boundary-faithful: any `space` or ZWNJ token between the letters is included in both halves. The sweep enumerates letters plus those two boundary tokens. `doc/definitions/shaping-leakage.md` has the full definition. `tools/leak_classify.py` labels each visible leak **bad** (a visible additive dangle reaching toward an absent neighbor) or **benign** (subtractive trims, swaps to a standalone variant, cosmetic tucks: variation that makes the text look hand-drawn and is wanted).

`tools/build_check_html.py` generates all of `site/check.html`. The isolation-leaks list is one of its four generated sections. The others are failing tests, corpus render diffs, and a depth-4 triage list that renders the leaks recorded in `site/bad-leak-backlog.txt`.

## Gates: bad is a hard gate, benign is a census

Only a new **bad** leak is a defect. The gates run at two depths:

- **Depth 3 (`make test`, fast).** `test/test_isolation_leaks.py::test_no_new_bad_isolation_leaks` asserts that every bad leak found at `--max-len 3` is in the approved backlog. This is the everyday gate.
- **Depth 4 (`make test-leaks`, ≈1 min).** These tests are marked `slow`, which the default run excludes. `test_bad_leak_backlog_unchanged` is the same backlog gate at depth 4. `test_benign_census_unchanged` compares the benign set with `site/benign-leak-census.txt`.

The bad gate (`site/bad-leak-backlog.txt`) is **asymmetric**. A new bad signature fails, because a change added a dangle. A _resolved_ one only prints a re-bless notice, because the automated fix loop is expected to empty the backlog and should not fail the gate when it succeeds. The benign census is **symmetric**. Any change, gained or lost, fails the test so that it gets reviewed, although it is not a defect on its own. In either case, re-bless both files with `make leak-snapshot` and review the diff. The snapshot comparison takes the place of hand-written tests over letter tuples. `doc/history/2026-06-03--leak-cleanup/leak-investigation-findings.md` explains why no fixed depth can be proved complete (contextual `calt` rules chain across ≈600 lookups) and why a static FEA checker cannot reliably replace the sweep.

## Refresh the list

```sh
make check-html-after
```

That target runs `make all` and then `tools/build_check_html.py`, which rebuilds `site/check.html` from scratch.

To regenerate only the HTML (without rebuilding the fonts) and to control the sweep depth, run the tool directly:

```sh
uv run python tools/build_check_html.py --max-len 3
```

`--max-len 3` is the default (about half a second). Pairs alone miss leaks that need context, such as `·Zoo ·It ·Utter`, where a third letter exposes a leak at an earlier break: the trailing ·Utter changes which variant the middle ·It takes when the right half is shaped on its own, so the `·Zoo | ·It` break differs only once ·Utter is in the sweep.

Raise `--max-len` if a leak appears only with more context. Each step multiplies the cost by roughly the sweep alphabet’s size (the 44 letters plus the two boundary tokens). `--max-len 4`, the slow gate’s depth, takes around a minute, and `--max-len 5` is impractical.

`--out` writes to a different path, to inspect a draft without overwriting `site/check.html`.

## Inspect the result

Open `site/check.html` and scroll to the **Auto-generated: isolation leaks** section. Each row shows three columns:

| Column                   | What it shows                                                                                                           |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Sequence                 | English-style label with a `\|` marking the leaky break, plus the glyph diff `qsX → qsX.variant`                        |
| In context               | The full sequence shaped as one buffer (what real text gets)                                                            |
| Halves shaped separately | The same letters split at the break into two `display: inline-block` halves, so HarfBuzz shapes each side independently |

Only visible leaks are shown. A signature counts as visible if _any_ swept example renders the two columns differently (`find_visible_leaks`). Leaks that change only the glyph name, with no visible effect, never appear; `qsThaw.after-tall`, which drops only an entry anchor, is a typical source. The badge on each row is the classifier’s label. Look at the **bad** rows first, because those are the defects.

Every shown row has a `bad` or `benign` badge in the Sequence column and a matching `data-visual` attribute. Visibility is decided by shaping the example in context, then shaping the two boundary-faithful halves separately and joining them at the left half’s total advance, as the inline-block layout does. The right half’s leading boundary glyph is dropped so the token is rendered once. Kerning is turned off for both. The two views differ if and only if some glyph has different pixels (`bitmap`, `y_offset`, `advance_width`) **or** a different absolute origin (pen position plus offset, in x and y). Comparing origins catches cursive-positioning leaks where the chosen variant has the same bitmap but a different exit or entry anchor. For example, `qsIt` and `qsIt.ex-y5` have identical pixels, but the latter’s exit anchor pulls the next glyph left through GPOS `curs`.

## Re-running

Running the tool again with the same `--max-len` over the same fonts and test results reproduces `site/check.html` byte for byte. After fixing the YAML, FEA, or IR rule behind a leak:

```sh
make check-html-after
```

The fixed leak should disappear from the section. Other rows can change too, because each signature is shown with the first swept example that renders it visibly, and a fix can change which example that is. Diff against the previous file for an exact before and after.

## Don’t hand-edit `site/check.html`

`tools/build_check_html.py` regenerates the whole file on every run, so hand edits are overwritten.
