"""The change groups: the classes the review crate sorts the units no ledger ruling covers into, by the kind of visible change each shows, so each group gets its own class and shard on the review corpus. Two kinds of unit take a group: an UNMATCHED unit, and a unit of an `ink_identical` ledger class whose picture changes, which the build lists under the change it shows rather than under a class that says it draws exactly as in the old font, unless its class exempts it from verdicts or a machine check approves it. The grouping is for presentation only: it reads one enriched unit, its ink deltas and the set of `ink_identical` ledger ids, changes no shaping, and writes no ledger predicate. Every such unit takes a group, and a changed junction token the rules cannot read fails the build, so the rules are total. This module holds the groups' order (`CHANGE_GROUP_ORDER`) and reads their titles, summaries and whys from rebuild/review/change-groups.yaml (`load_change_group_prose`), which only the manifest reads, so rewording one re-enriches no unit.

The first rule that applies decides:

- The set gate. A config is unexplained when it maps to UNMATCHED, or to an `ink_identical` class while the config's ink changed (it is in the unit's ink deltas), because a claim that the letters draw identically cannot explain a picture that changed. A unit with no per-config classes has every config unexplained. When no unexplained config is the default, the difference shows only under stylistic sets, and the unit goes to the group of the first of ss04, ss10, ss03 and ss05 that is on in every unexplained config, or to `only-with-several-sets` when none is.
- The ligature guard. When the before and after junction counts differ, or the junctions are not one fewer than the cells, a ligature formed or split: `ligature-forms-or-splits`.
- The junction kind. A new join (`break` to `yN`) decides first, then a dropped join (`yN` to `break`), then a change of height. The leftmost junction of the deciding kind names the pair: a new join there at y0 goes to `new-join-at-baseline` and one higher to `new-join-at-xheight-or-top`, and a height move goes to `join-lowered` or `join-raised`.
- The width. When no junction changed, the highlight's advance totals, which the enricher takes under the unit's first config, decide between `same-joins-narrower`, `same-joins-wider` and `same-joins-same-width`.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import yaml

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


class ChangeGroupProse(NamedTuple):
    title: str
    summary: str
    why: str


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
