Check which glyphs in @TODO.md are already implemented in the YAML files under `glyph_data/`. For each entry in the TODO, grep for the glyph name (both the `uniXXXX` form and common PostScript names like `ellipsis`, `radical`, `trademark`, etc.) across all `glyph_data/*.yaml` files, including `.prop` variants.

Report which TODO entries are stale (already have a `.prop` variant in the YAML) and which are genuinely still missing. Offer to remove the stale entries.
