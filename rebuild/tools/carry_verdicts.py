"""Carry prior verdicts onto the live review corpus by unit id.

A unit's id is derived from its content key (`rebuild.review.unit_cache.unit_id_for`; `rebuild/REVIEW-PLAN.md` §2.1 gives the shape), so a verdict names the same unit on every corpus where that unit's content is unchanged. The carry is a join on that id. A unit takes its newest verdict across the `--verdicts` files unless the store among them cleared it at or after that verdict (`resolve_prior` gives the rule and how it tells the store), so a verdict the reader cleared in the store after another file recorded it is not carried back while the store keeps that tombstone, which it does until the unit is set again, a stamp change leaves the unit off the new corpus's human units, or the store file is replaced whole by a store that lacks it (`merge_verdicts --restore-as-of --apply` to a moment before the clear, or to a later one whose replay starts from a base event written after the clear with no `cleared` list, since a restore puts back the tombstones the journal replays to (`journal.replay_store`), or `rekey_verdicts --undo`, which puts back the store from before the re-key); another file's tombstones are passed over, as a merge passes over an input's. A verdict whose unit is a human unit on the new corpus is carried, and one whose unit is not on the corpus at all is orphaned. Skip verdicts and clears are neither carried nor counted as orphaned. A carried record keeps the prior record's verdict, note and `at`, except that the note loses the `[carried …]` markers in its leading marker run (`verdict_notes.drop_carried_markers`), which older notes hold and tabs and imports can bring back. No prior corpus is opened, and a verdicts file's stamp is only printed, so a file recorded against any older corpus carries the same way the autosave does. `artifact_cycle.carry_counts` parses the `carry counts:` line, and the cycle records those counts on its run line in the timings journal.

The accepted list (`rebuild.review.accepted`, `--accepted`, by default rebuild/accepted-units.txt beside the store among the `--verdicts` files, and none when no file is the store) holds the units a close-out folded out of the store, which read as their accepted record wherever the store holds no override for them. Those need no carry: a listed id names its unit on every corpus where the unit's content is unchanged, and the readers look it up on the corpus they read. Nor does the store's override section, its records and tombstones on listed units (`accepted.Overrides`), which the carry passes over as it passes over the store's `accepted` field: the merge that takes the carried file keeps the section of the store the carry read, whole, whatever corpus its units are on (`merge_verdicts`). The carry refuses a store that does not pair with the list (`accepted.pair`), as every reader does. Another file's record on a listed unit, from its own records or its override section, is carried as any record is, unless the store holds no override for its unit and it equals the accepted record by value or is dated at or before the close-out (`Accepted.keeps_out`), so an export made before the close-out brings back no folded verdict while a note typed on an accepted unit since survives; the merge meets a carried record on a listed unit with the store's override by the newer `at`. In the orphan guard the store's override records other than skips on the served corpus's human units (`--served-corpus`, the live review corpus by default) count among the prior verdicts, as its own records do, and so do the listed units of those human units that no file's record and no override names, each counting among the orphaned when the new corpus lacks it; an override on a unit the served corpus lacks counts on neither side, since the section keeps it until the next close-out and it would otherwise count as orphaned on every pass; `accepted` counts the human units of the new corpus that read as accepted, a human unit holding an override record counts as matched and one holding an override tombstone as unmatched.

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

from rebuild.review import accepted, unit_index  # noqa: E402
from rebuild.review.accepted import Accepted, Overrides  # noqa: E402
from rebuild.review.landing import (
    ORPHAN_SHARE_LIMIT,
    SNAPSHOT_NAME,
    orphan_share,
    read_manifest,
)  # noqa: E402
from rebuild.review.verdict_store import note_of, parse_cleared, well_formed  # noqa: E402
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


class UnreadableVerdicts(ValueError):
    """Raised by `read_verdicts_file` for a verdicts file the carry cannot read; the message is the file's path followed by why."""


def read_verdicts_file(verdict_file: pathlib.Path) -> dict[str, Any]:
    """Return the payload of `verdict_file`, or raise `UnreadableVerdicts` when it cannot be read, is not JSON, holds no `verdicts` list, or holds a record `verdict_store.well_formed` refuses. The carry refuses such a file (`resolve_prior`), and so does a hand-run verdict update with nothing to merge for the store its fills would start from (`verdict_update.refuse_unreadable_store`)."""
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
            malformed = [record for record in accepted.records_of(payload) if not well_formed(record)]
            if not malformed:
                return payload
            problem = f"holds a verdict record that is not an object with a string `unit`, `verdict` and `at` and a `note`, if any, that is a string or false-valued, as `verdict_store.well_formed` requires ({json.dumps(malformed[0])})"
    raise UnreadableVerdicts(f"{verdict_file} {problem}")


def _read_verdicts_file(verdict_file):
    try:
        return read_verdicts_file(verdict_file)
    except UnreadableVerdicts as unreadable:
        raise SystemExit(
            f"carry refused: {unreadable}, and nothing was written. "
            "If it is the verdict store or the cycle's snapshot of it, restore a readable store from the journal with "
            "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`; "
            "otherwise name another verdicts file with --verdicts."
        ) from None


def resolve_prior(verdict_files, listed: Accepted | None = None):
    """Return each unit id's newest verdict across all the files, by `at`, leaving out a unit the store cleared at or after that verdict. The store is a file named `SNAPSHOT_NAME`, the live store's name, which the cycle's snapshot of it keeps. Its tombstones (`verdict_store.parse_cleared`), and its override tombstones on listed units, are the clears, and a tombstone wins over a record whose `at` is not after the clear, as the store's tombstones win in a merge (`merge_verdicts.merge_into`). The store's tombstone on a unit it holds a record for is passed over, as the verdict store passes it over, and so is every other file's tombstone, as a merge passes over an input's: a clear that no store records would leave no tombstone for the land to keep. The store's override section is passed over, since the merge keeps it, and another file's override records are read with its own records (`accepted.records_of`). With `listed`, the accepted list, the store must pair with it (`accepted.pair`), and another file's record on a listed unit that the store holds no override for is left out when the list keeps it out (`Accepted.keeps_out`). A file that cannot be read, is not JSON, holds no `verdicts` list, or holds a record `verdict_store.well_formed` refuses, and a store that does not pair with the list, stops the carry with a refusal that names it, before anything is written."""
    return _resolve(verdict_files, listed)[0]


def _resolve(verdict_files, listed: Accepted | None) -> tuple[dict, dict[str, str], Overrides]:
    """Return `resolve_prior`'s verdicts, the store's tombstones on units no file's record is left beside, and the store's override section."""
    prior = {}
    cleared = {}
    overrides = Overrides()
    others: list[dict] = []
    for verdict_file in verdict_files:
        payload = _read_verdicts_file(verdict_file)
        if verdict_file.name == SNAPSHOT_NAME:
            if listed is not None:
                try:
                    accepted.pair(payload, listed, store=verdict_file)
                except accepted.Mismatch as mismatch:
                    raise SystemExit(f"carry refused: {mismatch}, and nothing was written.") from None
            verdicts = latest_verdicts(payload)
            overrides = Overrides.of(payload)
            for unit_id, at in parse_cleared(payload).items():
                if unit_id not in verdicts:
                    cleared[unit_id] = max(at, cleared.get(unit_id, at))
        else:
            verdicts = latest_verdicts({"verdicts": accepted.records_of(payload)})
            others.append(verdicts)
        for unit_id, record in verdicts.items():
            if unit_id not in prior or record["at"] > prior[unit_id]["at"]:
                prior[unit_id] = record
        print(
            f"{verdict_file.name}: {len(verdicts)} verdicts, stamped {payload.get('manifest_generated_at')}"
        )
    if listed is not None and listed.verdicts:
        for verdicts in others:
            for unit_id, record in verdicts.items():
                if prior.get(unit_id) is record and unit_id not in overrides and listed.keeps_out(record):
                    del prior[unit_id]
    tombstones = {**cleared, **overrides.cleared}
    return (
        {
            unit_id: record
            for unit_id, record in prior.items()
            if unit_id not in tombstones or record["at"] > tombstones[unit_id]
        },
        cleared,
        overrides,
    )


def served_human_ids(served_corpus: pathlib.Path) -> frozenset[str]:
    """Return the served corpus's human unit ids, or none when no corpus is served yet."""
    try:
        return read_manifest(served_corpus)[1]
    except FileNotFoundError:
        return frozenset()


def _read_listed(path: pathlib.Path | None, verdict_files: list[pathlib.Path]) -> Accepted | None:
    """Return the accepted list at `path`, or else beside the store among `verdict_files` (`accepted.path_for`), or None when no file is the store; a malformed list stops the carry."""
    if path is None:
        stores = [verdict_file for verdict_file in verdict_files if verdict_file.name == SNAPSHOT_NAME]
        if not stores:
            return None
        path = accepted.path_for(stores[0])
    try:
        return accepted.read(path)
    except accepted.Malformed as malformed:
        raise SystemExit(f"carry refused: {malformed}, and nothing was written.") from None


def main(
    argv=None,
    *,
    current_units: Iterable[Mapping[str, Any]] | None = None,
    current_ids: set[str] | None = None,
    listed: Accepted | None = None,
):
    """Write the carried verdicts file for the current corpus.

    `current_units` and `current_ids` are passed together or not at all. `current_units` is a single-pass stream of human unit records that need only an `id`, and `current_ids` holds every corpus id, machine units included, for the orphaned count. The verdict update passes its duplicate records; a standalone run streams the human index and collects the ids in the same pass. `listed` is the accepted list the verdict update read once for every step, in place of `--accepted`.
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
    parser.add_argument(
        "--accepted",
        type=pathlib.Path,
        help=f"the accepted list the store pairs with (default: rebuild/accepted-units.txt beside the --verdicts file named {SNAPSHOT_NAME}, and none when no file is)",
    )
    parser.add_argument(
        "--served-corpus",
        type=pathlib.Path,
        default=CURRENT_CORPUS,
        help="the corpus the review server serves, whose human units' accepted ids count among the prior verdicts (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if (current_units is None) != (current_ids is None):
        parser.error("current_units and current_ids are handed over together or not at all")

    if listed is None:
        listed = _read_listed(args.accepted, args.verdicts)
    prior, _cleared, overrides = _resolve(args.verdicts, listed)
    listed_ids = listed.verdicts if listed is not None else {}

    manifest = json.loads((args.current_corpus / "manifest.json").read_text())
    if current_units is None or current_ids is None:
        current_ids = set()
        current_units = unit_index.iter_human_units(args.current_corpus, unit_ids=current_ids)
    human = current_units

    carried = []
    kinds = collections.Counter()
    unmatched = 0
    accepted_count = 0
    overridden = 0
    human_count = 0
    for unit in human:
        human_count += 1
        record = prior.get(unit["id"])
        if record is None:
            override = overrides.records.get(unit["id"])
            if override is not None:
                overridden += override["verdict"] != "skip"
            elif unit["id"] in listed_ids and unit["id"] not in overrides:
                accepted_count += 1
            else:
                unmatched += 1
            continue
        if record["verdict"] == "skip":
            continue
        carried.append(
            {
                "unit": unit["id"],
                "verdict": record["verdict"],
                "note": drop_carried_markers(note_of(record)),
                "at": record["at"],
            }
        )
        kinds[record["verdict"]] += 1

    served = served_human_ids(args.served_corpus) if listed_ids else frozenset()
    effective = [
        unit_id
        for held in (
            prior,
            {
                unit_id: record
                for unit_id, record in overrides.records.items()
                if unit_id in served and unit_id not in prior
            },
        )
        for unit_id, record in held.items()
        if record["verdict"] != "skip"
    ]
    orphaned = sum(1 for unit_id in effective if unit_id not in current_ids)
    listed_prior = 0
    for unit_id in listed_ids:
        if unit_id in served and unit_id not in prior and unit_id not in overrides:
            listed_prior += 1
            orphaned += unit_id not in current_ids
    print(
        f"carry counts: human={human_count} matched={human_count - unmatched - accepted_count} unmatched={unmatched} orphaned={orphaned} accepted={accepted_count}"
    )
    share = orphan_share(orphaned, len(effective) + listed_prior)
    if share is not None and not args.allow_orphans:
        no_carry = (
            "; "
            if listed_prior
            else ", or, in a cycle pass whose store holds no verdict other than skips and whose accepted list names no unit of the served corpus, pass --no-carry; "
        )
        raise SystemExit(
            f"carry refused: {share} prior verdicts name no unit on {args.current_corpus}, and nothing was written. "
            "If a change to the carry projection moved the units' content ids, re-key the verdicts onto the new ids first (rebuild/tools/rekey_verdicts.py); "
            "if the windows themselves changed, pass --allow-orphans. "
            "If a verdicts file other than the store (verdicts-autosave.json) is the wrong one, such as one on unit ids from before ids were content ids, name the right one with --verdicts"
            f"{no_carry}"
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
    print(
        f"human queue: {human_count} -> {human_count - len(carried) - overridden - accepted_count} still needing fresh verdicts"
    )
    return 0


if __name__ == "__main__":
    main()
