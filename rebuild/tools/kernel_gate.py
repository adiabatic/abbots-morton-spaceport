"""The standing dual-run (issue #40, sub-issue #47): every cycle whose kernel-relevant inputs moved asks the Rust kernel to enumerate the spec the cycle just built from, folds its streams through the Python half of the build, and requires what falls out to be the three artifacts and the contract digest the cycle's own Python engine already wrote. `rebuild/tools/kernel_fixpoint.py` is the exit bar and pays both sides of that comparison — a Python fixpoint per configuration and a Rust one — which is minutes of CPU nobody wants on every cycle; this pays exactly one, because the Python side's answer is already on disk under the stamp naming the sources it came from. The two harnesses state the same claim at different prices and different frequencies: the exit bar runs the scaling ladder and the whole alphabet when a human asks for it, and the gate runs the live alphabet whenever the sources under it move.

The comparison is against artifacts and never against a rebuild. The gate reads each `windows-<config>.tsv.gz` head stamp and the `table-digests.json` record beside them, and a set stamped from other sources is a red gate whose remedy is to run the artifact cycle — because producing the Python side itself is the one thing this must not do. A gate that rebuilt what it compares would charge the cycle the fixpoint it exists to spare, and would prove the two engines agree about a build nothing shipped. The configurations are `conform.ACCEPTANCE_CONFIGS` by name rather than whatever a glob finds, since `rebuild/out/m1/` still holds settlement, treaty and windows files for configurations the acceptance matrix has retired.

Four comparisons per configuration, and the digest is the one carrying the grain. The three artifacts are the files a build persists, compared as bytes with the real inputs stamp written into the windows head — not `kernel_fixpoint`'s pinned one, which exists precisely to keep a moving fingerprint out of that harness's byte comparison, where here agreeing with the cycle's own head line is the whole point. `table.table_digest` covers what the TSVs drop: the ordered rules with their provenance, every enumerated window row, the treaty rows, the reachable cells, the cited provenance and the identity guards. That is why `run_m1` records it at build time — the rows it covers leave memory on the way out of `_persist_tables`, so a digest recovered afterwards would cost the fixpoint that produced it.

One `enumerate-configs` process answers every configuration, invoked with `--timings` so that `kernel_exec` forwards the kernel's `[t]` lines to this process's stderr verbatim and the cycle journal reads its per-configuration walls beside the Python stage's. Exit 0 says every configuration compared identical at all four grains; a build that failed, a kernel that refused, a stale artifact set and a divergence are all exit 1, with `kernel_differential_summary.json` beside the artifacts saying which — in the shape the artifact cycle's verdict function reads.

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

from rebuild.pipeline import conform, kernel_exec, kernel_io, run_m1
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools.kernel_differential import Arm
from rebuild.tools.kernel_fixpoint import compare_blobs, world_label

ROOT = Path(__file__).resolve().parents[2]
M1_OUT = ROOT / "rebuild" / "out" / "m1"
SUMMARY_NAME = "kernel_differential_summary.json"
SUMMARY_FORMAT = "ams-kernel-differential-summary/1"
# The digest record `run_m1._write_table_digests` leaves beside the tables. Its format token is imported rather than spelled again; the file name is the one thing the two sides state separately, since the pipeline writes it as a literal.
TABLE_DIGESTS_NAME = "table-digests.json"
STALE_REMEDY = "run the artifact cycle"
IDENTICAL = "identical"
DIVERGED = "diverged"
# The three artifacts a build persists per configuration, keyed by the name the summary states each comparison under. The digest rides beside them as a fourth comparison at a grain no file carries on its own.
ARTIFACT_KINDS = ("windows", "settlement", "treaties")
COMPARISONS = (*ARTIFACT_KINDS, "digest")


def _label(path: Path) -> str:
    """A path as the report names it: repo-relative inside the tree, absolute for anything else, so a gate pointed at scratch names it instead of failing on the path arithmetic."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def fold_artifacts(
    spec: ResolvedSpec, product: table_module.FixpointProduct, directory: Path, inputs: str
) -> tuple[dict[str, Path], str]:
    """One product folded into the three files a build persists, plus the contract digest of the pair — `kernel_fixpoint.artifacts` in the shape this gate needs it. Two differences, both load-bearing: the windows head carries the real stamp rather than the exit bar's pinned one, because the bytes under comparison here were written by a cycle whose own head line names its sources; and what comes back is the paths rather than their bytes, so each folded file is compared against the file of exactly that name under the cycle's output directory."""
    decision, treaty = table_module.assemble_tables(spec, product)
    written = {
        "windows": table_module.windows_path(directory, decision.config),
        "settlement": directory / f"settlement-{decision.config}.tsv",
        "treaties": directory / f"treaties-{decision.config}.tsv",
    }
    table_module.write_windows(decision, written["windows"], inputs)
    decision.write_tsv(written["settlement"])
    treaty.write_tsv(written["treaties"])
    return written, table_module.table_digest(decision, treaty)


def check_freshness(out: Path, configs: tuple[str, ...], inputs: str) -> tuple[dict[str, str], list[str]]:
    """The recorded digests, and every reason the artifacts under `out` do not describe the sources on disk. A stale set is not something to fix here: the gate compares what a cycle built and has no business building it, so each miss becomes a line in the summary's `stale` list and the remedy is a cycle. Every configuration is checked even after the first miss, because a reader deciding whether to spend half an hour on a cycle wants the whole picture rather than its first line. The digest record must also name the Python engine: a set `run_m1 --engine rust` built satisfies every stamp this guard checks, and comparing the kernel against its own fold would read identical by construction — the one green this gate must never record."""
    stale: list[str] = []
    for config in configs:
        path = table_module.windows_path(out, config)
        try:
            stamp, _decision = table_module.read_windows(path, windows=False)
        except OSError:
            stale.append(f"{path.name} is absent or unreadable")
            continue
        except ValueError:
            stale.append(f"{path.name} is not a window enumeration this build understands")
            continue
        if stamp != inputs:
            stale.append(f"{path.name} was built from other sources")
    record = out / TABLE_DIGESTS_NAME
    try:
        payload = json.loads(record.read_text())
        stated, recorded, engine, digests = (
            payload["format"],
            payload["inputs"],
            payload["engine"],
            payload["digests"],
        )
    except OSError:
        stale.append(f"{TABLE_DIGESTS_NAME} is absent or unreadable")
        return {}, stale
    except ValueError, KeyError, TypeError:
        stale.append(f"{TABLE_DIGESTS_NAME} is not a digest record this build understands")
        return {}, stale
    if stated != run_m1.TABLE_DIGESTS_FORMAT:
        stale.append(f"{TABLE_DIGESTS_NAME} is not a {run_m1.TABLE_DIGESTS_FORMAT} record")
        return {}, stale
    if recorded != inputs:
        stale.append(f"{TABLE_DIGESTS_NAME} was written from other sources")
    if engine != run_m1.ENGINE_DEFAULT:
        stale.append(
            f"{TABLE_DIGESTS_NAME} records a {engine}-built table set — the differential needs the set the Python engine of record built"
        )
    absent = [config for config in configs if config not in digests]
    if absent:
        stale.append(f"{TABLE_DIGESTS_NAME} records no digest for {', '.join(absent)}")
    return digests, stale


def compare_config(
    spec: ResolvedSpec,
    config: str,
    stream: Path,
    scratch: Path,
    out: Path,
    inputs: str,
    recorded: dict[str, str],
    arms: dict[str, Arm],
) -> dict[str, str]:
    """One configuration's four comparisons, as the summary states them. The kernel's stream is packed into the gzip shape `read_transitions` reads the way `run_m1._fold_stream` packs it — streamed through `copyfileobj` rather than read whole, at the cheapest compression, since this copy is written, read once and unlinked — and both copies go the moment the product is in hand: a live configuration's stream is hundreds of megabytes and the fan-out wrote every one of them before this loop started. A stream that will not fold at all counts as a divergence at every grain rather than ending the run, on `kernel_fixpoint.kernel_tables`'s precedent: the fold is where a difference the byte compare would have called one line turns out to be a cell the table cannot seat."""
    entry: dict[str, str] = {}
    folded = scratch / f"fold-{config}"
    blob = folded / "transitions.ndjson.gz"
    try:
        folded.mkdir(parents=True, exist_ok=True)
        with (
            stream.open("rb") as plain,
            blob.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as handle,
        ):
            shutil.copyfileobj(plain, handle)
        stream.unlink()
        product = kernel_io.read_transitions(blob)
        blob.unlink()
        written, digest = fold_artifacts(spec, product, folded, inputs)
    except (OSError, ValueError, IndexError, KeyError, table_module.PartitionError) as complaint:
        print(f"    {config}: the kernel's stream did not fold into tables: {complaint}", flush=True)
        for kind in COMPARISONS:
            arms[kind].note(1, 1)
            entry[kind] = DIVERGED
        return entry
    for kind, path in written.items():
        built = out / path.name
        if built.is_file():
            mismatched = compare_blobs(
                f"{config} {path.name}", built.read_bytes(), path.read_bytes(), path.name.endswith(".gz")
            )
        else:
            print(f"    {config} {path.name}: the cycle left no artifact by this name", flush=True)
            mismatched = 1
        arms[kind].note(1, mismatched)
        entry[kind] = DIVERGED if mismatched else IDENTICAL
    mismatched = int(recorded.get(config) != digest)
    if mismatched:
        print(
            f"    {config} table_digest: the cycle recorded {recorded.get(config)}, the kernel folds to {digest}",
            flush=True,
        )
    arms["digest"].note(1, mismatched)
    entry["digest"] = DIVERGED if mismatched else IDENTICAL
    return entry


def compare(
    out: Path, configs: tuple[str, ...], threads: int, skip_build: bool, summary: dict, arms: dict[str, Arm]
) -> None:
    """The gate itself: build the kernel, learn which sources the artifacts on disk claim, refuse to compare against a set that claims other ones, and otherwise hand the spec to one fan-out process and fold every stream it answers with. Fills `summary` as it goes, so a run that ends on a `KernelBuildError` or a `KernelRunError` still leaves a record of how far it got."""
    if not skip_build:
        kernel_exec.cargo_build()
    spec = load_default_spec()
    inputs = run_m1.tables_inputs()
    summary["inputs"] = inputs
    recorded, stale = check_freshness(out, configs, inputs)
    summary["stale"] = stale
    if stale:
        for note in stale:
            print(f"    {note}", flush=True)
        return
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        streams = kernel_exec.enumerate_configs(
            spec_path, directory / "streams", configs, threads=threads, timings=True
        )
        for config in configs:
            start = time.perf_counter()
            entry = compare_config(spec, config, streams[config], directory, out, inputs, recorded, arms)
            summary["configs"][config] = entry
            identical = all(state == IDENTICAL for state in entry.values())
            print(
                f"  {config:>12}  {len(entry):2d} comparisons  {'OK' if identical else 'FAIL'}  {time.perf_counter() - start:.1f}s",
                flush=True,
            )


def _reported(arms: dict[str, Arm]) -> list[Arm]:
    """The arms with something to say. A run that ended before any comparison — a kernel that refused, a stale artifact set — reports its reason and not four lines of nothing compared."""
    return [arm for arm in arms.values() if arm.compared or arm.divergences]


def verdict(
    summary: dict, arms: dict[str, Arm], configs: tuple[str, ...], elapsed: float
) -> tuple[str, bool]:
    """The run's last line and whether it passed, in the order a reader wants the bad news: a kernel that never answered, then artifacts that were never the cycle's, then the divergences, then the configurations that never got compared at all. Green demands every configuration identical at every grain, rather than the absence of a recorded complaint, so a run that fell over between configurations can never read as agreement."""
    tally = ", ".join(f"{arm.compared} {arm.label}" for arm in _reported(arms))
    if summary["error"]:
        return f"kernel differential: the kernel never answered — {summary['error']}", False
    if summary["stale"]:
        return (
            f"kernel differential: the tables under comparison do not describe the sources on disk ({len(summary['stale'])} notes above) — {STALE_REMEDY}",
            False,
        )
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
        description="Prove the Rust kernel folds into the table artifacts this cycle's Python engine already wrote."
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=kernel_exec.KERNEL_THREADS_DEFAULT,
        help=f"how many configurations the kernel enumerates at once, capped at the configuration count and the CPU count (default {kernel_exec.KERNEL_THREADS_DEFAULT}); byte identity holds at any width, so this is a memory knob",
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
        help="the build output directory holding the artifacts to compare against, and where the summary is written (default rebuild/out/m1)",
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
        "inputs": None,
        "configs": {},
        "divergences": 0,
        "stale": [],
        "error": None,
    }
    start = time.perf_counter()
    print(
        f"kernel differential: {world_label()} at {threads} threads against {_label(kernel_exec.BINARY)}, over the tables under {_label(args.out)}",
        flush=True,
    )
    try:
        compare(args.out, configs, threads, args.skip_build, summary, arms)
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
