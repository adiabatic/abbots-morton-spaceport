"""How steeply does the M1 fixpoint grow with the modeled alphabet? Only part of the 44-letter target is modeled today, so what this sweep bears on is how far the port's constant factor reaches toward it: a workload that grows steeply in the alphabet spends a constant factor quickly. It is the migration's early-warning system rather than a one-time measurement — a port moves the constant and not the exponent, so a ladder that steepens between batches makes work avoidance due regardless of language, and `bench-the-rebuild/RUST-PORT-PLAN.md`'s threshold rows are stated against what this prints. The top rung is the whole current alphabet, which is the check that the sweep measures the real kernel rather than a subset of it.

Every rung goes through the kernel crate: one `enumerate-configs --configs=default --threads=1` child per rung, the engine of record, so the ladder times what a build runs. That is also what makes a row's `rss_high_water_gb` the rung's own figure — the child's process high-water, reaped with it through `peak_rss.reap_peak_rss_bytes` — rather than the cumulative maximum an in-process sweep charged every rung with once the tallest rung had run. `cpu` and `wall` cover the whole child: spec parse, enumerate and emit together, emit being a serialization the in-process Python arm never paid, so a row from before this change — the ones in git's history of `scaling.txt`, say — compares on exponent and not on constant. The `[t]` lines split that total into `spec_parse_s`, `enumerate_s` and `emit_s`, null rather than 0.0 for a phase the child did not report, since a zero there would read as a measurement.

The Python half folds each stream the way `run_m1._fold_stream` folds a build's — packed to gzip at the cheapest compression there is, read back through `kernel_io.read_transitions`, handed to `table.assemble_tables` — outside the timed region and reported on its own as `fold_s`. That fold is what keeps `windows`, `rules` and `cells` the same label-grain counts the sweep has always printed, and what gives every row a `digest` at `table.table_digest`'s contract grain, so a rung whose seconds drifted and a rung whose answer changed are two different events.

The report is the consecutive-pair exponents against runes, as before, plus a least-squares fit of ln count on ln alphabet over the whole ladder in both denominators. Fit the whole ladder and state the denominator: a single pair swings by a large fraction of the threshold on ordinary scatter and on which letters that rung happened to add, and a rune exponent is the letter exponent times `d ln letters / d ln runes`, which the nested ladder drives from below 1 to above 1 as it stops adding ligatures and starts adding letters. RUST-PORT-PLAN.md states its threshold in this fitted form and in both bases — about 4.5 in letters, about 5.5 in runes — and those are one threshold rather than two.

The knobs. Positional arguments are the rune counts to cut rungs at, any k rather than only a ladder rung, and default to the full ladder from `kernel_parity.ladder_rungs`, which is the ladder's one authority now that this script imports it instead of reproducing it. `AMS_SCALING_DUMP=<dir>` keeps each rung's spec dump and its kernel stream instead of letting a temporary directory take them, which is how `levers/kernel_all_configs.py --spec <dir>/spec-rN.json` re-times one rung, or times all six configurations on it. `AMS_SCALING_BINARY=<path>` measures that binary as-is rather than building the crate — the arm-at-another-revision knob, in the seat `AMS_SCALING_ROOT` held when the arm was a Python tree to import from. `AMS_DEEP_CLASSES=0`, `AMS_SIMULATED_PROSPECT=0` and `AMS_VOTE_SLOTS=0` reach the child through `kernel_exec.world_flags()`, and every row's `world` names the flags that rode, `shipping defaults` when none did.

Rows print as they land and the whole set is written to `scaling.json` beside this file. Run it from anywhere: `uv run python bench-the-rebuild/scaling/scaling.py [k ...]`.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from rebuild.pipeline import kernel_exec, kernel_io, table
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import kernel_parity, peak_rss
from rebuild.tools.cycle_timings import _INNER_LINE


def kernel_binary() -> Path:
    """The binary the rungs are measured against. `AMS_SCALING_BINARY` names one to take as it stands, which is how an arm at another revision is measured; otherwise the crate is built here, once, before any rung runs, so the sources on disk are what answered."""
    named = os.environ.get("AMS_SCALING_BINARY")
    if not named:
        kernel_exec.cargo_build()
        return kernel_exec.BINARY
    binary = Path(named).resolve()
    if not binary.is_file():
        raise SystemExit(f"AMS_SCALING_BINARY names no file at {binary}")
    return binary


def cpu_children() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def run_rung(binary: Path, spec_path: Path, out_dir: Path) -> dict:
    """One rung's fixpoint as one kernel child, with the wall, the CPU and the peak resident set the child itself spent. Its streams go to files and its two captures to temporary files rather than pipes, because the peak-RSS yardstick has to reap the child with `os.wait4` and a pipe would want a reader first; the CPU is a `RUSAGE_CHILDREN` delta, which attributes to this rung only because one child runs at a time. The CLI contract is read as strictly here as `kernel_exec.enumerate_configs` reads it — exit 2 is a usage refusal, any other nonzero is a complaint about the inputs, bytes on stdout are a failure since the answer is the files, and stderr on a clean exit may carry `[t]` lines and nothing else."""
    arguments = [
        str(binary),
        "enumerate-configs",
        str(spec_path),
        str(out_dir),
        "--configs=default",
        "--threads=1",
        *kernel_exec.world_flags(),
        "--timings",
    ]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        cpu0 = cpu_children()
        wall0 = time.perf_counter()
        process = subprocess.Popen(arguments, stdout=out, stderr=err)
        rss = peak_rss.reap_peak_rss_bytes(process)
        if rss is None:
            process.wait()
        wall = time.perf_counter() - wall0
        cpu = cpu_children() - cpu0
        out.seek(0)
        stdout = out.read()
        err.seek(0)
        stderr = err.read().decode(errors="replace").strip()
    if process.returncode == 2:
        raise SystemExit(
            f"the kernel rejected the invocation as a usage error, or does not support enumerate-configs: {stderr} ({' '.join(arguments)})"
        )
    if process.returncode != 0:
        raise SystemExit(f"the kernel exited {process.returncode} on enumerate-configs: {stderr}")
    if stdout:
        raise SystemExit(
            f"the kernel wrote {len(stdout)} bytes to stdout on a clean enumerate-configs exit, where the answer is the files"
        )
    stray = [line for line in stderr.split("\n") if line and not line.startswith("[t] ")]
    if stray:
        raise SystemExit(f"the kernel wrote a non-timing line to stderr on a clean exit: {stray[0]}")
    phases = {match.group(1): float(match.group(2)) for match in _INNER_LINE.finditer(stderr)}
    return {"wall": wall, "cpu": cpu, "rss": rss, "phases": phases}


def fold(sub: ResolvedSpec, stream: Path) -> tuple:
    """One rung's stream folded into its two tables, exactly as `run_m1._fold_stream` folds a build's: the plain ndjson the kernel wrote is packed beside itself into the gzip shape `kernel_io.read_transitions` reads, at the cheapest compression, since the copy is written, read once and unlinked."""
    packed = stream.with_name(f"{stream.stem}.ndjson.gz")
    with (
        stream.open("rb") as plain,
        packed.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as handle,
    ):
        shutil.copyfileobj(plain, handle)
    product = kernel_io.read_transitions(packed)
    packed.unlink()
    return table.assemble_tables(sub, product)


def fit(xs: list[int], ys: list[float]) -> float | None:
    """The least-squares slope of ln y on ln x — the whole-ladder exponent, which is the figure to quote rather than any one consecutive pair. None when fewer than two rungs carry a positive pair, or when every rung sits at the same alphabet size and there is no spread to fit against."""
    points = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y > 0]
    if len(points) < 2:
        return None
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    variance = sum((x - mean_x) ** 2 for x, _ in points)
    if not variance:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / variance


def exponent(slope: float | None) -> str:
    return "n/a" if slope is None else f"{slope:.2f}"


def report(rows: list[dict]) -> None:
    """The consecutive-pair exponents against runes, then the whole-ladder fit in both denominators. A pair whose CPU came back at zero prints no exponent rather than a ratio taken against a stopped clock."""
    print("\nrunes_a->runes_b   window exponent   cpu exponent")
    for a, b in zip(rows, rows[1:]):
        span = math.log(b["runes"] / a["runes"])
        windows = math.log(b["windows"] / a["windows"]) / span
        cpu = (
            f"cpu {math.log(b['cpu'] / a['cpu']) / span:5.2f}"
            if a["cpu"] > 0 and b["cpu"] > 0
            else "cpu   n/a"
        )
        print(f"{a['runes']:2d}->{b['runes']:2d}   windows {windows:5.2f}   {cpu}")
    if len(rows) < 2:
        print(f"\nthe whole-ladder fit needs two rungs; this run has {len(rows)}")
        return
    runes = [row["runes"] for row in rows]
    letters = [row["letters"] for row in rows]
    print(
        f"\nwhole-ladder fit over {len(rows)} rungs "
        f"(runes {min(runes)}..{max(runes)}, letters {min(letters)}..{max(letters)})"
    )
    for label in ("windows", "cpu"):
        counts = [row[label] for row in rows]
        by_runes = exponent(fit(runes, counts))
        by_letters = exponent(fit(letters, counts))
        print(f"  {label:<7} ~ runes^{by_runes}  letters^{by_letters}")


def main() -> int:
    spec = load_default_spec()
    order = kernel_parity.ladder_order(spec)
    rungs = [int(argument) for argument in sys.argv[1:]] or kernel_parity.ladder_rungs(order)
    binary = kernel_binary()
    dump = os.environ.get("AMS_SCALING_DUMP")
    rows: list[dict] = []
    with ExitStack() as stack:
        if dump:
            root = Path(dump)
            root.mkdir(parents=True, exist_ok=True)
        else:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for rung in rungs:
            sub = kernel_parity.sub_spec(spec, order, rung)
            runes = len(sub.runes)
            letters = sum(1 for name in sub.runes if not sub.runes[name].sequence)
            spec_path = root / f"spec-r{runes}.json"
            out_dir = root / f"r{runes}"
            kernel_io.write_spec(sub, spec_path)
            run = run_rung(binary, spec_path, out_dir)
            stream = out_dir / "transitions-default.ndjson"
            started = time.perf_counter()
            decision, treaty = fold(sub, stream)
            fold_s = time.perf_counter() - started
            if not dump:
                stream.unlink()
            rss = run["rss"]
            row = {
                "runes": runes,
                "letters": letters,
                "ligs": runes - letters,
                "windows": len(decision.transitions),
                "rules": len(decision.rules),
                "cells": len(decision.reachable_cells()),
                "cpu": round(run["cpu"], 3),
                "wall": round(run["wall"], 3),
                "spec_parse_s": run["phases"].get("spec_parse"),
                "enumerate_s": run["phases"].get("enumerate[default]"),
                "emit_s": run["phases"].get("emit[default]"),
                "fold_s": round(fold_s, 3),
                "rss_high_water_gb": None if rss is None else round(peak_rss.bytes_to_gb(rss), 2),
                "world": " ".join(kernel_exec.world_flags()) or "shipping defaults",
                "digest": table.table_digest(decision, treaty),
            }
            del decision, treaty
            rows.append(row)
            print(json.dumps(row), flush=True)
    json.dump(rows, open(HERE.parent / "scaling.json", "w"), indent=1)
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
