# Rebuild tools

Scripts for the M1 rebuild. Run each from the repo root as `uv run python -m rebuild.tools.<script>`. A script whose docstring gives the `uv run python rebuild/tools/<script>.py` form also runs that way.

Each script's module docstring says what it does and how to run it.

Start here:

- `artifact_cycle.py` (`make artifact-cycle`; `make review-cycle` runs it and then serves the corpus): the commit-time artifact cycle
- `verdict_ready.py` (`make verdict-ready`): the checklist that says whether the corpus is ready for a review session
- `probe.py`: prints the rebuild's settled cells and junctions for one or more codepoint windows, in every configuration
- `cycle_timings.py` (`make cycle-timings`): summarizes the recorded step timings and check results
- `calibrate_budgets.py` (`make job-costs`): checks the checked-in per-worker memory peaks against the peaks recorded in the timings journal
- `deep_sweep.py` (`make conform-deep`): gate:conform run to a longer maximum length on demand
- `memory_series.py` (`make memory-series`): measures what the table build or the review corpus build holds and writes at each of several alphabet cuts, one build at a time, into `rebuild/memory-series.txt`
- `build_fonts.py` (`make all`): publishes the six fonts into `site/`, Mono and Junior built from the runes and Senior copied from the build under `rebuild/out/m1/`, which `--build` runs first when it is stale; the font suite's session runs it without `--build` before it checks the fonts
- `fault_kit.py`: re-injects the fault kit's rows (`rebuild/perf/faults.yaml`) into its own tree, `var/perf/kit/`, each on its own small alphabet, and checks that their gates still catch them (`doc/perf-check.md`)

`console.py` is a library, not a script. It defines the line protocol every in-house child of the cycle prints (the `[t]`, `[phase]`, `[progress]`, and `[warn]` prefixes) and `CycleConsole`, the terminal renderer the artifact cycle shows that output with: the plan block, the per-step banners, the per-step logs under `var/build-logs/`, and the closing table. Read it before adding output to anything the cycle spawns.

`suite_fonts.py` is a library too, one the root conftest can import: it names the fonts the font suite checks the `data-expect` pins against, as `build_fonts.py` publishes them, and the glyph-facts sidecar beside the build.

`store_guards.py` is a library as well: the pass lock, the review-server check and the refusals the table-diff accept (`rebuild/review/accept.py`), a hand-run writer of the verdict store, takes.
