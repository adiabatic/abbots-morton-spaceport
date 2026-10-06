"""The lock that every writer of the review app's verdict store holds: the review server around each POST and while it loads the store at boot, `merge_verdicts` (a merge and `--restore-as-of --apply`), `rekey_verdicts`, the artifact cycle's store snapshot and retention pass (around its stash deletions and journal rewrite), and the land that moves a pass's corpus and store into place (`rebuild.review.landing`). The writers other than a server POST take it through `landing.locked_store`, which first finishes a land whose holder died. It is an exclusive `fcntl.flock` on `<store>.lock` beside the store (verdicts-autosave.json.lock for the live store), so one lock guards the store file and its journal together, and the kernel releases it when its holder exits for any reason, `kill -9` included.

Every journal append is made under it, so a reader can scan the journal without the lock and handle only the tail appended since then under it (`journal.scan`, `journal.compact_prepare` and `journal.compact_finish`).

`hold_flock` is the primitive, and the artifact cycle's pass lock uses it too; `holder` reports who holds a lock without taking it, which the readiness checks use to name a running pass. Standard library only.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

POLL_S = 0.05


class LockBusy(Exception):
    """Raised when a lock taken without blocking, or with a timeout, is still held by another holder."""


def lock_path_for(store: Path) -> Path:
    store = Path(store)
    return store.with_name(store.name + ".lock")


@contextlib.contextmanager
def hold_flock(
    path: Path,
    *,
    blocking: bool = True,
    timeout: float | None = None,
    on_wait: Callable[[int], None] | None = None,
) -> Iterator[int]:
    """Hold an exclusive `flock` on `path`, creating the file and its directory when missing, and yield the open descriptor. When another holder has it, raise `LockBusy` at once if `blocking` is false; otherwise call `on_wait` with the descriptor once and wait, for at most `timeout` seconds when one is given, raising `LockBusy` when it runs out. The lock is per open file description, so two holders in one process exclude each other too."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not blocking:
                raise LockBusy(str(path)) from None
            if on_wait is not None:
                on_wait(fd)
            if timeout is None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                _wait_until(fd, path, time.monotonic() + timeout)
        yield fd
    finally:
        os.close(fd)


def holder(path: Path) -> str | None:
    """Return what the lock file at `path` records, the holder's pid, while another holder has its lock, and None when the lock is free or the file is missing. It tries a shared lock without blocking and drops it at once, so it never waits and never writes the file."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return os.pread(fd, 32, 0).decode("utf-8", "replace").strip() or "unknown"
        return None
    finally:
        os.close(fd)


def _wait_until(fd: int, path: Path, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockBusy(str(path)) from None
            time.sleep(POLL_S)


def announce_wait(lock_path: Path) -> None:
    """Print the one line a writer shows when it must wait for the verdict store's lock."""
    print(f"waiting for the verdict store's lock ({lock_path}); another writer holds it", flush=True)


def store_lock(
    store: Path,
    *,
    blocking: bool = True,
    timeout: float | None = None,
    quiet: bool = False,
) -> contextlib.AbstractContextManager[int]:
    """Hold the lock of the verdict store at `store` (`lock_path_for`), as `hold_flock` holds it. A holder that must wait says so once (`announce_wait`) unless `quiet`."""
    lock_path = lock_path_for(store)
    return hold_flock(
        lock_path,
        blocking=blocking,
        timeout=timeout,
        on_wait=None if quiet else lambda _fd: announce_wait(lock_path),
    )
