# Rebuild tools

Scripts for the M1 rebuild. All run from the repo root (some import `rebuild.pipeline`, so use `PYTHONPATH=. uv run python rebuild/tools/<script>.py` where the docstring says so).

Each script's module docstring is the authority on what it does and how to run it — read the top of the file. No table here restates them: a second copy nothing checks can only drift out of agreement.

Start here:

- `artifact_cycle.py` (`make artifact-cycle`; `make review-cycle` is the hands-off loop) — the commit-time artifact cycle
- `verdict_ready.py` (`make verdict-ready`) — the sitting-readiness checklist
- `review_docket.py` — bakes the docket data for a review sitting; the live view is `#view=docket`
- `standing_probe.py` — read-only explainer for why a unit still queues under the standing approvals
- `probe.py` — probe one codepoint window: old-font baseline vs new settlement, all configs
- `cycle_timings.py` (`make cycle-timings`, `make job-costs`) — summarize recorded step timings and check verdicts
- `deep_sweep.py` (`make conform-deep`) — the periodic deep form of gate:conform

One file here is a library rather than a script: `console.py` defines the four-line protocol every in-house child prints for the cycle to read back (`[t]`, `[phase]`, `[progress]`, `[warn]`) and the digest the artifact cycle renders it with — the plan block, the per-step banners, the per-step logs under `var/build-logs/`, and the closing table. Read it before adding a line of output to anything the cycle spawns.
