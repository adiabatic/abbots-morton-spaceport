"""The review facts: the counts and structural facts a corpus build reduces its state to, and the regenerator that writes them to rebuild/review-facts-pins.json, the last accepted review facts. Every non-staging artifact-cycle pass rewrites that file from the corpus's review-facts.json sidecar and names what moved in its invariant block. Committing the rewritten file accepts the new review facts. No gate checks the checked-in numbers, so a changed count is something to read, not a failure.

The file has two blocks so the cycle can say what kind of change a pass made. `volatile` holds the manifest, built, audit, ink, and unmatched_groups groups, which change with every migrated letter. The unmatched_groups group counts the pre-merge units in each change group, the UNMATCHED units and the units re-routed out of an `ink_identical` class because their picture changes, and under `rerouted` how many each `ink_identical` class re-routed, so a broken identical claim shows as a number rather than as change-group growth. `invariant` holds the structural facts a person should review when they change: which classes the corpus ships, which classes the build machine-approves, which are exempt from individual verdicts, and which change groups the corpus reaches. Its class lists are in ledger order and then `CHANGE_GROUP_ORDER` (`class_rank`), not in the manifest's section order, so a class that changes section does not reorder them. The invariant block repeats structure that the volatile groups' keys already carry. Both blocks come from one emission, so they cannot disagree, and the separate block lets `invariant_delta` name a new class or a new no-verdict exemption in one summary line and lets the cycle print a diff of that block alone. `rebuild/out/cycle_summary.json` also records the corpus's totals. The machine-approved and change-group lists are recorded nowhere else, because both are emergent: a class is machine-approved when the build approved at least one of its units through any machine check, whatever the ledger declares. `ledger_coverage` compares the ledger's declarations with them.

No test reads this file, since a build asserting the numbers it just wrote would check nothing. The tests check internal consistency (the deduplicated units account for every audit row), invariants derived from the sources (the manifest's own totals, the ledger's no-verdict classes), and each in-memory reduction against values worked out by hand over synthetic units.

The manifest and built groups are post-merge: they describe the corpus after the ink-duplicate merge. The audit, ink, and unmatched_groups groups count pre-merge units, the (codepoints, baseline, new) triples before the merge, which no corpus shard reports, so their class counts can differ from the manifest's. `audit.row_count` counts raw audit rows.

The pre-merge groups come from the review-facts.json sidecar, which `build_m1` writes. It derives them from the pre-merge state it captured just before the merge and the phase-1 products it computes anyway (each post-merge unit's machine flags and change group), instead of shaping and enriching the whole corpus a second time, so the derivation takes milliseconds and is not independent of the build. `derive_premerge` checks the derivation's assumptions: it fails on a unit that takes its survivor's change group but was merged away, on a unit that takes a change group and resolves to none, and on a unit the build re-routes and the review facts do not, or the reverse, and it returns one ink flag per captured unit, which `ink_group_from_flags` counts. The sidecar (`build_facts`) holds the finished pins and the identity of the corpus that owns them; the per-unit ink flags and change groups that the ink and unmatched_groups groups are reduced from stay in the build's memory.

Usage:
    uv run python -m rebuild.review.facts --update [--corpus DIR]  # DIR defaults to rebuild/out/review; every non-staging artifact-cycle pass runs this
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from array import array
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from rebuild.review.audit import (
    BATCH_SIZE,
    NO_VERDICT,
    UNMATCHED_CLASS,
    Compaction,
    LedgerClass,
    UnitTable,
    format_codepoints,
    parse_codepoints,
)
from rebuild.review.change_groups import (
    CHANGE_GROUP_ORDER,
    reroutes,
    set_gate_group,
    unexplained_configs,
)
from rebuild.review.columns import StringTable, TuplePool

if TYPE_CHECKING:
    from rebuild.review.unit_store import UnitStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PINS_PATH = REPO_ROOT / "rebuild" / "review-facts-pins.json"
CORPUS = REPO_ROOT / "rebuild" / "out" / "review"

FACTS_FILENAME = "review-facts.json"
FACTS_FORMAT = "ams-review-facts/8"
FACTS_REMEDY = "rebuild the corpus with: uv run python -m rebuild.review.build"

CLASS_UNIT_COUNT_KEYS = ("split-by-space-or-zwnj", "identical-unused-join-name", "identical-variant-name")

REFERENCE_WINDOW_CODEPOINTS = "E670:E653:E652:E666"
_REFERENCE_WINDOW = parse_codepoints(REFERENCE_WINDOW_CODEPOINTS)


def class_rank(manifest: Mapping) -> Callable[[str], tuple[int, int]]:
    """The sort key the pins list a corpus's classes by: the ledger classes in ledger order, which is the order of the manifest's `ledger_classes` lookup, then the change groups in `CHANGE_GROUP_ORDER`. The manifest itself lists its classes by section first, so a class whose section changes would otherwise move in the pins. The key raises KeyError on a class that is neither."""
    ranks = {identifier: (0, index) for index, identifier in enumerate(manifest["ledger_classes"])}
    ranks.update((identifier, (1, index)) for index, identifier in enumerate(CHANGE_GROUP_ORDER))
    return ranks.__getitem__


def manifest_group(manifest: dict) -> dict:
    """The post-merge facts read straight from the built corpus's manifest.json, with the machine-approved histogram keyed in `class_rank` order."""
    by_id = {meta["id"]: meta for meta in manifest["classes"]}
    by_class = manifest["machine_approved"]["by_class"]
    return {
        "totals": dict(manifest["totals"]),
        "machine_approved": {
            "units": manifest["machine_approved"]["units"],
            "by_class": {key: by_class[key] for key in sorted(by_class, key=class_rank(manifest))},
        },
        "class_unit_count": {key: by_id[key]["unit_count"] for key in CLASS_UNIT_COUNT_KEYS},
        "secondary_junctions": dict(manifest["secondary_junctions"]),
    }


def invariant_group(manifest: dict, unmatched_groups: Mapping[str, int]) -> dict:
    """The invariant block: which classes the corpus ships, which the build machine-approves, and which are exempt from individual verdicts, each in `class_rank` order, and which change groups the corpus reaches, in the order of the pre-merge counts (`CHANGE_GROUP_ORDER`). The classes are listed, not counted, so `invariant_delta` can say which class appeared or went."""
    rank = class_rank(manifest)
    classes = sorted(manifest["classes"], key=lambda meta: rank(meta["id"]))
    return {
        "classes": [meta["id"] for meta in classes],
        "machine_approved_classes": sorted(manifest["machine_approved"]["by_class"], key=rank),
        "no_verdict_classes": [meta["id"] for meta in classes if meta["no_verdict"]],
        "unmatched_groups": list(unmatched_groups),
    }


INVARIANT_LABELS = {
    "classes": "classes",
    "machine_approved_classes": "machine-approved",
    "no_verdict_classes": "no-verdict",
    "unmatched_groups": "change groups",
}


def _named(ids: Sequence[str]) -> str:
    return f"{len(ids)} ({', '.join(ids)})" if ids else "0"


def invariant_delta(accepted: Mapping, current: Mapping) -> list[str]:
    """The changes in the invariant block since the accepted review facts, one finding per list and direction (`classes +2 (a, b)`, `machine-approved -1 (c)`), in the block's key order. The list is empty only when nothing changed. A list with the same members in a new order is reported only as `reordered`, because its order comes from the ledger and `CHANGE_GROUP_ORDER` and says nothing new about the corpus. A key that only one side has is reported as newly or no longer recorded, so a change to the block's shape does not read as a list of new classes."""
    findings: list[str] = []
    keys = list(accepted) + [key for key in current if key not in accepted]
    for key in keys:
        label = INVARIANT_LABELS.get(key, key)
        if key not in current:
            findings.append(f"{label} no longer recorded")
            continue
        if key not in accepted:
            findings.append(f"{label} newly recorded")
            continue
        old, new = accepted[key], current[key]
        if not isinstance(old, list) or not isinstance(new, list):
            if old != new:
                findings.append(f"{label} {old!r} -> {new!r}")
            continue
        gained = [item for item in new if item not in old]
        lost = [item for item in old if item not in new]
        if gained:
            findings.append(f"{label} +{_named(gained)}")
        if lost:
            findings.append(f"{label} -{_named(lost)}")
        if not gained and not lost and old != new:
            findings.append(f"{label} reordered")
    return findings


def invariant_diff(accepted: Mapping, current: Mapping) -> list[str]:
    """The invariant block's unified diff, accepted against current, in the pins file's JSON formatting: the part of `git diff -- rebuild/review-facts-pins.json` a reader needs, without the volatile hunks."""
    return list(
        difflib.unified_diff(
            json.dumps(accepted, indent=2).splitlines(),
            json.dumps(current, indent=2).splitlines(),
            fromfile="invariant (accepted)",
            tofile="invariant (this corpus)",
            lineterm="",
        )
    )


@dataclass(frozen=True)
class LedgerCoverage:
    """The ledger's declarations compared with what the corpus reached, from the ledger the corpus was built over and the invariant block. `unreached` lists the ledger entries the corpus ships no class for: no unit matched them, or every unit that did was re-routed to a change group. `machine_approved` is emergent (a class is in it when the build approved at least one of its units through any machine check) and is compared with the ledger's `ink_identical` declarations both ways: `ink_declared_unapproved` lists declared classes with no machine-approved unit, and `machine_approved_undeclared` lists approving classes the ledger never declared. `no_verdict` is declared in the ledger and copied onto each class the corpus ships, so its only possible disagreement is a declared class the corpus never reached. `describe` puts all of this on one line."""

    ledger: tuple[str, ...]
    unreached: tuple[str, ...]
    machine_approved: tuple[str, ...]
    ink_declared: tuple[str, ...]
    ink_declared_unapproved: tuple[str, ...]
    machine_approved_undeclared: tuple[str, ...]
    no_verdict_declared: tuple[str, ...]
    no_verdict_reached: tuple[str, ...]
    no_verdict_unreached: tuple[str, ...]

    def describe(self) -> str:
        """The one summary line. Approving classes without a declaration are counted, not named, because approval through the picture or Junior check, or of a change group's units, is the usual case and `invariant_delta` already names a class that joins them; `as_json` has the names. Each declaration the corpus does not show is named."""
        machine = (
            f"machine-approved: {len(self.machine_approved)} classes approve units,"
            f" {len(self.machine_approved_undeclared)} undeclared;"
            f" ink-identical: {len(self.ink_declared)} declared, "
            + (
                f"{_named(self.ink_declared_unapproved)} approving none"
                if self.ink_declared_unapproved
                else "all approving"
            )
        )
        no_verdict = (
            f"no-verdict: {len(self.no_verdict_reached)} of {len(self.no_verdict_declared)} declared reached"
            + (f", unreached {_named(self.no_verdict_unreached)}" if self.no_verdict_unreached else "")
        )
        ledger = f"ledger: {len(self.ledger) - len(self.unreached)} of {len(self.ledger)} classes reached" + (
            f", unreached {_named(self.unreached)}" if self.unreached else ""
        )
        return "; ".join((machine, no_verdict, ledger))

    def as_json(self) -> dict:
        return {
            "ledger": list(self.ledger),
            "unreached": list(self.unreached),
            "machine_approved": list(self.machine_approved),
            "ink_declared": list(self.ink_declared),
            "ink_declared_unapproved": list(self.ink_declared_unapproved),
            "machine_approved_undeclared": list(self.machine_approved_undeclared),
            "no_verdict_declared": list(self.no_verdict_declared),
            "no_verdict_reached": list(self.no_verdict_reached),
            "no_verdict_unreached": list(self.no_verdict_unreached),
        }


def ledger_coverage(ledger: Sequence[LedgerClass], invariant: Mapping) -> LedgerCoverage:
    """`LedgerCoverage` over a loaded ledger and an invariant block, every list in its source's order: ledger lists in ledger order, reached lists in the block's."""
    reached = set(invariant["classes"])
    machine = tuple(invariant["machine_approved_classes"])
    ink_declared = tuple(entry.id for entry in ledger if entry.ink_identical)
    no_verdict_declared = tuple(entry.id for entry in ledger if entry.no_verdict)
    return LedgerCoverage(
        ledger=tuple(entry.id for entry in ledger),
        unreached=tuple(entry.id for entry in ledger if entry.id not in reached),
        machine_approved=machine,
        ink_declared=ink_declared,
        ink_declared_unapproved=tuple(identifier for identifier in ink_declared if identifier not in machine),
        machine_approved_undeclared=tuple(
            identifier for identifier in machine if identifier not in ink_declared
        ),
        no_verdict_declared=no_verdict_declared,
        no_verdict_reached=tuple(invariant["no_verdict_classes"]),
        no_verdict_unreached=tuple(
            identifier
            for identifier in no_verdict_declared
            if identifier not in invariant["no_verdict_classes"]
        ),
    )


def _encode_note_distribution(distribution: dict[str | None, int]) -> list[list]:
    """JSON forbids a null object key, so the config-note histogram is stored as a list of [note, count] pairs, null first then lexicographic."""
    return [
        [note, distribution[note]]
        for note in sorted(distribution, key=lambda note: (note is not None, note or ""))
    ]


def _settled_class(config_classes: Iterable[str]) -> str:
    """The class a unit whose configs match `config_classes` is listed under before any change group is assigned, by the rule `audit.load_table` and the ink-duplicate merge share: UNMATCHED when any config is unmatched, otherwise the one ledger class every config matched."""
    classes = set(config_classes)
    if UNMATCHED_CLASS in classes:
        return UNMATCHED_CLASS
    if len(classes) != 1:
        raise ValueError(f"a unit matches {len(classes)} ledger classes, not one: {sorted(classes)}")
    return classes.pop()


def premerge_set_group(
    config_classes: Mapping[str, str],
    configs: tuple[str, ...],
    ink_deltas: Mapping[str, str],
    identical_classes: Collection[str],
) -> str | None:
    """The set gate's group (`change_groups.set_gate_group`) for one pre-merge unit that takes a change group, over the configs no ruling explains (`change_groups.unexplained_configs`) by the unit's own configs, config classes and per-config ink deltas, as phase 1 decides it for a unit. None when the default config is among them; the unit then takes its merge survivor's group, which is its own, because the unit holding a window's default config always leads that window's merge. `derive_premerge` decides it here."""
    return set_gate_group(
        unexplained_configs(_Configs(config_classes, configs), ink_deltas, identical_classes)
    )


def change_group_counts(assignments: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for group in assignments:
        counts[group] = counts.get(group, 0) + 1
    order = {group: index for index, group in enumerate(CHANGE_GROUP_ORDER)}
    return dict(sorted(counts.items(), key=lambda item: (order.get(item[0], len(order)), item[0])))


def rerouted_counts(identical_classes: Sequence[str], rerouted: Iterable[str]) -> dict[str, int]:
    """How many pre-merge units each `ink_identical` ledger class re-routed to a change group, keyed by every class in `identical_classes` in that order, zero where none moved. `rerouted` names each re-routed unit's class; a class outside `identical_classes` raises, since only an identical class re-routes its units."""
    counts = dict.fromkeys(identical_classes, 0)
    for class_id in rerouted:
        if class_id not in counts:
            raise ValueError(f"a unit is re-routed out of {class_id}, which is not an ink_identical class")
        counts[class_id] += 1
    return counts


# --- the review-facts sidecar ---------------------------------------------------------


class Grain(NamedTuple):
    """One pre-merge unit's window, class, no-verdict flag, and configs, as `PremergeSnapshot.grains` yields them in capture order."""

    codepoints: str
    class_id: str
    no_verdict: bool
    configs: tuple[str, ...]


class _Configs(NamedTuple):
    """A pre-merge row's config fields, in the shape `change_groups.unexplained_configs` reads."""

    config_classes: Mapping[str, str]
    configs: tuple[str, ...]


class PremergeSnapshot:
    """The workload as the build saw it before the ink-duplicate merge, as columns copied from the table with one entry per pre-merge row: the class id and config-set id (into the table's string table and tuple pool), the no-verdict byte, and the window's offsets into the table's value column (which compaction does not move). The configs must be copied here, because a row's set group (`premerge_set_group`) is decided over its own configs and the merge widens the survivor's: an ss03-only survivor that absorbs an ss04-only sibling is only-with-ss03 before the merge and would read as only-with-several-sets after it. `rebase` takes the merge's compaction and records, for each pre-merge row, its survivor's post-merge row and whether the row was merged away; `derive_premerge` then reads the survivor's phase-1 products from the store and the table by index. The columns take 14 bytes a row, 19 after `rebase`; a list of per-row records was the largest collection the build held after the merge."""

    __slots__ = (
        "n",
        "strings",
        "tuples",
        "identical_classes",
        "class_id",
        "no_verdict",
        "configs",
        "window_start",
        "window_n",
        "window_values",
        "survivor",
        "removed",
    )

    def __init__(self, table: UnitTable, identical_classes: Collection[str] = frozenset()) -> None:
        self.n = table.n
        self.strings: StringTable = table.strings
        self.tuples: TuplePool[str] = table.tuples
        self.identical_classes = frozenset(identical_classes)
        self.class_id = table.class_ids()
        flags = table.flags()
        self.no_verdict = bytearray(1 if flag & NO_VERDICT else 0 for flag in flags)
        self.configs = table.configs_ids()
        self.window_start, self.window_n, self.window_values = table.windows()
        self.survivor: array | None = None
        self.removed: bytearray | None = None

    def __len__(self) -> int:
        return self.n

    def rebase(self, compaction: Compaction) -> None:
        """Record where each pre-merge row's survivor sits once the table is compacted, and which rows were merged away."""
        if len(compaction.survivor) != self.n:
            raise ValueError(f"a compaction over {len(compaction.survivor)} rows, against {self.n} captured")
        self.survivor = compaction.survivor
        self.removed = compaction.removed

    def codepoints(self, row: int) -> tuple[int, ...]:
        start = self.window_start[row]
        return tuple(self.window_values[start : start + self.window_n[row]])

    def grains(self) -> Iterator[Grain]:
        strings = self.strings
        tuples = self.tuples
        for row in range(self.n):
            yield Grain(
                format_codepoints(self.codepoints(row)),
                strings[self.class_id[row]],
                bool(self.no_verdict[row]),
                tuples[self.configs[row]],
            )

    def class_rows(self) -> Iterator[tuple[str, bool]]:
        strings = self.strings
        for row in range(self.n):
            yield strings[self.class_id[row]], bool(self.no_verdict[row])

    def columns(self) -> Iterator[array | bytearray]:
        yield self.class_id
        yield self.no_verdict
        yield self.configs
        yield self.window_start
        yield self.window_n
        if self.survivor is not None and self.removed is not None:
            yield self.survivor
            yield self.removed


@dataclass(frozen=True)
class PremergeFacts:
    """The pre-merge facts `derive_premerge` returns: how many units the workload held, one '0'/'1' ink flag per unit in capture order, and the change group of every unit that takes one (each UNMATCHED unit and each re-routed unit) with its index in that order. A re-routed unit is one whose captured class is not UNMATCHED. `build_facts` reduces the ink flags and the change groups into the pins; the pins' unit count is the snapshot's."""

    units: int
    ink_flags: str
    unmatched_groups: list[tuple[int, str]]


def capture_premerge(table: UnitTable, identical_classes: Collection[str] = frozenset()) -> PremergeSnapshot:
    """Snapshot the workload for the sidecar. Call it immediately before `merge_ink_duplicate_units`, because the merge widens the configs each row's set group is decided over. `identical_classes` are the ledger's `ink_identical` ids, the classes whose units the build re-routes when their picture changes; with none, `derive_premerge` re-routes no unit and refuses a build that re-routed one."""
    return PremergeSnapshot(table, identical_classes)


def derive_premerge(snapshot: PremergeSnapshot, table: UnitTable, store: UnitStore) -> PremergeFacts:
    """Project the build's post-merge phase-1 products back onto the pre-merge units the review-facts pins count. A unit that survived the merge reads its own row. A unit that was merged away reads the survivor the merge recorded for it (`PremergeSnapshot.rebase`): the ink flag and the other machine flags from the store, and the change group the build promoted onto the table.

    Each UNMATCHED unit takes a change group, and so does each re-routed unit: one whose class `change_groups.reroutes` moves (an `ink_identical` class the capture was given, no exemption, and no machine check approving its survivor) and whose survivor is still in that class. A unit merged into an UNMATCHED survivor is not re-routed. The decision is cross-checked against the build's: a unit whose survivor settled in its own class is re-routed exactly when the build listed that survivor under a change group, and a disagreement raises.

    The projections are exact. The merge groups units only when every config of every sibling gives one identical `InkComparator.signature`, which is the pair of run-order ink lists `config_diff` reads, so a merged sibling's delta, ink verdict and picture verdict equal its survivor's. A unit's set group (`premerge_set_group`) is decided over its own captured configs, with the survivor's config classes and ink deltas for those configs: a window's units cover disjoint configs, so the survivor's merged config classes give each of them as the unit had it, and its deltas are the unit's own by the signature rule. A unit whose set group is None holds its window's default config, so it leads its window's merge order, is its own survivor, and finds its phase-1 change group on its own row. The assert below checks that argument where it could fail: a unit with no set group that was merged away.
    """
    if snapshot.survivor is None or snapshot.removed is None:
        raise ValueError(
            "the pre-merge snapshot is derived once the merge's compaction has been rebased onto it"
        )
    strings = snapshot.strings
    unmatched = strings.find(UNMATCHED_CLASS)
    identical_classes = snapshot.identical_classes
    identical = {index for index in map(strings.find, identical_classes) if index is not None}
    survivor = snapshot.survivor
    removed = snapshot.removed
    class_ids = snapshot.class_id
    no_verdict = snapshot.no_verdict
    configs = snapshot.configs
    tuples = snapshot.tuples
    ink_identical = store.ink_identical
    machine_approved = store.machine_approved
    flags = bytearray(snapshot.n)
    assigned: list[tuple[int, str]] = []
    for row in range(snapshot.n):
        target = survivor[row]
        flags[row] = 49 if ink_identical(target) else 48
        survivor_group = table.unmatched_group(target)
        class_index = class_ids[row]
        if class_index != unmatched:
            class_id = strings[class_index]
            rerouted = class_index in identical and reroutes(
                class_id, bool(no_verdict[row]), machine_approved(target), identical_classes
            )
            if not rerouted and not survivor_group:
                continue
            if _settled_class(table.config_classes(target).values()) != class_id:
                continue
            if rerouted and not survivor_group:
                raise ValueError(
                    f"window {format_codepoints(snapshot.codepoints(row))}: the review facts re-route this "
                    f"{class_id} unit, but the build lists its survivor under no change group"
                )
            if not rerouted:
                raise ValueError(
                    f"window {format_codepoints(snapshot.codepoints(row))}: the build lists this {class_id} unit "
                    f"under change group {survivor_group}, but the review facts do not re-route it"
                )
        row_configs = tuples[configs[row]]
        survivor_classes = table.config_classes(target)
        survivor_deltas = store.ink_deltas(target)
        set_group = premerge_set_group(
            {config: survivor_classes[config] for config in row_configs},
            row_configs,
            {config: survivor_deltas[config] for config in row_configs if config in survivor_deltas},
            identical_classes,
        )
        assert set_group or not removed[row], (
            f"window {format_codepoints(snapshot.codepoints(row))}: a unit with the default config was merged "
            "away, so its change group cannot be read off its own phase-1 row"
        )
        group = set_group or survivor_group
        if not group:
            raise ValueError(
                f"window {format_codepoints(snapshot.codepoints(row))}: a unit that takes a change group resolved to none"
            )
        assigned.append((row, group))
    return PremergeFacts(
        units=snapshot.n,
        ink_flags=flags.decode("ascii"),
        unmatched_groups=assigned,
    )


def ink_group_from_flags(class_rows: Iterable[tuple[str, bool]], flags: str) -> dict:
    """The ink group computed from one '0'/'1' flag per pre-merge unit and that unit's (class, no-verdict) pair, both in capture order: the machine-approved units per class, keyed in the order of each class's first flagged unit; the unflagged no-verdict units as `boundary_window_exempt`; and the other unflagged units as human units, cut into `BATCH_SIZE` batches. Junior equivalence and picture identity do not count, because the pre-merge facts count the ink verdict alone."""
    machine_by_class: dict[str, int] = {}
    exempt = 0
    human = 0
    total = 0
    for (class_id, no_verdict), flag in zip(class_rows, flags, strict=True):
        total += 1
        if flag == "1":
            machine_by_class[class_id] = machine_by_class.get(class_id, 0) + 1
        elif no_verdict:
            exempt += 1
        else:
            human += 1
    machine_total = sum(machine_by_class.values())
    return {
        "machine_total": machine_total,
        "non_identical": total - machine_total,
        "by_class": machine_by_class,
        "boundary_window_exempt": exempt,
        "human_units": human,
        "batches": (human + BATCH_SIZE - 1) // BATCH_SIZE,
    }


def unmatched_groups_group_from(assignments: Iterable[str], rerouted: Mapping[str, int]) -> dict:
    """The unmatched_groups group over change groups already assigned, so a build can report the groups phase 1 computed without enriching every window again: the units per change group in `CHANGE_GROUP_ORDER`, their total, and `rerouted`, the units each `ink_identical` class re-routed (`rerouted_counts`)."""
    counts = change_group_counts(assignments)
    return {"counts": counts, "total": sum(counts.values()), "rerouted": dict(rerouted)}


def built_group_from_memory(table: UnitTable, config_notes: Mapping[int, str | None]) -> dict:
    """The post-merge facts over every unit, read from the build's in-memory table: the human-unit count, the config-note histogram, and the reference window's duplicate-sibling count (the distinct windows one ·It·Day·Tea·No verdict covers). The duplicate-sibling count is None when the reference window is not a human unit. Every build writes the sidecar, including the unit-cache tests' small corpora, so only the live corpus is required to contain the reference window, and the pins diff shows a missing one as an accepted count replaced by None. The units are the table's rows; `config_notes` maps each unit's ordinal to its `config_note`, the one fragment field this group reads."""
    human_units = 0
    distribution: dict[str | None, int] = {}
    reference_group: str | None = None
    windows_by_group: dict[str, set[tuple[int, ...]]] = {}
    for ordinal in range(table.n):
        if table.batch(ordinal) is not None:
            duplicate_group = table.duplicate_group(ordinal)
            assert duplicate_group is not None
            human_units += 1
            window = table.codepoints(ordinal)
            windows_by_group.setdefault(duplicate_group, set()).add(window)
            if window == _REFERENCE_WINDOW:
                reference_group = duplicate_group
        note = config_notes[ordinal]
        distribution[note] = distribution.get(note, 0) + 1
    return {
        "human_units": human_units,
        "reference_window_duplicate_siblings": (
            len(windows_by_group[reference_group]) if reference_group is not None else None
        ),
        "config_note_distribution": _encode_note_distribution(distribution),
    }


def build_facts(
    manifest: dict,
    table: UnitTable,
    config_notes: Mapping[int, str | None],
    snapshot: PremergeSnapshot,
    premerge: PremergeFacts,
    row_count: int,
) -> dict:
    """The sidecar payload: the finished pins the regenerator copies into the checked-in file, and the identity of the corpus that owns them. The ink and unmatched_groups groups are reduced from `premerge`: a change-group record whose unit's class is not UNMATCHED is a re-routed unit, counted under that class, with the snapshot's identical classes in ledger order (`class_rank`)."""
    unmatched = snapshot.strings.find(UNMATCHED_CLASS)
    unmatched_groups = unmatched_groups_group_from(
        (group for _index, group in premerge.unmatched_groups),
        rerouted_counts(
            sorted(snapshot.identical_classes, key=class_rank(manifest)),
            (
                snapshot.strings[snapshot.class_id[index]]
                for index, _group in premerge.unmatched_groups
                if snapshot.class_id[index] != unmatched
            ),
        ),
    )
    return {
        "format": FACTS_FORMAT,
        "corpus": {
            "generated_at": manifest["generated_at"],
            "repo_head": manifest["repo_head"],
            "inputs_fingerprint": manifest["inputs_fingerprint"],
        },
        "pins": {
            "invariant": invariant_group(manifest, unmatched_groups["counts"]),
            "volatile": {
                "manifest": manifest_group(manifest),
                "built": built_group_from_memory(table, config_notes),
                "audit": {"row_count": row_count, "units": snapshot.n},
                "ink": ink_group_from_flags(snapshot.class_rows(), premerge.ink_flags),
                "unmatched_groups": unmatched_groups,
            },
        },
    }


def write_facts(out_dir: Path, facts: dict) -> None:
    """Write the sidecar beside the manifest as compact JSON."""
    path = Path(out_dir) / FACTS_FILENAME
    path.write_text(json.dumps(facts, separators=(",", ":"), ensure_ascii=True) + "\n", encoding="utf-8")


def load_facts(out_dir: Path, manifest: dict) -> dict:
    """The sidecar of a built corpus. Raises ValueError when the file is missing or in another format (the corpus predates the sidecar), or when its generated_at differs from the manifest's (the two came from different builds)."""
    path = Path(out_dir) / FACTS_FILENAME
    try:
        facts = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise ValueError(f"{path} is missing — {FACTS_REMEDY}") from None
    if facts.get("format") != FACTS_FORMAT:
        raise ValueError(f"{path} is not {FACTS_FORMAT} — {FACTS_REMEDY}")
    if facts["corpus"]["generated_at"] != manifest["generated_at"]:
        raise ValueError(
            f"{path} was written for corpus {facts['corpus']['generated_at']},"
            f" not {manifest['generated_at']} — {FACTS_REMEDY}"
        )
    return facts


def compute_pins(corpus: Path) -> dict:
    """Both blocks of the pins for the built corpus at `corpus`, which is only read: the volatile block from its review-facts.json sidecar, and the invariant block reduced again from its manifest and the sidecar's change-group counts, the same sources the build used, so the checked-in file has the block's current shape whichever build wrote the sidecar."""
    manifest = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    volatile = load_facts(corpus, manifest)["pins"]["volatile"]
    return {
        "invariant": invariant_group(manifest, volatile["unmatched_groups"]["counts"]),
        "volatile": volatile,
    }


def _dumps(pins: dict) -> str:
    return json.dumps(pins, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        required=True,
        help="rewrite the pins file from the corpus's review-facts.json sidecar",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS,
        help="the built corpus to read, read-only (default: rebuild/out/review)",
    )
    args = parser.parse_args(argv)

    PINS_PATH.write_text(_dumps(compute_pins(args.corpus)), encoding="utf-8")
    print(f"Wrote {PINS_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
