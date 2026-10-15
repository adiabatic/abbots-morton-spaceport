.PHONY: all test test-rebuild test-rebuild-slow test-slowly test-leaks leak-snapshot typecheck print-job serve explainer check-html-before check-html-after build-kerning-context-pairs review test-and-review review-build review-serve review-cycle artifact-cycle verdict-ready cycle-timings job-costs complaint-list novelty-order kernel-build kernel-check kernel-gate conform-deep replay-deep standing-daemon standing-daemon-stop prettier woff2 clean

all:
	uv run python tools/build_font.py glyph_data/ site/
	cp reference/DepartureMono-Regular.otf site/
	cd site && typst compile --font-path . print.typ

check-html-after: all
	uv run python tools/build_check_html.py

build-kerning-context-pairs: all
	uv run python tools/build_kerning_context_pairs.py

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

# Runs the font suite through rebuild/tools/make_test_gate.py, and pyright with it; each skips when nothing it reads has changed since its last green run (the wrapper's module docstring, rebuild/tools/pyright_gate.py). FORCE=1 runs both regardless, and the `typecheck` target runs pyright alone.
test:
	AMS_RUN_PYRIGHT=$(if $(FORCE),force,1) uv run python -m rebuild.tools.make_test_gate $(if $(FORCE),--force)

# Runs the rebuild suite through rebuild/tools/rebuild_gate.py, with pyright beside it, which is what type-checks rebuild/; the wrapper's module docstring and rebuild/tools/pyright_gate.py say what each skips. FORCE=1 runs both regardless.
test-rebuild:
	AMS_RUN_PYRIGHT=$(if $(FORCE),force,1) uv run python -m rebuild.tools.rebuild_gate $(if $(FORCE),--force)

# The rebuild suite's slow-marked tests, which the gate excludes, at the width the root conftest's `-n auto` hook resolves. The `-m slow` marker is the selection, so there is no lane flag. Every worker collects the whole rebuild suite before the marker deselects nearly all of it, so a worker the selection cannot fill costs one collection, which `collect_ignore` in rebuild/conftest.py keeps short.
test-rebuild-slow:
	uv run pytest rebuild/ -m slow -n auto --dist worksteal

# Runs the font suite on one L2 cluster of the slowest performance level, leaving the other cores free. `taskpolicy -b` runs the process tree at background priority, which the scheduler (not the documented `taskpolicy` contract) confines to one L2 cluster of the slowest performance level. `hw.perflevel0` is the fastest level and each later one is slower, so the slowest is the last, `hw.nperflevels - 1`. On a chip with efficiency cores that level is the efficiency cores; when the level spans several clusters, the tree gets one of them, and the scheduler may move the whole tree to another during a run. So the width is that level's `logicalcpu` divided by its cluster count, which is its `cpusperl2` when the level is made of whole clusters and never more than its `logicalcpu`. The recipe reads no level name and no fixed level index, so the same line holds on chips with two or three performance levels. `-n auto` would return every core the process may run on, because on Darwin it cannot see that confinement, and would oversubscribe the cluster. Memory does not limit this width, so don't derive it from a memory budget.
test-slowly:
	AMS_RUN_PYRIGHT=1 taskpolicy -b uv run pytest test/ site/ -n $$(l=$$(( $$(sysctl -n hw.nperflevels) - 1 )); c=$$(sysctl -n hw.perflevel$$l.logicalcpu); k=$$(sysctl -n hw.perflevel$$l.cpusperl2); echo $$(( c / ((c + k - 1) / k) ))) --dist worksteal

# Deep (≈1 min) isolation-leak gate: no new bad leak at depth 4 outside site/bad-leak-backlog.txt, plus the benign list (site/benign-leak-list.txt).
test-leaks: all
	uv run pytest test/test_isolation_leaks.py -m slow

# Re-bless the bad-leak backlog and benign list after an intended change (then review the diff).
leak-snapshot: all
	uv run python tools/leak_snapshot.py

review:
	uv run python tools/review_scoped_anchor_selectors.py --output site/scoped-anchor-review/index.html

# Runs both targets in parallel. `-j2` is the number of targets on the line, so it does not depend on the machine. Each target sizes its own pool to the whole machine: `make test` through the root conftest's `-n auto` hook, and the review tool at the usable cores capped at its count of families with suggestions. While both run, their pools oversubscribe the cores, and no memory budget covers that overlap.
test-and-review:
	@$(MAKE) -j2 test review

print-job: all
	lp site/print.pdf

explainer:
	cd doc/explainer && typst compile main.typ

serve:
	uv run python tools/serve.py

# Regenerate the §11 review corpus under rebuild/out/review/ (`review` is taken by the scoped-anchor-selector review above). It refuses while the review server listens, because it writes the served corpus in place; `make artifact-cycle` builds beside the served corpus and lands it while the server keeps running.
review-build:
	uv run python -m rebuild.review.build

review-serve:
	uv run python -m rebuild.review.serve

# Runs the commit-time artifact cycle: run_m1, the corpus rebuild, the verdict update (carry, merge into the autosave, the duplicate and standing fills, the complaint list), the review-facts pin refresh, and the gates. Bare `make artifact-cycle` picks the verdicts master to carry by itself; pass flags through ARGS, e.g. make artifact-cycle ARGS='--verdicts verdicts-X.json'. Each heavy stage skips itself when its green record shows its inputs unchanged since its last green run: run_m1, the corpus rebuild, gate:conform, the rebuild suite (gate:rebuild-contracts, with its own closure and record), and gate:make-test. A cycle after verdict-only changes therefore takes seconds. ARGS='--fresh' runs everything; ARGS='--force-make-test' forces only gate:make-test. The review-facts pin refresh runs on every pass except a staging pass: it copies the build's review-facts sidecar into rebuild/review-facts-pins.json in milliseconds and prints that file's git diff, and committing the diff accepts the review facts. This target only verifies; `make review-cycle` runs the same pass and then serves the corpus, for the edit-and-review loop. The terminal shows the plan, then a numbered banner per step with its description and surfaced child lines, then the summary table. The full output is kept under var/build-logs/<stamp>-<sha>/ (var/build-logs/latest is the newest run): plan.txt, terminal.log as a byte copy of the terminal, and one log per spawned step.
artifact-cycle:
	uv run python rebuild/tools/artifact_cycle.py $(ARGS)

# Runs the artifact cycle, then serves the corpus. The cycle's land puts the carried and filled verdicts into the autosave with the new corpus, so no browser import is needed. A failed cycle starts no server.
# A review server started from this checkout keeps running through every pass: the pass builds beside the served corpus and lands the corpus and the store together, and the open tabs move onto them by themselves. The cycle restarts a server running older code on the working tree's code. --stop-server matters only for a server from before the land protocol, which the cycle stops once when the pass writes under it. The serve step below binds the port only when nothing already holds it. When a server for another checkout holds it, the recipe names that checkout (`python -m rebuild.tools.review_server`) instead of saying the server kept running, and exits non-zero unless SERVE=0, because this checkout's corpus is not served.
# SERVE=0 runs the same cycle and prints the start command instead of serving when no server is up, so the target exits. Non-interactive callers need this: served in the foreground, the recipe never exits and the caller never sees the cycle summary as a finished command. A server that was already up is still up. Either way, var/build-logs/latest/ holds the plan, a byte copy of the terminal, and one log per step, so a caller that did not watch the terminal can read the run afterward.
# SERVE=bg exits and leaves the server running. The server module has no daemon flag, so the recipe detaches it with nohup (log in tmp/review-serve.log), and it outlives the shell that started it, including an agent harness's shell, which is killed after each command. The recipe waits up to 30 s for the port to answer before it returns, so the server row of the readiness checklist, which the cycle leaves to this recipe, is already true when it returns. Stop it the way the cycle does, with pkill -f 'rebuild\.review\.serve'.
review-cycle:
	uv run python rebuild/tools/artifact_cycle.py --stop-server $(ARGS)
	@if lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; then \
		other=$$(uv run python -m rebuild.tools.review_server) || exit 1; \
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

# Holds the review corpus in one process for the standing probe and the standing dry run. `serve` loads the corpus, its font pair, and the human units once, then answers probe and dry-run requests over var/standing-daemon.sock with byte-for-byte the output the in-process tools print. rebuild/tools/standing_daemon.py documents what it holds, when it declines a request, and when it exits by itself (when the corpus manifest, the fonts, the loaded code, or uv.lock changes). The tool serves in the foreground, so this recipe detaches it with nohup (log in var/standing-daemon.log) and waits until its `status` subcommand answers or the process dies. No cycle width accounts for the memory it holds, so stop it before a gate or cycle pass with `make standing-daemon-stop` or SIGTERM. Either removes the socket.
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

# Answers whether the corpus is ready to verdict: corpus freshness, gate status, verdict-store alignment, the server, and blanks left. Exits 0 when ready. A green artifact cycle that is not a staging pass ends by printing the same checklist (without the server row under `make review-cycle`), so this target is for asking the question on its own.
verdict-ready:
	uv run python -m rebuild.tools.verdict_ready $(ARGS)

# Reports what the cycle's steps and the checks cost on this machine and how the checks came out, from the journal rebuild/tools/cycle_timings.py describes; ARGS takes that tool's flags, and ARGS=--help lists them.
cycle-timings:
	uv run python -m rebuild.tools.cycle_timings $(ARGS)

# Checks whether the checked-in per-unit memory peaks still hold on this machine, printing each constant beside the peak measured here and the width it implies (rebuild/tools/calibrate_budgets.py); ARGS takes that tool's flags, and ARGS=--help lists them.
job-costs:
	uv run python -m rebuild.tools.calibrate_budgets $(ARGS)

# Clusters the open complaints (reject and neither verdicts) by the rune records that decided them, lists defer candidates among the still-blank lookalikes, and writes tmp/complaints-data.json. Reads the live autosave unless ARGS names a verdicts file. ARGS='--defer g-XXXXXXXX' writes a verdicts-deferred-*.json for the app's Import dialog.
complaint-list:
	uv run python rebuild/tools/complaint_list.py $(ARGS)

# Orders the blank queue for novelty and prints the worklist URL to paste into the review app. It takes one representative per duplicate group and picks each next unit to differ most from the last few shown, by class, families, letters, stances, junctions, configs, and provenance. Reads the live autosave unless ARGS names a verdicts file. Emits the first 40 entries by default; ARGS='--limit 0' emits the whole queue.
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

# The deep form of gate:conform: the font-vs-settle sweep at maximum length 5 or more, run by hand or overnight. ARGS='--max-length 6' goes deeper, ARGS='--status' asks whether it is due, ARGS='--targeted' shapes instead the texts the settle:bk1-la4 rules give (ARGS='--targeted --status' asks whether that is due), and ARGS='--jobs N' states a width, for debugging. rebuild/tools/deep_sweep.py's module docstring says which texts it shapes, which records it keeps and refreshes, and how it sizes its pool.
conform-deep:
	uv run python -m rebuild.tools.deep_sweep $(ARGS)

# The cheaper form of the deep sweep: the crate's string replay at one letter past the build's maximum length, over the texts that name the runes whose content changed since the last recorded walk. ARGS='--families qsPea,qsTea' names the runes, ARGS='--all' walks every text, ARGS='--max-length 6' goes deeper, ARGS='--status' asks whether it is due, and ARGS='--threads N' states how many units walk at once. rebuild/tools/deep_replay.py's module docstring says which texts a walk covers, what its record holds, and why it is not a cycle gate.
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
