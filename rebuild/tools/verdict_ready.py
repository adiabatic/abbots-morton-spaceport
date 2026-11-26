"""Print whether the review corpus is ready to adjudicate now.

It runs `rebuild.review.status.compute_status` over the production paths, adds a warning once the verdict journal's first event is old enough that a close-out is due (`rebuild.review.journal.close_out_due`), adds a check that a review server for this checkout is listening on port 7294 (one that names another checkout as its root fails it), and prints a checklist, or the status dict as JSON under `--json`. It exits 0 only when every blocking check passes and this checkout's server is up, so it can serve as a Makefile guard. The artifact cycle calls `readiness` and `checklist` to print the same checklist at the end of every green pass that is not a staging pass, so this CLI is for asking the question between passes.

The rows follow the corpus's mode. An m1-audit corpus prints `CHECK_ORDER`. A table-diff corpus prints `TABLE_DIFF_CHECK_ORDER`, with the snapshot and diff rows and without the fullest-verdicts and journal rows, since its store lasts from one accept to the next and no close-out is due; with no unaccepted change its ready line says so. `diff_summary_line` is the one-line count of its unaccepted changes for the cycle's summary.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from rebuild.review import journal, status
from rebuild.tools.review_server import other_checkout_root, server_listening

ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = ROOT / "rebuild" / "out" / "review"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CYCLE_SUMMARY_PATH = ROOT / "rebuild" / "out" / "cycle_summary.json"
AUTOSAVE_PATH = ROOT / "verdicts-autosave.json"
QUEUE_URL = "http://localhost:7294/#view=queue"

CHECK_ORDER = (
    "corpus",
    "freshness",
    "gates",
    "verdict_store",
    "fullest_verdicts",
    "blanks",
    "journal",
    "server",
)
TABLE_DIFF_CHECK_ORDER = (
    "corpus",
    "freshness",
    "gates",
    "snapshot",
    "verdict_store",
    "blanks",
    "diff",
    "server",
)


def table_diff(result: dict) -> bool:
    """Whether the status dict describes a table-diff corpus."""
    return (result.get("corpus") or {}).get("mode") == "table-diff"


def readiness(
    *,
    with_server: bool = True,
    repo_root: Path = ROOT,
    review_dir: Path = REVIEW_DIR,
    m1_out: Path = M1_OUT,
    autosave_path: Path = AUTOSAVE_PATH,
    cycle_summary_path: Path = CYCLE_SUMMARY_PATH,
    recompute: Callable | None = None,
    listening: Callable[[], bool] = server_listening,
    other_root: Callable[[Path], str | None] = other_checkout_root,
    recompute_table_diff: Callable | None = None,
) -> tuple[dict, bool]:
    """Return the status dict and whether the whole checklist passes. For an m1-audit corpus a journal row warns, without blocking, once the verdict journal beside the store needs a close-out (`journal.close_out_due`). `with_server=False` leaves out the server row. The artifact cycle passes it at the end of a `make review-cycle` pass, because the recipe restarts the server afterwards, or reports that it left it down, and a row read earlier would report as absent a server that is about to start. `recompute_table_diff` stands in for `status.table_diff_inputs` on a table-diff corpus."""
    result = status.compute_status(
        repo_root,
        review_dir,
        m1_out,
        autosave_path,
        cycle_summary_path,
        recompute=recompute,
        recompute_table_diff=recompute_table_diff,
    )
    due = (
        None
        if table_diff(result)
        else journal.close_out_due(Path(autosave_path).with_name(journal.JOURNAL_NAME))
    )
    if due is not None:
        result["checks"]["journal"] = {"level": "warn", "detail": due, "remedy": "make close-out"}
    if not with_server:
        return result, bool(result["ready"])
    up = listening()
    other = other_root(repo_root) if up else None
    if other is not None:
        server_check = {
            "level": "fail",
            "detail": f"a review server for another checkout ({other}) holds port 7294",
            "remedy": "stop that checkout's review server, then make review-serve",
        }
    elif up:
        server_check = {"level": "ok", "detail": "listening on port 7294", "remedy": None}
    else:
        server_check = {
            "level": "fail",
            "detail": "not listening on port 7294",
            "remedy": "make review-serve",
        }
    result["checks"]["server"] = server_check
    return result, bool(result["ready"]) and up and other is None


def checklist(result: dict, overall_ready: bool) -> list[str]:
    corpus = result["corpus"]
    lines = [
        f"Review corpus: {corpus['dir']}",
        f"  generated_at: {corpus['generated_at']}   repo_head: {corpus['repo_head']}",
        "",
    ]
    checks = result["checks"]
    for name in TABLE_DIFF_CHECK_ORDER if table_diff(result) else CHECK_ORDER:
        check = checks.get(name)
        if check is None:
            continue
        marker = {"ok": "✓", "warn": "!"}.get(check.get("level"), "✗")
        lines.append(f"  {marker} {name}: {check['detail']}")
        remedy = check.get("remedy")
        if check.get("level") != "ok" and remedy:
            lines.append(f"      remedy: {remedy}")
    lines.append("")
    diff = checks.get("diff") if table_diff(result) else None
    if overall_ready and isinstance(diff, dict) and diff.get("count") == 0:
        lines.append(f"READY - no unaccepted changes since {diff.get('since')}")
    else:
        lines.append(f"READY - adjudicate at {QUEUE_URL}" if overall_ready else "NOT READY")
    return lines


def diff_summary_line(result: dict) -> str | None:
    """The cycle summary's line for a table-diff corpus, `review diff: N unaccepted changes since <sha12> (H need a verdict)`, from the status dict's diff row, or None for another corpus."""
    diff = result.get("checks", {}).get("diff") if table_diff(result) else None
    if not isinstance(diff, dict):
        return None
    count = diff.get("count", 0)
    return (
        f"review diff: {count:,} unaccepted change{'' if count == 1 else 's'} since {diff.get('since')} "
        f"({diff.get('human', 0):,} need a verdict)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(".")[0] + ".")
    parser.add_argument("--json", action="store_true", help="dump the full status dict as JSON")
    args = parser.parse_args()

    result, overall_ready = readiness()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("\n".join(checklist(result, overall_ready)))
    sys.exit(0 if overall_ready else 1)


if __name__ == "__main__":
    main()
