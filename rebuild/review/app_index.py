"""The two sidecars the review app boots from: `app-units.ndjson.gz`, one slim row per human unit, and `app-locator.ndjson.gz`, one address per machine-approved or no-verdict unit.

They exist because the app's resident set had grown with the corpus rather than with the queue: loading every class shard to reach the units awaiting a verdict retained a gigabyte-scale map of records whose two largest fields — `explain` and `drafts`, over half of every shard's bytes — only the explain panel ever opens. The app index carries exactly the fields the row, sample, docket, search, echo, and progress paths read, so the tab holds the human workload and nothing else; `rebuild/test_app_index.py` holds `app_row` against the shards field for field, the same standard `rebuild/test_unit_index.py` sets for the plumbing's projection.

What replaces the dropped fields is an address rather than a copy. Every row carries the part index, byte offset and byte length of its own record inside the class shard it was written to, captured while `build._write_shard` streamed that shard out — so the explain panel fetches one record with an HTTP Range request against a static file, with no server-side logic and no endpoint. That makes `_write_shard`'s framing a byte-addressing contract as well as a serialization one: each fragment's bytes are a standalone JSON element, pure ASCII so a character offset is a byte offset.

The locator is the same address without the row, for every unit the app index does not hold, so a deep link to a machine-approved unit still resolves. It is streamed and discarded rather than retained — the whole point being that nothing here scales the tab's heap with the corpus.

Both files are stamped with the manifest's sha256 exactly as `unit_index` is, and carry the manifest's `generated_at` besides, so a tab that fetched an index written for another build refuses it at boot rather than Range-fetching offsets into rewritten shards.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from rebuild.review import unit_index
from rebuild.review.audit import MACHINE_CHANNELS

APP_INDEX_NAME = "app-units.ndjson.gz"
APP_INDEX_FORMAT = "ams-review-app-index/1"
LOCATOR_NAME = "app-locator.ndjson.gz"
LOCATOR_FORMAT = "ams-review-app-locator/1"
ARTIFACTS = ((APP_INDEX_NAME, APP_INDEX_FORMAT), (LOCATOR_NAME, LOCATOR_FORMAT))
# Level 6 rather than `unit_index`'s level 1: these cross the wire on every page load under `Cache-Control: no-store`, where the plumbing's index is read once per cycle off local disk.
COMPRESS_LEVEL = 6

_SLIMMED_FLAGS = (*MACHINE_CHANNELS, "no_verdict")

Span = tuple[int, int, int]


def artifact_path(surface: Path, name: str) -> Path:
    return Path(surface) / name


def app_row(fragment: dict, part: int, start: int, length: int) -> dict:
    """One human unit's shard fragment projected onto what the app draws, plus the address of the fragment itself. Key order is fixed and every key is always present, so two builds of the same surface write the same bytes and every row shares one hidden class in the browser.

    The four machine-channel flags are asserted false rather than carried: `build.check_unit` enforces that a unit with any of them, or with `no_verdict`, has `batch: null` — on the units its own build computed, and through the content-key stamp on the ones the unit cache served — so a row in this file provably has none. A reader finds them absent and falsy, which is what the shard's `false` already meant.
    """
    assert not any(fragment.get(flag) for flag in _SLIMMED_FLAGS), fragment.get("id")
    seams = fragment.get("secondary_seams")
    homeless = any(isinstance(seam, dict) and seam.get("home") is None for seam in seams or ())
    return {
        "id": fragment["id"],
        "batch": fragment.get("batch"),
        "class": fragment.get("class"),
        "group": fragment.get("group"),
        "echo": fragment.get("echo"),
        "cluster": fragment.get("cluster"),
        "notation": fragment.get("notation"),
        "notation_tokens": fragment.get("notation_tokens") or [],
        "codepoints": fragment.get("codepoints"),
        "text_entities": fragment.get("text_entities"),
        "pair": fragment.get("pair"),
        "pair_codepoints": fragment.get("pair_codepoints"),
        "highlight": fragment.get("highlight"),
        "boundary_marks": fragment.get("boundary_marks") or [],
        "secondary_seams": seams,
        # `onlyHereSeamSpans` reads after.cells only to place the homeless seams, and answers [] without them, so a row with none is identical to the same row carrying the cells — and the cells are the largest field left.
        "after": {"cells": list((fragment.get("after") or {}).get("cells") or [])} if homeless else None,
        "configs": fragment.get("configs") or [],
        "config_gate": fragment.get("config_gate"),
        "config_note": fragment.get("config_note"),
        "config_class_note": fragment.get("config_class_note"),
        "render_groups": fragment.get("render_groups"),
        "summary": fragment.get("summary"),
        "exemplar": fragment.get("exemplar"),
        "kinds": fragment.get("kinds") or [],
        "shard_part": part,
        "byte_start": start,
        "byte_length": length,
    }


def locator_row(fragment: dict, part: int, start: int, length: int) -> dict:
    """One machine-approved or no-verdict unit's address, and nothing else: enough for a deep link to fetch the record itself."""
    return {
        "id": fragment["id"],
        "class": fragment.get("class"),
        "shard_part": part,
        "byte_start": start,
        "byte_length": length,
    }


def header(surface: Path, fmt: str) -> dict:
    """The first line of either file. `manifest_sha256` is the same stamp `unit_index` writes, so a half-written surface can never be read as describing the shards beside it; `generated_at` is copied from the manifest so a browser can check the pairing without hashing anything."""
    surface = Path(surface)
    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    return {
        "format": fmt,
        "manifest_sha256": unit_index.manifest_sha256(surface),
        "generated_at": manifest.get("generated_at"),
    }


def write_app_artifacts(
    surface: Path,
    shards: Mapping[str, list[dict]],
    spans: Mapping[str, Sequence[Span]],
) -> tuple[Path, Path]:
    """Write both sidecars from the fragments and spans the build already holds, stamped with the manifest beside them — so this runs after the manifest is written. Classes are walked in `unit_index.class_shard_key` order, the order `write_index` and every shard walk use, and each class's fragments split into the app index or the locator on `batch is not None`. A pinned gzip mtime keeps consecutive builds of the same inputs byte-identical."""
    surface = Path(surface)
    ordered = sorted(shards.items(), key=lambda item: unit_index.class_shard_key(item[0]))
    human = sum(
        1 for _class_id, fragments in ordered for fragment in fragments if fragment.get("batch") is not None
    )
    machine = sum(len(fragments) for _class_id, fragments in ordered) - human
    index_path = artifact_path(surface, APP_INDEX_NAME)
    locator_path = artifact_path(surface, LOCATOR_NAME)
    with open(index_path, "wb") as index_raw, open(locator_path, "wb") as locator_raw:
        with (
            gzip.GzipFile(fileobj=index_raw, mode="wb", mtime=0, compresslevel=COMPRESS_LEVEL) as index,
            gzip.GzipFile(fileobj=locator_raw, mode="wb", mtime=0, compresslevel=COMPRESS_LEVEL) as locator,
        ):
            index.write(_line({**header(surface, APP_INDEX_FORMAT), "units": human}))
            locator.write(_line({**header(surface, LOCATOR_FORMAT), "units": machine}))
            for class_id, fragments in ordered:
                addresses = spans.get(class_id) or ()
                for fragment, (part, start, length) in zip(fragments, addresses, strict=True):
                    if fragment.get("batch") is None:
                        locator.write(_line(locator_row(fragment, part, start, length)))
                    else:
                        index.write(_line(app_row(fragment, part, start, length)))
    return index_path, locator_path


def _line(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False) + "\n").encode()


def artifact_header(surface: Path, name: str) -> dict | None:
    """One sidecar's header line alone, or None when there is none to read — so the build's contract check can say the file is there and stamped for the manifest beside it without parsing a hundred thousand rows to find out."""
    path = artifact_path(surface, name)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            record = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration:
        return None
    return record if isinstance(record, dict) else None


def artifact_is_current(surface: Path, name: str, fmt: str) -> bool:
    """Whether the sidecar beside this manifest describes it: present, in a format this reader knows, and stamped with the manifest's own bytes."""
    record = artifact_header(surface, name)
    if record is None or record.get("format") != fmt:
        return False
    try:
        return record.get("manifest_sha256") == unit_index.manifest_sha256(surface)
    except OSError:
        return False


def load_rows(surface: Path, name: str) -> list[dict[str, Any]] | None:
    """Every row of one sidecar, or None when it is absent or unreadable. The app streams these files line by line and never materializes them; this is for the tests and tools that want the whole list."""
    path = artifact_path(surface, name)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            next(stream)
            return [json.loads(line) for line in stream]
    except OSError, EOFError, ValueError, StopIteration:
        return None
