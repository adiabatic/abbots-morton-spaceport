import os
import subprocess
import sys

import pytest

from rebuild.tools import peak_rss
from rebuild.tools.cycle_timings import parse_inner_timings

BSD_TIME_OUTPUT = """\
       86.25 real        84.94 user         1.09 sys
          4712480768  maximum resident set size
                   0  average shared memory size
             2337210  page reclaims
"""

GNU_TIME_OUTPUT = """\
\tCommand being timed: "python x.py"
\tUser time (seconds): 84.94
\tMaximum resident set size (kbytes): 4602032
\tExit status: 0
"""


def test_maxrss_is_bytes_on_darwin_and_kib_elsewhere():
    assert peak_rss.maxrss_to_bytes(4712480768, platform="darwin") == 4712480768
    assert peak_rss.maxrss_to_bytes(4602032, platform="linux") == 4602032 * 1024


def test_self_and_children_peaks_are_normalized_bytes():
    own = peak_rss.peak_rss_self_bytes()
    assert own > 10 * 1024 * 1024
    assert peak_rss.process_peak_rss_bytes() >= max(own, peak_rss.peak_rss_children_bytes())


def test_gb_means_decimal_gigabytes_everywhere():
    assert peak_rss.bytes_to_gb(8_940_000_000) == 8.94
    assert peak_rss.format_gb(8_940_000_000) == "8.94"
    assert peak_rss.rss_token(8_940_000_000) == "rss_gb=8.94"


def test_parse_time_output_reads_both_dialects_back_to_bytes():
    assert peak_rss.parse_time_output(BSD_TIME_OUTPUT) == 4712480768
    assert peak_rss.parse_time_output(GNU_TIME_OUTPUT) == 4602032 * 1024
    assert peak_rss.parse_time_output("no rss here\n1.0 real") is None


def test_time_wrapper_picks_the_platform_flag():
    assert peak_rss.time_wrapper(platform="darwin") in ([], ["/usr/bin/time", "-l"])
    assert peak_rss.time_wrapper(platform="linux") in ([], ["/usr/bin/time", "-v"])


def test_rss_token_round_trips_through_the_inner_line_grammar():
    line = f"[t] build_tables_total 243.1s {peak_rss.rss_token(8_940_000_000)}"
    assert parse_inner_timings(line) == [{"label": "build_tables_total", "elapsed_s": 243.1, "rss_gb": 8.94}]


def test_both_rss_tokens_round_trip_side_by_side():
    line = f"[t] review.build plan 9.9s {peak_rss.rss_token(5_280_000_000)} {peak_rss.rss_now_token(4_020_000_000)}"
    assert parse_inner_timings(line) == [
        {"label": "review.build plan", "elapsed_s": 9.9, "rss_gb": 5.28, "rss_now_gb": 4.02}
    ]
    assert peak_rss.rss_now_token(4_020_000_000) == "rss_now_gb=4.02"


def test_the_current_reading_is_a_resident_set_under_the_peak():
    current = peak_rss.current_rss_bytes()
    assert current is not None and current > 10 * 1024 * 1024
    assert current <= peak_rss.peak_rss_self_bytes() * 1.05


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"), reason="no footprint source here"
)
def test_the_footprint_counts_what_a_process_touches_and_reads_another_live_process():
    """The current footprint rises by the pages this process touches, the lifetime peak is at least the current figure, and another process this user owns reads the same way while it is alive and reads None once it is reaped."""
    before = peak_rss.footprint_bytes()
    block = bytearray(64 * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    now = peak_rss.footprint_bytes()
    assert before is not None and now is not None
    assert now - before >= 60 * 1024 * 1024
    peak = peak_rss.peak_footprint_bytes()
    assert peak is not None and peak >= now
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        current = peak_rss.footprint_bytes(child.pid)
        assert current is not None and current > 0
        child_peak = peak_rss.peak_footprint_bytes(child.pid)
        assert child_peak is not None and child_peak >= current
    finally:
        child.kill()
        child.wait()
    assert peak_rss.footprint_bytes(child.pid) is None


def test_the_swap_in_use_is_what_the_machine_states():
    """On Linux the swap in use is `/proc/meminfo`'s total less its free, and a text without both fields reads None. Where there is a source, the live reading is a byte count."""
    meminfo = "MemTotal:       16384000 kB\nSwapTotal:       4194304 kB\nSwapFree:        1048576 kB\n"
    assert peak_rss.meminfo_swap_used_bytes(meminfo) == 3145728 * 1024
    assert peak_rss.meminfo_swap_used_bytes("SwapTotal:       4194304 kB\n") is None
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        used = peak_rss.swap_used_bytes()
        assert used is not None and used >= 0


def test_reap_returns_the_child_peak_and_sets_returncode():
    proc = subprocess.Popen([sys.executable, "-c", "x = bytearray(64 * 1024 * 1024)"])
    peak = peak_rss.reap_peak_rss_bytes(proc)
    assert peak is not None and peak > 64 * 1024 * 1024
    assert proc.returncode == 0
    assert proc.wait() == 0
    assert peak_rss.reap_peak_rss_bytes(proc) is None


def test_reap_reports_a_signal_the_way_popen_would():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    proc.terminate()
    peak = peak_rss.reap_peak_rss_bytes(proc)
    assert peak is not None and peak > 0
    assert proc.returncode == -15
    assert proc.wait() == -15


def test_the_reap_reads_a_childs_footprint_before_reaping_it(monkeypatch):
    """`reap_peaks_bytes` reads the child's peak footprint after it exits and before it is reaped, while the child is still waitable, then reaps it, so the footprint is the child's own lifetime peak. On Darwin it covers the pages the child touched; on Linux a zombie has no `VmHWM` and the footprint reads None. The resident peak and the return code come back as `reap_peak_rss_bytes` gives them, and a child already reaped reads neither line."""
    waitable = []
    read = peak_rss.peak_footprint_bytes

    def footprint_of(pid: int, platform: str = sys.platform) -> int | None:
        waitable.append(os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None)
        return read(pid, platform)

    monkeypatch.setattr(peak_rss, "peak_footprint_bytes", footprint_of)
    touch = "b = bytearray(64 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1"
    proc = subprocess.Popen([sys.executable, "-c", touch])
    peaks = peak_rss.reap_peaks_bytes(proc)
    assert waitable == [True]
    assert peaks.resident is not None and peaks.resident > 64 * 1024 * 1024
    assert proc.returncode == 0 and proc.wait() == 0
    if sys.platform == "darwin":
        assert peaks.footprint is not None and peaks.footprint > 60 * 1024 * 1024
    elif sys.platform.startswith("linux"):
        assert peaks.footprint is None
    assert peak_rss.reap_peaks_bytes(proc) == (None, None)
