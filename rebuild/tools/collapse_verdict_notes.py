"""Collapse the provenance-marker chain in each note of a verdicts file in place, keeping only the newest few markers. The review producers prepend one `[carried ...]` / `[echo-fill ...]` marker per cycle and splice the whole prior note in verbatim, so a long-lived verdict's note accretes dozens of stacked markers; this rewrites those notes down to `--keep` markers apiece (via cap_markers). Only the `note` values change — every other field, the record order, the top-level keys, the 2-space indentation, and the file's trailing-newline convention are preserved."""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.tools.verdict_notes import cap_markers  # noqa: E402


def collapse(path, keep=2):
    text = path.read_text()
    data = json.loads(text)
    for record in data["verdicts"]:
        record["note"] = cap_markers(record["note"], keep)
    rewritten = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(rewritten + "\n" if text.endswith("\n") else rewritten)


def main():
    parser = argparse.ArgumentParser(description="Collapse each verdict note to its newest few provenance markers in place.")
    parser.add_argument("files", nargs="+", type=pathlib.Path)
    parser.add_argument("--keep", type=int, default=2, help="markers to retain per note (default: %(default)s)")
    args = parser.parse_args()
    for path in args.files:
        before = path.stat().st_size
        collapse(path, args.keep)
        after = path.stat().st_size
        shrink = 100 * (before - after) / before if before else 0
        print(f"{path.name}: {before:,} -> {after:,} bytes ({shrink:.1f}% smaller)")


if __name__ == "__main__":
    main()
