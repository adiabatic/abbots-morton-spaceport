"""M1-mode unit assembly for the review surface (rebuild/REVIEW-PLAN.md §1.1, §2.1): load rebuild/out/m1/divergence-audit.tsv and rebuild/m1-divergences.yaml, dedupe the audit rows to (codepoints, baseline, new) units, and order them for triage — ledger class in ledger file order, then lead-family-pair group in code-point order, then codepoints — with fixed batch slices assigned over the global order. The name-grain dedupe key can split one visual question into sibling units when a config merely relabels a glyph without moving ink; the build folds those back together with `merge_ink_duplicate_units` before enrichment and batching."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ACCEPTANCE_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10")
BATCH_SIZE = 300

UNMATCHED_CLASS = "UNMATCHED"

AUDIT_HEADER = ("config", "codepoints", "kinds", "matched_entry", "baseline", "new")


@dataclass(frozen=True, slots=True)
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


MACHINE_CHANNELS = ("ink_identical", "picture_identical", "junior_equivalent")


def machine_approved(fragment) -> bool:
    """Whether a unit's JSON fragment carries any machine-approval flag, in the one precedence order MACHINE_CHANNELS fixes (ink identity is tried first, picture identity only where ink identity fails, Junior equivalence only where both fail, so at most one is ever true)."""
    return any(fragment.get(channel) is True for channel in MACHINE_CHANNELS)


# The two channels whose units the build writes slim — `drafts: null`, and an explain cut to its header (the settled names per position, which on an ink-identical window is the whole of what changed). Nothing on them is ever paged to a human, so the drafts a reviewer would act on and the candidate table behind them were bytes nobody opened, over half of every shard. Picture identity is not here: its units are the machine-approved ones whose ink actually moved, the occasional human look is at exactly them, and its flag sits outside the content key (`CARRY_PRESENTATION_KEYS` in unit_cache.py) — both slim channels sit inside it, which is what lets a cache-served fragment be slim exactly when a fresh emission would be.
SLIM_CHANNELS = ("ink_identical", "junior_equivalent")


def slim_detail(fragment) -> bool:
    """Whether a unit's JSON fragment is written slim: `drafts: null` and a header-only explain, the shape `build.check_unit` demands on every SLIM_CHANNELS unit and forbids on every other."""
    return any(fragment.get(channel) is True for channel in SLIM_CHANNELS)


@dataclass(slots=True)
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
    picture_identical: bool = False
    junior_equivalent: bool = False
    ink_deltas: dict[str, str] = field(default_factory=dict)
    no_verdict: bool = False
    config_classes: dict[str, str] = field(default_factory=dict)
    family_id: str = ""
    echo: str | None = None
    cluster: str | None = None

    @property
    def codepoint_values(self) -> tuple[int, ...]:
        return parse_codepoints(self.codepoints)

    @property
    def machine_approved(self) -> bool:
        return any(getattr(self, channel) for channel in MACHINE_CHANNELS)

    @property
    def slim_detail(self) -> bool:
        return any(getattr(self, channel) for channel in SLIM_CHANNELS)


def parse_codepoints(codepoints: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in codepoints.split(":"))


def format_codepoints(values: tuple[int, ...]) -> str:
    return ":".join(f"{value:04X}" for value in values)


def load_audit(path: Path) -> list[AuditRow]:
    """Every row the divergence audit states, in file order. Each label and each name tuple is pooled to one instance the way `kernel_io.read_transitions` pools its own, because the audit restates one small vocabulary of glyph names and the acceptance configurations on every one of its rows, and the surface build's parent holds the whole list alive through `unit.rows` for its entire length. Pooling the tuples keys on the built tuple rather than on the raw field text, so the split strings the file states are the only thing the reader drops."""
    rows: list[AuditRow] = []
    labels: dict[str, str] = {}
    names: dict[tuple[str, ...], tuple[str, ...]] = {}

    def label(value: str) -> str:
        return labels.setdefault(value, value)

    def name_tuple(value: str, separator: str) -> tuple[str, ...]:
        built = tuple(labels.setdefault(part, part) for part in value.split(separator))
        return names.setdefault(built, built)

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
                    config=label(config),
                    codepoints=label(codepoints),
                    kinds=name_tuple(kinds, ","),
                    matched_entry=label(matched_entry),
                    baseline=name_tuple(baseline, "|"),
                    new=name_tuple(new, "|"),
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


def _sibling_windows(units: list[Unit]) -> dict[str, list[Unit]]:
    by_window: dict[str, list[Unit]] = {}
    for unit in units:
        by_window.setdefault(unit.codepoints, []).append(unit)
    return {codepoints: siblings for codepoints, siblings in by_window.items() if len(siblings) >= 2}


def signature_rows(units: list[Unit]) -> list[AuditRow]:
    """One audit row per signature `merge_ink_duplicate_units` will ask for: every config of every sibling in a multi-sibling window, as the row that pins that (window, config)'s rendered names in both fonts. Sharing `_sibling_windows` with the merge is what keeps this enumeration exact — a signature provider built over these rows can never be asked for a pair outside them."""
    return [row for siblings in _sibling_windows(units).values() for unit in siblings for row in unit.rows]


def merge_ink_duplicate_units(
    units: list[Unit], ink_sig, exempt_classes: Collection[str] = frozenset()
) -> dict:
    """Fold sibling units of the same window whose placed ink is identical in both fonts across every config they cover. The (codepoints, baseline, new) dedupe key is name-grain, so a config that merely relabels a glyph — the old font's ss04 lookups rename word-initial ·It without changing its ink — splits one visual question into two units and asks it twice. `ink_sig(text, config)` supplies the rendered-outcome identity (see InkComparator.signature); units are only folded when every config on both sides yields the same signature, so a fold is proof the units present the same picture. The survivor is the sibling with the earliest config; it absorbs the others' rows, configs, kinds, and config_classes, keeps its own (earliest-config) baseline/new name tuples for display, re-resolves its class with the same UNMATCHED-wins rule as build_units, and collapses to a single render group (ink identity is exactly render-group identity). A fold that would put two distinct matched ledger classes on one unit is skipped — different names legitimately hit different ledger predicates — and counted in the returned stats. Mutates `units` in place and renumbers unit ids to stay contiguous; run before enrichment and batch assignment."""
    folded: set[int] = set()
    stats = {"windows_folded": 0, "units_folded": 0, "kept_split_matched_classes": 0}
    for codepoints, siblings in _sibling_windows(units).items():
        text = "".join(chr(value) for value in parse_codepoints(codepoints))
        groups: dict[tuple, list[Unit]] = {}
        for unit in siblings:
            signatures = {ink_sig(text, config) for config in unit.configs}
            if len(signatures) == 1:
                groups.setdefault(signatures.pop(), []).append(unit)
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda unit: _config_index(unit.configs[0]))
            survivor = members[0]
            merged_any = False
            for unit in members[1:]:
                matched = {cls for cls in survivor.config_classes.values() if cls != UNMATCHED_CLASS} | {
                    cls for cls in unit.config_classes.values() if cls != UNMATCHED_CLASS
                }
                if len(matched) > 1:
                    stats["kept_split_matched_classes"] += 1
                    continue
                rows = tuple(sorted(survivor.rows + unit.rows, key=lambda row: _config_index(row.config)))
                survivor.rows = rows
                survivor.configs = tuple(row.config for row in rows)
                survivor.kinds = tuple(sorted(set(survivor.kinds) | set(unit.kinds)))
                survivor.config_classes = {**survivor.config_classes, **unit.config_classes}
                classes = set(survivor.config_classes.values())
                survivor.class_id = UNMATCHED_CLASS if UNMATCHED_CLASS in classes else matched.pop()
                survivor.no_verdict = survivor.class_id in exempt_classes
                survivor.render_groups = (survivor.configs,)
                survivor.exemplar = survivor.exemplar or unit.exemplar
                folded.add(id(unit))
                merged_any = True
            if merged_any:
                stats["windows_folded"] += 1
    if folded:
        units[:] = [unit for unit in units if id(unit) not in folded]
        for index, unit in enumerate(units):
            unit.unit_id = f"u-{index:04d}"
    stats["units_folded"] = len(folded)
    return stats


def assign_batches(units: list[Unit], batch_size: int = BATCH_SIZE) -> int:
    """Batches cover the human workload only: the remaining units get fixed slices of batch_size in triage order, while machine-approved units (ink-identical, picture-identical, or junior-equivalent) and units of no-verdict ledger classes carry batch None — none is ever paged to a human. Returns the batch count."""
    index = 0
    for unit in units:
        if unit.machine_approved or unit.no_verdict:
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
