"""A debug tally of the large collections a corpus build holds, read at its phase boundaries, so that a peak in the timings journal can be attributed to the collection that caused it. It is off unless the environment sets `AMS_CORPUS_MEMORY_TALLY=1` (`TALLY_ENV`). The artifact cycle never sets it; the build child inherits it from the caller's environment. With it unset, `from_environment` returns None, the build skips its `if tally:` branches, and the shards and the build's other output are unchanged.

The figures are for attribution and are approximate. A held collection's estimate is the container's own `sys.getsizeof` plus a deep `sys.getsizeof` walk over a sample of up to `SAMPLE_SIZE` members, scaled by the member count. `_sample` takes every nth member from the start, with n the count divided by `SAMPLE_SIZE` and rounded down, so for a collection larger than `SAMPLE_SIZE` whose count is not a multiple of it, the sample covers only the first part of the collection, as little as about half. That takes a fraction of a second over a corpus of a million units, where walking every member would take seconds for each collection at each boundary. The walk descends into the stdlib containers, `__dict__`, and every slot, and stops at strings, bytes, and numbers. An object reached twice within one collection's sample is counted once, so members that share a pooled tuple or an interned name are charged for one copy, as the process is. `leaf_types` names types the walk counts without entering, so a collection of records that point into another collection does not also count that collection's members and outrank it. `nested` is for a collection whose members are themselves tables: each sampled member is estimated by its own bounded sample instead of walked whole, and the count is the members' lengths summed, so a boundary stays cheap over tables of a million rows each.

A collection registered with `hold` is re-estimated at every later boundary, because the build mutates and drains what it holds. A collection the build has emptied reads as empty at later boundaries and stays in the output. `hold_reading` registers a callable instead, returning `(count, bytes)` or a `Measure`. It serves a collection that keeps its own exact figures (`ink.shape_memo_sizes`), a collection stored as packed columns (`column_sizes`, as used by `UnitStore.sizes` and `build.row_column_sizes`), and a collection the build holds only through another object. `build_m1`, `_RecomputeRunner.hold_collections`, `_write_corpus` and `_corpus_worker` in `rebuild/review/build.py` register what they hold. A recomputed unit's fragment is written to the spool as it is drafted, so no process holds a collection of enrichments. What remains of a recomputed unit after its batch is its spool address: a column of the parent's packed unit store (`unit_store`), and a field of the projections a pooled worker holds until it has sent them (`worker.projections`).

Each boundary prints one line per collection, largest first, then one line naming the largest. Every line starts with `[tally] ` (`TALLY`) and is whitespace-separated, so a `grep '^\\[tally\\]'` over the cycle's corpus-build step log (`var/build-logs/<run>/<nn>-corpus-build.log`, which receives the build's stdout) recovers the whole record:

    [tally] <boundary> <collection> count=<n> est_bytes=<n> est_gb=<x.xx>
    [tally] <boundary> <collection> count=<n> est_bytes=<n> est_gb=<x.xx> packed_bytes=<n> packed_per=<x.x> walked_per=<x.x> ratio=<x.xx|-> strings=<n> string_bytes=<n>
    [tally] <boundary> largest=<collection>

The first form is a walked estimate or a bare `(count, bytes)` reading. The second is a reading that carries a `PackedCost`, which `column_sizes` returns for a collection stored as columns. `packed_bytes` is the columns' bytes over the whole count. `packed_per` and `walked_per` are bytes per member for the columns and for `est_bytes`. `ratio` is `walked_per` divided by `packed_per`, or `-` when either is zero, as for an empty collection. `strings` and `string_bytes` are the string table the columns index: the number of distinct strings and their bytes. The table is left out of `packed_bytes` because one table serves several collections, and `est_bytes` adds it only on the line of the collection that holds it (`column_sizes`), so the strings are counted once. `<boundary>` is the build phase at whose end the reading was taken (`load`, `plan`, `units`, `manifest+check`, `review-facts`, `cache`), or `w<i>/phase1-<n>` for a pooled worker's own collections after its nth batch. Neither contains whitespace. `est_gb` is in decimal gigabytes, the unit of the `rss_gb=` token on the same phase's `[t]` line. A boundary with nothing held prints only the `largest=` line, with `-` as the name.

The module imports only the standard library. rebuild/test_review_code_closure.py lists it in `WIDTH_AND_TELEMETRY_MODULES`, the rebuild/tools modules the build may import that cannot change a unit's output.
"""

from __future__ import annotations

import os
import sys
from array import array
from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from itertools import islice
from typing import IO, Protocol

TALLY_ENV = "AMS_CORPUS_MEMORY_TALLY"
TALLY = "[tally] "
SAMPLE_SIZE = 256

ID_WIDTH = 4
OFFSET_WIDTH = 4

_BYTES_PER_GB = 1e9
_LEAVES = (str, bytes, bytearray, int, float, complex, bool, type(None))
_SEQUENCES = (list, tuple, set, frozenset, deque)


def enabled(environ: Mapping[str, str] = os.environ) -> bool:
    return environ.get(TALLY_ENV) == "1"


def deep_size(root: object, seen: set[int], leaf_types: tuple[type, ...] = ()) -> int:
    """Return the bytes reachable from `root` that `seen` has not already counted: container sizes, slots and instance dicts, and the leaves under them. The caller shares `seen` across one sample, so an object shared by several members is counted once per collection."""
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


def _sample(collection: Collection, size: int) -> list:
    members: Iterable = collection.items() if isinstance(collection, Mapping) else collection
    step = max(1, len(collection) // size)
    return list(islice(members, 0, None, step))[:size]


def _is_table(value: object) -> bool:
    return isinstance(value, Collection) and not isinstance(value, _LEAVES)


def estimate(
    collection: Collection,
    *,
    leaf_types: tuple[type, ...] = (),
    sample_size: int = SAMPLE_SIZE,
    nested: bool = False,
) -> tuple[int, int]:
    """Return (count, estimated bytes) for one collection: the container's own size plus the deep size of a sample of its members (`_sample`), scaled by the count. A mapping's sample is its items, so keys and values are both counted. With `nested`, each sampled member that is a collection is estimated by its own bounded sample instead of walked in full, and the count is the members' lengths summed, which is the number of rows the collection's tables hold."""
    members = len(collection)
    total = sys.getsizeof(collection)
    if members == 0:
        return 0, total
    sample = _sample(collection, sample_size)
    seen: set[int] = set()
    sampled = 0
    for member in sample:
        key, value = member if isinstance(collection, Mapping) else (None, member)
        if key is not None:
            sampled += deep_size(key, seen, leaf_types)
        if nested and _is_table(value):
            sampled += estimate(value, leaf_types=leaf_types, sample_size=sample_size)[1]
        else:
            sampled += deep_size(value, seen, leaf_types)
    count = members
    if nested:
        values: Iterable = collection.values() if isinstance(collection, Mapping) else collection
        count = sum(len(value) if _is_table(value) else 1 for value in values)
    return count, total + round(sampled * members / len(sample))


@dataclass(frozen=True)
class PackedCost:
    est_bytes: int
    strings: int
    string_bytes: int


@dataclass(frozen=True)
class Measure:
    count: int
    est_bytes: int
    packed: PackedCost | None = None


class Vocabulary(Protocol):
    """A string table as `column_sizes` reads one: `chars` is the distinct strings' UTF-8 bytes summed, and `len` is their number (`columns.StringTable`)."""

    chars: int

    def __len__(self) -> int: ...


class Pool(Protocol):
    """A pool of name tuples as `pool_bytes` reads one: `elements` is the distinct tuples' lengths summed, and `len` is their number (`columns.TuplePool`)."""

    elements: int

    def __len__(self) -> int: ...


def bytes_of(column: array | bytearray) -> int:
    return len(column) if isinstance(column, bytearray) else len(column) * column.itemsize


def pool_bytes(pool: Pool) -> int:
    """Return what a packed side column over the pool takes: each distinct tuple once, at an `ID_WIDTH` id per element plus an `OFFSET_WIDTH` offset. The strings are charged to the string table they are interned in, not here."""
    return pool.elements * ID_WIDTH + len(pool) * OFFSET_WIDTH


def column_sizes(
    n: int,
    columns: Iterable[array | bytearray],
    table: Vocabulary,
    extra_bytes: int = 0,
    *,
    holds_table: bool = True,
) -> Measure:
    """Return the exact reading of a packed collection: `n` rows, with the columns' bytes plus `extra_bytes` (a tuple pool's `pool_bytes`, or lines the collection keeps as objects beside its arrays) as the packed figure. The string table is reported beside it: each distinct string once at its UTF-8 length plus an `OFFSET_WIDTH` offset. The walked figure is the packed figure plus the table, so the line's ratio shows the table's share: it reads `1.00` or `1.01` on the corpus and up to `1.09` on the mini bundle, whose table is larger beside its columns. A collection whose table another collection's line already counts passes `holds_table=False`, and its walked figure is then its rows alone, with the table still printed beside them. The workload table's strings are shared this way by the audit's row columns, the unit store and the pre-merge snapshot, so they are counted on one line, and a boundary's `largest=` compares what each collection holds on its own."""
    rows = sum(bytes_of(column) for column in columns) + extra_bytes
    strings = table.chars + len(table) * OFFSET_WIDTH
    return Measure(n, rows + strings if holds_table else rows, PackedCost(rows, len(table), strings))


@dataclass(frozen=True)
class Reading:
    collection: str
    count: int
    est_bytes: int
    packed: PackedCost | None = None

    @property
    def line_body(self) -> str:
        body = f"{self.collection} count={self.count} est_bytes={self.est_bytes} est_gb={self.est_bytes / _BYTES_PER_GB:.2f}"
        if self.packed is None:
            return body
        packed_per = self.packed.est_bytes / self.count if self.count else 0.0
        walked_per = self.est_bytes / self.count if self.count else 0.0
        ratio = f"{walked_per / packed_per:.2f}" if packed_per and walked_per else "-"
        return (
            f"{body} packed_bytes={self.packed.est_bytes} packed_per={packed_per:.1f} walked_per={walked_per:.1f} "
            f"ratio={ratio} strings={self.packed.strings} string_bytes={self.packed.string_bytes}"
        )


class MemoryTally:
    """The collections one process holds, registered as they are created and read at every later boundary. With `out` unset, each write goes to the `sys.stdout` of that moment, for the reason `rebuild.tools.console` gives: the cycle replaces `sys.stdout` with a tee after this module is imported."""

    def __init__(self, *, out: IO[str] | None = None, sample_size: int = SAMPLE_SIZE) -> None:
        self._out = out
        self._sample_size = sample_size
        self._held: dict[str, tuple[Collection, tuple[type, ...], bool]] = {}
        self._readings: dict[str, Callable[[], tuple[int, int] | Measure]] = {}

    def hold(
        self,
        name: str,
        collection: Collection,
        *,
        leaf_types: tuple[type, ...] = (),
        nested: bool = False,
    ) -> None:
        self._readings.pop(name, None)
        self._held[name] = (collection, leaf_types, nested)

    def hold_reading(self, name: str, read: Callable[[], tuple[int, int] | Measure]) -> None:
        self._held.pop(name, None)
        self._readings[name] = read

    def release(self, name: str) -> None:
        self._held.pop(name, None)
        self._readings.pop(name, None)

    def readings(self) -> list[Reading]:
        readings = []
        for name, (collection, leaf_types, nested) in self._held.items():
            count, est_bytes = estimate(
                collection, leaf_types=leaf_types, sample_size=self._sample_size, nested=nested
            )
            readings.append(Reading(name, count, est_bytes))
        for name, read in self._readings.items():
            result = read()
            if isinstance(result, Measure):
                readings.append(Reading(name, result.count, result.est_bytes, result.packed))
            else:
                count, est_bytes = result
                readings.append(Reading(name, int(count), int(est_bytes)))
        return sorted(readings, key=lambda reading: (-reading.est_bytes, reading.collection))

    def boundary(self, name: str) -> list[Reading]:
        readings = self.readings()
        lines = [f"{TALLY}{name} {reading.line_body}" for reading in readings]
        lines.append(f"{TALLY}{name} largest={readings[0].collection if readings else '-'}")
        stream = sys.stdout if self._out is None else self._out
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        return readings


def from_environment(
    environ: Mapping[str, str] = os.environ, *, out: IO[str] | None = None
) -> MemoryTally | None:
    """Return a tally when `AMS_CORPUS_MEMORY_TALLY=1` is in the environment, and None otherwise. The None is the only off switch: a build given None prints and estimates nothing."""
    return MemoryTally(out=out) if enabled(environ) else None
