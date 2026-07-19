"""Headless replacement for the review app's Import dialog: merge stamp-aligned ams-review-verdicts/1 files into verdicts-autosave.json with the exact union the app computes (per unit, the strictly newer `at` wins), so the artifact cycle lands carried verdicts without a browser round-trip and the app just picks them up on boot or focus. The existing aligned autosave is always part of the union and the result can only grow, so a merge never drops a verdict; a stale-stamped autosave is stashed aside first, exactly like the server's /autosave handler; inputs stamped for a different surface are refused outright — carry_verdicts.py is the cross-surface bridge, there is no force path. A merge that would write refuses while the review server is listening (an open tab would flush its own store back over the write on its next focus, exactly as --restore-as-of --apply guards against); stop the server or pass --yes. Every write is appended to verdicts-journal.ndjson (rebuild.review.journal), and --restore-as-of replays that journal to recover the store as of any recorded moment.

Usage:
  uv run python -m rebuild.tools.merge_verdicts [FILES ...]     # no FILES: merge the frontier file verdict-ready names
  uv run python -m rebuild.tools.merge_verdicts --dry-run FILES ...
  uv run python -m rebuild.tools.merge_verdicts --list
  uv run python -m rebuild.tools.merge_verdicts --restore-as-of 2026-07-19T03:00 [--apply [--yes]]
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

AUTOSAVE = ROOT / "verdicts-autosave.json"
SURFACE = ROOT / "rebuild" / "out" / "review"
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


def merge_into(result: dict[str, dict], verdicts) -> dict:
    counts = {"added": 0, "replaced": 0, "kept_newer": 0, "invalid": 0}
    for unit, record in sorted(journal.latest_by_unit(verdicts).items()):
        if record.get("verdict") not in VERDICT_KINDS:
            counts["invalid"] += 1
            continue
        current = result.get(unit)
        if current is not None and (current.get("at") or "") >= (record.get("at") or ""):
            counts["kept_newer"] += 1
            continue
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


def _server_listening() -> bool:
    from rebuild.tools.artifact_cycle import server_listening

    return server_listening()


def _surface_stamp(surface: Path) -> str | None:
    try:
        stamp = json.loads((surface / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        return None
    return stamp if isinstance(stamp, str) else None


def run_merge(
    files: list[Path], *, autosave: Path, surface: Path, journal_path: Path, dry_run: bool, yes: bool = False
) -> int:
    stamp = _surface_stamp(surface)
    if stamp is None:
        print(f"ERROR: {surface} has no readable manifest.json; build the surface first (make artifact-cycle).")
        return 1

    inputs = list(files)
    existing = _read_payload(autosave)
    existing_exists = autosave.exists()
    aligned = existing is not None and existing["manifest_generated_at"] == stamp
    if not inputs:
        hit = status.pick_frontier(ROOT, stamp)
        if hit is not None:
            inputs = [hit[0]]
            print(f"auto-picked the frontier file: {_rel(hit[0])} ({hit[1]} effective verdicts)")
        elif aligned:
            base_records = journal.latest_by_unit(existing["verdicts"])
            print(
                f"nothing to merge: no stamp-aligned verdicts file, and the autosave already holds "
                f"{_effective(base_records)} effective verdicts for this surface"
            )
            return 0
        else:
            print(
                "ERROR: nothing to merge — no stamp-aligned verdicts file at the repo root or under "
                "rebuild/evidence, and the autosave is not aligned with this surface. Run make artifact-cycle "
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
                f"ERROR: {_rel(path)} is stamped {data['manifest_generated_at']}, not the served surface "
                f"({stamp}). Refusing to join unit ids across surfaces — re-resolve it with "
                "rebuild/tools/carry_verdicts.py instead."
            )
            return 1
        payloads.append((path, data))

    if existing is not None and not aligned and existing["manifest_generated_at"] > stamp:
        print(
            f"ERROR: the autosave is stamped {existing['manifest_generated_at']}, newer than the surface at "
            f"{_rel(surface)} ({stamp}). Refusing to merge onto an outdated surface."
        )
        return 1

    base = journal.latest_by_unit(existing["verdicts"]) if aligned else {}
    result = dict(base)
    totals = {"added": 0, "replaced": 0, "kept_newer": 0, "invalid": 0}
    for path, data in payloads:
        counts = merge_into(result, data["verdicts"])
        for key in totals:
            totals[key] += counts[key]
        invalid = f", {counts['invalid']} invalid" if counts["invalid"] else ""
        print(
            f"{_rel(path)}: {counts['added']} added, {counts['replaced']} replaced, "
            f"{counts['kept_newer']} kept newer{invalid}"
        )

    dropped = sorted(set(base) - set(result))
    if dropped:
        print(f"ERROR: the merge would drop {len(dropped)} verdicts (first: {dropped[0]}); refusing to write.")
        return 1

    changed = (not aligned and (existing_exists or result)) or result != base
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

    if _server_listening() and not yes:
        print(
            "ERROR: the review server is listening on port 7294. An open tab would merge its own store right "
            "back over this merge on its next focus. Stop the server first (make review-cycle runs the merge "
            "with the server down), or pass --yes to merge anyway."
        )
        return 1

    stashed = None
    if existing_exists and not aligned:
        if existing is None:
            stash = autosave.with_name(f"verdicts-autosave-corrupt-{_sanitize(journal.now_stamp())}.json")
        else:
            stash = stash_path_for(autosave, existing["manifest_generated_at"])
        os.replace(autosave, stash)
        stashed = stash.name
        print(f"stashed the previous autosave as {stashed}")

    payload = journal.payload_for(stamp, result)
    _write_store(autosave, payload)
    journal.record_transition(
        journal_path,
        source="merge",
        stamp=stamp,
        old_stamp=existing["manifest_generated_at"] if aligned else None,
        old_verdicts=existing["verdicts"] if aligned else [],
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


def run_restore(
    as_of: str, *, autosave: Path, journal_path: Path, out: Path | None, apply: bool, yes: bool
) -> int:
    stamp, records = journal.replay(journal_path, as_of=as_of)
    if stamp is None:
        print(f"ERROR: {_rel(journal_path)} holds no event at or before {as_of}; nothing to restore.")
        return 1
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
    if _server_listening() and not yes:
        print(
            "ERROR: the review server is listening on port 7294. An open tab would merge its own store right "
            "back over the restore on its next focus. Stop the server first, or pass --yes to apply anyway."
        )
        return 1
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
    parser.add_argument("files", nargs="*", type=Path, help="ams-review-verdicts/1 files to merge (default: the frontier file)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing anything")
    parser.add_argument("--list", action="store_true", help="list the journal's events and exit")
    parser.add_argument(
        "--restore-as-of",
        metavar="TIME",
        help="reconstruct the store as of this UTC time (ISO prefix, e.g. 2026-07-19T03:00) from the journal",
    )
    parser.add_argument("--apply", action="store_true", help="with --restore-as-of: replace the live autosave with the reconstruction")
    parser.add_argument("--yes", action="store_true", help="proceed even while the review server is listening (a plain merge and --restore-as-of --apply both refuse otherwise)")
    parser.add_argument("--out", type=Path, help="with --restore-as-of: where to write the reconstruction")
    parser.add_argument("--autosave", type=Path, default=AUTOSAVE, help="the live store (default: %(default)s)")
    parser.add_argument("--surface", type=Path, default=SURFACE, help="the served surface (default: %(default)s)")
    parser.add_argument("--journal", type=Path, default=JOURNAL, help="the journal file (default: %(default)s)")
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
        )
    return run_merge(
        args.files,
        autosave=args.autosave,
        surface=args.surface,
        journal_path=args.journal,
        dry_run=args.dry_run,
        yes=args.yes,
    )


if __name__ == "__main__":
    sys.exit(main())
