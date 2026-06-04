# Shaping-leak fix loop journal

Cross-context memory for the loop in `doc/shaping-leak-loop/runbook.md`. Append one entry per fix and per skip. A fresh window reads this to know what's done, what's intractable, and which working-tree edits are its own. This file is tracked (it is the audit trail of an autonomous agent editing the font) and is committed as part of the loop's final batch.

Starting state (bless of commit d7f3269): 195 bad in `test/bad-leak-backlog.txt`, 242 benign in the census, gates green.

## Hard track (defer — needs second-order revert machinery, not a one-line lever)

The 17 signatures in `test/leak-force-bad.yaml` are cross-lookup-compose leaks (changed side strips to bare while an unchanged ligature neighbor keeps reaching). Do not attempt these with `not_before`/contract levers; leave in the backlog and surface at handoff. See the runbook's "hard track" section.

## Log

<!-- One entry per fix/skip. Format:
- [N] SIG `il->lc | ir->rc` — FIXED: lever (qsX.form field) | make test-leaks: targeted gone, 0 new | make test: <green @ batch K | not-run-this-batch>
- [N] SIG `...` — SKIPPED: reason
-->

(no iterations yet)
