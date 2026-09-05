"""The review-app generation CLI (rebuild/REVIEW-PLAN.md §1.3): assemble units, precompute enrichment and — for every unit that takes a verdict — all three verdict drafts, and write the self-contained rebuild/out/review/ directory — manifest.json, one unit shard per class (in byte-capped parts when a class outgrows one file), the census-facts.json sidecar the artifact cycle's census refresh copies into the checked-in pins, copied fonts, and the static app files. Also the `snapshot` subcommand for accepted-state baselines, and `refresh-assets`, which copies the static app files over an already-built surface and restamps that one fingerprint component without rebuilding a unit.

Usage:
    uv run python -m rebuild.review.build
    uv run python -m rebuild.review.build --mode table-diff --baseline <dir> --new <dir> --before-font <otf> --after-font <otf>
    uv run python -m rebuild.review.build snapshot --tables rebuild/out/m1 --font rebuild/out/m1/M1.otf --to rebuild/out/review-baseline
    uv run python -m rebuild.review.build refresh-assets
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import multiprocessing.connection
import random
import shutil
import subprocess
import sys
import time
import traceback
import warnings
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import batched, combinations
from pathlib import Path
from typing import TextIO, cast

from rebuild.pipeline import fingerprint
from rebuild.pipeline.baseline_subset import M1_ALPHABET
from rebuild.review import app_index, census, families, tablediff, unit_cache, unit_index
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    BATCH_SIZE,
    MACHINE_CHANNELS,
    SLIM_OMITTED_KEYS,
    UNMATCHED_CLASS,
    AuditRow,
    Unit,
    _config_index,
    assign_batches,
    format_codepoints,
    load_workload,
    machine_approved,
    merge_ink_duplicate_units,
    parse_codepoints,
    release_rows,
    signature_rows,
    slim_fragment,
    synthesize_family_classes,
)
from rebuild.review.drafts import Drafter, _import_test_shaping
from rebuild.review.families import assign_family
from rebuild.review.ink import (
    IDENTITY_DIFF,
    JUNIOR_VERIFICATION_METHOD,
    PICTURE_VERIFICATION_METHOD,
    VERIFICATION_METHOD,
    InkComparator,
    JuniorOracle,
    delta_digest,
    release_shape_memos,
    shape_memo_census,
    shaper_for,
    signature_digest,
)
from rebuild.review.enrich import (
    EXPLAIN_UNIT_BATCH_SIZE,
    LETTERS,
    EnrichedUnit,
    Enricher,
    SeamHomeUnit,
    is_seam_token,
    load_spec,
    notation,
    notation_tokens,
    resolve_home_assignments,
    seam_home_projection,
    text_entities,
)
from rebuild.tools import console, pile_tally
from rebuild.tools.cycle_timings import record_pool
from rebuild.tools.peak_rss import peak_rss_self_bytes, rss_token

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "rebuild" / "out" / "review"
STATIC_DIR = Path(__file__).resolve().parent / "static"

MANIFEST_FORMAT = "ams-review-manifest/2"
SHARD_PART_BYTES = 1 << 28
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
<p>This is the generator's placeholder page: the static app sources were not present under <code>rebuild/review/static/</code> when this directory was built. The data payload is complete — <a href="manifest.json">manifest.json</a> plus the unit shards under <code>units/</code>, and both fonts under <code>fonts/</code>.</p>
<p>Rebuild with <code>{build}</code>; serve with <code>{serve}</code>.</p>
</main>
</body>
</html>
"""


def _sha256(path: Path) -> str:
    return fingerprint.file_sha256(Path(path))


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
    """The manifest's machine_approved record: the totals across the three machine channels (ink-identical, picture-identical, and junior-equivalent), the audit rows those units cover, the per-class unit counts (classes with zero machine-approved units are omitted), and one sub-record per channel carrying its own counts and verification method one-liner. The junior channel also records which Junior font testified, since that font is an oracle input the fonts block doesn't cover (it is never rendered by the app)."""
    by_class: dict[str, int] = {}
    channels = {
        "ink_identical": {"units": 0, "rows": 0, "method": VERIFICATION_METHOD},
        "picture_identical": {"units": 0, "rows": 0, "method": PICTURE_VERIFICATION_METHOD},
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
        rows += unit.row_count
        channel = channels[next(name for name in MACHINE_CHANNELS if getattr(unit, name))]
        channel["units"] += 1
        channel["rows"] += unit.row_count
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
    "picture_identical",
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
    """Every fragment field the build re-derives from the workload on each pass — the order- and ledger-derived values plus the phase-1 machine flags carried on the unit. One definition serves both moments a fragment carries it: `unit_to_json` lays it down as the unit stands at drafting, and `patch_fragment` writes it over every fragment, fresh or served, as the shard that takes it is written, so no fragment can freeze a field a full build would have moved."""
    gate, note = config_badge(unit.configs, full_configs)
    return {
        "id": unit.unit_id,
        "batch": unit.batch,
        "ink_identical": unit.ink_identical,
        "picture_identical": unit.picture_identical,
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


def patch_fragment(
    fragment: dict, unit, seams: list[dict], seam_assign, full_configs=ACCEPTANCE_CONFIGS
) -> dict:
    """The one pass every fragment takes as its shard is written, whether it was drafted by this build's phase 1 or served out of the previous surface: re-stamp every scaffold field from the current workload and re-emit the secondary seams from the unit's rects — the projection's for a fresh unit, the store record's for a served one — under this build's home assignments. In-place key assignment keeps the fragment's key order, so the two kinds write the same bytes for the same unit. A fresh fragment is then stamped by `stamp_fragment`; a served one keeps the stamp it was located under."""
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


def stamp_fragment(fragment: dict) -> dict:
    """Write a fresh fragment's `content_key` over the placeholder it was drafted with, once `patch_fragment` has given it this build's scaffold: the key hashes the promoted class among the fragment's adjudicable fields, so it can only be taken after the patch. A served fragment is never re-stamped — the stamp it carries is the one the store record was checked against."""
    fragment["content_key"] = unit_cache.carry_content_hash(fragment)
    return fragment


def unit_to_json(enriched: EnrichedUnit, drafter: Drafter, full_configs=ACCEPTANCE_CONFIGS) -> dict:
    """The shard fragment for one enriched unit as phase 1 drafts it, at the moment the unit is enriched and while its batch's shapes are still in the memo: everything the enrichment and the drafter say, with the order- and ledger-derived fields carrying whatever the unit holds now — no batch, no echo, the pre-promotion class, every secondary seam homeless — and `content_key` None. Those are placeholders in the same sense a served fragment's are: `patch_fragment` writes over all of them when the fragment is written, and `stamp_fragment` writes the key last, so nothing the drafter or the enricher produces may depend on them (the drafter reads the window, its configs, the spans, seams and trace, and nothing else). A slim unit (`audit.slim_fragment`: machine-approved or verdict-exempt) never visits the drafter — whose pin draft replays a shaping per unit — and its fragment omits `SLIM_OMITTED_KEYS` outright, keys absent rather than null, because nothing under them reaches a reviewer; the enricher already rendered it no explain. Both of the fields that decide the shape are settled before this runs: the machine flags by the comparator and oracle in the same phase-1 step, the exemption by the ledger at load."""
    unit = enriched.unit
    drafts = None
    if not unit.slim_fragment:
        pin = drafter.draft_pin(enriched)
        policy = drafter.draft_policy(enriched)
        any_of = drafter.draft_any_of(enriched)
        drafts = {
            "pin": pin.to_json(),
            "policy": policy.to_json() if policy else None,
            "any_of": any_of.to_json(),
        }
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
        "drafts": drafts,
        "content_key": None,
    }
    if unit.slim_fragment:
        for key in SLIM_OMITTED_KEYS:
            del fragment[key]
    return fragment


def _copy_font(
    source: Path, out_dir: Path, name: str, family: str, repo_root: Path, expected_sha256: str
) -> dict:
    target = out_dir / "fonts" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    digest = _sha256(target)
    if digest != expected_sha256:
        raise SystemExit(
            f"{source} changed between this build's load ({expected_sha256}) and its copy ({digest}), "
            "so the units would describe a font other than the one shipped beside them; rebuild the surface"
        )
    try:
        rel = str(source.resolve().relative_to(repo_root))
    except ValueError:
        rel = str(source)
    return {
        "file": f"fonts/{name}",
        "family": family,
        "source": rel,
        "sha256": digest,
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


def refresh_assets(out_dir: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """Copy `rebuild/review/static/` over an already-built surface and restamp the manifest's `static` component alone — the whole of what an app JS/CSS/HTML edit moves, and the one surface input whose change cannot reach a single unit. Everything else the surface carries stays exactly as the build left it: `generated_at` and `repo_head`, every shard, both app sidecars, the per-unit index, and the unit-cache store. That is what keeps a live verdicting session whole — the autosave is keyed on `generated_at`, which does not move — and what keeps the sidecars and the store current, since `unit_index.manifest_sha256` projects this component out of the manifest's identity. `_check_output_files` runs afterwards as the executable proof of that second claim rather than a promise about it, and a surface that fails it gets its manifest put back: a restamped manifest over a surface whose sidecars do not describe it would read as fresh to the next cycle, which would then skip the build that is the only thing that can repair it."""
    out_dir = Path(out_dir)
    repo_root = Path(repo_root)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"no surface to refresh: {manifest_path} is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = manifest.get("inputs_fingerprint") if isinstance(manifest, dict) else None
    if not isinstance(recorded, dict):
        raise SystemExit(
            f"{manifest_path} records no inputs_fingerprint, so there is no component to restamp; "
            f"rebuild the surface with `{BUILD_COMMAND}`"
        )
    original = manifest_path.read_bytes()
    copied = copy_static(out_dir, repo_root / "rebuild" / "review" / "static")
    recorded["static"] = fingerprint.hash_paths(repo_root, fingerprint.static_paths(repo_root))
    _write_json(manifest_path, manifest)
    errors = _check_output_files(out_dir, manifest, repo_root)
    if errors:
        manifest_path.write_bytes(original)
        raise SystemExit(
            "contract check failed after the refresh, and the manifest is put back so the surface still "
            "reads as stale:\n" + "\n".join(f"  - {line}" for line in errors)
        )
    return copied


def _write_json(path: Path, payload) -> None:
    """Stream the payload into the file rather than materializing it, because `json.dumps(payload, indent=1, ensure_ascii=True) + "\\n"` handed to `write_text` stands two full-size copies up at once before a byte reaches disk — the concatenation never resizes the serialized str in place, so the copy and its source are both live, a high-water of twice the shard — and the biggest shards are hundreds of megabytes written at the very end of the build, where the surface-build parent's own peak already sits. A list goes out element by element, each serialized inside a one-element list whose framing is then peeled back off, so json's own C encoder lays the depth-1 indent down itself (on the pinned 3.14 it handles indent for any one-shot dumps) and the bytes are the one-shot dumps' bytes by construction; `JSONEncoder.iterencode` would stream more plainly but drops to the pure-Python generator whenever it is not one-shot, which multiplies the write time severalfold, where this peel adds about a sixth to it — a second or so across the whole units directory, against a surface-build step measured in minutes. Everything else the surface writes — the manifest, an empty shard — is a megabyte at most and goes through dumps untouched. Staging the bytes under a sibling name and renaming last is what streaming has to buy back, where serializing before opening anything gives it for free: nothing downstream guards against a half-written surface file, so a failed encode or a build killed mid-write has to leave the previous one intact rather than a truncated shard or an empty manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".partial")
    try:
        with staging.open("w", encoding="utf-8", newline="\n") as handle:
            if isinstance(payload, list) and payload:
                handle.write("[")
                for index, fragment in enumerate(payload):
                    if index:
                        handle.write(",")
                    handle.write(json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2])
                handle.write("\n]")
            else:
                handle.write(json.dumps(payload, indent=1, ensure_ascii=True))
            handle.write("\n")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


class _ShardWriter:
    """Streams classes into byte-capped shard parts, one fragment at a time, in the order the build hands them over: `open` a class, `add` each of its fragments and get back that fragment's (part index, byte start, byte length), `close` it for the relative paths the manifest lists in part order, and `commit` once every class is on disk. Nothing is held between calls but the open handle and the running byte count, which is what lets phase 2 stream into the shards instead of being assembled in the parent first.

    The cap exists for the browser: the app parses each part as one JSON string, and V8's `String::kMaxLength` under pointer compression is 2**29 - 24 bytes. Blink hands `JSON.parse` an empty string rather than an error when it cannot materialize a body that long, so an oversized shard surfaces as "Unexpected end of JSON input" from a fetch that looked like it succeeded. `SHARD_PART_BYTES` is half that ceiling, and the other half is headroom.

    A class that fits in one part keeps the bare `units/<class-id>.json` name, so the small classes, the checked-in fixtures, and the archived surfaces never churn. A class that does not is written as `units/<class-id>.000.json`, `units/<class-id>.001.json`, … — contiguous from zero, three digits, every part numbered, never a bare name beside numbered ones. Both spellings sort where `unit_index.class_shard_key` puts the class, because the character after the class id is `.` either way.

    The framing is `_write_json`'s, for the reasons its docstring gives: each fragment is serialized inside a one-element list whose framing is peeled back off, so a part's bytes are the one-shot `json.dumps(part, indent=1, ensure_ascii=True) + "\\n"` bytes by construction. Every part lands within the cap except one holding a single fragment that exceeds it alone, which nothing here can make smaller.

    That framing is a byte-addressing contract as well as a serialization one, and the spans returned here are what the review app's explain panel Range-fetches against — and what the next build reads its served units back through, since the unit store records each fragment's span as its address and `unit_cache.locate_prior_fragments` re-derives the same address off the written part for a record that lacks one: no punctuation is interleaved with a fragment's own bytes, and `ensure_ascii=True` under a utf-8 handle makes the running character count the byte offset, so `bytes[start:start + length]` is a standalone JSON element. A change to the `indent`, `ensure_ascii` or `separators` of the dump below breaks that silently — `rebuild/test_app_index.py` slices every fragment back out to catch it.

    Every part is staged under a sibling name and renamed only at `commit`, after the last class has closed, rather than as each class finishes. The build reads its served units out of the previous surface's shards by address while it writes this one, so a shard replaced class by class could put a unit's bytes under a new file before a later class asked for them; deferring the sweep keeps the previous surface whole until this one is entirely on disk, and it keeps what per-class renaming already gave: a failed encode or a build killed mid-write leaves the previous build's units in place rather than a truncated part, with `abort` sweeping the staging names away.
    """

    def __init__(self, out_dir: Path) -> None:
        self._units_dir = Path(out_dir) / "units"
        self._units_dir.mkdir(parents=True, exist_ok=True)
        self._pending: list[tuple[Path, Path]] = []
        self._class_id: str | None = None
        self._staged: list[Path] = []
        self._handle: TextIO | None = None
        self._size = 0

    def open(self, class_id: str) -> None:
        assert self._class_id is None, "close the open class first"
        self._class_id = class_id
        self._staged = []
        self._size = 0

    def add(self, fragment: dict) -> tuple[int, int, int]:
        body = json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2]
        if self._handle is not None and self._size + len(body) + len(",\n]\n") > SHARD_PART_BYTES:
            self._handle.write("\n]\n")
            self._handle.close()
            self._handle = None
        if self._handle is None:
            self._staged.append(self._units_dir / f"{self._class_id}.{len(self._staged):03d}.json.partial")
            self._handle = self._staged[-1].open("w", encoding="utf-8", newline="\n")
            self._handle.write("[")
            self._size = 1
        else:
            self._handle.write(",")
            self._size += 1
        span = (len(self._staged) - 1, self._size, len(body))
        self._handle.write(body)
        self._size += len(body)
        return span

    def close(self) -> list[str]:
        class_id = self._class_id
        assert class_id is not None, "no class is open"
        if self._handle is None:
            self._staged.append(self._units_dir / f"{class_id}.000.json.partial")
            self._handle = self._staged[-1].open("w", encoding="utf-8", newline="\n")
            self._handle.write("[]\n")
        else:
            self._handle.write("\n]\n")
        self._handle.close()
        self._handle = None
        names = (
            [f"{class_id}.json"]
            if len(self._staged) == 1
            else [f"{class_id}.{index:03d}.json" for index in range(len(self._staged))]
        )
        self._pending.extend(
            (staging, self._units_dir / name) for staging, name in zip(self._staged, names, strict=True)
        )
        self._class_id = None
        self._staged = []
        return [f"units/{name}" for name in names]

    def commit(self) -> None:
        for staging, target in self._pending:
            staging.replace(target)
        self._pending = []

    def abort(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        for staging in self._staged:
            staging.unlink(missing_ok=True)
        for staging, _target in self._pending:
            staging.unlink(missing_ok=True)
        self._staged = []
        self._pending = []
        self._class_id = None


def _write_shard(
    out_dir: Path, class_id: str, fragments: list[dict]
) -> tuple[list[str], list[tuple[int, int, int]]]:
    """One class's units a caller holds whole, written and renamed into place at once: `_ShardWriter` over the list, returning the relative paths the manifest lists in part order and, aligned index-for-index with `fragments`, each fragment's (part index, byte start, byte length). The table-diff build and the tests write their classes this way; the m1 build streams through the writer itself."""
    writer = _ShardWriter(out_dir)
    try:
        writer.open(class_id)
        spans = [writer.add(fragment) for fragment in fragments]
        parts = writer.close()
        writer.commit()
        return parts, spans
    finally:
        writer.abort()


FRESH_SPOOL_NAME = "fresh.spool.partial"


class _FragmentSpool:
    """Where one process's freshly drafted fragments wait between phase 1 and the write: a `_ShardWriter` over `<out_dir>/fresh.spool.partial`, one class named for the process that drafts into it (`serial`, or the pool's `w<index>`), so the parts carry the shard framing and each fragment's address reads back through `unit_cache.PriorFragmentReader` exactly as a served fragment's does out of the previous surface. That is the whole point of spooling rather than retaining: a fresh unit's `EnrichedUnit` dies the moment its fragment is on disk, and the write treats the two kinds of fragment alike — read by address, patched, released. `add` spools one fragment; `close` seals the parts and resolves every address to a `PriorFragment` carrying no stamp, since a fresh fragment is drafted with `content_key` None and stamped only once it is patched. The spool root is the runner's to sweep, on success and failure alike."""

    def __init__(self, out_dir: Path, name: str) -> None:
        self._writer = _ShardWriter(Path(out_dir) / FRESH_SPOOL_NAME)
        self._writer.open(name)
        self._spans: dict[str, tuple[int, int, int]] = {}

    def add(self, fragment: dict) -> None:
        self._spans[fragment["id"]] = self._writer.add(fragment)

    def close(self) -> dict[str, unit_cache.PriorFragment]:
        parts = self._writer.close()
        self._writer.commit()
        return {
            unit_id: unit_cache.PriorFragment(parts[part], start, length, unit_id, None)
            for unit_id, (part, start, length) in self._spans.items()
        }


def _prune_orphan_shards(out_dir: Path, manifest: dict) -> list[str]:
    """Delete units/*.json left over from ledger classes and shard parts the manifest no longer references. Runs only after the manifest is written, so a mid-build crash leaves the orphans in place rather than a manifest pointing at a deleted shard. Touches only *.json directly under units/ — subdirectories, non-JSON files, fonts, static assets, and manifest.json are never considered."""
    units_dir = Path(out_dir) / "units"
    if not units_dir.is_dir():
        return []
    keep = {Path(part).name for meta in manifest["classes"] for part in unit_index.class_shards(meta)}
    removed: list[str] = []
    for shard in units_dir.glob("*.json"):
        if shard.is_file() and shard.name not in keep:
            shard.unlink()
            removed.append(shard.name)
    return sorted(removed)


def _cluster_id_from_repr(configs, class_id, diffs_repr: bytes) -> str:
    """`_cluster_id` over the ink diffs' repr as bytes, fed to the hash in three pieces — the head of the tuple, the diffs, the closing parenthesis — so the composed key never exists as one string and the diffs' bytes can be dropped the moment they have been hashed. What is hashed is byte-for-byte `repr((tuple(configs), class_id, diffs))` — CPython renders a 3-tuple as exactly this join — which `rebuild/test_unit_cache.py::test_cluster_id_from_repr_matches_the_tuple_recipe` pins against the tuple form."""
    key = hashlib.sha1(f"({tuple(configs)!r}, {class_id!r}, ".encode())
    key.update(diffs_repr)
    key.update(b")")
    return "c-" + key.hexdigest()[:8]


def _cluster_id(configs, class_id, diffs) -> str:
    """The blank-queue cluster signature the in-app docket view groups by: the echo key minus the judged pair, so every echo group nests inside exactly one cluster. The repr recipe must stay byte-compatible with rebuild/tools/review_docket.py's historical ids so recorded c- references keep resolving."""
    return _cluster_id_from_repr(configs, class_id, repr(diffs).encode())


@dataclass(frozen=True, slots=True)
class _UnitProjection:
    """The slim, picklable phase-1 result a surface worker returns per unit: everything the parent's serial reduces read plus everything the unit cache persists, and never the EnrichedUnit, which no process keeps past the batch that made it — its fragment is drafted and spooled in the same step, and the address comes back beside the projections. The ink diffs travel as two digests over their repr and nothing else — `diffs_digest` is the echo key's diff component, and `cluster` is the blank-queue cluster signature, computed here rather than in the parent because everything it keys on is known the moment the family is assigned: the configs, the diffs, and the unit's final class, which is the verdict family for an UNMATCHED unit and the ledger class otherwise, exactly what the parent's family promotion writes onto the unit. It is computed for every unit, machine-approved ones included, because the store carries it forward: a served unit can cross into the human workload on a ledger edit alone (no_verdict flipping), and its cluster must already exist. So the parent holds a short id per unit where it once held the diffs' repr — a string as long as the diffs themselves — for the whole units phase."""

    unit_id: str
    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    ink_deltas: tuple[tuple[str, str], ...]
    diffs_digest: str
    cluster: str
    family: str
    pair_codepoints: tuple[int, int] | None
    seam_home: SeamHomeUnit
    seam_rects: tuple[tuple[tuple[int, int], dict, dict], ...]
    mismatches: tuple[str, ...]


def _phase1_unit(
    unit, comparator, oracle, enricher, drafter: Drafter, report=None
) -> tuple[_UnitProjection, dict]:
    """One unit's whole per-unit work: the ink flags and deltas, the enrichment, and the fragment drafted from it (`unit_to_json`) — returned as the slim projection the parent's reduces read and the fragment itself, which the caller spools or, for a verification sample, patches in hand. The EnrichedUnit is local to this call: nothing downstream needs it once the fragment exists, and drafting here rather than after the parent's reduces is what keeps the batch's shapes in the memo for the drafter's replay."""
    text = "".join(chr(value) for value in unit.codepoint_values)
    diffs = tuple(comparator.config_diff(text, config) for config in unit.configs)
    unit.ink_identical = all(diff == IDENTITY_DIFF for diff in diffs)
    unit.picture_identical = not unit.ink_identical and comparator.picture_identical(text, unit.configs)
    unit.junior_equivalent = not (unit.ink_identical or unit.picture_identical) and oracle.approves(
        unit.configs, text
    )
    unit.ink_deltas = {
        config: delta_digest(diff) for config, diff in zip(unit.configs, diffs) if diff != IDENTITY_DIFF
    }
    mismatch_mark = len(enricher.mismatches)
    enriched = enricher.enrich(unit, report)
    family = assign_family(enriched) if unit.class_id == UNMATCHED_CLASS else ""
    diffs_repr = repr(diffs).encode()
    projection = _UnitProjection(
        unit_id=unit.unit_id,
        ink_identical=unit.ink_identical,
        picture_identical=unit.picture_identical,
        junior_equivalent=unit.junior_equivalent,
        ink_deltas=tuple(unit.ink_deltas.items()),
        diffs_digest=hashlib.sha1(diffs_repr).hexdigest(),
        cluster=_cluster_id_from_repr(
            unit.configs, family if unit.class_id == UNMATCHED_CLASS else unit.class_id, diffs_repr
        ),
        family=family,
        pair_codepoints=enriched.pair_codepoints,
        seam_home=seam_home_projection(enriched),
        seam_rects=tuple(
            (seam.pair, seam.highlight_before, seam.highlight_after) for seam in enriched.secondary_seams
        ),
        mismatches=tuple(enricher.mismatches[mismatch_mark:]),
    )
    return projection, unit_to_json(enriched, drafter)


def _seam_records(seam_rects) -> list[dict]:
    """A projection's secondary-seam rects in the shape `patch_fragment` reads — the shape the store persists them in, so a fresh unit and a served one patch through one code path."""
    return [{"pair": list(pair), "before": before, "after": after} for pair, before, after in seam_rects]


def _recompute_fragment(
    unit, injection, comparator, oracle, enricher, drafter: Drafter
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """One sampled served unit recomputed from nothing and carried through the same patch the write gives a fresh fragment, with the parent's global fields — batch, echo, cluster, promoted class, seam homes — injected onto the unit copy first, since the copy was taken before the reduces ran. Answers with the content key the recomputation stamps and the ink deltas it found, which is what the caller holds against what the cache served."""
    projection, fragment = _phase1_unit(unit, comparator, oracle, enricher, drafter, None)
    unit.batch, unit.echo, unit.cluster, unit.class_id, seam_assign = injection
    stamp_fragment(patch_fragment(fragment, unit, _seam_records(projection.seam_rects), seam_assign))
    return fragment["content_key"], projection.ink_deltas


def _phase1_batches(enricher: Enricher, units):
    """Phase 1's unit batches, released as each one closes. The enricher's settlement batches (`Enricher.explain_unit_batches`) are the build's unit batch boundary, and the shared shape memo (`ink.release_shape_memos`) is released behind every one of them, so what the comparator, the oracle and the enricher share across a batch — each (text, config) shaped once for the three of them — is what a process holds at any moment, rather than everything its slice ever shaped. The pool worker and the in-process runner both iterate this rather than the enricher's batches directly, which is what makes the bound a fact about the build and not about one of its paths."""
    for unit_batch, reports in enricher.explain_unit_batches(units):
        yield unit_batch, reports
        release_shape_memos()


def _released_batches(items):
    """The same boundary for the one loop that settles nothing and so has no enricher batch to ride: the verification sample, which recomputes each served unit's phase 1 and patch in hand. Chunked at the enricher's own batch width so the memo has one bound across the whole build."""
    for chunk in batched(items, EXPLAIN_UNIT_BATCH_SIZE):
        yield chunk
        release_shape_memos()


VERIFICATION_SAMPLE = 200


def _verification_sample(served: list[str], seed: str, size: int = VERIFICATION_SAMPLE) -> list[str]:
    """Which cache-served units this build recomputes from nothing and holds against what it served. Deterministic in the inputs — the seed is the store's own environment stamp, a digest of them — so a failure reproduces on a rerun of the same build rather than depending on which units a random draw happened to reach. Sampling is what makes the check continuous rather than periodic: the guarantee a from-scratch comparison run once a cycle gives you all at once, this gives you a couple of hundred windows at a time, on every build, at a cost in the tenths of a second."""
    if not served:
        return []
    return random.Random(seed).sample(served, min(size, len(served)))


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
    """The ink-duplicate merge's signature digests, one per row of `signature_rows`, served from the persisted store where the content key still holds and shaped live for the remainder — across a spawn pool when the miss pile is deep enough to amortize its startup, else serially through the parent's shared shapers, whose memo is released once the pass is done so the parent carries no shape from it into the units phase. Returns the digests keyed (codepoints, config), the store records to persist after the build, the store's environment stamp, and the count actually shaped."""
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
            release_shape_memos()
        for row, digest in zip(misses, digests):
            signatures[(row.codepoints, row.config)] = digest
            entries[keys[(row.codepoints, row.config)]] = digest
    return signatures, entries, environment, len(misses)


def _surface_worker(conn, init: dict) -> None:
    """A persistent, stateful surface worker (spawn-only: uharfbuzz/fontTools C objects are not fork-safe, and drafts._import_test_shaping mutates a module-global singleton). `phase1` computes config_diff + enrich + draft over its slice, batch by batch with the shared shape memo released behind each (`_phase1_batches`), spooling every fragment to the build's fresh spool as it is drafted (`_FragmentSpool`, under the class name the message carries) so that no EnrichedUnit outlives its batch here, and answers with the slim projections and each fragment's spool address; the parent reads the fragments back by address itself as it writes the shards, so nothing is held in this process for it to pull and no phase-2 message exists. The other message, `verify`, recomputes phase 1 and the patch for a handful of units the cache served — units this worker never enriched — and answers with each one's content key and its freshly computed ink deltas, which is what makes the served fragments continuously checkable against a fresh computation of the same window.

    `phase1` answers with a running count as each batch lands, ahead of the one `ok` that ends the phase, which is what lets the parent say how far through the corpus the pool is while it is still working rather than only once a worker has finished. `verify` sends none: it is a couple of hundred units against tens of thousands, and a counter nobody would read costs a message per unit.
    """
    try:
        comparator = InkComparator(init["before_font"], init["after_font"], shaper_for)
        oracle = JuniorOracle(init["junior_font"], init["before_font"], init["after_font"], shaper_for)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spec = load_spec(init["spec_root"])
        enricher = Enricher(
            spec,
            init["subset_dir"],
            init["after_font"],
            repo_root=init["repo_root"],
            before_font=init["before_font"],
            shaper_factory=shaper_for,
        )
        drafter = Drafter(init["after_font"], repo_root=init["repo_root"], shaper_factory=shaper_for)
        tally = pile_tally.from_environment()
        if tally:
            tally.hold("worker.subset_rows", enricher._subset_rows, nested=True)
            tally.hold_reading("ink.shape_memo", shape_memo_census)
        while True:
            message = conn.recv()
            if message[0] == "stop":
                conn.send(("peak", peak_rss_self_bytes()))
                return
            if message[0] == "phase1":
                results: list[_UnitProjection] = []
                spool = _FragmentSpool(init["out_dir"], message[2]) if message[1] else None
                for unit_batch, reports in _phase1_batches(enricher, message[1]):
                    for unit, report in zip(unit_batch, reports):
                        projection, fragment = _phase1_unit(
                            unit, comparator, oracle, enricher, drafter, report
                        )
                        assert spool is not None
                        spool.add(fragment)
                        results.append(projection)
                    conn.send(("progress", len(results)))
                spooled = spool.close() if spool is not None else {}
                if tally:
                    tally.hold("worker.projections", results)
                    tally.hold("worker.spooled", spooled)
                    tally.boundary(f"{message[2]}/phase1")
                conn.send(("ok", results, spooled))
                if tally:
                    tally.release("worker.projections")
                    tally.release("worker.spooled")
            elif message[0] == "verify":
                keys: dict[str, tuple] = {}
                for chunk in _released_batches(message[1]):
                    for unit, injection in chunk:
                        keys[unit.unit_id] = _recompute_fragment(
                            unit, injection, comparator, oracle, enricher, drafter
                        )
                conn.send(("ok", keys))
    except Exception:
        try:
            conn.send(("error", traceback.format_exc()))
        except Exception:
            pass
    finally:
        conn.close()


def _record_surface_pool(width: int, peaks: dict[str, int]) -> None:
    """File one kind:"pool" record for a finished surface pool, so `make job-costs` can hold SURFACE_WORKER_BYTES against workers that actually ran. It is the same record an xdist controller writes — cycle_timings.record_pool is deliberately not a pytest-only entry point — under a unit name of this pool's own, and it never raises: a journal that cannot be written is not a reason for a surface build to fail. A build with no pool files nothing, because at width one there is no worker to price; the parent's own figure is the `surface-build` step peak the cycle already stamps."""
    if not peaks:
        return
    record_pool("surface", width=width, worker_peaks=peaks, controller_peak_bytes=peak_rss_self_bytes())


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


PHASE1_UNITS = "units enriched"


def _phase_timing(label: str, started: float, note: str = "") -> None:
    """Close one `review.build` phase on the `[t]` line the timings journal reads, stamped with this process's peak RSS so far (`peak_rss.rss_token`, the same token run_m1's phase lines carry) ahead of whatever note the phase hangs off the line. `make cycle-timings ARGS='--inner'` renders the token per phase, which is what says where in a build the step's high-water mark is reached — a peak only ever rises, so the phase whose token first shows the step's figure is the phase that made it."""
    tail = rss_token(peak_rss_self_bytes())
    if note:
        tail += f"\t{note}"
    console.timing(label, time.perf_counter() - started, tail, file=sys.stderr)


class _FreshRunner:
    """Phase 1 over the units the cache could not serve — in-process when `jobs` is 1, across persistent spawn workers otherwise, with identical per-unit semantics either way, which is what lets the serial and parallel builds share every reduce and stay byte-identical. The parent keeps the frozen ids/triage order and every order-sensitive reduce (batches, family promotion, echo numbering, secondary-home resolution); the runner enriches and drafts, spooling each fragment to disk as it is drafted (`_FragmentSpool`, under `out_dir`) so that no EnrichedUnit outlives the batch that produced it in either path, and hands the fragments back one at a time through `fragment`, read by address out of the spool exactly as a served fragment is read out of the previous surface. The spool is swept at `close`, whichever way the build ends."""

    def __init__(
        self,
        fresh: list,
        jobs: int,
        subset_dir: Path,
        before_font: Path,
        after_font: Path,
        junior_font: Path,
        repo_root: Path,
        verify: list | None = None,
        spec_root: Path | None = None,
        *,
        out_dir: Path,
    ) -> None:
        self._fresh = fresh
        self._verify = list(verify or ())
        self._before_font = before_font
        self._after_font = after_font
        self._junior_font = junior_font
        self._subset_dir = subset_dir
        self._repo_root = repo_root
        self._spec_root = Path(spec_root) if spec_root is not None else Path(repo_root)
        self._out_dir = Path(out_dir)
        # A spool a killed build left behind is litter under the served surface; this build's own parts replace it either way, but sweeping first keeps the directory to what this build wrote.
        shutil.rmtree(self._out_dir / FRESH_SPOOL_NAME, ignore_errors=True)
        self._spooled: dict[str, unit_cache.PriorFragment] = {}
        self._reader: unit_cache.PriorFragmentReader | None = None
        self._local: tuple | None = None
        self._procs: list = []
        self._conns: list = []
        self._slices: list[list] = []
        # The verification sample is worker work too, and it is the whole of the work when the cache served every unit: a pool sized on the fresh pile alone leaves a no-change rebuild recomputing its sample in the parent, which is both slower (200 units serially against eight workers' worth of them: measured 55.6 s against 42.4 s for the units phase of a fully-served build) and much heavier, since the parent that already holds every served fragment then builds an enricher and its per-config subset tables on top (18.9 GB peak against 8.8 GB, where a worker's copy would have been its own process's).
        workload_size = max(len(fresh), len(self._verify))
        if jobs > 1 and workload_size > 1:
            nworkers = min(jobs, workload_size)
            self._slices = _partition(fresh, nworkers)
            init = {
                "before_font": before_font,
                "after_font": after_font,
                "junior_font": junior_font,
                "subset_dir": subset_dir,
                "repo_root": repo_root,
                "spec_root": self._spec_root,
                "out_dir": self._out_dir,
            }
            ctx = multiprocessing.get_context("spawn")
            for index in range(nworkers):
                parent_conn, child_conn = ctx.Pipe()
                proc = ctx.Process(target=_surface_worker, args=(child_conn, init))
                proc.start()
                child_conn.close()
                self._procs.append(proc)
                self._conns.append(parent_conn)

    def phase1(self) -> dict[str, _UnitProjection]:
        """Enrich and draft every fresh unit, returning the projections the parent's reduces read and keeping each fragment's spool address for `fragment`. Pooled, each worker spools its own slice under its own class name and answers with the addresses beside its projections; serial, the same loop runs here over one spool, at the enricher's batch width with the memo released behind each batch, and either way the EnrichedUnit is gone by the time its batch closes."""
        projections: dict[str, _UnitProjection] = {}
        if self._conns:
            for index, (conn, chunk) in enumerate(zip(self._conns, self._slices)):
                conn.send(("phase1", chunk, f"w{index}"))
            for results, spooled in self._collect("phase 1"):
                for projection in results:
                    projections[projection.unit_id] = projection
                self._spooled.update(spooled)
        elif self._fresh:
            comparator, oracle, enricher, drafter = self._in_process()
            spool = _FragmentSpool(self._out_dir, "serial")
            for unit_batch, reports in _phase1_batches(enricher, self._fresh):
                for unit, report in zip(unit_batch, reports):
                    projection, fragment = _phase1_unit(unit, comparator, oracle, enricher, drafter, report)
                    spool.add(fragment)
                    projections[projection.unit_id] = projection
                self._count(len(projections))
            self._spooled = spool.close()
        return projections

    def fragment(self, unit_id: str) -> dict:
        """One fresh unit's fragment, read back out of the spool by the address phase 1 recorded for it — the same read, through the same reader, that serves a prior fragment out of the previous surface, so the write asks for fresh and served fragments alike in whatever order the shards take them and holds one at a time. What comes back is the fragment as it was drafted, placeholders and all; the caller patches and stamps it."""
        if self._reader is None:
            self._reader = unit_cache.PriorFragmentReader(self._out_dir / FRESH_SPOOL_NAME)
        return self._reader.read(self._spooled[unit_id])

    def _count(self, done: int) -> None:
        console.progress(done, len(self._fresh), PHASE1_UNITS, file=sys.stderr)

    def hold_piles(self, tally: pile_tally.PileTally) -> None:
        """Hand the debug tally the piles this runner holds in the parent: the spool address kept per fresh unit from phase 1 until `close` sweeps the spool — pooled, the addresses every worker answered with; serial, the one spool's — and, once the serial path has built its enricher, that enricher's projected subset tables. Pooled, the tables live in the workers, which tally their own at their own phase ends; the parent's hold then reads as empty, which is the honest reading rather than a gap."""
        tally.hold("runner.spooled", self._spooled)
        tally.hold("runner.subset_rows", self._local[2]._subset_rows if self._local else {}, nested=True)

    def _collect(self, label: str) -> list:
        """Every worker's answer to one phase, read as it arrives rather than one worker at a time — which is what lets a counter reach the terminal while the phase is still running, since a parent blocked on `recv` in submission order says nothing until its first worker has finished. `wait` hands back whichever connections have something; a `progress` tag replaces that worker's share of the count and reprints the sum, and the phase is over once every connection has answered with the payload after its `ok`. An error raises here exactly as it did when the parent recv'd in turn, and the replies queued behind it are drained by `close()`."""
        share = dict.fromkeys(self._conns, 0)
        waiting = list(self._conns)
        answered: list = []
        while waiting:
            for conn in cast(list, multiprocessing.connection.wait(waiting)):
                reply = conn.recv()
                if reply[0] == "progress":
                    share[conn] = reply[1]
                    self._count(sum(share.values()))
                    continue
                if reply[0] == "error":
                    raise RuntimeError(f"surface worker failed in {label}:\n" + reply[1])
                answered.append(reply[1:])
                waiting.remove(conn)
        return answered

    def _in_process(self) -> tuple:
        """The comparator, oracle, enricher, and drafter the serial path works through, built once and on first use. Lazy because the verification sample can be the only work there is — a rebuild the cache served whole still recomputes its sample, and it must not pay for these until it does."""
        if self._local is None:
            comparator = InkComparator(self._before_font, self._after_font, shaper_for)
            oracle = JuniorOracle(self._junior_font, self._before_font, self._after_font, shaper_for)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                spec = load_spec(self._spec_root)
            enricher = Enricher(
                spec,
                self._subset_dir,
                self._after_font,
                repo_root=self._repo_root,
                before_font=self._before_font,
                shaper_factory=shaper_for,
            )
            drafter = Drafter(self._after_font, repo_root=self._repo_root, shaper_factory=shaper_for)
            self._local = (comparator, oracle, enricher, drafter)
        return self._local

    def verify(self, injections: dict[str, tuple]) -> dict[str, tuple[str, tuple[tuple[str, str], ...]]]:
        """Recompute phase 1 and the patch for the sampled units the cache served, and answer with each one's content key beside the ink deltas the same recomputation produced. The units are recomputed from nothing — a fresh explain, a fresh config_diff, a fresh enrichment, fresh drafts — so what comes back is what this build would have written had the unit missed the cache, and the caller holds both halves against what the cache served: the key against the stamp on the served fragment, the deltas against the store record they were served from, since `ink_deltas` sits outside the key's projection."""
        keys: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {}
        if not self._verify:
            return keys
        if self._conns:
            for index, conn in enumerate(self._conns):
                conn.send(
                    (
                        "verify",
                        [
                            (unit, injections[unit.unit_id])
                            for unit in self._verify[index :: len(self._conns)]
                        ],
                    )
                )
            for conn in self._conns:
                reply = conn.recv()
                if reply[0] == "error":
                    raise RuntimeError("surface worker failed while verifying a served unit:\n" + reply[1])
                keys.update(reply[1])
        else:
            comparator, oracle, enricher, drafter = self._in_process()
            for chunk in _released_batches(self._verify):
                for unit in chunk:
                    keys[unit.unit_id] = _recompute_fragment(
                        unit, injections[unit.unit_id], comparator, oracle, enricher, drafter
                    )
        return keys

    def close(self) -> None:
        """Stop every worker, collect the peak each one answers with, join the processes, and sweep the fresh spool — reached from a `finally`, so the path that matters most is the failing one. A phase raises from inside its own recv loop, which leaves the conns after the failing one still holding that phase's `("ok", …)` reply, so the shutdown reply carries its own `peak` tag and this drains whatever is queued ahead of it: reading a phase's payload as a peak would raise out of the `finally`, displace the worker's traceback, and abandon the join below with spawn workers still running. The spool goes last, after every reader and worker that could hold one of its parts open is done with it."""
        peaks: dict[str, int] = {}
        for index, conn in enumerate(self._conns):
            try:
                conn.send(("stop",))
                while conn.poll(5):
                    reply = conn.recv()
                    if reply[0] == "peak":
                        peaks[f"w{index}"] = int(reply[1])
                        break
            except OSError, EOFError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        for proc in self._procs:
            proc.join(timeout=5)
            if proc.is_alive():
                proc.terminate()
        _record_surface_pool(len(self._procs), peaks)
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        shutil.rmtree(self._out_dir / FRESH_SPOOL_NAME, ignore_errors=True)
        self._spooled.clear()


class _SidecarSpool:
    """The three sidecars' lines, spooled to disk as the shards stream out and replayed into the stamped files once the manifest exists. Each sidecar carries the manifest's identity in its header, and the manifest cannot be written until every class's part list is known, so a build that no longer holds its fragments has to keep the projected lines somewhere until then: on disk under `.partial` names beside the files they become, which is bounded by the projection's own size and never by the corpus in the parent. `finish` writes the real files through the same writers `write_index` and `write_app_artifacts` reach, so the bytes are the ones a build holding every fragment would have written; `discard` sweeps the spools whether or not it did."""

    def __init__(self, out_dir: Path) -> None:
        self._paths = {
            name: Path(out_dir) / f"{name}.spool.partial"
            for name in (unit_index.INDEX_NAME, app_index.APP_INDEX_NAME, app_index.LOCATOR_NAME)
        }
        self._handles = {name: path.open("wb") for name, path in self._paths.items()}
        self.human = 0
        self.machine = 0

    def unit(self, fragment: dict, span: tuple[int, int, int]) -> None:
        self._handles[unit_index.INDEX_NAME].write(unit_index.index_line(fragment))
        if fragment.get("batch") is None:
            self._handles[app_index.LOCATOR_NAME].write(app_index.locator_line(fragment, *span))
            self.machine += 1
        else:
            self._handles[app_index.APP_INDEX_NAME].write(app_index.app_line(fragment, *span))
            self.human += 1

    def _lines(self, name: str) -> Iterator[bytes]:
        with self._paths[name].open("rb") as handle:
            yield from handle

    def finish(self, out_dir: Path) -> None:
        for handle in self._handles.values():
            handle.close()
        unit_index.write_index_lines(out_dir, self._lines(unit_index.INDEX_NAME))
        app_index.write_app_artifacts_lines(
            out_dir,
            self._lines(app_index.APP_INDEX_NAME),
            self._lines(app_index.LOCATOR_NAME),
            human=self.human,
            machine=self.machine,
        )

    def discard(self) -> None:
        for handle in self._handles.values():
            handle.close()
        for path in self._paths.values():
            path.unlink(missing_ok=True)


@dataclass(frozen=True)
class _WrittenSurface:
    """What `_write_surface` hands back beside the manifest: the three per-unit values the build still reads after the fragments are gone — each unit's `config_note`, which the census facts histogram, and its `content_key` and shard address, which the unit store records so the next build's plan can serve the fragment without walking the shard to find it. An address is the writer's own span resolved to the part name the manifest lists, one tuple per unit over a part name shared by every unit in the part."""

    manifest: dict
    config_notes: dict[str, str | None]
    content_keys: dict[str, str]
    addresses: dict[str, tuple[str, int, int]]


def _write_surface(
    out_dir: Path,
    workload,
    classes: list,
    by_class: dict,
    fragments: Callable[[list[Unit]], Iterator[dict]],
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
    font_digests: Mapping[str, str],
    served_ids: Collection[str] = (),
    tally: pile_tally.PileTally | None = None,
) -> _WrittenSurface:
    """Stream the per-unit JSON fragments into shards (per class, triage order within each), copy fonts, and write the manifest with its parent-once `generated_at`/`repo_head` stamps. `fragments` is asked once, for every unit in the order the shards will take them — classes in `unit_index.class_shard_key` order, which is the order the sidecars are written in anyway, and each class's units in triage order — and each fragment it yields is written, checked, projected onto the sidecar spools and released before the next is pulled, so the parent holds one fragment at a time rather than every unit's from the moment they exist until the manifest. What survives a fragment is slim: its shard address and the checker's per-unit identity for the cross-unit predicates, its sidecar lines on disk, and the two values `_WrittenSurface` carries. `check_shards`' predicates run over the fragments as they go by, through the same `_SurfaceCheck` the whole-surface form feeds, and `served_ids` carries the cache's plan into it (see `check_shards`). The manifest-shape predicates (`check_manifest`) and the beside-the-manifest file predicates (`_check_output_files`) do not run per build: every field they read is written right here out of this function's own inputs, and the fonts are held instead by the digest taken at load and asserted at `_copy_font`. `check_output_dir` proves them over a real build once per contracts run — `rebuild/test_app_index.py` over the mini bundle, `rebuild/test_review_build.py` over a table diff — and `refresh_assets` still runs the file predicates over the surface it restamps."""
    ordered = sorted(classes, key=lambda entry: unit_index.class_shard_key(entry.id))
    stream = fragments([unit for entry in ordered for unit in by_class[entry.id]])
    meta_by_id: dict[str, dict] = {}
    config_notes: dict[str, str | None] = {}
    content_keys: dict[str, str] = {}
    addresses: dict[str, tuple[str, int, int]] = {}
    check = _SurfaceCheck(
        mode="m1-audit",
        descriptions=FEATURE_DESCRIPTIONS,
        batch_size=batch_size,
        repo_root=repo_root,
        served_ids=served_ids,
    )
    if tally:
        tally.hold("checker.identity", check._identity)
        tally.hold("written.config_notes", config_notes)
        tally.hold("written.content_keys", content_keys)
        tally.hold("written.addresses", addresses)
    writer = _ShardWriter(out_dir)
    spool = _SidecarSpool(out_dir)
    try:
        for entry in ordered:
            units = by_class[entry.id]
            meta = {
                "id": entry.id,
                "status": entry.status,
                "ink_identical": entry.ink_identical,
                "no_verdict": entry.no_verdict,
                "why": entry.why,
                "unit_count": len(units),
                "row_count": sum(unit.row_count for unit in units),
                "machine_approved_count": sum(1 for unit in units if unit.machine_approved),
                # The app draws a class's machine fold — its count and its badge — before opening it, and under the slim app index those units are not resident to be counted. So the split the badge cascades over is recorded here rather than re-derived from records the tab no longer holds.
                "machine_channels": {
                    channel: sum(1 for unit in units if getattr(unit, channel))
                    for channel in MACHINE_CHANNELS
                },
                "shards": [],
                "batches": sorted({unit.batch for unit in units if unit.batch is not None}),
            }
            check.class_start(meta)
            writer.open(entry.id)
            spans: list[tuple[int, int, int]] = []
            for unit in units:
                fragment = next(stream)
                assert fragment["id"] == unit.unit_id, (fragment["id"], unit.unit_id)
                span = writer.add(fragment)
                check.unit(fragment)
                spool.unit(fragment, span)
                spans.append(span)
                config_notes[unit.unit_id] = fragment["config_note"]
                content_keys[unit.unit_id] = fragment["content_key"]
            meta["shards"] = writer.close()
            for unit, (part, start, length) in zip(units, spans, strict=True):
                addresses[unit.unit_id] = (meta["shards"][part], start, length)
            check.class_end()
            meta_by_id[entry.id] = meta
        assert next(stream, None) is None, "fragments yielded more units than the classes hold"
        writer.commit()

        fonts = {
            "before": _copy_font(
                before_font, out_dir, "before.otf", "AMS Review Before", repo_root, font_digests["before"]
            ),
            "after": _copy_font(
                after_font, out_dir, "after.otf", "AMS Review After", repo_root, font_digests["after"]
            ),
        }
        machine_units = [unit for unit in workload.units if unit.machine_approved]
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
            "classes": [meta_by_id[entry.id] for entry in classes],
            "build_command": BUILD_COMMAND,
            "serve_command": SERVE_COMMAND,
        }
        _write_json(out_dir / "manifest.json", manifest)
        pruned = _prune_orphan_shards(out_dir, manifest)
        if pruned:
            print(f"Pruned {len(pruned)} orphan shard(s): {', '.join(pruned)}", file=sys.stderr)
        copy_static(out_dir, static_dir)
        spool.finish(out_dir)
    finally:
        writer.abort()
        spool.discard()
    # A unit whose re-settled cells disagree with the audit it was built from is a surface describing a font nobody compiled, which is the one thing this directory exists not to be. The divergence is empty today, and a build that makes it non-empty stops rather than ships.
    errors: list[str] = []
    if mismatches:
        errors.append(
            f"enricher: re-settled cells diverge from the audit in {len(mismatches)} units "
            f"(first: {mismatches[0]})"
        )
    errors.extend(check.finish(manifest))
    if errors:
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:20]))
    return _WrittenSurface(manifest, config_notes, content_keys, addresses)


@dataclass(slots=True)
class _UnitState:
    """One unit's phase-1 products in the parent, served from the cache or returned by the runner, in the one shape the global reduces and the store writer read. Every string in it that repeats across units — the digests, the cluster, the family, the config names and delta digests — is interned into the one `sys.intern` table `audit.load_audit` describes, and the seam-home projection's tuples are pooled through `_pooled_seam_home`, because a pooled worker's reply and the store's JSON alike hand the parent a fresh copy of every name per unit, and this record is what the parent holds per unit for the rest of the build. The unit's own `ink_deltas` is this record's dict rather than a copy of it: nothing writes to it once it is here."""

    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    ink_deltas: dict[str, str]
    diffs_digest: str
    cluster: str
    family: str
    pair_codepoints: tuple[int, int] | None
    seam_home: SeamHomeUnit
    seam_rects: list[dict]
    mismatches: list[str]


def _pooled_seam_home(seam_home: SeamHomeUnit, pool: dict) -> SeamHomeUnit:
    """`seam_home` with every tuple it carries pooled to one instance per distinct value and every name inside them interned. The secondary-home reduce reads these for every unit of the corpus, so the parent holds one per unit — and their values are drawn from a small vocabulary: a handful of span layouts, a few hundred glyph and cell names, five seam tokens, the alphabet's codepoints. The codepoint values are pooled as ints and not as a window tuple, since a window repeats only across the siblings of one window while its letters repeat across the corpus. `pool` is the caller's and lives exactly as long as the ingestion that fills it."""

    def pooled(value):
        return pool.setdefault(value, value)

    def names(values: tuple[str, ...]) -> tuple[str, ...]:
        return pooled(tuple(sys.intern(value) for value in values))

    def pairs(values: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
        return pooled(tuple(pooled(value) for value in values))

    return replace(
        seam_home,
        codepoint_values=tuple(pooled(value) for value in seam_home.codepoint_values),
        pair=pooled(seam_home.pair) if seam_home.pair else None,
        after_spans=pairs(seam_home.after_spans),
        after_cells=names(seam_home.after_cells),
        after_seams=names(seam_home.after_seams),
        before_spans=pairs(seam_home.before_spans),
        before_glyphs=names(seam_home.before_glyphs),
        before_seams=names(seam_home.before_seams),
        seam_pairs=pairs(seam_home.seam_pairs),
    )


def _slim_for(unit: Unit, cached: unit_cache.CachedUnit) -> bool:
    """Whether this build would write the unit slim, answered before phase 1 runs from what the store already knows: the machine flags are the record's — pure functions of the fonts and the window, everything under the key, so the store's answer is this build's answer — and the exemption is this build's ledger's, which is the one input that can flip under a key-stable unit. Held against the record's own `slim` flag to decide whether the fragment it names is servable at all."""
    return cached.ink_identical or cached.picture_identical or cached.junior_equivalent or unit.no_verdict


def _cached_seam_home(unit, cached: unit_cache.CachedUnit) -> SeamHomeUnit:
    proj = cached.proj
    return SeamHomeUnit(
        unit_id=unit.unit_id,
        codepoint_values=unit.codepoint_values,
        ink_identical=cached.ink_identical,
        picture_identical=cached.picture_identical,
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
    spec_root: Path | None = None,
) -> dict:
    # The spec is the one input a frozen workload cannot carry in its tables: the enricher re-settles every window from it, so a bundle of audit rows, subsets and a font describes a rebuild that only still happens while the runes agree with them. `spec_root` lets such a bundle name its own frozen copy (the objects rebuild/review/fixtures/mini/pin.json names, materialized out of git by the rebuild suite's mini_bundle fixture) and stay hermetic across rune edits; everything else — the fingerprints, the git head, the relative paths in the manifest, the corpus pins — stays on `repo_root`, because those are facts about this checkout rather than about the workload.
    spec_root = Path(spec_root) if spec_root is not None else Path(repo_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    missing_subsets = [
        config
        for config in ACCEPTANCE_CONFIGS
        if not (subset_dir / f"baseline-{config}.subset.tsv.gz").is_file()
        or (subset_dir / f"baseline-{config}.subset.tsv.gz").stat().st_size == 0
    ]
    # The enricher reads these lazily, one config at a time, deep inside the units phase — where a missing table would surface as a per-unit ValueError several hundred seconds in. Every acceptance config needs one, and the cheapest moment to say so is before any of it starts.
    if missing_subsets:
        raise SystemExit(
            f"missing or empty baseline subset tables under {subset_dir}: {', '.join(missing_subsets)}"
        )

    tally = pile_tally.from_environment()
    console.phase("review.build load", file=sys.stderr)
    phase = time.perf_counter()
    workload = load_workload(audit_path, ledger_path, dict(LETTERS))
    if tally:
        tally.hold("workload.units", workload.units, leaf_types=(AuditRow,))
        tally.hold_reading(
            "workload.rows",
            lambda: pile_tally.estimate([row for unit in workload.units for row in unit.rows]),
        )
    if not workload.units:
        raise SystemExit(
            f"{audit_path} records no divergent rows, so there is nothing to build a review surface over"
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(spec_root)
    font_digests = {"before": _sha256(before_font), "after": _sha256(after_font)}
    family_keys, helpers_digest = unit_cache.family_content_keys(spec_root, spec, after_font)
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
    if tally:
        tally.hold("signatures", signatures)
        tally.hold_reading("ink.shape_memo", shape_memo_census)
        tally.boundary("load")
    _phase_timing(
        "review.build load",
        phase,
        f"(signatures: {len(signatures) - signatures_shaped:,} cached, {signatures_shaped:,} shaped)",
    )

    # The incremental plan (issue 20; rebuild/review/unit_cache.py is the contract): key every unit over its content closure, serve what the previous surface already computed, and hand the runner only the remainder. The reduces below always run over the full universe, so every order- or ledger-derived field is this build's own.
    console.phase("review.build plan", file=sys.stderr)
    phase = time.perf_counter()
    environment = unit_cache.environment_stamp(
        repo_root, spec, subset_dir, before_font, junior_font, helpers_digest
    )
    keys = {unit.unit_id: keyer.key(unit) for unit in workload.units}
    release_rows(workload.units)
    store = None if fresh_unit_cache else unit_cache.load_store(out_dir, environment)
    served: dict[str, unit_cache.CachedUnit] = {}
    located: dict[str, unit_cache.PriorFragment] = {}
    if store:
        units_by_id = {unit.unit_id: unit for unit in workload.units}
        candidates = {
            unit.unit_id: store[keys[unit.unit_id]] for unit in workload.units if keys[unit.unit_id] in store
        }
        # A candidate's address is its store record's, the span the shard writer returned for the fragment when the previous surface was written, so placing it costs nothing: the walk over the previous surface's shards is asked only for the records the store handed back without an address (see `unit_cache.load_store` for when that is), and on a surface this code wrote that is no record at all. Either way a candidate is served only when the fragment at its address carries the very stamp the store recorded for it — the walk reads the stamp as it goes, and a store address is stamped with the record's own — and everything that rides on a served fragment, that these are the bytes `check_unit` passed in the build that emitted them, so this build need not check them again, is only as good as that equality. What the plan keeps is the fragment's address, not the fragment: the bytes are read back through it when the shard that takes them is being written, and held against the same id and stamp then, which for a store-addressed fragment is the one time it is parsed. The second condition is the shape: a fragment is served only when it is the slim or full fragment this build would write for the unit, because the exemption that decides it is the ledger's and sits outside the key — a unit crossing into the human workload on a ledger edit is re-enriched in full rather than served the slim fragment its class earned before the edit, and one crossing out is re-drafted slim rather than served with drafts nobody will read.
        wanted: dict[str, set[str]] = {}
        for cached in candidates.values():
            found = cached.located()
            if found is None:
                wanted.setdefault(cached.prior_class, set()).add(cached.prior_id)
            else:
                located[cached.prior_id] = found
        if wanted:
            located.update(unit_cache.locate_prior_fragments(out_dir, wanted))
        served = {
            uid: cached
            for uid, cached in candidates.items()
            if cached.prior_id in located
            and located[cached.prior_id].content_key == cached.content_key
            and cached.slim == _slim_for(units_by_id[uid], cached)
        }
    fresh = [unit for unit in workload.units if unit.unit_id not in served]
    sampled = set(_verification_sample(sorted(served), environment))
    # Copies, because recomputing a unit's phase 1 writes the ink flags onto it and the verification patch writes the injected batch and class; the originals are the ones the reduces and the store read.
    verify_units = [replace(unit) for unit in workload.units if unit.unit_id in sampled]
    if tally:
        tally.hold("unit_cache.keys", keys)
        tally.hold("unit_cache.store", store or {})
        tally.hold("unit_cache.served", served)
        tally.hold("unit_cache.located", located)
        tally.boundary("plan")
    _phase_timing(
        "review.build plan", phase, f"(served {len(served):,} of {len(workload.units):,} units from cache)"
    )

    console.phase("review.build units", file=sys.stderr)
    phase = time.perf_counter()
    runner = _FreshRunner(
        fresh,
        jobs,
        subset_dir,
        before_font,
        after_font,
        junior_font,
        repo_root,
        verify_units,
        spec_root=spec_root,
        out_dir=out_dir,
    )
    try:
        projections = runner.phase1()

        states: dict[str, _UnitState] = {}
        pool: dict = {}
        for unit in workload.units:
            cached = served.get(unit.unit_id)
            if cached is not None:
                states[unit.unit_id] = _UnitState(
                    ink_identical=cached.ink_identical,
                    picture_identical=cached.picture_identical,
                    junior_equivalent=cached.junior_equivalent,
                    ink_deltas=cached.ink_deltas,
                    diffs_digest=cached.diffs_digest,
                    cluster=cached.cluster,
                    family=cached.family,
                    pair_codepoints=cached.pair_codepoints,
                    seam_home=_pooled_seam_home(_cached_seam_home(unit, cached), pool),
                    seam_rects=cached.seams,
                    mismatches=cached.mismatches,
                )
            else:
                projection = projections.pop(unit.unit_id)
                states[unit.unit_id] = _UnitState(
                    ink_identical=projection.ink_identical,
                    picture_identical=projection.picture_identical,
                    junior_equivalent=projection.junior_equivalent,
                    ink_deltas={
                        sys.intern(config): sys.intern(delta) for config, delta in projection.ink_deltas
                    },
                    diffs_digest=sys.intern(projection.diffs_digest),
                    cluster=sys.intern(projection.cluster),
                    family=sys.intern(projection.family),
                    pair_codepoints=projection.pair_codepoints,
                    seam_home=_pooled_seam_home(projection.seam_home, pool),
                    seam_rects=_seam_records(projection.seam_rects),
                    mismatches=list(projection.mismatches),
                )
        del pool

        for unit in workload.units:
            state = states[unit.unit_id]
            unit.ink_identical = state.ink_identical
            unit.picture_identical = state.picture_identical
            unit.junior_equivalent = state.junior_equivalent
            unit.ink_deltas = state.ink_deltas
        total_batches = assign_batches(workload.units, batch_size)

        # Promote each UNMATCHED unit's verdict family to its class so the per-class shard loop shards it under that family. The cluster signature already keys on that final class: the runner computed it where the family was assigned, and a served unit trusts the stored value, whose inputs (configs, final class, the ink diffs) are all under the content key.
        for unit in workload.units:
            if unit.class_id == UNMATCHED_CLASS:
                unit.family_id = states[unit.unit_id].family
                unit.class_id = unit.family_id

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

        # The verification sample recomputes served units on copies taken before the reduces ran, so the global fields every other unit already carries are handed to it explicitly.
        injections = {
            unit.unit_id: (unit.batch, unit.echo, unit.cluster, unit.class_id, assignments[unit.unit_id])
            for unit in workload.units
            if unit.unit_id in sampled
        }
        verified = runner.verify(injections)
        # What the cache serves must be what a fresh computation of the same window writes. The content key carries most of that claim: it hashes the fragment's adjudicable fields — the ink flag, both fonts' glyphs and cells, the seams, the notation, and on a full fragment the highlight geometry — so one comparison per sampled unit covers all of them at once, against the stamp the served fragment was proved to carry when it was located. The recomputation writes the slim or full shape from the unit's own flags and exemption, exactly as the write did, so a served fragment of the wrong shape would miss the key here as well as at the plan. Two things sit outside it and are answered elsewhere. `ink_deltas` is a carry-presentation key (`unit_cache.CARRY_PRESENTATION_KEYS`), so the recomputation hands it back beside the stamp and it is compared against the store record the unit was served from. The drafts, the explain text, and the secondary seams are outside it too, and they are guaranteed at production rather than sampled: the drafter raises on a pin or a policy record it cannot stand behind, the explain rides the same enrichment as the cells and seams the key does cover, and `patch_fragment` re-emits the secondary seams from the stored rects under this build's own home assignments.
        stale = sorted(
            unit_id
            for unit_id, (key, deltas) in verified.items()
            if key != served[unit_id].content_key or dict(deltas) != served[unit_id].ink_deltas
        )
        if stale:
            raise SystemExit(
                f"the unit cache served {len(stale)} of {len(verified)} sampled units whose content key or "
                f"ink deltas do not match a fresh recomputation: {', '.join(stale[:10])}"
            )
        mismatches = [line for unit in workload.units for line in states[unit.unit_id].mismatches]
        echo_count = len(echo_ids)
        if tally:
            tally.hold("states", states)
            tally.hold("verified", verified)
            runner.hold_piles(tally)
            tally.boundary("units")
        _phase_timing(
            "review.build units",
            phase,
            f"(jobs={jobs}, fresh={len(fresh):,}, verified={len(verified):,} served)",
        )

        # The write is phase 2, and it is one pass over both kinds of unit: each fragment is read back by address as the shard that takes it goes down — a served one out of the previous surface at the address the locate pass recorded, a fresh one out of the runner's spool at the address phase 1 recorded — patched with this build's scaffold and seam homes through `patch_fragment`, stamped if it is fresh, and gone from the parent once the shard, the checker and the sidecar spools have had it. It runs under the runner because the spool is the runner's.
        console.phase("review.build manifest+check", file=sys.stderr)
        phase = time.perf_counter()
        reader = unit_cache.PriorFragmentReader(out_dir)

        def fragments_in(ordered_units: list[Unit]) -> Iterator[dict]:
            for unit in ordered_units:
                cached = served.get(unit.unit_id)
                try:
                    if cached is None:
                        fragment = runner.fragment(unit.unit_id)
                    else:
                        fragment = reader.read(located[cached.prior_id])
                except ValueError as error:
                    raise SystemExit(
                        f"the fragment for {unit.unit_id} cannot be read back: {error}"
                    ) from None
                seams = states[unit.unit_id].seam_rects if cached is None else cached.seams
                fragment = patch_fragment(fragment, unit, seams, assignments[unit.unit_id])
                yield fragment if cached is not None else stamp_fragment(fragment)

        try:
            written = _write_surface(
                out_dir,
                workload,
                classes,
                by_class,
                fragments_in,
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
                font_digests,
                served_ids=frozenset(served),
                tally=tally,
            )
        finally:
            reader.close()
        if tally:
            tally.boundary("manifest+check")
    finally:
        runner.close()
    manifest = written.manifest
    _phase_timing("review.build manifest+check", phase)

    console.phase("review.build census-facts", file=sys.stderr)
    phase = time.perf_counter()
    premerge_facts = census.derive_premerge(premerge_capture, workload.units)
    # An UNMATCHED window is a real new join under review, so it is never ink-identical — a whole-corpus fact rather than a property of the projection, which is why it is asserted over the live workload here and not inside `derive_premerge`, where synthetic callers legitimately build the shape it forbids.
    families_on_identical = [
        index for index, _family in premerge_facts.families if premerge_facts.ink_flags[index] == "1"
    ]
    if families_on_identical:
        raise SystemExit(
            f"{len(families_on_identical)} ink-identical pre-merge units carry a verdict family "
            f"(first at capture index {families_on_identical[0]})"
        )
    census.write_facts(
        out_dir,
        census.build_facts(
            manifest,
            workload.units,
            written.config_notes,
            premerge_capture,
            premerge_facts,
            workload.row_count,
        ),
    )
    if tally:
        tally.boundary("census-facts")
    _phase_timing("review.build census-facts", phase)

    console.phase("review.build cache", file=sys.stderr)
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
                content_key=written.content_keys[unit.unit_id],
                slim=unit.slim_fragment,
                address=written.addresses[unit.unit_id],
                ink_identical=unit.ink_identical,
                picture_identical=unit.picture_identical,
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
    if tally:
        tally.hold("unit_cache.records", records)
        tally.boundary("cache")
    _phase_timing("review.build cache", phase)
    return manifest


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


# --- table-diff mode -----------------------------------------------------------------


def _table_diff_unit_json(
    entry: tablediff.DiffEntry,
    unit_id: str,
    batch: int | None,
    full_configs,
    ink_identical: bool,
    picture_identical: bool,
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
        "picture_identical": picture_identical,
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
    if not entries:
        raise SystemExit(
            f"{baseline_dir} and {new_dir} settle every window alike, so there is nothing to diff"
        )

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

    font_digests = {"before": _sha256(before_font), "after": _sha256(after_font)}
    comparator = InkComparator(before_font, after_font)
    classes_meta: list[dict] = []
    shards_by_class: dict[str, list[dict]] = {}
    spans_by_class: dict[str, list[tuple[int, int, int]]] = {}
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
        channel_counts = {channel: 0 for channel in MACHINE_CHANNELS}
        for entry in members:
            # A witnessless entry has no renderable text to shape, so it cannot be proven ink- or picture-identical and stays in the human workload.
            text = "".join(chr(value) for value in entry.witness) if entry.witness else ""
            ink_identical = bool(text) and comparator.ink_identical(text, (entry.config,))
            picture_identical = (
                bool(text) and not ink_identical and comparator.picture_identical(text, (entry.config,))
            )
            if ink_identical or picture_identical:
                batch = None
                machine_count += 1
                channel_counts["ink_identical" if ink_identical else "picture_identical"] += 1
                machine_rows += max(len(entry.paired), 1)
            else:
                batch = human_index // batch_size
                batches.add(batch)
                human_index += 1
            unit_id = f"u-{index:04d}"
            if batch is not None:
                human_unit_ids.append(unit_id)
            shard.append(
                _table_diff_unit_json(entry, unit_id, batch, all_configs, ink_identical, picture_identical)
            )
            index += 1
        parts, spans_by_class[bucket] = _write_shard(out_dir, bucket, shard)
        shards_by_class[bucket] = shard
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
                "machine_channels": channel_counts,
                "shards": parts,
                "batches": sorted(batches),
            }
        )

    fonts = {
        "before": _copy_font(
            before_font, out_dir, "before.otf", "AMS Review Before", repo_root, font_digests["before"]
        ),
        "after": _copy_font(
            after_font, out_dir, "after.otf", "AMS Review After", repo_root, font_digests["after"]
        ),
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
    unit_index.write_index(out_dir, shards_by_class.items())
    app_index.write_app_artifacts(out_dir, shards_by_class, spans_by_class)
    errors = check_shards(manifest, shards_by_class)
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
        f"inputs_fingerprint must map exactly the input components ({', '.join(fingerprint.COMPONENTS)}) to hashes or null",
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
        channels = machine.get("channels")
        if channels is not None:
            need(
                isinstance(channels, dict) and set(channels) == set(MACHINE_CHANNELS),
                "machine_approved.channels must map the three machine channels",
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
    need(isinstance(classes, list), "classes must be a list")
    for meta in classes or ():
        identifier = meta.get("id", "<missing>")
        for key in ("id", "why"):
            need(isinstance(meta.get(key), str), f"classes[{identifier}].{key} must be a string")
        shards = meta.get("shards")
        need(
            isinstance(shards, list) and shards and all(isinstance(part, str) for part in shards),
            f"classes[{identifier}].shards must be a nonempty list of paths",
        )
        for key in ("unit_count", "row_count", "machine_approved_count"):
            need(isinstance(meta.get(key), int), f"classes[{identifier}].{key} must be an integer")
        channels = meta.get("machine_channels")
        well_formed = (
            isinstance(channels, dict)
            and set(channels) == set(MACHINE_CHANNELS)
            and all(isinstance(count, int) for count in channels.values())
        )
        need(
            well_formed,
            f"classes[{identifier}].machine_channels must count the three machine channels",
        )
        need(isinstance(meta.get("batches"), list), f"classes[{identifier}].batches must be a list")
        need("status" in meta, f"classes[{identifier}].status must be present")
        need(
            isinstance(meta.get("ink_identical"), bool), f"classes[{identifier}].ink_identical must be a bool"
        )
        need(isinstance(meta.get("no_verdict"), bool), f"classes[{identifier}].no_verdict must be a bool")
    return errors


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
    need(isinstance(unit.get("picture_identical"), bool), "picture_identical must be a bool")
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
    approving = [channel for channel in MACHINE_CHANNELS if unit.get(channel) is True]
    need(len(approving) <= 1, "at most one machine channel may approve a unit")
    if approving or unit.get("no_verdict") is True:
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
    for key in ("class", "group", "notation", "summary"):
        need(isinstance(unit.get(key), str) and unit.get(key) != "", f"{key} must be a nonempty string")
    # Slim is a shape the checker holds exact in both directions, like batch null: a slim unit carrying an explain, drafts or a highlight is bytes the build promised not to write, and a human unit without any of them is a reviewer with nothing to act on. Absence is the test, not emptiness — a slim fragment omits the keys, and a human fragment with a null under one of them is the blank the app must never mistake for slim.
    slim = mode == "m1-audit" and slim_fragment(unit)
    if slim:
        for key in SLIM_OMITTED_KEYS:
            need(key not in unit, f"machine-approved and no-verdict units omit {key}")
    else:
        need(
            isinstance(unit.get("explain"), str) and unit.get("explain") != "",
            "explain must be a nonempty string",
        )
    summary = unit.get("summary")
    if mode == "m1-audit" and isinstance(summary, str):
        need(summary.startswith("New: "), "summary must open with the New: clause")
        need("\n" not in summary, "summary must be one line")
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
    # One group per unit is the M1 dedupe key's own guarantee: a unit's rows share (codepoints, baseline, new), so its configs cannot render differently. Data that broke it would have to render stacked rather than be collapsed, so it is a build error and not a display choice.
    if mode == "m1-audit" and isinstance(groups, list):
        need(len(groups) == 1, "m1-audit units must carry exactly one render group")
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
        need(all(is_seam_token(seam) for seam in before["seams"]), "before.seams must be break/lig/yN tokens")
    if isinstance(after, dict) and isinstance(after.get("seams"), list):
        need(all(is_seam_token(seam) for seam in after["seams"]), "after.seams must be break/lig/yN tokens")
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
    if mode == "m1-audit" and not slim:
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
                isinstance(record.get(key), int) for key in ("x_min", "x_max", "advance_total")
            ):
                need(
                    record["x_min"] <= record["x_max"] <= record["advance_total"],
                    f"highlight.{side} must satisfy x_min <= x_max <= advance_total",
                )

    def need_rect(record, label: str) -> None:
        need(
            isinstance(record, dict)
            and all(isinstance(record.get(key), int) for key in ("x_min", "x_max", "advance_total")),
            f"{label} must carry integer x_min/x_max/advance_total",
        )
        if isinstance(record, dict) and all(
            isinstance(record.get(key), int) for key in ("x_min", "x_max", "advance_total")
        ):
            need(
                record["x_min"] <= record["x_max"] <= record["advance_total"],
                f"{label} must satisfy x_min <= x_max <= advance_total",
            )

    seams = unit.get("secondary_seams")
    if seams is not None:
        need(isinstance(seams, list) and seams, "secondary_seams must be null or a nonempty list")
        need(
            unit.get("ink_identical") is not True and unit.get("picture_identical") is not True,
            "ink-identical and picture-identical units must not carry secondary_seams",
        )
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
    if not slim:
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
            # A pin the reviewer would paste into the corpus and watch fail is worse than no pin: the two verdicts the drafter records against it — the repo's own parser, and a replay of the assertion against the after font — must both read pass on every shipped unit.
            if mode == "m1-audit":
                need(pin.get("syntax") == "pass", f"drafts.pin.syntax is {pin.get('syntax')!r}")
                need(
                    pin.get("semantics_after_font") == "pass",
                    f"drafts.pin.semantics_after_font is {pin.get('semantics_after_font')!r}",
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
            if mode == "m1-audit":
                need(
                    policy.get("schema_valid") is True,
                    "drafts.policy.suggested_record must validate against the rune schema",
                )
                need(
                    policy.get("keypath") in ("policy.refuse[+]", "policy.prefer[+]", "policy.contract[+]"),
                    f"drafts.policy.keypath is {policy.get('keypath')!r}",
                )
                # The draft may only name records the unit's own trace named; anything else would send the reviewer to edit a record that had nothing to do with what they are looking at.
                if isinstance(policy.get("names_provenance"), list) and isinstance(
                    unit.get("provenance"), list
                ):
                    need(
                        set(policy["names_provenance"]) <= set(unit["provenance"]),
                        "drafts.policy.names_provenance must come from the unit's own provenance",
                    )
        any_of = drafts.get("any_of")
        if mode == "m1-audit":
            need(any_of is not None, "drafts.any_of must be present in m1-audit mode")
        if any_of is not None:
            need(
                isinstance(any_of.get("text"), str) and any_of.get("text"),
                "drafts.any_of.text must be a nonempty string",
            )
            need(isinstance(any_of.get("features"), dict), "drafts.any_of.features must be a mapping")
            candidates = any_of.get("candidates")
            need(
                isinstance(candidates, list) and candidates,
                "drafts.any_of.candidates must be a nonempty list",
            )
            if isinstance(candidates, list):
                need(
                    len(set(candidates)) == len(candidates),
                    "drafts.any_of.candidates must not repeat a behavior",
                )
                # The after-behavior candidate is the pin, already parsed above; the before-behavior one is written from the baseline subset row and is the only string here nothing else checks.
                if mode == "m1-audit":
                    expect = pin.get("expect") if isinstance(pin, dict) else None
                    parse_expect = _import_test_shaping().parse_expect
                    for candidate in candidates:
                        if candidate == expect or not isinstance(candidate, str):
                            continue
                        try:
                            parse_expect(candidate)
                        except ValueError as error:
                            need(False, f"drafts.any_of candidate {candidate!r} does not parse: {error}")
    return errors


class _SurfaceCheck:
    """The unit-grain and cross-unit halves of the §7 contract check as an accumulator fed one fragment at a time: `class_start` with the manifest's class record, `unit` per fragment in shard order, `class_end` when the class's last fragment has gone by, and `finish` with the manifest for the predicates that read its totals. It exists so the build can run the whole of `check_shards` over fragments it releases as it writes them — what it keeps per unit is the slim identity the cross-unit predicates read (codepoints, whether there is a primary pair, whether anything visible changed) and the grouping keys, never the fragment — and `check_shards` is this class fed from a mapping a caller holds whole. `check_unit` runs inside `unit` for every fragment not in `served_ids`, so the per-unit complaints land as the fragment goes by and the cross-unit ones at `finish`."""

    def __init__(
        self,
        *,
        mode: str,
        descriptions: Mapping,
        batch_size: object,
        repo_root: Path | None,
        served_ids: Collection[str],
    ) -> None:
        self._mode = mode
        self._descriptions = descriptions
        self._batch_size = batch_size
        self._repo_root = repo_root
        self._served_ids = served_ids
        self.errors: list[str] = []
        self._seen_units = 0
        self._seen_rows = 0
        self._seen_ids: set[str | None] = set()
        self._seen_human_ids: set[str] = set()
        self._seen_machine_by_class: dict[str, int] = {}
        self._seam_homes: list[tuple[str | None, str]] = []
        self._seam_units = 0
        self._seams_homed = 0
        self._seams_homeless = 0
        self._echo_keys: dict[str | None, set[tuple]] = {}
        self._cluster_keys: dict[str | None, set[tuple]] = {}
        self._echo_cluster: dict[str | None, str | None] = {}
        self._human_batches: list[tuple[int, str, object]] = []
        self._identity: dict[str | None, tuple] = {}
        self._policy_files: set[str] = set()
        self._meta: Mapping = {}
        self._class_units = 0
        self._machine_count = 0
        self._channel_counts: dict[str, int] = {}

    def class_start(self, meta: Mapping) -> None:
        self._meta = meta
        self._class_units = 0
        self._machine_count = 0
        self._channel_counts = {channel: 0 for channel in MACHINE_CHANNELS}
        if meta.get("no_verdict") and meta.get("batches"):
            self.errors.append(f"class {meta.get('id')}: a no-verdict class must carry no batches")

    def unit(self, unit: dict) -> None:
        errors = self.errors
        meta = self._meta
        mode = self._mode
        self._class_units += 1
        unit_id = unit.get("id")
        if unit_id not in self._served_ids:
            errors.extend(check_unit(unit, mode))
        self._identity[unit_id] = (
            unit.get("codepoints"),
            unit.get("pair") is not None,
            unit.get("ink_identical") is True or unit.get("picture_identical") is True,
        )
        policy = (unit.get("drafts") or {}).get("policy") or {}
        if isinstance(policy.get("file"), str):
            self._policy_files.add(policy["file"])
        for clause in unit.get("config_gate") or ():
            if isinstance(clause, dict) and not self._descriptions.get(clause.get("feature")):
                errors.append(
                    f"unit {unit_id}: config_gate names {clause.get('feature')!r}, which the manifest's "
                    "feature_descriptions does not gloss"
                )
        # Echo and cluster are m1-audit grains; a table-diff unit carries null for both, and reading them as group ids would put every one of its units in the same nonexistent group.
        if mode == "m1-audit" and unit.get("batch") is not None and isinstance(unit_id, str):
            key = (unit.get("class"), tuple(unit.get("configs") or ()))
            self._echo_keys.setdefault(unit.get("echo"), set()).add(key)
            self._cluster_keys.setdefault(unit.get("cluster"), set()).add(key)
            if self._echo_cluster.setdefault(unit.get("echo"), unit.get("cluster")) != unit.get("cluster"):
                errors.append(f"unit {unit_id}: echo {unit.get('echo')} spans two clusters")
            if unit_id[2:].isdigit():
                self._human_batches.append((int(unit_id[2:]), unit_id, unit.get("batch")))
        if unit.get("class") != meta.get("id"):
            errors.append(f"unit {unit.get('id')}: class {unit.get('class')} in shard {meta.get('id')}")
        if unit.get("id") in self._seen_ids:
            errors.append(f"duplicate unit id {unit.get('id')}")
        self._seen_ids.add(unit.get("id"))
        if unit.get("batch") is not None and isinstance(unit.get("id"), str):
            self._seen_human_ids.add(unit["id"])
        if unit.get("no_verdict") != bool(meta.get("no_verdict")):
            errors.append(
                f"unit {unit.get('id')}: no_verdict {unit.get('no_verdict')} in a class "
                f"whose no_verdict is {meta.get('no_verdict')}"
            )
        if machine_approved(unit):
            self._machine_count += 1
            for channel in MACHINE_CHANNELS:
                if unit.get(channel) is True:
                    self._channel_counts[channel] += 1
        elif (
            mode == "m1-audit"
            and unit.get("no_verdict") is not True
            and unit.get("batch") not in meta.get("batches", ())
        ):
            errors.append(f"unit {unit.get('id')}: batch {unit.get('batch')} not in class batches")
        if unit.get("secondary_seams"):
            self._seam_units += 1
            for seam in unit["secondary_seams"]:
                if not isinstance(seam, dict):
                    continue
                if seam.get("home") is None:
                    self._seams_homeless += 1
                else:
                    self._seams_homed += 1
                    self._seam_homes.append((unit.get("id"), seam["home"]))

    def class_end(self) -> None:
        meta = self._meta
        errors = self.errors
        if self._class_units != meta.get("unit_count"):
            errors.append(
                f"shard {meta['id']}: {self._class_units} units, manifest says {meta.get('unit_count')}"
            )
        if self._machine_count != meta.get("machine_approved_count"):
            errors.append(
                f"class {meta.get('id')}: {self._machine_count} machine-approved units, "
                f"manifest says {meta.get('machine_approved_count')}"
            )
        # The app renders a machine fold's badge from this record alone, so a stale count would mislabel a fold no reader can check against the units it summarizes.
        declared_channels = meta.get("machine_channels")
        if isinstance(declared_channels, dict) and dict(declared_channels) != self._channel_counts:
            errors.append(
                f"class {meta.get('id')}: machine_channels {dict(declared_channels)} != "
                f"{self._channel_counts} in the shards"
            )
        if self._machine_count:
            self._seen_machine_by_class[meta["id"]] = self._machine_count
        self._seen_units += self._class_units
        self._seen_rows += meta.get("row_count", 0)

    def finish(self, manifest: Mapping) -> list[str]:
        errors = self.errors
        mode = self._mode
        seen_ids = self._seen_ids
        identity = self._identity
        totals = manifest.get("totals", {})
        if self._seen_units != totals.get("units"):
            errors.append(f"totals.units {totals.get('units')} != {self._seen_units} shard units")
        if self._seen_rows != totals.get("rows"):
            errors.append(f"totals.rows {totals.get('rows')} != {self._seen_rows} summed class rows")
        human_unit_ids = manifest.get("human_unit_ids")
        if isinstance(human_unit_ids, list) and all(isinstance(unit, str) for unit in human_unit_ids):
            if set(human_unit_ids) != self._seen_human_ids:
                errors.append("human_unit_ids does not match the shards' non-null batches")
        machine = manifest.get("machine_approved") or {}
        if sum(self._seen_machine_by_class.values()) != machine.get("units"):
            errors.append(
                f"machine_approved.units {machine.get('units')} != "
                f"{sum(self._seen_machine_by_class.values())} machine-approved shard units"
            )
        if self._seen_machine_by_class != {
            key: value for key, value in (machine.get("by_class") or {}).items()
        }:
            errors.append("machine_approved.by_class does not match the shards' machine-approved counts")
        for echo, keys in self._echo_keys.items():
            if len(keys) > 1:
                errors.append(f"echo {echo}: one group spans {sorted(keys)[:2]}")
        for cluster, keys in self._cluster_keys.items():
            if len(keys) > 1:
                errors.append(f"cluster {cluster}: one signature spans {sorted(keys)[:2]}")
        batch_size = self._batch_size
        if mode == "m1-audit" and isinstance(batch_size, int) and batch_size > 0:
            human_batches = self._human_batches
            human_batches.sort()
            ordered = [unit_id for _number, unit_id, _batch in human_batches]
            if ordered != manifest.get("human_unit_ids"):
                errors.append("human_unit_ids is not the id-ordered sequence of the shards' batched units")
            expected = [index // batch_size for index in range(len(human_batches))]
            if [batch for _number, _unit_id, batch in human_batches] != expected:
                errors.append(
                    f"batches are not contiguous slices of {batch_size} over the id-ordered workload"
                )
            if manifest.get("totals", {}).get("batches") != len({b for _n, _u, b in human_batches}):
                errors.append("totals.batches does not count the distinct batches in the shards")
        for unit_id, home in self._seam_homes:
            if home == unit_id:
                errors.append(f"unit {unit_id}: a secondary seam names itself as home")
            elif home not in seen_ids:
                errors.append(f"unit {unit_id}: secondary seam home {home} is not a unit in this output")
        seam_census = manifest.get("secondary_seams")
        # The home relation's own shape, checkable only where the resolver assigned it — which is exactly where it wrote the census. A home is a shorter window contained in this one, judging the same adjacency as its own primary pair; and it is never ink-identical, because a home with nothing to see is what `seams_suppressed_invisible` counts instead of homing.
        if isinstance(seam_census, dict):
            for unit_id, home in self._seam_homes:
                if home not in identity or unit_id not in identity:
                    continue
                tokens = (identity[unit_id][0] or "").split(":")
                home_tokens = (identity[home][0] or "").split(":")
                contained = len(home_tokens) <= len(tokens) and any(
                    tokens[offset : offset + len(home_tokens)] == home_tokens
                    for offset in range(len(tokens) - len(home_tokens) + 1)
                )
                if not contained:
                    errors.append(f"unit {unit_id}: secondary seam home {home} is not a substring window")
                if not identity[home][1]:
                    errors.append(f"unit {unit_id}: secondary seam home {home} has no primary pair")
                if identity[home][2]:
                    errors.append(f"unit {unit_id}: secondary seam home {home} shows no visible change")
                if identity[unit_id][2]:
                    errors.append(f"unit {unit_id}: a unit with no visible change carries a secondary seam")
        if isinstance(seam_census, dict):
            for key, observed in (
                ("units_with_markers", self._seam_units),
                ("seams_homed", self._seams_homed),
                ("seams_homeless", self._seams_homeless),
            ):
                if seam_census.get(key) != observed:
                    errors.append(f"secondary_seams.{key} {seam_census.get(key)} != {observed} in the shards")
        if self._repo_root is not None:
            for name in sorted(self._policy_files):
                if not (Path(self._repo_root) / name).is_file():
                    errors.append(f"drafts.policy names {name}, which is not a file in the repo")
        return errors


def check_shards(
    manifest: dict,
    shards_by_class: dict[str, list[dict]],
    repo_root: Path | None = None,
    *,
    served_ids: Collection[str] = (),
) -> list[str]:
    """The unit-grain half of the §7 contract check over shard payloads a caller holds whole, keyed by class id — `check_output_dir` re-parses them from disk, the table-diff build hands over the dicts it serialized, and the m1 build runs the same predicates through `_SurfaceCheck` directly, a fragment at a time as each is written. Classes missing from the mapping are reported by the caller, which knows whether that means an unwritten file or an unassembled shard. `repo_root`, when given, also resolves the distinct policy-draft files once and checks they exist; every other predicate here is over the payload alone.

    Its second job is the cross-unit grain — the properties no single fragment can carry: that an echo group and a cluster each hold one class and one config set and that every echo nests inside one cluster, that the manifest's `human_unit_ids` really is the id-ordered human workload in contiguous batches, and that each secondary seam's home is a shorter unit of the same window where that adjacency is the primary judgment. Those were swept by tests over the live surface once a lane; here they run over every shipped unit on every build.

    A slim fragment (`audit.slim_fragment`) is complete for its kind: `check_unit` demands the omitted keys stay absent on it and present on every human fragment, and every cross-unit predicate reads fields both kinds carry — the flags, the window, the pair, the cells and seams, the batch and group ids — so the census the manifest states is counted over slim and full fragments alike.

    `served_ids` names the units the unit cache served this build, and `check_unit` is skipped for exactly those. A served fragment was fresh in the build that wrote it, where `check_unit` did run over it, and the build serves it only when the stamp on the shard equals the one its store record carries — so its adjudicable bytes are proven to be the bytes that passed. What the serving build then changes is the scaffold, and the scaffold comes from the same `unit_scaffold` a fresh emission reads; the cross-unit predicates below run over every unit either way, so the fields that relate a served unit to its neighbors are checked here on every build. A caller re-reading a finished surface (`check_output_dir`, the table-diff build) passes nothing and checks everything.
    """
    check = _SurfaceCheck(
        mode=manifest.get("mode", "m1-audit"),
        descriptions=manifest.get("feature_descriptions") or {},
        batch_size=manifest.get("batch_size"),
        repo_root=repo_root,
        served_ids=served_ids,
    )
    for meta in manifest.get("classes", ()):
        shard = shards_by_class.get(meta.get("id", ""))
        if shard is None:
            continue
        check.class_start(meta)
        for unit in shard:
            check.unit(unit)
        check.class_end()
    return check.finish(manifest)


def _check_output_files(out_dir: Path, manifest: dict, repo_root: Path | None = None) -> list[str]:
    """The files beside the manifest: every part of every shard present and non-empty, the per-unit index and the app's two sidecars present and stamped for this manifest, the index page written, and each copied font matching both the sha the manifest recorded and — when `repo_root` resolves the source it names — the font it was copied from. The build guards that second comparison itself, with the digest it takes at load and asserts at `_copy_font`, and the cycle's surface skip and `make verdict-ready` hold the manifest's recorded after-font sha against `rebuild/out/m1/M1.otf`; the comparison survives here for `check_output_dir` and `refresh_assets`."""
    errors: list[str] = []
    for meta in manifest.get("classes", ()):
        for part in unit_index.class_shards(meta):
            shard_path = Path(out_dir) / part
            if not shard_path.is_file():
                errors.append(f"shard {part} is missing")
            elif shard_path.stat().st_size == 0:
                errors.append(f"shard {part} is empty")
    for side, record in (manifest.get("fonts") or {}).items():
        font_path = Path(out_dir) / record.get("file", "")
        if not font_path.exists():
            errors.append(f"fonts.{side}: {record.get('file')} is missing")
            continue
        digest = _sha256(font_path)
        if digest != record.get("sha256"):
            errors.append(f"fonts.{side}: sha256 mismatch")
        if repo_root is not None and isinstance(record.get("source"), str):
            source = Path(record["source"])
            if not source.is_absolute():
                source = Path(repo_root) / source
            if source.is_file() and _sha256(source) != digest:
                errors.append(f"fonts.{side}: the copy is not {record['source']} as it stands on disk")
    if not (Path(out_dir) / "index.html").exists():
        errors.append("index.html is missing")
    if not unit_index.index_path(out_dir).is_file():
        errors.append(f"{unit_index.INDEX_NAME} is missing")
    elif not unit_index.index_is_current(out_dir):
        errors.append(f"{unit_index.INDEX_NAME} is unreadable or stamped for another manifest")
    for name, fmt in app_index.ARTIFACTS:
        if not app_index.artifact_path(out_dir, name).is_file():
            errors.append(f"{name} is missing")
        elif not app_index.artifact_is_current(out_dir, name, fmt):
            errors.append(f"{name} is unreadable or stamped for another manifest")
    return errors


def check_output_dir(out_dir: Path, repo_root: Path | None = None) -> list[str]:
    out_dir = Path(out_dir)
    errors: list[str] = []
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"{manifest_path} is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors.extend(check_manifest(manifest))
    shards_by_class: dict[str, list[dict]] = {}
    for meta in manifest.get("classes", ()):
        units: list[dict] = []
        for part in unit_index.class_shards(meta):
            shard_path = out_dir / part
            if not shard_path.exists():
                break
            units.extend(json.loads(shard_path.read_text(encoding="utf-8")))
        else:
            shards_by_class[meta.get("id", "")] = units
    errors.extend(check_shards(manifest, shards_by_class, repo_root))
    errors.extend(_check_output_files(out_dir, manifest, repo_root))
    return errors


# --- CLI ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "refresh-assets":
        parser = argparse.ArgumentParser(
            prog="rebuild.review.build refresh-assets", description=refresh_assets.__doc__
        )
        parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
        args = parser.parse_args(argv[1:])
        copied = refresh_assets(args.out)
        print(
            f"Refreshed {len(copied)} review asset(s) under {args.out}; the manifest's static component is "
            "restamped and nothing else moved",
            file=sys.stderr,
        )
        return
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

    from rebuild.tools.artifact_cycle import surface_job_budget, surface_job_derivation

    surface_jobs = surface_job_budget(skip_gates=True)
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
        "--jobs",
        type=int,
        default=surface_jobs,
        help=f"per-unit worker budget for the surface build; the default is the same `surface_job_budget()` width the artifact cycle passes rather than a checked-in one, taken at its unreserved arm because a hand run has no co-resident `make test` pool to leave cores or bytes to — on this box {surface_job_derivation(skip_gates=True)}, where the per-unit figure is one worker's own peak and the co-resident one is the parent that holds the whole corpus beside it. `--jobs 1` is serial, and it is what a box floors at when the pooled shape does not fit; a deliberate `--jobs N` is also how a wider run gets measured, since a pooled build files its per-worker peaks for `make job-costs`.",
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
