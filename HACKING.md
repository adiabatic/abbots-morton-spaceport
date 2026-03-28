# Abbots Morton Spaceport developer guide

## Making it go

```bash
make all
```

Dependencies are managed with `uv` and defined in `pyproject.toml`.

## Testing

Open `test/index.html` in a browser to test the font interactively.

## Quikscript data

Quikscript now uses a family-based source schema in `glyph_data/quikscript.yaml`. Each family can define `mono`, `prop`, shared `shapes`, and additional `forms`; `tools/build_font.py` compiles that into the flat glyph map used by feature generation and tests.

## Understanding

Really, you ought to ask an LLM set to maximum thinking mode to give you the 10¢ tour. If I wrote down documentation now, it’d probably be out of date by the time you read this.
