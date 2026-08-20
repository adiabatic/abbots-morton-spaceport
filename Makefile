.PHONY: all test test-rebuild test-slowly test-leaks leak-snapshot typecheck print-job serve explainer check-html-before check-html-after build-kerning-hardcases review test-and-review review-build review-serve review-cycle artifact-cycle verdict-ready cycle-timings complaint-docket novelty-order kernel-build kernel-check kernel-parity kernel-differential kernel-fixpoint kernel-fixpoint-pinned kernel-fixpoint-label-grain kernel-liveness kernel-gate prettier woff2 clean

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

# The rebuild suite's self-skipping wrapper, sharing the green record at rebuild/out/rebuild-gate-green.json with the artifact cycle's gate:rebuild. The suite's raw exit code is not the gate — it exits nonzero by design on the documented baseline failures — so the wrapper judges the run through the cycle's failure classifier: the documented baseline failures read green, and only an unexplained failure is red. FORCE=1 runs the suite regardless.
# AMS_RUN_PYRIGHT is what type-checks rebuild/: a rebuild-only edit provably cannot move gate:make-test's fingerprint (MAKE_TEST_EXEMPT_PREFIXES exempts rebuild/), so `make test` can never be its gate. The suite runs under -n auto, so the same pytest_configure hook fires here, but it recognizes a rebuild-only run and skips the font build (unless the site fonts are absent, as after `make clean`) — this suite shapes against the site fonts exactly as its input-closure fingerprint already hashed them — leaving pyright to run alone and still fast-fail before the workers spawn; a pyright failure exits pytest nonzero with no FAILED/ERROR lines, which classify_rebuild_output already buckets as a hard failure.
test-rebuild:
	AMS_RUN_PYRIGHT=1 uv run python -m rebuild.tools.rebuild_gate $(if $(FORCE),--force)

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

# Drive the commit-time artifact cycle (snapshot, run_m1, surface rebuild, carry, merge into the autosave, census-pin refresh, gates). Bare `make artifact-cycle` auto-resolves which verdicts master to carry; pass flags via ARGS, e.g. make artifact-cycle ARGS='--verdicts verdicts-X.json'. Every heavy stage auto-skips when a green record proves its inputs unchanged since its last green run — run_m1, the surface rebuild, gate:conform, gate:rebuild, and gate:make-test — so a verdict-only cycle costs seconds; ARGS='--fresh' runs everything anyway (ARGS='--force-make-test' forces just that gate). The census-pin refresh is not one of them: every pass copies the build's census sidecar into rebuild/review-census-pins.json in milliseconds and prints that file's git diff, which is the census you accept by committing. This target verifies in one pass; `make review-cycle` is the deferring, converging form for the look-edit-look loop.
artifact-cycle:
	uv run python rebuild/tools/artifact_cycle.py $(ARGS)

# The whole loop in one command: run the artifact cycle (whose merge step lands the carried verdicts in the autosave — no browser import), then serve the surface. A failed cycle stops before serving.
# --stop-server hands the server question to the driver, which alone knows whether this pass writes under it. A pass that rebuilds the surface or moves the verdict store stops the server first, as this recipe always did; a pass that does neither — the gate pass the deferred gates exist to produce — leaves it up, so the letters stay on screen through the whole verification pass instead of vanishing for it. Whichever happened, the serve step below only binds the port when nothing already holds it.
# --defer-gates makes repeated runs converge instead of re-verifying every time: a pass that rebuilds M1 or the surface leaves the heavy gates pending so the letters are on screen sooner, the next pass has no artifact work left and runs them, and the pass after that costs seconds. A deferred gate is unproven, so `make verdict-ready` stays NOT READY until a pass clears it. ARGS='--no-defer-gates' verifies in the one pass, as `make artifact-cycle` does.
# SERVE=0 runs the same cycle but prints the restart command instead of serving, so the target terminates. That is what any non-interactive caller wants: served in the foreground, the recipe never exits and the cycle summary never lands as a completed command. It no longer costs that caller the server on a gate-only pass, which now keeps serving.
review-cycle:
	uv run python rebuild/tools/artifact_cycle.py --defer-gates --stop-server $(ARGS)
	@if lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; then \
		printf '\nThe review server stayed up through this pass — the letters were on screen for all of it.\n'; \
	elif [ "$(SERVE)" = "0" ]; then \
		printf '\nThe review server was left stopped (SERVE=0). To look at the letters:\n    make review-serve\n\nUntil it is up, `make verdict-ready` reports the server down.\n'; \
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

# The crate's own gate: formatting, clippy with every warning fatal, and the crate's whole unit suite — spec ingest and its canonical-JSON echo, the specificity order, the settlement engine, the late-formation guard, and corpus-case replay. Named by surface rather than by case, because the suite grows with every packet of the port and any list of tests written here is stale by the next one.
kernel-check:
	cargo fmt --check --manifest-path rebuild/kernel-rs/Cargo.toml
	cargo clippy --all-targets --manifest-path rebuild/kernel-rs/Cargo.toml -- -D warnings
	cargo test --manifest-path rebuild/kernel-rs/Cargo.toml

# Prove the Rust kernel's spec ingest lossless: dump the live alphabet and every scaling-ladder rung through kernel_io.spec_json, echo each back out of the binary's own model, and require the bytes to be identical. A change to rebuild/pipeline/model.py that the Rust side has not followed fails here.
kernel-parity: kernel-build
	uv run python -m rebuild.tools.kernel_parity

# Prove the Rust settlement core answers every window the way Python does: the late-formation guard swept exhaustively, seeded fuzz windows in each mode combination the port has to reproduce, and the golden single-window corpus replayed per acceptance configuration — all compared as bytes, result record and fired-pointer delta included. ARGS passes the harness's own knobs; ARGS='--skip-corpus' is the fast form that skips the per-configuration fixpoints.
kernel-differential: kernel-build
	uv run python -m rebuild.tools.kernel_differential $(ARGS)

# Prove the Rust kernel's table-build fixpoint is Python's: for the live alphabet, every scaling-ladder rung and every acceptance configuration, compare the whole transition stream as bytes, fold the kernel's own stream back through assemble_tables and compare the three persisted artifacts, and compare table_digest. This is the shipping world — the harness reflects whatever world the Python process is in onto the kernel's flags, so a bare recipe compares the fixpoint a build actually enumerates. ARGS='--live-only' is the fast form that skips the ladder.
kernel-fixpoint: kernel-build
	uv run python -m rebuild.tools.kernel_fixpoint $(ARGS)

# The same comparison in sub-issue #44's pinned candidacy world, kept as a standing regression now that the shipping world is the default arm: both semantics flags off, which is also the one world where enumeration stays label-grain whatever AMS_DEEP_CLASSES says.
kernel-fixpoint-pinned: kernel-build
	AMS_SIMULATED_PROSPECT=0 AMS_VOTE_SLOTS=0 uv run python -m rebuild.tools.kernel_fixpoint $(ARGS)

# The same comparison at label grain: the deep slots enumerate one row per token instead of one per outcome fiber, which is the kernel's --deep-classes-off arm and the comparison state the issue-26 class grain is measured against.
kernel-fixpoint-label-grain: kernel-build
	AMS_DEEP_CLASSES=0 uv run python -m rebuild.tools.kernel_fixpoint $(ARGS)

# Prove the Rust kernel's deep-slot liveness is Python's, one grain below the fixpoint: every letter triple's third-slot verdict, every quad's fourth-slot verdict, and the class-grain fiber partition of every live context — in all four mode combinations, compared as bytes. Where a wrong verdict reaches kernel-fixpoint as thousands of rows that split differently, it reads here as one triple. --exhaustive rides the recipe because at this alphabet it is free: the third arm has already driven the fourth-slot probes through the joint34 belt, so the whole quad space answers off a warm memo, while a sample sized for that space misses nearly every live fourth slot there is. ARGS='--python-only' writes the keys and Python's answers without invoking a binary, which is the Python half of a cross-build comparison.
kernel-liveness: kernel-build
	uv run python -m rebuild.tools.kernel_liveness --exhaustive $(ARGS)

# The standalone Rust-vs-Python differential at artifact grain, to run around a kernel-semantics change (nothing in the artifact cycle runs it): enumerate the live spec's six acceptance configurations in one kernel process, enumerate the Python side fresh, fold both through Python's own back half, and require the three artifacts and the contract digest to be byte-identical. It builds both sides itself — the cycle's artifacts are the kernel's own fold now, so there is nothing on disk to compare against. ARGS passes --threads/--skip-build/--out.
kernel-gate: kernel-build
	uv run python -m rebuild.tools.kernel_gate $(ARGS)

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
