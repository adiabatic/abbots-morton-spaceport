.PHONY: all print-job

all:
	uv run python build_font.py glyph_data.yaml test/AbbotsMortonSpaceportMono.otf
	cd test && typst compile --font-path . test.typ

print-job: all
	lp test/test.pdf
