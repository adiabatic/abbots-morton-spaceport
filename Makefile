.PHONY: all test test-rebuild test-rebuild-slow test-slowly test-leaks leak-snapshot typecheck print-job serve explainer check-html-before check-html-after build-kerning-hardcases review test-and-review review-build review-serve review-cycle artifact-cycle verdict-ready cycle-timings job-costs complaint-docket novelty-order kernel-build kernel-check kernel-gate conform-deep standing-daemon standing-daemon-stop prettier woff2 clean

all:
	uv run python tools/build_font.py glyph_data/ site/
	cp reference/DepartureMono-Regular.otf site/
	cd site && typst compile --font-path . print.typ

check-html-after: all
	uv run python tools/build_check_html.py

build-kerning-hardcases: all
	uv run python tools/build_kerning_hardcases.py

check-html-before: all
	mkdir -p site/before
	cp site/AbbotsMortonSpaceportMono-Regular.otf site/before/
	cp site/AbbotsMortonSpaceportMono-Bold.otf site/before/
	cp site/AbbotsMortonSpaceportSansJunior-Regular.otf site/before/
	cp site/AbbotsMortonSpaceportSansJunior-Bold.otf site/before/
	cp site/AbbotsMortonSpaceportSansSenior-Regular.otf site/before/
	cp site/AbbotsMortonSpaceportSansSenior-Bold.otf site/before/

typecheck:
	uv run pyright

prettier:
	uv run black -q .

# Runs the font suite through rebuild/tools/make_test_gate.py, which exits 0 in about a second when nothing the suite reads has changed since its last green run. `make_test_exempt` in rebuild/tools/artifact_cycle.py defines what lies outside the suite's closure. The Makefile enters the closure only as the output of `make -n all` and `make -n test`, so a comment here or a target the suite never runs does not re-arm the gate. The green record, rebuild/out/make-test-green.json, is shared with the artifact cycle's gate:make-test. FORCE=1 runs the suite regardless. AMS_RUN_PYRIGHT starts pyright in the root conftest's pytest_configure, beside the font build, and a pyright failure stops the run before the workers spawn. Pyright skips itself when no file it reads has changed since its last green run (rebuild/tools/pyright_gate.py, record rebuild/out/pyright-green.json); FORCE=1 runs it too. `[tool.pyright] include` in pyproject.toml decides which paths are checked, so every invocation is a bare `uv run pyright`. The `typecheck` target runs pyright alone. The pre-push hook runs only black.
test:
	AMS_RUN_PYRIGHT=$(if $(FORCE),force,1) uv run python -m rebuild.tools.make_test_gate $(if $(FORCE),--force)

# Runs the rebuild suite through rebuild/tools/rebuild_gate.py, which skips it when its input closure is unchanged since its last green run. The green record, rebuild/out/rebuild-contracts-green.json, is shared with the artifact cycle's gate:rebuild-contracts. The suite has one lane, contracts. No test under rebuild/ reads live build output (rebuild/conftest.py's audit guard fails one that does), so the suite runs on every usable core, its closure holds no build output, and a change to build artifacts alone does not re-arm it. The build checks its own artifacts, the rule certificates among them, in run_m1. The wrapper judges each run with the cycle's failure classifier, `classify_rebuild_output`, which counts every FAILED or ERROR line and every nonzero exit as red. FORCE=1 runs the whole suite regardless.
# AMS_RUN_PYRIGHT here is what type-checks rebuild/, because `make_test_exempt` exempts all of rebuild/ and a rebuild-only edit therefore never re-arms `make test`. The root conftest's pytest_configure recognizes a rebuild-only run and skips the font build unless the site fonts are missing (as after `make clean`), since this suite shapes against the fonts its closure fingerprint already hashed. With no build to overlap, pyright starts in that hook and is joined at session end, so it runs beside the suite. The cost is that a type error is reported only after the suite finishes. When the fonts are missing, the hook builds them and waits for pyright beside the build, as `make test` does. Pyright skips itself on its own green record (rebuild/tools/pyright_gate.py), so a run narrowed to a rune edit's tests spawns no type check; FORCE=1 forces it with the suite. A pyright failure exits pytest nonzero with no FAILED or ERROR lines, which `classify_rebuild_output` counts as a hard failure.
test-rebuild:
	AMS_RUN_PYRIGHT=$(if $(FORCE),force,1) uv run python -m rebuild.tools.rebuild_gate $(if $(FORCE),--force)

# The rebuild suite's slow-marked tests, which the gate excludes. The `-m slow` marker is the selection, so there is no lane flag. This is the one target that sets its width instead of using `-n auto`. Every worker collects the whole rebuild suite before the marker deselects nearly all of it, and neither `-n auto` hook can see how little the marker selects, so a worker slot the selection cannot fill costs a full collection and gains nothing. Two workers is sized for the current slow set, not for the machine. Re-measure and widen it when the slow set grows past what two workers finish.
test-rebuild-slow:
	uv run pytest rebuild/ -m slow -n 2 --dist worksteal

# Runs the font suite on the efficiency cores only, leaving the performance cores free. `taskpolicy -b` runs the process tree at background priority, which confines it to the efficiency cores, so the width is the efficiency-core count. `-n auto` would return every core the process may run on, because on Darwin it cannot see that confinement, and would oversubscribe the efficiency cores. Memory does not limit this width, so don't derive it from a memory budget.
test-slowly:
	AMS_RUN_PYRIGHT=1 taskpolicy -b uv run pytest test/ site/ -n $$(sysctl -n hw.perflevel1.logicalcpu) --dist worksteal

# Deep (≈1 min) isolation-leak gate: no new bad leak at depth 4 outside site/bad-leak-backlog.txt, plus the benign census (site/benign-leak-census.txt).
test-leaks: all
	uv run pytest test/test_isolation_leaks.py -m slow

# Re-bless the bad-leak backlog and benign census after an intended change (then review the diff).
leak-snapshot: all
	uv run python tools/leak_snapshot.py

review:
	uv run python tools/review_scoped_anchor_selectors.py --output site/scoped-anchor-review/index.html

# Runs both targets in parallel. `-j2` is the number of targets on the line, so it does not depend on the machine and needs no memory budget. Each target sets its own parallelism: `make test` through the root conftest's `-n auto` hook, the review tool through its `--jobs`.
test-and-review:
	@$(MAKE) -j2 test review

print-job: all
	lp site/print.pdf

explainer:
	cd doc/explainer && typst compile main.typ

serve:
	uv run python tools/serve.py

# Regenerate the §11 review surface under rebuild/out/review/ (`review` is taken by the scoped-anchor-selector review above).
review-build:
	uv run python -m rebuild.review.build

review-serve:
	uv run python -m rebuild.review.serve

# Runs the commit-time artifact cycle: run_m1, the surface rebuild, the verdict chain (carry, merge into the autosave, the echo and standing fills, the complaint docket), the census-pin refresh, and the gates. Bare `make artifact-cycle` picks the verdicts master to carry by itself; pass flags through ARGS, e.g. make artifact-cycle ARGS='--verdicts verdicts-X.json'. Each heavy stage skips itself when its green record shows its inputs unchanged since its last green run: run_m1, the surface rebuild, gate:conform, the rebuild suite (gate:rebuild-contracts, with its own closure and record), and gate:make-test. A cycle after verdict-only changes therefore takes seconds. ARGS='--fresh' runs everything; ARGS='--force-make-test' forces only gate:make-test. The census-pin refresh runs on every pass except a rehearsal: it copies the build's census sidecar into rebuild/review-census-pins.json in milliseconds and prints that file's git diff, and committing the diff accepts the census. This target only verifies; `make review-cycle` runs the same pass and then serves the surface, for the look-edit-look loop. The terminal shows a digest: the plan, then a numbered banner per step with its description and surfaced child lines, then the summary table. The full output is kept under var/build-logs/<stamp>-<sha>/ (var/build-logs/latest is the newest run): plan.txt, terminal.log as a byte copy of the terminal, and one log per spawned step.
artifact-cycle:
	uv run python rebuild/tools/artifact_cycle.py $(ARGS)

# Runs the artifact cycle, then serves the surface. The cycle's merge step writes the carried verdicts into the autosave, so no browser import is needed. A failed cycle stops before serving.
# --stop-server lets the cycle driver decide whether to stop the review server, because only the driver knows whether this pass writes under it. A pass that rebuilds the surface or changes the verdict store stops the server first. A pass that writes neither leaves it running, so the open review tab keeps working for the whole pass. In either case the serve step below binds the port only when nothing already holds it.
# SERVE=0 runs the same cycle and prints the restart command instead of serving, so the target exits. Non-interactive callers need this: served in the foreground, the recipe never exits and the caller never sees the cycle summary as a finished command. On a pass that wrote neither the surface nor the store, the server was never stopped and is still up. Either way, var/build-logs/latest/ holds the plan, a byte copy of the terminal, and one log per step, so a caller that did not watch the terminal can read the run afterward.
# SERVE=bg exits and leaves the server running. The server module has no daemon flag, so the recipe detaches it with nohup (log in tmp/review-serve.log), and it outlives the shell that started it, including an agent harness's shell, which is killed after each command. The recipe waits up to 30 s for the port to answer before it returns, so the server row of the readiness checklist, which the cycle leaves to this recipe, is already true when it returns. Stop it the way the cycle does, with pkill -f 'rebuild\.review\.serve'.
review-cycle:
	uv run python rebuild/tools/artifact_cycle.py --stop-server $(ARGS)
	@if lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; then \
		printf '\nThe review server stayed up through this pass — the letters were on screen for all of it.\n'; \
	elif [ "$(SERVE)" = "0" ]; then \
		printf '\nThe review server was left stopped (SERVE=0). To look at the letters:\n    make review-serve\n'; \
	elif [ "$(SERVE)" = "bg" ]; then \
		mkdir -p tmp; \
		nohup uv run python -m rebuild.review.serve < /dev/null > tmp/review-serve.log 2>&1 & \
		waited=0; \
		while [ $$waited -lt 30 ] && ! lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; do \
			sleep 1; \
			waited=$$((waited + 1)); \
		done; \
		if lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; then \
			printf '\nThe review server is up in the background on http://localhost:7294/ (log: tmp/review-serve.log).\nTo stop it:\n    pkill -f '\''rebuild\\.review\\.serve'\''\n'; \
		else \
			printf '\nThe review server did not answer on port 7294 within 30s. See tmp/review-serve.log.\n'; \
			exit 1; \
		fi; \
	else \
		uv run python -m rebuild.review.serve; \
	fi

# Holds the review surface in one process for the standing probe and the standing dry run. `serve` loads the surface, its font pair, and the human units once, then answers probe and dry-run requests over var/standing-daemon.sock with byte-for-byte the output the in-process tools print. rebuild/tools/standing_daemon.py documents what it holds, when it declines a request, and when it exits by itself (when the surface manifest, the fonts, the loaded code, or uv.lock changes). The tool serves in the foreground, so this recipe detaches it with nohup (log in var/standing-daemon.log) and waits until its `status` subcommand answers or the process dies. STANDING_DAEMON_BYTES estimates what it holds, but no cycle width subtracts it, so stop it before a gate or cycle pass with `make standing-daemon-stop` or SIGTERM. Either removes the socket.
standing-daemon:
	@mkdir -p var; \
	nohup uv run python rebuild/tools/standing_daemon.py serve < /dev/null > var/standing-daemon.log 2>&1 & \
	pid=$$!; waited=0; \
	while kill -0 $$pid 2>/dev/null && [ $$waited -lt 300 ] && ! uv run python rebuild/tools/standing_daemon.py status > /dev/null 2>&1; do \
		sleep 2; waited=$$((waited + 2)); \
	done; \
	if uv run python rebuild/tools/standing_daemon.py status; then \
		printf 'log: var/standing-daemon.log; stop it with make standing-daemon-stop\n'; \
	else \
		printf 'the standing daemon did not answer; see var/standing-daemon.log\n'; exit 1; \
	fi

standing-daemon-stop:
	uv run python rebuild/tools/standing_daemon.py stop

# Answers whether the surface is ready to verdict: surface freshness, gate status, verdict-store alignment, the server, and blanks left. Exits 0 when ready. A green artifact cycle that is not a rehearsal ends by printing the same checklist (without the server row under `make review-cycle`), so this target is for asking the question on its own.
verdict-ready:
	uv run python -m rebuild.tools.verdict_ready $(ARGS)

# Reports what the cycle spends its time on, on this machine. Every artifact cycle appends per-step wall times and peak RSS to rebuild/out/cycle-timings.ndjson, tagged with the host and carrying each child's inner [t] phase lines (the protocol rebuild/tools/console.py defines). The journal is append-only, gitignored with the rest of rebuild/out, and never pruned by retention, so each machine keeps its own history. The default view shows recent runs, steps slowest first. ARGS='--by-step' aggregates count, median, max, and latest seconds and the maximum recorded RSS per step and host; ARGS='--inner' expands the phase lines; ARGS='--journal <path>' reads a concatenation of journals from several machines. ARGS='--by-outcome' reports the check records: every judged check invocation, from a cycle or run by hand, writes its verdict to the journal, and this view shows per check the invocations, the green, red, and skipped counts, and a histogram of the test ids it has failed on. That lets a suite's cost be weighed against what it has caught.
cycle-timings:
	uv run python -m rebuild.tools.cycle_timings $(ARGS)

# Checks whether the checked-in per-worker memory peaks still hold on this machine. Several widths are the machine's memory divided by a measured per-unit peak, each a checked-in constant (FONT_SUITE_WORKER_BYTES in conftest.py among them; `UNITS` in rebuild/tools/calibrate_budgets.py lists them all). A peak that grows makes every width derived from it wrong without failing anything. This reads the cycle-timings journal (the pool records each xdist controller appends and the per-step peaks the cycle records), filters to this host, and prints for each unit the checked-in constant, the measured peak, and the width each implies here. ARGS='--check' exits nonzero when a measured peak exceeds its constant. Re-seeding the constant and committing it accepts the new value, as committing rebuild/review-census-pins.json accepts the census. The artifact cycle runs the --check form every pass. A record older than the commit that set a constant's current value (found with git blame on its line) is not held against it, so a re-seed clears its row on the next pass. ARGS='--host all' reads every machine in the journal; ARGS='--recent 0' drops both the recency bound and that commit bound.
job-costs:
	uv run python -m rebuild.tools.calibrate_budgets $(ARGS)

# Clusters the open complaints (reject and neither verdicts) by the rune records that decided them, lists park candidates among the still-blank lookalikes, and writes tmp/complaints-data.json. Reads the live autosave unless ARGS names a verdicts file. ARGS='--park g-XXXXXXXX' writes a verdicts-park-*.json for the app's Import dialog.
complaint-docket:
	uv run python rebuild/tools/complaint_docket.py $(ARGS)

# Orders the blank queue for novelty and prints the worklist URL to paste into the review app. It takes one rep per echo group and picks each next unit to differ most from the last few shown, by class, families, letters, stances, seams, configs, and provenance. Reads the live autosave unless ARGS names a verdicts file. Emits the first 40 entries by default; ARGS='--limit 0' emits the whole queue.
novelty-order:
	uv run python rebuild/tools/novelty_order.py $(ARGS)

# Builds the Rust M1 kernel (rebuild/kernel-rs) in release mode. The pipeline and the rebuild suite's spec-echo parity test both run the release binary, so there is no debug target.
kernel-build:
	cargo build --release --manifest-path rebuild/kernel-rs/Cargo.toml

# The settlement engine's gate: `cargo fmt --check`, clippy with every warning an error, and the crate's test suite.
kernel-check:
	cargo fmt --check --manifest-path rebuild/kernel-rs/Cargo.toml
	cargo clippy --all-targets --manifest-path rebuild/kernel-rs/Cargo.toml -- -D warnings
	cargo test --manifest-path rebuild/kernel-rs/Cargo.toml

# Run this around any kernel-semantics change; no cycle gate runs the crate's own test suite. It runs the crate's fmt, clippy, and test gate and nothing else, and takes seconds once the crate is built. The spec-ingest parity check runs in the contracts lane instead: rebuild/test_kernel_io.py echoes the live dump through `spec-echo`. There is no settlement differential and no fixpoint byte comparison here, because there is no second implementation to compare against. Settlement is checked by the crate's own tests, by gate:conform's HarfBuzz shaping against a per-window re-settle keyed on the raw window, and by the build's witness stage. It takes no settings, so it reads no ARGS.
kernel-gate: kernel-check

# The deep form of gate:conform: the exhaustive font-vs-settle sweep at horizon 5 or more (ARGS='--horizon 6' to go deeper, ARGS='--status' to ask whether it is armed). Run it by hand or overnight; the cycle never runs it. Its green record is keyed on the emitted lookup's behavior classes, the font-compilation code, and the uharfbuzz version, not on the runes, so a rune edit that adds no new rule shape leaves it current. A green deep run also refreshes gate:conform's green record, because a sweep at this depth covers every text the per-edit belt shapes. The artifact cycle prints armed or current each pass.
conform-deep:
	uv run python -m rebuild.tools.deep_sweep $(ARGS)

# The cheaper form of the deep sweep: the crate's string replay at one letter past the build's horizon, over the texts that name the runes whose content changed since the last recorded walk (ARGS='--families qsPea,qsTea' names them, ARGS='--all' walks the whole universe overnight, ARGS='--status' asks whether it is armed). Its green record is keyed on rune content, so a rune edit arms it, and the artifact cycle reports that each pass. It is not a cycle gate because of its cost, which rebuild/tools/deep_replay.py states. A green `make conform-deep` at this depth also refreshes it.
replay-deep:
	uv run python -m rebuild.tools.deep_replay $(ARGS)

# Compresses the built OTFs in site/ into WOFF2 files beside them. The compressions are independent, so they run in parallel, one per usable core. The count comes from `usable_cores`, which respects an affinity mask and a cgroup CPU quota that `getconf` and `sysctl` ignore. xargs's own `-P 0` would start as many processes as possible, with no limit.
woff2: all
	find site -maxdepth 1 -name '*.otf' -print0 | xargs -0 -n1 -P "$$(uv run python -c 'from rebuild.tools.memory_budget import usable_cores; print(usable_cores())')" woff2_compress

# Deletes the build output and the Python and tool caches. Leaves the .uv-cache/ and .venv/ caches in place.
clean:
	find . -type d -name __pycache__ -not -path './.uv-cache/*' -not -path './.venv/*' -exec rm -rf {} +
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -not -path './.uv-cache/*' -not -path './.venv/*' -delete
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist wheels *.egg-info
	rm -rf site/before site/scoped-anchor-review
	rm -f site/AbbotsMortonSpaceport*.otf site/AbbotsMortonSpaceport*.fea site/DepartureMono-Regular.otf site/*.woff2 site/print.pdf site/check.html
