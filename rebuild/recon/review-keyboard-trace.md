# Hand-trace: review app keyboard flow

Trace of `rebuild/review/static/app.js` against the REVIEW-PLAN §3.2 keyboard map, performed on the fixture instance assembled under `rebuild/out/review/` (fixture manifest: 6 units, 2 classes, 2 batches). Each step names the code path; all 38 `node --test` assertions over the pure modules back the state transitions.

## Cold load (no hash)

`state = withDefaults(parseHash(''))` → `batch: 0`, everything else `null`. `applyHashState` fetches both class shards (batch 0 spans them), `visibleUnits = [u-0001 … u-0005]` in manifest class order, `renderBatch` builds four `details.group` folds. `ensureCursor` sees no `unit` param and `setStateReplace({unit: 'u-0001'})` via `history.replaceState` (no history spam, no `hashchange` loop — `applyHashState` is called directly and the early `return false` in the outer call prevents double work). URL is now `#batch=0&unit=u-0001`; reloading lands on the same cursor.

## The verdict keys

- `j` → `actionForKey('j', {inInput: false, overlayOpen: false, modified: false})` = `approve` → `verdictCursor('approve')` → `applyVerdict('u-0001', 'approve')`: reads the row's note input, `recordVerdict` stores `{unit, verdict, configs: null, note, at}`, pushes `{type: 'verdict', unit, prev: null}` on the undo stack, marks the unit unexported; `syncRowVerdict` paints the row (`data-verdict` left bar, `aria-pressed` on the Approve button); `advanceFrom('u-0001')` finds the next unverdicted visible unit (u-0002) and moves the cursor with `replaceState` + smooth scroll (`auto` under `prefers-reduced-motion`).
- `f` / `d` / `k` → identical path with `reject` / `either` / `skip`. Skip is a real record (`verdict: 'skip'`), so it exports, counts, and undoes like any other verdict.
- Pressing a verdict key with a config chip active (clicked beforehand) scopes the record: `unit.scopedConfig` → `configs: ['ss03']`; the scope clears after the verdict and the chip un-presses.
- Pressing the same verdict again on an already-verdicted unit retracts it (recordVerdict with `null`), mirroring check.html's click-to-retract convention.

## Undo, notes, group approve, explain

- `u` → `undoLast` pops the stack. Single verdict: the previous record (or absence) is restored and the cursor returns to that unit (`setStateReplace({unit: result.cursor})`). Group approval: one composite action restores every member and the cursor returns to the first.
- `n` → focuses the cursor row's note input. While focused, `isEditableTarget` makes every verdict key inert (`actionForKey` returns `null`); `Escape` is the one exception and blurs back to the list. A note typed before the verdict rides into the record at verdict time; a note edited after updates the live record via `updateNote` and re-marks it unexported.
- `g` → `approveGroupOf(cursor)`: every visible, unverdicted unit in the cursor's group gets one `groupApprove` call (single undo action), toast reports the count, then auto-advance.
- `x` → toggles the cursor row's explain/provenance/drafts panel (`hidden` + `aria-expanded`).

## Navigation

- `ArrowDown` / `ArrowUp` → `moveCursor(±1)` with `stepIndex` clamping at the list ends; no verdict recorded.
- `[` / `]` → `shiftBatch(±1)` over `availableBatches(manifest, state.class)` (the class filter narrows the batch list); writes a real history entry (`location.hash`) with `unit` cleared, so Back returns to the previous batch. On the fixture: `]` from batch 0 lands on batch 1 (u-0006 alone), `[` returns. At the ends the buttons are disabled and the keys no-op.
- When the whole batch is verdicted, `advanceFrom` toasts "Batch fully verdicted — press ] for the next batch" instead of looping.

## Overlays and guards

- `?` opens the help dialog; `?` again (the `overlayOpen` branch) or `Escape` (native dialog behavior) closes it. With any dialog open, every other key returns `null`.
- `Cmd`/`Ctrl`/`Alt`-modified keys never dispatch (`modified` guard), so browser shortcuts like Cmd+F stay intact.
- Verdicts never touch the URL or localStorage; `beforeunload` warns while `store.unexported` is nonempty, and the progress strip shows the unexported count persistently (plus a toast every 50th unexported verdict).

## Verified mechanically

- `node --check` passes on all seven shipped/test JS files.
- `node --test rebuild/review/jstests/*.test.js`: 38 pass, 0 fail. Note: on this machine's Node v26.3.0, `node --test <directory>` does not scan the directory (it tries to resolve it as a CJS entry point and dies with `MODULE_NOT_FOUND`); use the `*.test.js` glob form.
- The fixture instance under `rebuild/out/review/` serves on 7294 (index, manifest, shard, both OTFs, app.js all 200) and the generated HTML passes a balanced-tags/single-`main`/single-`h1`/resolvable-refs check.
