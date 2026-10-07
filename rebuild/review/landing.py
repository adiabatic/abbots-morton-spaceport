"""Land a pass's corpus and verdict store together, beside a running review server.

A corpus-changing pass builds its corpus beside the served one, at rebuild/out/review.next (a copy-on-write clone of the served tree, `clone_tree`), or promotes a staged one, and runs the verdict update against scratch copies of the store: `snapshot` copies the live store to the pass's run directory as the snapshot S and the prepared store R, under the store's lock, and the verdict update carries, merges and fills into R. `land` then moves both into place in one short section under the store's lock (`rebuild.review.store_lock`), which every writer of the store and its journal holds, the review server included, so a save is never applied between the two moves. A pass whose corpus did not move but whose store did lands the store alone.

The land first reads S, R and the new corpus's stamp and human unit ids without the lock, and, when the stamp moves, the journal's events, resuming from the scan state retention saved (`--scan-state`). Under it, it reads the live store C and aborts, keeping the staged tree, when C is on another stamp than S. Every unit whose record in C differs from S's, or that C cleared since S, is the reviewer's act after the snapshot, so it is laid over R and beats any fill. On a stamp change an overlaid set on a unit the new corpus does not have goes to the orphan document of the stamp it was made on (`verdict_store.append_orphans`), and an overlaid skip is dropped, as the carry drops every skip, so the unit is asked again: the result keeps no record for its unit, only a tombstone at the skip's `at`, so neither the verdict the skip replaced nor an older save sent later comes back. C's tombstones for units the result does not hold are kept (on a stamp change, for the units the new corpus has), and a tombstone older than `--tombstone-cutoff` is dropped. The result is written to the run directory, and the intent file (`intent_path_for`, var/cycle/land.json beside the store) records what the rest of the section will do. Then the corpus is swapped in with one `renamex_np(RENAME_SWAP)` call (`exchange_dirs`, with a three-rename fallback through `<live>.superseded`), the live store is linked to its stash name on a stamp change (`link_stash`), the result is renamed over the store, the change is appended to the journal, and the intent file is deleted. A stamp change is journaled as an event naming the stash: a base event holding the result when the journal is due one (`journal.base_due`, read from the journal's events, re-read under the lock from where the first read stopped), and otherwise the sets and clears from the store it replaced to the result. A land that keeps the stamp journals the sets and clears. Stop signals are blocked for the section. After the lock is released the swapped-out tree is renamed to a discard name beside the served corpus (`move_to_discard`) and deleted, or with `--keep-discard` left for the caller, which tells the open tabs to move before it deletes the tree; under the lock a tree is only ever renamed, never deleted.

A land killed inside the section leaves the intent file, and the kernel releases the lock. The next holder of the lock finishes it first (`recover_interrupted_land`, and `locked_store` for the writers that take the lock): it cuts the journal back to the length the intent recorded, then reads where the land stopped. A corpus not yet swapped drops the intent, and the staged tree stays for the next pass. A swapped corpus with the old store finishes the land from the result it wrote, or is swapped back out when that result is gone, and a store already replaced gets its journal event. The review server runs the recovery on its next request (`recover_if_orphaned`), so nobody runs a command.

`LAND_PROTOCOL` and `code_digest` are what the review server advertises on /capabilities, and what the artifact cycle compares with the working tree before it keeps a listening server running through a pass. This module imports only the journal, the store and its lock, never the cycle driver.

Usage: uv run python -m rebuild.review.landing --autosave FILE --journal FILE --run-dir DIR --corpus DIR [--prepared FILE] [--staged DIR --live DIR [--keep-discard]] [--tombstone-cutoff ISO] [--scan-state FILE]
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rebuild.review import journal
from rebuild.review.store_lock import LockBusy, store_lock
from rebuild.review.verdict_store import (
    EXPORT_FORMAT,
    append_orphans,
    orphans_dir_for,
    parse_autosave_payload,
    parse_cleared,
    stash_path_for,
)

LAND_PROTOCOL = 1
PROTOCOL_MODULES = (
    "serve.py",
    "verdict_store.py",
    "journal.py",
    "status.py",
    "store_lock.py",
    "landing.py",
)
SNAPSHOT_NAME = "verdicts-autosave.json"
PREPARED_NAME = "prepared.json"
LANDING_NAME = "landing.json"
LANDED_NAME = "landed.json"
REPORT_NAME = "land-report.json"
RENAME_SWAP = 0x2
RENAME_EXCHANGE = 0x2
AT_FDCWD = -100
UNSUPPORTED_SWAP_ERRNOS = frozenset({errno.ENOTSUP, errno.EINVAL, errno.ENOSYS})
BLOCKED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def code_digest(review_dir: Path) -> str:
    """Return the digest of the modules whose behavior the land protocol depends on (`PROTOCOL_MODULES` under `review_dir`). The server takes it at boot from the files it loaded, and the cycle from the working tree, so a server whose code differs from the tree's is never trusted to run through a pass."""
    digest = hashlib.sha256()
    for name in PROTOCOL_MODULES:
        path = Path(review_dir) / name
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        digest.update(f"{name}\t{hashlib.sha256(data).hexdigest()}\n".encode())
    return digest.hexdigest()


def intent_path_for(store: Path) -> Path:
    """Return the intent file of the store at `store`: var/cycle/land.json beside it, which for the live store is under the repo root's gitignored var/."""
    return Path(store).parent / "var" / "cycle" / "land.json"


@dataclass
class Store:
    """A verdicts document as the land reads it: its stamp, its records by unit, and its tombstones (unit to the clear's `at`)."""

    stamp: str | None
    records: dict[str, dict] = field(default_factory=dict)
    cleared: dict[str, str] = field(default_factory=dict)


def _normalize(record: dict) -> dict:
    return {
        "unit": record["unit"],
        "verdict": record.get("verdict"),
        "note": record.get("note") or "",
        "at": record.get("at") or "",
    }


def _signature(record: dict | None) -> tuple | None:
    if record is None:
        return None
    return (record.get("verdict"), record.get("note") or "", record.get("at") or "")


def read_store(path: Path) -> Store | None:
    """Return the verdicts document at `path`, or None when it is missing or is not an ams-review-verdicts/1 document."""
    try:
        data = parse_autosave_payload(Path(path).read_bytes())
    except OSError:
        return None
    if data is None:
        return None
    records = {unit: _normalize(record) for unit, record in journal.latest_by_unit(data["verdicts"]).items()}
    cleared = {unit: at for unit, at in parse_cleared(data).items() if unit not in records}
    return Store(data["manifest_generated_at"], records, cleared)


def document_bytes(store: Store) -> bytes:
    """Return `store` as the review server writes a store: one ams-review-verdicts/1 document with one record per line, and the tombstones after the records when there are any."""
    head = {"format": EXPORT_FORMAT, "manifest_generated_at": store.stamp, "exported_at": journal.now_stamp()}
    prefix = json.dumps(head, ensure_ascii=False)[:-1]
    body = ",\n".join(json.dumps(store.records[unit], ensure_ascii=False) for unit in sorted(store.records))
    tail = ""
    if store.cleared:
        cleared = [{"unit": unit, "at": at} for unit, at in sorted(store.cleared.items())]
        tail = f', "cleared": {json.dumps(cleared, ensure_ascii=False)}'
    return f'{prefix}, "verdicts": [\n{body}\n]{tail}}}\n'.encode()


def _write_file(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def read_manifest(corpus: Path) -> tuple[str, frozenset[str]]:
    """Return the corpus's `generated_at` and human unit ids, from its manifest's `human_unit_ids`."""
    manifest = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    stamp = manifest.get("generated_at")
    ids = manifest.get("human_unit_ids")
    if not isinstance(stamp, str) or not isinstance(ids, list):
        raise ValueError(f"{corpus}/manifest.json names no generated_at or human_unit_ids")
    return stamp, frozenset(unit for unit in ids if isinstance(unit, str))


def snapshot(autosave: Path, run_dir: Path) -> str | None:
    """Copy the live store to `run_dir` as the snapshot and the prepared store, under the store's lock, and return the snapshot's stamp. A missing store writes neither file and returns None. The snapshot keeps the store's file name (`SNAPSHOT_NAME`), because the carry writes the name of the file each verdict came from into the verdict's note, so a verdict carried from the snapshot reads as carried from the store."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    with locked_store(autosave, quiet=True):
        try:
            shutil.copyfile(autosave, run_dir / SNAPSHOT_NAME)
        except FileNotFoundError:
            return None
        shutil.copyfile(run_dir / SNAPSHOT_NAME, run_dir / PREPARED_NAME)
    taken = read_store(run_dir / SNAPSHOT_NAME)
    return taken.stamp if taken is not None else None


def clone_tree(source: Path, target: Path) -> str:
    """Copy the tree at `source` to `target`, which must not exist, and return how: `clone` for an APFS copy-on-write clone (`cp -c -R`), whose blocks the two trees share until one is written, and `copy` for a plain copy where cloning is not available."""
    source, target = Path(source), Path(target)
    if sys.platform == "darwin":
        result = subprocess.run(
            ["cp", "-c", "-R", str(source), str(target)], capture_output=True, check=False
        )
        if result.returncode == 0:
            return "clone"
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, symlinks=True)
    return "copy"


def _swap_call(a: Path, b: Path) -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renamex_np"):
        call = libc.renamex_np
        call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        call.restype = ctypes.c_int
        result = call(os.fsencode(a), os.fsencode(b), RENAME_SWAP)
    elif hasattr(libc, "renameat2"):
        call = libc.renameat2
        call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        call.restype = ctypes.c_int
        result = call(AT_FDCWD, os.fsencode(a), AT_FDCWD, os.fsencode(b), RENAME_EXCHANGE)
    else:
        return False
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(a))
    return True


def exchange_dirs(staged: Path, live: Path, *, atomic: bool = True) -> None:
    """Swap the trees at `staged` and `live`, so the live path holds the staged tree and the staged path the old one. One atomic exchange call where the platform has one and the filesystem accepts it; otherwise (no such call, a filesystem that answers it with one of `UNSUPPORTED_SWAP_ERRNOS`, or `atomic` false) three renames through `<live>.superseded`, which `recover_interrupted_land` and the cycle's `recover_superseded_corpus` both finish when a pass dies between them. A leftover `.superseded` beside a live tree is renamed aside to a discard name (`move_to_discard`), never deleted here, because this runs under the store's lock."""
    staged, live = Path(staged), Path(live)
    if atomic:
        try:
            if _swap_call(staged, live):
                return
        except OSError as error:
            if error.errno not in UNSUPPORTED_SWAP_ERRNOS:
                raise
    superseded = live.with_name(f"{live.name}.superseded")
    if superseded.exists():
        move_to_discard(superseded, live)
    os.replace(live, superseded)
    try:
        os.replace(staged, live)
    except OSError:
        os.replace(superseded, live)
        raise
    os.replace(superseded, staged)


def discard_path_for(live: Path) -> Path:
    """Return where a swapped-out tree waits to be deleted: `<live>.discard` beside the served corpus, or `<live>.discard-<n>` while a leftover holds that name (`discard_paths`). A delete cut short leaves the tree under that name, which no pass takes for a corpus it could land or seed from, and the next pass deletes it."""
    live = Path(live)
    return live.with_name(f"{live.name}.discard")


def discard_paths(live: Path) -> list[Path]:
    """Return every tree waiting to be deleted beside the served corpus `live`: `discard_path_for(live)` and its numbered siblings."""
    live = Path(live)
    return sorted(live.parent.glob(f"{live.name}.discard*"))


def move_to_discard(tree: Path, live: Path) -> Path:
    """Rename `tree` to the first free discard name beside `live` (`discard_path_for`, then `<live>.discard-1`, `-2`, …) and return the path to delete. It only renames, so a caller holding the store's lock never deletes a whole corpus inside it. A tree on another filesystem, which cannot be renamed there, is returned where it is."""
    discard = discard_path_for(live)
    number = 0
    while discard.exists():
        number += 1
        discard = discard.with_name(f"{Path(live).name}.discard-{number}")
    try:
        os.replace(tree, discard)
    except OSError:
        return Path(tree)
    return discard


def link_stash(store: Path, stash: Path) -> None:
    """Give the live store's file a second name, `stash`, before the store is replaced, so the old store stays on disk under the stash name without a copy. A stash that already is the store's file is left alone, so a recovery that repeats the step changes nothing, and any other file at the stash name is replaced, as a stash that moves the store aside does."""
    store, stash = Path(store), Path(stash)
    try:
        if stash.stat().st_ino == store.stat().st_ino:
            return
    except FileNotFoundError:
        pass
    tmp = stash.with_name(stash.name + ".link")
    tmp.unlink(missing_ok=True)
    os.link(store, tmp)
    os.replace(tmp, stash)


def write_orphans(store: Path, stamp: str, entries: list[dict]) -> None:
    if entries:
        append_orphans(orphans_dir_for(store), stamp, entries)


@contextlib.contextmanager
def _signals_blocked() -> Iterator[None]:
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, BLOCKED_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@dataclass
class LandResult:
    """What a land did. `landed` is False when it aborted, and `reason` says why."""

    landed: bool
    reason: str = ""
    old_stamp: str | None = None
    new_stamp: str | None = None
    records: int = 0
    overlaid: int = 0
    orphaned: int = 0
    skips_dropped: int = 0
    stash: str | None = None
    discard: str | None = None
    locked_s: float = 0.0

    def as_dict(self) -> dict:
        return {**asdict(self), "locked_s": round(self.locked_s, 3)}


def overlay(
    result: Store, before: Store, now: Store, *, new_ids: frozenset[str] | None, cutoff: str | None
) -> tuple[int, list[dict], int]:
    """Lay the live store's changes since the snapshot (`now` against `before`) over `result` in place, and keep `now`'s tombstones for units `result` does not hold. `new_ids` is the new corpus's human unit ids on a stamp change, and None on a land that keeps the stamp; on a stamp change a skip made since the snapshot clears its unit in `result`, leaving a tombstone at the skip's `at`. Returns the count of units overlaid, the orphan entries, and the count of skips dropped."""
    overlaid = 0
    skips = 0
    orphans: list[dict] = []
    for unit in sorted(set(before.records) | set(now.records)):
        record = now.records.get(unit)
        if _signature(record) == _signature(before.records.get(unit)):
            continue
        if record is None:
            if new_ids is not None and unit not in new_ids:
                continue
            overlaid += 1
            result.records.pop(unit, None)
            result.cleared[unit] = now.cleared.get(unit) or journal.now_stamp()
            continue
        if new_ids is not None and unit not in new_ids:
            orphans.append({**record, "reason": "orphan"})
            continue
        if new_ids is not None and record["verdict"] == "skip":
            skips += 1
            result.records.pop(unit, None)
            result.cleared[unit] = record["at"] or journal.now_stamp()
            continue
        overlaid += 1
        result.records[unit] = record
        result.cleared.pop(unit, None)
    for unit, at in now.cleared.items():
        if unit in result.records or unit in result.cleared:
            continue
        if new_ids is not None and unit not in new_ids:
            continue
        result.cleared[unit] = at
    if cutoff is not None:
        result.cleared = {unit: at for unit, at in result.cleared.items() if at >= cutoff}
    return overlaid, orphans, skips


def land(
    *,
    autosave: Path,
    journal_path: Path,
    run_dir: Path,
    corpus: Path,
    prepared: Path | None = None,
    staged: Path | None = None,
    live: Path | None = None,
    tombstone_cutoff: str | None = None,
    atomic: bool = True,
    keep_discard: bool = False,
    scan_state: Path | None = None,
) -> LandResult:
    """Land the prepared store, and the staged corpus when `staged` is given, as the module docstring describes. `corpus` is the directory whose manifest names the landed stamp and human unit ids: the staged tree, or the live one for a store-only land. With no `prepared` store, or none on disk, the land writes an empty store stamped for that corpus. The swapped-out tree is renamed to its discard name after the lock is released and deleted, or with `keep_discard` left for the caller to delete, its path in the result's `discard`, so the caller can tell the open tabs to move first. `scan_state` is the journal scan state retention saves (`journal.save_scan_state`); a land that moves the stamp resumes its read of the journal's events from it, and reads them from the start without it. The land only reads it."""
    autosave, journal_path, run_dir = Path(autosave), Path(journal_path), Path(run_dir)
    new_stamp, new_ids = read_manifest(corpus)
    before = read_store(run_dir / SNAPSHOT_NAME)
    ready = read_store(prepared) if prepared is not None else None
    if ready is None:
        ready = Store(new_stamp)
    if ready.stamp != new_stamp:
        return LandResult(
            False, f"the prepared store is stamped {ready.stamp}, not {new_stamp}, the corpus it lands with"
        )
    if staged is not None and live is None:
        raise ValueError("a staged corpus needs the live path it replaces")
    journal_scan = None
    if before is None or before.stamp != new_stamp:
        resume = journal.load_scan_state(scan_state) if scan_state is not None else None
        journal_scan = journal.scan(journal_path, resume=resume).state

    with locked_store(autosave, quiet=True), _signals_blocked():
        started = time.perf_counter()
        result = _land_locked(
            autosave=autosave,
            journal_path=journal_path,
            run_dir=run_dir,
            before=before,
            ready=ready,
            new_stamp=new_stamp,
            new_ids=new_ids,
            staged=staged,
            live=live,
            tombstone_cutoff=tombstone_cutoff,
            atomic=atomic,
            journal_scan=journal_scan,
        )
        result.locked_s = time.perf_counter() - started
    if result.landed and staged is not None and live is not None:
        discard = move_to_discard(staged, live)
        if keep_discard:
            result.discard = str(discard)
        else:
            shutil.rmtree(discard, ignore_errors=True)
    return result


def _land_locked(
    *,
    autosave: Path,
    journal_path: Path,
    run_dir: Path,
    before: Store | None,
    ready: Store,
    new_stamp: str,
    new_ids: frozenset[str],
    staged: Path | None,
    live: Path | None,
    tombstone_cutoff: str | None,
    atomic: bool,
    journal_scan: journal.ScanState | None,
) -> LandResult:
    now = read_store(autosave)
    if before is None:
        before = Store(now.stamp if now is not None else None)
    if now is None and before.stamp is not None:
        return LandResult(False, "the live store vanished since the snapshot")
    if now is None:
        now = Store(None)
    if now.stamp != before.stamp:
        return LandResult(
            False,
            f"the live store moved from {before.stamp} to {now.stamp} since the snapshot",
            old_stamp=now.stamp,
            new_stamp=new_stamp,
        )
    old_stamp = now.stamp
    moved = old_stamp != new_stamp
    overlaid, orphans, skips = overlay(
        ready, before, now, new_ids=new_ids if moved else None, cutoff=tombstone_cutoff
    )
    landing = run_dir / LANDING_NAME
    _write_file(landing, document_bytes(ready))
    if old_stamp is not None:
        write_orphans(autosave, old_stamp, orphans)
    stash = stash_path_for(autosave, old_stamp) if moved and old_stamp is not None else None
    journal.repair_tail(journal_path)
    journal_stat = _stat(journal_path)
    at = journal.now_stamp()
    base = None
    if moved and old_stamp is not None:
        base = journal.base_due(journal.scan(journal_path, resume=journal_scan).events, at)
    intent = {
        "run_dir": str(run_dir),
        "landing": str(landing),
        "staged": str(staged) if staged is not None else None,
        "live": str(live) if live is not None else None,
        "staged_inode": staged.stat().st_ino if staged is not None else None,
        "old_stamp": old_stamp,
        "new_stamp": new_stamp,
        "stash": stash.name if stash is not None else None,
        "journal": str(journal_path),
        "journal_inode": journal_stat.st_ino if journal_stat is not None else None,
        "journal_length": journal_stat.st_size if journal_stat is not None else None,
    }
    intent_path = intent_path_for(autosave)
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    _write_file(intent_path, (json.dumps(intent, indent=1) + "\n").encode())
    if staged is not None:
        assert live is not None
        exchange_dirs(staged, live, atomic=atomic)
    if stash is not None and autosave.exists():
        link_stash(autosave, stash)
    with contextlib.suppress(OSError):
        os.link(landing, run_dir / LANDED_NAME)
    os.replace(landing, autosave)
    journal.record_transition(
        journal_path,
        source="land",
        stamp=new_stamp,
        old_stamp=old_stamp,
        old_verdicts=list(now.records.values()),
        new_verdicts=list(ready.records.values()),
        stashed=stash.name if stash is not None else None,
        at=at,
        base=base,
    )
    intent_path.unlink()
    return LandResult(
        True,
        old_stamp=old_stamp,
        new_stamp=new_stamp,
        records=len(ready.records),
        overlaid=overlaid,
        orphaned=len(orphans),
        skips_dropped=skips,
        stash=stash.name if stash is not None else None,
    )


def _stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


@dataclass
class Recovery:
    """What `recover_interrupted_land` did: `message` for the log, and `discard`, the swapped-out tree, already renamed to a discard name (`move_to_discard`), that the caller deletes once it has released the lock."""

    message: str
    discard: Path | None = None


class LandUnrecoverable(RuntimeError):
    """Raised by `recover_interrupted_land` when a land swapped the corpus in but neither its result nor the corpus it swapped out is left, so neither finishing it nor undoing it keeps the store on the served corpus. The intent file stays, and every writer refuses until a person restores one of them."""


def recover_interrupted_land(store: Path) -> Recovery | None:
    """Finish or drop the land the intent file beside `store` records, and return what was done, or None when there is no intent. The caller holds the store's lock, so the land that wrote the intent is dead.

    The journal is cut back to the length the intent recorded when it is still the same file, so whatever the land had appended, whole or in part, is removed. Then: a staged corpus that was not swapped in means the land never started to move anything, and the intent is dropped. Otherwise the land's result either still waits in its run directory, and it is moved over the store (after the store is linked to its stash name on a stamp change), or the store already holds it. Either way the store's content is appended to the journal as a base event naming the stash, and the intent is deleted. A swapped corpus whose result is gone while the store is still on another stamp than the one the land moved to (the run directory was deleted by hand) is swapped back out, so the store stays on the served corpus, and the intent is dropped; when the swapped-out corpus is gone too, `LandUnrecoverable` is raised and the intent stays.
    """
    store = Path(store)
    intent_path = intent_path_for(store)
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        intent_path.unlink(missing_ok=True)
        return Recovery("dropped an unreadable land intent")
    journal_path = Path(intent["journal"])
    journal.truncate_to(journal_path, intent.get("journal_length"), intent.get("journal_inode"))
    landing = Path(intent["landing"])
    run = Path(intent["run_dir"]).name
    discard = None
    staged = Path(intent["staged"]) if intent.get("staged") else None
    if staged is not None:
        live = Path(intent["live"])
        superseded = live.with_name(f"{live.name}.superseded")
        if not live.exists() and superseded.exists():
            os.replace(superseded, live)
        live_stat = _stat(live)
        swapped = live_stat is not None and live_stat.st_ino == intent.get("staged_inode")
        if swapped and superseded.exists() and not staged.exists():
            os.replace(superseded, staged)
        if not swapped:
            intent_path.unlink()
            return Recovery(f"dropped the land of {run}, which had not swapped the corpus in")
        current = read_store(store) if not landing.exists() else None
        if current is not None and current.stamp != intent.get("new_stamp"):
            if not staged.exists():
                raise LandUnrecoverable(
                    f"the land of {run} swapped {staged} in for {live}, but its result ({landing}) and the corpus it swapped out are both gone, and {store} is still on {current.stamp}; {intent_path} stays until one of them is restored"
                )
            exchange_dirs(staged, live)
            intent_path.unlink()
            return Recovery(
                f"swapped the corpus back out: the land of {run} had swapped it in, but its result is gone and the store is still on {current.stamp}; {staged} keeps the new corpus"
            )
        discard = move_to_discard(staged, live) if staged.exists() else None
    if landing.exists():
        if intent.get("stash") and store.exists():
            link_stash(store, store.with_name(intent["stash"]))
        os.replace(landing, store)
    landed = read_store(store)
    if landed is not None and landed.stamp is not None:
        journal.record_transition(
            journal_path,
            source="land",
            stamp=landed.stamp,
            old_stamp=None,
            old_verdicts=[],
            new_verdicts=list(landed.records.values()),
            stashed=intent.get("stash"),
        )
    intent_path.unlink()
    return Recovery(f"finished the land of {run}", discard)


@contextlib.contextmanager
def _recovered_lock(store: Path, **lock_options) -> Iterator[tuple[int, Recovery | None]]:
    recovery = None
    with store_lock(store, **lock_options) as fd:
        recovery = recover_interrupted_land(store)
        yield fd, recovery
    if recovery is not None and recovery.discard is not None:
        shutil.rmtree(recovery.discard, ignore_errors=True)


@contextlib.contextmanager
def locked_store(store: Path, **lock_options) -> Iterator[int]:
    """Hold the verdict store's lock (`store_lock.store_lock`, with its options), after finishing any land a dead holder left (`recover_interrupted_land`). Every writer of the store and its journal takes the lock this way, so none writes over a land that is half done. The swapped-out tree a recovery leaves is deleted after the lock is released."""
    with _recovered_lock(store, **lock_options) as (fd, _recovery):
        yield fd


def finish_interrupted_land(store: Path, **lock_options) -> Recovery | None:
    """Take the verdict store's lock as `locked_store` does, finish or drop any land a dead holder left, release the lock, delete the swapped-out tree the recovery left, and return what was done (`Recovery`), or None when no land was left."""
    with _recovered_lock(store, **lock_options) as (_fd, recovery):
        return recovery


def recover_if_orphaned(store: Path) -> bool:
    """Finish a land whose holder died, for the review server: with no intent file, or with the lock free, return True once nothing is left to finish; while the lock is busy, return False, because a land is still running."""
    if not intent_path_for(store).exists():
        return True
    try:
        with locked_store(store, blocking=False):
            pass
    except LockBusy:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Land a pass's corpus and verdict store beside the review server."
    )
    parser.add_argument("--autosave", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--corpus", type=Path, required=True, help="the corpus whose stamp and human ids land"
    )
    parser.add_argument("--prepared", type=Path, help="the store the verdict update prepared")
    parser.add_argument("--staged", type=Path, help="the corpus to swap in")
    parser.add_argument("--live", type=Path, help="the served corpus it replaces")
    parser.add_argument("--tombstone-cutoff", help="drop tombstones older than this ISO-Z time")
    parser.add_argument(
        "--keep-discard",
        action="store_true",
        help="leave the swapped-out tree under its discard name for the caller to delete, named in the report",
    )
    parser.add_argument(
        "--scan-state",
        type=Path,
        help="the journal scan state retention saves, from which the land's read of the journal resumes",
    )
    args = parser.parse_args(argv)
    result = land(
        autosave=args.autosave,
        journal_path=args.journal,
        run_dir=args.run_dir,
        corpus=args.corpus,
        prepared=args.prepared,
        staged=args.staged,
        live=args.live,
        tombstone_cutoff=args.tombstone_cutoff,
        keep_discard=args.keep_discard,
        scan_state=args.scan_state,
    )
    _write_file(args.run_dir / REPORT_NAME, (json.dumps(result.as_dict(), indent=1) + "\n").encode())
    if not result.landed:
        print(f"land aborted: {result.reason}; nothing moved", flush=True)
        return 1
    what = "the corpus and the store" if args.staged is not None else "the store"
    print(
        f"landed {what} on {result.new_stamp}: {result.records} verdicts, {result.overlaid} laid over from saves "
        f"made during the pass, {result.orphaned} orphaned, {result.skips_dropped} skips dropped",
        flush=True,
    )
    print(f"[t] land-locked {result.locked_s:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
