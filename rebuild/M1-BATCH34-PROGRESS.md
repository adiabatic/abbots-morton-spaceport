# M1 batch 34 — qsExcite

Scratch for the ·Excite migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsExcite.yaml` waits for the author's review, and its `# DRAFT` marker marks what to review. The old record has no ductus or notes for ·Excite, so the motion, including the order of its two strokes, is drafted from the bitmap.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row holds ·Excite. Most repeat a change the same window shows, in the same configuration, with ·J’ai, ·Jay, ·-ing or ·Ooze in ·Excite's place, or a change at a junction ·Excite does not touch. The changes that touch ·Excite are of these kinds:
  - after the namer dot, ·Excite joins its follower at the baseline as at a word start, where the old font breaks (the namer-dot ruling below);
  - `·Utter ~x~ ·Gay | ·Excite`, `·{I,Ah} ~x~ ·They | ·Excite` and, under ss03, `·{I,Fee} ~x~ ·Tea | ·Excite`, where the old font joins (the leak ruling below);
  - `·X ~b~ ·Excite | ·Tea ~b~ ·Y`, where the old font keeps ·Excite's right foot splayed toward the break and the rebuild draws only the left splay;
  - `·Excite ~b~ ·Tea | ·It ~x~ ·Roe`, `·Excite ~b~ ·Tea | ·It ~b~ ·Day` and `·Pea | ·Excite ~b~ ·No ~b~ ·{It,Day}`, where the old font breaks one of those joins; the rebuild draws them as the old font draws ·J’ai in ·Excite's place;
  - under ss05, `·Excite ~b~ ·Tea ~b~ ·Ah`, where the old font breaks before ·Ah.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next, which would make readings taken now stale; `rebuild/scaling-series.txt` keeps its ·-ing readings. `make job-costs` checks this batch's runs against the booked constants.
- Joins with partners that are not migrated, which the partner's migration verifies through its own pair evidence: ·Eat on ·Excite's entry `from:`, and ·Exam and ·Eat on its exit `toward:`.

## Recorded design overrides

The old record draws four bitmaps of one motion: the bare letter, the left foot splayed out to a baseline entry, the right foot splayed out to a baseline exit, and both feet splayed. The rune is one `sole` stance on the bare drawing, with the left splay as the baseline entry's `joined:` drawing and the right and double splays as `cells:` rows, on the qsShe and qsOy precedent. The old `wide_on_right_at_short_height` shape, an x-height exit after ·Pea, ·Tea or ·Ye, appears in no baseline table and is dropped.

The old font never joins ·Excite to its follower after a space, a ZWNJ or the namer dot, though it does at a text edge. The author ruled that a bug in the old font: the rune has no namer-dot record, so ·Excite after the namer dot joins as at a word start, and the space and ZWNJ rows fall to `split-by-space-or-zwnj`.

The old font joins ·Excite at the baseline after three entered letters whose bare pairs break: `·Utter ~x~ ·Gay ~b~ ·Excite`, `·{I,Ah} ~x~ ·They ~b~ ·Excite` (·They's tuck for ·May), and under ss02, ss03 and ss05 `·I ~x~ ·Tea ~b~ ·Excite`. The author ruled that all three break: qsGay and qsThey stay off ·Excite's entry `from:` and keep ·Excite off their `toward:` lists, and qsTea's existing refuse covers ·Tea.

·Excite's entry `from:` excludes the ·They+Zoo ligature, which the old font never joins to ·Excite, on the qsIng precedent. Its exit carries qsJai's yielding ·Tea·Day prefer verbatim.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E66B E651:E66B E653:E66B E659:E66B E65C:E66B E665:E66B E666:E66B E670:E66B E67A:E66B E652:E679:E66B \
  E66B:E652 E66B:E653 E66B:E65B E66B:E665 E66B:E666 E66B:E670 E66B:E674 E66B:E67A E66B:E67B:E652 E66B:E653:E67A \
  E66B:E66B E66B:E66B:E66B E651:E66B:E652 E650:E66B:E652 E657:E65B:E66B \
  E67A:E655:E66B E675:E657:E66B E675:E652:E66B \
  E66B:E652:E653 E66B:E652:E650 E66B:E652:E670 E66B:E67A:E652:E653 E651:E66B:E652:E653 E653:E652:E66B \
  E666:E672:E66B:E652 E672:E66B:E650:E672 E670:E66B:E652:E670 \
  200C:E66B E651:200C:E66B E66B:200C:E653 00B7:E66B 00B7:E66B:E652
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

After a green `run_m1`, check placed ink for every row that `identical-unjoined-from-left` takes. The accepted result prints only the ·Roe baseline-bar rows: ·{Way,He,Why}·Cheer·It·Roe and ·{Ye,He}·-ing·It·Roe. Any other row it prints is one the class cannot take: stop and report it.

```zsh
PYTHONPATH=. uv run python - <<'EOF'
import csv, sys
from pathlib import Path
from rebuild.pipeline.oracle import materialized_audit
from rebuild.review.ink import InkComparator
csv.field_size_limit(sys.maxsize)
ink = InkComparator("site/AbbotsMortonSpaceportSansSenior-Regular.otf", "rebuild/out/m1/M1.otf")
def lines():
    rest = b""
    for chunk in materialized_audit(Path("rebuild/out/m1")):
        *done, rest = (rest + chunk).split(b"\n")
        yield from (line.decode() for line in done)
for r in csv.DictReader(lines(), delimiter="\t"):
    if r["matched_entry"] == "identical-unjoined-from-left" and not ink.ink_identical("".join(chr(int(c, 16)) for c in r["codepoints"].split(":")), (r["config"],)):
        print(r["config"], r["codepoints"])
EOF
```

The ·Excite block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, Manual-pin, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
