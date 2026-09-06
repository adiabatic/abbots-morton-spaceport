# Conventions for the shipped font’s YAML

`glyph_data/quikscript.yaml` is the source of the shipped font, compiled by the Python engine under `tools/` (`tools/quikscript_ir.py` builds the IR, `tools/quikscript_fea.py` emits the feature code). The rebuild’s rune files under `glyph_data/runes/` are a different format with its own design document, `doc/rebuild-design.md`. AGENTS.md carries the rules an agent needs before editing either file; this document holds the mechanism behind the old engine’s rules and the recipes that prove an edit to it is a pure cleanup.

## Selectors

- `{exit_y: N}` / `{entry_y: N}` resolve to every letter with an anchor at that Y. `except: [{family: …}, …]` drops families from the resolved set, so `{exit_y: 0, except: [{family: qsYe}, {family: qsPea}, {family: qsTea}]}` is every baseline exiter but ·Ye, ·Pea, and ·Tea.
- A family-scoped anchor selector — `{family: qsMay, exit_y: 5}` in an `after` list, `{family: qsTea, entry_y: 0}` in a `before` list — behaves like the bare family selector, compatible ligature and component expansion included, and then keeps only the variants with a compatible anchor at that Y.
  - It may still compile to include the bare scoped glyph when that glyph is the pre-lookup stance for an unrestricted entry or exit upgrade at the requested Y. That is what lets a cyclic join such as ·They·May use `{family: qsMay, entry_y: 0}` instead of widening back to `{family: qsMay}`.
  - `tools/suggest_scoped_anchor_selectors.py` lists narrowing candidates; `make review` renders the scoped-anchor review page.
- Anchor selectors expand to every matching variant, so replacing a long family list with one can change the generated feature code or raise join warnings even when the source reads as equivalent. A narrowing or a list replacement is a cleanup only once the proof below says so.
- When a narrow `after:` selector competes with a broad fallback such as a `context_set`, the narrow selector must win first.
- Repeated `select` / `derive` lists are consolidated into `context_sets`; `doc/cleanup.md` is the recipe.

### Proving a selector change is a pure cleanup

Capture the baseline on the branch you are changing from, make the change, rebuild, and compare the Senior feature code (or the six OTFs’ checksums):

```sh
make check-html-before
# edit glyph_data/quikscript.yaml
make all
diff site/before/AbbotsMortonSpaceportSansSenior-Regular.fea site/AbbotsMortonSpaceportSansSenior-Regular.fea
```

Byte-identical output means the change is equivalent and `make test` plus `make review` finishes the job. Any divergence is a real shaping change: surface the diff for review rather than landing it as a cleanup.

## Ligatures

- A two-glyph ligature inherits its entry anchor from its lead (`_inherit_ligature_entries_from_lead` in `tools/quikscript_ir.py`); a redundant or mismatched explicit declaration raises `LigatureEntryInheritanceWarning`. Keep an explicit entry only when the lead’s inheritable stance is context-restricted (`qsThey.en-y5`) or the ligature’s bitmap does not share the lead’s leftmost-ink column at the entry’s Y.
- The exit side mirrors that: `_iter_related_extension_targets` propagates the trailing component’s `extend_exit_before` / `contract_exit_before` onto a `qsX_qsY` ligature, and `calt_liga` routes `(qsX, qsY.<exit-modifier>)` to `qsX_qsY.<exit-modifier>`. Don’t restate trailing-component exit rules on a ligature — unless it declares its own `noentry_after`, which skips the propagation, in which case the ligature carries its exit rules in YAML. `qsDay_qsEat` and `qsThey_qsUtter` are the ligatures that do.
- Never hand-list ligature names (`qsJay_qsUtter`) in `select.after` / `select.before`: the `expand_selectors_for_ligatures` IR pass adds them from the trailing or lead component, and its docstring records the edge cases.
- A ligature opts out of every left-side join by declaring `entry: null` on its `prop.anchors` (the `entry_explicitly_none` field on the IR’s `JoinGlyph`). The FEA emitter reverts predecessors on its own, `expand_selectors_for_ligatures` skips the forward expansion because there is no entry Y to satisfy, and the backward direction still fires so `after: [trailing_component]` selectors on followers keep picking the ligature up after `liga`. Don’t hand-author `not_before: [qsX_qsY]` on predecessor variants to compensate.
- When the right glyph is about to be consumed into a ligature with no matching entry, the left glyph must not keep a now-false exit. ·Excite·Tea·Oy is the worked example: `qsTea_qsOy` has no baseline entry, so `qsExcite.ex-y0.before-vertical` surrenders its exit. Extend the `_PENDING_BK_ENTRY_GUARDS` table in `tools/quikscript_join_analysis.py` rather than broadening the plain pair-guard machinery.

## Entryless followers and `ex-noentry` stances

When a `noentry_after` ligature leaves a predecessor’s bare bitmap with unsupported exit-side ink, give the predecessor an explicit `.ex-noentry` stance with no exit anchor and the trimmed bitmap. Two flavors exist:

- **Entryless** (`qsMay.ex-noentry`): no entry either. The post-`liga` cleanup chooses it when the predecessor’s selected variant has no entry anchor, which is the usual case when nothing exits at the matching Y.
- **Entry-preserving** (`qsMay.en-y0.ex-noentry`, authored as `inherits: entry_baseline` plus `anchors.exit: null` and an `[ex-noentry]` modifier): the entry side stays so the join with the predecessor still attaches. Authoring one also opts the family out of the `calt_cycle` guard `_propagate_noentry_after_to_not_before` would otherwise emit, so the matching entry-bearing stance (`qsMay.en-y0`) is picked before `liga` and ·Roe ~b~ ·May holds whether or not the entryless ligature follows.

`_exit_noentry_fallback` in `tools/quikscript_fea.py` picks the replacement by matching the input variant’s entry side and modifier set, so each input variant routes to its closest sibling. The second pass, `calt_post_liga_left_cleanup_pred`, fires only when the replacement is entryless: any pre-predecessor whose `select.before` clause was triggered by the demoted family reverts to its bare base, so a glyph like `qsRoe.ex-y0` stops extending toward an entry the ligature does not offer.

An entry-preserving `ex-noentry` stance can also serve as a `before:` forward-pair override to match split shaping across a non-joining break. Scope its left context to predecessors whose isolated left half already chooses the same entry stance — an anchor selector with `except` for the positive predecessor set — and add a `trailing_demote_overrides` entry when the follower may already have taken a backward upgrade that must return to the isolated right-half stance (`qsIt.en-y0.ex-noentry.before-day-exam` demotes `qsDay.half` back to `qsDay`).

## Stance shortcuts

- `strip_entry_before: true` lets an entryless forward-exit stance displace its entry-bearing siblings without a near-duplicate `before:` stance. `qsIt.entry_nowhere_exit_baseline` is the worked example; the field’s description in `.vscode/quikscript.schema.json` states the full rules, including how it pairs with `select.not_after` and `select.not_before`.
- In `calt` selectors, ZWNJ is the literal `uni200C` glyph. List it beside `space` in `after` / `not_after` when blocking word boundaries.
