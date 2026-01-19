all:
	uv run python build_font.py glyph_data.yaml test/AbbotsMortonSpaceportMono.otf
	cd test && typst compile --font-path . test.typ
