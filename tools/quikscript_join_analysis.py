"""Slim, side-effect-free reachability view over compiled Quikscript IR.

The validator added in subtask A2 of the parent plan needs structural facts
about ``dict[str, JoinGlyph]`` (which families exist, which variants of a
family carry which entry/exit Ys, which pair-overrides apply under which
contexts) without dragging in the FEA emitter's lookup-DAG and cycle-detection
machinery in ``quikscript_fea._analyze_quikscript_joins``.

``JoinReachability`` is the narrow waist for that. Field shapes mirror the
corresponding entries on ``_JoinAnalysis`` for downstream familiarity, but the
population logic intentionally reads ``JoinGlyph`` attributes directly. Lookup
ordering, cycle detection, and the FEA emitter's policy-specific gates stay in
``quikscript_fea`` where they belong.
"""

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from quikscript_ir import (
    Anchor,
    JoinGlyph,
    has_entry_preserving_exit_noentry_sibling,
)
from quikscript_fea import (
    _LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS,
    _resolve_noentry_replacement,
)


class JoinContractWarning(UserWarning):
    """A glyph's join-contract metadata fails one of the consistency checks
    in `collect_join_warnings`."""


class OrphanAnchorWarning(UserWarning):
    """An entry or exit anchor at some Y has no counterpart at that Y on any
    other glyph, so no cursive attachment can ever fire there."""


__all__ = [
    "DerivedBkGuard",
    "JoinContractWarning",
    "JoinReachability",
    "OrphanAnchorWarning",
    "collect_join_warnings",
    "derive_pending_bk_entry_guards",
    "derive_pending_liga_entry_guards",
    "validate_join_consistency",
    "warn_join_contract_issues",
]


@dataclass(frozen=True)
class DerivedBkGuard:
    guard_glyphs: tuple[str, ...]
    before_bases: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PairIntent:
    variant_name: str
    left_family: str
    right_family: str
    y: int


# Authoritative override of structural derivation. The structural pass in
# `_collect_guards` cannot yet (a) emit guard entries keyed on the bare base
# `qsTea` (its own `all_entry_ys` is empty; entries live on family variants)
# nor (b) infer the multi-step right-context narrowings on
# `qsExcite.exit-baseline.before-vertical` that the runtime emitter relies on.
# Until those gaps close, `derive_pending_*` returns these tables verbatim —
# the structural pass still runs as a sanity layer (the
# `test_structural_*_guards_cover_coverable_residual` tests in
# `test/test_quikscript_join_analysis.py` enforce that every residual entry
# the structural pass *can* in principle cover is in fact covered) but the
# override pins the public output to byte-for-byte parity with the runtime
# emitter's prior behavior. Once the structural pass tightens, remove this
# override in favor of pure derivation.
_RESIDUAL_BK_GUARDS: dict[
    tuple[str, str, int], tuple[DerivedBkGuard, ...]
] = {
    ("qsTea", "qsTea.exit-baseline", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.exit-baseline.before-vertical",),
            ("qsAh", "qsTea"),
        ),
    ),
    ("qsTea", "qsTea.half.exit-xheight", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.exit-baseline.before-vertical",),
            ("qsAwe",),
        ),
    ),
    ("qsTea.entry-baseline", "qsTea.exit-baseline", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.exit-baseline.before-vertical",),
            ("qsAh", "qsTea"),
        ),
    ),
    ("qsTea.entry-baseline", "qsTea.half.exit-xheight", 0): (
        DerivedBkGuard(("qsEt",)),
        DerivedBkGuard(
            ("qsExcite.exit-baseline.before-vertical",),
            ("qsAwe",),
        ),
    ),
}

# Bitmap-gap pairs the structural collector flags but we knowingly accept.
_RESIDUAL_BITMAP_GAPS: frozenset[tuple[str, str, int]] = frozenset()


_RESIDUAL_LIGA_GUARDS: dict[
    tuple[str, str, int], tuple[DerivedBkGuard, ...]
] = {
    ("qsTea", "qsTea_qsOy", 0): (
        DerivedBkGuard(
            ("qsExcite.exit-baseline.before-vertical",),
            ("qsOy",),
        ),
    ),
    ("qsTea.entry-baseline", "qsTea_qsOy", 0): (
        DerivedBkGuard(
            ("qsExcite.exit-baseline.before-vertical",),
            ("qsOy",),
        ),
    ),
}


@dataclass(frozen=True)
class JoinReachability:
    glyph_meta: Mapping[str, JoinGlyph]
    base_to_variants: Mapping[str, frozenset[str]]
    bk_replacements: Mapping[str, Mapping[int, str]]
    pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...]], ...]]
    fwd_replacements: Mapping[str, Mapping[int, str]]
    fwd_pair_overrides: Mapping[
        str, tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    ]
    gated_pair_overrides: Mapping[str, tuple[tuple[str, tuple[str, ...], str], ...]]
    gated_fwd_pair_overrides: Mapping[
        str, tuple[tuple[str, tuple[str, ...], tuple[str, ...], str], ...]
    ]
    ligatures: tuple[tuple[str, tuple[str, ...]], ...]
    word_final_pairs: Mapping[str, str]
    entry_classes: Mapping[int, frozenset[str]]

    @classmethod
    def from_join_glyphs(
        cls, glyph_meta: Mapping[str, JoinGlyph]
    ) -> "JoinReachability":
        base_to_variants_buf: dict[str, set[str]] = {}
        bk_replacements_buf: dict[str, dict[int, str]] = {}
        pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        fwd_replacements_buf: dict[str, dict[int, str]] = {}
        fwd_pair_overrides_buf: dict[
            str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]
        ] = {}
        gated_pair_overrides_buf: dict[str, list[tuple[str, tuple[str, ...], str]]] = {}
        gated_fwd_pair_overrides_buf: dict[
            str, list[tuple[str, tuple[str, ...], tuple[str, ...], str]]
        ] = {}
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
                    bk_replacements_buf.setdefault(meta.base_name, {}).setdefault(
                        entry_y, glyph_name
                    )

            if meta.after:
                after = tuple(meta.after)
                if meta.gate_feature:
                    gated_pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, after, meta.gate_feature)
                    )
                else:
                    pair_overrides_buf.setdefault(meta.base_name, []).append(
                        (glyph_name, after)
                    )

            if meta.exit and not meta.before:
                exit_y = meta.exit[0][1]
                fwd_replacements_buf.setdefault(meta.base_name, {}).setdefault(
                    exit_y, glyph_name
                )

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
            entry_classes=MappingProxyType(
                {y: frozenset(names) for y, names in entry_classes_buf.items()}
            ),
        )


def validate_join_consistency(join_glyphs: Mapping[str, JoinGlyph]) -> None:
    """Steady-state cursive-join consistency check.

    For every form F that declares a contextual selector (``select.before`` or
    ``select.after``) and carries the matching cursive anchor, assert that some
    reachable variant of each named target family carries a compatible anchor
    on the other side at the same Y. Validates the default state plus each
    distinct stylistic-set gate observed in the corpus.

    Raises ``ValueError`` listing every mismatch. Orphan anchors (an exit Y
    with no matching entry Y anywhere, or vice versa) are warned to stderr
    only.
    """
    reachability = JoinReachability.from_join_glyphs(join_glyphs)
    glyph_meta_dict = dict(reachability.glyph_meta)
    base_to_variants_dict = {
        base: set(variants)
        for base, variants in reachability.base_to_variants.items()
    }

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
    _warn_orphans(reachability)
    if errors:
        raise ValueError(
            "Join consistency mismatches:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def collect_join_warnings(join_glyphs: Mapping[str, JoinGlyph]) -> tuple[str, ...]:
    """Return non-fatal diagnostics for joins that are still source-fragile."""
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
    warnings.extend(
        _collect_bitmap_gap_warnings(reachability, forward_intents, backward_intents)
    )
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
    """Whether ``family`` has a default form that joins with ``opposite_family`` at y.

    A default form is one that carries the right anchor (``exit`` for
    ``direction="exit"``, ``entry`` / ``entry_curs_only`` for ``"entry"``)
    but no matching ``before:`` / ``after:`` selector — meaning the form
    fires whenever its base does, with no per-pair gating. Such a form
    silently covers every right-hand (or left-hand) family that isn't
    excluded by ``not_before:`` / ``not_after:``.

    On the entry-direction (forward-intent) branch, candidates whose
    ``noentry_after`` lists ``opposite_family`` are also rejected: at runtime
    they're displaced to their ``.noentry`` counterpart whenever the opposite
    family precedes, so they don't actually cover the join.
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
        if any(
            _resolve_family(reachability, n) == opposite_family for n in negated
        ):
            continue
        if direction == "entry" and any(
            _resolve_family(reachability, n) == opposite_family
            for n in meta.noentry_after
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
    """Whether the right family's entry-y=`y` variants are displaced by ``noentry_after``.

    The backward-intent suppression call to ``_has_default_join_coverage``
    inspects the *left* family (looking for a default exit at ``y``).
    `noentry_after` lives on the *right* family, so this helper provides the
    parallel check: if any right-family variant carrying entry y=`y` lists
    ``left_family`` in its ``noentry_after``, the receiver is displaced to
    its ``.noentry`` counterpart whenever ``left_family`` precedes — so the
    left family's "default exit" does not actually support the join, and the
    one-sided-selection warning should not be suppressed.
    """
    for variant_name in reachability.base_to_variants.get(right_family, frozenset()):
        meta = reachability.glyph_meta.get(variant_name)
        if meta is None or meta.generated_from is not None or meta.is_noentry:
            continue
        if not meta.noentry_after:
            continue
        if not any(
            anchor[1] == y for anchor in (*meta.entry, *meta.entry_curs_only)
        ):
            continue
        if any(
            _resolve_family(reachability, n) == left_family
            for n in meta.noentry_after
        ):
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
    """Variants whose joining shape is wasted because of a ``noentry_after``.

    For every variant ``V_R`` carrying ``noentry_after: [F_1, …]`` and at
    least one entry-side anchor at Y, find every variant ``V_L`` of every
    named family ``F_i`` that

    - exits at Y, and
    - is plausibly selected when ``F_i`` precedes ``V_R``'s family — i.e.
      ``V_R.base_name`` is not in ``V_L.not_before``, and either ``V_L.before``
      is empty or contains ``V_R.base_name``, and ``V_L`` is not itself a
      generated/``.noentry`` form.

    These ``V_L`` variants choose a joining shape whose join is voided at
    runtime by the ``noentry_after`` substitution — the joining stub is
    visually rendered with nothing to attach to. Pairs are deduped on
    ``(V_L, R_base, y)`` so multiple ``V_R`` siblings of one right family
    surface a single warning.
    """
    pairs: dict[tuple[str, str, int], str] = {}

    for r_name, r_meta in reachability.glyph_meta.items():
        if r_meta.generated_from is not None or r_meta.is_noentry:
            continue
        if not r_meta.noentry_after:
            continue
        r_family = r_meta.base_name
        r_entry_ys = {
            anchor[1] for anchor in (*r_meta.entry, *r_meta.entry_curs_only)
        }
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
                    _resolve_family(reachability, n) == r_family
                    for n in l_meta.before
                ):
                    continue
                if any(
                    _resolve_family(reachability, n) == r_family
                    for n in l_meta.not_before
                ):
                    continue
                if has_entry_preserving_exit_noentry_sibling(
                    l_meta,
                    {
                        base: set(variants)
                        for base, variants in reachability.base_to_variants.items()
                    },
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
            intent.variant_name
            for intent in forward_by_key.get(key, ())
        } or _candidate_names_with_exit(
            reachability,
            left_family,
            y,
            opposite_family=right_family,
        )
        source_right_names = {
            intent.variant_name
            for intent in backward_by_key.get(key, ())
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
                        right_anchor = _first_anchor_at(
                            (*right_meta.entry, *right_meta.entry_curs_only), y
                        )
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
                            # The trim removed every ink cell at the join row.
                            # Fall back to the pre-trim parent bitmap so the gap
                            # check sees the ink position the predecessor's exit
                            # is meant to overlap, instead of flagging the empty
                            # row as a missing-side join.
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
    """Family triples where both sides could theoretically join at y, with no
    explicit `before:` / `after:` gating. The intent-keyed pass only walks
    pairs where one side declared a pair selector; this fills in the rest so
    the bitmap-gap collector sees default-default joins too."""
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
    """A base ligature glyph (no exit/entry suffix) never appears at runtime
    when its trailing component has an `extend_exit_before` or
    `contract_exit_before` rule that fires for the right-side family — the
    rule mutates the trailing component pre-liga, then `calt_liga` collapses
    into the corresponding ligature variant. Skipping such bases here keeps
    the bitmap-gap warning collector from flagging theoretical pairs that
    can't form at runtime."""
    if not meta.sequence:
        return False
    if meta.extended_exit_suffix or meta.contracted_exit_suffix:
        return False
    last_meta = reachability.glyph_meta.get(meta.sequence[-1])
    if last_meta is None:
        return False
    if last_meta.contract_exit_before and opposite_family in last_meta.contract_exit_before.targets:
        return True
    if last_meta.extend_exit_before and opposite_family in last_meta.extend_exit_before.targets:
        return True
    return False


def _ligature_base_mutates_at_entry(
    reachability: JoinReachability,
    meta: JoinGlyph,
    opposite_family: str,
) -> bool:
    """Mirror of `_ligature_base_mutates_at_exit` for the lead-component
    side."""
    if not meta.sequence:
        return False
    if meta.extended_entry_suffix:
        return False
    first_meta = reachability.glyph_meta.get(meta.sequence[0])
    if first_meta is None:
        return False
    if first_meta.contract_entry_after and opposite_family in first_meta.contract_entry_after.targets:
        return True
    if first_meta.extend_entry_after and opposite_family in first_meta.extend_entry_after.targets:
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
    return not any(
        _resolve_family(reachability, selector) == family for selector in meta.not_before
    )


def _entry_candidate_permits_family(
    reachability: JoinReachability,
    meta: JoinGlyph,
    family: str,
) -> bool:
    if meta.after and not _after_context_matches_family(reachability, meta, family):
        return False
    if any(
        _resolve_family(reachability, selector) == family for selector in meta.not_after
    ):
        return False
    return not any(
        _resolve_family(reachability, selector) == family for selector in meta.noentry_after
    )


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
        name
        for name in generated
        if _has_generated_transform_on_side(reachability.glyph_meta[name], side)
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
        _selector_matches_family_or_name(reachability, selector, family, names)
        for selector in meta.before
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
        _selector_matches_family_or_name(reachability, selector, family, names)
        for selector in meta.after
    )


def _ligature_component_propagates_context(
    reachability: JoinReachability,
    source_meta: JoinGlyph | None,
    candidate_meta: JoinGlyph,
    opposite_family: str,
    side: str,
) -> bool:
    # `_add_entry_contraction_variants` / `_add_entry_extension_variants` (and
    # the symmetric exit-side helpers) in `quikscript_ir` propagate a
    # component's contract / extend rule onto the matching ligature variant
    # but leave the variant's own `after` / `before` empty (the propagated
    # context is intersected with the base ligature's, which is `()`). At
    # runtime `calt_liga` still routes through that variant whenever the
    # component form's contraction / extension fires, so accept it as a swap
    # candidate when the relevant component family carries a matching rule
    # whose targets include `opposite_family`. The lead component drives
    # entry-side propagation; the trailing component drives exit-side.
    #
    # Ligature inheritance (mirroring `expand_selectors_for_ligatures`)
    # applies to the rule's `targets` list too: a target family `qsZ` matches
    # `opposite_family` directly *or* matches when `opposite_family` is a
    # ligature whose lead (entry side) or trailing (exit side) component is
    # `qsZ`, because runtime context lookups see that boundary component
    # pre-liga.
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
        if any(target in spec.targets for target in opposite_targets):
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


def _effective_exit_x(
    meta: JoinGlyph, anchor_x: int, right_family: str | None
) -> int:
    # `extend_exit_before` widens the bitmap rightward by the same amount it
    # shifts the exit anchor, so the visible gap is unchanged — skip it.
    if right_family is None:
        return anchor_x
    if meta.contract_exit_before and right_family in meta.contract_exit_before.targets:
        anchor_x -= meta.contract_exit_before.by
    return anchor_x


def _effective_entry_x(
    meta: JoinGlyph, anchor_x: int, left_family: str | None
) -> int:
    # `extend_entry_after` prepends ink to the receiver's bitmap, capped by the
    # original gap — modeling that without the bitmap rewrite would over-correct,
    # so skip it.
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
    entry_owners: dict[int, list[str]] = {
        y: sorted(names) for y, names in reachability.entry_classes.items()
    }
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
                anchor[1]
                for _, meta in candidates
                for anchor in (*meta.entry, *meta.entry_curs_only)
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
            exit_ys = {
                anchor[1] for _, meta in candidates for anchor in meta.exit
            }
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
            for variant, after_glyphs, tag in reachability.gated_pair_overrides.get(
                t_family, ()
            )
            if tag == gated_feature and source_family in after_glyphs
        }
        if gated_match:
            candidate_names = gated_match
        else:
            candidate_names = _candidate_names_for_family(
                reachability, t_family, side="right"
            )
    else:
        candidate_names = _candidate_names_for_family(
            reachability, t_family, side="right"
        )

    resolved: list[tuple[str, JoinGlyph]] = []
    seen: set[str] = set()
    for name in candidate_names:
        resolved_name = _resolve_noentry_replacement(
            glyph_meta, base_to_variants, name, name
        )
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
            for variant, before_glyphs, _, tag in reachability.gated_fwd_pair_overrides.get(
                t_family, ()
            )
            if tag == gated_feature and source_family in before_glyphs
        }
        if gated_match:
            candidate_names = gated_match
        else:
            candidate_names = _candidate_names_for_family(
                reachability, t_family, side="left"
            )
    else:
        candidate_names = _candidate_names_for_family(
            reachability, t_family, side="left"
        )

    resolved: list[tuple[str, JoinGlyph]] = []
    seen: set[str] = set()
    for name in candidate_names:
        resolved_name = _resolve_noentry_replacement(
            glyph_meta, base_to_variants, name, name
        )
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
            if (
                lig_name in _LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS
                and len(components) >= 2
            ):
                second = reachability.glyph_meta.get(components[1])
                if second and second.family == t_family:
                    candidates.add(lig_name)
        else:  # left
            last = reachability.glyph_meta.get(components[-1])
            if last and last.family == t_family:
                candidates.add(lig_name)
    return candidates


def _resolve_family(reachability: JoinReachability, t_name: str) -> str:
    """Resolve a selector reference to a family name.

    Selectors compile to glyph names (`{family: qsTea}` → `"qsTea"`,
    `{family: qsTea, traits: [alt]}` → `"qsTea.alt"`); the validator's
    family-bucket lookups key on `base_name`.
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
        for _, after_glyphs, tag in reachability.gated_pair_overrides.get(
            t_family, ()
        )
    )


def _gated_left_match(
    reachability: JoinReachability,
    t_family: str,
    source_family: str,
    gated_feature: str,
) -> bool:
    return any(
        tag == gated_feature and source_family in before_glyphs
        for _, before_glyphs, _, tag in reachability.gated_fwd_pair_overrides.get(
            t_family, ()
        )
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
    witness = "{" + ", ".join(sorted(name for name, _ in candidates)) + "}"
    ys_repr = "{" + ", ".join(str(y) for y in sorted(entry_ys)) + "}" if entry_ys else "∅"
    gate_note = f" under {gated_feature}" if gated_feature else ""
    wf_note = " (word-final)" if end_of_word else ""
    return (
        f"{source_name}{wf_note} expects to exit at y={exit_y} toward "
        f"{t_family}{gate_note}, but reachable variants of {t_family} "
        f"({witness}) carry entry Ys {ys_repr} — no y={exit_y} entry"
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
    witness = "{" + ", ".join(sorted(name for name, _ in candidates)) + "}"
    ys_repr = "{" + ", ".join(str(y) for y in sorted(exit_ys)) + "}" if exit_ys else "∅"
    gate_note = f" under {gated_feature}" if gated_feature else ""
    wf_note = " (word-final)" if end_of_word else ""
    return (
        f"{source_name}{wf_note} expects to receive entry at y={entry_y} from "
        f"{t_family}{gate_note}, but reachable variants of {t_family} "
        f"({witness}) carry exit Ys {ys_repr} — no y={entry_y} exit"
    )


def derive_pending_bk_entry_guards(
    reachability: JoinReachability,
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    """Public entrypoint pinned to ``_RESIDUAL_BK_GUARDS``.

    Runs the structural derivation in ``_compute_derived_bk_guards`` (which
    produces a superset of the structurally-coverable residual entries) and
    then pins the result to ``_RESIDUAL_BK_GUARDS`` via
    ``_apply_residual_override``. The structural pass continues to run so the
    coverable-residual tests stay meaningful; the override closes the
    byte-for-byte FEA parity gap until derivation can structurally identify
    exactly the residual keys (see the docstring on ``_RESIDUAL_BK_GUARDS``).
    """
    return _apply_residual_override(
        _compute_derived_bk_guards(reachability), _RESIDUAL_BK_GUARDS
    )


def derive_pending_liga_entry_guards(
    reachability: JoinReachability,
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    """Public entrypoint pinned to ``_RESIDUAL_LIGA_GUARDS``.

    Mirror of ``derive_pending_bk_entry_guards`` for ligature first-component
    sources; the override pins the result to ``_RESIDUAL_LIGA_GUARDS``.
    """
    return _apply_residual_override(
        _compute_derived_liga_guards(reachability), _RESIDUAL_LIGA_GUARDS
    )


def _compute_derived_bk_guards(
    reachability: JoinReachability,
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    """Structural pass behind ``derive_pending_bk_entry_guards``.

    Walks every ``(source, replacement)`` forward-substitution pair the FEA
    emitter would consider and, for each global exit Y the replacement cannot
    accept, collects every left-glyph variant that exits at that Y. These are
    the glyphs that would have joined the un-substituted source and whose join
    the substitution would silently break — the same set the runtime
    ``_emit_pending_bk_entry_guards`` emits ``ignore sub`` rules for.

    Output is a superset of the curated table; ``_apply_residual_override``
    pins the public output to curated parity.
    """
    glyph_by_exit_y = _index_glyphs_by_exit_y(reachability)
    buf: dict[tuple[str, str, int], list[DerivedBkGuard]] = {}
    for source_name, replacement_name in _iter_forward_sub_pairs(reachability):
        _collect_guards(
            reachability, glyph_by_exit_y, buf, source_name, replacement_name
        )
    return _finalize_guards(buf)


def _compute_derived_liga_guards(
    reachability: JoinReachability,
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    """Structural pass behind ``derive_pending_liga_entry_guards``.

    For every ligature, treats every variant of the first-component family as
    a possible runtime source being consumed; for each global exit Y the
    ligature cannot accept, collects every left-glyph variant whose exit Y
    would have produced a join. Mirrors the runtime semantics of
    ``_matching_pending_liga_guards``.
    """
    glyph_by_exit_y = _index_glyphs_by_exit_y(reachability)
    buf: dict[tuple[str, str, int], list[DerivedBkGuard]] = {}
    for lig_name, components in reachability.ligatures:
        if not components:
            continue
        first_component_meta = reachability.glyph_meta.get(components[0])
        if first_component_meta is None:
            continue
        first_family = first_component_meta.family or first_component_meta.base_name
        for source_name in reachability.base_to_variants.get(first_family, frozenset()):
            _collect_guards(
                reachability, glyph_by_exit_y, buf, source_name, lig_name
            )
    return _finalize_guards(buf)


def _index_glyphs_by_exit_y(
    reachability: JoinReachability,
) -> dict[int, list[tuple[str, JoinGlyph]]]:
    by_y: dict[int, list[tuple[str, JoinGlyph]]] = {}
    for name, meta in reachability.glyph_meta.items():
        for y in meta.exit_ys:
            by_y.setdefault(y, []).append((name, meta))
    return by_y


def _iter_forward_sub_pairs(reachability: JoinReachability):
    seen: set[tuple[str, str]] = set()

    def emit(source: str, replacement: str):
        pair = (source, replacement)
        if pair in seen:
            return
        seen.add(pair)
        yield pair

    for base, exit_to_variant in reachability.fwd_replacements.items():
        for variant in exit_to_variant.values():
            yield from emit(base, variant)
    for base, overrides in reachability.fwd_pair_overrides.items():
        for variant, _, _ in overrides:
            yield from emit(base, variant)

    # bk_var × fwd_var: the runtime emits sub rules at call sites 4/5 of
    # _emit_pending_bk_entry_guards (tools/quikscript_fea.py:2332, 2358), where
    # a backward variant is forward-subbed to a forward variant of the same
    # base. The bk_replacements dict is keyed by base; the corresponding fwd
    # variants live under the same base in the fwd_* maps. gated_fwd_pair_overrides
    # is intentionally omitted: the gated calt block at quikscript_fea.py:2990
    # uses its own _collect_pending_bk_pair_guards and never reads _derived_bk_guards,
    # so pairs sourced from there populate the dict but no caller queries them.
    for base, entry_to_bk_var in reachability.bk_replacements.items():
        fwd_variants: list[str] = []
        fwd_variants.extend(reachability.fwd_replacements.get(base, {}).values())
        fwd_variants.extend(
            v for v, _, _ in reachability.fwd_pair_overrides.get(base, ())
        )
        for bk_var in entry_to_bk_var.values():
            for fwd_var in fwd_variants:
                yield from emit(bk_var, fwd_var)


def _collect_guards(
    reachability: JoinReachability,
    glyph_by_exit_y: dict[int, list[tuple[str, JoinGlyph]]],
    buf: dict[tuple[str, str, int], list[DerivedBkGuard]],
    source_name: str,
    replacement_name: str,
) -> None:
    replacement_meta = reachability.glyph_meta.get(replacement_name)
    if replacement_meta is None:
        return
    source_meta = reachability.glyph_meta.get(source_name)
    if source_meta is None:
        return
    # A guard at (source, replacement, entry_y) only fires if a left glyph at
    # exit-Y entry_y could have joined source pre-substitution; that requires
    # source to have an entry anchor at entry_y. Residual entries keyed on bare
    # bases (like qsTea, whose all_entry_ys is empty but whose family carries
    # entries) are dropped here by design — `_apply_residual_override`
    # reintroduces them for the cases derivation cannot structurally cover.
    source_entry_ys = set(source_meta.all_entry_ys)
    replacement_entry_ys = set(replacement_meta.all_entry_ys)
    for entry_y, left_glyphs in glyph_by_exit_y.items():
        if entry_y in replacement_entry_ys:
            continue
        if entry_y not in source_entry_ys:
            continue
        for left_name, _ in left_glyphs:
            buf.setdefault((source_name, replacement_name, entry_y), []).append(
                DerivedBkGuard(guard_glyphs=(left_name,))
            )


def _finalize_guards(
    buf: dict[tuple[str, str, int], list[DerivedBkGuard]],
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    out: dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]] = {}
    for key, guards in buf.items():
        deduped = sorted(set(guards), key=lambda g: (g.guard_glyphs, g.before_bases))
        out[key] = tuple(deduped)
    return out


def _apply_residual_override(
    out: dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]],
    residual: dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]],
) -> dict[tuple[str, str, int], tuple[DerivedBkGuard, ...]]:
    # `out` is intentionally accepted-and-ignored. The structural pass still
    # runs upstream so the superset invariants stay derivable; this override
    # pins the public output to the residual until derivation is tight enough
    # to match curated keys structurally.
    del out
    return {key: tuple(guards) for key, guards in residual.items()}
