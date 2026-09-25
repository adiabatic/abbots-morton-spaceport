"""Append-only telemetry for the repo's checks and the artifact cycle, recording what each check decided and what it cost, and the reporter that reads it back.

The journal is rebuild/out/cycle-timings.ndjson. It is gitignored with the rest of rebuild/out and the retention pass does not prune it, so each machine keeps its own history. It holds four kinds of line.

A "check" line records one judged check invocation: the check's name, the verdict (green, red, or skipped), the status string the judge printed, the failure messages the cycle adds to its summary, and the ids of the tests that failed. The artifact cycle tags each check it judges with its run id. The interactive entry points (rebuild.tools.rebuild_gate, rebuild.tools.make_test_gate, rebuild.tools.deep_replay, and run_m1's CLI) record their checks with no run. Each invocation is recorded by one process: run_m1's CLI and make_test_gate, which a cycle spawns, record nothing when AMS_CYCLE_RUN (`CYCLE_RUN_ENV`) is set, and the cycle records their line instead. A check line carries its own host, cpu count, and total memory, because most check lines have no run line to take them from. The verdict is what the judge decided, never the process's return code, so a run that died before its judge and a run the judge failed can be told apart.

A "step" line records one subprocess the cycle spawned: the driver's step name (run_m1, gate:conform, merge, ...), the argv, the return code, the wall seconds, and the step's peak RSS in bytes. The driver measures the peak as it reaps the child (`peak_rss.reap_peak_rss_bytes`), and it is the largest of the child and its descendants. The line also carries every `[t] <label> <secs>s` phase line parsed from the child's captured output, so the per-configuration conform sweeps and run_m1's phases are kept even for steps whose output is not shown on the console. A phase line may end with a peak-RSS token `rss_gb=<n>` (`peak_rss.rss_token`) and a current-RSS token `rss_now_gb=<n>` (`peak_rss.rss_now_token`), both in decimal GB, which are stored as `rss_gb` and `rss_now_gb`. A step line has a return code and no verdict, and no reader here derives a verdict from it. A skipped stage spawns nothing and so writes no step line; the run line's plan and gates blocks say which stages were skipped.

A "run" line is written when a cycle finishes, including an interrupted one. It carries the host, cpu count, total memory, start and finish stamps, total wall seconds, the cycle summary's exit, failures, gates, plan, and argv, and the carry's figures (`artifact_cycle.carry_figures` parses them from the carry's own output line). The total memory is recorded because a step's peak read months later needs the size of the machine it ran on beside it.

A "pool" line records one finished worker pool: its unit name, width, and the controller's and every worker's peak. A pytest controller writes one when AMS_POOL_UNIT (`POOL_UNIT_ENV`) is set, which the two gate wrappers and the cycle's rebuild-lane spawns do. run_m1's conform belt and oracle shards and the review build's signature and surface pools write them too. `load_pool_records` reads them, and `make job-costs` compares the peaks with the checked-in per-worker constants.

The reporter is `make cycle-timings` (`uv run python -m rebuild.tools.cycle_timings`). By default it shows recent runs with steps slowest first. `--inner` expands the phase lines. `--by-step` reports count, median, max, and latest seconds per step and host. `--by-outcome` reports, per check, how many times it ran, how it came out, and which test ids it failed on. `--journal` reads another journal, such as journals from two machines concatenated.

The file, the module, and the `make cycle-timings` target keep the "cycle-timings" name although the cycle is not the only writer. The journal is per-machine and gitignored, so renaming the file would leave each machine's existing history behind.
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
# The variable a pytest controller reads for its pool's unit name. Both gate wrappers and the root conftest import the name from here, so they agree with the journal's `unit` field and the units `make job-costs` checks.
POOL_UNIT_ENV = "AMS_POOL_UNIT"
# The variable that tells a spawned check a cycle is recording its check line. The cycle sets it to its run id and every child inherits it; the entry points that check it import the name from here.
CYCLE_RUN_ENV = "AMS_CYCLE_RUN"

_RSS_TOKEN = re.compile(r"\brss_gb=(\d+(?:\.\d+)?)")
_RSS_NOW_TOKEN = re.compile(r"\brss_now_gb=(\d+(?:\.\d+)?)")

_JOURNAL_LOCK = threading.Lock()
_pool_warn_state: list[bool] = [False]
_check_warn_state: list[bool] = [False]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_entry(path: Path, entry: dict) -> str | None:
    """Append one JSON line under the module-wide lock, and return None on success or the failure's repr when the journal cannot be written. The lock is module-wide because several writers can share the file in one process (a cycle's gate-pool threads, and the module-level `record_pool` and `record_check`), and a per-instance lock would not serialize them against each other. A failure is returned, not raised, because an unwritable journal should never fail a caller; each caller warns once."""
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
    """Sort key for a (gateway id, peak) pair: gw2 before gw10, and an id without a number first. The root conftest sorts its printed worker line with it too, so the line and the pool record list workers in the same order."""
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
    """Append one kind:"pool" line for a finished worker pool: the unit it measured, the width it ran at, and the controller's and each worker's peak RSS. The writers are pytest controllers, run_m1's pools, and the review build's pools, none of which needs a cycle.

    The line carries no run id, even under CYCLE_RUN_ENV, because one cycle pass runs several pools and a standalone pool has no run. `load_journal` ignores pool lines, and `load_pool_records` reads them.

    `width` is recorded separately from `worker_peaks` because the two counts can differ: an xdist worker that dies without returning its workeroutput has no entry in `worker_peaks`. Peaks are ordered with `gateway_order`, the order the root conftest prints them in. `path` is resolved at call time, not bound as a default, so a test that patches `JOURNAL` redirects this write too. Nothing here raises, because a terminal-summary hook calls it and a raise would disrupt the report of a suite that passed; an unwritable journal warns once per process.
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
    """What one judged check invocation decided, in the shape every judge returns and every writer records. `verdict` is green, red, or skipped, which a report can group on. `status` is the label the console and the cycle summary print for the same judgment ("green", a FAILED clause with the unexplained count, "FAILED (no conform_summary.json)"). Each judge words its own status, and that output must not change, so the two are separate fields and neither is derived from the other.

    `failures` is the text the cycle adds to its summary, and `failed_ids` is the failing test ids, which `--by-outcome` counts across invocations. `recordable` says whether this pass may write a green record; the caller reads it, and it is not written to the journal.
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
    """Append one kind:"check" line for a judged check invocation. `run` is the optional parent run id: the artifact cycle passes it through `CycleTimings.record_check`, and an interactive entry point passes nothing.

    The host, cpu count, and total memory are read here because most check lines have no run line to take them from, and so parented and unparented lines can be compared directly. `recordable` is not written. `elapsed_s` is rounded to a tenth, as step lines are. Nothing here raises, because the callers are gate wrappers and the cycle's reporting path, where an unwritable journal should warn once and never fail a check that already has a verdict. `path` is resolved at call time, not bound as a default, so a test that patches `JOURNAL` redirects this write too.
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
        tail = match.group(3) or ""
        rss = _RSS_TOKEN.search(tail)
        if rss:
            entry["rss_gb"] = float(rss.group(1))
        now = _RSS_NOW_TOKEN.search(tail)
        if now:
            entry["rss_now_gb"] = float(now.group(1))
        entries.append(entry)
    return entries


class CycleTimings:
    """The journal writer for one cycle run. `wrap_spawn` wraps the driver's spawn callable so each subprocess that ran records a step line when it completes; `record_check` records a verdict under this run; `finish` records the run line from the built cycle summary. Appends are serialized by the module lock, because the gate tasks spawn from pool threads, and an unwritable journal warns once and never fails the cycle. Any other keyword argument a caller passes to a spawn, such as a per-child environment, is passed through unchanged."""

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
        """Record a check line tagged with this run. The cycle records every check it judges, including checks whose work a child process did, because a child the cycle spawned records nothing when CYCLE_RUN_ENV is set; one line per invocation keeps the `--by-outcome` counts accurate."""
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
                "carry": summary.get("carry"),
            }
        )

    def _append(self, entry: dict) -> None:
        failure = _append_entry(self.path, entry)
        if failure is not None and not self._warned:
            self._warned = True
            print(f"warning: failed to append to {self.path}: {failure}", file=sys.stderr)


def load_journal(path: Path) -> tuple[dict[str, dict], dict[str, list[dict]], list[str]]:
    """Return the run lines by run id, the step lines by run id, and the run ids in first-seen order. Only "run" and "step" lines are read. Check lines are skipped even when they name a run, because a check is not a subprocess, its seconds may duplicate a step line's, and it must not create a run entry; `load_checks` reads them. Malformed lines are skipped, because concurrent writers can leave a torn line and one bad line must not make the history unreadable."""
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
    """Return every kind:"check" line in the journal in file order, with or without a run. Malformed lines are skipped, and a missing journal reads as no checks."""
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
    """Return every kind:"pool" line in the journal in file order. Pool lines carry no run id, so `load_journal` does not read them. Malformed lines are skipped, because concurrent writers can leave a torn line, and a missing journal reads as no records."""
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
            # A run line without `mem_total_bytes` predates the field, so print nothing rather than `ram=?`, which would suggest a failed probe. `cpu_count` has always been written, so `cpus=?` does mean a failed probe.
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
                    now = item.get("rss_now_gb")
                    inner_suffix = f"  rss={rss:.2f}GB" if isinstance(rss, int | float) else ""
                    inner_suffix += f"  now={now:.2f}GB" if isinstance(now, int | float) else ""
                    lines.append(
                        f"  {_seconds(item.get('elapsed_s')):>10.1f}s    {item.get('label', '?')}{inner_suffix}"
                    )
        if not step_list:
            lines.append("  (no steps spawned — everything skipped)")
    return lines


def render_by_step(steps: dict[str, list[dict]], order: list[str], checks: list[dict]) -> list[str]:
    """Return the count, median, max, and latest seconds per step and host, over the cycle's step lines plus every check line recorded outside a cycle. A check line with a run is left out, because the same run's step line already counts its seconds. A skipped check has no seconds and is left out too, so it does not pull a median toward zero.

    A check line gets its own row, named `check:<name>`, because a check run alone on the machine and the same work run beside a whole cycle pass are different measurements. The prefix also keeps the check `run_m1` apart from the step `run_m1`. That check is recorded both for an interactive full build and for a seconds-long `run_m1 --gates-only`, and in the step's row it would make `latest` report the re-adjudication as the most recent cost of a full M1 build.
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
    """Return, per check, the number of invocations, the green, red, and skipped counts, and the test ids it failed on, across every host and with or without a parent run. This shows whether a check ever fails and which tests fail, so a suite's cost can be weighed against the failures it has found. Failed ids are ordered by how often each failed, ties by id. Checks are ordered by name, so rows stay in place as verdicts are added.

    Check lines with a run are counted here, unlike in `--by-step`, because a verdict is recorded nowhere else; only the seconds duplicate a step line.
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
