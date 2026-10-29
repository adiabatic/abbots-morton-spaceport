"""The speed-up check for #584 (`make perf-check`): does a candidate change keep the outputs, keep every fault caught, and prove a shorter edit-to-READY time on the scenarios in `rebuild/perf/scenarios.yaml`? `doc/perf-check.md` is the runbook; this docstring is the authority on what a check does.

A check is paired. It takes B, the candidate's own base (HEAD for the default `--candidate worktree`, which is this checkout's uncommitted diff, or A for `--candidate A..B`), and measures B and B plus the whole candidate side by side in one run, in the scratch clones `rebuild.tools.perf_clone` manages under `var/perf/`, never in this checkout. `--baseline` takes no candidate and measures B against itself, the A/A check, and on success rewrites this machine's row in each `rebuild/perf/baselines/<scenario>.json`, keyed by `cpu_count` and `mem_total_bytes`.

The scenarios: a `revert` scenario starts from the tree with its edit reversed and times the re-application, which lands exactly on the tree; a `forward` scenario starts from its own fixed base and times its patch; `none` times a pass over the tree itself. Every revert-form patch is checked at B first (`patches_apply_at`: each reverses, they compose, and each re-applies on the composite), on a temporary index, so this checkout's index and work tree are never touched; a patch that fails there makes its scenario `not applicable at B: re-aim`, and one the candidate's own changes stop from reversing makes it `not applicable: the candidate touches its edit`. A forward scenario takes a candidate only when the candidate's paths, less `HARNESS_PATHS` and Markdown, are unchanged between its base and B, and the candidate less those paths (`FORWARD_EXCLUDES`) applies at its base (`forward_status`); otherwise it is `not measured`, and its candidate side is built from the candidate less those paths. Each side's root is its composite, the tree with every revert-form edit reversed, so no start state's caches have seen the tree that its edit lands on. The base side's verdict store is seeded from this checkout's journal as of B's committer time (`merge_verdicts --restore-as-of`, read-only, run in the clone at this checkout's HEAD commit, so no uncommitted code reads the live journal), scrubbed of every fill of a standing rule that B has and the composite lacks or holds differently, and of every duplicate fill copied from a scrubbed unit (`scrub_seed`), and its stashes are deleted before the root is snapshotted. `--seed FILE` uses a kept seed instead, which is how another machine reproduces this machine's fingerprints. A paired check's seed is scratch, deleted once the base side's root is prepared; the letter migration's and each `--baseline`'s are kept under `var/keep/perf/seeds/`.

Each scenario runs two hyperfine batches back to back, base first for odd-numbered scenarios and the candidate first for even ones, each one warm-up and `--runs` (at least `MIN_RUNS`) timed runs of `make -C <clone> artifact-cycle ARGS=--stop-server` from the edit, already applied by `perf_clone prepare`, to its exit. A run counts only when its harvested `terminal.log` ends READY (`classify_run`); make's exit status says only zero or not. A batch with a refused, interrupted or disturbed run is discarded and rerun, at most `BATCH_RETRIES` times, after the probes clear; a failed run fails the scenario. Each batch must agree with the clone's timings journal (`journal_agreement`), its run lines must share one pass shape (`pass_shape`), the base batch must hold the scenario's declared shape (`holds_declared`; a miss is `shape drifted: re-aim`), and the candidate batch's compared shape must equal the base's (`compare_shapes`). Each side is fingerprinted after its last run (`rebuild/tools/perf_fingerprint.py`) and the two compared (`compare_fingerprints`); under `--baseline` the intermediate stores must match too. The timing verdict (`classify`) is `proven faster` only when every candidate run beats every base run, `proven slower` in the mirror case, `within spread` otherwise, and `no verdict` when the two batches ran under different `rustc` or OS builds. A pair whose only failure is a separation that fails the check (`proven slower`, or either separation under `--baseline`) is measured once more from the same start states, and the second pair's result stands, with the first pair's verdict beside it, so a chance separation fails a scenario only by repeating.

The fault kit (`rebuild/tools/fault_kit.py`, called only through its CLI) re-injects the rows `--faults` selects (`auto`: the rows whose catching gates the candidate's paths touch, and every row under `--baseline`, which has no candidate; `fault_selection`) on the candidate side's tree in the shared clone, and its result joins the report.

System probes run before every pass and around every timed run (`summarize_probes`, `probe_outcomes`): a refusing condition stops the check with exit 2, a waiting one makes it wait (up to `WAIT_LIMIT_S`), and one that holds after a run discards the batch. A probe whose command could not run counts as waiting. The driver holds `var/perf/perf.lock` for the whole check, runs `caffeinate -i -w` for its own lifetime, and refuses to start while `AMS_TOTAL_MEMORY_BYTES` is set, since the clone passes run without it and their run lines carry the machine's own total.

A successful `--baseline` rewrites this machine's rows only after comparing each new fingerprint with every other machine's row at the same base (`compare_fingerprints`, with `same_seed` from the two rows' seeds); a difference is a determinism finding, so the check names it and writes no baseline file.

Every record of a check is kept under `var/keep/perf/<stamp>-<id>/`: `report.json`, `report.txt`, `system.ndjson`, the captured candidate, and per scenario and side `hyperfine.json`, `fingerprint.json` and `runs/<k>/` (the stamp, the run's build logs and its journal lines). `--dry-run` prints the plan, the probes, which scenario patches apply at B, the fault kit's selection and the estimated wall time (`estimate`), and builds nothing. `--write-patch NAME` rewrites a scenario's patch in place from its `edit:`, and refuses when the edit does not resolve. `--clean`, which takes no other flag, deletes `var/perf/`, and refuses while a check or a clone's pass holds its lock. SIGINT, SIGTERM and SIGHUP stop a check after the current step, except one that was ignored when the check started, so a check under `nohup` outlives its shell. Exit status: 0 when every scenario and fault row passed, 1 when one failed or got no verdict, 2 when the check refused, before measuring or partway (a refusing probe, a wait past `WAIT_LIMIT_S`, a prepare hook's refusal, a root that would not warm), and 128 plus the signal's number when a signal stopped it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from rebuild.pipeline.fingerprint import file_sha256
from rebuild.review import store_lock
from rebuild.tools import cycle_timings, memory_budget, perf_clone
from rebuild.tools.peak_rss import format_gb
from rebuild.tools.perf_clone import PerfRefused, WarmFailed
from rebuild.tools.verdict_notes import _MARKER

ROOT = Path(__file__).resolve().parents[2]
PERF_DIR = ROOT / "var" / "perf"
KEEP_DIR = ROOT / "var" / "keep" / "perf"
PERF_DATA = ROOT / "rebuild" / "perf"
SCENARIOS = PERF_DATA / "scenarios.yaml"
SCENARIOS_FORMAT = "ams-perf-scenarios/1"
BASELINE_FORMAT = "ams-perf-baseline/1"
REPORT_FORMAT = "ams-perf-check/1"
FINGERPRINT_SCRIPT = ROOT / "rebuild" / "tools" / "perf_fingerprint.py"
FAULT_KIT = ROOT / "rebuild" / "tools" / "fault_kit.py"
STANDING_RULES = "rebuild/standing-approvals.yaml"

MIN_RUNS = 5
GAP_RANGE_S = 2.0
WAIT_LIMIT_S = 2 * 3600
POLL_S = 60.0
BATCH_RETRIES = 2
DISK_FLOOR_BYTES = 50_000_000_000
START_WARM_PASSES = 3
ROOT_WARM_PASSES = 4
FORMS = ("revert", "forward", "none")
SCENARIO_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
RESERVED_NAMES = ("root",)
SHARED_CLONE = "shared"
FAULT_MODES = ("auto", "all", "none")
MEMORY_OVERRIDE_ENV = "AMS_TOTAL_MEMORY_BYTES"

HARNESS_PATHS = (
    "rebuild/perf/",
    "rebuild/tools/perf_check.py",
    "rebuild/tools/perf_clone.py",
    "rebuild/tools/perf_fingerprint.py",
    "rebuild/tools/fault_kit.py",
    "rebuild/test_perf_check.py",
    "rebuild/test_perf_clone.py",
    "rebuild/test_perf_fingerprint.py",
    "rebuild/test_fault_kit.py",
)
FORWARD_EXCLUDES = (*(f"{path}*" if path.endswith("/") else path for path in HARNESS_PATHS), "*.md")
COMPARED_PLAN_FLAGS = (
    "first_run",
    "skip_run_m1",
    "rerun_gates_only",
    "skip_corpus",
    "skip_verdict_update",
    "skip_gates",
    "skip_contracts",
    "skip_conform",
    "do_merge",
    "land",
    "promote_corpus",
    "refresh_assets",
    "contracts_after_units",
    "conform_max_length",
)
REPORTED_PLAN_WIDTHS = (
    "kernel_threads",
    "replay_threads",
    "sweep_jobs",
    "corpus_jobs",
    "corpus_gates_idle_jobs",
    "signature_jobs",
    "standing_fill_jobs",
    "contracts_workers",
    "make_test_workers",
    "conform_jobs",
    "scratch_beside_default",
    "overlap_memo_writes",
    "pool_policy",
)
IGNORED_PLAN_KEYS = ("verdicts", "carry_out", "next_corpus", "review_out", "short_id")
HEAVY_EXECUTABLES = ("pytest", "cargo", "rustc", "pyright", "ams-m1-kernel")
HEAVY_MODULES = (
    "rebuild.tools.artifact_cycle",
    "rebuild.pipeline.run_m1",
    "rebuild.review.build",
    "rebuild.tools.deep_sweep",
    "rebuild.tools.deep_replay",
    "rebuild.tools.rebuild_gate",
    "rebuild.tools.make_test_gate",
    "rebuild.tools.standing_daemon",
    "pytest",
    "pyright",
)
PYTHON_EXECUTABLE = re.compile(r"[Pp]ython(\d+(\.\d+)*t?)?")
PYTHON_OPTIONS_WITH_ARGUMENT = ("-W", "-X")
UNREADABLE_PROBES = ("pmset", "therm", "ps")
PROBE_CHECKS = ("powermode", "therm", "pressure", "ac_power", "heavy", "unreadable")

READY_PREFIX = "READY - adjudicate at "
COMPLETE_LINE = "Cycle complete."
RETRY_OUTCOMES = ("refused", "interrupted", "disturbed")

PASS_OVERHEAD_S = 10.0
FINGERPRINT_S = 120.0
FAULT_KIT_S = 82 * 60.0
COLD_FIRST_PASS_S = 20 * 60.0
ESTIMATE_RECENT = 20
FALLBACK_PASS_S = 600.0


@dataclass(frozen=True)
class Scenario:
    """One entry of `rebuild/perf/scenarios.yaml`. `number` is its 1-based position, which decides which side runs first."""

    number: int
    name: str
    stands_for: str
    form: str
    base: str | None
    seed: Mapping | None
    commit: str | None
    subject: str | None
    paths: tuple[str, ...]
    patch: Path | None
    shape: Mapping


def load_scenarios(path: Path = SCENARIOS) -> list[Scenario]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("format") != SCENARIOS_FORMAT:
        raise PerfRefused(f"{path} is not an {SCENARIOS_FORMAT} file")
    scenarios: list[Scenario] = []
    for number, entry in enumerate(document.get("scenarios") or [], start=1):
        name = entry["name"]
        if not isinstance(name, str) or not SCENARIO_NAME.fullmatch(name) or name in RESERVED_NAMES:
            raise PerfRefused(
                f"{path}: scenario name {name!r} must be a lowercase hyphenated slug other than {', '.join(RESERVED_NAMES)}, since it names snapshot and record directories"
            )
        form = entry["form"]
        if form not in FORMS:
            raise PerfRefused(f"{path}: {name} has form {form!r}, not one of {', '.join(FORMS)}")
        edit = entry.get("edit") or {}
        patch = entry.get("patch")
        if form != "none" and (not edit.get("commit") or not patch):
            raise PerfRefused(f"{path}: {name} needs an edit and a patch")
        if form == "forward" and not entry.get("base"):
            raise PerfRefused(f"{path}: {name} is forward-form and needs a base")
        scenarios.append(
            Scenario(
                number=number,
                name=name,
                stands_for=entry.get("stands_for", ""),
                form=form,
                base=entry.get("base"),
                seed=entry.get("seed"),
                commit=edit.get("commit"),
                subject=edit.get("subject"),
                paths=tuple(edit.get("paths") or ()),
                patch=None if patch is None else path.parent / patch,
                shape=entry.get("shape") or {},
            )
        )
    names = [scenario.name for scenario in scenarios]
    if len(set(names)) != len(names):
        raise PerfRefused(f"{path}: scenario names repeat")
    return scenarios


def settle_shape(scenarios: Sequence[Scenario]) -> Mapping:
    """The declared shape of the `none`-form scenario, which a warm pass must hold to count as settled."""
    for scenario in scenarios:
        if scenario.form == "none":
            return scenario.shape
    raise PerfRefused("rebuild/perf/scenarios.yaml has no none-form scenario to settle warm passes against")


def mad(values: Sequence[float]) -> float:
    """The median absolute deviation from the median, unscaled."""
    center = statistics.median(values)
    return statistics.median(abs(value - center) for value in values)


def describe_times(values: Sequence[float]) -> str:
    if not values:
        return "no runs"
    return f"{statistics.median(values):.1f} ± {mad(values):.1f} s ({min(values):.1f}–{max(values):.1f}), n={len(values)}"


class Comparison(NamedTuple):
    verdict: str
    reason: str


def classify(
    base_runs: Sequence[float],
    candidate_runs: Sequence[float],
    *,
    base_toolchain: object = None,
    candidate_toolchain: object = None,
) -> Comparison:
    """Compare one scenario's two batches: `proven faster` when every candidate run beats every base run, `proven slower` in the mirror case, `within spread` otherwise, and `no verdict` when either batch's toolchain is unknown (it changed inside the batch) or the two differ. Complete separation of 5 runs against 5 is the exact one-sided rank test at level 1/252. Raises ValueError below `MIN_RUNS` runs a side."""
    if len(base_runs) < MIN_RUNS or len(candidate_runs) < MIN_RUNS:
        raise ValueError(f"a comparison needs at least {MIN_RUNS} runs a side")
    if base_toolchain is None or candidate_toolchain is None or base_toolchain != candidate_toolchain:
        return Comparison("no verdict", "the two batches ran under different rustc or OS builds")
    if max(candidate_runs) < min(base_runs):
        return Comparison("proven faster", "every candidate run beat every base run")
    if min(candidate_runs) > max(base_runs):
        return Comparison("proven slower", "every base run beat every candidate run")
    return Comparison("within spread", "the two batches' ranges overlap")


def classify_run(terminal_log: str, exit_code: int | None) -> str:
    """Classify one cycle pass from its `terminal.log`: `refused` (a `REFUSING TO RUN` line), `interrupted` (`CYCLE INTERRUPTED:`), `failed` (`CYCLE FAILED:`, or exit 0 without READY), `ready` (exit 0 and a `READY - adjudicate at ` line followed by `Cycle complete.`; a bare `READY` does not count, since `NOT READY` contains it), or `harness` (a nonzero exit with none of these lines: make, uv or the interpreter failed before the console opened)."""
    lines = [line.rstrip() for line in terminal_log.splitlines()]
    if any(line.startswith("REFUSING TO RUN") for line in lines):
        return "refused"
    if any(line.startswith("CYCLE INTERRUPTED:") for line in lines):
        return "interrupted"
    if any(line.startswith("CYCLE FAILED:") for line in lines):
        return "failed"
    if exit_code != 0:
        return "harness"
    ready = next((index for index, line in enumerate(lines) if line.startswith(READY_PREFIX)), None)
    if ready is not None and COMPLETE_LINE in lines[ready + 1 :]:
        return "ready"
    return "failed"


@dataclass(frozen=True)
class TimedRun:
    """One timed run as the journal check needs it: its index, its stamp's `prepared_at`, hyperfine's wall time and the clone timings-journal lines harvested for it."""

    k: int
    prepared_at: float
    time_s: float
    lines: Sequence[Mapping]


class Agreement(NamedTuple):
    ok: bool
    problems: list[str]
    wall_s: list[float]
    gaps: list[float]


def _stamp_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def journal_agreement(
    runs: Sequence[TimedRun], *, cpu_count: int, mem_total_bytes: int, gap_range_s: float = GAP_RANGE_S
) -> Agreement:
    """Check a batch against the clone's timings journal. Each run needs exactly one `kind: run` line in its window, with `exit: ok`, `--stop-server` in its argv, a plan that is not a first run, and this machine's `cpu_count` and `mem_total_bytes`; the line must start at or after `floor(prepared_at)` and finish by `prepared_at + t + 1`; its stamps must agree with its `wall_s` to a second; and each gap `t − wall_s` must be at least 0, with the batch's gaps within `gap_range_s` of each other. `wall_s` leaves out make and uv startup, planning and the readiness checklist, so the rule is a bounded, stable gap."""
    problems: list[str] = []
    walls: list[float] = []
    gaps: list[float] = []
    for run in runs:
        found = [line for line in run.lines if line.get("kind") == "run"]
        if len(found) != 1:
            problems.append(f"run {run.k}: {len(found)} run lines in its window, not one")
            continue
        line = found[0]
        plan = line.get("plan") or {}
        if line.get("exit") != "ok":
            problems.append(f"run {run.k}: the run line's exit is {line.get('exit')!r}")
        if "--stop-server" not in (line.get("argv") or []):
            problems.append(f"run {run.k}: the run line's argv lacks --stop-server")
        if plan.get("first_run") is not False:
            problems.append(f"run {run.k}: the plan is a first run")
        if line.get("cpu_count") != cpu_count or line.get("mem_total_bytes") != mem_total_bytes:
            problems.append(f"run {run.k}: the run line is from another machine")
        started = _stamp_seconds(line.get("started_at"))
        finished = _stamp_seconds(line.get("finished_at"))
        wall = line.get("wall_s")
        if started is None or finished is None or not isinstance(wall, (int, float)):
            problems.append(f"run {run.k}: the run line lacks readable stamps or wall_s")
            continue
        if started < math.floor(run.prepared_at):
            problems.append(f"run {run.k}: the run line started before the run was prepared")
        if finished > run.prepared_at + run.time_s + 1:
            problems.append(f"run {run.k}: the run line finished after the timed run ended")
        if abs((finished - started) - wall) > 1:
            problems.append(f"run {run.k}: started_at and finished_at disagree with wall_s by more than 1 s")
        gap = run.time_s - wall
        if gap < 0:
            problems.append(
                f"run {run.k}: hyperfine's time is {-gap:.1f} s shorter than the journal's wall_s"
            )
        walls.append(float(wall))
        gaps.append(gap)
    if gaps and max(gaps) - min(gaps) > gap_range_s:
        problems.append(
            f"the gaps range over {max(gaps) - min(gaps):.1f} s, more than GAP_RANGE_S ({gap_range_s:g} s)"
        )
    return Agreement(not problems, problems, walls, gaps)


def pass_shape(run_line: Mapping) -> dict:
    """A pass's shape from its run line: the compared part (`COMPARED_PLAN_FLAGS`, `promote_corpus` among them as whether the pass promotes a staged corpus, since its value is that corpus's path; each gate's `green` and `skip`; and the carry counts), the reported part (`REPORTED_PLAN_WIDTHS`), and the plan keys no list names (`new`). `IGNORED_PLAN_KEYS` are paths and HEAD."""
    plan = run_line.get("plan") or {}
    known = {*COMPARED_PLAN_FLAGS, *REPORTED_PLAN_WIDTHS, *IGNORED_PLAN_KEYS}
    gates = run_line.get("gates") or {}
    flags = {flag: plan.get(flag) for flag in COMPARED_PLAN_FLAGS}
    flags["promote_corpus"] = plan.get("promote_corpus") is not None
    return {
        "compared": {
            "plan": flags,
            "gates": {
                name: {"green": (gate or {}).get("green"), "skip": (gate or {}).get("skip")}
                for name, gate in sorted(gates.items())
            },
            "carry": run_line.get("carry"),
        },
        "reported": {width: plan.get(width) for width in REPORTED_PLAN_WIDTHS},
        "new": sorted(key for key in plan if key not in known),
    }


def _flatten(value: object, prefix: str) -> dict[str, object]:
    if isinstance(value, Mapping) and value:
        flat: dict[str, object] = {}
        for key in sorted(value):
            flat.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return flat
    return {prefix: value}


def compare_shapes(first: Mapping, second: Mapping) -> tuple[list[str], list[str]]:
    """Name every difference between two pass shapes: the compared part's, which fail, and the reported widths', which are only reported."""

    def differences(a: object, b: object) -> list[str]:
        flat_a, flat_b = _flatten(a, ""), _flatten(b, "")
        return [
            f"{key}: {json.dumps(flat_a.get(key))} → {json.dumps(flat_b.get(key))}"
            for key in sorted(set(flat_a) | set(flat_b))
            if flat_a.get(key) != flat_b.get(key)
        ]

    return differences(first["compared"], second["compared"]), differences(
        first["reported"], second["reported"]
    )


def holds_declared(run_line: Mapping, declared: Mapping) -> list[str]:
    """Return how a run line misses a declared shape, empty when it holds it. A declared shape names plan flags with their values, and gates as `ran` (`skip: null`) or `proved` (`skip: "proved"`); gates are never read from the plan flags, since a proved skip leaves `skip_conform` false."""
    misses: list[str] = []
    plan = run_line.get("plan") or {}
    for flag, wanted in (declared.get("plan") or {}).items():
        if plan.get(flag) != wanted:
            misses.append(f"plan.{flag} is {json.dumps(plan.get(flag))}, declared {json.dumps(wanted)}")
    gates = run_line.get("gates") or {}
    for gate, wanted in (declared.get("gates") or {}).items():
        expected = None if wanted == "ran" else "proved"
        if gate not in gates:
            misses.append(f"gate {gate} is missing, declared {wanted}")
        elif (gates[gate] or {}).get("skip") != expected:
            misses.append(
                f"gate {gate} has skip {json.dumps((gates[gate] or {}).get('skip'))}, declared {wanted}"
            )
    return misses


def parse_powermode(text: str | None) -> int | None:
    match = re.search(r"^\s*powermode\s+(\d+)", text or "", re.MULTILINE)
    return int(match.group(1)) if match else None


def parse_therm(text: str | None) -> list[str] | None:
    """Return `pmset -g therm`'s lines other than its "No … has been recorded" notes, which are all Apple Silicon prints while nothing is throttled; None when the command gave nothing."""
    if text is None:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line for line in lines if not re.fullmatch(r"Note: No .+ has been recorded", line)]


def parse_ac_power(text: str | None) -> bool | None:
    match = re.search(r"Now drawing from '([^']+)'", text or "")
    return None if match is None else match.group(1) == "AC Power"


def parse_int(text: str | None) -> int | None:
    stripped = (text or "").strip()
    return int(stripped) if stripped.lstrip("-").isdigit() else None


def parse_compressor_bytes(vm_stat: str | None) -> int | None:
    page = re.search(r"page size of (\d+) bytes", vm_stat or "")
    pages = re.search(r"Pages occupied by compressor:\s+(\d+)", vm_stat or "")
    return int(page.group(1)) * int(pages.group(1)) if page and pages else None


def parse_swap_used(text: str | None) -> str | None:
    match = re.search(r"used = (\S+)", text or "")
    return match.group(1) if match else None


def python_entry(argv: Sequence[str]) -> str | None:
    """The dotted name a Python command line runs: its `-m` module, or its script's path with `/` read as `.` and without `.py`; None for `-c` code or no entry point."""
    rest = iter(argv[1:])
    for token in rest:
        if token == "-m":
            return next(rest, None)
        if token.startswith("-m"):
            return token[2:]
        if token.startswith("-c"):
            return None
        if token in PYTHON_OPTIONS_WITH_ARGUMENT:
            next(rest, None)
        elif not token.startswith("-"):
            return token.removesuffix(".py").replace("/", ".")
    return None


def heavy_in(command: str) -> set[str]:
    """Which heavy-work names one `ps` command line runs: `HEAVY_EXECUTABLES` by the executable's own name, and `HEAVY_MODULES` by a Python interpreter's entry point (`python_entry`). A name that only appears in an argument, such as a file a shell, a search or an editor was given, does not count."""
    argv = command.split()
    if not argv:
        return set()
    executable = argv[0].rsplit("/", 1)[-1]
    found = {name for name in HEAVY_EXECUTABLES if executable == name}
    if PYTHON_EXECUTABLE.fullmatch(executable):
        entry = python_entry(argv)
        if entry:
            found.update(name for name in HEAVY_MODULES if entry == name or entry.endswith(f".{name}"))
    return found


def heavy_names(ps_text: str | None, exclude_pid: int) -> list[str]:
    """Return which heavy-work names (`heavy_in`) some process outside `exclude_pid`'s own tree runs, by name only. `ps -axo pid=,ppid=,command=` is the input."""
    processes: dict[int, tuple[int, str]] = {}
    for line in (ps_text or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            processes[int(parts[0])] = (int(parts[1]), parts[2])
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in processes.items():
        children.setdefault(ppid, []).append(pid)
    own = {exclude_pid}
    frontier = [exclude_pid]
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in own:
                own.add(child)
                frontier.append(child)
    parent = processes.get(exclude_pid, (0, ""))[0]
    while parent > 1 and parent not in own:
        own.add(parent)
        parent = processes.get(parent, (0, ""))[0]
    found: set[str] = set()
    for pid, (_, command) in processes.items():
        if pid not in own:
            found |= heavy_in(command)
    return sorted(found)


def summarize_probes(raw: Mapping) -> dict:
    """Reduce `perf_clone.collect_probes`' raw outputs to what a stamp records: names and figures, never process command lines or raw probe output. `unreadable` names each of `UNREADABLE_PROBES` whose command gave no output, which every parse would otherwise read as nothing to wait for."""
    return {
        "unreadable": [name for name in UNREADABLE_PROBES if raw.get(name) is None],
        "at": raw.get("at"),
        "powermode": parse_powermode(raw.get("pmset")),
        "therm": parse_therm(raw.get("therm")),
        "pressure_level": parse_int(raw.get("pressure")),
        "compressor_bytes": parse_compressor_bytes(raw.get("vm_stat")),
        "swap_used": parse_swap_used(raw.get("swapusage")),
        "ac_power": parse_ac_power(raw.get("batt")),
        "pass_lock": raw.get("pass_lock"),
        "standing_daemon": bool(raw.get("standing_daemon")),
        "heavy": heavy_names(raw.get("ps"), int(raw.get("exclude_pid") or os.getpid())),
        "main_journal_size": raw.get("main_journal_size"),
        "load_1m": raw.get("load_1m"),
        "server": raw.get("server"),
        "disk_free": raw.get("disk_free"),
        "toolchain": dict(raw.get("toolchain") or {}),
        "os_build": raw.get("os_build"),
    }


class Outcome(NamedTuple):
    check: str
    action: str
    reason: str


def probe_outcomes(summary: Mapping, *, phase: str, before: Mapping | None = None) -> list[Outcome]:
    """What each probe means in a phase: `prepare` (before a timed run) and `driver` (before any other pass) refuse, wait or pass; `conclude` (after a timed run) marks the run `disturbed` for the same conditions, minus the server and the refusals, plus a main-checkout timings journal that grew since `before` was taken. The load average is recorded only."""
    outcomes: list[Outcome] = []
    after_run = phase == "conclude"
    held = "disturbed" if after_run else "wait"

    if not after_run:
        toolchain = summary.get("toolchain") or {}
        missing = sorted(name for name, version in toolchain.items() if version is None)
        if missing:
            outcomes.append(Outcome("toolchain", "refuse", f"missing: {', '.join(missing)}"))
        hyperfine = re.match(r"hyperfine (\d+)\.", toolchain.get("hyperfine") or "")
        if toolchain.get("hyperfine") is not None and (hyperfine is None or hyperfine.group(1) != "2"):
            outcomes.append(
                Outcome("toolchain", "refuse", f"hyperfine is {toolchain.get('hyperfine')}, not 2.x")
            )
        free = summary.get("disk_free")
        if isinstance(free, int) and free < DISK_FLOOR_BYTES:
            outcomes.append(
                Outcome(
                    "disk", "refuse", f"{format_gb(free)} GB free, under {format_gb(DISK_FLOOR_BYTES)} GB"
                )
            )
        server = summary.get("server")
        if server not in ("foreign", "none"):
            outcomes.append(
                Outcome("server", "wait", f"the review server probes as {server}, not foreign or none")
            )

    if summary.get("unreadable"):
        outcomes.append(Outcome("unreadable", held, f"could not read {', '.join(summary['unreadable'])}"))
    if summary.get("powermode") == 1:
        outcomes.append(Outcome("powermode", held, "Low Power Mode is on"))
    therm = summary.get("therm")
    if therm:
        outcomes.append(Outcome("therm", held, f"thermal state: {'; '.join(therm)}"))
    if summary.get("pressure_level") != 1:
        outcomes.append(
            Outcome("pressure", held, f"memory pressure level is {summary.get('pressure_level')}, not 1")
        )
    if summary.get("ac_power") is not True:
        outcomes.append(Outcome("ac_power", held, "not on AC power"))
    if summary.get("pass_lock") is not None:
        outcomes.append(
            Outcome("heavy", held, f"a pass holds this checkout's pass lock ({summary.get('pass_lock')})")
        )
    if summary.get("standing_daemon"):
        outcomes.append(Outcome("heavy", held, "the standing daemon answers"))
    if summary.get("heavy"):
        outcomes.append(Outcome("heavy", held, f"heavy work runs: {', '.join(summary['heavy'])}"))
    if after_run and before is not None:
        grew = (summary.get("main_journal_size") or 0) > (before.get("main_journal_size") or 0)
        if grew:
            outcomes.append(
                Outcome("heavy", "disturbed", "this checkout's timings journal grew during the run")
            )
    return outcomes


def toolchain_key(summary: Mapping | None) -> tuple[str | None, str | None] | None:
    if not summary:
        return None
    return ((summary.get("toolchain") or {}).get("rustc"), summary.get("os_build"))


def is_harness_path(path: str) -> bool:
    return any(path == entry or (entry.endswith("/") and path.startswith(entry)) for entry in HARNESS_PATHS)


def _git(repo: Path, *args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=perf_clone.git_env(env)
    )


class PatchCheck(NamedTuple):
    """Which patches fit at a base: per scenario, None when the check passed and otherwise git's complaint. `reverses` and `reapplies` cover the revert-form patches; `composes` says whether all the reversible ones reversed one after another; `forwards` covers forward-form patches applied at the base; `candidate` is git's complaint when the candidate itself did not apply."""

    reverses: dict[str, str | None]
    composes: bool
    reapplies: dict[str, str | None]
    forwards: dict[str, str | None]
    candidate: str | None


def patches_apply_at(
    repo: Path,
    base: str,
    reverts: Mapping[str, Path],
    index: Path,
    *,
    forwards: Mapping[str, Path] | None = None,
    candidate: Path | None = None,
    exclude: Sequence[str] = (),
) -> PatchCheck:
    """Check the scenario patches at `base` (plus `candidate`, less the paths `exclude`'s `git apply --exclude` patterns match, when given) on the temporary index `index`, never the repository's own index or work tree: each revert-form patch must reverse (`git apply --cached -R --check`), all of them reversed one after another (the composite), and each re-applied on the composite (`git apply --cached --check`); a forward-form patch must apply at the base."""
    env = {"GIT_INDEX_FILE": str(index.resolve())}
    index.parent.mkdir(parents=True, exist_ok=True)
    perf_clone.remove(index)

    def apply(*args: str) -> str | None:
        result = _git(repo, "apply", "--cached", *args, env=env)
        return None if result.returncode == 0 else (result.stderr.strip() or "git apply failed")

    def reset() -> str | None:
        result = _git(repo, "read-tree", base, env=env)
        if result.returncode != 0:
            raise PerfRefused(f"{base} does not resolve in {repo}: {result.stderr.strip()}")
        if candidate is None:
            return None
        return apply(*(f"--exclude={pattern}" for pattern in exclude), str(candidate))

    try:
        failure = reset()
        if failure is not None:
            return PatchCheck({}, False, {}, {}, failure)
        reverses = {name: apply("-R", "--check", str(patch)) for name, patch in reverts.items()}
        composes = True
        for name, patch in reverts.items():
            if reverses[name] is None and apply("-R", str(patch)) is not None:
                composes = False
        reapplies = {
            name: apply("--check", str(patch)) if reverses[name] is None else "it does not reverse"
            for name, patch in reverts.items()
        }
        forward_status: dict[str, str | None] = {}
        for name, patch in (forwards or {}).items():
            reset()
            forward_status[name] = apply("--check", str(patch))
        return PatchCheck(reverses, composes, reapplies, forward_status, None)
    finally:
        perf_clone.remove(index)


def revert_status(check: PatchCheck, name: str) -> str | None:
    """The reason a revert-form scenario is not applicable at the base, or None when it is."""
    if check.candidate is not None:
        return f"not applicable: the candidate does not apply at B ({check.candidate})"
    if check.reverses.get(name) is not None:
        return f"not applicable at B: re-aim (its edit does not reverse: {check.reverses[name]})"
    if not check.composes:
        return "not applicable at B: re-aim (the revert-form edits do not compose)"
    if check.reapplies.get(name) is not None:
        return f"not applicable at B: re-aim (its edit does not re-apply on the composite: {check.reapplies[name]})"
    return None


def forward_status(
    repo: Path,
    paths: Iterable[str],
    *,
    letter_base: str,
    base: str,
    candidate: Path | None = None,
    index: Path | None = None,
) -> str | None:
    """Why a forward-form scenario at its own fixed base cannot take the candidate, or None when it can: every candidate path but `HARNESS_PATHS` and Markdown must be unchanged between that base and B, and, when `candidate` (the patch) and `index` (a temporary index) are given, the candidate less those paths (`FORWARD_EXCLUDES`) must apply at that base."""
    relevant = sorted({path for path in paths if not is_harness_path(path) and not path.endswith(".md")})
    if relevant:
        result = _git(repo, "diff", "-z", "--name-only", "--no-renames", letter_base, base, "--", *relevant)
        if result.returncode != 0:
            raise PerfRefused(f"git diff {letter_base} {base} failed: {result.stderr.strip()}")
        changed = [path for path in result.stdout.split("\0") if path]
        if changed:
            return f"not measured: {', '.join(changed)} changed since {letter_base[:8]}"
    if candidate is not None and index is not None:
        check = patches_apply_at(repo, letter_base, {}, index, candidate=candidate, exclude=FORWARD_EXCLUDES)
        if check.candidate is not None:
            return f"not measured: the candidate does not apply at {letter_base[:8]} ({check.candidate})"
    return None


def write_patch(repo: Path, scenario: Scenario) -> Path:
    """Rewrite a scenario's patch in place from its `edit:` (`git diff --binary <commit>^ <commit> -- <paths>`). Refuses, leaving the checked-in patch alone, when the edit is missing, does not resolve, or changes none of its paths."""
    if scenario.form == "none" or scenario.commit is None or scenario.patch is None:
        raise PerfRefused(f"{scenario.name} has no edit to write a patch from")
    commit = _git(repo, "rev-parse", "--verify", "--quiet", f"{scenario.commit}^{{commit}}")
    parent = _git(repo, "rev-parse", "--verify", "--quiet", f"{scenario.commit}^")
    if commit.returncode != 0 or parent.returncode != 0:
        raise PerfRefused(
            f"{scenario.name}: edit {scenario.commit} does not resolve in this repository, so {scenario.patch} is kept as it is"
        )
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--binary",
            parent.stdout.strip(),
            commit.stdout.strip(),
            "--",
            *scenario.paths,
        ],
        capture_output=True,
        env=perf_clone.git_env(),
    )
    if diff.returncode != 0 or not diff.stdout:
        raise PerfRefused(f"{scenario.name}: {scenario.commit} changes none of the edit's paths")
    scenario.patch.parent.mkdir(parents=True, exist_ok=True)
    scenario.patch.write_bytes(diff.stdout)
    return scenario.patch


class FingerprintDiff(NamedTuple):
    differences: list[str]
    stores: list[str]
    not_comparable: list[str]


def compare_fingerprints(base: Mapping, candidate: Mapping, *, same_seed: bool = True) -> FingerprintDiff:
    """Name every difference between two `ams-perf-fingerprint/1` documents: `tables.<config>`, `fonts.<path>` and `corpus.<file>` (differs, or missing on one side), `verdicts`, `complaints` and `review_facts_pins`, with the stores apart. Without the same seed, `verdicts` and `complaints` are not comparable rather than different."""
    differences: list[str] = []

    def keyed(section: str, first: Mapping, second: Mapping) -> None:
        for key in sorted(set(first) | set(second)):
            if key not in first:
                differences.append(f"{section}.{key}: missing on the base side")
            elif key not in second:
                differences.append(f"{section}.{key}: missing on the candidate side")
            elif first[key] != second[key]:
                differences.append(f"{section}.{key} differs")

    keyed("tables", base.get("tables") or {}, candidate.get("tables") or {})
    keyed("fonts", base.get("fonts") or {}, candidate.get("fonts") or {})
    keyed(
        "corpus",
        (base.get("corpus") or {}).get("files") or {},
        (candidate.get("corpus") or {}).get("files") or {},
    )
    not_comparable: list[str] = []
    for key in ("verdicts", "complaints"):
        if base.get(key) != candidate.get(key):
            (differences if same_seed else not_comparable).append(
                key if same_seed else f"{key}: not comparable: different seeds"
            )
    if base.get("review_facts_pins") != candidate.get("review_facts_pins"):
        differences.append("review_facts_pins")
    stores = [
        f"stores.{name}: an intermediate store differs"
        for name in sorted(set(base.get("stores") or {}) | set(candidate.get("stores") or {}))
        if (base.get("stores") or {}).get(name) != (candidate.get("stores") or {}).get(name)
    ]
    return FingerprintDiff(differences, stores, not_comparable)


def leading_markers(note: str) -> Iterator[tuple[str, str]]:
    """Each marker in a note's leading marker run (`verdict_notes`' marker grammar) as its kind and its text inside the brackets."""
    position = 0
    while (match := _MARKER.match(note, position)) is not None:
        yield match.group(2), match.group(1)[1:-1]
        position = match.end()


def standing_rule_ids(note: str) -> set[str]:
    """The rule ids the `[standing: …]` markers in a note's leading marker run name: one id for a single fill, several joined by `+` for a combined fill, and the source's markers after a `[duplicate-fill from …]` copy."""
    ids: set[str] = set()
    for kind, text in leading_markers(note):
        if kind == "standing":
            ids.update(part.strip() for part in text.partition(":")[2].split("+") if part.strip())
    return ids


def duplicate_sources(note: str) -> set[str]:
    """The units the `[duplicate-fill from …]` markers in a note's leading marker run copied from."""
    return {
        text.partition(" from ")[2].strip()
        for kind, text in leading_markers(note)
        if kind == "duplicate-fill" and " from " in text
    }


def scrub_seed(store: Mapping, rule_ids: Iterable[str]) -> tuple[dict, int]:
    """Drop every record whose leading markers name a fill of one of `rule_ids`, then every record a duplicate fill copied from a dropped unit, repeated until none is left, since a copy of a copy keeps only two markers (`verdict_notes.cap_markers`) and can lose the standing one. Return the scrubbed store and how many records it dropped. Every other record, fills of other rules included, stays."""
    rules = set(rule_ids)
    records = list(store.get("verdicts") or [])
    dropped = {
        index for index, record in enumerate(records) if standing_rule_ids(record.get("note") or "") & rules
    }
    while True:
        units = {records[index].get("unit") for index in dropped}
        copies = {
            index
            for index, record in enumerate(records)
            if index not in dropped and duplicate_sources(record.get("note") or "") & units
        }
        if not copies:
            break
        dropped |= copies
    kept = [record for index, record in enumerate(records) if index not in dropped]
    return {**store, "verdicts": kept}, len(dropped)


def changed_rule_ids(post_text: str, root_text: str) -> set[str]:
    """The ids of the standing rules in `post_text` that `root_text` lacks or holds differently, each rule compared whole as canonical JSON. A rule changed in place fills other units, so its fills are scrubbed too; a rule whose prose alone changed is scrubbed as well, and the root warm's standing fill refills what it matches."""

    def records(text: str) -> dict[str, str]:
        document = yaml.safe_load(text) or {}
        return {
            str(rule["id"]): json.dumps(rule, sort_keys=True, default=str)
            for rule in document.get("rules") or []
            if isinstance(rule, Mapping) and "id" in rule
        }

    post, root = records(post_text), records(root_text)
    return {rule_id for rule_id, record in post.items() if root.get(rule_id) != record}


def shape_medians(
    journal: Path, scenarios: Sequence[Scenario], *, cpu_count: int, mem_total_bytes: int
) -> dict[str, float | None]:
    """Each scenario's median `wall_s` over the last `ESTIMATE_RECENT` green, non-first-run passes on this machine whose run line holds its declared shape, or None when the journal has none."""
    runs, _, order = cycle_timings.load_journal(journal)
    green = [
        runs[run_id]
        for run_id in order
        if run_id in runs
        and runs[run_id].get("exit") == "ok"
        and runs[run_id].get("cpu_count") == cpu_count
        and runs[run_id].get("mem_total_bytes") == mem_total_bytes
        and not (runs[run_id].get("plan") or {}).get("first_run")
        and isinstance(runs[run_id].get("wall_s"), (int, float))
    ]
    medians: dict[str, float | None] = {}
    for scenario in scenarios:
        walls = [float(run["wall_s"]) for run in green if not holds_declared(run, scenario.shape)]
        recent = walls[-ESTIMATE_RECENT:]
        medians[scenario.name] = statistics.median(recent) if recent else None
    return medians


def estimate(
    medians: Mapping[str, float | None],
    scenarios: Sequence[Scenario],
    *,
    runs: int,
    fresh_clones: int,
    fault_share: float,
) -> tuple[list[tuple[str, float]], float]:
    """Estimate a check's wall time from the journal medians (`shape_medians`) plus `PASS_OVERHEAD_S` a pass: per scenario, two sides of one warm-up and `runs` timed passes, a warm of each start state (a pass of the heaviest shape in `medians`, which covers every scenario, not only the selected ones, since each start state warms from the composite root, then a settling pass of the lightest), and a fingerprint each; each side's root warm; a cold bootstrap per missing clone; and the fault kit, `fault_share` of it, the share of its rows selected. The pass times are this machine's journal medians; a fingerprint (`FINGERPRINT_S`), a cold first pass (`COLD_FIRST_PASS_S`), the fault kit over every row (`FAULT_KIT_S`) and a pass of a shape the journal lacks (`FALLBACK_PASS_S`) are fixed planning figures, measured on the 18-core M5 Pro MacBook Pro, since no journal records them. Every number is an estimate for planning, never a measurement."""
    known = [value for value in medians.values() if value is not None]
    fallback = max(known) if known else FALLBACK_PASS_S

    def pass_s(name: str) -> float:
        value = medians.get(name)
        return (fallback if value is None else value) + PASS_OVERHEAD_S

    heavy = (max(known) if known else FALLBACK_PASS_S) + PASS_OVERHEAD_S
    settle = (min(known) if known else FALLBACK_PASS_S) + PASS_OVERHEAD_S
    items: list[tuple[str, float]] = []
    for scenario in scenarios:
        timed = 2 * (1 + runs) * pass_s(scenario.name)
        warms = 2 * (settle if scenario.form == "forward" else heavy + settle)
        items.append(
            (
                f"{scenario.name}: 2 × {1 + runs} timed passes, start warms, fingerprints",
                timed + warms + 2 * FINGERPRINT_S,
            )
        )
    if any(scenario.form != "forward" for scenario in scenarios) or fault_share:
        items.append(("shared clone: both sides' root warms", 2 * (heavy + settle)))
    if any(scenario.form == "forward" for scenario in scenarios):
        items.append(("letter-migration clone: both sides' root warms", 2 * settle))
    if fresh_clones:
        items.append(
            (f"{fresh_clones} cold bootstrap(s)", fresh_clones * (COLD_FIRST_PASS_S + heavy + settle))
        )
    if fault_share:
        items.append(("fault kit", FAULT_KIT_S * fault_share))
    return items, sum(seconds for _, seconds in items)


def fault_selection(faults: str, *, baseline: bool) -> str:
    """The fault kit's `--rows` for a `--faults` value: under `--baseline` there are no candidate paths for `auto` to select by, so `auto` means every row there; a named selection is passed through."""
    return "all" if baseline and faults == "auto" else faults


def machine_tag(cpu_count: int, mem_total_bytes: int) -> str:
    return f"cpus={cpu_count} ram={format_gb(mem_total_bytes)}GB"


def merge_machine_row(document: Mapping, row: Mapping) -> dict:
    """Replace this machine's row (keyed by `cpu_count` and `mem_total_bytes`) in a baseline document, keeping every other machine's."""
    key = (row["cpu_count"], row["mem_total_bytes"])
    others = [
        entry
        for entry in document.get("machines") or []
        if (entry.get("cpu_count"), entry.get("mem_total_bytes")) != key
    ]
    return {**document, "machines": [*others, dict(row)]}


def machine_differences(document: Mapping, row: Mapping) -> list[str]:
    """Name every difference between a new baseline row's fingerprint and each other machine's row in `document` at the same base: `compare_fingerprints` with that row as the base side, its intermediate stores included, and the verdict store and complaint list compared only when the two rows' seeds match. Committed fingerprints are machine-independent given the same base and seed, so any difference is a determinism finding."""
    key = (row["cpu_count"], row["mem_total_bytes"])
    found: list[str] = []
    for other in document.get("machines") or []:
        if (other.get("cpu_count"), other.get("mem_total_bytes")) == key or other.get("base") != row["base"]:
            continue
        same_seed = other.get("seed_sha256") is not None and other.get("seed_sha256") == row["seed_sha256"]
        diff = compare_fingerprints(other.get("fingerprint") or {}, row["fingerprint"], same_seed=same_seed)
        tag = machine_tag(int(other.get("cpu_count") or 0), int(other.get("mem_total_bytes") or 0))
        found += [f"{item} (base side: the {tag} row)" for item in [*diff.differences, *diff.stores]]
    return found


def probe_record(system_log: Path) -> dict[str, str]:
    """Each of `PROBE_CHECKS` as a batch's baseline row records it, from the batch's `system.ndjson`: `waited` when some probe in the batch waited on it or marked a run disturbed by it, else `ok`."""
    held = {
        str(check)
        for line in perf_clone.journal_lines(system_log, 0)[1]
        for check, action, *_ in line.get("outcomes") or []
        if action in ("wait", "disturbed")
    }
    return {check: "waited" if check in held else "ok" for check in PROBE_CHECKS}


def empty_baseline(scenario: Scenario) -> dict:
    digest = file_sha256(scenario.patch) if scenario.patch and scenario.patch.is_file() else None
    return {"format": BASELINE_FORMAT, "scenario": scenario.name, "patch_sha256": digest, "machines": []}


@dataclass(frozen=True)
class Candidate:
    """What a check measures: the record id (`c-<12 hex of the patch's sha256>`, or `baseline`), the base commit B, the patch bytes and the paths it changes (both sides of a rename)."""

    id: str
    base: str
    patch: bytes | None
    paths: tuple[str, ...]
    source: str


def capture_candidate(main: Path, *, spec: str, baseline: bool, base: str | None) -> Candidate:
    def rev(name: str) -> str:
        result = _git(main, "rev-parse", "--verify", f"{name}^{{commit}}")
        if result.returncode != 0:
            raise PerfRefused(f"{name} does not resolve: {result.stderr.strip()}")
        return result.stdout.strip()

    def listed(*args: str) -> list[str]:
        result = _git(main, *args)
        if result.returncode != 0:
            raise PerfRefused(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return [path for path in result.stdout.split("\0") if path]

    if baseline:
        return Candidate("baseline", rev(base or "HEAD"), None, (), "baseline")
    if spec == "worktree":
        untracked = listed("ls-files", "-z", "--others", "--exclude-standard")
        if untracked:
            raise PerfRefused(
                "untracked, unignored files would be left out of the candidate: "
                + ", ".join(untracked)
                + " (`git add -N <path>` takes one in)"
            )
        start, end = rev("HEAD"), None
    elif ".." in spec:
        first, _, second = spec.partition("..")
        start, end = rev(first), rev(second)
    else:
        raise PerfRefused(f"--candidate takes worktree or A..B, not {spec!r}")
    span = [start] if end is None else [start, end]
    patch = subprocess.run(
        ["git", "-C", str(main), "diff", "--binary", *span],
        capture_output=True,
        check=True,
        env=perf_clone.git_env(),
    ).stdout
    if not patch:
        raise PerfRefused(f"the candidate ({spec}) changes nothing")
    paths = listed("diff", "-z", "--name-only", "--no-renames", *span)
    return Candidate(
        f"c-{hashlib.sha256(patch).hexdigest()[:12]}", start, patch, tuple(sorted(set(paths))), spec
    )


def committer_time_utc(repo: Path, commit: str) -> str:
    seconds = int(_git(repo, "show", "-s", "--format=%ct", commit).stdout.strip())
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class Side:
    id: str
    patch: Path | None


@dataclass(frozen=True)
class Seed:
    path: Path
    sha256: str
    as_of: str | None
    dropped: int
    rules: tuple[str, ...]
    scratch: bool = False


@dataclass
class Batch:
    """One side's batch of one scenario as read back from its record directory."""

    side: str
    record: Path
    outcome: str
    detail: str = ""
    times: list[float] = field(default_factory=list)
    stamps: list[dict] = field(default_factory=list)
    lines: list[list[dict]] = field(default_factory=list)
    toolchain: object = None

    def run_lines(self) -> list[dict]:
        return [run for lines in self.lines[1:] for run in lines if run.get("kind") == "run"]


def read_batch(record: Path, *, runs: int) -> Batch:
    """Read a batch back from `<record>`: `prepare-refused` when a prepare hook refused, the first run's non-ready classification or `disturbed`, `short` when hyperfine stopped without a reason, `harness` when a run exited nonzero, else `ok`."""
    side = record.name
    refused = record / "refused.json"
    if refused.is_file():
        return Batch(side, record, "prepare-refused", json.loads(refused.read_text()).get("reason", ""))
    run_dirs = sorted(
        (
            (path for path in (record / "runs").iterdir() if path.name.isdigit())
            if (record / "runs").is_dir()
            else []
        ),
        key=lambda path: int(path.name),
    )
    stamps = [json.loads((path / "stamp.json").read_text(encoding="utf-8")) for path in run_dirs]
    lines = [perf_clone.journal_lines(path / "journal.ndjson", 0)[1] for path in run_dirs]
    batch = Batch(side, record, "ok", stamps=stamps, lines=lines)
    for stamp in stamps:
        if stamp.get("disturbed"):
            batch.outcome, batch.detail = "disturbed", f"run {stamp['k']}: {stamp['disturbed']}"
            return batch
        classification = stamp.get("classification")
        if classification != "ready":
            batch.outcome = classification or "harness"
            log = record / "runs" / str(stamp["k"]) / "logs" / "terminal.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
            batch.detail = f"run {stamp['k']} ended {batch.outcome}" + perf_clone.log_tail(text)
            return batch
    export = record / "hyperfine.json"
    measurements = []
    if export.is_file() and export.stat().st_size:
        results = json.loads(export.read_text(encoding="utf-8")).get("results") or []
        measurements = results[0].get("measurements") or [] if results else []
    batch.times = [float(item["time_wall_clock"]["value"]) for item in measurements]
    if len(batch.times) != runs or len(stamps) != runs + 1:
        batch.outcome, batch.detail = (
            "short",
            f"{len(batch.times)} timed runs and {len(stamps)} stamps for {runs} runs",
        )
        return batch
    if any(item.get("exit_code") != 0 for item in measurements):
        batch.outcome, batch.detail = "harness", "a timed run exited nonzero"
        return batch
    keys = {toolchain_key(stamp.get(probes)) for stamp in stamps for probes in ("probes", "conclude_probes")}
    batch.toolchain = keys.pop() if len(keys) == 1 else None
    return batch


def root_pass(result: perf_clone.PassResult) -> dict:
    """One root-warm pass as the report keeps it: how it ended (`green, not ready` for a green pass without READY, as a bootstrap's first-run pass is), its wall seconds, and its carry counts with the orphan share (orphaned over human), which the carry itself refuses above `landing.ORPHAN_SHARE_LIMIT`."""
    carry = (result.run_line or {}).get("carry") or {}
    human, orphaned = carry.get("human"), carry.get("orphaned")
    share = orphaned / human if isinstance(human, int) and human and isinstance(orphaned, int) else None
    green_not_ready = result.classification == "failed" and result.returncode == 0
    return {
        "classification": "green, not ready" if green_not_ready else result.classification,
        "elapsed_s": round(result.elapsed_s, 1),
        "carry": carry or None,
        "orphan_share": share,
    }


def disk_use(perf_dir: Path) -> dict[str, int]:
    """The bytes each clone and snapshot directory under `perf_dir` takes, from `du -sk`."""
    sizes: dict[str, int] = {}
    for entry in sorted(perf_dir.iterdir()) if perf_dir.is_dir() else []:
        if entry.is_dir():
            result = subprocess.run(["du", "-sk", str(entry)], capture_output=True, text=True)
            fields = result.stdout.split()
            if result.returncode == 0 and fields and fields[0].isdigit():
                sizes[entry.name] = int(fields[0]) * 1024
    return sizes


def critical_path_summary(batch: Batch) -> str:
    steps: dict[str, list[dict]] = {}
    runs: dict[str, dict] = {}
    for lines in batch.lines[1:]:
        for line in lines:
            run_id = line.get("run")
            if not isinstance(run_id, str):
                continue
            if line.get("kind") == "run":
                runs[run_id] = line
            elif line.get("kind") == "step":
                steps.setdefault(run_id, []).append(line)
    ends: Counter[str] = Counter()
    for run_id, run in runs.items():
        path = cycle_timings.critical_path(run, steps.get(run_id, []))
        if path is not None:
            ends[path["last"]] += 1
    return ", ".join(f"{name} ({count}/{len(runs)})" for name, count in ends.most_common()) or "-"


def hyperfine_argv(
    *, runs: int, name: str, prepare: list[str], conclude: list[str], export: Path, command: list[str]
) -> list[str]:
    """hyperfine's argv for one batch. Hooks always run under a shell, so each is one shell-quoted string; `--ignore-failure` lets `conclude` harvest and classify a failed run, and its own exit 1 is what stops the batch."""
    return [
        "hyperfine",
        "--shell=none",
        "--style",
        "basic",
        "--warmup",
        "1",
        "--runs",
        str(runs),
        "--ignore-failure",
        "--command-name",
        name,
        "--prepare",
        shlex.join(prepare),
        "--conclude",
        shlex.join(conclude),
        "--export-json",
        str(export),
        shlex.join(command),
    ]


class Stopped(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(f"stopped by signal {signum}")
        self.signum = signum


class Check:
    """One paired check (the module docstring says what it does, in order)."""

    def __init__(
        self,
        args: argparse.Namespace,
        scenarios: Sequence[Scenario],
        candidate: Candidate,
        *,
        main: Path = ROOT,
    ):
        self.args = args
        self.main = main
        self.all_scenarios = list(scenarios)
        self.scenarios = select_scenarios(scenarios, args.scenario)
        self.candidate = candidate
        self.base = candidate.base
        self.record = KEEP_DIR / f"{utc_stamp()}-{candidate.id}"
        self.settle = settle_shape(scenarios)
        self.stopping: int | None = None
        self.results: list[dict] = []
        self.faults: dict | None = None
        self.cpu_count = os.cpu_count() or 0
        self.mem_total_bytes = memory_budget.total_memory_bytes()
        self.seeds: dict[str, Seed] = {}
        self.roots: dict[str, list[dict]] = {}
        candidate_side = (
            Side("aa", None)
            if candidate.patch is None
            else Side(candidate.id, self.record / "candidate.patch")
        )
        self.sides = (Side("base", None), candidate_side)

    def clone_path(self, name: str) -> Path:
        return PERF_DIR / name

    def log(self, name: str) -> Path:
        return self.record / "logs" / name

    def say(self, text: str) -> None:
        print(f"[perf] {text}", flush=True)

    def stop_requested(self, signum: int, _frame: object) -> None:
        if self.stopping is None:
            self.stopping = signum
            self.say(f"signal {signum}: stopping after the current step")

    def check_stop(self) -> None:
        if self.stopping is not None:
            raise Stopped(self.stopping)

    def wait_quiet(self, clone: Path | None) -> dict:
        summary, waited = perf_clone.wait_until_quiet(
            self.main,
            clone,
            log=self.record / "system.ndjson",
            phase="driver",
            stop=lambda: self.stopping is not None,
        )
        if waited:
            self.say(f"waited {waited:.0f}s for the machine to be quiet")
        self.check_stop()
        return summary

    def run(self) -> int:
        self.record.mkdir(parents=True)
        if self.candidate.patch is not None:
            (self.record / "candidate.patch").write_bytes(self.candidate.patch)
        (self.record / "plan.json").write_text(
            json.dumps(
                {
                    "base": self.base,
                    "candidate": self.candidate.id,
                    "source": self.candidate.source,
                    "paths": list(self.candidate.paths),
                    "scenarios": [scenario.name for scenario in self.scenarios],
                    "runs": self.args.runs,
                    "faults": self.fault_rows(),
                    "machine": machine_tag(self.cpu_count, self.mem_total_bytes),
                },
                indent=1,
            )
            + "\n"
        )
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            if signal.getsignal(signum) != signal.SIG_IGN:
                signal.signal(signum, self.stop_requested)
        perf_clone.install_shims(self.main)
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        try:
            self.wait_quiet(None)
            self.run_shared()
            self.run_letter_migration()
            self.run_faults()
            self.finish_shared()
            self.check_stop()
            return self.report()
        except Stopped as stop:
            return self.stopped(stop.signum)
        except (PerfRefused, subprocess.CalledProcessError) as failure:
            if self.stopping is not None:
                return self.stopped(self.stopping)
            if not isinstance(failure, PerfRefused):
                raise
            self.results.append({"name": "check", "status": "refused", "detail": str(failure)})
            self.report()
            return 2
        finally:
            awake.terminate()

    def stopped(self, signum: int) -> int:
        """End a check a stop signal reached: a step it interrupted can end in any failure, so this path is the only one a stop takes. Wait until no land holds a clone's pass lock, report, and return 128 plus the signal's number."""
        for name in (
            SHARED_CLONE,
            *(self.letter_clone_name(scenario) for scenario in self.forward_scenarios()),
        ):
            clone = self.clone_path(name)
            if clone.exists():
                perf_clone.wait_for_pass_lock(clone)
        self.results.append({"name": "check", "status": "stopped", "detail": f"stopped by signal {signum}"})
        self.report()
        return 128 + signum

    def forward_scenarios(self) -> list[Scenario]:
        return [scenario for scenario in self.scenarios if scenario.form == "forward"]

    def shared_scenarios(self) -> list[Scenario]:
        return [scenario for scenario in self.scenarios if scenario.form != "forward"]

    def revert_patches(self) -> dict[str, Path]:
        return {s.name: s.patch for s in self.all_scenarios if s.form == "revert" and s.patch is not None}

    def letter_clone_name(self, scenario: Scenario) -> str:
        return (scenario.base or "letter")[:8]

    def ensure_clone(self, name: str, base: str) -> Path:
        """Reuse a bootstrapped clone, removing any remote it has, or bootstrap it, replacing whatever a failed or stopped bootstrap left there; then fetch this checkout's HEAD and branches into it."""
        clone = self.clone_path(name)
        if perf_clone.bootstrapped(clone):
            perf_clone.remove_remotes(clone)
        else:
            self.say(f"bootstrapping var/perf/{name} at {base[:12]}")
            record = perf_clone.bootstrap(clone, base, main=self.main, log=self.log(f"bootstrap-{name}.log"))
            KEEP_DIR.mkdir(parents=True, exist_ok=True)
            (KEEP_DIR / f"bootstrap-{name}-{utc_stamp()}.json").write_text(
                json.dumps(record, indent=1) + "\n"
            )
        perf_clone.fetch(clone, main=self.main)
        if _git(clone, "cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
            raise PerfRefused(f"{base} is not in var/perf/{name} after the fetch")
        return clone

    def fault_rows(self) -> str:
        return fault_selection(self.args.faults, baseline=self.candidate.patch is None)

    def run_shared(self) -> None:
        if not self.shared_scenarios() and self.fault_rows() == "none":
            return
        clone = self.ensure_clone(SHARED_CLONE, self.base)
        reverts = self.revert_patches()
        as_is = [s.name for s in self.all_scenarios if s.form == "none"]
        status = {
            side.id: perf_clone.build_refs(
                clone, side.id, self.base, candidate=side.patch, reverts=reverts, forwards={}, as_is=as_is
            )
            for side in self.sides
        }
        at_base = patches_apply_at(clone, self.base, reverts, clone / ".git" / "perf-check.index")
        snaps = PERF_DIR / f"{SHARED_CLONE}.snap"
        seed = self.base_seed(clone)
        try:
            self.prepare_root(clone, self.sides[0], snaps / "root", snaps / "base" / "root", seed)
        finally:
            if seed.scratch:
                perf_clone.remove(seed.path)
        self.prepare_root(
            clone, self.sides[1], snaps / "base" / "root", snaps / self.sides[1].id / "root", None
        )
        for scenario in self.shared_scenarios():
            reason = revert_status(at_base, scenario.name) if scenario.form == "revert" else None
            reason = reason or next(
                (
                    "not applicable: the candidate touches its edit"
                    for side in self.sides
                    if status[side.id].get(scenario.name) is not None
                ),
                None,
            )
            if reason is not None:
                self.results.append(
                    {
                        "name": scenario.name,
                        "number": scenario.number,
                        "status": "not applicable",
                        "detail": reason,
                    }
                )
                continue
            starts = {side.id: snaps / side.id / scenario.name for side in self.sides}
            try:
                for side in self.sides:
                    ref = f"refs/perf/{side.id}/start/{scenario.name}"
                    perf_clone.warm(
                        clone,
                        ref,
                        settle_shape=self.settle,
                        log_dir=self.record / "logs",
                        label=f"warm-{scenario.name}-{side.id}",
                        max_passes=START_WARM_PASSES,
                        before_pass=lambda: self.wait_quiet(clone),
                        from_snapshot=snaps / side.id / "root",
                        main=self.main,
                        retries=BATCH_RETRIES,
                    )
                    perf_clone.take_snapshot(clone, starts[side.id], ref=ref)
                    self.check_stop()
            except WarmFailed as failure:
                self.check_stop()
                self.results.append(
                    {
                        "name": scenario.name,
                        "number": scenario.number,
                        "status": "failed",
                        "detail": f"its start tree does not build green at B: re-aim ({failure})",
                    }
                )
                continue
            self.measure(
                scenario,
                clone,
                {
                    side.id: (starts[side.id], f"refs/perf/{side.id}/start/{scenario.name}")
                    for side in self.sides
                },
            )
            for path in starts.values():
                perf_clone.remove(path)

    def base_seed(self, clone: Path) -> Seed:
        if self.args.seed is not None:
            seed = Path(self.args.seed).resolve()
            return Seed(seed, file_sha256(seed), None, 0, ())
        post = _git(self.main, "show", f"{self.base}:{STANDING_RULES}").stdout
        root = _git(clone, "show", f"refs/perf/base/composite:{STANDING_RULES}").stdout
        return self.prepare_seed(
            clone, committer_time_utc(self.main, self.base), post, root, keep=self.candidate.patch is None
        )

    def prepare_seed(self, clone: Path, as_of: str, post_rules: str, root_rules: str, *, keep: bool) -> Seed:
        """Restore this checkout's verdict store as of `as_of` from its journal (read-only), with `merge_verdicts` as this checkout's HEAD commit has it, run in `clone` reset to `refs/perf/main/HEAD`, so no uncommitted code reads the live journal; it writes only into the clone's `tmp/`. Stamp it `exported_at: as_of`, since the restore stamps the moment it ran and the seed's sha256 must depend only on the journal and `as_of`, then scrub the fills of the rules the post-edit trees have that the root lacks or holds differently (`changed_rule_ids`, `scrub_seed`). With `keep`, the seed is kept as `var/keep/perf/seeds/<sha256>.json`; otherwise it is scratch under `var/perf/`, for the caller to delete once the root is prepared."""
        perf_clone.wait_for_pass_lock(clone)
        perf_clone.reset_tracked(clone, "refs/perf/main/HEAD")
        restored = clone / "tmp" / "perf-seed.json"
        restored.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.tools.merge_verdicts",
            "--restore-as-of",
            as_of,
            "--journal",
            str(self.main / "verdicts-journal.ndjson"),
            "--autosave",
            str(clone / "verdicts-autosave.json"),
            "--out",
            str(restored),
        ]
        code, _ = perf_clone.run_logged(
            argv, cwd=clone, log=self.log("seed.log"), env=perf_clone.child_env(self.main)
        )
        if code != 0:
            raise PerfRefused(
                f"the verdict store could not be restored as of {as_of} (log: {self.log('seed.log')})"
            )
        store = json.loads(restored.read_text(encoding="utf-8"))
        restored.unlink()
        store["exported_at"] = as_of
        drop = sorted(changed_rule_ids(post_rules, root_rules))
        scrubbed, dropped = scrub_seed(store, drop)
        data = json.dumps(scrubbed, ensure_ascii=False).encode()
        digest = hashlib.sha256(data).hexdigest()
        path = KEEP_DIR / "seeds" / f"{digest}.json" if keep else PERF_DIR / f"{clone.name}.seed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not keep or not path.exists():
            path.write_bytes(data)
        self.say(
            f"seed as of {as_of}: dropped {dropped} records for {', '.join(drop) or 'no rule'}; "
            + (f"kept as {path}" if keep else "scratch until the root is prepared")
        )
        return Seed(path, digest, as_of, dropped, tuple(drop), scratch=not keep)

    def prepare_root(self, clone: Path, side: Side, source: Path, target: Path, seed: Seed | None) -> None:
        self.say(f"preparing {side.id}'s root in {clone.name}")
        if seed is not None:
            self.seeds[clone.name] = seed
        passes = perf_clone.prepare_root(
            clone,
            f"refs/perf/{side.id}/composite" if clone.name == SHARED_CLONE else f"refs/perf/{side.id}/tree",
            source=source if source.exists() else None,
            target=target,
            seed=None if seed is None else seed.path,
            settle_shape=self.settle,
            log_dir=self.record / "logs",
            label=f"root-{clone.name}-{side.id}",
            max_passes=ROOT_WARM_PASSES,
            before_pass=lambda: self.wait_quiet(clone),
            main=self.main,
            retries=BATCH_RETRIES,
        )
        self.roots[f"{clone.name}/{side.id}"] = [root_pass(result) for result in passes]
        self.check_stop()

    def run_letter_migration(self) -> None:
        for scenario in self.forward_scenarios():
            assert scenario.base is not None and scenario.patch is not None
            if self.candidate.patch is not None:
                reason = forward_status(
                    self.main,
                    self.candidate.paths,
                    letter_base=scenario.base,
                    base=self.base,
                    candidate=self.sides[1].patch,
                    index=self.record / "forward-check.index",
                )
                if reason is not None:
                    self.results.append(
                        {
                            "name": scenario.name,
                            "number": scenario.number,
                            "status": "not measured",
                            "detail": reason,
                        }
                    )
                    continue
            name = self.letter_clone_name(scenario)
            clone = self.ensure_clone(name, scenario.base)
            for side in self.sides:
                status = perf_clone.build_refs(
                    clone,
                    side.id,
                    scenario.base,
                    candidate=side.patch,
                    reverts={},
                    forwards={scenario.name: scenario.patch},
                    as_is=(),
                    exclude=FORWARD_EXCLUDES,
                )
                if status.get(scenario.name) is not None:
                    self.results.append(
                        {
                            "name": scenario.name,
                            "number": scenario.number,
                            "status": "not applicable",
                            "detail": status[scenario.name],
                        }
                    )
                    break
            else:
                snaps = PERF_DIR / f"{name}.snap"
                kept_root = snaps / "root"
                if not kept_root.exists():
                    self.prepare_root(
                        clone, self.sides[0], kept_root, kept_root, self.letter_seed(clone, scenario)
                    )
                for entry in sorted(snaps.iterdir()):
                    if entry.name != kept_root.name:
                        perf_clone.remove(entry)
                self.prepare_root(clone, self.sides[1], kept_root, snaps / self.sides[1].id / "root", None)
                roots = {self.sides[0].id: kept_root, self.sides[1].id: snaps / self.sides[1].id / "root"}
                self.measure(
                    scenario,
                    clone,
                    {
                        side.id: (roots[side.id], f"refs/perf/{side.id}/start/{scenario.name}")
                        for side in self.sides
                    },
                )
                perf_clone.remove(snaps / self.sides[1].id)
                perf_clone.wait_for_pass_lock(clone)
                perf_clone.restore_snapshot(clone, kept_root)
                perf_clone.reset_tracked(clone, "refs/perf/base/tree")

    def letter_seed(self, clone: Path, scenario: Scenario) -> Seed:
        assert scenario.base is not None and scenario.patch is not None
        if scenario.seed:
            kept = KEEP_DIR / "seeds" / f"{scenario.seed['sha256']}.json"
            if not kept.is_file() or file_sha256(kept) != scenario.seed["sha256"]:
                raise PerfRefused(
                    f"{scenario.name}'s kept seed {kept} is missing or does not match its sha256"
                )
            return Seed(kept, scenario.seed["sha256"], scenario.seed.get("as_of"), 0, ())
        root_rules = _git(clone, "show", f"{scenario.base}:{STANDING_RULES}").stdout
        index = clone / ".git" / "perf-post.index"
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            perf_clone.git(clone, "read-tree", scenario.base, env=env)
            perf_clone.git(clone, "apply", "--cached", str(scenario.patch), env=env)
            post_tree = perf_clone.git(clone, "write-tree", env=env)
        finally:
            perf_clone.remove(index)
        post_rules = _git(clone, "show", f"{post_tree}:{STANDING_RULES}").stdout
        seed = self.prepare_seed(
            clone, committer_time_utc(self.main, scenario.base), post_rules, root_rules, keep=True
        )
        self.say(
            f"record it in rebuild/perf/scenarios.yaml as {scenario.name}'s seed: {{as_of: {seed.as_of}, sha256: {seed.sha256}}}"
        )
        return seed

    def measure(self, scenario: Scenario, clone: Path, starts: Mapping[str, tuple[Path, str]]) -> None:
        """Measure a scenario's pair, and once more from the same start states when its only failure is a separation that fails the check (`judge`); the second pair's result stands, with the first pair's verdict beside it."""
        result, confirm = self.measure_pair(scenario, clone, starts, scenario.name)
        if confirm:
            self.say(f"{scenario.name}: {result['verdict']} on the first pair; measuring the pair again")
            first = {"verdict": result["verdict"], "detail": result["detail"]}
            result, _ = self.measure_pair(scenario, clone, starts, f"{scenario.name}.confirm")
            result["first_pair"] = first
        self.results.append(result)

    def measure_pair(
        self, scenario: Scenario, clone: Path, starts: Mapping[str, tuple[Path, str]], directory: str
    ) -> tuple[dict, bool]:
        order = list(self.sides) if scenario.number % 2 else list(reversed(self.sides))
        batches: dict[str, Batch] = {}
        fingerprints: dict[str, dict] = {}
        patch = scenario.patch if scenario.form != "none" else None
        for side in order:
            snapshot, start = starts[side.id]
            batch = self.run_batch(scenario, side, clone, snapshot, start, patch, directory)
            batches[side.id] = batch
            if batch.outcome != "ok":
                break
            out = batch.record / "fingerprint.json"
            failure = self.fingerprint(clone, out)
            if failure is not None:
                self.check_stop()
                batch.outcome, batch.detail = "fingerprint", failure
                break
            fingerprints[side.id] = json.loads(out.read_text(encoding="utf-8"))
        return self.judge(scenario, batches, fingerprints)

    def run_batch(
        self,
        scenario: Scenario,
        side: Side,
        clone: Path,
        snapshot: Path,
        start: str,
        patch: Path | None,
        directory: str,
    ) -> Batch:
        record = self.record / directory / side.id
        batch = Batch(side.id, record, "harness")
        for attempt in range(BATCH_RETRIES + 1):
            if record.exists():
                record.rename(record.with_name(f"{side.id}.discarded-{attempt}"))
            record.mkdir(parents=True)
            hook = ["uv", "run", "--directory", str(self.main), "python", "-m", "rebuild.tools.perf_clone"]
            prepare = [*hook, "prepare", "--clone", str(clone), "--snapshot", str(snapshot), "--start", start]
            if patch is not None:
                prepare += ["--patch", str(patch)]
            argv = hyperfine_argv(
                runs=self.args.runs,
                name=f"{scenario.name}@{side.id}",
                prepare=[*prepare, "--record", str(record)],
                conclude=[*hook, "conclude", "--clone", str(clone), "--record", str(record)],
                export=record / "hyperfine.json",
                command=perf_clone.cycle_argv(clone, self.main),
            )
            self.say(f"{scenario.name}@{side.id}: batch attempt {attempt + 1}")
            env = perf_clone.git_env({perf_clone.DRIVER_PID_ENV: str(os.getpid())})
            with (record / "hyperfine.log").open("wb") as out:
                subprocess.run(
                    argv,
                    cwd=self.main,
                    env=env,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            if self.stopping is not None:
                record.rename(record.with_name(f"{side.id}.discarded-stopped"))
                self.check_stop()
            batch = read_batch(record, runs=self.args.runs)
            if batch.outcome == "prepare-refused":
                raise PerfRefused(f"{scenario.name}@{side.id}: {batch.detail}")
            if batch.outcome not in RETRY_OUTCOMES:
                return batch
            self.say(f"{scenario.name}@{side.id}: discarding the batch: {batch.detail}")
            if attempt < BATCH_RETRIES:
                self.wait_quiet(clone)
        return batch

    def fingerprint(self, clone: Path, out: Path) -> str | None:
        """Fingerprint the clone into `out`, and return why it failed, or None."""
        argv = [
            "uv",
            "run",
            "--directory",
            str(clone),
            "python",
            "-P",
            str(FINGERPRINT_SCRIPT),
            "--root",
            str(clone),
            "--out",
            str(out),
        ]
        code, _ = perf_clone.run_logged(
            argv, cwd=clone, log=self.log("fingerprints.log"), env=perf_clone.child_env(self.main)
        )
        return f"fingerprinting {clone} failed (log: {self.log('fingerprints.log')})" if code != 0 else None

    def judge(
        self, scenario: Scenario, batches: Mapping[str, Batch], fingerprints: Mapping[str, dict]
    ) -> tuple[dict, bool]:
        """Judge one pair, and say whether its only failure is a separation that fails the check: `proven slower`, or under `--baseline` either separation, where the intermediate stores must match too."""
        result: dict[str, Any] = {"name": scenario.name, "number": scenario.number, "sides": {}}
        base_id, candidate_id = self.sides[0].id, self.sides[1].id
        failures: list[str] = []
        shapes: dict[str, dict] = {}
        for side_id, batch in batches.items():
            entry: dict[str, Any] = {
                "outcome": batch.outcome,
                "detail": batch.detail,
                "record": str(batch.record),
            }
            result["sides"][side_id] = entry
            if batch.outcome != "ok":
                failures.append(f"{side_id}: {batch.detail or batch.outcome}")
                continue
            timed = [
                TimedRun(k, float(batch.stamps[k]["prepared_at"]), batch.times[k - 1], batch.lines[k])
                for k in range(1, len(batch.stamps))
            ]
            agreement = journal_agreement(
                timed, cpu_count=self.cpu_count, mem_total_bytes=self.mem_total_bytes
            )
            run_lines = batch.run_lines()
            side_shapes = [pass_shape(line) for line in run_lines]
            inside = sorted(
                {
                    difference
                    for shape in side_shapes[1:]
                    for difference in compare_shapes(side_shapes[0], shape)[0]
                }
            )
            entry.update(
                runs_s=batch.times,
                summary=describe_times(batch.times),
                wall_s=agreement.wall_s,
                gaps_s=agreement.gaps,
                gap_range_s=(max(agreement.gaps) - min(agreement.gaps)) if agreement.gaps else None,
                agreement=agreement.problems,
                critical_path=critical_path_summary(batch),
                rescans=sum(1 for stamp in batch.stamps if stamp.get("journal_rescan")),
                new_plan_keys=side_shapes[0]["new"] if side_shapes else [],
            )
            if side_shapes:
                shapes[side_id] = side_shapes[0]
            failures += [f"{side_id}: {problem}" for problem in agreement.problems]
            failures += [
                f"{side_id}: the shape varies inside the batch: {difference}" for difference in inside
            ]
        if failures or len(shapes) != 2:
            result.update(status="failed", detail="; ".join(failures) or "a batch has no run line")
            return result, False
        misses = holds_declared(batches[base_id].run_lines()[0], scenario.shape)
        if misses:
            result.update(status="shape drifted", detail=f"shape drifted: re-aim ({'; '.join(misses)})")
            return result, False
        shape_differences, widths = compare_shapes(shapes[base_id], shapes[candidate_id])
        diff = compare_fingerprints(fingerprints[base_id], fingerprints[candidate_id])
        comparison = classify(
            batches[base_id].times,
            batches[candidate_id].times,
            base_toolchain=batches[base_id].toolchain,
            candidate_toolchain=batches[candidate_id].toolchain,
        )
        base_times, candidate_times = batches[base_id].times, batches[candidate_id].times
        base_walls = result["sides"][base_id]["wall_s"]
        candidate_walls = result["sides"][candidate_id]["wall_s"]
        result.update(
            verdict=comparison.verdict,
            verdict_reason=comparison.reason,
            saving_s=statistics.median(base_times) - statistics.median(candidate_times),
            wall_saving_s=statistics.median(base_walls) - statistics.median(candidate_walls),
            shape_differences=shape_differences,
            reported_widths=widths,
            fingerprint_differences=diff.differences,
            store_differences=diff.stores,
            not_comparable=diff.not_comparable,
        )
        aa = self.candidate.patch is None
        problems = [*shape_differences, *diff.differences, *(diff.stores if aa else [])]
        separated = comparison.verdict == "proven slower" or (aa and comparison.verdict == "proven faster")
        if comparison.verdict == "no verdict":
            problems.append(comparison.reason)
        confirm = separated and not problems
        if separated:
            problems.append(f"{comparison.verdict} ({comparison.reason})")
        result.update(status="failed" if problems else "measured", detail="; ".join(problems))
        return result, confirm

    def run_faults(self) -> None:
        rows = self.fault_rows()
        if rows == "none":
            return
        if not FAULT_KIT.is_file():
            self.faults = {"status": "failed", "detail": f"{FAULT_KIT.relative_to(self.main)} is missing"}
            return
        clone = self.clone_path(SHARED_CLONE)
        side = self.sides[1]
        snaps = PERF_DIR / f"{SHARED_CLONE}.snap"
        perf_clone.wait_for_pass_lock(clone)
        perf_clone.restore_snapshot(clone, snaps / side.id / "root")
        perf_clone.reset_tracked(clone, f"refs/perf/{side.id}/tree")
        changed = self.record / "changed.txt"
        changed.write_text("".join(f"{path}\n" for path in self.candidate.paths))
        out = self.record / "faults.json"
        argv = [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.tools.fault_kit",
            "--root",
            str(clone),
            "--snapshot",
            str(PERF_DIR / "faults.snap"),
            "--rows",
            rows,
            "--changed",
            str(changed),
            "--base",
            "refs/perf/base/tree",
            "--out",
            str(out),
            "--log-dir",
            str(self.record / "faults"),
        ]
        self.wait_quiet(clone)
        code, elapsed = perf_clone.run_logged(
            argv, cwd=self.main, log=self.log("fault-kit.log"), env=perf_clone.child_env(self.main)
        )
        for snapshot in (PERF_DIR / "faults.snap", PERF_DIR / "faults.snap-base"):
            perf_clone.remove(snapshot)
        self.check_stop()
        result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
        status = {0: "passed", 1: "failed"}.get(code, "refused")
        self.faults = {
            "status": status,
            "exit": code,
            "elapsed_s": elapsed,
            "selected": result.get("selected"),
            "unreached": result.get("unreached"),
            "rows": [{"id": row.get("id"), "result": row.get("result")} for row in result.get("rows") or []],
        }

    def finish_shared(self) -> None:
        clone = self.clone_path(SHARED_CLONE)
        snaps = PERF_DIR / f"{SHARED_CLONE}.snap"
        fresh = snaps / "base" / "root"
        if not fresh.exists():
            return
        perf_clone.wait_for_pass_lock(clone)
        perf_clone.restore_snapshot(clone, fresh)
        perf_clone.reset_tracked(clone, "refs/perf/base/composite")
        perf_clone.remove(snaps / "root")
        fresh.rename(snaps / "root")
        for entry in snaps.iterdir():
            if entry.name != "root":
                perf_clone.remove(entry)

    def report(self) -> int:
        failed = [
            result for result in self.results if result.get("status") not in ("measured", "not measured")
        ]
        if self.faults is not None and self.faults["status"] != "passed":
            failed.append({"name": "fault kit", **self.faults})
        baselines = None
        if self.candidate.patch is None and not failed and self.results:
            problems = self.write_baselines()
            baselines = {"written": not problems, "differences": problems}
            if problems:
                failed.append({"name": "baselines", "differences": problems})
        document = {
            "format": REPORT_FORMAT,
            "base": self.base,
            "candidate": self.candidate.id,
            "source": self.candidate.source,
            "machine": machine_tag(self.cpu_count, self.mem_total_bytes),
            "seeds": {
                clone: {
                    "sha256": seed.sha256,
                    "as_of": seed.as_of,
                    "dropped": seed.dropped,
                    "rules": list(seed.rules),
                }
                for clone, seed in self.seeds.items()
            },
            "scenarios": self.results,
            "faults": self.faults,
            "baselines": baselines,
            "roots": self.roots,
            "disk": disk_use(PERF_DIR),
        }
        (self.record / "report.json").write_text(
            json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        lines = render_report(document)
        (self.record / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines), flush=True)
        return 1 if failed else 0

    def write_baselines(self) -> list[str]:
        """Rewrite this machine's row in each measured scenario's baseline file, unless a new fingerprint differs from another machine's row at the same base (`machine_differences`): then write no file and return the differences."""
        by_name = {scenario.name: scenario for scenario in self.all_scenarios}
        writes: list[tuple[Path, dict]] = []
        differences: list[str] = []
        for result in self.results:
            scenario = by_name.get(result["name"])
            if scenario is None or result.get("status") != "measured":
                continue
            base = result["sides"]["base"]
            record = Path(base["record"])
            batch = read_batch(record, runs=self.args.runs)
            if scenario.form == "forward":
                seed = self.seeds.get(self.letter_clone_name(scenario))
                seed_sha256 = seed.sha256 if seed is not None else (scenario.seed or {}).get("sha256")
            else:
                seed = self.seeds.get(SHARED_CLONE)
                seed_sha256 = None if seed is None else seed.sha256
            shape = pass_shape(batch.run_lines()[0])["compared"]
            probes = batch.stamps[1].get("probes") or {}
            times = base["runs_s"]
            discarded = sum(
                1 for path in record.parent.iterdir() if path.name.startswith(f"{record.name}.discarded-")
            )
            row = {
                "cpu_count": self.cpu_count,
                "mem_total_bytes": self.mem_total_bytes,
                "os_build": probes.get("os_build"),
                "rustc": (probes.get("toolchain") or {}).get("rustc"),
                "hyperfine": (probes.get("toolchain") or {}).get("hyperfine"),
                "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "base": self.base if scenario.form != "forward" else scenario.base,
                "seed_sha256": seed_sha256,
                "fingerprint": json.loads((record / "fingerprint.json").read_text(encoding="utf-8")),
                "runs_s": times,
                "median_s": statistics.median(times),
                "mad_s": mad(times),
                "min_s": min(times),
                "max_s": max(times),
                "journal_wall_s": base["wall_s"],
                "gap_s": base["gaps_s"],
                "critical_path": base["critical_path"],
                "aa": {"verdict": result["verdict"], "fingerprints": "identical"},
                "pass": {"plan_flags": shape["plan"], "gates": shape["gates"], "carry": shape["carry"]},
                "probes": {
                    **probe_record(record / "system.ndjson"),
                    "servers": sorted(
                        {str((stamp.get("probes") or {}).get("server")) for stamp in batch.stamps}
                    ),
                    "waits_s": round(sum(float(stamp.get("waited_s") or 0) for stamp in batch.stamps), 1),
                    "disturbed_batches": discarded,
                    "journal_rescans": sum(1 for stamp in batch.stamps if stamp.get("journal_rescan")),
                },
            }
            path = PERF_DATA / "baselines" / f"{scenario.name}.json"
            document = (
                json.loads(path.read_text(encoding="utf-8")) if path.is_file() else empty_baseline(scenario)
            )
            document["patch_sha256"] = empty_baseline(scenario)["patch_sha256"]
            differences += [f"{scenario.name}: {item}" for item in machine_differences(document, row)]
            writes.append((path, merge_machine_row(document, row)))
        if differences:
            return differences
        for path, document in writes:
            path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return []


def render_report(document: Mapping) -> list[str]:
    lines = [
        f"perf-check {document['candidate']} on {document['base'][:12]} ({document['source']}), {document['machine']}",
        "",
    ]
    for result in sorted(document["scenarios"], key=lambda item: item.get("number") or 0):
        lines.append(f"{result.get('number', '-')}. {result['name']}: {result.get('status')}")
        if result.get("detail"):
            lines.append(f"   {result['detail']}")
        for side_id, side in (result.get("sides") or {}).items():
            if "summary" in side:
                walls = side.get("wall_s") or []
                journal = (
                    f"journal wall_s median {statistics.median(walls):.1f} s"
                    if walls
                    else "no journal wall_s"
                )
                gaps = (
                    f"; gap range {side['gap_range_s']:.1f} s" if side.get("gap_range_s") is not None else ""
                )
                lines.append(
                    f"   {side_id:<16} {side['summary']}; {journal}{gaps}; critical path {side['critical_path']}"
                )
            else:
                lines.append(f"   {side_id:<16} {side.get('outcome')}")
        if "verdict" in result:
            lines.append(
                f"   verdict: {result['verdict']}; saving {result['saving_s']:+.1f} s (journal wall_s {result['wall_saving_s']:+.1f} s)"
            )
            for key in (
                "shape_differences",
                "reported_widths",
                "fingerprint_differences",
                "store_differences",
                "not_comparable",
            ):
                for item in result.get(key) or []:
                    lines.append(f"   {key.replace('_', ' ')}: {item}")
        if result.get("first_pair"):
            lines.append(
                f"   first pair: {result['first_pair']['verdict']}; this is the second pair's result"
            )
    faults = document.get("faults")
    if faults is not None:
        lines += [
            "",
            f"fault kit: {faults['status']}" + (f" ({faults['detail']})" if faults.get("detail") else ""),
        ]
        for row in faults.get("rows") or []:
            lines.append(f"   {row['id']}: {row['result']}")
        for gate, rows in (faults.get("unreached") or {}).items():
            carriers = f"rows {', '.join(rows)}" if rows else "no row"
            lines.append(f"   unreached: {gate} ({carriers})")
    baselines = document.get("baselines")
    if baselines is not None:
        lines += ["", f"baselines: {'written' if baselines['written'] else 'not written'}"]
        lines += [f"   differs from another machine: {item}" for item in baselines["differences"]]
    roots = document.get("roots") or {}
    if roots:
        lines += ["", "root warms:"]
        for name, passes in roots.items():
            described = [
                f"{item['classification']} {item['elapsed_s']:.0f} s"
                + (
                    f", orphan share {item['orphan_share']:.4f}"
                    if item.get("orphan_share") is not None
                    else ""
                )
                for item in passes
            ]
            lines.append(f"   {name}: {'; '.join(described)}")
    disk = document.get("disk") or {}
    if disk:
        lines.append(
            "disk: " + ", ".join(f"var/perf/{name} {format_gb(size)} GB" for name, size in disk.items())
        )
    return lines


def select_scenarios(scenarios: Sequence[Scenario], names: Sequence[str] | None) -> list[Scenario]:
    wanted = list(names or ["all"])
    if wanted == ["all"]:
        return list(scenarios)
    if wanted == ["none"]:
        return []
    known = {scenario.name for scenario in scenarios}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise PerfRefused(
            f"unknown scenario(s): {', '.join(unknown)}; rebuild/perf/scenarios.yaml names {', '.join(sorted(known))}"
        )
    return [scenario for scenario in scenarios if scenario.name in wanted]


@contextlib.contextmanager
def perf_lock() -> Iterator[None]:
    try:
        with store_lock.hold_flock(PERF_DIR / "perf.lock", blocking=False) as fd:
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
            yield
    except store_lock.LockBusy:
        raise PerfRefused(
            f"another check holds {PERF_DIR / 'perf.lock'} (pid {store_lock.holder(PERF_DIR / 'perf.lock')})"
        ) from None


def dry_run(
    args: argparse.Namespace, scenarios: Sequence[Scenario], candidate: Candidate, *, main: Path = ROOT
) -> int:
    """Print what a check would do, without bootstrapping, building or running any pass. Its only writes are a temporary index and the captured candidate under `var/perf/dry-run/`, which it deletes, with `var/perf/` itself when it made it."""
    selected = select_scenarios(scenarios, args.scenario)
    cpu_count, mem_total_bytes = os.cpu_count() or 0, memory_budget.total_memory_bytes()
    made_perf_dir = not PERF_DIR.exists()
    scratch = PERF_DIR / "dry-run"
    scratch.mkdir(parents=True, exist_ok=True)
    lines = [
        f"perf-check dry run: {candidate.id} ({candidate.source}) on B = {candidate.base[:12]} "
        f"({_git(main, 'log', '-1', '--format=%s', candidate.base).stdout.strip()}), {machine_tag(cpu_count, mem_total_bytes)}",
        f"sides: base and {'aa (the A/A check; baselines are rewritten on success)' if candidate.patch is None else candidate.id}; "
        f"{args.runs} timed runs and one warm-up a batch",
    ]
    if candidate.paths:
        lines.append(f"candidate paths: {', '.join(candidate.paths)}")
    try:
        candidate_patch = None
        if candidate.patch is not None:
            candidate_patch = scratch / "candidate.patch"
            candidate_patch.write_bytes(candidate.patch)
        shared = PERF_DIR / SHARED_CLONE
        summary = summarize_probes(
            perf_clone.collect_probes(main, shared if (shared / ".git").exists() else None)
        )
        outcomes = probe_outcomes(summary, phase="driver")
        lines += ["", "Probes:"]
        lines.append(
            f"  powermode {summary['powermode']}, therm {summary['therm'] or 'quiet'}, pressure {summary['pressure_level']}, "
            f"AC {summary['ac_power']}, server {summary['server']}, disk {format_gb(summary['disk_free'] or 0)} GB free, "
            f"load {summary['load_1m']:.2f}, heavy {', '.join(summary['heavy']) or 'none'}"
        )
        versions = "; ".join(str(version) for version in summary["toolchain"].values())
        lines.append(f"  toolchain: {versions}; OS build {summary['os_build']}")
        lines += [f"  {item.action}: {item.reason}" for item in outcomes] or ["  nothing to wait for"]

        reverts = {s.name: s.patch for s in scenarios if s.form == "revert" and s.patch is not None}
        forwards = {s.name: s.patch for s in scenarios if s.form == "forward" and s.patch is not None}
        index = scratch / "check.index"
        at_base = patches_apply_at(main, candidate.base, reverts, index)
        lines += [
            "",
            f"Scenario patches at B (composite: {'composes' if at_base.composes else 'does not compose'}):",
        ]
        on_candidate = (
            patches_apply_at(main, candidate.base, reverts, index, candidate=candidate_patch)
            if candidate_patch
            else None
        )
        for scenario in selected:
            if scenario.form == "revert":
                reason = revert_status(at_base, scenario.name)
                if reason is None and on_candidate is not None:
                    if on_candidate.candidate is not None:
                        reason = (
                            f"not applicable: the candidate does not apply at B ({on_candidate.candidate})"
                        )
                    elif on_candidate.reverses.get(scenario.name) is not None:
                        reason = "not applicable: the candidate touches its edit"
                lines.append(
                    f"  {scenario.number}. {scenario.name} (revert): {reason or 'reverses, composes and re-applies'}"
                )
            elif scenario.form == "none":
                lines.append(f"  {scenario.number}. {scenario.name} (none): a pass over the tree itself")
            else:
                assert scenario.base is not None
                own = patches_apply_at(
                    main, scenario.base, {}, index, forwards={scenario.name: forwards[scenario.name]}
                )
                state = (
                    "applies at its base"
                    if own.forwards.get(scenario.name) is None
                    else f"does not apply at its base: {own.forwards[scenario.name]}"
                )
                if candidate.patch is not None:
                    reason = forward_status(
                        main,
                        candidate.paths,
                        letter_base=scenario.base,
                        base=candidate.base,
                        candidate=candidate_patch,
                        index=index,
                    )
                    state += f"; {reason}" if reason is not None else "; takes the candidate"
                seed = (
                    "kept seed recorded"
                    if scenario.seed
                    else "no kept seed yet: the first bootstrap restores one from the journal"
                )
                lines.append(
                    f"  {scenario.number}. {scenario.name} (forward at {scenario.base[:8]}): {state}; {seed}"
                )

        rows = fault_selection(args.faults, baseline=candidate.patch is None)
        lines += ["", f"Fault kit (--faults {rows}):"]
        fault_share = 0.0
        if rows == "none":
            lines.append("  not run")
        elif not FAULT_KIT.is_file():
            lines.append(
                f"  {FAULT_KIT.relative_to(main)} is missing, so a check would report the kit as failed"
            )
        else:
            changed = scratch / "changed.txt"
            changed.write_text("".join(f"{path}\n" for path in candidate.paths))
            listing = subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "rebuild.tools.fault_kit",
                    "--list",
                    "--rows",
                    rows,
                    "--changed",
                    str(changed),
                ],
                cwd=main,
                capture_output=True,
                text=True,
            )
            lines += [f"  {line}" for line in (listing.stdout + listing.stderr).splitlines()] or [
                "  (no output)"
            ]
            listed = [
                fields
                for fields in map(str.split, listing.stdout.splitlines())
                if fields and fields[0].isdigit()
            ]
            if listed:
                fault_share = sum(fields[-1] == "yes" for fields in listed) / len(listed)

        letter_clones = sorted({(s.base or "")[:8] for s in selected if s.form == "forward"})
        clones = (
            [SHARED_CLONE] if any(s.form != "forward" for s in selected) or rows != "none" else []
        ) + letter_clones
        fresh = [name for name in clones if not perf_clone.bootstrapped(PERF_DIR / name)]
        lines += ["", "Clones:"]
        for name in clones:
            state = "bootstraps (cold)" if name in fresh else "exists"
            root = PERF_DIR / f"{name}.snap" / "root"
            lines.append(
                f"  var/perf/{name}: {state}; kept root snapshot {'present' if root.exists() else 'absent'}"
            )

        medians = shape_medians(
            main / perf_clone.TIMINGS_JOURNAL, scenarios, cpu_count=cpu_count, mem_total_bytes=mem_total_bytes
        )
        items, total = estimate(
            medians, selected, runs=args.runs, fresh_clones=len(fresh), fault_share=fault_share
        )
        lines += ["", "Estimate (this machine's journal medians per declared shape):"]
        for scenario in selected:
            median = medians.get(scenario.name)
            lines.append(
                f"  {scenario.name}: {'no matching pass' if median is None else f'{median:.0f} s a pass'}"
            )
        lines += [f"  {label}: {seconds / 3600:.1f} h" for label, seconds in items]
        lines.append(f"  total: about {total / 3600:.1f} h")
        print("\n".join(lines))
        return 2 if any(item.action == "refuse" for item in outcomes) else 0
    finally:
        perf_clone.remove(scratch)
        if made_perf_dir:
            PERF_DIR.rmdir()


def clean() -> int:
    """Delete `var/perf/`, refusing while a check holds `perf.lock` or a clone's pass lock has a holder, as a land a killed check left running does."""
    holder = store_lock.holder(PERF_DIR / "perf.lock")
    if holder is not None:
        raise PerfRefused(f"a check (pid {holder}) holds {PERF_DIR / 'perf.lock'}; stop it first")
    for lock in sorted(PERF_DIR.glob(f"*/{perf_clone.PASS_LOCK}")):
        holder = store_lock.holder(lock)
        if holder is not None:
            raise PerfRefused(f"a pass (pid {holder}) holds {lock}; wait for it to end first")
    perf_clone.remove(PERF_DIR)
    print(f"deleted {PERF_DIR}; the next check bootstraps its clones again")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check a speed-up candidate against the edit-to-READY scenarios (doc/perf-check.md)."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="a scenario to run (repeatable); 'all' (the default) or 'none', which runs only the fault kit",
    )
    parser.add_argument(
        "--candidate",
        default="worktree",
        help="worktree (this checkout's uncommitted diff, the default) or A..B",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="measure the base against itself and record this machine's baselines",
    )
    parser.add_argument("--base", help="with --baseline: the commit to measure (default HEAD)")
    parser.add_argument(
        "--seed",
        type=Path,
        help="with --baseline: a kept verdict-store seed to start from instead of the journal",
    )
    parser.add_argument(
        "--runs", type=int, default=MIN_RUNS, help=f"timed runs a batch (at least {MIN_RUNS})"
    )
    parser.add_argument(
        "--faults",
        default="auto",
        help="auto (rows whose catching gates the candidate touches), all, none, or ids like 01,05",
    )
    parser.add_argument(
        "--write-patch",
        metavar="NAME",
        help="rewrite rebuild/perf/scenarios/NAME.patch from the scenario's edit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan, the probes, which patches apply and the estimate",
    )
    parser.add_argument("--clean", action="store_true", help="delete var/perf/")
    args = parser.parse_args(argv)
    if args.clean and any(
        value != parser.get_default(name) for name, value in vars(args).items() if name != "clean"
    ):
        parser.error("--clean takes no other flag")
    if args.runs < MIN_RUNS:
        parser.error(f"--runs may not go below {MIN_RUNS}")
    if (args.base or args.seed) and not args.baseline:
        parser.error("--base and --seed go with --baseline")
    if args.faults not in FAULT_MODES and not re.fullmatch(r"\d+(,\d+)*", args.faults):
        parser.error("--faults takes auto, all, none, or comma-separated row ids")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.clean:
            return clean()
        scenarios = load_scenarios()
        if args.write_patch:
            scenario = next((s for s in scenarios if s.name == args.write_patch), None)
            if scenario is None:
                raise PerfRefused(f"rebuild/perf/scenarios.yaml has no scenario {args.write_patch}")
            print(f"wrote {write_patch(ROOT, scenario).relative_to(ROOT)}")
            return 0
        if os.environ.get(MEMORY_OVERRIDE_ENV):
            raise PerfRefused(
                f"{MEMORY_OVERRIDE_ENV} is set; the clone passes run without it, so their run lines would carry another machine tag than this check's. Unset it first."
            )
        candidate = capture_candidate(ROOT, spec=args.candidate, baseline=args.baseline, base=args.base)
        if args.dry_run:
            return dry_run(args, scenarios, candidate)
        PERF_DIR.mkdir(parents=True, exist_ok=True)
        with perf_lock():
            return Check(args, scenarios, candidate).run()
    except PerfRefused as refusal:
        print(f"perf-check refused: {refusal}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
