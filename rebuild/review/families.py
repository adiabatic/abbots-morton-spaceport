"""Verdict-family grouping for the round-3 review surface: partition the UNMATCHED windows (new joins the engine makes that the old shipped font did not) into the taste-call families a human adjudicates, so each family gets its own sidebar shard. This is a presentation-only grouping computed on the review side from each unit's settled seams — it touches no pipeline shaping logic and authors no ledger predicate; the oracle stays dirty until the families are adjudicated. assign_family is total over every UNMATCHED unit (the unmatched-misc catch-all guarantees no window is ever dropped).

Two axes decide a family:

- Config gating. A window whose novel behavior appears only under a stylistic set (ss04 Group A, the ss10 isolation residue, the ss02/ss03/ss05 tail) is deferred for a later pass — sorted last, labeled by its set — so round 3 focuses on the default letter-joins of ordinary writing. A window reachable under the default config is a default family.

- The primary changed seam. Among the default families, the gap whose before/after seam tokens differ names the family by its (left rune, right rune) and direction: a gained join (break -> yN, or a raised seam) versus a lost join (yN -> break, or a lowered seam). Tea -> It and Oy -> It gains and the May/Utter reach-backs are their own families; the remaining gains pool as the No-chain join-maximizer gains; every loss pools as the withdrawal / seam-loss family (the context-dependent, partly engine-limited one). A window whose seams are unchanged but whose cell settled differently is the extension-non-summing family.
"""

from __future__ import annotations

from rebuild.review.enrich import LETTERS, EnrichedUnit

UNMATCHED = "UNMATCHED"

FAMILY_ORDER = [
    "no-chain-gains",
    "tea-it-xheight",
    "oy-it-baseline",
    "may-utter-gains",
    "seam-loss-withdrawal",
    "extension-non-summing",
    "unmatched-misc",
    "deferred-ss04",
    "deferred-ss10",
    "deferred-ss03",
]

FAMILY_WHY = {
    "no-chain-gains": "The new engine adds a ·No-chain join the old font left broken — ·No reaching forward to ·Oy at the x-height, ·It rising into ·No, and the other join-maximizer gains around ·No. A taste call: keep the richer joins or restore the old breaks.",
    "tea-it-xheight": "·Tea·It now joins at the x-height (before ·Day/·Utter) where the old font broke. Resembles the entered-·It x-height gains already accepted; adjudicate whether this window should join too.",
    "oy-it-baseline": "·Oy·It now joins at the baseline before ·No (a strict +1-pixel join) where the old font broke.",
    "may-utter-gains": "·May/·Utter reach-back gains — ·May·Utter joining at the x-height and the ·Utter·May reach-backs (including the post-ZWNJ ·Utter·May·X windows) the old font did not draw.",
    "seam-loss-withdrawal": "The new engine breaks (or lowers) a seam the old font joined — ·No's flipped exit withdrawing before ·Tea, the ·Utter/·No chain-flip lowering x-height joins to the baseline, ·It withdrawing before ·Utter. The context-dependent, partly engine-limited family flagged in the round-2 analysis.",
    "extension-non-summing": "The seams are unchanged but the lead settles as a different cell because a composed extension no longer sums — the ·Tea·Oy·Day extension-drop window and its kin.",
    "unmatched-misc": "Default-config UNMATCHED windows that fit none of the named seam-gain or seam-loss signatures — the catch-all so no window is ever dropped from review.",
    "deferred-ss04": "Deferred for a later pass: the novel behavior appears only under stylistic set ss04 (the ss04 Group A lowered-lead design question and the ss04 ligature declines). Not part of the round-3 default adjudication.",
    "deferred-ss10": "Deferred for a later pass: the novel behavior appears only under stylistic set ss10 (the isolation-overlay residue). Not part of the round-3 default adjudication.",
    "deferred-ss03": "Deferred for a later pass: the novel behavior appears only under the ss02/ss03/ss05 stylistic sets. Not part of the round-3 default adjudication.",
}


def _config_features(config: str) -> frozenset[str]:
    return frozenset() if config == "default" else frozenset(config.split("+"))


def _unmatched_configs(unit) -> list[str]:
    """The configs in which this unit's window is UNMATCHED (novel) — the behavior under adjudication. Falls back to every config when the per-config map is absent (a fully-UNMATCHED triple)."""
    if unit.config_classes:
        return [config for config, cls in unit.config_classes.items() if cls == UNMATCHED]
    return list(unit.configs)


def _deferred_family(unit) -> str | None:
    """The deferred stylistic-set bucket for a window whose novel behavior never appears under the default config, or None when it is default-reachable. ss04 takes precedence over ss10 over the ss02/ss03/ss05 tail when a window is gated by more than one set."""
    novel = _unmatched_configs(unit)
    if any(_config_features(config) == frozenset() for config in novel):
        return None
    tags = {tag for config in novel for tag in _config_features(config)}
    if "ss04" in tags:
        return "deferred-ss04"
    if "ss10" in tags:
        return "deferred-ss10"
    return "deferred-ss03"


def _entry_family(cell_token: str) -> str:
    """The family that owns a cell's entry (left) side — its lead component, e.g. qsTea_qsOy/... -> qsTea. This is the glyph a predecessor joins into."""
    return cell_token.split("/", 1)[0].split("_", 1)[0]


def _exit_family(cell_token: str) -> str:
    """The family that owns a cell's exit (right) side — its trailing component, e.g. qsTea_qsOy/... -> qsOy. This is the glyph that joins into a follower, so it names the left side of the seam that follows the cell."""
    return cell_token.split("/", 1)[0].rsplit("_", 1)[-1]


_SEAM_RANK = {"break": -1}


def _seam_rank(token: str) -> int:
    if token in _SEAM_RANK:
        return _SEAM_RANK[token]
    if token.startswith("y") and token[1:].isdigit():
        return int(token[1:])
    return -1


def _primary_change(enriched: EnrichedUnit) -> tuple[str, str, str, str] | None:
    """The first inter-cell gap whose seam token changed, as (left family, right family, before token, after token). None when the seams are unchanged (or their lengths disagree, e.g. a ligature formed/dissolved — left to the catch-all)."""
    before = enriched.before_seams
    after = enriched.after_seams
    cells = enriched.after_cells
    if len(before) != len(after) or len(after) + 1 != len(cells):
        return None
    for index, (was, now) in enumerate(zip(before, after)):
        if was != now:
            # The seam is the left cell's exit (its trailing component, for a ligature) joining the right cell's entry (its lead component).
            return (_exit_family(cells[index]), _entry_family(cells[index + 1]), was, now)
    return None


def assign_family(enriched: EnrichedUnit) -> str:
    """The verdict family id for one UNMATCHED unit. Total: every unit resolves to a family, with unmatched-misc as the catch-all."""
    deferred = _deferred_family(enriched.unit)
    if deferred is not None:
        return deferred

    change = _primary_change(enriched)
    if change is None:
        return "extension-non-summing"

    left, right, was, now = change
    gained = _seam_rank(now) > _seam_rank(was)
    lost = _seam_rank(now) < _seam_rank(was)

    if gained:
        if left == "qsTea" and right == "qsIt":
            return "tea-it-xheight"
        if left == "qsOy" and right == "qsIt":
            return "oy-it-baseline"
        if "qsMay" in (left, right) and "qsUtter" in (left, right):
            return "may-utter-gains"
        if "qsNo" in (left, right):
            return "no-chain-gains"
        return "unmatched-misc"
    if lost:
        return "seam-loss-withdrawal"
    return "unmatched-misc"
