---
name: bump-minor
description: Bump the font's minor version in glyph_data/metadata.yaml and pyproject.toml, refresh uv.lock, and open a changelog entry in FONTLOG.md. Use when the user asks to bump the minor version.
---

1. Increment the minor version in both files. The formats differ:
   - `glyph_data/metadata.yaml` uses `X.YYY` — increment by `.001` (e.g., `10.000` to `10.001`).
   - `pyproject.toml` uses `X.Y.Z` — increment the middle number (e.g., `10.0.0` to `10.1.0`).
2. Refresh the lockfile:

   ```sh
   uv sync
   ```

3. Ensure `FONTLOG.md` has a `### X.YYY` heading for the new version directly under `## Changelog`, matching the metadata format. Leave it empty if there is nothing to record yet.
