"""Recon probe for the week-one prototype: dump every compiled variant for qsIt/qsTea/qsMay (plus the candidate ligatures), then shape a battery of sequences through the built Senior font and print the chosen variant per position with its anchors.

Run with: uv run python prototype/recon/probe_families.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set

FONT_PATH = ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
FAMILIES_OF_INTEREST = ("qsIt", "qsTea", "qsMay", "qsTea_qsOy", "qsOut_qsTea")

with (ROOT / "postscript_glyph_names.yaml").open() as f:
    PS_NAMES = yaml.safe_load(f)

ZWNJ = "‌"


def char_for(token: str) -> str:
    if token == "zwnj":
        return ZWNJ
    if token == "space":
        return " "
    return chr(PS_NAMES[token])


def dump_inventory(meta_map) -> None:
    for name in sorted(meta_map):
        base = name.split(".", 1)[0]
        if base not in FAMILIES_OF_INTEREST:
            continue
        meta = meta_map[name]
        bits = [name]
        if meta.entry:
            bits.append(f"entry={list(meta.entry)}")
        elif meta.entry_explicitly_none:
            bits.append("entry=null")
        if meta.entry_curs_only:
            bits.append(f"entry_curs_only={list(meta.entry_curs_only)}")
        if meta.exit:
            bits.append(f"exit={list(meta.exit)}")
        if meta.traits:
            bits.append(f"traits={list(meta.traits)}")
        if meta.modifiers:
            bits.append(f"modifiers={list(meta.modifiers)}")
        if getattr(meta, "gate_feature_behind", None):
            bits.append(f"gated={meta.gate_feature_behind}")
        print("  ".join(bits))


def shape(font: hb.Font, tokens: list[str], features: dict[str, bool]) -> list[str]:
    buf = hb.Buffer()
    buf.add_str("".join(char_for(t) for t in tokens))
    buf.guess_segment_properties()
    hb.shape(font, buf, features)
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


SEQUENCES: list[list[str]] = [
    ["qsIt"],
    ["qsTea"],
    ["qsMay"],
    ["qsIt", "qsIt"],
    ["qsIt", "qsTea"],
    ["qsIt", "qsMay"],
    ["qsTea", "qsIt"],
    ["qsTea", "qsTea"],
    ["qsTea", "qsMay"],
    ["qsMay", "qsIt"],
    ["qsMay", "qsTea"],
    ["qsMay", "qsMay"],
    ["qsTea", "qsIt", "qsTea"],
    ["qsTea", "qsIt", "qsMay"],
    ["qsTea", "qsIt", "qsIt"],
    ["qsMay", "qsIt", "qsTea"],
    ["qsMay", "qsIt", "qsMay"],
    ["qsIt", "qsMay", "qsTea"],
    ["qsIt", "qsMay", "qsIt"],
    ["qsIt", "qsTea", "qsIt"],
    ["qsIt", "qsTea", "qsMay"],
    ["qsTea", "qsMay", "qsIt"],
    ["qsTea", "qsMay", "qsTea"],
    ["qsMay", "qsTea", "qsIt"],
    ["qsMay", "qsTea", "qsMay"],
    ["qsMay", "qsMay", "qsMay"],
    ["qsIt", "qsIt", "qsIt"],
    ["qsTea", "qsIt", "qsTea", "qsIt"],
    ["qsMay", "qsIt", "qsMay", "qsIt"],
    ["qsIt", "zwnj", "qsTea"],
    ["qsIt", "zwnj", "qsMay"],
    ["qsTea", "zwnj", "qsIt"],
    ["qsMay", "zwnj", "qsIt"],
    ["qsMay", "zwnj", "qsTea"],
    ["qsTea", "zwnj", "qsMay"],
    ["qsIt", "space", "qsMay"],
    ["qsTea", "space", "qsIt"],
    ["qsMay", "zwnj", "qsIt", "qsTea"],
    ["qsTea", "qsIt", "zwnj", "qsMay"],
    ["qsTea", "qsOy"],
    ["qsIt", "qsTea", "qsOy"],
    ["qsMay", "qsTea", "qsOy"],
    ["qsTea", "qsTea", "qsOy"],
    ["qsIt", "qsIt", "qsTea", "qsOy"],
    ["qsOut", "qsTea"],
    ["qsMay", "qsOut", "qsTea"],
    ["qsIt", "qsOut", "qsTea"],
    ["qsOut", "qsTea", "qsIt"],
    ["qsOut", "qsTea", "qsMay"],
    ["qsExcite", "qsTea", "qsOy"],
]


def main() -> None:
    data = load_glyph_data(ROOT / "glyph_data")
    meta_map = compile_glyph_set(data, "senior").glyph_meta
    print("== inventory ==")
    dump_inventory(meta_map)

    blob = hb.Blob.from_file_path(str(FONT_PATH))
    font = hb.Font(hb.Face(blob))

    for label, features in (("default", {}), ("ss03", {"ss03": True}), ("ss04", {"ss04": True})):
        print(f"\n== shaping ({label}) ==")
        for tokens in SEQUENCES:
            names = shape(font, tokens, features)
            annotated = []
            for n in names:
                meta = meta_map.get(n)
                if meta is None:
                    annotated.append(n)
                    continue
                anchor_bits = []
                if meta.entry:
                    anchor_bits.append("en" + ",".join(f"({x},{y})" for x, y in meta.entry))
                if meta.exit:
                    anchor_bits.append("ex" + ",".join(f"({x},{y})" for x, y in meta.exit))
                annotated.append(n + ("[" + " ".join(anchor_bits) + "]" if anchor_bits else ""))
            print(f"{' '.join(tokens)} -> {' | '.join(annotated)}")


if __name__ == "__main__":
    main()
