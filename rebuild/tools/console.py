"""One output format for the artifact cycle, and the line protocol that lets a child speak into it. Both halves live here on purpose: a protocol whose writer and reader sit in different trees is a protocol that drifts, and the `[t]` line alone already has four writers across three trees and one reader that has to agree with every one of them.

What it ends is a front door that printed like the pile of scripts it drives — bare `$ argv` lines, raw JSON summaries, completion-ordered timings from three producers, some children streamed and others captured with no dump path when they failed, and a numbered plan that only `--dry-run` ever showed. Whoever is watching, a human at a terminal or an agent tailing a `nohup` log, wants the same four things: which step is running, how far along it is, what that step is for, and never a lost line of child output.

**One format, terminal and pipe alike.** No color, no ANSI, no spinner, no carriage return — everything written here is append-only, so a redirect and a terminal see the same bytes and a `tail -f` never rewrites a line it has already shown. That is also why there is no progress bar: a bar is a lie in a log file, and the honest form of it is a counter line that arrives at most once a minute. Counts carry thousand separators (`fmt_count`), durations are read at a glance rather than in seconds (`fmt_duration`), and a peak is a decimal gigabyte, which is `peak_rss`'s unit and this repo's only one for it.

**The protocol is four prefixes and nothing else.** `[phase] <name>` opens a stretch of work and `[t] <label> <secs>s[<tab>tail]` closes one; `[progress] <k>/<n> <unit>` is a counter, with `?` for a total nobody knows yet; `[warn] <text>` is the one thing a child can say that always reaches the terminal unthrottled. The verdict chain's own banner and the two result lines that are not phases — a witnessed fixpoint and a failure — sit here beside those prefixes as well, because the chain writes them and the cycle splits its child's output on them, and a constant restated on both sides of a seam is a constant that can disagree with itself. The `[t]` line is not new — it is the contract `cycle_timings` has always parsed out of a child's captured output — and `timing()` exists so a new site prints exactly what that reader reads rather than a near-miss. For the same reason there is one compiled pattern for that line rather than two — `INNER_LINE`, here, which `cycle_timings` parses the journal with — because two regexes over one line format would be one regex too many, and the timing line's readers must agree about what a label is down to the character.

**Pairing is what makes a phase worth printing.** A `[phase]` alone says work started, which is worth a line; a `[t]` alone says work finished, which is what the timings journal already records and what nobody watching needs a second copy of. So a `Timing` whose label matches an open phase closes it, and the surfaced line carries the duration the child measured and whatever tail it hung off the line; a `Timing` with no open phase of that label — the crate's per-configuration enumerate lines, the oracle's per-configuration lines, the chain's own step timings — is log-only. That rule is what keeps a step that prints forty timings from printing forty lines to the terminal, without any producer having to know which of its timings the digest happens to care about.

**Every surfaced line carries its step and both clocks.** Up to three steps are open at once in the default plan and six under an overlapping rebuild pool, so a line without a step column is a line whose owner the reader has to guess. Both times are inline rather than one being implied, because "this step has been running four minutes" and "the cycle is fifty minutes in" answer different questions and a reader who has to subtract them is a reader who will not.

**Third-party children get adapters, not conversions.** pytest, node and git will never print this protocol, so the digest reads their own shapes back into the same events: a percent marker becomes a `Progress`, a `FAILED`/`ERROR` summary line and a TAP `not ok` become a `Warn`, and every step at all gets `warning_events`, which catches the two shapes Python itself emits. The adapters strip ANSI first, because pytest colors its summary whenever `FORCE_COLOR` is set — as it is under the agent harness — and a colored line starts with an escape sequence rather than with `FAILED`.

**Never a lost line.** Everything a child prints lands in that step's log, tagged when it came from stderr, in arrival order; the terminal gets the digest. A step that fails replays its whole log verbatim under its own banner, which is the path the old captured-and-discarded gates never had. The digest holds one lock and puts every line through it in a single write, so overlapping children interleave between lines and never splice inside one.

**This module imports nothing else in this tree, and that is a constraint rather than an accident.** `rebuild.tools.verdict_chain` opens each of its steps with `phase()`, which puts this module inside the verdict plumbing's code closure — and everything it imports lands in that closure with it. The timings journal and the two width yardsticks are exactly what that closure was named to shed, because a fan-out width or a telemetry field can never move a verdict and inside the closure would re-run the whole chain anyway; `rebuild/tools/review_server.py` is its own module for that same reason. So the `[t]` pattern lives here and `cycle_timings` reads it from this module, `fmt_rss` spells the gigabyte divisor again rather than importing `peak_rss`, and `rebuild/test_console.py` holds that spelling equal to the yardstick's while `rebuild/test_plumbing_closure.py` fails the moment an import creeps back in.

**`log_dir=None` is a real mode rather than a degenerate one.** It performs no `mkdir`, no `open` and no symlink at all, which is what lets the whole driver suite construct a digest without touching the repo — the rebuild suite's contracts lane audits every read and write against the live trees, and a renderer that insisted on a directory would put that suite in the other lane. In that mode a step's lines are kept in memory so a failure dump still replays them; with a log directory they are read back off disk instead, so a full cycle's output never has to fit in the driver's heap.

**Streams are resolved late, never bound.** Every writer here takes `file=None` and every digest takes `out=None`, meaning "whatever `sys.stdout` is when the line is written". Binding the stream at definition time would be a quiet bug rather than a style choice: `Digest.start` tees `sys.stdout` and `sys.stderr` into `terminal.log` precisely so the bare `print` calls this module never converted still land in the copy, and a digest holding the pre-tee stream would write past its own tee.
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
from dataclasses import dataclass, field
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
    """A closed stretch of work: what `[t] <label> <secs>s[<tab>tail]` says. `tail` is whatever the producer hung off the line — a peak-RSS token, a parenthesized count — kept verbatim because the digest reprints it and never reads it."""

    label: str
    seconds: float
    tail: str = ""


@dataclass(frozen=True)
class Phase:
    """An opened stretch of work, waiting for the `Timing` of the same label to close it."""

    name: str


@dataclass(frozen=True)
class Progress:
    """A counter, in either of the two shapes a producer can offer one: `done`/`total` over a named unit for a child that knows its own denominator, or a bare `percent` for pytest, which only ever states one. `total` of None renders `?`, which is the honest answer while a producer is still discovering how much work there is."""

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
    """Something the watcher is meant to see now rather than in the log. Unthrottled by design: a warning that a throttle could swallow is a warning nobody can act on, and the producers are miserly enough with them that unboundedness costs nothing."""

    text: str


Event = Timing | Phase | Progress | Warn


class StepResult(Protocol):
    """What the digest needs off the driver's step result, structurally rather than by import — the driver imports this module, so this module cannot import the driver."""

    elapsed: float
    peak_rss_bytes: int | None


@dataclass(frozen=True)
class PlanRow:
    """One row of the plan block: its number, whether it will run, its name, the reason it will not (or the condition under which it still might), and the argv it would spawn. A skipped row keeps its note verbatim, because the note is the whole of what a skipped step has to say."""

    number: int
    status: str
    name: str
    note: str = ""
    argv: str = ""


@dataclass(frozen=True)
class SummaryRow:
    """One row of the closing table. `figure` is the step's own headline number in whatever unit the step counts in, already formatted by the driver; `seconds` is None for a step that never ran."""

    number: int | None
    name: str
    outcome: str
    figure: str = ""
    seconds: float | None = None


def fmt_count(value: int) -> str:
    return f"{value:,}"


def fmt_duration(seconds: float) -> str:
    """A duration a reader takes in at a glance: tenths under a minute, `33m08s` under an hour, `1h02m` past one, and never a bare seconds count large enough to need dividing. The threshold is 59.95 rather than 60 so the two branches cannot both round to a minute — at 59.96 the tenths form would print `60.0s`, which is a minute spelled as though it were not."""
    value = max(0.0, float(seconds))
    if value < 59.95:
        return f"{value:.1f}s"
    total = int(round(value))
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def fmt_rss(byte_count: int | None) -> str:
    """A peak as one decimal gigabyte, or the empty string for a child whose peak nobody managed to reap — the caller drops the token rather than printing a figure it does not have. The unit is `peak_rss`'s decimal gigabyte and no other; only the precision differs, one place here against that module's two, because this is a line someone reads while a build runs rather than a figure anything is calibrated against. The divisor is spelled again rather than imported because this module may reach nothing else in the tree (see the module docstring), so a test pins the two spellings equal instead."""
    if byte_count is None:
        return ""
    return f"{byte_count / _BYTES_PER_GB:.1f}G"


def _write(line: str, file: IO[str] | None) -> None:
    stream = sys.stdout if file is None else file
    stream.write(line + "\n")
    stream.flush()


def phase(name: str, *, file: IO[str] | None = None) -> None:
    _write(PHASE + name, file)


def progress(done: int, total: int | None, unit: str, *, file: IO[str] | None = None) -> None:
    _write(f"{PROGRESS}{done}/{'?' if total is None else total} {unit}".rstrip(), file)


def warn(text: str, *, file: IO[str] | None = None) -> None:
    _write(WARN + text, file)


def timing(label: str, seconds: float, tail: str | None = None, *, file: IO[str] | None = None) -> None:
    """The `[t]` line, in the one spelling `cycle_timings` parses. The existing print sites keep their own f-strings; this is for new ones, so that the next producer to grow a timing line cannot invent a near-miss that the journal silently drops."""
    suffix = "" if not tail else f" {tail}"
    _write(f"{TIMING}{label} {seconds:.1f}s{suffix}", file)


def parse_line(line: str) -> Event | None:
    """One line of a child's output as an event, or None for the overwhelming majority that are not one. Deliberately as narrow as `INNER_LINE` is: anchored at the start of the line, so a protocol prefix indented or embedded mid-line is prose that happens to quote the protocol rather than an event, and a `[t]` line whose seconds are missing or malformed is nothing here exactly as it is nothing to the timings journal."""
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
    """pytest's own shapes as events: a summary `FAILED `/`ERROR ` line is the failure a watcher must see, and the percent marker every progress line carries is the only denominator pytest ever states. ANSI comes off first — pytest colors its summary whenever `FORCE_COLOR` is set, as it is under the agent harness, and a colored line starts with an escape sequence rather than with `FAILED`."""
    text = _ANSI_SGR.sub("", line).rstrip()
    if text.startswith(("FAILED ", "ERROR ")):
        return Warn(text)
    match = _PYTEST_PERCENT.search(text)
    if match is not None:
        return Progress(percent=int(match.group(1)))
    return None


def pytest_warning_count(line: str) -> int | None:
    """The warnings count out of pytest's terminal summary rule, for the closing line's `N warnings, see log`. Only a line that is that rule is read, because `warnings summary` headers and individual warning bodies carry counts of their own that mean something else entirely."""
    text = _ANSI_SGR.sub("", line).strip()
    if not text.startswith("="):
        return None
    match = _PYTEST_WARNINGS.search(text)
    return int(match.group(1)) if match else None


def node_test_events(line: str) -> Event | None:
    """The node test runner's TAP output as events: a `not ok` assertion anywhere in the tree, and the closing `# fail N` when N is not zero. `# fail 0` is a passing suite announcing that it passed, which is the closing line's job to say rather than a warning's."""
    text = line.strip()
    if text.startswith("not ok "):
        return Warn(text)
    match = _NODE_FAIL.match(text)
    if match is not None and int(match.group(1)) > 0:
        return Warn(text)
    return None


def warning_events(line: str) -> Warn | None:
    """The two warning shapes anything in this tree can print, which is why every step gets this adapter on top of its own: a line beginning `warning:` in either casing (leading whitespace stripped, since the standing-fill tripwire indents its own), and Python's `warnings.warn` shape, which run_m1's spec load lets through to stderr."""
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
    """Which third-party child a step spawns, and so which adapter reads that step's lines back into events. It is module data keyed by the plan's step name rather than a `step_start` keyword, because the driver's spawn seam has a closed signature thirty test fakes already match, and a step's output shape is a fact about the child rather than about the call that started it."""
    return STEP_ADAPTERS.get(name)


def skip_reason(note: str) -> str:
    """A skip's reason with the `SKIPPED (…)` wrapper the plan's notes carry taken off. The plan block puts that word in its own column and a surfaced skip line opens with it too, so a note reprinted whole says it twice — `skipped  SKIPPED (the surface is not rewritten …)` — while the notes themselves keep the wrapper, because in the plan they are the whole explanation and a reader greps them by it. A note that never wore one is answered unchanged."""
    match = _SKIPPED_WRAPPER.match(note)
    return f"{match.group(1)}{note[match.end() :]}" if match else note


def counts_line(rows: Sequence[PlanRow]) -> str:
    """The plan block's one-line arithmetic: how many steps there are, how many will run, how many are already skipped. It answers with a range exactly when some step is still undecided — gate:conform can be re-skipped after run_m1 proves its inputs unmoved — because a single number there would be a promise the plan cannot keep, and a reader who later counts eight steps against a stated seven has been told something false."""
    will_run = sum(1 for row in rows if row.status == STATUS_RUN)
    maybe = sum(1 for row in rows if row.status == STATUS_MAYBE)
    skipped = sum(1 for row in rows if row.status == STATUS_SKIP)
    span = f"{will_run}" if not maybe else f"{will_run}–{will_run + maybe}"
    return f"{len(rows)} steps: {span} will run, {skipped} skipped"


def plan_lines(rows: Sequence[PlanRow]) -> list[str]:
    """One line per step — number, run/skip column, name, note — with each row that will spawn something followed by its `$ argv`. The note is reprinted verbatim rather than reworded, because for a skipped step it is the entire explanation and for an undecided one it is the condition."""
    if not rows:
        return []
    number_width = max(len(str(row.number)) for row in rows)
    status_width = max(len(row.status) for row in rows)
    name_width = max(len(row.name) for row in rows)
    lines: list[str] = []
    for row in rows:
        head = f"  {row.number:>{number_width}}  {row.status:<{status_width}}  {row.name:<{name_width}}"
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
    """One stream written to two places. Only `write` and `flush` are duplicated; everything else — `isatty`, `fileno`, `encoding` — is the wrapped stream's own answer, because a tee that lies about being a terminal changes how the code under it behaves."""

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
    """The cycle's renderer and its sink: every line the terminal shows is written here, and every line a child prints passes through here on its way to that step's log.

    Constructed with the plan's step names (which is where the numbering and the width of the step column come from), the run's log directory (or None for no filesystem at all), the alias map that lets a spawn under one name report under the plan's row for it, and the three injection points a test needs — the output stream, the clock, and the silence window. Used as a context manager around everything after the dry-run return, so the tee and the heartbeat thread are installed and removed in one place.

    `emit` and `emit_block` are the lock-serialized writers this replaces an earlier `_Emitter` with, kept under their old names so a call site that has not been converted still compiles and still cannot splice a line. Every argument being optional is part of that: a bare construction is a renderer with no plan behind it and no directory under it, which is what a caller wanting nothing but a serialized stdout still asks for and what the driver's suite hands its stage functions.
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
        """Lines the terminal has already shown, into `terminal.log` alone. The driver answers three questions before there is a digest to catch them — whether a red cycle's snapshot is being kept, whether anything carryable was found, and which master the carry resolved to — and the copy of the terminal is meant to be a copy, so they are written into the file rather than said to the reader twice."""
        with self._lock:
            if self._terminal is None:
                return
            for line in lines:
                self._terminal.write(line + "\n")
            self._terminal.flush()

    def plan_block(self, lines: Sequence[str]) -> None:
        """The plan, to the terminal and to `plan.txt` — the same lines to both, so the file a reader opens afterwards is the block they watched scroll past rather than a second rendering of it."""
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
        """Open a step: its rule line, its description wrapped at 76 columns, a blank line, and the argv it is about to spawn. `verbatim` says this step's unparsed lines belong on the terminal too, which is true of the diffs a step prints for a human to act on — the census pins', whose whole point is that it is read and committed, and the constants', on the pass where the job-costs check trips."""
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
        """Close a step with its outcome, its headline figure and its peak. The elapsed time is the driver's own measurement when it has one, because the child's wall clock is what the timings journal records and the two must not disagree by a scheduling delay. A pytest lane that warned closes by saying how many times and where to read them: the warnings summary itself is pages long and belongs in the log, but a lane that closes as a bare `ok` is a lane whose warnings nobody ever goes looking for."""
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
        """A step the plan already ruled out, announced where it would have run. The reason is unwrapped first, so the line reads `skipped  <reason>` whatever spelling the note it came from wore."""
        with self._lock:
            self._one_line(name, "  ".join(part for part in ("skipped", skip_reason(note)) if part))

    def step_not_run(self, name: str, reason: str = "") -> None:
        with self._lock:
            self._one_line(name, "  ".join(part for part in ("not run", reason) if part))

    def note(self, name: str, text: str) -> None:
        """A line the driver itself has to say about a step — an error it caught, a green it declined to record — carrying the step column and both clocks like every other surfaced line, because a bare sentence in the middle of six interleaved steps belongs to nobody."""
        with self._lock:
            self._one_line(name, text)

    def substep(self, parent: str, name: str) -> None:
        """File a spawn under another step's banner. The census's `git diff` and the job-costs diff are children of steps rather than steps of the plan, and this is what lets them log into their parent's file and surface under its column without the spawn seam growing a keyword that thirty test fakes would have to grow with it."""
        with self._lock:
            self._substeps[name] = parent

    def substep_end(self, name: str) -> None:
        """Close whatever a sub-step's own lines opened. A sub-step that spawns once its parent has closed — the `git diff` the census prints after its refresh — would otherwise leave a state nobody ever closes: its log handle open until `stop()`, and the heartbeat announcing a step that finished minutes ago. A parent still genuinely open is left exactly where it is, since the sub-step never opened it and closing it here would take its banner's own closing line away."""
        with self._lock:
            state = self._open.get(self._key(name))
            if state is not None and state.transient:
                self._close(state)

    def child_line(self, name: str, stream: str, line: str) -> Event | None:
        """One line off a child's pipe: logged always, surfaced when it is worth surfacing. Returns the event it read, for a caller that wants it.

        The order is the protocol first, then the step's own adapter, then the generic warning shapes — so a child that speaks the protocol is never second-guessed by a heuristic, and a child that does not still cannot hide a warning. A `Phase` opens; the `Timing` that matches an open phase closes it and carries its duration and tail; a `Timing` matching nothing is log-only. A `Warn` always surfaces. A `Progress` surfaces only when the step has been silent for the whole heartbeat window, and is otherwise stored as this step's latest, which is what the heartbeat prints when the silence runs out.
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
        """Replay a failed step's whole log under its own banner, verbatim and stderr tags included, which is the path the captured-and-discarded gates never had. Call it before `step_end`, which is where a step's record is closed and forgotten."""
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
        """The closing block: the step table, then the cycle-level lines the driver composed, then the verdict. Reasons are reprinted verbatim under a `CYCLE FAILED:` / `CYCLE INTERRUPTED:` heading, and a green run ends on `Cycle complete.` with no reasons block at all."""
        lines = ["", self._rule(SUMMARY_BANNER), *_summary_table(rows), ""]
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
        """Every open step that has been silent for the whole window says something: its latest unsurfaced counter if one arrived, and otherwise that it is still there. A step whose child prints nothing at all for ten minutes — the compile, the read-back — is the case this exists for, and a bare line proving the cycle is alive is worth more than the silence it replaces."""
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
        total = len(self.steps) if self.steps else "?"
        number = "?" if state.number is None else state.number
        now = self._clock()
        return (
            f"step {number} of {total}  {state.display}  "
            f"step {fmt_duration(now - state.started)}  cycle {fmt_duration(now - self._t0)}"
        )

    def _key(self, name: str) -> str:
        return self._substeps.get(name, name)

    def _display(self, name: str) -> str:
        return self.aliases.get(name, name)

    def _number(self, display: str) -> int | None:
        return self.steps.index(display) + 1 if display in self.steps else None

    def _state_for(self, name: str, *, banner: bool) -> _StepState:
        """The state a line belongs to, opened on demand. A `child_line` for a step nobody started still gets a state rather than being dropped: the whole promise here is that no line of child output is lost, and a driver bug is a thing to see in the log, not a reason to swallow the log. Such a state is transient — nothing banner-worthy is happening under it and nobody is going to close it, which is what `substep_end` reaps — and it is dated from the step of that name that already ran, so a line arriving after a step closed reads as late rather than as a step that has just begun."""
        key = self._key(name)
        state = self._open.get(key)
        if state is not None:
            if not banner:
                return state
            self._close_log(state)
        now = self._clock()
        display = self._display(key)
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
        """Open a step's log file, which happens on the first line that step's child prints rather than when its banner goes up. A step that spawns nothing — the snapshot and the retention pass both run in this process — would otherwise leave an empty file in every run directory, and a run directory reads best when a file in it means a child ran."""
        if self.log_dir is None:
            return
        number = 0 if state.number is None else state.number
        state.log_path = self.log_dir / f"{number:02d}-{state.display.replace(':', '-')}.log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        state.handle = open(state.log_path, "a", encoding="utf-8", buffering=1)

    def _close(self, state: _StepState) -> None:
        """Forget an open step, remembering only when it started — which is what a line arriving after the close is dated from."""
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
        """Point `latest` at this run, replacing whatever it pointed at, and never fail a cycle over it — a filesystem that cannot hold a symlink costs the reader a convenience, not the run."""
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
            "?" if row.number is None else str(row.number),
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
