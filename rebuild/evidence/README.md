# Rebuild evidence

Data artifacts the rebuild reports cite as proof or input — kept in-repo so the multi-month project's evidence trail survives `tmp/` cleanups. Each file's story lives in the report that cites it (`WHATNEXT.md`, `rebuild/POLICY-ROUND-*-REPORT.md`, `rebuild/M1-BATCH2-PROGRESS.md`, `rebuild/VERDICT-APPLICATION-PROGRESS.md`).

- `lever-hunt-wf.js` + `wf2-result.json` — the round-2 exhaustive lever hunt (workflow and result) proving no closed-vocabulary policy record honors the ·May extension rejects. Kept verbatim as evidence; its internal `tmp/` paths describe the original run.
- `review-triage.yaml` / `review-triage-round2.yaml` — the exported round-1/2 triage YAMLs.
- `round2-control-audit.tsv` — the pre-edit control divergence audit the lever hunt diffed against.
- `round1-verdict-id-mapping.json` — the mapping for interpreting round-1 verdicts against renumbered surfaces.
- `jitter-candidates.md` — the misclick-audit worklist (`rebuild/tools/jitter_audit.py` output).
- `findings/` — per-rune triage findings from the M1 batch-2 session.
- `unmatched_after_fixes.json` — the batch-2 unmatched dump after the 2026-06-13 fixes.
- `v-final2/` — the final lever-hunt candidate's diff and summary.
- `surfaces/` — whole-surface archives of `review-baseline` and `review-preview-bd1`, the two surfaces prior verdicts were recorded on. `rebuild/tools/carry_verdicts.py` auto-extracts them into `tmp/` when needed; to extract by hand: `tar -xzf rebuild/evidence/surfaces/<name>.tar.gz -C tmp/`.
