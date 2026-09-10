"""Carry prior verdicts onto the live review surface by unit id.

A unit's id is its content key's (`rebuild.review.unit_cache.unit_id_for`; `rebuild/REVIEW-PLAN.md` §2.1 states the shape), so a verdict names the same unit on every surface whose content agrees with the one it was recorded on, and the carry is a join on that id: a verdict whose unit is on the new surface lands there, and one whose unit is not is stranded. No prior surface is opened, and the file's stamp is provenance rather than a key — a verdicts file recorded against an older surface carries onto the live one exactly as the autosave does. The four figures the carry prints (`carry figures:`) are what the cycle records on its run line in the timings journal (`artifact_cycle.carry_figures` reads them), so what a surface rebuild cost the store is on the record.
"""

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review import unit_index  # noqa: E402
from rebuild.tools.verdict_notes import cap_markers  # noqa: E402

OUT = ROOT / "verdicts-carried-forward.json"
CURRENT_SURFACE = ROOT / "rebuild/out/review"


def latest_verdicts(payload):
    best = {}
    for record in payload["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def resolve_prior(verdict_files):
    """Every source file's verdicts keyed by the unit id they name, newest `at` winning across files, each paired with the name of the file it came from for the provenance marker."""
    prior = {}
    for verdict_file in verdict_files:
        payload = json.loads(verdict_file.read_text())
        verdicts = latest_verdicts(payload)
        for unit_id, record in verdicts.items():
            if unit_id not in prior or record["at"] > prior[unit_id][0]["at"]:
                prior[unit_id] = (record, verdict_file.name)
        print(
            f"{verdict_file.name}: {len(verdicts)} verdicts, stamped {payload.get('manifest_generated_at')}"
        )
    return prior


def main(argv=None, *, current_units=None):
    """`current_units` lets a caller that already holds the live surface's index hand it over rather than have this tool read it again; rebuild.tools.verdict_chain is the one caller that does."""
    parser = argparse.ArgumentParser(
        description="Carry prior verdicts onto the live surface, landing each on the unit of the id it names."
    )
    parser.add_argument(
        "--verdicts",
        type=pathlib.Path,
        action="append",
        required=True,
        metavar="VERDICTS_JSON",
        help="a prior verdicts file; repeatable, the newest `at` per unit winning across files",
    )
    parser.add_argument("--out", default=str(OUT), help="output verdicts file (default: %(default)s)")
    parser.add_argument(
        "--current-surface",
        type=pathlib.Path,
        default=CURRENT_SURFACE,
        help="the freshly built surface to carry onto (default: the live review surface)",
    )
    args = parser.parse_args(argv)

    prior = resolve_prior(args.verdicts)

    manifest = json.loads((args.current_surface / "manifest.json").read_text())
    current = current_units if current_units is not None else unit_index.load_units(args.current_surface)
    human = [u for u in current if u.get("batch") is not None]

    carried = []
    kinds = collections.Counter()
    unhit = 0
    for unit in human:
        hit = prior.get(unit["id"])
        if hit is None:
            unhit += 1
            continue
        record, source = hit
        if record["verdict"] == "skip":
            continue
        provenance = f"[carried {record['unit']}@{source}, verdicted {record['at'][:10]}]"
        note = cap_markers(f"{provenance} {record['note']}".strip())
        carried.append({"unit": unit["id"], "verdict": record["verdict"], "note": note, "at": record["at"]})
        kinds[record["verdict"]] += 1

    current_ids = {u["id"] for u in current}
    stranded = sum(
        1
        for unit_id, (record, _source) in prior.items()
        if unit_id not in current_ids and record["verdict"] != "skip"
    )
    print(
        f"carry figures: human={len(human)} key_hits={len(human) - unhit} unhit={unhit} stranded={stranded}"
    )

    carried.sort(key=lambda r: r["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": carried,
    }
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out.name}: {len(carried)} carried onto manifest {manifest['generated_at']}")
    print(f"kinds: {dict(kinds)}")
    print(f"human queue: {len(human)} -> {len(human) - len(carried)} still needing fresh verdicts")
    return 0


if __name__ == "__main__":
    main()
