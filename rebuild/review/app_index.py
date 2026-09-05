"""The sidecars the review app boots from: `app-units.ndjson.gz`, one slim row per human unit, and the locator pair — `app-locator.ndjson.gz`, a block table, over `app-locator-rows.ndjson.gz`, one address per machine-approved or no-verdict unit.

They exist because the app's resident set had grown with the corpus rather than with the queue: loading every class shard to reach the units awaiting a verdict retained a gigabyte-scale map of records whose two largest fields — `explain` and `drafts`, over half of every shard's bytes — only the explain panel ever opens. The app index carries exactly the fields the row's label, the docket, search, echo, filter, and progress paths read, so the tab holds the human workload and nothing else; `rebuild/test_app_index.py` holds `app_row` against the shards field for field, the same standard `rebuild/test_unit_index.py` sets for the plumbing's projection.

What replaces the dropped fields is an address rather than a copy. Every row carries the part index, byte offset and byte length of its own record inside the class shard it was written to, captured while `build._write_shard` streamed that shard out — so a rendered card fetches its record with an HTTP Range request against a static file, with no server-side logic and no endpoint: the sample text, the pair band and the settled cells the moment the card is drawn, and the explain table when its panel opens. That makes `_write_shard`'s framing a byte-addressing contract as well as a serialization one: each fragment's bytes are a standalone JSON element, pure ASCII so a character offset is a byte offset.

The locator is the same address without the row, for every unit the app index does not hold, and it is addressed the same way its rows address the shards. The rows file is a sequence of gzip members, one per block of at most `LOCATOR_BLOCK_ROWS` rows, no block spanning two classes, in shard order — classes by `unit_index.class_shard_key`, each class's rows in triage order, which is unit-number order within the class because ids are assigned in that order. The table file lists those blocks: the byte span of each member inside the rows file, its class, and the first and last id it holds. So a show-machine fold reads its class's rows one block at a time and draws them a window at a time, and a deep link to a machine unit binary-searches each class's blocks by unit number and Range-fetches the one member that can hold the id — a block is decodable on its own, so neither path ever reads the whole file. Ids are assigned over the whole workload in triage order, so a class's ids ascend while the classes' id ranges overlap one another, which is why the order is shard order with the table rather than one global unit-number order: that would interleave the classes and cost a fold a scan of blocks it mostly discards. The table is the only part a tab retains, and it is the machine workload divided by the block size.

All three files are stamped for the manifest's sha256 exactly as `unit_index` is, and carry the manifest's `generated_at` besides, so a tab that fetched an index written for another build refuses it at boot rather than Range-fetching offsets into rewritten shards; the rows file carries no header of its own and is stamped through the table, which records the byte length the rows file must have and whose blocks each name the id their member starts with.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from rebuild.review import unit_index
from rebuild.review.audit import MACHINE_CHANNELS

APP_INDEX_NAME = "app-units.ndjson.gz"
APP_INDEX_FORMAT = "ams-review-app-index/1"
LOCATOR_NAME = "app-locator.ndjson.gz"
LOCATOR_FORMAT = "ams-review-app-locator/2"
LOCATOR_ROWS_NAME = "app-locator-rows.ndjson.gz"
ARTIFACTS = ((APP_INDEX_NAME, APP_INDEX_FORMAT), (LOCATOR_NAME, LOCATOR_FORMAT))
# Rows per gzip member of the locator's rows file. A member is what one Range request fetches and one decompression yields, for a fold's next window or a deep link's one candidate per class; at about a hundred bytes a row it is a few tens of kilobytes on the wire, and the table that names every member is the machine workload divided by this.
LOCATOR_BLOCK_ROWS = 1024
# Level 6 rather than `unit_index`'s level 1: these cross the wire on every page load under `Cache-Control: no-store`, where the plumbing's index is read once per cycle off local disk.
COMPRESS_LEVEL = 6

_SLIMMED_FLAGS = (*MACHINE_CHANNELS, "no_verdict")

Span = tuple[int, int, int]


def artifact_path(surface: Path, name: str) -> Path:
    return Path(surface) / name


def app_row(fragment: dict, part: int, start: int, length: int) -> dict:
    """One human unit's shard fragment projected onto what the app reads without the record in hand, plus the address of the fragment itself. Key order is fixed and every key is always present, so two builds of the same surface write the same bytes and every row shares one hidden class in the browser.

    What a card draws from the record itself — `text_entities`, `highlight`, and `after.cells` — is not here: the app Range-fetches the record for each card it renders, the way it already does for the explain panel, so the resident row holds only what the docket, search, filters, echo groups and progress read over the whole queue at once. The four machine-channel flags are asserted false rather than carried: `build.check_unit` enforces that a unit with any of them, or with `no_verdict`, has `batch: null` — on the units its own build computed, and through the content-key stamp on the ones the unit cache served — so a row in this file provably has none. A reader finds them absent and falsy, which is what the shard's `false` already meant.
    """
    assert not any(fragment.get(flag) for flag in _SLIMMED_FLAGS), fragment.get("id")
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
        "pair": fragment.get("pair"),
        "pair_codepoints": fragment.get("pair_codepoints"),
        "boundary_marks": fragment.get("boundary_marks") or [],
        "secondary_seams": fragment.get("secondary_seams"),
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
    """One machine-approved or no-verdict unit's address, and nothing else: enough for a fold's window or a deep link to fetch the record itself."""
    return {
        "id": fragment["id"],
        "class": fragment.get("class"),
        "shard_part": part,
        "byte_start": start,
        "byte_length": length,
    }


def unit_number(unit_id: str) -> int:
    """The integer a unit id counts, the order the locator's blocks are searched by: `build.check_unit` holds every id to the `u-NNNN` shape, and the app derives the same number from the same digits."""
    prefix, _dash, digits = unit_id.partition("-")
    if prefix != "u" or not digits.isdigit():
        raise ValueError(f"not a unit id: {unit_id!r}")
    return int(digits)


def locator_block(class_id: str | None, start: int, length: int, first: str, last: str, units: int) -> dict:
    """One line of the table: the byte span of a gzip member inside the rows file, the class every row in it belongs to, the ids of its first and last rows, and how many it holds."""
    return {
        "class": class_id,
        "byte_start": start,
        "byte_length": length,
        "first": first,
        "last": last,
        "units": units,
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


def app_line(fragment: dict, part: int, start: int, length: int) -> bytes:
    return _line(app_row(fragment, part, start, length))


def locator_line(fragment: dict, part: int, start: int, length: int) -> bytes:
    return _line(locator_row(fragment, part, start, length))


def write_app_artifacts_lines(
    surface: Path,
    index_lines: Iterable[bytes],
    locator_lines: Iterable[bytes],
    *,
    human: int,
    machine: int,
) -> tuple[Path, Path]:
    """Write the sidecars from already-projected lines in the order they arrive — the app index's rows, then the locator's — stamped with the manifest beside them, so this runs after the manifest is written; `human` and `machine` are the row counts the two headers carry. The locator's lines are cut into blocks as they go by, a block closing at `LOCATOR_BLOCK_ROWS` rows or where the class changes, each written to the rows file as its own gzip member and entered in the table by the span it landed on; the table is written last, since its header states the rows file's total length. Pinned gzip mtimes keep consecutive builds of the same inputs byte-identical.

    Each file is staged under a sibling `.partial` name and renamed only once all three have closed cleanly, exactly as `build._write_shard` stages its parts and for a sharper version of the same reason: `app_row` asserts on a fragment whose machine flags are not false, and the projection can raise partway through a file whose previous build's copy the app is still being served. In place, that leaves a truncated sidecar with a valid header — the app boots from it, Range-fetches offsets into shards it no longer describes, and the failure surfaces as garbled records rather than as the refusal `artifact_is_current` exists to make. Staged, a failed write leaves the last good set in place and nothing beside it, and the three land together, which is what lets `artifact_cycle.surface_build_skippable` read their currency as a statement about the whole surface.
    """
    surface = Path(surface)
    index_path = artifact_path(surface, APP_INDEX_NAME)
    locator_path = artifact_path(surface, LOCATOR_NAME)
    rows_path = artifact_path(surface, LOCATOR_ROWS_NAME)
    staged = tuple(path.with_name(path.name + ".partial") for path in (index_path, locator_path, rows_path))
    try:
        with gzip.GzipFile(staged[0], mode="wb", mtime=0, compresslevel=COMPRESS_LEVEL) as index:
            index.write(_line({**header(surface, APP_INDEX_FORMAT), "units": human}))
            for line in index_lines:
                index.write(line)
        with open(staged[2], "wb") as rows:
            blocks = _write_locator_blocks(rows, locator_lines)
            rows_bytes = rows.tell()
        with gzip.GzipFile(staged[1], mode="wb", mtime=0, compresslevel=COMPRESS_LEVEL) as locator:
            locator.write(
                _line(
                    {
                        **header(surface, LOCATOR_FORMAT),
                        "units": machine,
                        "blocks": len(blocks),
                        "block_rows": LOCATOR_BLOCK_ROWS,
                        "rows_bytes": rows_bytes,
                    }
                )
            )
            for block in blocks:
                locator.write(_line(block))
        staged[0].replace(index_path)
        staged[2].replace(rows_path)
        staged[1].replace(locator_path)
        return index_path, locator_path
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def _write_locator_blocks(rows, lines: Iterable[bytes]) -> list[dict]:
    """Cut the locator's lines into gzip members on `rows`, one per block, and return the table entry for each. Only the block being filled is held; a line is read for its id and class and released with the block it joins. A class's rows must arrive in ascending unit-number order, which is what the app's binary search over the class's blocks assumes, so a row out of that order is refused here rather than found nowhere in the browser."""
    blocks: list[dict] = []
    pending: list[bytes] = []
    pending_class: str | None = None
    first = last = None
    last_number = -1

    def close() -> None:
        assert first is not None and last is not None
        start = rows.tell()
        rows.write(gzip.compress(b"".join(pending), compresslevel=COMPRESS_LEVEL, mtime=0))
        blocks.append(locator_block(pending_class, start, rows.tell() - start, first, last, len(pending)))
        pending.clear()

    for line in lines:
        row = json.loads(line)
        unit_id = row["id"]
        number = unit_number(unit_id)
        class_id = row.get("class")
        if pending and class_id != pending_class:
            close()
            last_number = -1
        elif pending and len(pending) >= LOCATOR_BLOCK_ROWS:
            close()
        if number <= last_number:
            raise ValueError(f"{unit_id} follows u-{last_number} in {class_id}: a class's rows must ascend")
        if not pending:
            pending_class = class_id
            first = unit_id
        pending.append(line)
        last = unit_id
        last_number = number
    if pending:
        close()
    return blocks


def write_app_artifacts(
    surface: Path,
    shards: Mapping[str, list[dict]],
    spans: Mapping[str, Sequence[Span]],
) -> tuple[Path, Path]:
    """Write the sidecars from fragments and spans a caller holds whole. Classes are walked in `unit_index.class_shard_key` order, the order `write_index` and every shard walk use, and each class's fragments split into the app index or the locator on `batch is not None` — the same projection, in the same order, that the build spools a fragment at a time, so the two write the same bytes."""
    ordered = sorted(shards.items(), key=lambda item: unit_index.class_shard_key(item[0]))
    rows = [
        (fragment, address)
        for class_id, fragments in ordered
        for fragment, address in zip(fragments, spans.get(class_id) or (), strict=True)
    ]
    human = sum(1 for fragment, _address in rows if fragment.get("batch") is not None)
    return write_app_artifacts_lines(
        surface,
        (app_line(fragment, *address) for fragment, address in rows if fragment.get("batch") is not None),
        (locator_line(fragment, *address) for fragment, address in rows if fragment.get("batch") is None),
        human=human,
        machine=len(rows) - human,
    )


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
    """Whether the sidecar beside this manifest describes it: present, in a format this reader knows, and stamped with the manifest's own bytes. The locator answers for its rows file too, which carries no header: the table's header states the length the rows file was written at, and a rows file of any other length — missing, truncated, or left by another build — means the table's spans address the wrong bytes."""
    record = artifact_header(surface, name)
    if record is None or record.get("format") != fmt:
        return False
    try:
        if record.get("manifest_sha256") != unit_index.manifest_sha256(surface):
            return False
        if name == LOCATOR_NAME:
            return artifact_path(surface, LOCATOR_ROWS_NAME).stat().st_size == record.get("rows_bytes")
        return True
    except OSError:
        return False


def load_rows(surface: Path, name: str) -> list[dict[str, Any]] | None:
    """Every line after the header of one stamped sidecar — the app index's rows, or the locator's block table — or None when it is absent or unreadable. The app streams the index line by line and never materializes it; this is for the tests and tools that want the whole list."""
    path = artifact_path(surface, name)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            next(stream)
            return [json.loads(line) for line in stream]
    except OSError, EOFError, ValueError, StopIteration:
        return None


def load_locator_rows(surface: Path) -> list[dict[str, Any]] | None:
    """Every row of the locator's rows file in file order, or None when it is absent or unreadable. `gzip` reads the members back to back as one stream; the app never does, and fetches one member at a time by the table's spans."""
    path = artifact_path(surface, LOCATOR_ROWS_NAME)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream]
    except OSError, EOFError, ValueError:
        return None


def locator_block_bytes(surface: Path, block: Mapping[str, Any]) -> bytes:
    """The bytes of one block the way the app's Range request reads them: that slice of the rows file, nothing else."""
    with open(artifact_path(surface, LOCATOR_ROWS_NAME), "rb") as rows:
        rows.seek(block["byte_start"])
        return rows.read(block["byte_length"])
