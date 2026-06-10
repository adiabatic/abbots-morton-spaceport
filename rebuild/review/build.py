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
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

from rebuild.review import tablediff
from rebuild.review.audit import ACCEPTANCE_CONFIGS, BATCH_SIZE, load_workload
from rebuild.review.drafts import Drafter
from rebuild.review.enrich import LETTERS, EnrichedUnit, Enricher, load_spec, notation, text_entities

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
    except OSError, subprocess.CalledProcessError:
        return "unknown"


def _generated_at(*inputs: Path) -> str:
    """Deterministic across consecutive builds of the same inputs (the §6 byte-identity gate), and different whenever an input changes: the latest input mtime as UTC ISO."""
    latest = max(path.stat().st_mtime for path in inputs if path.exists())
    return (
        datetime.datetime.fromtimestamp(latest, tz=datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def unit_to_json(enriched: EnrichedUnit, drafter: Drafter) -> dict:
    unit = enriched.unit
    pin = drafter.draft_pin(enriched)
    policy = drafter.draft_policy(enriched)
    any_of = drafter.draft_any_of(enriched)
    return {
        "id": unit.unit_id,
        "batch": unit.batch,
        "class": unit.class_id,
        "group": unit.group,
        "codepoints": unit.codepoints,
        "text_entities": enriched.text_entities,
        "notation": enriched.notation,
        "configs": list(unit.configs),
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
        "highlight": {"before": enriched.highlight_before, "after": enriched.highlight_after},
        "boundary_marks": list(enriched.boundary_marks),
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
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    workload = load_workload(audit_path, ledger_path, dict(LETTERS), batch_size)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(repo_root)
    enricher = Enricher(spec, subset_dir, after_font, repo_root=repo_root)
    drafter = Drafter(after_font, repo_root=repo_root)

    by_class = workload.units_by_class()
    classes_meta: list[dict] = []
    for entry in workload.classes_present:
        units = by_class[entry.id]
        shard = [unit_to_json(enricher.enrich(unit), drafter) for unit in units]
        _write_json(out_dir / "units" / f"{entry.id}.json", shard)
        classes_meta.append(
            {
                "id": entry.id,
                "status": entry.status,
                "ink_identical": entry.ink_identical,
                "why": entry.why,
                "unit_count": len(units),
                "row_count": sum(len(unit.rows) for unit in units),
                "shard": f"units/{entry.id}.json",
                "batches": sorted({unit.batch for unit in units}),
            }
        )

    fonts = {
        "before": _copy_font(before_font, out_dir, "before.otf", "AMS Review Before", repo_root),
        "after": _copy_font(after_font, out_dir, "after.otf", "AMS Review After", repo_root),
    }
    total_batches = max(unit.batch for unit in workload.units) + 1 if workload.units else 0
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
        "batch_size": batch_size,
        "totals": {"units": len(workload.units), "rows": workload.row_count, "batches": total_batches},
        "classes": classes_meta,
        "build_command": BUILD_COMMAND,
        "serve_command": SERVE_COMMAND,
    }
    _write_json(out_dir / "manifest.json", manifest)
    copy_static(out_dir, static_dir)
    if enricher.mismatches:
        print(
            f"warning: {len(enricher.mismatches)} units where re-settled cells diverge from the audit "
            f"(first: {enricher.mismatches[0]})",
            file=sys.stderr,
        )
    errors = check_output_dir(out_dir)
    if errors:
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:20]))
    return manifest


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


# --- table-diff mode -----------------------------------------------------------------


def _table_diff_unit_json(entry: tablediff.DiffEntry, unit_id: str, batch: int) -> dict:
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
        "class": entry.bucket,
        "group": f"{entry.table}:{getattr(entry.key, 'input', getattr(entry.key, 'left', ''))}",
        "codepoints": ":".join(f"{value:04X}" for value in witness) if witness else None,
        "text_entities": text_entities(witness) if witness else None,
        "notation": notation(witness) if witness else entry.key.label(),
        "configs": [entry.config],
        "kinds": [entry.table],
        "exemplar": False,
        "before": before,
        "after": after,
        "diff_positions": diff_positions,
        "pair": pair,
        "highlight": None,
        "boundary_marks": [],
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

    by_bucket: dict[str, list[tablediff.DiffEntry]] = {}
    for entry in entries:
        by_bucket.setdefault(entry.bucket, []).append(entry)

    classes_meta: list[dict] = []
    index = 0
    for bucket in tablediff.DIFF_BUCKETS:
        members = by_bucket.get(bucket, [])
        if not members:
            continue
        shard = []
        batches = set()
        for entry in members:
            batch = index // batch_size
            shard.append(_table_diff_unit_json(entry, f"u-{index:04d}", batch))
            batches.add(batch)
            index += 1
        _write_json(out_dir / "units" / f"{bucket}.json", shard)
        classes_meta.append(
            {
                "id": bucket,
                "status": None,
                "ink_identical": False,
                "why": tablediff.BUCKET_WHY[bucket],
                "unit_count": len(members),
                "row_count": sum(max(len(entry.paired), 1) for entry in members),
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
        "configs": sorted({entry.config for entry in entries}),
        "batch_size": batch_size,
        "totals": {
            "units": index,
            "rows": sum(meta["row_count"] for meta in classes_meta),
            "batches": (index + batch_size - 1) // batch_size,
        },
        "classes": classes_meta,
        "build_command": BUILD_COMMAND + " --mode table-diff",
        "serve_command": SERVE_COMMAND,
    }
    _write_json(out_dir / "manifest.json", manifest)
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
        for key in ("unit_count", "row_count"):
            need(isinstance(meta.get(key), int), f"classes[{identifier}].{key} must be an integer")
        need(isinstance(meta.get("batches"), list), f"classes[{identifier}].batches must be a list")
        need("status" in meta, f"classes[{identifier}].status must be present")
        need(
            isinstance(meta.get("ink_identical"), bool), f"classes[{identifier}].ink_identical must be a bool"
        )
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
    need(isinstance(unit.get("batch"), int), "batch must be an integer")
    for key in ("class", "group", "notation", "explain"):
        need(isinstance(unit.get(key), str) and unit.get(key) != "", f"{key} must be a nonempty string")
    need(isinstance(unit.get("configs"), list) and unit.get("configs"), "configs must be a nonempty list")
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
    for meta in manifest.get("classes", ()):
        shard_path = out_dir / meta.get("shard", "")
        if not shard_path.exists():
            errors.append(f"shard {meta.get('shard')} is missing")
            continue
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        if len(shard) != meta.get("unit_count"):
            errors.append(f"shard {meta['id']}: {len(shard)} units, manifest says {meta.get('unit_count')}")
        for unit in shard:
            errors.extend(check_unit(unit, mode))
            if unit.get("class") != meta.get("id"):
                errors.append(f"unit {unit.get('id')}: class {unit.get('class')} in shard {meta.get('id')}")
            if unit.get("id") in seen_ids:
                errors.append(f"duplicate unit id {unit.get('id')}")
            seen_ids.add(unit.get("id"))
            if mode == "m1-audit" and unit.get("batch") not in meta.get("batches", ()):
                errors.append(f"unit {unit.get('id')}: batch {unit.get('batch')} not in class batches")
        seen_units += len(shard)
        seen_rows += meta.get("row_count", 0)
    totals = manifest.get("totals", {})
    if seen_units != totals.get("units"):
        errors.append(f"totals.units {totals.get('units')} != {seen_units} shard units")
    if seen_rows != totals.get("rows"):
        errors.append(f"totals.rows {totals.get('rows')} != {seen_rows} summed class rows")

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
        )
    totals = manifest["totals"]
    print(
        f"Wrote {args.out} ({totals['units']} units, {totals['rows']} rows, {totals['batches']} batches)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
