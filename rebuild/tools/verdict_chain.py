"""The cycle's verdict plumbing in one process: carry, merge, echo fill, standing fill, their merges, a witnessed echo fixpoint, and the complaint docket.

The first index walk retains every surface id and only the human id/echo/notation projection. Carry reads that projection for ids, and every echo round reuses it. Standing fill opens fresh human streams through its source factory; the complaint docket receives its own stream. Full human index records live only for the step consuming them, while machine lines supply ids without being parsed.

Standing fill runs with `--open-only --require-reach`, its persistent memo, and the cycle's `--standing-fill-jobs` width. The chain opens each step with `[phase]` and closes it with `[t]`; its failure and fixpoint reports retain the `[chain]` prefix. Echo rounds after the standing merge close the cascade, and the echo output holds the union of fills landed across rounds.
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
    """One step, opened as a phase and timed. A tool that fails by `SystemExit` — which is how the merge's stamp guard and the rules-file validation refuse — reports its message and its code here rather than taking the whole chain down, so the steps after it can be reported as not run."""
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
        description="Run the artifact cycle's verdict plumbing in one process over streamed human index records and a reusable echo projection."
    )
    parser.add_argument("--surface", type=pathlib.Path, default=SURFACE)
    parser.add_argument(
        "--verdicts",
        type=pathlib.Path,
        action="append",
        default=[],
        metavar="VERDICTS_JSON",
        help="a prior verdicts file for the carry, landed by unit id; repeatable",
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
        help=f"where the standing fill keeps its per-unit decisions across passes; defaults to {standing_verdicts.MEMO_NAME} beside the surface directory, outside it, so a surface rebuild never clears it",
    )
    parser.add_argument(
        "--fresh-standing-memo",
        action="store_true",
        help="have the standing fill evaluate every unit regardless of its memo, and rewrite it (the cycle's --fresh)",
    )
    parser.add_argument(
        "--standing-fill-jobs",
        type=int,
        default=1,
        metavar="N",
        help="how many worker processes the standing fill refills the units its memo cannot serve across (its --jobs); 1 is the serial fill. The artifact cycle states it from its own box arithmetic (standing_fill_jobs in rebuild/tools/artifact_cycle.py)",
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
    unit_ids: set[str] = set()
    units = [
        echo_verdicts.echo_record(unit) for unit in unit_index.iter_human_units(surface, unit_ids=unit_ids)
    ]
    print(
        f"[t] index {time.perf_counter() - started:.1f}s\t({len(units)} human of {len(unit_ids)} units)",
        flush=True,
    )
    stamp = json.loads((surface / "manifest.json").read_text())["generated_at"]

    if args.verdicts:
        if args.carry_out is None:
            parser.error("--verdicts needs --carry-out")
        carry_argv: list[str] = []
        for verdicts in args.verdicts:
            carry_argv += ["--verdicts", str(verdicts)]
        carry_argv += ["--out", str(args.carry_out), "--current-surface", str(surface)]
        code = _run(
            "carry", lambda: carry_verdicts.main(carry_argv, current_units=units, current_ids=unit_ids)
        )
        if code:
            return code
    if args.no_merge:
        return 0

    to_merge = args.carry_out if args.verdicts else args.merge_master
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
                "--jobs",
                str(args.standing_fill_jobs),
            ]
            if args.fresh_standing_memo:
                standing_argv.append("--fresh-memo")
            code = _run(
                "standing-fill",
                lambda: standing_verdicts.main(
                    standing_argv, unit_source=lambda: unit_index.iter_human_units(surface)
                ),
            )
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
            units=unit_index.iter_human_units(surface),
            unit_ids=unit_ids,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
