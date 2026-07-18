"""Readiness logic for the review surface: is the served surface present, does it still reflect the runes and code on disk, was it produced by a green artifact cycle, and is there a stamp-aligned verdict store to adjudicate against? Pure computation over the surface manifest, the persisted cycle summary, the autosave, and the repo-root verdicts files — the serve.py /status handler and the verdict_ready CLI both render this one dict, so the shape here is a contract other code encodes against."""

from __future__ import annotations

import json
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.review.serve import parse_autosave_payload

SURFACE_REMEDY = "uv run python -m rebuild.review.build"
REVIEW_BUILD_REMEDY = "make review-build"
CARRY_TOOL = "rebuild/tools/carry_verdicts.py"


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


def _iter_verdict_files(repo_root):
    """Carried masters land under rebuild/evidence/, so the sweep covers both that directory and the repo root, where exports and fill files live. The live autosave is excluded by name; callers that want it read it separately."""
    root = Path(repo_root)
    candidates = sorted(root.glob("verdicts-*.json")) + sorted(
        (root / "rebuild" / "evidence").glob("verdicts-*.json")
    )
    for path in candidates:
        if path.name == "verdicts-autosave.json":
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        data = parse_autosave_payload(raw)
        if data is None:
            continue
        yield path, data


def _effective_count(data) -> int | None:
    try:
        return count_effective(_latest_from_list(data["verdicts"]))
    except KeyError, TypeError:
        return None


def pick_frontier(repo_root, manifest_stamp) -> tuple[Path, int] | None:
    best: tuple[Path, int] | None = None
    for path, data in _iter_verdict_files(repo_root):
        if data["manifest_generated_at"] != manifest_stamp:
            continue
        count = _effective_count(data)
        if count is None:
            continue
        if best is None or count > best[1]:
            best = (path, count)
    return best


def resolve_carry_source(repo_root, manifest_stamp, autosave_path) -> dict | None:
    """Choose the verdicts file the artifact cycle carries forward when the caller didn't name one. Candidates are the live autosave plus every verdicts-*.json export at the repo root and under rebuild/evidence; the stamp-aligned candidate with the most effective verdicts wins (the autosave breaks ties, since it is the live store). When nothing aligns — the served surface was restamped outside a recorded cycle — the newest-stamped candidate stands in, which is safe because carry_verdicts re-resolves by content and ink keys rather than trusting the stamp. None means no candidate holds a single effective verdict."""
    entries: list[tuple[Path, str, int, bool]] = []
    autosave_path = Path(autosave_path)
    try:
        raw = autosave_path.read_bytes() if autosave_path.exists() else None
    except OSError:
        raw = None
    autosave = parse_autosave_payload(raw) if raw is not None else None
    if autosave is not None:
        count = _effective_count(autosave)
        if count:
            entries.append((autosave_path, autosave["manifest_generated_at"], count, True))
    for path, data in _iter_verdict_files(repo_root):
        count = _effective_count(data)
        if count:
            entries.append((path, data["manifest_generated_at"], count, False))
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
    ids: set[str] = set()
    for entry in manifest["classes"]:
        shard = entry.get("shard")
        if not shard:
            continue
        for unit in json.loads((review_dir / shard).read_text()):
            if unit.get("batch") is not None:
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
        return f"Import the carried verdicts ({carry_out}) in the app."
    return "Import the carried verdicts in the app."


def _carry_forward_remedy(carry_out) -> str:
    if carry_out:
        return f"Import the carried verdicts ({carry_out}) in the app."
    return f"Carry the autosave forward with {CARRY_TOOL}, then import it in the app."


def _surface_check(manifest) -> dict:
    if manifest is None:
        return {
            "level": "fail",
            "detail": "The review surface has no readable manifest.json.",
            "remedy": SURFACE_REMEDY,
        }
    return {"level": "ok", "detail": "The review surface manifest is present and readable.", "remedy": None}


def _freshness_check(manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy) -> dict:
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
    hard = [name for name in fingerprint.COMPONENTS if name != "static" and components[name] != "fresh"]
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
    if components["static"] != "fresh":
        return {
            "level": "warn",
            "detail": "Only the review UI assets changed since the surface was generated; the units are unchanged.",
            "remedy": REVIEW_BUILD_REMEDY,
            "components": components,
        }
    return {
        "level": "ok",
        "detail": "The surface reflects the current build inputs.",
        "remedy": None,
        "components": components,
    }


def _gates_check(summary, generated_at, manifest_fp, artifact_cycle_remedy) -> dict:
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
        if non_green:
            detail = f"The last artifact cycle {exit_val} with failing gates: {', '.join(non_green)}."
        else:
            detail = f"The last artifact cycle {exit_val}."
        return {"level": "fail", "detail": detail, "remedy": artifact_cycle_remedy}
    if not non_green:
        return {
            "level": "ok",
            "detail": f"The last artifact cycle finished green at {summary.get('finished_at')}.",
            "remedy": None,
        }
    skipped = [name for name in non_green if str((gates[name] or {}).get("status", "")).startswith("skipped")]
    if set(skipped) == set(non_green):
        return {
            "level": "warn",
            "detail": f"The last artifact cycle passed but skipped: {', '.join(skipped)}.",
            "remedy": None,
        }
    failing = [name for name in non_green if name not in set(skipped)]
    return {
        "level": "fail",
        "detail": f"The last artifact cycle has failing gates: {', '.join(failing)}.",
        "remedy": artifact_cycle_remedy,
    }


def _verdict_store_check(
    autosave_path, generated_at, carry_out, frontier_hit, frontier_rel
) -> tuple[dict, dict | None]:
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
            remedy = f"Import {frontier_rel} in the app; the stale autosave is empty and will be stashed automatically."
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
            "detail": "The autosave is aligned with this surface but empty; the frontier verdicts are not yet imported.",
            "remedy": f"Import {frontier_rel} in the app (carried verdicts first, then any echo fill).",
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
) -> dict:
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
        autosave_path, generated_at, carry_out, frontier_hit, frontier_rel
    )
    checks = {
        "surface": _surface_check(manifest),
        "freshness": _freshness_check(manifest, manifest_fp, repo_root, recompute, artifact_cycle_remedy),
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
