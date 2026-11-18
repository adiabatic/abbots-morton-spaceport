# M1 batch 35 — qsEat, qsDay_qsEat and qsSee_qsEat

Scratch for the ·Eat migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsEat.yaml`, `glyph_data/runes/qsDay_qsEat.yaml` and `glyph_data/runes/qsSee_qsEat.yaml` waits for the author's review, and their `# DRAFT` markers mark what to review. The old record has no ductus or notes for ·Eat or either ligature, so each motion is drafted from the bitmap.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row holds ·Eat. Nearly all repeat a change the same window shows, in the same configuration, with ·Ooze in ·Eat's place (·Day+Utter in ·Day+Eat's, ·See in ·See+Eat's), or a change at a junction ·Eat does not touch. The changes only ·Eat brings are the author's rulings below:
  - `·{I,Ah} ~x~ ·They | ·Eat`, where the old font joins;
  - after the namer dot, `·Excite ~b~ ·{Eat,Day+Eat}`, the ·Excite batch's namer-dot ruling;
  - `·{Day,See}+Eat | ·It ~b~ ·Zoo`, where the old font draws `·{Day,See}+Eat ~b~ ·It ~x~ ·Zoo`;
  - `·See+Eat ~b~ ·{Out,Low,At}` drawn as after ·Eat, where the old font draws ·Out's after-·See form and ·Low's and ·At's after-·See entry extension;
  - `·Day+Eat ~b~ ·I` with ·Eat's exit extension, which the old font gives ·Eat and ·See+Eat but not ·Day+Eat.
- Issue #522's ·-ing half is not applied: qsWay's ·Gay preference names ·Eat but not ·-ing, so `·Way.half ~b~ ·Gay | ·-ing` still diverges.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next, which would make readings taken now stale; `rebuild/scaling-series.txt` keeps its ·-ing readings. `make job-costs` checks this batch's runs against the booked constants.

## Recorded design overrides

The old record is one bitmap with a baseline entry, a baseline exit and an `extend_exit_before` derive toward ·I, so the rune is one `sole` stance whose entry `from:` and exit `toward:` are scoped from the baseline, with the derive as an exit `extend` before ·I. ·Eat never exits into ·Tea, so it needs no ·Tea·Day yield. Its entry `from:` excludes the ·They+Zoo ligature, which the old font never joins to ·Eat, on the qsExcite precedent.

·Day+Eat enters exactly as ·Day+Utter does, pair for pair, so every right-facing selector that names `qsDay_qsUtter` as a ·Day-led entry names `qsDay_qsEat` beside it. ·See+Eat has no entry, as ·See+Utter has none. Both ligatures take ·Eat's exit records through `outgoing: {stance: sole}`.

Under ss04 the old font joins `·Eat ~b~ ·It ~b~ ·{Day,Utter}` and otherwise breaks `·Eat | ·It`, so ·Eat's `toward:` names qsIt and qsIt's refusal of a baseline entry without an exit after ·Jay names ·Eat too, on the qsJay precedent. qsIt's refusal of a baseline exit into ·Utter from an unentered ·It no longer names ·Eat: it transcribed the old `before_utter` stance's `not_after`, but the old font's plain baseline-exit ·It still draws `·{Eat,Day+Eat} | ·It ~b~ ·Utter`.

qsWay's ·Gay preference names ·Eat, as issue #522 asked, so the rebuild keeps the old `·Way.half ~b~ ·Gay | ·Eat`. The `half-day-closer-at-baseline-join` ledger class takes half-·Day+Eat beside half-·Day and half-·Day+Utter, after the author's rulings that dropped the pixel after ·{Tea,Ye} for those two.

The author ruled on five old-font behaviors:

- The old font joins `·{I,Ah} ~x~ ·They ~b~ ·Eat` through ·They's tuck for ·May, where the bare pair breaks. As for ·Excite, it breaks: qsThey stays off ·Eat's entry `from:` and keeps ·Eat off its baseline `toward:`. ·Day+Eat follows ·Day and ·Day+Utter in that `toward:` and keeps the join.
- The old font draws ·Out's after-·See form after ·See+Eat, and gives ·Low and ·At their after-·See entry extension there, because its `after: qsSee` selectors also matched ·See-led ligatures. ·See+Eat ends in the ·Eat stroke, so `·See+Eat ~b~ ·{Out,Low,At}` draws them as after ·Eat. The author expects to drop the extra left column of ·Out's `raked` drawing.
- The old font draws `·{Day,See}+Eat ~b~ ·It ~x~ ·Zoo`, because its ·It guard against an x-height exit after ·Eat missed the ligatures. They follow ·Eat there: `·{Day,See}+Eat | ·It ~b~ ·Zoo`.
- The old font extends ·Eat's and ·See+Eat's exit before ·I but not ·Day+Eat's, whose old `derive` block replaced the inherited one. Both ligatures keep ·Eat's extension.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E671 E650:E671 E651:E671 E652:E671 E655:E671 E666:E671 E66B:E671 E670:E671 E67A:E671 E652:E679:E671 E657:E65B:E671 \
  E671:E653 E671:E656 E671:E65A E671:E65B E671:E665 E671:E666 E671:E668 E671:E66B E671:E674 E671:E675 E671:E67A E671:E67B \
  E671:E671 E671:E653:E671 E671:E653:E67A E671:E67B:E652 E653:E67A:E671 \
  E653:E671 E651:E653:E671 E652:E653:E671 E661:E653:E671 E653:E671:E675 E653:E671:E670:E65B \
  E65A:E671 E65A:E671:E675 E65A:E671:E67B \
  E675:E657:E671 E675:E657:E653:E671 E671:E670:E653 E671:E670:E67A \
  E671:E67A:E652:E653 E671:E674:E665 \
  200C:E671 E651:200C:E671 E671:200C:E653 200C:E653:E671 00B7:E671 00B7:E66B:E671
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

The ·Eat block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
