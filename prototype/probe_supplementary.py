"""Supplementary probes against today's built Senior font, required by prototype/PLAN.md section 1: pin the qsTea_qsOy ligature's forward seams (followed by qsIt / qsTea / qsMay / qsOy), the locked twins' exit-side settling (ZWNJ at the backtrack slot), word-edge ZWNJ, formation blocking across ZWNJ, and bare-qsOy behavior. The printed outcomes are recorded as provenance comments in prototype/spec.py.

Run with: uv run python prototype/probe_supplementary.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import uharfbuzz as hb
import yaml
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

FONT_PATH = ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

with (ROOT / "postscript_glyph_names.yaml").open() as f:
    PS_NAMES = yaml.safe_load(f)


def char_for(token: str) -> str:
    if token == "zwnj":
        return "‌"
    if token == "space":
        return " "
    return chr(PS_NAMES[token])


SEQUENCES: list[list[str]] = [
    ["qsOy"],
    ["qsOy", "qsIt"],
    ["qsOy", "qsTea"],
    ["qsOy", "qsMay"],
    ["qsIt", "qsOy"],
    ["qsMay", "qsOy"],
    ["qsTea", "qsOy", "qsIt"],
    ["qsTea", "qsOy", "qsTea"],
    ["qsTea", "qsOy", "qsMay"],
    ["qsTea", "qsOy", "qsOy"],
    ["qsIt", "qsTea", "qsOy", "qsIt"],
    ["qsMay", "qsTea", "qsOy", "qsMay"],
    ["qsTea", "qsOy", "qsTea", "qsOy"],
    ["qsTea", "zwnj", "qsOy"],
    ["zwnj", "qsTea", "qsIt"],
    ["zwnj", "qsIt", "qsMay"],
    ["zwnj", "qsMay", "qsIt"],
    ["zwnj", "qsMay", "qsTea"],
    ["zwnj", "qsTea", "qsMay"],
    ["zwnj", "qsIt"],
    ["zwnj", "qsTea"],
    ["zwnj", "qsMay"],
    ["qsIt", "zwnj"],
    ["qsTea", "zwnj"],
    ["qsMay", "zwnj"],
    ["qsTea", "qsIt", "qsTea", "qsOy"],
    ["qsMay", "qsIt", "qsTea", "qsOy"],
    ["qsIt", "qsMay", "qsTea", "qsOy"],
]


def main() -> None:
    tt = TTFont(FONT_PATH)
    font = hb.Font(hb.Face(hb.Blob.from_file_path(str(FONT_PATH))))
    for label, features in (("default", {}), ("ss03", {"ss03": True})):
        print(f"== shaping ({label}) ==")
        for tokens in SEQUENCES:
            buf = hb.Buffer()
            buf.add_str("".join(char_for(t) for t in tokens))
            buf.guess_segment_properties()
            hb.shape(font, buf, features)
            names = [tt.getGlyphName(info.codepoint) for info in buf.glyph_infos]
            print(f"{' '.join(tokens)} -> {' | '.join(names)}")
        print()


if __name__ == "__main__":
    main()
