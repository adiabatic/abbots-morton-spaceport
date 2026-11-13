# M1 batch 30 — qsWhy

Scratch for the ·Why migration, which also adds the ·Why+Utter ligature. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsWhy.yaml` and `glyph_data/runes/qsWhy_qsUtter.yaml` waits for the author's review, and its `# DRAFT` markers mark what to review. The old record has no ductus or notes for ·Why, so every motion is drafted from the bitmaps.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row repeats a question the corpus already asks: ·Why stands unjoined beside a window that is already unmatched, or the rest of the window draws as it does with ·Way (most rows), ·He, ·Tea, ·J’ai, or ·Pea in ·Why's place. Every junction that changes at ·Why or ·Why+Utter changes the same way at ·Way or ·Way+Utter in the same window and configuration:
  - `·Why ~x~ ·It ~b~ ·Day.half` and the same before ·Low and ·Utter under ss04, where the old font draws `·Why | ·It ~b~ ·Day.half`;
  - `·Why.half ~b~ ·Gay | ·Tea ~b~ ·Day` and its siblings, where the old font leaves ·Gay's baseline exit dangling and breaks on both sides of ·Gay;
  - `·Why+Utter ~x~ ·Utter.alt ~b~ ·Day.half` and `·Why+Utter ~x~ ·They ~b~ …`, where the old font breaks after the ligature;
  - under ss03, `·Why+Utter ~x~ ·Tea ~b~ ·Day.half`, where the old font joins ·Tea at the x-height and strands ·Day.
- Joins with partners that are not migrated, which the partner's migration verifies through its own pair evidence: `·Why ~x~ ·Llan` and `·Why ~x~ ·Foot`, which the full stance's x-height exit `toward:` names.
- GitHub updates on the public repo, which wait for the author: #212 (qsWhy stays on qsAh's baseline-entry `from:`; see the overrides), #523 (·Cheer after ·Why: the placed-ink check prints only the ·Roe baseline-bar rows), #209 (·Why cannot exit into ·Tea, and the ·Why+Utter·Tea·Day windows draw as the ·Way+Utter ones do), and the `waits on ·Why` label on #212 and #523.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.

## Recorded design overrides

·Why's bent top is a binding, not a stance. The old font draws an upright top in isolation and before every follower it does not join, and bends the top one pixel right (`curved_at_top`) only when the x-height exit joins. The difference is ink on the exit row alone, so the `full` stance's one exit-live cell binds the `curved-at-top` sibling drawing, on the qsShe `flattened-bottom` precedent.

·Why's half is a stance, `half` with `traits: [half]` and `require: [exit]`, on the ·Way precedent: it draws only the part below the baseline, a different motion from the full stroke.

qsWhy carries qsWay's yielding prefer for its half's baseline exit before ·Gay·X verbatim. In the old font every window that starts ·Why·Gay draws its first junction exactly as the same window starting ·Way·Gay, and without the record ·Why breaks from ·Gay before ·Gay·Gay and ·Gay·Utter where the old font and ·Way join.

qsWhy stays on qsAh's baseline-entry `from:` (#212). The old font's bare ·Ah takes any baseline exiter, and its ·Why·Ah break comes from ·Why's side (the old half's `not_before` names ·Ah); half-·Why's `toward:`, which names ·Gay alone, carries that break, so both fonts draw `·Why | ·Ah` everywhere.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E663 E663:E653 E663:E655 E663:E65B E663:E65D E663:E65F E663:E666 E663:E668 E663:E670 E663:E672 E663:E673 E663:E677 E663:E678 E663:E67A \
  E663:E653:E67A E663:E65D:E67A E663:E65F:E67A E663:E650 E663:E652 E663:E65E E663:E660 E663:E675 E663:E676 E651:E663 \
  E663:E655:E655:E666 E663:E655:E67A:E665 E663:E655:E652:E653 E663:E655:E666 E663:E670:E653 E663:E652:E653 \
  E663:E67A:E650 E663:E67A:E651 E663:E67A:E652 E663:E67A:E655 E663:E67A:E656 E663:E67A:E65D E663:E67A:E668 E663:E67A:E652:E653 E663:E67A:E67A:E653 \
  E663:E672:E666 E663:E672:E659:E67A:E668 200C:E663 200C:E663:E655 E663:200C:E653 00B7:E663
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-series.txt
make verdict-ready
```

After a green `run_m1`, check placed ink for every row that `identical-unjoined-from-left` takes. The accepted result prints only the ·Way·Cheer·It·Roe, ·He·Cheer·It·Roe and ·Why·Cheer·It·Roe rows. Any other row it prints is one the class cannot take: stop and report it.

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

The ·Why block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
