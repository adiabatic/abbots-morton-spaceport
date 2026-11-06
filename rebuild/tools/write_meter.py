"""SSD-write readings for a cycle pass, a fault-kit run or a memory-series step, in bytes, from the kernel's counters for a whole process tree, so a child's writes still count after it exits. `read()` returns a `WriteReading`, and `delta(earlier, later)` returns the bytes written between two readings as a `WriteDelta` that names the basis its figure rests on.

On Darwin the source is the reading process's resource coalition, which the kernel keeps for a process tree: a child inherits its parent's coalition, a new session does not leave it, and the coalition keeps an exited member's counters, so one read covers every process a pass has run, those that uv, pytest-xdist and cargo reaped included. `proc_pidinfo`'s coalition flavor gives a pid's coalition and `coalition_info_resource_usage` reads its counters, without root. That call is a private libsystem export, and `_COALITION_FIELDS` is the leading part of XNU's `struct coalition_resource_usage`, which no public header declares, so every reading checks the layout it reads (below).

The figure is the coalition's logical writes, immediate plus deferred plus metadata less invalidated (`WriteReading.logical`), which the kernel counts at `write()` time whoever later flushes the pages. `byteswritten` (`WriteReading.diskio`) is kept beside it and reads low: it counts only the I/O the writer's own threads issue, so a file held open while the kernel flushed it reads 0 of its 4 MB. Logical writes over-count sub-page writes (by a quarter for 4 KB writes into 16 KB pages) and count each rewrite of a page still in the cache at full size, and read within 0.25% for the 64 KB and 1 MiB writes that carry nearly all of a pass's bytes. Writes to the null device, into a drained FIFO and by an APFS clone count nothing.

Each reading also scans every pid (`proc_listallpids`) for the coalition's live members and records each one's pid, parent, start time and own `ri_logical_writes` as a `Member`, reading `proc_pid_rusage` into `peak_rss`'s `rusage_info_v4` structure. A process another user owns, such as a terminal's `login`, gives its parent through the short BSD-info flavor, and neither its start time nor its writes.

`delta` sorts the members of both readings by their relation to the earlier reading's process, the reader: the reader and its descendants are its tree, the members on its chain of parents are its ancestors, and every other member is an outsider. A member new in the later reading takes the side of its nearest parent the earlier reading classified, so a new child of an ancestor is an outsider; one whose parent is no member, an orphan the kernel reparented to launchd, counts as the reader's, which keeps the bound an upper one. The basis is then one of three:

- `own`: no member is an outsider, as in a one-shot launchd job (`job_launch`). `bytes` is the coalition's logical delta, the exact writes of the reader's tree and its ancestors.
- `shared`: some member is an outsider, as in a terminal's coalition, where every tab's processes sit. `bytes` is the coalition's delta less each outsider's own writes over the window (`outsiders_bytes`), an upper bound over a pass: an outsider gone by the later reading, one this user cannot read, one first seen in the later reading that started before the earlier one, and one that started and exited between the readings leave their writes in it, and `outsiders_unread` counts the first three kinds, which a reading saw. An outsider first seen in the later reading that started after the earlier one comes off at its whole counter. Over a window of tens of milliseconds beside an outsider's writes the figure has read below a member's own writes (958,464 B for a child's 1 MiB beside 7.4 MB of another process's writes), so a short window's shared figure is approximate.
- `device`: either reading failed its self-check, the two readings are of different coalitions, a coalition field fell between them, or the platform has no coalition. `bytes` is the device's delta, machine-wide, and `warning` says why.

`read(named=pid)` also records that process's own counter, whether or not it is in the coalition, so `delta` reports its writes over the window apart as `named_bytes`. It serves the review server, which runs in its own session and outlives the pass that started it, and which may sit in the pass's coalition, in the shared coalition as another pass's orphan, or in none. When it is a member, its share comes out of `bytes`, and it is never counted as an outsider. `delta` takes the later reading's named process; `named_bytes` is None when that process's share over the window cannot be read, and the process is then classified like any other member.

A reading checks itself and records what failed in `check`. It reads the coalition before and after its scan, and no field the figure rests on (`_RISING_FIELDS`: the two task counts, `byteswritten` and the four logical-write fields) may fall between the two; the others go unchecked, since `cpu_time` can fall by microseconds as a task exits. The scan's member count must lie within what `tasks_started` less `tasks_exited` allows over the scan, which is that difference exactly when no task started or exited meanwhile, and may fall short of it by the listed pids the scan could not place because their process was exiting (ESRCH), whatever their coalition: a process tearing down its memory still counts as its coalition's for tens of milliseconds after its pid stops answering. The reader must be among the members, and the coalition's logical writes must be at least its live members' own, which a write field read from the wrong slot would likely fail. A reading that fails scans again after each pause in `_SCAN_RETRY_PAUSES_S` until the check passes, since the counters can count a task the kernel is still starting before a scan can find it, and keeps the last failure. An OS update can move a field of the private layout; the check turns that into the `device` basis and a warning rather than a wrong figure, and `rebuild/test_write_meter.py` reads a child's mebibyte back from the coalition.

`read(device=True)` also reads the machine's counters from one `ioreg` run: the internal SSD's `Bytes (Write)` from the block-storage driver above BSD `disk0` (`device`), and the `Bytes written to block device` of the APFS volume with the `VM` role under it (`swap`), which is swap in bytes. Both are machine-wide, and `delta` reports their deltas without netting either. `device=False` skips the run, for a poll at a step's spawn and reap, where a reading is a few syscalls a process.

The callers print `warning` as a `[warn]` line. The cycle's step lines take `delta(at_spawn, at_reap).bytes` as `written_bytes`, and its run line's `written` block takes `basis`, `bytes`, `diskio_bytes`, `outsiders_bytes` and `outsiders_unread` (in the shared basis), `named_bytes` as `review_server_bytes`, `device_bytes` and `swap_bytes` from `delta(at_start, at_end)`. The meter writes no file.

On a platform without a coalition, `read()` records why in `check` and reads no device, and `delta` returns the `device` basis with `bytes` None. The module imports only the standard library and `peak_rss`, which imports only the standard library, so a driver can import it without widening its import closure.
"""

from __future__ import annotations

import ctypes
import errno
import functools
import os
import plistlib
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from rebuild.tools import peak_rss

Basis = Literal["own", "shared", "device"]

_PROC_PIDTBSDINFO = 3
_PROC_PIDT_SHORTBSDINFO = 13
_PROC_PIDCOALITIONINFO = 20
_RUSAGE_INFO_V4 = 4
_COALITION_FIELDS = (
    "tasks_started",
    "tasks_exited",
    "time_nonempty",
    "cpu_time",
    "interrupt_wakeups",
    "platform_idle_wakeups",
    "bytesread",
    "byteswritten",
    "gpu_time",
    "cpu_time_billed_to_me",
    "cpu_time_billed_to_others",
    "energy",
    "logical_immediate_writes",
    "logical_deferred_writes",
    "logical_invalidated_writes",
    "logical_metadata_writes",
)
_RISING_FIELDS = (
    "tasks_started",
    "tasks_exited",
    "byteswritten",
    "logical_immediate_writes",
    "logical_deferred_writes",
    "logical_invalidated_writes",
    "logical_metadata_writes",
)
_USAGE_WORDS = 128
_SCAN_RETRY_PAUSES_S = (0.005, 0.02, 0.08, 0.2)
_IOREG = ("/usr/sbin/ioreg", "-r", "-c", "IOBlockStorageDriver", "-a", "-l", "-w0")
_IOREG_TIMEOUT_S = 30
_DEVICE_BSD_NAME = "disk0"
_SWAP_ROLE = "VM"

_TREE = "tree"
_ANCESTOR = "ancestor"
_OUTSIDER = "outsider"


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = (
        [
            (name, ctypes.c_uint32)
            for name in (
                "pbi_flags",
                "pbi_status",
                "pbi_xstatus",
                "pbi_pid",
                "pbi_ppid",
                "pbi_uid",
                "pbi_gid",
                "pbi_ruid",
                "pbi_rgid",
                "pbi_svuid",
                "pbi_svgid",
                "rfu_1",
            )
        ]
        + [("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32)]
        + [(name, ctypes.c_uint32) for name in ("pbi_nfiles", "pbi_pgid", "pbi_pjobc", "e_tdev", "e_tpgid")]
        + [
            ("pbi_nice", ctypes.c_int32),
            ("pbi_start_tvsec", ctypes.c_uint64),
            ("pbi_start_tvusec", ctypes.c_uint64),
        ]
    )


class _ProcBsdShortInfo(ctypes.Structure):
    _fields_ = (
        [(name, ctypes.c_uint32) for name in ("pbsi_pid", "pbsi_ppid", "pbsi_pgid", "pbsi_status")]
        + [("pbsi_comm", ctypes.c_char * 16)]
        + [
            (name, ctypes.c_uint32)
            for name in (
                "pbsi_flags",
                "pbsi_uid",
                "pbsi_gid",
                "pbsi_ruid",
                "pbsi_rgid",
                "pbsi_svuid",
                "pbsi_svgid",
                "pbsi_rfu",
            )
        ]
    )


@dataclass(frozen=True)
class Member:
    """One live process at a reading: its pid, its parent's pid, its start time in seconds since the epoch (None for a process another user owns), and its own logical writes since it started (`ri_logical_writes`, None where this user cannot read them). `key`, the pid with the start time, tells a process from a later one that reuses its pid."""

    pid: int
    ppid: int
    start: float | None
    logical: int | None

    @property
    def key(self) -> tuple[int, float | None]:
        return (self.pid, self.start)


@dataclass(frozen=True)
class WriteReading:
    """One reading of the meter. `pid` is the reader, `at` is `time.monotonic()` and `wall` the UTC `time.time()` stamp, both taken as the reading starts. `coalition` is the reader's resource coalition id, `usage` the coalition's counters by `_COALITION_FIELDS` name as read after the scan, `members` its live members, and `check` None when the reading passed its self-check or what failed otherwise. `named` is the named process's own record. `device` and `swap` are the machine's counters, None when the reading skipped them or could not read them."""

    pid: int
    at: float
    wall: float
    coalition: int | None
    usage: Mapping[str, int] | None
    members: tuple[Member, ...]
    check: str | None
    named: Member | None
    device: int | None
    swap: int | None

    @property
    def logical(self) -> int | None:
        """The coalition's logical writes: immediate plus deferred plus metadata, less invalidated."""
        return None if self.usage is None else _logical(self.usage)

    @property
    def diskio(self) -> int | None:
        """The coalition's `byteswritten`, the I/O its members' own threads issued."""
        return None if self.usage is None else self.usage["byteswritten"]


@dataclass(frozen=True)
class WriteDelta:
    """The bytes written between two readings. `basis` is `own`, `shared` or `device` (the module docstring says what each means). `bytes` is the figure, None when the basis is `device` and the device was not read. `diskio_bytes` is the coalition's `byteswritten` delta, None in the device basis. `outsiders_bytes` is what the outsiders wrote and came out of `bytes`, and `outsiders_unread` the outsiders whose share stayed in it; both are 0 in the own basis and None in the device one. `named_bytes` is the named process's own writes over the window. `device_bytes` and `swap_bytes` are the machine-wide deltas. `seconds` is the time between the readings, and `warning` says why the basis is `device`, None otherwise."""

    basis: Basis
    bytes: int | None
    diskio_bytes: int | None
    outsiders_bytes: int | None
    outsiders_unread: int | None
    named_bytes: int | None
    device_bytes: int | None
    swap_bytes: int | None
    seconds: float
    warning: str | None


def _logical(usage: Mapping[str, int]) -> int:
    return (
        usage["logical_immediate_writes"]
        + usage["logical_deferred_writes"]
        + usage["logical_metadata_writes"]
        - usage["logical_invalidated_writes"]
    )


@functools.cache
def _libc() -> ctypes.CDLL | None:
    if sys.platform != "darwin":
        return None
    try:
        # A scan makes a call a process. A CDLL call releases the GIL and can wait a switch interval to take it back from a busy thread, which stretched a 2 ms scan to 5 s beside four threads spawning processes; PyDLL keeps the GIL through each call, all of them syscalls of microseconds.
        libc = ctypes.PyDLL(None, use_errno=True)
        libc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        libc.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
        libc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        libc.coalition_info_resource_usage.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t]
    except OSError, AttributeError:
        return None
    return libc


def _pidinfo(libc: ctypes.CDLL, pid: int, flavor: int, into: ctypes.Structure | ctypes.Array[Any]) -> bool:
    return libc.proc_pidinfo(pid, flavor, 0, ctypes.byref(into), ctypes.sizeof(into)) == ctypes.sizeof(into)


def _coalition_id(libc: ctypes.CDLL, pid: int) -> int | None:
    ids = (ctypes.c_uint64 * 5)()
    ctypes.set_errno(0)
    return int(ids[0]) if _pidinfo(libc, pid, _PROC_PIDCOALITIONINFO, ids) else None


def _usage(libc: ctypes.CDLL, coalition: int) -> dict[str, int] | None:
    words = (ctypes.c_uint64 * _USAGE_WORDS)()
    if libc.coalition_info_resource_usage(coalition, ctypes.byref(words), ctypes.sizeof(words)) != 0:
        return None
    return {name: int(words[index]) for index, name in enumerate(_COALITION_FIELDS)}


def _all_pids(libc: ctypes.CDLL) -> list[int]:
    count = libc.proc_listallpids(None, 0)
    while count > 0:
        capacity = count + 64
        pids = (ctypes.c_int * capacity)()
        count = libc.proc_listallpids(ctypes.byref(pids), ctypes.sizeof(pids))
        if 0 <= count < capacity:
            return list(pids[:count])
    return []


def _member(libc: ctypes.CDLL, pid: int) -> Member | None:
    info = _ProcBsdInfo()
    short = _ProcBsdShortInfo()
    if _pidinfo(libc, pid, _PROC_PIDTBSDINFO, info):
        ppid, start = int(info.pbi_ppid), info.pbi_start_tvsec + info.pbi_start_tvusec / 1e6
    elif _pidinfo(libc, pid, _PROC_PIDT_SHORTBSDINFO, short):
        ppid, start = int(short.pbsi_ppid), None
    else:
        return None
    rusage = peak_rss._RusageInfoV4()
    readable = libc.proc_pid_rusage(pid, _RUSAGE_INFO_V4, ctypes.byref(rusage)) == 0
    return Member(pid, ppid, start, int(rusage.ri_logical_writes) if readable else None)


def self_check(
    before: Mapping[str, int],
    after: Mapping[str, int],
    found: int,
    exiting: int,
    members: tuple[Member, ...],
    reader: int,
) -> str | None:
    """Return what a reading's self-check failed, or None when it passed. `before` and `after` are the coalition's counters read either side of the scan, `found` the number of pids the scan saw in the coalition, `exiting` the number of listed pids it could not place because their process was exiting, and `members` the records it read for them. The module docstring says what the check asks."""
    for name in _RISING_FIELDS:
        if after[name] < before[name]:
            return f"the coalition's {name} fell from {before[name]} to {after[name]} within one reading"
    least = before["tasks_started"] - after["tasks_exited"]
    most = after["tasks_started"] - before["tasks_exited"]
    if not least <= found + exiting or found > most:
        span = f"{least}" if least == most else f"{least} to {most}"
        unplaced = (
            f", and could not place {exiting} exiting {'pid' if exiting == 1 else 'pids'}" if exiting else ""
        )
        return f"tasks_started less tasks_exited puts {span} tasks in the coalition, but the scan found {found}{unplaced}"
    if all(member.pid != reader for member in members):
        return "the scan did not find the reader in its own coalition"
    own = sum(member.logical for member in members if member.logical is not None)
    if own > _logical(after):
        return f"the members' own logical writes ({own} B) exceed the coalition's ({_logical(after)} B)"
    return None


def _scan(
    libc: ctypes.CDLL, coalition: int, reader: int
) -> tuple[Mapping[str, int] | None, tuple[Member, ...], str | None]:
    before = _usage(libc, coalition)
    found = 0
    exiting = 0
    members: list[Member] = []
    for pid in _all_pids(libc):
        place = _coalition_id(libc, pid)
        if place is None:
            if ctypes.get_errno() == errno.ESRCH:
                exiting += 1
        elif place == coalition:
            found += 1
            member = _member(libc, pid)
            if member is not None:
                members.append(member)
    after = _usage(libc, coalition)
    if before is None or after is None:
        return None, (), f"coalition_info_resource_usage failed (errno {ctypes.get_errno()})"
    return after, tuple(members), self_check(before, after, found, exiting, tuple(members), reader)


def _read_coalition(
    reader: int,
) -> tuple[int | None, Mapping[str, int] | None, tuple[Member, ...], str | None]:
    libc = _libc()
    if libc is None:
        if sys.platform != "darwin":
            return None, None, (), f"{sys.platform} has no resource coalition"
        return None, None, (), "libsystem has no coalition_info_resource_usage"
    coalition = _coalition_id(libc, reader)
    if coalition is None:
        return None, None, (), "proc_pidinfo gave the reader no resource coalition"
    usage, members, check = _scan(libc, coalition, reader)
    for pause in _SCAN_RETRY_PAUSES_S:
        if check is None or usage is None:
            break
        time.sleep(pause)
        usage, members, check = _scan(libc, coalition, reader)
    return coalition, usage, members, check


def _walk(node: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield node
    for child in node.get("IORegistryEntryChildren", ()):
        yield from _walk(child)


def device_counters(drivers: Sequence[Mapping[str, Any]]) -> tuple[int | None, int | None]:
    """Return the internal SSD's `Bytes (Write)` and the swap volume's `Bytes written to block device` from the block-storage drivers of an `ioreg -a -l` tree (`_IOREG`), each None when the tree lacks it. The SSD is the driver whose media child is BSD `disk0`; the swap volume is the APFS volume under it whose roles include `VM`."""
    for driver in drivers:
        if not any(
            child.get("BSD Name") == _DEVICE_BSD_NAME for child in driver.get("IORegistryEntryChildren", ())
        ):
            continue
        device = (driver.get("Statistics") or {}).get("Bytes (Write)")
        swap = next(
            (
                (node.get("Statistics") or {}).get("Bytes written to block device")
                for node in _walk(driver)
                if node.get("IOObjectClass") == "AppleAPFSVolume" and _SWAP_ROLE in (node.get("Role") or ())
            ),
            None,
        )
        return (
            None if device is None else int(device),
            None if swap is None else int(swap),
        )
    return None, None


def _read_device() -> tuple[int | None, int | None]:
    if sys.platform != "darwin":
        return None, None
    try:
        reply = subprocess.run(_IOREG, capture_output=True, check=True, timeout=_IOREG_TIMEOUT_S)
        drivers = plistlib.loads(reply.stdout) if reply.stdout.strip() else []
    except OSError, subprocess.SubprocessError, ValueError:
        return None, None
    return device_counters(drivers)


def read(device: bool = True, named: int | None = None) -> WriteReading:
    """Read the meter: this process's coalition, its live members, the named process's own counter when `named` is a pid, and the machine's device and swap counters unless `device` is False. The module docstring says what each field holds and what the reading checks."""
    at, wall, pid = time.monotonic(), time.time(), os.getpid()
    coalition, usage, members, check = _read_coalition(pid)
    named_member = None
    if named is not None:
        named_member = next((member for member in members if member.pid == named), None)
        libc = _libc()
        if named_member is None and libc is not None:
            named_member = _member(libc, named)
    device_bytes, swap_bytes = _read_device() if device else (None, None)
    return WriteReading(
        pid, at, wall, coalition, usage, members, check, named_member, device_bytes, swap_bytes
    )


def _difference(earlier: int | None, later: int | None) -> int | None:
    return None if earlier is None or later is None else later - earlier


def _pair_failure(earlier: WriteReading, later: WriteReading) -> str | None:
    for reading in (earlier, later):
        if reading.check is not None:
            return reading.check
        if reading.usage is None or reading.coalition is None:
            return "a reading has no coalition counters"
    if earlier.coalition != later.coalition:
        return f"the readings are of two coalitions ({earlier.coalition} and {later.coalition})"
    assert earlier.usage is not None and later.usage is not None
    for name in _RISING_FIELDS:
        if later.usage[name] < earlier.usage[name]:
            return f"the coalition's {name} fell from {earlier.usage[name]} to {later.usage[name]} between the readings"
    return None


def _share(before: Member | None, after: Member | None, since: float) -> int | None:
    """Return what one process wrote between two readings, or None when that cannot be read: it was gone by the later reading, this user cannot read its counter, or it was first seen in the later reading without having started after the earlier one."""
    if after is None or after.logical is None:
        return None
    if before is not None:
        return None if before.logical is None else after.logical - before.logical
    if after.start is not None and after.start >= since:
        return after.logical
    return None


def _descends(member: Member, by_pid: Mapping[int, Member], reader: int) -> bool:
    seen = {member.pid}
    pid = member.ppid
    while pid not in seen:
        if pid == reader:
            return True
        parent = by_pid.get(pid)
        if parent is None:
            return False
        seen.add(pid)
        pid = parent.ppid
    return False


def _sides(earlier: WriteReading, later: WriteReading) -> dict[tuple[int, float | None], str]:
    """Sort every member of both readings into the reader's tree, its ancestors and the outsiders, keyed by `Member.key`. The module docstring gives the rule."""
    reader = earlier.pid
    first = {member.pid: member for member in earlier.members}
    ancestors: set[int] = set()
    walk = first.get(reader)
    while walk is not None and walk.ppid in first and walk.ppid not in ancestors and walk.ppid != reader:
        ancestors.add(walk.ppid)
        walk = first[walk.ppid]
    sides: dict[tuple[int, float | None], str] = {}
    for member in earlier.members:
        if member.pid == reader or _descends(member, first, reader):
            sides[member.key] = _TREE
        else:
            sides[member.key] = _ANCESTOR if member.pid in ancestors else _OUTSIDER
    second = {member.pid: member for member in later.members}
    for member in later.members:
        if member.key in sides:
            continue
        side = _TREE
        seen = {member.pid}
        parent = second.get(member.ppid)
        while parent is not None and parent.pid not in seen:
            known = sides.get(parent.key)
            if known is not None:
                side = _TREE if known == _TREE else _OUTSIDER
                break
            seen.add(parent.pid)
            parent = second.get(parent.ppid)
        sides[member.key] = side
    return sides


def delta(earlier: WriteReading, later: WriteReading) -> WriteDelta:
    """Return the bytes written between two readings, `earlier` taken first, with the basis the figure rests on. The module docstring says how each basis is reached and what each field means."""
    device_bytes = _difference(earlier.device, later.device)
    swap_bytes = _difference(earlier.swap, later.swap)
    seconds = later.at - earlier.at
    failure = _pair_failure(earlier, later)
    if failure is not None:
        warning = f"write meter: {failure}, so the figure is the device's, machine-wide"
        if device_bytes is None:
            warning += ", and the device was not read"
        return WriteDelta(
            "device", device_bytes, None, None, None, None, device_bytes, swap_bytes, seconds, warning
        )
    first = {member.key: member for member in earlier.members}
    second = {member.key: member for member in later.members}
    named_bytes = None
    named_key = None
    if later.named is not None:
        before = earlier.named if earlier.named is not None and earlier.named.key == later.named.key else None
        named_bytes = _share(before or first.get(later.named.key), later.named, earlier.wall)
        if named_bytes is not None:
            named_key = later.named.key
    sides = _sides(earlier, later)
    outsiders_bytes = 0
    outsiders_unread = 0
    shared = False
    for key, side in sides.items():
        if side != _OUTSIDER or key == named_key:
            continue
        shared = True
        share = _share(first.get(key), second.get(key), earlier.wall)
        if share is None:
            outsiders_unread += 1
        else:
            outsiders_bytes += share
    written = _difference(earlier.logical, later.logical)
    diskio_bytes = _difference(earlier.diskio, later.diskio)
    assert written is not None
    written -= outsiders_bytes
    if named_key is not None and named_key in sides:
        assert named_bytes is not None
        written -= named_bytes
    return WriteDelta(
        "shared" if shared else "own",
        written,
        diskio_bytes,
        outsiders_bytes,
        outsiders_unread,
        named_bytes,
        device_bytes,
        swap_bytes,
        seconds,
        None,
    )
