"""k1-micro: primitive-operation benchmark, Python side.

Eight kernels on data shapes lifted from the real M1 settlement fixpoint. The
Rust (rust/src/main.rs) and Go (go/main.go) runners implement the SAME kernels on
the SAME bytes out of data/, and every kernel emits a portable checksum that must
agree across all three.

Conventions shared by all three runners
---------------------------------------
* Timing: monotonic wall clock, WARMUP untimed reps then `reps` timed reps;
  median / min / max / spread reported.
* Dead-code defence: every timed loop stores its product into a preallocated heap
  buffer (or a package-level sink) AND folds a cheap portable integer into an
  accumulator that is printed and checked. CPython does no dead-store
  elimination, but the loops are written identically in all three languages so
  the Rust and Go optimisers face the same escaping store.
* "control" is the same loop with the kernel op removed; net = raw - control.
  Both are reported so the reader can judge the subtraction.
* Equivalence: the strong checksums are computed in SEPARATE, UNTIMED verify
  passes, so checksum arithmetic never lands inside a measured loop. A verify
  pass reads the record's fields back and maps them through the symbol table, so
  it proves the record holds the right values, not merely the right count.
"""

from __future__ import annotations

import gc
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns, process_time_ns
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

sys.path.insert(0, str(HERE.parents[1]))

from rebuild.tools.peak_rss import bytes_to_gb, peak_rss_self_bytes  # noqa: E402

MASK = (1 << 64) - 1
FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3

M1 = 400_000
M8 = 700_000
NPROBE = 1_000_000
NALLOC = 10_000_000
NLISTS = 60_000
NLEGACY = 500_000
REPS = 5
MED_REPS = 3
HEAVY_REPS = 2
WARMUP = 1
VERIFY_CAP = 200_000
PROBE_MUL = 2654435761
MISS = "MISS"
FILTER_B = ("qsNo", "qsMay", "qsPea")

RESULTS: list[dict] = []


def mix(h: int, v: int) -> int:
    """Order-sensitive 64-bit mixer. FNV-1a's prime, folded a whole u64 at a time
    so the verify passes stay cheap in CPython. Rust and Go implement it with
    `(h ^ v).wrapping_mul(PRIME)`, which is bit-for-bit this."""
    return ((h ^ (v & MASK)) * FNV_PRIME) & MASK


def settle_heap() -> None:
    """Take the harness's own long-lived data out of CPython's GC scan set. It
    inflates every kernel otherwise, and it is an artefact of the harness holding
    millions of setup objects alive, not of the operation being measured."""
    gc.collect()
    gc.freeze()


def timeit(name, fn, control, ops, extra=None, reps=REPS):
    for _ in range(WARMUP):
        acc = fn()
    raw = []
    for _ in range(reps):
        t0 = perf_counter_ns()
        acc = fn()
        raw.append(perf_counter_ns() - t0)
    ctl: list[int] = []
    if control is not None:
        for _ in range(WARMUP):
            control()
        for _ in range(reps):
            t0 = perf_counter_ns()
            control()
            ctl.append(perf_counter_ns() - t0)
    raw.sort()
    med = raw[len(raw) // 2]
    rec = {
        "op": name,
        "lang": "python",
        "ops": ops,
        "raw_ns_per_op": med / ops,
        "min_ns_per_op": raw[0] / ops,
        "max_ns_per_op": raw[-1] / ops,
        "spread_pct": 100.0 * (raw[-1] - raw[0]) / med if med else 0.0,
        "reps": reps,
        "acc": str(acc),
    }
    if ctl:
        ctl.sort()
        cmed = ctl[len(ctl) // 2]
        rec["control_ns_per_op"] = cmed / ops
        rec["net_ns_per_op"] = (med - cmed) / ops
    else:
        rec["control_ns_per_op"] = 0.0
        rec["net_ns_per_op"] = med / ops
    if extra:
        rec.update(extra)
    RESULTS.append(rec)
    print(
        f"  {name:44s} net {rec['net_ns_per_op']:9.2f}  raw {rec['raw_ns_per_op']:9.2f} ns/op",
        file=sys.stderr,
    )
    return rec


# --------------------------------------------------------------------- loading

meta = json.loads((DATA / "meta.json").read_text())
N = meta["n_keys"]
SYMS = (DATA / "symbols.txt").read_text().split("\n")[:-1]
assert SYMS[0] == "-"
SYM_ID = {s: i for i, s in enumerate(SYMS)}
GBUF = (DATA / "keys-global.u8").read_bytes()
PBUF = (DATA / "keys-packed.u64").read_bytes()
assert len(GBUF) == N * 10 and len(PBUF) == N * 8

PACKED = [int.from_bytes(PBUF[i * 8 : i * 8 + 8], "little") for i in range(N)]
SYMN: list[str | None] = [None] + SYMS[1:]  # id 0 ("-") is the null marker
KEYS: list[tuple] = [tuple(map(SYMN.__getitem__, GBUF[i * 10 : i * 10 + 10])) for i in range(N)]

# 8-field record, drawn slot-wise from the real key:
#   a=left_kind, b=input_rune (never null), c=cell_rune, d=cell_stance,
#   e=seam (nullable), n1=extension, n2=i%97, n3=i%13
FIELDS = [(s[0], s[5], s[1], s[2], s[3], int(s[4]), i % 97, i % 13) for i, s in enumerate(KEYS[:M1])]
settle_heap()


@dataclass(frozen=True)
class R8:
    a: str
    b: str
    c: str | None
    d: str | None
    e: str | None
    n1: int
    n2: int
    n3: int


@dataclass(frozen=True, slots=True)
class R8S:
    a: str
    b: str
    c: str | None
    d: str | None
    e: str | None
    n1: int
    n2: int
    n3: int


class R8N(NamedTuple):
    a: str
    b: str
    c: str | None
    d: str | None
    e: str | None
    n1: int
    n2: int
    n3: int


@dataclass(frozen=True)
class Cand5:
    stance: str
    entry: str | None
    seam: str | None
    order_index: int
    exit_index: int = 9999


def verify_records(buf, get) -> str:
    h = FNV_OFFSET
    for j in range(min(VERIFY_CAP, len(buf))):
        a, b, c, d, e, n1, n2, n3 = get(buf[j])
        for v in (a, b, c, d, e):
            h = mix(h, 0 if v is None else SYM_ID[v])
        h = mix(h, n1)
        h = mix(h, n2)
        h = mix(h, n3)
    return str(h)


# ------------------------------------------------------------- B1 construct


def bench_construct():
    print("B1 construct 8-field record", file=sys.stderr)
    buf: list = [None] * M1
    fields = FIELDS

    def ctl():
        acc = 0
        for j, (a, b, c, d, e, n1, n2, n3) in enumerate(fields):
            buf[j] = a
            acc ^= n2
        return acc

    def mk(cls):
        def run():
            acc = 0
            for j, (a, b, c, d, e, n1, n2, n3) in enumerate(fields):
                buf[j] = cls(a, b, c, d, e, n1, n2, n3)
                acc ^= n2
            return acc

        return run

    def run_tuple():
        acc = 0
        for j, (a, b, c, d, e, n1, n2, n3) in enumerate(fields):
            buf[j] = (a, b, c, d, e, n1, n2, n3)
            acc ^= n2
        return acc

    def run_dict():
        acc = 0
        for j, (a, b, c, d, e, n1, n2, n3) in enumerate(fields):
            buf[j] = {"a": a, "b": b, "c": c, "d": d, "e": e, "n1": n1, "n2": n2, "n3": n3}
            acc ^= n2
        return acc

    obj = lambda r: (r.a, r.b, r.c, r.d, r.e, r.n1, r.n2, r.n3)
    tup = lambda r: r
    dct = lambda r: (r["a"], r["b"], r["c"], r["d"], r["e"], r["n1"], r["n2"], r["n3"])

    for label, fn, unpack in (
        ("construct8/frozen-dataclass", mk(R8), obj),
        ("construct8/frozen-dataclass-slots", mk(R8S), obj),
        ("construct8/namedtuple", mk(R8N), obj),
        ("construct8/plain-tuple", run_tuple, tup),
        ("construct8/dict", run_dict, dct),
    ):
        fn()  # leave buf holding this variant's records, then verify before ctl overwrites them
        checksum = verify_records(buf, unpack)
        timeit(label, fn, ctl, M1, {"checksum": checksum})


def bench_legacy_candidate():
    """attr-overhead K7's exact 5-field settle.Candidate shape, to anchor this
    harness against its previously measured 375.4 / 361.7 / 32.0 / 107.0 / 21.2 ns."""
    print("B1x legacy 5-field Candidate (anchor on prior measurement)", file=sys.stderr)
    rows = [line.split("\t") for line in (DATA / "candidates.tsv").read_text().splitlines()[1:] if line]
    fields = [
        (r[1], None if r[2] == "-" else r[2], None if r[3] == "-" else r[3], i, i + 1)
        for i, r in enumerate(rows)
    ]
    reps_in = NLEGACY // len(fields)
    n = reps_in * len(fields)
    buf: list = [None] * len(fields)

    def ctl():
        acc = 0
        for _ in range(reps_in):
            for j, (s, e, seam, oi, xi) in enumerate(fields):
                buf[j] = s
                acc ^= oi
        return acc

    def run_dc():
        acc = 0
        for _ in range(reps_in):
            for j, (s, e, seam, oi, xi) in enumerate(fields):
                buf[j] = Cand5(s, e, seam, oi, xi)
                acc ^= oi
        return acc

    def run_tup():
        acc = 0
        for _ in range(reps_in):
            for j, (s, e, seam, oi, xi) in enumerate(fields):
                buf[j] = (s, e, seam, oi, xi)
                acc ^= oi
        return acc

    settle_heap()
    timeit("legacy5/frozen-dataclass-construct", run_dc, ctl, n, reps=MED_REPS)
    timeit("legacy5/plain-tuple-construct", run_tup, ctl, n, reps=MED_REPS)

    objs = [Cand5(*f) for f in fields]
    tups = [tuple(f) for f in fields]

    def hash_dc():
        acc = 0
        for _ in range(reps_in):
            for o in objs:
                acc ^= hash(o)
        return acc & MASK

    def hash_tup():
        acc = 0
        for _ in range(reps_in):
            for t in tups:
                acc ^= hash(t)
        return acc & MASK

    pre_hashes = [hash(o) for o in objs]

    def hash_ctl():
        acc = 0
        for _ in range(reps_in):
            for h in pre_hashes:
                acc ^= h
        return acc & MASK

    timeit("legacy5/frozen-dataclass-hash", hash_dc, hash_ctl, n, reps=MED_REPS)
    timeit("legacy5/plain-tuple-hash", hash_tup, hash_ctl, n, reps=MED_REPS)


# ------------------------------------------------------------------ B2 hash


def bench_hash():
    print("B2 hash 8-field record", file=sys.stderr)
    dc = [R8(*f) for f in FIELDS]
    nt = [R8N(*f) for f in FIELDS]
    tp = [tuple(f) for f in FIELDS]

    def ctl(src):
        pre = [hash(o) for o in src]

        def run():
            acc = 0
            for h in pre:
                acc ^= h
            return acc & MASK

        return run

    def mk(src):
        def run():
            acc = 0
            for o in src:
                acc ^= hash(o)
            return acc & MASK

        return run

    for label, src in (
        ("hash8/frozen-dataclass", dc),
        ("hash8/namedtuple", nt),
        ("hash8/plain-tuple", tp),
    ):
        settle_heap()
        timeit(label, mk(src), ctl(src), M1, {"distinct_hash_values": len({hash(o) for o in src})})


# -------------------------------------------------------------------- B3 eq


def bench_eq():
    print("B3 equality compare", file=sys.stderr)
    dc = [R8(*f) for f in FIELDS]
    dc2 = [R8(*f) for f in FIELDS]
    dc_shift = dc2[1:] + dc2[:1]
    tp = [tuple(f) for f in FIELDS]
    tp2 = [tuple(f) for f in FIELDS]
    tp_shift = tp2[1:] + tp2[:1]

    def ctl(x, y):
        def run():
            c = 0
            for j in range(M1):
                c += x[j] is y[j]
            return c

        return run

    def eq(x, y):
        def run():
            c = 0
            for j in range(M1):
                c += x[j] == y[j]
            return c

        return run

    settle_heap()
    for label, x, y, z in (
        ("eq8/frozen-dataclass", dc, dc2, dc_shift),
        ("eq8/plain-tuple", tp, tp2, tp_shift),
    ):
        timeit(label + "-equal", eq(x, y), ctl(x, y), M1)
        timeit(label + "-unequal", eq(x, z), ctl(x, z), M1)
    timeit("eq8/frozen-dataclass-identical-shortcut", eq(dc, dc), ctl(dc, dc2), M1)


# ---------------------------------------------------------------- B5 symbols


def bench_symbols():
    print("B5 interned strings vs u8 symbol ids", file=sys.stderr)
    strs = KEYS[:M1]
    strs2 = [tuple(s) for s in KEYS[:M1]]
    ids = [bytes(GBUF[i * 10 : i * 10 + 10]) for i in range(M1)]
    ids2 = [bytes(GBUF[i * 10 : i * 10 + 10]) for i in range(M1)]
    pk = PACKED[:M1]
    pk2 = [v + 0 for v in pk]

    def ctl(x, y):
        def run():
            c = 0
            for j in range(M1):
                c += x[j] is y[j]
            return c

        return run

    def eq(x, y):
        def run():
            c = 0
            for j in range(M1):
                c += x[j] == y[j]
            return c

        return run

    def hsh(src):
        def run():
            acc = 0
            for o in src:
                acc ^= hash(o)
            return acc & MASK

        return run

    def hctl(src):
        pre = [hash(o) for o in src]

        def run():
            acc = 0
            for h in pre:
                acc ^= h
            return acc & MASK

        return run

    settle_heap()
    timeit("sym/eq-10str-tuple", eq(strs, strs2), ctl(strs, strs2), M1)
    timeit("sym/eq-10u8-bytes", eq(ids, ids2), ctl(ids, ids2), M1)
    timeit("sym/eq-packed-u64", eq(pk, pk2), ctl(pk, pk2), M1)
    timeit("sym/hash-10str-tuple", hsh(strs), hctl(strs), M1)
    timeit("sym/hash-10u8-bytes", hsh(ids), hctl(ids), M1)
    timeit("sym/hash-packed-u64", hsh(pk), hctl(pk), M1)


# ------------------------------------------------------------------ B6 rank


def bench_rank():
    print("B6 rank a 3-8 candidate list by a multi-key predicate", file=sys.stderr)
    rows = [line.split("\t") for line in (DATA / "candidates.tsv").read_text().splitlines()[1:] if line]
    stances = sorted({r[1] for r in rows})
    sid = {s: i for i, s in enumerate(stances)}
    seam_y = {"-": -1, "ex-y0": 0, "ex-y5": 5, "ex-y6": 6}
    base = [(sid[r[1]], seam_y[r[3]], int(r[4])) for r in rows]
    nc = len(base)

    lists = []
    for j in range(NLISTS):
        L = 3 + (j % 6)
        item = []
        for k in range(L):
            st, sy, cnt = base[(j * 7 + k * 13) % nc]
            item.append((st, sy, k, (j + k) % 11, cnt % 5))
        lists.append(item)

    # floor_key mirrors settle.py:1259 -- joined first, then seam height, then exit row order
    def floor_key(c):
        return (1 if c[1] < 0 else 0, 1_000_000 if c[1] < 0 else c[1], c[2])

    # rank_key mirrors settle.py:1278 -- join count desc, then stance order, then exit row order
    def rank_key(c):
        return (-c[4], c[3], c[2])

    def run():
        acc = 0
        for item in lists:
            o = sorted(item, key=floor_key)
            acc ^= o[0][2] * 3 + o[1][2] * 5
            r = sorted(item, key=rank_key)
            acc ^= r[0][3] * 7
        return acc & MASK

    def ctl():
        acc = 0
        for item in lists:
            acc ^= item[0][2] * 3 + item[1][2] * 5
            acc ^= item[0][3] * 7
        return acc & MASK

    h = FNV_OFFSET
    for item in lists:
        o = sorted(item, key=floor_key)
        r = sorted(item, key=rank_key)
        h = mix(h, o[0][0])
        h = mix(h, o[1][2])
        h = mix(h, r[0][0])
        h = mix(h, r[0][3])
    settle_heap()
    timeit("rank/two-stable-sorts-per-list", run, ctl, NLISTS, {"checksum": str(h), "lists": NLISTS})


# ---------------------------------------------------------------- B8 filter


def bench_filter():
    print("B8 filter a %d-row table of 8-field records" % M8, file=sys.stderr)
    flds = [(s[0], s[5], s[1], s[2], s[3], int(s[4]), i % 97, i % 13) for i, s in enumerate(KEYS[:M8])]
    dc = [R8(*f) for f in flds]
    nt = [R8N(*f) for f in flds]
    tp = [tuple(f) for f in flds]

    def run_obj(src):
        def run():
            c = 0
            acc = 0
            for r in src:
                if (
                    r.c is not None
                    and r.d is not None
                    and (r.b == "qsNo" or r.b == "qsMay" or r.b == "qsPea")
                    and r.n1 >= 0
                    and r.a != "space"
                ):
                    c += 1
                    acc ^= r.n2
            return (c << 8) ^ acc

        return run

    def run_tup(src):
        def run():
            c = 0
            acc = 0
            for r in src:
                if (
                    r[2] is not None
                    and r[3] is not None
                    and (r[1] == "qsNo" or r[1] == "qsMay" or r[1] == "qsPea")
                    and r[5] >= 0
                    and r[0] != "space"
                ):
                    c += 1
                    acc ^= r[6]
            return (c << 8) ^ acc

        return run

    def ctl(src):
        def run():
            c = 0
            for r in src:
                c += 1
            return c << 8

        return run

    matched = 0
    h = FNV_OFFSET
    for f in flds:
        if f[2] is not None and f[3] is not None and f[1] in FILTER_B and f[5] >= 0 and f[0] != "space":
            matched += 1
            for v in f[:5]:
                h = mix(h, 0 if v is None else SYM_ID[v])
            h = mix(h, f[5])
            h = mix(h, f[6])
            h = mix(h, f[7])

    extra = {"matched": matched, "checksum": str(h)}
    settle_heap()
    timeit("filter700k/frozen-dataclass", run_obj(dc), ctl(dc), M8, extra, reps=MED_REPS)
    timeit("filter700k/namedtuple-attr", run_obj(nt), ctl(nt), M8, extra, reps=MED_REPS)
    timeit("filter700k/plain-tuple-index", run_tup(tp), ctl(tp), M8, extra, reps=MED_REPS)


# ------------------------------------------------------------------- B4 map


def bench_map():
    print("B4 map with a 10-slot optional-string key, N=%d" % N, file=sys.stderr)
    keys = KEYS

    def build():
        d = {}
        for i, k in enumerate(keys):
            d[k] = i
        return len(d)

    settle_heap()
    timeit("map10str/insert", build, None, N, reps=HEAVY_REPS)
    store = {k: i for i, k in enumerate(keys)}
    assert len(store) == N

    probes = []
    for p in range(NPROBE):
        k = keys[(p * PROBE_MUL) % N]
        probes.append(k[:9] + (MISS,) if p % 4 == 3 else k)

    def lookup():
        acc = 0
        hits = 0
        get = store.get
        for k in probes:
            v = get(k)
            if v is not None:
                hits += 1
                acc += v
        return ((acc & MASK) << 1) ^ hits

    def lookup_ctl():
        acc = 0
        for k in probes:
            acc ^= 1
        return acc

    hits = sum(1 for k in probes if k in store)
    acc = sum(store[k] for k in probes if k in store) & MASK
    timeit("map10str/lookup", lookup, lookup_ctl, NPROBE, {"hits": hits, "checksum": str(acc)}, reps=MED_REPS)
    del store, probes
    settle_heap()

    def build_p():
        d = {}
        for i, k in enumerate(PACKED):
            d[k] = i
        return len(d)

    timeit("mapU64/insert", build_p, None, N, reps=HEAVY_REPS)
    storep = {k: i for i, k in enumerate(PACKED)}
    probesp = []
    for p in range(NPROBE):
        k = PACKED[(p * PROBE_MUL) % N]
        probesp.append((k & ~(31 << 45)) | (31 << 45) if p % 4 == 3 else k)

    def lookup_p():
        acc = 0
        hits = 0
        get = storep.get
        for k in probesp:
            v = get(k)
            if v is not None:
                hits += 1
                acc += v
        return ((acc & MASK) << 1) ^ hits

    def lookup_p_ctl():
        acc = 0
        for k in probesp:
            acc ^= 1
        return acc

    hitsp = sum(1 for k in probesp if k in storep)
    accp = sum(storep[k] for k in probesp if k in storep) & MASK
    timeit(
        "mapU64/lookup", lookup_p, lookup_p_ctl, NPROBE, {"hits": hitsp, "checksum": str(accp)}, reps=MED_REPS
    )
    del storep, probesp


# ----------------------------------------------------------------- B7 alloc


def bench_alloc():
    print("B7 allocate and drop %d small objects" % NALLOC, file=sys.stderr)
    slots: list = [None] * 8
    a, b, c, d, e, n1 = FIELDS[0][:6]

    def run_dc():
        acc = 0
        for i in range(NALLOC):
            n2 = i % 97
            slots[i & 7] = R8(a, b, c, d, e, n1, n2, i % 13)
            acc ^= n2
        return acc

    def run_tuple():
        acc = 0
        for i in range(NALLOC):
            n2 = i % 97
            slots[i & 7] = (a, b, c, d, e, n1, n2, i % 13)
            acc ^= n2
        return acc

    def ctl():
        acc = 0
        for i in range(NALLOC):
            n2 = i % 97
            slots[i & 7] = a
            acc ^= n2
        return acc

    settle_heap()
    timeit("alloc10M/frozen-dataclass", run_dc, ctl, NALLOC, reps=HEAVY_REPS)
    timeit("alloc10M/plain-tuple", run_tuple, ctl, NALLOC, reps=HEAVY_REPS)


def main() -> None:
    t0 = perf_counter_ns()
    bench_construct()
    bench_legacy_candidate()
    bench_hash()
    bench_eq()
    bench_symbols()
    bench_rank()
    bench_filter()
    bench_map()
    bench_alloc()
    out = {
        "lang": "python",
        "runtime": sys.version.split()[0],
        "meta": meta,
        "wall_s": (perf_counter_ns() - t0) / 1e9,
        "cpu_s": process_time_ns() / 1e9,
        "peak_rss_gb": bytes_to_gb(peak_rss_self_bytes()),
        "results": RESULTS,
    }
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "python.json").write_text(json.dumps(out, indent=1) + "\n")
    print("python bench wall %.1f s" % out["wall_s"], file=sys.stderr)


if __name__ == "__main__":
    main()
