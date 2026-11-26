"""POSIX shared memory segments that a pool worker hands its parent, or the settle memo absorb its parent starts, instead of putting the bytes on its pickled result: `ship` copies the bytes into a new segment and returns what names it (`Shipped`), `Mapped` maps and unlinks a shipped segment at once, and `unlink_if_made` unlinks a segment its maker may have left behind. The pooled conformance sweep's units ship their fresh settle memo windows this way (`run_m1_gates._conformance_unit`, `run_m1_gates._absorb_memo`), and a sweep records its segments' names in a scratch directory of its own (`SEGMENT_RECORD`), which the next sweep over the same directory reads to unlink what a dead one left once its owner is no longer running (`owner_is_running`)."""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from multiprocessing import shared_memory

# The file in a pool's scratch directory that records the token and count its segments are named from (`run_m1_gates._SweepSegments`).
SEGMENT_RECORD = "segment-names"


@dataclass(frozen=True)
class Shipped:
    """Bytes a pool worker hands `run_m1`'s parent, or the settle memo absorb its parent starts, in a POSIX shared memory segment (`ship`) instead of on its pickled result: `name` is the segment and `lengths` the pieces laid end to end in it. A result reaches the parent through the pool's pipe as one message, which the parent reads into a buffer it grows to the message's size, and macOS's allocator keeps such buffers, and the large objects unpickled from them, dirty once freed, so what a worker sends would cost the parent several times its size in footprint (measured in `var/keep/ram-inventory/v4/11a-overrun/`). A segment's pages count in the footprint of a process that has them mapped and has touched them, and in no other: the pages a worker writes leave its footprint when it closes its mapping after the copy, and the parent's mapping touches none of them until the parent reads them."""

    name: str
    lengths: tuple[int, ...]


def ship(pieces: list[bytes], name: str) -> Shipped | None:
    """Copy `pieces` end to end into a new POSIX shared memory segment called `name`, dropping each from the list once it is copied, close this process's mapping, and return what names them, or None when there are no bytes, for which no segment is made. Making the segment registers it with the resource tracker, which a spawned pool worker shares with the parent that started it, and which unlinks any segment still registered once the parent and every worker have exited. Whoever maps the segment unlinks it as it maps it, and unregisters it in doing so (`Mapped`, in a settle memo absorb's worker, `run_m1_gates._absorb_memo`), and the parent unlinks any segment it named and nobody mapped as it exits (`run_m1_gates._SweepSegments`)."""
    lengths = tuple(len(piece) for piece in pieces)
    if not sum(lengths):
        return None
    segment = shared_memory.SharedMemory(name, create=True, size=sum(lengths))
    buffer = segment.buf
    assert buffer is not None
    try:
        at = 0
        for index, length in enumerate(lengths):
            buffer[at : at + length] = pieces[index]
            pieces[index] = b""
            at += length
    finally:
        segment.close()
    return Shipped(name, lengths)


def unlink_if_made(name: str, track: bool = True) -> None:
    """Unlink the shared memory segment `name` if a worker made it. Attaching registers the name with the resource tracker and unlinking unregisters it, so the tracker holds nothing for it afterward, whether or not the worker's own registration reached it. A dead run's segment is attached with `track` false, since the tracker it was registered with died with that run."""
    try:
        segment = shared_memory.SharedMemory(name, track=track)
    except FileNotFoundError:
        return
    segment.unlink()
    segment.close()


class Mapped:
    """A shipped segment mapped and unlinked at once, so that its name never outlives the mapping, with its pieces as views, by a settle memo absorb's worker. Attaching registers the name with the resource tracker again, which holds a name once, and unlinking unregisters it, so a segment mapped here leaves the tracker nothing to clean up. Its pages enter the mapping process's footprint as it reads them and leave at `close`."""

    def __init__(self, shipped: Shipped) -> None:
        self._segment = shared_memory.SharedMemory(shipped.name)
        self._segment.unlink()
        buffer = self._segment.buf
        assert buffer is not None
        self.size = sum(shipped.lengths)
        self.closed = False
        bounds = list(itertools.accumulate(shipped.lengths, initial=0))
        self.pieces = [buffer[first:stop] for first, stop in itertools.pairwise(bounds)]

    def close(self) -> None:
        for piece in self.pieces:
            piece.release()
        self.pieces = []
        self._segment.close()
        self.closed = True


def owner_is_running(pid: str) -> bool:
    """Return whether the process whose pid names a scratch directory is still running. A name that is not a positive integer counts as not running. A pid this process may not signal, or one too large to be a pid, counts as running, so the cleanup leaves the directory in place instead of raising."""
    if not pid.isdigit() or int(pid) < 1:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError, OverflowError:
        return True
    return True
