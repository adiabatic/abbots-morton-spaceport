"""Append-only wall-time telemetry for the artifact cycle, and the reporter that reads it back.

Every artifact-cycle run appends to rebuild/out/cycle-timings.ndjson (gitignored with the rest of rebuild/out, never touched by the retention pass, so each machine accumulates its own history): one "step" line per subprocess the driver actually spawned, and one "run" line when the cycle finishes, interrupted finishes included. A step line carries the driver's step name (run_m1, gate:conform, merge, ...), the argv, the return code, the wall seconds, the step's peak RSS in bytes (measured by the driver as it reaps the child, so it covers the child's whole process tree — see peak_rss.reap_peak_rss_bytes), and — parsed out of the child's captured stdout/stderr — any inner "[t] <label> <secs>s" phase lines the child printed, which is how the per-config conform sweeps and run_m1's phase breakdown survive even for gates whose output is never streamed to the console. An inner line may carry its own peak-RSS figure as a trailing "rss_gb=<n>" token (peak_rss.rss_token is the writer; decimal GB, like every figure here), which rides into the journal beside the label's seconds. A run line carries the run's identity — hostname, cpu count, and the size of the box it ran on — start/finish stamps, total wall seconds, and the cycle summary's exit/gates/plan blocks, so a slow step can be read in context: which machine, which skips were in effect, what was deferred.

The box is worth its own field because a per-step peak read months later means nothing without the machine's size beside it: whether a step that held 9 GB was comfortable or was most of the box is a fact about the box, not about the step, and the journal is the only place the two are ever written down together. `make job-costs` divides that same figure by a checked-in per-unit peak to state the width that constant implies here, which is the second reason it is recorded rather than probed at read time — a figure probed on the reader's box would answer for the wrong machine.

Skipped stages never spawn and so never produce a step line; whether a stage was skipped, deferred, or genuinely absent is read from the run line's plan and gates blocks, not from the step list.

A third kind of line, "pool", is written by something that is not the cycle at all: any pytest run whose pool has a unit name to declare — AMS_POOL_UNIT (POOL_UNIT_ENV), set on the child's own environment by the two gate wrappers and by the cycle's rebuild-lane spawns — has its xdist controller append one line at terminal summary naming the unit, the width the pool resolved to, the controller's own peak, and every worker's. That line deliberately carries no run id, because a suite invocation is not a cycle run and an interactive `make test` has no run to belong to; the consequence is wanted, since load_journal files everything under a run id and so never sees a pool line, leaving `make cycle-timings`' two views exactly as they were. load_pool_records is the reader, and `make job-costs` is what finally holds those measurements against the checked-in per-worker constants they are supposed to price.

The reporter is `make cycle-timings` (`uv run python -m rebuild.tools.cycle_timings`): recent runs with steps slowest-first by default, --inner to expand the phase lines, --by-step to aggregate count/median/max/latest per step and host — the host column is what makes a laptop and a desktop directly comparable. Journals from two machines can be concatenated and read with --journal.
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
from datetime import datetime, timezone
from pathlib import Path

from rebuild.tools import memory_budget
from rebuild.tools.peak_rss import format_gb

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "rebuild" / "out" / "cycle-timings.ndjson"
FORMAT = "ams-cycle-timings/1"
# The one spelling of the variable a pytest controller reads to learn what its pool is called. Both gate wrappers and the root conftest reach it from here rather than typing the string, so the journal's `unit` field, the units `make job-costs` calibrates, and whoever sets the variable can never disagree about a name.
POOL_UNIT_ENV = "AMS_POOL_UNIT"

_INNER_LINE = re.compile(r"^\[t\] (.+?) (\d+(?:\.\d+)?)s(?:[ \t](.*))?$", re.MULTILINE)
_RSS_TOKEN = re.compile(r"\brss_gb=(\d+(?:\.\d+)?)")

_JOURNAL_LOCK = threading.Lock()
_pool_warn_state: list[bool] = [False]


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

    The record carries no run id, deliberately. A standalone `make test` or `make test-rebuild` has no cycle run to belong to, and the controller could only invent one or inherit one through a second environment variable whose only job would be to make the record look like it belongs to a cycle it may not belong to. So the pool line is filed under nothing, load_journal drops it along with every other run-less entry, and load_pool_records is how a reader finds it.

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


def parse_inner_timings(text: str) -> list[dict]:
    entries: list[dict] = []
    for match in _INNER_LINE.finditer(text):
        entry: dict = {"label": match.group(1), "elapsed_s": float(match.group(2))}
        rss = _RSS_TOKEN.search(match.group(3) or "")
        if rss:
            entry["rss_gb"] = float(rss.group(1))
        entries.append(entry)
    return entries


class CycleTimings:
    """One instance per cycle run. wrap_spawn decorates the driver's spawn callable so every real subprocess records a step line as it completes; finish records the run line from the already-built cycle summary payload. Appends are lock-serialized (the gate tasks spawn from pool threads) and a journal that cannot be written warns once and never fails the cycle. What a caller adds to a spawn beyond the four arguments timing cares about — a per-child environment, say — passes straight through, because this decorator's business is the clock and not the child's terms."""

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
    """Returns (run lines by run id, step lines by run id, run ids in first-seen order). Malformed lines are skipped: the journal is written by concurrent threads across many runs, and one torn line must not make the whole history unreadable."""
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
        run_id = entry.get("run") if isinstance(entry, dict) else None
        if not isinstance(run_id, str):
            continue
        if run_id not in steps:
            steps[run_id] = []
            order.append(run_id)
        if entry.get("kind") == "run":
            runs[run_id] = entry
        elif entry.get("kind") == "step":
            steps[run_id].append(entry)
    return runs, steps, order


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
            deferred = (run.get("plan") or {}).get("deferred") or []
            if deferred:
                bits.append("deferred=" + ",".join(deferred))
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
            lines.append("  (no steps spawned — everything skipped or deferred)")
    return lines


def render_by_step(steps: dict[str, list[dict]], order: list[str]) -> list[str]:
    buckets: dict[tuple[str, str], list[float]] = {}
    rss_peaks: dict[tuple[str, str], list[float]] = {}
    for run_id in order:
        for step in steps.get(run_id, []):
            key = (str(step.get("name", "?")), str(step.get("host", "?")))
            buckets.setdefault(key, []).append(_seconds(step.get("elapsed_s")))
            peak = step.get("peak_rss_bytes")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize per-step wall times across artifact-cycle runs, host-tagged so machines are comparable."
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
        help="aggregate across all recorded runs: count, median, max, and latest seconds per step and host",
    )
    args = parser.parse_args(argv)
    runs, steps, order = load_journal(args.journal)
    if not order:
        print(f"No timing journal at {args.journal} yet — it appears after the first artifact cycle.")
        return 0
    print(f"{args.journal} — {len(order)} runs recorded")
    if args.by_step:
        body = render_by_step(steps, order)
    else:
        body = render_runs(runs, steps, order, args.runs, args.inner)
    print("\n".join(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
