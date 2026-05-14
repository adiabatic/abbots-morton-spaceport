# Generating the isolation-leaks list

The isolation-leaks section of `test/check.html` surfaces the sequences that currently need `|?|` (instead of `|`) in `data-expect`. Each leak is a non-joining adjacent pair whose chosen glyphs differ when the pair is shaped together vs. when each half is shaped in its own HarfBuzz buffer — the same invariant that `_check_break_isolation` enforces in `test/test_shaping.py`.

The whole `test/check.html` page is generated end-to-end by `tools/build_check_html.py`; the isolation-leaks list is one of the two auto-sections it emits.

## Refresh the list

```sh
make check-html
```

That target runs `make all` and then `tools/build_check_html.py`, which rebuilds `test/check.html` from scratch.

To regenerate only the HTML (without rebuilding the fonts) and to control the sweep depth, run the tool directly:

```sh
uv run python tools/build_check_html.py --max-len 3
```

`--max-len 3` is the sweet spot (about half a second; ~200 leaks at the time of writing). Pairs alone miss context-revealed leaks like `·Ah ·Yay ·Exam`, where a third letter is needed to kick the middle into a variant whose shape the right letter then keys off of.

Bump `--max-len` if a leak you're hunting only manifests with more context. Cost grows roughly 44× per step — `--max-len 4` is around 30 s, `--max-len 5` is impractical.

`--out` writes to a different path if you want to inspect a draft without clobbering `test/check.html`.

## Inspect the result

Open `test/check.html` and scroll to the **Auto-generated: isolation leaks** section. Each row shows three columns:

| Column                   | What it shows                                                                                                           |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Sequence                 | English-style label with a `\|` marking the leaky break, plus the glyph diff `qsX → qsX.variant`                        |
| In context               | The full sequence shaped as one buffer (what real text gets)                                                            |
| Halves shaped separately | The same letters split at the break into two `display: inline-block` halves, so HarfBuzz shapes each side independently |

If columns 2 and 3 look identical, the leak is purely a glyph-name signature change with no visible effect (typical for `qsThaw.after-tall`-style trim rules). If the columns differ visually, decide whether the in-context shape is the one you want; if not, that's a bug to fix.

Each row carries a `same` or `diff` badge in the Sequence column (and a matching `data-visual="same"|"diff"` attribute on the row, for filtering via DevTools). The classifier shapes the example sequence in context, then shapes the two halves independently and concatenates them at the left half's cumulative advance — exactly what the inline-block layout does. The two views are `same` iff every glyph has the same pixels (`bitmap`, `y_offset`, `advance_width`) **and** the same absolute origin (`pen_x + pos.x_offset`). Comparing origins catches cursive-positioning leaks where the chosen variant has the same bitmap but a different exit/entry anchor — e.g. `qsIt` vs `qsIt.exit-xheight` are pixel-identical but the latter's exit anchor pulls the next glyph leftward via GPOS `curs`. Reach for the `diff` rows first when hunting real bugs.

## Re-running

The tool is idempotent: re-running with the same `--max-len` reproduces `test/check.html` byte-for-byte. After fixing an offending YAML / FEA / IR rule:

```sh
make check-html
```

The fixed leak should drop out of the section. Other leaks in the same row neighborhood usually shift around when the dedup key changes, so expect surrounding rows to renumber — diff against the previous run if you want a precise before/after.

## Don't hand-edit `test/check.html`

The whole file is regenerated each run. Anything you add by hand will be overwritten the next time `tools/build_check_html.py` runs.
