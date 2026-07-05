"""The review-app generation CLI (rebuild/REVIEW-PLAN.md §1.3): assemble units, precompute enrichment and all three verdict drafts, and write the self-contained rebuild/out/review/ directory — manifest.json, one unit shard per class, copied fonts, and the static app files. Also the `snapshot` subcommand for accepted-state baselines.

Usage:
    uv run python -m rebuild.review.build
    uv run python -m rebuild.review.build --mode table-diff --baseline <dir> --new <dir> --before-font <otf> --after-font <otf>
    uv run python -m rebuild.review.build snapshot --tables rebuild/out/m1 --font rebuild/out/m1/M1.otf --to rebuild/out/review-baseline
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import multiprocessing
import shutil
import subprocess
import sys
import time
import traceback
import warnings
from dataclasses import dataclass
from pathlib import Path

from rebuild.review import families, tablediff
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    BATCH_SIZE,
    UNMATCHED_CLASS,
    _config_index,
    assign_batches,
    load_workload,
    merge_ink_duplicate_units,
    synthesize_family_classes,
)
from rebuild.review.drafts import Drafter
from rebuild.review.families import assign_family
from rebuild.review.ink import VERIFICATION_METHOD, InkComparator
from rebuild.review.enrich import (
    LETTERS,
    EnrichedUnit,
    Enricher,
    SeamHomeUnit,
    load_spec,
    notation,
    notation_tokens,
    resolve_home_assignments,
    resolve_secondary_homes,
    seam_home_projection,
    text_entities,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "rebuild" / "out" / "review"
STATIC_DIR = Path(__file__).resolve().parent / "static"

MANIFEST_FORMAT = "ams-review-manifest/1"
BUILD_COMMAND = "uv run python -m rebuild.review.build"
SERVE_COMMAND = "uv run python -m rebuild.review.serve"

M1_AUDIT = REPO_ROOT / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
M1_LEDGER = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
M1_SUBSETS = REPO_ROOT / "rebuild" / "out" / "m1"
M1_AFTER_FONT = REPO_ROOT / "rebuild" / "out" / "m1" / "M1.otf"
SITE_BEFORE_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf"

_FALLBACK_INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AMS review surface (placeholder)</title>
</head>
<body>
<main>
<h1>AMS review surface</h1>
<p>This is the generator's placeholder page: the static app sources were not present under <code>rebuild/review/static/</code> when this directory was built. The data payload is complete — <a href="manifest.json">manifest.json</a> plus one shard per class under <code>units/</code>, and both fonts under <code>fonts/</code>.</p>
<p>Rebuild with <code>{build}</code>; serve with <code>{serve}</code>.</p>
</main>
</body>
</html>
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _upem(path: Path) -> int:
    from fontTools.ttLib import TTFont

    return TTFont(str(path))["head"].unitsPerEm


def _repo_head(repo_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


UNIT_ASSEMBLY_EPOCH = "2026-07-04T07:00:00Z"


def _generated_at(*inputs: Path) -> str:
    """Deterministic across consecutive builds of the same inputs (the §6 byte-identity gate), and different whenever an input changes: the latest input mtime as UTC ISO, floored at UNIT_ASSEMBLY_EPOCH. Bump the epoch whenever a build-code change re-keys or renumbers units with no input change (the ink-duplicate merge did this on 2026-07-04) — unit ids must never be joined across manifests, and without the floor a code-only change would leave the stamp unchanged, letting the app silently restore a stale autosave or import an old export by id onto the wrong units."""
    latest = max(path.stat().st_mtime for path in inputs if path.exists())
    stamp = (
        datetime.datetime.fromtimestamp(latest, tz=datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return max(stamp, UNIT_ASSEMBLY_EPOCH)


FEATURE_DESCRIPTIONS = {
    "ss02": "allow ·I·Tea to join at the Short height",
    "ss03": "join ·Tea at the x-height as a full-size ·Tea (full when a baseline-join follows)",
    "ss04": "allow ·It to join at baseline after ·Day and before ·Low",
    "ss05": "allow ·Et·Tea·… double baseline joins again (older, manual-style behavior)",
    "ss06": "use gapped ·Owe (doesn’t connect at the top)",
    "ss07": "allow ·Owe·Day to join at the x-height again",
    "ss10": "suppress all joins for the wrapped letter(s)",
}


def _config_features(config: str) -> frozenset[str]:
    return frozenset() if config == "default" else frozenset(config.split("+"))


def config_note(unit_configs, full_configs) -> str | None:
    """The optional per-unit badge text describing when a divergence applies, computed from the unit's config set against the manifest's full acceptance-config list. Null when the unit covers every non-ss10 config (the overwhelmingly common case, where the set carries no information); otherwise a feature-gating phrase when the set is exactly the configs with some feature tag on ("only when ss03 is on", or "only under ss10" for the isolation overlay) or exactly the non-ss10 configs with it off ("only when ss03 is off"); otherwise the literal fallback "only under: <set>"."""
    covered = set(unit_configs)
    non_isolated = [config for config in full_configs if "ss10" not in _config_features(config)]
    if covered >= set(non_isolated):
        return None
    tags = sorted({tag for config in full_configs for tag in _config_features(config)})
    for tag in tags:
        if covered == {config for config in full_configs if tag in _config_features(config)}:
            return "only under ss10" if tag == "ss10" else f"only when {tag} is on"
    for tag in tags:
        if covered == {config for config in non_isolated if tag not in _config_features(config)}:
            return f"only when {tag} is off"
    return "only under: " + ", ".join(unit_configs)


def _config_class_note(unit) -> str | None:
    """For a per-config-split unit (UNMATCHED under some configs, already blessed under others — the ss03-chain-join-gains windows), a short strip describing both facts, e.g. "blessed as ss03-chain-join-gains under ss03, ss02+ss03; novel under default, ss02". None when the unit's class is the same across every config (every matched unit and every fully-novel unit)."""
    config_classes = unit.config_classes
    if not config_classes:
        return None
    novel = [config for config, cls in config_classes.items() if cls == UNMATCHED_CLASS]
    blessed = [config for config, cls in config_classes.items() if cls != UNMATCHED_CLASS]
    if not novel or not blessed:
        return None
    by_class: dict[str, list[str]] = {}
    for config in sorted(blessed, key=_config_index):
        by_class.setdefault(config_classes[config], []).append(config)
    blessed_phrase = "; ".join(
        f"blessed as {cls} under {', '.join(configs)}" for cls, configs in by_class.items()
    )
    novel_phrase = "novel under " + ", ".join(sorted(novel, key=_config_index))
    return f"{blessed_phrase}; {novel_phrase}"


def _machine_approved_meta(machine_units) -> dict:
    """The manifest's machine_approved record: the ink-identical total, the audit rows those units cover, the verification method one-liner, and the per-class unit counts (classes with zero machine-approved units are omitted)."""
    by_class: dict[str, int] = {}
    rows = 0
    for unit in machine_units:
        by_class[unit.class_id] = by_class.get(unit.class_id, 0) + 1
        rows += len(unit.rows)
    return {
        "units": len(machine_units),
        "rows": rows,
        "method": VERIFICATION_METHOD,
        "by_class": by_class,
    }


def unit_to_json(enriched: EnrichedUnit, drafter: Drafter, full_configs=ACCEPTANCE_CONFIGS) -> dict:
    unit = enriched.unit
    pin = drafter.draft_pin(enriched)
    policy = drafter.draft_policy(enriched)
    any_of = drafter.draft_any_of(enriched)
    return {
        "id": unit.unit_id,
        "batch": unit.batch,
        "ink_identical": unit.ink_identical,
        "no_verdict": unit.no_verdict,
        "echo": unit.echo,
        "class": unit.class_id,
        "group": unit.group,
        "codepoints": unit.codepoints,
        "text_entities": enriched.text_entities,
        "notation": enriched.notation,
        "notation_tokens": list(enriched.notation_tokens),
        "configs": list(unit.configs),
        "config_note": config_note(unit.configs, full_configs),
        "config_classes": dict(unit.config_classes) or None,
        "config_class_note": _config_class_note(unit),
        "render_groups": [{"configs": list(group)} for group in unit.render_groups],
        "kinds": list(unit.kinds),
        "exemplar": unit.exemplar,
        "before": {"glyphs": list(enriched.before_glyphs), "seams": list(enriched.before_seams)},
        "after": {
            "cells": list(enriched.after_cells),
            "seams": list(enriched.after_seams),
            "extensions": list(enriched.after_extensions),
        },
        "diff_positions": list(enriched.diff_positions),
        "pair": {"left": enriched.pair[0], "right": enriched.pair[1]} if enriched.pair else None,
        "pair_codepoints": list(enriched.pair_codepoints) if enriched.pair_codepoints else None,
        "highlight": {"before": enriched.highlight_before, "after": enriched.highlight_after},
        "boundary_marks": list(enriched.boundary_marks),
        "secondary_seams": [
            {
                "pair": {"left": seam.pair[0], "right": seam.pair[1]},
                "before": seam.highlight_before,
                "after": seam.highlight_after,
                "home": seam.home,
            }
            for seam in enriched.secondary_seams
            if not seam.suppressed
        ]
        or None,
        "summary": enriched.summary,
        "explain": enriched.explain_text,
        "provenance": list(enriched.provenance),
        "drafts": {
            "pin": pin.to_json(),
            "policy": policy.to_json() if policy else None,
            "any_of": any_of.to_json(),
        },
    }


def _copy_font(source: Path, out_dir: Path, name: str, family: str, repo_root: Path) -> dict:
    target = out_dir / "fonts" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    try:
        rel = str(source.resolve().relative_to(repo_root))
    except ValueError:
        rel = str(source)
    return {
        "file": f"fonts/{name}",
        "family": family,
        "source": rel,
        "sha256": _sha256(target),
        "upem": _upem(target),
    }


def copy_static(out_dir: Path, static_dir: Path = STATIC_DIR) -> list[str]:
    copied: list[str] = []
    if static_dir.is_dir():
        for source in sorted(static_dir.rglob("*")):
            if not source.is_file():
                continue
            rel = source.relative_to(static_dir)
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(str(rel))
    if "index.html" not in copied:
        (out_dir / "index.html").write_text(
            _FALLBACK_INDEX.format(build=BUILD_COMMAND, serve=SERVE_COMMAND), encoding="utf-8"
        )
        copied.append("index.html")
    return copied


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")


def _prune_orphan_shards(out_dir: Path, manifest: dict) -> list[str]:
    """Delete units/*.json left over from ledger classes the manifest no longer references. Runs only after the manifest is written, so a mid-build crash leaves today's behavior (orphans survive) rather than a manifest pointing at a deleted shard. Touches only *.json directly under units/ — subdirectories, non-JSON files, fonts, static assets, and manifest.json are never considered."""
    units_dir = Path(out_dir) / "units"
    if not units_dir.is_dir():
        return []
    keep = {Path(meta["shard"]).name for meta in manifest["classes"]}
    removed: list[str] = []
    for shard in units_dir.glob("*.json"):
        if shard.is_file() and shard.name not in keep:
            shard.unlink()
            removed.append(shard.name)
    return sorted(removed)


@dataclass(frozen=True)
class _UnitProjection:
    """The slim, picklable phase-1 result a surface worker returns per unit: everything the parent's serial reduces read, and never the EnrichedUnit (its ~61 KB ExplainReport stays alive worker-side for phase 2)."""

    unit_id: str
    ink_identical: bool
    config_diffs: tuple
    family: str
    pair_codepoints: tuple[int, int] | None
    seam_home: SeamHomeUnit


def _surface_worker(conn, init: dict) -> None:
    """A persistent, stateful surface worker (spawn-only: uharfbuzz/fontTools C objects are not fork-safe, and drafts._import_test_shaping mutates a module-global singleton). Phase 1 computes config_diff + enrich over its slice and retains each EnrichedUnit in-process; phase 2 injects the parent's global fields and emits the shard JSON from the retained ExplainReports."""
    try:
        comparator = InkComparator(init["before_font"], init["after_font"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spec = load_spec(init["repo_root"])
        enricher = Enricher(
            spec,
            init["subset_dir"],
            init["after_font"],
            repo_root=init["repo_root"],
            before_font=init["before_font"],
        )
        drafter = Drafter(init["after_font"], repo_root=init["repo_root"])
        retained: dict[str, EnrichedUnit] = {}
        while True:
            message = conn.recv()
            if message[0] == "stop":
                return
            if message[0] == "phase1":
                results: list[_UnitProjection] = []
                for unit in message[1]:
                    text = "".join(chr(value) for value in unit.codepoint_values)
                    diffs = tuple(comparator.config_diff(text, config) for config in unit.configs)
                    unit.ink_identical = all(diff == ((), ()) for diff in diffs)
                    enriched = enricher.enrich(unit)
                    retained[unit.unit_id] = enriched
                    family = assign_family(enriched) if unit.class_id == UNMATCHED_CLASS else ""
                    results.append(
                        _UnitProjection(
                            unit_id=unit.unit_id,
                            ink_identical=unit.ink_identical,
                            config_diffs=diffs,
                            family=family,
                            pair_codepoints=enriched.pair_codepoints,
                            seam_home=seam_home_projection(enriched),
                        )
                    )
                conn.send(("ok", results, list(enricher.mismatches)))
            elif message[0] == "phase2":
                fragments: dict[str, dict] = {}
                for unit_id, (batch, echo, class_id, seam_assign) in message[1].items():
                    enriched = retained[unit_id]
                    enriched.unit.batch = batch
                    enriched.unit.echo = echo
                    enriched.unit.class_id = class_id
                    for seam, (home, suppressed) in zip(enriched.secondary_seams, seam_assign):
                        seam.home = home
                        seam.suppressed = suppressed
                    fragments[unit_id] = unit_to_json(enriched, drafter)
                conn.send(("ok", fragments))
    except Exception:
        try:
            conn.send(("error", traceback.format_exc()))
        except Exception:
            pass
    finally:
        conn.close()


def _partition(items: list, parts: int) -> list[list]:
    """Contiguous, near-even slices of `items` in order — the first `len % parts` slices carry one extra so ids stay in triage order across the whole partition."""
    size, extra = divmod(len(items), parts)
    slices: list[list] = []
    start = 0
    for index in range(parts):
        length = size + (1 if index < extra else 0)
        slices.append(items[start : start + length])
        start += length
    return slices


def _run_parallel(
    workload,
    jobs: int,
    batch_size: int,
    subset_dir: Path,
    before_font: Path,
    after_font: Path,
    repo_root: Path,
):
    """The two-phase fan-out of the three per-unit passes across persistent spawn workers, returning exactly what `_write_surface` needs. The parent keeps the frozen ids/triage order and every order-sensitive reduce (batches, family promotion, echo numbering, secondary-home resolution) serial; the workers hold the EnrichedUnits and emit the shard JSON."""
    units = workload.units
    nworkers = max(1, min(jobs, len(units)))
    slices = _partition(units, nworkers)
    init = {
        "before_font": before_font,
        "after_font": after_font,
        "subset_dir": subset_dir,
        "repo_root": repo_root,
    }
    ctx = multiprocessing.get_context("spawn")
    procs = []
    conns = []
    for _ in range(nworkers):
        parent_conn, child_conn = ctx.Pipe()
        proc = ctx.Process(target=_surface_worker, args=(child_conn, init))
        proc.start()
        child_conn.close()
        procs.append(proc)
        conns.append(parent_conn)

    try:
        for conn, chunk in zip(conns, slices):
            conn.send(("phase1", chunk))
        result_by_id: dict[str, _UnitProjection] = {}
        projections: list[SeamHomeUnit] = []
        mismatches: list[str] = []
        for conn in conns:
            reply = conn.recv()
            if reply[0] == "error":
                raise RuntimeError("surface worker failed in phase 1:\n" + reply[1])
            for projection in reply[1]:
                result_by_id[projection.unit_id] = projection
                projections.append(projection.seam_home)
            mismatches.extend(reply[2])

        for unit in units:
            unit.ink_identical = result_by_id[unit.unit_id].ink_identical
        total_batches = assign_batches(units, batch_size)
        for unit in units:
            if unit.class_id == UNMATCHED_CLASS:
                unit.family_id = result_by_id[unit.unit_id].family
                unit.class_id = unit.family_id
        echo_ids: dict[tuple, str] = {}
        for unit in units:
            if unit.batch is None:
                continue
            pair_codepoints = result_by_id[unit.unit_id].pair_codepoints
            pair = None
            if pair_codepoints:
                values = unit.codepoint_values
                pair = (values[pair_codepoints[0]], values[pair_codepoints[1]])
            key = (unit.configs, pair, unit.class_id, result_by_id[unit.unit_id].config_diffs)
            unit.echo = echo_ids.setdefault(key, f"e-{len(echo_ids):04d}")

        classes = workload.classes_present + synthesize_family_classes(
            units, families.FAMILY_ORDER, families.FAMILY_WHY
        )
        by_class = workload.units_by_class()
        assignments, seam_census = resolve_home_assignments(projections)

        for conn, chunk in zip(conns, slices):
            payload = {
                unit.unit_id: (unit.batch, unit.echo, unit.class_id, assignments[unit.unit_id])
                for unit in chunk
            }
            conn.send(("phase2", payload))
        fragments: dict[str, dict] = {}
        for conn in conns:
            reply = conn.recv()
            if reply[0] == "error":
                raise RuntimeError("surface worker failed in phase 2:\n" + reply[1])
            fragments.update(reply[1])

        for conn in conns:
            conn.send(("stop",))
        return fragments, seam_census, len(echo_ids), total_batches, classes, by_class, mismatches
    finally:
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass
        for proc in procs:
            proc.join(timeout=5)
            if proc.is_alive():
                proc.terminate()


def _write_surface(
    out_dir: Path,
    workload,
    classes: list,
    by_class: dict,
    fragments: dict,
    seam_census: dict,
    echo_count: int,
    total_batches: int,
    batch_size: int,
    audit_path: Path,
    ledger_path: Path,
    before_font: Path,
    after_font: Path,
    repo_root: Path,
    static_dir: Path,
    mismatches: list,
) -> dict:
    """Reassemble the per-unit JSON fragments into shards (per class, triage order), copy fonts, and write the manifest with its parent-once `generated_at`/`repo_head` stamps. Shared by the serial and parallel paths so both produce byte-identical output."""
    classes_meta: list[dict] = []
    for entry in classes:
        units = by_class[entry.id]
        shard = [fragments[unit.unit_id] for unit in units]
        _write_json(out_dir / "units" / f"{entry.id}.json", shard)
        classes_meta.append(
            {
                "id": entry.id,
                "status": entry.status,
                "ink_identical": entry.ink_identical,
                "no_verdict": entry.no_verdict,
                "why": entry.why,
                "unit_count": len(units),
                "row_count": sum(len(unit.rows) for unit in units),
                "machine_approved_count": sum(1 for unit in units if unit.ink_identical),
                "shard": f"units/{entry.id}.json",
                "batches": sorted({unit.batch for unit in units if unit.batch is not None}),
            }
        )

    fonts = {
        "before": _copy_font(before_font, out_dir, "before.otf", "AMS Review Before", repo_root),
        "after": _copy_font(after_font, out_dir, "after.otf", "AMS Review After", repo_root),
    }
    machine_units = [unit for unit in workload.units if unit.ink_identical]
    manifest = {
        "format": MANIFEST_FORMAT,
        "mode": "m1-audit",
        "generated_at": _generated_at(audit_path, ledger_path, before_font, after_font),
        "repo_head": _repo_head(repo_root),
        "source": {
            "audit": _relative(audit_path, repo_root),
            "ledger": _relative(ledger_path, repo_root),
        },
        "fonts": fonts,
        "configs": list(ACCEPTANCE_CONFIGS),
        "feature_descriptions": dict(FEATURE_DESCRIPTIONS),
        "batch_size": batch_size,
        "totals": {
            "units": len(workload.units),
            "rows": workload.row_count,
            "batches": total_batches,
            "echo_groups": echo_count,
        },
        "machine_approved": _machine_approved_meta(machine_units),
        "secondary_seams": seam_census,
        "classes": classes_meta,
        "build_command": BUILD_COMMAND,
        "serve_command": SERVE_COMMAND,
    }
    _write_json(out_dir / "manifest.json", manifest)
    pruned = _prune_orphan_shards(out_dir, manifest)
    if pruned:
        print(f"Pruned {len(pruned)} orphan shard(s): {', '.join(pruned)}", file=sys.stderr)
    copy_static(out_dir, static_dir)
    if mismatches:
        print(
            f"warning: {len(mismatches)} units where re-settled cells diverge from the audit "
            f"(first: {mismatches[0]})",
            file=sys.stderr,
        )
    errors = check_output_dir(out_dir)
    if errors:
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:20]))
    return manifest


def build_m1(
    out_dir: Path = DEFAULT_OUT,
    audit_path: Path = M1_AUDIT,
    ledger_path: Path = M1_LEDGER,
    subset_dir: Path = M1_SUBSETS,
    before_font: Path = SITE_BEFORE_FONT,
    after_font: Path = M1_AFTER_FONT,
    repo_root: Path = REPO_ROOT,
    batch_size: int = BATCH_SIZE,
    static_dir: Path = STATIC_DIR,
    jobs: int = 1,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    phase = time.perf_counter()
    workload = load_workload(audit_path, ledger_path, dict(LETTERS))
    comparator = InkComparator(before_font, after_font)
    exempt_classes = {entry.id for entry in workload.ledger if entry.no_verdict}
    merge_ink_duplicate_units(workload.units, comparator.signature, exempt_classes)
    present = {unit.class_id for unit in workload.units}
    workload.classes_present = [entry for entry in workload.ledger if entry.id in present]
    print(f"[t] review.build load {time.perf_counter() - phase:.1f}s", file=sys.stderr, flush=True)

    if jobs > 1:
        phase = time.perf_counter()
        fragments, seam_census, echo_count, total_batches, classes, by_class, mismatches = _run_parallel(
            workload, jobs, batch_size, subset_dir, before_font, after_font, repo_root
        )
        print(
            f"[t] review.build parallel(jobs={jobs}) {time.perf_counter() - phase:.1f}s",
            file=sys.stderr,
            flush=True,
        )
    else:
        config_diffs: dict[str, tuple] = {}
        for unit in workload.units:
            text = "".join(chr(value) for value in unit.codepoint_values)
            diffs = tuple(comparator.config_diff(text, config) for config in unit.configs)
            unit.ink_identical = all(diff == ((), ()) for diff in diffs)
            config_diffs[unit.unit_id] = diffs
        total_batches = assign_batches(workload.units, batch_size)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spec = load_spec(repo_root)
        enricher = Enricher(spec, subset_dir, after_font, repo_root=repo_root, before_font=before_font)
        drafter = Drafter(after_font, repo_root=repo_root)

        # Enrich every unit once. UNMATCHED units were never enriched before (they had no ledger class and so never reached the old per-class loop); this single flat pass both produces their shard fields and yields the before/after seams the verdict-family grouper reads.
        phase = time.perf_counter()
        enriched_by_id = {unit.unit_id: enricher.enrich(unit) for unit in workload.units}
        print(f"[t] review.build enrich_loop {time.perf_counter() - phase:.1f}s", file=sys.stderr, flush=True)

        # Promote each UNMATCHED unit's verdict family to its class so the existing per-class loop shards it under that family.
        phase = time.perf_counter()
        for unit in workload.units:
            if unit.class_id == UNMATCHED_CLASS:
                unit.family_id = assign_family(enriched_by_id[unit.unit_id])
                unit.class_id = unit.family_id

        # Echo groups: human units whose judged pair, class, config set, and per-config ink deltas all agree show the same change in different surroundings, so one verdict answers all of them. Keyed after family promotion so the class component is final; ids are assigned in triage order.
        echo_ids: dict[tuple, str] = {}
        for unit in workload.units:
            if unit.batch is None:
                continue
            enriched = enriched_by_id[unit.unit_id]
            pair = None
            if enriched.pair_codepoints:
                values = unit.codepoint_values
                pair = (values[enriched.pair_codepoints[0]], values[enriched.pair_codepoints[1]])
            key = (unit.configs, pair, unit.class_id, config_diffs[unit.unit_id])
            unit.echo = echo_ids.setdefault(key, f"e-{len(echo_ids):04d}")

        classes = workload.classes_present + synthesize_family_classes(
            workload.units, families.FAMILY_ORDER, families.FAMILY_WHY
        )
        by_class = workload.units_by_class()
        seam_census = resolve_secondary_homes(list(enriched_by_id.values()))
        fragments = {
            unit.unit_id: unit_to_json(enriched_by_id[unit.unit_id], drafter) for unit in workload.units
        }
        echo_count = len(echo_ids)
        mismatches = enricher.mismatches
        print(
            f"[t] review.build group+shards {time.perf_counter() - phase:.1f}s", file=sys.stderr, flush=True
        )

    phase = time.perf_counter()
    manifest = _write_surface(
        out_dir,
        workload,
        classes,
        by_class,
        fragments,
        seam_census,
        echo_count,
        total_batches,
        batch_size,
        audit_path,
        ledger_path,
        before_font,
        after_font,
        repo_root,
        static_dir,
        mismatches,
    )
    print(f"[t] review.build manifest+check {time.perf_counter() - phase:.1f}s", file=sys.stderr, flush=True)
    return manifest


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


# --- table-diff mode -----------------------------------------------------------------


def _table_diff_unit_json(
    entry: tablediff.DiffEntry, unit_id: str, batch: int | None, full_configs, ink_identical: bool
) -> dict:
    witness = entry.witness
    members = entry.paired or (entry,)
    if entry.table == "treaty":
        old = entry.old
        new = entry.new
        before = {
            "glyphs": [entry.key.left, entry.key.right],  # type: ignore[union-attr]
            "seams": [old.junction if old else "absent"],
        }
        after = {
            "cells": [entry.key.left, entry.key.right],  # type: ignore[union-attr]
            "seams": [new.junction if new else "absent"],
            "extensions": [new.extension if new else 0],
        }
        diff_positions = [0, 1]
        pair = {"left": 0, "right": 1}
        explain = _treaty_explain(entry)
        provenance: list[str] = []
        summary = (
            f"The treaty row for {entry.key.label()} is {entry.bucket} under {entry.config}; "
            "old and new values are in the explain panel."
        )
    else:
        before = {
            "glyphs": [member.old.outcome for member in members if member.old is not None],
            "seams": [],
        }
        after = {
            "cells": [member.new.outcome for member in members if member.new is not None],
            "seams": [],
            "extensions": [],
        }
        diff_positions = [0] if (before["glyphs"] or after["cells"]) else []
        pair = None
        explain = _settlement_explain(entry)
        summary = (
            f"The settlement row for {entry.key.label()} is {entry.bucket} under {entry.config}; "
            "old and new values are in the explain panel."
        )
        provenance = sorted(
            {
                pointer.strip()
                for member in members
                for value in (member.old, member.new)
                if value is not None and getattr(value, "provenance", "")
                for pointer in value.provenance.split(";")
                if pointer.strip()
            }
        )
    return {
        "id": unit_id,
        "batch": batch,
        "ink_identical": ink_identical,
        "no_verdict": False,
        "echo": None,
        "class": entry.bucket,
        "group": f"{entry.table}:{getattr(entry.key, 'input', getattr(entry.key, 'left', ''))}",
        "codepoints": ":".join(f"{value:04X}" for value in witness) if witness else None,
        "text_entities": text_entities(witness) if witness else None,
        "notation": notation(witness) if witness else entry.key.label(),
        "notation_tokens": list(notation_tokens(witness)) if witness else None,
        "configs": [entry.config],
        "config_note": config_note((entry.config,), full_configs),
        "render_groups": [{"configs": [entry.config]}],
        "kinds": [entry.table],
        "exemplar": False,
        "before": before,
        "after": after,
        "diff_positions": diff_positions,
        "pair": pair,
        "pair_codepoints": None,
        "highlight": None,
        "boundary_marks": [],
        "summary": summary,
        "explain": explain,
        "provenance": provenance,
        "drafts": {"pin": None, "policy": None, "any_of": None},
    }


def _settlement_explain(entry: tablediff.DiffEntry) -> str:
    lines = [f"settlement diff ({entry.bucket}), config {entry.config}"]
    for member in entry.paired or (entry,):
        key = member.key
        lines.append(f"  context: {key.label()}")
        if member.old is not None:
            lines.append(f"    old: {member.old.outcome}" + (" [joint]" if member.old.joint else ""))
            if member.old.provenance:
                lines.append(f"    old provenance: {member.old.provenance}")
        if member.new is not None:
            lines.append(f"    new: {member.new.outcome}" + (" [joint]" if member.new.joint else ""))
            if member.new.provenance:
                lines.append(f"    new provenance: {member.new.provenance}")
    return "\n".join(lines)


def _treaty_explain(entry: tablediff.DiffEntry) -> str:
    lines = [f"treaty diff ({entry.bucket}), config {entry.config}", f"  pair: {entry.key.label()}"]
    if entry.old is not None:
        lines.append(
            f"    old: junction {entry.old.junction}, extension {entry.old.extension}, kern {entry.old.kern}"
        )
    if entry.new is not None:
        lines.append(
            f"    new: junction {entry.new.junction}, extension {entry.new.extension}, kern {entry.new.kern}"
        )
    return "\n".join(lines)


def build_table_diff(
    out_dir: Path,
    baseline_dir: Path,
    new_dir: Path,
    before_font: Path,
    after_font: Path,
    repo_root: Path = REPO_ROOT,
    batch_size: int = BATCH_SIZE,
    static_dir: Path = STATIC_DIR,
    with_witnesses: bool = True,
    witness_depth: int = 4,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = tablediff.diff_dirs(baseline_dir, new_dir)

    if with_witnesses and entries:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                spec = load_spec(repo_root)
            for config in sorted({entry.config for entry in entries}):
                tablediff.WitnessIndex(spec, config, max_depth=witness_depth).attach(entries)
        except Exception as error:  # noqa: BLE001 — witnesses are an enrichment, not a gate
            print(f"warning: witness search unavailable ({error})", file=sys.stderr)

    all_configs = sorted({entry.config for entry in entries})
    by_bucket: dict[str, list[tablediff.DiffEntry]] = {}
    for entry in entries:
        by_bucket.setdefault(entry.bucket, []).append(entry)

    comparator = InkComparator(before_font, after_font)
    classes_meta: list[dict] = []
    index = 0
    human_index = 0
    machine_units = 0
    machine_rows = 0
    machine_by_class: dict[str, int] = {}
    for bucket in tablediff.DIFF_BUCKETS:
        members = by_bucket.get(bucket, [])
        if not members:
            continue
        shard = []
        batches = set()
        machine_count = 0
        for entry in members:
            # A witnessless entry has no renderable text to shape, so it cannot be proven ink-identical and stays in the human workload.
            ink_identical = bool(entry.witness) and comparator.ink_identical(
                "".join(chr(value) for value in entry.witness), (entry.config,)
            )
            if ink_identical:
                batch = None
                machine_count += 1
                machine_rows += max(len(entry.paired), 1)
            else:
                batch = human_index // batch_size
                batches.add(batch)
                human_index += 1
            shard.append(_table_diff_unit_json(entry, f"u-{index:04d}", batch, all_configs, ink_identical))
            index += 1
        _write_json(out_dir / "units" / f"{bucket}.json", shard)
        machine_units += machine_count
        if machine_count:
            machine_by_class[bucket] = machine_count
        classes_meta.append(
            {
                "id": bucket,
                "status": None,
                "ink_identical": False,
                "no_verdict": False,
                "why": tablediff.BUCKET_WHY[bucket],
                "unit_count": len(members),
                "row_count": sum(max(len(entry.paired), 1) for entry in members),
                "machine_approved_count": machine_count,
                "shard": f"units/{bucket}.json",
                "batches": sorted(batches),
            }
        )

    fonts = {
        "before": _copy_font(before_font, out_dir, "before.otf", "AMS Review Before", repo_root),
        "after": _copy_font(after_font, out_dir, "after.otf", "AMS Review After", repo_root),
    }
    manifest = {
        "format": MANIFEST_FORMAT,
        "mode": "table-diff",
        "generated_at": _generated_at(Path(baseline_dir), Path(new_dir), before_font, after_font),
        "repo_head": _repo_head(repo_root),
        "source": {"baseline": str(baseline_dir), "new": str(new_dir)},
        "fonts": fonts,
        "configs": all_configs,
        "feature_descriptions": dict(FEATURE_DESCRIPTIONS),
        "batch_size": batch_size,
        "totals": {
            "units": index,
            "rows": sum(meta["row_count"] for meta in classes_meta),
            "batches": (human_index + batch_size - 1) // batch_size,
        },
        "machine_approved": {
            "units": machine_units,
            "rows": machine_rows,
            "method": VERIFICATION_METHOD,
            "by_class": machine_by_class,
        },
        "classes": classes_meta,
        "build_command": BUILD_COMMAND + " --mode table-diff",
        "serve_command": SERVE_COMMAND,
    }
    _write_json(out_dir / "manifest.json", manifest)
    pruned = _prune_orphan_shards(out_dir, manifest)
    if pruned:
        print(f"Pruned {len(pruned)} orphan shard(s): {', '.join(pruned)}", file=sys.stderr)
    copy_static(out_dir, static_dir)
    errors = check_output_dir(out_dir)
    if errors:
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:20]))
    return manifest


# --- the §7 contract checker (shared between the build's self-check and the tests) ------


def check_manifest(manifest: dict) -> list[str]:
    errors: list[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"manifest: {message}")

    need(manifest.get("format") == MANIFEST_FORMAT, f"format must be {MANIFEST_FORMAT}")
    need(manifest.get("mode") in ("m1-audit", "table-diff"), "mode must be m1-audit or table-diff")
    for key in ("generated_at", "repo_head", "build_command", "serve_command"):
        need(isinstance(manifest.get(key), str) and manifest.get(key), f"{key} must be a nonempty string")
    need(isinstance(manifest.get("source"), dict), "source must be a mapping")
    need(
        isinstance(manifest.get("configs"), list) and manifest.get("configs"),
        "configs must be a nonempty list",
    )
    need(isinstance(manifest.get("batch_size"), int), "batch_size must be an integer")
    totals = manifest.get("totals")
    need(isinstance(totals, dict), "totals must be a mapping")
    if isinstance(totals, dict):
        for key in ("units", "rows", "batches"):
            need(isinstance(totals.get(key), int), f"totals.{key} must be an integer")
        if manifest.get("mode") == "m1-audit":
            need(isinstance(totals.get("echo_groups"), int), "totals.echo_groups must be an integer")
    machine = manifest.get("machine_approved")
    need(isinstance(machine, dict), "machine_approved must be a mapping")
    if isinstance(machine, dict):
        for key in ("units", "rows"):
            need(isinstance(machine.get(key), int), f"machine_approved.{key} must be an integer")
        need(
            isinstance(machine.get("method"), str) and machine.get("method"),
            "machine_approved.method must be a nonempty string",
        )
        by_class = machine.get("by_class")
        need(
            isinstance(by_class, dict) and all(isinstance(count, int) for count in (by_class or {}).values()),
            "machine_approved.by_class must map class ids to integers",
        )
        if isinstance(by_class, dict) and isinstance(machine.get("units"), int):
            need(
                sum(by_class.values()) == machine["units"],
                "machine_approved.by_class must sum to machine_approved.units",
            )
    seam_census = manifest.get("secondary_seams")
    if seam_census is not None:
        need(
            isinstance(seam_census, dict)
            and {"units_with_markers", "seams_homed", "seams_homeless", "seams_suppressed_invisible"}
            == set(seam_census)
            and all(isinstance(count, int) for count in seam_census.values()),
            "secondary_seams must carry the four integer census counts",
        )
    fonts = manifest.get("fonts")
    need(isinstance(fonts, dict) and set(fonts or ()) == {"before", "after"}, "fonts must map before/after")
    if isinstance(fonts, dict):
        for side, record in fonts.items():
            for key in ("file", "family", "source", "sha256"):
                need(
                    isinstance(record.get(key), str) and record.get(key),
                    f"fonts.{side}.{key} must be a nonempty string",
                )
            need(isinstance(record.get("upem"), int), f"fonts.{side}.upem must be an integer")
    classes = manifest.get("classes")
    need(isinstance(classes, list) and classes, "classes must be a nonempty list")
    for meta in classes or ():
        identifier = meta.get("id", "<missing>")
        for key in ("id", "shard", "why"):
            need(isinstance(meta.get(key), str), f"classes[{identifier}].{key} must be a string")
        for key in ("unit_count", "row_count", "machine_approved_count"):
            need(isinstance(meta.get(key), int), f"classes[{identifier}].{key} must be an integer")
        need(isinstance(meta.get("batches"), list), f"classes[{identifier}].batches must be a list")
        need("status" in meta, f"classes[{identifier}].status must be present")
        need(
            isinstance(meta.get("ink_identical"), bool), f"classes[{identifier}].ink_identical must be a bool"
        )
        need(isinstance(meta.get("no_verdict"), bool), f"classes[{identifier}].no_verdict must be a bool")
    return errors


_SEAM_RE_TOKENS = ("break", "lig", "absent")


def _is_seam(token) -> bool:
    return isinstance(token, str) and (
        token in _SEAM_RE_TOKENS or (token.startswith("y") and token[1:].isdigit())
    )


def check_unit(unit: dict, mode: str = "m1-audit") -> list[str]:
    errors: list[str] = []
    identifier = unit.get("id", "<missing>")

    def need(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"unit {identifier}: {message}")

    need(isinstance(unit.get("id"), str) and unit.get("id", "").startswith("u-"), "id must look like u-NNNN")
    need(isinstance(unit.get("ink_identical"), bool), "ink_identical must be a bool")
    need(isinstance(unit.get("no_verdict"), bool), "no_verdict must be a bool")
    if unit.get("ink_identical") is True or unit.get("no_verdict") is True:
        need(unit.get("batch") is None, "ink-identical and no-verdict units must carry batch null")
    else:
        need(isinstance(unit.get("batch"), int), "batch must be an integer on human-workload units")
    need("echo" in unit, "echo must be present")
    echo = unit.get("echo")
    need(
        echo is None or (isinstance(echo, str) and echo.startswith("e-")),
        "echo must be null or an e-NNNN group id",
    )
    if mode == "m1-audit":
        if isinstance(unit.get("batch"), int):
            need(isinstance(echo, str), "human-workload units must carry an echo group id")
        else:
            need(echo is None, "units outside the human workload must carry echo null")
    for key in ("class", "group", "notation", "summary", "explain"):
        need(isinstance(unit.get(key), str) and unit.get(key) != "", f"{key} must be a nonempty string")
    need(isinstance(unit.get("configs"), list) and unit.get("configs"), "configs must be a nonempty list")
    need("config_note" in unit, "config_note must be present")
    note = unit.get("config_note")
    need(
        note is None or (isinstance(note, str) and note),
        "config_note must be null or a nonempty string",
    )
    groups = unit.get("render_groups")
    need(isinstance(groups, list) and groups, "render_groups must be a nonempty list")
    grouped_configs: list[str] = []
    for group in groups if isinstance(groups, list) else ():
        need(
            isinstance(group, dict) and isinstance(group.get("configs"), list) and group.get("configs"),
            "render_groups entries must carry a nonempty configs list",
        )
        if isinstance(group, dict) and isinstance(group.get("configs"), list):
            grouped_configs.extend(group["configs"])
    if isinstance(unit.get("configs"), list) and grouped_configs:
        need(
            len(grouped_configs) == len(set(grouped_configs))
            and sorted(grouped_configs) == sorted(unit["configs"]),
            "render_groups must partition configs exactly",
        )
    need(isinstance(unit.get("kinds"), list) and unit.get("kinds"), "kinds must be a nonempty list")
    need(isinstance(unit.get("exemplar"), bool), "exemplar must be a bool")
    need(isinstance(unit.get("provenance"), list), "provenance must be a list")
    need(isinstance(unit.get("boundary_marks"), list), "boundary_marks must be a list")
    for mark in unit.get("boundary_marks") or ():
        need(
            isinstance(mark, dict) and {"index", "kind", "x"} <= set(mark),
            "boundary marks must carry index/kind/x",
        )

    renderable = unit.get("codepoints") is not None
    if mode == "m1-audit":
        need(renderable, "codepoints must be present in m1-audit mode")
    if renderable:
        codepoints = unit.get("codepoints")
        need(
            isinstance(codepoints, str)
            and all(all(ch in "0123456789ABCDEF" for ch in part) for part in codepoints.split(":")),
            "codepoints must be colon-joined uppercase hex",
        )
        entities = unit.get("text_entities")
        need(
            isinstance(entities, str) and entities.startswith("&#x") and entities.endswith(";"),
            "text_entities must be numeric character references",
        )

    before = unit.get("before")
    after = unit.get("after")
    need(isinstance(before, dict) and isinstance(before.get("glyphs"), list), "before.glyphs must be a list")
    need(isinstance(before, dict) and isinstance(before.get("seams"), list), "before.seams must be a list")
    need(isinstance(after, dict) and isinstance(after.get("cells"), list), "after.cells must be a list")
    need(isinstance(after, dict) and isinstance(after.get("seams"), list), "after.seams must be a list")
    need(
        isinstance(after, dict) and isinstance(after.get("extensions"), list),
        "after.extensions must be a list",
    )
    if isinstance(before, dict) and isinstance(before.get("seams"), list):
        need(all(_is_seam(seam) for seam in before["seams"]), "before.seams must be break/lig/yN tokens")
    if isinstance(after, dict) and isinstance(after.get("seams"), list):
        need(all(_is_seam(seam) for seam in after["seams"]), "after.seams must be break/lig/yN tokens")
    if mode == "m1-audit" and isinstance(before, dict) and isinstance(after, dict):
        need(
            len(before.get("seams", ())) == max(len(before.get("glyphs", ())) - 1, 0),
            "before.seams must have one entry per inter-glyph gap",
        )
        need(
            len(after.get("seams", ())) == max(len(after.get("cells", ())) - 1, 0),
            "after.seams must have one entry per inter-cell gap",
        )
        need(
            len(after.get("extensions", ())) == len(after.get("seams", ())),
            "after.extensions must parallel after.seams",
        )

    need(isinstance(unit.get("diff_positions"), list), "diff_positions must be a list")
    pair = unit.get("pair")
    if pair is not None:
        need(
            isinstance(pair, dict)
            and isinstance(pair.get("left"), int)
            and isinstance(pair.get("right"), int)
            and pair["left"] < pair["right"],
            "pair must be {left, right} with left < right",
        )

    tokens = unit.get("notation_tokens")
    if mode == "m1-audit":
        need(
            isinstance(tokens, list) and tokens and all(isinstance(t, str) and t for t in tokens),
            "notation_tokens must be a nonempty list of nonempty strings in m1-audit mode",
        )
    if renderable and isinstance(tokens, list):
        need(
            len(tokens) == len(unit["codepoints"].split(":")),
            "notation_tokens must align one-to-one with codepoint positions",
        )
    need("pair_codepoints" in unit, "pair_codepoints must be present")
    span = unit.get("pair_codepoints")
    if span is not None:
        need(
            isinstance(span, list)
            and len(span) == 2
            and all(isinstance(value, int) for value in span)
            and 0 <= span[0] <= span[1],
            "pair_codepoints must be [start, end] with 0 <= start <= end",
        )
        if isinstance(span, list) and len(span) == 2 and isinstance(tokens, list):
            need(
                isinstance(span[1], int) and span[1] < len(tokens),
                "pair_codepoints must stay within the codepoint positions",
            )
    if mode == "m1-audit" and pair is not None:
        need(isinstance(span, list), "pair_codepoints must be non-null when pair is present")

    highlight = unit.get("highlight")
    if mode == "m1-audit":
        need(highlight is not None, "highlight must be present in m1-audit mode")
    if highlight is not None:
        for side in ("before", "after"):
            record = highlight.get(side) if isinstance(highlight, dict) else None
            need(
                isinstance(record, dict)
                and all(isinstance(record.get(key), int) for key in ("x_min", "x_max", "advance_total")),
                f"highlight.{side} must carry integer x_min/x_max/advance_total",
            )
            if isinstance(record, dict) and all(
                isinstance(record.get(key), int) for key in ("x_min", "x_max")
            ):
                need(record["x_min"] <= record["x_max"], f"highlight.{side} x_min must not exceed x_max")

    def need_rect(record, label: str) -> None:
        need(
            isinstance(record, dict)
            and all(isinstance(record.get(key), int) for key in ("x_min", "x_max", "advance_total")),
            f"{label} must carry integer x_min/x_max/advance_total",
        )
        if isinstance(record, dict) and all(isinstance(record.get(key), int) for key in ("x_min", "x_max")):
            need(record["x_min"] <= record["x_max"], f"{label} x_min must not exceed x_max")

    seams = unit.get("secondary_seams")
    if seams is not None:
        need(isinstance(seams, list) and seams, "secondary_seams must be null or a nonempty list")
        need(unit.get("ink_identical") is not True, "machine-approved units must not carry secondary_seams")
        for index, seam in enumerate(seams if isinstance(seams, list) else ()):
            label = f"secondary_seams[{index}]"
            if not isinstance(seam, dict) or {"pair", "before", "after", "home"} - set(seam):
                errors.append(f"unit {identifier}: {label} must carry pair/before/after/home")
                continue
            seam_pair = seam.get("pair")
            need(
                isinstance(seam_pair, dict)
                and isinstance(seam_pair.get("left"), int)
                and isinstance(seam_pair.get("right"), int)
                and seam_pair["left"] < seam_pair["right"],
                f"{label}.pair must be {{left, right}} with left < right",
            )
            if pair is not None and isinstance(seam_pair, dict):
                need(
                    (seam_pair.get("left"), seam_pair.get("right")) != (pair.get("left"), pair.get("right")),
                    f"{label} must not duplicate the primary pair",
                )
            need_rect(seam.get("before"), f"{label}.before")
            need_rect(seam.get("after"), f"{label}.after")
            home = seam.get("home")
            need(
                home is None or (isinstance(home, str) and home.startswith("u-")),
                f"{label}.home must be null or a unit id",
            )

    drafts = unit.get("drafts")
    need(
        isinstance(drafts, dict) and {"pin", "policy", "any_of"} <= set(drafts or ()),
        "drafts must carry pin/policy/any_of",
    )
    if isinstance(drafts, dict):
        pin = drafts.get("pin")
        if mode == "m1-audit":
            need(pin is not None, "drafts.pin must be present in m1-audit mode")
        if pin is not None:
            for key in ("expect", "attribute", "syntax", "semantics_after_font", "suggested_home"):
                need(
                    isinstance(pin.get(key), str) and pin.get(key),
                    f"drafts.pin.{key} must be a nonempty string",
                )
            need(
                pin.get("attribute") in ("data-expect", "data-expect-noncanonically"),
                "drafts.pin.attribute must be a data-expect attribute name",
            )
            need(
                pin.get("stylistic_set") is None or isinstance(pin.get("stylistic_set"), str),
                "drafts.pin.stylistic_set must be null or a string",
            )
        policy = drafts.get("policy")
        if policy is not None:
            for key in ("file", "keypath", "suggested_record", "decided_stage", "why_stub"):
                need(
                    isinstance(policy.get(key), str) and policy.get(key),
                    f"drafts.policy.{key} must be a nonempty string",
                )
            need(
                isinstance(policy.get("names_provenance"), list),
                "drafts.policy.names_provenance must be a list",
            )
            need(isinstance(policy.get("schema_valid"), bool), "drafts.policy.schema_valid must be a bool")
        any_of = drafts.get("any_of")
        if mode == "m1-audit":
            need(any_of is not None, "drafts.any_of must be present in m1-audit mode")
        if any_of is not None:
            need(
                isinstance(any_of.get("text"), str) and any_of.get("text"),
                "drafts.any_of.text must be a nonempty string",
            )
            need(isinstance(any_of.get("features"), dict), "drafts.any_of.features must be a mapping")
            need(
                isinstance(any_of.get("candidates"), list) and any_of.get("candidates"),
                "drafts.any_of.candidates must be a nonempty list",
            )
    return errors


def check_output_dir(out_dir: Path) -> list[str]:
    out_dir = Path(out_dir)
    errors: list[str] = []
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"{manifest_path} is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors.extend(check_manifest(manifest))
    mode = manifest.get("mode", "m1-audit")

    seen_units = 0
    seen_rows = 0
    seen_ids: set[str] = set()
    seen_machine_by_class: dict[str, int] = {}
    seam_homes: list[tuple[str, str]] = []
    seam_units = 0
    seams_homed = 0
    seams_homeless = 0
    for meta in manifest.get("classes", ()):
        shard_path = out_dir / meta.get("shard", "")
        if not shard_path.exists():
            errors.append(f"shard {meta.get('shard')} is missing")
            continue
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        if len(shard) != meta.get("unit_count"):
            errors.append(f"shard {meta['id']}: {len(shard)} units, manifest says {meta.get('unit_count')}")
        machine_count = 0
        for unit in shard:
            errors.extend(check_unit(unit, mode))
            if unit.get("class") != meta.get("id"):
                errors.append(f"unit {unit.get('id')}: class {unit.get('class')} in shard {meta.get('id')}")
            if unit.get("id") in seen_ids:
                errors.append(f"duplicate unit id {unit.get('id')}")
            seen_ids.add(unit.get("id"))
            if unit.get("no_verdict") != bool(meta.get("no_verdict")):
                errors.append(
                    f"unit {unit.get('id')}: no_verdict {unit.get('no_verdict')} in a class "
                    f"whose no_verdict is {meta.get('no_verdict')}"
                )
            if unit.get("ink_identical") is True:
                machine_count += 1
            elif (
                mode == "m1-audit"
                and unit.get("no_verdict") is not True
                and unit.get("batch") not in meta.get("batches", ())
            ):
                errors.append(f"unit {unit.get('id')}: batch {unit.get('batch')} not in class batches")
            if unit.get("secondary_seams"):
                seam_units += 1
                for seam in unit["secondary_seams"]:
                    if not isinstance(seam, dict):
                        continue
                    if seam.get("home") is None:
                        seams_homeless += 1
                    else:
                        seams_homed += 1
                        seam_homes.append((unit.get("id"), seam["home"]))
        if machine_count != meta.get("machine_approved_count"):
            errors.append(
                f"class {meta.get('id')}: {machine_count} ink-identical units, "
                f"manifest says {meta.get('machine_approved_count')}"
            )
        if machine_count:
            seen_machine_by_class[meta["id"]] = machine_count
        seen_units += len(shard)
        seen_rows += meta.get("row_count", 0)
    totals = manifest.get("totals", {})
    if seen_units != totals.get("units"):
        errors.append(f"totals.units {totals.get('units')} != {seen_units} shard units")
    if seen_rows != totals.get("rows"):
        errors.append(f"totals.rows {totals.get('rows')} != {seen_rows} summed class rows")
    machine = manifest.get("machine_approved") or {}
    if sum(seen_machine_by_class.values()) != machine.get("units"):
        errors.append(
            f"machine_approved.units {machine.get('units')} != "
            f"{sum(seen_machine_by_class.values())} ink-identical shard units"
        )
    if seen_machine_by_class != {key: value for key, value in (machine.get("by_class") or {}).items()}:
        errors.append("machine_approved.by_class does not match the shards' ink-identical counts")
    for unit_id, home in seam_homes:
        if home == unit_id:
            errors.append(f"unit {unit_id}: a secondary seam names itself as home")
        elif home not in seen_ids:
            errors.append(f"unit {unit_id}: secondary seam home {home} is not a unit in this output")
    seam_census = manifest.get("secondary_seams")
    if isinstance(seam_census, dict):
        for key, observed in (
            ("units_with_markers", seam_units),
            ("seams_homed", seams_homed),
            ("seams_homeless", seams_homeless),
        ):
            if seam_census.get(key) != observed:
                errors.append(f"secondary_seams.{key} {seam_census.get(key)} != {observed} in the shards")

    for side, record in (manifest.get("fonts") or {}).items():
        font_path = out_dir / record.get("file", "")
        if not font_path.exists():
            errors.append(f"fonts.{side}: {record.get('file')} is missing")
        elif _sha256(font_path) != record.get("sha256"):
            errors.append(f"fonts.{side}: sha256 mismatch")
    if not (out_dir / "index.html").exists():
        errors.append("index.html is missing")
    return errors


# --- CLI ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "snapshot":
        parser = argparse.ArgumentParser(
            prog="rebuild.review.build snapshot", description=tablediff.write_snapshot.__doc__
        )
        parser.add_argument("--tables", type=Path, required=True)
        parser.add_argument("--font", type=Path, required=True)
        parser.add_argument("--to", type=Path, required=True)
        args = parser.parse_args(argv[1:])
        tablediff.write_snapshot(args.tables, args.font, args.to, REPO_ROOT)
        print(f"Wrote {args.to}", file=sys.stderr)
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("m1-audit", "table-diff"), default="m1-audit")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--baseline", type=Path, help="baseline tables directory (table-diff mode)")
    parser.add_argument("--new", dest="new_dir", type=Path, help="new tables directory (table-diff mode)")
    parser.add_argument("--before-font", type=Path, default=SITE_BEFORE_FONT)
    parser.add_argument("--after-font", type=Path, default=M1_AFTER_FONT)
    parser.add_argument(
        "--jobs", type=int, default=1, help="per-unit worker budget for the surface build; 1 = serial"
    )
    args = parser.parse_args(argv)

    if args.mode == "table-diff":
        if not args.baseline or not args.new_dir:
            parser.error("table-diff mode needs --baseline and --new")
        manifest = build_table_diff(
            args.out,
            args.baseline,
            args.new_dir,
            args.before_font,
            args.after_font,
            batch_size=args.batch_size,
        )
    else:
        manifest = build_m1(
            args.out,
            before_font=args.before_font,
            after_font=args.after_font,
            batch_size=args.batch_size,
            jobs=args.jobs if args.jobs and args.jobs > 1 else 1,
        )
    totals = manifest["totals"]
    print(
        f"Wrote {args.out} ({totals['units']} units, {totals['rows']} rows, {totals['batches']} batches)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
