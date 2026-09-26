"""Move recorded verdicts from the unit ids of the review corpus built before the #357 renames to the ids of the corpus built after them. It runs once, after the first corpus build that follows the renames and before the carry that follows that build.

A unit's id is derived from its carry projection (`unit_cache.carry_projection`, `unit_cache.unit_id_for`), and the renames of #360, #362, #366, #372, #380, #386, #388 and #438 changed values inside it: the ledger's class ids and the unmatched group `seam-loss-withdrawal` (`CLASS_RENAMES`, in `class`, `config_classes` and `config_class_note`), the `seam` kind (kinds are a sorted list, so both directions sort after renaming), the `seams` field of both sides, the single-stance label `hapax` in the after cells and the summaries, and the summary phrases that named seams and the structural floor (`SUMMARY_RENAMES`). The rest of each unit is unchanged, so its pre-rename projection is its post-rename projection with the renames reversed (`pre_rename_projection`), and its pre-rename id is that projection's `unit_id_for`. The tool reads every fragment of the post-rename corpus, machine units included, and pairs the two ids.

Two checks guard the pairs. `pre_rename_projection` applies the forward renames to the reversed projection and refuses the unit unless that gives back the projection it started from, which checks that the tables invert on this corpus and stops on the first unit of a corpus still built with the old vocabulary. It cannot see a rename the tables leave out: such a unit reverses to a projection it never had, so its derived old id is one no verdict names. A record re-keys only when its id equals the hash of a reversed projection, so every re-keyed pair is a unit whose projection differs from its pre-rename one by the renames alone. The missed units show up in the store instead: a verdicts file stamped for the same corpus as the autosave was written against the pre-rename corpus, so each content id in it names a unit of that corpus and must be in the map. While any such id matches no unit, the tool refuses to write, dry run included, unless `--allow-unmatched` accepts them for a checkout whose units changed for another reason too.

The files it rewrites are the live autosave, every verdicts-*.json at the repository root stamped for the same corpus as the autosave (the stamp-aligned carried master and the fill files among them), every rebuild/evidence/verdicts-carried-*.json, and any file passed with `--verdicts`. Each record naming a pre-rename id moves to the post-rename id; a record already naming a unit on the corpus is left alone, and one naming neither is left alone and counted as unmatched, which blocks the write when the file is stamp-aligned and the id is a content id. Stamps, notes and `at` times are kept, so the carry that follows moves the verdicts onto the new corpus as it does any other store. Verdict records carry no duplicate-group or cluster id, and the `[carried u-…@file]` markers in notes record where a verdict came from, so they keep the ids they were written with. `--references` rewrites pre-rename unit ids in text files too, such as the unit ids rebuild/standing-approvals.yaml quotes in its notes.

Every file is copied to a new directory under var/keep/issue-357-rekey/ before it is rewritten, with the journal's length and whether the re-key appends to the journal, and `--undo DIR` puts those copies back and, when it appended, truncates the journal to that length. The rewritten autosave is appended to verdicts-journal.ndjson as a base event (`journal.record_transition` with no previous stamp), because journal lines before it name pre-rename ids. The old-to-new id map of every unit whose id changed is written to var/keep/issue-357-rekey/unit-id-map.json, which `merge_verdicts --restore-as-of TIME --rekey-map` applies to a store it replays from before the re-key. A second run finds every verdict already current and writes nothing. It prints `rekey counts: seen=N rekeyed=N current=N unmatched=N` over all the files.

Usage:
  uv run python -m rebuild.tools.rekey_verdicts --dry-run
  uv run python -m rebuild.tools.rekey_verdicts [--references rebuild/standing-approvals.yaml] [--allow-unmatched] [--yes]
  uv run python -m rebuild.tools.rekey_verdicts --undo var/keep/issue-357-rekey/<time>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.review import journal, unit_index  # noqa: E402
from rebuild.review.unit_cache import CARRY_PRESENTATION_KEYS, is_content_id, unit_id_for  # noqa: E402
from rebuild.tools.review_server import server_listening  # noqa: E402

MAP_FORMAT = "ams-review-unit-id-map/1"
KEEP = Path("var") / "keep" / "issue-357-rekey"
MAP_NAME = "unit-id-map.json"
BACKUP_RECORD = "backup.json"
JOURNAL_TAIL_BYTES = 1 << 20

CLASS_RENAMES = {
    "boundary-echo": "boundary-window",
    "jai-entry-contraction-respelled": "jai-entry-contraction-renamed",
    "kern-channel-out-of-scope": "kern-out-of-scope",
    "marker-staging-ligature-formation": "post-marker-ligature-formation",
    "may-ligature-seam-loosened": "may-ligature-junction-loosened",
    "regrouping-floor-drift": "regrouped-chain",
    "same-seam-extension-non-summing": "same-junction-extension-non-summing",
    "seam-loss-withdrawal": "junction-loss-unjoined",
    "see-out-fusion-respelled": "see-out-fusion-renamed",
    "zoo-entry-contraction-respelled": "zoo-entry-contraction-renamed",
    "zwnj-word-initial-seam-moved": "zwnj-word-initial-junction-moved",
}
KIND_RENAMES = {"seam": "junction"}
STANCE_RENAMES = {"hapax": "sole"}
SIDE_FIELD_RENAMES = {"seams": "junctions"}
SUMMARY_RENAMES = (
    ("keeps the same seams but", "keeps the same junctions but"),
    ("every letter cell and seam is unchanged", "every letter cell and junction is unchanged"),
    ("the structural floor", "the final tiebreak"),
    ("(hapax, ", "(sole, "),
    ("stances.hapax.", "stances.sole."),
)


class CrossCheckError(RuntimeError):
    """Raised when the forward renames do not map a unit's reversed projection back onto its own, or when two units reverse to one id."""


def _class_pattern(names: Iterable[str]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9-])({alternation})(?![a-z0-9-])")


class _Direction:
    """One direction of the renames: pre-rename to post-rename (`FORWARD`) or back (`REVERSE`)."""

    def __init__(self, *, reverse: bool) -> None:
        def table(pairs: Mapping[str, str]) -> dict[str, str]:
            return {new: old for old, new in pairs.items()} if reverse else dict(pairs)

        self.classes = table(CLASS_RENAMES)
        self.kinds = table(KIND_RENAMES)
        self.stances = table(STANCE_RENAMES)
        self.side_fields = table(SIDE_FIELD_RENAMES)
        self.summary = tuple((new, old) if reverse else (old, new) for old, new in SUMMARY_RENAMES)
        self.class_tokens = _class_pattern(self.classes)

    def cell(self, cell: str) -> str:
        parts = cell.split("/")
        if len(parts) > 1 and parts[1] in self.stances:
            parts[1] = self.stances[parts[1]]
            return "/".join(parts)
        return cell

    def text(self, value: str) -> str:
        for before, after in self.summary:
            value = value.replace(before, after)
        return value

    def note(self, value: str) -> str:
        return self.class_tokens.sub(lambda match: self.classes[match.group(1)], value)

    def side(self, side: Mapping[str, Any]) -> dict[str, Any]:
        out = {self.side_fields.get(key, key): value for key, value in side.items()}
        if isinstance(out.get("cells"), list):
            out["cells"] = [self.cell(cell) if isinstance(cell, str) else cell for cell in out["cells"]]
        return out

    def projection(self, projection: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(projection)
        if isinstance(out.get("class"), str):
            out["class"] = self.classes.get(out["class"], out["class"])
        if isinstance(out.get("config_classes"), dict):
            out["config_classes"] = {
                config: self.classes.get(value, value) if isinstance(value, str) else value
                for config, value in out["config_classes"].items()
            }
        if isinstance(out.get("config_class_note"), str):
            out["config_class_note"] = self.note(out["config_class_note"])
        kinds = out.get("kinds")
        if isinstance(kinds, list) and all(isinstance(kind, str) for kind in kinds):
            out["kinds"] = sorted(self.kinds.get(str(kind), str(kind)) for kind in kinds)
        for side in ("before", "after"):
            if isinstance(out.get(side), dict):
                out[side] = self.side(out[side])
        if isinstance(out.get("summary"), str):
            out["summary"] = self.text(out["summary"])
        return out


FORWARD = _Direction(reverse=False)
REVERSE = _Direction(reverse=True)


def carry_fields(fragment: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields of a fragment that `unit_cache.carry_projection` serializes."""
    return {key: value for key, value in fragment.items() if key not in CARRY_PRESENTATION_KEYS}


def pre_rename_projection(fragment: Mapping[str, Any]) -> dict[str, Any]:
    """Return the carry fields a post-rename fragment had before the renames. Raise `CrossCheckError` unless the forward renames map the result back onto the fragment's own carry fields, which checks that the tables invert on this unit. A rename the tables leave out passes this check; the stamp-aligned store catches it (`run`)."""
    current = carry_fields(fragment)
    prior = REVERSE.projection(current)
    if FORWARD.projection(prior) != current:
        raise CrossCheckError(
            f"{fragment.get('id')}: the forward renames do not map its reversed projection back onto its own"
        )
    return prior


def pre_rename_id(fragment: Mapping[str, Any]) -> str:
    """Return the id the fragment's unit had before the renames. It is the fragment's own id when no renamed value is in its projection."""
    prior = pre_rename_projection(fragment)
    if prior == carry_fields(fragment):
        return fragment["id"]
    return unit_id_for(hashlib.sha256(json.dumps(prior, sort_keys=True).encode()).hexdigest())


def build_id_map(fragments: Iterable[Mapping[str, Any]]) -> tuple[dict[str, str], set[str]]:
    """Return the pre-rename to post-rename id of every unit whose id the renames changed, and every post-rename id on the corpus. Two units with one pre-rename id would make the map ambiguous, so that raises `CrossCheckError` too."""
    renamed: dict[str, str] = {}
    current: set[str] = set()
    for fragment in fragments:
        unit = fragment["id"]
        current.add(unit)
        prior = pre_rename_id(fragment)
        if prior == unit:
            continue
        if prior in renamed:
            raise CrossCheckError(f"{prior}: both {renamed[prior]} and {unit} reverse to it")
        renamed[prior] = unit
    return renamed, current


def _stamp(payload: Mapping[str, Any] | None) -> str | None:
    stamp = payload.get("manifest_generated_at") if isinstance(payload, Mapping) else None
    return stamp if isinstance(stamp, str) else None


def _read(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
        return None
    return payload


def verdict_files(root: Path, autosave: Path, extra: Iterable[Path]) -> list[Path]:
    """Return the files to re-key: the autosave, the repository-root verdicts files stamped for the autosave's corpus, the checked-in carried masters, and `extra`, each once."""
    stamp = _stamp(_read(autosave))
    found = [autosave] if autosave.exists() else []
    for path in sorted(root.glob("verdicts-*.json")):
        if path.resolve() != autosave.resolve() and stamp is not None and _stamp(_read(path)) == stamp:
            found.append(path)
    found.extend(sorted((root / "rebuild" / "evidence").glob("verdicts-carried-*.json")))
    found.extend(extra)
    unique: dict[Path, Path] = {}
    for path in found:
        unique.setdefault(path.resolve(), path)
    return list(unique.values())


def rekey_payload(
    payload: dict, renamed: Mapping[str, str], current: set[str]
) -> tuple[dict, dict[str, int], list[str]]:
    """Return the payload with every pre-rename unit id replaced, records sorted by unit as the writers sort them, its counts, and the unmatched records' content ids."""
    counts = {"seen": 0, "rekeyed": 0, "current": 0, "unmatched": 0, "not_content_ids": 0}
    unmatched_content: list[str] = []
    records = []
    for record in payload["verdicts"]:
        counts["seen"] += 1
        unit = record.get("unit") if isinstance(record, dict) else None
        if isinstance(unit, str) and unit in renamed:
            record = {**record, "unit": renamed[unit]}
            counts["rekeyed"] += 1
        elif isinstance(unit, str) and unit in current:
            counts["current"] += 1
        else:
            counts["unmatched"] += 1
            if isinstance(unit, str) and is_content_id(unit):
                unmatched_content.append(unit)
            else:
                counts["not_content_ids"] += 1
        records.append(record)
    records.sort(key=lambda record: str(record.get("unit") or "") if isinstance(record, dict) else "")
    return {**payload, "verdicts": records}, counts, unmatched_content


def rekey_text(text: str, renamed: Mapping[str, str]) -> tuple[str, int]:
    count = 0

    def swap(match: re.Match[str]) -> str:
        nonlocal count
        new = renamed.get(match.group(0))
        if new is None:
            return match.group(0)
        count += 1
        return new

    return re.sub(r"\bu-[1-9A-HJ-NP-Za-km-z]{11}\b", swap, text), count


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _journal_tail(path: Path, size: int) -> str:
    """Return the sha256 of the journal's last `JOURNAL_TAIL_BYTES` bytes before `size`. A compaction rewrites the journal from a later base event, which moves those bytes, so `undo` can tell the recorded length no longer marks the re-key."""
    with path.open("rb") as handle:
        start = max(0, size - JOURNAL_TAIL_BYTES)
        handle.seek(start)
        return hashlib.sha256(handle.read(size - start)).hexdigest()


def _backup(root: Path, keep: Path, paths: list[Path], journal_path: Path, *, journaled: bool) -> Path:
    target = keep / "".join(c if c.isalnum() or c in ".-" else "." for c in journal.now_stamp())
    target.mkdir(parents=True, exist_ok=False)
    files = []
    for index, path in enumerate(paths):
        name = f"{index:02d}-{path.name}"
        shutil.copy2(path, target / name)
        files.append({"path": _rel(path, root), "backup": name})
    size = journal_path.stat().st_size if journal_path.exists() else None
    record = {
        "files": files,
        "journal": {
            "path": _rel(journal_path, root),
            "appended": journaled,
            "size": size,
            "tail_sha256": _journal_tail(journal_path, size) if size is not None else None,
        },
    }
    (target / BACKUP_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return target


def undo(root: Path, backup_dir: Path) -> int:
    """Put back every file a re-key rewrote from its copy in `backup_dir`. When the re-key appended its base event to the journal (it rewrote the live autosave), also truncate the journal to the length it had before the re-key, which drops every event appended after it, or remove the journal when the re-key created it. A journal compacted since the re-key is left alone, because the recorded length no longer marks the re-key in it. When the re-key did not append to the journal, the journal is left as it is, because every event after the recorded length belongs to a store the undo does not put back."""
    record = json.loads((backup_dir / BACKUP_RECORD).read_text(encoding="utf-8"))
    journal_path = root / record["journal"]["path"]
    size = record["journal"]["size"]
    appended = record["journal"]["appended"]
    if (
        appended
        and size is not None
        and (
            not journal_path.exists()
            or journal_path.stat().st_size < size
            or _journal_tail(journal_path, size) != record["journal"]["tail_sha256"]
        )
    ):
        print(
            f"ERROR: {record['journal']['path']} was compacted or replaced since the re-key, so its recorded length no "
            "longer marks the re-key. Nothing was restored; copy the files back by hand as backup.json lists them. The "
            "next corpus-stamp change writes a base event, from which the journal replays again."
        )
        return 1
    for entry in record["files"]:
        target = root / entry["path"]
        shutil.copy2(backup_dir / entry["backup"], target)
        print(f"restored {entry['path']}")
    if not appended:
        print(f"left {record['journal']['path']} as it is; the re-key did not append to it")
    elif size is None:
        journal_path.unlink(missing_ok=True)
        print(f"removed {record['journal']['path']}, which the re-key created")
    elif journal_path.exists() and journal_path.stat().st_size > size:
        with journal_path.open("r+b") as handle:
            handle.truncate(size)
        print(f"truncated {record['journal']['path']} to {size} bytes")
    return 0


def run(
    *,
    root: Path,
    corpus: Path,
    autosave: Path,
    journal_path: Path,
    keep: Path,
    extra: list[Path],
    references: list[Path],
    dry_run: bool,
    yes: bool,
    allow_unmatched: bool = False,
) -> int:
    try:
        corpus_stamp = json.loads((corpus / "manifest.json").read_text(encoding="utf-8")).get("generated_at")
    except OSError, ValueError:
        print(f"ERROR: {corpus} has no readable manifest.json; build the post-rename corpus first.")
        return 1
    try:
        renamed, current = build_id_map(unit_index.iter_shard_fragments(corpus))
    except CrossCheckError as error:
        print(f"ERROR: {error}. Was {_rel(corpus, root)} built after the renames? Nothing was written.")
        return 1
    print(
        f"corpus {_rel(corpus, root)} ({corpus_stamp}): {len(current)} units, {len(renamed)} ids changed by the renames"
    )

    totals = {"seen": 0, "rekeyed": 0, "current": 0, "unmatched": 0}
    payloads: list[tuple[Path, dict, dict]] = []
    aligned_stamp = _stamp(_read(autosave))
    stray: list[tuple[Path, list[str]]] = []
    for path in verdict_files(root, autosave, extra):
        payload = _read(path)
        if payload is None:
            print(f"ERROR: {_rel(path, root)} is not a readable verdicts file. Nothing was written.")
            return 1
        rekeyed, counts, unmatched_content = rekey_payload(payload, renamed, current)
        if unmatched_content and aligned_stamp is not None and _stamp(payload) == aligned_stamp:
            stray.append((path, unmatched_content))
        for key in totals:
            totals[key] += counts[key]
        legacy = f", {counts['not_content_ids']} of them not content ids" if counts["not_content_ids"] else ""
        print(
            f"{_rel(path, root)} (stamped {_stamp(payload)}): {counts['seen']} verdicts, {counts['rekeyed']} re-keyed, "
            f"{counts['current']} already current, {counts['unmatched']} unmatched{legacy}"
        )
        if counts["rekeyed"]:
            payloads.append((path, payload, rekeyed))
    texts: list[tuple[Path, str]] = []
    for path in references:
        rewritten, count = rekey_text(path.read_text(encoding="utf-8"), renamed)
        print(f"{_rel(path, root)}: {count} unit id references re-keyed")
        if count:
            texts.append((path, rewritten))
    print(
        f"rekey counts: seen={totals['seen']} rekeyed={totals['rekeyed']} current={totals['current']} unmatched={totals['unmatched']}"
    )

    targets = [path for path, _, _ in payloads] + [path for path, _ in texts]
    if stray and not allow_unmatched:
        for path, units in stray:
            print(
                f"ERROR: {_rel(path, root)} is stamped for the corpus the autosave was aligned with, yet {len(units)} of its "
                f"content ids match no unit before or after the renames: {', '.join(units[:10])}"
            )
        print(
            "Either a rename is missing from the tables in rebuild/tools/rekey_verdicts.py, so those units reverse to a "
            "projection they never had, or the units changed for another reason too. Nothing was written. Add the missing "
            "rename, or pass --allow-unmatched when the corpus changed beyond the renames and those verdicts are meant to "
            "be orphaned."
        )
        return 1
    if dry_run:
        print(f"dry run: nothing written; {len(targets)} file(s) would be rewritten")
        return 0
    rewrites_live_store = any(
        path.resolve() == (ROOT / "verdicts-autosave.json").resolve() for path in targets
    )
    if rewrites_live_store and server_listening() and not yes:
        print(
            "ERROR: the review server is listening on port 7294, and an open tab would write its own store back over "
            "the re-keyed autosave. Stop the server and close the review tabs, or pass --yes when the listening server "
            "serves another checkout. Nothing was written."
        )
        return 1
    map_path = keep / MAP_NAME
    map_payload = {"format": MAP_FORMAT, "corpus": corpus_stamp, "renamed": dict(sorted(renamed.items()))}
    if _read_map(map_path) != map_payload:
        keep.mkdir(parents=True, exist_ok=True)
        _write_json(map_path, map_payload)
        print(f"wrote {_rel(map_path, root)}: {len(renamed)} ids")
    if not targets:
        print("nothing to re-key: every verdict names a post-rename id or no unit on the corpus")
        return 0
    journaled = any(path.resolve() == autosave.resolve() for path, _, _ in payloads)
    backup_dir = _backup(root, keep, targets, journal_path, journaled=journaled)
    print(f"backed up {len(targets)} file(s) to {_rel(backup_dir, root)}")
    for path, before, after in payloads:
        _write_json(path, after)
        if path.resolve() == autosave.resolve():
            journal.record_transition(
                journal_path,
                source="rekey",
                stamp=after["manifest_generated_at"],
                old_stamp=None,
                old_verdicts=before["verdicts"],
                new_verdicts=after["verdicts"],
            )
        print(f"rewrote {_rel(path, root)}")
    for path, text in texts:
        _write_text(path, text)
        print(f"rewrote {_rel(path, root)}")
    return 0


def _read_map(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-key recorded verdicts from the unit ids of the corpus built before the #357 renames to the ids of the corpus built after them."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the checkout whose verdict files to re-key (default: %(default)s)",
    )
    parser.add_argument(
        "--corpus", type=Path, help="the post-rename corpus (default: ROOT/rebuild/out/review)"
    )
    parser.add_argument("--autosave", type=Path, help="the live store (default: ROOT/verdicts-autosave.json)")
    parser.add_argument(
        "--journal", type=Path, help="the verdict journal (default: ROOT/verdicts-journal.ndjson)"
    )
    parser.add_argument(
        "--keep", type=Path, help=f"where the id map and the backups go (default: ROOT/{KEEP})"
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="another verdicts file to re-key; repeatable",
    )
    parser.add_argument(
        "--references",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="a text file whose pre-rename unit ids to rewrite; repeatable",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change without writing anything"
    )
    parser.add_argument(
        "--allow-unmatched",
        action="store_true",
        help="write even when a stamp-aligned verdicts file names content ids that match no unit",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="rewrite the autosave even while a review server is listening on its port",
    )
    parser.add_argument(
        "--undo",
        type=Path,
        metavar="BACKUP_DIR",
        help="put back the files a re-key backed up to BACKUP_DIR, and truncate the journal",
    )
    args = parser.parse_args(argv)
    root = args.root
    if args.undo is not None:
        return undo(root, args.undo)
    return run(
        root=root,
        corpus=args.corpus or root / "rebuild" / "out" / "review",
        autosave=args.autosave or root / "verdicts-autosave.json",
        journal_path=args.journal or root / journal.JOURNAL_NAME,
        keep=args.keep or root / KEEP,
        extra=args.verdicts,
        references=args.references,
        dry_run=args.dry_run,
        yes=args.yes,
        allow_unmatched=args.allow_unmatched,
    )


if __name__ == "__main__":
    sys.exit(main())
