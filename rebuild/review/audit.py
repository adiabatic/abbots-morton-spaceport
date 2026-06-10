"""M1-mode unit assembly for the review surface (rebuild/REVIEW-PLAN.md §1.1, §2.1): load rebuild/out/m1/divergence-audit.tsv and rebuild/m1-divergences.yaml, dedupe the 15,528 audit rows to (codepoints, baseline, new) units, and order them for triage — ledger class in ledger file order, then lead-family-pair group in code-point order, then codepoints — with fixed batch slices assigned over the global order."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ACCEPTANCE_CONFIGS = ("default", "ss02", "ss03", "ss04", "ss05", "ss02+ss03", "ss02+ss03+ss05", "ss10")
BATCH_SIZE = 300

AUDIT_HEADER = ("config", "codepoints", "kinds", "matched_entry", "baseline", "new")


@dataclass(frozen=True)
class AuditRow:
    config: str
    codepoints: str
    kinds: tuple[str, ...]
    matched_entry: str
    baseline: tuple[str, ...]
    new: tuple[str, ...]


@dataclass(frozen=True)
class LedgerClass:
    id: str
    status: str
    why: str
    ink_identical: bool
    count: int
    exemplar_keys: frozenset[tuple[str, str]]  # (config, codepoints)


@dataclass
class Unit:
    codepoints: str
    baseline: tuple[str, ...]
    new: tuple[str, ...]
    class_id: str
    rows: tuple[AuditRow, ...]
    configs: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    group: str = ""
    exemplar: bool = False
    unit_id: str = ""
    batch: int = 0

    @property
    def codepoint_values(self) -> tuple[int, ...]:
        return parse_codepoints(self.codepoints)


def parse_codepoints(codepoints: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in codepoints.split(":"))


def format_codepoints(values: tuple[int, ...]) -> str:
    return ":".join(f"{value:04X}" for value in values)


def load_audit(path: Path) -> list[AuditRow]:
    rows: list[AuditRow] = []
    with open(path, encoding="utf-8") as handle:
        header = next(handle).rstrip("\n").split("\t")
        if tuple(header) != AUDIT_HEADER:
            raise ValueError(f"{path}: unexpected audit header {header!r}")
        for line in handle:
            if not line.strip():
                continue
            config, codepoints, kinds, matched_entry, baseline, new = line.rstrip("\n").split("\t")
            rows.append(
                AuditRow(
                    config=config,
                    codepoints=codepoints,
                    kinds=tuple(kinds.split(",")),
                    matched_entry=matched_entry,
                    baseline=tuple(baseline.split("|")),
                    new=tuple(new.split("|")),
                )
            )
    return rows


def load_ledger(path: Path) -> list[LedgerClass]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    classes: list[LedgerClass] = []
    for entry in entries:
        classes.append(
            LedgerClass(
                id=entry["id"],
                status=entry.get("status", ""),
                why=(entry.get("why") or "").strip(),
                ink_identical=bool(entry.get("ink_identical", False)),
                count=int(entry.get("count", 0)),
                exemplar_keys=frozenset(
                    (exemplar["config"], exemplar["codepoints"]) for exemplar in entry.get("exemplars", ())
                ),
            )
        )
    return classes


def group_for(codepoint_values: tuple[int, ...], family_of: dict[int, str]) -> str:
    families = [family_of[value] for value in codepoint_values if value in family_of]
    return ":".join(families[:2]) if families else "(boundaries)"


def _config_index(config: str) -> int:
    try:
        return ACCEPTANCE_CONFIGS.index(config)
    except ValueError:
        return len(ACCEPTANCE_CONFIGS)


def build_units(
    rows: list[AuditRow],
    ledger: list[LedgerClass],
    family_of: dict[int, str],
    batch_size: int = BATCH_SIZE,
) -> list[Unit]:
    """Dedupe to (codepoints, baseline, new) units and return them in triage order with ids and batch indices assigned. The ledger class is a function of the triple; a triple matched to two classes would be an upstream classification bug, so it raises."""
    by_triple: dict[tuple[str, tuple[str, ...], tuple[str, ...]], list[AuditRow]] = {}
    for row in rows:
        by_triple.setdefault((row.codepoints, row.baseline, row.new), []).append(row)

    units: list[Unit] = []
    for (codepoints, baseline, new), members in by_triple.items():
        classes = {member.matched_entry for member in members}
        if len(classes) != 1:
            raise ValueError(f"unit {codepoints} spans multiple ledger classes: {sorted(classes)}")
        ordered = tuple(sorted(members, key=lambda member: _config_index(member.config)))
        kinds = tuple(sorted({kind for member in members for kind in member.kinds}))
        units.append(
            Unit(
                codepoints=codepoints,
                baseline=baseline,
                new=new,
                class_id=classes.pop(),
                rows=ordered,
                configs=tuple(member.config for member in ordered),
                kinds=kinds,
                group=group_for(parse_codepoints(codepoints), family_of),
            )
        )

    class_order = {entry.id: index for index, entry in enumerate(ledger)}
    exemplar_keys = {key for entry in ledger for key in entry.exemplar_keys}
    family_rank = {name: value for value, name in family_of.items()}

    def group_key(unit: Unit) -> tuple:
        return tuple(family_rank.get(name, 10**6) for name in unit.group.split(":"))

    units.sort(
        key=lambda unit: (
            class_order.get(unit.class_id, len(class_order)),
            group_key(unit),
            len(unit.codepoint_values),
            unit.codepoint_values,
        )
    )
    for index, unit in enumerate(units):
        unit.unit_id = f"u-{index:04d}"
        unit.batch = index // batch_size
        unit.exemplar = any((row.config, row.codepoints) in exemplar_keys for row in unit.rows)
    return units


@dataclass
class Workload:
    units: list[Unit]
    ledger: list[LedgerClass]
    row_count: int
    classes_present: list[LedgerClass] = field(default_factory=list)

    def units_by_class(self) -> dict[str, list[Unit]]:
        grouped: dict[str, list[Unit]] = {}
        for unit in self.units:
            grouped.setdefault(unit.class_id, []).append(unit)
        return grouped


def load_workload(
    audit_path: Path,
    ledger_path: Path,
    family_of: dict[int, str],
    batch_size: int = BATCH_SIZE,
) -> Workload:
    rows = load_audit(audit_path)
    ledger = load_ledger(ledger_path)
    units = build_units(rows, ledger, family_of, batch_size)
    present = {unit.class_id for unit in units}
    return Workload(
        units=units,
        ledger=ledger,
        row_count=len(rows),
        classes_present=[entry for entry in ledger if entry.id in present],
    )
