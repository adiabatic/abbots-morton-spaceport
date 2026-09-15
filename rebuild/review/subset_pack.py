"""The baseline subset tables packed once and mapped read-only by every process that reads a row.

`write_pack` streams each acceptance configuration's `baseline-<config>.subset.tsv.gz` once through `rowmodel.open_table`, projects exactly the three fields `SubsetRow` carries plus the codepoint key, holds every row's seams to `is_seam_token` on the way, and writes one binary file: a JSON header carrying the format tag, this module's own prose-blind code digest (`PACKER_DIGEST`, so a tightening of the seam vocabulary or of the projection rewrites every pack it reaches rather than being served stale rows by one), the writer's byte order, the tables' sha256 digests (the ones `unit_cache.environment_stamp` already computes for its `subsets` line, passed in rather than rehashed), one string table over every glyph name and seam token, and each configuration's row count and section offsets; then, per configuration, a sorted array of 8-byte keys beside a fixed-width record array of glyph ids, cluster starts and seam ids. What the packer holds while it runs is priced under `SURFACE_PARENT_BYTES` in rebuild/tools/artifact_cycle.py, since it runs in the surface build's parent before the workload loads: every table's keys as one `array` of unsigned 64-bit integers and its rows as one `array` of indices into the pool of distinct `(glyphs, clusters, seams)` triples, which repeat heavily within and across configurations, and one configuration's encoded section at a time on its way to the file. A key is the window's codepoints packed right-aligned into one unsigned 64-bit integer, four 16-bit slots zero-padded on the left, so that the canonical `(length, codepoints)` order the extractor writes is the integer order and a table arrives already sorted; a table that does not is sorted here. The file is written to a temporary name and renamed into place, so a reader never maps a torn pack.

`SubsetPack.open` maps the file with `mmap.ACCESS_READ`, refuses a header whose format, packer digest, byte order or table digests disagree with this process's code and with what the caller measured off the tables on disk, interns the string table once through `sys.intern`, and answers `row` by bisecting the key array as a cast `memoryview` and materializing one frozen `SubsetRow` from the record at that index. The mapping is shared through the page cache by every worker on the box, and a process's resident share of it is the pages its lookups touch rather than the tables, which is what takes a surface worker's cost off the alphabet (`SURFACE_WORKER_BYTES` in rebuild/tools/artifact_cycle.py prices the rest). `ensure_pack` is the one entry a reader needs: a current pack beside the tables, or one written on demand.

The whole-table vocabulary sweep lives at pack time rather than at lookup on purpose: these rows are the sole source of a unit's `before.seams`, and a check that ran only over the rows a worker happened to look up would say nothing about the table. The packer refuses at the first bad row, before any file is renamed into place, so a table the classifier wrote a compound token into never becomes a pack.
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
    """Whether a token is one the seam vocabulary admits: `break`, `lig`, `absent`, or `y` followed by a height. The compound tokens `SeamClassifier.classify` can emit when two heights join at once (`y0+y5`) are deliberately outside it — a shard's seams are single-height, and a baseline row carrying a compound one is a table the surface cannot describe."""
    return isinstance(token, str) and (
        token in _SEAM_TOKENS or (token.startswith("y") and token[1:].isdigit())
    )


@dataclass(frozen=True, slots=True)
class SubsetRow:
    """What the enricher reads off one baseline subset row, and nothing else: the old font's glyph names, which the kern-neutral re-shape is checked against; the cluster starts, which are the before spans; and the seams, which are the before seams and the seam-grain half of the divergence. The full `rowmodel.Row` carries two fields more, and this path reads neither — `positions` is dead here by design, since the subset was extracted with the old font's kerning on and the before pens come from a live kern-neutral re-shape instead, and `codepoints` is the key the pack is bisected by. A row is materialized per lookup out of the mapped pack and lives as long as the enrichment that asked for it, so no process holds a table's worth of these."""

    glyphs: tuple[str, ...]
    clusters: tuple[int, ...]
    seams: tuple[str, ...]


def table_path(subset_dir: Path, config: str) -> Path:
    return Path(subset_dir) / f"baseline-{config}.subset.tsv.gz"


def table_digests(subset_dir: Path, configs: Sequence[str]) -> dict[str, str]:
    """Each configuration's table hashed whole, keyed by configuration: the digests the pack header records and the reader holds it to."""
    return {config: file_sha256(table_path(subset_dir, config)) for config in configs}


def pack_key(codepoints: str) -> int:
    """The colon-joined codepoint string as the pack's integer key: right-aligned 16-bit slots, so a shorter window sorts before every longer one and windows of one length sort by their codepoints. `KEY_SLOTS` is the widest window the extractor writes into a baseline table, four codepoints (the belt's `conform.BELT_HORIZON`), and a slot's width is the Basic Multilingual Plane, which every codepoint the tables carry is in; a window wider than that, or one carrying a codepoint past a slot's width, has no key and cannot be in a pack, so the packer refuses a table holding one and `SubsetPack.row` answers None for one."""
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
    """The canonical uppercase codepoint string a key was packed from."""
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
    """Every data row of one table as `(key, glyphs, clusters, seams)`, the seam field checked against the vocabulary the first time each distinct field text is seen — which still refuses the table at its first bad row, since a field that fails is never memoized — and the key re-derived through `int` so a table's spelling of its codepoints never decides what a window is found under."""
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
    """One table projected and keyed: its keys as one unsigned 64-bit array and its rows as one array of indices into `distinct`, the pool of `(glyphs, clusters, seams)` triples shared by every table of the pack, since a row that repeats across configurations, or within one, is then one index rather than one tuple per line."""
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
    """Pack every named configuration's table into `destination`, through a temporary file beside it that is renamed into place only once every table has been read, projected and checked. Each configuration's section is encoded and written in turn once the header is down, so the file holds at most one section's bytes beside the loaded tables."""
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
    """The pack's header and the file offset its body starts at, or a `ValueError` for a file that is not a pack."""
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
    """A pack beside the tables that is current for them — the one on disk when its header records these tables' digests and this packer's code digest, or one written on this call. `digests` is what the caller already measured; left out, the tables are hashed here. `pack` names another home for the file; the default is `PACK_NAME` under `subset_dir`, outside the `baseline-*.subset.tsv.gz` glob the subset stamp reads, so the stamp never takes it for an orphan."""
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
    """One mapped pack. `row` is the lookup the enricher makes; `rows` walks one configuration in key order for a reader that wants the whole table; `census` is the pile tally's reading of what the mapping is; `close` releases the views and the mapping."""

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
        """Map `path` read-only, refusing a pack whose header disagrees with `digests`, the tables as the caller measured them on disk, or with the packer code this process runs."""
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
        """The row for `codepoints` under `config`, or None when the pack holds no such configuration or the table no such window — a window no key can spell among them, since the packer refuses a table that holds one."""
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
        """Every row of one configuration in key order, as `(codepoints, row)`."""
        section = self._sections[config]
        for index in range(section.rows):
            yield key_codepoints(section.keys[index]), self._materialize(section, index)

    def census(self) -> tuple[int, int]:
        """The rows the pack holds across every configuration, beside the bytes the mapping spans — what the pile tally prints for it, since a mapping's resident share is the pages touched and not a figure a process can read of itself."""
        return sum(section.rows for section in self._sections.values()), len(self._mapping)

    def close(self) -> None:
        for section in self._sections.values():
            section.keys.release()
            section.records.release()
        self._sections.clear()
        self._view.release()
        self._mapping.close()
