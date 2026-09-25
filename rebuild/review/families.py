"""Group the UNMATCHED windows (joins the rebuild makes that the old font did not) into verdict families, so each family gets its own class and shard on the review surface. The grouping is for presentation only: it reads each unit's settled seams, changes no shaping, and writes no ledger predicate, and the oracle stays dirty until the families are adjudicated. `assign_family` returns a family for every UNMATCHED unit, with `unmatched-misc` as the catch-all.

Two things decide a family:

- Config gating. A window that is novel only under a stylistic set goes to a deferred family named for the set (ss04, ss10, or ss03 for the ss02/ss03/ss05 cases), which sorts last. A window that is novel under the default config gets a default family.

- The first changed seam. Among the default families, the first gap whose before and after seam tokens differ names the family by its left and right letters and by whether the join was gained (`break` to `yN`, or a raised seam) or lost (`yN` to `break`, or a lowered seam). Gains at ·Tea·It, at ·Oy·It, and between ·May and ·Utter each have a family, other gains that touch ·No go to `no-chain-gains`, and the remaining gains go to `unmatched-misc`. Every loss goes to `seam-loss-withdrawal`. A window with no changed seam, or whose seam and cell counts do not line up, goes to `extension-non-summing`.
"""

from __future__ import annotations

from typing import Mapping, Protocol

UNMATCHED = "UNMATCHED"


class UnitConfigs(Protocol):
    """The part of a unit that config gating reads. `audit.Unit` satisfies it."""

    @property
    def config_classes(self) -> Mapping[str, str]: ...

    @property
    def configs(self) -> tuple[str, ...]: ...


class FamilyInput(Protocol):
    """What `assign_family` reads from an enriched unit: the unit's configs and the seam and cell tuples `_primary_change` scans. A test stub can satisfy it without building a whole `EnrichedUnit`."""

    @property
    def unit(self) -> UnitConfigs: ...

    @property
    def before_seams(self) -> tuple[str, ...]: ...

    @property
    def after_seams(self) -> tuple[str, ...]: ...

    @property
    def after_cells(self) -> tuple[str, ...]: ...


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


def _unmatched_configs(unit: UnitConfigs) -> list[str]:
    """Return the configs in which the unit's window is UNMATCHED, or every config when `config_classes` is empty (a fully UNMATCHED triple)."""
    if unit.config_classes:
        return [config for config, cls in unit.config_classes.items() if cls == UNMATCHED]
    return list(unit.configs)


def deferred_family(unit: UnitConfigs) -> str | None:
    """Return the deferred family for a window that is UNMATCHED only under stylistic sets, or None when it is UNMATCHED under the default config. When several sets apply, ss04 wins over ss10, and ss10 over any other set, which goes to `deferred-ss03`."""
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
    """Return the family on a cell's entry side, which is a ligature's first component: `qsTea_qsOy/...` gives `qsTea`."""
    return cell_token.split("/", 1)[0].split("_", 1)[0]


def _exit_family(cell_token: str) -> str:
    """Return the family on a cell's exit side, which is a ligature's last component: `qsTea_qsOy/...` gives `qsOy`."""
    return cell_token.split("/", 1)[0].rsplit("_", 1)[-1]


_SEAM_RANK = {"break": -1}


def _seam_rank(token: str) -> int:
    if token in _SEAM_RANK:
        return _SEAM_RANK[token]
    if token.startswith("y") and token[1:].isdigit():
        return int(token[1:])
    return -1


def _primary_change(enriched: FamilyInput) -> tuple[str, str, str, str] | None:
    """Return the first gap between cells whose seam token changed, as (left family, right family, before token, after token). Return None when no seam changed, or when the seam counts differ or do not match the cell count, as when a ligature forms or splits."""
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


def assign_family(enriched: FamilyInput) -> str:
    """Return the verdict family id for one UNMATCHED unit."""
    deferred = deferred_family(enriched.unit)
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
