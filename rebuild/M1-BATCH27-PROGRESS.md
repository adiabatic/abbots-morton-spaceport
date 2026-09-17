# M1 batch 27 — qsYe

Scratch for the ·Ye migration. Delete when the sitting closes the batch, lifting any surviving forward pointer into `WHATNEXT.md`.

## Parked

- The drafted ductus in `glyph_data/runes/qsYe.yaml` awaits the author's vet; its `# DRAFT` markers are the worklist. The old record carried no ductus for ·Ye, so both motions are drafted from the bitmaps.
- The new review units await their sitting. `make verdict-ready` is the readiness authority.
- Issue #212 (re-verify qsAh's recon-derived from-list members) is discharged for qsYe by the oracle pair map: `·Ye ~b~ ·Ah` joins in every settlement configuration. Its `waits on ·Ye` label is a GitHub mutation on a public repo and waits for the author; the issue stays open for qsHe, qsWhy, and qsExcite.
- `·It ~b~ ·No.alt | ·Ye` in the old font is `·It ~x~ ·No ~x~ ·Ye` in the rebuild (`E670:E666:E660`): unentered ·It exits at the x-height into the loop ·No, which carries on into ·Ye's climbing loop, one join more. The same regrouping already sits unmatched before ·Jay (`E670:E666:E65F`), so the ·Ye rows join that open question rather than getting a record of their own; a qsIt preference for the baseline exit before `·No` then `·Ye` is the shape of the fix if the sitting rejects them.
- The old `extend_entry_after` on ·Ye's x-height stance targeted the `halves_exit_xheight` context set, of which only half-·He survives the stance's own `not_after` list, and the old font never draws `·He·Ye` joined in any configuration, so the rune carries no record for it. Re-check at ·He's migration.

## Recorded design overrides

The x-height-entered drawing (`from_short_height` in the old record) is its own stance, `climbing-loop`, on the qsFee `reversed-loop` precedent: the loop is redrawn a column wider and the stroke enters at the x-height and climbs into it, a different pen motion rather than join-localized ink, and the stance requires its entry. The isolated drawing is the `loop` stance.

·Ye entered at the baseline never exits: the old record's baseline-entry stance carries no exit anchor and no `qsYe.en-y0.ex-y0` form exists in any baseline table. The rune spells it as `pairings: never: {entry: baseline, exit: baseline}`. In `·Bay ~b~ ·Ye ~b~ ·Day`-shaped windows the old font keeps the right seam and breaks the left; the kernel's arbitration decides the rebuild's pick and the oracle reports it.

·Ye joins the curled-over ·See before ·Pea (`·Ye ~b~ ·See ~6~ ·Pea` in the old font), so qsSee's after-·Ye baseline-entry refusal is scoped to its `normal` stance; the old `noentry_after_ye` stance only ever replaced the bare shape. Whether `·Ye·See·Pea` should join at all is the author's call.

·Ye's exit extension by one before ·I is recorded once per stance, since both stances offer the baseline exit.

Entered ·It exits at the x-height into ·Ye (`·Gay ~b~ ·It ~x~ ·Ye`), though the isolated pair `·It | ·Ye` breaks; qsIt's own refusal already carries the unentered case, so ·Ye's x-height entry names qsIt.

·Ye prefers its baseline exit over its baseline entry when both seams are on offer (`·Bay | ·Ye ~b~ ·Day`, as the old font draws it), spelled as a yielding preference over an explicit follower list on the qsSee model. The list carves out ·At before ·May, where the falling ·At takes no entry, and ·Utter before the followers the alternate ·Utter exits into at the baseline, where ·Utter's own preference decides the seam (`·Bay ~b~ ·Ye | ·Utter.alt ~b~ ·Low`, against `·Bay | ·Ye ~b~ ·Utter ~x~ ·Zoo` and `·Bay | ·Ye ~b~ ·Utter` at a word end). qsNo's x-height-exit preference before ·Day/·No/·It/·Oy excepts a ·Ye left beside ·Pea and ·It, since only the flipped ·No takes ·Ye's exit and the old font draws `·Ye ~b~ ·No.alt ~b~ ·Day`.

A Manual pin that ·Ye brings into scope, `·Key ~b~ ·No.alt | ·Tea.half ~x~ ·It` (the-manual.html:4153), sits in the ratified `regrouping-floor-drift` family against the rebuild's grouping, and the pin gate has no waiver channel, so qsNo's round-3 decline of its baseline exit before ·Tea·It now names every baseline-exiting left the old font groups that way (·Roe, whose own x-height preference decides its seam, stays out). The ledger entry records the widening.

`·Ye ~b~ ·Thaw` and `·Ye ~b~ ·See` join only under the old ss10 overlay, where the after-Tall and after-·Ye breaks are themselves disabled substitutions; both ride the `ss10-isolation-completed` class with qsYe in `SS10_UNCOVERED_BY_OLD_FONT`, exit side only.

## Verification recipe

Run gates serially; detach heavy passes per `doc/running-long-steps.md`. `doc/testing.md` identifies each gate's authority and the macOS sandbox restrictions that require an unrestricted rerun.

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

The ·Ye block in `rebuild/pipeline/smoke_sequences_m1.txt` is the pair, break, ligature, yield, and boundary probe worklist. Read the `invariant` block of `rebuild/review-census-pins.json` before accepting its generated diff.

## Resume

```zsh
make review-cycle SERVE=bg
make verdict-ready
```
