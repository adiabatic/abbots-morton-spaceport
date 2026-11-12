"""Writes and reads the corpus's per-unit index, `units-index.ndjson.gz`: one record per unit with the fields the verdict update reads, written beside the manifest and held in the segment format (`rebuild/pipeline/row_delta.py`, #618's change 32): a head, `units-index.ndjson.gz.head`, over a base that is the whole file a write of every record gives, and the segments later builds wrote over it, keyed by class and id.

The shards are the authority, and the index is a projection of them. It exists because the verdict-update tools (carry, duplicate fill, standing fill, the complaint list) and the review-session preparation tools (the review queue snapshot, the novelty order) each read a few fields per unit. Reading those from the shards would mean parsing gigabytes of JSON once per tool per cycle, much of it `explain` and `drafts`, of which the index keeps only the policy draft's file, key path, and suggested record. `index_record` defines the projection. rebuild/test_unit_index.py checks it against the fixture shards field for field and pins the field set. A field a tool needs must be added here: `UnitRecord.get` returns the default for a name outside `INDEX_FIELDS`, so a missing field would silently read as absent.

Two fields are counts, because counting is all any reader does with them: `render_groups` is the number of render groups (standing fill requires exactly one) and `secondary_junctions` the number of secondary junctions (standing fill requires none). Two fields come from the manifest instead of the shard: `order` is the unit's position in the manifest's triage index (`human_unit_ids`), and `batch` is the `batch_size` slice that position falls in. Both are null for a unit outside the index. A fragment carries neither, because a unit's place in the queue is not a property of the unit, and an inserted or removed human unit moves the place of every one after it. So every reader takes them from the manifest beside the index (`Places`, built once a walk in the process that reads), never from a record (#618's decision 55): a base's records keep the pair a whole write gives them, which every reader cuts out and replaces, and a segment's records hold none. An id the triage index lacks reads null for both, whatever its record holds. Every other field is the shard's own value.

The index is stamped with the manifest's identity digest (`manifest_sha256`), as the unit store is, beside its format, in its head's stamps, which override a base's inline header, so an index a crashed build did not rewrite is not read as describing the shards beside it; `index_is_current` also checks that every part the head lists is on disk at the size it records. A reader that finds no index, or one stamped for another manifest, falls back to reading the shards through the same projection. A corpus from before the segment format holds the single file with no head, which every reader reads as a base by its inline header and the next write adopts by link as generation 0's base.

The materialized index (`materialized_index`) is the header line, the head's stamps, then the base's and the segments' records merged by key, `(class_shard_key(class), id)`, the order the build writes them in, each with its place put back from the manifest: byte for byte the file `write_index` writes whole over the same fragments (#618's decision 48), which `write_materialized_index` writes as the single file. A write (`write_index_lines`) takes one route, decided from the prior head before it writes anything of the index: a whole base when there is no prior (`first`) or one that does not read (`unusable`, counted as rewritten whole, `output_sink.note_rewritten`); the materialized form at this pass's content as the next generation's base when the head's fold is due and the index holds the pass's one fold or has waited `row_delta.FOLD_OVERRUN` segments past its cap (`fold`); and otherwise a segment of the records that differ from the prior's, compared with their places cut, or the head alone when none differs. No stamp says every record is re-derived and no size test switches to a base (#618's decisions 72 to 75): a segment of any size is written, and one whose bytes reach its base's makes the next write fold.

This module also holds the one reader of fragments out of shard parts, the live-fragment iterator (`LiveCorpus`), the rule a from-scratch build cuts a class's parts by (`PartLayout`), and the codec a fragment is held deflated in (`deflate_fragment`, `inflate_fragment`). A class's parts are its base parts, which a from-scratch build, a fold or a direct write lays out in id order, and after them its overflow parts (`units/<class>.x<NNN>.json`), to which a build appends each fragment that changed, leaving its old slot dead (`build._ShardWriter`). So a part can hold a fragment no unit owns any more, and a unit's fragment can sit in a later part than its id order puts it in. A fragment is plain, its shard bytes as ASCII JSON, or deflated (#618's change 35): a fragment a build appends, folds or writes directly by the share rule into a class it already has parts for is held as its shard bytes deflated alone, a raw deflate stream that is not ASCII, between the same `[`, commas and closing bytes, while a first base, a from-scratch build's included, stays plain. A deflated fragment's unit store address records its inflated length as a fourth element (decision 76), and its app-index and locator rows carry a trailing `"deflated": true` (decision 77); a plain fragment's record and rows carry neither. The iterator reads only live fragments, each class's in id order, and always hands its readers plain bytes, inflating each deflated slot as it reads it (`LiveCorpus.read`). The first of these that describes the shards decides which fragments are live: where the corpus has a store head, the unit store's records, the fragment at the address each unit's record gives, with its inflated length when it is deflated (`_store_slots`, which reads the store's lines itself, with the slicers `unit_cache` reads them with too, so this module imports nothing of the corpus build); then the index, where it is current and reads to its end, each id it lists taken from its last occurrence in manifest order, since overflow parts follow the base parts and an append follows every older copy; and only for a corpus with no store head, no such index and no overflow part, the parts themselves, walked part by part with every fragment taken as live, as a from-scratch build lays them out (a build puts a store under a head before it appends, `unit_cache.adopt_single_file`, so only a head deleted by hand leaves an appended corpus without one). The index and the parts are walked as JSON, so neither can read a deflated fragment, and a part that is not ASCII is refused there (`LayoutError`): a corpus holding a deflated fragment is readable only through the store. A corpus with a head or an overflow part that neither the store nor the index describes is refused (`LayoutError`). `rebuild/test_unit_index.py`'s contract test fails on any read of shard parts, through `class_shards`, `shard_paths` or `_walk_elements`, outside the iterator and the readers of part names it allows, each named there with its reason.
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import json
import os
import re
import sys
import zlib
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO

from rebuild.pipeline import output_sink, row_delta

INDEX_NAME = "units-index.ndjson.gz"
STORE_NAME = "unit-cache.ndjson.gz"
STORE_FORMAT = "ams-review-unit-cache/9"
STORE_KEY_PREFIX = b'{"key": "'
_ID_START = len(STORE_KEY_PREFIX) + 64 + len(b'", "id": "')
_CLASS_SEAM = b'", "class": '
_CONTENT_KEY_SEAM = b', "content_key": '
_ADDRESS_SEAM = b', "address": '
# The cap on one shard part's bytes on disk. The app reads each byte range it fetches, which never crosses a part, into one buffer and decodes each plain fragment in it as a JavaScript string, a byte a character in this ASCII-only JSON, and V8's `String::kMaxLength` on a 64-bit build is 2**29 - 24 characters. This is half that limit, and the other half is headroom. Whole-part readers keep their memory bound by it too; a part of deflated fragments holds about 2.5 times its bytes once inflated, but every reader inflates one fragment at a time (`LiveCorpus.read`).
SHARD_PART_BYTES = 1 << 28
PART_CLOSING = b"\n]\n"
SHARD_ORDER = "shard"
MANIFEST_ORDER = "manifest"
INDEX_FORMAT = "ams-review-unit-index/1"
ASSET_COMPONENTS: tuple[str, ...] = ("static",)
ID_OPEN = b'{"id": "'
CLASS_SEAM = b', "class": '
_ID_AT = len(ID_OPEN)
_CLUSTER_SEAM = b', "cluster": '
_NO_PLACE = b', "order": null, "batch": null'


def index_path(corpus: Path) -> Path:
    return Path(corpus) / INDEX_NAME


def deflate_fragment(body: bytes) -> bytes:
    """Return a fragment's plain shard bytes (`build._fragment_bytes`) deflated alone as a shard part holds a deflated fragment (#618's change 35): raw deflate, with no zlib header or trailer and no preset dictionary, at level 1, the level the fragments' 2.5 to 1 was measured at. The app inflates it with the browser's `DecompressionStream('deflate-raw')`, which takes no dictionary, and nothing else is needed to read it back, so it outlives the build that wrote it, unlike the arena's dictionary deflate (`build._FragmentPacker`)."""
    return zlib.compress(body, 1, wbits=-15)


def inflate_fragment(stored: bytes, length: int | None = None) -> bytes:
    """Return a deflated fragment's plain bytes, checked: `stored` must be exactly one raw deflate stream, which ends where `stored` ends, and inflate to `length` bytes when `length` is given (its unit store address records it, decision 76). A stream that does not inflate, is cut short, runs on past its end, or inflates to another length raises ValueError, and with `length` given the inflate stops one byte past it, so a corrupt stream cannot inflate without bound. A caller that knows the unit checks the bytes are its own."""
    inflater = zlib.decompressobj(wbits=-15)
    try:
        body = inflater.decompress(stored, 0 if length is None else length + 1)
    except zlib.error as error:
        raise ValueError(f"it does not inflate ({error})") from None
    if length is not None and len(body) > length:
        raise ValueError(f"it inflates past its {length} bytes")
    if not inflater.eof:
        raise ValueError("its deflated stream is cut short")
    if inflater.unused_data:
        raise ValueError("bytes follow the end of its deflated stream")
    if length is not None and len(body) != length:
        raise ValueError(f"it inflates to {len(body)} bytes, not {length}")
    return body


def holds_unit(body: bytes, unit_id: str) -> bool:
    """Whether a fragment's plain shard bytes are unit `unit_id`'s: the shard writer's framing puts each field on its own line as `"key": value`, so the id is found as a substring."""
    return f'"id": {json.dumps(unit_id)}'.encode() in body


def address_value(part: str, start: int, length: int, inflated: int = 0) -> list:
    """Return a fragment's unit store address as a record holds it: `[part, start, length]` for a plain fragment, with `length` its bytes on disk, and `[part, start, length, inflated]` for a deflated one, whose fourth element is its inflated length (#618's decisions 76 and 77)."""
    return [part, start, length, inflated] if inflated else [part, start, length]


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
    """Project one shard fragment onto the fields the verdict update reads, with the unit's place in the manifest's triage index passed in. The key order is fixed so that two builds of the same corpus write the same bytes, and so that every line starts with `id`, `order`, and `batch`, with `class` after them at `CLASS_SEAM`. A line is read by its head without being parsed: its id is sliced after `ID_OPEN`, its place is everything between the id and `CLASS_SEAM`, which a segment's record leaves out and a reader replaces with the manifest's (`Places.placed`), and its key is its class and id (`index_key`). `copy_forward_index_line` joins a new head onto a line there too, and `load_human_units` classifies a line by its id without parsing it. rebuild/test_unit_index.py checks this opening."""
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
    return _manifest_and_identity(corpus)[1]


def _manifest_and_identity(corpus: Path) -> tuple[dict, str]:
    """Return the manifest parsed, or empty when it does not parse as an object, beside its identity (`manifest_sha256`), from one read and one parse. A missing manifest raises OSError."""
    raw = (Path(corpus) / "manifest.json").read_bytes()
    try:
        document = json.loads(raw)
    except ValueError:
        return {}, hashlib.sha256(raw).hexdigest()
    return (document if isinstance(document, dict) else {}), manifest_identity(document)


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
    """Return one index record as its line in a whole file or a base, with its place. The build compresses these lines in memory as the fragments pass (`build._SidecarLines`), so it holds the compressed lines, not the fragments `write_index` holds."""
    return (json.dumps(index_record(fragment, order=order, batch=batch), ensure_ascii=False) + "\n").encode()


def line_head(unit_id: str, order: int | None, batch: int | None) -> bytes:
    """Return the start of an index or app-index line, the id and the unit's place in the queue, as `json.dumps` writes it, without the closing brace. A line whose other fields did not change can be copied forward by joining this to its tail."""
    return json.dumps({"id": unit_id, "order": order, "batch": batch}, ensure_ascii=False).encode()[:-1]


def copy_forward_index_line(line: bytes, *, unit_id: str, order: int | None, batch: int | None) -> bytes:
    """Return a previous corpus's index line for a cached unit, as a base holds it with its old place or as a segment holds it with none, with this corpus's `order` and `batch`. Every field after those two is the fragment's own, and a byte-copied unit has an unchanged fragment, so the result is the id and the new place joined to the old tail, byte for byte what `index_line` writes for the same fragment."""
    return line_head(unit_id, order, batch) + line[line.index(CLASS_SEAM) :]


def _id_end(line: bytes) -> int:
    """Return where the id of an index line ends, a base's, a segment's or a tombstone's: at the quote that closes it after `ID_OPEN`."""
    return line.index(b'"', _ID_AT)


def line_id(line: bytes) -> str:
    """Return an index line's unit id, sliced after `ID_OPEN` without parsing the line."""
    return line[_ID_AT : _id_end(line)].decode()


@functools.lru_cache(maxsize=4096)
def _class_key(raw: bytes) -> bytes:
    value = json.loads(raw)
    return class_shard_key(value if isinstance(value, str) else "").encode()


def index_key(line: bytes) -> list[bytes]:
    """Return the key the index's records are in order by and its parts merge by (`row_delta.merge_keyed`): the record's class as `class_shard_key` gives it, then its id, each as bytes, which sort as the strings do. A base's line, a segment's upsert and a tombstone read alike: the id is sliced after `ID_OPEN` and the class after `CLASS_SEAM`, up to `"cluster"`, or in a tombstone the closing brace, and only the class is parsed, once a distinct class."""
    end = _id_end(line)
    start = line.index(CLASS_SEAM, end) + len(CLASS_SEAM)
    stop = line.find(_CLUSTER_SEAM, start)
    return [_class_key(line[start : stop if stop >= 0 else line.rindex(b"}")]), line[_ID_AT:end]]


def _cut(line: bytes) -> bytes:
    """Return an index line with its place cut out, as a segment holds the record."""
    end = _id_end(line)
    at = line.index(CLASS_SEAM, end)
    return line if at == end + 1 else line[: end + 1] + line[at:]


def _tombstone(cut: bytes) -> bytes:
    """Return the payload of the tombstone that removes the record whose line, its place cut, is `cut`: its id and class, which `index_key` reads as the record's key."""
    start = _id_end(cut) + 1 + len(CLASS_SEAM)
    stop = cut.find(_CLUSTER_SEAM, start)
    return cut[: stop if stop >= 0 else cut.rindex(b"}")] + b"}\n"


def _header_line(header: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(header)) + "\n").encode()


class Places:
    """Each human unit's place in the queue as the manifest beside the index gives it, the only source of an index record's `order` and `batch` (#618's decision 55): its position in the triage index (`human_positions`) and that position's slice of `batch_size`, the manifest's, or 1 where it names none. A reader builds it once a walk, in the process that reads, from the manifest it parsed to check the index's stamp, and never hands it across a process. `slot_reader` reads a fragment's place through it, and `placed` puts a record's place into its line."""

    __slots__ = ("batch_size", "positions")

    def __init__(self, manifest: Mapping[str, Any]) -> None:
        self.positions = human_positions(manifest)
        batch_size = manifest.get("batch_size")
        self.batch_size = batch_size if isinstance(batch_size, int) and batch_size > 0 else 1

    def placed(self, line: bytes) -> bytes:
        """Return an index line, a base's with whatever place it holds or a segment's with none, with this manifest's place for its unit in that place: `line_head`'s bytes for the id and the place, joined to the line from `CLASS_SEAM`, the place null for an id the triage index lacks."""
        end = _id_end(line)
        return self.placed_at(line, end, self.positions.get(line[_ID_AT:end].decode()))

    def placed_at(self, line: bytes, end: int, order: int | None) -> bytes:
        """`placed` for a line whose id ends at `end`, its unit's position `order` already looked up."""
        place = (
            _NO_PLACE if order is None else b', "order": %d, "batch": %d' % (order, order // self.batch_size)
        )
        return line[: end + 1] + place + line[line.index(CLASS_SEAM, end) :]


class LineCursor:
    """A forward-only reader over one of a corpus's sidecars, the app index's gzipped NDJSON file (`LineCursor(path)`) or the units index's record lines (`LineCursor.over_index`), that returns the line whose leading `field` has a requested value, skipping every line before it. Each sidecar is written in shard order, whose terms are all content-derived, so a cached unit keeps its relative place from one corpus to the next, and the build reads the previous sidecar once, in step with what it writes, without holding it. The units index's lines are its base's and segments' merged by key, a buffer a part (`OpenIndex.lines`), each with the place its part holds or none, since `copy_forward_index_line` writes this pass's own, so a write worker that reads them holds no places. A skipped line belongs to a unit this build projects again. Once a requested value is not found, as with a file from another build or no file at all, the cursor returns None for every later request, and the caller projects the unit itself."""

    def __init__(self, path: Path, field: str = "id") -> None:
        self._open(field, lambda: _sidecar_lines(Path(path)))

    @classmethod
    def over_index(cls, corpus: Path) -> LineCursor:
        """Return a cursor by id over the units index of `corpus`."""
        cursor = cls.__new__(cls)
        cursor._open("id", lambda: _index_lines(Path(corpus)))
        return cursor

    def _open(self, field: str, opener: Callable[[], tuple[Iterator[bytes], Callable[[], None]]]) -> None:
        self._field = field
        self._lines: Iterator[bytes] | None = None
        self._closer: Callable[[], None] | None = None
        try:
            self._lines, self._closer = opener()
        except OSError, EOFError, ValueError, StopIteration:
            self.close()

    def take(self, value: str) -> bytes | None:
        if self._lines is None:
            return None
        prefix = f"{{{json.dumps(self._field)}: {json.dumps(value)}, ".encode()
        for line in self._lines:
            if line.startswith(prefix):
                return line
        self.close()
        return None

    def close(self) -> None:
        closer, self._closer, self._lines = self._closer, None, None
        if closer is not None:
            closer()


def _sidecar_lines(path: Path) -> tuple[Iterator[bytes], Callable[[], None]]:
    stream = gzip.open(path, "rb")
    try:
        next(stream)
    except BaseException:
        stream.close()
        raise
    return iter(stream), stream.close


def _index_lines(corpus: Path) -> tuple[Iterator[bytes], Callable[[], None]]:
    opened = open_index(corpus)
    if opened is None:
        raise FileNotFoundError(index_path(corpus))
    return opened.lines(), opened.close


def _manifest(corpus: Path) -> dict:
    try:
        document = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return document if isinstance(document, dict) else {}


def slot_reader(corpus: Path):
    """Return a function that maps a fragment to its `order` and `batch` on this corpus, reading the triage index once from the manifest beside it (`Places`)."""
    places = Places(_manifest(corpus))

    def slot(fragment: Mapping[str, Any]) -> dict[str, int | None]:
        return human_unit_slot(places.positions, places.batch_size, fragment)

    return slot


class IndexUnreadable(ValueError):
    """Raised when an index that exists cannot be opened: a head that does not read (`row_delta.HeadError`), a part missing or not the size, or, when checked, the sha256 its head records, or a single file whose header line does not read as an object."""


@dataclass
class OpenIndex:
    """The units index open for reading: its header, the head's stamps or the inline header of the single file a corpus from before the segment format holds, its head (None for that single file), and its parts, each opened as the head was read (`row_delta.open_parts`), so a later write's deletes cannot reach a reader mid-walk, since an open descriptor survives the unlink. Close it when done."""

    header: dict
    head: row_delta.Head | None
    handles: list[BinaryIO]

    def lines(self) -> Iterator[bytes]:
        """Return the materialized index's record lines in key order, each with the place its part holds, a base's, or none, a segment's, which a reader replaces with the manifest's (`Places.placed`): the base's read straight through for an index with no segment, and otherwise the base and the segments merged by key as streams (`row_delta.merge_keyed` over `index_key`), a buffer a part, the latest part's row winning at each key and a tombstone removing it. A segment out of key order, or a base out of key order before its last change, raises `ValueError` naming the key (`row_delta.merge_keyed`); a base's rows past its last change, and every row of a base with no segment, are read unchecked, so a base's key order is its writer's to keep (`write_index_lines`). A segment whose header is not the head's raises `row_delta.HeadError`."""
        base = _base_lines(self.handles[0])
        head = self.head
        if head is None or not head.segments:
            return base
        segments = [
            row_delta.keyed_segment_rows(handle, INDEX_NAME, head.generation, number)
            for number, handle in enumerate(self.handles[1:], start=1)
        ]
        return row_delta.merge_keyed(base, segments, index_key, index_key)

    def close(self) -> None:
        handles, self.handles = self.handles, []
        for handle in handles:
            handle.close()

    def __enter__(self) -> OpenIndex:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _base_lines(handle: BinaryIO) -> Iterator[bytes]:
    handle.seek(0)
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        if stream.readline():
            yield from stream


def _single_header(handle: BinaryIO) -> dict:
    """Return the inline header of an index file open as `handle`, its first line, leaving the handle at its start. A header that does not read as a JSON object raises `IndexUnreadable`."""
    try:
        with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
            header = json.loads(stream.readline())
    except (OSError, EOFError, ValueError, zlib.error) as error:
        raise IndexUnreadable(f"{INDEX_NAME}'s header does not read: {error!r}") from None
    finally:
        handle.seek(0)
    if not isinstance(header, dict):
        raise IndexUnreadable(f"{INDEX_NAME}'s header is not an object")
    return header


def open_index(corpus: Path, *, sha256: bool = False) -> OpenIndex | None:
    """Open the units index of `corpus` for reading, or return None when it has none: its head and every part the head lists, each checked against the head's size, and with `sha256` its digest too, as a reader that reads every byte for the acceptance bar does (`materialized_index`); or, with no head, the single file a corpus from before the segment format holds, read as a base by its inline header. An index that exists and does not open raises `IndexUnreadable`."""
    corpus = Path(corpus)
    try:
        head = row_delta.read_head(corpus, INDEX_NAME)
    except row_delta.HeadError as error:
        raise IndexUnreadable(str(error)) from None
    if head is not None:
        try:
            opened = row_delta.open_parts(corpus, head, sha256=sha256)
        except row_delta.HeadError as error:
            raise IndexUnreadable(str(error)) from None
        return OpenIndex(dict(head.stamps), head, opened.handles)
    try:
        handle = open(index_path(corpus), "rb")
    except FileNotFoundError:
        return None
    try:
        header = _single_header(handle)
    except BaseException:
        handle.close()
        raise
    return OpenIndex(header, None, [handle])


def _current(corpus: Path) -> tuple[OpenIndex, dict] | None:
    """Open the index when it describes the manifest beside it (`index_is_current`), beside that manifest parsed, from the one parse its stamp is checked against; None when it does not."""
    try:
        opened = open_index(corpus)
    except OSError, IndexUnreadable:
        return None
    if opened is None:
        return None
    try:
        manifest, identity = _manifest_and_identity(corpus)
    except OSError:
        opened.close()
        return None
    if opened.header.get("format") != INDEX_FORMAT or opened.header.get("manifest_sha256") != identity:
        opened.close()
        return None
    return opened, manifest


def _open_placed(corpus: Path) -> tuple[OpenIndex, Places] | None:
    """`_current` with the manifest's places (`Places`) in place of the manifest, which goes once they are built."""
    found = _current(corpus)
    return None if found is None else (found[0], Places(found[1]))


def _open_unplaced(corpus: Path) -> OpenIndex | None:
    """`_current` without the manifest, for a reader that takes no places."""
    found = _current(corpus)
    return None if found is None else found[0]


def index_header(corpus: Path) -> dict | None:
    """Return the index's header, or None when there is none to read: the head's stamps, which override a base's inline header, or with no head the single file's inline header. It is separate from the readers so a check can confirm the index is stamped for the manifest beside it without opening any part."""
    corpus = Path(corpus)
    try:
        head = row_delta.read_head(corpus, INDEX_NAME)
    except row_delta.HeadError:
        return None
    if head is not None:
        return dict(head.stamps)
    try:
        with open(index_path(corpus), "rb") as handle:
            return _single_header(handle)
    except OSError, IndexUnreadable:
        return None


def index_is_current(corpus: Path) -> bool:
    """Whether the index beside this manifest describes it: present, in a format this reader knows, stamped, by its head or by a single file's inline header, with the manifest's own identity, and with every part its head lists on disk at the size the head records. `app_index.outputs_not_current`, and through it the cycle's skip and `status`'s freshness check, read it, and so do the build's copy-forward condition and its output check."""
    opened = _open_unplaced(corpus)
    if opened is None:
        return False
    opened.close()
    return True


def materialized_index(corpus: Path, *, sha256: bool = False) -> tuple[dict, Iterator[bytes]] | None:
    """Return the index's materialized form, the file a whole write of the same records gives (#618's decision 48): its header, the head's stamps or a single file's inline header, and its record lines in key order, each with its place put back from the manifest beside it (`Places`). `perf_fingerprint` and `build.MaterializedCorpus` read it with `sha256`, every part checked against the digest its head records. None when there is no index; an index that does not open raises `IndexUnreadable`."""
    opened = open_index(corpus, sha256=sha256)
    if opened is None:
        return None
    places = Places(_manifest(corpus))

    def lines() -> Iterator[bytes]:
        with opened:
            for line in opened.lines():
                yield places.placed(line)

    return opened.header, lines()


def _write_lines(
    stream: row_delta.Sink, header: Mapping[str, Any], lines: Iterable[bytes]
) -> tuple[int, int]:
    """Write the index's header line and its record lines as a whole file and a base hold them, and return the records and their bytes."""
    stream.write(_header_line(header))
    count = total = 0
    for line in lines:
        stream.write(line)
        count += 1
        total += len(line)
    return count, total


def write_materialized_index(directory: Path, header: Mapping[str, Any], lines: Iterable[bytes]) -> Path:
    """Write a materialized index (`materialized_index`) as the single file `units-index.ndjson.gz` under `directory`, byte for byte as a base holds the same records (`_write_lines`, at gzip level 1 with a fixed mtime and the gzip header naming the file), through `output_sink.replace_output`."""
    path = index_path(directory)

    def fill(staging: Path) -> None:
        with open(staging, "wb") as handle:
            with gzip.GzipFile(
                filename=INDEX_NAME, fileobj=handle, mode="wb", mtime=0, compresslevel=1
            ) as stream:
                _write_lines(stream, header, lines)

    output_sink.replace_output(path, writer=fill)
    return path


@dataclass(frozen=True)
class IndexWrite:
    """What one write of the index did (`write_index_lines`): wrote the `head` alone, a `segment`, or a new `base`, with the base's reason (`first`, `unusable` or `fold`), and the segment's rows and their bytes or the base's records and theirs. `note` is what the corpus build's `[t] review.build manifest+check` line says of it, as `unit_cache.StoreWrite.note` says of the store."""

    outcome: str
    reason: str = ""
    rows: int = 0
    rows_bytes: int = 0

    def note(self) -> str:
        if self.outcome == "head":
            return "head only"
        if self.outcome == "segment":
            return f"a segment of {self.rows:,} rows, {self.rows_bytes / 1e6:.1f} MB"
        return f"a new base ({self.reason}), {self.rows:,} records"


@dataclass
class _IndexRoute:
    """A write's route, decided before anything of the index is written (`_plan_write`): `reason` names a new base's (`first`, `unusable` or `fold`) and is empty for a segment or the head alone, `prior` is the head the write follows, and `opened` its parts, checked, which the segment route's compare and a fold's walk read."""

    reason: str
    prior: row_delta.Head | None = None
    opened: OpenIndex | None = None


def _adopt_single_file(corpus: Path) -> row_delta.Head | None:
    """Put the single file a corpus from before the segment format holds under a head, as generation 0's base, and return the head, or None when the file does not read: its header line an object in this format, and every byte through to its gzip trailer, read once to count its records and their bytes, which the fold compares the segments with. The base is a hard link to the file, which writes no data, and the head over it, whose stamps are the file's inline header, is committed at once, as `unit_cache.adopt_single_file` commits the store's. The single file stays under its own name until the write commits a later head over the link, and every reader reads the head first."""
    single = index_path(corpus)
    rows = rows_bytes = 0
    try:
        with gzip.open(single, "rb") as stream:
            header = json.loads(stream.readline())
            if not isinstance(header, dict) or header.get("format") != INDEX_FORMAT:
                return None
            while chunk := stream.read(1 << 20):
                rows += chunk.count(b"\n")
                rows_bytes += len(chunk)
    except OSError, EOFError, ValueError, zlib.error:
        return None
    name = row_delta.base_name(INDEX_NAME, 0)
    path = corpus / name
    os.link(single, path)
    base = row_delta.Part(name, path.stat().st_size, row_delta.file_sha256(path), rows, rows_bytes)
    head = row_delta.Head(INDEX_NAME, 0, 0, header, base, (), row_delta.MERGE_KEY)
    row_delta.commit(corpus, head)
    return head


def _checked(corpus: Path, head: row_delta.Head, *, sha256: bool) -> OpenIndex | None:
    """Open `head`'s parts for the compare, each checked against the head, its sha256 too with `sha256`, and each segment's header line against the head's output, generation, number and merge, or return None when one fails."""
    try:
        opened = row_delta.open_parts(corpus, head, sha256=sha256)
    except row_delta.HeadError:
        return None
    try:
        for number, handle in enumerate(opened.handles[1:], start=1):
            handle.seek(0)
            with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
                row_delta.check_segment_header(
                    row_delta.gzipped_header(stream, INDEX_NAME, number),
                    INDEX_NAME,
                    head.generation,
                    number,
                    row_delta.MERGE_KEY,
                )
            handle.seek(0)
    except OSError, EOFError, ValueError, zlib.error:
        opened.close()
        return None
    return OpenIndex(dict(head.stamps), head, opened.handles)


def _plan_write(corpus: Path) -> _IndexRoute:
    """Decide a write's route from the prior head (the module docstring), before anything of the index is written. It deletes the staging files a killed write left, leaving a link the memory series put at a staging name (`row_delta.remove_staging` with `links` false, `memory_series.null_routes`), and the parts no head lists (`row_delta.remove_unlisted`), adopts the single file a corpus from before the segment format holds (`_adopt_single_file`), opens the prior's parts and checks each, every byte against its sha256 since the compare reads them all, and claims the pass's one fold where the head's fold is due (`row_delta.claim_fold`, the claim the windows rows, the settle memos, the audit, the oracle stores and the unit store share). A head that does not read, merges another way or names another format, a part that fails its check, and a single file whose header does not read in this format take the `unusable` route."""
    row_delta.remove_staging(corpus, INDEX_NAME, links=False)
    try:
        head = row_delta.read_head(corpus, INDEX_NAME)
    except row_delta.HeadError:
        row_delta.remove_unlisted(corpus, INDEX_NAME, None)
        return _IndexRoute("unusable")
    row_delta.remove_unlisted(corpus, INDEX_NAME, head)
    if head is None:
        if not index_path(corpus).exists():
            return _IndexRoute("first")
        head = _adopt_single_file(corpus)
        if head is None:
            return _IndexRoute("unusable")
        opened = _checked(corpus, head, sha256=False)
        return _IndexRoute("unusable", head) if opened is None else _IndexRoute("", head, opened)
    if head.merge != row_delta.MERGE_KEY or head.stamps.get("format") != INDEX_FORMAT:
        return _IndexRoute("unusable", head)
    opened = _checked(corpus, head, sha256=True)
    if opened is None:
        return _IndexRoute("unusable", head)
    route = _IndexRoute("", head, opened)
    if row_delta.fold_due(head) and (row_delta.claim_fold(INDEX_NAME) or row_delta.fold_forced(head)):
        route.reason = "fold"
    return route


def _compare(new_lines: Iterable[bytes], old_lines: Iterable[bytes], emit: Callable[[bytes], object]) -> int:
    """Walk this pass's index lines beside the prior materialized index's, both in key order, emit a segment row for each record that differs, with places cut on both sides (`_cut`), and return how many: an upsert of this pass's line for a record new or changed, a tombstone (`_tombstone`) for a prior record this pass lacks, and nothing for an equal one, a line equal to its prior byte for byte, place included, passed without cutting either. The rows come out in key order, and one whose key does not ascend past the last one's raises `ValueError` naming it, so no row out of key order reaches a segment, which no merge would take; a line out of key order that equals its prior emits no row and is not checked."""
    new, old = iter(new_lines), iter(old_lines)
    ours, theirs = next(new, None), next(old, None)
    rows = 0
    last: list[bytes] | None = None

    def row(op: bytes, payload: bytes, key: list[bytes]) -> None:
        nonlocal rows, last
        if last is not None and key <= last:
            raise ValueError(f"the index's records are out of key order at {key!r}")
        last = key
        emit(op + b"\t" + payload)
        rows += 1

    while ours is not None or theirs is not None:
        if ours is not None and ours == theirs:
            ours, theirs = next(new, None), next(old, None)
            continue
        cut_ours = None if ours is None else _cut(ours)
        cut_theirs = None if theirs is None else _cut(theirs)
        if cut_ours is not None and cut_ours == cut_theirs:
            ours, theirs = next(new, None), next(old, None)
            continue
        key_ours = None if cut_ours is None else index_key(cut_ours)
        key_theirs = None if cut_theirs is None else index_key(cut_theirs)
        if cut_ours is not None and key_ours is not None and (key_theirs is None or key_ours <= key_theirs):
            row(row_delta.UPSERT, cut_ours, key_ours)
            if key_ours == key_theirs:
                theirs = next(old, None)
            ours = next(new, None)
        else:
            assert cut_theirs is not None and key_theirs is not None
            row(row_delta.TOMBSTONE, _tombstone(cut_theirs), key_theirs)
            theirs = next(old, None)
    return rows


def _write_base(corpus: Path, route: _IndexRoute, stamps: dict, lines: Iterable[bytes]) -> IndexWrite:
    """Write this pass's lines whole as the next generation's base (`_write_lines`) and commit a head that lists it alone. A fold walks the prior's records beside the lines as the segment route does (`_compare`), writing no row, so its head's content generation moves only when a record did (`row_delta.Head.identity`)."""
    prior = route.prior
    if route.reason == "unusable":
        output_sink.note_rewritten(INDEX_NAME)
    generation = 0 if prior is None else prior.generation + 1
    differs = 0

    def fill(stream: row_delta.Sink) -> tuple[int, int]:
        nonlocal differs
        if route.opened is None:
            return _write_lines(stream, stamps, lines)
        written = [0, 0]

        def through() -> Iterator[bytes]:
            for line in lines:
                stream.write(line)
                written[0] += 1
                written[1] += len(line)
                yield line

        stream.write(_header_line(stamps))
        differs = _compare(through(), route.opened.lines(), lambda _row: None)
        return written[0], written[1]

    base = row_delta.write_part(
        corpus, row_delta.base_name(INDEX_NAME, generation), fill, gzip_name=INDEX_NAME
    )
    if route.opened is not None:
        route.opened.close()
    content = 0 if prior is None else prior.content + (1 if route.reason != "fold" or differs else 0)
    row_delta.commit(
        corpus, row_delta.Head(INDEX_NAME, generation, content, stamps, base, (), row_delta.MERGE_KEY)
    )
    index_path(corpus).unlink(missing_ok=True)
    return IndexWrite("base", route.reason, base.rows, base.rows_bytes)


def _write_segment(corpus: Path, route: _IndexRoute, stamps: dict, lines: Iterable[bytes]) -> IndexWrite:
    """Write the records that differ from the prior's as the prior generation's next segment (`_compare` into a `row_delta.StreamedPart`, gzipped at level 1 with a fixed mtime, which makes no part when no row reaches it) and commit a head that lists it, or, when none differs, the prior head under this pass's stamps alone, which `row_delta.commit` leaves in place when it comes out the same."""
    prior, opened = route.prior, route.opened
    assert prior is not None and opened is not None
    number = len(prior.segments) + 1
    part = row_delta.StreamedPart(
        corpus,
        row_delta.segment_name(INDEX_NAME, prior.generation, number),
        row_delta.segment_header(INDEX_NAME, prior.generation, number, row_delta.MERGE_KEY),
        gzipped=True,
    )
    try:
        _compare(lines, opened.lines(), part.write)
        written = part.close()
    except BaseException:
        part.abandon()
        raise
    opened.close()
    if written is None:
        head, outcome = replace(prior, stamps=stamps), IndexWrite("head")
    else:
        head = row_delta.with_segment(prior, written, stamps)
        outcome = IndexWrite("segment", "", written.rows, written.rows_bytes)
    row_delta.commit(corpus, head)
    index_path(corpus).unlink(missing_ok=True)
    return outcome


def write_index_lines(corpus: Path, lines: Iterable[bytes]) -> IndexWrite:
    """Write the index from already-projected lines, `index_line`'s, each with its place, in key order (`index_key`), stamped with the manifest beside it, so this must run after the manifest is written. It takes one route, decided from the prior head before anything is written (`_plan_write`, the module docstring), and commits by renaming the head into place last (`row_delta.commit`), after which the parts no head lists and the single file a corpus from before the segment format held are deleted. A base is the file a whole write gives: gzip level 1, since it is written once and read a few times a cycle and level 9's extra seconds cost more than the megabytes it saves, with a fixed mtime and the gzip header naming `units-index.ndjson.gz`, never a part's or a staging file's name. A segment holds the records whose line, its place cut, differs from the prior materialized index's (`_compare`): an upsert, `+`, a tab and the record's line without its place, for a record new or changed, and a tombstone, `-`, a tab and its id and class, for a prior record this pass lacks, so a unit that changes class is a tombstone at its old key and an upsert at its new one. The compare walks this pass's lines beside the prior's parts, a buffer a part, and holds no record. A write that raises or is killed leaves the previous head and every part it lists as they were: what it staged is removed, and a part it wrote that no head lists is deleted by the next write."""
    corpus = Path(corpus)
    stamps = {"format": INDEX_FORMAT, "manifest_sha256": manifest_sha256(corpus)}
    route = _plan_write(corpus)
    try:
        if route.reason:
            return _write_base(corpus, route, stamps, lines)
        return _write_segment(corpus, route, stamps, lines)
    finally:
        if route.opened is not None:
            route.opened.close()


def write_index(corpus: Path, shards: Iterable[tuple[str, list[dict]]]) -> IndexWrite:
    """Write the index from fragments the caller holds in memory. `shards` is (class id, fragments) pairs in any order, each class's fragments in id order, which nothing checks before a base is written (`OpenIndex.lines`). The lines are written in shard-path order, with each unit's place in the queue read from the manifest beside it, so this is `write_index_lines` over the same projection the build makes one fragment at a time: both take the same route and leave the same head, parts and materialized bytes."""
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


def load_index(corpus: Path, *, fields: Iterable[str] | None = None) -> list[UnitRecord] | None:
    """Return the index's records, each with its place from the manifest, or None when there is no usable index: absent, unreadable, a different format, or stamped for a manifest other than the one on disk. A None costs the caller one pass over the shards, so over-invalidation is the safe direction here too."""
    reader = _RecordReader(fields)
    found = _open_placed(corpus)
    if found is None:
        return None
    opened, places = found
    try:
        with opened:
            return [reader.record(json.loads(places.placed(line))) for line in opened.lines()]
    except OSError, EOFError, ValueError, StopIteration, zlib.error:
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
    """Yield every unit on a corpus, projected, one at a time: from the index when it is present and stamped for this manifest, each record's place from the manifest (`Places`), from the shards otherwise. A caller that keeps only part of the corpus should read it this way instead of through `load_units`, so the records it drops are never in memory with the ones it keeps. `load_human_units` does this for the verdict update's human units, and classifies each index line by its id, against the manifest's triage index, instead of parsing every record."""
    reader = _RecordReader(fields)
    return _iter_units(corpus, reader)


def _iter_units(corpus: Path, reader: _RecordReader) -> Iterator[UnitRecord]:
    found = _open_placed(corpus)
    if found is None:
        yield from _stream_shards(corpus, reader)
        return
    opened, places = found
    with opened:
        for line in opened.lines():
            yield reader.record(json.loads(places.placed(line)))


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
    """Yield the human units' index records in shard order. When `unit_ids` is given, every unit id on the corpus is added to it during the same walk; the set is complete only once the iterator is exhausted. A line is human when the manifest's triage index holds its id, sliced from its head (`Places`), and a machine unit's line contributes only its id and is not parsed as JSON. An absent or stale index falls back to the projected shards, which classify each unit through `human_unit_slot`. A corrupt current index raises, because restarting a partly consumed stream from the shards would yield units twice."""
    reader = _RecordReader(fields)
    return _iter_human_units(corpus, reader, unit_ids)


def _iter_human_units(corpus: Path, reader: _RecordReader, unit_ids: set[str] | None) -> Iterator[UnitRecord]:
    found = _open_placed(corpus)
    if found is not None:
        yield from _stream_human_index(*found, reader, unit_ids)
        return
    yield from _stream_human_shards(corpus, reader, unit_ids)


def _stream_human_index(
    opened: OpenIndex, places: Places, reader: _RecordReader, unit_ids: set[str] | None
) -> Iterator[UnitRecord]:
    positions = places.positions
    with opened:
        for line in opened.lines():
            end = _id_end(line)
            line.index(CLASS_SEAM, end)
            unit_id = line[_ID_AT:end].decode()
            if unit_ids is not None:
                unit_ids.add(unit_id)
            order = positions.get(unit_id)
            if order is not None:
                yield reader.record(json.loads(places.placed_at(line, end, order)))


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
    """Return the human units' records on a corpus (the units the manifest's triage index holds, with `batch` not None) and the id of every unit on it, machine units included. The verdict update's consumers read the human records and only the id of a machine record: carry's orphaned count and the complaint list's absent-unit warning check prior verdicts against every id on the corpus, which is why the id set is returned. Over a current index the classification is by id: each line's id is sliced from its head, which must reach `CLASS_SEAM`, and looked up in the manifest's triage index (`Places`), and only the human lines are parsed, each with its place from the manifest, whatever place a base's record holds. An index that fails partway through is discarded and the shards are read instead, as `load_index` does. That fallback parses every shard part, so a corrupt index costs a full parse of the corpus."""
    reader = _RecordReader(fields)
    ids: set[str] = set()
    found = _open_placed(corpus)
    if found is not None:
        try:
            return list(_stream_human_index(*found, reader, ids)), ids
        except OSError, EOFError, ValueError, StopIteration, zlib.error:
            ids = set()
            reader = _RecordReader(reader.schema)
    return list(_stream_human_shards(corpus, reader, ids)), ids


def iter_index_ids(corpus: Path) -> Iterator[str] | None:
    """Return every unit id the current index holds, in key order, each sliced from its line without parsing it and none a tombstone removed, or None when the index is not current, so the caller falls back to the shards. It holds no places. A line with no `CLASS_SEAM` after its id, as a torn record is, raises `ValueError`."""
    opened = _open_unplaced(corpus)
    if opened is None:
        return None
    return _ids(opened)


def _ids(opened: OpenIndex) -> Iterator[str]:
    with opened:
        for line in opened.lines():
            end = _id_end(line)
            line.index(CLASS_SEAM, end)
            yield line[_ID_AT:end].decode()


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
    """The rule a from-scratch build cuts one class's fragments into parts by, which `build._ShardWriter` writes a base by and the materialized form of the corpus is laid out by. Fragments arrive in id order, each with its bytes on disk; `place` returns the part index, the byte start and whether the fragment opens its part (else a comma precedes it). A part is `[`, its fragments joined by commas, and `PART_CLOSING`, so a part of plain fragments equals `json.dumps(part, indent=1, ensure_ascii=True) + "\\n"` under the shard writer's framing, and a class with no fragment is one part, `[]\\n`. A base of deflated fragments, a fold's or a share-rule direct write's, is cut by the same rule over their deflated lengths, so it takes fewer parts than its plain form, the materialized form's. A part closes before the fragment that would take it past `SHARD_PART_BYTES`, so every part stays within the cap but one holding a single fragment that alone exceeds it. `sizes` closes the layout and returns each part's size."""

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
    """Where one live fragment lies: its unit's id, the part (as the manifest names it), and the byte start and length on disk of its element there, the `(part, start, length)` the app's sidecars and the unit store's records use, by which the slot is read. `inflated` is a deflated fragment's inflated length (#618's decision 76), and 0 for a plain one."""

    unit_id: str
    part: str
    start: int
    length: int
    inflated: int = 0

    @property
    def content_length(self) -> int:
        """The fragment's plain length: its inflated length when it is deflated, else its length."""
        return self.inflated or self.length


class ClassSlots:
    """One class's live slots as columns, ids as their ASCII bytes padded with NUL to a unit id's width (which sorts a shorter id first, as a string does), with each slot's part, start, length on disk and inflated length (0 for a plain fragment), so a corpus's worth of addresses costs a few dozen bytes a unit rather than an object each. `ordered` sorts them by id, which is the order every reader takes them in."""

    ID_WIDTH = 13

    def __init__(self, parts: list[str]) -> None:
        self.parts = parts
        self._index = {part: index for index, part in enumerate(parts)}
        self._ids = bytearray()
        self._part = array("H")
        self._start = array("Q")
        self._length = array("I")
        self._inflated = array("I")

    def __len__(self) -> int:
        return len(self._part)

    def add(self, unit_id: str, part: str, start: int, length: int, inflated: int = 0) -> None:
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
        self._inflated.append(inflated)

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
                LiveSlot(
                    unit_id,
                    self.parts[self._part[index]],
                    self._start[index],
                    self._length[index],
                    self._inflated[index],
                )
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
    """Walk one part's elements (`_walk_elements`) with byte offsets. A part that is not pure ASCII raises `LayoutError`: its character offsets are not byte offsets, and a part a build wrote holds a non-ASCII byte only inside a deflated fragment, which no walk as JSON can read, so yielding nothing would drop a folded class's every unit without a word."""
    raw = path.read_bytes()
    if not raw.isascii():
        raise LayoutError(
            f"{path.name} is not ASCII, so it holds a deflated fragment only the unit store can place"
        )
    text = raw.decode("ascii")
    del raw
    yield from _walk_elements(text)


def _index_classes(corpus: Path) -> dict[str, set[str]]:
    """Return each class's live ids as the current index lists them: each record's id and class, read from its line's head (`index_record` writes `id`, its place, which a segment's record leaves out, and `class` first) without parsing the rest, and with no places taken from the manifest. An index that is not current or does not read to its end raises `LayoutError`."""
    classes: dict[str, set[str]] = {}
    try:
        opened = _open_unplaced(corpus)
        if opened is None:
            raise LayoutError("the index does not describe the manifest beside it")
        with opened:
            for line in opened.lines():
                record = json.loads(line[: line.index(_CLUSTER_SEAM)] + b"}")
                classes.setdefault(record["class"], set()).add(record["id"])
    except LayoutError:
        raise
    except (OSError, EOFError, ValueError, KeyError, TypeError, StopIteration, zlib.error) as error:
        raise LayoutError(f"the index does not give the live fragments: {error!r}") from error
    return classes


def record_id(line: bytes) -> str:
    """Return a unit store line's unit id, sliced from where `to_record` writes it, after the input key."""
    return line[_ID_START : line.index(b'"', _ID_START)].decode("ascii")


def record_class(line: bytes) -> str:
    """Return a unit store line's class, sliced from where `to_record` writes it, after the unit id."""
    start = line.index(_CLASS_SEAM, _ID_START) + len(_CLASS_SEAM)
    return json.loads(line[start : line.index(_CONTENT_KEY_SEAM, start)])


def record_address(line: bytes) -> tuple[str, int, int, int] | None:
    """Return a unit store line's address, sliced from its end, where `to_record` writes it, as its part, start, length on disk and inflated length, the last 0 for a plain fragment, whose address has three elements (`address_value`), or None for a record that has none."""
    value = json.loads(line[line.rindex(_ADDRESS_SEAM) + len(_ADDRESS_SEAM) :].rstrip()[:-1])
    if value is None:
        return None
    part, start, length, *inflated = value
    if len(inflated) > 1:
        raise ValueError(f"an address holds three or four elements, not {len(value)}")
    return str(part), int(start), int(length), int(inflated[0]) if inflated else 0


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


def _describing_store_head(corpus: Path, manifest: Mapping[str, Any]) -> row_delta.Head:
    """Return the unit store's head when the store describes the shards beside it: its format this one, its manifest stamp the manifest's identity, and its recorded part sizes every part the manifest lists, at the size each has on disk. A store that does not raises `LayoutError`."""
    head = row_delta.read_head(corpus, STORE_NAME)
    if head is None:
        raise LayoutError("no unit store head")
    stamps = head.stamps
    if stamps.get("format") != STORE_FORMAT or stamps.get("manifest_sha256") != manifest_sha256(corpus):
        raise LayoutError("the unit store describes another manifest")
    parts = [part for meta in manifest.get("classes") or () for part in class_shards(meta)]
    recorded = stamps.get("parts") or {}
    if set(recorded) != set(parts) or any((corpus / part).stat().st_size != recorded[part] for part in parts):
        raise LayoutError("a shard part is not the one the unit store records")
    return head


def store_is_current(corpus: Path) -> bool:
    """Whether the unit store beside this manifest describes it, as a read through the store requires (`_store_slots`). A build writes the store after the shards, the manifest and the sidecars, so a build stopped after its sidecars and before its store leaves the sidecars stamped for its manifest beside a store stamped for the one before, over parts whose deflated fragments (#618's change 35) only the store can place."""
    corpus = Path(corpus)
    try:
        _describing_store_head(corpus, _manifest(corpus))
    except OSError, ValueError, KeyError, TypeError, AttributeError:
        return False
    return True


def _store_slots(corpus: Path, manifest: Mapping[str, Any]) -> dict[str, ClassSlots]:
    """Return every class's live slots as the unit store's records give them: each record's unit id at the address it names, with its inflated length when it is deflated, under the class whose parts list that address's part. The store must have a head and describe the shards beside it (`_describing_store_head`). A store that does not, or a record with no address or an address outside the parts of the class it records, such as a dead slot the unit left when it changed class, raises `LayoutError`, and the iterator falls back to the index."""
    try:
        head = _describing_store_head(corpus, manifest)
        classes = list(manifest.get("classes") or ())
        part_class = {part: meta["id"] for meta in classes for part in class_shards(meta)}
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
    """The live-fragment iterator: the one reader of fragments out of a corpus's shard parts (see the module docstring for what makes a fragment live). Open it over a corpus, take its classes in shard order (`class_shard_key`, the order of the index and the sidecars) or manifest order, and each class's live slots in id order (`slots`), their plain bytes, a deflated slot's inflated (`read`), or the fragments parsed (`fragments`). `source` says what drives it: `store`, `index` or `parts`, the last for a corpus with no store head, no current index that reads to its end and no overflow part, whose every fragment it takes as live. The index and the parts walk each part as JSON and refuse one that is not ASCII (`LayoutError`), so only the store reads a corpus that holds a deflated fragment. Reads go through one descriptor a part, kept open until `close`, so a later writer's renames cannot reach a reader mid-walk."""

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
        """Return one live fragment's plain bytes, by one `os.pread` on its part's descriptor. A deflated slot is inflated, checked (`inflate_fragment`), and must hold its unit; one that does not inflate to its recorded length, or holds another unit, raises `LayoutError`."""
        descriptor = self._descriptors.get(slot.part)
        if descriptor is None:
            descriptor = os.open(self.corpus / slot.part, os.O_RDONLY)
            self._descriptors[slot.part] = descriptor
        body = os.pread(descriptor, slot.length, slot.start)
        if len(body) != slot.length:
            raise LayoutError(f"{slot.part} ends before {slot.unit_id}'s {slot.length} bytes at {slot.start}")
        if not slot.inflated:
            return body
        try:
            body = inflate_fragment(body, slot.inflated)
        except ValueError as error:
            raise LayoutError(
                f"{slot.part} at {slot.start}+{slot.length} does not hold {slot.unit_id}: {error}"
            ) from None
        if not holds_unit(body, slot.unit_id):
            raise LayoutError(
                f"{slot.part} at {slot.start}+{slot.length} inflates to another unit than {slot.unit_id}"
            )
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
