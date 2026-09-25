"""M1-mode unit assembly for the review surface (rebuild/REVIEW-PLAN.md §1.1, §2.1). It loads rebuild/out/m1/divergence-audit.tsv and rebuild/m1-divergences.yaml, dedupes the audit rows to (codepoints, baseline, new) units, and orders them for triage (`triage_key`): ledger class in ledger file order, then the lead family-pair group in code-point order, then the window's length and codepoints, then the unit's id. `assign_batches` assigns fixed batch slices over that order.

This module does not assign unit ids. A unit's id is `unit_cache.unit_id_for` over the content key the build stamps once the unit is enriched, so it depends on what the reviewer judges and not on where the unit sits in the order.

The dedupe key is name-grain, so a config that renames a glyph without moving ink splits one visual question into sibling units. The build merges them back with `merge_ink_duplicate_units` before enrichment and batching.

The workload is held as packed columns over the unit's ordinal (`UnitTable`, built on the `columns` module like `unit_store.UnitStore`), not as a list of records. `Unit` is the per-unit record that a worker, the enricher, the drafter and the fragment writer read; `UnitTable.unit` builds one at a time, and the build's parent never holds a list of them. The audit rows are likewise five id columns (`RowColumns`), about seventeen bytes a row: one byte for the config and four each for the kinds, entry, baseline and new ids. A parsed `AuditRow` lives only until the loader has written its ids.
"""

from __future__ import annotations

import sys
from array import array
from collections.abc import Callable, Collection, Hashable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path
from typing import NamedTuple, Protocol

import yaml

from rebuild.review import families
from rebuild.review.columns import MappingPool, StringTable, TuplePool

ACCEPTANCE_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10")
BATCH_SIZE = 300

UNMATCHED_CLASS = "UNMATCHED"

RESERVED_CLASS_IDS = frozenset({UNMATCHED_CLASS, *families.FAMILY_ORDER})

AUDIT_HEADER = ("config", "codepoints", "kinds", "matched_entry", "baseline", "new")


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One parsed line of the audit. `load_audit` yields these and the build does not keep them: `load_table` writes each row's ids into the row columns as the rows stream past, and later readers (the ink-signature keys, the unit content key) read the columns."""

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
    id: str
    status: str
    why: str
    ink_identical: bool
    no_verdict: bool
    count: int
    exemplar_keys: frozenset[tuple[str, str]]  # (config, codepoints)


MACHINE_CHANNELS = ("ink_identical", "picture_identical", "junior_equivalent")


def machine_approved(fragment) -> bool:
    """Whether a unit's JSON fragment has any machine-approval flag set. The build tries the channels in `MACHINE_CHANNELS` order (picture identity only where ink identity fails, Junior equivalence only where both fail), so at most one is true."""
    return any(fragment.get(channel) is True for channel in MACHINE_CHANNELS)


# The keys a slim fragment leaves out. A unit that is machine-approved (any of MACHINE_CHANNELS) or exempt by its ledger class (`no_verdict`) is never paged to a human. The app reaches its fragment only from a show-machine fold or a deep link, which draw the window, both fonts' cells and seams, the badge and the summary, but not the explain panel's candidate table, the drafts, or the pair band (`highlight`). The build omits these keys because `explain` and `drafts` are the largest fields of a full fragment, and computing the pin draft costs a shaping per unit. The keys are absent, not null, so the app can tell a slim fragment from a full one with a blank field. `build.check_unit` checks the shape in both directions, `build.unit_to_json` is the only writer, and `rebuild/review/static/slim.js` reads the same rule.
SLIM_OMITTED_KEYS = ("highlight", "explain", "drafts")


def slim_fragment(fragment) -> bool:
    """Whether a unit's JSON fragment is written slim: true for every machine-approved or verdict-exempt unit, and no other. It reads the fragment's flags, not which keys are missing, so the checker can check a fragment against the shape its flags require. The unit cache's store record keeps the same value as its `slim` flag, because the exemption comes from the ledger, which the content key does not cover, and a served fragment must have the shape this build would write."""
    return machine_approved(fragment) or fragment.get("no_verdict") is True


# A plain dict, not a mappingproxy, because a Unit is pickled to every surface worker and a mappingproxy cannot be pickled. The field's `Mapping` type is what rules out an in-place write.
NO_DELTAS: Mapping[str, str] = {}


class UnitStoreView(Protocol):
    """The per-unit store methods this module calls, each by ordinal: the columns `UnitTable.unit` copies onto a materialized record, the id word `sort_for_triage` orders by, and the machine-approval bit `assign_batches` skips on. `unit_store.UnitStore` is the only implementation. This is a Protocol so that this module does not import `unit_store`, which imports this module and the debug tally (`pile_tally`). The verdict chain reaches this module through `status`, and rebuild/test_plumbing_closure.py follows `if TYPE_CHECKING:` imports too, so importing `unit_store` here, even for types, would put the tally in the chain's closure."""

    def input_key_hex(self, ordinal: int) -> str: ...
    def folded(self, ordinal: int) -> bool: ...
    def unit_id(self, ordinal: int) -> str: ...
    def machine_flags(self, ordinal: int) -> tuple[bool, bool, bool]: ...
    def id_word(self, ordinal: int) -> int: ...
    def machine_approved(self, ordinal: int) -> bool: ...


@dataclass(slots=True)
class Unit:
    """One (codepoints, baseline, new) unit of the audit and what the build derives for it. `UnitTable.unit` materializes one from the workload table for a reader that needs a record: the worker's phase 1, the enricher, the drafter, `build.unit_scaffold` at the write, the verification sample, the census CLI and the tests. The build's parent never holds a list of them.

    `rows_start` and `row_count` address the unit's run of the row columns (`RowColumns`): its audit rows in config order, in file order within a config. The ink-signature keys and the build's content key read the run. After that, `release_rows` drops the columns and only `row_count` remains. `row_count` has no default, because the manifest's row totals are summed from it and a missing count must not read as zero.

    `ordinal` is the unit's row in the workload table and in the build's unit store (`unit_store.UnitStore`), which share one index from the plan boundary on. It is -1 on a unit built outside a table. `input_key` is the unit cache's content key over the unit's inputs (`unit_cache.UnitKeyer.key`), which the plan serves the unit by; it is copied from the store's column when a store is given. `unit_id` and the three machine flags also come from the store, and are empty or False on a unit the store has not folded. The worker's phase 1 sets them on its own copy and returns them on the projection.

    `ink_deltas` is always the shared empty mapping `NO_DELTAS` on a unit the build materializes: the store holds the per-config deltas, and drafting takes them as an argument. `ink_deltas` and `config_classes` (the table's pooled mapping) are typed `Mapping`, so a write through either is a type error at that line instead of a change every unit sharing the instance would see. `order` and `batch` are the unit's position in the manifest's triage index and the batch that position falls in, and None for a unit that takes no verdict. Neither is written into the fragment.
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
    def slim_fragment(self) -> bool:
        return self.machine_approved or self.no_verdict


def parse_codepoints(codepoints: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in codepoints.split(":"))


def format_codepoints(values: tuple[int, ...]) -> str:
    return ":".join(f"{value:04X}" for value in values)


def load_audit(path: Path, names: TuplePool[str] | None = None) -> Iterator[AuditRow]:
    """Yield every row of the divergence audit in file order, one `AuditRow` at a time, so the parsed rows are never held as a list: `load_table` takes each row's ids and the row can be freed before the next line is split. Every label (a config name, a class id, a glyph name, a kind, a window's codepoint string) goes through `sys.intern`. The subset pack's string table (`subset_pack.SubsetPack`), the unit store's records (`unit_cache.stream_store`) and the parent's per-unit state intern through it too, so a name from the audit and the same name from a worker or the cache are one object. A row's three name tuples (its kinds and the window's rendered names in either font) are pooled through `names`, keyed on the built tuple. A caller that passes the pool the row columns will index (as `load_workload` does) gets rows whose tuples are the instances the columns' ids name, and a unit's `baseline` and `new` are those instances too."""
    pool = (names if names is not None else TuplePool[str]()).pooled
    label = sys.intern

    with open(path, encoding="utf-8") as handle:
        header = next(handle).rstrip("\n").split("\t")
        if tuple(header) != AUDIT_HEADER:
            raise ValueError(f"{path}: unexpected audit header {header!r}")
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


def load_ledger(path: Path) -> list[LedgerClass]:
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
        classes.append(
            LedgerClass(
                id=identifier,
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
    table: UnitTable,
    family_order: list[str],
    family_why: dict[str, str],
) -> list[LedgerClass]:
    """Synthetic `LedgerClass` records for the verdict families present among the UNMATCHED units, in `family_order`, counted from the table's family column. `status='unmatched'` marks them as a grouping for presentation only: they have no ledger predicate, and the oracle stays dirty until they are adjudicated. The build appends them after the ledger classes, so each family gets a shard and a manifest entry the way a ledger class does. The build passes `families.FAMILY_ORDER` and `families.FAMILY_WHY`."""
    counts = table.family_counts()
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


def render_groups_for_rows(rows: Iterable[tuple[Hashable, Hashable, str]]) -> tuple[tuple[str, ...], ...]:
    """Partition a unit's configs by rendered outcome. Each row is its (baseline, new) cell names, as pooled tuples or their ids, and its config. The M1 dedupe key already includes both tuples, so every real unit has one group (`rebuild/test_review_audit.py` checks this). The grouping is computed anyway, so that configs that render differently show up as extra groups instead of being merged without notice."""
    groups: dict[tuple[Hashable, Hashable], list[str]] = {}
    for baseline, new, config in rows:
        groups.setdefault((baseline, new), []).append(config)
    return tuple(tuple(configs) for configs in groups.values())


_CONFIG_VOCABULARY = 256


class RowColumns:
    """Every row of the audit as five flat columns, grouped in runs: one run per unit, addressed by the unit's `rows_start` and `row_count`, holding the unit's rows in config order (`_config_index`) and in file order within a config. `config` is a byte naming one of at most `_CONFIG_VOCABULARY` configs in `vocabulary`, and `ranks` holds each config id's rank (`_config_index`), which orders the rows within a run. `kinds`, `baseline` and `new` are ids into `names`, the tuple pool `load_audit` pooled the rows' tuples through and `load_table` seals after the rows are read, so the ids name the same tuple instances the units hold. `entry` is an id into `table`, the string table the workload table shares, for the row's matched ledger class, so a unit's class id and its rows' entry ids are in one vocabulary.

    A row takes about seventeen bytes here. A whole row is rebuilt on demand: `line` returns the audit's line for the row without its newline, byte for byte, which is what the unit content key hashes, and `view` returns the shape the ink-signature key reads. The ink-duplicate merge appends a merged run for a survivor (`merge_runs`) instead of editing in place, so the two runs it replaces remain as `orphaned` rows. The debug tally's reading of the columns (`build.row_columns_census`) counts their bytes but reports `live` as its count: the number of audit rows, each belonging to one unit. So a tally line shows the same row count the manifest states. The tally is not imported here: the verdict chain reaches this module through `status`, and a telemetry module in the chain's closure would re-run the chain for an edit that cannot change a verdict.
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

EXEMPLAR = 1
NO_VERDICT = 2
LIVE = 4


class Compaction(NamedTuple):
    """What `UnitTable.compact` returns. `survivor` maps every pre-merge row to its survivor's post-merge row: its own new row if it stayed live, its survivor's if the merge removed it. `folded` has a byte per pre-merge row that is 1 for a removed row."""

    survivor: array
    folded: bytearray


class UnitTable:
    """The workload as columns over the unit's ordinal. The build's parent holds this from the load to the cache write instead of a list of `Unit` records, in the style of `unit_store.UnitStore`. The loader allocates one row per unit, and each field is a fixed-width `array`. Every name is an id into one `columns.StringTable`, the instance the row columns and the unit store share, so a class from the audit, a family from a worker and an echo id from a reduce are in one vocabulary. Each tuple or mapping field is an id into a pool, and a read returns the pooled instance, so units with equal values share one object. `configs` and `kinds` are ids into `tuples`, and `render_groups` into `groups`. `baseline` and `new` are ids into `names`, the tuple pool the row columns share, until `release_names` drops both columns and the pool. `config_classes` are ids into `mappings`, a `columns.MappingPool` keyed on each mapping's insertion order (the order the audit states the unit's configs in, which appears in the fragment's bytes); a read returns the pooled mapping typed read-only. The window is parsed once at load into `(start, count)` over a `u16` side column. The ledger's two flags and the merge's `LIVE` bit share one byte. `order` and `batch` are `u32`, with `NONE` for a unit outside the triage index. `rows_start` and `row_count` address the unit's run of the row columns. `survivor` is set by the ink-duplicate merge for a row it removes.

    The row index is the ordinal. The loader writes the rows in load order (ledger class, group, window, with the UNMATCHED units after every ledger class). The merge marks the rows it removes (`fold_into`), and `compact` drops them and renumbers the rest in place. After that every row is live, and the unit store is allocated over the same count, so one index reads both tables for the rest of the build. The manifest's triage order is a permutation over the rows (`sort_for_triage`) and does not reorder them. The machine flags and the unit id are stored only in the unit store. `unit` materializes a `Unit` from both tables, as `UnitStore.cached_unit` does for a store record, and `units` materializes the whole list for the census CLI and the tests. The debug tally measures the table through `build.unit_table_census` over `columns` and `pools`; the tally is not imported here, for the reason `RowColumns` gives.
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
        "_family",
        "_echo",
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
        self._family = array("I", [0]) * n
        self._echo = array("I", [0]) * n
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

    def family_id(self, ordinal: int) -> str:
        return self.strings[self._family[ordinal]]

    def echo(self, ordinal: int) -> str | None:
        index = self._echo[ordinal]
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
        """The row a removed unit folded into, or the row itself while it is live."""
        return ordinal if self._flags[ordinal] & LIVE else self._survivor[ordinal]

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

    # --- the writers the build's reduces go through ------------------------------------------

    def set_class(self, ordinal: int, class_id: str) -> None:
        self._class[ordinal] = self.strings.id(class_id)

    def set_family(self, ordinal: int, family_id: str) -> None:
        self._family[ordinal] = self.strings.id(family_id)

    def set_echo(self, ordinal: int, echo: str | None) -> None:
        self._echo[ordinal] = self.strings.optional(echo)

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

    def fold_into(self, ordinal: int, survivor: int) -> None:
        """Mark the row as removed by the ink-duplicate merge into `survivor`; `compact` drops it."""
        if not self._flags[survivor] & LIVE:
            raise ValueError(f"row {ordinal} folds into row {survivor}, which is not live")
        self._flags[ordinal] &= ~LIVE
        self._survivor[ordinal] = survivor

    def compact(self) -> Compaction:
        """Drop every row the merge removed, renumber the rest in place, and return the map from each pre-merge row to its survivor's post-merge row. The window values, the pools and the string table are not changed, and a row's `(start, count)` moves with the row, so a snapshot taken before the merge that copied the window offsets still reads the same values through them."""
        flags = self._flags
        n = self.n
        remap = array("I", [NONE]) * n
        folded = bytearray(n)
        kept = array("I")
        for ordinal in range(n):
            if flags[ordinal] & LIVE:
                remap[ordinal] = len(kept)
                kept.append(ordinal)
            else:
                folded[ordinal] = 1
        for ordinal in range(n):
            if folded[ordinal]:
                remap[ordinal] = remap[self._survivor[ordinal]]
        if len(kept) != n:
            self._class = array("I", map(self._class.__getitem__, kept))
            self._group = array("I", map(self._group.__getitem__, kept))
            self._family = array("I", map(self._family.__getitem__, kept))
            self._echo = array("I", map(self._echo.__getitem__, kept))
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
        return Compaction(remap, folded)

    def release_names(self) -> None:
        """Drop the `baseline` and `new` columns and their name pool; afterward `baseline` and `new` return the empty tuple. Nothing in the parent reads a name tuple after phase 1: each worker read them from the copy it was sent, and the verification sample holds its own copies."""
        self._baseline = self._new = None
        self.names = None

    # --- whole-table readings --------------------------------------------------------------------

    def family_counts(self) -> dict[str, int]:
        counts: dict[int, int] = {}
        for index in self._family:
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
        yield self._family
        yield self._echo
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
        """Materialize the unit at `ordinal` as a `Unit`. With a store, the input key comes from the store, and the id and the three machine flags come from it too once the store has folded the unit; everything else comes from this table. Each `phase1` message sends a worker one batch of these, the write materializes one at a time, and the parent never holds a list of them."""
        flags = self._flags[ordinal]
        unit_id = ""
        input_key = ""
        ink_identical = picture_identical = junior_equivalent = False
        if store is not None:
            input_key = store.input_key_hex(ordinal)
            if store.folded(ordinal):
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
            family_id=self.family_id(ordinal),
            echo=self.echo(ordinal),
            cluster=self.cluster(ordinal),
        )

    def units(self, store: UnitStoreView | None = None) -> list[Unit]:
        """Every live row materialized, in row order, for the census CLI and the tests. The build's parent does not call this."""
        return [self.unit(ordinal, store) for ordinal in range(self.n) if self._flags[ordinal] & LIVE]


def load_table(
    rows: Iterable[AuditRow],
    ledger: list[LedgerClass],
    family_of: dict[int, str],
    names: TuplePool[str] | None = None,
) -> tuple[UnitTable, RowColumns]:
    """Dedupe the audit rows to (codepoints, baseline, new) units, and return them as a `UnitTable` in load order together with the row columns the units' runs address. Load order is ledger class, group, window, with the UNMATCHED units after every ledger class, because families are assigned only at enrichment. Once every unit has its family and id, the build orders the units for the manifest with `sort_for_triage` and assigns batches.

    A unit's matched ledger class can differ by config, most often a window matched under ss03 but UNMATCHED under the default config. Each unit keeps the full per-config class map in `config_classes`, in the order the file states the configs. Its `class_id` is the single matched class if every config matched, and UNMATCHED if any config did not, so the unmatched default behavior is what gets reviewed; the matched configs stay in `config_classes` for display. A unit whose rows match two different ledger classes raises `ValueError`.

    The rows are read once. Each row's five ids go into file-order arrays, and its unit's first-seen index into one more. A counting placement over the per-unit counts writes each row's position into its unit's run in file order. Any run whose configs are not already in rank order is then sorted by config rank, so each run is a stable sort by config rank. The columns are the file-order arrays permuted by those positions. The transient state (the file-order arrays, the position array, the map from each (window, baseline, new) id triple, packed into one integer, to its unit, the window list, and the triage sort's one integer per row in `_triage_permutation`) is freed before return. The tuple pool is sealed when the stream ends, which frees its lookup dict before the load phase goes on. Table rows are written in first-seen order and then permuted into load order.
    """
    exempt_classes = {entry.id for entry in ledger if entry.no_verdict}
    columns = RowColumns(names)
    names = columns.names
    strings = columns.table
    config_id = columns.config_id
    ranks = columns.ranks
    name_id = names.id
    entry_id = strings.id

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
    _permute(table, _triage_permutation(table, class_order, family_ranks(family_of)))
    return table, columns


def _permute(table: UnitTable, permutation: Sequence[int]) -> None:
    """Reorder every per-row column of a freshly loaded table so that row `i` becomes the row `permutation[i]` was. The window values and the pools do not move, since the rows address them by offset and id."""
    for name in (
        "_class",
        "_group",
        "_family",
        "_echo",
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
    """The sort key for the order a surface pages its human units in (the manifest's `human_unit_ids`): the class's index in the manifest's class list, the group's families by code point, the window's length and codepoints, and last the unit's id. The id breaks ties between sibling units of one window by content instead of by audit order. Every term depends only on the unit and the ledger, so the same units sort the same way on every surface. `build.check_shards` checks every manifest's index against this key."""
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
    """Return the triage order as a permutation of the table's rows. The rows are not reordered, so the row index stays the ordinal. The class term is each unit's final class (its ledger class, or the verdict family the build promoted an UNMATCHED unit to), indexed through `class_order`, which maps every class the manifest lists. The id term is the store's `id_word`, the content key's first eight bytes as an integer. It sorts like the id strings `triage_key` uses, because `unit_cache.base58_64` writes a fixed-width base58 over an ascending alphabet. The other terms are `triage_key`'s."""
    return _triage_permutation(table, class_order, family_ranks(family_of), store.id_word)


def _triage_permutation(
    table: UnitTable,
    class_order: Mapping[str, int],
    family_rank: Mapping[str, int],
    id_word: Callable[[int], int] | None = None,
) -> array:
    """Return the rows in `triage_key` order as a permutation, sorting one integer per row instead of a tuple. From the high bits down, the integer packs: the class index; the group's rank among the distinct family-rank tuples (equal tuples share a rank, so groups `triage_key` cannot tell apart stay tied); the window's length; the window's codepoints, big-endian; the id word, when given; and the row index, which makes the sort stable. Each field is wide enough for its largest value, so the packing is exact for any table. The sort holds one `int` per row, a few dozen bytes each, in the list it sorts in place, and frees it on return."""
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


def merge_ink_duplicate_units(
    table: UnitTable, rows: RowColumns, ink_sig, exempt_classes: Collection[str] = frozenset()
) -> dict:
    """Merge sibling units of one window whose placed ink is identical in both fonts under every config they cover. The (codepoints, baseline, new) dedupe key is name-grain, so a config that only renames a glyph splits one visual question into two units; for example, the old font's ss04 lookups rename word-initial ·It without changing its ink. `ink_sig(text, config)` returns the rendered-outcome identity (`InkComparator.signature`, the pair of run-order ink lists that `config_diff` reads). Units merge only when every config on both sides has the same signature, so the delta, its digest and the ink verdict are identical for the survivor and the units it absorbs.

    The survivor is the sibling with the earliest config. It gets a merged run of both units' rows (`RowColumns.merge_runs`, its own rows first at equal rank, so each row keeps its own rendered names and the content key over the run is the key over the rows), the union of the configs and kinds, and the merged `config_classes` (its own entries first). It keeps its own baseline and new name tuples for display, re-resolves its class with `load_table`'s UNMATCHED-wins rule, and collapses to a single render group, since ink identity implies render-group identity. A merge that would give one unit two different matched ledger classes is skipped, because different names can match different ledger predicates, and is counted in the returned stats. The survivor's row is rewritten in place and each absorbed row is marked with `UnitTable.fold_into`; the caller compacts the table afterward. Run before enrichment and batch assignment.
    """
    stats = {"windows_folded": 0, "units_folded": 0, "kept_split_matched_classes": 0}
    folded = 0
    for siblings in _sibling_windows(table).values():
        text = "".join(chr(value) for value in table.codepoints(siblings[0]))
        groups: dict[tuple, list[int]] = {}
        for ordinal in siblings:
            signatures = {ink_sig(text, config) for config in table.configs(ordinal)}
            if len(signatures) == 1:
                groups.setdefault(signatures.pop(), []).append(ordinal)
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=table.config_rank)
            survivor = members[0]
            merged_any = False
            for ordinal in members[1:]:
                survivor_classes = table.config_classes(survivor)
                absorbed_classes = table.config_classes(ordinal)
                matched = {cls for cls in survivor_classes.values() if cls != UNMATCHED_CLASS} | {
                    cls for cls in absorbed_classes.values() if cls != UNMATCHED_CLASS
                }
                if len(matched) > 1:
                    stats["kept_split_matched_classes"] += 1
                    continue
                count = table.row_count(survivor) + table.row_count(ordinal)
                start = rows.merge_runs(
                    table.rows_start(survivor),
                    table.row_count(survivor),
                    table.rows_start(ordinal),
                    table.row_count(ordinal),
                )
                table.set_rows(survivor, start, count)
                configs = rows.configs(start, count)
                table.set_configs(survivor, configs)
                table.set_kinds(
                    survivor, tuple(sorted(set(table.kinds(survivor)) | set(table.kinds(ordinal))))
                )
                merged_classes = {**survivor_classes, **absorbed_classes}
                table.set_config_classes(survivor, merged_classes)
                classes = set(merged_classes.values())
                class_id = UNMATCHED_CLASS if UNMATCHED_CLASS in classes else matched.pop()
                table.set_class(survivor, class_id)
                table.set_no_verdict(survivor, class_id in exempt_classes)
                table.set_render_groups(survivor, (table.configs(survivor),))
                table.set_exemplar(survivor, table.exemplar(survivor) or table.exemplar(ordinal))
                table.fold_into(ordinal, survivor)
                folded += 1
                merged_any = True
            if merged_any:
                stats["windows_folded"] += 1
    stats["units_folded"] = folded
    return stats


def release_rows(workload: Workload) -> None:
    """Drop the workload's row columns; after this, each unit's `row_count` is the only record of its rows. The build reads row fields only to dedupe, to merge ink duplicates, and to compute the unit content key and the ink-signature keys, all before the first unit is enriched, so the columns are freed here instead of being held through the units phase for a count. The string table the columns share with the unit table stays. The tuple pool the columns index is the table's `names`, which `UnitTable.release_names` frees."""
    workload.rows = None


def assign_batches(
    table: UnitTable, store: UnitStoreView, order: Sequence[int], batch_size: int = BATCH_SIZE
) -> int:
    """Set each unit's place in the manifest's triage index, walking the units in `order`. A human unit (no machine channel approves it, per the store, and its ledger class does not exempt it, per the table) gets its position among the human units as `order` and the slice of `batch_size` that position falls in as `batch`. Machine-approved and no-verdict units get None for both, since none is paged to a human. Neither value is written into a fragment: the manifest's `human_unit_ids` is the index, and a batch is a slice of it. Returns the batch count."""
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
    """What `load_workload` returns to the build: the unit table, the ledger, the audit's row count, the row columns until `release_rows` drops them, and the ledger classes the units reach. `units` materializes the table's rows into a list for the census CLI and the tests; the build does not hold that list."""

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
) -> Workload:
    names: TuplePool[str] = TuplePool()
    ledger = load_ledger(ledger_path)
    table, rows = load_table(load_audit(audit_path, names), ledger, family_of, names)
    present = table.classes_present()
    return Workload(
        table=table,
        ledger=ledger,
        row_count=len(rows),
        rows=rows,
        classes_present=[entry for entry in ledger if entry.id in present],
    )
