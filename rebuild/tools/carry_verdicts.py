"""Carry prior verdicts onto the live review corpus by unit id.

A unit's id is derived from its content key (`rebuild.review.unit_cache.unit_id_for`; `rebuild/REVIEW-PLAN.md` §2.1 gives the shape), so a verdict names the same unit on every corpus where that unit's content is unchanged. The carry is a join on that id. A unit takes its newest verdict across the `--verdicts` files unless the store among them cleared it at or after that verdict (`resolve_prior` gives the rule and how it tells the store), so a verdict the reader cleared in the store after another file recorded it is not carried back while the store keeps that tombstone, which it does until the unit is set again, a stamp change leaves the unit off the new corpus's human units, or the store file is replaced whole by a store that lacks it (`merge_verdicts --restore-as-of --apply` to a moment before the clear, or to a later one whose replay starts from a base event written after the clear with no `cleared` list, since a restore puts back the tombstones the journal replays to (`journal.replay_store`), or `rekey_verdicts --undo`, which puts back the store from before the re-key); another file's tombstones are passed over, as a merge passes over an input's. A verdict whose unit is a human unit on the new corpus is carried, and one whose unit is not on the corpus at all is orphaned. Skip verdicts and clears are neither carried nor counted as orphaned. A carried record keeps the prior record's verdict, note and `at`, except that the note loses the `[carried …]` markers in its leading marker run (`verdict_notes.drop_carried_markers`), which older notes hold and tabs and imports can bring back. No prior corpus is opened, and a verdicts file's stamp is only printed, so a file recorded against any older corpus carries the same way the autosave does. `artifact_cycle.carry_counts` parses the `carry counts:` line, and the cycle records those counts on its run line in the timings journal.

The carry is a stream. Each `--verdicts` file is read once for its tail, its head and its tombstones and override section after its records, a record held at a time (`accepted.read_tail`), which also finds the first record `verdict_store.well_formed` refuses, counts the units its records name and checks that they come in unit order, as every repo writer writes them. Then the files' records stream in step by unit (`accepted.iter_records`), joined with the corpus's human unit ids, sorted once and kept with each id's place in the index's order (`_Human`), and with the accepted list's ids, which the list holds in order, so the carry holds a record at a time and no file whole. A file whose records do not come in unit order or name their `verdicts` list twice, another file than the store whose override section holds a record, and a file the stream reader cannot read as an ams-review-verdicts/1 document is read whole instead, as `read_verdicts_file` reads it, which gives the same records and the same refusals. The counts and the refusals come from one walk before anything is written, and the `kinds:` line lists each verdict in the order the index's walk first meets it, from the places the sorted ids keep. With `--out` the carry streams the files again and writes the carried file there, the records sorted by unit, in the layout `json.dumps` gives with an indent of two; the verdict update instead fuses the carry into its first merge (`Carried`, through `main`'s `fused`), which streams the files a third time and takes the carried records as they come, so no carried file is written, and its line reads `carried <N> onto manifest <stamp>` with no file name.

The accepted list (`rebuild.review.accepted`, `--accepted`, by default rebuild/accepted-units.txt beside the store among the `--verdicts` files, and none when no file is the store) holds the units a close-out folded out of the store, which read as their accepted record wherever the store holds no override for them. Those need no carry: a listed id names its unit on every corpus where the unit's content is unchanged, and the readers look it up on the corpus they read. Nor does the store's override section, its records and tombstones on listed units (`accepted.Overrides`), which the carry passes over as it passes over the store's `accepted` field: the merge that takes the carried records keeps the section of the store the carry read, whole, whatever corpus its units are on (`merge_verdicts`). The carry refuses a store that does not pair with the list (`accepted.pair`), as every reader does. Another file's record on a listed unit, from its own records or its override section, is carried as any record is, unless the store holds no override for its unit and it equals the accepted record by value or is dated at or before the close-out (`Accepted.keeps_out`), so an export made before the close-out brings back no folded verdict while a note typed on an accepted unit since survives; the merge meets a carried record on a listed unit with the store's override by the newer `at`. In the orphan guard the store's override records other than skips on the served corpus's human units (`--served-corpus`, the live review corpus by default) count among the prior verdicts, as its own records do, and so do the listed units of those human units that no file's record and no override names, each counting among the orphaned when the new corpus lacks it; an override on a unit the served corpus lacks counts on neither side, since the section keeps it until the next close-out and it would otherwise count as orphaned on every pass; `accepted` counts the human units of the new corpus that read as accepted, a human unit holding an override record counts as matched and one holding an override tombstone as unmatched.

Unless `--allow-orphans` is passed, the carry refuses when more than `landing.ORPHAN_SHARE_LIMIT` of the prior verdicts other than skips are orphaned (`landing.orphan_share` says why). It refuses after printing its counts and before writing anything, because a carried file stamped for the new corpus can outrank the store in a later pass's choice of carry source.
"""

import argparse
import heapq
import itertools
import json
import pathlib
import sys
from array import array
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from operator import itemgetter
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review import accepted, unit_index  # noqa: E402
from rebuild.review.accepted import Accepted, Overrides  # noqa: E402
from rebuild.review.landing import (
    ORPHAN_SHARE_LIMIT,
    SNAPSHOT_NAME,
    orphan_share,
    read_manifest,
)  # noqa: E402
from rebuild.review.verdict_store import note_of, parse_cleared, well_formed  # noqa: E402
from rebuild.tools import held_store  # noqa: E402
from rebuild.tools.verdict_notes import drop_carried_markers  # noqa: E402

OUT = ROOT / "verdicts-carried-forward.json"
CURRENT_CORPUS = ROOT / "rebuild/out/review"
FORMAT = "ams-review-verdicts/1"


def latest_verdicts(payload):
    best = {}
    for record in payload["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


class UnreadableVerdicts(ValueError):
    """Raised by `read_verdicts_file` for a verdicts file the carry cannot read; the message is the file's path followed by why."""


def read_verdicts_file(verdict_file: pathlib.Path) -> dict[str, Any]:
    """Return the payload of `verdict_file`, or raise `UnreadableVerdicts` when it cannot be read, is not JSON, holds no `verdicts` list, or holds a record `verdict_store.well_formed` refuses. The carry reads a file whole through it when the file does not stream (`_read_prior`), and a hand-run verdict update with nothing to merge checks the store its fills would start from with it (`verdict_update.refuse_unreadable_store`)."""
    try:
        payload = json.loads(verdict_file.read_text())
    except OSError as error:
        problem = f"cannot be read ({error.strerror or error})"
    except ValueError as error:
        problem = f"is not JSON ({error})"
    else:
        if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
            problem = "holds no `verdicts` list"
        else:
            malformed = [record for record in accepted.records_of(payload) if not well_formed(record)]
            if not malformed:
                return payload
            problem = _malformed_problem(malformed[0])
    raise UnreadableVerdicts(f"{verdict_file} {problem}")


def _malformed_problem(record: object) -> str:
    return f"holds a verdict record that is not an object with a string `unit`, `verdict` and `at` and a `note`, if any, that is a string or false-valued, as `verdict_store.well_formed` requires ({json.dumps(record)})"


def _refusal(unreadable: UnreadableVerdicts) -> SystemExit:
    return SystemExit(
        f"carry refused: {unreadable}, and nothing was written. "
        "If it is the verdict store or the cycle's snapshot of it, restore a readable store from the journal with "
        "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`; "
        "otherwise name another verdicts file with --verdicts."
    )


@dataclass
class _Prior:
    """A `--verdicts` file as the carry reads it: its path, whether it is the store (named `SNAPSHOT_NAME`), how many units its records name, its tombstones, its override section when it is the store, and its records by unit, newest first and the first of equal ones kept, either held (`whole`, sorted, for a file read whole) or streamed again from the file each time they are walked."""

    path: pathlib.Path
    store: bool
    units: int
    cleared: dict[str, str] = field(default_factory=dict)
    overrides: Overrides = field(default_factory=Overrides)
    whole: list[tuple[str, dict]] | None = None

    def records(self) -> Iterator[tuple[str, dict]]:
        if self.whole is not None:
            return iter(self.whole)
        return ((record["unit"], record) for record in held_store.own_records(self.path, str(self.path)))


def _read_prior(verdict_file: pathlib.Path, listed: Accepted | None) -> _Prior:
    """Read `verdict_file` as `_Prior`, refusing it as `read_verdicts_file` does, and the store when it does not pair with `listed`, then print its line."""
    store = verdict_file.name == SNAPSHOT_NAME
    order = {"last": None, "units": 0, "in_order": True}

    def each(record: object) -> None:
        unit = record.get("unit") if isinstance(record, dict) else None
        if not isinstance(unit, str):
            return
        last = order["last"]
        if last is None or unit > last:
            order["last"] = unit
            order["units"] += 1
        elif unit < last:
            order["in_order"] = False

    tail = accepted.read_tail(verdict_file, each)
    section = tail.members.get(accepted.OVERRIDES) if tail is not None else None
    section_records = section.get("verdicts") if isinstance(section, Mapping) else None
    streams = (
        tail is not None
        and tail.lists == 1
        and order["in_order"]
        and (store or not (isinstance(section_records, list) and section_records))
    )
    if not streams:
        try:
            payload = read_verdicts_file(verdict_file)
        except UnreadableVerdicts as unreadable:
            raise _refusal(unreadable) from None
        document: Mapping = payload
        stamp = payload.get("manifest_generated_at")
    else:
        assert tail is not None
        malformed = [tail.malformed[1]] if tail.malformed is not None else []
        if not malformed and isinstance(section_records, list):
            malformed = [record for record in section_records if not well_formed(record)][:1]
        if malformed:
            raise _refusal(UnreadableVerdicts(f"{verdict_file} {_malformed_problem(malformed[0])}"))
        document = {**tail.members, "verdicts": [None] if tail.records else []}
        payload = None
        stamp = tail.members.get("manifest_generated_at")
    if store and listed is not None:
        try:
            accepted.pair(document, listed, store=verdict_file)
        except accepted.Mismatch as mismatch:
            raise SystemExit(f"carry refused: {mismatch}, and nothing was written.") from None
    prior = _Prior(verdict_file, store, order["units"])
    if store:
        prior.cleared = parse_cleared(dict(document))
        prior.overrides = Overrides.of(document)
    if payload is not None:
        held = latest_verdicts(payload if store else {"verdicts": accepted.records_of(payload)})
        prior.whole = sorted(held.items(), key=itemgetter(0))
        prior.units = len(held)
    print(f"{verdict_file.name}: {prior.units} verdicts, stamped {stamp}")
    return prior


def resolve_prior(verdict_files, listed: Accepted | None = None):
    """Return each unit id's newest verdict across all the files, by `at`, leaving out a unit the store cleared at or after that verdict. The store is a file named `SNAPSHOT_NAME`, the live store's name, which the cycle's snapshot of it keeps. Its tombstones (`verdict_store.parse_cleared`), and its override tombstones on listed units, are the clears, and a tombstone wins over a record whose `at` is not after the clear, as the store's tombstones win in a merge (`merge_verdicts.merge_into`). The store's tombstone on a unit it holds a record for is passed over, as the verdict store passes it over, and so is every other file's tombstone, as a merge passes over an input's: a clear that no store records would leave no tombstone for the land to keep. The store's override section is passed over, since the merge keeps it, and another file's override records are read with its own records (`accepted.records_of`). With `listed`, the accepted list, the store must pair with it (`accepted.pair`), and another file's record on a listed unit that the store holds no override for is left out when the list keeps it out (`Accepted.keeps_out`). A file that cannot be read, is not JSON, holds no `verdicts` list, or holds a record `verdict_store.well_formed` refuses, and a store that does not pair with the list, stops the carry with a refusal that names it, before anything is written. The carry walks the same rule as a stream (`_resolved`)."""
    priors = [_read_prior(pathlib.Path(path), listed) for path in verdict_files]
    return {record["unit"]: record for record in _resolved(priors, listed)}


def _resolved(
    priors: list[_Prior], listed: Accepted | None, skips: dict[str, str] | None = None
) -> Iterator[dict]:
    """Yield `resolve_prior`'s verdicts in unit order, walking the files' records in step by unit. With `skips`, record there, for each unit whose newest record among a store's own is a skip, that skip's `at`, the later one where two stores hold one, which the verdict update hands its first merge as tombstones (`verdict_update.skip_clears` gives the rule)."""
    overrides = _overrides_of(priors)
    listing = listed is not None and bool(listed.verdicts)
    stores = [(index, prior) for index, prior in enumerate(priors) if prior.store]
    if len(priors) == 1:
        groups: Iterator[tuple[tuple[str, int, dict], ...]] = (
            ((unit, 0, record),) for unit, record in priors[0].records()
        )
    else:
        streams = [_tagged(prior.records(), index) for index, prior in enumerate(priors)]
        groups = (
            tuple(group)
            for _unit, group in itertools.groupby(
                heapq.merge(*streams, key=itemgetter(0, 1)), key=itemgetter(0)
            )
        )
    for group in groups:
        unit = group[0][0]
        best, best_index = group[0][2], group[0][1]
        for _unit, index, record in group:
            if record["at"] > best["at"]:
                best, best_index = record, index
            if skips is not None and record["verdict"] == "skip" and priors[index].store:
                skipped: str = record["at"]
                skips[unit] = max(skipped, skips.get(unit, skipped))
        if listing and not priors[best_index].store and unit not in overrides:
            assert listed is not None
            if listed.keeps_out(best):
                continue
        tombstone = overrides.cleared.get(unit)
        if tombstone is None:
            for index, prior in stores:
                at = prior.cleared.get(unit)
                if at is not None and all(held != index for _unit, held, _record in group):
                    tombstone = at if tombstone is None else max(at, tombstone)
        if tombstone is not None and not best["at"] > tombstone:
            continue
        yield best


def _joined(
    resolved: Iterator[dict], human: Iterable[tuple[str, int]]
) -> Iterator[tuple[str, dict | None, int | None]]:
    """Yield each unit of `resolved`, the resolved records, and of `human`, the human ids with their places, in unit order, with its record and its place, either None where the unit has none."""
    human = iter(human)
    prior = next(resolved, None)
    held = next(human, None)
    while prior is not None or held is not None:
        if held is None or (prior is not None and prior["unit"] < held[0]):
            assert prior is not None
            yield prior["unit"], prior, None
            prior = next(resolved, None)
        elif prior is None or held[0] < prior["unit"]:
            yield held[0], None, held[1]
            held = next(human, None)
        else:
            yield held[0], prior, held[1]
            prior, held = next(resolved, None), next(human, None)


def _tagged(records: Iterator[tuple[str, dict]], index: int) -> Iterator[tuple[str, int, dict]]:
    for unit, record in records:
        yield unit, index, record


def _overrides_of(priors: list[_Prior]) -> Overrides:
    """Return the override section of the last store among `priors`, which the carry passes over and the merge keeps, or an empty one."""
    stores = [prior for prior in priors if prior.store]
    return stores[-1].overrides if stores else Overrides()


class _Human:
    """The corpus's human unit ids sorted, as one buffer of 13-byte ids with the ids of another width beside it, split by width at `held_store.ID_BYTES`, the table's id width, each with its place in the index's order."""

    def __init__(self, ids: list[str]) -> None:
        order = sorted(range(len(ids)), key=ids.__getitem__)
        self.count = len(ids)
        self._ids = bytearray()
        self._places = array("I")
        self._other: list[tuple[str, int]] = []
        for place in order:
            unit = ids[place]
            key = unit.encode("utf-8", "surrogatepass")
            if len(key) == held_store.ID_BYTES:
                self._ids += key
                self._places.append(place)
            else:
                self._other.append((unit, place))

    def __iter__(self) -> Iterator[tuple[str, int]]:
        width = held_store.ID_BYTES
        ids = self._ids
        sized = (
            (ids[at : at + width].decode("utf-8", "surrogatepass"), place)
            for at, place in zip(range(0, len(ids), width), self._places, strict=True)
        )
        return heapq.merge(sized, self._other) if self._other else sized


@dataclass
class Carried:
    """The carry the verdict update fuses into its first merge: the files it read, the human ids it joins them with, the corpus's stamp, how many records it carries, and the `at` of each store's own skip by unit, from its counting walk (`_resolved`'s `skips`). `records` streams the carried records in unit order each time it is called, as `main` writes them to a carried file."""

    priors: list[_Prior]
    human: _Human
    listed: Accepted | None
    stamp: str
    count: int
    skips: dict[str, str]

    def records(self) -> Iterator[dict]:
        for unit, prior, place in _joined(_resolved(self.priors, self.listed), self.human):
            if prior is None or place is None or prior["verdict"] == "skip":
                continue
            yield {
                "unit": unit,
                "verdict": prior["verdict"],
                "note": drop_carried_markers(note_of(prior)),
                "at": prior["at"],
            }


def _indented(head: Mapping[str, object], records: Iterable[Mapping]) -> Iterator[str]:
    """Yield the text `json.dumps(payload, ensure_ascii=False, indent=2)` gives for a payload of `head`'s members and then its `verdicts` list of `records`, a record at a time."""
    yield "{\n"
    for key, value in head.items():
        yield f"  {json.dumps(key, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)},\n"
    opened = False
    for record in records:
        yield ",\n    " if opened else '  "verdicts": [\n    '
        yield json.dumps(record, ensure_ascii=False, indent=2).replace("\n", "\n    ")
        opened = True
    yield "\n  ]\n}\n" if opened else '  "verdicts": []\n}\n'


def served_human_ids(served_corpus: pathlib.Path) -> frozenset[str]:
    """Return the served corpus's human unit ids, or none when no corpus is served yet."""
    try:
        return read_manifest(served_corpus)[1]
    except FileNotFoundError:
        return frozenset()


def _read_listed(path: pathlib.Path | None, verdict_files: list[pathlib.Path]) -> Accepted | None:
    """Return the accepted list at `path`, or else beside the store among `verdict_files` (`accepted.path_for`), or None when no file is the store; a malformed list stops the carry."""
    if path is None:
        stores = [verdict_file for verdict_file in verdict_files if verdict_file.name == SNAPSHOT_NAME]
        if not stores:
            return None
        path = accepted.path_for(stores[0])
    try:
        return accepted.read(path)
    except accepted.Malformed as malformed:
        raise SystemExit(f"carry refused: {malformed}, and nothing was written.") from None


def main(
    argv=None,
    *,
    current_units: Iterable[Mapping[str, Any]] | None = None,
    current_ids: set[str] | None = None,
    listed: Accepted | None = None,
    fused: list[Carried] | None = None,
):
    """Write the carried verdicts file for the current corpus, or hand the carry to the verdict update's first merge.

    `current_units` and `current_ids` are passed together or not at all. `current_units` is a single-pass stream of human unit records that need only an `id`, and `current_ids` holds every corpus id, machine units included, for the orphaned count. The verdict update passes its duplicate records; a standalone run streams the human index and collects the ids in the same pass. `listed` is the accepted list the verdict update read once for every step, in place of `--accepted`. With `fused`, the verdict update's list, the carry writes no file: it appends the carry to `fused` for the first merge to stream (`Carried`), and its line names no file.
    """
    parser = argparse.ArgumentParser(
        description="Carry prior verdicts onto the live corpus, landing each on the unit of the id it names."
    )
    parser.add_argument(
        "--verdicts",
        type=pathlib.Path,
        action="append",
        required=True,
        metavar="VERDICTS_JSON",
        help=f"a prior verdicts file; repeatable, the newest `at` per unit winning across files unless the store (the file named {SNAPSHOT_NAME}) cleared the unit at or after it",
    )
    parser.add_argument("--out", default=str(OUT), help="output verdicts file (default: %(default)s)")
    parser.add_argument(
        "--current-corpus",
        "--current-surface",
        type=pathlib.Path,
        default=CURRENT_CORPUS,
        help="the freshly built corpus to carry onto (default: the live review corpus)",
    )
    parser.add_argument(
        "--allow-orphans",
        action="store_true",
        help=f"write the carried file even when more than {ORPHAN_SHARE_LIMIT:.0%}% of the prior verdicts name no unit on the corpus",
    )
    parser.add_argument(
        "--accepted",
        type=pathlib.Path,
        help=f"the accepted list the store pairs with (default: rebuild/accepted-units.txt beside the --verdicts file named {SNAPSHOT_NAME}, and none when no file is)",
    )
    parser.add_argument(
        "--served-corpus",
        type=pathlib.Path,
        default=CURRENT_CORPUS,
        help="the corpus the review server serves, whose human units' accepted ids count among the prior verdicts (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if (current_units is None) != (current_ids is None):
        parser.error("current_units and current_ids are handed over together or not at all")

    if listed is None:
        listed = _read_listed(args.accepted, args.verdicts)
    priors = [_read_prior(verdict_file, listed) for verdict_file in args.verdicts]
    overrides = _overrides_of(priors)
    listed_ids = listed.verdicts if listed is not None else {}

    manifest = json.loads((args.current_corpus / "manifest.json").read_text())
    if current_units is None or current_ids is None:
        current_ids = set()
        current_units = unit_index.iter_human_units(args.current_corpus, unit_ids=current_ids)
    human = _Human([unit["id"] for unit in current_units])
    served = served_human_ids(args.served_corpus) if listed_ids else frozenset()

    carried = 0
    first: dict[str, int] = {}
    kinds: dict[str, int] = {}
    unmatched = accepted_count = overridden = 0
    effective = orphaned = listed_prior = 0
    overridden_in_prior: set[str] = set()
    for unit in listed_ids if served else ():
        if unit in served and unit not in overrides:
            listed_prior += 1
            orphaned += unit not in current_ids
    skips: dict[str, str] = {}
    for unit, record, place in _joined(_resolved(priors, listed, skips), human):
        if record is not None:
            if unit in overrides.records:
                overridden_in_prior.add(unit)
            if record["verdict"] != "skip":
                effective += 1
                orphaned += unit not in current_ids
            if unit in served and unit in listed_ids and unit not in overrides:
                listed_prior -= 1
                orphaned -= unit not in current_ids
        if place is None:
            continue
        if record is None:
            override = overrides.records.get(unit)
            if override is not None:
                overridden += override["verdict"] != "skip"
            elif unit in listed_ids and unit not in overrides:
                accepted_count += 1
            else:
                unmatched += 1
            continue
        if record["verdict"] == "skip":
            continue
        carried += 1
        verdict = record["verdict"]
        kinds[verdict] = kinds.get(verdict, 0) + 1
        first[verdict] = min(place, first.get(verdict, place))
    for unit, record in overrides.records.items():
        if unit in served and unit not in overridden_in_prior and record["verdict"] != "skip":
            effective += 1
            orphaned += unit not in current_ids
    human_count = human.count
    print(
        f"carry counts: human={human_count} matched={human_count - unmatched - accepted_count} unmatched={unmatched} orphaned={orphaned} accepted={accepted_count}"
    )
    share = orphan_share(orphaned, effective + listed_prior)
    if share is not None and not args.allow_orphans:
        no_carry = (
            "; "
            if listed_prior
            else ", or, in a cycle pass whose store holds no verdict other than skips and whose accepted list names no unit of the served corpus, pass --no-carry; "
        )
        raise SystemExit(
            f"carry refused: {share} prior verdicts name no unit on {args.current_corpus}, and nothing was written. "
            "If a change to the carry projection moved the units' content ids, re-key the verdicts onto the new ids first (rebuild/tools/rekey_verdicts.py); "
            "if the windows themselves changed, pass --allow-orphans. "
            "If a verdicts file other than the store (verdicts-autosave.json) is the wrong one, such as one on unit ids from before ids were content ids, name the right one with --verdicts"
            f"{no_carry}"
            "if the store's own verdicts are on such ids, pass --allow-orphans."
        )

    stamp = manifest["generated_at"]
    carry = Carried(priors, human, listed, stamp, carried, skips)
    if fused is not None:
        fused.append(carry)
        print(f"carried {carried} onto manifest {stamp}")
    else:
        out = pathlib.Path(args.out)
        head = {"format": FORMAT, "manifest_generated_at": stamp, "exported_at": stamp}
        tmp = out.with_name(out.name + ".tmp")
        with tmp.open("w") as written:
            written.writelines(_indented(head, carry.records()))
        tmp.replace(out)
        print(f"wrote {out.name}: {carried} carried onto manifest {stamp}")
    print(f"kinds: {dict(sorted(kinds.items(), key=lambda item: first[item[0]]))}")
    print(
        f"human queue: {human_count} -> {human_count - carried - overridden - accepted_count} still needing fresh verdicts"
    )
    return 0


if __name__ == "__main__":
    main()
