# M1 batch 37 — qsExam

Scratch for the ·Exam migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsExam.yaml` waits for the author's review, and its `# DRAFT` marker marks what to review. The old record has no ductus or notes for ·Exam, so the motion, including the order of its two strokes, is drafted from the bitmap.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row holds ·Exam. Nearly all repeat a change the same window shows, in the same configuration, with ·Excite in ·Exam's place (`·May ~x~ ·Utter ~b~ ·Exam`, `·Utter ~x~ ·May ~b~ ·Exam`, `·Pea ~b~ ·No ~b~ ·Exam`, `·Bay ~b~ ·Gay ~x~ ·No | ·Exam`), or a change at a junction ·Exam does not touch. The changes at ·Exam's own junction are the rulings below:
  - after the namer dot, ·Excite joins ·Exam as at a word start, where the old font breaks (the ·Excite batch's namer-dot ruling);
  - under ss03 `·Fee ~x~ ·Tea | ·Exam` and under ss05 `·Et ~b~ ·Tea | ·Exam`, where the old font joins;
  - under ss04, `·Day ~b~ ·It | ·Exam`, where the old font joins;
  - `·Utter ~x~ ·Gay ~b~ ·Exam` with ·Gay's exit extension, which the old font draws only after an unentered ·Gay.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next, which would make readings taken now stale; `rebuild/scaling-series.txt` keeps its ·-ing readings. `make job-costs` checks this batch's runs against the booked constants.

## Recorded design overrides

The old record is one Deep bitmap with a baseline entry at its bottom-left corner, no exit, and a `nonjoining-left` stance after ·{Pea,Tea,Ye,It,Eat}, so the rune is one `sole` stance with a baseline entry and no exit. Its entry `from:` names the lefts the old font joins at the baseline, ·They among them, which ·They's baseline-exit `toward:` names back: the x-height-entered ·They joins ·Exam as in the old font, and the unentered ·They's refusal of a baseline exit except before ·May keeps `·They | ·Exam`. The author added ·Vie, which the old font breaks before ·Exam: ·Vie's swept-out baseline exit names ·Exam in its `toward:` and contracts by one before it, as before ·{-ing,No,Excite}, whose baseline entries rise on the same diagonal. The ·They+Zoo ligature, which the old font never joins to ·Exam or ·Excite, joins both by the author's ruling, as ·Zoo does: neither entry `from:` excludes it. ·Day, ·They, ·See, ·J’ai, ·Jay, ·Way and ·Why form no ligature with ·Utter before ·Exam in either font, as before ·Excite.

Records already waiting on ·Exam take effect: the by-one baseline exit extensions of ·{J’ai,Jay,He,Eight} before ·Exam and qsTea's refusal of a baseline exit before ·Exam. The old `gay_reaches_baseline_exit_forward` set names ·Exam, so qsGay's three records that transcribe it (the unentered ·Gay's exit preferred over its entry, the left's yield before ·Gay·X, and the by-one baseline exit extension) name qsExam. The extension is pair-wide: the old font draws it after an unentered ·Gay and drops it after an x-height-entered one, as it does before ·{Day,Zoo}.

·Exam's left column carries ink at y4 and y5, so the grounded ·May touches its top-left corner at y5 when the two join. The old font draws the same composite, and `rebuild/m1-contact-allow.yaml` allows the contact.

The author ruled that ·It never joins ·Exam: qsIt stays off ·Exam's entry `from:`, under ss04 too, as ·It breaks before ·{Pea,Tea,Ye} there. The old font's after-·Day ·It stance under ss04 draws `·Day ~b~ ·It ~b~ ·Exam`.

The author's rulings from the ·Excite and ·Eat batches decide two more old-font joins:

- `·Fee ~x~ ·Tea ~b~ ·Exam` under ss03 and `·Et ~b~ ·Tea ~b~ ·Exam` under ss05 break through qsTea's existing refusal of a baseline exit before ·Exam.
- After the namer dot, ·Excite joins ·Exam as at a word start.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E66C E651:E66C E653:E66C E655:E66C E65D:E66C E65F:E66C E662:E66C E665:E66C E666:E66C E66B:E66C E673:E66C E67A:E66C E652:E679:E66C \
  E650:E66C E652:E66C E660:E66C E670:E66C E671:E66C E657:E65B:E66C E653:E671:E66C E653:E67A:E66C E66C:E653 E66C:E652 \
  E651:E655:E66C E67A:E655:E66C E675:E657:E66C E676:E657:E66C E658:E652:E66C E672:E652:E66C E653:E670:E66C \
  E665:E67A:E66C E67A:E665:E66C E650:E666:E66C E651:E655:E666:E66C \
  E67A:E652:E653:E66C E66C:E67A:E652:E653 \
  200C:E66C E651:200C:E66C 00B7:E66C 00B7:E66B:E66C
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

The ·Exam block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, ruling, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
