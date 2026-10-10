"""Peak and current RSS readings, shared by the pipeline, the cycle driver and the test suite so every figure is measured the same way. Every function returns bytes. The only display unit is the decimal gigabyte (1 GB = 1e9 bytes), which is what every `*_gb` field and `rss_gb=` token means.

`getrusage`'s `ru_maxrss`, and the rusage `os.wait4` returns, is in bytes on Darwin and KiB on Linux, so every raw reading passes through `maxrss_to_bytes`. `/usr/bin/time` differs the same way: BSD `-l` reports bytes on Darwin and GNU `-v` reports KiB, and `parse_time_output` converts either to bytes.

`peak_rss_self_bytes` is this process's own peak. `peak_rss_children_bytes` is the largest peak among the children this process has reaped. `process_peak_rss_bytes` is the larger of the two, which is the figure a `[t]` line for a stage that fans out should carry. A peak only rises, so the difference between two peak readings says nothing about what happened between them; `reap_peak_rss_bytes` gives a per-child figure. `current_rss_bytes` is the resident set at the moment of the call. A `[t]` line with both tokens (`rss_token`, `rss_now_token`) shows where in the step the peak was reached and what the phase that just ended leaves resident.

Resident set under-reads a process under memory pressure: on Darwin the pages the compressor holds and the pages swapped out count in neither the resident set nor `ru_maxrss`. `footprint_bytes` and `peak_footprint_bytes` read what the process costs the machine instead, for any live pid this user owns. On Darwin that is `proc_pid_rusage`'s `rusage_info_v4` through `ctypes`: `ri_phys_footprint` now and `ri_lifetime_max_phys_footprint` over the process's life, the figures Activity Monitor and `top` report as memory. On Linux the current figure is `/proc/<pid>/status`'s `VmRSS` plus `VmSwap` and the peak is its `VmHWM`, a resident peak that leaves out what was swapped out. Elsewhere the current figure is None and the peak falls back to `ru_maxrss` for this process and None for another. Another process's peak footprint is readable until it is reaped, so `reap_peaks_bytes` reads a child's between its exit and its reap and returns it beside the resident peak `reap_peak_rss_bytes` reads; `footprint_token` writes it on a `[t]` line beside `rss_token`.

`swap_used_bytes` is the machine's swap in use, for a report to show beside a pool's footprint so a run short of memory is visible in its log: Darwin's `vm.swapusage` through `sysctlbyname`, or `/proc/meminfo`'s `SwapTotal` less `SwapFree` on Linux. It lags and does not fall back when the pressure ends, which is why `memory_budget` never sizes a pool from it.

The module imports only the standard library, so the pipeline, the corpus build and `tools/build_font.py` (through `memory_budget`) can import it without adding any other module to their import closures.
"""

from __future__ import annotations

import ctypes
import os
import re
import resource
import subprocess
import sys
from typing import NamedTuple

_BSD_TIME_RSS = re.compile(r"^\s*(\d+)\s+maximum resident set size", re.MULTILINE)
_GNU_TIME_RSS = re.compile(r"maximum resident set size[^:]*:\s*(\d+)", re.IGNORECASE)
_RUSAGE_INFO_V4 = 4
_STATUS_KIB = re.compile(r"^(VmRSS|VmSwap|VmHWM):\s*(\d+)\s*kB\s*$", re.MULTILINE)
_MEMINFO_SWAP_KIB = re.compile(r"^(SwapTotal|SwapFree):\s*(\d+)\s*kB\s*$", re.MULTILINE)


class _RusageInfoV4(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64)
        for name in (
            "ri_user_time",
            "ri_system_time",
            "ri_pkg_idle_wkups",
            "ri_interrupt_wkups",
            "ri_pageins",
            "ri_wired_size",
            "ri_resident_size",
            "ri_phys_footprint",
            "ri_proc_start_abstime",
            "ri_proc_exit_abstime",
            "ri_child_user_time",
            "ri_child_system_time",
            "ri_child_pkg_idle_wkups",
            "ri_child_interrupt_wkups",
            "ri_child_pageins",
            "ri_child_elapsed_abstime",
            "ri_diskio_bytesread",
            "ri_diskio_byteswritten",
            "ri_cpu_time_qos_default",
            "ri_cpu_time_qos_maintenance",
            "ri_cpu_time_qos_background",
            "ri_cpu_time_qos_utility",
            "ri_cpu_time_qos_legacy",
            "ri_cpu_time_qos_user_initiated",
            "ri_cpu_time_qos_user_interactive",
            "ri_billed_system_time",
            "ri_serviced_system_time",
            "ri_logical_writes",
            "ri_lifetime_max_phys_footprint",
            "ri_instructions",
            "ri_cycles",
            "ri_billed_energy",
            "ri_serviced_energy",
            "ri_interval_max_phys_footprint",
            "ri_runnable_time",
        )
    ]


class _XswUsage(ctypes.Structure):
    _fields_ = [
        ("xsu_total", ctypes.c_uint64),
        ("xsu_avail", ctypes.c_uint64),
        ("xsu_used", ctypes.c_uint64),
        ("xsu_pagesize", ctypes.c_uint32),
        ("xsu_encrypted", ctypes.c_int),
    ]


def _darwin_rusage(pid: int) -> _RusageInfoV4 | None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        info = _RusageInfoV4()
        if libc.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(_RUSAGE_INFO_V4), ctypes.byref(info)) != 0:
            return None
    except OSError, AttributeError:
        return None
    return info


def _linux_status_bytes(pid: int) -> dict[str, int]:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as handle:
            text = handle.read()
    except OSError, ValueError:
        return {}
    return {name: int(kib) * 1024 for name, kib in _STATUS_KIB.findall(text)}


def footprint_bytes(pid: int | None = None, platform: str = sys.platform) -> int | None:
    """Return the memory `pid` (this process when None) costs the machine now, in bytes, or None when it cannot be read: the process is gone or belongs to another user, or the platform has no source. Unlike a resident set it counts compressed and swapped pages, so it does not fall when the machine is under pressure. The module docstring names the source on each platform."""
    target = os.getpid() if pid is None else pid
    if platform == "darwin":
        info = _darwin_rusage(target)
        return None if info is None else int(info.ri_phys_footprint)
    status = _linux_status_bytes(target)
    if "VmRSS" not in status:
        return None
    return status["VmRSS"] + status.get("VmSwap", 0)


def peak_footprint_bytes(pid: int | None = None, platform: str = sys.platform) -> int | None:
    """Return the most memory `pid` (this process when None) has cost the machine at any point in its life, in bytes, or None when it cannot be read. A process can read its own peak just before it returns, which is how a pool worker reports its peak to the parent; another process's peak is readable only while it is alive and unreaped. The module docstring names the source on each platform, and where the only source is a resident peak it under-reads a process that was swapping."""
    target = os.getpid() if pid is None else pid
    if platform == "darwin":
        info = _darwin_rusage(target)
        return None if info is None else int(info.ri_lifetime_max_phys_footprint)
    peak = _linux_status_bytes(target).get("VmHWM")
    if peak is not None:
        return peak
    return peak_rss_self_bytes() if pid is None or pid == os.getpid() else None


def meminfo_swap_used_bytes(text: str) -> int | None:
    """Return the swap in use that a `/proc/meminfo` text states, `SwapTotal` less `SwapFree`, in bytes, or None when it states neither."""
    fields = {name: int(kib) * 1024 for name, kib in _MEMINFO_SWAP_KIB.findall(text)}
    if "SwapTotal" not in fields or "SwapFree" not in fields:
        return None
    return fields["SwapTotal"] - fields["SwapFree"]


def swap_used_bytes(platform: str = sys.platform) -> int | None:
    """Return the swap this machine has in use now, in bytes, or None when it cannot be read. The module docstring names the source on each platform."""
    if platform == "darwin":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            usage = _XswUsage()
            size = ctypes.c_size_t(ctypes.sizeof(usage))
            if libc.sysctlbyname(b"vm.swapusage", ctypes.byref(usage), ctypes.byref(size), None, 0) != 0:
                return None
        except OSError, AttributeError:
            return None
        return int(usage.xsu_used)
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            return meminfo_swap_used_bytes(handle.read())
    except OSError, ValueError:
        return None


def maxrss_to_bytes(ru_maxrss: int, platform: str = sys.platform) -> int:
    return ru_maxrss if platform == "darwin" else ru_maxrss * 1024


def peak_rss_self_bytes() -> int:
    return maxrss_to_bytes(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def peak_rss_children_bytes() -> int:
    return maxrss_to_bytes(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)


def process_peak_rss_bytes() -> int:
    return max(peak_rss_self_bytes(), peak_rss_children_bytes())


def bytes_to_gb(byte_count: float) -> float:
    return byte_count / 1e9


def format_gb(byte_count: float) -> str:
    return f"{bytes_to_gb(byte_count):.2f}"


def rss_token(byte_count: float) -> str:
    """Return the trailing peak-RSS token of a `[t]` phase line, as in `[t] build_tables_total 243.1s rss_gb=8.94`. `cycle_timings.parse_inner_timings` reads it, and rebuild/test_peak_rss.py checks the round trip."""
    return f"rss_gb={format_gb(byte_count)}"


def rss_now_token(byte_count: float) -> str:
    """Return the current-RSS token a `[t]` phase line carries beside the peak, as in `rss_gb=5.36 rss_now_gb=4.02`. `cycle_timings.parse_inner_timings` reads it into `rss_now_gb`. The peak token's pattern, `\\brss_gb=`, does not match inside `rss_now_gb=`, so the two tokens do not interfere."""
    return f"rss_now_gb={format_gb(byte_count)}"


def footprint_token(byte_count: float) -> str:
    """Return the peak-footprint token a `[t]` phase line carries beside its resident peak, as in `rss_gb=28.04 footprint_gb=29.50`. `cycle_timings.parse_inner_timings` reads it into `footprint_gb`; run_m1's `[t] kernel_build_tables` line carries the table build's (`reap_peaks_bytes`)."""
    return f"footprint_gb={format_gb(byte_count)}"


def current_rss_bytes() -> int | None:
    """Return this process's resident set now, in bytes, or None where it cannot be read (a sandbox that blocks `ps`, a platform with neither source). It reads the resident-pages field of `/proc/self/statm` times the page size where that file exists, and otherwise runs `ps -o rss=`, which reports KiB. Unlike the peaks, it can fall, so it separates a phase's own working set from the step's peak. On Darwin each call spawns `ps`, so call it once per phase, not in a loop."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except OSError, ValueError, IndexError:
        pass
    try:
        reply = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=False
        )
        return int(reply.stdout.strip()) * 1024
    except OSError, ValueError:
        return None


def time_wrapper(platform: str = sys.platform) -> list[str]:
    """Return the argv prefix that makes `/usr/bin/time` report a child's peak RSS on stderr, or [] where there is no `/usr/bin/time`. For a child this process spawns and waits on, use `reap_peak_rss_bytes` instead; the wrapper is for children that outlive their spawner or run under a shell."""
    if not os.path.isfile("/usr/bin/time"):
        return []
    return ["/usr/bin/time", "-l" if platform == "darwin" else "-v"]


def parse_time_output(text: str) -> int | None:
    """Return the peak RSS in bytes from `/usr/bin/time` output in either format, BSD `-l` (bytes on Darwin, the only BSD this repo runs on) or GNU `-v` (KiB), or None when the text has neither line."""
    match = _BSD_TIME_RSS.search(text)
    if match:
        return int(match.group(1))
    match = _GNU_TIME_RSS.search(text)
    if match:
        return int(match.group(1)) * 1024
    return None


def reap_peak_rss_bytes(proc: subprocess.Popen) -> int | None:
    """Reap `proc` with `os.wait4`, set `proc.returncode` as Popen would (a negative signal number on a kill), and return the child's peak RSS in bytes. The figure is the largest peak among the child and every descendant the child reaped, so for a step that runs a pool it is the largest single process in that tree. `RUSAGE_CHILDREN` cannot give this per-step figure, because it covers every child reaped so far, across steps. Returns None when the child is already reaped or another waiter reaps it first (the interrupt path's `terminate_all` also waits); the caller's own `proc.wait()` then works as usual."""
    if proc.returncode is not None or not hasattr(os, "wait4"):
        return None
    try:
        _, status, rusage = os.wait4(proc.pid, 0)
    except ChildProcessError:
        return None
    proc.returncode = os.waitstatus_to_exitcode(status)
    return maxrss_to_bytes(rusage.ru_maxrss)


class ReapedPeaks(NamedTuple):
    """A reaped child's two peak lines in bytes, either None where it cannot be read: `resident`, as `reap_peak_rss_bytes` reads it, and `footprint`, its own lifetime peak footprint (`peak_footprint_bytes`)."""

    resident: int | None
    footprint: int | None


def reap_peaks_bytes(proc: subprocess.Popen) -> ReapedPeaks:
    """Reap `proc` as `reap_peak_rss_bytes` does, reading its peak footprint first. A child's peak footprint is readable only until it is reaped, so this waits for it to exit without reaping it (`os.waitid` with `WNOWAIT`), reads `peak_footprint_bytes(proc.pid)` while it is a zombie, and only then reaps it with `os.wait4`. The footprint is the child's own, not its descendants'. It is None where `os.waitid` is missing, when the child is already reaped or another waiter reaps it first, and on Linux, where a zombie's `/proc/<pid>/status` carries no `VmHWM`."""
    footprint = None
    if proc.returncode is None and hasattr(os, "waitid"):
        try:
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
        except ChildProcessError:
            pass
        else:
            footprint = peak_footprint_bytes(proc.pid)
    return ReapedPeaks(reap_peak_rss_bytes(proc), footprint)
