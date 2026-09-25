"""Time the Rust kernel's `enumerate-configs` over the settlement configurations (`conform.SETTLEMENT_CONFIGS`; the ss10 overlay settles nothing and has no enumeration), serially or across threads, and print one JSON row per configuration plus a total row. It times the enumeration and the stream only. The table build runs `build-tables`, which also writes the memo and folds, so its `tables_total` line covers more than this harness's `kernel_total_s`, and the two compare only on the enumeration. The memory constants in `rebuild/pipeline/kernel_exec.py` are measured on `build-tables` runs, not with this harness.

It writes only the spec dump and the transition streams, both under rebuild/out/kernel-all/, and refuses an `--out-dir` that resolves anywhere else, because a scratch directory inside `rebuild/out/m1` would overwrite the artifact cycle's tables.

  uv run python -m rebuild.tools.kernel_all_configs [--mode serial|parallel] [--threads N] [--configs a,b] [--spec <dump>] [--rung N] [--reps N]

`--mode serial`, the default, runs at `--threads=1`, every configuration in the listed order, so its per-configuration times add up. `--mode parallel` runs `--threads` wide, by default one thread per configuration up to the cores. A row's `threads_requested` is the width asked for, and `threads` is that width capped at the configuration count. The kernel also caps the width at the machine's available parallelism, so on a machine with fewer cores than configurations `threads` can be larger than the width that ran. The streams are written to files, so neither mode's times include piping stdout. Per-configuration times come from the child's own `[t]` lines, at the one decimal place the kernel prints, and are null for a phase the child did not report, since 0.0 would look like a measurement. `console.INNER_LINE` defines the `[t]` line format, and this module imports it so that it parses the same lines the artifact cycle does.

Only the child is measured. `/usr/bin/time` wraps the invocation for the peak resident set (`peak_rss.parse_time_output` reads both the Darwin form in bytes and the Linux form in KiB), `resource.getrusage(RUSAGE_CHILDREN)` deltas give the CPU time, and the wall-clock time covers the whole invocation. The `[t]` lines and `/usr/bin/time`'s report share one stderr capture. On a machine without `/usr/bin/time` the peak is null.

The kernel enumerates the world `kernel_exec.world_flags()` describes, the same flags `run_m1` passes, so `AMS_SIMULATED_PROSPECT`, `AMS_VOTE_SLOTS` and `AMS_DEEP_CLASSES` change this measurement as they change a build. Every row records the flags in `world`, or `shipping defaults` when none is set, as `scaling_sweep.py` prints it.

Each configuration holds its working set until it has emitted, so a parallel run needs roughly the serial peak times the number of configurations in flight. Lower `--threads` on a machine with less memory than that.

Every configuration's row includes the SHA-256 of its stream file, and the total row's digest combines them, so a scheduling change that alters the output shows as a changed digest. The kernel writes byte-identical streams at any thread count (`a_fan_out_files_what_one_enumeration_writes_to_stdout` in `rebuild/kernel-rs/tests/cli.rs` checks this), and these digests check that each run timed the same output.

`--rung N` replaces the live alphabet with one rung of the ladder `rebuild/tools/scaling_ladder.py` defines (the ladder `scaling_sweep.py` sweeps) and times the default configuration on it, or the configurations `--configs` names. `--spec` times an existing dump instead of writing one; `scaling_sweep.py` keeps one per rung under `AMS_SCALING_DUMP=<dir>`. A dump names a spec, not configurations, so a bare `--spec` over a rung dump times every settlement configuration, where the `--rung` run that wrote it timed only `default`; add `--configs=default` to repeat that measurement. `--configs` narrows any run and refuses a name that is not a settlement configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
from pathlib import Path

from rebuild.pipeline import conform, kernel_exec, kernel_io
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import peak_rss, scaling_ladder
from rebuild.tools.console import INNER_LINE

SCRATCH_OUT = Path(__file__).resolve().parents[2] / "rebuild" / "out" / "kernel-all"


def cpu_children() -> float:
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def peak_rss_gb(text: str) -> float | None:
    measured = peak_rss.parse_time_output(text)
    return round(peak_rss.bytes_to_gb(measured), 2) if measured is not None else None


def phase_times(text: str) -> dict[str, float]:
    return {match.group(1): float(match.group(2)) for match in INNER_LINE.finditer(text)}


def digest_of(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def configs_from(requested: str) -> list[str]:
    """Return the configurations `--configs=` names, in its order. An unknown name exits here, before the spec is resolved and dumped, with a message that lists the valid names."""
    offered = dict.fromkeys((*conform.SETTLEMENT_CONFIGS, "default"))
    tokens = [token.strip() for token in requested.split(",") if token.strip()]
    unknown = [token for token in tokens if token not in offered]
    if not tokens:
        raise SystemExit(
            f"--configs {requested!r} names no configuration; the tokens are {', '.join(offered)}"
        )
    if unknown:
        raise SystemExit(f"no such configuration: {', '.join(unknown)}; the tokens are {', '.join(offered)}")
    return tokens


def scratch_out_dir(requested: str, arm: str) -> Path:
    out_dir = Path(requested).resolve() if requested else (SCRATCH_OUT / arm).resolve()
    if SCRATCH_OUT not in out_dir.parents:
        raise SystemExit(f"refusing out_dir {out_dir}: it must resolve under {SCRATCH_OUT}")
    return out_dir


def run_kernel(binary: Path, spec: Path, out_dir: Path, tokens: list[str], threads: int) -> dict:
    arguments = [
        *peak_rss.time_wrapper(),
        str(binary),
        "enumerate-configs",
        str(spec),
        str(out_dir),
        f"--configs={','.join(tokens)}",
        f"--threads={threads}",
        *kernel_exec.world_flags(),
        "--timings",
    ]
    cpu0 = cpu_children()
    wall0 = time.perf_counter()
    finished = subprocess.run(arguments, capture_output=True, text=True)
    wall = time.perf_counter() - wall0
    cpu = cpu_children() - cpu0
    if finished.returncode != 0:
        raise SystemExit(f"the kernel exited {finished.returncode}: {finished.stderr.strip()}")
    return {"wall": wall, "cpu": cpu, "stderr": finished.stderr}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="serial", choices=("serial", "parallel"))
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--configs", default="")
    ap.add_argument("--spec", default="")
    ap.add_argument("--rung", type=int, default=0)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--binary", default=str(kernel_exec.BINARY))
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    binary = Path(args.binary).resolve()
    if not binary.is_file():
        raise SystemExit(f"no kernel binary at {binary} — run `make kernel-build` first")
    if args.spec and args.rung:
        raise SystemExit("--spec and --rung name different specs; pass one of them")
    if args.threads and args.mode == "serial":
        raise SystemExit("--mode serial is --threads=1; pass --mode parallel to widen it")

    tokens: list[str]
    if args.configs:
        tokens = configs_from(args.configs)
    else:
        tokens = ["default"] if args.rung else list(conform.SETTLEMENT_CONFIGS)
    requested = (
        1 if args.mode == "serial" else (args.threads or min(len(tokens), os.process_cpu_count() or 1))
    )
    threads = min(requested, len(tokens))

    t0 = time.perf_counter()
    if args.spec:
        spec_path = Path(args.spec).resolve()
        if not spec_path.is_file():
            raise SystemExit(f"no spec dump at {spec_path}")
        runes = len(kernel_io.read_spec(spec_path).runes)
        arm = f"{spec_path.stem.removeprefix('spec-')}-{args.mode}"
        out_dir = scratch_out_dir(args.out_dir, arm)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        spec = load_default_spec()
        if args.rung:
            order = scaling_ladder.ladder_order(spec)
            rungs = scaling_ladder.ladder_rungs(order)
            if args.rung not in rungs:
                offered = ", ".join(str(rung) for rung in rungs)
                raise SystemExit(f"--rung {args.rung} is not a rung of the ladder: {offered}")
            spec = scaling_ladder.sub_spec(spec, order, args.rung)
        runes = len(spec.runes)
        stem = f"r{runes}" if args.rung else "live"
        out_dir = scratch_out_dir(args.out_dir, f"{stem}-{args.mode}")
        spec_path = out_dir / f"spec-{stem}.json"
        kernel_io.write_spec(spec, spec_path)
    spec_load_wall = time.perf_counter() - t0

    common = {
        "label": args.label,
        "mode": args.mode,
        "threads": threads,
        "threads_requested": requested,
        "world": " ".join(kernel_exec.world_flags()) or "shipping defaults",
        "runes": runes,
        "spec": str(spec_path),
        "binary": str(binary),
    }
    for rep in range(args.reps):
        run = run_kernel(binary, spec_path, out_dir, tokens, requested)
        phases = phase_times(run["stderr"])
        rows = []
        for token in tokens:
            stream = out_dir / f"transitions-{token}.ndjson"
            enumerated = phases.get(f"enumerate[{token}]")
            emitted = phases.get(f"emit[{token}]")
            both = None if enumerated is None or emitted is None else round(enumerated + emitted, 1)
            rows.append(
                {
                    "config": token,
                    "wall_s": both,
                    "enumerate_s": enumerated,
                    "emit_s": emitted,
                    "sha256": digest_of(stream),
                    "bytes": stream.stat().st_size,
                }
            )
        for row in rows:
            print(json.dumps({"kind": "config", **common, "rep": rep, **row}), flush=True)
        print(
            json.dumps(
                {
                    "kind": "total",
                    **common,
                    "rep": rep,
                    "configs": len(rows),
                    "spec_load_wall_s": round(spec_load_wall, 3),
                    "spec_parse_s": phases.get("spec_parse"),
                    "kernel_total_s": phases.get("enumerate_total"),
                    "total_wall_s": round(run["wall"], 2),
                    "total_cpu_s": round(run["cpu"], 2),
                    "peak_rss_gb": peak_rss_gb(run["stderr"]),
                    "out_dir": str(out_dir),
                    "digest": hashlib.sha256(
                        "\n".join(f"{r['config']}\t{r['sha256']}" for r in rows).encode()
                    ).hexdigest(),
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
