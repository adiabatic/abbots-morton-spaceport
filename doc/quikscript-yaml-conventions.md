# Conventions for the shipped font’s YAML

`glyph_data/quikscript.yaml` is the source of the shipped font, compiled by the Python engine under `tools/` (`tools/quikscript_ir.py` builds the IR, `tools/quikscript_fea.py` emits the feature code). The rebuild’s rune files under `glyph_data/runes/` use a different format, described in `doc/rebuild-design.md`. `AGENTS.md` has the rules an agent needs before editing either file. This document explains the mechanism behind the old engine’s rules and gives the recipe that shows an edit is a pure cleanup.

## Selectors

- `{exit_y: N}` / `{entry_y: N}` resolve to every letter with an anchor at that Y. `except: [{family: …}, …]` drops families from the resolved set, so `{exit_y: 0, except: [{family: qsYe}, {family: qsPea}, {family: qsTea}]}` is every baseline exiter but ·Ye, ·Pea, and ·Tea.
- A family-scoped anchor selector, such as `{family: qsMay, exit_y: 5}` in an `after` list or `{family: qsTea, entry_y: 0}` in a `before` list, expands like the bare family selector, including ligature and component expansion, and then keeps only the variants with a compatible anchor at that Y.
  - It can still include the family’s bare glyph when that glyph is the stance an unrestricted entry or exit upgrade at the requested Y starts from. This lets a cyclic join such as ·They·May use `{family: qsMay, entry_y: 0}` instead of the whole `{family: qsMay}`.
  - `tools/suggest_scoped_anchor_selectors.py` lists candidates for narrowing, and `make review` renders the scoped-anchor review page.
- Anchor selectors expand to every matching variant, so replacing a long family list with one can change the generated feature code or raise join warnings even when the source looks equivalent. A narrowing or a list replacement counts as a cleanup only after the check below shows no change.
- When a narrow `after:` selector competes with a broad fallback such as a `context_set`, the narrow selector must win first.
- Repeated `select` / `derive` lists are consolidated into `context_sets` in a separate cleanup pass (`doc/cleanup.md`). The check below shows whether a consolidation changed anything.

### Proving a selector change is a pure cleanup

Capture the baseline on the branch you are changing from, make the change, rebuild, and compare the Senior feature code (or the checksums of the six OTFs). `make check-html-before` copies only the OTFs into `site/before/`, so copy the feature code there yourself:

```sh
make check-html-before
cp site/AbbotsMortonSpaceportSansSenior-Regular.fea site/before/
# edit glyph_data/quikscript.yaml
make all
diff site/before/AbbotsMortonSpaceportSansSenior-Regular.fea site/AbbotsMortonSpaceportSansSenior-Regular.fea
```

Byte-identical output means the change is equivalent, and `make test` plus `make review` complete the check. Any difference is a real shaping change: show the diff for review instead of committing it as a cleanup.

## Ligatures

- A two-glyph ligature inherits its entry anchor from its lead (`_inherit_ligature_entries_from_lead` in `tools/quikscript_ir.py`). An explicit entry on a ligature always issues a `LigatureEntryInheritanceWarning` (through `warnings.warn`, so the build continues), saying whether inheritance would give the same anchor, a different one, or none. Keep an explicit entry only when the lead’s inheritable stance is context-restricted (`qsThey.en-y5`) or the ligature’s bitmap does not share the lead’s leftmost-ink column at the entry’s Y.
- The exit side works the same way: `_iter_related_extension_targets` copies the trailing component’s `extend_exit_before` / `contract_exit_before` onto a `qsX_qsY` ligature, and `calt_liga` maps `(qsX, qsY.<exit-modifier>)` to `qsX_qsY.<exit-modifier>`. Don’t repeat the trailing component’s exit rules on a ligature. The exception is a ligature that declares its own `noentry_after`, which turns off the copying, so the ligature’s exit rules must be written in its YAML. `qsDay_qsEat` and `qsThey_qsUtter` are the ligatures that do this.
- Don’t list ligature names (`qsJay_qsUtter`) by hand in `select.after` / `select.before`. The `expand_selectors_for_ligatures` IR pass adds them from the trailing or lead component, and its docstring covers the edge cases.
- A ligature opts out of every join on its left by declaring `entry: null` on its `prop.anchors` (the `entry_explicitly_none` field on the IR’s `JoinGlyph`). The FEA emitter then reverts predecessors by itself. `expand_selectors_for_ligatures` skips the forward expansion because there is no entry Y to match, and still does the backward expansion, so `after: [trailing_component]` selectors on followers still match the ligature after `liga`. Don’t add `not_before: [qsX_qsY]` by hand to predecessor variants to compensate.
- When the right glyph is about to become part of a ligature with no matching entry, the left glyph must not keep an exit that no longer has anything to join. In ·Excite·Tea·Oy, `qsTea_qsOy` has no baseline entry, so `qsExcite.en-y0.ex-y0.before-vertical` drops its exit. Handle such cases by adding to the `_PENDING_BK_ENTRY_GUARDS` table in `tools/quikscript_join_analysis.py`, whose hand-written glyph names (`qsExcite.ex-y0.before-vertical`) `heal_glyph_name` maps to compiled names, not by widening the ordinary pair guards.

## Entryless followers and `ex-noentry` stances

When a `noentry_after` ligature leaves a predecessor’s bare bitmap with exit-side ink that nothing joins, give the predecessor an explicit `.ex-noentry` stance with no exit anchor and the trimmed bitmap. There are two kinds:

- **Entryless** (`qsMay.ex-noentry`): no entry anchor either. The post-`liga` cleanup chooses it when the predecessor’s selected variant has no entry anchor, which is usual when the letter before it has no exit at the matching Y.
- **Entry-preserving** (`qsMay.en-y0.ex-noentry`, written as `inherits: entry_baseline` plus `anchors.exit: null` and an `[ex-noentry]` modifier): the entry anchor stays, so the join with the predecessor still attaches. Adding one also exempts the family from the `calt_cycle` guard that `_propagate_noentry_after_to_not_before` would otherwise emit, so the matching stance with an entry (`qsMay.en-y0`) is chosen before `liga`, and ·Roe ~b~ ·May holds whether or not the entryless ligature follows.

`_exit_noentry_fallback` in `tools/quikscript_fea.py` picks the replacement by matching the input variant’s entry anchors and modifiers, so each input variant maps to its closest sibling. The second lookup, `calt_post_liga_left_cleanup_pred`, applies only when the replacement is entryless: the glyph two places before the ligature reverts to its bare base if its `select.before` clause matched the demoted family, so a glyph such as `qsRoe.ex-y0` stops extending toward an entry the ligature does not have.

An entry-preserving `ex-noentry` stance can also serve as a `before:` forward-pair override, so that shaping across a non-joining break matches the shaping of the two halves separately. Limit its left context to predecessors whose isolated left half already chooses the same entry stance, using an anchor selector with `except` to describe that set. Add a `trailing_demote_overrides` entry when the follower may already have taken a backward upgrade that must go back to its isolated stance (`qsIt.en-y0.ex-noentry.before-day-exam` demotes `qsDay.half` back to `qsDay`).

## Stance shortcuts

- `strip_entry_before: true` lets an entryless stance with a forward exit replace its siblings that have an entry, without a near-duplicate `before:` stance. `qsIt.entry_nowhere_exit_baseline` is an example. The field’s description in `.vscode/quikscript.schema.json` gives the full rules, including how it combines with `select.not_after` and `select.not_before`.
- In `calt` selectors, ZWNJ is the literal `uni200C` glyph. List it beside `space` in `after` / `not_after` when blocking word boundaries.
