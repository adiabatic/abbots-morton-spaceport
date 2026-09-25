"""Pack the baseline subset tables into one binary file that every process maps read-only.

`write_pack` reads each acceptance configuration's `baseline-<config>.subset.tsv.gz` once through `rowmodel.open_table`, keeps the codepoint key and the three fields `SubsetRow` has, checks every seam token with `is_seam_token`, and writes one file. The file starts with a JSON header: the format tag, `PACKER_DIGEST` (this module's prose-blind code digest, so a change to the seam vocabulary or the projection rewrites every existing pack), the writer's byte order, the tables' sha256 digests (the caller passes the ones `unit_cache.environment_stamp` computes for its `subsets` line), one string table of every glyph name and seam token, and each configuration's row count and section offsets. Each configuration's section follows: a sorted array of 8-byte keys and a fixed-width record array of glyph ids, cluster starts, and seam ids.

A key is the window's codepoints packed right-aligned into one unsigned 64-bit integer, four 16-bit slots zero-padded on the left. The extractor writes rows in `(length, codepoints)` order, which is also the integer order, so a table normally arrives sorted; one that does not is sorted here.

The packer runs in the surface build's parent before the workload loads, and its memory is budgeted under `SURFACE_PARENT_BYTES` in rebuild/tools/artifact_cycle.py. It holds every table's keys as one `array` of unsigned 64-bit integers and its rows as one `array` of indices into a pool of distinct `(glyphs, clusters, seams)` triples, which repeat heavily within and across configurations, plus one encoded record per distinct triple and one configuration's encoded section at a time while it is written. The file is written under a temporary name and renamed into place, so a reader never maps a partly written pack.

`SubsetPack.open` maps the file with `mmap.ACCESS_READ` and fails when the header's format, packer digest, byte order, or table digests differ from this process's code and from the digests the caller computed from the tables on disk. It interns the string table once through `sys.intern`. `row` bisects the key array through a cast `memoryview` and builds one frozen `SubsetRow` from the record at that index. Every worker shares the mapping through the page cache, and a process's resident share is the pages its lookups touch, so a surface worker's memory does not grow with the tables or the alphabet (`SURFACE_WORKER_BYTES` in rebuild/tools/artifact_cycle.py budgets the rest). `ensure_pack` is the entry point a reader needs: it returns a current pack beside the tables, writing one if needed.

The seam vocabulary check runs over every row at pack time. These rows are the only source of a unit's `before.seams`, and a check over only the rows a worker looks up would not cover the table. The packer fails at the first bad row, before any file is renamed into place, so a table with a compound seam token never becomes a pack.
"""

from __future__ import annotations

import json
import mmap
import os
import struct
import sys
from array import array
from bisect import bisect_left
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rebuild.pipeline.fingerprint import code_file_digest, file_sha256
from rebuild.validation.rowmodel import open_table

PACK_NAME = "baseline-subsets.pack"
PACK_FORMAT = "ams-subset-pack/1"
PACKER_DIGEST = code_file_digest(Path(__file__))
KEY_SLOTS = 4
_SLOT_MAX = 0xFFFF
_KEY_BYTES = 8
_ALIGN = 8
_LENGTH = struct.Struct("<Q")
_SEAM_TOKENS = ("break", "lig", "absent")


def is_seam_token(token) -> bool:
    """Return whether `token` is in the seam vocabulary: `break`, `lig`, `absent`, or `y` followed by a height. The compound tokens `SeamClassifier.classify` emits when two heights join at once (`y0+y5`) are excluded, because a shard's seams are single-height and the surface cannot describe a baseline row with a compound seam."""
    return isinstance(token, str) and (
        token in _SEAM_TOKENS or (token.startswith("y") and token[1:].isdigit())
    )


@dataclass(frozen=True, slots=True)
class SubsetRow:
    """The fields the enricher reads from one baseline subset row: the old font's glyph names, which the kern-neutral re-shape is checked against; the cluster starts, which give the before spans; and the seams, which give the before seams and the seam-grain half of the divergence. `rowmodel.Row` also has `positions`, which this path does not use because the subset was extracted with the old font's kerning on and the before pens come from a kern-neutral re-shape, and `codepoints`, which is the key the pack is searched by. A row is built per lookup from the mapped pack and lives only as long as the enrichment that asked for it, so no process holds a table of these."""

    glyphs: tuple[str, ...]
    clusters: tuple[int, ...]
    seams: tuple[str, ...]


def table_path(subset_dir: Path, config: str) -> Path:
    return Path(subset_dir) / f"baseline-{config}.subset.tsv.gz"


def table_digests(subset_dir: Path, configs: Sequence[str]) -> dict[str, str]:
    """Return each configuration's table sha256, keyed by configuration: the digests the pack header records and `SubsetPack.open` checks."""
    return {config: file_sha256(table_path(subset_dir, config)) for config in configs}


def pack_key(codepoints: str) -> int:
    """Return the pack's integer key for a colon-joined codepoint string. The 16-bit slots are right-aligned, so a shorter window sorts before every longer one and windows of one length sort by their codepoints. `KEY_SLOTS` is four, the longest window the baseline extractor writes (`MAX_LENGTH` in rebuild/baseline/alphabet.py, equal to `conform.BELT_HORIZON`), and a 16-bit slot covers the Basic Multilingual Plane, which holds every codepoint in the tables. A longer window or a codepoint above U+FFFF raises ValueError, so the packer fails on a table that contains one and `SubsetPack.row` returns None for one."""
    key = 0
    slots = 0
    for part in codepoints.split(":"):
        value = int(part, 16)
        if not 0 <= value <= _SLOT_MAX:
            raise ValueError(
                f"the codepoint {part} is outside the pack's {_SLOT_MAX:04X} slot width: {codepoints}"
            )
        key = (key << 16) | value
        slots += 1
    if slots > KEY_SLOTS:
        raise ValueError(
            f"a window of {slots} codepoints outruns the pack's {KEY_SLOTS} key slots: {codepoints}"
        )
    return key


def key_codepoints(key: int) -> str:
    """Return the canonical uppercase codepoint string a key was packed from."""
    parts = []
    while key:
        parts.append(f"{key & 0xFFFF:04X}")
        key >>= 16
    return ":".join(reversed(parts))


def _aligned(offset: int) -> int:
    return (offset + _ALIGN - 1) // _ALIGN * _ALIGN


def _record_struct(glyph_slots: int, cluster_slots: int, seam_slots: int) -> struct.Struct:
    return struct.Struct(f"<BBB{glyph_slots}H{cluster_slots}B{seam_slots}H")


def _projected_rows(path: Path) -> Iterator[tuple[int, tuple[str, ...], tuple[int, ...], tuple[str, ...]]]:
    """Yield every data row of one table as `(key, glyphs, clusters, seams)`. Each distinct seam field is checked against the vocabulary the first time it appears, and only a field that passes is memoized, so the table still fails at its first bad row. The key is derived through `int`, so how the table formats a codepoint does not change the key."""
    seams_by_field: dict[str, tuple[str, ...]] = {}
    clusters_by_field: dict[str, tuple[int, ...]] = {}
    with open_table(path) as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            codepoint_field, glyph_field, cluster_field, seam_field, _positions = line.split("\t")
            seams = seams_by_field.get(seam_field)
            if seams is None:
                seams = tuple(seam_field.split(",")) if seam_field else ()
                for token in seams:
                    if not is_seam_token(token):
                        codepoints = ":".join(f"{int(cp, 16):04X}" for cp in codepoint_field.split(":"))
                        raise ValueError(
                            f"{path}: the baseline row for {codepoints} carries the seam token {token!r}, which is "
                            "not one of break/lig/absent/yN"
                        )
                seams_by_field[seam_field] = seams
            clusters = clusters_by_field.get(cluster_field)
            if clusters is None:
                clusters = clusters_by_field[cluster_field] = tuple(map(int, cluster_field.split(",")))
            yield pack_key(codepoint_field), tuple(glyph_field.split("|")), clusters, seams


@dataclass(slots=True)
class _Table:
    keys: array
    rows: array


def _load_table(path: Path, distinct: dict[tuple, int]) -> _Table:
    """Load one table as its keys, one unsigned 64-bit array, and its rows, one array of indices into `distinct`. `distinct` is the pool of `(glyphs, clusters, seams)` triples shared by every table in the pack, so a row that repeats within or across configurations costs one index. A table out of key order is sorted, and two rows for one window raise ValueError."""
    keys = array("Q")
    rows = array("I")
    for key, glyphs, clusters, seams in _projected_rows(path):
        triple = (glyphs, clusters, seams)
        keys.append(key)
        rows.append(distinct.setdefault(triple, len(distinct)))
    if any(later <= earlier for earlier, later in zip(keys, keys[1:])):
        order = sorted(range(len(keys)), key=keys.__getitem__)
        keys = array("Q", (keys[index] for index in order))
        rows = array("I", (rows[index] for index in order))
        for earlier, later in zip(keys, keys[1:]):
            if earlier == later:
                raise ValueError(f"{path}: the window {key_codepoints(later)} has two baseline rows")
    return _Table(keys, rows)


def write_pack(
    subset_dir: Path, configs: Sequence[str], digests: Mapping[str, str], destination: Path
) -> Path:
    """Pack every named configuration's table into `destination` through a temporary file beside it, which is renamed into place only after every table has been read and checked. After the header, sections are encoded and written one configuration at a time, so the process holds at most one encoded section beside the loaded tables and the encoded records of the distinct rows."""
    subset_dir = Path(subset_dir)
    destination = Path(destination)
    distinct: dict[tuple, int] = {}
    tables = {config: _load_table(table_path(subset_dir, config), distinct) for config in configs}
    triples = list(distinct)
    strings = sorted({text for glyphs, _clusters, seams in triples for text in (*glyphs, *seams)})
    string_ids = {text: index for index, text in enumerate(strings)}
    glyph_slots = max((len(glyphs) for glyphs, _clusters, _seams in triples), default=1)
    cluster_slots = max((len(clusters) for _glyphs, clusters, _seams in triples), default=1)
    seam_slots = max((len(seams) for _glyphs, _clusters, seams in triples), default=1)
    record = _record_struct(glyph_slots, cluster_slots, seam_slots)
    encoded_rows = [
        record.pack(
            len(glyphs),
            len(clusters),
            len(seams),
            *(string_ids[name] for name in glyphs),
            *(0 for _ in range(glyph_slots - len(glyphs))),
            *clusters,
            *(0 for _ in range(cluster_slots - len(clusters))),
            *(string_ids[token] for token in seams),
            *(0 for _ in range(seam_slots - len(seams))),
        )
        for glyphs, clusters, seams in triples
    ]
    del distinct, triples
    sections: dict[str, dict[str, int]] = {}
    offset = 0
    for config in configs:
        table = tables[config]
        keys_at = _aligned(offset)
        records_at = _aligned(keys_at + len(table.keys) * _KEY_BYTES)
        sections[config] = {"rows": len(table.keys), "keys": keys_at, "records": records_at}
        offset = records_at + len(table.rows) * record.size
    header = {
        "format": PACK_FORMAT,
        "packer": PACKER_DIGEST,
        "byteorder": sys.byteorder,
        "digests": {config: digests[config] for config in configs},
        "configs": list(configs),
        "strings": strings,
        "glyph_slots": glyph_slots,
        "cluster_slots": cluster_slots,
        "seam_slots": seam_slots,
        "sections": sections,
    }
    encoded = json.dumps(header, sort_keys=True).encode("utf-8")
    base = _aligned(_LENGTH.size + len(encoded))
    temp = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
    with open(temp, "wb") as out:
        out.write(_LENGTH.pack(len(encoded)))
        out.write(encoded)
        out.write(b"\0" * (base - _LENGTH.size - len(encoded)))
        written = base
        for config in configs:
            table = tables.pop(config)
            section = sections[config]
            out.write(b"\0" * (base + section["keys"] - written))
            out.write(table.keys.tobytes())
            written = base + section["keys"] + len(table.keys) * _KEY_BYTES
            out.write(b"\0" * (base + section["records"] - written))
            out.write(b"".join(map(encoded_rows.__getitem__, table.rows)))
            written = base + section["records"] + len(table.rows) * record.size
    os.replace(temp, destination)
    return destination


def _read_header(path: Path) -> tuple[dict, int]:
    """Return the pack's header and the file offset where its body starts, or raise ValueError for a file that is not a pack."""
    with open(path, "rb") as handle:
        prefix = handle.read(_LENGTH.size)
        if len(prefix) != _LENGTH.size:
            raise ValueError(f"{path}: not a subset pack")
        (length,) = _LENGTH.unpack(prefix)
        encoded = handle.read(length)
    if len(encoded) != length:
        raise ValueError(f"{path}: truncated subset pack header")
    try:
        header = json.loads(encoded.decode("utf-8"))
    except ValueError as error:
        raise ValueError(f"{path}: unreadable subset pack header") from error
    if not isinstance(header, dict) or header.get("format") != PACK_FORMAT:
        raise ValueError(f"{path}: not a {PACK_FORMAT} pack")
    return header, _aligned(_LENGTH.size + length)


def _header_disagreement(header: dict, digests: Mapping[str, str]) -> str | None:
    if header.get("packer") != PACKER_DIGEST:
        return "written by other packer code"
    if header.get("byteorder") != sys.byteorder:
        return f"written on a {header.get('byteorder')}-endian host"
    recorded = header.get("digests")
    if recorded != dict(digests):
        return "written over other tables"
    return None


def ensure_pack(
    subset_dir: Path,
    configs: Sequence[str],
    digests: Mapping[str, str] | None = None,
    pack: Path | None = None,
) -> Path:
    """Return a pack that is current for the tables: the file on disk when its header records these configurations, these tables' digests, this byte order, and this packer's code digest, or else one written by this call. `digests` are the table digests the caller already computed; when omitted, the tables are hashed here. `pack` overrides the file's location. The default is `PACK_NAME` under `subset_dir`, which is outside the `baseline-*.subset.tsv.gz` glob that the subset stamp in rebuild/pipeline/baseline_subset.py reads, so the stamp does not treat the pack as an orphan."""
    subset_dir = Path(subset_dir)
    destination = Path(pack) if pack is not None else subset_dir / PACK_NAME
    if digests is None:
        digests = table_digests(subset_dir, configs)
    if destination.is_file():
        try:
            header, _base = _read_header(destination)
        except ValueError:
            header = None
        if (
            header is not None
            and header.get("configs") == list(configs)
            and _header_disagreement(header, digests) is None
        ):
            return destination
    return write_pack(subset_dir, configs, digests, destination)


@dataclass(slots=True)
class _Section:
    keys: memoryview
    records: memoryview
    rows: int


class SubsetPack:
    """One mapped pack. `row` is the enricher's lookup, `rows` iterates one configuration in key order, `census` reports the pack's size to the pile tally, and `close` releases the views and the mapping."""

    def __init__(self, path: Path, mapping: mmap.mmap, header: dict, base: int) -> None:
        self.path = Path(path)
        self._mapping = mapping
        self._view = memoryview(mapping)
        self.strings = tuple(sys.intern(text) for text in header["strings"])
        self._record = _record_struct(header["glyph_slots"], header["cluster_slots"], header["seam_slots"])
        self._glyph_slots = header["glyph_slots"]
        self._cluster_slots = header["cluster_slots"]
        self._sections: dict[str, _Section] = {}
        for config, section in header["sections"].items():
            rows = section["rows"]
            keys_at = base + section["keys"]
            records_at = base + section["records"]
            self._sections[config] = _Section(
                keys=self._view[keys_at : keys_at + rows * _KEY_BYTES].cast("Q"),
                records=self._view[records_at : records_at + rows * self._record.size],
                rows=rows,
            )

    @classmethod
    def open(cls, path: Path, digests: Mapping[str, str]) -> SubsetPack:
        """Map `path` read-only. Raises ValueError when the file is not a pack, or when the header's packer digest or byte order differs from this process's, or its table digests differ from `digests`, which the caller computed from the tables on disk."""
        path = Path(path)
        header, base = _read_header(path)
        disagreement = _header_disagreement(header, digests)
        if disagreement is not None:
            raise ValueError(
                f"{path}: the subset pack was {disagreement}; rewrite it over the tables it is read beside"
            )
        with open(path, "rb") as handle:
            mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        return cls(path, mapping, header, base)

    @property
    def configs(self) -> tuple[str, ...]:
        return tuple(self._sections)

    def _materialize(self, section: _Section, index: int) -> SubsetRow:
        fields = self._record.unpack_from(section.records, index * self._record.size)
        glyph_count, cluster_count, seam_count = fields[0], fields[1], fields[2]
        glyphs_at = 3
        clusters_at = glyphs_at + self._glyph_slots
        seams_at = clusters_at + self._cluster_slots
        strings = self.strings
        return SubsetRow(
            glyphs=tuple(strings[index] for index in fields[glyphs_at : glyphs_at + glyph_count]),
            clusters=tuple(fields[clusters_at : clusters_at + cluster_count]),
            seams=tuple(strings[index] for index in fields[seams_at : seams_at + seam_count]),
        )

    def row(self, config: str, codepoints: str) -> SubsetRow | None:
        """Return the row for `codepoints` under `config`, or None when the pack has no such configuration or window. A window that has no key returns None, since the packer fails on a table that contains one."""
        section = self._sections.get(config)
        if section is None:
            return None
        try:
            key = pack_key(codepoints)
        except ValueError:
            return None
        index = bisect_left(section.keys, key)
        if index >= section.rows or section.keys[index] != key:
            return None
        return self._materialize(section, index)

    def rows(self, config: str) -> Iterator[tuple[str, SubsetRow]]:
        """Yield every row of one configuration in key order as `(codepoints, row)`."""
        section = self._sections[config]
        for index in range(section.rows):
            yield key_codepoints(section.keys[index]), self._materialize(section, index)

    def census(self) -> tuple[int, int]:
        """Return the pack's row count across all configurations and the mapping's size in bytes, which the pile tally prints. The tally reports the mapping's size because a process's resident share of a mapping is the pages it has touched, which the process cannot read for itself."""
        return sum(section.rows for section in self._sections.values()), len(self._mapping)

    def close(self) -> None:
        for section in self._sections.values():
            section.keys.release()
            section.records.release()
        self._sections.clear()
        self._view.release()
        self._mapping.close()
