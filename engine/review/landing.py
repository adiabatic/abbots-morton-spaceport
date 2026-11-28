"""Land a pass's corpus and carry the verdict store onto it, beside a running review server.

A corpus-changing pass builds its corpus beside the served one, at engine/out/review.next, or promotes a staged one, and the land then moves it into place and carries the store onto it in one short section under the store's lock (`engine.review.store_lock`), which the review server takes for each save. It reads the new corpus's stamp and human unit ids, takes the lock, reads the live store whole, which is small (`engine.review.verdict_store`), and when the store is on another stamp than the new corpus carries it onto the new corpus by unit id (`verdict_store.carry`): a verdict whose unit the new corpus still has, its content unchanged and so its id, stays, a skip is dropped and leaves a tombstone, a verdict off the new corpus's human units goes to the orphan document of the stamp it was made on (`var/verdict-orphans/<stamp>.json`), which no pass refuses over, and a tombstone stays on the new corpus's human units alone. Stop signals are blocked for the section. It swaps the corpus in with one `renamex_np(RENAME_SWAP)` call (`exchange_dirs`, with a three-rename fallback through `<live>.superseded`) and then writes the carried store over the live one (`verdict_store.write_store`). A store already on the new stamp, as on a promotion of a corpus built for it, is left as it is. A store holding a record `verdict_store.well_formed` refuses aborts the land before anything moves, naming the store and the record.

A save the server applies before the land takes the lock is in the store the land reads; one made while the land holds it gets a retryable 503 and waits in the tab's outbox; one made after it reaches the carried store, and a tab that saves on the old corpus's stamp has its save carried onto the store by unit id (rule 2 in `engine.review.verdict_store`). A land killed between the swap and the store write leaves the new corpus served beside a store on the old stamp, which the next save stamped for the served corpus carries onto it in the same way (rule 3 there), as the next pass's land does.

After the lock is released the swapped-out tree is renamed to a discard name beside the served corpus (`move_to_discard`) and deleted, or with `--keep-discard` left for the caller, which tells the open tabs to move before it deletes the tree; under the lock a tree is only ever renamed, never deleted. The land writes its report, `land-report.json`, into `--run-dir`.

`LAND_PROTOCOL` and `code_digest` are what the review server advertises on /capabilities, and what the artifact cycle compares with the working tree before it keeps a listening server running through a pass. This module imports only the store, its lock and the spec loader, whose refusal its command line runs first, so the land refuses while the alphabet switch (`spec_load.RUNES_ENV`) is set outside the fault kit (`spec_load.small_alphabet_refusal`).

Usage: uv run python -m engine.review.landing --autosave FILE --run-dir DIR --corpus DIR [--staged DIR --live DIR [--keep-discard]]
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
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.review.store_lock import store_lock
from engine.review.verdict_store import (
    MalformedStore,
    append_orphans,
    carry,
    latest_by_unit,
    orphans_dir_for,
    parse_cleared,
    read_store_document,
    write_store,
)

LAND_PROTOCOL = 2
PROTOCOL_MODULES = (
    "serve.py",
    "verdict_store.py",
    "status.py",
    "store_lock.py",
    "landing.py",
)
FRESHNESS_MODULES = (
    "pipeline/fingerprint.py",
    "review/shards.py",
)
# The modules the server answers its other routes from, under `review_dir`: the kerning view's routes and the block writer they write through. They take no part in the land, but a server running an older copy of one, or of a module one runs, would answer the app with it until the cycle restarted it, so the digest covers them and the trees they run (`served_paths`).
SERVED_MODULES = ("kern_routes.py", "kern_writer.py")
REPORT_NAME = "land-report.json"
RENAME_SWAP = 0x2
RENAME_EXCHANGE = 0x2
AT_FDCWD = -100
UNSUPPORTED_SWAP_ERRNOS = frozenset({errno.ENOTSUP, errno.EINVAL, errno.ENOSYS})
BLOCKED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def served_paths(review_dir: Path) -> list[Path]:
    """Return the files the server's other routes run, as the code digest covers them: `SERVED_MODULES` under `review_dir`, and every module of the pipeline and validation trees beside it (`review_dir`'s parent's `pipeline/` and `validation/`). The kerning routes run the kern stage, the spec loader, the table reader, the inventory's Manual digest and the build driver's minting, and those import across both trees, so the trees are named whole, as `fingerprint.pipeline_code_paths` names them for the corpus, without the crate and the font compile, which the routes never run. The modules of engine/tools the trees import are width and telemetry code or reached only through functions the routes never call (`engine/test_landing.py` walks the routes' imports to check it), so an edit there cannot change an answer. Globbing the two trees costs a fraction of what walking their imports would at every boot and every pass's probe."""
    review_dir = Path(review_dir)
    paths = [review_dir / name for name in SERVED_MODULES]
    for tree in ("pipeline", "validation"):
        paths += sorted((review_dir.parent / tree).glob("*.py"))
    return paths


def code_digest(review_dir: Path) -> str:
    """Return the digest of the modules whose behavior the land protocol depends on (`PROTOCOL_MODULES` under `review_dir`), of the ones the server answers its other routes from and the pipeline and validation trees those run (`served_paths`), and of the ones the server's /status judges the served corpus's freshness with (`FRESHNESS_MODULES`, under `review_dir`'s parent): the fingerprint components and the check of a finished corpus (`shards.missing_shards`). The server takes it at boot from the files it loaded, and the cycle from the working tree, so a server whose code differs from the tree's is never trusted to run through a pass. A server started before a change to the freshness modules would go on judging the corpus by the components it loaded and could call a stale corpus ready, so the next pass that writes under it restarts it."""
    review_dir = Path(review_dir)
    paths = [(name, review_dir / name) for name in PROTOCOL_MODULES]
    paths += [(path.relative_to(review_dir.parent).as_posix(), path) for path in served_paths(review_dir)]
    paths += [(name, review_dir.parent / name) for name in FRESHNESS_MODULES]
    digest = hashlib.sha256()
    for name, path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        digest.update(f"{name}\t{hashlib.sha256(data).hexdigest()}\n".encode())
    return digest.hexdigest()


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
    """Swap the trees at `staged` and `live`, so the live path holds the staged tree and the staged path the old one. One atomic exchange call where the platform has one and the filesystem accepts it; otherwise (no such call, a filesystem that answers it with one of `UNSUPPORTED_SWAP_ERRNOS`, or `atomic` false) three renames through `<live>.superseded`, which the cycle's `recover_superseded_corpus` finishes when a pass dies between them. A leftover `.superseded` beside a live tree is renamed aside to a discard name (`move_to_discard`), never deleted here, because this runs under the store's lock."""
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
    """Return where a swapped-out tree waits to be deleted: `<live>.discard` beside the served corpus, or `<live>.discard-<n>` while a leftover holds that name (`discard_paths`). A delete cut short leaves the tree under that name, which no pass takes for a corpus it could land, and the next pass deletes it."""
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


@contextlib.contextmanager
def _signals_blocked() -> Iterator[None]:
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, BLOCKED_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextlib.contextmanager
def locked(store: Path, *, quiet: bool = False) -> Iterator[None]:
    """Hold the store's lock, waiting for it, and saying once that it waits unless `quiet` (`store_lock.store_lock`): the path every writer of the store but the review server's saves takes, the land and the accept among them. The server answers a save made meanwhile with a retryable 503, which the tab sends again."""
    with store_lock(Path(store), quiet=quiet):
        yield


@dataclass(frozen=True)
class Read:
    """The store as the land reads it: its stamp (None with no store), its latest record by unit and its tombstones."""

    stamp: str | None
    records: dict[str, dict]
    cleared: dict[str, str]


def read_store(path: Path) -> Read:
    """Read the store at `path` whole, which is small. A missing file, or one that is not an ams-review-verdicts/1 document, reads as no store; one holding a record `verdict_store.well_formed` refuses raises `MalformedStore`, naming it and the record."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return Read(None, {}, {})
    data = read_store_document(raw, f"the store {path}")
    if data is None:
        return Read(None, {}, {})
    records = latest_by_unit(data["verdicts"])
    return Read(data["manifest_generated_at"], records, parse_cleared(data))


def read_manifest(corpus: Path) -> tuple[str, frozenset[str]]:
    """Return the corpus's stamp, its manifest's `generated_at`, and its human unit ids."""
    manifest = json.loads((Path(corpus) / "manifest.json").read_text())
    ids = manifest.get("human_unit_ids") or []
    return manifest["generated_at"], frozenset(unit for unit in ids if isinstance(unit, str))


@dataclass
class LandResult:
    """What a land did. `landed` is False when it aborted, and `reason` says why. `carried` says whether it carried the store onto a new stamp; `records` counts the verdicts the store holds after it, `orphaned` those it sent to the orphan document and `skips_dropped` the skips it dropped; `discard` names the swapped-out tree left for the caller, and `locked_s` how long it held the store's lock."""

    landed: bool
    reason: str = ""
    old_stamp: str | None = None
    new_stamp: str | None = None
    carried: bool = False
    records: int = 0
    orphaned: int = 0
    skips_dropped: int = 0
    discard: str | None = None
    locked_s: float = 0.0

    def as_dict(self) -> dict:
        return {**asdict(self), "locked_s": round(self.locked_s, 3)}


def land(
    autosave: Path,
    corpus: Path,
    *,
    staged: Path | None = None,
    live: Path | None = None,
    keep_discard: bool = False,
) -> LandResult:
    """Land the corpus at `corpus` and carry the store at `autosave` onto it (the module docstring): swap `staged` in for `live` when both are given, and carry the store when it is on another stamp than the corpus. Nothing moves when the land aborts."""
    autosave, corpus = Path(autosave), Path(corpus)
    new_stamp, human_ids = read_manifest(corpus)
    swapped_out: Path | None = None
    with locked(autosave, quiet=True), _signals_blocked():
        started = time.monotonic()
        try:
            found = read_store(autosave)
        except MalformedStore as malformed:
            return LandResult(
                False,
                f"{malformed}, and the land refuses a store holding such a record so that it never lands one; "
                "replace it with a readable store, or move it aside so the review server starts an empty one",
            )
        moved = None
        if found.stamp is not None and found.stamp != new_stamp:
            moved = carry(found.records, found.cleared, human_ids)
        if staged is not None and live is not None:
            exchange_dirs(Path(staged), Path(live))
            swapped_out = move_to_discard(Path(staged), Path(live))
        if moved is not None:
            assert found.stamp is not None
            if moved.orphans:
                append_orphans(orphans_dir_for(autosave), found.stamp, moved.orphans)
            write_store(autosave, new_stamp, moved.records, moved.cleared)
        locked_s = time.monotonic() - started
    discard = None
    if swapped_out is not None:
        if keep_discard:
            discard = str(swapped_out)
        else:
            shutil.rmtree(swapped_out, ignore_errors=True)
    return LandResult(
        True,
        old_stamp=found.stamp,
        new_stamp=new_stamp,
        carried=moved is not None,
        records=len(moved.records) if moved is not None else len(found.records),
        orphaned=len(moved.orphans) if moved is not None else 0,
        skips_dropped=len(moved.dropped) if moved is not None else 0,
        discard=discard,
        locked_s=locked_s,
    )


def main(argv: list[str] | None = None) -> int:
    from engine.pipeline import spec_load

    refusal = spec_load.small_alphabet_refusal("the land")
    if refusal is not None:
        raise SystemExit(refusal)
    parser = argparse.ArgumentParser(description="Land a pass's corpus and carry the verdict store onto it.")
    parser.add_argument("--autosave", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--corpus", type=Path, required=True, help="the corpus whose stamp and human ids land"
    )
    parser.add_argument("--staged", type=Path, help="the corpus to swap in")
    parser.add_argument("--live", type=Path, help="the served corpus it replaces")
    parser.add_argument(
        "--keep-discard",
        action="store_true",
        help="leave the swapped-out tree under its discard name for the caller to delete, named in the report",
    )
    args = parser.parse_args(argv)
    result = land(
        args.autosave, args.corpus, staged=args.staged, live=args.live, keep_discard=args.keep_discard
    )
    args.run_dir.mkdir(parents=True, exist_ok=True)
    report = args.run_dir / REPORT_NAME
    tmp = report.with_name(report.name + ".tmp")
    tmp.write_text(json.dumps(result.as_dict(), indent=1) + "\n")
    os.replace(tmp, report)
    if not result.landed:
        print(f"land aborted: {result.reason}; nothing moved", flush=True)
        return 1
    what = "the corpus" if args.staged is not None else "nothing"
    if result.carried:
        print(
            f"landed {what} and carried the store onto {result.new_stamp}: {result.records} verdicts, "
            f"{result.orphaned} orphaned, {result.skips_dropped} skips dropped",
            flush=True,
        )
    else:
        print(
            f"landed {what} on {result.new_stamp}; the store's {result.records} verdicts stay as they are",
            flush=True,
        )
    print(f"[t] land-locked {result.locked_s:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
