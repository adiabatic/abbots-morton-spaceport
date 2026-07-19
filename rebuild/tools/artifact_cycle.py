"""The one-command driver for the commit-time artifact cycle.

It mechanizes the commit-time sequence: snapshot the current review surface (the only recovery copy, since everything under rebuild/out is gitignored), recompile M1.otf and vet it, rebuild the review surface in place, carry prior verdicts forward onto the fresh manifest, merge the carried file into the live autosave (rebuild.tools.merge_verdicts, so the app needs no manual import; --no-merge opts out), re-baseline the census pins, and run the four gates — always printing a summary table at the end, even on failure.

The exit-code trap this driver exists to defuse: run_m1.main() SystemExits nonzero whenever any oracle rows are UNMATCHED, which is always true mid-migration. Its exit code is therefore not the gate; the four summary JSONs it writes are. The real gates are defect_errors, the boundary and Manual-pin passes, and multi_matched == 0.

The two artifact-independent gates (js, make-test) run from t=0 in a small thread pool while the build chain runs inline-serial in the main thread; gate:rebuild starts after the run_m1 gate passes, queued behind make-test by default so only one 12-way pytest pool is ever hot. gate:conform (the exhaustive font-vs-settle sweep, run_m1 --conform-only) also starts after the run_m1 gate passes and, by default, queues behind gate:make-test — co-resident with gate:rebuild's pytest pool rather than after it — since conform is short again post-depth-4-pruning and waiting out gate:rebuild would only add that pool's ~3 minutes to the critical path. Its per-config process pool spins up once the t=0 make-test pool has drained.

gate:make-test is auto-skipped when its input closure is provably unchanged since the last green run. The closure is every tracked or untracked-unignored file outside rebuild/, glyph_data/runes/, doc/, tmp/, .claude/, and Markdown — nothing `make test` executes (make all -> build_font over glyph_data/*.yaml non-recursively, typst, pyright over tools/ test/ conftest.py, pytest test/ site/) reads those trees, so a diff confined to them cannot move the gate's outcome and re-running its ~15 CPU-minutes would verify nothing. The last green fingerprint lives in rebuild/out/make-test-green.json, written by rebuild.tools.make_test_gate — the `make test` entry point — on every green run, so interactive greens and cycle greens share one record and `make test` itself self-skips on the same test. cycle_summary.json still records the fingerprint the cycle ran (or validly skipped) against, and prior_make_test_fingerprint falls back to it when the shared record is absent. The fingerprint sees file content only — a system-toolchain change (a typst upgrade, say; pyright and pytest are pinned through uv.lock, which is in the closure) is invisible to it. --force-make-test runs the gate regardless (as does `make test FORCE=1` inside the wrapper).

Run as: uv run python rebuild/tools/artifact_cycle.py — the carry source is auto-resolved from the autosave and the verdicts-*.json exports; pass --verdicts to name one explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CENSUS_PINS = ROOT / "rebuild" / "review-census-pins.json"
CARRY_TOOL = ROOT / "rebuild" / "tools" / "carry_verdicts.py"
CYCLE_SUMMARY = ROOT / "rebuild" / "out" / "cycle_summary.json"
MAKE_TEST_GREEN = ROOT / "rebuild" / "out" / "make-test-green.json"
JSTEST_DIR = ROOT / "rebuild" / "review" / "jstests"
REVIEW_PORT = 7294

POOL_POLICIES = ("queue", "overlap")
REBUILD_POOL_POLICY_DEFAULT = "queue"
_GATE_POOL_WORKERS = 5
_CONFORM_JOBS_CAP = 8
CONFORM_HORIZON_DEFAULT = 5

M1_SUMMARY_FILES = {
    "pipeline": M1_OUT / "pipeline_summary.json",
    "boundary": M1_OUT / "boundary_equivalence_summary.json",
    "manual_pins": M1_OUT / "manual_pins_summary.json",
    "oracle": M1_OUT / "oracle_summary.json",
}
CONFORM_SUMMARY = M1_OUT / "conform_summary.json"

BASELINE_REBUILD_FAILURES = frozenset(
    {
        "rebuild/test_surface.py::test_real_cell_bindings_all_match",
        "rebuild/test_spec_load.py::test_loads_all_six_runes",
        "rebuild/test_spec_load.py::test_predicate_class_membership",
        "rebuild/test_spec_load.py::test_group_resolution",
    }
)

CENSUS_HINT_MODULES = frozenset(
    {
        "test_review_audit",
        "test_review_build",
        "test_review_families",
        "test_review_ink",
    }
)

MAKE_TEST_EXEMPT_PREFIXES = ("rebuild/", "glyph_data/runes/", "doc/", "tmp/", ".claude/")


def make_test_exempt(path: str) -> bool:
    """Whether a repo-relative path is provably outside gate:make-test's input closure. The exempt trees are safe because nothing the gate executes reads them: build_font globs glyph_data/*.yaml non-recursively (never glyph_data/runes/), and test/, site/, tools/, conftest.py contain no reference to rebuild/ or the rune files; Markdown is never an input to any gate."""
    return path.endswith(".md") or any(path.startswith(prefix) for prefix in MAKE_TEST_EXEMPT_PREFIXES)


def make_test_closure_files(root: Path) -> list[str] | None:
    """Every tracked or untracked-unignored file that could affect gate:make-test, repo-relative and sorted. None when git is unavailable, in which case the caller must run the gate unconditionally."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    paths = {entry for entry in result.stdout.split("\0") if entry}
    return sorted(path for path in paths if not make_test_exempt(path))


def make_test_closure_fingerprint(root: Path = ROOT) -> str | None:
    """Content hash of gate:make-test's input closure, read from the worktree (not the index) so uncommitted edits count. A deleted-but-tracked file hashes as absent, so deletions move the fingerprint too."""
    files = make_test_closure_files(root)
    if files is None:
        return None
    digest = hashlib.sha256()
    for rel in files:
        path = root / rel
        try:
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            file_hash = "absent"
        digest.update(f"{rel}\t{file_hash}\n".encode())
    return digest.hexdigest()


def read_make_test_green(path: Path | None = None) -> dict | None:
    """The shared last-green record for `make test` ({fingerprint, finished_at}), written by rebuild.tools.make_test_gate on every green run — interactive or as gate:make-test."""
    try:
        record = json.loads((path if path is not None else MAKE_TEST_GREEN).read_text())
    except OSError, ValueError:
        return None
    if isinstance(record, dict) and isinstance(record.get("fingerprint"), str):
        return record
    return None


def record_make_test_green(fingerprint: str, path: Path | None = None) -> None:
    target = path if path is not None else MAKE_TEST_GREEN
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"format": "ams-make-test-green/1", "fingerprint": fingerprint, "finished_at": stamp})
        + "\n"
    )
    os.replace(tmp, target)


def prior_make_test_fingerprint(
    summary_path: Path | None = None, green_path: Path | None = None
) -> str | None:
    """The closure fingerprint of the last green `make test` run: the shared green record when present (always at least as fresh, since every green run rewrites it), else the fingerprint the previous cycle recorded green or validly carried forward."""
    record = read_make_test_green(green_path)
    if record is not None:
        return record["fingerprint"]
    try:
        summary = json.loads((summary_path if summary_path is not None else CYCLE_SUMMARY).read_text())
    except OSError, ValueError:
        return None
    value = summary.get("make_test_fingerprint") if isinstance(summary, dict) else None
    return value if isinstance(value, str) else None


@dataclass
class GateOutcome:
    ok: bool
    failures: list[str]
    unmatched: int | None
    multi_matched: int | None


def evaluate_run_m1_gate(pipeline: dict, boundary: dict, manual_pins: dict, oracle: dict) -> GateOutcome:
    """Decide whether the M1 build passed from its four summary JSONs. run_m1's own exit code is not usable — it fails on any UNMATCHED oracle rows, always present mid-migration — so this reads defect_errors, the boundary/Manual-pin passes, and multi_matched instead, and records UNMATCHED only as informational."""
    failures: list[str] = []

    defect_errors = pipeline.get("defect_errors") or []
    if defect_errors:
        failures.append(f"{len(defect_errors)} defect-gate error(s): {defect_errors[0]}")

    if not boundary.get("pass"):
        failures.append(f"boundary-equals-text-edge gate failed ({boundary.get('divergences')} divergences)")

    if not manual_pins.get("pass"):
        failures.append(
            f"Manual-pin gate failed ({len(manual_pins.get('disagreements') or [])} disagreements)"
        )

    multi_matched = oracle.get("multi_matched")
    if multi_matched is not None and multi_matched > 0:
        failures.append(f"oracle multi_matched = {multi_matched} (must be 0)")

    return GateOutcome(
        ok=not failures,
        failures=failures,
        unmatched=oracle.get("unmatched"),
        multi_matched=multi_matched,
    )


def evaluate_conform_gate(summary: dict | None) -> tuple[str, list[str]]:
    """Judge gate:conform from conform_summary.json's contents (None = the subprocess never wrote one). `pass` is the verdict; the detail lines name what broke — shaping divergences are compiler defects by definition, and nonzero uncovered counts mean dead generated rules or transitions."""
    if summary is None:
        return "FAILED (no conform_summary.json)", ["conform gate: run_m1 --conform-only wrote no summary"]
    failures: list[str] = []
    if summary.get("divergences"):
        failures.append(f"conform gate: {summary['divergences']} font-vs-settle divergence(s)")
    if summary.get("uncovered_rules"):
        failures.append(f"conform gate: {summary['uncovered_rules']} dead settlement rule(s)")
    if summary.get("uncovered_transitions"):
        failures.append(f"conform gate: {summary['uncovered_transitions']} dead decision-table transition(s)")
    if not summary.get("pass") and not failures:
        failures.append("conform gate: pass is false")
    if failures:
        return "FAILED", failures
    return "green", []


def conform_gate_argv(jobs: int, horizon: int = CONFORM_HORIZON_DEFAULT) -> list[str]:
    argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--conform-only"]
    if jobs > 1:
        argv += ["--jobs", str(jobs)]
    if horizon != CONFORM_HORIZON_DEFAULT:
        argv += ["--conform-horizon", str(horizon)]
    return argv


def classify_rebuild_failure(test_id: str, update_pins: bool) -> str:
    """Bucket a failing rebuild-suite test id: 'baseline' (the four documented batch-1 spec pins, always expected), 'census-hint' (a census-pinned review test, expected to go stale after a rune change until --update-pins), or 'hard' (anything unexplained — fails the cycle)."""
    if test_id in BASELINE_REBUILD_FAILURES:
        return "baseline"
    module = test_id.split("::", 1)[0]
    stem = Path(module).stem
    if stem in CENSUS_HINT_MODULES and not update_pins:
        return "census-hint"
    return "hard"


@dataclass
class Step:
    name: str
    argv: list[str] | None
    note: str = ""
    lane: str = ""


@dataclass
class Plan:
    short_id: str
    first_run: bool
    snapshot_dir: Path
    carry_out: Path | None
    verdicts: Path | None
    update_pins: bool
    skip_gates: bool
    do_merge: bool = False
    skip_conform: bool = False
    skip_make_test: bool = False
    make_test_note: str = ""
    make_test_fingerprint: str | None = None
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT
    job_budget: int = 1
    conform_jobs: int = 1
    conform_horizon: int = CONFORM_HORIZON_DEFAULT
    review_out: Path | None = None
    census_surface: Path = REVIEW_OUT
    steps: list[Step] = field(default_factory=list)


def jstest_argv() -> list[str]:
    """The JS suite argv. The *.test.js glob form is required — node v26 rejects the bare-directory form with 'Cannot find module' — and the glob is expanded in Python, never handed to a shell."""
    files = sorted(str(path.relative_to(ROOT)) for path in JSTEST_DIR.glob("*.test.js"))
    return ["node", "--test", *files]


def stage_job_budget(*, skip_gates: bool, ncores: int | None = None) -> int:
    """The --jobs budget the driver hands run_m1 and surface-build. Under a gated cycle a 12-way `make test` owns the box from t=0, so the build stages stay serial (1); only --skip-gates frees the cores for them to fan out."""
    n = ncores or (os.cpu_count() or 1)
    return n if skip_gates else 1


def build_plan(
    *,
    verdicts: Path | None,
    no_carry: bool,
    carry_out: Path | None,
    snapshot_dir: Path | None,
    update_pins: bool,
    skip_gates: bool,
    first_run: bool,
    short_id: str,
    no_merge: bool = False,
    skip_conform: bool = False,
    skip_make_test: bool = False,
    make_test_note: str = "",
    make_test_fingerprint: str | None = None,
    conform_horizon: int = CONFORM_HORIZON_DEFAULT,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    review_out: Path | None = None,
    ncores: int | None = None,
) -> Plan:
    resolved_snapshot = snapshot_dir if snapshot_dir is not None else ROOT / "tmp" / f"review-pre-{short_id}"
    do_carry = not no_carry and not first_run
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = (
            carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"
        )

    job_budget = stage_job_budget(skip_gates=skip_gates, ncores=ncores)
    conform_jobs = min(_CONFORM_JOBS_CAP, ncores or (os.cpu_count() or 1))
    census_surface = review_out if review_out is not None else REVIEW_OUT
    do_merge = do_carry and not no_merge and review_out is None

    plan = Plan(
        short_id=short_id,
        first_run=first_run,
        snapshot_dir=resolved_snapshot,
        carry_out=resolved_carry_out,
        verdicts=verdicts,
        update_pins=update_pins,
        skip_gates=skip_gates,
        do_merge=do_merge,
        skip_conform=skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fingerprint,
        pool_policy=pool_policy,
        job_budget=job_budget,
        conform_jobs=conform_jobs,
        conform_horizon=conform_horizon,
        review_out=review_out,
        census_surface=census_surface,
    )

    if first_run:
        plan.steps.append(
            Step("snapshot", None, "SKIPPED (first run: no existing surface to snapshot)", lane="build")
        )
    else:
        plan.steps.append(
            Step("snapshot", None, f"copytree {REVIEW_OUT} -> {resolved_snapshot}", lane="build")
        )

    run_m1_argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
    if job_budget > 1:
        run_m1_argv += ["--jobs", str(job_budget)]
    plan.steps.append(Step("run_m1", run_m1_argv, lane="build"))

    surface_argv = ["uv", "run", "python", "-m", "rebuild.review.build"]
    if job_budget > 1:
        surface_argv += ["--jobs", str(job_budget)]
    if review_out is not None:
        surface_argv += ["--out", str(review_out)]
    plan.steps.append(Step("surface-build", surface_argv, lane="build"))

    if do_carry:
        assert resolved_carry_out is not None
        carry_argv = [
            "uv",
            "run",
            "python",
            str(CARRY_TOOL),
            "--source",
            str(resolved_snapshot),
            str(verdicts),
            "--out",
            str(resolved_carry_out),
        ]
        if review_out is not None:
            carry_argv += ["--current-surface", str(review_out)]
        plan.steps.append(Step("carry", carry_argv, lane="build"))
    elif first_run:
        plan.steps.append(Step("carry", None, "SKIPPED (first run)", lane="build"))
    else:
        plan.steps.append(Step("carry", None, "SKIPPED (--no-carry)", lane="build"))

    if do_merge:
        assert resolved_carry_out is not None
        plan.steps.append(
            Step(
                "merge",
                ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(resolved_carry_out)],
                lane="build",
            )
        )
    elif do_carry and review_out is not None:
        plan.steps.append(
            Step("merge", None, "SKIPPED (rehearsal: the live autosave is never written)", lane="build")
        )
    elif do_carry:
        plan.steps.append(Step("merge", None, "SKIPPED (--no-merge)", lane="build"))
    elif first_run:
        plan.steps.append(Step("merge", None, "SKIPPED (first run)", lane="build"))
    else:
        plan.steps.append(Step("merge", None, "SKIPPED (--no-carry)", lane="build"))

    census_mode = "--update" if update_pins else "--check"
    plan.steps.append(
        Step(
            "census",
            [
                "uv",
                "run",
                "python",
                "-m",
                "rebuild.review.census",
                census_mode,
                "--surface",
                str(census_surface),
            ],
            (
                "then `git diff -- rebuild/review-census-pins.json`, printed in full"
                if update_pins
                else "staleness reported informationally"
            ),
            lane="build",
        )
    )

    if skip_gates:
        plan.steps.append(Step("gates", None, "SKIPPED (--skip-gates)"))
    else:
        plan.steps.append(Step("gate:js", jstest_argv(), lane="t0"))
        plan.steps.append(
            Step(
                "gate:rebuild",
                [
                    "uv",
                    "run",
                    "pytest",
                    "rebuild/",
                    "-n",
                    "auto",
                    "--dist",
                    "worksteal",
                    "-q",
                    "--tb=no",
                    "-rfE",
                ],
                lane="rebuild",
            )
        )
        if skip_conform:
            plan.steps.append(Step("gate:conform", None, "SKIPPED (--skip-conform)", lane="conform"))
        else:
            plan.steps.append(
                Step("gate:conform", conform_gate_argv(conform_jobs, conform_horizon), lane="conform")
            )
        if skip_make_test:
            plan.steps.append(Step("gate:make-test", None, f"SKIPPED ({make_test_note})", lane="t0"))
        else:
            plan.steps.append(Step("gate:make-test", ["make", "test"], lane="t0"))

    return plan


def resolve_carry_source() -> dict | None:
    from rebuild.review import status

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        stamp = None
    return status.resolve_carry_source(ROOT, stamp, AUTOSAVE)


def describe_carry_source(resolved: dict, root: Path) -> str:
    try:
        shown = resolved["path"].relative_to(root)
    except ValueError:
        shown = resolved["path"]
    if resolved["aligned"]:
        provenance = "stamped for the served surface"
    else:
        provenance = (
            f"stamped {resolved['stamp']}, not the served surface; carry re-resolves by content and ink keys"
        )
    return (
        f"Auto-resolved carry source: {shown} ({resolved['count']} effective verdicts, {provenance}). "
        "Pass --verdicts to override."
    )


def resolve_short_id() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        head = result.stdout.strip()
        if head:
            return head
    except OSError, subprocess.SubprocessError:
        pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def server_listening(port: int = REVIEW_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _render_concurrency(plan: Plan) -> list[str]:
    if plan.skip_gates:
        return [
            "",
            "  Concurrency (--skip-gates):",
            f"    Lane build only; no gates; --jobs budget: {plan.job_budget}",
        ]
    t0_lane = "gate:js" if plan.skip_make_test else "gate:js, gate:make-test"
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        f"    Lane t0   [from t=0, background]  : {t0_lane}",
        "    Lane build[serial, main thread]  : snapshot -> run_m1 -> surface-build -> carry -> merge -> census",
        "    Lane rebuild                     : starts when run_m1's four JSONs pass;",
    ]
    if plan.skip_make_test:
        lines.append(
            "                                       gate:make-test auto-skipped (closure unchanged), so no queueing"
        )
    elif plan.pool_policy == "overlap":
        lines.append(
            "                                       CO-RESIDENT with gate:make-test (overlap policy — two 12-way pytest pools)"
        )
    else:
        lines.append("                                       QUEUED behind gate:make-test  (queue policy)")
    if plan.skip_conform:
        lines.append("    Lane conform                     : SKIPPED (--skip-conform)")
    elif plan.pool_policy == "overlap":
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; CO-RESIDENT with the pytest pools (--jobs {plan.conform_jobs})"
        )
    else:
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; QUEUED behind gate:make-test, then CO-RESIDENT with gate:rebuild's pool (--jobs {plan.conform_jobs})"
        )
    lines.append(
        f"    build-stage --jobs budget        : {plan.job_budget}  (a 12-way `make test` owns the cores)"
    )
    return lines


def render_plan(plan: Plan) -> str:
    lines = ["Artifact-cycle plan (resolved, nothing executed):", ""]
    lines.append(f"  git short id : {plan.short_id}")
    lines.append(f"  first run    : {plan.first_run}")
    lines.append(f"  snapshot dir : {plan.snapshot_dir}")
    lines.append(f"  verdicts     : {plan.verdicts if plan.verdicts is not None else '(none)'}")
    lines.append(f"  carry output : {plan.carry_out if plan.carry_out is not None else '(no carry)'}")
    if plan.review_out is not None:
        lines.append(
            f"  rehearsal    : surface writes redirected to {plan.review_out}; the live surface at rebuild/out/review is never written."
        )
    lines.append("")
    lines.append("  Steps:")
    for index, step in enumerate(plan.steps, start=1):
        if step.argv is not None:
            lines.append(f"    {index}. {step.name}: {' '.join(step.argv)}")
            if step.note:
                lines.append(f"       ({step.note})")
        else:
            lines.append(f"    {index}. {step.name}: {step.note}")
    lines.extend(_render_concurrency(plan))
    return "\n".join(lines)


@dataclass
class CycleReport:
    snapshot_dir: Path | None = None
    unmatched: int | None = None
    multi_matched: int | None = None
    boundary_pass: bool | None = None
    pins_pass: bool | None = None
    surface_units: int | None = None
    surface_rows: int | None = None
    surface_batches: int | None = None
    echo_groups: int | None = None
    carry_out: Path | None = None
    carry_lines: list[str] = field(default_factory=list)
    merge_status: str = "not run"
    merge_lines: list[str] = field(default_factory=list)
    census_status: str = "not run"
    gate_js: str = "not run"
    gate_rebuild: str = "not run"
    gate_conform: str = "not run"
    gate_make_test: str = "not run"
    interrupted: bool = False


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text())


def _cmd_label(argv: list[str]) -> str:
    parts = [token for token in argv if token not in {"uv", "run", "python", "python3"}]
    if parts and parts[0] == "-m":
        parts = parts[1:]
    return " ".join(parts[:3])


class _Emitter:
    """Whole-line-atomic, lock-serialized stdout. Every write in the concurrent region routes through here so overlapping children never splice mid-line; cross-line interleave is expected and disambiguated by the [name] prefix."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def emit(self, text: str) -> None:
        with self._lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def emit_block(self, lines: list[str]) -> None:
        with self._lock:
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()


class _ChildRegistry:
    """Thread-safe set of live subprocesses, so a KeyboardInterrupt can reap every child (no orphaned pytest army survives)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: set[subprocess.Popen] = set()
        self._closed = False
        self.killed_count = 0

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def add(self, proc: subprocess.Popen) -> bool:
        """Track a live child. Returns False once terminate_all has torn the registry down, so a worker that unblocks after a KeyboardInterrupt (the queue-mode rebuild task parked on make_fut is the case) never leaves a fresh subprocess untracked — the caller reaps it instead of spawning an orphaned pytest army."""
        with self._lock:
            if self._closed:
                return False
            self._children.add(proc)
            return True

    def remove(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._children.discard(proc)

    def terminate_all(self) -> None:
        with self._lock:
            self._closed = True
            children = list(self._children)
            self._children.clear()
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            self.killed_count += 1


@dataclass
class _StepResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    elapsed: float


def _terminate_child(proc: subprocess.Popen) -> None:
    """Terminate one child promptly (SIGTERM, 3s grace, then SIGKILL) and drain its pipes. Used only for the narrow race where the registry is torn down between a Popen and its registry.add."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def _run_step(
    name: str, argv: list[str], *, emit: _Emitter, registry: _ChildRegistry, stream: bool
) -> _StepResult:
    if registry.closed:
        return _StepResult(name, 130, "", "", 0.0)
    emit.emit(f"\n$ {' '.join(argv)}")
    start = time.perf_counter()
    proc = subprocess.Popen(
        argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1
    )
    if not registry.add(proc):
        _terminate_child(proc)
        return _StepResult(name, 130, "", "", 0.0)
    out_buf: list[str] = []
    err_buf: list[str] = []

    def pump(pipe, buf: list[str]) -> None:
        for line in pipe:
            line = line.rstrip("\r\n")
            buf.append(line)
            if stream:
                emit.emit(f"[{name}] {line}")
        pipe.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, out_buf)),
        threading.Thread(target=pump, args=(proc.stderr, err_buf)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    returncode = proc.wait()
    registry.remove(proc)
    elapsed = time.perf_counter() - start
    emit.emit(f"[t] {_cmd_label(argv)} {elapsed:.1f}s")
    return _StepResult(name, returncode, "\n".join(out_buf), "\n".join(err_buf), elapsed)


def _dump_captured(emit: _Emitter, result: _StepResult) -> None:
    lines: list[str] = []
    if result.stdout:
        lines.extend(result.stdout.splitlines())
    if result.stderr:
        lines.extend(result.stderr.splitlines())
    if lines:
        emit.emit_block(lines)


def _parse_surface_build(stderr: str) -> tuple[int, int, int] | None:
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("Wrote ") and "units," in stripped and "batches)" in stripped:
            inner = stripped[stripped.index("(") + 1 : stripped.rindex(")")]
            numbers = []
            for chunk in inner.split(","):
                token = chunk.strip().split(" ", 1)[0]
                numbers.append(int(token))
            if len(numbers) == 3:
                return numbers[0], numbers[1], numbers[2]
    return None


@dataclass
class _RebuildOutcome:
    status: str
    failures: list[str]
    hard_ids: list[str]


_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _classify_rebuild(result: _StepResult, update_pins: bool) -> _RebuildOutcome:
    """Bucket the rebuild suite's FAILED/ERROR summary lines into baseline / census-hint / hard and turn them into a gate verdict. pytest emits ANSI color whenever FORCE_COLOR is set (as it is under the agent harness), wrapping each summary line in escape codes, so strip those first — otherwise no line begins with a literal "FAILED "/"ERROR ", the documented baseline can't be subtracted, and every colored run reads as an unexplained hard failure."""
    lines = [_ANSI_SGR.sub("", line) for line in result.stdout.splitlines()]
    failed_ids = [line.split(None, 2)[1] for line in lines if line.startswith("FAILED ")]
    error_ids = [line.split(None, 2)[1] for line in lines if line.startswith("ERROR ")]
    buckets: dict[str, list[str]] = {"baseline": [], "census-hint": [], "hard": []}
    for test_id in failed_ids:
        buckets[classify_rebuild_failure(test_id, update_pins)].append(test_id)
    buckets["hard"].extend(error_ids)
    if result.returncode != 0 and not failed_ids and not error_ids:
        buckets["hard"].append(f"pytest exited {result.returncode} with no parsed FAILED/ERROR lines")
    failures: list[str] = []
    if buckets["hard"]:
        status = f"FAILED ({len(buckets['hard'])} unexplained)"
        failures.append(f"rebuild suite: {len(buckets['hard'])} unexplained failure(s)")
    else:
        parts = []
        if buckets["baseline"]:
            parts.append(f"{len(buckets['baseline'])} documented baseline")
        if buckets["census-hint"]:
            parts.append(f"{len(buckets['census-hint'])} stale census pins? (re-run with --update-pins)")
        status = "green" if not parts else "green (" + ", ".join(parts) + ")"
    return _RebuildOutcome(status=status, failures=failures, hard_ids=list(buckets["hard"]))


def _do_run_m1(
    report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, budget: int
) -> GateOutcome | None:
    for path in M1_SUMMARY_FILES.values():
        path.unlink(missing_ok=True)
    CONFORM_SUMMARY.unlink(missing_ok=True)
    argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
    if budget > 1:
        argv += ["--jobs", str(budget)]
    spawn("run_m1", argv, emit=emit, registry=registry, stream=True)
    missing = [name for name, path in M1_SUMMARY_FILES.items() if not path.exists()]
    if missing:
        for name in missing:
            emit.emit(
                f"run_m1 gate failure: missing {name} summary ({M1_SUMMARY_FILES[name]}) — run_m1 did not complete"
            )
        return None
    summaries = {name: _load_summary(path) for name, path in M1_SUMMARY_FILES.items()}
    gate = evaluate_run_m1_gate(
        summaries["pipeline"], summaries["boundary"], summaries["manual_pins"], summaries["oracle"]
    )
    report.unmatched = gate.unmatched
    report.multi_matched = gate.multi_matched
    report.boundary_pass = bool(summaries["boundary"].get("pass"))
    report.pins_pass = bool(summaries["manual_pins"].get("pass"))
    return gate


def _run_m1_reasons(gate: GateOutcome | None) -> list[str]:
    if gate is None:
        return ["run_m1 did not write all four summary files"]
    return list(gate.failures)


def _do_surface_build(
    report: CycleReport,
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    review_out: Path | None,
    budget: int,
) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.review.build"]
    if budget > 1:
        argv += ["--jobs", str(budget)]
    if review_out is not None:
        argv += ["--out", str(review_out)]
    result = spawn("surface-build", argv, emit=emit, registry=registry, stream=True)
    parsed = _parse_surface_build(result.stderr) if result.returncode == 0 else None
    if result.returncode != 0 or parsed is None:
        emit.emit(
            "ERROR: review.build did not complete cleanly (no 'Wrote ... (N units, R rows, B batches)' line)."
        )
        return False
    report.surface_units, report.surface_rows, report.surface_batches = parsed
    surface_dir = review_out if review_out is not None else REVIEW_OUT
    manifest = json.loads((surface_dir / "manifest.json").read_text())
    report.echo_groups = manifest.get("totals", {}).get("echo_groups")
    return True


def _do_carry(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = [
        "uv",
        "run",
        "python",
        str(CARRY_TOOL),
        "--source",
        str(plan.snapshot_dir),
        str(plan.verdicts),
        "--out",
        str(plan.carry_out),
    ]
    if plan.review_out is not None:
        argv += ["--current-surface", str(plan.review_out)]
    result = spawn("carry", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    report.carry_out = plan.carry_out
    for line in result.stdout.splitlines():
        if any(word in line for word in ("carried", "kinds", "queue", "fallback")):
            report.carry_lines.append(line.strip())
    return result.returncode == 0


def _do_merge(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(plan.carry_out)]
    result = spawn("merge", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("merged ", "nothing changed", "stashed ")):
            report.merge_lines.append(stripped)
    report.merge_status = "merged" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_census(*, spawn, emit: _Emitter, registry: _ChildRegistry, update_pins: bool, surface: Path) -> str:
    if update_pins:
        census = spawn(
            "census",
            ["uv", "run", "python", "-m", "rebuild.review.census", "--update", "--surface", str(surface)],
            emit=emit,
            registry=registry,
            stream=False,
        )
        _dump_captured(emit, census)
        diff = spawn(
            "git-diff",
            ["git", "diff", "--", "rebuild/review-census-pins.json"],
            emit=emit,
            registry=registry,
            stream=False,
        )
        _dump_captured(emit, diff)
        if census.returncode != 0:
            return "update FAILED"
        if diff.stdout.strip():
            return "updated (diff shown above — review every moved number)"
        return "updated (no change)"
    census = spawn(
        "census",
        ["uv", "run", "python", "-m", "rebuild.review.census", "--check", "--surface", str(surface)],
        emit=emit,
        registry=registry,
        stream=False,
    )
    _dump_captured(emit, census)
    if census.returncode == 0:
        return "clean"
    return "STALE (informational — re-run with --update-pins or edit by hand)"


def _gate_js_task(spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:js", jstest_argv(), emit=emit, registry=registry, stream=False)


def _gate_make_test_task(spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:make-test", ["make", "test"], emit=emit, registry=registry, stream=True)


def _gate_conform_task(
    pool_policy: str,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> tuple[str, list[str]]:
    """gate:conform shapes the exhaustive font-vs-settle sweep against the fresh M1.otf via run_m1 --conform-only. Under the queue policy it parks behind gate:make-test — the head of the make-test -> rebuild chain — so its per-config process pool spins up once the t=0 make-test pool has drained, co-resident with gate:rebuild's pytest pool. Conform is short after the depth-4 pruning, so queueing it behind gate:rebuild too would only pile that pool's runtime onto the critical path; it stays a process pool, not a pytest pool, so the one-12-way-pytest-pool-at-a-time invariant (make-test then rebuild, guarded by gate:rebuild's own wait on make_fut) is untouched. The stale conform_summary.json was unlinked before run_m1 started, so the verdict here can only come from this cycle's subprocess."""
    if pool_policy == "queue" and make_fut is not None:
        try:
            make_fut.result()
        except Exception:
            pass
    result = spawn("gate:conform", argv, emit=emit, registry=registry, stream=False)
    summary = None
    if CONFORM_SUMMARY.exists():
        try:
            summary = json.loads(CONFORM_SUMMARY.read_text())
        except ValueError:
            summary = None
    status, failures = evaluate_conform_gate(summary)
    if result.returncode != 0 and not failures:
        status = f"FAILED (exit {result.returncode})"
        failures = [f"conform gate: exited {result.returncode} despite a passing summary"]
    return status, failures


def _gate_rebuild_task(
    pool_policy: str,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    update_pins: bool,
) -> _RebuildOutcome:
    if pool_policy == "queue" and make_fut is not None:
        try:
            make_fut.result()
        except Exception:
            pass
    result = spawn(
        "gate:rebuild",
        ["uv", "run", "pytest", "rebuild/", "-n", "auto", "--dist", "worksteal", "-q", "--tb=no", "-rfE"],
        emit=emit,
        registry=registry,
        stream=False,
    )
    return _classify_rebuild(result, update_pins)


def _gate_result(fut: Future, name: str, failures: list[str]):
    try:
        return fut.result()
    except Exception as exc:
        failures.append(f"{name} raised: {exc!r}")
        return None


def _join_gates(
    report: CycleReport,
    failures: list[str],
    js_fut: Future | None,
    rebuild_fut: Future | None,
    conform_fut: Future | None,
    make_fut: Future | None,
    update_pins: bool,
    emit: _Emitter,
) -> None:
    if js_fut is not None:
        js = _gate_result(js_fut, "gate:js", failures)
        if js is None:
            report.gate_js = "FAILED (exception)"
        else:
            report.gate_js = "green" if js.returncode == 0 else f"FAILED (exit {js.returncode})"
            if js.returncode != 0:
                failures.append("JS suite failed")
    if rebuild_fut is not None:
        outcome = _gate_result(rebuild_fut, "gate:rebuild", failures)
        if outcome is None:
            report.gate_rebuild = "FAILED (exception)"
        else:
            report.gate_rebuild = outcome.status
            for test_id in outcome.hard_ids:
                emit.emit(f"  hard rebuild failure: {test_id}")
            failures.extend(outcome.failures)
    if conform_fut is not None:
        conform = _gate_result(conform_fut, "gate:conform", failures)
        if conform is None:
            report.gate_conform = "FAILED (exception)"
        else:
            status, conform_failures = conform
            report.gate_conform = status
            failures.extend(conform_failures)
    if make_fut is not None:
        make = _gate_result(make_fut, "gate:make-test", failures)
        if make is None:
            report.gate_make_test = "FAILED (exception)"
        else:
            report.gate_make_test = "green" if make.returncode == 0 else f"FAILED (exit {make.returncode})"
            if make.returncode != 0:
                failures.append("make test failed")


def _run_cycle(
    plan: Plan, report: CycleReport, emit: _Emitter, registry: _ChildRegistry, spawn=_run_step
) -> int:
    pool = ThreadPoolExecutor(max_workers=_GATE_POOL_WORKERS)
    failures: list[str] = []
    try:
        js_fut = None if plan.skip_gates else pool.submit(_gate_js_task, spawn, emit, registry)
        make_fut = (
            None
            if plan.skip_gates or plan.skip_make_test
            else pool.submit(_gate_make_test_task, spawn, emit, registry)
        )
        rebuild_fut: Future | None = None
        conform_fut: Future | None = None
        if not plan.skip_gates and plan.skip_conform:
            report.gate_conform = "skipped (--skip-conform)"
        if not plan.skip_gates and plan.skip_make_test:
            report.gate_make_test = f"skipped ({plan.make_test_note})"

        gate = _do_run_m1(report, spawn=spawn, emit=emit, registry=registry, budget=plan.job_budget)
        if gate is None or not gate.ok:
            failures.extend(_run_m1_reasons(gate))
            report.gate_rebuild = "not run (run_m1 gate failed)"
            if not plan.skip_gates and not plan.skip_conform:
                report.gate_conform = "not run (run_m1 gate failed)"
            _join_gates(report, failures, js_fut, None, None, make_fut, plan.update_pins, emit)
            return _finish(report, failures, plan)

        if not plan.skip_gates:
            rebuild_fut = pool.submit(
                _gate_rebuild_task, plan.pool_policy, make_fut, spawn, emit, registry, plan.update_pins
            )
            if not plan.skip_conform:
                conform_fut = pool.submit(
                    _gate_conform_task,
                    plan.pool_policy,
                    make_fut,
                    spawn,
                    emit,
                    registry,
                    conform_gate_argv(plan.conform_jobs, plan.conform_horizon),
                )

        if not _do_surface_build(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            review_out=plan.review_out,
            budget=plan.job_budget,
        ):
            failures.append("surface rebuild failed")
            _join_gates(report, failures, js_fut, rebuild_fut, conform_fut, make_fut, plan.update_pins, emit)
            return _finish(report, failures, plan)

        if plan.carry_out is not None:
            carried = _do_carry(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
            if not carried:
                failures.append("carry_verdicts failed")
            if plan.do_merge:
                if not carried:
                    report.merge_status = "not run (carry failed)"
                elif not _do_merge(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("verdict merge failed")
        report.census_status = _do_census(
            spawn=spawn,
            emit=emit,
            registry=registry,
            update_pins=plan.update_pins,
            surface=plan.census_surface,
        )

        _join_gates(report, failures, js_fut, rebuild_fut, conform_fut, make_fut, plan.update_pins, emit)
        return _finish(report, failures, plan)
    except KeyboardInterrupt:
        registry.terminate_all()
        pool.shutdown(wait=False, cancel_futures=True)
        report.interrupted = True
        return _finish_interrupted(report, failures, registry.killed_count, plan)
    finally:
        pool.shutdown(wait=True)


def _print_summary(report: CycleReport) -> None:
    def show(value: object) -> str:
        return "—" if value is None else str(value)

    print("\n" + "=" * 68)
    print("ARTIFACT CYCLE SUMMARY")
    print("=" * 68)
    print(f"  snapshot dir       : {show(report.snapshot_dir)}")
    print(f"  oracle UNMATCHED   : {show(report.unmatched)} (informational)")
    print(f"  oracle multi_match : {show(report.multi_matched)}")
    print(f"  boundary gate      : {'pass' if report.boundary_pass else show(report.boundary_pass)}")
    print(f"  Manual-pin gate    : {'pass' if report.pins_pass else show(report.pins_pass)}")
    print(f"  surface units      : {show(report.surface_units)}")
    print(f"  surface rows       : {show(report.surface_rows)}")
    print(f"  surface batches    : {show(report.surface_batches)}")
    print(f"  echo groups        : {show(report.echo_groups)}")
    print(f"  carry output       : {show(report.carry_out)}")
    for line in report.carry_lines:
        print(f"      {line}")
    print(f"  merge -> autosave  : {report.merge_status}")
    for line in report.merge_lines:
        print(f"      {line}")
    print(f"  census pins        : {report.census_status}")
    print(f"  gate: JS suite     : {report.gate_js}")
    print(f"  gate: rebuild      : {report.gate_rebuild}")
    print(f"  gate: conform      : {report.gate_conform}")
    print(f"  gate: make test    : {report.gate_make_test}")
    print("  run_m1 summaries   :")
    for path in M1_SUMMARY_FILES.values():
        print(f"      {path}")
    print(f"      {CONFORM_SUMMARY}")
    print("=" * 68)


def _as_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _gate_entry(status: str) -> dict:
    return {"status": status, "green": status.startswith("green")}


def _surface_block(surface_dir: Path) -> dict:
    block: dict = {"dir": str(surface_dir), "generated_at": None, "inputs_fingerprint": None}
    try:
        manifest = json.loads((surface_dir / "manifest.json").read_text())
        block["generated_at"] = manifest.get("generated_at")
        block["inputs_fingerprint"] = manifest.get("inputs_fingerprint")
    except Exception:
        pass
    return block


def cycle_summary_payload(report: CycleReport, failures: list[str], plan: Plan, exit_kind: str) -> dict:
    return {
        "format": "ams-cycle-summary/1",
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "exit": exit_kind,
        "failures": list(failures),
        "gates": {
            "js": _gate_entry(report.gate_js),
            "rebuild": _gate_entry(report.gate_rebuild),
            "conform": _gate_entry(report.gate_conform),
            "make_test": _gate_entry(report.gate_make_test),
        },
        "make_test_fingerprint": (
            plan.make_test_fingerprint
            if report.gate_make_test.startswith("green") or plan.skip_make_test
            else None
        ),
        "unmatched": report.unmatched,
        "multi_matched": report.multi_matched,
        "boundary_pass": report.boundary_pass,
        "pins_pass": report.pins_pass,
        "surface_units": report.surface_units,
        "surface_rows": report.surface_rows,
        "surface_batches": report.surface_batches,
        "echo_groups": report.echo_groups,
        "carry_out": _as_str(report.carry_out),
        "carry_lines": list(report.carry_lines),
        "merge_status": report.merge_status,
        "merge_lines": list(report.merge_lines),
        "census_status": report.census_status,
        "snapshot_dir": _as_str(report.snapshot_dir),
        "interrupted": report.interrupted,
        "plan": {
            "verdicts": _as_str(plan.verdicts),
            "carry_out": _as_str(plan.carry_out),
            "do_merge": plan.do_merge,
            "conform_horizon": plan.conform_horizon,
            "pool_policy": plan.pool_policy,
            "skip_gates": plan.skip_gates,
            "skip_conform": plan.skip_conform,
            "update_pins": plan.update_pins,
            "review_out": _as_str(plan.review_out),
            "first_run": plan.first_run,
            "short_id": plan.short_id,
        },
        "argv": list(sys.argv),
        "surface": _surface_block(plan.census_surface),
    }


def write_cycle_summary(payload: dict) -> None:
    target = CYCLE_SUMMARY
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, target)


def _emit_cycle_summary(report: CycleReport, failures: list[str], plan: Plan, exit_kind: str) -> None:
    try:
        write_cycle_summary(cycle_summary_payload(report, failures, plan, exit_kind))
    except Exception as exc:
        print(f"warning: failed to write {CYCLE_SUMMARY}: {exc!r}", file=sys.stderr)


def _preflight(args: argparse.Namespace) -> bool:
    if args.review_out is not None:
        print(
            f"Rehearsal mode: surface writes redirected to {args.review_out}; the live surface at rebuild/out/review is never written."
        )
        return True
    if not server_listening():
        return True
    if args.yes:
        print("=" * 68)
        print("WARNING: a review server is listening on 127.0.0.1:7294.")
        print("Proceeding with --yes. The in-place surface rebuild will restamp the")
        print("manifest and rewrite the shards under it, stranding the live verdicting")
        print("session. AFTER this cycle you MUST:")
        print("  1. restart the review server:  uv run python -m rebuild.review.serve")
        print("  2. reload the app (the carried verdicts are merged into the autosave automatically).")
        print("=" * 68)
        return True
    print("=" * 68)
    print("REFUSING TO RUN: a review server is listening on 127.0.0.1:7294.")
    print("The in-place surface rebuild would strand your live verdicting session")
    print("(livereload rewrites the shards and the manifest restamp orphans the")
    print("autosave). Before re-running:")
    print("  1. in the review app, export or confirm the autosave of your verdicts")
    print("  2. stop the review server")
    print("  3. re-run this command (or pass --yes to override at your own risk)")
    print("  (or pass --review-out <dir> to rehearse without touching the live surface)")
    print("=" * 68)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the commit-time artifact cycle: snapshot, run_m1, surface rebuild, carry, census pins, gates."
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        help="prior verdicts master to carry forward (default: auto-resolve the best candidate among the autosave and the verdicts-*.json files at the repo root and under rebuild/evidence)",
    )
    parser.add_argument("--no-carry", action="store_true", help="skip the verdict carry-forward step")
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="leave verdicts-autosave.json untouched after the carry (skip the automatic merge into the live store)",
    )
    parser.add_argument(
        "--carry-out",
        type=Path,
        help="carried-forward output path (default: verdicts-carried-<short hash>.json at the repo root)",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="where to snapshot the current surface (default: tmp/review-pre-<short hash>)",
    )
    parser.add_argument(
        "--update-pins",
        action="store_true",
        help="re-baseline the census pins and print their git diff (default: check only, report staleness)",
    )
    parser.add_argument(
        "--skip-gates",
        action="store_true",
        help="skip the four post-build gates (JS suite, rebuild suite, conformance sweep, make test)",
    )
    parser.add_argument(
        "--skip-conform",
        action="store_true",
        help="skip gate:conform (the exhaustive font-vs-settle sweep) while keeping the other gates",
    )
    parser.add_argument(
        "--force-make-test",
        action="store_true",
        help="run gate:make-test even when its input closure is unchanged since its last green run (the auto-skip)",
    )
    parser.add_argument(
        "--conform-horizon",
        type=int,
        default=CONFORM_HORIZON_DEFAULT,
        help="exhaustive sweep length for gate:conform, passed through to run_m1 --conform-only; drop below 5 when the sweep becomes the cycle's long pole — witness top-ups keep rule/transition coverage exact at any horizon",
    )
    parser.add_argument(
        "--rebuild-pool",
        choices=POOL_POLICIES,
        default=REBUILD_POOL_POLICY_DEFAULT,
        help="how gate:rebuild shares cores with make test: 'queue' (one 12-way pool at a time, default) or 'overlap' (co-resident)",
    )
    parser.add_argument(
        "--review-out",
        type=Path,
        default=None,
        help="rehearsal mode: redirect the surface write to this dir so the cycle can run while the live server is up",
    )
    parser.add_argument("--yes", action="store_true", help="override the running-review-server refusal")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved step plan and exit without executing anything",
    )
    args = parser.parse_args(argv)

    first_run = not (REVIEW_OUT / "manifest.json").exists()

    skip_make_test = False
    make_test_note = ""
    make_test_fp: str | None = None
    if not args.skip_gates:
        make_test_fp = make_test_closure_fingerprint(ROOT)
        if (
            not args.force_make_test
            and make_test_fp is not None
            and make_test_fp == prior_make_test_fingerprint()
        ):
            skip_make_test = True
            make_test_note = "closure unchanged since its last green run; --force-make-test overrides"
            print(f"gate:make-test auto-skipped: {make_test_note}")

    if not args.no_carry and args.verdicts is None and not first_run:
        resolved = resolve_carry_source()
        if resolved is None:
            args.no_carry = True
            print(
                "No carryable verdicts found (neither the autosave nor any verdicts-*.json at the repo root or under rebuild/evidence holds an effective verdict); proceeding without carry. Pass --verdicts to name a master explicitly."
            )
        else:
            args.verdicts = resolved["path"]
            print(describe_carry_source(resolved, ROOT))

    if args.dry_run:
        plan = build_plan(
            verdicts=args.verdicts,
            no_carry=args.no_carry,
            carry_out=args.carry_out,
            snapshot_dir=args.snapshot_dir,
            update_pins=args.update_pins,
            skip_gates=args.skip_gates,
            first_run=first_run,
            short_id=resolve_short_id(),
            no_merge=args.no_merge,
            skip_conform=args.skip_conform,
            skip_make_test=skip_make_test,
            make_test_note=make_test_note,
            make_test_fingerprint=make_test_fp,
            conform_horizon=args.conform_horizon,
            pool_policy=args.rebuild_pool,
            review_out=args.review_out,
        )
        print(render_plan(plan))
        return 0

    if not _preflight(args):
        return 2

    if first_run:
        print("First-run mode: no existing surface at rebuild/out/review — skipping snapshot and carry.")

    plan = build_plan(
        verdicts=args.verdicts,
        no_carry=args.no_carry,
        carry_out=args.carry_out,
        snapshot_dir=args.snapshot_dir,
        update_pins=args.update_pins,
        skip_gates=args.skip_gates,
        first_run=first_run,
        short_id=resolve_short_id(),
        no_merge=args.no_merge,
        skip_conform=args.skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fp,
        conform_horizon=args.conform_horizon,
        pool_policy=args.rebuild_pool,
        review_out=args.review_out,
    )

    report = CycleReport()

    if not first_run:
        if plan.snapshot_dir.exists():
            print(f"ERROR: snapshot dir already exists: {plan.snapshot_dir}")
            print("Refusing to overwrite the only recovery copy. Remove it or pass --snapshot-dir.")
            return 2
        shutil.copytree(REVIEW_OUT, plan.snapshot_dir)
        report.snapshot_dir = plan.snapshot_dir
        print(f"Snapshotted {REVIEW_OUT} -> {plan.snapshot_dir}")

    emit = _Emitter()
    registry = _ChildRegistry()
    return _run_cycle(plan, report, emit, registry)


def _finish(report: CycleReport, failures: list[str], plan: Plan) -> int:
    _print_summary(report)
    _emit_cycle_summary(report, failures, plan, "failed" if failures else "ok")
    if failures:
        print("\nCYCLE FAILED:")
        for reason in failures:
            print(f"  - {reason}")
        return 1
    print("\nCycle complete.")
    return 0


def _finish_interrupted(report: CycleReport, failures: list[str], killed_count: int, plan: Plan) -> int:
    _print_summary(report)
    _emit_cycle_summary(report, failures, plan, "interrupted")
    print(f"\nCYCLE INTERRUPTED (SIGINT): terminated {killed_count} child process(es).")
    return 130


if __name__ == "__main__":
    sys.exit(main())
