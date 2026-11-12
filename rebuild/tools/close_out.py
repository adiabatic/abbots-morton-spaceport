"""Fold the verdict store's accepted verdicts into the accepted list, `rebuild/accepted-units.txt`, and start the store and its journal again from what is left. `make close-out` runs it as a dry run and `make close-out ARGS=--apply` writes. It runs by hand, from the close-out procedure, with the review server stopped, and never in the artifact cycle.

The fold reads the store, the list beside it (`rebuild.review.accepted`, whose docstring gives the list's format and the one rule every reader of the store answers through) and the served corpus's human unit ids. It reads the store's own records and tombstones and its override section, the records and tombstones on listed ids (`accepted.Overrides`), as one store (`accepted.joined`), and consumes the section, the one writer that does. The new list holds each listed id that is a human unit of the served corpus and holds no override, and each human unit whose record, its own or its override, is approve, either or identical (`accepted.ACCEPTING_VERDICTS`), with that record's mark, so a reject, neither, skip or tombstone on a listed id takes it out. Its `closed_at` is the later of the current time, the largest `at` among the records it folds and the previous list's `closed_at`, cut to whole seconds, so it bounds the `at` of every record the list stands for. The new store, stamped for the served corpus even when it holds nothing, keeps the store's reject, neither and skip records and its tombstones on the corpus's human units, overrides among them, as its own records and tombstones, none of whose units the new list names, so it holds no override section, and its `accepted` field names the new list. The notes of the folded records go, kept only in the archive.

Its checks run in this order, each refusing with nothing written. `make verdict-ready`'s blocking checks (corpus, freshness, gates and verdict store) must pass, so the human unit ids it reads belong to a corpus built from the tree the list is committed beside; they run before either lock is taken, since under the pass lock they would name the close-out itself as the pass to wait for, and not at all while a close-out's intent is left, since a close-out killed between its renames fails them on the pairing until it is finished. It takes the artifact cycle's pass lock (var/cycle/pass.lock beside the store) without waiting and holds it to the end, so no pass that snapshotted the store before the close-out lands after it. It refuses while a review server listens that may write the store (`serves`). It takes the store's lock through `landing.locked_store`, which first finishes a land whose holder died and then writes into the store the journal's events past the store's last write (`landing.catch_up`), so the saves a review server stopped before its idle write had journaled are folded or left in the store, and the new journal's base holds them, and then it finishes or drops a close-out whose holder died (`finish_interrupted`). The working tree's list must equal `HEAD`'s (`git status`, which runs without refreshing the index) and be well formed (`accepted.Malformed`), the store must be an ams-review-verdicts/1 document holding no record `verdict_store.well_formed` refuses, and the store's `accepted` field must name the list (`accepted.pair`), on the first close-out there being neither list nor field; and the served corpus's stamp must still be the one the readiness checks read. With nothing to fold, no accepting record in the store and no listed id off the corpus's human units, it says so and writes nothing, so two close-outs minutes apart make one list. It refuses nothing for rejects, blanks or orphan documents, which stay.

The dry run makes the same checks without taking either lock, prints every refusal the apply would make, and prints the report; it writes nothing, and exits 1 when the apply would refuse. It reports on the store as the apply's lock leaves it once caught up with the journal (`landing.caught_up_bytes`, which applies the journal's events past the store's last write in memory), so a save a review server stopped before its idle write had journaled counts in the report as in the apply, and it says so in a note. The report, which the apply prints too, counts the store's records and tombstones, the ids the fold adds to the list, keeps (re-marking those an accepting override changed), takes out for an override or a clear, and drops because they left the corpus's human units, what the store keeps and drops, the blank human units after the fold, the note bytes it discards and the orphan and conflict documents (`verdict_store.summarize_orphans`), names the journal it replaces, which no archive keeps, with its bytes and its first event's time, and the older close-out archives it deletes, and names the stashes it deletes, the other root verdicts files, which it leaves, and the `rebuild/M1-BATCH*-PROGRESS.md` files.

The apply writes the new list, the new store and the archive to the staging directory var/close-out/, outside var/cycle/, whose directories every pass deletes as it starts, the archive holding the store as found (`ARCHIVE_STORE`), the list before (`ARCHIVE_BEFORE`, absent when there was none) and after (`ARCHIVE_AFTER`), each gzipped, and the counts (`ARCHIVE_COUNTS`); then the intent var/cycle/close-out.json. Then (`_complete`) it moves the archive to var/keep/close-outs/<closed_at>/, renames the staged list over the list and the staged store over the store, refuses unless the store then names the new list, writes a new journal holding one base event of the store, source `close-out`, with its `cleared` list and its `accepted` field (`journal.record_transition`), to a temporary file renamed over the journal, removes the unjournaled marker (`journal.unjournaled_marker_for`) and the journal scan state, deletes the root's `verdicts-autosave-*.json` stashes, which no event in the new journal names, and every other close-out archive, so only the last close-out's is kept, and removes the staging directory and the intent. Every path is beside the store, so a run on a store in another directory leaves the checkout's files alone.

A close-out killed before it renamed the list changed nothing a reader sees, so the next `--apply`, which tells the two apart by the list's sha256 against the one it found and the one it wrote (both in the intent), drops it, deleting its staging directory, its archive and its intent; one killed before it wrote the intent leaves only the staging directory, which the next close-out writes over. One killed after it leaves the store a step behind the list, and every reader refuses on the pairing until the next `--apply` finishes it from the intent: that renames what is left in the staging directory into place, refusing when the list or the store has changed since or when the store is still to be renamed and the staged store is gone, and writes the journal from the store as it then stands, so a save made between the store's rename and the journal's is in both. The run that finishes or drops one folds nothing; the next run closes out. The dry run and `--undo` refuse while the intent is there.

`--undo` puts back the latest archive's list from before, deleting the list when the archive holds none, and writes the store as the archive's store with the live store's records and tombstones laid over it, each store's override section read with its own records and tombstones (`accepted.joined`), a record over a record by `at` and the tombstones as `merge_verdicts.merge_into` keeps them, so the verdicts made since the close-out survive, with the records and tombstones on the ids of the list it puts back as the store's override section (`accepted.split`) and its `accepted` field naming that list; then it appends a base event of that store, source `close-out-undo`, to the journal, leaving the unjournaled marker until that append is made. It takes the same locks, refuses on the same server, and refuses unless the list and the list the store names are each the archive's list after or before, so an undo cut short runs again. The list it puts back is a tracked change to commit, never to revert, since the store it writes names that list; when the close-out's own commit was never made, the tree already matches `HEAD`. `rebuild.tools.rekey_verdicts` re-keys the latest archive with the store and the list, so an undo still lands after a re-key.

Usage:
  uv run python -m rebuild.tools.close_out [--apply | --undo] [--autosave FILE] [--corpus DIR]
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.pipeline import fingerprint  # noqa: E402
from rebuild.review import accepted, journal  # noqa: E402
from rebuild.review.landing import (
    LandUnrecoverable,
    Store,
    caught_up_bytes,
    document_bytes,
    locked_store,
    read_manifest,
)  # noqa: E402
from rebuild.review.landing import intent_path_for as land_intent_path_for  # noqa: E402
from rebuild.review.store_lock import LockBusy, hold_flock, holder  # noqa: E402
from rebuild.review.verdict_store import (  # noqa: E402
    EXPORT_FORMAT,
    MalformedStore,
    note_of,
    orphans_dir_for,
    read_store_document,
    summarize_orphans,
)
from rebuild.tools import verdict_ready  # noqa: E402
from rebuild.tools.merge_verdicts import merge_into  # noqa: E402
from rebuild.tools.review_server import capabilities, server_listening  # noqa: E402

KEEP = Path("var") / "keep" / "close-outs"
CYCLE = Path("var") / "cycle"
STAGING = Path("var") / "close-out"
PASS_LOCK_NAME = "pass.lock"
SCAN_STATE_NAME = "journal-scan.json"
INTENT_FORMAT = "ams-close-out-intent/1"
LIST_FILE = "accepted-units.txt"
STORE_FILE = "store.json"
ARCHIVE_NAME = "archive"
ARCHIVE_STORE = "store.json.gz"
ARCHIVE_BEFORE = "accepted-units-before.txt.gz"
ARCHIVE_AFTER = "accepted-units-after.txt.gz"
ARCHIVE_COUNTS = "counts.json"
SOURCE = "close-out"
UNDO_SOURCE = "close-out-undo"
GZIP_LEVEL = 6
STASH_GLOB = "verdicts-autosave-*.json"
PROGRESS_GLOB = "rebuild/M1-BATCH*-PROGRESS.md"
_FOLDED_AT = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z")


class Refused(RuntimeError):
    """A refusal of the close-out or its undo; the message names the cause and the remedy, and nothing was written."""


def intent_path_for(store: Path) -> Path:
    return Path(store).parent / accepted.CLOSE_OUT_INTENT


def staging_path_for(store: Path) -> Path:
    """Return the close-out's staging directory beside the store, var/close-out/, outside var/cycle/, whose directories every pass deletes when it starts (`artifact_cycle.sweep_run_dirs`), so a pass started after a killed close-out leaves what the next `--apply` finishes from."""
    return Path(store).parent / STAGING


def pass_lock_path_for(store: Path) -> Path:
    """Return the artifact cycle's pass lock beside the store, var/cycle/pass.lock, which for the live store is `artifact_cycle.pass_lock_path()`."""
    return Path(store).parent / CYCLE / PASS_LOCK_NAME


def scan_state_path_for(store: Path) -> Path:
    """Return the journal scan state beside the store, var/cycle/journal-scan.json, which for the live store is `artifact_cycle.journal_scan_path()`."""
    return Path(store).parent / CYCLE / SCAN_STATE_NAME


def archive_root_for(store: Path) -> Path:
    return Path(store).parent / KEEP


def latest_archive(store: Path) -> Path | None:
    """Return the latest close-out archive beside the store, the one whose name sorts last, or None when there is none."""
    archives = _archives(store)
    return archives[-1] if archives else None


@dataclass(frozen=True)
class Archive:
    """A close-out archive's members, decompressed: the store as found (None when there was no store), the list before (None when there was none) and the list after."""

    path: Path
    store: bytes | None
    before: bytes | None
    after: bytes


def read_archive(path: Path) -> Archive:
    def member(name: str) -> bytes | None:
        try:
            return gzip.decompress((path / name).read_bytes())
        except FileNotFoundError:
            return None

    after = member(ARCHIVE_AFTER)
    if after is None:
        raise Refused(f"{path} holds no {ARCHIVE_AFTER}, so it is not a close-out archive")
    return Archive(path, member(ARCHIVE_STORE), member(ARCHIVE_BEFORE), after)


def write_archive_member(path: Path, raw: bytes) -> None:
    """Write `raw` gzipped to `path` through a temporary file, with no timestamp in the header, so one content always gives one file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0))
    os.replace(tmp, path)


def _sha256(path: Path) -> str | None:
    try:
        return fingerprint.file_sha256(path)
    except FileNotFoundError:
        return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def _normalize(record: dict) -> dict:
    return {
        "unit": record["unit"],
        "verdict": record["verdict"],
        "note": note_of(record),
        "at": record.get("at") or "",
    }


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@contextlib.contextmanager
def pass_lock(store: Path) -> Iterator[None]:
    """Hold the artifact cycle's pass lock beside `store` without waiting, writing this process's pid into it as a pass does (`artifact_cycle.pass_lock`), so a pass that starts meanwhile names the holder while it waits. Raise `store_lock.LockBusy` while a pass holds it."""
    with hold_flock(pass_lock_path_for(store), blocking=False) as fd:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        yield


def serves(store: Path) -> bool:
    """Return whether a review server listens that may write `store`: its /capabilities answer names that store, or names none, as a server that predates /capabilities or does not answer does. `rebuild.tools.rekey_verdicts` applies the same rule."""
    if not server_listening():
        return False
    answer = capabilities()
    served = answer.get("autosave") if isinstance(answer, dict) else None
    return not isinstance(served, str) or Path(served).resolve() == Path(store).resolve()


def pass_refusal(store: Path) -> str:
    """Return the refusal of a writer that found the pass lock beside `store` held (`pass_lock`), naming the holder's pid. A pass between its snapshot and its land would lay the saves made since over a prepared store from before the write."""
    who = holder(pass_lock_path_for(store)) or "unknown"
    return f"a pass holds {pass_lock_path_for(store)} (pid {who}); wait for it to finish"


def _server_refusal(store: Path) -> str:
    return (
        f"a review server listening on port 7294 may write {store.name}: its /capabilities answer names that store, or "
        "names none. Stop it for the close-out and restart it afterwards, and the open tabs fetch the store and the "
        "list again at the restart"
    )


def _intent_refusal(store: Path) -> str:
    return (
        f"{_rel(intent_path_for(store), store.parent)} is left by a close-out that did not finish; "
        "`make close-out ARGS=--apply` finishes it, or drops it when it had not written the list"
    )


def _readiness_refusal(result: dict, ready: bool) -> str | None:
    if ready:
        return None
    failing = [
        f"{name}: {check['detail']}" + (f" (remedy: {check['remedy']})" if check.get("remedy") else "")
        for name, check in result["checks"].items()
        if check.get("level") == "fail"
    ]
    return (
        "`make verdict-ready`'s blocking checks fail, so the human unit ids the fold reads may not belong to a "
        f"corpus built from this tree: {'; '.join(failing)}"
    )


def _head_refusal(list_path: Path, root: Path) -> str | None:
    """Return why the working tree's list differs from `HEAD`'s, or None when the two are the same file or both absent."""
    relative = os.path.relpath(list_path, root)
    result = subprocess.run(
        ["git", "--no-optional-locks", "status", "--porcelain=v1", "--ignored", "--", relative],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return f"`git status` cannot compare {relative} with HEAD: {result.stderr.strip()}"
    if not result.stdout.strip():
        return None
    return (
        f"{relative} differs from HEAD's (`git status` reads `{result.stdout.splitlines()[0][:2]}`): commit the list "
        "the last close-out or re-key wrote, in its commit, before the next close-out, or put back HEAD's when "
        "neither wrote the change"
    )


@dataclass(frozen=True)
class Found:
    """What the close-out reads: the served corpus's stamp and human unit ids, the store's bytes and document (None when there is no store file), and the list beside it with its bytes."""

    stamp: str
    human: frozenset[str]
    raw: bytes | None
    document: dict | None
    listed: accepted.Accepted
    list_raw: bytes | None


def _read_store(store: Path, raw: bytes | None = None) -> tuple[bytes | None, dict | None]:
    try:
        raw = store.read_bytes() if raw is None else raw
    except FileNotFoundError:
        return None, None
    try:
        document = read_store_document(raw, f"the store {store}")
    except MalformedStore as malformed:
        raise Refused(
            f"{malformed}, which no later pass could carry; restore a readable store from its journal with "
            "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply` first"
        ) from None
    if document is None:
        raise Refused(
            f"{store} is not an {EXPORT_FORMAT} document; restore a readable store from its journal with "
            "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply` first"
        )
    return raw, document


def read_found(store: Path, corpus: Path, raw: bytes | None = None) -> Found:
    """Read the served corpus's manifest, the list and the store, or the store's bytes `raw` when given, raising `Refused` for a manifest that cannot be read, a malformed list or store, and a store and list that do not pair."""
    try:
        stamp, human = read_manifest(corpus)
    except (OSError, ValueError) as error:
        raise Refused(
            f"the served corpus's manifest {corpus / 'manifest.json'} cannot be read: {error}"
        ) from None
    list_path = accepted.path_for(store)
    try:
        list_raw = list_path.read_bytes()
    except FileNotFoundError:
        list_raw = None
    try:
        listed = (
            accepted.parse(list_raw, list_path)
            if list_raw is not None
            else accepted.Accepted.empty(list_path)
        )
    except accepted.Malformed as malformed:
        raise Refused(str(malformed)) from None
    raw, document = _read_store(store, raw)
    try:
        accepted.pair(document, listed, store=store)
    except accepted.Mismatch as mismatch:
        raise Refused(str(mismatch)) from None
    return Found(stamp, human, raw, document, listed, list_raw)


def closed_at_for(times: Iterable[str], now: str) -> str:
    """Return the close-out's time: `now` (`journal.now_stamp`'s form), or the latest of `times` cut to whole seconds when that is later. A stamp without fractional seconds sorts after every stamp with them in its second, so the result is at or after every one of `times` as readers compare them, as strings. A later time the cut cannot bound raises `Refused`."""
    latest = max(times, default=now)
    if latest <= now:
        return now
    match = _FOLDED_AT.fullmatch(latest)
    if match is None:
        raise Refused(
            f"a folded record is dated {latest!r}, later than now and not a UTC time in the form the review app "
            "or `journal.now_stamp` writes, so no close-out time in whole seconds bounds it; correct that "
            "record's `at` first"
        )
    return f"{match[1]}Z"


@dataclass(frozen=True)
class Fold:
    """What a close-out writes: the new list's verdicts and `closed_at`, the new store's records and tombstones, its counts, and whether there is anything to fold."""

    closed_at: str
    verdicts: dict[str, str]
    records: dict[str, dict]
    cleared: dict[str, str]
    counts: dict[str, int]
    folds: bool

    def list_bytes(self) -> bytes:
        try:
            return accepted.render(self.closed_at, self.verdicts)
        except ValueError as error:
            raise Refused(f"the new list cannot be written: {error}") from None


def fold(found: Found, now: str) -> Fold:
    """Return the close-out of `found` at `now` (the module docstring gives the rule)."""
    document, human, before = found.document, found.human, found.listed.verdicts
    records, cleared = (
        accepted.joined(
            journal.latest_by_unit(document["verdicts"]),
            journal.parse_cleared(document),
            accepted.Overrides.of(document),
        )
        if document is not None
        else ({}, {})
    )
    verdicts = {
        unit: verdict
        for unit, verdict in before.items()
        if unit in human and unit not in records and unit not in cleared
    }
    kept: dict[str, dict] = {}
    folded_at: list[str] = []
    counts: Counter[str] = Counter(
        dict.fromkeys(("accepting_records", "accepting_off_corpus", "note_bytes"), 0)
    )
    for unit, record in records.items():
        verdict = record["verdict"]
        if verdict not in accepted.ACCEPTING_VERDICTS:
            kept[unit] = _normalize(record)
            continue
        counts["accepting_records"] += 1
        counts["note_bytes"] += len(note_of(record).encode("utf-8"))
        if unit in human:
            verdicts[unit] = verdict
            folded_at.append(record["at"])
        else:
            counts["accepting_off_corpus"] += 1
    tombstones = {unit: at for unit, at in cleared.items() if unit in human}
    counts["records"] = len(records)
    counts["tombstones"] = len(cleared)
    counts["tombstones_kept"] = len(tombstones)
    counts["tombstones_dropped"] = len(cleared) - len(tombstones)
    counts["list_before"] = len(before)
    counts["list_after"] = len(verdicts)
    counts["added"] = sum(1 for unit in verdicts if unit not in before)
    counts["kept"] = sum(1 for unit in verdicts if unit in before)
    counts["remarked"] = sum(1 for unit, verdict in verdicts.items() if before.get(unit, verdict) != verdict)
    counts["taken_out"] = sum(1 for unit in before if unit in human and unit not in verdicts)
    counts["dropped_off_corpus"] = sum(1 for unit in before if unit not in human)
    counts["blank"] = sum(
        1 for unit in human if unit not in verdicts and (unit not in kept or kept[unit]["verdict"] == "skip")
    )
    for record in records.values():
        counts[f"verdict:{record['verdict']}"] += 1
    for record in kept.values():
        counts[f"kept:{record['verdict']}"] += 1
    for verdict in verdicts.values():
        counts[f"list:{verdict}"] += 1
    times = folded_at + ([found.listed.closed_at] if found.listed.closed_at is not None else [])
    return Fold(
        closed_at=closed_at_for(times, now),
        verdicts=verdicts,
        records=kept,
        cleared=tombstones,
        counts=dict(counts),
        folds=bool(counts["accepting_records"] or counts["dropped_off_corpus"]),
    )


def _stashes(store: Path) -> list[Path]:
    return sorted(path for path in store.parent.glob(STASH_GLOB) if path.resolve() != store.resolve())


def _other_verdicts_files(store: Path) -> list[Path]:
    stashes = set(_stashes(store))
    return sorted(
        path
        for path in store.parent.glob("verdicts-*.json")
        if path not in stashes and path.resolve() != store.resolve()
    )


def _archives(store: Path) -> list[Path]:
    try:
        return sorted(path for path in archive_root_for(store).iterdir() if path.is_dir())
    except FileNotFoundError:
        return []


def _journal_line(store: Path) -> str:
    path = journal.journal_path_for(store)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return "none"
    return f"{_rel(path, store.parent)} ({size:,} B, first event at {journal.first_event_at(path) or 'none'})"


def _names(paths: list[Path], root: Path) -> str:
    return ", ".join(_rel(path, root) for path in paths) if paths else "none"


def _by_verdict(counts: Mapping[str, int], prefix: str) -> str:
    named = sorted(
        (key.removeprefix(prefix), count) for key, count in counts.items() if key.startswith(prefix)
    )
    return ", ".join(f"{count:,} {verdict}" for verdict, count in named) or "none"


def _kept_records(counts: Mapping[str, int]) -> str:
    kept = _by_verdict(counts, "kept:")
    return "no records" if kept == "none" else f"{kept} records"


def report(store: Path, found: Found, folded: Fold) -> list[str]:
    root, counts = store.parent, folded.counts
    listed = found.listed
    kept_orphans = summarize_orphans(orphans_dir_for(store))
    before = (
        f"{counts['list_before']:,} ids closed at {listed.closed_at}"
        if listed.sha256 is not None
        else "none yet"
    )
    return [
        f"close-out of {store.name} on the served corpus stamped {found.stamp} ({len(found.human):,} human units)",
        f"  store: {counts['records']:,} records ({_by_verdict(counts, 'verdict:')}) and "
        f"{counts['tombstones']:,} tombstones",
        f"  list {_rel(listed.path, root)}: {before}",
        f"  new list: {counts['list_after']:,} ids ({_by_verdict(counts, 'list:')}), closed at {folded.closed_at}: "
        f"{counts['added']:,} added, {counts['kept']:,} kept ({counts['remarked']:,} re-marked), "
        f"{counts['taken_out']:,} taken out by an override or a clear, {counts['dropped_off_corpus']:,} dropped "
        "because they left the corpus's human units",
        f"  the store keeps {_kept_records(counts)} and {counts['tombstones_kept']:,} tombstones, and "
        f"drops {counts['tombstones_dropped']:,} tombstones and {counts['accepting_off_corpus']:,} accepting records "
        "off the corpus's human units",
        f"  blank human units after the fold: {counts['blank']:,}",
        f"  note bytes discarded, kept only in the archive: {counts['note_bytes']:,}",
        f"  orphan documents, which stay: {kept_orphans['orphan']['count']:,} orphans and "
        f"{kept_orphans['conflict']['count']:,} conflicts",
        f"  journal it replaces, kept nowhere: {_journal_line(store)}",
        f"  older close-out archives it deletes: {_names(_archives(store), root)}",
        f"  stashes it deletes: {_names(_stashes(store), root)}",
        f"  other root verdicts files, which stay: {_names(_other_verdicts_files(store), root)}",
        f"  progress files: {_names(sorted(root.glob(PROGRESS_GLOB)), root)}",
    ]


def _nothing_to_fold() -> str:
    return "nothing to fold: the store holds no accepting record and every listed id is on the corpus's human units, so nothing was written"


def dry_run(
    store: Path,
    corpus: Path,
    *,
    now: Callable[[], str],
    readiness: Callable[[], tuple[dict, bool]],
    serving: Callable[[Path], bool],
) -> int:
    root = store.parent
    if intent_path_for(store).exists():
        print(f"ERROR: {_intent_refusal(store)}. Nothing was written.")
        return 1
    refusals: list[str] = []
    if holder(pass_lock_path_for(store)) is not None:
        refusals.append(pass_refusal(store))
    if serving(store):
        refusals.append(_server_refusal(store))
    if land_intent_path_for(store).exists():
        print(
            f"note: a land a stopped pass began is left ({_rel(land_intent_path_for(store), root)}); --apply "
            "finishes it first, which may change the store this report reads"
        )
    refusals += [
        refusal
        for refusal in (_readiness_refusal(*readiness()), _head_refusal(accepted.path_for(store), root))
        if refusal is not None
    ]
    caught = caught_up_bytes(store, journal.journal_path_for(store), accepted.path_for(store))
    if caught is not None:
        print(
            f"note: {journal.JOURNAL_NAME} holds saves past the store's last write, which --apply writes into "
            "the store first, under its lock; this report counts them"
        )
    try:
        found = read_found(store, corpus, caught)
        folded = fold(found, now())
        print("\n".join(report(store, found, folded)))
        if not folded.folds:
            print(_nothing_to_fold())
    except Refused as refused:
        refusals.append(str(refused))
    for refusal in refusals:
        print(f"ERROR: --apply would refuse: {refusal}.")
    print("dry run: nothing written; `make close-out ARGS=--apply` writes the close-out")
    return 1 if refusals else 0


def _apply_locked(store: Path, corpus: Path, *, now: Callable[[], str], checked: str | None) -> int:
    root = store.parent
    list_path = accepted.path_for(store)
    refusal = _head_refusal(list_path, root)
    if refusal is not None:
        raise Refused(refusal)
    found = read_found(store, corpus)
    if found.stamp != checked:
        raise Refused(
            f"the served corpus is stamped {found.stamp}, not {checked} as when `make verdict-ready`'s checks ran, "
            "so a pass landed in between; run the close-out again"
        )
    folded = fold(found, now())
    print("\n".join(report(store, found, folded)))
    if not folded.folds:
        print(_nothing_to_fold())
        return 0
    list_bytes = folded.list_bytes()
    field = {"sha256": hashlib.sha256(list_bytes).hexdigest(), "closed_at": folded.closed_at}
    staging = staging_path_for(store)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    (staging / LIST_FILE).write_bytes(list_bytes)
    (staging / STORE_FILE).write_bytes(
        document_bytes(Store(found.stamp, folded.records, folded.cleared, field))
    )
    _write_archive(staging / ARCHIVE_NAME, found, folded, list_bytes)
    archive = _archive_target(store, folded.closed_at)
    intent = {
        "format": INTENT_FORMAT,
        "closed_at": folded.closed_at,
        "store": str(store),
        "list": str(list_path),
        "journal": str(journal.journal_path_for(store)),
        "staging": str(staging),
        "archive": str(archive),
        "found_sha256": hashlib.sha256(found.raw).hexdigest() if found.raw is not None else None,
        "list_sha256": hashlib.sha256(found.list_raw).hexdigest() if found.list_raw is not None else None,
        "field": field,
    }
    _write_atomic(intent_path_for(store), (json.dumps(intent, indent=1) + "\n").encode())
    stashes, archives = _complete(store, intent)
    print(
        f"wrote {_rel(list_path, root)} ({len(list_bytes):,} B), {store.name} and a new "
        f"{journal.JOURNAL_NAME} of one base event; archived the store as found and both lists under "
        f"{_rel(archive, root)}; deleted {stashes} stash(es) and {archives} older archive(s)"
    )
    print(
        "next: restart the review server (`make review-serve`), and commit "
        f"{_rel(list_path, root)} in the close-out commit"
    )
    return 0


def _archive_target(store: Path, closed_at: str) -> Path:
    name = closed_at.replace(":", ".")
    target, number = archive_root_for(store) / name, 1
    while target.exists():
        number += 1
        target = archive_root_for(store) / f"{name}-{number}"
    return target


def _write_archive(target: Path, found: Found, folded: Fold, list_bytes: bytes) -> None:
    target.mkdir(parents=True)
    if found.raw is not None:
        write_archive_member(target / ARCHIVE_STORE, found.raw)
    if found.list_raw is not None:
        write_archive_member(target / ARCHIVE_BEFORE, found.list_raw)
    write_archive_member(target / ARCHIVE_AFTER, list_bytes)
    counts = {"closed_at": folded.closed_at, "stamp": found.stamp, "counts": folded.counts}
    (target / ARCHIVE_COUNTS).write_text(
        json.dumps(counts, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def _journal_restarted(journal_path: Path, field: Mapping[str, str]) -> bool:
    try:
        with journal_path.open("rb") as handle:
            first = handle.readline()
    except FileNotFoundError:
        return False
    try:
        entry = json.loads(first)
    except ValueError:
        return False
    return (
        isinstance(entry, dict)
        and entry.get("kind") == "event"
        and entry.get("source") == SOURCE
        and entry.get("accepted") == field
    )


def _complete(store: Path, intent: Mapping) -> tuple[int, int]:
    """Do what is left of the close-out `intent` records, in order, skipping each step already done, and return how many stashes and older archives it deleted. The caller holds the store's lock."""
    staging, list_path, archive = Path(intent["staging"]), Path(intent["list"]), Path(intent["archive"])
    if (staging / ARCHIVE_NAME).exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / ARCHIVE_NAME, archive)
    if (staging / LIST_FILE).exists():
        list_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / LIST_FILE, list_path)
    if (staging / STORE_FILE).exists():
        os.replace(staging / STORE_FILE, store)
    _raw, document = _read_store(store)
    if document is None or document.get("accepted") != intent["field"]:
        before = archive / ARCHIVE_BEFORE
        raise Refused(
            f"the close-out at {intent['closed_at']} renamed its list over {list_path}, but its staged store is gone "
            f"and {store} does not name that list, so it cannot be finished: put back the list from before "
            f"(`gunzip -c {before} > {list_path}`, or delete {list_path} when there is no {before.name}), delete "
            f"{archive} and {intent_path_for(store)}, and close out again"
        )
    journal_path = Path(intent["journal"])
    if not _journal_restarted(journal_path, intent["field"]):
        tmp = journal_path.with_name(journal_path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        records, cleared = _joined(document)
        journal.record_transition(
            tmp,
            source=SOURCE,
            stamp=document["manifest_generated_at"],
            old_stamp=None,
            old_verdicts=[],
            new_verdicts=list(records.values()),
            new_cleared=cleared,
            accepted=document.get("accepted"),
        )
        os.replace(tmp, journal_path)
    journal.unjournaled_marker_for(store).unlink(missing_ok=True)
    journal.save_scan_state(scan_state_path_for(store), None)
    stashes = _stashes(store)
    for stash in stashes:
        stash.unlink(missing_ok=True)
    others = [path for path in _archives(store) if path != archive]
    for other in others:
        shutil.rmtree(other)
    shutil.rmtree(staging, ignore_errors=True)
    intent_path_for(store).unlink()
    return len(stashes), len(others)


def finish_interrupted(store: Path) -> str | None:
    """Finish or drop the close-out whose intent is left beside `store`, and return what was done, or None when there is no intent. The caller holds the store's lock, so the close-out that wrote the intent is dead. Whether it renamed the list is read from the list's sha256, not from what is left in the staging directory. One that had not, whose list is still the one it found and whose store does not yet name the list it wrote, is dropped: its staging directory, its archive, wherever it is, and the intent are deleted. One that had is finished (`_complete`), after it checks, when the store is still to be renamed, that the store is still the one it read; `_complete` refuses when the store names another list and no staged store is left to rename. A list that is neither the one it found nor the one it wrote raises `Refused`, and the intent stays."""
    path = intent_path_for(store)
    try:
        intent = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        raise Refused(
            f"{path} is not readable, so the close-out it records can be neither finished nor dropped; compare "
            "the list and the store with the latest archive under var/keep/close-outs/ and delete the intent by hand"
        ) from None
    staging, archive, list_path = Path(intent["staging"]), Path(intent["archive"]), Path(intent["list"])
    closed_at = intent["closed_at"]
    live = _sha256(list_path)
    if live != intent["field"]["sha256"]:
        _raw, document = _read_store(store)
        if live == intent["list_sha256"] and (
            document is None or document.get("accepted") != intent["field"]
        ):
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(archive, ignore_errors=True)
            path.unlink()
            return f"dropped the close-out at {closed_at}, which had not written {list_path.name}; run the close-out again"
        held = archive if archive.exists() else staging / ARCHIVE_NAME
        raise Refused(
            f"the close-out at {closed_at} found one list at {list_path} and wrote another, and the list is now "
            f"neither, or is the one it found beside a store that names the one it wrote, so it can be neither "
            f"finished nor dropped: put back the list it wrote (`gunzip -c {held / ARCHIVE_AFTER} > {list_path}`) "
            "and run `make close-out ARGS=--apply` again"
        )
    if (staging / STORE_FILE).exists() and _sha256(store) != intent["found_sha256"]:
        raise Refused(
            f"the close-out at {closed_at} wrote {list_path} but not the store, and the store has changed since it "
            f"read it, so finishing it would lose those changes: put back the list from before "
            f"({archive / ARCHIVE_BEFORE}, or none when the archive holds none), delete {staging} and {path}, and "
            "close out again"
        )
    _complete(store, intent)
    return f"finished the close-out at {closed_at}; nothing more was folded, so run the close-out again for any verdicts since"


def _joined(document: dict | None) -> tuple[dict[str, dict], dict[str, str]]:
    """Return a store document's own records and tombstones and its override section's as one store (`accepted.joined`), the records normalized as the store writes them; none for no document."""
    if document is None:
        return {}, {}
    records = {
        unit: _normalize(record) for unit, record in journal.latest_by_unit(document["verdicts"]).items()
    }
    section = accepted.Overrides.of(document)
    overrides = accepted.Overrides(
        {unit: _normalize(record) for unit, record in section.records.items()}, section.cleared
    )
    return accepted.joined(records, journal.parse_cleared(document), overrides)


def _pairs(document: dict | None, listed: accepted.Accepted, store: Path) -> bool:
    try:
        accepted.pair(document, listed, store=store)
    except accepted.Mismatch:
        return False
    return True


def _undo_locked(store: Path) -> int:
    root = store.parent
    if intent_path_for(store).exists():
        raise Refused(_intent_refusal(store) + ", and the undo waits for that")
    archive_path = latest_archive(store)
    if archive_path is None:
        raise Refused(f"there is no close-out archive under {_rel(archive_root_for(store), root)} to undo")
    archive = read_archive(archive_path)
    list_path = accepted.path_for(store)
    try:
        list_raw = list_path.read_bytes()
    except FileNotFoundError:
        list_raw = None
    if list_raw != archive.after and list_raw != archive.before:
        raise Refused(
            f"{_rel(list_path, root)} is neither the list the close-out archived at {archive_path.name} wrote nor the "
            "one it replaced, so an undo would put back a list the store was not folded against: put back the list "
            f"that close-out wrote (`gunzip -c {archive_path / ARCHIVE_AFTER} > {list_path}`) and undo again"
        )
    before = (
        accepted.parse(archive.before, list_path)
        if archive.before is not None
        else accepted.Accepted.empty(list_path)
    )
    after = accepted.parse(archive.after, list_path)
    _raw, live = _read_store(store)
    if not (_pairs(live, after, store) or _pairs(live, before, store)):
        raise Refused(
            f"{store.name} names neither the list the close-out archived at {archive_path.name} wrote nor the one "
            "it replaced, so it was not written by that close-out or its undo; restore the store from its journal "
            "with `uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply` first"
        )
    archived = None
    if archive.store is not None:
        try:
            archived = read_store_document(
                archive.store, f"the archived store {archive_path / ARCHIVE_STORE}"
            )
        except MalformedStore as malformed:
            raise Refused(str(malformed)) from None
        if archived is None:
            raise Refused(f"{archive_path / ARCHIVE_STORE} is not an {EXPORT_FORMAT} document")
    live_records, live_cleared = _joined(live)
    records = dict(live_records)
    cleared = dict(live_cleared)
    if archived is not None:
        archived_records, archived_cleared = _joined(archived)
        for unit, at in archived_cleared.items():
            if unit not in records and at > cleared.get(unit, ""):
                cleared[unit] = at
        merge_into(records, list(archived_records.values()), cleared)
    source = live if live is not None else archived
    if source is None:
        raise Refused(
            f"neither {store.name} nor the archive at {archive_path.name} holds a store to put back"
        )
    stamp = source["manifest_generated_at"]
    field = before.field()
    done = (
        list_raw == archive.before
        and live is not None
        and live.get("accepted") == field
        and records == live_records
        and cleared == live_cleared
    )
    if done:
        print(
            f"nothing to undo: {store.name} and {_rel(list_path, root)} are already as the undo leaves them"
        )
        return 0
    journal.mark_unjournaled(store, "close_out --undo")
    if archive.before is not None:
        _write_atomic(list_path, archive.before)
    else:
        list_path.unlink(missing_ok=True)
    own, tombstones, overrides = accepted.split(records, cleared, before.verdicts)
    _write_atomic(store, document_bytes(Store(stamp, own, tombstones, field, overrides)))
    journal.record_transition(
        journal.journal_path_for(store),
        source=UNDO_SOURCE,
        stamp=stamp,
        old_stamp=None,
        old_verdicts=[],
        new_verdicts=list(records.values()),
        new_cleared=cleared,
        accepted=field,
    )
    journal.unjournaled_marker_for(store).unlink(missing_ok=True)
    restored = (
        f"put back {_rel(list_path, root)}"
        if archive.before is not None
        else f"deleted {_rel(list_path, root)}"
    )
    print(
        f"{restored} as it was before the close-out archived at {archive_path.name}, and wrote {store.name} with "
        f"{len(records):,} records and {len(cleared):,} tombstones: the archived store with the {len(live_records):,} "
        f"records and {len(live_cleared):,} tombstones of the store laid over it; appended a base event of it to "
        f"{journal.JOURNAL_NAME}"
    )
    print(
        f"next: commit the change to {_rel(list_path, root)} (when the close-out's commit was never made, the tree "
        "already matches HEAD), never revert it, since the store now names that list; and restart the review server"
    )
    return 0


def run(
    *,
    store: Path,
    corpus: Path,
    apply: bool = False,
    undo: bool = False,
    now: Callable[[], str] = journal.now_stamp,
    readiness: Callable[[], tuple[dict, bool]] | None = None,
    serving: Callable[[Path], bool] = serves,
) -> int:
    """Run the close-out of `store` against the served corpus at `corpus`: the dry run, or with `apply` the close-out, or with `undo` its undo. `now` gives the current time, `readiness` `make verdict-ready`'s answer without the server row, and `serving` whether a review server may write the store."""
    if readiness is None:
        readiness = functools.partial(
            verdict_ready.readiness,
            with_server=False,
            repo_root=store.parent,
            review_dir=corpus,
            autosave_path=store,
        )
    if not apply and not undo:
        return dry_run(store, corpus, now=now, readiness=readiness, serving=serving)
    checked = None
    try:
        if apply and not intent_path_for(store).exists():
            result, ready = readiness()
            refusal = _readiness_refusal(result, ready)
            if refusal is not None:
                raise Refused(refusal)
            checked = result["corpus"]["generated_at"]
        with pass_lock(store):
            if serving(store):
                raise Refused(_server_refusal(store))
            with locked_store(store):
                if undo:
                    return _undo_locked(store)
                finished = finish_interrupted(store)
                if finished is not None:
                    print(finished)
                    return 0
                return _apply_locked(store, corpus, now=now, checked=checked)
    except LockBusy:
        message = pass_refusal(store)
    except (LandUnrecoverable, Refused) as refused:
        message = str(refused)
    print(f"ERROR: {message}. Nothing was written.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fold the verdict store's accepted verdicts into rebuild/accepted-units.txt and start the store and its journal again from what is left; without --apply or --undo, a dry run."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the close-out")
    mode.add_argument(
        "--undo",
        action="store_true",
        help="put back the latest close-out archive's list and its store, with the verdicts made since laid over it",
    )
    parser.add_argument(
        "--autosave",
        type=Path,
        default=ROOT / journal.AUTOSAVE_NAME,
        help=f"the store to close out, named {journal.AUTOSAVE_NAME}; its list, journal, archive, intent and pass lock sit beside it (default: %(default)s)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        help="the served corpus whose human unit ids the fold reads (default: rebuild/out/review beside --autosave)",
    )
    args = parser.parse_args(argv)
    store = args.autosave.absolute()
    if store.name != journal.AUTOSAVE_NAME:
        parser.error(
            f"--autosave {store} is not named {journal.AUTOSAVE_NAME}, and the close-out finds the list, the journal "
            "and its archive beside a store of that name"
        )
    corpus = (args.corpus or store.parent / "rebuild" / "out" / "review").absolute()
    return run(store=store, corpus=corpus, apply=args.apply, undo=args.undo)


if __name__ == "__main__":
    sys.exit(main())
