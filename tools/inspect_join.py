"""Inspect cursive joins for a sequence of Quikscript glyphs.

Examples:

    uv run python tools/inspect_join.py qsHe qsRoe
    uv run python tools/inspect_join.py qsHe qsRoe qsAt --features ss03
    uv run python tools/inspect_join.py --variants qsRoe

Reads `test/AbbotsMortonSpaceportSansSenior-Regular.otf` (built by `make`) for
HarfBuzz shaping, and `glyph_data/` for compiled metadata. Prints the chosen
variant per input position, every variant's anchors, the join Y (or the
mismatch when none), and the bitmap-row gap math used by the analyzer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import JoinGlyph
from quikscript_join_analysis import (
    _bitmap_join_gap,
    _bitmap_row_at_y,
    _effective_entry_x,
    _effective_exit_x,
    _ink_bounds_at_y,
)

FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"


def _font() -> hb.Font:
    blob = hb.Blob.from_file_path(str(FONT_PATH))
    return hb.Font(hb.Face(blob))


def _char_for_family(family: str) -> str:
    with PS_NAMES_PATH.open() as f:
        ps_names = yaml.safe_load(f)
    if family not in ps_names:
        raise SystemExit(f"unknown family: {family}")
    return chr(ps_names[family])


def _shape(text: str, features: dict[str, bool]) -> list[str]:
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features)
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


def _format_anchor_list(anchors: tuple[tuple[int, int], ...]) -> str:
    if not anchors:
        return "—"
    return ", ".join(f"({x}, {y})" for x, y in anchors)


def _print_glyph(meta: JoinGlyph) -> None:
    print(f"  {meta.name}")
    print(f"    base={meta.base_name}  family={meta.family}")
    print(f"    entry: {_format_anchor_list(meta.entry)}")
    if meta.entry_curs_only:
        print(f"    entry_curs_only: {_format_anchor_list(meta.entry_curs_only)}")
    print(f"    exit:  {_format_anchor_list(meta.exit)}")
    if meta.exit_ink_y is not None:
        print(f"    exit_ink_y: {meta.exit_ink_y}")
    if meta.entry_explicitly_none:
        print(f"    entry_explicitly_none: True")
    if meta.generated_from:
        print(f"    generated_from: {meta.generated_from}  ({meta.transform_kind})")
    if meta.bitmap:
        top_y = meta.y_offset + len(meta.bitmap) - 1
        for index, row in enumerate(meta.bitmap):
            y = top_y - index
            text = row if isinstance(row, str) else "".join("#" if v else " " for v in row)
            marker = ""
            if any(anchor[1] == y for anchor in meta.entry):
                marker += " <- entry"
            if any(anchor[1] == y for anchor in meta.exit):
                marker += " <- exit" if not marker else " + exit"
            print(f"    y={y:>2}  \"{text}\"{marker}")


def _gap_explanation(left: JoinGlyph, right: JoinGlyph, y: int) -> None:
    left_anchor = next((a for a in left.exit if a[1] == y), None)
    right_anchor = next((a for a in right.entry if a[1] == y), None)
    if left_anchor is None or right_anchor is None:
        print(f"    no anchor pair at y={y}")
        return
    left_y_for_ink = left.exit_ink_y if left.exit_ink_y is not None else y
    left_bounds = _ink_bounds_at_y(left, left_y_for_ink)
    right_bounds = _ink_bounds_at_y(right, y)
    eff_left_x = _effective_exit_x(left, left_anchor[0], right.family)
    eff_right_x = _effective_entry_x(right, right_anchor[0], left.family)
    gap = _bitmap_join_gap(
        left, left_anchor, right, right_anchor,
        left_family=left.family, right_family=right.family,
    )

    left_row = _bitmap_row_at_y(left, left_y_for_ink)
    right_row = _bitmap_row_at_y(right, y)
    left_str = "".join("#" if v else " " for v in left_row) if left_row else "(no row)"
    right_str = "".join("#" if v else " " for v in right_row) if right_row else "(no row)"

    print(f"    join y={y}")
    print(f"      left  exit=({left_anchor[0]}, {left_anchor[1]})"
          f"  eff_x={eff_left_x}"
          f"  ink_y={left_y_for_ink}{' (declared)' if left.exit_ink_y is not None else ''}")
    print(f"      right entry=({right_anchor[0]}, {right_anchor[1]})  eff_x={eff_right_x}")
    if left_bounds is not None:
        lmin, lmax = left_bounds
        print(f"      left  row \"{left_str}\"  ink={lmin}..{lmax}  ink_to_exit={lmax - eff_left_x}")
    else:
        print(f"      left  row \"{left_str}\"  no ink at exit_ink_y")
    if right_bounds is not None:
        rmin, rmax = right_bounds
        print(f"      right row \"{right_str}\"  ink={rmin}..{rmax}  ink_to_entry={rmin - eff_right_x}")
    else:
        print(f"      right row \"{right_str}\"  no ink at entry y")
    if gap is None:
        print("      gap = N/A (one side has no ink at the join row)")
    else:
        verdict = "touch" if gap == 0 else ("overlap" if gap < 0 else "blank pixels between")
        print(f"      gap = {gap} ({verdict})")


def _print_pair(left: JoinGlyph, right: JoinGlyph) -> None:
    print(f"\n→ {left.name}  ⇒  {right.name}")
    if right.entry_explicitly_none:
        print("    right has entry_explicitly_none — no cursive join possible")
        return
    left_ys = set(left.exit_ys)
    right_ys = set(a[1] for a in (*right.entry, *right.entry_curs_only))
    shared = left_ys & right_ys
    if not shared:
        print(f"    no shared join y (left exit={sorted(left_ys) or '∅'},"
              f" right entry={sorted(right_ys) or '∅'})")
        return
    for y in sorted(shared):
        _gap_explanation(left, right, y)


def _list_variants(meta_map: dict[str, JoinGlyph], family: str) -> None:
    variants = sorted(
        (g for g in meta_map.values() if g.base_name.startswith(family)),
        key=lambda g: g.name,
    )
    if not variants:
        print(f"no variants found for {family}")
        return
    print(f"variants reachable for {family} (base_name match):")
    for g in variants:
        tags = []
        if g.generated_from:
            tags.append(f"from={g.generated_from}/{g.transform_kind}")
        if g.gate_feature:
            tags.append(f"gate={g.gate_feature}")
        if g.entry_explicitly_none:
            tags.append("entry=null")
        suffix = f"  [{', '.join(tags)}]" if tags else ""
        entry = _format_anchor_list(g.entry)
        exit_ = _format_anchor_list(g.exit)
        print(f"  {g.name}{suffix}")
        print(f"    entry={entry}  exit={exit_}")


def _parse_features(spec: str | None) -> dict[str, bool]:
    if not spec:
        return {}
    features: dict[str, bool] = {}
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            tag, value = token.split("=", 1)
            features[tag.strip()] = value.strip().lower() not in {"0", "off", "false"}
        else:
            features[token] = True
    return features


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("families", nargs="*", help="Glyph family names (e.g. qsHe qsRoe)")
    parser.add_argument(
        "--variants",
        metavar="FAMILY",
        help="Instead of shaping, list every compiled variant whose base_name starts with FAMILY",
    )
    parser.add_argument(
        "--features",
        help="Comma-separated OpenType feature spec, e.g. 'ss03' or 'ss03=on,ss07=off'",
    )
    args = parser.parse_args()

    data = load_glyph_data(ROOT / "glyph_data")
    meta_map = compile_glyph_set(data, "senior").glyph_meta

    if args.variants:
        _list_variants(meta_map, args.variants)
        return

    if not args.families:
        parser.error("provide at least one family name, or use --variants")

    text = "".join(_char_for_family(f) for f in args.families)
    features = _parse_features(args.features)
    shaped = _shape(text, features)
    feature_label = f" features={features}" if features else ""
    print(f"Input: {' '.join(args.families)}{feature_label}")
    print(f"Shaped ({len(shaped)} glyphs): {' | '.join(shaped)}")

    print("\nVariants chosen:")
    metas = []
    for name in shaped:
        meta = meta_map.get(name)
        if meta is None:
            print(f"  {name}: <not in compiled metadata>")
            continue
        metas.append(meta)
        _print_glyph(meta)

    if len(metas) >= 2:
        print("\nJoins:")
        for left, right in zip(metas, metas[1:]):
            _print_pair(left, right)


if __name__ == "__main__":
    main()
