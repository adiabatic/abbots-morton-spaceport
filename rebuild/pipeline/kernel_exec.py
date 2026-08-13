"""The Rust kernel's invocation seam for the pipeline (issue #40, sub-issue #47): build the binary, and ask it for one whole cycle's transition streams. It lives here rather than beside the four `rebuild/tools/kernel_*.py` harnesses because `run_m1` is what calls it and the pipeline does not import from the tools tree; the harnesses keep their own copies of the same constants, which is duplication with a reason — each of them is an exit-bar instrument that has to state the contract it is measuring rather than inherit it from the thing it measures.

The build is `cargo build --release` against the crate's own manifest and nothing else, because release is the only profile anything in this repo runs: the parity, differential, fixpoint and liveness harnesses all reach for `target/release/ams-m1-kernel`, and a debug binary that answered would answer far too slowly to be the same experiment. A box with no `cargo` is a `KernelBuildError` carrying the remedy rather than a stack trace, since that is the one failure a reader can fix in a minute.

`enumerate_configs` is the fan-out verb and the only one the pipeline needs: one process answers every acceptance configuration, writing each one's stream to a file of its own, and the streams are byte-identical to what the same binary emits one configuration at a time at any thread width (sub-issue #46's exit bar). Threads are the caller's to choose because the ceiling is memory rather than CPU — a live configuration holds its whole working set until it has emitted — so `KERNEL_THREADS_DEFAULT` is sub-issue #46's measured width on a 32 GB box and callers cap it at the number of configurations there are to answer and at the CPUs there are to answer them with.

The invocation is read strictly, on the CLI contract's own terms: exit 2 is the usage check, which for a well-formed invocation can only mean the verb is absent or the two sides' flag sets have drifted apart; any other nonzero exit is the kernel complaining about its inputs; and stderr on a clean exit is a failure unless timings were asked for, in which case every `[t]` line is forwarded to this process's own stderr verbatim so the cycle journal reads the kernel's per-configuration walls the same way it reads Python's, and anything else on that stream is still a failure. The answer is the files, so bytes on stdout are a failure too.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from rebuild.pipeline import settle, table

REPO_ROOT = Path(__file__).resolve().parents[2]
BINARY = REPO_ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
MANIFEST = REPO_ROOT / "rebuild" / "kernel-rs" / "Cargo.toml"
KERNEL_THREADS_DEFAULT = 3
TIMEOUT = 1800
# How much of a failed build's stderr rides the exception: cargo says what is wrong in its last few lines and repeats the whole compilation above them.
BUILD_TAIL_LINES = 20
# The three semantics flags a fixpoint's shape depends on, each as (the kernel flag that says it is off, the module holding the default, the attribute), exactly as `rebuild/tools/kernel_fixpoint.py` states them. Off is what carries a flag, so the shipping world invokes the verb bare.
WORLD_FLAGS = (
    ("--candidacy-prospect", settle, "SIMULATED_PROSPECT_DEFAULT"),
    ("--vote-slots-off", settle, "VOTE_SLOTS_DEFAULT"),
    ("--deep-classes-off", table, "DEEP_CLASSES_DEFAULT"),
)


class KernelBuildError(RuntimeError):
    """`cargo` is absent or the crate did not build. Distinct from a run failure, which is a binary that exists and answered badly."""


class KernelRunError(RuntimeError):
    """The binary refused the invocation, exited nonzero, complained on a clean exit, or left a stream unwritten."""


def cargo_build() -> None:
    """Build the kernel in release mode, the way `make kernel-build` does. Callers run this before every fan-out rather than checking whether the binary exists: a stale binary and a fresh one are the same file, and the whole point of a differential engine is that the sources on disk are what answered. A warm build costs a fraction of a second; a cold one costs what a cold one costs."""
    arguments = ["cargo", "build", "--release", "--manifest-path", str(MANIFEST)]
    try:
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


def world_flags() -> list[str]:
    """The mode flags the kernel needs to enumerate the world this Python process is in — one per default that is off. All three are module-level defaults consulted at construction time, so the environment is the only lever on the Python side and this is what carries it across to the kernel; the same three tokens ride `run_m1.tables_inputs`, so a flag-on enumeration can never be mistaken for a flag-off one on either side of the seam."""
    return [flag for flag, module, attribute in WORLD_FLAGS if not getattr(module, attribute)]


def enumerate_configs(
    spec_path: Path,
    out_dir: Path,
    configs: Sequence[str],
    *,
    threads: int,
    timings: bool = False,
) -> dict[str, Path]:
    """Every named configuration's transition stream, enumerated by one kernel process into `out_dir` and returned as `{config: path}`. The files are plain ndjson — the compression the artifacts wear is Python's job, since the crate carries serde_json and nothing else — and which file holds which configuration is the caller's own token, because the crate refuses a token that is not the canonical spelling of the features it names. Raises `KernelRunError` for every shape of refusal the CLI contract distinguishes, and for a run that exits clean having left a stream unwritten."""
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
    try:
        finished = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise KernelRunError(
            f"no kernel binary at {BINARY} — run `make kernel-build` first, or let the caller's cargo_build() build it"
        ) from None
    except subprocess.TimeoutExpired:
        raise KernelRunError(
            f"the kernel gave no answer within {TIMEOUT} seconds on enumerate-configs ({' '.join(arguments)})"
        ) from None
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
    _forward_stderr(errors, timings, arguments)
    streams = {config: out_dir / f"transitions-{config}.ndjson" for config in configs}
    missing = [config for config, path in streams.items() if not path.is_file()]
    if missing:
        left = sorted(found.name for found in out_dir.glob("*")) if out_dir.is_dir() else []
        raise KernelRunError(
            f"the kernel exited clean but wrote no stream for {', '.join(missing)} — it left {left}"
        )
    return streams


def _forward_stderr(errors: str, timings: bool, arguments: list[str]) -> None:
    """Pass the kernel's timing lines through to this process's own stderr and refuse everything else. `--timings` is the one thing that writes to a clean exit's stderr, and it writes only `[t] <label> <secs>s` lines, buffered and flushed in `--configs` order; forwarding them verbatim is what puts the kernel's per-configuration walls in the same journal as the Python stage's, since `cycle_timings` reads both off a step's captured output."""
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
        print(line, file=sys.stderr, flush=True)
