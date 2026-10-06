"""Measure how the M1 table build's cost grows with the modeled alphabet. Only part of the 44-letter alphabet is modeled, so the sweep shows how far toward it a constant-factor speedup can reach: a cost that grows steeply with the alphabet uses up a constant factor quickly. The sweep is meant to be re-run after each batch, not measured once. Porting changes the constant and not the exponent, so a series that grows steeper between batches means skipping work is due in any language; the threshold the whole-series fit is compared with is stated below. The largest size is the whole current alphabet, which checks that the sweep measures the real kernel and not a subset of it.

Each alphabet size runs as one kernel child, `build-tables --configs=default --threads=1`, the subcommand a build runs, which enumerates that alphabet and folds it. The series therefore times everything a build spends on one configuration. It also makes a row's `rss_high_water_gb` that size's own peak: the child's peak RSS, read when `peak_rss.reap_peak_rss_bytes` reaps it. `cpu` and `wall` cover the whole child: spec parse, enumeration, fold, and the artifacts it writes. A row recorded by an earlier form of the sweep, such as the Python fixpoint or a crate subcommand that did not fold, covers a different total, so compare it with a current row by exponent, not by constant. The child's `[t]` lines give `spec_parse_s`, `enumerate_s` and `fold_s`. A phase the child did not report is null, not 0.0, because a zero would look like a measurement.

Each size's font is built after its timed child and outside it, so `cpu` and `wall` cover the child alone. The shipped settlement lookup folds every settlement configuration, so the font needs a whole-set table build at that size, not the child's `--configs=default` one; `scratch_build.build_font` runs it, then the glyph chain and read-back, as run_m1 does for the live spec. The row takes three figures from the read-back report (`readback.budget_figures`): `settle_subtables`, the settlement lookup's subtable count N (format 2 plus format 3), which the subtable-offset headroom is spent on; `subtable_offset_headroom`, which read-back holds to `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`; and `largest_group_rule_bytes`, which read-back holds to `readback.GROUP_RULE_BYTES_CEILING`. The largest size is the whole current alphabet, so its figures are the live build's. The series cuts alphabets in `scaling_series.series_order`, not in the order letters migrate, so its N curve models growth, while `make cycle-timings ARGS='--by-commit'` lists N as each real build measured it. The font builds cost several whole-set table builds at the full alphabet, most of it in the largest sizes, and that is most of the sweep's wall time. Under `AMS_SCALING_BINARY` no font is built and the three figures are null, because the font build runs the crate on disk, not the binary being measured.

`windows`, `rules` and `cells` are read from the window payload the child wrote: its header gives the rules and the reachable cells, and its body is counted one line at a time. `digest` is the digest the child reports on stdout, at the grain of `table.table_digest`, so a size whose time changed can be told apart from a size whose output changed. Nothing here folds: the counts cost one streamed read of a file the child already wrote, where folding on this side would cost a parsed product and several gigabytes.

The report gives the consecutive-pair exponents against runes, then a least-squares fit of ln count on ln size over the whole series, against runes and against letters, for the windows, the CPU time and N. Quote the whole-series fit and say which size it is against. A single pair varies by a large fraction of the threshold because of ordinary scatter and because of which letters that size added. A rune exponent is the letter exponent times `d ln letters / d ln runes`, and the nested series moves that factor from below 1 to above 1 as it stops adding ligatures and starts adding letters. The threshold, in this fitted form, is about 4.5 against letters; against runes it is 4.5 times that factor, so it moves as the series grows. A fit past it means skipping work (the coverage settings in `doc/rebuild-design.md` §14.1) is due before the next batch, in any language.

Positional arguments are the rune counts to cut alphabets at, which need not be sizes of the series, and default to `scaling_series.series_sizes`. `AMS_SCALING_DUMP=<dir>` keeps each size's spec dump, the artifacts its child wrote, and its font build's out dir (`font-rN`) instead of deleting them with a temporary directory; `kernel_all_configs.py --spec <dir>/spec-rN.json` then times the enumeration at that size, in every settlement configuration or in the ones `--configs` names. `AMS_SCALING_BINARY=<path>` measures that binary as it is instead of building the crate, which is how a build at another revision is measured. `AMS_DEEP_CLASSES=0`, `AMS_SIMULATED_PROSPECT=0` and `AMS_FOLLOWER_PREFER_SLOTS=0` reach the child through `kernel_exec.mode_flags()`, and each row's `modes` names the flags passed, or `default modes` when none were.

Each row prints as its size finishes, and the whole set is written to `rebuild/out/scaling-series.json`. `rebuild/scaling-series.txt` is the checked-in record of the last run, the rows and the report as printed, and the add-a-new-letter checklist refreshes it after every migration batch. Run from the repo root: `uv run python -m rebuild.tools.scaling_sweep [k ...] | tee rebuild/scaling-series.txt`.
"""

from __future__ import annotations

import json
import math
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, redirect_stdout
from pathlib import Path

from rebuild.pipeline import conform, kernel_exec, kernel_io, readback, table
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import peak_rss, scaling_series, scratch_build
from rebuild.tools.console import INNER_LINE

REPO_ROOT = Path(__file__).resolve().parents[2]
ROWS_PATH = REPO_ROOT / "rebuild" / "out" / "scaling-series.json"
FONT_FIGURES = ("settle_subtables", "subtable_offset_headroom", "largest_group_rule_bytes")


def kernel_binary() -> Path:
    """Return the kernel binary to measure. `AMS_SCALING_BINARY` names one to use as it is, which is how a build at another revision is measured. Otherwise the crate is built here once, before any size runs, so the binary matches the sources on disk."""
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


def run_size(binary: Path, spec_path: Path, out_dir: Path, stamp: str) -> dict:
    """Build one alphabet size's tables in one kernel child and return the child's wall time, CPU time, peak RSS, `[t]` phases and digest. Its artifacts go to files and its stdout and stderr to temporary files, not pipes, because `peak_rss.reap_peak_rss_bytes` reaps the child with `os.wait4`, and a pipe would need a reader first. The CPU time is a `RUSAGE_CHILDREN` delta, which belongs to this size only because one child runs at a time. The CLI contract is checked as strictly as `kernel_exec.build_table_files` checks it: exit 2 is a usage error, any other nonzero exit is an error about the inputs, stdout has one `{config, digest}` line per configuration and nothing else, and stderr on a clean exit has only `[t]` lines."""
    arguments = [
        str(binary),
        "build-tables",
        str(spec_path),
        str(out_dir),
        "--configs=default",
        f"--inputs={stamp}",
        "--threads=1",
        *kernel_exec.mode_flags(),
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
            f"the kernel rejected the invocation as a usage error, or does not support build-tables: {stderr} ({' '.join(arguments)})"
        )
    if process.returncode != 0:
        raise SystemExit(f"the kernel exited {process.returncode} on build-tables: {stderr}")
    answers = [json.loads(line) for line in stdout.decode().splitlines()]
    if [answer.get("config") for answer in answers] != ["default"]:
        raise SystemExit(f"build-tables answered for {[a.get('config') for a in answers]}, not for default")
    stray = [line for line in stderr.split("\n") if line and not line.startswith("[t] ")]
    if stray:
        raise SystemExit(f"the kernel wrote a non-timing line to stderr on a clean exit: {stray[0]}")
    phases = {match.group(1): float(match.group(2)) for match in INNER_LINE.finditer(stderr)}
    return {
        "wall": wall,
        "cpu": cpu,
        "rss": rss,
        "phases": phases,
        "digest": answers[0]["digest"],
    }


def counts(payload: Path) -> tuple[int, int, int]:
    """Return one alphabet size's window, rule and reachable-cell counts from the payload the child wrote. The header gives the rules and cells. The body is counted one line at a time instead of loaded, so this process never holds that size's millions of rows."""
    with payload.open("rt", encoding="utf-8") as handle:
        _stamp, decision = table.read_windows(handle, windows=False)
        if tuple(handle.readline().rstrip("\n").split("\t")) != table.WINDOWS_COLUMNS:
            raise SystemExit(f"{payload}: window columns are not {table.WINDOWS_COLUMNS}")
        windows = sum(1 for _ in handle)
    return windows, len(decision.rules), len(decision.reachable_cells())


def font_figures(spec: ResolvedSpec, out_dir: Path) -> dict[str, int | None]:
    """Build `spec`'s font into `out_dir` over the whole settlement set (`scratch_build.build_font`) and return the `FONT_FIGURES` its read-back reports. A figure the report lacks is None. The build's own phase and progress lines go to stderr, so stdout, which `rebuild/scaling-series.txt` records, holds only the rows and the report."""
    with redirect_stdout(sys.stderr):
        built = scratch_build.build_font(spec, out_dir, list(conform.SETTLEMENT_CONFIGS))
    figures = readback.budget_figures(built.readback)
    format2, format3 = figures.get("settle_format2"), figures.get("settle_format3")
    return {
        "settle_subtables": None if format2 is None or format3 is None else format2 + format3,
        "subtable_offset_headroom": figures.get("subtable_offset_headroom"),
        "largest_group_rule_bytes": figures.get("largest_group_rule_bytes"),
    }


def fit(xs: list[int], ys: list[float | None]) -> float | None:
    """Return the least-squares slope of ln y on ln x, the whole-series exponent to quote instead of any one consecutive pair. A size whose y is None is left out. Returns None when fewer than two sizes have positive x and y, or when every size has the same x."""
    points = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y is not None and y > 0]
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


def pair_exponent(label: str, a: float | None, b: float | None, span: float) -> str:
    """Return one consecutive pair's exponent against runes, after `label`: ln(b / a) over `span`, the pair's ln rune ratio. It is `n/a` when the span is zero or either count is missing or not positive."""
    if not span or a is None or b is None or a <= 0 or b <= 0:
        return f"{label}   n/a"
    return f"{label} {math.log(b / a) / span:5.2f}"


def report(rows: list[dict]) -> None:
    """Print the consecutive-pair exponents against runes, then the whole-series fit against runes and against letters, for the windows, the CPU time and N (`settle_subtables`). A pair whose two sizes have the same rune count prints `n/a` for every exponent, and a pair with a zero or missing CPU time or N prints `n/a` for that exponent. Sizes from the command line can repeat, or resolve to the same rune count when they reach past the alphabet."""
    print("\nrunes_a->runes_b   window exponent   cpu exponent   subtable exponent")
    for a, b in zip(rows, rows[1:]):
        span = math.log(b["runes"] / a["runes"])
        windows = pair_exponent("windows", a["windows"], b["windows"], span)
        cpu = pair_exponent("cpu", a["cpu"], b["cpu"], span)
        subtables = pair_exponent("subtables", a.get("settle_subtables"), b.get("settle_subtables"), span)
        print(f"{a['runes']:2d}->{b['runes']:2d}   {windows}   {cpu}   {subtables}")
    if len(rows) < 2:
        print(f"\nthe whole-series fit needs two sizes; this run has {len(rows)}")
        return
    runes = [row["runes"] for row in rows]
    letters = [row["letters"] for row in rows]
    print(
        f"\nwhole-series fit over {len(rows)} sizes "
        f"(runes {min(runes)}..{max(runes)}, letters {min(letters)}..{max(letters)})"
    )
    for label, field in (("windows", "windows"), ("cpu", "cpu"), ("subtables", "settle_subtables")):
        counts = [row.get(field) for row in rows]
        by_runes = exponent(fit(runes, counts))
        by_letters = exponent(fit(letters, counts))
        print(f"  {label:<9} ~ runes^{by_runes}  letters^{by_letters}")


def main() -> int:
    spec = load_default_spec()
    order = scaling_series.series_order(spec)
    sizes = [int(argument) for argument in sys.argv[1:]] or scaling_series.series_sizes(order)
    binary = kernel_binary()
    build_fonts = not os.environ.get("AMS_SCALING_BINARY")
    dump = os.environ.get("AMS_SCALING_DUMP")
    rows: list[dict] = []
    with ExitStack() as stack:
        if dump:
            root = Path(dump)
            root.mkdir(parents=True, exist_ok=True)
        else:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for size in sizes:
            sub = scaling_series.sub_spec(spec, order, size)
            runes = len(sub.runes)
            letters = sum(1 for name in sub.runes if not sub.runes[name].sequence)
            spec_path = root / f"spec-r{runes}.json"
            out_dir = root / f"r{runes}"
            kernel_io.write_spec(sub, spec_path)
            run = run_size(binary, spec_path, out_dir, f"scaling-r{runes}")
            payload = out_dir / "windows-default.tsv"
            windows, rules, cells = counts(payload)
            if not dump:
                payload.unlink()
            figures: dict[str, int | None] = dict.fromkeys(FONT_FIGURES)
            if build_fonts:
                font_dir = root / f"font-r{runes}"
                figures = font_figures(sub, font_dir)
                if not dump:
                    shutil.rmtree(font_dir)
            rss = run["rss"]
            row = {
                "runes": runes,
                "letters": letters,
                "ligs": runes - letters,
                "windows": windows,
                "rules": rules,
                "cells": cells,
                "cpu": round(run["cpu"], 3),
                "wall": round(run["wall"], 3),
                "spec_parse_s": run["phases"].get("spec_parse"),
                "enumerate_s": run["phases"].get("enumerate[default]"),
                "fold_s": run["phases"].get("fold[default]"),
                "rss_high_water_gb": None if rss is None else round(peak_rss.bytes_to_gb(rss), 2),
                **figures,
                "modes": " ".join(kernel_exec.mode_flags()) or "default modes",
                "digest": run["digest"],
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
    ROWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ROWS_PATH.open("w") as handle:
        json.dump(rows, handle, indent=1)
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
