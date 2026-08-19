"""The on-demand kernel differential (issue #40; the standing per-cycle form retired at the issue #48 cutover): prove that the Rust kernel and the Python kernel still fold to the same three artifacts and the same contract digest. Rust builds the cycle's tables now and Python's settle kernel still ships — `gate:conform` re-settles every swept string through it and `emit_gsub` calls `formation_blocked` — so every future settlement-semantics change is written twice. Nothing arms this comparison automatically; it is the instrument to run by hand (`make kernel-gate`) around any kernel-semantics change, which is exactly when the twin implementations could have drifted.

Both sides are built here. Pre-cutover the gate compared the kernel's fold against the artifacts the cycle's Python engine had already written, and refusing a rust-stamped table set was its first check; now that the artifacts on disk are the kernel's own fold, reading them would compare the kernel against itself, so the Python side is enumerated fresh in-process — one fixpoint per configuration, the cost the per-cycle gate once existed to spare, acceptable in a harness you invoke deliberately rather than one every pass pays for. Each side's product folds through the same `assemble_tables` and the same writers, and both windows heads carry `kernel_fixpoint.INPUTS_STAMP`: the stamp names sources, this comparison is about semantics, and a real fingerprint would put a value that moves on every rune edit inside a byte comparison. `rebuild/tools/kernel_fixpoint.py` remains the deeper standalone harness — stream bytes, three worlds, fan-out widths — where this gate compares the shipping world at artifact grain.

Four comparisons per configuration, and the digest is the one carrying the grain. The three artifacts are the files a build persists, compared as bytes; `table.table_digest` covers what the TSVs drop: the ordered rules with their provenance, every enumerated window row, the treaty rows, the reachable cells, the cited provenance and the identity guards. The configurations are `conform.ACCEPTANCE_CONFIGS` by name rather than whatever a glob finds.

One `enumerate-configs` process answers every configuration, invoked with `--timings` so that `kernel_exec` forwards the kernel's `[t]` lines to this process's stderr verbatim and the cycle journal reads its per-configuration walls beside the Python side's. Exit 0 says every configuration compared identical at all four grains; a build that failed, a kernel that refused, and a divergence are all exit 1, with `kernel_differential_summary.json` saying which. Nothing downstream reads that summary: this tool's own `verdict` and its exit code are the interface, and the file is the record it leaves for a reader.

Run as `uv run python -m rebuild.tools.kernel_gate`, or through `make kernel-gate`, which builds the binary first.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from rebuild.pipeline import conform, kernel_exec, kernel_io
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools.kernel_differential import Arm
from rebuild.tools.kernel_fixpoint import artifacts, compare_blobs, world_label

ROOT = Path(__file__).resolve().parents[2]
M1_OUT = ROOT / "rebuild" / "out" / "m1"
SUMMARY_NAME = "kernel_differential_summary.json"
SUMMARY_FORMAT = "ams-kernel-differential-summary/2"
IDENTICAL = "identical"
DIVERGED = "diverged"
# The three artifacts a build persists per configuration, keyed by the name the summary states each comparison under. The digest rides beside them as a fourth comparison at a grain no file carries on its own.
ARTIFACT_KINDS = ("windows", "settlement", "treaties")
COMPARISONS = (*ARTIFACT_KINDS, "digest")


def _label(path: Path) -> str:
    """A path as the report names it: repo-relative inside the tree, absolute for anything else."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _kernel_side(spec: ResolvedSpec, config: str, stream: Path) -> tuple[dict[str, bytes], str] | None:
    """The kernel's three artifacts and digest for one configuration, or None when its stream does not fold into tables at all — a divergence of its own, reported as one instead of ending the run, on `kernel_fixpoint.kernel_tables`'s precedent. The stream is packed into the gzip shape `read_transitions` reads the way `run_m1._fold_stream` packs it — streamed through `copyfileobj` at the cheapest compression, since this copy is written, read once and unlinked — and both copies go the moment the fold is in hand: a live configuration's stream is hundreds of megabytes and the fan-out wrote every one of them before the comparison loop started."""
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        blob = directory / "transitions.ndjson.gz"
        with (
            stream.open("rb") as plain,
            blob.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as handle,
        ):
            shutil.copyfileobj(plain, handle)
        stream.unlink()
        try:
            product = kernel_io.read_transitions(blob)
            return artifacts(spec, product, directory)
        except (OSError, ValueError, IndexError, KeyError, table_module.PartitionError) as complaint:
            print(f"    {config}: the kernel's stream did not fold into tables: {complaint}", flush=True)
            return None


def _python_side(spec: ResolvedSpec, config: str) -> tuple[dict[str, bytes], str]:
    """The Python engine's answer for one configuration, enumerated fresh and folded through the identical path."""
    product = table_module.enumerate_transitions(spec, conform.features_for_config(config))
    with tempfile.TemporaryDirectory() as scratch:
        return artifacts(spec, product, Path(scratch))


def compare_config(spec: ResolvedSpec, config: str, stream: Path, arms: dict[str, Arm]) -> dict[str, str]:
    """One configuration's four comparisons, as the summary states them: each artifact's bytes, then the digest."""
    entry: dict[str, str] = {}
    kernel = _kernel_side(spec, config, stream)
    if kernel is None:
        for kind in COMPARISONS:
            arms[kind].note(1, 1)
            entry[kind] = DIVERGED
        return entry
    got, got_digest = kernel
    expected, expected_digest = _python_side(spec, config)
    names = {name.split("-", 1)[0]: name for name in expected}
    for kind in ARTIFACT_KINDS:
        name = names[kind]
        if name in got:
            mismatched = compare_blobs(f"{config} {name}", expected[name], got[name], name.endswith(".gz"))
        else:
            print(f"    {config} {name}: the kernel's fold wrote no artifact by this name", flush=True)
            mismatched = 1
        arms[kind].note(1, mismatched)
        entry[kind] = DIVERGED if mismatched else IDENTICAL
    mismatched = int(expected_digest != got_digest)
    if mismatched:
        print(
            f"    {config} table_digest: python folds to {expected_digest}, the kernel folds to {got_digest}",
            flush=True,
        )
    arms["digest"].note(1, mismatched)
    entry["digest"] = DIVERGED if mismatched else IDENTICAL
    return entry


def compare(
    configs: tuple[str, ...], threads: int, skip_build: bool, summary: dict, arms: dict[str, Arm]
) -> None:
    """The gate itself: build the kernel, hand the spec to one fan-out process, and compare each configuration's stream-fold against a fresh Python fixpoint. Fills `summary` as it goes, so a run that ends on a `KernelBuildError` or a `KernelRunError` still leaves a record of how far it got."""
    if not skip_build:
        kernel_exec.cargo_build()
    spec = load_default_spec()
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        streams = kernel_exec.enumerate_configs(
            spec_path, directory / "streams", configs, threads=threads, timings=True
        )
        for config in configs:
            start = time.perf_counter()
            entry = compare_config(spec, config, streams[config], arms)
            summary["configs"][config] = entry
            identical = all(state == IDENTICAL for state in entry.values())
            print(
                f"  {config:>12}  {len(entry):2d} comparisons  {'OK' if identical else 'FAIL'}  {time.perf_counter() - start:.1f}s",
                flush=True,
            )


def _reported(arms: dict[str, Arm]) -> list[Arm]:
    """The arms with something to say. A run that ended before any comparison — a kernel that refused to build or run — reports its reason and not four lines of nothing compared."""
    return [arm for arm in arms.values() if arm.compared or arm.divergences]


def verdict(
    summary: dict, arms: dict[str, Arm], configs: tuple[str, ...], elapsed: float
) -> tuple[str, bool]:
    """The run's last line and whether it passed, in the order a reader wants the bad news: a kernel that never answered, then the divergences, then the configurations that never got compared at all. Green demands every configuration identical at every grain, rather than the absence of a recorded complaint, so a run that fell over between configurations can never read as agreement."""
    tally = ", ".join(f"{arm.compared} {arm.label}" for arm in _reported(arms))
    if summary["error"]:
        return f"kernel differential: the kernel never answered — {summary['error']}", False
    if summary["divergences"]:
        return (
            f"kernel differential: {summary['divergences']} divergences over {tally} comparisons in {elapsed:.1f}s",
            False,
        )
    compared = summary["configs"]
    whole = list(compared) == list(configs) and all(
        compared[config].get(kind) == IDENTICAL for config in compared for kind in COMPARISONS
    )
    if not whole:
        return (
            f"kernel differential: only {len(compared)} of {len(configs)} configurations compared in {elapsed:.1f}s",
            False,
        )
    return f"kernel differential: {tally} comparisons identical in {elapsed:.1f}s", True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove the two kernel engines fold to identical table artifacts, building both sides fresh."
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=kernel_exec.KERNEL_THREADS_DEFAULT,
        help=f"how many configurations the kernel enumerates at once, capped at the configuration count and the CPU count (default {kernel_exec.KERNEL_THREADS_DEFAULT}, which AMS_KERNEL_THREADS overrides); byte identity holds at any width, so this is a memory knob",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="compare against the binary already on disk instead of building the crate first",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=M1_OUT,
        help="where kernel_differential_summary.json is written (default rebuild/out/m1); every table both engines build stays in scratch",
    )
    args = parser.parse_args(argv)
    configs = conform.ACCEPTANCE_CONFIGS
    threads = max(1, min(args.threads, len(configs), os.process_cpu_count() or 1))
    arms = {kind: Arm(kind) for kind in COMPARISONS}
    summary = {
        "format": SUMMARY_FORMAT,
        "binary": _label(kernel_exec.BINARY),
        "world": world_label(),
        "threads": threads,
        "configs": {},
        "divergences": 0,
        "error": None,
    }
    start = time.perf_counter()
    print(
        f"kernel differential: {world_label()} at {threads} threads against {_label(kernel_exec.BINARY)}, both sides built fresh",
        flush=True,
    )
    try:
        compare(configs, threads, args.skip_build, summary, arms)
    except (kernel_exec.KernelBuildError, kernel_exec.KernelRunError) as failure:
        summary["error"] = str(failure)
    summary["divergences"] = sum(arm.divergences for arm in arms.values())
    path = args.out / SUMMARY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")
    for arm in _reported(arms):
        state = "OK" if not arm.divergences else f"{arm.divergences} diverged"
        print(f"  {arm.label:>12}  {arm.compared:2d} compared  {state}", flush=True)
    line, passed = verdict(summary, arms, configs, time.perf_counter() - start)
    if summary["error"]:
        print(f"kernel differential: {summary['error']}", file=sys.stderr)
    print(line, flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
