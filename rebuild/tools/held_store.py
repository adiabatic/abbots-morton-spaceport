"""The verdict store as the verdict update holds it between its merges: its own records as sorted record lines in one buffer, a table over them, and its head and tail, written in the layout the review server and the land write (`landing.document_bytes`).

`Held` is one store: its stamp, the `accepted` field it names, its own record lines, each the record as the land normalizes it (`landing.record_line`) followed by `,\\n`, in unit order, its own tombstones on units that hold no own record, and its override section, its records normalized the same way. The table holds one 26-byte entry a record line (`ENTRY`: the unit id's 13 bytes, the verdict's code, its index in `KINDS`, and the line's offset in the buffer and its length), sorted by id; a line whose unit id is not 13 bytes in UTF-8, does not start with `u-` (`ID_PREFIX`, the prefix of every unit id, `unit_cache.unit_id_for`), or whose verdict is not one of `KINDS` is kept in a small map instead (`Held.escaped`), since `verdict_store.well_formed` accepts any string unit and verdict, and a lookup finds a tabled id in the bucket of its third and fourth bytes, which the table's order keeps in order only while every tabled id shares its first two. `Records` reads the lines through the table as a mapping from unit to record, which `accepted.View` takes in place of a dict (`Held.view`), so a reader handed the view answers as one over the parsed store does. A line is parsed when a reader asks for its record; the table and the buffer are a few large objects, never one per record.

`Builder` makes a `Held` from records in unit order, a line at a time, as a merge streams the store it reads and its inputs; `patched` makes the store a merge leaves from a `Held` and the records the merge changes, copying the buffer's bytes and the table's entries between the changed lines (patch by address), so a merge over a `Held` costs what its inputs hold, not what the store holds. `chunks` gives a `Held`'s bytes as a document and `write` writes them once, to a temporary name renamed over the path.

`own_records` streams a store file's own records in unit order, normalized, for a reader that found them in that order with `accepted.read_tail`. `Transition` is a hand merge's change to the journal (`journal.Transition`): it walks the store the merge replaced, read through the descriptor the merge kept open across the replace, and the `Held` it wrote, in step by unit, each as one store with its override section (`accepted.joined`), and gives the journal's set lines spliced from the written record lines, as the land splices them, and its clear lines and seed from the walk, so `journal.record_transition` writes the lines it writes when it diffs the two stores in memory.
"""

from __future__ import annotations

import heapq
import json
import os
import struct
from array import array
from bisect import bisect_left
from collections.abc import Callable, ItemsView, Iterable, Iterator, Mapping, ValuesView
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from rebuild.review import accepted, journal, landing
from rebuild.review.accepted import Overrides

KINDS = ("approve", "reject", "either", "identical", "neither", "skip")
CODES = {kind: code for code, kind in enumerate(KINDS)}
ID_BYTES = 13
ID_PREFIX = b"u-"
ENTRY = struct.Struct("<13sBqI")
_OFFSET = struct.Struct("<q")
_SPAN = struct.Struct("<qI")
_SPAN_AT = ID_BYTES + 1
_SEPARATOR = b",\n"
_SET_PREFIX = b'{"kind": "set", '
_BUCKETS = 1 << 16
_WRITE_BLOCK = 1 << 20


def normalized(record: Mapping) -> dict:
    """Return `record` as the store holds it, the land's normalization (`landing._normalize`): its unit, verdict, note (`""` when missing or false-valued, as `verdict_store.note_of` reads it) and `at` (`""` when missing or false-valued)."""
    return {
        "unit": record["unit"],
        "verdict": record.get("verdict"),
        "note": record.get("note") or "",
        "at": record.get("at") or "",
    }


def _key(unit: str) -> bytes:
    return unit.encode("utf-8", "surrogatepass")


@dataclass
class Held:
    """A store held as record lines: its stamp, the `accepted` field it names (`named`, None when it names no list), its own record lines in unit order (`lines`, each followed by `,\\n`), the table over them (`table`, `ENTRY` a line, sorted by id), the lines the table cannot hold by unit (`escaped`, unit to offset and length), its own tombstones on units holding no own record (`cleared`), and its override section (`overrides`), its records normalized."""

    stamp: str
    named: dict | None
    lines: bytes | bytearray = b""
    table: bytes = b""
    escaped: dict[str, tuple[int, int]] = field(default_factory=dict)
    cleared: dict[str, str] = field(default_factory=dict)
    overrides: Overrides = field(default_factory=Overrides)

    def records(self) -> Records:
        return Records(self)

    def view(self, listed: accepted.Accepted) -> accepted.View:
        """Return the store read through the list `listed` as `accepted.view_of` reads a store document that pairs with it: its own records through the table, its tombstones and its override section."""
        return accepted.View(self.records(), self.cleared, listed, self.overrides)

    def chunks(self, exported_at: str) -> Iterator[bytes]:
        """Yield the store's bytes as a document (`landing.document_bytes`'s layout), stamped `exported_at`."""
        yield landing.head_bytes(self.stamp, exported_at, self.named)
        body = memoryview(self.lines)[: max(0, len(self.lines) - len(_SEPARATOR))]
        for at in range(0, len(body), _WRITE_BLOCK):
            yield bytes(body[at : at + _WRITE_BLOCK])
        yield from (part for part, _unit in landing.tail_parts(self.cleared, self.overrides))

    def write(self, path: Path, exported_at: str | None = None) -> None:
        """Write the store to `path` once, through a temporary file renamed over it, stamped `exported_at` or the current time."""
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("wb") as out:
                for chunk in self.chunks(exported_at or journal.now_stamp()):
                    out.write(chunk)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def line_count(self) -> int:
        return len(self.table) // ENTRY.size + len(self.escaped)


def _starts(table: bytes, count: int) -> array:
    """Return where each bucket of the table's entries starts, by the third and fourth bytes of their ids, which the table's order keeps in order since every tabled id starts with `ID_PREFIX`, so a lookup bisects one bucket."""
    third, fourth = table[2 :: ENTRY.size], table[3 :: ENTRY.size]
    starts = array("I", bytes(4 * (_BUCKETS + 1)))
    for high in range(256):
        low_end = bisect_left(third, high)
        high_end = bisect_left(third, high + 1, low_end)
        base = high << 8
        for low in range(256):
            starts[base + low] = (
                bisect_left(fourth, low, low_end, high_end) if low_end < high_end else low_end
            )
    starts[_BUCKETS] = count
    return starts


def _bisect(table: bytes, lo: int, hi: int, key: bytes) -> int:
    while lo < hi:
        mid = (lo + hi) >> 1
        at = mid * ENTRY.size
        if table[at : at + ID_BYTES] < key:
            lo = mid + 1
        else:
            hi = mid
    return lo


class Records(Mapping[str, dict]):
    """A `Held` store's own records as a read-only mapping from unit to record, each parsed from its line when asked for, found through the table (by bucket and then by bisection) or in the escaped map. Iteration walks the units in order."""

    __slots__ = ("_held", "_count", "_starts")

    def __init__(self, held: Held) -> None:
        self._held = held
        self._count = len(held.table) // ENTRY.size
        self._starts: array | None = None

    def span(self, unit: object) -> tuple[int, int] | None:
        """Return the offset and length of `unit`'s own record line, or None when the store holds none."""
        held = self._held
        if not isinstance(unit, str):
            return None
        if self._count:
            key = _key(unit)
            if _tabled(key):
                if self._starts is None:
                    self._starts = _starts(held.table, self._count)
                bucket = (key[2] << 8) | key[3]
                index = _bisect(held.table, self._starts[bucket], self._starts[bucket + 1], key)
                at = index * ENTRY.size
                if index < self._count and held.table[at : at + ID_BYTES] == key:
                    return _SPAN.unpack_from(held.table, at + _SPAN_AT)
        return held.escaped.get(unit)

    def _parse(self, span: tuple[int, int]) -> dict:
        offset, length = span
        return json.loads(self._held.lines[offset : offset + length])

    def __getitem__(self, unit: str) -> dict:
        span = self.span(unit)
        if span is None:
            raise KeyError(unit)
        return self._parse(span)

    def get(self, unit: str, default=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        span = self.span(unit)
        return default if span is None else self._parse(span)

    def __contains__(self, unit: object) -> bool:
        return self.span(unit) is not None

    def __len__(self) -> int:
        return self._count + len(self._held.escaped)

    def entries(self) -> Iterator[tuple[str, int, int]]:
        """Yield each own line's unit, offset and length, in unit order."""
        tabled = (
            (unit.decode("utf-8", "surrogatepass"), offset, length)
            for unit, _code, offset, length in ENTRY.iter_unpack(self._held.table)
        )
        if not self._held.escaped:
            return tabled
        escaped = sorted((unit, offset, length) for unit, (offset, length) in self._held.escaped.items())
        return heapq.merge(tabled, escaped)

    def __iter__(self) -> Iterator[str]:
        return (unit for unit, _offset, _length in self.entries())

    def values(self) -> ValuesView[dict]:
        return _Values(self)

    def items(self) -> ItemsView[str, dict]:
        return _Items(self)


class _Values(ValuesView):
    def __iter__(self) -> Iterator[dict]:
        records: Records = self._mapping
        return (records._parse((offset, length)) for _unit, offset, length in records.entries())


class _Items(ItemsView):
    def __iter__(self) -> Iterator[tuple[str, dict]]:
        records: Records = self._mapping
        return ((unit, records._parse((offset, length))) for unit, offset, length in records.entries())


def _tabled(key: bytes) -> bool:
    return len(key) == ID_BYTES and key.startswith(ID_PREFIX)


def _index(table: bytearray, escaped: dict, unit: str, verdict: object, offset: int, length: int) -> None:
    key = _key(unit)
    code = CODES.get(verdict) if isinstance(verdict, str) else None
    if _tabled(key) and code is not None:
        table += ENTRY.pack(key, code, offset, length)
    else:
        escaped[unit] = (offset, length)


class Builder:
    """A `Held` made a record at a time, in unit order: each record's line is appended to one buffer and its entry to the table."""

    def __init__(self) -> None:
        self.lines = bytearray()
        self._table = bytearray()
        self._escaped: dict[str, tuple[int, int]] = {}
        self._last: str | None = None

    def add(self, record: Mapping) -> None:
        """Append `record`, normalized, after the records added before it, whose units all sort before its own."""
        unit = record["unit"]
        if self._last is not None and unit <= self._last:
            raise ValueError(f"{unit} follows {self._last}, out of unit order")
        self._last = unit
        line = landing.record_line(normalized(record))
        offset = len(self.lines)
        self.lines += line
        self.lines += _SEPARATOR
        _index(self._table, self._escaped, unit, record.get("verdict"), offset, len(line))

    def held(self, stamp: str, named: dict | None, cleared: dict[str, str], overrides: Overrides) -> Held:
        return Held(stamp, named, self.lines, bytes(self._table), self._escaped, cleared, overrides)


def _extend_shifted(entries: bytearray, table: bytes, start: int, stop: int, shift: int) -> None:
    """Append `table`'s entries from `start` to `stop` to `entries`, each line's offset moved by `shift`, through a view of the table, so no copy of the range is made beside the one appended."""
    begin = len(entries)
    with memoryview(table) as view:
        entries += view[start * ENTRY.size : stop * ENTRY.size]
    if shift:
        for at in range(begin + _SPAN_AT, len(entries), ENTRY.size):
            (offset,) = _OFFSET.unpack_from(entries, at)
            _OFFSET.pack_into(entries, at, offset + shift)


def patched(held: Held, changes: Iterable[Mapping], cleared: dict[str, str], overrides: Overrides) -> Held:
    """Return the store `held` with each record in `changes`, in unit order, as its unit's own record, replacing the line the unit holds or added at its place, and with the tombstones `cleared` and the override section `overrides`: the bytes between the changed lines are copied from `held`'s buffer and their entries from its table, each moved by the bytes the lines before it gained, through views, so the old store and the new one are the only copies of its lines."""
    table = held.table
    count = len(table) // ENTRY.size
    escapes = sorted(held.escaped.items())
    lines, entries, escaped = bytearray(), bytearray(), {}
    position = tabled = skipped = 0
    with memoryview(held.lines) as old:
        for record in changes:
            unit = record["unit"]
            key = _key(unit)
            index = _bisect(table, tabled, count, key)
            at = index * ENTRY.size
            in_table = index < count and table[at : at + ID_BYTES] == key
            while skipped < len(escapes) and escapes[skipped][0] < unit:
                skipped += 1
            in_escaped = skipped < len(escapes) and escapes[skipped][0] == unit
            if in_table:
                split, length = _SPAN.unpack_from(table, at + _SPAN_AT)
                replaced = length + len(_SEPARATOR)
            elif in_escaped:
                split, length = escapes[skipped][1]
                replaced = length + len(_SEPARATOR)
            else:
                split = _OFFSET.unpack_from(table, at + _SPAN_AT)[0] if index < count else len(old)
                if skipped < len(escapes):
                    split = min(split, escapes[skipped][1][0])
                replaced = 0
            shift = len(lines) - position
            lines += old[position:split]
            _extend_shifted(entries, table, tabled, index, shift)
            for escaped_unit, (offset, length) in escapes:
                if position <= offset < split:
                    escaped[escaped_unit] = (offset + shift, length)
            line = landing.record_line(normalized(record))
            _index(entries, escaped, unit, record.get("verdict"), len(lines), len(line))
            lines += line
            lines += _SEPARATOR
            position = split + replaced
            tabled = index + 1 if in_table else index
        shift = len(lines) - position
        lines += old[position:]
        _extend_shifted(entries, table, tabled, count, shift)
        for escaped_unit, (offset, length) in escapes:
            if offset >= position:
                escaped[escaped_unit] = (offset + shift, length)
    return Held(held.stamp, held.named, lines, bytes(entries), escaped, cleared, overrides)


def counts(held: Held) -> tuple[int, int]:
    """Return how many verdicts `held` holds and how many of them are not skips, its own records and its override records as one store, the override record taking a unit both name, as the merge counts them: own records by their codes in the table, and only the escaped lines and the own lines the override section shadows parsed."""
    records = Records(held)
    skip = CODES["skip"]
    own = records._count
    own_skips = held.table[ID_BYTES :: ENTRY.size].count(skip)
    for offset, length in held.escaped.values():
        own += 1
        own_skips += json.loads(held.lines[offset : offset + length]).get("verdict") == "skip"
    for unit in held.overrides.records:
        shadowed = records.get(unit)
        if shadowed is not None:
            own -= 1
            own_skips -= shadowed.get("verdict") == "skip"
    overridden = held.overrides.records.values()
    holds = own + len(held.overrides.records)
    return holds, own - own_skips + sum(1 for record in overridden if record.get("verdict") != "skip")


def own_lines(held: Held) -> Iterator[tuple[str, bytes]]:
    """Yield each of `held`'s own record lines with its unit, in unit order, without parsing it."""
    lines = held.lines
    for unit, offset, length in Records(held).entries():
        yield unit, bytes(lines[offset : offset + length])


def _splice(line: bytes) -> bytes:
    """Return the journal's set line for a store's record line, as `landing._splice` does: the record's fields after `"kind": "set"`."""
    return _SET_PREFIX + line[1:] + b"\n"


class Transition:
    """A hand merge's change to the journal (`journal.Transition`), from the store it replaced to the store it wrote, `new`, each as one store with its override section (`accepted.joined`). `old_records` gives the replaced store's own records in unit order, normalized, each time it is called: a stream through the descriptor the merge held across the replace, or the records it read whole; `old_cleared` is its tombstones, those on units holding a record among them, which the walk passes over, and `old_overrides` its override section. The diff walks the two stores in step by unit and keeps only the units of its set lines and its clear lines, so the set lines are spliced from `new`'s record lines afterward and nothing is held for a unit that did not change."""

    def __init__(
        self,
        new: Held,
        old_records: Callable[[], Iterator[dict]],
        old_cleared: Mapping[str, str],
        old_overrides: Overrides,
    ) -> None:
        self._new = new
        self._old_records = old_records
        self._old_cleared = old_cleared
        self._old_overrides = old_overrides
        overrides = new.overrides
        records = Records(new)
        hidden = sum(1 for unit in (*overrides.records, *overrides.cleared) if unit in records)
        self.records = new.line_count() - hidden + len(overrides.records)
        self._sets: list[str] = []
        self._clears: list[tuple[str, str | None]] = []
        self._old_count = 0
        self._old_tombstones: dict[str, str] = {}

    def _old_joined(self) -> Iterator[dict]:
        """Yield the replaced store's records as one store, its own on units its override section does not name and its override records, in unit order, normalized."""
        overrides = self._old_overrides
        pending = sorted(overrides.records)
        at = 0
        for record in self._old_records():
            unit = record["unit"]
            while at < len(pending) and pending[at] < unit:
                yield normalized(overrides.records[pending[at]])
                at += 1
            if unit in overrides:
                continue
            yield record
        for unit in pending[at:]:
            yield normalized(overrides.records[unit])

    def _new_joined(self) -> Iterator[tuple[str, bytes]]:
        """Yield the written store's records as one store, each as its line, in unit order."""
        overrides = self._new.overrides
        pending = sorted(overrides.records)
        at = 0
        for unit, line in own_lines(self._new):
            while at < len(pending) and pending[at] < unit:
                yield pending[at], landing.record_line(overrides.records[pending[at]])
                at += 1
            if unit in overrides:
                continue
            yield unit, line
        for unit in pending[at:]:
            yield unit, landing.record_line(overrides.records[unit])

    def _old_tombstone_map(self, old_named: set[str]) -> dict[str, str]:
        """Return the replaced store's tombstones as one store: its own on units its override section does not name and that hold no record, `old_named` holding those of its tombstones' units that do, and its override section's."""
        cleared = {
            unit: at
            for unit, at in self._old_cleared.items()
            if unit not in self._old_overrides and unit not in old_named
        }
        cleared.update(self._old_overrides.cleared)
        return cleared

    def _new_tombstones(self) -> dict[str, str]:
        overrides = self._new.overrides
        records = Records(self._new)
        tombstones = {
            unit: at
            for unit, at in self._new.cleared.items()
            if unit not in overrides and unit not in records
        }
        tombstones.update(overrides.cleared)
        return tombstones

    def diff(self) -> tuple[int, int, bool]:
        """Walk the two stores in step and return the counts of set and clear lines and whether the replaced store holds a record or a tombstone. Only the units of the set lines, the units the replaced store holds that the written one does not, and of the units either store holds a tombstone on, whether the other holds a record there, are kept."""
        new_tombstones = self._new_tombstones()
        watched = set(self._old_cleared) | set(self._old_overrides.cleared) | set(new_tombstones)
        old_named: set[str] = set()
        new_named: set[str] = set()
        removed: set[str] = set()
        old_records = self._old_joined()
        old_head = next(old_records, None)
        for unit, line in self._new_joined():
            if unit in watched:
                new_named.add(unit)
            while old_head is not None and old_head["unit"] < unit:
                removed.add(old_head["unit"])
                self._old_count += 1
                old_head = next(old_records, None)
            if old_head is not None and old_head["unit"] == unit:
                if unit in watched:
                    old_named.add(unit)
                self._old_count += 1
                same = landing.record_line(old_head) == line
                old_head = next(old_records, None)
                if same:
                    continue
            self._sets.append(unit)
        while old_head is not None:
            removed.add(old_head["unit"])
            self._old_count += 1
            old_head = next(old_records, None)
        old_named |= removed & watched
        self._old_tombstones = old_tombstones = self._old_tombstone_map(old_named)
        gone = (removed | set(old_tombstones) | set(new_tombstones)) - new_named
        self._clears = [
            (unit, new_tombstones.get(unit))
            for unit in sorted(gone)
            if unit in removed or unit in old_named or old_tombstones.get(unit) != new_tombstones.get(unit)
        ]
        return len(self._sets), len(self._clears), bool(self._old_count or old_tombstones)

    def tombstones(self) -> Mapping[str, str]:
        return self._new_tombstones()

    def set_lines(self, base: bool) -> Iterator[bytes]:
        if base:
            yield from (_splice(line) for _unit, line in self._new_joined())
            return
        wanted = iter(self._sets)
        next_unit = next(wanted, None)
        for unit, line in self._new_joined():
            if unit == next_unit:
                yield _splice(line)
                next_unit = next(wanted, None)

    def clear_lines(self) -> Iterator[bytes]:
        for unit, tombstone in self._clears:
            yield journal._json_line({"kind": "clear", "unit": unit, "at": tombstone})

    def seed(self) -> tuple[int, Mapping[str, str], Iterator[bytes]]:
        return (
            self._old_count,
            self._old_tombstones,
            (journal._json_line(journal._set_line(record)) for record in self._old_joined()),
        )


class Unsorted(Exception):
    """Raised by `own_records` when a store's records do not come in unit order: a record on a unit before the one ahead of it. The merge and the carry stream a file only once `accepted.read_tail` has found its records in unit order, so this stops a file that changed since rather than a file they read whole."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def own_records(source: Path | BinaryIO, name: str) -> Iterator[dict]:
    """Yield the records of the store document at `source`, a path or a binary file read from its start, in unit order, normalized, settling records on one unit next to each other as `journal.latest_by_unit` settles them, the latest `at` winning and the first of equal ones kept; a record on a unit before the one ahead of it raises `Unsorted`, naming the store `name`. The reader checked the document's records with `accepted.read_tail`, so each is a well-formed record."""
    held: dict | None = None
    for record in accepted.iter_records(source):
        assert isinstance(record, dict)
        if held is not None:
            if record["unit"] == held["unit"]:
                if (record.get("at") or "") > (held.get("at") or ""):
                    held = record
                continue
            if record["unit"] < held["unit"]:
                raise Unsorted(name)
            yield normalized(held)
        held = record
    if held is not None:
        yield normalized(held)
