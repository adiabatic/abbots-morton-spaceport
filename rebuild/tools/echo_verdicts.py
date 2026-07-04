"""Seed and audit echo-group verdicts on the live review surface. An echo group (the unit JSON's `echo` field) is a set of human units whose before→after ink change is pixel-identical with the same judged pair, class, and configs — one visual question. This tool reads a verdicts file, and for every multi-member group: (a) when the recorded verdicts agree and some members are blank, emits fill records for the blanks into an importable verdicts file, and (b) when the recorded verdicts disagree, prints the group for a human re-check (the sharper successor to the 2026-06-28 jitter audit). The app fills echo siblings live as new verdicts land; this tool exists for verdicts recorded before the mechanism, and as a standing consistency audit."""

import argparse
import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SURFACE = ROOT / "rebuild/out/review"
OUT = ROOT / "verdicts-echo-fill.json"


def load_units(surface):
    units = []
    for path in sorted((surface / "units").glob("*.json")):
        units.extend(json.loads(path.read_text()))
    return units


def latest_verdicts(path):
    best = {}
    for record in json.loads(path.read_text())["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__.split(".")[0] + ".")
    parser.add_argument("verdicts", help="the verdicts file to seed from (an export or the autosave)")
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

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
    for unit in load_units(surface):
        if unit.get("echo"):
            groups[unit["echo"]].append(unit)

    fills = []
    conflicts = []
    for echo_id, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        judged = [(unit, records[unit["id"]]) for unit in members if unit["id"] in records]
        judged = [(unit, record) for unit, record in judged if record["verdict"] != "skip"]
        blanks = [unit for unit in members if unit["id"] not in records]
        kinds = {record["verdict"] for _unit, record in judged}
        if len(kinds) > 1:
            conflicts.append((echo_id, members, judged))
            continue
        if len(kinds) == 1 and blanks:
            source_unit, source = max(judged, key=lambda pair: pair[1]["at"])
            note = f"[echo-fill from {source_unit['id']}] {source['note']}".strip()
            for unit in blanks:
                fills.append({"unit": unit["id"], "verdict": source["verdict"], "note": note, "at": source["at"]})

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
        print(f"\n{len(conflicts)} echo groups hold disagreeing verdicts — the same change judged differently; worth a re-check:")
        for echo_id, members, judged in conflicts:
            ids = ",".join(unit["id"] for unit in members)
            print(f"  {echo_id}  #units={ids}")
            verdicted_ids = {unit["id"] for unit, _record in judged}
            for unit, record in judged:
                print(f"    {unit['id']:9s} {unit['notation']:30s} {record['verdict']:9s} {record['note'][:70]}")
            for unit in members:
                if unit["id"] not in verdicted_ids:
                    print(f"    {unit['id']:9s} {unit['notation']:30s} (blank)")
    else:
        print("no echo group holds disagreeing verdicts")


if __name__ == "__main__":
    main()
