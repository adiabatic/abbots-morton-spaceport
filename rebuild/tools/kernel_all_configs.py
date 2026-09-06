"""The Rust kernel over the settlement configurations (`conform.SETTLEMENT_CONFIGS`; the ss10 overlay settles nothing and has no enumeration to time): one `ams-m1-kernel enumerate-configs` run, serially or fanned out over threads, with the rows named the way the retired Python endpoint (`m1_all_configs.py`, in git history) named its rows, so an old row and a new one still read side by side. It is the direct measurement behind `CONFIG_PEAK_BYTES` in `rebuild/pipeline/kernel_exec.py`: one child on its own, with the crate's `--cache-census` beside it for where the memo sits.

Read-only on the repo. The spec dump and the transition streams are all this writes, both under rebuild/out/kernel-all/, and an `--out-dir` resolving anywhere else is refused: a scratch directory that resolved into `rebuild/out/m1` would overwrite the artifact cycle's tables.

  uv run python -m rebuild.tools.kernel_all_configs [--mode serial|parallel] [--threads N] [--configs a,b] [--spec <dump>] [--rung N] [--reps N]

Two modes, because the port's claim has two halves. `serial` is `--threads=1`, every configuration in listed order, the default, and the arm whose per-configuration walls sum; `parallel` is the fan-out, `--threads` wide, defaulting to as many as the machine will give. The kernel caps whatever it is asked for at the number of configurations there are to answer, so a row's `threads` is the width that actually ran and `threads_requested` is the number that was asked for — a `--threads=32` over the whole acceptance set is a run at one thread per configuration. Either way the streams land in files rather than coming back through a pipe, so neither arm is charged for stdout plumbing, and the per-configuration walls are the child's own `[t]` lines rather than anything this process could time from outside — at the one decimal the kernel prints them in, and null rather than 0.0 for a phase the child never reported, since a zero there would read as a measurement. What a `[t]` line looks like is `console.INNER_LINE`, imported rather than copied, so this harness and the artifact cycle's own reader cannot come to disagree about the shape.

The child is what gets measured, never this process: `/usr/bin/time` wraps the invocation for the peak resident set (darwin reports bytes where Linux reports KiB, and `peak_rss.parse_time_output` normalizes both dialects back to bytes), `resource.getrusage(RUSAGE_CHILDREN)` deltas carry the CPU, and the wall covers the whole invocation. The `[t]` lines and `/usr/bin/time`'s own report share the one stderr, and both are parsed back out of the single capture. A box with no `/usr/bin/time` still gets its walls and its CPU, and reports a null peak rather than a guessed one.

The kernel is told which world to enumerate, through `kernel_exec.world_flags()` — the pipeline's own reflection off the three module defaults `kernel_exec` now holds, the very list `run_m1` hands the kernel, so `AMS_SIMULATED_PROSPECT`, `AMS_VOTE_SLOTS` and `AMS_DEEP_CLASSES` move this arm exactly as they move a build. A subprocess inherits none of that implicitly, so every row carries `world` in the flag spelling, `shipping defaults` when nothing is off — the spelling `scaling_sweep.py` prints too. A wall clock means as little as a byte comparison until you know which fixpoint produced it.

Peak resident set is per configuration and a fixpoint's working set lives until it has emitted, so a parallel run wants roughly the serial peak times the configurations in flight. `--threads` is the knob on a box with less memory than that.

Every configuration's row carries the sha256 of its stream file, so two runs at any width are comparable at a glance: a schedule that changed an answer surfaces as a digest that moved rather than as a number that drifted. The total row's digest folds the per-configuration ones. That the streams come out byte-identical at any thread width is the crate's own contract rather than anything measured here — `kernel_exec.enumerate_configs`'s docstring states it — so these digests are the cheap standing check that the thing being timed is still the same answer.

What this harness times is the enumeration and the stream, which is what `enumerate-configs` does and no longer the whole of what a build's table stage does: since the crate grew its `build-tables` verb a build folds in the same process and writes tables rather than a stream, so `make cycle-timings`'s `build_tables_total` now covers a fold this arm does not. The two still compare on the enumeration, which is where the growth is.

`--rung N` swaps the live alphabet for one rung of the nested ladder `rebuild/tools/scaling_ladder.py` cuts — the ladder `scaling_sweep.py` sweeps whole through this same binary — and times the default configuration alone on it, which is how one rung is re-timed, or timed at every settlement configuration with `--configs`, without re-running the sweep. `--spec` measures a dump already written instead of writing a fresh one, which is how a rung gets a second run without re-resolving the spec, and `scaling_sweep.py` leaves one per rung under `AMS_SCALING_DUMP=<dir>` — but it names a spec, not a set of configurations, so a bare `--spec` over a rung dump times the whole acceptance set where the `--rung` that wrote it timed one. `--configs=default` beside it is what repeats the rung's own measurement; `--configs` narrows any run the same way, and refuses a token the acceptance sweep does not spell.
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
    """The configurations a `--configs=` names, in the order it named them. A token the table build does not enumerate is refused here rather than left to the kernel, which would refuse it too but only after the spec had been resolved and dumped — and this way the complaint can list what there is."""
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
