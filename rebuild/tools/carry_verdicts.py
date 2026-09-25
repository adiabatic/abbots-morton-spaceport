"""Carry prior verdicts onto the live review surface by unit id.

A unit's id is derived from its content key (`rebuild.review.unit_cache.unit_id_for`; `rebuild/REVIEW-PLAN.md` §2.1 gives the shape), so a verdict names the same unit on every surface where that unit's content is unchanged. The carry is a join on that id. A verdict whose unit is a human unit on the new surface is carried, and one whose unit is not on the surface at all is stranded. Skip verdicts are neither carried nor counted as stranded. No prior surface is opened, and a verdicts file's stamp is only printed, so a file recorded against any older surface carries the same way the autosave does. `artifact_cycle.carry_figures` parses the `carry figures:` line, and the cycle records those figures on its run line in the timings journal.
"""

import argparse
import collections
import json
import pathlib
import sys
from collections.abc import Iterable, Mapping
from typing import Any

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
    """Return each unit id's newest verdict across all the files, by `at`, paired with the name of the file it came from for the provenance marker."""
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


def main(
    argv=None,
    *,
    current_units: Iterable[Mapping[str, Any]] | None = None,
    current_ids: set[str] | None = None,
):
    """Write the carried verdicts file for the current surface.

    `current_units` and `current_ids` are passed together or not at all. `current_units` is a single-pass stream of human unit records that need only an `id`, and `current_ids` holds every surface id, machine units included, for the stranded count. The verdict chain passes its echo records; a standalone run streams the human index and collects the ids in the same pass.
    """
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
    if (current_units is None) != (current_ids is None):
        parser.error("current_units and current_ids are handed over together or not at all")

    prior = resolve_prior(args.verdicts)

    manifest = json.loads((args.current_surface / "manifest.json").read_text())
    if current_units is None or current_ids is None:
        current_ids = set()
        current_units = unit_index.iter_human_units(args.current_surface, unit_ids=current_ids)
    human = current_units

    carried = []
    kinds = collections.Counter()
    unhit = 0
    human_count = 0
    for unit in human:
        human_count += 1
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

    stranded = sum(
        1
        for unit_id, (record, _source) in prior.items()
        if unit_id not in current_ids and record["verdict"] != "skip"
    )
    print(
        f"carry figures: human={human_count} key_hits={human_count - unhit} unhit={unhit} stranded={stranded}"
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
    print(f"human queue: {human_count} -> {human_count - len(carried)} still needing fresh verdicts")
    return 0


if __name__ == "__main__":
    main()
