# M1 batch 33 — qsLlan

Scratch for the ·Llan migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsLlan.yaml` waits for the author's review, and its `# DRAFT` marker marks what to review. The old record has no ductus or notes for ·Llan, so the motion is drafted from the bitmap, which is ·Low's turned upside down.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row repeats a change the same window shows, in the same configuration, with ·J’ai, ·Jay, ·Et, ·Eight, ·Awe, ·Ox, ·Ooze or ·-ing in ·Llan's place. The changes that touch a junction at ·Llan are of these kinds:
  - under ss05, `·Llan ~b~ ·Tea ~b~ ·Day` and the other windows where ·Tea joins at the baseline on both sides, where the old font breaks before ·Tea;
  - `·Llan ~b~ ·It`, mostly under ss04, where the old font breaks before ·It (`·Llan ~b~ ·It | ·Utter.alt ~b~ ·Thaw`, `·Llan ~b~ ·It ~x~ ·Zoo`), and `·Llan ~b~ ·Gay ~x~ ·No` and `·Llan ~b~ ·Utter ~x~ ·May`, where the old font breaks after ·Llan and joins the rest at the baseline;
  - `·No ~x~ ·Llan` and `·It ~x~ ·Llan` where the old font breaks before ·Llan: `·Gay ~x~ ·No ~x~ ·Llan` for `·Gay ~b~ ·No.alt | ·Llan`, and `·Utter.alt ~b~ ·It ~x~ ·Llan` for `·Utter ~x~ ·It | ·Llan`;
  - `·Pea ~b~ ·No.alt | ·Llan` where the old font draws `·Pea.half ~x~ ·No ~x~ ·Llan`.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next, which would make readings taken now stale; `rebuild/scaling-series.txt` keeps its ·-ing readings. `make job-costs` checks this batch's runs against the booked constants.

## Recorded design overrides

None. The old record is one bitmap with an x-height entry, a baseline exit and an `extend_entry_after` derive, so the rune is one `sole` stance whose entry `from:` and exit `toward:` are scoped from the baseline, with the derive as an entry `extend` after ·Pea, ·Tea and ·He on the qsEt precedent and qsJai's yielding ·Tea·Day prefer verbatim. qsDay's and qsNo's yields of their baseline exit before ·Tea·X gain ·Llan beside ·J’ai and ·Et, as the old font draws `·Day | ·Tea.half ~x~ ·Llan` and `·No | ·Tea.half ~x~ ·Llan`. ·Llan's baseline tail fails the `unjoined: safe` check, so its terminal is blessed as `dangle:` signatures in `rebuild/m1-contact-allow.yaml`, on the ·-ing precedent.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E66A E650:E66A E652:E66A E662:E66A E655:E66A E657:E66A E658:E66A E661:E66A E670:E66A E674:E66A E675:E66A E67B:E66A \
  E66A:E652 E66A:E653 E66A:E655 E66A:E656 E66A:E65B E66A:E665 E66A:E666 E66A:E674 E66A:E67A E66A:E67B \
  E66A:E653:E67A E66A:E67B:E652 E653:E67A:E66A E65D:E67A:E66A E657:E65B:E66A E67B:E652:E66A \
  E66A:E652:E653 E66A:E652:E650 E66A:E652:E670 E66A:E67A:E652:E653 E653:E652:E66A E666:E652:E66A E650:E666:E652:E66A \
  200C:E66A E652:200C:E66A E66A:200C:E653 00B7:E66A
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

The ·Llan block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
