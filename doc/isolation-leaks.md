# Generating the isolation-leaks list

The isolation-leaks section of `site/check.html` surfaces shaping leaks. A leak is a non-joining adjacent pair (across a pen-lift) whose chosen glyphs differ when the pair is shaped together vs. when each half is shaped in its own HarfBuzz buffer, boundary-faithfully (any `space`/ZWNJ token between them sits in both halves). The sweep enumerates letters plus the two boundary tokens; see `doc/definitions/shaping-leakage.md` for the full definition. Each visible leak is then labelled **bad** (a visible additive dangle reaching toward an absent neighbor) or **benign** (subtractive trims, standalone-variant swaps, cosmetic tucks — welcome faux-organic variation) by `tools/leak_classify.py`.

The whole `site/check.html` page is generated end-to-end by `tools/build_check_html.py`; the isolation-leaks list is one of the three auto-sections it emits (alongside corpus render diffs and failing tests).

## Gates: bad is a hard gate, benign is a census

CI fails only on **bad** leaks. Two depths:

- **Depth 3 (`make test`, fast).** `test/test_isolation_leaks.py::test_no_new_bad_isolation_leaks` asserts no live bad leak at `--max-len 3` falls outside the approved backlog. This is the everyday green/red gate.
- **Depth 4 (`make test-leaks`, ≈1 min, `slow`-marked, excluded from the default run).** `test_bad_leak_backlog_unchanged` is the same backlog gate at depth 4; `test_benign_census_unchanged` diffs the benign set against `site/benign-leak-census.txt`.

The bad gate (`site/bad-leak-backlog.txt`) is **asymmetric**: a NEW bad signature fails (a change grew a dangle); a _resolved_ one only prints a re-bless notice, because the autonomous fix loop is expected to drain the backlog and must not trip the gate by succeeding. The benign census is **symmetric**: any change is surfaced (informational, not a defect on its own) so you re-bless and notice the organic-variation set shifting. Either way, re-bless both files with `make leak-snapshot` and review the diff. This replaces hand-written `44^n` tuple tests: you never enumerate tuples, you diff a snapshot. See `doc/history/2026-06-03--leak-cleanup/leak-investigation-findings.md` for why no fixed depth is provably complete (contextual `calt` rules chain across ≈600 lookups) and why a static FEA checker cannot soundly replace the sweep.

## Refresh the list

```sh
make check-html
```

That target runs `make all` and then `tools/build_check_html.py`, which rebuilds `site/check.html` from scratch.

To regenerate only the HTML (without rebuilding the fonts) and to control the sweep depth, run the tool directly:

```sh
uv run python tools/build_check_html.py --max-len 3
```

`--max-len 3` is the sweet spot (about half a second; ≈200 leaks at the time of writing). Pairs alone miss context-revealed leaks like `·Zoo ·It ·Utter`, where a third letter is needed to expose a leak at an earlier break: the trailing ·Utter changes which variant the middle ·It takes when the right half is shaped on its own, so the `·Zoo | ·It` break only diverges once ·Utter is in the sweep.

Bump `--max-len` if a leak you're hunting only manifests with more context. Cost grows roughly 44× per step — `--max-len 4` is around a minute (the gate depth), `--max-len 5` is impractical.

`--out` writes to a different path if you want to inspect a draft without clobbering `site/check.html`.

## Inspect the result

Open `site/check.html` and scroll to the **Auto-generated: isolation leaks** section. Each row shows three columns:

| Column                   | What it shows                                                                                                           |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Sequence                 | English-style label with a `\|` marking the leaky break, plus the glyph diff `qsX → qsX.variant`                        |
| In context               | The full sequence shaped as one buffer (what real text gets)                                                            |
| Halves shaped separately | The same letters split at the break into two `display: inline-block` halves, so HarfBuzz shapes each side independently |

Only visible leaks are shown (a signature counts as visible if _any_ swept example renders the two columns differently — see `find_visible_leaks`); purely glyph-name-signature leaks with no visible effect (typical for `qsThaw.after-tall`-style trim rules) never appear. For each shown row, the badge is the mechanical verdict: reach for the **bad** rows first — those are the defects.

Every shown row carries a `bad` or `benign` badge in the Sequence column (and a matching `data-visual` attribute). Visibility is decided by shaping the example in context, then shaping the two boundary-faithful halves independently and concatenating them at the left half's cumulative advance (dropping the right half's leading shared boundary glyph so the token renders once) — exactly what the inline-block layout does. The two views differ iff some glyph has different pixels (`bitmap`, `y_offset`, `advance_width`) **or** a different absolute origin (`pen_x + pos.x_offset`). Comparing origins catches cursive-positioning leaks where the chosen variant has the same bitmap but a different exit/entry anchor — e.g. `qsIt` vs `qsIt.ex-y5` are pixel-identical but the latter's exit anchor pulls the next glyph leftward via GPOS `curs`.

## Re-running

The tool is idempotent: re-running with the same `--max-len` reproduces `site/check.html` byte-for-byte. After fixing an offending YAML / FEA / IR rule:

```sh
make check-html
```

The fixed leak should drop out of the section. Other leaks in the same row neighborhood usually shift around when the dedup key changes, so expect surrounding rows to renumber — diff against the previous run if you want a precise before/after.

## Don't hand-edit `site/check.html`

The whole file is regenerated each run. Anything you add by hand will be overwritten the next time `tools/build_check_html.py` runs.
