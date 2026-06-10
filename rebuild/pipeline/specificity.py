"""The section 6.2 extensional specificity order (doc/rebuild-design.md).

A record's specificity is computed extensionally: every constrained axis of its `when:` expands to its concrete match set over the finite registry. Record A outranks B iff A's match set is a subset of B's on every axis B constrains, with at least one strict subset. Within one axis, narrowness is set inclusion after expansion, so a literal family list, a predicate class, and a mixed literal-plus-class condition are all comparable for free. Non-nested overlap with conflicting demands is the hard error E-INCOMPARABLE (refuse to guess); a record-vs-record tie among equals demanding different outcomes is E-AMBIGUOUS. Evaluation is stratified: predicate-class membership comes pre-resolved from capability (the registry), so expanding policy conditions never re-enters settlement.

The design budgets extra paranoia for this module (section 15.5): it ships with its own regression-test class (rebuild/test_specificity.py), including synthetic analogs of the two named design cases, which need families outside the M1 alphabet and therefore run on fixture specs.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence

from rebuild.pipeline.model import Condition, PolicyRecord, ResolvedSpec, When

STROKES = frozenset({"horizontal", "vertical", "diagonal"})
IS_VALUES = frozenset({"edge", "space", "zwnj", "namer-dot", "letter"})
WORD_VALUES = frozenset({"initial", "medial", "final", "isolated"})
SELF_VALUES = frozenset({"live", "none"})


class SpecificityError(Exception):
    pass


class EIncomparableError(SpecificityError):
    """Non-nested overlap on a shared axis with conflicting demands (design section 6.2): neither record's match set contains the other's, both match the window at hand, and they demand different outcomes. Requires a recorded `resolve`."""


class EAmbiguousError(SpecificityError):
    """A genuine record-vs-record tie: equal match sets, different demands."""


class Ordering(enum.Enum):
    A_OUTRANKS = "a-outranks"
    B_OUTRANKS = "b-outranks"
    EQUAL = "equal"
    INCOMPARABLE = "incomparable"


def class_members(spec: ResolvedSpec, name: str, owner: str | None = None) -> frozenset[str]:
    """Resolve a `class:` reference to family names: registry predicate classes first, then the owning rune's local groups, then any rune's groups (spec_load lints cross-rune duplicates)."""
    members = spec.registry.predicate_classes.get(name)
    if members is not None:
        return members
    if owner is not None and owner in spec.runes:
        local = spec.runes[owner].policy.groups.get(name)
        if local is not None:
            return frozenset(local)
    for rune in spec.runes.values():
        local = rune.policy.groups.get(name)
        if local is not None:
            return frozenset(local)
    raise SpecificityError(f"unknown class or group: {name!r}")


def _family_universe(spec: ResolvedSpec) -> frozenset[str]:
    return frozenset(spec.registry.families)


def _condition_constrains_only_family(cond: Condition) -> bool:
    return bool(cond.family or cond.klass) and not (
        cond.stance
        or cond.joined_at is not None
        or cond.stroke is not None
        or cond.is_token is not None
        or cond.then is not None
        or cond.except_
    )


def _family_set(spec: ResolvedSpec, cond: Condition, owner: str | None) -> frozenset[str] | None:
    """The family-axis match set, or None when the axis is unconstrained. `family:` and `class:` on one condition are conjunctive; `except:` entries that constrain only the family axis subtract, and multi-axis excepts are conservatively ignored here (an over-approximation that can only demote a record toward INCOMPARABLE — the refuse-to-guess direction)."""
    base: frozenset[str] | None = None
    if cond.family:
        base = frozenset(cond.family)
    for klass in cond.klass:
        members = class_members(spec, klass, owner)
        base = members if base is None else base & members
    if cond.except_:
        carve: set[str] = set()
        for ex in cond.except_:
            if _condition_constrains_only_family(ex):
                carved = _family_set(spec, ex, owner)
                if carved is not None:
                    carve |= carved
        if carve:
            if base is None:
                base = _family_universe(spec)
            base = base - frozenset(carve)
    return base


def _is_set(cond: Condition) -> frozenset[str] | None:
    if cond.is_token is None:
        return None
    if cond.is_token == "boundary":
        return frozenset({"edge", "space", "zwnj", "namer-dot"})
    return frozenset({cond.is_token})


def _side_axes(
    spec: ResolvedSpec, cond: Condition | None, owner: str | None, prefix: str
) -> dict[str, frozenset[str]]:
    axes: dict[str, frozenset[str]] = {}
    if cond is None:
        return axes
    families = _family_set(spec, cond, owner)
    if families is not None:
        axes[f"{prefix}.family"] = families
    if cond.stance:
        axes[f"{prefix}.stance"] = frozenset(cond.stance)
    if cond.joined_at is not None:
        axes[f"{prefix}.joined_at"] = frozenset({cond.joined_at})
    if cond.stroke is not None:
        axes[f"{prefix}.stroke"] = frozenset({cond.stroke})
    is_values = _is_set(cond)
    if is_values is not None:
        axes[f"{prefix}.is"] = is_values
    if cond.then is not None:
        axes.update(_side_axes(spec, cond.then, owner, f"{prefix}.then"))
    return axes


def axis_sets(spec: ResolvedSpec, when: When, owner: str | None = None) -> dict[str, frozenset[str]]:
    """Every constrained axis of `when`, expanded to its concrete match set. Missing key = unconstrained (the axis universe)."""
    axes: dict[str, frozenset[str]] = {}
    axes.update(_side_axes(spec, when.left, owner, "left"))
    axes.update(_side_axes(spec, when.right, owner, "right"))
    if when.self_entry is not None:
        axes["self.entry"] = frozenset({when.self_entry})
    if when.self_exit is not None:
        axes["self.exit"] = frozenset({when.self_exit})
    if when.word is not None:
        axes["word"] = frozenset({when.word})
    if when.feature is not None:
        axes["feature"] = frozenset({when.feature})
    return axes


def outranks(
    spec: ResolvedSpec,
    a: PolicyRecord,
    b: PolicyRecord,
    owner_a: str | None = None,
    owner_b: str | None = None,
) -> Ordering:
    """Extensional comparison of two records' conditions per design section 6.2."""
    axes_a = axis_sets(spec, a.when, owner_a)
    axes_b = axis_sets(spec, b.when, owner_b)
    a_le_b = True
    b_le_a = True
    strict_a = False
    strict_b = False
    for axis in set(axes_a) | set(axes_b):
        set_a = axes_a.get(axis)
        set_b = axes_b.get(axis)
        if set_a is None and set_b is None:
            continue
        if set_b is None:
            strict_a = True
            b_le_a = False
        elif set_a is None:
            strict_b = True
            a_le_b = False
        else:
            if not set_a <= set_b:
                a_le_b = False
            elif set_a < set_b:
                strict_a = True
            if not set_b <= set_a:
                b_le_a = False
            elif set_b < set_a:
                strict_b = True
    if a_le_b and b_le_a and not strict_a and not strict_b:
        return Ordering.EQUAL
    if a_le_b and strict_a:
        return Ordering.A_OUTRANKS
    if b_le_a and strict_b:
        return Ordering.B_OUTRANKS
    return Ordering.INCOMPARABLE


def pick_most_specific(
    spec: ResolvedSpec,
    records: Sequence[PolicyRecord],
    owners: Sequence[str | None] | None = None,
    demand: Callable[[PolicyRecord], object] | None = None,
) -> PolicyRecord:
    """Among records that all matched one concrete window, pick the unique most-specific one. Nesting resolves silently (the narrow record wins by membership); multiple maximal records demanding the same thing collapse to the first in declaration order; multiple maximal records with different demands are E-INCOMPARABLE — the records already co-matched a window, so the overlap is a fact, not a possibility."""
    if not records:
        raise ValueError("pick_most_specific needs at least one record")
    if owners is None:
        owners = [None] * len(records)
    if demand is None:
        # `ok:` defaults to [by, by] (design section 3.3), so an explicit band equal to the default is the same demand.
        demand = lambda record: (
            record.by,
            record.ok or ((record.by, record.by) if record.by is not None else None),
            record.bind,
            record.trim,
            record.split,
            record.stance,
            record.entry,
            record.exit,
        )
    indexed = list(zip(records, owners))
    maximal: list[PolicyRecord] = []
    for record, owner in indexed:
        beaten = False
        for other, other_owner in indexed:
            if other is record:
                continue
            if outranks(spec, other, record, other_owner, owner) is Ordering.A_OUTRANKS:
                beaten = True
                break
        if not beaten:
            maximal.append(record)
    if len(maximal) == 1:
        return maximal[0]
    demands = {demand(record) for record in maximal}
    if len(demands) == 1:
        return maximal[0]
    described = "; ".join(str(record.provenance) if record.provenance else record.kind for record in maximal)
    raise EIncomparableError(
        f"E-INCOMPARABLE: {len(maximal)} records co-match one window with non-nested conditions and conflicting demands: {described}. Record a resolve with migrated: provenance."
    )
