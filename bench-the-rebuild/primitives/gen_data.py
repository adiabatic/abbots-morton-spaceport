"""Emit the shared, language-neutral inputs every k1-micro benchmark reads.

Read-only against the repo. Writes only into bench-the-rebuild/primitives/data/.

Source of truth is bench-the-rebuild/fixtures/memo-keys.tsv.gz: 1,875,829 real
settlement memo keys pulled off a real trace-memo store, ten tab-separated
fields (left_kind, cell_rune, cell_stance, seam, extension, input_rune,
right1..right4), "-" as the null marker.

Outputs
-------
symbols.txt        the union alphabet, one symbol per line; the line index is the
                   global u8 id. Line 0 is always "-" (the null marker).
keys-global.u8     N*10 bytes, row-major: global u8 id per slot.
keys-packed.u64    N*8 bytes little-endian: the same key packed as ten 5-bit
                   per-slot ids (slot s occupies bits 5s..5s+4).
slot-alphabets.txt one line per slot: tab-separated symbols in per-slot id order.
candidates.tsv     the 81 real (rune, stance, entry, exit, count) rows.
meta.json          counts and invariants the three runners assert on.

Reconstructing the string keys by indexing symbols.txt is deliberate: in the real
workload these strings come off the ResolvedSpec and are shared objects, so a
per-row fresh str would be less faithful, not more.
"""

from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "bench-the-rebuild" / "fixtures"
OUT = Path(__file__).resolve().parent / "data"

NSLOTS = 10
NULL = "-"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    rows: list[list[str]] = []
    with gzip.open(SRC / "memo-keys.tsv.gz", "rt") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            fields = line.split("\t")
            assert len(fields) == NSLOTS, fields
            rows.append(fields)

    slot_alpha: list[dict[str, int]] = []
    for s in range(NSLOTS):
        seen: dict[str, int] = {}
        for r in rows:
            v = r[s]
            if v not in seen:
                seen[v] = len(seen)
        slot_alpha.append(seen)
        assert len(seen) < 32, (s, len(seen))

    globals_: dict[str, int] = {NULL: 0}
    for s in range(NSLOTS):
        for v in slot_alpha[s]:
            if v not in globals_:
                globals_[v] = len(globals_)
    assert len(globals_) < 256, len(globals_)
    assert "" not in globals_, "empty string must not be a real symbol"

    syms = [""] * len(globals_)
    for v, i in globals_.items():
        syms[i] = v
    (OUT / "symbols.txt").write_text("\n".join(syms) + "\n")

    lines = []
    for s in range(NSLOTS):
        ordered = [""] * len(slot_alpha[s])
        for v, i in slot_alpha[s].items():
            ordered[i] = v
        lines.append("\t".join(ordered))
    (OUT / "slot-alphabets.txt").write_text("\n".join(lines) + "\n")

    gbuf = bytearray(len(rows) * NSLOTS)
    pbuf = bytearray()
    pack = struct.Struct("<Q").pack
    off = 0
    for r in rows:
        packed = 0
        for s in range(NSLOTS):
            v = r[s]
            gbuf[off] = globals_[v]
            off += 1
            packed |= slot_alpha[s][v] << (5 * s)
        pbuf += pack(packed)
    (OUT / "keys-global.u8").write_bytes(bytes(gbuf))
    (OUT / "keys-packed.u64").write_bytes(bytes(pbuf))

    cand_src = (SRC / "candidate-fields.tsv").read_text()
    (OUT / "candidates.tsv").write_text(cand_src)

    distinct_global = len({tuple(r) for r in rows})
    distinct_packed = len(set(struct.unpack(f"<{len(rows)}Q", bytes(pbuf))))

    meta = {
        "n_keys": len(rows),
        "n_slots": NSLOTS,
        "n_symbols": len(globals_),
        "null_id": 0,
        "null_symbol": NULL,
        "distinct_keys": distinct_global,
        "distinct_packed_keys": distinct_packed,
        "slot_alphabet_sizes": [len(a) for a in slot_alpha],
        "source": "bench-the-rebuild/fixtures/memo-keys.tsv.gz",
    }
    assert distinct_global == distinct_packed, (distinct_global, distinct_packed)
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
