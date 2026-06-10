"""Per-process shaping state: one hb.Font, one parallel TTFont for full glyph-name recovery (HarfBuzz's glyph_to_string truncates names to 63 bytes), and one reused hb.Buffer. Buffer-reuse invariant: glyph_infos and glyph_positions are materialized before shape() returns."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont


@dataclass(frozen=True)
class ShapeResult:
    names: tuple[str, ...]
    clusters: tuple[int, ...]
    positions: tuple[tuple[int, int, int], ...]


class Shaper:
    def __init__(self, font_path: str | Path):
        blob = hb.Blob.from_file_path(str(font_path))
        face = hb.Face(blob)
        self._font = hb.Font(face)
        self._tt_font = TTFont(str(font_path), lazy=True)
        self._buffer = hb.Buffer()

    def glyph_name(self, glyph_id: int) -> str:
        """Full compiled glyph name via TTFont, never hb.Font.glyph_to_string — HarfBuzz truncates names to 63 bytes and this font has longer ones."""
        return self._tt_font.getGlyphName(glyph_id)

    def shape(self, text: str, features: dict[str, bool] | None = None) -> ShapeResult:
        buffer = self._buffer
        buffer.clear_contents()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        hb.shape(self._font, buffer, features or {})
        names = tuple(self.glyph_name(info.codepoint) for info in buffer.glyph_infos)
        clusters = tuple(info.cluster for info in buffer.glyph_infos)
        positions = []
        for position in buffer.glyph_positions:
            if position.y_advance != 0:
                raise AssertionError(f"nonzero y_advance shaping {text!r}: {position.y_advance}")
            positions.append((position.x_offset, position.y_offset, position.x_advance))
        return ShapeResult(names=names, clusters=clusters, positions=tuple(positions))

    def shape_split(
        self,
        text: str,
        split_offsets: tuple[int, ...],
        features: dict[str, bool] | None = None,
    ) -> ShapeResult:
        """Shape each segment of text (split at the given character offsets) in its own buffer and concatenate, with clusters reported in whole-text coordinates. By construction no contextual lookup can fire across a split."""
        names: list[str] = []
        clusters: list[int] = []
        positions: list[tuple[int, int, int]] = []
        starts = (0, *split_offsets)
        ends = (*split_offsets, len(text))
        for start, end in zip(starts, ends):
            segment = self.shape(text[start:end], features)
            names.extend(segment.names)
            clusters.extend(cluster + start for cluster in segment.clusters)
            positions.extend(segment.positions)
        return ShapeResult(names=tuple(names), clusters=tuple(clusters), positions=tuple(positions))
