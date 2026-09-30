"""Append-only log of changes to the review app's verdict store.

After each write to verdicts-autosave.json, the review server's store (`rebuild.review.verdict_store`) and `rebuild.tools.merge_verdicts` append the change to verdicts-journal.ndjson: one event line (source, time, manifest stamp) followed by one line per changed verdict. Clears get their own lines, because the store files cannot represent them (a cleared verdict is absent). A base event carries the full store instead of a diff. One is written at every corpus-stamp change, and one seeds a new journal when the store it starts from is not empty, so `replay(as_of=...)` can reconstruct the store at any recorded moment from the journal alone. `rebuild.tools.merge_verdicts --restore-as-of` uses that replay to undo a bad merge, an overwritten store, or an accidental clear.

An append that crashes can leave the file ending in a line with no newline. Each append holds an exclusive `flock` on the file and first ends the file on a newline: a final line that parses as an entry keeps its bytes and gets the newline, and any other final line is cut off, so the lines appended after it stay readable. A base event names how many set lines follow it, and one followed by fewer is torn. The store from a torn base until the next complete base is unknown: replay raises `JournalGap` for a moment in that span and is exact again from the next complete base on, and compaction never starts the journal at a torn base. A writer that records the journal's length and inode before it appends (the land, `rebuild.review.landing`) lets the recovery of a land that died mid-append cut the journal back to that length (`truncate_to`) before it appends the land's event again.

Every writer appends while it holds the verdict store's lock (`rebuild.review.store_lock`), so the long reads that retention makes run without it. `scan_events` reads the journal's events once without the lock and then only the tail appended since, under it. Compaction is two phases: `compact_prepare` copies the kept lines to a temporary file without the lock, and `compact_finish` copies the tail appended since and replaces the journal under it, so an append made during the copy is kept.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EXPORT_FORMAT = "ams-review-verdicts/1"
JOURNAL_NAME = "verdicts-journal.ndjson"
_TAIL_BLOCK = 1 << 16
_RESUME_CHECK_BYTES = 1 << 16
_COPY_BLOCK = 1 << 20


def now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_by_unit(verdicts) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for record in verdicts:
        if not isinstance(record, dict):
            continue
        unit = record.get("unit")
        if not isinstance(unit, str):
            continue
        if unit not in best or (record.get("at") or "") > (best[unit].get("at") or ""):
            best[unit] = record
    return best


def _signature(record: dict) -> tuple:
    return (record.get("verdict"), record.get("note") or "", record.get("at") or "")


def _set_line(record: dict) -> dict:
    return {
        "kind": "set",
        "unit": record["unit"],
        "verdict": record.get("verdict"),
        "note": record.get("note") or "",
        "at": record.get("at") or "",
    }


def _event_line(*, source, at, stamp, base, stashed, sets, clears) -> dict:
    return {
        "kind": "event",
        "source": source,
        "at": at,
        "stamp": stamp,
        "base": base,
        "stashed": stashed,
        "sets": sets,
        "clears": clears,
    }


def record_transition(
    journal_path,
    *,
    source: str,
    stamp: str,
    old_stamp: str | None,
    old_verdicts,
    new_verdicts,
    stashed: str | None = None,
    at: str | None = None,
) -> dict:
    """Append the change from the store's previous content to its new content. A same-stamp change is written as a diff (sets and clears). A stamp change is written as a base event holding the full new store, because unit ids from different stamps cannot be matched. When the journal file does not exist yet and the previous store has the same stamp and is not empty, a seed base event holding the previous store is written first, so replay is complete from the journal's first line."""
    journal_path = Path(journal_path)
    at = at or now_stamp()
    base = old_stamp != stamp
    old_records = {} if base else latest_by_unit(old_verdicts)
    new_records = latest_by_unit(new_verdicts)
    sets = [
        record
        for _, record in sorted(new_records.items())
        if base
        or record["unit"] not in old_records
        or _signature(old_records[record["unit"]]) != _signature(record)
    ]
    clears = [] if base else sorted(unit for unit in old_records if unit not in new_records)

    seed = old_records if not base and old_records and not journal_path.exists() else None
    recorded = _append(
        journal_path,
        source=source,
        at=at,
        stamp=stamp,
        base=base,
        stashed=stashed,
        sets=sets,
        clears=clears,
        seed=seed,
    )
    return {"base": base, "sets": len(sets), "clears": len(clears), "recorded": recorded}


def record_delta(
    journal_path, *, source: str, stamp: str, sets, clears, seed_records=None, at: str | None = None
) -> dict:
    """Append a same-stamp change given as the records set and the units cleared, without diffing two whole stores. `seed_records` is the store before the change, without the changed units. It is written as a seed base event only when the journal file does not exist yet, as in `record_transition`; a caller that knows the file exists passes None."""
    journal_path = Path(journal_path)
    at = at or now_stamp()
    sets = [record for record in sets if isinstance(record, dict) and isinstance(record.get("unit"), str)]
    sets.sort(key=lambda record: record["unit"])
    clears = sorted(clears)
    seed = seed_records if seed_records and not journal_path.exists() else None
    recorded = _append(
        journal_path,
        source=source,
        at=at,
        stamp=stamp,
        base=False,
        stashed=None,
        sets=sets,
        clears=clears,
        seed=seed,
    )
    return {"base": False, "sets": len(sets), "clears": len(clears), "recorded": recorded}


def _append(journal_path, *, source, at, stamp, base, stashed, sets, clears, seed) -> bool:
    lines: list[dict] = []
    if seed:
        lines.append(
            _event_line(source="seed", at=at, stamp=stamp, base=True, stashed=None, sets=len(seed), clears=0)
        )
        lines.extend(_set_line(record) for _, record in sorted(seed.items()))
    if base or sets or clears or stashed is not None:
        lines.append(
            _event_line(
                source=source,
                at=at,
                stamp=stamp,
                base=base,
                stashed=stashed,
                sets=len(sets),
                clears=len(clears),
            )
        )
        lines.extend(_set_line(record) for record in sets)
        lines.extend({"kind": "clear", "unit": unit} for unit in clears)
    if lines:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _end_on_a_newline(handle)
            for line in lines:
                handle.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
    return bool(lines)


def repair_tail(journal_path) -> None:
    """End the journal on a newline now, as the next append would, so a caller that records the journal's length before appending records the offset at which its own lines begin."""
    try:
        handle = Path(journal_path).open("r+b")
    except FileNotFoundError:
        return
    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _end_on_a_newline(handle)


def truncate_to(journal_path, length: int | None, inode: int | None) -> bool:
    """Cut the journal back to `length` bytes, removing what a writer that died had appended after it, and return whether anything was cut. Only the file with inode `inode` is cut: a journal compacted or replaced since the length was taken is left as it is, since the offset no longer marks the same line. A `length` of None means the journal did not exist when the length was taken, so the file that exists now is removed: every other writer finishes the dead writer's work before it appends (`landing.locked_store`), so that file is the dead writer's."""
    journal_path = Path(journal_path)
    try:
        stat = journal_path.stat()
    except FileNotFoundError:
        return False
    if length is None:
        journal_path.unlink()
        return True
    if stat.st_ino != inode or stat.st_size <= length:
        return False
    with journal_path.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.truncate(length)
    return True


def _end_on_a_newline(handle) -> None:
    """Make the file end on a newline, so the lines appended next are not glued onto a final line a crashed append left without one. A final line that parses as an entry lost only its newline, which a buffered write can flush apart from the rest of the line, and every reader already counts it, so it gets the newline. Any other final line is cut off."""
    end = handle.seek(0, os.SEEK_END)
    if end == 0:
        return
    handle.seek(end - 1)
    if handle.read(1) == b"\n":
        return
    line_start = 0
    position = end
    while position > 0:
        start = max(0, position - _TAIL_BLOCK)
        handle.seek(start)
        newline = handle.read(position - start).rfind(b"\n")
        if newline >= 0:
            line_start = start + newline + 1
            break
        position = start
    handle.seek(line_start)
    try:
        entry = json.loads(handle.read(end - line_start).decode("utf-8"))
    except ValueError:
        entry = None
    if isinstance(entry, dict):
        handle.seek(0, os.SEEK_END)
        handle.write(b"\n")
    else:
        handle.truncate(line_start)


def _expected_sets(entry: dict) -> int:
    sets = entry.get("sets")
    return sets if isinstance(sets, int) and not isinstance(sets, bool) else 0


def _read_lines(handle) -> Iterator[tuple[bytes, dict | None, bool]]:
    """Yield each line of the binary `handle` with its entry and whether parsing has stopped. The entry is the parsed object, or None for a blank line or one that parses to something other than an object. Parsing stops at the first line that does not decode or parse, which is flagged along with every line after it, so every reader agrees on where the parseable journal ends. Reading bytes extends that to a tail torn mid-character, which can happen because notes may hold non-ASCII text; a text-mode read would raise on such a tail before yielding a line."""
    stopped = False
    for line in handle:
        entry = None
        if not stopped and line.strip():
            try:
                parsed = json.loads(line.decode("utf-8"))
            except ValueError:
                stopped = True
            else:
                entry = parsed if isinstance(parsed, dict) else None
        yield line, entry, stopped


def _digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_before(handle, offset: int) -> str:
    """Return the digest of the up to `_RESUME_CHECK_BYTES` bytes of the binary `handle` just before `offset`, which a two-phase reader compares before it resumes at `offset`."""
    start = max(0, offset - _RESUME_CHECK_BYTES)
    handle.seek(start)
    return _digest_of(handle.read(offset - start))


def _iter_entries(journal_path):
    """Yield the journal's parseable entries, reading one line at a time so only one line is in memory. Scanning stops at the first line that does not decode or parse (`_read_lines`), so a tail torn by a crashed append is never misread. `scan_events` and `compact_prepare` split the file with the same `_read_lines`, so every reader agrees on where each line begins and where parsing stops."""
    try:
        handle = Path(journal_path).open("rb")
    except OSError:
        return
    with handle:
        for _, entry, stopped in _read_lines(handle):
            if stopped:
                return
            if entry is not None:
                yield entry


def iter_events(journal_path):
    for entry in _iter_entries(journal_path):
        if entry.get("kind") == "event":
            yield entry


class JournalGap(ValueError):
    """Raised by `replay` for a moment the journal cannot reconstruct: at or after a torn base event and before the next complete base event, or the journal's end when none follows."""

    def __init__(self, torn_at: str, resumes_at: str | None) -> None:
        self.torn_at = torn_at
        self.resumes_at = resumes_at
        until = f"the next complete base event, at {resumes_at}" if resumes_at else "the journal's end"
        super().__init__(
            f"the base event at {torn_at} is cut short of its set lines, so no moment from it until {until} can be replayed"
        )


def replay(journal_path, as_of: str | None = None) -> tuple[str | None, dict[str, dict]]:
    """Return (stamp, records) as of the last event whose `at` is lexicographically <= `as_of`. Events are appended in time order, so a truncated ISO prefix works as `as_of`. A None stamp with empty records means the journal holds no event at or before that moment. A base event followed by fewer set lines than it names is torn, and the store from it until the next complete base is unknown, so for an `as_of` in that span replay raises `JournalGap`, naming where the span ends; replay is exact again from the next complete base on. A replay whose `as_of` falls in a gap reads on to the gap's end to name it."""
    stamp: str | None = None
    records: dict[str, dict] = {}
    base: tuple[str, int] | None = None
    base_sets = 0
    gap_from: str | None = None
    crossed: str | None = None
    for entry in _iter_entries(journal_path):
        kind = entry.get("kind")
        if kind == "event":
            if base is not None:
                if base_sets < base[1]:
                    gap_from = gap_from or base[0]
                elif crossed is not None:
                    raise JournalGap(crossed, base[0])
                else:
                    gap_from = None
                base = None
            at = entry.get("at") or ""
            if crossed is None and as_of is not None and at > as_of:
                if gap_from is None:
                    break
                crossed = gap_from
            if entry.get("base"):
                base = (at, _expected_sets(entry))
                base_sets = 0
                if crossed is None:
                    stamp = entry.get("stamp")
                    records = {}
            elif gap_from is None:
                stamp = entry.get("stamp")
            continue
        if kind == "set" and base is not None:
            base_sets += 1
        if stamp is None or crossed is not None or (gap_from is not None and base is None):
            continue
        if kind == "set":
            unit = entry.get("unit")
            if isinstance(unit, str):
                records[unit] = {
                    "unit": unit,
                    "verdict": entry.get("verdict"),
                    "note": entry.get("note") or "",
                    "at": entry.get("at") or "",
                }
        elif kind == "clear":
            unit = entry.get("unit")
            if isinstance(unit, str):
                records.pop(unit, None)
    if base is not None:
        if base_sets < base[1]:
            gap_from = gap_from or base[0]
        elif crossed is not None:
            raise JournalGap(crossed, base[0])
        else:
            gap_from = None
    gap = crossed or gap_from
    if gap is not None:
        raise JournalGap(gap, None)
    return stamp, records


@dataclass(frozen=True)
class EventScan:
    """The events a scan of the journal read, from byte offset `start` of the file with inode `inode`, and `end`, the offset just past the last newline-terminated line it read. `check` is the digest of the bytes just before `end` (`_digest_before`). A final line with no newline that parses is read, as `_iter_entries` reads it, but `end` stops before it, so a scan resumed from `end` reads it again."""

    events: list[dict]
    start: int
    end: int
    inode: int | None
    check: str


def scan_events(journal_path, *, resume: EventScan | None = None) -> EventScan:
    """Return the journal's events, stopping where `_iter_entries` stops, at the first line that does not decode or parse. Given `resume`, an earlier scan of the same journal, it reads only what was appended since: from `resume.end` when the file has the same inode, is at least that long, and still holds the same bytes just before that offset, and from the start otherwise, which `start` then records. The byte check catches a writer that cut the journal shorter and appended past the old end again (`rekey_verdicts --undo`), which inode and length alone would miss. The caller scans without the store's lock, then resumes under it; every append holds that lock, so the second scan sees each line the first one missed."""
    try:
        handle = Path(journal_path).open("rb")
    except OSError:
        return EventScan([], 0, 0, None, _digest_of(b""))
    with handle:
        stat = os.fstat(handle.fileno())
        start = 0
        if (
            resume is not None
            and resume.inode == stat.st_ino
            and stat.st_size >= resume.end
            and _digest_before(handle, resume.end) == resume.check
        ):
            start = resume.end
        handle.seek(start)
        events: list[dict] = []
        end = start
        for line, entry, stopped in _read_lines(handle):
            if stopped:
                break
            if entry is not None and entry.get("kind") == "event":
                events.append(entry)
            if not line.endswith(b"\n"):
                break
            end += len(line)
        check = _digest_before(handle, end)
    return EventScan(events, start, end, stat.st_ino, check)


@dataclass(frozen=True)
class PreparedCompaction:
    """A compaction `compact_prepare` staged: the kept lines up to `copied_to` are already in `tmp`, and `compact_finish` appends the rest and replaces the journal. `check` is the digest of the journal's bytes just before `copied_to` (`_digest_before`). `tmp` is None when there is nothing to rewrite, and `untouched` is then the result."""

    journal_path: Path
    tmp: Path | None
    inode: int | None
    copied_to: int
    check: str
    floor_at: str | None
    dropped_lines: int
    copied_lines: int
    untouched: dict


def compact_prepare(journal_path, *, cutoff: str) -> PreparedCompaction:
    """Stage a rewrite of the journal to begin at the newest complete base event whose `at` is lexicographically at or before `cutoff` (an ISO-Z stamp), dropping every earlier line. A base event carries the full store, so replay and --restore-as-of stay exact for every moment from that base on, and every earlier moment becomes unrecoverable; that is why the caller chooses the cutoff. The scan counts each base's set lines as `replay` does, so a torn base is never chosen and a complete base after it can be, and it stops choosing where `_iter_entries` stops, at the first line that does not decode or parse. A journal with no complete base at or before the cutoff, or one that already starts at that base, stages nothing.

    It runs without the store's lock. It copies the kept lines byte for byte into a temporary file beside the journal, up to the end of the file's last newline-terminated line: every byte before that offset is final unless a writer cuts the journal shorter, because appends only add lines and the tail repair (`_end_on_a_newline`) only touches a final line with no newline. `compact_finish` checks for such a cut. The scan and the copy read one handle in fixed-size blocks, so memory use stays at one line plus one block.
    """
    journal_path = Path(journal_path)
    floor: tuple[int, str | None, int] | None = None
    candidate: tuple[int, str | None, int] | None = None
    expected_sets = 0
    base_sets = 0
    total_lines = 0
    complete_lines = 0
    offset = 0
    complete_end = 0

    def settle_candidate() -> None:
        nonlocal floor, candidate
        if candidate is not None and base_sets >= expected_sets:
            floor = candidate
        candidate = None

    def nothing(inode: int | None = None, kept_lines: int = 0) -> PreparedCompaction:
        untouched = {"compacted": False, "floor_at": None, "dropped_lines": 0, "kept_lines": kept_lines}
        return PreparedCompaction(journal_path, None, inode, 0, _digest_of(b""), None, 0, 0, untouched)

    try:
        handle = journal_path.open("rb")
    except OSError:
        return nothing()
    with handle:
        inode = os.fstat(handle.fileno()).st_ino
        for line, entry, stopped in _read_lines(handle):
            if stopped:
                settle_candidate()
            elif entry is not None:
                kind = entry.get("kind")
                if kind == "event":
                    settle_candidate()
                    if entry.get("base"):
                        at = entry.get("at")
                        expected_sets = _expected_sets(entry)
                        base_sets = 0
                        if (at or "") <= cutoff:
                            candidate = (total_lines, at, offset)
                elif kind == "set":
                    base_sets += 1
            offset += len(line)
            total_lines += 1
            if line.endswith(b"\n"):
                complete_end = offset
                complete_lines = total_lines
        settle_candidate()
        floor_index, floor_at, floor_offset = floor if floor is not None else (None, None, 0)
        if not floor_index:
            return nothing(inode, total_lines)
        tmp = journal_path.with_name(journal_path.name + ".tmp")
        handle.seek(floor_offset)
        with tmp.open("wb") as target:
            remaining = complete_end - floor_offset
            while remaining > 0:
                block = handle.read(min(_COPY_BLOCK, remaining))
                if not block:
                    break
                target.write(block)
                remaining -= len(block)
        check = _digest_before(handle, complete_end)
    return PreparedCompaction(
        journal_path,
        tmp,
        inode,
        complete_end,
        check,
        floor_at,
        floor_index,
        complete_lines - floor_index,
        {"compacted": False, "floor_at": None, "dropped_lines": 0, "kept_lines": 0},
    )


def compact_finish(
    prepared: PreparedCompaction, *, lock: contextlib.AbstractContextManager | None = None
) -> dict:
    """Finish a staged compaction under `lock`, the store's lock: append what the journal gained past `prepared.copied_to` to the temporary file, and replace the journal with it atomically. A journal that was replaced, cut shorter, or rewritten before `copied_to` since the scan (a different inode, a shorter file, or different bytes just before that offset) is left as it is, the staged file is removed, and the result says `replaced`. Returns whether it compacted, the floor's `at`, and the dropped and kept line counts."""
    if prepared.tmp is None:
        return dict(prepared.untouched)
    journal_path = prepared.journal_path
    try:
        with lock if lock is not None else contextlib.nullcontext():
            try:
                source = journal_path.open("rb")
            except OSError:
                return {**prepared.untouched, "replaced": True}
            with source:
                stat = os.fstat(source.fileno())
                if (
                    stat.st_ino != prepared.inode
                    or stat.st_size < prepared.copied_to
                    or _digest_before(source, prepared.copied_to) != prepared.check
                ):
                    return {**prepared.untouched, "replaced": True}
                tail_lines = 0
                last = b"\n"
                source.seek(prepared.copied_to)
                with prepared.tmp.open("ab") as target:
                    while block := source.read(_COPY_BLOCK):
                        target.write(block)
                        tail_lines += block.count(b"\n")
                        last = block[-1:]
            if last != b"\n":
                tail_lines += 1
            os.replace(prepared.tmp, journal_path)
    finally:
        prepared.tmp.unlink(missing_ok=True)
    return {
        "compacted": True,
        "floor_at": prepared.floor_at,
        "dropped_lines": prepared.dropped_lines,
        "kept_lines": prepared.copied_lines + tail_lines,
    }


def compact(journal_path, *, cutoff: str, lock: contextlib.AbstractContextManager | None = None) -> dict:
    """Compact the journal in one call: `compact_prepare` without the lock, then `compact_finish` under `lock`."""
    return compact_finish(compact_prepare(journal_path, cutoff=cutoff), lock=lock)


def payload_for(stamp: str, records: dict[str, dict], exported_at: str | None = None) -> dict:
    verdicts = [
        {
            "unit": record["unit"],
            "verdict": record.get("verdict"),
            "note": record.get("note") or "",
            "at": record.get("at") or "",
        }
        for _, record in sorted(records.items())
    ]
    return {
        "format": EXPORT_FORMAT,
        "manifest_generated_at": stamp,
        "exported_at": exported_at or now_stamp(),
        "verdicts": verdicts,
    }
