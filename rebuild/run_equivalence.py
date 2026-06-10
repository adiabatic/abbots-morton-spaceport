"""Equivalence-triage CLI (rebuild/BASELINE-PLAN.md §6).

Usage:
    uv run python rebuild/run_equivalence.py --baseline rebuild/out/baseline-default.tsv.gz [--baseline ...] [--workers 10] [--limit N]

Runs the four §6 boundary checks over every eligible row of each baseline table and writes divergences to rebuild/out/equivalence-triage.tsv (one combined file; tables are processed in the order given, rows in baseline order, so output is deterministic).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation import equivalence
from validation.pins import REPO_ROOT
from validation.shaping import SENIOR_FONT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="append", required=True, type=Path)
    parser.add_argument("--font", type=Path, default=SENIOR_FONT)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "rebuild" / "out" / "equivalence-triage.tsv")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("# columns: " + " ".join(equivalence.TRIAGE_COLUMNS) + "\n")
        for table in args.baseline:
            counts = equivalence.run(table, fh, args.font, workers=args.workers, limit=args.limit)
            rows = counts.pop("rows", 0)
            total = sum(counts.values())
            print(f"{table}: rows={rows} divergences={total}")
            for key in sorted(counts, key=str):
                check, kind = key
                print(f"  {check} {kind}: {counts[key]}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
