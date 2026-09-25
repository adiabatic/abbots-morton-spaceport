"""Audit cursive-anchor geometry against the leftmost or rightmost ink at the anchor's Y.

The font's convention is:

    entry.x = min_ink_x_at_entry_y
    exit.x  = max_ink_x_at_exit_y + 1

This script compiles every Senior stance, including inherited and derived ones, and reports each anchor's gap from the convention.

Two groups of derived variants deviate from the convention as a result of their derivation:

    * `*.en-con-N` (such as `*.en-con-1`) has gap +N and `*.ex-con-N` has gap -N. The contraction moves the anchor N pixels inward to shorten the join and leaves the bitmap unchanged.
    * `*.en-trim-N` has gap entry.x - N. That is -N when the entry is at x=0 (qsZoo, qsJay) and less negative for an inset entry: qsJai's entry is at x=1, so its `en-trim-2` has gap -1. The trim blanks the receiver's leftmost N columns in the entry row to make room for the predecessor's exit stroke, which overlaps the receiver by N pixels after the predecessor's `ex-con-N` contraction. The entry stays at the base bitmap's anchor, so the predecessor's exit meets the receiver where the untrimmed ink begins.

Usage::

    uv run python tools/audit_anchor_geometry.py            # both sides
    uv run python tools/audit_anchor_geometry.py --side entry
    uv run python tools/audit_anchor_geometry.py --side exit --family qsTea
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set


def bitmap_row_at_y(glyph_def, y):
    bitmap = glyph_def.get("bitmap")
    if not bitmap:
        return None
    height = len(bitmap)
    y_offset = glyph_def.get("y_offset", 0)
    top_y = height - 1 + y_offset
    row_idx = top_y - y
    if not (0 <= row_idx < height):
        return None
    row = bitmap[row_idx]
    if isinstance(row, list):
        return "".join("#" if v else " " for v in row)
    return row


def min_ink_x(row):
    if row is None:
        return None
    for i, ch in enumerate(row):
        if ch != " " and ch != ".":
            return i
    return None


def max_ink_x(row):
    if row is None:
        return None
    for i in range(len(row) - 1, -1, -1):
        ch = row[i]
        if ch != " " and ch != ".":
            return i
    return None


def _normalize_anchors(raw):
    if not raw:
        return []
    if isinstance(raw[0], list):
        return raw
    return [raw]


def collect(side, defs, meta, family_filter=None):
    """Return `(rows, skipped_no_bitmap, skipped_no_ink)` for one side, 'entry' or 'exit'. Each row is `(gap, name, (x, y), family, ink_x, row, ink_y)`.

    ``ink_y`` is the bitmap row scanned for ink. It is the anchor's y, except for an exit on a stance that declares ``exit_ink_y`` (qsZoo measures its y=0 exit against the row at y=-1).
    """
    if side == "entry":
        fields = ("cursive_entry", "cursive_entry_curs_only")
    else:
        fields = ("cursive_exit",)
    rows = []
    skipped_no_bitmap = []
    skipped_no_ink = []

    for name, gdef in defs.items():
        if gdef is None:
            continue
        anchors = []
        for field in fields:
            anchors.extend(_normalize_anchors(gdef.get(field)))
        if not anchors:
            continue
        family = gdef.get("family") or name.split(".")[0]
        if family_filter and family != family_filter:
            continue
        join_glyph = meta.get(name) if meta else None
        exit_ink_y_override = getattr(join_glyph, "exit_ink_y", None) if join_glyph else None
        for anc in anchors:
            if not anc or anc[0] is None:
                continue
            ax, ay = anc
            if side == "exit" and exit_ink_y_override is not None:
                ink_y = exit_ink_y_override
            else:
                ink_y = ay
            row = bitmap_row_at_y(gdef, ink_y)
            if row is None:
                skipped_no_bitmap.append((name, ax, ay))
                continue
            if side == "entry":
                ink_x = min_ink_x(row)
                if ink_x is None:
                    skipped_no_ink.append((name, ax, ay, row))
                    continue
                gap = ax - ink_x
            else:
                ink_x = max_ink_x(row)
                if ink_x is None:
                    skipped_no_ink.append((name, ax, ay, row))
                    continue
                gap = ax - (ink_x + 1)
            rows.append((gap, name, (ax, ay), family, ink_x, row, ink_y))

    return rows, skipped_no_bitmap, skipped_no_ink


_DERIVED_MODIFIER_RE = {
    "entry": re.compile(r"en-(?:ext|con|trim)-\d+"),
    "exit": re.compile(r"ex-(?:ext|con|trim)-\d+"),
}


def is_derived_variant(name, side):
    pattern = _DERIVED_MODIFIER_RE[side]
    return any(pattern.fullmatch(part) for part in name.split(".")[1:])


def report(side, rows, skipped_no_bitmap, skipped_no_ink, *, verbose=False):
    label = side.capitalize()
    print(f"=== {label} anchors ===")
    print(f"Total examined: {len(rows)}")
    if skipped_no_bitmap:
        print(f"Skipped (no bitmap): {len(skipped_no_bitmap)}")
    if skipped_no_ink:
        print(f"Skipped (no ink at anchor's y): {len(skipped_no_ink)}")
    print()

    histo_source = Counter()
    histo_derived = Counter()
    for r in rows:
        if is_derived_variant(r[1], side):
            histo_derived[r[0]] += 1
        else:
            histo_source[r[0]] += 1
    convention_label = (
        "entry_gap = entry.x - min_ink_x_at_entry_y"
        if side == "entry"
        else "exit_gap = exit.x - (max_ink_x_at_exit_y + 1)"
    )
    print(f"Histogram of {convention_label}:")
    print("  (source = stances whose anchor comes from a YAML declaration;")
    print("   derived = stances whose anchor was shifted by extend/contract/trim)")
    all_gaps = sorted(set(histo_source) | set(histo_derived))
    for g in all_gaps:
        s = histo_source[g]
        d = histo_derived[g]
        marker = "  <- tight" if g == 0 else ("  <- loose source" if g == 1 and s else "")
        print(f"  gap = {g:+d}: {s + d} anchors  (source={s}, derived={d}){marker}")
    print()

    by_gap = defaultdict(list)
    for r in rows:
        by_gap[r[0]].append(r)

    for g in sorted(by_gap):
        if g >= 0 and not verbose and g not in (1, 2, 3):
            continue
        bucket = by_gap[g]
        source_bucket = [r for r in bucket if not is_derived_variant(r[1], side)]
        derived_bucket = [r for r in bucket if is_derived_variant(r[1], side)]
        if g == 1:
            fam_counts = Counter(r[3] for r in source_bucket)
            print(
                f"--- gap +1 (loose) — {len(source_bucket)} source anchors "
                f"across {len(fam_counts)} families "
                f"({len(derived_bucket)} derived not shown unless --verbose) ---"
            )
            for fam, cnt in sorted(fam_counts.items(), key=lambda x: -x[1]):
                print(f"  {fam}: {cnt}")
            if verbose:
                print()
                print("  source stances:")
                for gap, name, (ax, ay), family, ink_x, row, ink_y in source_bucket:
                    override = f"  [exit_ink_y={ink_y}]" if ink_y != ay else ""
                    print(f"    {name}  ({side}={ax},{ay})  ink_x={ink_x}  row={row!r}{override}")
                if derived_bucket:
                    print("  derived stances (extension / contraction / trim, intentional at +1):")
                    for gap, name, (ax, ay), family, ink_x, row, ink_y in derived_bucket:
                        override = f"  [exit_ink_y={ink_y}]" if ink_y != ay else ""
                        print(f"    {name}  ({side}={ax},{ay})  ink_x={ink_x}  row={row!r}{override}")
            print()
        else:
            print(f"--- gap {g:+d} ({len(bucket)} anchors) ---")
            for gap, name, (ax, ay), family, ink_x, row, ink_y in bucket:
                tag = "  [derived]" if is_derived_variant(name, side) else ""
                override = f"  [exit_ink_y={ink_y}]" if ink_y != ay else ""
                print(f"  {name}  ({side}={ax},{ay})  ink_x={ink_x}  row={row!r}{override}{tag}")
            print()

    if skipped_no_ink:
        print(f"--- {label} anchors with NO ink at anchor's y ({len(skipped_no_ink)}) ---")
        for name, ax, ay, row in skipped_no_ink:
            print(f"  {name}  ({side}={ax},{ay})  row_at_y={row!r}")
        print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--side",
        choices=("entry", "exit", "both"),
        default="both",
        help="Which anchor side to audit (default: both).",
    )
    parser.add_argument(
        "--family",
        help="Limit the report to a single family, e.g. qsTea.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="List individual loose-bucket stances, not just per-family counts.",
    )
    args = parser.parse_args()

    glyph_data = load_glyph_data(REPO / "glyph_data")
    compiled = compile_glyph_set(glyph_data, "senior")
    defs = compiled.glyph_definitions
    meta = compiled.glyph_meta

    sides = ("entry", "exit") if args.side == "both" else (args.side,)
    for side in sides:
        rows, skipped_no_bitmap, skipped_no_ink = collect(side, defs, meta, args.family)
        report(side, rows, skipped_no_bitmap, skipped_no_ink, verbose=args.verbose)


if __name__ == "__main__":
    main()
