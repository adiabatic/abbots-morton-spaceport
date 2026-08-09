"""Shared loader for K3: rebuild the repo's real InkComparator over pre-extracted shaped runs.

No HarfBuzz and no fontTools are involved: `bench-the-rebuild/fixtures/shaped-runs.jsonl` already holds
the (glyph name, x_offset, y_offset, x_advance) run for both fonts under each config, and
`outlines-{before,after}.json` holds the decomposed per-glyph outlines. We instantiate the genuine
`rebuild.review.ink.InkComparator` via object.__new__ and hand it a stub Shaper + a prefilled
OutlineCache, so every timed Python number below runs the repo's own method bodies.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "tmp" / "perf" / "attr-overhead" / "data"


def _load_outlines(path: Path) -> dict[str, tuple]:
    raw = json.loads(path.read_text())
    return {
        name: tuple(
            (operator, tuple(None if p is None else (p[0], p[1]) for p in points))
            for operator, points in value
        )
        for name, value in raw.items()
    }


def load_rows() -> list[dict]:
    rows = []
    with open(DATA / "shaped-runs.jsonl") as fh:
        for line in fh:
            rows.append(json.loads(line))
    return rows


def feature_key(features: dict | None) -> tuple:
    return tuple(sorted(name for name, on in (features or {}).items() if on))


class StubShapeResult:
    __slots__ = ("names", "clusters", "positions")

    def __init__(self, names, positions):
        self.names = names
        self.clusters = ()
        self.positions = positions


class StubShaper:
    """Serves the pre-extracted run for (text, enabled-feature-tuple). Kern-neutral shaping adds
    `kern: False`, which `feature_key` drops, so the key is exactly the config token's tag set."""

    def __init__(self):
        self.table: dict[tuple, StubShapeResult] = {}

    def shape(self, text, features=None):
        return self.table[(text, feature_key(features))]


def build_comparator():
    from rebuild.review.ink import InkComparator, OutlineCache

    before_outlines = _load_outlines(DATA / "outlines-before.json")
    after_outlines = _load_outlines(DATA / "outlines-after.json")
    rows = load_rows()

    sides = {}
    for side, outlines in (("before", before_outlines), ("after", after_outlines)):
        cache = object.__new__(OutlineCache)
        cache._glyph_set = None
        cache._cache = dict(outlines)
        sides[side] = (StubShaper(), cache)

    work = []
    for row in rows:
        text = "".join(chr(cp) for cp in row["cps"])
        tags = () if row["config"] == "default" else tuple(sorted(row["config"].split("+")))
        for side in ("before", "after"):
            names = tuple(entry[0] for entry in row[side])
            positions = tuple((entry[1], entry[2], entry[3]) for entry in row[side])
            sides[side][0].table[(text, tags)] = StubShapeResult(names, positions)
        work.append((row["unit"], text, row["config"]))

    comparator = object.__new__(InkComparator)
    comparator._sides = sides
    return comparator, work, before_outlines, after_outlines, rows
