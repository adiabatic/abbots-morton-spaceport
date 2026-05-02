.PHONY: all test typecheck print-job serve explainer snapshot-before

all:
	uv run python tools/build_font.py glyph_data/ test/
	cd test && typst compile --font-path . print.typ

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

test: typecheck
	uv run pytest test/ -n auto

print-job: all
	lp test/print.pdf

explainer:
	cd doc/explainer && typst compile main.typ

serve:
	uv run python tools/serve.py
