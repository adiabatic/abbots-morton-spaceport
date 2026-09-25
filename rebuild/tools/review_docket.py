"""Assemble the machine-readable docket data for the live surface: cluster the blank human units by the build's `cluster` signature (the echo key without the judged pair; see `_cluster_id` in rebuild/review/build.py), collect evidence from judged units with the same signature, list the ledger classes ruled intended, reviewed-approved or reviewed-rejected that still have blank units, and list the echo groups whose recorded verdicts disagree (`verdicts_agree` decides, and counts an approve/identical mix as agreement). The review app's `#view=docket` computes the same clustering live from the in-memory verdict store. This tool writes tmp/docket-data.json instead of a page: a fixed snapshot for writing bulk proposals, which need the blank membership fixed against one verdicts file."""

import argparse
import collections
import json
import pathlib
import sys
from collections.abc import Iterable, Mapping
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.review import unit_index  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
DATA_OUT = ROOT / "tmp/docket-data.json"
RULED_STATUSES = ("intended", "reviewed-approved", "reviewed-rejected")
TRANCHE_SIZE = 25
ACCEPTING_VERDICTS = frozenset({"approve", "either", "identical"})
ACCEPTING_MIX = frozenset({"approve", "identical"})


def verdicts_agree(verdicts):
    """Return whether the recorded verdicts on one echo group agree. They agree when they are all the same, or when they mix approve and identical: both accept the new rendering, and identical only adds that the highlighted part looks unchanged. `verdictsAgree` in rebuild/review/static/docket.js applies the same rule."""
    kinds = set(verdicts)
    return len(kinds) <= 1 or kinds == ACCEPTING_MIX


def triage_position(unit):
    """Return a sort key for the unit's place in the surface's triage index (the index record's `order`). A record without an `order` sorts after every ordered record, by id, so the order is total."""
    order = unit.get("order")
    return (order is None, order if isinstance(order, int) else 0, unit["id"])


def load_human_units(surface, *, fields: Iterable[str] | None = None):
    """Return the surface's human units in the slim index shape and the id of every unit on the surface. `rebuild.review.unit_index.load_human_units` does the projection, the classification and the fallback to the shards."""
    return unit_index.load_human_units(surface, fields=fields)


def latest_verdicts(path):
    best = {}
    for record in json.loads(path.read_text())["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def main(argv=None, *, units: Iterable[Mapping[str, Any]] | None = None):
    """Write the docket data. `units` lets a caller that already holds the surface's index records pass them instead of having this tool read them again. Only human records (`batch` not None) enter the docket either way."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument(
        "verdicts", help="the verdicts file for the current frontier (an export or the autosave)"
    )
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--data-out", default=str(DATA_OUT))
    args = parser.parse_args(argv)

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

    units = load_human_units(surface)[0] if units is None else units
    # Sorted by the index record's `order`, the order the app pages through and docket.js reads, so each cluster's exemplar and evidence samples are its earliest units in that order.
    human = sorted((unit for unit in units if unit["batch"] is not None), key=triage_position)
    unclustered = [unit["id"] for unit in human if not unit.get("cluster")]
    if unclustered:
        raise SystemExit(
            f"{len(unclustered)} human units carry no cluster signature — this surface predates the emission; "
            f"rebuild it with uv run python -m rebuild.review.build"
        )
    blanks = [unit for unit in human if unit["id"] not in records or records[unit["id"]]["verdict"] == "skip"]

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
        if not verdicts_agree(record["verdict"] for record in judged.values()):
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
