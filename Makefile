.PHONY: all test typecheck print-job serve explainer

all:
	uv run python tools/gen_ensure_sanity.py
	uv run python tools/build_font.py glyph_data/ test/
	cd test && typst compile --font-path . print.typ

typecheck:
	uv run pyright tools/ test/

test: all typecheck
	uv run pytest test/ -v

print-job: all
	lp test/print.pdf

explainer:
	cd doc/explainer && typst compile main.typ

serve:
	uv run python tools/serve.py
