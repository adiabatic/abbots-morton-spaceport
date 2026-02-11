# Instructions for agents

## General

- Always ask clarifying questions when there are multiple valid ways to do something.

## HTML/CSS/JS

- Prefer nested CSS over flat CSS.
- Prefer for-of loops to `.forEach()`.

## Python

IMPORTANT: Always use `uv run` instead of `python` or `python3` directly. For example:

- `uv run build_font.py` not `python build_font.py`
- `uv run pytest` not `pytest`

After running `build_font.py`, run `touch test/test.html` to trigger automatic browser refresh:

- `uv run build_font.py glyph_data test/ && touch test/test.html`

## Adding glyphs

- Whenever a glyph is added to any YAML file under `glyph_data/`, ensure it also has an entry in @postscript_glyph_names.yaml if it uses a standard PostScript name (not a `uniXXXX` name).

## Generating glyphs for the first time in @glyph_data/quikscript.yaml

- Keep all glyphs alphabetized by code point (`uniXXXX`).
- When looking at @inspo/manual-page-2.pdf, ignore the hyphens in the names when looking up the names (like `T-ea` for ·Tea).
- Tall letters have a height of 9 pixels (9 entries in `bitmap`).
- Short letters have a height of 6 pixels (6 entries in `bitmap`).
- Deep letters have a height of 9 pixels (9 entries in `bitmap`) and a `y_offset` of -3.

## Inspiration

- This is a font that’s supposed to match Departure Mono’s metrics.
- When in doubt, look at @test/DepartureMono-Regular.otf to check metrics.
- When referring to Quikscript letters, they are frequently prefixed by a `·`, like in `·Why`.
- Use @inspo/csur/index.html to find out what Quikscript letters go with what code points.
- To understand a Quikscript letter shape, zoom/crop @inspo/manual-page-2.pdf (the colons between the letters show the vertical dimension of a Short letter) and, if needed, compare against @inspo/csur/kingsley.ttf, then translate the stroke path into the 5×6 or 5×9 pixel grid.

## Bumping the version number

1. Update `version` in `glyph_data/metadata.yaml` (e.g., `3.000`)
2. Update `version` in `pyproject.toml` (e.g., `3.0.0`)
3. Run `uv sync` to update `uv.lock`

## Markdown-document style

- Use sentence case for titles, not title case.
- Make sure `markdownlint-cli2` doesn’t have anything to complain about.
