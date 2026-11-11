"""The divergence audit read as sorted sections, one window at a time, through the descriptors the caller holds, one a part. It imports only the standard library, so the corpus build's key-only read runs it as a process of its own (`python -m rebuild.review.audit_stream`) that holds this module and its job and nothing of the build's.

run_m1 writes the audit as a base and keyed segments under a head (`rebuild/pipeline/row_delta.py`'s plain keyed merge, `oracle.AuditWriter`), each part in sorted sections: one section a configuration, the sections in configuration rank (`audit._config_index`), each section's rows in strictly increasing window order, by the window's length, then its code points (`audit.triage_key`'s window terms). The base's lines are rows, and a segment's are upserts, `+\t<row>`, and tombstones, `-\t<config>\t<window>`, after its header line. Every window is written in `audit.format_codepoints`' form, four uppercase hex digits a code point, so a window field's length and then its text order windows as their code points do. A base stays plain until its first fold, and every segment and every folded base is a run of gzip members, the header line's and then one a section, which the head's member index lists (`row_delta.Part.members`). `audit_sections` finds each section's byte range in a plain part by bisecting on line starts with `os.pread` (`first_line`); a section held as a member is the member's byte range, which `audit.audit_layout` takes from the index, beside its inflated bytes (`Section.inflated`), and a cursor reads it by `os.pread` a piece at a time and inflates it with bounded output (`section_rows`). `Section.part` names the part, and `lockstep` walks the sections together, a configuration's sections across its parts merged by window (`_merged`: the latest part's row wins at a window, and a tombstone removes it), yielding each window's rows from every configuration that has the window, so it reads the materialized audit one window at a time; `materialized_lines` gives the same rows in the audit's own order. Every read is a `pread` on a held descriptor, never a reopening of a path, since a `--gates-only` pass can commit a new head during a build, and never a mapping, whose page touches count in the reader's resident set.

A file in any other shape is no error here: a cursor that meets a row of another configuration, a window that does not strictly increase, a window not in the canonical form, a row without six fields, a carriage return or bytes that are not UTF-8 raises `OrderBreak`, and the build reads the file through its in-memory source instead (`audit.load_audit_table`), which takes rows in any order. A member that does not inflate to what the index records is another matter: the bytes on disk are not the ones the writer wrote, so `MemberError`, naming the part and the section's configuration, fails the build, and no source reads the audit. Damage inside a member usually gives rows that break the walk's checks before zlib reaches the member's trailer, so a walk that meets a break inflates every member to its end before it lets the break through (`_check_members`), and the damage fails the build as `MemberError` there too.

`FamilyLines` gives a window's family lines, the suffix both store keys append to their lines (`unit_cache.UnitKeyer`), and `signature_key` and `input_key` compute the ink-signature store's key of a row and the unit store's input key of a unit from the audit's raw lines and fields, byte for byte as `UnitKeyer` computes them from the parsed rows.

Run as a module, it is one process of the key-only read (`main`): it reads its job from stdin, a JSON line naming every held descriptor of the audit's parts, which it inherits, the sections across them, its window range and the family keys, followed by the prior ink-signature store's sorted keys as raw `u64`s; walks its range in lockstep; keys every row of a window whose rows name two or more (baseline, new) pairs, the windows the ink-duplicate merge compares; bisects each key in the prior keys; and writes JSON lines to stdout a frame at a time, flushing each frame at the first window end after its rows' estimated size reaches `FRAME_BYTES`, so a frame holds at most `FRAME_BYTES` plus one window's rows: `hits`, the prior keys' positions it found, `misses`, `[key, section, window]` for each key it did not, then `break` with the reason if its range broke the layout, and `done` with its counts, seconds and resident peak; a member that fails to inflate ends the reply with `failed` and the `MemberError`'s message in place of `break` and `done`, and the process exits 1, which the build raises.
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
import zlib
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import NamedTuple

READ_BYTES = 1 << 20
# A segment's sections are read this much at a time, the base's `READ_BYTES`, a plain section's bytes or a member's inflated ones alike: a walk primes a cursor for every section of every part at once and each holds its read's lines while it waits (`section_rows`), and a segment's rows are 1.3% to 10% of the base's at 45 runes, so a read this size of a segment spans the windows of 0.6 to 5 MiB of the base's.
SEGMENT_READ_BYTES = 1 << 16
# A cursor over a section held as a gzip member reads the member this share of its read at a time, 64 KiB of a base section's and 4 KiB of a segment section's, which at the audit's 11 to 1 inflate to about two thirds of a read, so each holds one such piece beside its inflate state, about 45 KB, and its read's lines: at the audit's segment cap and overrun, six sections in each of 38 parts, the pieces and states come to about 11 MB.
PIECE_SHARE = 16
PROBE_BYTES = 4096
FRAME_BYTES = 1 << 19
FIELDS = 6
CANONICAL_WINDOW = re.compile(r"[0-9A-F]{4}(?::[0-9A-F]{4})*")

WindowKey = tuple[int, str]
Row = tuple[WindowKey, list[str], str]


class OrderBreak(Exception):
    """The audit is not in sorted sections, so the build reads it through its in-memory source."""


class MemberError(Exception):
    """A section held as a gzip member does not inflate to what the head's member index records: it fails to inflate, its gzip stream ends before its recorded length or runs past it, or it inflates to another length. The message names the part and the section's configuration, and the build fails rather than read the audit another way."""


class Section(NamedTuple):
    """One configuration's rows in one part: its configuration, its rank, the byte range `[start, end)` of its lines on disk, its part, the base's 0 and each segment's number, which names the descriptor it is read through and whether its lines are rows (the base's) or a segment's upserts and tombstones, and, for a section held as a gzip member, `inflated`, the member's inflated bytes, with `[start, end)` the member's bytes; None for a plain section."""

    config: str
    rank: int
    start: int
    end: int
    part: int = 0
    inflated: int | None = None


def _failure(section: Section, why: str) -> MemberError:
    part = "base" if section.part == 0 else f"segment {section.part}"
    held = f"the {section.config!r} section" if section.config else "its header line"
    return MemberError(f"the audit's {part} holds {held} in a member that {why}")


class _MemberReader:
    """The inflated bytes of `section`'s gzip member from its start, at most `read_bytes` a call, read by `os.pread` a `PIECE_SHARE` of `read_bytes` at a time and inflated by one `zlib.decompressobj` with gzip framing (`wbits` 31), which checks the member's CRC-32 and length trailer; a call at the member's end returns nothing. It holds the compressed piece not yet inflated and the inflate state, never a chunk it has returned, so a cursor waiting at a row holds only the lines it split. Read to its end, the member is checked against its section: one that fails to inflate, whose gzip stream ends before the section's end or runs past it, or that inflates past or short of `section.inflated` raises `MemberError`."""

    def __init__(self, descriptor: int, section: Section, read_bytes: int) -> None:
        assert section.inflated is not None
        self._descriptor = descriptor
        self._section = section
        self._inflated = section.inflated
        self._read_bytes = read_bytes
        self._piece = max(1, read_bytes // PIECE_SHARE)
        self._inflater: zlib._Decompress | None = zlib.decompressobj(31)
        self._position = section.start
        self._pending = b""
        self._total = 0

    def __call__(self) -> bytes:
        inflater = self._inflater
        if inflater is None:
            return b""
        section = self._section
        end = section.end
        while not inflater.eof:
            if not self._pending and self._position < end:
                self._pending = os.pread(
                    self._descriptor, min(self._piece, end - self._position), self._position
                )
                if not self._pending:
                    raise _failure(
                        section, f"ends at byte {self._position} of its part, inside its recorded length"
                    )
                self._position += len(self._pending)
            try:
                out = inflater.decompress(self._pending, self._read_bytes)
            except zlib.error as error:
                raise _failure(section, f"does not inflate: {error}") from None
            self._pending = inflater.unconsumed_tail
            if out:
                self._total += len(out)
                if self._total > self._inflated:
                    raise _failure(section, f"inflates past the {self._inflated} bytes its index records")
                return out
            if not self._pending and self._position >= end:
                break
        self._inflater = None
        if not inflater.eof:
            raise _failure(section, f"runs past its recorded {end - section.start} bytes")
        if inflater.unused_data or self._pending or self._position < end:
            raise _failure(section, f"ends before its recorded {end - section.start} bytes")
        if self._total != self._inflated:
            raise _failure(
                section, f"inflates to {self._total} bytes where its index records {self._inflated}"
            )
        return b""


def member_bytes(descriptor: int, section: Section) -> bytes:
    """A small member's whole inflated bytes, checked as `_MemberReader` checks a member it reads to its end: a part's header line's."""
    return b"".join(iter(_MemberReader(descriptor, section, READ_BYTES), b""))


def _check_members(descriptors: Sequence[int], sections: Sequence[Section]) -> None:
    """Inflate every section held as a gzip member to its end, discarding what it gives, so a member that does not inflate to what its index records raises `MemberError`: what a walk that met an `OrderBreak` runs before it lets the break through, since damage inside a member gives rows that break the walk's checks before zlib reaches the member's trailer, and the break alone would send the build to its in-memory source over the same member."""
    for section in sections:
        if section.inflated is not None:
            read = _MemberReader(descriptors[section.part], section, READ_BYTES)
            while read():
                pass


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
    """The sections of one plain part from byte `start`, the first line after its header, to `end`: each one's configuration and rank, from its first line, and its end, the first line of a higher rank (`first_line`). A segment's line (`part` above 0) is an upsert or a tombstone, its configuration and window one field later than a row's. A configuration with no rows has no section. Return why the part is not in sections instead when a section's rank is not above the one before it, or its last line is another configuration's. Whether every line inside a section is its configuration's, in order, is checked by the walk that reads it (`section_rows`)."""
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


def _content(section: Section) -> int:
    """A section's lines' bytes: a plain section's byte range, or a member's inflated bytes."""
    return section.end - section.start if section.inflated is None else section.inflated


def _lines_at(read: Callable[[], bytes], targets: Sequence[int]) -> list[bytes]:
    """The line, without its newline, that starts at the first line start at or after each of `targets`, ascending offsets into the bytes `read` returns end to end, a chunk a call and nothing at their end, a line start being the stream's start or the byte after a newline; a target with no line start at or after it finds none, and neither does any after it. It holds the bytes from the last line it found or the last target on, about a chunk or two."""
    buffer = b""
    origin = 0
    starts_line = True
    found: list[bytes] = []
    for target in targets:
        if target > origin or not starts_line:
            while origin + len(buffer) < target:
                origin, starts_line = origin + len(buffer), False
                buffer = read()
                if not buffer:
                    return found
            at = max(target - 1 - origin, 0)
            while (newline := buffer.find(b"\n", at)) < 0:
                at = len(buffer)
                chunk = read()
                if not chunk:
                    return found
                buffer += chunk
            buffer, origin, starts_line = buffer[newline + 1 :], origin + newline + 1, True
        while (end := buffer.find(b"\n")) < 0:
            chunk = read()
            if not chunk:
                end = len(buffer)
                break
            buffer += chunk
        if not buffer:
            return found
        found.append(buffer[:end])
    return found


def window_ranges(
    descriptors: Sequence[int], sections: Sequence[Section], count: int
) -> list[tuple[WindowKey | None, WindowKey | None]]:
    """Split the windows into at most `count` ranges `[low, high)` of about equal bytes, the ends None for the first and the last, at the windows found at equal fractions of the lines' bytes of the largest section of any part, by its lines' bytes: a plain section's found by `pread` at each fraction, a section held as a gzip member's by inflating the member once, through bounded output, as far as its last fraction (`_lines_at`). The fractions fall on a plain section's bytes and on a member's inflated lines alike, so a section's lines give the same cuts whichever way it is held."""
    if not sections:
        return []
    largest = max(sections, key=_content)
    descriptor = descriptors[largest.part]
    shift = 1 if largest.part else 0
    if largest.inflated is None:
        lines: list[bytes] = []
        for part in range(1, count):
            offset = largest.start + (largest.end - largest.start) * part // count
            start = next_line_start(descriptor, offset, largest.start, largest.end)
            if start >= largest.end:
                continue
            lines.append(line_at(descriptor, start, largest.end)[0])
    else:
        lines = _lines_at(
            _MemberReader(descriptor, largest, READ_BYTES),
            [largest.inflated * part // count for part in range(1, count)],
        )
    cuts: list[WindowKey] = []
    for line in lines:
        key = window_key(_field(line, 1 + shift))
        if not cuts or key > cuts[-1]:
            cuts.append(key)
    bounds: list[WindowKey | None] = [None, *cuts, None]
    return list(zip(bounds[:-1], bounds[1:]))


def _plain_reader(descriptor: int, section: Section, start: int, read_bytes: int) -> Callable[[], bytes]:
    """A plain section's bytes from byte `start` to its end, `read_bytes` a call by `os.pread`, and nothing at its end."""
    position, end = start, section.end

    def read() -> bytes:
        nonlocal position
        if position >= end:
            return b""
        chunk = os.pread(descriptor, min(read_bytes, end - position), position)
        if not chunk:
            raise OrderBreak(f"the audit ended at byte {position}, inside the {section.config!r} section")
        position += len(chunk)
        return chunk

    return read


def _from_window(body: bytes, low: WindowKey, shift: int) -> bytes:
    """`body`, whole lines without the last newline, from its first line whose window is at or past `low`, or nothing when its last line's is not, taking a line's window as `first_line`'s bisection does."""
    if window_key(_field(body[body.rfind(b"\n") + 1 :], 1 + shift)) < low:
        return b""
    start = 0
    while True:
        end = body.find(b"\n", start)
        if window_key(_field(body[start:] if end < 0 else body[start:end], 1 + shift)) >= low:
            return body[start:]
        start = end + 1


def section_rows(
    descriptors: Sequence[int],
    section: Section,
    start: int,
    stop: WindowKey | None = None,
    low: WindowKey | None = None,
) -> Iterator[tuple[WindowKey, list[str], str]]:
    """Yield each row of `section` from byte `start` with its window's key, its six fields and its line without the newline, reading through its part's descriptor `READ_BYTES` at a time from the base and `SEGMENT_READ_BYTES` from a segment, until a window at or past `stop`. A section held as a gzip member is read from its start, its member inflated with output bounded by the same read (`_MemberReader`), and checked against its index as the cursor reaches its end, a failure raising `MemberError`; with `low`, the rows below it are passed over a read at a time, their lines left unsplit but the last of each read, as a cursor over a plain section starts past them by bisection (`_cursors`). While it waits at a row it holds only that read's lines, as one list of strings, and the partial line after them, about 1.3 times the read for the audit's rows, and over a member one compressed piece and the inflate state beside them (`PIECE_SHARE`). A segment's upsert yields its row; its tombstone yields an empty field list and an empty line, which the merge across parts reads as the removal of the row at its key (`_merged`). A row of another configuration, one without six fields, a segment line that is neither an upsert nor a tombstone, a window that does not follow the one before it, a carriage return (which the in-memory source's text read takes as a line end), or bytes that are not UTF-8 raise `OrderBreak`."""
    descriptor = descriptors[section.part]
    segment = section.part > 0
    read_bytes = SEGMENT_READ_BYTES if segment else READ_BYTES
    config = section.config
    previous: WindowKey | None = None
    read = (
        _plain_reader(descriptor, section, start, read_bytes)
        if section.inflated is None
        else _MemberReader(descriptor, section, read_bytes)
    )
    remainder = b""
    while True:
        chunk = read()
        if chunk:
            data = remainder + chunk if remainder else chunk
            del chunk
            cut = data.rfind(b"\n")
            if cut < 0:
                remainder = data
                continue
            body, remainder = data[:cut], data[cut + 1 :]
            del data
        elif remainder:
            body, remainder = remainder, b""
        else:
            return
        if low is not None:
            body = _from_window(body, low, 1 if segment else 0)
            if not body:
                continue
            low = None
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
    """Each configuration's merged cursor (`_merged`) over `[low, high)`, in configuration rank, beside the index in `sections` of its first section, which names it. Each plain section's cursor starts at its first window at or past `low`, found by `first_line`; a section held as a gzip member cannot be bisected, so its cursor starts at the member's start and passes over the rows below `low` as it inflates them (`section_rows`)."""
    named: list[int] = []
    groups: list[list[tuple[int, Iterator[Row]]]] = []
    for index, section in enumerate(sections):
        start, below = section.start, None
        if low is not None and section.inflated is not None:
            below = low
        elif low is not None:
            bound, shift = low, 1 if section.part else 0
            start = first_line(
                descriptors[section.part],
                section.start,
                section.end,
                lambda line: window_key(_field(line, 1 + shift)) >= bound,
            )
        cursor = (section.part, section_rows(descriptors, section, start, high, below))
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
    """Yield every window in `[low, high)` (every window when both are None) in window order, with its rows: `(section index, fields, line)` for each configuration that has the window, in configuration rank, the index that of the configuration's first section in `sections`, which run in rank and then part order. A configuration's rows in several parts are merged by window (`_merged`), so the walk reads the materialized audit one window at a time. A window not in the canonical form raises `OrderBreak`, as the cursors do, once every section held as a gzip member has inflated to what its index records (`_check_members`)."""
    named, cursors = _cursors(descriptors, sections, low, high)
    try:
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
    except OrderBreak:
        _check_members(descriptors, sections)
        raise


def materialized_lines(descriptors: Sequence[int], sections: Sequence[Section]) -> Iterator[str]:
    """The materialized audit's rows, each line without its newline, in the audit's own order: configuration by configuration in rank, each one's rows merged across its parts by window (`_merged`). A break raises `OrderBreak` once every section held as a gzip member has inflated to what its index records (`_check_members`)."""
    _named, cursors = _cursors(descriptors, sections, None, None)
    try:
        for cursor in cursors:
            for _key, _fields, line in cursor:
                yield line
    except OrderBreak:
        _check_members(descriptors, sections)
        raise


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
    except MemberError as failed:
        flush()
        emit({"failed": str(failed)})
        out.flush()
        return 1
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
