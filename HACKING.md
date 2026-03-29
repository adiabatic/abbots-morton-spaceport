# Abbots Morton Spaceport developer guide

## Making it go

```bash
make all
```

Dependencies are managed with `uv` and defined in `pyproject.toml`.

## Testing

Open `test/index.html` in a browser to test the font interactively.

## Quikscript data

Quikscript now uses a family-based source schema in `glyph_data/quikscript.yaml`. Each family can define `mono`, `prop`, shared `shapes`, and additional `forms`; forms declare explicit `traits` / `modifiers`, can reuse scaffolding with `inherits`, and `select` / `derive` rules use structured family selectors plus top-level `context_sets` instead of compiled glyph-name strings. `tools/build_font.py` compiles that into the flat glyph map used by feature generation and tests. In VS Code, `.vscode/quikscript.schema.json` is associated with that file for hover docs and structural validation when the Red Hat YAML extension is installed.

The join compiler is now split into `tools/quikscript_ir.py`, `tools/quikscript_planner.py`, and `tools/quikscript_fea.py`. `tools/build_font.py` still owns generic font loading/building, but Quikscript family compilation, generated join transforms, rule planning, and `calt`/`curs` emission now live in those dedicated modules.

## Understanding

Really, you ought to ask an LLM set to maximum thinking mode to give you the 10¢ tour. If I wrote down documentation now, it’d probably be out of date by the time you read this.
