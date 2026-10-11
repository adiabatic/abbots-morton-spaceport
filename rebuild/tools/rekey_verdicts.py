"""Move recorded verdicts from the unit ids of one review corpus to the ids the same units have under the current carry projection. A unit's id is `unit_cache.unit_id_for` of the digest of its carry projection (`unit_cache.carry_content_hash`), which leaves out the fields in `unit_cache.CARRY_PRESENTATION_KEYS`, so a change to that key set changes every unit id at once, and each unit's id under the current key set is a function of its fragment on the old corpus alone. The tool turns that into a forward map, computed from the old corpus, and applies it once.

`--from-corpus OLD --map-out PATH` computes the map. It reads OLD's shard parts one at a time and walks each part one fragment at a time (`iter_fragments`), releasing the part before it reads the next, because a part reaches `build.SHARD_PART_BYTES` and parsing one whole takes several times that. Every fragment, machine-approved and verdict-exempt ones included, gives one entry under its old id (`map_entry`): its codepoints and configs and digests of its before side, after side and highlight, with `unit_id_for(carry_content_hash(fragment))` as its new id. Two fragments that share an old id or a new id stop it, because the map must be one-to-one. The map (`MAP_FORMAT`) holds `units` (old id → entry), `renamed` (old id → new id, for each id that changes), `from_corpora` (OLD's stamp), `corpora` (the corpora a run applied it onto) and `presentation_keys` (the key set it was computed under), one entry per line. It is never written over, and it depends on nothing but OLD, so it can be computed and kept before anything replaces OLD.

`--map PATH --corpus NEW` verifies the map and applies it. It refuses a map computed under another key set, one that pairs two old ids with one new id, and a NEW the map was computed from, whose ids are the old ones. It reads the verdict files and references below, then reads NEW once the same way, and lands every old id of the map they name. An id lands on its new id when NEW holds a unit with that id and the entry's codepoints, configs, before and after. When NEW lacks that id, as when a stale cached field in the projection changes it, the id lands on the one unit of NEW with the entry's codepoints and configs (a window's units cover disjoint config sets, so the pair is unique within a corpus), provided that unit's before, after and highlight digests equal the entry's and it is neither the old id itself (NEW was then built under the old key set), another entry's new id, nor already landed on. A verdicted or quoted id that lands nowhere blocks every write, dry run included, and so does a content id in a verdicts file stamped for a corpus the map was computed from that names no unit of the map or of NEW; `--allow-unmatched` accepts both when those verdicts are meant to be orphaned. Two ids of one file that land on one unit block the write with no override.

The files it rewrites are the live autosave, every verdicts-*.json at the repository root stamped for a corpus the map was computed from (the stamp-aligned carried master and the fill files among them), every rebuild/evidence/verdicts-carried-*.json, and any file passed with `--verdicts`, such as a stash or an orphan document still on old ids. Each record naming a landed old id moves to the unit it lands on; a record already naming a unit of NEW is left alone, and one naming neither is left alone and counted as unmatched. The store's tombstones (its `cleared` list) land the same way, and one whose unit holds a record after the re-key is dropped, as the verdict store drops it. Stamps, notes and `at` times are kept, so the carry that follows moves the verdicts onto NEW as it does any other store. Verdict records carry no duplicate-group or cluster id, and the `[carried u-…@file]` markers in notes record where a verdict came from, so they keep the ids they were written with. `--references` rewrites old unit ids in text files too, such as the unit ids rebuild/standing-approvals.yaml quotes in its notes.

Every file is copied to a new directory under var/keep/class-rejig-rekey/ before it is rewritten, with the journal's length and whether the re-key appends to the journal (a re-key that appends first ends the journal on a newline, `journal.repair_tail`, so the length marks where its base event begins), and `--undo DIR` puts those copies back and, when it appended, truncates the journal to that length and leaves the marker that makes the next land that moves the stamp journal a base (`journal.mark_unjournaled`), as it does when it refuses and asks for the copies to be put back by hand. The rewritten autosave is appended to verdicts-journal.ndjson as a base event (`journal.record_transition` with no previous stamp), because journal lines before it name old ids. The applied map, with each pair the codepoints-and-configs join landed in place of its computed one, is merged into var/keep/class-rejig-rekey/unit-id-map.json (`merge_id_maps`), which may be PATH itself: a run adds its entries to the ones already there instead of replacing them, and first copies the previous map beside it as unit-id-map-<time>.json. So the one map holds every pair any run applied, `--map` takes it again for a file still on old ids, and `merge_verdicts --restore-as-of TIME --rekey-map` applies it to a store it replays from before the re-key. Where two runs pair one old id with different units, the later run's unit wins and the tool names the clash; the earlier pair stays in the copy. A second run finds every verdict already current and writes nothing. It prints `rekey counts: seen=N rekeyed=N current=N unmatched=N collided=N` over all the files. From reading the verdict files through the journal append, and for all of `--undo`, it holds the verdict store's lock (`rebuild.review.store_lock`, through `landing.locked_store`, which first finishes a land whose holder died), which every writer of the store and the journal holds. It refuses to rewrite the autosave while a review server that may write it is listening (its /capabilities answer names that store, or names none), unless --yes is passed, because the re-keyed ids would replace the ones the open tabs hold; such a run, and a dry run, reads without the lock, so a run that ends up writing nothing never makes the server refuse a save.

Usage:
  uv run python -m rebuild.tools.rekey_verdicts --from-corpus rebuild/out/review --map-out var/keep/class-rejig-rekey/unit-id-map.json
  uv run python -m rebuild.tools.rekey_verdicts --map var/keep/class-rejig-rekey/unit-id-map.json --corpus NEW [--references rebuild/standing-approvals.yaml] --dry-run
  uv run python -m rebuild.tools.rekey_verdicts --map var/keep/class-rejig-rekey/unit-id-map.json --corpus NEW [--references FILE] [--verdicts FILE] [--allow-unmatched] [--yes]
  uv run python -m rebuild.tools.rekey_verdicts --undo var/keep/class-rejig-rekey/<time>
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rebuild.review import journal, unit_cache, unit_index  # noqa: E402
from rebuild.review.landing import locked_store  # noqa: E402
from rebuild.review.unit_cache import (  # noqa: E402
    CARRY_PRESENTATION_KEYS,
    base58_64,
    carry_content_hash,
    is_content_id,
    unit_id_for,
)
from rebuild.review.verdict_store import parse_cleared  # noqa: E402
from rebuild.tools.review_server import capabilities, server_listening  # noqa: E402

MAP_FORMAT = "ams-review-unit-id-map/2"
KEEP = Path("var") / "keep" / "class-rejig-rekey"
MAP_NAME = "unit-id-map.json"
BACKUP_RECORD = "backup.json"
JOURNAL_TAIL_BYTES = 1 << 20
UNIT_ID = re.compile(r"\bu-[1-9A-HJ-NP-Za-km-z]{11}\b")


class MapError(RuntimeError):
    """Raised when a corpus cannot give a one-to-one map, or a map cannot be applied."""


def iter_fragments(corpus: Path) -> Iterator[dict]:
    """Yield every fragment of the corpus's shard parts, in `unit_index.shard_paths` order. Each part is read as text and walked one fragment at a time (`unit_cache._walk_elements`), and its text is released before the next part is read, so one part's text and one fragment are in memory at a time."""
    for path in unit_index.shard_paths(corpus):
        text = path.read_text(encoding="utf-8")
        for _start, _length, fragment in unit_cache._walk_elements(text):
            yield fragment
        del text


def _digest(value: Any) -> str:
    return base58_64(hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())


def map_entry(fragment: Mapping[str, Any]) -> list[Any]:
    """Return what the map records of a fragment: its codepoints, its configs, and the digests of its before side, its after side and its highlight. A slim fragment (`audit.slim_fragment`) has no highlight, which digests as null."""
    return [
        fragment.get("codepoints"),
        fragment.get("configs"),
        _digest(fragment.get("before")),
        _digest(fragment.get("after")),
        _digest(fragment.get("highlight")),
    ]


def _join_key(codepoints: Any, configs: Any) -> tuple[Any, tuple[Any, ...]]:
    return codepoints, tuple(configs or ())


def _corpus_stamp(corpus: Path) -> str:
    try:
        stamp = json.loads((corpus / "manifest.json").read_text(encoding="utf-8")).get("generated_at")
    except OSError, ValueError, AttributeError:
        stamp = None
    if not isinstance(stamp, str):
        raise MapError(f"{corpus} has no readable manifest.json with a generated_at")
    return stamp


def _write_section(handle: TextIO, name: str, entries: Iterable[tuple[str, Any]]) -> None:
    handle.write(f"{json.dumps(name)}: {{")
    separator = "\n"
    for key, value in entries:
        handle.write(f"{separator}{json.dumps(key)}: {json.dumps(value)}")
        separator = ",\n"
    handle.write("\n}")


def _write_map(
    path: Path, header: Mapping[str, Any], units: Iterable[tuple[str, Any]], renamed: Mapping[str, str]
) -> None:
    """Write an id map with one entry per line: the header's keys, then `units`, then `renamed`. `units` may be a generator that fills `renamed` as it runs, since `renamed` is read only once `units` is exhausted. A failure removes the partial file."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write("{\n")
            for key, value in header.items():
                handle.write(f"{json.dumps(key)}: {json.dumps(value)},\n")
            _write_section(handle, "units", units)
            handle.write(",\n")
            _write_section(handle, "renamed", renamed.items())
            handle.write("\n}\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def compute_map(corpus: Path, out: Path) -> tuple[str, int, int]:
    """Write the forward map of `corpus` to `out` and return the corpus's stamp, its unit count and how many of its ids change. Raise `MapError`, leaving no file, when two fragments share an old id or a new id."""
    stamp = _corpus_stamp(corpus)
    renamed: dict[str, str] = {}
    owners: dict[str, str] = {}
    olds: set[str] = set()

    def units() -> Iterator[tuple[str, list[Any]]]:
        for fragment in iter_fragments(corpus):
            old = fragment["id"]
            new = unit_id_for(carry_content_hash(fragment))
            if old in olds:
                raise MapError(f"{old} appears twice on {corpus}")
            olds.add(old)
            if owners.setdefault(new, old) != old:
                raise MapError(f"{owners[new]} and {old} both map to {new}")
            if new != old:
                renamed[old] = new
            yield old, map_entry(fragment)

    header = {
        "format": MAP_FORMAT,
        "from_corpora": [stamp],
        "corpora": [],
        "presentation_keys": sorted(CARRY_PRESENTATION_KEYS),
    }
    _write_map(out, header, units(), renamed)
    return stamp, len(olds), len(renamed)


def write_forward_map(corpus: Path, out: Path, root: Path) -> int:
    if out.exists():
        print(
            f"ERROR: {_rel(out, root)} exists, and a map is never written over. Move it aside or pick another path."
        )
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        stamp, units, changed = compute_map(corpus, out)
    except MapError as error:
        print(f"ERROR: {error}. Nothing was written.")
        return 1
    print(
        f"corpus {_rel(corpus, root)} ({stamp}): {units} units, {changed} ids change under the current carry projection"
    )
    print(f"wrote {_rel(out, root)}")
    return 0


def load_map(path: Path) -> dict[str, Any]:
    """Return the forward map at `path`. Raise `MapError` unless it is an `MAP_FORMAT` map computed under the current `CARRY_PRESENTATION_KEYS`, since a map computed under another key set names new ids no corpus this checkout builds has."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise MapError(f"{path} is not a readable id map") from None
    if (
        not isinstance(payload, dict)
        or payload.get("format") != MAP_FORMAT
        or not isinstance(payload.get("units"), dict)
        or not isinstance(payload.get("renamed"), dict)
        or not isinstance(payload.get("from_corpora"), list)
    ):
        raise MapError(f"{path} is not an {MAP_FORMAT} map")
    if payload.get("presentation_keys") != sorted(CARRY_PRESENTATION_KEYS):
        raise MapError(
            f"{path} was computed under another carry projection than this checkout's "
            "unit_cache.CARRY_PRESENTATION_KEYS; compute it again from the old corpus with this checkout"
        )
    return payload


def check_one_to_one(id_map: Mapping[str, Any]) -> dict[str, str]:
    """Return each new id of the map with the old id that maps to it. Raise `MapError` when two old ids map to one new id."""
    renamed = id_map["renamed"]
    owners: dict[str, str] = {}
    for old in id_map["units"]:
        new = renamed.get(old, old)
        if owners.setdefault(new, old) != old:
            raise MapError(f"{owners[new]} and {old} both map to {new}")
    return owners


@dataclass(frozen=True)
class Landing:
    """Where the asked ids land on a corpus: `landed` pairs each landed old id of the map with its unit, `joined` holds the ones the codepoints-and-configs join landed, `unlanded` gives each old id of the map that lands nowhere with the reason, and `present` holds the asked ids that name a unit of the corpus."""

    units: int
    landed: dict[str, str]
    joined: frozenset[str]
    unlanded: dict[str, str]
    present: frozenset[str]


def land(id_map: Mapping[str, Any], owners: Mapping[str, str], corpus: Path, asked: set[str]) -> Landing:
    """Land every id of `asked` that the map holds on a unit of `corpus`, reading the corpus once (`iter_fragments`): on its new id when the corpus holds a unit with that id whose codepoints, configs, before and after equal the entry's, and otherwise, when the corpus lacks that id, on the one unit with the entry's codepoints and configs whose before, after and highlight digests equal the entry's, unless that unit is the old id itself, another entry's new id (`owners`), or one another id landed on first."""
    units, renamed = id_map["units"], id_map["renamed"]
    wanted = {old: renamed.get(old, old) for old in asked if old in units}
    keys = {_join_key(*units[old][:2]) for old in wanted}
    targets = set(wanted.values())
    found: set[str] = set()
    by_key: dict[tuple[Any, tuple[Any, ...]], list[tuple[str, list[Any]]]] = {}
    present: set[str] = set()
    count = 0
    for fragment in iter_fragments(corpus):
        count += 1
        unit = fragment["id"]
        if unit in asked:
            present.add(unit)
        if unit in targets:
            found.add(unit)
        key = _join_key(fragment.get("codepoints"), fragment.get("configs"))
        if key in keys:
            by_key.setdefault(key, []).append((unit, map_entry(fragment)))

    landed: dict[str, str] = {}
    holders: dict[str, str] = {}
    unlanded: dict[str, str] = {}
    pending: list[str] = []
    for old, target in wanted.items():
        if target not in found:
            pending.append(old)
            continue
        entry = units[old]
        if any(
            unit == target and other[:4] == entry[:4] for unit, other in by_key.get(_join_key(*entry[:2]), ())
        ):
            landed[old] = target
            holders[target] = old
        else:
            unlanded[old] = f"{target} on the corpus has other codepoints, configs or drawings"
    joined: set[str] = set()
    for old in pending:
        entry = units[old]
        candidates = by_key.get(_join_key(*entry[:2]), [])
        if not candidates:
            unlanded[old] = (
                f"its new id {wanted[old]} is not on the corpus, and no unit there has its codepoints and configs"
            )
            continue
        if len(candidates) > 1:
            unlanded[old] = f"{len(candidates)} units on the corpus share its codepoints and configs"
            continue
        ((unit, other),) = candidates
        if unit == old:
            unlanded[old] = (
                "it is still on the corpus under its old id, so the corpus was built under another carry projection"
            )
        elif other[2:] != entry[2:]:
            unlanded[old] = f"{unit} has its codepoints and configs but draws differently"
        elif owners.get(unit, old) != old:
            unlanded[old] = f"{unit} has its codepoints and configs but is the new id of {owners[unit]}"
        elif unit in holders:
            unlanded[old] = f"{unit} has its codepoints and configs but {holders[unit]} landed on it"
        else:
            landed[old] = unit
            holders[unit] = old
            joined.add(old)
    return Landing(count, landed, frozenset(joined), unlanded, frozenset(present))


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


def verdict_files(root: Path, stamps: set[str], autosave: Path, extra: Iterable[Path]) -> list[Path]:
    """Return the files to re-key: the autosave, the repository-root verdicts files stamped for a corpus in `stamps`, the checked-in carried masters, and `extra`, each once."""
    found = [autosave] if autosave.exists() else []
    for path in sorted(root.glob("verdicts-*.json")):
        if path.resolve() != autosave.resolve() and _stamp(_read(path)) in stamps:
            found.append(path)
    found.extend(sorted((root / "rebuild" / "evidence").glob("verdicts-carried-*.json")))
    found.extend(extra)
    unique: dict[Path, Path] = {}
    for path in found:
        unique.setdefault(path.resolve(), path)
    return list(unique.values())


def rekey_payload(
    payload: dict, renamed: Mapping[str, str], current: Container[str]
) -> tuple[dict, dict[str, int], list[str]]:
    """Return the payload with every unit id in `renamed` replaced, records sorted by unit as the writers sort them, its counts, and the unmatched records' content ids. `collided` counts the records that land on a unit a record naming another id lands on too. The store's tombstones (its `cleared` list) move through the same pairs; two that land on one id keep the later clear, and one whose unit holds a record after the re-key is dropped, as the verdict store drops it."""
    counts = {"seen": 0, "rekeyed": 0, "current": 0, "unmatched": 0, "collided": 0, "not_content_ids": 0}
    unmatched_content: list[str] = []
    records = []
    sources: dict[str, str] = {}
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
        if isinstance(unit, str) and sources.setdefault(record["unit"], unit) != unit:
            counts["collided"] += 1
        records.append(record)
    records.sort(key=lambda record: str(record.get("unit") or "") if isinstance(record, dict) else "")
    result = {**payload, "verdicts": records}
    if "cleared" in payload:
        held = {record.get("unit") for record in records if isinstance(record, dict)}
        cleared: dict[str, str] = {}
        for unit, at in parse_cleared(payload).items():
            unit = renamed.get(unit, unit)
            if unit not in held and at > cleared.get(unit, ""):
                cleared[unit] = at
        result["cleared"] = [{"unit": unit, "at": at} for unit, at in sorted(cleared.items())]
        if not cleared:
            del result["cleared"]
    return result, counts, unmatched_content


def rekey_text(text: str, renamed: Mapping[str, str]) -> tuple[str, int]:
    count = 0

    def swap(match: re.Match[str]) -> str:
        nonlocal count
        new = renamed.get(match.group(0))
        if new is None:
            return match.group(0)
        count += 1
        return new

    return UNIT_ID.sub(swap, text), count


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


def _time_name() -> str:
    return "".join(c if c.isalnum() or c in ".-" else "." for c in journal.now_stamp())


def _stamps(payload: Mapping[str, Any], key: str) -> list[Any]:
    listed = payload.get(key)
    return list(listed) if isinstance(listed, list) else []


def merge_id_maps(
    previous: Mapping[str, Any] | None, applied: Mapping[str, Any], corpus: str | None
) -> tuple[dict[str, Any], list[str]]:
    """Return the id map holding every entry and pair of `previous` and of `applied`, the map a run verified and applied onto the corpus stamped `corpus`, and the old ids the two pair with different units. A pair of `applied` wins such a clash, because it names a unit of the corpus the next carry lands on. `from_corpora` lists every corpus the entries were computed from, and `corpora` every corpus a run applied the map onto."""
    earlier = previous.get("renamed") if isinstance(previous, Mapping) else None
    renamed: dict[str, str] = dict(earlier) if isinstance(earlier, Mapping) else {}
    clashes = sorted(old for old, new in applied["renamed"].items() if renamed.get(old, new) != new)
    renamed.update(applied["renamed"])
    earlier_units = previous.get("units") if isinstance(previous, Mapping) else None
    units: dict[str, Any] = dict(earlier_units) if isinstance(earlier_units, Mapping) else {}
    units.update(applied["units"])
    from_corpora = _stamps(previous, "from_corpora") if isinstance(previous, Mapping) else []
    from_corpora += [stamp for stamp in applied["from_corpora"] if stamp not in from_corpora]
    corpora = _stamps(previous, "corpora") if isinstance(previous, Mapping) else []
    if corpus not in corpora:
        corpora.append(corpus)
    payload = {
        "format": MAP_FORMAT,
        "from_corpora": from_corpora,
        "corpora": corpora,
        "presentation_keys": applied["presentation_keys"],
        "units": units,
        "renamed": renamed,
    }
    return payload, clashes


def _read_previous_map(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _save_previous_map(map_path: Path, keep: Path) -> Path:
    stem = f"{map_path.stem}-{_time_name()}"
    target, count = keep / f"{stem}.json", 1
    while target.exists():
        count += 1
        target = keep / f"{stem}-{count}.json"
    shutil.copy2(map_path, target)
    return target


def _backup(root: Path, keep: Path, paths: list[Path], journal_path: Path, *, journaled: bool) -> Path:
    target = keep / _time_name()
    target.mkdir(parents=True, exist_ok=False)
    files = []
    for index, path in enumerate(paths):
        name = f"{index:02d}-{path.name}"
        shutil.copy2(path, target / name)
        files.append({"path": _rel(path, root), "backup": name})
    if journaled:
        journal.repair_tail(journal_path)
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
    """Put back every file a re-key rewrote from its copy in `backup_dir`. When the re-key appended its base event to the journal (it rewrote the live autosave), also truncate the journal to the length it had before the re-key, which drops every event appended after it, or remove the journal when the re-key created it. A journal compacted since the re-key is left alone, because the recorded length no longer marks the re-key in it, and nothing is put back; the person copies the files back by hand. When the re-key did not append to the journal, the journal is left as it is, because every event after the recorded length belongs to a store the undo does not put back. When the re-key rewrote the live autosave, the undo first leaves the marker that says the journal may lack a store write (`journal.mark_unjournaled`), whether it puts the store back or asks for it to be copied back by hand, so the next land that moves the stamp journals its store as a base (`journal.base_due`)."""
    with locked_store(root / "verdicts-autosave.json"):
        return _undo_locked(root, backup_dir)


def _undo_locked(root: Path, backup_dir: Path) -> int:
    record = json.loads((backup_dir / BACKUP_RECORD).read_text(encoding="utf-8"))
    journal_path = root / record["journal"]["path"]
    size = record["journal"]["size"]
    appended = record["journal"]["appended"]
    autosave = root / "verdicts-autosave.json"
    if appended:
        journal.mark_unjournaled(autosave, "rekey_verdicts --undo")
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
            "journal does not record a copy made by hand, so this undo left "
            f"{_rel(journal.unjournaled_marker_for(autosave), root)}, which makes the next land that moves the stamp "
            "journal its store as a base event: copy the files back before that land. Until the journal's next base "
            "event, merge_verdicts --restore-as-of gives a wrong store for every moment from the copy on."
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
    if appended:
        print(
            f"left {_rel(journal.unjournaled_marker_for(autosave), root)}, so the next land that moves the stamp "
            "journals the store as a base event"
        )
    return 0


def run(
    *,
    root: Path,
    map_path: Path,
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
        corpus_stamp = _corpus_stamp(corpus)
        id_map = load_map(map_path)
        if corpus_stamp in id_map["from_corpora"]:
            raise MapError(
                f"{_rel(corpus, root)} is stamped {corpus_stamp}, a corpus the map was computed from, so its units still "
                "have the old ids; pass --corpus the corpus built under the current carry projection"
            )
        owners = check_one_to_one(id_map)
    except MapError as error:
        print(f"ERROR: {error}. Nothing was written.")
        return 1
    print(
        f"map {_rel(map_path, root)}: {len(id_map['units'])} units of the corpus stamped "
        f"{', '.join(map(str, id_map['from_corpora']))}, {len(id_map['renamed'])} ids change"
    )

    guarded = not dry_run and not yes and server_listening() and _served(autosave)
    rekey = functools.partial(
        _rekey_files,
        root=root,
        id_map=id_map,
        owners=owners,
        map_path=map_path,
        corpus=corpus,
        corpus_stamp=corpus_stamp,
        autosave=autosave,
        journal_path=journal_path,
        keep=keep,
        extra=extra,
        references=references,
        dry_run=dry_run,
        allow_unmatched=allow_unmatched,
    )
    if dry_run or guarded:
        return rekey(refuse_live_store=guarded, write_lock=lambda: locked_store(autosave))
    with locked_store(autosave):
        return rekey(refuse_live_store=False, write_lock=contextlib.nullcontext)


def _served(autosave: Path) -> bool:
    """Return whether the review server listening on its port may write `autosave`: its /capabilities answer names that store, or names none, as a server that predates /capabilities or does not answer does."""
    answer = capabilities()
    served = answer.get("autosave") if isinstance(answer, dict) else None
    return not isinstance(served, str) or Path(served).resolve() == autosave.resolve()


def _rekey_files(
    *,
    root: Path,
    id_map: dict[str, Any],
    owners: Mapping[str, str],
    map_path: Path,
    corpus: Path,
    corpus_stamp: str,
    autosave: Path,
    journal_path: Path,
    keep: Path,
    extra: list[Path],
    references: list[Path],
    dry_run: bool,
    allow_unmatched: bool,
    refuse_live_store: bool,
    write_lock: Callable[[], contextlib.AbstractContextManager],
) -> int:
    """Land the ids the verdict files and references name, and re-key them. `run` calls it under the store's lock, from the reads through the writes and the journal append, unless the run is a dry run or a review server is listening without --yes. It then reads without the lock, `refuse_live_store` stops it before it rewrites the live autosave, and `write_lock` takes the lock for the writes that include the store and its journal."""
    from_stamps = {stamp for stamp in id_map["from_corpora"] if isinstance(stamp, str)}
    read: list[tuple[Path, dict]] = []
    for path in verdict_files(root, from_stamps, autosave, extra):
        payload = _read(path)
        if payload is None:
            print(f"ERROR: {_rel(path, root)} is not a readable verdicts file. Nothing was written.")
            return 1
        read.append((path, payload))
    quoted = [(path, path.read_text(encoding="utf-8")) for path in references]
    verdicted = {
        record["unit"]
        for _, payload in read
        for record in payload["verdicts"]
        if isinstance(record, dict) and isinstance(record.get("unit"), str)
    }
    verdicted.update(match for _, text in quoted for match in UNIT_ID.findall(text))
    cleared = {unit for _, payload in read for unit in parse_cleared(payload)}
    landing = land(id_map, owners, corpus, verdicted | cleared)
    asked = sum(1 for unit in verdicted | cleared if unit in id_map["units"])
    print(
        f"corpus {_rel(corpus, root)} ({corpus_stamp}): {landing.units} units; of the {asked} ids of the map the files "
        f"name, {len(landing.landed)} land, {len(landing.joined)} of them by codepoints and configs, and "
        f"{len(landing.unlanded)} land nowhere"
    )

    moves = {old: new for old, new in landing.landed.items() if new != old}
    totals = {"seen": 0, "rekeyed": 0, "current": 0, "unmatched": 0, "collided": 0}
    payloads: list[tuple[Path, dict, dict]] = []
    stray: list[tuple[Path, list[str]]] = []
    collided: list[Path] = []
    for path, payload in read:
        rekeyed, counts, unmatched_content = rekey_payload(payload, moves, landing.present)
        strays = [unit for unit in unmatched_content if unit not in id_map["units"]]
        if strays and _stamp(payload) in from_stamps:
            stray.append((path, strays))
        if counts["collided"]:
            collided.append(path)
        for key in totals:
            totals[key] += counts[key]
        legacy = f", {counts['not_content_ids']} of them not content ids" if counts["not_content_ids"] else ""
        print(
            f"{_rel(path, root)} (stamped {_stamp(payload)}): {counts['seen']} verdicts, {counts['rekeyed']} re-keyed, "
            f"{counts['current']} already current, {counts['unmatched']} unmatched{legacy}, {counts['collided']} collided"
        )
        if counts["rekeyed"]:
            payloads.append((path, payload, rekeyed))
    texts: list[tuple[Path, str]] = []
    for path, text in quoted:
        rewritten, count = rekey_text(text, moves)
        print(f"{_rel(path, root)}: {count} unit id references re-keyed")
        if count:
            texts.append((path, rewritten))
    print(
        f"rekey counts: seen={totals['seen']} rekeyed={totals['rekeyed']} current={totals['current']} "
        f"unmatched={totals['unmatched']} collided={totals['collided']}"
    )

    unlanded = sorted(unit for unit in verdicted if unit in landing.unlanded)
    if (unlanded or stray) and not allow_unmatched:
        if unlanded:
            print(
                f"ERROR: {len(unlanded)} verdicted or quoted id(s) of the map land on no unit of {_rel(corpus, root)}:"
            )
            for unit in unlanded[:10]:
                print(f"  {unit}: {landing.unlanded[unit]}")
        for path, units in stray:
            print(
                f"ERROR: {_rel(path, root)} is stamped for the corpus the map was computed from, yet {len(units)} of its "
                f"content ids are neither in the map nor on {_rel(corpus, root)}: {', '.join(units[:10])}"
            )
        print(
            "Nothing was written. A unit whose content changed beyond the carry projection's presentation fields lands "
            "nowhere, and a verdict the map does not hold was made on another corpus. Pass --allow-unmatched only when "
            "those verdicts are meant to be orphaned."
        )
        return 1
    if collided:
        print(
            f"ERROR: in {', '.join(_rel(path, root) for path in collided)}, records naming different ids land on one "
            "unit, so the file holds verdicts on one unit under both its old and its new id. Nothing was written; "
            "settle which record stands and run again."
        )
        return 1
    targets = [path for path, _, _ in payloads] + [path for path, _ in texts]
    if dry_run:
        print(f"dry run: nothing written; {len(targets)} file(s) would be rewritten")
        return 0
    rewrites_live_store = any(path.resolve() == autosave.resolve() for path in targets)
    if rewrites_live_store and refuse_live_store:
        print(
            f"ERROR: the review server listening on port 7294 may write {_rel(autosave, root)}, and an open tab would write "
            "its own store back over the re-keyed autosave. Stop the server and close the review tabs, or pass --yes when "
            "the listening server serves another checkout. Nothing was written."
        )
        return 1
    _merge_map(root, keep, id_map, map_path, landing, corpus_stamp)
    if not targets:
        print("nothing to re-key: every verdict names a unit of the corpus or no unit the map holds")
        return 0
    journaled = any(path.resolve() == autosave.resolve() for path, _, _ in payloads)
    with write_lock() if journaled else contextlib.nullcontext():
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


def _merge_map(
    root: Path, keep: Path, id_map: dict[str, Any], map_path: Path, landing: Landing, corpus_stamp: str
) -> None:
    """Merge the applied map, each pair the join landed in place of its computed one, into the kept map, copying the previous kept map beside it first. The kept map may be the applied map's own file."""
    kept = keep / MAP_NAME
    same_file = kept.exists() and kept.resolve() == map_path.resolve()
    previous = id_map if same_file else _read_previous_map(kept)
    joined = {old: landing.landed[old] for old in landing.joined}
    applied = {**id_map, "renamed": {**id_map["renamed"], **joined}}
    merged, clashes = merge_id_maps(previous, applied, corpus_stamp)
    if previous == merged:
        return
    keep.mkdir(parents=True, exist_ok=True)
    saved = _save_previous_map(kept, keep) if kept.exists() else None
    if saved is not None:
        print(f"copied the previous id map to {_rel(saved, root)}")
    header = {key: value for key, value in merged.items() if key not in ("units", "renamed")}
    _write_map(kept, header, merged["units"].items(), merged["renamed"])
    print(
        f"wrote {_rel(kept, root)}: {len(merged['units'])} units, {len(merged['renamed'])} pairs, "
        f"{len(joined)} landed by codepoints and configs"
    )
    if clashes and saved is not None:
        print(
            f"{len(clashes)} old id(s) pair with a different unit than in the previous map, whose copy "
            f"{_rel(saved, root)} keeps the earlier pair: {', '.join(clashes[:10])}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Move recorded verdicts from the unit ids of a review corpus to the ids its units have under the current carry projection: compute the id map from the old corpus, then verify it against the new corpus and apply it."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the checkout whose verdict files to re-key (default: %(default)s)",
    )
    parser.add_argument(
        "--from-corpus",
        type=Path,
        metavar="OLD",
        help="compute the id map from the corpus at OLD (with --map-out)",
    )
    parser.add_argument(
        "--map-out",
        type=Path,
        metavar="PATH",
        help="where --from-corpus writes the id map; it must not exist",
    )
    parser.add_argument(
        "--map",
        type=Path,
        metavar="PATH",
        help="verify the id map at PATH against --corpus and re-key the verdict files through it",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        metavar="NEW",
        help="the corpus built under the current carry projection that --map lands the ids on; required with --map",
    )
    parser.add_argument("--autosave", type=Path, help="the live store (default: ROOT/verdicts-autosave.json)")
    parser.add_argument(
        "--journal", type=Path, help="the verdict journal (default: ROOT/verdicts-journal.ndjson)"
    )
    parser.add_argument(
        "--keep", type=Path, help=f"where the merged id map and the backups go (default: ROOT/{KEEP})"
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
        help="a text file whose old unit ids to rewrite; repeatable",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change without writing anything"
    )
    parser.add_argument(
        "--allow-unmatched",
        action="store_true",
        help="write even when a verdicted id of the map lands on no unit, or a verdicts file stamped for the map's corpus names a content id that is in neither",
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
    if (args.from_corpus is None) != (args.map_out is None):
        parser.error("--from-corpus and --map-out go together")
    if args.from_corpus is not None:
        if args.map is not None:
            parser.error("--from-corpus computes a map and --map applies one; run them separately")
        return write_forward_map(args.from_corpus, args.map_out, root)
    if args.map is None:
        parser.error("pass --from-corpus OLD --map-out PATH, --map PATH --corpus NEW, or --undo BACKUP_DIR")
    if args.corpus is None:
        parser.error("--map needs --corpus NEW, the corpus built under the current carry projection")
    return run(
        root=root,
        map_path=args.map,
        corpus=args.corpus,
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
