# Rebuild evidence

Data artifacts kept in-repo because a live decision or a running tool still needs them. Closed-round triage dumps used to live here too; they were deleted once their conclusions had landed in the runes and ledgers, since git preserves them (see `AGENTS.md`, “Note-taking and the rebuild logs”).

- `surfaces/` — whole-surface archives of `review-baseline` and `review-preview-bd1`, the two surfaces prior verdicts were recorded on. `rebuild/tools/carry_verdicts.py` auto-extracts them into `tmp/` when needed; to extract by hand: `tar -xzf rebuild/evidence/surfaces/<name>.tar.gz -C tmp/`.
