# Shaping-leak fix loop: runbook + kickoff

This is the operational runbook for deliverable B — the long-running loop that drains `test/bad-leak-backlog.txt` (195 bad leaks at last bless). The _design_ brief is `doc/definitions/shaping-leak-loop.md`; read it once. The _definition_ of bad/benign is `doc/definitions/shaping-leakage.md`. This file is what an agent re-reads on every cold start to resume.

If you are an agent reading this fresh: you have probably been re-invoked with no memory of prior iterations. That is by design. **Everything you need is on disk** — reconstruct your state from the files below, do one iteration, write it down, and stop (the loop will wake you again).

## Status: the one-line seam is drained — a restarted loop must target the second-order track

As of the journal's "Inflection point" note, the clean one-line `not_before` / `not_after` seam this runbook's per-iteration recipe is built around is **exhausted**: fix [1] (·May→·He) was the only such fix, because ·He is the script's only universal non-receiver. The ~194 remaining backlog entries all need **second-order machinery** — entry-preserving `*.ex-noentry` forms, break-context-scoped `before:` / `after:` suppression, or demote-overrides (see the hard-track section and `doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md` Phase 4). A fresh window that just follows the one-line recipe below will re-read the journal, re-confirm exhaustion, and yield without a fix. **So if you are restarting the loop to make progress, retarget it at the second-order work explicitly** (the per-leak loop is a poor fit for this — a deliberate batch pass is better); otherwise stop and hand off to the user.

## How this loop manages context (read this first)

The loop will run for many hours across far more than one context window. It survives that because **no progress lives in the conversation** — it all lives in files:

- **`test/bad-leak-backlog.txt` is the to-do list and the progress bar.** It shrinks by one entry each time you fix and re-bless a leak. A fresh window reads it to see what's left. Never hold the backlog in your head; re-read the file.
- **`doc/shaping-leak-loop/journal.md` is the cross-window memory.** After every fix _and_ every skip, append one entry: the signature, the lever you used (which YAML form/field), the verify result, and — for skips — why. A new window reads the journal to avoid re-attempting a leak you already proved intractable, and to know which working-tree edits are yours. It is tracked (not in `tmp/`): it is the audit trail of an autonomous agent editing the font, committed with the loop's final batch.
- **The git working tree is the accumulating batch.** You do _not_ commit mid-run (project rule, and the user chose "one approval at the end"). So the only record that fix #37 happened is: the backlog shrank, the journal logged it, and `git diff` shows the YAML edit. Those three must always agree. If they ever disagree, trust `git diff` + `make test-leaks` over the journal and reconcile.

Concrete rules that keep each window cheap:

1. **Stateless iterations.** Pick the next target by re-reading `test/bad-leak-backlog.txt` and skipping any signature the journal marks done/skipped. Do not rely on chat history.
2. **Push token-heavy work into sub-agents.** Diagnosing a dangle (reading `quikscript.yaml`, tracing selectors, finding the right lever) is exploration that bloats context. Spawn a sub-agent to do it and return _only_ the proposed edit (file, anchor, before/after). The sub-agent's exploration is discarded; your main loop keeps just the verdict.
3. **Capture summaries, not firehoses.** Run builds/tests with output piped to `tail` (pass/fail lines only). Never paste full `make test` output into context — it is thousands of lines.
4. **One iteration per wake, then yield.** Do a single fix-and-verify (or a small handful), append to the journal, and stop. Let the persistent `/loop` re-invoke you with a fresh window. When you notice your context is getting low _mid-iteration_, finish the current verify, journal it, and stop early — never start a new fix on a nearly-full context.
5. **Cold-start recipe** (paste-able, for any new window): read this runbook → read `doc/shaping-leak-loop/journal.md` → `git diff --stat` to see the accumulated batch → re-read `test/bad-leak-backlog.txt` → continue.

## One iteration, step by step

1. **Pick a target.** First unfixed, non-skipped signature in `test/bad-leak-backlog.txt`. Skip the ones the journal (and `test/leak-force-bad.yaml`) mark as cross-lookup-compose — those need second-order revert machinery, not a one-line lever; defer them to the "hard track" below unless that is explicitly what you're doing.
2. **Diagnose** (sub-agent). The signature `*L il->lc | *R ir->rc` names the break-facing edge that grew a connector: the left glyph's exit (`lc` gained an `ex-yN`) or the right glyph's entry (`rc` gained an `en-yN`). The fix is always _subtractive for that context only_ — `not_before`, `contract_exit_before`/`contract_entry_after`, an `ex-noentry` trim, or a demote-override. The "How to do simple changes" section of `CLAUDE.md` lists the levers with worked examples. The sub-agent returns the exact YAML edit.
3. **Apply** the edit to `glyph_data/quikscript.yaml`.
4. **Verify** (the gate, all required, cheapest first):
   - `make all 2>&1 | tail -3` — rebuilds; must succeed.
   - `make test-leaks 2>&1 | tail -20` — the targeted bad leak must be gone, and **zero NEW bad leaks** anywhere. This is the per-fix gate.
   - `make test` — confirms no real cursive join broke. This is ~3 min and token-heavy, so run it on a **cadence**, not every fix: after every ~5 fixes, and always before the final handoff. (Strict decision-12 says every fix; that's ~10 h of test time for 195 fixes, so this runbook batches it. If a batched `make test` fails, bisect within the batch — revert the last few fixes one at a time until green, and journal which one broke a join.)
   - If any gate fails: revert the edit, try a different lever once, else **skip** (journal why) and move on. Do not flail.
5. **Re-bless + journal.** `make leak-snapshot` (shrinks the backlog, may shift the census — that's fine), then append a journal entry. The backlog shrinking is your checkpoint.
6. **Yield** if context is getting low (see rule 4).

## The hard track (force-bad cross-compose leaks)

The 17 signatures in `test/leak-force-bad.yaml` are the ones the per-form proxy can't see: the changed side strips to _bare_ while an unchanged ligature neighbor (`qsTea_qsOy`, `qsThey_qsZoo`, `qsSee_qsEat`, `qsThaw.ex-y0`, …) keeps reaching. A one-line `not_before` won't fix these — they need the downstream-revalidation / demote-override machinery described in `doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md` (Phase 4). Leave them in the backlog, journal them as "hard track — needs second-order revert", and surface them to the user at handoff rather than forcing a fragile fix.

## Landing (when the backlog is empty or only hard-track/skips remain)

1. `make test` (full) + `make test-leaks` green against the re-blessed files.
2. Present to the user: the journal summary (fixed / skipped / hard-track counts), the `git diff`, and the shrunken backlog. **Do not commit without approval.**
3. On approval, per `CLAUDE.md`: spawn a fresh sub-agent to draft commit-message suggestions describing how the letters now look (e.g. "Stop ·Cheer reaching toward an absent ·Tea·Oy across a break"), present those, then commit.

## Kickoff

The journal (`doc/shaping-leak-loop/journal.md`) already has a header stub. Start the persistent loop with this `/loop` prompt (self-paced, no fixed interval):

```text
/loop Drain test/bad-leak-backlog.txt one bad shaping leak at a time, following doc/shaping-leak-loop/runbook.md exactly. Resume from the journal + backlog + git diff (you have no memory of prior iterations). Do one verified subtractive fix, re-bless, journal it, and yield. Never commit. Skip the force-bad/hard-track signatures. Stop and summarize when only hard-track entries remain.
```

The loop self-paces; it does not need an interval. It will wake itself, do one iteration on a fresh context, and yield — indefinitely — until the easy backlog is drained.

**Cadence: when you yield, schedule the next wake with `delaySeconds: 60` (a 1-minute gap).** This is the author's chosen pace. Each iteration is a cold start that re-reads everything from disk, so prompt-cache warmth is moot regardless of the gap — just honor the 1-minute cadence. Pass the same `/loop` prompt verbatim so the next firing re-enters this runbook.
