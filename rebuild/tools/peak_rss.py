"""The one yardstick for peak RSS, shared by production and bench so a figure measured anywhere is a figure measured the same way (issue #51). Everything here answers in bytes; the sole presentation unit is the decimal gigabyte (1 GB = 1e9 bytes), which is what every `*_gb` field and `rss_gb=` token means — GiB and per-site conversions are exactly the drift this module exists to end.

The normalization it owns: `getrusage`'s `ru_maxrss` (and the rusage `os.wait4` returns) is bytes on Darwin and KiB on Linux, so every raw reading passes through `maxrss_to_bytes` before it is stored or compared. `/usr/bin/time` output is normalized the same way — BSD `-l` reports the maximum resident set size in bytes on Darwin, GNU `-v` reports kbytes — and `parse_time_output` reads either format back to bytes.

Self versus children is explicit because the two answer different questions: `peak_rss_self_bytes` is this process's own high-water mark, `peak_rss_children_bytes` is the max over every child this process has reaped, and `process_peak_rss_bytes` is the widest single process this one has been or has waited on — the figure a `[t]` line about a stage that fans out should carry. None of these is a current reading; a high-water mark only ever rises, so a delta between two readings attributes nothing (see `reap_peak_rss_bytes` for the per-child form that does).

Stdlib-only on purpose: the bench harnesses import this under alternative interpreters and from trees where only the repo root is on `sys.path`, and the pipeline imports it without pulling in any tools-tree machinery.
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
    """The trailing token a `[t]` phase line carries its peak RSS in, e.g. `[t] build_tables_total 243.1s rss_gb=8.94`. `cycle_timings.parse_inner_timings` is the reader; a round-trip test binds the two."""
    return f"rss_gb={format_gb(byte_count)}"


def time_wrapper(platform: str = sys.platform) -> list[str]:
    """The argv prefix that has `/usr/bin/time` report a child's peak RSS on stderr, or [] where there is no `/usr/bin/time` to ask. Prefer `reap_peak_rss_bytes` for a child this process spawns itself; the wrapper is for children that outlive their spawner or run under a shell."""
    if not os.path.isfile("/usr/bin/time"):
        return []
    return ["/usr/bin/time", "-l" if platform == "darwin" else "-v"]


def parse_time_output(text: str) -> int | None:
    """The peak RSS in bytes from `/usr/bin/time` output in either dialect — BSD `-l` (bytes on Darwin, the only BSD this repo meets) or GNU `-v` (KiB) — or None when the text carries neither line."""
    match = _BSD_TIME_RSS.search(text)
    if match:
        return int(match.group(1))
    match = _GNU_TIME_RSS.search(text)
    if match:
        return int(match.group(1)) * 1024
    return None


def reap_peak_rss_bytes(proc: subprocess.Popen) -> int | None:
    """Reap `proc` with `os.wait4` so its exit status arrives with its rusage, set `proc.returncode` the way Popen would (negative signal number on a kill), and return the child's peak RSS in bytes. The figure is a max over the child and every descendant the child itself reaped, so for a step that fans out a pool it is the widest single process in that tree — per-step attribution that `RUSAGE_CHILDREN` (a max over all children ever reaped, across steps) cannot give. None when the child is already reaped or another waiter wins the race (the interrupt path's terminate_all also waits); the caller's own `proc.wait()` then answers as usual."""
    if proc.returncode is not None or not hasattr(os, "wait4"):
        return None
    try:
        _, status, rusage = os.wait4(proc.pid, 0)
    except ChildProcessError:
        return None
    proc.returncode = os.waitstatus_to_exitcode(status)
    return maxrss_to_bytes(rusage.ru_maxrss)
