"""Print whether the review surface is ready to adjudicate now.

It runs `rebuild.review.status.compute_status` over the production paths, adds a check that the review server is listening on port 7294, and prints a checklist, or the status dict as JSON under `--json`. It exits 0 only when every blocking check passes and the server is up, so it can serve as a Makefile guard. The artifact cycle calls `readiness` and `checklist` to print the same checklist at the end of every green pass that is not a rehearsal, so this CLI is for asking the question between passes.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from rebuild.review import status
from rebuild.tools.review_server import server_listening

ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = ROOT / "rebuild" / "out" / "review"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CYCLE_SUMMARY_PATH = ROOT / "rebuild" / "out" / "cycle_summary.json"
AUTOSAVE_PATH = ROOT / "verdicts-autosave.json"
DOCKET_URL = "http://localhost:7294/#view=docket"

CHECK_ORDER = ("surface", "freshness", "gates", "verdict_store", "frontier", "blanks", "server")


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
) -> tuple[dict, bool]:
    """Return the status dict and whether the whole checklist passes. `with_server=False` leaves out the server row. The artifact cycle passes it at the end of a `make review-cycle` pass, because the recipe restarts the server afterwards, or reports that it left it down, and a row read earlier would report as absent a server that is about to start."""
    result = status.compute_status(
        repo_root, review_dir, m1_out, autosave_path, cycle_summary_path, recompute=recompute
    )
    if not with_server:
        return result, bool(result["ready"])
    up = listening()
    if up:
        server_check = {"level": "ok", "detail": "listening on port 7294", "remedy": None}
    else:
        server_check = {
            "level": "fail",
            "detail": "not listening on port 7294",
            "remedy": "make review-serve",
        }
    result["checks"]["server"] = server_check
    return result, bool(result["ready"]) and up


def checklist(result: dict, overall_ready: bool) -> list[str]:
    surface = result["surface"]
    lines = [
        f"Review surface: {surface['dir']}",
        f"  generated_at: {surface['generated_at']}   repo_head: {surface['repo_head']}",
        "",
    ]
    checks = result["checks"]
    for name in CHECK_ORDER:
        check = checks.get(name)
        if check is None:
            continue
        marker = {"ok": "✓", "warn": "!"}.get(check.get("level"), "✗")
        lines.append(f"  {marker} {name}: {check['detail']}")
        remedy = check.get("remedy")
        if check.get("level") != "ok" and remedy:
            lines.append(f"      remedy: {remedy}")
    lines.append("")
    lines.append(f"READY - adjudicate at {DOCKET_URL}" if overall_ready else "NOT READY")
    return lines


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
