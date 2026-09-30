"""Merge stamp-aligned ams-review-verdicts/1 files into verdicts-autosave.json without a browser, as the review app's Import dialog does: per unit, the record with the strictly newer `at` wins. The artifact cycle uses it to merge carried verdicts, and the app reads the result on boot or focus. The existing aligned autosave is always part of the union, so a merge never drops a verdict. An autosave stamped for another corpus is moved aside first (`stash_path_for`). An input stamped for another corpus is refused; `carry_verdicts.py` moves verdicts between corpora, and there is no override. An aligned merge keeps the store's tombstones (the units the app cleared, each with the time of the clear), and a tombstone wins over an incoming record whose `at` is not after the clear. A merge that writes reads, writes and journals the store under the store's lock (`rebuild.review.store_lock`), which the review server holds for each save, so a merge waits for a save in flight, and the server answers a save made during the merge with a retryable 503 and picks the merged file up by itself afterward. The app retries the save a closing tab sends only when it is next opened, so a merge that would write the live store refuses while the review server is listening, unless --yes is passed (for a server that serves another checkout); a dry run and a refused merge take no lock. `--restore-as-of --apply` has the same refusal and takes the lock too. Every write is appended to verdicts-journal.ndjson (`rebuild.review.journal`), and `--restore-as-of` replays that journal to recover the store as of any recorded time. `--rekey-map` moves a replayed store's unit ids through the id map `rebuild.tools.rekey_verdicts` writes, for a time before that re-key.

Usage:
  uv run python -m rebuild.tools.merge_verdicts [FILES ...]     # no FILES: merge the fullest verdicts file verdict-ready names
  uv run python -m rebuild.tools.merge_verdicts --dry-run FILES ...
  uv run python -m rebuild.tools.merge_verdicts --list
  uv run python -m rebuild.tools.merge_verdicts --restore-as-of 2026-07-19T03:00 [--apply [--yes]]
  uv run python -m rebuild.tools.merge_verdicts --restore-as-of 2026-07-19T03:00 --rekey-map var/keep/issue-357-rekey/unit-id-map.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.review import journal, status  # noqa: E402
from rebuild.review.serve import parse_autosave_payload, stash_path_for  # noqa: E402
from rebuild.review.store_lock import store_lock  # noqa: E402
from rebuild.review.verdict_store import parse_cleared  # noqa: E402
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
    files: list[Path], *, autosave: Path, corpus: Path, journal_path: Path, dry_run: bool, yes: bool = False
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
        )
    with store_lock(autosave):
        return _merge_into_store(
            files,
            stamp=stamp,
            autosave=autosave,
            corpus=corpus,
            journal_path=journal_path,
            dry_run=False,
            refuse_a_write=False,
        )


def _merge_into_store(
    files: list[Path],
    *,
    stamp: str,
    autosave: Path,
    corpus: Path,
    journal_path: Path,
    dry_run: bool,
    refuse_a_write: bool,
) -> int:
    """Merge `files` into the store at `autosave`. `run_merge` calls it under the store's lock for a merge that writes, and without it for a dry run and for a merge into the live store while a server listens, which `refuse_a_write` then stops before it writes anything."""
    inputs = list(files)
    existing = _read_payload(autosave)
    existing_exists = autosave.exists()
    aligned = existing if existing is not None and existing["manifest_generated_at"] == stamp else None
    if not inputs:
        hit = status.pick_fullest_verdicts(ROOT, stamp)
        if hit is not None:
            inputs = [hit[0]]
            print(f"auto-picked the fullest verdicts file: {_rel(hit[0])} ({hit[1]} effective verdicts)")
        elif aligned is not None:
            base_records = journal.latest_by_unit(aligned["verdicts"])
            print(
                f"nothing to merge: no stamp-aligned verdicts file, and the autosave already holds "
                f"{_effective(base_records)} effective verdicts for this corpus"
            )
            return 0
        else:
            print(
                "ERROR: nothing to merge — no stamp-aligned verdicts file at the repo root or under "
                "rebuild/evidence, and the autosave is not aligned with this corpus. Run make artifact-cycle "
                "(or rebuild/tools/carry_verdicts.py) to produce a carried file first."
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

    changed = (aligned is None and (existing_exists or result)) or result != base
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

    payload = journal.payload_for(stamp, result)
    if cleared:
        payload["cleared"] = [{"unit": unit, "at": at} for unit, at in sorted(cleared.items())]
    _write_store(autosave, payload)
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=stamp,
        old_stamp=aligned["manifest_generated_at"] if aligned is not None else None,
        old_verdicts=aligned["verdicts"] if aligned is not None else [],
        new_verdicts=payload["verdicts"],
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


def rekey_records(records: dict[str, dict], id_map: dict[str, str]) -> dict[str, dict]:
    """Return the records with each unit id in `id_map` replaced by its target. When two records land on one unit, the newer `at` wins, as in a merge."""
    moved: dict[str, dict] = {}
    for unit, record in records.items():
        target = id_map.get(unit, unit)
        current = moved.get(target)
        if current is None or (record.get("at") or "") > (current.get("at") or ""):
            moved[target] = {**record, "unit": target}
    return moved


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
        stamp, records = journal.replay(journal_path, as_of=as_of)
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
        moved = sum(1 for unit in records if unit in id_map)
        records = rekey_records(records, id_map)
        print(f"re-keyed {moved} of {len(records)} restored verdicts through {_rel(rekey_map)}")
    payload = journal.payload_for(stamp, records)
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
    with store_lock(autosave):
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
        help="ams-review-verdicts/1 files to merge (default: the fullest verdicts file)",
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
        args.files,
        autosave=args.autosave,
        corpus=args.corpus,
        journal_path=args.journal,
        dry_run=args.dry_run,
        yes=args.yes,
    )


if __name__ == "__main__":
    sys.exit(main())
