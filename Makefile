.PHONY: all test test-engine test-engine-slow test-slowly typecheck print-job serve review-build review-serve review-cycle artifact-cycle verdict-ready review-accept review-snapshot-remake cycle-timings job-costs kernel-build kernel-check kernel-gate kernel-proofs conform-deep replay-deep fault-kit memory-series prettier draft-release woff2 clean

# Publishes the six shipped fonts into site/ under their shipped file names (engine/tools/build_fonts.py, whose module docstring says what each font reads), then copies Departure Mono beside them and typesets site/print.pdf. Mono and Junior, Regular and Bold, build from the rune files and the glyph files in a few seconds, and Senior is the copy of run_build's build under engine/out/build/, so with that build current the target takes a few seconds. When the build does not cover the inputs on disk, `--build` runs it first (`uv run python -m engine.pipeline.run_build`) and publishes only once it is green. On the M5 Pro MacBook Pro (doc/fleet.md) that build takes about 2 minutes when its table-reuse record lets it reuse the tables on disk, as after an edit to a Latin glyph, about 5 minutes when it reuses them but a pipeline code edit makes its string replay walk every text, and about 4 to 6 minutes when it builds the tables, as after a rune edit; `make cycle-timings ARGS='--by-step'` reports what run_build costs on each machine.
all:
	uv run python -m engine.tools.build_fonts --build
	cmp -s reference/DepartureMono-Regular.otf site/DepartureMono-Regular.otf || cp reference/DepartureMono-Regular.otf site/
	cd site && typst compile --font-path . print.typ

typecheck:
	uv run pyright

prettier:
	uv run black -q .

# Runs the font suite through engine/tools/make_test_gate.py, and pyright with it; each skips when nothing it reads has changed since its last green run (the wrapper's module docstring, engine/tools/pyright_gate.py). FORCE=1 runs both regardless, and the `typecheck` target runs pyright alone. The suite's session first publishes the six fonts into site/ through the command `all` runs, without `--build`, and checks them there: the data-expect pins, the any-of regression checks and the engine-neutral tests. It never starts the Senior's build, so it fails, naming the build command, while the build's Senior is stale.
test:
	AMS_RUN_PYRIGHT=$(if $(FORCE),force,1) uv run python -m engine.tools.make_test_gate $(if $(FORCE),--force)

# Runs the engine suite through engine/tools/engine_gate.py, with pyright beside it, which is what type-checks engine/; the wrapper's module docstring and engine/tools/pyright_gate.py say what each skips. FORCE=1 runs both regardless.
test-engine:
	AMS_RUN_PYRIGHT=$(if $(FORCE),force,1) uv run python -m engine.tools.engine_gate $(if $(FORCE),--force)

# The engine suite's slow-marked tests, which the gate excludes, at the width the root conftest's `-n auto` hook resolves. The `-m slow` marker is the selection, so there is no lane flag. Every worker collects the whole engine suite before the marker deselects nearly all of it, so a worker the selection cannot fill costs one collection, which `collect_ignore` in engine/conftest.py keeps short.
test-engine-slow:
	uv run pytest engine/ -m slow -n auto --dist worksteal

# Runs the font suite on one L2 cluster of the slowest performance level, leaving the other cores free. `taskpolicy -b` runs the process tree at background priority, which the scheduler (not the documented `taskpolicy` contract) confines to one L2 cluster of the slowest performance level. `hw.perflevel0` is the fastest level and each later one is slower, so the slowest is the last, `hw.nperflevels - 1`. On a chip with efficiency cores that level is the efficiency cores; when the level spans several clusters, the tree gets one of them, and the scheduler may move the whole tree to another during a run. So the width is that level's `logicalcpu` divided by its cluster count, which is its `cpusperl2` when the level is made of whole clusters and never more than its `logicalcpu`. The recipe reads no level name and no fixed level index, so the same line holds on chips with two or three performance levels. `-n auto` would return every core the process may run on, because on Darwin it cannot see that confinement, and would oversubscribe the cluster. Memory does not limit this width, so don't derive it from a memory budget.
test-slowly:
	AMS_RUN_PYRIGHT=1 taskpolicy -b uv run pytest test/ site/ -n $$(l=$$(( $$(sysctl -n hw.nperflevels) - 1 )); c=$$(sysctl -n hw.perflevel$$l.logicalcpu); k=$$(sysctl -n hw.perflevel$$l.cpusperl2); echo $$(( c / ((c + k - 1) / k) ))) --dist worksteal

print-job: all
	lp site/print.pdf

serve:
	uv run python tools/serve.py

# Regenerate the table-diff review corpus under engine/out/review/, the accepted snapshot against engine/out/build. It refuses while the review server listens, because it writes the served corpus in place; `make artifact-cycle` builds beside the served corpus and lands it while the server keeps running.
review-build:
	uv run python -m engine.review.build

review-serve:
	uv run python -m engine.review.serve

# Runs the commit-time artifact cycle: run_build, the table-diff corpus build (the accepted snapshot that engine/review-snapshot.json names against engine/out/build), its land, which carries the verdict store onto the new corpus by unit id, and the gates; a green pass ends with a `review diff` line counting the unaccepted changes and the readiness checklist, and unaccepted changes leave it green and READY. Pass flags through ARGS, e.g. make artifact-cycle ARGS='--fresh'. Each heavy stage skips itself when its green record shows its inputs unchanged since its last green run: run_build, the corpus rebuild, gate:conform, the engine suite (gate:engine-contracts, with its own closure and record), and gate:make-test. A cycle over unchanged inputs therefore takes seconds. ARGS='--fresh' runs everything; ARGS='--force-make-test' forces only gate:make-test. `make review-accept ARGS=--apply` accepts the changes once the review app's Queue has a verdict for each. This target only verifies; `make review-cycle` runs the same pass and then serves the corpus, for the edit-and-review loop. The terminal shows the plan, then a numbered banner per step with its description and surfaced child lines, then the summary table. The full output is kept under var/build-logs/<stamp>-<sha>/ (var/build-logs/latest is the newest run): plan.txt, terminal.log as a byte copy of the terminal, and one log per spawned step.
artifact-cycle:
	uv run python engine/tools/artifact_cycle.py $(ARGS)

# Runs the artifact cycle, then serves the corpus. The cycle's land carries the verdict store onto the new corpus by unit id, so no browser import is needed. A failed cycle starts no server.
# A review server started from this checkout keeps running through every pass: the pass builds beside the served corpus and lands the corpus and the store together, and the open tabs move onto them by themselves. The cycle restarts a server running older code on the working tree's code. --recipe-serves tells the cycle that this recipe serves the corpus after the pass, so the readiness checklist the cycle prints leaves the server row to the lines below. The serve step below binds the port only when nothing already holds it. When a server for another checkout holds it, the recipe names that checkout (`python -m engine.tools.review_server`) instead of saying the server kept running, and exits non-zero unless SERVE=0, because this checkout's corpus is not served.
# SERVE=0 runs the same cycle and prints the start command instead of serving when no server is up, so the target exits. Non-interactive callers need this: served in the foreground, the recipe never exits and the caller never sees the cycle summary as a finished command. A server that was already up is still up. Either way, var/build-logs/latest/ holds the plan, a byte copy of the terminal, and one log per step, so a caller that did not watch the terminal can read the run afterward.
# SERVE=bg exits and leaves the server running. The server module has no daemon flag, so the recipe detaches it with nohup (log in tmp/review-serve.log), and it outlives the shell that started it, including an agent harness's shell, which is killed after each command. The recipe waits up to 30 s for the port to answer before it returns, so the server row of the readiness checklist, which the cycle leaves to this recipe, is already true when it returns. Stop it the way the cycle does, with pkill -f 'engine\.review\.serve'.
review-cycle:
	uv run python engine/tools/artifact_cycle.py --recipe-serves $(ARGS)
	@if lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; then \
		other=$$(uv run python -m engine.tools.review_server) || exit 1; \
		if [ -n "$$other" ]; then \
			printf '\nA review server for another checkout (%s) holds port 7294, so this checkout'\''s corpus is not served. Stop that server, then:\n    make review-serve\n' "$$other"; \
			[ "$(SERVE)" = "0" ] || exit 1; \
		else \
			printf '\nThe review server kept running through this pass, so the open review tab kept working.\n'; \
		fi; \
	elif [ "$(SERVE)" = "0" ]; then \
		printf '\nThe review server was left stopped (SERVE=0). To look at the letters:\n    make review-serve\n'; \
	elif [ "$(SERVE)" = "bg" ]; then \
		mkdir -p tmp; \
		nohup uv run python -m engine.review.serve < /dev/null > tmp/review-serve.log 2>&1 & \
		waited=0; \
		while [ $$waited -lt 30 ] && ! lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; do \
			sleep 1; \
			waited=$$((waited + 1)); \
		done; \
		if lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; then \
			printf '\nThe review server is up in the background on http://localhost:7294/ (log: tmp/review-serve.log).\nTo stop it:\n    pkill -f '\''engine\\.review\\.serve'\''\n'; \
		else \
			printf '\nThe review server did not answer on port 7294 within 30s. See tmp/review-serve.log.\n'; \
			exit 1; \
		fi; \
	else \
		uv run python -m engine.review.serve; \
	fi

# Answers whether the corpus is ready to verdict: corpus freshness, gate status, the snapshot the pointer names, verdict-store alignment, blanks left, the unaccepted changes since the snapshot (a row that never fails), and the server. Exits 0 when ready. A green artifact cycle that is not a staging pass ends by printing the same checklist (without the server row under `make review-cycle`), so this target is for asking the question on its own.
verdict-ready:
	uv run python -m engine.tools.verdict_ready $(ARGS)

# Accepts a table-diff corpus's changes: takes a snapshot of engine/out/build under var/keep/review-snapshot/, points the tracked engine/review-snapshot.json at it, which goes into the commit with the edit it accepts, empties the verdict store, keeping the store as found under var/keep/review-accept/, and tells the open review tabs to reload onto the empty store. It runs beside a listening review server. Before the snapshot it runs the four-letter sweep (engine/review/accept_sweep.py): every text of at most four letters around a letter whose rules changed, split across processes while it holds the pass lock, about a minute and a half for a one-join edit on the M5 Pro MacBook Pro in Low Power Mode and nothing when no letter's rules changed, writing what it finds to var/review/accept-sweep.json, which the next accept over the same build reads instead. Bare `make review-accept` is a dry run that runs the sweep, prints what ARGS=--apply would write and every refusal, and writes nothing but the sweep's record. It refuses while `make verdict-ready`'s blocking checks fail, while a pass runs, while the sweep has found changes the corpus does not show (`make artifact-cycle` adds them for verdicts), and while a human unit is blank, skipped, rejected or neither; ARGS='--apply --retake' skips the sweep and takes the snapshot over those refusals, for a snapshot-format bump or a snapshot no build remakes. engine/review/accept.py's module docstring gives the checks and the order of the writes.
review-accept:
	uv run python -m engine.review.accept $(ARGS)

# Installs the snapshot engine/review-snapshot.json names on a machine without it, from a build of the commit that last changed the pointer: `git archive` of that commit into var/cycle/snapshot-remake/, that tree's own table build, glyph chain and snapshot, holding the pass lock; it runs the whole table build, so never beside a gate. ARGS takes --rev and --home; engine/review/accept.py's module docstring gives the rest.
review-snapshot-remake:
	uv run python -m engine.review.accept remake $(ARGS)

# Reports what the cycle's steps and the checks cost on this machine and how the checks came out, from the journal engine/tools/cycle_timings.py describes; ARGS takes that tool's flags, and ARGS=--help lists them.
cycle-timings:
	uv run python -m engine.tools.cycle_timings $(ARGS)

# Checks whether the checked-in per-unit memory peaks still hold on this machine, printing each constant beside the peak measured here and the width it implies (engine/tools/calibrate_budgets.py); ARGS takes that tool's flags, and ARGS=--help lists them.
job-costs:
	uv run python -m engine.tools.calibrate_budgets $(ARGS)

# Builds the Rust kernel (engine/kernel-rs) in release mode, with cargo's output on the terminal. It goes through `kernel_exec.cargo_build` so that it can run beside a suite or pass: `LOCK_PATH`'s comment in engine/pipeline/kernel_exec.py says what a build without the relink lock does to the kernels they start. The pipeline and the engine suite's spec-echo parity test both run the release binary, so there is no debug target.
kernel-build:
	uv run python -m engine.pipeline.kernel_exec

# The settlement engine's gate: `cargo fmt --check`, clippy with every warning an error, and the crate's test suite.
kernel-check:
	cargo fmt --check --manifest-path engine/kernel-rs/Cargo.toml
	cargo clippy --all-targets --manifest-path engine/kernel-rs/Cargo.toml -- -D warnings
	cargo test --manifest-path engine/kernel-rs/Cargo.toml

# Run this around any kernel-semantics change; no cycle gate runs the crate's own test suite. It runs the crate's fmt, clippy, and test gate and nothing else, and takes seconds once the crate is built. The spec-ingest parity check runs in the contracts lane instead: engine/test_kernel_io.py echoes the live dump through `spec-echo`. There is no settlement differential and no fixpoint byte comparison here, because there is no second implementation to compare against. Settlement is checked by the crate's own tests, by gate:conform's HarfBuzz shaping against a per-window re-settle keyed on the raw window, and by the build's witness stage. It takes no settings, so it reads no ARGS.
kernel-gate: kernel-check

# Checks the Lean proofs about the grouped fourth-slot sweep (engine/kernel-rs/proofs), failing on any warning, an unfinished proof among them. It is run by hand: no local gate runs it and no build needs Lean. It needs `elan` on the path, which fetches the Lean release that the directory's `lean-toolchain` pins. The directory's README.md says what is proved and what the model assumes.
kernel-proofs:
	cd engine/kernel-rs/proofs && lake build --wfail

# The deep form of gate:conform: the font-vs-settle sweep at maximum length 5 or more, run by hand or overnight. ARGS='--max-length 6' goes deeper, ARGS='--status' asks whether it is due, ARGS='--targeted' shapes instead the texts the settle:bk1-la4 rules give (ARGS='--targeted --status' asks whether that is due), and ARGS='--jobs N' states a width, for debugging. engine/tools/deep_sweep.py's module docstring says which texts it shapes, which records it keeps and refreshes, and how it sizes its pool.
conform-deep:
	uv run python -m engine.tools.deep_sweep $(ARGS)

# The cheaper form of the deep sweep: the crate's string replay at one letter past the build's maximum length, over the texts that name the runes whose content changed since the last recorded walk. ARGS='--families qsPea,qsTea' names the runes, ARGS='--all' walks every text, ARGS='--max-length 6' goes deeper, ARGS='--status' asks whether it is due, and ARGS='--threads N' states how many units walk at once. engine/tools/deep_replay.py's module docstring says which texts a walk covers, what its record holds, and why it is not a cycle gate.
replay-deep:
	uv run python -m engine.tools.deep_replay $(ARGS)

# Runs the fault kit (engine/tools/fault_kit.py) as a one-shot launchd job (engine/tools/job_launch.py), under its meter gate, following its log in tmp/fault-kit.log and exiting with its status; ARGS takes the kit's flags, and ARGS=--help lists them. The job runs the venv's interpreter, which the `uv run` that starts the launcher has synced, rather than `uv run`, so that the kit is the job's own process: the kit refuses unless the write meter's first reading is in the own basis, and under `uv run` the child that `caffeinate` forks to hold the job's sleep assertion would be uv's other child, outside the kit's tree. doc/perf-check.md § The fault kit describes the gate.
fault-kit:
	uv run python -m engine.tools.job_launch --kind fault-kit --log tmp/fault-kit.log -- .venv/bin/python -m engine.tools.fault_kit $(ARGS)

# Runs the memory series (engine/tools/memory_series.py) as a one-shot launchd job (engine/tools/job_launch.py), following its log in tmp/memory-series.log and exiting with its status; it measures the whole alphabet once for the holder it measures, writes that holder's line in var/keep/memory-series.txt, making the directory when it is missing, and keeps the other holder's. ARGS takes the series' flags: `--holder replay` measures the string replay with its settle-memo absorbs instead of the table build, and ARGS=--help lists the rest. The job runs the venv's interpreter rather than `uv run`, as fault-kit's does, so that the series is the job's own process and the write meter reads the line's writes in the own basis. A run reads the meter a minute before and five minutes after its measured build or replay, so the waits alone add six minutes.
memory-series:
	uv run python -m engine.tools.job_launch --kind memory-series --log tmp/memory-series.log -- .venv/bin/python -m engine.tools.memory_series $(ARGS)

# Uploads the six fonts in site/ and a manifest of them (fonts.json: the commit, the version, and each font's file name, size and sha256) to a draft GitHub release for the tag release/<version>, the version glyph_data/metadata.yaml gives; pushing that tag then runs .github/workflows/deploy.yml, which checks the fonts against the manifest, deploys the site and publishes the draft (HACKING.md § Cutting a release). Bare `make draft-release` is a dry run that prints every check, every refusal and what ARGS=--apply would do, and writes nothing. It refuses while the working tree is dirty or HEAD is on no branch of origin, while the fonts in site/ are not what `make all` publishes for the tree or carry another version, while FONTLOG.md has no `### <version>` section, and while a published release for the tag or the tag itself is on origin; a draft release for the tag is replaced. It never builds, tags or pushes: it prints the two commands that tag and push. ARGS=--rehearse is the rehearsal form (HACKING.md § Rehearsing the release path), which exercises the workflow's GitHub steps with no public effect: ARGS='--rehearse --apply' uploads the same six fonts and manifest, through the same calls, to a draft for the tag rehearsal/<version>-<commit>, which no push publishes, relaxing and printing the refusals that only protect a release (a published release for the version, the FONTLOG.md section, the version's tags), and prints the `gh workflow run` command that starts the workflow's rehearsal, which deploys nothing and publishes nothing; it never starts the run itself. ARGS='--rehearse --delete --apply' deletes the rehearsal drafts, each only once it reads back as a draft whose tag starts with rehearsal/. Without --apply, each form is a dry run. engine/tools/release_fonts.py's module docstring gives the checks.
draft-release:
	uv run python -m engine.tools.release_fonts $(ARGS)

# Compresses the built OTFs in site/ into WOFF2 files beside them. The compressions are independent, so they run in parallel, one per usable core. The count comes from `usable_cores`, which respects an affinity mask and a cgroup CPU quota that `getconf` and `sysctl` ignore. xargs's own `-P 0` would start as many processes as possible, with no limit.
woff2: all
	find site -maxdepth 1 -name '*.otf' -print0 | xargs -0 -n1 -P "$$(uv run python -c 'from engine.tools.memory_budget import usable_cores; print(usable_cores())')" woff2_compress

# Deletes what `make all` and `make woff2` publish into site/ (the six fonts, Junior's feature files, the Departure Mono copy, the WOFF2 files and print.pdf), the Python bytecode outside .uv-cache/ and .venv/, pytest's cache, and the .ruff_cache/ that a ruff run outside this project's tooling leaves. Leaves engine/out/ in place.
clean:
	find . -type d -name __pycache__ -not -path './.uv-cache/*' -not -path './.venv/*' -exec rm -rf {} +
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -not -path './.uv-cache/*' -not -path './.venv/*' -delete
	rm -rf .pytest_cache .ruff_cache
	rm -f site/AbbotsMortonSpaceport*.otf site/AbbotsMortonSpaceport*.fea site/DepartureMono-Regular.otf site/*.woff2 site/print.pdf
