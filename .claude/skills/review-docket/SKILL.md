---
name: review-docket
description: Build the adjudication docket for the rebuild review surface before a sitting — run the echo prefill, cluster blank units across echo groups, and write recommendation briefs grounded in verdict history. Use when the user wants to prepare, resume, or speed up review-surface adjudication.
argument-hint: "[verdicts-file]"
---

Prepare a review sitting by pre-digesting the blank units on the live review surface (`rebuild/out/review/`, served by `uv run python -m rebuild.review.serve` on port 7294) into class- and cluster-grain decisions. The human decides everything in the browser, where the fonts render; your job is to make every browser minute land on a novel question and to multiply each decision across its lookalikes.

## Hard rules

- Never write the app's live verdict store (`verdicts-autosave.json`) or edit any existing `verdicts-*.json`. Everything you produce is a fresh `ams-review-verdicts/1` file the user imports through the app's own import control.
- Every file you emit pins the surface's `manifest_generated_at`. Never join unit ids across manifests; if stamps mismatch, stop and say so (`carry_verdicts.py` is the remedy, not `force`).
- Bulk proposals must never beat a human verdict on import collision: the app keeps the strictly newer `at`, so stamp proposal records with the manifest's `generated_at` (old, non-winning), the way `echo_verdicts.py` does. Imports are not undoable — remind the user to keep the prior verdicts file as the corrective re-import path.
- Verdicts are evidence, not gospel. Recommendations are suggestions grounded in the ledger and the user's own verdict history; state the grounds, admit when no verdicted unit shares the exact delta, and never bulk-recommend against a split history.
- Never author `why:` text, and never promote a draft's `why_stub` into a rune. That rationale is the user's voice.

## Steps

1. **Pick the frontier verdicts file.** Use the argument if given; otherwise find the `verdicts-*.json` at the repo root whose `manifest_generated_at` matches `rebuild/out/review/manifest.json` and has the most verdicts (WHATNEXT.md names the current one). Warn if the manifest's `repo_head` differs from `git rev-parse --short HEAD` — the surface is stale against the runes and the user should rebuild it or adjudicate knowingly.
2. **Echo prefill.** `uv run python rebuild/tools/echo_verdicts.py <verdicts-file>` — report how many blanks the unanimous echo groups fill (`verdicts-echo-fill.json`) and how many groups disagree.
3. **First docket pass.** `uv run python rebuild/tools/review_docket.py <verdicts-file>` — writes `rebuild/out/review/docket.html` and `tmp/docket-data.json`.
4. **Author recommendations** into `tmp/docket-recs.json` (`{preamble, classes: {id: {verdict?, reasoning}}, clusters: {id: {verdict?, reasoning}}}`), grounded in `tmp/docket-data.json` plus per-class verdict tallies computed from the verdicts file:
   - Preamble: the sitting order (import carried verdicts, import the echo fill, settle the disagreeing groups, rule the bulk candidates, then the clusters), which classes stay deferred per WHATNEXT.md, and any manifest/staleness caveats.
   - Ruled classes (the docket's "already ruled in the ledger" section): recommend the verdict the ledger status implies — approve for `intended`/`reviewed-approved`, reject for `reviewed-rejected` — citing the status and the class's verdict tally. A class with a genuinely split history gets reasoning only, no verdict.
   - Clusters: recommend for the large ones (say, size ≥ 8) outside ruled and deferred classes. Cite the class tally, describe the delta from the exemplar's summary, note when a single echo group means one in-app click fills the whole cluster, and flag that no verdicted unit shares the delta when the docket's evidence section is empty.
5. **Bake and hand over.** Re-run the docket tool (it folds the recommendations in), then give the user the headline numbers and `http://localhost:7294/docket.html` (start the server if it isn't running). Point out `verdicts-echo-fill.json` and the import order.
6. **On request, emit bulk proposals.** When the user blesses a class or cluster ("bless ss10", "cluster c-cbb49c41 approve, note …"), write one fresh import file per decision (e.g. `verdicts-bulk-<slug>.json`) covering exactly the blank members from `tmp/docket-data.json`, with the user's note verbatim plus a provenance tag like `[bulk: <class-or-cluster> — docket <manifest stamp>]`, `at` set to the manifest stamp, and report the count for the user to spot-check after import.

## Scratch discipline

`docket.html` lives in build output and `tmp/docket-*.json` are scratch — they die with the batch and are never committed. Durable outcomes land where they already live: verdicts in the imported verdict files, rationale in ledger/rune `why:` fields written by the user, and follow-ups in WHATNEXT.md.
