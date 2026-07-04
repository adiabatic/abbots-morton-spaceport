"""Manually re-shape a sample of equivalence-triage rows with raw uharfbuzz (no rebuild/ library code) to confirm each recorded divergence is a real behavior of today's font rather than a classifier artifact."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont

REPO_ROOT = Path(__file__).resolve().parents[2]
FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

CONFIG_FEATURES = {
    "default": {},
    "ss02": {"ss02": True},
    "ss03": {"ss03": True},
    "ss04": {"ss04": True},
    "ss05": {"ss05": True},
    "ss06": {"ss06": True},
    "ss07": {"ss07": True},
    "ss10": {"ss10": True},
    "ss02+ss03": {"ss02": True, "ss03": True},
    "ss06+ss07": {"ss06": True, "ss07": True},
    "ss02+ss03+ss05": {"ss02": True, "ss03": True, "ss05": True},
}

BOUNDARY = {"zwnj-vs-edge": ("prefix", "‌"), "space-vs-edge": ("prefix", " "), "edge-vs-zwnj": ("suffix", "‌"), "edge-vs-space": ("suffix", " ")}


def main() -> int:
    triage_path = Path(sys.argv[1])
    sample_n = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    blob = hb.Blob.from_file_path(str(FONT))
    font = hb.Font(hb.Face(blob))
    order = TTFont(str(FONT)).getGlyphOrder()

    def shape(text: str, features: dict) -> tuple[tuple[str, ...], tuple[int, ...]]:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(font, buf, features or None)
        return tuple(order[i.codepoint] for i in buf.glyph_infos), tuple(i.cluster for i in buf.glyph_infos)

    rows = [line.split("\t") for line in triage_path.read_text().splitlines() if line and not line.startswith("#")]
    random.seed(20260610)
    sample = random.sample(rows, min(sample_n, len(rows)))
    confirmed = refuted = 0
    for config, check, codepoints, baseline_glyphs, boundary_glyphs, *_rest in sample:
        features = CONFIG_FEATURES[config]
        text = "".join(chr(int(cp, 16)) for cp in codepoints.split(":"))
        side, boundary_char = BOUNDARY[check]
        bare_names, _ = shape(text, features)
        if side == "prefix":
            names, clusters = shape(boundary_char + text, features)
            portion = tuple(n for n, c in zip(names, clusters) if c >= 1)
        else:
            names, clusters = shape(text + boundary_char, features)
            portion = tuple(n for n, c in zip(names, clusters) if c < len(text))
        kind = _rest[-1]
        if kind == "glyph":
            ok = "|".join(bare_names) == baseline_glyphs and "|".join(portion) == boundary_glyphs and bare_names != portion
        else:
            ok = "|".join(bare_names) == baseline_glyphs and "|".join(portion) == boundary_glyphs
        status = "CONFIRMED" if ok else "REFUTED"
        confirmed += ok
        refuted += not ok
        print(f"{status} {config} {check} {codepoints} [{kind}] bare={'|'.join(bare_names)} boundary-portion={'|'.join(portion)}")
    print(f"\n{confirmed} confirmed, {refuted} refuted out of {len(sample)} sampled")
    return 1 if refuted else 0


if __name__ == "__main__":
    raise SystemExit(main())
