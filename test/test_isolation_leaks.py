"""Gates for shaping leaks (doc/definitions/shaping-leakage.md).

`tools/build_check_html.py::find_visible_leaks` shapes every sequence of Quikscript letters, plus the `space` and ZWNJ boundary tokens, up to a given length. At each non-joining adjacent pair it compares the glyphs chosen in context with those chosen when the sequence is split at the break, keeping the boundary token in both halves. It returns each signature that renders a visible difference. `tools/leak_classify.py` labels each one **bad** (an additive dangle: a break-facing connector reaching toward a neighbor that isn't there) or **benign** (subtractive trims, standalone-variant swaps, cosmetic tucks).

The tests run at two depths:

  * Depth 3, in `make test`: every live bad leak must be in the approved backlog.
  * Depth 4, in `make test-leaks`: the same backlog check, plus a check that the benign leaks match the approved census.

The bad check is asymmetric. A new bad signature fails, because a change introduced a dangle. A resolved one only prints a notice to re-bless, so fixing a leak does not fail the test. The benign check is symmetric: any change fails until `make leak-snapshot` re-blesses the census. `make leak-snapshot` regenerates both files.
"""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from leak_snapshot import (  # noqa: E402
    BAD_BACKLOG_PATH,
    BENIGN_CENSUS_PATH,
    Signature,
    current_partition,
    parse_snapshot,
)

# Depth 4 runs only under `make test-leaks`, because each extra depth multiplies the number of swept sequences by roughly the size of the sweep alphabet (`_sweep_alphabet` in `tools/build_check_html.py`).
_FAST_MAX_LEN = 3


@cache
def _partition(max_len: int) -> tuple[dict[Signature, str], dict[Signature, str]]:
    """Return the live (bad, benign) leaks at *max_len*, cached so the two depth-4 tests share one sweep."""
    return current_partition(max_len)


def _sig_diff(sig: Signature) -> str:
    il, lc, ir, rc = sig
    parts = []
    if il != lc:
        parts.append(f"L {il}->{lc}")
    if ir != rc:
        parts.append(f"R {ir}->{rc}")
    return ", ".join(parts)


def _require_backlog() -> dict[Signature, str]:
    if not BAD_BACKLOG_PATH.exists():
        pytest.fail(f"Missing {BAD_BACKLOG_PATH.relative_to(ROOT)} — generate it with `make leak-snapshot`.")
    return parse_snapshot(BAD_BACKLOG_PATH.read_text())


def _assert_no_new_bad(bad: dict[Signature, str], backlog: dict[Signature, str], *, depth: int) -> None:
    """Fail on any live bad signature missing from the backlog, and print a notice for backlog entries that no longer occur."""
    introduced = sorted(set(bad) - set(backlog))
    if introduced:
        body = "\n".join(f"  + {bad[sig]} :: {_sig_diff(sig)}" for sig in introduced)
        pytest.fail(
            f"{len(introduced)} NEW bad isolation leak(s) at depth {depth} — a change made these "
            f"non-joining pairs grow an additive dangle in context:\n{body}\n\n"
            "If this is intended, regenerate with `make leak-snapshot` and review; otherwise make the "
            "break-facing edge subtractive (or revert it) for the offending context."
        )
    resolved = sorted(set(backlog) - set(bad))
    if resolved:
        body = "\n".join(f"  - {backlog[sig]} :: {_sig_diff(sig)}" for sig in resolved)
        print(
            f"\n{len(resolved)} bad leak(s) no longer occur at depth {depth} (nice — re-bless with "
            f"`make leak-snapshot`):\n{body}"
        )


def test_no_new_bad_isolation_leaks() -> None:
    """Every live bad leak at depth 3 is in the approved backlog."""
    bad, _benign = _partition(_FAST_MAX_LEN)
    _assert_no_new_bad(bad, _require_backlog(), depth=_FAST_MAX_LEN)


@pytest.mark.slow
def test_bad_leak_backlog_unchanged() -> None:
    """Every live bad leak at depth 4 is in the approved backlog."""
    bad, _benign = _partition(4)
    _assert_no_new_bad(bad, _require_backlog(), depth=4)


@pytest.mark.slow
def test_benign_census_unchanged() -> None:
    """The live benign leaks at depth 4 match the approved census. Any gained or lost benign leak fails until `make leak-snapshot` re-blesses the census, so a reviewer sees the change."""
    if not BENIGN_CENSUS_PATH.exists():
        pytest.fail(
            f"Missing {BENIGN_CENSUS_PATH.relative_to(ROOT)} — generate it with `make leak-snapshot`."
        )
    approved = parse_snapshot(BENIGN_CENSUS_PATH.read_text())
    _bad, benign = _partition(4)

    introduced = sorted(set(benign) - set(approved))
    resolved = sorted(set(approved) - set(benign))
    if not introduced and not resolved:
        return

    sections: list[str] = []
    if introduced:
        body = "\n".join(f"  + {benign[sig]} :: {_sig_diff(sig)}" for sig in introduced)
        sections.append(f"{len(introduced)} NEW benign leak(s):\n{body}")
    if resolved:
        body = "\n".join(f"  - {approved[sig]} :: {_sig_diff(sig)}" for sig in resolved)
        sections.append(f"{len(resolved)} benign leak(s) no longer occur:\n{body}")
    sections.append(
        "Benign census changed (this is informational, not a defect). Regenerate with `make leak-snapshot` and review the diff."
    )
    pytest.fail("\n\n".join(sections))
