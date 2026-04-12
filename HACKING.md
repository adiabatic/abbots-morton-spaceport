# Abbots Morton Spaceport developer guide

## Making it go

```bash
make all
```

Dependencies are managed with `uv` and defined in `pyproject.toml`.

## Testing

Open `test/index.html` in a browser to test the font interactively.

For the repeatable workflow for finding Quikscript words in `test/the-manual.html` that are missing `data-expect` coverage elsewhere in the file, see [doc/checking-data-expect-coverage.md](doc/checking-data-expect-coverage.md).

## WIP word highlighting

`test/the-manual.html` still contains the only JavaScript consumer of `test/wip.json`. On load it fetches that file, parses it as a JSON array of strings, and adds the `.wip` class from `test/shared.css` to matching entries in the manual. If the file is missing, empty, or malformed, the code silently does nothing.

Each array item is a whitespace-separated sequence of Quikscript letter names using the names from `test/shared.js` (`Day`, `Roe`, `Eight`, `J'ai`, and so on). Tokens may also include a `.variant` suffix when you want to match a specific `data-expect` token variant.

```json
[
  "Day Roe Eight",
  "Way.alt At"
]
```

The matcher scans `[data-expect]`, `.pairings td`, `.pairings dd`, `.word-list td`, and `.word-list dd`. A sequence marks an element as WIP if either its `data-expect` contains the requested tokens in order or its text content contains the raw PUA substring built from the base letter names. No other current JavaScript reads `wip.json`.

## Quikscript data

Quikscript now uses a family-based source schema in `glyph_data/quikscript.yaml`. Each family can define `mono`, `prop`, shared `shapes`, and additional `forms`; forms declare explicit `traits` / `modifiers`, can reuse scaffolding with `inherits`, and `select` / `derive` rules use structured family selectors plus top-level `context_sets` instead of compiled glyph-name strings. `tools/build_font.py` compiles that into the flat glyph map used by feature generation and tests. In VS Code, `.vscode/quikscript.schema.json` is associated with that file for hover docs and structural validation when the Red Hat YAML extension is installed.

The join compiler is now split into `tools/quikscript_ir.py` and `tools/quikscript_fea.py`. `tools/build_font.py` still owns generic font loading/building, but Quikscript family compilation and generated join transforms live in the IR module, while Senior feature analysis and `curs`/gated-feature/`calt`/stylistic-set emission now live together in `tools/quikscript_fea.py`.

Within that pipeline, `tools/glyph_compiler.py` owns the canonical variant-level compilation result. `CompiledGlyphSet` carries legacy flat `glyphs:` data, compiled Quikscript `JoinGlyph`s, and merged glyph metadata; Quikscript stays in `JoinGlyph` form through feature analysis and emission, and only flattens back to raw glyph dicts at the final generic font-build boundary. Those flat glyph dicts are build materialization only; compiler metadata lives on `JoinGlyph`, not in `_base_name`-style keys on the flattened output.

## Understanding

Really, you ought to ask an LLM set to maximum thinking mode to give you the 10¢ tour. If I wrote down documentation now, it’d probably be out of date by the time you read this.
