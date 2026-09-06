---
name: tweak-an-old-font-join
description: One-line cursive-attachment tweaks in the old font's glyph_data/quikscript.yaml — make ·X·Y use one of Y's shapes, extend or contract the ·X·Y join by N pixels, or put before: and not_before: on one stance — by editing an existing stance instead of authoring a new one. Use when the user asks for a small join change to the old font (quikscript.yaml), not to the rebuild's runes (that is expand-or-contract-a-join).
argument-hint: "[·X·Y] [shape <name>|extend|contract] [by N]"
---

Most one-line join tweaks in `glyph_data/quikscript.yaml` fit one of the four patterns below. Check them before reaching for a new stance; a sibling stance is warranted only when other fields (selectors, anchors, bitmap) also differ. The rebuild's runes under `glyph_data/runes/` are a different record shape — expand-or-contract-a-join covers those.

Before editing, look the family up in `glyph_data/quikscript.yaml` and enumerate its variants on the relevant axis (half/full, exit Y, traits); when a letter name could mean several of them, ask rather than defaulting to a bare `{family: qsX}` selector. Keep `{family: qsX}` entries in code-point order (`postscript_glyph_names.yaml`).

## Make ·X·Y use Y's `<shape>` shape

- Add `{family: qsX}` to the `select.after` list of the qsY stance that carries that shape.
- If qsY's `prop.derive.extend_entry_after.targets` mirrors that `select.after` list, add `{family: qsX}` there too so the two stay in lockstep.
- Example: ·Jay·Roe using ·Roe's `shortened_top` shape is `{family: qsJay}` in both `qsRoe.stances.entry_extended_at_baseline.select.after` and `qsRoe.prop.derive.extend_entry_after.targets`.

## Extend the ·X·Y join by N pixels

- Add `{family: qsY}` to qsX's family-level `derive.extend_exit_before.targets`, creating the directive with `by: N` if it does not exist. The build widens X's exit stroke and shifts the exit anchor by N, so the connecting stroke gains N pixels of ink.
- Example: ·Jay·Exam extended by a pixel is `{family: qsExam}` in `qsJay.derive.extend_exit_before.targets`.
- When one stance needs different amounts for different followers, `extend_exit_before` accepts a list of `{by, targets}` rules in place of a single dict — add a second rule with the other `by` rather than a sibling stance. `extend_entry_after` takes the same list shape. `qsGay.stances.exit_xheight.derive` and `qsIng.prop.derive` are live list-form examples.

## Contract the ·X·Y join by N pixels

- Add `contract_entry_after: {by: N, targets: [{family: qsX}]}` to the `derive` of qsY's joining stance — `entry_xheight` for an x-height join, `entry_baseline` for a baseline join.
- Do this even when that stance's `extend_entry_after` already reaches qsX through a context set such as `halves_exit_xheight`: the build orders narrower selectors first, so the single-family contract wins over the broad extend.
- If the directive on that side already carries a different `by`, put the rule on the other side of the join instead.
- Example: ·He·Jay contracted by a pixel is `contract_entry_after: {by: 1, targets: [{family: qsHe}]}` in `qsJay.stances.entry_xheight.derive`, beside its existing `extend_entry_after`.

## Mix `before:` and `not_before:` on one stance

- A stance that needs both a forced positive trigger and a broad anchor-class fallback declares `before:` and `not_before:` together rather than splitting into two near-duplicates. `after:` / `not_after:` pair the same way.
- `qsGay.stances.exit_baseline` is the canonical example.

## Verify

```sh
uv run python tools/reflow_yaml.py glyph_data/quikscript.yaml
make test
```

To eyeball the change, capture the baseline OTFs first and compare in `site/check.html`:

```sh
make check-html-before   # on the baseline commit
make check-html-after    # after the edit
```
