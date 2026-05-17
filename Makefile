.PHONY: all test typecheck print-job serve explainer snapshot-before check-html review test-and-review prettier

export UV_CACHE_DIR := .uv-cache

all:
	uv run python tools/build_font.py glyph_data/ test/
	cd test && typst compile --font-path . print.typ

check-html: all
	uv run python tools/build_check_html.py

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
	uv run pytest test/ -n auto

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
