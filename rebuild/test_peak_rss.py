import subprocess
import sys

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
