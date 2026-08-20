"""The review-app generation CLI (rebuild/REVIEW-PLAN.md §1.3): assemble units, precompute enrichment and all three verdict drafts, and write the self-contained rebuild/out/review/ directory — manifest.json, one unit shard per class, the census-facts.json sidecar the artifact cycle's census refresh copies into the checked-in pins, copied fonts, and the static app files. Also the `snapshot` subcommand for accepted-state baselines.

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
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.pipeline.baseline_subset import M1_ALPHABET
from rebuild.review import census, families, tablediff, unit_cache
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    BATCH_SIZE,
    UNMATCHED_CLASS,
    AuditRow,
    _config_index,
    assign_batches,
    format_codepoints,
    load_workload,
    merge_ink_duplicate_units,
    parse_codepoints,
    signature_rows,
    synthesize_family_classes,
)
from rebuild.review.drafts import Drafter
from rebuild.review.families import assign_family
from rebuild.review.ink import (
    IDENTITY_DIFF,
    JUNIOR_VERIFICATION_METHOD,
    VERIFICATION_METHOD,
    InkComparator,
    JuniorOracle,
    delta_digest,
    shaper_for,
    signature_digest,
)
from rebuild.review.enrich import (
    LETTERS,
    EnrichedUnit,
    Enricher,
    SeamHomeUnit,
    load_spec,
    notation,
    notation_tokens,
    resolve_home_assignments,
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
SITE_JUNIOR_FONT = REPO_ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf"

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


def _alphabet_meta() -> dict:
    """How far the migration has come, for the surface chip. `migrated` is the letters this surface is built over — the subset filter's alphabet minus its boundary tokens, which is also the roster of runes under glyph_data/runes/ — against the whole Quikscript alphabet."""
    return {"migrated": len(M1_ALPHABET & set(LETTERS)), "total": len(LETTERS)}


def _inputs_fingerprint(repo_root: Path, m1_dir: Path, before_font: Path, junior_font: Path) -> dict:
    """Stage A values are copied from run_m1's recorded inputs_fingerprint.json rather than recomputed, so a surface rebuilt over stale out/m1 artifacts carries the stale hashes and the readiness checker can flag it; nulls mean the record predates fingerprinting."""
    stage_a = fingerprint.read_stage_a(m1_dir) or {key: None for key in fingerprint.STAGE_A_COMPONENTS}
    return {**stage_a, **fingerprint.stage_b(repo_root, before_font, junior_font)}


def _upem(path: Path) -> int:
    from fontTools.ttLib import TTFont

    return TTFont(str(path))["head"].unitsPerEm  # pyright: ignore[reportAttributeAccessIssue]


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


UNIT_ASSEMBLY_EPOCH = "2026-07-21T00:00:00Z"


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
    "ss03": "let letters join to a full-size ·Tea at the x-height",
    "ss04": "allow ·It to join at the baseline on both sides",
    "ss05": "allow ·Et·Tea·… double baseline joins again (older, manual-style behavior)",
    "ss06": "use gapped ·Owe (doesn’t connect at the top)",
    "ss07": "allow ·Owe·Day to join at the x-height again",
    "ss10": "suppress all joins for the wrapped letter(s)",
}


def _config_features(config: str) -> frozenset[str]:
    return frozenset() if config == "default" else frozenset(config.split("+"))


GATE_CONSTRAINT_CAP = 3


def _candidate_constraint_sets(tags, size):
    """Every assignment of on/off to `size` of the `tags`, most-on first so an inclusion gate always outranks an exclusion gate that selects the same configs, then in tag order. The most-on-first sweep is what keeps a lone feature's gate reading "only when ss03 is on" rather than an equivalent phrasing in the negative."""
    for on_count in range(size, -1, -1):
        for combination in combinations(tags, size):
            for on_tags in combinations(combination, on_count):
                yield {tag: tag in set(on_tags) for tag in combination}


def _gate_clauses(constraints) -> list[dict]:
    """A resolved constraint mapping rendered as the badge's ordered clauses — on first, then off, each group in tag order — with each clause carrying the prose the app prints for it. The on-first order puts the loud chip at the head of the badge. A lone ss10-on gate keeps its own wording, because "only under ss10" names the isolation overlay rather than a joining behavior. The `text` fields are the single home for this prose: config_note is their join, and the app renders them verbatim rather than re-deriving a phrase from the feature and state."""
    ordered = [
        (tag, "on" if on else "off")
        for wanted in (True, False)
        for tag, on in sorted(constraints.items())
        if on == wanted
    ]
    if ordered == [("ss10", "on")]:
        return [{"feature": "ss10", "state": "on", "text": "only under ss10"}]
    return [
        {
            "feature": tag,
            "state": state,
            "text": f"{'only when' if index == 0 else 'and'} {tag} is {state}",
        }
        for index, (tag, state) in enumerate(ordered)
    ]


@lru_cache(maxsize=None)
def _config_badge(
    unit_configs: tuple[str, ...], full_configs: tuple[str, ...]
) -> tuple[list[dict] | None, str | None]:
    covered = set(unit_configs)
    non_isolated = [config for config in full_configs if "ss10" not in _config_features(config)]
    if covered >= set(non_isolated):
        return None, None
    universe = (
        list(full_configs) if any("ss10" in _config_features(config) for config in covered) else non_isolated
    )
    tags = sorted({tag for config in universe for tag in _config_features(config)})
    for size in range(1, min(GATE_CONSTRAINT_CAP, len(tags)) + 1):
        for constraints in _candidate_constraint_sets(tags, size):
            selected = {
                config
                for config in universe
                if all((tag in _config_features(config)) == on for tag, on in constraints.items())
            }
            if selected == covered:
                clauses = _gate_clauses(constraints)
                return clauses, " ".join(clause["text"] for clause in clauses)
    return None, "only under: " + ", ".join(unit_configs)


def config_badge(unit_configs, full_configs) -> tuple[list[dict] | None, str | None]:
    """The per-unit config badge, as (gate, note). The gate is the minimal conjunction of feature on/off constraints selecting exactly the configs a divergence applies under — one clause per constraint, which the app draws as its own chip in that feature's color, so a set-gated unit is legible at a glance rather than as a config list to decode. The note is the clauses joined, kept as a string for the census histogram and for hover text.

    Both are null when the unit covers every non-ss10 config, the overwhelmingly common case where the set carries no information. When no conjunction of GATE_CONSTRAINT_CAP or fewer constraints pins the set — either because the set is a genuine disjunction, or because it needs more features named than the config list itself has entries — the gate stays null and the note falls back to the literal "only under: <set>".

    ss10 is a constraint like any other, except that a set touching no ss10 config resolves against the non-ss10 configs alone; that is what lets an exclusion gate like ss03-off stand without also spelling out the implied ss10-off.
    """
    return _config_badge(tuple(unit_configs), tuple(full_configs))


def config_gate(unit_configs, full_configs) -> list[dict] | None:
    return config_badge(unit_configs, full_configs)[0]


def config_note(unit_configs, full_configs) -> str | None:
    return config_badge(unit_configs, full_configs)[1]


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


def _machine_approved_meta(machine_units, junior_font: Path, repo_root: Path) -> dict:
    """The manifest's machine_approved record: the totals across both machine channels (ink-identical and junior-equivalent), the audit rows those units cover, the per-class unit counts (classes with zero machine-approved units are omitted), and one sub-record per channel carrying its own counts and verification method one-liner. The junior channel also records which Junior font testified, since that font is an oracle input the fonts block doesn't cover (it is never rendered by the app)."""
    by_class: dict[str, int] = {}
    channels = {
        "ink_identical": {"units": 0, "rows": 0, "method": VERIFICATION_METHOD},
        "junior_equivalent": {
            "units": 0,
            "rows": 0,
            "method": JUNIOR_VERIFICATION_METHOD,
            "junior_font": {"source": _relative(junior_font, repo_root), "sha256": _sha256(junior_font)},
        },
    }
    rows = 0
    for unit in machine_units:
        by_class[unit.class_id] = by_class.get(unit.class_id, 0) + 1
        rows += len(unit.rows)
        channel = channels["ink_identical" if unit.ink_identical else "junior_equivalent"]
        channel["units"] += 1
        channel["rows"] += len(unit.rows)
    return {
        "units": len(machine_units),
        "rows": rows,
        "method": VERIFICATION_METHOD,
        "by_class": by_class,
        "channels": channels,
    }


_SCAFFOLD_HEAD = (
    "id",
    "batch",
    "ink_identical",
    "junior_equivalent",
    "ink_deltas",
    "no_verdict",
    "echo",
    "cluster",
    "class",
    "group",
    "codepoints",
)
_SCAFFOLD_TAIL = (
    "configs",
    "config_note",
    "config_gate",
    "config_classes",
    "config_class_note",
    "render_groups",
    "kinds",
    "exemplar",
)


def unit_scaffold(unit, full_configs=ACCEPTANCE_CONFIGS) -> dict:
    """Every fragment field the build re-derives from the workload on each pass — the order- and ledger-derived values plus the phase-1 machine flags carried on the unit. One definition serves both emission paths: `unit_to_json` reads it for a freshly enriched unit, and the incremental build patches it over a cache-served fragment, so a served fragment can never freeze a field a full build would have moved."""
    gate, note = config_badge(unit.configs, full_configs)
    return {
        "id": unit.unit_id,
        "batch": unit.batch,
        "ink_identical": unit.ink_identical,
        "junior_equivalent": unit.junior_equivalent,
        "ink_deltas": dict(unit.ink_deltas),
        "no_verdict": unit.no_verdict,
        "echo": unit.echo,
        "cluster": unit.cluster,
        "class": unit.class_id,
        "group": unit.group,
        "codepoints": unit.codepoints,
        "configs": list(unit.configs),
        "config_note": note,
        "config_gate": gate,
        "config_classes": dict(unit.config_classes) or None,
        "config_class_note": _config_class_note(unit),
        "render_groups": [{"configs": list(group)} for group in unit.render_groups],
        "kinds": list(unit.kinds),
        "exemplar": unit.exemplar,
    }


def patch_cached_fragment(
    fragment: dict, unit, seams: list[dict], seam_assign, full_configs=ACCEPTANCE_CONFIGS
) -> dict:
    """Serve a prior build's fragment as this build's: re-stamp every scaffold field from the current workload and re-emit the secondary seams from the cached rects under this build's home assignments. In-place key assignment keeps the fragment's key order, so the served bytes stay identical to what a fresh `unit_to_json` emission would have written."""
    for key, value in unit_scaffold(unit, full_configs).items():
        fragment[key] = value
    entries = [
        {
            "pair": {"left": seam["pair"][0], "right": seam["pair"][1]},
            "before": seam["before"],
            "after": seam["after"],
            "home": home,
        }
        for seam, (home, suppressed) in zip(seams, seam_assign)
        if not suppressed
    ]
    fragment["secondary_seams"] = entries or None
    return fragment


def unit_to_json(enriched: EnrichedUnit, drafter: Drafter, full_configs=ACCEPTANCE_CONFIGS) -> dict:
    unit = enriched.unit
    pin = drafter.draft_pin(enriched)
    policy = drafter.draft_policy(enriched)
    any_of = drafter.draft_any_of(enriched)
    scaffold = unit_scaffold(unit, full_configs)
    fragment = {
        **{key: scaffold[key] for key in _SCAFFOLD_HEAD},
        "text_entities": enriched.text_entities,
        "notation": enriched.notation,
        "notation_tokens": list(enriched.notation_tokens),
        **{key: scaffold[key] for key in _SCAFFOLD_TAIL},
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
    fragment["content_key"] = unit_cache.carry_content_hash(fragment)
    return fragment


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
    """Delete units/*.json left over from ledger classes the manifest no longer references. Runs only after the manifest is written, so a mid-build crash leaves the orphans in place rather than a manifest pointing at a deleted shard. Touches only *.json directly under units/ — subdirectories, non-JSON files, fonts, static assets, and manifest.json are never considered."""
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


def _cluster_id_from_repr(configs, class_id, diffs_repr: str) -> str:
    """`_cluster_id` over a pre-rendered ink-diff repr, so the unit cache can carry the diffs across builds as the repr string instead of the (large, tuple-shaped) diffs themselves. The composed string is byte-for-byte `repr((tuple(configs), class_id, diffs))` — CPython renders a 3-tuple as exactly this join — which `test_cluster_id_repr_composition` pins against the tuple form."""
    key = f"({tuple(configs)!r}, {class_id!r}, {diffs_repr})"
    return "c-" + hashlib.sha1(key.encode()).hexdigest()[:8]


def _cluster_id(configs, class_id, diffs) -> str:
    """The blank-queue cluster signature the in-app docket view groups by: the echo key minus the judged pair, so every echo group nests inside exactly one cluster. The repr recipe must stay byte-compatible with rebuild/tools/review_docket.py's historical ids so recorded c- references keep resolving."""
    return _cluster_id_from_repr(configs, class_id, repr(diffs))


@dataclass(frozen=True)
class _UnitProjection:
    """The slim, picklable phase-1 result a surface worker returns per unit: everything the parent's serial reduces read plus everything the unit cache persists, and never the EnrichedUnit (its ≈61 KB ExplainReport stays alive worker-side for phase 2). The ink diffs travel as their repr string and its digest — the repr feeds the cluster id byte-contract, the digest is the echo key's diff component — so the parent never round-trips the tuple form."""

    unit_id: str
    ink_identical: bool
    junior_equivalent: bool
    ink_deltas: tuple[tuple[str, str], ...]
    diffs_repr: str
    diffs_digest: str
    family: str
    pair_codepoints: tuple[int, int] | None
    seam_home: SeamHomeUnit
    seam_rects: tuple[tuple[tuple[int, int], dict, dict], ...]
    mismatches: tuple[str, ...]


def _phase1_unit(unit, comparator, oracle, enricher) -> tuple[_UnitProjection, EnrichedUnit]:
    text = "".join(chr(value) for value in unit.codepoint_values)
    diffs = tuple(comparator.config_diff(text, config) for config in unit.configs)
    unit.ink_identical = all(diff == IDENTITY_DIFF for diff in diffs)
    unit.junior_equivalent = not unit.ink_identical and oracle.approves(unit.configs, text)
    unit.ink_deltas = {
        config: delta_digest(diff) for config, diff in zip(unit.configs, diffs) if diff != IDENTITY_DIFF
    }
    mismatch_mark = len(enricher.mismatches)
    enriched = enricher.enrich(unit)
    family = assign_family(enriched) if unit.class_id == UNMATCHED_CLASS else ""
    diffs_repr = repr(diffs)
    projection = _UnitProjection(
        unit_id=unit.unit_id,
        ink_identical=unit.ink_identical,
        junior_equivalent=unit.junior_equivalent,
        ink_deltas=tuple(unit.ink_deltas.items()),
        diffs_repr=diffs_repr,
        diffs_digest=hashlib.sha1(diffs_repr.encode()).hexdigest(),
        family=family,
        pair_codepoints=enriched.pair_codepoints,
        seam_home=seam_home_projection(enriched),
        seam_rects=tuple(
            (seam.pair, seam.highlight_before, seam.highlight_after) for seam in enriched.secondary_seams
        ),
        mismatches=tuple(enricher.mismatches[mismatch_mark:]),
    )
    return projection, enriched


def _phase2_unit(enriched: EnrichedUnit, injection, drafter: Drafter) -> dict:
    batch, echo, cluster, class_id, seam_assign = injection
    enriched.unit.batch = batch
    enriched.unit.echo = echo
    enriched.unit.cluster = cluster
    enriched.unit.class_id = class_id
    for seam, (home, suppressed) in zip(enriched.secondary_seams, seam_assign):
        seam.home = home
        seam.suppressed = suppressed
    return unit_to_json(enriched, drafter)


# Below this, pool startup (spawn plus two font loads per worker) stops paying for itself against a serial pass through the parent's shared shapers; see rebuild/out/cycle-timings.ndjson for the measured rates this was set from.
_SIGNATURE_POOL_THRESHOLD = 20_000

_signature_worker_state: dict = {}


def _signature_pool_init(before_font: Path, after_font: Path) -> None:
    _signature_worker_state["comparator"] = InkComparator(before_font, after_font)


def _signature_pair_digest(pair: tuple[str, str]) -> str:
    text, config = pair
    return signature_digest(_signature_worker_state["comparator"].signature(text, config))


def _resolve_signature_digests(
    rows: list[AuditRow],
    keyer: unit_cache.UnitKeyer,
    out_dir: Path,
    before_font: Path,
    after_font: Path,
    repo_root: Path,
    helpers_digest: str,
    jobs: int,
    fresh: bool,
) -> tuple[dict[tuple[str, str], str], dict[str, str], str, int]:
    """The ink-duplicate merge's signature digests, one per row of `signature_rows`, served from the persisted store where the content key still holds and shaped live for the remainder — across a spawn pool when the miss pile is deep enough to amortize its startup, else serially through the parent's shared shapers. Returns the digests keyed (codepoints, config), the store records to persist after the build, the store's environment stamp, and the count actually shaped."""
    environment = unit_cache.signature_environment(repo_root, before_font, helpers_digest)
    prior = None if fresh else unit_cache.load_signature_store(out_dir, environment)
    keys = {(row.codepoints, row.config): keyer.signature_key(row) for row in rows}
    signatures: dict[tuple[str, str], str] = {}
    entries: dict[str, str] = {}
    misses: list[AuditRow] = []
    for row in rows:
        digest = prior.get(keys[(row.codepoints, row.config)]) if prior else None
        if digest is None:
            misses.append(row)
        else:
            signatures[(row.codepoints, row.config)] = digest
            entries[keys[(row.codepoints, row.config)]] = digest
    if misses:
        pairs = [
            ("".join(chr(value) for value in parse_codepoints(row.codepoints)), row.config) for row in misses
        ]
        if jobs > 1 and len(misses) >= _SIGNATURE_POOL_THRESHOLD:
            ctx = multiprocessing.get_context("spawn")
            nworkers = min(jobs, len(misses))
            with ctx.Pool(
                nworkers, initializer=_signature_pool_init, initargs=(before_font, after_font)
            ) as pool:
                digests = pool.map(
                    _signature_pair_digest, pairs, chunksize=max(1, len(pairs) // (nworkers * 8))
                )
        else:
            comparator = InkComparator(before_font, after_font, shaper_for)
            digests = [signature_digest(comparator.signature(text, config)) for text, config in pairs]
        for row, digest in zip(misses, digests):
            signatures[(row.codepoints, row.config)] = digest
            entries[keys[(row.codepoints, row.config)]] = digest
    return signatures, entries, environment, len(misses)


def _surface_worker(conn, init: dict) -> None:
    """A persistent, stateful surface worker (spawn-only: uharfbuzz/fontTools C objects are not fork-safe, and drafts._import_test_shaping mutates a module-global singleton). Phase 1 computes config_diff + enrich over its slice and retains each EnrichedUnit in-process; phase 2 injects the parent's global fields and emits the shard JSON from the retained ExplainReports."""
    try:
        comparator = InkComparator(init["before_font"], init["after_font"], shaper_for)
        oracle = JuniorOracle(init["junior_font"], init["before_font"], init["after_font"], shaper_for)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spec = load_spec(init["repo_root"])
        enricher = Enricher(
            spec,
            init["subset_dir"],
            init["after_font"],
            repo_root=init["repo_root"],
            before_font=init["before_font"],
            shaper_factory=shaper_for,
        )
        drafter = Drafter(init["after_font"], repo_root=init["repo_root"], shaper_factory=shaper_for)
        retained: dict[str, EnrichedUnit] = {}
        while True:
            message = conn.recv()
            if message[0] == "stop":
                return
            if message[0] == "phase1":
                results: list[_UnitProjection] = []
                for unit in message[1]:
                    projection, enriched = _phase1_unit(unit, comparator, oracle, enricher)
                    retained[unit.unit_id] = enriched
                    results.append(projection)
                conn.send(("ok", results))
            elif message[0] == "phase2":
                fragments: dict[str, dict] = {}
                for unit_id, injection in message[1].items():
                    fragments[unit_id] = _phase2_unit(retained[unit_id], injection, drafter)
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


class _FreshRunner:
    """Phases 1–2 over the units the cache could not serve — in-process when `jobs` is 1, across persistent spawn workers otherwise, with identical per-unit semantics either way, which is what lets the serial and parallel builds share every reduce and stay byte-identical. The parent keeps the frozen ids/triage order and every order-sensitive reduce (batches, family promotion, echo numbering, secondary-home resolution); the runner holds the EnrichedUnits and emits the shard JSON."""

    def __init__(
        self,
        fresh: list,
        jobs: int,
        subset_dir: Path,
        before_font: Path,
        after_font: Path,
        junior_font: Path,
        repo_root: Path,
    ) -> None:
        self._fresh = fresh
        self._before_font = before_font
        self._after_font = after_font
        self._junior_font = junior_font
        self._subset_dir = subset_dir
        self._repo_root = repo_root
        self._retained: dict[str, EnrichedUnit] = {}
        self._drafter: Drafter | None = None
        self._procs: list = []
        self._conns: list = []
        self._slices: list[list] = []
        if jobs > 1 and len(fresh) > 1:
            nworkers = min(jobs, len(fresh))
            self._slices = _partition(fresh, nworkers)
            init = {
                "before_font": before_font,
                "after_font": after_font,
                "junior_font": junior_font,
                "subset_dir": subset_dir,
                "repo_root": repo_root,
            }
            ctx = multiprocessing.get_context("spawn")
            for _ in range(nworkers):
                parent_conn, child_conn = ctx.Pipe()
                proc = ctx.Process(target=_surface_worker, args=(child_conn, init))
                proc.start()
                child_conn.close()
                self._procs.append(proc)
                self._conns.append(parent_conn)

    def phase1(self) -> dict[str, _UnitProjection]:
        projections: dict[str, _UnitProjection] = {}
        if self._conns:
            for conn, chunk in zip(self._conns, self._slices):
                conn.send(("phase1", chunk))
            for conn in self._conns:
                reply = conn.recv()
                if reply[0] == "error":
                    raise RuntimeError("surface worker failed in phase 1:\n" + reply[1])
                for projection in reply[1]:
                    projections[projection.unit_id] = projection
        elif self._fresh:
            comparator = InkComparator(self._before_font, self._after_font, shaper_for)
            oracle = JuniorOracle(self._junior_font, self._before_font, self._after_font, shaper_for)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                spec = load_spec(self._repo_root)
            enricher = Enricher(
                spec,
                self._subset_dir,
                self._after_font,
                repo_root=self._repo_root,
                before_font=self._before_font,
                shaper_factory=shaper_for,
            )
            self._drafter = Drafter(self._after_font, repo_root=self._repo_root, shaper_factory=shaper_for)
            for unit in self._fresh:
                projection, enriched = _phase1_unit(unit, comparator, oracle, enricher)
                self._retained[unit.unit_id] = enriched
                projections[projection.unit_id] = projection
        return projections

    def phase2(self, injections: dict[str, tuple]) -> dict[str, dict]:
        fragments: dict[str, dict] = {}
        if self._conns:
            for conn, chunk in zip(self._conns, self._slices):
                payload = {unit.unit_id: injections[unit.unit_id] for unit in chunk}
                conn.send(("phase2", payload))
            for conn in self._conns:
                reply = conn.recv()
                if reply[0] == "error":
                    raise RuntimeError("surface worker failed in phase 2:\n" + reply[1])
                fragments.update(reply[1])
        else:
            for unit in self._fresh:
                assert self._drafter is not None
                fragments[unit.unit_id] = _phase2_unit(
                    self._retained[unit.unit_id], injections[unit.unit_id], self._drafter
                )
        return fragments

    def close(self) -> None:
        for conn in self._conns:
            try:
                conn.send(("stop",))
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        for proc in self._procs:
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
    subset_dir: Path,
    before_font: Path,
    after_font: Path,
    junior_font: Path,
    repo_root: Path,
    static_dir: Path,
    mismatches: list,
) -> dict:
    """Reassemble the per-unit JSON fragments into shards (per class, triage order), copy fonts, and write the manifest with its parent-once `generated_at`/`repo_head` stamps. The contract check runs over the in-memory shards and manifest it just assembled — the same dicts the writer serialized — instead of re-parsing the hundreds of megabytes it just wrote."""
    classes_meta: list[dict] = []
    shards_by_class: dict[str, list[dict]] = {}
    for entry in classes:
        units = by_class[entry.id]
        shard = [fragments[unit.unit_id] for unit in units]
        shards_by_class[entry.id] = shard
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
                "machine_approved_count": sum(
                    1 for unit in units if unit.ink_identical or unit.junior_equivalent
                ),
                "shard": f"units/{entry.id}.json",
                "batches": sorted({unit.batch for unit in units if unit.batch is not None}),
            }
        )

    fonts = {
        "before": _copy_font(before_font, out_dir, "before.otf", "AMS Review Before", repo_root),
        "after": _copy_font(after_font, out_dir, "after.otf", "AMS Review After", repo_root),
    }
    machine_units = [unit for unit in workload.units if unit.ink_identical or unit.junior_equivalent]
    manifest = {
        "format": MANIFEST_FORMAT,
        "mode": "m1-audit",
        "generated_at": _generated_at(audit_path, ledger_path, before_font, after_font),
        "repo_head": _repo_head(repo_root),
        "inputs_fingerprint": _inputs_fingerprint(repo_root, subset_dir, before_font, junior_font),
        "source": {
            "audit": _relative(audit_path, repo_root),
            "ledger": _relative(ledger_path, repo_root),
        },
        "fonts": fonts,
        "alphabet": _alphabet_meta(),
        "configs": list(ACCEPTANCE_CONFIGS),
        "feature_descriptions": dict(FEATURE_DESCRIPTIONS),
        "batch_size": batch_size,
        "human_unit_ids": [unit.unit_id for unit in workload.units if unit.batch is not None],
        "totals": {
            "units": len(workload.units),
            "rows": workload.row_count,
            "batches": total_batches,
            "echo_groups": echo_count,
        },
        "machine_approved": _machine_approved_meta(machine_units, junior_font, repo_root),
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
    errors = check_manifest(manifest)
    errors.extend(check_shards(manifest, shards_by_class))
    errors.extend(_check_output_files(out_dir, manifest))
    if errors:
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:20]))
    return manifest


@dataclass
class _UnitState:
    """One unit's phase-1 products in the parent, served from the cache or returned by the runner, in the one shape the global reduces and the store writer read."""

    ink_identical: bool
    junior_equivalent: bool
    ink_deltas: dict[str, str]
    diffs_digest: str
    diffs_repr: str | None
    cluster: str | None
    family: str
    pair_codepoints: tuple[int, int] | None
    seam_home: SeamHomeUnit
    seam_rects: list[dict]
    mismatches: list[str]


def _cached_seam_home(unit, cached: unit_cache.CachedUnit) -> SeamHomeUnit:
    proj = cached.proj
    return SeamHomeUnit(
        unit_id=unit.unit_id,
        codepoint_values=unit.codepoint_values,
        ink_identical=cached.ink_identical,
        pair=(proj["pair"][0], proj["pair"][1]) if proj["pair"] else None,
        after_spans=tuple((span[0], span[1]) for span in proj["after_spans"]),
        after_cells=tuple(proj["after_cells"]),
        after_seams=tuple(proj["after_seams"]),
        before_spans=tuple((span[0], span[1]) for span in proj["before_spans"]),
        before_glyphs=tuple(proj["before_glyphs"]),
        before_seams=tuple(proj["before_seams"]),
        seam_pairs=tuple((seam["pair"][0], seam["pair"][1]) for seam in cached.seams),
    )


def _seam_home_record(seam_home: SeamHomeUnit) -> dict:
    return {
        "pair": list(seam_home.pair) if seam_home.pair else None,
        "after_spans": [list(span) for span in seam_home.after_spans],
        "after_cells": list(seam_home.after_cells),
        "after_seams": list(seam_home.after_seams),
        "before_spans": [list(span) for span in seam_home.before_spans],
        "before_glyphs": list(seam_home.before_glyphs),
        "before_seams": list(seam_home.before_seams),
    }


def build_m1(
    out_dir: Path = DEFAULT_OUT,
    audit_path: Path = M1_AUDIT,
    ledger_path: Path = M1_LEDGER,
    subset_dir: Path = M1_SUBSETS,
    before_font: Path = SITE_BEFORE_FONT,
    after_font: Path = M1_AFTER_FONT,
    junior_font: Path = SITE_JUNIOR_FONT,
    repo_root: Path = REPO_ROOT,
    batch_size: int = BATCH_SIZE,
    static_dir: Path = STATIC_DIR,
    jobs: int = 1,
    fresh_unit_cache: bool = False,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    phase = time.perf_counter()
    workload = load_workload(audit_path, ledger_path, dict(LETTERS))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(repo_root)
    family_keys, helpers_digest = unit_cache.family_content_keys(repo_root, spec, after_font)
    keyer = unit_cache.UnitKeyer(family_keys, dict(LETTERS))
    signatures, signature_entries, signature_environment, signatures_shaped = _resolve_signature_digests(
        signature_rows(workload.units),
        keyer,
        out_dir,
        before_font,
        after_font,
        repo_root,
        helpers_digest,
        jobs,
        fresh_unit_cache,
    )

    def ink_sig(text: str, config: str) -> str:
        return signatures[(format_codepoints(tuple(ord(ch) for ch in text)), config)]

    exempt_classes = {entry.id for entry in workload.ledger if entry.no_verdict}
    premerge_capture = census.capture_premerge(workload.units)
    merge_ink_duplicate_units(workload.units, ink_sig, exempt_classes)
    present = {unit.class_id for unit in workload.units}
    workload.classes_present = [entry for entry in workload.ledger if entry.id in present]
    print(
        f"[t] review.build load {time.perf_counter() - phase:.1f}s"
        f"\t(signatures: {len(signatures) - signatures_shaped} cached, {signatures_shaped} shaped)",
        file=sys.stderr,
        flush=True,
    )

    # The incremental plan (issue 20; rebuild/review/unit_cache.py is the contract): key every unit over its content closure, serve what the previous surface already computed, and hand the runner only the remainder. The reduces below always run over the full universe, so every order- or ledger-derived field is this build's own.
    phase = time.perf_counter()
    environment = unit_cache.environment_stamp(
        repo_root, spec, subset_dir, before_font, junior_font, helpers_digest
    )
    keys = {unit.unit_id: keyer.key(unit) for unit in workload.units}
    store = None if fresh_unit_cache else unit_cache.load_store(out_dir, environment)
    served: dict[str, unit_cache.CachedUnit] = {}
    prior_fragments: dict[str, dict] = {}
    if store:
        # A key shared by two current units would hand both the same prior fragment object, and the in-place patch would corrupt one of them; distinct units always carry distinct rows today, so a collision means something upstream broke — recompute both rather than serve either.
        key_counts = Counter(keys.values())
        candidates = {
            unit.unit_id: store[keys[unit.unit_id]]
            for unit in workload.units
            if keys[unit.unit_id] in store and key_counts[keys[unit.unit_id]] == 1
        }
        wanted: dict[str, set[str]] = {}
        for cached in candidates.values():
            wanted.setdefault(cached.prior_class, set()).add(cached.prior_id)
        prior_fragments = unit_cache.load_prior_fragments(out_dir, wanted)
        served = {uid: cached for uid, cached in candidates.items() if cached.prior_id in prior_fragments}
    fresh = [unit for unit in workload.units if unit.unit_id not in served]
    print(
        f"[t] review.build plan {time.perf_counter() - phase:.1f}s"
        f"\t(served {len(served)} of {len(workload.units)} units from cache)",
        file=sys.stderr,
        flush=True,
    )

    phase = time.perf_counter()
    runner = _FreshRunner(fresh, jobs, subset_dir, before_font, after_font, junior_font, repo_root)
    try:
        projections = runner.phase1()

        states: dict[str, _UnitState] = {}
        for unit in workload.units:
            cached = served.get(unit.unit_id)
            if cached is not None:
                states[unit.unit_id] = _UnitState(
                    ink_identical=cached.ink_identical,
                    junior_equivalent=cached.junior_equivalent,
                    ink_deltas=dict(cached.ink_deltas),
                    diffs_digest=cached.diffs_digest,
                    diffs_repr=None,
                    cluster=cached.cluster,
                    family=cached.family,
                    pair_codepoints=cached.pair_codepoints,
                    seam_home=_cached_seam_home(unit, cached),
                    seam_rects=cached.seams,
                    mismatches=cached.mismatches,
                )
            else:
                projection = projections[unit.unit_id]
                states[unit.unit_id] = _UnitState(
                    ink_identical=projection.ink_identical,
                    junior_equivalent=projection.junior_equivalent,
                    ink_deltas=dict(projection.ink_deltas),
                    diffs_digest=projection.diffs_digest,
                    diffs_repr=projection.diffs_repr,
                    cluster=None,
                    family=projection.family,
                    pair_codepoints=projection.pair_codepoints,
                    seam_home=projection.seam_home,
                    seam_rects=[
                        {"pair": list(pair), "before": before, "after": after}
                        for pair, before, after in projection.seam_rects
                    ],
                    mismatches=list(projection.mismatches),
                )

        for unit in workload.units:
            state = states[unit.unit_id]
            unit.ink_identical = state.ink_identical
            unit.junior_equivalent = state.junior_equivalent
            unit.ink_deltas = dict(state.ink_deltas)
        total_batches = assign_batches(workload.units, batch_size)

        # Promote each UNMATCHED unit's verdict family to its class so the per-class shard loop shards it under that family.
        for unit in workload.units:
            if unit.class_id == UNMATCHED_CLASS:
                unit.family_id = states[unit.unit_id].family
                unit.class_id = unit.family_id

        # The cluster signature is computed for every unit, machine-approved ones included, because the store carries it forward: a served unit can cross into the human workload on a ledger edit alone (no_verdict flipping), and its cluster must already exist. Served units trust the stored value — its inputs (configs, final class, the ink diffs) are all under the content key.
        for unit in workload.units:
            state = states[unit.unit_id]
            if state.diffs_repr is not None:
                state.cluster = _cluster_id_from_repr(unit.configs, unit.class_id, state.diffs_repr)

        # Echo groups: human units whose judged pair, class, config set, and per-config ink deltas all agree show the same change in different surroundings, so one verdict answers all of them. Keyed after family promotion so the class component is final; ids are assigned in triage order.
        echo_ids: dict[tuple, str] = {}
        for unit in workload.units:
            if unit.batch is None:
                continue
            state = states[unit.unit_id]
            pair = None
            if state.pair_codepoints:
                values = unit.codepoint_values
                pair = (values[state.pair_codepoints[0]], values[state.pair_codepoints[1]])
            key = (unit.configs, pair, unit.class_id, state.diffs_digest)
            unit.echo = echo_ids.setdefault(key, f"e-{len(echo_ids):04d}")
            unit.cluster = state.cluster

        classes = workload.classes_present + synthesize_family_classes(
            workload.units, families.FAMILY_ORDER, families.FAMILY_WHY
        )
        by_class = workload.units_by_class()
        assignments, seam_census = resolve_home_assignments(
            [states[unit.unit_id].seam_home for unit in workload.units]
        )

        fragments = runner.phase2(
            {
                unit.unit_id: (unit.batch, unit.echo, unit.cluster, unit.class_id, assignments[unit.unit_id])
                for unit in fresh
            }
        )
    finally:
        runner.close()

    for unit in workload.units:
        cached = served.get(unit.unit_id)
        if cached is not None:
            fragments[unit.unit_id] = patch_cached_fragment(
                prior_fragments[cached.prior_id], unit, cached.seams, assignments[unit.unit_id]
            )
    mismatches = [line for unit in workload.units for line in states[unit.unit_id].mismatches]
    echo_count = len(echo_ids)
    print(
        f"[t] review.build units {time.perf_counter() - phase:.1f}s\t(jobs={jobs}, fresh={len(fresh)})",
        file=sys.stderr,
        flush=True,
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
        subset_dir,
        before_font,
        after_font,
        junior_font,
        repo_root,
        static_dir,
        mismatches,
    )
    print(f"[t] review.build manifest+check {time.perf_counter() - phase:.1f}s", file=sys.stderr, flush=True)

    phase = time.perf_counter()
    premerge_facts = census.derive_premerge(premerge_capture, workload.units)
    census.write_facts(
        out_dir,
        census.build_facts(
            manifest, workload.units, fragments, premerge_capture, premerge_facts, workload.row_count
        ),
    )
    print(f"[t] review.build census-facts {time.perf_counter() - phase:.1f}s", file=sys.stderr, flush=True)

    phase = time.perf_counter()
    records = []
    for unit in workload.units:
        state = states[unit.unit_id]
        assert state.cluster is not None
        records.append(
            unit_cache.CachedUnit(
                key=keys[unit.unit_id],
                prior_id=unit.unit_id,
                prior_class=unit.class_id,
                ink_identical=unit.ink_identical,
                junior_equivalent=unit.junior_equivalent,
                ink_deltas=dict(unit.ink_deltas),
                diffs_digest=state.diffs_digest,
                cluster=state.cluster,
                family=state.family,
                pair_codepoints=state.pair_codepoints,
                proj=_seam_home_record(state.seam_home),
                seams=state.seam_rects,
                mismatches=state.mismatches,
            )
        )
    unit_cache.write_store(out_dir, environment, records)
    unit_cache.write_signature_store(out_dir, signature_environment, signature_entries)
    print(f"[t] review.build cache {time.perf_counter() - phase:.1f}s", file=sys.stderr, flush=True)
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
    gate, note = config_badge((entry.config,), full_configs)
    if entry.table == "treaty":
        old = entry.old
        new = entry.new
        before = {
            "glyphs": [entry.key.left, entry.key.right],
            "seams": [old.junction if old else "absent"],
        }
        after = {
            "cells": [entry.key.left, entry.key.right],
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
        members = entry.paired or (entry,)
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
        "junior_equivalent": False,
        "no_verdict": False,
        "echo": None,
        "cluster": None,
        "class": entry.bucket,
        "group": f"{entry.table}:{getattr(entry.key, 'input', getattr(entry.key, 'left', ''))}",
        "codepoints": ":".join(f"{value:04X}" for value in witness) if witness else None,
        "text_entities": text_entities(witness) if witness else None,
        "notation": notation(witness) if witness else entry.key.label(),
        "notation_tokens": list(notation_tokens(witness)) if witness else None,
        "configs": [entry.config],
        "config_note": note,
        "config_gate": gate,
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


def _settlement_explain(entry: tablediff.SettlementDiffEntry) -> str:
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


def _treaty_explain(entry: tablediff.TreatyDiffEntry) -> str:
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
    witness_depth: int = 5,
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
    human_unit_ids: list[str] = []
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
            unit_id = f"u-{index:04d}"
            if batch is not None:
                human_unit_ids.append(unit_id)
            shard.append(_table_diff_unit_json(entry, unit_id, batch, all_configs, ink_identical))
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
        "inputs_fingerprint": {key: None for key in fingerprint.COMPONENTS},
        "source": {"baseline": str(baseline_dir), "new": str(new_dir)},
        "fonts": fonts,
        "alphabet": _alphabet_meta(),
        "configs": all_configs,
        "feature_descriptions": dict(FEATURE_DESCRIPTIONS),
        "batch_size": batch_size,
        "human_unit_ids": human_unit_ids,
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

    def need(condition: object, message: str) -> None:
        if not condition:
            errors.append(f"manifest: {message}")

    need(manifest.get("format") == MANIFEST_FORMAT, f"format must be {MANIFEST_FORMAT}")
    need(manifest.get("mode") in ("m1-audit", "table-diff"), "mode must be m1-audit or table-diff")
    for key in ("generated_at", "repo_head", "build_command", "serve_command"):
        need(isinstance(manifest.get(key), str) and manifest.get(key), f"{key} must be a nonempty string")
    need(isinstance(manifest.get("source"), dict), "source must be a mapping")
    human_unit_ids = manifest.get("human_unit_ids")
    valid_human_unit_ids = isinstance(human_unit_ids, list) and all(
        isinstance(unit, str) and unit.startswith("u-") for unit in human_unit_ids
    )
    need(valid_human_unit_ids, "human_unit_ids must be a list of u- ids")
    if isinstance(human_unit_ids, list) and all(isinstance(unit, str) for unit in human_unit_ids):
        need(len(human_unit_ids) == len(set(human_unit_ids)), "human_unit_ids must be unique")
    inputs = manifest.get("inputs_fingerprint")
    need(
        isinstance(inputs, dict)
        and set(inputs) == set(fingerprint.COMPONENTS)
        and all(value is None or isinstance(value, str) for value in inputs.values()),
        "inputs_fingerprint must map the six input components to hashes or null",
    )
    need(
        isinstance(manifest.get("configs"), list) and manifest.get("configs"),
        "configs must be a nonempty list",
    )
    need(isinstance(manifest.get("batch_size"), int), "batch_size must be an integer")
    alphabet = manifest.get("alphabet")
    need(
        isinstance(alphabet, dict)
        and set(alphabet or ()) == {"migrated", "total"}
        and all(isinstance(count, int) for count in (alphabet or {}).values()),
        "alphabet must carry integer migrated/total letter counts",
    )
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
        channels = machine.get("channels")
        if channels is not None:
            need(
                isinstance(channels, dict) and set(channels) == {"ink_identical", "junior_equivalent"},
                "machine_approved.channels must map the two machine channels",
            )
            if isinstance(channels, dict):
                for channel, record in channels.items():
                    if not isinstance(record, dict):
                        need(False, f"machine_approved.channels.{channel} must be a mapping")
                        continue
                    for key in ("units", "rows"):
                        need(
                            isinstance(record.get(key), int),
                            f"machine_approved.channels.{channel}.{key} must be an integer",
                        )
                    need(
                        isinstance(record.get("method"), str) and record.get("method"),
                        f"machine_approved.channels.{channel}.method must be a nonempty string",
                    )
                if all(isinstance(record, dict) for record in channels.values()) and isinstance(
                    machine.get("units"), int
                ):
                    need(
                        sum(record.get("units", 0) for record in channels.values()) == machine["units"],
                        "machine_approved.channels must sum to machine_approved.units",
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


def _is_delta_digest(token) -> bool:
    return (
        isinstance(token, str)
        and len(token) == 14
        and token.startswith("d-")
        and all(ch in "0123456789abcdef" for ch in token[2:])
    )


def check_unit(unit: dict, mode: str = "m1-audit") -> list[str]:
    errors: list[str] = []
    identifier = unit.get("id", "<missing>")

    def need(condition: object, message: str) -> None:
        if not condition:
            errors.append(f"unit {identifier}: {message}")

    need(isinstance(unit.get("id"), str) and unit.get("id", "").startswith("u-"), "id must look like u-NNNN")
    need(isinstance(unit.get("ink_identical"), bool), "ink_identical must be a bool")
    need(isinstance(unit.get("junior_equivalent", False), bool), "junior_equivalent must be a bool")
    if mode == "m1-audit":
        deltas = unit.get("ink_deltas")
        need(isinstance(deltas, dict), "ink_deltas must be a mapping")
        if isinstance(deltas, dict):
            need(
                all(isinstance(config, str) and config for config in deltas)
                and all(_is_delta_digest(value) for value in deltas.values()),
                "ink_deltas must map configs to d- delta digests",
            )
            if isinstance(unit.get("configs"), list):
                need(set(deltas) <= set(unit["configs"]), "ink_deltas keys must be a subset of configs")
            if unit.get("ink_identical") is True:
                need(not deltas, "ink-identical units must carry empty ink_deltas")
            elif unit.get("ink_identical") is False:
                need(bool(deltas), "units with ink changes must carry a nonempty ink_deltas")
        stamp = unit.get("content_key")
        need(
            isinstance(stamp, str) and len(stamp) == 64 and all(ch in "0123456789abcdef" for ch in stamp),
            "content_key must be a sha256 hex stamp in m1-audit mode",
        )
    need(isinstance(unit.get("no_verdict"), bool), "no_verdict must be a bool")
    # This equivalence is what lets every consumer read batch alone rather than re-deriving the disjunction: render.js's needsNoVerdict, export's human_units_total, complaint_docket, and carry_verdicts all split the workload on batch being null.
    if (
        unit.get("ink_identical") is True
        or unit.get("junior_equivalent") is True
        or unit.get("no_verdict") is True
    ):
        need(unit.get("batch") is None, "machine-approved and no-verdict units must carry batch null")
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
    need("cluster" in unit, "cluster must be present")
    cluster = unit.get("cluster")
    need(
        cluster is None or (isinstance(cluster, str) and cluster.startswith("c-")),
        "cluster must be null or a c-XXXXXXXX signature id",
    )
    if mode == "m1-audit":
        if isinstance(unit.get("batch"), int):
            need(isinstance(cluster, str), "human-workload units must carry a cluster signature id")
        else:
            need(cluster is None, "units outside the human workload must carry cluster null")
    for key in ("class", "group", "notation", "summary", "explain"):
        need(isinstance(unit.get(key), str) and unit.get(key) != "", f"{key} must be a nonempty string")
    need(isinstance(unit.get("configs"), list) and unit.get("configs"), "configs must be a nonempty list")
    need("config_note" in unit, "config_note must be present")
    note = unit.get("config_note")
    need(
        note is None or (isinstance(note, str) and note),
        "config_note must be null or a nonempty string",
    )
    need("config_gate" in unit, "config_gate must be present")
    clauses = unit.get("config_gate")
    need(
        clauses is None or (isinstance(clauses, list) and clauses),
        "config_gate must be null or a nonempty clause list",
    )
    for clause in clauses if isinstance(clauses, list) else ():
        need(
            isinstance(clause, dict)
            and isinstance(clause.get("feature"), str)
            and clause.get("state") in ("on", "off")
            and isinstance(clause.get("text"), str)
            and clause.get("text"),
            "config_gate clauses must carry a feature, an on/off state, and nonempty text",
        )
    if isinstance(clauses, list) and clauses:
        need(
            note == " ".join(clause.get("text", "") for clause in clauses),
            "config_note must be the config_gate clause texts joined",
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


def check_shards(manifest: dict, shards_by_class: dict[str, list[dict]]) -> list[str]:
    """The unit-grain half of the §7 contract check over in-memory shard payloads, keyed by class id — shared between the build's post-write self-check (which hands it the very dicts it serialized) and `check_output_dir` (which re-parses them from disk). Classes missing from the mapping are reported by the caller, which knows whether that means an unwritten file or an unassembled shard."""
    errors: list[str] = []
    mode = manifest.get("mode", "m1-audit")

    seen_units = 0
    seen_rows = 0
    seen_ids: set[str | None] = set()
    seen_human_ids: set[str] = set()
    seen_machine_by_class: dict[str, int] = {}
    seam_homes: list[tuple[str | None, str]] = []
    seam_units = 0
    seams_homed = 0
    seams_homeless = 0
    for meta in manifest.get("classes", ()):
        shard = shards_by_class.get(meta.get("id", ""))
        if shard is None:
            continue
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
            if unit.get("batch") is not None and isinstance(unit.get("id"), str):
                seen_human_ids.add(unit["id"])
            if unit.get("no_verdict") != bool(meta.get("no_verdict")):
                errors.append(
                    f"unit {unit.get('id')}: no_verdict {unit.get('no_verdict')} in a class "
                    f"whose no_verdict is {meta.get('no_verdict')}"
                )
            if unit.get("ink_identical") is True or unit.get("junior_equivalent") is True:
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
                f"class {meta.get('id')}: {machine_count} machine-approved units, "
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
    human_unit_ids = manifest.get("human_unit_ids")
    if isinstance(human_unit_ids, list) and all(isinstance(unit, str) for unit in human_unit_ids):
        if set(human_unit_ids) != seen_human_ids:
            errors.append("human_unit_ids does not match the shards' non-null batches")
    machine = manifest.get("machine_approved") or {}
    if sum(seen_machine_by_class.values()) != machine.get("units"):
        errors.append(
            f"machine_approved.units {machine.get('units')} != "
            f"{sum(seen_machine_by_class.values())} machine-approved shard units"
        )
    if seen_machine_by_class != {key: value for key, value in (machine.get("by_class") or {}).items()}:
        errors.append("machine_approved.by_class does not match the shards' machine-approved counts")
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
    return errors


def _check_output_files(out_dir: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    for side, record in (manifest.get("fonts") or {}).items():
        font_path = Path(out_dir) / record.get("file", "")
        if not font_path.exists():
            errors.append(f"fonts.{side}: {record.get('file')} is missing")
        elif _sha256(font_path) != record.get("sha256"):
            errors.append(f"fonts.{side}: sha256 mismatch")
    if not (Path(out_dir) / "index.html").exists():
        errors.append("index.html is missing")
    return errors


def check_output_dir(out_dir: Path) -> list[str]:
    out_dir = Path(out_dir)
    errors: list[str] = []
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"{manifest_path} is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors.extend(check_manifest(manifest))
    shards_by_class: dict[str, list[dict]] = {}
    for meta in manifest.get("classes", ()):
        shard_path = out_dir / meta.get("shard", "")
        if not shard_path.exists():
            errors.append(f"shard {meta.get('shard')} is missing")
            continue
        shards_by_class[meta.get("id", "")] = json.loads(shard_path.read_text(encoding="utf-8"))
    errors.extend(check_shards(manifest, shards_by_class))
    errors.extend(_check_output_files(out_dir, manifest))
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
    parser.add_argument("--junior-font", type=Path, default=SITE_JUNIOR_FONT)
    parser.add_argument(
        "--jobs", type=int, default=1, help="per-unit worker budget for the surface build; 1 = serial"
    )
    parser.add_argument(
        "--fresh-unit-cache",
        action="store_true",
        help="ignore the persisted per-unit cache and recompute every unit from scratch",
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
            junior_font=args.junior_font,
            batch_size=args.batch_size,
            jobs=args.jobs if args.jobs and args.jobs > 1 else 1,
            fresh_unit_cache=args.fresh_unit_cache,
        )
    totals = manifest["totals"]
    print(
        f"Wrote {args.out} ({totals['units']} units, {totals['rows']} rows, {totals['batches']} batches)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
