.PHONY: all test print-job watch explainer

all:
	uv run python tools/gen_ensure_sanity.py
	uv run python tools/build_font.py glyph_data/ test/
	cd test && typst compile --font-path . print.typ

test: all
	uv run pytest test/ -v

print-job: all
	lp test/print.pdf

explainer:
	cd doc/explainer && typst compile main.typ

watch:
	uv run python tools/watch.py
