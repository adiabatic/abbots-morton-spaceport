"""The real six-config M1 table stage: `run_m1.build_tables` over every acceptance configuration, serially, on one in-process `trace_memo.TraceShare`. The repo's single largest step, and the one the tracker's endpoint numbers name.

Read-only on the repo. Writes nothing outside bench-the-rebuild/levers/out/, and refuses an `--out-dir` resolving anywhere else: a comparison tree symlinks its `rebuild/out` back to the real one, so a scratch directory that resolved into `rebuild/out/m1` would overwrite the artifact cycle's tables.

  m1_all_configs.py --root <dir> --mode nostore|fresh|warm --gc on|off --reps 1

Three modes, because the stage has three honest states:

  nostore  `out_dir=None, inputs=None` — no persisted trace memo at all, the share only.
  fresh    a scratch `out_dir` plus `fingerprint.tables_value`, with `fresh_memo` set, so every window re-traces and the memo is rewritten. This is the state `evidence/raw/perf/calibrate/m1-all-fresh.txt` was measured in — its rows carry a `memo_saved`, which `nostore` cannot produce, and the memo write is ~17 s per configuration of the difference.
  warm     the same pair without `fresh_memo`, a priming pass first and `--reps` measured passes after it, so every memo entry is valid and served. The shape of `m1-all-warm.txt`.

Prints one JSON object per line: a `"kind": "config"` row per acceptance configuration and a `"kind": "total"` row per rep, carrying the field names the calibrate files use so the two compare line for line. Per-config rows time the inner `table.build_tables` fixpoint, which is where `trace_store.save` lives; the total times the whole `run_m1.build_tables` call, so the total less the row sum is the loop's store-open, assert and TSV-persist cost.

The digest covers the full emitted artifact per configuration — the settlement rules, every window row, the treaty rows, the reachable cells and the cited-provenance set — in the layout `m1_slice.py` hashes, so a lever that moves any of them is caught and the two harnesses agree on a table they both build. It is taken around the inner call, before `run_m1._persist_tables` drops the windows from the returned table, so one digest compares across all three modes rather than only within one.

`--gc off`, the default, collects, freezes and disables once the spec is loaded, which is what `run_m1`'s own `__main__` does to the real run; `--gc on` leaves the collector alone so both arms are reproducible.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

LEVERS_OUT = Path(__file__).resolve().parent / "out"


def cpu_now() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def peak_rss_gb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak * (1 if sys.platform == "darwin" else 1024) / 1e9, 2)


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
            "\t".join((row.left, row.right, row.junction, str(row.extension), str(row.kern))).encode() + b"\n"
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


def instrument_configs(table_module, rows: list[dict]) -> None:
    inner = table_module.build_tables

    def timed(spec, features, trace_store=None, share=None):
        collections_before = sum(s["collections"] for s in gc.get_stats())
        t0 = time.perf_counter()
        c0 = cpu_now()
        decision, treaty = inner(spec, features, trace_store=trace_store, share=share)
        wall = time.perf_counter() - t0
        cpu = cpu_now() - c0
        rows.append(
            {
                "config": decision.config,
                "wall_s": round(wall, 2),
                "cpu_s": round(cpu, 2),
                "windows": len(decision.transitions),
                "rules": len(decision.rules),
                "treaty_rows": len(treaty.rows),
                "share_served": getattr(getattr(share, "last_reader", None), "served", 0),
                "memo_served": trace_store.served if trace_store is not None else 0,
                "memo_saved": trace_store.saved if trace_store is not None else 0,
                "collections": sum(s["collections"] for s in gc.get_stats()) - collections_before,
                "rss_gb": peak_rss_gb(),
                "digest": table_digest(decision, treaty),
            }
        )
        return decision, treaty

    table_module.build_tables = timed


def scratch_out_dir(requested: str, root: Path) -> Path:
    out_dir = Path(requested).resolve() if requested else (LEVERS_OUT / "m1-all" / root.name).resolve()
    if LEVERS_OUT not in out_dir.parents:
        raise SystemExit(f"refusing out_dir {out_dir}: it must resolve under {LEVERS_OUT}")
    return out_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--mode", default="fresh", choices=("nostore", "fresh", "warm"))
    ap.add_argument("--gc", default="off", choices=("on", "off"))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    from rebuild.pipeline import fingerprint, run_m1
    from rebuild.pipeline import table as table_module
    from rebuild.pipeline.spec_load import load_default_spec

    if run_m1.REPO_ROOT != root:
        raise SystemExit(f"imported rebuild.pipeline from {run_m1.REPO_ROOT}, not --root {root}")

    out_dir = None if args.mode == "nostore" else scratch_out_dir(args.out_dir, root)

    t0 = time.perf_counter()
    spec = load_default_spec()
    spec_load_wall = time.perf_counter() - t0

    inputs = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        inputs = fingerprint.tables_value(root)

    if args.gc == "off":
        gc.collect()
        gc.freeze()
        gc.disable()

    common = {"label": args.label, "root": str(root), "mode": args.mode, "gc": args.gc}
    rows: list[dict] = []
    instrument_configs(table_module, rows)

    if args.mode == "warm":
        priming = run_m1.build_tables(spec, out_dir, inputs=inputs, fresh_memo=False)
        del priming

    for rep in range(args.reps):
        rows.clear()
        t0 = time.perf_counter()
        c0 = cpu_now()
        tables = run_m1.build_tables(spec, out_dir, inputs=inputs, fresh_memo=args.mode == "fresh")
        wall = time.perf_counter() - t0
        cpu = cpu_now() - c0
        del tables
        for row in rows:
            print(json.dumps({"kind": "config", **common, "rep": rep, **row}), flush=True)
        print(
            json.dumps(
                {
                    "kind": "total",
                    **common,
                    "rep": rep,
                    "configs": len(rows),
                    "spec_load_wall_s": round(spec_load_wall, 3),
                    "total_wall_s": round(wall, 2),
                    "total_cpu_s": round(cpu, 2),
                    "peak_rss_gb": peak_rss_gb(),
                    "out_dir": str(out_dir) if out_dir is not None else None,
                    "digest": hashlib.sha256(
                        "\n".join(f"{r['config']}\t{r['digest']}" for r in rows).encode()
                    ).hexdigest(),
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
