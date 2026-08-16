"""K5's other half: does sharing one parsed table via mmap beat rewriting the parser?

The cost model's note is that the parallel build "pays the subset parse six to twelve times over".
`rebuild/review/enrich.py:305-313` is the concrete instance: every spawn worker builds its own
`dict[str, Row]` for every config it touches, out of `baseline-<config>.subset.tsv.gz`.

Two variants, same N workers, same lookup list, same answers:

  A `per_worker_parse`  — the status quo. Each worker gunzips and parses all six subset tables into
                          dict[str, Row], then answers its slice of the lookups.
  B `mmap_shared`       — one prep pass packs the six tables into a single binary file: a sorted key
                          index plus the canonical row text. Each worker mmaps it, binary-searches,
                          and builds a Row only for the rows it is actually asked for.

B is measured twice: `cold` includes the prep pass, `warm` is the steady state where the pack is
already on disk (it is a pure function of the subset tables, so it invalidates exactly when they do).

Equivalence: each worker folds sha256 over `Row.to_tsv()` of every row it looks up, in query order;
the parent concatenates the per-worker digests in worker order. A and B must print the same value.
"""

from __future__ import annotations

import hashlib
import json
import mmap
import multiprocessing as mp
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from rebuild.validation.rowmodel import Row, format_codepoints, iter_rows  # noqa: E402

SUBSET_DIR = REPO / "rebuild" / "out" / "m1"
CONFIGS = ("default", "ss02", "ss02+ss03", "ss03", "ss04", "ss05")
PACK = HERE / "work" / "subset-pack.bin"


def _table_path(config: str) -> Path:
    return SUBSET_DIR / f"baseline-{config}.subset.tsv.gz"


# -- variant A -------------------------------------------------------------------------------------


def worker_parse(args):
    configs, keys = args
    start_cpu = time.process_time()
    tables = {}
    for config in configs:
        table = {}
        for row in iter_rows(_table_path(config)):
            table[format_codepoints(row.codepoints)] = row
        tables[config] = table
    digest = hashlib.sha256()
    hits = 0
    for config, key in keys:
        row = tables[config].get(key)
        if row is not None:
            digest.update(row.to_tsv().encode())
            digest.update(b"\n")
            hits += 1
        else:
            digest.update(b"-\n")
    return digest.hexdigest(), hits, time.process_time() - start_cpu


# -- variant B -------------------------------------------------------------------------------------


def build_pack(path: Path) -> dict:
    """One shared, mmap-able image of every subset table: per config a key-sorted fixed-stride index
    and a blob of canonical row text. Layout is little-endian; the index stride is 16 bytes."""
    blob = bytearray()
    index_blob = bytearray()
    key_blob = bytearray()
    directory = []
    for config in CONFIGS:
        entries = []
        for row in iter_rows(_table_path(config)):
            key = format_codepoints(row.codepoints).encode()
            text = row.to_tsv().encode()
            entries.append((key, len(blob), len(text)))
            blob += text
        entries.sort(key=lambda entry: entry[0])
        index_start = len(index_blob)
        for key, offset, length in entries:
            index_blob += struct.pack("<IIII", len(key_blob), len(key), offset, length)
            key_blob += key
        directory.append((config, index_start // 16, len(entries)))

    header = bytearray(b"K5P1")
    header += struct.pack("<I", len(directory))
    names = bytearray()
    for config, index_start, count in directory:
        raw = config.encode()
        header += struct.pack("<IIII", len(names), len(raw), index_start, count)
        names += raw
    base = len(header) + 16
    offsets = struct.pack(
        "<IIII",
        base,
        base + len(names),
        base + len(names) + len(index_blob),
        base + len(names) + len(index_blob) + len(key_blob),
    )
    path.write_bytes(
        bytes(header) + offsets + bytes(names) + bytes(index_blob) + bytes(key_blob) + bytes(blob)
    )
    return {"bytes": path.stat().st_size}


class Pack:
    def __init__(self, path: Path):
        self._fh = open(path, "rb")
        self._map = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        view = memoryview(self._map)
        assert bytes(view[0:4]) == b"K5P1"
        n = struct.unpack_from("<I", view, 4)[0]
        entries = [struct.unpack_from("<IIII", view, 8 + 16 * i) for i in range(n)]
        tail = 8 + 16 * n
        names_at, index_at, keys_at, blob_at = struct.unpack_from("<IIII", view, tail)
        self._view = view
        self._index_at = index_at
        self._keys_at = keys_at
        self._blob_at = blob_at
        self._configs = {}
        for name_off, name_len, index_start, count in entries:
            name = bytes(view[names_at + name_off : names_at + name_off + name_len]).decode()
            self._configs[name] = (index_start, count)

    def _key_at(self, slot: int) -> bytes:
        off, length, _o, _l = struct.unpack_from("<IIII", self._view, self._index_at + 16 * slot)
        return bytes(self._view[self._keys_at + off : self._keys_at + off + length])

    def get(self, config: str, key: str) -> Row | None:
        index_start, count = self._configs[config]
        target = key.encode()
        lo, hi = 0, count
        while lo < hi:
            mid = (lo + hi) // 2
            if self._key_at(index_start + mid) < target:
                lo = mid + 1
            else:
                hi = mid
        if lo >= count or self._key_at(index_start + lo) != target:
            return None
        _o, _l, offset, length = struct.unpack_from(
            "<IIII", self._view, self._index_at + 16 * (index_start + lo)
        )
        return Row.from_tsv(
            bytes(self._view[self._blob_at + offset : self._blob_at + offset + length]).decode()
        )


def worker_mmap(args):
    _configs, keys = args
    start_cpu = time.process_time()
    pack = Pack(PACK)
    digest = hashlib.sha256()
    hits = 0
    for config, key in keys:
        row = pack.get(config, key)
        if row is not None:
            digest.update(row.to_tsv().encode())
            digest.update(b"\n")
            hits += 1
        else:
            digest.update(b"-\n")
    return digest.hexdigest(), hits, time.process_time() - start_cpu


# -- driver ------------------------------------------------------------------------------------------


def make_keys(limit: int) -> list[tuple[str, str]]:
    """The lookups enrich actually issues: every distinct window in the M1 audit, under every config."""
    seen = []
    have = set()
    with open(REPO / "rebuild" / "out" / "m1" / "divergence-audit.tsv") as fh:
        next(fh)
        for line in fh:
            codepoints = line.split("\t", 2)[1]
            if codepoints not in have:
                have.add(codepoints)
                seen.append(codepoints)
                if len(seen) >= limit:
                    break
    return [(config, key) for key in seen for config in CONFIGS]


def run(pool_size: int, fn, keys, combine_only=False):
    chunk = (len(keys) + pool_size - 1) // pool_size
    slices = [keys[i : i + chunk] for i in range(0, len(keys), chunk)]
    payload = [(CONFIGS, part) for part in slices]
    ctx = mp.get_context("spawn")
    t = time.perf_counter()
    with ctx.Pool(pool_size) as pool:
        results = pool.map(fn, payload)
    wall = time.perf_counter() - t
    digest = hashlib.sha256()
    hits = 0
    cpu = 0.0
    for d, h, c in results:
        digest.update(d.encode())
        hits += h
        cpu += c
    return {"wall": wall, "worker_cpu": cpu, "hits": hits, "checksum": digest.hexdigest()}


def main() -> None:
    keys = make_keys(int(sys.argv[1]) if len(sys.argv) > 1 else 12000)
    result = {"lookups": len(keys), "configs": list(CONFIGS)}
    (HERE / "work").mkdir(exist_ok=True)

    t = time.perf_counter()
    pack_info = build_pack(PACK)
    prep = time.perf_counter() - t
    result["pack"] = {"seconds": prep, **pack_info}

    for n in (6, 12):
        a = run(n, worker_parse, keys)
        b = run(n, worker_mmap, keys)
        result[f"workers_{n}"] = {
            "per_worker_parse": a,
            "mmap_shared_warm": b,
            "mmap_shared_cold_wall": b["wall"] + prep,
            "checksums_match": a["checksum"] == b["checksum"],
            "wall_speedup_warm": a["wall"] / b["wall"],
            "wall_speedup_cold": a["wall"] / (b["wall"] + prep),
            "cpu_ratio": a["worker_cpu"] / b["worker_cpu"],
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
