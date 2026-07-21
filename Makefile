.PHONY: all test test-slowly test-leaks leak-snapshot typecheck print-job serve explainer check-html-before check-html-after build-kerning-hardcases review test-and-review review-build review-serve review-cycle artifact-cycle verdict-ready complaint-docket prettier woff2 clean

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
	uv run pyright tools/ test/ conftest.py

prettier:
	uv run --with black black -q tools/ test/ conftest.py

# Self-skipping: the wrapper exits 0 in ~a second when nothing the suite reads has changed since its last green run (the input closure excludes rebuild/, glyph_data/runes/, doc/, tmp/, .claude/, and Markdown; the green record at rebuild/out/make-test-green.json is shared with the artifact cycle's gate:make-test). FORCE=1 runs the suite regardless. The pyright gate runs inside pytest_configure (via AMS_RUN_PYRIGHT) so it overlaps the font build instead of preceding it serially; it still fast-fails before the workers spawn. The `typecheck` target stays for standalone/pre-commit use.
test:
	AMS_RUN_PYRIGHT=1 uv run python -m rebuild.tools.make_test_gate $(if $(FORCE),--force)

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

# Drive the commit-time artifact cycle (snapshot, run_m1, surface rebuild, carry, merge into the autosave, census pins, gates). Bare `make artifact-cycle` auto-resolves which verdicts master to carry; pass flags via ARGS, e.g. make artifact-cycle ARGS='--verdicts verdicts-X.json'. gate:make-test auto-skips when nothing outside rebuild/, glyph_data/runes/, doc/, and Markdown has changed since its last green run (ARGS='--force-make-test' overrides).
artifact-cycle:
	uv run python rebuild/tools/artifact_cycle.py $(ARGS)

# The whole loop in one command: stop the review server if it's running, run the artifact cycle (whose merge step lands the carried verdicts in the autosave — no browser import), then serve the fresh surface. A failed cycle stops before serving.
review-cycle:
	-@pkill -f 'rebuild\.review\.serve' 2>/dev/null || true
	@while lsof -ti tcp:7294 -sTCP:LISTEN >/dev/null 2>&1; do sleep 0.2; done
	uv run python rebuild/tools/artifact_cycle.py $(ARGS)
	uv run python -m rebuild.review.serve

# Answer "am I ready to verdict?": surface freshness, gate greenness, verdict-store alignment, server, blanks. Exit 0 when ready.
verdict-ready:
	uv run python -m rebuild.tools.verdict_ready $(ARGS)

# Cluster the open complaints (reject/neither verdicts) by the rune records that decided them, with park candidates for the still-blank lookalikes; writes tmp/complaints-data.json. Reads the live autosave unless ARGS names a verdicts file; ARGS='--park g-XXXXXXXX' emits a verdicts-park-*.json for the app's Import dialog.
complaint-docket:
	uv run python rebuild/tools/complaint_docket.py $(ARGS)

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
