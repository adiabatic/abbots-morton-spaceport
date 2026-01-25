.PHONY: all print-job

all:
	uv run python build_font.py glyph_data.yaml test/
	cd test && typst compile --font-path . print.typ

print-job: all
	lp test/print.pdf
