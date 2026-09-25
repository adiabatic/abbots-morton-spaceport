"""Seed and audit echo-group verdicts on the live review surface. An echo group (the unit JSON's `echo` field) is a set of human units whose localized before-to-after ink change is pixel-identical, with the same judged pair, class, configurations and follower shift. Unchanged letters around the change, including followers that only moved by the advance change, are not compared (`InkComparator.config_diff`). For every group with more than one member, this tool reads a verdicts file and does one of two things. When the recorded verdicts agree (`review_docket.verdicts_agree`: all the same, or a mix of approve and identical) and some members are blank, it writes fill records for the blanks, copied from the most recently recorded member, to an importable verdicts file. When the recorded verdicts disagree, it prints the group for a person to re-check. Skip verdicts count toward neither agreement nor blanks, so a skipped member is never filled. The app fills echo members as verdicts are recorded. This tool handles verdicts that never passed through the app's fill, such as carried verdicts, and audits the groups for consistency. The verdict chain (`rebuild/tools/verdict_chain.py`) runs it after the carry and merge, so blanks fill across cycles without a review session."""

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
from rebuild.tools.review_docket import verdicts_agree  # noqa: E402
from rebuild.tools.verdict_notes import cap_markers  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
OUT = ROOT / "verdicts-echo-fill.json"


def latest_verdicts(path):
    best = {}
    for record in json.loads(path.read_text())["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def echo_record(unit: Mapping[str, Any]) -> dict:
    """Return the fields echo fill keeps for a human unit: its id, echo group and notation (for the conflict report)."""
    return {"id": unit["id"], "echo": unit.get("echo"), "notation": unit.get("notation")}


def main(argv=None, *, units=None):
    """Write the echo fills and print the conflicts. `units` is the verdict chain's list of `echo_record` projections; without it, the human units are streamed from `--surface`. Only human units are read because the surface build requires every other unit to have a null echo."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(".")[0] + ".")
    parser.add_argument("verdicts", help="the verdicts file to seed from (an export or the autosave)")
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)

    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    data = json.loads(pathlib.Path(args.verdicts).read_text())
    if data.get("manifest_generated_at") != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the surface is "
            f"{manifest['generated_at']}; unit ids must never be joined across manifests — carry it forward first"
        )
    records = latest_verdicts(pathlib.Path(args.verdicts))

    groups = collections.defaultdict(list)
    for unit in unit_index.iter_human_units(surface) if units is None else units:
        if unit.get("echo"):
            groups[unit["echo"]].append(echo_record(unit))

    fills = []
    conflicts = []
    for echo_id, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        judged = [(unit, records[unit["id"]]) for unit in members if unit["id"] in records]
        judged = [(unit, record) for unit, record in judged if record["verdict"] != "skip"]
        blanks = [unit for unit in members if unit["id"] not in records]
        kinds = {record["verdict"] for _unit, record in judged}
        if not verdicts_agree(kinds):
            conflicts.append((echo_id, members, judged))
            continue
        if kinds and blanks:
            source_unit, source = max(judged, key=lambda pair: pair[1]["at"])
            note = cap_markers(f"[echo-fill from {source_unit['id']}] {source['note']}".strip())
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
    print(f"wrote {out.name}: {len(fills)} echo-fill verdicts onto manifest {manifest['generated_at']}")

    if conflicts:
        print()
        console.warn(
            f"{len(conflicts)} echo groups hold disagreeing verdicts — the same change judged differently; worth a re-check:"
        )
        for echo_id, members, judged in conflicts:
            ids = ",".join(unit["id"] for unit in members)
            print(f"  {echo_id}  #units={ids}")
            verdicted_ids = {unit["id"] for unit, _record in judged}
            for unit, record in judged:
                print(
                    f"    {unit['id']:9s} {unit['notation']:30s} {record['verdict']:9s} {record['note'][:70]}"
                )
            for unit in members:
                if unit["id"] not in verdicted_ids:
                    print(f"    {unit['id']:9s} {unit['notation']:30s} (blank)")
    else:
        print("no echo group holds disagreeing verdicts")
    return 0


if __name__ == "__main__":
    main()
