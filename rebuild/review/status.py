"""Readiness logic for the review surface: is the served surface present, does it still reflect the runes and code on disk — the after font it ships still the M1 font on disk, its per-unit index and both app sidecars still stamped for its manifest — was it produced by a green artifact cycle, and is there a stamp-aligned verdict store to adjudicate against? It reads the surface manifest and the files it answers for, the persisted cycle summary, the autosave, and the verdicts files at the repo root and under rebuild/evidence/. The one state it keeps between calls is a memo of what each of those verdicts files answered (its stamp, and its effective count once the file has been parsed), keyed on the file's stat, so a long-lived server answers an unchanged tree with a stat per file. The serve.py /status handler and the verdict_ready CLI both render this one dict, so the shape here is a contract other code encodes against."""

from __future__ import annotations

import codecs
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.review import app_index, unit_index
from rebuild.review.audit import slim_fragment
from rebuild.review.serve import parse_autosave_payload

SURFACE_REMEDY = "uv run python -m rebuild.review.build"
CARRY_TOOL = "rebuild/tools/carry_verdicts.py"
MERGE_TOOL = "uv run python -m rebuild.tools.merge_verdicts"


def _latest_from_list(verdicts) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for record in verdicts:
        unit = record["unit"]
        if unit not in best or record["at"] > best[unit]["at"]:
            best[unit] = record
    return best


def latest_verdicts(path) -> dict[str, dict]:
    return _latest_from_list(json.loads(Path(path).read_text())["verdicts"])


def count_effective(records) -> int:
    return sum(1 for record in records.values() if record.get("verdict") != "skip")


def _verdict_paths(repo_root) -> list[Path]:
    """Carried masters land under rebuild/evidence/, so the sweep covers both that directory and the repo root, where exports and fill files live. The live autosave is excluded by name; callers that want it read it separately."""
    root = Path(repo_root)
    candidates = sorted(root.glob("verdicts-*.json")) + sorted(
        (root / "rebuild" / "evidence").glob("verdicts-*.json")
    )
    return [path for path in candidates if path.name != "verdicts-autosave.json"]


def _effective_count(data) -> int | None:
    try:
        return count_effective(_latest_from_list(data["verdicts"]))
    except KeyError, TypeError:
        return None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """What the frontier and the carry source need from one verdicts file. A stamp of None means the file is not a verdicts document; on a parsed file, a count of None means a record in it could not be counted. An unparsed candidate is one whose head alone was read: its stamp is the head's reading, and its count is unknown rather than absent."""

    stamp: str | None
    count: int | None
    parsed: bool


def _summarize(raw: bytes) -> _Candidate:
    """The stamp and effective count of one verdicts file. Only these two scalars leave the frame, so the parsed store is released before the caller reads the next file."""
    data = parse_autosave_payload(raw)
    if data is None:
        return _Candidate(None, None, True)
    return _Candidate(data["manifest_generated_at"], _effective_count(data), True)


_HEAD_BYTES = 4096
_JSON_WHITESPACE = re.compile(r"[ \t\n\r]*")


def _skip_whitespace(text: str, index: int) -> int:
    match = _JSON_WHITESPACE.match(text, index)
    return match.end() if match else index


def _head_stamp(head: bytes) -> str | None:
    """The top-level manifest_generated_at of a verdicts file, read from the first bytes of it without parsing its records. The head settles a stamp only when it decodes as strict UTF-8 to a JSON object whose top-level members, walked one at a time with the decoder json.loads runs, reach the `verdicts` key after exactly one manifest_generated_at, and that one's value is a string. Any other head settles nothing — one that ends first, that is not an object, that starts with a BOM or is in another encoding, whose stamp is not a string, that names the stamp twice before `verdicts`, or whose object closes before `verdicts` — and the caller parses the whole file instead. The one input on which a settled stamp differs from json.loads's reading is a second top-level manifest_generated_at after the `verdicts` array, which json.loads lets win; such a file is out of contract, and pick_frontier's answer on it also depends on the calls before it: once resolve_carry_source has memoized the file's whole parse, pick_frontier answers from that parse's stamp, the later one. Every writer serializes one dict or one JS object (json.dumps, VerdictStore.payload_bytes, the app's JSON.stringify), and the only path that writes bytes it did not serialize is VerdictStore._receive_full, which writes a full-store POST verbatim once it parses, so only a POST built by some client other than the app could leave such a file on disk."""
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head)
        decoder = json.JSONDecoder()
        index = _skip_whitespace(text, 0)
        if index >= len(text) or text[index] != "{":
            return None
        index = _skip_whitespace(text, index + 1)
        stamp: str | None = None
        while index < len(text) and text[index] == '"':
            key, index = decoder.raw_decode(text, index)
            if not isinstance(key, str):
                return None
            index = _skip_whitespace(text, index)
            if index >= len(text) or text[index] != ":":
                return None
            index = _skip_whitespace(text, index + 1)
            if key == "verdicts":
                return stamp
            value, index = decoder.raw_decode(text, index)
            if key == "manifest_generated_at":
                if stamp is not None or not isinstance(value, str):
                    return None
                stamp = value
            index = _skip_whitespace(text, index)
            if index >= len(text) or text[index] != ",":
                return None
            index = _skip_whitespace(text, index + 1)
    except ValueError:
        return None
    return None


_SETTLE_NS = 2_000_000_000
_StatKey = tuple[int, int, int, int, int]
_MEMO: dict[Path, tuple[_StatKey, _Candidate]] = {}


def _stat_key(stat: os.stat_result) -> _StatKey:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _read_candidate(path: Path, manifest_stamp, *, counts: bool) -> tuple[_StatKey, bool, _Candidate] | None:
    """One verdicts file's stat key as the open handle saw it, whether its last change came at least _SETTLE_NS before the read began, and its candidate; or None when the file cannot be read. Counting parses every file whole. Otherwise a head that settles a stamp other than manifest_stamp answers alone, and every other file is parsed whole, so a matching head is validated by the parse and the parsed stamp, not the head's, is the one returned. The handle is unbuffered, so a whole-file read after the head returns one bytes object rather than the buffered head joined onto the rest; the head is the whole file only when the size fstat reported fits inside it."""
    try:
        with path.open("rb", buffering=0) as handle:
            stat = os.fstat(handle.fileno())
            now = time.time_ns()
            key = _stat_key(stat)
            settled = now - max(stat.st_mtime_ns, stat.st_ctime_ns) >= _SETTLE_NS
            if counts:
                return key, settled, _summarize(handle.read())
            head = handle.read(_HEAD_BYTES)
            stamp = _head_stamp(head)
            if stamp is not None and stamp != manifest_stamp:
                return key, settled, _Candidate(stamp, None, False)
            if stat.st_size <= len(head):
                return key, settled, _summarize(head)
            handle.seek(0)
            return key, settled, _summarize(handle.read())
    except OSError:
        return None


def _read_autosave(autosave_path: Path) -> _Candidate | None:
    try:
        raw = autosave_path.read_bytes() if autosave_path.exists() else None
    except OSError:
        raw = None
    return _summarize(raw) if raw is not None else None


def _survey(repo_root, manifest_stamp, *, counts: bool) -> list[tuple[Path, _Candidate]]:
    """Every verdicts file this call's glob finds, in glob order and spelled as the glob spells it, with what the frontier or the carry source needs from it; a path that cannot be stat'ed or read is left out. A file answers from _MEMO when its _stat_key still equals the entry's and the entry holds what this call needs: a parsed entry always, a head-only entry only when counts is false and its stamp is not manifest_stamp. Any other file is read, and the read is memoized only when the file's last change came at least _SETTLE_NS before the read began, so a same-size rewrite inside one timestamp tick is read again rather than answered from the old entry. The memo is then rebound to this walk's hits and settled reads alone, so a file the last walk did not find has no entry and the memo holds no more than one glob's files."""
    global _MEMO
    memo: dict[Path, tuple[_StatKey, _Candidate]] = {}
    surveyed: list[tuple[Path, _Candidate]] = []
    for path in _verdict_paths(repo_root):
        try:
            key = _stat_key(path.stat())
        except OSError:
            continue
        memoized = _MEMO.get(path)
        if memoized is not None and memoized[0] == key:
            candidate = memoized[1]
            if candidate.parsed or (
                not counts and candidate.stamp is not None and candidate.stamp != manifest_stamp
            ):
                memo[path] = memoized
                surveyed.append((path, candidate))
                continue
        read = _read_candidate(path, manifest_stamp, counts=counts)
        if read is None:
            continue
        read_key, settled, candidate = read
        if settled:
            memo[path] = (read_key, candidate)
        surveyed.append((path, candidate))
    _MEMO = memo
    return surveyed


def pick_frontier(repo_root, manifest_stamp) -> tuple[Path, int] | None:
    """The frontier: the verdicts file stamped for manifest_stamp with the most effective verdicts, the first in glob order on a tie, or None when no file qualifies; the live autosave is not a candidate. A file whose head settles a different stamp costs a bounded read and is never parsed. One whose head matches, or settles nothing, is parsed and validated whole before its stamp and count are trusted. A file unchanged since a settled read answers from the memo _survey keeps, for the cost of a stat."""
    best: tuple[Path, int] | None = None
    for path, candidate in _survey(repo_root, manifest_stamp, counts=False):
        if candidate.stamp is None or candidate.stamp != manifest_stamp:
            continue
        if candidate.count is None:
            continue
        if best is None or candidate.count > best[1]:
            best = (path, candidate.count)
    return best


def resolve_carry_source(repo_root, manifest_stamp, autosave_path) -> dict | None:
    """Choose the verdicts file the artifact cycle carries forward when the caller didn't name one. Candidates are the live autosave plus every verdicts-*.json export at the repo root and under rebuild/evidence; the stamp-aligned candidate with the most effective verdicts wins (the autosave breaks ties, since it is the live store). When nothing aligns — the served surface was restamped outside a recorded cycle, or a pass stopped between its surface build and its carry, so that nothing was ever stamped for the surface it built — the newest-stamped candidate is returned with aligned=False. The flag words the line that names the master and decides the cycle's route for it: an aligned master may be merged straight into the store when the surface build is skipped, and an unaligned one is always carried by unit id, since a verdict names its unit by content id and lands on the unit of that id on the live surface or on nothing. None means no candidate holds a single effective verdict. It counts every export whatever its stamp, from the memo it shares with pick_frontier, and parses whole any file pick_frontier read by its head alone; the autosave is read whole on every call and never memoized."""
    entries: list[tuple[Path, str, int, bool]] = []
    autosave_path = Path(autosave_path)
    autosave = _read_autosave(autosave_path)
    if autosave is not None and autosave.stamp is not None and autosave.count:
        entries.append((autosave_path, autosave.stamp, autosave.count, True))
    for path, candidate in _survey(repo_root, manifest_stamp, counts=True):
        if candidate.stamp is not None and candidate.count:
            entries.append((path, candidate.stamp, candidate.count, False))
    if not entries:
        return None
    pool = [entry for entry in entries if manifest_stamp is not None and entry[1] == manifest_stamp]
    aligned = bool(pool)
    if not pool:
        latest = max(stamp for _, stamp, _, _ in entries)
        pool = [entry for entry in entries if entry[1] == latest]
    path, stamp, count, _ = max(pool, key=lambda entry: (entry[2], entry[3]))
    return {"path": path, "stamp": stamp, "count": count, "aligned": aligned}


def load_human_unit_ids(review_dir) -> frozenset[str]:
    review_dir = Path(review_dir)
    manifest = json.loads((review_dir / "manifest.json").read_text())
    if "human_unit_ids" in manifest:
        persisted = manifest["human_unit_ids"]
        if not isinstance(persisted, list) or not all(
            isinstance(unit, str) and unit.startswith("u-") for unit in persisted
        ):
            raise TypeError("manifest human_unit_ids must be a list of u- ids")
        if len(persisted) != len(set(persisted)):
            raise ValueError("manifest human_unit_ids must be unique")
        return frozenset(persisted)
    ids: set[str] = set()
    for entry in manifest["classes"]:
        if not (entry.get("shards") or entry.get("shard")):
            continue
        for shard in unit_index.class_shards(entry):
            for unit in json.loads((review_dir / shard).read_text()):
                # A manifest without the index is one written before it existed, whose fragments carried the batch themselves; read that where it is, and the flags otherwise.
                human = unit["batch"] is not None if "batch" in unit else not slim_fragment(unit)
                if human:
                    ids.add(unit["id"])
    return frozenset(ids)


def _load_json_dict(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text())
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def _rel(repo_root, path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return str(path)


def _carry_import_remedy(carry_out) -> str:
    if carry_out:
        return f"Merge the carried verdicts into the autosave: {MERGE_TOOL} {carry_out}"
    return f"Merge the carried verdicts into the autosave with {MERGE_TOOL}."


def _carry_forward_remedy(carry_out) -> str:
    if carry_out:
        return f"Merge the carried verdicts into the autosave: {MERGE_TOOL} {carry_out}"
    return f"Carry the autosave forward with {CARRY_TOOL}, then merge it with {MERGE_TOOL}."


def _surface_check(manifest) -> dict:
    if manifest is None:
        return {
            "level": "fail",
            "detail": "The review surface has no readable manifest.json.",
            "remedy": SURFACE_REMEDY,
        }
    return {"level": "ok", "detail": "The review surface manifest is present and readable.", "remedy": None}


def _freshness_check(
    manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy, review_dir, m1_out
) -> dict:
    unknown = {name: "unknown" for name in fingerprint.COMPONENTS}
    if manifest is None:
        return {
            "level": "fail",
            "detail": "The surface manifest is missing, so its build inputs cannot be checked.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    if not isinstance(manifest_fp, dict):
        return {
            "level": "fail",
            "detail": "This surface predates input fingerprinting, so its freshness cannot be verified.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    try:
        current = recompute(repo_root)
    except Exception:
        return {
            "level": "fail",
            "detail": "The current build inputs could not be recomputed, so the surface freshness is unknown.",
            "remedy": artifact_cycle_remedy,
            "components": unknown,
        }
    components: dict[str, str] = {}
    for name in fingerprint.COMPONENTS:
        recorded = manifest_fp.get(name)
        if recorded is None:
            components[name] = "unknown"
        elif current.get(name) == recorded:
            components[name] = "fresh"
        else:
            components[name] = "stale"
    hard = [
        name
        for name in fingerprint.COMPONENTS
        if name not in unit_index.ASSET_COMPONENTS and components[name] != "fresh"
    ]
    if hard:
        stale = [name for name in hard if components[name] == "stale"]
        if stale:
            detail = f"The build inputs changed since the surface was generated: {', '.join(stale)}."
        else:
            detail = f"These components cannot be verified until the next M1 build records them: {', '.join(hard)}."
        return {
            "level": "fail",
            "detail": detail,
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    fonts = manifest.get("fonts")
    after_record = fonts.get("after") if isinstance(fonts, dict) else None
    recorded_sha = after_record.get("sha256") if isinstance(after_record, dict) else None
    after = Path(m1_out) / "M1.otf"
    if not isinstance(recorded_sha, str):
        return {
            "level": "fail",
            "detail": "The surface records no after-font hash, so it cannot be checked against rebuild/out/m1/M1.otf.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    try:
        on_disk = fingerprint.file_sha256(after)
    except OSError:
        on_disk = None
    if on_disk != recorded_sha:
        return {
            "level": "fail",
            "detail": "The surface's after font is not the M1 font on disk: rebuild/out/m1/M1.otf is missing or has moved on since this surface was built, so the letters being served are last build's.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    missing = [] if unit_index.index_is_current(review_dir) else [unit_index.INDEX_NAME]
    missing += [
        name for name, fmt in app_index.ARTIFACTS if not app_index.artifact_is_current(review_dir, name, fmt)
    ]
    if missing:
        return {
            "level": "fail",
            "detail": f"These files are missing or stamped for another manifest: {', '.join(missing)}. Either the build that wrote this surface did not finish, or the manifest was rewritten without them.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    if any(components[name] != "fresh" for name in unit_index.ASSET_COMPONENTS):
        return {
            "level": "warn",
            "detail": "Only the review UI assets changed since the surface was generated; the units are unchanged, and the cycle refreshes the served copy in place.",
            "remedy": artifact_cycle_remedy,
            "components": components,
        }
    return {
        "level": "ok",
        "detail": "The surface reflects the current build inputs.",
        "remedy": None,
        "components": components,
    }


def _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy) -> dict:
    """Keyed on each gate entry's `skip` field rather than its prose. A skip the driver marked "proved" rode a matching green record, which is proof that this exact content already passed, so it satisfies readiness; every other kind blocks a sitting, and the wording is what separates them. A "forced" skip and a gate that never ran read as unverified, while a real failure reads as one. Reading the status string instead is what once let `--skip-conform` report READY, since every skip kind spells itself "skipped (...)". Summaries written before the field existed carry no `skip` key at all, and fall back to the old prose rule so a cycle recorded under the previous shape keeps its former verdict instead of turning spuriously red."""
    if summary is None:
        return {
            "level": "fail",
            "detail": "There is no recorded artifact cycle for this surface.",
            "remedy": artifact_cycle_remedy,
        }
    surface = summary.get("surface") or {}
    if surface.get("generated_at") != generated_at or surface.get("inputs_fingerprint") != manifest_fp:
        return {
            "level": "fail",
            "detail": "The recorded cycle is for a different surface than the one being served.",
            "remedy": artifact_cycle_remedy,
        }
    gates = summary.get("gates") or {}
    exit_val = summary.get("exit")
    non_green = [name for name in sorted(gates) if not (gates[name] or {}).get("green")]
    if exit_val in ("interrupted", "failed"):
        failed = [name for name in non_green if not (gates[name] or {}).get("skip")]
        if failed:
            detail = f"The last artifact cycle {exit_val} with failing gates: {', '.join(failed)}."
        else:
            detail = f"The last artifact cycle {exit_val}."
        return {"level": "fail", "detail": detail, "remedy": artifact_cycle_remedy}
    if not non_green:
        return {
            "level": "ok",
            "detail": f"The last artifact cycle finished green at {summary.get('finished_at')}.",
            "remedy": None,
        }
    entries = {name: (gates[name] or {}) for name in non_green}
    if any("skip" not in entry for entry in entries.values()):
        skipped = [name for name in non_green if str(entries[name].get("status", "")).startswith("skipped")]
        if set(skipped) == set(non_green):
            return {
                "level": "warn",
                "detail": f"The last artifact cycle passed but skipped: {', '.join(skipped)}. It predates the skip-provenance record, so whether those skips were proved cannot be told from here.",
                "remedy": None,
            }
        failing = [name for name in non_green if name not in set(skipped)]
        return {
            "level": "fail",
            "detail": f"The last artifact cycle has failing gates: {', '.join(failing)}.",
            "remedy": artifact_cycle_remedy,
        }
    unproved = [name for name in non_green if entries[name].get("skip") != "proved"]
    if not unproved:
        return {
            "level": "ok",
            "detail": f"The last artifact cycle finished green at {summary.get('finished_at')}; every gate it skipped was already proved on this exact content.",
            "remedy": None,
        }
    unverified = [
        name
        for name in unproved
        if entries[name].get("skip")
        or str(entries[name].get("status", "")).startswith(("skipped", "not run"))
    ]
    failing = [name for name in unproved if name not in set(unverified)]
    if failing:
        return {
            "level": "fail",
            "detail": f"The last artifact cycle has failing gates: {', '.join(failing)}.",
            "remedy": artifact_cycle_remedy,
        }
    return {
        "level": "fail",
        "detail": f"The last artifact cycle left gates unverified: {', '.join(unverified)}.",
        "remedy": artifact_cycle_remedy,
    }


def _verdict_store_check(
    autosave_path, generated_at, carry_out, frontier_hit, frontier_rel, autosave=None
) -> tuple[dict, dict | None]:
    if autosave is None:
        path = Path(autosave_path)
        try:
            raw = path.read_bytes() if path.exists() else None
        except OSError:
            raw = None
        autosave = parse_autosave_payload(raw) if raw is not None else None
    if autosave is None:
        return {
            "level": "warn",
            "detail": "There is no autosave yet, so no in-progress verdicts are loaded.",
            "remedy": _carry_import_remedy(carry_out),
        }, None
    if autosave["manifest_generated_at"] != generated_at:
        try:
            stale_effective = count_effective(_latest_from_list(autosave["verdicts"]))
        except KeyError, TypeError:
            stale_effective = 0
        if stale_effective == 0 and frontier_hit and frontier_hit[1] > 0:
            remedy = f"Merge {frontier_rel} into the autosave ({MERGE_TOOL}); the stale autosave is empty and will be stashed automatically."
        else:
            remedy = _carry_forward_remedy(carry_out)
        return {
            "level": "fail",
            "detail": (
                f"The autosave is stamped for a different surface ({autosave['manifest_generated_at']}) "
                "than the one being served."
            ),
            "remedy": remedy,
        }, None
    try:
        records = _latest_from_list(autosave["verdicts"])
    except KeyError, TypeError:
        records = {}
    effective = count_effective(records)
    if effective == 0 and frontier_hit and frontier_hit[1] > 0:
        return {
            "level": "warn",
            "detail": "The autosave is aligned with this surface but empty; the frontier verdicts are not yet merged in.",
            "remedy": f"Merge {frontier_rel} into the autosave ({MERGE_TOOL}) — carried verdicts first, then any echo fill.",
        }, records
    return {
        "level": "ok",
        "detail": f"The autosave is aligned with this surface and holds {effective} effective verdicts.",
        "remedy": None,
    }, records


def _frontier_check(frontier_hit, frontier_rel) -> dict:
    if frontier_hit:
        count = frontier_hit[1]
        return {
            "level": "ok",
            "detail": f"The frontier verdicts file is {frontier_rel} ({count} effective verdicts).",
            "remedy": None,
            "path": frontier_rel,
            "count": count,
        }
    return {
        "level": "warn",
        "detail": "There is no stamp-matching verdicts file at the repo root or under rebuild/evidence.",
        "remedy": None,
        "path": None,
        "count": None,
    }


def _blanks_check(aligned_records, review_dir, human_ids) -> dict:
    if aligned_records is None:
        return {
            "level": "ok",
            "detail": "The blank count needs an autosave aligned with this surface.",
            "count": None,
        }
    if human_ids is None:
        try:
            human_ids = load_human_unit_ids(review_dir)
        except OSError, ValueError, KeyError, TypeError:
            return {
                "level": "ok",
                "detail": "The unit shards could not be read, so the blank count is unavailable.",
                "count": None,
            }
    effective_ids = {unit for unit, record in aligned_records.items() if record.get("verdict") != "skip"}
    remaining = len(set(human_ids) - effective_ids)
    return {"level": "ok", "detail": f"{remaining} blanks remaining.", "count": remaining}


def compute_status(
    repo_root,
    review_dir,
    m1_out,
    autosave_path,
    cycle_summary_path,
    *,
    human_ids=None,
    recompute=None,
    autosave=None,
) -> dict:
    """`autosave` is the parsed autosave document when the caller already holds it — the serve.py handler hands over its resident store rather than having the file read and parsed again per status request — and None to read `autosave_path`."""
    if recompute is None:
        recompute = fingerprint.compute_all
    repo_root = Path(repo_root)
    review_dir = Path(review_dir)

    manifest = _load_json_dict(review_dir / "manifest.json")
    generated_at = manifest.get("generated_at") if manifest else None
    repo_head = manifest.get("repo_head") if manifest else None
    manifest_fp = manifest.get("inputs_fingerprint") if manifest else None

    frontier_hit = pick_frontier(repo_root, generated_at)
    frontier_rel = _rel(repo_root, frontier_hit[0]) if frontier_hit else None
    artifact_cycle_remedy = "make artifact-cycle"

    summary = _load_json_dict(cycle_summary_path)
    carry_out = summary.get("carry_out") if summary else None

    verdict_store, aligned_records = _verdict_store_check(
        autosave_path, generated_at, carry_out, frontier_hit, frontier_rel, autosave
    )
    checks = {
        "surface": _surface_check(manifest),
        "freshness": _freshness_check(
            manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy, review_dir, m1_out
        ),
        "gates": _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy),
        "verdict_store": verdict_store,
        "frontier": _frontier_check(frontier_hit, frontier_rel),
        "blanks": _blanks_check(aligned_records, review_dir, human_ids),
    }
    ready = all(
        checks[name]["level"] != "fail" for name in ("surface", "freshness", "gates", "verdict_store")
    )
    return {
        "ready": ready,
        "surface": {"dir": str(review_dir), "generated_at": generated_at, "repo_head": repo_head},
        "checks": checks,
    }
