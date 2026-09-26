# Rebuild tools

Scripts for the M1 rebuild. Run each from the repo root as `uv run python -m rebuild.tools.<script>`. A script whose docstring gives the `uv run python rebuild/tools/<script>.py` form also runs that way.

Each script's module docstring says what it does and how to run it.

Start here:

- `artifact_cycle.py` (`make artifact-cycle`; `make review-cycle` runs it and then serves the corpus): the commit-time artifact cycle
- `verdict_ready.py` (`make verdict-ready`): the checklist that says whether the corpus is ready for a review session
- `review_docket.py`: writes the docket data for a review session; the live view is `#view=docket`
- `standing_probe.py`: explains, without writing anything, why a unit is still in the queue under the standing approvals
- `probe.py`: compares the old-font baseline with the new settlement for one or more codepoint windows, in every configuration
- `cycle_timings.py` (`make cycle-timings`): summarizes the recorded step timings and check results
- `calibrate_budgets.py` (`make job-costs`): checks the checked-in per-worker memory peaks against the peaks recorded in the timings journal
- `deep_sweep.py` (`make conform-deep`): gate:conform run to a longer horizon on demand

`console.py` is a library, not a script. It defines the line protocol every in-house child of the cycle prints (the `[t]`, `[phase]`, `[progress]`, and `[warn]` prefixes) and `CycleConsole`, the terminal renderer the artifact cycle shows that output with: the plan block, the per-step banners, the per-step logs under `var/build-logs/`, and the closing table. Read it before adding output to anything the cycle spawns.
