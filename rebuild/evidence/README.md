# Rebuild evidence

Data artifacts kept in-repo because a live decision or a running tool still needs them. Closed-round triage dumps used to live here too; they were deleted once their conclusions had landed in the runes and ledgers, since git preserves them (see `AGENTS.md`, “Note-taking and the rebuild logs”).

- `lever-hunt-wf.js` + `wf2-result.json` — the round-2 exhaustive lever hunt (workflow and result) proving no closed-vocabulary policy record honors the ·May extension rejects. Kept as the proof pile for the still-open round-2 fork (see `WHATNEXT.md`); its internal `tmp/` paths describe the original run.
- `surfaces/` — whole-surface archives of `review-baseline` and `review-preview-bd1`, the two surfaces prior verdicts were recorded on. `rebuild/tools/carry_verdicts.py` auto-extracts them into `tmp/` when needed; to extract by hand: `tar -xzf rebuild/evidence/surfaces/<name>.tar.gz -C tmp/`.
