"""A debug tally of the piles a surface build holds, read at its phase boundaries, so a peak the journal records can be attributed to the pile that made it (issue #156). Off unless the caller's environment carries `AMS_SURFACE_PILE_TALLY=1` (`TALLY_ENV`), which the artifact cycle never sets and which reaches the build child through the environment it inherits; with the variable unset `from_environment` answers None and the build's call sites are a handful of `if tally:` misses, so the shards and every other line the build prints are exactly what they were.

The figures are attribution, never precision. Each pile is a `sys.getsizeof` walk over a bounded, evenly spaced sample of its members (`SAMPLE_SIZE` of them), scaled by the member count and added to the container's own size — seconds over a corpus of a million units, where a walk over every member would be minutes. The walk descends into the stdlib containers, into `__dict__` and every slot, and stops at strings, bytes and numbers; an object seen once in a sample is counted once, so members that share a pooled tuple or an interned name are charged for one copy between them, which is the same discount the process gets. A pile can name leaf types the walk counts shallow rather than entering, which is how one pile is kept from subsuming another it holds pointers into — a pile of records that each point at a member of a pile reported on its own line would otherwise outrank that pile by construction. A pile whose members are themselves tables — the enricher's subset rows, one whole table per configuration — is held `nested`, so each sampled member is estimated by the same bounded sample rather than walked whole, its count is the members' rows summed, and a boundary stays seconds-cheap over tables of a million rows apiece. A pile held (`hold`) is re-estimated at every boundary after it, since the build mutates and drains what it holds, and a pile the build empties reads as empty at the boundaries past that point rather than dropping off the record — the audit's row columns once the content keys have read them, the fresh spool's addresses once the runner has closed; one read (`hold_reading`) is a callable answering (count, bytes) itself, or a `Measure`, for an instrument like `ink.shape_memo_census` that already keeps its own exact census, or `column_census` over a pile the build keeps as packed columns (the unit store's own reading, and `build.row_columns_census` over the audit's row columns), or for a pile the build holds only through what else it holds. The roster is the build's own: `build_m1`, `_FreshRunner.hold_piles`, `_write_surface` and `_surface_worker` in `rebuild/review/build.py` name what they hold, and what the fresh units leave in any process past their batch is the spool address per unit — a column of the parent's packed unit store (`unit_store`, which reports its own exact census), a field of the projections a pooled worker holds until it has answered with them (`worker.projections`) — never a pile of enrichments: a fresh unit's fragment goes to the spool as it is drafted.

A pile can also declare the packed shape of one member (`hold(..., packed=)`, or a `Measure` from `measure(..., packed=)` under `hold_reading`), and its line then carries what a packed row of the same fields would take beside what the walk found (issue #299): the shape is a tree of the small declarations below, one per field of the record, and `packed_estimate` prices it over the same evenly spaced sample the walk reads, so the ratio compares like with like. `Slot(width)` is a fixed-width column whatever the value holds — a flag byte, an ordinal, an integer of a stated width, the two cell indices of a pair; `Flag()` is one bit of a flag byte; `Id()` is an `ID_WIDTH` id into one string table, the string itself charged to the table and never to the row; `Hex(width)` is a hex digest as its raw bytes, `HEX_WIDTH` of them for the sha256 the build keys by, charged whether the value is there or not, since a fixed-width column has no holes and a member missing its digest costs the slot like any other; a present digest whose length disagrees with the declared width raises, which is the cross-check that keeps a column declared for one digest from quietly pricing another. `Derived(bytes, absent)` is whatever the callable answers for the value, for a column whose width follows the value, like a window of two to four codepoints inline; an absent value costs `absent`, the `COUNT_WIDTH` count slot such a column writes for an empty one, rather than reaching the callable. `Many(element)` is a variable-length field as an `OFFSET_WIDTH` offset and a `COUNT_WIDTH` count into a side column of its elements, each priced by the element shape, so a field that is empty on most members costs the pair and nothing else; `Table(key, value)` is the same over a mapping's entries. `Record(fields)` prices an object's named attributes, `Keyed(fields)` a dict's named keys, and `Positional(shapes)` a tuple's slots in order; an absent value (None) still costs every slot under it, since a column has no holes, but puts nothing in the string table. A shape over a mapping pile prices the values, the key being the ordinal a packed store indexes by, unless the shape is a `Table`, which then prices the pile's own entries as key beside value. The string table is the one term a sample cannot stand for — a vocabulary saturates rather than scaling with the count — so it is counted over every member of the pile, and a boundary's cost is that walk: one pass over all of them per id-bearing column, with the bare `Id` fields of one record read together by a multi-argument getter so a record's dozen names cost one pass rather than a dozen, every pass through `map`, `filter` and `chain` alone so it runs at C speed, and the distinct strings held in one set for the width of it. A pile of a million members with five id columns is therefore five million reads and a set of the vocabulary, seconds and a few megabytes, against the seconds the sampled walk takes — the price of an exact table, paid at every boundary the pile is held at. Each distinct string is charged once at its UTF-8 length plus an `OFFSET_WIDTH` offset, and the table is reported beside the row bytes rather than folded into them, since one table would serve every pile at once. Absent and empty strings alike put nothing in the table.

The format, one line per pile per boundary and sorted largest first within a boundary, then one line naming the largest — every line prefixed `[tally] ` (`TALLY`) and whitespace-tokenized, so a `grep '^\\[tally\\]'` over the cycle's per-step log (`var/build-logs/<run>/<nn>-surface-build.log`, where the build's stdout lands) reads the whole record back:

    [tally] <boundary> <pile> count=<n> est_bytes=<n> est_gb=<x.xx>
    [tally] <boundary> <pile> count=<n> est_bytes=<n> est_gb=<x.xx> packed_bytes=<n> packed_per=<x.x> walked_per=<x.x> ratio=<x.xx|-> strings=<n> string_bytes=<n>
    [tally] <boundary> largest=<pile>

The first is a pile with no packed shape declared; the second a pile with one, where `packed_bytes` is the packed rows over the whole count, `packed_per` and `walked_per` are bytes per member for the packed row and the walk, `ratio` is `walked_per` over `packed_per` (`-` when either is zero, which is what an empty pile prints), and `strings` and `string_bytes` are the pile's string table, distinct strings and their bytes. The ratio is the rows alone: the table is not in `packed_bytes` or in either per-member figure, because one table serves every pile at once and folding it into each would charge the same strings several times over. It is printed beside the ratio so a reader can add it back where it matters — on the corpus the build measures it is under a megabyte against piles of gigabytes, which is why the headline figure is the rows'. `<boundary>` is the build phase whose end the reading was taken at (`load`, `plan`, `units`, `manifest+check`, `census-facts`, `cache`), or `w<i>/<phase>` for a pooled worker's own piles at the end of one of its phases; neither carries whitespace. `est_gb` is `peak_rss`'s decimal gigabyte, the unit every peak in the journal is stated in, so a pile's figure reads beside the `rss_gb=` token on the same phase's `[t]` line without conversion. A boundary with nothing held prints only the `largest=` line, with `-` for the name, so the boundary is still on the record.

Stdlib-only, like `peak_rss`: the build imports this beside its other cost readings, and rebuild/test_review_code_closure.py pins it among the width and telemetry modules that may not move a byte of a unit's products.
"""

from __future__ import annotations

import os
import sys
from array import array
from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain, islice
from operator import attrgetter, itemgetter, methodcaller
from typing import IO, Any, Protocol

TALLY_ENV = "AMS_SURFACE_PILE_TALLY"
TALLY = "[tally] "
SAMPLE_SIZE = 256

ID_WIDTH = 4
OFFSET_WIDTH = 4
COUNT_WIDTH = 1
HEX_WIDTH = 32
_FLAG_BYTES = 1 / 8

_BYTES_PER_GB = 1e9
_LEAVES = (str, bytes, bytearray, int, float, complex, bool, type(None))
_SEQUENCES = (list, tuple, set, frozenset, deque)


def enabled(environ: Mapping[str, str] = os.environ) -> bool:
    return environ.get(TALLY_ENV) == "1"


def deep_size(root: object, seen: set[int], leaf_types: tuple[type, ...] = ()) -> int:
    """The bytes reachable from `root` that `seen` has not already charged: the container sizes, the slots and instance dicts, and the leaves under them. `seen` is the caller's, shared across one sample so a shared object is charged once per pile rather than once per member."""
    total = 0
    stack: list[object] = [root]
    while stack:
        item = stack.pop()
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        total += sys.getsizeof(item)
        if isinstance(item, _LEAVES) or (leaf_types and isinstance(item, leaf_types)):
            continue
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, _SEQUENCES):
            stack.extend(item)
        else:
            instance_dict = getattr(item, "__dict__", None)
            if instance_dict is not None:
                stack.append(instance_dict)
            for cls in type(item).__mro__:
                slots = cls.__dict__.get("__slots__", ())
                for name in (slots,) if isinstance(slots, str) else slots:
                    if name in ("__dict__", "__weakref__"):
                        continue
                    try:
                        stack.append(getattr(item, name))
                    except AttributeError:
                        pass
    return total


def _sample(pile: Collection, size: int) -> list:
    members: Iterable = pile.items() if isinstance(pile, Mapping) else pile
    step = max(1, len(pile) // size)
    return list(islice(members, 0, None, step))[:size]


def _is_table(value: object) -> bool:
    return isinstance(value, Collection) and not isinstance(value, _LEAVES)


def estimate(
    pile: Collection,
    *,
    leaf_types: tuple[type, ...] = (),
    sample_size: int = SAMPLE_SIZE,
    nested: bool = False,
) -> tuple[int, int]:
    """(count, estimated bytes) for one pile: the container's own size plus the deep size of an evenly spaced sample of its members, scaled by the count. A mapping's sample is its items, so keys and values are both charged. `nested` reads a pile of tables: each sampled member that is a collection is estimated by its own bounded sample instead of walked in full, and the count answered is the members' lengths summed, so the figure is the rows the pile holds rather than the tables."""
    members = len(pile)
    total = sys.getsizeof(pile)
    if members == 0:
        return 0, total
    sample = _sample(pile, sample_size)
    seen: set[int] = set()
    sampled = 0
    for member in sample:
        key, value = member if isinstance(pile, Mapping) else (None, member)
        if key is not None:
            sampled += deep_size(key, seen, leaf_types)
        if nested and _is_table(value):
            sampled += estimate(value, leaf_types=leaf_types, sample_size=sample_size)[1]
        else:
            sampled += deep_size(value, seen, leaf_types)
    count = members
    if nested:
        values: Iterable = pile.values() if isinstance(pile, Mapping) else pile
        count = sum(len(value) if _is_table(value) else 1 for value in values)
    return count, total + round(sampled * members / len(sample))


class Shape:
    """One node of a packed-row declaration; the module docstring lists the shapes and what each prices."""


@dataclass(frozen=True)
class Slot(Shape):
    width: int


@dataclass(frozen=True)
class Flag(Shape):
    pass


@dataclass(frozen=True)
class Id(Shape):
    pass


@dataclass(frozen=True)
class Hex(Shape):
    width: int = HEX_WIDTH


@dataclass(frozen=True)
class Derived(Shape):
    bytes: Callable[[Any], float]
    absent: float = COUNT_WIDTH


@dataclass(frozen=True)
class Many(Shape):
    element: Shape


@dataclass(frozen=True)
class Table(Shape):
    key: Shape
    value: Shape


@dataclass(frozen=True)
class Record(Shape):
    fields: Mapping[str, Shape]


@dataclass(frozen=True)
class Keyed(Shape):
    fields: Mapping[str, Shape]


@dataclass(frozen=True)
class Positional(Shape):
    shapes: tuple[Shape, ...]


def packed_size(shape: Shape, value: object) -> float:
    """The bytes a packed row holds for `value` under `shape` — the row's own columns and its share of every side column, never the string table. A value the shape cannot read (a name that is not a string under `Id`, a tuple of the wrong length under `Positional`) raises, so a declaration that has drifted from its record goes red in the tests that build with the tally on rather than pricing the wrong field quietly."""
    if isinstance(shape, Slot):
        return shape.width
    if isinstance(shape, Flag):
        return _FLAG_BYTES
    if isinstance(shape, Id):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"an Id column holds a string or None, not {type(value).__name__}")
        return ID_WIDTH
    if isinstance(shape, Hex):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"a Hex column holds a hex digest or None, not {type(value).__name__}")
        if value and len(value) != 2 * shape.width:
            raise ValueError(f"a Hex column of {shape.width} bytes holds {len(value)} hex characters")
        return shape.width
    if isinstance(shape, Derived):
        return shape.absent if value is None else shape.bytes(value)
    if isinstance(shape, Many):
        if value is None:
            return OFFSET_WIDTH + COUNT_WIDTH
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
            raise TypeError(f"a Many column holds a sequence, not {type(value).__name__}")
        return OFFSET_WIDTH + COUNT_WIDTH + sum(packed_size(shape.element, member) for member in value)
    if isinstance(shape, Table):
        if value is None:
            return OFFSET_WIDTH + COUNT_WIDTH
        if not isinstance(value, Mapping):
            raise TypeError(f"a Table column holds a mapping, not {type(value).__name__}")
        return (
            OFFSET_WIDTH
            + COUNT_WIDTH
            + sum(
                packed_size(shape.key, key) + packed_size(shape.value, member)
                for key, member in value.items()
            )
        )
    if isinstance(shape, Record):
        return sum(
            packed_size(field, None if value is None else getattr(value, name))
            for name, field in shape.fields.items()
        )
    if isinstance(shape, Keyed):
        if value is not None and not isinstance(value, Mapping):
            raise TypeError(f"a Keyed column holds a mapping, not {type(value).__name__}")
        return sum(
            packed_size(field, None if value is None else value[name]) for name, field in shape.fields.items()
        )
    if isinstance(shape, Positional):
        if value is None:
            return sum(packed_size(field, None) for field in shape.shapes)
        if not isinstance(value, Sequence) or len(value) != len(shape.shapes):
            raise ValueError(f"a Positional column of {len(shape.shapes)} slots holds {value!r}")
        return sum(packed_size(field, member) for field, member in zip(shape.shapes, value))
    raise TypeError(f"not a packed shape: {shape!r}")


_Column = Callable[[Iterable], Iterable]


def _string_columns(shape: Shape, take: _Column) -> list[_Column]:
    """The column readers for the `Id`s under `shape`: each a callable that turns an iterable of values shaped like `shape` into the strings its id columns hold, built from `map`, `filter` and `chain` alone so the pass over a corpus runs at C speed. There is one reader per id column, except that the bare `Id` members of one record or tuple share a reader (`_fold_members`), since one getter reads them all off the member at once. `take` is the path from the pile's members down to this shape's values; an absent value along the path (`filter(None, ...)`) contributes nothing."""
    if isinstance(shape, Id):
        return [lambda values: filter(None, take(values))]
    if isinstance(shape, Many):
        return _string_columns(shape.element, lambda values: chain.from_iterable(filter(None, take(values))))
    if isinstance(shape, Table):
        keys: _Column = lambda values: chain.from_iterable(
            map(methodcaller("keys"), filter(None, take(values)))
        )
        members: _Column = lambda values: chain.from_iterable(
            map(methodcaller("values"), filter(None, take(values)))
        )
        return _string_columns(shape.key, keys) + _string_columns(shape.value, members)
    if isinstance(shape, (Record, Keyed)):
        getter = attrgetter if isinstance(shape, Record) else itemgetter
        return _fold_members(list(shape.fields.items()), getter, take)
    if isinstance(shape, Positional):
        return _fold_members(list(enumerate(shape.shapes)), itemgetter, take)
    return []


def _fold_members(
    members: list[tuple[Any, Shape]], getter: Callable[..., Any], take: _Column
) -> list[_Column]:
    """The columns under one record's or tuple's members, with the members that are bare `Id`s read together by a multi-argument getter so their strings come off one pass rather than one pass apiece — the saving the module docstring prices, and the reason a record of a dozen names costs the same walk as a record of one."""
    plain = [key for key, field in members if isinstance(field, Id)]
    columns: list[_Column] = []
    if len(plain) == 1:
        columns.append(
            lambda values, get=getter(plain[0]): filter(None, map(get, filter(None, take(values))))
        )
    elif plain:
        columns.append(
            lambda values, get=getter(*plain): filter(
                None, chain.from_iterable(map(get, filter(None, take(values))))
            )
        )
    for key, field in members:
        if isinstance(field, Id):
            continue
        columns += _string_columns(
            field, lambda values, get=getter(key): map(get, filter(None, take(values)))
        )
    return columns


@dataclass(frozen=True)
class PackedCost:
    est_bytes: int
    strings: int
    string_bytes: int


def packed_estimate(pile: Collection, shape: Shape, *, sample_size: int = SAMPLE_SIZE) -> PackedCost:
    """What a packed store of `pile` takes: the rows, priced over the same evenly spaced sample `estimate` walks and scaled by the count, and the string table, counted over every member (the module docstring says why the table is not sampled). Over a mapping, the shape prices the values and the key is the ordinal, unless it is a `Table`, which prices the entries."""
    members = len(pile)
    if members == 0:
        return PackedCost(0, 0, 0)
    sample = _sample(pile, sample_size)
    strings: set[str] = set()
    if isinstance(pile, Mapping) and isinstance(shape, Table):
        sampled = sum(packed_size(shape.key, key) + packed_size(shape.value, value) for key, value in sample)
        items = pile.items()
        columns = _string_columns(shape.key, lambda members: map(itemgetter(0), members)) + _string_columns(
            shape.value, lambda members: map(itemgetter(1), members)
        )
        for column in columns:
            strings.update(column(items))
    else:
        members_view: Collection = pile.values() if isinstance(pile, Mapping) else pile
        sampled = sum(
            packed_size(shape, member[1] if isinstance(pile, Mapping) else member) for member in sample
        )
        for column in _string_columns(shape, lambda members: members):
            strings.update(column(members_view))
    string_bytes = sum(
        (len(string) if string.isascii() else len(string.encode())) + OFFSET_WIDTH for string in strings
    )
    return PackedCost(round(sampled * members / len(sample)), len(strings), string_bytes)


def _refuse_nested_packed(nested: bool, packed: Shape | None) -> None:
    if nested and packed is not None:
        raise ValueError("a nested pile counts the rows of its tables, which a packed row cannot price")


@dataclass(frozen=True)
class Measure:
    count: int
    est_bytes: int
    packed: PackedCost | None = None


class Vocabulary(Protocol):
    """A string table as `column_census` prices one: the distinct strings' UTF-8 bytes summed, and their number under `len` (`columns.StringTable`)."""

    chars: int

    def __len__(self) -> int: ...


class Pool(Protocol):
    """A pool of name tuples as `pool_bytes` prices one: the distinct tuples' elements summed, and their number under `len` (`columns.TuplePool`)."""

    elements: int

    def __len__(self) -> int: ...


def bytes_of(column: array | bytearray) -> int:
    return len(column) if isinstance(column, bytearray) else len(column) * column.itemsize


def pool_bytes(pool: Pool) -> int:
    """What a packed side column over the pool takes: each distinct tuple once, at an `ID_WIDTH` id per element plus an `OFFSET_WIDTH` offset, its strings charged to the string table they intern through and never here."""
    return pool.elements * ID_WIDTH + len(pool) * OFFSET_WIDTH


def column_census(
    n: int,
    columns: Iterable[array | bytearray],
    table: Vocabulary,
    extra_bytes: int = 0,
    *,
    holds_table: bool = True,
) -> Measure:
    """The exact reading of a packed pile for the tally: `n` rows, the columns' bytes plus `extra_bytes` (a tuple pool's under `pool_bytes`, or object-held lines the pile keeps beside its arrays) as the packed figure, with the string table reported beside them as `packed_estimate` counts one — each distinct string once at its UTF-8 length plus an offset — and the walked figure the packed one plus the table, so the line's ratio is the table's share of the columns, which reads `1.00` wherever the columns are gigabytes against a table of kilobytes. A pile that indexes a table another pile's line already holds passes `holds_table=False`: its walked figure is then its rows alone, with the table still printed beside them, so one table shared by several piles (the workload table's, which the audit's row columns, the unit store and the pre-merge snapshot name into) is charged to one line and a boundary's `largest=` compares what each pile holds on its own."""
    rows = sum(bytes_of(column) for column in columns) + extra_bytes
    strings = table.chars + len(table) * OFFSET_WIDTH
    return Measure(n, rows + strings if holds_table else rows, PackedCost(rows, len(table), strings))


def measure(
    pile: Collection,
    *,
    leaf_types: tuple[type, ...] = (),
    sample_size: int = SAMPLE_SIZE,
    nested: bool = False,
    packed: Shape | None = None,
) -> Measure:
    """`estimate` and, given a shape, `packed_estimate` over one pile, as the one record a reading carries. A nested pile takes no packed shape: its count is the rows of the tables under it while the packed figure is one row per member, so a line carrying both would divide bytes by a count that does not describe them."""
    _refuse_nested_packed(nested, packed)
    count, est_bytes = estimate(pile, leaf_types=leaf_types, sample_size=sample_size, nested=nested)
    cost = None if packed is None else packed_estimate(pile, packed, sample_size=sample_size)
    return Measure(count, est_bytes, cost)


@dataclass(frozen=True)
class Reading:
    pile: str
    count: int
    est_bytes: int
    packed: PackedCost | None = None

    @property
    def line_body(self) -> str:
        body = f"{self.pile} count={self.count} est_bytes={self.est_bytes} est_gb={self.est_bytes / _BYTES_PER_GB:.2f}"
        if self.packed is None:
            return body
        packed_per = self.packed.est_bytes / self.count if self.count else 0.0
        walked_per = self.est_bytes / self.count if self.count else 0.0
        ratio = f"{walked_per / packed_per:.2f}" if packed_per and walked_per else "-"
        return (
            f"{body} packed_bytes={self.packed.est_bytes} packed_per={packed_per:.1f} walked_per={walked_per:.1f} "
            f"ratio={ratio} strings={self.packed.strings} string_bytes={self.packed.string_bytes}"
        )


class PileTally:
    """The piles one process holds, named as they come into being and read at every boundary after. `out` is resolved at write time, never bound, for the reason `rebuild.tools.console` gives: the cycle tees `sys.stdout` after the module is imported."""

    def __init__(self, *, out: IO[str] | None = None, sample_size: int = SAMPLE_SIZE) -> None:
        self._out = out
        self._sample_size = sample_size
        self._held: dict[str, tuple[Collection, tuple[type, ...], bool, Shape | None]] = {}
        self._readings: dict[str, Callable[[], tuple[int, int] | Measure]] = {}

    def hold(
        self,
        name: str,
        pile: Collection,
        *,
        leaf_types: tuple[type, ...] = (),
        nested: bool = False,
        packed: Shape | None = None,
    ) -> None:
        _refuse_nested_packed(nested, packed)
        self._readings.pop(name, None)
        self._held[name] = (pile, leaf_types, nested, packed)

    def hold_reading(self, name: str, read: Callable[[], tuple[int, int] | Measure]) -> None:
        self._held.pop(name, None)
        self._readings[name] = read

    def release(self, name: str) -> None:
        self._held.pop(name, None)
        self._readings.pop(name, None)

    def readings(self) -> list[Reading]:
        readings = []
        for name, (pile, leaf_types, nested, packed) in self._held.items():
            measured = measure(
                pile, leaf_types=leaf_types, sample_size=self._sample_size, nested=nested, packed=packed
            )
            readings.append(Reading(name, measured.count, measured.est_bytes, measured.packed))
        for name, read in self._readings.items():
            result = read()
            if isinstance(result, Measure):
                readings.append(Reading(name, result.count, result.est_bytes, result.packed))
            else:
                count, est_bytes = result
                readings.append(Reading(name, int(count), int(est_bytes)))
        return sorted(readings, key=lambda reading: (-reading.est_bytes, reading.pile))

    def boundary(self, name: str) -> list[Reading]:
        readings = self.readings()
        lines = [f"{TALLY}{name} {reading.line_body}" for reading in readings]
        lines.append(f"{TALLY}{name} largest={readings[0].pile if readings else '-'}")
        stream = sys.stdout if self._out is None else self._out
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        return readings


def from_environment(
    environ: Mapping[str, str] = os.environ, *, out: IO[str] | None = None
) -> PileTally | None:
    """The tally the build works through when `AMS_SURFACE_PILE_TALLY=1` is in its environment, and None otherwise — the None is the whole of the off switch, so a build that has one prints nothing and estimates nothing."""
    return PileTally(out=out) if enabled(environ) else None
