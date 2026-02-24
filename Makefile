.PHONY: all test print-job

all:
	uv run python build_font.py glyph_data/ test/
	touch test/index.html
	cd test && typst compile --font-path . print.typ

test: all
	uv run pytest test/ -v

print-job: all
	lp test/print.pdf
