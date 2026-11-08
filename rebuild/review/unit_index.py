"""Writes and reads the corpus's per-unit index, `units-index.ndjson.gz`: one record per unit with the fields the verdict update reads, written beside the manifest.

The shards are the authority, and this file is a projection of them. It exists because the verdict-update tools (carry, duplicate fill, standing fill, the complaint list) and the review-session preparation tools (the review queue snapshot, the novelty order) each read a few fields per unit. Reading those from the shards would mean parsing gigabytes of JSON once per tool per cycle, much of it `explain` and `drafts`, of which the index keeps only the policy draft's file, key path, and suggested record. `index_record` defines the projection. rebuild/test_unit_index.py checks it against the fixture shards field for field and pins the field set. A field a tool needs must be added here: `UnitRecord.get` returns the default for a name outside `INDEX_FIELDS`, so a missing field would silently read as absent.

Two fields are counts, because counting is all any reader does with them: `render_groups` is the number of render groups (standing fill requires exactly one) and `secondary_junctions` the number of secondary junctions (standing fill requires none). Two fields come from the manifest instead of the shard: `order` is the unit's position in the manifest's triage index (`human_unit_ids`), and `batch` is the `batch_size` slice that position falls in. Both are null for a unit outside the index. A fragment carries neither, because a unit's place in the queue is not a property of the unit, and every reader derives them through `human_positions`. Every other field is the shard's own value.

The file is stamped with the manifest's identity digest (`manifest_sha256`), as the unit store is, so an index a crashed build did not rewrite is not read as describing the shards beside it. A reader that finds no index, or one stamped for another manifest, falls back to reading the shards through the same projection.

This module also holds the one reader of fragments out of shard parts, the live-fragment iterator (`LiveCorpus`), and the rule a from-scratch build cuts a class's parts by (`PartLayout`). A class's parts are its base parts, which a from-scratch build, a fold or a direct write lays out in id order, and after them its overflow parts (`units/<class>.x<NNN>.json`), to which a build appends each fragment that changed, leaving its old slot dead (`build._ShardWriter`). So a part can hold a fragment no unit owns any more, and a unit's fragment can sit in a later part than its id order puts it in. The iterator reads only live fragments, each class's in id order, and the first of these that describes the shards decides which they are: where the corpus has a store head, the unit store's records, the fragment at the address each unit's record gives (`_store_slots`, which reads the store's lines itself, with the slicers `unit_cache` reads them with too, so this module imports nothing of the corpus build); then the index, where it is current and reads to its end, each id it lists taken from its last occurrence in manifest order, since overflow parts follow the base parts and an append follows every older copy; and only for a corpus with no store head, no such index and no overflow part, the parts themselves, walked part by part with every fragment taken as live, as a from-scratch build lays them out (a build puts a store under a head before it appends, `unit_cache.adopt_single_file`, so only a head deleted by hand leaves an appended corpus without one). A corpus with a head or an overflow part that neither the store nor the index describes is refused (`LayoutError`). `rebuild/test_unit_index.py`'s contract test fails on any read of shard parts, through `class_shards`, `shard_paths` or `_walk_elements`, outside the iterator and the readers of part names it allows, each named there with its reason.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
from array import array
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from rebuild.pipeline import row_delta

INDEX_NAME = "units-index.ndjson.gz"
STORE_NAME = "unit-cache.ndjson.gz"
STORE_FORMAT = "ams-review-unit-cache/9"
STORE_KEY_PREFIX = b'{"key": "'
_ID_START = len(STORE_KEY_PREFIX) + 64 + len(b'", "id": "')
_CLASS_SEAM = b'", "class": '
_CONTENT_KEY_SEAM = b', "content_key": '
_ADDRESS_SEAM = b', "address": '
# The cap on one shard part's bytes. The app reads each byte range it fetches, which never crosses a part, as one JavaScript string, and V8's `String::kMaxLength` on a 64-bit build is 2**29 - 24 characters, a byte each in this ASCII-only JSON. When Blink cannot build a body that long, `Response.text()` resolves to an empty string instead of an error, so every fragment in an oversized range fails to parse after a fetch that appeared to succeed. This is half that limit, and the other half is headroom; whole-part readers keep their memory bound by it too.
SHARD_PART_BYTES = 1 << 28
PART_CLOSING = b"\n]\n"
SHARD_ORDER = "shard"
MANIFEST_ORDER = "manifest"
INDEX_FORMAT = "ams-review-unit-index/1"
ASSET_COMPONENTS: tuple[str, ...] = ("static",)
ID_OPEN = b'{"id": "'
ORDER_SEAM = b'", "order": '
CLASS_SEAM = b', "class": '
MACHINE_TAIL = b'"batch": null'


def index_path(corpus: Path) -> Path:
    return Path(corpus) / INDEX_NAME


def class_shard_key(class_id: str) -> str:
    """Return the key every walk of the corpus sorts classes by. `write_index` sorts by it too, so the index's records and a shard walk are in the same order. A class written as numbered parts sorts where its bare form would, because the character after the class id is `.` in both forms."""
    return f"{class_id}.json"


def class_shards(meta: Mapping[str, Any]) -> list[str]:
    """Return one manifest class entry's shard parts, in part order. An `ams-review-manifest/1` entry has a single `shard` string instead of the `shards` list. Both forms must be read, because the unit cache reads the prior corpus's manifest, which keeps the older form until that corpus is rebuilt."""
    shards = meta.get("shards")
    if shards is None:
        return [str(meta["shard"])]
    return [str(part) for part in shards]


def human_positions(manifest: Mapping[str, Any]) -> dict[str, int]:
    """Return each human unit's position in the manifest's triage index, keyed by id. This is the only source of a unit's `order`; its batch is that position divided by `batch_size`, computed inline by `human_unit_slot` and by `audit.batch_of` elsewhere. A manifest without the index puts every unit outside it; every manifest this build writes has one."""
    return {unit_id: position for position, unit_id in enumerate(manifest.get("human_unit_ids") or ())}


def human_unit_slot(
    positions: Mapping[str, int], batch_size: int, fragment: Mapping[str, Any]
) -> dict[str, int | None]:
    """Return a unit's `order` and `batch` as the index gives them, or None for both when the index does not hold the unit. The exception is a fragment that carries its own `batch`, which only a fragment from an older corpus does: that batch is returned with `order` None, since such a corpus paged in id order and its index, where it has one, gives the same batch."""
    order = positions.get(fragment["id"])
    if order is not None:
        return {"order": order, "batch": order // batch_size}
    return {"order": None, "batch": fragment.get("batch") if "batch" in fragment else None}


def index_record(fragment: dict, *, order: int | None = None, batch: int | None = None) -> dict:
    """Project one shard fragment onto the fields the verdict update reads, with the unit's place in the manifest's triage index passed in. The key order is fixed so that two builds of the same corpus write the same bytes, and so that every line starts with `id`, `order`, and `batch` at the delimiters `ID_OPEN`, `ORDER_SEAM`, and `CLASS_SEAM`. `copy_forward_index_line` joins a new head onto a line there, and `load_human_units` reads the id and the unit's place in the queue from the head without parsing the line. rebuild/test_unit_index.py checks this opening."""
    before = fragment.get("before") or {}
    after = fragment.get("after") or {}
    policy = (fragment.get("drafts") or {}).get("policy")
    return {
        "id": fragment["id"],
        "order": order,
        "batch": batch,
        "class": fragment.get("class"),
        "cluster": fragment.get("cluster"),
        "duplicate_group": fragment.get("duplicate_group"),
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
        "secondary_junctions": len(fragment.get("secondary_junctions") or []),
        "before": {"glyphs": before.get("glyphs") or [], "junctions": before.get("junctions") or []},
        "after": {"cells": after.get("cells") or [], "junctions": after.get("junctions") or []},
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


def manifest_sha256(corpus: Path) -> str:
    """Return the manifest's identity: the sha256 of its parsed content with `ASSET_COMPONENTS` removed from `inputs_fingerprint`. Those components fingerprint the copied review UI assets, which no shard, sidecar, unit, or step of the verdict update reads, so they are outside the corpus's identity and every hard freshness check. Every place that treats a component as soft reads `ASSET_COMPONENTS`. Removing them lets an assets refresh rewrite that one field in place and leave every sidecar, and the unit-cache store, stamped for the manifest they describe. A manifest that does not parse is hashed by its raw bytes, as `fingerprint._projected_digest` does, so a broken file mismatches instead of matching anything."""
    raw = (Path(corpus) / "manifest.json").read_bytes()
    try:
        document = json.loads(raw)
    except ValueError:
        return hashlib.sha256(raw).hexdigest()
    return manifest_identity(document)


def manifest_identity(document: Any) -> str:
    """Return `manifest_sha256` of a parsed manifest."""
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


def shard_paths(corpus: Path) -> list[Path]:
    """Return the shard parts in shard order: classes by `class_shard_key`, and each class's parts in the order its manifest lists them, base parts then overflow parts. The manifest is used instead of a glob over `units/` because only it says which parts belong to a class and in what order. The index is written in this order too, and every reader orders by the store or the index (`LiveCorpus`). A corpus with no readable manifest has no shard parts."""
    corpus = Path(corpus)
    try:
        manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
        classes = list(manifest["classes"])
    except OSError, ValueError, KeyError, TypeError:
        return []
    ordered = sorted(classes, key=lambda meta: class_shard_key(meta.get("id", "")))
    return [corpus / part for meta in ordered for part in class_shards(meta)]


def index_line(fragment: dict, *, order: int | None = None, batch: int | None = None) -> bytes:
    """Return one index record as its line in the file. The build compresses these lines in memory as the fragments pass (`build._SidecarLines`), so it holds the compressed lines, not the fragments `write_index` holds."""
    return (json.dumps(index_record(fragment, order=order, batch=batch), ensure_ascii=False) + "\n").encode()


def line_head(unit_id: str, order: int | None, batch: int | None) -> bytes:
    """Return the start of an index or app-index line, the id and the unit's place in the queue, as `json.dumps` writes it, without the closing brace. A line whose other fields did not change can be copied forward by joining this to its tail."""
    return json.dumps({"id": unit_id, "order": order, "batch": batch}, ensure_ascii=False).encode()[:-1]


def copy_forward_index_line(line: bytes, *, unit_id: str, order: int | None, batch: int | None) -> bytes:
    """Return a previous corpus's index line for a cached unit, with this corpus's `order` and `batch`. Every field after those two is the fragment's own, and a byte-copied unit has an unchanged fragment, so the result is the id and the new place joined to the old tail, byte for byte what `index_line` writes for the same fragment."""
    return line_head(unit_id, order, batch) + line[line.index(CLASS_SEAM) :]


class LineCursor:
    """A forward-only reader over one of a corpus's gzipped NDJSON sidecars that returns the line whose leading `field` has a requested value, skipping every line before it. Each sidecar is written in shard order, whose terms are all content-derived, so a cached unit keeps its relative place from one corpus to the next, and the build reads the previous file once, in step with what it writes, without holding it. A skipped line belongs to a unit this build projects again. Once a requested value is not found, as with a file from another build or no file at all, the cursor returns None for every later request, and the caller projects the unit itself."""

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


def _manifest(corpus: Path) -> dict:
    try:
        document = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return document if isinstance(document, dict) else {}


def slot_reader(corpus: Path):
    """Return a function that maps a fragment to its `order` and `batch` on this corpus, reading the triage index once from the manifest beside it."""
    manifest = _manifest(corpus)
    positions = human_positions(manifest)
    batch_size = manifest.get("batch_size")
    if not isinstance(batch_size, int) or batch_size <= 0:
        batch_size = 1

    def slot(fragment: Mapping[str, Any]) -> dict[str, int | None]:
        return human_unit_slot(positions, batch_size, fragment)

    return slot


def write_index_lines(corpus: Path, lines: Iterable[bytes]) -> Path:
    """Write the index from already-projected lines in the order given, stamped with the manifest beside it, so this must run after the manifest is written. It uses gzip level 1 and a fixed mtime, like the unit store: the file is written once per cycle, and level 9's extra seconds cost more than the megabytes it saves. The file is written under a sibling `.partial` name and renamed at the end, so a write that raises or is killed partway leaves the previous index whole. A write that raises also removes the staging file; a killed build leaves it behind, and the next write reopens the staging name and overwrites it. A truncated gzip still yields its header line, so `index_is_current` would accept a truncated index at the final path and every reader past the cut would raise `EOFError`. The gzip header records the final file name, since `GzipFile` would otherwise write the staging handle's name into the bytes."""
    header = {"format": INDEX_FORMAT, "manifest_sha256": manifest_sha256(corpus)}
    path = index_path(corpus)
    staging = path.with_name(path.name + ".partial")
    try:
        with open(staging, "wb") as handle:
            with gzip.GzipFile(
                filename=path.name, fileobj=handle, mode="wb", mtime=0, compresslevel=1
            ) as stream:
                stream.write((json.dumps(header) + "\n").encode())
                for line in lines:
                    stream.write(line)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)
    return path


def write_index(corpus: Path, shards: Iterable[tuple[str, list[dict]]]) -> Path:
    """Write the index from fragments the caller holds in memory. `shards` is (class id, fragments) pairs in any order. The file is written in shard-path order, with each unit's place in the queue read from the manifest beside it, so this is `write_index_lines` over the same projection the build makes one fragment at a time, and both write the same bytes."""
    ordered = sorted(shards, key=lambda item: class_shard_key(item[0]))
    slot = slot_reader(corpus)
    return write_index_lines(
        corpus,
        (
            index_line(fragment, **slot(fragment))
            for _class_id, fragments in ordered
            for fragment in fragments
        ),
    )


def index_header(corpus: Path) -> dict | None:
    """Return the index's header line, or None when there is none to read. It is separate from `load_index` so the build's output check can confirm the file exists and is stamped for the manifest beside it without parsing every record."""
    path = index_path(corpus)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
    except OSError, EOFError, ValueError, StopIteration:
        return None
    return header if isinstance(header, dict) else None


def index_is_current(corpus: Path) -> bool:
    """Whether the index beside this manifest describes it: present, in a format this reader knows, and stamped with the manifest's own identity."""
    header = index_header(corpus)
    if header is None or header.get("format") != INDEX_FORMAT:
        return False
    try:
        return header.get("manifest_sha256") == manifest_sha256(corpus)
    except OSError:
        return False


def load_index(corpus: Path, *, fields: Iterable[str] | None = None) -> list[UnitRecord] | None:
    """Return the index's records, or None when there is no usable index: absent, unreadable, a different format, or stamped for a manifest other than the one on disk. A None costs the caller one pass over the shards, so over-invalidation is the safe direction here too."""
    reader = _RecordReader(fields)
    if not index_is_current(corpus):
        return None
    try:
        with gzip.open(index_path(corpus), "rt", encoding="utf-8") as stream:
            next(stream)
            return [reader.record(json.loads(line)) for line in stream]
    except OSError, EOFError, ValueError, StopIteration:
        return None


def iter_shard_fragments(corpus: Path) -> Iterator[dict]:
    """Yield the shards' live fragments in shard order through the live-fragment iterator (`LiveCorpus`), one at a time."""
    with LiveCorpus(corpus) as live:
        for _meta, _slot, fragment in live.fragments():
            yield fragment


def stream_shards(corpus: Path, *, fields: Iterable[str] | None = None) -> Iterator[UnitRecord]:
    """Yield the shards' fragments projected as the index would hold them, with each unit's place in the queue read from the manifest. This is the fallback when the index is missing or stale."""
    reader = _RecordReader(fields)
    return _stream_shards(corpus, reader)


def _stream_shards(corpus: Path, reader: _RecordReader) -> Iterator[UnitRecord]:
    slot = slot_reader(corpus)
    for fragment in iter_shard_fragments(corpus):
        yield reader.record(index_record(fragment, **slot(fragment)))


def iter_units(corpus: Path, *, fields: Iterable[str] | None = None) -> Iterator[UnitRecord]:
    """Yield every unit on a corpus, projected, one at a time: from the index when it is present and stamped for this manifest, from the shards otherwise. A caller that keeps only part of the corpus should read it this way instead of through `load_units`, so the records it drops are never in memory with the ones it keeps. `load_human_units` does this for the verdict update's human units, and classifies each index line with a byte test on its head instead of parsing every record."""
    reader = _RecordReader(fields)
    return _iter_units(corpus, reader)


def _iter_units(corpus: Path, reader: _RecordReader) -> Iterator[UnitRecord]:
    if index_is_current(corpus):
        with gzip.open(index_path(corpus), "rt", encoding="utf-8") as stream:
            next(stream)
            for line in stream:
                yield reader.record(json.loads(line))
        return
    yield from _stream_shards(corpus, reader)


def load_units(corpus: Path, *, fields: Iterable[str] | None = None) -> list[UnitRecord]:
    """Every unit on a corpus, projected. The index when it is there and stamped for this manifest; the shards otherwise."""
    fields = None if fields is None else tuple(fields)
    records = load_index(corpus, fields=fields)
    if records is not None:
        return records
    return list(stream_shards(corpus, fields=fields))


def iter_human_units(
    corpus: Path, *, unit_ids: set[str] | None = None, fields: Iterable[str] | None = None
) -> Iterator[UnitRecord]:
    """Yield the human units' index records in shard order. When `unit_ids` is given, every unit id on the corpus is added to it during the same walk; the set is complete only once the iterator is exhausted. Machine units' lines contribute only their heads and are not parsed as JSON. An absent or stale index falls back to the projected shards, which classify each unit through `human_unit_slot`. A corrupt current index raises, because restarting a partly consumed stream from the shards would yield units twice."""
    reader = _RecordReader(fields)
    return _iter_human_units(corpus, reader, unit_ids)


def _iter_human_units(corpus: Path, reader: _RecordReader, unit_ids: set[str] | None) -> Iterator[UnitRecord]:
    if index_is_current(corpus):
        yield from _stream_human_index(corpus, reader, unit_ids)
        return
    yield from _stream_human_shards(corpus, reader, unit_ids)


def _stream_human_index(
    corpus: Path, reader: _RecordReader, unit_ids: set[str] | None
) -> Iterator[UnitRecord]:
    with gzip.open(index_path(corpus), "rb") as stream:
        next(stream)
        for line in stream:
            head = line[: line.index(CLASS_SEAM)]
            if unit_ids is not None:
                unit_ids.add(head[len(ID_OPEN) : head.index(ORDER_SEAM)].decode())
            if not head.endswith(MACHINE_TAIL):
                yield reader.record(json.loads(line))


def _stream_human_shards(
    corpus: Path, reader: _RecordReader, unit_ids: set[str] | None
) -> Iterator[UnitRecord]:
    slot = slot_reader(corpus)
    for fragment in iter_shard_fragments(corpus):
        if unit_ids is not None:
            unit_ids.add(fragment["id"])
        place = slot(fragment)
        if place["batch"] is not None:
            yield reader.record(index_record(fragment, **place))


def load_human_units(
    corpus: Path, *, fields: Iterable[str] | None = None
) -> tuple[list[UnitRecord], set[str]]:
    """Return the human units' records on a corpus (the units the manifest's triage index holds, with `batch` not None) and the id of every unit on it, machine units included. The verdict update's consumers read the human records and only the id of a machine record: carry's orphaned count and the complaint list's absent-unit warning check prior verdicts against every id on the corpus, which is why the id set is returned. Over a current index the classification is a byte test: `index_record` starts every record with `id`, `order`, and `batch` in that order (rebuild/test_unit_index.py checks the order), so a line's head up to `CLASS_SEAM` ends with `MACHINE_TAIL` exactly when the unit is outside the index, and only the human lines are parsed. An index that fails partway through is discarded and the shards are read instead, as `load_index` does. That fallback parses every shard part, so a corrupt index costs a full parse of the corpus."""
    reader = _RecordReader(fields)
    ids: set[str] = set()
    if index_is_current(corpus):
        try:
            return list(_stream_human_index(corpus, reader, ids)), ids
        except OSError, EOFError, ValueError, StopIteration:
            ids = set()
            reader = _RecordReader(reader.schema)
    return list(_stream_human_shards(corpus, reader, ids)), ids


def base_part_names(class_id: str, count: int) -> list[str]:
    """Return the names a from-scratch build gives a class's `count` base parts: the bare `units/<class>.json` for one, and `units/<class>.000.json` onward for more, never a bare name beside numbered ones. Both forms sort where `class_shard_key` puts the class, because the character after the class id is `.` in both."""
    if count == 1:
        return [f"units/{class_id}.json"]
    return [f"units/{class_id}.{index:03d}.json" for index in range(count)]


def overflow_part_name(class_id: str, index: int) -> str:
    """Return the name of a class's overflow part `index`, `units/<class>.x<NNN>.json`, numbered from `x000`."""
    return f"units/{class_id}.x{index:03d}.json"


def overflow_index(class_id: str, part: str) -> int | None:
    """Return the number of `part` if it is one of the class's overflow parts, else None."""
    match = re.fullmatch(rf"units/{re.escape(class_id)}\.x(\d{{3,}})\.json", part)
    return int(match.group(1)) if match else None


class PartLayout:
    """The rule a from-scratch build cuts one class's fragments into parts by, which `build._ShardWriter` writes a base by and the materialized form of the corpus is laid out by. Fragments arrive in id order; `place` returns the part index, the byte start and whether the fragment opens its part (else a comma precedes it). A part is `[`, its fragments joined by commas, and `PART_CLOSING`, so its bytes equal `json.dumps(part, indent=1, ensure_ascii=True) + "\\n"` under the shard writer's framing, and a class with no fragment is one part, `[]\\n`. A part closes before the fragment that would take it past `SHARD_PART_BYTES`, so every part stays within the cap but one holding a single fragment that alone exceeds it. `sizes` closes the layout and returns each part's size."""

    def __init__(self, cap: int | None = None) -> None:
        self._cap = SHARD_PART_BYTES if cap is None else cap
        self._sizes: list[int] = []
        self._open = False

    def place(self, length: int) -> tuple[int, int, bool]:
        if self._open and self._sizes[-1] + 1 + length + len(PART_CLOSING) > self._cap:
            self._sizes[-1] += len(PART_CLOSING)
            self._open = False
        if not self._open:
            self._sizes.append(1 + length)
            self._open = True
            return len(self._sizes) - 1, 1, True
        start = self._sizes[-1] + 1
        self._sizes[-1] = start + length
        return len(self._sizes) - 1, start, False

    def sizes(self) -> list[int]:
        if self._open:
            self._sizes[-1] += len(PART_CLOSING)
            self._open = False
        if not self._sizes:
            self._sizes.append(len(b"[]\n"))
        return list(self._sizes)


class LayoutError(ValueError):
    """Raised when a corpus's live fragments cannot be told from its dead slots: it has a unit store head or lists an overflow part, and neither the store nor the index describes the shards beside it."""


@dataclass(frozen=True, slots=True)
class LiveSlot:
    """Where one live fragment lies: its unit's id, the part (as the manifest names it), and the byte start and length of its element there, the `(part, start, length)` the app's sidecars and the unit store's records use."""

    unit_id: str
    part: str
    start: int
    length: int


class ClassSlots:
    """One class's live slots as columns, ids as their ASCII bytes padded with NUL to a unit id's width (which sorts a shorter id first, as a string does), so a corpus's worth of addresses costs a few dozen bytes a unit rather than an object each. `ordered` sorts them by id, which is the order every reader takes them in."""

    ID_WIDTH = 13

    def __init__(self, parts: list[str]) -> None:
        self.parts = parts
        self._index = {part: index for index, part in enumerate(parts)}
        self._ids = bytearray()
        self._part = array("H")
        self._start = array("Q")
        self._length = array("I")

    def __len__(self) -> int:
        return len(self._part)

    def add(self, unit_id: str, part: str, start: int, length: int) -> None:
        encoded = unit_id.encode("ascii")
        if len(encoded) > self.ID_WIDTH or b"\0" in encoded:
            raise LayoutError(f"{unit_id!r} is not a unit id")
        encoded = encoded.ljust(self.ID_WIDTH, b"\0")
        index = self._index.get(part)
        if index is None:
            raise LayoutError(f"{unit_id} is addressed in {part}, which its class does not list")
        self._ids += encoded
        self._part.append(index)
        self._start.append(start)
        self._length.append(length)

    def ordered(self) -> list[LiveSlot]:
        width = self.ID_WIDTH
        ids = self._ids
        order = sorted(range(len(self)), key=lambda index: ids[index * width : (index + 1) * width])
        slots = []
        previous = None
        for index in order:
            unit_id = ids[index * width : (index + 1) * width].rstrip(b"\0").decode("ascii")
            if unit_id == previous:
                raise LayoutError(f"{unit_id} is live twice in one class")
            previous = unit_id
            slots.append(
                LiveSlot(unit_id, self.parts[self._part[index]], self._start[index], self._length[index])
            )
        return slots


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def _walk_elements(text: str):
    """Yield each element of one shard part with the character offset and length of its text. It reads the `[`, then calls `raw_decode` once per element, skipping the commas and whitespace between them, so each fragment is parsed and released before the next and the part is never held as objects all at once. It does not depend on the shard writer's framing; a compact `json.dumps` of the same list is read the same way. A part that is not a JSON array raises ValueError, which the caller treats as unreadable."""
    decoder = json.JSONDecoder()
    index = _skip_whitespace(text, 0)
    if index >= len(text) or text[index] != "[":
        raise ValueError("a shard part is a JSON array")
    index = _skip_whitespace(text, index + 1)
    while index < len(text) and text[index] != "]":
        fragment, end = decoder.raw_decode(text, index)
        yield index, end - index, fragment
        index = _skip_whitespace(text, end)
        if index < len(text) and text[index] == ",":
            index = _skip_whitespace(text, index + 1)


def _walk_part(path: Path) -> Iterator[tuple[int, int, dict]]:
    """Walk one part's elements (`_walk_elements`) with byte offsets: a part that is not pure ASCII yields nothing, since its character offsets are not byte offsets, and every part a build writes is ASCII."""
    raw = path.read_bytes()
    if not raw.isascii():
        return
    text = raw.decode("ascii")
    del raw
    yield from _walk_elements(text)


def _index_classes(corpus: Path) -> dict[str, set[str]]:
    """Return each class's live ids as the current index lists them: each line's id and class, read from its head (`index_record` writes `id`, `order`, `batch` and `class` first) without parsing the rest. An index that does not read to its end raises `LayoutError`."""
    classes: dict[str, set[str]] = {}
    try:
        with gzip.open(index_path(corpus), "rb") as stream:
            next(stream)
            for line in stream:
                head = line[: line.index(b', "cluster": ')]
                record = json.loads(head + b"}")
                classes.setdefault(record["class"], set()).add(record["id"])
    except (OSError, EOFError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise LayoutError(f"the index does not give the live fragments: {error!r}") from error
    return classes


def record_id(line: bytes) -> str:
    """Return a unit store line's unit id, sliced from where `to_record` writes it, after the input key."""
    return line[_ID_START : line.index(b'"', _ID_START)].decode("ascii")


def record_class(line: bytes) -> str:
    """Return a unit store line's class, sliced from where `to_record` writes it, after the unit id."""
    start = line.index(_CLASS_SEAM, _ID_START) + len(_CLASS_SEAM)
    return json.loads(line[start : line.index(_CONTENT_KEY_SEAM, start)])


def record_address(line: bytes) -> tuple[str, int, int] | None:
    """Return a unit store line's address, sliced from its end, where `to_record` writes it, or None for a record that has none."""
    value = json.loads(line[line.rindex(_ADDRESS_SEAM) + len(_ADDRESS_SEAM) :].rstrip()[:-1])
    if value is None:
        return None
    part, start, length = value
    return str(part), int(start), int(length)


def store_base_lines(handle: BinaryIO) -> Iterator[bytes]:
    """Yield a unit store base's record lines from an open handle, from its start, past its header line."""
    handle.seek(0)
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        next(stream)
        yield from stream


def store_lines(
    head: row_delta.Head | None, handles: list[BinaryIO]
) -> Iterator[tuple[row_delta.Place, bytes]]:
    """Yield a unit store's materialized record lines in order, each with its place: the base's alone for a store with no segment or no head, else the base and the segments `head` lists merged as streams by place (`row_delta.merge_placed`). `handles` are the parts as `row_delta.open_parts` opened them, the base first."""
    if head is None or not head.segments:
        for index, line in enumerate(store_base_lines(handles[0])):
            yield (index, ""), line
        return
    generation = head.generation
    segments = [
        (lambda handle=handle, number=number: row_delta.segment_rows(handle, STORE_NAME, generation, number))
        for number, handle in enumerate(handles[1:], start=1)
    ]
    yield from row_delta.merge_placed(lambda: store_base_lines(handles[0]), segments)


def _store_slots(corpus: Path, manifest: Mapping[str, Any]) -> dict[str, ClassSlots]:
    """Return every class's live slots as the unit store's records give them: each record's unit id at the address it names, under the class whose parts list that address's part. The store must have a head and describe the shards beside it: its format this one, its manifest stamp the manifest's identity, and its recorded part sizes every part the manifest lists, at the size each has on disk. A store that does not, or a record with no address or an address outside the parts of the class it records, such as a dead slot the unit left when it changed class, raises `LayoutError`, and the iterator falls back to the index."""
    try:
        head = row_delta.read_head(corpus, STORE_NAME)
        if head is None:
            raise LayoutError("no unit store head")
        stamps = head.stamps
        if stamps.get("format") != STORE_FORMAT or stamps.get("manifest_sha256") != manifest_sha256(corpus):
            raise LayoutError("the unit store describes another manifest")
        classes = list(manifest.get("classes") or ())
        part_class = {part: meta["id"] for meta in classes for part in class_shards(meta)}
        recorded = stamps.get("parts") or {}
        if set(recorded) != set(part_class) or any(
            (corpus / part).stat().st_size != recorded[part] for part in part_class
        ):
            raise LayoutError("a shard part is not the one the unit store records")
        slots: dict[str, ClassSlots] = {str(meta["id"]): ClassSlots(class_shards(meta)) for meta in classes}
        with row_delta.open_parts(corpus, head) as opened:
            for _place, line in store_lines(head, opened.handles):
                address = record_address(line)
                unit_id = record_id(line)
                class_id = None if address is None else part_class.get(address[0])
                if address is None or class_id is None or record_class(line) != class_id:
                    raise LayoutError(f"{unit_id} is not addressed in a part its class lists")
                slots[class_id].add(unit_id, *address)
        return slots
    except LayoutError:
        raise
    except (OSError, EOFError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise LayoutError(f"the unit store does not give the live fragments: {error!r}") from error


class LiveCorpus:
    """The live-fragment iterator: the one reader of fragments out of a corpus's shard parts (see the module docstring for what makes a fragment live). Open it over a corpus, take its classes in shard order (`class_shard_key`, the order of the index and the sidecars) or manifest order, and each class's live slots in id order (`slots`), their bytes (`read`), or the fragments parsed (`fragments`). `source` says what drives it: `store`, `index` or `parts`, the last for a corpus with no store head, no current index that reads to its end and no overflow part, whose every fragment it takes as live. Reads go through one descriptor a part, kept open until `close`, so a later writer's renames cannot reach a reader mid-walk."""

    def __init__(self, corpus: Path) -> None:
        self.corpus = Path(corpus)
        self.manifest = _manifest(self.corpus)
        self.metas: list[dict] = list(self.manifest.get("classes") or ())
        self._descriptors: dict[str, int] = {}
        self._slots: dict[str, ClassSlots] | None = None
        self._index: dict[str, set[str]] | None = None
        try:
            head = row_delta.read_head(self.corpus, STORE_NAME)
        except row_delta.HeadError:
            head = True
        if head is not None:
            try:
                self._slots = _store_slots(self.corpus, self.manifest)
                self.source = "store"
                return
            except LayoutError:
                pass
        if index_is_current(self.corpus):
            try:
                self._index = _index_classes(self.corpus)
                self.source = "index"
                return
            except LayoutError:
                pass
        if head is None and not any(
            overflow_index(str(meta.get("id", "")), part) is not None
            for meta in self.metas
            for part in class_shards(meta)
        ):
            self.source = "parts"
            return
        found = (
            "has a unit store head, but neither the store nor the index describes its shards"
            if head is not None
            else "lists an overflow part, which a build appended, but has no unit store head, and its index does not describe its shards"
        )
        raise LayoutError(f"{self.corpus} {found}, so its live fragments cannot be told from its dead slots")

    def ordered(self, order: str = SHARD_ORDER) -> list[dict]:
        """Return the manifest's class entries in `order`: `SHARD_ORDER` or `MANIFEST_ORDER`."""
        if order == MANIFEST_ORDER:
            return list(self.metas)
        return sorted(self.metas, key=lambda meta: class_shard_key(meta.get("id", "")))

    def slots(self, meta: Mapping[str, Any]) -> list[LiveSlot]:
        """Return one class's live slots in id order."""
        class_id = str(meta.get("id", ""))
        parts = class_shards(meta)
        if self._slots is not None:
            found = self._slots.get(class_id)
            return found.ordered() if found is not None else []
        live = None if self._index is None else self._index.get(class_id, set())
        collected = ClassSlots(parts)
        last: dict[str, tuple[str, int, int]] = {}
        for part in parts:
            for start, length, fragment in _walk_part(self.corpus / part):
                unit_id = fragment.get("id") if isinstance(fragment, dict) else None
                if isinstance(unit_id, str) and (live is None or unit_id in live):
                    last[unit_id] = (part, start, length)
        for unit_id, (part, start, length) in last.items():
            collected.add(unit_id, part, start, length)
        return collected.ordered()

    def read(self, slot: LiveSlot) -> bytes:
        """Return one live fragment's bytes, by one `os.pread` on its part's descriptor."""
        descriptor = self._descriptors.get(slot.part)
        if descriptor is None:
            descriptor = os.open(self.corpus / slot.part, os.O_RDONLY)
            self._descriptors[slot.part] = descriptor
        body = os.pread(descriptor, slot.length, slot.start)
        if len(body) != slot.length:
            raise LayoutError(f"{slot.part} ends before {slot.unit_id}'s {slot.length} bytes at {slot.start}")
        return body

    def fragments(
        self, order: str = SHARD_ORDER, classes: Iterable[str] | None = None
    ) -> Iterator[tuple[dict, LiveSlot, dict]]:
        """Yield every live fragment parsed, with its class's manifest entry and its slot: classes in `order` (only those `classes` names, when given), each class's fragments in id order. A corpus the parts drive (`source` is `parts`) is walked part by part, every fragment as it lies, which is id order in a from-scratch layout. A fragment that does not hold its slot's id raises `LayoutError`."""
        wanted = None if classes is None else set(classes)
        for meta in self.ordered(order):
            if wanted is not None and meta.get("id") not in wanted:
                continue
            if self.source == "parts":
                for part in class_shards(meta):
                    for start, length, fragment in _walk_part(self.corpus / part):
                        unit_id = fragment.get("id") if isinstance(fragment, dict) else None
                        yield meta, LiveSlot(str(unit_id), part, start, length), fragment
                continue
            for slot in self.slots(meta):
                fragment = json.loads(self.read(slot))
                if not isinstance(fragment, dict) or fragment.get("id") != slot.unit_id:
                    raise LayoutError(
                        f"{slot.part} does not hold {slot.unit_id} at {slot.start}+{slot.length}"
                    )
                yield meta, slot, fragment

    def close(self) -> None:
        descriptors, self._descriptors = self._descriptors, {}
        for descriptor in descriptors.values():
            os.close(descriptor)

    def __enter__(self) -> LiveCorpus:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
