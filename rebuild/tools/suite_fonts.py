"""The rebuilt fonts the font suite checks the `data-expect` pins against: Senior and Junior Regular under rebuild/out/fonts/, as `rebuild/tools/build_fonts.py` writes them, and the glyph-facts sidecar it copies beside the Senior. The root conftest builds them at the start of a session that collects the pin pages and reads them here, and gate:make-test's key hashes them (`artifact_cycle.make_test_font_lines`).

The module imports nothing from the repo, so the root conftest can name the fonts without adding rebuild.pipeline to every rebuild test's closure (`rebuild.tools.cycle_paths` explains why that matters). The names restate `build_fonts.DEFAULT_OUT_DIR`, `build_family.font_file_name` and `check.glyph_facts_path`, and rebuild/test_build_fonts.py holds them to those.
"""

from __future__ import annotations

from pathlib import Path

FONTS_DIR = Path("rebuild") / "out" / "fonts"
FONT_FILES = {
    "senior": "AbbotsMortonSpaceportSansSenior-Regular.otf",
    "junior": "AbbotsMortonSpaceportSansJunior-Regular.otf",
}
SENIOR_GLYPH_FACTS = "AbbotsMortonSpaceportSansSenior.glyph-facts.json"
# The fonts the session builds, as `build_fonts --fonts` names them.
BUILT_FONTS = ("junior", "senior")


def fonts_dir(repo_root: Path) -> Path:
    return Path(repo_root) / FONTS_DIR


def font_paths(repo_root: Path) -> dict[str, Path]:
    """Return each pin font's path by the run font it shapes (`senior`, `junior`)."""
    return {font: fonts_dir(repo_root) / name for font, name in FONT_FILES.items()}


def glyph_facts_path(repo_root: Path) -> Path:
    return fonts_dir(repo_root) / SENIOR_GLYPH_FACTS
