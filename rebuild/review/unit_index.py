"""The surface's slim per-unit index: one record per unit carrying exactly the fields the verdict plumbing reads, written beside the manifest as `units-index.ndjson.gz`.

The shards are the authority and this file is a projection of them — never a second source. It exists because the four plumbing tools (carry, echo fill, standing fill, the complaint docket) and the two sitting-prep tools (the docket data, the novelty order) each reach a few slim fields per unit, and reading them off the shards means `json.loads` over gigabytes, once per tool per cycle, two thirds of it `explain`, `drafts` and `summary` prose that no plumbing consumer opens. `index_record` is the whole of the projection and `rebuild/test_unit_index.py` holds it against the shipped shards field for field, so a field added to a shard and wanted by a tool has to be added here rather than silently read as absent.

Two fields are counted rather than copied, because counting is all any reader does with them: `render_groups` is the number of groups (standing fill wants "exactly one") and `secondary_seams` the number of seams (standing fill wants "none"). Everything else is the shard's own value.

The file is stamped with the manifest's identity digest (`manifest_sha256`), exactly as the unit store is, so a surface half-written by a crashed build can never be read as describing the shards beside it — and a reader that finds no index, or one stamped for another manifest, falls back to streaming the shards through the same projection. That fallback is what lets carry resolve verdicts against the archived snapshots under tmp/review-pre-*, every one of which predates this file.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

INDEX_NAME = "units-index.ndjson.gz"
INDEX_FORMAT = "ams-review-unit-index/1"
ASSET_COMPONENTS: tuple[str, ...] = ("static",)


def index_path(surface: Path) -> Path:
    return Path(surface) / INDEX_NAME


def class_shard_key(class_id: str) -> str:
    """The sort key every walk of the surface orders classes by. `write_index` sorts on it too, so the index's records and a shard walk run in the same order — and a class written as numbered parts sorts where its bare form would, because the character after the class id is `.` either way."""
    return f"{class_id}.json"


def class_shards(meta: Mapping[str, Any]) -> list[str]:
    """One manifest class entry's shard parts, in part order. A `ams-review-manifest/1` entry carries a single `shard` string instead of the `shards` list, and reading either is load-bearing rather than politeness: the unit cache reads the prior surface's shards and the carry reads the archived snapshots under `tmp/review-pre-*`, both of which are the older shape until they are rebuilt."""
    shards = meta.get("shards")
    if shards is None:
        return [str(meta["shard"])]
    return [str(part) for part in shards]


def index_record(fragment: dict) -> dict:
    """One shard fragment projected onto the fields the plumbing reads. Key order is fixed so two builds of the same surface write the same bytes."""
    before = fragment.get("before") or {}
    after = fragment.get("after") or {}
    policy = (fragment.get("drafts") or {}).get("policy")
    return {
        "id": fragment["id"],
        "batch": fragment.get("batch"),
        "class": fragment.get("class"),
        "cluster": fragment.get("cluster"),
        "echo": fragment.get("echo"),
        "group": fragment.get("group"),
        "notation": fragment.get("notation"),
        "notation_tokens": fragment.get("notation_tokens") or [],
        "codepoints": fragment.get("codepoints"),
        "configs": fragment.get("configs") or [],
        "kinds": fragment.get("kinds") or [],
        "ink_identical": fragment.get("ink_identical"),
        "picture_identical": fragment.get("picture_identical"),
        "junior_equivalent": fragment.get("junior_equivalent"),
        "ink_deltas": fragment.get("ink_deltas"),
        "no_verdict": fragment.get("no_verdict"),
        "content_key": fragment.get("content_key"),
        "render_groups": len(fragment.get("render_groups") or []),
        "summary": fragment.get("summary"),
        "provenance": fragment.get("provenance") or [],
        "pair": fragment.get("pair"),
        "secondary_seams": len(fragment.get("secondary_seams") or []),
        "before": {"glyphs": before.get("glyphs") or [], "seams": before.get("seams") or []},
        "after": {"cells": after.get("cells") or [], "seams": after.get("seams") or []},
        "policy": (
            {
                "file": policy["file"],
                "keypath": policy["keypath"],
                "suggested_record": policy.get("suggested_record"),
            }
            if policy
            else None
        ),
    }


def manifest_sha256(surface: Path) -> str:
    """The manifest's identity: everything it says about which units and shards it describes, hashed over its parsed content with `ASSET_COMPONENTS` projected out of `inputs_fingerprint`. Those components are the fingerprint the copied review UI assets ride, and no shard, sidecar, unit or plumbing step reads them, so they are outside the surface's identity and outside every hard freshness check — this roster is what every site that treats a component as soft reads. Projecting them out is exactly what lets an assets refresh rewrite that one field in place and leave every sidecar beside it, and the unit-cache store with them, stamped for the manifest they describe. A manifest that will not parse is hashed by its raw bytes instead, the way `fingerprint._projected_digest` falls back, so a broken file mismatches rather than passing for something."""
    raw = (Path(surface) / "manifest.json").read_bytes()
    try:
        document = json.loads(raw)
    except ValueError:
        return hashlib.sha256(raw).hexdigest()
    if isinstance(document, dict):
        recorded = document.get("inputs_fingerprint")
        if isinstance(recorded, dict):
            document = {
                **document,
                "inputs_fingerprint": {
                    name: value for name, value in recorded.items() if name not in ASSET_COMPONENTS
                },
            }
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def shard_paths(surface: Path) -> list[Path]:
    """The shard parts in the order every reader walks them: classes by `class_shard_key`, each class's parts in the order its manifest lists them. The manifest is the authority rather than a glob over `units/`, because only it says which parts belong to a class and in what order they concatenate. The index is written in this order too, so a tool that resolves ties by "first seen" answers the same either way. A surface with no readable manifest has no shards to name."""
    surface = Path(surface)
    try:
        manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
        classes = list(manifest["classes"])
    except OSError, ValueError, KeyError, TypeError:
        return []
    ordered = sorted(classes, key=lambda meta: class_shard_key(meta.get("id", "")))
    return [surface / part for meta in ordered for part in class_shards(meta)]


def index_line(fragment: dict) -> bytes:
    """One index record as the line the file holds it on. The build streams these into a spool as its fragments go by, so the whole of what `write_index` would have held is on disk instead."""
    return (json.dumps(index_record(fragment), ensure_ascii=False) + "\n").encode()


def write_index_lines(surface: Path, lines: Iterable[bytes]) -> Path:
    """Write the index from already-projected lines in the order they arrive, stamped with the manifest beside it — so this runs after the manifest is written. Level 1 and a pinned gzip mtime, like the unit store: written once and read once per cycle, where level 9's seconds cost more than its megabytes save."""
    header = {"format": INDEX_FORMAT, "manifest_sha256": manifest_sha256(surface)}
    path = index_path(surface)
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
            stream.write((json.dumps(header) + "\n").encode())
            for line in lines:
                stream.write(line)
    return path


def write_index(surface: Path, shards: Iterable[tuple[str, list[dict]]]) -> Path:
    """Write the index from fragments a caller holds whole. `shards` is (class id, fragments) in any order; the file is written in shard-path order, so it is `write_index_lines` over the same projection the build spools a fragment at a time, and the two write the same bytes."""
    ordered = sorted(shards, key=lambda item: class_shard_key(item[0]))
    return write_index_lines(
        surface, (index_line(fragment) for _class_id, fragments in ordered for fragment in fragments)
    )


def index_header(surface: Path) -> dict | None:
    """The index's header line alone, or None when there is none to read. Separate from `load_index` because the build's own contract check wants to know the file is there and stamped for the manifest beside it without parsing four hundred thousand records to find out."""
    path = index_path(surface)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration:
        return None
    return header if isinstance(header, dict) else None


def index_is_current(surface: Path) -> bool:
    """Whether the index beside this manifest describes it: present, in a format this reader knows, and stamped with the manifest's own identity."""
    header = index_header(surface)
    if header is None or header.get("format") != INDEX_FORMAT:
        return False
    try:
        return header.get("manifest_sha256") == manifest_sha256(surface)
    except OSError:
        return False


def load_index(surface: Path) -> list[dict] | None:
    """The index's records, or None when there is no usable index: absent, unreadable, format-mismatched, or stamped for a manifest other than the one on disk. A None costs the caller one fallback pass over the shards, so over-invalidation stays the safe direction here too."""
    if not index_is_current(surface):
        return None
    try:
        with gzip.open(index_path(surface), "rt", encoding="utf-8") as stream:
            next(stream)
            return [json.loads(line) for line in stream]
    except OSError, EOFError, ValueError, StopIteration:
        return None


def iter_shard_fragments(surface: Path) -> Iterator[dict]:
    """The fallback source: the shards' own fragments, one part at a time so only one is ever resident. That bound is the whole point — the corpus runs to gigabytes and no one part is larger than `build.SHARD_PART_BYTES` — where concatenating them all would hold the whole corpus at once."""
    for path in shard_paths(surface):
        shard = json.loads(path.read_text(encoding="utf-8"))
        yield from shard


def stream_shards(surface: Path) -> Iterator[dict]:
    """The fallback: the shards themselves, projected the way the sidecar would have been."""
    for fragment in iter_shard_fragments(surface):
        yield index_record(fragment)


def iter_units(surface: Path) -> Iterator[dict]:
    """Every unit on a surface, projected, one at a time: the index when it is there and stamped for this manifest, the shards otherwise. A caller that keeps only a slice of the corpus — the carry, which wants the few tens of thousands of units a prior surface's verdicts actually name — should read it this way rather than through `load_units`, so the other four hundred thousand records never coexist with the ones it is keeping."""
    if index_is_current(surface):
        with gzip.open(index_path(surface), "rt", encoding="utf-8") as stream:
            next(stream)
            for line in stream:
                yield json.loads(line)
        return
    yield from stream_shards(surface)


def load_units(surface: Path) -> list[dict]:
    """Every unit on a surface, projected. The index when it is there and stamped for this manifest; the shards otherwise."""
    records = load_index(surface)
    if records is not None:
        return records
    return list(stream_shards(surface))
