"""Append-only history of the review app's verdict store. Every write to verdicts-autosave.json — the app's /autosave POSTs and the headless merge tool — first records what changed in verdicts-journal.ndjson as one event line (source, time, manifest stamp) followed by one line per changed verdict, including explicit clears, which the store files themselves cannot represent (a cleared verdict is simply absent from them). A base event carries the full store rather than a diff: one opens every journal (seeding the pre-journal state) and one marks every surface-stamp change, so replay(as_of=...) can reconstruct the exact store at any recorded moment from the journal alone. That replay is what makes every store mutation reversible — the corrective path for a bad merge, a clobbered store, or an accidental clear is rebuild.tools.merge_verdicts --restore-as-of, not archaeology over stash files."""

from __future__ import annotations

import json
import os
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
    """Append the transition from the store's previous content to its new content. Same-stamp transitions journal as a diff (sets plus clears); a stamp change journals as a base event holding the full new store, since unit ids are never joinable across stamps. When the journal file does not exist yet and the previous store is same-stamp and non-empty, a seed base event of that previous store is prepended so replay is complete from the journal's first line."""
    journal_path = Path(journal_path)
    at = at or now_stamp()
    base = old_stamp != stamp
    old_records = {} if base else latest_by_unit(old_verdicts)
    new_records = latest_by_unit(new_verdicts)
    sets = [
        record
        for _, record in sorted(new_records.items())
        if base or record["unit"] not in old_records or _signature(old_records[record["unit"]]) != _signature(record)
    ]
    clears = [] if base else sorted(unit for unit in old_records if unit not in new_records)

    lines: list[dict] = []
    if not base and old_records and not journal_path.exists():
        lines.append(
            _event_line(
                source="seed", at=at, stamp=stamp, base=True, stashed=None, sets=len(old_records), clears=0
            )
        )
        lines.extend(_set_line(record) for _, record in sorted(old_records.items()))
    if base or sets or clears or stashed is not None:
        lines.append(
            _event_line(
                source=source, at=at, stamp=stamp, base=base, stashed=stashed, sets=len(sets), clears=len(clears)
            )
        )
        lines.extend(_set_line(record) for record in sets)
        lines.extend({"kind": "clear", "unit": unit} for unit in clears)
    if lines:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return {"base": base, "sets": len(sets), "clears": len(clears), "recorded": bool(lines)}


def _iter_entries(journal_path):
    try:
        text = Path(journal_path).read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return
        if isinstance(entry, dict):
            yield entry


def iter_events(journal_path):
    for entry in _iter_entries(journal_path):
        if entry.get("kind") == "event":
            yield entry


def replay(journal_path, as_of: str | None = None) -> tuple[str | None, dict[str, dict]]:
    """Reconstruct (stamp, records) as of the last event whose `at` is lexicographically <= as_of (events append in time order, so a truncated ISO prefix works). None stamp with empty records means the journal holds no event at or before that moment."""
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
            records.pop(entry.get("unit"), None)
    return stamp, records


def compact(journal_path, *, cutoff: str) -> dict:
    """Rewrite the journal to begin at the newest base event whose `at` is lexicographically at or before `cutoff` (an ISO-Z stamp), dropping every earlier line. A base event carries the full store, so replay and --restore-as-of stay exact for every moment from that base onward; every earlier moment becomes unrecoverable, which is why the caller chooses the cutoff, not this function. Kept lines are carried byte-for-byte (scanning stops at the first unparseable line, mirroring replay, so a torn tail is never reinterpreted) and the rewrite is atomic. A journal with no parseable base at or before the cutoff, or one already starting at that base, is left untouched."""
    journal_path = Path(journal_path)
    untouched = {"compacted": False, "floor_at": None, "dropped_lines": 0, "kept_lines": 0}
    try:
        text = journal_path.read_text(encoding="utf-8")
    except OSError:
        return untouched
    lines = text.splitlines(keepends=True)
    floor_index = None
    floor_at = None
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            break
        if (
            isinstance(entry, dict)
            and entry.get("kind") == "event"
            and entry.get("base")
            and (entry.get("at") or "") <= cutoff
        ):
            floor_index = index
            floor_at = entry.get("at")
    if not floor_index:
        untouched["kept_lines"] = len(lines)
        return untouched
    tmp = journal_path.with_name(journal_path.name + ".tmp")
    tmp.write_text("".join(lines[floor_index:]), encoding="utf-8")
    os.replace(tmp, journal_path)
    return {
        "compacted": True,
        "floor_at": floor_at,
        "dropped_lines": floor_index,
        "kept_lines": len(lines) - floor_index,
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
