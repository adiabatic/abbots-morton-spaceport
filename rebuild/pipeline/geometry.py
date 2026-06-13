"""Per-cell bitmap and anchor realization (M1-PLAN section 5, Group 3).

`realize` turns a CellPlan plus generated adjustments into a GlyphRecord, in the design section 3.2 resolution order: the plan's resolved binding (explicit `cells:` row > side bindings > base bitmap, chosen by surface.resolve_cell) supplies the starting drawing; stub arithmetic applies per side liveness; then the adjustments grammar (model.py) applies extend/contract/trim connector arithmetic and `bind:` substitutions; then anchors land per the standing conventions (`entry.x = min_ink_x_at_entry_y`, `exit.x = max_ink_x_at_exit_y + 1`), with per-cell overrides and the flagged exceptions honored.

`seam_gap` is the section 9 gap arithmetic over two realized records; `verify_withdrawal_safe` discharges `withdrawal: safe` claims (no reaching connector ink at the declined row: the terminal ink pixel at the row must continue vertically into an adjacent row, or the row must be empty).
"""

from __future__ import annotations

import hashlib

from rebuild.pipeline.model import (
    Bitmap,
    CellId,
    CellPlan,
    GlyphRecord,
    Height,
    ResolvedSpec,
    Stance,
    parse_adjustment,
)

PIXEL = 50
INK_X_OFFSET = 1
MAX_GLYPH_NAME_BYTES = 63

# The closed height table (design section 2); registry-validated by spec_load, mirrored here so the
# plan-frozen seam_gap/verify_withdrawal_safe signatures can take height names without a registry handle.
HEIGHT_Y = {"baseline": 0, "x-height": 5, "y6": 6, "top": 8}


class GeometryError(Exception):
    pass


def _height_y(height: Height | int) -> int:
    if isinstance(height, int):
        return height
    try:
        return HEIGHT_Y[height]
    except KeyError:
        raise GeometryError(f"unknown height {height!r}") from None


def _grid(bitmap: Bitmap) -> list[list[str]]:
    width = bitmap.width
    return [list(row.ljust(width)) for row in bitmap.rows]


def _rows(grid: list[list[str]]) -> tuple[str, ...]:
    return tuple("".join(cells) for cells in grid)


def _row_index(grid: list[list[str]], y: int, y_offset: int) -> int | None:
    index = len(grid) - 1 - (y - y_offset)
    if 0 <= index < len(grid):
        return index
    return None


def _ink_span(grid: list[list[str]], y: int, y_offset: int) -> tuple[int, int] | None:
    index = _row_index(grid, y, y_offset)
    if index is None:
        return None
    cols = [x for x, cell in enumerate(grid[index]) if cell == "#"]
    if not cols:
        return None
    return (min(cols), max(cols))


def ink_span(bitmap_rows: tuple[str, ...], y_offset: int, y: int) -> tuple[int, int] | None:
    """(min ink x, max ink x) at glyph-space y, or None when the row has no ink or is outside the drawing."""
    bitmap = Bitmap(bitmap_rows, y_offset)
    return _ink_span(_grid(bitmap), y, y_offset)


def _base_live_exit(stance) -> Height | None:
    """The exit height whose connector ink is part of the stance's base drawing — signaled by a withdrawal binding to a different form. None when the base drawing is already the withdrawn form everywhere (withdrawal: safe or undeclared)."""
    live = [height for height, row in stance.surface.exits.items() if row.withdrawal not in (None, "safe")]
    return live[0] if len(live) == 1 else None


def isolated_cell(spec: ResolvedSpec, rune_name: str) -> CellId:
    """The cell the raw cmap glyph renders: the default stance with no entry, keeping exactly the exit the isolated drawing's own ink carries."""
    rune = spec.runes[rune_name]
    default = rune.default_stance
    return CellId(rune_name, default, None, _base_live_exit(rune.stances[default]), ())


def display_name(spec: ResolvedSpec, cell: CellId) -> str:
    """The generated display name for a cell: the bare rune name for the isolated cell (the raw cmap glyph), otherwise rune, non-default stance, anchor heights as y values, a `ex-wd` marker when the exit is withdrawn relative to the stance's base drawing, and the adjustments. Capped at 63 bytes with hash overflow; never parsed back by anything."""
    rune = spec.runes[cell.rune]
    if cell == isolated_cell(spec, cell.rune):
        return cell.rune
    parts = [cell.rune]
    if len(rune.stances) > 1 and cell.stance != rune.default_stance:
        parts.append(cell.stance)
    if cell.entry is not None:
        parts.append(f"en-y{spec.registry.y_of(cell.entry)}")
    if cell.exit is not None:
        parts.append(f"ex-y{spec.registry.y_of(cell.exit)}")
    elif _base_live_exit(rune.stances[cell.stance]) is not None:
        parts.append("ex-wd")
    parts.extend(cell.adjustments)
    name = ".".join(parts)
    if len(name.encode()) > MAX_GLYPH_NAME_BYTES:
        digest = hashlib.sha1(name.encode()).hexdigest()[:12]
        head = name.encode()[: MAX_GLYPH_NAME_BYTES - 13].decode(errors="ignore")
        name = f"{head}.{digest}"
    return name


def _apply_stub(grid: list[list[str]], y_offset: int, y: int, cols: tuple[int, ...], present: bool) -> None:
    index = _row_index(grid, y, y_offset)
    if index is None:
        raise GeometryError(f"stub row y={y} is outside the bitmap")
    for col in cols:
        while col >= len(grid[index]):
            for row in grid:
                row.append(" ")
        grid[index][col] = "#" if present else " "


def _extend_right(grid: list[list[str]], y_offset: int, y: int, amount: int) -> None:
    span = _ink_span(grid, y, y_offset)
    if span is None:
        raise GeometryError(f"cannot extend an exit at y={y}: no ink at that row")
    _, max_x = span
    new_width = max(len(grid[0]), max_x + 1 + amount)
    for row in grid:
        row.extend(" " * (new_width - len(row)))
    index = _row_index(grid, y, y_offset)
    assert index is not None
    for col in range(max_x + 1, max_x + 1 + amount):
        grid[index][col] = "#"


def _extend_left(grid: list[list[str]], y_offset: int, y: int, amount: int) -> None:
    span = _ink_span(grid, y, y_offset)
    if span is None:
        raise GeometryError(f"cannot extend an entry at y={y}: no ink at that row")
    min_x, _ = span
    for row in grid:
        for _ in range(amount):
            row.insert(0, " ")
    index = _row_index(grid, y, y_offset)
    assert index is not None
    for col in range(min_x, min_x + amount):
        grid[index][col] = "#"


def _blank_edge_ink(grid: list[list[str]], y_offset: int, y: int, amount: int, side: str) -> None:
    span = _ink_span(grid, y, y_offset)
    if span is None:
        raise GeometryError(f"cannot remove ink at y={y}: no ink at that row")
    index = _row_index(grid, y, y_offset)
    assert index is not None
    cols = [x for x, cell in enumerate(grid[index]) if cell == "#"]
    if len(cols) < amount:
        raise GeometryError(f"cannot remove {amount} ink pixels at y={y}: only {len(cols)} present")
    victims = cols[:amount] if side == "en" else cols[-amount:]
    for col in victims:
        grid[index][col] = " "


def _convention_anchor(grid: list[list[str]], y_offset: int, y: int, side: str) -> tuple[int, int]:
    span = _ink_span(grid, y, y_offset)
    if span is None:
        raise GeometryError(f"cannot place a {side} anchor by convention at y={y}: no ink at that row")
    return (span[0], y) if side == "en" else (span[1] + 1, y)


def _stance_of(spec: ResolvedSpec, cell: CellId) -> Stance:
    try:
        return spec.runes[cell.rune].stances[cell.stance]
    except KeyError:
        raise GeometryError(f"cell {cell} names an unknown rune or stance") from None


def realize(
    spec: ResolvedSpec,
    plan: CellPlan,
    adjustments: tuple[str, ...] = (),
    name: str | None = None,
) -> GlyphRecord:
    cell = plan.cell
    stance = _stance_of(spec, cell)
    effective = adjustments or cell.adjustments
    if plan.bitmap is not None:
        try:
            bitmap = stance.bitmaps[plan.bitmap]
        except KeyError:
            raise GeometryError(f"cell {cell} binds unknown bitmap {plan.bitmap!r}") from None
    else:
        bitmap = stance.bitmap
    grid = _grid(bitmap)
    y_offset = bitmap.y_offset

    entry_live = cell.entry is not None
    exit_live = cell.exit is not None
    if plan.entry_stub is not None and entry_live:
        present = plan.entry_stub.inks_when == "joined"
        _apply_stub(grid, y_offset, _height_y(cell.entry), plan.entry_stub.cols, present)
    if plan.exit_stub is not None and exit_live:
        present = plan.exit_stub.inks_when == "joined"
        _apply_stub(grid, y_offset, _height_y(cell.exit), plan.exit_stub.cols, present)

    entry_anchor: tuple[int, int] | None = None
    exit_anchor: tuple[int, int] | None = None
    if entry_live:
        entry_y = _height_y(cell.entry)
        entry_x = plan.entry_x
        if entry_x is None:
            row = stance.surface.entries.get(cell.entry)
            if row is None:
                raise GeometryError(f"cell {cell} enters at {cell.entry} but the stance declares no such row")
            entry_x = (
                row.joined_x
                if (row.joined is not None and plan.bitmap == row.joined and row.joined_x is not None)
                else row.x
            )
        entry_anchor = (entry_x, entry_y)
    if exit_live:
        exit_y = _height_y(cell.exit)
        exit_x = plan.exit_x
        if exit_x is None:
            row = stance.surface.exits.get(cell.exit)
            if row is None:
                raise GeometryError(f"cell {cell} exits at {cell.exit} but the stance declares no such row")
            exit_x = row.x
        exit_anchor = (exit_x, exit_y)

    entry_curs_only = plan.entry_curs_only
    convention_exempt: list[str] = []
    if plan.x_off_convention:
        convention_exempt.extend(("entry", "exit"))

    for token in effective:
        op, side, argument = parse_adjustment(token)
        if op == "locked":
            entry_anchor = None
            continue
        if op == "bind":
            assert isinstance(argument, str)
            try:
                bound = stance.bitmaps[argument]
            except KeyError:
                raise GeometryError(
                    f"adjustment {token!r} on {cell} names unknown bitmap {argument!r}"
                ) from None
            grid = _grid(bound)
            y_offset = bound.y_offset
            if entry_anchor is not None:
                entry_anchor = _convention_anchor(grid, y_offset, entry_anchor[1], "en")
            if exit_anchor is not None:
                exit_anchor = _convention_anchor(grid, y_offset, exit_anchor[1], "ex")
            continue
        assert isinstance(argument, int)
        anchor = entry_anchor if side == "en" else exit_anchor
        if anchor is None:
            raise GeometryError(f"adjustment {token!r} on {cell} targets a side with no live anchor")
        y = anchor[1]
        if op == "ext":
            if side == "ex":
                _extend_right(grid, y_offset, y, argument)
                exit_anchor = (exit_anchor[0] + argument, y)  # type: ignore[index]
            else:
                _extend_left(grid, y_offset, y, argument)
                if exit_anchor is not None:
                    exit_anchor = (exit_anchor[0] + argument, exit_anchor[1])
                if entry_curs_only is not None:
                    entry_curs_only = (entry_curs_only[0] + argument, entry_curs_only[1])
        elif op == "con":
            _blank_edge_ink(grid, y_offset, y, argument, side)
            if side == "ex":
                exit_anchor = (exit_anchor[0] - argument, y)  # type: ignore[index]
            else:
                entry_anchor = (entry_anchor[0] + argument, y)  # type: ignore[index]
        elif op == "trim":
            _blank_edge_ink(grid, y_offset, y, argument, side)
            convention_exempt.append("entry" if side == "en" else "exit")
        else:
            raise GeometryError(f"unrecognized adjustment {token!r}")

    return GlyphRecord(
        name=name if name is not None else display_name(spec, cell),
        bitmap=_rows(grid),
        y_offset=y_offset,
        entry=entry_anchor,
        exit=exit_anchor,
        entry_curs_only=entry_curs_only,
        exit_ink_y=plan.exit_ink_y,
        convention_exempt=tuple(dict.fromkeys(convention_exempt)),
        safety_checks=plan.safety_checks,
        provenance=str(cell),
    )


def seam_gap(left: GlyphRecord, right: GlyphRecord, height: Height | int) -> int:
    """The section 9 arithmetic: with the two anchors aligned by curs, the count of blank pixels between the left glyph's last ink and the right glyph's first ink at the seam row. 0 = the join physically realizes; negative = overlap."""
    y = _height_y(height)
    if left.exit is None or right.entry is None:
        raise GeometryError("seam_gap needs a live exit on the left and a live entry on the right")
    left_span = ink_span(left.bitmap, left.y_offset, y)
    left_scan_y = y
    if left_span is None and left.exit_ink_y is not None:
        left_scan_y = left.exit_ink_y
        left_span = ink_span(left.bitmap, left.y_offset, left_scan_y)
    right_span = ink_span(right.bitmap, right.y_offset, y)
    if left_span is None or right_span is None:
        raise GeometryError(f"seam_gap at y={y}: a side has no ink at the seam row")
    return (left.exit[0] - 1 - left_span[1]) + (right_span[0] - right.entry[0])


def verify_withdrawal_safe(record: GlyphRecord, side: str, height: Height | int) -> bool:
    """True when the declined side's row has no reaching connector ink: the row is empty, or its terminal ink pixel (rightmost for an exit, leftmost for an entry) continues vertically into an adjacent row — i.e. it belongs to a stroke, not a connector."""
    y = _height_y(height)
    span = ink_span(record.bitmap, record.y_offset, y)
    if span is None:
        return True
    terminal_x = span[1] if side == "exit" else span[0]
    for neighbor_y in (y - 1, y + 1):
        neighbor = ink_span(record.bitmap, record.y_offset, neighbor_y)
        if neighbor is not None and neighbor[0] <= terminal_x <= neighbor[1]:
            bitmap = Bitmap(record.bitmap, record.y_offset)
            row = bitmap.row_for_y(neighbor_y)
            if row is not None and terminal_x < len(row) and row[terminal_x] == "#":
                return True
    return False


def ink_cells(record: GlyphRecord, x_origin: int = 0) -> frozenset[tuple[int, int]]:
    """All (x, y) ink pixels in glyph space, shifted by x_origin — the overlay primitive for the off-anchor contact gate."""
    cells: set[tuple[int, int]] = set()
    for row_index, row in enumerate(record.bitmap):
        y = record.y_offset + (len(record.bitmap) - 1 - row_index)
        for x, cell in enumerate(row):
            if cell == "#":
                cells.add((x + x_origin, y))
    return frozenset(cells)
