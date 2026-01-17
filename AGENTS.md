# Instructions for agents

## General

- Always ask clarifying questions when there are multiple valid ways to do something.

## Python

IMPORTANT: Always use `uv run` instead of `python` or `python3` directly. For example:

- `uv run build_font.py` not `python build_font.py`
- `uv run pytest` not `pytest`

## Generating glyphs for the first time in @glyph_data.yaml

- Keep all glyphs alphabetized by code point (`uniXXXX`).
- When looking at @inspo/manual-page-2.pdf, ignore the hyphens in the names when looking up the names (like `T-ea` for ·Tea).
- Tall letters have a height of 9 pixels (9 entries in `bitmap`).
- Short letters have a height of 6 pixels (6 entries in `bitmap`).
- Deep letters have a height of 6 pixels (6 entries in `bitmap`) and a `y_offset` of -3.

## Inspiration

- This is a font that’s supposed to match Departure Mono’s metrics.
- When in doubt, look at @inspo/DepartureMono-Regular.otf to check metrics.
- When referring to Quikscript letters, they are frequently prefixed by a `·`, like in `·Why`.
- Use @inspo/csur/index.html to find out what Quikscript letters go with what code points.

## Markdown-document style

- Use sentence case for titles, not title case.
- Make sure `markdownlint-cli2` doesn’t have anything to complain about.
