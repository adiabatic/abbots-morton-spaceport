# M1 batch 38 — qsOwe

Scratch for the ·Owe migration, the last letter. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsOwe.yaml` waits for the author's review, and its `# DRAFT` marker marks what to review. The old record has no ductus or notes for ·Owe, so the motion, a counterclockwise ring begun and ended at the top, is drafted from the bitmaps.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row holds ·Owe, and about two thirds repeat the change the same window shows, in the same configuration, with ·Awe or ·Ox in ·Owe's place. The rest:
  - `·Low | ·Owe` where the old font joins `·Low ~x~ ·Owe` before an unextended x-height exit (the ruling below);
  - joins the old font loses and the rebuild keeps: `·Owe ~x~ ·Owe` inside longer windows, `·Owe ~x~ ·It ~b~ ·Day` (every other x-height left already joins ·It there), `·{They,Out,Fee} ~x~ ·Owe ~x~ ·Tea.half` under ss03, and `·Ah ~x~ ·Gay | ·Owe`;
  - ss07 rows that mirror a `default` row, since the stand-in windows list no ss07 (decision 41 of #618);
  - neighbors' extension and contraction cells in the windows ·Owe's joins newly reach.
- The table build's kernel terms are not re-measured, now with seven settlement configurations: the issue-492, issue-534 and issue-516 harnesses the skill names are not under `var/keep/` on the 18-core M5 Pro MacBook Pro. `make job-costs` checks this batch's runs against the booked constants.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next; `rebuild/scaling-series.txt` keeps its ·-ing readings.

## Recorded design overrides

The batch is three commits, at the author's choice: the shipped font's ss06 fix (`Join ·Owe exactly the same with ss06 on as off`), the cosmetic-set machinery (`Check that ss06 redraws an unjoined letter and never changes whether any letters join`), and `Add ·Owe`.

The rune is one `sole` stance, the closed ring, with the joined drawings as bindings in the ·Excite pattern: `open-on-the-left` for the x-height entry (`joined:`), and `cells:` rows binding `open-on-the-right` to the exit-only cell and `open-on-both-sides` to the cell joined on both sides. The ss06 gapped ring is the rune's `cosmetic:` block, which the emitter applies to the unjoined cell, plain or locked, after settlement. The conformance sweep checks ss06 and ss06+ss07 to length 3, with read-back and spec_load's guards (`doc/testing.md`).

The old exit extensions transcribe as extend records: by one before ·{Cheer,Roe,It}, by two before ·Fee, and by one before ·Tea under ss03. The old entry extension after half x-height exiters other than ·Pea and the entry contraction after ·He fire in no baseline row of any configuration, so the author dropped both.

The author ruled that ·Low never joins ·Owe. The old font joins `·Low ~x~ ·Owe` only when ·Owe goes on to join an unextended x-height follower, and The Manual pins `·See ~b~ ·Low | ·Owe` and `·Gay ~b~ ·Low | ·Owe | ·Bay`.

ss07 is ·Owe's exit unlock toward ·Day{,+Utter,+Eat}, as the old font's before-day stances had it. The crate ignored an exit unlock at a height the stance declares, and the author chose to change the crate over moving the record to ·Day's side: an exit unlock now widens a declared row's `toward:` scope as far as its `when:` reaches, and its `when:` narrows an undeclared height's grant too (`Engine::exit_unlocked`). No other rune has an exit unlock.

·Gay joins ·Owe at the x-height only when ·Gay is entered, with its exit extended by two, as it treats ·Et: qsOwe is on ·Gay's x-height `toward:`, its unentered refusal, and its by-two extension. The old font also joins the ZWNJ-locked ·Gay to ·Owe, which the `split-by-space-or-zwnj` class takes. Under ss03 ·Owe reaches only the half ·Tea, through that stance's ss03 unlock.

A review unit's config badge is resolved against the configurations its window can hold (`audit.window_configs`, the Rust scaffold's `Roster::window`), so a unit without ·Owe, which never holds ss07, keeps the badge it had; only an ·Owe window's badge can name ss07.

`kernel_exec.TABLE_BUILD_PEAK_BYTES` takes the derived seven-configuration booking, 14.8 GB, which `rebuild/test_memory_budget.py` requires; the first build read more (Parked).

`rebuild/m1-contact-allow.yaml` allows the side-by-side contacts of ·Owe's ring with ·Owe, ·At, ·Foot, ·Et and ·Day{,+Utter,+Eat}, each composite pixel-identical to the old font's, and the declined-exit terminal of the ring's top, which `geometry.verify_unjoined_safe` cannot accept.

The mini bundle's `baseline-ss07.subset.tsv.gz` holds only the live table's header: ss07's sub-table keeps only windows with ·Owe, and no mini window holds one.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py rebuild/test_cosmetic.py -n auto --dist worksteal
make kernel-gate
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py \
  E67C E657:E67C E658:E67C E661:E67C E665:E67C E666:E67C E670:E67C E674:E67C E675:E67C E676:E67C E67A:E67C E67B:E67C E67D:E67C E67C:E67C \
  E67C:E658 E67C:E65B E67C:E65D E67C:E65E E67C:E65F E67C:E660 E67C:E666 E67C:E668 E67C:E670 E67C:E672 E67C:E673 E67C:E677 E67C:E678 E67C:E67D \
  E67C:E652 E67C:E653 E67C:E653:E67A E67C:E653:E671 E674:E666:E67C:E653 E665:E67C:E652 \
  E651:E655:E67C E667:E67C E65A:E667:E67C E655:E667:E67C:E651 E667:E67C:E65B E67C:E670:E653 \
  E67A:E652:E653:E67C E67C:E67A:E652:E653 \
  200C:E67C E657:200C:E67C 200C:E67C:E65B 00B7:E67C
make test-rebuild
make test
make artifact-cycle
make verdict-ready
```

After a green `run_m1`, check placed ink for every row that `identical-unjoined-from-left` takes, as the ·Exam batch did; the accepted result prints only the ·Roe baseline-bar rows ·{Way,He,Why}·Cheer·It·Roe and ·{Ye,He}·-ing·It·Roe. The ·Owe block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, ruling, and boundary probes. Read the `invariant` block of `rebuild/review-facts-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
