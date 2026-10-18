"""Carry prior verdicts onto the live review corpus by unit id.

A unit's id is derived from its content key (`rebuild.review.unit_cache.unit_id_for`; `rebuild/REVIEW-PLAN.md` §2.1 gives the shape), so a verdict names the same unit on every corpus where that unit's content is unchanged. The carry is a join on that id. A unit takes its newest verdict across the `--verdicts` files unless the store among them cleared it at or after that verdict (`resolve_prior` gives the rule and how it tells the store), so a verdict the reader cleared in the store after another file recorded it is not carried back while the store keeps that tombstone, which it does until the unit is set again, a stamp change leaves the unit off the new corpus's human units, or the store file is replaced whole by a store that lacks it (`merge_verdicts --restore-as-of --apply` to a moment before the clear, or to a later one whose replay starts from a base event written after the clear with no `cleared` list, since a restore puts back the tombstones the journal replays to (`journal.replay_store`), or `rekey_verdicts --undo`, which puts back the store from before the re-key); another file's tombstones are passed over, as a merge passes over an input's. A verdict whose unit is a human unit on the new corpus is carried, and one whose unit is not on the corpus at all is orphaned. Skip verdicts and clears are neither carried nor counted as orphaned. A carried record keeps the prior record's verdict, note and `at`, except that the note loses the `[carried …]` markers in its leading marker run (`verdict_notes.drop_carried_markers`), which older notes hold and tabs and imports can bring back. No prior corpus is opened, and a verdicts file's stamp is only printed, so a file recorded against any older corpus carries the same way the autosave does. `artifact_cycle.carry_counts` parses the `carry counts:` line, and the cycle records those counts on its run line in the timings journal.

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
from rebuild.review.landing import ORPHAN_SHARE_LIMIT, SNAPSHOT_NAME, orphan_share  # noqa: E402
from rebuild.review.verdict_store import parse_cleared  # noqa: E402
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


def _well_formed(record):
    return (
        isinstance(record, dict)
        and all(isinstance(record.get(key), str) for key in ("unit", "verdict", "at"))
        and isinstance(record.get("note") or "", str)
    )


def _read_verdicts_file(verdict_file):
    try:
        payload = json.loads(verdict_file.read_text())
    except OSError as error:
        problem = f"cannot be read ({error.strerror or error})"
    except ValueError as error:
        problem = f"is not JSON ({error})"
    else:
        if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
            problem = "holds no `verdicts` list"
        else:
            malformed = [record for record in payload["verdicts"] if not _well_formed(record)]
            if not malformed:
                return payload
            problem = f"holds a verdict record that is not an object with a string `unit`, `verdict` and `at` and a string `note` if any ({json.dumps(malformed[0])})"
    raise SystemExit(
        f"carry refused: {verdict_file} {problem}, and nothing was written. "
        "If it is the verdict store or the cycle's snapshot of it, restore a readable store from the journal with "
        "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`; "
        "otherwise name another verdicts file with --verdicts."
    )


def resolve_prior(verdict_files):
    """Return each unit id's newest verdict across all the files, by `at`, leaving out a unit the store cleared at or after that verdict. The store is a file named `SNAPSHOT_NAME`, the live store's name, which the cycle's snapshot of it keeps. Its tombstones (`verdict_store.parse_cleared`) are the clears, and a tombstone wins over a record whose `at` is not after the clear, as the store's tombstones win in a merge (`merge_verdicts.merge_into`). The store's tombstone on a unit it holds a record for is passed over, as the verdict store passes it over, and so is every other file's tombstone, as a merge passes over an input's: a clear that no store records would leave no tombstone for the land to keep. A file that cannot be read, is not JSON, holds no `verdicts` list, or holds a record that is not an object with a string `unit`, `verdict` and `at` stops the carry with a refusal that names it, before anything is written; a record's `note` may be absent, as every reader of the store takes it."""
    prior = {}
    cleared = {}
    for verdict_file in verdict_files:
        payload = _read_verdicts_file(verdict_file)
        verdicts = latest_verdicts(payload)
        for unit_id, record in verdicts.items():
            if unit_id not in prior or record["at"] > prior[unit_id]["at"]:
                prior[unit_id] = record
        if verdict_file.name == SNAPSHOT_NAME:
            for unit_id, at in parse_cleared(payload).items():
                if unit_id not in verdicts:
                    cleared[unit_id] = max(at, cleared.get(unit_id, at))
        print(
            f"{verdict_file.name}: {len(verdicts)} verdicts, stamped {payload.get('manifest_generated_at')}"
        )
    return {
        unit_id: record
        for unit_id, record in prior.items()
        if unit_id not in cleared or record["at"] > cleared[unit_id]
    }


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
        help=f"a prior verdicts file; repeatable, the newest `at` per unit winning across files unless the store (the file named {SNAPSHOT_NAME}) cleared the unit at or after it",
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
                "note": drop_carried_markers(record.get("note") or ""),
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
            "If a change to the carry projection moved the units' content ids, re-key the verdicts onto the new ids first (rebuild/tools/rekey_verdicts.py); "
            "if the windows themselves changed, pass --allow-orphans. "
            "If a verdicts file other than the store (verdicts-autosave.json) is the wrong one, such as one on unit ids from before ids were content ids, name the right one with --verdicts, "
            "or, in a cycle pass whose store holds no verdict other than skips, pass --no-carry; "
            "if the store's own verdicts are on such ids, pass --allow-orphans."
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
