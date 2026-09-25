"""Join-consistency checks, join warnings, and pending-entry guard tables for compiled Quikscript glyphs.

``JoinReachability`` indexes a ``dict[str, JoinGlyph]`` by family, anchor Y, and pair selector. Its fields are named after the matching ``_JoinAnalysis`` fields in ``quikscript_fea``, but it is built from ``JoinGlyph`` attributes alone, without the FEA emitter's lookup ordering, cycle detection, and policy gates. ``glyph_compiler.compile_glyph_set`` runs ``validate_join_consistency`` and ``warn_join_contract_issues`` on the Senior build, and ``quikscript_fea._emit_quikscript_calt`` takes its guard tables from ``derive_pending_bk_entry_guards`` and ``derive_pending_fwd_strip_guards``.
"""

import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from quikscript_ir import (
    Anchor,
    JoinGlyph,
    has_entry_preserving_exit_noentry_sibling,
)
from quikscript_fea import (
    _LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS,
    _analyze_quikscript_joins,
    _can_eventually_exit_at,
    _expand_backward_after_variants,
    _expand_forward_before_variants,
    _expand_join_variants,
    _resolve_noentry_replacement,
)


class JoinContractWarning(UserWarning):
    """A glyph's join-contract metadata fails one of the consistency checks in `collect_join_warnings`."""


class OrphanAnchorWarning(UserWarning):
    """An entry or exit anchor at some Y has no opposite anchor at that Y on any glyph, so no cursive attachment can happen there."""


class NonJoiningNeighborSelectionWarning(UserWarning):
    """The derived join contract dropped a different number of selections than `_EXPECTED_CONTRACT_DROP_COUNT` in `quikscript_fea`.

    A dropped selection is one where the `calt` emitter would pick a variant whose exit (forward) or entry (backward) cannot join the neighbor, with no `before-<family>` / `after-<family>` modifier naming that neighbor. `_JoinContractRecorder.flush` always writes the full list to `tmp/leak-contract-emit.txt`. It emits this warning only when the glyph set contains every letter in `_BASELINE_REPERTOIRE_SENTINELS`, which marks the production glyph set, because the unit tests' small glyph sets drop other counts.
    """


__all__ = [
    "DerivedBkGuard",
    "FwdStripGuard",
    "JoinContractWarning",
    "JoinReachability",
    "NonJoiningNeighborSelectionWarning",
    "OrphanAnchorWarning",
    "collect_join_warnings",
    "derive_pending_bk_entry_guards",
    "derive_pending_fwd_strip_guards",
    "validate_join_consistency",
    "warn_join_contract_issues",
]


@dataclass(frozen=True)
class DerivedBkGuard:
    guard_glyphs: tuple[str, ...]
    before_bases: tuple[str, ...] = ()


@dataclass(frozen=True)
class FwdStripGuard:
    """A bare base that, when it follows a predecessor, should suppress the predecessor's substitution.

    ``mid_base``'s forward substitution picks a stance with no entry at the predecessor's exit Y, so the predecessor's exit would join nothing. ``_emit_narrow_mid_entry_strip_guards`` uses these guards to relax its bare-base skip for each ``(source, variant, exit_y)`` key separately, because relaxing it for every predecessor suppresses too much.
    """

    mid_base: str


class _FwdStripReachability(Protocol):
    @property
    def glyph_meta(self) -> Mapping[str, JoinGlyph]: ...

    @property
    def base_to_variants(self) -> Mapping[str, Iterable[str]]: ...

    @property
    def fwd_replacements(self) -> Mapping[str, Mapping[int, str]]: ...

    @property
    def fwd_pair_overrides(self) -> Mapping[str, Iterable[tuple[str, Iterable[str], Iterable[str]]]]: ...


@dataclass(frozen=True)
class _PairIntent:
    variant_name: str
    left_family: str
    right_family: str
    y: int


# Hand-written `ignore sub` guards for `_emit_pending_bk_entry_guards` in `tools/quikscript_fea.py`. A key `(source, replacement, entry_y)` names the substitution of `source` by `replacement`, where `replacement` has no entry at `entry_y`. For each guard the emitter writes `ignore sub [guard_glyphs] source' [right context]`, so the substitution does not happen after a guard glyph. `before_bases`, when set, limits the right context to variants of those bases. The right context depends on the FEA emitter's call sites and cannot be derived from `JoinReachability`, so the table is written by hand. When adding an entry, match the behavior at the relevant call site in `_emit_quikscript_calt` and check the generated FEA with a byte diff.
_PENDING_BK_ENTRY_GUARDS: dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]] = {
    ("qsTea", "qsTea.ex-y0", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.ex-y0.before-vertical",),
            ("qsAh", "qsTea"),
        ),
        DerivedBkGuard(
            ("qsShe.ex-y0",),
            ("qsTea",),
        ),
    ),
    ("qsTea", "qsTea.half.ex-y5", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.ex-y0.before-vertical",),
            ("qsAwe",),
        ),
        DerivedBkGuard(
            ("qsShe.ex-y0",),
            ("qsTea",),
        ),
    ),
    ("qsTea.en-y0", "qsTea.ex-y0", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.ex-y0.before-vertical",),
            ("qsAh", "qsTea"),
        ),
    ),
    ("qsTea.en-y0", "qsTea.half.ex-y5", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.ex-y0.before-vertical",),
            ("qsAwe",),
        ),
    ),
}

_RESIDUAL_BITMAP_GAPS: frozenset[tuple[str, str, int]] = frozenset()


@dataclass(frozen=True)
class JoinReachability:
    glyph_meta: Mapping[str, JoinGlyph]
    base_to_variants: Mapping[str, frozenset[str]]
    bk_replacements: Mapping[str, Mapping[int, str]]
    pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]]
    fwd_replacements: Mapping[str, Mapping[int, str]]
    fwd_pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]]
    gated_pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...], str], ...]]
    gated_fwd_pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]]
    ligatures: tuple[tuple[str, tuple[str, ...]], ...]
    word_final_pairs: Mapping[str, str]
    entry_classes: Mapping[int, frozenset[str]]

    @classmethod
    def from_join_glyphs(cls, glyph_meta: Mapping[str, JoinGlyph]) -> "JoinReachability":
        base_to_variants_buf: dict[str, set[str]] = {}
        bk_replacements_buf: dict[str, dict[int, str]] = {}
        pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        fwd_replacements_buf: dict[str, dict[int, str]] = {}
        fwd_pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]] = {}
        gated_pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...], str]]] = {}
        gated_fwd_pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...], tuple[str, ...], str]]] = {}
        ligatures_buf: list[tuple[str, tuple[str, ...]]] = []
        word_final_pairs_buf: dict[str, str] = {}
        entry_classes_buf: dict[int, set[str]] = {}

        for glyph_name, meta in glyph_meta.items():
            base_to_variants_buf.setdefault(meta.base_name, set()).add(glyph_name)

            if meta.entry:
                for anchor in meta.entry:
                    entry_classes_buf.setdefault(anchor[1], set()).add(glyph_name)
                if not meta.after:
                    entry_y = meta.entry[0][1]
                    bk_replacements_buf.setdefault(meta.base_name, {}).setdefault(entry_y, glyph_name)

            if meta.after:
                after = tuple(meta.after)
                if meta.gate_feature:
                    gated_pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, after, meta.gate_feature)
                    )
                else:
                    pair_overrides_buf.setdefault(meta.base_name, []).append((glyph_name, after))

            if meta.exit and not meta.before:
                exit_y = meta.exit[0][1]
                fwd_replacements_buf.setdefault(meta.base_name, {}).setdefault(exit_y, glyph_name)

            if meta.before:
                before = tuple(meta.before)
                not_after = tuple(meta.not_after)
                if meta.gate_feature:
                    gated_fwd_pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, before, not_after, meta.gate_feature)
                    )
                else:
                    fwd_pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, before, not_after)
                    )

            if meta.sequence and glyph_name == meta.base_name:
                ligatures_buf.append((glyph_name, meta.sequence))

            if meta.word_final:
                word_final_pairs_buf[meta.base_name] = glyph_name

        return cls(
            glyph_meta=MappingProxyType(dict(glyph_meta)),
            base_to_variants=MappingProxyType(
                {base: frozenset(names) for base, names in base_to_variants_buf.items()}
            ),
            bk_replacements=MappingProxyType(
                {base: MappingProxyType(dict(ys)) for base, ys in bk_replacements_buf.items()}
            ),
            pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in pair_overrides_buf.items()}
            ),
            fwd_replacements=MappingProxyType(
                {base: MappingProxyType(dict(ys)) for base, ys in fwd_replacements_buf.items()}
            ),
            fwd_pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in fwd_pair_overrides_buf.items()}
            ),
            gated_pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in gated_pair_overrides_buf.items()}
            ),
            gated_fwd_pair_overrides=MappingProxyType(
                {base: tuple(items) for base, items in gated_fwd_pair_overrides_buf.items()}
            ),
            ligatures=tuple(ligatures_buf),
            word_final_pairs=MappingProxyType(dict(word_final_pairs_buf)),
            entry_classes=MappingProxyType({y: frozenset(names) for y, names in entry_classes_buf.items()}),
        )


def validate_join_consistency(join_glyphs: Mapping[str, JoinGlyph]) -> None:
    """Raise ``ValueError`` listing every join-selector mismatch in ``join_glyphs``.

    For each stance with a ``select.before`` or ``select.after`` selector and the matching anchor, some reachable variant of each named family must have the opposite anchor at the same Y. This is checked with no stylistic set on and under each stylistic set that gates a pair override. ``_check_concrete_selector_consistency`` then checks each selector against the glyphs it expands to under ``_analyze_quikscript_joins``. Orphan anchors (a Y with an exit but no entry anywhere, or the reverse) are reported as ``OrphanAnchorWarning`` warnings, not errors.
    """
    reachability = JoinReachability.from_join_glyphs(join_glyphs)
    glyph_meta_dict = dict(reachability.glyph_meta)
    base_to_variants_dict = {base: set(variants) for base, variants in reachability.base_to_variants.items()}

    errors: list[str] = []
    _check_join_consistency(
        reachability,
        glyph_meta_dict,
        base_to_variants_dict,
        gated_feature=None,
        errors=errors,
    )
    for ss_tag in _ss_tags(reachability):
        _check_join_consistency(
            reachability,
            glyph_meta_dict,
            base_to_variants_dict,
            gated_feature=ss_tag,
            errors=errors,
        )
    _check_concrete_selector_consistency(glyph_meta_dict, errors)
    _warn_orphans(reachability)
    if errors:
        raise ValueError("Join consistency mismatches:\n" + "\n".join(f"  - {e}" for e in errors))


def collect_join_warnings(join_glyphs: Mapping[str, JoinGlyph]) -> tuple[str, ...]:
    reachability = JoinReachability.from_join_glyphs(join_glyphs)
    forward_intents, backward_intents = _collect_pair_intents(
        reachability,
        include_gated_before=True,
    )
    coverage_forward_intents, coverage_backward_intents = _collect_pair_intents(
        reachability,
        include_generated=True,
        include_gated_before=True,
    )
    warnings: list[str] = []
    warnings.extend(
        _collect_one_sided_join_warnings(
            reachability,
            forward_intents,
            backward_intents,
            coverage_forward_intents,
            coverage_backward_intents,
        )
    )
    warnings.extend(_collect_bitmap_gap_warnings(reachability, forward_intents, backward_intents))
    warnings.extend(_collect_noentry_shape_leak_warnings(reachability))
    return tuple(sorted(dict.fromkeys(warnings)))


def warn_join_contract_issues(join_glyphs: Mapping[str, JoinGlyph]) -> None:
    for warning in collect_join_warnings(join_glyphs):
        warnings.warn(str(warning), JoinContractWarning, stacklevel=2)


def _collect_pair_intents(
    reachability: JoinReachability,
    *,
    include_generated: bool = False,
    include_gated_before: bool = False,
) -> tuple[list[_PairIntent], list[_PairIntent]]:
    forward_intents: list[_PairIntent] = []
    backward_intents: list[_PairIntent] = []
    for name, meta in reachability.glyph_meta.items():
        if meta.is_noentry:
            continue
        if meta.generated_from is not None and not include_generated:
            continue
        source_family = meta.family or meta.base_name
        before_targets = [*meta.before]
        if include_gated_before:
            for _feature_tag, gated_targets in meta.gated_before:
                before_targets.extend(gated_targets)
        for anchor in meta.exit:
            for target in before_targets:
                forward_intents.append(
                    _PairIntent(
                        variant_name=name,
                        left_family=source_family,
                        right_family=_resolve_family(reachability, target),
                        y=anchor[1],
                    )
                )
        for anchor in (*meta.entry, *meta.entry_curs_only):
            for target in meta.after:
                backward_intents.append(
                    _PairIntent(
                        variant_name=name,
                        left_family=_resolve_family(reachability, target),
                        right_family=source_family,
                        y=anchor[1],
                    )
                )
    return forward_intents, backward_intents


def _pair_intent_key(intent: _PairIntent) -> tuple[str, str, int]:
    return (intent.left_family, intent.right_family, intent.y)


def _has_default_join_coverage(
    reachability: JoinReachability,
    family: str,
    y: int,
    opposite_family: str,
    *,
    direction: str,
) -> bool:
    """Return whether ``family`` has a default stance that joins ``opposite_family`` at ``y``.

    A default stance has an anchor at ``y`` on the ``direction`` side (``exit``, or ``entry`` / ``entry_curs_only``) and no ``before:`` / ``after:`` selector on that side, so it applies to every neighbor its ``not_before:`` / ``not_after:`` does not exclude. Generated and ``.noentry`` variants are not considered. For ``direction="entry"``, a stance whose ``noentry_after`` names ``opposite_family`` does not count, because it becomes its ``.noentry`` form after that family.
    """
    variant_names = reachability.base_to_variants.get(family, frozenset())
    for variant_name in variant_names:
        meta = reachability.glyph_meta.get(variant_name)
        if meta is None or meta.generated_from is not None or meta.is_noentry:
            continue
        if direction == "exit":
            anchors: tuple[Anchor, ...] = meta.exit
            selectors: tuple[str, ...] = meta.before
            negated: tuple[str, ...] = meta.not_before
        else:
            anchors = (*meta.entry, *meta.entry_curs_only)
            selectors = meta.after
            negated = meta.not_after
        if not any(anchor[1] == y for anchor in anchors):
            continue
        if selectors:
            continue
        if any(_resolve_family(reachability, n) == opposite_family for n in negated):
            continue
        if direction == "entry" and any(
            _resolve_family(reachability, n) == opposite_family for n in meta.noentry_after
        ):
            continue
        return True
    return False


def _right_family_displaces_via_noentry(
    reachability: JoinReachability,
    right_family: str,
    left_family: str,
    y: int,
) -> bool:
    """Return whether a ``right_family`` variant with an entry at ``y`` lists ``left_family`` in its ``noentry_after``.

    Such a variant becomes its ``.noentry`` form after ``left_family``, so a default exit on the left family does not make the join, and the backward one-sided warning must not be suppressed. This is the right-side counterpart of the ``noentry_after`` check in ``_has_default_join_coverage``, which inspects only the left family for backward intents.
    """
    for variant_name in reachability.base_to_variants.get(right_family, frozenset()):
        meta = reachability.glyph_meta.get(variant_name)
        if meta is None or meta.generated_from is not None or meta.is_noentry:
            continue
        if not meta.noentry_after:
            continue
        if not any(anchor[1] == y for anchor in (*meta.entry, *meta.entry_curs_only)):
            continue
        if any(_resolve_family(reachability, n) == left_family for n in meta.noentry_after):
            return True
    return False


def _collect_one_sided_join_warnings(
    reachability: JoinReachability,
    forward_intents: list[_PairIntent],
    backward_intents: list[_PairIntent],
    coverage_forward_intents: list[_PairIntent] | None = None,
    coverage_backward_intents: list[_PairIntent] | None = None,
) -> list[str]:
    coverage_forward_intents = coverage_forward_intents or forward_intents
    coverage_backward_intents = coverage_backward_intents or backward_intents
    forward_keys = {_pair_intent_key(intent) for intent in coverage_forward_intents}
    backward_keys = {_pair_intent_key(intent) for intent in coverage_backward_intents}
    warnings: list[str] = []

    for intent in sorted(forward_intents, key=lambda i: (i.variant_name, i.right_family, i.y)):
        if _pair_intent_key(intent) in backward_keys:
            continue
        if _has_default_join_coverage(
            reachability,
            intent.right_family,
            intent.y,
            intent.left_family,
            direction="entry",
        ):
            continue
        warnings.append(
            "join-selection-one-sided: "
            f"{intent.variant_name} exits y={intent.y} before {intent.right_family}, "
            f"but {intent.right_family} has no matching after-selector for "
            f"{intent.left_family} at y={intent.y}"
        )

    for intent in sorted(backward_intents, key=lambda i: (i.variant_name, i.left_family, i.y)):
        if _pair_intent_key(intent) in forward_keys:
            continue
        if not _right_family_displaces_via_noentry(
            reachability, intent.right_family, intent.left_family, intent.y
        ) and _has_default_join_coverage(
            reachability,
            intent.left_family,
            intent.y,
            intent.right_family,
            direction="exit",
        ):
            continue
        warnings.append(
            "join-selection-one-sided: "
            f"{intent.variant_name} enters y={intent.y} after {intent.left_family}, "
            f"but {intent.left_family} has no matching before-selector for "
            f"{intent.right_family} at y={intent.y}"
        )

    return warnings


def _collect_noentry_shape_leak_warnings(
    reachability: JoinReachability,
) -> list[str]:
    """Return a warning for each left variant whose joining stub has nothing to attach to because the right letter's ``noentry_after`` removes its entry.

    Take a variant ``V_R`` with an entry at Y and ``noentry_after: [F, …]``. ``V_R`` becomes its ``.noentry`` form after ``F``. A variant ``V_L`` of ``F`` is reported when all of these hold:

    - it exits at Y;
    - it can be selected before ``V_R``'s base: its ``before`` is empty or names that base, and its ``not_before`` does not;
    - it is neither generated nor ``.noentry``;
    - it has no entry-preserving exit-noentry sibling.

    Warnings are deduplicated on ``(V_L, right base, Y)``, so several ``V_R`` variants of one family give one warning.
    """
    pairs: dict[tuple[str, str, int], str] = {}

    for r_name, r_meta in reachability.glyph_meta.items():
        if r_meta.generated_from is not None or r_meta.is_noentry:
            continue
        if not r_meta.noentry_after:
            continue
        r_family = r_meta.base_name
        r_entry_ys = {anchor[1] for anchor in (*r_meta.entry, *r_meta.entry_curs_only)}
        if not r_entry_ys:
            continue
        for f_name in r_meta.noentry_after:
            f_family = _resolve_family(reachability, f_name)
            for l_name in reachability.base_to_variants.get(f_family, frozenset()):
                l_meta = reachability.glyph_meta.get(l_name)
                if l_meta is None:
                    continue
                if l_meta.generated_from is not None or l_meta.is_noentry:
                    continue
                if l_meta.before and not any(
                    _resolve_family(reachability, n) == r_family for n in l_meta.before
                ):
                    continue
                if any(_resolve_family(reachability, n) == r_family for n in l_meta.not_before):
                    continue
                if has_entry_preserving_exit_noentry_sibling(
                    l_meta,
                    {base: set(variants) for base, variants in reachability.base_to_variants.items()},
                    dict(reachability.glyph_meta),
                ):
                    continue
                for y in l_meta.exit_ys:
                    if y not in r_entry_ys:
                        continue
                    pairs.setdefault((l_name, r_family, y), f_family)

    warnings: list[str] = []
    for (l_name, r_family, y), f_family in sorted(pairs.items()):
        warnings.append(
            "join-noentry-shape-leak: "
            f"{l_name} exits y={y} before {r_family}, but {r_family}'s "
            f"noentry_after lists {f_family} — {r_family}.noentry will fire "
            f"and {l_name}'s joining shape will be visually unsupported"
        )
    return warnings


def _collect_bitmap_gap_warnings(
    reachability: JoinReachability,
    forward_intents: list[_PairIntent],
    backward_intents: list[_PairIntent],
) -> list[str]:
    warnings: list[str] = []
    forward_by_key = _intents_by_pair(forward_intents)
    backward_by_key = _intents_by_pair(backward_intents)
    default_keys = _default_default_pair_keys(reachability)
    keys = sorted(set(forward_by_key) | set(backward_by_key) | default_keys)
    seen: set[tuple[str, str, int]] = set()

    for key in keys:
        left_family, right_family, y = key
        source_left_names = {
            intent.variant_name for intent in forward_by_key.get(key, ())
        } or _candidate_names_with_exit(
            reachability,
            left_family,
            y,
            opposite_family=right_family,
        )
        source_right_names = {
            intent.variant_name for intent in backward_by_key.get(key, ())
        } or _candidate_names_with_entry(
            reachability,
            right_family,
            y,
            opposite_family=left_family,
        )

        for source_left_name in sorted(source_left_names):
            for source_right_name in sorted(source_right_names):
                left_names = _replace_with_pair_specific_generated_variants(
                    reachability,
                    {source_left_name},
                    opposite_family=right_family,
                    opposite_names={source_right_name},
                    y=y,
                    side="exit",
                )
                for left_name in sorted(left_names):
                    left_meta = reachability.glyph_meta.get(left_name)
                    if left_meta is None:
                        continue
                    left_anchor = _first_anchor_at(left_meta.exit, y)
                    if left_anchor is None:
                        continue
                    right_names = _replace_with_pair_specific_generated_variants(
                        reachability,
                        {source_right_name},
                        opposite_family=left_family,
                        opposite_names={left_name},
                        y=y,
                        side="entry",
                    )
                    for right_name in sorted(right_names):
                        right_meta = reachability.glyph_meta.get(right_name)
                        if right_meta is None:
                            continue
                        right_anchor = _first_anchor_at((*right_meta.entry, *right_meta.entry_curs_only), y)
                        if right_anchor is None:
                            continue
                        pair_key = (left_name, right_name, y)
                        if pair_key in seen:
                            continue
                        seen.add(pair_key)
                        right_bounds_meta = right_meta
                        if (
                            right_meta.transform_kind == "entry-trimmed"
                            and right_meta.generated_from is not None
                            and _ink_bounds_at_y(right_meta, y) is None
                        ):
                            # The trim removed all ink in the join row, so measure against the untrimmed parent's bitmap, where the predecessor's exit is meant to overlap. Otherwise the empty row is reported as a join with no ink on one side.
                            parent = reachability.glyph_meta.get(right_meta.generated_from)
                            if parent is not None:
                                right_bounds_meta = parent
                        gap = _bitmap_join_gap(
                            left_meta,
                            left_anchor,
                            right_bounds_meta,
                            right_anchor,
                            left_family=left_family,
                            right_family=right_family,
                        )
                        if (left_name, right_name, y) in _RESIDUAL_BITMAP_GAPS:
                            continue
                        if gap is None:
                            warnings.append(
                                "join-bitmap-gap: "
                                f"{left_name} -> {right_name} at y={y} has no ink on "
                                "one side of the join row"
                            )
                        elif gap > 0:
                            warnings.append(
                                "join-bitmap-gap: "
                                f"{left_name} -> {right_name} at y={y} leaves "
                                f"{gap}px blank between strokes"
                            )

    return warnings


def _default_default_pair_keys(
    reachability: JoinReachability,
) -> set[tuple[str, str, int]]:
    """Return every ``(left family, right family, y)`` where some variant of the left family exits at ``y`` and some variant of the right family enters at ``y``, ignoring ``.noentry`` variants. The intent-keyed pass covers only pairs where one side has a pair selector, so these keys add the joins between default stances to the bitmap-gap check."""
    exit_ys: dict[str, set[int]] = {}
    entry_ys: dict[str, set[int]] = {}
    for family, variants in reachability.base_to_variants.items():
        for name in variants:
            meta = reachability.glyph_meta.get(name)
            if meta is None or meta.is_noentry:
                continue
            for anchor in meta.exit:
                exit_ys.setdefault(family, set()).add(anchor[1])
            for anchor in (*meta.entry, *meta.entry_curs_only):
                entry_ys.setdefault(family, set()).add(anchor[1])
    keys: set[tuple[str, str, int]] = set()
    for left_family, left_ys in exit_ys.items():
        for right_family, right_ys in entry_ys.items():
            for y in left_ys & right_ys:
                keys.add((left_family, right_family, y))
    return keys


def _intents_by_pair(
    intents: list[_PairIntent],
) -> dict[tuple[str, str, int], list[_PairIntent]]:
    by_pair: dict[tuple[str, str, int], list[_PairIntent]] = {}
    for intent in intents:
        by_pair.setdefault(_pair_intent_key(intent), []).append(intent)
    return by_pair


def _candidate_names_with_exit(
    reachability: JoinReachability,
    family: str,
    y: int,
    *,
    opposite_family: str,
) -> set[str]:
    return {
        name
        for name in reachability.base_to_variants.get(family, frozenset())
        if _is_source_authored_join_candidate(reachability.glyph_meta[name])
        and y in reachability.glyph_meta[name].exit_ys
        and _exit_candidate_permits_family(
            reachability,
            reachability.glyph_meta[name],
            opposite_family,
        )
        and not _ligature_base_mutates_at_exit(
            reachability,
            reachability.glyph_meta[name],
            opposite_family,
        )
    }


def _candidate_names_with_entry(
    reachability: JoinReachability,
    family: str,
    y: int,
    *,
    opposite_family: str,
) -> set[str]:
    return {
        name
        for name in reachability.base_to_variants.get(family, frozenset())
        if _is_source_authored_join_candidate(reachability.glyph_meta[name])
        and y in reachability.glyph_meta[name].all_entry_ys
        and _entry_candidate_permits_family(
            reachability,
            reachability.glyph_meta[name],
            opposite_family,
        )
        and not _ligature_base_mutates_at_entry(
            reachability,
            reachability.glyph_meta[name],
            opposite_family,
        )
    }


def _ligature_base_mutates_at_exit(
    reachability: JoinReachability,
    meta: JoinGlyph,
    opposite_family: str,
) -> bool:
    """Return whether a ligature with no exit extension or contraction suffix cannot appear before ``opposite_family`` because its trailing component has an ``extend_exit_before`` or ``contract_exit_before`` rule targeting that family. The rule changes the trailing component before ligation, and ``calt_liga`` then forms the matching ligature variant instead, so the bitmap-gap check skips this pair."""
    if not meta.sequence:
        return False
    if meta.extended_exit_suffix or meta.contracted_exit_suffix:
        return False
    last_meta = reachability.glyph_meta.get(meta.sequence[-1])
    if last_meta is None:
        return False
    if last_meta.contract_exit_before and opposite_family in last_meta.contract_exit_before.targets:
        return True
    if any(opposite_family in rule.targets for rule in last_meta.extend_exit_before):
        return True
    return False


def _ligature_base_mutates_at_entry(
    reachability: JoinReachability,
    meta: JoinGlyph,
    opposite_family: str,
) -> bool:
    if not meta.sequence:
        return False
    if meta.extended_entry_suffix:
        return False
    first_meta = reachability.glyph_meta.get(meta.sequence[0])
    if first_meta is None:
        return False
    if first_meta.contract_entry_after and opposite_family in first_meta.contract_entry_after.targets:
        return True
    if any(opposite_family in rule.targets for rule in first_meta.extend_entry_after):
        return True
    return False


def _is_source_authored_join_candidate(meta: JoinGlyph) -> bool:
    return meta.generated_from is None and not meta.is_noentry


def _exit_candidate_permits_family(
    reachability: JoinReachability,
    meta: JoinGlyph,
    family: str,
) -> bool:
    if meta.before and not _before_context_matches_family(reachability, meta, family):
        return False
    return not any(_resolve_family(reachability, selector) == family for selector in meta.not_before)


def _entry_candidate_permits_family(
    reachability: JoinReachability,
    meta: JoinGlyph,
    family: str,
) -> bool:
    if meta.after and not _after_context_matches_family(reachability, meta, family):
        return False
    if any(_resolve_family(reachability, selector) == family for selector in meta.not_after):
        return False
    return not any(_resolve_family(reachability, selector) == family for selector in meta.noentry_after)


def _replace_with_pair_specific_generated_variants(
    reachability: JoinReachability,
    names: set[str],
    *,
    opposite_family: str,
    opposite_names: set[str],
    y: int,
    side: str,
) -> set[str]:
    replaced: set[str] = set()
    for name in names:
        generated = _pair_specific_generated_variants(
            reachability,
            name,
            opposite_family=opposite_family,
            opposite_names=opposite_names,
            y=y,
            side=side,
        )
        if generated:
            replaced.update(generated)
        else:
            replaced.add(name)
    return replaced


def _pair_specific_generated_variants(
    reachability: JoinReachability,
    source_name: str,
    *,
    opposite_family: str,
    opposite_names: set[str],
    y: int,
    side: str,
) -> set[str]:
    source_meta = reachability.glyph_meta.get(source_name)
    generated: set[str] = set()
    for name, meta in reachability.glyph_meta.items():
        if meta.generated_from != source_name or meta.is_noentry:
            continue
        if side == "exit":
            if y not in meta.exit_ys:
                continue
            if not _before_context_matches_opposite(
                reachability, meta, opposite_family, opposite_names
            ) and not _ligature_component_propagates_context(
                reachability, source_meta, meta, opposite_family, side="exit"
            ):
                continue
        else:
            if y not in meta.all_entry_ys:
                continue
            if not _after_context_matches_opposite(
                reachability, meta, opposite_family, opposite_names
            ) and not _ligature_component_propagates_context(
                reachability, source_meta, meta, opposite_family, side="entry"
            ):
                continue
        generated.add(name)
    side_specific = {
        name for name in generated if _has_generated_transform_on_side(reachability.glyph_meta[name], side)
    }
    if side_specific:
        return side_specific
    return generated


def _has_generated_transform_on_side(meta: JoinGlyph, side: str) -> bool:
    if side == "exit":
        return meta.extended_exit_suffix is not None or meta.contracted_exit_suffix is not None
    return meta.extended_entry_suffix is not None or meta.contracted_entry_suffix is not None


def _before_context_matches_family(
    reachability: JoinReachability,
    meta: JoinGlyph,
    family: str,
) -> bool:
    if any(_resolve_family(reachability, selector) == family for selector in meta.before):
        return True
    return any(
        _resolve_family(reachability, selector) == family
        for _feature_tag, selectors in meta.gated_before
        for selector in selectors
    )


def _after_context_matches_family(
    reachability: JoinReachability,
    meta: JoinGlyph,
    family: str,
) -> bool:
    return any(_resolve_family(reachability, selector) == family for selector in meta.after)


def _before_context_matches_opposite(
    reachability: JoinReachability,
    meta: JoinGlyph,
    family: str,
    names: set[str],
) -> bool:
    if any(
        _selector_matches_family_or_name(reachability, selector, family, names) for selector in meta.before
    ):
        return True
    return any(
        _selector_matches_family_or_name(reachability, selector, family, names)
        for _feature_tag, selectors in meta.gated_before
        for selector in selectors
    )


def _after_context_matches_opposite(
    reachability: JoinReachability,
    meta: JoinGlyph,
    family: str,
    names: set[str],
) -> bool:
    return any(
        _selector_matches_family_or_name(reachability, selector, family, names) for selector in meta.after
    )


def _ligature_component_propagates_context(
    reachability: JoinReachability,
    source_meta: JoinGlyph | None,
    candidate_meta: JoinGlyph,
    opposite_family: str,
    side: str,
) -> bool:
    # `_add_entry_contraction_variants`, `_add_entry_extension_variants`, and the exit-side helpers in `quikscript_ir` copy a component's contract or extend rule onto the matching ligature variant but leave that variant's own `after` / `before` empty, because the copied context is intersected with the base ligature's empty context. `calt_liga` still forms that variant whenever the component's rule fires, so accept it when the component family has a matching rule whose targets include `opposite_family`. The lead component decides the entry side and the trailing component the exit side.
    # As in `expand_selectors_for_ligatures`, a target `qsZ` also matches when `opposite_family` is a ligature whose component next to the candidate is `qsZ` (its trailing component on the entry side, its lead component on the exit side), because context lookups run before ligation.
    if source_meta is None or not source_meta.sequence:
        return False
    if side == "entry":
        component_index = 0
        opposite_match_index = -1
        if candidate_meta.contracted_entry_suffix is not None:
            spec_attr = "contract_entry_after"
        elif candidate_meta.extended_entry_suffix is not None:
            spec_attr = "extend_entry_after"
        else:
            return False
    else:
        component_index = -1
        opposite_match_index = 0
        if candidate_meta.contracted_exit_suffix is not None:
            spec_attr = "contract_exit_before"
        elif candidate_meta.extended_exit_suffix is not None:
            spec_attr = "extend_exit_before"
        else:
            return False

    opposite_targets = {opposite_family}
    opposite_meta = reachability.glyph_meta.get(opposite_family)
    if opposite_meta is not None and opposite_meta.sequence:
        opposite_targets.add(opposite_meta.sequence[opposite_match_index])

    component_family = source_meta.sequence[component_index]
    for variant_name in reachability.base_to_variants.get(component_family, frozenset()):
        variant_meta = reachability.glyph_meta.get(variant_name)
        if variant_meta is None:
            continue
        spec = getattr(variant_meta, spec_attr)
        if spec is None:
            continue
        if spec_attr in ("extend_entry_after", "extend_exit_before"):
            rules = spec
        else:
            rules = (spec,)
        for rule in rules:
            if any(target in rule.targets for target in opposite_targets):
                return True
    return False


def _selector_matches_family_or_name(
    reachability: JoinReachability,
    selector: str,
    family: str,
    names: set[str],
) -> bool:
    meta = reachability.glyph_meta.get(selector)
    if meta is not None and selector != meta.base_name:
        return any(_name_matches_selector(reachability, name, selector) for name in names)
    return _resolve_family(reachability, selector) == family


def _name_matches_selector(
    reachability: JoinReachability,
    name: str,
    selector: str,
) -> bool:
    current = name
    seen: set[str] = set()
    while current not in seen:
        if current == selector:
            return True
        seen.add(current)
        meta = reachability.glyph_meta.get(current)
        if meta is None or meta.generated_from is None:
            return False
        current = meta.generated_from
    return False


def _first_anchor_at(anchors: tuple[tuple[int, int], ...], y: int) -> tuple[int, int] | None:
    return next((anchor for anchor in anchors if anchor[1] == y), None)


def _effective_exit_x(meta: JoinGlyph, anchor_x: int, right_family: str | None) -> int:
    # `extend_exit_before` widens the bitmap to the right by as much as it moves the exit anchor, so it does not change the visible gap and is not modeled.
    if right_family is None:
        return anchor_x
    if meta.contract_exit_before and right_family in meta.contract_exit_before.targets:
        anchor_x -= meta.contract_exit_before.by
    return anchor_x


def _effective_entry_x(meta: JoinGlyph, anchor_x: int, left_family: str | None) -> int:
    # `extend_entry_after` prepends ink to the receiver's bitmap, capped by the original gap — modeling that without the bitmap rewrite would over-correct, so skip it.
    if left_family is None:
        return anchor_x
    if meta.contract_entry_after and left_family in meta.contract_entry_after.targets:
        anchor_x += meta.contract_entry_after.by
    return anchor_x


def _bitmap_join_gap(
    left_meta: JoinGlyph,
    left_anchor: tuple[int, int],
    right_meta: JoinGlyph,
    right_anchor: tuple[int, int],
    *,
    left_family: str | None = None,
    right_family: str | None = None,
) -> int | None:
    if left_meta.exit_ink_y is not None:
        left_bounds = _ink_bounds_at_y(left_meta, left_meta.exit_ink_y)
    else:
        left_bounds = _ink_bounds_at_y(left_meta, left_anchor[1])
    right_bounds = _ink_bounds_at_y(right_meta, right_anchor[1])
    if left_bounds is None or right_bounds is None:
        return None
    _, left_max = left_bounds
    right_min, _ = right_bounds
    eff_left_x = _effective_exit_x(left_meta, left_anchor[0], right_family)
    eff_right_x = _effective_entry_x(right_meta, right_anchor[0], left_family)
    left_ink_to_exit = left_max - eff_left_x
    right_ink_to_entry = right_min - eff_right_x
    return right_ink_to_entry - left_ink_to_exit - 1


def _ink_bounds_at_y(meta: JoinGlyph, y: int) -> tuple[int, int] | None:
    row = _bitmap_row_at_y(meta, y)
    if row is None:
        return None
    ink_xs = [index for index, has_ink in enumerate(row) if has_ink]
    if not ink_xs:
        return None
    return min(ink_xs), max(ink_xs)


def _bitmap_row_at_y(meta: JoinGlyph, y: int) -> tuple[bool, ...] | None:
    if not meta.bitmap:
        return None
    top_y = meta.y_offset + len(meta.bitmap) - 1
    row_index = top_y - y
    if row_index < 0 or row_index >= len(meta.bitmap):
        return None
    row = meta.bitmap[row_index]
    if isinstance(row, str):
        return tuple(char == "#" for char in row)
    return tuple(bool(value) for value in row)


def _ss_tags(reachability: JoinReachability) -> list[str]:
    tags: set[str] = set()
    for entries in reachability.gated_pair_overrides.values():
        tags.update(tag for _, _, tag in entries)
    for entries in reachability.gated_fwd_pair_overrides.values():
        tags.update(tag for _, _, _, tag in entries)
    return sorted(tags)


def _warn_orphans(reachability: JoinReachability) -> None:
    entry_owners: dict[int, list[str]] = {y: sorted(names) for y, names in reachability.entry_classes.items()}
    exit_owners: dict[int, list[str]] = {}
    for name, meta in reachability.glyph_meta.items():
        for anchor in meta.exit:
            exit_owners.setdefault(anchor[1], []).append(name)
        for anchor in meta.entry_curs_only:
            entry_owners.setdefault(anchor[1], []).append(name)
    for y in sorted(set(entry_owners) - set(exit_owners)):
        for name in entry_owners[y]:
            warnings.warn(
                f"orphan entry_y={y} on {name} (no exit_y={y} anywhere)",
                OrphanAnchorWarning,
                stacklevel=2,
            )
    for y in sorted(set(exit_owners) - set(entry_owners)):
        for name in sorted(exit_owners[y]):
            warnings.warn(
                f"orphan exit_y={y} on {name} (no entry_y={y} anywhere)",
                OrphanAnchorWarning,
                stacklevel=2,
            )


def _check_concrete_selector_consistency(
    glyph_meta: dict[str, JoinGlyph],
    errors: list[str],
) -> None:
    plan = _analyze_quikscript_joins(glyph_meta)
    seen_errors: set[str] = set()

    def add_error(message: str) -> None:
        if message in seen_errors:
            return
        seen_errors.add(message)
        errors.append(message)

    for _base_name, overrides in plan.pair_overrides.items():
        for variant_name, after_glyphs in overrides:
            _check_concrete_after_selectors(
                plan,
                variant_name,
                after_glyphs,
                feature_tag=None,
                add_error=add_error,
            )

    for _base_name, overrides in plan.gated_pair_overrides.items():
        for variant_name, after_glyphs, feature_tag in overrides:
            _check_concrete_after_selectors(
                plan,
                variant_name,
                after_glyphs,
                feature_tag=feature_tag,
                add_error=add_error,
            )

    for _base_name, overrides in plan.fwd_pair_overrides.items():
        for variant_name, before_glyphs, _not_after_glyphs in overrides:
            _check_concrete_before_selectors(
                plan,
                variant_name,
                before_glyphs,
                feature_tag=None,
                add_error=add_error,
            )

    for _base_name, overrides in plan.gated_fwd_pair_overrides.items():
        for variant_name, before_glyphs, _not_after_glyphs, feature_tag in overrides:
            _check_concrete_before_selectors(
                plan,
                variant_name,
                before_glyphs,
                feature_tag=feature_tag,
                add_error=add_error,
            )


def _check_concrete_after_selectors(
    plan,
    variant_name: str,
    after_glyphs: list[str] | tuple[str, ...],
    *,
    feature_tag: str | None,
    add_error,
) -> None:
    variant_meta = plan.glyph_meta[variant_name]
    required_entry_ys = set(variant_meta.all_entry_ys)
    if not required_entry_ys:
        return

    for selector in after_glyphs:
        expanded = _expand_concrete_after_selector(
            plan,
            variant_name,
            selector,
            feature_tag=feature_tag,
        )
        expanded -= plan.terminal_entry_only
        for candidate in sorted(expanded):
            if candidate not in plan.glyph_meta:
                continue
            for entry_y in sorted(required_entry_ys):
                if _can_eventually_exit_at(
                    plan,
                    candidate,
                    entry_y,
                    before_base=variant_meta.base_name,
                    feature_tag=feature_tag,
                ):
                    continue
                add_error(
                    _format_concrete_after_error(
                        variant_name,
                        entry_y,
                        selector,
                        candidate,
                        feature_tag,
                    )
                )


def _check_concrete_before_selectors(
    plan,
    variant_name: str,
    before_glyphs: list[str] | tuple[str, ...],
    *,
    feature_tag: str | None,
    add_error,
) -> None:
    variant_meta = plan.glyph_meta[variant_name]
    required_exit_ys = set(variant_meta.exit_ys)
    if not required_exit_ys:
        return

    for selector in before_glyphs:
        expanded = _expand_forward_before_variants(
            variant_name,
            (selector,),
            analysis=plan,
            feature_tag=feature_tag,
        )
        expanded -= plan.terminal_exit_only
        for candidate in sorted(expanded):
            if candidate not in plan.glyph_meta:
                continue
            if not _candidate_reachable_after_base(
                plan,
                candidate,
                after_base=variant_meta.base_name,
                feature_tag=feature_tag,
            ):
                continue
            for exit_y in sorted(required_exit_ys):
                candidate_entry_ys = set(plan.glyph_meta[candidate].all_entry_ys)
                if feature_tag is not None and candidate_entry_ys and exit_y not in candidate_entry_ys:
                    continue
                if _can_eventually_enter_at(
                    plan,
                    candidate,
                    exit_y,
                    after_base=variant_meta.base_name,
                    feature_tag=feature_tag,
                ):
                    continue
                if _candidate_has_runtime_entry_context(plan, candidate):
                    continue
                add_error(
                    _format_concrete_before_error(
                        variant_name,
                        exit_y,
                        selector,
                        candidate,
                        feature_tag,
                    )
                )


def _expand_concrete_after_selector(
    plan,
    variant_name: str,
    selector: str,
    *,
    feature_tag: str | None,
) -> set[str]:
    if feature_tag is None:
        return _expand_backward_after_variants(
            variant_name,
            (selector,),
            expand_selector=lambda glyph: _expand_join_variants([glyph], plan),
            analysis=plan,
        )

    def expand_gated(glyph: str) -> set[str]:
        base = plan.glyph_meta[glyph].base_name if glyph in plan.glyph_meta else glyph
        return set(plan.base_to_variants.get(base, ()))

    return _expand_backward_after_variants(
        variant_name,
        (selector,),
        expand_selector=expand_gated,
        analysis=plan,
        feature_tag=feature_tag,
    )


def _can_eventually_enter_at(
    plan,
    name: str,
    y: int,
    *,
    after_base: str | None,
    feature_tag: str | None,
) -> bool:
    meta = plan.glyph_meta[name]
    if y in meta.all_entry_ys:
        return True
    if name in plan.entry_classes.get(y, set()):
        return True
    for ancestor in _generation_ancestors(plan, name):
        ancestor_meta = plan.glyph_meta[ancestor]
        if y in ancestor_meta.all_entry_ys:
            return True
        if ancestor in plan.entry_classes.get(y, set()):
            return True

    base_name = meta.base_name
    if name == base_name and y in plan.bk_replacements.get(base_name, {}):
        return True

    if after_base is not None and name == base_name:
        for variant_name, selectors in plan.pair_overrides.get(base_name, ()):
            variant_meta = plan.glyph_meta[variant_name]
            if y in variant_meta.all_entry_ys and after_base in _selector_bases(
                plan,
                selectors,
            ):
                return True
        if feature_tag is not None:
            for variant_name, selectors, tag in plan.gated_pair_overrides.get(
                base_name,
                (),
            ):
                if tag != feature_tag:
                    continue
                variant_meta = plan.glyph_meta[variant_name]
                if y in variant_meta.all_entry_ys and after_base in _selector_bases(
                    plan,
                    selectors,
                ):
                    return True

    if y not in plan.bk_replacements.get(base_name, {}):
        return False

    for fwd_variant, _before_glyphs, _not_after_glyphs in plan.fwd_pair_overrides.get(
        base_name,
        (),
    ):
        if fwd_variant != name:
            continue
        fwd_meta = plan.glyph_meta[fwd_variant]
        if fwd_meta.entry and y not in fwd_meta.entry_ys:
            return True

    if feature_tag is not None:
        for (
            fwd_variant,
            _before_glyphs,
            _not_after_glyphs,
            tag,
        ) in plan.gated_fwd_pair_overrides.get(base_name, ()):
            if tag != feature_tag or fwd_variant != name:
                continue
            fwd_meta = plan.glyph_meta[fwd_variant]
            if fwd_meta.entry and y not in fwd_meta.entry_ys:
                return True

    return False


def _candidate_has_runtime_entry_context(plan, name: str) -> bool:
    meta = plan.glyph_meta[name]
    if meta.generated_from is not None or meta.noentry_for is not None:
        return True
    if meta.is_noentry:
        return True
    if meta.after or meta.before or meta.gated_before or meta.gate_feature:
        return True
    if meta.traits & {"alt", "half"}:
        return True
    for replacements in plan.fwd_replacements.values():
        if name in replacements.values():
            return True
    for overrides in plan.fwd_pair_overrides.values():
        if any(variant_name == name for variant_name, _before, _not_after in overrides):
            return True
    for overrides in plan.gated_fwd_pair_overrides.values():
        if any(variant_name == name for variant_name, _before, _not_after, _tag in overrides):
            return True
    return False


def _candidate_reachable_after_base(
    plan,
    name: str,
    *,
    after_base: str | None,
    feature_tag: str | None,
) -> bool:
    if after_base is None:
        return True

    current = name
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        meta = plan.glyph_meta.get(current)
        if meta is None:
            return True

        if meta.after and after_base not in _selector_bases(plan, meta.after):
            return False

        for variant_name, selectors in plan.pair_overrides.get(meta.base_name, ()):
            if variant_name != current:
                continue
            if after_base not in _selector_bases(plan, selectors):
                return False

        for variant_name, selectors, tag in plan.gated_pair_overrides.get(
            meta.base_name,
            (),
        ):
            if variant_name != current:
                continue
            if tag != feature_tag or after_base not in _selector_bases(plan, selectors):
                return False

        if meta.generated_from is not None:
            current = meta.generated_from
            continue
        if meta.noentry_for is not None:
            current = meta.noentry_for
            continue
        return True

    return True


def _generation_ancestors(plan, name: str) -> tuple[str, ...]:
    ancestors: list[str] = []
    current = name
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        meta = plan.glyph_meta.get(current)
        if meta is None:
            break
        parent = meta.generated_from or meta.noentry_for
        if parent is None or parent not in plan.glyph_meta:
            break
        ancestors.append(parent)
        current = parent
    return tuple(ancestors)


def _selector_bases(plan, selectors: list[str] | tuple[str, ...]) -> set[str]:
    bases: set[str] = set()
    for selector in selectors:
        meta = plan.glyph_meta.get(selector)
        bases.add(meta.base_name if meta is not None else selector)
    return bases


def _format_concrete_after_error(
    variant_name: str,
    entry_y: int,
    selector: str,
    candidate: str,
    feature_tag: str | None,
) -> str:
    gate_note = f" under {feature_tag}" if feature_tag else ""
    return (
        f"concrete-selector-mismatch: {variant_name} enters y={entry_y} "
        f"after {selector}{gate_note}, but selector {selector} expands to "
        f"{candidate} with no y={entry_y} exit"
    )


def _format_concrete_before_error(
    variant_name: str,
    exit_y: int,
    selector: str,
    candidate: str,
    feature_tag: str | None,
) -> str:
    gate_note = f" under {feature_tag}" if feature_tag else ""
    return (
        f"concrete-selector-mismatch: {variant_name} exits y={exit_y} "
        f"before {selector}{gate_note}, but selector {selector} expands to "
        f"{candidate} with no y={exit_y} entry"
    )


def _check_join_consistency(
    reachability: JoinReachability,
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    *,
    gated_feature: str | None,
    errors: list[str],
) -> None:
    for source_name, source_meta in reachability.glyph_meta.items():
        if source_meta.is_noentry:
            continue
        _check_one_source(
            reachability,
            glyph_meta,
            base_to_variants,
            source_name,
            source_meta,
            anchor_meta=source_meta,
            gated_feature=gated_feature,
            end_of_word=False,
            errors=errors,
        )
        wf_name = reachability.word_final_pairs.get(source_meta.base_name)
        if wf_name and wf_name != source_name:
            wf_meta = reachability.glyph_meta[wf_name]
            _check_one_source(
                reachability,
                glyph_meta,
                base_to_variants,
                source_name,
                source_meta,
                anchor_meta=wf_meta,
                gated_feature=gated_feature,
                end_of_word=True,
                errors=errors,
            )


def _check_one_source(
    reachability: JoinReachability,
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    source_name: str,
    source_meta: JoinGlyph,
    *,
    anchor_meta: JoinGlyph,
    gated_feature: str | None,
    end_of_word: bool,
    errors: list[str],
) -> None:
    source_family = source_meta.family or source_meta.base_name

    if source_meta.before and anchor_meta.exit:
        exit_y = anchor_meta.exit[0][1]
        for t_name in source_meta.before:
            t_family = _resolve_family(reachability, t_name)
            if not _family_has_candidates(reachability, t_family):
                continue
            if gated_feature is not None and not _gated_right_match(
                reachability, t_family, source_family, gated_feature
            ):
                continue
            candidates = _reachable_right_variants(
                reachability,
                glyph_meta,
                base_to_variants,
                t_family,
                gated_feature,
                source_family,
            )
            entry_ys = {
                anchor[1] for _, meta in candidates for anchor in (*meta.entry, *meta.entry_curs_only)
            }
            if exit_y not in entry_ys:
                errors.append(
                    _format_forward_error(
                        source_name,
                        exit_y,
                        t_family,
                        candidates,
                        entry_ys,
                        gated_feature,
                        end_of_word,
                    )
                )

    if source_meta.after and (anchor_meta.entry or anchor_meta.entry_curs_only):
        entry_anchors = anchor_meta.entry or anchor_meta.entry_curs_only
        entry_y = entry_anchors[0][1]
        for t_name in source_meta.after:
            t_family = _resolve_family(reachability, t_name)
            if not _family_has_candidates(reachability, t_family):
                continue
            if gated_feature is not None and not _gated_left_match(
                reachability, t_family, source_family, gated_feature
            ):
                continue
            candidates = _reachable_left_variants(
                reachability,
                glyph_meta,
                base_to_variants,
                t_family,
                gated_feature,
                source_family,
            )
            exit_ys = {anchor[1] for _, meta in candidates for anchor in meta.exit}
            if entry_y not in exit_ys:
                errors.append(
                    _format_backward_error(
                        source_name,
                        entry_y,
                        t_family,
                        candidates,
                        exit_ys,
                        gated_feature,
                        end_of_word,
                    )
                )


def _reachable_right_variants(
    reachability: JoinReachability,
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    t_family: str,
    gated_feature: str | None,
    source_family: str,
) -> list[tuple[str, JoinGlyph]]:
    if gated_feature is not None:
        gated_match = {
            variant
            for variant, after_glyphs, tag in reachability.gated_pair_overrides.get(t_family, ())
            if tag == gated_feature and source_family in after_glyphs
        }
        if gated_match:
            candidate_names = gated_match
        else:
            candidate_names = _candidate_names_for_family(reachability, t_family, side="right")
    else:
        candidate_names = _candidate_names_for_family(reachability, t_family, side="right")

    resolved: list[tuple[str, JoinGlyph]] = []
    seen: set[str] = set()
    for name in candidate_names:
        resolved_name = _resolve_noentry_replacement(glyph_meta, base_to_variants, name, name)
        if resolved_name is None:
            continue
        if resolved_name in seen:
            continue
        seen.add(resolved_name)
        meta = reachability.glyph_meta[resolved_name]
        if source_family in meta.noentry_after:
            continue
        resolved.append((resolved_name, meta))
    return resolved


def _reachable_left_variants(
    reachability: JoinReachability,
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    t_family: str,
    gated_feature: str | None,
    source_family: str,
) -> list[tuple[str, JoinGlyph]]:
    if gated_feature is not None:
        gated_match = {
            variant
            for variant, before_glyphs, _, tag in reachability.gated_fwd_pair_overrides.get(t_family, ())
            if tag == gated_feature and source_family in before_glyphs
        }
        if gated_match:
            candidate_names = gated_match
        else:
            candidate_names = _candidate_names_for_family(reachability, t_family, side="left")
    else:
        candidate_names = _candidate_names_for_family(reachability, t_family, side="left")

    resolved: list[tuple[str, JoinGlyph]] = []
    seen: set[str] = set()
    for name in candidate_names:
        resolved_name = _resolve_noentry_replacement(glyph_meta, base_to_variants, name, name)
        if resolved_name is None:
            continue
        if resolved_name in seen:
            continue
        seen.add(resolved_name)
        meta = reachability.glyph_meta[resolved_name]
        resolved.append((resolved_name, meta))
    return resolved


def _candidate_names_for_family(
    reachability: JoinReachability,
    t_family: str,
    *,
    side: str,
) -> set[str]:
    candidates: set[str] = set()
    candidates.update(reachability.base_to_variants.get(t_family, frozenset()))

    for lig_name, components in reachability.ligatures:
        if not components:
            continue
        if side == "right":
            first = reachability.glyph_meta.get(components[0])
            if first and first.family == t_family:
                candidates.add(lig_name)
            if lig_name in _LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS and len(components) >= 2:
                second = reachability.glyph_meta.get(components[1])
                if second and second.family == t_family:
                    candidates.add(lig_name)
        else:
            last = reachability.glyph_meta.get(components[-1])
            if last and last.family == t_family:
                candidates.add(lig_name)
    return candidates


def _resolve_family(reachability: JoinReachability, t_name: str) -> str:
    """Resolve a selector reference to a family name.

    Selectors compile to glyph names (`{family: qsTea}` → `"qsTea"`, `{family: qsTea, traits: [alt]}` → `"qsTea.alt"`); the validator's family-bucket lookups key on `base_name`.
    """
    meta = reachability.glyph_meta.get(t_name)
    if meta is not None:
        return meta.base_name
    return t_name.split(".")[0]


def _gated_right_match(
    reachability: JoinReachability,
    t_family: str,
    source_family: str,
    gated_feature: str,
) -> bool:
    return any(
        tag == gated_feature and source_family in after_glyphs
        for _, after_glyphs, tag in reachability.gated_pair_overrides.get(t_family, ())
    )


def _gated_left_match(
    reachability: JoinReachability,
    t_family: str,
    source_family: str,
    gated_feature: str,
) -> bool:
    return any(
        tag == gated_feature and source_family in before_glyphs
        for _, before_glyphs, _, tag in reachability.gated_fwd_pair_overrides.get(t_family, ())
    )


def _family_has_candidates(reachability: JoinReachability, t_family: str) -> bool:
    if t_family in reachability.base_to_variants:
        return True
    for _, components in reachability.ligatures:
        if not components:
            continue
        first = reachability.glyph_meta.get(components[0])
        if first and first.family == t_family:
            return True
        last = reachability.glyph_meta.get(components[-1])
        if last and last.family == t_family:
            return True
    return False


def _format_forward_error(
    source_name: str,
    exit_y: int,
    t_family: str,
    candidates: list[tuple[str, JoinGlyph]],
    entry_ys: set[int],
    gated_feature: str | None,
    end_of_word: bool,
) -> str:
    example = "{" + ", ".join(sorted(name for name, _ in candidates)) + "}"
    ys_repr = "{" + ", ".join(str(y) for y in sorted(entry_ys)) + "}" if entry_ys else "∅"
    gate_note = f" under {gated_feature}" if gated_feature else ""
    wf_note = " (word-final)" if end_of_word else ""
    return (
        f"{source_name}{wf_note} expects to exit at y={exit_y} toward "
        f"{t_family}{gate_note}, but reachable variants of {t_family} "
        f"({example}) carry entry Ys {ys_repr} — no y={exit_y} entry"
    )


def _format_backward_error(
    source_name: str,
    entry_y: int,
    t_family: str,
    candidates: list[tuple[str, JoinGlyph]],
    exit_ys: set[int],
    gated_feature: str | None,
    end_of_word: bool,
) -> str:
    example = "{" + ", ".join(sorted(name for name, _ in candidates)) + "}"
    ys_repr = "{" + ", ".join(str(y) for y in sorted(exit_ys)) + "}" if exit_ys else "∅"
    gate_note = f" under {gated_feature}" if gated_feature else ""
    wf_note = " (word-final)" if end_of_word else ""
    return (
        f"{source_name}{wf_note} expects to receive entry at y={entry_y} from "
        f"{t_family}{gate_note}, but reachable variants of {t_family} "
        f"({example}) carry exit Ys {ys_repr} — no y={entry_y} exit"
    )


def _heal_curated_guards_table(
    table: dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]],
    reachability: JoinReachability,
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    """Return ``table`` with every glyph name passed through `heal_glyph_name`, so hand-written names resolve to the compiled names that `_synthesize_anchor_modifiers` produces."""
    from quikscript_ir import family_names_from_compiled, heal_glyph_name

    available = frozenset(reachability.glyph_meta)
    families = family_names_from_compiled(available)

    def _heal(name: str) -> str:
        return heal_glyph_name(name, families, available)

    healed: dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]] = {}
    for (source, replacement, entry_y), guards in table.items():
        healed_key = (_heal(source), _heal(replacement), entry_y)
        healed_guards = tuple(
            DerivedBkGuard(
                tuple(_heal(g) for g in guard.guard_glyphs),
                tuple(_heal(b) for b in guard.before_bases) if guard.before_bases else (),
            )
            for guard in guards
        )
        healed[healed_key] = healed_guards
    return healed


def derive_pending_bk_entry_guards(
    reachability: JoinReachability,
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    """Return `_PENDING_BK_ENTRY_GUARDS` with its glyph names healed to compiled names, such as `qsExcite.ex-y0.before-vertical` to `qsExcite.en-y0.ex-y0.before-vertical`."""
    return _heal_curated_guards_table(
        {key: tuple(guards) for key, guards in _PENDING_BK_ENTRY_GUARDS.items()},
        reachability,
    )


def derive_pending_fwd_strip_guards(
    reachability: _FwdStripReachability,
) -> dict[tuple[str, str, int], tuple[FwdStripGuard, ...]]:
    """Return forward-strip guards keyed by predecessor ``(source_base, variant, exit_y)``.

    Predecessors come from ``fwd_replacements`` and ``fwd_pair_overrides`` and must pass ``_predecessor_visually_reaches``. A predecessor's guards name each bare base ``B`` whose forward substitution at ``exit_y`` has no entry at ``exit_y`` while another variant of ``B`` has one (``_bases_with_stripped_fwd_per_y``). The FEA emitter uses them to suppress the predecessor's substitution when bare ``B`` follows, since the predecessor's exit would then join nothing. ``_emit_narrow_mid_entry_strip_guards`` decides at each call site whether to emit a guard.
    """
    return _compute_derived_fwd_strip_guards(reachability)


def _compute_derived_fwd_strip_guards(
    reachability: _FwdStripReachability,
) -> dict[tuple[str, str, int], tuple[FwdStripGuard, ...]]:
    glyph_meta = reachability.glyph_meta

    candidate_bases = _bases_with_stripped_fwd_per_y(reachability)
    if not candidate_bases:
        return {}

    buf: dict[tuple[str, str, int], list[FwdStripGuard]] = {}

    for source_base, fwd_at_y in reachability.fwd_replacements.items():
        for predecessor_exit_y, predecessor_variant in fwd_at_y.items():
            if not _predecessor_visually_reaches(glyph_meta, predecessor_variant):
                continue
            _add_fwd_strip_guards(
                buf,
                candidate_bases,
                source_base,
                predecessor_variant,
                predecessor_exit_y,
            )

    for source_base, overrides in reachability.fwd_pair_overrides.items():
        for predecessor_variant, _before, _not_after in overrides:
            predecessor_meta = glyph_meta.get(predecessor_variant)
            if predecessor_meta is None or not predecessor_meta.exit:
                continue
            if not _predecessor_visually_reaches(glyph_meta, predecessor_variant):
                continue
            for predecessor_exit_y in set(predecessor_meta.exit_ys):
                _add_fwd_strip_guards(
                    buf,
                    candidate_bases,
                    source_base,
                    predecessor_variant,
                    predecessor_exit_y,
                )

    out: dict[tuple[str, str, int], tuple[FwdStripGuard, ...]] = {}
    for key, guards in buf.items():
        deduped = sorted(set(guards), key=lambda g: g.mid_base)
        out[key] = tuple(deduped)
    return out


def _revert_keeps_reaching_exit(
    glyph_meta: Mapping[str, JoinGlyph],
    source_base: str,
    predecessor_variant: str,
    exit_y: int,
) -> bool:
    """Return whether keeping ``source_base`` in place of ``predecessor_variant`` would leave the same reaching exit stroke.

    ``source_base`` is the input glyph of the substitution that produces ``predecessor_variant``. It is often a bare base, but it can also be a variant or a ``.noentry`` stance. A forward-strip guard blocks that substitution to remove a dangling exit connector. That only helps when ``source_base``'s exit at ``exit_y`` is shorter or absent. In ``·It ~b~ ·Day.half``, ``qsDay.half.en-y0.ex-y0`` has the same rows as ``qsDay`` from its baseline exit down, so keeping ``qsDay`` removes nothing on the right and breaks the join with ·It, because ``qsDay`` enters at the x-height. When this returns True, the caller skips the guard and keeps the half stance.
    """
    bare_meta = glyph_meta.get(source_base)
    variant_meta = glyph_meta.get(predecessor_variant)
    if bare_meta is None or variant_meta is None or source_base == predecessor_variant:
        return False
    if exit_y not in set(bare_meta.exit_ys) or exit_y not in set(variant_meta.exit_ys):
        return False
    if not _predecessor_visually_reaches(glyph_meta, source_base):
        return False
    # Blocking the substitution must also lose a left-side join: the variant has an entry Y that `source_base` lacks (the half stance's baseline entry against the full stance's x-height entry). Otherwise the variant is an exit-only or noentry sibling, and skipping its guard would change shaping where no join is at stake.
    if not set(variant_meta.all_entry_ys) - set(bare_meta.all_entry_ys):
        return False
    y = exit_y
    while True:
        bare_row = _bitmap_row_at_y(bare_meta, y)
        variant_row = _bitmap_row_at_y(variant_meta, y)
        if bare_row is None and variant_row is None:
            return True
        if bare_row != variant_row:
            return False
        y -= 1


def _predecessor_visually_reaches(
    glyph_meta: Mapping[str, JoinGlyph],
    variant_name: str,
) -> bool:
    """Return whether the variant's exit stroke is a connector reaching toward the next glyph, which is left dangling when the follower's stance has no entry.

    Three cases qualify:

    * An exit extension (``.ex-ext-N``) adds ink toward the follower, even on a Short letter such as ``qsEight.ex-ext-1``.
    * A stance of 9 or more rows with the ``ex-y0`` modifier and its exit at y=0 carries its body's lower stroke into a baseline connector.
    * Ink below the exit row means the exit stroke leaves the body as a connector, as in ``qsGay.ex-y0``. A Short letter that exits at its bottom row, such as ``qsUtter.alt.ex-y0``, has no ink below, so its exit is only the glyph's bottom edge and does not count.
    """
    meta = glyph_meta.get(variant_name)
    if meta is None or not meta.exit or not meta.bitmap:
        return False
    if meta.extended_exit_suffix is not None:
        return True
    exit_y = meta.exit[0][1]
    if exit_y == 0 and "ex-y0" in meta.modifiers and len(meta.bitmap) >= 9:
        return True
    top_y = meta.y_offset + len(meta.bitmap) - 1
    for row_idx, row in enumerate(meta.bitmap):
        row_y = top_y - row_idx
        if row_y >= exit_y:
            continue
        if isinstance(row, str):
            if "#" in row:
                return True
        elif any(row):
            return True
    return False


def _bases_with_stripped_fwd_per_y(
    reachability: _FwdStripReachability,
) -> dict[int, frozenset[str]]:
    """Map each Y to the bare bases whose forward substitution at Y gives a stance with no entry at Y while another variant of the base has an entry at Y. The first condition is what leaves the predecessor's exit with nothing to join. The second is what puts the bare base in the predecessor's @entry_y class. A pair-override replacement whose ink at Y starts in the same column as an entry-bearing variant's is skipped."""
    glyph_meta = reachability.glyph_meta
    by_y: dict[int, set[str]] = {}
    family_entry_ys_by_base: dict[str, set[int]] = {}

    def _family_entry_ys(base: str) -> set[int]:
        if base not in family_entry_ys_by_base:
            family_entry_ys: set[int] = set()
            for variant in reachability.base_to_variants.get(base, frozenset()):
                variant_meta = glyph_meta.get(variant)
                if variant_meta is None:
                    continue
                family_entry_ys.update(variant_meta.all_entry_ys)
            family_entry_ys_by_base[base] = family_entry_ys
        return family_entry_ys_by_base[base]

    for base, fwd_at_y in reachability.fwd_replacements.items():
        family_entry_ys = _family_entry_ys(base)
        if not family_entry_ys:
            continue
        for exit_y, replacement in fwd_at_y.items():
            replacement_meta = glyph_meta.get(replacement)
            if replacement_meta is None:
                continue
            if exit_y in replacement_meta.all_entry_ys:
                continue
            if exit_y not in family_entry_ys:
                continue
            by_y.setdefault(exit_y, set()).add(base)

    for base, overrides in reachability.fwd_pair_overrides.items():
        family_entry_ys = _family_entry_ys(base)
        if not family_entry_ys:
            continue
        for replacement, _before, _not_after in overrides:
            replacement_meta = glyph_meta.get(replacement)
            if replacement_meta is None:
                continue
            for exit_y in set(replacement_meta.exit_ys):
                if exit_y in replacement_meta.all_entry_ys:
                    continue
                if exit_y not in family_entry_ys:
                    continue
                if _stripped_pair_replacement_keeps_entry_ink(
                    reachability,
                    base,
                    replacement_meta,
                    exit_y,
                ):
                    continue
                by_y.setdefault(exit_y, set()).add(base)
    return {y: frozenset(bases) for y, bases in by_y.items()}


def _stripped_pair_replacement_keeps_entry_ink(
    reachability: _FwdStripReachability,
    base: str,
    replacement_meta: JoinGlyph,
    exit_y: int,
) -> bool:
    replacement_bounds = _ink_bounds_at_y(replacement_meta, exit_y)
    if replacement_bounds is None:
        return False
    replacement_min_x, _ = replacement_bounds
    for variant in reachability.base_to_variants.get(base, frozenset()):
        variant_meta = reachability.glyph_meta.get(variant)
        if variant_meta is None or exit_y not in variant_meta.all_entry_ys:
            continue
        variant_bounds = _ink_bounds_at_y(variant_meta, exit_y)
        if variant_bounds is None:
            continue
        variant_min_x, _ = variant_bounds
        if variant_min_x == replacement_min_x:
            return True
    return False


def _add_fwd_strip_guards(
    buf: dict[tuple[str, str, int], list[FwdStripGuard]],
    candidate_bases: dict[int, frozenset[str]],
    source_base: str,
    predecessor_variant: str,
    predecessor_exit_y: int,
) -> None:
    bases = candidate_bases.get(predecessor_exit_y)
    if not bases:
        return
    key = (source_base, predecessor_variant, predecessor_exit_y)
    bucket = buf.setdefault(key, [])
    for mid_base in bases:
        bucket.append(FwdStripGuard(mid_base=mid_base))
