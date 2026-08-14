"""The deep counterpart to `gate:kernel-differential` (issue #40): the three harnesses the Rust port landed on — `kernel_liveness`, `kernel_fixpoint` and `kernel_differential` — run again across the five arms their make targets spell out, so the evidence the port was accepted on is something a cycle can re-establish rather than something a commit message remembers. The standing gate beside it compares one artifact grain per cycle and costs a fold; this one re-runs the exhaustive sweeps underneath that grain and costs the better part of an hour, so the driver arms it on the sources both engines are written from rather than on every rune edit — `artifact_cycle.kernel_harness_skip_fingerprint` is where that decision lives, and this tool only ever runs once it has been made.

Five arms in one fixed order, cheapest first, each a whole harness in an interpreter of its own: the exhaustive liveness differential, then the fixpoint in the pinned candidacy world, then the guard/fuzz/corpus differential, then the fixpoint in the shipping world and at label grain. Each is invoked exactly as its `make` target invokes it, world and all — the three semantics flags a fixpoint's shape depends on are module-level defaults `settle` and `table` read from the environment at import, so the only way to ask for a world is to hand the child an environment, which is why the arm table carries `AMS_*` overrides where a harness flag would be the obvious thing. The fixpoints all run `--live-only`: the scaling ladder is a bisection instrument for a port under construction, and what this gate re-proves is the live alphabet.

The run stops at the first arm that exits nonzero. A divergence between the two engines is a drop-everything fact, and the arms behind the failing one would spend another fifty minutes rediscovering that something is wrong before anyone read the first line of it. Those arms are then simply absent from the summary rather than recorded as skipped, because absent is already what the driver's verdict function reads as unproven — five arms present and exiting zero is the only green there is.

`kernel_harness_summary.json` lands beside the artifacts on every path this can take, a build that never happened included, because a gate whose subprocess died silently and a gate that wrote a red summary must not look alike to the cycle. Each arm's record is its exit code, its wall seconds and the last few lines of its merged output — enough for the driver to name the failure without a reader going back to the console, and bounded because a diverging fixpoint's output is measured in megabytes. The alphabet's structure digest rides the summary beside them, saying which alphabet the arms swept; it is informational here rather than load-bearing, since the key deciding whether this gate runs at all belongs to the driver. What this never writes is a green record: whether that greenness may be reused turns on inputs this tool cannot see, exactly as `kernel_gate` leaves it.

Run as `uv run python -m rebuild.tools.kernel_harness_gate`, or through `make kernel-harness-gate`, which builds the binary first.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M1_OUT = ROOT / "rebuild" / "out" / "m1"
SUMMARY_NAME = "kernel_harness_summary.json"
SUMMARY_FORMAT = "ams-kernel-harness-summary/1"
# How much of an arm's merged output the summary keeps. A harness prints a running report and, when it diverges, the diff under it; the last few lines are the verdict and the first thing that went wrong is already in the arm's own console output.
TAIL_LINES = 20
# The five arms, cheapest first, and the contract the artifact cycle imports this module for. Names, order and membership are all load-bearing: `evaluate_kernel_harness_gate` walks this tuple and reads an absent arm as one the run never reached.
ARM_NAMES = (
    "liveness-exhaustive",
    "fixpoint-pinned",
    "differential",
    "fixpoint-shipping",
    "fixpoint-label-grain",
)


@dataclass(frozen=True)
class ArmSpec:
    """One arm's invocation: the harness module, the flags its make target passes it, and the environment overrides that put the child in the world the arm compares. The world is an environment rather than an argument because `settle.SIMULATED_PROSPECT_DEFAULT`, `settle.VOTE_SLOTS_DEFAULT` and `table.DEEP_CLASSES_DEFAULT` are read at import time, so a world can only be chosen before the harness starts."""

    module: str
    arguments: tuple[str, ...] = ()
    world: dict[str, str] = field(default_factory=dict)


ARMS: dict[str, ArmSpec] = {
    "liveness-exhaustive": ArmSpec("rebuild.tools.kernel_liveness", ("--exhaustive",)),
    "fixpoint-pinned": ArmSpec(
        "rebuild.tools.kernel_fixpoint",
        ("--live-only",),
        {"AMS_SIMULATED_PROSPECT": "0", "AMS_VOTE_SLOTS": "0"},
    ),
    "differential": ArmSpec("rebuild.tools.kernel_differential"),
    "fixpoint-shipping": ArmSpec("rebuild.tools.kernel_fixpoint", ("--live-only",)),
    "fixpoint-label-grain": ArmSpec(
        "rebuild.tools.kernel_fixpoint", ("--live-only",), {"AMS_DEEP_CLASSES": "0"}
    ),
}


def _label(path: Path) -> str:
    """A path as the report names it: repo-relative inside the tree, absolute for anything else, so a run against a binary outside the tree names it instead of failing on the path arithmetic."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def binary_label() -> str:
    """The kernel the arms will ask, as the summary states it. The import is deferred like every other reach into the pipeline from this module: the artifact cycle imports this file at startup to read `ARM_NAMES` and nothing else, and must not pay a spec-loading import chain for a tuple."""
    from rebuild.pipeline.kernel_exec import BINARY

    return _label(BINARY)


def spec_structure() -> str | None:
    """The digest of the alphabet shape both engines enumerate over, recorded so a summary says what its arms swept. A spec that will not load costs the summary this one field and nothing else — the arms load their own, and the arming key that decides whether any of this runs is the driver's, not this file's, so there is nothing here worth failing a gate over before a single harness has been asked."""
    from rebuild.pipeline import spec_load, trace_memo

    try:
        return trace_memo.spec_structure_digest(spec_load.load_default_spec())
    except Exception:
        return None


def _spawn(argv: list[str], environment: dict[str, str]) -> tuple[int, str]:
    """The one place this gate starts a process: exit code and merged output, run from the repo root so each harness resolves its own defaults the way its make target does. The two streams are merged because a harness's verdict is on stdout and a refusal — no binary, an absent verb — is on stderr, and the summary's tail has to be able to carry either."""
    finished = subprocess.run(
        argv,
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return finished.returncode, finished.stdout


def run_arm(spec: ArmSpec) -> dict:
    """One arm run to completion, as the summary records it. The environment is this process's own plus the arm's world, so the harness inherits the invocation's `PATH`, `AMS_*` and everything else a caller set, and only the flags the arm exists to vary are overridden."""
    argv = [sys.executable, "-m", spec.module, *spec.arguments]
    environment = dict(os.environ) | spec.world
    started = time.perf_counter()
    code, output = _spawn(argv, environment)
    return {
        "exit": code,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "tail": output.splitlines()[-TAIL_LINES:],
    }


def run_arms(summary: dict, skip_build: bool) -> None:
    """Build the crate once, then walk the arms in order until one of them fails. The build is up front and shared: every arm reaches for the same release binary, and a crate that does not compile is one line rather than five identical ones. Fills `summary` as it goes, so a run that stops early still says how far it got."""
    from rebuild.pipeline import kernel_exec

    if not skip_build:
        try:
            kernel_exec.cargo_build()
        except kernel_exec.KernelBuildError as failure:
            summary["error"] = str(failure)
            return
    for name in ARM_NAMES:
        record = run_arm(ARMS[name])
        summary["arms"][name] = record
        state = "OK" if record["exit"] == 0 else f"exited {record['exit']}"
        print(f"  {name:>20}  {state}  {record['elapsed_s']:.1f}s", flush=True)
        print(f"[t] {name} {record['elapsed_s']:.1f}s", flush=True)
        if record["exit"] != 0:
            return


def verdict(summary: dict, elapsed: float) -> tuple[str, bool]:
    """The run's last line and whether it passed, in the order a reader wants the bad news: a crate that never built, then the arm that stopped the run, then a run that ended between arms for some other reason. Green demands every named arm present and exiting zero rather than the absence of a recorded complaint, so a run that fell over midway can never read as agreement."""
    arms = summary["arms"]
    if summary["error"]:
        return f"kernel harness: the arms never ran — {summary['error']}", False
    failed = [name for name in ARM_NAMES if name in arms and arms[name]["exit"] != 0]
    if failed:
        name = failed[0]
        unrun = len(ARM_NAMES) - len(arms)
        return (
            f"kernel harness: {name} exited {arms[name]['exit']} after {elapsed:.1f}s, leaving {unrun} arms behind it unrun",
            False,
        )
    if list(arms) != list(ARM_NAMES):
        return (
            f"kernel harness: only {len(arms)} of {len(ARM_NAMES)} arms ran in {elapsed:.1f}s",
            False,
        )
    return f"kernel harness: {len(arms)} arms agree in {elapsed:.1f}s", True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-run the port's three landing harnesses across their five arms, and record how each one exited."
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="run the arms against the binary already on disk instead of building the crate first",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=M1_OUT,
        help="the directory the summary is written to (default rebuild/out/m1)",
    )
    args = parser.parse_args(argv)
    summary = {
        "format": SUMMARY_FORMAT,
        "binary": binary_label(),
        "structure": spec_structure(),
        "arms": {},
        "error": None,
    }
    start = time.perf_counter()
    print(
        f"kernel harness: {len(ARM_NAMES)} arms against {summary['binary']}, summary under {_label(args.out)}",
        flush=True,
    )
    run_arms(summary, args.skip_build)
    path = args.out / SUMMARY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")
    line, passed = verdict(summary, time.perf_counter() - start)
    if summary["error"]:
        print(f"kernel harness: {summary['error']}", file=sys.stderr)
    print(line, flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
