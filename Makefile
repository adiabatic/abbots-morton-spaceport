.PHONY: all test test-slowly test-leaks leak-snapshot typecheck print-job serve explainer check-html-before check-html-after build-kerning-hardcases review test-and-review review-build review-serve artifact-cycle prettier woff2 clean

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

# The pyright gate runs inside pytest_configure (via AMS_RUN_PYRIGHT) so it overlaps the font build instead of preceding it serially; it still fast-fails before the workers spawn. The `typecheck` target stays for standalone/pre-commit use.
test:
	AMS_RUN_PYRIGHT=1 uv run pytest test/ site/ -n auto --dist worksteal

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

# Drive the commit-time artifact cycle (snapshot, run_m1, surface rebuild, carry, census pins, gates). Pass flags via ARGS, e.g. make artifact-cycle ARGS='--verdicts verdicts-X.json'.
artifact-cycle:
	uv run python rebuild/tools/artifact_cycle.py $(ARGS)

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
