"""The two shapers the compiled M1 font is read through. `Shaper` is HarfBuzz over the font. `IsolatedOverlayShaper` is the overlay configuration's output computed without shaping, from `isolated_overlay_labels` over `isolated_overlay_tokens`. The conformance sweep in rebuild/pipeline/conform.py shapes every text through them, and the oracle's position comparison in rebuild/pipeline/oracle_positions.py shapes its rows through them.

They live apart from conform.py so that the position comparison's import closure, which `oracle_cache.POSITION_CODE_PATHS` names, holds the shapers and not the settlement walk, the settle memo, the crate driver, or the row store. An edit to one of those therefore keeps every stored position whose settled stream did not change (rebuild/pipeline/oracle_cache.py). rebuild/test_oracle_code_closure.py checks that the position comparison reaches none of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from rebuild.pipeline import settle
from rebuild.pipeline.labels import _BOUNDARY_KIND_LABELS
from rebuild.pipeline.model import ResolvedSpec, ss10_copy_name


class Shaper:
    def __init__(self, font_path: Path):
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont

        self._hb = hb
        self.font_path = Path(font_path)
        self.tt = TTFont(str(font_path))
        self.hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(font_path))))
        self.glyph_set = self.tt.getGlyphSet()
        self._outline_cache: dict[str, tuple] = {}
        self._buffer = hb.Buffer()

    def _shaped(self, text: str, features: frozenset[str]):
        """Shape `text` into this shaper's one reused buffer and return it. `shape` and `positions` both read from here, so they see the same slots. Because the buffer is reused, a shaper must not be shared across threads, and each caller copies what it needs before the next call clears the buffer."""
        hb = self._hb
        buf = self._buffer
        buf.clear_contents()
        # MONOTONE_CHARACTERS keeps each input character in its own cluster, so the ZWNJ slot stays identifiable.
        buf.cluster_level = hb.BufferClusterLevel.MONOTONE_CHARACTERS
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {tag: True for tag in features})
        return buf

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        buf = self._shaped(text, features)
        return [
            {
                "name": self.tt.getGlyphName(info.codepoint),
                "gid": info.codepoint,
                "cluster": info.cluster,
                "x_advance": pos.x_advance,
                "x_offset": pos.x_offset,
                "y_offset": pos.y_offset,
            }
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
        ]

    def positions(self, text: str, features: frozenset[str]) -> list[tuple[int, int, int]]:
        """Each slot's `(x_offset, y_offset, x_advance)` from the same shaping `shape` performs. The position comparison reads nothing else, and skipping the per-slot fontTools name lookup is what makes this cheaper than `shape`."""
        buf = self._shaped(text, features)
        return [(pos.x_offset, pos.y_offset, pos.x_advance) for pos in buf.glyph_positions]

    def advance(self, glyph_name: str) -> int:
        """The glyph's `hmtx` advance: how far the pen moves at a slot nothing positions, which is what the overlay sweep expects at every slot."""
        return self.tt["hmtx"][glyph_name][0]

    def outline_signature(self, glyph_name: str) -> tuple:
        cached = self._outline_cache.get(glyph_name)
        if cached is None:
            from fontTools.pens.recordingPen import RecordingPen

            pen = RecordingPen()
            self.glyph_set[glyph_name].draw(pen)
            cached = tuple(pen.value)
            self._outline_cache[glyph_name] = cached
        return cached


def isolated_overlay_labels(spec: ResolvedSpec, tokens: Sequence[settle.RightToken]) -> list[str]:
    """The glyph names an `overlay: isolated` taste set renders for raw tokens: each letter's anchor-free `.ss10` copy, and each boundary token's own glyph. There is one name per raw token, because the ss10 input substitution replaces every letter with its copy before formation, so no ligature forms."""
    return [
        ss10_copy_name(token.letter) if token.kind == "letter" else _BOUNDARY_KIND_LABELS[token.kind]
        for token in tokens
    ]


def isolated_overlay_tokens(spec: ResolvedSpec, text: str) -> list[settle.RightToken]:
    return settle.tokens_from_codepoints(spec, [ord(ch) for ch in text])


class IsolatedOverlayShaper:
    """HarfBuzz's output under the overlay, computed without shaping: each letter becomes its copy, each boundary character its glyph, and each slot sits at zero offset with its `hmtx` advance. The position comparison uses it for the overlay configuration. That is valid because the conformance sweep checks every overlay text up to `OVERLAY_MAX_LENGTH` against this output, and cursive attachment is pairwise, so a glyph no pair moves is moved by no text. The font lowers the namer dot before a Short copy, but this class always names it `periodcentered`. The constructor therefore raises when the two dot glyphs have different advances, since pen positions would then depend on more than the text."""

    def __init__(self, font_path: Path, spec: ResolvedSpec):
        from fontTools.ttLib import TTFont

        self.spec = spec
        self.font_path = Path(font_path)
        self.tt = TTFont(str(font_path))
        self._advances = {name: metrics[0] for name, metrics in self.tt["hmtx"].metrics.items()}
        dot, lowered = "periodcentered", "periodcentered.lowered"
        if lowered in self._advances and self._advances[lowered] != self._advances.get(dot):
            raise ValueError(
                f"{font_path}: {dot} advances {self._advances.get(dot)} but {lowered} advances {self._advances[lowered]}, so the overlay's pen positions are not a function of the text alone"
            )

    def _labels(self, text: str) -> list[str]:
        """The glyph label of each slot of `text` under the overlay, which `shape` and `positions` both use."""
        return isolated_overlay_labels(self.spec, isolated_overlay_tokens(self.spec, text))

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        labels = self._labels(text)
        return [
            {
                "name": name,
                "gid": self.tt.getGlyphID(name),
                "cluster": cluster,
                "x_advance": self._advances[name],
                "x_offset": 0,
                "y_offset": 0,
            }
            for cluster, name in enumerate(labels)
        ]

    def positions(self, text: str, features: frozenset[str]) -> list[tuple[int, int, int]]:
        """Each slot's position from the same labels `shape` uses: zero offset and the glyph's `hmtx` advance."""
        return [(0, 0, self._advances[name]) for name in self._labels(text)]
