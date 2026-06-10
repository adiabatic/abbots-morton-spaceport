"""HarfBuzz shaping for the validation suite: one hb.Font plus a parallel TTFont glyph order for full-name recovery (HarfBuzz's glyph_to_string truncates compiled names at 63 bytes), one reused buffer per Shaper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont

from .classify import SeamClassifier
from .rowmodel import Row

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SENIOR_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"


@dataclass(frozen=True)
class ShapeResult:
    names: tuple[str, ...]
    clusters: tuple[int, ...]
    positions: tuple[tuple[int, int, int], ...]


class Shaper:
    def __init__(self, font_path: Path | str = SENIOR_FONT) -> None:
        blob = hb.Blob.from_file_path(str(font_path))
        self._font = hb.Font(hb.Face(blob))
        self._glyph_order = TTFont(str(font_path)).getGlyphOrder()
        self._buf = hb.Buffer()

    def glyph_name(self, glyph_id: int) -> str:
        """Full compiled glyph name via the TTFont glyph order, never hb.Font.glyph_to_string — HarfBuzz truncates names to 63 bytes and this font has longer ones."""
        return self._glyph_order[glyph_id]

    def shape(self, text: str, features: dict[str, bool] | None = None) -> ShapeResult:
        buf = self._buf
        buf.clear_contents()
        buf.add_str(text)
        buf.guess_segment_properties()
        if features:
            hb.shape(self._font, buf, features)
        else:
            hb.shape(self._font, buf)
        names = tuple(self.glyph_name(info.codepoint) for info in buf.glyph_infos)
        clusters = tuple(info.cluster for info in buf.glyph_infos)
        positions = []
        for pos in buf.glyph_positions:
            if pos.y_advance != 0:
                raise AssertionError(f"nonzero y_advance shaping {text!r}")
            positions.append((pos.x_offset, pos.y_offset, pos.x_advance))
        if any(b < a for a, b in zip(clusters, clusters[1:])):
            raise AssertionError(f"non-monotonic clusters shaping {text!r}: {clusters}")
        return ShapeResult(names, clusters, tuple(positions))

    def shape_split(
        self, text: str, split_offsets: list[int], features: dict[str, bool] | None = None
    ) -> ShapeResult:
        bounds = [0, *sorted(split_offsets), len(text)]
        names: list[str] = []
        clusters: list[int] = []
        positions: list[tuple[int, int, int]] = []
        for start, end in zip(bounds, bounds[1:]):
            if start >= end:
                continue
            segment = self.shape(text[start:end], features)
            names.extend(segment.names)
            clusters.extend(c + start for c in segment.clusters)
            positions.extend(segment.positions)
        return ShapeResult(tuple(names), tuple(clusters), tuple(positions))


def last_glyph_covering(clusters: tuple[int, ...], position: int) -> int:
    covering = -1
    for i, cluster in enumerate(clusters):
        if cluster <= position:
            covering = i
        else:
            break
    if covering < 0:
        raise ValueError(f"no glyph covers input position {position} (clusters={clusters})")
    return covering


def first_glyph_covering(clusters: tuple[int, ...], position: int) -> int:
    last = last_glyph_covering(clusters, position)
    value = clusters[last]
    first = last
    while first > 0 and clusters[first - 1] == value:
        first -= 1
    return first


def seams_for(result: ShapeResult, length: int, classifier: SeamClassifier) -> tuple[str, ...]:
    seams: list[str] = []
    for k in range(length - 1):
        left = last_glyph_covering(result.clusters, k)
        right_last = last_glyph_covering(result.clusters, k + 1)
        if left == right_last:
            seams.append("lig")
            continue
        right = first_glyph_covering(result.clusters, k + 1)
        seams.append(classifier.classify(result.names[left], result.names[right]))
    return tuple(seams)


def row_for(
    shaper: Shaper, classifier: SeamClassifier, text: str, features: dict[str, bool] | None = None
) -> Row:
    result = shaper.shape(text, features)
    return Row(
        codepoints=tuple(ord(c) for c in text),
        glyphs=result.names,
        clusters=result.clusters,
        seams=seams_for(result, len(text), classifier),
        positions=result.positions,
    )
