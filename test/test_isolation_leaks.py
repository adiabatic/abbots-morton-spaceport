"""Hard gates for shaping leaks (doc/definitions/shaping-leakage.md).

`tools/build_check_html.py::find_leaks` sweeps every Quikscript letter sequence (plus the `space`/ZWNJ boundary tokens) up to a chosen length and reports each non-joining adjacent pair whose chosen glyphs differ between in-context shaping and boundary-faithful split shaping. `tools/leak_classify.py` then labels each visible leak **bad** (a visible additive dangle reaching toward an absent neighbor) or **benign** (subtractive trims, standalone-variant swaps, cosmetic tucks — the welcome faux-organic variation).

CI fails only on **bad** leaks. Two depths:

  * depth-3 (fast, in the default `make test`): no live bad leak may fall outside the approved backlog.
  * depth-4 (slow, `make test-leaks`): same backlog gate, plus a symmetric benign census so a shifting organic-variation set is surfaced for review.

The bad gate is asymmetric — a NEW bad signature fails (a change introduced a dangle); a *resolved* one only prints a re-bless notice, because the autonomous fix loop is expected to drain the backlog and should not trip the gate by succeeding. The benign census is symmetric: any change means `make leak-snapshot` + review. Both files are regenerated together by `make leak-snapshot`.
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

# The everyday fast gate; the slow gate sweeps to depth 4 (≈44× per deeper step — see tools/build_check_html.py).
_FAST_MAX_LEN = 3


@cache
def _partition(max_len: int) -> tuple[dict[Signature, str], dict[Signature, str]]:
    """(bad, benign) live partition at *max_len*, cached so the two depth-4 gates share one sweep."""
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
    """Asymmetric bad gate: fail on any live bad signature not already grandfathered into the backlog; a resolved one is only a notice."""
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
        # Progress, not a failure: the loop drained these. Surface so the snapshot gets re-blessed.
        body = "\n".join(f"  - {backlog[sig]} :: {_sig_diff(sig)}" for sig in resolved)
        print(
            f"\n{len(resolved)} bad leak(s) no longer occur at depth {depth} (nice — re-bless with "
            f"`make leak-snapshot`):\n{body}"
        )


def test_no_new_bad_isolation_leaks() -> None:
    """Fast everyday gate: no live bad leak at depth 3 outside the approved backlog."""
    bad, _benign = _partition(_FAST_MAX_LEN)
    _assert_no_new_bad(bad, _require_backlog(), depth=_FAST_MAX_LEN)


@pytest.mark.slow
def test_bad_leak_backlog_unchanged() -> None:
    """Deep bad gate: no NEW bad leak at depth 4; resolved ones are a re-bless notice. This is the gate that the autonomous fix loop drives toward empty."""
    bad, _benign = _partition(4)
    _assert_no_new_bad(bad, _require_backlog(), depth=4)


@pytest.mark.slow
def test_benign_census_unchanged() -> None:
    """Deep benign census: the welcome faux-organic variation must match the approved census. Symmetric — any change (gained or lost benign leak) is surfaced so `make leak-snapshot` re-blesses it and review notices the set shifting. Never a hard failure on its own; it shares the depth-4 sweep with the bad gate."""
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
