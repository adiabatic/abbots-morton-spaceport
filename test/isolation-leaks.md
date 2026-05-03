# Generating the isolation-leaks list

Quick reference for the `tools/find_isolation_leaks.py` utility, which surfaces the sequences that currently need `|?|` (instead of `|`) in `data-expect`. Each leak is a non-joining adjacent pair whose chosen glyphs differ when the pair is shaped together vs. when each half is shaped in its own HarfBuzz buffer — the same invariant that `_check_break_isolation` enforces in `test/test_shaping.py`.

## Refresh the list

```sh
make all
uv run python tools/find_isolation_leaks.py --max-len 3 --write
```

`--max-len 3` is the sweet spot (about half a second; 192 leaks at the time of writing). Pairs alone miss context-revealed leaks like `·Ah ·Yay ·Exam`, where a third letter is needed to kick the middle into a variant whose shape the right letter then keys off of.

Bump `--max-len` if a leak you're hunting only manifests with more context. Cost grows roughly 44× per step — `--max-len 4` is around 30 s, `--max-len 5` is impractical.

Skip `--write` to print the rows to stdout without touching `test/check.html`.

## Inspect the result

Open `test/check.html` and scroll to the auto-generated section. Each row shows three columns:

| Column                   | What it shows                                                                                                           |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Sequence                 | English-style label with a `\|` marking the leaky break, plus the glyph diff `qsX → qsX.variant`                        |
| In context               | The full sequence shaped as one buffer (what real text gets)                                                            |
| Halves shaped separately | The same letters split at the break into two `display: inline-block` halves, so HarfBuzz shapes each side independently |

If columns 2 and 3 look identical, the leak is purely a glyph-name signature change with no visible effect (typical for `qsThaw.after-tall`-style trim rules). If the columns differ visually, decide whether the in-context shape is the one you want; if not, that's a bug to fix.

## Re-running

The tool is idempotent: re-running with the same `--max-len` reproduces the file byte-for-byte. After fixing an offending YAML / FEA / IR rule:

```sh
make all
uv run python tools/find_isolation_leaks.py --max-len 3 --write
```

The fixed leak should drop out of the section. Other leaks in the same row neighborhood usually shift around when the dedup key changes, so expect surrounding rows to renumber — diff against the previous run if you want a precise before/after.

## How the markers work

The auto-section sits between two HTML comments inside `test/check.html`:

```html
<!-- BEGIN AUTO: isolation-leaks -->
<!-- ... -->
<!-- END AUTO: isolation-leaks -->
```

The script preserves the existing leading whitespace, the supporting `.isolation-leaks` CSS, and everything outside the markers. If the markers are missing (the user nuked the section), the script re-inserts it just above the `<p class="footer">` anchor. Don't hand-edit the rows — they'll be overwritten on the next run.
