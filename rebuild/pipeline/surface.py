"""Cell enumeration and binding resolution (rebuild/M1-PLAN.md section 5, Group 1): declared rows ∪ {none} per side filtered by pairings/require/unlocks, explicit cells: bindings over side bindings over the base bitmap, per-cell anchor overrides, and the E-ANCHOR x-convention validator over resolved per-cell bitmaps.

Pairings constrain two-sided cells only; one-sided and isolated cells always exist unless require removes them (doc/rebuild-design.md section 3.2). A mid-word declined exit arrives as an `ex-bind-<bitmap>` adjustment from settlement and resolves to the withdrawal binding; the token-less exit-none cell is the boundary rendering, where the exit was never declined and the base drawing stands.
"""

from __future__ import annotations

import warnings

from rebuild.pipeline.model import Bitmap, CellId, CellPlan, ResolvedSpec, Stance, SurfaceRow, Unlock
from rebuild.pipeline.spec_load import SpecError, SpecIssue, SpecWarning


def _all_features(spec: ResolvedSpec) -> frozenset[str]:
    return frozenset(spec.registry.features)


def effective_rows(
    spec: ResolvedSpec, rune: str, stance: str, features: frozenset[str] | None = None
) -> tuple[dict[str, SurfaceRow], dict[str, SurfaceRow], dict[tuple[str, str], tuple[Unlock, ...]]]:
    """Declared rows plus unlock-added rows active under `features` (None = every registered feature). Returns (entries, exits, granted) where granted maps ("entry"|"exit", height) to the unlock records gating that row."""
    if features is None:
        features = _all_features(spec)
    stance_obj = spec.runes[rune].stances[stance]
    entries = dict(stance_obj.surface.entries)
    exits = dict(stance_obj.surface.exits)
    granted: dict[tuple[str, str], list[Unlock]] = {}
    for unlock in stance_obj.surface.unlocks:
        if unlock.feature not in features:
            continue
        for side, height, rows in (("entry", unlock.entry, entries), ("exit", unlock.exit, exits)):
            if height is None:
                continue
            if height not in rows:
                rows[height] = _synthesized_row(spec, stance_obj, side, height, unlock)
            granted.setdefault((side, height), []).append(unlock)
    return entries, exits, {key: tuple(records) for key, records in granted.items()}


def _synthesized_row(
    spec: ResolvedSpec, stance: Stance, side: str, height: str, unlock: Unlock
) -> SurfaceRow:
    y = spec.registry.heights[height]
    row = stance.bitmap.row_for_y(y)
    ink = _ink_columns(row or "")
    if not ink:
        raise SpecError(
            unlock.provenance.file if unlock.provenance else "<unknown>",
            unlock.provenance.path if unlock.provenance else "",
            f"unlock adds a {side} at {height!r} but the base bitmap has no ink at y={y}, so no anchor x can be derived by convention",
        )
    x = min(ink) if side == "entry" else max(ink) + 1
    return SurfaceRow(height=height, x=x, provenance=unlock.provenance)


def enumerate_cells_with_unlocks(
    spec: ResolvedSpec, rune: str, features: frozenset[str] = frozenset()
) -> tuple[tuple[CellId, tuple[Unlock, ...]], ...]:
    rune_obj = spec.runes[rune]
    out: list[tuple[CellId, tuple[Unlock, ...]]] = []
    for stance_name, stance in rune_obj.stances.items():
        entries, exits, granted = effective_rows(spec, rune, stance_name, features)
        pairing_grants: dict[tuple[str, str], list[Unlock]] = {}
        for unlock in stance.surface.unlocks:
            if unlock.pairing is not None and unlock.feature in features:
                key = (unlock.pairing.entry, unlock.pairing.exit)
                pairing_grants.setdefault(key, []).append(unlock)
        entry_options = [height for height, row in entries.items() if row.selectable] + [None]
        exit_options = list(exits) + [None]
        require = stance.surface.require
        for entry in entry_options:
            if entry is None and "entry" in require:
                continue
            for exit_ in exit_options:
                if exit_ is None and "exit" in require:
                    continue
                tags: list[Unlock] = []
                if entry is not None and exit_ is not None:
                    grants = pairing_grants.get((entry, exit_), [])
                    if grants:
                        tags.extend(grants)
                    else:
                        pairings = stance.surface.pairings
                        if pairings.only is not None and not any(
                            pair.entry == entry and pair.exit == exit_ for pair in pairings.only
                        ):
                            continue
                        if any(pair.entry == entry and pair.exit == exit_ for pair in pairings.never):
                            continue
                row_tags = granted.get(("entry", entry), ()) + granted.get(("exit", exit_), ())
                out.append((CellId(rune, stance_name, entry, exit_, ()), tuple(row_tags) + tuple(tags)))
    return tuple(out)


def enumerate_cells(
    spec: ResolvedSpec, rune: str, features: frozenset[str] = frozenset()
) -> tuple[CellId, ...]:
    return tuple(cell for cell, _tags in enumerate_cells_with_unlocks(spec, rune, features))


def unlocks_for_cell(spec: ResolvedSpec, cell: CellId) -> tuple[Unlock, ...]:
    """Every unlock record gating this cell; empty when the cell exists at default capability."""
    bare = CellId(cell.rune, cell.stance, cell.entry, cell.exit, ())
    if bare in enumerate_cells(spec, cell.rune, frozenset()):
        return ()
    for candidate, tags in enumerate_cells_with_unlocks(spec, cell.rune, _all_features(spec)):
        if candidate == bare:
            return tags
    raise SpecError(
        cell.rune, "", f"cell {bare} exists under no feature configuration; it cannot be resolved"
    )


def _matches_state(token: str, side: str | None, declared: dict[str, SurfaceRow]) -> bool:
    if token == "none":
        return side is None
    if token.endswith("-withdrawn"):
        return side is None and token.removesuffix("-withdrawn") in declared
    return side == token


def resolve_cell(spec: ResolvedSpec, cell: CellId) -> CellPlan:
    rune_obj = spec.runes[cell.rune]
    if cell.stance not in rune_obj.stances:
        raise SpecError(cell.rune, "", f"unknown stance {cell.stance!r} on {cell.rune}")
    stance = rune_obj.stances[cell.stance]
    entries, exits, _granted = effective_rows(spec, cell.rune, cell.stance, None)
    if cell.entry is not None and cell.entry not in entries:
        raise SpecError(
            cell.rune, "", f"{cell} names entry {cell.entry!r}, which {cell.stance!r} never offers"
        )
    if cell.exit is not None and cell.exit not in exits:
        raise SpecError(cell.rune, "", f"{cell} names exit {cell.exit!r}, which {cell.stance!r} never offers")

    explicit = None
    for binding in stance.surface.cells:
        if _matches_state(binding.entry, cell.entry, entries) and _matches_state(
            binding.exit, cell.exit, exits
        ):
            explicit = binding
            break

    entry_row = entries.get(cell.entry) if cell.entry is not None else None
    exit_row = exits.get(cell.exit) if cell.exit is not None else None

    bitmap_name: str | None = None
    # A `withdrawal:` binding applies when the bound exit's base-drawing ink must come off: a live exit at a different height, or a mid-word decline (which settlement records as an `ex-bind-<bitmap>` adjustment). The token-less exit-none cell is the boundary rendering, where the exit was never declined and the base drawing stands, dangling anchor and all (settle.py's run semantics; prototype anchor_kept_at_boundary).
    withdrawn_exit = cell.exit is not None or any(token.startswith("ex-bind-") for token in cell.adjustments)
    if explicit is not None:
        bitmap_name = explicit.bitmap
    else:
        candidates: dict[str, str] = {}
        if entry_row is not None and entry_row.joined is not None:
            candidates[entry_row.joined] = f"entry {cell.entry} joined: binding"
        if withdrawn_exit:
            for height, row in exits.items():
                if height != cell.exit and row.withdrawal not in (None, "safe"):
                    candidates[row.withdrawal] = f"exit {height} withdrawal: binding"
        if len(candidates) > 1:
            described = "; ".join(
                f"{name!r} from the {source}" for name, source in sorted(candidates.items())
            )
            raise SpecError(
                cell.rune,
                f"stances.{cell.stance}.surface.cells",
                f"cell {cell} has disagreeing side bindings ({described}) and no explicit cells: row; name the composition explicitly (doc/rebuild-design.md section 3.2)",
            )
        if candidates:
            bitmap_name = next(iter(candidates))

    joined_active = (
        entry_row is not None
        and entry_row.joined is not None
        and (explicit is None or explicit.bitmap == entry_row.joined)
    )
    entry_x: int | None = None
    if entry_row is not None:
        if explicit is not None and explicit.entry_x is not None:
            entry_x = explicit.entry_x
        elif joined_active and entry_row.joined_x is not None:
            entry_x = entry_row.joined_x
        else:
            entry_x = entry_row.x
    exit_x: int | None = None
    if exit_row is not None:
        if explicit is not None and explicit.exit_x is not None:
            exit_x = explicit.exit_x
        else:
            exit_x = exit_row.x

    safety_checks = tuple(
        ("exit", height)
        for height, row in exits.items()
        if height != cell.exit and row.withdrawal in (None, "safe")
    )

    entry_curs_only = None
    for height, row in entries.items():
        if not row.selectable:
            entry_curs_only = (row.x, spec.registry.heights[height])
            break

    unlock = None
    tags = unlocks_for_cell(spec, cell)
    if tags:
        unlock = tags[0]

    return CellPlan(
        cell=cell,
        bitmap=bitmap_name,
        entry_x=entry_x,
        exit_x=exit_x,
        entry_stub=entry_row.stub if (explicit is None and entry_row is not None) else None,
        exit_stub=exit_row.stub if (explicit is None and exit_row is not None) else None,
        entry_curs_only=entry_curs_only,
        exit_ink_y=exit_row.ink_y if exit_row is not None else None,
        x_off_convention=bool(
            (entry_row is not None and entry_row.x_off_convention)
            or (exit_row is not None and exit_row.x_off_convention)
        ),
        safety_checks=safety_checks,
        unlock=unlock,
    )


def _ink_columns(row: str) -> list[int]:
    return [column for column, pixel in enumerate(row) if pixel == "#"]


def resolved_cell_bitmap(spec: ResolvedSpec, plan: CellPlan) -> Bitmap:
    """The cell's drawing with live-side stub arithmetic applied — sufficient for anchor-convention validation; geometry's realize() is the full version with extensions and binds."""
    stance = spec.runes[plan.cell.rune].stances[plan.cell.stance]
    base = stance.bitmaps[plan.bitmap] if plan.bitmap is not None else stance.bitmap
    width = base.width
    rows = [list(row.ljust(width)) for row in base.rows]
    for stub, height in ((plan.entry_stub, plan.cell.entry), (plan.exit_stub, plan.cell.exit)):
        if stub is None or height is None:
            continue
        y = spec.registry.heights[height]
        index = len(rows) - 1 - (y - base.y_offset)
        if not 0 <= index < len(rows):
            continue
        pixel = "#" if stub.inks_when == "joined" else " "
        for column in stub.cols:
            while column >= len(rows[index]):
                rows[index].append(" ")
            rows[index][column] = pixel
    return Bitmap(rows=tuple("".join(row) for row in rows), y_offset=base.y_offset)


def _convention_issue(
    issues: list[SpecIssue], row: SurfaceRow, side: str, declared_x: int, expected: int | None, cell: CellId
) -> None:
    if expected is None:
        message = f"E-ANCHOR: {cell} has no ink at the {side} height, so the anchor convention cannot hold"
    elif expected == declared_x:
        return
    else:
        message = (
            f"E-ANCHOR: {cell} {side} anchor x={declared_x} drifts from the convention value {expected} "
            f"(entry.x = min ink, exit.x = max ink + 1) and carries no x_off_convention flag"
        )
    provenance = row.provenance
    issues.append(
        SpecIssue(
            provenance.file if provenance else "<unknown>", provenance.path if provenance else "", message
        )
    )


def check_anchor_conventions(spec: ResolvedSpec) -> tuple[SpecIssue, ...]:
    """The E-ANCHOR gate: every reachable cell's effective anchors against its resolved per-cell bitmap, plus the selectable: false GPOS-parity anchors against the base drawing."""
    issues: list[SpecIssue] = []
    every_feature = _all_features(spec)
    for rune_name, rune_obj in spec.runes.items():
        for stance_name, stance in rune_obj.stances.items():
            entries, exits, _granted = effective_rows(spec, rune_name, stance_name, every_feature)
            for height, row in entries.items():
                if not row.selectable and not row.x_off_convention:
                    ink = _ink_columns(stance.bitmap.row_for_y(spec.registry.heights[height]) or "")
                    expected = min(ink) if ink else None
                    _convention_issue(
                        issues,
                        row,
                        "entry",
                        row.x,
                        expected,
                        CellId(rune_name, stance_name, height, None, ()),
                    )
        for cell in enumerate_cells(spec, rune_name, every_feature):
            plan = resolve_cell(spec, cell)
            bitmap = resolved_cell_bitmap(spec, plan)
            entries, exits, _granted = effective_rows(spec, cell.rune, cell.stance, every_feature)
            if cell.entry is not None:
                row = entries[cell.entry]
                if not row.x_off_convention:
                    ink = _ink_columns(bitmap.row_for_y(spec.registry.heights[cell.entry]) or "")
                    _convention_issue(issues, row, "entry", plan.entry_x, min(ink) if ink else None, cell)
            if cell.exit is not None:
                row = exits[cell.exit]
                if not row.x_off_convention:
                    ink = _ink_columns(bitmap.row_for_y(spec.registry.heights[cell.exit]) or "")
                    if not ink and plan.exit_ink_y is not None:
                        ink = _ink_columns(bitmap.row_for_y(plan.exit_ink_y) or "")
                    _convention_issue(issues, row, "exit", plan.exit_x, max(ink) + 1 if ink else None, cell)
    return tuple(issues)


def check_cell_bindings(spec: ResolvedSpec) -> None:
    """Warns about explicit cells: rows matching no enumerable cell under any feature configuration."""
    for rune_name, rune_obj in spec.runes.items():
        cells = set()
        for cell in enumerate_cells(spec, rune_name, _all_features(spec)):
            cells.add((cell.stance, cell.entry, cell.exit))
        for stance_name, stance in rune_obj.stances.items():
            entries, exits, _granted = effective_rows(spec, rune_name, stance_name, None)
            for binding in stance.surface.cells:
                matched = any(
                    stance_key == stance_name
                    and _matches_state(binding.entry, entry, entries)
                    and _matches_state(binding.exit, exit_, exits)
                    for stance_key, entry, exit_ in cells
                )
                if not matched:
                    where = binding.provenance or rune_name
                    warnings.warn(
                        f"{where}: explicit cells: row ({binding.entry}, {binding.exit}) matches no enumerable cell",
                        SpecWarning,
                        stacklevel=2,
                    )
