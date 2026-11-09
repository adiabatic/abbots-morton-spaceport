"""Interning tables for the corpus build's column-packed collections: `StringTable` for the strings every id column names, `TuplePool` for the name-tuple columns, and `MappingPool` for the per-config class maps. The module imports nothing from the repository, so `audit.RowColumns`, `audit.UnitTable`, and `unit_store.UnitStore` can all build on it, and the verdict update, which imports `audit` through `status`, does not import the memory tally through it. `memory_tally.column_sizes` estimates the memory of a collection built on these tables.

Every table assigns ids in first-seen order and reserves id 0 for the empty value. Each keeps a running total of its members' size (UTF-8 bytes for strings, elements for tuples, two ids per entry for mappings), so the tally reports the tables exactly instead of by sampling.

`gather`, `gather_slices` and `gather_runs` copy some rows of a collection's columns into columns of their own, in the order given, and `Withheld` stands in for a column such a copy leaves out. `UnitTable.gather` and `UnitStore.gather` build the corpus write's chunks from them.

The corpus build's whole-corpus passes answer their questions from columns instead of a Python object a unit: `sorted_by_word` sorts a column of 64-bit words 256 top-byte buckets at a time (the unit store's id index, the ink-signature misses), `WindowIndex` holds the units in window order beside each one's window packed into a word (the triage sort's walk and the primary-unit search's lookups), and `KeyIndex` maps a column of fixed-width keys to their ordinals in an open-addressed table (the plan's input-key lookups).
"""

from __future__ import annotations

import sys
from array import array
from bisect import bisect_left, bisect_right
from collections.abc import Hashable, Iterable, Iterator, Mapping, Sequence
from typing import Generic, TypeVar

_T = TypeVar("_T", bound=Hashable)


class StringTable:
    """Interning table for the strings an id column names, with the empty string at id 0. Each string goes through `sys.intern` before the lookup, so the table stores the one object that every interned copy of the name shares, including fresh copies from worker replies. `chars` is the distinct strings' UTF-8 bytes summed, and `len` counts the distinct strings without the empty one, as the tally does. `find` returns a string's existing id, or None, without assigning one."""

    __slots__ = ("_strings", "_ids", "chars")

    def __init__(self) -> None:
        self._strings: list[str] = [""]
        self._ids: dict[str, int] = {"": 0}
        self.chars = 0

    def id(self, value: str) -> int:
        value = sys.intern(value)
        found = self._ids.get(value)
        if found is None:
            found = len(self._strings)
            self._strings.append(value)
            self._ids[value] = found
            self.chars += len(value.encode())
        return found

    def find(self, value: str) -> int | None:
        return self._ids.get(value)

    def optional(self, value: str | None) -> int:
        return 0 if value is None else self.id(value)

    def __getitem__(self, index: int) -> str:
        return self._strings[index]

    def __len__(self) -> int:
        return len(self._strings) - 1


class TuplePool(Generic[_T]):
    """Interning pool for the tuples a column names (a row's kinds, a window's rendered names in either font, a unit's config set, its render groups), with the empty tuple at id 0. `pooled` returns the pool's own instance of a value, so records built from the pool share one tuple per distinct value; `id` is what a column stores. `elements` is the distinct tuples' lengths summed and `len` their count without the empty one, which is what `memory_tally.pool_bytes` estimates a packed side column from; the strings inside are counted against the string table, not here. The tuple-to-id dict is the pool's largest container (at the live audit's scale, a dict of millions of name tuples is tens of megabytes, several times a list of the same pointers) and is needed only to assign ids. `seal` drops it once the columns over the pool are written; after that `__getitem__` still works and `id` raises. Pools over a vocabulary of a few dozen tuples are never sealed."""

    __slots__ = ("_tuples", "_ids", "elements")

    def __init__(self) -> None:
        self._tuples: list[tuple[_T, ...]] = [()]
        self._ids: dict[tuple[_T, ...], int] | None = {(): 0}
        self.elements = 0

    def seal(self) -> None:
        self._ids = None

    @property
    def sealed(self) -> bool:
        return self._ids is None

    def id(self, value: tuple[_T, ...]) -> int:
        if self._ids is None:
            raise ValueError("the tuple pool is sealed: its columns are written and it assigns no further id")
        found = self._ids.get(value)
        if found is None:
            found = len(self._tuples)
            self._tuples.append(value)
            self._ids[value] = found
            self.elements += len(value)
        return found

    def pooled(self, value: tuple[_T, ...]) -> tuple[_T, ...]:
        return self._tuples[self.id(value)]

    def __getitem__(self, index: int) -> tuple[_T, ...]:
        return self._tuples[index]

    def __len__(self) -> int:
        return len(self._tuples) - 1


class MappingPool:
    """Interning pool for the per-config class maps (`audit.Unit.config_classes`), with the empty mapping at id 0. It is keyed on `tuple(mapping.items())` in insertion order, unsorted. That order is the order the audit lists a unit's configs, and it appears in the shipped fragment's bytes, while the content key hashes the map without regard to order; a pool keyed on sorted items would change `units/*.json` without changing an id. `pooled` returns the pool's instance typed `Mapping[str, str]`, so a write through it is a type error instead of a change every unit sharing the instance sees; `rebuild/test_unit_cache.py` checks that no build, cold or cached, writes through a pooled map. A mapping with a non-string key or value raises `TypeError` when first seen, since an id column could not name it. `elements` is twice the distinct mappings' entries summed (a key id and a value id per entry) and `len` their count without the empty one, so `memory_tally.pool_bytes` estimates the pool as it does a tuple pool."""

    __slots__ = ("_mappings", "_ids", "elements")

    def __init__(self) -> None:
        self._mappings: list[Mapping[str, str]] = [{}]
        self._ids: dict[tuple[tuple[str, str], ...], int] = {(): 0}
        self.elements = 0

    def id(self, value: Mapping[str, str]) -> int:
        key = tuple(value.items())
        found = self._ids.get(key)
        if found is None:
            if not all(isinstance(name, str) and isinstance(member, str) for name, member in key):
                raise TypeError("a mapping pool holds string keys and string values")
            found = len(self._mappings)
            self._mappings.append(dict(key))
            self._ids[key] = found
            self.elements += 2 * len(key)
        return found

    def pooled(self, value: Mapping[str, str]) -> Mapping[str, str]:
        return self._mappings[self.id(value)]

    def __getitem__(self, index: int) -> Mapping[str, str]:
        return self._mappings[index]

    def __len__(self) -> int:
        return len(self._mappings) - 1


class Withheld:
    """What a chunk of a column-packed collection holds in place of a column it was not sent. Any read raises, so a reader that starts reading such a column fails where it reads instead of reading an empty or stale one."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def _refuse(self, *_args) -> None:
        raise RuntimeError(
            f"the corpus write read {self.name}, which a write worker's chunk is not sent (`unit_store.WRITE_UNREAD_COLUMNS`, `audit.UnitTable.gather`)"
        )

    __getitem__ = __len__ = __iter__ = get = _refuse


def gather(column: array, ordinals: Sequence[int]) -> array:
    """Return the column's values at `ordinals`, in that order, as a column of the same type."""
    return array(column.typecode, map(column.__getitem__, ordinals))


def gather_slices(column: bytearray, width: int, ordinals: Sequence[int]) -> bytearray:
    """Return the `width`-byte slices a fixed-width byte column holds for `ordinals`, in that order, as a byte column of their own."""
    return bytearray().join(column[ordinal * width : (ordinal + 1) * width] for ordinal in ordinals)


def gather_runs(
    starts: array, counts: array, ordinals: Sequence[int], *sides: tuple[array, int]
) -> tuple[array, list[array]]:
    """Return the runs of side-array entries that `starts` and `counts` give `ordinals`, copied in that order into side arrays of their own, beside the starts rebased onto them. Each side array comes with its stride, the entries it holds for each counted item (two for a junction's pair, six for its rect edges), so one start and one count address every side array of a group. The counts move with their rows; `gather` copies them."""
    rebased = array(starts.typecode)
    gathered = [array(side.typecode) for side, _stride in sides]
    position = 0
    for ordinal in ordinals:
        start, count = starts[ordinal], counts[ordinal]
        rebased.append(position)
        if count:
            for (side, stride), into in zip(sides, gathered):
                into.extend(side[start * stride : (start + count) * stride])
        position += count
    return rebased, gathered


WORD_BUCKETS = 256


def sorted_by_word(words: array) -> array:
    """Return the positions of a column of 64-bit words (`array("Q")`) in ascending word order, equal words in position order, as `array("I")`: the order `sorted(range(len(words)), key=words.__getitem__)` gives. The positions are dealt into `WORD_BUCKETS` buckets by each word's top byte and each bucket is sorted on its own, so the sort holds one bucket's Python integers at a time instead of one a position; on words spread like a digest's, a bucket is a 256th of the column."""
    buckets: list[array | None] = [array("I") for _ in range(WORD_BUCKETS)]
    for position, word in enumerate(words):
        bucket = buckets[word >> 56]
        assert bucket is not None
        bucket.append(position)
    ordered = array("I")
    get = words.__getitem__
    for index, bucket in enumerate(buckets):
        assert bucket is not None
        ordered.extend(sorted(bucket, key=get) if len(bucket) > 1 else bucket)
        buckets[index] = None
    return ordered


WINDOW_SLOTS = 4


def window_word(values: Sequence[int]) -> int | None:
    """Return a window's code points packed into one 64-bit word, 16 bits a code point, right-aligned as `subset_pack.pack_key` packs a key, so words order windows as `audit.triage_key` does: by length, then by code points. None for a window the word cannot hold in that order: longer than `WINDOW_SLOTS`, leading with code point zero, or holding one above U+FFFF."""
    if not 0 < len(values) <= WINDOW_SLOTS or not values[0]:
        return None
    word = 0
    for value in values:
        if value > 0xFFFF:
            return None
        word = (word << 16) | value
    return word


class WindowIndex:
    """A table's units in window order, the order `audit.triage_key` puts windows in (length, then code points), with the units of one window in ascending ordinal order, beside each one's window as a packed word (`window_word`): 12 bytes a unit. `candidates` bisects the words for one window's units, and `runs` gives the stretches of units that share a window. When some window cannot be packed, the keys are `(length, code points)` tuples instead, one object a unit, which no corpus at the live alphabet needs. `remapped` carries the index through the ink-duplicate merge's compaction."""

    __slots__ = ("ordinals", "keys", "_windows")

    def __init__(self, ordinals: array, keys: array | list[tuple[int, tuple[int, ...]]]) -> None:
        self.ordinals = ordinals
        self.keys = keys
        self._windows: int | None = None

    @classmethod
    def build(cls, windows: Iterable[tuple[int, Sequence[int]]]) -> WindowIndex:
        """Index `(ordinal, window)` pairs given in ascending ordinal order, sorting them by window. This sort holds one Python object a unit while it runs; a source that streams its units in window order builds the index without it."""
        ordinals = array("I")
        values: list[tuple[int, ...]] = []
        for ordinal, window in windows:
            ordinals.append(ordinal)
            values.append(tuple(window))
        words = [word for word in map(window_word, values) if word is not None]
        if len(words) == len(values):
            packed = array("Q", words)
            order = sorted(range(len(packed)), key=packed.__getitem__)
            return cls(
                array("I", map(ordinals.__getitem__, order)), array("Q", map(packed.__getitem__, order))
            )
        keys = [(len(window), window) for window in values]
        order = sorted(range(len(keys)), key=keys.__getitem__)
        return cls(array("I", map(ordinals.__getitem__, order)), [keys[position] for position in order])

    @property
    def packed(self) -> bool:
        return isinstance(self.keys, array)

    def key(self, window: Sequence[int]) -> int | tuple[int, tuple[int, ...]] | None:
        if self.packed:
            return window_word(window)
        return (len(window), tuple(window))

    def candidates(self, window: Sequence[int]) -> Sequence[int]:
        """The ordinals of the units whose window is `window`, ascending."""
        key = self.key(window)
        if key is None:
            return ()
        keys = self.keys
        low = bisect_left(keys, key)  # pyright: ignore[reportArgumentType, reportCallIssue]
        high = bisect_right(keys, key, low)  # pyright: ignore[reportArgumentType, reportCallIssue]
        return self.ordinals[low:high]

    def runs(self) -> Iterator[tuple[int, int]]:
        """Yield `(start, end)`, positions into `ordinals`, of every stretch of two or more units that share a window, in window order."""
        keys = self.keys
        start = 0
        count = len(keys)
        for position in range(1, count + 1):
            if position == count or keys[position] != keys[start]:
                if position - start > 1:
                    yield start, position
                start = position

    @property
    def windows(self) -> int:
        """How many distinct windows the index holds."""
        if self._windows is None:
            keys = self.keys
            self._windows = sum(
                1 for position in range(len(keys)) if not position or keys[position] != keys[position - 1]
            )
        return self._windows

    def remapped(self, survivor: Sequence[int], removed: bytearray) -> WindowIndex:
        """The index over a table compacted with this `survivor` map and `removed` bytes (`audit.Compaction`): each kept unit at its new ordinal, in the same order. Compaction keeps the order of the rows it keeps, so a window's units stay ascending."""
        ordinals = array("I")
        keys: array | list[tuple[int, tuple[int, ...]]] = array("Q") if self.packed else []
        for position, ordinal in enumerate(self.ordinals):
            if not removed[ordinal]:
                ordinals.append(survivor[ordinal])
                keys.append(self.keys[position])  # pyright: ignore[reportArgumentType]
        return WindowIndex(ordinals, keys)

    def nbytes(self) -> int:
        return len(self.ordinals) * self.ordinals.itemsize + (
            len(self.keys) * self.keys.itemsize if isinstance(self.keys, array) else 0
        )


KEY_EMPTY = 0xFFFFFFFF


class RepeatedKey(Exception):
    """Raised by `KeyIndex` when two ordinals hold one key, naming the earlier ordinal and the later one, the first such pair in ordinal order."""

    def __init__(self, first: int, second: int) -> None:
        super().__init__(first, second)
        self.first = first
        self.second = second


class KeyIndex:
    """An open-addressed table of `u32` ordinals over a column of fixed-width keys (`width` bytes each, a multiple of eight, that of a digest), at a load below one half: four bytes a slot, so eight to sixteen bytes a key. A key's slot is its first eight bytes read as a native integer, which a digest spreads evenly, and a slot's holder is checked against the whole key in the column. Building it inserts the ordinals in ascending order and raises `RepeatedKey` at the first ordinal whose key an earlier one holds. `find` returns the ordinal holding a key, or None."""

    __slots__ = ("_column", "_words", "_table", "_mask", "_width", "_stride")

    def __init__(self, column: bytearray, width: int, count: int) -> None:
        if width % 8 or len(column) < width * count:
            raise ValueError(f"a key index reads {count} keys of {width} bytes, a multiple of eight")
        size = 1 << max(1, (2 * count).bit_length())
        self._column = memoryview(column)
        self._words = self._column[: width * count].cast("Q")
        self._table = array("I", [KEY_EMPTY]) * size
        self._mask = size - 1
        self._width = width
        self._stride = width // 8
        table = self._table
        mask = self._mask
        words = self._words
        view = self._column
        stride = self._stride
        for ordinal in range(count):
            slot = words[ordinal * stride] & mask
            start = ordinal * width
            while True:
                held = table[slot]
                if held == KEY_EMPTY:
                    table[slot] = ordinal
                    break
                if view[held * width : held * width + width] == view[start : start + width]:
                    raise RepeatedKey(held, ordinal)
                slot = (slot + 1) & mask

    def find(self, key: bytes) -> int | None:
        if len(key) != self._width:
            return None
        table = self._table
        mask = self._mask
        width = self._width
        view = self._column
        slot = int.from_bytes(key[:8], sys.byteorder) & mask
        while True:
            held = table[slot]
            if held == KEY_EMPTY:
                return None
            if view[held * width : held * width + width] == key:
                return held
            slot = (slot + 1) & mask

    def nbytes(self) -> int:
        return len(self._table) * self._table.itemsize

    def release(self) -> None:
        """Release the views over the key column, so the column can be resized or freed."""
        self._words.release()
        self._column.release()
