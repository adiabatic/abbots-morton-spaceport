"""Group the open complaints (reject and neither verdicts) on the live review corpus by the rune records that decided them, and list the blank units those records also decide as defer candidates: each group becomes one entry in the fix worklist, and its defer candidates can be set aside until the fix is committed.

A reject with a policy draft is grouped by its draft target (file and keypath). A reject without a draft is grouped by its exact tuple of provenance pointers, and complaints with no pointers form one unattributed group. Neithers are collected by pointer tuple too, and each tuple joins the reject group whose pointers overlap it most, or forms its own group when none overlaps.

Deferring uses skip verdicts. A defer file holds one skip verdict per defer candidate, with `at` set to the manifest's `generated_at`, so any verdict the user records on this corpus is newer and wins. The echo fill ignores skips, and the review queue counts a skipped unit as blank but defers its echo group. The user imports the file through the app's Import dialog. The carry drops skip verdicts, so deferred units return to the blank queue on the first cycle that rebuilds the corpus.

Writes tmp/complaints-data.json. `--defer g-XXXXXXXX` also writes a verdicts-deferred-*.json for that group.
"""

import argparse
import collections
import hashlib
import json
import pathlib
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.tools.review_queue import (  # noqa: E402
    ACCEPTING_VERDICTS,
    RULED_STATUSES,
    latest_verdicts,
)
from rebuild.review.unit_index import iter_human_units  # noqa: E402
from rebuild.tools.verdict_notes import strip_markers  # noqa: E402

CORPUS = ROOT / "rebuild/out/review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
DATA_OUT = ROOT / "tmp/complaints-data.json"
COMPLAINT_KINDS = ("reject", "neither")
AT_RISK_KINDS = tuple(sorted(ACCEPTING_VERDICTS))


def _triage_position(unit):
    """Return the unit's position in the corpus's triage index (the record's `order`), which orders lookalikes in the complaint list. A record without an order sorts last."""
    order = unit.get("order")
    return order if isinstance(order, int) else sys.maxsize


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
        key_repr = f"{key[0]}|{key[1]}"
    else:
        key_repr = "\n".join(key)
    return {
        "kind": kind,
        "key": key,
        "id": "g-" + hashlib.sha256(key_repr.encode()).hexdigest()[:8],
        "rejects": [],
        "neithers": [],
        "provenance_pointers": set(),
    }


def build_groups(complaints):
    groups = {}
    neither_pool = collections.defaultdict(list)
    for unit, record in complaints:
        pointers = tuple(sorted(set(unit.get("provenance") or [])))
        if record["verdict"] == "neither":
            neither_pool[pointers].append((unit, record))
            continue
        policy = unit.get("policy")
        if policy:
            key = ("policy", (policy["file"], policy["keypath"]))
        elif pointers:
            key = ("provenance", pointers)
        else:
            key = ("unattributed", ())
        group = groups.setdefault(key, _scaffold(*key))
        group["rejects"].append((unit, record))
        group["provenance_pointers"].update(pointers)
    reject_pointers = {
        key: set(group["provenance_pointers"]) for key, group in groups.items() if group["rejects"]
    }
    for pointers in sorted(neither_pool):
        members = neither_pool[pointers]
        if not pointers:
            key = ("unattributed", ())
            group = groups.setdefault(key, _scaffold(*key))
            group["neithers"].extend(members)
            continue
        overlaps = [
            (len(set(pointers) & reject_pointers[key]), len(groups[key]["rejects"]), groups[key]["id"], key)
            for key in reject_pointers
            if set(pointers) & reject_pointers[key]
        ]
        if overlaps:
            overlaps.sort(key=lambda item: (-item[0], -item[1], item[2]))
            target = groups[overlaps[0][3]]
        else:
            key = ("provenance", pointers)
            target = groups.setdefault(key, _scaffold(*key))
        target["neithers"].extend(members)
        target["provenance_pointers"].update(pointers)
    return groups


def _defer_naming(group):
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
    return f"verdicts-deferred-{slug}-{group['id'][2:]}.json", marker_target


def _split_by_age(members, threshold):
    entries = [(_complaint_entry(unit, record), _triage_position(unit)) for unit, record in members]
    entries.sort(key=lambda item: (item[0]["at"], item[1]), reverse=True)
    entries = [entry for entry, _position in entries]
    return {
        "new": [entry for entry in entries if entry["at"] >= threshold],
        "older": [entry for entry in entries if entry["at"] < threshold],
    }


def finalize_groups(groups, *, threshold, human, records, ruled_ids):
    prov_sets = {unit["id"]: frozenset(unit.get("provenance") or []) for unit in human}
    blanks = [unit for unit in human if unit["id"] not in records or records[unit["id"]]["verdict"] == "skip"]
    finalized = []
    naming = {}
    for group in groups.values():
        provenance_pointers = group["provenance_pointers"]
        defer_file, marker_target = _defer_naming(group)
        candidates, ruled_blank = [], []
        at_risk = collections.Counter()
        if provenance_pointers:
            for unit in blanks:
                if prov_sets[unit["id"]] & provenance_pointers:
                    (ruled_blank if unit["class"] in ruled_ids else candidates).append(unit)
            for unit in human:
                record = records.get(unit["id"])
                if (
                    record
                    and record["verdict"] in AT_RISK_KINDS
                    and prov_sets[unit["id"]] & provenance_pointers
                ):
                    at_risk[record["verdict"]] += 1
        candidates.sort(key=_triage_position)
        suggested = sorted(
            {
                _elide_why(policy["suggested_record"])
                for unit, _record in group["rejects"]
                if (policy := unit.get("policy"))
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
                "pointers": sorted(provenance_pointers),
                "classes": sorted({unit["class"] for unit, _record in member_entries}),
                "rejects": _split_by_age(group["rejects"], threshold),
                "neithers": _split_by_age(group["neithers"], threshold),
                "suggested_records": suggested,
                "draft_conflicts": len(suggested) > 1,
                "defer_candidates": {
                    "count": len(candidates),
                    "unit_ids": [unit["id"] for unit in candidates],
                    "echo_groups": len({unit.get("echo") or unit["id"] for unit in candidates}),
                    "by_class": dict(collections.Counter(unit["class"] for unit in candidates)),
                },
                "ruled_class_blanks": {
                    "count": len(ruled_blank),
                    "by_class": dict(collections.Counter(unit["class"] for unit in ruled_blank)),
                },
                "approved_units_at_risk": {kind: at_risk.get(kind, 0) for kind in AT_RISK_KINDS},
                "shares_pointers_with": [],
                "defer_file": defer_file,
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
            -(len(group["rejects"]["new"]) + len(group["neithers"]["new"])),
            -(
                sum(len(part) for part in group["rejects"].values())
                + sum(len(part) for part in group["neithers"].values())
            ),
            group["id"],
        )
    )
    return finalized, naming


def emit_defer(group, marker_target, *, stamp, defer_dir, note_text):
    marker = f"[deferred: {marker_target} — complaint list {stamp}]"
    note = f"{marker} {note_text}" if note_text else marker
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": stamp,
        "exported_at": stamp,
        "verdicts": [
            {"unit": unit_id, "verdict": "skip", "note": note, "at": stamp}
            for unit_id in group["defer_candidates"]["unit_ids"]
        ],
    }
    path = defer_dir / group["defer_file"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    return path


def main(argv=None, *, units: Iterable[Mapping[str, Any]] | None = None, unit_ids: set[str] | None = None):
    """Write the complaint list data, and defer files for any `--defer` groups.

    `units` and `unit_ids` are passed together or not at all: a single-pass stream of human unit records, and every corpus id, machine units included, for the absent-unit warning. Only the complaint fields and small projections of the blank and at-risk units are kept from the stream.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument(
        "verdicts",
        nargs="?",
        default=str(AUTOSAVE),
        help="the verdicts file to cluster (default: the live autosave)",
    )
    parser.add_argument("--corpus", "--surface", default=str(CORPUS))
    parser.add_argument("--data-out", default=str(DATA_OUT))
    parser.add_argument(
        "--since",
        default=None,
        help="new/older threshold as an ISO-8601 stamp (default: the manifest's generated_at)",
    )
    parser.add_argument(
        "--defer",
        action="append",
        default=[],
        metavar="GROUP_ID",
        help="emit a verdicts-deferred-*.json of skip verdicts covering this group's defer candidates; repeatable",
    )
    parser.add_argument("--defer-dir", default=str(ROOT), help="where defer files are written")
    parser.add_argument(
        "--note", default="", help="verbatim reviewer text appended after the marker in every deferred record"
    )
    args = parser.parse_args(argv)
    if (units is None) != (unit_ids is None):
        parser.error("units and unit_ids are handed over together or not at all")

    corpus = pathlib.Path(args.corpus)
    manifest = json.loads((corpus / "manifest.json").read_text())
    stamp = manifest["generated_at"]
    verdicts_path = pathlib.Path(args.verdicts)
    data = json.loads(verdicts_path.read_text())
    if data.get("manifest_generated_at") != stamp:
        print(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the corpus is "
            f"{stamp}; unit ids must never be joined across manifests — carry it forward first",
            file=sys.stderr,
        )
        return 1
    records = latest_verdicts(verdicts_path)

    if units is None or unit_ids is None:
        unit_ids = set()
        units = iter_human_units(corpus, unit_ids=unit_ids)
    complaints = []
    human = []
    for unit in units:
        record = records.get(unit["id"])
        if record and record["verdict"] in COMPLAINT_KINDS:
            complaint = {key: unit[key] for key in ("id", "class", "codepoints", "notation")}
            complaint.update(
                order=unit.get("order"),
                provenance=unit.get("provenance"),
                policy=unit.get("policy"),
            )
            complaints.append((complaint, record))
        elif unit.get("provenance") and (
            not record or record["verdict"] == "skip" or record["verdict"] in AT_RISK_KINDS
        ):
            human.append(
                {
                    "id": unit["id"],
                    "class": unit["class"],
                    "order": unit.get("order"),
                    "echo": unit.get("echo"),
                    "provenance": frozenset(unit.get("provenance") or []),
                }
            )
    unknown = sum(1 for unit_id in records if unit_id not in unit_ids)
    if unknown:
        print(f"warning: {unknown} verdict records name units absent from this corpus", file=sys.stderr)
    ruled_ids = {
        entry["id"] for entry in manifest.get("classes", []) if entry.get("status") in RULED_STATUSES
    }

    complaints.sort(key=lambda item: (_triage_position(item[0]), item[0]["id"]))
    threshold = args.since or stamp
    groups, naming = finalize_groups(
        build_groups(complaints), threshold=threshold, human=human, records=records, ruled_ids=ruled_ids
    )

    new = sum(len(group["rejects"]["new"]) + len(group["neithers"]["new"]) for group in groups)
    defer_union = {unit_id for group in groups for unit_id in group["defer_candidates"]["unit_ids"]}
    ruled_blank_total = sum(group["ruled_class_blanks"]["count"] for group in groups)
    approved_at_risk = sum(group["approved_units_at_risk"]["approve"] for group in groups)
    payload = {
        "manifest_generated_at": stamp,
        "verdicts_file": verdicts_path.name,
        "since": threshold,
        "totals": {
            "complaints": len(complaints),
            "rejects": sum(1 for _unit, record in complaints if record["verdict"] == "reject"),
            "neithers": sum(1 for _unit, record in complaints if record["verdict"] == "neither"),
            "new": new,
            "older": len(complaints) - new,
            "groups": len(groups),
            "defer_candidates": len(defer_union),
            "ruled_class_blanks": ruled_blank_total,
            "approved_units_at_risk": approved_at_risk,
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
            f"({totals['new']} new / {totals['older']} older) in {totals['groups']} groups — "
            f"{totals['defer_candidates']} defer candidates, "
            f"{totals['approved_units_at_risk']} approved units a fix would likely change"
        )

    if args.defer:
        by_id = {group["id"]: group for group in groups}
        defer_dir = pathlib.Path(args.defer_dir)
        for group_id in args.defer:
            group = by_id.get(group_id)
            if group is None:
                known = ", ".join(sorted(by_id)) or "none"
                print(f"unknown group id {group_id} (known: {known})", file=sys.stderr)
                return 1
            if group["defer_file"] is None or not group["defer_candidates"]["count"]:
                print(f"{group_id} has no defer candidates — nothing to emit", file=sys.stderr)
                return 1
            path = emit_defer(group, naming[group_id], stamp=stamp, defer_dir=defer_dir, note_text=args.note)
            print(
                f"deferred {group['defer_candidates']['count']} units -> {path} — "
                f"land it through the app's Import dialog"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
