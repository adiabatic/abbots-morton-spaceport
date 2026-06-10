"""Corpus-pin replay CLI (rebuild/BASELINE-PLAN.md §7).

Usage:
    uv run python rebuild/validate_pins.py
    uv run python rebuild/validate_pins.py --baseline rebuild/out/baseline-default.tsv.gz --baseline rebuild/out/baseline-ss02.tsv.gz

Without --baseline it replays every eligible corpus pin against live black-box shaping plus the GPOS seam classifier. Each --baseline adds the table cross-check for pins whose configuration matches that table's header. Exits nonzero on any disagreement; depth-2-horizon findings are reported but never fail.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validation.classify import SeamClassifier
from validation.pins import (
    REPO_ROOT,
    ReplayReport,
    check_against_baseline,
    check_pin,
    collect_pin_runs,
)
from validation.rowmodel import header_config_token, read_header
from validation.shaping import SENIOR_FONT, Shaper

REPORT_COLUMNS = ("kind", "source", "config", "codepoints", "expect", "detail")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="append", default=[], type=Path)
    parser.add_argument("--font", type=Path, default=SENIOR_FONT)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "rebuild" / "out" / "pin-disagreements.tsv")
    args = parser.parse_args(argv)

    tables: dict[str, Path] = {}
    for table in args.baseline:
        token = header_config_token(read_header(table))
        if token in tables:
            raise SystemExit(f"two baseline tables claim config {token!r}")
        tables[token] = table

    report = ReplayReport()
    pins = collect_pin_runs(report)
    shaper = Shaper(args.font)
    classifier = SeamClassifier(args.font)

    pins_with_rows = [(pin, check_pin(shaper, classifier, pin, report)) for pin in pins]
    if tables:
        check_against_baseline(pins_with_rows, tables, report)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("# columns: " + " ".join(REPORT_COLUMNS) + "\n")
        for entry in [*report.disagreements, *report.horizon_findings]:
            fh.write(entry.to_tsv() + "\n")

    config_counts = Counter(pin.config_token for pin in pins)
    print(f"cells with pins: {report.cells_total}")
    print(f"runs total: {report.runs_total}")
    print(
        f"runs replayed: {report.runs_replayed} "
        f"({', '.join(f'{token}={n}' for token, n in sorted(config_counts.items()))})"
    )
    print(
        f"runs skipped: junior={report.skipped_junior} non-basis={report.skipped_non_basis} "
        f"config-not-covered={report.skipped_config}"
    )
    print(
        f"assertions: seams={report.seam_assertions_checked} identity={report.identity_assertions_checked} "
        f"variant-skipped={report.variant_assertions_skipped}"
    )
    if tables:
        print(
            f"baseline cross-check: rows={report.baseline_rows_checked} "
            f"windows={report.baseline_windows_checked} (tables: {', '.join(sorted(tables))})"
        )
    print(f"depth-2-horizon findings: {len(report.horizon_findings)}")
    print(f"disagreements: {report.failure_count} -> {args.out}")
    for entry in report.disagreements[:20]:
        print(f"  {entry.kind} {entry.source} [{entry.config}] {entry.expect!r}: {entry.detail}")
    if report.failure_count > 20:
        print(f"  ... and {report.failure_count - 20} more")
    return 1 if report.failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
