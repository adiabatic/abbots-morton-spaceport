"""Pack the baseline subset tables into one binary file that every process maps read-only.

`write_pack` reads each acceptance configuration's `baseline-<config>.subset.tsv.gz` through `rowmodel.open_table`, keeps the codepoint key and the three fields `SubsetRow` has, checks every junction token with `is_junction_token`, and writes one file. The file starts with a JSON header: the format tag, `PACKER_DIGEST` (this module's prose-insensitive code digest, so a change to the junction vocabulary or the projection rewrites every existing pack), the writer's byte order, the tables' sha256 digests (the caller passes the ones `unit_cache.environment_stamp` computes for its `subsets` line), one string table of every glyph name and junction token, and each configuration's row count and section offsets. Each configuration's section follows: a sorted array of 8-byte keys and a fixed-width record array of glyph ids, cluster starts, and junction ids.

A key is the window's codepoints packed right-aligned into one unsigned 64-bit integer, four 16-bit slots zero-padded on the left. The extractor writes rows in `(length, codepoints)` order, which is also the integer order, so a table normally arrives sorted; one that does not is sorted here, on the in-memory path below.

The packer runs in the corpus build's parent before the workload loads and before any pool starts, and its memory is budgeted under `CORPUS_PARENT_BYTES` in rebuild/tools/artifact_cycle.py. It reads the tables in two passes, each in one spawn process a configuration whatever the build's widths, which inherit the parent's environment and with it the build's allocator setting. Pass 1 (`_survey`) streams one table through `_projected_rows` and returns its row count, whether its keys strictly increase, its glyph names and junction tokens, its largest glyph, cluster and junction counts, and the exception its read raised, as a value, since the tables finish in any order. The parent collects every survey before it decides: it raises the earliest configuration's error in `configs` order, the one a serial read in that order meets first, and writes no file before it does. Otherwise it lays out the header from the surveys exactly as one pass over every row would (`_layout`), marks `SURVEYED_MARK` with the rows and strings (`memory_tally.mark`), writes the header and its padding at offset 0 of the temporary file, and sizes the file to its final length with `ftruncate`, which leaves every alignment gap a hole that reads as zeros, so no byte is written twice. Pass 2 (`_write_section`) opens the temporary file by path without truncating it, so a late opener leaves its neighbors' sections alone, streams its table again, encodes each row with the header's string ids and record layout (`_encode_section`), and writes its keys and its records at their offsets with `pwrite`, a bounded chunk at a time. No message between the parent and a pack process carries rows: the arguments are paths and the header's parameters, and the results are counts, a few hundred strings and errors. So the parent holds the surveys' sets and the header, and each pack process one table's field memos and a chunk. The file is renamed into place once every pass-2 process has answered, and removed if any failed, so a reader never maps a partly written pack.

A table whose keys do not strictly increase sends the whole pack down the in-memory path (`write_pack_in_memory`), in the parent alone: it holds every table's keys as one `array` of unsigned 64-bit integers and its rows as one `array` of indices into a pool of distinct `(glyphs, clusters, junctions)` triples, which repeat heavily across configurations, plus one encoded record per distinct triple and one configuration's encoded section at a time while it is written. It sorts an out-of-order table and refuses a window with two rows, and writes the bytes the two passes write.

`SubsetPack.open` maps the file with `mmap.ACCESS_READ` and fails when the header's format, packer digest, byte order, or table digests differ from this process's code and from the digests the caller computed from the tables on disk. It interns the string table once through `sys.intern`. `row` bisects the key array through a cast `memoryview` and builds one frozen `SubsetRow` from the record at that index. Every worker shares the mapping through the page cache, and a process's resident share is the pages its lookups touch, so a corpus worker's memory does not grow with the tables or the alphabet (`CORPUS_WORKER_BYTES` in rebuild/tools/artifact_cycle.py budgets the rest). `ensure_pack` is the entry point a reader needs: it returns a current pack beside the tables, writing one if needed.

The junction vocabulary check runs over every row at pack time. These rows are the only source of a unit's `before.junctions`, and a check over only the rows a worker looks up would not cover the table. Pass 1 checks every row of every table, and the parent raises the earliest configuration's first bad row before any file is written, so a table with a compound junction token never becomes a pack.
"""

from __future__ import annotations

import functools
import json
import mmap
import multiprocessing
import os
import struct
import sys
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor
from dataclasses import dataclass
from itertools import repeat
from pathlib import Path

from rebuild.pipeline.fingerprint import code_file_digest, file_sha256
from rebuild.tools import memory_tally
from rebuild.validation.rowmodel import open_table

PACK_NAME = "baseline-subsets.pack"
PACK_FORMAT = "ams-subset-pack/1"
PACKER_DIGEST = code_file_digest(Path(__file__))
KEY_SLOTS = 4
_SLOT_MAX = 0xFFFF
_KEY_BYTES = 8
_ALIGN = 8
_LENGTH = struct.Struct("<Q")
_JUNCTION_TOKENS = ("break", "lig", "absent")
_CHUNK_ROWS = 8192
# The mark (`memory_tally.mark`) the parent makes between the passes, with the pack's rows and strings, which the memory series' pack child reads.
SURVEYED_MARK = "pack surveyed"


def is_junction_token(token) -> bool:
    """Return whether `token` is in the junction vocabulary: `break`, `lig`, `absent`, or `y` followed by a height. The compound tokens `JunctionClassifier.classify` emits when two heights join at once (`y0+y5`) are excluded, because a shard's junctions are single-height and the corpus cannot describe a baseline row with a compound junction."""
    return isinstance(token, str) and (
        token in _JUNCTION_TOKENS or (token.startswith("y") and token[1:].isdigit())
    )


@dataclass(frozen=True, slots=True)
class SubsetRow:
    """The fields the enricher reads from one baseline subset row: the old font's glyph names, which the kern-neutral re-shape is checked against; the cluster starts, which give the before spans; and the junctions, which give the before junctions and the junction-grain half of the divergence. `rowmodel.Row` also has `positions`, which this path does not use because the subset was extracted with the old font's kerning on and the before pens come from a kern-neutral re-shape, and `codepoints`, which is the key the pack is searched by. A row is built per lookup from the mapped pack and lives only as long as the enrichment that asked for it, so no process holds a table of these."""

    glyphs: tuple[str, ...]
    clusters: tuple[int, ...]
    junctions: tuple[str, ...]


def table_path(subset_dir: Path, config: str) -> Path:
    return Path(subset_dir) / f"baseline-{config}.subset.tsv.gz"


def table_digests(subset_dir: Path, configs: Sequence[str]) -> dict[str, str]:
    """Return each configuration's table sha256, keyed by configuration: the digests the pack header records and `SubsetPack.open` checks."""
    return {config: file_sha256(table_path(subset_dir, config)) for config in configs}


def pack_key(codepoints: str) -> int:
    """Return the pack's integer key for a colon-joined codepoint string. The 16-bit slots are right-aligned, so a shorter window sorts before every longer one and windows of one length sort by their codepoints. `KEY_SLOTS` is four, the longest window the baseline extractor writes (`MAX_LENGTH` in rebuild/baseline/alphabet.py, equal to `conform.SWEEP_MAX_LENGTH`), and a 16-bit slot covers the Basic Multilingual Plane, which holds every codepoint in the tables. A longer window or a codepoint above U+FFFF raises ValueError, so the packer fails on a table that contains one and `SubsetPack.row` returns None for one."""
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


def _record_struct(glyph_slots: int, cluster_slots: int, junction_slots: int) -> struct.Struct:
    return struct.Struct(f"<BBB{glyph_slots}H{cluster_slots}B{junction_slots}H")


def _projected_rows(path: Path) -> Iterator[tuple[int, tuple[str, ...], tuple[int, ...], tuple[str, ...]]]:
    """Yield every data row of one table as `(key, glyphs, clusters, junctions)`. Each distinct junction field is checked against the vocabulary the first time it appears, and only a field that passes is memoized, so the table still fails at its first bad row. The key is derived through `int`, so how the table formats a codepoint does not change the key."""
    junctions_by_field: dict[str, tuple[str, ...]] = {}
    clusters_by_field: dict[str, tuple[int, ...]] = {}
    with open_table(path) as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            codepoint_field, glyph_field, cluster_field, junction_field, _positions = line.split("\t")
            junctions = junctions_by_field.get(junction_field)
            if junctions is None:
                junctions = tuple(junction_field.split(",")) if junction_field else ()
                for token in junctions:
                    if not is_junction_token(token):
                        codepoints = ":".join(f"{int(cp, 16):04X}" for cp in codepoint_field.split(":"))
                        raise ValueError(
                            f"{path}: the baseline row for {codepoints} carries the junction token {token!r}, which is "
                            "not one of break/lig/absent/yN"
                        )
                junctions_by_field[junction_field] = junctions
            clusters = clusters_by_field.get(cluster_field)
            if clusters is None:
                clusters = clusters_by_field[cluster_field] = tuple(map(int, cluster_field.split(",")))
            yield pack_key(codepoint_field), tuple(glyph_field.split("|")), clusters, junctions


@dataclass(slots=True)
class _Table:
    keys: array
    rows: array


def _load_table(path: Path, distinct: dict[tuple, int]) -> _Table:
    """Load one table as its keys, one unsigned 64-bit array, and its rows, one array of indices into `distinct`. `distinct` is the pool of `(glyphs, clusters, junctions)` triples shared by every table in the pack, so a row that repeats within or across configurations costs one index. A table out of key order is sorted, and two rows for one window raise ValueError."""
    keys = array("Q")
    rows = array("I")
    for key, glyphs, clusters, junctions in _projected_rows(path):
        triple = (glyphs, clusters, junctions)
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


@dataclass(frozen=True, slots=True)
class _Survey:
    """One table's pass-1 result, what the parent lays the pack out from: its row count, whether its keys strictly increase, its glyph names and junction tokens, its largest glyph, cluster and junction counts (0 for a table with no rows), and the exception its read raised, or None. A table that raised holds the counts up to its bad row."""

    rows: int
    increasing: bool
    strings: frozenset[str]
    glyphs: int
    clusters: int
    junctions: int
    error: Exception | None


def _survey(path: str) -> _Survey:
    """Pass 1 over one table, in a pack process: stream it once through `_projected_rows` and return its `_Survey`. The exception the read raises at its first bad row is returned, not raised, so the parent can raise the earliest configuration's whichever table finishes first."""
    rows = 0
    increasing = True
    last = -1
    strings: set[str] = set()
    glyph_slots = cluster_slots = junction_slots = 0
    error: Exception | None = None
    try:
        for key, glyphs, clusters, junctions in _projected_rows(Path(path)):
            if key <= last:
                increasing = False
            last = key
            rows += 1
            strings.update(glyphs)
            strings.update(junctions)
            glyph_slots = max(glyph_slots, len(glyphs))
            cluster_slots = max(cluster_slots, len(clusters))
            junction_slots = max(junction_slots, len(junctions))
    except Exception as raised:
        error = raised
    return _Survey(rows, increasing, frozenset(strings), glyph_slots, cluster_slots, junction_slots, error)


@dataclass(frozen=True, slots=True)
class _Layout:
    """The pack the parent lays out from every table's survey: its header, the header's bytes with their length prefix and padding (`head`), the offset of the body (`base`), and the file's final length (`size`), the base plus the last section's end."""

    header: dict
    head: bytes
    base: int
    size: int

    @property
    def slots(self) -> tuple[int, int, int]:
        return (self.header["glyph_slots"], self.header["cluster_slots"], self.header["junction_slots"])


def _layout(configs: Sequence[str], surveys: Sequence[_Survey], digests: Mapping[str, str]) -> _Layout:
    """Lay the pack out as the in-memory path does from every row: the strings sorted over the union of the surveys', each slot count the largest over every row, or 1 when no table has a row, and each configuration's section offsets after the one before it."""
    strings = sorted(frozenset().union(*(survey.strings for survey in surveys)))
    if any(survey.rows for survey in surveys):
        glyph_slots = max(survey.glyphs for survey in surveys)
        cluster_slots = max(survey.clusters for survey in surveys)
        junction_slots = max(survey.junctions for survey in surveys)
    else:
        glyph_slots = cluster_slots = junction_slots = 1
    record = _record_struct(glyph_slots, cluster_slots, junction_slots)
    sections: dict[str, dict[str, int]] = {}
    offset = 0
    for config, survey in zip(configs, surveys):
        keys_at = _aligned(offset)
        records_at = _aligned(keys_at + survey.rows * _KEY_BYTES)
        sections[config] = {"rows": survey.rows, "keys": keys_at, "records": records_at}
        offset = records_at + survey.rows * record.size
    header = {
        "format": PACK_FORMAT,
        "packer": PACKER_DIGEST,
        "byteorder": sys.byteorder,
        "digests": {config: digests[config] for config in configs},
        "configs": list(configs),
        "strings": strings,
        "glyph_slots": glyph_slots,
        "cluster_slots": cluster_slots,
        "junction_slots": junction_slots,
        "sections": sections,
    }
    encoded = json.dumps(header, sort_keys=True).encode("utf-8")
    base = _aligned(_LENGTH.size + len(encoded))
    head = _LENGTH.pack(len(encoded)) + encoded + bytes(base - _LENGTH.size - len(encoded))
    return _Layout(header, head, base, base + offset)


def _encode_section(
    path: Path,
    rows: int,
    keys_at: int,
    records_at: int,
    strings: Sequence[str],
    slots: tuple[int, int, int],
    sink: Callable[[int, bytes], None],
) -> None:
    """Pass 2's encoder: stream one table through `_projected_rows`, encode each row's key and its record with `strings`' ids and the record layout `slots` gives, and hand `sink` each chunk of up to `_CHUNK_ROWS` keys and of as many records as `(offset, bytes)`, at `keys_at` and `records_at` (file offsets) plus the rows before it, keys first. Holds one chunk of each. Raises ValueError, before it hands over a row past the section, when the table holds other than the `rows` pass 1 counted."""
    string_ids = {text: index for index, text in enumerate(strings)}
    glyph_slots, cluster_slots, junction_slots = slots
    record = _record_struct(glyph_slots, cluster_slots, junction_slots)
    size = record.size
    pack_into = record.pack_into
    keys = array("Q")
    records = bytearray(_CHUNK_ROWS * size)
    done = 0
    filled = 0
    for key, glyphs, clusters, junctions in _projected_rows(path):
        if done + filled == rows:
            raise ValueError(f"{path}: the table holds more than the {rows:,} rows its first pass counted")
        keys.append(key)
        pack_into(
            records,
            filled * size,
            len(glyphs),
            len(clusters),
            len(junctions),
            *[string_ids[name] for name in glyphs],
            *(0,) * (glyph_slots - len(glyphs)),
            *clusters,
            *(0,) * (cluster_slots - len(clusters)),
            *[string_ids[token] for token in junctions],
            *(0,) * (junction_slots - len(junctions)),
        )
        filled += 1
        if filled == _CHUNK_ROWS:
            sink(keys_at + done * _KEY_BYTES, keys.tobytes())
            sink(records_at + done * size, bytes(records))
            done += filled
            filled = 0
            del keys[:]
    if filled:
        sink(keys_at + done * _KEY_BYTES, keys.tobytes())
        sink(records_at + done * size, bytes(memoryview(records)[: filled * size]))
        done += filled
    if done != rows:
        raise ValueError(f"{path}: the table holds {done:,} rows, not the {rows:,} its first pass counted")


def _pwrite_all(descriptor: int, offset: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.pwrite(descriptor, view, offset)
        view = view[written:]
        offset += written


def _write_section(
    temp: str,
    path: str,
    rows: int,
    keys_at: int,
    records_at: int,
    strings: Sequence[str],
    slots: tuple[int, int, int],
) -> Exception | None:
    """Pass 2 over one table, in a pack process: open the staged pack `temp` by path for writing without truncating it, so its neighbors' sections stay as they are, and `pwrite` this table's keys and records at their offsets as `_encode_section` hands them over. Return the exception it raised, or None."""
    try:
        descriptor = os.open(temp, os.O_WRONLY)
        try:
            _encode_section(
                Path(path),
                rows,
                keys_at,
                records_at,
                strings,
                slots,
                functools.partial(_pwrite_all, descriptor),
            )
        finally:
            os.close(descriptor)
    except Exception as raised:
        return raised
    return None


def write_pack(
    subset_dir: Path, configs: Sequence[str], digests: Mapping[str, str], destination: Path
) -> Path:
    """Pack every named configuration's table into `destination` in two passes, one spawn process a configuration, through a temporary file beside it that is renamed into place once every section is written (the module docstring has the passes). Raises the exception the earliest configuration's table raised in pass 1 before any file is written, and takes the in-memory path (`write_pack_in_memory`) when a table's keys do not strictly increase."""
    subset_dir = Path(subset_dir)
    destination = Path(destination)
    paths = [str(table_path(subset_dir, config)) for config in configs]
    with ProcessPoolExecutor(
        max_workers=max(1, len(paths)), mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        surveys = list(pool.map(_survey, paths))
        for survey in surveys:
            if survey.error is not None:
                raise survey.error
            if not survey.increasing:
                break
        else:
            return _write_sections(pool, configs, paths, surveys, digests, destination)
    return write_pack_in_memory(subset_dir, configs, digests, destination)


def _write_sections(
    pool: Executor,
    configs: Sequence[str],
    paths: Sequence[str],
    surveys: Sequence[_Survey],
    digests: Mapping[str, str],
    destination: Path,
) -> Path:
    """Lay the pack out from the surveys, stage the header in a temporary file sized to the pack's length, have `pool` run pass 2 one table a task, and rename the file into place once every task has answered, or remove it and raise the earliest configuration's error if any failed."""
    layout = _layout(configs, surveys, digests)
    header = layout.header
    memory_tally.mark(
        SURVEYED_MARK, rows=sum(survey.rows for survey in surveys), strings=len(header["strings"])
    )
    sections = [header["sections"][config] for config in configs]
    temp = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
    try:
        try:
            _pwrite_all(descriptor, 0, layout.head)
            os.ftruncate(descriptor, layout.size)
        finally:
            os.close(descriptor)
        failures = list(
            pool.map(
                _write_section,
                repeat(str(temp)),
                paths,
                [section["rows"] for section in sections],
                [layout.base + section["keys"] for section in sections],
                [layout.base + section["records"] for section in sections],
                repeat(header["strings"]),
                repeat(layout.slots),
            )
        )
        failure = next((failure for failure in failures if failure is not None), None)
        if failure is not None:
            raise failure
        os.replace(temp, destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return destination


def write_pack_in_memory(
    subset_dir: Path, configs: Sequence[str], digests: Mapping[str, str], destination: Path
) -> Path:
    """Pack every named configuration's table into `destination` in this process, through a temporary file beside it, which is renamed into place only after every table has been read and checked: the path `write_pack` takes when a table's keys do not strictly increase, which sorts the table. After the header, sections are encoded and written one configuration at a time, so the process holds at most one encoded section beside the loaded tables and the encoded records of the distinct rows. It writes the bytes the two passes write."""
    subset_dir = Path(subset_dir)
    destination = Path(destination)
    distinct: dict[tuple, int] = {}
    tables = {config: _load_table(table_path(subset_dir, config), distinct) for config in configs}
    triples = list(distinct)
    strings = sorted({text for glyphs, _clusters, junctions in triples for text in (*glyphs, *junctions)})
    string_ids = {text: index for index, text in enumerate(strings)}
    glyph_slots = max((len(glyphs) for glyphs, _clusters, _junctions in triples), default=1)
    cluster_slots = max((len(clusters) for _glyphs, clusters, _junctions in triples), default=1)
    junction_slots = max((len(junctions) for _glyphs, _clusters, junctions in triples), default=1)
    record = _record_struct(glyph_slots, cluster_slots, junction_slots)
    encoded_rows = [
        record.pack(
            len(glyphs),
            len(clusters),
            len(junctions),
            *(string_ids[name] for name in glyphs),
            *(0 for _ in range(glyph_slots - len(glyphs))),
            *clusters,
            *(0 for _ in range(cluster_slots - len(clusters))),
            *(string_ids[token] for token in junctions),
            *(0 for _ in range(junction_slots - len(junctions))),
        )
        for glyphs, clusters, junctions in triples
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
        "junction_slots": junction_slots,
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
    """One mapped pack. `row` is the enricher's lookup, `rows` iterates one configuration in key order, `sizes` reports the pack's size to the memory tally, and `close` releases the views and the mapping."""

    def __init__(self, path: Path, mapping: mmap.mmap, header: dict, base: int) -> None:
        self.path = Path(path)
        self._mapping = mapping
        self._view = memoryview(mapping)
        self.strings = tuple(sys.intern(text) for text in header["strings"])
        self._record = _record_struct(
            header["glyph_slots"], header["cluster_slots"], header["junction_slots"]
        )
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
        glyph_count, cluster_count, junction_count = fields[0], fields[1], fields[2]
        glyphs_at = 3
        clusters_at = glyphs_at + self._glyph_slots
        junctions_at = clusters_at + self._cluster_slots
        strings = self.strings
        return SubsetRow(
            glyphs=tuple(strings[index] for index in fields[glyphs_at : glyphs_at + glyph_count]),
            clusters=tuple(fields[clusters_at : clusters_at + cluster_count]),
            junctions=tuple(strings[index] for index in fields[junctions_at : junctions_at + junction_count]),
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

    def sizes(self) -> tuple[int, int]:
        """Return the pack's row count across all configurations and the mapping's size in bytes, which the memory tally prints. The tally reports the mapping's size because a process's resident share of a mapping is the pages it has touched, which the process cannot read for itself."""
        return sum(section.rows for section in self._sections.values()), len(self._mapping)

    def close(self) -> None:
        for section in self._sections.values():
            section.keys.release()
            section.records.release()
        self._sections.clear()
        self._view.release()
        self._mapping.close()
