# Instructions for agents

## General

- Always ask clarifying questions when there are multiple valid ways to do something.

## Python

IMPORTANT: Always use `uv run` instead of `python` or `python3` directly. For example:

- `uv run build_font.py` not `python build_font.py`
- `uv run pytest` not `pytest`

## Inspiration

- This is a font that’s supposed to match Departure Mono’s metrics.
- When in doubt, look at @inspo/DepartureMono-Regular.otf to check metrics.
- When referring to Quikscript letters, they are frequently prefixed by a `·`, like in `·Why`.
- If you want to know what Quikscript letters go with what code points, look at @inspo/csur/index.html .

## Markdown-document style

- Use sentence case for titles, not title case.
- Make sure `markdownlint-cli2` doesn’t have anything to complain about.
