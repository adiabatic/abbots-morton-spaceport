"""The packed per-unit store the surface build's parent holds from the plan boundary to the cache write (issue #299, stage 2): every phase-1 product the global reduces and the store writer read, as fixed-width `array` columns over the unit's ordinal rather than as one object graph per unit.

Held as one slotted record per unit — the machine flags, the ink deltas, the digests, the seam-home projection with its name tuples, the seam rects, the spool address, the written address, the content key — that state walks at five to seven times what a packed row of the same fields takes (the stage-1 readings in `var/issue-299/stage1-cold.log`: 1,192 bytes a unit walked against 171 packed for the states alone). The difference is object overhead: a tuple header per span, a pointer per name, a dict per unit for the deltas, a string object per digest. Columns pay none of it. A `UnitStore` is allocated once with the row count and the row's fixed fields live in one `array` each, typed by width — flag bits in a byte, string ids in a `u32`, the two 32-byte keys in one `bytearray` apiece, addresses as a part id, a `u64` start and a `u32` length — and every variable-length field (the window's codepoints, the ink deltas, the after and before rows of the seam-home projection, the seams with their rects and homes) is an offset and a count into a side array, so an empty field costs its five bytes and nothing else. The mismatch lines are the one field kept as objects, in a dict keyed by ordinal: every shipping build has none (the write fails otherwise), so a column for them would be all offsets.

The ordinal is the unit's row in the workload table (`audit.UnitTable`) as it stands compacted after the ink-duplicate fold, when the store is allocated over the same count; one index reads both tables, and every reduce joins by it. The two tables share one string table, handed in as `strings`, so a class the audit states and a family a worker names are one vocabulary. The input key is written here first, by the plan's keyer loop (`set_input_key`), before any row is folded; a fold that carries a key holds it to the column rather than copying over it. The unit's id is not a column: `unit_cache.unit_id_for` spells the first 64 bits of the content key in a fixed eleven base58 symbols over an ASCII-ordered alphabet, so the id is the key's first eight bytes read big-endian (`id_word`) and the string order of ids is the integer order of those words, which is what lets the home reduce tie-break on the integer. The few readers that hold an id string (the checker's home relation, the verification sample) go through `ordinal_of`, a bisect over a sorted array of the words built once after the fold, and that build is where a repeated id is refused: two windows under one 64-bit prefix would share a fragment address, so it is a refusal rather than a merge.

Every string — a digest, a cluster, a family, a config name, a delta digest, a glyph or cell name, a seam token, a part name, a class, an echo, a policy file, a config note — is an id into one `columns.StringTable`, which interns through the `sys.intern` table `audit.load_audit` describes and assigns ids in first-seen order as the fold runs. In a pooled build that order is timing-dependent, and it never reaches an output byte, because every accessor materializes the string; nothing needs the vocabulary ahead of the fold. Id 0 is the empty string, which the accessors read as `""` where the record holds a string and as `None` where it holds an optional one.

The accessors materialize on demand, one unit at a time, exactly the shapes the build writes: `seam_home` is the `enrich.SeamHomeUnit` the home reduce compares, `seam_home_record` the `proj` dict the store line carries, `seam_rects` the `[{"pair", "before", "after"}]` list `patch_fragment` reads, `homes_record` the `[[home, suppressed]]` list, `cached_unit` the whole `unit_cache.CachedUnit` for `record_line`, and `source` the `unit_cache.PriorFragment` the fragment is read back by. JSON key and value order lives in the shipped bytes, so each of these rebuilds its dict in the order the writer reads it, and the fold refuses a projection or a record whose shape it could not rebuild byte for byte: rect dicts with keys other than `x_min`, `x_max`, `advance_total` in that order, or rows whose spans, names and seams disagree in length. The store is also the home reduce's `SeamHomeSource` (the protocol `enrich.resolve_home_assignments` takes): `windows`, `seam_count`, `projection`, `id_word`, `invisible` and `set_homes`, so the reduce skips the nine units in ten with no seam without materializing anything and writes its result straight into the seam side column.

`census` reports the store to the debug tally exactly: the walked figure is the sum of its arrays' bytes plus the string table, the packed figure the arrays alone with the table printed beside it as `pile_tally`'s line contracts, so the tally's ratio for it is the table's share of the columns, which on the corpus the build measures reads `1.00`. The layout is written so that the mapped shape the design describes (the same columns dumped to a file and reopened through `mmap`) is a dump-and-reopen of these arrays; that shape is not built, because the stage-1 readings put the columns under the cap line in the parent's heap.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left
from collections.abc import Iterable, Iterator, Sequence
from typing import NamedTuple, Protocol

from rebuild.review import enrich, unit_cache
from rebuild.review.audit import MACHINE_CHANNELS, format_codepoints
from rebuild.review.columns import StringTable
from rebuild.tools import pile_tally

KEY_BYTES = 32
ID_BYTES = 8
NO_HOME = 0xFFFFFFFF
RECT_EDGES = ("x_min", "x_max", "advance_total")

INK_IDENTICAL = 1
PICTURE_IDENTICAL = 2
JUNIOR_EQUIVALENT = 4
SERVED = 8
SLIM = 16
EXEMPLAR = 32
NO_VERDICT = 64
VERBATIM = 128

_ALPHABET_INDEX = {symbol: index for index, symbol in enumerate(unit_cache.BASE58_ALPHABET)}


class Flags(NamedTuple):
    """One unit's flag byte, unpacked. The three machine channels are every unit's, and this byte is their one home — a materialized `audit.Unit` copies them from here; `slim` is the fragment shape the build writes for it (`audit.slim_fragment` over the three channels and the ledger's exemption, the store record's `slim` for a served unit); `served`, `exemplar`, `no_verdict` and `verbatim` are the served-only bits — whether the unit was served from the previous surface, what its store record says the fragment was written with, and whether its address is the shard writer's own (`unit_cache.PriorFragment.verbatim`) — and read as False on a fresh unit."""

    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    served: bool
    slim: bool
    exemplar: bool
    no_verdict: bool
    verbatim: bool


class FreshProjection(Protocol):
    """What `fold_projection` reads off a phase-1 projection: the fields of `build._UnitProjection`, stated structurally so this module never imports the build that imports it. `ordinal` is the row the projection folds into (negative when the caller states the ordinal instead), and `part`, `start` and `length` are the fragment's spool address, an empty `part` meaning the caller states the address or the row has none."""

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
    def family(self) -> str: ...
    @property
    def pair_codepoints(self) -> tuple[int, int] | None: ...
    @property
    def seam_home(self) -> enrich.SeamHomeUnit: ...
    @property
    def seam_rects(self) -> tuple[tuple[tuple[int, int], dict, dict], ...]: ...
    @property
    def mismatches(self) -> tuple[str, ...]: ...


Address = tuple[str, int, int]


def id_word_of(unit_id: str) -> int:
    """The 64-bit word a unit id spells: the inverse of `unit_cache.unit_id_for` over the key's first eight bytes, so `ordinal_of` can bisect an id string against the word column without spelling every word. An id of any other shape, or one whose eleven symbols spell a value past 64 bits, is a `KeyError`, since no unit can carry it."""
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
    """One highlight rect's three edges, from a dict that must hold exactly those keys in `enrich._highlight`'s order: the accessor rebuilds the dict from the three integers, so any other shape could not be materialized byte for byte and is refused here rather than mis-written."""
    if tuple(edge) != RECT_EDGES:
        raise ValueError(f"a seam rect edge holds {RECT_EDGES}, not {tuple(edge)}")
    return int(edge["x_min"]), int(edge["x_max"]), int(edge["advance_total"])


class UnitStore:
    """The parent's per-unit state as columns over the ordinal (the module docstring). Allocated with the row count, over the caller's string table when one is given (the workload table's, so the two share a vocabulary) and over one of its own otherwise, and with the input-key column copied from `input_keys` when the plan restarts a store after a broken stream; filled by `fold_projection` for a fresh unit and `fold_served` for a served one, once per ordinal; written to by the reduces and the write through the `set_*` methods; read through the accessors. `index` (or the first `ordinal_of`) builds the id index and refuses a repeated id, and requires every row folded, since an unfolded row would carry an all-zero key."""

    def __init__(
        self, n: int, strings: StringTable | None = None, input_keys: bytearray | None = None
    ) -> None:
        self.n = n
        self._folded = bytearray(n)
        self._table = strings if strings is not None else StringTable()
        self._holds_table = strings is None
        self._flags = array("B", [0]) * n
        self._diffs_digest = array("I", [0]) * n
        self._cluster = array("I", [0]) * n
        self._family = array("I", [0]) * n
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
        self._echo = array("I", [0]) * n
        self._policy_file = array("I", [0]) * n
        self._config_note = array("I", [0]) * n
        self._codepoints_start = array("I", [0]) * n
        self._codepoints_n = array("B", [0]) * n
        self._deltas_start = array("I", [0]) * n
        self._deltas_n = array("B", [0]) * n
        self._after_start = array("I", [0]) * n
        self._after_seam_start = array("I", [0]) * n
        self._after_n = array("B", [0]) * n
        self._before_start = array("I", [0]) * n
        self._before_seam_start = array("I", [0]) * n
        self._before_n = array("B", [0]) * n
        self._seam_start = array("I", [0]) * n
        self._seam_n = array("B", [0]) * n
        self._codepoint_values = array("H")
        self._delta_config = array("I")
        self._delta_digest = array("I")
        self._after_spans = array("H")
        self._after_cells = array("I")
        self._after_seams = array("I")
        self._before_spans = array("H")
        self._before_glyphs = array("I")
        self._before_seams = array("I")
        self._seam_pairs = array("b")
        self._rect_edges = array("i")
        self._home = array("I")
        self._home_suppressed = array("B")
        self._served_home = array("I")
        self._served_home_suppressed = array("B")
        self._mismatches: dict[int, tuple[str, ...]] = {}
        self._id_words: array | None = None
        self._id_ordinals: array | None = None

    def __len__(self) -> int:
        return self.n

    def emptied(self) -> UnitStore:
        """A store over the same rows, string table and input keys with nothing folded: what the plan restarts with when a stream breaks after some records were folded, so the keys the keyer wrote survive and the rows the broken store vouched for do not."""
        store = UnitStore(self.n, strings=self._table, input_keys=self._input_keys)
        store._holds_table = self._holds_table
        return store

    def _begin(self, ordinal: int) -> None:
        if not 0 <= ordinal < self.n:
            raise IndexError(f"ordinal {ordinal} is outside a store of {self.n} units")
        if self._folded[ordinal]:
            raise ValueError(f"ordinal {ordinal} is folded twice")
        self._folded[ordinal] = 1
        self._id_words = self._id_ordinals = None

    def set_input_key(self, ordinal: int, input_key: str) -> None:
        """Write the unit's input key (`unit_cache.UnitKeyer.key`) into its row, ahead of any fold: the column is the key's one home, and the plan reads it back (`input_key_hex`) to name the records it wants and to materialize a unit for a worker."""
        inputs = bytes.fromhex(input_key)
        if len(inputs) != KEY_BYTES:
            raise ValueError(f"ordinal {ordinal}: an input key is {KEY_BYTES} bytes")
        self._input_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES] = inputs

    def _fold_keys(self, ordinal: int, unit_id: str, content_key: str, input_key: str) -> None:
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
                    f"ordinal {ordinal}: the fold carries input key {input_key}, not the key the plan wrote for the row"
                )
        else:
            self._input_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES] = inputs
        self._content_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES] = content

    def _fold_deltas(self, ordinal: int, deltas: Iterable[tuple[str, str]]) -> None:
        self._deltas_start[ordinal] = len(self._delta_config)
        count = 0
        for config, delta in deltas:
            self._delta_config.append(self._table.id(config))
            self._delta_digest.append(self._table.id(delta))
            count += 1
        self._deltas_n[ordinal] = count

    def _fold_row(
        self,
        ordinal: int,
        spans: Sequence[tuple[int, int]],
        names: Sequence[str],
        seams: Sequence[str],
        start_column: array,
        seam_start_column: array,
        count_column: array,
        span_column: array,
        name_column: array,
        seam_column: array,
        label: str,
    ) -> None:
        """One side of the seam-home projection: `n` spans beside `n` names beside `n - 1` seams (a seam sits between two adjacent cells or glyphs, `Enricher.enrich`), held under one count, so a row whose three lengths do not stand in that relation is refused rather than sliced short. The seams take a start of their own, since a column one short per row cannot share the names' offsets."""
        count = len(spans)
        if len(names) != count or len(seams) != max(count - 1, 0):
            raise ValueError(
                f"ordinal {ordinal}: {label} row holds {count} spans, {len(names)} names and {len(seams)} seams"
            )
        start_column[ordinal] = len(name_column)
        seam_start_column[ordinal] = len(seam_column)
        count_column[ordinal] = count
        for span in spans:
            span_column.append(span[0])
            span_column.append(span[1])
        name_column.extend(self._table.id(name) for name in names)
        seam_column.extend(self._table.id(seam) for seam in seams)

    def _fold_projection_rows(self, ordinal: int, seam_home: enrich.SeamHomeUnit) -> None:
        self._codepoints_start[ordinal] = len(self._codepoint_values)
        self._codepoints_n[ordinal] = len(seam_home.codepoint_values)
        self._codepoint_values.extend(seam_home.codepoint_values)
        if seam_home.pair is not None:
            self._cell_pair_l[ordinal], self._cell_pair_r[ordinal] = seam_home.pair
        self._fold_row(
            ordinal,
            seam_home.after_spans,
            seam_home.after_cells,
            seam_home.after_seams,
            self._after_start,
            self._after_seam_start,
            self._after_n,
            self._after_spans,
            self._after_cells,
            self._after_seams,
            "after",
        )
        self._fold_row(
            ordinal,
            seam_home.before_spans,
            seam_home.before_glyphs,
            seam_home.before_seams,
            self._before_start,
            self._before_seam_start,
            self._before_n,
            self._before_spans,
            self._before_glyphs,
            self._before_seams,
            "before",
        )

    def _fold_seams(
        self,
        ordinal: int,
        seam_pairs: Sequence[tuple[int, int]],
        rects: Sequence[tuple[Sequence[int], dict, dict]],
        served_homes: Sequence[Sequence] | None,
    ) -> None:
        """The seam side column: one count over the seam pairs, the rects and both home columns, so the fold checks that the rects name the projection's seam pairs in order (they are one row each in `Enricher.enrich`, so they agree today) and that a served record's homes are one per seam."""
        count = len(seam_pairs)
        if len(rects) != count:
            raise ValueError(f"ordinal {ordinal}: {count} seam pairs beside {len(rects)} seam rects")
        if served_homes is not None and len(served_homes) != count:
            raise ValueError(f"ordinal {ordinal}: {count} seams beside {len(served_homes)} served homes")
        self._seam_start[ordinal] = len(self._home)
        self._seam_n[ordinal] = count
        for pair, (rect_pair, before, after) in zip(seam_pairs, rects):
            if tuple(rect_pair) != tuple(pair):
                raise ValueError(f"ordinal {ordinal}: seam rect pair {rect_pair} is not seam pair {pair}")
            self._seam_pairs.append(pair[0])
            self._seam_pairs.append(pair[1])
            self._rect_edges.extend(_rect_edges(before))
            self._rect_edges.extend(_rect_edges(after))
        self._home.extend([NO_HOME] * count)
        self._home_suppressed.extend([0] * count)
        if served_homes is None:
            self._served_home.extend([0] * count)
            self._served_home_suppressed.extend([0] * count)
        else:
            for home, suppressed in served_homes:
                self._served_home.append(self._table.optional(home))
                self._served_home_suppressed.append(1 if suppressed else 0)

    def _fold_source(self, ordinal: int, address: Address | unit_cache.PriorFragment | None) -> None:
        if address is None:
            return
        if isinstance(address, unit_cache.PriorFragment):
            if address.verbatim:
                self._flags[ordinal] |= VERBATIM
            address = (address.part, address.start, address.length)
        part, start, length = address
        self._src_part[ordinal] = self._table.id(part)
        self._src_start[ordinal] = start
        self._src_len[ordinal] = length

    def fold_projection(
        self,
        projection: FreshProjection,
        *,
        no_verdict: bool,
        ordinal: int | None = None,
        address: Address | unit_cache.PriorFragment | None = None,
    ) -> int:
        """Fold one fresh unit's phase-1 projection into the row at its ordinal and answer the ordinal. The ordinal is the argument when given, else the projection's `ordinal`; the spool address is the argument when given (a `(part, start, length)` triple or the spool's own `PriorFragment`), else the projection's `part`, `start` and `length`, else nothing, which leaves `source` answering None. `no_verdict` is the ledger's exemption for the unit (the workload table's flag), which with the projection's three machine flags decides the `slim` bit — the fragment shape the drafting wrote (`audit.slim_fragment`). The seam-home projection carries the unit's ink flags too (`seam_home_projection` reads them off the unit), and one that disagrees with the projection's own is refused, since `seam_home` reads the flag column. The ink deltas' home is here: `ink_deltas` reads them back in the order they were folded, which is the order the fragment's JSON carries."""
        if ordinal is None:
            ordinal = projection.ordinal
            if ordinal < 0:
                raise ValueError(f"no ordinal for unit {projection.unit_id}")
        if address is None and projection.part:
            address = (projection.part, projection.start, projection.length)
        seam_home = projection.seam_home
        if (seam_home.ink_identical, seam_home.picture_identical) != (
            projection.ink_identical,
            projection.picture_identical,
        ):
            raise ValueError(f"ordinal {ordinal}: the seam-home projection's ink flags are not the unit's")
        self._begin(ordinal)
        self._fold_keys(ordinal, projection.unit_id, projection.content_key, projection.input_key)
        approved = projection.ink_identical or projection.picture_identical or projection.junior_equivalent
        self._flags[ordinal] = (
            (INK_IDENTICAL if projection.ink_identical else 0)
            | (PICTURE_IDENTICAL if projection.picture_identical else 0)
            | (JUNIOR_EQUIVALENT if projection.junior_equivalent else 0)
            | (SLIM if approved or no_verdict else 0)
        )
        self._diffs_digest[ordinal] = self._table.id(projection.diffs_digest)
        self._cluster[ordinal] = self._table.id(projection.cluster)
        self._family[ordinal] = self._table.id(projection.family)
        if projection.pair_codepoints is not None:
            self._pair_l[ordinal], self._pair_r[ordinal] = projection.pair_codepoints
        self._fold_deltas(ordinal, projection.ink_deltas)
        self._fold_projection_rows(ordinal, seam_home)
        self._fold_seams(ordinal, seam_home.seam_pairs, projection.seam_rects, None)
        self._fold_source(ordinal, address)
        if projection.mismatches:
            self._mismatches[ordinal] = tuple(projection.mismatches)
        return ordinal

    def fold_served(
        self,
        ordinal: int,
        cached: unit_cache.ServedUnit,
        *,
        codepoints: tuple[int, ...],
        found: unit_cache.PriorFragment | None = None,
    ) -> int:
        """Fold one served unit's store record into the row at its ordinal and answer the ordinal. `found` is the prior fragment the plan located for the record — the record's own address (`ServedUnit.located`, the default) or the one the walk found for a record without one — and is the row's source; it must carry the record's id and stamp, since the plan serves a unit only on that equality. `codepoints` is the unit's window, which the record does not carry and the workload table does; the record's class, echo, exemplar and exemption flags, homes and policy file go into the served-only columns as the record holds them, which is what `served_as_is` compares against this build's values."""
        if found is None:
            found = cached.located()
        if found is None:
            raise ValueError(f"ordinal {ordinal}: a served unit is folded with the fragment the plan located")
        if found.unit_id != cached.prior_id or found.content_key != cached.content_key:
            raise ValueError(
                f"ordinal {ordinal}: the located fragment {found.unit_id} is not the record's {cached.prior_id}"
            )
        self._begin(ordinal)
        self._fold_keys(ordinal, cached.prior_id, cached.content_key, cached.key)
        self._flags[ordinal] = (
            SERVED
            | (INK_IDENTICAL if cached.ink_identical else 0)
            | (PICTURE_IDENTICAL if cached.picture_identical else 0)
            | (JUNIOR_EQUIVALENT if cached.junior_equivalent else 0)
            | (SLIM if cached.slim else 0)
            | (EXEMPLAR if cached.exemplar else 0)
            | (NO_VERDICT if cached.no_verdict else 0)
        )
        self._diffs_digest[ordinal] = self._table.id(cached.diffs_digest)
        self._cluster[ordinal] = self._table.id(cached.cluster)
        self._family[ordinal] = self._table.id(cached.family)
        if cached.pair_codepoints is not None:
            self._pair_l[ordinal], self._pair_r[ordinal] = cached.pair_codepoints
        self._prior_class[ordinal] = self._table.id(cached.prior_class)
        self._echo[ordinal] = self._table.optional(cached.echo)
        self._policy_file[ordinal] = self._table.optional(cached.policy_file)
        self._fold_deltas(ordinal, cached.ink_deltas.items())
        self._fold_projection_rows(
            ordinal,
            enrich.SeamHomeUnit(
                unit_id=cached.prior_id,
                codepoint_values=codepoints,
                ink_identical=cached.ink_identical,
                picture_identical=cached.picture_identical,
                pair=cached.pair,
                after_spans=cached.after_spans,
                after_cells=cached.after_cells,
                after_seams=cached.after_seams,
                before_spans=cached.before_spans,
                before_glyphs=cached.before_glyphs,
                before_seams=cached.before_seams,
                seam_pairs=cached.seam_pairs,
            ),
        )
        rects = [(seam["pair"], seam["before"], seam["after"]) for seam in cached.seam_rects]
        self._fold_seams(ordinal, cached.seam_pairs, rects, cached.homes)
        self._fold_source(ordinal, found)
        if cached.mismatches:
            self._mismatches[ordinal] = tuple(cached.mismatches)
        return ordinal

    def index(self) -> tuple[array, array]:
        """The id index: the units' id words sorted, beside the ordinal each word belongs to, built once after the fold and dropped by any later fold. Building it is where a repeated id is refused, naming both windows, and where an unfolded row is caught, since its all-zero key would otherwise index as a unit."""
        if self._id_words is None or self._id_ordinals is None:
            unfolded = self._folded.find(0)
            if unfolded != -1:
                raise ValueError(f"ordinal {unfolded} was never folded")
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
        """The ordinal of the unit with this id, by bisect over the index; a `KeyError` for an id no unit carries."""
        words, ordinals = self.index()
        word = id_word_of(unit_id)
        index = bisect_left(words, word)
        if index < len(words) and words[index] == word:
            return ordinals[index]
        raise KeyError(unit_id)

    def id_word(self, ordinal: int) -> int:
        """The unit's id as the integer it spells: the content key's first eight bytes, big-endian, whose order is the string order of the ids."""
        return int.from_bytes(self._content_keys[ordinal * KEY_BYTES : ordinal * KEY_BYTES + ID_BYTES], "big")

    def unit_id(self, ordinal: int) -> str:
        return "u-" + unit_cache.base58_64(
            self._content_keys[ordinal * KEY_BYTES : ordinal * KEY_BYTES + ID_BYTES].hex()
        )

    def content_key_hex(self, ordinal: int) -> str:
        return self._content_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES].hex()

    def input_key_hex(self, ordinal: int) -> str:
        return self._input_keys[ordinal * KEY_BYTES : (ordinal + 1) * KEY_BYTES].hex()

    def folded(self, ordinal: int) -> bool:
        return bool(self._folded[ordinal])

    def flags(self, ordinal: int) -> Flags:
        bits = self._flags[ordinal]
        return Flags(
            bool(bits & INK_IDENTICAL),
            bool(bits & PICTURE_IDENTICAL),
            bool(bits & JUNIOR_EQUIVALENT),
            bool(bits & SERVED),
            bool(bits & SLIM),
            bool(bits & EXEMPLAR),
            bool(bits & NO_VERDICT),
            bool(bits & VERBATIM),
        )

    def invisible(self, ordinal: int) -> bool:
        """Whether the unit is ink- or picture-identical: a seam homed on it is suppressed."""
        return bool(self._flags[ordinal] & (INK_IDENTICAL | PICTURE_IDENTICAL))

    def machine_flags(self, ordinal: int) -> tuple[bool, bool, bool]:
        """The three machine channels in `audit.MACHINE_CHANNELS` order, what a materialized `audit.Unit` copies."""
        bits = self._flags[ordinal]
        return bool(bits & INK_IDENTICAL), bool(bits & PICTURE_IDENTICAL), bool(bits & JUNIOR_EQUIVALENT)

    def ink_identical(self, ordinal: int) -> bool:
        return bool(self._flags[ordinal] & INK_IDENTICAL)

    def machine_approved(self, ordinal: int) -> bool:
        """Whether any machine channel approves the unit (`audit.Unit.machine_approved` over the flag column)."""
        return bool(self._flags[ordinal] & (INK_IDENTICAL | PICTURE_IDENTICAL | JUNIOR_EQUIVALENT))

    def machine_channel(self, ordinal: int) -> str | None:
        """The one channel that approves the unit, in `MACHINE_CHANNELS` precedence, or None."""
        bits = self._flags[ordinal]
        for name, bit in zip(MACHINE_CHANNELS, (INK_IDENTICAL, PICTURE_IDENTICAL, JUNIOR_EQUIVALENT)):
            if bits & bit:
                return name
        return None

    def diffs_digest(self, ordinal: int) -> str:
        return self._table[self._diffs_digest[ordinal]]

    def cluster(self, ordinal: int) -> str:
        return self._table[self._cluster[ordinal]]

    def family(self, ordinal: int) -> str:
        return self._table[self._family[ordinal]]

    def pair_codepoints(self, ordinal: int) -> tuple[int, int] | None:
        left = self._pair_l[ordinal]
        return None if left < 0 else (left, self._pair_r[ordinal])

    def cell_pair(self, ordinal: int) -> tuple[int, int] | None:
        """The judged pair as after-cell indices: `SeamHomeUnit.pair`, the `pair` a store record's `proj` carries and `_served_identity` reads."""
        left = self._cell_pair_l[ordinal]
        return None if left < 0 else (left, self._cell_pair_r[ordinal])

    def served_class(self, ordinal: int) -> str | None:
        """The class the served fragment was written under, from its store record; None on a fresh unit."""
        return self._optional(self._prior_class[ordinal])

    def served_echo(self, ordinal: int) -> str | None:
        return self._optional(self._echo[ordinal])

    def policy_file(self, ordinal: int) -> str | None:
        """The rune file the unit's policy draft names: a served record's until the write records its own, which for a fragment copied as it lies is the same value."""
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
        """The unit's per-config ink deltas as a fresh dict in the folded order — the fragment's JSON order, which the shipped bytes carry."""
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
        """Where the unit's fragment is read from — the fresh spool's address for a fresh unit, the prior surface's for a served one — as the `PriorFragment` the reader holds the bytes to, stamped with the row's own id and content key; None for a row folded without an address."""
        part = self._src_part[ordinal]
        if part == 0:
            return None
        return unit_cache.PriorFragment(
            self._table[part],
            self._src_start[ordinal],
            self._src_len[ordinal],
            self.unit_id(ordinal),
            self.content_key_hex(ordinal),
            verbatim=bool(self._flags[ordinal] & VERBATIM),
        )

    def written_address(self, ordinal: int) -> Address | None:
        """This build's shard address for the unit, as the shard writer returned it (`_WrittenSurface.addresses`), or None before the write records it."""
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

    def seam_count(self, ordinal: int) -> int:
        return self._seam_n[ordinal]

    def seam_pairs(self, ordinal: int) -> tuple[tuple[int, int], ...]:
        return self._spans(self._seam_pairs, self._seam_start[ordinal], self._seam_n[ordinal])

    def seam_home(self, ordinal: int) -> enrich.SeamHomeUnit:
        """The unit's `SeamHomeUnit`, equal to the projection the fold took it from (the served path's is the record's tuples plus the unit's window and id)."""
        return self._seam_home(ordinal, self.unit_id(ordinal))

    def _seam_home(self, ordinal: int, unit_id: str) -> enrich.SeamHomeUnit:
        after_start, after_n = self._after_start[ordinal], self._after_n[ordinal]
        before_start, before_n = self._before_start[ordinal], self._before_n[ordinal]
        return enrich.SeamHomeUnit(
            unit_id=unit_id,
            codepoint_values=self.codepoints(ordinal),
            ink_identical=bool(self._flags[ordinal] & INK_IDENTICAL),
            picture_identical=bool(self._flags[ordinal] & PICTURE_IDENTICAL),
            pair=self.cell_pair(ordinal),
            after_spans=self._spans(self._after_spans, after_start, after_n),
            after_cells=self._names(self._after_cells, after_start, after_n),
            after_seams=self._names(self._after_seams, self._after_seam_start[ordinal], max(after_n - 1, 0)),
            before_spans=self._spans(self._before_spans, before_start, before_n),
            before_glyphs=self._names(self._before_glyphs, before_start, before_n),
            before_seams=self._names(
                self._before_seams, self._before_seam_start[ordinal], max(before_n - 1, 0)
            ),
            seam_pairs=self.seam_pairs(ordinal),
        )

    def seam_home_record(self, ordinal: int) -> dict:
        """The `proj` dict a store record carries, key for key and list for list, built from the columns."""
        after_start, after_n = self._after_start[ordinal], self._after_n[ordinal]
        before_start, before_n = self._before_start[ordinal], self._before_n[ordinal]
        pair = self.cell_pair(ordinal)
        return {
            "pair": list(pair) if pair else None,
            "after_spans": [list(span) for span in self._spans(self._after_spans, after_start, after_n)],
            "after_cells": list(self._names(self._after_cells, after_start, after_n)),
            "after_seams": list(
                self._names(self._after_seams, self._after_seam_start[ordinal], max(after_n - 1, 0))
            ),
            "before_spans": [list(span) for span in self._spans(self._before_spans, before_start, before_n)],
            "before_glyphs": list(self._names(self._before_glyphs, before_start, before_n)),
            "before_seams": list(
                self._names(self._before_seams, self._before_seam_start[ordinal], max(before_n - 1, 0))
            ),
        }

    def seam_rects(self, ordinal: int) -> list[dict]:
        """The unit's secondary-seam rects in the shape `patch_fragment` reads and the store persists: `_seam_records`' list of `{"pair", "before", "after"}` with each edge dict in `enrich._highlight`'s key order."""
        start, count = self._seam_start[ordinal], self._seam_n[ordinal]
        pairs = self._seam_pairs[2 * start : 2 * (start + count)]
        edges = self._rect_edges[6 * start : 6 * (start + count)]
        return [
            {
                "pair": [pairs[2 * index], pairs[2 * index + 1]],
                "before": dict(zip(RECT_EDGES, edges[6 * index : 6 * index + 3])),
                "after": dict(zip(RECT_EDGES, edges[6 * index + 3 : 6 * index + 6])),
            }
            for index in range(count)
        ]

    def set_homes(self, ordinal: int, seam_assign: Sequence[tuple[int | str | None, bool]]) -> None:
        """Record the home reduce's result for the unit: one `(home, suppressed)` per seam in the seam's order, the home as an ordinal, as an id string (resolved through the index), or None. A result of the wrong length is refused, since the column is sized by the seams."""
        start, count = self._seam_start[ordinal], self._seam_n[ordinal]
        if len(seam_assign) != count:
            raise ValueError(f"ordinal {ordinal}: {count} seams beside {len(seam_assign)} home assignments")
        for offset, (home, suppressed) in enumerate(seam_assign):
            if home is None:
                target = NO_HOME
            elif isinstance(home, str):
                target = self.ordinal_of(home)
            elif 0 <= home < self.n:
                target = home
            else:
                raise IndexError(
                    f"ordinal {ordinal}: home ordinal {home} is outside a store of {self.n} units"
                )
            self._home[start + offset] = target
            self._home_suppressed[start + offset] = 1 if suppressed else 0

    def home_ordinals(self, ordinal: int) -> tuple[tuple[int | None, bool], ...]:
        start, count = self._seam_start[ordinal], self._seam_n[ordinal]
        return tuple(
            (None if home == NO_HOME else home, bool(suppressed))
            for home, suppressed in zip(
                self._home[start : start + count], self._home_suppressed[start : start + count]
            )
        )

    def homes(self, ordinal: int) -> tuple[tuple[str | None, bool], ...]:
        """This build's home assignment for the unit, the homes as id strings: what `resolve_home_assignments` answered per unit, and empty for a unit with no seam (#278)."""
        return tuple(
            (None if home is None else self.unit_id(home), suppressed)
            for home, suppressed in self.home_ordinals(ordinal)
        )

    def homes_record(self, ordinal: int) -> list[list]:
        """This build's homes as the store records them and `patch_fragment` writes them: `_homes`' `[[home, suppressed]]` list."""
        return [[home, suppressed] for home, suppressed in self.homes(ordinal)]

    def served_homes(self, ordinal: int) -> list[list]:
        """The homes the served fragment was written with, as its store record holds them; empty for a fresh unit or a unit with no seam."""
        start, count = self._seam_start[ordinal], self._seam_n[ordinal]
        return [
            [self._optional(home), bool(suppressed)]
            for home, suppressed in zip(
                self._served_home[start : start + count], self._served_home_suppressed[start : start + count]
            )
        ]

    def served_as_is(
        self, ordinal: int, *, class_id: str, echo: str | None, exemplar: bool, no_verdict: bool
    ) -> bool:
        """Whether the served fragment's bytes on disk are already what this build writes for the unit (read off the served-only columns): the address is the shard writer's own, and the class, echo, exemplar and exemption flags and homes the record says the fragment was written with equal this build's, handed in as the workload table holds them."""
        flags = self.flags(ordinal)
        return (
            flags.served
            and flags.verbatim
            and self.served_class(ordinal) == class_id
            and self.served_echo(ordinal) == echo
            and flags.exemplar == exemplar
            and flags.no_verdict == no_verdict
            and self.served_homes(ordinal) == self.homes_record(ordinal)
        )

    def windows(self) -> Iterator[tuple[int, tuple[int, ...]]]:
        """Every unit's ordinal beside its window, for the home reduce's `by_codepoints` index."""
        for ordinal in range(self.n):
            yield ordinal, self.codepoints(ordinal)

    def projection(self, ordinal: int) -> enrich.SeamHomeUnit:
        """The unit as the home reduce compares it: `seam_home` with the id left empty, since the reduce reads no id off a projection (the ordinal and `id_word` carry identity) and spelling one is the costliest part of the build for a candidate the reduce asks for once per lookup."""
        return self._seam_home(ordinal, "")

    def cached_unit(
        self, ordinal: int, *, class_id: str, echo: str | None, exemplar: bool, no_verdict: bool
    ) -> unit_cache.CachedUnit:
        """The unit's store record for this build, field for field what `store_entries` built from the unit state and the write's maps: the keys, the flags, the deltas, the digests, the projection and the seams from the columns; the fragment shape from the flag column and the exemption; the class, the echo and the ledger's flags as the workload table holds them; the address, the homes and the policy file from what the write and the reduce set."""
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
            family=self.family(ordinal),
            pair_codepoints=self.pair_codepoints(ordinal),
            proj=self.seam_home_record(ordinal),
            seams=self.seam_rects(ordinal),
            mismatches=self.mismatches(ordinal),
            echo=echo,
            exemplar=exemplar,
            no_verdict=no_verdict,
            homes=self.homes_record(ordinal),
            policy_file=self.policy_file(ordinal),
        )

    def _columns(self) -> Iterator[array | bytearray]:
        for value in vars(self).values():
            if isinstance(value, (array, bytearray)):
                yield value

    def census(self) -> pile_tally.Measure:
        """The store's own reading for the debug tally (`pile_tally.column_census`): the rows, every array's bytes plus the mismatch lines as the packed figure, the string table beside them, and the walked figure the packed one plus the table when the table is the store's own — a store handed the workload table's names into a table that table's line already holds (`build.unit_table_census`), so its walked figure is its rows alone. The columns are the rows, so the line's ratio is the table's share of them: under a megabyte against columns of gigabytes on the corpus the build measures, where it reads `1.00`."""
        lines = sum(
            len(line.encode()) + pile_tally.OFFSET_WIDTH
            for lines in self._mismatches.values()
            for line in lines
        )
        return pile_tally.column_census(
            self.n, self._columns(), self._table, lines, holds_table=self._holds_table
        )
