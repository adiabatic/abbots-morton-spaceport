# M1 batch 32 — qsLoch

Scratch for the ·Loch migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row holds ·Loch and repeats a question the corpus already asks. In nearly all of them a stretch of the window on one side of ·Loch is itself an unmatched window. The rest show the same change with ·Thaw in ·Loch's place: after a letter that joins nothing forward, the rebuild draws `·No ~x~ ·Utter.alt ~b~ ·Day.half` and `·Utter.alt ~b~ ·No ~b~ ·X`, where the old font draws them as at a word start.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next, which would make readings taken now stale; `rebuild/scaling-series.txt` keeps its ·-ing readings. `make job-costs` checks this batch's runs against the booked constants.

## Recorded design overrides

None. The old record is a bare `mono` bitmap with no `prop` record, anchors, stances or derives, and the old font draws the bare `qsLoch` with a break on both sides in every window of every configuration, so the rune is one `sole` stance with no entry or exit row and no neighbor rune names it.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E669 E652:E669 E669:E652 E653:E669 E669:E653 E665:E669 E669:E665 E670:E669 E669:E670 E67A:E669 E669:E67A \
  E669:E656 E669:E670:E656 E652:E653:E669 E67A:E652:E653:E669 E669:E652:E653 E652:E679:E669 E653:E67A:E669 \
  200C:E669 E669:200C:E669 00B7:E669
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

The ·Loch block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the isolation, break, ligature-neighbor, word-end, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
