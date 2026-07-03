"""M1-mode unit assembly for the review surface (rebuild/REVIEW-PLAN.md §1.1, §2.1): load rebuild/out/m1/divergence-audit.tsv and rebuild/m1-divergences.yaml, dedupe the 15,528 audit rows to (codepoints, baseline, new) units, and order them for triage — ledger class in ledger file order, then lead-family-pair group in code-point order, then codepoints — with fixed batch slices assigned over the global order."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ACCEPTANCE_CONFIGS = ("default", "ss02", "ss03", "ss04", "ss05", "ss02+ss03", "ss02+ss03+ss05", "ss10")
BATCH_SIZE = 300

UNMATCHED_CLASS = "UNMATCHED"

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
    no_verdict: bool
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
    batch: int | None = None
    render_groups: tuple[tuple[str, ...], ...] = ()
    ink_identical: bool = False
    no_verdict: bool = False
    config_classes: dict[str, str] = field(default_factory=dict)
    family_id: str = ""

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
                no_verdict=bool(entry.get("no_verdict", False)),
                count=int(entry.get("count", 0)),
                exemplar_keys=frozenset(
                    (exemplar["config"], exemplar["codepoints"]) for exemplar in entry.get("exemplars", ())
                ),
            )
        )
    return classes


def synthesize_family_classes(
    units: list[Unit],
    family_order: list[str],
    family_why: dict[str, str],
) -> list[LedgerClass]:
    """Synthetic LedgerClass records for the verdict families present among the UNMATCHED units, in `family_order`. `status='unmatched'` marks them as a presentation-only grouping — no ledger predicate, the oracle stays dirty until they are adjudicated. Appended after the real ledger classes by the build so `build_m1`'s existing class loop emits a shard + manifest entry per family with no new build logic. `family_order`/`family_why` come from `rebuild.review.families`, passed in so this module stays free of the enrich/families import cycle."""
    counts: dict[str, int] = {}
    for unit in units:
        if unit.family_id:
            counts[unit.family_id] = counts.get(unit.family_id, 0) + 1
    return [
        LedgerClass(
            id=family_id,
            status="unmatched",
            why=family_why.get(family_id, ""),
            ink_identical=False,
            no_verdict=False,
            count=counts[family_id],
            exemplar_keys=frozenset(),
        )
        for family_id in family_order
        if family_id in counts
    ]


def group_for(codepoint_values: tuple[int, ...], family_of: dict[int, str]) -> str:
    families = [family_of[value] for value in codepoint_values if value in family_of]
    return ":".join(families[:2]) if families else "(boundaries)"


def _config_index(config: str) -> int:
    try:
        return ACCEPTANCE_CONFIGS.index(config)
    except ValueError:
        return len(ACCEPTANCE_CONFIGS)


def render_groups_for_rows(rows: tuple[AuditRow, ...]) -> tuple[tuple[str, ...], ...]:
    """Partition a unit's configs by rendered-outcome identity — the (baseline, new) cell-name tuples its audit rows carry, which are everything position-bearing the rows record. The M1 dedupe key already includes both tuples, so every real unit yields exactly one group (the documented invariant, locked in by tests); the grouping is computed rather than assumed so data whose configs render differently would surface as extra stacked groups instead of being silently collapsed."""
    groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
    for row in rows:
        groups.setdefault((row.baseline, row.new), []).append(row.config)
    return tuple(tuple(configs) for configs in groups.values())


def build_units(
    rows: list[AuditRow],
    ledger: list[LedgerClass],
    family_of: dict[int, str],
) -> list[Unit]:
    """Dedupe to (codepoints, baseline, new) units and return them in triage order with ids assigned; batch indices are assigned later by `assign_batches`, once the build has computed each unit's ink_identical flag. A triple's matched ledger class can vary by config — most often a window already blessed under ss03 but UNMATCHED (novel) under the default config — so each unit carries the full per-config class map in `config_classes`, and its own `class_id` is the single matched class when the triple is everywhere-matched, or the UNMATCHED sentinel when any config leaves it unmatched (UNMATCHED-wins, so the novel default behavior is what gets adjudicated; the blessed configs ride along in `config_classes` for display). A triple resolving to two distinct *matched* classes would be a genuine classification bug and still raises."""
    exempt_classes = {entry.id for entry in ledger if entry.no_verdict}
    by_triple: dict[tuple[str, tuple[str, ...], tuple[str, ...]], list[AuditRow]] = {}
    for row in rows:
        by_triple.setdefault((row.codepoints, row.baseline, row.new), []).append(row)

    units: list[Unit] = []
    for (codepoints, baseline, new), members in by_triple.items():
        config_classes = {member.config: member.matched_entry for member in members}
        classes = set(config_classes.values())
        matched = classes - {UNMATCHED_CLASS}
        if len(matched) > 1:
            raise ValueError(f"unit {codepoints} spans multiple matched ledger classes: {sorted(matched)}")
        class_id = UNMATCHED_CLASS if UNMATCHED_CLASS in classes else matched.pop()
        ordered = tuple(sorted(members, key=lambda member: _config_index(member.config)))
        kinds = tuple(sorted({kind for member in members for kind in member.kinds}))
        units.append(
            Unit(
                codepoints=codepoints,
                baseline=baseline,
                new=new,
                class_id=class_id,
                rows=ordered,
                configs=tuple(member.config for member in ordered),
                kinds=kinds,
                group=group_for(parse_codepoints(codepoints), family_of),
                render_groups=render_groups_for_rows(ordered),
                no_verdict=class_id in exempt_classes,
                config_classes=config_classes,
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
        unit.exemplar = any((row.config, row.codepoints) in exemplar_keys for row in unit.rows)
    return units


def assign_batches(units: list[Unit], batch_size: int = BATCH_SIZE) -> int:
    """Batches cover the human workload only: the remaining units get fixed slices of batch_size in triage order, while ink-identical units (machine-approved) and units of no-verdict ledger classes carry batch None — neither is ever paged to a human. Returns the batch count."""
    index = 0
    for unit in units:
        if unit.ink_identical or unit.no_verdict:
            unit.batch = None
        else:
            unit.batch = index // batch_size
            index += 1
    return (index + batch_size - 1) // batch_size


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
) -> Workload:
    rows = load_audit(audit_path)
    ledger = load_ledger(ledger_path)
    units = build_units(rows, ledger, family_of)
    present = {unit.class_id for unit in units}
    return Workload(
        units=units,
        ledger=ledger,
        row_count=len(rows),
        classes_present=[entry for entry in ledger if entry.id in present],
    )
