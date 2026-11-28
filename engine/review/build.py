"""Build the review app's output directory, engine/out/review/ (doc/review-design.md §1.3): the table-diff corpus (`build_table_diff`), the accepted build as a snapshot holds it (`engine/review/snapshot.py`) against a build, with the units `engine/review/diff_units.py` finds, written as manifest.json, one unit shard per class (split into byte-capped parts when a class is too large for one file), the app sidecars (`engine/review/app_index.py`), copies of both fonts, and the static app files. The §7 contract checker (`check_manifest`, `check_unit`, `check_shards`, `check_output_dir`) checks a corpus, the one the build has just written included. `refresh-assets` copies the static app files over an existing corpus and restamps only that fingerprint component, without rebuilding any unit. A build into engine/out/review/ refuses while a review server that may serve this checkout listens (`refuse_the_served_corpus`); the artifact cycle builds beside it, at engine/out/review.next. Every form of the command refuses while the alphabet switch (`spec_load.RUNES_ENV`) is set outside the fault kit (`spec_load.small_alphabet_refusal`).

Usage:
    uv run python -m engine.review.build [--snapshot <dir>] [--new engine/out/build] [--out <dir>]
    uv run python -m engine.review.build refresh-assets
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import shutil
import socket
import subprocess
import sys
import urllib.request
import warnings
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import BinaryIO

from engine.pipeline import fingerprint, output_sink, spec_load, table
from engine.pipeline.alphabet import ALPHABET
from engine.review import app_index, diff_units, shards, snapshot, sweep_record, tablediff, unit_ids
from engine.review.audit import (
    BATCH_SIZE,
    MACHINE_CHECKS,
    SECTIONS,
    class_section,
    family_ranks,
    machine_approved,
    needs_no_verdict,
    parse_codepoints,
    triage_key,
)
from engine.review.enrich import LETTERS, load_spec
from engine.review.ink import PICTURE_VERIFICATION_METHOD, VERIFICATION_METHOD
from engine.tools import console

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "engine" / "out" / "review"
STATIC_DIR = Path(__file__).resolve().parent / "static"

MANIFEST_FORMAT = "ams-review-manifest/2"
MODE = "table-diff"
SHARD_PART_BYTES = shards.SHARD_PART_BYTES
BUILD_COMMAND = "uv run python -m engine.review.build"
SERVE_COMMAND = "uv run python -m engine.review.serve"
REVIEW_SERVER_PORT = 7294

BUILD_OUT = REPO_ROOT / "engine" / "out" / "build"

_FALLBACK_INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AMS review corpus (placeholder)</title>
</head>
<body>
<main>
<h1>AMS review corpus</h1>
<p>This is the generator's placeholder page: the static app sources were not present under <code>engine/review/static/</code> when this directory was built. The data payload is complete — <a href="manifest.json">manifest.json</a> plus the unit shards under <code>units/</code>, and both fonts under <code>fonts/</code>.</p>
<p>Rebuild with <code>{build}</code>; serve with <code>{serve}</code>.</p>
</main>
</body>
</html>
"""


def _sha256(path: Path) -> str:
    return fingerprint.file_sha256(Path(path))


def _alphabet_meta() -> dict:
    """Return the alphabet count shown on the corpus chip. `migrated` counts the letters the build's alphabet (`alphabet.ALPHABET`) holds without its boundary tokens, which is the set of letters that have runes under glyph_data/runes/. `total` counts the whole Quikscript alphabet."""
    return {"migrated": len(ALPHABET & set(LETTERS)), "total": len(LETTERS)}


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


def _generated_at(*inputs: Path) -> str:
    """Return the manifest's `generated_at` stamp: the latest input mtime as UTC ISO, floored at UNIT_ASSEMBLY_EPOCH. It is the same for consecutive builds of the same inputs (the §6 determinism gate) and changes whenever an input changes. The stamp is the corpus's identity: the app and the server take an ams-review-verdicts/1 document stamped for this corpus as already carried onto it, the land carries the store onto a corpus by unit id only when the stamp moves, and a pass tells the open tabs to move only when the served stamp moves. Bump the epoch whenever a build-code change re-keys units without changing any input: without the floor the stamp would not move, the land would not carry the store, and the store and an open tab's saves still on the old ids would be taken as carried onto the re-keyed corpus, leaving their verdicts on ids the corpus no longer has."""
    latest = max(path.stat().st_mtime for path in inputs if path.exists())
    stamp = (
        datetime.datetime.fromtimestamp(latest, tz=datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return max(stamp, UNIT_ASSEMBLY_EPOCH)


FEATURE_DESCRIPTIONS = {
    "ss03": "let letters join to a full-size ·Tea at the x-height",
    "ss04": "allow ·It to join at the baseline on both sides",
    "ss05": "allow ·Tea to join at the baseline on both sides",
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
    """Return a resolved constraint mapping as the badge's ordered clauses, on before off and each group in tag order, each clause carrying the text the app prints for it. The `text` fields are the only place this wording is defined: config_note joins them, and the app renders them as given."""
    ordered = [
        (tag, "on" if on else "off")
        for wanted in (True, False)
        for tag, on in sorted(constraints.items())
        if on == wanted
    ]
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
    if covered >= set(full_configs):
        return None, None
    universe = list(full_configs)
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
    """Return the unit's config badge as (gate, note). The gate is the smallest conjunction of feature on/off constraints that selects exactly the configs the unit's change shows under, one clause per constraint; the app draws each clause as a chip in that feature's color. The note is the clauses joined, kept as a string for hover text.

    Both are None when the unit covers every config of `full_configs`, which is the common case. When no conjunction of at most GATE_CONSTRAINT_CAP constraints selects exactly the set, the gate is None and the note is the literal "only under: <set>", as for a glyph only the ss10 overlay reaches, which is outside the roster.
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
    """Copy `engine/review/static/` over an already-built corpus (`copy_static`, which leaves a file whose bytes come out the same in place) and restamp only the manifest's `static` fingerprint component. An app JS, CSS or HTML edit changes only that component, and no unit depends on it. Everything else stays as the build left it: `generated_at` and `repo_head`, every shard, and both app sidecars. The autosave is keyed on `generated_at`, so a verdict session in progress is unaffected, and the sidecars stay current because `shards.manifest_sha256` leaves this component out of the manifest's identity. `_check_output_files` runs afterwards to confirm that. If it finds errors, the original manifest is restored, because a restamped manifest over sidecars that do not match it would look fresh to the next cycle, which would then skip the build that could repair it."""
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
    copied = copy_static(out_dir, repo_root / "engine" / "review" / "static")
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

    The build calls it only for manifest.json, a dict that goes through `json.dumps` whole. Shards are written by `_write_shard`, which frames each fragment as the list path here frames an element, and the tests write shards through this function. Each list element is serialized inside a one-element list and the list's framing is stripped, so the C encoder writes the depth-1 indent (it handles `indent` for a one-shot dump on Python 3.14) and the bytes equal `json.dumps(payload, indent=1, ensure_ascii=True) + "\\n"`. Serializing a large list whole that way holds two full-size copies of the string at once, because the concatenation cannot resize the serialized string in place. `JSONEncoder.iterencode` would be simpler, but it uses the pure-Python encoder when it is not one-shot, which makes the write several times slower; stripping the framing adds under a third, a couple of seconds across the whole units directory.

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


def _fragment_bytes(fragment: dict) -> bytes:
    """Return a fragment's bytes as a shard part holds them (`_write_shard`): framed as `_write_json` frames a list element, without the comma before it."""
    return json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2].encode("ascii")


def _write_shard(
    out_dir: Path, class_id: str, fragments: list[dict]
) -> tuple[list[str], list[tuple[int, int, int]]]:
    """Write one class's fragments, held as a list, as its parts, and return the relative part paths in part order and, aligned with `fragments`, each fragment's (part index, byte start, byte length). The fragments go into the parts in the order passed, which is id order, cut by `shards.PartLayout` at `SHARD_PART_BYTES` and named by `shards.base_part_names`: the bare `units/<class-id>.json` for a class that fits one part, and `units/<class-id>.000.json` onward for a larger one. The cap is for the browser, whose range reads never cross a part (`shards.SHARD_PART_BYTES` says why), and every part stays within it except a part holding a single fragment that alone exceeds it. Each fragment is framed as `_fragment_bytes` frames it, so a part equals `json.dumps(part, indent=1, ensure_ascii=True) + "\\n"`.

    The returned spans are byte addresses on disk, which the review app's explain panel Range-fetches through the app sidecars. No punctuation falls inside a fragment's own bytes, and `ensure_ascii=True` makes the character count equal the byte offset, so `bytes[start:start + length]` is a standalone JSON value. Changing the `indent`, `ensure_ascii` or `separators` of the dump in `_fragment_bytes` breaks this without any error; `engine/test_app_index.py` slices every fragment back out to catch it.

    Every part is staged under a `.partial` name and renamed into place once the last one is written, so a failed encode leaves the previous parts in place, and the staging files are deleted.
    """
    out_dir = Path(out_dir)
    units_dir = out_dir / "units"
    units_dir.mkdir(parents=True, exist_ok=True)
    layout = shards.PartLayout(SHARD_PART_BYTES)
    staged: list[Path] = []
    spans: list[tuple[int, int, int]] = []
    handle: BinaryIO | None = None
    try:
        for fragment in fragments:
            body = _fragment_bytes(fragment)
            index, start, first = layout.place(len(body))
            if first:
                if handle is not None:
                    handle.write(shards.PART_CLOSING)
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
            handle.write(shards.PART_CLOSING)
            handle.close()
            handle = None
        if not staged:
            staged.append(units_dir / f"{class_id}.000.json.partial")
            staged[-1].write_bytes(b"[]\n")
        names = shards.base_part_names(class_id, len(layout.sizes()))
        for staging, name in zip(staged, names, strict=True):
            staging.replace(out_dir / name)
        return names, spans
    finally:
        if handle is not None:
            handle.close()
        for staging in staged:
            staging.unlink(missing_ok=True)


def _prune_orphan_shards(out_dir: Path, manifest: dict) -> list[str]:
    """Delete the units/*.json files the manifest no longer references: shards of classes that are gone and parts that are no longer used. It runs only after the manifest is written, so a crash mid-build leaves orphan files instead of a manifest that names a deleted shard. It only considers *.json files directly under units/."""
    units_dir = Path(out_dir) / "units"
    if not units_dir.is_dir():
        return []
    keep = {Path(part).name for meta in manifest["classes"] for part in shards.class_shards(meta)}
    removed: list[str] = []
    for shard in units_dir.glob("*.json"):
        if shard.is_file() and shard.name not in keep:
            shard.unlink()
            removed.append(shard.name)
    return sorted(removed)


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        return str(path)


def _new_certificates(
    new_dir: Path, config: str, spec, inputs: Sequence[str]
) -> list[tuple[str, tuple[int, ...] | None]]:
    """The new build's certificates under `config`, one (input label, text) pair per rule, read from its windows head (`table.read_windows_head`, the one reader of a table's head). A head that does not read, or whose rules are not the settlement table's, refuses the build, since the two would describe different builds."""
    try:
        _stamp, decision = table.read_windows_head(new_dir, config)
    except (OSError, ValueError) as error:
        raise SystemExit(
            f"{new_dir}'s {config} windows head does not read ({error}); run `uv run python -m engine.pipeline.run_build`"
        ) from None
    if [rule.input_glyph for rule in decision.rules] != list(inputs):
        raise SystemExit(
            f"{new_dir}'s {config} settlement table and windows head hold different rules; run `uv run python -m engine.pipeline.run_build`"
        )
    try:
        certificates = snapshot.certificates_of(spec, decision)
    except snapshot.SnapshotError as error:
        raise SystemExit(str(error)) from None
    return [(rule.input_glyph, certificate.text) for rule, certificate in zip(decision.rules, certificates)]


def _diff_inputs(
    accepted: snapshot.Snapshot | None, new_dir: Path, after_font: Path, sweep: Path | None = None
) -> list[Path]:
    """The files a table-diff build reads, whose latest modification time is its `generated_at`: the snapshot's record, the new build's tables and windows heads, its font, its kern table and its kerning inventory, and `sweep`, the accept sweep's record when one with finds applies, whose texts add units."""
    paths = [after_font, new_dir / snapshot.KERN_SOURCE, new_dir / "kern-inventory.json"]
    if sweep is not None:
        paths.append(sweep)
    if accepted is not None:
        paths.append(accepted.directory / snapshot.RECORD_NAME)
    for config in tablediff.SETTLED_CONFIGS:
        paths += [
            new_dir / f"settlement-{config}.tsv",
            new_dir / f"joins-{config}.tsv",
            table.windows_head_path(new_dir, config),
        ]
    return paths


@dataclass
class FontChanges:
    """What a table-diff build found outside the tables: the glyphs both fonts have that draw differently, by what changed in them (`diff_units.glyph_changes`), and the kern pairs whose value moved, by bucket (`diff_units.kern_changes`)."""

    glyphs: dict[str, int] = field(default_factory=dict)
    kern: dict[str, int] = field(default_factory=dict)

    def record(self) -> dict:
        return {"glyphs": dict(sorted(self.glyphs.items())), "kern": dict(sorted(self.kern.items()))}


@dataclass(frozen=True)
class ConfigDiff:
    """One settled configuration's row diff between a snapshot and a build (`config_diffs`): its changed settlement and join rows, and the certificates of both tables' rules, each an (input label, certificate text) pair per rule in rule order, the build's only when a settlement row changed. `new_outcomes` pairs each of the build's rules' outcome with its certificate's text, for the glyph bucket, and is empty where `new_certificates` is."""

    config: str
    settlement: list[tablediff.SettlementDiffEntry]
    joins: list[tablediff.JoinDiffEntry]
    old_certificates: list[tuple[str, tuple[int, ...] | None]]
    new_certificates: list[tuple[str, tuple[int, ...] | None]]
    new_outcomes: list[tuple[str, tuple[int, ...] | None]]


def load_quiet_spec(repo_root: Path = REPO_ROOT):
    """The spec under `repo_root`, loaded with its warnings suppressed, as the build loads it (`doc/review-design.md` §1.3)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_spec(repo_root)


def config_diffs(accepted: snapshot.Snapshot, new_dir: Path, spec_of) -> Iterator[ConfigDiff]:
    """Diff every settled configuration's tables in `accepted` against `new_dir`'s and yield each configuration whose rows changed (`ConfigDiff`). A configuration whose two tables hold the same bytes on both sides is passed over unparsed. `spec_of()` gives the spec, which spells the new build's certificates out, and is called only for a configuration whose rows changed. The corpus build (`table_diff_units`) and the accept's sweep (`engine/review/accept_sweep.py`) read the same diffs."""
    for config in tablediff.SETTLED_CONFIGS:
        new_settlement = new_dir / f"settlement-{config}.tsv"
        new_joins = new_dir / f"joins-{config}.tsv"
        held = config in accepted.configs
        if not held and not new_settlement.is_file():
            continue
        if (
            held
            and new_settlement.is_file()
            and new_joins.is_file()
            and tablediff.same_table(accepted.settlement_path(config), new_settlement)
            and tablediff.same_table(accepted.joins_path(config), new_joins)
        ):
            continue
        old_rows = accepted.settlement(config)
        new_rows = tablediff.load_settlement(new_settlement, config) if new_settlement.is_file() else {}
        settlement_entries = tablediff.diff_settlement(old_rows, new_rows, config)
        join_entries = tablediff.diff_joins(
            accepted.joins(config),
            tablediff.load_joins(new_joins, config) if new_joins.is_file() else {},
            config,
        )
        if not settlement_entries and not join_entries:
            continue
        spec = spec_of()
        old_certificates = accepted.certificates(config)
        if len(old_certificates) != len(old_rows):
            raise SystemExit(
                f"{accepted.directory} holds {len(old_certificates)} {config} certificates for {len(old_rows)} rules; take the snapshot again"
            )
        new_certificates = (
            _new_certificates(new_dir, config, spec, [key.input for key in new_rows])
            if settlement_entries
            else []
        )
        new_outcomes = [
            (value.outcome, text) for value, (_label, text) in zip(new_rows.values(), new_certificates)
        ]
        old_pairs = [(key.input, certificate.text) for key, certificate in zip(old_rows, old_certificates)]
        del old_rows, new_rows, old_certificates
        yield ConfigDiff(config, settlement_entries, join_entries, old_pairs, new_certificates, new_outcomes)
        del settlement_entries, join_entries, old_pairs, new_certificates, new_outcomes


def table_diff_units(
    accepted: snapshot.Snapshot,
    new_dir: Path,
    fonts: diff_units.Fonts,
    spec=None,
    repo_root: Path = REPO_ROOT,
    font_changes: FontChanges | None = None,
    *,
    context: int = diff_units.SWEEP_CONTEXT,
    extra_texts: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[diff_units.Unit], dict[str, diff_units.ConfigCounts]]:
    """Diff every settled configuration's tables in `accepted` against `new_dir`'s (`config_diffs`) and find the units that show the changes (`diff_units.UnitFinder`, with `context` letters of context around a changed letter and each configuration's `extra_texts` beside the certificates), then the glyph bucket, the glyphs the two fonts draw differently (`diff_units.glyph_units`), and the kern bucket, the facing pairs whose value moved between the two `kern.tsv` (`diff_units.kern_units`), and merge them all (`diff_units.merge`). The spec is loaded from `repo_root` only when some configuration's rows changed. A glyph unit's certificates are the snapshot's in every configuration, and the new build's in each configuration whose rows changed; in one whose tables did not move they are the snapshot's. `font_changes`, when given, is filled with the counts of the two buckets."""
    finder: diff_units.UnitFinder | None = None
    new_outcomes: dict[str, list[tuple[str, tuple[int, ...] | None]]] = {}
    inventory = diff_units.load_inventory(new_dir / "kern-inventory.json")
    loaded = [spec]

    def spec_of():
        if loaded[0] is None:
            loaded[0] = load_quiet_spec(repo_root)
        return loaded[0]

    for diff in config_diffs(accepted, new_dir, spec_of):
        if finder is None:
            finder = diff_units.UnitFinder(
                spec_of(),
                fonts,
                tablediff.SETTLED_CONFIGS,
                diff_units.inventory_texts(inventory),
                context,
            )
        if diff.new_outcomes:
            new_outcomes[diff.config] = diff.new_outcomes
        finder.add_config(
            diff.config,
            diff.settlement,
            diff.joins,
            diff.old_certificates,
            diff.new_certificates,
            (extra_texts or {}).get(diff.config, ()),
        )
        del diff

    changed_glyphs = diff_units.glyph_changes(accepted.font, fonts.paths[1])
    extra: list[diff_units.Unit] = []
    if changed_glyphs:
        certificates = diff_units.certificate_witnesses(
            {
                config: list(zip(accepted.outcomes(config), (c.text for c in accepted.certificates(config))))
                for config in accepted.configs
            },
            "before",
        ) + diff_units.certificate_witnesses(new_outcomes, "after")
        extra += diff_units.glyph_units(
            changed_glyphs,
            fonts,
            tablediff.SETTLED_CONFIGS,
            certificates,
            diff_units.inventory_texts(inventory),
        )
    new_kern = new_dir / snapshot.KERN_SOURCE
    new_kern_rows = diff_units.load_kern(new_kern) if new_kern.is_file() else []
    changed_kern = diff_units.kern_changes(diff_units.load_kern(accepted.kern), new_kern_rows)
    extra += diff_units.kern_units(changed_kern, tablediff.SETTLED_CONFIGS, inventory, new_kern_rows)
    if font_changes is not None:
        for change in changed_glyphs:
            for kind in change.differs:
                font_changes.glyphs[kind] = font_changes.glyphs.get(kind, 0) + 1
        for change, _row, _index in changed_kern:
            font_changes.kern[change.bucket] = font_changes.kern.get(change.bucket, 0) + 1
    if finder is None:
        return diff_units.merge(extra), {}
    return finder.units(extra), finder.counts


def _unit_rows(unit: diff_units.Unit) -> set[tuple[str, str, str]]:
    """The rows a unit stands for, for its class's `row_count`: each changed table row by configuration, each redrawn glyph and each kern pair."""
    rows = {
        (config, entry.table, entry.key.label())
        for config, entries in unit.rows.items()
        for entry in entries
        for _member in (entry.paired or (entry,))
    }
    for part in unit.parts:
        if isinstance(part.subject, diff_units.GlyphChange):
            rows.add(("font", "glyph", part.subject.name))
        elif isinstance(part.subject, diff_units.KernChange):
            rows.add(("font", "kern", f"{part.subject.leading_side} + {part.subject.following_side}"))
    return rows


def build_table_diff(
    out_dir: Path,
    snapshot_dir: Path | None,
    new_dir: Path = BUILD_OUT,
    *,
    after_font: Path | None = None,
    spec=None,
    repo_root: Path = REPO_ROOT,
    batch_size: int = BATCH_SIZE,
    static_dir: Path = STATIC_DIR,
    sweep_path: Path | None = None,
    context: int = diff_units.SWEEP_CONTEXT,
    accept_context: int = diff_units.ACCEPT_SWEEP_CONTEXT,
) -> dict:
    """Build the table-diff corpus into `out_dir`: the accepted build in `snapshot_dir` (a snapshot, `engine/review/snapshot.py`) against the build in `new_dir`, its tables, windows heads, `Senior-Regular.otf` (or `after_font`), `kern.tsv` and kerning inventory, and return the manifest. Each unit is one visible change (`diff_units`), in one of `diff_units.CLASSES`, shaped in the snapshot's font as before and the new build's as after, found with `context` letters of context around each changed letter. `sweep_path` is where the accept's sweep writes its record (`engine/review/sweep_record.py`; the command passes the checkout's, and None reads none): when the record there is the one for these inputs, swept at `accept_context` beside this `context`, each configuration's texts it found are candidates beside the certificates, and the units they show are units like any other. An empty diff gives a corpus with no units, and so does a `snapshot_dir` of None, the build of a machine with no snapshot, whose manifest's `baseline` says no snapshot is present and whose before font is the after font, so the app shell and the kerning view still boot. The manifest's `configs` is every settled configuration, the universe every unit's config badge is resolved against, `table_rows` counts each configuration's changed rows and `font_changes` the redrawn glyphs and moved kern pairs (`FontChanges`), `baseline.commit` is the commit the snapshot stands for (`snapshot.pointer_commit`), and `inputs_fingerprint` is `fingerprint.table_diff_values` over the snapshot's record, the tables' stamp of `new_dir` (`snapshot.tables_stamp`) and the sweep record that applies (`sweep_record.component`). The written corpus is checked with `check_shards` before the build returns."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    new_dir = Path(new_dir)
    after_font = Path(after_font) if after_font is not None else new_dir / "Senior-Regular.otf"
    universe = list(tablediff.SETTLED_CONFIGS)
    accepted: snapshot.Snapshot | None = None
    if snapshot_dir is not None:
        try:
            accepted = snapshot.load(Path(snapshot_dir))
        except snapshot.SnapshotError as error:
            raise SystemExit(str(error)) from None
    before_font = accepted.font if accepted is not None else after_font
    font_digests = {"before": _sha256(before_font), "after": _sha256(after_font)}
    tables_stamp = snapshot.tables_stamp(new_dir)
    swept = sweep_record.applicable(
        sweep_path,
        sweep_record.key_for(
            accepted.digest if accepted is not None else None,
            tables_stamp,
            font_digests["after"],
            context,
            accept_context,
        ),
    )
    fonts = diff_units.Fonts(before_font, after_font)
    font_changes = FontChanges()
    units, counts = (
        table_diff_units(
            accepted,
            new_dir,
            fonts,
            spec,
            repo_root,
            font_changes,
            context=context,
            extra_texts=sweep_record.texts(swept),
        )
        if accepted is not None
        else ([], {})
    )

    by_class: dict[str, list[dict]] = {cls.id: [] for cls in diff_units.CLASSES}
    rows_by_class: dict[str, set[tuple[str, str, str]]] = {cls.id: set() for cls in diff_units.CLASSES}
    ids_seen: set[str] = set()
    for unit in units:
        fragment = diff_units.fragment(unit, fonts, universe, config_badge)
        if fragment["id"] in ids_seen:
            raise SystemExit(f"two table-diff units share the id {fragment['id']}: {fragment['row_key']}")
        ids_seen.add(fragment["id"])
        by_class[fragment["class"]].append(fragment)
        rows_by_class[fragment["class"]].update(_unit_rows(unit))

    present = [cls for cls in diff_units.CLASSES if by_class[cls.id]]
    sections = {
        cls.id: class_section(
            cls.no_verdict, sum(1 for fragment in by_class[cls.id] if not needs_no_verdict(fragment))
        )
        for cls in present
    }
    present.sort(key=lambda cls: SECTIONS.index(sections[cls.id]))
    rank = family_ranks(dict(LETTERS))
    human = sorted(
        (
            triage_key(
                index,
                fragment["group"],
                parse_codepoints(fragment["codepoints"]) if fragment["codepoints"] else (),
                fragment["id"],
                rank,
            ),
            fragment["id"],
        )
        for index, cls in enumerate(present)
        for fragment in by_class[cls.id]
        if not needs_no_verdict(fragment)
    )
    human_unit_ids = [unit_id for _key, unit_id in human]
    position = {unit_id: index for index, unit_id in enumerate(human_unit_ids)}

    classes_meta: list[dict] = []
    shards_by_class: dict[str, list[dict]] = {}
    spans_by_class: dict[str, list[tuple[int, int, int]]] = {}
    machine_by_class: dict[str, int] = {}
    check_totals = {check_id: 0 for check_id in MACHINE_CHECKS}
    for cls in present:
        shard = sorted(by_class[cls.id], key=lambda fragment: fragment["id"])
        check_counts = {
            check_id: sum(1 for fragment in shard if fragment.get(check_id) is True)
            for check_id in MACHINE_CHECKS
        }
        machine_count = sum(1 for fragment in shard if machine_approved(fragment))
        parts, spans_by_class[cls.id] = _write_shard(out_dir, cls.id, shard)
        shards_by_class[cls.id] = shard
        if machine_count:
            machine_by_class[cls.id] = machine_count
        for check_id, count in check_counts.items():
            check_totals[check_id] += count
        classes_meta.append(
            {
                "id": cls.id,
                "title": cls.title,
                "summary": cls.summary,
                "section": sections[cls.id],
                "no_verdict": cls.no_verdict,
                "why": cls.why,
                "unit_count": len(shard),
                "row_count": len(rows_by_class[cls.id]),
                "machine_approved_count": machine_count,
                "machine_checks": check_counts,
                "shards": parts,
                "batches": sorted(
                    {
                        position[fragment["id"]] // batch_size
                        for fragment in shard
                        if fragment["id"] in position
                    }
                ),
            }
        )

    fonts_meta = {
        "before": _copy_font(
            before_font, out_dir, "before.otf", "AMS Review Before", repo_root, font_digests["before"]
        ),
        "after": _copy_font(
            after_font, out_dir, "after.otf", "AMS Review After", repo_root, font_digests["after"]
        ),
    }
    machine_units = sum(machine_by_class.values())
    methods = {"ink_identical": VERIFICATION_METHOD, "picture_identical": PICTURE_VERIFICATION_METHOD}
    manifest = {
        "format": MANIFEST_FORMAT,
        "mode": MODE,
        "generated_at": _generated_at(
            *_diff_inputs(
                accepted, new_dir, after_font, sweep_path if sweep_record.has_finds(swept) else None
            )
        ),
        "repo_head": _repo_head(repo_root),
        "inputs_fingerprint": fingerprint.table_diff_values(
            repo_root,
            accepted.directory / snapshot.RECORD_NAME if accepted is not None else None,
            tables_stamp,
            accept_sweep=sweep_record.component(swept),
        ),
        "source": {
            "snapshot": None if accepted is None else _relative(accepted.directory, repo_root),
            "new": _relative(new_dir, repo_root),
        },
        "baseline": (
            accepted.baseline(snapshot.pointer_commit(accepted.digest, repo_root))
            if accepted is not None
            else dict(snapshot.ABSENT_BASELINE)
        ),
        "fonts": fonts_meta,
        "alphabet": _alphabet_meta(),
        "configs": universe,
        "feature_descriptions": dict(FEATURE_DESCRIPTIONS),
        "batch_size": batch_size,
        "human_unit_ids": human_unit_ids,
        "totals": {
            "units": len(units),
            "rows": sum(meta["row_count"] for meta in classes_meta),
            "batches": (len(human_unit_ids) + batch_size - 1) // batch_size,
        },
        "machine_approved": {
            "units": machine_units,
            "rows": sum(len(rows_by_class[cls_id]) for cls_id in machine_by_class),
            "method": VERIFICATION_METHOD,
            "by_class": machine_by_class,
            "checks": {
                check_id: {
                    "units": check_totals[check_id],
                    "rows": check_totals[check_id],
                    "method": methods[check_id],
                }
                for check_id in MACHINE_CHECKS
            },
        },
        "table_rows": {config: counts[config].record() for config in universe if config in counts},
        "font_changes": font_changes.record(),
        "classes": classes_meta,
        "build_command": BUILD_COMMAND,
        "serve_command": SERVE_COMMAND,
    }
    _write_json(out_dir / "manifest.json", manifest)
    pruned = _prune_orphan_shards(out_dir, manifest)
    if pruned:
        print(f"Pruned {len(pruned)} orphan shard(s): {', '.join(pruned)}", file=sys.stderr)
    copy_static(out_dir, static_dir)
    app_index.write_app_artifacts(out_dir, shards_by_class, spans_by_class)
    errors = check_shards(manifest, shards_by_class)
    if errors:
        raise SystemExit("contract check failed:\n" + "\n".join(errors[:CONTRACT_ERRORS_SHOWN]))
    shaped = sum(record.texts for record in counts.values())
    seconds = sum(record.seconds for record in counts.values())
    console.say(
        f"review.build table-diff: {len(units)} units against "
        + (f"the snapshot {accepted.digest[:12]}" if accepted is not None else "no snapshot")
        + f"; {shaped} texts shaped in {seconds:.1f}s"
        + (
            f", {sum(map(len, sweep_record.texts(swept).values()))} of them the accept sweep's ({sweep_path})"
            if sweep_record.has_finds(swept)
            else ""
        ),
        file=sys.stderr,
    )
    return manifest


# --- the §7 contract checker (shared between the build's self-check and the tests) ------

# The junction vocabulary a unit's `before.junctions` and `after.junctions` take: `break`, `lig`, `absent`, or `y` followed by a height.
_JUNCTION_TOKENS = ("break", "lig", "absent")


def is_junction_token(token) -> bool:
    """Return whether `token` is in the junction vocabulary: `break`, `lig`, `absent`, or `y` followed by a height. The compound tokens `JunctionClassifier.classify` emits when two heights join at once (`y0+y5`) are excluded, because a unit's junctions are single-height."""
    return isinstance(token, str) and (
        token in _JUNCTION_TOKENS or (token.startswith("y") and token[1:].isdigit())
    )


def check_manifest(manifest: dict) -> list[str]:
    """The manifest's half of the §7 contract check: its stamps, the triage index, the inputs fingerprint (`fingerprint.TABLE_DIFF_COMPONENTS`, with `snapshot` null exactly when no snapshot is present), the configurations (every settled one, the universe the badges are resolved against), the totals and the machine-approval record, the fonts, the snapshot it was built against (`baseline`, which says when none is present, in which case the corpus holds no units), the redrawn glyphs and moved kern pairs (`font_changes`), and each class's record, in section order, every class one of `diff_units.CLASS_IDS` and taking no verdict exactly when it is one of `diff_units.NO_VERDICT_CLASS_IDS`."""
    errors: list[str] = []

    def need(condition: object, message: str) -> None:
        if not condition:
            errors.append(f"manifest: {message}")

    need(manifest.get("format") == MANIFEST_FORMAT, f"format must be {MANIFEST_FORMAT}")
    need(manifest.get("mode") == MODE, f"mode must be {MODE}")
    for key in ("generated_at", "repo_head", "build_command", "serve_command"):
        need(isinstance(manifest.get(key), str) and manifest.get(key), f"{key} must be a nonempty string")
    need(isinstance(manifest.get("source"), dict), "source must be a mapping")
    human_unit_ids = manifest.get("human_unit_ids")
    need(
        isinstance(human_unit_ids, list) and all(unit_ids.is_content_id(unit) for unit in human_unit_ids),
        "human_unit_ids must be a list of u- ids",
    )
    if isinstance(human_unit_ids, list) and all(isinstance(unit, str) for unit in human_unit_ids):
        need(len(human_unit_ids) == len(set(human_unit_ids)), "human_unit_ids must be unique")
    inputs = manifest.get("inputs_fingerprint")
    need(
        isinstance(inputs, dict)
        and set(inputs) == set(fingerprint.TABLE_DIFF_COMPONENTS)
        and all(value is None or isinstance(value, str) for value in inputs.values()),
        f"inputs_fingerprint must map exactly the table-diff input components ({', '.join(fingerprint.TABLE_DIFF_COMPONENTS)}) to hashes or null",
    )
    baseline = manifest.get("baseline")
    if isinstance(inputs, dict) and isinstance(baseline, dict):
        need(
            (inputs.get("snapshot") is None) is not (baseline.get("present") is True),
            "inputs_fingerprint.snapshot must be a hash exactly when a snapshot is present",
        )
    need(
        manifest.get("configs") == list(tablediff.SETTLED_CONFIGS),
        f"configs must be every settled configuration ({', '.join(tablediff.SETTLED_CONFIGS)})",
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
        need(
            isinstance(checks, dict) and set(checks) == set(MACHINE_CHECKS),
            f"machine_approved.checks must map the machine checks ({', '.join(MACHINE_CHECKS)})",
        )
        for check_id, record in checks.items() if isinstance(checks, dict) else ():
            need(
                isinstance(record, dict)
                and all(isinstance(record.get(key), int) for key in ("units", "rows"))
                and isinstance(record.get("method"), str)
                and record.get("method"),
                f"machine_approved.checks.{check_id} must carry integer units and rows and a nonempty method",
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
    keys = ("commit", "taken_at", "digest")
    if not (
        isinstance(baseline, dict)
        and set(baseline) == {*keys, "present"}
        and isinstance(baseline.get("present"), bool)
        and all(
            baseline.get(key) is None or (isinstance(baseline.get(key), str) and baseline.get(key))
            for key in keys
        )
    ):
        need(False, "baseline must map commit, taken_at and digest to strings or null, and present to a bool")
    elif baseline["present"]:
        need(isinstance(baseline.get("digest"), str), "a present baseline must name its snapshot's digest")
    else:
        need(
            all(baseline.get(key) is None for key in keys),
            "a baseline that is not present names no commit, time or digest",
        )
        need((totals or {}).get("units") == 0, "a corpus built with no snapshot holds no units")
    changes = manifest.get("font_changes")
    need(
        isinstance(changes, dict)
        and set(changes) == {"glyphs", "kern"}
        and all(
            isinstance(counts, dict) and all(isinstance(count, int) for count in counts.values())
            for counts in changes.values()
        ),
        "font_changes must map glyphs and kern to counts",
    )
    classes = manifest.get("classes")
    need(isinstance(classes, list), "classes must be a list")
    section_ranks: list[int] = []
    for meta in classes or ():
        identifier = meta.get("id", "<missing>")
        need(
            identifier in diff_units.CLASS_IDS,
            f"classes[{identifier}] is not a table-diff class ({', '.join(diff_units.CLASS_IDS)})",
        )
        need(isinstance(meta.get("why"), str), f"classes[{identifier}].why must be a string")
        for key in ("title", "summary"):
            need(
                isinstance(meta.get(key), str) and meta[key].strip(),
                f"classes[{identifier}].{key} must be a nonempty string",
            )
        need(isinstance(meta.get("no_verdict"), bool), f"classes[{identifier}].no_verdict must be a bool")
        need(
            meta.get("no_verdict") is (identifier in diff_units.NO_VERDICT_CLASS_IDS),
            f"classes[{identifier}].no_verdict must be {identifier in diff_units.NO_VERDICT_CLASS_IDS}",
        )
        section = meta.get("section")
        need(section in SECTIONS, f"classes[{identifier}].section must be one of {', '.join(SECTIONS)}")
        units, machine_count = meta.get("unit_count"), meta.get("machine_approved_count")
        if section in SECTIONS and isinstance(units, int) and isinstance(machine_count, int):
            section_ranks.append(SECTIONS.index(section))
            need(
                section == class_section(meta.get("no_verdict") is True, units - machine_count),
                f"classes[{identifier}].section must be the one audit.class_section gives its exemption "
                f"and {units - machine_count} human units",
            )
        parts = meta.get("shards")
        need(
            isinstance(parts, list) and parts and all(isinstance(part, str) for part in parts),
            f"classes[{identifier}].shards must be a nonempty list of paths",
        )
        for key in ("unit_count", "row_count", "machine_approved_count"):
            need(isinstance(meta.get(key), int), f"classes[{identifier}].{key} must be an integer")
        checks = meta.get("machine_checks")
        need(
            isinstance(checks, dict)
            and set(checks) == set(MACHINE_CHECKS)
            and all(isinstance(count, int) for count in checks.values()),
            f"classes[{identifier}].machine_checks must count the machine checks",
        )
        need(isinstance(meta.get("batches"), list), f"classes[{identifier}].batches must be a list")
    need(section_ranks == sorted(section_ranks), f"classes must be listed by section ({', '.join(SECTIONS)})")
    return errors


# How many lines of a failing contract check the build prints.
CONTRACT_ERRORS_SHOWN = 20


def check_unit(unit: dict) -> list[str]:
    """The per-unit half of the §7 contract check, over one fragment: the identity and its stamp, the machine flags, the class and the group, the row key, the text, the junctions, the highlight, the notation, the summary and explain, the config badge, and the cluster. `check_shards` and `check_output_dir` run it over a corpus held in memory or read back from disk."""
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
    human = not needs_no_verdict(unit)
    pair = unit.get("pair")

    need(unit_ids.is_content_id(unit.get("id")), f"id must be u- and {unit_ids.ID_SYMBOLS} base58 symbols")
    for check_id in MACHINE_CHECKS:
        need(isinstance(unit.get(check_id), bool), f"{check_id} must be a bool")
    stamp = unit.get("content_key")
    need(
        isinstance(stamp, str) and len(stamp) == 64 and all(ch in "0123456789abcdef" for ch in stamp),
        "content_key must be a sha256 hex stamp",
    )
    # The id is the first 64 bits of the content key in base58, so a fragment whose id and stamp disagree names one change and describes another.
    if isinstance(stamp, str) and len(stamp) == 64:
        need(unit.get("id") == unit_ids.unit_id_for(stamp), "id must be the content key's own")
    need(isinstance(unit.get("no_verdict"), bool), "no_verdict must be a bool")
    # Whether a unit takes a verdict comes from its flags (`audit.needs_no_verdict`), and its place in the queue comes from the manifest's triage index, so a fragment carries no batch.
    need("batch" not in unit, "a fragment carries no batch; the manifest's human_unit_ids is the index")
    need(len(approving) <= 1, "at most one machine check may approve a unit")
    for key in ("class", "group", "notation", "summary", "row_key", "explain"):
        need(isinstance(unit.get(key), str) and unit.get(key) != "", f"{key} must be a nonempty string")
    need(
        unit.get("class") in diff_units.CLASS_IDS,
        f"class must be a table-diff class ({', '.join(diff_units.CLASS_IDS)})",
    )
    need(
        unit.get("no_verdict") is (unit.get("class") in diff_units.NO_VERDICT_CLASS_IDS),
        "no_verdict must be true exactly on a unit of a class that takes no verdict",
    )
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
    kinds = unit.get("kinds")
    need(
        isinstance(kinds, list) and kinds and all(kind in diff_units.KINDS for kind in kinds),
        f"kinds must be a nonempty list of the row buckets, {diff_units.CONTEXT_KIND} and the glyph kinds ({', '.join(diff_units.KINDS)})",
    )
    need(isinstance(unit.get("provenance"), list), "provenance must be a list")
    need(isinstance(unit.get("boundary_marks"), list), "boundary_marks must be a list")
    for mark in unit.get("boundary_marks") or ():
        need(
            isinstance(mark, dict) and {"index", "kind", "x"} <= set(mark),
            "boundary marks must carry index/kind/x",
        )

    codepoints = unit.get("codepoints")
    if codepoints is not None:
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
    else:
        need(
            all(
                unit.get(key) is None
                for key in ("text_entities", "notation_tokens", "pair", "pair_codepoints", "highlight")
            ),
            "a unit with no text carries no text_entities, notation_tokens, pair, pair_codepoints or highlight",
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
    for side, record in (("before", before), ("after", after)):
        if isinstance(record, dict) and isinstance(record.get("junctions"), list):
            need(
                all(is_junction_token(junction) for junction in record["junctions"]),
                f"{side}.junctions must be break/lig/absent/yN tokens",
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

    highlight = unit.get("highlight")
    if highlight is not None:
        for side in ("before", "after"):
            need_rect(highlight.get(side) if isinstance(highlight, dict) else None, f"highlight.{side}")
    if pair is not None:
        need(
            isinstance(span, list) and isinstance(highlight, dict),
            "a unit with a pair carries its pair_codepoints and highlight",
        )

    need("cluster" in unit, "cluster must be present")
    cluster = unit.get("cluster")
    need(
        cluster is None or (isinstance(cluster, str) and cluster.startswith("c-")),
        "cluster must be null or a c-XXXXXXXX signature id",
    )
    if human:
        need(isinstance(cluster, str), "human units must carry a cluster signature id")
    else:
        need(cluster is None, "machine-approved and no-verdict units must carry cluster null")
    kinds = unit.get("kinds")
    need(
        isinstance(kinds, list) and all(kind in diff_units.KINDS for kind in kinds),
        f"kinds must name the row buckets and {diff_units.CONTEXT_KIND} ({', '.join(diff_units.KINDS)})",
    )
    # A unit that shows several changes names each in its row key, joined by `; `, and stands in `joins` when any of them is a join row, else in `settlement` when any is a cell change (`diff_units.KIND_PRECEDENCE`); a unit in `glyphs` shows only redrawn glyphs, and one in `kerning` only kern pairs.
    changes = str(unit.get("row_key") or "").split("; ")
    if unit.get("class") in ("joins", "settlement"):
        need(
            (unit.get("class") == "joins") == any(change.startswith("join ") for change in changes),
            "a unit that shows a join row is in joins, and one that shows none is not",
        )
    for class_id, prefix in (("glyphs", "glyph "), ("kerning", "kern ")):
        if unit.get("class") == class_id:
            need(
                all(change.startswith(prefix) for change in changes),
                f"a unit in {class_id} names only {prefix.strip()} changes in its row key",
            )
    need(
        unit.get("kerning") is (unit.get("class") == "kerning"),
        "kerning must be true exactly on a unit of the kerning class, which is drawn with kerning on",
    )
    return errors


class _CorpusCheck:
    """The per-unit and cross-unit parts of the §7 contract check as an accumulator fed one fragment at a time: `class_start` with the manifest's class record, `unit` for each fragment in shard order, `class_end` after the class's last fragment, and `finish` with the manifest for the predicates that read its totals. Per unit it keeps only what the cross-unit predicates read (its id and, for a human unit, its class, group and codepoints), never the fragment. `unit` runs `check_unit` on every fragment. Per-unit errors are recorded as each fragment is fed in, and cross-unit errors at `finish`."""

    def __init__(self, *, descriptions: Mapping, batch_size: object) -> None:
        self._descriptions = descriptions
        self._batch_size = batch_size
        self.errors: list[str] = []
        self._seen_units = 0
        self._seen_rows = 0
        self._seen_ids: set[str | None] = set()
        self._seen_machine_by_class: dict[str, int] = {}
        self._human_units: dict[str, tuple[str, str, str | None]] = {}
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
        self._class_units += 1
        unit_id = unit.get("id")
        errors.extend(check_unit(unit))
        for clause in unit.get("config_gate") or ():
            if isinstance(clause, dict) and not self._descriptions.get(clause.get("feature")):
                errors.append(
                    f"unit {unit_id}: config_gate names {clause.get('feature')!r}, which the manifest's "
                    "feature_descriptions does not gloss"
                )
        if unit.get("class") != meta.get("id"):
            errors.append(f"unit {unit_id}: class {unit.get('class')} in shard {meta.get('id')}")
        if unit_id in self._seen_ids:
            errors.append(f"duplicate unit id {unit_id}")
        self._seen_ids.add(unit_id)
        if not needs_no_verdict(unit) and isinstance(unit_id, str):
            self._human_units[unit_id] = (
                str(unit.get("class")),
                str(unit.get("group") or ""),
                unit.get("codepoints"),
            )
        if unit.get("no_verdict") != bool(meta.get("no_verdict")):
            errors.append(
                f"unit {unit_id}: no_verdict {unit.get('no_verdict')} in a class "
                f"whose no_verdict is {meta.get('no_verdict')}"
            )
        if machine_approved(unit):
            self._machine_count += 1
            for check_id in MACHINE_CHECKS:
                if unit.get(check_id) is True:
                    self._check_counts[check_id] += 1

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
        if self._seen_machine_by_class != dict(machine.get("by_class") or {}):
            errors.append("machine_approved.by_class does not match the shards' machine-approved counts")
        # The triage index is the manifest's, and every batch is a slice of it, so both are checked against the index. Every term of `audit.triage_key` is a fragment field or a manifest fact, so the index must hold the human units in that order, every class's `batches` must be the slices its units occupy, and `totals.batches` the number of slices.
        if index_valid and set(human_unit_ids) == set(self._human_units):
            class_index: dict[str, int] = {
                str(meta.get("id")): index for index, meta in enumerate(manifest.get("classes") or ())
            }
            rank = family_ranks(dict(LETTERS))

            def key(unit_id: str) -> tuple:
                class_id, group, codepoints = self._human_units[unit_id]
                return triage_key(
                    class_index.get(class_id, len(class_index)),
                    group,
                    parse_codepoints(codepoints) if codepoints else (),
                    unit_id,
                    rank,
                )

            if human_unit_ids != sorted(self._human_units, key=key):
                errors.append("human_unit_ids is not the triage-ordered sequence of the shards' human units")
        batch_size = self._batch_size
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
        return errors


def check_shards(manifest: dict, shards_by_class: dict[str, list[dict]]) -> list[str]:
    """Run the per-unit and cross-unit §7 checks over shard payloads held in memory, keyed by class id. The build passes the dicts it serialized and `check_output_dir` passes the shards it re-parsed from disk. A class missing from the mapping is skipped here and reported by the caller.

    The cross-unit predicates check what no single fragment can: every unit id is unique, each class's counts and machine checks are the manifest's, the manifest's `human_unit_ids` lists the human units in `audit.triage_key` order, and every class's `batches` are the slices its units occupy in it.
    """
    check = _CorpusCheck(
        descriptions=manifest.get("feature_descriptions") or {},
        batch_size=manifest.get("batch_size"),
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
    """Check the files beside the manifest: every shard part the manifest lists is present and non-empty, a reader of part names that never opens one, both app sidecars are present and stamped for this manifest (`app_index.artifact_is_current`), index.html exists, and each copied font matches the sha256 the manifest records and, when `repo_root` resolves its `source`, the font it was copied from, byte for byte, since the kern units show the `kern` lookup. A build already guarantees the source comparison (`_copy_font` checks the copy against the digest taken at load), and the cycle's corpus skip and `make verdict-ready` compare the manifest's after-font sha256 with `engine/out/build/Senior-Regular.otf`; the comparison here is for `check_output_dir` and `refresh_assets`."""
    errors: list[str] = []
    for meta in manifest.get("classes", ()):
        for part in shards.class_shards(meta):
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
    for name, fmt in app_index.ARTIFACTS:
        if not app_index.artifact_path(out_dir, name).is_file():
            errors.append(f"{name} is missing")
        elif not app_index.artifact_is_current(out_dir, name, fmt):
            errors.append(f"{name} is unreadable or stamped for another manifest")
    return errors


def check_output_dir(out_dir: Path, repo_root: Path | None = None) -> list[str]:
    """Run the whole §7 contract check over the corpus written at `out_dir`: the manifest (`check_manifest`), every fragment read back from its parts (`check_shards`), and the files beside the manifest (`_check_output_files`)."""
    out_dir = Path(out_dir)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"{manifest_path} is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = check_manifest(manifest)
    shards_by_class: dict[str, list[dict]] = {}
    for meta in manifest.get("classes", ()):
        class_id = meta.get("id", "")
        if not all((out_dir / part).exists() for part in shards.class_shards(meta)):
            continue
        try:
            shards_by_class[class_id] = list(shards.iter_class_fragments(out_dir, meta))
        except (OSError, ValueError) as error:
            errors.append(f"shard of {class_id} does not read: {error}")
    errors.extend(check_shards(manifest, shards_by_class))
    errors.extend(_check_output_files(out_dir, manifest, repo_root))
    return errors


# --- CLI ------------------------------------------------------------------------


def review_server_listening(port: int = REVIEW_SERVER_PORT) -> bool:
    """Return whether something accepts connections on the review server's port (`engine.review.serve.PORT`). It is a bare socket check, not `engine.tools.review_server`, so the build's import closure stays inside the modules `engine/test_review_code_closure.py` allows."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def review_server_root(port: int = REVIEW_SERVER_PORT) -> str | None:
    """Return the repo root the review server on `port` names in its /capabilities answer (`engine.review.serve.capabilities_payload`), or None when it names none: nothing answers, the server predates /capabilities, or the answer carries no root. A bare urllib call, for the same reason as `review_server_listening`."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/capabilities", timeout=2.0) as response:
            answer = json.loads(response.read())
    except OSError, ValueError, http.client.HTTPException:
        return None
    root = answer.get("root") if isinstance(answer, dict) else None
    return root if isinstance(root, str) else None


def refuse_the_served_corpus(out: Path) -> None:
    """Refuse a build into the corpus the review server serves while the server listens. A build rewrites the shards the open tabs read by byte range and restamps the manifest their saves are keyed on; the artifact cycle builds beside the served corpus and lands the result with the verdict store (`engine.review.landing`), which keeps the tabs working. A build into any other directory, a build while the listening server names another checkout as its root (`review_server_root`), such as a worktree's first pass beside the main checkout's server, and `refresh-assets`, which renames each file into place, are allowed; a server that names no root may serve this checkout, so it blocks the build. So is a build into a served corpus with no manifest (a first run, after engine/out/review was deleted under a running server), which no tab can have loaded, and which the artifact cycle builds in place because it has nothing to land beside."""
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
            prog="engine.review.build refresh-assets", description=refresh_assets.__doc__
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

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="the snapshot of the accepted build to compare against (`engine/review/snapshot.py`); the default is the current one under var/keep/review-snapshot/, and with none there the corpus has no units",
    )
    parser.add_argument(
        "--new",
        dest="new_dir",
        type=Path,
        default=BUILD_OUT,
        help="the build to compare, whose tables, windows heads and Senior-Regular.otf it reads",
    )
    args = parser.parse_args(argv)
    refuse_the_served_corpus(args.out)
    manifest = build_table_diff(
        args.out,
        args.snapshot if args.snapshot is not None else snapshot.current(),
        args.new_dir,
        batch_size=args.batch_size,
        sweep_path=REPO_ROOT / sweep_record.RECORD_RELATIVE,
    )
    totals = manifest["totals"]
    print(
        f"Wrote {args.out} ({totals['units']} units, {totals['rows']} rows, {totals['batches']} batches)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
