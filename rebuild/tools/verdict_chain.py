"""The cycle's verdict plumbing as one process: carry, merge, echo fill, standing fill, their merges, the echo pass that witnesses the fixpoint, and the complaint docket, over one copy of the surface's unit index.

Every one of those steps reaches a few slim fields per unit, and each run as its own `uv run` subprocess would parse the whole of the unit shards to get at them — gigabytes per step, most of the chain's wall time in parsing. They read the index sidecar instead (rebuild/review/unit_index), and reading it once and handing it down is the whole reason this module exists. The steps themselves are the tools' own: each tool's `main` does its own work and writes its own file, so `rebuild/tools/complaint_docket.py` and the rest are runnable on their own for the sitting-prep targets in the Makefile. The standing fill runs in its `--open-only --require-reach` form here. The narrowing is what it writes from — the blanks and the units verdicted outside the accepting set are the only units that can move a fill or a warning — while the reach check is a reading of the surface rather than of the queue, taken over the whole human domain against a blank store, and it is a refusal: a checked-in rule that reaches no window on this surface fails this step, which turns the plumbing red and `make verdict-ready` NOT READY, until the rule is deleted or the form it waits for migrates. The fill also gets its memo (`--standing-memo`, beside the surface directory by default), so a pass whose surface moved evaluates only the units whose content moved; `--fresh-standing-memo` is the cycle's `--fresh` reaching it.

The chain opens each step with `rebuild.tools.console`'s `[phase] <step>` line and closes it with `[t] <step> <secs>s`, so the cycle surfaces the step it is on and the duration it took exactly as it does for every other child, and the cycle-timings journal reads what it always did. The `[chain] ` prefix is left to the two lines that are results rather than phases — the fixpoint witness and a step's failure — which is what still delimits the sections the driver reads its per-step report out of.

The echo pass runs twice. Standing fills can make an echo group unanimous and leave a blank sibling, so a chain that ran echo once would have to refuse the plumbing green whenever the standing merge moved anything and hand the cascade to the next cycle; holding the index in memory makes a second echo pass cost a second, so the chain closes the cascade itself and the green is witnessed — "a re-run would write nothing" — rather than inferred from an ordering argument. The echo-fill file holds every echo fill the chain landed, later rounds included, so the artifact and the store still say the same thing.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections.abc import Callable

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.review import unit_index  # noqa: E402
from rebuild.tools import (  # noqa: E402
    carry_verdicts,
    complaint_docket,
    console,
    echo_verdicts,
    merge_verdicts,
    standing_verdicts,
)

SURFACE = ROOT / "rebuild/out/review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
ECHO_FILL = ROOT / "verdicts-echo-fill.json"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
# Two rounds close the cascade by construction — standing can only feed echo, and an echo fill only ever removes blanks — so a third is the belt to that argument's braces and a fourth would mean the argument is wrong.
MAX_ECHO_ROUNDS = 4


def _run(name: str, call: Callable[[], int | None]) -> int:
    """One step, opened as a phase and timed. A tool that fails by `SystemExit` — which is how the stamp guards and the rules-file validation refuse — reports its message and its code here rather than taking the whole chain down, so the steps after it can be reported as not run."""
    console.phase(name)
    started = time.perf_counter()
    try:
        code = call() or 0
    except SystemExit as exit_:
        code = exit_.code
        if isinstance(code, str):
            print(code, file=sys.stderr, flush=True)
            code = 1
        code = int(code or 0)
    print(f"[t] {name} {time.perf_counter() - started:.1f}s", flush=True)
    if code:
        print(f"{console.FAILED_LINE}{name} (exit {code})", flush=True)
    return code


def _write_fills(path: pathlib.Path, stamp: str, fills: list[dict]) -> None:
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": sorted(fills, key=lambda record: record["unit"]),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _merge(
    name: str, path: pathlib.Path, *, autosave: pathlib.Path, surface: pathlib.Path, journal: pathlib.Path
) -> int:
    return _run(
        name,
        lambda: merge_verdicts.main(
            [
                str(path),
                "--autosave",
                str(autosave),
                "--surface",
                str(surface),
                "--journal",
                str(journal),
            ]
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the artifact cycle's verdict plumbing in one process over one copy of the unit index."
    )
    parser.add_argument("--surface", type=pathlib.Path, default=SURFACE)
    parser.add_argument(
        "--source",
        nargs=2,
        action="append",
        default=[],
        metavar=("SURFACE_DIR", "VERDICTS_JSON"),
        help="a prior surface and the verdicts recorded against it, for the carry; repeatable",
    )
    parser.add_argument("--carry-out", type=pathlib.Path, help="where the carried verdicts are written")
    parser.add_argument(
        "--merge-master",
        type=pathlib.Path,
        help="merge this verdicts master directly instead of carrying: the form for a pass whose surface did not move, where the carry is provably the identity and the master is the one input the autosave's hash cannot see",
    )
    parser.add_argument("--autosave", type=pathlib.Path, default=AUTOSAVE)
    parser.add_argument("--journal", type=pathlib.Path, default=merge_verdicts.JOURNAL)
    parser.add_argument("--echo-out", type=pathlib.Path, default=ECHO_FILL)
    parser.add_argument("--standing-out", type=pathlib.Path, default=STANDING_FILL)
    parser.add_argument("--rules", type=pathlib.Path, default=standing_verdicts.RULES)
    parser.add_argument(
        "--standing-memo",
        type=pathlib.Path,
        help=f"where the standing fill keeps its per-unit decisions across passes; defaults to {standing_verdicts.MEMO_NAME} beside the surface directory, outside it, so a surface rebuild never clears it and the pre-pass snapshot never copies it",
    )
    parser.add_argument(
        "--fresh-standing-memo",
        action="store_true",
        help="have the standing fill evaluate every unit regardless of its memo, and rewrite it (the cycle's --fresh)",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="carry only: never write the live store, and run neither fill nor the docket (the rehearsal form)",
    )
    parser.add_argument(
        "--no-complaints", action="store_true", help="skip the complaint docket at the end of the chain"
    )
    parser.add_argument("--complaints-out", type=pathlib.Path, default=complaint_docket.DATA_OUT)
    args = parser.parse_args(argv)

    surface = args.surface
    started = time.perf_counter()
    units = unit_index.load_units(surface)
    print(f"[t] index {time.perf_counter() - started:.1f}s\t({len(units)} units)", flush=True)
    stamp = json.loads((surface / "manifest.json").read_text())["generated_at"]

    if args.source:
        if args.carry_out is None:
            parser.error("--source needs --carry-out")
        carry_argv: list[str] = []
        for source in args.source:
            carry_argv += ["--source", *source]
        carry_argv += ["--out", str(args.carry_out), "--current-surface", str(surface)]
        code = _run("carry", lambda: carry_verdicts.main(carry_argv, current_units=units))
        if code:
            return code
    if args.no_merge:
        return 0

    to_merge = args.carry_out if args.source else args.merge_master
    if to_merge is not None:
        code = _merge("merge", to_merge, autosave=args.autosave, surface=surface, journal=args.journal)
        if code:
            return code

    echo_argv = [str(args.autosave), "--surface", str(surface), "--out", str(args.echo_out)]
    fills: list[dict] = []
    settled = False
    for round_ in range(MAX_ECHO_ROUNDS):
        suffix = "" if round_ == 0 else f"-{round_ + 1}"
        code = _run("echo-fill" + suffix, lambda: echo_verdicts.main(echo_argv, units=units))
        if code:
            return code
        known = {record["unit"] for record in fills}
        landed = json.loads(args.echo_out.read_text())["verdicts"]
        fresh = [record for record in landed if record["unit"] not in known]
        fills += fresh
        # Every round writes only the blanks still blank when it ran, so the file is restored to the union: the artifact the cycle names holds every echo fill this chain landed rather than the last round's remainder.
        _write_fills(args.echo_out, stamp, fills)
        if round_ and not fresh:
            settled = True
            break
        code = _merge(
            "echo-merge" + suffix,
            args.echo_out,
            autosave=args.autosave,
            surface=surface,
            journal=args.journal,
        )
        if code:
            return code
        if round_ == 0:
            standing_argv = [
                str(args.autosave),
                "--surface",
                str(surface),
                "--rules",
                str(args.rules),
                "--out",
                str(args.standing_out),
                "--open-only",
                "--require-reach",
                "--memo",
                str(args.standing_memo or surface.parent / standing_verdicts.MEMO_NAME),
            ]
            if args.fresh_standing_memo:
                standing_argv.append("--fresh-memo")
            code = _run("standing-fill", lambda: standing_verdicts.main(standing_argv, units=units))
            if code:
                return code
            code = _merge(
                "standing-merge",
                args.standing_out,
                autosave=args.autosave,
                surface=surface,
                journal=args.journal,
            )
            if code:
                return code
    print(
        console.FIXPOINT_LINE
        + (
            "witnessed — a re-run of the fill cascade writes nothing"
            if settled
            else f"not witnessed after {MAX_ECHO_ROUNDS} echo rounds"
        ),
        flush=True,
    )

    if args.no_complaints:
        return 0
    return _run(
        "complaints",
        lambda: complaint_docket.main(
            [
                str(args.autosave),
                "--surface",
                str(surface),
                "--data-out",
                str(args.complaints_out),
            ],
            units=units,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
