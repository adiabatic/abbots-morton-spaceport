# Cleanup passes

Changes that normal authoring can skip. Make them in a separate cleanup pass.

## Consolidate repeated `select` / `derive` reference lists

When the same list of `{family: qsX}` entries, or the same list with one or two differences, appears in more than one `select` / `derive` block in `glyph_data/quikscript.yaml`, move it to a top-level entry under `context_sets` and reference it inline as `{context_set: some_name}`.

A context set may reference other context sets, so a larger list can be built from smaller ones when that keeps the source clearer.

A consolidation must not change the generated feature code. Check it with the recipe in `doc/quikscript-yaml-conventions.md` (“Proving a selector change is a pure cleanup”): capture the Senior feature code before the change, rebuild after it, and diff. A nonempty diff means some selector now resolves differently, for example because two lists differ by an entry or a composition changed an order. That is a shaping change, so investigate it before committing.
