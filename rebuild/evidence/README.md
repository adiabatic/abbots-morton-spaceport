# Rebuild evidence

Data artifacts kept in-repo because a live decision or a running tool still needs them. Closed-round triage dumps used to live here too; they were deleted once their conclusions had landed in the runes and ledgers, since git preserves them (see `AGENTS.md`, “Note-taking and the rebuild logs”).

- `verdicts-carried-<short hash>.json` — the carried verdict master: the latest resolution of every recorded human verdict, keyed by `rebuild/tools/carry_verdicts.py` to the review surface built at commit `<short hash>`. This is the sole surviving copy of the human decisions (the per-sitting exports it was carried from were scratch and are gone), which is why it is checked in while every other verdicts file stays gitignored. After each artifact cycle, replace it with the fresh carried master the cycle writes at the repo root, renaming to the new short hash.
