"""Hard gate for the isolation-leaks section of test/check.html.

`tools/build_check_html.py::find_leaks` sweeps every Quikscript letter sequence up to a chosen length and reports each non-joining adjacent pair whose chosen glyphs differ between in-context shaping and split shaping. The HTML page only renders the visually-distinct (`diff`-classified) subset; this test asserts that subset is empty so "are we done fixing the leaks?" has a green/red answer instead of needing a manual look at the page.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_check_html import IsolationLeakExample, Leak, _visual_status, find_leaks  # noqa: E402

# Matches the default the check-html target passes; deeper sweeps cost ~44× per step (see tools/build_check_html.py docstring).
_MAX_LEN = 3


def _format_leak(leak: Leak, example: IsolationLeakExample) -> str:
    seq = " ".join(f.removeprefix("qs") for f in example.families)
    diffs = []
    if leak.left_changed:
        diffs.append(f"{leak.left_iso} -> {leak.left_chosen}")
    if leak.right_changed:
        diffs.append(f"{leak.right_iso} -> {leak.right_chosen}")
    return f"  - {seq} (break {example.break_index}): {', '.join(diffs)}"


def test_no_visible_isolation_leaks() -> None:
    leaks = find_leaks(max_len=_MAX_LEN)
    diff_leaks = [(leak, example) for leak, example in leaks.items() if _visual_status(example) == "diff"]
    if not diff_leaks:
        return
    formatted = "\n".join(_format_leak(leak, example) for leak, example in diff_leaks)
    pytest.fail(
        f"{len(diff_leaks)} visible isolation leak(s) remain at --max-len {_MAX_LEN}. "
        "Regenerate test/check.html with `make check-html` to inspect them side-by-side.\n"
        f"{formatted}"
    )
