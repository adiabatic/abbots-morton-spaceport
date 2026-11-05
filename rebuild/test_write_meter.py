"""Tests for the write meter in `rebuild/tools/write_meter.py`.

Most tests build readings by hand: a coalition's counters, its members with their parents, start times and own writes, and the machine's counters, so the bases, the outsider arithmetic, the named process and the fallback to the device run on any platform. The self-check and the `ioreg` tree are tested through their pure functions. One test, on Darwin only, writes a mebibyte in a child and reads it back from this process's coalition, which checks the private struct's layout on the machine that runs it.
"""

import subprocess
import sys

import pytest

from rebuild.tools import write_meter
from rebuild.tools.write_meter import Member, WriteReading

READER = 100
EARLIER_WALL = 1000.0
BEFORE = 990.0
AFTER = 1000.5


def usage(
    logical: int, *, diskio: int = 0, started: int = 1, exited: int = 0, **fields: int
) -> dict[str, int]:
    values: dict[str, int] = dict.fromkeys(write_meter._COALITION_FIELDS, 0)
    values |= {"tasks_started": started, "tasks_exited": exited, "byteswritten": diskio}
    values |= {"logical_deferred_writes": logical, **fields}
    return values


def reading(
    members: list[Member],
    logical: int,
    *,
    diskio: int = 0,
    later: bool = False,
    check: str | None = None,
    coalition: int = 7,
    named: Member | None = None,
    device: int | None = None,
    swap: int | None = None,
    **fields: int,
) -> WriteReading:
    return WriteReading(
        pid=READER,
        at=12.5 if later else 10.0,
        wall=EARLIER_WALL + 2.5 if later else EARLIER_WALL,
        coalition=coalition,
        usage=usage(logical, diskio=diskio, **fields),
        members=tuple(members),
        check=check,
        named=named,
        device=device,
        swap=swap,
    )


SHELL = Member(90, 1, BEFORE, 0)
DRIVER = Member(READER, 90, BEFORE, 0)


def test_the_coalitions_logical_writes_are_immediate_deferred_and_metadata_less_invalidated():
    counters = usage(
        400, logical_immediate_writes=30, logical_metadata_writes=20, logical_invalidated_writes=50, diskio=9
    )
    one = WriteReading(READER, 0.0, EARLIER_WALL, 7, counters, (), None, None, None, None)
    assert (one.logical, one.diskio) == (400, 9)


def test_the_own_basis_is_the_whole_coalitions_delta_with_the_machines_beside_it():
    """With every member the reader, an ancestor or a descendant, the figure is the coalition's logical delta, the ancestors' writes and a new child's included, with the device and swap deltas beside it and neither netted."""
    child = Member(101, READER, AFTER, 0)
    earlier = reading([SHELL, DRIVER], 1_000, diskio=500, device=10_000, swap=4_000)
    later = reading(
        [Member(90, 1, BEFORE, 300), Member(READER, 90, BEFORE, 200), child],
        9_000,
        diskio=6_500,
        later=True,
        device=90_000,
        swap=4_000,
    )
    window = write_meter.delta(earlier, later)
    assert window == write_meter.WriteDelta(
        basis="own",
        bytes=8_000,
        diskio_bytes=6_000,
        outsiders_bytes=0,
        outsiders_unread=0,
        named_bytes=None,
        device_bytes=80_000,
        swap_bytes=0,
        seconds=2.5,
        warning=None,
    )


def test_the_shared_basis_subtracts_each_outsiders_window_and_counts_the_ones_it_cannot_read():
    """An outsider alive at both readings comes off at its delta and one that started after the earlier reading at its whole counter. One gone by the later reading, one this user cannot read and one first seen later that started before the earlier reading stay in the figure, which is then an upper bound, and are counted as unread."""
    tab = Member(200, 1, BEFORE, 1_000)
    gone = Member(201, 1, BEFORE, 50)
    login = Member(202, 1, None, None)
    raced = Member(203, 200, BEFORE + 1, 70)
    earlier = reading([SHELL, DRIVER, tab, gone, login], 10_000)
    later = reading(
        [SHELL, DRIVER, Member(200, 1, BEFORE, 1_400), login, Member(210, 200, AFTER, 250), raced],
        12_000,
        later=True,
    )
    window = write_meter.delta(earlier, later)
    assert window.basis == "shared"
    assert window.outsiders_bytes == 400 + 250
    assert window.outsiders_unread == 3
    assert window.bytes == 2_000 - 650


def test_a_new_member_takes_its_nearest_known_parents_side():
    """A new descendant of the reader is its own, a new child of an ancestor or of an outsider is an outsider, and an orphan whose parent is no member counts as the reader's, which keeps the bound an upper one."""
    tab = Member(200, 1, BEFORE, 0)
    earlier = reading([SHELL, DRIVER, tab], 0)
    step = Member(101, READER, AFTER, 10)
    worker = Member(102, 101, AFTER, 20)
    sibling = Member(103, 90, AFTER, 40)
    tabs_child = Member(104, 200, AFTER, 80)
    orphan = Member(105, 1, AFTER, 160)
    later = reading([SHELL, DRIVER, tab, step, worker, sibling, tabs_child, orphan], 310, later=True)
    sides = write_meter._sides(earlier, later)
    assert {member.pid: sides[member.key] for member in later.members} == {
        90: "ancestor",
        READER: "tree",
        200: "outsider",
        101: "tree",
        102: "tree",
        103: "outsider",
        104: "outsider",
        105: "tree",
    }
    window = write_meter.delta(earlier, later)
    assert (window.basis, window.outsiders_bytes, window.bytes) == ("shared", 120, 190)


def test_a_reused_pid_is_a_new_process():
    """A pid whose start time differs between the readings is a different process, so an outsider's exit and a new process on its pid are not read as one counter that fell."""
    earlier = reading([SHELL, DRIVER, Member(200, 1, BEFORE, 5_000)], 5_000)
    later = reading([SHELL, DRIVER, Member(200, 90, AFTER, 30)], 5_100, later=True)
    window = write_meter.delta(earlier, later)
    assert (window.outsiders_bytes, window.outsiders_unread, window.bytes) == (30, 1, 70)


@pytest.mark.parametrize(
    ("where", "named_before", "named_after", "basis", "bytes_"),
    [
        ("the pass's child", Member(150, READER, BEFORE, 100), Member(150, READER, BEFORE, 700), "own", 400),
        ("an orphan in the coalition", Member(150, 1, BEFORE, 100), Member(150, 1, BEFORE, 700), "own", 400),
        ("outside the coalition", Member(150, 1, BEFORE, 100), Member(150, 1, BEFORE, 700), "own", 1_000),
    ],
)
def test_the_named_process_is_reported_apart_wherever_it_sits(
    where, named_before, named_after, basis, bytes_
):
    """The named process's writes over the window are reported apart and never as an outsider's; a member's come out of the figure, and a process outside the coalition leaves the figure as it is."""
    inside = where != "outside the coalition"
    earlier = reading([SHELL, DRIVER, named_before] if inside else [SHELL, DRIVER], 1_000, named=named_before)
    later = reading(
        [SHELL, DRIVER, named_after] if inside else [SHELL, DRIVER], 2_000, later=True, named=named_after
    )
    window = write_meter.delta(earlier, later)
    assert (window.basis, window.named_bytes, window.bytes, window.outsiders_bytes) == (basis, 600, bytes_, 0)


def test_a_named_process_that_restarted_in_the_window_reports_its_whole_counter():
    """When the later reading names a server the pass started after the earlier one, its whole counter is its window; the server it replaced is classified like any other member."""
    old = Member(150, 1, BEFORE, 100)
    new = Member(160, READER, AFTER, 250)
    earlier = reading([SHELL, DRIVER, old], 1_000, named=old)
    later = reading([SHELL, DRIVER, new], 2_000, later=True, named=new)
    window = write_meter.delta(earlier, later)
    assert (window.basis, window.named_bytes, window.outsiders_unread, window.bytes) == (
        "shared",
        250,
        1,
        750,
    )


def test_a_named_process_gone_by_the_later_reading_is_classified_like_any_other():
    server = Member(150, 1, BEFORE, 100)
    earlier = reading([SHELL, DRIVER, server], 1_000, named=server)
    later = reading([SHELL, DRIVER], 2_000, later=True)
    window = write_meter.delta(earlier, later)
    assert (window.basis, window.named_bytes, window.outsiders_unread, window.bytes) == (
        "shared",
        None,
        1,
        1_000,
    )


@pytest.mark.parametrize(
    ("earlier_kwargs", "later_kwargs", "reason"),
    [
        ({"check": "the scan did not find the reader in its own coalition"}, {}, "did not find the reader"),
        (
            {},
            {"check": "tasks_started less tasks_exited puts 3 tasks in the coalition, but the scan found 2"},
            "found 2",
        ),
        ({}, {"coalition": 8}, "two coalitions (7 and 8)"),
        ({"diskio": 50}, {"diskio": 40}, "byteswritten fell from 50 to 40 between the readings"),
    ],
)
def test_a_reading_that_fails_its_check_falls_back_to_the_device_with_a_warning(
    earlier_kwargs, later_kwargs, reason
):
    earlier = reading([SHELL, DRIVER], 1_000, device=10_000, swap=0, **earlier_kwargs)
    later = reading([SHELL, DRIVER], 2_000, later=True, device=70_000, swap=16_384, **later_kwargs)
    window = write_meter.delta(earlier, later)
    assert (window.basis, window.bytes, window.device_bytes, window.swap_bytes) == (
        "device",
        60_000,
        60_000,
        16_384,
    )
    assert (window.diskio_bytes, window.outsiders_bytes, window.outsiders_unread, window.named_bytes) == (
        None,
    ) * 4
    assert window.warning is not None and reason in window.warning and "machine-wide" in window.warning


def test_without_a_coalition_or_a_device_reading_the_figure_is_none_and_says_so():
    """What `read()` returns where the platform has no coalition and the poll skipped the device."""
    blank = WriteReading(
        READER, 0.0, EARLIER_WALL, None, None, (), "linux has no resource coalition", None, None, None
    )
    window = write_meter.delta(blank, blank)
    assert (window.basis, window.bytes) == ("device", None)
    assert window.warning is not None
    assert "linux has no resource coalition" in window.warning and "the device was not read" in window.warning


class TestTheSelfCheck:
    """`self_check` over counters read either side of a scan. The scan's count must lie within what the task counters allow over it, less the pids it found exiting, no field the figure rests on may fall, the reader must be among the members, and the members' own writes must fit in the coalition's."""

    MEMBERS = (Member(READER, 90, BEFORE, 300), Member(90, 1, BEFORE, None))

    def test_a_quiet_scan_must_find_exactly_the_live_task_count(self):
        counters = usage(1_000, started=9, exited=7)
        assert write_meter.self_check(counters, counters, 2, 0, self.MEMBERS, READER) is None
        failure = write_meter.self_check(counters, counters, 3, 0, self.MEMBERS, READER)
        assert (
            failure == "tasks_started less tasks_exited puts 2 tasks in the coalition, but the scan found 3"
        )

    def test_a_member_whose_pid_stopped_answering_as_it_exits_may_be_missing(self):
        """A process tearing down its memory still counts as the coalition's after its pid stops answering, so each exiting pid the scan listed lowers the count it must reach, and never raises the count it may reach."""
        counters = usage(1_000, started=10, exited=7)
        assert write_meter.self_check(counters, counters, 2, 1, self.MEMBERS, READER) is None
        failure = write_meter.self_check(counters, counters, 1, 1, self.MEMBERS, READER)
        assert failure == (
            "tasks_started less tasks_exited puts 3 tasks in the coalition, but the scan found 1, and could not place 1 exiting pid"
        )
        assert write_meter.self_check(counters, counters, 4, 2, self.MEMBERS, READER) is not None

    def test_tasks_that_start_or_exit_during_the_scan_widen_what_it_may_find(self):
        before = usage(1_000, started=9, exited=7)
        after = usage(1_000, started=10, exited=8)
        assert [
            write_meter.self_check(before, after, found, 0, self.MEMBERS, READER) is None
            for found in (0, 1, 2, 3, 4)
        ] == [
            False,
            True,
            True,
            True,
            False,
        ]

    def test_a_field_the_figure_rests_on_that_falls_within_the_reading_fails_it(self):
        """`cpu_time`, which can fall by microseconds as a task exits and which the figure does not read, may fall."""
        failure = write_meter.self_check(
            usage(1_000, started=2, diskio=50, cpu_time=50),
            usage(1_000, started=2, diskio=49, cpu_time=49),
            2,
            0,
            self.MEMBERS,
            READER,
        )
        assert failure == "the coalition's byteswritten fell from 50 to 49 within one reading"
        assert (
            write_meter.self_check(
                usage(1_000, started=2, cpu_time=50),
                usage(1_000, started=2, cpu_time=49),
                2,
                0,
                self.MEMBERS,
                READER,
            )
            is None
        )

    def test_the_reader_must_be_among_the_members(self):
        counters = usage(1_000, started=2)
        failure = write_meter.self_check(counters, counters, 2, 0, (Member(90, 1, BEFORE, 0),) * 2, READER)
        assert failure == "the scan did not find the reader in its own coalition"

    def test_the_members_own_writes_must_fit_in_the_coalitions(self):
        counters = usage(299, started=2)
        failure = write_meter.self_check(counters, counters, 2, 0, self.MEMBERS, READER)
        assert failure == "the members' own logical writes (300 B) exceed the coalition's (299 B)"


def test_the_device_counters_come_from_the_driver_above_disk0_and_its_vm_volume():
    """The first driver in the tree holds no disk, the external disk's volumes carry a `VM` role too, and only the driver whose media child is `disk0` and the `VM` volume under it count."""

    def volume(name: str, role: str, written: int) -> dict:
        return {
            "IOObjectClass": "AppleAPFSVolume",
            "BSD Name": name,
            "Role": [role],
            "Statistics": {"Bytes written to block device": written},
        }

    def driver(disk: str | None, written: int, volumes: list[dict]) -> dict:
        media = {
            "IOObjectClass": "IOMedia",
            "IORegistryEntryChildren": [{"IORegistryEntryChildren": volumes}],
        }
        if disk is not None:
            media["BSD Name"] = disk
        return {"Statistics": {"Bytes (Write)": written}, "IORegistryEntryChildren": [media]}

    tree = [
        driver(None, 0, []),
        driver("disk4", 555, [volume("disk5s6", "VM", 777)]),
        driver("disk0", 18_000, [volume("disk3s5", "Data", 17_000), volume("disk3s6", "VM", 1_280)]),
    ]
    assert write_meter.device_counters(tree) == (18_000, 1_280)
    assert write_meter.device_counters(tree[:2]) == (None, None)


@pytest.mark.skipif(sys.platform != "darwin", reason="the resource coalition is Darwin's")
def test_a_childs_mebibyte_reads_back_from_the_coalition(tmp_path):
    """A child writes 1 MiB and exits, and the coalition's figure over the window holds it. The window's two readings pass their self-check, so the private struct's layout holds on this machine: a misread layout fails every reading, and the window is measured again up to `attempts` times, so a scan that the processes around the suite upset passes on another try. The figure lies between the bytes written and those plus what this process's ancestors and any outsider that started or exited inside the window, such as another xdist worker's child, wrote meanwhile, which `slack` bounds."""
    slack = 64_000_000
    attempts = 5
    target = tmp_path / "mebibyte.bin"
    writer = "import os, sys\nfd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\nos.write(fd, os.urandom(1 << 20))\nos.close(fd)"

    def measure() -> tuple[WriteReading, WriteReading]:
        before = write_meter.read(device=False)
        subprocess.run([sys.executable, "-c", writer, str(target)], check=True)
        return before, write_meter.read(device=False)

    windows = [measure()]
    while len(windows) < attempts and (windows[-1][0].check, windows[-1][1].check) != (None, None):
        windows.append(measure())
    before, after = windows[-1]
    assert (before.check, after.check) == (None, None), [(early.check, late.check) for early, late in windows]
    window = write_meter.delta(before, after)
    assert window.basis in ("own", "shared"), window.warning
    assert window.bytes is not None and 1 << 20 <= window.bytes < (1 << 20) + slack
    assert window.diskio_bytes is not None and window.diskio_bytes >= 0
