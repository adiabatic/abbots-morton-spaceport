"""Run the cycle's verdict update in one process: carry, merge, duplicate fill and merge, standing fill and merge, a final duplicate fill and merge, and the complaint list.

The first index walk keeps every corpus id and, for human units, only the `duplicate_record` projection (id, duplicate group, notation). The carry reads that projection, and both duplicate fills reuse it. The standing fill and the complaint list each get a fresh stream of human index records, so full records stay in memory only for the step that reads them. Machine units' index lines contribute their ids without being parsed.

On a pass that lands, the only kind that takes a snapshot of the store, the cycle hands that snapshot over with `--store-snapshot` beside a carry's master that is another file, and on every direct merge (`--merge-master`, the form for a pass whose corpus did not move, which always lands). The store can be replaced or removed between the plan and the snapshot, so what becomes of the snapshot is decided here, from the file the snapshot step left. A carry carries it beside the master when it exists. A direct merge merges the master straight in unless the merge would set the store aside because the snapshot is stamped for an older corpus or is not an ams-review-verdicts/1 document (`merge_sets_store_aside`, which reads it a record at a time). Such a snapshot is then carried onto the corpus by unit id, its skips handed to the merge as tombstones (`skip_clears`), and the master, unless it is that snapshot, merged into the result. The carry refuses a snapshot it cannot read, naming it, and the merge refuses an ams-review-verdicts/1 snapshot stamped for a newer corpus, either of which fails the verdict update. A missing snapshot, which the snapshot step leaves when there is no store, is not carried: a carry takes the master alone, a direct merge merges a master that is another file straight in, and the merge refuses a master that is the missing snapshot, naming it. With neither --verdicts nor --merge-master there is nothing to carry or merge, and the fills run over the store at --autosave. When there is no store there, the merge runs with no input: under the store's lock, which first finishes any land a dead holder left (`landing.locked_store`), it merges nothing into the store that land leaves, or, when there is still no store, leaves an empty store stamped for the corpus (`merge_verdicts`), so the fills start from an empty store, as they do on a pass with no store whose master holds no verdict. Against the live store while a review server listens, that merge, like any of the update's merges that would write the store, refuses and the run fails at it, since the update has no --yes (`merge_verdicts`). With nothing to merge, when the store at --autosave is one the carry cannot read, or is not an ams-review-verdicts/1 document, which the merges after the fills would set aside, keeping none of its verdicts, the update refuses before the first fill, naming the store and why, and writes nothing (`refuse_unreadable_store`).

On a merging run the carry is fused into the first merge: the carry prints its counts and makes its refusals, and the first merge then streams the carried records straight from the files the carry read (`carry_verdicts.Carried`), so no carried file is written, and the carry's line reads `carried <N> onto manifest <stamp>`. `--carry-out` writes the carried file there instead, which the first merge reads, and a `--no-merge` run, which carries only, needs it. The first merge takes the carry first and the master after it, so a carried record keeps a tie, and names the fused carry `carry` in its line.

Every merge after the first takes its fill's records in memory, handed over by the fill that made them, so neither fill file is read back. The fill files are still written, to --duplicate-out and --standing-out, or else to verdicts-duplicate-fill.json and verdicts-standing-fill.json beside --autosave, which only a store named verdicts-autosave.json has by default (`journal.beside_store`), so a hand run on a copy writes no verdicts file at the repo root, where `rebuild.review.status` counts every verdicts-*.json but the live store as a carry candidate. The standing fill writes its file once. The duplicate fill writes its file after the first round, and the update writes it once more after the second, holding the union of both rounds' proposals, the first proposal for each unit kept; the second round prints its own `wrote` line and leaves the write to that union, which is in place before the next phase opens. The standing fill runs with `--open-only --require-reach`, its persistent memo, and the cycle's `--standing-fill-jobs` width. Each step opens with a `[phase]` line and closes with a `[t]` line; the failure and completion lines use the `[verdict-update]` prefix. A duplicate fill proposes every blank member of an agreeing group at once, so the initial fill and the final fill after standing seeds groups are sufficient. Completion names this schedule, not acceptance of every proposal: a newer clear in the store can refuse a fill. The complaint list (--complaints-out) and the standing fill's memo (--standing-memo) keep their defaults whatever --autosave names, because the cycle's verdict update, which runs on the pass's prepared store, writes the complaint list at its default and the memo beside the served corpus, the complaint list is not a verdicts file, and no memo decision depends on the store.

On a pass that lands, the cycle names the snapshot whose file it linked the prepared store to (`--prepared-from`). Then the update holds the store it prepares in memory from its first merge to its end, as sorted record lines with a table over them (`held_store.Held`): each merge after the first merges into it, reading only the units its inputs name, the fills and the complaint list read it through the table instead of parsing a file, and nothing is handed to the standing fill's refill pool, which takes units. After its last merge, before the complaint list, the update writes it once, when a merge changed it, in the layout the land writes (`landing.document_bytes`), to a temporary name renamed over --autosave, and journals nothing: the land journals the store it lands, and when the live store is the snapshot it renames this file into place as its result (`landing`). `--journal` beside `--prepared-from` is a usage error. A merge that sets the prepared store aside renames it to its stash name when it does. The update then compares the store at --autosave with the snapshot, size first and then in chunks (`output_sink.same_bytes`), and when every step left it as the snapshot's bytes, because no merge changed anything, so it is still the snapshot's own file, it records that beside the store for the land (`landing.record_unchanged`), which then leaves the live store in place (`landing`'s module docstring says when). Its completion line ends `r_unchanged=true` or `r_unchanged=false` then, and names nothing without `--prepared-from`.

Without `--prepared-from`, a hand run on the live store or a copy, every merge reads the store under its lock, and one that changes it writes it, one record a line, and journals it (`merge_verdicts`) to --journal, or else to the verdicts-journal.ndjson beside a store named verdicts-autosave.json (`journal.journal_path_for`), and a store under any other name needs --journal. The fills and the complaint list read the store the last merge left, held in memory, and with nothing to merge the first fill reads the store's file.

The accepted list (`rebuild.review.accepted`; `--accepted`, by default rebuild/accepted-units.txt beside --autosave, which only a store named verdicts-autosave.json has, so the cycle names it for its prepared store) is read once and handed to every step: in process to the carry and the merges, and by path to the fills and the complaint list. The served corpus (`--served-corpus`) is where the carry counts the list's units among the prior verdicts. Every carry of the store, through --verdicts or --store-snapshot, hands the merge the store's skips as tombstones (`skip_clears`), since the carry drops every skip; a fused carry collects them in its own walk of every file it reads as the store, one named `SNAPSHOT_NAME` (`carry_verdicts.Carried.skips`), so that file is not read again for them, and a snapshot under another name, which the carry reads as any other file, is read for them as it is when the carried file is written (`_store_clears`). The store's override section, its records and tombstones on listed units (`accepted.Overrides`), travels in neither the carry nor those tombstones: the merge keeps the section of the store it merges into, which is the store the carry read or the cycle's prepared store from it, whole, a stamp move turning its skips into tombstones (`merge_verdicts`), so an override or a clear on an accepted unit survives the pass whatever corpus its unit is on. With nothing to merge, a store that does not pair with the list is refused before the first fill, as one the carry cannot read is.

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
from rebuild.tools.held_store import Held  # noqa: E402

CORPUS = ROOT / "rebuild/out/review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
DUPLICATE_FILL_NAME = "verdicts-duplicate-fill.json"
STANDING_FILL_NAME = "verdicts-standing-fill.json"
R_UNCHANGED = "r_unchanged"
STORE_CARRIED_NOTE = "the store's snapshot is carried onto the corpus by unit id before the merge, because it is stamped for an older corpus or is not an ams-review-verdicts/1 document and the merge would set the store aside and keep none of its verdicts; the carry refuses a snapshot it cannot read, and the merge keeps the snapshot's skips as tombstones"


def merge_sets_store_aside(snapshot: pathlib.Path, stamp: str) -> bool:
    """Return whether merge_verdicts, merging into a store prepared from `snapshot` for the corpus stamped `stamp`, would set that store aside and keep none of its verdicts: the snapshot exists and is stamped for an older corpus, or is not an ams-review-verdicts/1 document, read as the merge reads it, a record at a time (`accepted.read_tail`). The merge keeps the verdicts of an ams-review-verdicts/1 store stamped for the corpus and refuses one stamped for a newer corpus, and a missing snapshot, which the cycle's snapshot step leaves for a missing store, holds nothing to set aside. The carry refuses a snapshot it cannot read, naming it (`carry_verdicts.resolve_prior`)."""
    if not snapshot.exists():
        return False
    tail = accepted.read_tail(snapshot)
    return tail is None or tail.stamp < stamp


def skip_clears(snapshot: pathlib.Path, human_ids: set[str]) -> dict[str, str]:
    """Return a tombstone at the skip's `at` for each of `human_ids` whose newest record among the snapshot's own is a skip, as the land leaves for a skip made on another corpus (`landing.overlay`). Every carry of the store hands them to the merge, because the carry drops every skip and the merge keeps none of the records of a store it sets aside, so without them a master record no newer than the reviewer's deferral would land. A skip in the snapshot's override section needs none, since the merge keeps that section and turns its skips into tombstones itself (`accepted.Overrides.moved`). It reads the snapshot after the carry has read it, so the file is a readable verdicts list: a record at a time when its records come in unit order, and whole otherwise."""
    clears: dict[str, str] = {}
    walk: dict = {"held": None, "in_order": True}

    def settle(record: dict) -> None:
        if record["verdict"] == "skip" and record["unit"] in human_ids:
            clears[record["unit"]] = record["at"]

    def each(record: object) -> None:
        held = walk["held"]
        assert isinstance(record, dict)
        if held is not None and record["unit"] == held["unit"]:
            if record["at"] > held["at"]:
                walk["held"] = record
            return
        if held is not None:
            walk["in_order"] = walk["in_order"] and record["unit"] > held["unit"]
            settle(held)
        walk["held"] = record

    tail = accepted.read_tail(snapshot, each)
    if tail is not None and tail.lists == 1 and walk["in_order"]:
        if walk["held"] is not None:
            settle(walk["held"])
        return clears
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


class _Store:
    """The store the update's merges leave, held as record lines (`held_store.Held`) once a merge has run, for the fills and the complaint list to read, and on a run with `--prepared-from` (`prepared`) for each merge after the first to merge into and for the update to write once after its last merge; whether a merge changed it (`changed`)."""

    def __init__(
        self, *, autosave: pathlib.Path, corpus: pathlib.Path, journal: pathlib.Path | None, listed: Accepted
    ) -> None:
        self.autosave = autosave
        self.corpus = corpus
        self.journal = journal
        self.listed = listed
        self.held: Held | None = None
        self.changed = False

    def merge(
        self, name: str, inputs: list[merge_verdicts.Input], clears: Mapping[str, str] | None = None
    ) -> int:
        def call() -> int:
            outcome = merge_verdicts.merge(
                inputs,
                autosave=self.autosave,
                corpus=self.corpus,
                journal_path=self.journal,
                clears=clears,
                listed=self.listed,
                held=self.held if self.journal is None else None,
            )
            if outcome.code == 0:
                self.held = outcome.held
                self.changed = self.changed or outcome.changed
            return outcome.code

        return _run(name, call)

    def write(self) -> None:
        """Write the held store over --autosave once, under its lock, when a merge changed it."""
        if self.changed and self.held is not None:
            with landing.locked_store(self.autosave):
                self.held.write(self.autosave)


def _store_clears(
    stores: list[pathlib.Path], fused: list[carry_verdicts.Carried], units: list[dict]
) -> dict[str, str]:
    """Return the tombstones the first merge takes for the skips of `stores`, the carried files that are the store (`skip_clears`), the later `at` where two hold one: a fused carry's own walk collected them for every file it read as the store, one named `SNAPSHOT_NAME` (`carry_verdicts.Carried.skips`), and every other one is read for them here, as each is when the carried file is written."""
    skips = fused[0].skips if fused else {}
    walked = [store for store in stores if not fused or store.name != SNAPSHOT_NAME]
    human_ids = {unit["id"] for unit in units} if skips or walked else set()
    clears = {unit: at for unit, at in skips.items() if unit in human_ids}
    for store in walked:
        for unit, at in skip_clears(store, human_ids).items():
            clears[unit] = max(at, clears.get(unit, at))
    return clears


def _fills(name: pathlib.Path, records: list[dict]) -> list[merge_verdicts.Input]:
    return [merge_verdicts.Input(merge_verdicts._rel(name), records=lambda: records)]


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
    parser.add_argument(
        "--carry-out",
        type=pathlib.Path,
        help="where the carried verdicts are written, which the first merge then reads; without it a merging run fuses the carry into its first merge and writes no carried file, and a --no-merge run needs it",
    )
    parser.add_argument(
        "--allow-orphans",
        action="store_true",
        help="pass the carry --allow-orphans, so it carries however many prior verdicts it orphans",
    )
    parser.add_argument(
        "--merge-master",
        type=pathlib.Path,
        help="merge this verdicts master directly instead of carrying it: the form for a pass whose corpus did not move, where the carry is provably the identity and the master is the one input the autosave's hash cannot see. When a carry runs (--verdicts, or a --store-snapshot the merge would set aside), the merge takes the carry first and this master after it, so a carried record keeps a tie, as the store's record does in a plain direct merge; a master that is the --store-snapshot the carry took is not merged again",
    )
    parser.add_argument(
        "--store-snapshot",
        type=pathlib.Path,
        help="with --verdicts or --merge-master: the snapshot of the store whose file --autosave was linked from (`landing.snapshot`), as the snapshot step left it, so it may be missing. With --verdicts alone it is carried beside them when it exists. With --merge-master it is carried only when the merge would set that store aside, because the snapshot is stamped for an older corpus or is not an ams-review-verdicts/1 document: a snapshot stamped for an older corpus, or one that is not such a document but holds a readable `verdicts` list, is carried onto the corpus by unit id, its skips kept as tombstones, and the master merged into the result, so the store's verdicts are kept, and one the carry cannot read is refused by the carry, which names it (`carry_verdicts.resolve_prior`). A missing snapshot, or an ams-review-verdicts/1 document stamped for the corpus or a newer one, is not carried",
    )
    parser.add_argument("--autosave", type=pathlib.Path, default=AUTOSAVE)
    parser.add_argument(
        "--prepared-from",
        type=pathlib.Path,
        metavar="SNAPSHOT",
        help="the snapshot the cycle linked --autosave from (`landing.snapshot`): the update holds the store it prepares in memory from its first merge, writes it over --autosave once after its last merge, before the complaint list, when a merge changed it, journals nothing, and records beside --autosave whether it left the store as the snapshot's bytes, for the land (`landing.record_unchanged`), naming it on its completion line as r_unchanged",
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
        help=f"the journal every merge that writes the store appends to (default: the {JOURNAL_NAME} beside --autosave; a store not named {AUTOSAVE_NAME} needs --journal); not with --prepared-from, whose store the land journals",
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
    if args.store_snapshot is not None and not (args.verdicts or args.merge_master):
        parser.error("--store-snapshot needs --verdicts or --merge-master")
    if args.prepared_from is not None and args.journal is not None:
        parser.error(
            "--journal names where a hand run's merges journal; with --prepared-from the update journals nothing, since the land journals the store it lands"
        )

    try:
        journal = None if args.prepared_from is not None else args.journal or journal_path_for(args.autosave)
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
    fused: list[carry_verdicts.Carried] = []
    if carried:
        if args.carry_out is None and args.no_merge:
            parser.error("--verdicts needs --carry-out on a --no-merge run")
        carry_argv: list[str] = []
        for verdicts in carried:
            carry_argv += ["--verdicts", str(verdicts)]
        if args.carry_out is not None:
            carry_argv += ["--out", str(args.carry_out)]
        carry_argv += ["--current-corpus", str(corpus), "--served-corpus", str(args.served_corpus)]
        if args.allow_orphans:
            carry_argv.append("--allow-orphans")

        def carry() -> int | None:
            if store_carried:
                print(STORE_CARRIED_NOTE, flush=True)
            return carry_verdicts.main(
                carry_argv,
                current_units=units,
                current_ids=unit_ids,
                listed=listed,
                fused=fused if args.carry_out is None else None,
            )

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
        clears = _store_clears(stores, fused, units)
    if store_carried:
        assert snapshot is not None
        if master is not None and master.resolve() == snapshot.resolve():
            master = None
    inputs: list[merge_verdicts.Input] = []
    if carried and args.carry_out is not None:
        inputs.append(merge_verdicts.Input.file(args.carry_out))
    elif carried:
        assert len(fused) == 1
        inputs.append(merge_verdicts.Input("carry", records=fused[0].records, in_order=True))
    if master is not None:
        inputs.append(merge_verdicts.Input.file(master))
    store = _Store(autosave=args.autosave, corpus=corpus, journal=journal, listed=listed)
    if inputs or not args.autosave.exists():
        code = store.merge("merge", inputs, clears)
    else:
        code = _run("store", lambda: refuse_unreadable_store(args.autosave, listed))
    inputs.clear()
    fused.clear()
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
    first: list[dict] = []
    code = _run(
        "duplicate-fill",
        lambda: duplicate_verdicts.main(duplicate_argv, units=units, held=store.held, proposed=first),
    )
    if code:
        return code
    code = store.merge("duplicate-merge", _fills(args.duplicate_out, first))
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
    standing: list[dict] = []
    code = _run(
        "standing-fill",
        lambda: standing_verdicts.main(
            standing_argv,
            unit_source=lambda: unit_index.iter_human_units(corpus),
            held=store.held,
            proposed=standing,
        ),
    )
    if code:
        return code
    code = store.merge("standing-merge", _fills(args.standing_out, standing))
    standing.clear()
    if code:
        return code

    second: list[dict] = []
    code = _run(
        "duplicate-fill-2",
        lambda: duplicate_verdicts.main(
            duplicate_argv, units=units, held=store.held, proposed=second, write=False
        ),
    )
    if code:
        return code
    known = {record["unit"] for record in first}
    union = [*first, *(record for record in second if record["unit"] not in known)]
    _write_fills(args.duplicate_out, stamp, union)
    code = store.merge("duplicate-merge-2", _fills(args.duplicate_out, union))
    if code:
        return code
    unchanged = ""
    if args.prepared_from is not None:
        store.write()
        same = output_sink.same_bytes(args.autosave, args.prepared_from)
        landing.record_unchanged(args.autosave, same)
        unchanged = f" {R_UNCHANGED}={'true' if same else 'false'}"

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
                held=store.held,
            ),
        )
        if code:
            return code
    print(console.COMPLETE_LINE + "duplicate, standing, duplicate" + unchanged, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
