import ast
import io
import pathlib
import re
import subprocess
import sys
import threading

import pytest

from rebuild.tools import console
from rebuild.tools.cycle_timings import parse_inner_timings
from rebuild.tools.peak_rss import bytes_to_gb, rss_token


class _Clock:
    def __init__(self, now=0.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class _Result:
    def __init__(self, elapsed=1.0, peak_rss_bytes=None):
        self.elapsed = elapsed
        self.peak_rss_bytes = peak_rss_bytes


def _digest(clock=None, **kwargs):
    kwargs.setdefault("steps", ["snapshot", "run_m1", "surface-build", "gate:conform"])
    return console.Digest(clock=clock or _Clock(), **kwargs)


def _surfaced(out):
    return [line for line in out.splitlines() if line.startswith("  ")]


_SURFACED = re.compile(r"^  (\S+)\s+step\s+\S+\s+cycle\s+\S+  (.*)$")


def _bodies(out, name):
    matches = (_SURFACED.match(line) for line in out.splitlines())
    return [match.group(2) for match in matches if match is not None and match.group(1) == name]


def test_the_writers_print_the_four_protocol_prefixes():
    sink = io.StringIO()
    console.phase("build_tables", file=sink)
    console.progress(3, 6, "configurations", file=sink)
    console.progress(8192, None, "units", file=sink)
    console.warn("green, but this pass recorded no green", file=sink)
    console.timing("build_tables", 243.05, file=sink)
    assert sink.getvalue().splitlines() == [
        "[phase] build_tables",
        "[progress] 3/6 configurations",
        "[progress] 8192/? units",
        "[warn] green, but this pass recorded no green",
        "[t] build_tables 243.1s",
    ]


def test_the_writers_default_to_stdout_resolved_at_write_time(capsys):
    console.phase("load")
    console.warn("careful")
    assert capsys.readouterr().out == "[phase] load\n[warn] careful\n"


def test_timing_writes_exactly_what_the_timings_journal_parses():
    sink = io.StringIO()
    console.timing("build_tables", 243.05, rss_token(8_940_000_000), file=sink)
    console.timing("review.build enrich", 12.0, "\t(4 shards)", file=sink)
    assert parse_inner_timings(sink.getvalue()) == [
        {"label": "build_tables", "elapsed_s": 243.1, "rss_gb": 8.94},
        {"label": "review.build enrich", "elapsed_s": 12.0},
    ]


def test_parse_line_reads_back_every_writer():
    sink = io.StringIO()
    console.phase("enrich", file=sink)
    console.progress(3, 6, "configurations", file=sink)
    console.progress(8192, None, "units", file=sink)
    console.warn("2 echo groups hold disagreeing verdicts", file=sink)
    console.timing("enrich", 12.5, "(4 shards)", file=sink)
    assert [console.parse_line(line) for line in sink.getvalue().splitlines()] == [
        console.Phase("enrich"),
        console.Progress(done=3, total=6, unit="configurations"),
        console.Progress(done=8192, total=None, unit="units"),
        console.Warn("2 echo groups hold disagreeing verdicts"),
        console.Timing("enrich", 12.5, "(4 shards)"),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "[t] build_tables 243.1s",
        "[t] conform[default] 5.5s shaping_runs=123",
        "[t] settle 1.5s\tqueued=4",
        "[t] gate:js 3s",
    ],
)
def test_parse_line_agrees_with_the_journal_on_a_timing_line(line):
    parsed = console.parse_line(line)
    assert isinstance(parsed, console.Timing)
    (entry,) = parse_inner_timings(line)
    assert (parsed.label, parsed.seconds) == (entry["label"], entry["elapsed_s"])


@pytest.mark.parametrize(
    "line",
    [
        "",
        "wrote 12 standing-approval verdicts",
        "[t] build_tables",
        "[t] build_tables 243.1",
        "[t]  243.1s",
        "  [t] build_tables 243.1s",
        "prefix [t] build_tables 243.1s",
        "[t] build_tables abc s",
    ],
)
def test_parse_line_ignores_everything_the_journal_ignores(line):
    assert console.parse_line(line) is None
    assert parse_inner_timings(line) == []


@pytest.mark.parametrize(
    "line",
    ["[phase] ", "[progress] many/6 units", "[progress] 3 units", "[progress] ", "[warn]  "],
)
def test_parse_line_refuses_a_malformed_protocol_line(line):
    assert console.parse_line(line) is None


def test_progress_text_formats_counts_and_an_unknown_total():
    assert console.Progress(done=1_080_064, total=1_080_064, unit="units").text == "1,080,064/1,080,064 units"
    assert console.Progress(done=8192, total=None, unit="units").text == "8,192/? units"
    assert console.Progress(percent=17).text == "17%"
    assert console.Progress(done=3, total=6).text == "3/6"


def test_pytest_events_read_failures_and_the_percent_marker():
    assert console.pytest_events("FAILED rebuild/test_x.py::test_y - boom") == console.Warn(
        "FAILED rebuild/test_x.py::test_y - boom"
    )
    assert console.pytest_events("ERROR rebuild/test_x.py::test_y") == console.Warn(
        "ERROR rebuild/test_x.py::test_y"
    )
    assert console.pytest_events("rebuild/test_x.py ....         [ 17%]") == console.Progress(percent=17)
    assert console.pytest_events("[gw3] [100%] PASSED rebuild/test_x.py::test_y") == console.Progress(
        percent=100
    )
    assert console.pytest_events("collected 1200 items") is None


def test_pytest_events_strip_the_color_a_forced_terminal_adds():
    colored = "\x1b[31mFAILED\x1b[0m rebuild/test_x.py::test_y - boom"
    assert console.pytest_events(colored) == console.Warn("FAILED rebuild/test_x.py::test_y - boom")


def test_the_pytest_warning_count_comes_off_the_terminal_summary_rule():
    assert console.pytest_warning_count("===== 1 failed, 1200 passed, 3 warnings in 61.2s =====") == 3
    assert console.pytest_warning_count("======= 1 warning in 2.10s =======") == 1
    assert console.pytest_warning_count("=========== warnings summary ===========") is None
    assert console.pytest_warning_count("1200 passed, 3 warnings in 61.2s") is None


def test_node_test_events_read_tap_failures_and_not_a_zero_fail_count():
    assert console.node_test_events("not ok 3 - keyboard binds every letter") == console.Warn(
        "not ok 3 - keyboard binds every letter"
    )
    assert console.node_test_events("    not ok 1 - nested") == console.Warn("not ok 1 - nested")
    assert console.node_test_events("# fail 2") == console.Warn("# fail 2")
    assert console.node_test_events("# fail 0") is None
    assert console.node_test_events("ok 4 - docket groups") is None


def test_warning_events_catch_both_shapes_anything_here_prints():
    assert console.warning_events("WARNING: a verdict outside approve sits on a filled unit") is not None
    assert console.warning_events("  WARNING: indented by the standing fill") == console.Warn(
        "WARNING: indented by the standing fill"
    )
    assert console.warning_events("warning: retention pass failed") is not None
    assert (
        console.warning_events("rebuild/pipeline/spec.py:88: UserWarning: two runes claim one code point")
        is not None
    )
    assert console.warning_events("no warning here") is None
    assert console.warning_events("this line warns you about nothing") is None


def test_an_adapter_is_chosen_by_step_name():
    assert console.adapter_for("gate:js") is console.node_test_events
    assert console.adapter_for("gate:rebuild-contracts") is console.pytest_events
    assert console.adapter_for("run_m1") is None


def test_fmt_count_separates_thousands():
    assert console.fmt_count(1_080_064) == "1,080,064"
    assert console.fmt_count(0) == "0"
    assert console.fmt_count(999) == "999"


@pytest.mark.parametrize(
    "seconds,rendered",
    [
        (0.0, "0.0s"),
        (-1.0, "0.0s"),
        (0.44, "0.4s"),
        (59.94, "59.9s"),
        (59.96, "1m00s"),
        (60.0, "1m00s"),
        (1988.0, "33m08s"),
        (3599.4, "59m59s"),
        (3600.0, "1h00m"),
        (3720.0, "1h02m"),
    ],
)
def test_fmt_duration_reads_at_a_glance(seconds, rendered):
    assert console.fmt_duration(seconds) == rendered


def test_fmt_rss_is_one_decimal_gigabyte_or_nothing():
    assert console.fmt_rss(19_600_000_000) == "19.6G"
    assert console.fmt_rss(0) == "0.0G"
    assert console.fmt_rss(None) == ""


def test_fmt_rss_reads_the_same_gigabyte_peak_rss_does():
    """`fmt_rss` divides by its own constant because console may import nothing else in this tree, so this is what keeps the two spellings of a decimal gigabyte from drifting apart: the module that measures a peak and the module that prints one have to mean the same unit, and only the printed precision differs."""
    for byte_count in (0, 1, 19_600_000_000, 2**40):
        assert console.fmt_rss(byte_count) == f"{bytes_to_gb(byte_count):.1f}G"


def test_console_imports_nothing_else_in_this_tree():
    """The verdict chain opens its steps with `console.phase`, so console sits inside the plumbing key's code closure and anything console imports sits there with it. The timings journal and the two width yardsticks are what that closure was named to shed — a width or a telemetry field can never move a verdict — so an import here would re-run the whole chain for an edit to one of them. rebuild/test_plumbing_closure.py fails on the consequence; this fails on the cause, which is the line a reader of console.py can act on."""
    tree = ast.parse(pathlib.Path(console.__file__).read_text(encoding="utf-8"))
    reached = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            reached.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            reached.add(node.module)
    assert sorted(n for n in reached if n.split(".")[0] in ("rebuild", "tools")) == []


def _rows():
    return [
        console.PlanRow(1, console.STATUS_RUN, "snapshot", "copies the served surface"),
        console.PlanRow(2, console.STATUS_RUN, "run_m1", argv="uv run python -m rebuild.pipeline.run_m1"),
        console.PlanRow(3, console.STATUS_SKIP, "surface-build", "green record matches"),
        console.PlanRow(4, console.STATUS_MAYBE, "gate:conform", "may re-skip after run_m1", "make conform"),
    ]


def test_the_counts_line_states_a_range_only_while_a_step_is_undecided():
    assert console.counts_line(_rows()) == "4 steps: 2–3 will run, 1 skipped"
    decided = [row for row in _rows() if row.status != console.STATUS_MAYBE]
    assert console.counts_line(decided) == "3 steps: 2 will run, 1 skipped"


def test_plan_lines_carry_the_column_the_note_and_the_argv():
    lines = console.plan_lines(_rows())
    assert lines[0] == "  1  run   snapshot       copies the served surface"
    assert lines[1] == "  2  run   run_m1"
    assert lines[2] == "           $ uv run python -m rebuild.pipeline.run_m1"
    assert lines[3] == "  3  skip  surface-build  green record matches"
    assert lines[4] == "  4  run?  gate:conform   may re-skip after run_m1"


def test_the_step_banner_numbers_the_step_and_wraps_its_description(capsys):
    digest = _digest()
    digest.step_start(
        "run_m1",
        ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"],
        "Builds the M1 tables for every acceptance configuration in the Rust kernel, mints the glyphs, emits GSUB and GPOS, compiles the font, and reads it back.",
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == ""
    assert lines[1].startswith("---- step 2 of 4  run_m1  step 0.0s  cycle 0.0s ----")
    body = lines[2:-2]
    assert len(body) > 1
    assert all(len(line) <= console.WRAP_COLUMNS for line in body)
    assert " ".join(body).startswith("Builds the M1 tables")
    assert lines[-2] == ""
    assert lines[-1] == "$ uv run python -m rebuild.pipeline.run_m1"


def test_a_step_outside_the_plan_still_opens_a_banner(capsys):
    digest = _digest()
    digest.step_start("ad-hoc", ["true"], "")
    assert "---- step ? of 4  ad-hoc " in capsys.readouterr().out


def test_an_alias_reports_under_the_plan_row_it_stands_for(capsys):
    digest = _digest(aliases={"run_m1:gates-only": "run_m1"})
    digest.step_start("run_m1:gates-only", ["true"], "")
    digest.child_line("run_m1:gates-only", console.STDOUT, "[warn] oracle cache reused")
    out = capsys.readouterr().out
    assert "---- step 2 of 4  run_m1  step" in out
    assert _bodies(out, "run_m1") == ["warn oracle cache reused"]


def test_every_surfaced_line_carries_the_step_and_both_clocks(capsys):
    clock = _Clock()
    digest = _digest(clock)
    digest.step_start("surface-build", ["true"], "")
    clock.advance(75)
    digest.child_line("surface-build", console.STDOUT, "[warn] cache miss")
    (line,) = _surfaced(capsys.readouterr().out)
    assert line == "  surface-build  step   1m15s  cycle   1m15s  warn cache miss"


def test_a_phase_pairs_with_the_timing_of_the_same_label(capsys):
    clock = _Clock()
    digest = _digest(clock)
    digest.step_start("run_m1", ["true"], "")
    digest.child_line("run_m1", console.STDOUT, "[phase] build_tables")
    clock.advance(243.1)
    digest.child_line("run_m1", console.STDOUT, "[t] build_tables 243.1s rss_gb=8.94")
    bodies = _bodies(capsys.readouterr().out, "run_m1")
    assert bodies == ["phase build_tables", "phase build_tables done 4m03s  rss_gb=8.94"]


def test_an_unpaired_timing_is_log_only(capsys, tmp_path):
    digest = _digest(log_dir=tmp_path / "run")
    digest.step_start("run_m1", ["true"], "")
    digest.child_line("run_m1", console.STDOUT, "[t] kernel_enumerate[default] 3.0s")
    assert _bodies(capsys.readouterr().out, "run_m1") == []
    assert (tmp_path / "run" / "02-run_m1.log").read_text() == "[t] kernel_enumerate[default] 3.0s\n"


def test_a_phase_closes_once_and_the_second_timing_is_log_only(capsys):
    digest = _digest()
    digest.step_start("run_m1", ["true"], "")
    digest.child_line("run_m1", console.STDOUT, "[phase] oracle")
    digest.child_line("run_m1", console.STDOUT, "[t] oracle 1.0s")
    digest.child_line("run_m1", console.STDOUT, "[t] oracle 1.0s")
    assert len(_bodies(capsys.readouterr().out, "run_m1")) == 2


def test_progress_is_throttled_to_the_silence_window(capsys):
    clock = _Clock()
    digest = _digest(clock, heartbeat_seconds=60)
    digest.step_start("run_m1", ["true"], "")
    digest.child_line("run_m1", console.STDOUT, "[progress] 1/6 configurations")
    clock.advance(10)
    digest.child_line("run_m1", console.STDOUT, "[progress] 2/6 configurations")
    assert _bodies(capsys.readouterr().out, "run_m1") == []
    clock.advance(51)
    digest.child_line("run_m1", console.STDOUT, "[progress] 3/6 configurations")
    assert _bodies(capsys.readouterr().out, "run_m1") == ["progress 3/6 configurations"]


def test_a_warning_is_never_throttled(capsys):
    digest = _digest(_Clock(), heartbeat_seconds=60)
    digest.step_start("run_m1", ["true"], "")
    for index in range(3):
        digest.child_line("run_m1", console.STDERR, f"[warn] warning {index}")
    assert len(_bodies(capsys.readouterr().out, "run_m1")) == 3


def test_the_heartbeat_surfaces_the_stored_counter_then_bare_silence(capsys):
    clock = _Clock()
    digest = _digest(clock, heartbeat_seconds=60)
    digest.step_start("surface-build", ["true"], "")
    digest.child_line("surface-build", console.STDERR, "[progress] 8192/15903 units")
    digest._heartbeat_tick()
    assert _bodies(capsys.readouterr().out, "surface-build") == []
    clock.advance(61)
    digest._heartbeat_tick()
    assert _bodies(capsys.readouterr().out, "surface-build") == ["progress 8,192/15,903 units"]
    clock.advance(61)
    digest._heartbeat_tick()
    assert _bodies(capsys.readouterr().out, "surface-build") == ["heartbeat"]


def test_a_counter_never_surfaces_under_a_phase_that_did_not_count_it(capsys):
    """A stored counter belongs to the phase that produced it and to no other. Left in place across a phase boundary, the next silent minute surfaces the last phase's tally under the new phase's name — every unit of the corpus counted under a phase that counts no units — which is a number a reader has every reason to believe."""
    clock = _Clock()
    digest = _digest(clock, heartbeat_seconds=60)
    digest.step_start("surface-build", ["true"], "")
    digest.child_line("surface-build", console.STDERR, "[phase] review.build units")
    digest.child_line("surface-build", console.STDERR, "[progress] 15903/15903 units")
    digest.child_line("surface-build", console.STDERR, "[t] review.build units 12.0s")
    digest.child_line("surface-build", console.STDERR, "[phase] review.build manifest+check")
    capsys.readouterr()
    clock.advance(61)
    digest._heartbeat_tick()
    assert _bodies(capsys.readouterr().out, "surface-build") == ["heartbeat"]


def test_a_counter_stored_under_an_opening_phase_is_dropped_with_it(capsys):
    """The same rule in the other direction: a counter that arrived before its phase opened belongs to whatever came before, and the phase line is where it stops being this step's latest."""
    clock = _Clock()
    digest = _digest(clock, heartbeat_seconds=60)
    digest.step_start("run_m1", ["true"], "")
    digest.child_line("run_m1", console.STDOUT, "[progress] 3/6 configurations")
    digest.child_line("run_m1", console.STDOUT, "[phase] oracle")
    capsys.readouterr()
    clock.advance(61)
    digest._heartbeat_tick()
    assert _bodies(capsys.readouterr().out, "run_m1") == ["heartbeat"]


def test_the_heartbeat_thread_starts_and_stops_with_the_digest(tmp_path):
    def names():
        return {thread.name for thread in threading.enumerate()}

    with console.Digest(log_dir=tmp_path / "run"):
        assert "digest-heartbeat" in names()
    assert "digest-heartbeat" not in names()


def test_the_closing_line_carries_the_outcome_the_figure_and_the_peak(capsys):
    digest = _digest()
    digest.step_start("run_m1", ["true"], "")
    digest.step_end("run_m1", _Result(elapsed=1988.0, peak_rss_bytes=19_600_000_000), "ok", "7 unmatched")
    assert _surfaced(capsys.readouterr().out) == [
        "  run_m1         step  33m08s  cycle    0.0s  ok  7 unmatched  rss 19.6G"
    ]


def test_a_pytest_lane_closes_saying_how_many_times_it_warned(capsys):
    """pytest's warnings summary is pages long and belongs in the log, but a lane that closes as a bare `ok` is a lane whose warnings nobody ever goes looking for. The count comes off the terminal summary rule, which is the one line that states it, and rides the closing line between the figure and the peak."""
    digest = _digest(steps=["gate:make-test", "gate:js"])
    digest.step_start("gate:make-test", ["make", "test"], "")
    digest.child_line("gate:make-test", console.STDOUT, "=========== warnings summary ===========")
    digest.child_line("gate:make-test", console.STDOUT, "test/test_x.py::test_y: nope")
    digest.child_line("gate:make-test", console.STDOUT, "=== 412 passed, 3 warnings in 91.20s ===")
    digest.step_end("gate:make-test", _Result(elapsed=91.2, peak_rss_bytes=2_000_000_000), "ok")
    assert _bodies(capsys.readouterr().out, "gate:make-test") == ["ok  3 warnings, see log  rss 2.0G"]

    digest.step_start("gate:js", ["node", "--test"], "")
    digest.child_line("gate:js", console.STDOUT, "=== 412 passed, 3 warnings in 91.20s ===")
    digest.step_end("gate:js", _Result(elapsed=2.0), "ok")
    assert _bodies(capsys.readouterr().out, "gate:js") == ["ok"]


def test_a_step_whose_peak_was_never_reaped_prints_no_peak(capsys):
    digest = _digest()
    digest.step_start("run_m1", ["true"], "")
    digest.step_end("run_m1", _Result(elapsed=2.0), "FAILED", "3 unexplained")
    assert _bodies(capsys.readouterr().out, "run_m1") == ["FAILED  3 unexplained"]


def test_a_step_with_no_child_still_closes(capsys):
    digest = _digest()
    digest.step_start("snapshot", None, "")
    digest.step_end("snapshot", None, "ok", "15,903 units")
    assert _bodies(capsys.readouterr().out, "snapshot") == ["ok  15,903 units"]


def test_closing_a_step_nobody_opened_still_says_so_and_writes_no_log(capsys, tmp_path):
    digest = _digest(log_dir=tmp_path / "run")
    digest.step_end("gate:conform", _Result(elapsed=4.0), "ok", "green")
    assert _bodies(capsys.readouterr().out, "gate:conform") == ["ok  green"]
    assert not (tmp_path / "run").exists()


def test_skipped_and_not_run_steps_announce_with_their_note_verbatim(capsys):
    digest = _digest()
    digest.step_skipped("gate:conform", "SKIPPED after run_m1 (green record matches)")
    digest.step_not_run("surface-build", "not run (run_m1 failed)")
    digest.note("run_m1", "ERROR: run_m1 did not write all three summary files")
    out = capsys.readouterr().out
    assert "skipped  SKIPPED after run_m1 (green record matches)" in out
    assert "not run  not run (run_m1 failed)" in out
    assert "ERROR: run_m1 did not write all three summary files" in out
    assert all(line.startswith("  ") for line in out.splitlines())


def test_a_substep_logs_and_surfaces_under_its_parent(capsys, tmp_path):
    digest = _digest(log_dir=tmp_path / "run")
    digest.step_start("census", ["true"], "", verbatim=True)
    digest.substep("census", "git-diff")
    digest.child_line("git-diff", console.STDOUT, '+  "volatile": {')
    digest.child_line("git-diff", console.STDOUT, "")
    digest.child_line("git-diff", console.STDERR, "[warn] the pins moved")
    out = capsys.readouterr().out
    assert out.splitlines()[-3:-1] == ['+  "volatile": {', ""]
    assert _bodies(out, "census") == ["warn the pins moved"]
    log = (tmp_path / "run" / "00-census.log").read_text().splitlines()
    assert log == ['+  "volatile": {', "", "stderr| [warn] the pins moved"]


def test_a_substep_that_spawns_after_its_parent_closed_leaves_nothing_open(capsys, tmp_path):
    """The census prints its diff once the refresh itself is done, so the sub-step's own lines open a state nobody else would ever close: its log handle would stay open until `stop()` and the heartbeat would announce a step that finished minutes ago, once a minute, for the rest of the pass. What it surfaces still carries the census's clock, because a line arriving late is late rather than a step that has just begun."""
    clock = _Clock()
    digest = _digest(clock, log_dir=tmp_path / "run", heartbeat_seconds=60)
    digest.step_start("census", ["true"], "", verbatim=True)
    clock.advance(30)
    digest.step_end("census", _Result(elapsed=30.0), "ok")
    capsys.readouterr()

    digest.substep("census", "git-diff")
    digest.child_line("git-diff", console.STDOUT, "[warn] the pins moved")
    digest.substep_end("git-diff")

    out = capsys.readouterr().out
    assert _bodies(out, "census") == ["warn the pins moved"]
    assert "step   30.0s" in out
    assert digest._open == {}
    clock.advance(61)
    digest._heartbeat_tick()
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "run" / "00-git-diff.log").exists()


def test_a_substep_close_leaves_a_parent_that_is_still_running_alone(capsys, tmp_path):
    """The ordinary case, where the sub-step runs under an open banner: the parent has its own closing line to print and its own figure to print it with, so nothing here may close it early."""
    digest = _digest(log_dir=tmp_path / "run")
    digest.step_start("census", ["true"], "")
    digest.substep("census", "git-diff")
    digest.child_line("git-diff", console.STDOUT, "[warn] the pins moved")
    digest.substep_end("git-diff")
    assert set(digest._open) == {"census"}
    digest.step_end("census", None, "ok", "updated")
    assert digest._open == {}
    assert _bodies(capsys.readouterr().out, "census") == ["warn the pins moved", "ok  updated"]


def test_a_plain_line_reaches_the_terminal_only_for_a_verbatim_step(capsys):
    digest = _digest()
    digest.step_start("run_m1", ["true"], "")
    digest.child_line("run_m1", console.STDOUT, "wrote rebuild/out/m1/tables.json")
    assert _surfaced(capsys.readouterr().out) == []


def test_a_failure_dumps_the_whole_step_log_verbatim(capsys, tmp_path):
    for log_dir in (None, tmp_path / "run"):
        digest = _digest(log_dir=log_dir)
        digest.step_start("gate:js", ["node", "--test"], "")
        digest.child_line("gate:js", console.STDOUT, "not ok 3 - keyboard")
        digest.child_line("gate:js", console.STDERR, "TypeError: nope")
        capsys.readouterr()
        digest.failure_dump("gate:js")
        assert capsys.readouterr().out == "\nnot ok 3 - keyboard\nstderr| TypeError: nope\n"


def test_the_summary_prints_the_table_the_cycle_lines_and_the_verdict(capsys):
    digest = _digest()
    rows = [
        console.SummaryRow(1, "snapshot", "ok", "15,903 units", 0.4),
        console.SummaryRow(2, "run_m1", "ok", "7 unmatched", 1988.0),
        console.SummaryRow(3, "surface-build", "skipped", "", None),
    ]
    digest.summary(rows, ["census pins  : unchanged", "READY - adjudicate at the docket"], console.VERDICT_OK)
    out = capsys.readouterr().out
    assert console.SUMMARY_BANNER in out
    assert "  1  snapshot       ok       15,903 units    0.4s" in out
    assert "  3  surface-build  skipped" in out
    assert "READY - adjudicate at the docket" in out
    assert out.rstrip().endswith("Cycle complete.")


def test_a_failed_summary_reprints_its_reasons_and_drops_the_next_line(capsys):
    digest = _digest()
    digest.summary(
        [console.SummaryRow(1, "gate:js", "FAILED", "1 failure", 3.0)],
        [],
        console.VERDICT_FAILED,
        ["gate:js: 1 unexplained failure(s)"],
    )
    out = capsys.readouterr().out
    assert "CYCLE FAILED:" in out
    assert "  - gate:js: 1 unexplained failure(s)" in out
    assert "Cycle complete." not in out


def test_an_interrupted_summary_says_so(capsys):
    digest = _digest()
    digest.summary([], [], console.VERDICT_INTERRUPTED, ["SIGINT: terminated 3 child process(es)."])
    out = capsys.readouterr().out
    assert "CYCLE INTERRUPTED:" in out
    assert "  - SIGINT: terminated 3 child process(es)." in out


def test_emit_and_emit_block_still_write_whole_lines(capsys):
    digest = console.Digest()
    digest.emit("one")
    digest.emit_block(["two", "three"])
    assert capsys.readouterr().out == "one\ntwo\nthree\n"


def test_a_digest_without_a_log_dir_touches_no_filesystem(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _digest() as digest:
        digest.plan_block(["4 steps: 2–3 will run, 1 skipped"])
        digest.step_start("run_m1", ["true"], "Builds the tables.")
        digest.child_line("run_m1", console.STDOUT, "[phase] load")
        digest.step_end("run_m1", _Result(), "ok", "")
    assert list(tmp_path.iterdir()) == []


def test_the_log_directory_holds_the_plan_the_terminal_copy_and_a_log_per_step(capsys, tmp_path):
    root = tmp_path / "build-logs"
    run = root / "2026-09-04T12.00.00Z-abc1234"
    with _digest(log_dir=run) as digest:
        digest.plan_block(["artifact cycle", "4 steps: 2–3 will run, 1 skipped"])
        digest.step_start("run_m1", ["true"], "Builds the tables.")
        print("Snapshotted the surface")
        digest.child_line("run_m1", console.STDOUT, "wrote tables.json")
        digest.child_line("run_m1", console.STDERR, "compiling")
        digest.child_line("run_m1", console.STDOUT, "[warn] no green recorded")
        digest.step_end("run_m1", _Result(), "ok", "")
    assert sorted(path.name for path in run.iterdir()) == ["02-run_m1.log", "plan.txt", "terminal.log"]
    assert (run / "plan.txt").read_text() == "artifact cycle\n4 steps: 2–3 will run, 1 skipped\n"
    assert (run / "02-run_m1.log").read_text().splitlines() == [
        "wrote tables.json",
        "stderr| compiling",
        "[warn] no green recorded",
    ]
    terminal = (run / "terminal.log").read_text()
    assert "artifact cycle" in terminal
    assert "Snapshotted the surface" in terminal
    assert "warn no green recorded" in terminal
    assert (root / "latest").is_symlink()
    assert (root / "latest").resolve() == run.resolve()
    assert capsys.readouterr().out.count("Snapshotted the surface") == 1


def test_a_step_that_spawns_nothing_leaves_no_log_behind(capsys, tmp_path):
    """One log per spawned step, and none for a step that spawns nothing. The snapshot and the retention pass both run inside the driver's own process, so a file opened when their banner went up would sit in every run directory at zero bytes — and a reader who opens a run directory wants a file in it to mean a child ran and said something."""
    run = tmp_path / "run"
    with _digest(log_dir=run) as digest:
        digest.step_start("snapshot", None, "Copies the surface that is currently served.")
        digest.step_end("snapshot", None, "ok", "clone -> var/review-pre-abc1234")
        digest.step_start("run_m1", ["true"], "Builds the tables.")
        digest.child_line("run_m1", console.STDOUT, "wrote tables.json")
        digest.step_end("run_m1", _Result(), "ok", "")
    assert sorted(path.name for path in run.iterdir()) == ["02-run_m1.log", "terminal.log"]
    surfaced = _surfaced(capsys.readouterr().out)
    assert any("clone -> var/review-pre-abc1234" in line for line in surfaced)


def test_a_skip_says_the_word_once(capsys, tmp_path):
    """The plan's notes wear a `SKIPPED (…)` wrapper because in the plan block they are the whole explanation and a reader greps them by it. A surfaced skip line already opens with the word, so the reason arrives unwrapped rather than as `skipped  SKIPPED (…)` — and a note that never wore the wrapper, like the sweep's mid-run re-decision, is left exactly as it was written."""
    assert console.skip_reason("SKIPPED (--keep-history)") == "--keep-history"
    assert console.skip_reason("SKIPPED (first run); no carry reads it") == "first run; no carry reads it"
    assert console.skip_reason("input closure unchanged") == "input closure unchanged"
    assert (
        console.skip_reason("SKIPPED after run_m1 — the key still matches")
        == "SKIPPED after run_m1 — the key still matches"
    )

    digest = _digest()
    digest.step_skipped("snapshot", "SKIPPED (the surface is not rewritten and no carry runs)")
    digest.step_skipped("gate:conform", "--skip-conform")
    lines = _surfaced(capsys.readouterr().out)
    assert lines[0].endswith("skipped  the surface is not rewritten and no carry runs")
    assert lines[1].endswith("skipped  --skip-conform")


def test_a_replay_reaches_the_terminal_copy_and_says_nothing_twice(capsys, tmp_path):
    """The driver answers three questions before the plan is resolved and so before there is a digest to catch them. They have already reached the terminal; what they are missing is the copy — so a replay writes them into terminal.log alone and the reader who watched the pass still sees each of them once."""
    run = tmp_path / "run"
    preamble = ["No carryable verdicts found; proceeding without carry."]
    print(preamble[0])
    with _digest(log_dir=run) as digest:
        digest.replay(preamble)
    assert (run / console.TERMINAL_LOG).read_text() == preamble[0] + "\n"
    assert capsys.readouterr().out == preamble[0] + "\n"

    without_a_directory = _digest()
    without_a_directory.replay(preamble)
    assert capsys.readouterr().out == ""


def test_a_crash_inside_the_digest_lands_its_traceback_in_the_terminal_copy(capsys, tmp_path):
    """A driver exception escapes the digest's context before Python prints it, and `stop` has already handed the real streams back by then — so the one line that explains a crash would reach the terminal and never the copy a watcher was told to read. The traceback goes into terminal.log on the way out, and only there: Python still prints it to the terminal once."""
    run = tmp_path / "run"
    before = (sys.stdout, sys.stderr)
    with pytest.raises(RuntimeError, match="boom"):
        with _digest(log_dir=run):
            raise RuntimeError("boom")
    assert (sys.stdout, sys.stderr) == before
    copy = (run / console.TERMINAL_LOG).read_text()
    assert "Traceback (most recent call last):" in copy
    assert "RuntimeError: boom" in copy
    assert "RuntimeError: boom" not in capsys.readouterr().out


def test_the_latest_symlink_moves_to_the_newest_run(tmp_path):
    root = tmp_path / "build-logs"
    for name in ("first", "second"):
        with console.Digest(log_dir=root / name):
            pass
    assert (root / "latest").resolve() == (root / "second").resolve()
    assert sorted(path.name for path in root.iterdir()) == ["first", "latest", "second"]


_CHILD_SCRIPT = (
    "import sys\n"
    "tag = sys.argv[1]\n"
    "for i in range(200):\n"
    "    print(f'[warn] {tag}-out-{i:04d}', flush=True)\n"
    "    print(f'[warn] {tag}-err-{i:04d}', file=sys.stderr, flush=True)\n"
)


def _pump(digest, name, pipe, stream):
    for line in pipe:
        digest.child_line(name, stream, line)
    pipe.close()


def _run_child(digest, tag):
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD_SCRIPT, tag],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    threads = [
        threading.Thread(target=_pump, args=(digest, tag, proc.stdout, console.STDOUT)),
        threading.Thread(target=_pump, args=(digest, tag, proc.stderr, console.STDERR)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    proc.wait()


def test_two_children_at_once_never_splice_a_line(capsys, tmp_path):
    run = tmp_path / "run"
    digest = console.Digest(steps=["childA", "childB"], log_dir=run)
    for tag in ("childA", "childB"):
        digest.step_start(tag, [sys.executable, "-c", "...", tag], "")
    threads = [threading.Thread(target=_run_child, args=(digest, tag)) for tag in ("childA", "childB")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for tag in ("childA", "childB"):
        digest.step_end(tag, None, "ok", "")

    pattern = re.compile(
        r"^  (childA|childB)\s+step\s+\S+\s+cycle\s+\S+\s+warn (childA|childB)-(out|err)-\d{4}$"
    )
    body = [line for line in capsys.readouterr().out.splitlines() if " warn child" in line]
    assert len(body) == 800
    for line in body:
        match = pattern.match(line)
        assert match is not None, line
        assert match.group(1) == match.group(2), line
    for index, tag in enumerate(("childA", "childB"), start=1):
        lines = (run / f"{index:02d}-{tag}.log").read_text().splitlines()
        assert len(lines) == 400
        assert sum(1 for line in lines if line.startswith(console.STDERR_TAG)) == 200
        assert all(f"[warn] {tag}-" in line for line in lines)
