"""M1-mode unit assembly for the review corpus (rebuild/REVIEW-PLAN.md §1.1, §2.1). It loads rebuild/out/m1/divergence-audit.tsv and rebuild/m1-divergences.yaml, dedupes the audit rows to (codepoints, baseline, new) units, and orders them for triage (`triage_key`): ledger class in ledger file order, then the lead family-pair group in code-point order, then the window's length and codepoints, then the unit's id. `assign_batches` assigns fixed batch slices over that order.

This module does not assign unit ids. A unit's id is `unit_cache.unit_id_for` over the content key the build stamps once the unit is enriched, so it depends on what the reviewer judges and not on where the unit sits in the order.

The dedupe key is name-grain, so a config that renames a glyph without moving ink splits one visual question into sibling units. The build merges them back with the ink-duplicate merge (`merge_ink_duplicate_units`' rules) before enrichment and batching.

The build reads the audit through one of two window sources, both over the descriptor it holds for the whole build. The stream (`stream_workload`) reads an audit in sorted sections (`rebuild/review/audit_stream.py`: one section a configuration, in rank order, each section's windows strictly increasing), as run_m1 writes it, one window at a time: it groups each window's rows into units, merges the window's ink duplicates, keys each unit from its raw lines, and appends the unit's columns, so it holds one window's rows and never the audit's. Any other file, the fixture audit's one-row sections among them, takes the in-memory source (`load_workload`, `load_audit_table`), which stages every row and dedupes them whole, and which a stream that meets a break partway restarts on; `load_table` over parsed rows is the same load and the tests' reference. Both give the same table in load order, the same pre-merge snapshot and merge, and the same input keys.

The workload is held as packed columns over the unit's ordinal (`UnitTable`, built on the `columns` module like `unit_store.UnitStore`), not as a list of records. `Unit` is the per-unit record that a worker, the enricher, the drafter and the fragment writer read; `UnitTable.unit` builds one at a time, and the build's parent never holds a list of them. The in-memory source holds the audit rows as five id columns (`RowColumns`), about seventeen bytes a row: one byte for the config and four each for the kinds, entry, baseline and new ids. It parses each audit line straight into those ids (`load_audit_table`) and builds no `AuditRow`. The stream builds no row columns: what the build reads of a unit's rows after the load is its row count, kept on the table.
"""

from __future__ import annotations

import io
import os
import sys
from array import array
from collections.abc import Callable, Collection, Hashable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path
from typing import NamedTuple, Protocol

import yaml

from rebuild.review import audit_stream, change_groups
from rebuild.review.columns import (
    MappingPool,
    StringTable,
    TuplePool,
    WindowIndex,
    Withheld,
    gather,
    gather_runs,
    window_word,
)

# Restate `conform.ORACLE_CONFIGS` and `conform.M1_ONLY_CONFIGS`, not `conform.ACCEPTANCE_CONFIGS`: importing conform would pull it into the corpus code closure. `rebuild/test_review_tablediff.py` ties them.
ACCEPTANCE_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10")
M1_ONLY_CONFIGS = ("ss08",)
BATCH_SIZE = 300

UNMATCHED_CLASS = "UNMATCHED"

RESERVED_CLASS_IDS = frozenset({UNMATCHED_CLASS, *change_groups.CHANGE_GROUP_ORDER})

# The status vocabulary of the divergence ledger. `by-design` means a rule of the rebuild's design decides the class, `approved` that the user ruled the class's change correct, and `per-unit` that no ruling covers the class as a whole, so each unit's verdict decides it. The change groups are `per-unit` too, and a table-diff bucket has no status.
LEDGER_STATUSES = ("by-design", "approved", "per-unit")
RULED_STATUSES = ("by-design", "approved")

# The review app's sections, in the order the manifest lists their classes. `class_section` is the one place a class is assigned to one.
OPEN_QUESTIONS = "open-questions"
SPOT_CHECK = "spot-check"
NO_REVIEW_NEEDED = "no-review-needed"
SECTIONS = (OPEN_QUESTIONS, SPOT_CHECK, NO_REVIEW_NEEDED)

AUDIT_HEADER = ("config", "codepoints", "kinds", "matched_entry", "baseline", "new")


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One parsed line of the audit. `load_audit` yields these for the tests, and `load_table` writes each row's ids into the row columns as the rows stream past; later readers (the ink-signature keys, the unit content key) read the columns. The build parses the audit with `load_audit_table`, which builds none."""

    config: str
    codepoints: str
    kinds: tuple[str, ...]
    matched_entry: str
    baseline: tuple[str, ...]
    new: tuple[str, ...]


class RowView(NamedTuple):
    """One row of the row columns in the shape `unit_cache.UnitKeyer.signature_key` reads (its `SignatureRow` protocol, which `AuditRow` also satisfies), with `row` giving the row's index in the columns."""

    config: str
    codepoints: str
    baseline: tuple[str, ...]
    new: tuple[str, ...]
    row: int


@dataclass(frozen=True)
class LedgerClass:
    """One class of the review corpus: a divergence-ledger entry (`load_ledger`) or a change group the build synthesizes (`synthesize_unmatched_group_classes`). `title`, `summary` and the optional `note` are the review app's copy for the class, and `why` its full rationale."""

    id: str
    status: str
    why: str
    ink_identical: bool
    no_verdict: bool
    exemplar_keys: frozenset[tuple[str, str]]  # (config, codepoints)
    title: str = ""
    summary: str = ""
    note: str = ""


def class_section(status: str | None, no_verdict: bool, human_units: int) -> str:
    """Return the review app's section for a class. A class that is `no_verdict`, or whose every unit a machine check approves (`human_units` is zero), needs no review, whatever its status. Otherwise a ruled status (`RULED_STATUSES`) makes it a spot-check, and `per-unit`, or None for a table-diff bucket, an open question. Any other status raises."""
    if status is not None and status not in LEDGER_STATUSES:
        raise ValueError(f"{status!r} is not a ledger status ({', '.join(LEDGER_STATUSES)})")
    if no_verdict or human_units == 0:
        return NO_REVIEW_NEEDED
    if status in RULED_STATUSES:
        return SPOT_CHECK
    return OPEN_QUESTIONS


MACHINE_CHECKS = ("ink_identical", "picture_identical", "junior_equivalent")


def machine_approved(fragment) -> bool:
    """Whether a unit's JSON fragment has any machine-approval flag set. The build tries the checks in `MACHINE_CHECKS` order (picture identity only where ink identity fails, Junior equivalence only where both fail), so at most one is true."""
    return any(fragment.get(check_id) is True for check_id in MACHINE_CHECKS)


# The keys a slim fragment leaves out. A unit that is machine-approved (any of MACHINE_CHECKS) or exempt by its ledger class (`no_verdict`) is never paged to a human. The app reaches its fragment only from a show-machine fold or a deep link, which draw the window, both fonts' cells and junctions, the badge and the summary, but not the explain panel's candidate table, the drafts, or the pair band (`highlight`). The build omits these keys because `explain` and `drafts` are the largest fields of a full fragment, and computing the pin draft costs a shaping per unit. The keys are absent, not null, so the app can tell a slim fragment from a full one with a blank field. `build.check_unit` checks the shape in both directions, `build.unit_to_json` is the only writer, and `rebuild/review/static/slim.js` reads the same rule.
SLIM_OMITTED_KEYS = ("highlight", "explain", "drafts")


def slim_fragment(fragment) -> bool:
    """Whether a unit's JSON fragment is written slim: true for every machine-approved or verdict-exempt unit, and no other. It reads the fragment's flags, not which keys are missing, so the checker can check a fragment against the shape its flags require. The unit cache's store record keeps the same value as its `slim` flag, because the exemption comes from the ledger, which the content key does not cover, and a cached fragment must have the shape this build would write."""
    return machine_approved(fragment) or fragment.get("no_verdict") is True


# A plain dict, not a mappingproxy, because a Unit is pickled to every corpus worker and a mappingproxy cannot be pickled. The field's `Mapping` type is what rules out an in-place write.
NO_DELTAS: Mapping[str, str] = {}


class UnitStoreView(Protocol):
    """The per-unit store methods this module calls, each by ordinal: the columns `UnitTable.unit` copies onto a materialized record, the id word `sort_for_triage` orders by, and the machine-approval bit `assign_batches` skips on. `unit_store.UnitStore` is the only implementation. This is a Protocol so that this module does not import `unit_store`, which imports this module and the debug tally (`memory_tally`). The verdict update reaches this module through `status`, and rebuild/test_verdict_update_closure.py follows `if TYPE_CHECKING:` imports too, so importing `unit_store` here, even for types, would put the tally in the verdict update's closure."""

    def input_key_hex(self, ordinal: int) -> str: ...
    def loaded(self, ordinal: int) -> bool: ...
    def unit_id(self, ordinal: int) -> str: ...
    def machine_flags(self, ordinal: int) -> tuple[bool, bool, bool]: ...
    def id_word(self, ordinal: int) -> int: ...
    def machine_approved(self, ordinal: int) -> bool: ...


@dataclass(slots=True)
class Unit:
    """One (codepoints, baseline, new) unit of the audit and what the build derives for it. `UnitTable.unit` materializes one from the workload table for a reader that needs a record: the worker's phase 1, the enricher, the drafter, `build.unit_scaffold` at the write, the verification sample and the tests. The build's parent never holds a list of them.

    `rows_start` and `row_count` address the unit's run of the row columns (`RowColumns`): its audit rows in config order, in file order within a config. The ink-signature keys and the build's content key read the run. After that, `release_rows` drops the columns and only `row_count` remains. `row_count` has no default, because the manifest's row totals are summed from it and a missing count must not read as zero.

    `ordinal` is the unit's row in the workload table and in the build's unit store (`unit_store.UnitStore`), which share one index from the plan boundary on. It is -1 on a unit built outside a table. `input_key` is the unit cache's content key over the unit's inputs (`unit_cache.UnitKeyer.key`), which the plan reuses the unit by; it is copied from the store's column when a store is given. `unit_id` and the three machine flags also come from the store, and are empty or False on a unit the store has not loaded. The worker's phase 1 sets them on its own copy and returns them on the projection.

    `ink_deltas` is always the shared empty mapping `NO_DELTAS` on a unit the build materializes: the store holds the per-config deltas, and drafting takes them as an argument. `ink_deltas` and `config_classes` (the table's pooled mapping) are typed `Mapping`, so a write through either is a type error at that line instead of a change every unit sharing the instance would see. `order` and `batch` are the unit's position in the manifest's triage index and the batch that position falls in, and None for a unit that takes no verdict. Neither is written into the fragment. `ledger_class` is the `ink_identical` ledger class of a unit the build lists under a change group because its picture changes (`change_groups.reroutes`), and empty on every other unit.
    """

    codepoints: str
    baseline: tuple[str, ...]
    new: tuple[str, ...]
    class_id: str
    row_count: int
    rows_start: int = 0
    configs: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    group: str = ""
    exemplar: bool = False
    unit_id: str = ""
    input_key: str = ""
    ordinal: int = -1
    order: int | None = None
    batch: int | None = None
    render_groups: tuple[tuple[str, ...], ...] = ()
    ink_identical: bool = False
    picture_identical: bool = False
    junior_equivalent: bool = False
    ink_deltas: Mapping[str, str] = field(default_factory=lambda: NO_DELTAS)
    no_verdict: bool = False
    config_classes: Mapping[str, str] = field(default_factory=dict)
    unmatched_group: str = ""
    duplicate_group: str | None = None
    cluster: str | None = None
    ledger_class: str = ""

    @property
    def codepoint_values(self) -> tuple[int, ...]:
        return parse_codepoints(self.codepoints)

    @property
    def machine_approved(self) -> bool:
        return any(getattr(self, check_id) for check_id in MACHINE_CHECKS)

    @property
    def slim_fragment(self) -> bool:
        return self.machine_approved or self.no_verdict


def parse_codepoints(codepoints: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in codepoints.split(":"))


def format_codepoints(values: tuple[int, ...]) -> str:
    return ":".join(f"{value:04X}" for value in values)


def _check_audit_header(handle: Iterator[str], path: Path) -> None:
    first = next(handle, None)
    if first is None:
        raise ValueError(f"{path}: empty audit, no header")
    header = first.rstrip("\n").split("\t")
    if tuple(header) != AUDIT_HEADER:
        raise ValueError(f"{path}: unexpected audit header {header!r}")


def load_audit(path: Path, names: TuplePool[str] | None = None) -> Iterator[AuditRow]:
    """Yield every row of the divergence audit in file order, one `AuditRow` at a time, so the parsed rows are never held as a list: `load_table` takes each row's ids and the row can be freed before the next line is split. The build does not call it: `load_audit_table` parses the same lines straight into the row columns, interning and pooling each distinct field the way this does. Every label (a config name, a class id, a glyph name, a kind, a window's codepoint string) goes through `sys.intern`. The subset pack's string table (`subset_pack.SubsetPack`), the unit store's records (`unit_cache.stream_store`) and the parent's per-unit state intern through it too, so a name from the audit and the same name from a worker or the cache are one object. A row's three name tuples (its kinds and the window's rendered names in either font) are pooled through `names`, keyed on the built tuple. A caller that passes the pool the row columns will index gets rows whose tuples are the instances the columns' ids name, and a unit's `baseline` and `new` are those instances too."""
    pool = (names if names is not None else TuplePool[str]()).pooled
    label = sys.intern

    with open(path, encoding="utf-8") as handle:
        _check_audit_header(handle, path)
        for line in handle:
            if not line.strip():
                continue
            config, codepoints, kinds, matched_entry, baseline, new = line.rstrip("\n").split("\t")
            yield AuditRow(
                config=label(config),
                codepoints=label(codepoints),
                kinds=pool(tuple(map(label, kinds.split(",")))),
                matched_entry=label(matched_entry),
                baseline=pool(tuple(map(label, baseline.split("|")))),
                new=pool(tuple(map(label, new.split("|")))),
            )


def _prose_field(path: Path, identifier: str, entry: Mapping, key: str, *, required: bool) -> str:
    value = entry.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {identifier} needs a nonempty string {key}, not {value!r}")
    return value.strip()


def load_ledger(path: Path) -> list[LedgerClass]:
    """Load the divergence ledger's entries as classes, in file order. An entry is refused unless it has a nonempty string id that no other entry and no class the build synthesizes (`RESERVED_CLASS_IDS`) uses, a status from `LEDGER_STATUSES`, and a nonempty `title` and `summary`. Its `match` is not read here: run_m1's pre-gate guards compile the ledger (`oracle.compile_ledger`) and refuse a predicate the oracle does not define before any gate runs. A pass refused there before its first write leaves the Stage A record as it found it, if there is one (only a run whose gates pass writes it), whose `data` value the edited ledger no longer gives, and one refused after its refilter wrote leaves none, so a corpus built over either carries stale or null Stage A values, which the readiness checker flags."""
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    classes: list[LedgerClass] = []
    seen: set[str] = set()
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{path}: every ledger entry needs a nonempty string id, not {identifier!r}")
        if identifier in RESERVED_CLASS_IDS:
            raise ValueError(f"{path}: {identifier} is a class the build synthesizes itself")
        if identifier in seen:
            raise ValueError(f"{path}: {identifier} is declared twice")
        seen.add(identifier)
        status = entry.get("status")
        if status not in LEDGER_STATUSES:
            raise ValueError(
                f"{path}: {identifier} has status {status!r}, not one of {', '.join(LEDGER_STATUSES)}"
            )
        classes.append(
            LedgerClass(
                id=identifier,
                status=status,
                why=(entry.get("why") or "").strip(),
                ink_identical=bool(entry.get("ink_identical", False)),
                no_verdict=bool(entry.get("no_verdict", False)),
                exemplar_keys=frozenset(
                    (exemplar["config"], exemplar["codepoints"]) for exemplar in entry.get("exemplars", ())
                ),
                title=_prose_field(path, identifier, entry, "title", required=True),
                summary=_prose_field(path, identifier, entry, "summary", required=True),
                note=_prose_field(path, identifier, entry, "note", required=False),
            )
        )
    return classes


def synthesize_unmatched_group_classes(
    table: UnitTable,
    group_order: Sequence[str],
    group_prose: Mapping[str, change_groups.ChangeGroupProse],
) -> list[LedgerClass]:
    """Synthetic `LedgerClass` records for the change groups present in the table's unmatched-group column, in `group_order`, with each group's title, summary and why from `group_prose`. Every group has status `per-unit`: no ruling covers it as a whole. A group has no ledger predicate, so the oracle matches no row to it, and its units' unmatched rows, which do not fail the gate, stay unmatched until a ledger entry matches them or the rows stop diverging. The build appends these after the ledger classes, so each group gets a shard and a manifest entry the way a ledger class does. The build passes `change_groups.CHANGE_GROUP_ORDER` and `change_groups.load_change_group_prose()`."""
    counts = table.unmatched_group_counts()
    return [
        LedgerClass(
            id=group_id,
            status="per-unit",
            why=group_prose[group_id].why,
            ink_identical=False,
            no_verdict=False,
            exemplar_keys=frozenset(),
            title=group_prose[group_id].title,
            summary=group_prose[group_id].summary,
        )
        for group_id in group_order
        if group_id in counts
    ]


def group_for(codepoint_values: tuple[int, ...], family_of: Mapping[int, str]) -> str:
    families = [family_of[value] for value in codepoint_values if value in family_of]
    return ":".join(families[:2]) if families else "(boundaries)"


def _config_index(config: str) -> int:
    try:
        return ACCEPTANCE_CONFIGS.index(config)
    except ValueError:
        return len(ACCEPTANCE_CONFIGS)


def render_groups_for_rows(rows: Iterable[tuple[Hashable, Hashable, str]]) -> tuple[tuple[str, ...], ...]:
    """Partition a unit's configs by rendered outcome. Each row is its (baseline, new) cell names, as pooled tuples or their ids, and its config. The M1 dedupe key already includes both tuples, so every real unit has one group (`rebuild/test_review_audit.py` checks this). The grouping is computed anyway, so that configs that render differently show up as extra groups instead of being merged without notice."""
    groups: dict[tuple[Hashable, Hashable], list[str]] = {}
    for baseline, new, config in rows:
        groups.setdefault((baseline, new), []).append(config)
    return tuple(tuple(configs) for configs in groups.values())


_CONFIG_VOCABULARY = 256


class RowColumns:
    """Every row of the audit as five flat columns, grouped in runs: one run per unit, addressed by the unit's `rows_start` and `row_count`, holding the unit's rows in config order (`_config_index`) and in file order within a config. `config` is a byte naming one of at most `_CONFIG_VOCABULARY` configs in `vocabulary`, and `ranks` holds each config id's rank (`_config_index`), which orders the rows within a run. `kinds`, `baseline` and `new` are ids into `names`, the tuple pool the loader pooled the rows' tuples through and seals after the rows are read, so the ids name the same tuple instances the units hold. `entry` is an id into `table`, the string table the workload table shares, for the row's matched ledger class, so a unit's class id and its rows' entry ids are in one vocabulary.

    A row takes about seventeen bytes here. A whole row is rebuilt on demand: `line` returns the audit's line for the row without its newline, byte for byte, which is what the unit content key hashes, and `view` returns the shape the ink-signature key reads. The ink-duplicate merge appends a merged run for a survivor (`merge_runs`) instead of editing in place, so the two runs it replaces remain as `orphaned` rows. The debug tally's reading of the columns (`build.row_column_sizes`) counts their bytes but reports `live` as its count: the number of audit rows, each belonging to one unit. So a tally line shows the same row count the manifest states. The tally is not imported here: the verdict update reaches this module through `status`, and a telemetry module in the verdict update's closure would re-run the verdict update for an edit that cannot change a verdict.
    """

    __slots__ = (
        "config",
        "kinds",
        "entry",
        "baseline",
        "new",
        "vocabulary",
        "ranks",
        "_config_ids",
        "names",
        "table",
        "orphaned",
    )

    def __init__(self, names: TuplePool[str] | None = None, table: StringTable | None = None) -> None:
        self.config = array("B")
        self.kinds = array("I")
        self.entry = array("I")
        self.baseline = array("I")
        self.new = array("I")
        self.vocabulary: list[str] = []
        self.ranks: list[int] = []
        self._config_ids: dict[str, int] = {}
        self.names: TuplePool[str] = names if names is not None else TuplePool[str]()
        self.table = table if table is not None else StringTable()
        self.orphaned = 0

    def __len__(self) -> int:
        return len(self.config)

    @property
    def live(self) -> int:
        return len(self.config) - self.orphaned

    def config_id(self, config: str) -> int:
        found = self._config_ids.get(config)
        if found is None:
            if len(self.vocabulary) == _CONFIG_VOCABULARY:
                raise ValueError(
                    f"the audit names more than {_CONFIG_VOCABULARY} configs, which a byte cannot"
                )
            found = len(self.vocabulary)
            self.vocabulary.append(sys.intern(config))
            self.ranks.append(_config_index(config))
            self._config_ids[config] = found
        return found

    def configs(self, start: int, count: int) -> tuple[str, ...]:
        vocabulary = self.vocabulary
        return tuple(vocabulary[index] for index in self.config[start : start + count])

    def config_at(self, index: int) -> str:
        return self.vocabulary[self.config[index]]

    def line(self, index: int, codepoints: str) -> str:
        names = self.names
        return "\t".join(
            (
                self.vocabulary[self.config[index]],
                codepoints,
                ",".join(names[self.kinds[index]]),
                self.table[self.entry[index]],
                "|".join(names[self.baseline[index]]),
                "|".join(names[self.new[index]]),
            )
        )

    def view(self, index: int, codepoints: str) -> RowView:
        names = self.names
        return RowView(
            self.vocabulary[self.config[index]],
            codepoints,
            names[self.baseline[index]],
            names[self.new[index]],
            index,
        )

    def _copy_row(self, index: int) -> None:
        self.config.append(self.config[index])
        self.kinds.append(self.kinds[index])
        self.entry.append(self.entry[index])
        self.baseline.append(self.baseline[index])
        self.new.append(self.new[index])

    def merge_runs(self, first_start: int, first_count: int, second_start: int, second_count: int) -> int:
        """Append the two runs merged by config rank, the first run's row ahead of the second's at equal rank — a stable sort of the two runs' rows concatenated in that order — and return the merged run's start; the two runs are orphaned."""
        ranks = self.ranks
        config = self.config
        start = len(config)
        i, first_end = first_start, first_start + first_count
        j, second_end = second_start, second_start + second_count
        while i < first_end and j < second_end:
            if ranks[config[j]] < ranks[config[i]]:
                self._copy_row(j)
                j += 1
            else:
                self._copy_row(i)
                i += 1
        for index in range(i, first_end):
            self._copy_row(index)
        for index in range(j, second_end):
            self._copy_row(index)
        self.orphaned += first_count + second_count
        return start

    def columns(self) -> Iterator[array]:
        yield self.config
        yield self.kinds
        yield self.entry
        yield self.baseline
        yield self.new


NONE = 0xFFFFFFFF
INPUT_KEY_BYTES = 32

EXEMPLAR = 1
NO_VERDICT = 2
LIVE = 4


class Compaction(NamedTuple):
    """What `UnitTable.compact` returns. `survivor` maps every pre-merge row to its survivor's post-merge row: its own new row if it stayed live, its survivor's if the merge removed it. `removed` has a byte per pre-merge row that is 1 for a row the merge removed."""

    survivor: array
    removed: bytearray


class UnitTable:
    """The workload as columns over the unit's ordinal. The build's parent holds this from the load to the cache write instead of a list of `Unit` records, in the style of `unit_store.UnitStore`. The loader allocates one row per unit, and each field is a fixed-width `array`. Every name is an id into one `columns.StringTable`, the instance the row columns and the unit store share, so a class from the audit, a change group from a worker and a duplicate-group id from a whole-corpus pass are in one vocabulary. Each tuple or mapping field is an id into a pool, and a read returns the pooled instance, so units with equal values share one object. `configs` and `kinds` are ids into `tuples`, and `render_groups` into `groups`. `baseline` and `new` are ids into `names`, the tuple pool the row columns share, until `release_names` drops both columns and the pool. `config_classes` are ids into `mappings`, a `columns.MappingPool` keyed on each mapping's insertion order (the order the audit states the unit's configs in, which appears in the fragment's bytes); a read returns the pooled mapping typed read-only. The window is parsed once at load into `(start, count)` over a `u16` side column. The ledger's two flags and the merge's `LIVE` bit share one byte. `order` and `batch` are `u32`, with `NONE` for a unit outside the triage index. `rows_start` and `row_count` address the unit's run of the row columns. `survivor` is set by the ink-duplicate merge for a row it removes.

    The row index is the ordinal. The loader writes the rows in load order (ledger class, group, window, with the UNMATCHED units after every ledger class). The in-memory source's merge marks the rows it removes (`merge_into`), and `compact` drops them and renumbers the rest in place; the stream merges each window before it writes the window's rows, so its table holds the merged units alone. After that every row is live, and the unit store is allocated over the same count, so one index reads both tables for the rest of the build. The manifest's triage order is a permutation over the rows (`sort_for_triage`) and does not reorder them. `window_index` holds the rows in window order (`columns.WindowIndex`), which the triage sort walks and the primary-unit search bisects: the stream writes it as it writes the rows, in window order, and for a table the in-memory source loaded it is sorted once on first use; `compact` carries it through the merge. The machine flags and the unit id are stored only in the unit store. `unit` materializes a `Unit` from both tables, as `UnitStore.cached_unit` does for a store record, and `units` materializes the whole list for the tests. The debug tally measures the table through `build.unit_table_sizes` over `columns` and `pools`; the tally is not imported here, for the reason `RowColumns` gives.
    """

    __slots__ = (
        "n",
        "strings",
        "names",
        "tuples",
        "groups",
        "mappings",
        "_class",
        "_group",
        "_unmatched_group",
        "_duplicate_group",
        "_cluster",
        "_configs",
        "_kinds",
        "_render_groups",
        "_baseline",
        "_new",
        "_config_classes",
        "_flags",
        "_window_start",
        "_window_n",
        "_window_values",
        "_rows_start",
        "_row_count",
        "_order",
        "_batch",
        "_survivor",
        "_ranks",
        "_window_index",
    )

    def __init__(self, n: int, strings: StringTable, names: TuplePool[str]) -> None:
        self.n = n
        self.strings = strings
        self.names: TuplePool[str] | None = names
        self.tuples: TuplePool[str] = TuplePool()
        self.groups: TuplePool[tuple[str, ...]] = TuplePool()
        self.mappings = MappingPool()
        self._class = array("I", [0]) * n
        self._group = array("I", [0]) * n
        self._unmatched_group = array("I", [0]) * n
        self._duplicate_group = array("I", [0]) * n
        self._cluster = array("I", [0]) * n
        self._configs = array("I", [0]) * n
        self._kinds = array("I", [0]) * n
        self._render_groups = array("I", [0]) * n
        self._baseline: array | None = array("I", [0]) * n
        self._new: array | None = array("I", [0]) * n
        self._config_classes = array("I", [0]) * n
        self._flags = array("B", [LIVE]) * n
        self._window_start = array("I", [0]) * n
        self._window_n = array("B", [0]) * n
        self._window_values = array("H")
        self._rows_start = array("I", [0]) * n
        self._row_count = array("I", [0]) * n
        self._order = array("I", [NONE]) * n
        self._batch = array("I", [NONE]) * n
        self._survivor = array("I", [NONE]) * n
        self._ranks: list[int] = [len(ACCEPTANCE_CONFIGS)]
        self._window_index: WindowIndex | None = None

    def __len__(self) -> int:
        return self.n

    # --- the loader's writers, one row at a time -------------------------------------------

    def _write_window(self, ordinal: int, values: tuple[int, ...]) -> None:
        self._window_start[ordinal] = len(self._window_values)
        self._window_n[ordinal] = len(values)
        self._window_values.extend(values)

    def _write_names(self, ordinal: int, baseline: int, new: int) -> None:
        assert self._baseline is not None and self._new is not None
        self._baseline[ordinal] = baseline
        self._new[ordinal] = new

    def _tuple_id(self, value: tuple[str, ...]) -> int:
        found = self.tuples.id(value)
        if found == len(self._ranks):
            self._ranks.append(_config_index(value[0]) if value else len(ACCEPTANCE_CONFIGS))
        return found

    # --- the readers --------------------------------------------------------------------------

    def class_id(self, ordinal: int) -> str:
        return self.strings[self._class[ordinal]]

    def group(self, ordinal: int) -> str:
        return self.strings[self._group[ordinal]]

    def unmatched_group(self, ordinal: int) -> str:
        return self.strings[self._unmatched_group[ordinal]]

    def duplicate_group(self, ordinal: int) -> str | None:
        index = self._duplicate_group[ordinal]
        return None if index == 0 else self.strings[index]

    def cluster(self, ordinal: int) -> str | None:
        index = self._cluster[ordinal]
        return None if index == 0 else self.strings[index]

    def configs(self, ordinal: int) -> tuple[str, ...]:
        return self.tuples[self._configs[ordinal]]

    def kinds(self, ordinal: int) -> tuple[str, ...]:
        return self.tuples[self._kinds[ordinal]]

    def render_groups(self, ordinal: int) -> tuple[tuple[str, ...], ...]:
        return self.groups[self._render_groups[ordinal]]

    def baseline(self, ordinal: int) -> tuple[str, ...]:
        if self.names is None or self._baseline is None:
            return ()
        return self.names[self._baseline[ordinal]]

    def new(self, ordinal: int) -> tuple[str, ...]:
        if self.names is None or self._new is None:
            return ()
        return self.names[self._new[ordinal]]

    def config_classes(self, ordinal: int) -> Mapping[str, str]:
        return self.mappings[self._config_classes[ordinal]]

    def exemplar(self, ordinal: int) -> bool:
        return bool(self._flags[ordinal] & EXEMPLAR)

    def no_verdict(self, ordinal: int) -> bool:
        return bool(self._flags[ordinal] & NO_VERDICT)

    def live(self, ordinal: int) -> bool:
        return bool(self._flags[ordinal] & LIVE)

    def survivor(self, ordinal: int) -> int:
        """The row a removed unit merged into, or the row itself while it is live."""
        return ordinal if self._flags[ordinal] & LIVE else self._survivor[ordinal]

    def window_index(self) -> WindowIndex:
        """The rows in window order beside their packed windows (`columns.WindowIndex`), sorted from the window column on the first call when no source wrote it."""
        if self._window_index is None:
            self._window_index = WindowIndex.build(
                (ordinal, self.codepoints(ordinal)) for ordinal in range(self.n)
            )
        return self._window_index

    def release_window_index(self) -> None:
        """Drop the window index once the primary-unit search, its last reader, has run."""
        self._window_index = None

    def codepoints(self, ordinal: int) -> tuple[int, ...]:
        start = self._window_start[ordinal]
        return tuple(self._window_values[start : start + self._window_n[ordinal]])

    def codepoints_text(self, ordinal: int) -> str:
        """The window in the audit's format: colon-joined uppercase hex, at least four digits a codepoint. `parse_codepoints` inverts it, so this returns the string the loader parsed."""
        return format_codepoints(self.codepoints(ordinal))

    def config_rank(self, ordinal: int) -> int:
        """`_config_index` of the unit's first config (a unit's configs are in rank order), memoized per distinct config tuple."""
        return self._ranks[self._configs[ordinal]]

    def rows_start(self, ordinal: int) -> int:
        return self._rows_start[ordinal]

    def row_count(self, ordinal: int) -> int:
        return self._row_count[ordinal]

    def order(self, ordinal: int) -> int | None:
        value = self._order[ordinal]
        return None if value == NONE else value

    def batch(self, ordinal: int) -> int | None:
        value = self._batch[ordinal]
        return None if value == NONE else value

    # --- the writers the build's whole-corpus passes go through ------------------------------------------

    def set_class(self, ordinal: int, class_id: str) -> None:
        self._class[ordinal] = self.strings.id(class_id)

    def set_unmatched_group(self, ordinal: int, group_id: str) -> None:
        self._unmatched_group[ordinal] = self.strings.id(group_id)

    def set_duplicate_group(self, ordinal: int, duplicate_group: str | None) -> None:
        self._duplicate_group[ordinal] = self.strings.optional(duplicate_group)

    def set_cluster(self, ordinal: int, cluster: str | None) -> None:
        self._cluster[ordinal] = self.strings.optional(cluster)

    def set_order_batch(self, ordinal: int, order: int | None, batch: int | None) -> None:
        self._order[ordinal] = NONE if order is None else order
        self._batch[ordinal] = NONE if batch is None else batch

    def set_exemplar(self, ordinal: int, value: bool) -> None:
        self._flags[ordinal] = (
            (self._flags[ordinal] | EXEMPLAR) if value else (self._flags[ordinal] & ~EXEMPLAR)
        )

    def set_no_verdict(self, ordinal: int, value: bool) -> None:
        self._flags[ordinal] = (
            (self._flags[ordinal] | NO_VERDICT) if value else (self._flags[ordinal] & ~NO_VERDICT)
        )

    def set_configs(self, ordinal: int, configs: tuple[str, ...]) -> None:
        self._configs[ordinal] = self._tuple_id(configs)

    def set_kinds(self, ordinal: int, kinds: tuple[str, ...]) -> None:
        self._kinds[ordinal] = self._tuple_id(kinds)

    def set_render_groups(self, ordinal: int, render_groups: tuple[tuple[str, ...], ...]) -> None:
        self._render_groups[ordinal] = self.groups.id(render_groups)

    def set_config_classes(self, ordinal: int, config_classes: Mapping[str, str]) -> None:
        self._config_classes[ordinal] = self.mappings.id(config_classes)

    def set_rows(self, ordinal: int, start: int, count: int) -> None:
        self._rows_start[ordinal] = start
        self._row_count[ordinal] = count

    def merge_into(self, ordinal: int, survivor: int) -> None:
        """Mark the row as removed by the ink-duplicate merge into `survivor`; `compact` drops it."""
        if not self._flags[survivor] & LIVE:
            raise ValueError(f"row {ordinal} merges into row {survivor}, which is not live")
        self._flags[ordinal] &= ~LIVE
        self._survivor[ordinal] = survivor

    def compact(self) -> Compaction:
        """Drop every row the merge removed, renumber the rest in place, and return the map from each pre-merge row to its survivor's post-merge row. The window values, the pools and the string table are not changed, and a row's `(start, count)` moves with the row, so a snapshot taken before the merge that copied the window offsets still reads the same values through them."""
        flags = self._flags
        n = self.n
        remap = array("I", [NONE]) * n
        removed = bytearray(n)
        kept = array("I")
        for ordinal in range(n):
            if flags[ordinal] & LIVE:
                remap[ordinal] = len(kept)
                kept.append(ordinal)
            else:
                removed[ordinal] = 1
        for ordinal in range(n):
            if removed[ordinal]:
                remap[ordinal] = remap[self._survivor[ordinal]]
        if len(kept) != n:
            self._class = array("I", map(self._class.__getitem__, kept))
            self._group = array("I", map(self._group.__getitem__, kept))
            self._unmatched_group = array("I", map(self._unmatched_group.__getitem__, kept))
            self._duplicate_group = array("I", map(self._duplicate_group.__getitem__, kept))
            self._cluster = array("I", map(self._cluster.__getitem__, kept))
            self._configs = array("I", map(self._configs.__getitem__, kept))
            self._kinds = array("I", map(self._kinds.__getitem__, kept))
            self._render_groups = array("I", map(self._render_groups.__getitem__, kept))
            if self._baseline is not None and self._new is not None:
                self._baseline = array("I", map(self._baseline.__getitem__, kept))
                self._new = array("I", map(self._new.__getitem__, kept))
            self._config_classes = array("I", map(self._config_classes.__getitem__, kept))
            self._flags = array("B", map(self._flags.__getitem__, kept))
            self._window_start = array("I", map(self._window_start.__getitem__, kept))
            self._window_n = array("B", map(self._window_n.__getitem__, kept))
            self._rows_start = array("I", map(self._rows_start.__getitem__, kept))
            self._row_count = array("I", map(self._row_count.__getitem__, kept))
            self._order = array("I", map(self._order.__getitem__, kept))
            self._batch = array("I", map(self._batch.__getitem__, kept))
            self._survivor = array("I", [NONE]) * len(kept)
            self.n = len(kept)
            if self._window_index is not None:
                self._window_index = self._window_index.remapped(remap, removed)
        return Compaction(remap, removed)

    def release_names(self) -> None:
        """Drop the `baseline` and `new` columns and their name pool; afterward `baseline` and `new` return the empty tuple. Nothing in the parent reads a name tuple after phase 1: each worker read them from the copy it was sent, and the verification sample holds its own copies."""
        self._baseline = self._new = None
        self.names = None

    # --- whole-table readings --------------------------------------------------------------------

    def unmatched_group_counts(self) -> dict[str, int]:
        counts: dict[int, int] = {}
        for index in self._unmatched_group:
            if index:
                counts[index] = counts.get(index, 0) + 1
        return {self.strings[index]: count for index, count in counts.items()}

    def classes_present(self) -> set[str]:
        return {self.strings[index] for index in set(self._class)}

    def rows_by_class(self, order: Sequence[int]) -> dict[str, array]:
        """Every class's ordinals in the order `order` states them, keyed by class id in the order the classes first appear along it."""
        grouped: dict[int, array] = {}
        column = self._class
        for ordinal in order:
            index = column[ordinal]
            members = grouped.get(index)
            if members is None:
                members = grouped[index] = array("I")
            members.append(ordinal)
        return {self.strings[index]: members for index, members in grouped.items()}

    def class_ids(self) -> array:
        return array("I", self._class)

    def configs_ids(self) -> array:
        return array("I", self._configs)

    def config_classes_ids(self) -> array:
        return array("I", self._config_classes)

    def flags(self) -> array:
        return array("B", self._flags)

    def windows(self) -> tuple[array, array, array]:
        """`(start, count, values)`: copies of the window offsets and the shared values column, for a snapshot that outlives a compaction."""
        return array("I", self._window_start), array("B", self._window_n), self._window_values

    def columns(self) -> Iterator[array]:
        yield self._class
        yield self._group
        yield self._unmatched_group
        yield self._duplicate_group
        yield self._cluster
        yield self._configs
        yield self._kinds
        yield self._render_groups
        if self._baseline is not None and self._new is not None:
            yield self._baseline
            yield self._new
        yield self._config_classes
        yield self._flags
        yield self._window_start
        yield self._window_n
        yield self._window_values
        yield self._rows_start
        yield self._row_count
        yield self._order
        yield self._batch
        yield self._survivor

    def pools(self) -> Iterator[TuplePool | MappingPool]:
        """The pools the id columns point into, which the debug tally measures with the columns: the config and kind tuples, the render groups, the class maps, and the name tuples until `release_names`."""
        yield self.tuples
        yield self.groups
        yield self.mappings
        if self.names is not None:
            yield self.names

    # --- materialization ----------------------------------------------------------------------

    def unit(self, ordinal: int, store: UnitStoreView | None = None) -> Unit:
        """Materialize the unit at `ordinal` as a `Unit`. With a store, the input key comes from the store, and the id and the three machine flags come from it too once the store has loaded the unit; everything else comes from this table. Each `phase1` message sends a worker one batch of these, the write materializes one at a time, and the parent never holds a list of them."""
        flags = self._flags[ordinal]
        unit_id = ""
        input_key = ""
        ink_identical = picture_identical = junior_equivalent = False
        if store is not None:
            input_key = store.input_key_hex(ordinal)
            if store.loaded(ordinal):
                unit_id = store.unit_id(ordinal)
                ink_identical, picture_identical, junior_equivalent = store.machine_flags(ordinal)
        return Unit(
            codepoints=self.codepoints_text(ordinal),
            baseline=self.baseline(ordinal),
            new=self.new(ordinal),
            class_id=self.class_id(ordinal),
            row_count=self._row_count[ordinal],
            rows_start=self._rows_start[ordinal],
            configs=self.configs(ordinal),
            kinds=self.kinds(ordinal),
            group=self.group(ordinal),
            exemplar=bool(flags & EXEMPLAR),
            unit_id=unit_id,
            input_key=input_key,
            ordinal=ordinal,
            order=self.order(ordinal),
            batch=self.batch(ordinal),
            render_groups=self.render_groups(ordinal),
            ink_identical=ink_identical,
            picture_identical=picture_identical,
            junior_equivalent=junior_equivalent,
            no_verdict=bool(flags & NO_VERDICT),
            config_classes=self.config_classes(ordinal),
            unmatched_group=self.unmatched_group(ordinal),
            duplicate_group=self.duplicate_group(ordinal),
            cluster=self.cluster(ordinal),
        )

    def units(self, store: UnitStoreView | None = None) -> list[Unit]:
        """Every live row materialized, in row order, for the tests. The build's parent does not call this."""
        return [self.unit(ordinal, store) for ordinal in range(self.n) if self._flags[ordinal] & LIVE]

    def gather(self, ordinals: Sequence[int]) -> UnitTable:
        """Return a table over the units at `ordinals`, each answered by its position in that sequence: one chunk of a class for a write worker (`build._write_chunks`). Every column the write reads is copied for those rows, the window's values with its offsets rebased, and the string table and the pools are this table's own, which the worker holds once (`build._SharedPickler`). The name tuples are left out as `release_names` leaves them, so `baseline` and `new` read empty, and `survivor` and `config_rank`, which the write never reads, are `Withheld`. A read past the chunk's last position raises, as any array read past its end does."""
        chunk = UnitTable.__new__(UnitTable)
        chunk.n = len(ordinals)
        chunk.strings = self.strings
        chunk.names = None
        chunk.tuples = self.tuples
        chunk.groups = self.groups
        chunk.mappings = self.mappings
        for name in (
            "_class",
            "_group",
            "_unmatched_group",
            "_duplicate_group",
            "_cluster",
            "_configs",
            "_kinds",
            "_render_groups",
            "_config_classes",
            "_flags",
            "_window_n",
            "_rows_start",
            "_row_count",
            "_order",
            "_batch",
        ):
            setattr(chunk, name, gather(getattr(self, name), ordinals))
        chunk._baseline = chunk._new = None
        chunk._window_start, (chunk._window_values,) = gather_runs(
            self._window_start, self._window_n, ordinals, (self._window_values, 1)
        )
        for name in ("_survivor", "_ranks", "_window_index"):
            setattr(chunk, name, Withheld(name))
        return chunk


def load_table(
    rows: Iterable[AuditRow],
    ledger: list[LedgerClass],
    family_of: dict[int, str],
    names: TuplePool[str] | None = None,
) -> tuple[UnitTable, RowColumns]:
    """Dedupe the audit rows to (codepoints, baseline, new) units, and return them as a `UnitTable` in load order together with the row columns the units' runs address. Load order is ledger class, group, window, with the UNMATCHED units after every ledger class, because change groups are assigned only at enrichment, and the units of one window in the order the file first names them. Once every unit has its change group and id, the build orders the units for the manifest with `sort_for_triage` and assigns batches.

    This is the in-memory source, one of the build's two window sources (the module docstring): it takes rows in any order, and the tests hold the stream (`stream_workload`) to it. The stream reads an audit in sorted sections one window at a time and gives the same table in the same load order, with the ink-duplicate merge already made and each unit's input key computed.

    A unit's matched ledger class can differ by config: a window can match a ledger class under an ss03 config and be UNMATCHED under a config without ss03, such as the default. Each unit keeps the full per-config class map in `config_classes`, in the order the file states the configs. Its `class_id` is the single matched class if every config matched, and UNMATCHED if any config did not, so the unmatched default behavior is what gets reviewed; the matched configs stay in `config_classes` for display. A unit whose rows match two different ledger classes raises `ValueError`.

    The rows are read once. Each row's five ids go into file-order arrays, and its unit's first-seen index into one more. A counting placement over the per-unit counts writes each row's position into its unit's run in file order. Any run whose configs are not already in rank order is then sorted by config rank, so each run is a stable sort by config rank. The columns are the file-order arrays permuted by those positions. The transient state (the file-order arrays, the position array, the map from each (window, baseline, new) id triple, packed into one integer, to its unit, the window list, and the triage sort's one integer per row in `_triage_permutation`) is freed before return. The tuple pool is sealed when the stream ends, which frees its lookup dict before the load phase goes on. Table rows are written in first-seen order and then permuted into load order.

    `load_audit_table` is the same load over the audit file, parsed straight into the arrays without building an `AuditRow` per line.
    """
    columns = RowColumns(names)
    return _load_staged(columns, lambda: _stage_rows(rows, columns), ledger, family_of)


def load_audit_table(
    path: Path,
    ledger: list[LedgerClass],
    family_of: dict[int, str],
    *,
    mark: Callable[[str], None] | None = None,
    descriptor: int | None = None,
) -> tuple[UnitTable, RowColumns]:
    """`load_table(load_audit(path, names), ledger, family_of, names)` with the parse fused into the row loop (`_stage_audit`), which is how the build's in-memory source loads the audit; the columns, the name pool, the string table and the config vocabulary come out identical to that form. `mark` is `load_workload`'s. With `descriptor`, the audit is read through that open descriptor from its start, and `path` only names it in messages."""
    columns = RowColumns()
    return _load_staged(columns, lambda: _stage_audit(path, columns, descriptor), ledger, family_of, mark)


_Staged = tuple[array, array, array, array, array, array, array, list[str]]


def _stage_rows(rows: Iterable[AuditRow], columns: RowColumns) -> _Staged:
    """The row loop over parsed rows: each row's config, kinds, entry, baseline and new ids and its unit's first-seen index in file order, each unit's row count, and each unit's window string, with the tuple pool sealed once the rows are read."""
    names = columns.names
    config_id = columns.config_id
    name_id = names.id
    entry_id = columns.table.id

    config_f = array("B")
    kinds_f = array("I")
    entry_f = array("I")
    baseline_f = array("I")
    new_f = array("I")
    unit_f = array("I")
    counts = array("I")
    windows: list[str] = []
    window_ids: dict[str, int] = {}
    unit_of: dict[int, int] = {}
    for row in rows:
        baseline = name_id(row.baseline)
        new = name_id(row.new)
        window = window_ids.get(row.codepoints)
        if window is None:
            window = window_ids[row.codepoints] = len(window_ids)
        triple = (window << 64) | (baseline << 32) | new
        unit = unit_of.get(triple)
        if unit is None:
            unit = unit_of[triple] = len(windows)
            windows.append(row.codepoints)
            counts.append(1)
        else:
            counts[unit] += 1
        unit_f.append(unit)
        config_f.append(config_id(row.config))
        kinds_f.append(name_id(row.kinds))
        entry_f.append(entry_id(row.matched_entry))
        baseline_f.append(baseline)
        new_f.append(new)
    del unit_of, window_ids
    names.seal()
    return config_f, kinds_f, entry_f, baseline_f, new_f, unit_f, counts, windows


def _audit_text(path: Path, descriptor: int | None) -> io.TextIOBase:
    """The audit as UTF-8 text from its start: opened by `path`, or, with `descriptor`, read through a duplicate of the descriptor the build holds, so a file replaced by rename since the build opened it is not read."""
    if descriptor is None:
        return open(path, encoding="utf-8")
    duplicate = os.dup(descriptor)
    os.lseek(duplicate, 0, os.SEEK_SET)
    return io.TextIOWrapper(io.BufferedReader(io.FileIO(duplicate, "rb", closefd=True)), encoding="utf-8")


def _stage_audit(path: Path, columns: RowColumns, descriptor: int | None = None) -> _Staged:
    """`_stage_rows` over the audit file, parsing each line straight into the arrays. Each of a line's kinds, rendered-name, matched-entry and config fields maps to its id through a memo keyed on the raw field string, so a repeated field costs one dict lookup; a field seen for the first time is split, interned and pooled as `load_audit` does it, kinds, then baseline, then new, so the ids are assigned in the same order. A unit's window string is interned once, when the unit is first seen. The memos are freed beside the pool's seal."""
    names = columns.names
    config_id = columns.config_id
    name_id = names.id
    entry_id = columns.table.id
    label = sys.intern

    config_f = array("B")
    kinds_f = array("I")
    entry_f = array("I")
    baseline_f = array("I")
    new_f = array("I")
    unit_f = array("I")
    counts = array("I")
    windows: list[str] = []
    window_ids: dict[str, int] = {}
    unit_of: dict[int, int] = {}
    kinds_memo: dict[str, int] = {}
    names_memo: dict[str, int] = {}
    entry_memo: dict[str, int] = {}
    config_memo: dict[str, int] = {}
    with _audit_text(path, descriptor) as handle:
        _check_audit_header(handle, path)
        for line in handle:
            if not line.strip():
                continue
            config, codepoints, kinds, matched_entry, baseline, new = line.rstrip("\n").split("\t")
            kinds_id = kinds_memo.get(kinds)
            if kinds_id is None:
                kinds_id = kinds_memo[kinds] = name_id(tuple(map(label, kinds.split(","))))
            baseline_id = names_memo.get(baseline)
            if baseline_id is None:
                baseline_id = names_memo[baseline] = name_id(tuple(map(label, baseline.split("|"))))
            new_id = names_memo.get(new)
            if new_id is None:
                new_id = names_memo[new] = name_id(tuple(map(label, new.split("|"))))
            window = window_ids.get(codepoints)
            if window is None:
                window = window_ids[codepoints] = len(window_ids)
            triple = (window << 64) | (baseline_id << 32) | new_id
            unit = unit_of.get(triple)
            if unit is None:
                unit = unit_of[triple] = len(windows)
                windows.append(label(codepoints))
                counts.append(1)
            else:
                counts[unit] += 1
            unit_f.append(unit)
            config_index = config_memo.get(config)
            if config_index is None:
                config_index = config_memo[config] = config_id(config)
            config_f.append(config_index)
            kinds_f.append(kinds_id)
            entry_index = entry_memo.get(matched_entry)
            if entry_index is None:
                entry_index = entry_memo[matched_entry] = entry_id(matched_entry)
            entry_f.append(entry_index)
            baseline_f.append(baseline_id)
            new_f.append(new_id)
    del unit_of, window_ids, kinds_memo, names_memo, entry_memo, config_memo
    names.seal()
    return config_f, kinds_f, entry_f, baseline_f, new_f, unit_f, counts, windows


def _load_staged(
    columns: RowColumns,
    stage: Callable[[], _Staged],
    ledger: list[LedgerClass],
    family_of: dict[int, str],
    mark: Callable[[str], None] | None = None,
) -> tuple[UnitTable, RowColumns]:
    """The unit half of `load_table`: run the row loop `stage` into `columns`' pools, place its file-order arrays into the row columns, and build the unit table over them. `stage` is called here rather than its arrays passed in, so this frame holds the only reference to them and can free each as soon as it is spent. `mark` is `load_workload`'s, called as the row loop returns and once the triage permutation is built."""
    config_f, kinds_f, entry_f, baseline_f, new_f, unit_f, counts, windows = stage()
    if mark is not None:
        mark("stage_audit returned")
    exempt_classes = {entry.id for entry in ledger if entry.no_verdict}
    names = columns.names
    strings = columns.table
    ranks = columns.ranks
    starts = array("I", accumulate(counts, initial=0))
    fill = array("I", starts)
    order = array("I", (0,)) * len(unit_f)
    for position, unit in enumerate(unit_f):
        order[fill[unit]] = position
        fill[unit] += 1
    del fill, unit_f
    for unit, count in enumerate(counts):
        if count > 1:
            start = starts[unit]
            end = start + count
            run = order[start:end]
            run_ranks = [ranks[config_f[position]] for position in run]
            if run_ranks != sorted(run_ranks):
                order[start:end] = array("I", (position for _, position in sorted(zip(run_ranks, run))))
    columns.config = array("B", map(config_f.__getitem__, order))
    columns.kinds = array("I", map(kinds_f.__getitem__, order))
    columns.entry = array("I", map(entry_f.__getitem__, order))
    columns.baseline = array("I", map(baseline_f.__getitem__, order))
    columns.new = array("I", map(new_f.__getitem__, order))
    del config_f, kinds_f, entry_f, baseline_f, new_f

    table = UnitTable(len(windows), strings, names)
    exemplar_keys = {key for entry in ledger for key in entry.exemplar_keys}
    exempt_ids = {strings.id(class_id) for class_id in exempt_classes}
    unmatched = strings.id(UNMATCHED_CLASS)
    vocabulary = columns.vocabulary
    config_col = columns.config
    kinds_col = columns.kinds
    entry_col = columns.entry
    baseline_col = columns.baseline
    new_col = columns.new
    configs_of: dict[bytes, int] = {}
    kinds_of: dict[bytes, int] = {}
    classes_of: dict[tuple[int, ...], int] = {}
    tuples = table.tuples
    groups = table.groups
    mappings = table.mappings
    class_col = table._class
    group_col = table._group
    configs_ids = table._configs
    kinds_ids = table._kinds
    render_ids = table._render_groups
    mapping_ids = table._config_classes
    flags = table._flags
    for unit, codepoints in enumerate(windows):
        start = starts[unit]
        count = counts[unit]
        end = start + count
        run: Sequence[int] = range(start, end)
        if count > 1:
            positions = order[start:end]
            if any(positions[index] > positions[index + 1] for index in range(count - 1)):
                run = sorted(run, key=order.__getitem__)
        classes = set(entry_col[start:end])
        matched = classes - {unmatched}
        if len(matched) > 1:
            raise ValueError(
                f"unit {codepoints} spans multiple matched ledger classes: {sorted(strings[index] for index in matched)}"
            )
        class_id = unmatched if unmatched in classes else matched.pop()
        mapping_key = tuple(item for index in run for item in (config_col[index], entry_col[index]))
        mapping = classes_of.get(mapping_key)
        if mapping is None:
            mapping = classes_of[mapping_key] = mappings.id(
                {vocabulary[config_col[index]]: strings[entry_col[index]] for index in run}
            )
        config_bytes = config_col[start:end].tobytes()
        configs = configs_of.get(config_bytes)
        if configs is None:
            configs = configs_of[config_bytes] = table._tuple_id(columns.configs(start, count))
        kinds_bytes = kinds_col[start:end].tobytes()
        kinds = kinds_of.get(kinds_bytes)
        if kinds is None:
            kinds = kinds_of[kinds_bytes] = table._tuple_id(
                tuple(sorted({kind for index in range(start, end) for kind in names[kinds_col[index]]}))
            )
        config_tuple = tuples[configs]
        values = parse_codepoints(codepoints)
        class_col[unit] = class_id
        group_col[unit] = strings.id(group_for(values, family_of))
        configs_ids[unit] = configs
        kinds_ids[unit] = kinds
        render_ids[unit] = groups.id(
            render_groups_for_rows(zip(baseline_col[start:end], new_col[start:end], config_tuple))
        )
        mapping_ids[unit] = mapping
        table._write_names(unit, baseline_col[start], new_col[start])
        table._write_window(unit, values)
        table.set_rows(unit, start, count)
        flags[unit] = (
            LIVE
            | (
                EXEMPLAR
                if exemplar_keys and any((config, codepoints) in exemplar_keys for config in config_tuple)
                else 0
            )
            | (NO_VERDICT if class_id in exempt_ids else 0)
        )
    del order, starts, counts, windows, configs_of, kinds_of, classes_of

    class_order = {entry.id: index for index, entry in enumerate(ledger)}
    permutation = _triage_permutation(table, class_order, family_ranks(family_of))
    if mark is not None:
        mark("triage permutation built")
    _permute(table, permutation)
    return table, columns


def _permute(table: UnitTable, permutation: Sequence[int]) -> None:
    """Reorder every per-row column of a freshly loaded table so that row `i` becomes the row `permutation[i]` was. The window values and the pools do not move, since the rows address them by offset and id."""
    for name in (
        "_class",
        "_group",
        "_unmatched_group",
        "_duplicate_group",
        "_cluster",
        "_configs",
        "_kinds",
        "_render_groups",
        "_baseline",
        "_new",
        "_config_classes",
        "_window_start",
        "_rows_start",
        "_row_count",
        "_order",
        "_batch",
        "_survivor",
    ):
        column = getattr(table, name)
        setattr(table, name, array("I", map(column.__getitem__, permutation)))
    for name in ("_flags", "_window_n"):
        column = getattr(table, name)
        setattr(table, name, array("B", map(column.__getitem__, permutation)))
    table._window_index = None


def build_units(
    rows: Iterable[AuditRow],
    ledger: list[LedgerClass],
    family_of: dict[int, str],
    names: TuplePool[str] | None = None,
) -> tuple[list[Unit], RowColumns]:
    """`load_table` with its units materialized into a list, for the tests; the build uses the table."""
    table, columns = load_table(rows, ledger, family_of, names)
    return table.units(), columns


def family_ranks(family_of: Mapping[int, str]) -> dict[str, int]:
    """Map each family name to its code point, the key a group's two families sort by."""
    return {name: value for value, name in family_of.items()}


def triage_key(
    class_index: int,
    group: str,
    codepoint_values: tuple[int, ...],
    unit_id: str,
    family_rank: Mapping[str, int],
) -> tuple:
    """The sort key for the order a corpus pages its human units in (the manifest's `human_unit_ids`): the class's index in the manifest's class list, the group's families by code point, the window's length and codepoints, and last the unit's id. The id breaks ties between sibling units of one window by content instead of by audit order. Every term depends only on the unit and the ledger, so the same units sort the same way on every corpus. `build.check_shards` checks every manifest's index against this key."""
    return (
        class_index,
        tuple(family_rank.get(name, 10**6) for name in group.split(":")),
        len(codepoint_values),
        codepoint_values,
        unit_id,
    )


def sort_for_triage(
    table: UnitTable, store: UnitStoreView, class_order: Mapping[str, int], family_of: Mapping[int, str]
) -> array:
    """Return the triage order as a permutation of the table's rows. The rows are not reordered, so the row index stays the ordinal. The class term is each unit's final class (its ledger class, or the change group the build promoted it to), indexed through `class_order`, which maps every class the manifest lists. The id term is the store's `id_word`, the content key's first eight bytes as an integer. It sorts like the id strings `triage_key` uses, because `unit_cache.base58_64` writes a fixed-width base58 over an ascending alphabet. The other terms are `triage_key`'s. It is a counting sort over the table's window index (`_counting_permutation`), the permutation `_triage_permutation` gives with the id term, ties included."""
    index = table.window_index()
    return _counting_permutation(
        table._class,
        table._group,
        table.strings,
        class_order,
        family_ranks(family_of),
        index.ordinals,
        table.n,
        runs=index.runs(),
        id_word=store.id_word,
    )


def _counting_permutation(
    class_col: Sequence[int],
    group_col: Sequence[int],
    strings: StringTable,
    class_order: Mapping[str, int],
    family_rank: Mapping[str, int],
    walk: Sequence[int] | Iterable[int],
    n: int,
    *,
    runs: Iterable[tuple[int, int]] | None = None,
    id_word: Callable[[int], int] | None = None,
) -> array:
    """Return `n` rows in `triage_key` order as a permutation, without a comparison sort over the rows: count each (class, group) bucket, `_triage_permutation`'s first two terms, in one pass, then place the rows into their buckets' slices in the order `walk` gives, which must be window order with the rows of one window in ascending order. A bucket's slice then holds its rows by window, and only rows of one window can tie. With `id_word`, `runs` gives the stretches of `walk` that share a window (`columns.WindowIndex.runs`), and the rows of each that land in one bucket, which sit side by side there, are sorted by id word, keeping ascending order among equal words. Without it the tie keeps `walk`'s order, which is the load's: the stream walks its rows in window order and, within a window, in the order the file first names them. Either way the result is `_triage_permutation`'s with the same terms. It holds one bucket number a row, beside the permutation."""
    unranked = len(class_order)
    class_rank = {index: class_order.get(strings[index], unranked) for index in set(class_col)}
    group_tuples = {
        index: tuple(family_rank.get(name, 10**6) for name in strings[index].split(":"))
        for index in set(group_col)
    }
    tuple_rank = {ranks: rank for rank, ranks in enumerate(sorted(set(group_tuples.values())))}
    width = max(len(tuple_rank), 1)
    class_term = {index: rank * width for index, rank in class_rank.items()}
    group_term = {index: tuple_rank[ranks] for index, ranks in group_tuples.items()}
    buckets = (max(class_rank.values(), default=0) + 1) * width
    bucket = array("I", [0]) * n
    counts = array("I", [0]) * buckets
    for ordinal in range(n):
        number = class_term[class_col[ordinal]] + group_term[group_col[ordinal]]
        bucket[ordinal] = number
        counts[number] += 1
    fill = array("I", accumulate(counts, initial=0))
    del counts
    order = array("I", [0]) * n
    if runs is None or id_word is None:
        for ordinal in walk:
            number = bucket[ordinal]
            at = fill[number]
            order[at] = ordinal
            fill[number] = at + 1
        return order
    walked = walk if isinstance(walk, (array, list, range)) else array("I", walk)
    position = 0
    for start, end in (*runs, (len(walked), len(walked))):
        for ordinal in walked[position:start]:
            number = bucket[ordinal]
            at = fill[number]
            order[at] = ordinal
            fill[number] = at + 1
        placed: dict[int, list[int]] = {}
        for ordinal in walked[start:end]:
            number = bucket[ordinal]
            at = fill[number]
            order[at] = ordinal
            fill[number] = at + 1
            placed.setdefault(number, []).append(at)
        for spots in placed.values():
            if len(spots) > 1:
                low, high = spots[0], spots[-1] + 1
                order[low:high] = array("I", sorted(order[low:high], key=id_word))
        position = end
    return order


def _triage_permutation(
    table: UnitTable,
    class_order: Mapping[str, int],
    family_rank: Mapping[str, int],
    id_word: Callable[[int], int] | None = None,
) -> array:
    """Return the rows in `triage_key` order as a permutation, sorting one integer per row instead of a tuple. The in-memory source's load orders its rows with it, and the tests hold `_counting_permutation` to it. From the high bits down, the integer packs: the class index; the group's rank among the distinct family-rank tuples (equal tuples share a rank, so groups `triage_key` cannot tell apart stay tied); the window's length; the window's codepoints, big-endian; the id word, when given; and the row index, which makes the sort stable. Each field is wide enough for its largest value, so the packing is exact for any table. The sort holds one `int` per row, a few dozen bytes each, in the list it sorts in place, and frees it on return."""
    strings = table.strings
    unranked = len(class_order)
    class_rank = {index: class_order.get(strings[index], unranked) for index in set(table._class)}
    group_tuples = {
        index: tuple(family_rank.get(name, 10**6) for name in strings[index].split(":"))
        for index in set(table._group)
    }
    tuple_rank = {ranks: rank for rank, ranks in enumerate(sorted(set(group_tuples.values())))}
    counts = table._window_n
    n = table.n
    ordinal_bits = max(n - 1, 0).bit_length()
    id_bits = 64 if id_word is not None else 0
    window_bits = 16 * (max(counts) if n else 0)
    length_bits = window_bits.bit_length()
    group_bits = max(len(tuple_rank) - 1, 0).bit_length()
    window_shift = ordinal_bits + id_bits
    length_shift = window_shift + window_bits
    group_shift = length_shift + length_bits
    class_shift = group_shift + group_bits
    class_term = {index: rank << class_shift for index, rank in class_rank.items()}
    group_term = {index: tuple_rank[ranks] << group_shift for index, ranks in group_tuples.items()}
    length_term = [count << length_shift for count in range(window_bits // 16 + 1)]
    class_col = table._class
    group_col = table._group
    starts = table._window_start
    values = table._window_values
    swap = sys.byteorder == "little"
    words: list[int] = []
    for ordinal in range(n):
        count = counts[ordinal]
        start = starts[ordinal]
        window = values[start : start + count]
        if swap:
            window.byteswap()
        word = (
            class_term[class_col[ordinal]]
            | group_term[group_col[ordinal]]
            | length_term[count]
            | int.from_bytes(window.tobytes(), "big") << window_shift
        )
        if id_word is not None:
            word |= id_word(ordinal) << ordinal_bits
        words.append(word | ordinal)
    words.sort()
    mask = (1 << ordinal_bits) - 1
    return array("I", (word & mask for word in words))


def _sibling_windows(table: UnitTable) -> dict[bytes, list[int]]:
    """Every window that two or more live units share, mapped from the window's packed values to those units' ordinals in row order. The dict over all windows holds one int per window, and a list exists only for a shared window."""
    first: dict[bytes, int] = {}
    siblings: dict[bytes, list[int]] = {}
    values = table._window_values
    starts = table._window_start
    counts = table._window_n
    flags = table._flags
    for ordinal in range(table.n):
        if not flags[ordinal] & LIVE:
            continue
        start = starts[ordinal]
        key = values[start : start + counts[ordinal]].tobytes()
        seen = first.setdefault(key, ordinal)
        if seen != ordinal:
            group = siblings.get(key)
            if group is None:
                siblings[key] = [seen, ordinal]
            else:
                group.append(ordinal)
    return siblings


class SignatureRows:
    """One `RowView` per signature `merge_ink_duplicate_units` asks for: every row of every sibling in a shared window, in `_sibling_windows` order. It can be iterated any number of times; the views are built during iteration and not held. Because it uses the same `_sibling_windows` as the merge, a signature provider built over these rows covers every pair the merge asks for."""

    def __init__(self, table: UnitTable, rows: RowColumns) -> None:
        self._siblings = array(
            "I", (ordinal for siblings in _sibling_windows(table).values() for ordinal in siblings)
        )
        self._table = table
        self._rows = rows

    def __iter__(self) -> Iterator[RowView]:
        view = self._rows.view
        table = self._table
        for ordinal in self._siblings:
            codepoints = table.codepoints_text(ordinal)
            start = table.rows_start(ordinal)
            for index in range(start, start + table.row_count(ordinal)):
                yield view(index, codepoints)


def signature_rows(table: UnitTable, rows: RowColumns) -> SignatureRows:
    return SignatureRows(table, rows)


@dataclass(slots=True)
class _Member:
    """One sibling unit as the ink-duplicate merge reads and widens it: its row in the table (or its place in the stream's window), its config rank, its configs, kinds and per-config classes, its exemplar flag, and once it absorbs a sibling, its class and exemption; `survivor` is the member that absorbed it, or -1."""

    ordinal: int
    rank: int
    configs: tuple[str, ...]
    kinds: tuple[str, ...]
    config_classes: Mapping[str, str]
    exemplar: bool
    class_id: str = ""
    no_verdict: bool = False
    merged: bool = False
    survivor: int = -1


def _merge_members(
    members: list[_Member],
    exempt_classes: Collection[str],
    stats: dict,
    absorb: Callable[[_Member, _Member], tuple[str, ...]],
) -> bool:
    """Merge one window's members whose every config gives one ink signature, the rule `merge_ink_duplicate_units` states: the survivor is the member with the earliest config (a stable sort by config rank), and each later member in that order is absorbed unless the two would join two matched ledger classes, which is counted. `absorb(survivor, other)` merges the two runs of rows, the survivor's first at equal rank, and returns the merged configs. Return whether any member was absorbed."""
    members.sort(key=lambda member: member.rank)
    survivor = members[0]
    merged_any = False
    for other in members[1:]:
        matched = {cls for cls in survivor.config_classes.values() if cls != UNMATCHED_CLASS} | {
            cls for cls in other.config_classes.values() if cls != UNMATCHED_CLASS
        }
        if len(matched) > 1:
            stats["kept_split_matched_classes"] += 1
            continue
        survivor.configs = absorb(survivor, other)
        survivor.kinds = tuple(sorted(set(survivor.kinds) | set(other.kinds)))
        survivor.config_classes = {**survivor.config_classes, **other.config_classes}
        classes = set(survivor.config_classes.values())
        survivor.class_id = UNMATCHED_CLASS if UNMATCHED_CLASS in classes else matched.pop()
        survivor.no_verdict = survivor.class_id in exempt_classes
        survivor.exemplar = survivor.exemplar or other.exemplar
        survivor.merged = True
        other.survivor = survivor.ordinal
        stats["units_merged"] += 1
        merged_any = True
    return merged_any


def merge_ink_duplicate_units(
    table: UnitTable, rows: RowColumns, ink_sig, exempt_classes: Collection[str] = frozenset()
) -> dict:
    """Merge sibling units of one window whose placed ink is identical in both fonts under every config they cover. The (codepoints, baseline, new) dedupe key is name-grain, so a config that only renames a glyph splits one visual question into two units; for example, the old font's ss04 lookups rename word-initial ·It without changing its ink. `ink_sig(codepoints, config)` returns the rendered-outcome identity of the window under the config (`InkComparator.signature`, the pair of run-order ink lists that `config_diff` reads), where `codepoints` is the window in the audit's format (`UnitTable.codepoints_text`), the same string the rows of `signature_rows` carry, so a provider built over those rows is keyed by it directly. Units merge only when every config on both sides has the same signature, so the delta, its digest and the ink verdict are identical for the survivor and the units it absorbs.

    The survivor is the sibling with the earliest config. It gets a merged run of both units' rows (`RowColumns.merge_runs`, its own rows first at equal rank, so each row keeps its own rendered names and the content key over the run is the key over the rows), the union of the configs and kinds, and the merged `config_classes` (its own entries first). It keeps its own baseline and new name tuples for display, re-resolves its class with `load_table`'s unmatched-first class rule, and collapses to a single render group, since ink identity implies render-group identity. A merge that would give one unit two different matched ledger classes is skipped, because different names can match different ledger predicates, and is counted in the returned stats. The survivor's row is rewritten in place and each absorbed row is marked with `UnitTable.merge_into`; the caller compacts the table afterward. Run before enrichment and batch assignment. This is the in-memory source's merge; the stream applies the same rules (`_merge_members`) to each window as it reads it (`stream_workload`).
    """
    stats = {"windows_merged": 0, "units_merged": 0, "kept_split_matched_classes": 0}

    def absorb(survivor: _Member, other: _Member) -> tuple[str, ...]:
        count = table.row_count(survivor.ordinal) + table.row_count(other.ordinal)
        start = rows.merge_runs(
            table.rows_start(survivor.ordinal),
            table.row_count(survivor.ordinal),
            table.rows_start(other.ordinal),
            table.row_count(other.ordinal),
        )
        table.set_rows(survivor.ordinal, start, count)
        return rows.configs(start, count)

    for siblings in _sibling_windows(table).values():
        codepoints = table.codepoints_text(siblings[0])
        groups: dict[tuple, list[_Member]] = {}
        for ordinal in siblings:
            signatures = {ink_sig(codepoints, config) for config in table.configs(ordinal)}
            if len(signatures) == 1:
                groups.setdefault(signatures.pop(), []).append(
                    _Member(
                        ordinal,
                        table.config_rank(ordinal),
                        table.configs(ordinal),
                        table.kinds(ordinal),
                        table.config_classes(ordinal),
                        table.exemplar(ordinal),
                    )
                )
        for members in groups.values():
            if len(members) < 2:
                continue
            if _merge_members(members, exempt_classes, stats, absorb):
                stats["windows_merged"] += 1
            for member in members:
                if member.merged:
                    survivor = member.ordinal
                    table.set_configs(survivor, member.configs)
                    table.set_kinds(survivor, member.kinds)
                    table.set_config_classes(survivor, member.config_classes)
                    table.set_class(survivor, member.class_id)
                    table.set_no_verdict(survivor, member.no_verdict)
                    table.set_render_groups(survivor, (member.configs,))
                    table.set_exemplar(survivor, member.exemplar)
            for member in members:
                if member.survivor >= 0:
                    table.merge_into(member.ordinal, member.survivor)
    return stats


class AuditLayout(NamedTuple):
    """What `audit_layout` finds: the audit's sections (`audit_stream.Section`) when it is in sorted sections, or None and `why` it is not, beside the byte its rows start at."""

    sections: list[audit_stream.Section] | None
    why: str
    rows_start: int


def audit_layout(descriptor: int, path: Path) -> AuditLayout:
    """Check the audit's header through the held descriptor, raising the in-memory source's `ValueError` for an empty file or another header, and find its sections (`audit_stream.audit_sections`), or why it has none: a header that is not UTF-8 or holds a carriage return, which the in-memory source's text read takes, or sections out of rank order."""
    size = os.fstat(descriptor).st_size
    if size == 0:
        raise ValueError(f"{path}: empty audit, no header")
    line, start = audit_stream.line_at(descriptor, 0, size)
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return AuditLayout(None, "its header is not UTF-8", start)
    if "\r" in text:
        return AuditLayout(None, "its header ends in a carriage return", start)
    header = text.split("\t")
    if tuple(header) != AUDIT_HEADER:
        raise ValueError(f"{path}: unexpected audit header {header!r}")
    sections = audit_stream.audit_sections(descriptor, start, size, _config_index)
    if isinstance(sections, str):
        return AuditLayout(None, sections, start)
    return AuditLayout(sections, "", start)


class PremergeColumns(NamedTuple):
    """The stream's pre-merge units in load order, in the columns `facts.PremergeSnapshot` holds: each one's class id and config-set id (into the table's string table and tuple pool), its exemption, and its window's offsets into the table's value column."""

    class_id: array
    no_verdict: bytearray
    configs: array
    window_start: array
    window_n: array


class StreamedLoad(NamedTuple):
    """What `stream_workload` returns: the workload, whose table holds the merged units in load order with their window index; the pre-merge units' columns in load order and the compaction from each to its merged unit's row; each merged unit's input key, 32 bytes a row in load order; and the merge's stats."""

    workload: Workload
    premerge: PremergeColumns
    compaction: Compaction
    input_keys: bytearray
    merge_stats: dict


def _merged_rows(first: list, second: list) -> list:
    """Two runs of `(section, fields, line)` rows merged by section, which is config rank, the first run's row ahead at an equal one, as `RowColumns.merge_runs` merges runs."""
    merged = []
    i = j = 0
    while i < len(first) and j < len(second):
        if second[j][0] < first[i][0]:
            merged.append(second[j])
            j += 1
        else:
            merged.append(first[i])
            i += 1
    merged.extend(first[i:])
    merged.extend(second[j:])
    return merged


def stream_workload(
    descriptor: int,
    audit_path: Path,
    ledger_path: Path,
    sections: Sequence[audit_stream.Section],
    ledger: list[LedgerClass],
    family_of: Mapping[int, str],
    family_keys: Mapping[str, str],
    ink_digest: Callable[[int, str, str], Hashable] | None,
    *,
    mark: Callable[..., None] | None = None,
) -> StreamedLoad:
    """Load the audit from its sorted sections one window at a time (`audit_stream.lockstep`), the build's stream source (the module docstring). Each window's rows group into units by `(baseline, new)` in the order the file first names them, which in rank-ordered sections is config order; each unit's run is in rank order, and so is its `config_classes`, as in the file. A unit is classed, flagged and pooled as `load_table` does it, the unknown-entry check runs once the rows are read, and a unit whose rows match two ledger classes raises the same `ValueError`, though with two such units it may name another first. In a window of two or more units every row's ink-signature key is computed from its raw fields (`audit_stream.signature_key`) and `ink_digest(key, window, config)` gives its signature, and the window's units merge by `merge_ink_duplicate_units`' rules (`_merge_members`). With `ink_digest` None the merge is skipped. Each merged unit's input key is the sha256 of its run's raw lines and its window's family lines (`audit_stream.input_key`), as `unit_cache.UnitKeyer.key` computes it from row columns.

    The merged units are written in window order, and the pre-merge units' snapshot columns beside them. Once the rows are read, the load order is a counting sort of the pre-merge units over their classes and groups, walked in that window order (`_counting_permutation`), the permutation `_triage_permutation` gives the in-memory source's table; the merged units keep their survivors' places in it, as `UnitTable.compact` keeps them, so the table, the snapshot and the input keys come out as the in-memory source's after its merge and compaction. The window index (`UnitTable.window_index`) is the merged units in window order, which the walk wrote them in. `mark` is the build's sub-step hook, called as the rows are read (`stream ended`) and once the load order is placed (`triage permutation built`). An `audit_stream.OrderBreak` from the walk propagates, and the caller discards what the stream built.
    """
    label = sys.intern
    strings = StringTable()
    names: TuplePool[str] = TuplePool()
    table = UnitTable(0, strings, names)
    tuple_id = table._tuple_id
    groups_pool = table.groups
    mappings = table.mappings
    window_values = table._window_values
    lines_of = audit_stream.FamilyLines(family_of, family_keys)
    exempt = frozenset(entry.id for entry in ledger if entry.no_verdict)
    exemplar_keys = {key for entry in ledger for key in entry.exemplar_keys}
    section_configs = [label(section.config) for section in sections]
    section_ranks = [section.rank for section in sections]
    stats = {"windows_merged": 0, "units_merged": 0, "kept_split_matched_classes": 0}
    unmatched = UNMATCHED_CLASS

    class_col = array("I")
    group_col = array("I")
    configs_col = array("I")
    kinds_col = array("I")
    render_col = array("I")
    baseline_col = array("I")
    new_col = array("I")
    mapping_col = array("I")
    flags_col = array("B")
    start_col = array("I")
    count_col = array("B")
    rows_col = array("I")
    words = array("Q")
    keys = bytearray()

    pre_class = array("I")
    pre_group = array("I")
    pre_exempt = bytearray()
    pre_configs = array("I")
    pre_start = array("I")
    pre_count = array("B")
    post_of = array("I")
    absorbed = bytearray()

    configs_memo: dict[tuple[int, ...], tuple[tuple[str, ...], int]] = {}
    kinds_memo: dict[tuple[str, ...], tuple[tuple[str, ...], int]] = {}
    class_memo: dict[tuple[tuple[int, ...], tuple[str, ...]], tuple[str, int, Mapping[str, str], int]] = {}
    entries_seen: set[str] = set()
    packed = True
    row_total = 0

    def name_id(field: str) -> int:
        return names.id(tuple(map(label, field.split("|"))))

    for window, found in audit_stream.lockstep(descriptor, sections):
        row_total += len(found)
        values = audit_stream.parse_window(window)
        offset = len(window_values)
        window_values.extend(values)
        length = len(values)
        group = strings.id(group_for(values, family_of))
        word = window_word(values)
        if word is None:
            packed = False
            word = 0
        suffix = lines_of.suffix(values)
        if len(found) == 1:
            units = [found]
        else:
            by_pair: dict[tuple[str, str], list] = {}
            for row in found:
                fields = row[1]
                pair = (fields[4], fields[5])
                unit = by_pair.get(pair)
                if unit is None:
                    by_pair[pair] = [row]
                else:
                    unit.append(row)
            units = list(by_pair.values())
        members: list[_Member] = []
        identities: list[tuple[int, int, int, int, int]] = []
        for place, unit_rows in enumerate(units):
            section_key = tuple(row[0] for row in unit_rows)
            got_configs = configs_memo.get(section_key)
            if got_configs is None:
                configs = tuple(section_configs[index] for index in section_key)
                got_configs = configs_memo[section_key] = (configs, tuple_id(configs))
            configs, configs_id = got_configs
            kinds_key = tuple(row[1][2] for row in unit_rows)
            got_kinds = kinds_memo.get(kinds_key)
            if got_kinds is None:
                kinds = tuple(sorted({label(kind) for field in kinds_key for kind in field.split(",")}))
                got_kinds = kinds_memo[kinds_key] = (kinds, tuple_id(kinds))
            kinds, kinds_id = got_kinds
            entries = tuple(row[1][3] for row in unit_rows)
            got_class = class_memo.get((section_key, entries))
            if got_class is None:
                classes = set(entries)
                matched = classes - {unmatched}
                if len(matched) > 1:
                    raise ValueError(
                        f"unit {window} spans multiple matched ledger classes: {sorted(matched)}"
                    )
                entries_seen.update(classes)
                class_name = label(unmatched if unmatched in classes else matched.pop())
                mapping_id = mappings.id(
                    {section_configs[index]: label(entry) for index, entry in zip(section_key, entries)}
                )
                got_class = class_memo[(section_key, entries)] = (
                    class_name,
                    strings.id(class_name),
                    mappings[mapping_id],
                    mapping_id,
                )
            class_name, class_index, mapping, mapping_id = got_class
            exemplar = bool(exemplar_keys) and any((config, window) in exemplar_keys for config in configs)
            exempted = class_name in exempt
            members.append(
                _Member(
                    place,
                    section_ranks[section_key[0]],
                    configs,
                    kinds,
                    mapping,
                    exemplar,
                    class_name,
                    exempted,
                )
            )
            identities.append((class_index, configs_id, kinds_id, mapping_id, 1 if exempted else 0))
        if len(units) > 1 and ink_digest is not None:
            by_signature: dict[Hashable, list[_Member]] = {}
            for member, unit_rows in zip(members, units):
                signatures = {
                    ink_digest(audit_stream.signature_key(fields, suffix), window, fields[0])
                    for _index, fields, _line in unit_rows
                }
                if len(signatures) == 1:
                    by_signature.setdefault(signatures.pop(), []).append(member)

            def absorb(survivor: _Member, other: _Member) -> tuple[str, ...]:
                merged = units[survivor.ordinal] = _merged_rows(units[survivor.ordinal], units[other.ordinal])
                return tuple(section_configs[row[0]] for row in merged)

            for group_members in by_signature.values():
                if len(group_members) > 1 and _merge_members(group_members, exempt, stats, absorb):
                    stats["windows_merged"] += 1
        posts = [0] * len(members)
        for member, unit_rows in zip(members, units):
            if member.survivor >= 0:
                continue
            posts[member.ordinal] = len(class_col)
            class_index, configs_id, kinds_id, mapping_id, _exempted = identities[member.ordinal]
            if member.merged:
                class_index = strings.id(member.class_id)
                configs_id = tuple_id(member.configs)
                kinds_id = tuple_id(member.kinds)
                mapping_id = mappings.id(member.config_classes)
            class_col.append(class_index)
            group_col.append(group)
            configs_col.append(configs_id)
            kinds_col.append(kinds_id)
            render_col.append(groups_pool.id((member.configs,)))
            baseline_col.append(name_id(unit_rows[0][1][4]))
            new_col.append(name_id(unit_rows[0][1][5]))
            mapping_col.append(mapping_id)
            flags_col.append(
                LIVE | (EXEMPLAR if member.exemplar else 0) | (NO_VERDICT if member.no_verdict else 0)
            )
            start_col.append(offset)
            count_col.append(length)
            rows_col.append(len(unit_rows))
            words.append(word)
            keys += audit_stream.input_key((row[2] for row in unit_rows), suffix)
        for member in members:
            class_index, configs_id, _kinds_id, _mapping_id, exempted = identities[member.ordinal]
            pre_class.append(class_index)
            pre_group.append(group)
            pre_exempt.append(exempted)
            pre_configs.append(configs_id)
            pre_start.append(offset)
            pre_count.append(length)
            absorbed.append(1 if member.survivor >= 0 else 0)
            post_of.append(posts[member.survivor if member.survivor >= 0 else member.ordinal])
    names.seal()
    n_pre = len(pre_class)
    n = len(class_col)
    if mark is not None:
        mark("stream ended", units=n_pre, rows=row_total)
    unknown = sorted(entries_seen - {unmatched} - {entry.id for entry in ledger})
    if unknown:
        raise ValueError(
            f"{audit_path}: rows match classes {ledger_path} does not declare: {', '.join(unknown)}"
        )

    load = _counting_permutation(
        pre_class,
        pre_group,
        strings,
        {entry.id: index for index, entry in enumerate(ledger)},
        family_ranks(family_of),
        range(n_pre),
        n_pre,
    )
    if mark is not None:
        mark("triage permutation built")
    order = array("I", (post_of[pre] for pre in load if not absorbed[pre]))
    place_of = array("I", [0]) * n
    for position, post in enumerate(order):
        place_of[post] = position
    premerge = PremergeColumns(
        gather(pre_class, load),
        bytearray(map(pre_exempt.__getitem__, load)),
        gather(pre_configs, load),
        gather(pre_start, load),
        gather(pre_count, load),
    )
    compaction = Compaction(
        array("I", (place_of[post_of[pre]] for pre in load)), bytearray(map(absorbed.__getitem__, load))
    )
    del load, pre_class, pre_group, pre_exempt, pre_configs, pre_start, pre_count, post_of, absorbed

    table.n = n
    streamed = {
        "_class": class_col,
        "_group": group_col,
        "_configs": configs_col,
        "_kinds": kinds_col,
        "_render_groups": render_col,
        "_baseline": baseline_col,
        "_new": new_col,
        "_config_classes": mapping_col,
        "_flags": flags_col,
        "_window_start": start_col,
        "_window_n": count_col,
        "_row_count": rows_col,
    }
    del class_col, group_col, configs_col, kinds_col, render_col, baseline_col, new_col, mapping_col
    del flags_col, start_col, count_col, rows_col
    for name in list(streamed):
        setattr(table, name, gather(streamed.pop(name), order))
    for name in ("_unmatched_group", "_duplicate_group", "_cluster", "_rows_start"):
        setattr(table, name, array("I", [0]) * n)
    for name in ("_order", "_batch", "_survivor"):
        setattr(table, name, array("I", [NONE]) * n)
    input_keys = bytearray(INPUT_KEY_BYTES * n)
    for position, post in enumerate(order):
        input_keys[position * INPUT_KEY_BYTES : (position + 1) * INPUT_KEY_BYTES] = keys[
            post * INPUT_KEY_BYTES : (post + 1) * INPUT_KEY_BYTES
        ]
    del keys, order
    if packed:
        index = WindowIndex(place_of, words)
        for start, end in index.runs():
            run = place_of[start:end]
            place_of[start:end] = array("I", sorted(run))
        table._window_index = index
    workload = Workload(
        table=table,
        ledger=ledger,
        row_count=row_total,
        rows=None,
        classes_present=[entry for entry in ledger if entry.id in table.classes_present()],
    )
    return StreamedLoad(workload, premerge, compaction, input_keys, stats)


def release_rows(workload: Workload) -> None:
    """Drop the workload's row columns; after this, each unit's `row_count` is the only record of its rows. The build reads row fields only to dedupe, to merge ink duplicates, and to compute the unit content key and the ink-signature keys, all before the first unit is enriched, so the columns are freed here instead of being held through the units phase for a count. The string table the columns share with the unit table stays. The tuple pool the columns index is the table's `names`, which `UnitTable.release_names` frees."""
    workload.rows = None


def assign_batches(
    table: UnitTable, store: UnitStoreView, order: Sequence[int], batch_size: int = BATCH_SIZE
) -> int:
    """Set each unit's place in the manifest's triage index, walking the units in `order`. A human unit (no machine check approves it, per the store, and its ledger class does not exempt it, per the table) gets its position among the human units as `order` and the slice of `batch_size` that position falls in as `batch`. Machine-approved and no-verdict units get None for both, since none is paged to a human. Neither value is written into a fragment: the manifest's `human_unit_ids` is the index, and a batch is a slice of it. Returns the batch count."""
    index = 0
    machine_approved = store.machine_approved
    no_verdict = table.no_verdict
    for ordinal in order:
        if machine_approved(ordinal) or no_verdict(ordinal):
            table.set_order_batch(ordinal, None, None)
        else:
            table.set_order_batch(ordinal, index, index // batch_size)
            index += 1
    return (index + batch_size - 1) // batch_size


def batch_of(order: int | None, batch_size: int) -> int | None:
    """The batch a triage-index position falls in, or None for a unit outside the index."""
    return None if order is None else order // batch_size


@dataclass
class Workload:
    """What `load_workload` returns to the build: the unit table, the ledger, the audit's row count, the row columns until `release_rows` drops them, and the ledger classes the units reach. `units` materializes the table's rows into a list for the tests; the build does not hold that list."""

    table: UnitTable
    ledger: list[LedgerClass]
    row_count: int
    rows: RowColumns | None = None
    classes_present: list[LedgerClass] = field(default_factory=list)

    def units(self) -> list[Unit]:
        return self.table.units()


def load_workload(
    audit_path: Path,
    ledger_path: Path,
    family_of: dict[int, str],
    *,
    mark: Callable[[str], None] | None = None,
    descriptor: int | None = None,
) -> Workload:
    """Load the ledger and the audit into a `Workload` through the in-memory source, the table in load order before the ink-duplicate merge, beside its row columns. Every row's `matched_entry` must be UNMATCHED or a ledger id: an audit written by run_m1 under an older ledger, one whose ids have since been renamed, would otherwise list its units under classes the ledger does not describe. `mark` is the build's sub-step hook (`memory_tally.mark`), passed in rather than imported for the reason `RowColumns` gives. With `descriptor`, the audit is read through that open descriptor (`load_audit_table`)."""
    ledger = load_ledger(ledger_path)
    table, rows = load_audit_table(audit_path, ledger, family_of, mark=mark, descriptor=descriptor)
    named = {rows.table[index] for index in set(rows.entry)}
    unknown = sorted(named - {UNMATCHED_CLASS} - {entry.id for entry in ledger})
    if unknown:
        raise ValueError(
            f"{audit_path}: rows match classes {ledger_path} does not declare: {', '.join(unknown)}"
        )
    present = table.classes_present()
    return Workload(
        table=table,
        ledger=ledger,
        row_count=len(rows),
        rows=rows,
        classes_present=[entry for entry in ledger if entry.id in present],
    )
