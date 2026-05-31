# TODO

## Kerning

- ensure kerning pairs work just as well in Quikscript Junior
- Kern:
  - ·No·Tea
  - ·See·Eight

## More/fewer glyphs

- Make ligatures
  - ·Bay·Utter
  - ·Gay·Utter ("waggon")
  - ·Gay·Out

## Other

- Make lists of letters like "goes straight up and down on the left" and put these letters in the list, and declare that ·Owe never joins to it:
  - ·Tea
  - ·Day
  - ·He
  - ·It
- Straight up and down on the right:
  - ·Pea
  - ·Tea
  - ·It

## Restructure source so join mismatches are inexpressible

Follow-on to the Phase A static validator (`tools/quikscript_join_analysis.py:validate_join_consistency`) and Phase B derived guards. Once those have stabilized for a few feature additions, look at where the validator is still doing meaningful work — i.e., places where the same join is declared on both sides and the two declarations have to be kept in sync (e.g., one form's `cursive_exit` paired with another form's matching `cursive_entry` and an `extend_entry_after`).

If a cluster of those exists, propose a top-level `joins:` (or similar) section in `glyph_data/quikscript.yaml` that declares pair-level joins as bilateral edges, with both sides' anchors derived from the edge declaration. The goal is to reduce mismatches from "build-fails on inconsistent state" to "inexpressible in source".

Don't design this until the Phase A complaints (and Phase B derivation gaps) tell us where the current source language is genuinely inadequate.

Current status: `tools/quikscript_join_analysis.py:collect_join_warnings` is clean for the real senior glyph set, and `test_real_join_warning_collector_is_clean` locks that in. Only revisit a top-level `joins:` section if future real-data warnings show repeated bilateral source-maintenance pain.

## The Manual

- Leave a column on the right of the page to show page-number markers (that link to the PDF's pages) (and also to leave space for the buttons)

## Add a pixel of extension between ·It·I

Goal: ·It·I should join with exactly one connecting pixel, mirroring the ·Ye·I fix in 03f6aa8. Blocked for now; pursue after ·It's form names are shortened (the `entry_nowhere_exit_baseline_before_day` family of names is what overflows below).

The rule (from the ductus): ·It·I joins **only when ·It is not baseline-joined to its preceding letter**. ·It alternates — entered at the baseline, it exits at the x-height; entered at the x-height, it exits at the baseline. So ·It·I joins in exactly two cases, and both need the pixel: ·It word-initial (or after a non-joining / stripped predecessor), and ·It after an x-height-exiter (so it enters at the x-height and exits at the baseline). After a baseline-exiter (e.g. ·Excite), ·It joins its predecessor and exits at the x-height, so ·It·I does **not** join and gets no pixel.

As with ·Ye·I, the pixel must live on the **left** glyph's exit (`qsIt`'s baseline-exit forms `extend_exit_before` toward `qsI`), not on ·I's entry — ·I's `extend_entry_after` drops the moment a follower pulls ·I into `ex-ext-1` / `ex-con-1`.

Two obstacles block the clean version:

1. **CFF1 63-byte name truncation.** Declaring the extension at `qsIt`'s family level generates `.ex-ext-1` variants of every baseline-exiting form, including `qsIt.ex-y0.before-day.after-no-baseline-join.ex-ext-1` (66 bytes). That truncates to 63 bytes when HarfBuzz reads it, collides, and misroutes plain ·It·I to a `before-day` form. This is the same wall the stashed "Fix B" hit. Shortening the long ·It modifier names (e.g. `after-no-baseline-join`) so even the four-modifier variants stay under 63 bytes is the prerequisite, hence pursuing this after the name rejigger.

2. **Forward extension steals ·It from a backward baseline join.** Scoping the extension to just the two serving forms (`entry_xheight_exit_baseline`, `entry_nowhere_exit_baseline`) dodges the truncation and makes ·It·I join with a pixel everywhere — but it regresses `·Excite·It·I` into an isolation leak. The generated forward rule (`sub qsIt' [qsI …] by qsIt.en-y5.ex-y0.ex-ext-1`) fires on bare ·It before ·Excite's backward baseline join can claim it, so ·It flips to an x-height entry that ·Excite's baseline exit can't feed, and ·Excite's `before-vertical` exit dangles. A blunt `not_after: [{exit_y: 0}]` guard on `entry_xheight_exit_baseline` had collateral damage (·At·It·I lost its pixel, ·Excite still leaked via the entryless form).

The real work: make the ·It·I forward extension reliably **lose** to any backward baseline join from ·It's predecessor — guard both carrier forms against baseline-exiter predecessors so that after a baseline-exiter, ·It settles on `entry_baseline_exit_xheight` (joins the predecessor, exits x-height, no ·I join), while word-initial and after-x-height-exiter ·It still take the extended baseline exit. This is fragile forward/backward cycle-arbitration territory.

When picking this back up, add a `test_it_i_extends_by_one_pixel_when_joined` parametrized over `_PAIR_SWEEP_BEFORE_FIRSTS` (mirroring `test_ye_i_extends_by_one_pixel_when_joined`), and confirm `test_no_visible_isolation_leaks` stays green — `·Excite·It·I` is the canary.

## Mixed `before:` / `not_before:` follow-ups

After the mixed-selector consolidation landed in 25c8649 (qsGay / qsHe / qsRoe / qsWay), some cleanup remains:

- If a ligature ever needs a mixed `before:` + `not_before:` selector and the forward-path emission misbehaves (e.g. the pair-override doesn't reach the ligature's lead), revisit the `expand_selectors_for_ligatures` interaction in `tools/quikscript_ir.py`. Today only positive selectors are expanded for ligatures by design, and the mixed-selector change doesn't alter that.
- Consider adding an IR diagnostic for the case where a merged form's resolved `not_before:` exclusion list subsumes its resolved `before:` list (i.e. the form can never fire). Skipped initially because the existing missing-glyph errors already catch typos and the failure mode is a silent no-op rather than a wrong shape.
