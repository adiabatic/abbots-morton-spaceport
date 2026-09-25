"""The sidecar files the review app boots from: `app-units.ndjson.gz`, one row per human unit, and the locator pair, `app-locator.ndjson.gz`, a block table, over `app-locator-rows.ndjson.gz`, one address per machine-approved or no-verdict unit.

The app reads these instead of the class shards so that a tab's memory grows with the review queue, not with the corpus. Loading the shards would hold every record, including `explain` and `drafts`, the two largest fields of a full fragment, which only the explain panel reads. An app-index row carries only the fields that the row label, the docket, search, echo groups, filters and progress read. `rebuild/test_app_index.py` checks `app_row` against the shards field by field, as `rebuild/test_unit_index.py` does for the plumbing's index.

In place of the dropped fields, each row carries the address of its own record in its class shard: the part index, byte offset and byte length, captured as `build._write_shard` wrote the shard. A card fetches its record with an HTTP Range request against the static file, with no server-side endpoint: the sample text, the pair band and the settled cells when the card is drawn, and the explain table when its panel opens. So `_write_shard`'s framing must keep every fragment addressable by byte offset: each fragment's bytes are a standalone JSON element, and they are pure ASCII, so a character offset is a byte offset.

The locator holds the same address, without the other fields, for every unit the app index does not hold. The rows file is a sequence of gzip members, one per block of at most `LOCATOR_BLOCK_ROWS` rows. No block spans two classes. The blocks are in shard order: classes by `unit_index.class_shard_key`, and each class's rows in id order, which is the order the build writes a class's fragments in. The table file lists the blocks: each member's byte span inside the rows file, its class, and the first and last id it holds. A show-machine fold reads its class's rows one block at a time. A deep link to a machine unit binary-searches each class's blocks by id and Range-fetches the one member that can hold the id. Ids are content-derived strings, so the search compares strings. Each block decodes on its own, so neither path reads the whole file. Within a class the blocks are disjoint and ascending, but the id ranges of different classes overlap. One global id order would interleave the classes and make a fold read many blocks only to discard them, which is why the file is in shard order. A tab keeps the table, whose length is the machine workload divided by the block size, and a few decoded blocks (`BLOCK_CACHE_CAP` in `static/slim.js`).

An app-index row carries two values the fragment does not: `order`, the unit's position in the manifest's triage index (`human_unit_ids`), and `batch`, the slice that position falls in. They describe the queue, not the unit, so no fragment carries them. The batch view shows the rows whose `batch` matches, in `order`.

The app index and the locator table each start with a header carrying the manifest's sha256, as `unit_index` does, and the manifest's `generated_at`. A tab rejects a sidecar written for another build at boot instead of Range-fetching offsets into rewritten shards. The rows file has no header. The table's header records the byte length the rows file must have, and each block names the id its member starts with.
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
# Rows per gzip member of the locator's rows file. The app fetches one member with one Range request and decompresses it on its own, for a fold's next window or a deep link's candidate in one class. A row is about 120 bytes uncompressed, and a full member is about 18 KB gzipped (measured on the live surface's locator). The table has one line per member.
LOCATOR_BLOCK_ROWS = 1024
# Level 6, where `unit_index` uses level 1: the app fetches these files on every page load (`Cache-Control: no-store`), while the plumbing reads its index from local disk.
COMPRESS_LEVEL = 6

_SLIMMED_FLAGS = (*MACHINE_CHANNELS, "no_verdict")

Span = tuple[int, int, int]


def artifact_path(surface: Path, name: str) -> Path:
    return Path(surface) / name


def app_row(fragment: dict, part: int, start: int, length: int, *, order: int, batch: int) -> dict:
    """Project one human unit's shard fragment onto the fields the app reads without fetching the record, plus the unit's place in the manifest's triage index and the fragment's address. Every key is always present, in a fixed order, so two builds of the same surface write the same bytes and every row shares one hidden class in the browser.

    What a card draws from the record (`text_entities`, `highlight` and `after.cells`) is not here; the app Range-fetches the record for each card it renders. The three machine-channel flags and `no_verdict` are asserted false and left out: `audit.assign_batches` gives no triage-index place to a unit with any of them, and `build.check_shards` checks that the manifest's index holds only human units. A reader finds the flags absent, which it treats like the shard's `false`.
    """
    assert not any(fragment.get(flag) for flag in _SLIMMED_FLAGS), fragment.get("id")
    return {
        "id": fragment["id"],
        "order": order,
        "batch": batch,
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
    """One machine-approved or no-verdict unit's id, class and shard address, which is all a fold or a deep link needs to fetch the record."""
    return {
        "id": fragment["id"],
        "class": fragment.get("class"),
        "shard_part": part,
        "byte_start": start,
        "byte_length": length,
    }


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
    """The header line of the app index or the locator table. `manifest_sha256` is the stamp `unit_index` writes, which ties the file to the manifest beside it. `generated_at` is copied from the manifest so a browser can check the pairing without hashing anything."""
    surface = Path(surface)
    manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
    return {
        "format": fmt,
        "manifest_sha256": unit_index.manifest_sha256(surface),
        "generated_at": manifest.get("generated_at"),
    }


def app_line(fragment: dict, part: int, start: int, length: int, *, order: int, batch: int) -> bytes:
    return _line(app_row(fragment, part, start, length, order=order, batch=batch))


def locator_line(fragment: dict, part: int, start: int, length: int) -> bytes:
    return _line(locator_row(fragment, part, start, length))


def respool_app_line(
    line: bytes, *, unit_id: str, order: int, batch: int, part: int, start: int, length: int
) -> bytes:
    """Rewrite a previous surface's app-index row for a unit served verbatim so it fits this surface. The id and queue place at the head and the address at the tail come from this build. The fields between them are the fragment's own and have not changed, so the result is byte for byte what `app_line` writes for the same fragment."""
    middle = line[line.index(b', "class": ') : line.rindex(b', "shard_part": ')]
    tail = json.dumps({"shard_part": part, "byte_start": start, "byte_length": length}).encode()[1:]
    return unit_index.line_head(unit_id, order, batch) + middle + b", " + tail + b"\n"


def write_app_artifacts_lines(
    surface: Path,
    index_lines: Iterable[bytes],
    locator_lines: Iterable[bytes],
    *,
    human: int,
    machine: int,
) -> tuple[Path, Path]:
    """Write the three sidecars from already-projected lines, in the order they arrive: the app index's rows, then the locator's. The headers carry the manifest's stamp, so this runs after the manifest is written. `human` and `machine` are the row counts the two headers carry. Locator lines are cut into blocks as they arrive: a block closes at `LOCATOR_BLOCK_ROWS` rows or where the class changes, and is written to the rows file as its own gzip member. The table is written last, because its header states the rows file's total length. Gzip mtimes are pinned, so the same inputs write the same bytes.

    Each file is written under a sibling `.partial` name and renamed only after all three have closed, as `build._write_shard` stages its parts. `app_row` asserts on a fragment with a machine flag, so the projection can fail partway through while the app is still serving the previous set. Written in place, that failure would leave a truncated sidecar whose header still matches the manifest, which `artifact_is_current` accepts. Staged, a failed write leaves the previous set in place and no `.partial` file. The three renames run back to back, since `artifact_cycle.surface_build_skippable` treats the three files being current as a statement about the whole surface.
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
    """Write the locator's lines to `rows` as gzip members, one per block, and return the table entry for each. Only the block being filled is held in memory. The app's binary search assumes each class's rows arrive in ascending id order (the order the build writes a class's fragments in), so a row out of order raises `ValueError` here instead of going missing in the browser."""
    blocks: list[dict] = []
    pending: list[bytes] = []
    pending_class: str | None = None
    first = last = None

    def close() -> None:
        assert first is not None and last is not None
        start = rows.tell()
        rows.write(gzip.compress(b"".join(pending), compresslevel=COMPRESS_LEVEL, mtime=0))
        blocks.append(locator_block(pending_class, start, rows.tell() - start, first, last, len(pending)))
        pending.clear()

    for line in lines:
        row = json.loads(line)
        unit_id = row["id"]
        class_id = row.get("class")
        if pending and class_id != pending_class:
            close()
            last = None
        elif pending and len(pending) >= LOCATOR_BLOCK_ROWS:
            close()
        if last is not None and unit_id <= last:
            raise ValueError(f"{unit_id} follows {last} in {class_id}: a class's rows must ascend")
        if not pending:
            pending_class = class_id
            first = unit_id
        pending.append(line)
        last = unit_id
    if pending:
        close()
    return blocks


def write_app_artifacts(
    surface: Path,
    shards: Mapping[str, list[dict]],
    spans: Mapping[str, Sequence[Span]],
) -> tuple[Path, Path]:
    """Write the sidecars from fragments and spans the caller holds in memory. Classes go in `unit_index.class_shard_key` order, as in `unit_index.write_index`. A fragment goes to the app index if the manifest's triage index holds its unit, and to the locator otherwise. The build spools the same projection one fragment at a time, in the same order, so both paths write the same bytes."""
    ordered = sorted(shards.items(), key=lambda item: unit_index.class_shard_key(item[0]))
    slot = unit_index.slot_reader(surface)
    human_rows: list[tuple[dict, Span, int, int]] = []
    machine_rows: list[tuple[dict, Span]] = []
    for class_id, fragments in ordered:
        for fragment, address in zip(fragments, spans.get(class_id) or (), strict=True):
            place = slot(fragment)
            order, batch = place["order"], place["batch"]
            if order is None or batch is None:
                machine_rows.append((fragment, address))
            else:
                human_rows.append((fragment, address, order, batch))
    return write_app_artifacts_lines(
        surface,
        (
            app_line(fragment, *address, order=order, batch=batch)
            for fragment, address, order, batch in human_rows
        ),
        (locator_line(fragment, *address) for fragment, address in machine_rows),
        human=len(human_rows),
        machine=len(machine_rows),
    )


def _line(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False) + "\n").encode()


def artifact_header(surface: Path, name: str) -> dict | None:
    """One sidecar's header line, or None if it is missing or unreadable. `artifact_is_current` uses it to check a file's stamp without reading its rows."""
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
    """Whether the sidecar matches the manifest beside it: present, in format `fmt`, and stamped with the manifest's sha256. For the locator this also covers the rows file, which has no header. The rows file's size must equal the `rows_bytes` the table's header records; a file of any other size (missing, truncated, or from another build) means the table's spans address the wrong bytes."""
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
    """Every line after the header of one sidecar (the app index's rows, or the locator's block table), or None if it is missing or unreadable. This is for tests and tools that want the whole list; the app reads the index line by line."""
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
    """Every row of the locator's rows file in file order, or None if it is missing or unreadable. `gzip` reads the members back to back as one stream; the app instead fetches one member at a time by the table's spans."""
    path = artifact_path(surface, LOCATOR_ROWS_NAME)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream]
    except OSError, EOFError, ValueError:
        return None


def locator_block_bytes(surface: Path, block: Mapping[str, Any]) -> bytes:
    """The bytes of one block as the app's Range request reads them: that slice of the rows file."""
    with open(artifact_path(surface, LOCATOR_ROWS_NAME), "rb") as rows:
        rows.seek(block["byte_start"])
        return rows.read(block["byte_length"])
