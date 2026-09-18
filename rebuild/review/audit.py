"""M1-mode unit assembly for the review surface (rebuild/REVIEW-PLAN.md §1.1, §2.1): load rebuild/out/m1/divergence-audit.tsv and rebuild/m1-divergences.yaml, dedupe the audit rows to (codepoints, baseline, new) units, and order them for triage — ledger class in ledger file order, then lead-family-pair group in code-point order, then codepoints, then the unit's own id (`triage_key`) — with fixed batch slices assigned over that order (`assign_batches`). A unit's id is not assigned here: it is `unit_cache.unit_id_for` over the content key the build stamps once the unit is enriched, so it names what the reviewer judges and nothing about where the unit sits. The name-grain dedupe key can split one visual question into sibling units when a config merely relabels a glyph without moving ink; the build folds those back together with `merge_ink_duplicate_units` before enrichment and batching. The workload is held as packed columns over the unit's ordinal (`UnitTable`, following `unit_store.UnitStore`'s idiom over the same `columns` kit) rather than as a list of records: the `Unit` dataclass is the materialized per-unit record a worker, the enricher, the drafter and the fragment writer read, built one at a time by `UnitTable.unit` and never held as a list by the build's parent. The rows likewise are five id columns (`RowColumns`) rather than row objects: the parsed `AuditRow` a line yields lives only until the loader has written its ids, and what the parent holds through the load phase — the unit content key is the last reader of a row's fields — is about seventeen bytes a row."""

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
    """One parsed line of the audit, the record `load_audit` yields and the build never retains: `build_units` writes its ids into the row columns as it streams past, and every later reader of a row — the ink-signature keys, the unit content key — reads the columns."""

    config: str
    codepoints: str
    kinds: tuple[str, ...]
    matched_entry: str
    baseline: tuple[str, ...]
    new: tuple[str, ...]


class RowView(NamedTuple):
    """One row of the columns as the ink-signature key reads it (`unit_cache.UnitKeyer.signature_key`, whose parameter is any record carrying these four names, `AuditRow` included), with `row` naming the row's index in the columns so a caller can come back to it."""

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
    """Whether a unit's JSON fragment carries any machine-approval flag, in the one precedence order MACHINE_CHANNELS fixes (ink identity is tried first, picture identity only where ink identity fails, Junior equivalence only where both fail, so at most one is ever true)."""
    return any(fragment.get(channel) is True for channel in MACHINE_CHANNELS)


# What a slim fragment leaves out. A unit the build machine-approves (any of MACHINE_CHANNELS) or the ledger exempts (`no_verdict`) is never paged to a human, and the app reaches its fragment only from a show-machine fold or a deep link, where it draws the window, both fonts' cells and seams, the badge and the summary — never the explain panel's candidate table, the drafts a reviewer would act on, or the pair band. Those three fields were the bulk of every shard's bytes and largest on exactly the shards nobody opens, and the pin draft replayed a shaping per unit besides, so the build omits them outright: absent keys rather than emptied values, which is what lets the app tell a slim fragment from a full one with a blank field. `build.check_unit` holds the shape exact in both directions, `build.unit_to_json` is the one writer, and `rebuild/review/static/slim.js` is the app's reader of the same rule.
SLIM_OMITTED_KEYS = ("highlight", "explain", "drafts")


def slim_fragment(fragment) -> bool:
    """Whether a unit's JSON fragment is written slim — every machine-approved or verdict-exempt unit, and no other. Read off the flags the fragment carries rather than off which keys it lacks, so the checker can hold a fragment to the shape its flags demand; the unit-cache store record carries the same answer as its `slim` flag, since two of its inputs (picture identity and the exemption) sit outside the content key and a served fragment has to be the shape this build would write."""
    return machine_approved(fragment) or fragment.get("no_verdict") is True


# A plain dict rather than a read-only proxy: a Unit is pickled to every surface worker, and a mappingproxy cannot be. The field's `Mapping` type is what refuses an in-place write.
NO_DELTAS: Mapping[str, str] = {}


class UnitStoreView(Protocol):
    """What this module reads off the per-unit store by ordinal: the columns `UnitTable.unit` copies onto a materialized record, the id word `sort_for_triage` orders by and the machine-approval bit `assign_batches` skips on. Stated structurally so that this module never imports `unit_store`, which imports it — and so that the verdict chain, which reaches this module through the app index, reaches no telemetry through the store's own import of the debug tally (rebuild/test_plumbing_closure.py walks `if TYPE_CHECKING:` imports too). `unit_store.UnitStore` is the one implementation."""

    def input_key_hex(self, ordinal: int) -> str: ...
    def folded(self, ordinal: int) -> bool: ...
    def unit_id(self, ordinal: int) -> str: ...
    def machine_flags(self, ordinal: int) -> tuple[bool, bool, bool]: ...
    def id_word(self, ordinal: int) -> int: ...
    def machine_approved(self, ordinal: int) -> bool: ...


@dataclass(slots=True)
class Unit:
    """One (codepoints, baseline, new) triple of the audit and everything the build derives per unit, materialized from the workload table (`UnitTable.unit`) for the reader that needs a record in hand — the worker's phase 1, the enricher, the drafter, `build.unit_scaffold` at the write, the verification sample, the census CLI and the tests — and never held as a list by the build's parent, whose per-unit state is the table's columns and the unit store's. Its audit rows are a run of the workload's row columns (`RowColumns`), `row_count` rows from `rows_start`, in config order with the file's order within a config; the run is read by the ink-signature keys and by the build's content key, the last reader of a row's fields, after which `release_rows` drops the columns and `row_count` alone answers for the rows in the manifest. The count is stated by whoever builds the unit — a constructor that omits it is refused rather than read as zero, since the manifest's row totals are summed from it. `ordinal` is the unit's row in the workload table and in the build's unit store (`unit_store.UnitStore`), one index from the plan boundary on; it is -1 on a unit built outside a table. `input_key` is the unit cache's content key over the unit's inputs (`unit_cache.UnitKeyer.key`), the handle the plan serves the unit by, copied off the store's column when the record is materialized with a store in hand. `unit_id` and the three machine flags are the store's too, copied at materialization and empty or False on a unit the store has not folded; the worker's phase 1 writes them onto its own copy and hands them back on the projection. `ink_deltas` is the shared empty mapping `NO_DELTAS` on every unit the build materializes — the store carries the per-config deltas, and the drafting reads them as an argument — so nothing assigns or mutates it, and the field is typed as a read-only `Mapping` so that a write through it is a type error at the line that makes it rather than a corpus-wide value; `config_classes` is the pooled mapping the table holds for the unit, typed the same way for the same reason. `order` and `batch` are the unit's place in the manifest's triage index — its position among the human units and the batch that position falls in — and null for a unit that takes no verdict; neither is written into the unit's fragment."""

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
    """Every row the divergence audit states, in file order, one `AuditRow` at a time — a generator, so the parsed rows are never a list: `load_table` takes each one's ids as it goes and the row is garbage before the next line is split. Every label — a config name, a class id, a glyph name, a kind, a window's codepoint string — goes through `sys.intern`, the one table the whole surface build shares: the subset pack's glyph names and seam tokens (its string table, interned once as `subset_pack.SubsetPack` opens; its codepoint keys are packed integers, never strings), the unit store's records (the store parse in `unit_cache.stream_store`) and the parent's per-unit state all intern through it too, so a name the audit states and a name a worker or the cache hands back are one object rather than one per site. The three name tuples of a row — its kinds and the window's rendered names in either font — are pooled through `names`, keyed on the built tuple rather than on the raw field text, so the split strings the file states are the only thing the reader drops; a caller that hands in the pool the row columns will index (as `load_workload` does) gets rows whose tuples are the very instances the columns' ids name, and a unit's own `baseline` and `new` are those instances too."""
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
    """Synthetic LedgerClass records for the verdict families present among the UNMATCHED units, in `family_order`, counted off the table's family column. `status='unmatched'` marks them as a presentation-only grouping — no ledger predicate, the oracle stays dirty until they are adjudicated. Appended after the real ledger classes by the build so `build_m1`'s existing class loop emits a shard + manifest entry per family with no new build logic. `family_order`/`family_why` come from `rebuild.review.families`, passed in so this module stays free of the enrich/families import cycle."""
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
    """Partition a unit's configs by rendered-outcome identity — each row as its (baseline, new) cell-name identity and its config, the names being everything position-bearing the rows record, stated as the pooled tuples or as their ids. The M1 dedupe key already includes both tuples, so every real unit yields exactly one group (the documented invariant, locked in by tests); the grouping is computed rather than assumed so data whose configs render differently would surface as extra stacked groups instead of being silently collapsed."""
    groups: dict[tuple[Hashable, Hashable], list[str]] = {}
    for baseline, new, config in rows:
        groups.setdefault((baseline, new), []).append(config)
    return tuple(tuple(configs) for configs in groups.values())


_CONFIG_VOCABULARY = 256


class RowColumns:
    """Every row of the audit as five flat columns, in runs: one run per unit, the unit's rows in config order (`_config_index`) with the file's order within a config, addressed by the unit's `rows_start` and `row_count`. `config` is a byte naming one of at most `_CONFIG_VOCABULARY` configs in `vocabulary` (the vocabulary is the audit's, a config's rank is a sort key and never stored); `kinds`, `baseline` and `new` are ids into `names`, the tuple pool `load_audit` pooled the rows' tuples through and `load_table` seals once its columns are written, so the tuples the ids name are the instances the units hold; `entry` is an id into `table` for the row's matched ledger class, the string table the workload table shares, so a unit's class id and its rows' entry ids are one vocabulary. A row is about seventeen bytes here against the object graph a parsed row is, and a whole row is reconstructed on demand: `line` is the audit's own line for the row minus its newline, byte for byte, because a tab-joined split round-trips, which is what the unit content key hashes, and `view` is the shape the ink-signature key reads. The ink-duplicate fold appends a merged run for a survivor (`merge_runs`) rather than editing in place, so the two runs it replaces stay as `orphaned` rows: they cost their bytes, which the debug tally's reading of the columns charges (`build.row_columns_census`), and are outside `live`, the count that reading carries — the audit's rows, each under exactly one unit — so a tally line over the columns reads the same count the manifest states. The tally itself is not imported here: the verdict chain reaches this module through `status`, and a telemetry module in its closure would re-run the chain for an edit that cannot move a verdict."""

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
    """What `UnitTable.compact` answers: for every row the table held before the fold, the row its survivor holds after it — its own new row for a row that stayed live, its survivor's for one the fold removed — and a byte per pre-fold row saying which of the two it was."""

    survivor: array
    folded: bytearray


class UnitTable:
    """The workload as columns over the unit's ordinal, the table the build's parent holds from the load to the cache write in place of a list of `Unit` records, following `unit_store.UnitStore`'s idiom: allocated by the loader with one row per audit triple, a fixed-width `array` per field, every name an id into one `columns.StringTable` — the instance the row columns and the unit store share, so a class the audit states, a family a worker names and an echo the reduce assigns are one vocabulary — and every tuple or mapping an id into a pool of its own. `configs` and `kinds` name into `tuples`, a vocabulary of a few dozen distinct tuples over the whole audit, and `render_groups` into `groups`, a few distinct tuples of them; a read hands back the pooled instance, so two units stating the same configs hold the same tuple and the echo key hashes a tuple the interpreter has hashed before. `baseline` and `new` name into `names`, the tuple pool the row columns share, until `release_names` drops both columns and the pool once the worker has read its copies and nothing in the parent reads a name tuple again. `config_classes` names into `mappings`, a `columns.MappingPool` keyed on the mapping's own insertion order — the order the audit states the unit's configs in, which is in the shipped fragment's bytes — and a read hands back the pooled mapping typed read-only, one instance per distinct map over the corpus rather than a dict per unit. The window is parsed once at load into `(start, count)` over a `u16` side column; the ledger's two flags and the fold's `live` bit share one byte; `order` and `batch` are `u32` with `NONE` for a unit outside the triage index; `rows_start` and `row_count` address the unit's run of the row columns; `survivor` is written by the ink-duplicate fold for a row it removes.

    Row index is the ordinal. The loader writes the rows in triage load order — ledger class, group, window, the UNMATCHED units behind every ledger class — so the table stands in the order a list of units stood in; the fold marks its victims dead and `compact` drops them, renumbering the survivors in place, after which every row is live and the unit store is allocated over the same count, so one index reads both tables for the rest of the build. The triage order the manifest pages by is a permutation over the rows (`sort_for_triage`), never a reordering of them. The three machine flags and the unit's id have one home, the unit store; `unit` materializes a `Unit` off both tables for the reader that needs a record in hand, the way `UnitStore.cached_unit` materializes a store record, and `units` materializes the whole list for the census CLI and the tests. A `config_classes` write goes through the pool, which refuses a mapping an id column could not name. The debug tally prices the table exactly through `build.unit_table_census` over `columns` and `pools`; the tally is not imported here, for the reason `RowColumns` gives.
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
        """The window as the audit spells it — colon-joined uppercase hex, four digits a codepoint — which `parse_codepoints` inverts, so the string the loader parsed is the string this rebuilds."""
        return format_codepoints(self.codepoints(ordinal))

    def config_rank(self, ordinal: int) -> int:
        """`_config_index` over the unit's first config, the one it settles under, memoized per distinct config tuple."""
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
        """Mark the row removed by the ink-duplicate fold, folded into `survivor`; `compact` drops it."""
        if not self._flags[survivor] & LIVE:
            raise ValueError(f"row {ordinal} folds into row {survivor}, which is not live")
        self._flags[ordinal] &= ~LIVE
        self._survivor[ordinal] = survivor

    def compact(self) -> Compaction:
        """Drop every row the fold removed and renumber the survivors in place, answering the map from each pre-fold row to the post-fold row of its survivor. The window values, the pools and the string table are untouched — a row's `(start, count)` moves with the row — so a snapshot taken before the fold that copied a row's window offsets still reads the same values through them."""
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
        """Drop the `baseline` and `new` columns and the name pool behind them: the worker read each unit's name tuples through the copy it was handed and the verification sample holds its own copies, so past phase 1 nothing in the parent reads one, and `baseline` and `new` answer the empty tuple from here on."""
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
        """The window offsets copied and the values shared: `(start, count, values)`, for a snapshot that outlives a compaction."""
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
        """The pools a packed side column stands over, priced beside the columns: the config and kind tuples, the render groups, the class maps, and the name tuples while the table still holds them."""
        yield self.tuples
        yield self.groups
        yield self.mappings
        if self.names is not None:
            yield self.names

    # --- materialization ----------------------------------------------------------------------

    def unit(self, ordinal: int, store: UnitStoreView | None = None) -> Unit:
        """The unit as a record in hand, off this table and, when a store is given, off the store's columns too: the id and the three machine flags from the store's row when it is folded, the input key from the store's column, and everything else from here. The worker is handed one batch of these per `phase1` message, the write materializes one at a time, and the parent never holds a list of them."""
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
        """Every live row materialized, in row order — one record per unit, for the census CLI and the tests; the build's parent never asks for this."""
        return [self.unit(ordinal, store) for ordinal in range(self.n) if self._flags[ordinal] & LIVE]


def load_table(
    rows: Iterable[AuditRow],
    ledger: list[LedgerClass],
    family_of: dict[int, str],
    names: TuplePool[str] | None = None,
) -> tuple[UnitTable, RowColumns]:
    """Dedupe to (codepoints, baseline, new) units and return them as a `UnitTable` in load order — ledger class, group, codepoints, with the UNMATCHED units behind every ledger class since their families are assigned only at enrichment; the build orders them for the manifest by `sort_for_triage` once every unit has its family and its id, and assigns batches then — beside the row columns the units' runs address. A triple's matched ledger class can vary by config — most often a window already blessed under ss03 but UNMATCHED (novel) under the default config — so each unit carries the full per-config class map in `config_classes`, in the order the file states the configs, and its own `class_id` is the single matched class when the triple is everywhere-matched, or the UNMATCHED sentinel when any config leaves it unmatched (UNMATCHED-wins, so the novel default behavior is what gets adjudicated; the blessed configs ride along in `config_classes` for display). A triple resolving to two distinct *matched* classes would be a genuine classification bug and still raises. A unit's config set, its kinds, its render groups and its class map are each drawn from a vocabulary of a few dozen values over the whole audit, and its group name from a few thousand family pairs, so each is an id into a pool rather than a value built once per unit.

    The rows stream past once: each row's five ids go into flat file-order arrays and its unit's first-seen index into one more, and the grouping is a counting placement over the per-unit counts — one pass writing each row's position into its unit's run, which leaves every run in file order — followed by a rank sort of the few positions of any run the file does not already state in config order, so the runs read as a stable sort by config rank with the file's order within a config. The columns are then the file-order arrays permuted by the placed positions, and every unit's run is a slice of them, read once to derive the unit's row of the table. Nothing per triple is built but the table's row and nothing per row but an array cell: the transient is the file-order arrays, the position array, the map from triple to unit index — keyed by the row's window, baseline and new ids packed into one integer — the window vocabulary and the sort's one integer word a row (`_triage_permutation`), all gone at return. The tuple pool is sealed as the stream ends, so its lookup dict — the one term of the pool that is a dict over every distinct name tuple — dies here too rather than riding the load phase beside the table and the signature tables. The table's rows are written in first-seen order and then permuted into load order, so row index is the position a list of units would have had.
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
    """Reorder every per-row column of a freshly loaded table so row `i` becomes the row `permutation[i]` held; the window values and the pools stay where they are, since the rows address them by offset and id."""
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
    """`load_table` with its units materialized into a list, for the census CLI's re-derivations and the tests; the build loads the table."""
    table, columns = load_table(rows, ledger, family_of, names)
    return table.units(), columns


def family_ranks(family_of: Mapping[int, str]) -> dict[str, int]:
    """Each family's rank in code-point order, the order a group's two families sort by."""
    return {name: value for value, name in family_of.items()}


def triage_key(
    class_index: int,
    group: str,
    codepoint_values: tuple[int, ...],
    unit_id: str,
    family_rank: Mapping[str, int],
) -> tuple:
    """The order a surface pages its human units in — the manifest's `human_unit_ids` — as a sort key over what any reader of a unit holds: the class's index in the manifest's class list, the group's families in code-point order, the window's length and codepoints, and last the unit's own id, which breaks the tie between sibling units of one window (different name tuples, different ink) on content rather than on the order the audit happened to state them in. Every term is a function of the unit and the ledger, so the order is the same on every surface the same units appear on, and `build.check_shards` holds every manifest's index to it."""
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
    """The triage order as a permutation of the table's rows — the ordinals in `triage_key` order over each unit's final class (the ledger class, or the verdict family the build promoted an UNMATCHED unit to), with `class_order` mapping every class the manifest lists to its index — and never a reordering of the rows, so row index stays the ordinal. The id term is the store's `id_word`, the content key's first eight bytes as an integer, whose order is the string order of the ids `triage_key` names (`unit_store.UnitStore.unit_id` spells a fixed-width base58 over an ascending alphabet); every other term is `triage_key`'s, with the class and group terms resolved once per distinct id rather than once per unit."""
    return _triage_permutation(table, class_order, family_ranks(family_of), store.id_word)


def _triage_permutation(
    table: UnitTable,
    class_order: Mapping[str, int],
    family_rank: Mapping[str, int],
    id_word: Callable[[int], int] | None = None,
) -> array:
    """The rows in `triage_key` order, as a permutation, sorted on one integer word a row rather than a tuple of its terms: the class index, the group's rank among the distinct groups sorted by their family-rank tuples (equal tuples take one rank, so two groups `triage_key` cannot tell apart stay tied), the window's length, the window's codepoints big-endian, the id word when the caller has one, and the row itself last, so the sort is stable and never compares beyond the word. Each field is as wide as its largest value and no wider, so the word is exact for any class count, group count and window length the table holds. What the sort holds is that one `int` per row, a few dozen bytes, beside the list it sorts in place — no tuple, no window tuple built from the side column, no separate list of boxed ordinals — and all of it is gone when the permutation returns."""
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
    """Every window two or more live units share, as the window's packed values against those units' ordinals in row order. The one dict over every window holds an int per window rather than a list, so the transient is the windows' keys alone; the sibling lists exist only for the windows that have them."""
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
    """One `RowView` per signature `merge_ink_duplicate_units` will ask for: every config of every sibling in a multi-sibling window, as the row that pins that (window, config)'s rendered names in both fonts, in the order `_sibling_windows` states the windows and each window its siblings. Iterable as often as a caller needs — the views are built on each pass rather than held, one in hand at a time — over the sibling ordinals gathered once. Sharing `_sibling_windows` with the merge is what keeps this enumeration exact: a signature provider built over these rows can never be asked for a pair outside them."""

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
    """Fold sibling units of the same window whose placed ink is identical in both fonts across every config they cover. The (codepoints, baseline, new) dedupe key is name-grain, so a config that merely relabels a glyph — the old font's ss04 lookups rename word-initial ·It without changing its ink — splits one visual question into two units and asks it twice. `ink_sig(text, config)` supplies the rendered-outcome identity (see InkComparator.signature, which is the pair of run-order ink lists `config_diff` itself consumes); units are only folded when every config on both sides yields the same signature, so a fold leaves every downstream reading of the ink — the delta, its digest, the ink verdict — identical between the survivor and what it absorbed, by definition rather than by resemblance. The survivor is the sibling with the earliest config; it takes a merged run of its rows and the absorbed unit's in the columns (`RowColumns.merge_runs`, its own rows ahead at equal rank, so each row keeps its own rendered names and the content key over the run stays the key over the rows), and absorbs the other's configs, kinds, and config_classes — the merged map re-pooled, the survivor's entries ahead of the absorbed unit's — keeps its own (earliest-config) baseline/new name tuples for display, re-resolves its class with the same UNMATCHED-wins rule as `load_table`, and collapses to a single render group (ink identity is exactly render-group identity). A fold that would put two distinct matched ledger classes on one unit is skipped — different names legitimately hit different ledger predicates — and counted in the returned stats. Rewrites the survivor's row in place and marks each absorbed unit's row removed (`UnitTable.fold_into`); the caller compacts the table afterward. Run before enrichment and batch assignment."""
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
    """Drop the workload's row columns, leaving each unit's `row_count` to answer for its rows. The rows exist to be deduped into units, folded by the ink-duplicate merge, and read into the unit content key and the ink-signature keys, all of which the build does before its first unit is enriched; past that point nothing reads a row's fields, only how many there were, so the columns and their class table go here rather than riding the units phase and the shard write for the sake of a count. The tuple pool the columns index is the table's `names` and goes with `UnitTable.release_names`."""
    workload.rows = None


def assign_batches(
    table: UnitTable, store: UnitStoreView, order: Sequence[int], batch_size: int = BATCH_SIZE
) -> int:
    """The manifest's triage index over the units as `order` states them: every human unit — one no machine channel approves (the store's flags) and no ledger class exempts (the table's) — takes its position among the human units as `order` and the fixed slice of `batch_size` that position falls in as `batch`, while machine-approved units (ink-identical, picture-identical, or junior-equivalent) and units of no-verdict ledger classes carry None for both, since none is ever paged to a human. Neither value is written into a fragment: the manifest's `human_unit_ids` is the index, and a batch is a partition of it. Returns the batch count."""
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
    """The batch a triage-index position falls in, or None for a unit outside the index — the one rule every reader of a surface's index derives a batch by, so the manifest's `human_unit_ids` and `batch_size` are all a batch number ever comes from."""
    return None if order is None else order // batch_size


@dataclass
class Workload:
    """What `load_workload` hands the build: the unit table, the ledger, the audit's row count, the row columns until `release_rows` drops them, and the ledger classes the units reach. `units` materializes the table's rows into a list for a caller that wants records in hand — the census CLI, the tests — and is never what the build holds."""

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
