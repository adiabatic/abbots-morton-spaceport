# TODO

## Kerning

- ensure kerning pairs work just as well in Quikscript Junior
- Kern:
  - ·No·Tea
  - ·See·Eight

## More glyphs

- Standalone
  - an ·I that drags along the baseline before going up sharply so it works with ·Way.half better (see "wild" and "wise" in The Manual)
- Make ligatures
  - ·Bay·Utter
  - ·Gay·Utter ("waggon")
  - ·Gay·Out

## Other

- Use `{exit_y: N}` / `{entry_y: N}` only where the replacement is proven equivalent after variant expansion. A quick audit at `72f7466` of long family-only `select.after` / `select.before` / `select.not_after` / `select.not_before` lists found no safe shorter replacements: the promising `qsTea.half_entry_xheight_ss03.select.after` and `qsWhy.half.select.not_before` rewrites changed generated Senior FEA, and the `qsTea` rewrite introduced a join warning. Remaining possible cleanup should focus on `derive.extend_*` targets that truly mirror anchor classes, with generated FEA or shaping-test proof before committing.
## Anchor-geometry regularization

Audit (run `tools/audit_anchor_geometry.py` if saved, otherwise the inline script in the original audit thread): the codebase historically mixed two internally consistent conventions for cursive anchor placement.

| Side  | Tight (CLAUDE.md convention) | Loose                       |
| ----- | ---------------------------- | --------------------------- |
| Exit  | exit.x = max_ink_x + 1       | exit.x = max_ink_x + 2      |
| Entry | entry.x = min_ink_x          | entry.x = min_ink_x + 1     |

Tight × tight → flush. Loose × loose → flush. Cross-convention pairs leak 1 px (or 1 px overlap, depending on direction).

### Done

- **Exits regularized to tight** (one column past the rightmost ink). 45 families touched, 89 individual `exit: [...]` edits in `glyph_data/quikscript.yaml`. Negative-gap exits (·He, ·Ye, qsThey.exit-xheight, qsHe.exit-baseline-style strokes) intentionally left alone — they're documented in CLAUDE.md as inside-bitmap anchors for left/middle-exiting strokes.

### Remaining

- **Entry side.** ~319 forms still place their entry one column inside the leftmost ink (the loose convention). Until they're tightened to `entry.x = min_ink_x`, every join with a now-tight predecessor produces a 1 px overlap (the warning system flags positive gaps, not overlaps, so these don't show up at build time — eyeball them in `test/check.html`). Triage that list family-by-family — most fixes will be one-line YAML edits, but watch for forms whose bitmap was widened to accommodate a loose entry; some bitmaps may want a column trimmed after the entry moves.
- **Entry outliers (+2 / +3).** 47 forms at entry_gap = +2 and one at +3. These are special shapes (the wider letters) — confirm each is intentional before assuming the loose pattern carries through.
- **Negative-gap entries.** 4 forms place the entry to the left of the leftmost ink. Audit whether any are accidental holdovers vs. real pre-stroke anchors.
- **CLAUDE.md.** Once entries are also tight, expand the "one pixel to the right of the stroke" paragraph to also state the entry convention symmetrically, and to acknowledge the inside-bitmap exit exceptions.
- **`extend_*` derive directives.** Now that the base anchors are tight, some of the `extend_exit_before` / `extend_entry_after` rules may be preserving (or having been chosen to fight) the old loose geometry. Spot check on next few feature additions; nothing to do speculatively.
- **Audit tool.** `tools/audit_anchor_geometry.py` not yet saved — script exists inline in the regularization thread. Save it next time the audit is needed so the canonical check is reusable.
- **Test fallout from the exit pass.** 9 tests failing after the tightening: `test_qs_gay_joining_targets_share_shifted_baseline_anchor[it/i/exam]`, `test_qs_gay_exit_baseline_extended_targets_include_joining_followers[tea/it/i/exam]`, `test_qs_gay_exit_xheight_extended_exists_for_it`, and `test_qs_he_half_contracted_pairs_with_trimmed_qs_zoo`. Each pins a specific anchor x in the compiled IR — update the expected values to match the new tight geometry.

- Make lists of letters like "goes straight up and down on the left" and put these letters in the list, and declare that ·Owe never joins to it:
  - ·Tea
  - ·Day
  - ·He
  - ·It
- Straight up and down on the right:
  - ·Pea
  - ·Tea
  - ·It
- Document why ligatures live in `calt` (not `liga`): contextual alternates must run first and be able to block ligatures by changing glyph identity (e.g. in ·Day·Utter·Low, the forward rule replaces ·Utter with ·Utter.alt before the ligature lookup sees it, preventing the ·Day·Utter ligature from firing)

## Restructure source so join mismatches are inexpressible

Follow-on to the Phase A static validator (`tools/quikscript_join_analysis.py:validate_join_consistency`) and Phase B derived guards. Once those have stabilized for a few feature additions, look at where the validator is still doing meaningful work — i.e., places where the same join is declared on both sides and the two declarations have to be kept in sync (e.g., one form's `cursive_exit` paired with another form's matching `cursive_entry` and an `extend_entry_after`).

If a cluster of those exists, propose a top-level `joins:` (or similar) section in `glyph_data/quikscript.yaml` that declares pair-level joins as bilateral edges, with both sides' anchors derived from the edge declaration. The goal is to reduce mismatches from "build-fails on inconsistent state" to "inexpressible in source".

Don't design this until the Phase A complaints (and Phase B derivation gaps) tell us where the current source language is genuinely inadequate.

Current status: `tools/quikscript_join_analysis.py:collect_join_warnings` is clean for the real senior glyph set, and `test_real_join_warning_collector_is_clean` locks that in. Only revisit a top-level `joins:` section if future real-data warnings show repeated bilateral source-maintenance pain.

## Bare-form bitmap stubs from `noentry_after` predecessors

Now that `noentry_after`-driven `not_before` blocks backward selection (a `qsX qsY` ligature with `qsM` in its `noentry_after` reverts qsM to its bare `prop` form), the bare bitmap may still carry the same exit-side ink as the joining-shape variant. Concrete case: `·Roe ·May ·They ·Utter` reverts qsMay to bare instead of qsMay.entry-baseline, but qsMay's `prop.shape: mono` and qsMay.entry-baseline's `shape: mono` are the same bitmap — so the y=5 exit ink (`   ##` in the top row) still hangs over qsThey_qsUtter.noentry's empty top-left. Audit which `noentry_after`-fed families need an `.exit-noentry` shape variant (with the exit-side ink trimmed) and how to route the cleanup to choose it over the bare form.

## The Manual

- Leave a column on the right of the page to show page-number markers (that link to the PDF's pages) (and also to leave space for the buttons)

## Mixed `before:` / `not_before:` follow-ups

After the mixed-selector consolidation landed in 25c8649 (qsGay / qsHe / qsRoe / qsWay), some cleanup remains:

- If a ligature ever needs a mixed `before:` + `not_before:` selector and the forward-path emission misbehaves (e.g. the pair-override doesn't reach the ligature's lead), revisit the `expand_selectors_for_ligatures` interaction in `tools/quikscript_ir.py`. Today only positive selectors are expanded for ligatures by design, and the mixed-selector change doesn't alter that.
- Consider adding an IR diagnostic for the case where a merged form's resolved `not_before:` exclusion list subsumes its resolved `before:` list (i.e. the form can never fire). Skipped initially because the existing missing-glyph errors already catch typos and the failure mode is a silent no-op rather than a wrong shape.
