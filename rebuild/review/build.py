"""Build the review app's output directory, rebuild/out/review/ (rebuild/REVIEW-PLAN.md §1.3). The build assembles units and precomputes their enrichment and, for every unit that takes a verdict, all three verdict drafts. It writes manifest.json, one unit shard per class (split into byte-capped parts when a class is too large for one file), the census-facts.json sidecar that the artifact cycle copies into the checked-in census pins, copies of both fonts, and the static app files. The `snapshot` subcommand writes an accepted-state baseline. `refresh-assets` copies the static app files over an existing surface and restamps only that fingerprint component, without rebuilding any unit.

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
import math
import multiprocessing.connection
import random
import shutil
import subprocess
import sys
import threading
import time
import traceback
import warnings
from array import array
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import batched, chain, combinations
from pathlib import Path
from typing import BinaryIO, cast

from rebuild.pipeline import fingerprint
from rebuild.pipeline.baseline_subset import M1_ALPHABET
from rebuild.review import app_index, census, families, tablediff, unit_cache, unit_index
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    BATCH_SIZE,
    MACHINE_CHANNELS,
    SLIM_OMITTED_KEYS,
    UNMATCHED_CLASS,
    RowColumns,
    Unit,
    UnitTable,
    _config_index,
    assign_batches,
    batch_of,
    family_ranks,
    format_codepoints,
    load_workload,
    machine_approved,
    merge_ink_duplicate_units,
    parse_codepoints,
    release_rows,
    signature_rows,
    slim_fragment,
    sort_for_triage,
    synthesize_family_classes,
    triage_key,
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
    load_spec,
    notation,
    notation_tokens,
    resolve_home_assignments,
    seam_home_projection,
    text_entities,
)
from rebuild.review.subset_pack import ensure_pack, is_seam_token, table_digests
from rebuild.review.unit_store import UnitStore
from rebuild.tools import console, pile_tally
from rebuild.tools.cycle_timings import record_pool
from rebuild.tools.peak_rss import current_rss_bytes, peak_rss_self_bytes, rss_now_token, rss_token

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
    """Return the migration progress shown on the surface chip. `migrated` counts the letters this surface is built over: the subset filter's alphabet without its boundary tokens, which is the set of letters that have runes under glyph_data/runes/. `total` counts the whole Quikscript alphabet."""
    return {"migrated": len(M1_ALPHABET & set(LETTERS)), "total": len(LETTERS)}


def _inputs_fingerprint(
    repo_root: Path, m1_dir: Path, before_font: Path, junior_font: Path, spec_root: Path | None = None
) -> dict:
    """Return the manifest's `inputs_fingerprint`. The Stage A values are copied from run_m1's recorded inputs_fingerprint.json instead of being recomputed, so a surface built over stale out/m1 artifacts carries the stale hashes and the readiness checker can flag it. The Stage A values are null when that record is missing, unreadable, or incomplete."""
    stage_a = fingerprint.read_stage_a(m1_dir) or {key: None for key in fingerprint.STAGE_A_COMPONENTS}
    return {**stage_a, **fingerprint.stage_b(repo_root, before_font, junior_font, spec_root)}


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


UNIT_ASSEMBLY_EPOCH = "2026-09-05T00:00:00Z"


def _generated_at(*inputs: Path) -> str:
    """Return the manifest's `generated_at` stamp: the latest input mtime as UTC ISO, floored at UNIT_ASSEMBLY_EPOCH. It is the same for consecutive builds of the same inputs (the §6 determinism gate) and changes whenever an input changes. A verdict store is joined to a surface by this stamp. Bump the epoch whenever a build-code change re-keys units without changing any input: without the floor the stamp would not move, and the app would restore a stale autosave or import an old export by id onto the wrong units."""
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
    """Yield every assignment of on/off to `size` of the `tags`, most-on first and then in tag order. Most-on first makes an inclusion gate outrank an exclusion gate that selects the same configs, so a lone feature's gate reads "only when ss03 is on" instead of an equivalent negative phrasing."""
    for on_count in range(size, -1, -1):
        for combination in combinations(tags, size):
            for on_tags in combinations(combination, on_count):
                yield {tag: tag in set(on_tags) for tag in combination}


def _gate_clauses(constraints) -> list[dict]:
    """Return a resolved constraint mapping as the badge's ordered clauses, on before off and each group in tag order, each clause carrying the text the app prints for it. A lone ss10-on gate reads "only under ss10", because ss10 names the isolated overlay, not a joining behavior. The `text` fields are the only place this wording is defined: config_note joins them, and the app renders them as given."""
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
    """Return the unit's config badge as (gate, note). The gate is the smallest conjunction of feature on/off constraints that selects exactly the configs the divergence applies under, one clause per constraint; the app draws each clause as a chip in that feature's color. The note is the clauses joined, kept as a string for the census histogram and for hover text.

    Both are None when the unit covers every non-ss10 config, which is the common case. When no conjunction of at most GATE_CONSTRAINT_CAP constraints selects exactly the set, the gate is None and the note is the literal "only under: <set>".

    ss10 is a constraint like the others, except that a set containing no ss10 config is resolved against the non-ss10 configs alone. That lets an exclusion gate like ss03-off stand without also stating the implied ss10-off.
    """
    return _config_badge(tuple(unit_configs), tuple(full_configs))


def config_gate(unit_configs, full_configs) -> list[dict] | None:
    return config_badge(unit_configs, full_configs)[0]


def config_note(unit_configs, full_configs) -> str | None:
    return config_badge(unit_configs, full_configs)[1]


def _config_class_note(unit) -> str | None:
    """Return a note for a unit whose class differs by config (UNMATCHED under some configs and a ledger class under others), for example "blessed as ss03-chain-join-gains under ss03, ss02+ss03; novel under default, ss02". Return None when the unit has one class under every config."""
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


def _machine_approved_meta(
    machine_units: Iterable[tuple[str, int, str]], junior_font: Path, repo_root: Path
) -> dict:
    """Return the manifest's `machine_approved` record: unit and row totals across the three machine channels (ink-identical, picture-identical and junior-equivalent), unit counts per class (classes with none are omitted), and one sub-record per channel with its counts and verification method. `machine_units` yields one `(class_id, row_count, channel)` per machine-approved unit, in triage order. `by_class` keeps classes in first-appearance order, so they follow the manifest's class order: ledger classes, then the verdict families in `families.FAMILY_ORDER`. `census.invariant_group` publishes that order as the pins' `machine_approved_classes`, so it must not depend on the table's load order. The junior channel also records the Junior font it used, because the manifest's fonts block does not cover it: the app never renders it."""
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
    units = 0
    for class_id, row_count, channel_name in machine_units:
        units += 1
        by_class[class_id] = by_class.get(class_id, 0) + 1
        rows += row_count
        channel = channels[channel_name]
        channel["units"] += 1
        channel["rows"] += row_count
    return {
        "units": units,
        "rows": rows,
        "method": VERIFICATION_METHOD,
        "by_class": by_class,
        "channels": channels,
    }


_SCAFFOLD_HEAD = (
    "id",
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
_STAMPED_SCAFFOLD_KEYS = tuple(
    key for key in _SCAFFOLD_HEAD + _SCAFFOLD_TAIL if key not in unit_cache.CARRY_PRESENTATION_KEYS
)
# The scaffold keys `hold_scaffold` compares between the worker's drafting and the parent's patch: the stamped keys, whose equality means the content key still holds, plus the keys outside the carry projection that the parent writes back unchanged. The remaining scaffold keys, `echo` and `cluster`, are assigned by the parent's reduces after drafting, and `check_unit`'s write-time subset (`PATCHED`) checks them.
_HELD_SCAFFOLD_KEYS = _STAMPED_SCAFFOLD_KEYS + (
    "id",
    "no_verdict",
    "exemplar",
    "picture_identical",
    "ink_deltas",
)


def unit_scaffold(
    unit, full_configs=ACCEPTANCE_CONFIGS, *, ink_deltas: Mapping[str, str] | None = None
) -> dict:
    """Return every fragment field the build derives from the workload instead of from the enrichment: the unit's identity, its ledger-derived values, and the phase-1 machine flags. `ink_deltas` is passed in because the unit does not carry the deltas: drafting passes the comparator's values and the write passes the unit store's. Without it the unit's own mapping is used, which on every unit the build materializes is the shared empty `audit.NO_DELTAS`. A fragment carries no batch, because a batch is a slice of the manifest's triage index and a fragment's bytes depend only on its own content and the ledger. `unit_to_json` and `patch_fragment` both build the scaffold here, so a fragment cannot keep a stale value of a field a full build would change."""
    gate, note = config_badge(unit.configs, full_configs)
    return {
        "id": unit.unit_id,
        "ink_identical": unit.ink_identical,
        "picture_identical": unit.picture_identical,
        "junior_equivalent": unit.junior_equivalent,
        "ink_deltas": dict(unit.ink_deltas if ink_deltas is None else ink_deltas),
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
    fragment: dict,
    unit,
    seams: list[dict],
    seam_assign,
    full_configs=ACCEPTANCE_CONFIGS,
    *,
    hold: bool = False,
    ink_deltas: Mapping[str, str] | None = None,
) -> dict:
    """Rewrite a fragment's scaffold and secondary seams for this build, and return it. The write does this to every fresh fragment read from the spool and to every served fragment whose patched fields changed. Every scaffold field is rewritten from the current workload, with `ink_deltas` read from the unit store, and the secondary seams are rebuilt from the unit's rects in the unit store under this build's home assignments. Assigning keys in place keeps the fragment's key order, so a patched fragment has the same bytes a from-scratch build writes, and a served fragment whose patched fields did not change can be copied byte for byte instead.

    With `hold` set, `hold_scaffold` compares the held scaffold keys (`_HELD_SCAFFOLD_KEYS`) before they are written. That checks that the fragment's content key still holds after the patch, and that the scaffold `check_unit`'s drafting-time subset read is the scaffold that ships, which is why that subset may run in the process that drafts. The write sets `hold` for fresh fragments only. A served fragment's stamp was checked by the build that drafted it, and this build checks it again on the verification sample (`_recompute_fragment`, `hold_stamp`).
    """
    scaffold = unit_scaffold(unit, full_configs, ink_deltas=ink_deltas)
    if hold:
        hold_scaffold(fragment, scaffold)
    for key, value in scaffold.items():
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


def hold_scaffold(fragment: dict, scaffold: dict) -> None:
    """Exit if `patch_fragment` would change any of a fresh fragment's held scaffold keys (`_HELD_SCAFFOLD_KEYS`). Equality at the keys inside the carry projection (`_STAMPED_SCAFFOLD_KEYS`) means the fragment's `content_key` still holds after the patch: those keys are everything the patch can change under the stamp (the promoted class, the group and machine flags, and the codepoints and config badge derived from them). Equality at the held keys outside the projection means the drafting-time `check_unit` read the same scaffold values the write ships. The fragment arrives from the spool stamped and checked on every field the patch leaves alone, so this per-key comparison gives the same answer as rehashing the patched fragment (`hold_stamp`), with one comparison per key instead of a sorted-key dump of the whole fragment, on every fresh unit of the parent's serial write. A difference is a build bug: the parent's workload disagrees with what the worker drafted under."""
    moved = [key for key in _HELD_SCAFFOLD_KEYS if fragment[key] != scaffold[key]]
    if moved:
        inside = [key for key in moved if key in _STAMPED_SCAFFOLD_KEYS]
        outside = [key for key in moved if key not in _STAMPED_SCAFFOLD_KEYS]
        reasons = []
        if inside:
            reasons.append(
                f"{', '.join(inside)} inside the carry projection, so the stamp names other content"
            )
        if outside:
            reasons.append(
                f"{', '.join(outside)} outside the carry projection, so the drafting-time check answered "
                "for other bytes than the ones that ship"
            )
        raise SystemExit(
            f"unit {fragment.get('id')}: the fragment was drafted under other values of "
            f"{', '.join(moved)} than the write patches onto the fragment: " + "; ".join(reasons)
        )


def hold_stamp(fragment: dict) -> dict:
    """Exit unless a fragment's `content_key` is the hash of the fragment as it stands after `patch_fragment`, and return the fragment. The stamp is taken at drafting (`unit_to_json`). It still holds after the patch because the parent's values for the scaffold keys inside the carry projection (`_STAMPED_SCAFFOLD_KEYS`) equal the worker's, and everything else the patch writes is outside the projection. A difference is a bug in the projection's exclusions or in the parent's workload. The write path checks the same thing more cheaply with `hold_scaffold`; this full rehash runs on the verification sample (`_recompute_fragment`)."""
    stamp = fragment.get("content_key")
    recomputed = unit_cache.carry_content_hash(fragment)
    if stamp != recomputed:
        raise SystemExit(
            f"unit {fragment.get('id')}: the content key stamped at drafting ({stamp}) is not the key of the "
            f"fragment as written ({recomputed}); a field outside the carry projection moved between the two"
        )
    return fragment


def unit_to_json(
    enriched: EnrichedUnit,
    drafter: Drafter,
    full_configs=ACCEPTANCE_CONFIGS,
    *,
    final_class: str | None = None,
    ink_deltas: Mapping[str, str] | None = None,
) -> dict:
    """Return the shard fragment for one enriched unit as phase 1 drafts it, while its batch's shapes are still in the shape memo. The fragment is first built without drafts, with `final_class` (the verdict family an UNMATCHED unit is promoted to) as its class and `ink_deltas` as the comparator found them. It is then stamped: `content_key` is the hash of its carry projection, and the unit's id is `unit_cache.unit_id_for` of that key, written onto both the unit and the fragment so the seam-home projection carries the final id. The drafts are added after the stamp.

    The echo, the cluster and the secondary-seam homes are placeholders here: `patch_fragment` overwrites them at the write, and all of them are outside the carry projection. The scaffold keys inside the projection (`_STAMPED_SCAFFOLD_KEYS`) already carry the parent's values, which `hold_scaffold` checks at the write. Together these keep the stamp taken here valid for the written fragment. Nothing the drafter or the enricher produces may depend on the placeholders.

    A slim unit (`audit.slim_fragment`: machine-approved or verdict-exempt) skips the drafter, whose pin draft replays a shaping per unit, and its fragment omits the `SLIM_OMITTED_KEYS` entirely (absent, not null), since no reviewer sees them. The machine flags and the exemption that decide slimness are both set before this runs: the flags by the comparator and oracle earlier in phase 1, the exemption by the ledger at load.
    """
    unit = enriched.unit
    scaffold = unit_scaffold(unit, full_configs, ink_deltas=ink_deltas)
    if final_class is not None:
        scaffold["class"] = final_class
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
        "drafts": None,
        "content_key": None,
    }
    if unit.slim_fragment:
        for key in SLIM_OMITTED_KEYS:
            del fragment[key]
    fragment["content_key"] = unit_cache.carry_content_hash(fragment)
    unit.unit_id = fragment["id"] = unit_cache.unit_id_for(fragment["content_key"])
    if not unit.slim_fragment:
        pin = drafter.draft_pin(enriched)
        policy = drafter.draft_policy(enriched)
        any_of = drafter.draft_any_of(enriched)
        fragment["drafts"] = {
            "pin": pin.to_json(),
            "policy": policy.to_json() if policy else None,
            "any_of": any_of.to_json(),
        }
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
    """Copy `rebuild/review/static/` over an already-built surface and restamp only the manifest's `static` fingerprint component. An app JS, CSS or HTML edit changes only that component, and no unit depends on it. Everything else stays as the build left it: `generated_at` and `repo_head`, every shard, the per-unit index, both app sidecars, and the unit-cache store. The autosave is keyed on `generated_at`, so a verdict session in progress is unaffected, and the sidecars and the store stay current because `unit_index.manifest_sha256` leaves this component out of the manifest's identity. `_check_output_files` runs afterwards to confirm that. If it finds errors, the original manifest is restored, because a restamped manifest over sidecars that do not match it would look fresh to the next cycle, which would then skip the build that could repair it."""
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
    """Write `payload` to `path` as indented ASCII JSON through a staging file that is renamed into place, streaming a non-empty list one element at a time.

    The builds call it only for manifest.json, a dict that goes through `json.dumps` whole. Shards are written by `_ShardWriter`, which frames each fragment as the list path here frames an element, and the tests write shards through this function. Each list element is serialized inside a one-element list and the list's framing is stripped, so the C encoder writes the depth-1 indent (it handles `indent` for a one-shot dump on Python 3.14) and the bytes equal `json.dumps(payload, indent=1, ensure_ascii=True) + "\\n"`. Serializing a large list whole that way holds two full-size copies of the string at once, because the concatenation cannot resize the serialized string in place. `JSONEncoder.iterencode` would be simpler, but it uses the pure-Python encoder when it is not one-shot, which makes the write several times slower; stripping the framing adds about a sixth, about a second across the whole units directory.

    The staging file is needed because nothing downstream detects a half-written surface file: a failed encode or a killed build must leave the previous file intact, not a truncated shard or an empty manifest.
    """
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
    """Write classes into byte-capped shard parts one fragment at a time, in the order the build passes them. `open` a class, `add` each fragment (or `add_verbatim` the bytes of one the previous surface already holds) and receive its (part index, byte start, byte length), `close` the class to get the relative paths the manifest lists in part order, and `commit` once every class is written. Between calls it holds only the open file handle and the running byte count, so no shard is assembled in the parent's memory.

    The cap is for the browser. The app parses each part as one JSON string, and V8's `String::kMaxLength` under pointer compression is 2**29 - 24 bytes. When Blink cannot build a body that long it passes `JSON.parse` an empty string instead of an error, so an oversized shard shows up as "Unexpected end of JSON input" from a fetch that appeared to succeed. `SHARD_PART_BYTES` is half that limit, and the other half is headroom.

    A class that fits in one part keeps the bare `units/<class-id>.json` name, so the small classes, the checked-in fixtures and the archived surfaces do not change. A larger class is written as `units/<class-id>.000.json`, `units/<class-id>.001.json` and so on: numbered from zero with three digits, every part numbered, and never a bare name beside numbered ones. Both forms sort where `unit_index.class_shard_key` puts the class, because the character after the class id is `.` in both. A class opened with `numbered` uses the numbered form whatever its part count, so a part's path is final as soon as a fragment is added to it; the fresh spool opens its class this way so that each address is final when the fragment is added.

    Each fragment is framed as `_write_json` frames a list element, so a part's bytes equal `json.dumps(part, indent=1, ensure_ascii=True) + "\\n"`. Every part stays within the cap except a part holding a single fragment that alone exceeds it.

    The returned spans are byte addresses. The review app's explain panel Range-fetches them, the unit store records them so the next build can read its served units back, and `unit_cache.locate_prior_fragments` recomputes them from a written part for a record that lacks one. No punctuation falls inside a fragment's own bytes, and `ensure_ascii=True` makes the character count equal the byte offset, so `bytes[start:start + length]` is a standalone JSON value. Changing the `indent`, `ensure_ascii` or `separators` of the dump below breaks this without any error; `rebuild/test_app_index.py` slices every fragment back out to catch it.

    A class opened with the previous surface's part list is written against it. Each part is assumed to be the previous surface's part, unchanged, for as long as every fragment passed is the verbatim bytes of a fragment already at the offset the writer would put it at, which is the case for a served unit whose content and patched fields did not change. The first fragment that breaks this, a fresh one or one at a different offset, turns the part into a staging file holding the same prefix copied from the previous file, and writing continues from there. A part whose fragments all match and whose previous file ends where this one would end is not written at all. So a class with no changes costs one read of its bytes and no write, and a class with a fresh unit costs a copy from that unit on, without parsing or serializing any other fragment.

    Every staged part is written under a `.partial` name and renamed only at `commit`, after the last class closes. The build reads its served units out of the previous surface's shards by address while it writes this one, so replacing shards class by class could change a file before a later class reads from it. Deferring the renames keeps the previous surface intact until this one is fully written, and a failed encode or a killed build leaves the previous units in place; `abort` deletes the staging files. A kept part is already in place under its name. One whose name changes because the class's part count changed is copied to the new name and renamed with the rest.
    """

    _CLOSING = b"\n]\n"

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = Path(out_dir)
        self._units_dir = self._out_dir / "units"
        self._units_dir.mkdir(parents=True, exist_ok=True)
        self._pending: list[tuple[Path, Path]] = []
        self._class_id: str | None = None
        self._prior: list[str] = []
        self._parts: list[Path | str] = []
        self._handle: BinaryIO | None = None
        self._open = False
        self._size = 0
        self._numbered = False

    def open(self, class_id: str, prior_parts: Sequence[str] = (), *, numbered: bool = False) -> None:
        assert self._class_id is None, "close the open class first"
        self._class_id = class_id
        self._numbered = numbered
        self._prior = [part for part in prior_parts if (self._out_dir / part).is_file()]
        self._parts = []
        self._handle = None
        self._open = False
        self._size = 0

    def _staging(self, index: int) -> Path:
        return self._units_dir / f"{self._class_id}.{index:03d}.json.partial"

    def _begin_part(self) -> None:
        index = len(self._parts)
        self._size = 1
        if index < len(self._prior):
            self._parts.append(self._prior[index])
            self._handle = None
        else:
            path = self._staging(index)
            self._handle = path.open("wb")
            self._handle.write(b"[")
            self._parts.append(path)
        self._open = True

    def _materialize(self, prefix: int) -> None:
        """Turn the open kept part into a staging file holding the first `prefix` bytes of the previous file, which are the bytes the writer would have written so far."""
        index = len(self._parts) - 1
        prior = self._parts[index]
        assert isinstance(prior, str)
        path = self._staging(index)
        with (self._out_dir / prior).open("rb") as source, path.open("wb") as target:
            remaining = prefix
            while remaining:
                chunk = source.read(min(remaining, 1 << 20))
                if not chunk:
                    raise ValueError(f"{prior} is shorter than the {prefix} bytes this build read out of it")
                target.write(chunk)
                remaining -= len(chunk)
        self._handle = path.open("ab")
        self._parts[index] = path

    def _end_part(self) -> None:
        if self._handle is None:
            prior = self._parts[-1]
            assert isinstance(prior, str)
            if (self._out_dir / prior).stat().st_size != self._size + len(self._CLOSING):
                self._materialize(self._size)
        if self._handle is not None:
            self._handle.write(self._CLOSING)
            self._handle.close()
            self._handle = None
        self._open = False

    def _reserve(self, length: int) -> tuple[int, int, bool]:
        """Where the next fragment of `length` bytes goes: its part index and byte start, and whether it opens the part (else a comma precedes it)."""
        if self._open and self._size + length + 1 + len(self._CLOSING) > SHARD_PART_BYTES:
            self._end_part()
        if not self._open:
            self._begin_part()
            return len(self._parts) - 1, self._size, True
        return len(self._parts) - 1, self._size + 1, False

    def _emit(self, body: bytes, index: int, start: int, first: bool) -> tuple[int, int, int]:
        if self._handle is None:
            self._materialize(self._size)
        assert self._handle is not None
        if not first:
            self._handle.write(b",")
        self._handle.write(body)
        self._size = start + len(body)
        return index, start, len(body)

    def add(self, fragment: dict) -> tuple[int, int, int]:
        body = json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2].encode("ascii")
        index, start, first = self._reserve(len(body))
        return self._emit(body, index, start, first)

    def add_verbatim(self, body: bytes, source: unit_cache.PriorFragment) -> tuple[int, int, int]:
        """Add a served fragment's bytes, which the previous surface holds at `source`. If the open part is the previous surface's and the bytes already lie at the offset the writer would use, nothing is written and the part stays as it is. Otherwise the bytes are written as `add` would write them."""
        index, start, first = self._reserve(len(body))
        if self._handle is None and source.part == self._parts[index] and source.start == start:
            self._size = start + len(body)
            return index, start, len(body)
        return self._emit(body, index, start, first)

    def close(self) -> list[str]:
        class_id = self._class_id
        assert class_id is not None, "no class is open"
        if self._open:
            self._end_part()
        if not self._parts:
            path = self._staging(0)
            path.write_bytes(b"[]\n")
            self._parts.append(path)
        names = (
            [f"{class_id}.{index:03d}.json" for index in range(len(self._parts))]
            if self._numbered or len(self._parts) > 1
            else [f"{class_id}.json"]
        )
        for index, (part, name) in enumerate(zip(self._parts, names, strict=True)):
            if isinstance(part, str):
                if part == f"units/{name}":
                    continue
                staging = self._staging(index)
                shutil.copyfile(self._out_dir / part, staging)
                self._parts[index] = part = staging
            self._pending.append((part, self._units_dir / name))
        self._class_id = None
        self._parts = []
        return [f"units/{name}" for name in names]

    def commit(self) -> None:
        for staging, target in self._pending:
            staging.replace(target)
        self._pending = []

    def abort(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        for part in self._parts:
            if isinstance(part, Path):
                part.unlink(missing_ok=True)
        for staging, _target in self._pending:
            staging.unlink(missing_ok=True)
        self._parts = []
        self._pending = []
        self._class_id = None
        self._open = False


def _write_shard(
    out_dir: Path, class_id: str, fragments: list[dict]
) -> tuple[list[str], list[tuple[int, int, int]]]:
    """Write one class's fragments, held as a list, and rename the parts into place at once. Returns the relative part paths in part order and, aligned with `fragments`, each fragment's (part index, byte start, byte length)."""
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
    """Hold one process's freshly drafted fragments on disk between phase 1 and the write. It is a `_ShardWriter` over `<out_dir>/fresh.spool.partial` with one class named for the drafting process (`serial`, or `w<index>` in the pool). The parts use the shard framing, so a fragment's address reads back through `unit_cache.PriorFragmentReader` the same way a served fragment is read from the previous surface. Spooling lets a fresh unit's `EnrichedUnit` be freed as soon as its fragment is on disk, and lets the write treat fresh and served fragments alike: read by address, patched, released. `add` returns the address at once (the class is opened numbered, so the part name is already final) as a `PriorFragment` carrying the drafted stamp, so the read back checks a fresh fragment against its own key as it does a served one. The spool does not keep the address: the caller puts it on the unit's projection, which carries it to the parent's unit store. `close` closes and commits the parts. The runner deletes the spool directory whether the build succeeds or fails."""

    def __init__(self, out_dir: Path, name: str) -> None:
        self._writer = _ShardWriter(Path(out_dir) / FRESH_SPOOL_NAME)
        self._writer.open(name, numbered=True)
        self._name = name

    def add(self, fragment: dict) -> unit_cache.PriorFragment:
        part, start, length = self._writer.add(fragment)
        return unit_cache.PriorFragment(
            f"units/{self._name}.{part:03d}.json", start, length, fragment["id"], fragment.get("content_key")
        )

    def close(self) -> None:
        self._writer.close()
        self._writer.commit()


def _prune_orphan_shards(out_dir: Path, manifest: dict) -> list[str]:
    """Delete the units/*.json files the manifest no longer references: shards of classes that are gone and parts that are no longer used. It runs only after the manifest is written, so a crash mid-build leaves orphan files instead of a manifest that names a deleted shard. It only considers *.json files directly under units/."""
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
    """Return `_cluster_id` computed from the ink diffs' repr as bytes. The hash is fed in three pieces (the tuple's head, the diffs, the closing parenthesis), so the full key string is never built and the diffs' bytes can be freed once hashed. The hashed bytes equal `repr((tuple(configs), class_id, diffs))`, which `rebuild/test_unit_cache.py::test_cluster_id_from_repr_matches_the_tuple_recipe` checks."""
    key = hashlib.sha1(f"({tuple(configs)!r}, {class_id!r}, ".encode())
    key.update(diffs_repr)
    key.update(b")")
    return "c-" + key.hexdigest()[:8]


def _cluster_id(configs, class_id, diffs) -> str:
    """Return the cluster id the in-app docket view groups blank units by: the echo key without the judged pair, so every echo group falls inside one cluster. The repr recipe must not change, so that recorded `c-` ids keep resolving."""
    return _cluster_id_from_repr(configs, class_id, repr(diffs).encode())


@dataclass(frozen=True, slots=True)
class _UnitProjection:
    """The picklable phase-1 result a surface worker returns per unit: what the parent's serial reduces read and what the unit cache persists. It never includes the EnrichedUnit, which is freed when its batch ends; the fragment is drafted and spooled in the same step, and its spool address (`part`, `start`, `length`) is included here. `ordinal` is the unit's row in the parent's unit store (`audit.Unit.ordinal`), so the parent folds the projection in without a lookup. `input_key` is the unit's cache key over its inputs, which the store records so the next build can serve the unit by it. `unit_id` and `content_key` are the drafting's stamp. The ink diffs are sent only as two digests of their repr: `diffs_digest` is the echo key's diff component, and `cluster` is the blank-queue cluster id. The cluster is computed here because everything it depends on is known once the family is assigned: the configs, the diffs, and the final class (the verdict family for an UNMATCHED unit, else the ledger class). It is computed for machine-approved units too, because the store carries it forward and a served unit can become a human unit through a ledger edit alone (its `no_verdict` flipping). Sending digests keeps the parent from holding each unit's diffs repr, which is as long as the diffs, through the units phase."""

    unit_id: str
    input_key: str
    content_key: str
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
    ordinal: int = -1
    part: str = ""
    start: int = 0
    length: int = 0


def _phase1_unit(
    unit, comparator, oracle, enricher, drafter: Drafter, report, spool: _FragmentSpool | None = None
) -> tuple[_UnitProjection, dict, list[str]]:
    """Do one unit's per-unit work: the ink flags and deltas, the enrichment, the drafted fragment (`unit_to_json`), and the drafting-time contract check over it (`check_unit` at `DRAFTED`). Returns the projection the parent's reduces read, the fragment, and the check's complaints, which the caller passes to the write so the build fails there in the same list as the write-time check. With a `spool`, the fragment is spooled as it is drafted and its address is put on the projection; the verification sample passes none and patches the fragment it holds. The deltas go onto the fragment and the projection, never onto the unit, whose `ink_deltas` stays the shared empty mapping in every process. The check runs in whichever process drafts, so the pooled and serial paths check the same way. Drafting here, before the parent's reduces, keeps the batch's shapes in the memo for the drafter's replay."""
    text = "".join(chr(value) for value in unit.codepoint_values)
    diffs = tuple(comparator.config_diff(text, config) for config in unit.configs)
    unit.ink_identical = comparator.ink_identical(text, unit.configs)
    unit.picture_identical = not unit.ink_identical and all(diff == IDENTITY_DIFF for diff in diffs)
    unit.junior_equivalent = not (unit.ink_identical or unit.picture_identical) and oracle.approves(
        unit.configs, text
    )
    ink_deltas = {
        config: delta_digest(diff) for config, diff in zip(unit.configs, diffs) if diff != IDENTITY_DIFF
    }
    mismatch_mark = len(enricher.mismatches)
    enriched = enricher.enrich(unit, report)
    family = assign_family(enriched) if unit.class_id == UNMATCHED_CLASS else ""
    diffs_repr = repr(diffs).encode()
    fragment = unit_to_json(enriched, drafter, final_class=family or None, ink_deltas=ink_deltas)
    address = spool.add(fragment) if spool is not None else None
    projection = _UnitProjection(
        unit_id=unit.unit_id,
        input_key=unit.input_key,
        content_key=fragment["content_key"],
        ink_identical=unit.ink_identical,
        picture_identical=unit.picture_identical,
        junior_equivalent=unit.junior_equivalent,
        ink_deltas=tuple(ink_deltas.items()),
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
        ordinal=unit.ordinal,
        part=address.part if address is not None else "",
        start=address.start if address is not None else 0,
        length=address.length if address is not None else 0,
    )
    return projection, fragment, check_unit(fragment, at=(DRAFTED,))


def _seam_records(seam_rects) -> list[dict]:
    """Return a projection's secondary-seam rects in the shape `patch_fragment` reads, which is also the shape the store persists, so fresh and served units are patched through one code path."""
    return [{"pair": list(pair), "before": before, "after": after} for pair, before, after in seam_rects]


def _recompute_fragment(
    unit, injection, comparator, oracle, enricher, drafter: Drafter, report
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Recompute one sampled served unit from nothing and patch it as the write patches a fresh fragment. The parent's global fields (echo, cluster, promoted class, seam homes) are injected onto the unit copy first, because the copy was taken before the reduces ran. `report` is the unit's result from its chunk's single `Enricher.explain_units` pass (`_released_batches`), settled from the unit's own codepoints and configuration and not read from the cache. Passing it in avoids settling each sampled unit on its own, which would cost one `settle-cases` process per position. Returns the recomputed content key and ink deltas, which the caller compares with what the cache served; the id follows from the key."""
    projection, fragment, _complaints = _phase1_unit(unit, comparator, oracle, enricher, drafter, report)
    unit.echo, unit.cluster, unit.class_id, seam_assign = injection
    hold_stamp(
        patch_fragment(
            fragment,
            unit,
            _seam_records(projection.seam_rects),
            seam_assign,
            ink_deltas=dict(projection.ink_deltas),
        )
    )
    return fragment["content_key"], projection.ink_deltas


def _phase1_batches(enricher: Enricher, units):
    """Yield phase 1's unit batches and release the shared shape memo (`ink.release_shape_memos`) after each one. The batches are the enricher's settlement batches (`Enricher.explain_unit_batches`). The comparator, the oracle, the enricher and the drafter's semantics replay share each (text, config) shaping within a batch, so a process holds one batch's shapes at a time instead of everything it has shaped. The pool worker and the in-process runner both iterate this, so the bound holds on both paths."""
    for unit_batch, reports in enricher.explain_unit_batches(units):
        yield unit_batch, reports
        release_shape_memos()


def _released_batches(items):
    """Yield the verification sample's items in chunks of `EXPLAIN_UNIT_BATCH_SIZE` and release the shape memo after each one. Each chunk is also the settlement batch its units are explained in: one `Enricher.explain_units` pass, matched back to the units by position. Using the enricher's batch width keeps one memo bound for the whole build. This is separate from `_phase1_batches` because the pooled sample's items are `(unit, injection)` pairs. `SURFACE_WORKER_BYTES` assumes the release at this boundary."""
    for chunk in batched(items, EXPLAIN_UNIT_BATCH_SIZE):
        yield chunk
        release_shape_memos()


VERIFICATION_SAMPLE = 200


def _verification_sample(served: Sequence[int], seed: str, size: int = VERIFICATION_SAMPLE) -> list[int]:
    """Return the id words (`UnitStore.id_word`) of the cache-served units this build recomputes from nothing and compares with what it served. `served` is the served units' id words in ascending order, which is the ids' own order. The draw is seeded with the store's environment stamp, a digest of the inputs, so a failure reproduces on a rerun of the same build. Sampling checks a couple of hundred windows on every build, at a cost in the tenths of a second, where a full from-scratch comparison would run only once a cycle."""
    if not served:
        return []
    return random.Random(seed).sample(served, min(size, len(served)))


# Below this many misses, pool startup (spawn plus two font loads per worker) costs more than it saves against a serial pass through the parent's shared shapers. It was set from the measured rates in rebuild/out/cycle-timings.ndjson.
_SIGNATURE_POOL_THRESHOLD = 20_000

_signature_worker_state: dict = {}


def _signature_pool_init(before_font: Path, after_font: Path) -> None:
    _signature_worker_state["comparator"] = InkComparator(before_font, after_font)
    _signature_worker_state["label"] = multiprocessing.current_process().name


def _signature_chunk_digests(pairs: Sequence[tuple[str, str]]) -> tuple[list[str], str, int]:
    """Shape one chunk of the misses through this worker's comparator, in order, and return the digests with the worker's label and its peak RSS so far. The peak is returned with the result because a `multiprocessing.Pool` has no other channel for it (`run_m1.run_oracle` does the same for its shards). The parent keeps the maximum per label, so the record `_record_signature_pool` writes covers each worker's whole life."""
    comparator = _signature_worker_state["comparator"]
    digests = [signature_digest(comparator.signature(text, config)) for text, config in pairs]
    return digests, _signature_worker_state["label"], peak_rss_self_bytes()


def _record_signature_pool(width: int, peaks: dict[str, int]) -> None:
    """Write one kind:"pool" record for a finished signature pool under its own unit name, so `make job-costs` reports what a signature worker held beside the surface worker's figure. It is an observation only: no constant divides memory by it, because the width is bound by cores (`artifact_cycle.signature_job_budget`) and a worker's resident set, one comparator over two fonts, does not grow with the number of signatures. It never raises, for the same reason as `_record_surface_pool`, and a serial pass writes nothing."""
    if not peaks:
        return
    record_pool("signature", width=width, worker_peaks=peaks, controller_peak_bytes=peak_rss_self_bytes())


def signature_text(window: str) -> str:
    """Return the text a window's ink signature is taken over: the window's codepoints as characters. `unit_cache.SIGNATURE_STORE_FORMAT` names this derivation and rebuild/test_review_audit.py checks the two together, so a change here that changes a text needs a format bump."""
    return "".join(chr(value) for value in parse_codepoints(window))


def _resolve_signature_digests(
    table: UnitTable,
    rows: RowColumns,
    keyer: unit_cache.UnitKeyer,
    out_dir: Path,
    before_font: Path,
    after_font: Path,
    repo_root: Path,
    helpers_digest: str,
    signature_jobs: int,
    fresh: bool,
) -> tuple[dict[tuple[str, str], str], dict[str, str], fingerprint.EnvironmentStamp, int, int]:
    """Return the ink-duplicate merge's signature digests, one per row of `signature_rows(table, rows)`. A digest comes from the persisted store when its content key still matches, and the rest are shaped now. The misses are shaped across a spawn pool when `signature_jobs` is above 1 and there are at least `_SIGNATURE_POOL_THRESHOLD` misses, otherwise serially through the parent's shared shapers, whose memo is then released so the parent keeps no shape from this pass into the units phase. The pool width is `signature_jobs`, separate from the units runner's `jobs`: a signature worker holds one comparator over the two fonts, its memory does not grow with the misses, and it is CPU-bound, so cores set its width (`artifact_cycle.signature_job_budget`). The pool maps about eight chunks per worker instead of single pairs so each reply can carry its worker's peak, and `pool.map` keeps the chunks in miss order, which makes the pooled result byte-identical to the serial one. Returns the digests keyed by (codepoints, config), the store entries `build_m1` writes when the units phase starts, the store's environment stamp, the number of rows shaped, and the width they were shaped at (1 for a serial pass or when nothing was shaped)."""
    environment = unit_cache.signature_environment(repo_root, before_font, helpers_digest)
    prior = None if fresh else unit_cache.load_signature_store(out_dir, environment)
    if not fresh and prior is None:
        note = unit_cache.signature_miss_note(out_dir, environment) or unit_cache.UNREADABLE_NOTE
        report = console.say if note == unit_cache.NO_STORE_NOTE else console.warn
        report(f"ink-signature store: {note}", file=sys.stderr)
    views = signature_rows(table, rows)
    keys = {(view.codepoints, view.config): keyer.signature_key(view) for view in views}
    signatures: dict[tuple[str, str], str] = {}
    entries: dict[str, str] = {}
    misses = array("I")
    windows: list[str] = []
    for view in views:
        digest = prior.get(keys[(view.codepoints, view.config)]) if prior else None
        if digest is None:
            misses.append(view.row)
            windows.append(view.codepoints)
        else:
            signatures[(view.codepoints, view.config)] = digest
            entries[keys[(view.codepoints, view.config)]] = digest
    width = 1
    if misses:
        config_at = rows.config_at
        pairs = [(signature_text(window), config_at(index)) for window, index in zip(windows, misses)]
        if signature_jobs > 1 and len(misses) >= _SIGNATURE_POOL_THRESHOLD:
            ctx = multiprocessing.get_context("spawn")
            width = min(signature_jobs, len(misses))
            chunk_width = max(1, len(pairs) // (width * 8))
            with ctx.Pool(
                width, initializer=_signature_pool_init, initargs=(before_font, after_font)
            ) as pool:
                chunks = pool.map(_signature_chunk_digests, batched(pairs, chunk_width), chunksize=1)
            peaks: dict[str, int] = {}
            for _chunk, label, peak in chunks:
                peaks[label] = max(peaks.get(label, 0), peak)
            _record_signature_pool(width, peaks)
            digests: Iterable[str] = chain.from_iterable(chunk for chunk, _label, _peak in chunks)
        else:
            comparator = InkComparator(before_font, after_font, shaper_for)
            digests = [signature_digest(comparator.signature(text, config)) for text, config in pairs]
            release_shape_memos()
        for window, index, digest in zip(windows, misses, digests):
            config = config_at(index)
            signatures[(window, config)] = digest
            entries[keys[(window, config)]] = digest
    return signatures, entries, environment, len(misses), width


class _SignatureWrite:
    """Write the ink-signature store on a background thread during a pooled build's units phase, joined in the cache phase. The entries are final when `_resolve_signature_digests` returns, and the store's stamp (`unit_cache.signature_environment`) reads neither the manifest nor the check, so the write depends on nothing later phases produce. `_write` drops the entries as soon as `unit_cache.write_signature_store` returns, and `build_m1` deletes its own reference right after starting the thread, so the dict is freed with the write instead of staying through the units-to-cache stretch of the step that `SURFACE_PARENT_BYTES` covers. A failed write also drops them, but the stored exception's traceback keeps the write's frames, and the entries with them, until `join` re-raises it; that only happens on a failing build.

    The overlap costs nothing because zlib releases the GIL and a pooled build's parent spends the units phase waiting in `multiprocessing.connection.wait`, waking only to fold a batch reply and hand out the next batch. Starting the thread earlier, when the entries become final, would make the sort and line formatting (which hold the GIL) compete with the end of the load and the plan phase. A serial build has no idle parent, so it writes inline at the same point. The thread is a daemon and is joined only on the success path, so an exception abandons the write instead of waiting for it; a failed write raises at the join. The write's temporary sorted key list, tens of megabytes, falls within the units phase and within the noise of `SURFACE_PARENT_BYTES`. The runner and the signature pool start processes with spawn, not fork, so a live thread at process creation is safe; moving any of this to a fork context would have to account for the thread.
    """

    def __init__(
        self, out_dir: Path, environment: fingerprint.EnvironmentStamp | str, entries: Mapping[str, str]
    ) -> None:
        self._out_dir = out_dir
        self._environment = environment
        self._entries: Mapping[str, str] | None = entries
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._write, name="ink-signature-store", daemon=True)
        self._thread.start()

    def _write(self) -> None:
        try:
            assert self._entries is not None
            unit_cache.write_signature_store(self._out_dir, self._environment, self._entries)
        except BaseException as error:
            self._error = error
        finally:
            self._entries = None

    def join(self) -> None:
        self._thread.join()
        if self._error is not None:
            raise self._error


def _surface_worker(conn, init: dict) -> None:
    """Run one persistent surface worker, answering the parent's messages on `conn` until `stop`. Workers are started with spawn only, because uharfbuzz and fontTools C objects are not fork-safe and `drafts._import_test_shaping` sets a module-global singleton.

    A `phase1` message carries one batch of units from the parent's queue and the spool's class name. For each unit the worker runs config_diff, enrichment, drafting and the drafting-time contract check, releasing the shape memo after each settlement batch (`_phase1_batches`). It spools each fragment as it is drafted (`_FragmentSpool`, opened on the first batch and kept across batches), so no EnrichedUnit outlives its batch. It replies `batch` with the batch's projections, each carrying its fragment's spool address and the unit's ordinal, and the check's complaints (`check_unit` at `DRAFTED`, capped at `CONTRACT_ERRORS_SHOWN`), and keeps nothing after the reply, so it holds one batch's units and projections at a time. `phase1-done` closes the spool and replies `ok`; the parent reads the fragments back by address itself. `verify` recomputes phase 1 and the patch for units the cache served, which this worker never enriched, and replies with each one's content key and freshly computed ink deltas. `stop` replies with the worker's peak RSS.

    Each `batch` reply is sent as its batch finishes, so the parent can print progress while the pool is still working. `verify` sends no progress, since it covers only a couple of hundred units.
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
            subset_pack=init["subset_pack"],
        )
        drafter = Drafter(init["after_font"], repo_root=init["repo_root"], shaper_factory=shaper_for)
        tally = pile_tally.from_environment()
        if tally:
            tally.hold_reading("worker.subset_pack", enricher.subset_pack_census)
            tally.hold_reading("ink.shape_memo", shape_memo_census)
        spool: _FragmentSpool | None = None
        batches = 0
        while True:
            message = conn.recv()
            if message[0] == "stop":
                conn.send(("peak", peak_rss_self_bytes()))
                return
            if message[0] == "phase1":
                if spool is None:
                    spool = _FragmentSpool(init["out_dir"], message[2])
                batches += 1
                results: list[_UnitProjection] = []
                complaints: list[str] = []
                for unit_batch, reports in _phase1_batches(enricher, message[1]):
                    for unit, report in zip(unit_batch, reports):
                        projection, _fragment, errors = _phase1_unit(
                            unit, comparator, oracle, enricher, drafter, report, spool
                        )
                        results.append(projection)
                        _keep_complaints(complaints, errors)
                if tally:
                    tally.hold("worker.projections", results)
                    tally.boundary(f"{message[2]}/phase1-{batches}")
                conn.send(("batch", results, complaints))
                if tally:
                    tally.release("worker.projections")
                del message, results, complaints
            elif message[0] == "phase1-done":
                if spool is not None:
                    spool.close()
                    spool = None
                conn.send(("ok",))
            elif message[0] == "verify":
                keys: dict[str, tuple] = {}
                for chunk in _released_batches(message[1]):
                    reports = enricher.explain_units([unit for unit, _injection in chunk])
                    for (unit, injection), report in zip(chunk, reports, strict=True):
                        keys[unit.unit_id] = _recompute_fragment(
                            unit, injection, comparator, oracle, enricher, drafter, report
                        )
                conn.send(("ok", keys))
    except Exception:
        try:
            conn.send(("error", traceback.format_exc()))
        except Exception:
            pass
    finally:
        conn.close()


def _keep_complaints(kept: list[str], found: Sequence[str]) -> None:
    """Append a fragment's drafting-time complaints to `kept`, up to the `CONTRACT_ERRORS_SHOWN` the write prints. A build in which every fragment fails is stopped by its first page of complaints, so neither a worker's reply nor the parent's list grows with the corpus."""
    room = CONTRACT_ERRORS_SHOWN - len(kept)
    if room > 0:
        kept.extend(found[:room])


def _record_surface_pool(width: int, peaks: dict[str, int]) -> None:
    """Write one kind:"pool" record for a finished surface pool under its own unit name, so `make job-costs` can compare SURFACE_WORKER_BYTES with the workers that ran. It never raises, because failing to write the journal is not a reason to fail a surface build. A build without a pool writes nothing; the parent's own peak is the `surface-build` step peak the cycle already records."""
    if not peaks:
        return
    record_pool("surface", width=width, worker_peaks=peaks, controller_peak_bytes=peak_rss_self_bytes())


PHASE1_UNITS = "units enriched"

# The most fresh units the parent sends a pool worker per `phase1` message. It equals the enricher's settlement batch width, so the hand-out uses the same boundary as the shape-memo release (`_phase1_batches`): a worker holds one batch of units, projections and spool addresses, and a wider hand-out would raise that without changing the memo's bound. It is read through the module global at call time so a test can lower it.
PHASE1_HANDOUT_UNITS = EXPLAIN_UNIT_BATCH_SIZE


def _configuration_order(fresh: Sequence[int], table: UnitTable) -> array:
    """Return the ordinals a pool is handed, stably sorted by the configuration each unit settles under: `UnitTable.config_rank`, which is `audit._config_index` of the unit's first config, the one `Enricher.subset_row` reads the unit's baseline row under. Consecutive batches then share a configuration. A worker's lookups stay within one configuration's key range of the mapped subset pack, which keeps its resident share of the mapping to those pages, and most batches settle under one configuration, so `settle_sequences` makes one kernel call per position instead of one per configuration per position. Within a configuration the load order is kept, and a configuration outside `ACCEPTANCE_CONFIGS` sorts last. Only the pooled paths use this order. The serial path drafts in load order, which is the reference the byte-identity tests compare a pooled build against."""
    return array("I", sorted(fresh, key=table.config_rank))


def _fold_fresh(store: UnitStore, table: UnitTable, projection: _UnitProjection) -> None:
    """Fold one fresh unit's projection into the store at its ordinal, with the unit's ledger exemption read from the table. The projection is the only way the id and the machine flags pass from the worker to the parent, and the store holds them from here on."""
    store.fold_projection(projection, no_verdict=table.no_verdict(projection.ordinal))


def _handout_width(fresh: int, nworkers: int) -> int:
    """Return how many units one `phase1` message carries: `PHASE1_HANDOUT_UNITS`, or fewer when the fresh units would not otherwise reach every worker twice. The ceiling bounds what a worker holds. The smaller width keeps a small build parallel: a dev-loop build of a few thousand fresh units at eight jobs gives each worker a few hundred at a time instead of one worker drawing all of them while the other seven wait. Two draws per worker let the queue even out batches of unequal cost. On the full corpus the ceiling applies."""
    return max(1, min(PHASE1_HANDOUT_UNITS, math.ceil(fresh / (2 * nworkers))))


def _phase_timing(label: str, started: float, note: str = "") -> None:
    """Print the `[t]` line that closes one `review.build` phase, with this process's peak RSS so far (`peak_rss.rss_token`, as on run_m1's phase lines) and, where it can be read, its current resident set (`peak_rss.rss_now_token`), before any note. `make cycle-timings ARGS='--inner'` shows both per phase. The peak only rises, so the first phase whose peak shows the step's figure is the phase that reached it. The current reading shows what the phase leaves resident once its temporary allocations are freed, which the peak cannot separate from the load phase's."""
    tail = rss_token(peak_rss_self_bytes())
    now = current_rss_bytes()
    if now is not None:
        tail += f" {rss_now_token(now)}"
    if note:
        tail += f"\t{note}"
    console.timing(label, time.perf_counter() - started, tail, file=sys.stderr)


class _FreshRunner:
    """Run phase 1 over the units the cache could not serve: in-process when `jobs` is 1, across persistent spawn workers otherwise, with the same per-unit work either way, so serial and parallel builds share every reduce and are byte-identical. The parent keeps the triage order and every order-sensitive reduce (the index and its batches, family promotion, echo grouping, secondary-home resolution) and takes each fresh unit's id from the projection its drafting stamped. The runner enriches, drafts and runs the drafting-time contract check (`_phase1_unit`). It spools each fragment as it is drafted (`_FragmentSpool`, under `out_dir`) so no EnrichedUnit outlives its batch on either path, folds each projection into the parent's unit store as it arrives (`phase1`), keeps the check's complaints in `contract_errors` for the write, and returns fragments one at a time through `fragment`, read from the spool at the address the store holds, as a served fragment is read from the previous surface.

    Pooled, every worker draws batches from one queue (`_handout_width` units at a time, one batch in flight per worker) instead of owning a fixed share, so which worker drafts which unit depends on timing and changes no output byte: `OutlineIntern` keys by shape, not by first-seen order, the parent folds each projection into its ordinal's row, and every order-dependent reduce runs in the parent over the whole store. The queue is in configuration order (`_configuration_order`), so a worker's consecutive batches share a configuration. Its baseline rows come from the subset pack the parent writes before the pool starts (`subset_pack`, mapped read-only by every worker and shared through the page cache), its lookups mostly stay in one configuration's key range, and most batches settle under one configuration. The verification sample is split into contiguous slices of the same order for the same reason. A worker holds its interpreter and shapers, one batch's units, projections and addresses, the rows materialized for that batch, and the pages of the mapping it has touched; `SURFACE_WORKER_BYTES` in rebuild/tools/artifact_cycle.py estimates that peak. `close` deletes the spool however the build ends.
    """

    def __init__(
        self,
        fresh: Sequence[int],
        jobs: int,
        subset_dir: Path,
        before_font: Path,
        after_font: Path,
        junior_font: Path,
        repo_root: Path,
        verify: list | None = None,
        spec_root: Path | None = None,
        *,
        table: UnitTable,
        out_dir: Path,
        subset_pack: Path,
    ) -> None:
        self._fresh = fresh
        self._table = table
        self._verify = list(verify or ())
        self._before_font = before_font
        self._after_font = after_font
        self._junior_font = junior_font
        self._subset_dir = subset_dir
        self._subset_pack = Path(subset_pack)
        self._repo_root = repo_root
        self._spec_root = Path(spec_root) if spec_root is not None else Path(repo_root)
        self._out_dir = Path(out_dir)
        # Delete a spool a killed build left behind, so the directory holds only what this build writes.
        shutil.rmtree(self._out_dir / FRESH_SPOOL_NAME, ignore_errors=True)
        self.contract_errors: list[str] = []
        self._reader: unit_cache.PriorFragmentReader | None = None
        self._local: tuple | None = None
        self._procs: list = []
        self._conns: list = []
        # The verification sample is worker work too, and it is all of the work when the cache served every unit. Sizing the pool on the fresh units alone would make a no-change rebuild recompute its sample in the parent. That is slower (200 units serially against eight workers: measured 55.6 s against 42.4 s for the units phase of a fully served build) and adds to the parent's peak, since the parent, which already holds the corpus's table and store, would also build an enricher, its shapers and their memos.
        workload_size = max(len(fresh), len(self._verify))
        if jobs > 1 and workload_size > 1:
            nworkers = min(jobs, workload_size)
            self._handout = _handout_width(len(fresh), nworkers)
            init = {
                "before_font": before_font,
                "after_font": after_font,
                "junior_font": junior_font,
                "subset_dir": subset_dir,
                "subset_pack": self._subset_pack,
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

    def phase1(self, store: UnitStore) -> None:
        """Enrich and draft every fresh unit, folding each projection into `store` at its ordinal as it arrives (`_fold_fresh`). The parent holds no projection past the reply that carried it and no unit record past the hand-out that materialized it: the fresh units are ordinals, and a `Unit` is built from the table and the store (`UnitTable.unit`) for each batch sent to a worker, or one at a time on the serial path. Pooled, each worker spools its batches under its own class name and replies per batch with projections carrying spool addresses, which are folded here as they arrive (`_drive_phase1`), with at most one reply per worker in flight. Serial, the same loop runs here over one spool, at the enricher's batch width with the memo released after each batch. Either way the EnrichedUnit is freed by the time its batch closes."""
        if self._conns:
            self._drive_phase1(store)
        elif self._fresh:
            comparator, oracle, enricher, drafter = self._in_process()
            spool = _FragmentSpool(self._out_dir, "serial")
            table = self._table
            done = 0
            materialized = (table.unit(ordinal, store) for ordinal in self._fresh)
            for unit_batch, reports in _phase1_batches(enricher, materialized):
                for unit, report in zip(unit_batch, reports):
                    projection, _fragment, errors = _phase1_unit(
                        unit, comparator, oracle, enricher, drafter, report, spool
                    )
                    _fold_fresh(store, table, projection)
                    _keep_complaints(self.contract_errors, errors)
                done += len(unit_batch)
                self._count(done)
            spool.close()

    @property
    def pooled(self) -> bool:
        """Whether this runner drives a worker pool, in which case the parent spends the units phase waiting in `wait` instead of shaping."""
        return bool(self._conns)

    def fragment(self, source: unit_cache.PriorFragment) -> dict:
        """Return one fresh unit's fragment, read from the spool at `source`, the address phase 1 folded into the unit store. It uses the same reader that reads a served fragment from the previous surface, so the write can request fresh and served fragments in shard order and hold one at a time. The fragment comes back as drafted, placeholders included; the caller patches it."""
        if self._reader is None:
            self._reader = unit_cache.PriorFragmentReader(self._out_dir / FRESH_SPOOL_NAME)
        return self._reader.read(source)

    def _count(self, done: int) -> None:
        console.progress(done, len(self._fresh), PHASE1_UNITS, file=sys.stderr)

    def hold_piles(self, tally: pile_tally.PileTally) -> None:
        """Register with the debug tally the one pile this runner holds in the parent: the serial path's subset-pack mapping, once its enricher exists. Pooled, the workers map the pack and tally it at their own batch boundaries, and the parent's reading stays at zero. The spool addresses are unit-store columns, which the store reports itself."""
        tally.hold_reading(
            "runner.subset_pack", lambda: self._local[2].subset_pack_census() if self._local else (0, 0)
        )

    def _drive_phase1(self, store: UnitStore) -> None:
        """Hand the fresh units to the pool one batch at a time and fold each reply as it arrives, instead of collecting from one worker at a time, so progress reaches the terminal while the phase runs. Each worker starts with one batch and has at most one in flight, so a worker holds one batch of units and the parent holds at most one reply per worker. `wait` returns the connections that have data. A `batch` reply's projections are folded into `store` and its complaints into `contract_errors`, the reply is dropped, and that worker gets the next batch, materialized from the table and the store as it is sent, or the end marker once the queue is empty. The phase ends when every worker has answered the end marker with `ok`. The printed count is the sum of the batches folded. An `error` reply raises here, and `close()` drains the replies queued behind it."""
        table = self._table
        handouts = batched(_configuration_order(self._fresh, table), self._handout)
        names = {conn: f"w{index}" for index, conn in enumerate(self._conns)}

        def hand(conn) -> None:
            batch = next(handouts, None)
            if batch is None:
                conn.send(("phase1-done",))
            else:
                conn.send(("phase1", tuple(table.unit(ordinal, store) for ordinal in batch), names[conn]))

        for conn in self._conns:
            hand(conn)
        waiting = list(self._conns)
        done = 0
        while waiting:
            for conn in cast(list, multiprocessing.connection.wait(waiting)):
                reply = conn.recv()
                if reply[0] == "batch":
                    for projection in reply[1]:
                        _fold_fresh(store, table, projection)
                    _keep_complaints(self.contract_errors, reply[2])
                    done += len(reply[1])
                    del reply
                    self._count(done)
                    hand(conn)
                elif reply[0] == "error":
                    raise RuntimeError("surface worker failed in phase 1:\n" + reply[1])
                else:
                    waiting.remove(conn)

    def _in_process(self) -> tuple:
        """Return the comparator, oracle, enricher and drafter for in-process work, built on first use. They are built lazily because, when the cache served every unit, the verification sample can be the only work, and they are not needed before it runs."""
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
                subset_pack=self._subset_pack,
            )
            drafter = Drafter(self._after_font, repo_root=self._repo_root, shaper_factory=shaper_for)
            self._local = (comparator, oracle, enricher, drafter)
        return self._local

    def verify(self, injections: dict[str, tuple]) -> dict[str, tuple[str, tuple[tuple[str, str], ...]]]:
        """Recompute phase 1 and the patch for the sampled served units, and return each unit's content key with the ink deltas the recomputation found. Nothing is read from the cache: each chunk is settled in one fresh explain pass over the units' own codepoints, followed by a fresh config_diff, enrichment and drafts per unit. The result is what this build would have written had the unit missed the cache. The caller compares the key with the stamp on the served fragment and the deltas with the store record they were served from, since `ink_deltas` is outside the key's projection."""
        keys: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {}
        if not self._verify:
            return keys
        if self._conns:
            by_ordinal = {unit.ordinal: unit for unit in self._verify}
            ordered = _configuration_order(list(by_ordinal), self._table)
            shares = batched(
                [by_ordinal[ordinal] for ordinal in ordered], math.ceil(len(self._verify) / len(self._conns))
            )
            for conn in self._conns:
                share = next(shares, ())
                conn.send(("verify", [(unit, injections[unit.unit_id]) for unit in share]))
            for conn in self._conns:
                reply = conn.recv()
                if reply[0] == "error":
                    raise RuntimeError("surface worker failed while verifying a served unit:\n" + reply[1])
                keys.update(reply[1])
        else:
            comparator, oracle, enricher, drafter = self._in_process()
            for chunk in _released_batches(self._verify):
                reports = enricher.explain_units(chunk)
                for unit, report in zip(chunk, reports, strict=True):
                    keys[unit.unit_id] = _recompute_fragment(
                        unit, injections[unit.unit_id], comparator, oracle, enricher, drafter, report
                    )
        return keys

    def close(self) -> None:
        """Stop every worker, collect each one's peak, join the processes, and delete the fresh spool. It is called from a `finally`, so the failing path matters most. A phase that raises inside its recv loop leaves the later connections holding that phase's replies (a `batch` with its payload, and the `ok` after it). The stop reply is tagged `peak`, and this loop drains whatever is queued ahead of it: reading a phase's payload as a peak would raise out of the `finally`, hide the worker's traceback, and skip the join below with spawn workers still running. The spool is deleted last, after every reader and worker that could hold one of its parts open is done."""
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


@dataclass(frozen=True, slots=True)
class _Emission:
    """One unit as the write receives it. Either `fragment` is set, a fragment to serialize (fresh from the spool, or served but patched again because a field the reduces or the ledger write changed), or `body` is set, the bytes of a served fragment the previous surface holds exactly as this build would write them, with `source`, the address they are at, and `identity`, the slim identity the cross-unit checks and the sidecars read. `content_key` and `policy_file` come from the fragment or the store record alike; the store keeps them past the write."""

    unit_id: str
    content_key: str
    policy_file: str | None
    fragment: dict | None = None
    body: bytes | None = None
    source: unit_cache.PriorFragment | None = None
    identity: dict | None = None


class _SidecarSpool:
    """Spool the three sidecars' lines to disk while the shards are written, and write the stamped files once the manifest exists. Each sidecar's header carries the manifest's identity, and the manifest can only be written once every class's part list is known, so the lines wait in `.partial` files beside the files they become. That keeps them out of the parent's memory. `finish` writes the real files through the same writers `write_index` and `write_app_artifacts` use, so the bytes match a build that held every fragment. `discard` deletes the spools whether or not `finish` ran.

    A unit served verbatim is projected without parsing it. Its line in the previous surface's index, and its row in the previous app index if it is a human unit, are read in step through `unit_index.LineCursor` (both files are in shard order, and a served unit keeps its place in it). They are respooled with this build's queue position and shard address substituted (`unit_index.respool_index_line`, `app_index.respool_app_line`); every other field is the fragment's own and unchanged. Its locator row needs only its id, class and address. The cursors are opened only when the previous sidecars are stamped for the manifest beside them, and a unit a cursor cannot reach is parsed instead, which gives the same bytes at a higher cost.
    """

    def __init__(self, out_dir: Path, *, respool: bool = False) -> None:
        out_dir = Path(out_dir)
        self._paths = {
            name: out_dir / f"{name}.spool.partial"
            for name in (unit_index.INDEX_NAME, app_index.APP_INDEX_NAME, app_index.LOCATOR_NAME)
        }
        self._handles = {name: path.open("wb") for name, path in self._paths.items()}
        self._index_cursor: unit_index.LineCursor | None = None
        self._app_cursor: unit_index.LineCursor | None = None
        if (
            respool
            and unit_index.index_is_current(out_dir)
            and app_index.artifact_is_current(out_dir, app_index.APP_INDEX_NAME, app_index.APP_INDEX_FORMAT)
        ):
            self._index_cursor = unit_index.LineCursor(unit_index.index_path(out_dir))
            self._app_cursor = unit_index.LineCursor(
                app_index.artifact_path(out_dir, app_index.APP_INDEX_NAME)
            )
        self.human = 0
        self.machine = 0
        self.respooled = 0

    def unit(self, fragment: dict, span: tuple[int, int, int], order: int | None, batch: int | None) -> None:
        self._handles[unit_index.INDEX_NAME].write(unit_index.index_line(fragment, order=order, batch=batch))
        if order is None or batch is None:
            self._handles[app_index.LOCATOR_NAME].write(app_index.locator_line(fragment, *span))
            self.machine += 1
        else:
            self._handles[app_index.APP_INDEX_NAME].write(
                app_index.app_line(fragment, *span, order=order, batch=batch)
            )
            self.human += 1

    def served(
        self, emission: _Emission, span: tuple[int, int, int], order: int | None, batch: int | None
    ) -> None:
        assert emission.body is not None and emission.identity is not None
        unit_id = emission.unit_id
        line = self._index_cursor.take(unit_id) if self._index_cursor is not None else None
        row = None
        if line is not None and order is not None and batch is not None and self._app_cursor is not None:
            row = self._app_cursor.take(unit_id)
        if line is None or (order is not None and row is None):
            self.unit(json.loads(emission.body), span, order, batch)
            return
        self._handles[unit_index.INDEX_NAME].write(
            unit_index.respool_index_line(line, unit_id=unit_id, order=order, batch=batch)
        )
        if order is None or batch is None:
            self._handles[app_index.LOCATOR_NAME].write(app_index.locator_line(emission.identity, *span))
            self.machine += 1
        else:
            assert row is not None
            part, start, length = span
            self._handles[app_index.APP_INDEX_NAME].write(
                app_index.respool_app_line(
                    row, unit_id=unit_id, order=order, batch=batch, part=part, start=start, length=length
                )
            )
            self.human += 1
        self.respooled += 1

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
        for cursor in (self._index_cursor, self._app_cursor):
            if cursor is not None:
                cursor.close()
        for path in self._paths.values():
            path.unlink(missing_ok=True)


@dataclass(frozen=True)
class _WrittenSurface:
    """What `_write_surface` returns besides the manifest: how many units were written without parsing, and how many sidecar rows were respooled from the previous sidecars. The per-unit values the build reads after the fragments are gone are written into the unit store's columns during the write instead: each unit's `config_note`, which the census facts histogram, and its shard address and policy-draft file, which the unit-cache store records so the next build's plan can serve the fragment without walking the shard and check it without parsing it."""

    manifest: dict
    verbatim: int
    respooled: int


class _StoreNotes(Mapping[int, str | None]):
    """Each unit's `config_note` by ordinal, as `census.build_facts` reads it, read from the unit store's column instead of a separate dict. The census has each unit's ordinal at every lookup, so the column is indexed directly without any id lookup."""

    __slots__ = ("_store",)

    def __init__(self, store: UnitStore) -> None:
        self._store = store

    def __getitem__(self, ordinal: int) -> str | None:
        return self._store.config_note(ordinal)

    def __iter__(self) -> Iterator[int]:
        return iter(range(len(self._store)))

    def __len__(self) -> int:
        return len(self._store)


class _ServedIds:
    """The ids the unit cache served this build, as the collection `_SurfaceCheck` checks membership in to skip `check_unit` on a fragment that passed it in the build that drafted it. Membership reads the store's served flag through a bisect of the id index, instead of a set of one string per served unit, which on a served pass would be the whole corpus. With no served units it returns False without the bisect. `__len__` and `__iter__` exist because the checker's parameter is a `Collection[str]`; only membership is used. The m1 write does not use even that, since it passes the checker each unit's served flag from the store directly, and it decides whether there is a previous surface from the served count."""

    __slots__ = ("_store", "_count")

    def __init__(self, store: UnitStore, count: int) -> None:
        self._store = store
        self._count = count

    def __contains__(self, unit_id: object) -> bool:
        if not self._count or not isinstance(unit_id, str):
            return False
        try:
            ordinal = self._store.ordinal_of(unit_id)
        except KeyError:
            return False
        return self._store.flags(ordinal).served

    def __iter__(self) -> Iterator[str]:
        store = self._store
        return (store.unit_id(ordinal) for ordinal in range(len(store)) if store.flags(ordinal).served)

    def __len__(self) -> int:
        return self._count


def _prior_parts(out_dir: Path) -> dict[str, list[str]]:
    """Return the previous surface's shard parts per class, read from the manifest beside them, for the shard writer to write each class against; empty when there is no readable manifest."""
    try:
        manifest = json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))
        return {meta["id"]: unit_index.class_shards(meta) for meta in manifest["classes"]}
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return {}


def _write_surface(
    out_dir: Path,
    table: UnitTable,
    order: Sequence[int],
    row_total: int,
    classes: list,
    by_class: Mapping[str, Sequence[int]],
    fragments: Callable[[Iterable[int]], Iterator[_Emission]],
    store: UnitStore,
    served: int,
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
    unit_errors: Sequence[str],
    font_digests: Mapping[str, str],
    tally: pile_tally.PileTally | None = None,
    spec_root: Path | None = None,
) -> _WrittenSurface:
    """Stream the per-unit fragments into shards, copy the fonts, and write the manifest and the sidecars. The manifest's triage index, `human_unit_ids`, is the human units taken in `order`, the permutation `audit.sort_for_triage` returned; a batch is a slice of it. Every per-unit value read here is a column of `table` or `store`, indexed by ordinal; no unit record is materialized on this side of `fragments`.

    `fragments` is called once, for every ordinal in shard order: classes in `unit_index.class_shard_key` order, which is also the sidecars' order, and each class's units by id (`store.id_word`, which sorts like the id strings). So a class's fragments and its locator rows ascend together, and a fresh unit is placed by its content, not by its queue position. Each fragment is written, checked, projected onto the sidecar spools and released before the next one is read, so the parent holds one fragment at a time. What remains of a fragment afterward is its shard address, config note and policy-draft file (written into `store` at its ordinal), the checker's per-unit identity for the cross-unit predicates, and its sidecar lines on disk. Its content key must equal the one the store already holds for the unit, since the fragment was drafted or served under it.

    `check_shards`' predicates run over the fragments as they pass, through the same `_SurfaceCheck` the whole-surface form uses, at `PATCHED` only plus every cross-unit predicate (`check_unit` describes the two subsets). `unit_errors` holds what the drafting-time check found, and those errors fail the build here in the same `contract check failed` list as the write's own. `served` is the number of units the cache served. It gives the checker `_ServedIds` (see `check_shards`) and decides whether the shards are written against the previous surface.

    The manifest predicates (`check_manifest`) and the output-file predicates (`_check_output_files`) do not run here: every field they read is written by this function from its own inputs, and the fonts are checked against the digests taken at load in `_copy_font`. `check_output_dir` runs them over a real build in the contracts lane (`rebuild/test_app_index.py` over the mini bundle, `rebuild/test_review_build.py` over a table diff), and `refresh_assets` runs the file predicates over the surface it restamps.
    """
    ordered = sorted(classes, key=lambda entry: unit_index.class_shard_key(entry.id))
    by_id = {entry.id: array("I", sorted(by_class.get(entry.id, ()), key=store.id_word)) for entry in ordered}
    stream = fragments(chain.from_iterable(by_id[entry.id] for entry in ordered))
    meta_by_id: dict[str, dict] = {}
    verbatim = 0
    check = _SurfaceCheck(
        mode="m1-audit",
        descriptions=FEATURE_DESCRIPTIONS,
        batch_size=batch_size,
        repo_root=repo_root,
        served_ids=_ServedIds(store, served),
        at=(PATCHED,),
    )
    if tally:
        tally.hold("checker.identity", check._identity, packed=_packed_shape("checker.identity"))
    prior_parts = _prior_parts(out_dir) if served else {}
    writer = _ShardWriter(out_dir)
    spool = _SidecarSpool(out_dir, respool=bool(served))
    try:
        for entry in ordered:
            ordinals = by_id[entry.id]
            channels = {channel: 0 for channel in MACHINE_CHANNELS}
            for ordinal in ordinals:
                channel = store.machine_channel(ordinal)
                if channel is not None:
                    channels[channel] += 1
            meta = {
                "id": entry.id,
                "status": entry.status,
                "ink_identical": entry.ink_identical,
                "no_verdict": entry.no_verdict,
                "why": entry.why,
                "unit_count": len(ordinals),
                "row_count": sum(map(table.row_count, ordinals)),
                "machine_approved_count": sum(channels.values()),
                # The app draws a class's machine fold, its count and its badge, before opening the class, and under the slim app index those units are not loaded. So the per-channel counts the badge is chosen from are recorded here.
                "machine_channels": channels,
                "shards": [],
                "batches": sorted({batch for batch in map(table.batch, ordinals) if batch is not None}),
            }
            check.class_start(meta)
            writer.open(entry.id, prior_parts.get(entry.id, ()))
            spans: list[tuple[int, int, int]] = []
            for ordinal in ordinals:
                emission = next(stream)
                assert emission.unit_id == store.unit_id(ordinal), (emission.unit_id, ordinal)
                if emission.fragment is not None:
                    fragment = emission.fragment
                    span = writer.add(fragment)
                    check.unit(fragment, served=store.flags(ordinal).served)
                    spool.unit(fragment, span, table.order(ordinal), table.batch(ordinal))
                    store.set_config_note(ordinal, fragment["config_note"])
                else:
                    assert emission.body is not None and emission.source is not None
                    assert emission.identity is not None
                    span = writer.add_verbatim(emission.body, emission.source)
                    check.unit(emission.identity, served=True)
                    spool.served(emission, span, table.order(ordinal), table.batch(ordinal))
                    store.set_config_note(ordinal, config_note(table.configs(ordinal), ACCEPTANCE_CONFIGS))
                    verbatim += 1
                spans.append(span)
                assert emission.content_key == store.content_key_hex(ordinal), emission.unit_id
                store.set_policy_file(ordinal, emission.policy_file)
            meta["shards"] = writer.close()
            for ordinal, (part, start, length) in zip(ordinals, spans, strict=True):
                store.set_written_address(ordinal, (meta["shards"][part], start, length))
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
        machine_units = (
            (table.class_id(ordinal), table.row_count(ordinal), channel)
            for ordinal in order
            if (channel := store.machine_channel(ordinal)) is not None
        )
        manifest = {
            "format": MANIFEST_FORMAT,
            "mode": "m1-audit",
            "generated_at": _generated_at(audit_path, ledger_path, before_font, after_font),
            "repo_head": _repo_head(repo_root),
            "inputs_fingerprint": _inputs_fingerprint(
                repo_root, subset_dir, before_font, junior_font, spec_root
            ),
            "source": {
                "audit": _relative(audit_path, repo_root),
                "ledger": _relative(ledger_path, repo_root),
            },
            "fonts": fonts,
            "alphabet": _alphabet_meta(),
            "configs": list(ACCEPTANCE_CONFIGS),
            "feature_descriptions": dict(FEATURE_DESCRIPTIONS),
            "batch_size": batch_size,
            "human_unit_ids": [
                store.unit_id(ordinal) for ordinal in order if table.batch(ordinal) is not None
            ],
            "totals": {
                "units": table.n,
                "rows": row_total,
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
    # A unit whose re-settled cells disagree with the audit it was built from would describe a font nobody compiled, so any mismatch fails the build.
    errors: list[str] = []
    if mismatches:
        errors.append(
            f"enricher: re-settled cells diverge from the audit in {len(mismatches)} units "
            f"(first: {mismatches[0]})"
        )
    errors.extend(unit_errors)
    errors.extend(check.finish(manifest))
    if errors:
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:CONTRACT_ERRORS_SHOWN]))
    return _WrittenSurface(manifest, verbatim, spool.respooled)


def row_columns_census(rows: RowColumns | None) -> pile_tally.Measure:
    """Return the row columns' exact reading for the debug tally (`pile_tally.column_census`): the live rows as the count, and the five arrays plus the tuple pool at its packed size (orphaned runs included) as the bytes. The string table the columns share with the workload table is printed beside them and counted under `workload.units`. After `release_rows` drops the columns the reading is empty, so the line still appears at every boundary after the load. The pool is sealed by the time a tally reads it, so beyond its packed size it holds only its id list, one pointer per tuple, and the tuples, which the workload table's units use as their `baseline` and `new` until `UnitTable.release_names`."""
    if rows is None:
        return pile_tally.Measure(0, 0, pile_tally.PackedCost(0, 0, 0))
    return pile_tally.column_census(
        rows.live, rows.columns(), rows.table, pile_tally.pool_bytes(rows.names), holds_table=False
    )


def unit_table_census(table: UnitTable) -> pile_tally.Measure:
    """Return the workload table's exact reading for the debug tally, under `workload.units`: the rows as the count, and as the bytes the table's arrays plus every pool a side column indexes at its packed size. The pools are the config and kind tuples, the render groups, the class maps, and the name tuples until `release_names` drops them, so the reading drops between the plan and units boundaries. The walked figure includes the string table and the packed figure prints it beside the rows. It is the build's only string table, which the row columns, the unit store and the pre-merge snapshot also index, so this is the one line that counts it. The line reads `ratio=1.00` on the corpus, since the table is columns; the only memory it adds past the plan is the pools and the string table, which includes the store's digests and cluster ids."""
    pools = sum(pile_tally.pool_bytes(pool) for pool in table.pools())
    return pile_tally.column_census(table.n, table.columns(), table.strings, pools)


def premerge_census(snapshot: census.PremergeSnapshot) -> pile_tally.Measure:
    """Return the pre-merge snapshot's exact reading for the debug tally, under `census.premerge`: one row per pre-merge unit, with the snapshot's own columns (its window offsets only; the values are the table's) as the bytes. The string table it indexes is the workload table's, printed beside the rows and counted under `workload.units`."""
    return pile_tally.column_census(snapshot.n, snapshot.columns(), snapshot.strings, holds_table=False)


@lru_cache(maxsize=None)
def _packed_shape(pile: str) -> pile_tally.Shape:
    """Return the packed row the debug tally measures one member of the named pile against (`pile_tally.hold(..., packed=)`; the pile_tally module docstring defines the terms). Each shape matches the record the parent holds, field for field: `unit_cache.ServedUnit` under `unit_cache.unplaced`, the input-key-to-id map under `unit_cache.keys`, and the checker's identity triple under `checker.identity`. The rest of the per-unit state is columns, measured exactly elsewhere: the workload table under `workload.units` (`unit_table_census`), the unit store under `unit_store`, the audit's row columns under `workload.rows`, and the pre-merge snapshot under `census.premerge`.

    The widths are the unit store's own. Each flag is a bit of one flag byte. A window is a count byte and a `u16` per codepoint, measured from the value since a window is two to four cells. A pair's two cell indices take two bytes. A span is two `u16`, and a seam-rect edge three `i32`. Every interned name (the class, the configs, the glyph and cell names, the seam tokens, the diff and delta digests, the cluster, the echo, a shard part's name, a policy file) is a `u32` id into one string table. A sha256 content or input key is its 32 raw bytes, and a content id the 8 raw bytes it is cut from (`unit_cache.unit_id_for`). Every variable-length field (names, deltas, seam rects, homes) is an offset and count into a side column, so an empty one costs the pair. The shape measures `mismatches` the same way, although the store keeps those lines as tuples in a dict keyed by ordinal.

    A unit's own id costs nothing where the pile is keyed by it, because a packed store indexes by ordinal; that applies to the checker's identity. `unit_cache.keys` does count its key, since it maps the input key to the id and the plan's key map needs an index for that lookup. `unit_cache.unplaced` holds the records a served plan received without an address, buffered whole until the walk over the previous shards places them (none, on a store this code wrote), and each carries its `key` as a digest column because the plan finds the record's unit through it. The shapes are constant and requested at every boundary a pile is held at, so one is built per pile name and cached.
    """
    flag = pile_tally.Flag()
    name = pile_tally.Id()
    names = pile_tally.Many(name)
    digest = pile_tally.Hex()
    content_id = pile_tally.Slot(8)
    window = pile_tally.Derived(lambda text: 1 + 2 * len(parse_codepoints(text)))
    pair = pile_tally.Slot(2)
    labeled = pile_tally.Table(name, name)
    spans = pile_tally.Many(pile_tally.Slot(4))
    cell_pairs = pile_tally.Many(pair)
    address = pile_tally.Positional((name, pile_tally.Slot(4), pile_tally.Slot(4)))
    edge = pile_tally.Keyed(
        {"x_min": pile_tally.Slot(4), "x_max": pile_tally.Slot(4), "advance_total": pile_tally.Slot(4)}
    )
    seam_rects = pile_tally.Many(pile_tally.Keyed({"pair": pair, "before": edge, "after": edge}))
    served = pile_tally.Record(
        {
            "key": digest,
            "prior_id": content_id,
            "prior_class": name,
            "content_key": digest,
            "slim": flag,
            "address": address,
            "ink_identical": flag,
            "picture_identical": flag,
            "junior_equivalent": flag,
            "ink_deltas": labeled,
            "diffs_digest": name,
            "cluster": name,
            "family": name,
            "pair_codepoints": pair,
            "echo": name,
            "exemplar": flag,
            "no_verdict": flag,
            "homes": pile_tally.Many(pile_tally.Positional((content_id, flag))),
            "policy_file": name,
            "seam_rects": seam_rects,
            "mismatches": names,
            "pair": pair,
            "after_spans": spans,
            "after_cells": names,
            "after_seams": names,
            "before_spans": spans,
            "before_glyphs": names,
            "before_seams": names,
            "seam_pairs": cell_pairs,
        }
    )
    shapes: dict[str, pile_tally.Shape] = {
        "unit_cache.keys": pile_tally.Table(digest, content_id),
        "unit_cache.unplaced": served,
        "checker.identity": pile_tally.Positional((window, flag, flag)),
    }
    return shapes[pile]


def _slim_for(no_verdict: bool, cached: unit_cache.ServedUnit) -> bool:
    """Whether this build would write the unit's fragment slim, decided before phase 1 from the store record. The record's machine flags depend only on inputs the input key and the environment stamp cover, so they hold for this build. The exemption comes from this build's ledger (the table's `no_verdict`), which no key covers, so it can change while the key stays the same. The plan compares the result with the record's `slim` flag and serves the fragment only when they agree."""
    return cached.ink_identical or cached.picture_identical or cached.junior_equivalent or no_verdict


def _policy_file(fragment: Mapping) -> str | None:
    """The rune file a fragment's policy draft names, or None. It is the only drafts field the cross-unit check reads, and the store keeps it so a fragment served verbatim need not be parsed to supply it."""
    policy = (fragment.get("drafts") or {}).get("policy") or {}
    file = policy.get("file")
    return file if isinstance(file, str) else None


def _served_identity(table: UnitTable, store: UnitStore, ordinal: int, seam_assign) -> dict:
    """The dict that stands in for a verbatim-served fragment when `_SurfaceCheck.unit` and the locator row read it. It carries every field the cross-unit predicates and the locator read, taken from the workload table and the unit store, so a served unit is checked against its neighbors on every build without parsing its fragment or materializing a unit record."""
    pair = store.cell_pair(ordinal)
    policy_file = store.policy_file(ordinal)
    ink_identical, picture_identical, junior_equivalent = store.machine_flags(ordinal)
    configs = table.configs(ordinal)
    return {
        "id": store.unit_id(ordinal),
        "ink_identical": ink_identical,
        "picture_identical": picture_identical,
        "junior_equivalent": junior_equivalent,
        "no_verdict": table.no_verdict(ordinal),
        "echo": table.echo(ordinal),
        "cluster": table.cluster(ordinal),
        "class": table.class_id(ordinal),
        "group": table.group(ordinal),
        "codepoints": table.codepoints_text(ordinal),
        "configs": list(configs),
        "config_gate": config_gate(configs, ACCEPTANCE_CONFIGS),
        "pair": {"left": pair[0], "right": pair[1]} if pair else None,
        "secondary_seams": [{"home": home} for home, suppressed in seam_assign if not suppressed] or None,
        "drafts": {"policy": {"file": policy_file}} if policy_file else None,
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
    signature_jobs: int = 1,
    fresh_unit_cache: bool = False,
    spec_root: Path | None = None,
    subset_pack: Path | None = None,
) -> dict:
    # The enricher re-settles every window from the spec, so a frozen bundle of audit rows, subsets and a font stays consistent only while the runes match it. `spec_root` lets such a bundle use its own frozen spec (the objects rebuild/review/fixtures/mini/pin.json names, which the `mini_bundle` fixture in rebuild/conftest.py materializes out of git), so rune edits do not affect it. What is read from the spec follows `spec_root`: the enrichment, the unit cache's family keys, the environment stamp's spec lines, and the `explain_prose` fingerprint component, because the explain text quotes the spec's refuse and ledger rationales. What describes this checkout stays on `repo_root`: the other fingerprint components, the git head, the manifest's relative paths, and the rest of the environment stamp.
    spec_root = Path(spec_root) if spec_root is not None else Path(repo_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    missing_subsets = [
        config
        for config in ACCEPTANCE_CONFIGS
        if not (subset_dir / f"baseline-{config}.subset.tsv.gz").is_file()
        or (subset_dir / f"baseline-{config}.subset.tsv.gz").stat().st_size == 0
    ]
    # Every acceptance config's table goes into the pack, so a missing one fails the build before any work starts.
    if missing_subsets:
        raise SystemExit(
            f"missing or empty baseline subset tables under {subset_dir}: {', '.join(missing_subsets)}"
        )
    # The pack is written before the pool starts, so no worker races the write. Its table digests are also the environment stamp's `subsets` line, so each table is hashed once for both.
    subset_digests = table_digests(subset_dir, ACCEPTANCE_CONFIGS)
    subset_pack = ensure_pack(subset_dir, ACCEPTANCE_CONFIGS, subset_digests, subset_pack)

    tally = pile_tally.from_environment()
    console.phase("review.build load", file=sys.stderr)
    phase = time.perf_counter()
    workload = load_workload(audit_path, ledger_path, dict(LETTERS))
    table = workload.table
    rows = workload.rows
    assert rows is not None
    if tally:
        tally.hold_reading("workload.units", lambda: unit_table_census(table))
        tally.hold_reading("workload.rows", lambda: row_columns_census(workload.rows))
    if not table.n:
        raise SystemExit(
            f"{audit_path} records no divergent rows, so there is nothing to build a review surface over"
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(spec_root)
    font_digests = {"before": _sha256(before_font), "after": _sha256(after_font)}
    family_keys, helpers_digest = unit_cache.family_content_keys(spec_root, spec, after_font)
    keyer = unit_cache.UnitKeyer(family_keys, dict(LETTERS))
    signatures, signature_entries, signature_environment, signatures_shaped, signature_width = (
        _resolve_signature_digests(
            table,
            rows,
            keyer,
            out_dir,
            before_font,
            after_font,
            repo_root,
            helpers_digest,
            signature_jobs,
            fresh_unit_cache,
        )
    )

    def ink_sig(text: str, config: str) -> str:
        return signatures[(format_codepoints(tuple(ord(ch) for ch in text)), config)]

    exempt_classes = {entry.id for entry in workload.ledger if entry.no_verdict}
    premerge_capture = census.capture_premerge(table)
    signature_count = len(signatures)
    if tally:
        signature_reading = pile_tally.estimate(signatures)
        tally.hold_reading("signatures", lambda: signature_reading)
        tally.hold_reading("census.premerge", lambda: premerge_census(premerge_capture))
    merge_ink_duplicate_units(table, rows, ink_sig, exempt_classes)
    del signatures, ink_sig
    # The merge marked the absorbed rows. Compacting drops them and renumbers the survivors, so from here a table row index is the unit's ordinal, which the unit store below uses too. The pre-merge snapshot records the compaction so it can find each pre-fold row's survivor.
    premerge_capture.rebase(table.compact())
    present = table.classes_present()
    workload.classes_present = [entry for entry in workload.ledger if entry.id in present]
    if tally:
        tally.hold_reading("ink.shape_memo", shape_memo_census)
        tally.boundary("load")
        tally.release("signatures")
    _phase_timing(
        "review.build load",
        phase,
        f"(signatures: {signature_count - signatures_shaped:,} cached, {signatures_shaped:,} shaped"
        + (
            ")"
            if not signatures_shaped
            else " serially)" if signature_width == 1 else f" across {signature_width} workers)"
        ),
    )

    # The incremental plan (the `rebuild/review/unit_cache.py` module docstring is the authority): key every unit by its inputs, serve what the previous surface already computed, and pass only the rest to the runner. The reduces below always run over every unit, so every order- or ledger-derived field comes from this build. The table is compacted, so the set of units is final here. The unit store (`unit_store.UnitStore`) holds every per-unit product of phase 1 from here to the cache write. It is allocated over the table's count and shares the table's string table, and the keyer writes each unit's input key into it before any record is folded. The store's records are parsed and folded one at a time in store order (`unit_cache.stream_store`), matched to units through the map from input key to ordinal. Fold order changes no output byte: ids are read through the string table, every side column is read by `(start, count)`, and the mismatches are keyed by ordinal (the `unit_store` module docstring). A store that fails partway is restarted with the same input keys and nothing folded. The ids the discarded store added to the shared string table stay there, which is harmless because every read goes by id.
    console.phase("review.build plan", file=sys.stderr)
    phase = time.perf_counter()
    environment = unit_cache.environment_stamp(
        repo_root, spec, subset_dir, before_font, junior_font, helpers_digest, subset_digests=subset_digests
    )
    store = UnitStore(table.n, strings=table.strings)
    for ordinal in range(table.n):
        store.set_input_key(ordinal, keyer.key(table, rows, ordinal))
    release_rows(workload)
    del rows
    served = 0
    unplaced: list[unit_cache.ServedUnit] = []
    if not fresh_unit_cache:
        named: dict[str, int] = {}
        for ordinal in range(table.n):
            key = store.input_key_hex(ordinal)
            first = named.setdefault(key, ordinal)
            if first != ordinal:
                raise SystemExit(
                    f"units {table.codepoints_text(first)} and {table.codepoints_text(ordinal)} spell one input "
                    f"key {key}: every row belongs to one triple and every key line carries its window and "
                    "names, so a repeat is a sha256 collision"
                )
        stream = unit_cache.stream_store(out_dir, environment, wanted=named)
        if stream is None:
            note = unit_cache.store_miss_note(out_dir, environment) or unit_cache.UNREADABLE_NOTE
            report = console.say if note == unit_cache.NO_STORE_NOTE else console.warn
            report(f"unit cache: {note}", file=sys.stderr)
        else:
            # The store parses only records the workload names, so every record it returns is a candidate. A record's address is the span the shard writer returned when the previous surface was written, so the record is folded as soon as it is parsed. A record without an address (from an older store, or in a part whose size changed, as `unit_cache.stream_store` describes) is buffered for one walk over the previous surface's shards after the stream; on a surface this code wrote there are none. A candidate is served only when two conditions hold. First, the fragment at its address must carry the stamp the store recorded for it. The walk reads the stamp as it goes, a store address carries the record's own stamp, and `unit_cache.PriorFragmentReader` checks the id and stamp again when the write reads the bytes, which for a store-addressed fragment is the only time they are read. Skipping `check_unit` on a served fragment is safe only because of this equality. Second, the fragment must have the shape (slim or full) this build would write, because the ledger exemption that decides the shape is not covered by the key (`_slim_for`). So a unit that moves into the human workload after a ledger edit is re-enriched in full, and one that moves out is re-drafted slim. The plan keeps only the fragment's address, folded into the unit store beside the record's projection. A served unit's id comes from its stored content key, so it is known before phase 1; a fresh unit's id is stamped when it is drafted and returns with the projection. If the store fails partway, the rows already folded cannot be trusted, so the plan discards them and falls back to a full build over the same input keys.
            try:
                for cached in stream:
                    ordinal = named[cached.key]
                    found = cached.located()
                    if found is None:
                        unplaced.append(cached)
                    elif found.content_key == cached.content_key and cached.slim == _slim_for(
                        table.no_verdict(ordinal), cached
                    ):
                        store.fold_served(ordinal, cached, codepoints=table.codepoints(ordinal), found=found)
                        served += 1
            except unit_cache.StoreUnreadable:
                store = store.emptied()
                served = 0
                unplaced = []
                console.warn(f"unit cache: {unit_cache.UNREADABLE_NOTE}", file=sys.stderr)
        if unplaced:
            wanted: dict[str, set[str]] = {}
            for cached in unplaced:
                wanted.setdefault(cached.prior_class, set()).add(cached.prior_id)
            located = unit_cache.locate_prior_fragments(out_dir, wanted)
            del wanted
            for cached in unplaced:
                ordinal = named[cached.key]
                found = located.get(cached.prior_id)
                if (
                    found is not None
                    and found.content_key == cached.content_key
                    and cached.slim == _slim_for(table.no_verdict(ordinal), cached)
                ):
                    store.fold_served(ordinal, cached, codepoints=table.codepoints(ordinal), found=found)
                    served += 1
            del located
        del named
    fresh = array("I", (ordinal for ordinal in range(table.n) if not store.folded(ordinal)))
    # The sample is drawn from the served units' id words in ascending order, which is the ids' own order. The sampled units are materialized once, as copies the recomputation may write to: its phase 1 writes the ink flags, and the verification patch writes the injected echo and class. The reduces read the table and the store, not these copies.
    sampled = set(
        _verification_sample(
            sorted(store.id_word(ordinal) for ordinal in range(table.n) if store.folded(ordinal)),
            environment.value,
        )
    )
    sampled_ordinals = array(
        "I",
        (
            ordinal
            for ordinal in range(table.n)
            if store.folded(ordinal) and store.id_word(ordinal) in sampled
        ),
    )
    del sampled
    verify_units = [table.unit(ordinal, store) for ordinal in sampled_ordinals]
    if tally:
        # Read at each boundary, not held: the map's keys and values are new strings built from the store's columns, and holding the dict would keep them alive for the whole build, which an untallied pass never does.
        tally.hold_reading(
            "unit_cache.keys",
            lambda: pile_tally.measure(
                {
                    store.input_key_hex(ordinal): (store.unit_id(ordinal) if store.folded(ordinal) else "")
                    for ordinal in range(table.n)
                },
                packed=_packed_shape("unit_cache.keys"),
            ),
        )
        tally.hold("unit_cache.unplaced", unplaced, packed=_packed_shape("unit_cache.unplaced"))
        tally.hold_reading("unit_store", store.census)
        tally.boundary("plan")
    # The buffered records have been folded into the unit store or rejected, so they are freed here. Only a tallied pass keeps them, through the hold above.
    del unplaced
    _phase_timing("review.build plan", phase, f"(served {served:,} of {table.n:,} units from cache)")

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
        table=table,
        out_dir=out_dir,
        subset_pack=subset_pack,
    )
    try:
        if runner.pooled:
            signature_write = _SignatureWrite(out_dir, signature_environment, signature_entries)
        else:
            unit_cache.write_signature_store(out_dir, signature_environment, signature_entries)
            signature_write = None
        del signature_entries
        runner.phase1(store)
        # The enricher read each unit's name tuples from the record it was handed, and the verification sample holds its own records, so the parent reads no name tuple after this point.
        table.release_names()

        # Every unit's id and machine flags are now in the store: a served unit's from the plan, a fresh unit's from its projection. Building the id index fails the build on a repeated id. With 64-bit ids a repeat is very unlikely, but it would give two windows one id, so it is an error and not a merge.
        store.index()

        # Promote each UNMATCHED unit's verdict family to its class, so the per-class shard loop writes it under that family. The cluster id is already keyed on this final class: the runner computed it where it assigned the family, and a served unit uses the stored value, whose inputs (configs, final class, ink diffs) are all covered by the unit's input key and the store's environment stamp.
        for ordinal in range(table.n):
            if table.class_id(ordinal) == UNMATCHED_CLASS:
                family_id = store.family(ordinal)
                table.set_family(ordinal, family_id)
                table.set_class(ordinal, family_id)

        # The triage index. `order` is a permutation of all the table's rows in `audit.triage_key` order: class (the ledger classes first, then the verdict families), group, window, id. `assign_batches` numbers the human units along it and slices them into batches. Every term of the key is content, so this index is the only place the queue order is recorded, and no fragment carries a position in it.
        classes = workload.classes_present + synthesize_family_classes(
            table, families.FAMILY_ORDER, families.FAMILY_WHY
        )
        order = sort_for_triage(
            table, store, {entry.id: index for index, entry in enumerate(classes)}, dict(LETTERS)
        )
        total_batches = assign_batches(table, store, order, batch_size)

        # Echo groups: human units whose config set, judged pair, class and ink diffs (`diffs_digest`) all agree show the same change in different surroundings, so one verdict covers all of them. They are keyed after family promotion so the class is final. The id is a digest of the key (`unit_cache.echo_id_for`), so a group keeps its id on every surface it appears on. The table's string table stores each id once, however many units carry it.
        echo_groups: set[str] = set()
        for ordinal in range(table.n):
            if table.batch(ordinal) is None:
                continue
            pair = None
            pair_codepoints = store.pair_codepoints(ordinal)
            if pair_codepoints:
                values = table.codepoints(ordinal)
                pair = (values[pair_codepoints[0]], values[pair_codepoints[1]])
            key = (table.configs(ordinal), pair, table.class_id(ordinal), store.diffs_digest(ordinal))
            echo = unit_cache.echo_id_for(repr(key))
            echo_groups.add(echo)
            table.set_echo(ordinal, echo)
            table.set_cluster(ordinal, store.cluster(ordinal))

        by_class = table.rows_by_class(order)
        # The home reduce reads the corpus through the store and writes each unit's homes back into it. The returned dict is filled only on the list path, so it is empty here.
        _assignments, seam_census = resolve_home_assignments(store)

        # The sampled records were materialized before the reduces ran, so the fields the reduces assign (echo, cluster, class, homes) are passed to the recomputation explicitly.
        injections = {
            store.unit_id(ordinal): (
                table.echo(ordinal),
                table.cluster(ordinal),
                table.class_id(ordinal),
                store.homes(ordinal),
            )
            for ordinal in sampled_ordinals
        }
        verified = runner.verify(injections)
        # A served fragment must be what a fresh computation of the same window would write. The content key covers most of that: it hashes the fragment's adjudicable fields (the ink flag, both fonts' glyphs and cells, the seams, the notation, and on a full fragment the highlight geometry), so one comparison per sampled unit against the stamp the served fragment carried checks all of them, and the id with them. The recomputation writes the slim or full shape from the unit's own flags and exemption, as the write does, so a served fragment of the wrong shape would also fail here. Some fields are outside the key. `ink_deltas` is a carry-presentation key (`unit_cache.CARRY_PRESENTATION_KEYS`), so the recomputation returns it beside the key and it is compared with the store record the unit was served from. The drafts, the explain text and the secondary seams are checked where they are produced, not sampled: the drafter raises on a pin or policy record it cannot validate, the explain text comes from the same enrichment as the cells and seams the key covers, and `patch_fragment` re-emits the secondary seams from the stored rects under this build's home assignments.
        stale: list[str] = []
        for unit_id, (key, deltas) in verified.items():
            ordinal = store.ordinal_of(unit_id)
            if key != store.content_key_hex(ordinal) or dict(deltas) != store.ink_deltas(ordinal):
                stale.append(unit_id)
        if stale:
            stale.sort()
            raise SystemExit(
                f"the unit cache served {len(stale)} of {len(verified)} sampled units whose content key or "
                f"ink deltas do not match a fresh recomputation: {', '.join(stale[:10])}"
            )
        mismatches = [line for ordinal in range(table.n) for line in store.mismatches(ordinal)]
        echo_count = len(echo_groups)
        del echo_groups
        if tally:
            tally.hold("verified", verified)
            runner.hold_piles(tally)
            tally.boundary("units")
        _phase_timing(
            "review.build units",
            phase,
            f"(jobs={jobs}, fresh={len(fresh):,}, verified={len(verified):,} served)",
        )

        # The write (phase 2) is one pass over fresh and served units, each read by the address the store holds for it as its shard is written. A fresh fragment is read from the runner's spool, patched with this build's scaffold, ink deltas and seam homes through `patch_fragment` (on a unit record materialized for the patch), checked by `hold_scaffold`, and released once the shard, the checker and the sidecar spools have used it. A served fragment is read from the previous surface. When `UnitStore.served_as_is` says every field the patch would write already matches, it is copied as bytes without parsing, which lets the shard writer leave it, and a part made only of such fragments, in place; the checker reads its identity from the columns (`_served_identity`). Otherwise it is parsed, patched and serialized again. This runs inside the runner's `try` because the spool belongs to the runner.
        console.phase("review.build manifest+check", file=sys.stderr)
        phase = time.perf_counter()
        reader = unit_cache.PriorFragmentReader(out_dir)

        def emissions_in(ordinals: Iterable[int]) -> Iterator[_Emission]:
            for ordinal in ordinals:
                fresh_unit = not store.flags(ordinal).served
                source = store.source(ordinal)
                assert source is not None, ordinal
                seam_assign = store.homes(ordinal)
                try:
                    if fresh_unit:
                        fragment = runner.fragment(source)
                    elif store.served_as_is(
                        ordinal,
                        class_id=table.class_id(ordinal),
                        echo=table.echo(ordinal),
                        exemplar=table.exemplar(ordinal),
                        no_verdict=table.no_verdict(ordinal),
                    ):
                        yield _Emission(
                            store.unit_id(ordinal),
                            store.content_key_hex(ordinal),
                            store.policy_file(ordinal),
                            body=reader.read_bytes(source),
                            source=source,
                            identity=_served_identity(table, store, ordinal, seam_assign),
                        )
                        continue
                    else:
                        fragment = reader.read(source)
                except ValueError as error:
                    raise SystemExit(
                        f"the fragment for {store.unit_id(ordinal)} cannot be read back: {error}"
                    ) from None
                unit = table.unit(ordinal, store)
                fragment = patch_fragment(
                    fragment,
                    unit,
                    store.seam_rects(ordinal),
                    seam_assign,
                    hold=fresh_unit,
                    ink_deltas=store.ink_deltas(ordinal),
                )
                yield _Emission(
                    unit.unit_id, fragment["content_key"], _policy_file(fragment), fragment=fragment
                )

        try:
            written = _write_surface(
                out_dir,
                table,
                order,
                workload.row_count,
                classes,
                by_class,
                emissions_in,
                store,
                served,
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
                runner.contract_errors,
                font_digests,
                tally=tally,
                spec_root=spec_root,
            )
        finally:
            reader.close()
        if tally:
            tally.boundary("manifest+check")
    finally:
        runner.close()
    manifest = written.manifest
    _phase_timing(
        "review.build manifest+check",
        phase,
        f"(verbatim {written.verbatim:,} of {table.n:,} fragments, "
        f"respooled {written.respooled:,} sidecar rows)",
    )

    console.phase("review.build census-facts", file=sys.stderr)
    phase = time.perf_counter()
    premerge_facts = census.derive_premerge(premerge_capture, table, store)
    # An UNMATCHED window is a new join under review, so it is never ink-identical. That holds for the real corpus, not for every input, so it is asserted here and not in `derive_premerge`, which tests call with synthetic inputs that give ink-identical units a family.
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
            table,
            _StoreNotes(store),
            premerge_capture,
            premerge_facts,
            workload.row_count,
        ),
    )
    if tally:
        tally.boundary("census-facts")
    _phase_timing("review.build census-facts", phase)

    # The store is written as a merge over the previous one. A unit whose fragment was copied verbatim to an unchanged address has the same record as in the previous store, so its line is copied from that store through a cursor that reads it in step. Every other unit's record (fresh, re-patched or moved) is built from the unit store's columns and the table's class, echo and ledger flags (`UnitStore.cached_unit`, no record materialized). Both stores list units in triage order, and every term of `audit.triage_key` is content-derived, so the cursor only reads forward.
    console.phase("review.build cache", file=sys.stderr)
    phase = time.perf_counter()
    prior_store = unit_cache.StoreCursor(out_dir) if served else None
    carried = 0

    def store_entries() -> Iterator[unit_cache.CachedUnit | bytes]:
        nonlocal carried
        for ordinal in order:
            class_id = table.class_id(ordinal)
            echo = table.echo(ordinal)
            exemplar = table.exemplar(ordinal)
            no_verdict = table.no_verdict(ordinal)
            if prior_store is not None and store.served_as_is(
                ordinal, class_id=class_id, echo=echo, exemplar=exemplar, no_verdict=no_verdict
            ):
                source = store.source(ordinal)
                assert source is not None, ordinal
                if store.written_address(ordinal) == (source.part, source.start, source.length):
                    line = prior_store.take(store.input_key_hex(ordinal))
                    if line is not None:
                        carried += 1
                        yield line
                        continue
            yield store.cached_unit(
                ordinal, class_id=class_id, echo=echo, exemplar=exemplar, no_verdict=no_verdict
            )

    try:
        unit_cache.write_store(
            out_dir,
            environment,
            store_entries(),
            parts=[part for meta in manifest["classes"] for part in unit_index.class_shards(meta)],
        )
    finally:
        if prior_store is not None:
            prior_store.close()
    if signature_write is not None:
        signature_write.join()
    if tally:
        tally.boundary("cache")
    _phase_timing("review.build cache", phase, f"(carried {carried:,} of {table.n:,} store records)")
    return manifest


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


# --- table-diff mode -----------------------------------------------------------------


def _table_diff_unit_json(
    entry: tablediff.DiffEntry,
    full_configs,
    ink_identical: bool,
    picture_identical: bool,
) -> dict:
    """One table-diff entry's fragment, with the same content identity an m1-audit unit has: it is built with `id` and `content_key` None, stamped with the hash of its carry projection, and given `unit_cache.unit_id_for` of that stamp as its id."""
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
    fragment = {
        "id": None,
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
        "content_key": None,
    }
    fragment["content_key"] = unit_cache.carry_content_hash(fragment)
    fragment["id"] = unit_cache.unit_id_for(fragment["content_key"])
    return fragment


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

    if with_witnesses:
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
    ids_seen: set[str] = set()
    for bucket in tablediff.DIFF_BUCKETS:
        members = by_bucket.get(bucket, [])
        if not members:
            continue
        shard = []
        batches = set()
        machine_count = 0
        channel_counts = {channel: 0 for channel in MACHINE_CHANNELS}
        for entry in members:
            # An entry without a witness has no text to shape, so it cannot be shown ink- or picture-identical and stays in the human workload.
            text = "".join(chr(value) for value in entry.witness) if entry.witness else ""
            ink_identical = bool(text) and comparator.ink_identical(text, (entry.config,))
            picture_identical = (
                bool(text) and not ink_identical and comparator.picture_identical(text, (entry.config,))
            )
            fragment = _table_diff_unit_json(entry, all_configs, ink_identical, picture_identical)
            if fragment["id"] in ids_seen:
                raise SystemExit(f"two table-diff entries share the id {fragment['id']}: {entry.key.label()}")
            ids_seen.add(fragment["id"])
            if ink_identical or picture_identical:
                machine_count += 1
                channel_counts["ink_identical" if ink_identical else "picture_identical"] += 1
                machine_rows += max(len(entry.paired), 1)
            else:
                batches.add(human_index // batch_size)
                human_index += 1
                human_unit_ids.append(fragment["id"])
            shard.append(fragment)
            index += 1
        # Sort by id within the bucket, as every m1-audit shard is sorted, so the locator's blocks ascend.
        shard.sort(key=lambda fragment: fragment["id"])
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
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:CONTRACT_ERRORS_SHOWN]))
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
        unit_cache.is_content_id(unit) and unit.startswith("u-") for unit in human_unit_ids
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


DRAFTED = "drafted"
PATCHED = "patched"
CHECKED_AT = (DRAFTED, PATCHED)
# How many lines of a failing contract check the build prints. It also caps how many drafting-time complaints each worker, and the parent, carries to the write.
CONTRACT_ERRORS_SHOWN = 20


def check_unit(unit: dict, mode: str = "m1-audit", *, at: tuple[str, ...] = CHECKED_AT) -> list[str]:
    """The per-unit half of the §7 contract check. `at` selects which of its two subsets run. `DRAFTED` covers every field settled when `unit_to_json` builds the fragment: the identity and its stamp, the machine flags and `ink_deltas`, the class and group, the window, the seams, the highlight, the notation, the summary and explain, the config badge, and the drafts. `PATCHED` covers the fields `patch_fragment` assigns after the parent's reduces: `echo`, `cluster`, and the secondary seams with their homes.

    The m1 build runs `DRAFTED` in the process that drafts the fragment (`_phase1_unit`) and `PATCHED` in the parent's write (`_write_surface`). This is sound because `hold_scaffold` checks at the write that every scaffold field outside `PATCHED` (`_HELD_SCAFFOLD_KEYS`) still has the value the drafting-time check read, and because only the scaffold and the secondary seams are written after drafting. Either subset may read a held field, but only `PATCHED` may read an unheld one. A new fragment field must be assigned to one subset here. `rebuild/test_surface_checks.py` checks that every scaffold key is either held or one of the keys `PATCHED` checks, and that `DRAFTED` and `PATCHED` together equal the full check. `check_shards` and `check_output_dir` run both subsets, so a surface read back from disk gets every predicate.
    """
    errors: list[str] = []
    identifier = unit.get("id", "<missing>")

    def need(condition: object, message: str) -> None:
        if not condition:
            errors.append(f"unit {identifier}: {message}")

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

    approving = [channel for channel in MACHINE_CHANNELS if unit.get(channel) is True]
    human = not approving and unit.get("no_verdict") is not True
    pair = unit.get("pair")

    if DRAFTED in at:
        need(
            unit_cache.is_content_id(unit.get("id")) and str(unit.get("id")).startswith("u-"),
            f"id must be u- and {unit_cache.ID_SYMBOLS} base58 symbols",
        )
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
                if unit.get("ink_identical") is True or unit.get("picture_identical") is True:
                    need(not deltas, "ink- and picture-identical units must carry empty ink_deltas")
                elif unit.get("ink_identical") is False and unit.get("picture_identical") is False:
                    need(bool(deltas), "units with a visible ink change must carry a nonempty ink_deltas")
        stamp = unit.get("content_key")
        need(
            isinstance(stamp, str) and len(stamp) == 64 and all(ch in "0123456789abcdef" for ch in stamp),
            "content_key must be a sha256 hex stamp",
        )
        # The id is the first 64 bits of the content key in base58, so a fragment whose id and stamp disagree names one window and describes another.
        if isinstance(stamp, str) and len(stamp) == 64:
            need(unit.get("id") == unit_cache.unit_id_for(stamp), "id must be the content key's own")
        need(isinstance(unit.get("no_verdict"), bool), "no_verdict must be a bool")
        # Whether a unit takes a verdict comes from its flags (`audit.slim_fragment`), and its place in the queue comes from the manifest's triage index, so a fragment carries no batch.
        need("batch" not in unit, "a fragment carries no batch; the manifest's human_unit_ids is the index")
        need(len(approving) <= 1, "at most one machine channel may approve a unit")
        for key in ("class", "group", "notation", "summary"):
            need(isinstance(unit.get(key), str) and unit.get(key) != "", f"{key} must be a nonempty string")
        # The slim shape is checked in both directions. A slim fragment must omit every key in `SLIM_OMITTED_KEYS`, and a full fragment must carry them with values, because a human unit without them gives the reviewer nothing to act on. Key presence is the test, as in the app's `isSlimFragment`: a null under one of these keys in a full fragment is an error, not a slim marker.
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
        # A unit has one render group because its configs cannot render differently: its rows either share (codepoints, baseline, new), or `audit.merge_ink_duplicate_units` folded them together because their ink is identical in every config. Data that broke this would need stacked rendering, so it is a build error.
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

        codepoints = unit.get("codepoints")
        renderable = codepoints is not None
        if mode == "m1-audit":
            need(renderable, "codepoints must be present in m1-audit mode")
        if renderable:
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
        need(
            isinstance(before, dict) and isinstance(before.get("glyphs"), list),
            "before.glyphs must be a list",
        )
        need(
            isinstance(before, dict) and isinstance(before.get("seams"), list), "before.seams must be a list"
        )
        need(isinstance(after, dict) and isinstance(after.get("cells"), list), "after.cells must be a list")
        need(isinstance(after, dict) and isinstance(after.get("seams"), list), "after.seams must be a list")
        need(
            isinstance(after, dict) and isinstance(after.get("extensions"), list),
            "after.extensions must be a list",
        )
        if isinstance(before, dict) and isinstance(before.get("seams"), list):
            need(
                all(is_seam_token(seam) for seam in before["seams"]),
                "before.seams must be break/lig/yN tokens",
            )
        if isinstance(after, dict) and isinstance(after.get("seams"), list):
            need(
                all(is_seam_token(seam) for seam in after["seams"]), "after.seams must be break/lig/yN tokens"
            )
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
        if isinstance(codepoints, str) and isinstance(tokens, list):
            need(
                len(tokens) == len(codepoints.split(":")),
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
                need_rect(highlight.get(side) if isinstance(highlight, dict) else None, f"highlight.{side}")

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
                # A pin that fails when pasted into the corpus is worse than no pin, so both results the drafter records for it must be "pass" on every shipped unit: the repo's own parser, and a replay of the assertion against the after font.
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
                need(
                    isinstance(policy.get("schema_valid"), bool), "drafts.policy.schema_valid must be a bool"
                )
                if mode == "m1-audit":
                    need(
                        policy.get("schema_valid") is True,
                        "drafts.policy.suggested_record must validate against the rune schema",
                    )
                    need(
                        policy.get("keypath")
                        in ("policy.refuse[+]", "policy.prefer[+]", "policy.contract[+]"),
                        f"drafts.policy.keypath is {policy.get('keypath')!r}",
                    )
                    # The draft may name only records the unit's own trace named; any other record would send the reviewer to edit something unrelated to the unit.
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
                    # The after-behavior candidate is the pin's `expect`, which the `drafts.pin.syntax` check above covers. The before-behavior candidate comes from the baseline side of the enrichment. `Drafter.draft_any_of` already parses it and raises `DraftError` when it fails; this parse repeats the check so that a surface read from disk is covered too.
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

    if PATCHED in at:
        need("echo" in unit, "echo must be present")
        echo = unit.get("echo")
        need(
            echo is None or (isinstance(echo, str) and echo.startswith("e-")),
            "echo must be null or an e- group id",
        )
        if mode == "m1-audit":
            if human:
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
            if human:
                need(isinstance(cluster, str), "human-workload units must carry a cluster signature id")
            else:
                need(cluster is None, "units outside the human workload must carry cluster null")

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
                if isinstance(pair, dict) and isinstance(seam_pair, dict):
                    need(
                        (seam_pair.get("left"), seam_pair.get("right"))
                        != (pair.get("left"), pair.get("right")),
                        f"{label} must not duplicate the primary pair",
                    )
                need_rect(seam.get("before"), f"{label}.before")
                need_rect(seam.get("after"), f"{label}.after")
                home = seam.get("home")
                need(
                    home is None or (isinstance(home, str) and home.startswith("u-")),
                    f"{label}.home must be null or a unit id",
                )
    return errors


class _SurfaceCheck:
    """The per-unit and cross-unit parts of the §7 contract check as an accumulator fed one fragment at a time: `class_start` with the manifest's class record, `unit` for each fragment in shard order, `class_end` after the class's last fragment, and `finish` with the manifest for the predicates that read its totals. The m1 build uses it to check fragments it releases as it writes them, so per unit it keeps only what the cross-unit predicates read (codepoints, whether there is a primary pair, whether anything visible changed, and the grouping keys), never the fragment. `check_shards` feeds it from a mapping held in memory. `unit` runs `check_unit` on every fragment the unit cache did not serve, at the subsets `at` names: both by default, and `PATCHED` alone in the m1 write, where `DRAFTED` already ran when each fragment was drafted. Per-unit errors are recorded as each fragment is fed in, and cross-unit errors at `finish`."""

    def __init__(
        self,
        *,
        mode: str,
        descriptions: Mapping,
        batch_size: object,
        repo_root: Path | None,
        served_ids: Collection[str],
        at: tuple[str, ...] = CHECKED_AT,
    ) -> None:
        self._mode = mode
        self._at = at
        self._descriptions = descriptions
        self._batch_size = batch_size
        self._repo_root = repo_root
        self._served_ids = served_ids
        self.errors: list[str] = []
        self._seen_units = 0
        self._seen_rows = 0
        self._seen_ids: set[str | None] = set()
        self._seen_machine_by_class: dict[str, int] = {}
        self._seam_homes: list[tuple[str | None, str]] = []
        self._seam_units = 0
        self._seams_homed = 0
        self._seams_homeless = 0
        self._echo_keys: dict[str | None, set[tuple]] = {}
        self._cluster_keys: dict[str | None, set[tuple]] = {}
        self._echo_cluster: dict[str | None, str | None] = {}
        self._human_units: dict[str, tuple[str, str, str]] = {}
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

    def unit(self, unit: dict, *, served: bool | None = None) -> None:
        """Check one fragment, in shard order. `served` says whether the unit cache served it, in which case `check_unit` is skipped. The m1 write passes it from the store's flag column; when it is None, membership in `served_ids` decides."""
        errors = self.errors
        meta = self._meta
        mode = self._mode
        self._class_units += 1
        unit_id = unit.get("id")
        if served is None:
            served = unit_id in self._served_ids
        if not served:
            errors.extend(check_unit(unit, mode, at=self._at))
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
        human = not slim_fragment(unit)
        # Echo and cluster exist only in m1-audit. A table-diff unit has null for both, and treating null as a group id would put all its units in one group.
        if mode == "m1-audit" and human and isinstance(unit_id, str):
            key = (unit.get("class"), tuple(unit.get("configs") or ()))
            self._echo_keys.setdefault(unit.get("echo"), set()).add(key)
            self._cluster_keys.setdefault(unit.get("cluster"), set()).add(key)
            if self._echo_cluster.setdefault(unit.get("echo"), unit.get("cluster")) != unit.get("cluster"):
                errors.append(f"unit {unit_id}: echo {unit.get('echo')} spans two clusters")
        if unit.get("class") != meta.get("id"):
            errors.append(f"unit {unit.get('id')}: class {unit.get('class')} in shard {meta.get('id')}")
        if unit.get("id") in self._seen_ids:
            errors.append(f"duplicate unit id {unit.get('id')}")
        self._seen_ids.add(unit.get("id"))
        if human and isinstance(unit_id, str):
            self._human_units[unit_id] = (
                str(unit.get("class")),
                str(unit.get("group") or ""),
                str(unit.get("codepoints") or ""),
            )
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
        # The app draws a machine fold's badge from this record alone, before it loads the fold's units, so a stale count would mislabel the fold.
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
        recorded_index = manifest.get("human_unit_ids")
        human_unit_ids: list[str] = (
            [unit for unit in recorded_index if isinstance(unit, str)]
            if isinstance(recorded_index, list)
            else []
        )
        index_valid = isinstance(recorded_index, list) and len(human_unit_ids) == len(recorded_index)
        if index_valid and set(human_unit_ids) != set(self._human_units):
            errors.append("human_unit_ids does not match the shards' human workload")
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
        # The triage index is the manifest's, and every batch is a slice of it, so both are checked against the index. In m1-audit, where every term of `audit.triage_key` is a fragment field or a manifest fact, the index must hold the human units in that order. Every class's `batches` must be the slices its units occupy, and `totals.batches` the number of slices.
        batch_size = self._batch_size
        if index_valid and mode == "m1-audit":
            class_index: dict[str, int] = {
                str(meta.get("id")): index for index, meta in enumerate(manifest.get("classes") or ())
            }
            rank = family_ranks(dict(LETTERS))
            expected = sorted(
                self._human_units,
                key=lambda unit_id: triage_key(
                    class_index.get(self._human_units[unit_id][0], len(class_index)),
                    self._human_units[unit_id][1],
                    parse_codepoints(self._human_units[unit_id][2]),
                    unit_id,
                    rank,
                ),
            )
            if human_unit_ids != expected:
                errors.append("human_unit_ids is not the triage-ordered sequence of the shards' human units")
        if index_valid and isinstance(batch_size, int) and batch_size > 0:
            occupied: dict[str, set[int]] = {}
            for position, unit_id in enumerate(human_unit_ids):
                if unit_id in self._human_units:
                    occupied.setdefault(self._human_units[unit_id][0], set()).add(position // batch_size)
            for meta in manifest.get("classes") or ():
                if meta.get("batches") != sorted(occupied.get(str(meta.get("id")), ())):
                    errors.append(
                        f"class {meta.get('id')}: batches {meta.get('batches')} are not the slices of "
                        f"{batch_size} its units occupy in human_unit_ids"
                    )
            if totals.get("batches") != (len(human_unit_ids) + batch_size - 1) // batch_size:
                errors.append("totals.batches does not count the slices human_unit_ids partitions into")
        for unit_id, home in self._seam_homes:
            if home == unit_id:
                errors.append(f"unit {unit_id}: a secondary seam names itself as home")
            elif home not in seen_ids:
                errors.append(f"unit {unit_id}: secondary seam home {home} is not a unit in this output")
        seam_census = manifest.get("secondary_seams")
        # The home relation can be checked only where the resolver assigned homes, which is where it wrote the census. A home's window must be contained in this unit's window, the home must have a primary pair, and it must show a visible change (a seam whose home has none is counted in `seams_suppressed_invisible` instead). A unit with no visible change must not carry a secondary seam.
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
    """Run the per-unit and cross-unit §7 checks over shard payloads held in memory, keyed by class id. The table-diff build passes the dicts it serialized and `check_output_dir` passes the shards it re-parsed from disk; the m1 build runs the same predicates through `_SurfaceCheck` one fragment at a time as it writes. A class missing from the mapping is skipped here and reported by the caller. When `repo_root` is given, each distinct policy-draft file is checked to exist; every other predicate reads only the payload.

    The cross-unit predicates check what no single fragment can: each echo group and each cluster holds one class and one config set, every echo group lies inside one cluster, the manifest's `human_unit_ids` is the human workload in `audit.triage_key` order, every class's `batches` are the slices its units occupy in it, and each secondary seam's home is a unit whose window this unit's window contains and which has a primary pair and a visible change.

    Apart from the policy-draft files, the cross-unit predicates read only fields that slim and full fragments both carry (the machine flags, the window, the pair, the class, group, configs, echo and cluster), so the manifest's counts are checked over both kinds. `check_unit` checks that a slim fragment (`audit.slim_fragment`) omits `SLIM_OMITTED_KEYS` and a full one carries them.

    `check_unit` is skipped for the units in `served_ids`. A served fragment passed `check_unit` in the build that drafted it, and the build serves it only when the stamp on the shard equals its store record's. The fields a later build re-patches onto a served fragment (the scaffold and secondary seams) are not re-checked per unit; the cross-unit predicates still run over every unit. A caller re-reading a finished surface passes no `served_ids` and so checks everything.
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
    """Check the files beside the manifest: every shard part is present and non-empty, the per-unit index and the app's two sidecars are present and stamped for this manifest, index.html exists, and each copied font matches the sha256 the manifest records and, when `repo_root` resolves its `source`, the font it was copied from. A build already guarantees the source comparison (`_copy_font` checks the copy against the digest taken at load), and the cycle's surface skip and `make verdict-ready` compare the manifest's after-font sha256 with `rebuild/out/m1/M1.otf`; the comparison here is for `check_output_dir` and `refresh_assets`."""
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

    from rebuild.tools.artifact_cycle import (
        signature_job_budget,
        signature_job_derivation,
        surface_job_budget,
        surface_job_derivation,
    )

    surface_jobs = surface_job_budget(skip_gates=True)
    signature_jobs = signature_job_budget(skip_gates=True)
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
        "--signature-jobs",
        type=int,
        default=signature_jobs,
        help=f"the width the ink-signature phase shapes its store misses at, independent of `--jobs`: a signature worker is one comparator over the two fonts, flat in the pile and pure CPU, so cores bind it where memory binds the unit worker, and `--jobs 1` on a small box still shapes signatures across the cores. The default is `signature_job_budget()` at its unreserved arm, a hand run having no co-resident `make test` pool to leave cores to — on this box {signature_job_derivation(skip_gates=True)}. A miss pile under the pool's threshold shapes serially at any width, and a pooled pass files its per-worker peaks for `make job-costs`.",
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
            signature_jobs=args.signature_jobs if args.signature_jobs and args.signature_jobs > 1 else 1,
            fresh_unit_cache=args.fresh_unit_cache,
        )
    totals = manifest["totals"]
    print(
        f"Wrote {args.out} ({totals['units']} units, {totals['rows']} rows, {totals['batches']} batches)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
