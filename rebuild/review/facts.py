"""The review facts: the counts and structural facts a corpus build reduces its state to, and the regenerator that writes them to rebuild/review-facts-pins.json, the last accepted review facts. Every non-staging artifact-cycle pass rewrites that file from the corpus's review-facts.json sidecar and names what moved in its invariant block. Committing the rewritten file accepts the new review facts. No gate checks the checked-in numbers, so a changed count is something to read, not a failure.

The file has two blocks so the cycle can say what kind of change a pass made. `volatile` holds the manifest, built, audit, ink, and unmatched_groups groups, which change with every migrated letter. `invariant` holds the structural facts a person should review when they change: which classes the corpus ships, which classes the build machine-approves, which are exempt from individual verdicts, and which unmatched groups the corpus reaches. The invariant block repeats structure that the volatile groups' keys already carry. Both blocks come from one emission, so they cannot disagree, and the separate block lets `invariant_delta` name a new class or a new no-verdict exemption in one summary line and lets the cycle print a diff of that block alone. `rebuild/out/cycle_summary.json` also records the corpus's totals. The machine-approved and unmatched-group lists are recorded nowhere else, because both are emergent: a class is machine-approved when the build approved at least one of its units through any machine check, whatever the ledger declares. `ledger_coverage` compares the ledger's declarations with them.

No test reads this file, since a build asserting the numbers it just wrote would check nothing. The tests check internal consistency (the deduplicated units account for every audit row), invariants derived from the sources (the manifest's own totals, the ledger's no-verdict classes), and that each in-memory reduction matches the shard walk or shaping it replaces.

The manifest and built groups are post-merge: they are read from a built corpus after the ink-duplicate merge. The audit, ink, and unmatched_groups groups count pre-merge units, the (codepoints, baseline, new) triples before the merge, which no corpus shard reports, so their class counts can differ from the manifest's. `audit.row_count` counts raw audit rows.

The pre-merge groups come from the review-facts.json sidecar, which `build_m1` writes. It derives them from the pre-merge state it captured just before the merge and the phase-1 products it computes anyway (each post-merge unit's ink verdict and each UNMATCHED unit's unmatched group), instead of shaping and enriching the whole corpus a second time. The review facts are then less independent of the build, but it takes milliseconds instead of minutes. `--from-scratch` recomputes the groups from the source inputs (TSV, ledger, fonts, spec) when an independent comparison is wanted. `derive_premerge` checks the derivation's assumptions: it fails on a default-novel UNMATCHED unit that was merged away and on an UNMATCHED unit with no unmatched group, and it writes one ink flag per captured unit.

Usage:
    uv run python -m rebuild.review.facts --update --corpus rebuild/out/review  # what every non-staging artifact-cycle pass runs
    uv run python -m rebuild.review.facts --check --corpus rebuild/out/review   # the manual comparison
    uv run python -m rebuild.review.facts            # --check, building a fresh temporary corpus first
    uv run python -m rebuild.review.facts --check --from-scratch
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import json
import sys
import tempfile
import warnings
from array import array
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol

from rebuild.review import unit_index
from rebuild.review.audit import (
    BATCH_SIZE,
    NO_VERDICT,
    UNMATCHED_CLASS,
    Compaction,
    LedgerClass,
    Unit,
    UnitTable,
    Workload,
    _config_index,
    format_codepoints,
    group_for,
    load_audit,
    load_workload,
    parse_codepoints,
    render_groups_for_rows,
    slim_fragment,
)
from rebuild.review.columns import StringTable, TuplePool
from rebuild.review.enrich import LETTERS, Enricher, load_spec
from rebuild.review.unmatched_groups import (
    UNMATCHED_GROUP_ORDER,
    assign_unmatched_group,
    deferred_unmatched_group,
)
from rebuild.review.ink import InkComparator

if TYPE_CHECKING:
    from rebuild.review.unit_store import UnitStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PINS_PATH = REPO_ROOT / "rebuild" / "review-facts-pins.json"

FACTS_FILENAME = "review-facts.json"
FACTS_FORMAT = "ams-review-facts/7"
FACTS_REMEDY = "rebuild the corpus with: uv run python -m rebuild.review.build"

AUDIT_PATH = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
SUBSET_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"
BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

CLASS_UNIT_COUNT_KEYS = ("boundary-window", "dangling-anchor-dropped", "bare-name-live-join")

REFERENCE_WINDOW_CODEPOINTS = "E670:E653:E652:E666"
_REFERENCE_WINDOW = parse_codepoints(REFERENCE_WINDOW_CODEPOINTS)


def _text(unit) -> str:
    return "".join(chr(value) for value in unit.codepoint_values)


def manifest_group(manifest: dict) -> dict:
    """The post-merge facts read straight from the built corpus's manifest.json."""
    by_id = {meta["id"]: meta for meta in manifest["classes"]}
    return {
        "totals": dict(manifest["totals"]),
        "machine_approved": {
            "units": manifest["machine_approved"]["units"],
            "by_class": dict(manifest["machine_approved"]["by_class"]),
        },
        "class_unit_count": {key: by_id[key]["unit_count"] for key in CLASS_UNIT_COUNT_KEYS},
        "secondary_junctions": dict(manifest["secondary_junctions"]),
    }


def invariant_group(manifest: dict, unmatched_groups: dict[str, int]) -> dict:
    """The invariant block: which classes the corpus ships, which the build machine-approves, which are exempt from individual verdicts, and which unmatched groups the corpus reaches, each in its source's order. The classes are listed, not counted, so `invariant_delta` can say which class appeared or went."""
    return {
        "classes": [meta["id"] for meta in manifest["classes"]],
        "machine_approved_classes": list(manifest["machine_approved"]["by_class"]),
        "no_verdict_classes": [meta["id"] for meta in manifest["classes"] if meta["no_verdict"]],
        "unmatched_groups": list(unmatched_groups),
    }


INVARIANT_LABELS = {
    "classes": "classes",
    "machine_approved_classes": "machine-approved",
    "no_verdict_classes": "no-verdict",
    "unmatched_groups": "unmatched groups",
}


def _named(ids: Sequence[str]) -> str:
    return f"{len(ids)} ({', '.join(ids)})" if ids else "0"


def invariant_delta(accepted: Mapping, current: Mapping) -> list[str]:
    """The changes in the invariant block since the accepted review facts, one finding per list and direction (`classes +2 (a, b)`, `machine-approved -1 (c)`), in the block's key order. The list is empty only when nothing changed. A list with the same members in a new order is reported only as `reordered`, because its order comes from the ledger or the manifest and says nothing new about the corpus. A key that only one side has is reported as newly or no longer recorded, so a change to the block's shape does not read as a list of new classes."""
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
    """The ledger's declarations compared with what the corpus reached, from the ledger the corpus was built over and the invariant block. `unreached` lists the ledger entries no unit matched, so the corpus ships no class for them. `machine_approved` is emergent (a class is in it when the build approved at least one of its units through any machine check) and is compared with the ledger's `ink_identical` declarations both ways: `ink_declared_unapproved` lists declared classes with no machine-approved unit, and `machine_approved_undeclared` lists approving classes the ledger never declared. `no_verdict` is declared in the ledger and copied onto each class the corpus ships, so its only possible disagreement is a declared class the corpus never reached. `describe` puts all of this on one line."""

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
        """The one summary line. Approving classes without a declaration are counted, not named, because approval through the picture or Junior check, or as a deferred unmatched group, is the usual case and `invariant_delta` already names a class that joins them; `as_json` has the names. Each declaration the corpus does not show is named."""
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


def _shard_units(out_dir: Path, meta: dict) -> Iterable[dict]:
    """One class's units, a shard part at a time so a large class is never resident whole."""
    for part in unit_index.class_shards(meta):
        yield from json.loads((out_dir / part).read_text(encoding="utf-8"))


def built_group(out_dir: Path, manifest: dict) -> dict:
    """The post-merge facts that need a walk of the corpus's unit shards: the human-unit count, the config-note histogram, and the reference window's duplicate-sibling count (the distinct windows one ·It·Day·Tea·No verdict covers). The duplicate-sibling count is None when the reference window is not a human unit. Every build writes the sidecar, including the unit-cache tests' small corpora, so only the live corpus is required to contain the reference window, and the pins diff shows a missing one as an accepted count replaced by None."""
    out_dir = Path(out_dir)
    human_units = 0
    distribution: dict[str | None, int] = {}
    reference_group: str | None = None
    codepoints_by_group: dict[str, set[str]] = {}
    for meta in manifest["classes"]:
        for unit in _shard_units(out_dir, meta):
            if not slim_fragment(unit):
                human_units += 1
                codepoints_by_group.setdefault(unit["duplicate_group"], set()).add(unit["codepoints"])
                if unit["codepoints"] == REFERENCE_WINDOW_CODEPOINTS:
                    reference_group = unit["duplicate_group"]
            note = unit["config_note"]
            distribution[note] = distribution.get(note, 0) + 1
    return {
        "human_units": human_units,
        "reference_window_duplicate_siblings": (
            len(codepoints_by_group[reference_group]) if reference_group is not None else None
        ),
        "config_note_distribution": _encode_note_distribution(distribution),
    }


def _encode_note_distribution(distribution: dict[str | None, int]) -> list[list]:
    """JSON forbids a null object key, so the config-note histogram is stored as a list of [note, count] pairs, null first then lexicographic."""
    return [
        [note, distribution[note]]
        for note in sorted(distribution, key=lambda note: (note is not None, note or ""))
    ]


def audit_group(repo_root: Path = REPO_ROOT) -> dict:
    """The pre-merge name-grain audit facts: the raw row count and the deduped unit count, cheap (no shaping)."""
    workload = load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))
    return {"row_count": workload.row_count, "units": workload.table.n}


def ink_histogram(workload: Workload, comparator) -> dict:
    """The ink group over the pre-merge workload, computed by shaping: flag every unit whose placed ink is identical in both fonts under every config in its set, count the machine-approved units per class, and count the boundary-window no-verdict exemptions, the human units, and their batches. It counts as `ink_group_from_flags` does: the ink verdict alone decides, and the batches are the human units cut into `BATCH_SIZE` slices. `workload.units()` materializes new records, and the flags are set on those."""
    units = workload.units()
    machine_by_class: dict[str, int] = {}
    for unit in units:
        if comparator.ink_identical(_text(unit), unit.configs):
            unit.ink_identical = True
            machine_by_class[unit.class_id] = machine_by_class.get(unit.class_id, 0) + 1
    machine_total = sum(machine_by_class.values())
    exempt = sum(1 for unit in units if unit.no_verdict and not unit.ink_identical)
    human = sum(1 for unit in units if not unit.ink_identical and not unit.no_verdict)
    return {
        "machine_total": machine_total,
        "non_identical": len(units) - machine_total,
        "by_class": machine_by_class,
        "boundary_window_exempt": exempt,
        "human_units": human,
        "batches": (human + BATCH_SIZE - 1) // BATCH_SIZE,
    }


def ink_group(repo_root: Path = REPO_ROOT) -> dict:
    workload = load_workload(AUDIT_PATH, LEDGER_PATH, dict(LETTERS))
    comparator = InkComparator(BEFORE_FONT, AFTER_FONT)
    return ink_histogram(workload, comparator)


def unmatched_group_assignments(repo_root: Path = REPO_ROOT) -> list[str]:
    """Assign every UNMATCHED pre-merge unit to its unmatched group: load the audit, group rows by (codepoints, baseline, new) triple, enrich each triple that is UNMATCHED under any config, and classify it with `assign_unmatched_group`. Returns one group label per such triple, in audit order."""
    by_triple: dict[tuple, list] = {}
    for row in load_audit(AUDIT_PATH):
        by_triple.setdefault((row.codepoints, row.baseline, row.new), []).append(row)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(repo_root)
    enricher = Enricher(spec, SUBSET_DIR, AFTER_FONT, repo_root=repo_root, before_font=BEFORE_FONT)
    units: list[Unit] = []
    for (codepoints, baseline, new), members in by_triple.items():
        if not any(member.matched_entry == "UNMATCHED" for member in members):
            continue
        config_classes = {member.config: member.matched_entry for member in members}
        ordered = tuple(sorted(members, key=lambda member: _config_index(member.config)))
        unit = Unit(
            codepoints=codepoints,
            baseline=baseline,
            new=new,
            class_id="UNMATCHED",
            row_count=len(ordered),
            configs=tuple(member.config for member in ordered),
            kinds=tuple(sorted({kind for member in members for kind in member.kinds})),
            group=group_for(parse_codepoints(codepoints), dict(LETTERS)),
            render_groups=render_groups_for_rows(
                (member.baseline, member.new, member.config) for member in ordered
            ),
            config_classes=config_classes,
        )
        units.append(unit)
    return [assign_unmatched_group(enriched) for enriched in enricher.enrich_many(units)]


def unmatched_group_counts(assignments: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for group in assignments:
        counts[group] = counts.get(group, 0) + 1
    order = {group: index for index, group in enumerate(UNMATCHED_GROUP_ORDER)}
    return dict(sorted(counts.items(), key=lambda item: (order.get(item[0], len(order)), item[0])))


def unmatched_groups_group(repo_root: Path = REPO_ROOT) -> dict:
    counts = unmatched_group_counts(unmatched_group_assignments(repo_root))
    return {"counts": counts, "total": sum(counts.values())}


# --- the review-facts sidecar ---------------------------------------------------------


class FactsGrain(Protocol):
    """The four fields the pre-merge facts are defined over. A materialized `Unit` and a `PremergeSnapshot` row (`PremergeSnapshot.grains`) both satisfy it, so `workload_digest` can be taken from either."""

    @property
    def codepoints(self) -> str: ...

    @property
    def class_id(self) -> str: ...

    @property
    def no_verdict(self) -> bool: ...

    @property
    def configs(self) -> tuple[str, ...]: ...


class Grain(NamedTuple):
    """One pre-merge unit's `FactsGrain` fields. The snapshot yields one at a time for `workload_digest`."""

    codepoints: str
    class_id: str
    no_verdict: bool
    configs: tuple[str, ...]


class _Configs(NamedTuple):
    """A pre-merge row's config fields, in the shape `unmatched_groups.deferred_unmatched_group` reads."""

    config_classes: Mapping[str, str]
    configs: tuple[str, ...]


class PremergeSnapshot:
    """The workload as the build saw it before the ink-duplicate merge, as columns copied from the table with one entry per pre-merge row: the class id and config-set id (into the table's string table and tuple pool), the no-verdict byte, the window's offsets into the table's value column (which compaction does not move), and the deferred stylistic-set bucket. The bucket must be decided here, because deferral depends only on the pre-merge config classes and the merge changes them: an ss03-only survivor that absorbs an ss04-only sibling is deferred-ss03 before the merge and would read as deferred-ss04 after it. `rebase` takes the merge's compaction and records, for each pre-merge row, its survivor's post-merge row and whether the row was merged away; `derive_premerge` then reads the survivor's phase-1 products from the store and the table by index. The columns take 18 bytes a row, 23 after `rebase`; a list of per-row records was the largest collection the build held after the merge."""

    __slots__ = (
        "n",
        "strings",
        "tuples",
        "class_id",
        "no_verdict",
        "configs",
        "deferred",
        "window_start",
        "window_n",
        "window_values",
        "survivor",
        "removed",
    )

    def __init__(self, table: UnitTable) -> None:
        self.n = table.n
        self.strings: StringTable = table.strings
        self.tuples: TuplePool[str] = table.tuples
        self.class_id = table.class_ids()
        flags = table.flags()
        self.no_verdict = bytearray(1 if flag & NO_VERDICT else 0 for flag in flags)
        self.configs = table.configs_ids()
        self.window_start, self.window_n, self.window_values = table.windows()
        self.deferred = array("I", [0]) * table.n
        self.survivor: array | None = None
        self.removed: bytearray | None = None
        unmatched = self.strings.find(UNMATCHED_CLASS)
        if unmatched is None:
            return
        mappings = table.config_classes_ids()
        buckets: dict[tuple[int, int], int] = {}
        for row in range(table.n):
            if self.class_id[row] != unmatched:
                continue
            key = (mappings[row], self.configs[row])
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = self.strings.optional(
                    deferred_unmatched_group(_Configs(table.mappings[key[0]], self.tuples[key[1]]))
                )
            self.deferred[row] = bucket

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
        yield self.deferred
        yield self.window_start
        yield self.window_n
        if self.survivor is not None and self.removed is not None:
            yield self.survivor
            yield self.removed


@dataclass(frozen=True)
class PremergeFacts:
    """The pre-merge facts a build reports: how many units the workload held, the digest identifying them, one '0'/'1' ink flag per unit in capture order, and the unmatched group of every UNMATCHED unit with its index in that order."""

    units: int
    workload_digest: str
    ink_flags: str
    unmatched_groups: list[tuple[int, str]]


def capture_premerge(table: UnitTable) -> PremergeSnapshot:
    """Snapshot the workload for the sidecar. Call it immediately before `merge_ink_duplicate_units`, because each UNMATCHED row's deferral is decided here, from config classes the merge is about to widen. Matched rows get no deferral, since `deferred_unmatched_group` has no meaningful answer outside UNMATCHED."""
    return PremergeSnapshot(table)


def workload_digest(units: Iterable[FactsGrain]) -> str:
    """A sha256 over the `FactsGrain` fields of a unit sequence, in order. It identifies the unit list that the sidecar's ink flags and unmatched-group indices refer to. The units are hashed one newline-separated line at a time, so the payload for millions of rows is never built as one string."""
    digest = hashlib.sha256()
    first = True
    for unit in units:
        line = f"{unit.codepoints}\t{unit.class_id}\t{int(unit.no_verdict)}\t{','.join(unit.configs)}"
        if first:
            first = False
        else:
            digest.update(b"\n")
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def derive_premerge(snapshot: PremergeSnapshot, table: UnitTable, store: UnitStore) -> PremergeFacts:
    """Project the build's post-merge phase-1 products back onto the pre-merge units the review-facts pins count. A unit that survived the merge reads its own row. A unit that was merged away reads the survivor the merge recorded for it (`PremergeSnapshot.rebase`): the ink flag from the store and the unmatched group from the table.

    Both projections are exact. The merge groups units only when every config of every sibling gives one identical `InkComparator.signature`, which is the pair of run-order ink lists `config_diff` reads, so a merged sibling's delta and ink verdict equal its survivor's. A pre-merge UNMATCHED unit that is novel under the default config always leads its window's merge order, so it is its own survivor and its phase-1 unmatched group is on its own row. Every other UNMATCHED unit is deferred and took its bucket at capture. The assert below checks the second argument where it could fail: an UNMATCHED, undeferred row that was merged away.
    """
    if snapshot.survivor is None or snapshot.removed is None:
        raise ValueError(
            "the pre-merge snapshot is derived once the merge's compaction has been rebased onto it"
        )
    strings = snapshot.strings
    unmatched = strings.find(UNMATCHED_CLASS)
    survivor = snapshot.survivor
    removed = snapshot.removed
    class_ids = snapshot.class_id
    deferred = snapshot.deferred
    ink_identical = store.ink_identical
    flags = bytearray(snapshot.n)
    assigned: list[tuple[int, str]] = []
    for row in range(snapshot.n):
        target = survivor[row]
        is_unmatched = class_ids[row] == unmatched
        assert not is_unmatched or deferred[row] or not removed[row], (
            f"window {format_codepoints(snapshot.codepoints(row))}: a default-novel UNMATCHED unit was merged "
            "away, so its unmatched group cannot be read off its own phase-1 row"
        )
        flags[row] = 49 if ink_identical(target) else 48
        if is_unmatched:
            group = strings[deferred[row]] if deferred[row] else table.unmatched_group(target)
            if not group:
                raise ValueError(
                    f"window {format_codepoints(snapshot.codepoints(row))}: UNMATCHED unit resolved to no unmatched group"
                )
            assigned.append((row, group))
    return PremergeFacts(
        units=snapshot.n,
        workload_digest=workload_digest(snapshot.grains()),
        ink_flags=flags.decode("ascii"),
        unmatched_groups=assigned,
    )


def ink_group_from_flags(class_rows: Iterable[tuple[str, bool]], flags: str) -> dict:
    """The ink group computed from one '0'/'1' flag per pre-merge unit and that unit's (class, no-verdict) pair, both in capture order. It must match `ink_histogram` key for key, including the insertion order of `by_class`; rebuild/test_review_facts.py checks this on synthetic units. Neither side uses Junior equivalence or picture identity: the pre-merge facts count the ink verdict alone."""
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


def unmatched_groups_group_from(assignments: list[str]) -> dict:
    """The unmatched_groups group over groups already assigned, so a build can report the groups phase 1 computed without enriching every UNMATCHED window again."""
    counts = unmatched_group_counts(assignments)
    return {"counts": counts, "total": sum(counts.values())}


def built_group_from_memory(table: UnitTable, config_notes: Mapping[int, str | None]) -> dict:
    """`built_group` computed from the build's in-memory state instead of the shards it wrote: the same three facts by the same rules, including None for a missing reference window, without parsing the shards again. The units are the table's rows; `config_notes` maps each unit's ordinal to its `config_note`, the one fragment field this group reads."""
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
    """The sidecar payload: the finished pins the regenerator copies into the checked-in file, the pre-merge records they were reduced from, and the identity of the corpus that owns them. The pre-merge records let a reader re-reduce the pre-merge groups. Only rebuild/test_review_facts.py reads them."""
    unmatched_groups = unmatched_groups_group_from([group for _index, group in premerge.unmatched_groups])
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
        "premerge": {
            "units": premerge.units,
            "workload_digest": premerge.workload_digest,
            "ink_identical": premerge.ink_flags,
            "unmatched_groups": [[index, group] for index, group in premerge.unmatched_groups],
        },
    }


def write_facts(out_dir: Path, facts: dict) -> None:
    """Write the sidecar beside the manifest as compact JSON. The flag string has one character per pre-merge unit, and the file stays a small fraction of one shard."""
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


@contextlib.contextmanager
def _build_or_load_corpus(corpus: Path | None):
    """Yield (out_dir, manifest). With --corpus, read that built corpus without writing to it. Otherwise build a fresh corpus into a temporary directory under the project tmp/, which is deleted afterward, so the pins never describe a stale corpus."""
    if corpus is not None:
        out_dir = Path(corpus)
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        yield out_dir, manifest
        return
    from rebuild.review.build import build_m1

    scratch = REPO_ROOT / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ams-review-facts-corpus-", dir=scratch) as temp:
        out_dir = Path(temp)
        manifest = build_m1(out_dir)
        yield out_dir, manifest


def compute_pins(corpus: Path | None = None, repo_root: Path = REPO_ROOT, from_scratch: bool = False) -> dict:
    """Both blocks of the pins. By default the volatile block is read from the corpus's review-facts.json sidecar, and the invariant block is reduced again from the corpus's manifest and the sidecar's unmatched-group counts, the same sources the build used, so the checked-in file has the block's current shape whichever build wrote the sidecar. With `from_scratch`, all five volatile groups are recomputed from the source artifacts, which shapes and enriches the corpus again."""
    if from_scratch:
        with _build_or_load_corpus(corpus) as (out_dir, manifest):
            unmatched_groups = unmatched_groups_group(repo_root)
            return {
                "invariant": invariant_group(manifest, unmatched_groups["counts"]),
                "volatile": {
                    "manifest": manifest_group(manifest),
                    "built": built_group(out_dir, manifest),
                    "audit": audit_group(repo_root),
                    "ink": ink_group(repo_root),
                    "unmatched_groups": unmatched_groups,
                },
            }
    with _build_or_load_corpus(corpus) as (out_dir, manifest):
        volatile = load_facts(out_dir, manifest)["pins"]["volatile"]
        return {
            "invariant": invariant_group(manifest, volatile["unmatched_groups"]["counts"]),
            "volatile": volatile,
        }


def _dumps(pins: dict) -> str:
    return json.dumps(pins, indent=2) + "\n"


def _flatten(obj, prefix: str, out: dict) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            _flatten(value, f"{prefix}.{key}" if prefix else str(key), out)
    else:
        out[prefix] = obj


def _mismatches(old: dict, new: dict) -> list[tuple[str, object, object]]:
    """Per-key mismatches over every flattened key of both blocks."""
    old_flat: dict = {}
    new_flat: dict = {}
    _flatten(old, "", old_flat)
    _flatten(new, "", new_flat)
    keys = sorted(set(old_flat) | set(new_flat))
    return [
        (key, old_flat.get(key), new_flat.get(key)) for key in keys if old_flat.get(key) != new_flat.get(key)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check", action="store_true", help="recompute and compare against the checked-in pins (default)"
    )
    action.add_argument("--update", action="store_true", help="recompute and rewrite the pins file")
    parser.add_argument(
        "--corpus",
        "--surface",
        type=Path,
        default=None,
        help="reuse an existing built corpus directory (read-only); default builds a fresh one in a temp directory",
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="recompute the audit/ink/unmatched_groups groups from sources instead of reading the corpus's review-facts.json sidecar — the slow, independent re-derivation",
    )
    args = parser.parse_args(argv)

    new = compute_pins(args.corpus, from_scratch=args.from_scratch)
    if args.update:
        PINS_PATH.write_text(_dumps(new), encoding="utf-8")
        print(f"Wrote {PINS_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 0

    if not PINS_PATH.exists():
        print(f"{PINS_PATH} is missing — run --update first", file=sys.stderr)
        return 1
    old = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    mismatches = _mismatches(old, new)
    if mismatches:
        print("review-facts pins are stale:", file=sys.stderr)
        for key, old_value, new_value in mismatches:
            print(f"  {key}: pinned {old_value!r} != computed {new_value!r}", file=sys.stderr)
        print("Re-baseline with: uv run python -m rebuild.review.facts --update", file=sys.stderr)
        return 1
    print("review-facts pins are current.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
