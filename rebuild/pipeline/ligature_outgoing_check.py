"""Build-time preservation of each ligature stance's declared outgoing contract.

The check asks the Rust settlement engine about the formed rune's outgoing capability, independently of its incoming requirements and of the unformed trailing component's internal left neighbor. Each mapped stance is compared with the same ligature carrying its source stance's outgoing scopes and applicable policy. A formation guard rescuing an omitted exit does not discharge this contract. The ordinary conformance sweep proves the realized, fully contextual stream; this check proves the author's per-stance outgoing declaration before tables are built.

The domain contains every modeled follower and second token, the splitting boundaries and namer dot, and witnesses for authored deeper condition chains, including the follower's chains shifted one position into the window. Both joining and yielding must agree. Windows stream through the existing settlement batch bound, with one projection pair resident at a time. The isolated overlay preempts formation and is reported separately.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import batched, product
from typing import Iterable, Mapping

from rebuild.pipeline import conform, kernel_exec, spec_load
from rebuild.pipeline.model import (
    Condition,
    Policy,
    PolicyRecord,
    ResolvedSpec,
    Rune,
    Settled,
    Stance,
    When,
    isolated_overlay_active,
)
from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, ZWNJ, LeftContext, RightToken, SettleError
from rebuild.tools import console

_BOUNDARIES = (EDGE, SPACE, ZWNJ, NAMER_DOT)


class LigatureOutgoingError(ValueError):
    """A formed ligature loses a join or yield from its declared outgoing source."""


def _records(policy: Policy) -> Iterable[PolicyRecord]:
    for kind in ("refuse", "prefer", "extend", "contract", "resolve"):
        yield from getattr(policy, kind)


def _single_stance(rune: Rune, stance: Stance, policy: Policy) -> Rune:
    surface = replace(
        stance.surface, require=tuple(side for side in stance.surface.require if side != "entry")
    )
    return replace(
        rune,
        stances={stance.name: replace(stance, surface=surface)},
        policy=replace(policy, order=(stance.name,)),
    )


def _projected_policy(policy: Policy, stance_name: str) -> Policy:
    def applies(record: PolicyRecord) -> bool:
        if record.stance not in (None, stance_name):
            return False
        return all(
            pattern is None or pattern.get("stance", stance_name) == stance_name
            for pattern in (record.cell, record.over, record.pick)
        )

    return replace(
        policy,
        **{
            kind: tuple(record for record in getattr(policy, kind) if applies(record))
            for kind in ("refuse", "prefer", "extend", "contract", "resolve")
        },
    )


def _projection_pair(
    spec: ResolvedSpec, rune: Rune, stance: Stance, declaration: Mapping
) -> tuple[ResolvedSpec, ResolvedSpec]:
    assert rune.sequence
    source_rune = spec.runes[rune.sequence[-1]]
    source = source_rune.stances[declaration["stance"]]
    exceptions = declaration.get("exceptions", {})
    expected_groups = dict(rune.policy.groups)

    def condition(value: Condition) -> Condition:
        groups = []
        for group in value.klass:
            if group in source_rune.policy.groups:
                key = f"{source_rune.name}.{group}"
                expected_groups[key] = source_rune.policy.groups[group]
                groups.append(key)
            else:
                groups.append(group)
        return replace(
            value,
            klass=tuple(groups),
            except_=tuple(condition(item) for item in value.except_),
            then=condition(value.then) if value.then else None,
        )

    def when(value: When) -> When:
        return replace(value, right=condition(value.right) if value.right else None)

    expected_exits = dict(stance.surface.exits)
    for height, row in source.surface.exits.items():
        if f"surface.exits.{height}" in exceptions:
            continue
        local = expected_exits.get(height, row)
        expected_exits[height] = replace(local, scope=tuple(condition(item) for item in row.scope))
    policies: dict[str, tuple[PolicyRecord, ...]] = {}
    for kind in ("refuse", "prefer", "extend", "contract", "resolve"):
        inherited = []
        for index, record in enumerate(getattr(source_rune.policy, kind)):
            path = f"policy.{kind}[{index}]"
            if path in exceptions:
                replacement = declaration.get("replacements", {}).get(path)
                if replacement is not None:
                    local_index = int(replacement.split("[")[1][:-1])
                    inherited.append(getattr(rune.policy, kind)[local_index])
                continue
            projected = spec_load.outgoing_policy_record(record, source, stance.name)
            if projected is not None:
                inherited.append(replace(projected, when=when(projected.when)))
        policies[kind] = tuple(inherited)
    actual_rune = _single_stance(rune, stance, _projected_policy(rune.policy, stance.name))
    expected_unlocks = list(stance.surface.unlocks)
    for index, unlock in enumerate(source.surface.unlocks):
        if unlock.exit is None or unlock.entry is not None or unlock.pairing is not None:
            continue
        if unlock.when and (
            unlock.when.left is not None or unlock.when.self_entry is not None or unlock.when.word is not None
        ):
            continue
        inherited_unlock = replace(unlock, when=when(unlock.when) if unlock.when else None)
        if f"surface.unlocks[{index}]" not in exceptions and inherited_unlock not in expected_unlocks:
            expected_unlocks.append(inherited_unlock)
    expected_stance = replace(
        stance, surface=replace(stance.surface, exits=expected_exits, unlocks=tuple(expected_unlocks))
    )
    expected_policy = replace(Policy(order=(stance.name,), groups=expected_groups), **policies)
    expected_rune = _single_stance(rune, expected_stance, expected_policy)
    return (
        replace(spec, runes={**spec.runes, rune.name: actual_rune}),
        replace(spec, runes={**spec.runes, rune.name: expected_rune}),
    )


def _condition_paths(condition: Condition) -> Iterable[tuple[Condition, ...]]:
    if condition.then is not None:
        yield (condition, condition.then)
        for tail in _condition_paths(condition.then):
            yield (condition, *tail)
    for exclusion in condition.except_:
        yield from _condition_paths(exclusion)


def _condition_tokens(
    spec: ResolvedSpec, rune: Rune, condition: Condition, tokens: tuple[RightToken, ...]
) -> tuple[RightToken, ...]:
    families = set(condition.family)
    for klass in condition.klass:
        families.update(rune.policy.groups.get(klass, spec.registry.predicate_classes.get(klass, ())))
    if families:
        return tuple(token for token in tokens if token.kind == "letter" and token.letter in families)
    if condition.is_token is not None:
        return tuple(
            token
            for token in tokens
            if token.kind != "letter"
            and (condition.is_token == "boundary" or token.kind == condition.is_token)
        )
    return tokens


def _right_windows(spec: ResolvedSpec) -> Iterable[tuple[RightToken, ...]]:
    tokens = tuple(RightToken("letter", name) for name in spec.runes) + _BOUNDARIES
    yield from ((first, second, EDGE, EDGE) for first, second in product(tokens, repeat=2))
    deep: set[tuple[RightToken, ...]] = set()
    for rune in spec.runes.values():
        for record in _records(rune.policy):
            if record.when.right is None:
                continue
            for path in _condition_paths(record.when.right):
                for window in product(
                    *(_condition_tokens(spec, rune, condition, tokens) for condition in path)
                ):
                    candidates = []
                    if len(window) >= 3:
                        candidates.append(window)
                    if len(window) <= 3:
                        candidates.append((RightToken("letter", rune.name), *window))
                    for candidate in candidates:
                        padded = candidate + (EDGE,) * (4 - len(candidate))
                        if padded not in deep:
                            deep.add(padded)
                            yield padded


def _capability_answer(result: Mapping) -> Settled | None:
    try:
        return kernel_exec.trace_of(result).settled
    except SettleError as error:
        if error.bucket != "E-UNREACHABLE":
            raise
        return None


def validate_ligature_outgoing(spec: ResolvedSpec, rune_raws: Mapping[str, dict]) -> dict:
    """Raise with the mapped stance, configuration and window when formation loses an unexcepted source join or yield. Returned counters belong in the build summary, never in checked-in prose."""
    mappings = []
    for name, rune in spec.runes.items():
        if not rune.sequence:
            continue
        for stance_name, stance in rune.stances.items():
            declaration = dict(rune_raws[name]["stances"][stance_name].get("outgoing", {}))
            if "exception" not in declaration:
                if "stance" not in declaration:
                    declaration["stance"] = next(iter(spec.runes[rune.sequence[-1]].stances))
                mappings.append((rune, stance, declaration))
    configs = []
    overlays = []
    for config in conform.ACCEPTANCE_CONFIGS:
        features = frozenset() if config == "default" else frozenset(config.split("+"))
        if not features.issubset(spec.registry.features):
            continue
        if isolated_overlay_active(spec, features):
            overlays.append(config)
        else:
            configs.append((config, features))
    checked = 0
    completed = 0
    for rune, stance, declaration in mappings:
        actual_spec, expected_spec = _projection_pair(spec, rune, stance, declaration)
        for config, features in configs:
            windows = ((left, right) for right in _right_windows(spec) for left in _BOUNDARIES)
            for batch in batched(windows, kernel_exec.SETTLE_CASE_BATCH_SIZE):
                cases = [
                    kernel_exec.case_line(LeftContext(left.kind), RightToken("letter", rune.name), right)
                    for left, right in batch
                ]
                actual = kernel_exec.settle_cases(actual_spec, cases, features, decode=_capability_answer)
                expected = kernel_exec.settle_cases(expected_spec, cases, features, decode=_capability_answer)
                for (left, right), got, wanted in zip(batch, actual, expected, strict=True):
                    checked += 1
                    if wanted is None:
                        continue
                    expected_join = wanted.seam is not None
                    actual_join = got is not None and got.seam is not None
                    if expected_join == actual_join:
                        continue
                    following = " ".join(token.rune or token.kind for token in right)
                    mismatch = f"lost {wanted.seam} seam" if expected_join else "lost outgoing yield"
                    raise LigatureOutgoingError(
                        f"{rune.name}.stances.{stance.name}.outgoing: {mismatch} from {rune.sequence[-1]}.{declaration['stance']} under {config}; left={left.kind}, right=[{following}]"
                    )
            completed += 1
            console.progress(
                completed, len(mappings) * len(configs), f"outgoing {rune.name}.{stance.name} [{config}]"
            )
    return {
        "mappings": len(mappings),
        "windows": checked,
        "configurations": [name for name, _features in configs],
        "formation_preempted": overlays,
    }
