"""The kernel boundary's Python face (issue #40, sub-issue #47; the only fixpoint there is since issue #78): build the binary, fan one process out over a whole cycle's transition streams, read the section 5.7 guard surface, replay batched author-facing explain cases, and hand back the single-configuration product and tables for everything that is not `run_m1`. It lives here rather than beside the `rebuild/tools/kernel_*.py` harnesses because the pipeline is what calls it and the pipeline does not import from the tools tree; `rebuild/tools/kernel_differential.py` and `kernel_parity.py` keep their own copies of the same constants, which is duplication with a reason — each of them is an exit-bar instrument that has to state the contract it is measuring rather than inherit it from the thing it measures.

The build is `cargo build --release` against the crate's own manifest and nothing else, because release is the only profile anything in this repo runs: the pipeline, the parity harness and the differential all reach for `target/release/ams-m1-kernel`, and a debug binary that answered would answer far too slowly to be the same experiment. A box with no `cargo` is a `KernelBuildError` carrying the remedy rather than a stack trace, since that is the one failure a reader can fix in a minute; `ensure_built` is the memoized form every caller in a process shares, so a suite that builds a hundred tables pays for one build.

`enumerate_configs` is the fan-out verb and the one `run_m1` needs: one process answers every acceptance configuration, writing each one's stream to a file of its own, and the streams are byte-identical to what the same binary emits one configuration at a time at any thread width (sub-issue #46's exit bar). Threads are the caller's to choose because the ceiling is memory rather than CPU — a live configuration holds its whole working set until it has emitted — so sub-issue #46 measured 3 as the solo width on a 32 GB box and `KERNEL_THREADS_DEFAULT` ships one below it, because a cycle runs the fan-out beside a pytest pool and the Python fold rather than alone; `AMS_KERNEL_THREADS` overrides it in either direction, and since the streams are byte-identical at any width that override is purely a memory knob. Callers cap whatever width they are handed at the number of configurations there are to answer and at the CPUs there are to answer them with.

`enumerate_transitions` and `build_tables` are the single-configuration forms of the same call, in memory and writing nothing: one spec dumped to a scratch directory, one stream enumerated and read back as the `table.FixpointProduct` the fold consumes. That product is where the two halves of the build meet, so this module is where the seam is invoked from — the crate enumerates, `table.assemble_tables` folds — and a test, a tool or a hand-assembled spec reaches the build through here, while `run_m1.build_tables` is the persisting, whole-cycle form that stamps its artifacts with the sources they came from.

`guard_sweep` is one other in-memory form: one spec dump, one crate invocation, and one complete mapping from `(ligature, first raw slot, second raw slot)` to the config-blind formation verdict. `settle_cases` is the author-facing sibling: a file of independent `ams-m1-corpus/3` windows in, the full Rust trace objects out, with count and question echo checked before the caller decodes a report. The CLI spells boundary tokens as `edge`, `space`, `zwnj`, `namer-dot`, and `unknown`; the guard mapping converts them to Python's `RightToken` constants at the boundary so consumers never confuse those model tokens with glyph names such as `uni200C` or `periodcentered`.

The invocation is read strictly, on the CLI contract's own terms: exit 2 is the usage check, which for a well-formed invocation can only mean the verb is absent or the two sides' flag sets have drifted apart; any other nonzero exit is the kernel complaining about its inputs; and stderr on a clean exit is a failure unless timings were asked for, in which case every `[t]` line is forwarded to this process's own stderr verbatim so the cycle journal reads the kernel's per-configuration walls the same way it reads Python's, and anything else on that stream is still a failure. Enumeration answers in files, so bytes on stdout there are a failure; `guard-sweep` answers on stdout and its complete TSV surface is parsed strictly.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from rebuild.pipeline import kernel_io, settle, table
from rebuild.pipeline.model import ResolvedSpec, feature_config_token
from rebuild.pipeline.table import DecisionTable, FixpointProduct, TreatyTable

REPO_ROOT = Path(__file__).resolve().parents[2]
BINARY = REPO_ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
MANIFEST = REPO_ROOT / "rebuild" / "kernel-rs" / "Cargo.toml"
KERNEL_THREADS_DEFAULT = max(1, int(os.environ.get("AMS_KERNEL_THREADS", "2")))
TIMEOUT = 1800
# Every `cargo build` re-uplifts the binary into target/release — removes it, then hard-links the fresh one in — even when nothing recompiled, so a build in one process can make another process's exec miss the file for an instant. One lock in the target directory orders the two: a build holds it exclusively for the uplift, an invocation holds it shared for exactly the spawn, and never for the run.
LOCK_PATH = MANIFEST.parent / "target" / ".ams-kernel-uplift.lock"
# How much of a failed build's stderr rides the exception: cargo says what is wrong in its last few lines and repeats the whole compilation above them.
BUILD_TAIL_LINES = 20
# The issue-26 flag: deep window slots enumerate at class grain (one row per outcome fiber, expanded back to labels for every fold-side consumer). It is a kernel invocation flag, carried across by `world_flags` like settle's two semantics defaults — module-level, consulted at call time, AMS_DEEP_CLASSES=0 the label-grain comparison state — and `class_grain` states the grain rule the crate itself applies.
DEEP_CLASSES_DEFAULT = os.environ.get("AMS_DEEP_CLASSES", "1") != "0"
# The three semantics flags a fixpoint's shape depends on, each as (the kernel flag that says it is off, the module holding the default, the attribute). Off is what carries a flag, so the shipping world invokes the verb bare.
SETTLEMENT_FLAGS = (
    ("--candidacy-prospect", settle, "SIMULATED_PROSPECT_DEFAULT"),
    ("--vote-slots-off", settle, "VOTE_SLOTS_DEFAULT"),
)
WORLD_FLAGS = (
    *SETTLEMENT_FLAGS,
    ("--deep-classes-off", sys.modules[__name__], "DEEP_CLASSES_DEFAULT"),
)
GUARD_TAIL_TOKENS = {
    token.kind: token for token in (settle.EDGE, settle.SPACE, settle.ZWNJ, settle.NAMER_DOT, settle.UNKNOWN)
}
FormationGuard = dict[tuple[str, settle.RightToken, settle.RightToken], bool]

_BUILT = False


class KernelBuildError(RuntimeError):
    """`cargo` is absent or the crate did not build. Distinct from a run failure, which is a binary that exists and answered badly."""


class KernelRunError(RuntimeError):
    """The binary refused the invocation, exited nonzero, complained on a clean exit, or left a stream unwritten."""


def cargo_build() -> None:
    """Build the kernel in release mode, the way `make kernel-build` does. Callers run this before every fan-out rather than checking whether the binary exists: a stale binary and a fresh one are the same file, and the whole point of a differential engine is that the sources on disk are what answered. A warm build costs a fraction of a second; a cold one costs what a cold one costs."""
    arguments = ["cargo", "build", "--release", "--manifest-path", str(MANIFEST)]
    try:
        with _uplift_lock(fcntl.LOCK_EX):
            finished = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise KernelBuildError(
            "no cargo on PATH — install the Rust toolchain (https://rustup.rs) to build the M1 kernel"
        ) from None
    except subprocess.TimeoutExpired:
        raise KernelBuildError(
            f"cargo gave no answer within {TIMEOUT} seconds on {' '.join(arguments)}"
        ) from None
    if finished.returncode != 0:
        errors = finished.stderr.decode(errors="replace").strip().split("\n")
        tail = "\n".join(errors[-BUILD_TAIL_LINES:])
        raise KernelBuildError(f"the kernel did not build (cargo exited {finished.returncode}):\n{tail}")


class _UpliftLock:
    def __init__(self, mode: int) -> None:
        self._mode = mode
        self._handle = None

    def __enter__(self):
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._handle = LOCK_PATH.open("w")
        fcntl.flock(self._handle, self._mode)
        return self

    def __exit__(self, *_exc) -> None:
        assert self._handle is not None
        fcntl.flock(self._handle, fcntl.LOCK_UN)
        self._handle.close()


def _uplift_lock(mode: int) -> _UpliftLock:
    return _UpliftLock(mode)


def _run_kernel(arguments: list[str], verb: str) -> subprocess.CompletedProcess:
    """Invoke the binary with the uplift lock held shared across the spawn alone — the one instant a concurrent `cargo build` could make the path vanish — then wait unlocked, so a minutes-long enumeration never stalls a build elsewhere. Raises the same `KernelRunError`s every verb used to raise for a missing binary or a silent kernel."""
    try:
        with _uplift_lock(fcntl.LOCK_SH):
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise KernelRunError(
            f"no kernel binary at {BINARY} — run `make kernel-build` first, or let the caller's cargo_build() build it"
        ) from None
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise KernelRunError(
            f"the kernel gave no answer within {TIMEOUT} seconds on {verb} ({' '.join(arguments)})"
        ) from None
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def ensure_built() -> None:
    """`cargo_build` once per process, and nothing at all on every call after. The build itself is what a caller wants before its first invocation — the sources on disk are what must answer — but a warm `cargo` still costs a fraction of a second, and a suite or a cycle stage that builds a hundred tables would pay it a hundred times over for a binary that cannot have moved underneath it. A caller that genuinely wants the toolchain consulted again calls `cargo_build` directly."""
    global _BUILT
    if _BUILT:
        return
    cargo_build()
    _BUILT = True


def world_flags() -> list[str]:
    """The mode flags the kernel needs to enumerate the world this Python process is in — one per default that is off. All three are module-level defaults consulted at construction time, so the environment is the only lever on the Python side and this is what carries it across to the kernel; the same three tokens ride `run_m1.tables_inputs`, so a flag-on enumeration can never be mistaken for a flag-off one on either side of the seam."""
    return [flag for flag, module, attribute in WORLD_FLAGS if not getattr(module, attribute)]


def settlement_flags() -> list[str]:
    """The two mode flags shared by every direct settlement invocation. Deep-class grain belongs only to enumeration, so it is deliberately absent from this narrower list."""
    return [flag for flag, module, attribute in SETTLEMENT_FLAGS if not getattr(module, attribute)]


def class_grain() -> bool:
    """Whether the enumeration this process asks for splits its deep slots into outcome fibers — the grain rule the crate applies, restated on the Python side for the callers that have to name it. `AMS_DEEP_CLASSES` asks for class grain, but the fibers have a source only where a deep token can move an outcome at all: in the pinned candidacy world, with neither the simulated prospect nor the shifted vote slots, there is nothing to probe and the crate enumerates at label grain whatever the flag says. `run_m1.tables_inputs` reads this, because the stamp on a serialized enumeration has to distinguish the two grains."""
    return DEEP_CLASSES_DEFAULT and (settle.SIMULATED_PROSPECT_DEFAULT or settle.VOTE_SLOTS_DEFAULT)


def enumerate_configs(
    spec_path: Path,
    out_dir: Path,
    configs: Sequence[str],
    *,
    threads: int,
    timings: bool = False,
    timings_tag: str | None = None,
) -> dict[str, Path]:
    """Every named configuration's transition stream, enumerated by one kernel process into `out_dir` and returned as `{config: path}`. The files are plain ndjson — the compression the artifacts wear is Python's job, since the crate carries serde_json and nothing else — and which file holds which configuration is the caller's own token, because the crate refuses a token that is not the canonical spelling of the features it names. Raises `KernelRunError` for every shape of refusal the CLI contract distinguishes, and for a run that exits clean having left a stream unwritten.

    `timings_tag` names the configuration a whole invocation stands for, and is what a caller running one process per configuration passes: the crate labels its per-configuration lines `enumerate[<config>]` already, but `spec_parse` and `enumerate_total` name the process rather than any configuration, and six processes' worth of those would be six unattributable pairs in the cycle journal. Tagged, they read `spec_parse[<config>]`.
    """
    arguments = [
        str(BINARY),
        "enumerate-configs",
        str(spec_path),
        str(out_dir),
        f"--configs={','.join(configs)}",
        f"--threads={threads}",
        *world_flags(),
    ]
    if timings:
        arguments.append("--timings")
    finished = _run_kernel(arguments, "enumerate-configs")
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support enumerate-configs yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on enumerate-configs: {errors}")
    if finished.stdout:
        raise KernelRunError(
            f"the kernel wrote {len(finished.stdout)} bytes to stdout on a clean enumerate-configs exit, where the answer is the files"
        )
    _forward_stderr(errors, timings, arguments, timings_tag)
    streams = {config: out_dir / f"transitions-{config}.ndjson" for config in configs}
    missing = [config for config, path in streams.items() if not path.is_file()]
    if missing:
        left = sorted(found.name for found in out_dir.glob("*")) if out_dir.is_dir() else []
        raise KernelRunError(
            f"the kernel exited clean but wrote no stream for {', '.join(missing)} — it left {left}"
        )
    return streams


def _forward_stderr(errors: str, timings: bool, arguments: list[str], tag: str | None = None) -> None:
    """Pass the kernel's timing lines through to this process's own stderr and refuse everything else. `--timings` is the one thing that writes to a clean exit's stderr, and it writes only `[t] <label> <secs>s` lines, buffered and flushed in `--configs` order; forwarding them verbatim is what puts the kernel's per-configuration walls in the same journal as the Python stage's, since `cycle_timings` reads both off a step's captured output. A `tag` bracket is appended to whichever labels do not carry one already, so a fan-out that spends one process per configuration stays attributable."""
    if not errors:
        return
    lines = errors.split("\n")
    if not timings:
        raise KernelRunError(
            f"the kernel wrote to stderr on a clean enumerate-configs exit: {errors} ({' '.join(arguments)})"
        )
    stray = [line for line in lines if not line.startswith("[t] ")]
    if stray:
        raise KernelRunError(
            f"the kernel wrote {len(stray)} non-timing lines to stderr on a clean enumerate-configs exit: {stray[0]}"
        )
    for line in lines:
        print(_tagged(line, tag) if tag else line, file=sys.stderr, flush=True)


def _tagged(line: str, tag: str) -> str:
    marker, _, rest = line.partition(" ")
    label, separator, tail = rest.partition(" ")
    if not separator or label.endswith("]"):
        return line
    return f"{marker} {label}[{tag}] {tail}"


def _settle_cases(
    spec_path: Path,
    cases_path: Path,
    cases: Sequence[Mapping],
    features: frozenset[str],
) -> list[dict]:
    """Invoke `settle-cases` over one already-written spec and case file, then prove that the kernel returned one answer per question without changing or reordering any question fields."""
    arguments = [str(BINARY), "settle-cases", str(spec_path), str(cases_path)]
    if features:
        arguments.append(f"--features={','.join(sorted(features))}")
    arguments.extend(settlement_flags())
    finished = _run_kernel(arguments, "settle-cases")
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support settle-cases yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on settle-cases: {errors}")
    if errors:
        raise KernelRunError(f"the kernel wrote to stderr on a clean settle-cases exit: {errors}")
    try:
        lines = finished.stdout.decode().splitlines()
    except UnicodeDecodeError as error:
        raise KernelRunError(f"the kernel wrote non-UTF-8 settle-cases output: {error}") from None
    if len(lines) != len(cases):
        raise KernelRunError(f"settle-cases returned {len(lines)} answers for {len(cases)} questions")
    answers: list[dict] = []
    for line_number, (line, question) in enumerate(zip(lines, cases), 1):
        try:
            answer = json.loads(line)
        except json.JSONDecodeError as error:
            raise KernelRunError(f"settle-cases line {line_number} is not JSON: {error.msg}") from None
        if not isinstance(answer, dict):
            raise KernelRunError(f"settle-cases line {line_number} is not a JSON object")
        expected_question = {key: value for key, value in question.items() if key != "result"}
        returned_question = {key: value for key, value in answer.items() if key != "result"}
        if list(answer) != list(question) or returned_question != expected_question:
            raise KernelRunError(f"settle-cases line {line_number} changed or reordered its question")
        if "result" not in answer:
            raise KernelRunError(f"settle-cases line {line_number} has no result")
        answers.append(answer)
    return answers


def settle_cases(spec: ResolvedSpec, cases: Sequence[Mapping], features: frozenset[str]) -> list[dict]:
    """Replay a batch of settlement windows through the crate and return the full trace objects it emitted. The case dictionaries use the `ams-m1-corpus/3` row shape; the caller owns conversion between those transport rows and pipeline model types."""
    if not cases:
        return []
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        cases_path = directory / "cases.ndjson"
        kernel_io.write_spec(spec, spec_path)
        cases_path.write_text(
            "".join(json.dumps(dict(case), separators=(",", ":")) + "\n" for case in cases),
            encoding="utf-8",
        )
        ensure_built()
        return _settle_cases(spec_path, cases_path, cases, frozenset(features))


def _guard_verdicts(spec: ResolvedSpec, spec_path: Path) -> FormationGuard:
    """Invoke `guard-sweep` over one already-dumped spec and parse its complete answer. Completeness and uniqueness are checked here rather than left to a consumer's lookup miss, because a clean kernel exit that silently omitted or duplicated a row is a broken boundary, not an emitter error."""
    arguments = [str(BINARY), "guard-sweep", str(spec_path)]
    finished = _run_kernel(arguments, "guard-sweep")
    errors = finished.stderr.decode(errors="replace").strip()
    if finished.returncode == 2:
        raise KernelRunError(
            f"kernel does not support guard-sweep yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if finished.returncode != 0:
        raise KernelRunError(f"the kernel exited {finished.returncode} on guard-sweep: {errors}")
    if errors:
        raise KernelRunError(f"the kernel wrote to stderr on a clean guard-sweep exit: {errors}")
    try:
        lines = finished.stdout.decode().splitlines()
    except UnicodeDecodeError as error:
        raise KernelRunError(f"the kernel wrote non-UTF-8 guard-sweep output: {error}") from None

    rune_names = frozenset(spec.runes)
    ligature_names = frozenset(name for name, rune in spec.runes.items() if rune.sequence)
    verdicts: FormationGuard = {}
    for line_number, line in enumerate(lines, 1):
        fields = line.split("\t")
        if len(fields) != 4:
            raise KernelRunError(
                f"guard-sweep line {line_number} has {len(fields)} tab-separated fields, expected 4: {line!r}"
            )
        ligature, right1_name, right2_name, verdict = fields
        if ligature not in ligature_names:
            raise KernelRunError(
                f"guard-sweep line {line_number} names non-ligature {ligature!r} as its ligature"
            )
        if right1_name not in rune_names:
            raise KernelRunError(
                f"guard-sweep line {line_number} names unknown first-slot rune {right1_name!r}"
            )
        right1 = settle.RightToken("letter", right1_name)
        if right2_name in rune_names:
            right2 = settle.RightToken("letter", right2_name)
        else:
            right2 = GUARD_TAIL_TOKENS.get(right2_name)
            if right2 is None:
                raise KernelRunError(
                    f"guard-sweep line {line_number} names unknown second-slot token {right2_name!r}"
                )
        if verdict not in ("blocked", "free"):
            raise KernelRunError(
                f"guard-sweep line {line_number} has unknown verdict {verdict!r}, expected 'blocked' or 'free'"
            )
        key = (ligature, right1, right2)
        if key in verdicts:
            raise KernelRunError(f"guard-sweep line {line_number} duplicates {fields[:3]}")
        verdicts[key] = verdict == "blocked"

    letters = tuple(settle.RightToken("letter", name) for name in sorted(rune_names))
    second_slots = (*letters, *GUARD_TAIL_TOKENS.values())
    expected = {
        (ligature, right1, right2)
        for ligature in ligature_names
        for right1 in letters
        for right2 in second_slots
    }
    missing = expected - verdicts.keys()
    extra = verdicts.keys() - expected
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {len(missing)}")
        if extra:
            detail.append(f"carrying {len(extra)} unexpected")
        raise KernelRunError(f"guard-sweep returned an incomplete surface ({', '.join(detail)} verdicts)")
    return verdicts


def guard_sweep(spec: ResolvedSpec) -> FormationGuard:
    """The crate's complete config-blind section 5.7 verdict surface for `spec`, parsed into Python model tokens. One call performs exactly one `guard-sweep` invocation."""
    with tempfile.TemporaryDirectory() as scratch:
        spec_path = Path(scratch) / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        ensure_built()
        return _guard_verdicts(spec, spec_path)


def read_stream(stream: Path) -> FixpointProduct:
    """One kernel stream read back as the product it stands for. `enumerate-configs` writes plain ndjson, which `kernel_io.read_transitions` reads straight off the open handle — the gzip it wraps a path in is what the artifacts under `rebuild/out/` wear, not something a stream on its way into one fold needs. The file goes as soon as the product is in hand: a live configuration's stream is hundreds of megabytes and a whole cycle's worth would otherwise sit in the scratch directory for the length of the build."""
    with stream.open("rt", encoding="utf-8") as handle:
        product = kernel_io.read_transitions(handle)
    stream.unlink()
    return product


def enumerate_transitions(spec: ResolvedSpec, features: frozenset[str]) -> FixpointProduct:
    """One configuration's reachable windows, enumerated by the crate and parsed back into the value the Python half folds. This is the seam `table.FixpointProduct` is stated at: the kernel enumerates, `table.assemble_tables` folds, and nothing between them is consulted, so a product that arrived over this boundary assembles into exactly the tables the enumeration that produced it would have. Everything is in memory and nothing survives the call — the spec dump and the stream live in a scratch directory that goes with the frame — which is the form a test, a tool, or a spec someone assembled by hand builds through; `run_m1.build_tables` is the persisting, multi-configuration form, and the only one that stamps what it writes."""
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        ensure_built()
        streams = enumerate_configs(
            spec_path, directory / "streams", [feature_config_token(features)], threads=1
        )
        return read_stream(next(iter(streams.values())))


def build_tables(spec: ResolvedSpec, features: frozenset[str]) -> tuple[DecisionTable, TreatyTable]:
    """One configuration's decision and treaty tables: the crate for the fixpoint, `table.assemble_tables` for the fold. The table-level asserts are deliberately not run here — the live build asks the fold for `assert_outcome_partition` as it folds, and a caller that wants that or `assert_e_stranded` calls it on the table it is handed."""
    return table.assemble_tables(spec, enumerate_transitions(spec, features))
