.PHONY: all test test-slowly test-leaks leak-snapshot typecheck print-job serve explainer snapshot-before check-html build-kerning-hardcases review test-and-review prettier

all:
	uv run python tools/build_font.py glyph_data/ site/
	cd site && typst compile --font-path . print.typ

check-html: all
	uv run python tools/build_check_html.py

build-kerning-hardcases: all
	uv run python tools/build_kerning_hardcases.py

snapshot-before: all
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

test: typecheck
	uv run pytest test/ site/ -n auto --dist worksteal

# Run the test suite on efficiency cores only
test-slowly: typecheck
	taskpolicy -b uv run pytest test/ site/ -n $$(sysctl -n hw.perflevel1.logicalcpu) --dist worksteal

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
