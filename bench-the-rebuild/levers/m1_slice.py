"""Bounded, real M1 slice: build_tables over a rune subset of the real default spec.

Read-only on the repo. Writes nothing outside bench-the-rebuild/levers/.

  m1_slice.py --root <dir> --subset s8 --gc on|off --reps 3 [--config default]

Prints one JSON object per line: {"rep": i, "wall": s, "cpu": s, "digest": "..."}.
The digest covers the full emitted artifact (settlement TSV text + treaty TSV text +
every window row + the cited-provenance set), so a lever that moves any of them is
caught. Equivalence is exact string equality of the digest.
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

SUBSETS = {
    "s6": ["qsAh", "qsDay", "qsIt", "qsMay", "qsPea", "qsUtter"],
    "s8": ["qsAh", "qsDay", "qsIt", "qsMay", "qsPea", "qsUtter", "qsSee", "qsTea"],
    "s10": [
        "qsAh", "qsDay", "qsIt", "qsMay", "qsPea", "qsUtter", "qsSee", "qsTea", "qsOy", "qsRoe",
    ],
    "s10L": [
        "qsAh", "qsDay", "qsIt", "qsMay", "qsPea", "qsUtter", "qsSee", "qsTea",
        "qsDay_qsUtter", "qsSee_qsUtter",
    ],
    "full": None,
}


def cpu_now() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def table_digest(decision, treaty) -> str:
    h = hashlib.sha256()
    h.update(f"config\t{decision.config}\n".encode())
    for rule in decision.rules:
        h.update(
            "\t".join(
                (
                    rule.input_glyph,
                    " ".join(rule.backtrack) if rule.backtrack else "-",
                    " ".join(rule.look1) if rule.look1 else "-",
                    " ".join(rule.look2) if rule.look2 else "-",
                    " ".join(rule.look3) if rule.look3 else "-",
                    " ".join(rule.look4) if rule.look4 else "-",
                    rule.outcome,
                    "joint" if rule.joint else "-",
                    "; ".join(dict.fromkeys(p for p in rule.provenance if p)),
                )
            ).encode()
            + b"\n"
        )
    h.update(b"--windows--\n")
    for row in decision.transitions:
        h.update(
            "\t".join(
                (
                    row.input_glyph,
                    row.left,
                    row.right1,
                    row.right2,
                    row.right3,
                    row.right4,
                    row.outcome,
                )
            ).encode()
            + b"\n"
        )
    h.update(b"--treaty--\n")
    for row in treaty.rows:
        h.update(
            "\t".join((row.left, row.right, row.junction, str(row.extension), str(row.kern))).encode()
            + b"\n"
        )
    h.update(b"--cells--\n")
    for cell in sorted(
        decision.reachable_cells(),
        key=lambda c: (c.rune, c.stance, c.entry or "", c.exit or "", c.adjustments),
    ):
        h.update(f"{cell.rune}\t{cell.stance}\t{cell.entry}\t{cell.exit}\t{cell.adjustments}\n".encode())
    h.update(b"--provenance--\n")
    for pointer in sorted(decision.cited_provenance):
        h.update(pointer.encode() + b"\n")
    h.update(f"--guards--\t{decision.identity_guard_rules}\n".encode())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--subset", default="s8")
    ap.add_argument("--gc", default="on", choices=("on", "off"))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--config", default="default")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(args.root).resolve()))
    from rebuild.pipeline import conform
    from rebuild.pipeline import table as table_module
    from rebuild.pipeline.spec_load import load_default_spec

    spec = load_default_spec()
    names = SUBSETS[args.subset]
    if names is not None:
        spec = dataclasses.replace(spec, runes={k: v for k, v in spec.runes.items() if k in names})
    features = conform.features_for_config(args.config)

    if args.gc == "off":
        gc.collect()
        gc.freeze()
        gc.disable()

    for rep in range(args.reps):
        gc_before = sum(s["collections"] for s in gc.get_stats())
        t0 = time.perf_counter()
        c0 = cpu_now()
        decision, treaty = table_module.build_tables(spec, features, trace_store=None, share=None)
        wall = time.perf_counter() - t0
        cpu = cpu_now() - c0
        gc_after = sum(s["collections"] for s in gc.get_stats())
        print(
            json.dumps(
                {
                    "label": args.label,
                    "root": args.root,
                    "subset": args.subset,
                    "gc": args.gc,
                    "rep": rep,
                    "wall": round(wall, 4),
                    "cpu": round(cpu, 4),
                    "collections": gc_after - gc_before,
                    "rules": len(decision.rules),
                    "windows": len(decision.transitions),
                    "treaty_rows": len(treaty.rows),
                    "digest": table_digest(decision, treaty),
                }
            ),
            flush=True,
        )
        del decision, treaty
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
