"""Shaped-ink join check.

Drive HarfBuzz with every plain ·X·Y pair surrounded by 1 left + 1 right context glyph (the same 45-entry context set the other pair sweeps use), then for each adjacent pair in the shaped output verify that wherever the design intends a connection — either both sides have cursive anchors at the same Y, OR one side carries an extension suffix at a Y that has no matching partner anchor — the rendered ink columns actually touch.

The companion `_collect_bitmap_gap_warnings` in `tools/quikscript_join_analysis.py` performs the cursive-meet case structurally (no shaping). The shaped pass is the ground truth: it sees the exact glyph variant calt selects in context, and the cursive shifts GPOS actually applies. The stranded-extension cases mirror what `test_no_stranded_extension_joins_anywhere` flags by anchor metadata, but expressed as a measured ink gap.

Many pairs currently fail — see `_ACCEPTED_SHAPED_INK_GAPS` for the triaged list. Treat each entry as a TODO, not a permanent acceptance.
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
    _plain_quikscript_letters,
    _qs_text,
)

PIXEL_SIZE = 50  # from glyph_data/metadata.yaml


# (left_glyph_name, right_glyph_name, join_y) tuples whose rendered ink
# is a known-accepted gap or overlap. Add entries here only after
# verifying the visual rendering is genuinely intended; don't use this as
# a silencer for real regressions.
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
    """Font-unit X of bitmap column 0 relative to the glyph's drawing origin.

    Mirrors the centering computed in `tools/build_font.py` — `(advance_width - bitmap_width) // 2`. The advance_width there is the pre-shaping hmtx value, not the cursive-modified runtime advance.
    """
    advance = _hmtx_widths().get(glyph_name, 0)
    bitmap_w = _bitmap_width_cols(meta) * PIXEL_SIZE
    if advance == 0:
        return -(bitmap_w // 2)
    return (advance - bitmap_w) // 2


# Invariant for callers of `_BUF`: materialize `buf.glyph_infos` / `buf.glyph_positions` into a list (comprehension or `list(...)`) before the function returns. Never return the property itself or a generator over it — the next `_shape()` call will `clear_contents()` and overwrite the buffer, invalidating any unmaterialized view.
_BUF: hb.Buffer = hb.Buffer()


def _shape(text: str) -> tuple[list[str], list]:
    font = _font()
    buf = _BUF
    buf.clear_contents()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    names = [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]
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
    """Return (gap_in_px, detail_for_no_ink_case) or None if the gap is OK.

    Negative gaps (overlap) and zero gaps are acceptable; positive gaps are failures. Returns (None, side) when one side has no ink at the row — that's the worst kind of failure: the stroke reaches into empty space.
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
        # Mirror `_collect_bitmap_gap_warnings` in quikscript_join_analysis.py: an entry-trim that strips every ink cell at the join row is the trim transform's intended geometry — the predecessor's exit is sized to meet the parent's ink position, then the trim lets the connection slope into the upper rows. Fall back to the parent's bitmap so we measure the gap against the position the trim was sized against.
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
    """Ys where the design intends a connection, tagged with intent kind.

    - 'cursive': both sides have anchors at this Y — cursive will align them.
    - 'stranded-exit': left has an extension suffix and an exit at this Y, but right has no entry there — left's extended stroke dangles.
    - 'stranded-entry': symmetric — right's extended entry has no partner.
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
    """Walk every ·X·Y pair surrounded by 1+1 context chars whose first `before` slot is the named context glyph, report ink-join gaps.

    Failures are deduped on (left_variant, right_variant, join_y) — the same variant pair has the same geometry in every context, so the first sighting is enough to triage.
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
