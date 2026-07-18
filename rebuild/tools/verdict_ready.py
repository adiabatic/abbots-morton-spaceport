"""Answer one question from the shell: is the review surface ready to adjudicate right now? Renders rebuild.review.status.compute_status over the production paths, adds a live check that the review server is actually listening on port 7294, and prints a readable checklist (or the raw dict under --json). Exit 0 only when every blocking check passes and the server is up, so it drops cleanly into a Makefile guard."""

import argparse
import json
import sys
from pathlib import Path

from rebuild.review import status

ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = ROOT / "rebuild" / "out" / "review"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CYCLE_SUMMARY_PATH = ROOT / "rebuild" / "out" / "cycle_summary.json"
AUTOSAVE_PATH = ROOT / "verdicts-autosave.json"
DOCKET_URL = "http://localhost:7294/#view=docket"

CHECK_ORDER = ("surface", "freshness", "gates", "verdict_store", "frontier", "blanks", "server")


def _print_human(result: dict, overall_ready: bool) -> None:
    surface = result["surface"]
    print(f"Review surface: {surface['dir']}")
    print(f"  generated_at: {surface['generated_at']}   repo_head: {surface['repo_head']}")
    print("")
    checks = result["checks"]
    for name in CHECK_ORDER:
        check = checks.get(name)
        if check is None:
            continue
        marker = {"ok": "✓", "warn": "!"}.get(check.get("level"), "✗")
        print(f"  {marker} {name}: {check['detail']}")
        remedy = check.get("remedy")
        if check.get("level") != "ok" and remedy:
            print(f"      remedy: {remedy}")
    print("")
    if overall_ready:
        print(f"READY - adjudicate at {DOCKET_URL}")
    else:
        print("NOT READY")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split(".")[0] + ".")
    parser.add_argument("--json", action="store_true", help="dump the full status dict as JSON")
    args = parser.parse_args()

    from rebuild.tools.artifact_cycle import server_listening

    result = status.compute_status(ROOT, REVIEW_DIR, M1_OUT, AUTOSAVE_PATH, CYCLE_SUMMARY_PATH)
    listening = server_listening()
    if listening:
        server_check = {"level": "ok", "detail": "listening on port 7294", "remedy": None}
    else:
        server_check = {
            "level": "fail",
            "detail": "not listening on port 7294",
            "remedy": "make review-serve",
        }
    result["checks"]["server"] = server_check
    overall_ready = result["ready"] and listening

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result, overall_ready)
    sys.exit(0 if overall_ready else 1)


if __name__ == "__main__":
    main()
