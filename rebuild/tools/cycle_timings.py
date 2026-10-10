"""Append-only telemetry for the repo's checks and the artifact cycle, recording what each check decided and what it cost, and the reporter that reads it back.

The journal is rebuild/out/cycle-timings.ndjson. It is gitignored with the rest of rebuild/out and the retention pass does not prune it, so each machine keeps its own history. It holds four kinds of line.

A "check" line records one evaluated check invocation: the check's name, the outcome (green, red, or skipped), the status string the evaluator printed, the failure messages the cycle adds to its summary, and the ids of the tests that failed. The artifact cycle tags each check it evaluates with its run id. The interactive entry points (rebuild.tools.rebuild_gate, rebuild.tools.make_test_gate, rebuild.tools.deep_replay, rebuild.tools.deep_sweep, and run_m1's CLI) record their checks with no run. Each invocation is recorded by one process: run_m1's CLI and make_test_gate, which a cycle spawns, record nothing when AMS_CYCLE_RUN (`CYCLE_RUN_ENV`) is set, and the cycle records their line instead. A check line carries its own host, cpu count, and total memory, because most check lines have no run line to take them from. `make conform-deep`'s check line also carries each configuration's highest unit peak footprint beside the per-unit need its width was derived from. The outcome is what the evaluator decided, never the process's return code, so a run that died before its evaluator and a run the evaluator failed can be told apart. run_m1's own check line, from an invocation that built tables, also carries that build's phase lines as `inner`, in a step line's form: the crate's lines, the `kernel_build_tables` line with its record, and the `readback` line with the settlement lookup's figures, both described below.

A "step" line records one subprocess the cycle spawned: the driver's step name (run_m1, gate:conform, merge, ...), the argv, the return code, the wall seconds, and the step's peak RSS in bytes. The driver measures the peak as it reaps the child (`peak_rss.reap_peak_rss_bytes`), and it is the largest of the child and its descendants. The line also carries every `[t] <label> <secs>s` phase line parsed from the child's captured output, so the per-configuration conform sweeps and run_m1's phases are kept even for steps whose output is not shown on the console. A phase line may end with a peak-RSS token `rss_gb=<n>` (`peak_rss.rss_token`), a current-RSS token `rss_now_gb=<n>` (`peak_rss.rss_now_token`) and a peak-footprint token `footprint_gb=<n>` (`peak_rss.footprint_token`), all in decimal GB, which are stored as `rss_gb`, `rss_now_gb` and `footprint_gb`. run_m1's `[t] kernel_build_tables` line ends with the table build's record (`run_m1.TableBuildRecord`) as `key=value` tokens, each stored under its key with the reader `TABLE_BUILD_TOKENS` names: the memo structure stamp (`structure`), the digest of the code the table build runs (`code`, over `run_m1.table_build_code_paths`, which a letter batch's `M1_ALPHABET` edit does not touch), how many configurations read a previous build's memo (`memos_read`, 0 for a fresh build), the runes and predicate classes those memos excluded (`edited`, `classes`), the build's width (`width`), and the alphabet's rune count (`runes`), then with the `build-tables` process's own resident peak and peak footprint as `rss_gb` and `footprint_gb`, which the kernel-build row of `make job-costs` reads beside the step's peak. The journal is per machine, so two fresh builds in one machine's journal with the same code digest and width, one on each side of a letter batch, measure that batch's growth in the table build. run_m1's `[t] readback` line ends with the settlement lookup's size figures, each stored under its key with the reader `READBACK_TOKENS` names: HEAD's short commit id when the build ran (`commit`), the format-2 and format-3 subtable counts whose sum N the subtable-offset headroom is spent on (`settle_format2`, `settle_format3`), that headroom (`subtable_offset_headroom`), the GSUB lookup count (`gsub_lookups`), the merged rule count (`settle_rules`), and the largest packed group's rule bytes (`largest_group_rule_bytes`). The figures are the ones `readback_summary.json` holds, and they do not depend on the machine. The corpus build's `[t] review.build units` line opens its note with the width its units pool ran at, stored as `jobs` (`CORPUS_BUILD_TOKENS`): under a gated cycle that is the plan's `corpus_jobs` or, when the gate lane was idle as the pool started, its `corpus_gates_idle_jobs`. Its `[t] review.build manifest+check` line opens with the width its write ran at under the same key, the number of processes that wrote the shards (`build._write_corpus`). A step line has a return code and no outcome, and no reader here derives an outcome from it. A skipped stage spawns nothing and so writes no step line; the run line's plan and gates blocks say which stages were skipped.

A "run" line is written when a cycle finishes, including an interrupted one. It carries the host, cpu count, total memory, start and finish stamps, total wall seconds, the cycle summary's exit, failures, gates, plan, and argv, and the carry's counts (`artifact_cycle.carry_counts` parses them from the carry's own output line). The plan block names the width of each pooled step (`sweep_jobs`, `corpus_jobs`, `corpus_gates_idle_jobs`, `signature_jobs`, `standing_fill_jobs`, `make_test_workers`, `contracts_workers` and `conform_jobs`), null for a step the pass skipped, and `corpus_gates_idle_jobs` also null on a pass that offers the corpus build no wider width; its `kernel_threads` and `replay_threads` are null only on a gates-only rerun. The total memory is recorded because a step's peak read months later needs the size of the machine it ran on beside it.

A "pool" line records one finished worker pool: its unit name, width, and the controller's and every worker's peak. A pytest controller writes one when AMS_POOL_UNIT (`POOL_UNIT_ENV`) is set, which the two gate wrappers and the cycle's rebuild-lane spawns do. run_m1's conformance sweep and oracle shards and the review build's signature and corpus pools write them too. `load_pool_records` reads them, and `make job-costs` compares the peaks with the checked-in per-worker constants.

The reporter is `make cycle-timings` (`uv run python -m rebuild.tools.cycle_timings`). By default it shows recent runs with steps slowest first. `--inner` expands the phase lines. `--by-step` reports count, median, max, and latest seconds per step and host. `--by-outcome` reports, per check, how many times it ran, how it came out, and which test ids it failed on. `--by-commit` lists the settlement lookup's figures from every run_m1 build that recorded them, by commit (`readback_readings`, `render_by_commit`). `--critical-path` reports, per host, pool policy and pass shape, which step ended each finished pass and how long its lane ran past the other lane, computed from the step lines (`critical_path`). `--journal` reads another journal, such as journals from two machines concatenated.

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
from collections.abc import Callable, Mapping
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
STEP_NAME_ALIASES = {
    "plumbing": "verdict-update",
    "surface-build": "corpus-build",
    "surface-promote": "corpus-promote",
    "census": "review-facts",
    "echo-fill": "duplicate-fill",
    "echo-merge": "duplicate-merge",
}
POOL_UNIT_ALIASES = {"surface": "corpus", "conform-belt": "conform-sweep"}
AFTER_JOIN_STEPS = frozenset({"job-costs", "job-costs-diff"})
CRITICAL_PATH_SHAPE_STEPS = ("run_m1", "corpus-build", "gate:make-test")

_RSS_TOKEN = re.compile(r"\brss_gb=(\d+(?:\.\d+)?)")
_RSS_NOW_TOKEN = re.compile(r"\brss_now_gb=(\d+(?:\.\d+)?)")
_FOOTPRINT_TOKEN = re.compile(r"\bfootprint_gb=(\d+(?:\.\d+)?)")


def _names(value: str) -> list[str]:
    return [] if value in ("", "-") else value.split(",")


TABLE_BUILD_TOKENS: dict[str, Callable[[str], object]] = {
    "structure": str,
    "code": str,
    "memos_read": int,
    "edited": _names,
    "classes": _names,
    "width": int,
    "runes": int,
}

READBACK_TOKENS: dict[str, Callable[[str], object]] = {
    "commit": str,
    "settle_format2": int,
    "settle_format3": int,
    "subtable_offset_headroom": int,
    "gsub_lookups": int,
    "settle_rules": int,
    "largest_group_rule_bytes": int,
}
CORPUS_BUILD_TOKENS: dict[str, Callable[[str], object]] = {"jobs": int}
PHASE_TOKENS: dict[str, Callable[[str], object]] = {
    **TABLE_BUILD_TOKENS,
    **READBACK_TOKENS,
    **CORPUS_BUILD_TOKENS,
}
READBACK_FIGURES = tuple(name for name in READBACK_TOKENS if name != "commit")

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
class CheckResult:
    """What one evaluated check invocation decided, in the shape every evaluator returns and every writer records. `outcome` is green, red, or skipped, which a report can group on. `status` is the label the console and the cycle summary print for the same result ("green", a FAILED clause with the unexplained count, "FAILED (no conform_summary.json)"). Each evaluator words its own status, and that output must not change, so the two are separate fields and neither is derived from the other.

    `failures` is the text the cycle adds to its summary, and `failed_ids` is the failing test ids, which `--by-outcome` counts across invocations. `recordable` says whether this pass may write a green record; the caller reads it, and it is not written to the journal.
    """

    check: str
    outcome: str
    status: str
    failures: list[str]
    failed_ids: list[str]
    recordable: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome == "green"


def record_check(
    result: CheckResult,
    *,
    run: str | None = None,
    argv: list[str] | None = None,
    elapsed_s: float | None = None,
    peak_rss_bytes: int | None = None,
    worker_peak_footprint_bytes: Mapping[str, int] | None = None,
    worker_estimate_bytes: Mapping[str, int] | None = None,
    inner: list[dict] | None = None,
    path: Path | None = None,
) -> None:
    """Append one kind:"check" line for an evaluated check invocation. `run` is the optional parent run id: the artifact cycle passes it through `CycleTimings.record_check`, and an interactive entry point passes nothing. A check that runs its own pool can also record each worker's peak footprint (`peak_rss.peak_footprint_bytes`) and the need it was sized for, keyed by worker, as `make conform-deep` does per configuration, so a real run can be held against its estimate. `inner` is phase lines as `parse_inner_timings` returns them, which run_m1's CLI passes for its table build.

    The host, cpu count, and total memory are read here because most check lines have no run line to take them from, and so parented and unparented lines can be compared directly. `recordable` is not written. `elapsed_s` is rounded to a tenth, as step lines are. Nothing here raises, because the callers are gate wrappers and the cycle's reporting path, where an unwritable journal should warn once and never fail a check that already has a result. `path` is resolved at call time, not bound as a default, so a test that patches `JOURNAL` redirects this write too.
    """
    journal = JOURNAL if path is None else path
    entry: dict = {
        "format": FORMAT,
        "kind": "check",
        "check": result.check,
        "outcome": result.outcome,
        "status": result.status,
        "failures": list(result.failures),
        "failed_ids": list(result.failed_ids),
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
    if worker_peak_footprint_bytes is not None:
        entry["worker_peak_footprint_bytes"] = {
            worker: int(peak) for worker, peak in worker_peak_footprint_bytes.items()
        }
    if worker_estimate_bytes is not None:
        entry["worker_estimate_bytes"] = {
            worker: int(estimate) for worker, estimate in worker_estimate_bytes.items()
        }
    if inner is not None:
        entry["inner"] = [dict(item) for item in inner]
    failure = _append_entry(journal, entry)
    if failure is not None and not _check_warn_state[0]:
        _check_warn_state[0] = True
        print(f"warning: failed to append to {journal}: {failure}", file=sys.stderr)


def parse_inner_timings(text: str) -> list[dict]:
    """Return every `[t]` line in `text` as a phase entry: its label and seconds, the two RSS tokens and the peak-footprint token, and each `key=value` token `PHASE_TOKENS` names (the table build's record, the read-back's figures and the corpus build's units-pool and write widths), read by that key's reader. A token whose value its reader rejects is left out, and every other part of the tail is dropped."""
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
        footprint = _FOOTPRINT_TOKEN.search(tail)
        if footprint:
            entry["footprint_gb"] = float(footprint.group(1))
        for token in tail.split():
            key, separator, value = token.partition("=")
            read = PHASE_TOKENS.get(key) if separator else None
            if read is None:
                continue
            try:
                entry[key] = read(value)
            except ValueError:
                continue
        entries.append(entry)
    return entries


class CycleTimings:
    """The journal writer for one cycle run. `wrap_spawn` wraps the driver's spawn callable so each subprocess that ran records a step line when it completes; `record_check` records a check result under this run; `finish` records the run line from the built cycle summary. Appends are serialized by the module lock, because the gate tasks spawn from pool threads, and an unwritable journal warns once and never fails the cycle. Any other keyword argument a caller passes to a spawn, such as a per-child environment, is passed through unchanged."""

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

    def record_check(self, result: CheckResult, **kw) -> None:
        """Record a check line tagged with this run. The cycle records every check it evaluates, including checks whose work a child process did, because a child the cycle spawned records nothing when CYCLE_RUN_ENV is set; one line per invocation keeps the `--by-outcome` counts accurate."""
        record_check(result, run=self.run_id, path=self.path, **kw)

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
    """Return the run lines by run id, the step lines by run id, and the run ids in first-seen order. Only "run" and "step" lines are read. Check lines are skipped even when they name a run, because a check is not a subprocess, its seconds may duplicate a step line's, and it must not create a run entry; `load_checks` reads them. Malformed lines are skipped, because concurrent writers can leave a torn line and one bad line must not make the history unreadable. A step line whose name is a key of `STEP_NAME_ALIASES`, the name older lines give a step, is read under the step's current name, so `--by-step` and `calibrate_budgets` count its history as one step."""
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
            name = entry.get("name")
            if isinstance(name, str) and name in STEP_NAME_ALIASES:
                entry["name"] = STEP_NAME_ALIASES[name]
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
    """Return every kind:"pool" line in the journal in file order. Pool lines carry no run id, so `load_journal` does not read them. Malformed lines are skipped, because concurrent writers can leave a torn line, and a missing journal reads as no records. A line whose unit is a key of `POOL_UNIT_ALIASES`, the name older lines give a pool, is read under the pool's current name, so `calibrate_budgets` counts its history as one pool."""
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
            unit = entry.get("unit")
            if isinstance(unit, str) and unit in POOL_UNIT_ALIASES:
                entry["unit"] = POOL_UNIT_ALIASES[unit]
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
                    footprint = item.get("footprint_gb")
                    inner_suffix += (
                        f"  footprint={footprint:.2f}GB" if isinstance(footprint, int | float) else ""
                    )
                    inner_suffix += f"  jobs={item['jobs']}" if isinstance(item.get("jobs"), int) else ""
                    lines.append(
                        f"  {_seconds(item.get('elapsed_s')):>10.1f}s    {item.get('label', '?')}{inner_suffix}"
                    )
        if not step_list:
            lines.append("  (no steps spawned — everything skipped)")
    return lines


def render_by_step(steps: dict[str, list[dict]], order: list[str], checks: list[dict]) -> list[str]:
    """Return the count, median, max, and latest seconds per step and host, over the cycle's step lines plus every check line recorded outside a cycle. A check line with a run is left out, because the same run's step line already counts its seconds. A skipped check has no seconds and is left out too, so it does not pull a median toward zero.

    A check line gets its own row, named `check:<name>`, because a check run alone on the machine and the same work run beside a whole cycle pass are different measurements. The prefix also keeps the check `run_m1` apart from the step `run_m1`. That check is recorded both for an interactive full build and for a seconds-long `run_m1 --gates-only`, and in the step's row it would make `latest` report the gates-only rerun as the most recent cost of a full M1 build.
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
    """Return, per check, the number of invocations, the green, red, and skipped counts, and the test ids it failed on, across every host and with or without a parent run. This shows whether a check ever fails and which tests fail, so a suite's cost can be weighed against the failures it has found. Failed ids are ordered by how often each failed, ties by id. Checks are ordered by name, so rows stay in place as check lines are added.

    Check lines with a run are counted here, unlike in `--by-step`, because a check's outcome is recorded nowhere else; only the seconds duplicate a step line. A check line without an `outcome` key carries it under `verdict`, the key older journals use, and is counted from that.
    """
    counts: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    histograms: dict[str, dict[str, int]] = {}
    for entry in checks:
        name = str(entry.get("check", "?"))
        tally = counts.setdefault(name, {"green": 0, "red": 0, "skipped": 0})
        totals[name] = totals.get(name, 0) + 1
        outcome = entry.get("outcome", entry.get("verdict"))
        if isinstance(outcome, str) and outcome in tally:
            tally[outcome] += 1
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


def _stamp_seconds(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def critical_path(run: dict, steps: list[dict]) -> dict | None:
    """Return which step ended one cycle pass, computed from its step lines alone, so it covers a run line whose plan block lacks the pool widths too. A step's end is its `finished_at` less the run's `started_at`, and its start is that end less its `elapsed_s`. The steps in `AFTER_JOIN_STEPS` are left out, because the cycle spawns them once every lane has joined and they end last on every pass.

    The result names the step that ended last and the runner-up with their ends in seconds, the margin between those ends, and the pass shape: the names in `CRITICAL_PATH_SHAPE_STEPS` that have a step line, in that order. The runner-up is the step that ended last in the other lane: the gate lane holds the `gate:` steps, which the cycle runs in its gate pool, and the build lane holds every other step, which the cycle runs one after another. The margin is therefore how long the lane that ended the pass ran past the other lane. The stamps are whole seconds, so two steps that end in the same second are ordered by start and a margin under a second reads as zero. With no ranked step in the other lane the runner-up and the margin are None. The result is None for an interrupted pass, a run line without a readable start, or a pass with no step left to rank.
    """
    started = _stamp_seconds(run.get("started_at"))
    if started is None or run.get("interrupted") is True:
        return None
    spans: list[tuple[float, float, str]] = []
    for step in steps:
        name = str(step.get("name", "?"))
        finished = _stamp_seconds(step.get("finished_at"))
        if name in AFTER_JOIN_STEPS or finished is None:
            continue
        end = finished - started
        spans.append((end, end - _seconds(step.get("elapsed_s")), name))
    if not spans:
        return None
    spans.sort()
    last_end, _, last = spans[-1]
    other_lane = [span for span in spans if span[2].startswith("gate:") != last.startswith("gate:")]
    runner_up = other_lane[-1] if other_lane else None
    names = {step.get("name") for step in steps}
    return {
        "last": last,
        "last_end_s": last_end,
        "runner_up": None if runner_up is None else runner_up[2],
        "runner_up_end_s": None if runner_up is None else runner_up[0],
        "margin_s": None if runner_up is None else last_end - runner_up[0],
        "shape": tuple(name for name in CRITICAL_PATH_SHAPE_STEPS if name in names),
    }


def _median_make_test_workers(plans: list[dict]) -> str:
    """Return the median gate:make-test width over the plans that record one, noting how many plans predate the field. A plan that records null ran no gate:make-test; with no width at all the result is `unrecorded` when some plan lacks the field and `-` otherwise."""
    widths = [plan["make_test_workers"] for plan in plans if isinstance(plan.get("make_test_workers"), int)]
    unrecorded = sum(1 for plan in plans if "make_test_workers" not in plan)
    if not widths:
        return "unrecorded" if unrecorded else "-"
    text = f"{statistics.median(widths):g}"
    return f"{text} ({unrecorded} unrecorded)" if unrecorded else text


def render_critical_path(runs: dict[str, dict], steps: dict[str, list[dict]]) -> list[str]:
    """Return one row per host, pool policy and pass shape (`critical_path`) over every run line: the run count, the median margin by which the lane that ended the pass outlasted the other lane, the median wall, the median gate:make-test width (`unrecorded` for runs whose plan block predates the field), and how often each step ended last, most often first. A run line whose plan block lacks a pool policy is grouped under `unrecorded`, and a pass `critical_path` cannot rank is left out."""
    groups: dict[tuple[str, str, str], list[tuple[dict, dict, dict]]] = {}
    for run_id, run in runs.items():
        path = critical_path(run, steps.get(run_id, []))
        if path is None:
            continue
        plan = run.get("plan")
        plan = plan if isinstance(plan, dict) else {}
        key = (
            str(run.get("host", "?")),
            str(plan.get("pool_policy", "unrecorded")),
            "+".join(path["shape"]) or "none",
        )
        groups.setdefault(key, []).append((run, plan, path))
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    for (host, policy, shape), members in sorted(groups.items()):
        margins = [path["margin_s"] for _, _, path in members if path["margin_s"] is not None]
        walls = [float(run["wall_s"]) for run, _, _ in members if isinstance(run.get("wall_s"), int | float)]
        tally: dict[str, int] = {}
        for _, _, path in members:
            tally[path["last"]] = tally.get(path["last"], 0) + 1
        ended = ", ".join(
            f"{name} {count}" for name, count in sorted(tally.items(), key=lambda item: (-item[1], item[0]))
        )
        rows.append(
            (
                host,
                policy,
                shape,
                str(len(members)),
                f"{statistics.median(margins):.1f}s" if margins else "",
                f"{statistics.median(walls):.1f}s" if walls else "",
                _median_make_test_workers([plan for _, plan, _ in members]),
                ended,
            )
        )
    header = ("host", "policy", "shape", "runs", "margin", "wall", "make-test workers", "ended last")
    widths = [max(len(row[column]) for row in [header, *rows]) for column in range(len(header) - 1)]
    lines: list[str] = []
    for index, row in enumerate([header, *rows]):
        cells = [
            cell.rjust(width) if column in (3, 4, 5, 6) else cell.ljust(width)
            for column, (cell, width) in enumerate(zip(row, widths))
        ]
        lines.append(("\n" if index == 0 else "") + "  ".join([*cells, row[-1]]))
    return lines


def readback_readings(path: Path) -> list[dict]:
    """Return one reading per run_m1 build whose `[t] readback` phase carries the settlement lookup's figures, oldest first by `finished_at`, so journals concatenated from several machines interleave. A reading holds the line's `finished_at`, the phase's `commit` and figures, and the rune count from the same build's `kernel_build_tables` phase. The readings come from the cycle's run_m1 step lines and from run_m1 check lines recorded outside a cycle; a cycle's own run_m1 check line carries no phases. A build whose read-back failed still printed its figures, so it is read like any other. Malformed lines are skipped, and a missing journal reads as no readings."""
    readings: list[dict] = []
    if not path.exists():
        return readings
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        step = entry.get("kind") == "step" and entry.get("name") == "run_m1"
        check = entry.get("kind") == "check" and entry.get("check") == "run_m1" and entry.get("run") is None
        if not (step or check):
            continue
        phases = [item for item in entry.get("inner") or [] if isinstance(item, dict)]
        figures = [
            item
            for item in phases
            if item.get("label") == "readback" and any(name in item for name in READBACK_FIGURES)
        ]
        if not figures:
            continue
        runes = [
            item["runes"] for item in phases if item.get("label") == "kernel_build_tables" and "runes" in item
        ]
        readback = figures[-1]
        readings.append(
            {
                "finished_at": entry.get("finished_at"),
                "commit": readback.get("commit"),
                "runes": runes[-1] if runes else None,
                **{name: readback.get(name) for name in READBACK_FIGURES},
            }
        )
    readings.sort(key=lambda reading: str(reading.get("finished_at") or ""))
    return readings


def render_by_commit(readings: list[dict]) -> list[str]:
    """Return one row per run of consecutive readings that share a commit and every figure, oldest first: the commit, the rune count, N (the format-2 and format-3 subtable counts summed) with its two parts, the subtable-offset headroom, the GSUB lookup count, the merged rule count, the largest group's rule bytes, how many builds the row stands for, and when the first of them finished. A rebuild that reproduces the figures adds to its row's count. A commit whose builds gave different figures gets a row for each, because the commit is HEAD when the build ran and the working tree can hold uncommitted edits on top of it: the pass made before a commit records the figures that commit will hold under the commit before it. A missing figure prints as `-`."""

    def cell(value) -> str:
        if value is None:
            return "-"
        return f"{value:,}" if isinstance(value, int) else str(value)

    groups: list[tuple[tuple, dict, int]] = []
    for reading in readings:
        key = (reading.get("commit"), reading.get("runes"), *(reading.get(name) for name in READBACK_FIGURES))
        if groups and groups[-1][0] == key:
            groups[-1] = (key, groups[-1][1], groups[-1][2] + 1)
        else:
            groups.append((key, reading, 1))
    header = (
        "commit",
        "runes",
        "N",
        "format2",
        "format3",
        "headroom",
        "lookups",
        "rules",
        "largest group",
        "builds",
        "first finished",
    )
    rows = []
    for _key, reading, count in groups:
        format2, format3 = reading.get("settle_format2"), reading.get("settle_format3")
        total = format2 + format3 if isinstance(format2, int) and isinstance(format3, int) else None
        rows.append(
            (
                cell(reading.get("commit")),
                cell(reading.get("runes")),
                cell(total),
                cell(format2),
                cell(format3),
                cell(reading.get("subtable_offset_headroom")),
                cell(reading.get("gsub_lookups")),
                cell(reading.get("settle_rules")),
                cell(reading.get("largest_group_rule_bytes")),
                str(count),
                cell(reading.get("finished_at")),
            )
        )
    if not rows:
        return ["\nNo run_m1 build in this journal has recorded its read-back figures yet."]
    widths = [max(len(row[column]) for row in [header, *rows]) for column in range(len(header) - 1)]
    lines: list[str] = []
    for index, row in enumerate([header, *rows]):
        cells = [
            cell_text.ljust(width) if column == 0 else cell_text.rjust(width)
            for column, (cell_text, width) in enumerate(zip(row, widths))
        ]
        lines.append(("\n" if index == 0 else "") + "  ".join([*cells, row[-1]]))
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
        help="expand each step's inner [t] phase timings (run_m1 phases, per-config conform sweeps, corpus-build phases)",
    )
    parser.add_argument(
        "--by-step",
        action="store_true",
        help="aggregate across all recorded runs: count, median, max, and latest seconds per step and host, with an interactive check's own timings in rows of their own named check:<name>",
    )
    parser.add_argument(
        "--critical-path",
        action="store_true",
        help="aggregate across every finished run whose run line carries its start stamp, from their step lines; an interrupted run is left out. Per host, pool policy and pass shape (which of run_m1, corpus-build and gate:make-test ran): how often each step ended last, the median margin by which the lane that ended the pass outlasted the other lane, the median wall, and the median gate:make-test width",
    )
    parser.add_argument(
        "--by-commit",
        action="store_true",
        help="list the settlement lookup's figures that run_m1's read-back recorded, oldest first: the commit HEAD was at, the rune count, N (format-2 plus format-3 subtables), the subtable-offset headroom, the GSUB lookup count, the merged rule count and the largest group's rule bytes, one row per run of consecutive builds that agree on all of them",
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
    elif args.by_commit:
        body = render_by_commit(readback_readings(args.journal))
    elif args.by_step:
        body = render_by_step(steps, order, checks)
    elif args.critical_path:
        body = render_critical_path(runs, steps)
    else:
        body = render_runs(runs, steps, order, args.runs, args.inner)
    print("\n".join(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
