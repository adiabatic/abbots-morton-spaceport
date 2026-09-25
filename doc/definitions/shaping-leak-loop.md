# The autonomous bad-leak fix loop (deliverable B)

This is the brief for the unbuilt part of `doc/definitions/shaping-leakage.md`: an unattended detect→fix→verify loop that empties the bad-leak backlog. **Deliverable A** (the boundary-faithful sweep, the bad/benign classifier, the two override channels, and the gates) is built, and the loop uses its output.

## What A hands the loop

- `site/bad-leak-backlog.txt` is the to-do list. Each line is `<example> [break N] :: *L il->lc | *R ir->rc`, where `*` marks a side that changed. The signature is the `(isolated_left, left_chosen, isolated_right, right_chosen)` tuple after `::`. `tools/leak_snapshot.parse_snapshot` reads it.
- `tools/leak_classify.py::classify(signature, visible=…)` returns the bad/benign verdict. `force_bad_signatures()` and `force_benign_signatures()` load the per-signature override lists in `site/`.
- The gates: `make test` runs the depth-3 bad gate, which fails on a live bad leak missing from the backlog. `make test-leaks` runs the depth-4 bad gate and the benign census. `make leak-snapshot` re-blesses both files.

## The loop, per decisions 9 and 12 of the definition

For each bad leak in the backlog:

1. **Diagnose.** Read the signature. Every bad leak is an additive dangle: a break-facing edge (`left_chosen`’s exit or `right_chosen`’s entry) gained a connector toward a neighbor it doesn’t join. The fix is always **subtractive**: make that edge subtractive, or revert it to the isolated form, _for the offending context only_. The mechanisms are `not_before` to stop the additive stance being selected, `contract_exit_before` / `contract_entry_after`, an `ex-noentry` trim, or a `predecessor_demote_overrides` / `trailing_demote_overrides` row. The tweak-an-old-font-join skill (`.claude/skills/tweak-an-old-font-join/SKILL.md`) has the patterns and worked examples.
2. **Apply** the YAML edit.
3. **Verify** with the decision-12 gate. Rebuild (`make all`), then require all three: (a) the targeted bad leak is gone; (b) no new bad leak appears anywhere, checked with `make test-leaks` or with `uv run python tools/leak_snapshot.py` and a diff; (c) `make test` passes, so no real cursive join broke. If any check fails, revert the edit and either try a different mechanism or skip the leak and log it for a human.
4. The benign census may change. Report the change at the end and don’t stop the loop for it. `make test-leaks` fails on any census change until `make leak-snapshot` re-blesses it, so check (b) should read only the result of `test_bad_leak_backlog_unchanged`.

Some bad leaks are on the force-bad list, `site/leak-force-bad.yaml`, because the proxy reads them as benign. Most of them are cross-lookup-compose cases: the changed side strips to its bare form while an unchanged ligature neighbor absorbs the join. A one-line record change does not fix these. They need the second-order (downstream-revalidation) contract that Phase 4 of `doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md` describes. The loop should recognize force-bad signatures and flag them for a human instead of attempting a fix.

## Substrate and landing (the user’s choices)

- **Persistent self-pacing loop.** Run it as a `/loop` (or the ralph-loop plugin) that re-enters across turns, picks the next backlog entry, runs the steps above, and continues until the backlog is empty or only force-bad or intractable entries remain. It needs no fixed interval.
- **Batch, one approval at end.** Apply each _verified_ fix to the working tree but **never commit during the run**, because the project requires explicit approval for every commit. Keep a running log of what changed and of the bad count. When the loop stops, present the whole batch, the diff, and the re-blessed backlog for one approval. At that commit point, spawn a fresh sub-agent to draft commit-message suggestions.

## Why fixes can’t be parallelized

The verify gate covers the whole FEA, because a fix anywhere can introduce a dangle anywhere, and every fix edits the same `glyph_data/quikscript.yaml`. Diagnosis of separate bad leaks can run in parallel, but apply and verify must run one fix at a time, since each fix rebuilds and re-sweeps the whole corpus. The depth-4 re-sweep takes about a minute, so verification dominates wall-clock time.

## Done

The loop is done when the backlog is empty or holds only documented-intractable entries, `make test` and `make test-leaks` pass against the re-blessed files, and the batch is presented for approval. With an empty backlog, the depth-4 bad gate fails on any bad leak.
