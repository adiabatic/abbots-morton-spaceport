# Abbots Morton Spaceport developer guide

## Making it go

```bash
make all
```

`make all` publishes the six fonts into `site/` (`engine/tools/build_fonts.py`): Mono and Junior built from the rune files and the other glyph files, and Senior copied from run_build's build under `engine/out/build/`, which it runs first when that build is stale. Dependencies are managed with `uv` and defined in `pyproject.toml`. The Senior's table build runs in the Rust crate under `engine/kernel-rs/`, so the tree needs a Rust toolchain (`cargo`, from [rustup](https://rustup.rs)) besides `uv`: without it you cannot build Senior, settle a window, or run the engine suite. `make kernel-build` builds the crate in release mode, and the pipeline builds it before it first runs it.

`make serve` serves `site/` on `http://localhost:7293/` and reloads the open pages when a font changes.

## Where the letters live

- Each Quikscript letter and ligature is a rune file under `glyph_data/runes/`, whose grammar is `engine/schema/rune.schema.json`: its ductus, its stances with their bitmaps and join surfaces, its policy records and its kerning. `doc/engine-design.md` explains the model.
- The Latin, numeral, punctuation, composite, symbol and Shavian glyphs, the font metadata and the Latin kerning rules are the other `glyph_data/*.yaml` files, and `postscript_glyph_names.yaml` maps glyph names to code points.
- The variation sequences and the stylistic sets are in the registry, `glyph_data/script.yaml`, the one file in `glyph_data/` that holds no glyphs.
- [doc/glyph-names.md](doc/glyph-names.md) maps each letter's name to its code point and PostScript name, and [doc/abbreviations.md](doc/abbreviations.md) lists the standard Quikscript abbreviations.

## Testing

`doc/testing.md` maps each kind of change to the gate that checks it. In short: `make test` checks the published fonts, the `data-expect` pins on the site pages among them (`doc/data-expect.md`); `make test-engine` checks the pipeline under `engine/`; and `uv run python -m engine.pipeline.run_build` rebuilds Senior's tables and font after a rune or pipeline edit.

Open `site/index.html` (through `make serve`) to test the fonts interactively. For the repeatable workflow for finding Quikscript words in `site/the-manual.html` that are missing `data-expect` coverage elsewhere in the file, see [doc/checking-data-expect-coverage.md](doc/checking-data-expect-coverage.md).

## Reviewing an edit

`make review-cycle` rebuilds what an edit touches, runs the gates, and serves the review app on `http://localhost:7294/`, whose Queue shows what the edit changed against the accepted build; `make review-accept ARGS=--apply` accepts the changes once each has a verdict. `doc/review-cycle.md` is the runbook, and `make artifact-cycle` is the same verification without the server, for commit time.

## Cutting a release

The fonts are built here and GitHub publishes them: the Senior's table build needs more memory than a GitHub-hosted runner has, so `.github/workflows/deploy.yml` builds nothing and publishes the fonts a draft release holds.

1. Bump the version with the bump-minor or bump-major skill, which opens a `### <version>` heading in `FONTLOG.md`.
2. Fill that section with what changed for the fonts' users (the summarize-for-FONTLOG command drafts it). It becomes the release notes.
3. Commit, with the commit-time gates (`make artifact-cycle`), and push `master`.
4. Run `make all`, so that `site/` holds the six fonts of that commit, then `make draft-release`, a dry run that prints its checks and what it would upload. `make draft-release ARGS=--apply` uploads the six fonts and their manifest, `fonts.json`, to a draft release for `release/<version>`, replacing any draft for that tag; `engine/tools/release_fonts.py`'s module docstring lists what it refuses.
5. Run the two commands it prints, `git tag release/<version> <commit>` and `git push origin release/<version>`. The push runs the workflow, which checks the fonts against `fonts.json` and the tagged commit, makes the WOFF2 files, deploys the site, attaches `abbots-morton-spaceport-<version>.zip` and publishes the release with the `FONTLOG.md` section as its notes.

`site/print.pdf`, which `make all` typesets, is not deployed: git ignores it and no page links it.

Running the workflow by hand from the Actions page redeploys the site with the fonts of the latest published release, or of the release its `tag` input names (`release/12.000` or later).

### Rehearsing the release path

A rehearsal runs the workflow's GitHub steps on the tip of `master` without deploying the site, publishing a release or pushing a tag, so it needs no new version. It refuses what a release refuses, but for the refusals that only protect a release (a published release for the version, the version's `FONTLOG.md` section, the version's tags), which it prints as `relaxed`, and it needs HEAD to be the tip of a branch of origin, since the run checks that tip out.

1. `make draft-release ARGS='--rehearse --apply'` uploads the six fonts and `fonts.json` through the release's own calls to a draft for `rehearsal/<version>-<commit>`, a tag the workflow's push trigger never matches. Bare `ARGS=--rehearse` is its dry run.
2. Run the command it prints, `gh workflow run deploy.yml --ref master -f rehearse=true -f tag=rehearsal/<version>-<commit>`, and follow the run with `gh run watch`. Once it has passed, re-running its `release` job (`gh run rerun <run-id> --job <job-id>`, with the job's id from `gh run view <run-id> --json jobs`) replaces the zip the first attempt attached, as a rerun after a failed publish does.
3. `make draft-release ARGS='--rehearse --delete --apply'` deletes the rehearsal drafts, each only once it reads back as a draft whose tag starts with `rehearsal/`. Bare `ARGS='--rehearse --delete'` lists them.

The run proves what a tag push does on GitHub short of its public effects: that the workflow's token lists the draft, that the fonts download by asset id and match `fonts.json` and the commit the run checks out, the WOFF2 conversion, the assembled site (the run's `github-pages` artifact, which GitHub keeps for a day) and the release files (`font-files`), the release notes from `FONTLOG.md`, the zip, and its upload to the draft by the draft's id. It cannot prove the tag push's trigger, the Pages deployment or the `PATCH` that publishes the draft, which run only on a tag push.

## Understanding

Really, you ought to ask an LLM set to maximum thinking mode to give you the 10¢ tour. Anything written down here would probably be out of date by the time you read it.
