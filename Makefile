.PHONY: all print-job

all:
	uv run python build_font.py glyph_data/ test/
	touch test/test.html
	cd test && typst compile --font-path . print.typ

print-job: all
	lp test/print.pdf
