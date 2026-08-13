"""The fixpoint exit bar (issue #40, sub-issue #44): the whole table-build worklist run twice for each configuration — once by Python's `table.enumerate_transitions` and once by the Rust kernel's `enumerate` verb — and compared at both places a difference could ever surface, the stream the kernel hands back and the artifacts Python folds out of it.

Three comparisons per (spec, configuration), each load-bearing for a different reason. The stream is compared as raw bytes against what `kernel_io.write_transitions` writes for Python's own product, which asserts far more than that the two sides found the same windows: rows in the product's own key order, the cell vocabulary seated the way `table._cell_key` sorts it, the provenance the engine fired while tabulating, the deep-class map, and every field's exact JSON spelling. The artifacts are compared after the kernel's own bytes have been fed back through `kernel_io.read_transitions` and `table.assemble_tables` — the seam this port is built around, where Python keeps the rule fold, the treaty fold and every writer forever — so what is proved is not that two products resemble each other but that the Rust one folds into the same three files a build persists. `table.table_digest` is the third, at the grain the rest of the rebuild states table identity in; it is deliberately redundant, and a digest that agreed while the bytes disagreed would be saying the digest had stopped covering something.

The harness answers whichever world the Python process is in, and tells the kernel which one that is. `simulated_prospect` and `vote_slots` are engine defaults `settle` reads from the environment at import, `DEEP_CLASSES_DEFAULT` is `table`'s companion, and all three change what a fixpoint enumerates rather than how fast it gets there; a comparison that let the two sides pick their worlds separately would blame the port for every row of the difference. So the flags are reflected off the Python side's own defaults rather than pinned here — the shipping defaults invoke the verb bare, `AMS_SIMULATED_PROSPECT=0 AMS_VOTE_SLOTS=0` reproduces sub-issue #44's pinned candidacy world, and `AMS_DEEP_CLASSES=0` is the label-grain arm — and each exit-bar arm is one `make` target: `kernel-fixpoint`, `kernel-fixpoint-pinned`, `kernel-fixpoint-label-grain`. The world rides the run's own header line, because a byte comparison that agrees says nothing until you know what it agreed about.

The kernel's verb is probed before any Python fixpoint runs. A live fixpoint costs tens of seconds per configuration, so a binary that predates the verb would otherwise be discovered after minutes of work with nowhere to go; the probe is one mini-fixture enumeration, and a binary that answers exit 2 to it reads as the verb being absent — one clean line, exactly as `kernel_differential` reads that status.

Specs are every rung of `kernel_parity`'s nested scaling ladder and then the live alphabet, cheapest first: a port that is wrong is usually wrong at six runes too, and a rung answers in seconds where the live alphabet takes minutes. `--live-only` skips the ladder for the iteration loop. The ladder's last rung is the whole alphabet by construction, so the full form enumerates the live spec twice — kept rather than special-cased, because the rungs are the sub-issue's stated bar and are what the later differential beds are cut at.

Run as `uv run python -m rebuild.tools.kernel_fixpoint`, or through `make kernel-fixpoint`, which builds the binary first.
"""

from __future__ import annotations

import argparse
import gzip
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rebuild.pipeline import conform, fixtures, kernel_io, settle, table
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import kernel_parity
from rebuild.tools.kernel_differential import Arm, KernelVerbMissing, compare_lines

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
TIMEOUT = 3600
CONTEXT = 48
LINE_LIMIT = 400
# The sources stamp `write_windows` seals into the windows head. Both sides get this same constant on purpose: the stamp names the files a build was cut from, which is not what this harness compares, and a real fingerprint would put a value that moves on every rune edit inside a byte comparison.
INPUTS_STAMP = "kernel-fixpoint"
# The three semantics flags a fixpoint's shape depends on, each as (the kernel flag that says it is off, the module holding the default, the attribute). Off is what carries a flag, so the shipping world invokes the verb bare; the environment spellings the recipes bake in are AMS_SIMULATED_PROSPECT, AMS_VOTE_SLOTS and AMS_DEEP_CLASSES respectively.
WORLD_FLAGS = (
    ("--candidacy-prospect", settle, "SIMULATED_PROSPECT_DEFAULT"),
    ("--vote-slots-off", settle, "VOTE_SLOTS_DEFAULT"),
    ("--deep-classes-off", table, "DEEP_CLASSES_DEFAULT"),
)


def world_flags() -> list[str]:
    """The mode flags the kernel needs to enumerate the world this Python process is in — one per default that is off. All three are module-level defaults consulted at construction time, so the environment is the only lever on the Python side and this is what carries it across to the kernel."""
    return [flag for flag, module, attribute in WORLD_FLAGS if not getattr(module, attribute)]


def world_label() -> str:
    """The world named in the run's header, in the flag spelling both sides use — `shipping defaults` when nothing is off — plus the grain the deep slots enumerate at, which no flag list states on its own: the pinned candidacy world has no fibre source and so enumerates at label grain whatever `AMS_DEEP_CLASSES` says, which is exactly the coincidence a header naming only the flags would let a reader miss. The grain is read off `table`'s own rule rather than restated, so the two can only agree. A byte comparison says nothing until you know which fixpoint it compared."""
    flags = world_flags()
    grain = "class grain" if table.DEEP_CLASSES_DEFAULT and table._deep_world(None) else "label grain"
    return f"{' '.join(flags) if flags else 'shipping defaults'}, {grain}"


def _run(arguments: list[str]) -> tuple[bytes, str, int | None]:
    """One kernel invocation: its stdout as raw bytes, its stderr, and its exit status — None when it gave no answer within `TIMEOUT` seconds, so a wedged fixpoint reads as a failing configuration rather than a hung build. Bytes rather than the sibling harness's decoded lines because byte identity against a stream Python wrote is the whole assertion here, and decoding would launder exactly the differences this is looking for."""
    try:
        finished = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return b"", f"no answer within {TIMEOUT} seconds", None
    return finished.stdout, finished.stderr.decode(errors="replace").strip(), finished.returncode


def _kernel(arguments: list[str], verb: str) -> bytes:
    """The kernel's stdout, with the two failure shapes the CLI contract distinguishes turned into exceptions: exit 2 is the usage check, which for a well-formed invocation can only mean the verb is absent or the flag sets have drifted apart, and any other nonzero exit is the kernel complaining about its inputs. Matches `kernel_differential._kernel` line for line bar the byte-level return; the two harnesses read the same contract."""
    stdout, errors, status = _run(arguments)
    if status == 2:
        raise KernelVerbMissing(
            f"kernel does not support {verb} yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if status != 0:
        exited = "gave no answer" if status is None else f"exited {status}"
        raise RuntimeError(f"the kernel {exited} on {verb}: {errors}")
    if errors:
        raise RuntimeError(f"the kernel wrote to stderr on a clean {verb} exit: {errors}")
    return stdout


def enumerate_flags(config: str) -> list[str]:
    """The `enumerate` flags one configuration calls for: its active stylistic sets, and the world this process is in. An empty feature set passes no flag at all rather than an empty value, matching the CLI's own refusal of `--features=`; a shipping-defaults run therefore names only its features, and the mode flags appear exactly where the Python side has a default switched off."""
    features = sorted(conform.features_for_config(config))
    flags = [f"--features={','.join(features)}"] if features else []
    return [*flags, *world_flags()]


def probe(scratch: Path) -> None:
    """Ask the kernel to enumerate the mini fixture before any live fixpoint runs. The mini spec's fixpoint costs the binary milliseconds, so this buys the fail-fast for nothing: a binary built before the verb existed exits 2 here and the whole run ends on one line, rather than after the minutes of Python enumeration whose answer had nowhere to go."""
    path = scratch / "spec-probe.json"
    kernel_io.write_spec(fixtures.mini_spec(), path)
    _kernel([str(BINARY), "enumerate", str(path), *enumerate_flags("default")], "enumerate")


def stream_bytes(product: table.FixpointProduct, path: Path) -> bytes:
    """The transition stream one product serializes to, gunzipped: the bytes the kernel's stdout is compared against. It goes through `kernel_io.write_transitions` and a file rather than being formatted here, so this side has exactly one spelling of the format and it is the one the build itself uses."""
    kernel_io.write_transitions(product, path)
    with gzip.open(path, "rb") as handle:
        return handle.read()


def packed(stream: bytes, path: Path) -> Path:
    """The kernel's stdout packed into the file shape `read_transitions` reads, with the stamp zeroed exactly as `write_transitions` zeroes it. Compression is the harness's job because the crate carries serde_json and nothing else, and what rides through the seam this way is the kernel's own bytes rather than a re-serialization of something Python built."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
        handle.write(stream)
    return path


def artifacts(
    spec: ResolvedSpec, product: table.FixpointProduct, directory: Path
) -> tuple[dict[str, bytes], str]:
    """The three files a build persists for one configuration, plus the contract digest, folded out of one product by the Python half of the build alone. `assemble_tables`, the rule fold, the treaty fold and every writer stay Python forever, so feeding both sides' products through this identical path is what turns a stream comparison into a statement about the artifacts a build would ship."""
    decision, treaty = table.assemble_tables(spec, product)
    windows = table.windows_path(directory, decision.config)
    table.write_windows(decision, windows, INPUTS_STAMP)
    settlement = directory / f"settlement-{decision.config}.tsv"
    decision.write_tsv(settlement)
    treaties = directory / f"treaties-{decision.config}.tsv"
    treaty.write_tsv(treaties)
    written = {path.name: path.read_bytes() for path in (windows, settlement, treaties)}
    return written, table.table_digest(decision, treaty)


def kernel_tables(
    spec: ResolvedSpec, stream: bytes, directory: Path, label: str
) -> tuple[dict[str, bytes], str] | None:
    """The artifacts and digest Python folds out of the kernel's own stream — the seam the exit bar is stated at — or None when those bytes do not survive the trip at all, which is a divergence of its own and is reported as one instead of ending the run. A stream that has already diverged is still worth folding: the fold is where a difference the byte compare reported as one line turns out to be a cell the table cannot seat."""
    try:
        product = kernel_io.read_transitions(packed(stream, directory / "transitions.ndjson.gz"))
        return artifacts(spec, product, directory)
    except (OSError, ValueError, IndexError, KeyError, table.PartitionError) as complaint:
        print(f"    {label}: the kernel's stream did not fold into tables: {complaint}", flush=True)
        return None


def _lines(data: bytes, gzipped: bool) -> list[str]:
    """One blob's lines for the divergence report, gunzipped first when the artifact is gzipped and empty when the bytes cannot be read that way at all — in which case the byte report beside it is the one that says anything useful. Over-long lines are clipped, because the head line of a stream or a windows file is one JSON object naming every reachable cell and every fired pointer, and printing three-quarters of a megabyte twice is a wall of text where the byte report below is a location; clipping can only ever cost the line view a difference past the clip, which is exactly the difference the byte report states outright."""
    try:
        raw = gzip.decompress(data) if gzipped else data
    except OSError, EOFError:
        return []
    lines = raw.decode(errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [
        line if len(line) <= LINE_LIMIT else f"{line[:LINE_LIMIT]}… ({len(line)} chars)" for line in lines
    ]


def compare_blobs(label: str, expected: bytes, got: bytes, gzipped: bool = False) -> int:
    """Two blobs compared as bytes, with the disagreement reported in the terms the port is debugged in: the first few differing lines through the sibling harness's report, and then the first differing byte with context, which is the authority on where they part and the only view that can show a difference no line carries — a gzip parameter, a trailing newline, an encoding. Returns 1 when they differ, because the assertion is on the whole blob and a blob is either identical or it is not."""
    if expected == got:
        return 0
    print(f"    {label} diverged", flush=True)
    compare_lines(label, _lines(expected, gzipped), _lines(got, gzipped))
    offset = kernel_parity.first_difference(expected, got)
    print(f"      first difference at byte {offset} of {len(expected)} python, {len(got)} kernel", flush=True)
    start = max(0, offset - CONTEXT)
    print(f"      python[{start}:{offset + CONTEXT}] {expected[start : offset + CONTEXT]!r}", flush=True)
    print(f"      kernel[{start}:{offset + CONTEXT}] {got[start : offset + CONTEXT]!r}", flush=True)
    return 1


def run_config(
    spec: ResolvedSpec,
    spec_path: Path,
    label: str,
    config: str,
    stream: Arm,
    artifact: Arm,
    digest: Arm,
) -> None:
    """One (spec, configuration) compared end to end: Python's fixpoint, the kernel's, the stream bytes, the three artifacts through the seam, and the digest. Its scratch is its own, because a live configuration's windows file is tens of megabytes and nothing after the comparison reads it."""
    features = conform.features_for_config(config)
    product = table.enumerate_transitions(spec, features)
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        expected = stream_bytes(product, directory / "python" / "transitions.ndjson.gz")
        got = _kernel([str(BINARY), "enumerate", str(spec_path), *enumerate_flags(config)], "enumerate")
        divergences = compare_blobs(f"{label} stream", expected, got)
        stream.note(1, divergences)
        want_files, want_digest = artifacts(spec, product, directory / "python")
        folded = kernel_tables(spec, got, directory / "kernel", label)
        if folded is None:
            artifact.note(len(want_files), len(want_files))
            digest.note(1, 1)
            divergences += len(want_files) + 1
        else:
            got_files, got_digest = folded
            for name, wanted in want_files.items():
                got_blob = got_files.get(name)
                if got_blob is None:
                    print(
                        f"    {label} {name}: the kernel-fed fold wrote no artifact by this name — its config token names {sorted(got_files)}",
                        flush=True,
                    )
                    mismatched = 1
                else:
                    mismatched = compare_blobs(f"{label} {name}", wanted, got_blob, name.endswith(".gz"))
                artifact.note(1, mismatched)
                divergences += mismatched
            mismatched = int(want_digest != got_digest)
            if mismatched:
                print(f"    {label} table_digest: python {want_digest}, kernel {got_digest}", flush=True)
            digest.note(1, mismatched)
            divergences += mismatched
    rows = len(product.transitions)
    print(
        f"  {label:>28}  {rows:8d} rows  {len(expected):10d} stream bytes  {'OK' if not divergences else 'FAIL'}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare the Rust kernel's fixpoint against Python's, as stream bytes and as the artifacts the stream folds into."
    )
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="compare the live alphabet alone, skipping the scaling-ladder rungs",
    )
    args = parser.parse_args(argv)
    if not BINARY.is_file():
        print(
            f"kernel fixpoint: no kernel binary at {BINARY.relative_to(ROOT)} — run `make kernel-build` first",
            file=sys.stderr,
        )
        return 1
    live = load_default_spec()
    order = kernel_parity.ladder_order(live)
    rungs = [] if args.live_only else kernel_parity.ladder_rungs(order)
    specs = [(f"r{rung}", kernel_parity.sub_spec(live, order, rung)) for rung in rungs] + [("live", live)]
    stream = Arm("stream")
    artifact = Arm("artifact")
    digest = Arm("digest")
    start = time.perf_counter()
    ladder = "" if args.live_only else f" plus {len(rungs)} ladder rungs"
    print(
        f"kernel fixpoint: {world_label()} — the live alphabet{ladder} at {len(conform.ACCEPTANCE_CONFIGS)} configurations against {BINARY.relative_to(ROOT)}",
        flush=True,
    )
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        try:
            probe(directory)
            for name, sub in specs:
                path = directory / f"spec-{name}.json"
                kernel_io.write_spec(sub, path)
                print(f"{name}: {len(sub.runes)} runes", flush=True)
                for config in conform.ACCEPTANCE_CONFIGS:
                    run_config(sub, path, f"{name} {config}", config, stream, artifact, digest)
        except KernelVerbMissing as missing:
            print(f"kernel fixpoint: {missing}", file=sys.stderr)
            return 1
        except RuntimeError as failure:
            print(f"kernel fixpoint: {failure}", file=sys.stderr)
            return 1
    elapsed = time.perf_counter() - start
    arms = [arm for arm in (stream, artifact, digest) if arm.compared or arm.divergences]
    tally = ", ".join(f"{arm.compared} {arm.label}" for arm in arms) or "nothing"
    divergences = sum(arm.divergences for arm in arms)
    if divergences:
        print(f"kernel fixpoint: {divergences} divergences over {tally} comparisons in {elapsed:.1f}s")
        return 1
    print(f"kernel fixpoint: {tally} comparisons identical in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
