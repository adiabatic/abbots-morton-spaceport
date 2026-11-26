"""The review server's kerning routes (rebuild/KERN-PLAN.md section 6), which the app's kerning view (`#view=kern`, `static/kern-view.js`) reads and writes through:

- `GET /kern/inventory` answers with `rebuild/out/m1/kern-inventory.json` as the build wrote it (`rebuild/pipeline/kern_inventory.py`), or a 404 whose body says to run the M1 build.
- `GET /kern` answers with the values the view applies to the inventory's pixels, as one JSON document (`values_payload`, format `VALUES_FORMAT`, checked by `check_values_payload`):
  - `live`: every facing pair a `kern:` record decides, resolved now from the rune files on disk over the tables under `rebuild/out/m1` (`kern.resolve`), each with its value and its deciding records, or the error the stage raises over them, in which case the view shows the built values.
  - `built`: every facing pair whose value in the built font is not 0, from the `kern-classes.json` beside `M1.otf`, which read-back vouches for, so the view marks a value the built font does not have yet.
  - `runes`: each rune file's `kern:` block, the raw text from its `kern:` line to the end of the file, that text's sha256 (an edit's `base`), why the editor cannot write it back (`refusal`, `kern_writer.parse_block`) or null, and its records with their key paths, values, flow form (`kern.describe`), `why`, the line the file holds them on (null when the block is not one record per line), the two side names whose scope the record is (`kern_writer.record_sides`, null for a record with a class, a family list or an `except`), and whether a key narrows it below the two letters (`narrow`).
  - `carried` (`carried`): the facing pairs the import of the old font's kerning set, from `var/kern/imported.json`, each with its value and the record that sets it; the ones the author has marked reviewed, from `var/kern/reviewed.json`, as `lead|follow` keys; the imported or marked keys whose sides the current build no longer has (`stale`); and why the marks cannot be read, when they cannot. The view counts a pair carried over and unreviewed while it is listed, unmarked, and still has the imported value.
  - `apart` (`apart`): the facing pairs the author has marked as kerned apart from their relatives on purpose, from `var/kern/apart.json`, as `lead|follow` keys; the marked keys that name no facing pair of the current build (`stale`); and why the marks cannot be read, when they cannot. The view finds a facing pair's relatives itself, from the inventory's drawings (`static/kern.js`), and leaves a marked pair out of every comparison with them.
  - `journal` (`kern_writer.Journal.summary`): the journal file's `id`, which changes when the file is deleted or replaced and its `seq`s start over, its last `seq`, how many requests wait for a running pass to end (`pending`), and the newest requests dropped when one ended, with why.
  - The staleness the view reports: `tables` (the tables' stamp and whether it is the one the sources on disk give, `run_m1.tables_inputs`, recomputed at most every `TABLES_TTL_S` seconds), `inventory` (whether the file is there, its stamp, and whether that stamp names these tables and the Manual pages on disk), and `pass` (the pid holding the cycle's pass lock, while a pass runs; the server's own short holds are not one).
- `POST /kern` takes a delta, `{"sets": [...]}`, every set naming one record of one rune file by its scope (`parse_delta`): `{"scope": "letters", "lead": <leading rune>, "follow": <following rune>}`, the record that names only the two families, or `{"scope": "facing", "lead": <leading side>, "follow": <following side>}`, the record whose own-side and `right` keys are exactly the two sides' keys (`kern_writer.facing_scope`), with `by`, the value, from -`BY_LIMIT` to `BY_LIMIT` pixels as the schema bounds it, or null to drop the record, `base`, the sha256 of the block the client last saw, and an optional `why`, the author's typed note, stored verbatim (an empty one removes the record's note; none leaves it). The record lives in the leading rune's file, a ligature's own included. Every rune a set names, leading or following, must be one the loaded spec holds under exactly that name, and the leading rune's file must be named exactly after it (`_check_runes`, `_block`), whatever the set's value, since a case-insensitive file system would otherwise open another rune's file under a misspelled name.
- `POST /kern/reviewed` (`post_reviewed`) marks one imported facing pair reviewed, or every imported facing pair of two letters, or takes the mark off, in `var/kern/reviewed.json`, refusing a pair the import did not set or the current build does not have, and marks it cannot read. Every write marks the imported facing pairs whose value it moves and keeps those marks in its journal event (`marked`), and its undo takes exactly those off (`unmarked`).
- `POST /kern/apart` (`post_apart`) takes `{"pairs": [{"lead": <leading side>, "follow": <following side>}, ...], "apart": bool}` and marks each facing pair as kerned apart from its relatives on purpose, or takes the marks off, in `var/kern/apart.json`, refusing a pair the current build does not have, and marks it cannot read. A mark changes no rune file and is not journaled.
- `POST /kern/undo` takes `{"undoes": seq}`, the journal `seq` a write's reply gave for the change to undo (`post_undo`). A pending request's `seq` cancels it while it waits, journaled as `cancelled`, and reverts nothing written. Otherwise the change, or the `applied` event a pending request became, must be the newest applied change no undo has reverted (`kern_writer.JournalState.undo_target`), and its block must still be what it wrote; an older change, one never written, or one already undone is refused and changes nothing.

**An edit** (`_edit`). A set whose `base` is neither the block on disk nor the block this write began from (`starts`, so a client's change queued behind its own earlier one still applies) changes nothing and is a conflict (409) carrying the block as it stands. Otherwise each set gives or replaces its scope's record, or drops it; a record never touches one that carries a class, a family list or an `except`, beside which it stands as a narrower one. A record equal to what it would inherit is dropped: the spec with the record and without it are resolved in memory (`_values_with`), and when every facing pair takes the same value both ways the record goes, its `why` with it; a set that brings a note to a record that goes is refused, since a note needs a record. The new block (`kern_writer.render_block` in `kern_writer.canonical` order) replaces the old after the file's last byte before it (`kern_writer.splice`). A file is read and written as its bytes hold it, and a block's lines end the way the file's first line does (`kern_writer.line_ending`), so a CRLF file keeps every byte outside its block. Before anything is replaced, `_validate` writes the new file into a temporary copy of the runes directory and runs the spec loader, `kern.resolve` and the kern lookup's plan (`emit_gpos.kern_plan`) over the copy, and the defect gate's contact check at the kerned offset (`defects.check_kerned_breaks`, under `rebuild/m1-contact-allow.yaml`) over every member break whose value the edit moves. A refusal (422) carries the build's own messages, with the copy's paths read as the runes directory's, and changes nothing. The file is then replaced atomically, through a staging file beside the journal renamed over it (`_replace`), and the change is appended to the journal (`kern_writer.Journal`, `var/kern/journal.ndjson`) before the reply; an append that fails puts the file back. The reply (200) names the change and carries the values the edit resolved to, which the routes keep, so the next `GET /kern` answers from them.

**A running pass.** Every write holds the cycle's pass lock (`var/cycle/pass.lock`) without waiting, as the table-diff accept takes it, writing the server's pid into it. While a pass holds it, a delta or an undo is appended to the journal as pending and answered with 202. Pending requests are applied in order once the lock frees: a timer tries every `DRAIN_INTERVAL_MS` (`drain`), and every write drains them first. Each is checked again against the file as it then stands, and one that no longer applies, or whose application raises anything, is journaled as dropped with its reason, which `GET /kern` reports; only the journal's own lock being busy leaves a request for the next try, `JOURNAL_BUSY_TRIES` times at most. A write that finds requests still pending queues behind them. A failure the routes do not foresee is answered as the JSON error reply (500, or 503 while the journal's lock is busy).

**Undo** (`_undo`) puts back the block the change found, when the block on disk is still the one it wrote, and otherwise is a conflict; it is checked as an edit is, and journaled as an `undo` event. An undo always names its change, so a queued undo whose delta was dropped, or a press that changed nothing, never reverts an older change in its place.

Anything that goes wrong while `GET /kern` is computed, tables whose configurations carry more than one stamp among it, comes back as the same document with `live.error` naming it (`failure_payload`), never as a server error, and a spec or tables that do not load are not read again until one of their files moves. Neither read route writes, and no route settles a text, so none runs the kernel or `cargo`. The tables are read with `table.read_windows_head(cells_only=True)` and `table.read_join_tsv`, and kept, with the spec, the minted glyphs and the last answer, until a file they came from moves (`file_signature`). The reads run in the server's executor, and concurrent requests share one computation; the writes run one at a time on a thread of their own.

It is server-only: the corpus build never imports it or `kern_writer` (`fingerprint.REVIEW_NON_BUILD_MODULES`), `serve.main` imports it when the server starts, so no tool that imports `serve.py` reaches it, and the server's code digest covers both (`landing.SERVED_MODULES`). The routes take the runes directory (`runes_dir`), so a test server writes into a copy.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from rebuild.pipeline import conform, defects, emit_gpos, kern, kern_inventory, output_sink, spec_load
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import CellId, GlyphRecord, Provenance, ResolvedSpec
from rebuild.review import kern_writer, store_lock

VALUES_FORMAT = "ams-kern-values/1"
REVIEWED_FORMAT = "ams-kern-reviewed/1"
IMPORTED_NAME = "imported.json"  # the facing pairs the import of the old font's kerning set
REVIEWED_NAME = "reviewed.json"
APART_FORMAT = "ams-kern-apart/1"
APART_NAME = "apart.json"
APART_LIMIT = 4096  # the most facing pairs one `POST /kern/apart` names
TABLES_TTL_S = 10.0
DRAIN_INTERVAL_MS = 1000
JOURNAL_BUSY_TRIES = (
    5  # how many drains a pending request waits out the journal's own lock before it is dropped
)
RUNE_NAME = re.compile(r"^[A-Za-z0-9_]+$")
BY_LIMIT = 64  # the largest |by| a kern record takes, `rebuild/schema/rune.schema.json`'s bound on `by`; the font's value record overflows int16 at 655 pixels
SHA256 = re.compile(r"^[0-9a-f]{64}$")

Signature = tuple[int, int, int] | None


def file_signature(path: Path) -> Signature:
    """Return the file's inode, modification time and size, or None when it is missing."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size)


def kern_block_text(text: str) -> str:
    """Return a rune file's `kern:` block as written: the text from the line that opens with `kern:` to the end of the file, `kern` being the file's last key (rebuild/KERN-PLAN.md section 2), or the empty string when the file has none."""
    return kern_writer.split_file(text)[1]


def file_label(path: Path) -> str:
    """Return the name the spec loader gives a rune file in a record's provenance: its path under the repository, or the whole path for a file outside it."""
    try:
        return str(Path(path).relative_to(spec_load.REPO_ROOT))
    except ValueError:
        return str(path)


@functools.lru_cache(maxsize=256)
def block_refusal(block: str, file: str, newline: str = "\n") -> str | None:
    """Return why the editor cannot write `block` back exactly (`kern_writer.parse_block`), or None when it can. Only that verdict is kept: any other exception propagates, and `lru_cache` keeps no exception."""
    try:
        kern_writer.parse_block(block, file, newline)
    except kern_writer.BlockRefused as refusal:
        return str(refusal)
    return None


def _record_lines(block: str, count: int) -> list[str | None]:
    lines = block.splitlines()
    if len(lines) == count + 1 and all(line.startswith("- {") for line in lines[1:]):
        return list(lines[1:])
    return [None] * count


def _written_records(block: str, count: int) -> list[dict | None]:
    """Return the block's records as written, read with the spec loader's YAML, or Nones when they do not read as `count` records."""
    try:
        data = yaml.load(block, Loader=kern_writer.SAFE_LOADER) if block else None
    except yaml.YAMLError:
        data = None
    records = data.get("kern") if isinstance(data, dict) else None
    if not isinstance(records, list) or len(records) != count:
        return [None] * count
    return [record if isinstance(record, dict) else None for record in records]


def rune_blocks(spec: ResolvedSpec | None, runes_dir: Path, repo_root: Path) -> dict[str, dict]:
    """Return each rune file's `kern:` block by the rune's name, as the module docstring lays it out. The records come from the loaded spec, so a spec that did not load gives none."""
    blocks: dict[str, dict] = {}
    for path in sorted(Path(runes_dir).glob("*.yaml")):
        try:
            whole = kern_writer.read_text(path)
            text = kern_block_text(whole)
        except OSError:
            continue
        rune = spec.runes.get(path.stem) if spec is not None else None
        try:
            file = str(path.relative_to(repo_root))
        except ValueError:
            file = str(path)
        records = rune.kern.records if rune is not None else ()
        lines = _record_lines(text, len(records))
        written = _written_records(text, len(records))
        blocks[path.stem] = {
            "file": file,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "text": text,
            "refusal": block_refusal(text, file, kern_writer.line_ending(whole)) if text else None,
            "records": [
                {
                    "path": record.provenance.path if record.provenance is not None else "",
                    "by": record.by,
                    "text": kern.describe(record),
                    "why": record.why,
                    "line": line,
                    "sides": None if data is None else kern_writer.record_sides(path.stem, data),
                    "narrow": kern_writer.record_narrow(record),
                }
                for record, line, data in zip(records, lines, written)
            ],
        }
    return blocks


def live_values(resolution: kern.KernResolution) -> list[dict]:
    """Return every facing pair a record decides, with its value and its deciders, in `kern.tsv` order. A pair no record decides has the value 0 and is left out."""
    return [
        {
            "lead": pair.leading.name,
            "follow": pair.following.name,
            "by": pair.by,
            "deciders": [
                {
                    "file": decider.record.provenance.file if decider.record.provenance is not None else "",
                    "path": decider.record.provenance.path if decider.record.provenance is not None else "",
                    "inherited": decider.inherited,
                }
                for decider in pair.deciders
            ],
        }
        for pair in resolution.pairs
        if pair.deciders or pair.by
    ]


def built_values(m1_out: Path) -> dict:
    """Return the built font's values from the `kern-classes.json` beside it: `present`, and each facing pair whose value is not 0 as `[leading side, following side, by]`."""
    try:
        document = json.loads((Path(m1_out) / kern.KERN_CLASSES_JSON).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {"present": False, "values": []}
    if not isinstance(document, dict) or document.get("format") != kern.KERN_CLASSES_FORMAT:
        return {"present": False, "values": []}
    return {
        "present": True,
        "values": [
            list(row)
            for row in document.get("values", [])
            if isinstance(row, list) and len(row) == 3 and row[2]
        ],
    }


def failure_payload(message: str) -> dict:
    """Return `GET /kern`'s answer when it cannot be computed: no inventory, tables or values known, and `live.error` carrying `message`."""
    return {
        "format": VALUES_FORMAT,
        "inventory": {"present": False, "stamp": None, "tables_current": None, "manual_current": None},
        "tables": {"inputs": None, "current": None},
        "pass": None,
        "live": {"values": [], "error": {"message": message, "issues": []}},
        "built": {"present": False, "values": []},
        "runes": {},
        "journal": {"id": None, "seq": 0, "pending": 0, "dropped": []},
        "carried": {"pairs": [], "reviewed": [], "stale": [], "error": None},
        "apart": {"pairs": [], "stale": [], "error": None},
    }


def check_values_payload(payload: object) -> list[str]:
    """Return every way `payload` breaks `GET /kern`'s shape, the module docstring's; an empty list means it holds. The app's node tests read a fixture this checks (`rebuild/review/fixtures/kern-values.json`)."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["the payload is not a JSON object"]
    if payload.get("format") != VALUES_FORMAT:
        errors.append(f"format is {payload.get('format')!r}, not {VALUES_FORMAT!r}")
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("present"), bool):
        errors.append("inventory must say whether the file is there")
    elif inventory["present"] and not isinstance(inventory.get("stamp"), dict):
        errors.append("a present inventory must carry its stamp")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or tables.get("current") not in (True, False, None):
        errors.append("tables must say whether they are current, or null when unknown")
    pass_holder = payload.get("pass")
    if pass_holder is not None and not isinstance(pass_holder, dict):
        errors.append("pass must be null or name the pass's pid")
    live = payload.get("live")
    if not isinstance(live, dict) or not isinstance(live.get("values"), list):
        errors.append("live must hold its values")
    else:
        for number, value in enumerate(live["values"]):
            if not (
                isinstance(value, dict)
                and isinstance(value.get("lead"), str)
                and isinstance(value.get("follow"), str)
                and isinstance(value.get("by"), int)
                and isinstance(value.get("deciders"), list)
            ):
                errors.append(f"live.values[{number}] must name two sides, a value and its deciders")
        error = live.get("error")
        if error is not None and (not isinstance(error, dict) or not isinstance(error.get("message"), str)):
            errors.append("live.error must be null or carry a message")
    built = payload.get("built")
    if (
        not isinstance(built, dict)
        or not isinstance(built.get("present"), bool)
        or not isinstance(built.get("values"), list)
    ):
        errors.append("built must say whether kern-classes.json is there and hold its values")
    runes = payload.get("runes")
    if not isinstance(runes, dict):
        errors.append("runes must map each rune to its kern block")
    else:
        for name, block in runes.items():
            if not (
                isinstance(block, dict)
                and isinstance(block.get("file"), str)
                and isinstance(block.get("text"), str)
                and block.get("sha256") == hashlib.sha256(block["text"].encode()).hexdigest()
                and (block.get("refusal") is None or isinstance(block.get("refusal"), str))
                and isinstance(block.get("records"), list)
            ):
                errors.append(
                    f"runes.{name} must hold its file, its block's text and that text's sha256, its refusal or null, and its records"
                )
                continue
            for number, record in enumerate(block["records"]):
                sides = record.get("sides") if isinstance(record, dict) else None
                if not (
                    isinstance(record, dict)
                    and isinstance(record.get("path"), str)
                    and isinstance(record.get("by"), int)
                    and isinstance(record.get("text"), str)
                    and (record.get("why") is None or isinstance(record.get("why"), str))
                    and (record.get("line") is None or isinstance(record.get("line"), str))
                    and (sides is None or (isinstance(sides, list) and len(sides) == 2))
                    and isinstance(record.get("narrow"), bool)
                ):
                    errors.append(
                        f"runes.{name}.records[{number}] must hold its path, value, text, why, line, sides and narrow"
                    )
    journal = payload.get("journal")
    if (
        not isinstance(journal, dict)
        or not (journal.get("id") is None or isinstance(journal.get("id"), str))
        or not isinstance(journal.get("seq"), int)
        or not isinstance(journal.get("pending"), int)
        or not isinstance(journal.get("dropped"), list)
    ):
        errors.append("journal must give its id, its last seq, its pending count and its dropped requests")
    carried = payload.get("carried")
    if (
        not isinstance(carried, dict)
        or not isinstance(carried.get("pairs"), list)
        or not isinstance(carried.get("reviewed"), list)
        or not all(
            isinstance(entry, dict)
            and isinstance(entry.get("lead"), str)
            and isinstance(entry.get("follow"), str)
            and isinstance(entry.get("by"), int)
            for entry in carried["pairs"]
        )
        or not all(isinstance(key, str) for key in carried["reviewed"])
        or not isinstance(carried.get("stale"), list)
        or not (carried.get("error") is None or isinstance(carried.get("error"), str))
    ):
        errors.append(
            "carried must list the imported facing pairs with their values, the reviewed ones, the stale ones, and why the marks cannot be read or null"
        )
    apart = payload.get("apart")
    if (
        not isinstance(apart, dict)
        or not isinstance(apart.get("pairs"), list)
        or not all(isinstance(key, str) for key in apart["pairs"])
        or not isinstance(apart.get("stale"), list)
        or not (apart.get("error") is None or isinstance(apart.get("error"), str))
    ):
        errors.append(
            "apart must list the facing pairs marked apart, the stale ones, and why the marks cannot be read or null"
        )
    return errors


class MarksUnreadable(Exception):
    """A marks file under `var/kern` (`reviewed.json` or `apart.json`) is there but cannot be read; nothing replaces it until it is fixed or removed."""


class DeltaInvalid(ValueError):
    """A `POST /kern` body that is not a delta; answered with 400."""


class Refused(Exception):
    """A write that changes nothing: `status` is `refused` (422, the build would refuse the result or the writer cannot write it), `conflict` (409, the block is not the one the request was made against), or `nothing` (200, nothing to undo). `issues` holds the build's messages as `[code, text]` pairs, and `block` the block as it stands for a conflict."""

    def __init__(self, status: str, message: str, issues=(), block: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.issues = [list(issue) for issue in issues]
        self.block = block

    @property
    def code(self) -> int:
        return {"refused": 422, "conflict": 409, "nothing": 200}.get(self.status, 422)


@dataclass(frozen=True)
class EditSet:
    """One set of a delta: the record it names (`scope`), the value or None to drop it, the block's sha256 it was made against, and the note to store, None to leave the record's as it is."""

    scope: kern_writer.Scope
    by: int | None
    base: str
    why: str | None


def parse_delta(body: object) -> list[EditSet]:
    """Return a `POST /kern` body's sets, or raise `DeltaInvalid` naming what is wrong with it. Every set of one delta edits one rune file."""
    if not isinstance(body, dict) or not isinstance(body.get("sets"), list) or not body["sets"]:
        raise DeltaInvalid('a kern delta is {"sets": [...]} with at least one set')
    sets: list[EditSet] = []
    for number, raw in enumerate(body["sets"]):
        where = f"sets[{number}]"
        if not isinstance(raw, dict):
            raise DeltaInvalid(f"{where} is not an object")
        lead, follow = raw.get("lead"), raw.get("follow")
        if not isinstance(lead, str) or not isinstance(follow, str):
            raise DeltaInvalid(f"{where} must name its lead and its follow")
        if raw.get("scope") == kern_writer.LETTERS:
            if not (RUNE_NAME.match(lead) and RUNE_NAME.match(follow)):
                raise DeltaInvalid(f"{where}: a letters scope names two runes, not {lead!r} and {follow!r}")
            scope = kern_writer.letters_scope(lead, follow)
        elif raw.get("scope") == kern_writer.FACING:
            try:
                scope = kern_writer.facing_scope(lead, follow)
            except ValueError as error:
                raise DeltaInvalid(f"{where}: {error}") from None
        else:
            raise DeltaInvalid(f"{where}: scope is letters or facing, not {raw.get('scope')!r}")
        by = raw.get("by", ...)
        if not (by is None or (isinstance(by, int) and not isinstance(by, bool) and abs(by) <= BY_LIMIT)):
            raise DeltaInvalid(
                f"{where}: by is a whole number of pixels from -{BY_LIMIT} to {BY_LIMIT}, or null to drop the record"
            )
        base = raw.get("base")
        if not isinstance(base, str) or not SHA256.match(base):
            raise DeltaInvalid(f"{where}: base is the sha256 of the kern block the change was made against")
        why = raw.get("why")
        if why is not None and (not isinstance(why, str) or "\n" in why or "\r" in why):
            raise DeltaInvalid(f"{where}: why is one line of text")
        if by is None and why:
            raise DeltaInvalid(f"{where}: a record that is dropped keeps no note")
        sets.append(EditSet(scope, by, base, why))
    if len({one.scope.rune for one in sets}) > 1:
        raise DeltaInvalid("every set of one delta edits the same rune file")
    return sets


@dataclass(frozen=True)
class _Context:
    """What an edit is checked against: the spec on disk, the tables, the minted glyphs and ss10 copies, and the resolution of the blocks on disk (None when they do not resolve)."""

    spec: ResolvedSpec
    tables: dict
    glyphs: dict[CellId, GlyphRecord]
    ss10: dict[str, str]
    before: kern.KernResolution | None


def _pair_values(resolution: kern.KernResolution) -> dict[tuple[str, str], int]:
    return {(pair.leading.name, pair.following.name): pair.by for pair in resolution.pairs}


def _member_values(resolution: kern.KernResolution | None) -> dict[tuple[CellId, CellId], int]:
    if resolution is None:
        return {}
    return {member: pair.by for pair in resolution.pairs for member in pair.members}


class KernRoutes:
    """The state behind the routes: where to read and write, the cached tables, spec, glyphs, resolution and answer, the time the tables' currency was last computed, the journal, and the thread the writes run on. `tables_inputs` gives the stamp the sources on disk would give the tables (`run_m1.tables_inputs` by default)."""

    def __init__(
        self,
        repo_root: Path,
        m1_out: Path,
        tables_inputs: Callable[[], str] | None = None,
        *,
        runes_dir: Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.m1_out = Path(m1_out)
        self.runes_dir = Path(runes_dir) if runes_dir is not None else self.repo_root / "glyph_data" / "runes"
        self.registry = self.repo_root / "glyph_data" / "script.yaml"
        self.schema_dir = self.repo_root / "rebuild" / "schema"
        self.pass_lock = self.repo_root / "var" / "cycle" / "pass.lock"
        self.allow_path = self.repo_root / "rebuild" / "m1-contact-allow.yaml"
        self.journal = kern_writer.Journal(self.repo_root / "var" / "kern")
        self.imported_path = self.repo_root / "var" / "kern" / IMPORTED_NAME
        self.reviewed_path = self.repo_root / "var" / "kern" / REVIEWED_NAME
        self.apart_path = self.repo_root / "var" / "kern" / APART_NAME
        self._tables_inputs = tables_inputs
        self._lock = threading.Lock()
        self._tables_key: tuple | None = None
        self._tables: dict[str, tuple] | None = None
        self._tables_stamp: str | None = None
        self._tables_error: str | None = None
        self._spec_key: tuple | None = None
        self._spec: ResolvedSpec | None = None
        self._spec_error: str | None = None
        self._live_key: tuple | None = None
        self._live: dict | None = None
        self._resolved: tuple[dict, dict, kern.KernResolution | None] | None = None
        self._current: tuple[float, str | None, bool | None] | None = None
        self._inventory: tuple[Signature, bytes | None, dict | None] | None = None
        self._future = None
        self._writes = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kern-write")
        self._draining = None
        self._busy: dict[int, int] = {}

    def _source_inputs(self) -> str:
        if self._tables_inputs is not None:
            return self._tables_inputs()
        from rebuild.pipeline import run_m1

        return run_m1.tables_inputs()

    def _table_paths(self) -> list[Path]:
        paths: list[Path] = []
        for config in conform.SETTLEMENT_CONFIGS:
            # The windows head records its head file's size and sha256, so a new head file comes with a new head.
            paths += [
                table_module.windows_head_path(self.m1_out, config),
                self.m1_out / f"joins-{config}.tsv",
            ]
        return paths

    def tables(self) -> tuple[dict[str, tuple] | None, str | None, str | None]:
        """Return every settlement configuration's tables as the kern stage reads them, their stamp, and the reason they cannot be read, reading them again only when one of their files moved."""
        key = tuple(file_signature(path) for path in self._table_paths())
        if key != self._tables_key:
            self._tables = None
            self._tables_stamp = None
            self._tables_error = None
            try:
                tables: dict[str, tuple] = {}
                stamps: dict[str, str] = {}
                for config in conform.SETTLEMENT_CONFIGS:
                    stamps[config], decision = table_module.read_windows_head(
                        self.m1_out, config, cells_only=True
                    )
                    tables[config] = (
                        decision,
                        table_module.read_join_tsv(self.m1_out / f"joins-{config}.tsv"),
                    )
                if len(set(stamps.values())) > 1:
                    self._tables_error = f"the tables under {self.m1_out} carry more than one stamp ({', '.join(f'{config}: {stamp}' for config, stamp in stamps.items())}), so they are no one build's; run the M1 build"
                else:
                    self._tables = tables
                    self._tables_stamp = next(iter(stamps.values()), None)
            except Exception as error:
                self._tables_error = f"the tables under {self.m1_out} cannot be read ({type(error).__name__}: {error}); run the M1 build"
            self._tables_key = key
        return self._tables, self._tables_stamp, self._tables_error

    def _spec_paths(self) -> list[Path]:
        return [
            *sorted(self.runes_dir.glob("*.yaml")),
            self.registry,
            *sorted(self.schema_dir.glob("*.json")),
        ]

    def _spec_signature(self) -> tuple:
        return tuple((str(path), file_signature(path)) for path in self._spec_paths())

    def _load(self, runes_dir: Path) -> ResolvedSpec:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", spec_load.SpecWarning)
            spec = spec_load.load_spec(runes_dir, self.registry, self.schema_dir)
            runes = spec_load.m1_runes()
            return spec if runes is None else spec_load.narrowed(spec, runes)

    def spec(self) -> tuple[ResolvedSpec | None, tuple, str | None]:
        """Return the spec the rune files on disk load to, the signature of the files it was loaded from, and the loader's refusal, loading again only when one of those files moved."""
        key = self._spec_signature()
        if key != self._spec_key:
            self._spec = None
            self._spec_error = None
            try:
                self._spec = self._load(self.runes_dir)
            except Exception as error:
                self._spec_error = f"the rune files do not load ({type(error).__name__}: {error})"
            self._spec_key = key
        return self._spec, key, self._spec_error

    def live(self) -> dict:
        """Return the `live` part of the answer: the values the rune files on disk resolve to over the tables on disk, or the reason they do not."""
        tables, _stamp, tables_error = self.tables()
        spec, spec_key, spec_error = self.spec()
        key = (self._tables_key, spec_key)
        if key == self._live_key and self._live is not None:
            return self._live
        self._resolved = None
        if tables is None or spec is None:
            live = {"values": [], "error": {"message": tables_error or spec_error or "", "issues": []}}
        else:
            from rebuild.pipeline import run_m1

            try:
                glyphs = run_m1.mint_cell_glyphs(spec, tables)
                _bare, _copies, ss10_copies = run_m1.mint_raw_glyphs(spec)
                self._resolved = (glyphs, ss10_copies, None)
                resolution = kern.resolve(spec, tables, glyphs, ss10_copies)
                self._resolved = (glyphs, ss10_copies, resolution)
                live = {"values": live_values(resolution), "error": None}
            except kern.KernError as error:
                live = {
                    "values": [],
                    "error": {
                        "message": "the kern blocks on disk do not resolve over the tables on disk",
                        "issues": [list(issue) for issue in error.issues],
                    },
                }
            except Exception as error:
                live = {
                    "values": [],
                    "error": {
                        "message": f"the rune files on disk do not resolve over the tables on disk, which the next M1 build replaces ({type(error).__name__}: {error})",
                        "issues": [],
                    },
                }
        self._live_key = key
        self._live = live
        return live

    def tables_current(self, stamp: str | None, recheck: bool = True) -> bool | None:
        """Return whether `stamp`, the tables' stamp, is the one the sources on disk give, recomputing that at most every `TABLES_TTL_S` seconds, or only when it was never computed for `stamp` when `recheck` is false, or None when there are no tables."""
        if stamp is None:
            return None
        now = time.monotonic()
        stale = recheck and self._current is not None and now - self._current[0] >= TABLES_TTL_S
        if self._current is None or self._current[1] != stamp or stale:
            try:
                current: bool | None = self._source_inputs() == stamp
            except Exception:
                current = None
            self._current = (now, stamp, current)
        return self._current[2]

    def inventory_bytes(self) -> bytes | None:
        """Return the inventory's bytes as the build wrote them, or None when there is none, reading the file again only when it moved."""
        return self._read_inventory()[1]

    def _read_inventory(self) -> tuple[Signature, bytes | None, dict | None]:
        path = self.m1_out / kern_inventory.KERN_INVENTORY_JSON
        signature = file_signature(path)
        if self._inventory is None or self._inventory[0] != signature:
            try:
                body = path.read_bytes()
            except OSError:
                body = None
            stamp = None
            if body is not None:
                try:
                    document = json.loads(body)
                    stamp = document.get("stamp") if isinstance(document, dict) else None
                except ValueError:
                    stamp = None
            self._inventory = (signature, body, stamp if isinstance(stamp, dict) else None)
        return self._inventory

    def pass_holder(self) -> str | None:
        """Return the pid of the pass holding the pass lock, or None while it is free or held by this server's own write."""
        holder = store_lock.holder(self.pass_lock)
        return None if holder == str(os.getpid()) else holder

    def values_payload(self, recheck: bool = True) -> dict:
        """Return `GET /kern`'s answer, as the module docstring lays it out. An exception while it is computed comes back as the same document with `live.error` naming it (`failure_payload`), never as a server error, so the view shows what went wrong and keeps polling. A write's reply passes `recheck` false, so it does not wait for the tables' currency, which `GET /kern` keeps current (`tables_current`)."""
        with self._lock:
            try:
                return self._values_payload(recheck)
            except Exception as error:
                return failure_payload(
                    f"the server could not compute the kern values ({type(error).__name__}: {error})"
                )

    def _values_payload(self, recheck: bool = True) -> dict:
        live = self.live()
        _tables, stamp, _error = self.tables()
        _signature, body, inventory_stamp = self._read_inventory()
        pages = kern_inventory.read_manual_pages(self.repo_root)
        holder = self.pass_holder()
        return {
            "format": VALUES_FORMAT,
            "inventory": {
                "present": body is not None,
                "stamp": inventory_stamp,
                "tables_current": (
                    None
                    if inventory_stamp is None or stamp is None
                    else inventory_stamp.get("tables") == stamp
                ),
                "manual_current": (
                    None
                    if inventory_stamp is None
                    else inventory_stamp.get("manual") == kern_inventory.manual_digest(pages)
                ),
            },
            "tables": {"inputs": stamp, "current": self.tables_current(stamp, recheck)},
            "pass": None if holder is None else {"pid": holder},
            "live": live,
            "built": built_values(self.m1_out),
            "runes": rune_blocks(self._spec, self.runes_dir, self.repo_root),
            "journal": self.journal.summary(),
            "carried": self.carried(),
            "apart": self.apart(),
        }

    def values_bytes(self) -> bytes:
        return json.dumps(self.values_payload(), ensure_ascii=False).encode()

    def values_future(self):
        """Return the computation of `GET /kern`'s answer in the server's executor, shared by every request made while it runs."""
        from tornado.ioloop import IOLoop

        if self._future is None:
            future = IOLoop.current().run_in_executor(None, self.values_bytes)

            def done(_finished) -> None:
                self._future = None

            future.add_done_callback(done)
            self._future = future
        return self._future

    # The carried-over marks.

    def _imported(self) -> list[dict]:
        """Return the facing pairs the import of the old font's kerning set, as `imported.json` lists them, or none."""
        try:
            document = json.loads(self.imported_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return []
        pairs = document.get("pairs") if isinstance(document, dict) else None
        return [
            {key: entry.get(key) for key in ("lead", "follow", "by", "file", "line", "scope")}
            for entry in pairs or []
            if isinstance(entry, dict)
            and isinstance(entry.get("lead"), str)
            and isinstance(entry.get("follow"), str)
            and isinstance(entry.get("by"), int)
        ]

    def _imported_keys(self) -> set[str]:
        return {f"{entry['lead']}|{entry['follow']}" for entry in self._imported()}

    def _marks(self, path: Path, document_format: str, field: str, what: str) -> set[str]:
        """Return the `lead|follow` keys a marks file lists under `field`, none when the file is missing. A file that is there but cannot be read raises `MarksUnreadable`, so no write replaces it."""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return set()
        except OSError as error:
            raise MarksUnreadable(f"{path} cannot be read ({error}); fix it or remove it") from None
        try:
            document = json.loads(text)
        except ValueError as error:
            raise MarksUnreadable(f"{path} is not JSON ({error}); fix it or remove it") from None
        keys = document.get(field) if isinstance(document, dict) else None
        if (
            not isinstance(document, dict)
            or document.get("format") != document_format
            or not isinstance(keys, list)
            or not all(isinstance(key, str) for key in keys)
        ):
            raise MarksUnreadable(f"{path} is not a list of {what}; fix it or remove it")
        return set(keys)

    def _reviewed(self) -> set[str]:
        """Return the reviewed marks (`_marks` over `reviewed.json`)."""
        return self._marks(self.reviewed_path, REVIEWED_FORMAT, "reviewed", "reviewed marks")

    def _apart(self) -> set[str]:
        """Return the apart marks (`_marks` over `apart.json`)."""
        return self._marks(self.apart_path, APART_FORMAT, "apart", "apart marks")

    def _side_names(self) -> tuple[set[str], set[str]] | None:
        """Return the leading and the following side names of the current build, or None while the rune files do not resolve."""
        with self._lock:
            return self._side_names_held()

    def _side_names_held(self) -> tuple[set[str], set[str]] | None:
        """`_side_names` for a caller that holds the routes' lock, as `GET /kern`'s answer does."""
        self.live()
        resolution = self._resolved[2] if self._resolved is not None else None
        if resolution is None:
            return None
        return {pair.leading.name for pair in resolution.pairs}, {
            pair.following.name for pair in resolution.pairs
        }

    def _pair_keys_held(self) -> set[str] | None:
        """Return the `lead|follow` key of every facing pair of the current build, for a caller that holds the routes' lock, or None while the rune files do not resolve."""
        self.live()
        resolution = self._resolved[2] if self._resolved is not None else None
        if resolution is None:
            return None
        return {f"{pair.leading.name}|{pair.following.name}" for pair in resolution.pairs}

    def apart(self) -> dict:
        """Return `GET /kern`'s `apart` part, for a caller that holds the routes' lock: the facing pairs marked as kerned apart from their relatives on purpose, as `lead|follow` keys; the marked keys that name no facing pair of the current build (`stale`), since a later build can split or rename a side; and why the marks cannot be read, if they cannot."""
        error = None
        try:
            marked = self._apart()
        except MarksUnreadable as unreadable:
            marked, error = set(), str(unreadable)
        keys = self._pair_keys_held()
        stale = [] if keys is None else sorted(marked - keys)
        return {"pairs": sorted(marked), "stale": stale, "error": error}

    def post_apart(self, body: object) -> tuple[int, dict]:
        """Answer `POST /kern/apart`: mark each facing pair of `{"pairs": [{"lead": <side>, "follow": <side>}, ...], "apart": bool}` as kerned apart from its relatives on purpose, or take the marks off. Every pair must be a facing pair of the current build; one that is not refuses the whole request, and so does a change to marks that cannot be read."""
        pairs = body.get("pairs") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("apart"), bool)
            or not isinstance(pairs, list)
            or not 0 < len(pairs) <= APART_LIMIT
            or not all(
                isinstance(pair, dict)
                and isinstance(pair.get("lead"), str)
                and isinstance(pair.get("follow"), str)
                for pair in pairs
            )
        ):
            return self._reply(
                400,
                {
                    "status": "invalid",
                    "message": f'an apart mark is {{"pairs": [{{"lead", "follow"}}, ...], "apart": true or false}}, naming 1 to {APART_LIMIT} facing pairs',
                },
            )
        with self._lock:
            known = self._pair_keys_held()
        if known is None:
            return self._reply(
                422,
                {
                    "status": "refused",
                    "message": "the rune files do not resolve, so the build's facing pairs are not known",
                },
            )
        keys = {f"{pair['lead']}|{pair['follow']}" for pair in pairs}
        unknown = sorted(keys - known)
        if unknown:
            return self._reply(
                422,
                {
                    "status": "refused",
                    "message": f"{unknown[0].replace('|', ' | ')} is no facing pair of the current build (side names are case-sensitive)",
                },
            )
        try:
            marks = self._apart()
            changed = marks | keys if body["apart"] else marks - keys
            if changed != marks:
                output_sink.replace_output(
                    self.apart_path,
                    json.dumps(
                        {"format": APART_FORMAT, "apart": sorted(changed)}, indent=1, ensure_ascii=False
                    )
                    + "\n",
                )
        except MarksUnreadable as unreadable:
            return self._reply(
                409, {"status": "refused", "message": f"the marks were not changed: {unreadable}"}
            )
        except OSError as error:
            return self._reply(500, {"status": "error", "message": f"the marks were not written ({error})"})
        return self._reply(200, {"status": "apart" if body["apart"] else "together", "pairs": sorted(keys)})

    def carried(self) -> dict:
        """Return `GET /kern`'s `carried` part, for a caller that holds the routes' lock: the facing pairs the importer set, each with its value and its record; the ones the author has marked reviewed, as `lead|follow` keys; the imported or marked keys that name a side the current build no longer has (`stale`), since a later build can split or rename a side; and why the marks cannot be read, if they cannot. A pair counts as carried over and unreviewed while it is listed, unmarked, and still has the imported value, which the view decides."""
        pairs = self._imported()
        error = None
        try:
            reviewed = self._reviewed()
        except MarksUnreadable as unreadable:
            reviewed, error = set(), str(unreadable)
        sides = self._side_names_held()
        stale: list[str] = []
        if sides is not None:
            leading, following = sides
            keys = {f"{entry['lead']}|{entry['follow']}" for entry in pairs} | reviewed
            stale = sorted(
                key for key in keys if key.split("|")[0] not in leading or key.split("|")[-1] not in following
            )
        return {"pairs": pairs, "reviewed": sorted(reviewed), "stale": stale, "error": error}

    def _mark(self, keys: set[str], reviewed: bool) -> None:
        """Add `keys` to the reviewed marks, or take them off, writing `reviewed.json` only when it changes. Raises `MarksUnreadable` rather than replace a file it cannot read."""
        marks = self._reviewed()
        changed = marks | keys if reviewed else marks - keys
        if changed == marks:
            return
        output_sink.replace_output(
            self.reviewed_path,
            json.dumps({"format": REVIEWED_FORMAT, "reviewed": sorted(changed)}, indent=1, ensure_ascii=False)
            + "\n",
        )

    def _marks_for(self, before: kern.KernResolution | None, after: kern.KernResolution) -> list[str]:
        """Return the imported facing pairs a change from `before` to `after` moves that are not marked yet: the marks the change makes, which its journal event keeps so its undo can take exactly those off."""
        if before is None:
            return []
        moved = _pair_values(after).items() ^ _pair_values(before).items()
        try:
            reviewed = self._reviewed()
        except MarksUnreadable:
            return []
        return sorted(
            ({f"{lead}|{follow}" for (lead, follow), _by in moved} & self._imported_keys()) - reviewed
        )

    def post_reviewed(self, body: object) -> tuple[int, dict]:
        """Answer `POST /kern/reviewed`: mark one imported facing pair reviewed or take its mark off (`{"scope": "facing", "lead": <side>, "follow": <side>, "reviewed": bool}`), or every imported facing pair of two letters (`{"scope": "letters", "lead": <rune>, "follow": <rune>, ...}`). Only pairs the import set, whose sides the current build has, are marked; anything else is refused, and so is a change to marks that cannot be read."""
        if not isinstance(body, dict) or not isinstance(body.get("reviewed"), bool):
            return self._reply(
                400,
                {
                    "status": "invalid",
                    "message": 'a mark is {"scope", "lead", "follow", "reviewed": true or false}',
                },
            )
        lead, follow, scope = body.get("lead"), body.get("follow"), body.get("scope")
        if not isinstance(lead, str) or not isinstance(follow, str):
            return self._reply(400, {"status": "invalid", "message": "a mark names its lead and its follow"})
        if scope == kern_writer.FACING:
            try:
                kern_writer.facing_scope(lead, follow)
            except ValueError as error:
                return self._reply(400, {"status": "invalid", "message": str(error)})
        elif scope == kern_writer.LETTERS:
            if not (RUNE_NAME.match(lead) and RUNE_NAME.match(follow)):
                return self._reply(400, {"status": "invalid", "message": "a letters mark names two runes"})
        else:
            return self._reply(400, {"status": "invalid", "message": "a mark's scope is facing or letters"})
        sides = self._side_names()
        if sides is None:
            return self._reply(
                422,
                {
                    "status": "refused",
                    "message": "the rune files do not resolve, so the build's sides are not known",
                },
            )
        leading, following = sides
        imported = self._imported_keys()
        if scope == kern_writer.FACING:
            if lead not in leading or follow not in following:
                return self._reply(
                    422,
                    {
                        "status": "refused",
                        "message": f"{lead} | {follow} is no facing pair of the current build (side names are case-sensitive)",
                    },
                )
            keys = {f"{lead}|{follow}"} & imported
        else:
            keys = {
                key
                for key in imported
                if key.split("|")[0].split("[")[0] == lead
                and key.split("|")[-1].split("[")[0] == follow
                and key.split("|")[0] in leading
                and key.split("|")[-1] in following
            }
        if not keys:
            return self._reply(
                422,
                {"status": "refused", "message": "nothing in this scope was carried over from the old font"},
            )
        try:
            self._mark(keys, body["reviewed"])
        except MarksUnreadable as unreadable:
            return self._reply(
                409, {"status": "refused", "message": f"the marks were not changed: {unreadable}"}
            )
        except OSError as error:
            return self._reply(500, {"status": "error", "message": f"the marks were not written ({error})"})
        return self._reply(
            200, {"status": "reviewed" if body["reviewed"] else "unreviewed", "pairs": sorted(keys)}
        )

    # The write side.

    def context(self) -> _Context:
        """Return what an edit is checked against, from the caches `GET /kern` keeps, or raise `Refused` when the tables or the spec on disk do not load."""
        with self._lock:
            tables, _stamp, tables_error = self.tables()
            spec, _key, spec_error = self.spec()
            if tables is None or spec is None:
                raise Refused(
                    "refused",
                    f"the editor cannot check a change: {tables_error or spec_error or 'nothing loaded'}",
                )
            self.live()
            if self._resolved is None:
                live_error = (self._live or {}).get("error") or {}
                raise Refused(
                    "refused",
                    f"the editor cannot check a change: {live_error.get('message', 'the glyphs were not minted')}",
                    live_error.get("issues", ()),
                )
            glyphs, ss10, before = self._resolved
            return _Context(spec, tables, glyphs, ss10, before)

    def _order_key(self, spec: ResolvedSpec) -> Callable[[str], tuple[int, ...]]:
        return lambda rune: kern.rune_order_key(spec, rune)

    def resolve_with(self, context: _Context, blocks: Mapping[str, list]) -> kern.KernResolution:
        """Return the resolution with each rune of `blocks` holding the records given for it, in canonical order with the provenance the file would give them, resolved in memory over the context's spec, tables and glyphs. Raises `kern.KernError` as the stage does."""
        runes = dict(context.spec.runes)
        order = self._order_key(context.spec)
        for rune, records in blocks.items():
            owner = runes.get(rune)
            if owner is None:
                raise Refused("refused", f"{rune} is not a rune of the alphabet the server loads")
            label = file_label(self.runes_dir / f"{rune}.yaml")
            built = tuple(
                kern_writer.kern_record(rune, record, Provenance(label, f"kern[{index}]"))
                for index, record in enumerate(kern_writer.canonical(records, order))
            )
            runes[rune] = replace(owner, kern=replace(owner.kern, records=built))
        return kern.resolve(replace(context.spec, runes=runes), context.tables, context.glyphs, context.ss10)

    def _values_with(self, context: _Context, rune: str, records: list) -> dict[tuple[str, str], int]:
        """Return every facing pair's value with `rune`'s block holding `records` (`resolve_with`)."""
        return _pair_values(self.resolve_with(context, {rune: records}))

    def _apply_set(self, records: list, one: EditSet, file: str) -> list:
        """Return `records` with the set applied: its scope's record given `one.by` and its note, added when the block has none, or dropped for None."""
        index = kern_writer.scope_index(records, one.scope, file)
        if one.by is None:
            return [record for number, record in enumerate(records) if number != index]
        if index is None:
            return [*records, one.scope.record(one.by, one.why)]
        return [
            kern_writer.with_value(record, one.by, one.why) if number == index else record
            for number, record in enumerate(records)
        ]

    def _validate(
        self, context: _Context, rune: str, new_text: str
    ) -> tuple[ResolvedSpec, kern.KernResolution]:
        return self.check(context, {rune: new_text})

    def check(self, context: _Context, texts: Mapping[str, str]) -> tuple[ResolvedSpec, kern.KernResolution]:
        """Check the runes directory with each rune file of `texts` replaced by the text given for it, as the next build would: the spec loader over a temporary copy, `kern.resolve`, the kern lookup's plan (`emit_gpos.kern_plan`), and the contact check at the kerned offset over every member break whose value moves. A copy in which a file of `texts` was not replaced is refused, so the check is always of the files to be written. Raises `Refused` with the build's messages. The editor checks one file this way."""
        real_prefix = file_label(self.runes_dir)
        with tempfile.TemporaryDirectory(prefix="kern-edit-") as scratch:
            copy_dir = Path(scratch) / self.runes_dir.name
            copy_dir.mkdir()
            replaced: set[str] = set()
            for path in sorted(self.runes_dir.glob("*.yaml")):
                if path.stem in texts:
                    (copy_dir / path.name).write_bytes(texts[path.stem].encode("utf-8"))
                    replaced.add(path.stem)
                else:
                    shutil.copyfile(path, copy_dir / path.name)
            for rune in sorted(set(texts) - replaced):
                raise Refused("refused", f"the runes directory holds no file named exactly {rune}.yaml")

            def home(text: str) -> str:
                return text.replace(f"{copy_dir}{os.sep}", f"{real_prefix}/")

            try:
                spec = self._load(copy_dir)
            except spec_load.SpecError as error:
                raise Refused(
                    "refused",
                    "the spec loader refuses the result",
                    [("spec", home(str(issue))) for issue in error.issues],
                ) from None
            except Exception as error:
                raise Refused(
                    "refused",
                    f"the spec loader refuses the result ({type(error).__name__}: {home(str(error))})",
                ) from None
            labels = {
                file_label(copy_dir / path.name): file_label(self.runes_dir / path.name)
                for path in self.runes_dir.glob("*.yaml")
            }
        spec = replace(
            spec,
            runes={
                name: replace(
                    one,
                    kern=replace(
                        one.kern,
                        records=tuple(
                            (
                                replace(
                                    record,
                                    provenance=Provenance(
                                        labels.get(record.provenance.file, record.provenance.file),
                                        record.provenance.path,
                                    ),
                                )
                                if record.provenance is not None
                                else record
                            )
                            for record in one.kern.records
                        ),
                    ),
                )
                for name, one in spec.runes.items()
            },
        )
        try:
            resolution = kern.resolve(spec, context.tables, context.glyphs, context.ss10)
        except kern.KernError as error:
            raise Refused("refused", "the kern stage refuses the result", error.issues) from None
        try:
            emit_gpos.kern_plan(resolution, context.glyphs)
        except emit_gpos.KernPlanError as error:
            raise Refused(
                "refused", "the kern lookup refuses the result", [("KernPlanError", str(error))]
            ) from None
        before = _member_values(context.before)
        moved = {
            member: value
            for member, value in _member_values(resolution).items()
            if before.get(member, 0) != value
        }
        records = {**resolution.realized, **context.glyphs}
        allow = defects.read_allow(self.allow_path) if self.allow_path.is_file() else frozenset()
        report = defects.check_kerned_breaks(allow, records, moved)
        if report.errors:
            raise Refused(
                "refused",
                "the defect gate refuses the result: kerned ink would overlap",
                [(defect.code, f"[{defect.signature}]: {defect.message}") for defect in report.errors],
            )
        return spec, resolution

    @contextmanager
    def hold_pass_lock(self) -> Iterator[None]:
        """Hold the cycle's pass lock without waiting, writing the server's pid into it as the accept does; raise `store_lock.LockBusy` while a pass holds it."""
        with store_lock.hold_flock(self.pass_lock, blocking=False) as fd:
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
            yield

    def _replace(self, path: Path, text: str) -> None:
        """Put `text` in place of `path` atomically, staged beside the journal (`kern_writer.replace_text`)."""
        kern_writer.replace_text(path, text, self.journal.path.parent)

    def _block(self, rune: str) -> tuple[Path, str, str, str, str]:
        """Return `rune`'s file, its label, its text as its bytes hold it, the text before its block and the block. The file's name must be exactly the rune's, which a case-insensitive file system would not check on its own."""
        path = self.runes_dir / f"{rune}.yaml"
        file = file_label(path)
        try:
            names = set(os.listdir(self.runes_dir))
        except OSError:
            names = set()
        if path.name not in names:
            raise Refused(
                "refused", f"there is no rune file named exactly {path.name} in {file_label(self.runes_dir)}"
            )
        try:
            text = kern_writer.read_text(path)
        except (OSError, UnicodeDecodeError) as error:
            raise Refused("refused", f"{file} cannot be read ({type(error).__name__}: {error})") from None
        prefix, block = kern_writer.split_file(text)
        return path, file, text, prefix, block

    def _check_runes(self, sets: list[EditSet], spec: ResolvedSpec) -> None:
        """Refuse a delta that names a rune the loaded spec does not hold, by its exact name, as the leading or the following rune of any set, whatever the set's value."""
        for one in sets:
            for rune in (one.scope.rune, one.scope.follow_rune):
                if rune not in spec.runes:
                    raise Refused(
                        "refused",
                        f"{rune} is no rune of the alphabet the server loads (rune names are case-sensitive)",
                    )

    def _conflict(self, file: str, block: str, message: str) -> Refused:
        return Refused(
            "conflict",
            message,
            block={"file": file, "sha256": kern_writer.block_sha(block), "text": block},
        )

    def _write(
        self,
        path: Path,
        text: str,
        new_text: str,
        event: dict,
        context: _Context,
        checked: tuple[ResolvedSpec, kern.KernResolution],
    ) -> dict:
        """Replace the rune file, journal the change, and keep the checked spec and resolution as the answer's, returning the journal event. A file that changed while the change was checked is a conflict, and an append that fails puts the file back. Every write moves the cache's key: the checked answer is kept under the new files' signatures only while the file holds exactly what was checked, and otherwise the next answer is computed from the disk."""
        now = kern_writer.read_text(path)
        if now != text:
            raise self._conflict(
                file_label(path),
                kern_writer.split_file(now)[1],
                f"{file_label(path)} changed while the change was checked; try again",
            )
        if event["event"] == "applied":
            event["marked"] = self._marks_for(context.before, checked[1])
        self._replace(path, new_text)
        try:
            written = self.journal.append(event)
        except BaseException:
            self._replace(path, text)
            raise
        spec, resolution = checked
        with self._lock:
            self.tables()
            key = self._spec_signature()
            if kern_writer.read_text(path) == new_text:
                self._spec_key, self._spec, self._spec_error = key, spec, None
                self._resolved = (context.glyphs, context.ss10, resolution)
                self._live_key = (self._tables_key, key)
                self._live = {"values": live_values(resolution), "error": None}
            else:
                self._spec_key = self._live_key = None
        try:
            if event["event"] == "applied" and event.get("marked"):
                self._mark(set(event["marked"]), True)
            elif event["event"] == "undo" and event.get("unmarked"):
                self._mark(set(event["unmarked"]), False)
        except (OSError, MarksUnreadable) as error:
            print(f"kern: the reviewed marks were not changed ({error})", flush=True)
        return written

    def _edit(
        self, sets: list[EditSet], delta: dict, starts: dict[str, str], pending: int | None = None
    ) -> dict:
        """Apply one delta's sets to their rune file, as the module docstring says, and return the reply's fields. Runs under the pass lock.

        A record a set gives a value is checked against what it would inherit: the values with every other record of the new block are resolved in memory before the new file is checked (`_values_with`), and when they are the checked file's values the record goes, and the file without it is checked in its place. The check runs too for a set that gives a record the value it already has, so a record set to what it would inherit goes either way. When several such records go at once and the values move, every one of them stays.
        """
        rune = sets[0].scope.rune
        context = self.context()
        self._check_runes(sets, context.spec)
        path, file, text, prefix, block = self._block(rune)
        sha = kern_writer.block_sha(block)
        starts.setdefault(rune, sha)
        for one in sets:
            if one.base not in (sha, starts[rune]):
                raise self._conflict(
                    file,
                    block,
                    f"the kern block of {file} is not the one this change was made against; the view now shows the block as it stands",
                )
        order = self._order_key(context.spec)
        newline = kern_writer.line_ending(text)
        try:
            records = kern_writer.parse_block(block, file, newline)
            changed = list(records)
            for one in sets:
                changed = self._apply_set(changed, one, file)
            plain_before = [kern_writer.plain(record) for record in records]
            same = [kern_writer.plain(record) for record in changed] == plain_before
            inherited: list[tuple[EditSet, dict | None]] = []
            for one in sets:
                index = kern_writer.scope_index(changed, one.scope, file)
                if one.by is None or index is None:
                    continue
                without = [record for number, record in enumerate(changed) if number != index]
                try:
                    inherited.append((one, self._values_with(context, rune, without)))
                except kern.KernError:
                    inherited.append((one, None))
            if same and (not inherited or context.before is None):
                return {"status": "unchanged", "file": file, "changes": []}
            new_block, new_text, checked = block, text, None
            if same:
                assert context.before is not None
                values = _pair_values(context.before)
            else:
                new_block = kern_writer.render_block(kern_writer.canonical(changed, order), newline)
                new_text = kern_writer.splice(prefix, new_block, file)
                checked = self._validate(context, rune, new_text)
                values = _pair_values(checked[1])
            redundant = [one for one, without in inherited if without == values]
            if redundant:
                noted = [one for one in redundant if one.why]
                if noted:
                    raise Refused(
                        "refused",
                        f"{noted[0].scope.label} at {noted[0].by} is the value it takes without a record of its own, so there is no record to hold the note; give it another value first",
                    )
                dropped = [
                    record for record in changed if not any(one.scope.matches(record) for one in redundant)
                ]
                if [kern_writer.plain(record) for record in dropped] != plain_before:
                    dropped_block = kern_writer.render_block(kern_writer.canonical(dropped, order), newline)
                    dropped_text = kern_writer.splice(prefix, dropped_block, file)
                    dropped_checked = self._validate(context, rune, dropped_text)
                    if _pair_values(dropped_checked[1]) == values:
                        changed, new_block, new_text, checked = (
                            dropped,
                            dropped_block,
                            dropped_text,
                            dropped_checked,
                        )
                elif not same:
                    return {"status": "unchanged", "file": file, "changes": []}
            if checked is None or new_block == block:
                return {"status": "unchanged", "file": file, "changes": []}
        except kern_writer.BlockRefused as refusal:
            raise Refused("refused", str(refusal)) from None
        changes = []
        for one in sets:
            before_index = kern_writer.scope_index(records, one.scope, file)
            after_index = kern_writer.scope_index(changed, one.scope, file)
            changes.append(
                {
                    "scope": one.scope.label,
                    "before": (
                        None if before_index is None else kern_writer.record_line(records[before_index])
                    ),
                    "after": None if after_index is None else kern_writer.record_line(changed[after_index]),
                }
            )
        event = {
            "event": "applied",
            "rune": rune,
            "file": file,
            "before": block,
            "after": new_block,
            "before_sha256": sha,
            "after_sha256": kern_writer.block_sha(new_block),
            "delta": delta,
            **({} if pending is None else {"pending": pending}),
        }
        written = self._write(path, text, new_text, event, context, checked)
        return {"status": "applied", "seq": written["seq"], "file": file, "changes": changes}

    def _undo(self, seq: int, starts: dict[str, str], pending: int | None = None) -> dict:
        """Revert the change the undo names, `seq`: an `applied` event, or a pending request since written, whose `applied` event it then names. It must be the newest applied change no undo has reverted, and its block must still be what it wrote; otherwise the undo is refused or a conflict, and changes nothing. Returns the reply's fields. Runs under the pass lock."""
        target = seq
        if self.journal.applied(seq) is None:
            settled = self.journal.settled(seq)
            if settled is None:
                raise Refused("nothing", f"the journal holds no change {seq} to undo")
            if settled["event"] != "applied":
                why = f": {settled['reason']}" if settled.get("reason") else ""
                raise Refused(
                    "nothing",
                    f"change {seq} was never written ({settled['event']}{why}), so there is nothing to undo",
                )
            target = settled["seq"]
        change = self.journal.applied(target)
        if change is None:
            raise Refused("nothing", f"the journal holds no change {target} to undo")
        if change.get("undone"):
            raise Refused("nothing", f"change {target} is already undone")
        newest = self.journal.undo_target()
        if newest != target:
            raise Refused(
                "conflict",
                f"change {target} is not the newest change the editor wrote (change {newest} is), so undoing it would revert an older change under a newer one; undo that one first, or set the value by hand",
            )
        rune, before, after = change.get("rune"), change.get("before"), change.get("after")
        if not (isinstance(rune, str) and isinstance(before, str) and isinstance(after, str)):
            raise Refused("refused", f"the journal's change {target} does not say which block it wrote")
        context = self.context()
        path, file, text, prefix, block = self._block(rune)
        starts.setdefault(rune, kern_writer.block_sha(block))
        if block != after:
            raise self._conflict(
                file,
                block,
                f"the kern block of {file} changed since change {target}, so undo cannot put back what it found; fix it by hand",
            )
        try:
            new_text = kern_writer.splice(prefix, before, file)
        except kern_writer.BlockRefused as refusal:
            raise Refused("refused", str(refusal)) from None
        checked = self._validate(context, rune, new_text)
        event = {
            "event": "undo",
            "undoes": target,
            "rune": rune,
            "file": file,
            "before": block,
            "after": before,
            "before_sha256": kern_writer.block_sha(block),
            "after_sha256": kern_writer.block_sha(before),
            "unmarked": change.get("marked", []),
            **({} if pending is None else {"pending": pending}),
        }
        written = self._write(path, text, new_text, event, context, checked)
        return {
            "status": "undone",
            "seq": written["seq"],
            "undoes": target,
            "file": file,
            "delta": change.get("delta"),
        }

    def _drain_locked(self, starts: dict[str, str]) -> bool:
        """Apply every pending request in order, journaling each as applied, unchanged or dropped, and return whether none is left. A request whose application raises is dropped with the exception as its reason, so no request is retried for good; only the journal's own lock being busy (`kern_writer.JournalBusy`) leaves it, and the ones after it, for the next try, at most `JOURNAL_BUSY_TRIES` times before it is dropped too. Runs under the pass lock."""
        for seq, event in self.journal.pending_items():
            request = event.get("request")
            if not isinstance(request, dict):
                request = {}
            try:
                try:
                    if request.get("kind") == "undo":
                        undoes = request.get("undoes")
                        if not isinstance(undoes, int) or isinstance(undoes, bool):
                            raise DeltaInvalid("the queued undo does not name the change it undoes")
                        self._undo(undoes, starts, pending=seq)
                    else:
                        delta = request.get("delta")
                        outcome = self._edit(
                            parse_delta(delta), delta if isinstance(delta, dict) else {}, starts, pending=seq
                        )
                        if outcome["status"] == "unchanged":
                            self.journal.append({"event": "unchanged", "pending": seq})
                    self._busy.pop(seq, None)
                except kern_writer.JournalBusy:
                    tries = self._busy[seq] = self._busy.get(seq, 0) + 1
                    if tries < JOURNAL_BUSY_TRIES:
                        return False
                    self._drop(seq, request, f"the journal's lock stayed busy through {tries} tries")
                except Refused as refusal:
                    if refusal.status == "nothing":
                        self.journal.append({"event": "unchanged", "pending": seq})
                    else:
                        issues = "; ".join(f"{code}: {text}" for code, text in refusal.issues)
                        self._drop(seq, request, refusal.message + (f" ({issues})" if issues else ""))
                except Exception as error:
                    self._drop(seq, request, f"{type(error).__name__}: {error}")
            except kern_writer.JournalBusy:
                return False
        return True

    def _drop(self, seq: int, request: dict, reason: str) -> None:
        self._busy.pop(seq, None)
        self.journal.append({"event": "dropped", "pending": seq, "reason": reason, "request": request})

    def _reply(self, status: int, fields: dict) -> tuple[int, dict]:
        return status, {**fields, "values": self.values_payload(recheck=False)}

    def _queue(self, request: dict, rune: str | None, message: str) -> tuple[int, dict]:
        """Journal `request` as pending and answer 202, or 503 when the journal's lock stays busy."""
        try:
            event = self.journal.append(
                {"event": "pending", "request": request, **({"rune": rune} if rune else {})}
            )
        except kern_writer.JournalBusy as busy:
            return self._reply(
                503,
                {"status": "error", "message": f"the change was not written or queued: {busy}; try again"},
            )
        return self._reply(202, {"status": "pending", "seq": event["seq"], "message": message})

    def _locked_write(
        self, request: dict, rune: str | None, run: Callable[[dict[str, str]], dict]
    ) -> tuple[int, dict]:
        try:
            with self.hold_pass_lock():
                starts: dict[str, str] = {}
                if self._drain_locked(starts):
                    fields = run(starts)
                else:
                    fields = None
        except store_lock.LockBusy:
            return self._queue(
                request,
                rune,
                f"a cycle pass (pid {self.pass_holder() or 'unknown'}) is reading the runes; the change is written when it ends",
            )
        except Refused as refusal:
            return self._reply(
                refusal.code,
                {
                    "status": refusal.status,
                    "message": refusal.message,
                    "issues": refusal.issues,
                    **({"block": refusal.block} if refusal.block is not None else {}),
                },
            )
        except kern_writer.JournalBusy as busy:
            return self._reply(
                503, {"status": "error", "message": f"the change was not written: {busy}; try again"}
            )
        except Exception as error:
            return self._reply(
                500, {"status": "error", "message": f"the write failed ({type(error).__name__}: {error})"}
            )
        if fields is None:
            return self._queue(
                request, rune, "earlier changes wait for the journal's lock; this one is written after them"
            )
        return self._reply(200, fields)

    def post_delta(self, body: object) -> tuple[int, dict]:
        """Answer `POST /kern`: the HTTP status and the reply, which always carries `GET /kern`'s answer as `values`."""
        try:
            sets = parse_delta(body)
        except DeltaInvalid as invalid:
            return self._reply(400, {"status": "invalid", "message": str(invalid)})
        assert isinstance(body, dict)
        return self._locked_write(
            {"kind": "delta", "delta": body},
            sets[0].scope.rune,
            lambda starts: self._edit(sets, body, starts),
        )

    def post_undo(self, body: object) -> tuple[int, dict]:
        """Answer `POST /kern/undo`, whose body names the journal `seq` of the change to undo (`{"undoes": seq}`), the `seq` a write's reply gave: a pending request's `seq` cancels it while it is still pending, journaled as `cancelled`, and reverts nothing; otherwise the change is undone as `_undo` says, or queued while a pass holds the lock."""
        undoes = body.get("undoes") if isinstance(body, dict) else None
        if not isinstance(undoes, int) or isinstance(undoes, bool):
            return self._reply(
                400, {"status": "invalid", "message": 'an undo names the change it undoes: {"undoes": <seq>}'}
            )
        if self.journal.is_pending(undoes):
            try:
                event = self.journal.append({"event": "cancelled", "pending": undoes})
            except kern_writer.JournalBusy as busy:
                return self._reply(
                    503, {"status": "error", "message": f"the undo was not recorded: {busy}; try again"}
                )
            return self._reply(200, {"status": "cancelled", "seq": event["seq"], "cancels": undoes})
        return self._locked_write(
            {"kind": "undo", "undoes": undoes}, None, lambda starts: self._undo(undoes, starts)
        )

    def drain(self) -> None:
        """Apply the pending requests when the pass lock is free, leaving them for the next try while it is held."""
        if not self.journal.pending_count():
            return
        try:
            with self.hold_pass_lock():
                self._drain_locked({})
        except store_lock.LockBusy:
            return

    def _tick(self) -> None:
        if self._draining is not None or not self.journal.pending_count() or self.pass_holder() is not None:
            return
        future = self._writes.submit(self.drain)
        self._draining = future

        def done(finished) -> None:
            self._draining = None
            error = finished.exception()
            if error is not None:
                print(
                    f"kern: applying the pending changes failed: {type(error).__name__}: {error}", flush=True
                )

        future.add_done_callback(done)

    def handlers(self) -> list[tuple]:
        """Return the routes' Tornado handlers, for `serve.main` to put before the static files, and start the timer that applies pending requests once a pass ends."""
        from tornado.ioloop import IOLoop, PeriodicCallback
        from tornado.web import RequestHandler

        routes = self
        PeriodicCallback(self._tick, DRAIN_INTERVAL_MS).start()

        def answer(handler: RequestHandler, status: int, reply: dict) -> None:
            handler.set_status(status)
            handler.set_header("Content-Type", "application/json; charset=utf-8")
            handler.finish(json.dumps(reply, ensure_ascii=False))

        async def written(post: Callable[[object], tuple[int, dict]], body: object) -> tuple[int, dict]:
            """Run a write on the writes' thread, and answer anything it raises as the JSON error reply, never as Tornado's HTML page."""
            try:
                return await IOLoop.current().run_in_executor(routes._writes, post, body)
            except Exception as error:
                return 500, {
                    "status": "error",
                    "message": f"the write failed ({type(error).__name__}: {error})",
                }

        def body_of(handler: RequestHandler) -> object:
            try:
                return json.loads(handler.request.body or b"null")
            except ValueError:
                return None

        class ValuesHandler(RequestHandler):
            def set_default_headers(self) -> None:
                self.set_header("Cache-Control", "no-store")

            async def get(self) -> None:
                try:
                    body = await routes.values_future()
                except Exception as error:
                    self.set_status(500)
                    self.set_header("Content-Type", "application/json")
                    self.finish(json.dumps({"error": f"{type(error).__name__}: {error}"}))
                    return
                self.set_header("Content-Type", "application/json; charset=utf-8")
                self.finish(body)

            async def post(self) -> None:
                status, reply = await written(routes.post_delta, body_of(self))
                answer(self, status, reply)

        class UndoHandler(RequestHandler):
            def set_default_headers(self) -> None:
                self.set_header("Cache-Control", "no-store")

            async def post(self) -> None:
                status, reply = await written(routes.post_undo, body_of(self))
                answer(self, status, reply)

        class ReviewedHandler(RequestHandler):
            def set_default_headers(self) -> None:
                self.set_header("Cache-Control", "no-store")

            async def post(self) -> None:
                status, reply = await written(routes.post_reviewed, body_of(self))
                answer(self, status, reply)

        class ApartHandler(RequestHandler):
            def set_default_headers(self) -> None:
                self.set_header("Cache-Control", "no-store")

            async def post(self) -> None:
                status, reply = await written(routes.post_apart, body_of(self))
                answer(self, status, reply)

        class InventoryHandler(RequestHandler):
            def set_default_headers(self) -> None:
                self.set_header("Cache-Control", "no-store")

            async def get(self) -> None:
                body = await IOLoop.current().run_in_executor(None, routes.inventory_bytes)
                if body is None:
                    self.set_status(404)
                    self.set_header("Content-Type", "application/json")
                    self.finish(
                        json.dumps(
                            {
                                "error": f"no {kern_inventory.KERN_INVENTORY_JSON} under {routes.m1_out}; the M1 build writes it (make artifact-cycle)"
                            }
                        )
                    )
                    return
                self.set_header("Content-Type", "application/json; charset=utf-8")
                self.finish(body)

        return [
            (r"/kern", ValuesHandler),
            (r"/kern/undo", UndoHandler),
            (r"/kern/reviewed", ReviewedHandler),
            (r"/kern/apart", ApartHandler),
            (r"/kern/inventory", InventoryHandler),
        ]
