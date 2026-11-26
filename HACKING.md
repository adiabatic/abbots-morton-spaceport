# Abbots Morton Spaceport developer guide

## Making it go

```bash
make all
```

`make all` publishes the six fonts into `site/` (`rebuild/tools/build_fonts.py`): Mono and Junior built from the rune files and the other glyph files, and Senior copied from the rebuild's own build under `rebuild/out/m1/`, which it runs first when that build is stale. Dependencies are managed with `uv` and defined in `pyproject.toml`. The Senior's table build runs in the Rust crate under `rebuild/kernel-rs/`, so the tree needs a Rust toolchain (`cargo`, from [rustup](https://rustup.rs)) besides `uv`: without it you cannot build Senior, settle a window, or run the rebuild test suite. `make kernel-build` builds the crate in release mode, and the pipeline builds it before it first runs it.

`make serve` serves `site/` on `http://localhost:7293/` and reloads the open pages when a font changes.

## Where the letters live

- Each Quikscript letter and ligature is a rune file under `glyph_data/runes/`, whose grammar is `rebuild/schema/rune.schema.json`: its ductus, its stances with their bitmaps and join surfaces, its policy records and its kerning. `doc/rebuild-design.md` explains the model.
- The Latin, numeral, punctuation, composite, symbol and Shavian glyphs, the font metadata and the Latin kerning rules are the other `glyph_data/*.yaml` files, and `postscript_glyph_names.yaml` maps glyph names to code points.
- The variation sequences and the stylistic sets are in the registry, `glyph_data/script.yaml`, the one file in `glyph_data/` that holds no glyphs.

## Testing

`doc/testing.md` maps each kind of change to the gate that checks it. In short: `make test` checks the published fonts, the `data-expect` pins on the site pages among them (`doc/data-expect.md`); `make test-rebuild` checks the pipeline under `rebuild/`; and `uv run python -m rebuild.pipeline.run_m1` rebuilds Senior's tables and font after a rune or pipeline edit.

Open `site/index.html` (through `make serve`) to test the fonts interactively. For the repeatable workflow for finding Quikscript words in `site/the-manual.html` that are missing `data-expect` coverage elsewhere in the file, see [doc/checking-data-expect-coverage.md](doc/checking-data-expect-coverage.md).

## Reviewing an edit

`make review-cycle` rebuilds what an edit touches, runs the gates, and serves the review app on `http://localhost:7294/`, whose Queue shows what the edit changed against the accepted build; `make review-accept ARGS=--apply` accepts the changes once each has a verdict. `doc/review-cycle.md` is the runbook, and `make artifact-cycle` is the same verification without the server, for commit time.

## Cutting a release

The fonts are built here and GitHub publishes them: the Senior's table build needs more memory than a GitHub-hosted runner has, so `.github/workflows/deploy.yml` builds nothing and publishes the fonts a draft release holds.

1. Bump the version with the bump-minor or bump-major skill, which opens a `### <version>` heading in `FONTLOG.md`.
2. Fill that section with what changed for the fonts' users (the summarize-for-FONTLOG command drafts it). It becomes the release notes.
3. Commit, with the commit-time gates (`make artifact-cycle`), and push `master`.
4. Run `make all`, so that `site/` holds the six fonts of that commit, then `make draft-release`, a dry run that prints its checks and what it would upload. `make draft-release ARGS=--apply` uploads the six fonts and their manifest, `fonts.json`, to a draft release for `release/<version>`, replacing any draft for that tag; `rebuild/tools/release_fonts.py`'s module docstring lists what it refuses.
5. Run the two commands it prints, `git tag release/<version> <commit>` and `git push origin release/<version>`. The push runs the workflow, which checks the fonts against `fonts.json` and the tagged commit, makes the WOFF2 files, deploys the site, attaches `abbots-morton-spaceport-<version>.zip` and publishes the release with the `FONTLOG.md` section as its notes.

Running the workflow by hand from the Actions page redeploys the site with the fonts of the latest published release, or of the release its `tag` input names (`release/12.000` or later).

## WIP word highlighting

`site/the-manual.html` is the only JavaScript consumer of `site/wip.json`. On load it fetches that file, parses it as a JSON array of strings, and adds the `.wip` class from `site/shared.css` to matching entries in the manual. If the file is missing, empty, or malformed, the code silently does nothing.

Each array item is a whitespace-separated sequence of Quikscript letter names using the names from `site/shared.js` (`Day`, `Roe`, `Eight`, `J'ai`, and so on). Tokens may also include a `.variant` suffix when you want to match a specific `data-expect` token variant.

```json
[
  "Day Roe Eight",
  "Way.alt At"
]
```

The matcher scans `[data-expect]`, `.pairings td`, `.pairings dd`, `.word-list td`, and `.word-list dd`. A sequence marks an element as WIP if either its `data-expect` contains the requested tokens in order or its text content contains the raw PUA substring built from the base letter names.

With no active WIP list, `site/wip.json` stays checked in as `[]`, so adding an entry is a one-line edit.

## Understanding

Really, you ought to ask an LLM set to maximum thinking mode to give you the 10¢ tour. Anything written down here would probably be out of date by the time you read it.
