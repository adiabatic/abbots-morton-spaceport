"""The design section 9 defect gates over the M1 subset (M1-PLAN section 5, Group 3).

`run_gates` checks the decision and treaty tables and the realized glyph records before any font exists. Errors fail the build and flags are only reported. Every defect carries a signature. A defect whose signature is in the `allow` set (the reviewed entries of `rebuild/m1-contact-allow.yaml`) is reported as blessed instead of as an error or flag. The dead-policy check produces lists, not defects, and takes no allow set.

`tables_by_config` maps each feature configuration to a `(DecisionTable, TreatyTable)` pair, as `kernel_exec.build_tables` returns, or to one object that serves as both. From the decision side this module reads `cited_provenance` and each rule's `provenance`, for the dead-policy check. From the treaty side it reads `rows`. Each row gives `left` and `right` (a CellId, a cell label or glyph name, or None at a boundary), the join height under `join`, `junction`, `height`, or `seam` (None or "break" for a break), and `extension`, the summed connector pixels on the seam.

The extension-band check is coarse because a treaty row does not say which extend record applied. A seam with a positive summed extension is checked against every band that an extend record on either rune declares at the seam's side and height, whatever its `when:`. An extension below every band, or on a seam where no record declares a band, is an error. Any other extension outside every band is a flag.

E-ANCHOR is checked only here. `_check_anchors` checks the live, non-exempt sides of every realized record against the drawing that record ships. `_check_parity_anchors` checks the `selectable: false` rows, which realize into no cell, against their stance's base drawing. A row's `x_off_convention` flag exempts that side alone, and a `trim` adjustment exempts the side it trimmed. An `anchor:` signature in the allow set blesses an E-ANCHOR finding like any other.

The dead-policy check splits unexercised records by scope. A record is exercised when its provenance is cited. The settlement engine cites the provenance of every record that fired while tabulating a configuration (refusals that removed a candidate, including inside the lookahead closure, unlocks that granted a capability, row scopes that admitted a side, and extends, contracts, and prefers that shaped a committed cell), and the table exposes them as `DecisionTable.cited_provenance`. Rule and treaty-row provenance strings are added to the cited set too. An unexercised record is deferred-partner when one of its `when:` conditions names only families that are not modeled runes. A row scope is deferred-partner when every one of its conditions names only such families. Otherwise the record or scope is dead within the alphabet. `run_m1` writes both lists to `pipeline_summary.json`, and neither fails the build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

from rebuild.pipeline.model import (
    CellId,
    Condition,
    GlyphRecord,
    PolicyRecord,
    ResolvedSpec,
    Unlock,
    When,
)
from rebuild.pipeline import geometry


@dataclass(frozen=True)
class Defect:
    code: str
    signature: str
    message: str


@dataclass
class DefectReport:
    errors: list[Defect] = field(default_factory=list)
    flags: list[Defect] = field(default_factory=list)
    blessed: list[Defect] = field(default_factory=list)
    deferred_partner: list[str] = field(default_factory=list)
    dead_in_alphabet: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail_if_broken(self) -> None:
        if self.errors:
            lines = [f"{d.code} [{d.signature}]: {d.message}" for d in self.errors]
            raise AssertionError("defect gates failed:\n" + "\n".join(lines))


def _decision_and_treaty(value) -> tuple[object, object]:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return value[0], value[1]
    return value, value


def _treaty_rows(treaty) -> Iterable:
    return getattr(treaty, "rows", ())


def _row_join(row):
    for attribute in ("join", "junction", "height", "seam"):
        if hasattr(row, attribute):
            value = getattr(row, attribute)
            return None if value in (None, "break") else value
    return None


def _cell_label(cell: CellId) -> str:
    """The cell's label with heights as names (`en-baseline`). `_endpoint_index` also indexes the `settle.cell_label` form, with heights as y values."""
    parts = [cell.rune, cell.stance]
    if cell.entry is not None:
        parts.append(f"en-{cell.entry}")
    if cell.exit is not None:
        parts.append(f"ex-{cell.exit}")
    parts.extend(cell.adjustments)
    return ".".join(parts)


def _endpoint_index(glyphs: Mapping[CellId, GlyphRecord]) -> dict[str, CellId]:
    index: dict[str, CellId] = {}
    for cell, record in glyphs.items():
        index[record.name] = cell
        index[_cell_label(cell)] = cell
        label = [cell.rune, cell.stance]
        if cell.entry is not None:
            label.append(f"en-y{_HEIGHT_Y.get(cell.entry, cell.entry)}")
        if cell.exit is not None:
            label.append(f"ex-y{_HEIGHT_Y.get(cell.exit, cell.exit)}")
        label.extend(cell.adjustments)
        index[".".join(label)] = cell
    return index


_HEIGHT_Y = {"baseline": 0, "x-height": 5, "y6": 6, "top": 8}


def _resolve_endpoint(value, glyphs: Mapping[CellId, GlyphRecord], index: dict[str, CellId]) -> CellId | None:
    if value is None:
        return None
    if isinstance(value, CellId):
        return value if value in glyphs else None
    return index.get(str(value))


def _reachable_cells(decision) -> frozenset[CellId]:
    accessor: Callable[[], Iterable[CellId]] | Iterable[CellId] | None = getattr(
        decision, "reachable_cells", None
    )
    if callable(accessor):
        return frozenset(accessor())
    return frozenset(accessor or ())


def _report(
    report: DefectReport, allow: frozenset[str], code: str, signature: str, message: str, error: bool
) -> None:
    defect = Defect(code, signature, message)
    if signature in allow:
        report.blessed.append(defect)
    elif error:
        report.errors.append(defect)
    else:
        report.flags.append(defect)


def _check_dangle(report: DefectReport, allow: frozenset[str], glyphs: Mapping[CellId, GlyphRecord]) -> None:
    for cell, record in glyphs.items():
        for side, height in record.safety_checks:
            if not geometry.verify_withdrawal_safe(record, side, height):
                _report(
                    report,
                    allow,
                    "E-DANGLE",
                    f"dangle:{record.name}:{side}:{height}",
                    f"{cell}: declined {side} at {height} has reaching ink and no withdrawal binding",
                    error=True,
                )


def _check_anchors(report: DefectReport, allow: frozenset[str], glyphs: Mapping[CellId, GlyphRecord]) -> None:
    for cell, record in glyphs.items():
        if record.entry is not None and "entry" not in record.convention_exempt:
            span = geometry.ink_span(record.bitmap, record.y_offset, record.entry[1])
            if span is None or span[0] != record.entry[0]:
                _report(
                    report,
                    allow,
                    "E-ANCHOR",
                    f"anchor:{record.name}:entry",
                    f"{cell}: entry anchor x={record.entry[0]} but leftmost ink at y={record.entry[1]} is {span[0] if span else 'absent'}",
                    error=True,
                )
        if record.exit is not None and "exit" not in record.convention_exempt:
            y = record.exit[1] if record.exit_ink_y is None else record.exit_ink_y
            span = geometry.ink_span(record.bitmap, record.y_offset, y)
            if span is None or span[1] + 1 != record.exit[0]:
                _report(
                    report,
                    allow,
                    "E-ANCHOR",
                    f"anchor:{record.name}:exit",
                    f"{cell}: exit anchor x={record.exit[0]} but rightmost ink + 1 at y={y} is {span[1] + 1 if span else 'absent'}",
                    error=True,
                )


def _check_parity_anchors(report: DefectReport, allow: frozenset[str], spec: ResolvedSpec) -> None:
    """Check the entry anchor of every declared `selectable: false` row against the stance's base drawing. Such a row realizes into no cell, because settlement never offers it, so `_check_anchors` never sees it; its anchor reaches GPOS only as `GlyphRecord.entry_curs_only`. Rows added by unlocks are always selectable, so only declared rows are checked."""
    for rune_name, rune in spec.runes.items():
        for stance in rune.stances.values():
            for height, row in stance.surface.entries.items():
                if row.selectable or row.x_off_convention:
                    continue
                y = spec.registry.heights[height]
                span = geometry.ink_span(stance.bitmap.rows, stance.bitmap.y_offset, y)
                if span is None or span[0] != row.x:
                    _report(
                        report,
                        allow,
                        "E-ANCHOR",
                        f"anchor:{rune_name}.{stance.name}.en-{height}:parity",
                        f"{rune_name}.{stance.name} entry {height} (selectable: false) anchor x={row.x} but leftmost ink at y={y} is {span[0] if span else 'absent'}",
                        error=True,
                    )


def _check_treaties(
    report: DefectReport,
    allow: frozenset[str],
    spec: ResolvedSpec,
    tables_by_config: Mapping,
    glyphs: Mapping[CellId, GlyphRecord],
) -> None:
    seen: set[tuple] = set()
    index = _endpoint_index(glyphs)
    for config, value in tables_by_config.items():
        _decision, treaty = _decision_and_treaty(value)
        for row in _treaty_rows(treaty):
            left = _resolve_endpoint(getattr(row, "left", None), glyphs, index)
            right = _resolve_endpoint(getattr(row, "right", None), glyphs, index)
            join = _row_join(row)
            extension = getattr(row, "extension", 0) or 0
            key = (left, right, join, extension)
            if key in seen:
                continue
            seen.add(key)
            if left is None or right is None:
                continue
            left_record = glyphs.get(left)
            right_record = glyphs.get(right)
            if join is not None and left_record is not None and right_record is not None:
                signature = f"unrealized:{left_record.name}:{right_record.name}:{join}"
                try:
                    gap = geometry.seam_gap(left_record, right_record, join)
                except geometry.GeometryError as error:
                    _report(
                        report, allow, "E-UNREALIZED", signature, f"{left} -> {right}: {error}", error=True
                    )
                else:
                    if gap != 0:
                        _report(
                            report,
                            allow,
                            "E-UNREALIZED",
                            signature,
                            f"{left} -> {right} at {join}: gap {gap}, want 0",
                            error=True,
                        )
                _check_band(report, allow, spec, left, right, join, extension)
            if left_record is not None and right_record is not None:
                _check_contact(report, allow, left_record, right_record, join)


def _check_band(report, allow, spec: ResolvedSpec, left: CellId, right: CellId, join, extension: int) -> None:
    if extension <= 0:
        return
    bands: list[tuple[int, int]] = []
    for rune_name, side_key, height in ((left.rune, "exit", join), (right.rune, "entry", join)):
        rune = spec.runes.get(rune_name)
        if rune is None:
            continue
        for record in rune.policy.extend:
            if getattr(record, side_key) != height:
                continue
            band = record.ok
            if band is None:
                assert record.by is not None
                band = (record.by, record.by)
            bands.append(band)
    if not bands:
        _report(
            report,
            allow,
            "E-EXTENSION-BAND",
            f"band:{left}:{right}:{join}",
            f"{left} -> {right}: seam carries extension {extension} but neither rune declares an extend at that side and height",
            error=True,
        )
        return
    if any(lo <= extension <= hi for lo, hi in bands):
        return
    too_short = extension < min(lo for lo, _hi in bands)
    _report(
        report,
        allow,
        "E-EXTENSION-BAND",
        f"band:{left}:{right}:{join}:{extension}",
        f"{left} -> {right}: extension {extension} outside every authored band {sorted(bands)}",
        error=too_short,
    )


def _check_contact(report, allow, left: GlyphRecord, right: GlyphRecord, join) -> None:
    if join is not None:
        if left.exit is None or right.entry is None:
            return
        offset = left.exit[0] - right.entry[0]
        seam_y = left.exit[1]
    else:
        advance = (
            left.advance_width
            if left.advance_width is not None
            else len(max(left.bitmap, key=len, default="")) + 2
        )
        offset = (
            advance - 1
        )  # tools/build_font.py compiles a Senior Quikscript letter with no explicit advance one pixel narrower than width + 2
        seam_y = None
    left_ink = geometry.ink_cells(left)
    right_ink = geometry.ink_cells(right, x_origin=offset)
    overlap = left_ink & right_ink
    if overlap:
        _report(
            report,
            allow,
            "E-CONTACT",
            f"contact:{left.name}:{right.name}:overlap",
            f"{left.name} + {right.name}: ink overlap at {sorted(overlap)[:4]}",
            error=True,
        )
    if seam_y is None:
        return
    for y in sorted({y for x, y in left_ink if y != seam_y and (x + 1, y) in right_ink}):
        _report(
            report,
            allow,
            "E-CONTACT",
            f"contact:{left.name}:{right.name}:y{y}",
            f"{left.name} + {right.name}: off-anchor ink contact at y={y} (seam is y={seam_y})",
            error=True,
        )


def _condition_positive_families(spec: ResolvedSpec, rune_name: str, condition: Condition) -> set[str]:
    """The families a condition requires: its family literals plus the members of the classes and groups it names. `except:` entries only narrow this set, so they are not collected."""
    families = set(condition.family)
    rune = spec.runes.get(rune_name)
    for klass in condition.klass:
        members = spec.registry.predicate_classes.get(klass)
        if members is None and rune is not None:
            members = rune.policy.groups.get(klass)
        families.update(members or ())
    return families


def _when_axes(spec: ResolvedSpec, rune_name: str, when: When | None) -> list[set[str]]:
    """One required-family set per family-constrained condition of a `when:` (left, right, and each `then:` hop). A record can fire only when every set has a modeled member, so one set with no modeled member makes the record deferred-partner."""
    axes: list[set[str]] = []
    if when is None:
        return axes
    for condition in (when.left, when.right):
        while condition is not None:
            positive = _condition_positive_families(spec, rune_name, condition)
            if positive:
                axes.append(positive)
            condition = condition.then
    return axes


def _check_dead_policy(report: DefectReport, spec: ResolvedSpec, tables_by_config: Mapping) -> None:
    cited: set[str] = set()
    for value in tables_by_config.values():
        decision, treaty = _decision_and_treaty(value)
        cited.update(str(item) for item in getattr(decision, "cited_provenance", ()) or ())
        for rule in getattr(decision, "rules", ()):
            for item in getattr(rule, "provenance", ()) or ():
                cited.add(str(item))
        for row in _treaty_rows(treaty):
            for item in getattr(row, "provenance", ()) or ():
                cited.add(str(item))

    modeled = set(spec.runes)

    def classify(rune_name: str, label: str, when: When | None, provenance) -> None:
        if provenance is not None and str(provenance) in cited:
            return
        axes = _when_axes(spec, rune_name, when)
        empty_axes = [axis for axis in axes if not (axis & modeled)]
        if empty_axes:
            partners = sorted(set().union(*empty_axes) - modeled)
            report.deferred_partner.append(f"{label} (partners: {', '.join(partners)})")
        else:
            report.dead_in_alphabet.append(label)

    for rune_name, rune in spec.runes.items():
        for kind in ("refuse", "prefer", "extend", "contract", "resolve"):
            for record in getattr(rune.policy, kind):
                assert isinstance(record, PolicyRecord)
                label = str(record.provenance) if record.provenance else f"{rune_name}.policy.{kind}"
                classify(rune_name, label, record.when, record.provenance)
        for stance in rune.stances.values():
            for side_name, rows in (("entries", stance.surface.entries), ("exits", stance.surface.exits)):
                for height, row in rows.items():
                    if not row.scope:
                        continue
                    label = f"{rune_name}.{stance.name}.{side_name}.{height}.scope"
                    if row.provenance is not None and str(row.provenance) in cited:
                        continue
                    # Scope conditions are an OR list: the row is admittable in-alphabet if any condition is family-unconstrained or names a modeled family.
                    positives = [
                        _condition_positive_families(spec, rune_name, condition) for condition in row.scope
                    ]
                    if all(positive and not (positive & modeled) for positive in positives):
                        partners = sorted(set().union(*positives) - modeled)
                        report.deferred_partner.append(f"{label} (partners: {', '.join(partners)})")
                    elif label not in cited:
                        report.dead_in_alphabet.append(label)
            for unlock in stance.surface.unlocks:
                assert isinstance(unlock, Unlock)
                label = (
                    str(unlock.provenance)
                    if unlock.provenance
                    else f"{rune_name}.{stance.name}.unlocks.{unlock.feature}"
                )
                classify(rune_name, label, unlock.when, unlock.provenance)


def run_gates(
    spec: ResolvedSpec,
    tables_by_config: Mapping,
    glyphs: Mapping[CellId, GlyphRecord],
    allow: frozenset[str] = frozenset(),
) -> DefectReport:
    report = DefectReport()
    _check_dangle(report, allow, glyphs)
    _check_anchors(report, allow, glyphs)
    _check_parity_anchors(report, allow, spec)
    _check_treaties(report, allow, spec, tables_by_config, glyphs)
    _check_dead_policy(report, spec, tables_by_config)
    report.notes.append(
        f"gates ran over {len(glyphs)} glyphs and {len(tables_by_config)} configurations; "
        f"{len(report.errors)} errors, {len(report.flags)} flags, {len(report.blessed)} blessed"
    )
    return report
