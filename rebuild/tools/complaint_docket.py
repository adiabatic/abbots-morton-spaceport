"""Cluster the open complaints (reject/neither verdicts) on the live review surface by the rune records that decided them, so N complaints about one flaw collapse into one fix-worklist entry, and invert those pointers over the still-blank queue to list the lookalike windows worth parking until the fix lands. Rejects with a mechanical policy draft group by the draft's fix site (file + keypath); draftless rejects and all neithers group by their exact provenance-pointer tuple, with a neither strand attaching to the reject group whose pointer basis it overlaps most. Parking rides skip semantics: a park file is bulk skip verdicts (never echo-filling, blank-but-deferred in the docket) stamped with the manifest's generated_at so they never beat a human verdict, landed through the app's Import dialog; the next cycle's carry drops skips, so parked windows return to the queue exactly when the fix makes them worth re-judging. Writes tmp/complaints-data.json as the machine-readable feed; --park g-XXXXXXXX emits verdicts-park-*.json for a group."""

import argparse
import collections
import hashlib
import json
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.tools.review_docket import RULED_STATUSES, latest_verdicts, load_units  # noqa: E402
from rebuild.tools.verdict_notes import strip_markers  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
DATA_OUT = ROOT / "tmp/complaints-data.json"
COMPLAINT_KINDS = ("reject", "neither")
CHURN_KINDS = ("approve", "either", "identical")


def _unit_number(unit_id):
    try:
        return int(unit_id.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _marker_safe(text):
    return text.replace("[", "(").replace("]", ")")


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def _elide_why(suggested_record):
    try:
        record = yaml.safe_load(suggested_record)
    except yaml.YAMLError:
        return suggested_record.strip()
    if isinstance(record, dict) and "why" in record:
        record["why"] = None
        return yaml.safe_dump(record, default_flow_style=True).strip()
    return suggested_record.strip()


def _complaint_entry(unit, record):
    return {
        "unit": unit["id"],
        "class": unit["class"],
        "codepoints": unit["codepoints"],
        "notation": unit["notation"],
        "verdict": record["verdict"],
        "at": record["at"],
        "note": record["note"],
        "gist": strip_markers(record["note"]),
    }


def _scaffold(kind, key):
    if kind == "policy":
        basis_repr = f"{key[0]}|{key[1]}"
    else:
        basis_repr = "\n".join(key)
    return {
        "kind": kind,
        "key": key,
        "id": "g-" + hashlib.sha256(basis_repr.encode()).hexdigest()[:8],
        "rejects": [],
        "neithers": [],
        "basis": set(),
    }


def build_groups(complaints):
    groups = {}
    neither_pool = collections.defaultdict(list)
    for unit, record in complaints:
        pointers = tuple(sorted(set(unit.get("provenance") or [])))
        if record["verdict"] == "neither":
            neither_pool[pointers].append((unit, record))
            continue
        policy = (unit.get("drafts") or {}).get("policy")
        if policy:
            key = ("policy", (policy["file"], policy["keypath"]))
        elif pointers:
            key = ("provenance", pointers)
        else:
            key = ("unattributed", ())
        group = groups.setdefault(key, _scaffold(*key))
        group["rejects"].append((unit, record))
        group["basis"].update(pointers)
    reject_bases = {key: set(group["basis"]) for key, group in groups.items() if group["rejects"]}
    for pointers in sorted(neither_pool):
        members = neither_pool[pointers]
        if not pointers:
            key = ("unattributed", ())
            group = groups.setdefault(key, _scaffold(*key))
            group["neithers"].extend(members)
            continue
        overlaps = [
            (len(set(pointers) & reject_bases[key]), len(groups[key]["rejects"]), groups[key]["id"], key)
            for key in reject_bases
            if set(pointers) & reject_bases[key]
        ]
        if overlaps:
            overlaps.sort(key=lambda item: (-item[0], -item[1], item[2]))
            target = groups[overlaps[0][3]]
        else:
            key = ("provenance", pointers)
            target = groups.setdefault(key, _scaffold(*key))
        target["neithers"].extend(members)
        target["basis"].update(pointers)
    return groups


def _park_naming(group):
    if group["kind"] == "unattributed":
        return None, None
    if group["kind"] == "policy":
        file, keypath = group["key"]
        name = pathlib.Path(file).name
        slug = _slug(f"{pathlib.Path(file).stem} {keypath}")
        marker_target = _marker_safe(f"{name} {keypath}")
    else:
        first = group["key"][0]
        slug = _slug(f"{pathlib.Path(first.split(':', 1)[0]).stem} {first.split(':', 1)[1]}")
        marker_target = f"{group['id']} {_marker_safe(first)}"
    return f"verdicts-park-{slug}-{group['id'][2:]}.json", marker_target


def _split_by_freshness(members, threshold):
    entries = [_complaint_entry(unit, record) for unit, record in members]
    entries.sort(key=lambda entry: (entry["at"], _unit_number(entry["unit"])), reverse=True)
    return {
        "fresh": [entry for entry in entries if entry["at"] >= threshold],
        "standing": [entry for entry in entries if entry["at"] < threshold],
    }


def finalize_groups(groups, *, threshold, human, records, ruled_ids):
    prov_sets = {unit["id"]: frozenset(unit.get("provenance") or []) for unit in human}
    blanks = [
        unit
        for unit in human
        if unit["id"] not in records or records[unit["id"]]["verdict"] == "skip"
    ]
    finalized = []
    naming = {}
    for group in groups.values():
        basis = group["basis"]
        park_file, marker_target = _park_naming(group)
        candidates, ruled_blank = [], []
        churn = collections.Counter()
        if basis:
            for unit in blanks:
                if prov_sets[unit["id"]] & basis:
                    (ruled_blank if unit["class"] in ruled_ids else candidates).append(unit)
            for unit in human:
                record = records.get(unit["id"])
                if record and record["verdict"] in CHURN_KINDS and prov_sets[unit["id"]] & basis:
                    churn[record["verdict"]] += 1
        candidates.sort(key=lambda unit: _unit_number(unit["id"]))
        suggested = sorted(
            {
                _elide_why(policy["suggested_record"])
                for unit, _record in group["rejects"]
                if (policy := (unit.get("drafts") or {}).get("policy"))
            }
        )
        if group["kind"] == "policy":
            target = {"file": group["key"][0], "keypath": group["key"][1]}
        else:
            target = {"pointers": list(group["key"])}
        member_entries = group["rejects"] + group["neithers"]
        finalized.append(
            {
                "id": group["id"],
                "kind": group["kind"],
                "target": target,
                "pointers": sorted(basis),
                "classes": sorted({unit["class"] for unit, _record in member_entries}),
                "rejects": _split_by_freshness(group["rejects"], threshold),
                "neithers": _split_by_freshness(group["neithers"], threshold),
                "suggested_records": suggested,
                "draft_conflicts": len(suggested) > 1,
                "park_candidates": {
                    "count": len(candidates),
                    "unit_ids": [unit["id"] for unit in candidates],
                    "echo_groups": len({unit.get("echo") or unit["id"] for unit in candidates}),
                    "by_class": dict(collections.Counter(unit["class"] for unit in candidates)),
                },
                "ruled_class_blanks": {
                    "count": len(ruled_blank),
                    "by_class": dict(collections.Counter(unit["class"] for unit in ruled_blank)),
                },
                "churn_if_fixed": {kind: churn.get(kind, 0) for kind in CHURN_KINDS},
                "shares_pointers_with": [],
                "park_file": park_file,
            }
        )
        naming[finalized[-1]["id"]] = marker_target
    for group in finalized:
        mine = set(group["pointers"])
        group["shares_pointers_with"] = sorted(
            other["id"] for other in finalized if other["id"] != group["id"] and mine & set(other["pointers"])
        )
    finalized.sort(
        key=lambda group: (
            group["kind"] == "unattributed",
            -(len(group["rejects"]["fresh"]) + len(group["neithers"]["fresh"])),
            -(sum(len(part) for part in group["rejects"].values()) + sum(len(part) for part in group["neithers"].values())),
            group["id"],
        )
    )
    return finalized, naming


def emit_park(group, marker_target, *, stamp, park_dir, note_text):
    marker = f"[parked: {marker_target} — docket {stamp}]"
    note = f"{marker} {note_text}" if note_text else marker
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": [
            {"unit": unit_id, "verdict": "skip", "note": note, "at": stamp}
            for unit_id in group["park_candidates"]["unit_ids"]
        ],
    }
    path = park_dir / group["park_file"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split(":")[0] + ".")
    parser.add_argument(
        "verdicts",
        nargs="?",
        default=str(AUTOSAVE),
        help="the verdicts file to cluster (default: the live autosave)",
    )
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--data-out", default=str(DATA_OUT))
    parser.add_argument(
        "--since",
        default=None,
        help="fresh/standing threshold as an ISO-8601 stamp (default: the manifest's generated_at)",
    )
    parser.add_argument(
        "--park",
        action="append",
        default=[],
        metavar="GROUP_ID",
        help="emit a verdicts-park-*.json of skip verdicts covering this group's park candidates; repeatable",
    )
    parser.add_argument("--park-dir", default=str(ROOT), help="where park files are written")
    parser.add_argument("--note", default="", help="verbatim reviewer text appended after the marker in every parked record")
    args = parser.parse_args(argv)

    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    stamp = manifest["generated_at"]
    verdicts_path = pathlib.Path(args.verdicts)
    data = json.loads(verdicts_path.read_text())
    if data.get("manifest_generated_at") != stamp:
        print(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the surface is "
            f"{stamp}; unit ids must never be joined across manifests — carry it forward first",
            file=sys.stderr,
        )
        return 1
    records = latest_verdicts(verdicts_path)

    units = load_units(surface)
    units_by_id = {unit["id"]: unit for unit in units}
    unknown = sum(1 for unit_id in records if unit_id not in units_by_id)
    if unknown:
        print(f"warning: {unknown} verdict records name units absent from this surface", file=sys.stderr)
    human = [unit for unit in units if unit.get("batch") is not None and not unit.get("no_verdict")]
    human_ids = {unit["id"] for unit in human}
    ruled_ids = {entry["id"] for entry in manifest.get("classes", []) if entry.get("status") in RULED_STATUSES}

    complaints = [
        (units_by_id[unit_id], record)
        for unit_id, record in sorted(records.items(), key=lambda item: _unit_number(item[0]))
        if record["verdict"] in COMPLAINT_KINDS and unit_id in human_ids
    ]
    threshold = args.since or stamp
    groups, naming = finalize_groups(
        build_groups(complaints), threshold=threshold, human=human, records=records, ruled_ids=ruled_ids
    )

    fresh = sum(len(group["rejects"]["fresh"]) + len(group["neithers"]["fresh"]) for group in groups)
    park_union = {unit_id for group in groups for unit_id in group["park_candidates"]["unit_ids"]}
    ruled_blank_total = sum(group["ruled_class_blanks"]["count"] for group in groups)
    approved_sharing = sum(group["churn_if_fixed"]["approve"] for group in groups)
    payload = {
        "manifest_generated_at": stamp,
        "verdicts_file": verdicts_path.name,
        "since": threshold,
        "totals": {
            "complaints": len(complaints),
            "rejects": sum(1 for _unit, record in complaints if record["verdict"] == "reject"),
            "neithers": sum(1 for _unit, record in complaints if record["verdict"] == "neither"),
            "fresh": fresh,
            "standing": len(complaints) - fresh,
            "groups": len(groups),
            "park_candidates": len(park_union),
            "ruled_class_blanks": ruled_blank_total,
            "approved_sharing": approved_sharing,
        },
        "groups": groups,
    }
    data_out = pathlib.Path(args.data_out)
    data_out.parent.mkdir(parents=True, exist_ok=True)
    data_out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")

    if not complaints:
        print("no open complaints")
    else:
        totals = payload["totals"]
        print(
            f"wrote {data_out}: {totals['complaints']} open complaints "
            f"({totals['fresh']} fresh / {totals['standing']} standing) in {totals['groups']} groups — "
            f"{totals['park_candidates']} park candidates, "
            f"{totals['approved_sharing']} approved sharers likely churn if fixed"
        )

    if args.park:
        by_id = {group["id"]: group for group in groups}
        park_dir = pathlib.Path(args.park_dir)
        for group_id in args.park:
            group = by_id.get(group_id)
            if group is None:
                known = ", ".join(sorted(by_id)) or "none"
                print(f"unknown group id {group_id} (known: {known})", file=sys.stderr)
                return 1
            if group["park_file"] is None or not group["park_candidates"]["count"]:
                print(f"{group_id} has no park candidates — nothing to emit", file=sys.stderr)
                return 1
            path = emit_park(group, naming[group_id], stamp=stamp, park_dir=park_dir, note_text=args.note)
            print(
                f"parked {group['park_candidates']['count']} units -> {path} — "
                f"land it through the app's Import dialog"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
