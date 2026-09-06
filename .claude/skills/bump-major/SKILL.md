---
name: bump-major
description: Bump the font's major version in glyph_data/metadata.yaml and pyproject.toml, refresh uv.lock, and open a changelog entry in FONTLOG.md. Use when the user asks to bump the major version.
---

1. Increment the major version in both files. The formats differ:
   - `glyph_data/metadata.yaml` uses `X.000` (e.g., `4.000`).
   - `pyproject.toml` uses `X.0.0` (e.g., `4.0.0`).
2. Refresh the lockfile:

   ```sh
   uv sync
   ```

3. Ensure `FONTLOG.md` has a `### X.000` heading for the new version directly under `## Changelog`, matching the metadata format. Leave it empty if there is nothing to record yet.
