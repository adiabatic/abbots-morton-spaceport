"""Fixtures for the old engine's tests under test/. `shaping_env` holds the old build's Senior and Junior fonts under site/ with the anchor maps of their compiled glyph metadata (`test_shaping.build_anchor_map`), which the tests that read old glyph names or anchors check against. It stands on the root conftest's `built_fonts`, which runs `make all` once per session.

It lives here and not in the root conftest because the root conftest is in every rebuild test's import closure (`rebuild/tools/contracts_closure.py`), and test/test_shaping.py reaches the old engine's tools/ modules.
"""

from typing import Any

import pytest

_shaping_cache: dict[str, Any] = {}


@pytest.fixture(scope="session")
def shaping_env(built_fonts: None) -> dict[str, Any]:
    if "fonts" not in _shaping_cache:
        from test_shaping import build_anchor_map, load_font

        fonts = {}
        anchor_maps = {}
        potentials = {}
        for variant in ("senior", "junior"):
            fonts[variant] = load_font(variant)
            anchors, potential = build_anchor_map(variant)
            anchor_maps[variant] = anchors
            potentials[variant] = potential
        _shaping_cache["fonts"] = fonts
        _shaping_cache["anchor_maps"] = anchor_maps
        _shaping_cache["potentials"] = potentials
    return _shaping_cache
