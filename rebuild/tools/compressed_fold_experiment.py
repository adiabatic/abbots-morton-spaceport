"""Measure the compressed-fold experiment without writing the live M1 artifact tree.

Run this module from the isolated source clone under ``var/keep/issue302/source``. ``run`` launches a measured ``child`` process against an explicitly named cache directory, ``ladder`` drives the existing scaling sweep into kept evidence, and ``compare`` checks the production and prototype artifacts. The child mirrors the full ``run_m1`` build and its table gates, Manual pins, and oracle while keeping every generated M1 artifact under the experiment root.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from rebuild.pipeline import baseline_subset, conform, fingerprint, kernel_exec, oracle, run_m1
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import artifact_cycle, console, memory_budget, peak_rss

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT_ROOT = (
    ROOT.parent
    if ROOT.name == "source" and ROOT.parent.name == "issue302"
    else ROOT / "var" / "keep" / "issue302"
)
FORMAT = "ams-compressed-fold-experiment/1"
ARMS = ("production", "prototype")
PROTOTYPE_ENV = "AMS_COMPRESSED_FOLD"
ARTIFACT_PATTERNS = (
    "settlement-*.tsv",
    "treaties-*.tsv",
    "windows-*.tsv.gz",
    "table-digests.json",
    "settle-fold.ndjson",
    "M1.generated.fea",
    "M1.otf",
    "pipeline_summary.json",
    "readback_summary.json",
    "replay_summary.json",
    "witness_summary.json",
    "emitted_order_summary.json",
    "oracle_summary.json",
    "manual_pins_summary.json",
    "ligature_outgoing_summary.json",
)
SOURCE_PATCH_PATHS = (
    "rebuild/kernel-rs",
    "rebuild/pipeline",
    "rebuild/tools/compressed_fold_experiment.py",
    "rebuild/test_compressed_fold_experiment.py",
    "rebuild/COMPRESSED-FOLD-PLAN.md",
    "glyph_data/runes/qsPea.yaml",
)
PEA_EDIT_OLD = """  - stance: half
    when:
      right:
        family: qsIt
        then: {family: qsEt}
"""
PEA_EDIT_NEW = """  - stance: half
    when:
      right:
        family: qsIt
        then: {family: qsEight}
"""


def sha256_path(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def utc_stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def git_text(*arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def source_record() -> dict[str, Any]:
    diff = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--no-textconv", "--", *SOURCE_PATCH_PATHS],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    untracked = [
        ROOT / line
        for line in git_text(
            "ls-files", "--others", "--exclude-standard", "--", *SOURCE_PATCH_PATHS
        ).splitlines()
        if line
    ]
    untracked_lines = [f"{path.relative_to(ROOT)}\t{sha256_path(path)}" for path in sorted(untracked)]
    return {
        "root": str(ROOT),
        "commit": git_text("rev-parse", "HEAD"),
        "dirty": bool(git_text("status", "--short")),
        "tracked_patch_sha256": sha256_bytes(diff),
        "untracked_sources": untracked_lines,
        "untracked_sources_sha256": sha256_bytes("\n".join(untracked_lines).encode()),
    }


def machine_record() -> dict[str, Any]:
    return {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": memory_budget.usable_cores(),
        "mem_total_bytes": memory_budget.total_memory_bytes(),
    }


def width_record() -> dict[str, int]:
    return {
        "kernel_threads": min(
            kernel_exec.kernel_threads_default(),
            len(conform.SETTLEMENT_CONFIGS),
            memory_budget.usable_cores(),
        ),
        "replay_threads": min(
            kernel_exec.replay_threads_default(),
            len(conform.SETTLEMENT_CONFIGS),
            memory_budget.usable_cores(),
        ),
        "oracle_jobs": artifact_cycle.sweep_job_budget(),
    }


def stream_record(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha256_path(path)}


def gzip_logical_record(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with gzip.open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {"logical_bytes": size, "logical_sha256": digest.hexdigest()}


def file_record(path: Path) -> dict[str, Any]:
    record = stream_record(path)
    if path.name.startswith("windows-"):
        record.update(gzip_logical_record(path))
    if path.name == "M1.otf":
        record["semantic_sha256"] = fingerprint.font_content_digest(path)
    return record


def artifact_snapshot(out_dir: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for pattern in ARTIFACT_PATTERNS:
        for path in sorted(out_dir.glob(pattern)):
            files[path.name] = file_record(path)
    readback_path = out_dir / "readback_summary.json"
    readback = json.loads(readback_path.read_text()) if readback_path.is_file() else {}
    return {
        "out_dir": str(out_dir),
        "files": files,
        "gsub_budget": readback.get("checked", {}).get("gsub_budget"),
    }


def compare_snapshots(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_files = left.get("files", {})
    right_files = right.get("files", {})
    names = sorted(set(left_files) | set(right_files))
    files: dict[str, Any] = {}
    for name in names:
        a = left_files.get(name)
        b = right_files.get(name)
        if name.startswith("windows-"):
            identity_key = "logical_sha256"
        elif name == "M1.otf":
            identity_key = "semantic_sha256"
        else:
            identity_key = "sha256"
        required = name.startswith(("settlement-", "treaties-", "windows-")) or name in {
            "table-digests.json",
            "M1.generated.fea",
            "M1.otf",
        }
        files[name] = {
            "left": a,
            "right": b,
            "required": required,
            "identity_key": identity_key,
            "identical": a is not None and b is not None and a.get(identity_key) == b.get(identity_key),
            "byte_delta": None if a is None or b is None else b["bytes"] - a["bytes"],
        }
    required_names = {
        *(f"settlement-{config}.tsv" for config in conform.SETTLEMENT_CONFIGS),
        *(f"treaties-{config}.tsv" for config in conform.SETTLEMENT_CONFIGS),
        *(f"windows-{config}.tsv.gz" for config in conform.SETTLEMENT_CONFIGS),
        "table-digests.json",
        "M1.generated.fea",
        "M1.otf",
    }
    missing_required = sorted(required_names - set(names))
    gsub_equal = left.get("gsub_budget") is not None and left.get("gsub_budget") == right.get("gsub_budget")
    return {
        "format": FORMAT,
        "kind": "comparison",
        "identical": not missing_required
        and gsub_equal
        and all(record["identical"] for record in files.values() if record["required"]),
        "missing_required": missing_required,
        "files": files,
        "gsub_budget": {
            "left": left.get("gsub_budget"),
            "right": right.get("gsub_budget"),
            "identical": gsub_equal,
        },
    }


def prepare_baselines(out_dir: Path) -> None:
    baseline_subset.ensure_fresh(ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in run_m1.OUT_DIR.glob("baseline-*.subset.tsv.gz"):
        shutil.copy2(path, out_dir / path.name)
    for name in (baseline_subset.NAMES_NAME, baseline_subset.STAMP_NAME):
        shutil.copy2(run_m1.OUT_DIR / name, out_dir / name)
    missing = oracle.unaliased_subset_names(out_dir, run_m1.ALIAS_YAML)
    if missing:
        names = ", ".join(sorted(missing))
        raise SystemExit(f"the isolated subset baselines have unaliased old glyph names: {names}")


def install_digest_recorder(out_dir: Path) -> None:
    original = kernel_exec.build_table_files

    def recorded(*arguments, **keywords):
        digests = original(*arguments, **keywords)
        target = Path(arguments[1]).resolve()
        if target == out_dir.resolve() and set(digests) == set(conform.SETTLEMENT_CONFIGS):
            (out_dir / "table-digests.json").write_text(json.dumps(digests, indent=2) + "\n")
        return digests

    kernel_exec.build_table_files = recorded


def full_build(out_dir: Path, *, fresh_oracle_cache: bool) -> dict[str, Any]:
    started = time.perf_counter()
    install_digest_recorder(out_dir)
    phase = time.perf_counter()
    prepare_baselines(out_dir)
    baseline_preflight_s = time.perf_counter() - phase
    inputs = run_m1.tables_inputs()
    memo_inputs = run_m1.settle_memo_inputs()
    spec = load_default_spec()
    phase = time.perf_counter()
    run_m1.run_ligature_outgoing(spec, out_dir)
    ligature_outgoing_s = time.perf_counter() - phase
    widths = width_record()
    gates: run_m1.TableGates | None = None
    phase = time.perf_counter()
    try:
        pipeline, gates = run_m1.run(
            out_dir=out_dir,
            spec=spec,
            inputs=inputs,
            kernel_threads=widths["kernel_threads"],
            memo_inputs=memo_inputs,
            replay_threads=widths["replay_threads"],
        )
        pins = run_m1.run_manual_pin_gate(out_dir=out_dir, spec=spec)
        pin_failure = run_m1.manual_pin_gate_failure(pins)
        if pin_failure is not None:
            raise SystemExit(pin_failure)
        gates.wait_for_memo()
        oracle_summary = run_m1.run_oracle(
            out_dir=out_dir,
            spec=spec,
            jobs=widths["oracle_jobs"],
            fresh_cache=fresh_oracle_cache,
            memo_inputs=memo_inputs,
        )
        gates.join()
    finally:
        if gates is not None:
            gates.close()
    verdict = artifact_cycle.evaluate_run_m1_gate(pipeline, pins, oracle_summary)
    if not verdict.ok:
        raise SystemExit("; ".join(verdict.failures))
    return {
        "elapsed_s": round(time.perf_counter() - started, 3),
        "baseline_preflight_s": round(baseline_preflight_s, 3),
        "ligature_outgoing_s": round(ligature_outgoing_s, 3),
        "pipeline_and_gates_s": round(time.perf_counter() - phase, 3),
        "inputs": inputs,
        "widths": widths,
        "pipeline": pipeline,
        "manual_pins": pins,
        "oracle": oracle_summary,
    }


def require_experiment_source() -> None:
    if ROOT.name != "source" or ROOT.parent.name != "issue302":
        raise SystemExit(f"run measurements from the isolated issue302/source clone; this checkout is {ROOT}")
    required = [ROOT / "site", ROOT / "rebuild" / "out" / "baseline-default.tsv.gz"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(
            f"the isolated source clone is missing prepared old-font inputs: {', '.join(missing)}"
        )


def require_experiment_root(path: Path) -> None:
    if path.resolve() != DEFAULT_EXPERIMENT_ROOT.resolve():
        raise SystemExit(f"experiment output must stay at {DEFAULT_EXPERIMENT_ROOT}; got {path.resolve()}")


def cache_dir(experiment_root: Path, name: str) -> Path:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise SystemExit(f"--cache must be one directory name, got {name!r}")
    path = (experiment_root / "app-cache" / name).resolve()
    parent = (experiment_root / "app-cache").resolve()
    if path.parent != parent:
        raise SystemExit(f"cache path escapes {parent}: {path}")
    return path


def controlled_edit_state() -> str:
    payload = (ROOT / "glyph_data" / "runes" / "qsPea.yaml").read_bytes()
    if payload.count(PEA_EDIT_OLD.encode()) == 1 and PEA_EDIT_NEW.encode() not in payload:
        return "baseline"
    if payload.count(PEA_EDIT_NEW.encode()) == 1 and PEA_EDIT_OLD.encode() not in payload:
        return "edited"
    return "unknown"


def cache_marker(path: Path) -> dict[str, Any] | None:
    marker = path / "experiment-cache.json"
    try:
        value = json.loads(marker.read_text())
    except OSError, ValueError:
        return None
    return value if isinstance(value, dict) else None


def validate_cache_arm(path: Path, arm: str, scenario: str, tables_inputs: str) -> None:
    marker = cache_marker(path)
    state = controlled_edit_state()
    if scenario == "cold":
        if state != "baseline":
            raise SystemExit("a cold arm starts from the baseline qsPea input; restore the controlled edit")
        if path.exists() and any(path.iterdir()):
            raise SystemExit(f"cold cache {path} is not empty; choose a new --cache name")
        return
    if marker is None:
        raise SystemExit(f"{scenario} requires a completed experiment cache at {path}")
    if marker.get("arm") != arm:
        raise SystemExit(f"cache {path} belongs to arm {marker.get('arm')!r}, not {arm!r}")
    prior_inputs = marker.get("tables_inputs")
    if scenario == "warm":
        if state != "baseline" or prior_inputs != tables_inputs:
            raise SystemExit("a warm run requires the unchanged baseline source and matching cache stamp")
    elif state != "edited" or prior_inputs == tables_inputs:
        raise SystemExit("a rune-edit run requires the controlled qsPea edit over a completed baseline cache")


def command_prepare(args: argparse.Namespace) -> int:
    require_experiment_source()
    require_experiment_root(args.experiment_root)
    started = time.perf_counter()
    refiltered = baseline_subset.ensure_fresh(ROOT)
    record = {
        "format": FORMAT,
        "kind": "preparation",
        "source": source_record(),
        "machine": machine_record(),
        "widths": width_record(),
        "tables_inputs": run_m1.tables_inputs(),
        "baseline_subset": {
            "refiltered": refiltered,
            "elapsed_s": round(time.perf_counter() - started, 3),
        },
        "binary": (file_record(kernel_exec.BINARY) if kernel_exec.BINARY.is_file() else {"missing": True}),
    }
    target = args.experiment_root / "prepared.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2) + "\n")
    print(target)
    return 0


def command_child(args: argparse.Namespace) -> int:
    request = json.loads(args.request.read_text())
    experiment_root = Path(request["experiment_root"])
    result_path = Path(request["result"])
    runs_root = (experiment_root / "runs").resolve()
    if not args.request.resolve().is_relative_to(runs_root) or not result_path.resolve().is_relative_to(
        runs_root
    ):
        raise SystemExit("the child request and result must stay under the experiment runs directory")
    try:
        require_experiment_source()
        require_experiment_root(experiment_root)
        expected_out = cache_dir(experiment_root, request["cache"])
        if Path(request["out_dir"]).resolve() != expected_out:
            raise SystemExit("the child out_dir is not the request's isolated cache directory")
        if request["edit_state"] != controlled_edit_state():
            raise SystemExit("the controlled input state moved before the measured child started")
        expected_binary = request["binary"].get("sha256")
        if not kernel_exec.BINARY.is_file() or sha256_path(kernel_exec.BINARY) != expected_binary:
            raise SystemExit("the prepared kernel binary moved before the measured child started")
        kernel_exec.cargo_build = lambda: None
        result = full_build(Path(request["out_dir"]), fresh_oracle_cache=bool(request["fresh_oracle_cache"]))
        payload = {"format": FORMAT, "kind": "child", "ok": True, **result}
    except BaseException as error:
        payload = {
            "format": FORMAT,
            "kind": "child",
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, indent=2) + "\n")
        raise
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2) + "\n")
    return 0


def child_environment(arm: str) -> dict[str, str]:
    environment = os.environ.copy()
    if arm == "prototype":
        environment[PROTOTYPE_ENV] = "1"
    else:
        environment.pop(PROTOTYPE_ENV, None)
    return environment


def measured_child(
    arguments: list[str], log_path: Path, environment: dict[str, str]
) -> tuple[int, int | None, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            arguments,
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        peak = peak_rss.reap_peak_rss_bytes(process)
        if peak is None:
            process.wait()
    return process.returncode, peak, time.perf_counter() - started


def timing_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(errors="replace")
    return [
        {
            "label": match.group(1),
            "elapsed_s": float(match.group(2)),
            **({"detail": match.group(3)} if match.group(3) else {}),
        }
        for match in console.INNER_LINE.finditer(text)
    ]


def command_run(args: argparse.Namespace) -> int:
    require_experiment_source()
    require_experiment_root(args.experiment_root)
    kernel_exec.cargo_build()
    application_cache = cache_dir(args.experiment_root, args.cache)
    inputs = run_m1.tables_inputs()
    validate_cache_arm(application_cache, args.arm, args.scenario, inputs)
    application_cache.mkdir(parents=True, exist_ok=True)
    run_dir = args.experiment_root / "runs" / args.arm / args.scenario / utc_stamp()
    run_dir.mkdir(parents=True)
    request_path = run_dir / "request.json"
    result_path = run_dir / "child-result.json"
    request = {
        "format": FORMAT,
        "kind": "request",
        "arm": args.arm,
        "scenario": args.scenario,
        "cache": args.cache,
        "experiment_root": str(args.experiment_root),
        "out_dir": str(application_cache),
        "result": str(result_path),
        "fresh_oracle_cache": args.scenario == "cold",
        "source": source_record(),
        "machine": machine_record(),
        "widths": width_record(),
        "tables_inputs": inputs,
        "edit_state": controlled_edit_state(),
        "cache_before": artifact_snapshot(application_cache),
        "binary": file_record(kernel_exec.BINARY),
        "environment": {PROTOTYPE_ENV: "1" if args.arm == "prototype" else None},
    }
    request_path.write_text(json.dumps(request, indent=2) + "\n")
    command = [
        "uv",
        "run",
        "python",
        "-m",
        "rebuild.tools.compressed_fold_experiment",
        "child",
        "--request",
        str(request_path),
    ]
    raw_log = run_dir / "raw.log"
    rc, peak, wall = measured_child(command, raw_log, child_environment(args.arm))
    child = json.loads(result_path.read_text()) if result_path.is_file() else None
    record = {
        **request,
        "kind": "run",
        "command": command,
        "returncode": rc,
        "wall_s": round(wall, 3),
        "peak_rss_bytes": peak,
        "inner": timing_records(raw_log),
        "child": child,
        "cache_after": artifact_snapshot(application_cache),
    }
    (run_dir / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    if rc == 0:
        (application_cache / "experiment-cache.json").write_text(
            json.dumps(
                {
                    "format": FORMAT,
                    "arm": args.arm,
                    "scenario": args.scenario,
                    "tables_inputs": inputs,
                    "edit_state": controlled_edit_state(),
                    "run": str(run_dir),
                },
                indent=2,
            )
            + "\n"
        )
    print(run_dir)
    return rc


def command_compare(args: argparse.Namespace) -> int:
    require_experiment_source()
    app_cache = (DEFAULT_EXPERIMENT_ROOT / "app-cache").resolve()
    for path in (args.left.resolve(), args.right.resolve()):
        if path.parent != app_cache:
            raise SystemExit(f"comparison input must be one cache directly under {app_cache}: {path}")
    comparisons = (DEFAULT_EXPERIMENT_ROOT / "comparisons").resolve()
    if not args.output.resolve().is_relative_to(comparisons):
        raise SystemExit(f"comparison output must stay under {comparisons}: {args.output.resolve()}")
    left = artifact_snapshot(args.left)
    right = artifact_snapshot(args.right)
    report = compare_snapshots(left, right)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(args.output)
    return 0 if report["identical"] else 1


def command_ladder(args: argparse.Namespace) -> int:
    require_experiment_source()
    require_experiment_root(args.experiment_root)
    run_dir = args.experiment_root / "ladder" / args.arm / utc_stamp()
    dump = run_dir / "dump"
    run_dir.mkdir(parents=True)
    environment = child_environment(args.arm)
    environment["AMS_SCALING_DUMP"] = str(dump)
    if args.binary:
        environment["AMS_SCALING_BINARY"] = str(args.binary.resolve())
    command = ["uv", "run", "python", "-m", "rebuild.tools.scaling_sweep"]
    rows_path = ROOT / "rebuild" / "out" / "scaling-ladder.json"
    rows_path.unlink(missing_ok=True)
    rc, peak, wall = measured_child(command, run_dir / "raw.log", environment)
    fresh_rows = rc == 0 and rows_path.is_file()
    if fresh_rows:
        shutil.copy2(rows_path, run_dir / "scaling-ladder.json")
    record = {
        "format": FORMAT,
        "kind": "ladder",
        "arm": args.arm,
        "source": source_record(),
        "machine": machine_record(),
        "binary": file_record(Path(environment.get("AMS_SCALING_BINARY", kernel_exec.BINARY))),
        "command": command,
        "returncode": rc,
        "wall_s": round(wall, 3),
        "peak_rss_bytes": peak,
        "dump": str(dump),
        "fresh_ladder": fresh_rows,
    }
    (run_dir / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    print(run_dir)
    return rc


def command_edit(args: argparse.Namespace) -> int:
    require_experiment_source()
    require_experiment_root(args.experiment_root)
    path = ROOT / "glyph_data" / "runes" / "qsPea.yaml"
    before = path.read_bytes()
    old = PEA_EDIT_OLD.encode()
    new = PEA_EDIT_NEW.encode()
    expected, replacement = (old, new) if args.action == "apply" else (new, old)
    if before.count(expected) != 1 or replacement in before:
        raise SystemExit(f"qsPea controlled edit cannot {args.action}: expected block is not unique")
    after = before.replace(expected, replacement)
    record_dir = args.experiment_root / "controlled-edit" / utc_stamp()
    record_dir.mkdir(parents=True)
    (record_dir / "before.yaml").write_bytes(before)
    (record_dir / "after.yaml").write_bytes(after)
    path.write_bytes(after)
    record = {
        "format": FORMAT,
        "kind": "controlled-edit",
        "action": args.action,
        "path": str(path.relative_to(ROOT)),
        "before_sha256": sha256_bytes(before),
        "after_sha256": sha256_bytes(after),
    }
    (record_dir / "edit.json").write_text(json.dumps(record, indent=2) + "\n")
    print(record_dir)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    result.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").set_defaults(function=command_prepare)

    child = commands.add_parser("child")
    child.add_argument("--request", type=Path, required=True)
    child.set_defaults(function=command_child)

    run = commands.add_parser("run")
    run.add_argument("--arm", choices=ARMS, required=True)
    run.add_argument("--scenario", choices=("cold", "warm", "rune-edit"), required=True)
    run.add_argument("--cache", required=True)
    run.set_defaults(function=command_run)

    compare = commands.add_parser("compare")
    compare.add_argument("--left", type=Path, required=True)
    compare.add_argument("--right", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.set_defaults(function=command_compare)

    ladder = commands.add_parser("ladder")
    ladder.add_argument("--arm", choices=ARMS, required=True)
    ladder.add_argument("--binary", type=Path)
    ladder.set_defaults(function=command_ladder)

    edit = commands.add_parser("edit")
    edit.add_argument("action", choices=("apply", "restore"))
    edit.set_defaults(function=command_edit)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.experiment_root = args.experiment_root.resolve()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
