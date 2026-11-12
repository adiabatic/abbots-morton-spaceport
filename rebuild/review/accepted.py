"""The accepted list, `rebuild/accepted-units.txt`: the units a close-out folds out of the verdict store, how a tool finds and reads the list, how the store names the list it was folded against, and the one rule by which every reader of the store says what a unit's verdict is.

The file is ASCII. Its first line is the header `# ams-accepted-units/1 closed_at=<stamp>`, the stamp in `journal.now_stamp()`'s form (whole seconds, `Z`): the close-out's time, the later of the current time and the largest `at` among the records it folded. Readers compare stamps as strings, and a stamp without fractional seconds sorts after every millisecond stamp in its second, so `closed_at` is at or after every folded record's `at`. Each other line is one unit id, `u-` and eleven base58 symbols (`unit_cache.unit_id_for`), in string order, which is also the ids' 64-bit order, followed by nothing for approve, ` e` for either and ` i` for identical (`MARKS`); the marked verdicts are `ACCEPTING_VERDICTS`, and a neither, a skip or a reject stays in the store. Every line ends in a newline. Only the close-out and the re-key write the file, through `render`. A reader refuses anything else as `accepted-malformed` (`Malformed`), naming the line: a merge conflict marker, a bad header, a line that is not a `u-` id (an `e-` duplicate-group id among them), an unknown mark, an id out of order or repeated, a byte outside ASCII, or a last line with no newline.

The store keeps every record and tombstone it holds on a listed id, an override, in a top-level `overrides` section of its own, `{"verdicts": [...], "cleared": [...]}` (`Overrides`): an override record there, an override tombstone there, a unit holding at most one of the two, and the store's own `verdicts` and `cleared` never name a listed id. The section takes the shape of the store's own two lists, so the parsers and checks of those lists (`journal.latest_by_unit`, `journal.parse_cleared`, `verdict_store.well_formed`) read it unchanged, and a reader of the store's own lists passes over it as it passes over `accepted`. Every write to a listed id (a save, a clear, an undo, a fill, a merge, a carry, a land, a re-key) goes to the section, and no writer drops an entry from it: a writer that keeps only some of the store's own records, such as one that moves the store onto another corpus, copies the section through whole, and replaces an entry only by its own rule (a newer save beats an older one), a skip there becoming a tombstone at its `at` on a stamp move as the carry drops every skip (`Overrides.moved`). The close-out alone consumes it: an override that is an accepting verdict folds into the next list, and a reject, neither, skip or tombstone takes the id out of it. The journal records the two parts as one store, each set and clear line naming its unit whichever part holds it, since the list each event names partitions the ids: `joined` gives a journal writer the one store, and `split` gives a restore its two parts back.

`verdict_of` answers for a unit in this order: for an id the list does not name, the store's own record, or blank (None) when it holds none; for a listed id, its override, a record answering with itself and a tombstone with blank, since a clear after the close-out is a re-verdict to blank; else its accepted record `{unit, verdict, note: "", at: closed_at}`. A skip is the store's record like any other, which each reader reads as blank as it always has. A listed id that is not on the corpus a reader reads is ignored by that reader, as a store record off its corpus is, so nothing between close-outs rewrites the list.

The store document's top-level `accepted` field, `{"sha256": <hex of the list file's bytes>, "closed_at": <the header's stamp>}` (`Accepted.field`), names the list the store was folded against. Every writer of the store carries it forward, and a reader of `verdicts` alone passes over it, as it passes over `cleared` and `overrides`. `pair` refuses as `accepted-mismatch` (`Mismatch`) a store whose field names another list than the one beside it, a store that names a list where none exists, and a list beside a store that holds records, tombstones or overrides and names none, naming both and the remedy, so a checkout, stash or revert that separates them stops every reader until the two pair again. An Export from the review app names no list and holds the store's records alone, its overrides among them, without the tombstones, so a read-path tool reads it through `view_of_file`, which pairs only the store and a document that names a list, splits an Export's records on listed ids off into its overrides, and takes its tombstones from the store beside the list. A store with no record, no tombstone, no override and no field pairs with whatever list the tree holds and records the field at its first write; no store at all pairs too, since the review server writes an empty stamped store at boot; and no list beside a store that names none is an empty list.

The list's path follows the store's, as the journal's does (`journal.beside_store`): `path_for` gives `rebuild/accepted-units.txt` beside a store named exactly `verdicts-autosave.json`, the live store's name, and refuses any other name, so a tool run on a store in a temporary directory never reads the live list. A tool takes `--accepted PATH` for any other store, the cycle's prepared store among them.

This module imports the standard library alone and reads nothing at import, because `rebuild.tools.review_queue` imports it and `standing_verdicts.MEMO_CODE_MODULES` is the standing fill's import closure: a repo import here would put that module's code into the standing memo's stamp. So it spells out what it shares with other modules, and `rebuild/test_review_accepted.py` checks each against its source: the base58 alphabet and the id width (`unit_cache`), the live store's name, the store document's format and the latest-record and tombstone reads of a store document (`journal`), the rule a verdict record meets (`verdict_store.well_formed`), and the accepting verdicts (`review_queue.ACCEPTING_VERDICTS`).

The API:

- `path_for(store, flag="--accepted")`: the list beside a store named verdicts-autosave.json; `ValueError` asking for `flag` for any other name.
- `read(path) -> Accepted`: the whole list as a dict from id to verdict, with its sha256 and `closed_at`; `Malformed` for a malformed file; an empty `Accepted` (no sha256, no `closed_at`) when the file does not exist.
- `parse(raw, path) -> Accepted`: the list whose bytes are `raw`, as `read` gives it for a file at `path`, which names it in a refusal; for a list held in memory, such as a close-out archive's.
- `read_stamp(path) -> ListStamp`: the list's sha256 and `closed_at` alone, for a reader that pairs before it streams the ids; it checks the header but no id line.
- `iter_lines(path)`: each `(id, verdict)` in the list's order, holding one line at a time, for a streaming reader; `Malformed` when it reaches a malformed line; nothing when the file does not exist.
- `read_tail(path, each=None) -> Tail | None`: a verdicts store document read once a block of about 1 MiB at a time, holding one record of its `verdicts` list at a time: every top-level member but that list, with its stamp (`.stamp`), `accepted` field (`.accepted`), tombstones (`.cleared`, as `journal.parse_cleared` reads them) and override section (`.overrides()`), how many records the list holds, the first record `verdict_store.well_formed` refuses with its index (`.malformed`), and how many `verdicts` members it names; None for a file that is missing, unreadable, not JSON anywhere in it or not an ams-review-verdicts/1 document, as `verdict_store.parse_autosave_payload` reads one. It decodes the file as `json.loads` decodes bytes, UTF-16 and a byte order mark among them, so a reader that checks a store with it gives the answer the readers that parse it whole give. `each` is called with every record it reads. The land, the merge and the carry read their stores through it and `iter_records` (`rebuild.review.landing`, `rebuild.tools.merge_verdicts`, `rebuild.tools.carry_verdicts`).
- `iter_records(source)`: the records of a document's first `verdicts` list, from a path or a binary file read from its start, in file order and one at a time, for a reader that checked the document with `read_tail`; `ValueError` for text that is not a JSON object.
- `head_stamp(head) -> str | None`: a verdicts document's top-level `manifest_generated_at` read from its first bytes, member by member up to its `verdicts` key, without parsing a record; None when those bytes do not settle it, and the caller parses the whole file. It answers as `json.loads` does except on a document with a second top-level `manifest_generated_at` after its `verdicts` array, which it judges by the first and `json.loads` by the second; no repo writer makes one.
- `read_head_stamp(path) -> str | None`: `head_stamp` of the first `HEAD_BYTES` (4096) bytes a read of the regular file at `path` starts with, read at the opened descriptor's offset without moving it, so a later read of the path sees every byte, and None for a path that is not a regular file, such as a pipe, whose bytes a read consumes; for a reader that checks a verdicts file's stamp before it parses the file: the duplicate and standing fills, the complaint list and the queue snapshot, through `review_queue.verdicts_stamp`. `rebuild.review.status` reads the same head and hands it to `head_stamp` itself, since it parses a file the head holds whole from those bytes.
- `render(closed_at, verdicts) -> bytes`: the file's bytes for a dict from id to verdict, sorted; `ValueError` for a stamp, id or verdict the format cannot hold.
- `pair(document, listed, *, store)`: `Mismatch` unless the store document (None when no store file exists) pairs with `listed`, an `Accepted` or a `ListStamp`; `store` names the store in the refusal, whose remedy is `make close-out ARGS=--apply` while a close-out's intent is left beside the list.
- `close_out_intent_for(list_path)`: where a close-out leaves its intent, var/cycle/close-out.json beside the store whose list is at `list_path`, or None for a list no close-out writes.
- `verdict_of(unit, records, overrides, accepted) -> dict | None`: the rule above, over a dict from unit to the store's latest record, its override section (`Overrides`), and the list; an accepted record is a new dict on each call.
- `Overrides(records, cleared)`: the store's override section, a dict from unit to its latest override record and one from unit to the `at` of its override tombstone. `Overrides.of(document)` reads a store document's section (an empty one when it has none), `.field()` is the section a writer writes (None when it is empty, which a writer records by leaving it out), `in` says whether a unit holds an entry, `.set(record)`, `.clear(unit, at)` and `.drop(unit)` replace or remove a unit's entry, and `.moved(now)` is the section a stamp move keeps.
- `records_of(document) -> list`: every record a verdicts document holds, its own and its override section's, for a reader of another file's records.
- `split(records, cleared, listed) -> (records, cleared, Overrides)`: the store's own records and tombstones and its override section from records and tombstones held as one store, such as a journal replay's or an Export's, a listed id's going to the section; a tombstone on a unit that holds a record is passed over.
- `joined(records, cleared, overrides) -> (records, cleared)`: the one store a journal writer records, the section's entry winning on a unit both parts name.
- `View(records, cleared, accepted, overrides=None)`: the bulk form, a read-only mapping over the store's own records and tombstones, the list and the override section (none when None) that answers `in`, `[]`, `get` and `verdict_of` through the rule and makes an accepted record only when asked. It keeps references, not copies, so a view over the server's live dicts follows their changes. Iterating it walks the store's own records on unlisted ids, the override records, and then the listed ids that hold no override, and `len` counts the same, walking the list; a reader that counts the store's own records iterates `view.records` and `view.overrides.records`. `tombstone(unit)` is the `at` of the unit's tombstone in the part that holds its entries, `is_accepted(unit)` says whether the unit's answer comes from the list, `from_list(within=None)` yields each `(id, verdict)` whose answer does, in the list's order, within a set of ids when one is given, and `app_field(within)` is the review app's `accepted` field of the full GET of /autosave: `{"sha256", "closed_at", "approve": [...], "either": [...], "identical": [...]}` over `from_list(within)`.
- `view_of_file(document, accepted, *, path) -> View`: the view of a verdicts file a read-path tool is given: `view_of` for the store or a document that names an `accepted` field, and for an Export from the review app, which names none and holds no tombstones, a view over its records, split by the list (`split`), its own tombstones and those of the store beside the list (`store_for`), which must pair.
- `store_for(list_path)`: the store beside the list at `list_path`, verdicts-autosave.json in the directory that holds rebuild/accepted-units.txt, or None for a list at any other path.
- `view_of(document, accepted, *, store) -> View`: `pair`, then a view over the document's latest record for each unit, its tombstones and its override section, which every reader that reads a store document uses.
- `Accepted`: the list a reader holds; `.path`, `.sha256` and `.closed_at` (None when the file does not exist), `.verdicts`, `.field()` (the store's field, None with no file), `.record(unit)` (the unit's accepted record whatever the store holds, None when the unit is not listed), `.matches(record)` (whether a record equals its unit's accepted record by value: listed, the same verdict, an empty note), and `.keeps_out(record)` (whether the merge and the carry drop an incoming record on a listed id that holds no override: it matches, or it is dated at or before `closed_at`). `Accepted.empty(path)` is the list of a tree that has none.
- `ListStamp`: `.path`, `.sha256`, `.closed_at` and `.field()`, as on `Accepted`.
- `Refused`, a `ValueError` whose `reason` is the code a reply carries and whose message names the file and the remedy: `Malformed` (`MALFORMED`, with `.path` and `.line`) and `Mismatch` (`MISMATCH`).

`rebuild/review/fixtures/accepted-samples.json` holds sample lists with the answer `read` gives each, sample pairings, a store and list with each unit's answer and the app's field and records, and two folds over the contract fixture's human units; its README section says who checks what.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import json
import os
import re
from collections.abc import Callable, Container, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

FORMAT = "ams-accepted-units/1"
EXPORT_FORMAT = "ams-review-verdicts/1"
RELATIVE_PATH = Path("rebuild") / "accepted-units.txt"
CLOSE_OUT_INTENT = Path("var") / "cycle" / "close-out.json"
AUTOSAVE_NAME = "verdicts-autosave.json"
OVERRIDES = "overrides"
MALFORMED = "accepted-malformed"
MISMATCH = "accepted-mismatch"
MARKS = {"approve": "", "either": "e", "identical": "i"}
ACCEPTING_VERDICTS = frozenset(MARKS)
ID_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ID_SYMBOLS = 11
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
HEAD_BYTES = 4096

_VERDICT_OF_MARK = {mark: verdict for verdict, mark in MARKS.items() if mark}
_UNIT_ID = re.compile(rf"u-[{ID_ALPHABET}]{{{ID_SYMBOLS}}}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_HEADER = re.compile(rf"# {re.escape(FORMAT)} closed_at=(.*)")
_CONFLICT_MARKERS = ("<<<<<<<", "|||||||", "=======", ">>>>>>>")
_HASH_BLOCK = 1 << 20
_READ_BLOCK = 1 << 20
_TRUNCATION_SLACK = 16
_WHITESPACE = re.compile(r"[ \t\n\r]*")
_Scanner = Callable[[str, int], tuple[Any, int]]
_SCAN: _Scanner = json.JSONDecoder().scan_once  # pyright: ignore[reportAttributeAccessIssue]
_SCAN_STRING: _Scanner = json.decoder.scanstring  # pyright: ignore[reportAttributeAccessIssue]
_WRITTEN_WHOLE = (
    f"a close-out or a re-key writes {RELATIVE_PATH} whole, so put back a version one wrote "
    f"(`git log -- {RELATIVE_PATH}` lists them) rather than editing or merging it by hand"
)
_NO_VERDICTS_REMEDY = (
    "on a machine that makes no verdicts, instead stop the review server, delete the store and its journal by hand, "
    "and start the server again, which writes an empty store for the tree's list at boot"
)
_NAMED_REMEDY = (
    f"put back the list the store names (`git log -- {RELATIVE_PATH}` finds the commit that holds it), and undo a "
    f"close-out with `make close-out ARGS=--undo` rather than by reverting its list; {_NO_VERDICTS_REMEDY}"
)
_UNNAMED_REMEDY = (
    "put back the list the store was made beside (the one before that close-out, or none); "
    f"{_NO_VERDICTS_REMEDY}"
)


class Refused(ValueError):
    """A refusal of the accepted list or of the store's pairing with it. `reason` is the code a reply carries, and the message names the file and the remedy."""

    reason = ""


class Malformed(Refused):
    """Raised for a list file the format does not allow; `line` is its 1-based line number, the header being line 1."""

    reason = MALFORMED

    def __init__(self, path: Path, line: int, problem: str) -> None:
        self.path = path
        self.line = line
        super().__init__(f"{path} line {line} {problem}: {_WRITTEN_WHOLE}")


class Mismatch(Refused):
    """Raised by `pair` for a store and a list that were not folded together."""

    reason = MISMATCH


@dataclass(frozen=True, slots=True)
class ListStamp:
    """A list file's identity as the store's `accepted` field records it; `sha256` and `closed_at` are None when the file does not exist."""

    path: Path
    sha256: str | None
    closed_at: str | None

    def field(self) -> dict[str, str] | None:
        """Return the store's `accepted` field for this list, or None when there is no list, which a writer records by leaving the field out."""
        if self.sha256 is None or self.closed_at is None:
            return None
        return {"sha256": self.sha256, "closed_at": self.closed_at}


@dataclass(frozen=True, slots=True)
class Accepted:
    """The accepted list as a reader holds it: its stamp and a dict from id to verdict."""

    stamp: ListStamp
    verdicts: Mapping[str, str]

    @classmethod
    def empty(cls, path: str | Path) -> Accepted:
        return cls(ListStamp(Path(path), None, None), {})

    @property
    def path(self) -> Path:
        return self.stamp.path

    @property
    def sha256(self) -> str | None:
        return self.stamp.sha256

    @property
    def closed_at(self) -> str | None:
        return self.stamp.closed_at

    def field(self) -> dict[str, str] | None:
        return self.stamp.field()

    def record(self, unit: str) -> dict | None:
        """Return the unit's accepted record, `{unit, verdict, note: "", at: closed_at}`, whatever the store holds for it, or None when the list does not name it."""
        verdict = self.verdicts.get(unit)
        if verdict is None:
            return None
        return {"unit": unit, "verdict": verdict, "note": "", "at": self.closed_at}

    def matches(self, record: Mapping) -> bool:
        """Return whether `record` equals its unit's accepted record by value: the list names the unit under the record's verdict, and the record's note is empty or false-valued (`verdict_store.note_of`)."""
        unit = record.get("unit")
        verdict = self.verdicts.get(unit) if isinstance(unit, str) else None
        return verdict is not None and verdict == record.get("verdict") and not (record.get("note") or "")

    def keeps_out(self, record: Mapping) -> bool:
        """Return whether the merge and the carry drop `record`, an incoming record on a listed id the store holds no override for: the list names its unit and it either `matches` or is dated at or before `closed_at`, so a stale export or carried file cannot undo an acceptance, while a note typed on an accepted unit after the close-out survives."""
        unit = record.get("unit")
        if not isinstance(unit, str) or unit not in self.verdicts or self.closed_at is None:
            return False
        return self.matches(record) or (record.get("at") or "") <= self.closed_at


def path_for(store: str | Path, flag: str = "--accepted") -> Path:
    """Return the list beside the store at `store`: `rebuild/accepted-units.txt` under the store's directory, which for the live store is the tracked file. Only a store named exactly verdicts-autosave.json has it, and for any other name this raises `ValueError` asking for `flag`, which the tools report as a usage error, for the reason `journal.beside_store` gives."""
    store = Path(store)
    if store.name != AUTOSAVE_NAME:
        raise ValueError(f"{store} is not named {AUTOSAVE_NAME}, so {flag} has no default: pass {flag}")
    return store.parent / RELATIVE_PATH


def _is_stamp(text: str) -> bool:
    if _STAMP.fullmatch(text) is None:
        return False
    try:
        datetime.strptime(text, STAMP_FORMAT)
    except ValueError:
        return False
    return True


def _line_text(raw: bytes, path: Path, number: int) -> str:
    try:
        line = raw.removesuffix(b"\n").decode("ascii")
    except UnicodeDecodeError:
        raise Malformed(path, number, "holds a byte outside ASCII") from None
    if line.startswith(_CONFLICT_MARKERS):
        raise Malformed(path, number, f"is a merge conflict marker, {line!r}")
    if not raw.endswith(b"\n"):
        raise Malformed(path, number, "ends the file without a newline")
    return line


def _read_header(handle: BinaryIO, path: Path, digest) -> str:
    raw = handle.readline()
    if digest is not None:
        digest.update(raw)
    if not raw:
        raise Malformed(
            path, 1, f"is missing: the file is empty, with no `# {FORMAT} closed_at=<stamp>` header"
        )
    line = _line_text(raw, path, 1)
    match = _HEADER.fullmatch(line)
    if match is None or not _is_stamp(match[1]):
        raise Malformed(
            path,
            1,
            f"is {line!r}, not the header `# {FORMAT} closed_at=<stamp>` with the stamp in whole seconds "
            "and `Z`, as `journal.now_stamp` writes it",
        )
    return match[1]


def _lines(handle: BinaryIO, path: Path, digest) -> Iterator[tuple[str, str]]:
    previous = ""
    for number, raw in enumerate(handle, start=2):
        if digest is not None:
            digest.update(raw)
        line = _line_text(raw, path, number)
        unit, space, mark = line.partition(" ")
        if _UNIT_ID.fullmatch(unit) is None:
            raise Malformed(path, number, f"names {unit!r}, which is not a `u-` unit id")
        verdict = _VERDICT_OF_MARK.get(mark) if space else "approve"
        if verdict is None:
            raise Malformed(
                path,
                number,
                f"marks {unit} {mark!r}: approve takes no mark, either ` e` and identical ` i`",
            )
        if unit <= previous:
            problem = (
                "repeats" if unit == previous else f"is out of order: it follows {previous}, a later id than"
            )
            raise Malformed(path, number, f"{problem} {unit}")
        previous = unit
        yield unit, verdict


def _parse(handle: BinaryIO, path: Path) -> Accepted:
    digest = hashlib.sha256()
    closed_at = _read_header(handle, path, digest)
    verdicts = dict(_lines(handle, path, digest))
    return Accepted(ListStamp(path, digest.hexdigest(), closed_at), verdicts)


def read(path: str | Path) -> Accepted:
    """Return the list at `path` as `Accepted`, reading the file once and hashing it as it goes. A file that does not exist is the empty list of a tree with none; a malformed one raises `Malformed`."""
    path = Path(path)
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return Accepted.empty(path)
    with handle:
        return _parse(handle, path)


def parse(raw: bytes, path: str | Path) -> Accepted:
    """Return the list whose bytes are `raw` as `read` returns a file at `path` holding them, `path` naming it in a refusal; for a list held in memory, such as a close-out archive's."""
    return _parse(io.BytesIO(raw), Path(path))


def read_stamp(path: str | Path) -> ListStamp:
    """Return the stamp of the list at `path` without holding its ids: the header's `closed_at`, which it checks, and the sha256 of the whole file. A reader that streams the ids with `iter_lines` checks them there."""
    path = Path(path)
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return ListStamp(path, None, None)
    digest = hashlib.sha256()
    with handle:
        closed_at = _read_header(handle, path, digest)
        while block := handle.read(_HASH_BLOCK):
            digest.update(block)
    return ListStamp(path, digest.hexdigest(), closed_at)


def iter_lines(path: str | Path) -> Iterator[tuple[str, str]]:
    """Yield each `(id, verdict)` of the list at `path` in the list's order, one line held at a time, after checking the header. It raises `Malformed` on reaching a malformed line, having yielded the lines before it, and yields nothing when the file does not exist."""
    path = Path(path)
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return
    with handle:
        _read_header(handle, path, None)
        yield from _lines(handle, path, None)


def render(closed_at: str, verdicts: Mapping[str, str]) -> bytes:
    """Return the list file's bytes for `verdicts`, a dict from id to accepting verdict, under a header stamped `closed_at`, the ids sorted. It raises `ValueError` for a stamp not in `journal.now_stamp`'s form, an id that is not a `u-` id, or a verdict outside `ACCEPTING_VERDICTS`, so the close-out and the re-key never write a file `read` refuses."""
    if not isinstance(closed_at, str) or not _is_stamp(closed_at):
        raise ValueError(f"closed_at {closed_at!r} is not a stamp in whole seconds and `Z`")
    lines = [f"# {FORMAT} closed_at={closed_at}\n"]
    for unit in sorted(verdicts):
        verdict = verdicts[unit]
        if not isinstance(unit, str) or _UNIT_ID.fullmatch(unit) is None:
            raise ValueError(f"{unit!r} is not a `u-` unit id")
        mark = MARKS.get(verdict)
        if mark is None:
            raise ValueError(f"{unit} has verdict {verdict!r}, which a close-out does not accept")
        lines.append(f"{unit} {mark}\n" if mark else f"{unit}\n")
    return "".join(lines).encode("ascii")


def _tombstones(document: Mapping) -> dict[str, str]:
    cleared = document.get("cleared")
    if not isinstance(cleared, list):
        return {}
    return {
        entry["unit"]: entry["at"]
        for entry in cleared
        if isinstance(entry, dict) and isinstance(entry.get("unit"), str) and isinstance(entry.get("at"), str)
    }


def _latest_by_unit(verdicts) -> dict[str, dict]:
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


@dataclass(slots=True)
class Overrides:
    """The store's override section: its latest record on each listed id it holds one for (`records`) and the `at` of its tombstone on each listed id it holds no record for (`cleared`)."""

    records: dict[str, dict] = field(default_factory=dict)
    cleared: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, document: Mapping | None) -> Overrides:
        """Return the override section of the store document `document`, read as the store's own records and tombstones are read (`journal.latest_by_unit`, `journal.parse_cleared`), a tombstone on a unit the section holds a record for passed over; an empty one when it has none."""
        section = document.get(OVERRIDES) if document is not None else None
        if not isinstance(section, Mapping):
            return cls()
        records = _latest_by_unit(section.get("verdicts") or ())
        return cls(records, {unit: at for unit, at in _tombstones(section).items() if unit not in records})

    def __contains__(self, unit: object) -> bool:
        return unit in self.records or unit in self.cleared

    def __bool__(self) -> bool:
        return bool(self.records or self.cleared)

    def copy(self) -> Overrides:
        return Overrides(dict(self.records), dict(self.cleared))

    def set(self, record: dict) -> None:
        """Make `record` its unit's override, replacing the unit's record or tombstone."""
        unit = record["unit"]
        self.records[unit] = record
        self.cleared.pop(unit, None)

    def clear(self, unit: str, at: str) -> None:
        """Make a tombstone at `at` the unit's override, replacing its record or tombstone."""
        self.records.pop(unit, None)
        self.cleared[unit] = at

    def drop(self, unit: str) -> bool:
        """Remove the unit's override, so it reads as its accepted record again, and return whether it held one."""
        held = self.records.pop(unit, None) is not None
        return self.cleared.pop(unit, None) is not None or held

    def moved(self, now: str) -> Overrides:
        """Return the section a move of the store onto another corpus keeps: every entry, whatever corpus its unit is on, each skip record becoming a tombstone at its `at` (`now` for an empty one), since the carry drops every skip and a skipped unit is asked again."""
        records = {unit: record for unit, record in self.records.items() if record.get("verdict") != "skip"}
        cleared = dict(self.cleared)
        for unit, record in self.records.items():
            if unit not in records:
                cleared[unit] = record.get("at") or now
        return Overrides(records, cleared)

    def field(self) -> dict | None:
        """Return the section as a store document holds it, its records and tombstones each sorted by unit, or None when it is empty."""
        if not self:
            return None
        return {
            "verdicts": [self.records[unit] for unit in sorted(self.records)],
            "cleared": [{"unit": unit, "at": at} for unit, at in sorted(self.cleared.items())],
        }


def records_of(document: Mapping) -> list:
    """Return every record a verdicts document holds as its records: its own `verdicts` and its override section's, so a reader that takes another file's records, such as a merge's input or a carry's prior file, takes the overrides of a file written as a store, a stash or a restore's preview among them. A document's tombstones are not records."""
    own = document.get("verdicts") or []
    section = document.get(OVERRIDES)
    if isinstance(section, Mapping) and isinstance(section.get("verdicts"), list):
        return [*own, *section["verdicts"]]
    return list(own)


def split(
    records: Mapping[str, dict], cleared: Mapping[str, str], listed: Container[str]
) -> tuple[dict[str, dict], dict[str, str], Overrides]:
    """Return the store's own records and tombstones and its override section from `records` and `cleared` held as one store, each record and tombstone on an id in `listed` going to the section; a tombstone on a unit that holds a record is passed over."""
    own = {unit: record for unit, record in records.items() if unit not in listed}
    overrides = Overrides({unit: record for unit, record in records.items() if unit in listed})
    tombstones: dict[str, str] = {}
    for unit, at in cleared.items():
        if unit not in records:
            (overrides.cleared if unit in listed else tombstones)[unit] = at
    return own, tombstones, overrides


def joined(
    records: Mapping[str, dict], cleared: Mapping[str, str], overrides: Overrides
) -> tuple[dict[str, dict], dict[str, str]]:
    """Return the store's own records and tombstones and its override section as one store, the records and tombstones a journal writer records, the section's entry winning on a unit both name."""
    every = {unit: record for unit, record in records.items() if unit not in overrides}
    every.update(overrides.records)
    tombstones = {unit: at for unit, at in cleared.items() if unit not in overrides and unit not in every}
    tombstones.update(overrides.cleared)
    return every, tombstones


def _well_formed(record: object) -> bool:
    return (
        isinstance(record, dict)
        and all(isinstance(record.get(key), str) for key in ("unit", "verdict", "at"))
        and isinstance(record.get("note") or "", str)
    )


class _NotJSON(Exception):
    """Raised inside the stream readers for text that is not the JSON object they read."""


class _Text:
    """A JSON document's text decoded a block at a time, as `json.loads` decodes the whole file (`json.detect_encoding`, `surrogatepass`), holding the text from the value being read on and at most a block beyond it."""

    __slots__ = ("_handle", "_decoder", "text", "pos", "eof")

    def __init__(self, handle: BinaryIO) -> None:
        start = handle.read(4)
        self._handle = handle
        self._decoder = codecs.getincrementaldecoder(json.detect_encoding(start))("surrogatepass")
        self.text = self._decoder.decode(start, False)
        self.pos = 0
        self.eof = False

    def _more(self) -> None:
        """Drop the text before `pos` and decode the next block, at least as long as the text kept, so a value longer than a block is read again a bounded number of times."""
        block = self._handle.read(max(_READ_BLOCK, len(self.text) - self.pos))
        self.eof = not block
        self.text = self.text[self.pos :] + self._decoder.decode(block, self.eof)
        self.pos = 0

    def _cut_short(self, at: int, problem: str) -> bool:
        """Return whether a value read up to `at`, or an error there, may come from the block's end rather than the document, while the file has more: an unterminated string, which names where it began, or anything within a few characters of the end, where a number such as `12.5` reads as `12`."""
        return not self.eof and (
            at >= len(self.text) - _TRUNCATION_SLACK or problem.startswith("Unterminated string")
        )

    def peek(self) -> str:
        """Skip whitespace and return the next character, or "" at the end of the file."""
        while True:
            found = _WHITESPACE.match(self.text, self.pos)
            self.pos = found.end() if found is not None else self.pos
            if self.pos < len(self.text):
                return self.text[self.pos]
            if self.eof:
                return ""
            self._more()

    def take(self, char: str) -> bool:
        if self.peek() != char:
            return False
        self.pos += 1
        return True

    def value(self) -> object:
        """Read the JSON value at the next character whole, reading more of the file while the value may run past the text held."""
        if self.pos >= len(self.text) or self.text[self.pos] in " \t\n\r":
            self.peek()
        while True:
            try:
                value, end = _SCAN(self.text, self.pos)
            except StopIteration as stop:
                if self._cut_short(stop.value, ""):
                    self._more()
                    continue
                raise _NotJSON from None
            except json.JSONDecodeError as error:
                if self._cut_short(error.pos, error.msg):
                    self._more()
                    continue
                raise _NotJSON from None
            if self._cut_short(end, ""):
                self._more()
                continue
            self.pos = end
            return value

    def key(self) -> str:
        if self.peek() != '"':
            raise _NotJSON
        while True:
            try:
                key, end = _SCAN_STRING(self.text, self.pos + 1)
            except json.JSONDecodeError as error:
                if self._cut_short(error.pos, error.msg):
                    self._more()
                    continue
                raise _NotJSON from None
            self.pos = end
            return key

    def members(self) -> Iterator[str]:
        """Yield each key of the object at the next character, leaving its value for the caller to read (`value` or `elements`) before the next key."""
        if not self.take("{"):
            raise _NotJSON
        if self.take("}"):
            return
        while True:
            key = self.key()
            if not self.take(":"):
                raise _NotJSON
            yield key
            if self.take(","):
                continue
            if self.take("}"):
                return
            raise _NotJSON

    def elements(self) -> Iterator[object]:
        """Yield each element of the array at the next character, one held at a time."""
        if not self.take("["):
            raise _NotJSON
        if self.take("]"):
            return
        while True:
            yield self.value()
            after = self.peek()
            self.pos += 1
            if after == "]":
                return
            if after != ",":
                raise _NotJSON

    def finish(self) -> None:
        if self.peek() != "":
            raise _NotJSON


@dataclass(frozen=True, slots=True)
class Tail:
    """A verdicts document as `read_tail` reads it without holding its own records: every top-level member but `verdicts` (`members`), how many records its `verdicts` list holds (`records`), the index and the record of the first one the carry refuses (`malformed`, by the rule `verdict_store.well_formed` states), and how many `verdicts` members it names (`lists`), the last of which `json.loads` keeps."""

    members: dict
    records: int
    malformed: tuple[int, object] | None
    lists: int

    @property
    def stamp(self) -> str:
        return self.members["manifest_generated_at"]

    @property
    def accepted(self) -> object:
        """The document's `accepted` field as it holds it, None when it has none."""
        return self.members.get("accepted")

    @property
    def cleared(self) -> dict[str, str]:
        """The document's tombstones, read as `journal.parse_cleared` reads them."""
        return _tombstones(self.members)

    def overrides(self) -> Overrides:
        """The document's override section, read as `Overrides.of` reads it."""
        return Overrides.of(self.members)


def _tail(text: _Text, each: Callable[[object], None] | None) -> Tail | None:
    members: dict = {}
    lists = records = 0
    malformed: tuple[int, object] | None = None
    listed = False
    for key in text.members():
        if key != "verdicts":
            members[key] = text.value()
            continue
        lists += 1
        records, malformed, listed = 0, None, text.peek() == "["
        if not listed:
            text.value()
            continue
        for index, record in enumerate(text.elements()):
            records += 1
            if malformed is None and not _well_formed(record):
                malformed = (index, record)
            if each is not None:
                each(record)
    text.finish()
    if (
        not listed
        or members.get("format") != EXPORT_FORMAT
        or not isinstance(members.get("manifest_generated_at"), str)
    ):
        return None
    return Tail(members, records, malformed, lists)


def read_tail(path: str | Path, each: Callable[[object], None] | None = None) -> Tail | None:
    """Return the verdicts document at `path` as `Tail`, reading it once a block at a time and holding one record of its `verdicts` list at a time, or None when the file is missing or unreadable or is not an ams-review-verdicts/1 document as `verdict_store.parse_autosave_payload` reads one: not JSON anywhere in it, not an object, another `format`, a `manifest_generated_at` that is not a string, or no `verdicts` list. `each`, when given, is called with every record of every `verdicts` list it reads, in file order, for a reader that counts or orders them in the same read, as the carry does."""
    try:
        handle = Path(path).open("rb")
    except OSError:
        return None
    with handle:
        try:
            return _tail(_Text(handle), each)
        except _NotJSON, OSError, ValueError:
            return None


def iter_records(source: str | Path | BinaryIO) -> Iterator[object]:
    """Yield the records of the verdicts document at `source`, a path or a binary file read from its start: its first `verdicts` list, in file order, one record held at a time. It reads the members before that list whole and stops at the list's end. A reader checks the document with `read_tail` first; text that is not a JSON object raises `ValueError`."""
    if isinstance(source, (str, Path)):
        with Path(source).open("rb") as handle:
            yield from _records(handle)
        return
    source.seek(0)
    yield from _records(source)


def _records(handle: BinaryIO) -> Iterator[object]:
    text = _Text(handle)
    try:
        for key in text.members():
            if key == "verdicts" and text.peek() == "[":
                yield from text.elements()
                return
            text.value()
    except _NotJSON:
        raise ValueError(f"{getattr(handle, 'name', 'the file')} is not a JSON object") from None


def _skip_whitespace(text: str, index: int) -> int:
    match = _WHITESPACE.match(text, index)
    return match.end() if match else index


def head_stamp(head: bytes) -> str | None:
    """Return the top-level manifest_generated_at of a verdicts document from its first bytes, without parsing its records, or None when the head does not settle it.

    The head settles a stamp only when it decodes as strict UTF-8 to a JSON object whose top-level members, decoded one at a time with the decoder json.loads uses, reach the `verdicts` key after exactly one manifest_generated_at with a string value. Any other head returns None and the caller parses the whole file: a head that ends first, is not an object, starts with a BOM or uses another encoding, has a non-string stamp, has the stamp twice before `verdicts`, or closes before `verdicts`.

    The result differs from json.loads only on a document with a second top-level manifest_generated_at after the `verdicts` array, where json.loads takes the later one. Repo writers serialize one dict or one JS object (json.dumps, VerdictStore.payload_bytes, the app's JSON.stringify), which produces no duplicate top-level members. Externally supplied files may contain duplicate members.
    """
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head)
        decoder = json.JSONDecoder()
        index = _skip_whitespace(text, 0)
        if index >= len(text) or text[index] != "{":
            return None
        index = _skip_whitespace(text, index + 1)
        stamp: str | None = None
        while index < len(text) and text[index] == '"':
            key, index = decoder.raw_decode(text, index)
            if not isinstance(key, str):
                return None
            index = _skip_whitespace(text, index)
            if index >= len(text) or text[index] != ":":
                return None
            index = _skip_whitespace(text, index + 1)
            if key == "verdicts":
                return stamp
            value, index = decoder.raw_decode(text, index)
            if key == "manifest_generated_at":
                if stamp is not None or not isinstance(value, str):
                    return None
                stamp = value
            index = _skip_whitespace(text, index)
            if index >= len(text) or text[index] != ",":
                return None
            index = _skip_whitespace(text, index + 1)
    except ValueError:
        return None
    return None


def read_head_stamp(path: str | Path) -> str | None:
    """Return `head_stamp` of the first HEAD_BYTES bytes a read of the regular file at `path` starts with, or None when `path` is not a regular file. The bytes are read at the opened descriptor's offset without moving it, so a later read of `path` sees every byte it sees without this one, also where opening the path shares an offset with another descriptor, as `/dev/stdin` and `/dev/fd/N` do on macOS; a pipe, whose bytes a read consumes, is not read."""
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("rb", buffering=0) as handle:
        return head_stamp(os.pread(handle.fileno(), HEAD_BYTES, handle.tell()))


def _describe(named: object) -> str:
    if (
        isinstance(named, dict)
        and isinstance(named.get("sha256"), str)
        and isinstance(named.get("closed_at"), str)
    ):
        return f"the accepted list {named['sha256']} closed at {named['closed_at']}"
    return f"an `accepted` field, {named!r}, that is not an object with a string `sha256` and `closed_at`"


def _root_of(list_path: str | Path) -> Path | None:
    list_path = Path(list_path)
    depth = len(RELATIVE_PATH.parts)
    if list_path.parts[-depth:] != RELATIVE_PATH.parts:
        return None
    return list_path.parents[depth - 1]


def close_out_intent_for(list_path: str | Path) -> Path | None:
    """Return where a close-out leaves its intent for the list at `list_path`, var/cycle/close-out.json in the directory that holds rebuild/accepted-units.txt, as `close_out.intent_path_for` places it beside the store; None for a list at any other path, which no close-out writes."""
    root = _root_of(list_path)
    return root / CLOSE_OUT_INTENT if root is not None else None


def store_for(list_path: str | Path) -> Path | None:
    """Return the store the list at `list_path` sits beside, verdicts-autosave.json in the directory that holds rebuild/accepted-units.txt (the inverse of `path_for`); None for a list at any other path."""
    root = _root_of(list_path)
    return root / AUTOSAVE_NAME if root is not None else None


def _remedy(stamp: ListStamp, otherwise: str) -> str:
    intent = close_out_intent_for(stamp.path)
    if intent is None or not intent.exists():
        return otherwise
    return (
        f"a close-out that did not finish left {intent}, having written the list and not yet the store, so stop "
        "the review server and run `make close-out ARGS=--apply`, which finishes it, rather than putting back a list"
    )


def pair(document: Mapping | None, listed: Accepted | ListStamp, *, store: str | Path) -> None:
    """Raise `Mismatch` unless the store document `document` pairs with the list `listed`: its `accepted` field names that list, or it names none and either holds no record and no tombstone or sits beside no list. A `document` of None, no store file, pairs with any list. The refusal names the remedy: `make close-out ARGS=--apply` while a close-out's intent is left beside the list (`close_out_intent_for`), since a close-out killed between its list's rename and its store's leaves exactly this mismatch, and otherwise putting back the list the store was folded against."""
    stamp = listed.stamp if isinstance(listed, Accepted) else listed
    if document is None:
        return
    named = document.get("accepted")
    if named is None:
        if stamp.sha256 is None or (
            not document.get("verdicts") and not _tombstones(document) and not Overrides.of(document)
        ):
            return
        raise Mismatch(
            f"{store} holds verdicts and names no accepted list, but beside it {stamp.path} is the list "
            f"{stamp.sha256} closed at {stamp.closed_at}, from a close-out the store never went through: "
            f"{_remedy(stamp, _UNNAMED_REMEDY)}"
        )
    if stamp.sha256 is None:
        raise Mismatch(
            f"{store} names {_describe(named)}, but there is no {stamp.path}: {_remedy(stamp, _NAMED_REMEDY)}"
        )
    if named != stamp.field():
        raise Mismatch(
            f"{store} names {_describe(named)}, but {stamp.path} is the list {stamp.sha256} closed at "
            f"{stamp.closed_at}, so the two were not folded together: {_remedy(stamp, _NAMED_REMEDY)}"
        )


def verdict_of(
    unit: str, records: Mapping[str, dict], overrides: Overrides, accepted: Accepted
) -> dict | None:
    """Return what every reader takes as `unit`'s verdict: for an id the list does not name, the store's own record for it (`records`, the latest by unit), or None, blank, without one; for a listed id, its override record in `overrides`, or None for an override tombstone, or else its accepted record."""
    if unit not in accepted.verdicts:
        return records.get(unit)
    record = overrides.records.get(unit)
    if record is not None or unit in overrides.cleared:
        return record
    return accepted.record(unit)


class View(Mapping[str, dict]):
    """The store's own records and tombstones, the accepted list and the store's override section read as one mapping from unit to `verdict_of`'s answer, a unit with a blank answer being absent. It holds references to the four, not copies, and makes an accepted record only when one is asked for."""

    __slots__ = ("records", "cleared", "accepted", "overrides")

    def __init__(
        self,
        records: Mapping[str, dict],
        cleared: Mapping[str, str],
        accepted: Accepted,
        overrides: Overrides | None = None,
    ) -> None:
        self.records = records
        self.cleared = cleared
        self.accepted = accepted
        self.overrides = overrides if overrides is not None else Overrides()

    def verdict_of(self, unit: str) -> dict | None:
        return verdict_of(unit, self.records, self.overrides, self.accepted)

    def __getitem__(self, unit: str) -> dict:
        record = self.verdict_of(unit)
        if record is None:
            raise KeyError(unit)
        return record

    def __contains__(self, unit: object) -> bool:
        if not isinstance(unit, str):
            return False
        if unit not in self.accepted.verdicts:
            return unit in self.records
        return unit in self.overrides.records or unit not in self.overrides.cleared

    def tombstone(self, unit: str) -> str | None:
        """Return the `at` of the unit's tombstone in the part of the store that holds its entries, the override section for a listed id and the store's own tombstones otherwise, or None when it holds none."""
        if unit in self.accepted.verdicts:
            return self.overrides.cleared.get(unit)
        return None if unit in self.records else self.cleared.get(unit)

    def is_accepted(self, unit: str) -> bool:
        """Return whether the unit's answer is its accepted record: the list names it and the store holds no override for it."""
        return unit in self.accepted.verdicts and unit not in self.overrides

    def from_list(self, within: Container[str] | None = None) -> Iterator[tuple[str, str]]:
        """Yield `(id, verdict)` for each listed id whose answer is its accepted record, in the list's order, keeping only the ids in `within` when it is given, such as a corpus's human unit ids."""
        overrides = self.overrides
        for unit, verdict in self.accepted.verdicts.items():
            if unit not in overrides and (within is None or unit in within):
                yield unit, verdict

    def app_field(self, within: Container[str]) -> dict:
        """Return the review app's `accepted` field for the full GET of /autosave: the list's sha256 and `closed_at` (both None with no list) and the ids `from_list(within)` yields, by verdict, in the list's order, with `within` the served corpus's human unit ids."""
        field: dict = {"sha256": self.accepted.sha256, "closed_at": self.accepted.closed_at}
        field.update((verdict, []) for verdict in MARKS)
        for unit, verdict in self.from_list(within):
            field[verdict].append(unit)
        return field

    def __iter__(self) -> Iterator[str]:
        listed = self.accepted.verdicts
        yield from (unit for unit in self.records if unit not in listed)
        yield from (unit for unit in self.overrides.records if unit in listed)
        for unit, _verdict in self.from_list():
            yield unit

    def __len__(self) -> int:
        return sum(1 for _ in self)


def view_of_file(document: Mapping | None, accepted: Accepted, *, path: str | Path) -> View:
    """Return the view of the verdicts document `document` read from `path` (None for no file there). The store, a file named verdicts-autosave.json, and any document that names an `accepted` field, such as a copy of the store under another name, are read through `view_of`, which pairs them with the list. Any other document is an Export from the review app, which holds the store's records alone, its overrides among them, with no `accepted` field and no tombstones: its records are read over the list, those on listed ids as the store's overrides (`split`), and its tombstones, when it has any, joined by the tombstones of the store beside the list (`store_for`), its override tombstones among them, on the units the Export holds no record for, so an accepted unit cleared since the close-out reads blank, as it does in the store. That store must pair with the list (`pair`), and a store that is not JSON raises `Mismatch` naming it."""
    if document is None or Path(path).name == AUTOSAVE_NAME or document.get("accepted") is not None:
        return view_of(document, accepted, store=path)
    records = _latest_by_unit(document.get("verdicts") or ())
    cleared = _tombstones(document)
    store = store_for(accepted.path)
    if accepted.sha256 is not None and store is not None and store.is_file():
        try:
            held = json.loads(store.read_bytes())
        except ValueError:
            raise Mismatch(
                f"{store}, whose tombstones an Export of it lacks, is not JSON, so {path} cannot be read beside "
                f"{accepted.path}: restore a readable store from the journal with "
                "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`"
            ) from None
        if not isinstance(held, dict):
            held = {}
        pair(held, accepted, store=store)
        _every, held_cleared = joined(
            _latest_by_unit(held.get("verdicts") or ()), _tombstones(held), Overrides.of(held)
        )
        for unit, at in held_cleared.items():
            cleared.setdefault(unit, at)
    own, tombstones, overrides = split(records, cleared, accepted.verdicts)
    return View(own, tombstones, accepted, overrides)


def view_of(document: Mapping | None, accepted: Accepted, *, store: str | Path) -> View:
    """Return the view of the store document `document` (None for no store file) and the list `accepted` after `pair` has checked that the two pair, raising `Mismatch` otherwise. The view's records are the latest record for each unit, a later `at` winning and the first of equal ones kept (`journal.latest_by_unit`), its tombstones the document's `cleared` list (`journal.parse_cleared`), and its overrides the document's `overrides` section (`Overrides.of`)."""
    pair(document, accepted, store=store)
    if document is None:
        return View({}, {}, accepted)
    return View(
        _latest_by_unit(document.get("verdicts") or ()),
        _tombstones(document),
        accepted,
        Overrides.of(document),
    )
