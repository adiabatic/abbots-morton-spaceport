"""Merge stamp-aligned ams-review-verdicts/1 files into verdicts-autosave.json without a browser, as the review app's Import dialog does: per unit, the record with the strictly newer `at` wins. The artifact cycle uses it to merge carried verdicts, and the app reads the result on boot or focus. The existing aligned autosave is always part of the union, so a merge never drops a verdict. Unless it is a dry run or refused, a merge writes the store when its records or tombstones differ from the aligned autosave's, and always when no autosave is aligned with the corpus, so a merge into a missing store leaves one stamped for the corpus even when it holds no verdicts, for the verdict update's fills to read. An autosave that is an ams-review-verdicts/1 document stamped for an older corpus (a `manifest_generated_at` that sorts before the corpus's `generated_at`) is moved aside first (`stash_path_for`), and one stamped for a newer corpus is refused, since the merge would move the store back onto an outdated corpus. An autosave that is not an ams-review-verdicts/1 document (`parse_autosave_payload`) is moved aside as verdicts-autosave-corrupt-<time>.json whatever stamp it names, keeping none of its verdicts or tombstones. An input stamped for another corpus is refused; `carry_verdicts.py` moves verdicts between corpora, and there is no override. An input holding a verdict record the carry refuses (`carry_verdicts.well_formed`) is refused too, naming the file and the first such record's unit, or its index when it names no unit: the merge refuses any file the carry refuses, because a record whose non-string `at` or `note` the merge copied as is would leave a store that no later pass can carry. A merge keeps the store's tombstones (the units the app cleared, each with the time of the clear), and a tombstone wins over an incoming record whose `at` is not after the clear. A merge onto an ams-review-verdicts/1 store stamped for an older corpus keeps none of that store's verdicts but keeps its tombstones on the corpus's human units, as the review server's move of a store onto the served corpus (rule 3 in `rebuild.review.verdict_store`) and the land (`landing.overlay`) keep them, so the fills a pass that moves the stamp merges after the carry meet the same tombstones as on a pass that keeps it. A caller in the same process can add tombstones with `run_merge`'s `clears`, which the merge keeps as the store's own on units the kept store holds no record for: the verdict update passes the skips of a store it carries onto the corpus before a direct merge (`verdict_update.skip_clears`). A merge that writes reads, writes and journals the store under the store's lock (`rebuild.review.store_lock`, taken through `landing.locked_store`, which first finishes a land whose holder died), which the review server holds for each save, so a merge waits for a save in flight, and the server answers a save made during the merge with a retryable 503 and picks the merged file up by itself afterward. The app retries the save a closing tab sends only when it is next opened, so a merge that would write the live store refuses while the review server is listening, unless --yes is passed (for a server that serves another checkout); a dry run and a merge refused because the server is listening take no lock. `--restore-as-of --apply` has the same refusal and takes the lock too. Every write is appended to verdicts-journal.ndjson (`rebuild.review.journal`), and `--restore-as-of` replays that journal to recover the store as of any recorded time, its records and its tombstones (`journal.replay_store`), so a unit cleared by then stays cleared through a later merge of a file that still holds its older verdict, unless the replay starts from a base event written after the clear with no `cleared` list (the `rebuild.review.journal` docstring says where a replay of lines without the tombstone fields differs from the store). `--rekey-map` moves a replayed store's unit ids, its tombstones' among them, through the id map `rebuild.tools.rekey_verdicts` writes, for a time before that re-key.

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

from rebuild.review import journal, status  # noqa: E402
from rebuild.review.landing import locked_store, read_manifest  # noqa: E402
from rebuild.review.serve import parse_autosave_payload, stash_path_for  # noqa: E402
from rebuild.review.verdict_store import parse_cleared  # noqa: E402
from rebuild.tools.carry_verdicts import well_formed  # noqa: E402
from rebuild.tools.review_server import server_listening as _server_listening  # noqa: E402

AUTOSAVE = ROOT / "verdicts-autosave.json"
CORPUS = ROOT / "rebuild" / "out" / "review"
JOURNAL = ROOT / journal.JOURNAL_NAME
VERDICT_KINDS = frozenset({"approve", "reject", "either", "identical", "neither", "skip"})


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


def merge_into(result: dict[str, dict], verdicts, cleared: dict[str, str] | None = None) -> dict:
    """Merge `verdicts` into `result` in place, the strictly newer `at` winning per unit, and return the counts. `cleared` holds the store's tombstones (unit → the clear's `at`), which count as a newer state than any record whose `at` is not after the clear; a record that wins over a tombstone removes it from `cleared`."""
    counts = {"added": 0, "replaced": 0, "kept_newer": 0, "invalid": 0}
    for unit, record in sorted(journal.latest_by_unit(verdicts).items()):
        if record.get("verdict") not in VERDICT_KINDS:
            counts["invalid"] += 1
            continue
        current = result.get(unit)
        at = record.get("at") or ""
        if current is not None and (current.get("at") or "") >= at:
            counts["kept_newer"] += 1
            continue
        if current is None and cleared is not None and unit in cleared:
            if cleared[unit] >= at:
                counts["kept_newer"] += 1
                continue
            del cleared[unit]
        result[unit] = {
            "unit": unit,
            "verdict": record["verdict"],
            "note": record.get("note") or "",
            "at": record.get("at") or "",
        }
        counts["replaced" if current is not None else "added"] += 1
    return counts


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
    clears: Mapping[str, str] | None = None,
) -> int:
    stamp = _corpus_stamp(corpus)
    if stamp is None:
        print(f"ERROR: {corpus} has no readable manifest.json; build the corpus first (make artifact-cycle).")
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
            clears=clears,
        )
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
        )


def _merge_into_store(
    files: list[Path] | None,
    *,
    stamp: str,
    autosave: Path,
    corpus: Path,
    journal_path: Path,
    dry_run: bool,
    refuse_a_write: bool,
    clears: Mapping[str, str] | None = None,
) -> int:
    """Merge `files` into the store at `autosave`. None auto-picks as the command line does with no FILES: it merges the fullest stamp-aligned verdicts file at the repo root (`status.pick_fullest_verdicts`) when no autosave is aligned with the corpus or that file holds more effective verdicts than the aligned autosave, and otherwise merges nothing, succeeding when an autosave is aligned and failing when none is. An empty list merges no file. `run_merge` calls it under the store's lock for a merge that writes, and without it for a dry run and for a merge into the live store while a server listens, which `refuse_a_write` then stops before it writes anything. `clears` (unit → the clear's `at`) joins the kept store's tombstones before the files are merged, on the units the kept store holds no record for."""
    inputs = list(files or ())
    existing = _read_payload(autosave)
    existing_exists = autosave.exists()
    aligned = existing if existing is not None and existing["manifest_generated_at"] == stamp else None
    if files is None:
        hit = status.pick_fullest_verdicts(ROOT, stamp)
        base_effective = (
            _effective(journal.latest_by_unit(aligned["verdicts"])) if aligned is not None else None
        )
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
        malformed = next(
            (index for index, record in enumerate(data["verdicts"]) if not well_formed(record)), None
        )
        if malformed is not None:
            record = data["verdicts"][malformed]
            unit = record.get("unit") if isinstance(record, dict) else None
            where = f"on {unit}" if isinstance(unit, str) else f"at index {malformed} of its `verdicts` list"
            print(
                f"ERROR: {_rel(path)} holds a verdict record {where} that is not an object with a string `unit`, "
                "`verdict` and `at` and a string `note` if any. Refusing to merge it, and the merge wrote neither "
                "the store nor the journal: the merge refuses any file the carry refuses "
                "(`carry_verdicts.well_formed`), because a record whose non-string `at` or `note` the merge copied "
                "as is would leave a store that no later pass can carry."
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
    if aligned is None and existing is not None and (stale := parse_cleared(existing)):
        held = journal.latest_by_unit(existing["verdicts"])
        _, human_ids = read_manifest(corpus)
        cleared = {unit: at for unit, at in stale.items() if unit in human_ids and unit not in held}
    kept_cleared = dict(cleared)
    for unit, at in (clears or {}).items():
        if unit not in result:
            cleared[unit] = max(at, cleared.get(unit, at))
    totals = {"added": 0, "replaced": 0, "kept_newer": 0, "invalid": 0}
    for path, data in payloads:
        counts = merge_into(result, data["verdicts"], cleared)
        for key in totals:
            totals[key] += counts[key]
        invalid = f", {counts['invalid']} invalid" if counts["invalid"] else ""
        print(
            f"{_rel(path)}: {counts['added']} added, {counts['replaced']} replaced, "
            f"{counts['kept_newer']} kept newer{invalid}"
        )

    changed = aligned is None or result != base or cleared != kept_cleared
    if dry_run:
        print(
            f"dry run: nothing written. Store would hold {len(result)} verdicts ({_effective(result)} "
            f"effective); {'no ' if not changed else ''}change from the current autosave."
        )
        return 0
    if not changed:
        print(
            f"nothing changed: the autosave already holds all {len(result)} verdicts "
            f"({_effective(result)} effective)."
        )
        return 0

    if refuse_a_write:
        print(
            "ERROR: the review server is listening on port 7294. While a merge holds the verdict store's lock, the "
            "server refuses the app's saves, and the save a closing tab sends is sent again only when the app is "
            "next opened, so a verdict recorded just before a tab closes would wait in that browser until then. Stop the server first (make review-cycle runs the "
            "merge with the server down), or pass --yes when the listening server serves another checkout."
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

    payload = journal.payload_for(stamp, result, cleared)
    _write_store(autosave, payload)
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=stamp,
        old_stamp=aligned["manifest_generated_at"] if aligned is not None else None,
        old_verdicts=aligned["verdicts"] if aligned is not None else [],
        new_verdicts=payload["verdicts"],
        old_cleared=parse_cleared(aligned),
        new_cleared=cleared,
        stashed=stashed,
    )
    print(
        f"merged {len(payloads)} file(s) into {autosave.name}: {totals['added']} added, "
        f"{totals['replaced']} replaced, {totals['kept_newer']} kept newer; store holds "
        f"{len(result)} verdicts ({_effective(result)} effective) on manifest {stamp}"
    )
    if _server_listening():
        print("note: the review server is up — an open app tab picks this up on its next focus (or reload).")
    return 0


class UntimedRecord(ValueError):
    """Raised by `rekey_cleared` when a record and a clear land on one unit and the record's `at`, read as the verdict store, the land and the journal read it (`record.get("at") or ""`, so a missing or empty one is the oldest), is not a string, so nothing says which of the two is newer. The carry refuses a record without a string `at` (`carry_verdicts.well_formed`), and so does a merge in a file it is given. `unit` is the record's unit before the re-key and `at` its `at` as the record holds it."""

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


def run_restore(
    as_of: str,
    *,
    autosave: Path,
    journal_path: Path,
    out: Path | None,
    apply: bool,
    yes: bool,
    rekey_map: Path | None = None,
) -> int:
    try:
        stamp, records, cleared = journal.replay_store(journal_path, as_of=as_of)
    except journal.JournalGap as gap:
        print(
            f"ERROR: {_rel(journal_path)} cannot reconstruct the store as of {as_of}: {gap}. Pick a moment before "
            f"{gap.torn_at}" + (f" or at or after {gap.resumes_at}." if gap.resumes_at else ".")
        )
        return 1
    if stamp is None:
        print(f"ERROR: {_rel(journal_path)} holds no event at or before {as_of}; nothing to restore.")
        return 1
    if rekey_map is not None:
        id_map = json.loads(rekey_map.read_text(encoding="utf-8"))["renamed"]
        restored = records
        try:
            records, cleared = rekey_store(restored, cleared, id_map)
        except UntimedRecord as untimed:
            print(
                f"ERROR: in the store that {_rel(journal_path)} replays as of {as_of}, {untimed}. Nothing was written."
            )
            return 1
        moved = sum(1 for unit in restored if unit in id_map)
        dropped = sum(1 for unit in restored if id_map.get(unit, unit) in cleared)
        print(
            f"re-keyed {moved} of {len(restored)} restored verdicts through {_rel(rekey_map)}; dropped {dropped} "
            "that a clear at or after them landed on"
        )
    payload = journal.payload_for(stamp, records, cleared)
    if not apply:
        target = out if out is not None else ROOT / f"verdicts-restored-{_sanitize(as_of)}.json"
        _write_store(target, payload)
        print(
            f"wrote {_rel(target)}: {len(records)} verdicts ({_effective(records)} effective) as of {as_of}, "
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
    with locked_store(autosave):
        return _apply_restore(as_of, stamp, records, payload, autosave=autosave, journal_path=journal_path)


def _apply_restore(
    as_of: str, stamp: str, records: dict[str, dict], payload: dict, *, autosave: Path, journal_path: Path
) -> int:
    existing = _read_payload(autosave)
    stashed = None
    if autosave.exists():
        stash = autosave.with_name(f"verdicts-autosave-pre-restore-{_sanitize(journal.now_stamp())}.json")
        os.replace(autosave, stash)
        stashed = stash.name
        print(f"stashed the previous autosave as {stashed}")
    _write_store(autosave, payload)
    journal.record_transition(
        journal_path,
        source="restore",
        stamp=stamp,
        old_stamp=existing["manifest_generated_at"] if existing is not None else None,
        old_verdicts=existing["verdicts"] if existing is not None else [],
        new_verdicts=payload["verdicts"],
        old_cleared=parse_cleared(existing),
        new_cleared=parse_cleared(payload),
        stashed=stashed,
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
    parser.add_argument("--out", type=Path, help="with --restore-as-of: where to write the reconstruction")
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
        "--journal", type=Path, default=JOURNAL, help="the journal file (default: %(default)s)"
    )
    args = parser.parse_args(argv)

    if args.list:
        return run_list(args.journal)
    if args.restore_as_of:
        return run_restore(
            args.restore_as_of,
            autosave=args.autosave,
            journal_path=args.journal,
            out=args.out,
            apply=args.apply,
            yes=args.yes,
            rekey_map=args.rekey_map,
        )
    return run_merge(
        args.files or None,
        autosave=args.autosave,
        corpus=args.corpus,
        journal_path=args.journal,
        dry_run=args.dry_run,
        yes=args.yes,
    )


if __name__ == "__main__":
    sys.exit(main())
