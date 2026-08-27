.PHONY: all test test-rebuild test-rebuild-slow test-slowly test-leaks leak-snapshot typecheck print-job serve explainer check-html-before check-html-after build-kerning-hardcases review test-and-review review-build review-serve review-cycle artifact-cycle verdict-ready cycle-timings complaint-docket novelty-order kernel-build kernel-check kernel-parity kernel-gate conform-deep prettier woff2 clean

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

# Self-skipping: the wrapper exits 0 in ≈a second when nothing the suite reads has changed since its last green run (the exempt trees are MAKE_TEST_EXEMPT_PREFIXES in rebuild/tools/artifact_cycle.py, plus Markdown; the green record at rebuild/out/make-test-green.json is shared with the artifact cycle's gate:make-test). FORCE=1 runs the suite regardless. The pyright gate runs inside pytest_configure (via AMS_RUN_PYRIGHT) so it overlaps the font build instead of preceding it serially; it still fast-fails before the workers spawn. Which paths get checked is `[tool.pyright] include` in pyproject.toml, not the argv here — every invocation is a bare `uv run pyright` so that list is the single authority. The `typecheck` target stays for standalone use; pre-commit runs black only.
test:
	AMS_RUN_PYRIGHT=1 uv run python -m rebuild.tools.make_test_gate $(if $(FORCE),--force)

# The rebuild suite's self-skipping wrapper, in two lanes with a green record each — rebuild/out/rebuild-contracts-green.json and rebuild/out/rebuild-validators-green.json, shared with the artifact cycle's gate:rebuild-contracts and gate:rebuild-validators. rebuild/conftest.py decides which lane a test is in: one whose fixture closure names live_artifacts reads live build output and is a validators test, everything else is contracts. Contracts runs first — it reads no artifact, so it takes every core this process may actually run on and its closure holds no build output, which is what lets an artifact-only change skip it — and a hard failure there returns before the long validators lane ever starts. Each lane's raw exit code is not the gate — the suite exits nonzero by design on the documented baseline failures — so the wrapper judges each run through the cycle's failure classifier: the documented baseline failures read green, and only an unexplained failure is red. FORCE=1 runs both lanes regardless.
# AMS_RUN_PYRIGHT is what type-checks rebuild/: a rebuild-only edit provably cannot move gate:make-test's fingerprint (MAKE_TEST_EXEMPT_PREFIXES exempts rebuild/), so `make test` can never be its gate. The suite runs under -n auto, so the same pytest_configure hook fires here, but it recognizes a rebuild-only run and skips the font build (unless the site fonts are absent, as after `make clean`) — this suite shapes against the site fonts exactly as its input-closure fingerprint already hashed them — leaving pyright to run alone and still fast-fail before the workers spawn; a pyright failure exits pytest nonzero with no FAILED/ERROR lines, which classify_rebuild_output already buckets as a hard failure. The flag rides into whichever lane spawns first and is stripped from every lane after it, since pyright's answer over one working tree cannot change between them.
test-rebuild:
	AMS_RUN_PYRIGHT=1 uv run python -m rebuild.tools.rebuild_gate $(if $(FORCE),--force)

# The rebuild suite's slow-marked tests, which both lanes' gates exclude: today that is the memo-dedupe audit over the live alphabet, which settles every distinct raw window the depth-3 sweep reaches rather than one per memo key. No lane flag — a slow test may sit in either lane, and the selection is the marker. The width is stated here rather than left to `-n auto`, and this is the one target in the repo where that is right: neither hook that answers `-n auto` can see how little the marker selects, every worker collects the whole rebuild suite before a single item is deselected, and a slot the selection cannot occupy therefore buys no parallelism while costing a whole collection. Measured with the selection emptied, the same run takes a few seconds at two workers and most of a minute at this box's full width. So two is what the marker's selection is sized for rather than what the box is — re-measure and widen it when the slow set grows past what two slots drain.
test-rebuild-slow:
	uv run pytest rebuild/ -m slow -n 2 --dist worksteal

# Run the test suite on efficiency cores only
test-slowly:
	AMS_RUN_PYRIGHT=1 taskpolicy -b uv run pytest test/ site/ -n $$(sysctl -n hw.perflevel1.logicalcpu) --dist worksteal

# Deep (≈1 min) isolation-leak gate: no NEW bad leak at depth 4 (site/bad-leak-backlog.txt), plus the benign census (site/benign-leak-census.txt).
test-leaks: all
	uv run pytest test/test_isolation_leaks.py -m slow

# Re-bless the bad-leak backlog and benign census after an intended change (then review the diff).
leak-snapshot: all
	uv run python tools/leak_snapshot.py

review:
	uv run python tools/review_scoped_anchor_selectors.py --output site/scoped-anchor-review/index.html

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

# Drive the commit-time artifact cycle (snapshot, run_m1, surface rebuild, the verdict chain — carry, merge into the autosave, the fills, the complaint docket — census-pin refresh, gates). Bare `make artifact-cycle` auto-resolves which verdicts master to carry; pass flags via ARGS, e.g. make artifact-cycle ARGS='--verdicts verdicts-X.json'. Every heavy stage auto-skips when a green record proves its inputs unchanged since its last green run — run_m1, the surface rebuild, gate:conform, the rebuild suite's two lanes (gate:rebuild-contracts and gate:rebuild-validators, each on its own closure and its own record), and gate:make-test — so a verdict-only cycle costs seconds; ARGS='--fresh' runs everything anyway (ARGS='--force-make-test' forces just that gate). The census-pin refresh is not one of them: every pass copies the build's census sidecar into rebuild/review-census-pins.json in milliseconds and prints that file's git diff, which is the census you accept by committing. This target verifies in one pass; `make review-cycle` is the deferring, converging form for the look-edit-look loop.
artifact-cycle:
	uv run python rebuild/tools/artifact_cycle.py $(ARGS)

# The whole loop in one command: run the artifact cycle (whose merge step lands the carried verdicts in the autosave — no browser import), then serve the surface. A failed cycle stops before serving.
# --stop-server hands the server question to the driver, which alone knows whether this pass writes under it. A pass that rebuilds the surface or moves the verdict store stops the server first, as this recipe always did; a pass that does neither — the gate pass the deferred gates exist to produce — leaves it up, so the letters stay on screen through the whole verification pass instead of vanishing for it. Whichever happened, the serve step below only binds the port when nothing already holds it.
# --defer-gates makes repeated runs converge instead of re-verifying every time: a pass that rebuilds M1 or the surface leaves the heavy gates pending so the letters are on screen sooner, the next pass has no artifact work left and runs them, and the pass after that costs seconds. A deferred gate is unproven, so `make verdict-ready` stays NOT READY until a pass clears it. ARGS='--no-defer-gates' verifies in the one pass, as `make artifact-cycle` does.
# SERVE=0 runs the same cycle but prints the restart command instead of serving, so the target terminates. That is what any non-interactive caller wants: served in the foreground, the recipe never exits and the cycle summary never lands as a completed command. It no longer costs that caller the server on a gate-only pass, which now keeps serving.
# SERVE=bg is for the caller that wants both: a recipe that terminates and the letters on screen after it. The server module has no daemon flag, so the recipe detaches it here (nohup, log to tmp/review-serve.log) and it outlives the shell that started it — including an agent harness's, which kills its own shell after each command. The wait is the point: the recipe does not return until the port answers, so a `make verdict-ready` on the next line reads a server that is up rather than racing one that is still importing tornado. Stop it the way the cycle does, with pkill -f 'rebuild\.review\.serve'.
review-cycle:
	uv run python rebuild/tools/artifact_cycle.py --defer-gates --stop-server $(ARGS)
	@if lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; then \
		printf '\nThe review server stayed up through this pass — the letters were on screen for all of it.\n'; \
	elif [ "$(SERVE)" = "0" ]; then \
		printf '\nThe review server was left stopped (SERVE=0). To look at the letters:\n    make review-serve\n\nUntil it is up, `make verdict-ready` reports the server down.\n'; \
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

# Answer "am I ready to verdict?": surface freshness, gate greenness, verdict-store alignment, server, blanks. Exit 0 when ready.
verdict-ready:
	uv run python -m rebuild.tools.verdict_ready $(ARGS)

# Answer "what is the cycle spending its time on, on this machine?": every artifact cycle appends per-step wall times and peak RSS (host-tagged, with each child's inner [t] phase lines) to rebuild/out/cycle-timings.ndjson — append-only, gitignored with the rest of rebuild/out, never pruned by retention, so each machine accumulates its own history. Default view: recent runs, steps slowest-first. ARGS='--by-step' aggregates count/median/max/latest seconds plus max recorded RSS per step and host; ARGS='--inner' expands the phase lines; ARGS='--journal <path>' reads a concatenation of journals from several machines.
cycle-timings:
	uv run python -m rebuild.tools.cycle_timings $(ARGS)

# Cluster the open complaints (reject/neither verdicts) by the rune records that decided them, with park candidates for the still-blank lookalikes; writes tmp/complaints-data.json. Reads the live autosave unless ARGS names a verdicts file; ARGS='--park g-XXXXXXXX' emits a verdicts-park-*.json for the app's Import dialog.
complaint-docket:
	uv run python rebuild/tools/complaint_docket.py $(ARGS)

# Order the blank queue for novelty — one rep per echo group, each next unit maximally unlike the last few across class, families, letters, stances, seams, configs, and provenance — and print the worklist URL to paste into the review app. Reads the live autosave unless ARGS names a verdicts file; emits a sitting-sized prefix of 40 by default, and ARGS='--limit 0' emits the whole queue.
novelty-order:
	uv run python rebuild/tools/novelty_order.py $(ARGS)

# Build the Rust M1 kernel (rebuild/kernel-rs, issue #40) in release mode. The release profile is the one the parity harness runs and the one every later port gate reuses, so there is deliberately no debug target.
kernel-build:
	cargo build --release --manifest-path rebuild/kernel-rs/Cargo.toml

# The settlement's gate: formatting, clippy with every warning fatal, and the crate's whole unit suite — spec ingest and its canonical-JSON echo, the specificity order, the settlement engine, the late-formation guard, and corpus-case replay. The crate is the only implementation of any of those, so what passes here is what ships. Named by surface rather than by case, because the suite grows with every packet of the port and any list of tests written here is stale by the next one.
kernel-check:
	cargo fmt --check --manifest-path rebuild/kernel-rs/Cargo.toml
	cargo clippy --all-targets --manifest-path rebuild/kernel-rs/Cargo.toml -- -D warnings
	cargo test --manifest-path rebuild/kernel-rs/Cargo.toml

# Prove the Rust kernel's spec ingest lossless: dump the live alphabet and every scaling-ladder rung through kernel_io.spec_json, echo each back out of the binary's own model, and require the bytes to be identical. A change to rebuild/pipeline/model.py that the Rust side has not followed fails here.
kernel-parity: kernel-build
	uv run python -m rebuild.tools.kernel_parity

# The thing to run around any kernel-semantics change (nothing in the artifact cycle runs it): the crate's own gate and the spec-ingest parity, seconds to a minute end to end. There is no settlement differential here and no fixpoint byte-compare, because there is no second settler and no second enumeration to compare against — settlement has one home. Settlement trust is the crate's own tests, gate:conform's HarfBuzz shaping against a per-window re-settle keyed on the raw window, and the witness gate. Neither leg takes knobs, so this target reads no ARGS.
kernel-gate: kernel-check kernel-parity

# The periodic deep form of gate:conform: the exhaustive font-vs-settle sweep at horizon 5+ (ARGS='--horizon 6' to go deeper, ARGS='--status' to ask whether it is armed), run by hand or overnight and never by the cycle. Its green is keyed on the emitted lookup's behavior classes, the font-compilation code and the uharfbuzz version rather than on the runes, so a rune edit that introduces no novel rule shape never stales it; a green deep run also refreshes gate:conform's own record, since an exhaustive sweep at this depth covers every text the per-edit belt shapes. The artifact cycle prints armed/current each pass.
conform-deep:
	uv run python -m rebuild.tools.deep_sweep $(ARGS)

# Compress the built OTFs in site/ into WOFF2 alongside them.
woff2: all
	find site -maxdepth 1 -name '*.otf' -print0 | xargs -0 -n1 woff2_compress

# Delete generated artifacts (the gitignored build output and Python caches). Leaves .uv-cache/ and .venv/ alone — those are deliberately-kept caches, not junk.
clean:
	find . -type d -name __pycache__ -not -path './.uv-cache/*' -not -path './.venv/*' -exec rm -rf {} +
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -not -path './.uv-cache/*' -not -path './.venv/*' -delete
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist wheels *.egg-info
	rm -rf site/before site/scoped-anchor-review
	rm -f site/AbbotsMortonSpaceport*.otf site/AbbotsMortonSpaceport*.fea site/DepartureMono-Regular.otf site/*.woff2 site/print.pdf site/check.html
