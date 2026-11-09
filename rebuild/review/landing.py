"""Land a pass's corpus and verdict store together, beside a running review server.

A corpus-changing pass builds its corpus beside the served one, at rebuild/out/review.next (a copy-on-write clone of the served tree, `clone_tree`), or promotes a staged one, and runs the verdict update against the store's snapshot: `snapshot` links the live store's file into the pass's run directory as the snapshot S and the prepared store R, under the store's lock, and the verdict update carries, merges and fills into R. Every writer of the store, of S and of R replaces its file by rename, so a write to one never reaches the others, and the review server's signature of the store (`verdict_store.file_signature`) does not move. `land` then moves both into place in one short section under the store's lock (`rebuild.review.store_lock`), which every writer of the store and its journal holds, the review server included, so a save is never applied between the two moves. A pass whose corpus did not move but whose store did lands the store alone.

The land holds no store whole. Without the lock it reads the new corpus's stamp and human unit ids, checks S and R a record at a time and holds each one's head, tombstones and override section (`_tail_of`, over `accepted.read_tail`), reads, when the stamp moves, the journal's events, resuming from the scan state retention saved (`--scan-state`), and walks S, R and S again in the live store C's place as streams merged by unit (`_plan`, over `accepted.iter_records`), working out the result, the offsets of its record lines, the journal's sets and clears, and the orphan guard's terms. Under the lock it compares C with S by bytes (`_is_snapshot`); when C is not S, as after a save made during the pass, or when the walk without the lock had to stamp a tombstone with the time of day, which the land takes under the lock, it checks C the same way and walks S, R and C again under the lock. It aborts, keeping the staged tree, when C is on another stamp than S; when C does not pair with the accepted list beside it (`rebuild.review.accepted`: `--accepted`, by default rebuild/accepted-units.txt beside the store) or the list cannot be read; when S or R does not pair with the list, since a close-out or a re-key then changed it during the pass; or, under the orphan guard the command line sets unless `--allow-orphans` is passed, when the stamp moves and more than `ORPHAN_SHARE_LIMIT` of C's verdicts other than skips, its override records on the live corpus's human units among them, and its accepted units name a unit outside the new corpus's triage index; an override on a unit the live corpus lacks counts in neither term, since the section keeps it until the next close-out and it would count as orphaned on every pass. Its accepted units are the listed units of the live corpus's human units that C holds no override for, which it counts in both terms, streaming the list, so a wholesale id move is refused on a store a close-out emptied and on a pass that carried nothing. The result names the list in its `accepted` field. It aborts the same way, before it writes anything, when S, R or C holds a verdict record `verdict_store.well_formed` refuses, naming the store and the first such record, as `read_store` names them, so it never lands one. Every unit whose record in C differs from S's, or that C cleared since S (a tombstone in C that S lacks or holds at another `at`), is the reviewer's act after the snapshot, so it is laid over R and beats any fill: a clear removes R's record for its unit, whether a fill, a carry or a merge put it there, and leaves C's tombstone. The store's override section, its records and tombstones on listed units (`accepted.Overrides`), is laid over R's the same way, on a stamp change too, whatever corpus its units are on: an override C set, changed or cleared since S replaces R's for its unit, a skip among them becoming a tombstone at its `at` on a stamp change, and an override C removed, a set back to the accepted record (`verdict_store`), removes R's and leaves none, so the unit reads as the list names it; R's other overrides, S's carried through the pass whole (`merge_verdicts`), stay, and an override C shares unchanged with S is kept for a unit R holds none for, a skip becoming a tombstone on a stamp change, so the empty store a pass with no prepared store starts from keeps the section whole. On a stamp change only the new corpus's human units are laid over among the store's own records and tombstones: a set or skip C made since S on any other unit goes to the orphan document of the stamp it was made on (`verdict_store.append_orphans`), a clear on one is dropped, and a skip on one of the human units is dropped, as the carry drops every skip, so the unit is asked again: the result keeps no record for its unit, only a tombstone at the skip's `at`, so neither the verdict the skip replaced nor an older save sent later comes back. A tombstone C shares unchanged with S is kept only for a unit the result holds neither a record nor a tombstone for (on a stamp change, only for the new corpus's human units), whatever its age. The result is written once, under the lock, to the run directory in the layout `document_bytes` gives, and only by a land that lands it: as R's records stream past (`_write_result`), or, when the walk runs under the lock on a land that can neither leave the store in place nor be refused by the orphan guard, as it walks (`_ResultWriter`); and the intent file (`intent_path_for`, var/cycle/land.json beside the store) records what the rest of the section will do. Then the corpus is swapped in with one `renamex_np(RENAME_SWAP)` call (`exchange_dirs`, with a three-rename fallback through `<live>.superseded`), the live store is linked to its stash name on a stamp change (`link_stash`), the result is renamed over the store, the change is appended to the journal, each set line spliced from the result's record line for its unit, read in order on a base and at the walk's offsets on a diff (`_Spliced`), and the intent file is deleted. A stamp change is journaled as an event naming the stash: a base event holding the result when the journal is due one (`journal.base_due`, read from the journal's events, re-read under the lock from where the first read stopped, and due while the marker that says the journal may lack a store write exists, `journal.unjournaled_marker_for`) or when the sets and clears would reach the result's record count (`journal.record_transition`), and otherwise the sets and clears from the store it replaced to the result. A land that keeps the stamp journals the sets and clears. A land that journaled a base removes the marker before it deletes the intent file, and `LandResult` says which the land journaled. A store whose records do not stream by unit, one listing a unit before the unit ahead of it or naming its `verdicts` list twice, makes the land read every store whole (`_land_whole`: `read_store`, `overlay`, `document_bytes` and `journal.record_transition` over the stores in memory), which gives the same bytes, and print one line naming the store and why. Stop signals are blocked for the section. After the lock is released the swapped-out tree is renamed to a discard name beside the served corpus (`move_to_discard`) and deleted, or with `--keep-discard` left for the caller, which tells the open tabs to move before it deletes the tree; under the lock a tree is only ever renamed, never deleted.

A pass whose verdict update changed nothing leaves the store in place. The update then records, beside the prepared store, that it left R as S's bytes (`record_unchanged`, `UNCHANGED_NAME`), and a land that finds that record for the R it is given (`prepared_unchanged`) still reads C, checks it against the accepted list and S, and swaps a staged corpus in, but when laying C over R, which is S, gives C itself (its records, tombstones, override section and `accepted` field) and the journal would gain no line, it writes no result, renames nothing over the store and appends nothing to the journal. It links the store as `LANDED_NAME`, as a land that writes links its result, so the store keeps its inode and modification time and the review server reloads nothing, and `LandResult.store_kept` says so. Any other land, an R that moved after the record among them, lands as above. A land that leaves the store in place writes an intent file only to swap a staged corpus, marked `store_kept`.

A land killed inside the section leaves the intent file, and the kernel releases the lock. The next holder of the lock finishes it first (`recover_interrupted_land`, and `locked_store` for the writers that take the lock): it cuts the journal back to the length the intent recorded, then reads where the land stopped. A corpus not yet swapped drops the intent, and the staged tree stays for the next pass. A swapped corpus with the old store finishes the land from the result it wrote, or is swapped back out when that result is gone, and a store already replaced gets its journal event; a swapped corpus whose land left the store in place needs only its swapped-out tree discarded. A store or result holding a verdict record `verdict_store.well_formed` refuses stops the recovery, naming that file, before it replaces the store, swaps a corpus back or discards one, or appends to the journal, and the intent stays (`LandUnrecoverable`), as it does for a land whose result and old corpus are both gone; every caller reports that refusal as its own (`locked_store`). The review server runs the recovery on its next request (`recover_if_orphaned`), so a recovery that can finish or drop the land needs nobody to run a command.

`LAND_PROTOCOL` and `code_digest` are what the review server advertises on /capabilities, and what the artifact cycle compares with the working tree before it keeps a listening server running through a pass. This module imports only the journal, the store, its lock, the accepted list's reader and the output sink's byte compare, never the cycle driver; its command line also imports the spec loader, whose refusal it runs first, so the land refuses while the alphabet switch (`spec_load.M1_RUNES_ENV`) is set outside the fault kit (`spec_load.small_alphabet_refusal`).

Usage: uv run python -m rebuild.review.landing --autosave FILE --journal FILE --run-dir DIR --corpus DIR [--prepared FILE] [--staged DIR --live DIR [--keep-discard]] [--scan-state FILE] [--allow-orphans] [--accepted FILE]
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
from array import array
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from operator import itemgetter
from pathlib import Path
from typing import Any, BinaryIO

from rebuild.pipeline import output_sink
from rebuild.review import accepted, journal
from rebuild.review.accepted import Overrides
from rebuild.review.store_lock import LockBusy, store_lock
from rebuild.review.verdict_store import (
    EXPORT_FORMAT,
    MalformedStore,
    append_orphans,
    link_or_copy,
    malformed_clause,
    malformed_override,
    note_of,
    orphans_dir_for,
    parse_cleared,
    read_store_document,
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
    "accepted.py",
)
FRESHNESS_MODULES = (
    "pipeline/fingerprint.py",
    "tools/site_fonts.py",
    "review/unit_index.py",
    "review/app_index.py",
)
SNAPSHOT_NAME = "verdicts-autosave.json"
PREPARED_NAME = "prepared.json"
UNCHANGED_NAME = "unchanged.json"
LANDING_NAME = "landing.json"
LANDED_NAME = "landed.json"
REPORT_NAME = "land-report.json"
RENAME_SWAP = 0x2
RENAME_EXCHANGE = 0x2
AT_FDCWD = -100
UNSUPPORTED_SWAP_ERRNOS = frozenset({errno.ENOTSUP, errno.EINVAL, errno.ENOSYS})
BLOCKED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
ORPHAN_SHARE_LIMIT = 0.10


def code_digest(review_dir: Path) -> str:
    """Return the digest of the modules whose behavior the land protocol depends on (`PROTOCOL_MODULES` under `review_dir`) and of the ones the server's /status judges the served corpus's freshness with (`FRESHNESS_MODULES`, under `review_dir`'s parent): the fingerprint components, the site font paths the `fonts` component hashes, and the per-unit index and app sidecar stamps. The server takes it at boot from the files it loaded, and the cycle from the working tree, so a server whose code differs from the tree's is never trusted to run through a pass. A server started before a change to the freshness modules would go on judging the corpus by the components it loaded and could call a stale corpus ready, so the next pass that writes under it restarts it."""
    review_dir = Path(review_dir)
    paths = [(name, review_dir / name) for name in PROTOCOL_MODULES]
    paths += [(name, review_dir.parent / name) for name in FRESHNESS_MODULES]
    digest = hashlib.sha256()
    for name, path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        digest.update(f"{name}\t{hashlib.sha256(data).hexdigest()}\n".encode())
    return digest.hexdigest()


def orphan_share(orphaned: int, prior: int) -> str | None:
    """Return `orphaned` verdicts as a share of `prior` ones, worded for a refusal, when the share is more than `ORPHAN_SHARE_LIMIT`, and None otherwise. The carry (`rebuild/tools/carry_verdicts.py`) and the land refuse past the limit unless `--allow-orphans` is passed: a change that moves unit ids wholesale orphans nearly every verdict at once, while an edit to the runes orphans only the verdicts on the windows it redrew, a few percent at most by the carry counts on the run lines of rebuild/out/cycle-timings.ndjson. The limit sits well above the routine passes there and far below a wholesale move."""
    if orphaned <= ORPHAN_SHARE_LIMIT * prior:
        return None
    return f"{orphaned:,} of {prior:,} ({orphaned / prior:.2%}, more than the {ORPHAN_SHARE_LIMIT:.0%} limit)"


def intent_path_for(store: Path) -> Path:
    """Return the intent file of the store at `store`: var/cycle/land.json beside it, which for the live store is under the repo root's gitignored var/."""
    return Path(store).parent / "var" / "cycle" / "land.json"


@dataclass
class Store:
    """A verdicts document as the land reads it: its stamp, its own records by unit, its own tombstones (unit to the clear's `at`), its `accepted` field, the accepted list it pairs with (`rebuild.review.accepted`), None when it names none, and its override section (`accepted.Overrides`)."""

    stamp: str | None
    records: dict[str, dict] = field(default_factory=dict)
    cleared: dict[str, str] = field(default_factory=dict)
    accepted: Any = None
    overrides: Overrides = field(default_factory=Overrides)

    def document(self) -> dict:
        """Return the store as `accepted.pair` reads a store document: its field, its records, its tombstones and its override section."""
        document = {
            "accepted": self.accepted,
            "verdicts": list(self.records.values()),
            "cleared": [{"unit": unit, "at": at} for unit, at in self.cleared.items()],
        }
        if (section := self.overrides.field()) is not None:
            document[accepted.OVERRIDES] = section
        return document

    def joined(self) -> tuple[dict[str, dict], dict[str, str]]:
        """Return the store's records and tombstones and its override section's as one store, as the journal records it (`accepted.joined`)."""
        return accepted.joined(self.records, self.cleared, self.overrides)


def _normalize(record: dict) -> dict:
    return {
        "unit": record["unit"],
        "verdict": record.get("verdict"),
        "note": note_of(record),
        "at": record.get("at") or "",
    }


def _signature(record: dict | None) -> tuple | None:
    if record is None:
        return None
    return (record.get("verdict"), note_of(record), record.get("at") or "")


def read_store(path: Path, name: str | None = None) -> Store | None:
    """Return the verdicts document at `path`, or None when it is missing or is not an ams-review-verdicts/1 document. A document holding a record `verdict_store.well_formed` refuses raises `verdict_store.MalformedStore` before any of its records is compared, naming the document `name`, or `the store <path>` without one, and the first such record (`verdict_store.read_store_document`)."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    data = read_store_document(raw, name or f"the store {path}")
    if data is None:
        return None
    records = {unit: _normalize(record) for unit, record in journal.latest_by_unit(data["verdicts"]).items()}
    cleared = {unit: at for unit, at in parse_cleared(data).items() if unit not in records}
    section = Overrides.of(data)
    overrides = Overrides(
        {unit: _normalize(record) for unit, record in section.records.items()}, section.cleared
    )
    return Store(data["manifest_generated_at"], records, cleared, data.get("accepted"), overrides)


def document_bytes(store: Store) -> bytes:
    """Return `store` as the review server writes a store: one ams-review-verdicts/1 document with one record per line, its `accepted` field in the head when it names a list, the tombstones after the records when there are any, and the override section after them when the store holds one."""
    head = {"format": EXPORT_FORMAT, "manifest_generated_at": store.stamp, "exported_at": journal.now_stamp()}
    if store.accepted is not None:
        head["accepted"] = store.accepted
    prefix = json.dumps(head, ensure_ascii=False)[:-1]
    body = ",\n".join(json.dumps(store.records[unit], ensure_ascii=False) for unit in sorted(store.records))
    tail = ""
    if store.cleared:
        cleared = [{"unit": unit, "at": at} for unit, at in sorted(store.cleared.items())]
        tail = f', "cleared": {json.dumps(cleared, ensure_ascii=False)}'
    if (section := store.overrides.field()) is not None:
        overridden = ",\n".join(json.dumps(record, ensure_ascii=False) for record in section["verdicts"])
        tail += (
            f', "{accepted.OVERRIDES}": {{"verdicts": [\n{overridden}\n], '
            f'"cleared": {json.dumps(section["cleared"], ensure_ascii=False)}}}'
        )
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
    """Link the live store's file into `run_dir` as the snapshot (`SNAPSHOT_NAME`) and the prepared store (`PREPARED_NAME`), under the store's lock, and return the snapshot's stamp. The three names share one file, copied only where a link fails, as across filesystems (`verdict_store.link_or_copy`), and every writer of the store, of the snapshot and of the prepared store replaces its file by rename, so a write to one never reaches the others and the review server's `file_signature` of the store does not move. A missing store makes neither name and returns None. A store holding a record `verdict_store.well_formed` refuses raises `verdict_store.MalformedStore` naming the live store, the first such record and the journal restore (`read_store`), so a pass stops at its snapshot, which the land would refuse, before the verdict update reads it."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    with locked_store(autosave, quiet=True):
        try:
            link_or_copy(autosave, run_dir / SNAPSHOT_NAME)
        except FileNotFoundError:
            return None
        link_or_copy(run_dir / SNAPSHOT_NAME, run_dir / PREPARED_NAME)
    try:
        taken = read_store(
            run_dir / SNAPSHOT_NAME, f"the live store {autosave}, linked to {run_dir / SNAPSHOT_NAME},"
        )
    except MalformedStore as malformed:
        raise MalformedStore(
            f"{malformed}; restore a readable store from the journal with "
            "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`"
        ) from None
    return taken.stamp if taken is not None else None


def record_unchanged(prepared: Path, unchanged: bool) -> None:
    """Record beside the prepared store `prepared`, as `UNCHANGED_NAME`, whether the verdict update left it as the snapshot's bytes, with the file's inode, size and modification time, so the land leaves the store in place only for that file (`prepared_unchanged`); a False removes any record there."""
    record = Path(prepared).with_name(UNCHANGED_NAME)
    if not unchanged:
        record.unlink(missing_ok=True)
        return
    _write_file(record, (json.dumps({"prepared": _identity(Path(prepared))}) + "\n").encode())


def prepared_unchanged(prepared: Path) -> bool:
    """Return whether the verdict update recorded the prepared store `prepared` as the snapshot's bytes (`record_unchanged`) and the file is still the one it recorded. A missing or unreadable record, or a file that moved since, reads as changed, which lands it as usual."""
    try:
        recorded = json.loads(Path(prepared).with_name(UNCHANGED_NAME).read_text(encoding="utf-8"))
        return isinstance(recorded, dict) and recorded.get("prepared") == _identity(Path(prepared))
    except OSError, ValueError:
        return False


def _identity(path: Path) -> list[int]:
    found = path.stat()
    return [found.st_ino, found.st_size, found.st_mtime_ns]


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
    """What a land did. `landed` is False when it aborted, and `reason` says why. `store_kept` is True when it left the store in place, as the module docstring says when. `journal_base` is True when the land journaled its result as a base event, False when it journaled sets and clears, and None when it journaled nothing; `journal_sets` and `journal_clears` count the set and clear lines it journaled."""

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
    journal_base: bool | None = None
    journal_sets: int = 0
    journal_clears: int = 0
    store_kept: bool = False

    def as_dict(self) -> dict:
        return {**asdict(self), "locked_s": round(self.locked_s, 3)}


def overlay(
    result: Store,
    before: Store,
    now: Store,
    *,
    new_ids: frozenset[str] | None,
) -> tuple[int, list[dict], int]:
    """Lay the live store's changes since the snapshot (`now` against `before`) over `result` in place: a record `now` set, changed or removed, and a clear since the snapshot, a tombstone in `now` that `before` lacks or holds at another `at`, which removes `result`'s record for its unit and leaves the tombstone at `now`'s `at`. A tombstone `now` shares unchanged with `before` is kept only for a unit `result` holds neither a record nor a tombstone for. `new_ids` is the new corpus's human unit ids on a stamp change, and None on a land that keeps the stamp; on a stamp change only those units are laid over or keep a tombstone, a set on any other unit becomes an orphan entry, and a skip made since the snapshot clears its unit in `result`, leaving a tombstone at the skip's `at`. The override section is laid over `result`'s whole, on a stamp change too, and its entries `now` shares unchanged with `before` are kept for the units `result` holds none for (`_overlay_overrides`). Returns the count of units overlaid, the orphan entries, and the count of skips dropped."""
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
        if unit in before.records or (new_ids is not None and unit not in new_ids):
            continue
        if before.cleared.get(unit) != at:
            overlaid += 1
            result.records.pop(unit, None)
            result.cleared[unit] = at
        elif unit not in result.records and unit not in result.cleared:
            result.cleared[unit] = at
    laid, skipped = _overlay_overrides(
        result.overrides, before.overrides, now.overrides, moved=new_ids is not None
    )
    return overlaid + laid, orphans, skips + skipped


def _overlay_overrides(
    result: Overrides,
    before: Overrides,
    now: Overrides,
    *,
    moved: bool,
    clock: Callable[[], str] | None = None,
) -> tuple[int, int]:
    """Lay the live store's override changes since the snapshot (`now` against `before`) over `result` in place, whatever corpus each unit is on: a unit whose override record or tombstone `now` set, changed or removed takes `now`'s, none when `now` removed it, a set back to the unit's accepted record, and on a stamp change (`moved`) a skip `now` set becomes a tombstone at its `at`, as the merge's `Overrides.moved` makes of the skips the snapshot held. An entry `now` shares unchanged with `before` is kept for a unit `result` holds none for, a skip becoming a tombstone the same way, as `overlay` keeps an unchanged tombstone, so a land with no prepared store, which starts from an empty one, keeps the section whole. A skip with an empty `at` becomes a tombstone at `clock()`, `journal.now_stamp` without one. Return how many units it laid over and how many of them were skips it turned into tombstones."""
    clock = clock or journal.now_stamp
    laid = skips = 0
    for unit in sorted(
        before.records.keys() | before.cleared.keys() | now.records.keys() | now.cleared.keys()
    ):
        record = now.records.get(unit)
        changed = _signature(record) != _signature(before.records.get(unit)) or now.cleared.get(
            unit
        ) != before.cleared.get(unit)
        if not changed and unit in result:
            continue
        laid += changed
        if record is not None and moved and record["verdict"] == "skip":
            skips += changed
            result.clear(unit, record["at"] or clock())
        elif record is not None:
            result.set(record)
        elif unit in now.cleared:
            result.clear(unit, now.cleared[unit])
        else:
            result.drop(unit)
    return laid, skips


def land(
    *,
    autosave: Path,
    journal_path: Path,
    run_dir: Path,
    corpus: Path,
    prepared: Path | None = None,
    staged: Path | None = None,
    live: Path | None = None,
    atomic: bool = True,
    keep_discard: bool = False,
    scan_state: Path | None = None,
    guard_orphans: bool = False,
    accepted_path: Path | None = None,
) -> LandResult:
    """Land the prepared store, and the staged corpus when `staged` is given, as the module docstring describes: walking the stores as streams (`_land_streamed`), or reading them whole (`_land_whole`) after printing why when one's records do not stream by unit (`_Unstreamable`). `corpus` is the directory whose manifest names the landed stamp and human unit ids: the staged tree, or the live one for a store-only land. When the verdict update recorded `prepared` as the snapshot's bytes (`prepared_unchanged`), the land leaves the store in place where the module docstring says it does. With no `prepared` store, or none on disk, a land that moves the stamp writes an empty store stamped for the new corpus, and a land on an unchanged stamp starts from the snapshot's own records and tombstones, so the live store's verdicts stay as they are, since a carry on an unchanged stamp maps every unit to itself; either way the overlay gives the result the live store's override section whole. The swapped-out tree is renamed to its discard name after the lock is released and deleted, or with `keep_discard` left for the caller to delete, its path in the result's `discard`, so the caller can tell the open tabs to move first. `scan_state` is the journal scan state retention saves (`journal.save_scan_state`); a land that moves the stamp resumes its read of the journal's events from it, and reads them from the start without it. The land only reads it. With `guard_orphans` set, a land that moves the stamp aborts, before it writes anything, when more than `ORPHAN_SHARE_LIMIT` of the live store's verdicts other than skips name a unit outside the new corpus's triage index (`orphan_share`); the command line sets it unless `--allow-orphans` is passed. A land whose snapshot, prepared store or live store holds a record `verdict_store.well_formed` refuses aborts before it writes anything, and its reason names that store and the first such record. A land that finds a dead land's intent whose recovery refuses (`LandUnrecoverable`) aborts with that refusal as its reason. `accepted_path` is the accepted list, by default the one beside `autosave` (`accepted.path_for`); the land aborts, writing nothing, when the live store does not pair with it or it cannot be read (`accepted.Refused`), and when the snapshot or the prepared store does not pair with it, since the list then changed during the pass, writes the list's field into its result, and counts the listed units of the live corpus's human units that the live store holds no override for in both terms of its orphan guard."""
    autosave, journal_path, run_dir = Path(autosave), Path(journal_path), Path(run_dir)
    listed_path = Path(accepted_path) if accepted_path is not None else accepted.path_for(autosave)
    new_stamp, new_ids = read_manifest(corpus)

    def run(path: Callable[..., LandResult]) -> LandResult:
        return path(
            autosave=autosave,
            journal_path=journal_path,
            run_dir=run_dir,
            prepared=prepared,
            staged=staged,
            live=live,
            atomic=atomic,
            scan_state=scan_state,
            guard_orphans=guard_orphans,
            listed_path=listed_path,
            new_stamp=new_stamp,
            new_ids=new_ids,
        )

    try:
        result = run(_land_streamed)
    except _Unstreamable as unstreamable:
        print(f"the land reads the stores whole: {unstreamable}", flush=True)
        result = run(_land_whole)
    if result.landed and staged is not None and live is not None:
        discard = move_to_discard(staged, live)
        if keep_discard:
            result.discard = str(discard)
        else:
            shutil.rmtree(discard, ignore_errors=True)
    return result


def _land_whole(
    *,
    autosave: Path,
    journal_path: Path,
    run_dir: Path,
    prepared: Path | None,
    staged: Path | None,
    live: Path | None,
    atomic: bool,
    scan_state: Path | None,
    guard_orphans: bool,
    listed_path: Path,
    new_stamp: str,
    new_ids: frozenset[str],
) -> LandResult:
    """Land as `land` does with every store read whole (`read_store`), laid over in memory (`overlay`), written whole (`document_bytes`) and diffed in memory for the journal (`journal.record_transition`): the land's path for stores whose records do not stream by unit (`_Unstreamable`), which gives the bytes the walk gives."""
    try:
        before = read_store(run_dir / SNAPSHOT_NAME, f"the snapshot {run_dir / SNAPSHOT_NAME}")
        ready = read_store(prepared, f"the prepared store {prepared}") if prepared is not None else None
    except MalformedStore as malformed:
        return _refuse_malformed(malformed)
    if ready is None:
        ready = (
            Store(new_stamp, dict(before.records), dict(before.cleared))
            if before is not None and before.stamp == new_stamp
            else Store(new_stamp)
        )
    if ready.stamp != new_stamp:
        return _refuse_prepared(ready.stamp, new_stamp)
    if staged is not None and live is None:
        raise ValueError("a staged corpus needs the live path it replaces")
    kept = prepared is not None and prepared_unchanged(prepared)
    journal_scan, live_ids = _journal_and_live(
        before is None or before.stamp != new_stamp,
        journal_path=journal_path,
        scan_state=scan_state,
        guard_orphans=guard_orphans,
        listed_path=listed_path,
        staged=staged,
        live=live,
        new_ids=new_ids,
    )

    try:
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
                atomic=atomic,
                journal_scan=journal_scan,
                guard_orphans=guard_orphans,
                listed_path=listed_path,
                live_ids=live_ids,
                kept=kept,
            )
            result.locked_s = time.perf_counter() - started
    except LandUnrecoverable as refused:
        return LandResult(False, str(refused))
    return result


def _refuse_prepared(stamp: str | None, new_stamp: str) -> LandResult:
    return LandResult(
        False, f"the prepared store is stamped {stamp}, not {new_stamp}, the corpus it lands with"
    )


def _journal_and_live(
    moves: bool,
    *,
    journal_path: Path,
    scan_state: Path | None,
    guard_orphans: bool,
    listed_path: Path,
    staged: Path | None,
    live: Path | None,
    new_ids: frozenset[str],
) -> tuple[journal.ScanState | None, frozenset[str] | None]:
    """Return what a land that `moves` the stamp reads without the lock: the journal's scan state, resumed from `scan_state`, and under the orphan guard beside a list the live corpus's human unit ids; both None on a land that keeps the stamp."""
    if not moves:
        return None, None
    resume = journal.load_scan_state(scan_state) if scan_state is not None else None
    journal_scan = journal.scan(journal_path, resume=resume).state
    live_ids = None
    if guard_orphans and listed_path.exists():
        live_ids = _live_ids(live) if staged is not None and live is not None else new_ids
    return journal_scan, live_ids


class _Unstreamable(Exception):
    """Raised before the land writes anything when a store's records do not stream by unit: they go out of unit order, or its document names a `verdicts` list more than once. The land then reads the stores whole (`_land_whole`), which gives the same bytes."""

    def __init__(self, name: str, why: str) -> None:
        super().__init__(f"{name} {why}")


_LINE = json.JSONEncoder(ensure_ascii=False).encode
_SET_PREFIX = b'{"kind": "set", '
_READ_BLOCK = 1 << 20


def _tail_of(path: Path, name: str) -> accepted.Tail | None:
    """Return the verdicts document at `path` as `accepted.read_tail` reads it, or None when it is missing or is not an ams-review-verdicts/1 document, as `read_store` answers. A document holding a record `verdict_store.well_formed` refuses raises `verdict_store.MalformedStore` naming the document `name` and the first such record, its own records before its override section's, as `read_store` does, and one naming its `verdicts` list more than once raises `_Unstreamable`."""
    tail = accepted.read_tail(path)
    if tail is None:
        return None
    if tail.malformed is not None:
        raise MalformedStore(malformed_clause(*tail.malformed, name))
    if (clause := malformed_override(tail.members, name)) is not None:
        raise MalformedStore(clause)
    if tail.lists > 1:
        raise _Unstreamable(name, "names its `verdicts` list more than once")
    return tail


@dataclass
class _Side:
    """One store the land walks: the file its own records stream from (None when it holds none), the name a refusal gives it, its tombstones as `journal.parse_cleared` reads them, those on units holding a record among them, which the walk passes over unit by unit as `read_store` drops them, and its override section, its records normalized as `read_store` normalizes them."""

    source: Path | None
    name: str
    cleared: dict[str, str] = field(default_factory=dict)
    overrides: Overrides = field(default_factory=Overrides)


def _side_of(path: Path, tail: accepted.Tail, name: str) -> _Side:
    section = tail.overrides()
    overrides = Overrides(
        {unit: _normalize(record) for unit, record in section.records.items()}, section.cleared
    )
    return _Side(path, name, tail.cleared, overrides)


def _own_records(source: Path | BinaryIO | None, name: str) -> Iterator[dict]:
    """Yield the store's own records at `source` in unit order, normalized as `read_store` normalizes them, one record held at a time. Records on one unit next to each other are settled as `journal.latest_by_unit` settles them, the latest `at` winning and the first of equal ones kept; a record on a unit before the one ahead of it raises `_Unstreamable`, naming the store `name`."""
    if source is None:
        return
    held: dict | None = None
    for record in accepted.iter_records(source):
        assert isinstance(record, dict)
        if held is not None:
            if record["unit"] == held["unit"]:
                if record["at"] > held["at"]:
                    held = record
                continue
            if record["unit"] < held["unit"]:
                raise _Unstreamable(name, f"lists {record['unit']} after {held['unit']}")
            yield _normalize(held)
        held = record
    if held is not None:
        yield _normalize(held)


class _Clock:
    """`journal.now_stamp`, noting whether the walk asked it, since a plan that took the time outside the lock is taken again under it."""

    def __init__(self) -> None:
        self.asked = False

    def __call__(self) -> str:
        self.asked = True
        return journal.now_stamp()


@dataclass
class _Plan:
    """What landing the prepared store R over the live store C gives, laying over it C's changes since the snapshot S, as the walk (`_plan`) works it out holding no store: the units whose own record in the result is not R's, each with its record or None (`changes`), the result's own tombstones and override section, and `body`, the bytes of its own record lines from the first line's start to the last line's end, as `document_bytes` writes them. For the journal, against C as one store with its override section (`accepted.joined`): each own record line the diff sets, as its offset in that span and its length (`set_offsets`, `set_lengths`, 12 bytes a set), each override record it sets with how many own lines it sets before it (`override_sets`), its clears in unit order, the result's record count and tombstones, and C's; for a base, the ordinals of the own lines on units the result's override section names, which the journal records through the section (`hidden`), and each override record with how many own lines the journal holds before it (`override_records`). Then the overlay's counts and orphan entries, whether the result is C's records, tombstones and override section (`same`), the orphan guard's two terms over C's verdicts (`prior`, `orphaned`), and whether the walk took the time (`stamped`)."""

    changes: dict[str, dict | None] = field(default_factory=dict)
    cleared: dict[str, str] = field(default_factory=dict)
    overrides: Overrides = field(default_factory=Overrides)
    body: int = 0
    set_offsets: array = field(default_factory=lambda: array("q"))
    set_lengths: array = field(default_factory=lambda: array("I"))
    override_sets: list[tuple[str, int]] = field(default_factory=list)
    hidden: array = field(default_factory=lambda: array("q"))
    override_records: list[tuple[str, int]] = field(default_factory=list)
    clears: list[tuple[str, str | None]] = field(default_factory=list)
    records: int = 0
    tombstones: dict[str, str] = field(default_factory=dict)
    old_records: int = 0
    old_tombstones: dict[str, str] = field(default_factory=dict)
    orphans: list[dict] = field(default_factory=list)
    overlaid: int = 0
    skips: int = 0
    same: bool = True
    prior: int = 0
    orphaned: int = 0
    stamped: bool = False


def _plan(
    s: _Side,
    r: _Side,
    c: _Side,
    *,
    moved: bool,
    new_ids: frozenset[str],
    live_ids: frozenset[str] | None,
    out: _ResultWriter | None = None,
) -> _Plan:
    """Walk the snapshot `s`, the prepared store `r` and the live store `c` as streams merged by unit, with their tombstones and override sections held, and return what laying `c`'s changes since `s` over `r` gives (`_Plan`), by `overlay`'s rules unit by unit, `moved` and `new_ids` as `overlay` takes them. `c` given as `s` itself is the live store read as the snapshot, which streams once; `r` streaming from `s`'s file is the snapshot's own records and tombstones, as a land with no prepared store on an unchanged stamp starts from. `live_ids` is the live corpus's human unit ids the orphan guard counts override records on. With `out`, a result being written under the lock, each of the result's own record lines goes to it as the walk reaches it."""
    clock = _Clock()
    plan = _Plan(overrides=r.overrides.copy())
    laid, plan.skips = _overlay_overrides(plan.overrides, s.overrides, c.overrides, moved=moved, clock=clock)
    plan.overlaid = laid
    c_is_s = c is s
    r_is_s = r.source is not None and r.source == s.source
    s_records = _own_records(s.source, s.name)
    r_records = None if r_is_s else _own_records(r.source, r.name)
    c_records = None if c_is_s else _own_records(c.source, c.name)
    s_head = next(s_records, None)
    r_head = next(r_records, None) if r_records is not None else None
    c_head = next(c_records, None) if c_records is not None else None
    ours, theirs = plan.overrides, c.overrides
    keys = sorted(
        s.cleared.keys()
        | r.cleared.keys()
        | c.cleared.keys()
        | s.overrides.records.keys()
        | s.overrides.cleared.keys()
        | theirs.records.keys()
        | theirs.cleared.keys()
        | ours.records.keys()
        | ours.cleared.keys()
    )
    key = 0
    lines = joined = 0
    body = 0
    while True:
        unit = keys[key] if key < len(keys) else None
        for head in (s_head, r_head, c_head):
            if head is not None and (unit is None or head["unit"] < unit):
                unit = head["unit"]
        if unit is None:
            break
        if key < len(keys) and keys[key] == unit:
            key += 1
        s_rec = r_rec = c_rec = None
        if s_head is not None and s_head["unit"] == unit:
            s_rec = s_head
            s_head = next(s_records, None)
        if r_records is None:
            r_rec = s_rec
        elif r_head is not None and r_head["unit"] == unit:
            r_rec = r_head
            r_head = next(r_records, None)
        if c_records is None:
            c_rec = s_rec
        elif c_head is not None and c_head["unit"] == unit:
            c_rec = c_head
            c_head = next(c_records, None)
        s_cl = s.cleared.get(unit) if s_rec is None else None
        r_cl = r.cleared.get(unit) if r_rec is None else None
        c_cl = c.cleared.get(unit) if c_rec is None else None

        elsewhere = moved and unit not in new_ids
        rec, cl = r_rec, r_cl
        if c_rec is not s_rec and _signature(c_rec) != _signature(s_rec):
            if c_rec is None:
                if not elsewhere:
                    plan.overlaid += 1
                    rec, cl = None, c_cl or clock()
            elif elsewhere:
                plan.orphans.append({**c_rec, "reason": "orphan"})
            elif moved and c_rec["verdict"] == "skip":
                plan.skips += 1
                rec, cl = None, c_rec["at"] or clock()
            else:
                plan.overlaid += 1
                rec, cl = c_rec, None
        if c_cl is not None and s_rec is None and not elsewhere:
            if s_cl != c_cl:
                plan.overlaid += 1
                rec, cl = None, c_cl
            elif rec is None and cl is None:
                cl = c_cl
        if rec is not r_rec:
            plan.changes[unit] = rec
        if cl is not None:
            plan.cleared[unit] = cl
        if plan.same and (rec != c_rec or cl != c_cl):
            plan.same = False

        ours_held = unit in ours.records or unit in ours.cleared
        start = length = 0
        if rec is not None:
            line = _LINE(rec)
            if out is not None:
                encoded = line.encode()
                out.record(encoded)
                length = len(encoded)
            else:
                length = len(line) if line.isascii() else len(line.encode())
            start = body + 2 if lines else body
            body = start + length
            if ours_held:
                plan.hidden.append(lines)
            lines += 1
        if ours_held:
            new_rec, new_cl = ours.records.get(unit), ours.cleared.get(unit)
        else:
            new_rec, new_cl = rec, cl
        if unit in theirs.records or unit in theirs.cleared:
            old_rec, old_cl = theirs.records.get(unit), theirs.cleared.get(unit)
        else:
            old_rec, old_cl = c_rec, c_cl
        if new_rec is not None:
            plan.records += 1
            if ours_held:
                plan.override_records.append((unit, joined))
            else:
                joined += 1
            if old_rec is not new_rec and (old_rec is None or _signature(old_rec) != _signature(new_rec)):
                if ours_held:
                    plan.override_sets.append((unit, len(plan.set_offsets)))
                else:
                    plan.set_offsets.append(start)
                    plan.set_lengths.append(length)
        elif old_rec is not None or old_cl != new_cl:
            plan.clears.append((unit, new_cl))
        if new_cl is not None:
            plan.tombstones[unit] = new_cl
        if old_rec is not None:
            plan.old_records += 1
        if old_cl is not None:
            plan.old_tombstones[unit] = old_cl

        if c_rec is not None and c_rec["verdict"] != "skip":
            plan.prior += 1
            plan.orphaned += unit not in new_ids
        overridden = theirs.records.get(unit)
        if (
            overridden is not None
            and c_rec is None
            and unit in (live_ids or ())
            and overridden["verdict"] != "skip"
        ):
            plan.prior += 1
            plan.orphaned += unit not in new_ids
    plan.body = body
    plan.same = plan.same and plan.overrides == c.overrides
    plan.stamped = clock.asked
    return plan


def _result_records(records: Iterator[dict], changes: Mapping[str, dict | None]) -> Iterator[dict]:
    """Yield the result's own records in unit order: `records`, R's, with each unit in `changes` taking its record there, or none where it holds None."""
    pending = sorted(changes.items(), key=itemgetter(0))
    at = 0
    for record in records:
        unit = record["unit"]
        while at < len(pending) and pending[at][0] < unit:
            added = pending[at][1]
            at += 1
            if added is not None:
                yield added
        if at < len(pending) and pending[at][0] == unit:
            replaced = pending[at][1]
            at += 1
            if replaced is not None:
                yield replaced
            continue
        yield record
    for _unit, added in pending[at:]:
        if added is not None:
            yield added


class _ResultWriter:
    """The land's result as it is written, to `path` through a temporary file and a rename, as `document_bytes` lays out a store: the head with `exported_at` stamped when the writer opens and the list's `field_`, then each own record line in unit order (`record`), and at `finish` the tombstones and the override section. Until `finish` renames it into place, `discard` leaves no file."""

    def __init__(self, path: Path, stamp: str, field_: dict | None) -> None:
        self._path = path
        self._tmp = path.with_name(path.name + ".tmp")
        self._out = self._tmp.open("wb", buffering=_READ_BLOCK)
        head: dict[str, object] = {
            "format": EXPORT_FORMAT,
            "manifest_generated_at": stamp,
            "exported_at": journal.now_stamp(),
        }
        if field_ is not None:
            head["accepted"] = field_
        prefix = (json.dumps(head, ensure_ascii=False)[:-1] + ', "verdicts": [\n').encode()
        self._out.write(prefix)
        self.head = len(prefix)
        self._lines = 0
        self._body = 0

    def record(self, line: bytes) -> None:
        if self._lines:
            self._out.write(b",\n")
            self._body += 2
        self._out.write(line)
        self._body += len(line)
        self._lines += 1

    def finish(self, plan: _Plan) -> dict[str, tuple[int, int]]:
        """Write the tombstones and the override section `plan` holds, rename the file into place and return each override record line's offset and length by unit, for the splice. Record lines that came to another length than the plan's raise RuntimeError, leaving no file."""
        try:
            if self._body != plan.body:
                raise RuntimeError(
                    f"the land wrote {self._body} bytes of record lines where its walk counted {plan.body}"
                )
            position = self.head + self._body
            tail = "\n]"
            if plan.cleared:
                cleared = [{"unit": unit, "at": at} for unit, at in sorted(plan.cleared.items())]
                tail += f', "cleared": {json.dumps(cleared, ensure_ascii=False)}'
            spans: dict[str, tuple[int, int]] = {}
            if (section := plan.overrides.field()) is not None:
                opening = (tail + f', "{accepted.OVERRIDES}": {{"verdicts": [\n').encode()
                self._out.write(opening)
                position += len(opening)
                for index, record in enumerate(section["verdicts"]):
                    line = _LINE(record).encode()
                    if index:
                        self._out.write(b",\n")
                        position += 2
                    spans[record["unit"]] = (position, len(line))
                    self._out.write(line)
                    position += len(line)
                tail = f'\n], "cleared": {json.dumps(section["cleared"], ensure_ascii=False)}}}'
            self._out.write((tail + "}\n").encode())
            self._out.close()
            os.replace(self._tmp, self._path)
        except BaseException:
            self.discard()
            raise
        return spans

    def discard(self) -> None:
        self._out.close()
        self._tmp.unlink(missing_ok=True)


def _write_result(
    path: Path, r: _Side, plan: _Plan, stamp: str, field_: dict | None
) -> tuple[int, dict[str, tuple[int, int]]]:
    """Write the result `plan` describes to `path` (`_ResultWriter`): R's own records with the plan's changes, read from R's file as they are written. Return the length of the head, before the first record line, and each override record line's offset and length by unit, for the splice."""
    writer = _ResultWriter(path, stamp, field_)
    try:
        for record in _result_records(_own_records(r.source, r.name), plan.changes):
            writer.record(_LINE(record).encode())
    except BaseException:
        writer.discard()
        raise
    return writer.head, writer.finish(plan)


def _pread(fd: int, length: int, offset: int) -> bytes:
    data = os.pread(fd, length, offset)
    if len(data) != length:
        raise OSError(f"read {len(data)} of {length} bytes at {offset} of the landed store")
    return data


def _splice(line: bytes) -> bytes:
    """Return the journal's set line for a record line of the landed store, the record's JSON with no comma after it: the record's fields after `"kind": "set"`, as `json.dumps(journal._set_line(record))` writes them, since the record line is `json.dumps` of the same fields."""
    return _SET_PREFIX + line[1:] + b"\n"


def _record_lines(handle: BinaryIO, start: int, length: int) -> Iterator[bytes]:
    """Yield the landed store's own record lines, read in order from `start` for `length` bytes, each without its comma."""
    handle.seek(start)
    pending = b""
    while length:
        block = handle.read(min(_READ_BLOCK, length))
        if not block:
            raise OSError("the landed store ends inside its record lines")
        length -= len(block)
        lines = (pending + block).split(b"\n")
        pending = lines.pop()
        for line in lines:
            yield line[:-1]
    if pending:
        yield pending


def _shown(lines: Iterator[bytes], hidden: array) -> Iterator[bytes]:
    """Yield `lines` but those at the ordinals in `hidden`, which ascend."""
    skipped = iter(hidden)
    skip = next(skipped, None)
    for ordinal, line in enumerate(lines):
        if ordinal == skip:
            skip = next(skipped, None)
            continue
        yield line


class _Spliced:
    """The land's change to the journal (`journal.Transition`), from the walk's plan: its set lines spliced from the record lines of the landed store at `result`, all of them read in order on a base and those at the plan's offsets otherwise, and the seed's from the live store it replaced, read from `old`, the handle it held before the rename (none when there was no store)."""

    def __init__(
        self,
        plan: _Plan,
        result: Path,
        head: int,
        spans: dict[str, tuple[int, int]],
        old: BinaryIO | None,
        live: _Side,
    ) -> None:
        self.records = plan.records
        self._plan = plan
        self._result = result
        self._head = head
        self._spans = spans
        self._old = old
        self._live = live

    def diff(self) -> tuple[int, int, bool]:
        plan = self._plan
        held = bool(plan.old_records or plan.old_tombstones)
        return len(plan.set_offsets) + len(plan.override_sets), len(plan.clears), held

    def tombstones(self) -> Mapping[str, str]:
        return self._plan.tombstones

    def _override(self, fd: int, unit: str) -> bytes:
        offset, length = self._spans[unit]
        return _splice(_pread(fd, length, offset))

    def set_lines(self, base: bool) -> Iterator[bytes]:
        plan = self._plan
        with self._result.open("rb") as handle:
            fd = handle.fileno()
            overrides = plan.override_records if base else plan.override_sets
            at = 0
            if base:
                lines = _shown(_record_lines(handle, self._head, plan.body), plan.hidden)
            else:
                lines = (
                    _pread(fd, length, self._head + offset)
                    for offset, length in zip(plan.set_offsets, plan.set_lengths, strict=True)
                )
            for position, line in enumerate(lines):
                while at < len(overrides) and overrides[at][1] == position:
                    yield self._override(fd, overrides[at][0])
                    at += 1
                yield _splice(line)
            for unit, _position in overrides[at:]:
                yield self._override(fd, unit)

    def clear_lines(self) -> Iterator[bytes]:
        for unit, tombstone in self._plan.clears:
            yield journal._json_line({"kind": "clear", "unit": unit, "at": tombstone})

    def seed(self) -> tuple[int, Mapping[str, str], Iterator[bytes]]:
        return self._plan.old_records, self._plan.old_tombstones, self._seeded()

    def _seeded(self) -> Iterator[bytes]:
        """Yield the replaced store's set lines in unit order, its own records and its override section's as one store (`accepted.joined`), each parsed from it and dumped."""
        overrides = self._live.overrides
        pending = sorted(overrides.records)
        at = 0
        for record in _own_records(self._old, self._live.name):
            unit = record["unit"]
            if unit in overrides:
                continue
            while at < len(pending) and pending[at] < unit:
                yield journal._json_line(journal._set_line(overrides.records[pending[at]]))
                at += 1
            yield journal._json_line(journal._set_line(record))
        for unit in pending[at:]:
            yield journal._json_line(journal._set_line(overrides.records[unit]))


@contextlib.contextmanager
def _held(path: Path) -> Iterator[BinaryIO | None]:
    """Hold the file at `path` open for reading, or None when there is none, so it can be read after a rename replaces it."""
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        yield None
        return
    with handle:
        yield handle


def _is_snapshot(store: Path, snapshot_path: Path) -> bool:
    """Return whether the live store at `store` holds the snapshot's bytes: it is the snapshot's own file, as it stays until a writer replaces it by rename, or another file with the same bytes (`output_sink.same_bytes`)."""
    try:
        ours, theirs = store.stat(), snapshot_path.stat()
    except FileNotFoundError:
        return False
    if (ours.st_dev, ours.st_ino) == (theirs.st_dev, theirs.st_ino):
        return True
    return output_sink.same_bytes(store, snapshot_path)


def _paired(tail: accepted.Tail | None, *, own: bool = False) -> dict:
    """Return the store document `accepted.pair` reads for the store `tail` describes, as `Store.document` gives it from `read_store`: its members but its own records, with a `verdicts` list that holds an entry exactly when its own does, since `pair` asks only whether the store holds a record, a tombstone or an override; with `own`, its own records and tombstones alone, the store a land with no prepared store starts from on an unchanged stamp. No store is an empty one."""
    if tail is None:
        return {}
    verdicts = [None] if tail.records else []
    if own:
        return {"verdicts": verdicts, "cleared": tail.members.get("cleared")}
    return {**tail.members, "verdicts": verdicts}


def _land_streamed(
    *,
    autosave: Path,
    journal_path: Path,
    run_dir: Path,
    prepared: Path | None,
    staged: Path | None,
    live: Path | None,
    atomic: bool,
    scan_state: Path | None,
    guard_orphans: bool,
    listed_path: Path,
    new_stamp: str,
    new_ids: frozenset[str],
) -> LandResult:
    """Land as `land` does, walking the stores as streams (`_plan`): without the lock over the snapshot S, the prepared store R and S in the live store's place, then under the lock (`_land_streamed_locked`), handing it that plan only when it stands, the live store C being the snapshot's bytes (`_is_snapshot`) and the walk having taken no time of day, and otherwise dropping it before the walk under the lock starts, so the two walks' plans are never held at once. It raises `_Unstreamable` before it writes anything for stores whose records do not stream by unit."""
    snapshot_path = run_dir / SNAPSHOT_NAME
    snapshot_name = f"the snapshot {snapshot_path}"
    ready = None
    r: _Side | None = None
    try:
        before = _tail_of(snapshot_path, snapshot_name)
        s = (
            _side_of(snapshot_path, before, snapshot_name)
            if before is not None
            else _Side(None, snapshot_name)
        )
        if prepared is not None:
            prepared_name = f"the prepared store {prepared}"
            ready = _tail_of(prepared, prepared_name)
            r = _side_of(prepared, ready, prepared_name) if ready is not None else None
    except MalformedStore as malformed:
        return _refuse_malformed(malformed)
    if r is None:
        same_stamp = before is not None and before.stamp == new_stamp
        r = _Side(snapshot_path, snapshot_name, s.cleared) if same_stamp else _Side(None, "")
    elif ready is not None and ready.stamp != new_stamp:
        return _refuse_prepared(ready.stamp, new_stamp)
    if staged is not None and live is None:
        raise ValueError("a staged corpus needs the live path it replaces")
    kept = prepared is not None and prepared_unchanged(prepared)
    journal_scan, live_ids = _journal_and_live(
        before is None or before.stamp != new_stamp,
        journal_path=journal_path,
        scan_state=scan_state,
        guard_orphans=guard_orphans,
        listed_path=listed_path,
        staged=staged,
        live=live,
        new_ids=new_ids,
    )
    plan = None
    if before is not None:
        plan = _plan(s, r, s, moved=before.stamp != new_stamp, new_ids=new_ids, live_ids=live_ids)

    try:
        with locked_store(autosave, quiet=True), _signals_blocked():
            started = time.perf_counter()
            if plan is not None and (plan.stamped or not _is_snapshot(autosave, snapshot_path)):
                plan = None
            result = _land_streamed_locked(
                autosave=autosave,
                journal_path=journal_path,
                run_dir=run_dir,
                before=before,
                s=s,
                ready=ready,
                r=r,
                plan=plan,
                new_stamp=new_stamp,
                new_ids=new_ids,
                staged=staged,
                live=live,
                atomic=atomic,
                journal_scan=journal_scan,
                guard_orphans=guard_orphans,
                listed_path=listed_path,
                live_ids=live_ids,
                kept=kept,
            )
            result.locked_s = time.perf_counter() - started
    except LandUnrecoverable as refused:
        return LandResult(False, str(refused))
    return result


def _land_streamed_locked(
    *,
    autosave: Path,
    journal_path: Path,
    run_dir: Path,
    before: accepted.Tail | None,
    s: _Side,
    ready: accepted.Tail | None,
    r: _Side,
    plan: _Plan | None,
    new_stamp: str,
    new_ids: frozenset[str],
    staged: Path | None,
    live: Path | None,
    atomic: bool,
    journal_scan: journal.ScanState | None,
    guard_orphans: bool,
    listed_path: Path,
    live_ids: frozenset[str] | None,
    kept: bool,
) -> LandResult:
    """The land's section under the store's lock, as `_land_locked` runs it over stores read whole: `plan` is the plan made without the lock when it stands, the live store C being the snapshot's bytes and that walk having taken no time of day (`_land_streamed`), and the result is written once as R's records stream past (`_write_result`); with no `plan`, C is read and checked and the walk runs again over S, R and C, writing the result as it goes (`_ResultWriter`), unless the land may leave the store in place or the orphan guard may refuse it, when it writes the result after the walk (`_write_result`) only when it lands one. The result is journaled by splice (`_Spliced`)."""
    if plan is not None:
        now, c = before, s
    else:
        live_name = f"the live store {autosave}"
        try:
            now = _tail_of(autosave, live_name)
            c = _side_of(autosave, now, live_name) if now is not None else _Side(None, live_name)
        except MalformedStore as malformed:
            return _refuse_malformed(malformed)
    before_stamp = before.stamp if before is not None else (now.stamp if now is not None else None)
    if now is None and before_stamp is not None:
        return LandResult(False, "the live store vanished since the snapshot")
    old_stamp = now.stamp if now is not None else None
    if old_stamp != before_stamp:
        return LandResult(
            False,
            f"the live store moved from {before_stamp} to {old_stamp} since the snapshot",
            old_stamp=old_stamp,
            new_stamp=new_stamp,
        )
    moved = old_stamp != new_stamp
    if ready is not None:
        starts = _paired(ready)
    else:
        starts = _paired(before, own=True) if r.source is not None else {}
    try:
        listed = accepted.read_stamp(listed_path)
        accepted.pair(_paired(now), listed, store=autosave)
        for name, held in (("snapshot", _paired(before)), ("prepared store", starts)):
            try:
                accepted.pair(held, listed, store=f"the pass's {name}")
            except accepted.Mismatch as mismatch:
                return LandResult(
                    False,
                    f"the accepted list changed since the pass's snapshot ({mismatch}); the next pass prepares the store against the list as it stands",
                    old_stamp=old_stamp,
                    new_stamp=new_stamp,
                )
        guarded = live_ids if moved and guard_orphans else None
        listed_prior, listed_orphaned = _listed_units(listed, c.overrides, guarded, new_ids)
    except accepted.Refused as refused:
        return LandResult(
            False, f"{refused}; the land moved nothing", old_stamp=old_stamp, new_stamp=new_stamp
        )
    field_ = listed.field()
    landing = run_dir / LANDING_NAME
    writer = None
    if plan is None:
        writes = not guard_orphans if moved else not kept
        writer = _ResultWriter(landing, new_stamp, field_) if writes else None
        try:
            plan = _plan(s, r, c, moved=moved, new_ids=new_ids, live_ids=live_ids, out=writer)
        except BaseException:
            if writer is not None:
                writer.discard()
            raise
    if moved and guard_orphans:
        share = orphan_share(plan.orphaned + listed_orphaned, plan.prior + listed_prior)
        if share is not None:
            return _refuse_orphans(share, listed_prior, old_stamp, new_stamp)
    same = plan.same and field_ == (now.accepted if now is not None else None)
    if (
        kept
        and not moved
        and _changes_nothing(same, journal_path, lambda: bool(plan.old_records or plan.old_tombstones))
    ):
        return _leave_store(
            autosave,
            run_dir,
            landing,
            staged,
            live,
            atomic,
            journal_path,
            old_stamp,
            new_stamp,
            records=plan.old_records,
            overlaid=plan.overlaid,
        )
    if writer is not None:
        head, spans = writer.head, writer.finish(plan)
    else:
        head, spans = _write_result(landing, r, plan, new_stamp, field_)
    if old_stamp is not None:
        write_orphans(autosave, old_stamp, plan.orphans)
    stash = stash_path_for(autosave, old_stamp) if moved and old_stamp is not None else None
    journal.repair_tail(journal_path)
    at = journal.now_stamp()
    marker = journal.unjournaled_marker_for(autosave)
    base = None
    if moved and old_stamp is not None:
        base = journal.base_due(journal.scan(journal_path, resume=journal_scan).events, at, marker=marker)
    intent_path = _write_intent(
        autosave, run_dir, landing, staged, live, old_stamp, new_stamp, stash, journal_path
    )
    if staged is not None:
        assert live is not None
        exchange_dirs(staged, live, atomic=atomic)
    if stash is not None and autosave.exists():
        link_stash(autosave, stash)
    with contextlib.suppress(OSError):
        os.link(landing, run_dir / LANDED_NAME)
    with _held(autosave) as old:
        os.replace(landing, autosave)
        journaled = journal.record_transition(
            journal_path,
            source="land",
            stamp=new_stamp,
            old_stamp=old_stamp,
            stashed=stash.name if stash is not None else None,
            at=at,
            base=base,
            accepted=field_,
            transition=_Spliced(plan, autosave, head, spans, old, c),
        )
    if journaled["base"]:
        marker.unlink(missing_ok=True)
    intent_path.unlink()
    return LandResult(
        True,
        old_stamp=old_stamp,
        new_stamp=new_stamp,
        records=plan.records,
        overlaid=plan.overlaid,
        orphaned=len(plan.orphans),
        skips_dropped=plan.skips,
        stash=stash.name if stash is not None else None,
        journal_base=journaled["base"] if journaled["recorded"] else None,
        journal_sets=journaled["sets"],
        journal_clears=journaled["clears"],
    )


def _live_ids(live: Path) -> frozenset[str]:
    """Return the served corpus's human unit ids, which the orphan guard counts the accepted units on, or none when no corpus is served."""
    try:
        return read_manifest(live)[1]
    except FileNotFoundError:
        return frozenset()


def _refuse_malformed(malformed: MalformedStore) -> LandResult:
    return LandResult(
        False,
        f"{malformed}, and the land refuses a store holding a record the carry refuses (`verdict_store.well_formed`) "
        "so that it never lands one; if it is the live store, restore a readable store from the journal with "
        "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`",
    )


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
    atomic: bool,
    journal_scan: journal.ScanState | None,
    guard_orphans: bool,
    listed_path: Path,
    live_ids: frozenset[str] | None,
    kept: bool = False,
) -> LandResult:
    try:
        now = read_store(autosave, f"the live store {autosave}")
    except MalformedStore as malformed:
        return _refuse_malformed(malformed)
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
    try:
        listed = accepted.read_stamp(listed_path)
        accepted.pair(now.document(), listed, store=autosave)
        for name, held in (("snapshot", before), ("prepared store", ready)):
            try:
                accepted.pair(held.document(), listed, store=f"the pass's {name}")
            except accepted.Mismatch as mismatch:
                return LandResult(
                    False,
                    f"the accepted list changed since the pass's snapshot ({mismatch}); the next pass prepares the store against the list as it stands",
                    old_stamp=old_stamp,
                    new_stamp=new_stamp,
                )
        guarded = live_ids if moved and guard_orphans else None
        listed_prior, listed_orphaned = _listed_units(listed, now.overrides, guarded, new_ids)
    except accepted.Refused as refused:
        return LandResult(
            False, f"{refused}; the land moved nothing", old_stamp=old_stamp, new_stamp=new_stamp
        )
    if moved and guard_orphans:
        overridden = {
            unit: record
            for unit, record in now.overrides.records.items()
            if unit in (live_ids or ()) and unit not in now.records
        }
        prior = [
            unit
            for held in (now.records, overridden)
            for unit, record in held.items()
            if record["verdict"] != "skip"
        ]
        orphaned = sum(1 for unit in prior if unit not in new_ids) + listed_orphaned
        share = orphan_share(orphaned, len(prior) + listed_prior)
        if share is not None:
            return _refuse_orphans(share, listed_prior, old_stamp, new_stamp)
    ready.accepted = listed.field()
    overlaid, orphans, skips = overlay(ready, before, now, new_ids=new_ids if moved else None)
    landing = run_dir / LANDING_NAME
    if kept and not moved and _leaves_store(ready, now, journal_path):
        return _leave_store(
            autosave,
            run_dir,
            landing,
            staged,
            live,
            atomic,
            journal_path,
            old_stamp,
            new_stamp,
            records=len(now.joined()[0]),
            overlaid=overlaid,
        )
    _write_file(landing, document_bytes(ready))
    if old_stamp is not None:
        write_orphans(autosave, old_stamp, orphans)
    stash = stash_path_for(autosave, old_stamp) if moved and old_stamp is not None else None
    journal.repair_tail(journal_path)
    at = journal.now_stamp()
    marker = journal.unjournaled_marker_for(autosave)
    base = None
    if moved and old_stamp is not None:
        base = journal.base_due(journal.scan(journal_path, resume=journal_scan).events, at, marker=marker)
    intent_path = _write_intent(
        autosave, run_dir, landing, staged, live, old_stamp, new_stamp, stash, journal_path
    )
    if staged is not None:
        assert live is not None
        exchange_dirs(staged, live, atomic=atomic)
    if stash is not None and autosave.exists():
        link_stash(autosave, stash)
    with contextlib.suppress(OSError):
        os.link(landing, run_dir / LANDED_NAME)
    os.replace(landing, autosave)
    old_records, old_cleared = now.joined()
    new_records, new_cleared = ready.joined()
    journaled = journal.record_transition(
        journal_path,
        source="land",
        stamp=new_stamp,
        old_stamp=old_stamp,
        old_verdicts=list(old_records.values()),
        new_verdicts=list(new_records.values()),
        old_cleared=old_cleared,
        new_cleared=new_cleared,
        stashed=stash.name if stash is not None else None,
        at=at,
        base=base,
        accepted=ready.accepted,
    )
    if journaled["base"]:
        marker.unlink(missing_ok=True)
    intent_path.unlink()
    return LandResult(
        True,
        old_stamp=old_stamp,
        new_stamp=new_stamp,
        records=len(new_records),
        overlaid=overlaid,
        orphaned=len(orphans),
        skips_dropped=skips,
        stash=stash.name if stash is not None else None,
        journal_base=journaled["base"] if journaled["recorded"] else None,
        journal_sets=journaled["sets"],
        journal_clears=journaled["clears"],
    )


def _refuse_orphans(share: str, listed_prior: int, old_stamp: str | None, new_stamp: str) -> LandResult:
    return LandResult(
        False,
        f"{share} of the live store's verdicts name no unit in the new corpus's triage index"
        + (
            f", counting {listed_prior:,} accepted units of the served corpus among them"
            if listed_prior
            else ""
        )
        + "; pass --allow-orphans to land anyway",
        old_stamp=old_stamp,
        new_stamp=new_stamp,
    )


def _leave_store(
    autosave: Path,
    run_dir: Path,
    landing: Path,
    staged: Path | None,
    live: Path | None,
    atomic: bool,
    journal_path: Path,
    old_stamp: str | None,
    new_stamp: str,
    *,
    records: int,
    overlaid: int,
) -> LandResult:
    """Leave the store in place, as the module docstring says a land whose verdict update changed nothing does: swap a staged corpus in under an intent marked `store_kept`, link the store as `LANDED_NAME`, and return the land's result, `records` the store's record count and `overlaid` the units saved during the pass."""
    swap_intent = None
    if staged is not None:
        assert live is not None
        swap_intent = _write_intent(
            autosave,
            run_dir,
            landing,
            staged,
            live,
            old_stamp,
            new_stamp,
            None,
            journal_path,
            store_kept=True,
        )
        exchange_dirs(staged, live, atomic=atomic)
    with contextlib.suppress(OSError):
        os.link(autosave, run_dir / LANDED_NAME)
    if swap_intent is not None:
        swap_intent.unlink()
    return LandResult(
        True, old_stamp=old_stamp, new_stamp=new_stamp, records=records, overlaid=overlaid, store_kept=True
    )


def _leaves_store(result: Store, now: Store, journal_path: Path) -> bool:
    """Return whether landing `result` over the live store `now` on its own stamp would change nothing the store or its journal holds (`_changes_nothing`)."""
    same = (result.records, result.cleared, result.overrides, result.accepted) == (
        now.records,
        now.cleared,
        now.overrides,
        now.accepted,
    )
    return _changes_nothing(same, journal_path, lambda: any(now.joined()))


def _changes_nothing(same: bool, journal_path: Path, holds: Callable[[], bool]) -> bool:
    """Return whether a land on an unchanged stamp whose result is `same` as the live store, its records, tombstones, override section and `accepted` field, would change nothing the store or its journal holds: the journal's diff then holds no line, and the journal is already there or the live store holds nothing (`holds`), so the land would write no seed base either (`journal.record_transition`)."""
    return same and (journal_path.exists() or not holds())


def _write_intent(
    autosave: Path,
    run_dir: Path,
    landing: Path,
    staged: Path | None,
    live: Path | None,
    old_stamp: str | None,
    new_stamp: str,
    stash: Path | None,
    journal_path: Path,
    *,
    store_kept: bool = False,
) -> Path:
    """Write the intent file beside `autosave` (`intent_path_for`) that says what the rest of a land's section does, for `recover_interrupted_land`, with the journal's inode and length as they stand, and return its path. `store_kept` marks a land that leaves the store in place and only swaps a staged corpus."""
    journal_stat = _stat(journal_path)
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
    if store_kept:
        intent["store_kept"] = True
    intent_path = intent_path_for(autosave)
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    _write_file(intent_path, (json.dumps(intent, indent=1) + "\n").encode())
    return intent_path


def _listed_units(
    listed: accepted.ListStamp,
    overrides: Overrides,
    live_ids: frozenset[str] | None,
    new_ids: frozenset[str],
) -> tuple[int, int]:
    """Stream the list once, when `live_ids` is given, and return how many listed units of the live corpus's human units the live store's override section (`overrides`) holds nothing for and how many of those the new corpus lacks, the orphan guard's two terms. `accepted.Malformed` comes from a line the stream reaches."""
    prior = orphaned = 0
    if listed.sha256 is None or live_ids is None:
        return prior, orphaned
    for unit, _verdict in accepted.iter_lines(listed.path):
        if unit in live_ids and unit not in overrides:
            prior += 1
            orphaned += unit not in new_ids
    return prior, orphaned


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
    """Raised by `recover_interrupted_land` when it can neither finish nor undo the land the intent file records: the land swapped the corpus in but neither its result nor the corpus it swapped out is left, so neither keeps the store on the served corpus (`reason` is `land-unrecoverable`), or the result or the store the recovery would lay in place and journal holds a record `verdict_store.well_formed` refuses (`reason` is `store-malformed`). The intent file stays, and every writer that takes the store's lock (`locked_store`) refuses with the message until a person restores, replaces or deletes the file it names, as the message says; the review server answers under `reason`."""

    def __init__(self, message: str, reason: str = "land-unrecoverable") -> None:
        super().__init__(message)
        self.reason = reason


def recover_interrupted_land(store: Path) -> Recovery | None:
    """Finish or drop the land the intent file beside `store` records, and return what was done, or None when there is no intent. The caller holds the store's lock, so the land that wrote the intent is dead.

    The journal is cut back to the length the intent recorded when it is still the same file, so whatever the land had appended, whole or in part, is removed. Then: a staged corpus that was not swapped in means the land never started to move anything, and the intent is dropped. A swapped corpus whose intent is marked `store_kept` comes from a land that left the store in place, so the recovery moves the swapped-out tree to its discard name and deletes the intent; it reads neither the store nor a result, replaces no store, appends nothing to the journal and leaves the marker as it is. Otherwise the land's result either still waits in its run directory, and it is moved over the store (after the store is linked to its stash name on a stamp change), or the store already holds it. Either way the store's content is appended to the journal as a base event naming the stash, which puts replay right after any store write the journal missed, so the marker that says it may have missed one (`journal.unjournaled_marker_for`) is removed, and the intent is deleted. A swapped corpus whose result is gone while the store is still on another stamp than the one the land moved to (the run directory was deleted by hand) is swapped back out, so the store stays on the served corpus, and the intent is dropped; when the swapped-out corpus is gone too, `LandUnrecoverable` is raised and the intent stays. A store or a result holding a record `verdict_store.well_formed` refuses raises `LandUnrecoverable` too, naming that file, the first such record and the intent (`_read_left`), and the intent stays: the refusal comes before the recovery replaces the store, swaps a corpus back or discards one, or appends to the journal, though the journal's cut-back and the last rename of an interrupted three-rename swap (`<live>.superseded`, the swapped-out corpus, renamed to the staged path) come first.
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
        if intent.get("store_kept"):
            discard = move_to_discard(staged, live) if staged.exists() else None
            intent_path.unlink()
            return Recovery(f"finished the land of {run}, which left the store in place", discard)
        landed = _read_left(store, landing, run, intent_path, journal_path)
        if not landing.exists() and landed is not None and landed.stamp != intent.get("new_stamp"):
            if not staged.exists():
                raise LandUnrecoverable(
                    f"the land of {run} swapped {staged} in for {live}, but its result ({landing}) and the corpus it swapped out are both gone, and {store} is still on {landed.stamp}; {intent_path} stays until one of them is restored"
                )
            exchange_dirs(staged, live)
            intent_path.unlink()
            return Recovery(
                f"swapped the corpus back out: the land of {run} had swapped it in, but its result is gone and the store is still on {landed.stamp}; {staged} keeps the new corpus"
            )
        discard = move_to_discard(staged, live) if staged.exists() else None
    else:
        landed = _read_left(store, landing, run, intent_path, journal_path)
    if landing.exists():
        if intent.get("stash") and store.exists():
            link_stash(store, store.with_name(intent["stash"]))
        os.replace(landing, store)
    if landed is not None and landed.stamp is not None:
        records, cleared = landed.joined()
        journal.record_transition(
            journal_path,
            source="land",
            stamp=landed.stamp,
            old_stamp=None,
            old_verdicts=[],
            new_verdicts=list(records.values()),
            new_cleared=cleared,
            stashed=intent.get("stash"),
            accepted=landed.accepted,
        )
        journal.unjournaled_marker_for(store).unlink(missing_ok=True)
    intent_path.unlink()
    return Recovery(f"finished the land of {run}", discard)


def _read_left(store: Path, landing: Path, run: str, intent_path: Path, journal_path: Path) -> Store | None:
    """Return what a recovery lays in place and journals: the land's result when it still waits at `landing`, and otherwise the store. One holding a record `verdict_store.well_formed` refuses raises `LandUnrecoverable` (`reason` `store-malformed`), naming it, the first such record and the intent, which stays, and the way out: for the result, deleting it, after which the recovery treats the land as one whose run directory is gone, and for the store, the restore of `store` from `journal_path` that writes a replacement without the lock."""
    left = landing if landing.exists() else store
    try:
        return read_store(left, f"the result {landing}" if left == landing else None)
    except MalformedStore as malformed:
        way_out = (
            f"deleting {landing} makes the next recovery leave {store} as it stands, swapping the corpus back out when "
            "the store is on another stamp than the land's, as for a land whose run directory is gone"
            if left == landing
            else f"`uv run python -m rebuild.tools.merge_verdicts --autosave {store} --journal {journal_path} "
            "--restore-as-of <time> --out <file>` writes a store from the journal without the lock to put in its place"
        )
        raise LandUnrecoverable(
            f"{malformed}, so the recovery of the land of {run} refuses to finish it, and {intent_path} stays: every "
            f"writer that takes the store's lock refuses with this message while {left} holds such a record; {way_out}",
            "store-malformed",
        ) from None


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
    """Hold the verdict store's lock (`store_lock.store_lock`, with its options), after finishing any land a dead holder left (`recover_interrupted_land`). Every writer of the store and its journal takes the lock this way, so none writes over a land that is half done. The swapped-out tree a recovery leaves is deleted after the lock is released. A recovery that refuses raises `LandUnrecoverable` before the caller's section runs, and each caller reports it as its own refusal: the merge (the verdict update's among them) and the re-key print it and exit 1, the land aborts with it as its reason, the cycle fails its step or stops the pass, and the review server answers with it (`serve.orphaned_land_reply`)."""
    with _recovered_lock(store, **lock_options) as (fd, _recovery):
        yield fd


def finish_interrupted_land(store: Path, **lock_options) -> Recovery | None:
    """Take the verdict store's lock as `locked_store` does, finish or drop any land a dead holder left, release the lock, delete the swapped-out tree the recovery left, and return what was done (`Recovery`), or None when no land was left."""
    with _recovered_lock(store, **lock_options) as (_fd, recovery):
        return recovery


def recover_if_orphaned(store: Path) -> bool:
    """Finish a land whose holder died, for the review server: with no intent file, or with the lock free, return True once nothing is left to finish; while the lock is busy, return False, because a land is still running. A recovery that refuses raises `LandUnrecoverable`."""
    if not intent_path_for(store).exists():
        return True
    try:
        with locked_store(store, blocking=False):
            pass
    except LockBusy:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    from rebuild.pipeline import spec_load

    refusal = spec_load.small_alphabet_refusal("the land")
    if refusal is not None:
        raise SystemExit(refusal)
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
    parser.add_argument(
        "--allow-orphans",
        action="store_true",
        help=f"land a stamp change even when more than {ORPHAN_SHARE_LIMIT:.0%}% of the live store's verdicts and its accepted units on the served corpus name no unit in the new corpus's triage index",
    )
    parser.add_argument(
        "--accepted",
        type=Path,
        help="the accepted list the live store pairs with (default: rebuild/accepted-units.txt beside --autosave, which only a store named verdicts-autosave.json has)",
    )
    args = parser.parse_args(argv)
    try:
        listed_path = args.accepted or accepted.path_for(args.autosave)
    except ValueError as error:
        parser.error(str(error))
    result = land(
        autosave=args.autosave,
        journal_path=args.journal,
        run_dir=args.run_dir,
        corpus=args.corpus,
        prepared=args.prepared,
        staged=args.staged,
        live=args.live,
        keep_discard=args.keep_discard,
        scan_state=args.scan_state,
        guard_orphans=not args.allow_orphans,
        accepted_path=listed_path,
    )
    _write_file(args.run_dir / REPORT_NAME, (json.dumps(result.as_dict(), indent=1) + "\n").encode())
    if not result.landed:
        print(f"land aborted: {result.reason}; nothing moved", flush=True)
        return 1
    if result.store_kept:
        what = "landed the corpus and left" if args.staged is not None else "left"
        print(
            f"{what} the store in place on {result.new_stamp}, since the verdict update changed nothing: "
            f"{result.records} verdicts, with the {result.overlaid} units saved during the pass already in it",
            flush=True,
        )
    else:
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
