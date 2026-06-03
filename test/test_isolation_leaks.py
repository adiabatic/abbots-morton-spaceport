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
from leak_snapshot import SNAPSHOT_PATH, current_leaks, parse_snapshot  # noqa: E402

# Matches the default the check-html target passes; deeper sweeps cost ~44× per step (see tools/build_check_html.py docstring).
_MAX_LEN = 3


def _format_leak(leak: Leak, example: IsolationLeakExample) -> str:
    seq = " ".join(f.removeprefix("qs") for f in example.families)
    diffs = []
    if leak.left_changed:
        diffs.append(f"{leak.isolated_left} -> {leak.left_chosen}")
    if leak.right_changed:
        diffs.append(f"{leak.isolated_right} -> {leak.right_chosen}")
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


@pytest.mark.slow
def test_isolation_leak_snapshot_unchanged() -> None:
    """Deeper-context gate: the set of visible leaks at depth 4 must match the approved snapshot.

    Unlike the depth-3 gate above — which demands *zero* leaks — this one freezes the (currently non-empty) set of four-letter-context leaks and fails on any *change*. A new signature means a YAML/emitter change introduced a cross-break shape difference; a missing one means you fixed a leak and should re-bless the snapshot. Either way the fix is `make leak-snapshot`, then review the diff. This is the gate that retires hand-written 44^n tuple tests; it is slow (~50 s) and excluded from the default run, so invoke it via `make test-leaks`.
    """
    if not SNAPSHOT_PATH.exists():
        pytest.fail(f"Missing {SNAPSHOT_PATH.relative_to(ROOT)} — generate it with `make leak-snapshot`.")

    approved = parse_snapshot(SNAPSHOT_PATH.read_text())
    live = current_leaks()

    introduced = sorted(set(live) - set(approved))
    resolved = sorted(set(approved) - set(live))
    if not introduced and not resolved:
        return

    sections: list[str] = []
    if introduced:
        body = "\n".join(f"  + {live[sig]} :: {_sig_diff(sig)}" for sig in introduced)
        sections.append(
            f"{len(introduced)} NEW isolation leak(s) — a change made these non-joining pairs shape "
            f"differently in context vs. in isolation:\n{body}"
        )
    if resolved:
        body = "\n".join(f"  - {approved[sig]} :: {_sig_diff(sig)}" for sig in resolved)
        sections.append(
            f"{len(resolved)} previously-approved leak(s) no longer occur (nice — re-bless):\n{body}"
        )
    sections.append(
        "If these changes are intended, regenerate the snapshot with `make leak-snapshot` and review the diff."
    )
    pytest.fail("\n\n".join(sections))


def _sig_diff(sig: tuple[str, str, str, str]) -> str:
    il, lc, ir, rc = sig
    parts = []
    if il != lc:
        parts.append(f"L {il}->{lc}")
    if ir != rc:
        parts.append(f"R {ir}->{rc}")
    return ", ".join(parts)
