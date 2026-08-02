"""Append-only wall-time telemetry for the artifact cycle, and the reporter that reads it back.

Every artifact-cycle run appends to rebuild/out/cycle-timings.ndjson (gitignored with the rest of rebuild/out, never touched by the retention pass, so each machine accumulates its own history): one "step" line per subprocess the driver actually spawned, and one "run" line when the cycle finishes, interrupted finishes included. A step line carries the driver's step name (run_m1, gate:conform, merge, ...), the argv, the return code, the wall seconds, and — parsed out of the child's captured stdout/stderr — any inner "[t] <label> <secs>s" phase lines the child printed, which is how the per-config conform sweeps and run_m1's phase breakdown survive even for gates whose output is never streamed to the console. A run line carries the run's identity (hostname, cpu count), start/finish stamps, total wall seconds, and the cycle summary's exit/gates/plan blocks, so a slow step can be read in context: which machine, which skips were in effect, what was deferred.

Skipped stages never spawn and so never produce a step line; whether a stage was skipped, deferred, or genuinely absent is read from the run line's plan and gates blocks, not from the step list.

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

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "rebuild" / "out" / "cycle-timings.ndjson"
FORMAT = "ams-cycle-timings/1"

_INNER_LINE = re.compile(r"^\[t\] (.+?) (\d+(?:\.\d+)?)s(?:[ \t].*)?$", re.MULTILINE)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_inner_timings(text: str) -> list[dict]:
    return [
        {"label": match.group(1), "elapsed_s": float(match.group(2))} for match in _INNER_LINE.finditer(text)
    ]


class CycleTimings:
    """One instance per cycle run. wrap_spawn decorates the driver's spawn callable so every real subprocess records a step line as it completes; finish records the run line from the already-built cycle summary payload. Appends are lock-serialized (the gate tasks spawn from pool threads) and a journal that cannot be written warns once and never fails the cycle."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.run_id = uuid.uuid4().hex[:12]
        self.host = socket.gethostname()
        self.started_at = _utc_stamp()
        self._t0 = time.perf_counter()
        self._lock = threading.Lock()
        self._warned = False

    def wrap_spawn(self, spawn):
        def timed(name, argv, *, emit, registry, stream):
            result = spawn(name, argv, emit=emit, registry=registry, stream=stream)
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
        line = json.dumps(entry)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:
            if not self._warned:
                self._warned = True
                print(f"warning: failed to append to {self.path}: {exc!r}", file=sys.stderr)


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


def _seconds(value) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


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
            lines.append(f"  {_seconds(step.get('elapsed_s')):>8.1f}s  {step.get('name', '?')}{suffix}")
            if inner:
                for item in step.get("inner", []):
                    lines.append(f"  {_seconds(item.get('elapsed_s')):>10.1f}s    {item.get('label', '?')}")
        if not step_list:
            lines.append("  (no steps spawned — everything skipped or deferred)")
    return lines


def render_by_step(steps: dict[str, list[dict]], order: list[str]) -> list[str]:
    buckets: dict[tuple[str, str], list[float]] = {}
    for run_id in order:
        for step in steps.get(run_id, []):
            key = (str(step.get("name", "?")), str(step.get("host", "?")))
            buckets.setdefault(key, []).append(_seconds(step.get("elapsed_s")))
    rows = [
        (name, host, len(values), statistics.median(values), max(values), values[-1])
        for (name, host), values in buckets.items()
    ]
    rows.sort(key=lambda row: (-row[3], row[0], row[1]))
    name_width = max([len(row[0]) for row in rows] + [len("step")])
    host_width = max([len(row[1]) for row in rows] + [len("host")])
    lines = [
        f"\n{'step':<{name_width}}  {'host':<{host_width}}  {'runs':>4}  {'median':>8}  {'max':>8}  {'latest':>8}"
    ]
    for name, host, count, median, peak, latest in rows:
        lines.append(
            f"{name:<{name_width}}  {host:<{host_width}}  {count:>4}  {median:>7.1f}s  {peak:>7.1f}s  {latest:>7.1f}s"
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
