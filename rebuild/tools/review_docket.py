"""Assemble the machine-readable docket data for the live surface: cluster the blank human units across echo groups by the build-emitted `cluster` signature (the echo key minus the judged pair — see rebuild/review/build.py's `_cluster_id`), collect evidence from judged units sharing a signature, list ledger classes already ruled intended/reviewed-approved/reviewed-rejected that still hold blank units, and list echo groups whose recorded verdicts disagree. The adjudication view itself lives in the review app — `#view=docket` computes this same clustering live against the in-memory verdict store — so this tool no longer renders a page; it writes tmp/docket-data.json as the pinned data feed for bulk-proposal authoring, which needs exact blank membership frozen against a specific verdicts file."""

import argparse
import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

SURFACE = ROOT / "rebuild/out/review"
DATA_OUT = ROOT / "tmp/docket-data.json"
RULED_STATUSES = ("intended", "reviewed-approved", "reviewed-rejected")
TRANCHE_SIZE = 25


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
    parser = argparse.ArgumentParser(description=__doc__.split(":")[0] + ".")
    parser.add_argument("verdicts", help="the verdicts file for the current frontier (an export or the autosave)")
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--data-out", default=str(DATA_OUT))
    args = parser.parse_args()

    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    verdicts_path = pathlib.Path(args.verdicts)
    data = json.loads(verdicts_path.read_text())
    if data.get("manifest_generated_at") != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the surface is "
            f"{manifest['generated_at']}; unit ids must never be joined across manifests — carry it forward first"
        )
    records = latest_verdicts(verdicts_path)

    units = load_units(surface)
    human = [unit for unit in units if unit["batch"] is not None]
    unclustered = [unit["id"] for unit in human if not unit.get("cluster")]
    if unclustered:
        raise SystemExit(
            f"{len(unclustered)} human units carry no cluster signature — this surface predates the emission; "
            f"rebuild it with uv run python -m rebuild.review.build"
        )
    blanks = [
        unit
        for unit in human
        if unit["id"] not in records or records[unit["id"]]["verdict"] == "skip"
    ]

    clusters_by_id = collections.defaultdict(list)
    for unit in blanks:
        clusters_by_id[unit["cluster"]].append(unit)

    evidence_by_id = collections.defaultdict(list)
    for unit in human:
        record = records.get(unit["id"])
        if record and record["verdict"] != "skip":
            evidence_by_id[unit["cluster"]].append((unit, record))

    clusters = []
    for cluster_id, members in clusters_by_id.items():
        groups = collections.defaultdict(list)
        for unit in members:
            groups[unit.get("echo") or unit["id"]].append(unit)
        echo_groups = [
            {
                "echo": echo,
                "unit_ids": [unit["id"] for unit in group],
                "notations": [unit["notation"] for unit in group],
            }
            for echo, group in sorted(groups.items())
        ]
        judged = evidence_by_id.get(cluster_id, [])
        counts = collections.Counter(record["verdict"] for _unit, record in judged)
        samples = [
            {"unit": unit["id"], "verdict": record["verdict"], "note": record["note"]}
            for unit, record in judged[:3]
        ]
        exemplar = members[0]
        clusters.append(
            {
                "id": cluster_id,
                "class": exemplar["class"],
                "configs": list(exemplar["configs"]),
                "size": len(members),
                "echo_groups": echo_groups,
                "exemplar": {
                    "id": exemplar["id"],
                    "notation": exemplar["notation"],
                    "summary": exemplar.get("summary"),
                },
                "evidence": {"counts": dict(counts.most_common()), "samples": samples},
            }
        )
    clusters.sort(key=lambda cluster: (-cluster["size"], cluster["class"], cluster["id"]))

    blank_by_class = collections.Counter(unit["class"] for unit in blanks)
    ruled = []
    for entry in manifest["classes"]:
        if entry["status"] in RULED_STATUSES and blank_by_class.get(entry["id"]):
            class_blanks = [unit for unit in blanks if unit["class"] == entry["id"]]
            ruled.append(
                {
                    "id": entry["id"],
                    "status": entry["status"],
                    "blank_count": len(class_blanks),
                    "echo_group_count": len({unit.get("echo") or unit["id"] for unit in class_blanks}),
                    "exemplar_ids": [unit["id"] for unit in class_blanks[:3]],
                }
            )
    ruled.sort(key=lambda entry: -entry["blank_count"])

    echo_members = collections.defaultdict(list)
    for unit in human:
        if unit.get("echo"):
            echo_members[unit["echo"]].append(unit)
    conflicts = []
    for echo, members in sorted(echo_members.items()):
        judged = {
            unit["id"]: records[unit["id"]]
            for unit in members
            if unit["id"] in records and records[unit["id"]]["verdict"] != "skip"
        }
        if len({record["verdict"] for record in judged.values()}) > 1:
            conflicts.append(
                {
                    "echo": echo,
                    "class": members[0]["class"],
                    "unit_ids": [unit["id"] for unit in members],
                    "verdicts": {unit_id: record["verdict"] for unit_id, record in judged.items()},
                }
            )

    ruled_ids = {entry["id"] for entry in ruled}
    multi = [cluster for cluster in clusters if cluster["size"] > 1 and cluster["class"] not in ruled_ids]
    tranche = multi[:TRANCHE_SIZE]

    docket_data = {
        "manifest_generated_at": manifest["generated_at"],
        "verdicts_file": verdicts_path.name,
        "totals": {
            "blank_units": len(blanks),
            "echo_groups": len({unit.get("echo") or unit["id"] for unit in blanks}),
            "clusters": len(clusters),
            "multi_clusters": sum(1 for cluster in clusters if cluster["size"] > 1),
            "singleton_clusters": sum(1 for cluster in clusters if cluster["size"] == 1),
            "ruled_units": sum(entry["blank_count"] for entry in ruled),
            "tranche_clusters": len(tranche),
            "tranche_units": sum(cluster["size"] for cluster in tranche),
        },
        "clusters": clusters,
        "ruled_classes": ruled,
        "conflicts": conflicts,
    }
    data_out = pathlib.Path(args.data_out)
    data_out.parent.mkdir(parents=True, exist_ok=True)
    data_out.write_text(json.dumps(docket_data, ensure_ascii=False, indent=1) + "\n")

    stale_page = surface / "docket.html"
    if stale_page.exists():
        stale_page.unlink()
        print(f"removed {stale_page} — the docket is now the app's #view=docket")

    totals = docket_data["totals"]
    print(
        f"wrote {data_out}: {totals['blank_units']} blank units in {totals['echo_groups']} echo groups → "
        f"{len(ruled)} class rulings ({totals['ruled_units']} units) + a {len(tranche)}-cluster tranche "
        f"({totals['tranche_units']} units) + {len(multi) - len(tranche)} later clusters + "
        f"{sum(1 for cluster in clusters if cluster['size'] == 1 and cluster['class'] not in ruled_ids)} singletons; "
        f"{len(conflicts)} echo groups disagree — adjudicate at #view=docket in the app"
    )


if __name__ == "__main__":
    main()
