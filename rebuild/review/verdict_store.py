"""The review server's in-memory copy of verdicts-autosave.json, its delta POST protocol, and its full-store and delta GET formats.

The file is one ams-review-verdicts/1 document holding every verdict on the corpus. Parsing and writing all of it for each saved verdict is too slow, so the server parses the file once, keeps the records in memory, and exchanges changes. A POST must be a delta (`sets` of whole records and `clears`, under ams-review-verdicts-delta/1), which is applied in place and appended to the journal as set and clear lines. A GET with `since=<token>` returns the records changed after that token. A GET without a token, or with one `changes_since` cannot serve, returns the whole store with the current token. The token is a boot id and a change sequence. A token gets the whole store when it predates a server restart or a reload of the file onto another stamp, or when it is older than the retained changes (`CHANGE_LOG_CAP`).

A set may carry `base_at`, the `at` of the record the tab last saw the server hold for that unit (null when it saw none). A clear is a unit id (the form older tabs send) or `{unit, base_at, at}`, where `at` is when the tab cleared it. The store keeps a tombstone for each cleared unit (unit → the clear's `at`), in memory and as a top-level `cleared` list in the file, which readers that use only `verdicts` ignore; a set removes its unit's tombstone.

Which stamp a delta carries decides how it is applied. The served corpus (`ServedCorpus`, the served manifest's stamp and human unit ids) is what the caller says the server is serving.

1. A delta on the store's stamp is applied last-writer-wins. One marked `replay: true`, which a tab sends from its outbox for saves it could not confirm before it closed, takes rule 2's per-unit test instead.
2. A delta on another stamp, while the store is on the served corpus, is carried by unit id, because a unit id is a content key and names the same unit on every corpus where its content is unchanged. A set applies when the store holds no record for the unit and no newer tombstone, when the store's record has the `at` the set names as `base_at`, or when the set's `at` is not older than the store's record's (an equal `at` is the same act, whose note an edit or the carry's provenance prefix may have changed); a clear applies on the same tests with its own `at`. Anything else is a conflict: the store keeps its record, the incoming set or clear goes to the orphan document with `reason: conflict`, and the response hands back the store's record for the unit. A set on a unit the served corpus does not have, and a clear with no `at`, go to the orphan document with `reason: orphan`. A skip is dropped, as the carry drops every skip, so the unit is asked again on the served corpus: when the store holds a record for the unit, the skip clears it under the same tests (a skip that fails them is a conflict), and the response lists the unit under `dropped`.
3. A delta stamped for the served corpus while the store is on another stamp moves the store onto the served corpus by unit id: the file is kept under its stash name (`stash_path_for`), the store keeps its records and tombstones on the units the served corpus has, less its skips (as the carry drops every skip), which invalidates every token, the delta is applied under rule 1, and the journal records the move as a transition naming the stash. This is how a store left on another corpus than the served one (by a first pass, which builds the missing corpus in place and lands no store, or by a restore under --yes) follows the corpus the tabs load without leaving its verdicts behind in the stash.
4. Any other delta on another stamp, one made on neither the store's corpus nor the served one, or one arriving while the served corpus is unknown, gets a 503 with `retry: true`, and nothing is written; the tab keeps the verdicts and saves them again. An unstamped or missing store takes the served corpus's stamp first, or the delta's when the served corpus is unknown.

The orphan documents live in `var/verdict-orphans/` beside the store (`orphans_dir_for`), one per stamp the verdicts were made on (`orphan_path`). Each is an ams-review-verdicts/1 document stamped for that corpus, whose records carry `reason` and `recorded_at` (a clear is a record with a null verdict), so nothing a tab sends is dropped, and none of them can become a master, because `status` surveys only the repo root and rebuild/evidence. Every accepted delta's response carries `corpus_stamp` (the store's stamp), `carried` (the units a delta on another stamp changed), `orphaned` (the unit ids sent to the orphan document as orphans), `dropped` (the units whose skip was dropped under rule 2), and `conflicts` (each unit with the store's record, or null when the store holds none).

A full-store POST gets a 400 with `reason: whole-store-post-unsupported` and recovery instructions, regardless of its stamp or the file's state, and writes nothing. A 409 tells legacy readers to reload rather than download pending verdicts, and `corpus_stamp` can prompt newer clients to reload automatically, so the refusal uses neither. When the store writes the file itself, it writes an ams-review-verdicts/1 document with one record per line, which `parse_autosave_payload`, the merge tool, the status check and the carry all read; GETs and export files use that format too.

The file holds the verdicts between server runs, and another writer (a pass's land, `rebuild.review.landing`, or the merge tool or a journal restore under --yes) can replace it while the server runs; each holds the store's lock (`rebuild.review.store_lock`) for its write, as the server does for each POST. So every request first compares the file's mtime, size and inode with what the store last read or wrote, and reloads on a mismatch. A reload onto the same stamp diffs the new records against the ones in memory and records the changed units, so outstanding tokens stay valid and a tab's next sync fetches only those units; a reload onto another stamp, or onto a file that is missing or unreadable, invalidates every token. A delta that changes no record and no stamp writes nothing, so the file's bytes and mtime stay as they were. A delta whose write fails reloads the file before the error propagates, so memory never holds a record the file lacks and a resend of the same delta is written. The orphan document is written before the store, so a delta whose orphan write fails changes nothing.

Before a store write the journal will record, the store writes the marker that says the journal may lack a write (`journal.mark_unjournaled`), and it removes the marker once the journal append succeeds, unless the marker was already there. A marker that cannot be written fails the save as a failed store write does, so no store write goes unmarked. An append that fails (reported as `journal_error`), is cut short, or never runs because the server was killed leaves it, so the next land that moves the stamp journals a base (`journal.base_due`).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from bisect import bisect_left, insort
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

from rebuild.review import journal

EXPORT_FORMAT = "ams-review-verdicts/1"
DELTA_FORMAT = "ams-review-verdicts-delta/1"
CHANGE_LOG_CAP = 100_000
ORPHAN_REASONS = ("orphan", "conflict")
ORPHAN_RECENT_CAP = 200


@dataclass(frozen=True, slots=True)
class ServedCorpus:
    """The corpus the server serves: its manifest's `generated_at` and its human unit ids."""

    stamp: str
    ids: frozenset[str]


def parse_autosave_payload(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw)
    except ValueError, UnicodeDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("format") != EXPORT_FORMAT:
        return None
    if not isinstance(data.get("manifest_generated_at"), str):
        return None
    if not isinstance(data.get("verdicts"), list):
        return None
    return data


def _optional_str(value) -> bool:
    return value is None or isinstance(value, str)


def _parse_clear(clear) -> dict | None:
    if isinstance(clear, str):
        return {"unit": clear, "base_at": None, "at": None}
    if not isinstance(clear, dict) or not isinstance(clear.get("unit"), str):
        return None
    if not _optional_str(clear.get("base_at")) or not _optional_str(clear.get("at")):
        return None
    return {"unit": clear["unit"], "base_at": clear.get("base_at"), "at": clear.get("at") or None}


def parse_delta_payload(raw: bytes) -> dict | None:
    """Return the delta with its clears normalized to `{unit, base_at, at}` (a bare unit id has null `base_at` and `at`) and `replay` as a bool, or None when the body is not an ams-review-verdicts-delta/1 document."""
    try:
        data = json.loads(raw)
    except ValueError, UnicodeDecodeError:
        return None
    if not isinstance(data, dict) or data.get("format") != DELTA_FORMAT:
        return None
    if not isinstance(data.get("manifest_generated_at"), str):
        return None
    sets = data.get("sets", [])
    clears = data.get("clears", [])
    replay = data.get("replay", False)
    if not isinstance(sets, list) or not isinstance(clears, list) or not isinstance(replay, bool):
        return None
    for record in sets:
        if not isinstance(record, dict) or not isinstance(record.get("unit"), str):
            return None
        if not all(_optional_str(record.get(key)) for key in ("base_at", "at", "note")):
            return None
    parsed_clears = [_parse_clear(clear) for clear in clears]
    if any(clear is None for clear in parsed_clears):
        return None
    return {
        "manifest_generated_at": data["manifest_generated_at"],
        "sets": sets,
        "clears": parsed_clears,
        "replay": replay,
    }


def _safe_stamp(stamp: str) -> str:
    return "".join(c if c.isalnum() or c in ".-" else "." for c in stamp)


def stash_path_for(path: Path, stamp: str) -> Path:
    return path.with_name(f"{path.stem}-{_safe_stamp(stamp)}{path.suffix}")


def _link_or_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(target.name + ".link")
    tmp.unlink(missing_ok=True)
    try:
        os.link(source, tmp)
    except OSError:
        shutil.copyfile(source, tmp)
    os.replace(tmp, target)


def orphans_dir_for(store_path: Path) -> Path:
    """Return the orphan documents' directory for the store at `store_path`: `var/verdict-orphans/` beside it, which for the live store is under the repo root's gitignored `var/`."""
    return Path(store_path).parent / "var" / "verdict-orphans"


def orphan_path(orphans_dir: Path, stamp: str) -> Path:
    return Path(orphans_dir) / f"{_safe_stamp(stamp)}.json"


def _orphan_key(record: dict) -> tuple:
    return (
        record.get("unit"),
        record.get("verdict"),
        record.get("note") or "",
        record.get("at") or "",
        record.get("reason"),
    )


def append_orphans(orphans_dir: Path, stamp: str, entries: list[dict]) -> None:
    """Add `entries` (records with a `reason`) to the orphan document for `stamp`, skipping any the document already holds, so a resent delta adds nothing. The document is rewritten through a temporary file."""
    path = orphan_path(orphans_dir, stamp)
    existing = None
    try:
        existing = parse_autosave_payload(path.read_bytes())
    except FileNotFoundError:
        pass
    verdicts = [record for record in existing["verdicts"] if isinstance(record, dict)] if existing else []
    seen = {_orphan_key(record) for record in verdicts}
    recorded_at = journal.now_stamp()
    added = False
    for entry in entries:
        key = _orphan_key(entry)
        if key in seen:
            continue
        seen.add(key)
        verdicts.append({**entry, "recorded_at": recorded_at})
        added = True
    if not added:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "format": EXPORT_FORMAT,
        "manifest_generated_at": stamp,
        "exported_at": recorded_at,
        "verdicts": verdicts,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def summarize_orphans(orphans_dir: Path) -> dict[str, dict]:
    """Return, for each of `ORPHAN_REASONS`, how many records the orphan documents hold with that reason, the newest `recorded_at` among them, the document holding that newest record, and `recent`: `[recorded_at, count]` pairs, newest first and at most `ORPHAN_RECENT_CAP` of them, so a reader can count the records kept since a given time. One delta's records share one `recorded_at`. A document that cannot be read is skipped."""
    summary = {reason: {"count": 0, "newest_at": None, "file": None} for reason in ORPHAN_REASONS}
    recent: dict[str, Counter[str]] = {reason: Counter() for reason in ORPHAN_REASONS}
    try:
        paths = sorted(Path(orphans_dir).glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        try:
            data = parse_autosave_payload(path.read_bytes())
        except OSError:
            continue
        if data is None:
            continue
        for record in data["verdicts"]:
            if not isinstance(record, dict) or record.get("reason") not in summary:
                continue
            entry = summary[record["reason"]]
            entry["count"] += 1
            at = record.get("recorded_at")
            if not isinstance(at, str):
                continue
            recent[record["reason"]][at] += 1
            if entry["newest_at"] is None or at > entry["newest_at"]:
                entry["newest_at"] = at
                entry["file"] = path
    for reason, entry in summary.items():
        entry["recent"] = [
            [at, count] for at, count in sorted(recent[reason].items(), reverse=True)[:ORPHAN_RECENT_CAP]
        ]
    return summary


def _normalize(record: dict) -> dict:
    return {
        "unit": record["unit"],
        "verdict": record.get("verdict"),
        "note": record.get("note") or "",
        "at": record.get("at") or "",
    }


def _signature(record: dict) -> tuple:
    return (record.get("verdict"), record.get("note") or "", record.get("at") or "")


def parse_cleared(data: dict | None) -> dict[str, str]:
    """Return a verdicts document's tombstones (its top-level `cleared` list) as unit → the clear's `at`, skipping malformed entries."""
    cleared = data.get("cleared") if data else None
    if not isinstance(cleared, list):
        return {}
    return {
        entry["unit"]: entry["at"]
        for entry in cleared
        if isinstance(entry, dict) and isinstance(entry.get("unit"), str) and isinstance(entry.get("at"), str)
    }


def _stat_signature(stat: os.stat_result) -> tuple[int, int, int]:
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


def file_signature(path: Path) -> tuple[int, int, int] | None:
    """Return the file's (mtime, size, inode), or None when it cannot be stat'ed. A write through a renamed temporary file moves the inode even when the mtime and size match."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return _stat_signature(stat)


@dataclass(slots=True)
class _Plan:
    """What one delta does, decided before anything is written: the records to set, the clears to apply (unit → the tombstone's `at`), the conflicts and orphans to report, and the units whose skip was dropped."""

    sets: list[dict]
    clears: dict[str, str]
    conflicts: list[dict]
    orphans: list[dict]
    dropped: list[str]


class VerdictStore:
    def __init__(self, path, journal_path=None, orphans_dir=None):
        self.path = Path(path)
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self.orphans_dir = Path(orphans_dir) if orphans_dir is not None else orphans_dir_for(self.path)
        self.stamp: str | None = None
        self.records: dict[str, dict] = {}
        self.cleared: dict[str, str] = {}
        self._lines: dict[str, str] = {}
        self._order: list[str] = []
        self._signature: tuple[int, int, int] | None = None
        self._boot = secrets.token_hex(4)
        self._seq = 0
        self._changes: deque[tuple[int, str]] = deque(maxlen=CHANGE_LOG_CAP)
        self.reload()

    def reload(self) -> None:
        """Replace the store's content with the file's. A missing file or one that is not a verdicts document leaves the store empty and unstamped, so the next save overwrites it. When the file holds the stamp the store already has, the units whose records differ are recorded as changes and every token stays valid; otherwise every token is invalidated.

        The signature comes from the open handle, so it describes the bytes the store parsed even when another writer renames a new file over the path mid-read; the next `refresh_if_changed` then sees the new file and reloads. When the file cannot be opened or read, the signature is None, so a file renamed into place after the failed open leaves a mismatch, and an existing file the store could not read is reloaded on the next refresh.
        """
        try:
            with self.path.open("rb") as f:
                stat = os.fstat(f.fileno())
                raw = f.read()
            self._signature = _stat_signature(stat)
        except OSError:
            raw = None
            self._signature = None
        data = parse_autosave_payload(raw) if raw is not None else None
        stamp = data["manifest_generated_at"] if data else None
        new_records = journal.latest_by_unit(data["verdicts"]) if data else {}
        cleared = parse_cleared(data)
        if stamp is not None and stamp == self.stamp:
            changed = self._diff_units(new_records)
            self._replace(stamp, new_records, cleared)
            self._note_changes(changed)
            return
        self._replace(stamp, new_records, cleared)
        self._invalidate_tokens()

    def refresh_if_changed(self) -> bool:
        if file_signature(self.path) == self._signature:
            return False
        self.reload()
        return True

    @property
    def token(self) -> str:
        return f"{self._boot}:{self._seq}"

    def _invalidate_tokens(self) -> None:
        self._boot = secrets.token_hex(4)
        self._seq = 0
        self._changes.clear()

    def _replace(self, stamp: str | None, new_records: dict[str, dict], cleared: dict[str, str]) -> None:
        self.stamp = stamp
        self.records = {unit: _normalize(record) for unit, record in new_records.items()}
        self.cleared = {unit: at for unit, at in cleared.items() if unit not in self.records}
        self._lines = {unit: json.dumps(record, ensure_ascii=False) for unit, record in self.records.items()}
        self._order = sorted(self.records)

    def _set(self, record: dict) -> bool:
        record = _normalize(record)
        unit = record["unit"]
        existing = self.records.get(unit)
        if existing is not None and _signature(existing) == _signature(record):
            return False
        if existing is None:
            insort(self._order, unit)
        self.records[unit] = record
        self._lines[unit] = json.dumps(record, ensure_ascii=False)
        self.cleared.pop(unit, None)
        return True

    def _clear(self, unit: str, at: str) -> bool:
        if unit not in self.records:
            return False
        del self.records[unit]
        del self._lines[unit]
        index = bisect_left(self._order, unit)
        del self._order[index]
        self.cleared[unit] = at
        return True

    def _note_changes(self, units) -> None:
        for unit in units:
            self._seq += 1
            self._changes.append((self._seq, unit))

    def _cleared_list(self) -> list[dict]:
        return [{"unit": unit, "at": at} for unit, at in sorted(self.cleared.items())]

    def payload_dict(self) -> dict:
        payload = {
            "format": EXPORT_FORMAT,
            "manifest_generated_at": self.stamp,
            "exported_at": journal.now_stamp(),
            "verdicts": [self.records[unit] for unit in self._order],
        }
        if self.cleared:
            payload["cleared"] = self._cleared_list()
        return payload

    def payload_bytes(self, *, token: bool = False) -> bytes:
        """Return the whole store as one ams-review-verdicts/1 document, one record per line, with the tombstones after the records when there are any. With `token`, the sync token is added as an extra top-level field for the app."""
        head = {
            "format": EXPORT_FORMAT,
            "manifest_generated_at": self.stamp,
            "exported_at": journal.now_stamp(),
        }
        if token:
            head["token"] = self.token
        prefix = json.dumps(head, ensure_ascii=False)[:-1]
        body = ",\n".join(self._lines[unit] for unit in self._order)
        tail = f', "cleared": {json.dumps(self._cleared_list(), ensure_ascii=False)}' if self.cleared else ""
        return f'{prefix}, "verdicts": [\n{body}\n]{tail}}}\n'.encode()

    def changes_since(self, token: str | None) -> dict | None:
        """Return the records set or cleared after `token`, or None when the token is malformed, from another boot, ahead of the current sequence, or older than the retained changes, so the caller sends the whole store."""
        if not isinstance(token, str) or ":" not in token:
            return None
        boot, _, seq_text = token.partition(":")
        if boot != self._boot or not seq_text.isdigit():
            return None
        seq = int(seq_text)
        if seq > self._seq:
            return None
        if self._changes and seq < self._changes[0][0] - 1:
            return None
        units = {unit for change_seq, unit in self._changes if change_seq > seq}
        sets = [self.records[unit] for unit in sorted(units) if unit in self.records]
        clears = sorted(unit for unit in units if unit not in self.records)
        return {
            "format": DELTA_FORMAT,
            "manifest_generated_at": self.stamp,
            "token": self.token,
            "sets": sets,
            "clears": clears,
        }

    def _write_bytes(self, raw: bytes) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(raw)
        signature = file_signature(tmp)
        os.replace(tmp, self.path)
        self._signature = signature

    def write(self) -> None:
        self._write_bytes(self.payload_bytes())

    def receive(self, raw: bytes, served: ServedCorpus | None = None) -> tuple[int, dict]:
        """Apply a delta POST body, or refuse a full store with recovery instructions, and return the HTTP status and response body. `served` is the corpus the server serves, which decides what a delta on another stamp does; None means it is unknown."""
        self.refresh_if_changed()
        delta = parse_delta_payload(raw)
        if delta is not None:
            return self._receive_delta(delta, served)
        if parse_autosave_payload(raw) is not None:
            return 400, {
                "ok": False,
                "reason": "whole-store-post-unsupported",
                "error": (
                    "whole-store saves are unsupported; before closing or reloading this tab, export/download "
                    "its verdicts or copy any pending verdicts, and note pending clears and note edits. Then "
                    "open/reload the current app and import the saved file (or reapply copied verdicts). "
                    "Manually reapply pending clears and note edits: historical exports omit clears, and "
                    "imports can keep equal or newer server records. Keep the saved file until recovery is verified"
                ),
            }
        return 400, {"ok": False, "error": f"not an {DELTA_FORMAT} document"}

    def _diff_units(self, new_records: dict[str, dict]) -> list[str]:
        changed = [
            unit
            for unit, record in new_records.items()
            if unit not in self.records or _signature(self.records[unit]) != _signature(record)
        ]
        changed.extend(unit for unit in self.records if unit not in new_records)
        return changed

    def _admits(self, unit: str, at: str, base_at: str | None) -> bool:
        """Return whether a set or clear whose `at` and `base_at` are given may replace the store's state for `unit` under rule 2: no record and no newer tombstone, a record whose `at` is `base_at`, or a record not newer than `at`."""
        current = self.records.get(unit)
        if current is None:
            tombstone = self.cleared.get(unit)
            return tombstone is None or tombstone <= at
        return current["at"] == base_at or at >= current["at"]

    def _plan(self, delta: dict, *, tested: bool, members: frozenset[str] | None) -> _Plan:
        plan = _Plan(sets=[], clears={}, conflicts=[], orphans=[], dropped=[])
        for raw_record in delta["sets"]:
            record = _normalize(raw_record)
            unit = record["unit"]
            if members is not None and unit not in members:
                plan.orphans.append({**record, "reason": "orphan"})
                continue
            current = self.records.get(unit)
            if current is not None and _signature(current) == _signature(record):
                continue
            skip = members is not None and record["verdict"] == "skip"
            if skip and current is None:
                plan.dropped.append(unit)
                continue
            if tested and not self._admits(unit, record["at"], raw_record.get("base_at")):
                plan.conflicts.append({**record, "reason": "conflict"})
                continue
            if skip:
                plan.dropped.append(unit)
                plan.clears[unit] = record["at"] or journal.now_stamp()
                continue
            plan.sets.append(record)
        for clear in delta["clears"]:
            unit = clear["unit"]
            at = clear["at"]
            gone = {"unit": unit, "verdict": None, "note": "", "at": at or ""}
            if tested and at is None:
                plan.orphans.append({**gone, "reason": "orphan"})
                continue
            if unit not in self.records:
                continue
            if tested and not self._admits(unit, at, clear["base_at"]):
                plan.conflicts.append({**gone, "reason": "conflict"})
                continue
            plan.clears[unit] = at or journal.now_stamp()
        return plan

    def _receive_delta(self, delta: dict, served: ServedCorpus | None) -> tuple[int, dict]:
        stamp = delta["manifest_generated_at"]
        adopted = None
        if self.stamp is None:
            adopted = served.stamp if served is not None else stamp
        store_stamp = adopted or self.stamp
        if served is not None and stamp == served.stamp and store_stamp != stamp:
            return self._receive_delta_onto_served_stamp(delta, served)
        if stamp == store_stamp:
            plan = self._plan(delta, tested=delta["replay"], members=None)
            source = "autosave"
            target = stamp
        elif served is None or store_stamp != served.stamp:
            return 503, {
                "ok": False,
                "retry": True,
                "reason": "store-not-on-served-corpus",
                "corpus_stamp": self.stamp,
                "error": (
                    "the verdict store is not on the served corpus, so a save made on neither corpus cannot be "
                    "carried onto it; save again shortly"
                ),
            }
        else:
            plan = self._plan(delta, tested=True, members=served.ids)
            source = "autosave-carried"
            target = served.stamp
        rejected = plan.orphans + plan.conflicts
        if rejected:
            append_orphans(self.orphans_dir, stamp, rejected)
        if adopted is not None:
            self.stamp = adopted
        sets, clears = self._apply(plan)
        changed = [record["unit"] for record in sets] + clears
        marked = False
        if changed or adopted is not None:
            try:
                marked = bool(changed) and self._mark_unjournaled()
                self.write()
            except BaseException:
                self.reload()
                raise
        body = {
            "ok": True,
            "saved": len(self.records),
            "token": self.token,
            "corpus_stamp": self.stamp,
            "carried": changed if source == "autosave-carried" else [],
            "orphaned": [entry["unit"] for entry in plan.orphans],
            "dropped": plan.dropped,
            "conflicts": [
                {"unit": entry["unit"], "server": self.records.get(entry["unit"])} for entry in plan.conflicts
            ],
        }
        if self.journal_path is not None and changed:
            self._journal(
                body,
                marked,
                journal.record_delta,
                source=source,
                stamp=target,
                sets=[self.records[record["unit"]] for record in sets],
                clears=clears,
                seed_records=None if self.journal_path.exists() else self._records_before(sets, clears),
            )
        return 200, body

    def _apply(self, plan: _Plan) -> tuple[list[dict], list[str]]:
        """Apply the plan's sets and clears to memory, record the changed units, and return the sets and clears that changed something."""
        sets = [record for record in plan.sets if self._set(record)]
        clears = [unit for unit, at in plan.clears.items() if self._clear(unit, at)]
        self._note_changes([record["unit"] for record in sets] + clears)
        return sets, clears

    def _receive_delta_onto_served_stamp(self, delta: dict, served: ServedCorpus) -> tuple[int, dict]:
        """Apply a delta stamped for the served corpus to a store on another stamp (rule 3 in the module docstring): keep the file under its stash name, carry the store's records and tombstones on the served corpus's units onto the served stamp by unit id, less its skips, apply the delta under rule 1, and journal the move as one transition that names the stash. The stash is a second name for the old file (a copy where the filesystem refuses a hard link), and the new store is renamed over it, so a failed write leaves the old store in place."""
        stamp = delta["manifest_generated_at"]
        old_stamp = self.stamp
        old_verdicts = list(self.records.values())
        stash = stash_path_for(self.path, old_stamp) if old_stamp is not None else None
        if stash is not None and self.path.exists():
            _link_or_copy(self.path, stash)
        else:
            stash = None
        kept = {
            unit: record
            for unit, record in self.records.items()
            if unit in served.ids and record["verdict"] != "skip"
        }
        cleared = {unit: at for unit, at in self.cleared.items() if unit in served.ids}
        self._replace(stamp, kept, cleared)
        self._invalidate_tokens()
        self._apply(self._plan(delta, tested=delta["replay"], members=None))
        try:
            marked = self._mark_unjournaled()
            self.write()
        except BaseException:
            self.reload()
            raise
        body = {
            "ok": True,
            "saved": len(self.records),
            "token": self.token,
            "corpus_stamp": stamp,
            "carried": [],
            "orphaned": [],
            "dropped": [],
            "conflicts": [],
        }
        if self.journal_path is not None:
            self._journal(
                body,
                marked,
                journal.record_transition,
                source="autosave",
                stamp=stamp,
                old_stamp=old_stamp,
                old_verdicts=old_verdicts,
                new_verdicts=list(self.records.values()),
                stashed=stash.name if stash is not None else None,
            )
        return 200, body

    def _records_before(self, sets, clears) -> dict[str, dict]:
        """Return the store's records minus the units this delta touched, to seed a journal that does not exist yet. The touched units are left out, not reverted, because the delta's own journal lines record them."""
        touched = {record["unit"] for record in sets} | set(clears)
        return {unit: record for unit, record in self.records.items() if unit not in touched}

    def _mark_unjournaled(self) -> bool:
        """Before a store write the journal will record, write the marker that says the journal may lack it (`journal.mark_unjournaled`), and return whether this write left it. A store with no journal writes none."""
        if self.journal_path is None:
            return False
        return journal.mark_unjournaled(self.path, "review server")

    def _journal(self, body: dict, marked: bool, append, **kwargs) -> None:
        """Append the store write just made to the journal with `append` (`journal.record_delta` or `journal.record_transition`), reporting an OSError as `journal_error`, and once the append succeeds remove the marker when this write left it (`marked`). A failed append, or a server killed before or during it, leaves the marker for the next land that moves the stamp."""
        assert self.journal_path is not None
        try:
            append(self.journal_path, **kwargs)
        except OSError as exc:
            body["journal_error"] = str(exc)
            return
        if marked:
            journal.unjournaled_marker_for(self.path).unlink(missing_ok=True)
