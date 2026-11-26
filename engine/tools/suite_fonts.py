"""The fonts the font suite checks: the six under site/ that `engine/tools/build_fonts.py` publishes, which the root conftest publishes at the start of a session that collects the suite and reads here, and the glyph-facts sidecar the `data-expect` checker reads for the Senior, which stays beside the build under engine/out/build. gate:make-test's key hashes what they are published from (`artifact_cycle.make_test_font_lines`).

The module imports nothing from the repo, so the root conftest can name the fonts without adding engine.pipeline to every rebuild test's closure (`engine.tools.cycle_paths` explains why that matters). The names restate `build_fonts.SITE_DIR`, `build_family.font_file_name` and `glyph_facts.FILE_NAME`, and engine/test_build_fonts.py holds them to those.
"""

from __future__ import annotations

from pathlib import Path

FONTS_DIR = Path("site")
# Each pin font's file by the run font it shapes.
FONT_FILES = {
    "senior": "AbbotsMortonSpaceportSansSenior-Regular.otf",
    "junior": "AbbotsMortonSpaceportSansJunior-Regular.otf",
}
SENIOR_GLYPH_FACTS = Path("engine") / "out" / "build" / "Senior.glyph-facts.json"


def fonts_dir(repo_root: Path) -> Path:
    return Path(repo_root) / FONTS_DIR


def font_paths(repo_root: Path) -> dict[str, Path]:
    """Return each pin font's path by the run font it shapes (`senior`, `junior`)."""
    return {font: fonts_dir(repo_root) / name for font, name in FONT_FILES.items()}


def glyph_facts_path(repo_root: Path) -> Path:
    return Path(repo_root) / SENIOR_GLYPH_FACTS
