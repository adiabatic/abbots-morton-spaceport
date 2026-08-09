"""Parenthesize the repo's PEP 758 `except A, B:` clauses so a parser older than CPython 3.14 can read them.

`except A, B:` and `except (A, B):` are the same clause; PEP 758 only removed the parentheses. Cython 3.2.9's parser and PyPy 3.11's parser both predate it, so both reject the repo's source outright. Rewriting is exact — no semantics move.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERN = re.compile(r"^(\s*except )([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)+)(\s*:.*)$")


def main() -> int:
    root = Path(sys.argv[1])
    changed = 0
    for path in sorted(root.rglob("*.py")):
        lines = path.read_text().splitlines(keepends=True)
        out = []
        touched = False
        for line in lines:
            m = PATTERN.match(line.rstrip("\n"))
            if m:
                out.append(f"{m.group(1)}({m.group(2)}){m.group(3)}\n")
                touched = True
                changed += 1
            else:
                out.append(line)
        if touched:
            path.write_text("".join(out))
    print(f"parenthesized {changed} except clauses under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
