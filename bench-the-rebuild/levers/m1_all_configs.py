"""The real six-config M1 table stage: `run_m1.build_tables` over every acceptance configuration, serially, in one process. The repo's single largest step, and the one the tracker's endpoint numbers name.

Pinned to `run_m1`'s python engine throughout: the per-configuration rows below are taken by wrapping `table.build_tables`, which only the in-process arm calls, so the kernel arm — the engine of record since the Rust cutover — would time nothing and emit no config rows at all.

Read-only on the repo. Writes nothing outside bench-the-rebuild/levers/out/, and refuses an `--out-dir` resolving anywhere else: a comparison tree symlinks its `rebuild/out` back to the real one, so a scratch directory that resolved into `rebuild/out/m1` would overwrite the artifact cycle's tables.

  m1_all_configs.py --root <dir> --mode nostore|fresh|warm --gc on|off --reps 1

Three modes, because the stage has three honest states:

  nostore  `out_dir=None, inputs=None` — nothing persisted, every configuration built and dropped in memory.
  fresh    a scratch `out_dir` plus `fingerprint.tables_value`, so every configuration's TSVs and windows are written.
  warm     the same pair with a priming pass first and `--reps` measured passes after it, so the writes land over files that already exist.

The trace memo these three names were coined against went with the Rust cutover, and `fresh` and `warm` now differ only by that priming pass. The `memo_served` / `memo_saved` / `share_served` columns of `evidence/raw/perf/calibrate/m1-all-fresh.txt` and `m1-all-warm.txt` are therefore no longer reproducible, and the rows below no longer carry them; the timing and digest columns still compare line for line.

Prints one JSON object per line: a `"kind": "config"` row per acceptance configuration and a `"kind": "total"` row per rep, carrying the field names the calibrate files use so the two compare line for line. Per-config rows time the inner `table.build_tables` fixpoint; the total times the whole `run_m1.build_tables` call, so the total less the row sum is the loop's assert and TSV-persist cost.

The digest covers the full emitted artifact per configuration — the settlement rules, every window row, the treaty rows, the reachable cells and the cited-provenance set — in the layout `m1_slice.py` hashes, so a lever that moves any of them is caught and the two harnesses agree on a table they both build. It is taken around the inner call, before `run_m1._persist_tables` drops the windows from the returned table, so one digest compares across all three modes rather than only within one. The measured tree's own `table.table_digest` is used when it has one, so a digest quoted here is the same scalar the pipeline's tests pin; the local copy below stays because the older comparison trees `mktree_at.sh` builds predate that promotion, and their object shapes are the ones it was written against. `rebuild/test_table_digest.py` holds the two in lockstep.

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
    digest_of = getattr(table_module, "table_digest", None) or table_digest

    def timed(spec, features):
        collections_before = sum(s["collections"] for s in gc.get_stats())
        t0 = time.perf_counter()
        c0 = cpu_now()
        decision, treaty = inner(spec, features)
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
                "collections": sum(s["collections"] for s in gc.get_stats()) - collections_before,
                "rss_gb": peak_rss_gb(),
                "digest": digest_of(decision, treaty),
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
        priming = run_m1.build_tables(spec, out_dir, inputs=inputs, engine="python")
        del priming

    for rep in range(args.reps):
        rows.clear()
        t0 = time.perf_counter()
        c0 = cpu_now()
        tables = run_m1.build_tables(spec, out_dir, inputs=inputs, engine="python")
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
