# M1 batch 31 — qsIng

Scratch for the ·-ing migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsIng.yaml` waits for the author's review, and its `# DRAFT` marker marks what to review. The old record has no ductus or notes for ·-ing, so the motion is drafted from the bitmap.
- Two new prefers carry no `why:`, which only the author writes: qsUtter's run-start `cell: {exit: x-height}` before ·May·Thaw, and qsEt's yield before ·Pea·It·-ing. Both exist to satisfy Manual pins (see the overrides).
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Nearly every new unmatched row repeats a question the corpus already asks: the same window with ·Ooze in ·-ing's place is an accepted unit, or the change sits at a junction ·-ing does not touch. Two kinds of change are new:
  - `·Thaw ~b~ ·-ing ~b~ ·Thaw` and `·Tea+Oy ~b~ ·-ing ~b~ ·Thaw`, where the rebuild extends ·-ing's entry and exit together and the old font, which has one extension per glyph, draws only the exit's;
  - `·They+Zoo | ·Ye ~b~ ·-ing`, the one context where the old font joins ·Ye to ·-ing; the rebuild breaks it, as the old font does everywhere else.
- Joins with partners that are not migrated, which the partner's migration verifies through its own pair evidence: ·Llan, ·Excite and ·Eat on ·-ing's entry `from:`, and ·Excite, ·Exam and ·Eat on its exit `toward:`.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.

## Recorded design overrides

·Thaw's old `exit_baseline` stance is a baseline exit row on the sole stance, at the foot, with ·-ing alone in its `toward:`. The old font never draws ·Thaw with both baseline sides joined and always gives up the entry for the exit, so the stance carries `pairings: never` on the two baseline sides and qsHe's `prefer: cell {entry: none} over {entry: baseline}`.

·-ing's baseline entry is scoped positively from the baseline. It leaves out ·Ye and ·He (the old `noentry_after`) and the ·They+Zoo ligature, which the old font never joins to ·-ing.

·-ing's entry takes no extension after ·Tea, by the author's review of `·Pea | ·Tea ~b~ ·-ing ~b~ ·Thaw`, where the old font draws none either; the old font draws one after a bare ·Tea. ·-ing's one-pixel entry extension after ·Tea+Oy stays, as the old font draws it (its `family: qsTea` target reaches the ligature by lead expansion). The half-·Day and ·Vie rulings dropped the same pixel after ·Tea+Oy; this batch keeps the old ink there until a review says otherwise.

Neither exit terminal passes `unjoined: safe` (each is reached diagonally), so ·-ing's right leg and ·Thaw's foot are blessed as `dangle:` signatures in `rebuild/m1-contact-allow.yaml`, on the ·Awe and ·Eight precedent.

Three Manual pins that ·-ing brings into scope decide three records ("The Manual has to win, somehow"):

- `·Cheer | ·Utter.alt ~b~ ·-ing | ·Key`: qsUtter's `exit baseline over entry baseline` follower list names ·-ing. The old font draws every ·X·Utter·-ing this way.
- `\· | ·Et | ·Pea.half ~x~ ·It ~b~ ·-ing`: qsEt yields its baseline exit before ·Pea·It·-ing only, so the accepted `·Et ~b~ ·Pea | ·It ·X` units keep their verdicts.
- `·See | ◊ZWNJ | ·Utter ~x~ ·May | ·Thaw | ·It ~b~ ·-ing`: a bare ·Utter at a run start enters ·May at the x-height before ·May·Thaw. The old font draws ·Utter·May this way after a ZWNJ, and the Manual's `·No | ·Utter.alt ~b~ ·May | ·Ah` keeps the alternate after a letter. The record is limited to ·Thaw because qsMay's own x-height-exit prefer collides with a wider one (`E-INCOMPARABLE` at `·Utter·May·Day+Utter`).

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E664 E652:E664 E652:E679:E664 E656:E664 E664:E656 E656:E664:E656 E652:E664:E656 E653:E656:E664 E664:E656:E664 \
  E660:E664 E662:E664 E664:E660 E664:E662 E657:E65B:E664 E657:E65B:E660:E664 E675:E657:E664 E67A:E657:E664 \
  E65E:E67A:E664 E653:E67A:E664 E67A:E665:E664 E664:E652:E653 E664:E652:E670 E664:E67A:E652:E653 \
  E672:E650:E670:E664 E672:E650:E670 E67A:E665:E656 200C:E67A:E665:E656 200C:E664 E664:200C:E656 00B7:E664
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-series.txt
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

The ·-ing block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, extension, yield, Manual-pin, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
