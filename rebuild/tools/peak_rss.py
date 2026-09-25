"""Peak and current RSS readings, shared by the pipeline, the cycle driver and the test suite so every figure is measured the same way. Every function returns bytes. The only display unit is the decimal gigabyte (1 GB = 1e9 bytes), which is what every `*_gb` field and `rss_gb=` token means.

`getrusage`'s `ru_maxrss`, and the rusage `os.wait4` returns, is in bytes on Darwin and KiB on Linux, so every raw reading passes through `maxrss_to_bytes`. `/usr/bin/time` differs the same way: BSD `-l` reports bytes on Darwin and GNU `-v` reports KiB, and `parse_time_output` converts either to bytes.

`peak_rss_self_bytes` is this process's own peak. `peak_rss_children_bytes` is the largest peak among the children this process has reaped. `process_peak_rss_bytes` is the larger of the two, which is the figure a `[t]` line for a stage that fans out should carry. A peak only rises, so the difference between two peak readings says nothing about what happened between them; `reap_peak_rss_bytes` gives a per-child figure. `current_rss_bytes` is the resident set at the moment of the call. A `[t]` line with both tokens (`rss_token`, `rss_now_token`) shows where in the step the peak was reached and what the phase that just ended leaves resident.

The module imports only the standard library, so the pipeline, the surface build and `tools/build_font.py` (through `memory_budget`) can import it without adding any other module to their import closures.
"""

from __future__ import annotations

import os
import re
import resource
import subprocess
import sys

_BSD_TIME_RSS = re.compile(r"^\s*(\d+)\s+maximum resident set size", re.MULTILINE)
_GNU_TIME_RSS = re.compile(r"maximum resident set size[^:]*:\s*(\d+)", re.IGNORECASE)


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
