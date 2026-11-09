"""Append-only log of changes to the review app's verdict store.

After each write to verdicts-autosave.json, the review server's store (`rebuild.review.verdict_store`) and `rebuild.tools.merge_verdicts` append the change to verdicts-journal.ndjson: one event line (source, time, manifest stamp) followed by one line per changed unit. A set line holds the unit's new record, which removes any tombstone the store kept for it. Each writer dumps its set lines from the records it holds but the land's walk over the stores as streams, which splices each from the record's line in the store it wrote, `"kind": "set"` and then that line's fields, the bytes the dump gives, and dumps only a seed's, from the store it replaced; `record_transition` writes both from one set of decisions (`transition_shape`, over a `Transition`). A clear line names a unit the store holds no record for after the change, either because the record went or because the unit's tombstone changed, and its `at` is the tombstone the store keeps for the unit (the time of the clear, the store's `cleared` list), or null when it keeps none. A base event carries the full store instead of a diff: its set lines hold the records, and the event line's `cleared` list holds the tombstones. Every event line a writer appends also carries the store's `accepted` field, the accepted list the store pairs with (`rebuild.review.accepted`), whenever the store names one, so `replay_with_list` gives back the field with the store and a restore writes a store that names its list. The journal holds the store's own records and tombstones and its override section, the records and tombstones on listed ids, as one store, whichever part a set or clear line's unit is in (`accepted.joined`), since the list partitions the ids; a restore splits a replayed store by the list again (`accepted.split`). The close-out (`rebuild.tools.close_out`) replaces the journal with a new one that starts at one base event of the store it leaves, `source: close-out`. One seeds a new journal when the store it starts from is not empty, every writer but the land writes one at each corpus-stamp change, and a restore writes one at the same stamp too when the store it replaces holds a record `verdict_store.well_formed` refuses (`merge_verdicts._apply_restore`). The land (`rebuild.review.landing`) writes one only when the journal is due a new one (`base_due`) or a diff would be at least as long (`record_transition`), and otherwise journals a stamp change as the sets and clears against the store it replaces, because a unit's id is its content key and names the same unit on either stamp. So `replay_store(as_of=...)` can reconstruct the store, its records and its tombstones, at any recorded moment from the journal alone. Journal lines without those fields record the tombstones only in part, and their replay can differ from the store. A clear line with no `at` field takes its event's `at` as the tombstone, which is when the writer journaled the change, not when the unit was cleared: for a clear a closed tab's outbox sent when the app next opened, it can be days after the store's own tombstone, so its replay keeps out a record dated between the two that the store's own would let in, and for a line from a land or a restore that names a record the writer dropped without a clear (a unit a land that moved the stamp dropped off the new corpus, a skip its carry dropped, or a record a restore left out), its replay lays a tombstone the store never held. A base event with no `cleared` list holds no tombstones, so a replay from it on lacks the tombstones of the clears made before it, and while the journal's last base is one, the land is due a base (`base_due`). Lines in that form record a tombstone only where a record went, so their replay lacks one the store gained on a unit holding no record (as a merge's `clears` add them) and keeps one the store dropped (as a land drops those off its corpus). `rebuild.tools.merge_verdicts --restore-as-of` uses that replay to undo a bad merge, an overwritten store, or an accidental clear.

Those sets and clears replay to the landed store only while the journal replays to the store the land replaces, so a marker beside the store (`unjournaled_marker_for`) says when the journal may lack a store write. The review server writes it before each save's store write and removes it once that save's append succeeds unless it was already there, so a save whose append fails, is cut short, or never runs leaves it, and `rebuild.tools.rekey_verdicts --undo` of a re-key that appended to a journal leaves one for the store the re-key journaled, its `--autosave`, whether it puts that store back or asks to have it put back by hand. While it exists `base_due` says a base is due, and the land removes it once it has journaled a base.

An append that crashes can leave the file ending in a line with no newline. Each append holds an exclusive `flock` on the file and first ends the file on a newline: a final line that parses as an entry keeps its bytes and gets the newline, and any other final line is cut off, so the lines appended after it stay readable. A base event names how many set lines follow it, and one followed by fewer is torn. The store from a torn base until the next complete base is unknown: replay raises `JournalGap` for a moment in that span and is exact again from the next complete base on, and compaction never starts the journal at a torn base. A writer that records the journal's length and inode before it appends (the land, `rebuild.review.landing`) lets the recovery of a land that died mid-append cut the journal back to that length (`truncate_to`), the start of the land's event line, before it journals the landed store again as a base event.

Every writer appends while it holds the verdict store's lock (`rebuild.review.store_lock`), so the long reads that retention makes run without it. `scan` reads the journal's events and the set lines after each once without the lock, and then only the tail appended since, under it. Compaction is two phases: `compact_prepare` resumes that scan to choose the floor and copies the kept lines to a temporary file without the lock, and `compact_finish` copies the tail appended since and replaces the journal under it, so an append made during the copy is kept. Retention saves the last scan's state (`save_scan_state`), rebased onto the compacted file when it compacted, and the next pass's scan resumes from it, so each pass parses only what was appended since the last one; the land resumes from the same saved state to decide whether a stamp change is due a base. A scan resumes only while the journal still holds the bytes just before where the state ends and every event line the state recorded (`_scan_handle`), and reads from the start otherwise.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from rebuild.pipeline import output_sink

EXPORT_FORMAT = "ams-review-verdicts/1"
SCAN_STATE_FORMAT = "ams-journal-scan/3"
JOURNAL_NAME = "verdicts-journal.ndjson"
AUTOSAVE_NAME = "verdicts-autosave.json"
UNJOURNALED_NAME = "unjournaled.json"
_TAIL_BLOCK = 1 << 16
_RESUME_CHECK_BYTES = 1 << 16
_COPY_BLOCK = 1 << 20
BASE_INTERVAL = timedelta(days=1)
HISTORY_DAYS = 90
CLOSE_OUT_DUE_DAYS = 60


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


def parse_cleared(data: dict | None) -> dict[str, str]:
    """Return the tombstones a verdicts document or a base event line holds (its top-level `cleared` list) as unit → the clear's `at`, skipping malformed entries."""
    cleared = data.get("cleared") if data else None
    if not isinstance(cleared, list):
        return {}
    return {
        entry["unit"]: entry["at"]
        for entry in cleared
        if isinstance(entry, dict) and isinstance(entry.get("unit"), str) and isinstance(entry.get("at"), str)
    }


def _tombstones(cleared: Mapping[str, str] | None, records: Mapping[str, dict]) -> dict[str, str]:
    return {unit: at for unit, at in (cleared or {}).items() if unit not in records}


def _cleared_list(cleared: Mapping[str, str]) -> list[dict]:
    return [{"unit": unit, "at": at} for unit, at in sorted(cleared.items())]


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


def _event_line(*, source, at, stamp, base, stashed, sets, clears, cleared=None, accepted=None) -> dict:
    line = {
        "kind": "event",
        "source": source,
        "at": at,
        "stamp": stamp,
        "base": base,
        "stashed": stashed,
        "sets": sets,
        "clears": clears,
    }
    if cleared is not None:
        line["cleared"] = _cleared_list(cleared)
    if accepted is not None:
        line["accepted"] = dict(accepted)
    return line


def _json_line(entry: dict) -> bytes:
    return (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")


class Transition(Protocol):
    """A change from the store's previous content to its new content as `record_transition` writes it: `records`, how many records the new store holds; `diff`, the count of set lines and of clear lines the change takes as a diff against the store the journal replays to, and whether that store holds a record or a tombstone, asked only of a change that is not a base by then; `tombstones`, the new store's tombstones, which a base event holds; `set_lines`, the set lines in unit order, every record's on a base and the diff's otherwise, and `clear_lines`, the diff's clear lines in unit order, each line its bytes with the newline; and `seed`, the previous store's record count, tombstones and set lines, for the seed base event. `record_transition` diffs two stores held in memory (`_Stores`), and the land (`rebuild.review.landing`) hands it one it worked out while walking its stores, whose set lines it splices from the record lines of the store it wrote."""

    records: int

    def diff(self) -> tuple[int, int, bool]: ...

    def tombstones(self) -> Mapping[str, str]: ...

    def set_lines(self, base: bool) -> Iterator[bytes]: ...

    def clear_lines(self) -> Iterator[bytes]: ...

    def seed(self) -> tuple[int, Mapping[str, str], Iterator[bytes]]: ...


class _Stores:
    """A transition given as the two stores' records and tombstones, which it diffs in memory."""

    def __init__(self, old_verdicts, new_verdicts, old_cleared, new_cleared) -> None:
        self._old = (old_verdicts, old_cleared)
        self.new_records = latest_by_unit(new_verdicts)
        self.new_tombstones = _tombstones(new_cleared, self.new_records)
        self.records = len(self.new_records)
        self.old_records: dict[str, dict] = {}
        self.old_tombstones: dict[str, str] = {}
        self.sets: list[dict] = []
        self.clears: dict[str, str | None] = {}

    def diff(self) -> tuple[int, int, bool]:
        old_verdicts, old_cleared = self._old
        old_records = self.old_records = latest_by_unit(old_verdicts)
        old_tombstones = self.old_tombstones = _tombstones(old_cleared, old_records)
        new_records, new_tombstones = self.new_records, self.new_tombstones
        self.sets = [
            record
            for _, record in sorted(new_records.items())
            if record["unit"] not in old_records
            or _signature(old_records[record["unit"]]) != _signature(record)
        ]
        gone = (set(old_records) | set(old_tombstones) | set(new_tombstones)) - set(new_records)
        self.clears = {
            unit: new_tombstones.get(unit)
            for unit in sorted(gone)
            if unit in old_records or old_tombstones.get(unit) != new_tombstones.get(unit)
        }
        return len(self.sets), len(self.clears), bool(old_records or old_tombstones)

    def tombstones(self) -> Mapping[str, str]:
        return self.new_tombstones

    def set_lines(self, base: bool) -> Iterator[bytes]:
        records = (record for _, record in sorted(self.new_records.items())) if base else self.sets
        for record in records:
            yield _json_line(_set_line(record))

    def clear_lines(self) -> Iterator[bytes]:
        for unit, tombstone in self.clears.items():
            yield _json_line({"kind": "clear", "unit": unit, "at": tombstone})

    def seed(self) -> tuple[int, Mapping[str, str], Iterator[bytes]]:
        records = self.old_records
        return (
            len(records),
            self.old_tombstones,
            (_json_line(_set_line(record)) for _, record in sorted(records.items())),
        )


@dataclass(frozen=True)
class Shape:
    """What `record_transition` writes for a change (`transition_shape`): whether its event is a base, how many set and clear lines follow the event line, whether a seed base event precedes it, and whether it writes its event line at all."""

    base: bool
    sets: int
    clears: int
    seed: bool
    event: bool


def transition_shape(
    change: Transition,
    *,
    moved: bool,
    old_stamp: str | None,
    base: bool | None,
    exists: bool,
    stashed: str | None,
) -> Shape:
    """Return what `record_transition` writes for `change` onto a journal that exists or not (`exists`), with `base` as the caller passed it. `base` True makes any change a base. A stamp change is a base unless `base` is False, and always one from no previous store, onto a journal that does not exist yet, or whose diff's sets and clears reach the new store's record count. Anything else is a diff, which `change` is asked for only then, so a base the caller forces never reads the previous store. A diff onto a journal that does not exist yet from a previous store that holds a record or a tombstone takes a seed base event first. The event line is written on a base, a stamp change, a named stash, or a diff that holds a line."""
    if base is None:
        base = moved
    elif moved and (old_stamp is None or not exists):
        base = True
    sets = clears = 0
    held = False
    if not base:
        sets, clears, held = change.diff()
        if moved and sets + clears >= change.records:
            base = True
    if base:
        sets, clears = change.records, 0
    event = base or moved or bool(sets) or bool(clears) or stashed is not None
    return Shape(base, sets, clears, not base and held and not exists, event)


def record_transition(
    journal_path,
    *,
    source: str,
    stamp: str,
    old_stamp: str | None,
    old_verdicts=(),
    new_verdicts=(),
    old_cleared: Mapping[str, str] | None = None,
    new_cleared: Mapping[str, str] | None = None,
    stashed: str | None = None,
    at: str | None = None,
    base: bool | None = None,
    accepted: Mapping[str, str] | None = None,
    transition: Transition | None = None,
) -> dict:
    """Append the change from the store's previous content to its new content. `old_cleared` and `new_cleared` are the two stores' tombstones (unit → the clear's `at`, as `parse_cleared` reads them), and a tombstone on a unit its store holds a record for is passed over, as the verdict store passes it over. A same-stamp change is written as a diff unless `base` is True: a set line for each record that is new or changed, and a clear line for each unit the new store holds no record for whose record went or whose tombstone changed, naming the new store's tombstone for it or null. A stamp change is written as a base event holding the full new store, its tombstones included, unless `base` is False: the land passes False when the journal is not due a base (`base_due`), and the stamp change is then written as a diff against `old_verdicts` and `old_cleared`, which must be the store the journal replays to, with its event line written even when the diff is empty, so replay moves to the new stamp. A stamp change from no previous store, or onto a journal that does not exist yet, is always a base, and so is one whose sets and clears reach the new store's record count, since its diff would be no shorter than the base. `base` True writes a base event on any change, the same stamp included, without reading `old_verdicts` or `old_cleared`: a restore passes it when the store it replaces holds a record `verdict_store.well_formed` refuses (`merge_verdicts._apply_restore`). `accepted` is the new store's `accepted` field, which every event line it writes carries. When the journal file does not exist yet and the previous store has the same stamp and holds a record or a tombstone, a diff is preceded by a seed base event holding the previous store, so replay is complete from the journal's first line. `transition` gives the change in place of the four stores' arguments (`Transition`), as the land does; `transition_shape` makes every one of these decisions for both. The result says whether a base was written and how many set and clear lines followed the event line."""
    journal_path = Path(journal_path)
    at = at or now_stamp()
    change = (
        transition
        if transition is not None
        else _Stores(old_verdicts, new_verdicts, old_cleared, new_cleared)
    )
    moved = old_stamp != stamp
    shape = transition_shape(
        change, moved=moved, old_stamp=old_stamp, base=base, exists=journal_path.exists(), stashed=stashed
    )

    def lines() -> Iterator[bytes]:
        if shape.seed:
            count, tombstones, seeded = change.seed()
            yield _json_line(
                _event_line(
                    source="seed",
                    at=at,
                    stamp=stamp,
                    base=True,
                    stashed=None,
                    sets=count,
                    clears=0,
                    cleared=tombstones,
                    accepted=accepted,
                )
            )
            yield from seeded
        if shape.event:
            yield _json_line(
                _event_line(
                    source=source,
                    at=at,
                    stamp=stamp,
                    base=shape.base,
                    stashed=stashed,
                    sets=shape.sets,
                    clears=shape.clears,
                    cleared=change.tombstones() if shape.base else None,
                    accepted=accepted,
                )
            )
            yield from change.set_lines(shape.base)
            if not shape.base:
                yield from change.clear_lines()

    recorded = shape.seed or shape.event
    if recorded:
        _write_lines(journal_path, lines())
    return {"base": shape.base, "sets": shape.sets, "clears": shape.clears, "recorded": recorded}


def record_delta(
    journal_path,
    *,
    source: str,
    stamp: str,
    sets,
    clears: Mapping[str, str | None],
    seed_records: Mapping[str, dict] | None = None,
    seed_cleared: Mapping[str, str] | None = None,
    at: str | None = None,
    accepted: Mapping[str, str] | None = None,
) -> dict:
    """Append a same-stamp change given as the records set and the units cleared, without diffing two whole stores. `clears` maps each cleared unit to the tombstone the store keeps for it, or None when it keeps none, as for a unit returned to its accepted record. `accepted` is the store's `accepted` field, which every event line it writes carries. `seed_records` and `seed_cleared` are the store's records and tombstones before the change, without the changed units. They are written as a seed base event only when the journal file does not exist yet and they hold a record or a tombstone, as in `record_transition`; a caller that knows the file exists passes None."""
    journal_path = Path(journal_path)
    at = at or now_stamp()
    sets = [record for record in sets if isinstance(record, dict) and isinstance(record.get("unit"), str)]
    sets.sort(key=lambda record: record["unit"])
    clears = dict(sorted(clears.items()))
    seed_records = dict(seed_records or {})
    seed_cleared = _tombstones(seed_cleared, seed_records)
    seed = (
        (seed_records, seed_cleared) if (seed_records or seed_cleared) and not journal_path.exists() else None
    )
    recorded = _append(
        journal_path,
        source=source,
        at=at,
        stamp=stamp,
        base=False,
        stashed=None,
        sets=sets,
        clears=clears,
        cleared=None,
        seed=seed,
        accepted=accepted,
    )
    return {"base": False, "sets": len(sets), "clears": len(clears), "recorded": recorded}


def _append(
    journal_path,
    *,
    source,
    at,
    stamp,
    base,
    stashed,
    sets,
    clears: Mapping[str, str | None],
    cleared: Mapping[str, str] | None,
    seed: tuple[Mapping[str, dict], Mapping[str, str]] | None,
    accepted: Mapping[str, str] | None = None,
) -> bool:
    lines: list[dict] = []
    if seed is not None:
        seed_records, seed_cleared = seed
        lines.append(
            _event_line(
                source="seed",
                at=at,
                stamp=stamp,
                base=True,
                stashed=None,
                sets=len(seed_records),
                clears=0,
                cleared=seed_cleared,
                accepted=accepted,
            )
        )
        lines.extend(_set_line(record) for _, record in sorted(seed_records.items()))
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
                cleared=(cleared or {}) if base else None,
                accepted=accepted,
            )
        )
        lines.extend(_set_line(record) for record in sets)
        lines.extend({"kind": "clear", "unit": unit, "at": tombstone} for unit, tombstone in clears.items())
    if lines:
        _write_lines(journal_path, (_json_line(line) for line in lines))
    return bool(lines)


def _write_lines(journal_path: Path, lines: Iterable[bytes]) -> None:
    """Append `lines` to the journal under an exclusive `flock`, after ending the file on a newline (`_end_on_a_newline`)."""
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _end_on_a_newline(handle)
        for line in lines:
            handle.write(line)


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
    """Yield the journal's parseable entries, reading one line at a time so only one line is in memory. Scanning stops at the first line that does not decode or parse (`_read_lines`), so a tail torn by a crashed append is never misread. `scan` and `compact_prepare` split the file with the same `_read_lines`, so every reader agrees on where each line begins and where parsing stops."""
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


def first_event_at(journal_path) -> str | None:
    """Return the `at` of the journal's first event, reading the file only up to that event's line, or None when there is no journal or its first event has no string `at`."""
    events = iter_events(journal_path)
    try:
        first = next(events, None)
    finally:
        events.close()
    at = first.get("at") if first is not None else None
    return at if isinstance(at, str) else None


def close_out_due(journal_path, now: datetime | None = None) -> str | None:
    """Return the warning retention's report and `make verdict-ready` give once the journal's first event is `CLOSE_OUT_DUE_DAYS` days old, or None before then or with no event to date. It names the day that event turns `HISTORY_DAYS` days old, from which retention's compaction (`compact_prepare`, under `artifact_cycle.journal_history_cutoff`) can copy the journal's kept history whole, and `make close-out`, which starts the journal again at one base event."""
    first = first_event_at(journal_path)
    try:
        began = datetime.fromisoformat(first.replace("Z", "+00:00")) if first is not None else None
    except ValueError:
        began = None
    if began is None or began.tzinfo is None:
        return None
    age = (now or datetime.now(timezone.utc)) - began
    if age < timedelta(days=CLOSE_OUT_DUE_DAYS):
        return None
    due = (began + timedelta(days=HISTORY_DAYS)).date().isoformat()
    when = "since" if age >= timedelta(days=HISTORY_DAYS) else "from"
    return (
        f"the verdict journal's first event, at {first}, is {age.days} days old, and {when} {due} retention's "
        "compaction can copy the journal's kept history whole; `make close-out` starts the journal again"
    )


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
    """Return the stamp and records `replay_store` gives, without the tombstones."""
    stamp, records, _ = replay_store(journal_path, as_of)
    return stamp, records


def replay_store(
    journal_path, as_of: str | None = None
) -> tuple[str | None, dict[str, dict], dict[str, str]]:
    """Return the stamp, records and tombstones `replay_with_list` gives, without the store's `accepted` field."""
    stamp, records, cleared, _ = replay_with_list(journal_path, as_of)
    return stamp, records, cleared


def replay_with_list(
    journal_path, as_of: str | None = None
) -> tuple[str | None, dict[str, dict], dict[str, str], dict | None]:
    """Return (stamp, records, cleared, accepted), the store as of the last event whose `at` is lexicographically <= `as_of`: its stamp, its records by unit, its tombstones (unit → the clear's `at`), and its `accepted` field, the accepted list it pairs with (`rebuild.review.accepted`), or None when it names none. Events are appended in time order, so a truncated ISO prefix works as `as_of`. A None stamp with empty records means the journal holds no event at or before that moment. A base event starts the store over from its set lines, its `cleared` list and its `accepted` field, an event that is not a base sets the field when it carries one and keeps it otherwise, a set line removes its unit's tombstone, and a clear line removes its unit's record and leaves the tombstone its `at` names (none for a null `at`, and the event's own `at` for a line with no `at` field). A base event followed by fewer set lines than it names is torn, and the store from it until the next complete base is unknown, so for an `as_of` in that span replay raises `JournalGap`, naming where the span ends; replay is exact again from the next complete base on. A replay whose `as_of` falls in a gap reads on to the gap's end to name it."""
    stamp: str | None = None
    records: dict[str, dict] = {}
    cleared: dict[str, str] = {}
    accepted: dict | None = None
    event_at = ""
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
                    cleared = parse_cleared(entry)
                    accepted = _accepted_field(entry)
            elif gap_from is None:
                stamp = entry.get("stamp")
                if "accepted" in entry:
                    accepted = _accepted_field(entry)
            event_at = at
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
                cleared.pop(unit, None)
        elif kind == "clear":
            unit = entry.get("unit")
            if isinstance(unit, str):
                records.pop(unit, None)
                tombstone = entry.get("at", event_at)
                if isinstance(tombstone, str):
                    cleared[unit] = tombstone
                else:
                    cleared.pop(unit, None)
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
    return stamp, records, cleared, accepted


def _accepted_field(entry: dict) -> dict | None:
    field = entry.get("accepted")
    return dict(field) if isinstance(field, dict) else None


@dataclass(frozen=True)
class EventMark:
    """One event line as a scan records it: the byte `offset` and the line index `line` at which it begins, its `at`, `stamp`, `base` and `stashed` (`at`, `stamp` and `stashed` are None where the line holds no string), `holds_cleared`, whether it holds a `cleared` list, as every base event `record_transition` writes does, the set lines it names (`sets`, as `_expected_sets` reads them), `counted`, the set lines that follow it before the next event or where parsing stops, and `digest`, the digest of the line's bytes with its newline, which a scan resuming from a state that holds the mark compares (`_holds`). A base event whose `counted` falls short of its `sets` is torn. The pending line's mark (`JournalScan.events`) has an empty `digest`, since no state holds it."""

    offset: int
    line: int
    at: str | None
    stamp: str | None
    base: bool
    stashed: str | None
    holds_cleared: bool
    sets: int
    counted: int
    digest: str


def _mark(entry: dict, offset: int, line: int, digest: str) -> EventMark:
    at = entry.get("at")
    stamp = entry.get("stamp")
    stashed = entry.get("stashed")
    return EventMark(
        offset,
        line,
        at if isinstance(at, str) else None,
        stamp if isinstance(stamp, str) else None,
        bool(entry.get("base")),
        stashed if isinstance(stashed, str) and stashed else None,
        isinstance(entry.get("cleared"), list),
        _expected_sets(entry),
        0,
        digest,
    )


@dataclass(frozen=True)
class ScanState:
    """What a scan of the journal read, for a later scan to resume from. `end` is the offset just past the last newline-terminated line read before parsing stopped, in the file with inode `inode`; `lines` is the number of lines before it, `check` the digest of the bytes just before it (`_digest_before`), and `marks` every event before it."""

    inode: int | None
    end: int
    lines: int
    check: str
    marks: tuple[EventMark, ...]


_EMPTY_STATE = ScanState(None, 0, 0, _digest_of(b""), ())


@dataclass(frozen=True)
class JournalScan:
    """A scan of the journal (`scan`): `state`, which a later scan resumes from, and what the file held past `state.end` when it was read. `start` is the offset this scan began reading at, 0 or the resumed state's `end`. `pending` is a final line with no newline that parses as an entry, read where parsing had not stopped: it counts, as `_iter_entries` counts it, but `state` stops before it, so a resumed scan reads it again. `complete_end` is the offset just past the file's last newline-terminated line, past any line where parsing stopped, and `complete_lines` and `total_lines` count the lines before it and every line."""

    state: ScanState
    start: int
    pending: dict | None
    complete_end: int
    complete_lines: int
    total_lines: int

    @property
    def events(self) -> tuple[EventMark, ...]:
        """Every event the scan read, the pending line included."""
        marks = self.state.marks
        kind = self.pending.get("kind") if self.pending is not None else None
        if kind == "event" and self.pending is not None:
            return (*marks, _mark(self.pending, self.state.end, self.state.lines, ""))
        if kind == "set" and marks:
            return (*marks[:-1], replace(marks[-1], counted=marks[-1].counted + 1))
        return marks


def _holds(handle, state: ScanState, size: int) -> bool:
    """Return whether the binary `handle`, `size` bytes long, still holds what `state` read where a resumed scan relies on it: at least `state.end` bytes, the same bytes just before that offset (`_digest_before`), and each event line `state` recorded, byte for byte at its offset. That is one short read per event."""
    if size < state.end or _digest_before(handle, state.end) != state.check:
        return False
    for mark in state.marks:
        handle.seek(mark.offset)
        if _digest_of(handle.readline()) != mark.digest:
            return False
    return True


def _scan_handle(handle, resume: ScanState | None) -> JournalScan:
    """Scan the binary `handle` from its start, or from `resume.end` when the file has `resume`'s inode and still holds what `resume` read (`_holds`). The byte checks catch a writer that cut the journal shorter and appended past the old end again (`rekey_verdicts --undo`, a land recovery's `truncate_to`), which inode and length alone would miss. Each such writer cuts the journal back to the start of the event line it appended, so the next append writes its own event line at the offset of an event the state recorded, and that line differs from the recorded one unless the append repeats it byte for byte, `at` included, within the same second. So the check on the event lines catches the cut even where the appends repeat the bytes just before the old end, as a re-key undone and run again does. Parsing stops where `_iter_entries` stops, at the first line that does not decode or parse, and the lines after it are only counted. Reading stops after the first line with no newline, as at the file's end, so a scan made while a writer appends never reads the rest of a line it began as a line of its own."""
    stat = os.fstat(handle.fileno())
    state = replace(_EMPTY_STATE, inode=stat.st_ino)
    if resume is not None and resume.inode == stat.st_ino and _holds(handle, resume, stat.st_size):
        state = resume
    marks = list(state.marks)
    current = marks.pop() if marks else None
    counted = current.counted if current is not None else 0
    start = offset = end = state.end
    lines = count = state.lines
    pending = None
    unterminated = False
    handle.seek(start)
    for line, entry, stopped in _read_lines(handle):
        if not line.endswith(b"\n"):
            pending, unterminated = entry, True
            break
        if entry is not None:
            kind = entry.get("kind")
            if kind == "event":
                if current is not None:
                    marks.append(replace(current, counted=counted))
                current = _mark(entry, offset, count, _digest_of(line))
                counted = 0
            elif kind == "set" and current is not None:
                counted += 1
        offset += len(line)
        count += 1
        if not stopped:
            end, lines = offset, count
    if current is not None:
        marks.append(replace(current, counted=counted))
    check = state.check if end == start else _digest_before(handle, end)
    return JournalScan(
        ScanState(stat.st_ino, end, lines, check, tuple(marks)),
        start,
        pending,
        offset,
        count,
        count + unterminated,
    )


def scan(journal_path, *, resume: ScanState | None = None) -> JournalScan:
    """Read the journal's events once, with the set lines that follow each, stopping where `_iter_entries` stops. Given `resume`, the state of an earlier scan of the same journal, it reads only what was appended since, and from the start when the file no longer holds what `resume` read (`_holds`); `start` records which. Retention scans without the store's lock and then resumes under it, and every append holds that lock, so the second scan sees each line the first one missed. Retention saves the state at the end of its pass (`save_scan_state`), so the next pass parses only what was appended since. The land resumes from that saved state the same way, before and under the lock, to decide whether a stamp change is due a base (`base_due`)."""
    try:
        handle = Path(journal_path).open("rb")
    except OSError:
        return JournalScan(_EMPTY_STATE, 0, None, 0, 0, 0)
    with handle:
        return _scan_handle(handle, resume)


def beside_store(store, name: str | Path, flag: str) -> Path:
    """Return `name`, a file or a relative path, beside the store at `store`: the default path of an output of a hand run on that store that no `flag` names, which for the live store is at the repo root. `rebuild.tools.merge_verdicts`, `rebuild.tools.verdict_update` and `rebuild.tools.rekey_verdicts` place these outputs by it: the journal (`journal_path_for`), the restore preview, the fill files, and the re-key's backups and kept id map, so a run on a copy named verdicts-autosave.json in another directory writes them beside the copy and none of them at the repo root, where `rebuild.review.status` counts every verdicts-*.json but the live store as a carry candidate; the verdict update's complaint list and the standing fill's memo keep their own defaults, and `rebuild.tools.verdict_update` says why. Only a store named exactly verdicts-autosave.json has these defaults, and for any other name this raises `ValueError` asking for `flag`, which the tools report as a usage error, because beside a store under another name in the live store's directory (a stash, or the preview a restore of the live store writes) the journal is the live store's and every other default lands among the live store's files, and on a case-insensitive filesystem another spelling of verdicts-autosave.json can name the live store itself."""
    store = Path(store)
    if store.name != AUTOSAVE_NAME:
        raise ValueError(
            f"--autosave {store} is not named {AUTOSAVE_NAME}, so {flag} has no default: pass {flag}"
        )
    return store.parent / name


def journal_path_for(store) -> Path:
    """Return the journal of the store at `store`: the verdicts-journal.ndjson beside it, which for the live store is the repo root's journal, and which only a store named exactly verdicts-autosave.json has (`beside_store` says why). `rebuild.tools.merge_verdicts`, `rebuild.tools.verdict_update` and `rebuild.tools.rekey_verdicts` take this journal when no `--journal` names one, and `rebuild.tools.rekey_verdicts` re-keys the files at its `--root` only when its `--autosave` is the verdicts-autosave.json there (`rekey_verdicts.verdict_files`) and refuses a verdicts-autosave.json its `--autosave` does not name passed with `--verdicts` or `--references` (`rekey_verdicts.another_store`), so a run on a copy named verdicts-autosave.json in another directory journals beside the copy and leaves the live store and its journal alone."""
    return beside_store(store, JOURNAL_NAME, "--journal")


def unjournaled_marker_for(store) -> Path:
    """Return the marker that says the store at `store` may hold a write the journal lacks: var/cycle/unjournaled.json beside it, which for the live store is under the repo root's gitignored var/."""
    return Path(store).parent / "var" / "cycle" / UNJOURNALED_NAME


def mark_unjournaled(store, writer: str) -> bool:
    """Write the marker for the store at `store` (`unjournaled_marker_for`), naming `writer` and the time, unless one exists, and return whether this call wrote it. A writer removes only a marker it wrote, once its own append succeeds, because one that was already there records an earlier write the journal missed, which only a base event puts right."""
    marker = unjournaled_marker_for(store)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        with marker.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"writer": writer, "at": now_stamp()}) + "\n")
    except FileExistsError:
        return False
    return True


def base_due(events, at: str, *, marker: Path | None = None) -> bool:
    """Return whether a land at `at` that moves the stamp writes its store as a base event, given the journal's `events` (`scan`): while `marker` (`unjournaled_marker_for`) exists, because the journal may then lack a store write and not replay to the store a diff would be taken against; when the journal holds no base event; when its last base event is torn, so a diff after it would replay only from the next complete base on (`replay`); when its last base event holds no `cleared` list, not even an empty one, so a replay from it on lacks the tombstones of the clears made before it until a base that holds them (`replay_store`); or when its last base is at least `BASE_INTERVAL` older than `at`. Otherwise the land journals the move as sets and clears. Compaction starts the journal at the newest complete base at or before its cutoff (`compact_prepare`), so the history a compacted journal keeps before the cutoff is at most the span between two bases. A base at most once per `BASE_INTERVAL` bounds that span to the interval plus the wait for the next land that moves the stamp, and leaves about one base per interval in the retention window rather than one per land. Measuring the interval from the last base, not from the start of a calendar day, never puts two bases minutes apart on either side of midnight."""
    if marker is not None and marker.exists():
        return True
    last = None
    for mark in events:
        if mark.base:
            last = mark
    if last is None or last.counted < last.sets or not last.holds_cleared:
        return True
    threshold = datetime.fromisoformat(at.replace("Z", "+00:00")) - BASE_INTERVAL
    return (last.at or "") <= threshold.isoformat().replace("+00:00", "Z")


def save_scan_state(path, state: ScanState | None) -> None:
    """Write `state` to `path` for a later scan to resume from, replacing the file atomically (`output_sink.replace_output`), or remove the file when there is no state to resume from. A file that already holds the state, as after a pass whose journal did not grow, is left in place. A state a scan produced is safe to save, because a later scan resumes from it only after checking it against the journal (`_holds`)."""
    path = Path(path)
    if state is None or state.inode is None:
        path.unlink(missing_ok=True)
        return
    payload = {
        "format": SCAN_STATE_FORMAT,
        "inode": state.inode,
        "end": state.end,
        "lines": state.lines,
        "check": state.check,
        "marks": [
            [
                mark.offset,
                mark.line,
                mark.at,
                mark.stamp,
                mark.base,
                mark.stashed,
                mark.holds_cleared,
                mark.sets,
                mark.counted,
                mark.digest,
            ]
            for mark in state.marks
        ],
    }
    output_sink.replace_output(path, json.dumps(payload, separators=(",", ":")), keep_if_equal=True)


def load_scan_state(path) -> ScanState | None:
    """Return the state `save_scan_state` wrote to `path`, or None when the file is missing, unreadable, or in another format, so the scan starts over."""
    try:
        payload = json.loads(Path(path).read_text())
        if payload.get("format") != SCAN_STATE_FORMAT:
            return None
        return ScanState(
            int(payload["inode"]),
            int(payload["end"]),
            int(payload["lines"]),
            str(payload["check"]),
            tuple(
                EventMark(
                    int(offset),
                    int(line),
                    at if isinstance(at, str) else None,
                    stamp if isinstance(stamp, str) else None,
                    bool(base),
                    stashed if isinstance(stashed, str) and stashed else None,
                    bool(holds_cleared),
                    int(sets),
                    int(counted),
                    str(digest),
                )
                for offset, line, at, stamp, base, stashed, holds_cleared, sets, counted, digest in payload[
                    "marks"
                ]
            ),
        )
    except OSError, ValueError, TypeError, KeyError, AttributeError:
        return None


def _rebased(state: ScanState, floor: EventMark, handle) -> ScanState:
    """Return `state` as it reads in the journal a compaction to `floor` writes, whose bytes are the old file's from `floor.offset` on: every offset and line index less the floor's, the events before the floor dropped, and the check over the bytes the new file holds before the new end, read from the binary `handle` on the old file. The inode is unset until `compact_finish` writes the file."""
    start = max(floor.offset, state.end - _RESUME_CHECK_BYTES)
    handle.seek(start)
    check = _digest_of(handle.read(state.end - start))
    marks = tuple(
        replace(mark, offset=mark.offset - floor.offset, line=mark.line - floor.line)
        for mark in state.marks
        if mark.offset >= floor.offset
    )
    return ScanState(None, state.end - floor.offset, state.lines - floor.line, check, marks)


@dataclass(frozen=True)
class PreparedCompaction:
    """A compaction `compact_prepare` staged: the kept lines up to `copied_to` are already in `tmp`, and `compact_finish` appends the rest and replaces the journal. `check` is the digest of the journal's bytes just before `copied_to` (`_digest_before`). `tmp` is None when there is nothing to rewrite, and `untouched` is then the result. `read` is the state of the scan that chose the floor, and `rebased` that state as it reads in the compacted journal (`_rebased`)."""

    journal_path: Path
    tmp: Path | None
    inode: int | None
    copied_to: int
    check: str
    floor_at: str | None
    dropped_lines: int
    copied_lines: int
    untouched: dict
    read: ScanState | None = None
    rebased: ScanState | None = None


def compact_prepare(journal_path, *, cutoff: str, resume: ScanState | None = None) -> PreparedCompaction:
    """Stage a rewrite of the journal to begin at the newest complete base event whose `at` is lexicographically at or before `cutoff` (an ISO-Z stamp), dropping every earlier line. A base event carries the full store, so replay and --restore-as-of stay exact for every moment from that base on, and every earlier moment becomes unrecoverable; that is why the caller chooses the cutoff. The scan counts each base's set lines as `replay` does, so a torn base is never chosen and a complete base after it can be, and it stops choosing where `_iter_entries` stops, at the first line that does not decode or parse. A journal with no complete base at or before the cutoff, or one that already starts at that base, stages nothing, and its result (`untouched`) gives that base's `at` as `floor_at` in the second case and None in the first. Given `resume`, an earlier scan's state, it reads only what was appended since (`_scan_handle`).

    It runs without the store's lock. It copies the kept lines byte for byte into a temporary file beside the journal, up to the end of the file's last newline-terminated line: every byte before that offset is final unless a writer cuts the journal shorter, because appends only add lines and the tail repair (`_end_on_a_newline`) only touches a final line with no newline. `compact_finish` checks for such a cut. The scan and the copy read one handle in fixed-size blocks, so memory use stays at one line plus one block, and the events' marks.
    """
    journal_path = Path(journal_path)

    def nothing(
        inode: int | None = None,
        kept_lines: int = 0,
        read: ScanState | None = None,
        floor_at: str | None = None,
    ) -> PreparedCompaction:
        untouched = {"compacted": False, "floor_at": floor_at, "dropped_lines": 0, "kept_lines": kept_lines}
        return PreparedCompaction(
            journal_path, None, inode, 0, _digest_of(b""), floor_at, 0, 0, untouched, read
        )

    try:
        handle = journal_path.open("rb")
    except OSError:
        return nothing()
    with handle:
        scanned = _scan_handle(handle, resume)
        inode = scanned.state.inode
        floor = None
        for mark in scanned.events:
            if mark.base and mark.counted >= mark.sets and (mark.at or "") <= cutoff:
                floor = mark
        if floor is None or not floor.line:
            return nothing(inode, scanned.total_lines, scanned.state, floor.at if floor is not None else None)
        floor_at, floor_offset, complete_end = floor.at, floor.offset, scanned.complete_end
        rebased = _rebased(scanned.state, floor, handle)
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
        floor.line,
        scanned.complete_lines - floor.line,
        {"compacted": False, "floor_at": None, "dropped_lines": 0, "kept_lines": 0},
        scanned.state,
        rebased,
    )


def compact_finish(
    prepared: PreparedCompaction, *, lock: contextlib.AbstractContextManager | None = None
) -> dict:
    """Finish a staged compaction under `lock`, the store's lock: append what the journal gained past `prepared.copied_to` to the temporary file, and replace the journal with it atomically. A journal that was replaced, cut shorter, or rewritten before `copied_to` since the scan (a different inode, a shorter file, or different bytes just before that offset) is left as it is, the staged file is removed, and the result says `replaced`. Returns whether it compacted, the floor's `at`, the dropped and kept line counts, and under `resume` the scan state a later scan resumes from: `prepared.rebased` on the new file once it compacted, `prepared.read` when there was nothing to rewrite, and None when the journal was replaced."""
    if prepared.tmp is None:
        return {**prepared.untouched, "resume": prepared.read}
    journal_path = prepared.journal_path
    try:
        with lock if lock is not None else contextlib.nullcontext():
            try:
                source = journal_path.open("rb")
            except OSError:
                return {**prepared.untouched, "replaced": True, "resume": None}
            with source:
                stat = os.fstat(source.fileno())
                if (
                    stat.st_ino != prepared.inode
                    or stat.st_size < prepared.copied_to
                    or _digest_before(source, prepared.copied_to) != prepared.check
                ):
                    return {**prepared.untouched, "replaced": True, "resume": None}
                tail_lines = 0
                last = b"\n"
                source.seek(prepared.copied_to)
                with prepared.tmp.open("ab") as target:
                    inode = os.fstat(target.fileno()).st_ino
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
        "resume": replace(prepared.rebased, inode=inode) if prepared.rebased is not None else None,
    }


def compact(journal_path, *, cutoff: str, lock: contextlib.AbstractContextManager | None = None) -> dict:
    """Compact the journal in one call: `compact_prepare` without the lock, then `compact_finish` under `lock`."""
    return compact_finish(compact_prepare(journal_path, cutoff=cutoff), lock=lock)


def payload_for(
    stamp: str,
    records: dict[str, dict],
    cleared: Mapping[str, str] | None = None,
    exported_at: str | None = None,
    accepted: Mapping[str, str] | None = None,
    overrides: Mapping | None = None,
) -> dict:
    """Return `records` as an ams-review-verdicts/1 document stamped `stamp`, with `cleared`, the tombstones on units it holds no record for, as its `cleared` list when there are any, `accepted`, the list the store pairs with (`rebuild.review.accepted`), as its `accepted` field when it names one, and `overrides`, the store's override section as a document holds it (`accepted.Overrides.field`), as its `overrides` section when it holds one."""
    verdicts = [
        {
            "unit": record["unit"],
            "verdict": record.get("verdict"),
            "note": record.get("note") or "",
            "at": record.get("at") or "",
        }
        for _, record in sorted(records.items())
    ]
    payload: dict = {
        "format": EXPORT_FORMAT,
        "manifest_generated_at": stamp,
        "exported_at": exported_at or now_stamp(),
    }
    if accepted is not None:
        payload["accepted"] = dict(accepted)
    payload["verdicts"] = verdicts
    tombstones = _tombstones(cleared, records)
    if tombstones:
        payload["cleared"] = _cleared_list(tombstones)
    if overrides is not None:
        payload["overrides"] = dict(overrides)
    return payload
