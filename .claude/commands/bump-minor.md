Bump the minor version number in both @glyph_data/metadata.yaml and @pyproject.toml, then run `uv sync`.

Version formats differ between files:

- glyph_data/metadata.yaml uses `X.YYY` format — increment by `.001` (e.g., `10.000` → `10.001`)
- pyproject.toml uses `X.Y.Z` format — increment the middle number (e.g., `10.0.0` → `10.1.0`)
- FONTLOG.md uses the same format as metadata.yaml (e.g., `10.001`)

After bumping the version, ensure FONTLOG.md has a changelog entry for the new version. Add a new `### X.YYY` section after the `## Changelog` heading if one doesn't already exist. Leave the entry empty (no bullet points) if there's nothing to add yet.
