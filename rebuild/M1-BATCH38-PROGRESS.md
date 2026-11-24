# M1 batch 38 — qsOwe

Scratch for the ·Owe migration, the last letter. Delete it when the review session closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsOwe.yaml` waits for the author's review, and its `# DRAFT` marker marks what to review. The old record has no ductus or notes for ·Owe, so the motion, a counterclockwise ring begun and ended at the top, is drafted from the bitmaps.
- The new review units wait for their review session. `make verdict-ready` reports whether the corpus is ready for it. Every new unmatched row holds ·Owe, and about two thirds repeat the change the same window shows, in the same configuration, with ·Awe or ·Ox in ·Owe's place. The rest:
  - joins the old font breaks and the rebuild makes (the ruling below): `·Low ~x~ ·Owe` before ·{Fee,Cheer,Roe,It} and wherever ·Owe joins nothing after it, `·Gay ~x~ ·Owe` after an unentered ·Gay, `·Ah | ·Gay ~x~ ·Owe`, where the old font breaks both, and `·Owe ~x~ ·{Llan,Oy}`;
  - joins the old font loses and the rebuild keeps: `·Owe ~x~ ·Owe` inside longer windows, `·Owe ~x~ ·It ~b~ ·Day` (every other x-height left already joins ·It there), and `·{They,Out,Fee} ~x~ ·Owe ~x~ ·Tea.half` under ss03;
  - ss07 rows that mirror a `default` row, since the stand-in windows list no ss07 (decision 41 of #618);
  - neighbors' extension and contraction cells in the windows ·Owe's joins newly reach.
- The scaling series and the `REPLAY_PEAK_BYTES` and `deep_replay.DEEP_REPLAY_PEAK_BYTES` readings wait for the optimization work the author plans next; `rebuild/scaling-series.txt` keeps its ·-ing readings.

## Recorded design overrides

The batch is three commits, at the author's choice: the shipped font's ss06 fix (`Join ·Owe exactly the same with ss06 on as off`), the cosmetic-set machinery (`Check that ss06 redraws an unjoined letter and never changes whether any letters join`), and `Add ·Owe`.

The rune is one `sole` stance, the closed ring, with the joined drawings as bindings in the ·Excite pattern: `open-on-the-left` for the x-height entry (`joined:`), and `cells:` rows binding `open-on-the-right` to the exit-only cell and `open-on-both-sides` to the cell joined on both sides. The ss06 gapped ring is the rune's `cosmetic:` block, which the emitter applies to the unjoined cell, plain or locked, after settlement. The conformance sweep checks ss06 and ss06+ss07 to length 3, with read-back and spec_load's guards (`doc/testing.md`).

The old exit extensions transcribe as extend records: by one before ·{Cheer,Roe,It}, by two before ·Fee, and by one before ·Tea under ss03. The old entry extension after half x-height exiters other than ·Pea and the entry contraction after ·He fire in no baseline row of any configuration, so the author dropped both.

The author ruled that ·Owe joins at the x-height on both sides as a pair-wide matter: no record refuses `·{Low,Gay} ~x~ ·Owe` or `·Owe ~x~ ·{Llan,Oy}` because of the letters around them. The old font joins `·Low ~x~ ·Owe` only when ·Owe goes on to join an unextended x-height follower, joins an unentered ·Gay to ·Owe only after a ZWNJ, and never joins ·Owe to ·Llan or ·Oy. The Manual's pins that break ·Low·Owe or ·Gay·Owe carry a ZWNJ there instead of the font refusing the join (`site/the-manual.html`).

ss07 is ·Owe's exit unlock toward ·Day{,+Utter,+Eat}, as the old font's before-day stances had it. The crate ignored an exit unlock at a height the stance declares, and the author chose to change the crate over moving the record to ·Day's side: an exit unlock now widens a declared row's `toward:` scope as far as its `when:` reaches, and its `when:` narrows an undeclared height's grant too (`Engine::exit_unlocked`). No other rune has an exit unlock.

·Gay joins ·Owe at the x-height with its exit extended by two, entered or not: qsOwe is on ·Gay's x-height `toward:` and its by-two extension. An x-height-entered ·Gay cannot also exit at the x-height, so `·Utter ~x~ ·Gay | ·Owe`, while after ·Ah ·Gay's prefer for its own exit gives `·Ah | ·Gay ~x~ ·Owe`. Under ss03 ·Owe reaches only the half ·Tea, through that stance's ss03 unlock.

A review unit's config badge is resolved against the configurations its window can hold (`Roster::window` in `rebuild/review-rs/src/scaffold.rs`), so a unit without ·Owe, which never holds ss07, keeps the badge it had; only an ·Owe window's badge can name ss07.

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
