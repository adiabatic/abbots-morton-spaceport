"""The review server's resident copy of verdicts-autosave.json, and the two wire formats it speaks. The store on disk is one ams-review-verdicts/1 document holding every verdict on the surface — a couple of hundred thousand records once the carries and fills have landed — and reading or writing the whole of it per verdict is what made the app's autosave the slowest thing in the loop: the tab serialized every record it held after each keystroke pause, the server parsed that twice to diff it, and every Range request for a card's samples queued behind the parse. So the server parses the file once, keeps the records in memory, and takes and gives changes: a POST is a delta (`sets` of whole records, `clears` of unit ids, under `ams-review-verdicts-delta/1`), applied in place and appended to the journal as the same set and clear lines a full save would have produced; a GET with `since=<token>` answers with the records changed after that token, and a GET without one answers with the whole store plus a fresh token. The token is a boot id and a change sequence, so a client whose token predates a server restart, an external rewrite of the file, or the retained change window is handed the whole store instead of a gap.

A full-store POST is still accepted — it is the shape the receiver always took, and the tests hold it to the same bytes-on-disk contract: the file is written verbatim and a stamp change stashes the file it replaces. Either shape refuses a stamp older than the store's with 409, since a tab still open from before a rebuild must not clobber the freshly merged store. The file the store writes for itself is the same ams-review-verdicts/1 document, one record per line, and every consumer of the file (`parse_autosave_payload`, the merge tool, the status check, the carry) reads it as before.

The disk file remains the store of record between server runs, and an external writer — the merge tool under --yes, a journal restore — may replace it while the server is up, so every request first compares the file's size and mtime with what the store last read or wrote and reloads on a mismatch, which also invalidates every outstanding token.
"""

from __future__ import annotations

import json
import os
import secrets
from bisect import bisect_left, insort
from collections import deque
from pathlib import Path

from rebuild.review import journal

EXPORT_FORMAT = "ams-review-verdicts/1"
DELTA_FORMAT = "ams-review-verdicts-delta/1"
CHANGE_LOG_CAP = 100_000


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


def parse_delta_payload(raw: bytes) -> dict | None:
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
    if not isinstance(sets, list) or not isinstance(clears, list):
        return None
    if not all(isinstance(record, dict) and isinstance(record.get("unit"), str) for record in sets):
        return None
    if not all(isinstance(unit, str) for unit in clears):
        return None
    return {"manifest_generated_at": data["manifest_generated_at"], "sets": sets, "clears": clears}


def stash_path_for(path: Path, stamp: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-" else "." for c in stamp)
    return path.with_name(f"{path.stem}-{safe}{path.suffix}")


def _normalize(record: dict) -> dict:
    return {
        "unit": record["unit"],
        "verdict": record.get("verdict"),
        "note": record.get("note") or "",
        "at": record.get("at") or "",
    }


def _signature(record: dict) -> tuple:
    return (record.get("verdict"), record.get("note") or "", record.get("at") or "")


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


class VerdictStore:
    def __init__(self, path, journal_path=None):
        self.path = Path(path)
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self.stamp: str | None = None
        self.records: dict[str, dict] = {}
        self._lines: dict[str, str] = {}
        self._order: list[str] = []
        self._signature: tuple[int, int] | None = None
        self._boot = secrets.token_hex(4)
        self._seq = 0
        self._changes: deque[tuple[int, str]] = deque(maxlen=CHANGE_LOG_CAP)
        self.reload()

    def reload(self) -> None:
        """Read the file as the store's whole content. A file that is missing or not a verdicts document leaves the store empty and unstamped, which is what lets a first or corrupt file be overwritten rather than stashed."""
        raw = None
        try:
            if self.path.exists():
                raw = self.path.read_bytes()
        except OSError:
            raw = None
        self._signature = _file_signature(self.path)
        data = parse_autosave_payload(raw) if raw is not None else None
        self._replace(data["manifest_generated_at"] if data else None, data["verdicts"] if data else [])
        self._invalidate_tokens()

    def refresh_if_changed(self) -> bool:
        if _file_signature(self.path) == self._signature:
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

    def _replace(self, stamp: str | None, verdicts) -> None:
        self.stamp = stamp
        self.records = {unit: _normalize(record) for unit, record in journal.latest_by_unit(verdicts).items()}
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
        return True

    def _clear(self, unit: str) -> bool:
        if unit not in self.records:
            return False
        del self.records[unit]
        del self._lines[unit]
        index = bisect_left(self._order, unit)
        del self._order[index]
        return True

    def _note_changes(self, units) -> None:
        for unit in units:
            self._seq += 1
            self._changes.append((self._seq, unit))

    def payload_dict(self) -> dict:
        return {
            "format": EXPORT_FORMAT,
            "manifest_generated_at": self.stamp,
            "exported_at": journal.now_stamp(),
            "verdicts": [self.records[unit] for unit in self._order],
        }

    def payload_bytes(self, *, token: bool = False) -> bytes:
        """The whole store as one ams-review-verdicts/1 document, a record per line; with `token`, the sync token rides along as an extra top-level field the app reads back."""
        head = {
            "format": EXPORT_FORMAT,
            "manifest_generated_at": self.stamp,
            "exported_at": journal.now_stamp(),
        }
        if token:
            head["token"] = self.token
        prefix = json.dumps(head, ensure_ascii=False)[:-1]
        body = ",\n".join(self._lines[unit] for unit in self._order)
        return f'{prefix}, "verdicts": [\n{body}\n]}}\n'.encode()

    def changes_since(self, token: str | None) -> dict | None:
        """The records set or cleared after `token`, or None when the token is from another boot, predates the retained changes, or is malformed — the cases where only the whole store is an honest answer."""
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
        os.replace(tmp, self.path)
        self._signature = _file_signature(self.path)

    def write(self) -> None:
        self._write_bytes(self.payload_bytes())

    def _stash_existing(self, existing_stamp: str) -> str:
        stash = stash_path_for(self.path, existing_stamp)
        os.replace(self.path, stash)
        return stash.name

    def receive(self, raw: bytes) -> tuple[int, dict]:
        """Apply one POST body — a delta or a whole store — returning the HTTP status and response body."""
        self.refresh_if_changed()
        delta = parse_delta_payload(raw)
        if delta is not None:
            return self._receive_delta(delta)
        data = parse_autosave_payload(raw)
        if data is None:
            return 400, {"ok": False, "error": f"not an {EXPORT_FORMAT} or {DELTA_FORMAT} document"}
        return self._receive_full(raw, data)

    def _refuse_stale(self, stamp: str) -> tuple[int, dict] | None:
        if self.stamp is not None and self.stamp != stamp and self.stamp > stamp:
            return 409, {
                "ok": False,
                "error": (
                    "stale session: the autosave on disk is stamped for a newer surface "
                    f"({self.stamp}); reload the app"
                ),
            }
        return None

    def _receive_full(self, raw: bytes, data: dict) -> tuple[int, dict]:
        stamp = data["manifest_generated_at"]
        refused = self._refuse_stale(stamp)
        if refused is not None:
            return refused
        old_stamp = self.stamp
        old_verdicts = list(self.records.values())
        stashed = None
        if old_stamp is not None and old_stamp != stamp and self.path.exists():
            stashed = self._stash_existing(old_stamp)
        self._write_bytes(raw)
        changed = self._diff_units(data["verdicts"])
        self._replace(stamp, data["verdicts"])
        self._note_changes(changed)
        body = {"ok": True, "saved": len(data["verdicts"]), "stashed": stashed}
        self._journal_transition(
            source="autosave",
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=old_verdicts,
            new_verdicts=data["verdicts"],
            stashed=stashed,
            body=body,
        )
        return 200, body

    def _diff_units(self, new_verdicts) -> list[str]:
        new_records = journal.latest_by_unit(new_verdicts)
        changed = [
            unit
            for unit, record in new_records.items()
            if unit not in self.records or _signature(self.records[unit]) != _signature(record)
        ]
        changed.extend(unit for unit in self.records if unit not in new_records)
        return changed

    def _receive_delta(self, delta: dict) -> tuple[int, dict]:
        stamp = delta["manifest_generated_at"]
        refused = self._refuse_stale(stamp)
        if refused is not None:
            return refused
        if self.stamp is not None and self.stamp != stamp:
            return self._receive_delta_onto_new_stamp(delta)
        sets = [record for record in delta["sets"] if self._set(record)]
        clears = [unit for unit in delta["clears"] if self._clear(unit)]
        changed = [record["unit"] for record in sets] + clears
        self.stamp = stamp
        self._note_changes(changed)
        self.write()
        body = {"ok": True, "saved": len(self.records), "stashed": None, "token": self.token}
        if self.journal_path is not None and (sets or clears):
            try:
                journal.record_delta(
                    self.journal_path,
                    source="autosave",
                    stamp=stamp,
                    sets=[self.records[record["unit"]] for record in sets],
                    clears=clears,
                    seed_records=None if self.journal_path.exists() else self._records_before(sets, clears),
                )
            except OSError as exc:
                body["journal_error"] = str(exc)
        return 200, body

    def _records_before(self, sets, clears) -> dict[str, dict]:
        """The store as it stood before this delta, used only to seed a journal that does not exist yet. The delta's units are dropped rather than reverted: the seed then carries every untouched record, and the delta's own lines carry the rest."""
        touched = {record["unit"] for record in sets} | set(clears)
        return {unit: record for unit, record in self.records.items() if unit not in touched}

    def _receive_delta_onto_new_stamp(self, delta: dict) -> tuple[int, dict]:
        """A delta stamped for a newer surface than the store: the tab booted on a rebuilt surface while the file still holds the old one, so what it sends is everything it has, and the file it replaces is stashed exactly as a full save would."""
        stamp = delta["manifest_generated_at"]
        old_stamp = self.stamp
        old_verdicts = list(self.records.values())
        stashed = self._stash_existing(old_stamp) if old_stamp is not None and self.path.exists() else None
        self._replace(stamp, delta["sets"])
        self._invalidate_tokens()
        self.write()
        body = {"ok": True, "saved": len(self.records), "stashed": stashed, "token": self.token}
        self._journal_transition(
            source="autosave",
            stamp=stamp,
            old_stamp=old_stamp,
            old_verdicts=old_verdicts,
            new_verdicts=list(self.records.values()),
            stashed=stashed,
            body=body,
        )
        return 200, body

    def _journal_transition(self, *, body: dict, **kwargs) -> None:
        if self.journal_path is None:
            return
        try:
            journal.record_transition(self.journal_path, **kwargs)
        except OSError as exc:
            body["journal_error"] = str(exc)
