"""The liveness-grain differential (issue #40, sub-issue #45): the deep-slot filters' verdicts and the class-grain fiber partitions, asked of Python and of the Rust kernel over one file of keys and compared as bytes.

This sits between the two harnesses beside it, at the grain neither of them can state. `kernel_differential` settles one window at a time and a liveness verdict is not a window's answer — it is a claim about a whole family of windows, that no deep token could ever move the seat — so no case line carries it. `kernel_fixpoint` compares whole enumerations, where a wrong verdict does surface, but as tens of thousands of rows that split where Python did not and a `#NA` where Python opened a slot, with no single window to point at and a diff whose first line is as arbitrary as its last. Asked directly, the same defect is one triple. The fiber partition is the same statement one grain finer — which r3 letters share an outcome record, and which r4 letters share one under them — and the class ids are content-addressed over exactly those member sets, so a fiber that differs by one member renames every `#C…` token in the stream.

Three key shapes, in the order the file carries them. The `3` sweep is exhaustive: the surface is cubic in an alphabet of a couple of dozen runes, so there is no sampling to argue about and no triple a port can be lucky about. The `4` sweep is exhaustive too at the alphabet the gate runs on, and that is a measured choice rather than a thorough-by-default one. Its surface is one alphabet larger, but the exhaustive third arm has already driven `fourth_live` across every concrete letter third through the belt, so the fourth arm is answering off a warm memo and asking all of it costs the run essentially nothing over asking a sample of it. What a sample does cost is the only keys that carry information: live fourth slots are a vanishing fraction of the quad space, so a draw sized for the space misses nearly all of them and can miss every one of them in a world where the mode flags leave few — which reads as a green arm that compared nothing but agreement about `dead`. The sampling path and `--count` stay for an alphabet where the exhaustive form stops being free, and `--seed` still fixes the draw. The `fibers` keys ride the `3` sweep's answers rather than a list of their own: a fiber is only defined for a live letter-letter context, so the third arm asks exactly where the first arm said live, which makes it a check on the first as well — a port that judged a context dead is never asked for its fibers, and the absent lines are the divergence.

An arm that compares nothing must not read as a passing gate, so two floors are checked at the end whether or not a kernel was asked: every world's third arm has to have answered something, and a spec whose deep worlds between them found no live context at all is reported and fails. Nothing is asserted about how many — the alphabet moves — only that the surface the sweep exists to compare was not empty.

Four worlds, `kernel_differential`'s mode combinations, because the two flags are what decide whether there is a liveness arm at all: with both off the filters are the own-rune chain census alone and every answer here is the pinned world's, and each single flag on exercises one probe arm without the other. One engine per world, built with explicit mode arguments rather than by moving the environment, and the filter closures and the fiber deriver all lent that one engine — the liveness memo, the trace memo and the fired journal are per engine, so a second engine would answer the same questions from a colder cache and, through `cited_provenance`, a different fixpoint. The verdicts are read through `third_slot_filter` / `fourth_slot_filter` and never through `_ProspectLiveness` directly, because the filter is where the chain arm and the liveness arm are ORed together, and the chain arm alone decides a great many triples that the probe would never be asked about.

The harness is wired before the verb it drives exists. A kernel that does not know `liveness-cases` exits 2 on the usage check, which this reads as the verb being absent and reports as one line, exactly as its siblings do. `--python-only` goes further and never invokes a binary at all: it writes the keys and Python's own answers into the output directory and stops, which is how the target is testable today. It is also half of the cross-build comparison: run it once for Python's answers, then run each candidate under `--binary <build> --out <that build's own directory>`, and every `kernel-*.txt` is comparable line for line against Python's `python-*.txt` and against every other build's. Nothing is shared between those runs but the keys, and the keys are a function of the spec, the seed and the world — so each run regenerates the identical file rather than depending on the others having left one behind.

Run as `uv run python -m rebuild.tools.kernel_liveness`, or through `make kernel-liveness`, which builds the binary first.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from rebuild.pipeline import conform, fixtures, kernel_io, table
from rebuild.pipeline.model import ResolvedSpec
from rebuild.pipeline.settle import Engine
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools import kernel_differential
from rebuild.tools.kernel_differential import Arm, KernelVerbMissing, compare_lines

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "rebuild" / "kernel-rs" / "target" / "release" / "ams-m1-kernel"
LIVENESS_DIR = ROOT / "rebuild" / "out" / "kernel-liveness"
# The sub-issue's own number, following the fuzz corpus's convention of seeding with the issue it was cut for. Nothing depends on the value beyond its being fixed: the whole point is that two runs draw the same quads, here and in whatever build answers them next.
DEFAULT_SEED = 45
DEFAULT_QUADS = 20000
# The three key shapes, in the order the keys file carries them, which is also the order the arms report in. `fibers` is last because it is the only shape whose keys are a function of an earlier shape's answers.
ARMS = ("triple", "quad", "fiber")


@dataclass(frozen=True)
class World:
    """One mode combination: the two engine flags, the kernel flags that spell them, and whether there is a deep world here at all — with both flags off no engine grows a `_ProspectLiveness`, so there are no fibers to derive and the filters answer from the chain census alone."""

    simulated_prospect: bool
    vote_slots: bool

    @property
    def deep(self) -> bool:
        return self.simulated_prospect or self.vote_slots

    @property
    def label(self) -> str:
        return f"sp{int(self.simulated_prospect)}vs{int(self.vote_slots)}"

    def flags(self, config: str) -> list[str]:
        """The `liveness-cases` flags this world and configuration call for: the active stylistic sets, and a flag per mode that is off. An empty feature set passes no flag at all rather than an empty value, matching the CLI's own refusal of `--features=`."""
        features = sorted(conform.features_for_config(config))
        flags = [f"--features={','.join(features)}"] if features else []
        if not self.simulated_prospect:
            flags.append("--candidacy-prospect")
        if not self.vote_slots:
            flags.append("--vote-slots-off")
        return flags


WORLDS = tuple(
    World(simulated_prospect, vote_slots)
    for simulated_prospect, vote_slots in kernel_differential.MODE_COMBINATIONS
)


@dataclass
class Sweep:
    """One world's whole file: the keys the kernel is handed and the answer lines Python owes for them, plus how many keys each shape contributed, which is what lets the comparison report per arm without a second invocation."""

    keys: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ARMS, 0))

    def note(self, arm: str, key: str, answer: str) -> None:
        self.keys.append(key)
        self.answers.append(f"{key}\t{answer}")
        self.counts[arm] += 1


def triple_keys(spec: ResolvedSpec) -> list[tuple[str, str, str]]:
    """Every letter triple, in sorted rune order so the file is a function of the spec alone. Ligatures ride as ordinary letters at every slot: the filters key on family names and a formed ligature is one."""
    names = sorted(spec.runes)
    return [(input_family, right1, right2) for input_family in names for right1 in names for right2 in names]


def quad_keys(spec: ResolvedSpec, count: int, seed: int, exhaustive: bool) -> list[tuple[str, str, str, str]]:
    """The sampled fourth-slot keys: `count` quads drawn without replacement from the whole quad space by `random.Random(seed)`, or all of it when `--exhaustive` asks or when the alphabet is small enough that the sample would be the population anyway (the mini fixture). The draw is sorted back into index order afterwards, so a sampled file walks the space in the same direction an exhaustive one does and the two forms warm the memos alike; the sampling is still without replacement, and the seed still fixes which quads."""
    names = sorted(spec.runes)
    size = len(names)
    population = size**4
    if exhaustive or count >= population:
        drawn = range(population)
    else:
        drawn = sorted(random.Random(seed).sample(range(population), count))
    return [
        (
            names[index // size**3 % size],
            names[index // size**2 % size],
            names[index // size % size],
            names[index % size],
        )
        for index in drawn
    ]


def fiber_answer(context: table._ContextFibers) -> str:
    """One context's fiber partition as the `fibers` answer spells it: compact JSON, keys in the order written here, every token through `table._right_token_label`. Everything rides the deriver's own order — boundary options in static-list order, fibers in first-member-encountered order, members as collected, `r4_groups` in option-pipeline order — because the order is what the class ids and the row stream are cut from, and a partition compared as a set would call two different tables equal."""
    label = table._right_token_label
    return json.dumps(
        {
            "boundaries": [label(token) for token in context.boundary_options],
            "fibers": [
                {
                    "members": [label(member) for member in fiber.members],
                    "fourth_matters": fiber.fourth_matters,
                    "r4_groups": [[label(token) for token in group] for group in fiber.r4_groups],
                }
                for fiber in context.fibers
            ],
        },
        separators=(",", ":"),
    )


def sweep_python(
    spec: ResolvedSpec,
    features: frozenset[str],
    world: World,
    quads: list[tuple[str, str, str, str]],
    fiber_cap: int | None,
) -> Sweep:
    """Python's whole side of one world: the keys and the answers, from one engine and the filter closures lent it. The engine carries `trace_memo` because the build's does and the probes' traces land in it either way; the liveness instance is fetched through `table._liveness_probe`, which is the same instance the filters built, so the fiber deriver reads the memo the verdicts filled rather than a second one. `fiber_cap` clips the fiber arm for an iteration loop; None asks for every live context."""
    engine = Engine(
        spec,
        features,
        trace_memo=True,
        simulated_prospect=world.simulated_prospect,
        vote_slots=world.vote_slots,
    )
    third_slot_matters = table.third_slot_filter(spec, features, engine)
    fourth_slot_matters = table.fourth_slot_filter(spec, features, engine)
    sweep = Sweep()
    live: list[tuple[str, str, str]] = []
    for input_family, right1, right2 in triple_keys(spec):
        verdict = third_slot_matters(input_family, right1, right2)
        sweep.note("triple", f"3\t{input_family}\t{right1}\t{right2}", "live" if verdict else "dead")
        if verdict:
            live.append((input_family, right1, right2))
    for input_family, right1, right2, right3 in quads:
        verdict = fourth_slot_matters(input_family, right1, right2, right3)
        sweep.note("quad", f"4\t{input_family}\t{right1}\t{right2}\t{right3}", "live" if verdict else "dead")
    if world.deep:
        deriver = table._DeepFiberDeriver(
            spec,
            engine,
            table._WindowOptions(spec),
            table._liveness_probe(spec, engine),
            fourth_slot_matters,
        )
        for input_family, right1, right2 in live if fiber_cap is None else live[:fiber_cap]:
            context = deriver.context(input_family, right1, right2)
            sweep.note("fiber", f"fibers\t{input_family}\t{right1}\t{right2}", fiber_answer(context))
    return sweep


def write_lines(lines: list[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines))
    return path


def binary_label(binary: Path) -> str:
    """The kernel's path as the report names it: repo-relative for a binary in the tree, absolute for anything else, so pointing the harness at a candidate build elsewhere names it instead of failing on the path arithmetic."""
    try:
        return str(binary.relative_to(ROOT))
    except ValueError:
        return str(binary)


def kernel_answers(binary: Path, spec_path: Path, keys_path: Path, world: World, config: str) -> list[str]:
    """The kernel's answer lines for one keys file, through the sibling harness's own invocation contract — exit 2 is the usage check and reads as the verb being absent, any other nonzero exit is the kernel complaining about its inputs, and chatter on stderr after a clean exit is a contract break."""
    arguments = [
        str(binary),
        "liveness-cases",
        str(spec_path),
        str(keys_path),
        *world.flags(config),
    ]
    return kernel_differential._kernel(arguments, "liveness-cases")


def compare_sweep(label: str, sweep: Sweep, got: list[str], arms: dict[str, Arm]) -> int:
    """Python's answers against the kernel's, sliced by key shape so a divergence is reported under the arm that asked for it. The last arm takes the whole tail rather than its own count, so a kernel that answered more lines than it was asked is caught there instead of silently ignored."""
    total = 0
    start = 0
    for index, arm in enumerate(ARMS):
        stop = start + sweep.counts[arm]
        expected = sweep.answers[start:stop]
        answered = got[start:] if index == len(ARMS) - 1 else got[start:stop]
        divergences = compare_lines(f"{label} {arm}", expected, answered)
        arms[arm].note(len(expected), divergences)
        total += divergences
        start = stop
    return total


def run_world(
    spec: ResolvedSpec,
    spec_name: str,
    spec_path: Path,
    config: str,
    world: World,
    quads: list[tuple[str, str, str, str]],
    fiber_cap: int | None,
    out_dir: Path,
    arms: dict[str, Arm],
    binary: Path | None,
) -> tuple[int, Sweep]:
    """One (spec, configuration, world) swept, written out, and compared — or, with no binary to ask, swept and written alone. The keys and both sides' answers persist under the output directory rather than in a scratch that vanishes, because the same keys file is what the next candidate build is handed and the answer files are what get cross-diffed. The sweep comes back beside the divergence count so the caller can hold the run to its floors."""
    label = f"{spec_name} {config} {world.label}"
    stem = f"{spec_name}-{config}-{world.label}"
    start = time.perf_counter()
    sweep = sweep_python(spec, conform.features_for_config(config), world, quads, fiber_cap)
    keys_path = write_lines(sweep.keys, out_dir / f"keys-{stem}.txt")
    write_lines(sweep.answers, out_dir / f"python-{stem}.txt")
    tally = "  ".join(f"{sweep.counts[arm]:6d} {arm}" for arm in ARMS)
    if binary is None:
        for arm in ARMS:
            arms[arm].note(sweep.counts[arm], 0)
        print(f"  {label:>26}  {tally}  {time.perf_counter() - start:6.1f}s  PYTHON", flush=True)
        return 0, sweep
    got = kernel_answers(binary, spec_path, keys_path, world, config)
    write_lines(got, out_dir / f"kernel-{stem}.txt")
    divergences = compare_sweep(label, sweep, got, arms)
    print(
        f"  {label:>26}  {tally}  {time.perf_counter() - start:6.1f}s  {'OK' if not divergences else 'FAIL'}",
        flush=True,
    )
    return divergences, sweep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Differentially test the Rust kernel's deep-slot liveness verdicts and fiber partitions against Python."
    )
    parser.add_argument(
        "--specs",
        nargs="+",
        choices=("mini", "live"),
        default=["mini", "live"],
        help="which specs to sweep, cheapest first (default: both)",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        default=["default"],
        help="feature configurations to sweep (default: the default configuration alone; the sweep is exhaustive at the third slot and every configuration multiplies it by four worlds)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_QUADS,
        help="fourth-slot keys drawn per spec, when the whole quad space is not being asked",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="the seed the fourth-slot draw is a function of"
    )
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="ask every quad rather than a sample of them, which is what `make kernel-liveness` does",
    )
    parser.add_argument(
        "--fiber-cap",
        type=int,
        default=0,
        help="stop the fiber arm after this many live contexts (default 0, meaning every one of them)",
    )
    parser.add_argument(
        "--out", type=Path, default=LIVENESS_DIR, help="directory the keys and both sides' answers land in"
    )
    parser.add_argument(
        "--binary", type=Path, default=None, help="the kernel to ask (default: the release build in the tree)"
    )
    parser.add_argument(
        "--python-only",
        action="store_true",
        help="write the keys and Python's answers and stop, without invoking any kernel",
    )
    args = parser.parse_args(argv)
    if args.python_only and args.binary is not None:
        parser.error("--python-only asks no kernel anything; drop it or drop --binary")
    binary = None if args.python_only else (args.binary if args.binary is not None else BINARY)
    if binary is not None and not binary.is_file():
        print(
            f"kernel liveness: no kernel binary at {binary_label(binary)} — run `make kernel-build` first",
            file=sys.stderr,
        )
        return 1
    specs = [(name, fixtures.mini_spec() if name == "mini" else load_default_spec()) for name in args.specs]
    fiber_cap = args.fiber_cap if args.fiber_cap > 0 else None
    arms = {arm: Arm(arm) for arm in ARMS}
    divergences = 0
    start = time.perf_counter()
    against = "Python alone" if binary is None else binary_label(binary)
    print(f"kernel liveness: {', '.join(name for name, _ in specs)} against {against}", flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    floor: list[str] = []
    try:
        for name, spec in specs:
            spec_path = args.out / f"spec-{name}.json"
            kernel_io.write_spec(spec, spec_path)
            quads = quad_keys(spec, args.count, args.seed, args.exhaustive)
            print(f"{name}: {len(spec.runes)} runes, {len(quads)} quads drawn", flush=True)
            deep_worlds = 0
            fibers = 0
            for config in args.configs:
                for world in WORLDS:
                    world_divergences, sweep = run_world(
                        spec,
                        name,
                        spec_path,
                        config,
                        world,
                        quads,
                        fiber_cap,
                        args.out,
                        arms,
                        binary,
                    )
                    divergences += world_divergences
                    if not sweep.counts["triple"]:
                        floor.append(f"{name} {config} {world.label}: the third-slot arm asked nothing")
                    if world.deep:
                        deep_worlds += 1
                        fibers += sweep.counts["fiber"]
            if deep_worlds and not fibers:
                floor.append(
                    f"{name}: no deep world found a live context, so the fiber arm asked nothing and the class-grain partition went uncompared"
                )
    except KernelVerbMissing as missing:
        print(f"kernel liveness: {missing}", file=sys.stderr)
        return 1
    except RuntimeError as complaint:
        print(f"kernel liveness: {complaint}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - start
    counted = [arm for arm in arms.values() if arm.compared or arm.divergences]
    tally = ", ".join(f"{arm.compared} {arm.label}" for arm in counted) or "nothing"
    for complaint in floor:
        print(f"kernel liveness: {complaint}", file=sys.stderr)
    if divergences:
        print(f"kernel liveness: {divergences} divergences over {tally} answers in {elapsed:.1f}s")
        return 1
    if floor:
        print(f"kernel liveness: an arm compared nothing, which is not a pass — {tally} in {elapsed:.1f}s")
        return 1
    if binary is None:
        print(f"kernel liveness: {tally} answers written to {args.out} in {elapsed:.1f}s")
        return 0
    print(f"kernel liveness: {tally} answers identical in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
