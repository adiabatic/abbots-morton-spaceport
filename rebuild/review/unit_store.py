"""Packed per-unit state that the corpus build's parent holds from the plan boundary to the cache write. Every phase-1 product that the whole-corpus passes and the store writer read is kept in fixed-width `array` columns indexed by the unit's ordinal, instead of as objects per unit.

Columns avoid per-object overhead: a tuple header per span, a pointer per name, a dict per unit for the deltas, a string object per digest. The same state held as one slotted record per unit measured 1,192 bytes a unit against 171 packed (the `units states` tally line in `var/issue-299/stage1-cold.log`). A `UnitStore` is allocated once with the row count. Each fixed-width field is one `array` typed by width: flag bits in a byte, string ids as `u32`, the content and input keys as 32-byte slices of one `bytearray` each, and an address as a part id, a `u64` start and a `u32` length. Each variable-length field (the window's codepoints, the ink deltas, the after and before rows of the primary-unit projection, the secondary junctions with their rects and primary units) is an offset and a count into a side array, so an empty field costs only its offsets and count. The mismatch lines are kept as objects in a dict keyed by ordinal. A shipping build has none, because the write fails when any exist, so a column for them would hold only empty offsets.

The ordinal is the unit's row in the workload table (`audit.UnitTable`) after the ink-duplicate merge compacts it, and the store is allocated over the same count, so one index reads both tables. The two tables share one string table, passed in as `strings`. The plan's keyer loop writes every input key (`set_input_key`) before any row is loaded, and a load that carries an input key must match the one already written. The unit id is not stored. `unit_cache.unit_id_for` writes the first 64 bits of the content key as eleven base58 symbols over an ASCII-ordered alphabet, so the id is the key's first eight bytes read big-endian (`id_word`), and ids sort as strings in the same order as those integers. The primary-unit resolution pass breaks ties on the integer. Readers that hold an id string (the checker's cached-id lookup, the verification sample, `set_primary_units` given a string) go through `ordinal_of`, a bisect over the sorted words, built once after the load. That build fails on a repeated id, because two windows with one 64-bit prefix would share a fragment address.

Every string (a digest, a cluster, an unmatched group, a config name, a glyph or cell name, a junction token, a part name, a class, a duplicate-group id, a policy file, a config note) is an id into one `columns.StringTable`, which interns through `sys.intern` and assigns ids in first-seen order as the load runs. In a pooled build that order depends on timing, but no output byte depends on it, because every accessor returns the string. Id 0 is the empty string, which the accessors read as `""` for a required field and as `None` for an optional one.

The accessors build one unit's values on demand, in the shapes the build writes: `primary_unit_projection` is the `enrich.PrimaryUnitProjection` the primary-unit resolution pass compares, `primary_unit_projection_record` the `proj` dict of a store record, `junction_rects` the `[{"pair", "before", "after"}]` list `patch_fragment` reads, `primary_units_record` the `[[primary_unit, suppressed]]` list, `cached_unit` the `unit_cache.CachedUnit` for `record_line`, and `source` the `unit_cache.PriorFragment` the fragment is read back through. JSON key order is part of the shipped bytes, so each accessor builds its dicts in the order the writer reads them, and the load raises on input it could not rebuild byte for byte: rect dicts whose keys are not `x_min`, `x_max`, `advance_total` in that order, and rows whose spans, names and junctions disagree in length. The store is also the primary-unit resolution pass's `enrich.PrimaryUnitSource` (`windows`, `junction_count`, `projection`, `id_word`, `invisible` and `set_primary_units`), so `enrich.resolve_primary_unit_assignments` skips units with no secondary junction without building anything and writes its result straight into the junction side column.

`sizes` reports the store to the debug tally (`memory_tally.column_sizes`). The packed figure is the arrays' bytes plus the mismatch lines, with the string table printed beside it. The walked figure adds the table only when the store owns it; a store built over the workload table's strings reports its rows alone, because the workload table's tally line already counts the table.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left
from collections.abc import Iterable, Iterator, Sequence
from typing import NamedTuple, Protocol

from rebuild.review import enrich, unit_cache
from rebuild.review.audit import MACHINE_CHECKS, format_codepoints
from rebuild.review.columns import StringTable
from rebuild.tools import memory_tally

KEY_BYTES = 32
ID_BYTES = 8
NO_PRIMARY_UNIT = 0xFFFFFFFF
RECT_EDGES = ("x_min", "x_max", "advance_total")

INK_IDENTICAL = 1
PICTURE_IDENTICAL = 2
JUNIOR_EQUIVALENT = 4
CACHED = 8
SLIM = 16
EXEMPLAR = 32
NO_VERDICT = 64
BYTE_COPIED = 128

_ALPHABET_INDEX = {symbol: index for index, symbol in enumerate(unit_cache.BASE58_ALPHABET)}

# The columns the corpus write (`build._write_class`) never reads: the primary-unit projection and the store's copy of the window, which the whole-corpus passes before the write read; the diff digest, cluster, unmatched group and judged pair, which the plan, those passes and the cache write read; the shard address and config note, which the write produces and the parent stores; and the id index. `build._dump_columns` leaves them out of the copy each write worker loads.
WRITE_UNREAD_COLUMNS = (
    "_diffs_digest",
    "_cluster",
    "_unmatched_group",
    "_pair_l",
    "_pair_r",
    "_out_part",
    "_out_start",
    "_out_len",
    "_config_note",
    "_codepoints_start",
    "_codepoints_n",
    "_codepoint_values",
    "_after_start",
    "_after_junction_start",
    "_after_n",
    "_before_start",
    "_before_junction_start",
    "_before_n",
    "_after_spans",
    "_after_cells",
    "_after_junctions",
    "_before_spans",
    "_before_glyphs",
    "_before_junctions",
    "_id_words",
    "_id_ordinals",
)


class Flags(NamedTuple):
    """One unit's flag byte, unpacked. The flag byte is the only place the three machine checks are stored; a materialized `audit.Unit` copies them from here. `slim` says the build writes the fragment in the slim shape: set when a machine check approves the unit or the ledger exempts it (`audit.slim_fragment`), and for a cached unit copied from the store record's `slim`. `cached`, `exemplar`, `no_verdict` and `byte_copied` are set only for a cached unit: whether it was taken from the previous corpus, the exemplar and exemption flags its store record says the fragment was written with, and whether its address is the shard writer's own (`unit_cache.PriorFragment.byte_copied`). They read False on a recomputed unit."""

    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    cached: bool
    slim: bool
    exemplar: bool
    no_verdict: bool
    byte_copied: bool


class RecomputedProjection(Protocol):
    """The fields `load_projection` reads from a phase-1 projection, matching `build._UnitProjection`. It is a Protocol so that this module does not import `build`, which imports this module. `ordinal` is the row to load into, negative when the caller passes the ordinal. `part`, `start` and `length` are the fragment's spool address; an empty `part` means the caller passes the address or the row has none."""

    @property
    def ordinal(self) -> int: ...
    @property
    def part(self) -> str: ...
    @property
    def start(self) -> int: ...
    @property
    def length(self) -> int: ...
    @property
    def unit_id(self) -> str: ...
    @property
    def input_key(self) -> str: ...
    @property
    def content_key(self) -> str: ...
    @property
    def ink_identical(self) -> bool: ...
    @property
    def picture_identical(self) -> bool: ...
    @property
    def junior_equivalent(self) -> bool: ...
    @property
    def ink_deltas(self) -> tuple[tuple[str, str], ...]: ...
    @property
    def diffs_digest(self) -> str: ...
    @property
    def cluster(self) -> str: ...
    @property
    def unmatched_group(self) -> str: ...
    @property
    def pair_codepoints(self) -> tuple[int, int] | None: ...
    @property
    def primary_unit_projection(self) -> enrich.PrimaryUnitProjection: ...
    @property
    def junction_rects(self) -> tuple[tuple[tuple[int, int], dict, dict], ...]: ...
    @property
    def mismatches(self) -> tuple[str, ...]: ...


Address = tuple[str, int, int]


def id_word_of(unit_id: str) -> int:
    """Return the 64-bit word a unit id encodes, the inverse of `unit_cache.unit_id_for`, so `ordinal_of` can bisect the word column without encoding every word. Raises `KeyError` for an id of any other shape, or one whose eleven symbols exceed 64 bits, since no unit can have it."""
    if len(unit_id) != 2 + unit_cache.ID_SYMBOLS or not unit_id.startswith("u-"):
        raise KeyError(unit_id)
    value = 0
    for symbol in unit_id[2:]:
        index = _ALPHABET_INDEX.get(symbol)
        if index is None:
            raise KeyError(unit_id)
        value = value * 58 + index
    if value >> 64:
        raise KeyError(unit_id)
    return value


def _rect_edges(edge: dict) -> tuple[int, int, int]:
    """Return one highlight rect's three edges. The dict must hold exactly `RECT_EDGES` in `enrich._highlight`'s order, because `junction_rects` rebuilds the dict from the three integers and any other shape would not round-trip byte for byte."""
    if tuple(edge) != RECT_EDGES:
        raise ValueError(f"a junction rect edge holds {RECT_EDGES}, not {tuple(edge)}")
    return int(edge["x_min"]), int(edge["x_max"]), int(edge["advance_total"])


class UnitStore:
    """Per-unit state as columns over the ordinal (see the module docstring). `strings` is the workload table's string table, so the two share ids; without it the store owns a table. `input_keys` is an input-key column to copy, for a store restarted after a broken stream (`emptied`). Each ordinal is loaded once, by `load_projection` for a recomputed unit or `load_cached` for a cached one. The whole-corpus passes and the write store their results through the `set_*` methods. `index`, or the first `ordinal_of`, builds the id index."""

    def __init__(
        self, n: int, strings: StringTable | None = None, input_keys: bytearray | None = None
    ) -> None:
        self.n = n
        self._loaded = bytearray(n)
        self._table = strings if strings is not None else StringTable()
        self._holds_table = strings is None
        self._flags = array("B", [0]) * n
        self._diffs_digest = array("I", [0]) * n
        self._cluster = array("I", [0]) * n
        self._unmatched_group = array("I", [0]) * n
        self._pair_l = array("b", [-1]) * n
        self._pair_r = array("b", [-1]) * n
        self._cell_pair_l = array("b", [-1]) * n
        self._cell_pair_r = array("b", [-1]) * n
        self._content_keys = bytearray(n * KEY_BYTES)
        if input_keys is not None and len(input_keys) != n * KEY_BYTES:
            raise ValueError(
                f"an input-key column for {n} units is {n * KEY_BYTES} bytes, not {len(input_keys)}"
            )
        self._input_keys = bytearray(n * KEY_BYTES) if input_keys is None else bytearray(input_keys)
        self._src_part = array("I", [0]) * n
        self._src_start = array("Q", [0]) * n
        self._src_len = array("I", [0]) * n
        self._out_part = array("I", [0]) * n
        self._out_start = array("Q", [0]) * n
        self._out_len = array("I", [0]) * n
        self._prior_class = array("I", [0]) * n
        self._duplicate_group = array("I", [0]) * n
        self._policy_file = array("I", [0]) * n
        self._config_note = array("I", [0]) * n
        self._codepoints_start = array("I", [0]) * n
        self._codepoints_n = array("B", [0]) * n
        self._deltas_start = array("I", [0]) * n
        self._deltas_n = array("B", [0]) * n
        self._after_start = array("I", [0]) * n
        self._after_junction_start = array("I", [0]) * n
        self._after_n = array("B", [0]) * n
        self._before_start = array("I", [0]) * n
        self._before_junction_start = array("I", [0]) * n
        self._before_n = array("B", [0]) * n
        self._junction_start = array("I", [0]) * n
        self._junction_n = array("B", [0]) * n
        self._codepoint_values = array("H")
        self._delta_config = array("I")
        self._delta_digest = array("I")
        self._after_spans = array("H")
        self._after_cells = array("I")
        self._after_junctions = array("I")
        self._before_spans = array("H")
        self._before_glyphs = array("I")
        self._before_junctions = array("I")
        self._junction_pairs = array("b")
        self._rect_edges = array("i")
        self._primary_unit = array("I")
        self._primary_unit_suppressed = array("B")
        self._cached_primary_unit = array("I")
        self._cached_primary_unit_suppressed = array("B")
        self._mismatches: dict[int, tuple[str, ...]] = {}
        self._id_words: array | None = None
        self._id_ordinals: array | None = None

    def __len__(self) -> int:
        return self.n

    def emptied(self) -> UnitStore:
        """Return a store over the same rows, string table and input keys with nothing loaded. The plan restarts with it when a stream breaks after some records were loaded: the input keys the keyer wrote are kept, and the loaded rows are discarded."""
        store = UnitStore(self.n, strings=self._table, input_keys=self._input_keys)
        store._holds_table = self._holds_table
        return store

    def _begin(self, ordinal: int) -> None:
        if not 0 <= ordinal < self.n:
            raise IndexError(f"ordinal {ordinal} is outside a store of {self.n} units")
        if self._loaded[ordinal]:
            raise ValueError(f"ordinal {ordinal} is loaded twice")
        self._loaded[ordinal] = 1
        self._id_words = self._id_ordinals = None

    def set_input_key(self, ordinal: int, input_key: str) -> None:
        """Write the unit's input key (`unit_cache.UnitKeyer.key`) into its row before any load. The column is the only copy; the plan reads it back through `input_key_hex` to match store records and to materialize units."""
        inputs = bytes.fromhex(input_key)
        if len(inputs) != KEY_BYTES:
            raise ValueError(f"ordinal {ordinal}: an input key is {KEY_BYTES} bytes")
        self._input_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES] = inputs

    def _load_keys(self, ordinal: int, unit_id: str, content_key: str, input_key: str) -> None:
        content = bytes.fromhex(content_key)
        inputs = bytes.fromhex(input_key)
        if len(content) != KEY_BYTES or len(inputs) != KEY_BYTES:
            raise ValueError(f"ordinal {ordinal}: a content key and an input key are {KEY_BYTES} bytes each")
        if unit_cache.unit_id_for(content_key) != unit_id:
            raise ValueError(f"ordinal {ordinal}: id {unit_id} is not the id of content key {content_key}")
        held = self._input_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES]
        if any(held):
            if held != inputs:
                raise ValueError(
                    f"ordinal {ordinal}: the load carries input key {input_key}, not the key the plan wrote for the row"
                )
        else:
            self._input_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES] = inputs
        self._content_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES] = content

    def _load_deltas(self, ordinal: int, deltas: Iterable[tuple[str, str]]) -> None:
        self._deltas_start[ordinal] = len(self._delta_config)
        count = 0
        for config, delta in deltas:
            self._delta_config.append(self._table.id(config))
            self._delta_digest.append(self._table.id(delta))
            count += 1
        self._deltas_n[ordinal] = count

    def _load_row(
        self,
        ordinal: int,
        spans: Sequence[tuple[int, int]],
        names: Sequence[str],
        junctions: Sequence[str],
        start_column: array,
        junction_start_column: array,
        count_column: array,
        span_column: array,
        name_column: array,
        junction_column: array,
        label: str,
    ) -> None:
        """Load one side of the primary-unit projection: `n` spans, `n` names and `n - 1` junctions (a junction sits between two adjacent cells or glyphs; see `Enricher.enrich`) under one count. Raises when the three lengths disagree, instead of truncating. The junctions have their own start offset, because a column one entry shorter per row cannot share the names' offsets."""
        count = len(spans)
        if len(names) != count or len(junctions) != max(count - 1, 0):
            raise ValueError(
                f"ordinal {ordinal}: {label} row holds {count} spans, {len(names)} names and {len(junctions)} junctions"
            )
        start_column[ordinal] = len(name_column)
        junction_start_column[ordinal] = len(junction_column)
        count_column[ordinal] = count
        for span in spans:
            span_column.append(span[0])
            span_column.append(span[1])
        name_column.extend(self._table.id(name) for name in names)
        junction_column.extend(self._table.id(junction) for junction in junctions)

    def _load_projection_rows(
        self, ordinal: int, primary_unit_projection: enrich.PrimaryUnitProjection
    ) -> None:
        self._codepoints_start[ordinal] = len(self._codepoint_values)
        self._codepoints_n[ordinal] = len(primary_unit_projection.codepoint_values)
        self._codepoint_values.extend(primary_unit_projection.codepoint_values)
        if primary_unit_projection.pair is not None:
            self._cell_pair_l[ordinal], self._cell_pair_r[ordinal] = primary_unit_projection.pair
        self._load_row(
            ordinal,
            primary_unit_projection.after_spans,
            primary_unit_projection.after_cells,
            primary_unit_projection.after_junctions,
            self._after_start,
            self._after_junction_start,
            self._after_n,
            self._after_spans,
            self._after_cells,
            self._after_junctions,
            "after",
        )
        self._load_row(
            ordinal,
            primary_unit_projection.before_spans,
            primary_unit_projection.before_glyphs,
            primary_unit_projection.before_junctions,
            self._before_start,
            self._before_junction_start,
            self._before_n,
            self._before_spans,
            self._before_glyphs,
            self._before_junctions,
            "before",
        )

    def _load_junctions(
        self,
        ordinal: int,
        junction_pairs: Sequence[tuple[int, int]],
        rects: Sequence[tuple[Sequence[int], dict, dict]],
        cached_primary_units: Sequence[Sequence] | None,
    ) -> None:
        """Load the secondary-junction side column, where one count covers the junction pairs, the rects and both primary unit columns. Raises unless the rects name the projection's junction pairs in order (the build derives both from the unit's secondary junctions) and a cached record has one primary unit per junction."""
        count = len(junction_pairs)
        if len(rects) != count:
            raise ValueError(f"ordinal {ordinal}: {count} junction pairs beside {len(rects)} junction rects")
        if cached_primary_units is not None and len(cached_primary_units) != count:
            raise ValueError(
                f"ordinal {ordinal}: {count} junctions beside {len(cached_primary_units)} cached primary_units"
            )
        self._junction_start[ordinal] = len(self._primary_unit)
        self._junction_n[ordinal] = count
        for pair, (rect_pair, before, after) in zip(junction_pairs, rects):
            if tuple(rect_pair) != tuple(pair):
                raise ValueError(
                    f"ordinal {ordinal}: junction rect pair {rect_pair} is not junction pair {pair}"
                )
            self._junction_pairs.append(pair[0])
            self._junction_pairs.append(pair[1])
            self._rect_edges.extend(_rect_edges(before))
            self._rect_edges.extend(_rect_edges(after))
        self._primary_unit.extend([NO_PRIMARY_UNIT] * count)
        self._primary_unit_suppressed.extend([0] * count)
        if cached_primary_units is None:
            self._cached_primary_unit.extend([0] * count)
            self._cached_primary_unit_suppressed.extend([0] * count)
        else:
            for primary_unit, suppressed in cached_primary_units:
                self._cached_primary_unit.append(self._table.optional(primary_unit))
                self._cached_primary_unit_suppressed.append(1 if suppressed else 0)

    def _load_source(self, ordinal: int, address: Address | unit_cache.PriorFragment | None) -> None:
        if address is None:
            return
        if isinstance(address, unit_cache.PriorFragment):
            if address.byte_copied:
                self._flags[ordinal] |= BYTE_COPIED
            address = (address.part, address.start, address.length)
        part, start, length = address
        self._src_part[ordinal] = self._table.id(part)
        self._src_start[ordinal] = start
        self._src_len[ordinal] = length

    def load_projection(
        self,
        projection: RecomputedProjection,
        *,
        no_verdict: bool,
        ordinal: int | None = None,
        address: Address | unit_cache.PriorFragment | None = None,
    ) -> int:
        """Load one recomputed unit's phase-1 projection into its row and return the ordinal. The ordinal is the argument, else the projection's `ordinal`. The spool address is the argument (a `(part, start, length)` triple or the spool's `PriorFragment`), else the projection's `part`, `start` and `length`, else absent, in which case `source` returns None. `no_verdict` is the ledger's exemption for the unit (the workload table's flag); with the three machine flags it sets the `slim` bit, the fragment shape the drafting wrote (`audit.slim_fragment`). Raises when the primary-unit projection's ink flags disagree with the projection's, because `primary_unit_projection` reads them from the flag column. `ink_deltas` returns the deltas in the order loaded here, which is the fragment's JSON order."""
        if ordinal is None:
            ordinal = projection.ordinal
            if ordinal < 0:
                raise ValueError(f"no ordinal for unit {projection.unit_id}")
        if address is None and projection.part:
            address = (projection.part, projection.start, projection.length)
        primary_unit_projection = projection.primary_unit_projection
        if (primary_unit_projection.ink_identical, primary_unit_projection.picture_identical) != (
            projection.ink_identical,
            projection.picture_identical,
        ):
            raise ValueError(f"ordinal {ordinal}: the primary-unit projection's ink flags are not the unit's")
        self._begin(ordinal)
        self._load_keys(ordinal, projection.unit_id, projection.content_key, projection.input_key)
        approved = projection.ink_identical or projection.picture_identical or projection.junior_equivalent
        self._flags[ordinal] = (
            (INK_IDENTICAL if projection.ink_identical else 0)
            | (PICTURE_IDENTICAL if projection.picture_identical else 0)
            | (JUNIOR_EQUIVALENT if projection.junior_equivalent else 0)
            | (SLIM if approved or no_verdict else 0)
        )
        self._diffs_digest[ordinal] = self._table.id(projection.diffs_digest)
        self._cluster[ordinal] = self._table.id(projection.cluster)
        self._unmatched_group[ordinal] = self._table.id(projection.unmatched_group)
        if projection.pair_codepoints is not None:
            self._pair_l[ordinal], self._pair_r[ordinal] = projection.pair_codepoints
        self._load_deltas(ordinal, projection.ink_deltas)
        self._load_projection_rows(ordinal, primary_unit_projection)
        self._load_junctions(ordinal, primary_unit_projection.junction_pairs, projection.junction_rects, None)
        self._load_source(ordinal, address)
        if projection.mismatches:
            self._mismatches[ordinal] = tuple(projection.mismatches)
        return ordinal

    def load_cached(
        self,
        ordinal: int,
        cached: unit_cache.ParsedCachedUnit,
        *,
        codepoints: tuple[int, ...],
        found: unit_cache.PriorFragment | None = None,
    ) -> int:
        """Load one cached unit's store record into its row and return the ordinal. `found` is the prior fragment the plan located for the record, by default the record's own address (`ParsedCachedUnit.located`), and becomes the row's source. It must carry the record's id and content key, because the plan reuses a unit only when they match. `codepoints` is the unit's window, which the workload table has and the record does not. The record's class, duplicate group, exemplar and exemption flags, primary units and policy file are stored as the record holds them, and `cached_as_is` compares them with this build's values."""
        if found is None:
            found = cached.located()
        if found is None:
            raise ValueError(f"ordinal {ordinal}: a cached unit is loaded with the fragment the plan located")
        if found.unit_id != cached.prior_id or found.content_key != cached.content_key:
            raise ValueError(
                f"ordinal {ordinal}: the located fragment {found.unit_id} is not the record's {cached.prior_id}"
            )
        self._begin(ordinal)
        self._load_keys(ordinal, cached.prior_id, cached.content_key, cached.key)
        self._flags[ordinal] = (
            CACHED
            | (INK_IDENTICAL if cached.ink_identical else 0)
            | (PICTURE_IDENTICAL if cached.picture_identical else 0)
            | (JUNIOR_EQUIVALENT if cached.junior_equivalent else 0)
            | (SLIM if cached.slim else 0)
            | (EXEMPLAR if cached.exemplar else 0)
            | (NO_VERDICT if cached.no_verdict else 0)
        )
        self._diffs_digest[ordinal] = self._table.id(cached.diffs_digest)
        self._cluster[ordinal] = self._table.id(cached.cluster)
        self._unmatched_group[ordinal] = self._table.id(cached.unmatched_group)
        if cached.pair_codepoints is not None:
            self._pair_l[ordinal], self._pair_r[ordinal] = cached.pair_codepoints
        self._prior_class[ordinal] = self._table.id(cached.prior_class)
        self._duplicate_group[ordinal] = self._table.optional(cached.duplicate_group)
        self._policy_file[ordinal] = self._table.optional(cached.policy_file)
        self._load_deltas(ordinal, cached.ink_deltas.items())
        self._load_projection_rows(
            ordinal,
            enrich.PrimaryUnitProjection(
                unit_id=cached.prior_id,
                codepoint_values=codepoints,
                ink_identical=cached.ink_identical,
                picture_identical=cached.picture_identical,
                pair=cached.pair,
                after_spans=cached.after_spans,
                after_cells=cached.after_cells,
                after_junctions=cached.after_junctions,
                before_spans=cached.before_spans,
                before_glyphs=cached.before_glyphs,
                before_junctions=cached.before_junctions,
                junction_pairs=cached.junction_pairs,
            ),
        )
        rects = [
            (junction["pair"], junction["before"], junction["after"]) for junction in cached.junction_rects
        ]
        self._load_junctions(ordinal, cached.junction_pairs, rects, cached.primary_units)
        self._load_source(ordinal, found)
        if cached.mismatches:
            self._mismatches[ordinal] = tuple(cached.mismatches)
        return ordinal

    def index(self) -> tuple[array, array]:
        """Return the id index: the units' id words sorted, beside the ordinal of each word. It is built on the first call and cached. Building it fails on a repeated id, naming both windows, and on an unloaded row, whose all-zero key would otherwise be indexed as a unit."""
        if self._id_words is None or self._id_ordinals is None:
            unloaded = self._loaded.find(0)
            if unloaded != -1:
                raise ValueError(f"ordinal {unloaded} was never loaded")
            words = array("Q", (self.id_word(ordinal) for ordinal in range(self.n)))
            order = sorted(range(self.n), key=words.__getitem__)
            sorted_words = array("Q", (words[ordinal] for ordinal in order))
            for index in range(1, self.n):
                if sorted_words[index] == sorted_words[index - 1]:
                    first, second = order[index - 1], order[index]
                    raise SystemExit(
                        f"two units share the id {self.unit_id(first)}: "
                        f"{format_codepoints(self.codepoints(first))} and "
                        f"{format_codepoints(self.codepoints(second))}; "
                        "their carry projections hash to one 64-bit prefix"
                    )
            self._id_words = sorted_words
            self._id_ordinals = array("I", order)
        return self._id_words, self._id_ordinals

    def ordinal_of(self, unit_id: str) -> int:
        """Return the ordinal of the unit with this id by bisecting the index. Raises `KeyError` for an id no unit has."""
        words, ordinals = self.index()
        word = id_word_of(unit_id)
        index = bisect_left(words, word)
        if index < len(words) and words[index] == word:
            return ordinals[index]
        raise KeyError(unit_id)

    def id_word(self, ordinal: int) -> int:
        """Return the unit's id as an integer: the content key's first eight bytes, big-endian. Integer order matches the ids' string order."""
        return int.from_bytes(self._content_keys[ordinal * KEY_BYTES : ordinal * KEY_BYTES + ID_BYTES], "big")

    def unit_id(self, ordinal: int) -> str:
        return "u-" + unit_cache.base58_64(
            self._content_keys[ordinal * KEY_BYTES : ordinal * KEY_BYTES + ID_BYTES].hex()
        )

    def content_key_hex(self, ordinal: int) -> str:
        return self._content_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES].hex()

    def input_key_hex(self, ordinal: int) -> str:
        return self._input_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES].hex()

    def loaded(self, ordinal: int) -> bool:
        return bool(self._loaded[ordinal])

    def flags(self, ordinal: int) -> Flags:
        bits = self._flags[ordinal]
        return Flags(
            bool(bits & INK_IDENTICAL),
            bool(bits & PICTURE_IDENTICAL),
            bool(bits & JUNIOR_EQUIVALENT),
            bool(bits & CACHED),
            bool(bits & SLIM),
            bool(bits & EXEMPLAR),
            bool(bits & NO_VERDICT),
            bool(bits & BYTE_COPIED),
        )

    def invisible(self, ordinal: int) -> bool:
        """Whether the unit is ink- or picture-identical: a junction with this unit as its primary unit is suppressed."""
        return bool(self._flags[ordinal] & (INK_IDENTICAL | PICTURE_IDENTICAL))

    def machine_flags(self, ordinal: int) -> tuple[bool, bool, bool]:
        """Return the three machine checks in `audit.MACHINE_CHECKS` order, as a materialized `audit.Unit` copies them."""
        bits = self._flags[ordinal]
        return bool(bits & INK_IDENTICAL), bool(bits & PICTURE_IDENTICAL), bool(bits & JUNIOR_EQUIVALENT)

    def ink_identical(self, ordinal: int) -> bool:
        return bool(self._flags[ordinal] & INK_IDENTICAL)

    def machine_approved(self, ordinal: int) -> bool:
        """Whether any machine check approves the unit (`audit.Unit.machine_approved`, read from the flag column)."""
        return bool(self._flags[ordinal] & (INK_IDENTICAL | PICTURE_IDENTICAL | JUNIOR_EQUIVALENT))

    def machine_check(self, ordinal: int) -> str | None:
        """Return the first check in `MACHINE_CHECKS` order that approves the unit, or None."""
        bits = self._flags[ordinal]
        for name, bit in zip(MACHINE_CHECKS, (INK_IDENTICAL, PICTURE_IDENTICAL, JUNIOR_EQUIVALENT)):
            if bits & bit:
                return name
        return None

    def diffs_digest(self, ordinal: int) -> str:
        return self._table[self._diffs_digest[ordinal]]

    def cluster(self, ordinal: int) -> str:
        return self._table[self._cluster[ordinal]]

    def unmatched_group(self, ordinal: int) -> str:
        return self._table[self._unmatched_group[ordinal]]

    def pair_codepoints(self, ordinal: int) -> tuple[int, int] | None:
        left = self._pair_l[ordinal]
        return None if left < 0 else (left, self._pair_r[ordinal])

    def cell_pair(self, ordinal: int) -> tuple[int, int] | None:
        """Return the judged pair as after-cell indices, or None: `PrimaryUnitProjection.pair`, and the `pair` in a store record's `proj`, which `_cached_identity` reads."""
        left = self._cell_pair_l[ordinal]
        return None if left < 0 else (left, self._cell_pair_r[ordinal])

    def cached_class(self, ordinal: int) -> str | None:
        """Return the class the cached fragment was written under, from its store record; None for a recomputed unit."""
        return self._optional(self._prior_class[ordinal])

    def cached_duplicate_group(self, ordinal: int) -> str | None:
        return self._optional(self._duplicate_group[ordinal])

    def policy_file(self, ordinal: int) -> str | None:
        """Return the rune file the unit's policy draft names. For a cached unit it is the store record's value until the write sets this build's, which is the same for a fragment copied unchanged."""
        return self._optional(self._policy_file[ordinal])

    def set_policy_file(self, ordinal: int, value: str | None) -> None:
        self._policy_file[ordinal] = self._table.optional(value)

    def config_note(self, ordinal: int) -> str | None:
        return self._optional(self._config_note[ordinal])

    def set_config_note(self, ordinal: int, value: str | None) -> None:
        self._config_note[ordinal] = self._table.optional(value)

    def _optional(self, index: int) -> str | None:
        return None if index == 0 else self._table[index]

    def ink_deltas(self, ordinal: int) -> dict[str, str]:
        """Return the unit's per-config ink deltas as a new dict in the loaded order, which is the fragment's JSON order."""
        start = self._deltas_start[ordinal]
        stop = start + self._deltas_n[ordinal]
        table = self._table
        return {
            table[config]: table[delta]
            for config, delta in zip(self._delta_config[start:stop], self._delta_digest[start:stop])
        }

    def codepoints(self, ordinal: int) -> tuple[int, ...]:
        start = self._codepoints_start[ordinal]
        return tuple(self._codepoint_values[start : start + self._codepoints_n[ordinal]])

    def mismatches(self, ordinal: int) -> list[str]:
        return list(self._mismatches.get(ordinal, ()))

    def source(self, ordinal: int) -> unit_cache.PriorFragment | None:
        """Return where the unit's fragment is read from (the recomputed spool's address for a recomputed unit, the prior corpus's for a cached one) as a `PriorFragment` stamped with the row's id and content key. None for a row loaded without an address."""
        part = self._src_part[ordinal]
        if part == 0:
            return None
        return unit_cache.PriorFragment(
            self._table[part],
            self._src_start[ordinal],
            self._src_len[ordinal],
            self.unit_id(ordinal),
            self.content_key_hex(ordinal),
            byte_copied=bool(self._flags[ordinal] & BYTE_COPIED),
        )

    def written_address(self, ordinal: int) -> Address | None:
        """Return this build's shard address for the unit, as the write recorded it through `set_written_address`, or None before then."""
        part = self._out_part[ordinal]
        if part == 0:
            return None
        return (self._table[part], self._out_start[ordinal], self._out_len[ordinal])

    def set_written_address(self, ordinal: int, address: Address) -> None:
        part, start, length = address
        self._out_part[ordinal] = self._table.id(part)
        self._out_start[ordinal] = start
        self._out_len[ordinal] = length

    def _spans(self, column: array, start: int, count: int) -> tuple[tuple[int, int], ...]:
        flat = column[2 * start : 2 * (start + count)]
        return tuple((flat[index], flat[index + 1]) for index in range(0, 2 * count, 2))

    def _names(self, column: array, start: int, count: int) -> tuple[str, ...]:
        table = self._table
        return tuple(table[index] for index in column[start : start + count])

    def junction_count(self, ordinal: int) -> int:
        return self._junction_n[ordinal]

    def junction_pairs(self, ordinal: int) -> tuple[tuple[int, int], ...]:
        return self._spans(self._junction_pairs, self._junction_start[ordinal], self._junction_n[ordinal])

    def primary_unit_projection(self, ordinal: int) -> enrich.PrimaryUnitProjection:
        """Return the unit's `PrimaryUnitProjection`, equal to the one loaded (for a cached unit, the record's tuples plus the unit's window and id)."""
        return self._primary_unit_projection(ordinal, self.unit_id(ordinal))

    def _primary_unit_projection(self, ordinal: int, unit_id: str) -> enrich.PrimaryUnitProjection:
        after_start, after_n = self._after_start[ordinal], self._after_n[ordinal]
        before_start, before_n = self._before_start[ordinal], self._before_n[ordinal]
        return enrich.PrimaryUnitProjection(
            unit_id=unit_id,
            codepoint_values=self.codepoints(ordinal),
            ink_identical=bool(self._flags[ordinal] & INK_IDENTICAL),
            picture_identical=bool(self._flags[ordinal] & PICTURE_IDENTICAL),
            pair=self.cell_pair(ordinal),
            after_spans=self._spans(self._after_spans, after_start, after_n),
            after_cells=self._names(self._after_cells, after_start, after_n),
            after_junctions=self._names(
                self._after_junctions, self._after_junction_start[ordinal], max(after_n - 1, 0)
            ),
            before_spans=self._spans(self._before_spans, before_start, before_n),
            before_glyphs=self._names(self._before_glyphs, before_start, before_n),
            before_junctions=self._names(
                self._before_junctions, self._before_junction_start[ordinal], max(before_n - 1, 0)
            ),
            junction_pairs=self.junction_pairs(ordinal),
        )

    def primary_unit_projection_record(self, ordinal: int) -> dict:
        """Return the `proj` dict of a store record, key for key and list for list, built from the columns."""
        after_start, after_n = self._after_start[ordinal], self._after_n[ordinal]
        before_start, before_n = self._before_start[ordinal], self._before_n[ordinal]
        pair = self.cell_pair(ordinal)
        return {
            "pair": list(pair) if pair else None,
            "after_spans": [list(span) for span in self._spans(self._after_spans, after_start, after_n)],
            "after_cells": list(self._names(self._after_cells, after_start, after_n)),
            "after_junctions": list(
                self._names(self._after_junctions, self._after_junction_start[ordinal], max(after_n - 1, 0))
            ),
            "before_spans": [list(span) for span in self._spans(self._before_spans, before_start, before_n)],
            "before_glyphs": list(self._names(self._before_glyphs, before_start, before_n)),
            "before_junctions": list(
                self._names(
                    self._before_junctions, self._before_junction_start[ordinal], max(before_n - 1, 0)
                )
            ),
        }

    def junction_rects(self, ordinal: int) -> list[dict]:
        """Return the unit's secondary-junction rects in the shape `patch_fragment` reads and the store record keeps: the `{"pair", "before", "after"}` list `build._junction_records` writes, with each edge dict in `enrich._highlight`'s key order."""
        start, count = self._junction_start[ordinal], self._junction_n[ordinal]
        pairs = self._junction_pairs[2 * start : 2 * (start + count)]
        edges = self._rect_edges[6 * start : 6 * (start + count)]
        return [
            {
                "pair": [pairs[2 * index], pairs[2 * index + 1]],
                "before": dict(zip(RECT_EDGES, edges[6 * index : 6 * index + 3])),
                "after": dict(zip(RECT_EDGES, edges[6 * index + 3 : 6 * index + 6])),
            }
            for index in range(count)
        ]

    def set_primary_units(
        self, ordinal: int, junction_assign: Sequence[tuple[int | str | None, bool]]
    ) -> None:
        """Record the primary-unit resolution pass's result for the unit: one `(primary_unit, suppressed)` per junction, in junction order. A primary unit is an ordinal, an id string (resolved through the index), or None. Raises when the length differs from the junction count, which sizes the column."""
        start, count = self._junction_start[ordinal], self._junction_n[ordinal]
        if len(junction_assign) != count:
            raise ValueError(
                f"ordinal {ordinal}: {count} junctions beside {len(junction_assign)} primary-unit assignments"
            )
        for offset, (primary_unit, suppressed) in enumerate(junction_assign):
            if primary_unit is None:
                target = NO_PRIMARY_UNIT
            elif isinstance(primary_unit, str):
                target = self.ordinal_of(primary_unit)
            elif 0 <= primary_unit < self.n:
                target = primary_unit
            else:
                raise IndexError(
                    f"ordinal {ordinal}: primary-unit ordinal {primary_unit} is outside a store of {self.n} units"
                )
            self._primary_unit[start + offset] = target
            self._primary_unit_suppressed[start + offset] = 1 if suppressed else 0

    def primary_unit_ids(self) -> frozenset[str]:
        """Return the id of every unit an unsuppressed secondary junction names as its primary unit. Those are the junctions the written fragments carry, so these are the units whose identity the corpus check's primary-unit predicates read beside the units that name them."""
        return frozenset(
            self.unit_id(target)
            for target, suppressed in zip(self._primary_unit, self._primary_unit_suppressed)
            if target != NO_PRIMARY_UNIT and not suppressed
        )

    def primary_unit_ordinals(self, ordinal: int) -> tuple[tuple[int | None, bool], ...]:
        start, count = self._junction_start[ordinal], self._junction_n[ordinal]
        return tuple(
            (None if primary_unit == NO_PRIMARY_UNIT else primary_unit, bool(suppressed))
            for primary_unit, suppressed in zip(
                self._primary_unit[start : start + count],
                self._primary_unit_suppressed[start : start + count],
            )
        )

    def primary_units(self, ordinal: int) -> tuple[tuple[str | None, bool], ...]:
        """Return this build's primary unit assignments for the unit, with primary units as id strings; empty for a unit with no secondary junction."""
        return tuple(
            (None if primary_unit is None else self.unit_id(primary_unit), suppressed)
            for primary_unit, suppressed in self.primary_unit_ordinals(ordinal)
        )

    def primary_units_record(self, ordinal: int) -> list[list]:
        """Return this build's primary units as the `primary_units` field of a store record: a `[[primary_unit, suppressed]]` list."""
        return [[primary_unit, suppressed] for primary_unit, suppressed in self.primary_units(ordinal)]

    def cached_primary_units(self, ordinal: int) -> list[list]:
        """Return the primary units the cached fragment was written with, as its store record holds them; empty for a recomputed unit or a unit with no secondary junction."""
        start, count = self._junction_start[ordinal], self._junction_n[ordinal]
        return [
            [self._optional(primary_unit), bool(suppressed)]
            for primary_unit, suppressed in zip(
                self._cached_primary_unit[start : start + count],
                self._cached_primary_unit_suppressed[start : start + count],
            )
        ]

    def cached_as_is(
        self, ordinal: int, *, class_id: str, duplicate_group: str | None, exemplar: bool, no_verdict: bool
    ) -> bool:
        """Whether the cached fragment's bytes on disk already equal what this build writes for the unit. That holds when its address is the shard writer's own and the class, duplicate group, exemplar and exemption flags and primary units in its store record equal this build's values, which the caller passes from the workload table."""
        flags = self.flags(ordinal)
        return (
            flags.cached
            and flags.byte_copied
            and self.cached_class(ordinal) == class_id
            and self.cached_duplicate_group(ordinal) == duplicate_group
            and flags.exemplar == exemplar
            and flags.no_verdict == no_verdict
            and self.cached_primary_units(ordinal) == self.primary_units_record(ordinal)
        )

    def windows(self) -> Iterator[tuple[int, tuple[int, ...]]]:
        """Yield every unit's ordinal and window, for the primary-unit resolution pass's `by_codepoints` index."""
        for ordinal in range(self.n):
            yield ordinal, self.codepoints(ordinal)

    def projection(self, ordinal: int) -> enrich.PrimaryUnitProjection:
        """Return the unit as the primary-unit resolution pass compares it: `primary_unit_projection` with an empty id. The pass reads identity from the ordinal and `id_word`, so encoding an id for every candidate it looks up would be wasted work."""
        return self._primary_unit_projection(ordinal, "")

    def cached_unit(
        self, ordinal: int, *, class_id: str, duplicate_group: str | None, exemplar: bool, no_verdict: bool
    ) -> unit_cache.CachedUnit:
        """Return the unit's store record for this build. The keys, flags, deltas, digests, projection and junctions come from the columns, and `slim` from the flag column and `no_verdict`. The class, duplicate group, exemplar and exemption are the workload table's values, passed by the caller. The address, primary units and policy file are what the write and the primary-unit resolution pass set."""
        flags = self.flags(ordinal)
        return unit_cache.CachedUnit(
            key=self.input_key_hex(ordinal),
            prior_id=self.unit_id(ordinal),
            prior_class=class_id,
            content_key=self.content_key_hex(ordinal),
            slim=flags.ink_identical or flags.picture_identical or flags.junior_equivalent or no_verdict,
            address=self.written_address(ordinal),
            ink_identical=flags.ink_identical,
            picture_identical=flags.picture_identical,
            junior_equivalent=flags.junior_equivalent,
            ink_deltas=self.ink_deltas(ordinal),
            diffs_digest=self.diffs_digest(ordinal),
            cluster=self.cluster(ordinal),
            unmatched_group=self.unmatched_group(ordinal),
            pair_codepoints=self.pair_codepoints(ordinal),
            proj=self.primary_unit_projection_record(ordinal),
            junctions=self.junction_rects(ordinal),
            mismatches=self.mismatches(ordinal),
            duplicate_group=duplicate_group,
            exemplar=exemplar,
            no_verdict=no_verdict,
            primary_units=self.primary_units_record(ordinal),
            policy_file=self.policy_file(ordinal),
        )

    def _columns(self) -> Iterator[array | bytearray]:
        for value in vars(self).values():
            if isinstance(value, (array, bytearray)):
                yield value

    def sizes(self) -> memory_tally.Measure:
        """Return the store's reading for the debug tally (`memory_tally.column_sizes`). The packed figure is every array's bytes plus the mismatch lines, and the string table is printed beside it. The walked figure adds the table only when the store owns it. A store built over the workload table's strings reports its rows alone, because `build.unit_table_sizes` counts that table."""
        lines = sum(
            len(line.encode()) + memory_tally.OFFSET_WIDTH
            for lines in self._mismatches.values()
            for line in lines
        )
        return memory_tally.column_sizes(
            self.n, self._columns(), self._table, lines, holds_table=self._holds_table
        )
