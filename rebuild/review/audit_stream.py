"""The divergence audit read as sorted sections, one window at a time, through the descriptors the caller holds, one a part. It imports only the standard library, so the corpus build's key-only read runs it as a process of its own (`python -m rebuild.review.audit_stream`) that holds this module and its job and nothing of the build's.

run_m1 writes the audit as a base and plain keyed segments under a head (`rebuild/pipeline/row_delta.py`, `oracle.AuditWriter`), each part in sorted sections: one section a configuration, the sections in configuration rank (`audit._config_index`), each section's rows in strictly increasing window order, by the window's length, then its code points (`audit.triage_key`'s window terms). The base's lines are rows, and a segment's are upserts, `+\t<row>`, and tombstones, `-\t<config>\t<window>`, after its header line. Every window is written in `audit.format_codepoints`' form, four uppercase hex digits a code point, so a window field's length and then its text order windows as their code points do. `audit_sections` finds each section's byte range in a part by bisecting on line starts with `os.pread` (`first_line`), `Section.part` naming the part, and `lockstep` walks the sections together, a configuration's sections across its parts merged by window (`_merged`: the latest part's row wins at a window, and a tombstone removes it), yielding each window's rows from every configuration that has the window, so it reads the materialized audit one window at a time; `materialized_lines` gives the same rows in the audit's own order. Every read is a `pread` on a held descriptor, never a reopening of a path, since a `--gates-only` pass can commit a new head during a build, and never a mapping, whose page touches count in the reader's resident set.

A file in any other shape is no error here: a cursor that meets a row of another configuration, a window that does not strictly increase, a window not in the canonical form, a row without six fields, a carriage return or bytes that are not UTF-8 raises `OrderBreak`, and the build reads the file through its in-memory source instead (`audit.load_audit_table`), which takes rows in any order.

`FamilyLines` gives a window's family lines, the suffix both store keys append to their lines (`unit_cache.UnitKeyer`), and `signature_key` and `input_key` compute the ink-signature store's key of a row and the unit store's input key of a unit from the audit's raw lines and fields, byte for byte as `UnitKeyer` computes them from the parsed rows.

Run as a module, it is one process of the key-only read (`main`): it reads its job from stdin, a JSON line naming every held descriptor of the audit's parts, which it inherits, the sections across them, its window range and the family keys, followed by the prior ink-signature store's sorted keys as raw `u64`s; walks its range in lockstep; keys every row of a window whose rows name two or more (baseline, new) pairs, the windows the ink-duplicate merge compares; bisects each key in the prior keys; and writes JSON lines to stdout a frame at a time, flushing each frame at the first window end after its rows' estimated size reaches `FRAME_BYTES`, so a frame holds at most `FRAME_BYTES` plus one window's rows: `hits`, the prior keys' positions it found, `misses`, `[key, section, window]` for each key it did not, then `break` with the reason if its range broke the layout, and `done` with its counts, seconds and resident peak.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import resource
import sys
import time
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import NamedTuple

READ_BYTES = 1 << 20
# A segment's sections are read this much at a time, the base's `READ_BYTES`: a walk primes a cursor for every section of every part at once and each holds its read's lines while it waits (`section_rows`), and a segment's rows are 1.3% to 10% of the base's at 45 runes, so a read this size of a segment spans the windows of 0.6 to 5 MiB of the base's.
SEGMENT_READ_BYTES = 1 << 16
PROBE_BYTES = 4096
FRAME_BYTES = 1 << 19
FIELDS = 6
CANONICAL_WINDOW = re.compile(r"[0-9A-F]{4}(?::[0-9A-F]{4})*")

WindowKey = tuple[int, str]
Row = tuple[WindowKey, list[str], str]


class OrderBreak(Exception):
    """The audit is not in sorted sections, so the build reads it through its in-memory source."""


class Section(NamedTuple):
    """One configuration's rows in one part: its configuration, its rank, the byte range `[start, end)` of its lines, and its part, the base's 0 and each segment's number, which names the descriptor it is read through and whether its lines are rows (the base's) or a segment's upserts and tombstones."""

    config: str
    rank: int
    start: int
    end: int
    part: int = 0


def window_key(field: str) -> WindowKey:
    """The order of a canonical window field: its length, then its text, which is the order of its length in code points, then its code points."""
    return (len(field), field)


def next_line_start(descriptor: int, offset: int, lower: int, end: int) -> int:
    """The first line start at or after `offset` and before `end`, or `end`; `lower` is a line start at or before `offset`."""
    if offset <= lower:
        return lower
    position = offset - 1
    while position < end:
        chunk = os.pread(descriptor, min(PROBE_BYTES, end - position), position)
        if not chunk:
            return end
        found = chunk.find(b"\n")
        if found >= 0:
            return min(position + found + 1, end)
        position += len(chunk)
    return end


def line_at(descriptor: int, start: int, end: int) -> tuple[bytes, int]:
    """The line that starts at `start`, without its newline, and the start of the line after it, reading no further than `end`."""
    parts: list[bytes] = []
    position = start
    while position < end:
        chunk = os.pread(descriptor, min(PROBE_BYTES, end - position), position)
        if not chunk:
            break
        found = chunk.find(b"\n")
        if found >= 0:
            parts.append(chunk[:found])
            return b"".join(parts), position + found + 1
        parts.append(chunk)
        position += len(chunk)
    return b"".join(parts), end


def last_line_start(descriptor: int, lower: int, end: int) -> int:
    """The start of the last line that ends at or before `end`, a line start or the file's end, reading back no further than `lower`, a line start."""
    position = end - 1
    while position > lower:
        start = max(lower, position - PROBE_BYTES)
        chunk = os.pread(descriptor, position - start, start)
        found = chunk.rfind(b"\n")
        if found >= 0:
            return start + found + 1
        position = start
    return lower


def first_line(descriptor: int, low: int, high: int, satisfies: Callable[[bytes], bool]) -> int:
    """The start of the first line in `[low, high)` that `satisfies`, or `high` when none does, given that `low` is a line start and that every line that satisfies follows every line that does not. Each step reads the line after the range's midpoint with one or two `pread`s."""
    while low < high:
        middle = (low + high) // 2
        start = next_line_start(descriptor, middle, low, high)
        if start >= high:
            line, after = line_at(descriptor, low, high)
            if satisfies(line):
                return low
            low = after
            continue
        line, after = line_at(descriptor, start, high)
        if satisfies(line):
            high = start
        else:
            low = after
    return low


def _field(line: bytes, index: int) -> str:
    parts = line.split(b"\t", index + 1)
    return parts[index].decode("utf-8", "replace") if len(parts) > index else ""


def audit_sections(
    descriptor: int, start: int, end: int, rank: Callable[[str], int], part: int = 0
) -> list[Section] | str:
    """The sections of one part from byte `start`, the first line after its header, to `end`: each one's configuration and rank, from its first line, and its end, the first line of a higher rank (`first_line`). A segment's line (`part` above 0) is an upsert or a tombstone, its configuration and window one field later than a row's. A configuration with no rows has no section. Return why the part is not in sections instead when a section's rank is not above the one before it, or its last line is another configuration's. Whether every line inside a section is its configuration's, in order, is checked by the walk that reads it (`section_rows`)."""
    shift = 1 if part else 0
    sections: list[Section] = []
    position = start
    last = -1
    while position < end:
        line, _after = line_at(descriptor, position, end)
        config = _field(line, shift)
        ranked = rank(config)
        if ranked <= last:
            return f"the {config!r} rows at byte {position} of part {part} do not rank above the section before them"
        position_end = first_line(descriptor, position, end, lambda line: rank(_field(line, shift)) > ranked)
        last, _after = line_at(descriptor, last_line_start(descriptor, position, position_end), position_end)
        if _field(last, shift) != config:
            return f"the {config!r} rows from byte {position} of part {part} end in a row of {_field(last, shift)!r}"
        sections.append(Section(config, ranked, position, position_end, part))
        last = ranked
        position = position_end
    return sections


def window_ranges(
    descriptors: Sequence[int], sections: Sequence[Section], count: int
) -> list[tuple[WindowKey | None, WindowKey | None]]:
    """Split the windows into at most `count` ranges `[low, high)` of about equal bytes, the ends None for the first and the last, at the windows found at equal byte fractions of the largest section of any part."""
    if not sections:
        return []
    largest = max(sections, key=lambda section: section.end - section.start)
    descriptor = descriptors[largest.part]
    shift = 1 if largest.part else 0
    cuts: list[WindowKey] = []
    for part in range(1, count):
        offset = largest.start + (largest.end - largest.start) * part // count
        start = next_line_start(descriptor, offset, largest.start, largest.end)
        if start >= largest.end:
            continue
        line, _after = line_at(descriptor, start, largest.end)
        key = window_key(_field(line, 1 + shift))
        if not cuts or key > cuts[-1]:
            cuts.append(key)
    bounds: list[WindowKey | None] = [None, *cuts, None]
    return list(zip(bounds[:-1], bounds[1:]))


def section_rows(
    descriptors: Sequence[int], section: Section, start: int, stop: WindowKey | None = None
) -> Iterator[tuple[WindowKey, list[str], str]]:
    """Yield each row of `section` from byte `start` with its window's key, its six fields and its line without the newline, reading through its part's descriptor `READ_BYTES` at a time from the base and `SEGMENT_READ_BYTES` from a segment, until a window at or past `stop`. While it waits at a row it holds only that read's lines, as one list of strings, and the partial line after them, about 1.3 times the read for the audit's rows. A segment's upsert yields its row; its tombstone yields an empty field list and an empty line, which the merge across parts reads as the removal of the row at its key (`_merged`). A row of another configuration, one without six fields, a segment line that is neither an upsert nor a tombstone, a window that does not follow the one before it, a carriage return (which the in-memory source's text read takes as a line end), or bytes that are not UTF-8 raise `OrderBreak`."""
    descriptor = descriptors[section.part]
    segment = section.part > 0
    read_bytes = SEGMENT_READ_BYTES if segment else READ_BYTES
    config = section.config
    end = section.end
    position = start
    previous: WindowKey | None = None
    remainder = b""
    while position < end:
        chunk = os.pread(descriptor, min(read_bytes, end - position), position)
        if not chunk:
            raise OrderBreak(f"the audit ended at byte {position}, inside the {config!r} section")
        position += len(chunk)
        data = remainder + chunk if remainder else chunk
        del chunk
        if position < end:
            cut = data.rfind(b"\n")
            if cut < 0:
                remainder = data
                continue
            body, remainder = data[:cut], data[cut + 1 :]
        else:
            body, remainder = (data[:-1] if data.endswith(b"\n") else data), b""
        del data
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise OrderBreak(f"the {config!r} section holds bytes that are not UTF-8: {error}") from None
        del body
        if "\r" in text:
            raise OrderBreak(
                f"the {config!r} section holds a carriage return, which a text read takes as a line end"
            )
        lines = text.split("\n")
        del text
        for line in lines:
            if segment:
                op, _tab, line = line.partition("\t")
                fields = line.split("\t")
                if op == "-" and len(fields) == 2:
                    line = ""
                elif op != "+" or len(fields) != FIELDS:
                    raise OrderBreak(
                        f"the {config!r} section of part {section.part} holds the line {op}\t{line[:120]!r}"
                    )
            else:
                fields = line.split("\t")
                if len(fields) != FIELDS:
                    raise OrderBreak(f"the {config!r} section holds the row {line[:120]!r}")
            if fields[0] != config:
                raise OrderBreak(f"the {config!r} section holds the row {line[:120]!r}")
            field = fields[1]
            key = (len(field), field)
            if previous is not None and key <= previous:
                raise OrderBreak(f"the {config!r} section's windows do not strictly increase at {field}")
            if stop is not None and key >= stop:
                return
            previous = key
            yield key, (fields if line else []), line


_PAST: Row = ((sys.maxsize, ""), [], "")


def _tagged(part: int, cursor: Iterator[Row]) -> Iterator[tuple[WindowKey, int, list[str], str]]:
    for key, fields, line in cursor:
        yield key, -part, fields, line


def _latest(segments: Sequence[tuple[int, Iterator[Row]]]) -> Iterator[Row]:
    """The segments' rows merged among themselves by window, the latest part's row or tombstone alone at each."""
    last: WindowKey | None = None
    for key, _part, fields, line in heapq.merge(*(_tagged(part, cursor) for part, cursor in segments)):
        if key != last:
            last = key
            yield key, fields, line


def _merged(parts: Sequence[tuple[int, Iterator[Row]]]) -> Iterator[Row]:
    """One configuration's rows merged across its parts' cursors, each beside its part's number: at each window the latest part's row wins, and a tombstone there removes the row. As `row_delta.merge_plain_keyed` merges a head, the segments are merged among themselves first (`_latest`) and that one sparse stream is merged with the base's cursor, the part numbered 0 when the configuration has rows there, so a base row costs a window comparison or two however many parts there are."""
    base = next((cursor for part, cursor in parts if part == 0), None)
    segments = [(part, cursor) for part, cursor in parts if part]
    if not segments:
        if base is not None:
            yield from base
        return
    changes = _latest(segments)
    change = next(changes, _PAST)
    if base is not None:
        for row in base:
            key = row[0]
            if key < change[0]:
                yield row
                continue
            while change[0] < key:
                if change[2]:
                    yield change
                change = next(changes, _PAST)
            if change[0] == key:
                if change[2]:
                    yield change
                change = next(changes, _PAST)
            else:
                yield row
    while change is not _PAST:
        if change[2]:
            yield change
        change = next(changes, _PAST)


def _cursors(
    descriptors: Sequence[int],
    sections: Sequence[Section],
    low: WindowKey | None,
    high: WindowKey | None,
) -> tuple[list[int], list[Iterator[tuple[WindowKey, list[str], str]]]]:
    """Each configuration's merged cursor (`_merged`) over `[low, high)`, in configuration rank, beside the index in `sections` of its first section, which names it. Each part's cursor starts at its first window at or past `low`, found by `first_line`."""
    named: list[int] = []
    groups: list[list[tuple[int, Iterator[Row]]]] = []
    for index, section in enumerate(sections):
        start = section.start
        if low is not None:
            bound, shift = low, 1 if section.part else 0
            start = first_line(
                descriptors[section.part],
                section.start,
                section.end,
                lambda line: window_key(_field(line, 1 + shift)) >= bound,
            )
        cursor = (section.part, section_rows(descriptors, section, start, high))
        if named and sections[named[-1]].config == section.config:
            groups[-1].append(cursor)
        else:
            named.append(index)
            groups.append([cursor])
    return named, [_merged(group) for group in groups]


def lockstep(
    descriptors: Sequence[int],
    sections: Sequence[Section],
    low: WindowKey | None = None,
    high: WindowKey | None = None,
) -> Iterator[tuple[str, list[tuple[int, list[str], str]]]]:
    """Yield every window in `[low, high)` (every window when both are None) in window order, with its rows: `(section index, fields, line)` for each configuration that has the window, in configuration rank, the index that of the configuration's first section in `sections`, which run in rank and then part order. A configuration's rows in several parts are merged by window (`_merged`), so the walk reads the materialized audit one window at a time. A window not in the canonical form raises `OrderBreak`, as the cursors do."""
    named, cursors = _cursors(descriptors, sections, low, high)
    heads = [next(cursor, None) for cursor in cursors]
    while True:
        least: WindowKey | None = None
        for head in heads:
            if head is not None and (least is None or head[0] < least):
                least = head[0]
        if least is None:
            return
        if CANONICAL_WINDOW.fullmatch(least[1]) is None:
            raise OrderBreak(
                f"the window {least[1]!r} is not written as four uppercase hex digits a code point"
            )
        rows: list[tuple[int, list[str], str]] = []
        for index, head in enumerate(heads):
            if head is not None and head[0] == least:
                rows.append((named[index], head[1], head[2]))
                heads[index] = next(cursors[index], None)
        yield least[1], rows


def materialized_lines(descriptors: Sequence[int], sections: Sequence[Section]) -> Iterator[str]:
    """The materialized audit's rows, each line without its newline, in the audit's own order: configuration by configuration in rank, each one's rows merged across its parts by window (`_merged`)."""
    _named, cursors = _cursors(descriptors, sections, None, None)
    for cursor in cursors:
        for _key, _fields, line in cursor:
            yield line


def parse_window(field: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in field.split(":"))


class FamilyLines:
    """A window's family lines as one suffix, `\\n<family>\\t<key>` for each family the window cites in name order, memoized a set of families: the same lines `unit_cache.UnitKeyer` appends to both store keys. A family is cited when every one of its `_`-joined components is a window letter's family, so a ligature family is cited with its letters."""

    def __init__(self, family_of: Mapping[int, str], family_keys: Mapping[str, str]) -> None:
        self._family_of = dict(family_of)
        self._keys = dict(family_keys)
        self._names = sorted(self._keys)
        self._suffixes: dict[frozenset[str], str] = {}

    def suffix(self, values: Iterable[int]) -> str:
        family_of = self._family_of
        families = frozenset(family_of[value] for value in values if value in family_of)
        found = self._suffixes.get(families)
        if found is None:
            keys = self._keys
            found = self._suffixes[families] = "".join(
                f"\n{name}\t{keys[name]}"
                for name in self._names
                if all(component in families for component in name.split("_"))
            )
        return found


def signature_key(fields: Sequence[str], suffix: str) -> int:
    """The ink-signature store key of one audit row from its raw fields, as an integer: the first 64 bits of the sha256 of its config, window and both rendered-name fields, tab-joined, then its window's family lines (`unit_cache.UnitKeyer.signature_key`)."""
    text = f"{fields[0]}\t{fields[1]}\t{fields[4]}\t{fields[5]}{suffix}"
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def input_key(lines: Iterable[str], suffix: str) -> bytes:
    """The unit store's input key of one unit from its run's audit lines, each without its newline, in run order: the sha256 of the lines joined by newlines, then its window's family lines (`unit_cache.UnitKeyer.key`)."""
    return hashlib.sha256(("\n".join(lines) + suffix).encode()).digest()


def sibling_window(rows: Sequence[tuple[int, Sequence[str], str]]) -> bool:
    """Whether a window's rows name two or more (baseline, new) pairs, so it holds two or more units, which the ink-duplicate merge compares."""
    if len(rows) < 2:
        return False
    first = (rows[0][1][4], rows[0][1][5])
    return any((fields[4], fields[5]) != first for _index, fields, _line in rows[1:])


def _key(value: Sequence[object] | None) -> WindowKey | None:
    return None if value is None else (int(value[0]), str(value[1]))  # pyright: ignore[reportArgumentType]


def _read_prior(stream, count: int) -> array:
    prior = array("Q", bytes(8 * count))
    view = memoryview(prior).cast("B")
    got = 0
    while got < len(view):
        read = stream.readinto(view[got : got + READ_BYTES])
        if not read:
            raise EOFError(f"the prior keys stopped after {got} of {len(view)} bytes")
        got += read
    view.release()
    return prior


def main() -> int:
    """Run one process of the key-only read; the module docstring describes its job and its replies."""
    started = time.perf_counter()
    source = sys.stdin.buffer
    job = json.loads(source.readline())
    prior = _read_prior(source, int(job["prior"]))
    descriptors = [int(descriptor) for descriptor in job["descriptors"]]
    sections = [Section(*section) for section in job["sections"]]
    family = FamilyLines({int(value): name for value, name in job["family_of"].items()}, job["family_keys"])
    out = sys.stdout.buffer
    hits: list[int] = []
    misses: list[list[object]] = []
    held = 0
    rows = sibling_rows = windows = 0

    def emit(record: dict) -> None:
        out.write(json.dumps(record, separators=(",", ":")).encode() + b"\n")

    def flush() -> None:
        nonlocal held
        if hits:
            emit({"hits": hits})
            hits.clear()
        if misses:
            emit({"misses": misses})
            misses.clear()
        held = 0

    count = len(prior)
    try:
        for field, found in lockstep(descriptors, sections, _key(job["low"]), _key(job["high"])):
            windows += 1
            rows += len(found)
            if not sibling_window(found):
                continue
            suffix = family.suffix(parse_window(field))
            for index, fields, _line in found:
                key = signature_key(fields, suffix)
                position = bisect_left(prior, key)
                sibling_rows += 1
                if position < count and prior[position] == key:
                    hits.append(position)
                    held += 8
                else:
                    misses.append([key, index, field])
                    held += 32 + len(field)
            if held >= FRAME_BYTES:
                flush()
    except OrderBreak as broken:
        flush()
        emit({"break": str(broken)})
    flush()
    emit(
        {
            "done": {
                "windows": windows,
                "rows": rows,
                "sibling_rows": sibling_rows,
                "seconds": round(time.perf_counter() - started, 3),
                "maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
        }
    )
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
