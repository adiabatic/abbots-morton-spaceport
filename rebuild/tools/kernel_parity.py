"""Differential proof that the Rust kernel's spec ingest is lossless (issue #40, sub-issue #42): for the live alphabet and every rung of the scaling ladder, dump a resolved spec through `kernel_io.spec_json`, hand the file to `ams-m1-kernel spec-echo`, and require the bytes it prints back to be identical to the ones Python wrote.

Byte identity is the whole assertion, and it is a stronger one than a value comparison: the binary parses the dump into its interned model, drops the parse tree, and re-emits from the model alone, so a field the model forgot to carry, a mapping it reordered, and an escape it spells differently all surface here as a diff rather than as a disagreement discovered several sub-issues downstream. `rebuild/pipeline/kernel_io.py` is the binding contract on the Python side, and a change to `rebuild/pipeline/model.py` is a cross-group coordination event: this harness is what catches the Rust side lagging one.

The ladder is `bench-the-rebuild/scaling/scaling.py`'s, reproduced here rather than imported because that script is a benchmark whose module body runs the kernel: ligature closure first and then alphabetical, rungs at `sorted({*range(6, len(base), 2), len(base)})`, and each rung's keep-set filtered so a ligature rides only when every component it names rides with it. Byte-wise a rung adds nothing over the live dump — a nested subset with the registry riding whole can only restate bytes the live check already covers — so the rungs are not here for extra coverage today: they cost milliseconds, they are the sub-issue's stated bar, and they keep this harness aligned rung-for-rung with the per-rung differential beds the later port stages run their fixpoint comparisons on.

Run it as `uv run python -m rebuild.tools.kernel_parity` once the binary exists, or through `make kernel-parity`, which builds it first.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
from pathlib import Path

from rebuild.pipeline import kernel_io
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.spec_load import load_default_spec

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
CONTEXT = 48
TIMEOUT = 60


def ladder_order(spec: ResolvedSpec) -> list[str]:
    """The nested subset order the rungs are cut from: every ligature preceded by the components it names, then the remaining runes alphabetically. Nesting is what makes the rungs comparable to each other — rung k is rung k-2 plus two more runes, never a different alphabet."""
    names = sorted(spec.runes)
    order: list[str] = []
    for name in names:
        sequence = spec.runes[name].sequence
        if not sequence:
            continue
        for part in sequence:
            if part not in order:
                order.append(part)
        if name not in order:
            order.append(name)
    for name in names:
        if name not in order:
            order.append(name)
    return order


def ladder_rungs(order: list[str]) -> list[int]:
    """Rune counts to cut sub-specs at: every even count from 6 up, plus the whole alphabet however odd its size."""
    return sorted({*range(6, len(order), 2), len(order)})


def sub_spec(spec: ResolvedSpec, order: list[str], rung: int) -> ResolvedSpec:
    """The first `rung` runes of the nested order as a spec of their own, in the original spec's rune order, with any ligature whose components did not make the cut dropped."""
    candidates = set(order[:rung])
    keep: set[str] = set()
    for name in candidates:
        sequence = spec.runes[name].sequence
        if not sequence or set(sequence) <= candidates:
            keep.add(name)
    return dataclasses.replace(spec, runes={name: rune for name, rune in spec.runes.items() if name in keep})


def first_difference(written: bytes, echoed: bytes) -> int:
    """The offset of the first byte the two sides disagree on, or the length of the shorter one when the disagreement is that one ran out."""
    for offset in range(min(len(written), len(echoed))):
        if written[offset] != echoed[offset]:
            return offset
    return min(len(written), len(echoed))


def echo(path: Path) -> tuple[bytes, bytes, int | None]:
    """Run `spec-echo` over one dump file, returning its stdout, its stderr, and its exit status — None for a kernel that gave no answer within `TIMEOUT` seconds, so a wedged parser reads as a failing rung rather than a hung build."""
    try:
        finished = subprocess.run([str(BINARY), "spec-echo", str(path)], capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return b"", f"no answer within {TIMEOUT} seconds".encode(), None
    return finished.stdout, finished.stderr, finished.returncode


def check(label: str, spec: ResolvedSpec, directory: Path) -> bool:
    """Dump one spec, echo it through the kernel, and report the comparison as a single line — plus, when the bytes disagree, where they first do and what each side has there. A clean exit that still wrote to stderr fails too: the CLI contract reserves stderr for error complaints, so chatter there is a contract break the byte compare cannot see."""
    dump = (kernel_io.spec_json(spec) + "\n").encode()
    path = directory / f"spec-{label}.json"
    path.write_bytes(dump)
    echoed, errors, status = echo(path)
    ok = status == 0 and echoed == dump and not errors
    print(f"  {label:>5}  {len(spec.runes):3d} runes  {len(dump):7d} bytes  {'OK' if ok else 'FAIL'}")
    if ok:
        return True
    if status != 0:
        exited = "gave no answer" if status is None else f"exited {status}"
        print(f"        the kernel {exited}: {errors.decode(errors='replace').strip()}")
        return False
    if errors:
        print(
            f"        the kernel wrote to stderr on a clean exit: {errors.decode(errors='replace').strip()}"
        )
        if echoed == dump:
            return False
    offset = first_difference(dump, echoed)
    print(f"        first difference at byte {offset} of {len(dump)} written, {len(echoed)} echoed")
    start = max(0, offset - CONTEXT)
    print(f"        python[{start}:{offset + CONTEXT}] {dump[start : offset + CONTEXT]!r}")
    print(f"        kernel[{start}:{offset + CONTEXT}] {echoed[start : offset + CONTEXT]!r}")
    return False


def main() -> int:
    if not BINARY.is_file():
        print(
            f"kernel parity: no kernel binary at {BINARY.relative_to(ROOT)} — run `make kernel-build` first",
            file=sys.stderr,
        )
        return 1
    spec = load_default_spec()
    order = ladder_order(spec)
    rungs = ladder_rungs(order)
    print(
        f"kernel parity: the live alphabet plus {len(rungs)} ladder rungs against {BINARY.relative_to(ROOT)}"
    )
    failures = 0
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        if not check("live", spec, directory):
            failures += 1
        for rung in rungs:
            if not check(f"r{rung}", sub_spec(spec, order, rung), directory):
                failures += 1
    if failures:
        print(f"kernel parity: {failures} of {len(rungs) + 1} dumps did not survive the round trip")
        return 1
    print("kernel parity: every dump echoed back byte for byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
