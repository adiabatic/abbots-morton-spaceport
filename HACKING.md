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

`test/the-manual.html` is the only JavaScript consumer of `test/wip.json`. On load it fetches that file, parses it as a JSON array of strings, and adds the `.wip` class from `test/shared.css` to matching entries in the manual. If the file is missing, empty, or malformed, the code silently does nothing.

Each array item is a whitespace-separated sequence of Quikscript letter names using the names from `test/shared.js` (`Day`, `Roe`, `Eight`, `J'ai`, and so on). Tokens may also include a `.variant` suffix when you want to match a specific `data-expect` token variant.

```json
[
  "Day Roe Eight",
  "Way.alt At"
]
```

The matcher scans `[data-expect]`, `.pairings td`, `.pairings dd`, `.word-list td`, and `.word-list dd`. A sequence marks an element as WIP if either its `data-expect` contains the requested tokens in order or its text content contains the raw PUA substring built from the base letter names.

When there is no active WIP list, `test/wip.json` is intentionally checked in as `[]` rather than deleted: the highlighting machinery stays loaded on standby, and adding a new WIP entry is a one-line edit instead of restoring a deleted file.

## Quikscript data

Quikscript uses a family-based source schema in `glyph_data/quikscript.yaml`. Each family can define `mono`, `prop`, shared `shapes`, and additional `forms`; forms declare explicit `traits` / `modifiers`, can reuse scaffolding with `inherits`, and `select` / `derive` rules use structured family selectors plus top-level `context_sets` instead of compiled glyph-name strings. `tools/build_font.py` compiles that into the flat glyph map used by feature generation and tests. In VS Code, `.vscode/quikscript.schema.json` is associated with that file for hover docs and structural validation when the Red Hat YAML extension is installed.

The join compiler is split across `tools/quikscript_ir.py` and `tools/quikscript_fea.py`. `tools/build_font.py` owns generic font loading/building; Quikscript family compilation and generated join transforms live in the IR module; Senior feature analysis and `curs`/gated-feature/`calt`/stylistic-set emission live together in `tools/quikscript_fea.py`.

Within that pipeline, `tools/glyph_compiler.py` owns the canonical variant-level compilation result. `CompiledGlyphSet` carries legacy flat `glyphs:` data, compiled Quikscript `JoinGlyph`s, and merged glyph metadata; Quikscript stays in `JoinGlyph` form through feature analysis and emission, and only flattens back to raw glyph dicts at the final generic font-build boundary. Those flat glyph dicts are build materialization only; compiler metadata lives on `JoinGlyph`, not in `_base_name`-style keys on the flattened output.

## Shaping leaks: what you have to drive

A "shaping leak" is a letter changing shape across a pen-lift (a non-join) because of a neighbor it cannot actually connect to — the canonical case is a stroke reaching out to join a letter that isn't there, dangling into space. The full definition and the design decisions behind it are in [doc/definitions/shaping-leakage.md](doc/definitions/shaping-leakage.md). The detection-and-classification machinery is built and runs on its own; this section is the part that needs _you_, because the calls it makes are judgment calls a human owns.

The machinery sorts every visible leak into **bad** (a real defect — a dangle) or **benign** (a subtractive trim, a standalone-variant swap, or an intentional cosmetic tuck — the slightly-hand-drawn variation we actually want). That sort is mechanical, validated to agree with your past triage exactly, but it is a proxy: when it is wrong, you correct it with an override (below). The two sets live in two checked-in files:

- `test/bad-leak-backlog.txt` — the defects still outstanding. This is the to-do list.
- `test/benign-leak-census.txt` — the welcome variation. This is a census, not a defect list.

### When a gate complains

- **`make test` (every run) fails with "NEW bad isolation leak(s)".** A change you made grew a dangle. Either fix it — make the break-facing edge subtractive (or revert it) for that one context, using the levers in the "How to do simple changes" section of `CLAUDE.md` — or, if you decide the new bad leak is actually acceptable, re-bless (next bullet) so it joins the backlog. Resolving an _existing_ backlog entry never fails the gate; it just prints a "nice — re-bless" notice.
- **`make test-leaks` (the deep, ~1-minute gate) fails on the benign census.** The set of benign variation shifted. This is informational, never a defect on its own — but look at the diff so you _notice_ the organic-variation set moving, then re-bless.
- **Re-bless after any intended change:** `make leak-snapshot` regenerates both files. Always `git diff` them before committing — that diff is the whole point of the gate, and reviewing it is your job.

### When the bad/benign call is wrong (overrides)

The proxy occasionally mislabels a leak. You correct it per-signature, never by weakening the proxy:

- `test/leak-force-bad.yaml` — a leak the proxy calls benign but you find ugly. Add its 4-tuple signature here and it counts as a defect.
- `test/leak-force-benign.yaml` — a leak the proxy calls bad but you've decided is fine (a legitimate standalone variant). Add its signature here and it stops failing the gate.

A signature is the `[isolated_left, left_chosen, isolated_right, right_chosen]` 4-tuple — copy it straight off the `:: *L a->b | *R c->d` line in the backlog or census. After editing either file, run `uv run python tools/leak_verdict_reconcile.py` to confirm the classifier still reconciles cleanly (it scores the proxy against your historical triage and prints precision/recall).

### Eyeballing leaks

`make check-html` regenerates `test/check.html`; open it and scroll to "Auto-generated: isolation leaks". Each row is tagged `bad` or `benign` and shows the in-context shaping beside the two halves shaped separately. Reach for the `bad` rows first — those are the defects. See [test/isolation-leaks.md](test/isolation-leaks.md) for the full workflow.

### Fixing the backlog in bulk (the loop)

Draining a backlog of bad leaks one at a time is the kind of grind meant to be handed to an autonomous loop — but it is _not yet built_, and when it is, it still needs you at the ends: you launch it, and you approve the batch it produces. The brief for that loop (how it diagnoses each dangle, the per-fix verify gate, and the fact that it accumulates fixes and stops for one approval rather than committing on its own) is [doc/definitions/shaping-leak-loop.md](doc/definitions/shaping-leak-loop.md). Until it exists, fixing bad leaks is the same manual loop: pick a backlog entry, apply a subtractive fix, `make test-leaks`, re-bless, repeat.

## Understanding

Really, you ought to ask an LLM set to maximum thinking mode to give you the 10¢ tour. Anything written down here would probably be out of date by the time you read it.
