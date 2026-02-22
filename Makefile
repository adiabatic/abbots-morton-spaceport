.PHONY: all test print-job

all:
	uv run python build_font.py glyph_data/ test/
	touch test/test.html
	cd test && typst compile --font-path . print.typ

test: all
	uv run pytest test/test_shaping.py -v

print-job: all
	lp test/print.pdf
