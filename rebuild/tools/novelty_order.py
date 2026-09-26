"""Order the blank review queue for novelty, so consecutive units in a review session differ as much as possible instead of following the shard order's near-identical neighbors. It takes one representative per duplicate group among the human units with no record, since the app copies a verdict on the representative to the group's members that have no record. A unit whose latest verdict is a skip is blank too, but the duplicate fill never reaches it, so each skipped unit is its own representative. The distance between two representatives is a weighted sum over `DIMENSIONS`: divergence class, left and right family, letter set, settled stances, changed junctions, configuration set, unit kinds, deciding provenance and window length. The walk starts at a representative of the rarest class and then picks, each time, the representative whose smallest distance to the last `RECENT_WINDOW` shown is largest, breaking ties toward rarer classes and then earlier triage position, so one-off questions are not buried behind the large classes. It prints the worklist URL to paste into the review app, `#units=…&order=given`, which the app keeps in the given order instead of sorting by family pair."""

import argparse
import collections
import json
import pathlib
import subprocess
import sys
from collections.abc import Callable

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.serve import PORT  # noqa: E402
from rebuild.tools.review_queue import latest_verdicts, load_human_units  # noqa: E402

CORPUS = ROOT / "rebuild/out/review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
RECENT_WINDOW = 3


def _triage_position(unit):
    """Return the unit's position in the corpus's triage index (the index record's `order`), which decides the representative chosen from each group and breaks the walk's remaining ties."""
    order = unit.get("order")
    return order if isinstance(order, int) else sys.maxsize


def blank_representatives(units, records):
    human = [unit for unit in units if unit["batch"] is not None]
    blanks = [unit for unit in human if unit["id"] not in records or records[unit["id"]]["verdict"] == "skip"]
    groups = collections.defaultdict(list)
    for unit in blanks:
        groups[unit["id"] if unit["id"] in records else unit.get("duplicate_group") or unit["id"]].append(
            unit
        )
    representatives = [min(members, key=_triage_position) for members in groups.values()]
    representatives.sort(key=_triage_position)
    return representatives, len(blanks)


def features(unit):
    left, _, right = (unit.get("group") or ":").partition(":")
    before = unit.get("before") or {}
    after = unit.get("after") or {}
    changed = frozenset(
        f"{i}:{b}>{a}"
        for i, (b, a) in enumerate(zip(before.get("junctions") or [], after.get("junctions") or []))
        if b != a
    )
    return {
        "class": unit["class"],
        "left": left,
        "right": right,
        "letters": frozenset(token for token in unit.get("notation_tokens") or [] if token != "·"),
        "cells": frozenset("/".join(cell.split("/")[:2]) for cell in after.get("cells") or []),
        "junctions": changed or frozenset({"unchanged"}),
        "configs": tuple(unit.get("configs") or []),
        "kinds": frozenset(unit.get("kinds") or []),
        "provenance": frozenset((unit.get("provenance") or [])[:12]),
        "length": len(unit.get("notation_tokens") or []),
    }


def _differs(a, b):
    return 0.0 if a == b else 1.0


def _jaccard(a, b):
    union = a | b
    if not union:
        return 0.0
    return 1.0 - len(a & b) / len(union)


DIMENSIONS = (
    ("class", 0.16, _differs),
    ("left", 0.09, _differs),
    ("right", 0.09, _differs),
    ("letters", 0.14, _jaccard),
    ("cells", 0.13, _jaccard),
    ("junctions", 0.11, _jaccard),
    ("configs", 0.08, _differs),
    ("kinds", 0.05, _jaccard),
    ("provenance", 0.11, _jaccard),
    ("length", 0.04, _differs),
)


def distance(fa, fb):
    return sum(weight * measure(fa[key], fb[key]) for key, weight, measure in DIMENSIONS)


def novelty_order(representatives):
    if not representatives:
        return []
    feats = {unit["id"]: features(unit) for unit in representatives}
    position = {unit["id"]: _triage_position(unit) for unit in representatives}
    rarity = collections.Counter(f["class"] for f in feats.values())
    seed = min(representatives, key=lambda u: (rarity[feats[u["id"]]["class"]], position[u["id"]]))
    order = [seed["id"]]
    remaining = {unit["id"] for unit in representatives} - {seed["id"]}
    while remaining:
        window = order[-RECENT_WINDOW:]
        best = max(
            remaining,
            key=lambda uid: (
                min(distance(feats[uid], feats[prev]) for prev in window),
                -rarity[feats[uid]["class"]],
                -position[uid],
            ),
        )
        order.append(best)
        remaining.discard(best)
    return order


def _copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode(), check=True)


def main(clipboard_write: Callable[[str], None] | None = None, *, units=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(",")[0] + ".")
    parser.add_argument(
        "verdicts",
        nargs="?",
        default=str(AUTOSAVE),
        help="the verdicts file to read, such as the fullest verdicts file (default: the live autosave)",
    )
    parser.add_argument("--corpus", "--surface", default=str(CORPUS))
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="emit only the first N worklist entries (0 for the whole queue; default 40)",
    )
    args = parser.parse_args()

    corpus = pathlib.Path(args.corpus)
    manifest = json.loads((corpus / "manifest.json").read_text())
    verdicts_path = pathlib.Path(args.verdicts)
    data = json.loads(verdicts_path.read_text())
    if data.get("manifest_generated_at") != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the corpus is "
            f"{manifest['generated_at']}; unit ids must never be joined across manifests — carry it forward first"
        )
    records = latest_verdicts(verdicts_path)

    representatives, blank_count = blank_representatives(
        units if units is not None else load_human_units(corpus)[0], records
    )
    if not representatives:
        print("No blank units — nothing to order.")
        return
    order = novelty_order(representatives)
    if args.limit > 0 and args.limit < len(order):
        emitted = order[: args.limit]
        print(
            f"{blank_count} blank units collapse to {len(order)} duplicate groups; "
            f"emitting the first {len(emitted)} representatives of the novelty order."
        )
    else:
        emitted = order
        print(
            f"{blank_count} blank units collapse to {len(order)} duplicate groups; "
            f"verdicting the worklist representatives duplicate-fills the rest."
        )
    url = f"http://localhost:{PORT}/#units={','.join(emitted)}&order=given"
    print(url)
    clipboard_write = clipboard_write or (_copy_to_clipboard if sys.platform == "darwin" else None)
    if clipboard_write is not None:
        clipboard_write(url)
        print("(copied to clipboard)")


if __name__ == "__main__":
    main()
