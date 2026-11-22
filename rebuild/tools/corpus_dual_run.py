"""Run two corpus builds of one build scenario and compare their output trees file for file, for the port of the corpus build's parent to Rust (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § The dual run).

The scenarios are `units_stage_fixtures`' (`open_build_source`): each prepares its prior corpus and its inputs over the source's base prior, the mini bundle's `first` build or the live base prior refreshed at the current tree. `--scenario all` runs every scenario of the source but `refusals`, which runs by name; a live run names one scenario.

`--engines` names the two sides, `python,rust` by default. Each side builds in a fresh process (`units_stage_fixtures.run_engine_build`, through its `python-build` child) as the cycle's step runs the build: under the Rust units engine, its journal redirected into the side's run directory, its own counts file, no cycle run unless the build's pass holds a fold claim another output took, and on macOS the allocator's large cache off. A Python side is `build.build_m1` at the harness's default width (`units_stage_fixtures.default_width`); a Rust side is `build._rust_build_m1`, which runs `ams-review build` (`corpus_exec.run_build`), at `--threads`, by default the same width. `--engines python,python` runs the first side at the default width and the second at two, which `_write_corpus` holds to write the same tree; a case that names its own width (`BuildCase.jobs`) runs at that width on both sides. The sides run one after the other, each case on both sides before the next case, and each side builds over a clone of the case's prior, or over its own output of the case before.

Before comparing bytes, each case compares the fields that move when the inputs move between the sides (`manifest.json`'s `repo_head`, `inputs_fingerprint` and `generated_at`, the unit store's and the ink-signature store's `environment`) where both sides wrote them, and both sides' zlib versions; a difference stops the run with exit status 3 naming the field, and the run is repeated rather than debugged. The comparison then takes the set of relative paths under each side's output, every file byte for byte (with the first differing offset and, for a gzip file, whether the inflated bytes agree), the `[phase]` and `[t]` labels in order, the `[warn]` lines, the plain miss notes and the `Pruned` line, each with the side's own output directory written `<out>`, the counts files as sorted lists, both exit statuses, and the scenario's checks (`BuildCase.check`, and `check_chain` over a chain) on every Python side.

The report is JSON under `var/rust-port/corpus-dual-run/` (`--report` names another path): per scenario and case, the files compared and each difference, and each side's width, exit status, wall time, phase seconds, peak footprints from its pool records (a Python side's parent and units engine, a Rust side's build process) and zlib version, a Rust side's as `ams-review version` prints it. Any difference, or a scenario check a Python side does not pass, exits 1. `--plant shard` changes one byte of the second side's first shard part after its last build, to show that the comparison reports it.

A live run is a heavy pass that starts with the live base prior's refresh, itself a whole build: detach it, watch its log (`doc/running-long-steps.md`), and run nothing else beside it.

Run from the repository root:

    uv run python -m rebuild.tools.corpus_dual_run --source mini --scenario first --threads 1
    uv run python -m rebuild.tools.corpus_dual_run --source mini --scenario all --engines python,python
    uv run python -m rebuild.tools.corpus_dual_run --source mini --scenario warm --engines python,python --plant shard
    uv run python -m rebuild.tools.corpus_dual_run --source mini --scenario refusals --engines python,python
"""

from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import json
import re
import sys
import time
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rebuild.pipeline import row_delta
from rebuild.review import unit_cache, units_exec
from rebuild.tools.units_stage_fixtures import (
    BUILD_SCENARIOS,
    REPO_ROOT,
    SOURCES,
    BuildCase,
    BuildRun,
    check_chain,
    default_width,
    open_build_source,
    run_engine_build,
    scenario_names,
)

REPORT_FORMAT = "ams-review-corpus-dual-run/1"
REPORT_DIR = REPO_ROOT / "var" / "rust-port" / "corpus-dual-run"
ENGINES = ("python", "rust")
PLANTABLE = ("shard",)
SECOND_PYTHON_WIDTH = 2
INPUTS_MOVED = 3
DIFFERENCES_SHOWN = 10
CHUNK = 1 << 20
MANIFEST_STAMPS = ("repo_head", "inputs_fingerprint", "generated_at")
MISS_NOTE = re.compile(r"(unit cache|ink-signature store): ")


@dataclass
class Side:
    """One side of the run: its engine, its units width, the directory its builds write under, the output of its last build, and the runs it has made."""

    index: int
    engine: str
    width: int
    directory: Path
    previous: Path | None = None
    runs: list[BuildRun] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"side {self.index} ({self.engine})"

    def zlib_version(self) -> str:
        if self.engine == "rust":
            return rust_zlib_version()
        return zlib.ZLIB_RUNTIME_VERSION


@functools.cache
def rust_zlib_version() -> str:
    """The zlib version the review binary links, from its `zlib` line in `ams-review version`."""
    units_exec.ensure_built()
    process = units_exec.spawn(["version"])
    output, stderr = process.communicate()
    if process.returncode != 0:
        raise SystemExit(f"ams-review version exited {process.returncode}: {stderr.decode(errors="replace")}")
    for line in output.decode("ascii").splitlines():
        name, _, value = line.partition("\t")
        if name == "zlib":
            return value
    raise SystemExit("ams-review version printed no zlib line")


@dataclass
class SideRun:
    """One side's build of one case: the run, its width, its wall time, and its counts file."""

    side: Side
    run: BuildRun
    width: int
    wall_seconds: float
    counts: Path
    journal: Path

    def phases(self) -> dict[str, float]:
        """Each `[t] review.build <phase>` line's seconds, by phase."""
        seconds: dict[str, float] = {}
        for line in self.run.log.splitlines():
            if line.startswith("[t] review.build "):
                words = line.partition("\t")[0].split()
                if len(words) > 3 and words[3].endswith("s"):
                    seconds[words[2]] = float(words[3][:-1])
        return seconds

    def pools(self) -> dict[str, dict]:
        """The pool records the build wrote to its journal, by unit: the width, the controller's peak footprint and each worker's."""
        pools: dict[str, dict] = {}
        if not self.journal.is_file():
            return pools
        for line in self.journal.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("kind") == "pool":
                pools[record["unit"]] = {
                    "width": record["width"],
                    "controller_peak_bytes": record["controller_peak_rss_bytes"],
                    "worker_peak_bytes": record["worker_peak_rss_bytes"],
                }
        return pools

    def describe(self) -> dict:
        pools = self.pools()
        units = pools.get("corpus-units-rs")
        built = pools.get("corpus-build-rs")
        return {
            "label": self.side.label,
            "engine": self.side.engine,
            "width": self.width,
            "returncode": self.run.returncode,
            "wall_seconds": round(self.wall_seconds, 3),
            "phase_seconds": self.phases(),
            "peak_footprint_bytes": {
                "parent": max((pool["controller_peak_bytes"] for pool in pools.values()), default=None),
                "units_engine": None if units is None else sum(units["worker_peak_bytes"].values()),
                "build_process": None if built is None else sum(built["worker_peak_bytes"].values()),
            },
            "pools": pools,
            "zlib": self.side.zlib_version(),
        }


@dataclass
class CaseResult:
    """What the comparison of one case found."""

    scenario: str
    case: str
    files_compared: int = 0
    differences: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    moved: list[dict] = field(default_factory=list)
    sides: list[dict] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.differences and not self.problems and not self.moved


# --- the build of one case ----------------------------------------------------------------------


def build_case(side: Side, case: BuildCase) -> SideRun:
    """Run one side's build of `case` over a clone of its prior: the case's own, or the side's output of the case before."""
    prior = case.prior if case.prior is not None else side.previous
    assert prior is not None, "a scenario's first build names its prior"
    out = side.directory / case.name
    logs = side.directory / "logs" / case.name
    width = case.jobs if case.jobs is not None else side.width
    print(f"corpus_dual_run: {side.label} builds {case.name} at {width}", file=sys.stderr)
    started = time.perf_counter()
    run = run_engine_build(
        side.engine,
        case.inputs,
        out,
        logs,
        case.name,
        prior=prior,
        jobs=width,
        claim_held_by=case.claim_held_by,
    )
    wall = time.perf_counter() - started
    side.previous = out
    side.runs.append(run)
    return SideRun(side, run, width, wall, logs / f"{case.name}.counts", logs / "journal.ndjson")


# --- the inputs-moved check ---------------------------------------------------------------------


def _signature_environment(out: Path) -> str | None:
    path = unit_cache.signature_store_path(out)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration:
        return None
    return header.get("environment") if isinstance(header, dict) else None


def input_stamps(side_run: SideRun) -> dict[str, str | None]:
    """The fields of one side's output that move when the inputs move, each None where the side wrote none, and the side's zlib version."""
    out = side_run.run.out
    stamps: dict[str, str | None] = {f"manifest.json {name}": None for name in MANIFEST_STAMPS}
    try:
        manifest = json.loads((out / "manifest.json").read_bytes())
    except OSError, ValueError:
        manifest = None
    if isinstance(manifest, dict):
        for name in MANIFEST_STAMPS:
            value = manifest.get(name)
            stamps[f"manifest.json {name}"] = None if value is None else json.dumps(value, sort_keys=True)
    try:
        head = row_delta.read_head(out, unit_cache.STORE_NAME)
    except row_delta.HeadError:
        head = None
    stamps["unit store environment"] = None if head is None else head.stamps.get("environment")
    stamps["ink-signature store environment"] = _signature_environment(out)
    stamps["zlib"] = side_run.side.zlib_version()
    return stamps


def inputs_moved(left: SideRun, right: SideRun) -> list[dict]:
    """Each field both sides wrote that differs between them."""
    left_stamps, right_stamps = input_stamps(left), input_stamps(right)
    return [
        {"field": name, "left": value, "right": right_stamps[name]}
        for name, value in left_stamps.items()
        if value is not None and right_stamps[name] is not None and value != right_stamps[name]
    ]


# --- the comparison -----------------------------------------------------------------------------


def tree_files(root: Path) -> list[str]:
    """Every file under `root`, by its path relative to it, as `rebuild/test_unit_cache.py`'s `_tree` takes a tree."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def first_difference(left: Path, right: Path) -> int | None:
    """The offset of the first byte at which the two files differ, the shorter one's length when one is a prefix of the other, or None when they are equal."""
    offset = 0
    with left.open("rb") as left_file, right.open("rb") as right_file:
        while True:
            left_chunk = left_file.read(CHUNK)
            right_chunk = right_file.read(CHUNK)
            if left_chunk != right_chunk:
                common = min(len(left_chunk), len(right_chunk))
                return offset + next(
                    (index for index in range(common) if left_chunk[index] != right_chunk[index]), common
                )
            if not left_chunk:
                return None
            offset += len(left_chunk)


def _inflated_digest(path: Path) -> str:
    """The sha256 of a gzip file's inflated bytes, every member read as one stream as Python's gzip reader reads it, or why it does not inflate."""
    digest = hashlib.sha256()
    try:
        with gzip.open(path, "rb") as stream:
            while chunk := stream.read(CHUNK):
                digest.update(chunk)
    except (OSError, EOFError, zlib.error) as error:
        return f"unreadable: {type(error).__name__}: {error}"
    return digest.hexdigest()


def _is_gzip(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"\x1f\x8b"


def compare_trees(left: Path, right: Path, result: CaseResult) -> None:
    """Compare the two outputs' paths and every common file's bytes, adding each difference to `result`."""
    left_files, right_files = tree_files(left), tree_files(right)
    left_set, right_set = set(left_files), set(right_files)
    for path in sorted(left_set ^ right_set):
        result.differences.append(
            {"kind": "path", "path": path, "left": path in left_set, "right": path in right_set}
        )
    for path in left_files:
        if path not in right_set:
            continue
        result.files_compared += 1
        offset = first_difference(left / path, right / path)
        if offset is None:
            continue
        difference: dict = {
            "kind": "bytes",
            "path": path,
            "offset": offset,
            "left_size": (left / path).stat().st_size,
            "right_size": (right / path).stat().st_size,
        }
        if _is_gzip(left / path) and _is_gzip(right / path):
            left_digest, right_digest = _inflated_digest(left / path), _inflated_digest(right / path)
            difference["inflated_agree"] = left_digest == right_digest and not left_digest.startswith(
                "unreadable"
            )
            if left_digest.startswith("unreadable") or right_digest.startswith("unreadable"):
                difference["inflated"] = {"left": left_digest, "right": right_digest}
        result.differences.append(difference)


def console_lines(run: BuildRun) -> dict[str, list[str]]:
    """The console lines the dual run compares, by kind, with the build's own output directory written `<out>`: the `[phase]` labels and the `[t]` labels without their readings, the `[warn]` lines, the plain miss notes and the `Pruned` line."""
    lines: dict[str, list[str]] = {"labels": [], "warns": [], "miss_notes": [], "pruned": []}
    out = str(run.out)
    for line in run.log.splitlines():
        line = line.replace(out, "<out>")
        if line.startswith("[phase] "):
            lines["labels"].append(line)
        elif line.startswith("[t] "):
            lines["labels"].append(" ".join(line.partition("\t")[0].split()[:3]))
        elif line.startswith("[warn] "):
            lines["warns"].append(line)
        elif MISS_NOTE.match(line):
            lines["miss_notes"].append(line)
        elif line.startswith("Pruned "):
            lines["pruned"].append(line)
    return lines


def _counts(side_run: SideRun) -> list[str]:
    return sorted(side_run.counts.read_text(encoding="utf-8").splitlines())


def compare_case(left: SideRun, right: SideRun, result: CaseResult) -> None:
    """Compare two sides' builds of one case, adding each difference to `result`."""
    if left.run.returncode != right.run.returncode:
        result.differences.append(
            {"kind": "status", "left": left.run.returncode, "right": right.run.returncode}
        )
    compare_trees(left.run.out, right.run.out, result)
    left_lines, right_lines = console_lines(left.run), console_lines(right.run)
    for kind, lines in left_lines.items():
        if lines != right_lines[kind]:
            result.differences.append({"kind": kind, "left": lines, "right": right_lines[kind]})
    left_counts, right_counts = _counts(left), _counts(right)
    if left_counts != right_counts:
        result.differences.append({"kind": "counts", "left": left_counts, "right": right_counts})


def describe_difference(difference: dict) -> str:
    kind = difference["kind"]
    if kind == "path":
        side = "the first side" if difference["left"] else "the second side"
        return f"{difference['path']} is only in {side}'s tree"
    if kind == "bytes":
        text = (
            f"{difference['path']} differs at byte {difference['offset']:,} "
            f"({difference['left_size']:,} and {difference['right_size']:,} bytes)"
        )
        if "inflated_agree" in difference:
            text += (
                ", its inflated bytes agree"
                if difference["inflated_agree"]
                else ", its inflated bytes differ too"
            )
        return text
    if kind == "status":
        return f"the builds exited {difference['left']} and {difference['right']}"
    return f"the {kind.replace('_', ' ')} differ"


# --- the plant ----------------------------------------------------------------------------------


def plant_shard(out: Path) -> dict:
    """Change one byte in the middle of the first shard part under `out`, keeping its length, and return where."""
    parts = [path for path in sorted((out / "units").glob("*.json")) if path.stat().st_size > 2]
    if not parts:
        raise SystemExit(f"--plant shard found no shard part under {out}")
    part = parts[0]
    data = bytearray(part.read_bytes())
    offset = len(data) // 2
    data[offset] ^= 0x01
    part.write_bytes(bytes(data))
    return {"kind": "shard", "path": part.relative_to(out).as_posix(), "offset": offset}


# --- the run ------------------------------------------------------------------------------------


def _widths(engines: tuple[str, ...], threads: int | None) -> tuple[int, ...]:
    """Each side's units width: the harness's default for a Python side, and two for a second Python side; `threads`, or else the default, for a Rust side."""
    if engines == ("python", "python"):
        return default_width(), SECOND_PYTHON_WIDTH
    return tuple(
        threads if engine == "rust" and threads is not None else default_width() for engine in engines
    )


def run_scenario(
    source: str, name: str, engines: tuple[str, ...], plant: str | None, threads: int | None = None
) -> tuple[list[CaseResult], dict | None]:
    """Prepare one scenario, build each of its cases on every side and compare them, and run the scenario's checks on every Python side. Stops at the first case whose inputs moved. Returns each case's result and the planted part, if any."""
    scenario = open_build_source(source, name)
    root = scenario.directory / "dual"
    sides = [
        Side(index, engine, width, root / f"{index}-{engine}")
        for index, (engine, width) in enumerate(zip(engines, _widths(engines, threads)))
    ]
    results: list[CaseResult] = []
    planted = None
    for number, case in enumerate(scenario.cases):
        result = CaseResult(name, case.name)
        results.append(result)
        side_runs = [build_case(side, case) for side in sides]
        if plant == "shard" and number == len(scenario.cases) - 1:
            planted = plant_shard(side_runs[1].run.out)
        result.sides = [side_run.describe() for side_run in side_runs]
        for side_run in side_runs:
            if side_run.side.engine == "python":
                result.problems += [
                    f"{side_run.side.label}: {problem}" for problem in case.check(side_run.run)
                ]
        result.moved = inputs_moved(side_runs[0], side_runs[1])
        if result.moved:
            return results, planted
        compare_case(side_runs[0], side_runs[1], result)
        _print_case(source, result)
    if name == "chain":
        chained = CaseResult(name, "the chain")
        for side in sides:
            if side.engine == "python":
                chained.problems += [f"{side.label}: {problem}" for problem in check_chain(side.runs)]
        results.append(chained)
        _print_case(source, chained)
    return results, planted


def _print_case(source: str, result: CaseResult) -> None:
    where = f"{source}-{result.scenario} {result.case}"
    for difference in result.differences[:DIFFERENCES_SHOWN]:
        print(f"{where}: {describe_difference(difference)}", file=sys.stderr)
    if len(result.differences) > DIFFERENCES_SHOWN:
        print(
            f"{where}: and {len(result.differences) - DIFFERENCES_SHOWN:,} more differences", file=sys.stderr
        )
    for problem in result.problems:
        print(f"{where}: {problem}", file=sys.stderr)
    if result.ok():
        agreed = f"{result.files_compared:,} files agree" if result.files_compared else "its checks pass"
        print(f"{where}: {agreed}", file=sys.stderr)


def write_report(
    path: Path,
    source: str,
    names: Sequence[str],
    engines: tuple[str, ...],
    results: Sequence[CaseResult],
    planted: dict | None,
    ok: bool,
) -> None:
    payload = {
        "format": REPORT_FORMAT,
        "ok": ok,
        "source": source,
        "scenarios": list(names),
        "engines": list(engines),
        "planted": planted,
        "cases": [
            {
                "scenario": result.scenario,
                "case": result.case,
                "ok": result.ok(),
                "files_compared": result.files_compared,
                "inputs_moved": result.moved,
                "differences": result.differences,
                "problems": result.problems,
                "sides": result.sides,
            }
            for result in results
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corpus_dual_run", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--scenario", choices=(*BUILD_SCENARIOS, "all"), required=True)
    parser.add_argument(
        "--engines",
        default="python,rust",
        help="the two sides' engines, comma-separated (default: python,rust)",
    )
    parser.add_argument(
        "--threads", type=int, help="a Rust side's width (default: the harness's default width)"
    )
    parser.add_argument("--plant", choices=PLANTABLE, help="corrupt one file of the second side's last build")
    parser.add_argument("--report", type=Path, help="where to write the JSON report")
    args = parser.parse_args(argv)
    engines = tuple(args.engines.split(","))
    if len(engines) != 2:
        parser.error("--engines names two engines")
    for engine in engines:
        if engine not in ENGINES:
            parser.error(
                f"engine {engine!r} is not one the harness runs; the engines are {', '.join(ENGINES)}"
            )
    if args.threads is not None and args.threads < 1:
        parser.error("--threads is at least one")
    if args.source == "live" and args.scenario == "all":
        parser.error("a live run names one scenario")
    names = scenario_names(args.source, args.scenario)
    for name in names:
        if args.source not in BUILD_SCENARIOS[name]:
            parser.error(f"the {name} scenario runs on {', '.join(BUILD_SCENARIOS[name])}, not {args.source}")
    stem = (
        "-".join((args.source, args.scenario, *engines))
        + (f"-threads-{args.threads}" if args.threads is not None else "")
        + (f"-plant-{args.plant}" if args.plant else "")
    )
    path = args.report or REPORT_DIR / f"{stem}.json"
    results: list[CaseResult] = []
    planted = None
    moved = False
    for name in names:
        found, planted_here = run_scenario(args.source, name, engines, args.plant, args.threads)
        results += found
        planted = planted or planted_here
        if any(result.moved for result in found):
            moved = True
            break
    ok = not moved and all(result.ok() for result in results)
    write_report(path, args.source, names, engines, results, planted, ok)
    if planted is not None:
        print(
            f"planted: byte {planted['offset']:,} of {planted['path']} on the second side's last build",
            file=sys.stderr,
        )
    compared = sum(result.files_compared for result in results)
    print(
        f"corpus_dual_run: compared {compared:,} files in {len(results)} cases; report {path}",
        file=sys.stderr,
    )
    if moved:
        for result in results:
            for item in result.moved:
                print(
                    f"corpus_dual_run: the inputs moved between the sides at {result.scenario} {result.case}: "
                    f"{item['field']} is {item['left']} and {item['right']}; run again",
                    file=sys.stderr,
                )
        return INPUTS_MOVED
    if ok:
        print("corpus_dual_run: the builds agree file for file", file=sys.stderr)
        return 0
    print("corpus_dual_run: the builds differ", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
