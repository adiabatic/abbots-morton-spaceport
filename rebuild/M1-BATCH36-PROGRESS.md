# M1 batch 36 — qsFoot

Scratch for the ·Foot migration. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsFoot.yaml` waits for the author's review, and its `# DRAFT` marker marks what to review. The old record has no ductus or notes for ·Foot, so the motion is drafted from the bitmap.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row holds ·Foot. Most repeat a change the same window shows with another x-height letter in ·Foot's place (·Et, ·Eight, ·No, ·Llan), at a junction ·Foot does not touch. The changes at ·Foot's own junctions are the design overrides below:
  - `·X ~x~ ·Foot | ·Tea` under ss03, where the old font joins half-·Tea;
  - `·Y ~b~ ·Gay ~x~ ·Foot` with ·Gay's exit extension, which the old font draws only after some lefts;
  - `·Foot ~x~ ·J’ai{,+Utter}` with ·J’ai's entry contraction in place of ·Foot's exit contraction, as for ·{No,Low,At,I};
  - `·Foot | ·Gay` before ·Tea+Oy and ·Thaw·-ing, as for ·{Low,At,I}.
- The table build's kernel terms are not re-measured: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next, which would make readings taken now stale; `rebuild/scaling-series.txt` keeps its ·-ing readings. `make job-costs` checks this batch's runs against the booked constants.

## Recorded design overrides

The old record is one bitmap with an x-height entry, an x-height exit and three derives, so the rune is one `sole` stance whose entry `from:` and exit `toward:` are scoped from the x-height joins the baseline shows. The entry takes every x-height exiter but ·Tea and ·Roe, which the old font never joins to ·Foot. The exit's `toward:` names the same receivers as the rising ·At's, plus ·Owe and ·Foot.

- `extend_entry_after` the halves becomes an entry `extend` by one after ·Pea and ·He, the halves that join ·Foot (half-·Tea refuses its x-height exit before ·Foot). The extension is pair-wide: the old font drops it in `·{Pea,He}.half ~x~ ·Foot ~x~ ·J’ai{,+Utter}`, where its ·Foot also contracts its exit.
- `contract_exit_before` ·J’ai becomes qsJai's and qsJai_qsUtter's entry contraction by one after ·Foot, beside ·{No,Low,At,I}, on the ·At precedent.
- The old ·Gay record extends its x-height exit by one before ·Foot, so qsGay's by-one x-height extend names qsFoot. The extension is pair-wide: the old font draws it after an unentered ·Gay and drops it after most baseline-entered ones, as it does before ·Ye and ·Llan.

The author ruled on two old-font behaviors:

- `extend_exit_before_gated` names ·Tea under ss03, and the old font draws `·Foot ~x~ ·Tea.half` there (without the extension). ·Foot and ·Tea never join, under ss03 too: ·Foot's `toward:` leaves out qsTea and neither ss03 unlock on qsTea names qsFoot.
- The old font joins `·Foot ~x~ ·Gay` only where ·Gay cannot exit, before ·Tea+Oy or ·Thaw·-ing, through its entryless-exit ·Gay stance, which takes any x-height left. As for ·{Low,At,I}, ·Foot stays off qsGay's x-height `from:`, so `·Foot | ·Gay` breaks everywhere.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E67D E650:E67D E655:E67D E657:E67D E658:E67D E661:E67D E662:E67D E663:E67D E665:E67D E666:E67D E667:E67D E670:E67D E674:E67D E675:E67D E676:E67D E67A:E67D E67B:E67D E668:E67D E652:E67D \
  E67D:E653 E67D:E65B E67D:E65D E67D:E65E E67D:E65F E67D:E660 E67D:E666 E67D:E668 E67D:E66A E67D:E670 E67D:E672 E67D:E673 E67D:E677 E67D:E678 E67D:E652 E67D:E655 E67D:E67A \
  E67D:E67D E650:E67D:E65D E662:E67D:E65D E67D:E65D:E67A E67D:E653:E67A E67D:E653:E671 E653:E67A:E67D E657:E67A:E67D \
  E67D:E655:E652:E679 E651:E655:E67D E651:E665:E67D E67D:E670:E653 \
  E67D:E67A:E652:E653 E67A:E652:E653:E67D \
  200C:E67D E675:200C:E67D E67D:200C:E653 200C:E67D:E65D 00B7:E67D 00B7:E67D:E653
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

The ·Foot block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
