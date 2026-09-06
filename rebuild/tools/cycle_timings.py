"""Append-only telemetry for the checks this repo runs — what each one judged and what it cost — and the reporter that reads it back.

The journal is rebuild/out/cycle-timings.ndjson (gitignored with the rest of rebuild/out, never touched by the retention pass, so each machine accumulates its own history), and its organizing unit is the check invocation. Every judged check appends one "check" line naming the check (run_m1, conform, rebuild-contracts, make-test, js), the verdict the judge reached — green, red, or skipped — the human status string that same judge printed, the prose failures a cycle rolls up into its summary, and the ids of the tests that actually failed. A cycle run is an optional parent that some of those lines name: the artifact cycle's parent process tags every check it judges with its own run id, while the interactive entry points — rebuild.tools.rebuild_gate, rebuild.tools.make_test_gate, and run_m1's CLI — record theirs with no run at all, which is what finally puts the ordinary interactive check run on the record instead of leaving it invisible. Exactly one process records any one invocation: the two entry points a cycle spawns suppress their own line when AMS_CYCLE_RUN (CYCLE_RUN_ENV) is in the environment they inherited, and the cycle writes it in their place.

A check line denormalizes its own context — host, cpu count, box size — rather than pointing at a run line for it, because the parentless line is the common case and a record nobody can interpret without a parent it does not have is not a record. rc is not a verdict, and this journal is where that stays honest: a check line records what the judge decided and never what the process returned, so a run that died before its judge and a run the judge failed are told apart. The rule runs backwards too — a "step" line carries a return code and no verdict, and no reader here manufactures one from it, so the history from before check lines existed stays what it is, unjudged, rather than being back-filled with a guess that would read exactly like a measurement.

What a cycle spent is still recorded as it always was: one "step" line per subprocess the driver actually spawned, and one "run" line when the cycle finishes, interrupted finishes included. A step line carries the driver's step name (run_m1, gate:conform, merge, ...), the argv, the return code, the wall seconds, the step's peak RSS in bytes (measured by the driver as it reaps the child, so it covers the child's whole process tree — see peak_rss.reap_peak_rss_bytes), and — parsed out of the child's captured stdout/stderr — any inner "[t] <label> <secs>s" phase lines the child printed, which is how the per-config conform sweeps and run_m1's phase breakdown survive even for gates whose output is never streamed to the console. An inner line may carry its own peak-RSS figure as a trailing "rss_gb=<n>" token (peak_rss.rss_token is the writer; decimal GB, like every figure here), which rides into the journal beside the label's seconds. A run line carries the run's identity — hostname, cpu count, and the size of the box it ran on — start/finish stamps, total wall seconds, and the cycle summary's exit/gates/plan blocks, so a slow step can be read in context: which machine, and which skips were in effect.

The box is worth its own field because a per-step peak read months later means nothing without the machine's size beside it: whether a step that held 9 GB was comfortable or was most of the box is a fact about the box, not about the step, and the journal is the only place the two are ever written down together. `make job-costs` divides that same figure by a checked-in per-unit peak to state the width that constant implies here, which is the second reason it is recorded rather than probed at read time — a figure probed on the reader's box would answer for the wrong machine.

Skipped stages never spawn and so never produce a step line; whether a stage was skipped or genuinely absent is read from the run line's plan and gates blocks, not from the step list. A check the cycle never even reached produces no check line either — a skipped check is one that was judged skipped, not one that was never asked.

A fourth kind of line, "pool", is written by something that is not the cycle at all: any pytest run whose pool has a unit name to declare — AMS_POOL_UNIT (POOL_UNIT_ENV), set on the child's own environment by the two gate wrappers and by the cycle's rebuild-lane spawns — has its xdist controller append one line at terminal summary naming the unit, the width the pool resolved to, the controller's own peak, and every worker's. load_pool_records is the reader, and `make job-costs` is what finally holds those measurements against the checked-in per-worker constants they are supposed to price.

The reporter is `make cycle-timings` (`uv run python -m rebuild.tools.cycle_timings`): recent runs with steps slowest-first by default, --inner to expand the phase lines, --by-step to aggregate count/median/max/latest per step and host — the host column is what makes a laptop and a desktop directly comparable — and --by-outcome to ask of each check how often it ran, how it came out, and which test ids it failed on. --by-step gives an unparented check line a row of its own, named check:<name>, rather than merging it into the cycle's row for the same work, because a check sharing the box with a whole cycle pass and that same work alone on the box are two different measurements and an average of them describes neither — and because one check name, run_m1, is spelled exactly like the step the cycle spawns to do the build it judges. Journals from two machines can be concatenated and read with --journal.

The file, the module, and the `make cycle-timings` target all keep the "cycle-timings" spelling although the cycle is no longer the only writer nor the unit anything is filed under. The journal is gitignored and per-machine, so nothing but the file itself holds a box's history: a rename would buy a better name at the price of orphaning every machine's accumulated record, and this paragraph is cheaper.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rebuild.tools import memory_budget
from rebuild.tools.console import INNER_LINE
from rebuild.tools.peak_rss import format_gb

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "rebuild" / "out" / "cycle-timings.ndjson"
FORMAT = "ams-cycle-timings/2"
# The one spelling of the variable a pytest controller reads to learn what its pool is called. Both gate wrappers and the root conftest reach it from here rather than typing the string, so the journal's `unit` field, the units `make job-costs` calibrates, and whoever sets the variable can never disagree about a name.
POOL_UNIT_ENV = "AMS_POOL_UNIT"
# The one spelling of the variable a spawned check reads to learn that a cycle is already recording on its behalf. The cycle sets it when it arms its CycleTimings and every child inherits it; the gate wrappers that a cycle can spawn read it from here rather than typing the string, so the writer that sets the variable and the writers that stand down for it can never disagree about a name.
CYCLE_RUN_ENV = "AMS_CYCLE_RUN"

_RSS_TOKEN = re.compile(r"\brss_gb=(\d+(?:\.\d+)?)")

_JOURNAL_LOCK = threading.Lock()
_pool_warn_state: list[bool] = [False]
_check_warn_state: list[bool] = [False]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_entry(path: Path, entry: dict) -> str | None:
    """Append one JSON line under the module-wide lock, answering None on success and the failure's repr when the journal could not be written. The lock is module-wide rather than per-instance because two writers now share this file inside one process — the cycle's step and run lines from its gate-pool threads, and a pool line from whichever pytest controller this process happens to be — and a per-instance lock would serialize each of them against itself while leaving them free to interleave with each other. Failure comes back as a string rather than as a raise because a journal that cannot be written is never an error a caller has to handle, only one worth saying once, and each caller owns its own warn-once flag."""
    line = json.dumps(entry)
    try:
        with _JOURNAL_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        return repr(exc)
    return None


def gateway_order(item: tuple[str, int]) -> tuple[int, str]:
    """Sort key for a (gateway id, peak) pair: gw2 before gw10, and anything without a number first. Public because the root conftest's terminal line sorts its workers with it too — the printed line and the pool record are supposed to list the same workers in the same order, and one sort key shared between them makes that true by construction rather than by two copies of four lines staying in step."""
    digits = "".join(ch for ch in item[0] if ch.isdigit())
    return (int(digits) if digits else -1, item[0])


def record_pool(
    unit: str,
    *,
    width: int,
    worker_peaks: dict[str, int],
    controller_peak_bytes: int,
    path: Path | None = None,
) -> None:
    """Append one kind:"pool" line for a finished xdist pool: which unit it was a pool of, how wide it ran, and what the controller and each worker held at their peaks. It is a module function rather than a CycleTimings method because there is no run and no instance behind it — the writer is a pytest controller, which knows nothing about a cycle and may not be inside one at all.

    The record carries no run id, deliberately, and CYCLE_RUN_ENV does not change that: a run is the wrong grain for a pool, not merely an id a controller had no way to learn. What a pool belongs to is one suite invocation, and a cycle pass spawns several of them, so a run id would file several different measurements under one name while a standalone `make test` still had nothing to be filed under. So the pool line is filed under nothing, load_journal never indexes it, and load_pool_records is how a reader finds it.

    `width` is the width the pool was asked for — the resolved `numprocesses`, not the length of `worker_peaks` — because the two are different facts and both are worth having: a node that dies without handing back its workeroutput leaves the peaks dict short by one, so the dict's own length says how many workers answered while `width` says how many ran. Averaging them into a single number would lose the discrepancy that is the interesting part.

    Peaks are ordered by gateway number, matching the order the controller's own terminal line prints them in, so the record and the line a human just read agree — the same `gateway_order` key does both, so the two can never drift apart. `path` is resolved when the call is made rather than bound as a default, so a test that redirects the module's JOURNAL is redirected here too instead of quietly appending to the live one. Nothing here raises: this is called from a terminal-summary hook, where a raise would disfigure the report of a suite that passed, so an unwritable journal warns once per process and is thereafter silent.
    """
    journal = JOURNAL if path is None else path
    entry = {
        "format": FORMAT,
        "kind": "pool",
        "host": socket.gethostname(),
        "unit": unit,
        "finished_at": _utc_stamp(),
        "width": int(width),
        "controller_peak_rss_bytes": int(controller_peak_bytes),
        "worker_peak_rss_bytes": {
            ident: int(peak) for ident, peak in sorted(worker_peaks.items(), key=gateway_order)
        },
    }
    failure = _append_entry(journal, entry)
    if failure is not None and not _pool_warn_state[0]:
        _pool_warn_state[0] = True
        print(f"warning: failed to append to {journal}: {failure}", file=sys.stderr)


@dataclass
class CheckVerdict:
    """What one judged check invocation decided, in the single shape every judge answers in and every writer records from. `verdict` is the trichotomy a reader can group on — green, red, skipped — and `status` is the human label the console and the cycle summary have always printed for that same judgment ("green", a FAILED clause carrying the unexplained count, "FAILED (no conform_summary.json)"). They are separate fields because the status strings are output nobody may reword and each judge words its own, while a report that counts how a check tends to come out has to compare judges to each other; deriving either from the other would mean either rewriting console output to be parseable or parsing prose to recover a verdict.

    `failures` is the prose the cycle rolls up into its summary and `failed_ids` is the test ids themselves, which is the field this record exists for: a failure sentence answers "did this pass", a list of ids answers "which test earns its keep" across every invocation on the record. `recordable` is green-record machinery the caller consults and is deliberately not journaled — whether this pass may write a green record is a fact about this pass and its inputs, not history a later reader could use.
    """

    check: str
    verdict: str
    status: str
    failures: list[str]
    failed_ids: list[str]
    recordable: bool = False

    @property
    def ok(self) -> bool:
        return self.verdict == "green"


def record_check(
    verdict: CheckVerdict,
    *,
    run: str | None = None,
    argv: list[str] | None = None,
    elapsed_s: float | None = None,
    peak_rss_bytes: int | None = None,
    path: Path | None = None,
) -> None:
    """Append one kind:"check" line for a judged check invocation. `run` is the optional parent: the artifact cycle passes its run id (through CycleTimings.record_check) and an interactive entry point passes nothing, which is the whole reason this is a module function as well as a method — the majority of this repo's check runs happen with no cycle in sight and had no way to be recorded before.

    host, cpu_count, and mem_total_bytes are read here rather than left to a parent run line to supply, because the parentless line is the common case and a record that can only be read next to a run record it does not have is not a record. That denormalization is also what lets a parented and an unparented line be compared directly, which --by-outcome does.

    `recordable` never reaches the journal: it tells the caller whether a green record may be written for this pass and is meaningless to a reader months later. Rounding elapsed_s to a tenth matches the step lines, so the two can be read in one column. Nothing here raises — the callers are gate wrappers and a cycle's reporting path, where a journal that cannot be written is worth saying once and is never worth failing a check that already reached a verdict. `path` resolves at call time rather than binding JOURNAL as a default, so a test that redirects the module constant is redirected here too instead of quietly appending to the live journal.
    """
    journal = JOURNAL if path is None else path
    entry: dict = {
        "format": FORMAT,
        "kind": "check",
        "check": verdict.check,
        "verdict": verdict.verdict,
        "status": verdict.status,
        "failures": list(verdict.failures),
        "failed_ids": list(verdict.failed_ids),
        "host": socket.gethostname(),
        "cpu_count": os.cpu_count(),
        "mem_total_bytes": memory_budget.total_memory_bytes(),
        "finished_at": _utc_stamp(),
    }
    if run is not None:
        entry["run"] = run
    if argv is not None:
        entry["argv"] = list(argv)
    if elapsed_s is not None:
        entry["elapsed_s"] = round(float(elapsed_s), 1)
    if peak_rss_bytes is not None:
        entry["peak_rss_bytes"] = int(peak_rss_bytes)
    failure = _append_entry(journal, entry)
    if failure is not None and not _check_warn_state[0]:
        _check_warn_state[0] = True
        print(f"warning: failed to append to {journal}: {failure}", file=sys.stderr)


def parse_inner_timings(text: str) -> list[dict]:
    entries: list[dict] = []
    for match in INNER_LINE.finditer(text):
        entry: dict = {"label": match.group(1), "elapsed_s": float(match.group(2))}
        rss = _RSS_TOKEN.search(match.group(3) or "")
        if rss:
            entry["rss_gb"] = float(rss.group(1))
        entries.append(entry)
    return entries


class CycleTimings:
    """One instance per cycle run. wrap_spawn decorates the driver's spawn callable so every real subprocess records a step line as it completes; record_check files a judgment under this run; finish records the run line from the already-built cycle summary payload. Appends are lock-serialized (the gate tasks spawn from pool threads) and a journal that cannot be written warns once and never fails the cycle. What a caller adds to a spawn beyond the four arguments timing cares about — a per-child environment, say — passes straight through, because this decorator's business is the clock and not the child's terms."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.run_id = uuid.uuid4().hex[:12]
        self.host = socket.gethostname()
        self.started_at = _utc_stamp()
        self._t0 = time.perf_counter()
        self._warned = False

    def wrap_spawn(self, spawn):
        def timed(name, argv, *, emit, registry, stream, **passthrough):
            result = spawn(name, argv, emit=emit, registry=registry, stream=stream, **passthrough)
            if not (result.returncode == 130 and result.elapsed == 0.0):
                self.record_step(result, argv)
            return result

        return timed

    def record_step(self, result, argv: list[str]) -> None:
        entry = {
            "format": FORMAT,
            "kind": "step",
            "run": self.run_id,
            "host": self.host,
            "name": result.name,
            "argv": list(argv),
            "rc": result.returncode,
            "elapsed_s": round(result.elapsed, 1),
            "finished_at": _utc_stamp(),
        }
        peak = getattr(result, "peak_rss_bytes", None)
        if peak is not None:
            entry["peak_rss_bytes"] = int(peak)
        inner = parse_inner_timings(result.stdout + "\n" + result.stderr)
        if inner:
            entry["inner"] = inner
        self._append(entry)

    def record_check(self, verdict: CheckVerdict, **kw) -> None:
        """The cycle's own check lines, tagged with this run so a reader can put a judgment beside the step that produced it. The cycle records every check it judges — including the ones whose work a child process did — because a child that a cycle spawned stands down on CYCLE_RUN_ENV, and one writer per invocation is what keeps the counts in --by-outcome counts of invocations rather than of processes that happened to have an opinion."""
        record_check(verdict, run=self.run_id, path=self.path, **kw)

    def finish(self, summary: dict) -> None:
        self._append(
            {
                "format": FORMAT,
                "kind": "run",
                "run": self.run_id,
                "host": self.host,
                "cpu_count": os.cpu_count(),
                "mem_total_bytes": memory_budget.total_memory_bytes(),
                "started_at": self.started_at,
                "finished_at": _utc_stamp(),
                "wall_s": round(time.perf_counter() - self._t0, 1),
                "exit": summary.get("exit"),
                "interrupted": summary.get("interrupted"),
                "failures": summary.get("failures"),
                "gates": summary.get("gates"),
                "plan": summary.get("plan"),
                "argv": summary.get("argv"),
            }
        )

    def _append(self, entry: dict) -> None:
        failure = _append_entry(self.path, entry)
        if failure is not None and not self._warned:
            self._warned = True
            print(f"warning: failed to append to {self.path}: {failure}", file=sys.stderr)


def load_journal(path: Path) -> tuple[dict[str, dict], dict[str, list[dict]], list[str]]:
    """Returns (run lines by run id, step lines by run id, run ids in first-seen order) — the cycle's own two kinds and nothing else. A check line is skipped whether or not it names a run: it is a judgment rather than a subprocess, its seconds may be the very seconds a step line already reports, and letting a parented one file itself here would make it a step while letting it create an order entry would conjure a run out of a line that only points at one. load_checks is its reader. Malformed lines are skipped: the journal is written by concurrent threads across many runs, and one torn line must not make the whole history unreadable."""
    runs: dict[str, dict] = {}
    steps: dict[str, list[dict]] = {}
    order: list[str] = []
    if not path.exists():
        return runs, steps, order
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("kind") not in ("run", "step"):
            continue
        run_id = entry.get("run")
        if not isinstance(run_id, str):
            continue
        if run_id not in steps:
            steps[run_id] = []
            order.append(run_id)
        if entry.get("kind") == "run":
            runs[run_id] = entry
        else:
            steps[run_id].append(entry)
    return runs, steps, order


def load_checks(path: Path) -> list[dict]:
    """Every kind:"check" line in the journal, in file order, parented and unparented alike. A separate loader from load_journal for the same reason load_pool_records is one: a check is not filed under a run, since most of them have none and the ones that do are pointing at a parent rather than belonging to it. Malformed lines are skipped and a journal that does not exist reads as no checks, because a box that has never run one has nothing to say and that is not a failure."""
    records: list[dict] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kind") == "check":
            records.append(entry)
    return records


def load_pool_records(path: Path) -> list[dict]:
    """Every kind:"pool" line in the journal, in file order. A separate loader from load_journal because a pool record carries no run id and so cannot be filed under one: the pool a pytest controller measured belongs to a suite invocation, which a standalone `make test` has and a cycle run does not uniquely own — a single pass spawns several of them. Malformed lines are skipped for the same reason load_journal skips them, since the journal is appended to by concurrent threads across processes and one torn line must not make the whole history unreadable, and a journal that does not exist reads as no records rather than as an error, because a box that has never run a pool has nothing to say and that is not a failure."""
    records: list[dict] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kind") == "pool":
            records.append(entry)
    return records


def _seconds(value) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _rss_suffix(entry: dict, key: str = "peak_rss_bytes") -> str:
    value = entry.get(key)
    return f"  rss={format_gb(value)}GB" if isinstance(value, int | float) else ""


def render_runs(
    runs: dict[str, dict],
    steps: dict[str, list[dict]],
    order: list[str],
    limit: int,
    inner: bool,
) -> list[str]:
    lines: list[str] = []
    for run_id in order[-limit:]:
        run = runs.get(run_id)
        step_list = steps.get(run_id, [])
        if run is None:
            last_seen = step_list[-1].get("finished_at", "?") if step_list else "?"
            host = step_list[0].get("host", "?") if step_list else "?"
            lines.append(f"\n{last_seen}  host={host}  (no run record — killed before the summary landed)")
        else:
            bits = [
                str(run.get("started_at", "?")),
                f"host={run.get('host', '?')}",
                f"cpus={run.get('cpu_count', '?')}",
            ]
            # `cpus=?` is defensible where the count is missing, because that key has always been written and its absence therefore means a probe that failed. The box's size postdates most of this journal, so a record without it is simply older than the field — say nothing rather than `ram=?`, which would read as a probe that failed on a run where nothing was ever asked.
            mem = run.get("mem_total_bytes")
            if isinstance(mem, int | float):
                bits.append(f"ram={format_gb(mem)}GB")
            bits += [
                f"wall={_seconds(run.get('wall_s')):.1f}s",
                f"exit={run.get('exit', '?')}",
            ]
            lines.append("\n" + "  ".join(bits))
        for step in sorted(step_list, key=lambda entry: -_seconds(entry.get("elapsed_s"))):
            rc = step.get("rc")
            suffix = "" if rc == 0 else f"  (rc {rc})"
            lines.append(
                f"  {_seconds(step.get('elapsed_s')):>8.1f}s  {step.get('name', '?')}{_rss_suffix(step)}{suffix}"
            )
            if inner:
                for item in step.get("inner", []):
                    rss = item.get("rss_gb")
                    inner_suffix = f"  rss={rss:.2f}GB" if isinstance(rss, int | float) else ""
                    lines.append(
                        f"  {_seconds(item.get('elapsed_s')):>10.1f}s    {item.get('label', '?')}{inner_suffix}"
                    )
        if not step_list:
            lines.append("  (no steps spawned — everything skipped)")
    return lines


def render_by_step(steps: dict[str, list[dict]], order: list[str], checks: list[dict]) -> list[str]:
    """Count/median/max/latest seconds per step and host, over the cycle's step lines plus every check that timed itself outside a cycle. A parented check is excluded because its cost is already the step line of the same run — counting both would double an observation the journal holds once. A skipped check excludes itself by carrying no seconds, which is the right answer rather than a zero: nothing ran, and a zero would drag a median toward a run that never happened. What is left is the interactive invocations, and they land in rows of their own rather than merging into the cycle's rows for the same work, because a check sharing the box with a whole cycle pass and that same work alone on the box are different measurements whose average would describe neither.

    A check row is named `check:<name>` — the same namespacing idiom the step names already use for `gate:*` — so that separation is structural rather than a property of the names in play. Every check name but one differs from every step name, and the exception is the expensive one: a cycle spawns the M1 build as the step `run_m1` while `evaluate_run_m1_gate` files its judgment under the check `run_m1`, so a bare name would drop an interactive build's check — or a seconds-long `run_m1 --gates-only` re-adjudication, which files that same check name — into the row that is supposed to say what a full M1 build costs on this box. That row is the one this view exists to answer with, and poisoning it is worse than merely miscounting: `latest` would report the re-adjudication as the most recent cost of the build, so a ledger-edit loop would quietly rewrite the number a reader is about to size a timeout from.
    """
    buckets: dict[tuple[str, str], list[float]] = {}
    rss_peaks: dict[tuple[str, str], list[float]] = {}
    for run_id in order:
        for step in steps.get(run_id, []):
            key = (str(step.get("name", "?")), str(step.get("host", "?")))
            buckets.setdefault(key, []).append(_seconds(step.get("elapsed_s")))
            peak = step.get("peak_rss_bytes")
            if isinstance(peak, int | float):
                rss_peaks.setdefault(key, []).append(float(peak))
    for check in checks:
        elapsed = check.get("elapsed_s")
        if check.get("run") is not None or not isinstance(elapsed, int | float):
            continue
        key = (f"check:{check.get('check', '?')}", str(check.get("host", "?")))
        buckets.setdefault(key, []).append(float(elapsed))
        peak = check.get("peak_rss_bytes")
        if isinstance(peak, int | float):
            rss_peaks.setdefault(key, []).append(float(peak))
    rows = [
        (name, host, len(values), statistics.median(values), max(values), values[-1])
        for (name, host), values in buckets.items()
    ]
    rows.sort(key=lambda row: (-row[3], row[0], row[1]))
    name_width = max([len(row[0]) for row in rows] + [len("step")])
    host_width = max([len(row[1]) for row in rows] + [len("host")])
    lines = [
        f"\n{'step':<{name_width}}  {'host':<{host_width}}  {'runs':>4}  {'median':>8}  {'max':>8}  {'latest':>8}  {'maxrss':>8}"
    ]
    for name, host, count, median, peak, latest in rows:
        recorded = rss_peaks.get((name, host))
        maxrss = f"{format_gb(max(recorded))}GB" if recorded else ""
        lines.append(
            f"{name:<{name_width}}  {host:<{host_width}}  {count:>4}  {median:>7.1f}s  {peak:>7.1f}s  {latest:>7.1f}s  {maxrss:>8}"
        )
    return lines


def render_by_outcome(checks: list[dict]) -> list[str]:
    """Per check, across every host and whether or not a cycle was its parent: how many invocations are on the record, how they came out, and which test ids they failed on. This is the report the check line exists for — a green count answers whether a check ever catches anything and the id histogram answers which test is doing the catching, so a suite's cost can be argued against what it has actually found rather than against an impression of it. Failed ids are ordered by how often each has failed, ties by id, so the recurring one is at the top of the check it belongs to; checks are ordered by name, so a row stays where a reader last found it instead of moving whenever a verdict lands.

    Parented lines are counted here, unlike in --by-step: the double-counting that view avoids is of seconds a step line already reports, while a verdict is reported nowhere else, and a check's history would be a strange thing to split by whether a cycle happened to be driving.
    """
    counts: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    histograms: dict[str, dict[str, int]] = {}
    for entry in checks:
        name = str(entry.get("check", "?"))
        tally = counts.setdefault(name, {"green": 0, "red": 0, "skipped": 0})
        totals[name] = totals.get(name, 0) + 1
        verdict = entry.get("verdict")
        if isinstance(verdict, str) and verdict in tally:
            tally[verdict] += 1
        histogram = histograms.setdefault(name, {})
        for test_id in entry.get("failed_ids") or []:
            histogram[str(test_id)] = histogram.get(str(test_id), 0) + 1
    name_width = max([len(name) for name in counts] + [len("check")])
    lines = [f"\n{'check':<{name_width}}  {'runs':>4}  {'green':>5}  {'red':>5}  {'skipped':>7}"]
    for name in sorted(counts):
        tally = counts[name]
        lines.append(
            f"{name:<{name_width}}  {totals[name]:>4}  {tally['green']:>5}  {tally['red']:>5}  {tally['skipped']:>7}"
        )
        failures = sorted(histograms[name].items(), key=lambda item: (-item[1], item[0]))
        for test_id, count in failures:
            lines.append(f"{'':<{name_width}}  {count:>4}  {test_id}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize what this repo's checks cost and how they came out, host-tagged so machines are comparable."
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=JOURNAL,
        help="timing journal to read (default: rebuild/out/cycle-timings.ndjson; concatenate journals from several machines to compare them side by side)",
    )
    parser.add_argument("--runs", type=int, default=8, help="how many of the most recent runs to show")
    parser.add_argument(
        "--inner",
        action="store_true",
        help="expand each step's inner [t] phase timings (run_m1 phases, per-config conform sweeps, surface-build phases)",
    )
    parser.add_argument(
        "--by-step",
        action="store_true",
        help="aggregate across all recorded runs: count, median, max, and latest seconds per step and host, with an interactive check's own timings in rows of their own named check:<name>",
    )
    parser.add_argument(
        "--by-outcome",
        action="store_true",
        help="aggregate across all recorded checks: invocations, green/red/skipped counts, and a histogram of the test ids each check has failed on",
    )
    args = parser.parse_args(argv)
    runs, steps, order = load_journal(args.journal)
    checks = load_checks(args.journal)
    if not order and not checks:
        print(f"No timing journal at {args.journal} yet — it appears the first time a check runs here.")
        return 0
    print(f"{args.journal} — {len(order)} runs recorded")
    if args.by_outcome:
        body = render_by_outcome(checks)
    elif args.by_step:
        body = render_by_step(steps, order, checks)
    else:
        body = render_runs(runs, steps, order, args.runs, args.inner)
    print("\n".join(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
