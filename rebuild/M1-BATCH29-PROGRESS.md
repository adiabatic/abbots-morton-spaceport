# M1 batch 29 — qsHe

Scratch for the ·He migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsHe.yaml` waits for the author's review, and its `# DRAFT` markers mark what to review. The old record has no ductus or notes for ·He, so both motions are drafted from the bitmaps.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Most new unmatched rows repeat a question the corpus already asks: ·He stands unjoined beside an existing unmatched window, or the rest of the window draws as it does with ·Ye, ·Tea, ·Pea, or ·Way in ·He's place. The junction changes at ·He, each accepted below:
  - `·He ~b~ ·It ~x~ ·Jay` and the same before ·Ye, ·Eight, ·Awe, and ·Ox, where the old font draws `·He.half ~x~ ·It | ·Jay`;
  - `·He.half ~x~ ·It ~b~ ·Day` and the same before ·Low and ·Utter under ss04, where the old font draws `·He ~b~ ·It ~b~ ·Day`;
  - `·He.half ~x~ ·It ~b~ ·Zoo.half`, where the old font leaves ·He's baseline exit dangling and breaks (`·He | ·It ~b~ ·Zoo.half`), and the same before ·It·I and, outside ss04, before ·It·Day and ·It·Utter;
  - `·Bay | ·He.half ~x~ ·It ~b~ ·Roe` and its siblings, where the old font draws `·Bay ~b~ ·He | ·It ~x~ ·Roe`;
  - `·He.half ~x~ ·It | ·Thaw`, where the old font joins ·It·Thaw at the baseline.
- `·They+Zoo | ·He` breaks, as in the old font: the baseline entry's `from:` names qsZoo, which would also admit the ligature that ends in it, so the scope excludes qsThey_qsZoo by name.
- Joins with partners that are not migrated, to re-verify at each partner's migration, where the partner's row must admit ·He:
  - `·-ing ~b~ ·He` and `·Llan ~b~ ·He`, which the full stance's baseline entry `from:` names.
  - `·He ~b~ ·Exam` (with the exit extension) and `·He ~b~ ·Eat`, which the full stance's baseline exit `toward:` names.
  - `·He.half ~x~ ·Llan` and `·He.half ~x~ ·Foot`, which the half's x-height exit `toward:` names.
  - The old font breaks `·He | ·-ing` because ·-ing drops its entry after ·He (its `noentry_after`); qsIng's migration needs that refusal on qsIng, the ·Day·He·-ing example of `doc/rebuild-design.md` §5.9.
- GitHub updates on the public repo, which wait for the author: #207 (no ·It·Et record is authored for qsHe, and the qsPea and qsTea half-stance prefers stay because the build still cites them), #212 (qsHe is discharged: `·He ~b~ ·Ah` joins in every settlement configuration), #523 (the ·He item is reconciled below), and the `waits on ·He` label on #207, #212, #523, and #524.

## Recorded design overrides

·He's half is a stance, `half` with `traits: [half]`, rather than a binding, on the ·Way precedent: the Manual pins ·He brings into scope name `·He.half` and `·He.!half`, and `manual_pins._stance_traits` reads `.half` from a stance's traits. The half requires its exit, so `order: [full, half]` keeps the full stance as the cmap glyph and the isolated cell.

The old `exit_baseline` and `entry_baseline` stances draw the same bitmap, so they are cells of the full stance with a `never` pairing (`doc/rebuild-design.md` §5.9). The old `entry_baseline` stance's `not_after` complement list becomes the baseline entry's `from:` allowlist, which leaves ·Way out (#523's ·He item): `·Way | ·He` breaks in both fonts.

·He prefers its follower: the old font joins the letter after ·He whenever it can and the letter before only otherwise. §5.9's single record, `{cell: {exit: baseline}, over: {entry: baseline}}`, misses the half's x-height exit, and two unconditioned records, one per exit, fail the build with `E-AMBIGUOUS` before ·It, where both exits are on offer. The rune writes it as one yielding preference of no entry over a baseline entry, `{cell: {entry: none}, over: {entry: baseline}}`, plus a yielding preference of the x-height exit over the baseline exit before ·It, which keeps the old pair `·He.half ~x~ ·It` and yields where join count favors `·He ~b~ ·It ~x~ ·Jai` and `·He ~b~ ·It ~x~ ·Cheer`, as the old font draws them.

The author accepts the junction changes the Parked list names (Q1–Q3). `·He ~b~ ·It ~x~ ·Jay` and its siblings join as `·Bay ~b~ ·It ~x~ ·Jay` does, where restoring the old break would need an absolute preference conditioned on the letter after ·It. Under ss04 the half keeps `·He.half ~x~ ·It ~b~ ·Day`, the default drawing, rather than an ss04-gated preference for the baseline exit. `·He.half ~x~ ·It ~b~ ·Zoo.half` is what the old font draws after half-·Tea, and `·Bay | ·He.half ~x~ ·It ~b~ ·Roe` is ·He's follower preference. ·He is Tall, so qsIt's refusal of its baseline exit into ·Thaw after a Tall letter also applies after ·He, as after half-·Tea.

The old half's exit tuck before ·Zoo (`qsHe.half.ex-y5.ex-con-1` beside `qsZoo.en-trim-1`) is ·Zoo's own entry contraction: qsZoo's `contract[0]` names qsHe beside qsTea, and `zoo-entry-contraction-renamed` in `rebuild/m1-divergences.yaml` takes the half-·He pair as it takes the half-·Tea pair. The placed ink is identical to the old font's in every settlement configuration.

The plain `qsCheer.noentry` alias covers `·He | ·Cheer`, the old `noentry_after` variant after ·He. The placed-ink check over `zwnj-word-initial-unification` prints the ·He·Cheer·It·Roe rows beside the ·Way·Cheer·It·Roe rows, the same ·Roe baseline-bar redraw, which `roe-baseline-bar-kept-after-it` covers for any left letter; the class's `why:` names both.

Issue #524's two classifier gaps close in their own commit ahead of `Add ·He`: `zwnj-word-initial-junction-moved` takes a row only when every `.noentry` glyph follows a ZWNJ or the namer dot, and `_noentry_name_tokens` attributes `exit-added` per glyph. Run with and without it at this alphabet, the oracle gives every row the same class.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E662 E662:E653 E662:E659 E662:E665 E662:E667 E662:E674 E662:E675 E662:E676 E662:E67A E662:E67B E662:E67E E662:E653:E67A E662:E659:E67A E662:E67B:E652 \
  E662:E65B E662:E65D E662:E65F E662:E666 E662:E668 E662:E670 E662:E672 E662:E673 E662:E677 E662:E678 E662:E65D:E67A E662:E65F:E67A \
  E651:E662 E653:E662 E654:E662 E65B:E662 E65E:E662 E672:E662 E673:E662 E677:E662 E678:E662 E679:E662 E67E:E662 E652:E679:E662 \
  E662:E650 E662:E652 E662:E655 E662:E65E E662:E660 E662:E662 E661:E662 E67A:E662 \
  E651:E662:E653 E653:E662:E653 E651:E662:E65D E651:E662:E655 E651:E662:E670:E65F E651:E662:E67A:E656 E651:E662:E674:E665 E651:E662:E670:E668 \
  E662:E670:E65D E662:E670:E65E E662:E670:E65F E662:E670:E653 E662:E670:E667 E662:E670:E65B E662:E670:E656 \
  E652:E653:E662 E67A:E652:E653:E662 E662:E674:E653 E662:E67A:E668 E662:E672:E653 E662:E673:E65B E662:E677:E667 E662:E67E:E65B E662:E659:E666:E652 \
  200C:E662 200C:E662:E653 200C:E662:E65D E662:200C:E653 E651:200C:E662 00B7:E662
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-series.txt
uv run python -m rebuild.pipeline.coretext_smoke --font rebuild/out/m1/M1.otf
make verdict-ready
```

After a green `run_m1`, check placed ink for every row that `zwnj-word-initial-unification` takes. The accepted result prints only the ·Way·Cheer·It·Roe and ·He·Cheer·It·Roe rows. Any other row it prints is one the class cannot take: stop and report it.

```zsh
PYTHONPATH=. uv run python - <<'EOF'
import csv
from rebuild.review.ink import InkComparator
ink = InkComparator("site/AbbotsMortonSpaceportSansSenior-Regular.otf", "rebuild/out/m1/M1.otf")
rows = [r for r in csv.DictReader(open("rebuild/out/m1/divergence-audit.tsv"), delimiter="\t") if r["matched_entry"] == "zwnj-word-initial-unification"]
for r in rows:
    if not ink.ink_identical("".join(chr(int(c, 16)) for c in r["codepoints"].split(":")), (r["config"],)):
        print(r["config"], r["codepoints"])
EOF
```

The ·He block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
