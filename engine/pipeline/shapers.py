"""The shaper the compiled font is read through: `Shaper`, HarfBuzz over the font, which the conformance sweep shapes every text through with the font's `kern` feature on, and the kern inventory with it off (`kern=False`). Beside it, `isolated_overlay_labels` over `isolated_overlay_tokens` is the overlay configuration's output computed without shaping, which the conformance sweep expects under ss10."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from engine.pipeline import settle
from engine.pipeline.labels import _BOUNDARY_KIND_LABELS
from engine.pipeline.model import ResolvedSpec, ss10_copy_name


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

    def _shaped(self, text: str, features: frozenset[str], kern: bool = True):
        """Shape `text` into this shaper's one reused buffer and return it, with the `kern` feature on (HarfBuzz's default) or, with `kern` False, off. Because the buffer is reused, a shaper must not be shared across threads, and each caller copies what it needs before the next call clears the buffer."""
        hb = self._hb
        buf = self._buffer
        buf.clear_contents()
        # MONOTONE_CHARACTERS keeps each input character in its own cluster, so the ZWNJ slot stays identifiable.
        buf.cluster_level = hb.BufferClusterLevel.MONOTONE_CHARACTERS
        buf.add_str(text)
        buf.guess_segment_properties()
        settings = {tag: True for tag in features}
        if not kern:
            settings["kern"] = False
        hb.shape(self.hb_font, buf, settings)
        return buf

    def shape(self, text: str, features: frozenset[str], kern: bool = True) -> list[dict]:
        buf = self._shaped(text, features, kern)
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
