"""Build the review app's output directory, rebuild/out/review/ (rebuild/REVIEW-PLAN.md §1.3). The m1-audit build (`build_m1`) runs in the review crate's build process (`rebuild/review-rs/`, `ams-review build`), which assembles units and precomputes their enrichment and, for every unit that takes a verdict, all three verdict drafts, and writes manifest.json, one unit shard per class (split into byte-capped parts when a class is too large for one file, with the overflow parts a cached build appends changed units to after them), the review-facts.json sidecar that the artifact cycle copies into the checked-in review-facts pins, copies of both fonts, and the static app files. This module computes what the process reads before it starts, the units init and the build input with every stamp in it, and runs it. The table-diff mode (`build_table_diff`) builds here, in Python. The §7 contract checker (`check_unit`, `check_shards`, `check_output_dir`) and the corpus's materialized form (`MaterializedCorpus`) read a written corpus. The `snapshot` subcommand writes an accepted-state baseline. `refresh-assets` copies the static app files over an existing corpus and restamps only that fingerprint component, without rebuilding any unit. A build into rebuild/out/review/ refuses while a review server that may serve this checkout listens (`refuse_the_served_corpus`); the artifact cycle builds beside it, at rebuild/out/review.next. Every form of the command refuses while the alphabet switch (`spec_load.M1_RUNES_ENV`) is set outside the fault kit (`spec_load.small_alphabet_refusal`).

Usage:
    uv run python -m rebuild.review.build
    uv run python -m rebuild.review.build --mode table-diff --baseline <dir> --new <dir> --before-font <otf> --after-font <otf>
    uv run python -m rebuild.review.build snapshot --tables rebuild/out/m1 --font rebuild/out/m1/M1.otf --to rebuild/out/review-baseline
    uv run python -m rebuild.review.build refresh-assets
"""

from __future__ import annotations

import argparse
import bisect
import ctypes
import datetime
import errno
import gzip
import http.client
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import BinaryIO

from rebuild.pipeline import fingerprint, output_sink, row_delta, spec_load
from rebuild.pipeline.baseline_subset import M1_ALPHABET
from rebuild.review import (
    app_index,
    audit,
    corpus_exec,
    tablediff,
    unit_cache,
    unit_index,
    units_exec,
)
from rebuild.review.audit import (
    ACCEPTANCE_CONFIGS,
    BATCH_SIZE,
    LEDGER_STATUSES,
    MACHINE_CHECKS,
    SECTIONS,
    SLIM_OMITTED_KEYS,
    class_section,
    family_ranks,
    load_ledger,
    machine_approved,
    parse_codepoints,
    slim_fragment,
    triage_key,
)
from rebuild.review.change_groups import (
    CHANGE_GROUP_ORDER,
    load_change_group_prose,
)
from rebuild.review.drafts import corpus_index_from_pages
from rebuild.review.feature_reach import ConfigFold, derive_fold
from rebuild.review.ink import (
    JUNIOR_VERIFICATION_METHOD,
    PICTURE_VERIFICATION_METHOD,
    VERIFICATION_METHOD,
    InkComparator,
)
from rebuild.review.enrich import (
    EXPLAIN_UNIT_BATCH_SIZE,
    LETTERS,
    load_spec,
    notation,
    notation_tokens,
    text_entities,
)
from rebuild.review.subset_pack import ensure_pack, is_junction_token, table_digests
from rebuild.tools import console, cycle_paths
from rebuild.validation.expect import parse_expect

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "rebuild" / "out" / "review"
STATIC_DIR = Path(__file__).resolve().parent / "static"

MANIFEST_FORMAT = "ams-review-manifest/2"
SHARD_PART_BYTES = unit_index.SHARD_PART_BYTES
BUILD_COMMAND = "uv run python -m rebuild.review.build"
SERVE_COMMAND = "uv run python -m rebuild.review.serve"
REVIEW_SERVER_PORT = 7294

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
<title>AMS review corpus (placeholder)</title>
</head>
<body>
<main>
<h1>AMS review corpus</h1>
<p>This is the generator's placeholder page: the static app sources were not present under <code>rebuild/review/static/</code> when this directory was built. The data payload is complete — <a href="manifest.json">manifest.json</a> plus the unit shards under <code>units/</code>, and both fonts under <code>fonts/</code>.</p>
<p>Rebuild with <code>{build}</code>; serve with <code>{serve}</code>.</p>
</main>
</body>
</html>
"""


def _sha256(path: Path) -> str:
    return fingerprint.file_sha256(Path(path))


def _alphabet_meta() -> dict:
    """Return the migration progress shown on the corpus chip. `migrated` counts the letters this corpus is built over: the subset filter's alphabet without its boundary tokens, which is the set of letters that have runes under glyph_data/runes/. `total` counts the whole Quikscript alphabet."""
    return {"migrated": len(M1_ALPHABET & set(LETTERS)), "total": len(LETTERS)}


def _inputs_fingerprint(
    repo_root: Path,
    m1_dir: Path,
    before_font: Path,
    junior_font: Path,
    spec_root: Path | None = None,
    *,
    draft_lines: Sequence[str],
) -> dict:
    """Return the manifest's `inputs_fingerprint`. The Stage A values are copied from run_m1's recorded inputs_fingerprint.json instead of being recomputed, so a corpus built over stale out/m1 artifacts carries the stale hashes and the readiness checker can flag it. The Stage A values are null when that record is missing, unreadable, or incomplete. `draft_lines` is the `fingerprint.draft_input_lines` that `build_m1` took from the read of the pages the units init's corpus index came from, before the crate drafted anything, and `fingerprint.stage_b` takes `draft_inputs` from them instead of reading the pages again, so a page edited during the build leaves the manifest describing the pages the drafts saw."""
    stage_a = fingerprint.read_stage_a(m1_dir) or {key: None for key in fingerprint.STAGE_A_COMPONENTS}
    return {
        **stage_a,
        **fingerprint.stage_b(repo_root, before_font, junior_font, spec_root, draft_lines=draft_lines),
    }


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


UNIT_ASSEMBLY_EPOCH = "2026-10-11T08:52:00Z"


def _generated_at(*inputs: Path, mtimes: Iterable[float] = ()) -> str:
    """Return the manifest's `generated_at` stamp: the latest input mtime as UTC ISO, floored at UNIT_ASSEMBLY_EPOCH. The m1-audit build passes the audit's time in `mtimes`: under its head, the time its content last moved (`audit.AuditParts.content_time`, the head's `row_delta.CONTENT_TIME_STAMP`), which a segment moves and a head-alone write or a fold does not; for a single file, its modification time. It is the same for consecutive builds of the same inputs (the §6 determinism gate) and changes whenever an input changes. The stamp is the corpus's identity: the app, the server, the merge, both fills and the review tools take an ams-review-verdicts/1 document stamped for this corpus as already carried onto it, a pass that skips the corpus build merges a master that is an ams-review-verdicts/1 document stamped for it straight into the store (`artifact_cycle.master_stamped_for_corpus`), or, when the snapshot of the store the verdict update reads is stamped for an older corpus or is not an ams-review-verdicts/1 document, carries that snapshot onto this one first and merges any other master into it (`verdict_update.merge_sets_store_aside`; the carry refuses a snapshot it cannot read, naming it, and the merge refuses an ams-review-verdicts/1 snapshot stamped for a newer corpus, either of which fails the verdict update), a pass tells the open tabs to move only when the served stamp moves, and `rebuild.tools.rekey_verdicts` refuses a corpus stamped as one its map was computed from. Bump the epoch whenever a build-code change re-keys units without changing any input: without the floor the stamp would not move. The pass that builds the re-keyed corpus would still carry by unit id its master and, in a landing pass whose master is not the store, the store's snapshot as a second input when the snapshot step found a store (`verdict_update`), and that carry refuses when more than `landing.ORPHAN_SHARE_LIMIT` of the prior verdicts `carry_verdicts.resolve_prior` resolves across those files, skips aside, are orphaned, but the re-key its refusal points to would refuse the re-keyed corpus as the one its map was computed from. A carry that goes through (a smaller re-key, or `--allow-orphans`) would merge into an ams-review-verdicts/1 store that carries the same stamp, so the merge would keep the store's records on the old ids instead of stashing the store, and the land's orphan guard, which runs only when the stamp moves, would not run. Once a corpus re-keyed under the old stamp is served, landed by such a pass or written in place by a hand `make review-build`, the store, an export or an open tab's saves still on the old ids would be taken as carried onto it, with no carry and no orphan-share refusal (a pass that skips the build merges its master directly), leaving their verdicts on ids the corpus no longer has."""
    latest = max([path.stat().st_mtime for path in inputs if path.exists()] + list(mtimes))
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
    "ss05": "allow ·Tea to join at the baseline on both sides",
    "ss06": "use gapped ·Owe (doesn’t connect at the top)",
    "ss07": "allow ·Owe·Day to join at the x-height again",
    "ss08": "write ·Key·Utter·May as The Manual does: no join after ·Key, then the alternate ·Utter",
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
    """Return the unit's config badge as (gate, note). The gate is the smallest conjunction of feature on/off constraints that selects exactly the configs the divergence applies under, one clause per constraint; the app draws each clause as a chip in that feature's color. The note is the clauses joined, kept as a string for the review-facts histogram and for hover text.

    Both are None when the unit covers every non-ss10 config, which is the common case. When no conjunction of at most GATE_CONSTRAINT_CAP constraints selects exactly the set, the gate is None and the note is the literal "only under: <set>".

    ss10 is a constraint like the others, except that a set containing no ss10 config is resolved against the non-ss10 configs alone. That lets an exclusion gate like ss03-off stand without also stating the implied ss10-off.
    """
    return _config_badge(tuple(unit_configs), tuple(full_configs))


def _copy_font(
    source: Path, out_dir: Path, name: str, family: str, repo_root: Path, expected_sha256: str
) -> dict:
    """Copy one font into the corpus as `fonts/<name>` and return its manifest record. The copy is renamed into place (`output_sink.replace_output`), so a tab that fetches it mid-copy gets the old font or the new one, and it is checked against `expected_sha256`, the digest the build took when it loaded the font, so the units never describe a font other than the one shipped beside them. A copy already there with that digest is left in place, since it is that font."""
    target = out_dir / "fonts" / name

    def copy_checked(staging: Path) -> None:
        shutil.copyfile(source, staging)
        digest = _sha256(staging)
        if digest != expected_sha256:
            raise SystemExit(
                f"{source} changed between this build's load ({expected_sha256}) and its copy ({digest}), "
                "so the units would describe a font other than the one shipped beside them; rebuild the corpus"
            )

    output_sink.replace_output(target, writer=copy_checked, sha256=expected_sha256, keep_if_equal=True)
    try:
        rel = str(source.resolve().relative_to(repo_root))
    except ValueError:
        rel = str(source)
    return {
        "file": f"fonts/{name}",
        "family": family,
        "source": rel,
        "sha256": expected_sha256,
        "upem": _upem(target),
    }


def copy_static(out_dir: Path, static_dir: Path = STATIC_DIR) -> list[str]:
    """Copy the static app files (`fingerprint.static_files`) into `out_dir`, and a fallback `index.html` naming the build and serve commands when the listing holds no `index.html`, and return their paths relative to it. Each file is renamed into place (`output_sink.replace_output`), so a tab that fetches it mid-copy gets the old bytes or the new ones, and one that already holds the bytes it would get is left in place."""
    copied: list[str] = []
    if static_dir.is_dir():
        for source in fingerprint.static_files(static_dir):
            rel = source.relative_to(static_dir)
            output_sink.replace_output(out_dir / rel, source=source, keep_if_equal=True)
            copied.append(str(rel))
    if "index.html" not in copied:
        fallback = _FALLBACK_INDEX.format(build=BUILD_COMMAND, serve=SERVE_COMMAND)
        output_sink.replace_output(out_dir / "index.html", fallback, keep_if_equal=True)
        copied.append("index.html")
    return copied


def refresh_assets(out_dir: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """Copy `rebuild/review/static/` over an already-built corpus (`copy_static`, which leaves a file whose bytes come out the same in place) and restamp only the manifest's `static` fingerprint component. An app JS, CSS or HTML edit changes only that component, and no unit depends on it. Everything else stays as the build left it: `generated_at` and `repo_head`, every shard, the per-unit index, both app sidecars, and the unit-cache store. The autosave is keyed on `generated_at`, so a verdict session in progress is unaffected, and the sidecars and the store stay current because `unit_index.manifest_sha256` leaves this component out of the manifest's identity. `_check_output_files` runs afterwards to confirm that. If it finds errors, the original manifest is restored, because a restamped manifest over sidecars that do not match it would look fresh to the next cycle, which would then skip the build that could repair it."""
    out_dir = Path(out_dir)
    repo_root = Path(repo_root)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"no corpus to refresh: {manifest_path} is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = manifest.get("inputs_fingerprint") if isinstance(manifest, dict) else None
    if not isinstance(recorded, dict):
        raise SystemExit(
            f"{manifest_path} records no inputs_fingerprint, so there is no component to restamp; "
            f"rebuild the corpus with `{BUILD_COMMAND}`"
        )
    original = manifest_path.read_bytes()
    copied = copy_static(out_dir, repo_root / "rebuild" / "review" / "static")
    recorded["static"] = fingerprint.hash_paths(repo_root, fingerprint.static_paths(repo_root))
    _write_json(manifest_path, manifest)
    errors = _check_output_files(out_dir, manifest, repo_root)
    if errors:
        manifest_path.write_bytes(original)
        raise SystemExit(
            "contract check failed after the refresh, and the manifest is put back so the corpus still "
            "reads as stale:\n" + "\n".join(f"  - {line}" for line in errors)
        )
    return copied


def _write_json(path: Path, payload) -> None:
    """Write `payload` to `path` as indented ASCII JSON through a staging file that is renamed into place, streaming a non-empty list one element at a time.

    The table-diff build calls it only for manifest.json, a dict that goes through `json.dumps` whole. Shards are written by `_write_shard`, which frames each fragment as the list path here frames an element, and the tests write shards through this function. Each list element is serialized inside a one-element list and the list's framing is stripped, so the C encoder writes the depth-1 indent (it handles `indent` for a one-shot dump on Python 3.14) and the bytes equal `json.dumps(payload, indent=1, ensure_ascii=True) + "\\n"`. Serializing a large list whole that way holds two full-size copies of the string at once, because the concatenation cannot resize the serialized string in place. `JSONEncoder.iterencode` would be simpler, but it uses the pure-Python encoder when it is not one-shot, which makes the write several times slower; stripping the framing adds under a third, a couple of seconds across the whole units directory.

    The staging file is needed because nothing downstream detects a half-written corpus file: a failed encode or a killed build must leave the previous file intact, not a truncated shard or an empty manifest.
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


@lru_cache(maxsize=1)
def _clonefile():
    """Return libc's `clonefile(2)`, or None on a platform without it."""
    if sys.platform != "darwin":
        return None
    call = getattr(ctypes.CDLL(None, use_errno=True), "clonefile", None)
    if call is not None:
        call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        call.restype = ctypes.c_int
    return call


def _clone(source: Path, target: Path) -> bool:
    """Make `target`, which must not exist, a copy-on-write clone of `source` (`clonefile(2)`, the call `landing.clone_tree`'s `cp -c` makes) and return True, or return False, having written nothing, where the platform or the volume cannot clone. A clone writes no data: on APFS the two files share every block until one of them is written."""
    call = _clonefile()
    if call is None:
        return False
    if call(os.fsencode(source), os.fsencode(target), 0) == 0:
        return True
    code = ctypes.get_errno()
    if code in (errno.ENOTSUP, errno.EXDEV):
        return False
    raise OSError(code, os.strerror(code), str(source), None, str(target))


def _fragment_bytes(fragment: dict) -> bytes:
    """Return a fragment's plain bytes as a shard part holds them (`_write_shard`): framed as `_write_json` frames a list element, without the comma before it. A part holding the fragment deflated holds these bytes through `unit_index.deflate_fragment`."""
    return json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2].encode("ascii")


def _write_shard(
    out_dir: Path, class_id: str, fragments: list[dict]
) -> tuple[list[str], list[tuple[int, int, int]]]:
    """Write one class's fragments, held as a list, as the parts a from-scratch build gives them, and return the relative part paths in part order and, aligned with `fragments`, each fragment's (part index, byte start, byte length). The fragments go into base parts plain, in the order passed, which is id order, cut by `unit_index.PartLayout` at `SHARD_PART_BYTES` and named by `unit_index.base_part_names`: the bare `units/<class-id>.json` for a class that fits one part, and `units/<class-id>.000.json` onward for a larger one. The cap is for the browser, whose range reads never cross a part (`unit_index.SHARD_PART_BYTES` says why), and every part stays within it except a part holding a single fragment that alone exceeds it. Each fragment is framed as `_fragment_bytes` frames it, so a part equals `json.dumps(part, indent=1, ensure_ascii=True) + "\\n"`.

    The returned spans are byte addresses on disk, which the review app's explain panel Range-fetches through the app sidecars. No punctuation falls inside a fragment's own bytes, and `ensure_ascii=True` makes the character count equal the byte offset, so `bytes[start:start + length]` is a standalone JSON value. Changing the `indent`, `ensure_ascii` or `separators` of the dump in `_fragment_bytes` breaks this without any error; `rebuild/test_app_index.py` slices every fragment back out to catch it.

    Every part is staged under a `.partial` name and renamed into place once the last one is written, so a failed encode leaves the previous parts in place, and the staging files are deleted.
    """
    out_dir = Path(out_dir)
    units_dir = out_dir / "units"
    units_dir.mkdir(parents=True, exist_ok=True)
    layout = unit_index.PartLayout(SHARD_PART_BYTES)
    staged: list[Path] = []
    spans: list[tuple[int, int, int]] = []
    handle: BinaryIO | None = None
    try:
        for fragment in fragments:
            body = _fragment_bytes(fragment)
            index, start, first = layout.place(len(body))
            if first:
                if handle is not None:
                    handle.write(unit_index.PART_CLOSING)
                    handle.close()
                staged.append(units_dir / f"{class_id}.{index:03d}.json.partial")
                handle = staged[-1].open("wb")
                handle.write(b"[")
            else:
                assert handle is not None
                handle.write(b",")
            handle.write(body)
            spans.append((index, start, len(body)))
        if handle is not None:
            handle.write(unit_index.PART_CLOSING)
            handle.close()
            handle = None
        if not staged:
            staged.append(units_dir / f"{class_id}.000.json.partial")
            staged[-1].write_bytes(b"[]\n")
        names = unit_index.base_part_names(class_id, len(layout.sizes()))
        for staging, name in zip(staged, names, strict=True):
            staging.replace(out_dir / name)
        return names, spans
    finally:
        if handle is not None:
            handle.close()
        for staging in staged:
            staging.unlink(missing_ok=True)


# The preset dictionary the review crate deflates every recomputed fragment against, whose bytes reach it beside the units init (`units_exec.write_init`). It is a data file and not a module, so neither the unit store's stamp (`unit_cache.corpus_code_paths`) nor the manifest's (`fingerprint.review_code_paths`) hashes it: it cannot change a shard byte, so regenerating it (rebuild/tools/fragment_dictionary.py) forces no recompute.
FRAGMENT_DICTIONARY = Path(__file__).resolve().parent / "fragment-dictionary.bin"


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


def _config_fold(before_font: Path, after_font: Path) -> ConfigFold:
    """Derive, over the two fonts, which of the configurations a unit can hold (`audit.ACCEPTANCE_CONFIGS`) take `default`'s shapes for a window that holds none of their trigger letters (`feature_reach.derive_fold`), and print the line that names them and every configuration shaped in every window, with the check it failed. The build derives it once and hands it to the crate in the units init (`units_exec.write_init`)."""
    fold = derive_fold((before_font, after_font), ACCEPTANCE_CONFIGS)
    console.say(f"review.build units: {fold.note()}", file=sys.stderr)
    return fold


# The build's constants the review crate reads through the build input's vocabulary (`corpus_exec.VOCABULARY`): the most cached units the verification sample recomputes and compares, the key-only read's window ranges of the audit, each walked on a thread of its own, and the most recomputed units the units phase hands a thread at a time, the enricher's settlement batch width, so a thread holds one batch of units and of projections with their deflated fragments.
VERIFICATION_SAMPLE = 200
KEY_ONLY_PROCESSES = 6
PHASE1_HANDOUT_UNITS = EXPLAIN_UNIT_BATCH_SIZE


@dataclass(frozen=True)
class _ClassLayout:
    """One class in the materialized form: its live slots in id order, the base part names and sizes a from-scratch build gives them, each slot's address there, at its plain length, and each part's run of slots."""

    slots: list[unit_index.LiveSlot]
    ids: list[str]
    names: list[str]
    sizes: list[int]
    addresses: list[tuple[int, int, int]]
    bounds: list[tuple[int, int]]


class MaterializedCorpus:
    """The corpus's materialized form (#618's decision 48): the files a from-scratch build of the same units writes, from which a corpus the build appended to, folded or wrote directly differs only in its layout and in the form its fragments are held in, and one whose units index is a head, a base and segments differs in that too. Each class's live fragments (`unit_index.LiveCorpus`) are laid into base parts in id order by the from-scratch writer's rule (`unit_index.PartLayout`), each at its plain length, a deflated fragment's inflated length as the unit store records it (decision 76), so nothing is inflated to lay it out. The parts hold every fragment plain, a deflated one inflated as it is read (change 35), the manifest lists those parts, the app sidecars' rows and the unit store's records give each fragment its plain address there with no deflated fragment's marker, the locator's blocks are cut again over the moved rows, the units index is its base and segments merged by key with each record's place from the manifest (`unit_index.materialized_index`, change 32), every part checked against its head's sha256, and every stamp names the manifest that lists those parts. `materialize_corpus` writes the whole tree, for the tests, and `rebuild/tools/perf_fingerprint.py` digests it without writing anything. `identical` says the corpus already is its materialized form: every class's parts, their sizes and every live fragment's address as a from-scratch build gives them, and no fragment deflated, as on a corpus no build has appended to, folded or left a dead slot in."""

    STORE_FILES = re.compile(rf"{re.escape(unit_cache.STORE_NAME)}(?:\.head|\.\d+\.base|\.\d+\.\d+\.delta)?")
    INDEX_FILES = re.compile(rf"{re.escape(unit_index.INDEX_NAME)}(?:\.head|\.\d+\.base|\.\d+\.\d+\.delta)?")
    SIDECARS = (
        unit_index.INDEX_NAME,
        app_index.APP_INDEX_NAME,
        app_index.LOCATOR_NAME,
        app_index.LOCATOR_ROWS_NAME,
    )

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)
        self.live = unit_index.LiveCorpus(self.out_dir)
        self.manifest = json.loads(json.dumps(self.live.manifest))
        self._layouts: dict[str, _ClassLayout] = {}
        self._part_class: dict[str, str] = {}
        identical = True
        for meta in self.manifest.get("classes") or ():
            class_id = str(meta.get("id", ""))
            on_disk = unit_index.class_shards(meta)
            self._part_class.update(dict.fromkeys(on_disk, class_id))
            slots = self.live.slots(meta)
            layout = unit_index.PartLayout(SHARD_PART_BYTES)
            addresses = []
            for slot in slots:
                index, start, _first = layout.place(slot.content_length)
                addresses.append((index, start, slot.content_length))
            sizes = layout.sizes()
            names = unit_index.base_part_names(class_id, len(sizes))
            bounds = []
            position = 0
            for index in range(len(sizes)):
                low = position
                while position < len(addresses) and addresses[position][0] == index:
                    position += 1
                bounds.append((low, position))
            if (
                names != on_disk
                or any(slot.inflated for slot in slots)
                or [(self.out_dir / part).stat().st_size for part in on_disk] != sizes
                or any(
                    slot.part != names[address[0]] or slot.start != address[1]
                    for slot, address in zip(slots, addresses, strict=True)
                )
            ):
                identical = False
                meta.pop("shard", None)
                meta["shards"] = names
            self._layouts[class_id] = _ClassLayout(
                slots, [slot.unit_id for slot in slots], names, sizes, addresses, bounds
            )
        self.identical = identical
        self.manifest_sha256 = unit_index.manifest_identity(self.manifest)

    def close(self) -> None:
        self.live.close()

    def __enter__(self) -> MaterializedCorpus:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def manifest_bytes(self) -> bytes:
        return (json.dumps(self.manifest, indent=1, ensure_ascii=True) + "\n").encode()

    def part_files(self) -> Iterator[tuple[str, Iterator[bytes]]]:
        """Yield each materialized part's name and its bytes, as chunks read one live fragment at a time, a deflated one inflated (`unit_index.LiveCorpus.read`)."""
        for meta in self.manifest.get("classes") or ():
            layout = self._layouts[str(meta.get("id", ""))]
            for name, (low, high) in zip(layout.names, layout.bounds, strict=True):
                yield name, self._part_chunks(layout, low, high)

    def _part_chunks(self, layout: _ClassLayout, low: int, high: int) -> Iterator[bytes]:
        if low == high:
            yield b"[]\n"
            return
        for position in range(low, high):
            yield (b"[" if position == low else b",") + self.live.read(layout.slots[position])
        yield unit_index.PART_CLOSING

    def layout(self, class_id: str) -> _ClassLayout:
        """Return one class's materialized layout."""
        return self._layouts[class_id]

    def address(self, class_id: str, unit_id: str) -> tuple[int, int, int] | None:
        """Return a live fragment's materialized address as a part index into its class's materialized parts, a byte start and a length, or None for a unit its class does not hold."""
        layout = self._layouts.get(class_id)
        if layout is None:
            return None
        ids = layout.ids
        position = bisect.bisect_left(ids, unit_id)
        if position < len(ids) and ids[position] == unit_id:
            return layout.addresses[position]
        return None

    def _remap_row(self, line: bytes) -> bytes:
        """Return a sidecar row with its fragment's materialized address, and without the trailing `"deflated": true` a deflated fragment's row carries (#618's decision 77), which the materialized form's plain fragments never take; a plain row already at its materialized address is returned as it is."""
        row = json.loads(line)
        address = (row["shard_part"], row["byte_start"], row["byte_length"])
        moved = self.address(str(row.get("class")), row["id"]) or address
        if moved == address and "deflated" not in row:
            return line
        tail = json.dumps({"shard_part": moved[0], "byte_start": moved[1], "byte_length": moved[2]}).encode()
        return line[: line.rindex(b', "shard_part": ')] + b", " + tail[1:] + b"\n"

    def _sidecar_lines(self, name: str) -> Iterator[bytes]:
        with gzip.open(self.out_dir / name, "rb") as stream:
            next(stream)
            yield from stream

    def sidecar_header(self, name: str) -> dict:
        """Return a sidecar's header as the materialized form has it: the file's own, the units index's its head's stamps (`unit_index.index_header`), its manifest stamp the materialized manifest's."""
        if name == unit_index.INDEX_NAME:
            header = unit_index.index_header(self.out_dir) or {}
        else:
            header = app_index.artifact_header(self.out_dir, name) or {}
        return {**header, "manifest_sha256": self.manifest_sha256}

    def index_lines(self) -> Iterator[bytes]:
        """Return the units index's materialized record lines (`unit_index.materialized_index`), every part checked against its head's size and sha256."""
        materialized = unit_index.materialized_index(self.out_dir, sha256=True)
        if materialized is None:
            raise FileNotFoundError(unit_index.index_path(self.out_dir))
        return materialized[1]

    def app_lines(self) -> Iterator[bytes]:
        return (self._remap_row(line) for line in self._sidecar_lines(app_index.APP_INDEX_NAME))

    def locator(self) -> tuple[bytes, list[dict], int]:
        """Return the locator's rows file, its blocks cut again over the materialized addresses (`app_index.LocatorBlocks`), and its row count."""
        rows = io.BytesIO()
        blocks = app_index.LocatorBlocks(rows)
        count = 0
        with gzip.open(self.out_dir / app_index.LOCATOR_ROWS_NAME, "rb") as stream:
            for line in stream:
                blocks.add(self._remap_row(line))
                count += 1
        table = blocks.close()
        return rows.getvalue(), table, count

    def store(self) -> tuple[dict, Iterator[bytes]] | None:
        """Return the unit store's materialized form (`unit_cache.materialized_store`) with every record's address the materialized one, three elements with no deflated fragment's inflated length, and the header's manifest stamp and part sizes the materialized parts', every store part checked against its head's size and sha256."""
        sizes = {
            name: size
            for layout in self._layouts.values()
            for name, size in zip(layout.names, layout.sizes, strict=True)
        }

        def address_of(
            unit_id: str, address: tuple[str, int, int, int] | None
        ) -> tuple[str, int, int] | None:
            if address is None:
                return None
            class_id = self._part_class.get(address[0])
            moved = None if class_id is None else self.address(class_id, unit_id)
            if moved is None:
                return address[:3]
            return self._layouts[str(class_id)].names[moved[0]], moved[1], moved[2]

        return unit_cache.materialized_store(
            self.out_dir,
            address_of=address_of,
            stamps={"manifest_sha256": self.manifest_sha256, "parts": dict(sorted(sizes.items()))},
            sha256=True,
        )

    def is_layout_file(self, relative: str) -> bool:
        """Whether a corpus file is one the materialized form replaces: a shard part, the manifest, a sidecar, one of the units index's files or one of the unit store's."""
        return (
            relative.startswith("units/")
            or relative == "manifest.json"
            or relative in self.SIDECARS
            or self.INDEX_FILES.fullmatch(relative) is not None
            or self.STORE_FILES.fullmatch(relative) is not None
        )


def materialize_corpus(out_dir: Path, target: Path) -> None:
    """Write the corpus at `out_dir` in its materialized form (`MaterializedCorpus`) into `target`, which must not exist, with the files a from-scratch build writes: every other file copied, the parts, the manifest, the app sidecars through their own writers, the units index as the single file `units-index.ndjson.gz` and the unit store as the single file `unit-cache.ndjson.gz`. A corpus built from scratch and one appended to over the same units materialize to the same bytes."""
    out_dir, target = Path(out_dir), Path(target)
    target.mkdir(parents=True)
    with MaterializedCorpus(out_dir) as corpus:
        for path in sorted(out_dir.rglob("*")):
            relative = path.relative_to(out_dir).as_posix()
            if path.is_file() and not corpus.is_layout_file(relative):
                (target / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target / relative)
        (target / "units").mkdir()
        for name, chunks in corpus.part_files():
            with open(target / name, "wb") as handle:
                for chunk in chunks:
                    handle.write(chunk)
        _write_json(target / "manifest.json", corpus.manifest)
        unit_index.write_materialized_index(
            target, corpus.sidecar_header(unit_index.INDEX_NAME), corpus.index_lines()
        )
        rows, blocks, machine = corpus.locator()
        human = int(corpus.sidecar_header(app_index.APP_INDEX_NAME).get("units") or 0)
        app_index.write_app_artifacts_lines(
            target, corpus.app_lines(), [(rows, blocks)], human=human, machine=machine
        )
        store = corpus.store()
        if store is not None:
            header, lines = store
            unit_cache.write_materialized_store(target, header, lines)


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
    recompute_all_units: bool = False,
    spec_root: Path | None = None,
    subset_pack: Path | None = None,
    gates_idle_jobs: int | None = None,
    gates_idle_marker: Path | None = None,
    write_jobs: int | None = None,
) -> dict:
    """Build the m1-audit review corpus into `out_dir` with the review crate's build process (`ams-review build`, `corpus_exec.run_build`) at `jobs` threads, and return the manifest it wrote, read back from `out_dir`. The audit is held open first: its head read and every part it lists opened here, before anything else, or the single file a tree with no head holds (`audit.AuditParts`), and those descriptors are the ones every read of the audit goes through until the build returns, the process's reads and the manifest's stamp's (`_generated_at`) alike, never a path again, since a `--gates-only` pass can commit a new head during a build. The build holds one descriptor a part, up to `row_delta.AUDIT_SEGMENT_CAP` and `row_delta.FOLD_OVERRUN` segments past it beside the base, and the process inherits them.

    What comes before the process is Python's: the subset tables' check and the pack (`_packed_subsets`); the units init (`units_exec.write_init`), which derives the configuration fold here, so its line prints before the load; and the build input's values (`_build_inputs`), written with the held audit's descriptors (`corpus_exec.write_input`). The init, the input and the process's spill files are in a temporary directory held until the process exits. A refusal raises `SystemExit` and any other failure `corpus_exec.BuildRunError`.
    """
    parts = audit.AuditParts(audit_path)
    try:
        spec_root = Path(spec_root) if spec_root is not None else Path(repo_root)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        subset_digests, subset_pack = _packed_subsets(subset_dir, subset_pack)
        with tempfile.TemporaryDirectory(prefix="ams-review-build-") as held:
            directory = Path(held)
            site_pages = fingerprint.read_site_corpus(repo_root)
            draft_lines = fingerprint.draft_input_lines(repo_root, site_pages)
            init = {
                "before_font": before_font,
                "after_font": after_font,
                "junior_font": junior_font,
                "subset_dir": subset_dir,
                "subset_pack": subset_pack,
                "repo_root": repo_root,
                "spec_root": spec_root,
                "identical_classes": frozenset(
                    entry.id for entry in load_ledger(ledger_path) if entry.ink_identical
                ),
                "fold": _config_fold(before_font, after_font),
                "corpus_index": corpus_index_from_pages(site_pages),
                "dictionary": FRAGMENT_DICTIONARY.read_bytes(),
            }
            del site_pages
            (directory / "init").mkdir()
            units_init = units_exec.write_init(init, directory / "init")
            del init
            scratch = directory / "spill"
            scratch.mkdir()
            values = _build_inputs(
                parts,
                out_dir=out_dir,
                audit_path=audit_path,
                ledger_path=ledger_path,
                subset_dir=subset_dir,
                before_font=before_font,
                after_font=after_font,
                junior_font=junior_font,
                repo_root=repo_root,
                batch_size=batch_size,
                static_dir=static_dir,
                jobs=jobs,
                signature_jobs=signature_jobs,
                recompute_all_units=recompute_all_units,
                spec_root=spec_root,
                subset_digests=subset_digests,
                gates_idle_jobs=gates_idle_jobs,
                gates_idle_marker=gates_idle_marker,
                write_jobs=write_jobs,
                draft_lines=draft_lines,
                units_init=units_init,
                scratch=scratch,
            )
            input_path = corpus_exec.write_input(values, directory / "build.json", parts.descriptors)
            del values
            corpus_exec.run_build(input_path, parts.descriptors)
        return json.loads((out_dir / "manifest.json").read_bytes())
    finally:
        parts.close()


def _packed_subsets(subset_dir: Path, subset_pack: Path | None) -> tuple[dict[str, str], Path]:
    """Check that every acceptance config's baseline subset table is there and not empty, which fails the build before any work starts since each goes into the pack, then write the pack where its tables changed (`ensure_pack`), and return the tables' digests and the pack's path. The pack is written before the build process that maps it starts, and its own processes, one a configuration (`subset_pack.write_pack`), have exited by then, so a pass that rewrites the pack spends that time ahead of the step's first phase line. Its table digests are also the environment stamp's `subsets` line, so each table is hashed once for both."""
    missing_subsets = [
        config
        for config in ACCEPTANCE_CONFIGS
        if not (subset_dir / f"baseline-{config}.subset.tsv.gz").is_file()
        or (subset_dir / f"baseline-{config}.subset.tsv.gz").stat().st_size == 0
    ]
    if missing_subsets:
        raise SystemExit(
            f"missing or empty baseline subset tables under {subset_dir}: {', '.join(missing_subsets)}"
        )
    subset_digests = table_digests(subset_dir, ACCEPTANCE_CONFIGS)
    return subset_digests, ensure_pack(subset_dir, ACCEPTANCE_CONFIGS, subset_digests, subset_pack)


def _stamp_values(stamp: fingerprint.EnvironmentStamp) -> dict:
    """A whole-store stamp as the build input carries it: its value, its lines, the lines behind each detailed label, and the header fields its store writes (`unit_cache._stamp_header`)."""
    return {
        "value": stamp.value,
        "lines": stamp.lines,
        "detail": stamp.detail,
        "header": unit_cache._stamp_header(stamp),
    }


def _build_inputs(
    parts: audit.AuditParts,
    *,
    out_dir: Path,
    audit_path: Path,
    ledger_path: Path,
    subset_dir: Path,
    before_font: Path,
    after_font: Path,
    junior_font: Path,
    repo_root: Path,
    batch_size: int,
    static_dir: Path,
    jobs: int,
    signature_jobs: int,
    recompute_all_units: bool,
    spec_root: Path,
    subset_digests: Mapping[str, str],
    gates_idle_jobs: int | None,
    gates_idle_marker: Path | None,
    write_jobs: int | None,
    draft_lines: Sequence[str],
    units_init: Path,
    scratch: Path,
) -> dict[str, object]:
    """Return the values of the build input (`corpus_exec.INPUT_FIELDS`) for a build of `build_m1`'s arguments over the held audit `parts`: the ledger, the family keys and both stores' stamps, the unit store's with its `units_engine` line and the signature store's with its `crate_code` line, both the review crate's digest; the manifest's values, `generated_at` over the held audit's content time and each font record with its `upem`; the fonts' digests, which the process checks each copy against; the static files `copy_static` copies; the widths; and what the build reads from the environment, the cycle's run, its fold claim's file and the output-counts file. `draft_lines` is the site pages' and draft harness's lines (`fingerprint.draft_input_lines`) the caller took from the read its units init's corpus index came from, `units_init` the units init it wrote (`units_exec.write_init`) and `scratch` the directory the process's spill files go in. Paths are absolute strings, but the audit's and the ledger's, which messages name as given."""
    out_dir, repo_root, spec_root = Path(out_dir), Path(repo_root), Path(spec_root)
    ledger = load_ledger(ledger_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = load_spec(spec_root)
    family_keys, helpers_digest = unit_cache.family_content_keys(spec_root, spec, after_font)
    identical_classes = frozenset(entry.id for entry in ledger if entry.ink_identical)
    crate = units_exec.crate_digest()
    environment = unit_cache.environment_stamp(
        repo_root,
        spec,
        subset_dir,
        before_font,
        junior_font,
        helpers_digest,
        subset_digests=subset_digests,
        identical_classes=identical_classes,
        draft_lines=draft_lines,
        units_engine=crate,
    )
    del spec
    sources = {"before": Path(before_font), "after": Path(after_font)}
    copies = {
        side: {
            "source": str(source.absolute()),
            "name": f"{side}.otf",
            "family": family,
            "sha256": _sha256(source),
        }
        for (side, source), family in zip(
            sources.items(), ("AMS Review Before", "AMS Review After"), strict=True
        )
    }
    static_files = fingerprint.static_files(static_dir) if Path(static_dir).is_dir() else []
    return {
        "format": corpus_exec.INPUT_FORMAT,
        "units_init": str(Path(units_init).absolute()),
        "out_dir": str(out_dir.absolute()),
        "scratch": str(Path(scratch).absolute()),
        "audit": {
            "path": str(audit_path),
            "head": None if parts.head is None else parts.head.document(),
            "members": parts.members,
            "identity": unit_cache.audit_identity(parts.descriptors[0], parts.head),
            "segment_rows": parts.segment_rows,
            "compressed": parts.compressed,
        },
        "ledger": {"path": str(ledger_path), "entries": ledger},
        "family_of": dict(LETTERS),
        "family_keys": family_keys,
        "family_keys_digest": unit_cache.family_keys_digest(family_keys),
        "change_groups": {"order": CHANGE_GROUP_ORDER, "prose": load_change_group_prose()},
        "environment": _stamp_values(environment),
        "signature_environment": _stamp_values(
            unit_cache.signature_environment(repo_root, before_font, helpers_digest, crate)
        ),
        "manifest": {
            "generated_at": _generated_at(ledger_path, before_font, after_font, mtimes=(parts.content_time,)),
            "repo_head": _repo_head(repo_root),
            "inputs_fingerprint": _inputs_fingerprint(
                repo_root, subset_dir, before_font, junior_font, spec_root, draft_lines=draft_lines
            ),
            "source": {
                "audit": _relative(audit_path, repo_root),
                "ledger": _relative(ledger_path, repo_root),
            },
            "fonts": {
                side: {
                    "file": f"fonts/{copy['name']}",
                    "family": copy["family"],
                    "source": _relative(sources[side], repo_root),
                    "sha256": copy["sha256"],
                    "upem": _upem(sources[side]),
                }
                for side, copy in copies.items()
            },
            "alphabet": _alphabet_meta(),
            "configs": list(ACCEPTANCE_CONFIGS),
            "feature_descriptions": dict(FEATURE_DESCRIPTIONS),
            "batch_size": batch_size,
            "junior_font": {"source": _relative(junior_font, repo_root), "sha256": _sha256(junior_font)},
            "verification_methods": {
                "ink_identical": VERIFICATION_METHOD,
                "picture_identical": PICTURE_VERIFICATION_METHOD,
                "junior_equivalent": JUNIOR_VERIFICATION_METHOD,
            },
            "build_command": BUILD_COMMAND,
            "serve_command": SERVE_COMMAND,
        },
        "fonts": copies,
        "static": {
            "files": [
                (str(source.relative_to(static_dir)), str(source.absolute())) for source in static_files
            ],
            "fallback_index": _FALLBACK_INDEX.format(build=BUILD_COMMAND, serve=SERVE_COMMAND),
        },
        "repo_root": str(repo_root.absolute()),
        "recompute_all": recompute_all_units,
        "widths": {
            "threads": jobs,
            "gates_idle_threads": gates_idle_jobs,
            "gates_idle_marker": (
                None if gates_idle_marker is None else str(Path(gates_idle_marker).absolute())
            ),
            "signature_threads": signature_jobs,
            "write_threads": write_jobs,
        },
        "cycle": {
            "run": os.environ.get(row_delta.CYCLE_RUN_ENV) or None,
            "fold_claim": str((cycle_paths.CYCLE_VAR / cycle_paths.FOLD_CLAIM_NAME).absolute()),
            "counts": os.environ.get(output_sink.OUTPUT_COUNTS_ENV) or None,
        },
        "vocabulary": corpus_exec.vocabulary(),
    }


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
    """One table-diff entry's fragment, with the same content identity an m1-audit unit has: it is built with `id` and `content_key` None, stamped with the hash of its carry projection, and given `unit_cache.unit_id_for` of that stamp as its id. `row_key` is the entry's table key (`key.label()`), which keeps the row in the projection: the class and the summary are outside it, and two settlement rows of one input can share an example text and both outcomes."""
    example_text = entry.example_text
    gate, note = config_badge((entry.config,), full_configs)
    if entry.table == "join":
        old = entry.old
        new = entry.new
        before = {
            "glyphs": [entry.key.left, entry.key.right],
            "junctions": [old.junction if old else "absent"],
        }
        after = {
            "cells": [entry.key.left, entry.key.right],
            "junctions": [new.junction if new else "absent"],
            "extensions": [new.extension if new else 0],
        }
        diff_positions = [0, 1]
        pair = {"left": 0, "right": 1}
        explain = _join_explain(entry)
        provenance: list[str] = []
        summary = (
            f"The join row for {entry.key.label()} is {entry.bucket} under {entry.config}; "
            "old and new values are in the explain panel."
        )
    else:
        members = entry.paired or (entry,)
        before = {
            "glyphs": [member.old.outcome for member in members if member.old is not None],
            "junctions": [],
        }
        after = {
            "cells": [member.new.outcome for member in members if member.new is not None],
            "junctions": [],
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
        "duplicate_group": None,
        "cluster": None,
        "class": entry.bucket,
        "group": f"{entry.table}:{getattr(entry.key, 'input', getattr(entry.key, 'left', ''))}",
        "row_key": entry.key.label(),
        "codepoints": ":".join(f"{value:04X}" for value in example_text) if example_text else None,
        "text_entities": text_entities(example_text) if example_text else None,
        "notation": notation(example_text) if example_text else entry.key.label(),
        "notation_tokens": list(notation_tokens(example_text)) if example_text else None,
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


def _join_explain(entry: tablediff.JoinDiffEntry) -> str:
    lines = [f"join diff ({entry.bucket}), config {entry.config}", f"  pair: {entry.key.label()}"]
    if entry.old is not None:
        lines.append(f"    old: junction {entry.old.junction}, extension {entry.old.extension}")
    if entry.new is not None:
        lines.append(f"    new: junction {entry.new.junction}, extension {entry.new.extension}")
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
    with_example_texts: bool = True,
    example_depth: int = 5,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = tablediff.diff_dirs(baseline_dir, new_dir)
    if not entries:
        raise SystemExit(
            f"{baseline_dir} and {new_dir} settle every window alike, so there is nothing to diff"
        )

    if with_example_texts:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                spec = load_spec(repo_root)
            for config in sorted({entry.config for entry in entries}):
                tablediff.ExampleIndex(spec, config, max_depth=example_depth).attach(entries)
        except Exception as error:  # noqa: BLE001 — example texts are an enrichment, not a gate
            print(f"warning: example-text search unavailable ({error})", file=sys.stderr)

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
        check_counts = {check_id: 0 for check_id in MACHINE_CHECKS}
        for entry in members:
            # An entry without an example text has no text to shape, so it cannot be shown ink- or picture-identical and stays a human unit.
            text = "".join(chr(value) for value in entry.example_text) if entry.example_text else ""
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
                check_counts["ink_identical" if ink_identical else "picture_identical"] += 1
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
                "title": tablediff.BUCKET_TITLE[bucket],
                "summary": tablediff.BUCKET_SUMMARY[bucket],
                "note": None,
                "section": class_section(None, False, len(members) - machine_count),
                "status": None,
                "ink_identical": False,
                "no_verdict": False,
                "why": tablediff.BUCKET_WHY[bucket],
                "unit_count": len(members),
                "row_count": sum(max(len(entry.paired), 1) for entry in members),
                "machine_approved_count": machine_count,
                "machine_checks": check_counts,
                "shards": parts,
                "batches": sorted(batches),
            }
        )

    # A bucket with no human unit is listed with No review needed, after the buckets that hold questions; it adds nothing to the triage index, so moving it keeps the index in class order.
    classes_meta.sort(key=lambda meta: SECTIONS.index(meta["section"]))
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
        "ledger_classes": {},
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
            need(
                isinstance(totals.get("duplicate_groups"), int), "totals.duplicate_groups must be an integer"
            )
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
        checks = machine.get("checks")
        if checks is not None:
            need(
                isinstance(checks, dict) and set(checks) == set(MACHINE_CHECKS),
                "machine_approved.checks must map the three machine checks",
            )
            if isinstance(checks, dict):
                for check_id, record in checks.items():
                    if not isinstance(record, dict):
                        need(False, f"machine_approved.checks.{check_id} must be a mapping")
                        continue
                    for key in ("units", "rows"):
                        need(
                            isinstance(record.get(key), int),
                            f"machine_approved.checks.{check_id}.{key} must be an integer",
                        )
                    need(
                        isinstance(record.get("method"), str) and record.get("method"),
                        f"machine_approved.checks.{check_id}.method must be a nonempty string",
                    )
    secondary_junction_counts = manifest.get("secondary_junctions")
    if secondary_junction_counts is not None:
        need(
            isinstance(secondary_junction_counts, dict)
            and {
                "units_with_markers",
                "junctions_with_primary_unit",
                "junctions_without_primary_unit",
                "junctions_suppressed_invisible",
            }
            == set(secondary_junction_counts)
            and all(isinstance(count, int) for count in secondary_junction_counts.values()),
            "secondary_junctions must carry the four integer counts",
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
    statuses = (None,) if manifest.get("mode") == "table-diff" else LEDGER_STATUSES
    section_ranks: list[int] = []
    for meta in classes or ():
        identifier = meta.get("id", "<missing>")
        for key in ("id", "why"):
            need(isinstance(meta.get(key), str), f"classes[{identifier}].{key} must be a string")
        for key in ("title", "summary"):
            need(
                isinstance(meta.get(key), str) and meta[key].strip(),
                f"classes[{identifier}].{key} must be a nonempty string",
            )
        note = meta.get("note")
        need(
            note is None or (isinstance(note, str) and note.strip()),
            f"classes[{identifier}].note must be absent, null or a nonempty string",
        )
        status = meta.get("status")
        need(
            status in statuses, f"classes[{identifier}].status must be one of {', '.join(map(str, statuses))}"
        )
        section = meta.get("section")
        need(section in SECTIONS, f"classes[{identifier}].section must be one of {', '.join(SECTIONS)}")
        units, machine = meta.get("unit_count"), meta.get("machine_approved_count")
        if section in SECTIONS and status in statuses and isinstance(units, int) and isinstance(machine, int):
            section_ranks.append(SECTIONS.index(section))
            need(
                section == class_section(status, meta.get("no_verdict") is True, units - machine),
                f"classes[{identifier}].section must be the one audit.class_section gives its status, "
                f"exemption and {units - machine} human units",
            )
        shards = meta.get("shards")
        need(
            isinstance(shards, list) and shards and all(isinstance(part, str) for part in shards),
            f"classes[{identifier}].shards must be a nonempty list of paths",
        )
        for key in ("unit_count", "row_count", "machine_approved_count"):
            need(isinstance(meta.get(key), int), f"classes[{identifier}].{key} must be an integer")
        checks = meta.get("machine_checks")
        well_formed = (
            isinstance(checks, dict)
            and set(checks) == set(MACHINE_CHECKS)
            and all(isinstance(count, int) for count in checks.values())
        )
        need(
            well_formed,
            f"classes[{identifier}].machine_checks must count the three machine checks",
        )
        need(isinstance(meta.get("batches"), list), f"classes[{identifier}].batches must be a list")
        need("status" in meta, f"classes[{identifier}].status must be present")
        need(
            isinstance(meta.get("ink_identical"), bool), f"classes[{identifier}].ink_identical must be a bool"
        )
        need(isinstance(meta.get("no_verdict"), bool), f"classes[{identifier}].no_verdict must be a bool")
    need(section_ranks == sorted(section_ranks), f"classes must be listed by section ({', '.join(SECTIONS)})")
    lookup = manifest.get("ledger_classes")
    need(isinstance(lookup, dict), "ledger_classes must map ledger class ids to their copy")
    for identifier, record in lookup.items() if isinstance(lookup, dict) else ():
        need(
            isinstance(record, dict)
            and all(isinstance(record.get(key), str) and record[key].strip() for key in ("title", "summary"))
            and record.get("status") in LEDGER_STATUSES
            and isinstance(record.get("why"), str),
            f"ledger_classes[{identifier}] must carry a nonempty title and summary, a ledger status and a why",
        )
    return errors


def _is_delta_digest(token) -> bool:
    return (
        isinstance(token, str)
        and len(token) == 14
        and token.startswith("d-")
        and all(ch in "0123456789abcdef" for ch in token[2:])
    )


# How many lines of a failing contract check the build prints, which the review crate reads through the build input's vocabulary (`corpus_exec.VOCABULARY`) and the units init's (`units_exec.VOCABULARY`); it also caps how many drafting-time errors the units phase carries to the write.
CONTRACT_ERRORS_SHOWN = 20


def check_unit(unit: dict, mode: str = "m1-audit") -> list[str]:
    """The per-unit half of the §7 contract check, over one fragment read back from a corpus: the identity and its stamp, the machine flags and `ink_deltas`, the class, the ledger class and the group, the table-diff row key, the window, the junctions, the highlight, the notation, the summary and explain, the config badge, the drafts, `duplicate_group`, `cluster`, and the secondary junctions with their primary units. The review crate runs the same predicates over every fragment it writes; `check_shards` and `check_output_dir` run these over a corpus held in memory or read back from disk."""
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

    approving = [check_id for check_id in MACHINE_CHECKS if unit.get(check_id) is True]
    human = not approving and unit.get("no_verdict") is not True
    pair = unit.get("pair")

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
    need(len(approving) <= 1, "at most one machine check may approve a unit")
    for key in ("class", "group", "notation", "summary"):
        need(isinstance(unit.get(key), str) and unit.get(key) != "", f"{key} must be a nonempty string")
    if mode == "table-diff":
        need(
            isinstance(unit.get("row_key"), str) and unit.get("row_key") != "",
            "row_key must be a nonempty string in table-diff mode",
        )
    ledger_class = unit.get("ledger_class")
    need(
        ledger_class is None
        or (isinstance(ledger_class, str) and ledger_class and ledger_class != unit.get("class") and human),
        "ledger_class must be absent, null, or on a human unit the id of a class other than its own",
    )
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
    # A unit has one render group because its configs cannot render differently: its rows either share (codepoints, baseline, new), or the build's ink-duplicate merge merged them because their ink is identical in every config. Data that broke this would need stacked rendering, so it is a build error.
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
        isinstance(before, dict) and isinstance(before.get("junctions"), list),
        "before.junctions must be a list",
    )
    need(isinstance(after, dict) and isinstance(after.get("cells"), list), "after.cells must be a list")
    need(
        isinstance(after, dict) and isinstance(after.get("junctions"), list),
        "after.junctions must be a list",
    )
    need(
        isinstance(after, dict) and isinstance(after.get("extensions"), list),
        "after.extensions must be a list",
    )
    if isinstance(before, dict) and isinstance(before.get("junctions"), list):
        need(
            all(is_junction_token(junction) for junction in before["junctions"]),
            "before.junctions must be break/lig/yN tokens",
        )
    if isinstance(after, dict) and isinstance(after.get("junctions"), list):
        need(
            all(is_junction_token(junction) for junction in after["junctions"]),
            "after.junctions must be break/lig/yN tokens",
        )
    if mode == "m1-audit" and isinstance(before, dict) and isinstance(after, dict):
        need(
            len(before.get("junctions", ())) == max(len(before.get("glyphs", ())) - 1, 0),
            "before.junctions must have one entry per inter-glyph gap",
        )
        need(
            len(after.get("junctions", ())) == max(len(after.get("cells", ())) - 1, 0),
            "after.junctions must have one entry per inter-cell gap",
        )
        need(
            len(after.get("extensions", ())) == len(after.get("junctions", ())),
            "after.extensions must parallel after.junctions",
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
                # The after-behavior candidate is the pin's `expect`, which the `drafts.pin.syntax` check above covers. The before-behavior candidate comes from the baseline side of the enrichment. The crate's any-of drafter already parses it and fails the unit when it does not parse; this parse repeats the check so that a corpus read from disk is covered too.
                if mode == "m1-audit":
                    expect = pin.get("expect") if isinstance(pin, dict) else None
                    for candidate in candidates:
                        if candidate == expect or not isinstance(candidate, str):
                            continue
                        try:
                            parse_expect(candidate)
                        except ValueError as error:
                            need(False, f"drafts.any_of candidate {candidate!r} does not parse: {error}")

    need("duplicate_group" in unit, "duplicate_group must be present")
    duplicate_group = unit.get("duplicate_group")
    need(
        duplicate_group is None or (isinstance(duplicate_group, str) and duplicate_group.startswith("e-")),
        "duplicate_group must be null or an e- group id",
    )
    if mode == "m1-audit":
        if human:
            need(isinstance(duplicate_group, str), "human units must carry a duplicate group id")
        else:
            need(
                duplicate_group is None,
                "machine-approved and no-verdict units must carry duplicate_group null",
            )
    need("cluster" in unit, "cluster must be present")
    cluster = unit.get("cluster")
    need(
        cluster is None or (isinstance(cluster, str) and cluster.startswith("c-")),
        "cluster must be null or a c-XXXXXXXX signature id",
    )
    if mode == "m1-audit":
        if human:
            need(isinstance(cluster, str), "human units must carry a cluster signature id")
        else:
            need(cluster is None, "machine-approved and no-verdict units must carry cluster null")

    junctions = unit.get("secondary_junctions")
    if junctions is not None:
        need(
            isinstance(junctions, list) and junctions,
            "secondary_junctions must be null or a nonempty list",
        )
        need(
            unit.get("ink_identical") is not True and unit.get("picture_identical") is not True,
            "ink-identical and picture-identical units must not carry secondary_junctions",
        )
        for index, junction in enumerate(junctions if isinstance(junctions, list) else ()):
            label = f"secondary_junctions[{index}]"
            if not isinstance(junction, dict) or {"pair", "before", "after", "primary_unit"} - set(junction):
                errors.append(f"unit {identifier}: {label} must carry pair/before/after/primary_unit")
                continue
            junction_pair = junction.get("pair")
            need(
                isinstance(junction_pair, dict)
                and isinstance(junction_pair.get("left"), int)
                and isinstance(junction_pair.get("right"), int)
                and junction_pair["left"] < junction_pair["right"],
                f"{label}.pair must be {{left, right}} with left < right",
            )
            if isinstance(pair, dict) and isinstance(junction_pair, dict):
                need(
                    (junction_pair.get("left"), junction_pair.get("right"))
                    != (pair.get("left"), pair.get("right")),
                    f"{label} must not duplicate the primary pair",
                )
            need_rect(junction.get("before"), f"{label}.before")
            need_rect(junction.get("after"), f"{label}.after")
            primary_unit = junction.get("primary_unit")
            need(
                primary_unit is None or (isinstance(primary_unit, str) and primary_unit.startswith("u-")),
                f"{label}.primary_unit must be null or a unit id",
            )
    return errors


class _CorpusCheck:
    """The per-unit and cross-unit parts of the §7 contract check as an accumulator fed one fragment at a time: `class_start` with the manifest's class record, `unit` for each fragment in shard order, `class_end` after the class's last fragment, and `finish` with the manifest for the predicates that read its totals. Per unit it keeps only what the cross-unit predicates read (its id, the grouping keys of a human unit, and its codepoints, whether there is a primary pair, and whether anything visible changed, which the primary-unit predicates read), never the fragment. `unit` runs `check_unit` on every fragment. Per-unit errors are recorded as each fragment is fed in, and cross-unit errors at `finish`."""

    def __init__(
        self,
        *,
        mode: str,
        descriptions: Mapping,
        batch_size: object,
        repo_root: Path | None,
    ) -> None:
        self._mode = mode
        self._descriptions = descriptions
        self._batch_size = batch_size
        self._repo_root = repo_root
        self.errors: list[str] = []
        self._seen_units = 0
        self._seen_rows = 0
        self._seen_ids: set[str | None] = set()
        self._seen_machine_by_class: dict[str, int] = {}
        self._primary_units: list[tuple[str | None, str]] = []
        self._junction_units = 0
        self._junctions_with_primary_unit = 0
        self._junctions_without_primary_unit = 0
        self._duplicate_group_keys: dict[str | None, set[tuple]] = {}
        self._cluster_keys: dict[str | None, set[tuple]] = {}
        self._duplicate_group_cluster: dict[str | None, str | None] = {}
        self._human_units: dict[str, tuple[str, str, str]] = {}
        self._identity: dict[str | None, tuple] = {}
        self._policy_files: set[str] = set()
        self._meta: Mapping = {}
        self._class_units = 0
        self._machine_count = 0
        self._check_counts: dict[str, int] = {}

    def class_start(self, meta: Mapping) -> None:
        self._meta = meta
        self._class_units = 0
        self._machine_count = 0
        self._check_counts = {check_id: 0 for check_id in MACHINE_CHECKS}
        if meta.get("no_verdict") and meta.get("batches"):
            self.errors.append(f"class {meta.get('id')}: a no-verdict class must carry no batches")

    def unit(self, unit: dict) -> None:
        """Check one fragment, in shard order."""
        errors = self.errors
        meta = self._meta
        mode = self._mode
        self._class_units += 1
        unit_id = unit.get("id")
        errors.extend(check_unit(unit, mode))
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
        # The duplicate group and cluster exist only in m1-audit. A table-diff unit has null for both, and treating null as a group id would put all its units in one group.
        if mode == "m1-audit" and human and isinstance(unit_id, str):
            key = (unit.get("class"), tuple(unit.get("configs") or ()))
            self._duplicate_group_keys.setdefault(unit.get("duplicate_group"), set()).add(key)
            self._cluster_keys.setdefault(unit.get("cluster"), set()).add(key)
            if self._duplicate_group_cluster.setdefault(
                unit.get("duplicate_group"), unit.get("cluster")
            ) != unit.get("cluster"):
                errors.append(
                    f"unit {unit_id}: duplicate group {unit.get('duplicate_group')} spans two clusters"
                )
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
            for check_id in MACHINE_CHECKS:
                if unit.get(check_id) is True:
                    self._check_counts[check_id] += 1
        if unit.get("secondary_junctions"):
            self._junction_units += 1
            for junction in unit["secondary_junctions"]:
                if not isinstance(junction, dict):
                    continue
                if junction.get("primary_unit") is None:
                    self._junctions_without_primary_unit += 1
                else:
                    self._junctions_with_primary_unit += 1
                    self._primary_units.append((unit.get("id"), junction["primary_unit"]))
        self._identity[unit_id] = (
            unit.get("codepoints"),
            unit.get("pair") is not None,
            unit.get("ink_identical") is True or unit.get("picture_identical") is True,
        )

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
        declared_checks = meta.get("machine_checks")
        if isinstance(declared_checks, dict) and dict(declared_checks) != self._check_counts:
            errors.append(
                f"class {meta.get('id')}: machine_checks {dict(declared_checks)} != "
                f"{self._check_counts} in the shards"
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
            errors.append("human_unit_ids does not match the shards' human units")
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
        for duplicate_group, keys in self._duplicate_group_keys.items():
            if len(keys) > 1:
                errors.append(f"duplicate group {duplicate_group}: one group spans {sorted(keys)[:2]}")
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
        for unit_id, primary_unit in self._primary_units:
            if primary_unit == unit_id:
                errors.append(f"unit {unit_id}: a secondary junction names itself as its primary unit")
            elif primary_unit not in seen_ids:
                errors.append(
                    f"unit {unit_id}: secondary junction's primary unit {primary_unit} is not a unit in this output"
                )
        secondary_junction_counts = manifest.get("secondary_junctions")
        # The primary unit relation can be checked only where the resolver assigned primary units, which is where it wrote the counts. A primary unit's window must be contained in this unit's window, the primary unit must have a primary pair, and it must show a visible change (a junction whose primary unit has none is counted in `junctions_suppressed_invisible` instead). A unit with no visible change must not carry a secondary junction.
        if isinstance(secondary_junction_counts, dict):
            for unit_id, primary_unit in self._primary_units:
                if primary_unit not in identity or unit_id not in identity:
                    continue
                tokens = (identity[unit_id][0] or "").split(":")
                primary_tokens = (identity[primary_unit][0] or "").split(":")
                contained = len(primary_tokens) <= len(tokens) and any(
                    tokens[offset : offset + len(primary_tokens)] == primary_tokens
                    for offset in range(len(tokens) - len(primary_tokens) + 1)
                )
                if not contained:
                    errors.append(
                        f"unit {unit_id}: secondary junction's primary unit {primary_unit} is not a substring window"
                    )
                if not identity[primary_unit][1]:
                    errors.append(
                        f"unit {unit_id}: secondary junction's primary unit {primary_unit} has no primary pair"
                    )
                if identity[primary_unit][2]:
                    errors.append(
                        f"unit {unit_id}: secondary junction's primary unit {primary_unit} shows no visible change"
                    )
                if identity[unit_id][2]:
                    errors.append(
                        f"unit {unit_id}: a unit with no visible change carries a secondary junction"
                    )
        if isinstance(secondary_junction_counts, dict):
            for key, observed in (
                ("units_with_markers", self._junction_units),
                ("junctions_with_primary_unit", self._junctions_with_primary_unit),
                ("junctions_without_primary_unit", self._junctions_without_primary_unit),
            ):
                if secondary_junction_counts.get(key) != observed:
                    errors.append(
                        f"secondary_junctions.{key} {secondary_junction_counts.get(key)} != {observed} in the shards"
                    )
        if self._repo_root is not None:
            for name in sorted(self._policy_files):
                if not (Path(self._repo_root) / name).is_file():
                    errors.append(f"drafts.policy names {name}, which is not a file in the repo")
        return errors


def check_shards(
    manifest: dict,
    shards_by_class: dict[str, list[dict]],
    repo_root: Path | None = None,
) -> list[str]:
    """Run the per-unit and cross-unit §7 checks over shard payloads held in memory, keyed by class id. The table-diff build passes the dicts it serialized and `check_output_dir` passes the shards it re-parsed from disk; the review crate runs the same predicates over the m1-audit corpus it writes. A class missing from the mapping is skipped here and reported by the caller. When `repo_root` is given, each distinct policy-draft file is checked to exist; every other predicate reads only the payload.

    The cross-unit predicates check what no single fragment can: each duplicate group and each cluster holds one class and one config set, every duplicate group lies inside one cluster, the manifest's `human_unit_ids` lists the human units in `audit.triage_key` order, every class's `batches` are the slices its units occupy in it, and each secondary junction's primary unit is a unit whose window this unit's window contains and which has a primary pair and a visible change.

    Apart from the policy-draft files, the cross-unit predicates read only fields that slim and full fragments both carry (the machine flags, the window, the pair, the class, group, configs, duplicate group and cluster), so the manifest's counts are checked over both kinds. `check_unit` checks that a slim fragment (`audit.slim_fragment`) omits `SLIM_OMITTED_KEYS` and a full one carries them.
    """
    check = _CorpusCheck(
        mode=manifest.get("mode", "m1-audit"),
        descriptions=manifest.get("feature_descriptions") or {},
        batch_size=manifest.get("batch_size"),
        repo_root=repo_root,
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


def _font_copy_current(side: str, copy: Path, digest: str, source: Path) -> bool:
    """Return whether a corpus's copied font still stands for the font it was copied from. The before font must equal its source byte for byte. The after font may differ from `rebuild/out/m1/M1.otf` in the `kern` feature's lookups alone (`fingerprint.after_font_current`): the corpus shapes with kerning off, so a kern edit recompiles M1.otf without making the corpus's copy stale (rebuild/KERN-PLAN.md section 7)."""
    if side == "after":
        return fingerprint.after_font_current(copy, digest, source)
    return _sha256(source) == digest


def _check_output_files(out_dir: Path, manifest: dict, repo_root: Path | None = None) -> list[str]:
    """Check the files beside the manifest: every shard part the manifest lists, base and overflow, is present and non-empty, a reader of part names that never opens one, the per-unit index and the app's two sidecars are present and stamped for this manifest, index.html exists, and each copied font matches the sha256 the manifest records and, when `repo_root` resolves its `source`, the font it was copied from. A build already guarantees the source comparison (`_copy_font` checks the copy against the digest taken at load), and the cycle's corpus skip and `make verdict-ready` compare the manifest's after-font sha256 with `rebuild/out/m1/M1.otf`; the comparison here is for `check_output_dir` and `refresh_assets`. The after font is compared as those two compare it (`_font_copy_current`), so a kern-only rebuild of M1.otf leaves the copy current."""
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
            if source.is_file() and not _font_copy_current(side, font_path, digest, source):
                errors.append(f"fonts.{side}: the copy is not {record['source']} as it stands on disk")
    if not (Path(out_dir) / "index.html").exists():
        errors.append("index.html is missing")
    if not (
        row_delta.head_path(out_dir, unit_index.INDEX_NAME).is_file()
        or unit_index.index_path(out_dir).is_file()
    ):
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
    missing = {
        meta.get("id", "")
        for meta in manifest.get("classes", ())
        if not all((out_dir / part).exists() for part in unit_index.class_shards(meta))
    }
    try:
        with unit_index.LiveCorpus(out_dir) as live:
            for meta, _slot, fragment in live.fragments(
                unit_index.MANIFEST_ORDER,
                classes=[
                    meta.get("id", "")
                    for meta in manifest.get("classes", ())
                    if meta.get("id", "") not in missing
                ],
            ):
                shards_by_class.setdefault(meta.get("id", ""), []).append(fragment)
    except unit_index.LayoutError as error:
        errors.append(str(error))
    for meta in manifest.get("classes", ()):
        if meta.get("id", "") not in missing:
            shards_by_class.setdefault(meta.get("id", ""), [])
    errors.extend(check_shards(manifest, shards_by_class, repo_root))
    errors.extend(_check_output_files(out_dir, manifest, repo_root))
    return errors


# --- CLI ------------------------------------------------------------------------


def review_server_listening(port: int = REVIEW_SERVER_PORT) -> bool:
    """Return whether something accepts connections on the review server's port (`rebuild.review.serve.PORT`). It is a bare socket check, not `rebuild.tools.review_server`, so the build's import closure stays inside the modules `rebuild/test_review_code_closure.py` allows."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def review_server_root(port: int = REVIEW_SERVER_PORT) -> str | None:
    """Return the repo root the review server on `port` names in its /capabilities answer (`rebuild.review.serve.capabilities_payload`), or None when it names none: nothing answers, the server predates /capabilities, or the answer carries no root. A bare urllib call, for the same reason as `review_server_listening`."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/capabilities", timeout=2.0) as response:
            answer = json.loads(response.read())
    except OSError, ValueError, http.client.HTTPException:
        return None
    root = answer.get("root") if isinstance(answer, dict) else None
    return root if isinstance(root, str) else None


def refuse_the_served_corpus(out: Path) -> None:
    """Refuse a build into the corpus the review server serves while the server listens. A build rewrites the shards the open tabs read by byte range and restamps the manifest their saves are keyed on; the artifact cycle builds beside the served corpus and lands the result with the verdict store (`rebuild.review.landing`), which keeps the tabs working. A build into any other directory, a build while the listening server names another checkout as its root (`review_server_root`), such as a worktree's first pass beside the main checkout's server, and `refresh-assets`, which renames each file into place, are allowed; a server that names no root may serve this checkout, so it blocks the build. So is a build into a served corpus with no manifest (a first run, after rebuild/out/review was deleted under a running server), which no tab can have loaded, and which the artifact cycle builds in place because it has nothing to land beside."""
    if Path(out).resolve() != DEFAULT_OUT.resolve() or not (DEFAULT_OUT / "manifest.json").exists():
        return
    if not review_server_listening():
        return
    root = review_server_root()
    if root is not None and Path(root).resolve() != REPO_ROOT.resolve():
        return
    raise SystemExit(
        f"REFUSING TO BUILD: the review server is listening on port {REVIEW_SERVER_PORT} and serves {DEFAULT_OUT}, so a build "
        "there would rewrite the files under its open tabs. Run `make artifact-cycle`, which builds beside the served corpus "
        "and lands it with the verdict store while the server keeps running, or pass --out <dir> for a build elsewhere."
    )


def main(argv: list[str] | None = None) -> None:
    refusal = spec_load.small_alphabet_refusal("the review build")
    if refusal is not None:
        raise SystemExit(refusal)
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
        corpus_job_budget,
        corpus_job_derivation,
    )

    corpus_jobs = corpus_job_budget(skip_gates=True)
    signature_jobs = signature_job_budget()
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
        default=corpus_jobs,
        help=f"the corpus build's threads, which the review crate's build process runs its units phase on; the default is `corpus_job_budget(skip_gates=True)`, the width a `--skip-gates` artifact cycle passes rather than a checked-in one, with no memory set aside for a gate pool because a hand run has no co-resident `make test` pool to leave cores or bytes to (a gated cycle passes the build lane's share of the cores instead, `memory_budget.split_cores`, with `--gates-idle-jobs` for when the gate lane is idle) — on this machine, {corpus_job_derivation(skip_gates=True)}, where the per-unit figure is one build thread's own peak and the co-resident one the build's process with the Python process waiting on it. It is the width a machine gets when the wider shape does not fit, and a deliberate `--jobs N` is also how a wider run gets measured, since a build records its peak for `make job-costs`.",
    )
    parser.add_argument(
        "--gates-idle-jobs",
        type=int,
        help="the units phase's width in place of `--jobs` when the file `--gates-idle-marker` names exists as the phase starts, after the load and plan phases. A gated artifact cycle passes both (`artifact_cycle.corpus_gates_idle_job_budget`) and writes that file once the gate lane has nothing left to run beside the units phase (`artifact_cycle._GateLaneWatch`); a hand run passes neither.",
    )
    parser.add_argument(
        "--gates-idle-marker",
        type=Path,
        help="the file whose presence as the units phase starts selects `--gates-idle-jobs`",
    )
    parser.add_argument(
        "--signature-jobs",
        type=int,
        default=signature_jobs,
        help=f"the threads the ink-signature phase shapes its store misses on, independent of `--jobs`: each shaper holds the two fonts, flat in the number of signature misses and pure CPU, so cores bind it where memory binds the units phase, and `--jobs 1` on a small machine still shapes signatures across the cores. The default is `signature_job_budget()`, the same width the artifact cycle passes — on this machine {signature_job_derivation()}. The build shapes its misses on this many threads at any count of misses and records no peaks.",
    )
    parser.add_argument(
        "--recompute-all-units",
        "--fresh-unit-cache",
        dest="recompute_all_units",
        action="store_true",
        help="reuse no unit from the persisted per-unit cache and recompute every unit; the prior store still gives each fragment's address, so the shards compare every recomputed fragment with its prior and append only what changed",
    )
    args = parser.parse_args(argv)
    refuse_the_served_corpus(args.out)

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
            recompute_all_units=args.recompute_all_units,
            gates_idle_jobs=args.gates_idle_jobs,
            gates_idle_marker=args.gates_idle_marker,
        )
    totals = manifest["totals"]
    print(
        f"Wrote {args.out} ({totals['units']} units, {totals['rows']} rows, {totals['batches']} batches)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
