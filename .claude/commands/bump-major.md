Bump the major version number in both @glyph_data/metadata.yaml and @pyproject.toml, then run `uv sync`.

Version formats differ between files:

- glyph_data/metadata.yaml uses `X.000` format (e.g., `4.000`)
- pyproject.toml uses `X.0.0` format (e.g., `4.0.0`)
- FONTLOG.md uses `X.000` format (e.g., `4.000`)

After bumping the version, ensure FONTLOG.md has a changelog entry for the new version. Add a new `### X.000` section after the `## Changelog` heading if one doesn't already exist. Leave the entry empty (no bullet points) if there's nothing to add yet.
