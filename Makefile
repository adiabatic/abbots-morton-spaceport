.PHONY: all test print-job watch

all:
	uv run python tools/build_font.py glyph_data/ test/
	cd test && typst compile --font-path . print.typ

test: all
	uv run pytest test/ -v

print-job: all
	lp test/print.pdf

watch:
	browser-sync start --server test/ --port 7293 --files "test/*.html, test/*.css, *.otf"
