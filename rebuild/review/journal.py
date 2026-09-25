"""Append-only log of changes to the review app's verdict store.

After each write to verdicts-autosave.json, the review server's store (`rebuild.review.verdict_store`) and `rebuild.tools.merge_verdicts` append the change to verdicts-journal.ndjson: one event line (source, time, manifest stamp) followed by one line per changed verdict. Clears get their own lines, because the store files cannot represent them (a cleared verdict is absent). A base event carries the full store instead of a diff. One is written at every surface-stamp change, and one seeds a new journal when the store it starts from is not empty, so `replay(as_of=...)` can reconstruct the store at any recorded moment from the journal alone. `rebuild.tools.merge_verdicts --restore-as-of` uses that replay to undo a bad merge, an overwritten store, or an accidental clear.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

EXPORT_FORMAT = "ams-review-verdicts/1"
JOURNAL_NAME = "verdicts-journal.ndjson"


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
        with journal_path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return bool(lines)


def _iter_entries(journal_path):
    """Yield the journal's parseable entries, reading one line at a time so only one line is in memory. Scanning stops at the first line that does not decode or parse, so a tail torn by a crashed append is never misread. Reading bytes extends that to a tail torn mid-character, which can happen because notes may hold non-ASCII text. A text-mode read would raise on such a tail before yielding a line, and every reader, the restore path included, would fail. `compact` splits the file the same way, so the two agree on where each line begins."""
    try:
        handle = Path(journal_path).open("rb")
    except OSError:
        return
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line.decode("utf-8"))
            except ValueError:
                return
            if isinstance(entry, dict):
                yield entry


def iter_events(journal_path):
    for entry in _iter_entries(journal_path):
        if entry.get("kind") == "event":
            yield entry


def replay(journal_path, as_of: str | None = None) -> tuple[str | None, dict[str, dict]]:
    """Return (stamp, records) as of the last event whose `at` is lexicographically <= `as_of`. Events are appended in time order, so a truncated ISO prefix works as `as_of`. A None stamp with empty records means the journal holds no event at or before that moment."""
    stamp: str | None = None
    records: dict[str, dict] = {}
    for entry in _iter_entries(journal_path):
        kind = entry.get("kind")
        if kind == "event":
            if as_of is not None and (entry.get("at") or "") > as_of:
                break
            stamp = entry.get("stamp")
            if entry.get("base"):
                records = {}
        elif stamp is None:
            continue
        elif kind == "set":
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
    return stamp, records


def compact(journal_path, *, cutoff: str) -> dict:
    """Rewrite the journal to begin at the newest base event whose `at` is lexicographically at or before `cutoff` (an ISO-Z stamp), dropping every earlier line. A base event carries the full store, so replay and --restore-as-of stay exact for every moment from that base on, and every earlier moment becomes unrecoverable; that is why the caller chooses the cutoff. Kept lines are copied byte for byte, and the rewrite is atomic. The scan for the base stops where `_iter_entries` stops, at the first line that does not decode or parse. A journal with no parseable base at or before the cutoff, or one that already starts at that base, is left untouched.

    The scan reads bytes and records the base's offset, and the rewrite copies from that offset in fixed-size blocks, so memory use stays at one line plus one block, including in the common case where the journal already starts at the newest base and nothing is rewritten.
    """
    journal_path = Path(journal_path)
    untouched = {"compacted": False, "floor_at": None, "dropped_lines": 0, "kept_lines": 0}
    floor_index = None
    floor_at = None
    floor_offset = 0
    total_lines = 0
    offset = 0
    scanning = True
    try:
        with journal_path.open("rb") as handle:
            for line in handle:
                if scanning and line.strip():
                    try:
                        entry = json.loads(line.decode("utf-8"))
                    except ValueError:
                        scanning = False
                    else:
                        if (
                            isinstance(entry, dict)
                            and entry.get("kind") == "event"
                            and entry.get("base")
                            and (entry.get("at") or "") <= cutoff
                        ):
                            floor_index = total_lines
                            floor_at = entry.get("at")
                            floor_offset = offset
                offset += len(line)
                total_lines += 1
    except OSError:
        return untouched
    if not floor_index:
        untouched["kept_lines"] = total_lines
        return untouched
    tmp = journal_path.with_name(journal_path.name + ".tmp")
    with journal_path.open("rb") as source, tmp.open("wb") as target:
        source.seek(floor_offset)
        shutil.copyfileobj(source, target)
    os.replace(tmp, journal_path)
    return {
        "compacted": True,
        "floor_at": floor_at,
        "dropped_lines": floor_index,
        "kept_lines": total_lines - floor_index,
    }


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
