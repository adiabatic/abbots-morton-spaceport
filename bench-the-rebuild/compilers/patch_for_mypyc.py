"""Apply the five annotation-only edits mypy demands before mypyc will compile settle.py / table.py.

Every edit widens or renames a local binding; none changes a value, a branch, or a call. The benchmark proves that by checksumming the built tables against the unpatched repo run.
"""

from __future__ import annotations

import sys
from pathlib import Path

EDITS = {
    "rebuild/pipeline/settle.py": [
        (
            "            key = (rune_name, candidate.stance, candidate.entry, candidate.seam, right1.letter, right2.letter)\n",
            "            key: tuple = (rune_name, candidate.stance, candidate.entry, candidate.seam, right1.letter, right2.letter)\n",
        ),
        (
            "            owner, record, favored = applicable[index]\n"
            "            narrowed = [candidate for candidate in current if candidate in favored]\n",
            "            owner, record, favored_set = applicable[index]\n"
            "            narrowed = [candidate for candidate in current if candidate in favored_set]\n",
        ),
    ],
    "rebuild/pipeline/table.py": [
        ("            intern = {}\n", "            intern: dict[str, str] = {}\n"),
        ("            gathered = []\n", "            gathered: list = []\n"),
        (
            "                                successor_allowed = frozenset({right3})\n",
            "                                successor_allowed: frozenset[RightToken] | None = frozenset({right3})\n",
        ),
    ],
}


def main() -> int:
    root = Path(sys.argv[1])
    total = 0
    for rel, edits in EDITS.items():
        path = root / rel
        text = path.read_text()
        for old, new in edits:
            if new in text:
                continue
            if text.count(old) != 1:
                raise SystemExit(f"{rel}: expected exactly one occurrence of {old!r}, got {text.count(old)}")
            text = text.replace(old, new)
            total += 1
        path.write_text(text)
    print(f"applied {total} annotation edits under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
