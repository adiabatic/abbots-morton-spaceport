"""The seeded settlement fuzz corpus (issue #43): windows the golden corpus cannot reach, written in the golden corpus's own `ams-m1-corpus/3` layout so one reader and one replay serve both piles.

The golden corpus next door samples the fixpoint's own rows, which is exactly its value and exactly its limit: every case it carries is a window the enumeration reached, so a Rust port that agrees with it has been proved right about reachable settlement and nothing else. The kernel's argument surface is far wider than its reachable one — a left committed at a seam the input rune has no acceptor row for, which is what the lookahead closure exists to make unreachable, a boundary left in front of a rune that only ever follows letters, UNKNOWN parked at the second slot where the enumeration always bakes a class — and the port has to answer those the same way too, because the guard, the liveness probes, and the prospect recursion all call the kernel with windows the table never enumerates. This generator draws from that wider surface directly: real (rune, stance) pairs and real declared heights, so a case is a call the kernel could plausibly receive, but combined without regard for whether the fixpoint would ever assemble them.

Determinism is the whole contract, twice over. Same seed and same spec means the same cases in the same order, so a divergence found on a large sweep can be reproduced on the spot by rerunning the seed; and the file is written through the golden corpus's own `write_corpus` — sorted, deduplicated, zeroed gzip stamp — so two runs at one HEAD are byte-identical and a Rust run and a Python run cannot silently disagree about which cases they ran. Raising cases are ordinary cases here, unlike in the golden corpus's settled arm where a raise means the reconstruction is broken: reaching the error paths on purpose is half the point of fuzzing a kernel whose refusals are as load-bearing as its answers.

The engine modes ride the head rather than the invocation because they are settlement semantics, not sampling: `simulated_prospect` off is the pinned candidacy world the port must reproduce as faithfully as the shipping default, and `vote_slots` off is the comparison state the deep-slot machinery is measured against. Each mode combination gets its own file, and `kernel_differential` reads the modes back out of the head to build the Rust engine with the same ones.

Run as: uv run python -m rebuild.tools.fuzz_settlement_corpus [--spec {live,mini}] [--configs CONFIG ...] [--cases N] [--seed N] [--simulated-prospect {0,1}] [--vote-slots {0,1}] [--out DIR]
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from rebuild.pipeline import conform, fixtures
from rebuild.pipeline.model import CellId, Height, ResolvedSpec, Settled
from rebuild.pipeline.run_m1 import REPO_ROOT
from rebuild.pipeline.settle import (
    BOUNDARY_KINDS,
    EDGE,
    NAMER_DOT,
    SPACE,
    UNKNOWN,
    ZWNJ,
    Engine,
    LeftContext,
    RightToken,
)
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.tools.export_settlement_corpus import _case_row, _replay, corpus_head, write_corpus

FUZZ_DIR = REPO_ROOT / "rebuild" / "out" / "kernel-fuzz"
DEFAULT_SEED = 40
DEFAULT_CASES = 400

# The non-letter right tokens, drawn as one pool: the four boundary kinds plus UNKNOWN, which is the one token no boundary walk produces and every optimistic lookahead depends on.
NON_LETTER_TOKENS = (EDGE, SPACE, ZWNJ, NAMER_DOT, UNKNOWN)
# One left in this many is a boundary rather than a letter, and one right slot in this many is a non-letter token. Letters are weighted heavily on purpose: a window whose slots are mostly boundaries exercises the early returns and little else, while the ranking, the prefers, and the prospect recursion only have work to do when there are letters to rank against.
BOUNDARY_LEFT_ODDS = 5
NON_LETTER_SLOT_ODDS = 5
# The extension a joining left carries. Real settled lefts rarely carry more, and the field is only read by the same-seam non-summing suppression, which cares whether it is zero and by how much rather than how large it can grow.
EXTENSIONS = (0, 1, 2)


def fuzz_path(out_dir: Path, config: str, simulated_prospect: bool, vote_slots: bool) -> Path:
    """One file per configuration and mode combination — the modes are in the name because a fuzz pile cut under the pinned candidacy world answers different questions from the shipping one and the two must never overwrite each other."""
    return Path(out_dir) / f"fuzz-{config}-sp{int(simulated_prospect)}-vs{int(vote_slots)}.ndjson.gz"


def stance_shapes(spec: ResolvedSpec) -> list[tuple[str, str, tuple[Height, ...], tuple[Height, ...]]]:
    """Every (rune, stance) pair the spec models, with that stance's declared entry and exit heights — the vocabulary a letter left is drawn from. Families are walked in sorted order rather than declaration order so the draw is a function of the alphabet rather than of the order `spec_load` happened to resolve it in."""
    shapes = []
    for name in sorted(spec.runes):
        for stance_name, stance in spec.runes[name].stances.items():
            shapes.append((name, stance_name, tuple(stance.surface.entries), tuple(stance.surface.exits)))
    return shapes


def _letter_left(
    rng: random.Random, shapes: list[tuple[str, str, tuple[Height, ...], tuple[Height, ...]]]
) -> LeftContext:
    """A settled letter left assembled from one real stance's own heights: the committed seam is a height that stance declares an exit at (or none, drawn at twice the weight of any single height, because the unjoined left is the common case the enumeration hands the kernel), the cell's entry is a height it declares an entry at, and the extension is only nonzero where there is a seam to carry it."""
    rune, stance, entries, exits = shapes[rng.randrange(len(shapes))]
    seam = rng.choice((None, None) + exits)
    entry = rng.choice((None,) + entries)
    extension = 0 if seam is None else rng.choice(EXTENSIONS)
    cell = CellId(rune=rune, stance=stance, entry=entry, exit=seam, adjustments=())
    return LeftContext("letter", Settled(cell=cell, seam=seam, extension=extension))


def _right_token(rng: random.Random, letters: list[str]) -> RightToken:
    if rng.randrange(NON_LETTER_SLOT_ODDS) == 0:
        return NON_LETTER_TOKENS[rng.randrange(len(NON_LETTER_TOKENS))]
    return RightToken("letter", letters[rng.randrange(len(letters))])


def fuzz_cases(spec: ResolvedSpec, engine: Engine, count: int, seed: int) -> list[dict]:
    """`count` windows drawn from one seeded stream and replayed through one shared engine, in draw order. Sharing the engine is deliberate rather than incidental: the fired-pointer delta a case carries is the delta its own evaluation journaled, and journaling is what makes that delta order-independent, so a warm engine and a cold one owe the same answer and the differential is run warm on both sides."""
    rng = random.Random(seed)
    shapes = stance_shapes(spec)
    letters = sorted(spec.runes)
    rows: list[dict] = []
    for _ in range(count):
        if rng.randrange(BOUNDARY_LEFT_ODDS) == 0:
            left = LeftContext(BOUNDARY_KINDS[rng.randrange(len(BOUNDARY_KINDS))])
        else:
            left = _letter_left(rng, shapes)
        family = letters[rng.randrange(len(letters))]
        rights = tuple(_right_token(rng, letters) for _ in range(4))
        result, _raised = _replay(engine, left, RightToken("letter", family), rights)
        rows.append(_case_row(left, family, rights, result))
    return rows


def fuzz_config(
    spec: ResolvedSpec,
    spec_name: str,
    config: str,
    out_dir: Path,
    count: int,
    seed: int,
    simulated_prospect: bool,
    vote_slots: bool,
) -> tuple[Path, int]:
    """One configuration's fuzz corpus, and the number of cases drawn for it. Fewer lines than that can land in the file: the layout deduplicates, and a small alphabet under a large count repeats windows."""
    features = conform.features_for_config(config)
    engine = Engine(
        spec,
        features,
        trace_memo=True,
        simulated_prospect=simulated_prospect,
        vote_slots=vote_slots,
    )
    cases = fuzz_cases(spec, engine, count, seed)
    head = corpus_head(spec, spec_name, config, engine)
    head["seed"] = seed
    head["cases"] = count
    path = fuzz_path(out_dir, config, simulated_prospect, vote_slots)
    write_corpus(head, cases, path)
    return path, len(cases)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate the seeded settlement fuzz corpus.")
    parser.add_argument("--out", type=Path, default=FUZZ_DIR, help="directory the corpus files land in")
    parser.add_argument(
        "--configs",
        nargs="+",
        default=None,
        help="feature configurations to fuzz (default: every acceptance configuration)",
    )
    parser.add_argument("--spec", choices=("live", "mini"), default="live", help="which spec to fuzz")
    parser.add_argument("--cases", type=int, default=DEFAULT_CASES, help="windows drawn per configuration")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="the seed the draw is a function of")
    parser.add_argument(
        "--simulated-prospect",
        type=int,
        choices=(0, 1),
        default=1,
        help="the issue-28 simulated transition (0 is the pinned candidacy world)",
    )
    parser.add_argument(
        "--vote-slots",
        type=int,
        choices=(0, 1),
        default=1,
        help="shifted follower-vote slots (0 pins everything past the vote's own right1 to UNKNOWN)",
    )
    args = parser.parse_args(argv)
    spec = fixtures.mini_spec() if args.spec == "mini" else load_default_spec()
    configs = tuple(args.configs) if args.configs is not None else conform.ACCEPTANCE_CONFIGS
    simulated_prospect = bool(args.simulated_prospect)
    vote_slots = bool(args.vote_slots)
    start = time.perf_counter()
    for config in configs:
        path, count = fuzz_config(
            spec,
            args.spec,
            config,
            args.out,
            args.cases,
            args.seed,
            simulated_prospect,
            vote_slots,
        )
        print(f"{config}: {count} fuzzed cases at seed {args.seed} -> {path}", flush=True)
    print(f"[t] fuzz_settlement_corpus_total {time.perf_counter() - start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
