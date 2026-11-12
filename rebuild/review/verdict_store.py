"""The review server's in-memory copy of verdicts-autosave.json, its delta POST protocol, and its full-store and delta GET formats.

The file is one ams-review-verdicts/1 document holding every verdict on the corpus. Parsing and writing all of it for each saved verdict is too slow, so the server parses the file once, keeps the records in memory, and exchanges changes. A POST must be a delta (`sets` of whole records and `clears`, under ams-review-verdicts-delta/1), which is applied in place and appended to the journal as set and clear lines. A body `parse_delta_payload` refuses, a delta holding a set whose record the carry would refuse (`well_formed`) among them, gets a 400 and writes nothing, so no save adds a record the next pass cannot carry; the 400 for a delta holding a malformed set names the first such set (`MalformedSet`), and the 400 for one whose sets all pass but which holds a malformed clear names the first such clear (`MalformedClear`). A GET with `since=<token>` returns the records changed after that token. A GET without a token, or with one `changes_since` cannot serve, returns the whole store with the current token. The token is a boot id and a change sequence. A token gets the whole store when it predates a server restart or a reload of the file onto another stamp, or when it is older than the retained changes (`CHANGE_LOG_CAP`).

A set may carry `base_at`, the `at` of the record the tab last saw the server hold for that unit (null when it saw none). A clear is a unit id (the form older tabs send) or `{unit, base_at, at}`, where `at` is when the tab cleared it. The store keeps a tombstone for each cleared unit (unit → the clear's `at`), in memory and as a top-level `cleared` list in the file, which readers that use only `verdicts` ignore; a set removes its unit's tombstone.

The store reads a unit through the accepted list beside it (`accepted_path_for`; `rebuild.review.accepted` gives the list, the rule and the override section): an unlisted unit's record, else blank, and for a listed unit its override, a record or a tombstone, which blanks it, else the accepted record a close-out folded into the list, dated the close-out's `closed_at` (`accepted.verdict_of`). Every rule below reads a unit's verdict that way, so a set or clear on an accepted unit with no override meets the accepted record. Every set, clear and undo on a listed unit is applied to the store's override section (`accepted.Overrides`, the file's `overrides`) and never to its own records and tombstones, which name only unlisted units. A clear of a listed unit, and a stale skip rule 2 drops on one, leaves an override tombstone, which blanks it, and a set equal to the unit's accepted record (`Accepted.matches`: its verdict and an empty note) dated the close-out's `closed_at`, which is what a tab's undo back to the accepted record sends, removes the unit's override instead of being stored, so the unit reads as the list names it again; the journal records that as a clear line with a null `at`. Any other set on an accepted unit, the accepted mark picked again with an empty note among them, is stored as an override dated by the tab, so every other tab adopts it as newer than what it holds. A reply reports a unit's verdict as `reported` reads it, an accepted record flagged `accepted: true`. The full GET hands a tab the store's own records and tombstones and its override section as one list of each, as the journal records them, since a tab knows no section. The file's top-level `accepted` field names the list the store pairs with, and a list the store cannot read (`accepted-malformed`) or a store that does not pair with the list (`accepted-mismatch`, `accepted.pair`) is refused as a store holding a malformed record is, below.

Which stamp a delta carries decides how it is applied. The served corpus (`ServedCorpus`, the served manifest's stamp and human unit ids) is what the caller says the server is serving.

1. A delta on the store's stamp is applied last-writer-wins. One marked `replay: true`, which a tab sends from its outbox for saves it could not confirm before it closed, takes rule 2's per-unit test instead.
2. A delta on another stamp, while the store is on the served corpus, is carried by unit id, because a unit id is a content key and names the same unit on every corpus where its content is unchanged. A set applies when the unit has no verdict and no newer tombstone, when its verdict has the `at` the set names as `base_at`, or when the set's `at` is not older than its verdict's (an equal `at` is the same act, whose note an edit or the carry's provenance prefix may have changed); a clear applies on the same tests with its own `at`. Anything else is a conflict: the store keeps its state, the incoming set or clear goes to the orphan document with `reason: conflict`, and the response hands back the unit's verdict. A set on a unit outside the served corpus's human units, and a clear with no `at`, go to the orphan document with `reason: orphan`. A skip is dropped, as the carry drops every skip, so the unit is asked again on the served corpus: when the unit has a verdict, the skip clears it under the same tests (a skip that fails them is a conflict), and the response lists the unit under `dropped`.
3. A delta stamped for the served corpus while the store is on another stamp moves the store onto the served corpus by unit id: the file is kept under its stash name (`stash_path_for`), the store keeps its own records and tombstones on the served corpus's human units, less its skips (as the carry drops every skip), each of which leaves a tombstone at its `at`, and its override section whole, on the served corpus or off it, each skip there becoming a tombstone at its `at` (`Overrides.moved`), so a skipped accepted unit stays blank and an override or clear on an accepted unit outlasts the move, which invalidates every token, the delta is applied under rule 1, and the journal records the move as a transition naming the stash. This is how a store left on another corpus than the served one (by a first pass, which builds the missing corpus in place and lands no store, or by a restore under --yes) follows the corpus the tabs load without leaving its verdicts behind in the stash.
4. Any other delta on another stamp, one made on neither the store's corpus nor the served one, or one arriving while the served corpus is unknown, gets a 503 with `retry: true`, and nothing is written; the tab keeps the verdicts and saves them again. An unstamped or missing store takes the served corpus's stamp first, or the delta's when the served corpus is unknown.

The orphan documents live in `var/verdict-orphans/` beside the store (`orphans_dir_for`), one per stamp the verdicts were made on (`orphan_path`). Each is an ams-review-verdicts/1 document stamped for that corpus, whose records carry `reason` and `recorded_at` (a clear is a record with a null verdict), so nothing a tab sends is dropped, and none of them can become a master, because `status` surveys only the repo root. Every applied delta's response carries `corpus_stamp` (the store's stamp), `accepted` (the list's sha256 and `closed_at`, both null with no list), `carried` (the units a delta on another stamp changed), `orphaned` (the unit ids sent to the orphan document as orphans), `dropped` (the units whose skip was dropped under rule 2), and `conflicts` (each unit with its verdict, or null when it reads blank).

A full-store POST gets a 400 with `reason: whole-store-post-unsupported` and recovery instructions, regardless of its stamp or the file's state, and writes nothing. A 409 tells legacy readers to reload rather than download pending verdicts, and `corpus_stamp` can prompt newer clients to reload automatically, so the refusal uses neither. When the store writes the file itself, it writes an ams-review-verdicts/1 document with one record per line, a chunk of about 1 MiB at a time, never joined whole (`payload_chunks`), which `parse_autosave_payload`, the merge tool, the status check and the carry all read; GETs and export files use that format too, and the server streams the full GET from the same chunks.

The file holds the verdicts between server runs, and another writer (a pass's land, `rebuild.review.landing`, or the merge tool or a journal restore under --yes) can replace it while the server runs; each holds the store's lock (`rebuild.review.store_lock`) for its write, as the server does for each POST. So every request first compares the file's mtime, size and inode, and the accepted list's, with what the store last read or wrote, and reloads on a mismatch. A reload onto the same stamp diffs the new records and overrides against the ones in memory, reduced to a digest a record before the parse, and records the changed units, so outstanding tokens stay valid and a tab's next sync fetches only those units; a reload onto another stamp, onto a file that is missing or unreadable, or beside another accepted list, frees the records in memory before the parse and invalidates every token, so a reload never holds two generations. A file holding a verdict record `well_formed` refuses is read as an unreadable one is, so the store serves no verdict from it, but unlike an unreadable one it is never written over: while it stays, every delta `parse_delta_payload` accepts gets a 422 and a GET a 404 (`serve.missing_autosave_reply`), both with `reason: store-malformed` and an error naming the file and the first such record (`malformed_record`), which the app shows the reader, and nothing is written until another writer replaces the file. A delta that changes no record and no stamp writes nothing, so the file's bytes and mtime stay as they were. A delta whose write fails reloads the file before the error propagates, so memory never holds a record the file lacks and a resend of the same delta is written. The orphan document is written before the store, so a delta whose orphan write fails changes nothing.

Before a store write the journal will record, the store writes the marker that says the journal may lack a write (`journal.mark_unjournaled`), and it removes the marker once the journal append succeeds, unless the marker was already there. A marker that cannot be written fails the save as a failed store write does, so no store write goes unmarked. An append that fails (reported as `journal_error`), is cut short, or never runs because the server was killed leaves it, so the next land that moves the stamp journals a base (`journal.base_due`).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from bisect import bisect_left, insort
from collections import Counter, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from rebuild.review import accepted, journal
from rebuild.review.accepted import Accepted, Overrides, View
from rebuild.review.journal import parse_cleared

EXPORT_FORMAT = "ams-review-verdicts/1"
DELTA_FORMAT = "ams-review-verdicts-delta/1"
CHANGE_LOG_CAP = 100_000
_CHUNK_CHARS = 1 << 20
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


def well_formed(record) -> bool:
    """Return whether `record` is a verdict record the carry reads: an object with a string `unit`, `verdict` and `at`, and a `note`, if any, that is a string or false-valued, since the carry (`carry_verdicts.main`) and the merge (`merge_verdicts.merge_into`) both write a false-valued note as `""` (`note_of`). The carry refuses a verdicts file that holds any other record (`carry_verdicts.resolve_prior`), the merge refuses an input file that holds one (`merge_verdicts`), the review app's Import refuses a file that holds one (`importVerdicts` with `strict` in `rebuild/review/static/verdicts.js`, whose `wellFormed` mirrors this function, as `accepted._well_formed` does for the land's stream readers), and a save refuses a delta holding a set whose record, as the store keeps it (`_normalize`), is one (`parse_delta_payload`), so neither the merge, an Import, nor a save adds a record the carry would refuse. `rebuild/review/fixtures/verdict-record-samples.json` holds sample records with the answer this function gives each, which `rebuild/test_review_autosave.py` checks against this function, `rebuild/test_review_accepted.py` against `accepted._well_formed` and `rebuild/review/jstests/verdicts.test.js` against the app's strict Import."""
    return (
        isinstance(record, dict)
        and all(isinstance(record.get(key), str) for key in ("unit", "verdict", "at"))
        and isinstance(record.get("note") or "", str)
    )


def note_of(record: dict) -> str:
    """Return `record`'s note as the carry and the merge write it and the readers of a verdicts file read it: its `note`, or `""` when that is missing or false-valued, which `well_formed` accepts. Two readers spell the same expression out instead of importing it: `journal`, which this module imports, and `review_queue`, because the standing fill imports it and the standing memo's code stamp covers the standing fill's import closure (`standing_verdicts.MEMO_CODE_MODULES`), which would then take in this module and the journal."""
    return record.get("note") or ""


def malformed_record(verdicts: list, name: str) -> str | None:
    """Return the clause that refuses the document called `name` when its `verdicts` hold a record `well_formed` refuses, naming the first such record by its unit, or by its index when it names no unit, and None when every record passes. The merge refuses an input file or a store stamped for the corpus with it (`merge_verdicts`), a journal restore the store it replays (`merge_verdicts.run_restore`), and the readers that read a store through `read_store_document` the store they read."""
    for index, record in enumerate(verdicts):
        if not well_formed(record):
            return malformed_clause(index, record, name)
    return None


def malformed_clause(index: int, record: object, name: str) -> str:
    """Return the clause that refuses the document called `name` for `record`, a record `well_formed` refuses at `index` of its `verdicts` list, naming it by its unit, or by its index when it names no unit (`malformed_record`); the land names the first such record a stream reader found (`accepted.read_tail`)."""
    unit = record.get("unit") if isinstance(record, dict) else None
    where = f"on {unit}" if isinstance(unit, str) else f"at index {index} of its `verdicts` list"
    return (
        f"{name} holds a verdict record {where} that is not an object with a string `unit`, `verdict` and "
        "`at` and a `note`, if any, that is a string or false-valued"
    )


class MalformedStore(ValueError):
    """Raised by `read_store_document`, and by the land's stream check (`landing._tail_of`), for a store holding a record `well_formed` refuses; the message names the store and the first such record (`malformed_record`)."""


def read_store_document(raw: bytes, name: str) -> dict | None:
    """Return the ams-review-verdicts/1 document in `raw` as `parse_autosave_payload` reads it, or None when it is not one, and raise `MalformedStore`, calling the document `name`, when it holds a record `well_formed` refuses, before anything compares its records' `at` values: `journal.latest_by_unit` compares them with `>`, so a record whose `at` is not a string beside one whose `at` is a string on the same unit raises TypeError. These readers read their store here: the land's whole-read fallback (`landing._land_whole`), the snapshot step's and a dead land's recovery's (`landing.read_store`), the review server's (`VerdictStore.reload`), the readiness check's (`status._verdict_store_check`), and the re-key's of its `--autosave` store (`rebuild.tools.rekey_verdicts`). The merge reads its store itself, refusing one stamped for the corpus that holds such a record (`malformed_in`) and setting an older-stamped one aside whatever its own records hold, though not one whose override section holds such a record (`malformed_override`), since it keeps that section (`merge_verdicts`), the carry refuses such a record in any file it reads (`carry_verdicts`), and status's count of the carry candidates gives a file whose records it cannot compare no count (`status._effective_count`). The land's streamed reads of the snapshot, the prepared store and the live store check the same rule a record at a time instead (`landing._tail_of`), through `accepted.read_tail` with its copy of `well_formed` (`accepted._well_formed`, which `rebuild/test_review_accepted.py` holds to `well_formed`) and through `malformed_clause` and `malformed_override`, so a change to the rule here or to `malformed_in` must reach that check too."""
    data = parse_autosave_payload(raw)
    if data is not None and (malformed := malformed_in(data, name)) is not None:
        raise MalformedStore(malformed)
    return data


def malformed_in(document: dict, name: str) -> str | None:
    """Return `malformed_record`'s clause for the document called `name`, over its own `verdicts` and then over its override section's (`accepted.Overrides`), so a store holding a malformed override is refused as one holding a malformed record is; None when every record passes. The land's streamed reads make the same two checks without holding the document (`landing._tail_of`), so a clause added here must reach that check too."""
    return malformed_record(document["verdicts"], name) or malformed_override(document, name)


def malformed_override(document: dict, name: str) -> str | None:
    """Return `malformed_record`'s clause for the override section of the document called `name`, and None when it has none or every record there passes. The merge checks a store it sets aside with it alone, since it keeps that store's section and none of its own records (`merge_verdicts`)."""
    section = document.get(accepted.OVERRIDES)
    if isinstance(section, dict) and isinstance(section.get("verdicts"), list):
        return malformed_record(section["verdicts"], f"the override section of {name}")
    return None


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
    """Return the delta with its clears normalized to `{unit, base_at, at}` (a bare unit id has null `base_at` and `at`) and `replay` as a bool, or None when the body is not an ams-review-verdicts-delta/1 document. A set must be an object with a string `unit` whose `base_at`, `at` and `note` are each a string or null, and whose record as the store keeps it (`_normalize`, which reads a missing `at` or `note` as empty) passes `well_formed`, so a set with no string `verdict` is malformed, and a delta holding a malformed set raises `MalformedSet`. A clear must be a unit id, or an object with a string `unit` whose `base_at` and `at` are each a string or null, and a delta whose sets all pass but which holds a malformed clear raises `MalformedClear`. `receive` answers a body that is not a delta, and a delta holding a malformed set or clear, with a 400 before it writes the store or the journal. The review app mirrors this rule for one set in `wellFormedSet` (`rebuild/review/static/verdicts.js`), by which its outbox replay (`planOutboxReplay` in `rebuild/review/static/outbox.js`) leaves out each entry whose set the rule refuses, so one such set does not take the rest of the replay down with it. `rebuild/review/fixtures/delta-set-samples.json` holds sample sets with the answer this rule gives each, which `rebuild/test_review_autosave.py` checks against this function and `rebuild/review/jstests/verdicts.test.js` against `wellFormedSet`."""
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


def stash_path_for(path: Path, stamp: str) -> Path:
    return path.with_name(f"{path.stem}-{_safe_stamp(stamp)}{path.suffix}")


def link_or_copy(source: Path, target: Path) -> None:
    """Give the file at `source` the name `target` too, through a temporary name and a rename, so a reader of `target` never sees it half made; where the link fails, as across filesystems, copy the file instead. Every writer of the store replaces its file by rename, so a write never reaches the other name."""
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
        "note": note_of(record),
        "at": record.get("at") or "",
    }


def _signature(record: dict) -> tuple:
    return (record.get("verdict"), note_of(record), record.get("at") or "")


def _signature_digest(record: dict) -> int:
    """Return an 8-byte digest of the record's `_signature`, this process's 64-bit `hash` of it, which a same-stamp reload compares in its place (`_Reduced`); a reload computes both sides in one process."""
    return hash(_signature(record))


@dataclass(frozen=True, slots=True)
class _Reduced:
    """A generation reduced to what a same-stamp reload's diff reads (`_diff_units`): each own record's unit with `_signature_digest` of it, each override record's apart, and the override tombstones as they were."""

    records: dict[str, int]
    overrides: dict[str, int]
    override_cleared: dict[str, str]


def _diff_units(held: _Reduced, new_records: dict[str, dict], new_overrides: Overrides) -> list[str]:
    """Return the units whose answer a same-stamp reload from the generation `held` onto `new_records` and `new_overrides` can change: each unit whose own record changed, came or went, and each unit whose override record or tombstone changed, came or went, which changes its answer, blanks it or returns it to its accepted record (`accepted.verdict_of`). A change to an unlisted unit's tombstone alone changes no answer. A record changed when its `_signature_digest` did, so the units are those a comparison of the records' `_signature`s gives."""
    changed = [
        unit for unit, record in new_records.items() if held.records.get(unit) != _signature_digest(record)
    ]
    changed.extend(unit for unit in held.records if unit not in new_records)
    now = new_overrides
    for unit in sorted(
        held.overrides.keys() | held.override_cleared.keys() | now.records.keys() | now.cleared.keys()
    ):
        after = now.records.get(unit)
        if held.overrides.get(unit) != (
            _signature_digest(after) if after is not None else None
        ) or held.override_cleared.get(unit) != now.cleared.get(unit):
            changed.append(unit)
    return changed


def _chunks(pieces: Iterable[str]) -> Iterator[bytes]:
    """Join `pieces` and yield them encoded, a chunk of about `_CHUNK_CHARS` characters at a time."""
    held: list[str] = []
    size = 0
    for piece in pieces:
        held.append(piece)
        size += len(piece)
        if size >= _CHUNK_CHARS:
            yield "".join(held).encode()
            held.clear()
            size = 0
    if held:
        yield "".join(held).encode()


def accepted_path_for(store: Path) -> Path | None:
    """Return the accepted list beside the store at `store` (`accepted.path_for`), which for the live store is the tracked rebuild/accepted-units.txt, or None for a store under any other name, which reads no list. The review server's store and the readiness check take the list this way when no caller names one; the tools that run on a store under another name, the cycle's prepared store among them, take `--accepted`."""
    store = Path(store)
    return accepted.path_for(store) if store.name == accepted.AUTOSAVE_NAME else None


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
    """What one delta does, decided before anything is written: the records to set, the clears to apply (unit → the tombstone's `at`), the units a set returns to their accepted record (`restores`), the conflicts and orphans to report, and the units whose skip was dropped."""

    sets: list[dict]
    clears: dict[str, str]
    restores: list[str]
    conflicts: list[dict]
    orphans: list[dict]
    dropped: list[str]


class VerdictStore:
    def __init__(self, path, journal_path=None, orphans_dir=None, accepted_path=None):
        self.path = Path(path)
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self.orphans_dir = Path(orphans_dir) if orphans_dir is not None else orphans_dir_for(self.path)
        self.accepted_path = (
            Path(accepted_path) if accepted_path is not None else accepted_path_for(self.path)
        )
        self.accepted: Accepted = Accepted.empty(self.accepted_path or accepted.RELATIVE_PATH)
        self.stamp: str | None = None
        self.records: dict[str, dict] = {}
        self.cleared: dict[str, str] = {}
        self.overrides = Overrides()
        self.refusal: str | None = None
        self.refusal_reason: str | None = None
        self._lines: dict[str, str] = {}
        self._order: list[str] = []
        self._override_order: list[str] = []
        self._signature: tuple[int, int, int] | None = None
        self._accepted_signature: tuple[int, int, int] | None = None
        self._accepted_read = False
        self._accepted_error: accepted.Refused | None = None
        self._paired_sha: str | None = None
        self._field_cache: tuple[tuple, object, str] | None = None
        self._boot = secrets.token_hex(4)
        self._seq = 0
        self._changes: deque[tuple[int, str]] = deque(maxlen=CHANGE_LOG_CAP)
        self.reload()

    @property
    def view(self) -> View:
        """The store's own records and tombstones and its override section read with the accepted list through `accepted.verdict_of`, as every reader of the store reads a unit."""
        return View(self.records, self.cleared, self.accepted, self.overrides)

    def _read_accepted(self) -> None:
        """Read the accepted list again when its file's signature moved since the last read, keeping a malformed one's refusal (`accepted.Malformed`) for `reload` to raise. The signature is taken before the read, so a list renamed into place during the read is read again on the next refresh."""
        if self.accepted_path is None:
            return
        signature = file_signature(self.accepted_path)
        if self._accepted_read and signature == self._accepted_signature:
            return
        self._accepted_signature = signature
        self._accepted_read = True
        try:
            self.accepted = accepted.read(self.accepted_path)
            self._accepted_error = None
        except accepted.Malformed as malformed:
            self.accepted = Accepted.empty(self.accepted_path)
            self._accepted_error = malformed

    def reload(self) -> None:
        """Replace the store's content with the file's, and read the accepted list again when it moved. A missing file or one that is not a verdicts document leaves the store empty and unstamped, so the next save overwrites it. A file holding a verdict record `well_formed` refuses leaves the store empty and unstamped too, but sets `refusal`, which names the file and the first such record (`read_store_document`, which refuses it before anything compares its records' `at` values), and `refusal_reason`, `store-malformed`, and `receive` refuses every delta with it until a reload of another file clears it, so no save writes over the file. A malformed list (`accepted-malformed`) and a store that does not pair with the list (`accepted-mismatch`, `accepted.pair`) are refused the same way, until the list or the store is replaced. When the file holds the stamp the store already has and the list is the one the store last paired with, the units whose answer can differ (`_diff_units`) are recorded as changes and every token stays valid; otherwise every token is invalidated, since a new list changes the answer for every unit it names.

        A reload never holds two generations: before it parses the file it frees the one the store holds (`_release`). When the file's head names another stamp than the store's (`accepted.head_stamp` over its first `accepted.HEAD_BYTES`), the list moved, or the file cannot be read, it frees it whole, since every token then goes and no diff is needed. When the head names the store's stamp or does not settle one, it first reduces it to each own and override record's unit with an 8-byte digest of its `_signature`, and the override tombstones as they are (`_Reduced`), which `_diff_units` compares the new records against. A file whose head names another stamp than its whole parse does, which no repo writer makes (`accepted.head_stamp`), invalidates every token even when the parse lands on the store's stamp. A reload that raises leaves the store empty and unstamped with no signature, so the next request reads the file again.

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
        self.refusal = None
        self.refusal_reason = None
        self._read_accepted()
        held_stamp = self.stamp
        same_list = self._paired_sha == self.accepted.sha256
        head = accepted.head_stamp(raw[: accepted.HEAD_BYTES]) if raw is not None else None
        held = self._release(
            raw is not None and same_list and held_stamp is not None and head in (None, held_stamp)
        )
        try:
            data = None
            try:
                data = read_store_document(raw, f"the store {self.path}") if raw is not None else None
            except MalformedStore as malformed:
                self.refusal_reason = "store-malformed"
                self.refusal = (
                    f"{malformed}. The review server serves no verdict from it and refuses every save, writing neither "
                    "the store nor the journal, until the file is replaced: it reads a store holding a record the carry "
                    "refuses (`verdict_store.well_formed`) as it reads an unreadable one, but never writes over it. "
                    "Restore a readable store from the journal with "
                    "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`."
                )
            raw = None
            if self.refusal is None:
                try:
                    if self._accepted_error is not None:
                        raise self._accepted_error
                    accepted.pair(data, self.accepted, store=self.path)
                except accepted.Refused as refused:
                    data = None
                    self.refusal_reason = refused.reason
                    self.refusal = (
                        f"{refused}. Until then the review server serves no verdict and refuses every save, writing "
                        "neither the store nor the journal."
                    )
            stamp = data["manifest_generated_at"] if data else None
            new_records = journal.latest_by_unit(data["verdicts"]) if data else {}
            cleared = parse_cleared(data)
            overrides = Overrides.of(data)
            data = None
            self._paired_sha = self.accepted.sha256
            if held is not None and stamp is not None and stamp == held_stamp:
                changed = _diff_units(held, new_records, overrides)
                held = None
                self._replace(stamp, new_records, cleared, overrides)
                self._note_changes(changed)
                return
            held = None
            self._replace(stamp, new_records, cleared, overrides)
            self._invalidate_tokens()
        except BaseException:
            self._release(False)
            self.stamp = None
            self._signature = None
            self._invalidate_tokens()
            raise

    def _release(self, reduce: bool) -> _Reduced | None:
        """Free the generation the store holds, its records, lines, orders, tombstones and override section, and return it reduced (`_Reduced`) when `reduce` asks for it, else None. The stamp stays until the reload that calls it replaces it."""
        held = (
            _Reduced(
                {unit: _signature_digest(record) for unit, record in self.records.items()},
                {unit: _signature_digest(record) for unit, record in self.overrides.records.items()},
                self.overrides.cleared,
            )
            if reduce
            else None
        )
        self.records = {}
        self.cleared = {}
        self.overrides = Overrides()
        self._lines = {}
        self._order = []
        self._override_order = []
        return held

    def refresh_if_changed(self) -> bool:
        """Reload when the store's file or the accepted list's moved since the store last read it (`reload`), and return whether it reloaded."""
        if file_signature(self.path) == self._signature and (
            self.accepted_path is None or file_signature(self.accepted_path) == self._accepted_signature
        ):
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

    def _replace(
        self, stamp: str | None, new_records: dict[str, dict], cleared: dict[str, str], overrides: Overrides
    ) -> None:
        self.stamp = stamp
        self.records = {unit: _normalize(record) for unit, record in new_records.items()}
        self.cleared = {unit: at for unit, at in cleared.items() if unit not in self.records}
        self.overrides = Overrides(
            {unit: _normalize(record) for unit, record in overrides.records.items()}, dict(overrides.cleared)
        )
        held = (*self.records.items(), *self.overrides.records.items())
        self._lines = {unit: json.dumps(record, ensure_ascii=False) for unit, record in held}
        self._order = sorted(self.records)
        self._override_order = sorted(self.overrides.records)

    def _record(self, unit: str) -> dict | None:
        """Return the unit's record in the part of the store that holds its entries: its override record for a listed unit, its own record otherwise."""
        return (self.overrides.records if unit in self.accepted.verdicts else self.records).get(unit)

    def _set(self, record: dict) -> bool:
        """Store `record`, in the override section for a listed unit and among the store's own records otherwise, replacing the unit's record and tombstone there, and return whether it changed anything."""
        record = _normalize(record)
        unit = record["unit"]
        listed = unit in self.accepted.verdicts
        existing = self._record(unit)
        if existing is not None and _signature(existing) == _signature(record):
            return False
        if existing is None:
            insort(self._override_order if listed else self._order, unit)
        if listed:
            self.overrides.set(record)
        else:
            self.records[unit] = record
            self.cleared.pop(unit, None)
        self._lines[unit] = json.dumps(record, ensure_ascii=False)
        return True

    def _drop_record(self, unit: str) -> bool:
        listed = unit in self.accepted.verdicts
        records = self.overrides.records if listed else self.records
        if unit not in records:
            return False
        del records[unit]
        del self._lines[unit]
        order = self._override_order if listed else self._order
        del order[bisect_left(order, unit)]
        return True

    def _clear(self, unit: str, at: str) -> bool:
        """Clear `unit`, leaving a tombstone at `at`: an unlisted unit's record goes and its tombstone stays, and a listed unit that does not already read blank takes an override tombstone in place of its override record or its accepted record. A unit that already reads blank changes nothing."""
        if unit in self.accepted.verdicts:
            if unit in self.overrides.cleared:
                return False
            self._drop_record(unit)
            self.overrides.cleared[unit] = at
            return True
        if not self._drop_record(unit):
            return False
        self.cleared[unit] = at
        return True

    def _restore(self, unit: str) -> bool:
        """Return `unit`, a listed one, to its accepted record: its override record or tombstone goes and nothing is left, so it reads as the list names it."""
        dropped = self._drop_record(unit)
        return self.overrides.cleared.pop(unit, None) is not None or dropped

    def _note_changes(self, units) -> None:
        for unit in units:
            self._seq += 1
            self._changes.append((self._seq, unit))

    def _cleared_list(self) -> list[dict]:
        return [{"unit": unit, "at": at} for unit, at in sorted(self.cleared.items())]

    def payload_dict(self) -> dict:
        """Return the store as its file holds it, as one document: its `accepted` field, its own records and tombstones, and its override section when it holds one."""
        payload = {
            "format": EXPORT_FORMAT,
            "manifest_generated_at": self.stamp,
            "exported_at": journal.now_stamp(),
        }
        if (field := self.accepted.field()) is not None:
            payload["accepted"] = field
        payload["verdicts"] = [self.records[unit] for unit in self._order]
        if self.cleared:
            payload["cleared"] = self._cleared_list()
        if (section := self.overrides.field()) is not None:
            payload[accepted.OVERRIDES] = section
        return payload

    def list_stamp(self) -> dict:
        """Return the accepted list's sha256 and `closed_at` (both None with no list), which every reply that applies a save carries, so a tab that loaded another list moves."""
        return {"sha256": self.accepted.sha256, "closed_at": self.accepted.closed_at}

    def _app_field(self, within: frozenset[str] | None) -> str:
        """Return the review app's `accepted` field as JSON (`View.app_field` over `within`, the served corpus's human unit ids, or every listed id when they are unknown), rebuilt only when the list, `within` or a record or tombstone moved, since every change moves the token."""
        key = (self._boot, self._seq, self.accepted.sha256)
        cached = self._field_cache
        if cached is not None and cached[0] == key and cached[1] is within:
            return cached[2]
        field = self.view.app_field(within if within is not None else self.accepted.verdicts)
        text = json.dumps(field, ensure_ascii=False)
        self._field_cache = (key, within, text)
        return text

    def payload_bytes(self, *, token: bool = False, within: frozenset[str] | None = None) -> bytes:
        """Return the whole store as one ams-review-verdicts/1 document, one record per line, with the tombstones after the records when there are any: `payload_chunks` joined. Without `token` it is the store file, whose `accepted` field names the list the store pairs with when there is one and whose override section follows the tombstones when the store holds one. With `token` it is the full GET a tab reads: the sync token and the review app's `accepted` field are added after the head (the list's sha256 and `closed_at` and, by verdict, the listed ids among `within`, the served corpus's human unit ids, that hold no override, `View.app_field`), and the override section's records and tombstones are listed with the store's own, as one store (`accepted.joined`), since a tab knows no section."""
        return b"".join(self.payload_chunks(token=token, within=within))

    def payload_chunks(self, *, token: bool = False, within: frozenset[str] | None = None) -> Iterator[bytes]:
        """Yield `payload_bytes`' document a chunk of about 1 MiB at a time, never joining it whole: the store file's write (`write`) and the server's full GET (`serve.stream_full_store`) both take it this way. The first chunk is the head, with the token when `token` asks for it, built when the first chunk is asked for, and the order of the store's own and override records is copied then too. Each later chunk reads the record lines the store holds as it is built, so a save between two chunks, as one landing across the full GET's `await` can, is tolerated: a unit whose line has gone is left out, and a unit set since is not listed, which the tab's next sync from the token fetches. With no save between the chunks they are `payload_bytes` byte for byte."""
        head = {
            "format": EXPORT_FORMAT,
            "manifest_generated_at": self.stamp,
            "exported_at": journal.now_stamp(),
        }
        if token:
            head["token"] = self.token
        elif (field := self.accepted.field()) is not None:
            head["accepted"] = field
        prefix = json.dumps(head, ensure_ascii=False)[:-1]
        if token:
            prefix += f', "accepted": {self._app_field(within)}'
        own, overridden = list(self._order), list(self._override_order)
        yield f'{prefix}, "verdicts": [\n'.encode()
        yield from _chunks(self._payload_pieces(token, own, overridden))

    def _payload_pieces(self, token: bool, own: list[str], overridden: list[str]) -> Iterator[str]:
        """Yield the text of `payload_chunks`' document after its head, over the units `own` and `overridden` name, as `payload_chunks` describes."""
        if token:
            yield from self._joined_lines(own, overridden)
            yield "\n]"
            cleared = {**self.cleared, **self.overrides.cleared}
            if cleared:
                tombstones = [{"unit": unit, "at": at} for unit, at in sorted(cleared.items())]
                yield f', "cleared": {json.dumps(tombstones, ensure_ascii=False)}'
            yield "}\n"
            return
        yield from self._joined_lines(own)
        yield "\n]"
        if self.cleared:
            yield f', "cleared": {json.dumps(self._cleared_list(), ensure_ascii=False)}'
        if self.overrides:
            yield f', "{accepted.OVERRIDES}": {{"verdicts": [\n'
            yield from self._joined_lines(overridden)
            section_cleared = [
                {"unit": unit, "at": at} for unit, at in sorted(self.overrides.cleared.items())
            ]
            yield f'\n], "cleared": {json.dumps(section_cleared, ensure_ascii=False)}}}'
        yield "}\n"

    def _joined_lines(self, *orders: list[str]) -> Iterator[str]:
        """Yield the record lines of the units `orders` name, in order and joined by `,\\n`, leaving out a unit the store no longer holds a line for."""
        first = True
        for order in orders:
            for unit in order:
                line = self._lines.get(unit)
                if line is None:
                    continue
                if not first:
                    yield ",\n"
                first = False
                yield line

    def reported(self, unit: str) -> dict | None:
        """Return `unit`'s verdict as a reply reports it to a tab: `accepted.verdict_of`'s answer, with `accepted: true` on an accepted record, so the tab keeps it out of its count since the close-out and out of Export, or None when the unit reads blank."""
        view = self.view
        record = view.verdict_of(unit)
        if record is not None and view.is_accepted(unit):
            return {**record, "accepted": True}
        return record

    def changes_since(self, token: str | None) -> dict | None:
        """Return what changed after `token`, or None when the token is malformed, from another boot, ahead of the current sequence, or older than the retained changes, so the caller sends the whole store. Each changed unit is reported as `reported` reads it: a set for a unit that reads verdicted, its accepted record among them, so a removed override or tombstone on an accepted unit comes back as the accepted record, and a clear for one that reads blank."""
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
        sets = []
        clears = []
        for unit in sorted(units):
            record = self.reported(unit)
            if record is None:
                clears.append(unit)
            else:
                sets.append(record)
        return {
            "format": DELTA_FORMAT,
            "manifest_generated_at": self.stamp,
            "token": self.token,
            "sets": sets,
            "clears": clears,
        }

    def _write_bytes(self, chunks: Iterable[bytes]) -> None:
        """Write `chunks` to the store's temporary file one at a time and rename it over the store, taking the signature from the temporary file. A write that fails removes the temporary file."""
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with tmp.open("wb") as handle:
                for chunk in chunks:
                    handle.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        signature = file_signature(tmp)
        os.replace(tmp, self.path)
        self._signature = signature

    def write(self) -> None:
        self._write_bytes(self.payload_chunks())

    def receive(self, raw: bytes, served: ServedCorpus | None = None) -> tuple[int, dict]:
        """Apply a delta POST body and return the HTTP status and response body. A body `parse_delta_payload` refuses gets a 400 and writes nothing: a delta holding a malformed set or clear is told which one (`MalformedSet`, `MalformedClear`), a full store gets recovery instructions, and any other body is told it is not an ams-review-verdicts-delta/1 document. `served` is the corpus the server serves, which decides what a delta on another stamp does; None means it is unknown. While the file holds a record `well_formed` refuses (`refusal`), a delta gets a 422 naming the file and the record, and nothing is written."""
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
        """Return whether a set or clear whose `at` and `base_at` are given may replace the store's state for `unit` under rule 2, read through `accepted.verdict_of`: no verdict and no newer tombstone, an override tombstone on a listed unit among them, or a verdict, an accepted record dated `closed_at` among them, whose `at` is `base_at` or is not newer than `at`."""
        view = self.view
        current = view.verdict_of(unit)
        if current is None:
            tombstone = view.tombstone(unit)
            return tombstone is None or tombstone <= at
        return current["at"] == base_at or at >= current["at"]

    def _plan(self, delta: dict, *, tested: bool, members: frozenset[str] | None) -> _Plan:
        plan = _Plan(sets=[], clears={}, restores=[], conflicts=[], orphans=[], dropped=[])
        view = self.view
        for raw_record in delta["sets"]:
            record = _normalize(raw_record)
            unit = record["unit"]
            if members is not None and unit not in members:
                plan.orphans.append({**record, "reason": "orphan"})
                continue
            current = view.verdict_of(unit)
            restore = self.accepted.matches(record) and record["at"] == self.accepted.closed_at
            if restore and view.is_accepted(unit):
                continue
            if not restore and current is not None and _signature(current) == _signature(record):
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
            if restore:
                plan.restores.append(unit)
                continue
            plan.sets.append(record)
        for clear in delta["clears"]:
            unit = clear["unit"]
            at = clear["at"]
            gone = {"unit": unit, "verdict": None, "note": "", "at": at or ""}
            if tested and at is None:
                plan.orphans.append({**gone, "reason": "orphan"})
                continue
            if unit not in view:
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
        sets, clears, restores = self._apply(plan)
        changed = [record["unit"] for record in sets] + clears + restores
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
            "saved": len(self.records) + len(self.overrides.records),
            "token": self.token,
            "corpus_stamp": self.stamp,
            "accepted": self.list_stamp(),
            "carried": changed if source == "autosave-carried" else [],
            "orphaned": [entry["unit"] for entry in plan.orphans],
            "dropped": plan.dropped,
            "conflicts": [
                {"unit": entry["unit"], "server": self.reported(entry["unit"])} for entry in plan.conflicts
            ],
        }
        if self.journal_path is not None and changed:
            seed_records, seed_cleared = (
                (None, None) if self.journal_path.exists() else self._store_before(sets, clears + restores)
            )
            self._journal(
                body,
                marked,
                journal.record_delta,
                source=source,
                stamp=target,
                sets=[self._record(record["unit"]) for record in sets],
                clears={**{unit: self.view.tombstone(unit) for unit in clears}, **dict.fromkeys(restores)},
                seed_records=seed_records,
                seed_cleared=seed_cleared,
                accepted=self.accepted.field(),
            )
        return 200, body

    def _apply(self, plan: _Plan) -> tuple[list[dict], list[str], list[str]]:
        """Apply the plan's sets, restores and clears to memory, record the changed units, and return the sets, clears and restores that changed something."""
        sets = [record for record in plan.sets if self._set(record)]
        restores = [unit for unit in plan.restores if self._restore(unit)]
        clears = [unit for unit, at in plan.clears.items() if self._clear(unit, at)]
        self._note_changes([record["unit"] for record in sets] + clears + restores)
        return sets, clears, restores

    def _receive_delta_onto_served_stamp(self, delta: dict, served: ServedCorpus) -> tuple[int, dict]:
        """Apply a delta stamped for the served corpus to a store on another stamp (rule 3 in the module docstring): keep the file under its stash name, carry the store's own records and tombstones on the served corpus's human units onto the served stamp by unit id, less its skips, each of which leaves a tombstone at its `at`, keep its override section whole (`Overrides.moved`), apply the delta under rule 1, and journal the move as one transition that names the stash. The stash is a second name for the old file (a copy where the filesystem refuses a hard link), and the new store is renamed over it, so a failed write leaves the old store in place."""
        stamp = delta["manifest_generated_at"]
        old_stamp = self.stamp
        old_verdicts, old_cleared = accepted.joined(self.records, self.cleared, self.overrides)
        stash = stash_path_for(self.path, old_stamp) if old_stamp is not None else None
        if stash is not None and self.path.exists():
            link_or_copy(self.path, stash)
        else:
            stash = None
        kept = {
            unit: record
            for unit, record in self.records.items()
            if unit in served.ids and record["verdict"] != "skip"
        }
        cleared = {unit: at for unit, at in self.cleared.items() if unit in served.ids}
        for unit, record in self.records.items():
            if unit in served.ids and record["verdict"] == "skip":
                cleared[unit] = record["at"] or journal.now_stamp()
        self._replace(stamp, kept, cleared, self.overrides.moved(journal.now_stamp()))
        self._invalidate_tokens()
        self._apply(self._plan(delta, tested=delta["replay"], members=None))
        try:
            marked = self._mark_unjournaled()
            self.write()
        except BaseException:
            self.reload()
            raise
        new_verdicts, new_cleared = accepted.joined(self.records, self.cleared, self.overrides)
        body = {
            "ok": True,
            "saved": len(new_verdicts),
            "token": self.token,
            "corpus_stamp": stamp,
            "accepted": self.list_stamp(),
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
                old_verdicts=list(old_verdicts.values()),
                new_verdicts=list(new_verdicts.values()),
                old_cleared=old_cleared,
                new_cleared=new_cleared,
                stashed=stash.name if stash is not None else None,
                accepted=self.accepted.field(),
            )
        return 200, body

    def stamp_empty_store(self, stamp: str) -> dict | None:
        """With no store file beside an accepted list, write an empty store stamped `stamp`, the served corpus's, that names the list, and journal it as a base event, so the review app shows every accepted unit of a fresh clone, a worktree or a machine whose store was deleted instead of a 404 that restores nothing. Return the write's report, which holds `journal_error` when the append failed, or None when a store file exists, a refusal stands, or there is no list. The review server calls it at boot under the store's lock."""
        field = self.accepted.field()
        if self.path.exists() or self.refusal is not None or field is None or self.stamp is not None:
            return None
        self.stamp = stamp
        try:
            marked = self._mark_unjournaled()
            self.write()
        except BaseException:
            self.reload()
            raise
        report: dict = {"stamp": stamp, "accepted": field}
        if self.journal_path is not None:
            self._journal(
                report,
                marked,
                journal.record_transition,
                source="autosave",
                stamp=stamp,
                old_stamp=None,
                old_verdicts=[],
                new_verdicts=[],
                new_cleared={},
                accepted=field,
            )
        return report

    def _store_before(self, sets, clears) -> tuple[dict[str, dict], dict[str, str]]:
        """Return the store's records and tombstones, its override section's among them (`accepted.joined`), minus the units this delta touched (`clears` names the cleared and restored units), to seed a journal that does not exist yet. The touched units are left out, not reverted, because the delta's own journal lines record them."""
        touched = {record["unit"] for record in sets} | set(clears)
        every, tombstones = accepted.joined(self.records, self.cleared, self.overrides)
        records = {unit: record for unit, record in every.items() if unit not in touched}
        cleared = {unit: at for unit, at in tombstones.items() if unit not in touched}
        return records, cleared

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
