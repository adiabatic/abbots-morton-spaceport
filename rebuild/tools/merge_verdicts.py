"""Merge stamp-aligned ams-review-verdicts/1 files into verdicts-autosave.json without a browser, as the review app's Import dialog does: per unit, the record with the strictly newer `at` wins. The artifact cycle uses it to merge carried verdicts, and the app reads the result on boot or focus. The existing aligned autosave is always part of the union, so a merge never drops a verdict. Unless it is a dry run or refused, a merge writes the store when its records or tombstones differ from the aligned autosave's, and always when no autosave is aligned with the corpus, so a merge into a missing store leaves one stamped for the corpus even when it holds no verdicts, for the verdict update's fills to read. An autosave that is an ams-review-verdicts/1 document stamped for an older corpus (a `manifest_generated_at` that sorts before the corpus's `generated_at`) is moved aside first (`stash_path_for`), and one stamped for a newer corpus is refused, since the merge would move the store back onto an outdated corpus. An autosave that is not an ams-review-verdicts/1 document (`parse_autosave_payload`) is moved aside as verdicts-autosave-corrupt-<time>.json whatever stamp it names, keeping none of its verdicts or tombstones. An input stamped for another corpus is refused; `carry_verdicts.py` moves verdicts between corpora, and there is no override. An input file holding a verdict record the carry refuses (`verdict_store.well_formed`) is refused too, naming the file and the first such record's unit, or its index when it names no unit, because the merge writes the `at` and `note` of the record that wins each unit as they are unless false-valued, and compares `at`s to pick that record, so a record whose `at` or `note` is neither a string nor false-valued can leave a store that no later pass can carry, or, for such an `at`, stop the merge with a traceback where it compares two `at`s. A store stamped for the corpus that holds such a record is refused the same way, naming the store and the record, before anything is written, because the merge keeps that store's records; a store the merge sets aside is set aside whatever its own records hold, since the merge keeps none of its verdicts and reads of them only which units they name, on which it keeps none of that store's tombstones, but one whose override section holds such a record is refused the same way, since the merge keeps that section. A merge keeps the store's tombstones (the units the app cleared, each with the time of the clear), and a tombstone wins over an incoming record whose `at` is not after the clear. A merge onto an ams-review-verdicts/1 store stamped for an older corpus keeps none of that store's verdicts but keeps its tombstones on the corpus's human units, as the review server's move of a store onto the served corpus (rule 3 in `rebuild.review.verdict_store`) and the land (`landing.overlay`) keep them, so the fills a pass that moves the stamp merges after the carry meet the same tombstones as on a pass that keeps it. A caller in the same process can add tombstones with `run_merge`'s `clears`, which the merge keeps as the store's own on units the kept store holds no record for: the verdict update passes the skips of a store it carries onto the corpus before a direct merge (`verdict_update.skip_clears`). A merge that writes reads, writes and journals the store under the store's lock (`rebuild.review.store_lock`, taken through `landing.locked_store`, which first finishes a land whose holder died, and while that recovery refuses, `landing.LandUnrecoverable`, the merge and `--restore-as-of --apply` print its refusal and write nothing), which the review server holds for each save, so a merge waits for a save in flight, and the server answers a save made during the merge with a retryable 503 and picks the merged file up by itself afterward. The app retries the save a closing tab sends only when it is next opened, so a merge that would write the live store refuses while the review server is listening, unless --yes is passed on this command line (for a server that serves another checkout); the verdict update, which calls `run_merge` in process, has no --yes, so a hand-run verdict update's merges into the live store refuse the same way with no override, and the server must be stopped first; a dry run and a merge refused because the server is listening take no lock. `--restore-as-of --apply` has the same refusal and takes the lock too. Every write is appended to the journal (`rebuild.review.journal`) that --journal names, or else to the verdicts-journal.ndjson beside the store, which only a store named verdicts-autosave.json takes by default (`journal.journal_path_for`), so a store under any other name needs --journal, and `--restore-as-of` replays that journal to recover the store as of any recorded time, its records and its tombstones (`journal.replay_store`), so a unit cleared by then stays cleared through a later merge of a file that still holds its older verdict, unless the replay starts from a base event written after the clear with no `cleared` list (the `rebuild.review.journal` docstring says where a replay of lines without the tombstone fields differs from the store). A restore, with --apply or without, refuses a moment whose replay holds a verdict record the carry refuses (`verdict_store.well_formed`), naming the journal, the moment and the first such record by the unit the journal holds it on, before any `--rekey-map` re-key and before it writes anything, as the merge refuses a store holding one. `--apply` over a store holding such a record, which the review server and the land refuse and send the reader here to replace, stashes it as it stashes any store and journals the restored store as a base event, which reads none of the old store's records, since a diff against them compares their `at` values (`journal.latest_by_unit`). Without --apply, the restore writes the store it recovers to --out, or else to verdicts-restored-<time>.json beside the store, which again only a store named verdicts-autosave.json has by default (`journal.beside_store`), so a restore whose --autosave is a copy in another directory writes nothing at the repo root, where `status` counts every verdicts-*.json but the live store as a carry candidate. `--rekey-map` moves a replayed store's unit ids, its tombstones' among them, through the id map `rebuild.tools.rekey_verdicts` writes, for a time before that re-key.

The accepted list beside the store (`rebuild.review.accepted`; `--accepted`, by default rebuild/accepted-units.txt beside --autosave) holds the units a close-out folded out of the store, each read as its accepted record, dated the close-out, wherever the store holds no override for it, and the store keeps its records and tombstones on listed units in its override section (`accepted.Overrides`). A merge refuses a store, kept or set aside, that does not pair with the list (`accepted.pair`), merges an input's record on a listed unit, from the input's own records or its override section, into the store's override section, where it meets the unit's override as a record meets a record and its accepted record as a store record dated the close-out would meet it (`merge_into`), counts the store's accepted units among its effective verdicts in the auto-pick, and writes a store whose `accepted` field names the list. A merge keeps the store's override section whole, a store it sets aside on another stamp included, whose skips there become tombstones at their `at` (`Overrides.moved`), so an override or a clear on an accepted unit outlasts a stamp move whatever corpus its unit is on. A restore writes the field the journal's events recorded (`journal.replay_with_list`), moved through each re-key since when `--rekey-map` is given (`rekeyed_field`), and the records and tombstones on listed units as the override section of the list that field names (`accepted.split`), `--apply` refuses a restored store that does not pair with the list, and a moment before a journal that a close-out started is refused naming the close-out's archive.

Usage:
  uv run python -m rebuild.tools.merge_verdicts [FILES ...]     # no FILES: merge the fullest verdicts file verdict-ready names
  uv run python -m rebuild.tools.merge_verdicts --dry-run FILES ...
  uv run python -m rebuild.tools.merge_verdicts --list
  uv run python -m rebuild.tools.merge_verdicts --restore-as-of 2026-07-19T03:00 [--apply [--yes]]
  uv run python -m rebuild.tools.merge_verdicts --restore-as-of 2026-07-19T03:00 --rekey-map var/keep/class-rejig-rekey/unit-id-map.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.review import accepted, journal, status  # noqa: E402
from rebuild.review.accepted import Accepted, Overrides, View  # noqa: E402
from rebuild.review.landing import LandUnrecoverable, locked_store, read_manifest  # noqa: E402
from rebuild.review.serve import parse_autosave_payload, stash_path_for  # noqa: E402
from rebuild.review.verdict_store import (  # noqa: E402
    malformed_in,
    malformed_override,
    malformed_record,
    note_of,
    parse_cleared,
)
from rebuild.tools.review_server import server_listening as _server_listening  # noqa: E402

AUTOSAVE = ROOT / "verdicts-autosave.json"
CORPUS = ROOT / "rebuild" / "out" / "review"
JOURNAL = ROOT / journal.JOURNAL_NAME
VERDICT_KINDS = frozenset({"approve", "reject", "either", "identical", "neither", "skip"})
REKEY_BACKUP_RECORD = "backup.json"
CLOSE_OUT_ARCHIVES = Path("var") / "keep" / "close-outs"


def _sanitize(stamp: str) -> str:
    return "".join(c if c.isalnum() or c in ".-" else "." for c in stamp)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_payload(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return parse_autosave_payload(raw)


def _effective(records: dict[str, dict]) -> int:
    return sum(1 for record in records.values() if record.get("verdict") != "skip")


def _refused_by_tombstone(counts: Mapping[str, int]) -> str:
    return (
        f", {counts['refused_by_tombstone']} refused by a tombstone" if counts["refused_by_tombstone"] else ""
    )


def _already_accepted(counts: Mapping[str, int]) -> str:
    return f", {counts['accepted']} already accepted" if counts["accepted"] else ""


COUNT_KEYS: tuple[str, ...] = (
    "added",
    "replaced",
    "kept_newer",
    "refused_by_tombstone",
    "accepted",
    "invalid",
)


def merge_into(
    result: dict[str, dict],
    verdicts,
    cleared: dict[str, str] | None = None,
    listed: Accepted | None = None,
    overrides: Overrides | None = None,
) -> dict:
    """Merge `verdicts` into `result` in place, the strictly newer `at` winning per unit, and return the counts. `cleared` holds the store's tombstones (unit → the clear's `at`), which count as a newer state than any record whose `at` is not after the clear; a record that wins over a tombstone removes it from `cleared`. A record a tombstone keeps out counts under `refused_by_tombstone`, and one a record in `result` keeps out under `kept_newer`. `listed` is the accepted list, and a record on a listed unit is merged into `overrides`, the store's override section, by the same rule, with the unit's accepted record, dated the close-out, standing in for an override it lacks (`rebuild.review.accepted`): an incoming record that equals the accepted record by value adds nothing and counts under `accepted`, one dated at or before the close-out is kept out as a store record dated then would keep it and counts under `kept_newer`, and any other is added as an override (`Accepted.keeps_out`). A record on a listed unit with no `overrides` to merge it into raises `ValueError`."""
    counts = dict.fromkeys(COUNT_KEYS, 0)
    for unit, record in sorted(journal.latest_by_unit(verdicts).items()):
        if record.get("verdict") not in VERDICT_KINDS:
            counts["invalid"] += 1
            continue
        if listed is not None and unit in listed.verdicts:
            if overrides is None:
                raise ValueError(
                    f"{unit} is listed, and merge_into was given no override section to merge it into"
                )
            records, tombstones, list_rule = overrides.records, overrides.cleared, listed
        else:
            records, tombstones, list_rule = result, cleared, None
        current = records.get(unit)
        at = record.get("at") or ""
        if current is not None and (current.get("at") or "") >= at:
            counts["kept_newer"] += 1
            continue
        if current is None and tombstones is not None and unit in tombstones:
            if tombstones[unit] >= at:
                counts["refused_by_tombstone"] += 1
                continue
            del tombstones[unit]
        elif current is None and list_rule is not None and list_rule.keeps_out(record):
            counts["accepted" if list_rule.matches(record) else "kept_newer"] += 1
            continue
        records[unit] = {
            "unit": unit,
            "verdict": record["verdict"],
            "note": note_of(record),
            "at": record.get("at") or "",
        }
        counts["replaced" if current is not None else "added"] += 1
    return counts


def _aligned_effective(aligned: dict, listed: Accepted, corpus: Path) -> int:
    """Return the aligned store's effective verdicts as `status` counts the store's: its records and override records other than skips and its accepted units on the corpus's human units that it holds no override for."""
    records = journal.latest_by_unit(aligned["verdicts"])
    overrides = Overrides.of(aligned)
    if not listed.verdicts:
        return _effective(records) + _effective(overrides.records)
    _, human_ids = read_manifest(corpus)
    view = View(records, parse_cleared(aligned), listed, overrides)
    return _effective(records) + _effective(overrides.records) + sum(1 for _ in view.from_list(human_ids))


def _write_store(autosave: Path, payload: dict) -> None:
    tmp = autosave.with_name(autosave.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, autosave)


def _corpus_stamp(corpus: Path) -> str | None:
    try:
        stamp = json.loads((corpus / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        return None
    return stamp if isinstance(stamp, str) else None


def run_merge(
    files: list[Path] | None,
    *,
    autosave: Path,
    corpus: Path,
    journal_path: Path,
    dry_run: bool,
    yes: bool = False,
    offers_yes: bool = False,
    clears: Mapping[str, str] | None = None,
    listed: Accepted | None = None,
) -> int:
    """Merge `files` into the store at `autosave` (`_merge_into_store`), under the store's lock unless it is a dry run or refused because a server listens. `listed` is the accepted list the store pairs with, read here from beside `autosave` (`accepted.path_for`) when None; a list that cannot be read or a store that does not pair with it is refused before anything is written."""
    stamp = _corpus_stamp(corpus)
    if stamp is None:
        print(f"ERROR: {corpus} has no readable manifest.json; build the corpus first (make artifact-cycle).")
        return 1
    if listed is None:
        try:
            listed = accepted.read(accepted.path_for(autosave))
        except ValueError as error:
            print(f"ERROR: {error}. Nothing was merged.")
            return 1
    guarded = autosave.resolve() == AUTOSAVE.resolve() and not yes and _server_listening()
    if dry_run or guarded:
        return _merge_into_store(
            files,
            stamp=stamp,
            autosave=autosave,
            corpus=corpus,
            journal_path=journal_path,
            dry_run=dry_run,
            refuse_a_write=guarded,
            offers_yes=offers_yes,
            clears=clears,
            listed=listed,
        )
    try:
        with locked_store(autosave):
            return _merge_into_store(
                files,
                stamp=stamp,
                autosave=autosave,
                corpus=corpus,
                journal_path=journal_path,
                dry_run=False,
                refuse_a_write=False,
                clears=clears,
                listed=listed,
            )
    except LandUnrecoverable as refused:
        print(f"ERROR: {refused}. Nothing was merged.")
        return 1


def _merge_into_store(
    files: list[Path] | None,
    *,
    stamp: str,
    autosave: Path,
    corpus: Path,
    journal_path: Path,
    dry_run: bool,
    refuse_a_write: bool,
    listed: Accepted,
    offers_yes: bool = False,
    clears: Mapping[str, str] | None = None,
) -> int:
    """Merge `files` into the store at `autosave`. None auto-picks as the command line does with no FILES: it merges the fullest stamp-aligned verdicts file at the repo root (`status.pick_fullest_verdicts`) when no autosave is aligned with the corpus or that file holds more effective verdicts than the aligned autosave, and otherwise merges nothing, succeeding when an autosave is aligned and failing when none is. An empty list merges no file. Whatever `files` names, an aligned autosave holding a record `verdict_store.well_formed` refuses, or an autosave it sets aside holding one in its override section, is refused first. `run_merge` calls it under the store's lock for a merge that writes, and without it for a dry run and for a merge into the live store while a server listens, which `refuse_a_write` then stops before it writes anything. The refusal names --yes only when `offers_yes` says the caller has one: the command line does, and the verdict update, which runs its merges in process, has none, so its refusal says only to stop the server. `clears` (unit → the clear's `at`) joins the kept store's tombstones before the files are merged, on the units the kept store holds no record for. An ams-review-verdicts/1 store, kept or set aside, that does not pair with `listed` (`accepted.pair`) is refused before anything is written; every file's records on listed units, its override records among them (`accepted.records_of`), go to the store's override section by `listed`'s rule (`merge_into`), the store written names `listed` in its `accepted` field, the set-aside store's field once it pairs, and the kept store's override section is kept whole, the set-aside store's as a stamp move keeps it (`Overrides.moved`). The auto-pick counts the aligned store's accepted units on the corpus's human units among its effective verdicts, as `status` does."""
    inputs = list(files or ())
    existing = _read_payload(autosave)
    existing_exists = autosave.exists()
    aligned = existing if existing is not None and existing["manifest_generated_at"] == stamp else None
    name = f"the store {_rel(autosave)}"
    if aligned is not None:
        malformed = malformed_in(aligned, name)
    else:
        malformed = malformed_override(existing, name) if existing is not None else None
    if malformed is not None:
        print(
            f"ERROR: {malformed}. Refusing to merge into it, and the merge wrote neither the store nor the "
            "journal: the merge keeps the records of a store stamped for the corpus, and the override section of "
            "one it sets aside, so it refuses one holding a record the carry refuses there "
            "(`verdict_store.well_formed`), as it refuses such an input file. If it is "
            "the verdict store or the cycle's prepared copy of it, restore a readable store from the journal with "
            "`uv run python -m rebuild.tools.merge_verdicts --restore-as-of <time> --apply`."
        )
        return 1
    if existing is not None:
        try:
            accepted.pair(existing, listed, store=_rel(autosave))
        except accepted.Mismatch as mismatch:
            print(
                f"ERROR: {mismatch}. Refusing to merge into it, and the merge wrote neither the store nor the journal."
            )
            return 1
    if files is None:
        hit = status.pick_fullest_verdicts(ROOT, stamp)
        base_effective = _aligned_effective(aligned, listed, corpus) if aligned is not None else None
        if hit is not None and (base_effective is None or hit[1] > base_effective):
            inputs = [hit[0]]
            print(f"auto-picked the fullest verdicts file: {_rel(hit[0])} ({hit[1]} effective verdicts)")
        elif base_effective is not None:
            print(
                f"nothing to merge: no stamp-aligned verdicts file at the repo root holds more effective verdicts "
                f"than the autosave, which holds {base_effective} for this corpus"
            )
            return 0
        else:
            print(
                "ERROR: nothing to merge — no stamp-aligned verdicts file at the repo root, and the autosave is "
                "not aligned with this corpus. Run make artifact-cycle, which carries the store onto this corpus, "
                "or restore a missing store from the journal with --restore-as-of."
            )
            return 1

    payloads: list[tuple[Path, dict]] = []
    for path in inputs:
        data = _read_payload(path)
        if data is None:
            print(f"ERROR: {_rel(path)} is not a readable ams-review-verdicts/1 document.")
            return 1
        if data["manifest_generated_at"] != stamp:
            print(
                f"ERROR: {_rel(path)} is stamped {data['manifest_generated_at']}, not the served corpus "
                f"({stamp}). Refusing to merge a file stamped for another corpus — carry it onto this one with "
                "rebuild/tools/carry_verdicts.py first."
            )
            return 1
        if (malformed := malformed_in(data, _rel(path))) is not None:
            print(
                f"ERROR: {malformed}. Refusing to merge it, and the merge wrote neither the store nor the journal: "
                "the merge refuses any input file holding a record the carry refuses (`verdict_store.well_formed`), "
                "because it writes the `at` and `note` of the record that wins each unit as they are unless "
                "false-valued, and compares `at`s to pick that record, so a record whose `at` or `note` is neither a "
                "string nor false-valued can leave a store that no later pass can carry, or, for such an `at`, stop "
                "the merge with a traceback where it compares two `at`s."
            )
            return 1
        payloads.append((path, data))

    if existing is not None and aligned is None and existing["manifest_generated_at"] > stamp:
        print(
            f"ERROR: the autosave is stamped {existing['manifest_generated_at']}, newer than the corpus at "
            f"{_rel(corpus)} ({stamp}). Refusing to merge onto an outdated corpus."
        )
        return 1

    base = journal.latest_by_unit(aligned["verdicts"]) if aligned is not None else {}
    result = dict(base)
    cleared = {unit: at for unit, at in parse_cleared(aligned).items() if unit not in base}
    overrides = Overrides.of(aligned)
    if aligned is None and existing is not None:
        overrides = Overrides.of(existing).moved(journal.now_stamp())
        if stale := parse_cleared(existing):
            named = {record.get("unit") for record in existing["verdicts"] if isinstance(record, dict)}
            _, human_ids = read_manifest(corpus)
            cleared = {unit: at for unit, at in stale.items() if unit in human_ids and unit not in named}
    kept_cleared, kept_overrides = dict(cleared), overrides.copy()
    for unit, at in (clears or {}).items():
        if unit not in result:
            cleared[unit] = max(at, cleared.get(unit, at))
    totals = dict.fromkeys(COUNT_KEYS, 0)
    for path, data in payloads:
        counts = merge_into(result, accepted.records_of(data), cleared, listed, overrides)
        for key in totals:
            totals[key] += counts[key]
        invalid = f", {counts['invalid']} invalid" if counts["invalid"] else ""
        print(
            f"{_rel(path)}: {counts['added']} added, {counts['replaced']} replaced, "
            f"{counts['kept_newer']} kept newer{_refused_by_tombstone(counts)}{_already_accepted(counts)}{invalid}"
        )

    changed = aligned is None or result != base or cleared != kept_cleared or overrides != kept_overrides
    held = {**result, **overrides.records}
    if dry_run:
        print(
            f"dry run: nothing written. Store would hold {len(held)} verdicts ({_effective(held)} "
            f"effective); {'no ' if not changed else ''}change from the current autosave."
        )
        return 0
    if not changed:
        print(
            f"nothing changed: the autosave already holds all {len(held)} verdicts "
            f"({_effective(held)} effective)."
        )
        return 0

    if refuse_a_write:
        override = ", or pass --yes when the listening server serves another checkout" if offers_yes else ""
        print(
            "ERROR: the review server is listening on port 7294. While a merge holds the verdict store's lock, the "
            "server refuses the app's saves, and the save a closing tab sends is sent again only when the app is "
            "next opened, so a verdict recorded just before a tab closes would wait in that browser until then. "
            f"Stop the server first{override}."
        )
        return 1

    stashed = None
    if existing_exists and aligned is None:
        if existing is None:
            stash = autosave.with_name(f"verdicts-autosave-corrupt-{_sanitize(journal.now_stamp())}.json")
        else:
            stash = stash_path_for(autosave, existing["manifest_generated_at"])
        os.replace(autosave, stash)
        stashed = stash.name
        print(f"stashed the previous autosave as {stashed}")

    payload = journal.payload_for(
        stamp, result, cleared, accepted=listed.field(), overrides=overrides.field()
    )
    _write_store(autosave, payload)
    old_records, old_cleared = accepted.joined(base, parse_cleared(aligned), Overrides.of(aligned))
    new_records, new_cleared = accepted.joined(result, cleared, overrides)
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=stamp,
        old_stamp=aligned["manifest_generated_at"] if aligned is not None else None,
        old_verdicts=list(old_records.values()),
        new_verdicts=list(new_records.values()),
        old_cleared=old_cleared,
        new_cleared=new_cleared,
        stashed=stashed,
        accepted=listed.field(),
    )
    print(
        f"merged {len(payloads)} file(s) into {autosave.name}: {totals['added']} added, "
        f"{totals['replaced']} replaced, {totals['kept_newer']} kept newer{_refused_by_tombstone(totals)}"
        f"{_already_accepted(totals)}; store holds {len(held)} verdicts ({_effective(held)} effective) on "
        f"manifest {stamp}"
    )
    if _server_listening():
        print("note: the review server is up — an open app tab picks this up on its next focus (or reload).")
    return 0


class UntimedRecord(ValueError):
    """Raised by `rekey_cleared` when a record and a clear land on one unit and the record's `at`, read as the verdict store, the land and the journal read it (`record.get("at") or ""`, so a missing or empty one is the oldest), is not a string, so nothing says which of the two is newer. The carry refuses a record without a string `at` (`verdict_store.well_formed`), and so does a merge in a file it is given. `unit` is the record's unit before the re-key and `at` its `at` as the record holds it."""

    def __init__(self, unit: str, at: object) -> None:
        self.unit = unit
        self.at = at
        super().__init__(
            f"a verdict record on {unit} whose `at` is not a string ({json.dumps(at)}) lands on the unit a clear "
            "lands on, so the re-key cannot weigh the two"
        )


def rekey_cleared(
    cleared: Mapping[str, str], id_map: Mapping[str, str], records: Iterable[object]
) -> tuple[dict[str, str], set[str]]:
    """Return the tombstones `cleared` (unit → the clear's `at`) with each unit id in `id_map` replaced by its target, and the targets whose records the rule drops. `records` are the records before the re-key, each naming its unit under `unit`; a tombstone on a unit one of them names is passed over, as the verdict store passes it over. Two tombstones that land on one unit keep the later clear. A tombstone that lands on a unit records land on meets the newest of them as a merge meets a record (`merge_into`): a clear at or after the record's `at` wins, so the tombstone stays and the unit's records are dropped, and a record strictly newer than the clear wins, so the tombstone is dropped. Each unit's outcome depends only on the newest clear and the newest record that land on it, so the order of `cleared` and `records` does not change it. A record that lands on a unit a tombstone lands on and whose `at` is not a string raises `UntimedRecord`, whose docstring says how the rule reads an `at`. The rule reads every record's `at` as it lists the records, but checks and compares only the `at` of the records that land on a unit a tombstone lands on. A journal restore's re-key (`rekey_store`) and `rebuild.tools.rekey_verdicts` both move a store's tombstones through it."""
    timed = [
        (record["unit"], record.get("at") or "")
        for record in records
        if isinstance(record, dict) and isinstance(record.get("unit"), str)
    ]
    named = {unit for unit, _ in timed}
    tombstones: dict[str, str] = {}
    for unit, at in cleared.items():
        target = id_map.get(unit, unit)
        if unit not in named and at > tombstones.get(target, ""):
            tombstones[target] = at
    held: dict[str, str] = {}
    for unit, at in timed:
        target = id_map.get(unit, unit)
        if target not in tombstones:
            continue
        if not isinstance(at, str):
            raise UntimedRecord(unit, at)
        held[target] = max(at, held.get(target, at))
    dropped = {unit for unit, at in held.items() if tombstones[unit] >= at}
    return {unit: at for unit, at in tombstones.items() if unit not in held or unit in dropped}, dropped


def rekey_store(
    records: dict[str, dict], cleared: dict[str, str], id_map: dict[str, str]
) -> tuple[dict[str, dict], dict[str, str]]:
    """Return the records and the tombstones with each unit id in `id_map` replaced by its target. The tombstones move by `rekey_cleared`, which also drops the records on a unit a clear at or after the newest of them lands on and raises `UntimedRecord` for a record it cannot weigh against a clear. When two records land on one unit, the newer `at` wins, as in a merge, and a tie goes to the record whose own id sorts first, so the order the records arrive in does not change the winner."""
    tombstones, dropped = rekey_cleared(
        cleared, id_map, ({**record, "unit": unit} for unit, record in records.items())
    )
    moved: dict[str, dict] = {}
    for unit, record in sorted(records.items()):
        target = id_map.get(unit, unit)
        if target in dropped:
            continue
        current = moved.get(target)
        if current is None or (record.get("at") or "") > (current.get("at") or ""):
            moved[target] = {**record, "unit": target}
    return moved, tombstones


def rekeyed_field(field: dict | None, rekey_map: Path) -> dict | None:
    """Return the `accepted` field a store replayed from before a re-key names once its ids move through `rekey_map`: the list each re-key's backup record (`rebuild.tools.rekey_verdicts`, a `backup.json` in a directory beside the map) records it wrote in place of the one `field` names, its `accepted` entry's `before` and `after`, followed through every re-key since, and `field` itself when no backup records a move from it."""
    moves: dict[str, dict | None] = {}
    for record_path in sorted(rekey_map.parent.glob(f"*/{REKEY_BACKUP_RECORD}")):
        moved = json.loads(record_path.read_text(encoding="utf-8")).get("accepted")
        if isinstance(moved, dict) and isinstance(moved.get("before"), dict):
            moves[json.dumps(moved["before"], sort_keys=True)] = moved.get("after")
    seen: set[str] = set()
    while field is not None:
        key = json.dumps(field, sort_keys=True)
        if key in seen or key not in moves:
            break
        seen.add(key)
        field = moves[key]
    return field


def run_restore(
    as_of: str,
    *,
    autosave: Path,
    journal_path: Path,
    out: Path | None,
    apply: bool,
    yes: bool,
    rekey_map: Path | None = None,
    accepted_path: Path | None = None,
) -> int:
    """Replay the journal as of `as_of` into a store that names the list its events recorded (`journal.replay_with_list`), re-keyed through `rekey_map` when given, its field moved with it (`rekeyed_field`), and write it to `out`, or with `apply` over `autosave`, which refuses a restored store that does not pair with the list at `accepted_path` (`accepted.pair`). The journal holds the store as one, so the restore puts the records and tombstones on that list's ids into the override section again (`accepted.split`); `out` is split by the list at `accepted_path` when that list reads and is the one the restored store names, and is otherwise written as one store, which pairs with no list the restore could read. A moment before the journal's first event, when that event is a close-out's, is refused naming the close-out's archive, which holds the store before it."""
    try:
        stamp, records, cleared, field = journal.replay_with_list(journal_path, as_of=as_of)
    except journal.JournalGap as gap:
        print(
            f"ERROR: {_rel(journal_path)} cannot reconstruct the store as of {as_of}: {gap}. Pick a moment before "
            f"{gap.torn_at}" + (f" or at or after {gap.resumes_at}." if gap.resumes_at else ".")
        )
        return 1
    if stamp is None:
        first = next(journal.iter_events(journal_path), None)
        if first is not None and first.get("source") == "close-out":
            print(
                f"ERROR: {_rel(journal_path)} starts at the close-out of {first.get('at')}, after {as_of}, so it holds "
                f"nothing from before it. The store that close-out found is in its archive under "
                f"{_rel(autosave.parent / CLOSE_OUT_ARCHIVES)}, which `make close-out ARGS=--undo` lays back under "
                "the verdicts made since; nothing was restored."
            )
            return 1
        print(f"ERROR: {_rel(journal_path)} holds no event at or before {as_of}; nothing to restore.")
        return 1
    if (
        malformed := malformed_record(
            journal.payload_for(stamp, records)["verdicts"],
            f"the store that {_rel(journal_path)} replays as of {as_of}",
        )
    ) is not None:
        print(
            f"ERROR: {malformed}. Refusing to restore it, and nothing was written: a restore refuses a store holding "
            "a record the carry refuses (`verdict_store.well_formed`), as the merge refuses one, because no later "
            "pass could carry it. Pick a moment before the journal recorded that record."
        )
        return 1
    if rekey_map is not None:
        id_map = json.loads(rekey_map.read_text(encoding="utf-8"))["renamed"]
        restored, restored_cleared = records, cleared
        records, cleared = rekey_store(restored, restored_cleared, id_map)
        moved = sum(1 for unit in restored if unit in id_map)
        dropped = sum(1 for unit in restored if id_map.get(unit, unit) in cleared)
        landed = [unit for unit in restored_cleared if id_map.get(unit, unit) in cleared]
        moved_cleared = sum(1 for unit in landed if unit in id_map)
        print(
            f"re-keyed {moved} of {len(restored)} restored verdicts and {moved_cleared} of {len(restored_cleared)} "
            f"restored tombstones through {_rel(rekey_map)}; dropped {dropped} verdicts that a clear at or after "
            f"them landed on and {len(restored_cleared) - len(landed)} tombstones that a newer verdict landed on"
        )
        field = rekeyed_field(field, rekey_map)
    if not apply:
        assert out is not None
        held = _list_named(accepted_path, field)
        _write_store(out, _restored(stamp, records, cleared, field, held))
        print(
            f"wrote {_rel(out)}: {len(records)} verdicts ({_effective(records)} effective) as of {as_of}, "
            f"manifest {stamp}. To make it the live store, re-run with --apply (the current autosave is "
            "stashed first)."
        )
        return 0
    if autosave.resolve() == AUTOSAVE.resolve() and _server_listening() and not yes:
        print(
            "ERROR: the review server is listening on port 7294. An open tab would merge its own store right "
            "back over the restore on its next focus. Stop the server first, or pass --yes to apply anyway."
        )
        return 1
    try:
        listed = accepted.read(accepted_path if accepted_path is not None else accepted.path_for(autosave))
        accepted.pair(
            journal.payload_for(stamp, records, cleared, accepted=field),
            listed,
            store=f"the store {_rel(journal_path)} replays as of {as_of}",
        )
    except ValueError as error:
        print(f"ERROR: {error}. Nothing was restored.")
        return 1
    payload = _restored(stamp, records, cleared, field, listed)
    try:
        with locked_store(autosave):
            return _apply_restore(
                as_of, stamp, records, payload, autosave=autosave, journal_path=journal_path
            )
    except LandUnrecoverable as refused:
        print(f"ERROR: {refused}. Nothing was restored.")
        return 1


def _list_named(path: Path | None, field: dict | None) -> Accepted | None:
    """Return the list at `path` when it reads and is the one `field` names, which a restore's preview splits its store by, or None."""
    if path is None or field is None:
        return None
    try:
        held = accepted.read(path)
    except accepted.Malformed:
        return None
    return held if held.field() == field else None


def _restored(
    stamp: str, records: dict[str, dict], cleared: dict[str, str], field: dict | None, listed: Accepted | None
) -> dict:
    """Return the restored store as a document: the replayed records and tombstones, those on `listed`'s ids as its override section (`accepted.split`), or all as the store's own without a list."""
    if listed is None:
        return journal.payload_for(stamp, records, cleared, accepted=field)
    own, tombstones, overrides = accepted.split(records, cleared, listed.verdicts)
    return journal.payload_for(stamp, own, tombstones, accepted=field, overrides=overrides.field())


def _apply_restore(
    as_of: str, stamp: str, records: dict[str, dict], payload: dict, *, autosave: Path, journal_path: Path
) -> int:
    existing = _read_payload(autosave)
    unreadable = existing is not None and malformed_in(existing, "the store") is not None
    old_records, old_cleared = (
        accepted.joined(
            journal.latest_by_unit(existing["verdicts"]), parse_cleared(existing), Overrides.of(existing)
        )
        if existing is not None and not unreadable
        else ({}, {})
    )
    stashed = None
    if autosave.exists():
        stash = autosave.with_name(f"verdicts-autosave-pre-restore-{_sanitize(journal.now_stamp())}.json")
        os.replace(autosave, stash)
        stashed = stash.name
        print(f"stashed the previous autosave as {stashed}")
    _write_store(autosave, payload)
    _every, journal_cleared = accepted.joined(
        journal.latest_by_unit(payload["verdicts"]), parse_cleared(payload), Overrides.of(payload)
    )
    journal.record_transition(
        journal_path,
        source="restore",
        stamp=stamp,
        old_stamp=existing["manifest_generated_at"] if existing is not None else None,
        old_verdicts=list(old_records.values()),
        new_verdicts=list(records.values()),
        old_cleared=old_cleared,
        new_cleared=journal_cleared,
        stashed=stashed,
        base=True if unreadable else None,
        accepted=payload.get("accepted"),
    )
    print(
        f"restored {autosave.name} to {as_of}: {len(records)} verdicts ({_effective(records)} effective) "
        f"on manifest {stamp}"
    )
    return 0


def run_list(journal_path: Path) -> int:
    count = 0
    for event in journal.iter_events(journal_path):
        count += 1
        markers = []
        if event.get("base"):
            markers.append("base")
        if event.get("stashed"):
            markers.append(f"stashed {event['stashed']}")
        suffix = f"  [{', '.join(markers)}]" if markers else ""
        print(
            f"{event.get('at')}  {event.get('source', '?'):<8}  stamp {event.get('stamp')}  "
            f"+{event.get('sets', 0)} -{event.get('clears', 0)}{suffix}"
        )
    if count == 0:
        print(f"no journal events yet ({_rel(journal_path)} is absent or empty)")
    return 0


def _preview_list(given: Path | None, autosave: Path) -> Path | None:
    """Return the list a restore's preview splits its store by: the one `--accepted` names, or else the one beside a store named verdicts-autosave.json, or None for a store under any other name."""
    if given is not None:
        return given
    return accepted.path_for(autosave) if autosave.name == journal.AUTOSAVE_NAME else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge stamp-aligned verdicts files into the live autosave (the app's import, headless), or replay the verdict journal."
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="ams-review-verdicts/1 files to merge, each stamped for the corpus and holding no record the carry refuses (default: the fullest verdicts file)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change without writing anything"
    )
    parser.add_argument("--list", action="store_true", help="list the journal's events and exit")
    parser.add_argument(
        "--restore-as-of",
        metavar="TIME",
        help="reconstruct the store as of this UTC time (ISO prefix, e.g. 2026-07-19T03:00) from the journal",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="with --restore-as-of: replace the live autosave with the reconstruction",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="proceed even while the review server is listening (a plain merge that would write the live store and --restore-as-of --apply both refuse otherwise)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=f"with --restore-as-of and no --apply: where to write the reconstruction (default: verdicts-restored-<time>.json beside --autosave, which for the live store is the repo root; a store not named {journal.AUTOSAVE_NAME} needs --out)",
    )
    parser.add_argument(
        "--rekey-map",
        type=Path,
        help="with --restore-as-of: move the restored unit ids through this rebuild.tools.rekey_verdicts id map",
    )
    parser.add_argument(
        "--autosave", type=Path, default=AUTOSAVE, help="the live store (default: %(default)s)"
    )
    parser.add_argument(
        "--corpus", "--surface", type=Path, default=CORPUS, help="the served corpus (default: %(default)s)"
    )
    parser.add_argument(
        "--journal",
        type=Path,
        help=f"the journal file (default: the {journal.JOURNAL_NAME} beside --autosave, which for the live store is {JOURNAL}; a store not named {journal.AUTOSAVE_NAME} needs --journal)",
    )
    parser.add_argument(
        "--accepted",
        type=Path,
        help=f"the accepted list the store pairs with (default: rebuild/accepted-units.txt beside --autosave; a store not named {journal.AUTOSAVE_NAME} needs --accepted to merge or to apply a restore)",
    )
    args = parser.parse_args(argv)
    try:
        journal_path = args.journal or journal.journal_path_for(args.autosave)
    except ValueError as error:
        parser.error(str(error))

    def accepted_path() -> Path:
        try:
            return args.accepted or accepted.path_for(args.autosave)
        except ValueError as error:
            parser.error(str(error))

    if args.list:
        return run_list(journal_path)
    if args.restore_as_of:
        out = args.out
        if out is None and not args.apply:
            try:
                out = journal.beside_store(
                    args.autosave, f"verdicts-restored-{_sanitize(args.restore_as_of)}.json", "--out"
                )
            except ValueError as error:
                parser.error(str(error))
        return run_restore(
            args.restore_as_of,
            autosave=args.autosave,
            journal_path=journal_path,
            out=out,
            apply=args.apply,
            yes=args.yes,
            rekey_map=args.rekey_map,
            accepted_path=accepted_path() if args.apply else _preview_list(args.accepted, args.autosave),
        )
    listed_path = accepted_path()
    try:
        listed = accepted.read(listed_path)
    except accepted.Malformed as malformed:
        print(f"ERROR: {malformed}. Nothing was merged.")
        return 1
    return run_merge(
        args.files or None,
        autosave=args.autosave,
        corpus=args.corpus,
        journal_path=journal_path,
        dry_run=args.dry_run,
        yes=args.yes,
        offers_yes=True,
        listed=listed,
    )


if __name__ == "__main__":
    sys.exit(main())
