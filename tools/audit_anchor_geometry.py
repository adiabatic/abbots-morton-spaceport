"""Audit cursive-anchor geometry against the leftmost / rightmost ink at the anchor's Y.

The font's tight convention is:

    entry.x = min_ink_x_at_entry_y
    exit.x  = max_ink_x_at_exit_y + 1

This script walks every compiled form (post-inheritance, post-derive expansion) and
reports the gap between each anchor's x and what the convention would put it at.

Reading the report, two derive-generated buckets look anomalous but are
intentional and follow from a tight source:

    * `*.entry-contracted` / `*.exit-contracted` variants land at gap = +N (entry
      side) or gap = -N (exit side) by design — the contract shifts the anchor
      inward by N to shorten the join, leaving the bitmap unchanged on that side.
    * `*.entry-trimmed-by-N` variants land at gap = -N. The receiver's leftmost
      N ink columns are trimmed at the entry's row to make room for the
      predecessor's overlapping exit stroke; the entry stays at the original
      attachment point so the predecessor's exit meets the receiver where the
      receiver's ink used to begin.

Both follow mechanically from the source declaration. Tighten the source, and
those two buckets either move with it (contract) or stay correct (trim).

Usage::

    uv run python tools/audit_anchor_geometry.py            # both sides
    uv run python tools/audit_anchor_geometry.py --side entry
    uv run python tools/audit_anchor_geometry.py --side exit --family qsTea

If the default uv cache is unwritable, prefix with ``UV_CACHE_DIR=.uv-cache``.
"""

from __future__ import annotations

import argparse
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
    """Return a list of [x, y] pairs. Accepts a single pair or a list of pairs."""
    if not raw:
        return []
    if isinstance(raw[0], list):
        return raw
    return [raw]


def collect(side, defs, meta, family_filter=None):
    """Side is 'entry' or 'exit'. Returns list of (gap, name, (x, y), family, ink_x, row, ink_y).

    ``ink_y`` is the bitmap row that was scanned for ink. For entries it always equals
    the anchor's y. For exits it equals the form's ``exit_ink_y`` override when the
    JoinGlyph carries one (compiled from YAML ``exit_ink_y`` / dict key
    ``cursive_exit_ink_y``), otherwise the exit anchor's y; this lets a glyph like
    qsZoo say "judge my exit's tightness against the row at y=-1 rather than y=0"
    without the audit flagging it as loose.
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


_DERIVED_ENTRY_MODIFIERS = (
    "entry-extended",
    "entry-doubly-extended",
    "entry-contracted",
    "entry-doubly-contracted",
    "entry-trimmed-by-",
)
_DERIVED_EXIT_MODIFIERS = (
    "exit-extended",
    "exit-doubly-extended",
    "exit-contracted",
    "exit-doubly-contracted",
    "exit-trimmed-by-",
)


def is_derived_variant(name, side):
    parts = name.split(".")[1:]
    needles = _DERIVED_ENTRY_MODIFIERS if side == "entry" else _DERIVED_EXIT_MODIFIERS
    for part in parts:
        for needle in needles:
            if part == needle or part.startswith(needle):
                return True
    return False


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
    print("  (source = forms whose anchor comes from a YAML declaration;")
    print("   derived = forms whose anchor was shifted by extend/contract/trim)")
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
                print("  source forms:")
                for gap, name, (ax, ay), family, ink_x, row, ink_y in source_bucket:
                    override = f"  [exit_ink_y={ink_y}]" if ink_y != ay else ""
                    print(f"    {name}  ({side}={ax},{ay})  ink_x={ink_x}  row={row!r}{override}")
                if derived_bucket:
                    print("  derived forms (extension / contraction / trim, intentional at +1):")
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
        help="List individual loose-bucket forms, not just per-family counts.",
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
