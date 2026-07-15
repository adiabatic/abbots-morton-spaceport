"""Emit machine verdicts for the blank ss10-only units on the live review surface, judged by the Junior-equivalence oracle (rebuild.review.ink.JuniorOracle): a unit divergent only under ss10 is approved when the rebuild's ss10 rendering places exactly the ink the shipped Junior font places for the same string, once Junior's uniform one-pixel-per-letter tracking is removed. The oracle testifies against the served font copies (rebuild/out/review/fonts/), so it judges exactly what the surface shows. Units that fail the oracle are left blank and listed for human eyes. From the next surface rebuild onward, review.build applies the same oracle at build time (units carry `junior_equivalent` and leave the human workload), so this emitter only matters for surfaces built before that channel existed.

Usage:
    uv run python rebuild/tools/auto_classify_ss10.py                    # skip units already verdicted in verdicts-autosave.json
    uv run python rebuild/tools/auto_classify_ss10.py <verdicts.json>    # skip units already verdicted in the given export
    Import the emitted verdicts-ss10-junior.json through the app's import control.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.ink import JuniorOracle

REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
JUNIOR_FONT = ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf"

NOTE = (
    "auto: divergent only under ss10; the rebuild's ss10 rendering is ink-identical to Junior's "
    "isolated rendering (minus Junior's one-pixel letter tracking)"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "verdicts",
        nargs="?",
        default=str(ROOT / "verdicts-autosave.json"),
        help="the verdicts file whose units are already adjudicated (default: verdicts-autosave.json)",
    )
    parser.add_argument("--out", type=Path, default=ROOT / "verdicts-ss10-junior.json")
    args = parser.parse_args(argv)

    manifest = json.loads((REVIEW_OUT / "manifest.json").read_text())
    verdicts_doc = json.loads(Path(args.verdicts).read_text())
    if verdicts_doc.get("manifest_generated_at") != manifest["generated_at"]:
        print(
            f"{args.verdicts} is stamped {verdicts_doc.get('manifest_generated_at')!r} but the surface "
            f"is {manifest['generated_at']!r} — unit ids do not join across manifests; refusing to emit.",
            file=sys.stderr,
        )
        return 1
    already_verdicted = {record["unit"] for record in verdicts_doc["verdicts"]}

    oracle = JuniorOracle(JUNIOR_FONT, REVIEW_OUT / "fonts" / "before.otf", REVIEW_OUT / "fonts" / "after.otf")

    records = []
    refused = []
    verdicted_count = 0
    for meta in manifest["classes"]:
        for unit in json.loads((REVIEW_OUT / meta["shard"]).read_text()):
            if unit["batch"] is None or unit["configs"] != ["ss10"]:
                continue
            if unit["id"] in already_verdicted:
                verdicted_count += 1
                continue
            text = "".join(chr(int(codepoint, 16)) for codepoint in unit["codepoints"].split(":"))
            if oracle.approves(tuple(unit["configs"]), text):
                records.append({"unit": unit["id"], "verdict": "approve", "note": NOTE, "at": now_iso()})
            else:
                refused.append(f"{unit['id']} ({unit['notation']})")

    records.sort(key=lambda record: record["unit"])
    output = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": now_iso(),
        "verdicts": records,
    }
    args.out.write_text(json.dumps(output, indent=2) + "\n")

    print(f"emitted {len(records)} approve verdict(s) to {args.out}")
    print(f"skipped {verdicted_count} already-verdicted ss10-only unit(s)")
    if refused:
        print(f"refused {len(refused)} unit(s) — the ss10 rendering is not Junior's; judge these by hand:")
        for line in refused:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
