"""The artifact cycle's output format, and the line protocol its children use to report into it. Both are in one module so the protocol's writers and its reader share one definition.

Everything written here is append-only: no color, ANSI escapes, spinners, or carriage returns. A redirect and a terminal get the same bytes, and `tail -f` never shows a line rewritten. Progress is a counter line, printed at most once per heartbeat window (60 seconds by default), instead of a progress bar. Counts carry thousand separators (`fmt_count`), durations use minutes and hours past a minute (`fmt_duration`), and peak memory is in decimal gigabytes, the unit `peak_rss` uses.

The protocol is four line prefixes. `[phase] <name>` opens a stretch of work and `[t] <label> <secs>s` closes it, optionally followed by a space or tab and a tail. `[progress] <k>/<n> <unit>` is a counter, with `?` for an unknown total. `[warn] <text>` always reaches the terminal. The verdict chain's `[chain]` banner and its two result lines (fixpoint and failure) are defined here too, because the chain writes them and the cycle splits the chain's output on them. `INNER_LINE` is the one pattern for the `[t]` line; `cycle_timings` parses the journal with it, and `timing()` writes a line it matches.

A `[phase]` line is surfaced when it arrives. A `[t]` line whose label matches an open phase closes it, and the surfaced line carries the child's measured duration and the tail. A `[t]` line with no open phase of that label, such as the crate's per-configuration enumerate lines, the oracle's per-configuration lines, or the chain's step timings, goes to the log only. This keeps a step that prints many timings from flooding the terminal without any producer knowing which of its timings the digest shows.

Every surfaced line carries its step name, the step's elapsed time, and the cycle's elapsed time, because several steps can be open at once.

pytest, node, and git do not print this protocol, so adapters read their output into the same events. A pytest percent marker becomes a `Progress`, a pytest `FAILED`/`ERROR` summary line or a TAP `not ok` becomes a `Warn`, and `warning_events` runs on every step to catch the two warning shapes Python prints. The adapters strip ANSI first, because pytest colors its summary when `FORCE_COLOR` is set, as it is under the agent harness.

Every line a child prints goes to that step's log, in arrival order, with stderr lines tagged; the terminal gets the digest. A failed step replays its whole log under its own banner. The digest writes each line in one call under one lock, so lines from overlapping children interleave but never split.

This module imports nothing else from the repo. `rebuild.tools.verdict_chain` calls `phase()`, so this module is in the verdict plumbing's code closure, and anything it imported would be too. An edit to `cycle_timings` or a width module cannot change a verdict but would re-run the whole chain if it were in that closure. So `cycle_timings` imports `INNER_LINE` from here, and `fmt_rss` repeats the gigabyte divisor instead of importing `peak_rss`. `rebuild/test_console.py` checks the divisor matches and that this module has no repo imports, and `rebuild/test_plumbing_closure.py` checks the chain's closure.

With `log_dir=None`, a digest does no `mkdir`, `open`, or symlink, so the driver's tests can build one without touching the repo; the rebuild suite's contracts lane audits every read and write against the live trees. In that mode a step's lines are kept in memory so a failure dump can replay them. With a log directory, they are read back from disk, so a full cycle's output never has to fit in the driver's memory.

Every writer takes `file=None` and every digest takes `out=None`, meaning `sys.stdout` at the time the line is written. `Digest.start` replaces `sys.stdout` and `sys.stderr` with tees into `terminal.log` so bare `print` calls reach the copy, and a stream bound earlier would bypass the tee.
"""

from __future__ import annotations

import os
import re
import sys
import textwrap
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Protocol

TIMING = "[t] "
PHASE = "[phase] "
PROGRESS = "[progress] "
WARN = "[warn] "

INNER_LINE = re.compile(r"^\[t\] (.+?) (\d+(?:\.\d+)?)s(?:[ \t](.*))?$", re.MULTILINE)

CHAIN_BANNER = "[chain] "
FIXPOINT_LINE = CHAIN_BANNER + "fixpoint: "
FAILED_LINE = CHAIN_BANNER + "failed: "

STDOUT = "stdout"
STDERR = "stderr"
STDERR_TAG = "stderr| "

TERMINAL_LOG = "terminal.log"
PLAN_TXT = "plan.txt"
LATEST_LINK = "latest"

SUMMARY_BANNER = "ARTIFACT CYCLE SUMMARY"
VERDICT_OK = "ok"
VERDICT_FAILED = "failed"
VERDICT_INTERRUPTED = "interrupted"

STATUS_RUN = "run"
STATUS_SKIP = "skip"
STATUS_MAYBE = "run?"

WRAP_COLUMNS = 76
_NAME_WIDTH_FLOOR = 12
_DURATION_WIDTH = 7
_TICK_SECONDS = 1.0

_BYTES_PER_GB = 1e9
_PROGRESS_BODY = re.compile(r"^(\d+)/(\d+|\?)(?:\s+(\S.*))?$")
_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PYTEST_PERCENT = re.compile(r"\[\s*(\d{1,3})%\]")
_PYTEST_WARNINGS = re.compile(r"\b(\d+) warnings?\b")
_NODE_FAIL = re.compile(r"^#\s+fail\s+(\d+)$")
_PYTHON_WARNING = re.compile(r"^\S+:\d+: \w*Warning: ")
_SKIPPED_WRAPPER = re.compile(r"^SKIPPED \((.*)\)(?=$|[;,] )")


@dataclass(frozen=True)
class Timing:
    """A closed stretch of work, parsed from `[t] <label> <secs>s` and an optional tail after a space or tab. `tail` is whatever follows the seconds, such as a peak-RSS token or a parenthesized count; the digest reprints it and never parses it."""

    label: str
    seconds: float
    tail: str = ""


@dataclass(frozen=True)
class Phase:
    """An opened stretch of work, waiting for the `Timing` of the same label to close it."""

    name: str


@dataclass(frozen=True)
class Progress:
    """A counter: `done`/`total` over a named unit for a producer that knows its total, or a bare `percent` for pytest, which reports only a percentage. A `total` of None renders as `?`."""

    done: int | None = None
    total: int | None = None
    unit: str = ""
    percent: int | None = None

    @property
    def text(self) -> str:
        if self.done is None:
            return "?" if self.percent is None else f"{self.percent}%"
        total = "?" if self.total is None else fmt_count(self.total)
        return f"{fmt_count(self.done)}/{total} {self.unit}".rstrip()


@dataclass(frozen=True)
class Warn:
    """A line the watcher should see immediately. Warnings are never throttled, and producers emit few of them."""

    text: str


Event = Timing | Phase | Progress | Warn


class StepResult(Protocol):
    """The fields the digest reads from the driver's step result, declared structurally because the driver imports this module and this module cannot import the driver."""

    elapsed: float
    peak_rss_bytes: int | None


@dataclass(frozen=True)
class PlanRow:
    """One row of the plan block: its status, its name, the reason it is skipped (or the condition under which it may still run), and the argv it would spawn. A skipped row's note is kept verbatim, because it is all a skipped step shows."""

    status: str
    name: str
    note: str = ""
    argv: str = ""


@dataclass(frozen=True)
class SummaryRow:
    """One row of the closing table. `figure` is the step's headline number, already formatted by the driver in the step's own unit; `seconds` is None for a step that did not run."""

    number: int | None
    name: str
    outcome: str
    figure: str = ""
    seconds: float | None = None


def fmt_count(value: int) -> str:
    return f"{value:,}"


def fmt_duration(seconds: float) -> str:
    """Format a duration as tenths of a second under a minute, `33m08s` under an hour, and `1h02m` above. The threshold is 59.95, not 60, because a value from 59.95 to 60 would otherwise print as `60.0s`."""
    value = max(0.0, float(seconds))
    if value < 59.95:
        return f"{value:.1f}s"
    total = int(round(value))
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def fmt_rss(byte_count: int | None) -> str:
    """Format a byte count as decimal gigabytes to one place, or return the empty string when the peak is unknown so the caller can drop the token. The unit is `peak_rss`'s decimal gigabyte, at one decimal place instead of that module's two. The divisor is repeated here because this module imports nothing from the repo, and `rebuild/test_console.py` checks that the two match."""
    if byte_count is None:
        return ""
    return f"{byte_count / _BYTES_PER_GB:.1f}G"


def _write(line: str, file: IO[str] | None) -> None:
    stream = sys.stdout if file is None else file
    stream.write(line + "\n")
    stream.flush()


def say(text: str, *, file: IO[str] | None = None) -> None:
    """Write one line in a single call. `print` writes the text and the newline separately, so another thread's output can land between them and leave a `[t]` or `[phase]` line mid-line, where neither `parse_line` nor `cycle_timings.parse_inner_timings` (both anchored at line start) can read it."""
    _write(text, file)


def phase(name: str, *, file: IO[str] | None = None) -> None:
    _write(PHASE + name, file)


def progress(done: int, total: int | None, unit: str, *, file: IO[str] | None = None) -> None:
    _write(f"{PROGRESS}{done}/{'?' if total is None else total} {unit}".rstrip(), file)


def warn(text: str, *, file: IO[str] | None = None) -> None:
    _write(WARN + text, file)


def timing(label: str, seconds: float, tail: str | None = None, *, file: IO[str] | None = None) -> None:
    """Write a `[t]` line in the format `INNER_LINE` parses, so a new producer cannot write a variant the journal drops. Existing print sites format their own lines."""
    suffix = "" if not tail else f" {tail}"
    _write(f"{TIMING}{label} {seconds:.1f}s{suffix}", file)


def parse_line(line: str) -> Event | None:
    """Return one line of child output as an event, or None when it is not a protocol line. Like `INNER_LINE`, it is anchored at the start of the line, so an indented or embedded prefix is ordinary text, and a `[t]` line with missing or malformed seconds is not an event."""
    text = line.rstrip("\r\n")
    if text.startswith(PHASE):
        name = text[len(PHASE) :].strip()
        return Phase(name) if name else None
    if text.startswith(PROGRESS):
        return _parse_progress(text[len(PROGRESS) :].strip())
    if text.startswith(WARN):
        body = text[len(WARN) :]
        return Warn(body) if body.strip() else None
    match = INNER_LINE.match(text)
    if match is None:
        return None
    return Timing(match.group(1), float(match.group(2)), match.group(3) or "")


def _parse_progress(body: str) -> Progress | None:
    match = _PROGRESS_BODY.match(body)
    if match is None:
        return None
    total = None if match.group(2) == "?" else int(match.group(2))
    return Progress(done=int(match.group(1)), total=total, unit=(match.group(3) or "").strip())


def pytest_events(line: str) -> Event | None:
    """Return a pytest output line as an event: a summary `FAILED `/`ERROR ` line becomes a `Warn`, and the percent marker on a progress line becomes a `Progress`. ANSI escapes are stripped first, because pytest colors its summary when `FORCE_COLOR` is set, as it is under the agent harness."""
    text = _ANSI_SGR.sub("", line).rstrip()
    if text.startswith(("FAILED ", "ERROR ")):
        return Warn(text)
    match = _PYTEST_PERCENT.search(text)
    if match is not None:
        return Progress(percent=int(match.group(1)))
    return None


def pytest_warning_count(line: str) -> int | None:
    """Return the warnings count from pytest's closing summary rule, for the step's closing `N warnings, see log`. Only a line starting with `=` is read, because warning bodies carry other counts."""
    text = _ANSI_SGR.sub("", line).strip()
    if not text.startswith("="):
        return None
    match = _PYTEST_WARNINGS.search(text)
    return int(match.group(1)) if match else None


def node_test_events(line: str) -> Event | None:
    """Return a node test runner TAP line as a `Warn`: a `not ok` assertion at any depth, or the closing `# fail N` when N is not zero."""
    text = line.strip()
    if text.startswith("not ok "):
        return Warn(text)
    match = _NODE_FAIL.match(text)
    if match is not None and int(match.group(1)) > 0:
        return Warn(text)
    return None


def warning_events(line: str) -> Warn | None:
    """Return a `Warn` for the two warning shapes code in this repo prints, which is why every step gets this adapter: a line starting with `warning:` in any case after leading whitespace (the standing-fill tripwire indents its line), and Python's `warnings.warn` format, which run_m1's spec load writes to stderr."""
    text = line.strip()
    if text.lower().startswith("warning:"):
        return Warn(text)
    if _PYTHON_WARNING.match(text):
        return Warn(text)
    return None


STEP_ADAPTERS: dict[str, Callable[[str], Event | None]] = {
    "gate:js": node_test_events,
    "gate:make-test": pytest_events,
    "gate:rebuild-contracts": pytest_events,
}


def adapter_for(name: str) -> Callable[[str], Event | None] | None:
    """Return the adapter for the third-party child a step spawns, keyed by the plan's step name. A step's output format is a property of the child, so it is module data instead of a `step_start` argument, which would change the spawn signature every test fake matches."""
    return STEP_ADAPTERS.get(name)


def skip_reason(note: str) -> str:
    """Return a skip note with its `SKIPPED (…)` wrapper removed. The plan block and a surfaced skip line both print `skipped` in their own column, so the whole note would say it twice. The notes keep the wrapper because the plan prints them as the full explanation and readers grep for it. A note without the wrapper is returned unchanged."""
    match = _SKIPPED_WRAPPER.match(note)
    return f"{match.group(1)}{note[match.end() :]}" if match else note


def counts_line(rows: Sequence[PlanRow]) -> str:
    """Return the plan block's summary line: how many steps there are, how many will run, and how many are skipped. When a step is undecided (gate:conform can still be skipped after run_m1 shows its inputs unchanged), the run count is a range."""
    will_run = sum(1 for row in rows if row.status == STATUS_RUN)
    maybe = sum(1 for row in rows if row.status == STATUS_MAYBE)
    skipped = sum(1 for row in rows if row.status == STATUS_SKIP)
    span = f"{will_run}" if not maybe else f"{will_run}–{will_run + maybe}"
    return f"{len(rows)} steps: {span} will run, {skipped} skipped"


def plan_lines(rows: Sequence[PlanRow]) -> list[str]:
    """Return one line per step (status, name, note), with each row that spawns a child followed by its `$ argv`. The plan is not numbered, because parallel gates and conditional skips decide the start order only when the steps run."""
    if not rows:
        return []
    status_width = max(len(row.status) for row in rows)
    name_width = max(len(row.name) for row in rows)
    lines: list[str] = []
    for row in rows:
        head = f"  {row.status:<{status_width}}  {row.name:<{name_width}}"
        lines.append(f"{head}  {row.note}".rstrip())
        if row.argv:
            lines.append(f"{' ' * (len(head) - name_width)}$ {row.argv}")
    return lines


@dataclass
class _StepState:
    name: str
    display: str
    number: int | None
    started: float
    last_surfaced: float
    verbatim: bool = False
    transient: bool = False
    warnings: int | None = None
    adapter: Callable[[str], Event | None] | None = None
    handle: IO[str] | None = None
    log_path: Path | None = None
    lines: list[str] = field(default_factory=list)
    phases: dict[str, float] = field(default_factory=dict)
    pending: Progress | None = None


class _Tee:
    """A stream that writes to two places. Only `write` and `flush` are duplicated; `isatty`, `fileno`, `encoding`, and every other attribute come from the wrapped stream, so code under the tee still detects a terminal correctly."""

    def __init__(self, stream: IO[str], copy: IO[str], lock: threading.RLock) -> None:
        self._stream = stream
        self._copy = copy
        self._lock = lock

    def write(self, text: str) -> int:
        with self._lock:
            written = self._stream.write(text)
            self._copy.write(text)
            return written

    def flush(self) -> None:
        with self._lock:
            self._stream.flush()
            self._copy.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

    def fileno(self) -> int:
        return self._stream.fileno()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


class Digest:
    """The cycle's renderer: every line the terminal shows is written here, and every line a child prints passes through here to that step's log.

    It takes the plan's step names (for the step column's width), the run's log directory (None for no filesystem access), an alias map that reports a spawn under the plan row for it, and three test hooks: the output stream, the clock, and the heartbeat window. Step banners take consecutive numbers under the output lock, and the logs and the closing table use those numbers; skipped and unstarted steps are unnumbered. The driver uses it as a context manager around everything after the dry-run return, so the tee and the heartbeat thread are installed and removed in one place.

    `emit` and `emit_block` are lock-serialized writers. `artifact_cycle._Emitter` is an alias of this class. Every constructor argument is optional, so `Digest()` is a serialized stdout writer with no plan and no directory, which the driver's tests pass to its stage functions.
    """

    def __init__(
        self,
        steps: Sequence[str] | None = None,
        log_dir: Path | None = None,
        aliases: dict[str, str] | None = None,
        out: IO[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_seconds: float = 60.0,
    ) -> None:
        self.steps = list(steps or [])
        self.log_dir = None if log_dir is None else Path(log_dir)
        self.aliases = dict(aliases or {})
        self._out = out
        self._clock = clock
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._lock = threading.RLock()
        self._t0 = clock()
        self._open: dict[str, _StepState] = {}
        self._closed_starts: dict[str, float] = {}
        self._substeps: dict[str, str] = {}
        self._numbers: dict[str, int] = {}
        self._name_width = max([_NAME_WIDTH_FLOOR, *(len(name) for name in self.steps)])
        self._terminal: IO[str] | None = None
        self._saved_streams: tuple[IO[str], IO[str]] | None = None
        self._stopping = threading.Event()
        self._heartbeat: threading.Thread | None = None

    @property
    def _stream(self) -> IO[str]:
        return sys.stdout if self._out is None else self._out

    def emit(self, text: str) -> None:
        with self._lock:
            stream = self._stream
            stream.write(text + "\n")
            stream.flush()

    def emit_block(self, lines: Sequence[str]) -> None:
        with self._lock:
            stream = self._stream
            for line in lines:
                stream.write(line + "\n")
            stream.flush()

    def start(self) -> Digest:
        with self._lock:
            if self.log_dir is not None:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                self._terminal = open(self.log_dir / TERMINAL_LOG, "a", encoding="utf-8", buffering=1)
                self._saved_streams = (sys.stdout, sys.stderr)
                sys.stdout = _Tee(sys.stdout, self._terminal, self._lock)
                sys.stderr = _Tee(sys.stderr, self._terminal, self._lock)
                self._link_latest()
            self._stopping.clear()
            self._heartbeat = threading.Thread(
                target=self._heartbeat_loop, name="digest-heartbeat", daemon=True
            )
            self._heartbeat.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        thread = self._heartbeat
        if thread is not None:
            thread.join(timeout=_TICK_SECONDS * 3)
        with self._lock:
            self._heartbeat = None
            for state in self._open.values():
                self._close_log(state)
            if self._saved_streams is not None:
                sys.stdout, sys.stderr = self._saved_streams
                self._saved_streams = None
            if self._terminal is not None:
                self._terminal.close()
                self._terminal = None

    def __enter__(self) -> Digest:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        exc = exc_info[1]
        if isinstance(exc, BaseException):
            with self._lock:
                if self._terminal is not None:
                    self._terminal.write("".join(traceback.format_exception(exc)))
                    self._terminal.flush()
        self.stop()

    def replay(self, lines: Sequence[str]) -> None:
        """Write lines the terminal has already shown into `terminal.log` only. The driver prints some lines before the digest exists, and this copies them into the log without printing them again."""
        with self._lock:
            if self._terminal is None:
                return
            for line in lines:
                self._terminal.write(line + "\n")
            self._terminal.flush()

    def plan_block(self, lines: Sequence[str]) -> None:
        """Write the plan to the terminal and the same lines to `plan.txt`."""
        self.emit_block(lines)
        if self.log_dir is not None:
            path = self.log_dir / PLAN_TXT
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def step_start(
        self,
        name: str,
        argv: Sequence[str] | None = None,
        describe: str = "",
        *,
        verbatim: bool = False,
    ) -> None:
        """Open a step: print its rule line, its description wrapped at `WRAP_COLUMNS`, a blank line, and the argv it will spawn. `verbatim` sends the step's unparsed lines to the terminal as well as the log, for a child whose output a human must act on, such as the job-costs constants diff on a pass where that check fails."""
        with self._lock:
            state = self._state_for(name, banner=True)
            state.verbatim = verbatim
            lines = ["", self._rule(self._banner_text(state))]
            if describe:
                lines.extend(textwrap.wrap(describe, width=WRAP_COLUMNS))
            lines.append("")
            if argv:
                lines.append(f"$ {' '.join(argv)}")
            self.emit_block(lines)

    def step_end(self, name: str, result: StepResult | None, outcome: str, figure: str = "") -> None:
        """Close a step with its outcome, headline figure, and peak RSS. The elapsed time is the driver's measurement when it has one, so it agrees with the timings journal. A pytest step that emitted warnings closes with `N warnings, see log`; the warnings summary itself stays in the log."""
        with self._lock:
            state = self._open.get(self._key(name))
            now = self._clock()
            if state is not None and result is not None:
                state.started = now - result.elapsed
            rss = fmt_rss(None if result is None else result.peak_rss_bytes)
            warned = (
                "" if not (state and state.warnings) else f"{fmt_count(state.warnings)} warnings, see log"
            )
            body = "  ".join(part for part in (outcome, figure, warned, f"rss {rss}" if rss else "") if part)
            if state is None:
                self._one_line(name, body)
                return
            self._surface(state, body, now=now)
            self._close(state)

    def step_skipped(self, name: str, note: str = "") -> None:
        """Announce a step the plan skipped, at the point it would have run. The note's `SKIPPED (…)` wrapper is removed first, so the line reads `skipped  <reason>`."""
        with self._lock:
            self._one_line(name, "  ".join(part for part in ("skipped", skip_reason(note)) if part))

    def step_not_run(self, name: str, reason: str = "") -> None:
        with self._lock:
            self._one_line(name, "  ".join(part for part in ("not run", reason) if part))

    def note(self, name: str, text: str) -> None:
        """Surface a line the driver itself says about a step, such as an error it caught or a green it did not record, with the step column and both clocks like every other surfaced line."""
        with self._lock:
            self._one_line(name, text)

    def substep(self, parent: str, name: str) -> None:
        """Log a sub-step under another step. The census's invariant diff and the job-costs diff belong to steps without being plan steps, and this sends their lines to the parent's log and column without adding a keyword to the spawn signature."""
        with self._lock:
            self._substeps[name] = parent

    def substep_end(self, name: str) -> None:
        """Close the state a sub-step's lines opened. When a sub-step's lines arrive after its parent has closed, they open a transient state that nothing else closes, which would keep its log open until `stop()` and make the heartbeat report a finished step. A parent that is still open is left alone, because the sub-step did not open it."""
        with self._lock:
            state = self._open.get(self._key(name))
            if state is not None and state.transient:
                self._close(state)

    def child_line(self, name: str, stream: str, line: str) -> Event | None:
        """Log one line of a child's output, surface it if it is an event worth showing, and return the event it parsed.

        The line is parsed as a protocol line first, then by the step's adapter, then by `warning_events`, so a heuristic never overrides a protocol line and a child that does not use the protocol still cannot hide a warning. A `Phase` opens; a `Timing` that matches an open phase closes it and carries its duration and tail; a `Timing` that matches nothing is logged only. A `Warn` always surfaces. A `Progress` surfaces only when the step has been silent for the whole heartbeat window; otherwise it is stored as the step's latest, which the heartbeat prints when the window runs out.
        """
        with self._lock:
            text = line.rstrip("\r\n")
            state = self._state_for(name, banner=False)
            self._log(state, f"{STDERR_TAG}{text}" if stream == STDERR else text)
            if state.adapter is pytest_events:
                counted = pytest_warning_count(text)
                if counted is not None:
                    state.warnings = counted
            event = parse_line(text)
            if event is None and state.adapter is not None:
                event = state.adapter(text)
            if event is None:
                event = warning_events(text)
            if event is None:
                if state.verbatim:
                    self.emit(text)
                return None
            self._surface_event(state, event)
            return event

    def failure_dump(self, name: str) -> None:
        """Replay a failed step's whole log under its own banner, stderr tags included. Call it before `step_end`, which closes and forgets the step."""
        with self._lock:
            state = self._open.get(self._key(name))
            lines = self._recorded_lines(state)
            if lines:
                self.emit_block(["", *lines])

    def summary(
        self,
        rows: Sequence[SummaryRow],
        cycle_lines: Sequence[str] = (),
        verdict: str = VERDICT_OK,
        reasons: Sequence[str] = (),
    ) -> None:
        """Print the closing block: the step table, the cycle-level lines the driver composed, then the verdict. Reasons are printed verbatim under `CYCLE FAILED:` or `CYCLE INTERRUPTED:`, and a green run ends with `Cycle complete.` and no reasons."""
        with self._lock:
            numbered = [replace(row, number=self._number(row.name)) for row in rows]
            numbered.sort(key=lambda row: row.number if row.number is not None else float("inf"))
        lines = ["", self._rule(SUMMARY_BANNER), *_summary_table(numbered), ""]
        lines.extend(cycle_lines)
        if verdict == VERDICT_OK:
            lines.extend(["", "Cycle complete."])
        else:
            heading = "CYCLE INTERRUPTED:" if verdict == VERDICT_INTERRUPTED else "CYCLE FAILED:"
            lines.extend(["", heading, *(f"  - {reason}" for reason in reasons)])
        self.emit_block(lines)

    def _heartbeat_loop(self) -> None:
        while not self._stopping.wait(_TICK_SECONDS):
            self._heartbeat_tick()

    def _heartbeat_tick(self) -> None:
        """Surface a line for every open step that has been silent for the whole window: its latest unsurfaced counter if there is one, and otherwise `heartbeat`. This covers steps whose child prints nothing for minutes, such as the compile and the read-back."""
        with self._lock:
            now = self._clock()
            for state in list(self._open.values()):
                if now - state.last_surfaced < self._heartbeat_seconds:
                    continue
                pending = state.pending
                state.pending = None
                self._surface(
                    state, f"progress {pending.text}" if pending is not None else "heartbeat", now=now
                )

    def _surface_event(self, state: _StepState, event: Event) -> None:
        now = self._clock()
        if isinstance(event, Phase):
            state.phases[event.name] = now
            state.pending = None
            self._surface(state, f"phase {event.name}", now=now)
            return
        if isinstance(event, Timing):
            if state.phases.pop(event.label, None) is None:
                return
            state.pending = None
            done = f"phase {event.label} done {fmt_duration(event.seconds)}"
            self._surface(state, f"{done}  {event.tail}" if event.tail else done, now=now)
            return
        if isinstance(event, Warn):
            self._surface(state, f"warn {event.text}", now=now)
            return
        if now - state.last_surfaced >= self._heartbeat_seconds:
            state.pending = None
            self._surface(state, f"progress {event.text}", now=now)
        else:
            state.pending = event

    def _one_line(self, name: str, body: str) -> None:
        state = self._open.get(self._key(name))
        if state is not None:
            self._surface(state, body)
            return
        now = self._clock()
        key = self._key(name)
        display = self._display(key)
        self._surface(
            _StepState(
                name=key,
                display=display,
                number=self._number(display),
                started=self._closed_starts.get(key, now),
                last_surfaced=now,
            ),
            body,
            now=now,
        )

    def _surface(self, state: _StepState, body: str, *, now: float | None = None) -> None:
        moment = self._clock() if now is None else now
        state.last_surfaced = moment
        step = fmt_duration(moment - state.started)
        cycle = fmt_duration(moment - self._t0)
        self.emit(
            f"  {state.display:<{self._name_width}}  step {step:>{_DURATION_WIDTH}}  "
            f"cycle {cycle:>{_DURATION_WIDTH}}  {body}"
        )

    def _rule(self, text: str) -> str:
        head = f"---- {text} "
        return head + "-" * max(4, WRAP_COLUMNS - len(head))

    def _banner_text(self, state: _StepState) -> str:
        number = "?" if state.number is None else state.number
        now = self._clock()
        return (
            f"step {number}  {state.display}  "
            f"step {fmt_duration(now - state.started)}  cycle {fmt_duration(now - self._t0)}"
        )

    def _key(self, name: str) -> str:
        return self._substeps.get(name, name)

    def _display(self, name: str) -> str:
        return self.aliases.get(name, name)

    def _number(self, display: str) -> int | None:
        return self._numbers.get(display)

    def _state_for(self, name: str, *, banner: bool) -> _StepState:
        """Return the state a line belongs to, opening one if needed. A `child_line` for a step that was never started still gets a state, so its output is logged instead of dropped. That state is transient, because no banner opened it and nothing else will close it (`substep_end` closes it). It is dated from the start of the step of that name that already ran, so a line arriving after a step closed shows as late instead of as a new step."""
        key = self._key(name)
        state = self._open.get(key)
        if state is not None:
            if not banner:
                return state
            self._close_log(state)
        now = self._clock()
        display = self._display(key)
        if banner and display not in self._numbers:
            self._numbers[display] = len(self._numbers) + 1
        state = _StepState(
            name=key,
            display=display,
            number=self._number(display),
            started=now if banner else self._closed_starts.get(key, now),
            last_surfaced=now,
            transient=not banner,
            adapter=adapter_for(display),
        )
        self._open[key] = state
        return state

    def _open_log(self, state: _StepState) -> None:
        """Open a step's log file on the first line its child prints, not when its banner goes up, so a step that spawns nothing (the retention pass runs in this process) leaves no empty file in the run directory."""
        if self.log_dir is None:
            return
        number = 0 if state.number is None else state.number
        state.log_path = self.log_dir / f"{number:02d}-{state.display.replace(':', '-')}.log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        state.handle = open(state.log_path, "a", encoding="utf-8", buffering=1)

    def _close(self, state: _StepState) -> None:
        """Forget an open step, keeping only its start time, which a line arriving after the close is dated from."""
        self._close_log(state)
        self._closed_starts[state.name] = state.started
        self._open.pop(state.name, None)

    def _close_log(self, state: _StepState) -> None:
        if state.handle is not None:
            state.handle.close()
            state.handle = None

    def _log(self, state: _StepState, text: str) -> None:
        if state.handle is None:
            self._open_log(state)
        if state.handle is not None:
            state.handle.write(text + "\n")
        else:
            state.lines.append(text)

    def _recorded_lines(self, state: _StepState | None) -> list[str]:
        if state is None:
            return []
        if state.log_path is not None and state.log_path.exists():
            return state.log_path.read_text(encoding="utf-8").splitlines()
        return list(state.lines)

    def _link_latest(self) -> None:
        """Point `latest` at this run's log directory, replacing the old link. A filesystem that cannot hold a symlink loses only the link; the cycle does not fail."""
        if self.log_dir is None:
            return
        link = self.log_dir.parent / LATEST_LINK
        staging = self.log_dir.parent / f".{LATEST_LINK}.{os.getpid()}"
        try:
            staging.unlink(missing_ok=True)
            os.symlink(self.log_dir.name, staging, target_is_directory=True)
            os.replace(staging, link)
        except OSError:
            pass


def _summary_table(rows: Sequence[SummaryRow]) -> list[str]:
    header = ("#", "step", "outcome", "figure", "time")
    body = [
        (
            "-" if row.number is None else str(row.number),
            row.name,
            row.outcome,
            row.figure,
            "" if row.seconds is None else fmt_duration(row.seconds),
        )
        for row in rows
    ]
    widths = [max(len(cell) for cell in column) for column in zip(header, *body)] if body else []
    if not widths:
        return []
    lines = []
    for cells in (header, *body):
        number, name, outcome, figure, elapsed = cells
        lines.append(
            f"  {number:>{widths[0]}}  {name:<{widths[1]}}  {outcome:<{widths[2]}}  "
            f"{figure:<{widths[3]}}  {elapsed:>{widths[4]}}".rstrip()
        )
    return lines
