---
name: review-docket
description: Prepare a review sitting on the rebuild review surface — run the echo prefill, bake the docket data, and emit bulk-proposal files for blessed classes or clusters. The adjudication queue itself is the app's live Docket view (#view=docket). Use when the user wants to prepare, resume, or speed up review-surface adjudication.
argument-hint: "[verdicts-file]"
---

Prepare a review sitting on the live review surface (`rebuild/out/review/`, served by `uv run python -m rebuild.review.serve` on port 7294). The human decides everything in the browser, where the fonts render; the app's **Docket view** (`http://localhost:7294/#view=docket`) is the queue — it clusters the blank units by the build-emitted `cluster` signature, largest decision first, live against the in-memory verdict store, so it recomputes as verdicts land. Your job is the surrounding leverage: prefill the unanimous echo groups, pin the docket data for bulk proposals, and multiply each blessing across its lookalikes.

## Hard rules

- Never write the app's live verdict store (`verdicts-autosave.json`) or edit any existing `verdicts-*.json`. Everything you produce is a fresh `ams-review-verdicts/1` file the user imports through the app's own import control.
- Every file you emit pins the surface's `manifest_generated_at`. Never join unit ids across manifests; if stamps mismatch, stop and say so (`carry_verdicts.py` is the remedy, not `force`).
- Bulk proposals must never beat a human verdict on import collision: the app keeps the strictly newer `at`, so stamp proposal records with the manifest's `generated_at` (old, non-winning), the way `echo_verdicts.py` does. Imports are not undoable — remind the user to keep the prior verdicts file as the corrective re-import path.
- Verdicts are evidence, not gospel. When proposing a bulk decision, state the grounds from the ledger and the user's own verdict history, admit when no verdicted unit shares the exact delta, and never bulk-propose against a split history.
- Never author `why:` text, and never promote a draft's `why_stub` into a rune. That rationale is the user's voice.

## Steps

1. **Check readiness and pick the frontier verdicts file.** Run `make verdict-ready` first — it verifies surface freshness (by input fingerprint, which unlike `repo_head` is honest about dirty-tree builds), gate greenness from the last recorded cycle, verdict-store alignment, and the server, and it names the frontier verdicts file: the `verdicts-*.json` at the repo root or under `rebuild/evidence/` whose `manifest_generated_at` matches `rebuild/out/review/manifest.json` with the most non-skip verdicts. Use the argument if given instead of the checker's pick. If the checker reports the surface stale or the gates unknown, stop and hand the user its remedy rather than preparing a sitting on a stale surface.
2. **Echo prefill.** `uv run python rebuild/tools/echo_verdicts.py <verdicts-file>` — report how many blanks the unanimous echo groups fill (`verdicts-echo-fill.json`) and how many groups disagree.
3. **Bake the docket data.** `uv run python rebuild/tools/review_docket.py <verdicts-file>` — writes `tmp/docket-data.json`: every blank cluster (id, class, configs, echo-group substructure, evidence from judged lookalikes), the ledger-ruled classes still holding blanks, and the disagreeing echo groups. The cluster ids match the app's Docket-view cards, and the bake exists to freeze exact blank membership against the verdicts file for step 5 — the app view itself needs no bake and never goes stale.
4. **Hand over.** Give the user the headline numbers (blank units, echo groups, clusters, ruled units, disagreeing groups) and `http://localhost:7294/#view=docket` (start the server if it isn't running). Point out `verdicts-echo-fill.json` and the import order: carried verdicts first, then the echo fill, then settle the disagreeing groups, then work the queue.
5. **On request, emit bulk proposals.** When the user blesses a class or cluster ("bless ss10", "cluster c-cbb49c41 approve, note …"), write one fresh import file per decision (e.g. `verdicts-bulk-<slug>.json`) covering exactly the blank members from `tmp/docket-data.json`, with the user's note verbatim plus a provenance tag like `[bulk: <class-or-cluster> — docket <manifest stamp>]`, `at` set to the manifest stamp, and report the count for the user to spot-check after import.

## Scratch discipline

`tmp/docket-data.json` is scratch — it dies with the batch and is never committed. Durable outcomes land where they already live: verdicts in the imported verdict files, rationale in ledger/rune `why:` fields written by the user, and follow-ups in WHATNEXT.md.
