"""Split-buffer cross-check CLI (rebuild/BASELINE-PLAN.md §4).

Usage:
    uv run python rebuild/run_split_check.py --baseline rebuild/out/baseline-default.tsv.gz [--baseline ...] [--workers 10] [--limit N]

Checks every length-2 break seam and a deterministic 1% sample of length-3/4 rows containing a break seam; disagreements (current-font isolation leaks, not extraction failures) go to rebuild/out/split-check-disagreements.tsv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation import split_check
from validation.pins import REPO_ROOT
from validation.shaping import SENIOR_FONT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="append", required=True, type=Path)
    parser.add_argument("--font", type=Path, default=SENIOR_FONT)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "rebuild" / "out" / "split-check-disagreements.tsv"
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("# columns: " + " ".join(split_check.DISAGREEMENT_COLUMNS) + "\n")
        for table in args.baseline:
            counts = split_check.run(table, fh, args.font, workers=args.workers, limit=args.limit)
            print(
                f"{table}: rows={counts['rows']} seams_checked={counts['seams_checked']} "
                f"kern_only={counts['kern_only']} disagreements={counts['disagreements']}"
            )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
