"""Writes and reads the surface's per-unit index, `units-index.ndjson.gz`: one record per unit with the fields the verdict plumbing reads, written beside the manifest.

The shards are the authority, and this file is a projection of them. It exists because the plumbing tools (carry, echo fill, standing fill, the complaint docket) and the review-session preparation tools (the docket data, the novelty order) each read a few fields per unit. Reading those from the shards would mean parsing gigabytes of JSON once per tool per cycle, much of it `explain` and `drafts`, of which the index keeps only the policy draft's file, key path, and suggested record. `index_record` defines the projection. rebuild/test_unit_index.py checks it against the fixture shards field for field and pins the field set. A field a tool needs must be added here: `UnitRecord.get` returns the default for a name outside `INDEX_FIELDS`, so a missing field would silently read as absent.

Two fields are counts, because counting is all any reader does with them: `render_groups` is the number of render groups (standing fill requires exactly one) and `secondary_seams` the number of secondary seams (standing fill requires none). Two fields come from the manifest instead of the shard: `order` is the unit's position in the manifest's triage index (`human_unit_ids`), and `batch` is the `batch_size` slice that position falls in. Both are null for a unit outside the index. A fragment carries neither, because a unit's place in the queue is not a property of the unit, and every reader derives them through `human_positions`. Every other field is the shard's own value.

The file is stamped with the manifest's identity digest (`manifest_sha256`), as the unit store is, so an index a crashed build did not rewrite is not read as describing the shards beside it. A reader that finds no index, or one stamped for another manifest, falls back to reading the shards through the same projection.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INDEX_NAME = "units-index.ndjson.gz"
INDEX_FORMAT = "ams-review-unit-index/1"
ASSET_COMPONENTS: tuple[str, ...] = ("static",)
ID_OPEN = b'{"id": "'
ORDER_SEAM = b'", "order": '
CLASS_SEAM = b', "class": '
MACHINE_TAIL = b'"batch": null'


def index_path(surface: Path) -> Path:
    return Path(surface) / INDEX_NAME


def class_shard_key(class_id: str) -> str:
    """Return the key every walk of the surface sorts classes by. `write_index` sorts by it too, so the index's records and a shard walk are in the same order. A class written as numbered parts sorts where its bare form would, because the character after the class id is `.` in both forms."""
    return f"{class_id}.json"


def class_shards(meta: Mapping[str, Any]) -> list[str]:
    """Return one manifest class entry's shard parts, in part order. An `ams-review-manifest/1` entry has a single `shard` string instead of the `shards` list. Both forms must be read, because the unit cache reads the prior surface's manifest, which keeps the older form until that surface is rebuilt."""
    shards = meta.get("shards")
    if shards is None:
        return [str(meta["shard"])]
    return [str(part) for part in shards]


def human_positions(manifest: Mapping[str, Any]) -> dict[str, int]:
    """Return each human unit's position in the manifest's triage index, keyed by id. This is the only source of a unit's `order`; its batch is that position divided by `batch_size`, computed inline by `workload_slot` and by `audit.batch_of` elsewhere. A manifest without the index puts every unit outside it; every manifest this build writes has one."""
    return {unit_id: position for position, unit_id in enumerate(manifest.get("human_unit_ids") or ())}


def workload_slot(
    positions: Mapping[str, int], batch_size: int, fragment: Mapping[str, Any]
) -> dict[str, int | None]:
    """Return a unit's `order` and `batch` as the index gives them, or None for both when the index does not hold the unit. The exception is a fragment that carries its own `batch`, which only a fragment from an older surface does: that batch is returned with `order` None, since such a surface paged in id order and its index, where it has one, gives the same batch."""
    order = positions.get(fragment["id"])
    if order is not None:
        return {"order": order, "batch": order // batch_size}
    return {"order": None, "batch": fragment.get("batch") if "batch" in fragment else None}


def index_record(fragment: dict, *, order: int | None = None, batch: int | None = None) -> dict:
    """Project one shard fragment onto the fields the plumbing reads, with the unit's place in the manifest's triage index passed in. The key order is fixed so that two builds of the same surface write the same bytes, and so that every line starts with `id`, `order`, and `batch` at the delimiters `ID_OPEN`, `ORDER_SEAM`, and `CLASS_SEAM`. `respool_index_line` joins a new head onto a line there, and `load_human_units` reads the id and the unit's place in the queue from the head without parsing the line. rebuild/test_unit_index.py checks this opening."""
    before = fragment.get("before") or {}
    after = fragment.get("after") or {}
    policy = (fragment.get("drafts") or {}).get("policy")
    return {
        "id": fragment["id"],
        "order": order,
        "batch": batch,
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


INDEX_FIELDS = tuple(index_record({"id": ""}))
_INDEX_FIELD_SET = frozenset(INDEX_FIELDS)
_CONTAINER_POOL_LIMIT = 16_384


@dataclass(frozen=True, slots=True, eq=False)
class UnitRecord(Mapping[str, Any]):
    """An immutable mapping over one record's selected fields, sharing its schema with every record from the same reader. Subscription and `.get` both raise KeyError for an index field the reader did not select, so a matcher that reads a field it did not declare fails instead of reading it as absent. Nested lists and dicts are pooled across records and must not be modified; copy them first. Use `dict(record)` to get an outer mapping that JSON can serialize."""

    _values: tuple[Any, ...]
    _schema: dict[str, int]

    def __getitem__(self, key: str) -> Any:
        return self._values[self._schema[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._schema)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        if key in _INDEX_FIELD_SET:
            return self[key]
        return default


class _RecordReader:
    """Projects index documents onto one field selection and pools nested containers across the records it reads. A pool key names child containers by `id()`, which keeps the keys shallow and is safe because the pool keeps every child it names. The pool stops growing at `_CONTAINER_POOL_LIMIT` entries, which bounds what it retains from records a streaming caller has already dropped."""

    def __init__(self, fields: Iterable[str] | None) -> None:
        selected = _INDEX_FIELD_SET if fields is None else frozenset(fields)
        unknown = selected - _INDEX_FIELD_SET
        if unknown:
            raise ValueError(f"Unknown unit-index fields: {', '.join(sorted(unknown))}")
        self.schema = {
            name: index for index, name in enumerate(name for name in INDEX_FIELDS if name in selected)
        }
        self.pool: dict[tuple, Any] = {}

    def _key(self, value: Any) -> tuple:
        if isinstance(value, (dict, list)):
            return (type(value), id(value))
        if isinstance(value, float):
            return (float, value.hex())
        return (type(value), value)

    def _value(self, value: Any) -> Any:
        if isinstance(value, str):
            return sys.intern(value)
        if isinstance(value, list):
            value = [self._value(item) for item in value]
            key = (list, *(self._key(item) for item in value))
        elif isinstance(value, dict):
            value = {sys.intern(name): self._value(item) for name, item in value.items()}
            key = (dict, *((name, self._key(item)) for name, item in value.items()))
        else:
            return value
        existing = self.pool.get(key)
        if existing is not None:
            return existing
        if len(self.pool) < _CONTAINER_POOL_LIMIT:
            self.pool[key] = value
        return value

    def record(self, document: Mapping[str, Any]) -> UnitRecord:
        return UnitRecord(tuple(self._value(document[name]) for name in self.schema), self.schema)


def manifest_sha256(surface: Path) -> str:
    """Return the manifest's identity: the sha256 of its parsed content with `ASSET_COMPONENTS` removed from `inputs_fingerprint`. Those components fingerprint the copied review UI assets, which no shard, sidecar, unit, or plumbing step reads, so they are outside the surface's identity and every hard freshness check. Every place that treats a component as soft reads `ASSET_COMPONENTS`. Removing them lets an assets refresh rewrite that one field in place and leave every sidecar, and the unit-cache store, stamped for the manifest they describe. A manifest that does not parse is hashed by its raw bytes, as `fingerprint._projected_digest` does, so a broken file mismatches instead of matching anything."""
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
    """Return the shard parts in the order every reader walks them: classes by `class_shard_key`, and each class's parts in the order its manifest lists them. The manifest is used instead of a glob over `units/` because only it says which parts belong to a class and in what order. The index is written in this order too, so a tool that breaks ties by first occurrence gets the same result from either source. A surface with no readable manifest has no shard parts."""
    surface = Path(surface)
    try:
        manifest = json.loads((surface / "manifest.json").read_text(encoding="utf-8"))
        classes = list(manifest["classes"])
    except OSError, ValueError, KeyError, TypeError:
        return []
    ordered = sorted(classes, key=lambda meta: class_shard_key(meta.get("id", "")))
    return [surface / part for meta in ordered for part in class_shards(meta)]


def index_line(fragment: dict, *, order: int | None = None, batch: int | None = None) -> bytes:
    """Return one index record as its line in the file. The build writes these lines to a spool as the fragments pass, so what `write_index` would hold in memory is on disk instead."""
    return (json.dumps(index_record(fragment, order=order, batch=batch), ensure_ascii=False) + "\n").encode()


def line_head(unit_id: str, order: int | None, batch: int | None) -> bytes:
    """Return the start of an index or app-index line, the id and the unit's place in the queue, as `json.dumps` writes it, without the closing brace. A line whose other fields did not change can be respooled by joining this to its tail."""
    return json.dumps({"id": unit_id, "order": order, "batch": batch}, ensure_ascii=False).encode()[:-1]


def respool_index_line(line: bytes, *, unit_id: str, order: int | None, batch: int | None) -> bytes:
    """Return a previous surface's index line for a served unit, with this surface's `order` and `batch`. Every field after those two is the fragment's own, and a unit served verbatim has an unchanged fragment, so the result is the id and the new place joined to the old tail, byte for byte what `index_line` writes for the same fragment."""
    return line_head(unit_id, order, batch) + line[line.index(CLASS_SEAM) :]


class LineCursor:
    """A forward-only reader over one of a surface's gzipped NDJSON files (a sidecar or the unit store) that returns the line whose leading `field` has a requested value, skipping every line before it. Each of these files is written in an order whose terms are all content-derived (shard order for the sidecars, triage order for the store), so a served unit keeps its relative place from one surface to the next, and the build reads the previous file once, in step with what it writes, without holding it. A skipped line belongs to a unit this build projects again. Once a requested value is not found, as with a file from another build or no file at all, the cursor returns None for every later request, and the caller projects the unit itself."""

    def __init__(self, path: Path, field: str = "id") -> None:
        self._field = field
        self._stream = None
        try:
            self._stream = gzip.open(path, "rb")
            next(self._stream)
        except OSError, EOFError, StopIteration:
            self.close()

    def take(self, value: str) -> bytes | None:
        if self._stream is None:
            return None
        prefix = f"{{{json.dumps(self._field)}: {json.dumps(value)}, ".encode()
        for line in self._stream:
            if line.startswith(prefix):
                return line
        self.close()
        return None

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def _manifest(surface: Path) -> dict:
    try:
        document = json.loads((Path(surface) / "manifest.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return document if isinstance(document, dict) else {}


def slot_reader(surface: Path):
    """Return a function that maps a fragment to its `order` and `batch` on this surface, reading the triage index once from the manifest beside it."""
    manifest = _manifest(surface)
    positions = human_positions(manifest)
    batch_size = manifest.get("batch_size")
    if not isinstance(batch_size, int) or batch_size <= 0:
        batch_size = 1

    def slot(fragment: Mapping[str, Any]) -> dict[str, int | None]:
        return workload_slot(positions, batch_size, fragment)

    return slot


def write_index_lines(surface: Path, lines: Iterable[bytes]) -> Path:
    """Write the index from already-projected lines in the order given, stamped with the manifest beside it, so this must run after the manifest is written. It uses gzip level 1 and a fixed mtime, like the unit store: the file is written once per cycle, and level 9's extra seconds cost more than the megabytes it saves."""
    header = {"format": INDEX_FORMAT, "manifest_sha256": manifest_sha256(surface)}
    path = index_path(surface)
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
            stream.write((json.dumps(header) + "\n").encode())
            for line in lines:
                stream.write(line)
    return path


def write_index(surface: Path, shards: Iterable[tuple[str, list[dict]]]) -> Path:
    """Write the index from fragments the caller holds in memory. `shards` is (class id, fragments) pairs in any order. The file is written in shard-path order, with each unit's place in the queue read from the manifest beside it, so this is `write_index_lines` over the same projection the build spools one fragment at a time, and both write the same bytes."""
    ordered = sorted(shards, key=lambda item: class_shard_key(item[0]))
    slot = slot_reader(surface)
    return write_index_lines(
        surface,
        (
            index_line(fragment, **slot(fragment))
            for _class_id, fragments in ordered
            for fragment in fragments
        ),
    )


def index_header(surface: Path) -> dict | None:
    """Return the index's header line, or None when there is none to read. It is separate from `load_index` so the build's output check can confirm the file exists and is stamped for the manifest beside it without parsing every record."""
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


def load_index(surface: Path, *, fields: Iterable[str] | None = None) -> list[UnitRecord] | None:
    """Return the index's records, or None when there is no usable index: absent, unreadable, a different format, or stamped for a manifest other than the one on disk. A None costs the caller one pass over the shards, so over-invalidation is the safe direction here too."""
    reader = _RecordReader(fields)
    if not index_is_current(surface):
        return None
    try:
        with gzip.open(index_path(surface), "rt", encoding="utf-8") as stream:
            next(stream)
            return [reader.record(json.loads(line)) for line in stream]
    except OSError, EOFError, ValueError, StopIteration:
        return None


def iter_shard_fragments(surface: Path) -> Iterator[dict]:
    """Yield the shards' own fragments, reading one part at a time so that only one part is in memory. The corpus is gigabytes and no part is larger than `build.SHARD_PART_BYTES`, so this bound matters: reading every part at once would hold the whole corpus."""
    for path in shard_paths(surface):
        shard = json.loads(path.read_text(encoding="utf-8"))
        yield from shard


def stream_shards(surface: Path, *, fields: Iterable[str] | None = None) -> Iterator[UnitRecord]:
    """Yield the shards' fragments projected as the index would hold them, with each unit's place in the queue read from the manifest. This is the fallback when the index is missing or stale."""
    reader = _RecordReader(fields)
    return _stream_shards(surface, reader)


def _stream_shards(surface: Path, reader: _RecordReader) -> Iterator[UnitRecord]:
    slot = slot_reader(surface)
    for fragment in iter_shard_fragments(surface):
        yield reader.record(index_record(fragment, **slot(fragment)))


def iter_units(surface: Path, *, fields: Iterable[str] | None = None) -> Iterator[UnitRecord]:
    """Yield every unit on a surface, projected, one at a time: from the index when it is present and stamped for this manifest, from the shards otherwise. A caller that keeps only part of the corpus should read it this way instead of through `load_units`, so the records it drops are never in memory with the ones it keeps. `load_human_units` does this for the plumbing's human units, and classifies each index line with a byte test on its head instead of parsing every record."""
    reader = _RecordReader(fields)
    return _iter_units(surface, reader)


def _iter_units(surface: Path, reader: _RecordReader) -> Iterator[UnitRecord]:
    if index_is_current(surface):
        with gzip.open(index_path(surface), "rt", encoding="utf-8") as stream:
            next(stream)
            for line in stream:
                yield reader.record(json.loads(line))
        return
    yield from _stream_shards(surface, reader)


def load_units(surface: Path, *, fields: Iterable[str] | None = None) -> list[UnitRecord]:
    """Every unit on a surface, projected. The index when it is there and stamped for this manifest; the shards otherwise."""
    fields = None if fields is None else tuple(fields)
    records = load_index(surface, fields=fields)
    if records is not None:
        return records
    return list(stream_shards(surface, fields=fields))


def iter_human_units(
    surface: Path, *, unit_ids: set[str] | None = None, fields: Iterable[str] | None = None
) -> Iterator[UnitRecord]:
    """Yield the human units' index records in shard order. When `unit_ids` is given, every unit id on the surface is added to it during the same walk; the set is complete only once the iterator is exhausted. Machine units' lines contribute only their heads and are not parsed as JSON. An absent or stale index falls back to the projected shards, which classify each unit through `workload_slot`. A corrupt current index raises, because restarting a partly consumed stream from the shards would yield units twice."""
    reader = _RecordReader(fields)
    return _iter_human_units(surface, reader, unit_ids)


def _iter_human_units(
    surface: Path, reader: _RecordReader, unit_ids: set[str] | None
) -> Iterator[UnitRecord]:
    if index_is_current(surface):
        yield from _stream_human_index(surface, reader, unit_ids)
        return
    yield from _stream_human_shards(surface, reader, unit_ids)


def _stream_human_index(
    surface: Path, reader: _RecordReader, unit_ids: set[str] | None
) -> Iterator[UnitRecord]:
    with gzip.open(index_path(surface), "rb") as stream:
        next(stream)
        for line in stream:
            head = line[: line.index(CLASS_SEAM)]
            if unit_ids is not None:
                unit_ids.add(head[len(ID_OPEN) : head.index(ORDER_SEAM)].decode())
            if not head.endswith(MACHINE_TAIL):
                yield reader.record(json.loads(line))


def _stream_human_shards(
    surface: Path, reader: _RecordReader, unit_ids: set[str] | None
) -> Iterator[UnitRecord]:
    slot = slot_reader(surface)
    for fragment in iter_shard_fragments(surface):
        if unit_ids is not None:
            unit_ids.add(fragment["id"])
        workload = slot(fragment)
        if workload["batch"] is not None:
            yield reader.record(index_record(fragment, **workload))


def load_human_units(
    surface: Path, *, fields: Iterable[str] | None = None
) -> tuple[list[UnitRecord], set[str]]:
    """Return the human units' records on a surface (the units the manifest's triage index holds, with `batch` not None) and the id of every unit on it, machine units included. The plumbing's consumers read the human records and only the id of a machine record: carry's stranded count and the complaint docket's absent-unit warning check prior verdicts against every id on the surface, which is why the id set is returned. Over a current index the classification is a byte test: `index_record` starts every record with `id`, `order`, and `batch` in that order (rebuild/test_unit_index.py checks the order), so a line's head up to `CLASS_SEAM` ends with `MACHINE_TAIL` exactly when the unit is outside the index, and only the human lines are parsed. An index that fails partway through is discarded and the shards are read instead, as `load_index` does. That fallback parses every shard part, so a corrupt index costs a full parse of the corpus."""
    reader = _RecordReader(fields)
    ids: set[str] = set()
    if index_is_current(surface):
        try:
            return list(_stream_human_index(surface, reader, ids)), ids
        except OSError, EOFError, ValueError, StopIteration:
            ids = set()
            reader = _RecordReader(reader.schema)
    return list(_stream_human_shards(surface, reader, ids)), ids
