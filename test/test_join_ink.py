"""Check that shaped ink meets at every intended join.

The test shapes every plain ·X·Y pair with one context glyph on each side, drawn from `_context_chars()`. For each adjacent pair in the output it finds the rows where the design intends a connection: both sides have cursive anchors at that Y, or one side has an extension suffix at a Y where the other side has no matching anchor. At each such row the left glyph's ink must reach the right glyph's ink.

`_collect_bitmap_gap_warnings` in `tools/quikscript_join_analysis.py` checks the cursive case from bitmaps and anchors without shaping. This test shapes, so it sees the variant `calt` selects in context and the offsets GPOS applies. The stranded-extension cases are the ones `test_no_stranded_extension_joins_anywhere` flags from anchor data, measured here as an ink gap.

`_ACCEPTED_SHAPED_INK_GAPS` lists `(left_variant, right_variant, join_y)` triples whose gap or overlap is intended. Add an entry only after checking the rendering.
"""

from functools import cache
from itertools import product

import pytest
import uharfbuzz as hb
from fontTools.ttLib import TTFont

from quikscript_shaping_helpers import (
    FONT_PATH,
    _assert_no_failures,
    _compiled_meta,
    _context_chars,
    _font,
    _gid_to_full_name,
    _plain_quikscript_letters,
    _qs_text,
)

PIXEL_SIZE = 50  # from glyph_data/metadata.yaml


_ACCEPTED_SHAPED_INK_GAPS: frozenset[tuple[str, str, int]] = frozenset()


_BEFORE_FIRSTS = tuple(name for name, _ in _context_chars())


@cache
def _hmtx_widths() -> dict[str, int]:
    return {name: width for name, (width, _lsb) in TTFont(str(FONT_PATH))["hmtx"].metrics.items()}


def _bitmap_row_at_y(meta, y: int) -> tuple[bool, ...] | None:
    if not meta.bitmap:
        return None
    top_y = meta.y_offset + len(meta.bitmap) - 1
    row_index = top_y - y
    if row_index < 0 or row_index >= len(meta.bitmap):
        return None
    row = meta.bitmap[row_index]
    if isinstance(row, str):
        return tuple(ch == "#" for ch in row)
    return tuple(bool(value) for value in row)


def _ink_bounds_at_y(meta, y: int) -> tuple[int, int] | None:
    row = _bitmap_row_at_y(meta, y)
    if row is None:
        return None
    ink_xs = [index for index, has_ink in enumerate(row) if has_ink]
    if not ink_xs:
        return None
    return min(ink_xs), max(ink_xs)


def _bitmap_width_cols(meta) -> int:
    if not meta.bitmap:
        return 0
    return max(len(row) for row in meta.bitmap)


def _bitmap_origin_x_offset(glyph_name: str, meta) -> int:
    """Return the font-unit X of bitmap column 0 relative to the glyph's origin.

    This repeats the centering in `tools/build_font.py`, `(advance_width - bitmap_width) // 2`, using the font's hmtx advance. For a Senior Quikscript letter with no explicit `advance_width`, build_font centers on the advance before it trims one pixel off the right sidebearing, so this result is 25 units left of the real one. The error cancels in a gap when both glyphs are trimmed.
    """
    advance = _hmtx_widths().get(glyph_name, 0)
    bitmap_w = _bitmap_width_cols(meta) * PIXEL_SIZE
    if advance == 0:
        return -(bitmap_w // 2)
    return (advance - bitmap_w) // 2


# `_shape()` reuses this buffer. Copy `glyph_infos` and `glyph_positions` into lists before returning, because the next call clears and overwrites the buffer.
_BUF: hb.Buffer = hb.Buffer()


def _shape(text: str) -> tuple[list[str], list]:
    font = _font()
    buf = _BUF
    buf.clear_contents()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    names = [_gid_to_full_name(info.codepoint) for info in buf.glyph_infos]
    positions = list(buf.glyph_positions)
    return names, positions


def _origin_xs(positions) -> list[int]:
    pen = 0
    xs: list[int] = []
    for pos in positions:
        xs.append(pen + pos.x_offset)
        pen += pos.x_advance
    return xs


def _check_ink_gap_at_y(
    left: str,
    right: str,
    left_meta,
    right_meta,
    join_y: int,
    left_origin: int,
    right_origin: int,
    meta_map,
) -> tuple[int | None, str] | None:
    """Return `(gap, detail)` for the ink at *join_y*.

    `gap` is in pixels, and a zero or negative gap (an overlap) is acceptable. When the gap is not a whole number of pixels, `gap` is in font units and `detail` is `"non-int"`. When one side has no ink on the row, the result is `(None, side)`.
    """
    left_exits_here = any(anchor[1] == join_y for anchor in left_meta.exit)
    left_scan_y = left_meta.exit_ink_y if left_exits_here and left_meta.exit_ink_y is not None else join_y
    left_ink = _ink_bounds_at_y(left_meta, left_scan_y)
    right_ink = _ink_bounds_at_y(right_meta, join_y)
    if (
        right_ink is None
        and right_meta.transform_kind == "entry-trimmed"
        and right_meta.generated_from is not None
    ):
        # As in `_collect_bitmap_gap_warnings`: an entry trim can remove every ink cell on the join row. The predecessor's exit is sized to meet the untrimmed parent's ink, so measure against the parent's bitmap.
        parent_meta = meta_map.get(right_meta.generated_from)
        if parent_meta is not None:
            parent_ink = _ink_bounds_at_y(parent_meta, join_y)
            if parent_ink is not None:
                right_ink = parent_ink

    if left_ink is None or right_ink is None:
        return (None, "left" if left_ink is None else "right")

    left_bx = _bitmap_origin_x_offset(left, left_meta)
    right_bx = _bitmap_origin_x_offset(right, right_meta)
    left_right_edge = left_origin + left_bx + (left_ink[1] + 1) * PIXEL_SIZE
    right_left_edge = right_origin + right_bx + right_ink[0] * PIXEL_SIZE
    gap_units = right_left_edge - left_right_edge
    if gap_units % PIXEL_SIZE != 0:
        return (gap_units, "non-int")
    return (gap_units // PIXEL_SIZE, "")


def _intended_join_ys(left_meta, right_meta) -> set[tuple[int, str]]:
    """Return the Ys where the design intends a connection, each tagged with its kind.

    - 'cursive': both sides have anchors at this Y.
    - 'stranded-exit': the left glyph has an extension suffix and an exit at this Y, and the right glyph has no entry there.
    - 'stranded-entry': the right glyph has an extension suffix and an entry at this Y, and the left glyph has no exit there.
    """
    left_exit_ys = {anchor[1] for anchor in left_meta.exit}
    right_entry_ys = {anchor[1] for anchor in right_meta.entry} | {
        anchor[1] for anchor in right_meta.entry_curs_only
    }
    cursive = left_exit_ys & right_entry_ys
    intents: set[tuple[int, str]] = {(y, "cursive") for y in cursive}
    if left_meta.extended_exit_suffix is not None:
        intents.update((y, "stranded-exit") for y in left_exit_ys - right_entry_ys)
    if right_meta.extended_entry_suffix is not None:
        intents.update((y, "stranded-entry") for y in right_entry_ys - left_exit_ys)
    return intents


def _collect_shaped_ink_gaps(before_first: str) -> list[str]:
    """Return the ink-gap failures for every ·X·Y pair shaped between a *before_first* context glyph and each context glyph after it.

    Failures are reported once per (left_variant, right_variant, join_y), because a variant pair has the same geometry in every context.
    """
    meta_map = _compiled_meta()
    letters = _plain_quikscript_letters()
    context_set = _context_chars()
    valid_names = {name for name, _ in context_set}
    if before_first not in valid_names:
        raise ValueError(f"before_first={before_first!r} not in context set")

    before_combos = tuple(
        (left_name, left_char, right_name, right_char)
        for (left_name, left_char), (right_name, right_char) in product(context_set, context_set)
        if left_name == before_first
    )

    seen: set[tuple[str, str, int]] = set()
    failures: list[str] = []

    for before_first_name, before_char, after_name, after_char in before_combos:
        for left_letter, left_char in letters:
            for right_letter, right_char in letters:
                text = before_char + left_char + right_char + after_char
                glyphs, positions = _shape(text)
                origins = _origin_xs(positions)
                for index in range(len(glyphs) - 1):
                    left = glyphs[index]
                    right = glyphs[index + 1]
                    left_meta = meta_map.get(left)
                    right_meta = meta_map.get(right)
                    if left_meta is None or right_meta is None:
                        continue
                    for join_y, kind in _intended_join_ys(left_meta, right_meta):
                        key = (left, right, join_y)
                        if key in seen:
                            continue
                        if key in _ACCEPTED_SHAPED_INK_GAPS:
                            continue
                        result = _check_ink_gap_at_y(
                            left,
                            right,
                            left_meta,
                            right_meta,
                            join_y,
                            origins[index],
                            origins[index + 1],
                            meta_map,
                        )
                        if result is None:
                            continue
                        gap, detail = result
                        if gap is None:
                            seen.add(key)
                            failures.append(
                                f"{left} -> {right} at y={join_y} ({kind}): "
                                f"no ink on {detail} side of join row (context "
                                f"·{before_first_name}·{left_letter}·{right_letter}·{after_name})"
                            )
                            continue
                        if detail == "non-int":
                            seen.add(key)
                            failures.append(
                                f"{left} -> {right} at y={join_y} ({kind}): "
                                f"non-integer-pixel gap ({gap} units; context "
                                f"·{before_first_name}·{left_letter}·{right_letter}·{after_name})"
                            )
                            continue
                        if gap > 0:
                            seen.add(key)
                            failures.append(
                                f"{left} -> {right} at y={join_y} ({kind}): "
                                f"gap={gap}px (context "
                                f"·{before_first_name}·{left_letter}·{right_letter}·{after_name})"
                            )

    return failures


@pytest.mark.parametrize("before_first", _BEFORE_FIRSTS)
def test_no_shaped_ink_gaps(before_first: str):
    _assert_no_failures(_collect_shaped_ink_gaps(before_first), limit=None)
