"""Classify a shaping-leak signature as bad or benign.

`doc/definitions/shaping-leakage.md` defines leaks and the bad/benign split (decisions 6, 10, and 11). `classify` implements that split without shaping, and the leak sweep and the gates both use it.

A leak is **bad** when it is visible (the shaped run differs from its two halves shaped separately, each keeping the boundary token) and a changed side is an additive dangle: relative to its isolated form, the chosen stance gained a connector modifier on its break-facing edge, which is the left glyph's exit or the right glyph's entry. Every other leak is **benign**. A side whose break-facing edge is removed (`noexit` or `ex-noentry` on the left, `noentry` on the right) has nothing to dangle, so `qsThey_qsUtter.noentry.ex-con-1` is benign even though it changed.

Overrides, from highest precedence:

- **Force-bad**, per signature: `site/leak-force-bad.yaml`. It covers swaps the modifier test reads as benign. Most are leaks from several lookups combined, where the changed side reverts to its bare form while an unchanged ligature neighbor takes the join. The rest are ·Excite swaps into its `before-vertical` stances.
- **Force-benign**, per signature: `site/leak-force-benign.yaml`. It covers accepted standalone-variant swaps that gain a break-facing anchor but have no cosmetic modifier, such as `qsNo -> qsNo.alt.en-y0.ex-y0`.
- **Force-benign**, per stance: a `before-<fam>` or `after-<fam>` modifier for the neighbor across the break, as `leak_contract_report._is_cosmetic` decides it.
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
# Modifiers that remove the break-facing edge, so nothing can dangle.
_LEFT_EDGE_REMOVED = {"noexit", "ex-noentry"}
_RIGHT_EDGE_REMOVED = {"noentry"}


def _modifiers(name: str) -> frozenset[str]:
    meta = _compiled_meta().get(name)
    return frozenset(meta.modifiers) if meta is not None else frozenset()


def _is_additive_dangle(isolated: str, chosen: str, *, side: str) -> bool:
    """Return whether *chosen* gained, relative to *isolated*, a connector modifier on its break-facing edge (the exit for ``side="left"``, the entry for ``side="right"``) and does not remove that edge."""
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
        isolated_left, left_chosen, isolated_right, right_chosen = row
        out.add((isolated_left, left_chosen, isolated_right, right_chosen))
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
