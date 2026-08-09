"""Fold every variant's artifact into one JSON blob on stdout, plus the derived arithmetic the pricing rests on."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(sys.argv[1])


def read_json(name: str):
    path = OUT / name
    return json.loads(path.read_text()) if path.exists() else None


def timed(name: str) -> dict:
    path = OUT / name
    if not path.exists():
        return {}
    text = path.read_text()
    got: dict = {}
    for key in ("real", "user", "sys"):
        # `/usr/bin/time -p` writes "real 1.06"; `/usr/bin/time -l` writes "  1.06 real".
        match = re.search(rf"^{key}\s+([0-9.]+)$", text, re.M) or re.search(rf"([0-9.]+)\s+{key}\b", text)
        if match:
            got[f"{key}_s"] = float(match.group(1))
    match = re.search(r"hb\.shape calls=(\d+) hb_seconds=([0-9.]+) _shape lookups=(\d+)", text)
    if match:
        got |= {
            "hb_shape_calls": int(match.group(1)),
            "hb_seconds": float(match.group(2)),
            "shape_lookups": int(match.group(3)),
        }
    match = re.search(r"(\d+) passed in ([0-9.]+)s", text)
    if match:
        got |= {"passed": int(match.group(1)), "pytest_wall_s": float(match.group(2))}
    return got


def sweep_sequences(alphabet: int, horizon: int) -> int:
    return sum(alphabet**k for k in range(1, horizon + 1))


conform = {
    name: read_json(f"conform-{name}.json")
    for name in ("h3-cold", "h4-cold", "h5-cold", "h5-warm", "h5-noboundary")
}
calt = {
    "baseline_overhead": timed("calt-baseline.log"),
    "one_shard_after2": timed("calt-shard-after2.log"),
    "all_17_sweeps": timed("calt-17-sweeps.log"),
}
depth2 = None
log = OUT / "depth2-states.log"
if log.exists():
    depth2 = json.loads(log.read_text()[log.read_text().index("{") :])

CONTEXT = 46
after_combos = {1: 1 + CONTEXT, 2: 1 + CONTEXT + CONTEXT**2}
result = {
    "harness": "cut-the-work",
    "conform_horizon": conform,
    "calt_sweep": calt,
    "depth2_states": depth2,
    "derived": {
        "calt_context_set": CONTEXT,
        "after_combos": after_combos,
        "after_2_to_1_factor": after_combos[2] / after_combos[1],
        "shaped_strings_per_shard_after2": 48 * after_combos[2],
        "shaped_strings_per_shard_after1": 48 * after_combos[1],
        "shards_per_test": CONTEXT,
        "sharded_2x2_tests": 17,
        "conform_alphabet_today": 18,
        "conform_alphabet_full_migration": 47,
        "sweep_sequences": {
            "today": {h: sweep_sequences(18, h) for h in (3, 4, 5)},
            "full_migration": {h: sweep_sequences(47, h) for h in (3, 4, 5)},
        },
    },
}
print(json.dumps(result, indent=2))
