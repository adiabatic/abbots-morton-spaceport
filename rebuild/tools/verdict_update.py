"""Run the cycle's verdict update in one process: carry, merge, duplicate fill and merge, standing fill and merge, a final duplicate fill and merge, and the complaint list.

The first index walk keeps every corpus id and, for human units, only the `duplicate_record` projection (id, duplicate group, notation). The carry reads that projection, and both duplicate fills reuse it. The standing fill and the complaint list each get a fresh stream of human index records, so full records stay in memory only for the step that reads them. Machine units' index lines contribute their ids without being parsed.

On a pass that lands, the only kind that takes a snapshot of the store, the cycle hands that snapshot over with `--store-snapshot` beside a carry's master that is another file, and on every direct merge (`--merge-master`, the form for a pass whose corpus did not move, which always lands). The store can be replaced or removed between the plan and the snapshot, so what becomes of the snapshot is decided here, from the file the snapshot step left. A carry carries it beside the master when it exists. A direct merge merges the master straight in unless the merge would set the store aside because the snapshot is stamped for an older corpus or is not an ams-review-verdicts/1 document (`merge_sets_store_aside`). Such a snapshot is then carried onto the corpus by unit id, its skips handed to the merge as tombstones (`skip_clears`), and the master, unless it is that snapshot, merged into the result. The carry refuses a snapshot it cannot read, naming it, and the merge refuses an ams-review-verdicts/1 snapshot stamped for a newer corpus, either of which fails the verdict update. A missing snapshot, which the snapshot step leaves when there is no store, is not carried: a carry takes the master alone, a direct merge merges a master that is another file straight in, and the merge refuses a master that is the missing snapshot, naming it. With neither --verdicts nor --merge-master there is nothing to carry or merge, and the fills run over the store at --autosave. When there is no store there, the merge runs with no file: under the store's lock, which first finishes any land a dead holder left (`landing.locked_store`), it merges no file into the store that land leaves, or, when there is still no store, writes an empty store stamped for the corpus (`merge_verdicts`), so the fills start from an empty store, as they do on a pass with no store whose master holds no verdict. Against the live store while a review server listens, that merge, like any of the update's merges that would write the store, refuses and the run fails at it, since the update has no --yes (`merge_verdicts`). With nothing to merge, when the store at --autosave is one the carry cannot read, or is not an ams-review-verdicts/1 document, which the merges after the fills would set aside, keeping none of its verdicts, the update refuses before the first fill, naming the store and why, and writes nothing (`refuse_unreadable_store`).

The standing fill runs with `--open-only --require-reach`, its persistent memo, and the cycle's `--standing-fill-jobs` width. Each step opens with a `[phase]` line and closes with a `[t]` line; the failure and completion lines use the `[verdict-update]` prefix. A duplicate fill proposes every blank member of an agreeing group at once, so the initial fill and the final fill after standing seeds groups are sufficient. Completion names this schedule, not acceptance of every proposal: a newer clear in the store can refuse a fill. The duplicate-fill output file holds the union of both passes' proposals, retaining the first proposal for each unit. The fill files go to --duplicate-out and --standing-out, or else to verdicts-duplicate-fill.json and verdicts-standing-fill.json beside --autosave, which only a store named verdicts-autosave.json has by default (`journal.beside_store`), so a hand run on a copy writes no verdicts file at the repo root, where `rebuild.review.status` counts every verdicts-*.json but the live store as a carry candidate. The complaint list (--complaints-out) and the standing fill's memo (--standing-memo) keep their defaults whatever --autosave names, because the cycle's verdict update, which runs on the pass's prepared store, writes the complaint list at its default and the memo beside the served corpus, the complaint list is not a verdicts file, and no memo decision depends on the store.

The accepted list (`rebuild.review.accepted`; `--accepted`, by default rebuild/accepted-units.txt beside --autosave, which only a store named verdicts-autosave.json has, so the cycle names it for its prepared store) is read once and handed to every step: in process to the carry and the merges, and by path to the fills and the complaint list. The served corpus (`--served-corpus`) is where the carry counts the list's units among the prior verdicts. Every carry of the store, through --verdicts or --store-snapshot, hands the merge the store's skips as tombstones (`skip_clears`), since the carry drops every skip. The store's override section, its records and tombstones on listed units (`accepted.Overrides`), travels in neither the carried file nor those tombstones: the merge keeps the section of the store it merges into, which is the store the carry read or the cycle's prepared store from it, whole, a stamp move turning its skips into tombstones (`merge_verdicts`), so an override or a clear on an accepted unit survives the pass whatever corpus its unit is on. With nothing to merge, a store that does not pair with the list is refused before the first fill, as one the carry cannot read is.

On a pass that lands, the cycle names the snapshot whose file it linked the prepared store to (`--prepared-from`). At its end the update compares the store at --autosave with that snapshot, size first and then in chunks (`output_sink.same_bytes`), and when every step left it as the snapshot's bytes, because every merge skips its write when it changes nothing, which leaves it the snapshot's own file, it records that beside the store for the land (`landing.record_unchanged`), which then leaves the live store in place (`landing`'s module docstring says when). Its completion line ends `r_unchanged=true` or `r_unchanged=false` then, and names nothing without `--prepared-from`.

The update refuses before it parses its command line while the alphabet switch (`spec_load.M1_RUNES_ENV`) is set outside the fault kit (`spec_load.small_alphabet_refusal`).
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

from rebuild.pipeline import output_sink  # noqa: E402
from rebuild.review import accepted, landing, unit_index  # noqa: E402
from rebuild.review.accepted import Accepted  # noqa: E402
from rebuild.review.journal import AUTOSAVE_NAME, JOURNAL_NAME, beside_store, journal_path_for  # noqa: E402
from rebuild.review.landing import SNAPSHOT_NAME  # noqa: E402
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
DUPLICATE_FILL_NAME = "verdicts-duplicate-fill.json"
STANDING_FILL_NAME = "verdicts-standing-fill.json"
R_UNCHANGED = "r_unchanged"
STORE_CARRIED_NOTE = "the store's snapshot is carried onto the corpus by unit id before the merge, because it is stamped for an older corpus or is not an ams-review-verdicts/1 document and the merge would set the store aside and keep none of its verdicts; the carry refuses a snapshot it cannot read, and the merge keeps the snapshot's skips as tombstones"


def merge_sets_store_aside(snapshot: pathlib.Path, stamp: str) -> bool:
    """Return whether merge_verdicts, merging into a store prepared from `snapshot` for the corpus stamped `stamp`, would set that store aside and keep none of its verdicts: the snapshot exists and is stamped for an older corpus, or is not an ams-review-verdicts/1 document, read as the merge reads it. The merge keeps the verdicts of an ams-review-verdicts/1 store stamped for the corpus and refuses one stamped for a newer corpus, and a missing snapshot, which the cycle's snapshot step leaves for a missing store, holds nothing to set aside. The carry refuses a snapshot it cannot read, naming it (`carry_verdicts.resolve_prior`)."""
    if not snapshot.exists():
        return False
    try:
        payload = parse_autosave_payload(snapshot.read_bytes())
    except OSError:
        return True
    return payload is None or payload["manifest_generated_at"] < stamp


def skip_clears(snapshot: pathlib.Path, human_ids: set[str]) -> dict[str, str]:
    """Return a tombstone at the skip's `at` for each of `human_ids` whose newest record among the snapshot's own is a skip, as the land leaves for a skip made on another corpus (`landing.overlay`). Every carry of the store hands them to the merge, because the carry drops every skip and the merge keeps none of the records of a store it sets aside, so without them a master record no newer than the reviewer's deferral would land. A skip in the snapshot's override section needs none, since the merge keeps that section and turns its skips into tombstones itself (`accepted.Overrides.moved`). It reads the snapshot after the carry has read it, so the file is a readable verdicts list."""
    records = carry_verdicts.latest_verdicts(json.loads(snapshot.read_text()))
    return {
        unit: record["at"]
        for unit, record in records.items()
        if record["verdict"] == "skip" and unit in human_ids
    }


def refuse_unreadable_store(store: pathlib.Path, listed: Accepted) -> None:
    """Raise a `SystemExit` naming `store` and why when the carry cannot read it (`carry_verdicts.read_verdicts_file`), it is not an ams-review-verdicts/1 document (`parse_autosave_payload`), which the merges after the fills would set aside, keeping none of its verdicts, or it does not pair with the accepted list `listed` (`accepted.pair`). A hand run with nothing to merge calls it before the first fill, which reads the store as it stands."""
    try:
        payload = carry_verdicts.read_verdicts_file(store)
        accepted.pair(payload, listed, store=store)
    except accepted.Mismatch as mismatch:
        raise SystemExit(f"verdict update refused: {mismatch}, and nothing was written.") from None
    except carry_verdicts.UnreadableVerdicts as unreadable:
        raise SystemExit(
            f"verdict update refused: the store {unreadable}, and nothing was written. "
            "If it is the verdict store, restore a readable store from the journal with "
            "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`."
        ) from None
    if parse_autosave_payload(store.read_bytes()) is None:
        raise SystemExit(
            f"verdict update refused: the store {store} is not an ams-review-verdicts/1 document, so the merges after "
            "the fills would set it aside and keep none of its verdicts, and nothing was written. If it is the "
            "verdict store, a cycle pass that names it as the master "
            "(`make artifact-cycle ARGS='--verdicts verdicts-autosave.json'`) carries it onto the corpus by unit id "
            "before its fills."
        )


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
    listed: Accepted,
    clears: Mapping[str, str] | None = None,
) -> int:
    return _run(
        name,
        lambda: merge_verdicts.run_merge(
            list(paths),
            autosave=autosave,
            corpus=corpus,
            journal_path=journal,
            dry_run=False,
            clears=clears,
            listed=listed,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    from rebuild.pipeline import spec_load

    refusal = spec_load.small_alphabet_refusal("the verdict update")
    if refusal is not None:
        raise SystemExit(refusal)
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
        help="with --carry-out, and --verdicts or --merge-master: the snapshot of the store whose file --autosave was linked from (`landing.snapshot`), as the snapshot step left it, so it may be missing. With --verdicts alone it is carried beside them when it exists. With --merge-master it is carried only when the merge would set that store aside, because the snapshot is stamped for an older corpus or is not an ams-review-verdicts/1 document: a snapshot stamped for an older corpus, or one that is not such a document but holds a readable `verdicts` list, is carried onto the corpus by unit id, its skips kept as tombstones, and the master merged into the result, so the store's verdicts are kept, and one the carry cannot read is refused by the carry, which names it (`carry_verdicts.resolve_prior`). A missing snapshot, or an ams-review-verdicts/1 document stamped for the corpus or a newer one, is not carried",
    )
    parser.add_argument("--autosave", type=pathlib.Path, default=AUTOSAVE)
    parser.add_argument(
        "--prepared-from",
        type=pathlib.Path,
        metavar="SNAPSHOT",
        help="the snapshot the cycle linked --autosave from (`landing.snapshot`): at its end the update records beside --autosave whether it left the store as the snapshot's bytes, for the land (`landing.record_unchanged`), and names it on its completion line as r_unchanged",
    )
    parser.add_argument(
        "--accepted",
        type=pathlib.Path,
        help=f"the accepted list the store pairs with, read once for every step (default: rebuild/accepted-units.txt beside --autosave; a store not named {AUTOSAVE_NAME} needs --accepted)",
    )
    parser.add_argument(
        "--served-corpus",
        type=pathlib.Path,
        default=CORPUS,
        help="the corpus the review server serves, on whose human units the carry counts the accepted list's units among the prior verdicts (default: %(default)s)",
    )
    parser.add_argument(
        "--journal",
        type=pathlib.Path,
        help=f"the journal every merge appends to (default: the {JOURNAL_NAME} beside --autosave; a store not named {AUTOSAVE_NAME} needs --journal)",
    )
    parser.add_argument(
        "--duplicate-out",
        type=pathlib.Path,
        help=f"where the duplicate fills are written (default: the {DUPLICATE_FILL_NAME} beside --autosave; a store not named {AUTOSAVE_NAME} needs --duplicate-out)",
    )
    parser.add_argument(
        "--standing-out",
        type=pathlib.Path,
        help=f"where the standing fill is written (default: the {STANDING_FILL_NAME} beside --autosave; a store not named {AUTOSAVE_NAME} needs --standing-out)",
    )
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

    try:
        journal = args.journal or journal_path_for(args.autosave)
        accepted_path = args.accepted or accepted.path_for(args.autosave)
        if not args.no_merge:
            args.duplicate_out = args.duplicate_out or beside_store(
                args.autosave, DUPLICATE_FILL_NAME, "--duplicate-out"
            )
            args.standing_out = args.standing_out or beside_store(
                args.autosave, STANDING_FILL_NAME, "--standing-out"
            )
    except ValueError as error:
        parser.error(str(error))
    try:
        listed = accepted.read(accepted_path)
    except accepted.Malformed as malformed:
        raise SystemExit(f"verdict update refused: {malformed}, and nothing was written.") from None
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
        carry_argv += [
            "--out",
            str(args.carry_out),
            "--current-corpus",
            str(corpus),
            "--served-corpus",
            str(args.served_corpus),
        ]
        if args.allow_orphans:
            carry_argv.append("--allow-orphans")

        def carry() -> int | None:
            if store_carried:
                print(STORE_CARRIED_NOTE, flush=True)
            return carry_verdicts.main(carry_argv, current_units=units, current_ids=unit_ids, listed=listed)

        code = _run("carry", carry)
        if code:
            return code
    if args.no_merge:
        return 0

    master = args.merge_master
    clears = None
    stores = [path for path in args.verdicts if path.name == SNAPSHOT_NAME]
    if carry_snapshot:
        assert snapshot is not None
        stores.append(snapshot)
    if stores:
        human_ids = {unit["id"] for unit in units}
        clears = {}
        for store in stores:
            for unit, at in skip_clears(store, human_ids).items():
                clears[unit] = max(at, clears.get(unit, at))
    if store_carried:
        assert snapshot is not None
        if master is not None and master.resolve() == snapshot.resolve():
            master = None
    carry_out = args.carry_out if carried else None
    to_merge = [path for path in (carry_out, master) if path is not None]
    if to_merge or not args.autosave.exists():
        code = _merge(
            "merge",
            *to_merge,
            autosave=args.autosave,
            corpus=corpus,
            journal=journal,
            listed=listed,
            clears=clears,
        )
    else:
        code = _run("store", lambda: refuse_unreadable_store(args.autosave, listed))
    if code:
        return code

    listed_argv = ["--accepted", str(accepted_path)]
    duplicate_argv = [
        str(args.autosave),
        "--corpus",
        str(corpus),
        "--out",
        str(args.duplicate_out),
        *listed_argv,
    ]
    code = _run("duplicate-fill", lambda: duplicate_verdicts.main(duplicate_argv, units=units))
    if code:
        return code
    fills = json.loads(args.duplicate_out.read_text())["verdicts"]
    code = _merge(
        "duplicate-merge",
        args.duplicate_out,
        autosave=args.autosave,
        corpus=corpus,
        journal=journal,
        listed=listed,
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
        *listed_argv,
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
        "standing-merge",
        args.standing_out,
        autosave=args.autosave,
        corpus=corpus,
        journal=journal,
        listed=listed,
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
        "duplicate-merge-2",
        args.duplicate_out,
        autosave=args.autosave,
        corpus=corpus,
        journal=journal,
        listed=listed,
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
                    *listed_argv,
                ],
                units=unit_index.iter_human_units(corpus),
                unit_ids=unit_ids,
            ),
        )
        if code:
            return code
    unchanged = ""
    if args.prepared_from is not None:
        same = output_sink.same_bytes(args.autosave, args.prepared_from)
        landing.record_unchanged(args.autosave, same)
        unchanged = f" {R_UNCHANGED}={'true' if same else 'false'}"
    print(console.COMPLETE_LINE + "duplicate, standing, duplicate" + unchanged, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
