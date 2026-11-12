"""Seed and audit duplicate-group verdicts on the live review corpus. A duplicate group (the unit JSON's `duplicate_group` field) is a set of human units whose localized before-to-after ink change is pixel-identical, with the same judged pair, class, configurations and follower shift. Unchanged letters around the change, including followers that only moved by the advance change, are not compared (`InkComparator.config_diff`). For every group with more than one member, this tool reads a verdicts file and does one of two things. When the recorded verdicts agree (`review_queue.verdicts_agree`: all the same, or a mix of approve and identical) and some members are blank, it writes fill records for the blanks, copied from the most recently recorded member, to an importable verdicts file. When the recorded verdicts disagree, it prints the group for a person to re-check. Skip verdicts count toward neither agreement nor blanks, so a skipped member is never filled. It reads the verdicts file's stamp from its head when the head settles it (`review_queue.verdicts_stamp`), so a file stamped for another corpus is refused without a whole parse and a document with a second top-level `manifest_generated_at` after its `verdicts` array, which no repo writer makes, is judged by the first, and it reads the file through its accepted list (`review_queue.latest_verdicts`): a member a close-out accepted is judged under its mark with an empty note, dated at the close-out's `closed_at`, a member with an override is judged by it, and a member with a tombstone and no record, an override tombstone among them, is blank, so a fill copied from an accepted member carries its marker alone and that time, and when the group's newest members are accepted ones, all dated `closed_at`, the first of them in corpus order is the source. A fill whose source is dated the list's `closed_at` leaves out the members with a tombstone, in either part of the store (`View.tombstone`): that time is the close-out's, not the approval's, whether the source is an accepted member or a record an earlier fill copied from one, and it would outdate a clear made before the close-out that the approval itself did not, so the merge would let the fill undo the clear. The store's override section does not cover that clear: a member cleared before the close-out is no listed id, since a tombstone takes an id out of the list, so its tombstone is the store's own, which the merge lets a newer fill replace. The app fills duplicate-group members as verdicts are recorded. This tool handles verdicts that never passed through the app's fill, such as carried verdicts, and audits the groups for consistency. The verdict update (`rebuild/tools/verdict_update.py`) runs it after the carry and merge, over the store it holds in memory (`review_queue.HeldStore`) and the human units' `Projection` its first walk of the index made, and merges the fills it is handed back without reading the fill file, so blanks fill across cycles without a review session."""

import argparse
import json
import pathlib
import sys
from array import array
from collections.abc import Iterable, Iterator, Mapping, Set
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.tools import console  # noqa: E402
from rebuild.review import unit_index  # noqa: E402
from rebuild.review.unit_cache import base58_64, id_word  # noqa: E402
from rebuild.review.verdict_store import note_of  # noqa: E402
from rebuild.review.accepted import Accepted  # noqa: E402
from rebuild.tools.review_queue import (  # noqa: E402
    HeldStore,
    accepted_list,
    add_accepted_argument,
    latest_verdicts,
    verdicts_agree,
    verdicts_stamp,
)
from rebuild.tools.verdict_notes import cap_markers  # noqa: E402

CORPUS = ROOT / "rebuild/out/review"
OUT = ROOT / "verdicts-duplicate-fill.json"


NO_GROUP = 0xFFFF_FFFF


class Projection:
    """What the duplicate fills, their conflict report and the carry keep of the human units, in shard order, in `array`s, 12 bytes a unit: its group's ordinal (`group_of`, an index into `group_ids`, the distinct `duplicate_group` ids in the order the walk meets them, or `NO_GROUP` for a unit with none) and its id's 64-bit word (`words`, `unit_cache.id_word`). An id `id_word` refuses keeps its string in `ids`, keyed by the unit's ordinal. A unit's id is rebuilt from its word (`unit_cache.base58_64`, which `id_word` inverts)."""

    def __init__(self) -> None:
        self.group_of = array("I")
        self.words = array("Q")
        self.group_ids: list[str] = []
        self._group_ordinals: dict[str, int] = {}
        self.ids: dict[int, str] = {}

    @classmethod
    def of(cls, units: Iterable[Mapping[str, Any]]) -> "Projection":
        projection = cls()
        for unit in units:
            projection.add(unit)
        return projection

    def add(self, unit: Mapping[str, Any]) -> None:
        word = id_word(unit["id"])
        if word is None:
            self.ids[len(self.words)] = unit["id"]
        self.words.append(word or 0)
        group = unit.get("duplicate_group")
        if group:
            index = self._group_ordinals.get(group)
            if index is None:
                index = self._group_ordinals[group] = len(self.group_ids)
                self.group_ids.append(group)
            self.group_of.append(index)
        else:
            self.group_of.append(NO_GROUP)

    def __len__(self) -> int:
        return len(self.words)

    def unit_id(self, ordinal: int) -> str:
        escaped = self.ids.get(ordinal)
        return escaped if escaped is not None else "u-" + base58_64(f"{self.words[ordinal]:016x}")

    def groups(self) -> Iterator[tuple[str, array]]:
        """Yield each group's id and its members' ordinals, the groups in sorted id order and each group's members in shard order: a stable counting sort over the group ordinals."""
        count = len(self.group_ids)
        starts = array("I", bytes(4 * (count + 1)))
        for group in self.group_of:
            if group != NO_GROUP:
                starts[group + 1] += 1
        for group in range(count):
            starts[group + 1] += starts[group]
        members = array("I", bytes(4 * starts[count]))
        cursor = array("I", starts)
        for ordinal, group in enumerate(self.group_of):
            if group != NO_GROUP:
                members[cursor[group]] = ordinal
                cursor[group] += 1
        for group in sorted(range(count), key=self.group_ids.__getitem__):
            yield self.group_ids[group], members[starts[group] : starts[group + 1]]


def _notations(corpus: pathlib.Path, wanted: Set[str]) -> dict[str, str]:
    """Return the index's notation of each human unit whose id is in `wanted`, from one walk of the human units, which the conflict report takes for its captions."""
    return {
        unit["id"]: unit["notation"]
        for unit in unit_index.iter_human_units(corpus, fields=("id", "notation"))
        if unit["id"] in wanted
    }


def main(
    argv=None,
    *,
    projection: Projection | None = None,
    listed: Accepted | None = None,
    held: HeldStore | None = None,
    proposed: list[dict] | None = None,
    write: bool = True,
):
    """Write the duplicate fills and print the conflicts. `projection` is the human units' `Projection` the verdict update made in its first walk of the index, which both its duplicate fills read; without it, one is made here from the human units streamed from `--corpus`. Only human units are read because the corpus build requires every other unit to have a null duplicate group. The groups are visited in sorted id order, each one's members in shard order (`Projection.groups`). When groups disagree, the report's captions are the members' notations as the index holds them, read in one more walk of the human units, which a pass with no group in disagreement does not take. `listed` is the accepted list when the caller already holds the one `--accepted` names, as the verdict update does; without it the list is read here (`review_queue.accepted_list`). `held` is the store the verdict update holds in memory (`review_queue.HeldStore`), read in place of the verdicts file, which it names; `proposed`, when given, receives the fills, sorted by unit, for the caller's merge; and without `write` the fill file is not written, though its line is printed, for a caller that writes it itself before anyone reads it."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(".")[0] + ".")
    parser.add_argument("verdicts", help="the verdicts file to seed from (an export or the autosave)")
    parser.add_argument("--corpus", "--surface", default=str(CORPUS))
    parser.add_argument("--out", default=str(OUT))
    add_accepted_argument(parser)
    args = parser.parse_args(argv)
    if listed is None:
        listed = accepted_list(parser, args.accepted, args.verdicts)

    corpus = pathlib.Path(args.corpus)
    manifest = json.loads((corpus / "manifest.json").read_text())
    file_stamp, data = (held.stamp, None) if held is not None else verdicts_stamp(args.verdicts)
    if file_stamp != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {file_stamp} but the corpus is "
            f"{manifest['generated_at']}, so it has not been carried onto this corpus: it can hold skips, "
            "which the carry drops, and verdicts on units the corpus does not have, and it is missing the "
            "fills a cycle runs on this corpus; a cycle pass (make artifact-cycle) leaves the store carried "
            "onto the live corpus"
        )
    records = held.view(listed) if held is not None else latest_verdicts(args.verdicts, listed, document=data)

    if projection is None:
        projection = Projection.of(unit_index.iter_human_units(corpus))

    fills = []
    conflicts = []
    for group_id, members in projection.groups():
        if len(members) < 2:
            continue
        ids = [projection.unit_id(member) for member in members]
        judged = [(member, records[unit_id]) for member, unit_id in zip(members, ids) if unit_id in records]
        judged = [(member, record) for member, record in judged if record["verdict"] != "skip"]
        blanks = [unit_id for unit_id in ids if unit_id not in records]
        kinds = {record["verdict"] for _member, record in judged}
        if not verdicts_agree(kinds):
            conflicts.append((group_id, members, judged))
            continue
        if kinds and blanks:
            source_member, source = max(judged, key=lambda pair: pair[1]["at"])
            if source["at"] == records.accepted.closed_at:
                blanks = [unit_id for unit_id in blanks if records.tombstone(unit_id) is None]
            source_id = projection.unit_id(source_member)
            note = cap_markers(f"[duplicate-fill from {source_id}] {note_of(source)}".strip())
            for unit_id in blanks:
                fills.append(
                    {"unit": unit_id, "verdict": source["verdict"], "note": note, "at": source["at"]}
                )

    fills.sort(key=lambda record: record["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": fills,
    }
    out = pathlib.Path(args.out)
    if write:
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if proposed is not None:
        proposed.extend(fills)
    print(f"wrote {out.name}: {len(fills)} duplicate-fill verdicts onto manifest {manifest['generated_at']}")

    if conflicts:
        notations = _notations(
            corpus,
            {projection.unit_id(member) for _group_id, members, _judged in conflicts for member in members},
        )
        print()
        console.warn(
            f"{len(conflicts)} duplicate groups hold disagreeing verdicts — the same change judged differently; worth a re-check:"
        )
        for group_id, members, judged in conflicts:
            print(f"  {group_id}  #units={','.join(projection.unit_id(member) for member in members)}")
            verdicted = {member for member, _record in judged}
            for member, record in judged:
                unit_id = projection.unit_id(member)
                print(
                    f"    {unit_id:9s} {notations[unit_id]:30s} {record['verdict']:9s} {note_of(record)[:70]}"
                )
            for member in members:
                if member not in verdicted:
                    unit_id = projection.unit_id(member)
                    print(f"    {unit_id:9s} {notations[unit_id]:30s} (blank)")
    else:
        print("no duplicate group holds disagreeing verdicts")
    return 0


if __name__ == "__main__":
    main()
