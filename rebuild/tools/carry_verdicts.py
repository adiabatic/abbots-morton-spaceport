"""Carry prior verdicts onto the live review corpus by unit id.

A unit's id is derived from its content key (`rebuild.review.unit_cache.unit_id_for`; `rebuild/REVIEW-PLAN.md` §2.1 gives the shape), so a verdict names the same unit on every corpus where that unit's content is unchanged. The carry is a join on that id. A verdict whose unit is a human unit on the new corpus is carried, and one whose unit is not on the corpus at all is orphaned. Skip verdicts are neither carried nor counted as orphaned. A carried record keeps the prior record's verdict, note and `at`, except that the note loses the `[carried …]` markers in its leading marker run (`verdict_notes.drop_carried_markers`), which older notes hold and tabs and imports can bring back. No prior corpus is opened, and a verdicts file's stamp is only printed, so a file recorded against any older corpus carries the same way the autosave does. `artifact_cycle.carry_counts` parses the `carry counts:` line, and the cycle records those counts on its run line in the timings journal.

Unless `--allow-orphans` is passed, the carry refuses when more than `landing.ORPHAN_SHARE_LIMIT` of the prior verdicts other than skips are orphaned (`landing.orphan_share` says why). It refuses after printing its counts and before writing anything, because a carried file stamped for the new corpus can outrank the store in a later pass's choice of carry source.
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
from rebuild.review.landing import ORPHAN_SHARE_LIMIT, orphan_share  # noqa: E402
from rebuild.tools.verdict_notes import drop_carried_markers  # noqa: E402

OUT = ROOT / "verdicts-carried-forward.json"
CURRENT_CORPUS = ROOT / "rebuild/out/review"


def latest_verdicts(payload):
    best = {}
    for record in payload["verdicts"]:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def resolve_prior(verdict_files):
    """Return each unit id's newest verdict across all the files, by `at`."""
    prior = {}
    for verdict_file in verdict_files:
        payload = json.loads(verdict_file.read_text())
        verdicts = latest_verdicts(payload)
        for unit_id, record in verdicts.items():
            if unit_id not in prior or record["at"] > prior[unit_id]["at"]:
                prior[unit_id] = record
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
    """Write the carried verdicts file for the current corpus.

    `current_units` and `current_ids` are passed together or not at all. `current_units` is a single-pass stream of human unit records that need only an `id`, and `current_ids` holds every corpus id, machine units included, for the orphaned count. The verdict update passes its duplicate records; a standalone run streams the human index and collects the ids in the same pass.
    """
    parser = argparse.ArgumentParser(
        description="Carry prior verdicts onto the live corpus, landing each on the unit of the id it names."
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
        "--current-corpus",
        "--current-surface",
        type=pathlib.Path,
        default=CURRENT_CORPUS,
        help="the freshly built corpus to carry onto (default: the live review corpus)",
    )
    parser.add_argument(
        "--allow-orphans",
        action="store_true",
        help=f"write the carried file even when more than {ORPHAN_SHARE_LIMIT:.0%}% of the prior verdicts name no unit on the corpus",
    )
    args = parser.parse_args(argv)
    if (current_units is None) != (current_ids is None):
        parser.error("current_units and current_ids are handed over together or not at all")

    prior = resolve_prior(args.verdicts)

    manifest = json.loads((args.current_corpus / "manifest.json").read_text())
    if current_units is None or current_ids is None:
        current_ids = set()
        current_units = unit_index.iter_human_units(args.current_corpus, unit_ids=current_ids)
    human = current_units

    carried = []
    kinds = collections.Counter()
    unmatched = 0
    human_count = 0
    for unit in human:
        human_count += 1
        record = prior.get(unit["id"])
        if record is None:
            unmatched += 1
            continue
        if record["verdict"] == "skip":
            continue
        carried.append(
            {
                "unit": unit["id"],
                "verdict": record["verdict"],
                "note": drop_carried_markers(record["note"]),
                "at": record["at"],
            }
        )
        kinds[record["verdict"]] += 1

    orphaned = sum(
        1 for unit_id, record in prior.items() if unit_id not in current_ids and record["verdict"] != "skip"
    )
    print(
        f"carry counts: human={human_count} matched={human_count - unmatched} unmatched={unmatched} orphaned={orphaned}"
    )
    share = orphan_share(orphaned, sum(1 for record in prior.values() if record["verdict"] != "skip"))
    if share is not None and not args.allow_orphans:
        raise SystemExit(
            f"carry refused: {share} prior verdicts name no unit on {args.current_corpus}, and nothing was written. "
            "If a change moved the units' ids, re-key the verdicts onto the new ids first (rebuild/tools/rekey_verdicts.py); "
            "if the windows themselves changed, pass --allow-orphans."
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
