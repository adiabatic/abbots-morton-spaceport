.PHONY: all test test-slowly test-leaks leak-snapshot typecheck print-job serve explainer snapshot-before check-html build-kerning-hardcases review test-and-review prettier

all:
	uv run python tools/build_font.py glyph_data/ test/
	cd test && typst compile --font-path . print.typ

check-html: all
	uv run python tools/build_check_html.py

build-kerning-hardcases: all
	uv run python tools/build_kerning_hardcases.py

snapshot-before: all
	mkdir -p test/before
	cp test/AbbotsMortonSpaceportMono-Regular.otf test/before/
	cp test/AbbotsMortonSpaceportMono-Bold.otf test/before/
	cp test/AbbotsMortonSpaceportSansJunior-Regular.otf test/before/
	cp test/AbbotsMortonSpaceportSansJunior-Bold.otf test/before/
	cp test/AbbotsMortonSpaceportSansSenior-Regular.otf test/before/
	cp test/AbbotsMortonSpaceportSansSenior-Bold.otf test/before/

typecheck:
	uv run pyright tools/ test/

prettier:
	uv run --with black black -q tools/ test/

test: typecheck
	uv run pytest test/ -n auto --dist worksteal

# Run the test suite on efficiency cores only
test-slowly: typecheck
	taskpolicy -b uv run pytest test/ -n $$(sysctl -n hw.perflevel1.logicalcpu) --dist worksteal

# Deep (≈1 min) isolation-leak gate: no NEW bad leak at depth 4 (test/bad-leak-backlog.txt), plus the benign census (test/benign-leak-census.txt).
test-leaks: all
	uv run pytest test/test_isolation_leaks.py -m slow

# Re-bless the bad-leak backlog and benign census after an intended change (then review the diff).
leak-snapshot: all
	uv run python tools/leak_snapshot.py

review:
	uv run python tools/review_scoped_anchor_selectors.py --output test/scoped-anchor-review/index.html

test-and-review:
	@$(MAKE) -j2 test review

print-job: all
	lp test/print.pdf

explainer:
	cd doc/explainer && typst compile main.typ

serve:
	uv run python tools/serve.py
