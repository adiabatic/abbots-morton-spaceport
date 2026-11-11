"""Seed and audit duplicate-group verdicts on the live review corpus. A duplicate group (the unit JSON's `duplicate_group` field) is a set of human units whose localized before-to-after ink change is pixel-identical, with the same judged pair, class, configurations and follower shift. Unchanged letters around the change, including followers that only moved by the advance change, are not compared (`InkComparator.config_diff`). For every group with more than one member, this tool reads a verdicts file and does one of two things. When the recorded verdicts agree (`review_queue.verdicts_agree`: all the same, or a mix of approve and identical) and some members are blank, it writes fill records for the blanks, copied from the most recently recorded member, to an importable verdicts file. When the recorded verdicts disagree, it prints the group for a person to re-check. Skip verdicts count toward neither agreement nor blanks, so a skipped member is never filled. It reads the verdicts file's stamp from its head when the head settles it (`review_queue.verdicts_stamp`), so a file stamped for another corpus is refused without a whole parse and a document with a second top-level `manifest_generated_at` after its `verdicts` array, which no repo writer makes, is judged by the first, and it reads the file through its accepted list (`review_queue.latest_verdicts`): a member a close-out accepted is judged under its mark with an empty note, dated at the close-out's `closed_at`, a member with an override is judged by it, and a member with a tombstone and no record, an override tombstone among them, is blank, so a fill copied from an accepted member carries its marker alone and that time, and when the group's newest members are accepted ones, all dated `closed_at`, the first of them in corpus order is the source. A fill whose source is dated the list's `closed_at` leaves out the members with a tombstone, in either part of the store (`View.tombstone`): that time is the close-out's, not the approval's, whether the source is an accepted member or a record an earlier fill copied from one, and it would outdate a clear made before the close-out that the approval itself did not, so the merge would let the fill undo the clear. The store's override section does not cover that clear: a member cleared before the close-out is no listed id, since a tombstone takes an id out of the list, so its tombstone is the store's own, which the merge lets a newer fill replace. The app fills duplicate-group members as verdicts are recorded. This tool handles verdicts that never passed through the app's fill, such as carried verdicts, and audits the groups for consistency. The verdict update (`rebuild/tools/verdict_update.py`) runs it after the carry and merge, so blanks fill across cycles without a review session."""

import argparse
import collections
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.tools import console  # noqa: E402
from rebuild.review import unit_index  # noqa: E402
from rebuild.review.verdict_store import note_of  # noqa: E402
from rebuild.review.accepted import Accepted  # noqa: E402
from rebuild.tools.review_queue import (  # noqa: E402
    accepted_list,
    add_accepted_argument,
    latest_verdicts,
    verdicts_agree,
    verdicts_stamp,
)
from rebuild.tools.verdict_notes import cap_markers  # noqa: E402

CORPUS = ROOT / "rebuild/out/review"
OUT = ROOT / "verdicts-duplicate-fill.json"


def duplicate_record(unit: Mapping[str, Any]) -> dict:
    """Return the fields the duplicate fill keeps for a human unit: its id, duplicate group and notation (for the conflict report)."""
    return {
        "id": unit["id"],
        "duplicate_group": unit.get("duplicate_group"),
        "notation": unit.get("notation"),
    }


def main(argv=None, *, units=None, listed: Accepted | None = None):
    """Write the duplicate fills and print the conflicts. `units` is the verdict update's list of `duplicate_record` projections; without it, the human units are streamed from `--corpus`. Only human units are read because the corpus build requires every other unit to have a null duplicate group. `listed` is the accepted list when the caller already holds the one `--accepted` names, as the verdict update does; without it the list is read here (`review_queue.accepted_list`)."""
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
    file_stamp, data = verdicts_stamp(args.verdicts)
    if file_stamp != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {file_stamp} but the corpus is "
            f"{manifest['generated_at']}, so it has not been carried onto this corpus: it can hold skips, "
            "which the carry drops, and verdicts on units the corpus does not have, and it is missing the "
            "fills a cycle runs on this corpus; a cycle pass (make artifact-cycle) leaves the store carried "
            "onto the live corpus"
        )
    records = latest_verdicts(args.verdicts, listed, document=data)

    groups = collections.defaultdict(list)
    for unit in unit_index.iter_human_units(corpus) if units is None else units:
        if unit.get("duplicate_group"):
            groups[unit["duplicate_group"]].append(duplicate_record(unit))

    fills = []
    conflicts = []
    for group_id, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        judged = [(unit, records[unit["id"]]) for unit in members if unit["id"] in records]
        judged = [(unit, record) for unit, record in judged if record["verdict"] != "skip"]
        blanks = [unit for unit in members if unit["id"] not in records]
        kinds = {record["verdict"] for _unit, record in judged}
        if not verdicts_agree(kinds):
            conflicts.append((group_id, members, judged))
            continue
        if kinds and blanks:
            source_unit, source = max(judged, key=lambda pair: pair[1]["at"])
            if source["at"] == records.accepted.closed_at:
                blanks = [unit for unit in blanks if records.tombstone(unit["id"]) is None]
            note = cap_markers(f"[duplicate-fill from {source_unit['id']}] {note_of(source)}".strip())
            for unit in blanks:
                fills.append(
                    {"unit": unit["id"], "verdict": source["verdict"], "note": note, "at": source["at"]}
                )

    fills.sort(key=lambda record: record["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": fills,
    }
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out.name}: {len(fills)} duplicate-fill verdicts onto manifest {manifest['generated_at']}")

    if conflicts:
        print()
        console.warn(
            f"{len(conflicts)} duplicate groups hold disagreeing verdicts — the same change judged differently; worth a re-check:"
        )
        for group_id, members, judged in conflicts:
            ids = ",".join(unit["id"] for unit in members)
            print(f"  {group_id}  #units={ids}")
            verdicted_ids = {unit["id"] for unit, _record in judged}
            for unit, record in judged:
                print(
                    f"    {unit['id']:9s} {unit['notation']:30s} {record['verdict']:9s} {note_of(record)[:70]}"
                )
            for unit in members:
                if unit["id"] not in verdicted_ids:
                    print(f"    {unit['id']:9s} {unit['notation']:30s} (blank)")
    else:
        print("no duplicate group holds disagreeing verdicts")
    return 0


if __name__ == "__main__":
    main()
