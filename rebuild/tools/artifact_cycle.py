"""The one-command driver for the commit-time artifact cycle.

It mechanizes the sequence documented in rebuild/VERDICT-APPLICATION-PROGRESS.md: snapshot the current review surface (the only recovery copy, since everything under rebuild/out is gitignored), recompile M1.otf and vet it, rebuild the review surface in place, carry prior verdicts forward onto the fresh manifest, re-baseline the census pins, and run the three gates — always printing a summary table at the end, even on failure.

The exit-code trap this driver exists to defuse: run_m1.main() SystemExits nonzero whenever any oracle rows are UNMATCHED, which is always true mid-migration. Its exit code is therefore not the gate; the four summary JSONs it writes are. The real gates are defect_errors, the boundary and Manual-pin passes, and multi_matched == 0.

Run as: uv run python rebuild/tools/artifact_cycle.py --verdicts verdicts-X.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CENSUS_PINS = ROOT / "rebuild" / "review-census-pins.json"
CARRY_TOOL = ROOT / "rebuild" / "tools" / "carry_verdicts.py"
JSTEST_DIR = ROOT / "rebuild" / "review" / "jstests"
REVIEW_PORT = 7294

M1_SUMMARY_FILES = {
    "pipeline": M1_OUT / "pipeline_summary.json",
    "boundary": M1_OUT / "boundary_equivalence_summary.json",
    "manual_pins": M1_OUT / "manual_pins_summary.json",
    "oracle": M1_OUT / "oracle_summary.json",
}

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
        failures.append(f"Manual-pin gate failed ({len(manual_pins.get('disagreements') or [])} disagreements)")

    multi_matched = oracle.get("multi_matched")
    if multi_matched is not None and multi_matched > 0:
        failures.append(f"oracle multi_matched = {multi_matched} (must be 0)")

    return GateOutcome(
        ok=not failures,
        failures=failures,
        unmatched=oracle.get("unmatched"),
        multi_matched=multi_matched,
    )


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


@dataclass
class Plan:
    short_id: str
    first_run: bool
    snapshot_dir: Path
    carry_out: Path | None
    verdicts: Path | None
    update_pins: bool
    skip_gates: bool
    steps: list[Step] = field(default_factory=list)


def jstest_argv() -> list[str]:
    """The JS suite argv. The *.test.js glob form is required — node v26 rejects the bare-directory form with 'Cannot find module' — and the glob is expanded in Python, never handed to a shell."""
    files = sorted(str(path.relative_to(ROOT)) for path in JSTEST_DIR.glob("*.test.js"))
    return ["node", "--test", *files]


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
) -> Plan:
    resolved_snapshot = snapshot_dir if snapshot_dir is not None else ROOT / "tmp" / f"review-pre-{short_id}"
    do_carry = not no_carry and not first_run
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"

    plan = Plan(
        short_id=short_id,
        first_run=first_run,
        snapshot_dir=resolved_snapshot,
        carry_out=resolved_carry_out,
        verdicts=verdicts,
        update_pins=update_pins,
        skip_gates=skip_gates,
    )

    if first_run:
        plan.steps.append(Step("snapshot", None, "SKIPPED (first run: no existing surface to snapshot)"))
    else:
        plan.steps.append(Step("snapshot", None, f"copytree {REVIEW_OUT} -> {resolved_snapshot}"))

    plan.steps.append(Step("run_m1", ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]))
    plan.steps.append(Step("surface-build", ["uv", "run", "python", "-m", "rebuild.review.build"]))

    if do_carry:
        assert resolved_carry_out is not None
        plan.steps.append(
            Step(
                "carry",
                [
                    "uv",
                    "run",
                    "python",
                    str(CARRY_TOOL),
                    "--source",
                    str(resolved_snapshot),
                    str(verdicts),
                    "--out",
                    str(resolved_carry_out),
                ],
            )
        )
    elif first_run:
        plan.steps.append(Step("carry", None, "SKIPPED (first run)"))
    else:
        plan.steps.append(Step("carry", None, "SKIPPED (--no-carry)"))

    census_mode = "--update" if update_pins else "--check"
    plan.steps.append(
        Step(
            "census",
            ["uv", "run", "python", "-m", "rebuild.review.census", census_mode, "--surface", str(REVIEW_OUT)],
            "then `git diff -- rebuild/review-census-pins.json`, printed in full" if update_pins else "staleness reported informationally",
        )
    )

    if skip_gates:
        plan.steps.append(Step("gates", None, "SKIPPED (--skip-gates)"))
    else:
        plan.steps.append(Step("gate:js", jstest_argv()))
        plan.steps.append(
            Step(
                "gate:rebuild",
                ["uv", "run", "pytest", "rebuild/", "-n", "auto", "--dist", "worksteal", "-q", "--tb=no", "-rfE"],
            )
        )
        plan.steps.append(Step("gate:make-test", ["make", "test"]))

    return plan


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
    except (OSError, subprocess.SubprocessError):
        pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def server_listening(port: int = REVIEW_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def render_plan(plan: Plan) -> str:
    lines = ["Artifact-cycle plan (resolved, nothing executed):", ""]
    lines.append(f"  git short id : {plan.short_id}")
    lines.append(f"  first run    : {plan.first_run}")
    lines.append(f"  snapshot dir : {plan.snapshot_dir}")
    lines.append(f"  verdicts     : {plan.verdicts if plan.verdicts is not None else '(none)'}")
    lines.append(f"  carry output : {plan.carry_out if plan.carry_out is not None else '(no carry)'}")
    lines.append("")
    lines.append("  Steps:")
    for index, step in enumerate(plan.steps, start=1):
        if step.argv is not None:
            lines.append(f"    {index}. {step.name}: {' '.join(step.argv)}")
            if step.note:
                lines.append(f"       ({step.note})")
        else:
            lines.append(f"    {index}. {step.name}: {step.note}")
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
    census_status: str = "not run"
    gate_js: str = "not run"
    gate_rebuild: str = "not run"
    gate_make_test: str = "not run"


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text())


def _stream(argv: list[str]) -> int:
    print(f"\n$ {' '.join(argv)}", flush=True)
    return subprocess.run(argv, cwd=ROOT).returncode


def _capture(argv: list[str]) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(argv)}", flush=True)
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result


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


def _run_gates(report: CycleReport, update_pins: bool) -> list[str]:
    failures: list[str] = []

    js = _stream(jstest_argv())
    report.gate_js = "green" if js == 0 else f"FAILED (exit {js})"
    if js != 0:
        failures.append("JS suite failed")

    rebuild = _capture(
        ["uv", "run", "pytest", "rebuild/", "-n", "auto", "--dist", "worksteal", "-q", "--tb=no", "-rfE"]
    )
    lines = rebuild.stdout.splitlines()
    failed_ids = [line.split(None, 2)[1] for line in lines if line.startswith("FAILED ")]
    error_ids = [line.split(None, 2)[1] for line in lines if line.startswith("ERROR ")]
    buckets = {"baseline": [], "census-hint": [], "hard": []}
    for test_id in failed_ids:
        buckets[classify_rebuild_failure(test_id, update_pins)].append(test_id)
    buckets["hard"].extend(error_ids)
    if rebuild.returncode != 0 and not failed_ids and not error_ids:
        buckets["hard"].append(f"pytest exited {rebuild.returncode} with no parsed FAILED/ERROR lines")
    if buckets["hard"]:
        report.gate_rebuild = f"FAILED ({len(buckets['hard'])} unexplained)"
        failures.append(f"rebuild suite: {len(buckets['hard'])} unexplained failure(s)")
        for test_id in buckets["hard"]:
            print(f"  hard rebuild failure: {test_id}")
    else:
        parts = []
        if buckets["baseline"]:
            parts.append(f"{len(buckets['baseline'])} documented baseline")
        if buckets["census-hint"]:
            parts.append(f"{len(buckets['census-hint'])} stale census pins? (re-run with --update-pins)")
        report.gate_rebuild = "green" if not parts else "green (" + ", ".join(parts) + ")"

    make_test = _stream(["make", "test"])
    report.gate_make_test = "green" if make_test == 0 else f"FAILED (exit {make_test})"
    if make_test != 0:
        failures.append("make test failed")

    return failures


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
    print(f"  census pins        : {report.census_status}")
    print(f"  gate: JS suite     : {report.gate_js}")
    print(f"  gate: rebuild      : {report.gate_rebuild}")
    print(f"  gate: make test    : {report.gate_make_test}")
    print("  run_m1 summaries   :")
    for path in M1_SUMMARY_FILES.values():
        print(f"      {path}")
    print("=" * 68)


def _preflight(args: argparse.Namespace) -> bool:
    if not server_listening():
        return True
    if args.yes:
        print("=" * 68)
        print("WARNING: a review server is listening on 127.0.0.1:7294.")
        print("Proceeding with --yes. The in-place surface rebuild will restamp the")
        print("manifest and rewrite the shards under it, stranding the live verdicting")
        print("session. AFTER this cycle you MUST:")
        print("  1. restart the review server:  uv run python -m rebuild.review.serve")
        print("  2. import the fresh carried verdicts file printed below.")
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
    print("=" * 68)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the commit-time artifact cycle: snapshot, run_m1, surface rebuild, carry, census pins, gates."
    )
    parser.add_argument("--verdicts", type=Path, help="prior verdicts master to carry forward (required unless --no-carry)")
    parser.add_argument("--no-carry", action="store_true", help="skip the verdict carry-forward step")
    parser.add_argument("--carry-out", type=Path, help="carried-forward output path (default: verdicts-carried-<short hash>.json at the repo root)")
    parser.add_argument("--snapshot-dir", type=Path, help="where to snapshot the current surface (default: tmp/review-pre-<short hash>)")
    parser.add_argument("--update-pins", action="store_true", help="re-baseline the census pins and print their git diff (default: check only, report staleness)")
    parser.add_argument("--skip-gates", action="store_true", help="skip the three post-build gates (JS suite, rebuild suite, make test)")
    parser.add_argument("--yes", action="store_true", help="override the running-review-server refusal")
    parser.add_argument("--dry-run", action="store_true", help="print the resolved step plan and exit without executing anything")
    args = parser.parse_args(argv)

    first_run = not (REVIEW_OUT / "manifest.json").exists()

    if not args.no_carry and args.verdicts is None and not first_run:
        parser.error("--verdicts is required unless --no-carry (refusing to guess which master to carry)")

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
    )

    report = CycleReport()
    failures: list[str] = []

    if not first_run:
        if plan.snapshot_dir.exists():
            print(f"ERROR: snapshot dir already exists: {plan.snapshot_dir}")
            print("Refusing to overwrite the only recovery copy. Remove it or pass --snapshot-dir.")
            return 2
        shutil.copytree(REVIEW_OUT, plan.snapshot_dir)
        report.snapshot_dir = plan.snapshot_dir
        print(f"Snapshotted {REVIEW_OUT} -> {plan.snapshot_dir}")

    for path in M1_SUMMARY_FILES.values():
        path.unlink(missing_ok=True)
    _stream(["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"])
    missing = [name for name, path in M1_SUMMARY_FILES.items() if not path.exists()]
    if missing:
        for name in missing:
            print(f"run_m1 gate failure: missing {name} summary ({M1_SUMMARY_FILES[name]}) — run_m1 did not complete")
        failures.append(f"run_m1 did not write {len(missing)} summary file(s): {', '.join(missing)}")
        return _finish(report, failures)
    summaries = {name: _load_summary(path) for name, path in M1_SUMMARY_FILES.items()}
    gate = evaluate_run_m1_gate(
        summaries["pipeline"], summaries["boundary"], summaries["manual_pins"], summaries["oracle"]
    )
    report.unmatched = gate.unmatched
    report.multi_matched = gate.multi_matched
    report.boundary_pass = bool(summaries["boundary"].get("pass"))
    report.pins_pass = bool(summaries["manual_pins"].get("pass"))
    if not gate.ok:
        for reason in gate.failures:
            print(f"run_m1 gate failure: {reason}")
        failures.extend(gate.failures)
        return _finish(report, failures)

    build = _capture(["uv", "run", "python", "-m", "rebuild.review.build"])
    parsed = _parse_surface_build(build.stderr) if build.returncode == 0 else None
    if build.returncode != 0 or parsed is None:
        print("ERROR: review.build did not complete cleanly (no 'Wrote ... (N units, R rows, B batches)' line).")
        failures.append("surface rebuild failed")
        return _finish(report, failures)
    report.surface_units, report.surface_rows, report.surface_batches = parsed
    manifest = json.loads((REVIEW_OUT / "manifest.json").read_text())
    report.echo_groups = manifest.get("totals", {}).get("echo_groups")

    if plan.carry_out is not None:
        carry = _capture(
            [
                "uv",
                "run",
                "python",
                str(CARRY_TOOL),
                "--source",
                str(plan.snapshot_dir),
                str(args.verdicts),
                "--out",
                str(plan.carry_out),
            ]
        )
        report.carry_out = plan.carry_out
        for line in carry.stdout.splitlines():
            if any(word in line for word in ("carried", "kinds", "queue", "fallback")):
                report.carry_lines.append(line.strip())
        if carry.returncode != 0:
            failures.append("carry_verdicts failed")

    report.census_status = _run_census(args.update_pins)

    if not args.skip_gates:
        failures.extend(_run_gates(report, args.update_pins))

    return _finish(report, failures)


def _run_census(update_pins: bool) -> str:
    if update_pins:
        census = _capture(
            ["uv", "run", "python", "-m", "rebuild.review.census", "--update", "--surface", str(REVIEW_OUT)]
        )
        diff = _capture(["git", "diff", "--", "rebuild/review-census-pins.json"])
        if census.returncode != 0:
            return "update FAILED"
        if diff.stdout.strip():
            return "updated (diff shown above — review every moved number)"
        return "updated (no change)"
    census = _capture(
        ["uv", "run", "python", "-m", "rebuild.review.census", "--check", "--surface", str(REVIEW_OUT)]
    )
    if census.returncode == 0:
        return "clean"
    return "STALE (informational — re-run with --update-pins or edit by hand)"


def _finish(report: CycleReport, failures: list[str]) -> int:
    _print_summary(report)
    if failures:
        print("\nCYCLE FAILED:")
        for reason in failures:
            print(f"  - {reason}")
        return 1
    print("\nCycle complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
