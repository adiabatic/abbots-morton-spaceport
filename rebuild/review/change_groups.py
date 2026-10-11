"""Sort the units no ledger ruling covers into change groups by the kind of visible change each shows, so each group gets its own class and shard on the review corpus. Two kinds of unit take a group: an UNMATCHED unit, and a unit of an `ink_identical` ledger class whose picture changes (`reroutes`), which the build lists under the change it shows rather than under a class that says it draws exactly as in the old font. The grouping is for presentation only: it reads one enriched unit, its ink deltas and the set of `ink_identical` ledger ids, changes no shaping, and writes no ledger predicate. `assign_change_group` returns a group for every such unit, and raises on a changed junction token it cannot read, so the rules are total. The groups' titles, summaries and whys live in rebuild/review/change-groups.yaml (`load_change_group_prose`), which only the manifest reads, so rewording one re-enriches no unit.

The first rule that applies decides:

- The set gate. A config is unexplained when it maps to UNMATCHED, or to an `ink_identical` class while the config's ink changed (it is in the unit's ink deltas), because a claim that the letters draw identically cannot explain a picture that changed. A unit with no per-config classes has every config unexplained. When no unexplained config is the default, the difference shows only under stylistic sets, and the unit goes to the group of the first of ss04, ss10, ss03 and ss05 that is on in every unexplained config, or to `only-with-several-sets` when none is.
- The ligature guard. When the before and after junction counts differ, or the junctions are not one fewer than the cells, a ligature formed or split: `ligature-forms-or-splits`.
- The junction kind. A new join (`break` to `yN`) decides first, then a dropped join (`yN` to `break`), then a change of height. The leftmost junction of the deciding kind names the pair: a new join there at y0 goes to `new-join-at-baseline` and one higher to `new-join-at-xheight-or-top`, and a height move goes to `join-lowered` or `join-raised`.
- The width. When no junction changed, the highlight's advance totals, which the enricher takes under the unit's first config, decide between `same-joins-narrower`, `same-joins-wider` and `same-joins-same-width`.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import NamedTuple, Protocol

import yaml

UNMATCHED = "UNMATCHED"

CHANGE_GROUP_PROSE = Path(__file__).with_name("change-groups.yaml")

CHANGE_GROUP_ORDER = (
    "new-join-at-baseline",
    "new-join-at-xheight-or-top",
    "join-dropped",
    "join-lowered",
    "join-raised",
    "ligature-forms-or-splits",
    "same-joins-narrower",
    "same-joins-wider",
    "same-joins-same-width",
    "only-with-ss03",
    "only-with-ss04",
    "only-with-ss05",
    "only-with-ss10",
    "only-with-several-sets",
)

SET_GATE = (
    ("ss04", "only-with-ss04"),
    ("ss10", "only-with-ss10"),
    ("ss03", "only-with-ss03"),
    ("ss05", "only-with-ss05"),
)
SEVERAL_SETS = "only-with-several-sets"


class UnitConfigs(Protocol):
    """The part of a unit the set gate reads. `audit.Unit` satisfies it."""

    @property
    def config_classes(self) -> Mapping[str, str]: ...

    @property
    def configs(self) -> tuple[str, ...]: ...


class ChangeGroupInput(Protocol):
    """What `assign_change_group` reads from an enriched unit: the unit's configs, the junction and cell tuples, and the highlight rects, of which only `advance_total` is read. `enrich.EnrichedUnit` satisfies it, and a test stub can without building one."""

    @property
    def unit(self) -> UnitConfigs: ...

    @property
    def before_junctions(self) -> tuple[str, ...]: ...

    @property
    def after_junctions(self) -> tuple[str, ...]: ...

    @property
    def after_cells(self) -> tuple[str, ...]: ...

    @property
    def highlight_before(self) -> Mapping[str, int]: ...

    @property
    def highlight_after(self) -> Mapping[str, int]: ...


class ChangeGroupProse(NamedTuple):
    title: str
    summary: str
    why: str


def reroutes(
    class_id: str, no_verdict: bool, machine_approved: bool, identical_classes: Collection[str]
) -> bool:
    """Whether a unit of ledger class `class_id` is listed under a change group instead: its class is one of `identical_classes` (the ledger's `ink_identical` ids), the class does not exempt it from verdicts, and no machine check approves it. A no-verdict class is skipped so that an identical class exempt by ruling never moves its units into the queue."""
    return class_id in identical_classes and not no_verdict and not machine_approved


def unexplained_configs(
    unit: UnitConfigs, ink_deltas: Mapping[str, str], identical_classes: Collection[str]
) -> list[str]:
    """Return the configs no ledger ruling explains, in the unit's config order: those mapped to UNMATCHED, and those mapped to one of `identical_classes` whose ink changed. With no per-config classes, every config is unexplained."""
    classes = unit.config_classes
    if not classes:
        return list(unit.configs)
    return [
        config
        for config in unit.configs
        if classes.get(config) == UNMATCHED
        or (classes.get(config) in identical_classes and config in ink_deltas)
    ]


def _config_features(config: str) -> frozenset[str]:
    return frozenset() if config == "default" else frozenset(config.split("+"))


def set_gate_group(configs: Iterable[str]) -> str | None:
    """Return the set group for a unit whose unexplained configs are `configs`, or None when one of them is the default, in which case the junction and width rules decide. Raises on an empty list, since a unit that takes a change group always has a config no ruling explains."""
    features = [_config_features(config) for config in configs]
    if not features:
        raise ValueError("a unit that takes a change group has a config no ledger ruling explains")
    if any(not feature for feature in features):
        return None
    for tag, group in SET_GATE:
        if all(tag in feature for feature in features):
            return group
    return SEVERAL_SETS


def _height(token: str) -> int | None:
    """Return a junction token's join height, or None for `break`."""
    if token == "break":
        return None
    if token.startswith("y") and token[1:].isdigit():
        return int(token[1:])
    raise ValueError(f"a changed junction token is break or yN, not {token!r}")


def _junction_group(before: tuple[str, ...], after: tuple[str, ...]) -> str | None:
    """Return the group the changed junctions decide, or None when every junction is as the old font had it."""
    changed = []
    for was, now in zip(before, after):
        if was != now:
            old, new = _height(was), _height(now)
            if old != new:
                changed.append((old, new))
    if not changed:
        return None
    gained = [new for old, new in changed if old is None]
    if gained:
        return "new-join-at-baseline" if gained[0] == 0 else "new-join-at-xheight-or-top"
    if any(new is None for _old, new in changed):
        return "join-dropped"
    old, new = changed[0]
    assert old is not None and new is not None
    return "join-lowered" if new < old else "join-raised"


def assign_change_group(
    enriched: ChangeGroupInput, ink_deltas: Mapping[str, str], identical_classes: Collection[str]
) -> str:
    """Return the change group for one unit that takes one, an UNMATCHED unit or one `reroutes` moves: the set gate, the ligature guard, the junction kind, then the width, as the module docstring states. `ink_deltas` are the unit's per-config ink deltas and `identical_classes` the ledger's `ink_identical` ids, so the group is a function of the unit and that constant set alone."""
    gated = set_gate_group(unexplained_configs(enriched.unit, ink_deltas, identical_classes))
    if gated is not None:
        return gated
    before = enriched.before_junctions
    after = enriched.after_junctions
    if len(before) != len(after) or len(after) + 1 != len(enriched.after_cells):
        return "ligature-forms-or-splits"
    by_junction = _junction_group(before, after)
    if by_junction is not None:
        return by_junction
    width = enriched.highlight_after["advance_total"] - enriched.highlight_before["advance_total"]
    if width < 0:
        return "same-joins-narrower"
    if width > 0:
        return "same-joins-wider"
    return "same-joins-same-width"


def load_change_group_prose(path: Path = CHANGE_GROUP_PROSE) -> dict[str, ChangeGroupProse]:
    """Return each change group's title, summary and why from the prose file, keyed by id in `CHANGE_GROUP_ORDER`. The file is a list of `id`, `title`, `summary` and `why` mappings in that order; any other id, order or a missing or empty field raises."""
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    ids = [entry.get("id") for entry in entries]
    if ids != list(CHANGE_GROUP_ORDER):
        raise ValueError(f"{path}: the groups must be CHANGE_GROUP_ORDER in order, not {ids}")
    prose: dict[str, ChangeGroupProse] = {}
    for entry in entries:
        fields = {key: entry.get(key) for key in ChangeGroupProse._fields}
        empty = [key for key, value in fields.items() if not isinstance(value, str) or not value.strip()]
        if empty:
            raise ValueError(f"{path}: {entry['id']} needs a nonempty {', '.join(empty)}")
        prose[entry["id"]] = ChangeGroupProse(**{key: value.strip() for key, value in fields.items()})
    return prose
