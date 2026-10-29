"""The fault kit: inject each fault of `doc/rebuild-design.md` §10 that some gate catches, as a patch against the current head, into a scratch clone, and check that its catching gates still catch it. `make perf-check` runs it on a speed-up candidate's tree for the rows whose catching gates the candidate touches (`doc/perf-check.md`); the kit imports nothing from the check, and nothing in the build imports the kit.

The manifest, `rebuild/perf/faults.yaml` (format `ams-fault-kit/1`), is the authority on the rows. Its `gates` mapping is the closed vocabulary of catching gates: each names the modules whose code decides it (`modules`), the files that call it (`files`), and whether the crate's output feeds it (`crate`). Each row names its §10 row (`section10`), a `slug` saying what the fault does (§10 describes it), its patch (`faults/<id>.patch`, relative to the manifest), the effect files a faulted build rewrites (`effect`), the gates that catch the fault but that no step can reach (`unreached`), and its steps. A step is an argv run at the clone's root, the gate it reaches (none for a step that only builds what a later step reads), the exit status a catch gives (`exit`), the substrings a catch must show (`require`), and the substrings it is expected to show (`detail`). Both take `output`, searched in the step's merged stdout and stderr, and `files`, a map from a root-relative path to the substrings searched in that file after the step. The required part is small on purpose, the gate's fixed message prefix and the faulted glyph where the message names one, because stance names, rule texts and positions move with ordinary rune edits; `detail` is reported as matched or missing and never decides a result. A row's gates are its steps' gates followed by its `unreached` ones.

Selection. `--rows all` selects every row, and a comma-separated id list selects those rows. `--rows auto` reads `--changed`, one repo-relative path per line (both sides of a rename), and selects a row when a changed path is a file its patch modifies, its own patch file, the manifest or this module (the last two select every row), or a path one of its gates covers, reached or not: the gate's `files`, the import closure of its `modules` (`contracts_closure.ImportGraph` walked on `--root`, kept to the `.py` files under `rebuild/` and `tools/`, so a candidate's new imports count), and, for a `crate` gate, the crate's `Cargo.toml`, `Cargo.lock` and `src/`. An empty `--changed` selects nothing. A gate the change touches that no selected step reaches is reported under `unreached` with the selected rows that carry it; under `all`, an id list, or a change to a row's own patch, its patched files, the manifest or this module, every gate of the rows so selected counts as touched.

A run:

1. It refuses with exit 2 when `--root` is this module's own checkout, does not lie under that checkout's `var/perf/`, where scratch clones live, is not the top of a git work tree, or shows any `git status --porcelain` line; when `--snapshot`, `--log-dir` or `--out` lies inside `--root`; when `--snapshot` or `<snapshot>-base` exists as anything but an empty directory or a snapshot the kit made (it holds `SNAPSHOT_MARKER`), because the kit replaces both; and when `--base` names no commit in `--root`.
2. The control runs each distinct step argv of the selected rows once on the unfaulted tree, ordered by the earliest step position each holds, so the bare `run_m1` precedes the steps that read its tables. Controls run without a timeout. A control that exits nonzero refuses the run with exit 2, naming the command, since a catch has to be the fault's doing, and so does a control that leaves the tree unclean. It records each control's elapsed time and the raw SHA-256 of every effect file the selected rows name.
3. The snapshot copies `<root>/rebuild/out` to `<snapshot>/out` (`landing.clone_tree`) beside the marker.
4. Each selected row runs, the rows whose patch touches only Python first and then the rows whose patch touches the crate, each group by id, so the crate builds once per crate row: `git apply --check` (a failure makes the row `stale-patch`), `git apply`, then every step in order, each in its own session with stdout and stderr merged into `<log-dir>/<id>-<k>.log`, `cwd` at the root, under the environment below. A step's timeout is `STEP_TIMEOUT_FACTOR` times its argv's control time or `MIN_STEP_TIMEOUT_S`, whichever is longer; on timeout its session is killed. Then, whatever happened, `git apply -R` reverts the patch, `git status --porcelain` must come back empty, and the snapshot's copy replaces `rebuild/out`.
5. The row's result comes from its steps (`classify`). `caught`: every step exits as expected with every required substring present, and a step expected to exit 0 changed an effect file. Otherwise the first step that misses decides, since a later step can run on outputs an earlier one never rewrote: `wrong-catch` when it timed out, exited nonzero, or exited 0 with an effect file changed but a required substring missing; `inert` when it exited 0 with every effect file equal to the control's, so the fault did nothing on this tree; `missed` when it should have failed and exited 0 with an effect file changed, so the fault took effect and its gate let it through.
6. With `--base`, the rows not caught run again on the base: the kit checks out that commit, runs those rows' controls into `<snapshot>-base`, runs the rows the same way with logs under `<log-dir>/base/`, then checks out the commit it found and restores that commit's snapshot. When the base's control fails, each row keeps its first result and `base_control` holds the reason. A row the base catches becomes `lost` when the tree under test gave `missed` or `wrong-catch`, since the change lost or altered the catch, and `re-author` when it gave `inert` or `stale-patch`, since the change moved the injection site and the patch is re-authored against it in the same change. A row the base does not catch either is `drifted`: it no longer fits the head, which is not the change's doing.
7. It writes `--out` and exits 0 when every selected row is caught (or none is selected), else 1.

`--out` holds `format`, `root`, `tree` (the root's HEAD), `base` (the base commit or null), `selected`, `unreached`, `control` and `base_control` (each argv's exit, elapsed time, effect digests and log), and per row its id, `section10`, `slug`, `gates`, `patch`, `result`, `on_candidate`, `on_base`, `elapsed_s`, `note` (git's message for a `stale-patch`), and `steps` and `base_steps`, each step with its gate, argv, expected and actual exit, whether it timed out, its elapsed time, the required substrings missing, the detail matched and missing, the effect files changed (null after a nonzero exit, when nothing is compared) and its log. A refused run writes `refused` with the reason, and a run an error or a signal stopped writes `error`.

Environment: every control and step runs with only `HOME`, `PATH`, `USER`, `LOGNAME`, `LANG` and `TMPDIR` passed through, the whitelist `make perf-check` gives its clone passes, plus `UV_CACHE_DIR` at this checkout's `.uv-cache` and `AMS_CYCLE_RUN=fault-kit` (`cycle_timings.CYCLE_RUN_ENV`), which keeps run_m1's CLI and `--conform-only` from recording check lines or settling greens. Git runs without the caller's `GIT_*` variables, so an inherited `GIT_DIR` cannot point it at another repository.

The kit leaves `--root` clean, at the commit it found, with `rebuild/out` restored to the control snapshot, even on failure. SIGINT, SIGTERM and SIGHUP stop a run as they stop a cycle pass (`artifact_cycle.stop_signals`): the running step's session is killed, the row is reverted, and the exit status is 128 plus the signal's number. A revert that leaves the tree dirty stops the run with exit 1, after the kit resets the tracked files to the commit it found and removes the untracked files the run made (the tree had none when it started). What a row leaves behind is outside `rebuild/out` and the tracked files: the crate's release binary under `rebuild/kernel-rs/target/`, which the next use rebuilds by mtime, and whatever `uv run` synced into the root's `.venv`.

Run as: uv run python -m rebuild.tools.fault_kit --root DIR --snapshot DIR --rows auto|all|ID[,ID...] [--changed FILE] [--base REF] --out FILE --log-dir DIR. `--list [--rows ...] [--changed FILE] [--root DIR]` prints each row's id, gates, §10 row, patch and whether it is selected, then the unreached gates; it needs no clone, walks the closures on `--root` when given and on this checkout otherwise, and selects `all` unless `--rows` says otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rebuild.review.landing import clone_tree
from rebuild.tools.artifact_cycle import CycleStopped, stop_signals
from rebuild.tools.closure_record import KERNEL_MANIFEST, KERNEL_PREFIX
from rebuild.tools.contracts_closure import ImportGraph
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV

ROOT = Path(__file__).resolve().parents[2]
FORMAT = "ams-fault-kit/1"
PERF_DIR = "rebuild/perf"
MANIFEST_PATH = f"{PERF_DIR}/faults.yaml"
KIT_PATH = "rebuild/tools/fault_kit.py"
MANIFEST = ROOT / MANIFEST_PATH
KIT_WIDE_PATHS = frozenset((MANIFEST_PATH, KIT_PATH))
CRATE_FILES = frozenset((KERNEL_MANIFEST, f"{KERNEL_PREFIX}Cargo.lock"))
CRATE_SOURCES = f"{KERNEL_PREFIX}src/"
CLOSURE_PREFIXES = ("rebuild/", "tools/")
ENV_PASSED = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "TMPDIR")
CYCLE_RUN_VALUE = "fault-kit"
OUTPUT = "output"
SNAPSHOT_MARKER = "fault-kit-snapshot.json"
MIN_STEP_TIMEOUT_S = 600.0
STEP_TIMEOUT_FACTOR = 3.0
KILL_GRACE_S = 10.0
CAUGHT = "caught"
STALE_PATCH = "stale-patch"


class ManifestError(ValueError):
    """The manifest does not have the shape this module's docstring describes."""


class Refused(Exception):
    """A reason to stop before any row runs, with exit 2."""


class KitError(Exception):
    """A row's revert left the root dirty."""


@dataclass(frozen=True)
class Gate:
    name: str
    modules: tuple[str, ...]
    crate: bool
    files: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    argv: tuple[str, ...]
    gate: str | None
    exit: int
    require: tuple[tuple[str, str], ...]
    detail: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Row:
    id: str
    section10: int
    slug: str
    patch: str
    patch_file: Path
    patched_files: frozenset[str]
    effect: tuple[str, ...]
    steps: tuple[Step, ...]
    unreached: tuple[str, ...]

    @property
    def gates(self) -> tuple[str, ...]:
        reached = [step.gate for step in self.steps if step.gate is not None]
        return tuple(dict.fromkeys([*reached, *self.unreached]))

    @property
    def patch_path(self) -> str:
        return f"{PERF_DIR}/{self.patch}"

    @property
    def crate(self) -> bool:
        return any(path.startswith(KERNEL_PREFIX) for path in self.patched_files)


@dataclass(frozen=True)
class Manifest:
    gates: Mapping[str, Gate]
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Selection:
    rows: tuple[Row, ...]
    unreached: Mapping[str, tuple[str, ...]]


@dataclass
class StepRecord:
    gate: str | None
    argv: list[str]
    expected_exit: int
    exit: int | None
    timed_out: bool
    elapsed_s: float
    required_missing: list[str]
    detail_matched: list[str]
    detail_missing: list[str]
    effects_changed: list[str] | None
    log: str


@dataclass
class RowRun:
    result: str
    steps: list[StepRecord] = field(default_factory=list)
    elapsed_s: float = 0.0
    note: str | None = None


@dataclass
class Control:
    argv: list[str]
    exit: int | None
    elapsed_s: float
    effects: dict[str, str | None]
    log: str


@dataclass(frozen=True)
class Side:
    """What a row's run on one tree compares against: the control's effect digests and elapsed times, the snapshot to restore, and where the logs go."""

    effects: Mapping[str, str | None]
    elapsed: Mapping[tuple[str, ...], float]
    snapshot: Path
    log_dir: Path


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be a mapping")
    return value


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestError(f"{where} must be a list of strings")
    return tuple(value)


def _substrings(value: object, where: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    spec = _mapping(value, where)
    unknown = set(spec) - {OUTPUT, "files"}
    if unknown:
        raise ManifestError(f"{where} has unknown keys {sorted(unknown)}")
    pairs = [(OUTPUT, text) for text in _strings(spec.get(OUTPUT, []), f"{where}.output")]
    for path, texts in _mapping(spec.get("files", {}), f"{where}.files").items():
        pairs.extend((path, text) for text in _strings(texts, f"{where}.files.{path}"))
    return tuple(pairs)


def patched_files(patch_text: str) -> frozenset[str]:
    """The repo-relative paths a patch modifies: both sides of each `--- a/` and `+++ b/` header, without `/dev/null`."""
    paths: set[str] = set()
    for line in patch_text.splitlines():
        for prefix in ("--- a/", "+++ b/"):
            if line.startswith(prefix):
                paths.add(line[len(prefix) :].split("\t", 1)[0])
    return frozenset(paths)


def parse_manifest(data: object, directory: Path) -> Manifest:
    """Check a loaded manifest's shape and resolve each row's patch against `directory`, the manifest's own directory."""
    document = _mapping(data, "the manifest")
    if document.get("format") != FORMAT:
        raise ManifestError(f"the manifest's format must be {FORMAT}")
    gates: dict[str, Gate] = {}
    for name, raw in _mapping(document.get("gates"), "gates").items():
        spec = _mapping(raw, f"gate {name}")
        crate = spec.get("crate")
        if not isinstance(crate, bool):
            raise ManifestError(f"gate {name}: crate must be true or false")
        gates[name] = Gate(
            name,
            _strings(spec.get("modules"), f"gate {name}: modules"),
            crate,
            _strings(spec.get("files"), f"gate {name}: files"),
        )
    raw_rows = document.get("rows")
    if not isinstance(raw_rows, list):
        raise ManifestError("rows must be a list")
    rows: list[Row] = []
    for raw in raw_rows:
        spec = _mapping(raw, "a row")
        row_id = spec.get("id")
        if not isinstance(row_id, str) or not row_id:
            raise ManifestError("every row needs a string id")
        where = f"row {row_id}"
        if any(row.id == row_id for row in rows):
            raise ManifestError(f"{where} appears twice")
        section10 = spec.get("section10")
        slug = spec.get("slug")
        patch = spec.get("patch")
        if not isinstance(section10, int) or isinstance(section10, bool):
            raise ManifestError(f"{where}: section10 must be a row number")
        if not isinstance(slug, str) or not slug:
            raise ManifestError(f"{where}: slug must be a string")
        if not isinstance(patch, str) or patch != f"faults/{row_id}.patch":
            raise ManifestError(f"{where}: patch must be faults/{row_id}.patch")
        patch_file = directory / patch
        try:
            patch_text = patch_file.read_text(encoding="utf-8")
        except OSError as error:
            raise ManifestError(f"{where}: cannot read {patch_file}: {error.strerror}") from None
        raw_steps = spec.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ManifestError(f"{where}: steps must be a non-empty list")
        steps: list[Step] = []
        for index, raw_step in enumerate(raw_steps, 1):
            step = _mapping(raw_step, f"{where} step {index}")
            gate = step.get("gate")
            if gate is not None and gate not in gates:
                raise ManifestError(f"{where} step {index}: unknown gate {gate!r}")
            exit_status = step.get("exit")
            if not isinstance(exit_status, int) or isinstance(exit_status, bool):
                raise ManifestError(f"{where} step {index}: exit must be an integer")
            argv = _strings(step.get("argv"), f"{where} step {index}: argv")
            if not argv:
                raise ManifestError(f"{where} step {index}: argv is empty")
            steps.append(
                Step(
                    argv,
                    gate if isinstance(gate, str) else None,
                    exit_status,
                    _substrings(step.get("require"), f"{where} step {index}: require"),
                    _substrings(step.get("detail"), f"{where} step {index}: detail"),
                )
            )
        unreached = _strings(spec.get("unreached", []), f"{where}: unreached")
        unknown = [gate for gate in unreached if gate not in gates]
        if unknown:
            raise ManifestError(f"{where}: unknown unreached gates {unknown}")
        if not any(step.gate is not None for step in steps):
            raise ManifestError(f"{where}: no step reaches a gate")
        effect = _strings(spec.get("effect"), f"{where}: effect")
        if not effect:
            raise ManifestError(f"{where}: effect names no file")
        rows.append(
            Row(
                row_id,
                section10,
                slug,
                patch,
                patch_file,
                patched_files(patch_text),
                effect,
                tuple(steps),
                unreached,
            )
        )
    return Manifest(gates, tuple(rows))


def load_manifest(path: Path = MANIFEST) -> Manifest:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ManifestError(f"cannot read {path}: {error.strerror}") from None
    except yaml.YAMLError as error:
        raise ManifestError(f"{path} does not parse: {error}") from None
    return parse_manifest(data, path.parent)


def module_files(root: Path, module: str) -> list[str]:
    """The root-relative files importing `module` executes first: each package `__init__.py` on its path and the module itself."""
    parts = module.split(".")
    found = [
        "/".join([*parts[:depth], "__init__.py"])
        for depth in range(1, len(parts))
        if (root.joinpath(*parts[:depth]) / "__init__.py").is_file()
    ]
    target = root.joinpath(*parts)
    if target.with_suffix(".py").is_file():
        found.append("/".join(parts) + ".py")
    elif (target / "__init__.py").is_file():
        found.append("/".join([*parts, "__init__.py"]))
    return found


def gate_paths(gate: Gate, graph: ImportGraph) -> frozenset[str]:
    """The files a gate covers apart from the crate: its `files` and the import closure of its modules under `rebuild/` and `tools/`."""
    closure: set[str] = set()
    for module in gate.modules:
        for rel in module_files(graph.root, module):
            closure |= graph.closure(rel)
    return frozenset(gate.files) | {path for path in closure if path.startswith(CLOSURE_PREFIXES)}


def crate_path(path: str) -> bool:
    return path in CRATE_FILES or path.startswith(CRATE_SOURCES)


def touched_gates(manifest: Manifest, changed: frozenset[str], root: Path) -> set[str]:
    """The gates whose files, import closure or crate a changed path is in."""
    if not changed:
        return set()
    graph = ImportGraph(root)
    crate_changed = any(crate_path(path) for path in changed)
    return {
        name
        for name, gate in manifest.gates.items()
        if (gate.crate and crate_changed) or not changed.isdisjoint(gate_paths(gate, graph))
    }


def named_rows(manifest: Manifest, rows: str) -> list[Row]:
    """The rows a comma-separated id list names, in manifest order. Raises on an id the manifest lacks."""
    wanted = [part.strip() for part in rows.split(",") if part.strip()]
    unknown = [row_id for row_id in wanted if not any(row.id == row_id for row in manifest.rows)]
    if not wanted or unknown:
        raise ManifestError(f"--rows names no row of the manifest: {', '.join(unknown) or repr(rows)}")
    return [row for row in manifest.rows if row.id in wanted]


def select(manifest: Manifest, rows: str, changed: frozenset[str] | None, root: Path) -> Selection:
    """Choose the rows `--rows` names, reading `changed` for `auto`, and the touched gates no chosen step reaches (this module's docstring, "Selection")."""
    if rows == "auto":
        if changed is None:
            raise ManifestError("--rows auto needs --changed")
        by_gate = touched_gates(manifest, changed, root)
        kit_wide = not changed.isdisjoint(KIT_WIDE_PATHS)
        own = [
            row
            for row in manifest.rows
            if kit_wide or row.patch_path in changed or not changed.isdisjoint(row.patched_files)
        ]
        chosen = [row for row in manifest.rows if row in own or not by_gate.isdisjoint(row.gates)]
        touched = by_gate | {gate for row in own for gate in row.gates}
    else:
        chosen = list(manifest.rows) if rows == "all" else named_rows(manifest, rows)
        touched = {gate for row in chosen for gate in row.gates}
    reached = {step.gate for row in chosen for step in row.steps}
    unreached = {
        gate: tuple(row.id for row in chosen if gate in row.unreached)
        for gate in manifest.gates
        if gate in touched and gate not in reached
    }
    return Selection(tuple(chosen), unreached)


def unreached_text(unreached: Mapping[str, Sequence[str]]) -> str:
    if not unreached:
        return "unreached: none"
    parts = [
        f"{gate} (rows {', '.join(rows)})" if rows else f"{gate} (no row)" for gate, rows in unreached.items()
    ]
    return f"unreached: {'; '.join(parts)}"


def list_text(manifest: Manifest, selection: Selection) -> str:
    chosen = {row.id for row in selection.rows}
    table = [("id", "gates", "section10", "patch", "selected")]
    table.extend(
        (row.id, ",".join(row.gates), str(row.section10), row.patch_path, "yes" if row.id in chosen else "no")
        for row in manifest.rows
    )
    widths = [max(len(line[column]) for line in table) for column in range(len(table[0]))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip() for line in table]
    return "\n".join([*lines, unreached_text(selection.unreached)])


def classify(steps: Sequence[StepRecord]) -> str:
    """A row's result from the first step that misses its expectation, as this module's docstring gives it (step 5 of a run), or `caught` when none does."""

    def met(step: StepRecord) -> bool:
        if step.timed_out or step.exit != step.expected_exit or step.required_missing:
            return False
        return step.expected_exit != 0 or bool(step.effects_changed)

    missed = next((step for step in steps if not met(step)), None)
    if missed is None:
        return CAUGHT
    if missed.timed_out or missed.exit != 0:
        return "wrong-catch"
    if missed.effects_changed == []:
        return "inert"
    if missed.expected_exit != 0:
        return "missed"
    return "wrong-catch"


def final_result(on_candidate: str, on_base: str) -> str:
    """A row's result once the base has run it too (step 6 of a run)."""
    if on_base != CAUGHT:
        return "drifted"
    if on_candidate in ("missed", "wrong-catch"):
        return "lost"
    return "re-author"


def file_digest(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except FileNotFoundError:
        return None


def effect_digests(root: Path, effects: Iterable[str]) -> dict[str, str | None]:
    return {path: file_digest(root / path) for path in effects}


def search(pairs: Sequence[tuple[str, str]], output: str, root: Path) -> tuple[list[str], list[str]]:
    """Split `pairs` into the substrings found and the ones missing, each named as the manifest names it: bare for the output, `<path>: <text>` for a file."""
    texts: dict[str, str] = {OUTPUT: output}
    found: list[str] = []
    missing: list[str] = []
    for source, text in pairs:
        if source not in texts:
            try:
                texts[source] = (root / source).read_text(encoding="utf-8", errors="replace")
            except OSError:
                texts[source] = ""
        label = text if source == OUTPUT else f"{source}: {text}"
        (found if text in texts[source] else missing).append(label)
    return found, missing


def git_env() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=check, env=git_env()
    )


def porcelain(root: Path) -> str:
    return git(root, "status", "--porcelain").stdout.strip()


def step_env(kit_root: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_PASSED if name in os.environ}
    env["UV_CACHE_DIR"] = str(kit_root / ".uv-cache")
    env[CYCLE_RUN_ENV] = CYCLE_RUN_VALUE
    return env


def _signal_session(pid: int, signum: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signum)


def _kill_session(proc: subprocess.Popen[bytes]) -> None:
    _signal_session(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        _signal_session(proc.pid, signal.SIGKILL)
        proc.wait()
    _signal_session(proc.pid, signal.SIGKILL)


def run_command(
    argv: Sequence[str], *, root: Path, env: Mapping[str, str], log: Path, timeout: float | None
) -> tuple[int, bool, float]:
    """Run one argv in its own session at `root`, its stdout and stderr merged into `log`. Returns its exit status, whether it timed out, and its elapsed time. A timeout or an exception here kills the session, and so does a normal exit, for anything the command left running."""
    log.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    timed_out = False
    with log.open("wb") as out:
        proc = subprocess.Popen(
            list(argv),
            cwd=root,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_session(proc)
        except BaseException:
            _kill_session(proc)
            raise
        _signal_session(proc.pid, signal.SIGKILL)
    return proc.returncode, timed_out, time.monotonic() - start


def control_argvs(rows: Sequence[Row]) -> list[tuple[str, ...]]:
    """Each distinct step argv of `rows`, ordered by the earliest step position it holds in any row, then by first appearance."""
    first: dict[tuple[str, ...], tuple[int, int]] = {}
    seen = 0
    for row in rows:
        for position, step in enumerate(row.steps):
            if step.argv not in first:
                first[step.argv] = (position, seen)
                seen += 1
            else:
                first[step.argv] = min(first[step.argv], (position, first[step.argv][1]))
    return sorted(first, key=lambda argv: first[argv])


def run_control(
    rows: Sequence[Row],
    root: Path,
    env: Mapping[str, str],
    log_dir: Path,
    controls: list[Control],
    snapshot: Path,
    tree: str,
) -> Side:
    """Run each distinct step argv of `rows` on the unfaulted tree, appending each to `controls` as it finishes, and refuse when one exits nonzero or the tree is no longer clean; then snapshot `rebuild/out` (steps 2 and 3 of a run). Returns what the rows' runs on this tree compare against."""
    effects = list(dict.fromkeys(path for row in rows for path in row.effect))
    for index, argv in enumerate(control_argvs(rows), 1):
        log = log_dir / f"control-{index}.log"
        code, _, elapsed = run_command(argv, root=root, env=env, log=log, timeout=None)
        controls.append(Control(list(argv), code, elapsed, effect_digests(root, effects), str(log)))
        if code != 0:
            raise Refused(
                f"the control `{' '.join(argv)}` exited {code} on the unfaulted tree, so a catch would not be the fault's doing; see {log}"
            )
    if porcelain(root):
        raise Refused("the control changed the root's tracked or untracked files")
    take_snapshot(root, snapshot, tree)
    return Side(
        controls[-1].effects if controls else {},
        {tuple(control.argv): control.elapsed_s for control in controls},
        snapshot,
        log_dir,
    )


def take_snapshot(root: Path, snapshot: Path, tree: str) -> None:
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.mkdir(parents=True)
    (snapshot / SNAPSHOT_MARKER).write_text(
        json.dumps({"format": FORMAT, "root": str(root), "tree": tree}) + "\n"
    )
    clone_tree(root / "rebuild" / "out", snapshot / "out")


def restore_out(root: Path, snapshot: Path) -> None:
    out = root / "rebuild" / "out"
    if out.is_symlink() or out.is_file():
        out.unlink()
    elif out.exists():
        shutil.rmtree(out)
    clone_tree(snapshot / "out", out)


def run_order(rows: Iterable[Row]) -> list[Row]:
    """The Python-only rows by id, then the crate rows by id."""
    return sorted(rows, key=lambda row: (row.crate, row.id))


def run_row(row: Row, root: Path, env: Mapping[str, str], side: Side) -> RowRun:
    """Apply a row's patch, run its steps, classify them, and revert (step 4 of a run)."""
    patch = str(row.patch_file)
    checked = git(root, "apply", "--check", patch, check=False)
    if checked.returncode != 0:
        return RowRun(STALE_PATCH, note=checked.stderr.strip())
    applied = git(root, "apply", patch, check=False)
    if applied.returncode != 0:
        if porcelain(root):
            raise KitError(f"`git apply` of row {row.id} failed partway and left the root dirty")
        return RowRun(STALE_PATCH, note=applied.stderr.strip())
    records: list[StepRecord] = []
    try:
        for index, step in enumerate(row.steps, 1):
            log = side.log_dir / f"{row.id}-{index}.log"
            timeout = max(MIN_STEP_TIMEOUT_S, STEP_TIMEOUT_FACTOR * side.elapsed[step.argv])
            code, timed_out, elapsed = run_command(step.argv, root=root, env=env, log=log, timeout=timeout)
            output = log.read_text(encoding="utf-8", errors="replace")
            _, required_missing = search(step.require, output, root)
            detail_matched, detail_missing = search(step.detail, output, root)
            changed = None
            if code == 0 and not timed_out:
                current = effect_digests(root, row.effect)
                changed = [path for path in row.effect if current[path] != side.effects.get(path)]
            records.append(
                StepRecord(
                    step.gate,
                    list(step.argv),
                    step.exit,
                    None if timed_out else code,
                    timed_out,
                    round(elapsed, 1),
                    required_missing,
                    detail_matched,
                    detail_missing,
                    changed,
                    str(log),
                )
            )
    finally:
        reverted = git(root, "apply", "-R", patch, check=False)
        left = porcelain(root)
        if reverted.returncode != 0 or left:
            raise KitError(
                f"reverting row {row.id} left the root dirty: {reverted.stderr.strip() or left.splitlines()[0]}"
            )
        restore_out(root, side.snapshot)
    return RowRun(classify(records), records, round(sum(record.elapsed_s for record in records), 1))


def resolve_commit(root: Path, ref: str) -> str | None:
    found = git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return found.stdout.strip() if found.returncode == 0 else None


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _foreign_contents(snapshot: Path) -> bool:
    """Whether `snapshot` exists as something other than an empty directory or a snapshot this module made, which the kit must not delete."""
    if not snapshot.exists() and not snapshot.is_symlink():
        return False
    if snapshot.is_symlink() or not snapshot.is_dir():
        return True
    return any(snapshot.iterdir()) and not (snapshot / SNAPSHOT_MARKER).is_file()


def preflight(root: Path, snapshot: Path, outputs: Sequence[Path], kit_root: Path) -> None:
    """Refuse the conditions step 1 of a run names, apart from `--base`."""
    if root == kit_root.resolve():
        raise Refused(f"--root {root} is the fault kit's own checkout")
    scratch = (kit_root / "var" / "perf").resolve()
    if root == scratch or scratch not in root.parents:
        raise Refused(f"--root {root} is not a scratch clone under {scratch}")
    toplevel = git(root, "rev-parse", "--show-toplevel", check=False) if root.is_dir() else None
    if toplevel is None or toplevel.returncode != 0 or Path(toplevel.stdout.strip()).resolve() != root:
        raise Refused(f"--root {root} is not the top of a git work tree")
    for path in (snapshot, *outputs):
        if _inside(path, root):
            raise Refused(f"{path} lies inside --root {root}")
    for path in (snapshot, base_snapshot(snapshot)):
        if _foreign_contents(path):
            raise Refused(
                f"{path} holds something other than a fault-kit snapshot, which the kit would replace"
            )
    dirty = porcelain(root)
    if dirty:
        raise Refused(f"--root {root} is not clean: {dirty.splitlines()[0]}")


def base_snapshot(snapshot: Path) -> Path:
    return snapshot.with_name(f"{snapshot.name}-base")


def _checkout(root: Path, branch: str | None, commit: str) -> None:
    if branch is not None:
        git(root, "checkout", "-q", branch)
    else:
        git(root, "checkout", "-q", "--detach", commit)


def _leave_as_found(root: Path, branch: str | None, commit: str) -> None:
    """Put the root back at the commit it was found at, clean. It was clean when the run started, so a hard reset and a clean of untracked files remove only what the run made."""
    head = git(root, "rev-parse", "HEAD", check=False).stdout.strip()
    on_branch = git(root, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip() or None
    if porcelain(root):
        git(root, "reset", "-q", "--hard")
        git(root, "clean", "-q", "-fd")
    if head != commit or on_branch != branch:
        _checkout(root, branch, commit)


def _step_json(record: StepRecord) -> dict[str, object]:
    return {
        "gate": record.gate,
        "argv": record.argv,
        "expected_exit": record.expected_exit,
        "exit": record.exit,
        "timed_out": record.timed_out,
        "elapsed_s": record.elapsed_s,
        "required_missing": record.required_missing,
        "detail_matched": record.detail_matched,
        "detail_missing": record.detail_missing,
        "effects_changed": record.effects_changed,
        "log": record.log,
    }


def _control_json(control: Control) -> dict[str, object]:
    return {
        "argv": control.argv,
        "exit": control.exit,
        "elapsed_s": round(control.elapsed_s, 1),
        "effects": control.effects,
        "log": control.log,
    }


def _row_json(row: Row, candidate: RowRun, base: RowRun | None) -> dict[str, object]:
    return {
        "id": row.id,
        "section10": row.section10,
        "slug": row.slug,
        "gates": list(row.gates),
        "patch": row.patch_path,
        "result": candidate.result if base is None else final_result(candidate.result, base.result),
        "on_candidate": candidate.result,
        "on_base": None if base is None else base.result,
        "elapsed_s": candidate.elapsed_s,
        "note": candidate.note,
        "steps": [_step_json(record) for record in candidate.steps],
        "base_steps": None if base is None else [_step_json(record) for record in base.steps],
    }


def run_kit(
    manifest: Manifest,
    *,
    root: Path,
    snapshot: Path,
    rows: str,
    changed: frozenset[str] | None,
    base: str | None,
    out: Path,
    log_dir: Path,
    kit_root: Path = ROOT,
) -> int:
    """A whole run (this module's docstring, "A run"). Writes `out` and returns the exit status; a stop signal propagates as `CycleStopped` after the root is left as it was found."""
    root, snapshot, out, log_dir = (path.resolve() for path in (root, snapshot, out, log_dir))
    tree: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    selection = Selection((), {})
    controls: list[Control] = []
    base_controls: list[Control] = []
    base_refused: str | None = None
    candidate: dict[str, RowRun] = {}
    on_base: dict[str, RowRun] = {}
    refused: str | None = None
    error: str | None = None
    snapshot_taken = False
    try:
        preflight(root, snapshot, (out, log_dir), kit_root)
        tree = git(root, "rev-parse", "HEAD").stdout.strip()
        branch = git(root, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip() or None
        if base is not None:
            base_commit = resolve_commit(root, base)
            if base_commit is None:
                raise Refused(f"--base {base} names no commit in {root}")
        selection = select(manifest, rows, changed, root)
        chosen = ", ".join(row.id for row in selection.rows) or "none"
        print(f"fault kit: rows {chosen} selected; {unreached_text(selection.unreached)}", flush=True)
        if not selection.rows:
            return 0
        env = step_env(kit_root)
        side = run_control(selection.rows, root, env, log_dir, controls, snapshot, tree)
        snapshot_taken = True
        for row in run_order(selection.rows):
            candidate[row.id] = run_row(row, root, env, side)
            print(
                f"fault kit: row {row.id} {candidate[row.id].result} ({candidate[row.id].elapsed_s:.1f} s)",
                flush=True,
            )
        failing = [row for row in selection.rows if candidate[row.id].result != CAUGHT]
        if failing and base_commit is not None:
            git(root, "checkout", "-q", "--detach", base_commit)
            try:
                base_side = run_control(
                    failing, root, env, log_dir / "base", base_controls, base_snapshot(snapshot), base_commit
                )
                for row in run_order(failing):
                    on_base[row.id] = run_row(row, root, env, base_side)
                    print(f"fault kit: row {row.id} on the base {on_base[row.id].result}", flush=True)
            except Refused as refusal:
                base_refused = str(refusal)
                print(f"fault kit: the base's control failed, so each row keeps its first result: {refusal}")
            finally:
                _leave_as_found(root, branch, tree)
                restore_out(root, snapshot)
        results = [
            (
                final_result(candidate[row.id].result, on_base[row.id].result)
                if row.id in on_base
                else candidate[row.id].result
            )
            for row in selection.rows
        ]
        return 0 if all(result == CAUGHT for result in results) else 1
    except Refused as refusal:
        refused = str(refusal)
        print(f"fault kit: REFUSING TO RUN: {refusal}", file=sys.stderr)
        return 2
    except KitError as failure:
        error = str(failure)
        print(f"fault kit: FAILED: {failure}", file=sys.stderr)
        return 1
    except CycleStopped as stopped:
        error = f"stopped by signal {stopped.signum}"
        raise
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        raise
    finally:
        if tree is not None:
            _leave_as_found(root, branch, tree)
            if snapshot_taken:
                restore_out(root, snapshot)
        if not _inside(out, root):
            report = {
                "format": FORMAT,
                "root": str(root),
                "tree": tree,
                "base": base_commit,
                "selected": [row.id for row in selection.rows],
                "unreached": {gate: list(ids) for gate, ids in selection.unreached.items()},
                "control": [_control_json(control) for control in controls],
                "base_control": base_refused or [_control_json(control) for control in base_controls] or None,
                "rows": [
                    _row_json(row, candidate[row.id], on_base.get(row.id))
                    for row in selection.rows
                    if row.id in candidate
                ],
                "refused": refused,
                "error": error,
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2) + "\n")


def read_changed(path: Path) -> frozenset[str]:
    return frozenset(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rebuild.tools.fault_kit",
        description="Inject the fault kit's rows (rebuild/perf/faults.yaml) into a scratch clone and check that their gates catch them.",
    )
    parser.add_argument(
        "--list", action="store_true", help="print the rows and the selection, and run nothing"
    )
    parser.add_argument(
        "--root", type=Path, help="the scratch clone to inject into; with --list, the tree to walk"
    )
    parser.add_argument(
        "--snapshot", type=Path, help="where to keep the control's rebuild/out, outside --root"
    )
    parser.add_argument(
        "--rows", help="auto, all, or a comma-separated list of row ids (default with --list: all)"
    )
    parser.add_argument("--changed", type=Path, help="a file of changed repo-relative paths, one per line")
    parser.add_argument("--base", help="a ref in --root to rerun the rows not caught on")
    parser.add_argument("--out", type=Path, help="the JSON result file")
    parser.add_argument("--log-dir", type=Path, help="where each step's log goes")
    args = parser.parse_args(argv)
    rows = args.rows if args.rows is not None else ("all" if args.list else None)
    if rows is None:
        parser.error("--rows is required")
    if rows == "auto" and args.changed is None:
        parser.error("--rows auto needs --changed")
    if not args.list:
        missing = [
            flag
            for flag, value in (
                ("--root", args.root),
                ("--snapshot", args.snapshot),
                ("--out", args.out),
                ("--log-dir", args.log_dir),
            )
            if value is None
        ]
        if missing:
            parser.error(f"a run needs {', '.join(missing)}")
    try:
        manifest = load_manifest()
        if rows not in ("auto", "all"):
            named_rows(manifest, rows)
        changed = read_changed(args.changed) if args.changed is not None else None
        if args.list:
            root = args.root.resolve() if args.root is not None else ROOT
            print(list_text(manifest, select(manifest, rows, changed, root)))
            return 0
    except (ManifestError, OSError) as error:
        print(f"fault kit: {error}", file=sys.stderr)
        return 2
    with stop_signals():
        try:
            return run_kit(
                manifest,
                root=args.root,
                snapshot=args.snapshot,
                rows=rows,
                changed=changed,
                base=args.base,
                out=args.out,
                log_dir=args.log_dir,
            )
        except CycleStopped as stopped:
            return 128 + stopped.signum


if __name__ == "__main__":
    raise SystemExit(main())
