"""The fault kit: inject each fault of `doc/rebuild-design.md` §10 that some gate catches, as a patch against the tree under test, into the kit's own tree, build on the row's small alphabet, and check that its catching gates still catch it. A change that touches a catching gate's files runs it for the rows those gates decide (`doc/perf-check.md`); nothing in the build imports the kit.

The manifest, `rebuild/perf/faults.yaml` (format `ams-fault-kit/1`), is the authority on the rows. Its `gates` mapping is the closed vocabulary of catching gates: each names the modules whose code decides it (`modules`), the files that call it (`files`), and whether the crate's output feeds it (`crate`). Each row names its §10 row (`section10`), a `slug` saying what the fault does (§10 describes it), its patch (`faults/<id>.patch`, relative to the manifest), its `alphabet`, the runes its builds run on through the alphabet switch (`spec_load.M1_RUNES_ENV`), the smallest that keeps the row's rule shape and what an unfaulted build needs, or another row's that holds it (`doc/perf-check.md` § The fault kit), the effect files a faulted build rewrites (`effect`), the gates that catch the fault but that no step can reach (`unreached`), and its steps. A step is an argv run at the tree's root, the gate it reaches (none for a step that only builds what a later step reads), the exit status a catch gives (`exit`), the substrings a catch must show (`require`), and the substrings it is expected to show (`detail`). Both take `output`, searched in the step's merged stdout and stderr, and `files`, a map from a root-relative path to the substrings searched in that file after the step. The required part is small on purpose, the gate's fixed message prefix and the faulted glyph where the message names one, because stance names, rule texts and positions move with ordinary rune edits; `detail` is reported as matched or missing and never decides a result. A row's gates are its steps' gates followed by its `unreached` ones.

Selection. `--rows all` selects every row, and a comma-separated id list selects those rows. `--rows auto` reads `--changed`, one repo-relative path per line (both sides of a rename), and selects a row when a changed path is a file its patch modifies, its own patch file, the manifest or this module (the last two select every row), or a path one of its gates covers, reached or not: the gate's `files`, the import closure of its `modules` (`contracts_closure.ImportGraph` walked on the root, kept to the `.py` files under `rebuild/` and `tools/`, so a candidate's new imports count), and, for a `crate` gate, the crate's `Cargo.toml`, `Cargo.lock` and `src/`. An empty `--changed` selects nothing. A gate the change touches that no selected step reaches is reported under `unreached` with the selected rows that carry it; under `all`, an id list, or a change to a row's own patch, its patched files, the manifest or this module, every gate of the rows so selected counts as touched.

The tree. A run works in `TREE_PATH` and keeps its controls in `STORE_PATH`, both under this checkout's `var/perf/`. `prepare_tree` makes the tree from this checkout at the start of every run, before anything else reads it: every file git does not ignore, as the work tree holds it, cloned with `cp -c` when it moved since the last run (`SYNC_RECORD` holds what each clone came from) and stamped with the time of the clone, so cargo and the bytecode caches rebuild whatever a clone changed; the site fonts (`SITE_FONTS`), which the baseline tables name; the crate's `target/release/` once, so the first build there starts warm; and a git repository that borrows this checkout's objects (`objects/info/alternates`), its HEAD detached at this checkout's HEAD when the work tree matches it and otherwise at a commit of the work tree on top of it, so the tree under test is the work tree, uncommitted changes included. It removes whatever else git would not ignore, and refuses a directory there that holds anything but no repository, since it would clean it. Before it starts, it deletes the lock files a git command killed partway leaves in the tree's `.git`, and `_leave_as_found` does too (`clear_git_locks`). The seed, `<store>/seed`, clones the baseline tables the oracle compares against (`SEED_INPUTS`) out of this checkout's `rebuild/out`, and every control starts from it, so every control build is cold. A run holds `<tree>.lock` beside the tree (`var/perf/kit.lock`, `store_lock.hold_flock`) from before it refreshes the tree until it has left the tree as found, and refuses with exit 2 when another run holds it, since two runs would clone over, reset and replace each other's tree and snapshots.

The store's slots (the seed and each snapshot) are whole or absent. A slot is built under its `PARTIAL_SUFFIX` name, its `out` filled first and its marker written last, then renamed into place; a slot is deleted by renaming it to its `RETIRED_SUFFIX` name first (`retire_slot`). So a run that a signal or the meter gate stops partway leaves no marker beside a partial `out`, only a name the next run's `prune_store` removes.

A run:

1. It refuses with exit 2 when the write meter's first reading is not in the own basis (the meter gate, below); when the root is this module's own checkout, does not lie under that checkout's `var/perf/`, is not the top of a git work tree, or shows any `git status --porcelain` line; when the store, `--log-dir` or `--out` lies inside the root; when another run holds the tree's lock ("The tree"); when the store holds anything but the snapshots the kit made (each holds `SNAPSHOT_MARKER`) and the partial and retired slots a stopped run left, because the kit replaces them; and when `--base` names no commit of this checkout.
2. The control runs on each alphabet the selected rows run on. Each distinct step argv of the manifest's rows on that alphabet runs once on the unfaulted tree, from the seed, ordered by the earliest step position each holds, so the bare `run_m1` precedes the steps that read its tables. Controls run without a timeout. A control that exits nonzero refuses the run with exit 2, naming the command, since a catch has to be the fault's doing, and so does a control that leaves the tree unclean. It records each control's elapsed time and the raw SHA-256 of every effect file those rows name. An alphabet's control is kept, and a later run reuses it while its key holds (`control_key`: the tree's git tree, the alphabet, the argvs, the effect files, the seed's files and the site fonts), so a control is rebuilt only when the sources move.
3. The snapshot copies `<root>/rebuild/out` to `<store>/tree-<alphabet digest>/out` (`landing.clone_tree`) beside the marker, which holds the control's key and records. Before the controls run, the kit deletes the snapshots of alphabets no row of the manifest names, and the partial and retired slots a stopped run left (`prune_store`).
4. Each selected row runs, the rows whose patch touches only Python first and then the rows whose patch touches the crate, each group by id, so the crate builds once per crate row: its alphabet's snapshot replaces `rebuild/out`, then `git apply --check` (a failure makes the row `stale-patch`), `git apply`, then every step in order, each in its own session with stdout and stderr merged into `<log-dir>/<id>-<k>.log`, `cwd` at the root, under the environment below. A step's timeout is `STEP_TIMEOUT_FACTOR` times its argv's control time or `MIN_STEP_TIMEOUT_S`, whichever is longer; on timeout its session is killed. Then, whatever happened, `git apply -R` reverts the patch, `git status --porcelain` must come back empty, and the snapshot's copy replaces `rebuild/out` again.
5. The row's result comes from its steps (`classify`). `caught`: every step exits as expected with every required substring present, and a step expected to exit 0 changed an effect file. Otherwise the first step that misses decides, since a later step can run on outputs an earlier one never rewrote: `wrong-catch` when it timed out, exited nonzero, or exited 0 with an effect file changed but a required substring missing; `inert` when it exited 0 with every effect file equal to the control's, so the fault did nothing on this tree; `missed` when it should have failed and exited 0 with an effect file changed, so the fault took effect and its gate let it through.
6. With `--base`, the rows not caught run again on the base, each on its own alphabet: the kit checks out that commit, runs the controls of those rows' alphabets into `<store>/base-<alphabet digest>`, kept and reused as the tree's are, runs the rows the same way with logs under `<log-dir>/base/`, then checks out the commit it found and restores the tree's snapshot. A base whose `rebuild/pipeline/spec_load.py` lacks the alphabet switch would build the whole alphabet for every row, so the kit does not run it; then, and when the base's control fails, each row keeps its first result and `base_control` holds the reason. A row inert on both sides is `not-covered` (`NOT_COVERED_NOTE`): its alphabet shows the fault on neither tree, so the run says nothing about its gates. A row the base catches becomes `lost` when the tree under test gave `missed` or `wrong-catch`, since the change lost or altered the catch, and `re-author` when it gave `inert` or `stale-patch`, since the change moved the injection site and the patch is re-authored against it in the same change. Any other row the base does not catch is `drifted`: it no longer fits the head, which is not the change's doing.
7. It writes `--out` and exits 0 when every selected row is caught (or none is selected), else 1, or `WRITE_CAP_STATUS` when the meter gate stopped it.

`--out` holds `format`, `root`, `tree` (the root's HEAD), `base` (the base commit or null), `selected`, `unreached`, `control` and `base_control` (each argv's alphabet, exit, elapsed time, effect digests and log, and whether an earlier run's control was `reused`), and per row its id, `section10`, `slug`, `alphabet`, `gates`, `patch`, `result`, `on_candidate`, `on_base`, `elapsed_s`, `note` (git's message for a `stale-patch`, `NOT_COVERED_NOTE` for a `not-covered` row), and `steps` and `base_steps`, each step with its gate, argv, expected and actual exit, whether it timed out, its elapsed time, the required substrings missing, the detail matched and missing, the effect files changed (null after a nonzero exit, when nothing is compared) and its log. A refused run writes `refused` with the reason, and a run an error, a signal or the meter gate stopped writes `error`. `writes` is the meter gate's record, null for a run it refused: `basis`, `bytes` (the kit's writes over the run), `diskio_bytes`, `device_bytes` and `swap_bytes` from `write_meter.delta` between the first reading and the last, `cap_bytes` and `watermark_bytes`, `poll_s`, `polls`, `unread_polls` (the polls that gave no figure), `peak_rate_bytes_s` (the fastest the coalition wrote between two polls), `seconds`, `warning` (the meter's, in the device basis), `failure` (why a poll failed, null when none did), and `stopped`, null unless the gate stopped the run, else the writes at the poll that reached the watermark, the seconds since the first reading, the writes once every member was stopped (`held_bytes`), and each stopped member's pid, parent and own writes.

The meter gate. A run reads the write meter (`rebuild/tools/write_meter.py`) first, with the device, and refuses unless that reading is in the own basis, where the kit's resource coalition holds only the kit, the processes it started and those it runs under; a coalition shared with a terminal's other processes would put their writes in the figure and their processes in the stop's reach. `make fault-kit` runs the kit as a one-shot launchd job's own process (`rebuild/tools/job_launch.py`), whose coalition holds nothing else, and each run is its own invocation under the gate. A thread then reads the meter without the device every `WRITE_POLL_S` and takes the coalition's logical writes since the first reading. `WRITE_CAP_BYTES` caps them, and the gate stops the run at `WRITE_WATERMARK_BYTES`, the cap less what `WRITE_RATE_MAX_BYTES_S`, the fastest rate the coalition is taken to write at, writes in one poll, so a stop ends under the cap; the records' `peak_rate_bytes_s` revise that rate. At the watermark the gate sends SIGSTOP to every live member of the coalition but the kit and the processes it runs under, scanning again until a scan finds none left running, records the stop, sends each member it stopped SIGKILL, and raises `WriteCapReached` in the main thread, which unwinds the run as a stop signal does and exits with `WRITE_CAP_STATUS`. It signals the coalition's members rather than a step's session, because a process a step starts in a new session leaves the step's process group but stays in the coalition. A poll that raises stops the run through `WriteCapReached` as well, without that stop, since a gate that no longer polls would leave the run uncapped. The gate stops polling before the kit leaves the root as found, so that cleanup runs unwatched and uninterrupted, then reads the meter with the device for the record. Swap is the VM volume's machine-wide figure, so the record reports it beside the cap and never counts it.

Environment: every control and step runs with only `HOME`, `PATH`, `USER`, `LOGNAME`, `LANG` and `TMPDIR` passed through, plus `UV_CACHE_DIR` at this checkout's `.uv-cache`, `AMS_CYCLE_RUN=fault-kit` (`cycle_timings.CYCLE_RUN_ENV`), which keeps run_m1's CLI and `--conform-only` from recording check lines or settling greens and lets the entry points that refuse a small alphabet elsewhere run on one (`spec_load.small_alphabet_refusal`), and the row's alphabet in `spec_load.M1_RUNES_ENV`. The kit never sets the switch in its own process. Git runs without the caller's `GIT_*` variables, so an inherited `GIT_DIR` cannot point it at another repository.

The kit leaves the root clean, at the commit it found, with `rebuild/out` restored to the last tree snapshot a row used, even on failure. SIGINT, SIGTERM and SIGHUP stop a run as they stop a cycle pass (`artifact_cycle.stop_signals`): the running step's session is killed, the row is reverted, and the exit status is 128 plus the signal's number. A revert that leaves the tree dirty stops the run with exit 1, after the kit resets the tracked files to the commit it found and removes the untracked files the run made (the tree had none when it started). What a row leaves behind is outside `rebuild/out` and the tracked files: the crate's release binary under `rebuild/kernel-rs/target/`, which the next use rebuilds by mtime, and whatever `uv run` synced into the root's `.venv`.

Run as: make fault-kit ARGS='--rows auto|all|ID[,ID...] [--changed FILE] [--base REF] --out FILE --log-dir DIR'. `--list [--rows ...] [--changed FILE] [--root DIR]` prints each row's id, gates, §10 row, patch, whether it is selected and its alphabet, then the unreached gates; it needs no tree, walks the closures on `--root` when given and on this checkout otherwise, and selects `all` unless `--rows` says otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import select as kernel_events
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from rebuild.pipeline.spec_load import M1_RUNES_ENV
from rebuild.review import store_lock
from rebuild.review.landing import clone_tree
from rebuild.tools import write_meter
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
PARTIAL_SUFFIX = ".partial"
RETIRED_SUFFIX = ".retired"
TREE_PATH = "var/perf/kit"
STORE_PATH = "var/perf/kit-controls"
SEED = "seed"
SYNC_RECORD = "fault-kit-sync.json"
SITE_FONTS = "site/*.otf"
SEED_INPUTS = ("baseline-*.tsv.gz", "digests.tsv", "baseline-font-content-digests.json")
CRATE_RELEASE = f"{KERNEL_PREFIX}target/release"
SWITCH_FILE = "rebuild/pipeline/spec_load.py"
TREE_IDENTITY = (
    "-c",
    "user.name=fault kit",
    "-c",
    "user.email=fault-kit@localhost",
    "-c",
    "commit.gpgsign=false",
)
MIN_STEP_TIMEOUT_S = 600.0
STEP_TIMEOUT_FACTOR = 3.0
KILL_GRACE_S = 10.0
WRITE_CAP_BYTES = 1_000_000_000
WRITE_POLL_S = 0.02
WRITE_RATE_MAX_BYTES_S = 4_000_000_000
WRITE_WATERMARK_BYTES = WRITE_CAP_BYTES - round(WRITE_RATE_MAX_BYTES_S * WRITE_POLL_S)
WRITE_CAP_STATUS = 3
GATE_SIGNAL = signal.SIGUSR1
_NOTE_USECONDS = 0x2
_NOTE_CRITICAL = 0x20
CAUGHT = "caught"
STALE_PATCH = "stale-patch"
INERT = "inert"
NOT_COVERED = "not-covered"
NOT_COVERED_NOTE = "inert at its alphabet on both sides"


class ManifestError(ValueError):
    """The manifest does not have the shape this module's docstring describes."""


class Refused(Exception):
    """A reason to stop before any row runs, with exit 2."""


class KitError(Exception):
    """A row's revert left the root dirty."""


class GitError(subprocess.CalledProcessError):
    """A git command the kit needed exited nonzero. Its text ends with what git printed on stderr, which `CalledProcessError`'s leaves out."""

    def __str__(self) -> str:
        return f"{super().__str__()} git said: {(self.stderr or '').strip() or 'nothing'}"


class WriteCapReached(KeyboardInterrupt):
    """The meter gate stopped the run at the watermark. It is raised in the main thread wherever the run is waiting, as `CycleStopped` is, and subclasses KeyboardInterrupt for the same reason, so that no `except Exception` catches it."""


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
    alphabet: tuple[str, ...]
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
    alphabet: list[str]
    exit: int | None
    elapsed_s: float
    effects: dict[str, str | None]
    log: str
    reused: bool = False


@dataclass(frozen=True)
class Side:
    """What a row's run on one tree and alphabet compares against: the control's effect digests and elapsed times, the snapshot to restore, where the logs go, and the environment its steps run under."""

    effects: Mapping[str, str | None]
    elapsed: Mapping[tuple[str, ...], float]
    snapshot: Path
    log_dir: Path
    env: Mapping[str, str]


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
        alphabet = _strings(spec.get("alphabet"), f"{where}: alphabet")
        if not alphabet or len(set(alphabet)) != len(alphabet):
            raise ManifestError(f"{where}: alphabet must name at least one rune, each once")
        rows.append(
            Row(
                row_id,
                section10,
                slug,
                patch,
                patch_file,
                patched_files(patch_text),
                tuple(sorted(alphabet)),
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
    table = [("id", "gates", "section10", "patch", "selected", "alphabet")]
    table.extend(
        (
            row.id,
            ",".join(row.gates),
            str(row.section10),
            row.patch_path,
            "yes" if row.id in chosen else "no",
            ",".join(row.alphabet),
        )
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
        return INERT
    if missed.expected_exit != 0:
        return "missed"
    return "wrong-catch"


def final_result(on_candidate: str, on_base: str) -> str:
    """A row's result once the base has run it too (step 6 of a run)."""
    if on_candidate == INERT and on_base == INERT:
        return NOT_COVERED
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
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=git_env())
    if check and done.returncode != 0:
        raise GitError(done.returncode, done.args, done.stdout, done.stderr)
    return done


def clear_git_locks(root: Path) -> None:
    """Delete the lock files in `root`'s `.git`. Only the kit runs git in its tree, one command at a time from the main thread and only under the run lock, so between two of its commands any lock there is one a killed command left, which would fail every later command that takes it."""
    for lock in (root / ".git").glob("*.lock"):
        lock.unlink(missing_ok=True)


def porcelain(root: Path) -> str:
    return git(root, "status", "--porcelain").stdout.strip()


def step_env(kit_root: Path, alphabet: Sequence[str]) -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_PASSED if name in os.environ}
    env["UV_CACHE_DIR"] = str(kit_root / ".uv-cache")
    env[CYCLE_RUN_ENV] = CYCLE_RUN_VALUE
    env[M1_RUNES_ENV] = ",".join(alphabet)
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


def alphabet_slug(alphabet: Sequence[str]) -> str:
    """The store's name for an alphabet: the first 12 hex digits of the SHA-256 of its comma-joined runes."""
    return hashlib.sha256(",".join(alphabet).encode()).hexdigest()[:12]


def _stat_key(path: Path) -> list[int]:
    found = path.lstat()
    return [found.st_size, found.st_mtime_ns]


def _clone_file(source: Path, target: Path) -> None:
    """Copy one file to `target`, which must not exist, keeping its mtime: an APFS clone (`cp -c`) on Darwin, a plain copy elsewhere."""
    if sys.platform == "darwin":
        subprocess.run(["cp", "-c", str(source), str(target)], check=True, capture_output=True)
    else:
        shutil.copy2(source, target)


def build_slot(slot: Path, marker: Mapping[str, object], fill: Callable[[Path], object]) -> None:
    """Replace `slot` with a whole one: build it under its partial name, have `fill` make its `out`, write `marker` last, retire the slot it replaces, and rename it into place."""
    partial = slot.with_name(slot.name + PARTIAL_SUFFIX)
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    fill(partial / "out")
    (partial / SNAPSHOT_MARKER).write_text(json.dumps(marker, indent=1) + "\n")
    retire_slot(slot)
    partial.rename(slot)


def retire_slot(slot: Path) -> None:
    """Delete `slot`, renaming it to its retired name first, so a delete cut off partway leaves a name `prune_store` removes rather than a slot without its marker."""
    retired = slot.with_name(slot.name + RETIRED_SUFFIX)
    if retired.exists():
        shutil.rmtree(retired)
    if slot.exists():
        slot.rename(retired)
        shutil.rmtree(retired)


def _read_marker(slot: Path) -> dict[str, Any] | None:
    try:
        marker = json.loads((slot / SNAPSHOT_MARKER).read_text())
    except OSError, ValueError:
        return None
    return marker if isinstance(marker, dict) else None


def source_files(source: Path) -> list[str]:
    """The source-relative files the tree takes from `source`: every file git does not ignore that the work tree holds, and the site fonts."""
    listed = git(source, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout.split("\0")
    files = {path for path in listed if path and (source / path).is_file()}
    files |= {path.relative_to(source).as_posix() for path in source.glob(SITE_FONTS) if path.is_file()}
    return sorted(files)


def prepare_tree(source: Path, root: Path, store: Path) -> str:
    """Make or refresh the kit's tree at `root` from the checkout at `source`, and the seed in `store` (this module's docstring, "The tree"). Returns the commit the tree's HEAD is detached at."""
    if not (root / ".git").is_dir():
        if root.exists() and (root.is_symlink() or not root.is_dir() or any(root.iterdir())):
            raise Refused(f"{root} holds something other than the kit's tree, which the kit would clean")
        root.mkdir(parents=True, exist_ok=True)
        git(root, "init", "-q")
        common = Path(git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        (root / ".git" / "objects" / "info" / "alternates").write_text(f"{common / 'objects'}\n")
    clear_git_locks(root)
    git(root, "clean", "-q", "-fd")
    record = root / ".git" / SYNC_RECORD
    try:
        kept = json.loads(record.read_text())
    except OSError, ValueError:
        kept = {}
    record.unlink(missing_ok=True)
    files = source_files(source)
    wanted = set(files)
    for path in git(root, "ls-files", "-z").stdout.split("\0"):
        if path and path not in wanted:
            (root / path).unlink(missing_ok=True)
    synced: dict[str, list[list[int]]] = {}
    for path in files:
        origin, target = source / path, root / path
        moved = _stat_key(origin)
        if target.is_file() and kept.get(path) == [moved, _stat_key(target)]:
            synced[path] = kept[path]
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        _clone_file(origin, target)
        os.utime(target)
        synced[path] = [moved, _stat_key(target)]
    record.write_text(json.dumps(synced) + "\n")
    release = root / CRATE_RELEASE
    if not release.exists() and (source / CRATE_RELEASE).is_dir():
        release.parent.mkdir(parents=True, exist_ok=True)
        clone_tree(source / CRATE_RELEASE, release)
    git(root, "add", "-A")
    work = git(root, "write-tree").stdout.strip()
    head = git(source, "rev-parse", "HEAD").stdout.strip()
    commit = head
    if work != git(source, "rev-parse", "HEAD^{tree}").stdout.strip():
        message = "The checkout's work tree on top of its HEAD"
        commit = git(root, *TREE_IDENTITY, "commit-tree", work, "-p", head, "-m", message).stdout.strip()
    git(root, "update-ref", "--no-deref", "HEAD", commit)

    def seed_inputs(out: Path) -> None:
        out.mkdir()
        for pattern in SEED_INPUTS:
            for path in sorted((source / "rebuild" / "out").glob(pattern)):
                if path.is_file():
                    _clone_file(path, out / path.name)

    build_slot(store / SEED, {"format": FORMAT, "source": str(source)}, seed_inputs)
    return commit


def control_key(
    root: Path, alphabet: Sequence[str], argvs: Sequence[Sequence[str]], effects: Sequence[str], seed: Path
) -> str:
    """The key an alphabet's kept control is reused under: the root's git tree, the alphabet, the control argvs, the effect files, and the path, size and mtime of each seed file and of each site font in the root, which the baseline tables' provenance check reads."""
    out = seed / "out"
    listing = sorted(
        [path.relative_to(out).as_posix(), *_stat_key(path)] for path in out.rglob("*") if path.is_file()
    )
    fonts = sorted([path.relative_to(root).as_posix(), *_stat_key(path)] for path in root.glob(SITE_FONTS))
    payload = {
        "format": FORMAT,
        "tree": git(root, "rev-parse", "HEAD^{tree}").stdout.strip(),
        "alphabet": list(alphabet),
        "argvs": [list(argv) for argv in argvs],
        "effects": list(effects),
        "seed": listing,
        "fonts": fonts,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _ensure_seed(root: Path, store: Path) -> Path:
    """The seed, taken from the root's `rebuild/out` as found when nothing made one (`prepare_tree` makes it from the checkout on every run)."""
    seed = store / SEED
    if not (seed / "out").is_dir():
        found = root / "rebuild" / "out"
        build_slot(
            seed,
            {"format": FORMAT, "root": str(root)},
            lambda out: clone_tree(found, out) if found.is_dir() else out.mkdir(),
        )
    return seed


def run_controls(
    manifest_rows: Sequence[Row],
    rows: Sequence[Row],
    *,
    root: Path,
    store: Path,
    side: str,
    log_dir: Path,
    controls: list[Control],
    tree: str,
    kit_root: Path,
) -> dict[tuple[str, ...], Side]:
    """Give each alphabet `rows` run on its control on this tree (steps 2 and 3 of a run): reuse the kept one in `<store>/<side>-<alphabet digest>` while its key holds, and otherwise run the distinct step argvs of the manifest's rows on that alphabet from the seed, refusing when one exits nonzero or the tree is no longer clean, and snapshot `rebuild/out` with the key and the records. Appends each control to `controls` as it is run or reused. Returns what each alphabet's rows compare against."""
    seed = _ensure_seed(root, store)
    sides: dict[tuple[str, ...], Side] = {}
    for alphabet in dict.fromkeys(row.alphabet for row in rows):
        group = [row for row in manifest_rows if row.alphabet == alphabet]
        argvs = control_argvs(group)
        effects = list(dict.fromkeys(path for row in group for path in row.effect))
        env = step_env(kit_root, alphabet)
        slug = alphabet_slug(alphabet)
        slot = store / f"{side}-{slug}"
        key = control_key(root, alphabet, argvs, effects, seed)
        kept = _read_marker(slot)
        if kept is not None and kept.get("key") == key and (slot / "out").is_dir():
            ran = [_control_from_json(record) for record in kept["controls"]]
            controls.extend(ran)
        else:
            retire_slot(slot)
            restore_out(root, seed)
            ran = []
            for index, argv in enumerate(argvs, 1):
                log = log_dir / f"control-{slug}-{index}.log"
                code, _, elapsed = run_command(argv, root=root, env=env, log=log, timeout=None)
                ran.append(
                    Control(
                        list(argv), list(alphabet), code, elapsed, effect_digests(root, effects), str(log)
                    )
                )
                controls.append(ran[-1])
                if code != 0:
                    raise Refused(
                        f"the control `{' '.join(argv)}` on {','.join(alphabet)} exited {code} on the unfaulted tree, so a catch would not be the fault's doing; see {log}"
                    )
            if porcelain(root):
                raise Refused("the control changed the root's tracked or untracked files")
            take_snapshot(
                root,
                slot,
                {
                    "format": FORMAT,
                    "root": str(root),
                    "tree": tree,
                    "key": key,
                    "alphabet": list(alphabet),
                    "controls": [_control_json(control) for control in ran],
                },
            )
        sides[alphabet] = Side(
            ran[-1].effects, {tuple(control.argv): control.elapsed_s for control in ran}, slot, log_dir, env
        )
    return sides


def prune_store(store: Path, manifest: Manifest) -> None:
    """Delete the store's snapshots of alphabets no row of the manifest names, and the partial and retired slots a stopped run left, so the store keeps one control per alphabet in use and side."""
    kept = {SEED} | {
        f"{side}-{alphabet_slug(row.alphabet)}" for row in manifest.rows for side in ("tree", "base")
    }
    if store.is_dir():
        for slot in sorted(store.iterdir()):
            if slot.name not in kept:
                retire_slot(slot)


def take_snapshot(root: Path, slot: Path, marker: Mapping[str, object]) -> None:
    build_slot(slot, marker, lambda out: clone_tree(root / "rebuild" / "out", out))


def restore_out(root: Path, snapshot: Path) -> None:
    out = root / "rebuild" / "out"
    if out.is_symlink() or out.is_file():
        out.unlink()
    elif out.exists():
        shutil.rmtree(out)
    clone_tree(snapshot / "out", out)


def lacks_switch(root: Path, commit: str) -> bool:
    """Whether `commit`'s `SWITCH_FILE` lacks the alphabet switch, so that its builds run on the whole alphabet whatever a row names."""
    return git(root, "grep", "-q", "-F", M1_RUNES_ENV, commit, "--", SWITCH_FILE, check=False).returncode != 0


def run_order(rows: Iterable[Row]) -> list[Row]:
    """The Python-only rows by id, then the crate rows by id."""
    return sorted(rows, key=lambda row: (row.crate, row.id))


def run_row(row: Row, root: Path, side: Side) -> RowRun:
    """Restore the row's alphabet's snapshot, apply its patch, run its steps, classify them, revert, and restore the snapshot again (step 4 of a run)."""
    restore_out(root, side.snapshot)
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
            code, timed_out, elapsed = run_command(
                step.argv, root=root, env=side.env, log=log, timeout=timeout
            )
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


def _foreign_contents(store: Path) -> bool:
    """Whether `store` exists as something other than a directory holding only snapshots this module made and the partial and retired slots of a stopped run, which the kit must not delete."""
    if not store.exists() and not store.is_symlink():
        return False
    if store.is_symlink() or not store.is_dir():
        return True
    return any(
        entry.is_symlink()
        or not entry.is_dir()
        or not (entry.name.endswith((PARTIAL_SUFFIX, RETIRED_SUFFIX)) or (entry / SNAPSHOT_MARKER).is_file())
        for entry in store.iterdir()
    )


def preflight(root: Path, store: Path, outputs: Sequence[Path], kit_root: Path) -> None:
    """Refuse the conditions step 1 of a run names, apart from `--base`."""
    if root == kit_root.resolve():
        raise Refused(f"--root {root} is the fault kit's own checkout")
    scratch = (kit_root / "var" / "perf").resolve()
    if root == scratch or scratch not in root.parents:
        raise Refused(f"--root {root} is not a scratch tree under {scratch}")
    toplevel = git(root, "rev-parse", "--show-toplevel", check=False) if root.is_dir() else None
    if toplevel is None or toplevel.returncode != 0 or Path(toplevel.stdout.strip()).resolve() != root:
        raise Refused(f"--root {root} is not the top of a git work tree")
    for path in (store, *outputs):
        if _inside(path, root):
            raise Refused(f"{path} lies inside --root {root}")
    if _foreign_contents(store):
        raise Refused(f"{store} holds something other than fault-kit snapshots, which the kit would replace")
    dirty = porcelain(root)
    if dirty:
        raise Refused(f"--root {root} is not clean: {dirty.splitlines()[0]}")


def _checkout(root: Path, branch: str | None, commit: str) -> None:
    if branch is not None:
        git(root, "checkout", "-q", branch)
    else:
        git(root, "checkout", "-q", "--detach", commit)


def _leave_as_found(root: Path, branch: str | None, commit: str) -> None:
    """Put the root back at the commit it was found at, clean. It was clean when the run started, so a hard reset and a clean of untracked files remove only what the run made. It first deletes the git locks a command killed by a stop signal or the meter gate left (`clear_git_locks`)."""
    clear_git_locks(root)
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
        "alphabet": control.alphabet,
        "exit": control.exit,
        "elapsed_s": round(control.elapsed_s, 1),
        "effects": control.effects,
        "log": control.log,
        "reused": control.reused,
    }


def _control_from_json(record: Mapping[str, Any]) -> Control:
    return Control(
        list(record["argv"]),
        list(record["alphabet"]),
        record["exit"],
        float(record["elapsed_s"]),
        dict(record["effects"]),
        str(record["log"]),
        reused=True,
    )


def row_result(candidate: RowRun, base: RowRun | None) -> str:
    return candidate.result if base is None else final_result(candidate.result, base.result)


def _row_json(row: Row, candidate: RowRun, base: RowRun | None) -> dict[str, object]:
    result = row_result(candidate, base)
    return {
        "id": row.id,
        "section10": row.section10,
        "slug": row.slug,
        "alphabet": list(row.alphabet),
        "gates": list(row.gates),
        "patch": row.patch_path,
        "result": result,
        "on_candidate": candidate.result,
        "on_base": None if base is None else base.result,
        "elapsed_s": candidate.elapsed_s,
        "note": NOT_COVERED_NOTE if result == NOT_COVERED else candidate.note,
        "steps": [_step_json(record) for record in candidate.steps],
        "base_steps": None if base is None else [_step_json(record) for record in base.steps],
    }


@dataclass(frozen=True)
class WriteStop:
    """What the meter gate records at the watermark: the coalition's writes at the poll that reached it, the seconds since the first reading, the coalition's writes once every member it stops is stopped, and those members, each as the reading taken then shows it."""

    bytes: int
    seconds: float
    held_bytes: int | None
    members: tuple[write_meter.Member, ...]

    def text(self) -> str:
        pids = ", ".join(str(member.pid) for member in self.members) or "none"
        return f"the coalition wrote {self.bytes} B of its {WRITE_CAP_BYTES} B cap, past the {WRITE_WATERMARK_BYTES} B watermark, {self.seconds:.1f} s into the run, so the gate stopped and killed its other members (pids {pids})"


def lineage(reading: write_meter.WriteReading) -> frozenset[int]:
    """The reader's pid and its chain of parents among the reading's members: the processes the meter gate never stops."""
    parents = {member.pid: member.ppid for member in reading.members}
    spared = {reading.pid}
    pid = parents.get(reading.pid)
    while pid in parents and pid not in spared:
        spared.add(pid)
        pid = parents[pid]
    return frozenset(spared)


def _send(pid: int, signum: int) -> None:
    if pid > 1:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signum)


class WriteGate:
    """The meter gate (this module's docstring, "The meter gate"). Constructed on the main thread, it takes the first reading through `meter` (`write_meter.read`, called with whether to read the device), raises `Refused` unless that reading is in the own basis, installs the `GATE_SIGNAL` handler that raises `WriteCapReached`, and starts the poll thread. `halt` stops the thread and puts the handler back, after which nothing the gate does reaches the run, and `finish` takes the last reading and returns the record."""

    def __init__(self, meter: Callable[[bool], write_meter.WriteReading]) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("the meter gate raises its stop in the main thread, so it starts there")
        self._meter = meter
        self.first = meter(True)
        opening = write_meter.delta(self.first, self.first)
        if opening.basis != "own":
            why = (
                opening.warning
                if opening.basis == "device"
                else "processes other than the kit, those it started and those it runs under share its resource coalition"
            )
            raise Refused(
                f"the write meter's first reading is in the {opening.basis} basis, not the own one: {why}; `make fault-kit` runs the kit as a launchd job in a coalition of its own"
            )
        self.polls = 0
        self.unread_polls = 0
        self.peak_rate = 0.0
        self.stop: WriteStop | None = None
        self.failure: str | None = None
        self._spared = lineage(self.first)
        self._main = threading.get_ident()
        self._lock = threading.Lock()
        self._halted = False
        self._finished = threading.Event()
        previous = signal.signal(GATE_SIGNAL, self._interrupt)
        self._previous = signal.SIG_DFL if previous is None else previous
        self._armed = True
        self._thread = threading.Thread(target=self._watch, name="fault-kit-meter-gate", daemon=True)
        self._thread.start()

    def _interrupt(self, _signum: int, _frame: object) -> None:
        if self._armed and (self.stop is not None or self.failure is not None):
            self._armed = False
            raise WriteCapReached(
                self.stop.text() if self.stop is not None else f"the meter gate's poll failed: {self.failure}"
            )

    def _written(self, reading: write_meter.WriteReading) -> int | None:
        if reading.coalition != self.first.coalition or reading.logical is None or self.first.logical is None:
            return None
        return reading.logical - self.first.logical

    def _watch(self) -> None:
        """Poll until `halt` or the stop. A poll that raises stops the run as well, since a gate that stopped polling would leave the run uncapped; the record's `failure` says why."""
        try:
            self._poll()
        except Exception as failure:
            self.failure = f"{type(failure).__name__}: {failure}"
            print(f"fault kit: THE METER GATE'S POLL FAILED: {self.failure}", file=sys.stderr, flush=True)
            if self._armed:
                signal.pthread_kill(self._main, GATE_SIGNAL)

    def _poll(self) -> None:
        last_at, last_written = self.first.at, 0
        clock = kernel_events.kqueue()
        try:
            # Darwin wakes a sleep or a timed wait up to half its length late, so a 0.02 s wait polls every 0.03 s; the kernel never coalesces a NOTE_CRITICAL timer.
            tick = kernel_events.kevent(
                1,
                filter=kernel_events.KQ_FILTER_TIMER,
                flags=kernel_events.KQ_EV_ADD | kernel_events.KQ_EV_ENABLE,
                fflags=_NOTE_USECONDS | _NOTE_CRITICAL,
                data=round(WRITE_POLL_S * 1_000_000),
            )
            clock.control([tick], 0)
            while True:
                clock.control(None, 1)
                if self._finished.is_set():
                    return
                reading = self._meter(False)
                self.polls += 1
                written = self._written(reading)
                if written is None:
                    self.unread_polls += 1
                    continue
                if reading.at > last_at:
                    self.peak_rate = max(self.peak_rate, (written - last_written) / (reading.at - last_at))
                last_at, last_written = reading.at, written
                if written >= WRITE_WATERMARK_BYTES:
                    self._stop(written, reading)
                    return
        finally:
            clock.close()

    def _stop(self, written: int, reading: write_meter.WriteReading) -> None:
        """SIGSTOP every live member but the spared ones, scanning again until a scan finds none left running, record them, SIGKILL them, and raise the stop in the main thread unless `halt` has begun."""
        with self._lock:
            if self._halted:
                return
            stopped: dict[int, write_meter.Member] = {}
            try:
                held = reading
                while fresh := [
                    member
                    for member in held.members
                    if member.pid not in self._spared and member.pid not in stopped
                ]:
                    for member in fresh:
                        _send(member.pid, signal.SIGSTOP)
                        stopped[member.pid] = member
                    held = self._meter(False)
                now = {member.pid: member for member in held.members}
                self.stop = WriteStop(
                    written,
                    round(reading.at - self.first.at, 3),
                    self._written(held),
                    tuple(now.get(pid, member) for pid, member in stopped.items()),
                )
                print(
                    f"fault kit: STOPPED AT THE WRITE WATERMARK: {self.stop.text()}",
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                for pid in stopped:
                    _send(pid, signal.SIGKILL)
            if self._armed:
                signal.pthread_kill(self._main, GATE_SIGNAL)

    def halt(self) -> None:
        self._armed = False
        with self._lock:
            self._halted = True
        self._finished.set()
        self._thread.join()
        signal.signal(GATE_SIGNAL, self._previous)

    def finish(self) -> dict[str, object]:
        """Take the last reading, with the device, print the run's writes beside the cap and swap beside them, and return the `writes` record."""
        change = write_meter.delta(self.first, self._meter(True))
        written = "an unread amount" if change.bytes is None else f"{change.bytes / 1e9:.3f} GB"
        swap = "unread" if change.swap_bytes is None else f"{change.swap_bytes / 1e9:.3f} GB"
        print(
            f"fault kit: the run wrote {written} of its {WRITE_CAP_BYTES / 1e9:g} GB cap ({change.basis} basis); swap, machine-wide and outside the cap: {swap}",
            flush=True,
        )
        if change.warning is not None:
            print(f"[warn] {change.warning}", flush=True)
        stop = self.stop
        return {
            "basis": change.basis,
            "bytes": change.bytes,
            "cap_bytes": WRITE_CAP_BYTES,
            "watermark_bytes": WRITE_WATERMARK_BYTES,
            "swap_bytes": change.swap_bytes,
            "device_bytes": change.device_bytes,
            "diskio_bytes": change.diskio_bytes,
            "poll_s": WRITE_POLL_S,
            "polls": self.polls,
            "unread_polls": self.unread_polls,
            "peak_rate_bytes_s": round(self.peak_rate),
            "seconds": round(change.seconds, 1),
            "stopped": (
                None
                if stop is None
                else {
                    "bytes": stop.bytes,
                    "seconds": stop.seconds,
                    "held_bytes": stop.held_bytes,
                    "members": [
                        {"pid": member.pid, "ppid": member.ppid, "logical": member.logical}
                        for member in stop.members
                    ],
                }
            ),
            "warning": change.warning,
            "failure": self.failure,
        }


def hold_run_lock(root: Path, held: contextlib.ExitStack) -> None:
    """Take the run lock, `<root>.lock` beside the tree, into `held` without waiting, and refuse when another run holds it (this module's docstring, "The tree")."""
    path = store_lock.lock_path_for(root)
    try:
        held.enter_context(store_lock.hold_flock(path, blocking=False))
    except store_lock.LockBusy:
        raise Refused(f"another fault kit run holds {path} and is using {root}") from None


def run_kit(
    manifest: Manifest,
    *,
    root: Path,
    store: Path,
    rows: str,
    changed: frozenset[str] | None,
    base: str | None,
    out: Path,
    log_dir: Path,
    kit_root: Path = ROOT,
    source: Path | None = None,
    meter: Callable[[bool], write_meter.WriteReading] | None = None,
) -> int:
    """A whole run (this module's docstring, "A run"), in the tree `prepare_tree` makes at `root` from `source` when `source` is given and in `root` as found otherwise, under the meter gate when `meter` is given (`write_meter.read`, called with whether to read the device). `--base` resolves in `source`, or in `root` without one. Writes `out` and returns the exit status; a stop signal propagates as `CycleStopped` after the root is left as it was found."""
    root, store, out, log_dir = (path.resolve() for path in (root, store, out, log_dir))
    gate: WriteGate | None = None
    writes: dict[str, object] | None = None
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
    home: Path | None = None
    held = contextlib.ExitStack()
    try:
        if meter is not None:
            gate = WriteGate(meter)
        hold_run_lock(root, held)
        if source is not None:
            prepare_tree(source.resolve(), root, store)
        preflight(root, store, (out, log_dir), kit_root)
        prune_store(store, manifest)
        tree = git(root, "rev-parse", "HEAD").stdout.strip()
        branch = git(root, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip() or None
        if base is not None:
            where = root if source is None else source.resolve()
            base_commit = resolve_commit(where, base)
            if base_commit is None:
                raise Refused(f"--base {base} names no commit in {where}")
        selection = select(manifest, rows, changed, root)
        chosen = ", ".join(row.id for row in selection.rows) or "none"
        print(f"fault kit: rows {chosen} selected; {unreached_text(selection.unreached)}", flush=True)
        if not selection.rows:
            return 0
        sides = run_controls(
            manifest.rows,
            run_order(selection.rows),
            root=root,
            store=store,
            side="tree",
            log_dir=log_dir,
            controls=controls,
            tree=tree,
            kit_root=kit_root,
        )
        for row in run_order(selection.rows):
            home = sides[row.alphabet].snapshot
            candidate[row.id] = run_row(row, root, sides[row.alphabet])
            print(
                f"fault kit: row {row.id} {candidate[row.id].result} ({candidate[row.id].elapsed_s:.1f} s)",
                flush=True,
            )
        failing = [row for row in selection.rows if candidate[row.id].result != CAUGHT]
        if failing and base_commit is not None:
            if lacks_switch(root, base_commit):
                base_refused = f"the base {base_commit} has no alphabet switch ({M1_RUNES_ENV} in {SWITCH_FILE}), so its builds would run on the whole alphabet and the kit does not run them"
                print(f"fault kit: each row keeps its first result: {base_refused}", flush=True)
            else:
                git(root, "checkout", "-q", "--detach", base_commit)
                try:
                    base_sides = run_controls(
                        manifest.rows,
                        run_order(failing),
                        root=root,
                        store=store,
                        side="base",
                        log_dir=log_dir / "base",
                        controls=base_controls,
                        tree=base_commit,
                        kit_root=kit_root,
                    )
                    for row in run_order(failing):
                        on_base[row.id] = run_row(row, root, base_sides[row.alphabet])
                        print(f"fault kit: row {row.id} on the base {on_base[row.id].result}", flush=True)
                except Refused as refusal:
                    base_refused = str(refusal)
                    print(
                        f"fault kit: the base's control failed, so each row keeps its first result: {refusal}"
                    )
                finally:
                    _leave_as_found(root, branch, tree)
                    if home is not None:
                        restore_out(root, home)
        results = {row.id: row_result(candidate[row.id], on_base.get(row.id)) for row in selection.rows}
        for row_id, result in results.items():
            if result == NOT_COVERED:
                print(f"fault kit: row {row_id} not covered: {NOT_COVERED_NOTE}", flush=True)
        return 0 if all(result == CAUGHT for result in results.values()) else 1
    except Refused as refusal:
        refused = str(refusal)
        print(f"fault kit: REFUSING TO RUN: {refusal}", file=sys.stderr)
        return 2
    except KitError as failure:
        error = str(failure)
        print(f"fault kit: FAILED: {failure}", file=sys.stderr)
        return 1
    except WriteCapReached as stopped:
        error = str(stopped)
        return WRITE_CAP_STATUS
    except CycleStopped as stopped:
        error = f"stopped by signal {stopped.signum}"
        raise
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        raise
    finally:
        if gate is not None:
            gate.halt()
        with held:
            if tree is not None:
                _leave_as_found(root, branch, tree)
                if home is not None:
                    restore_out(root, home)
        if gate is not None:
            writes = gate.finish()
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
                "writes": writes,
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2) + "\n")


def read_changed(path: Path) -> frozenset[str]:
    return frozenset(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rebuild.tools.fault_kit",
        description=f"Inject the fault kit's rows (rebuild/perf/faults.yaml) into the kit's tree ({TREE_PATH}), each on its own small alphabet, and check that their gates catch them.",
    )
    parser.add_argument(
        "--list", action="store_true", help="print the rows and the selection, and run nothing"
    )
    parser.add_argument("--root", type=Path, help="with --list, the tree whose import closures to walk")
    parser.add_argument(
        "--rows", help="auto, all, or a comma-separated list of row ids (default with --list: all)"
    )
    parser.add_argument("--changed", type=Path, help="a file of changed repo-relative paths, one per line")
    parser.add_argument("--base", help="a commit of this checkout to rerun the rows not caught on")
    parser.add_argument("--out", type=Path, help="the JSON result file")
    parser.add_argument("--log-dir", type=Path, help="where each step's log goes")
    args = parser.parse_args(argv)
    rows = args.rows if args.rows is not None else ("all" if args.list else None)
    if rows is None:
        parser.error("--rows is required")
    if rows == "auto" and args.changed is None:
        parser.error("--rows auto needs --changed")
    if not args.list:
        if args.root is not None:
            parser.error(f"--root goes with --list: a run makes its own tree, {TREE_PATH}")
        missing = [
            flag for flag, value in (("--out", args.out), ("--log-dir", args.log_dir)) if value is None
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
                root=ROOT / TREE_PATH,
                store=ROOT / STORE_PATH,
                rows=rows,
                changed=changed,
                base=args.base,
                out=args.out,
                log_dir=args.log_dir,
                source=ROOT,
                meter=write_meter.read,
            )
        except WriteCapReached:
            return WRITE_CAP_STATUS
        except CycleStopped as stopped:
            return 128 + stopped.signum


if __name__ == "__main__":
    raise SystemExit(main())
