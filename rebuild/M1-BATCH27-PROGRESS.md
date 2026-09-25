# M1 batch 27 — qsYe

Scratch for the ·Ye migration. Delete it when the review sitting closes the batch, and move any remaining pointer to future work into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsYe.yaml` waits for the author's review, and its `# DRAFT` markers mark what to review. The old record has no ductus for ·Ye, so both motions are drafted from the bitmaps.
- The new review units wait for their sitting. `make verdict-ready` reports whether the surface is ready for it.
- Issue #212 (re-verify qsAh's recon-derived from-list members) is resolved for qsYe: the oracle rows show `·Ye ~b~ ·Ah` joining in every settlement configuration. Removing its `waits on ·Ye` label is a GitHub mutation on a public repo, so it waits for the author. The issue stays open for qsHe, qsWhy, and qsExcite.
- `E670:E666:E660` is `·It ~b~ ·No.alt | ·Ye` in the old font and `·It ~x~ ·No ~x~ ·Ye` in the rebuild, one join more: unentered ·It exits at the x-height into the loop ·No, which continues into ·Ye's climbing loop. The same regrouping before ·Jay (`E670:E666:E65F`) is already unmatched, so the ·Ye rows join that open question and get no record of their own. If the sitting rejects them, the fix is a qsIt preference for the baseline exit before `·No` followed by `·Ye`.
- The old `extend_entry_after` on ·Ye's x-height stance targets the `halves_exit_xheight` context set. Only half-·He in that set passes the stance's own `not_after` list, and the old font never joins `·He·Ye` in any configuration, so the rune has no record for it. Re-check this when ·He is migrated.

## Recorded design overrides

The x-height-entered drawing (`from_short_height` in the old record) is its own stance, `climbing-loop`, following the qsFee `reversed-loop` precedent. The loop is drawn one column wider, and the stroke enters at the x-height and climbs into it. That is a different pen motion, not ink added at the join, and the stance requires its entry. The isolated drawing is the `loop` stance.

·Ye entered at the baseline never exits. The old record's baseline-entry stance has no exit anchor, and no baseline table contains a `qsYe.en-y0.ex-y0` form. The rune writes this as `pairings: never: {entry: baseline, exit: baseline}`. In windows shaped like `·Bay ~b~ ·Ye ~b~ ·Day`, the old font keeps the right seam and breaks the left. The kernel's ranking decides the rebuild's choice, and the oracle reports it.

·Ye joins the curled-over ·See before ·Pea (`·Ye ~b~ ·See ~6~ ·Pea` in the old font), so qsSee's refusal of a baseline entry after ·Ye is scoped to its `normal` stance. The old `noentry_after_ye` stance only replaced the bare shape. Whether `·Ye·See·Pea` should join at all is the author's decision.

·Ye's one-pixel exit extension before ·I is recorded once per stance, because both stances have the baseline exit.

Entered ·It exits at the x-height into ·Ye (`·Gay ~b~ ·It ~x~ ·Ye`), while the isolated pair `·It | ·Ye` breaks. qsIt's own refusal already covers the unentered case, so ·Ye's x-height entry lists qsIt.

·Ye prefers its baseline exit over its baseline entry when both seams are available (`·Bay | ·Ye ~b~ ·Day`, as the old font draws it). The rune writes this as a yielding preference over an explicit follower list, following qsSee. The list excludes ·At before ·May, where the falling ·At takes no entry. It also excludes ·Utter before the followers that the alternate ·Utter exits into at the baseline, where ·Utter's own preference decides the seam (`·Bay ~b~ ·Ye | ·Utter.alt ~b~ ·Low`, against `·Bay | ·Ye ~b~ ·Utter ~x~ ·Zoo`, and `·Bay | ·Ye ~b~ ·Utter` at a word end). qsNo's x-height-exit preference before ·Day, ·No, ·It, and ·Oy excludes the lefts ·Pea, ·Ye, and ·It. ·Ye is excluded because only the flipped ·No takes ·Ye's exit, and the old font draws `·Ye ~b~ ·No.alt ~b~ ·Day`.

·Ye brings a Manual pin into scope, `·Key ~b~ ·No.alt | ·Tea.half ~x~ ·It` (the-manual.html:4153). It is in the `regrouping-floor-drift` class and disagrees with the rebuild's grouping, and the pin gate has no waiver mechanism. So qsNo's round-3 decline of its baseline exit before ·Tea·It lists every baseline-exiting left that the old font groups this way. ·Roe is left out, because its own x-height preference decides its seam. The ledger entry records the widening.

`·Ye ~b~ ·Thaw` and `·Ye ~b~ ·See` join only under the old ss10 overlay, where the after-Tall and after-·Ye breaks are themselves disabled substitutions. Both fall under the `ss10-isolation-completed` class, with qsYe in `SS10_UNCOVERED_BY_OLD_FONT` on the exit side only.

## Verification recipe

Run the gates one at a time, and detach heavy passes as `doc/running-long-steps.md` describes. `doc/testing.md` names each gate's authority and the macOS sandbox restrictions that need an unrestricted rerun.

```zsh
uv run pytest rebuild/test_spec_load.py -n auto --dist worksteal
uv run python -m rebuild.pipeline.run_m1
uv run python rebuild/tools/probe.py E651:E660 E655:E660 E658:E660 E665:E660 E670:E660 E655:E670:E660 E673:E660 E660:E653 E660:E659 E660:E65A E660:E65A:E650 E660:E65B E660:E665 E660:E666 E660:E668 E660:E674 E660:E675 E665:E660:E675 E660:E676 E660:E67A E660:E67B E660:E67E E651:E660:E653 E673:E660:E653 E665:E660:E653 E660:E653:E67A E660:E659:E67A E660:E67B:E652 E653:E67A:E660 E652:E679:E660 E660:E656 E660:E674:E665 E660:E666:E658 E660:E67A:E656 200C:E660 E660:200C:E653
make test-rebuild
make test
make artifact-cycle
uv run python -m rebuild.tools.scaling_sweep | tee rebuild/scaling-ladder.txt
uv run python -m rebuild.pipeline.coretext_smoke --font rebuild/out/m1/M1.otf
make verdict-ready
```

The ·Ye block in `rebuild/pipeline/smoke_sequences_m1.txt` lists the pair, break, ligature, yield, and boundary probes. Read the `invariant` block of `rebuild/review-census-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
