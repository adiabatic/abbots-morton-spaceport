"""The mechanical bad/benign classifier for shaping leaks.

`doc/definitions/shaping-leakage.md` defines a *leak* as a cross-break shape difference and overlays a *bad* vs *benign* severity. This module is that overlay: a pure, shaping-free `signature -> verdict` function the sweep and the gates both import.

A leak is **bad** ⇔ it is **visible** (the rendered run differs from the concatenation of its boundary-faithful halves) *and* a changed flanking stance is an **additive dangle** — it gained, in context, a break-facing connector the isolated form lacked, so the stroke reaches toward a neighbor that isn't there. Everything else is **benign**: subtractive trims that make a letter more self-contained, standalone variant swaps, and all invisible swaps. A little benign leakage is welcome — it is the faux-organic variation the script wants.

### The additive signal is the *gained break-facing anchor*, not a static token

`doc/definitions/shaping-leakage.md` decision 6's table filed the additive class under `ex-ext-N`/`en-ext-N`. Measured against the 99 human-verified "broken" leaks that fires on **zero** of them: the real dangle in this font is the in-context stance **gaining a break-facing connecting anchor** vs its isolated form — `ex-y0`/`ex-y5`/`ex-y6` on the left glyph's exit (reaching right into the break), `en-y0`/`en-y5`/`en-y6`/`en-y8` on the right glyph's entry (reaching left into the break). 97 of 100 broken rows involve such an anchor. This matches the decision's *prose* ("reaches connector ink toward the across-break neighbor"); only the token bucketing was wrong. So the test is a **delta** — what the chosen stance gained relative to the isolated form on the break-facing edge — and the `ex-ext`/`en-ext` length tokens (and `extended`) stay in the additive set because they co-occur and are genuinely additive.

A break-facing *subtractive* edge wins over any additive token on that same edge: a left stance carrying `noexit`/`ex-noentry` has no exit to dangle; a right stance carrying `noentry` has no entry to dangle. So `qsThey_qsUtter.noentry.ex-con-1` (subtractive on the entry) reads benign even though it changed.

### Overrides (decision 11, plus one symmetric completion)

- **Force-benign**, per stance: a directional `before-<fam>`/`after-<fam>` cosmetic interaction with the across-break neighbor (decided by `leak_contract_report._is_cosmetic`, which pins the opt-out to the neighbor's family via the stance's resolved trigger list, so `before-vertical`-style stems and `after-baseline-letter`-style class tokens are handled correctly).
- **Force-benign**, per signature: `site/leak-force-benign.yaml`. A symmetric completion of decision 11 — some human-accepted standalone-variant swaps (e.g. `qsNo -> qsNo.alt.en-y0.ex-y0`) gain a facing anchor and trip the proxy yet carry no cosmetic modifier; this allowlist demotes the exact swap without weakening the proxy elsewhere.
- **Force-bad**, per signature: `site/leak-force-bad.yaml`. The proxy is structurally blind to the cross-lookup-compose leaks where the changed side strips to bare while an *unchanged* ligature neighbor absorbs the join; this blocklist condemns the exact swap. Force-bad outranks every force-benign signal.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
TEST_DIR = ROOT / "test"
SITE_DIR = ROOT / "site"
for _p in (str(TOOLS_DIR), str(TEST_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from leak_contract_report import _is_cosmetic  # noqa: E402
from quikscript_shaping_helpers import _compiled_meta  # noqa: E402

Signature = tuple[str, str, str, str]  # (isolated_left, left_chosen, isolated_right, right_chosen)

# The override files live under site/ (not glyph_data/, which build_font.py merges as glyph families).
FORCE_BAD_PATH = SITE_DIR / "leak-force-bad.yaml"
FORCE_BENIGN_PATH = SITE_DIR / "leak-force-benign.yaml"

# Break-facing additive connectors, by side. The left glyph's break-facing edge is its exit; the right glyph's is its entry. `extended` widens the body toward whichever side it sits on, so it counts on both.
_LEFT_ADDITIVE_RE = re.compile(r"^(ex-y[0-9]|ex-ext-\d+|extended)$")
_RIGHT_ADDITIVE_RE = re.compile(r"^(en-y[0-9]|en-ext-\d+|extended)$")
# Break-facing edge removed entirely — nothing left to dangle.
_LEFT_EDGE_REMOVED = {"noexit", "ex-noentry"}
_RIGHT_EDGE_REMOVED = {"noentry"}


def _modifiers(name: str) -> frozenset[str]:
    meta = _compiled_meta().get(name)
    return frozenset(meta.modifiers) if meta is not None else frozenset()


def _is_additive_dangle(isolated: str, chosen: str, *, side: str) -> bool:
    """Whether the break-facing edge of *chosen* (vs the isolated form) is an additive reach into the break. ``side`` is ``"left"`` (exit faces the break) or ``"right"`` (entry faces the break)."""
    if isolated == chosen:
        return False
    chosen_mods = _modifiers(chosen)
    gained = chosen_mods - _modifiers(isolated)
    if side == "left":
        if chosen_mods & _LEFT_EDGE_REMOVED:
            return False
        return any(_LEFT_ADDITIVE_RE.match(t) for t in gained)
    if chosen_mods & _RIGHT_EDGE_REMOVED:
        return False
    return any(_RIGHT_ADDITIVE_RE.match(t) for t in gained)


def _load_signatures(path: Path) -> set[Signature]:
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text()) or {}
    rows = raw.get("signatures", []) if isinstance(raw, dict) else raw
    out: set[Signature] = set()
    for row in rows:
        if len(row) != 4:
            raise ValueError(f"{path.name}: each signature must be 4 glyph names, got {row!r}")
        out.add(tuple(row))  # type: ignore[arg-type]
    return out


def force_bad_signatures() -> set[Signature]:
    return _load_signatures(FORCE_BAD_PATH)


def force_benign_signatures() -> set[Signature]:
    return _load_signatures(FORCE_BENIGN_PATH)


def classify(
    signature: Signature,
    *,
    visible: bool,
    force_bad: set[Signature] | None = None,
    force_benign: set[Signature] | None = None,
) -> str:
    force_bad = force_bad_signatures() if force_bad is None else force_bad
    force_benign = force_benign_signatures() if force_benign is None else force_benign
    isolated_left, left_chosen, isolated_right, right_chosen = signature

    if signature in force_bad:
        return "bad"
    if signature in force_benign:
        return "benign"

    left_changed = isolated_left != left_chosen
    right_changed = isolated_right != right_chosen
    if left_changed and _is_cosmetic(left_chosen, right_chosen, direction="forward"):
        return "benign"
    if right_changed and _is_cosmetic(right_chosen, left_chosen, direction="backward"):
        return "benign"

    if not visible:
        return "benign"

    left_bad = left_changed and _is_additive_dangle(isolated_left, left_chosen, side="left")
    right_bad = right_changed and _is_additive_dangle(isolated_right, right_chosen, side="right")
    return "bad" if (left_bad or right_bad) else "benign"
