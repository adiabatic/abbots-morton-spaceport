"""The settlement differential (issue #40, sub-issue #43): every window the Python kernel answers, answered again by the Rust kernel, compared as bytes.

Byte identity of a whole result line is the assertion, and it is deliberately stronger than comparing settled cells. A case line carries the whole trace: the settled triple, the prospect, the joint floor, the provenance notes, and the fired-pointer delta the evaluation journaled, and then the stage the decision fell out at, the runner-up, the ranked ladder, and every elimination. So a port that lands on the right cell by the wrong route — a refusal that never fired, a note that came from a different record, a prospect that agreed by luck at this window, a candidate eliminated two stages late, a tie broken at the floor that Python broke at the prefers — diverges here rather than several sub-issues downstream where the dead-policy gate reads live records as dead. `rebuild/pipeline/settle.py` and `rebuild/pipeline/specificity.py` are the oracle in the strict sense: where the two sides disagree, Python is right by definition and the Rust side is what moves.

Three arms, cheapest first, because a port that is wrong is usually wrong everywhere and the fast arms say so in seconds. The guard arm sweeps the section 5.7 late-formation verdict over every (ligature, right1, right2) triple — a pure function of two raw slots, so the whole surface is enumerable and there is no sampling to argue about. The fuzz arm draws seeded windows from the kernel's argument surface rather than its reachable one, in each mode combination the port has to reproduce: the shipping defaults, the pinned candidacy world (`simulated_prospect` off), the unshifted-vote comparison state (`vote_slots` off), and both of those off together, which is what the late-formation guard builds its own engines with. The golden-corpus arm is last because it is the expensive one — each configuration's export runs a full fixpoint first — and it is also the only arm whose cases are certified reachable, which is why `--skip-corpus` exists and why the other two arms never touch the enumeration.

The Rust side is run engine-warm, one engine per corpus file replaying its cases in file order, exactly as the Python exporter cut them. That is not a shortcut around a cold-start comparison: the fired delta a case carries is journaled per evaluation and replayed on every cache hit precisely so it stays order-independent, so warm and cold owe the same answer and any disagreement between them is itself a defect the crate's own tests catch.

The harness is wired before the verbs it drives exist. A kernel that does not know `settle-cases` or `guard-sweep` exits 2 on the usage check, which this reads as the verb being absent and reports as such — one clean failure line rather than a diff against empty output, so the make target is testable from the day it lands.

Run as `uv run python -m rebuild.tools.kernel_differential`, or through `make kernel-differential`, which builds the binary first.
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from rebuild.pipeline import conform, fixtures, kernel_io, settle
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, UNKNOWN, ZWNJ, RightToken
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import export_settlement_corpus, fuzz_settlement_corpus

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
TIMEOUT = 1800
# How many divergent lines get the full case-plus-field treatment before the rest are counted silently. A port with a systematic defect diverges on most of its cases and the first few say everything the hundredth would.
REPORTED_DIVERGENCES = 3
DEFAULT_CASES = 400
# Every mode combination the port has to reproduce: the shipping defaults, the pinned candidacy world, the unshifted-vote comparison state, and both flags off at once. That last one ships nowhere, but it is what `_guard_state` pins its own engines to (bar their EDGE deep-slot pin, which no verb exposes), and while it was left out it was the one engine configuration no arm here ever built — the two single-flag runs separate the mechanisms without ever crossing them.
MODE_COMBINATIONS = ((True, True), (False, True), (True, False), (False, False))
GUARD_TAIL_TOKENS = (
    ("edge", EDGE),
    ("space", SPACE),
    ("zwnj", ZWNJ),
    ("namer-dot", NAMER_DOT),
    ("unknown", UNKNOWN),
)


class KernelVerbMissing(Exception):
    """The binary rejected the verb outright — it predates this stage of the port. Distinct from a divergence, which is a kernel that answered and answered wrong."""


@dataclass
class Arm:
    """One arm's running tally: how many lines were compared and how many of them disagreed."""

    label: str
    compared: int = 0
    divergences: int = 0

    def note(self, compared: int, divergences: int) -> None:
        self.compared += compared
        self.divergences += divergences


def _binary_label() -> str:
    """The kernel's path as the report names it: repo-relative for the built binary, absolute for anything else, so pointing the harness at a binary outside the tree names it instead of failing on the path arithmetic."""
    try:
        return str(BINARY.relative_to(ROOT))
    except ValueError:
        return str(BINARY)


def _run(arguments: list[str]) -> tuple[list[str], str, int | None]:
    """One kernel invocation: its stdout as lines, its stderr, and its exit status — None when it gave no answer within `TIMEOUT` seconds, so a wedged engine reads as a failing arm rather than a hung build."""
    try:
        finished = subprocess.run(arguments, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return [], f"no answer within {TIMEOUT} seconds", None
    stdout = finished.stdout.decode(errors="replace")
    lines = stdout.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines, finished.stderr.decode(errors="replace").strip(), finished.returncode


def _kernel(arguments: list[str], verb: str) -> list[str]:
    """The kernel's stdout lines, with the two failure shapes the CLI contract distinguishes turned into exceptions: exit 2 is the usage check, and any other nonzero exit is the kernel complaining about its inputs. Exit 2 reads as the verb being absent because that is the only way a well-formed invocation reaches it — and once the verb exists it can only mean the flags this harness sends and the flags the CLI accepts have drifted apart, which is why the whole invocation rides the complaint."""
    lines, errors, status = _run(arguments)
    if status == 2:
        raise KernelVerbMissing(
            f"kernel does not support {verb} yet, or rejected the invocation as a usage error: {errors} ({' '.join(arguments)})"
        )
    if status != 0:
        exited = "gave no answer" if status is None else f"exited {status}"
        raise RuntimeError(f"the kernel {exited} on {verb}: {errors}")
    if errors:
        raise RuntimeError(f"the kernel wrote to stderr on a clean {verb} exit: {errors}")
    return lines


def read_case_lines(path: Path) -> tuple[dict, str, list[str]]:
    """A corpus file's head, its marker line verbatim, and its case lines verbatim. The lines come back as text rather than parsed objects because the comparison is against these exact bytes; `export_settlement_corpus.read_corpus` remains the one place the format marker is checked, and this reads the same file through it first so a file of another format is refused here too."""
    head, _cases = export_settlement_corpus.read_corpus(path)
    with gzip.open(path, "rt") as handle:
        marker = handle.readline().rstrip("\n")
        lines = [line.rstrip("\n") for line in handle]
    return head, marker, lines


def _plain_copy(marker: str, lines: list[str], path: Path) -> Path:
    """The gunzipped form the `settle-cases` verb reads: the head line the kernel skips, then the cases. Decompression stays on this side on purpose — the crate carries serde_json and nothing else."""
    path.write_text("\n".join([marker, *lines]) + "\n")
    return path


def _field_notes(expected: str, got: str) -> list[str]:
    """Where two case lines disagree, in the terms the port is debugged in. Falls back to nothing when the kernel's line is not JSON at all, since the raw lines are printed beside this anyway."""
    try:
        left = json.loads(expected)
        right = json.loads(got)
    except ValueError:
        return []
    if not isinstance(left, dict) or not isinstance(right, dict):
        return []
    notes = []
    for key in ("left", "input", "right"):
        if left.get(key) != right.get(key):
            notes.append(f"the kernel re-emitted a different {key}")
    expected_result = left.get("result", {})
    got_result = right.get("result", {})
    if isinstance(expected_result, dict) and isinstance(got_result, dict):
        for key in sorted(set(expected_result) | set(got_result)):
            if expected_result.get(key) != got_result.get(key):
                notes.append(
                    f"result.{key}: python {expected_result.get(key)!r}, kernel {got_result.get(key)!r}"
                )
    return notes


def compare_lines(label: str, expected: list[str], got: list[str]) -> int:
    """Two line lists compared as ordered bytes, with the first few disagreements printed in full. Returns the number of divergences, counting a length mismatch as one."""
    divergences = 0
    for index, (want, have) in enumerate(zip(expected, got)):
        if want == have:
            continue
        divergences += 1
        if divergences > REPORTED_DIVERGENCES:
            continue
        print(f"    {label} line {index + 1} diverged", flush=True)
        print(f"      python: {want}", flush=True)
        print(f"      kernel: {have}", flush=True)
        for note in _field_notes(want, have):
            print(f"      {note}", flush=True)
    if len(expected) != len(got):
        divergences += 1
        print(f"    {label}: python wrote {len(expected)} lines, the kernel wrote {len(got)}", flush=True)
    return divergences


def _replay_flags(head: dict) -> list[str]:
    """The `settle-cases` flags one corpus's head calls for: the configuration's features, and a flag per mode that is off. An empty feature set passes no flag at all rather than an empty value, so the default configuration's invocation carries no `--features` at all."""
    features = sorted(conform.features_for_config(head["config"]))
    simulated_prospect, vote_slots = export_settlement_corpus.head_modes(head)
    flags = []
    if features:
        flags.append(f"--features={','.join(features)}")
    if not simulated_prospect:
        flags.append("--candidacy-prospect")
    if not vote_slots:
        flags.append("--vote-slots-off")
    return flags


def replay_corpus(spec_path: Path, corpus_file: Path, scratch: Path, label: str) -> tuple[int, int]:
    """One corpus file replayed through the kernel and compared line for line. Returns (cases compared, divergences)."""
    head, marker, lines = read_case_lines(corpus_file)
    plain = _plain_copy(marker, lines, scratch / f"cases-{label.replace(' ', '-')}.ndjson")
    arguments = [str(BINARY), "settle-cases", str(spec_path), str(plain), *_replay_flags(head)]
    got = _kernel(arguments, "settle-cases")
    divergences = compare_lines(label, lines, got)
    print(f"  {label:>34}  {len(lines):6d} cases  {'OK' if not divergences else 'FAIL'}", flush=True)
    return len(lines), divergences


def guard_lines(spec: ResolvedSpec) -> list[str]:
    """The Python side of the late-formation sweep, in the order and the spelling the `guard-sweep` verb prints: every ligature in sorted-name order, every modeled letter at the first raw slot, and every modeled letter followed by the boundary kinds and UNKNOWN at the second. `right1` is letters only because a non-letter first slot short-circuits the verdict to free before any engine runs."""
    letters = sorted(spec.runes)
    ligatures = sorted(name for name, rune in spec.runes.items() if rune.sequence)
    right2_tokens = [(name, RightToken("letter", name)) for name in letters] + list(GUARD_TAIL_TOKENS)
    lines = []
    for liga in ligatures:
        for right1 in letters:
            token1 = RightToken("letter", right1)
            for label, token2 in right2_tokens:
                blocked = settle.formation_blocked(spec, liga, token1, token2)
                lines.append(f"{liga}\t{right1}\t{label}\t{'blocked' if blocked else 'free'}")
    return lines


def run_guard(spec: ResolvedSpec, spec_path: Path, label: str) -> tuple[int, int]:
    expected = guard_lines(spec)
    got = _kernel([str(BINARY), "guard-sweep", str(spec_path)], "guard-sweep")
    divergences = compare_lines(label, expected, got)
    print(f"  {label:>34}  {len(expected):6d} verdicts  {'OK' if not divergences else 'FAIL'}", flush=True)
    return len(expected), divergences


def run_fuzz(
    spec: ResolvedSpec,
    spec_name: str,
    spec_path: Path,
    configs: tuple[str, ...],
    cases: int,
    seed: int,
    scratch: Path,
    arm: Arm,
) -> None:
    out_dir = scratch / f"fuzz-{spec_name}"
    for config in configs:
        for simulated_prospect, vote_slots in MODE_COMBINATIONS:
            path, _count = fuzz_settlement_corpus.fuzz_config(
                spec, spec_name, config, out_dir, cases, seed, simulated_prospect, vote_slots
            )
            label = f"{spec_name} {config} sp{int(simulated_prospect)}vs{int(vote_slots)}"
            arm.note(*replay_corpus(spec_path, path, scratch, label))


def run_corpus(
    spec: ResolvedSpec,
    spec_name: str,
    spec_path: Path,
    configs: tuple[str, ...],
    scratch: Path,
    arm: Arm,
) -> None:
    out_dir = scratch / f"corpus-{spec_name}"
    for config in configs:
        path, _settled, _raising = export_settlement_corpus.export_config(
            spec,
            spec_name,
            config,
            out_dir,
            export_settlement_corpus.DEFAULT_PER_GROUP,
            export_settlement_corpus.DEFAULT_PER_FAMILY,
        )
        arm.note(*replay_corpus(spec_path, path, scratch, f"{spec_name} {config}"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Differentially test the Rust settlement kernel against Python."
    )
    parser.add_argument(
        "--specs",
        nargs="+",
        choices=("mini", "live"),
        default=["mini", "live"],
        help="which specs to compare over, cheapest first (default: both)",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        default=None,
        help="feature configurations to compare (default: every acceptance configuration; the mini spec runs at default only)",
    )
    parser.add_argument(
        "--cases", type=int, default=DEFAULT_CASES, help="fuzzed windows per configuration and mode"
    )
    parser.add_argument("--seed", type=int, default=fuzz_settlement_corpus.DEFAULT_SEED, help="the fuzz seed")
    parser.add_argument(
        "--skip-corpus", action="store_true", help="skip the golden-corpus arm and its fixpoints"
    )
    parser.add_argument("--skip-fuzz", action="store_true", help="skip the seeded fuzz arm")
    parser.add_argument("--skip-guard", action="store_true", help="skip the late-formation guard sweep")
    args = parser.parse_args(argv)
    if not BINARY.is_file():
        print(
            f"kernel differential: no kernel binary at {_binary_label()} — run `make kernel-build` first",
            file=sys.stderr,
        )
        return 1
    configs = tuple(args.configs) if args.configs is not None else conform.ACCEPTANCE_CONFIGS
    specs = [(name, fixtures.mini_spec() if name == "mini" else load_default_spec()) for name in args.specs]
    guard = Arm("guard")
    fuzz = Arm("fuzz")
    corpus = Arm("corpus")
    start = time.perf_counter()
    print(
        f"kernel differential: {', '.join(name for name, _ in specs)} against {_binary_label()}", flush=True
    )
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        paths = {}
        for name, spec in specs:
            paths[name] = scratch / f"spec-{name}.json"
            kernel_io.write_spec(spec, paths[name])
        try:
            if not args.skip_guard:
                print("guard sweep", flush=True)
                for name, spec in specs:
                    guard.note(*run_guard(spec, paths[name], name))
            if not args.skip_fuzz:
                print("fuzz", flush=True)
                for name, spec in specs:
                    run_fuzz(
                        spec,
                        name,
                        paths[name],
                        ("default",) if name == "mini" else configs,
                        args.cases,
                        args.seed,
                        scratch,
                        fuzz,
                    )
            if not args.skip_corpus:
                print("golden corpus", flush=True)
                for name, spec in specs:
                    run_corpus(
                        spec, name, paths[name], ("default",) if name == "mini" else configs, scratch, corpus
                    )
        except KernelVerbMissing as missing:
            print(f"kernel differential: {missing}", file=sys.stderr)
            return 1
        except RuntimeError as complaint:
            print(f"kernel differential: {complaint}", file=sys.stderr)
            return 1
    elapsed = time.perf_counter() - start
    arms = [arm for arm in (guard, fuzz, corpus) if arm.compared or arm.divergences]
    tally = ", ".join(f"{arm.compared} {arm.label}" for arm in arms) or "nothing"
    divergences = sum(arm.divergences for arm in arms)
    if divergences:
        print(f"kernel differential: {divergences} divergences over {tally} lines in {elapsed:.1f}s")
        return 1
    print(f"kernel differential: {tally} lines identical in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
