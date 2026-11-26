"""The verdict store: verdicts-autosave.json at the repo root, the review server's copy of it in memory, its delta POST protocol, and its full-store and delta GET formats.

The file is one ams-review-verdicts/1 document: its `format`, `manifest_generated_at` (the corpus stamp it is on), `exported_at`, `verdicts`, one `{unit, verdict, note, at}` record per unit in unit order, and `cleared`, a tombstone `{unit, at}` per unit cleared and not set since. It holds the verdicts made since the last accept (`rebuild.review.accept`), which empties it, so it stays small, and every writer writes it whole (`write_store`, through a temporary file renamed over it) under the store's lock (`rebuild.review.store_lock`): the review server for each save that changes it, the land that carries it onto a new corpus (`rebuild.review.landing`), and the accept.

A POST must be a delta (`sets` of whole records and `clears`, under ams-review-verdicts-delta/1), applied in memory and written to the file before the reply. A body `parse_delta_payload` refuses gets a 400 and writes nothing: a delta holding a set whose record `well_formed` refuses names the first such set (`MalformedSet`), one whose sets all pass but which holds a malformed clear names the first such clear (`MalformedClear`), a whole-store document gets recovery instructions (`whole-store-post-unsupported`), and anything else is told it is not a delta. A GET with `since=<token>` returns the units changed after that token; a GET without one, or with one `changes_since` cannot serve, returns the whole store and the current token. A token is a boot id and a change sequence, and it gets the whole store when it predates a server restart or a reload of the file onto another stamp, or is older than the retained changes (`CHANGE_LOG_CAP`).

A set may carry `base_at`, the `at` of the record the tab last saw the server hold for the unit (null when it saw none). A clear is a unit id (the form older tabs send) or `{unit, base_at, at}`, where `at` is when the tab cleared it. A clear leaves a tombstone, and a set removes its unit's tombstone.

Which stamp a delta carries decides how it is applied. The served corpus (`ServedCorpus`, the served manifest's stamp and human unit ids) is what the caller says the server serves.

1. A delta on the store's stamp is applied last-writer-wins. One marked `replay: true`, which a tab sends from its outbox for saves it could not confirm before it closed, takes rule 2's per-unit test instead.
2. A delta on another stamp, while the store is on the served corpus, is carried by unit id, because a unit id is a content key and names the same unit on every corpus where its content is unchanged. A set applies when the unit has no verdict and no newer tombstone, when its verdict has the `at` the set names as `base_at`, or when the set's `at` is not older than its verdict's (an equal `at` is the same act, whose note an edit may have changed); a clear applies on the same tests with its own `at`. Anything else is a conflict: the store keeps its state, the incoming set or clear goes to the orphan document with `reason: conflict`, and the reply hands back the unit's verdict. A set on a unit outside the served corpus's human units, and a clear with no `at`, go to the orphan document with `reason: orphan`. A skip is dropped, as the land drops every skip, so the unit is asked again on the served corpus: when the unit has a verdict, the skip clears it under the same tests (a skip that fails them is a conflict), and the reply lists the unit under `dropped`.
3. A delta stamped for the served corpus while the store is on another stamp first carries the store onto the served corpus by unit id, as the land does (`carry`): its records on the served corpus's human units stay, a skip among them becomes a tombstone at its `at`, the rest go to the orphan document of the stamp they were made on, and its tombstones on those units stay. It invalidates every token, and the delta is then applied under rule 1. A store is left on another corpus than the served one by a land killed between its corpus swap and its store write, or by a first pass, which builds the missing corpus in place and lands no store.
4. Any other delta on another stamp, one made on neither the store's corpus nor the served one, or one arriving while the served corpus is unknown, gets a 503 with `retry: true`, and nothing is written; the tab keeps the verdicts and saves them again. An unstamped or missing store takes the served corpus's stamp first, or the delta's when the served corpus is unknown.

The orphan documents live in `var/verdict-orphans/` beside the store (`orphans_dir_for`), one per stamp the verdicts were made on (`orphan_path`). Each is an ams-review-verdicts/1 document stamped for that corpus, whose records carry `reason` and `recorded_at` (a clear is a record with a null verdict), so nothing a tab sends or a land carries off is dropped unseen. Every applied delta's reply carries `corpus_stamp` (the store's stamp), `carried` (the units a delta on another stamp changed), `orphaned` (the unit ids sent to the orphan document as orphans), `dropped` (the units whose skip was dropped under rule 2), and `conflicts` (each unit with its verdict, or null when it has none).

Another writer can replace the file while the server runs, so every request first compares the file's mtime, size and inode with what the store last read or wrote, and reloads on a mismatch (`refresh_if_changed`). A reload onto the same stamp records the units whose record or tombstone moved, so outstanding tokens stay valid and a tab's next sync fetches only those; a reload onto another stamp, or onto a file that is missing or unreadable, invalidates every token. A file holding a verdict record `well_formed` refuses is read as an unreadable one is, so the store serves no verdict from it, but it is never written over: while it stays, every delta `parse_delta_payload` accepts gets a 422 and a GET a 404, both with `reason: store-malformed` and an error naming the file and the first such record, which the app shows the reader, until another writer replaces the file. A delta that changes nothing writes nothing, so the file's bytes and mtime stay as they were. A delta whose write fails is put back, the store reloaded from the file, before the error propagates, so memory never holds a record the file lacks. The orphan document is written before the store, so a delta whose orphan write fails changes nothing.
"""

from __future__ import annotations

import json
import os
import secrets
from collections import Counter, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EXPORT_FORMAT = "ams-review-verdicts/1"
DELTA_FORMAT = "ams-review-verdicts-delta/1"
AUTOSAVE_NAME = "verdicts-autosave.json"
CHANGE_LOG_CAP = 100_000
ORPHAN_REASONS = ("orphan", "conflict")
ORPHAN_RECENT_CAP = 200


def now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ServedCorpus:
    """The corpus the server serves: its manifest's `generated_at` and its human unit ids."""

    stamp: str
    ids: frozenset[str]


def parse_autosave_payload(raw: bytes) -> dict | None:
    """Return the ams-review-verdicts/1 document in `raw`, or None when it is not one: a JSON object with that `format`, a string `manifest_generated_at` and a `verdicts` list."""
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


def latest_by_unit(verdicts: Iterable) -> dict[str, dict]:
    """Return each unit's latest record among `verdicts` by `at`, skipping an entry that is not an object with a string `unit`."""
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


def parse_cleared(data: Mapping | None) -> dict[str, str]:
    """Return the tombstones a verdicts document holds (its `cleared` list) as unit → the clear's `at`, skipping malformed entries."""
    cleared = data.get("cleared") if data else None
    if not isinstance(cleared, list):
        return {}
    return {
        entry["unit"]: entry["at"]
        for entry in cleared
        if isinstance(entry, dict) and isinstance(entry.get("unit"), str) and isinstance(entry.get("at"), str)
    }


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


def well_formed(record) -> bool:
    """Return whether `record` is a verdict record the store keeps: an object with a string `unit`, `verdict` and `at`, and a `note`, if any, that is a string or false-valued, which the store writes as `""` (`note_of`). A store file holding any other record is refused (`read_store_document`), a save refuses a delta holding a set whose record, as the store keeps it (`_normalize`), is one (`parse_delta_payload`), and the review app's Import refuses a file holding one (`importVerdicts` with `strict` in `rebuild/review/static/verdicts.js`, whose `wellFormed` mirrors this function). `rebuild/review/fixtures/verdict-record-samples.json` holds sample records with the answer this function gives each, which `rebuild/test_review_autosave.py` checks against this function and `rebuild/review/jstests/verdicts.test.js` against the app's strict Import."""
    return (
        isinstance(record, dict)
        and all(isinstance(record.get(key), str) for key in ("unit", "verdict", "at"))
        and isinstance(record.get("note") or "", str)
    )


def note_of(record: Mapping) -> str:
    """Return `record`'s note as the store writes it: its `note`, or `""` when that is missing or false-valued, which `well_formed` accepts."""
    return record.get("note") or ""


def malformed_record(verdicts: list, name: str) -> str | None:
    """Return the clause that refuses the document called `name` when its `verdicts` hold a record `well_formed` refuses, naming the first such record by its unit, or by its index when it names no unit, and None when every record passes."""
    for index, record in enumerate(verdicts):
        if not well_formed(record):
            unit = record.get("unit") if isinstance(record, dict) else None
            where = f"on {unit}" if isinstance(unit, str) else f"at index {index} of its `verdicts` list"
            return (
                f"{name} holds a verdict record {where} that is not an object with a string `unit`, `verdict` and "
                "`at` and a `note`, if any, that is a string or false-valued"
            )
    return None


class MalformedStore(ValueError):
    """Raised by `read_store_document` for a store holding a record `well_formed` refuses; the message names the store and the first such record (`malformed_record`)."""


def read_store_document(raw: bytes, name: str) -> dict | None:
    """Return the ams-review-verdicts/1 document in `raw` as `parse_autosave_payload` reads it, or None when it is not one, and raise `MalformedStore`, calling the document `name`, when it holds a record `well_formed` refuses, before anything compares its records' `at` values. The review server (`VerdictStore.reload`), the land, the readiness check and the accept read the store here."""
    data = parse_autosave_payload(raw)
    if data is not None and (malformed := malformed_record(data["verdicts"], name)) is not None:
        raise MalformedStore(malformed)
    return data


class MalformedSet(ValueError):
    """Raised by `parse_delta_payload` for an ams-review-verdicts-delta/1 document holding a set it refuses; `where` names the first such set by its unit, or by its index when it names no unit."""

    def __init__(self, where: str) -> None:
        super().__init__(
            f"the {DELTA_FORMAT} document holds a malformed set {where}: a set must be an object with a string "
            "`unit` and `verdict` and a `base_at`, `at` and `note` that are each, when present, a string or null"
        )


class MalformedClear(ValueError):
    """Raised by `parse_delta_payload` for an ams-review-verdicts-delta/1 document whose sets all pass but which holds a clear it refuses; `where` names the first such clear by its unit, or by its index when it names no unit."""

    def __init__(self, where: str) -> None:
        super().__init__(
            f"the {DELTA_FORMAT} document holds a malformed clear {where}: a clear must be a unit id, or an object "
            "with a string `unit` and a `base_at` and `at` that are each, when present, a string or null"
        )


def parse_delta_payload(raw: bytes) -> dict | None:
    """Return the delta with its clears normalized to `{unit, base_at, at}` (a bare unit id has null `base_at` and `at`) and `replay` as a bool, or None when the body is not an ams-review-verdicts-delta/1 document. A set must be an object with a string `unit` whose `base_at`, `at` and `note` are each a string or null, and whose record as the store keeps it (`_normalize`, which reads a missing `at` or `note` as empty) passes `well_formed`, so a set with no string `verdict` is malformed, and a delta holding a malformed set raises `MalformedSet`. A clear must be a unit id, or an object with a string `unit` whose `base_at` and `at` are each a string or null, and a delta whose sets all pass but which holds a malformed clear raises `MalformedClear`. The review app mirrors this rule for one set in `wellFormedSet` (`rebuild/review/static/verdicts.js`), by which its outbox replay (`planOutboxReplay` in `rebuild/review/static/outbox.js`) leaves out each entry whose set the rule refuses. `rebuild/review/fixtures/delta-set-samples.json` holds sample sets with the answer this rule gives each, which `rebuild/test_review_autosave.py` checks against this function and `rebuild/review/jstests/verdicts.test.js` against `wellFormedSet`."""
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
    for index, record in enumerate(sets):
        if not isinstance(record, dict) or not isinstance(record.get("unit"), str):
            raise MalformedSet(f"at index {index} of its `sets` list")
        if not all(_optional_str(record.get(key)) for key in ("base_at", "at", "note")):
            raise MalformedSet(f"on {record['unit']}")
        if not well_formed(_normalize(record)):
            raise MalformedSet(f"on {record['unit']}")
    parsed_clears = []
    for index, clear in enumerate(clears):
        parsed = _parse_clear(clear)
        if parsed is None:
            unit = clear.get("unit") if isinstance(clear, dict) else None
            raise MalformedClear(
                f"on {unit}" if isinstance(unit, str) else f"at index {index} of its `clears` list"
            )
        parsed_clears.append(parsed)
    return {
        "manifest_generated_at": data["manifest_generated_at"],
        "sets": sets,
        "clears": parsed_clears,
        "replay": replay,
    }


def _safe_stamp(stamp: str) -> str:
    return "".join(c if c.isalnum() or c in ".-" else "." for c in stamp)


def orphans_dir_for(store_path: Path) -> Path:
    """Return the orphan documents' directory for the store at `store_path`: `var/verdict-orphans/` beside it, which for the live store is under the repo root's gitignored `var/`."""
    return Path(store_path).parent / "var" / "verdict-orphans"


def orphan_path(orphans_dir: Path, stamp: str) -> Path:
    return Path(orphans_dir) / f"{_safe_stamp(stamp)}.json"


def _orphan_key(record: Mapping) -> tuple:
    return (
        record.get("unit"),
        record.get("verdict"),
        note_of(record),
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
    recorded_at = now_stamp()
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


def _normalize(record: Mapping) -> dict:
    return {
        "unit": record["unit"],
        "verdict": record.get("verdict"),
        "note": note_of(record),
        "at": record.get("at") or "",
    }


def _signature(record: Mapping | None) -> tuple | None:
    return None if record is None else (record.get("verdict"), note_of(record), record.get("at") or "")


@dataclass(slots=True)
class Carried:
    """A store carried onto a corpus by unit id (`carry`): the records and tombstones it keeps, the records it sends to the orphan document, and the units whose skip it dropped."""

    records: dict[str, dict]
    cleared: dict[str, str]
    orphans: list[dict] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def carry(records: Mapping[str, dict], cleared: Mapping[str, str], human_ids: frozenset[str]) -> Carried:
    """Carry a store's `records` and tombstones `cleared` onto a corpus whose human units are `human_ids`, by unit id: a record on one of them stays, a skip among them is dropped and leaves a tombstone at its `at`, so the unit is asked again and no older save brings back the verdict it replaced, a record off them goes to the orphans with `reason: orphan`, a skip off them is dropped, and a tombstone stays on those units alone. The land carries the store onto each new corpus this way, and a save stamped for the served corpus carries a store left on another one (rule 3 in the module docstring)."""
    kept: dict[str, dict] = {}
    tombstones = {unit: at for unit, at in cleared.items() if unit in human_ids}
    orphans: list[dict] = []
    dropped: list[str] = []
    for unit, record in sorted(records.items()):
        skip = record.get("verdict") == "skip"
        if unit in human_ids and not skip:
            kept[unit] = _normalize(record)
            tombstones.pop(unit, None)
        elif unit in human_ids:
            tombstones[unit] = record.get("at") or now_stamp()
            dropped.append(unit)
        elif skip:
            dropped.append(unit)
        else:
            orphans.append({**_normalize(record), "reason": "orphan"})
    return Carried(kept, tombstones, orphans, dropped)


def store_document(
    stamp: str | None, records: Mapping[str, dict], cleared: Mapping[str, str], *, token: str | None = None
) -> dict:
    """Return the store as one ams-review-verdicts/1 document: its records in unit order, its tombstones on units it holds no record for, and, for the full GET, the sync `token` after the head."""
    document: dict = {"format": EXPORT_FORMAT, "manifest_generated_at": stamp, "exported_at": now_stamp()}
    if token is not None:
        document["token"] = token
    document["verdicts"] = [_normalize(records[unit]) for unit in sorted(records)]
    tombstones = [{"unit": unit, "at": at} for unit, at in sorted(cleared.items()) if unit not in records]
    if tombstones:
        document["cleared"] = tombstones
    return document


def store_bytes(document: Mapping) -> bytes:
    """Return a store document as the file holds it, one record per line."""
    head = {key: value for key, value in document.items() if key not in ("verdicts", "cleared")}
    lines = [json.dumps(record, ensure_ascii=False) for record in document["verdicts"]]
    text = json.dumps(head, ensure_ascii=False)[:-1] + ', "verdicts": [\n' + ",\n".join(lines) + "\n]"
    if document.get("cleared"):
        text += f', "cleared": {json.dumps(document["cleared"], ensure_ascii=False)}'
    return (text + "}\n").encode()


def write_store(
    path: Path, stamp: str | None, records: Mapping[str, dict], cleared: Mapping[str, str]
) -> tuple[int, int, int] | None:
    """Write the store at `path` whole (`store_document`, `store_bytes`) through a temporary file renamed over it, so a reader never meets half of one and a write that fails leaves the old file in place, and return the signature (`file_signature`) of the file it wrote, taken before the rename, which keeps the inode, mtime and size: a file another writer renames over `path` right after then reads as a change. The caller holds the store's lock."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(store_bytes(store_document(stamp, records, cleared)))
        written = file_signature(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return written


def file_signature(path: Path) -> tuple[int, int, int] | None:
    """Return the file's (mtime, size, inode), or None when it cannot be stat'ed. A write through a renamed temporary file moves the inode even when the mtime and size match."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


@dataclass(slots=True)
class _Plan:
    """What one delta does, decided before anything is written: the records to set, the clears to apply (unit → the tombstone's `at`), the conflicts and orphans to report, and the units whose skip was dropped."""

    sets: list[dict] = field(default_factory=list)
    clears: dict[str, str] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    orphans: list[dict] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


class VerdictStore:
    """The review server's copy of the store file at `path`, with the orphan documents under `orphans_dir` (by default `orphans_dir_for(path)`)."""

    def __init__(self, path, orphans_dir=None):
        self.path = Path(path)
        self.orphans_dir = Path(orphans_dir) if orphans_dir is not None else orphans_dir_for(self.path)
        self.stamp: str | None = None
        self.records: dict[str, dict] = {}
        self.cleared: dict[str, str] = {}
        self.refusal: str | None = None
        self.refusal_reason: str | None = None
        self._signature: tuple[int, int, int] | None = None
        self._boot = secrets.token_hex(4)
        self._seq = 0
        self._changes: deque[tuple[int, str]] = deque(maxlen=CHANGE_LOG_CAP)
        self.reload()

    def reload(self) -> None:
        """Replace the store's content with the file's. A missing file or one that is not a verdicts document leaves the store empty and unstamped, so the next save writes it. A file holding a verdict record `well_formed` refuses leaves the store empty and unstamped too, but sets `refusal`, which names the file and the first such record, and `refusal_reason`, `store-malformed`, and `receive` refuses every delta with it until a reload of another file clears it, so no save writes over the file. A reload onto the stamp the store holds records each unit whose record or tombstone moved as a change, so every token stays valid; any other reload invalidates every token. The signature comes from the open handle, so it describes the bytes the store parsed even when another writer renames a new file over the path mid-read; the next `refresh_if_changed` then sees the new file and reloads."""
        try:
            with self.path.open("rb") as handle:
                stat = os.fstat(handle.fileno())
                raw = handle.read()
            self._signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except OSError:
            raw = None
            self._signature = None
        self.refusal = None
        self.refusal_reason = None
        data = None
        try:
            data = read_store_document(raw, f"the store {self.path}") if raw is not None else None
        except MalformedStore as malformed:
            self.refusal_reason = "store-malformed"
            self.refusal = (
                f"{malformed}. The review server serves no verdict from it and refuses every save, writing nothing, "
                "until the file is replaced: replace it with a readable store, or move it aside so the server starts "
                "an empty one."
            )
        stamp = data["manifest_generated_at"] if data else None
        records = (
            {unit: _normalize(record) for unit, record in latest_by_unit(data["verdicts"]).items()}
            if data
            else {}
        )
        cleared = {unit: at for unit, at in parse_cleared(data).items() if unit not in records}
        if stamp is not None and stamp == self.stamp:
            moved = {
                unit
                for unit in records.keys() | self.records.keys() | cleared.keys() | self.cleared.keys()
                if _signature(records.get(unit)) != _signature(self.records.get(unit))
                or cleared.get(unit) != self.cleared.get(unit)
            }
            self.records, self.cleared = records, cleared
            self._note_changes(sorted(moved))
            return
        self.stamp, self.records, self.cleared = stamp, records, cleared
        self._invalidate_tokens()

    def refresh_if_changed(self) -> bool:
        """Reload when the store's file moved since the store last read or wrote it (`reload`), and return whether it reloaded."""
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

    def _note_changes(self, units: Iterable[str]) -> None:
        for unit in units:
            self._seq += 1
            self._changes.append((self._seq, unit))

    def payload_dict(self, *, token: bool = False) -> dict:
        """Return the store as one document (`store_document`): the file's form, or with `token` the full GET's, which carries the sync token."""
        return store_document(self.stamp, self.records, self.cleared, token=self.token if token else None)

    def payload_bytes(self, *, token: bool = False) -> bytes:
        """Return `payload_dict` as the file holds it (`store_bytes`)."""
        return store_bytes(self.payload_dict(token=token))

    def changes_since(self, token: str | None) -> dict | None:
        """Return what changed after `token` as a delta (a set for a unit that holds a record, a clear for one that holds none), or None when the token is malformed, from another boot, ahead of the current sequence, or older than the retained changes, so the caller sends the whole store."""
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
        units = sorted({unit for change_seq, unit in self._changes if change_seq > seq})
        return {
            "format": DELTA_FORMAT,
            "manifest_generated_at": self.stamp,
            "token": self.token,
            "sets": [self.records[unit] for unit in units if unit in self.records],
            "clears": [unit for unit in units if unit not in self.records],
        }

    def write(self) -> None:
        """Write the store over its file (`write_store`), taking the signature of what it wrote."""
        self._signature = write_store(self.path, self.stamp, self.records, self.cleared)

    def receive(self, raw: bytes, served: ServedCorpus | None = None) -> tuple[int, dict]:
        """Apply a delta POST body and return the HTTP status and response body (the module docstring gives the rules). `served` is the corpus the server serves, which decides what a delta on another stamp does; None means it is unknown. The caller holds the store's lock, as the server's POST does (`serve.receive_autosave_locked`)."""
        self.refresh_if_changed()
        try:
            delta = parse_delta_payload(raw)
        except (MalformedSet, MalformedClear) as malformed:
            return 400, {"ok": False, "error": str(malformed)}
        if delta is not None:
            if self.refusal is not None:
                return 422, {"ok": False, "reason": self.refusal_reason, "error": self.refusal}
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

    def _admits(self, unit: str, at: str, base_at: str | None) -> bool:
        """Return whether a set or clear whose `at` and `base_at` are given may replace the store's state for `unit` under rule 2: no verdict and no newer tombstone, or a verdict whose `at` is `base_at` or is not newer than `at`."""
        current = self.records.get(unit)
        if current is None:
            tombstone = self.cleared.get(unit)
            return tombstone is None or tombstone <= at
        return current["at"] == base_at or at >= current["at"]

    def _plan(self, delta: dict, *, tested: bool, members: frozenset[str] | None) -> _Plan:
        plan = _Plan()
        for raw_record in delta["sets"]:
            record = _normalize(raw_record)
            unit = record["unit"]
            if members is not None and unit not in members:
                plan.orphans.append({**record, "reason": "orphan"})
                continue
            current = self.records.get(unit)
            if _signature(current) == _signature(record):
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
                plan.clears[unit] = record["at"] or now_stamp()
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
            plan.clears[unit] = at or now_stamp()
        return plan

    def _apply(self, plan: _Plan) -> list[str]:
        """Apply the plan's sets and clears to memory, record the changed units, and return them."""
        changed: list[str] = []
        for record in plan.sets:
            unit = record["unit"]
            if _signature(self.records.get(unit)) != _signature(record):
                self.records[unit] = record
                self.cleared.pop(unit, None)
                changed.append(unit)
        for unit, at in plan.clears.items():
            if self.records.pop(unit, None) is not None:
                self.cleared[unit] = at
                changed.append(unit)
        self._note_changes(changed)
        return changed

    def _reply(self, plan: _Plan, changed: list[str], carried: bool) -> dict:
        return {
            "ok": True,
            "saved": len(self.records),
            "token": self.token,
            "corpus_stamp": self.stamp,
            "carried": changed if carried else [],
            "orphaned": [entry["unit"] for entry in plan.orphans],
            "dropped": plan.dropped,
            "conflicts": [
                {"unit": entry["unit"], "server": self.records.get(entry["unit"])} for entry in plan.conflicts
            ],
        }

    def _write_or_put_back(self) -> None:
        """Write the store, and when the write fails, reload the file, which holds every save but this one, before the error propagates."""
        try:
            self.write()
        except BaseException:
            self.reload()
            raise

    def _receive_delta(self, delta: dict, served: ServedCorpus | None) -> tuple[int, dict]:
        stamp = delta["manifest_generated_at"]
        adopted = None
        if self.stamp is None:
            adopted = served.stamp if served is not None else stamp
        store_stamp = adopted or self.stamp
        if served is not None and stamp == served.stamp and store_stamp != stamp:
            return self._receive_onto_served(delta, served)
        if stamp == store_stamp:
            plan = self._plan(delta, tested=delta["replay"], members=None)
            carried = False
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
            carried = True
        rejected = plan.orphans + plan.conflicts
        if rejected:
            append_orphans(self.orphans_dir, stamp, rejected)
        if adopted is not None:
            self.stamp = adopted
        changed = self._apply(plan)
        if changed or adopted is not None:
            self._write_or_put_back()
        return 200, self._reply(plan, changed, carried)

    def _receive_onto_served(self, delta: dict, served: ServedCorpus) -> tuple[int, dict]:
        """Apply a delta stamped for the served corpus to a store on another stamp (rule 3 in the module docstring): carry the store onto the served corpus by unit id (`carry`), sending what it carries off to the orphan document of its old stamp, apply the delta under rule 1, and write the moved store at once."""
        old_stamp = self.stamp
        moved = carry(self.records, self.cleared, served.ids)
        if moved.orphans and old_stamp is not None:
            append_orphans(self.orphans_dir, old_stamp, moved.orphans)
        self.stamp, self.records, self.cleared = served.stamp, moved.records, moved.cleared
        self._invalidate_tokens()
        plan = self._plan(delta, tested=delta["replay"], members=None)
        changed = self._apply(plan)
        self._write_or_put_back()
        return 200, self._reply(plan, changed, False)
