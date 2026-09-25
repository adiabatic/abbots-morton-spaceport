"""The paths of the two compiled site fonts under site/, Senior and Junior. The rebuild suite shapes against them, and the surface's `fonts` stamp component hashes them (`fingerprint.font_paths` is this function). The module imports nothing from the repo, so the root conftest can check that the fonts exist before a rebuild-only run without adding rebuild.pipeline to every rebuild test's closure; `rebuild.tools.cycle_paths` explains why that matters."""

from __future__ import annotations

from pathlib import Path


def font_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    return [
        root / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
        root / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf",
    ]
