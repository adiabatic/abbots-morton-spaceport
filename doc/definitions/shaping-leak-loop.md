# The autonomous bad-leak fix loop (deliverable B)

This is the executable brief for the remaining build-work item in `doc/definitions/shaping-leakage.md`: the unattended detect→fix→verify loop that drains the bad-leak backlog. **Deliverable A — the boundary-faithful sweep, the bad/benign classifier, the two override channels, and the retargeted gates — is already built and green.** This loop consumes A's output; it is not yet built.

## What A hands the loop

- `site/bad-leak-backlog.txt` — the machine-readable to-do list. Each line is `…example… [break N] :: *L il->lc | *R ir->rc`; the signature is the `(isolated_left, left_chosen, isolated_right, right_chosen)` 4-tuple after `::`. `tools/leak_snapshot.parse_snapshot` reads it.
- `tools/leak_classify.py::classify(signature, visible=…)` — the bad/benign verdict, plus `force_bad_signatures()` / `force_benign_signatures()` (the per-signature override sets in `site/`).
- The gates: `make test` (fast depth-3, asymmetric `live_bad ⊆ backlog`) and `make test-leaks` (depth-4 bad gate + benign census). Re-bless both files with `make leak-snapshot`.

## The loop, per decisions 9 and 12 of the definition

For each bad leak in the backlog:

1. **Diagnose.** Read the signature. Every bad leak is an additive dangle: a break-facing edge (`left_chosen`'s exit or `right_chosen`'s entry) grew a connector toward a neighbor that isn't there. The remedy is always **subtractive**: make that edge subtractive (or revert it to the isolated form) _for the offending context only_. The levers are the existing ones — `not_before` to stop the additive form being selected, `contract_exit_before`/`contract_entry_after`, an `ex-noentry` trim, or a `predecessor_demote_overrides` / `trailing_demote_overrides` row. See the "How to do simple changes" section of `CLAUDE.md` and the worked examples it points to.
2. **Apply** the YAML edit.
3. **Verify** (the decision-12 gate, all required): rebuild (`make all`), then (a) the targeted bad leak is gone; (b) **zero new bad leaks anywhere** — re-sweep with `make test-leaks` (or `uv run python tools/leak_snapshot.py` + diff); (c) `make test` green so no real cursive join broke. If any fails, revert the edit and either try a different lever or skip the leak (log it for human attention).
4. The benign census may shift — that is informational; surface it at the end, never block on it.

A few bad leaks are force-bad cross-lookup-compose cases (the changed side strips to bare while an unchanged ligature neighbor absorbs the join). These need the second-order revert machinery, not a one-line lever; the loop should recognize them (they're the `site/leak-force-bad.yaml` signatures) and flag rather than flail.

## Substrate and landing (the user's choices)

- **Persistent self-pacing loop.** Run it as a `/loop` (or the ralph-loop plugin) that re-enters across turns, picks the next backlog entry, runs the cycle above, and keeps going until the backlog is empty or only force-bad/intractable entries remain. It self-paces; it does not need a fixed interval.
- **Batch, one approval at end.** Apply each _verified_ fix to the working tree but **never commit mid-run** (project rule: never commit without explicit approval). Keep a running log of what changed and the shrinking bad count. When the loop stops, present the whole batch + diff + re-blessed backlog for a single approval. Per the project's commit-message convention, when a natural commit point is reached, spawn a fresh sub-agent to draft commit-message suggestions.

## Why fixes can't be parallelized

The verify gate is global to the whole FEA — a fix anywhere can introduce a dangle anywhere — and every fix edits the one `glyph_data/quikscript.yaml`. So _diagnosis_ of distinct bad leaks can fan out, but _apply + verify_ must be sequential (each fix rebuilds and re-sweeps against the whole corpus). Budget the loop accordingly: the depth-4 re-sweep is ~1 min, so the verify step dominates wall-clock.

## Done

The backlog reaches empty (or only documented-intractable entries remain), `make test` and `make test-leaks` are green against the re-blessed files, and the batch is presented for approval. At that point the asymmetric bad gate has converged to the spec's "zero bad" by construction.
