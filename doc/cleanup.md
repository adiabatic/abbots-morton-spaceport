# Cleanup passes

Things that don't need to happen during normal authoring, but are worth doing in a dedicated cleanup pass.

## Consolidate repeated `select` / `derive` reference lists

When the same list of `{family: qsX}` entries (or the same list with one or two differences) shows up in more than one `select` / `derive` block in `glyph_data/quikscript.yaml`, lift it to a top-level entry under `context_sets` and reference it inline as `{context_set: some_name}`.

`context_sets` may themselves reference other context sets, so a larger list can be composed from smaller ones when that keeps the source clearer.

A consolidation is supposed to be a pure refactor, so prove it: run `make test` before any changes and capture the generated FEA (and any other build artifacts you care about); run `make test` again after consolidating; diff the artifacts. If the diff is non-empty, the substitution wasn't byte-equivalent (lists differ by an entry, composition changed an ordering, or some other selector started resolving differently) and the change is a behavior shift rather than a refactor — investigate before committing.
