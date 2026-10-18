"""Run the cycle's verdict update in one process: carry, merge, duplicate fill and merge, standing fill and merge, a final duplicate fill and merge, and the complaint list.

The first index walk keeps every corpus id and, for human units, only the `duplicate_record` projection (id, duplicate group, notation). The carry reads that projection, and both duplicate fills reuse it. The standing fill and the complaint list each get a fresh stream of human index records, so full records stay in memory only for the step that reads them. Machine units' index lines contribute their ids without being parsed.

On a pass that lands, the only kind that takes a snapshot of the store, the cycle hands that snapshot over with `--store-snapshot` beside a carry's master that is another file, and on every direct merge (`--merge-master`, the form for a pass whose corpus did not move, which always lands). The store can be replaced or removed between the plan and the snapshot, so what becomes of the snapshot is decided here, from the file the snapshot step left. A carry carries it beside the master when it exists. A direct merge merges the master straight in unless the merge would set the store aside because the snapshot is stamped for an older corpus or is not an ams-review-verdicts/1 document (`merge_sets_store_aside`). Such a snapshot is then carried onto the corpus by unit id, its skips handed to the merge as tombstones (`skip_clears`), and the master, unless it is that snapshot, merged into the result. The carry refuses a snapshot it cannot read, naming it, and the merge refuses an ams-review-verdicts/1 snapshot stamped for a newer corpus, either of which fails the verdict update. A missing snapshot, which the snapshot step leaves when there is no store, is not carried: a carry takes the master alone, a direct merge merges a master that is another file straight in, and the merge refuses a master that is the missing snapshot, naming it.

The standing fill runs with `--open-only --require-reach`, its persistent memo, and the cycle's `--standing-fill-jobs` width. Each step opens with a `[phase]` line and closes with a `[t]` line; the failure and completion lines use the `[verdict-update]` prefix. A duplicate fill proposes every blank member of an agreeing group at once, so the initial fill and the final fill after standing seeds groups are sufficient. Completion names this schedule, not acceptance of every proposal: a newer clear in the store can refuse a fill. The duplicate-fill output file holds the union of both passes' proposals, retaining the first proposal for each unit.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections.abc import Callable, Mapping

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.review import unit_index  # noqa: E402
from rebuild.review.verdict_store import parse_autosave_payload  # noqa: E402
from rebuild.tools import (  # noqa: E402
    carry_verdicts,
    complaint_list,
    console,
    duplicate_verdicts,
    merge_verdicts,
    standing_verdicts,
)

CORPUS = ROOT / "rebuild/out/review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
DUPLICATE_FILL = ROOT / "verdicts-duplicate-fill.json"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
STORE_CARRIED_NOTE = "the store's snapshot is carried onto the corpus by unit id before the merge, because it is stamped for an older corpus or is not an ams-review-verdicts/1 document and the merge would set the store aside and keep none of its verdicts; the carry refuses a snapshot it cannot read, and the merge keeps the snapshot's skips as tombstones"


def merge_sets_store_aside(snapshot: pathlib.Path, stamp: str) -> bool:
    """Return whether merge_verdicts, merging into a store copied from `snapshot` for the corpus stamped `stamp`, would set that store aside and keep none of its verdicts: the snapshot exists and is stamped for an older corpus, or is not an ams-review-verdicts/1 document, read as the merge reads it. The merge keeps the verdicts of an ams-review-verdicts/1 store stamped for the corpus and refuses one stamped for a newer corpus, and a missing snapshot, which the cycle's snapshot step leaves for a missing store, holds nothing to set aside. The carry refuses a snapshot it cannot read, naming it (`carry_verdicts.resolve_prior`)."""
    if not snapshot.exists():
        return False
    try:
        payload = parse_autosave_payload(snapshot.read_bytes())
    except OSError:
        return True
    return payload is None or payload["manifest_generated_at"] < stamp


def skip_clears(snapshot: pathlib.Path, human_ids: set[str]) -> dict[str, str]:
    """Return a tombstone at the skip's `at` for each of `human_ids` whose newest record in the snapshot is a skip, as the land leaves for a skip made on another corpus (`landing.overlay`). The store carry hands them to the merge, because the carry drops every skip and the merge keeps none of the records of a store it sets aside, so without them a master record no newer than the reviewer's deferral would land. It reads the snapshot after the carry has read it, so the file is a readable verdicts list."""
    records = carry_verdicts.latest_verdicts(json.loads(snapshot.read_text()))
    return {
        unit: record["at"]
        for unit, record in records.items()
        if record["verdict"] == "skip" and unit in human_ids
    }


def _run(name: str, call: Callable[[], int | None]) -> int:
    """Run one step as a timed phase and return its exit code. A `SystemExit` (the carry's orphan refusal, the duplicate fill's stamp check and the rules-file validation raise one) is converted to a code and its message printed, so the verdict update still prints its `[verdict-update] failed:` line and the cycle can report the later steps as not run."""
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
    name: str,
    *paths: pathlib.Path,
    autosave: pathlib.Path,
    corpus: pathlib.Path,
    journal: pathlib.Path,
    clears: Mapping[str, str] | None = None,
) -> int:
    return _run(
        name,
        lambda: merge_verdicts.main(
            [
                *map(str, paths),
                "--autosave",
                str(autosave),
                "--corpus",
                str(corpus),
                "--journal",
                str(journal),
            ],
            clears=clears,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the artifact cycle's verdict update in one process over streamed human index records and a reusable duplicate projection."
    )
    parser.add_argument("--corpus", "--surface", type=pathlib.Path, default=CORPUS)
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
        "--allow-orphans",
        action="store_true",
        help="pass the carry --allow-orphans, so it writes its file however many prior verdicts it orphans",
    )
    parser.add_argument(
        "--merge-master",
        type=pathlib.Path,
        help="merge this verdicts master directly instead of carrying it: the form for a pass whose corpus did not move, where the carry is provably the identity and the master is the one input the autosave's hash cannot see. When a carry runs (--verdicts, or a --store-snapshot the merge would set aside), the merge takes the carried file first and this master after it, so a carried record keeps a tie, as the store's record does in a plain direct merge; a master that is the --store-snapshot the carry took is not merged again",
    )
    parser.add_argument(
        "--store-snapshot",
        type=pathlib.Path,
        help="with --carry-out, and --verdicts or --merge-master: the snapshot of the store that --autosave copies, as the snapshot step left it, so it may be missing. With --verdicts alone it is carried beside them when it exists. With --merge-master it is carried only when the merge would set that store aside, because the snapshot is stamped for an older corpus or is not an ams-review-verdicts/1 document: a snapshot stamped for an older corpus, or one that is not such a document but holds a readable `verdicts` list, is carried onto the corpus by unit id, its skips kept as tombstones, and the master merged into the result, so the store's verdicts are kept, and one the carry cannot read is refused by the carry, which names it (`carry_verdicts.resolve_prior`). A missing snapshot, or an ams-review-verdicts/1 document stamped for the corpus or a newer one, is not carried",
    )
    parser.add_argument("--autosave", type=pathlib.Path, default=AUTOSAVE)
    parser.add_argument("--journal", type=pathlib.Path, default=merge_verdicts.JOURNAL)
    parser.add_argument("--duplicate-out", type=pathlib.Path, default=DUPLICATE_FILL)
    parser.add_argument("--standing-out", type=pathlib.Path, default=STANDING_FILL)
    parser.add_argument("--rules", type=pathlib.Path, default=standing_verdicts.RULES)
    parser.add_argument(
        "--standing-memo",
        type=pathlib.Path,
        help=f"where the standing fill keeps its per-unit decisions across passes; defaults to {standing_verdicts.MEMO_NAME} beside the corpus directory, outside it, so a corpus rebuild never clears it",
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
        help="how many worker processes the standing fill refills the units its memo cannot serve across (its --jobs); 1 is the serial fill. The artifact cycle states it from this machine's cores and memory (standing_fill_jobs in rebuild/tools/artifact_cycle.py)",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="carry only: never write the live store, and run neither fill nor the complaint list (the staging form)",
    )
    parser.add_argument(
        "--no-complaints",
        action="store_true",
        help="skip the complaint list at the end of the verdict update",
    )
    parser.add_argument("--complaints-out", type=pathlib.Path, default=complaint_list.DATA_OUT)
    args = parser.parse_args(argv)
    if args.store_snapshot is not None and (
        args.carry_out is None or not (args.verdicts or args.merge_master)
    ):
        parser.error("--store-snapshot needs --carry-out, and --verdicts or --merge-master")

    corpus = args.corpus
    started = time.perf_counter()
    unit_ids: set[str] = set()
    units = [
        duplicate_verdicts.duplicate_record(unit)
        for unit in unit_index.iter_human_units(corpus, unit_ids=unit_ids)
    ]
    print(
        f"[t] index {time.perf_counter() - started:.1f}s\t({len(units)} human of {len(unit_ids)} units)",
        flush=True,
    )
    stamp = json.loads((corpus / "manifest.json").read_text())["generated_at"]

    snapshot = args.store_snapshot
    store_carried = (
        snapshot is not None and args.merge_master is not None and merge_sets_store_aside(snapshot, stamp)
    )
    carry_snapshot = store_carried or (
        snapshot is not None and args.merge_master is None and snapshot.exists()
    )
    carried = [*args.verdicts, *([snapshot] if carry_snapshot else [])]
    if carried:
        if args.carry_out is None:
            parser.error("--verdicts needs --carry-out")
        carry_argv: list[str] = []
        for verdicts in carried:
            carry_argv += ["--verdicts", str(verdicts)]
        carry_argv += ["--out", str(args.carry_out), "--current-corpus", str(corpus)]
        if args.allow_orphans:
            carry_argv.append("--allow-orphans")

        def carry() -> int | None:
            if store_carried:
                print(STORE_CARRIED_NOTE, flush=True)
            return carry_verdicts.main(carry_argv, current_units=units, current_ids=unit_ids)

        code = _run("carry", carry)
        if code:
            return code
    if args.no_merge:
        return 0

    master = args.merge_master
    clears = None
    if store_carried:
        assert snapshot is not None
        clears = skip_clears(snapshot, {unit["id"] for unit in units})
        if master is not None and master.resolve() == snapshot.resolve():
            master = None
    to_merge = [path for path in (args.carry_out if carried else None, master) if path is not None]
    if to_merge:
        code = _merge(
            "merge", *to_merge, autosave=args.autosave, corpus=corpus, journal=args.journal, clears=clears
        )
        if code:
            return code

    duplicate_argv = [str(args.autosave), "--corpus", str(corpus), "--out", str(args.duplicate_out)]
    code = _run("duplicate-fill", lambda: duplicate_verdicts.main(duplicate_argv, units=units))
    if code:
        return code
    fills = json.loads(args.duplicate_out.read_text())["verdicts"]
    code = _merge(
        "duplicate-merge", args.duplicate_out, autosave=args.autosave, corpus=corpus, journal=args.journal
    )
    if code:
        return code

    standing_argv = [
        str(args.autosave),
        "--corpus",
        str(corpus),
        "--rules",
        str(args.rules),
        "--out",
        str(args.standing_out),
        "--open-only",
        "--require-reach",
        "--memo",
        str(args.standing_memo or corpus.parent / standing_verdicts.MEMO_NAME),
        "--jobs",
        str(args.standing_fill_jobs),
    ]
    if args.fresh_standing_memo:
        standing_argv.append("--fresh-memo")
    code = _run(
        "standing-fill",
        lambda: standing_verdicts.main(
            standing_argv, unit_source=lambda: unit_index.iter_human_units(corpus)
        ),
    )
    if code:
        return code
    code = _merge(
        "standing-merge", args.standing_out, autosave=args.autosave, corpus=corpus, journal=args.journal
    )
    if code:
        return code

    code = _run("duplicate-fill-2", lambda: duplicate_verdicts.main(duplicate_argv, units=units))
    if code:
        return code
    known = {record["unit"] for record in fills}
    fills += [
        record
        for record in json.loads(args.duplicate_out.read_text())["verdicts"]
        if record["unit"] not in known
    ]
    _write_fills(args.duplicate_out, stamp, fills)
    code = _merge(
        "duplicate-merge-2", args.duplicate_out, autosave=args.autosave, corpus=corpus, journal=args.journal
    )
    if code:
        return code

    if not args.no_complaints:
        code = _run(
            "complaints",
            lambda: complaint_list.main(
                [
                    str(args.autosave),
                    "--corpus",
                    str(corpus),
                    "--data-out",
                    str(args.complaints_out),
                ],
                units=unit_index.iter_human_units(corpus),
                unit_ids=unit_ids,
            ),
        )
        if code:
            return code
    print(console.COMPLETE_LINE + "duplicate, standing, duplicate", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
