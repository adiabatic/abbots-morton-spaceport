"""The two site fonts the rebuild suite shapes against and the surface's `fonts` stamp component hashes: the compiled Senior and Junior faces under site/. A leaf, so the root conftest can ask whether they are present ahead of a rebuild-only run without importing rebuild.pipeline into every rebuild test's closure (`rebuild.tools.cycle_paths` says why that matters); `fingerprint.font_paths` is this function."""

from __future__ import annotations

from pathlib import Path


def font_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    return [
        root / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
        root / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf",
    ]
