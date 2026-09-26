import re
import warnings
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

from quikscript_ir import (
    _SYNTHESIZED_MODIFIER_TOKENS,
    JoinGlyph,
    family_names_from_compiled,
    heal_glyph_name,
    resolve_known_glyph_names,
    ss10_twin_name,
    ss10_twins,
)

_ENTRY_EXTENSION_SUFFIXES = (
    ".en-ext-6",
    ".en-ext-5",
    ".en-ext-4",
    ".en-ext-3",
    ".en-ext-2",
    ".en-ext-1",
)


_EXIT_EXTENSION_WORD_BY_COUNT = {
    1: "ext-1",
    2: "ext-2",
    3: "ext-3",
    4: "ext-4",
    5: "ext-5",
    6: "ext-6",
}

_EXIT_CONTRACTION_WORD_BY_COUNT = {
    1: "con-1",
    2: "con-2",
    3: "con-3",
    4: "con-4",
    5: "con-5",
    6: "con-6",
}


_LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS = {
    "qsOut_qsTea",
}


@dataclass
class _JoinAnalysis:
    glyph_meta: dict[str, JoinGlyph]
    glyph_names: set[str] = field(default_factory=set)
    base_to_variants: dict[str, set[str]] = field(default_factory=dict)
    bk_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    bk_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    bk_fwd_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    bk_fwd_exclusion_sequences: dict[str, dict[int, list[tuple[str, ...]]]] = field(default_factory=dict)
    pair_overrides: dict[str, list[tuple[str, list[str]]]] = field(default_factory=dict)
    fwd_upgrades: dict[str, list[tuple[str, str, int, list[str]]]] = field(default_factory=dict)
    fwd_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    fwd_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    fwd_bk_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    fwd_pair_overrides: dict[str, list[tuple[str, list[str], list[str]]]] = field(default_factory=dict)
    reverse_only_upgrades: list[tuple[str, list[str], list[int], list[str], list[str]]] = field(
        default_factory=list
    )
    terminal_entry_only: set[str] = field(default_factory=set)
    terminal_exit_only: set[str] = field(default_factory=set)
    exit_classes: dict[int, set[str]] = field(default_factory=dict)
    entry_classes: dict[int, set[str]] = field(default_factory=dict)
    entry_exclusive: dict[int, set[str]] = field(default_factory=dict)
    fwd_use_exclusive: set[tuple[str, int]] = field(default_factory=set)
    fwd_preferred_lookahead: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)
    preferred_lookahead_bridges: dict[tuple[int, int], set[str]] = field(default_factory=dict)
    sorted_bases: list[str] = field(default_factory=list)
    cycle_bases: set[str] = field(default_factory=set)
    edges: dict[str, set[str]] = field(default_factory=dict)
    pair_only: list[str] = field(default_factory=list)
    all_bk_bases: list[str] = field(default_factory=list)
    all_fwd_bases: set[str] = field(default_factory=set)
    fwd_only: list[str] = field(default_factory=list)
    early_pair_fwd_general: list[str] = field(default_factory=list)
    early_pair_fwd_general_exit_ys: dict[str, set[int]] = field(default_factory=dict)
    lig_fwd_bases: set[str] = field(default_factory=set)
    early_pair_upgrade_bases: set[str] = field(default_factory=set)
    early_fwd_pairs: set[str] = field(default_factory=set)
    ligatures: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    word_final_pairs: dict[str, str] = field(default_factory=dict)
    gated_pair_overrides: dict[str, list[tuple[str, list[str], str]]] = field(default_factory=dict)
    gated_fwd_pair_overrides: dict[str, list[tuple[str, list[str], list[str], str]]] = field(
        default_factory=dict
    )
    exit_reachability: dict[str, set[int]] = field(default_factory=dict)
    exit_reachability_before: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    gated_exit_reachability: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    gated_exit_reachability_before: dict[tuple[str, str, str], set[int]] = field(default_factory=dict)
    # Each entry is (prior_family, target_family, follower_family, isolated_form), from `restore_isolated_form_overrides` in `glyph_data/quikscript.yaml`, whose comment describes the rules. They become `calt_pair_guard_reflip_*` rules and the `calt_post_reflip_bk_*` lookups that follow them.
    restore_isolated_form_overrides: tuple[tuple[str, str, str, str], ...] = ()
    # Each entry is (backtrack_stance or None, predecessor_stance, trigger_stance, isolated_form), from `predecessor_demote_overrides` in `glyph_data/quikscript.yaml`, whose comment describes the rules. A backtrack stance limits the demote to one stance of the glyph before the predecessor, as when `qsMay.en-y0.ex-y5` precedes `qsGay.en-y0.ex-y5`.
    predecessor_demote_overrides: tuple[tuple[str | None, str, str, str], ...] = ()
    # Each entry is (leader_stance, trailing_stance, isolated_form), from `trailing_demote_overrides` in `glyph_data/quikscript.yaml`, whose comment describes the rules.
    trailing_demote_overrides: tuple[tuple[str, str, str], ...] = ()


def _backward_pair_sort_key(
    glyph_meta: dict[str, JoinGlyph],
    variant_name: str,
    selector_glyphs: list[str],
) -> tuple[int, int, int, int, int, str]:
    # Orders one base's pair-override lookups. A lookup emitted earlier takes any input that a later sibling's context also matches. A `terminal_default` stance goes first, then stances with more modifiers, more `before` entries, more `not_before` entries, and fewer selector glyphs, and `variant_name` breaks the remaining ties.
    meta = glyph_meta[variant_name]
    return (
        0 if meta.terminal_default else 1,
        -len(meta.modifiers),
        -len(meta.before),
        -len(meta.not_before),
        len(selector_glyphs),
        variant_name,
    )


def _expand_join_variants(
    glyphs,
    analysis: _JoinAnalysis,
    *,
    include_base: bool = False,
) -> set[str]:
    glyph_meta = analysis.glyph_meta
    expanded = set(glyphs)
    for glyph in glyphs:
        glyph_meta_entry = glyph_meta.get(glyph)
        base = glyph_meta_entry.base_name if glyph_meta_entry else glyph
        if base not in glyph_meta:
            continue
        form_specific = glyph != base
        if include_base:
            expanded.add(base)
        all_variants: set[str] = set()
        if base in analysis.bk_replacements:
            all_variants.update(analysis.bk_replacements[base].values())
        if base in analysis.fwd_replacements:
            all_variants.update(analysis.fwd_replacements[base].values())
        if base in analysis.pair_overrides:
            all_variants.update(variant_name for variant_name, _ in analysis.pair_overrides[base])
        if base in analysis.fwd_pair_overrides:
            all_variants.update(variant_name for variant_name, _, _ in analysis.fwd_pair_overrides[base])
        if base in analysis.fwd_upgrades:
            # `fwd_upgrades` holds entry-and-exit stances that share an entry Y with an entry-only stance, which `bk_replacements` keeps instead (e.g. `qsTea.half.en-y8.ex-y5`). Without them a `{family: qsX}` selector drops those stances, and a derive lookup that should fire after one (`qsRoe.entry_xheight.contract_entry_after`) falls through to a broader competing lookup.
            all_variants.update(entry_exit_var for entry_exit_var, _, _, _ in analysis.fwd_upgrades[base])
        if form_specific:
            prefix = glyph + "."
            expanded.update(
                variant for variant in all_variants if variant == glyph or variant.startswith(prefix)
            )
        else:
            expanded.update(all_variants)
    return expanded


def _analyze_quikscript_joins(join_glyphs: dict[str, JoinGlyph]) -> _JoinAnalysis:
    glyph_meta = join_glyphs

    def _meta(name: str) -> JoinGlyph:
        return glyph_meta[name]

    plan = _JoinAnalysis(glyph_meta=glyph_meta, glyph_names=set(glyph_meta))
    for glyph_name, glyph_meta_entry in glyph_meta.items():
        plan.base_to_variants.setdefault(glyph_meta_entry.base_name, set()).add(glyph_name)

    bk_replacements = plan.bk_replacements
    bk_exclusions = plan.bk_exclusions
    pair_overrides = plan.pair_overrides
    fwd_upgrades = plan.fwd_upgrades
    bk_fwd_candidates: list[tuple[str, int, str, list[str]]] = []

    for glyph_name, meta in glyph_meta.items():
        if meta.word_final:
            continue
        if meta.is_noentry:
            continue
        if "ex-noentry" in meta.modifiers and not (meta.after and meta.generated_from is None):
            # `ex-noentry` stances are chosen by the post-liga cleanup, which moves the predecessor of a `noentry_after` ligature onto one, and by forward-pair rules; in `bk_replacements` or `fwd_upgrades` they would displace the regular entry-only stances from pre-liga selection. `after:` admits an authored backward override (`qsPea.en-y0.ex-noentry` after ·Et and ·Awe), which the `calt_after` branch below files under `pair_overrides`. `generated_from is None` skips derived stances that carry an `after:`, such as `qsIt.en-y0.ex-noentry.en-ext-1` after ·Key, which would otherwise become a new backward override.
            continue
        if not meta.is_entry_variant:
            if not meta.entry and not meta.after:
                continue
            if "half" not in meta.traits and "alt" not in meta.traits and not meta.after:
                continue
        if not meta.entry and not meta.after:
            continue
        if meta.reverse_upgrade_from:
            continue
        entry_y = meta.entry[0][1] if meta.entry else None
        base_name = meta.base_name
        if base_name not in glyph_meta:
            continue
        if "alt" in meta.traits:
            base_meta = glyph_meta.get(base_name)
            if base_meta and entry_y in base_meta.entry_ys:
                continue
        if meta.before and not meta.after:
            continue
        calt_after = meta.after
        if calt_after:
            if meta.gate_feature:
                plan.gated_pair_overrides.setdefault(base_name, []).append(
                    (glyph_name, list(calt_after), meta.gate_feature)
                )
            else:
                pair_overrides.setdefault(base_name, []).append((glyph_name, list(calt_after)))
        elif meta.extended_entry_suffix is not None:
            pass
        elif meta.extended_exit_suffix is not None:
            pass
        elif meta.contracted_entry_suffix is not None:
            pass
        elif meta.contracted_exit_suffix is not None:
            pass
        else:
            if "half" in meta.traits and not meta.exit:
                continue
            assert entry_y is not None
            existing = bk_replacements.get(base_name, {}).get(entry_y)
            if existing is not None:
                existing_meta = _meta(existing)
                existing_has_exit = bool(existing_meta.exit)
                new_has_exit = bool(meta.exit)
                if existing_has_exit != new_has_exit:
                    if new_has_exit:
                        exit_y_val = meta.exit[0][1]
                        nb = list(meta.not_before)
                        fwd_upgrades.setdefault(base_name, []).append(
                            (glyph_name, existing, exit_y_val, list(nb))
                        )
                    else:
                        exit_y_val = existing_meta.exit[0][1]
                        nb = list(existing_meta.not_before)
                        fwd_upgrades.setdefault(base_name, []).append(
                            (existing, glyph_name, exit_y_val, list(nb))
                        )
                        bk_replacements[base_name][entry_y] = glyph_name
                else:
                    bk_replacements.setdefault(base_name, {})[entry_y] = glyph_name
            else:
                bk_replacements.setdefault(base_name, {})[entry_y] = glyph_name
            not_after = meta.not_after
            if not_after:
                resolved = resolve_known_glyph_names(not_after, plan.glyph_names)
                bk_exclusions.setdefault(base_name, {})[entry_y] = resolved
            if meta.not_before:
                bk_fwd_candidates.append((base_name, entry_y, glyph_name, list(meta.not_before)))
            elif meta.not_before_from_noentry_after:
                bk_fwd_candidates.append(
                    (
                        base_name,
                        entry_y,
                        glyph_name,
                        list(meta.not_before_from_noentry_after),
                    )
                )

    for base_name, entry_y, glyph_name, not_before in bk_fwd_candidates:
        if bk_replacements.get(base_name, {}).get(entry_y) == glyph_name:
            resolved_fwd = resolve_known_glyph_names(not_before, plan.glyph_names)
            plan.bk_fwd_exclusions.setdefault(base_name, {})[entry_y] = resolved_fwd
            sequences: list[tuple[str, ...]] = []
            seen_sequences: set[tuple[str, ...]] = set()
            for family in not_before:
                family_meta = glyph_meta.get(family)
                if family_meta is None or not family_meta.sequence:
                    continue
                seq = tuple(family_meta.sequence)
                if seq in seen_sequences:
                    continue
                seen_sequences.add(seq)
                sequences.append(seq)
            if sequences:
                plan.bk_fwd_exclusion_sequences.setdefault(base_name, {})[entry_y] = sequences

    # `derive.noentry_after` on a non-ligature family becomes a backward pair override that swaps the base glyph for its `.noentry` variant after the listed families. The post-liga cleanup in `_emit_quikscript_calt` handles ligatures.
    for glyph_name, meta in glyph_meta.items():
        if glyph_name != meta.base_name:
            continue
        if not meta.noentry_after:
            continue
        if meta.sequence:
            continue
        noentry_name = glyph_name + ".noentry"
        if noentry_name not in glyph_meta:
            continue
        pair_overrides.setdefault(glyph_name, []).append((noentry_name, list(meta.noentry_after)))

    for base_name, overrides in pair_overrides.items():
        by_after: dict[tuple, list[tuple[str, list[str]]]] = {}
        for variant_name, after in overrides:
            key = tuple(sorted(after))
            by_after.setdefault(key, []).append((variant_name, after))
        deferred_pair_exit_variants: set[str] = set()
        subgroups: list[list[tuple[str, list[str]]]] = []
        for group in by_after.values():
            # A left-context group can hold two sets of stances, each with its own entry-only and entry-and-exit stance (·Out's bare `en-trim` and its after-·See `en-trim`, which both fire after a contracted ·See). Pair the upgrade within each set, so a bare entry-only stance isn't upgraded to a stance meant for one predecessor. The sets are split by their `after-…` modifiers. A set with only entry-only or only entry-and-exit members (·Excite's `noexit` against `before-vertical.after-baseline-letter`) goes into one residual group, where its members pair with each other.
            by_after_modifiers: dict[tuple, list[tuple[str, list[str]]]] = {}
            for variant_name, after in group:
                after_sig = tuple(sorted(m for m in _meta(variant_name).modifiers if m.startswith("after-")))
                by_after_modifiers.setdefault(after_sig, []).append((variant_name, after))
            residual: list[tuple[str, list[str]]] = []
            for members in by_after_modifiers.values():
                has_with = any(_meta(name).exit for name, _ in members)
                has_without = any(not _meta(name).exit for name, _ in members)
                if has_with and has_without:
                    subgroups.append(members)
                else:
                    residual.extend(members)
            if residual:
                subgroups.append(residual)
        for group in subgroups:
            with_exit = []
            without_exit = []
            for variant_name, after in group:
                variant_meta = _meta(variant_name)
                if variant_meta.exit:
                    with_exit.append((variant_name, variant_meta))
                else:
                    without_exit.append((variant_name, variant_meta))
            if with_exit and without_exit:
                entry_only_var = without_exit[0][0]
                entry_exit_var, entry_exit_meta = next(
                    (
                        (variant_name, variant_meta)
                        for variant_name, variant_meta in with_exit
                        if not variant_meta.before
                    ),
                    with_exit[0],
                )
                exit_y = entry_exit_meta.exit[0][1]
                nb = list(entry_exit_meta.not_before)
                fwd_upgrades.setdefault(base_name, []).append(
                    (entry_exit_var, entry_only_var, exit_y, list(nb))
                )
                if not entry_exit_meta.before:
                    deferred_pair_exit_variants.add(entry_exit_var)
        if deferred_pair_exit_variants:
            pair_overrides[base_name] = [
                (variant_name, after)
                for variant_name, after in overrides
                if variant_name not in deferred_pair_exit_variants
            ]

    fwd_replacements = plan.fwd_replacements
    fwd_exclusions = plan.fwd_exclusions
    fwd_pair_overrides = plan.fwd_pair_overrides
    for glyph_name, meta in glyph_meta.items():
        if not meta.modifiers:
            continue
        if meta.is_noentry:
            continue
        if meta.extended_entry_suffix is not None:
            continue
        if meta.extended_exit_suffix is not None and not meta.before and not meta.gated_before:
            continue
        if meta.contracted_entry_suffix is not None and not meta.after:
            continue
        if meta.contracted_exit_suffix is not None and not meta.before and not meta.gated_before:
            continue
        if meta.is_entry_variant and not meta.before:
            continue
        if meta.word_final:
            continue
        if meta.after:
            continue
        if "half" in meta.traits and meta.entry and not meta.before:
            continue
        extra_parts = meta.modifier_set - {"alt", "prop"} - _SYNTHESIZED_MODIFIER_TOKENS
        if extra_parts and "alt" in meta.traits and meta.entry and not meta.before:
            continue
        if not meta.exit and not (meta.before or meta.gated_before):
            continue
        exit_y = meta.exit[0][1] if meta.exit else None
        base_name = meta.base_name
        if base_name not in glyph_meta:
            continue
        calt_before = meta.before
        gated_before = meta.gated_before
        if calt_before:
            resolved = resolve_known_glyph_names(calt_before, plan.glyph_names)
            not_after = meta.not_after
            resolved_not_after = resolve_known_glyph_names(not_after, plan.glyph_names) if not_after else []
            if meta.gate_feature:
                plan.gated_fwd_pair_overrides.setdefault(base_name, []).append(
                    (glyph_name, resolved, resolved_not_after, meta.gate_feature)
                )
            else:
                fwd_pair_overrides.setdefault(base_name, []).append(
                    (glyph_name, resolved, resolved_not_after)
                )
        if gated_before:
            not_after = meta.not_after
            resolved_not_after = resolve_known_glyph_names(not_after, plan.glyph_names) if not_after else []
            for feature_tag, families in gated_before:
                resolved_gated = resolve_known_glyph_names(list(families), plan.glyph_names)
                plan.gated_fwd_pair_overrides.setdefault(base_name, []).append(
                    (glyph_name, resolved_gated, resolved_not_after, feature_tag)
                )
        if exit_y is None:
            continue
        if not calt_before and not gated_before:
            fwd_replacements.setdefault(base_name, {})[exit_y] = glyph_name
            not_before = meta.not_before
            if not_before:
                resolved = resolve_known_glyph_names(not_before, plan.glyph_names)
                fwd_exclusions.setdefault(base_name, {})[exit_y] = resolved
            not_after = meta.not_after
            if not_after:
                resolved_bk = resolve_known_glyph_names(not_after, plan.glyph_names)
                plan.fwd_bk_exclusions.setdefault(base_name, {})[exit_y] = resolved_bk
        elif meta.not_before and meta.extended_exit_suffix is None and meta.contracted_exit_suffix is None:
            fwd_replacements.setdefault(base_name, {})[exit_y] = glyph_name
            resolved = resolve_known_glyph_names(meta.not_before, plan.glyph_names)
            fwd_exclusions.setdefault(base_name, {})[exit_y] = resolved
            not_after = meta.not_after
            if not_after:
                resolved_bk = resolve_known_glyph_names(not_after, plan.glyph_names)
                plan.fwd_bk_exclusions.setdefault(base_name, {})[exit_y] = resolved_bk

    reverse_only_upgrades = plan.reverse_only_upgrades
    for glyph_name, meta in glyph_meta.items():
        reverse_from = meta.reverse_upgrade_from
        if not reverse_from:
            continue
        entries = list(meta.entry)
        exits = list(meta.exit)
        if not entries or not exits:
            continue
        exit_ys = {anchor[1] for anchor in exits}
        resolved_sources = resolve_known_glyph_names(reverse_from, plan.glyph_names)
        matching_sources = []
        for source_name in resolved_sources:
            source_exits = list(_meta(source_name).exit)
            if source_exits and exit_ys & {anchor[1] for anchor in source_exits}:
                matching_sources.append(source_name)
        if matching_sources:
            reverse_only_upgrades.append(
                (
                    glyph_name,
                    matching_sources,
                    [anchor[1] for anchor in entries],
                    list(meta.after),
                    list(meta.not_before),
                )
            )

    _base_anchors: dict[str, list[tuple[str, set[int], set[int]]]] = {}
    for glyph_name, meta in glyph_meta.items():
        if meta.is_noentry:
            continue
        entry_ys = set(meta.entry_ys)
        exit_ys = set(meta.exit_ys)
        _base_anchors.setdefault(meta.base_name, []).append((glyph_name, entry_ys, exit_ys))

    terminal_entry_only = plan.terminal_entry_only
    terminal_exit_only = plan.terminal_exit_only
    for base_name, siblings in _base_anchors.items():
        for glyph_name, entry_ys, exit_ys in siblings:
            if entry_ys and not exit_ys:
                for y in entry_ys:
                    if not any(
                        sibling_name != glyph_name and y in sibling_entries and sibling_exits
                        for sibling_name, sibling_entries, sibling_exits in siblings
                    ):
                        terminal_entry_only.add(glyph_name)
                        break
            if exit_ys and not entry_ys:
                for y in exit_ys:
                    if not any(
                        sibling_name != glyph_name and y in sibling_exits and sibling_entries
                        for sibling_name, sibling_entries, sibling_exits in siblings
                    ):
                        terminal_exit_only.add(glyph_name)
                        break

    exit_classes = plan.exit_classes
    for glyph_name, meta in glyph_meta.items():
        if not meta.exit:
            continue
        for anchor in meta.exit:
            exit_classes.setdefault(anchor[1], set()).add(glyph_name)

    entry_classes = plan.entry_classes
    for glyph_name, meta in glyph_meta.items():
        if not meta.entry:
            continue
        for anchor in meta.entry:
            entry_classes.setdefault(anchor[1], set()).add(glyph_name)
            is_bk = meta.is_entry_variant
            if not is_bk:
                is_bk = ("half" in meta.traits or "alt" in meta.traits) and bool(meta.entry)
            if is_bk:
                base_name = meta.base_name
                if base_name in glyph_meta and anchor[1] in bk_replacements.get(base_name, {}):
                    entry_classes[anchor[1]].add(base_name)

    for base_name in fwd_upgrades:
        for _, entry_only_var, exit_y, _ in fwd_upgrades[base_name]:
            entry_meta = _meta(entry_only_var)
            if not entry_meta.entry:
                continue
            entry_y_val = entry_meta.entry[0][1]
            exit_only_var = fwd_replacements.get(base_name, {}).get(exit_y)
            if exit_only_var and entry_y_val in entry_classes:
                entry_classes[entry_y_val].add(exit_only_var)

    for base_name, fwd_vars in fwd_replacements.items():
        base_entry_ys = {y for y, members in entry_classes.items() if base_name in members}
        if not base_entry_ys:
            continue
        for _, fwd_var in fwd_vars.items():
            fwd_meta = _meta(fwd_var)
            if fwd_meta.entry:
                continue
            for y in base_entry_ys:
                entry_classes[y].add(fwd_var)

    entry_exclusive = plan.entry_exclusive
    all_entry_ys = set(entry_classes.keys())
    for y in all_entry_ys:
        exclusive = set(entry_classes[y])
        for other_y in all_entry_ys:
            if other_y != y:
                exclusive -= entry_classes[other_y]
        entry_exclusive[y] = exclusive

    fwd_use_exclusive = plan.fwd_use_exclusive
    for base_name in fwd_replacements:
        if base_name in bk_replacements:
            bk_variant_names = set(bk_replacements[base_name].values())
            for exit_y, variant_name in fwd_replacements[base_name].items():
                if variant_name in bk_variant_names:
                    fwd_use_exclusive.add((base_name, exit_y))
        base_meta = glyph_meta.get(base_name)
        if base_meta and base_meta.exit:
            known_exits = set(base_meta.exit_ys)
            min_base_exit = min(known_exits)
            for exit_y in fwd_replacements[base_name]:
                if exit_y not in known_exits and exit_y < min_base_exit:
                    fwd_use_exclusive.add((base_name, exit_y))

    fwd_preferred_lookahead = plan.fwd_preferred_lookahead
    for base_name in fwd_replacements:
        for exit_y, variant_name in fwd_replacements[base_name].items():
            if (base_name, exit_y) not in fwd_use_exclusive:
                continue
            variant_meta = _meta(variant_name)
            preferred_over = variant_meta.preferred_over
            if not preferred_over:
                continue
            base_meta = glyph_meta.get(base_name)
            if base_meta and base_meta.exit:
                sibling_exit_y = base_meta.exit[0][1]
            else:
                for sibling in preferred_over:
                    sibling_meta = glyph_meta.get(sibling)
                    if sibling_meta and sibling_meta.exit:
                        sibling_exit_y = sibling_meta.exit[0][1]
                        break
                else:
                    continue
            if sibling_exit_y != exit_y:
                fwd_preferred_lookahead.setdefault(base_name, []).append(
                    (variant_name, exit_y, sibling_exit_y)
                )

    preferred_lookahead_bridges = plan.preferred_lookahead_bridges
    for entries in fwd_preferred_lookahead.values():
        for _variant_name, exit_y, sibling_y in entries:
            key = (exit_y, sibling_y)
            if key in preferred_lookahead_bridges:
                continue
            bridge_members: set[str] = set()
            for candidate in entry_classes.get(exit_y, ()):
                candidate_meta = glyph_meta.get(candidate)
                candidate_base = candidate_meta.base_name if candidate_meta else candidate
                for variant_name in plan.base_to_variants.get(candidate_base, ()):
                    variant_meta = glyph_meta.get(variant_name)
                    if variant_meta is None:
                        continue
                    if exit_y in variant_meta.entry_ys and sibling_y in variant_meta.exit_ys:
                        bridge_members.add(candidate)
                        break
            preferred_lookahead_bridges[key] = bridge_members

    base_exit_ys: dict[str, set[int]] = {}
    for base_name in bk_replacements:
        base_ys = set()
        base_meta = glyph_meta.get(base_name)
        if base_meta:
            base_ys.update(base_meta.exit_ys)
        new_exit_ys = set()
        all_variants = list(bk_replacements[base_name].values())
        if base_name in fwd_replacements:
            all_variants.extend(fwd_replacements[base_name].values())
        for variant_name in all_variants:
            variant_meta = glyph_meta.get(variant_name)
            if variant_meta:
                for exit_y in variant_meta.exit_ys:
                    if exit_y not in base_ys:
                        new_exit_ys.add(exit_y)
        base_exit_ys[base_name] = new_exit_ys

    base_order = list(bk_replacements.keys())
    edges: dict[str, set[str]] = {base: set() for base in base_order}
    for base_a in base_order:
        for base_b in base_order:
            if base_a == base_b:
                continue
            b_entry_ys = set(bk_replacements[base_b].keys())
            if base_exit_ys[base_a] & b_entry_ys:
                edges[base_b].add(base_a)

    out_edges: dict[str, set[str]] = {base: set() for base in base_order}
    in_degree: dict[str, int] = {base: len(edges[base]) for base in base_order}
    for base in base_order:
        for dependency in edges[base]:
            out_edges[dependency].add(base)

    queue = deque(sorted(base for base in base_order if in_degree[base] == 0))
    sorted_bases: list[str] = []
    while queue:
        node = queue.popleft()
        sorted_bases.append(node)
        for neighbor in sorted(out_edges[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    cycle_bases: set[str] = set(base_order) - set(sorted_bases)
    sorted_bases.extend(sorted(cycle_bases))

    entry_ext_pair_only: set[str] = set()
    for base_name, overrides in pair_overrides.items():
        if all(_meta(variant_name).extended_entry_suffix is not None for variant_name, _ in overrides):
            entry_ext_pair_only.add(base_name)

    all_fwd_bases = set(fwd_replacements) | set(fwd_pair_overrides) | set(plan.gated_fwd_pair_overrides)
    pair_override_bases = set(pair_overrides)
    entry_ext_fwd_only = entry_ext_pair_only & all_fwd_bases

    dependent_pair_fwd_general: dict[str, set[int]] = {}
    pair_only_fwd_candidates = (pair_override_bases - set(bk_replacements) - entry_ext_fwd_only) & set(
        fwd_replacements
    )
    for base_name in pair_only_fwd_candidates:
        base_meta = glyph_meta.get(base_name)
        if base_meta is None:
            continue
        current_exit_ys = set(base_meta.exit_ys)
        shadowed_exit_ys: set[int] = set()
        for variant_name, _, _ in fwd_pair_overrides.get(base_name, []):
            shadowed_exit_ys.update(_meta(variant_name).exit_ys)
        for variant_name, _, _, _ in plan.gated_fwd_pair_overrides.get(base_name, []):
            shadowed_exit_ys.update(_meta(variant_name).exit_ys)
        safe_exit_ys: set[int] = set()
        for exit_y in fwd_replacements.get(base_name, {}):
            if exit_y in current_exit_ys or exit_y in shadowed_exit_ys:
                continue
            # Emit only the plain forward-exit Ys that another base's backward substitutions cannot see until this base has changed.
            if any(
                other_base != base_name and exit_y in set(entry_variants)
                for other_base, entry_variants in bk_replacements.items()
            ):
                safe_exit_ys.add(exit_y)
        if safe_exit_ys:
            dependent_pair_fwd_general[base_name] = safe_exit_ys

    pair_only = sorted(pair_override_bases - set(bk_replacements) - entry_ext_fwd_only)
    all_bk_bases = sorted_bases + pair_only

    fwd_only_set = all_fwd_bases - set(bk_replacements) - (pair_override_bases - entry_ext_pair_only)
    early_fwd_set = fwd_only_set | set(dependent_pair_fwd_general)

    fwd_fwd_edges: dict[str, set[str]] = {base: set() for base in early_fwd_set}
    for base_a in early_fwd_set:
        for exit_y in fwd_replacements.get(base_a, {}):
            use_excl = (base_a, exit_y) in fwd_use_exclusive
            if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                continue
            cls = entry_exclusive[exit_y] if use_excl else entry_classes.get(exit_y, set())
            for base_b in early_fwd_set:
                if base_b == base_a or base_b not in cls:
                    continue
                for b_variant in fwd_replacements.get(base_b, {}).values():
                    if b_variant not in cls:
                        fwd_fwd_edges[base_a].add(base_b)
                        break

    for base_a in early_fwd_set:
        if base_a not in pair_overrides:
            continue
        for _, after_glyphs in pair_overrides[base_a]:
            for after_glyph in after_glyphs:
                base_b = glyph_meta[after_glyph].base_name
                if base_b != base_a and base_b in early_fwd_set:
                    fwd_fwd_edges[base_a].add(base_b)

    fwd_out: dict[str, set[str]] = {base: set() for base in early_fwd_set}
    fwd_in_deg: dict[str, int] = {base: len(fwd_fwd_edges[base]) for base in early_fwd_set}
    for base in early_fwd_set:
        for dependency in fwd_fwd_edges[base]:
            fwd_out[dependency].add(base)

    fwd_queue = deque(sorted(base for base in early_fwd_set if fwd_in_deg[base] == 0))
    fwd_early: list[str] = []
    while fwd_queue:
        node = fwd_queue.popleft()
        fwd_early.append(node)
        for neighbor in sorted(fwd_out[node]):
            fwd_in_deg[neighbor] -= 1
            if fwd_in_deg[neighbor] == 0:
                fwd_queue.append(neighbor)

    fwd_early.extend(sorted(early_fwd_set - set(fwd_early)))
    fwd_only = [base for base in fwd_early if base in fwd_only_set]
    early_pair_fwd_general = [base for base in fwd_early if base in dependent_pair_fwd_general]

    lig_fwd_bases: set[str] = set()
    for base_name in fwd_only:
        base_meta = glyph_meta.get(base_name)
        if (
            base_meta
            and base_meta.sequence
            and all(component in glyph_meta for component in base_meta.sequence)
        ):
            lig_fwd_bases.add(base_name)

    early_pair_upgrade_bases: set[str] = set()
    for base_name in pair_only:
        if base_name not in fwd_upgrades or base_name not in all_fwd_bases:
            continue
        pair_var_names = {variant_name for variant_name, _ in pair_overrides.get(base_name, [])}
        if any(entry_only in pair_var_names for _, entry_only, _, _ in fwd_upgrades[base_name]):
            early_pair_upgrade_bases.add(base_name)

    early_fwd_pairs: set[str] = set()
    for base_name, overrides in fwd_pair_overrides.items():
        found = False
        for variant_name, before_glyphs, _ in overrides:
            variant_meta = _meta(variant_name)
            if variant_meta.extended_exit_suffix is not None:
                continue
            if base_name in {glyph_meta[glyph].base_name for glyph in before_glyphs}:
                early_fwd_pairs.add(base_name)
                found = True
                break
            if variant_meta.exit:
                exit_ys = set(variant_meta.exit_ys)
                for before_glyph in before_glyphs:
                    before_base = glyph_meta[before_glyph].base_name
                    bk_ys = set(bk_replacements.get(before_base, {}))
                    for pair_variant, _ in pair_overrides.get(before_base, []):
                        bk_ys.update(_meta(pair_variant).entry_ys)
                    if exit_ys & bk_ys:
                        early_fwd_pairs.add(base_name)
                        found = True
                        break
            if found:
                break

    word_final_pairs = {}
    for glyph_name, meta in glyph_meta.items():
        if meta.word_final:
            base_name = meta.base_name
            if base_name in glyph_meta:
                word_final_pairs[base_name] = glyph_name

    ligatures = []
    for glyph_name in glyph_meta:
        meta = _meta(glyph_name)
        if not meta.sequence:
            continue
        if glyph_name != meta.base_name:
            continue
        if meta.is_noentry or meta.extended_entry_suffix is not None:
            continue
        if meta.extended_exit_suffix is not None:
            continue
        if all(component in glyph_meta for component in meta.sequence):
            ligatures.append((glyph_name, meta.sequence))

    plan.sorted_bases = sorted_bases
    plan.cycle_bases = cycle_bases
    plan.edges = edges
    plan.pair_only = pair_only
    plan.all_bk_bases = all_bk_bases
    plan.all_fwd_bases = all_fwd_bases
    plan.fwd_only = fwd_only
    plan.early_pair_fwd_general = early_pair_fwd_general
    plan.early_pair_fwd_general_exit_ys = {
        base_name: dependent_pair_fwd_general[base_name] for base_name in early_pair_fwd_general
    }
    plan.lig_fwd_bases = lig_fwd_bases
    plan.early_pair_upgrade_bases = early_pair_upgrade_bases
    plan.early_fwd_pairs = early_fwd_pairs
    plan.word_final_pairs = word_final_pairs
    plan.ligatures = ligatures

    _populate_exit_reachability(plan)

    return plan


def _populate_exit_reachability(plan: _JoinAnalysis) -> None:
    glyph_meta = plan.glyph_meta
    glyph_names = plan.glyph_names
    base_to_variants = plan.base_to_variants

    generation_children: dict[str, list[str]] = defaultdict(list)
    for glyph_name, meta in glyph_meta.items():
        if meta.generated_from:
            generation_children[meta.generated_from].append(glyph_name)

    def _meta(name: str) -> JoinGlyph:
        return glyph_meta[name]

    def _base_name(name: str) -> str:
        if name in glyph_meta:
            return _meta(name).base_name
        return name

    def _selector_bases(selectors: list[str] | tuple[str, ...]) -> set[str]:
        return {_base_name(selector) for selector in selectors}

    def _expand_all_variants(glyphs, *, include_base=False) -> set[str]:
        return _expand_join_variants(glyphs, plan, include_base=include_base)

    def _context_bases_for_entry_y(
        base_name: str,
        entry_y: int,
        excluded_glyphs: set[str],
    ) -> set[str]:
        use_exclusive = (base_name, entry_y) in plan.fwd_use_exclusive
        if use_exclusive and (entry_y not in plan.entry_exclusive or not plan.entry_exclusive[entry_y]):
            return set()
        members = plan.entry_exclusive[entry_y] if use_exclusive else plan.entry_classes.get(entry_y, set())
        excluded_bases = {_base_name(name) for name in excluded_glyphs}
        return {_base_name(name) for name in members} - excluded_bases

    def _add_exit_path(
        source_name: str,
        exit_y: int,
        *,
        before_bases: set[str] | None = None,
        feature_tag: str | None = None,
    ) -> None:
        if source_name not in glyph_meta:
            return
        if before_bases is None:
            if feature_tag is None:
                plan.exit_reachability.setdefault(source_name, set()).add(exit_y)
            else:
                plan.gated_exit_reachability.setdefault(
                    (feature_tag, source_name),
                    set(),
                ).add(exit_y)
            return

        for before_base in before_bases:
            if feature_tag is None:
                plan.exit_reachability_before.setdefault(
                    (source_name, before_base),
                    set(),
                ).add(exit_y)
            else:
                plan.gated_exit_reachability_before.setdefault(
                    (feature_tag, source_name, before_base),
                    set(),
                ).add(exit_y)

    def _add_replacement_path(
        source_name: str,
        replacement_name: str | None,
        *,
        before_bases: set[str] | None = None,
        feature_tag: str | None = None,
    ) -> None:
        if replacement_name is None or replacement_name not in glyph_meta:
            return
        for exit_y in _meta(replacement_name).exit_ys:
            _add_exit_path(
                source_name,
                exit_y,
                before_bases=before_bases,
                feature_tag=feature_tag,
            )

    def _entry_bearing_strip_targets(
        base_name: str,
        exit_y: int,
        replacement_name: str,
    ) -> list[str]:
        replacement_meta = _meta(replacement_name)
        if not replacement_meta.strip_entry_before:
            return []
        if _has_left_entry(replacement_meta):
            return []

        targets = []
        for target_name in sorted(base_to_variants.get(base_name, ())):
            target_meta = _meta(target_name)
            if target_name == base_name:
                continue
            if target_meta.is_noentry:
                continue
            if target_meta.gate_feature:
                continue
            if not _has_left_entry(target_meta):
                continue
            if exit_y in set(target_meta.exit_ys):
                continue
            targets.append(target_name)
        return targets

    def _fwd_pair_targets(base_name: str) -> set[str]:
        targets = {base_name}
        if base_name in plan.bk_replacements:
            targets.update(plan.bk_replacements[base_name].values())
        if base_name in plan.fwd_replacements:
            targets.update(plan.fwd_replacements[base_name].values())
        if base_name in plan.pair_overrides:
            targets.update(variant_name for variant_name, _ in plan.pair_overrides[base_name])
        if base_name in plan.fwd_upgrades:
            targets.update(entry_exit_var for entry_exit_var, _, _, _ in plan.fwd_upgrades[base_name])
        noentry_name = f"{base_name}.noentry"
        if noentry_name in glyph_names:
            targets.add(noentry_name)
        return targets

    def _add_orthogonal_derivations(
        targets: set[str],
        variant_name: str,
    ) -> set[str]:
        variant_meta = _meta(variant_name)
        variant_is_exit_side = bool(variant_meta.extended_exit_suffix or variant_meta.contracted_exit_suffix)
        variant_is_entry_side = bool(
            variant_meta.extended_entry_suffix or variant_meta.contracted_entry_suffix
        )
        if variant_is_exit_side == variant_is_entry_side:
            return set()

        if variant_is_exit_side:
            orthogonal_kinds = {
                "en-ext-1",
                "en-con-1",
                "entry-trimmed",
            }
        else:
            orthogonal_kinds = {
                "ex-ext-1",
                "ex-con-1",
                "exit-trimmed",
            }

        orthogonal_derivations: set[str] = set()
        derivation_queue = deque(targets)
        while derivation_queue:
            parent = derivation_queue.popleft()
            for child in generation_children.get(parent, ()):
                if child in orthogonal_derivations:
                    continue
                child_meta = glyph_meta.get(child)
                if child_meta is None:
                    continue
                if child_meta.transform_kind not in orthogonal_kinds:
                    continue
                orthogonal_derivations.add(child)
                derivation_queue.append(child)
        targets.update(orthogonal_derivations)
        return orthogonal_derivations

    def _actual_fwd_pair_replacement(
        variant_name: str,
        target_name: str,
    ) -> str | None:
        target_meta = _meta(target_name)
        actual_variant = variant_name
        suffix = target_meta.extended_entry_suffix
        if suffix:
            extended = variant_name + suffix
            if extended not in glyph_names:
                extended = variant_name + ".en-ext-1"
            if extended in glyph_names:
                actual_variant = extended
        return _resolve_noentry_replacement(
            glyph_meta,
            base_to_variants,
            target_name,
            actual_variant,
        )

    def _fwd_pair_target_emits(
        variant_name: str,
        target_name: str,
        expanded_before: set[str],
        orthogonal_derivations: set[str],
    ) -> bool:
        variant_meta = _meta(variant_name)
        target_meta = _meta(target_name)
        target_has_entry = bool(target_meta.entry)

        if target_meta.is_entry_variant and target_meta.exit and variant_meta.exit:
            target_exit_ys = set(target_meta.exit_ys)
            before_entry_ys: set[int] = set()
            for before_glyph in expanded_before:
                before_meta = glyph_meta.get(before_glyph)
                if before_meta and before_meta.entry:
                    before_entry_ys.update(before_meta.entry_ys)
            if before_entry_ys and not (target_exit_ys & before_entry_ys):
                return False

        variant_entry_ys = set(variant_meta.entry_ys) if variant_meta.entry else None
        if variant_entry_ys is not None:
            if target_has_entry:
                target_entry_ys = set(target_meta.entry_ys)
                if not target_entry_ys.issubset(variant_entry_ys):
                    incompatible_ys = target_entry_ys - variant_entry_ys
                    if incompatible_ys == target_entry_ys:
                        return False
            return True

        if target_has_entry and target_meta.is_entry_variant:
            if target_meta.exit:
                target_exit_ys = set(target_meta.exit_ys)
                variant_exit_ys = set(variant_meta.exit_ys)
                if variant_exit_ys <= target_exit_ys:
                    return False
            elif target_meta.after and target_name not in orthogonal_derivations:
                return False

        return True

    for base_name, variants in plan.fwd_replacements.items():
        for exit_y, variant_name in variants.items():
            excluded = set(
                _expand_all_variants(
                    plan.fwd_exclusions.get(base_name, {}).get(exit_y, []),
                    include_base=True,
                )
            )
            before_bases = _context_bases_for_entry_y(base_name, exit_y, excluded)
            if not excluded:
                _add_replacement_path(base_name, variant_name)
            _add_replacement_path(base_name, variant_name, before_bases=before_bases)

            noentry_name = f"{base_name}.noentry"
            if noentry_name in glyph_names:
                actual_variant = _resolve_noentry_replacement(
                    glyph_meta,
                    base_to_variants,
                    noentry_name,
                    variant_name,
                )
                if not excluded:
                    _add_replacement_path(noentry_name, actual_variant)
                _add_replacement_path(
                    noentry_name,
                    actual_variant,
                    before_bases=before_bases,
                )

            for target_name in _entry_bearing_strip_targets(
                base_name,
                exit_y,
                variant_name,
            ):
                if not excluded:
                    _add_replacement_path(target_name, variant_name)
                _add_replacement_path(
                    target_name,
                    variant_name,
                    before_bases=before_bases,
                )

    for base_name, upgrades in plan.fwd_upgrades.items():
        for entry_exit_var, entry_only_var, exit_y, not_before in upgrades:
            excluded = set(_expand_all_variants(not_before, include_base=True))
            before_bases = _context_bases_for_entry_y(base_name, exit_y, excluded)
            if not excluded:
                _add_replacement_path(entry_only_var, entry_exit_var)
            _add_replacement_path(
                entry_only_var,
                entry_exit_var,
                before_bases=before_bases,
            )

    # Record the paths `_emit_noentry_fwd_overrides` emits: an entry-only backward replacement (`qsTea.en-y0`) can change to an exit-only forward replacement of its base (`qsTea.ex-y0` or `qsTea.half.ex-y5`, depending on the follower). Without them, `_can_eventually_exit_at(qsTea.en-y0, 0, before_base=qsDay)` is False, `qsTea.en-y0` drops out of the backtrack class of ·Day's extended-entry rule, and `qsBay qsTea qsDay` joins ·Tea·Day at y=5 while `qsTea qsDay` alone joins at y=0. The override also fires on the entry-only variant's `.en-ext-N` stances, so they get the same paths.
    for base_name, bk_variants in plan.bk_replacements.items():
        if base_name not in plan.fwd_replacements:
            continue
        for _entry_y, bk_var in bk_variants.items():
            if _meta(bk_var).exit:
                continue
            for fwd_exit_y, fwd_var in plan.fwd_replacements[base_name].items():
                if fwd_exit_y not in plan.entry_classes:
                    continue
                if _meta(fwd_var).entry:
                    continue
                has_upgrade = any(
                    entry_only == bk_var and ey == fwd_exit_y
                    for _, entry_only, ey, _ in plan.fwd_upgrades.get(base_name, [])
                )
                if has_upgrade:
                    continue
                excluded = set(
                    _expand_all_variants(
                        plan.fwd_exclusions.get(base_name, {}).get(fwd_exit_y, []),
                        include_base=True,
                    )
                )
                before_bases = _context_bases_for_entry_y(
                    base_name,
                    fwd_exit_y,
                    excluded,
                )
                source_variants = [bk_var]
                for ext_suffix in _ENTRY_EXTENSION_SUFFIXES:
                    ext_bk = f"{bk_var}{ext_suffix}"
                    if ext_bk not in glyph_meta:
                        continue
                    if _meta(ext_bk).exit:
                        continue
                    source_variants.append(ext_bk)
                for src in source_variants:
                    if not excluded:
                        _add_replacement_path(src, fwd_var)
                    _add_replacement_path(src, fwd_var, before_bases=before_bases)

    def _record_fwd_pair_reachability(
        overrides: dict[str, list[tuple[str, list[str], list[str]]]],
        *,
        feature_tag: str | None = None,
    ) -> None:
        for base_name, entries in overrides.items():
            for variant_name, before_glyphs, _not_after_glyphs in entries:
                before_bases = _selector_bases(before_glyphs)
                if not before_bases:
                    continue
                expanded_before = _expand_all_variants(before_glyphs)
                targets = _fwd_pair_targets(base_name)
                orthogonal_derivations = _add_orthogonal_derivations(
                    targets,
                    variant_name,
                )
                for target_name in sorted(targets):
                    if target_name not in glyph_meta:
                        continue
                    if not _fwd_pair_target_emits(
                        variant_name,
                        target_name,
                        expanded_before,
                        orthogonal_derivations,
                    ):
                        continue
                    actual_variant = _actual_fwd_pair_replacement(
                        variant_name,
                        target_name,
                    )
                    _add_replacement_path(
                        target_name,
                        actual_variant,
                        before_bases=before_bases,
                        feature_tag=feature_tag,
                    )

    _record_fwd_pair_reachability(plan.fwd_pair_overrides)
    for feature_tag, grouped in _group_gated_fwd_pair_overrides(plan.gated_fwd_pair_overrides).items():
        _record_fwd_pair_reachability(grouped, feature_tag=feature_tag)


def _group_gated_fwd_pair_overrides(
    overrides: dict[str, list[tuple[str, list[str], list[str], str]]],
) -> dict[str, dict[str, list[tuple[str, list[str], list[str]]]]]:
    grouped: dict[str, dict[str, list[tuple[str, list[str], list[str]]]]] = {}
    for base_name, entries in overrides.items():
        for variant_name, before_glyphs, not_after_glyphs, feature_tag in entries:
            grouped.setdefault(feature_tag, {}).setdefault(base_name, []).append(
                (variant_name, before_glyphs, not_after_glyphs)
            )
    return grouped


def _can_eventually_exit_at(
    plan: _JoinAnalysis,
    name: str,
    y: int,
    *,
    before_base: str | None = None,
    feature_tag: str | None = None,
) -> bool:
    meta = plan.glyph_meta[name]
    if y in meta.exit_ys:
        return True
    if y in plan.exit_reachability.get(name, set()):
        return True
    if before_base is not None and y in plan.exit_reachability_before.get(
        (name, before_base),
        set(),
    ):
        return True
    if feature_tag is not None:
        if y in plan.gated_exit_reachability.get((feature_tag, name), set()):
            return True
        if before_base is not None and y in plan.gated_exit_reachability_before.get(
            (feature_tag, name, before_base),
            set(),
        ):
            return True
    return False


_CONTRACT_EMIT_DUMP_PATH = Path(__file__).resolve().parent.parent / "tmp" / "leak-contract-emit.txt"

# The number of cross-break selections the derived join contract drops from the production font. Dropping them is intended, so `_JoinContractRecorder.flush` warns only when the count differs from this. Update it when an intended change moves the count; `tmp/leak-contract-emit.txt` lists every dropped selection.
_EXPECTED_CONTRACT_DROP_COUNT = 717

# The expected count applies only to the production font. Unit tests run the emitter over small glyph sets that drop other counts, so the check runs only when the glyph set contains all of these letters.
_BASELINE_REPERTOIRE_SENTINELS = frozenset({"qsPea", "qsHe", "qsOoze"})


@dataclass
class _JoinContractRecorder:
    """Classifies and filters the neighbors of each contextual calt rule under the derived join contract (doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md).

    `_emit_quikscript_calt` installs one as `_active_contract_recorder` for its run. Each neighbor that `_select_rule_neighbors` sees gets one verdict per `(variant, neighbor, direction)` triple:

    - `joining`: the selected variant `V` joins the neighbor `N`: `exit_ys(V) & entry_ys(N)` (forward) or `exit_ys(N) & entry_ys(V)` (backward) is non-empty, using the Ys `N` can reach in context. A `V` with no exit (forward) or no entry (backward) also counts as joining.
    - `cosmetic`: `V` has a `before-<fam>` (forward) or `after-<fam>` (backward) modifier whose trigger list names `N`'s family, so the author declared this shape change and the rule keeps it.
    - `leak`: neither of those. `keep` drops these neighbors from the rule.

    `flush` writes every verdict to `tmp/leak-contract-emit.txt` and warns when the leak count differs from `_EXPECTED_CONTRACT_DROP_COUNT`. `tools/leak_contract_report.py` applies the same predicate to the built FEA with bare anchor Ys only, so the two can disagree.
    """

    glyph_meta: dict[str, JoinGlyph]
    # The plan's maps let the contract judge a neighbor by the entry and exit Ys it can reach in context (its upgrade stances, pair overrides from the source family, and the ligatures it leads or trails), besides its own anchors. Bare `qsIt` has no entry, but in context it is upgraded to `qsIt.en-y0.ex-y5`, so without these maps the contract would drop the `qsShe.ex-y0` to `qsIt` join.
    bk_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    fwd_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    base_to_variants: dict[str, set[str]] = field(default_factory=dict)
    reverse_only_upgrades: list[tuple[str, list[str], list[int], list[str], list[str]]] = field(
        default_factory=list
    )
    verdicts: dict[tuple[str, str, str], str] = field(default_factory=dict)
    pivots: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)
    _entry_cache: dict[tuple[str, str], frozenset[int]] = field(default_factory=dict)
    _exit_cache: dict[tuple[str, str], frozenset[int]] = field(default_factory=dict)
    _leads: dict[str, set[str]] = field(default_factory=dict)
    _trails: dict[str, set[str]] = field(default_factory=dict)
    _reverse_upgrades_from: dict[str, list[tuple[str, list[str]]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for upgrade_name, sources, _entry_ys, after, _not_before in self.reverse_only_upgrades:
            for source in sources:
                self._reverse_upgrades_from.setdefault(source, []).append((upgrade_name, after))
        # base -> ligature variant names that lead (sequence[0]) / trail (sequence[-1]) with that base.
        for name, meta in self.glyph_meta.items():
            seq = meta.sequence
            if not seq or len(seq) < 2 or name != meta.base_name:
                continue
            lead_base = self.glyph_meta[seq[0]].base_name if seq[0] in self.glyph_meta else seq[0]
            trail_base = self.glyph_meta[seq[-1]].base_name if seq[-1] in self.glyph_meta else seq[-1]
            for variant in self.base_to_variants.get(name, {name}):
                self._leads.setdefault(lead_base, set()).add(variant)
                self._trails.setdefault(trail_base, set()).add(variant)

    def observe(self, base_name: str, variant_name: str, candidate_members: set[str], direction: str) -> None:
        for neighbor in candidate_members:
            key = (variant_name, neighbor, direction)
            self.pivots.setdefault(key, set()).add(base_name)
            if key not in self.verdicts:
                self.verdicts[key] = self._classify(variant_name, neighbor, direction)

    def keep(self, variant_name: str, candidate_members: set[str], direction: str) -> set[str]:
        """Return `candidate_members` without the neighbors classified as `leak`. A neighbor `observe` has not classified is kept."""
        return {
            neighbor
            for neighbor in candidate_members
            if self.verdicts.get((variant_name, neighbor, direction)) != "leak"
        }

    def _base_name(self, glyph: str) -> str:
        meta = self.glyph_meta.get(glyph)
        return meta.base_name if meta is not None else glyph

    def _source_matches(self, triggers: tuple[str, ...], source_family: str) -> bool:
        for trigger in triggers:
            if trigger == source_family:
                return True
            tmeta = self.glyph_meta.get(trigger)
            if tmeta is not None and tmeta.base_name == source_family:
                return True
        return False

    def _reachable_entry_ys(self, neighbor: str, source_family: str) -> frozenset[int]:
        """Return the entry Ys the follower `neighbor` can have in context, for a rule whose variant belongs to `source_family`.

        Every neighbor gets its own entries and those of the ligatures its family leads. A bare family glyph also gets the entries of its backward replacements and of its pair overrides whose `after` names `source_family` (`qsFee.en-y5` after `qsLow.ex-ext-1`). Any other stance with an exit and no entry, such as `qsFee.ex-y5`, also gets the entries of the backward replacements that keep its exit, and of the `reverse_upgrade_from` stances that name it and have no `after` or an `after` that names `source_family` (`qsPea.half.ex-y5.ex-dips` gains y6 through `qsPea.half.en-y6.ex-y5.ex-dips`). Other stances get nothing more.
        """
        key = (neighbor, source_family)
        cached = self._entry_cache.get(key)
        if cached is not None:
            return cached
        nmeta = self.glyph_meta.get(neighbor)
        if nmeta is None:
            self._entry_cache[key] = frozenset()
            return frozenset()
        ys: set[int] = set(nmeta.all_entry_ys)
        base = nmeta.base_name
        if neighbor == base:
            for var in self.bk_replacements.get(base, {}).values():
                vm = self.glyph_meta.get(var)
                if vm is not None:
                    ys.update(vm.all_entry_ys)
            for var in self.base_to_variants.get(base, ()):
                vm = self.glyph_meta.get(var)
                if vm is not None and vm.entry and vm.after and self._source_matches(vm.after, source_family):
                    ys.update(vm.all_entry_ys)
        elif nmeta.exit_ys and not nmeta.all_entry_ys:
            # `neighbor` has already chosen its exit, and a later backward upgrade can still give it an entry. Only the upgrades that keep that exit can fire, so only their entries count. `qsTea.half.ex-y5` before `qsIt.ex-y0` joins because ·It becomes `qsIt.en-y5.ex-y0`, and `qsRoe.ex-y0` before `qsMay.ex-ext-1` joins through `qsMay.en-y0.ex-y5`. `qsJai.ex-y0` before `qsIt.ex-y0` stays a leak, because the upgrade it needs, `qsIt.en-y0.ex-y5`, moves ·It's exit to the x-height.
            proxy_exits = set(nmeta.exit_ys)
            for var in self.bk_replacements.get(base, {}).values():
                vm = self.glyph_meta.get(var)
                if vm is not None and proxy_exits <= set(vm.exit_ys):
                    ys.update(vm.all_entry_ys)
            for upgrade_name, after in self._reverse_upgrades_from.get(neighbor, ()):
                um = self.glyph_meta.get(upgrade_name)
                if um is not None and (not after or self._source_matches(tuple(after), source_family)):
                    ys.update(um.all_entry_ys)
        for lig in self._leads.get(base, ()):
            lm = self.glyph_meta.get(lig)
            if lm is not None:
                ys.update(lm.all_entry_ys)
        result = frozenset(ys)
        self._entry_cache[key] = result
        return result

    def _reachable_exit_ys(self, neighbor: str, source_family: str) -> frozenset[int]:
        """Return the exit Ys the predecessor `neighbor` can have in context: its own exits and those of the ligatures its family trails, plus, for a bare family glyph, the exits of its forward replacements and of its pair overrides whose `before` names `source_family`."""
        key = (neighbor, source_family)
        cached = self._exit_cache.get(key)
        if cached is not None:
            return cached
        nmeta = self.glyph_meta.get(neighbor)
        if nmeta is None:
            self._exit_cache[key] = frozenset()
            return frozenset()
        ys: set[int] = set(nmeta.exit_ys)
        base = nmeta.base_name
        if neighbor == base:
            for var in self.fwd_replacements.get(base, {}).values():
                vm = self.glyph_meta.get(var)
                if vm is not None:
                    ys.update(vm.exit_ys)
            for var in self.base_to_variants.get(base, ()):
                vm = self.glyph_meta.get(var)
                if (
                    vm is not None
                    and vm.exit
                    and vm.before
                    and self._source_matches(vm.before, source_family)
                ):
                    ys.update(vm.exit_ys)
        for lig in self._trails.get(base, ()):
            lm = self.glyph_meta.get(lig)
            if lm is not None:
                ys.update(lm.exit_ys)
        result = frozenset(ys)
        self._exit_cache[key] = result
        return result

    def _classify(self, variant_name: str, neighbor: str, direction: str) -> str:
        vmeta = self.glyph_meta.get(variant_name)
        nmeta = self.glyph_meta.get(neighbor)
        if vmeta is None or nmeta is None:
            return "unknown"
        # A variant with no exit (an `ex-noentry` or `noexit` stance) can't dangle toward a follower, and one with no entry can't dangle toward a predecessor. The emitter selects such a stance because the neighbor can't join, as with `qsGay.en-y5.ex-noentry` before the entryless `qsTea_qsOy` in ·Utter·Gay·Tea·Oy. Without this exemption every exit-less forward rule would lose all its followers and never fire.
        if direction == "fwd" and not vmeta.exit_ys:
            return "joining"
        if direction == "bk" and not vmeta.all_entry_ys:
            return "joining"
        source_family = vmeta.base_name
        if direction == "fwd":
            joined = bool(set(vmeta.exit_ys) & self._reachable_entry_ys(neighbor, source_family))
        else:
            joined = bool(self._reachable_exit_ys(neighbor, source_family) & set(vmeta.all_entry_ys))
        if joined:
            return "joining"
        if self._is_cosmetic(vmeta, neighbor, direction):
            return "cosmetic"
        return "leak"

    def _is_cosmetic(self, vmeta: JoinGlyph, neighbor: str, direction: str) -> bool:
        prefix = "before-" if direction == "fwd" else "after-"
        if not any(m.startswith(prefix) for m in vmeta.modifiers):
            return False
        triggers = vmeta.before if direction == "fwd" else vmeta.after
        neighbor_bases = {neighbor, self._base_name(neighbor)}
        for trigger in triggers:
            if trigger in neighbor_bases or self._base_name(trigger) in neighbor_bases:
                return True
        return False

    def leak_keys(self) -> list[tuple[str, str, str]]:
        return sorted(key for key, verdict in self.verdicts.items() if verdict == "leak")

    def flush(self) -> None:
        counts: dict[str, int] = {"joining": 0, "cosmetic": 0, "leak": 0, "unknown": 0}
        for verdict in self.verdicts.values():
            counts[verdict] = counts.get(verdict, 0) + 1

        lines = [
            "# Leak-contract in-emitter report. Generated by _emit_quikscript_calt; do not hand-edit.",
            "# Each row is a (variant, neighbor, direction) triple the calt selection chokepoint considered,",
            "# classified by the derived join contract (doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md). Phase 2 enforces: the",
            "# `leak` rows below are dropped from their rules' context, so the emitted FEA no longer selects",
            "# those variants across the breaks they cannot join.",
            f"# triples considered: {len(self.verdicts)}  "
            f"(leak={counts['leak']}, cosmetic={counts['cosmetic']}, joining={counts['joining']}, unknown={counts['unknown']})",
            "",
        ]
        headers = {
            "leak": "Non-joining, non-cosmetic selections the contract drops:",
            "cosmetic": "Author-declared cosmetic tucks the contract keeps (before-/after- modifier):",
            "unknown": "Neighbor or variant absent from glyph_meta (not classified):",
        }
        for verdict in ("leak", "cosmetic", "unknown"):
            rows = [key for key, v in self.verdicts.items() if v == verdict]
            lines.append(f"## {headers[verdict]} ({len(rows)})")
            for variant, neighbor, direction in sorted(rows):
                pivots = ",".join(sorted(self.pivots[(variant, neighbor, direction)]))
                lines.append(f"  {direction}  {variant}  vs  {neighbor}  [pivots: {pivots}]")
            lines.append("")

        _CONTRACT_EMIT_DUMP_PATH.parent.mkdir(exist_ok=True)
        _CONTRACT_EMIT_DUMP_PATH.write_text("\n".join(lines) + "\n")

        is_production_repertoire = _BASELINE_REPERTOIRE_SENTINELS <= self.glyph_meta.keys()
        if is_production_repertoire and counts["leak"] != _EXPECTED_CONTRACT_DROP_COUNT:
            # Function-local import: quikscript_join_analysis imports from quikscript_fea, so a top-of-module import here would cycle.
            from quikscript_join_analysis import NonJoiningNeighborSelectionWarning

            warnings.warn(
                f"Derived join contract dropped {counts['leak']} single-rule cross-break selections that "
                f"named a non-joining, non-cosmetic neighbor; expected {_EXPECTED_CONTRACT_DROP_COUNT} (baseline drift). "
                f"If this change is intended, update _EXPECTED_CONTRACT_DROP_COUNT in tools/quikscript_fea.py. "
                f"Full breakdown: {_CONTRACT_EMIT_DUMP_PATH}.",
                NonJoiningNeighborSelectionWarning,
                stacklevel=2,
            )


_active_contract_recorder: _JoinContractRecorder | None = None


def _select_rule_neighbors(
    base_name: str,
    variant_name: str,
    candidate_members: set[str],
    *,
    direction: str,
) -> set[str]:
    """Return the neighbors a contextual rule selecting `variant_name` may keep under the derived join contract.

    `candidate_members` is the rule's followers for `direction="fwd"` or its predecessors for `direction="bk"`. While `_emit_quikscript_calt` runs, `_active_contract_recorder` classifies each neighbor and this drops the `leak` ones (see `_JoinContractRecorder`). With no recorder installed, as in unit tests that call this directly, it returns a copy of `candidate_members`, so callers can compare `kept == candidate_members` without aliasing their set.
    """
    recorder = _active_contract_recorder
    if recorder is None:
        return set(candidate_members)
    recorder.observe(base_name, variant_name, candidate_members, direction)
    return recorder.keep(variant_name, candidate_members, direction)


def _has_left_entry(meta: JoinGlyph) -> bool:
    return bool(meta.entry or meta.entry_curs_only)


def _entry_anchor_is_visual_addition(
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    variant_name: str,
) -> bool:
    """Return True when this variant has an entry anchor and no entryless sibling draws the same bitmap.

    A matching entryless sibling means the anchor changes only the position, not the drawing. Generated `.noentry` siblings don't count, because they always copy their source's bitmap.
    """
    variant_meta = glyph_meta[variant_name]
    if not variant_meta.entry:
        return False
    target_bitmap = variant_meta.bitmap
    base = variant_meta.base_name
    for sibling_name in base_to_variants.get(base, ()):
        if sibling_name == variant_name:
            continue
        sibling_meta = glyph_meta[sibling_name]
        if _has_left_entry(sibling_meta):
            continue
        if sibling_meta.noentry_for is not None:
            continue
        if sibling_meta.bitmap == target_bitmap:
            return False
    return True


def _resolve_noentry_replacement(
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    target_name: str,
    replacement_name: str,
) -> str | None:
    target_meta = glyph_meta[target_name]
    if not target_meta.is_noentry:
        return replacement_name

    return _resolve_entryless_replacement(
        glyph_meta,
        base_to_variants,
        target_name,
        replacement_name,
    )


def _resolve_entryless_replacement(
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    target_name: str,
    replacement_name: str,
) -> str | None:
    target_meta = glyph_meta[target_name]
    if _has_left_entry(target_meta):
        return replacement_name

    replacement_meta = glyph_meta[replacement_name]
    if not _has_left_entry(replacement_meta):
        return replacement_name

    desired_exit_ys = tuple(sorted(set(replacement_meta.exit_ys)))
    candidates: list[tuple[tuple, str]] = []
    for candidate_name in sorted(base_to_variants.get(replacement_meta.base_name, ())):
        candidate_meta = glyph_meta[candidate_name]
        if _has_left_entry(candidate_meta):
            continue
        if tuple(sorted(set(candidate_meta.exit_ys))) != desired_exit_ys:
            continue
        if candidate_meta.extended_exit_suffix != replacement_meta.extended_exit_suffix:
            continue
        candidate_modifiers = tuple(
            modifier for modifier in candidate_meta.modifiers if modifier != "noentry"
        )
        score = (
            candidate_meta.generated_from == replacement_name,
            candidate_meta.noentry_for == replacement_name,
            candidate_meta.exit == replacement_meta.exit,
            candidate_meta.exit_suffix == replacement_meta.exit_suffix,
            candidate_meta.before == replacement_meta.before,
            candidate_meta.not_before == replacement_meta.not_before,
            candidate_meta.gate_feature == replacement_meta.gate_feature,
            candidate_meta.transform_kind == replacement_meta.transform_kind,
            candidate_modifiers == replacement_meta.modifiers,
            not candidate_meta.is_contextual,
            -len(candidate_meta.modifiers),
        )
        candidates.append((score, candidate_name))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][1]


def _expand_backward_after_variants(
    variant_name: str,
    after_glyphs: list[str] | tuple[str, ...],
    *,
    expand_selector,
    analysis: _JoinAnalysis,
    feature_tag: str | None = None,
) -> set[str]:
    glyph_meta = analysis.glyph_meta
    base_to_variants = analysis.base_to_variants
    variant_meta = glyph_meta[variant_name]
    entry_ys = set(variant_meta.entry_ys)
    right_base = variant_meta.base_name
    expanded: set[str] = set()

    for after_glyph in after_glyphs:
        candidates = set(expand_selector(after_glyph))
        # Add the base's `.noentry` stances. `calt_zwnj` substitutes one after a ZWNJ, and `expand_selector` doesn't return them, so without this the backward rules wouldn't fire after one.
        after_base = glyph_meta[after_glyph].base_name if after_glyph in glyph_meta else after_glyph
        for candidate_name in base_to_variants.get(after_base, ()):
            if glyph_meta[candidate_name].is_noentry:
                candidates.add(candidate_name)
        if entry_ys:

            def _unstripped_is_candidate(name: str) -> bool:
                parts = name.split(".")
                if "noentry" not in parts:
                    return True
                unstripped = ".".join(p for p in parts if p != "noentry")
                return unstripped in candidates

            candidates = {
                candidate
                for candidate in candidates
                if (
                    set(glyph_meta[candidate].exit_ys) & entry_ys
                    or (
                        not glyph_meta[candidate].exit
                        # `_can_eventually_exit_at` is unreliable for a `.noentry` stance: bare `qsTea.noentry` never reaches a half ·Tea exit. Accept a `.noentry` stance only when the same name without `noentry` is also a candidate.
                        and (not glyph_meta[candidate].is_noentry or _unstripped_is_candidate(candidate))
                        and any(
                            _can_eventually_exit_at(
                                analysis,
                                candidate,
                                entry_y,
                                before_base=right_base,
                                feature_tag=feature_tag,
                            )
                            for entry_y in entry_ys
                        )
                    )
                )
            }
        expanded.update(candidates)

    if variant_meta.entry_restriction_y is not None:
        entry_y = variant_meta.entry_restriction_y
        expanded = {
            candidate
            for candidate in expanded
            if _can_eventually_exit_at(
                analysis,
                candidate,
                entry_y,
                before_base=right_base,
                feature_tag=feature_tag,
            )
        }

    if variant_meta.not_after:
        excluded: set[str] = set()
        for excluded_glyph in variant_meta.not_after:
            base = glyph_meta[excluded_glyph].base_name if excluded_glyph in glyph_meta else excluded_glyph
            variants = base_to_variants.get(base)
            if variants:
                excluded.update(variants)
            else:
                excluded.add(excluded_glyph)
        expanded -= excluded

    return expanded


def _expand_forward_before_variants(
    variant_name: str,
    before_glyphs: list[str] | tuple[str, ...],
    *,
    analysis: _JoinAnalysis,
    feature_tag: str | None = None,
) -> set[str]:
    glyph_meta = analysis.glyph_meta
    source_meta = glyph_meta[variant_name]
    if feature_tag is None:
        expanded_before = _expand_join_variants(before_glyphs, analysis)
        for glyph in before_glyphs:
            base = glyph_meta[glyph].base_name if glyph in glyph_meta else glyph
            if base in analysis.fwd_upgrades:
                expanded_before.update(
                    entry_exit_var
                    for entry_exit_var, _entry_y, _exit_y, _not_after in analysis.fwd_upgrades[base]
                )
    else:
        expanded_before: set[str] = set()
        for glyph in before_glyphs:
            base = glyph_meta[glyph].base_name if glyph in glyph_meta else glyph
            expanded_before.update(analysis.base_to_variants.get(base, ()))

    if feature_tag is not None or not source_meta.exit:
        return expanded_before

    source_exit_ys = set(source_meta.exit_ys)

    def _candidate_compatible(name: str) -> bool:
        cand_meta = glyph_meta[name]
        cand_entry_ys = {
            anchor[1]
            for anchor in (
                *cand_meta.entry,
                *cand_meta.entry_curs_only,
            )
        }
        if not cand_entry_ys:
            return True
        if cand_entry_ys & source_exit_ys:
            return True
        if name == cand_meta.base_name:
            family_entry_ys: set[int] = set()
            for sibling in analysis.base_to_variants.get(name, ()):
                sib_meta = glyph_meta[sibling]
                family_entry_ys.update(
                    anchor[1]
                    for anchor in (
                        *sib_meta.entry,
                        *sib_meta.entry_curs_only,
                    )
                )
            if family_entry_ys & source_exit_ys:
                return True
        return False

    return {
        candidate
        for candidate in expanded_before
        if candidate in glyph_meta and _candidate_compatible(candidate)
    }


def _split_fea_context_tokens(body: str) -> tuple[str, ...] | None:
    tokens = []
    i = 0
    while i < len(body):
        while i < len(body) and body[i].isspace():
            i += 1
        if i >= len(body):
            break
        if body[i] == "[":
            end = body.find("]", i + 1)
            if end == -1:
                return None
            end += 1
            if end < len(body) and body[end] == "'":
                end += 1
            tokens.append(body[i:end])
            i = end
            continue
        end = i
        while end < len(body) and not body[end].isspace():
            end += 1
        tokens.append(body[i:end])
        i = end
    return tuple(tokens)


def _parse_ignore_sub_line(line: str) -> tuple[str, tuple[str, ...]] | None:
    stripped = line.lstrip()
    if not stripped.startswith("ignore sub ") or not stripped.endswith(";"):
        return None
    indent = line[: len(line) - len(stripped)]
    body = stripped.removeprefix("ignore sub ")[:-1].strip()
    tokens = _split_fea_context_tokens(body)
    if not tokens:
        return None
    return indent, tokens


def _is_groupable_context_token(token: str) -> bool:
    return (
        not token.startswith("[")
        and not token.startswith("@")
        and not token.endswith("'")
        and "[" not in token
        and "]" not in token
    )


def _format_ignore_sub_line(indent: str, tokens: tuple[str, ...]) -> str:
    return f"{indent}ignore sub {' '.join(tokens)};"


def _parse_substitution_line(line: str) -> tuple[str, tuple[str, ...], str] | None:
    stripped = line.lstrip()
    if not stripped.startswith("sub ") or not stripped.endswith(";"):
        return None
    indent = line[: len(line) - len(stripped)]
    body = stripped.removeprefix("sub ")[:-1].strip()
    try:
        context_body, replacement = body.rsplit(" by ", 1)
    except ValueError:
        return None
    tokens = _split_fea_context_tokens(context_body)
    if not tokens:
        return None
    return indent, tokens, replacement.strip()


def _format_substitution_line(indent: str, tokens: tuple[str, ...], replacement: str) -> str:
    return f"{indent}sub {' '.join(tokens)} by {replacement};"


def _coalesce_parsed_ignore_rules(
    entries: list[tuple[str, tuple[str, ...]]],
) -> list[str]:
    deduped_entries = []
    seen = set()
    for entry in entries:
        if entry in seen:
            continue
        seen.add(entry)
        deduped_entries.append(entry)

    consumed: set[int] = set()
    result = []
    for i, (indent, tokens) in enumerate(deduped_entries):
        if i in consumed:
            continue

        best_group: list[int] = []
        best_slot: int | None = None
        for slot, token in enumerate(tokens):
            if not _is_groupable_context_token(token):
                continue
            group = [i]
            for j in range(i + 1, len(deduped_entries)):
                if j in consumed:
                    continue
                other_indent, other_tokens = deduped_entries[j]
                if other_indent != indent or len(other_tokens) != len(tokens):
                    continue
                if not _is_groupable_context_token(other_tokens[slot]):
                    continue
                if other_tokens[:slot] != tokens[:slot]:
                    continue
                if other_tokens[slot + 1 :] != tokens[slot + 1 :]:
                    continue
                group.append(j)
            if len(group) > len(best_group):
                best_group = group
                best_slot = slot

        if best_slot is not None and len(best_group) > 1:
            replacements = {deduped_entries[group_index][1][best_slot] for group_index in best_group}
            if len(replacements) > 1:
                grouped_tokens = list(tokens)
                grouped_tokens[best_slot] = f"[{' '.join(sorted(replacements))}]"
                result.append(_format_ignore_sub_line(indent, tuple(grouped_tokens)))
                consumed.update(best_group)
                continue

        result.append(_format_ignore_sub_line(indent, tokens))
        consumed.add(i)

    return result


def _coalesce_ignore_sub_run(lines: list[str]) -> list[str]:
    result = []
    parsed_entries: list[tuple[str, tuple[str, ...]]] = []

    def flush() -> None:
        nonlocal parsed_entries
        if parsed_entries:
            result.extend(_coalesce_parsed_ignore_rules(parsed_entries))
            parsed_entries = []

    for line in lines:
        parsed = _parse_ignore_sub_line(line)
        if parsed is None:
            flush()
            result.append(line)
        else:
            parsed_entries.append(parsed)
    flush()
    return result


def _coalesce_consecutive_ignore_rules(lines: list[str]) -> list[str]:
    result = []
    run = []

    def flush() -> None:
        nonlocal run
        if run:
            result.extend(_coalesce_ignore_sub_run(run))
            run = []

    for line in lines:
        if line.lstrip().startswith("ignore sub "):
            run.append(line)
        else:
            flush()
            result.append(line)
    flush()
    return result


_ZWNJ_FIREWALL_EXEMPT_LOOKUPS: frozenset[str] = frozenset(
    {
        # `calt_zwnj` matches the ZWNJ itself (`sub uni200C @qs_has_entry' by @qs_noentry;`), so a guard would block it.
        "calt_zwnj",
    }
)


_POST_ZWNJ_NOENTRY_SUFFIX = ".noentry"


def _strip_post_zwnj_token(
    token: str,
    base_to_variants: dict[str, set[str]],
) -> str | None:
    """Remove ``<family>.noentry`` glyphs, the stances ``calt_zwnj`` produces, from one FEA context token.

    A token is a glyph name, a bracketed class, or a ``@cls`` reference. Class references are returned unchanged. Returns ``None`` when no member is left, so the caller drops the rule.
    """

    def is_post_zwnj(name: str) -> bool:
        if not name.endswith(_POST_ZWNJ_NOENTRY_SUFFIX):
            return False
        base = name[: -len(_POST_ZWNJ_NOENTRY_SUFFIX)]
        variants = base_to_variants.get(base)
        # Only `<family>.noentry` on a bare family glyph counts; `qsX.half.noentry` does not.
        return bool(variants and base in variants)

    stripped_marker = token.endswith("'")
    body = token[:-1] if stripped_marker else token
    if body.startswith("[") and body.endswith("]"):
        members = body[1:-1].split()
        kept = [m for m in members if not is_post_zwnj(m)]
        if not kept:
            return None
        if len(kept) == 1:
            new_body = kept[0]
        else:
            new_body = "[" + " ".join(kept) + "]"
    else:
        if is_post_zwnj(body):
            return None
        new_body = body
    return new_body + ("'" if stripped_marker else "")


def _strip_post_zwnj_from_ignore_contexts(
    lines: list[str],
    base_to_variants: dict[str, set[str]],
) -> list[str]:
    """Remove ``<family>.noentry`` glyphs from the lookahead positions of every ``ignore sub`` rule, and drop a rule whose lookahead position is left empty.

    ``calt_zwnj`` puts these glyphs right after a ZWNJ. HarfBuzz skips the ZWNJ when it matches context, so in lookahead they would let an ignore rule on the letter before the ZWNJ match across it. Backtrack positions are kept because they are valid guards inside the run after the ZWNJ, such as the one that stops ``ZWNJ ·Ye ·It`` from joining at the baseline. Input positions are kept too.
    """
    result: list[str] = []
    for line in lines:
        parsed = _parse_ignore_sub_line(line)
        if parsed is None:
            result.append(line)
            continue
        indent, tokens = parsed
        marked_indexes = [index for index, token in enumerate(tokens) if token.endswith("'")]
        if not marked_indexes:
            result.append(line)
            continue
        last_marked_index = marked_indexes[-1]
        new_tokens: list[str] = []
        drop_rule = False
        for index, token in enumerate(tokens):
            if index <= last_marked_index:
                new_tokens.append(token)
                continue
            replacement = _strip_post_zwnj_token(token, base_to_variants)
            if replacement is None:
                drop_rule = True
                break
            new_tokens.append(replacement)
        if drop_rule:
            continue
        if tuple(new_tokens) == tokens:
            result.append(line)
        else:
            result.append(_format_ignore_sub_line(indent, tuple(new_tokens)))
    return result


def _marked_glyphs_for_token(token: str) -> list[str]:
    if not token.endswith("'"):
        return []
    body = token[:-1]
    if body.startswith("[") and body.endswith("]"):
        return [member for member in body[1:-1].split() if not member.startswith("@")]
    if body.startswith("@") or body == "uni200C":
        return []
    return [body]


def _collect_marked_input_glyphs(
    body_lines: list[str],
) -> tuple[list[str], list[str]]:
    backtrack_seen: list[str] = []
    backtrack_seen_set: set[str] = set()
    lookahead_seen: list[str] = []
    lookahead_seen_set: set[str] = set()

    def add_backtrack_target(name: str) -> None:
        if name in backtrack_seen_set:
            return
        backtrack_seen_set.add(name)
        backtrack_seen.append(name)

    def add_lookahead_target(name: str) -> None:
        if name in lookahead_seen_set:
            return
        lookahead_seen_set.add(name)
        lookahead_seen.append(name)

    def collect_from_tokens(tokens: tuple[str, ...]) -> None:
        marked_indexes = [index for index, token in enumerate(tokens) if token.endswith("'")]
        if not marked_indexes:
            return
        has_backtrack = marked_indexes[0] > 0
        has_lookahead = marked_indexes[-1] < len(tokens) - 1
        for index in marked_indexes:
            for target in _marked_glyphs_for_token(tokens[index]):
                if has_backtrack:
                    add_backtrack_target(target)
                if has_lookahead:
                    add_lookahead_target(target)

    for line in body_lines:
        parsed_sub = _parse_substitution_line(line)
        if parsed_sub is not None:
            _, tokens, _ = parsed_sub
            collect_from_tokens(tokens)
            continue
        parsed_ignore = _parse_ignore_sub_line(line)
        if parsed_ignore is not None:
            _, tokens = parsed_ignore
            collect_from_tokens(tokens)
    return backtrack_seen, lookahead_seen


def _prefix_zwnj_for_run_initial_noentry_input(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    marked_indexes = [index for index, token in enumerate(tokens) if token.endswith("'")]
    if marked_indexes != [0]:
        return None
    input_token = tokens[0]
    input_name = input_token[:-1]
    if input_name.startswith("[") or input_name.startswith("@"):
        return None
    if not input_name.endswith(_POST_ZWNJ_NOENTRY_SUFFIX):
        return None
    return ("uni200C", *tokens)


def _suffix_zwnj_for_run_final_input(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    marked_indexes = [index for index, token in enumerate(tokens) if token.endswith("'")]
    if marked_indexes != [len(tokens) - 1]:
        return None
    input_name = tokens[-1][:-1]
    if input_name == "uni200C":
        return None
    return (*tokens, "uni200C")


def _zwnj_boundary_replay_lines_for_calt_lookup(body_lines: list[str]) -> list[str]:
    replay_lines: list[str] = []
    seen: set[str] = set()
    for line in body_lines:
        parsed_ignore = _parse_ignore_sub_line(line)
        if parsed_ignore is not None:
            indent, tokens = parsed_ignore
            for replay_tokens in (
                _prefix_zwnj_for_run_initial_noentry_input(tokens),
                _suffix_zwnj_for_run_final_input(tokens),
            ):
                if replay_tokens is None:
                    continue
                replay_line = _format_ignore_sub_line(indent, replay_tokens)
                if replay_line not in seen:
                    seen.add(replay_line)
                    replay_lines.append(replay_line)
            continue
        parsed_sub = _parse_substitution_line(line)
        if parsed_sub is None:
            continue
        indent, tokens, replacement = parsed_sub
        for replay_tokens in (
            _prefix_zwnj_for_run_initial_noentry_input(tokens),
            _suffix_zwnj_for_run_final_input(tokens),
        ):
            if replay_tokens is None:
                continue
            replay_line = _format_substitution_line(indent, replay_tokens, replacement)
            if replay_line not in seen:
                seen.add(replay_line)
                replay_lines.append(replay_line)
    return replay_lines


def _ensure_zwnj_coverage_for_calt_lookups(lines: list[str]) -> list[str]:
    """Add ZWNJ guard rules to every chained calt lookup so its context rules can't match across a ZWNJ.

    HarfBuzz skips default-ignorable glyphs such as ZWNJ when it matches context, so without the guards a rule like ``sub @bk X' @fwd by Y;`` matches across a ZWNJ.

    For each ``lookup calt_NAME { ... } calt_NAME;`` block that has a chained rule and doesn't name ``uni200C``, this adds, ahead of the body:

    - replays of the rules whose only input is a ``.noentry`` glyph with no backtrack, with ``uni200C`` as explicit backtrack, and replays of the rules with no lookahead, with ``uni200C`` as explicit lookahead. They keep normal shaping at the start and end of a ZWNJ-delimited run.
    - ``ignore sub uni200C TARGET';`` for each input that has backtrack context in some rule, and ``ignore sub TARGET' uni200C;`` for each input that has lookahead context.

    ``_ZWNJ_FIREWALL_EXEMPT_LOOKUPS`` lists the lookups left alone.
    """
    lookup_open_pattern = re.compile(r"^(\s*)lookup\s+(calt_\S+)\s*\{\s*$")
    lookup_close_template = "{indent}}} {name};"

    # A font without `calt_zwnj`, such as a small test fixture, may have no uni200C glyph, and naming it would fail FEA compilation.
    if not any("calt_zwnj" in line for line in lines):
        return lines

    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = lookup_open_pattern.match(line)
        if match is None:
            result.append(line)
            i += 1
            continue
        indent = match.group(1)
        name = match.group(2)
        expected_close = lookup_close_template.format(indent=indent, name=name).strip()
        close_index = None
        for j in range(i + 1, len(lines)):
            if lines[j].strip() == expected_close:
                close_index = j
                break
        if close_index is None:
            result.append(line)
            i += 1
            continue
        body = lines[i + 1 : close_index]
        if name in _ZWNJ_FIREWALL_EXEMPT_LOOKUPS:
            result.extend(lines[i : close_index + 1])
            i = close_index + 1
            continue
        if any("uni200C" in body_line for body_line in body):
            result.extend(lines[i : close_index + 1])
            i = close_index + 1
            continue
        backtrack_targets, lookahead_targets = _collect_marked_input_glyphs(body)
        if not backtrack_targets and not lookahead_targets:
            result.extend(lines[i : close_index + 1])
            i = close_index + 1
            continue
        guard_lines = [
            f"{indent}    ignore sub uni200C {target}';" for target in sorted(set(backtrack_targets))
        ]
        guard_lines.extend(
            f"{indent}    ignore sub {target}' uni200C;" for target in sorted(set(lookahead_targets))
        )
        result.append(line)
        result.extend(_zwnj_boundary_replay_lines_for_calt_lookup(body))
        result.extend(guard_lines)
        result.extend(body)
        result.append(lines[close_index])
        i = close_index + 1
    return result


def _add_zwnj_guards_for_two_position_forward_rules(lines: list[str]) -> list[str]:
    """Stop two-position forward rules like `sub TARGET' MID [LIST] by REPLACEMENT;` from matching across a ZWNJ between MID and LIST.

    `_ensure_zwnj_coverage_for_calt_lookups` guards only the position next to TARGET, so `TARGET MID uni200C LIST_MEMBER` still matches, because HarfBuzz skips the ZWNJ. This adds `ignore sub TARGET' MID uni200C;` ahead of each such rule.

    It matches only rules whose second lookahead position is a bracketed class, the form `_emit_fwd_pairs` writes for `before_lig_lead_followups`. It skips the lookups in `_ZWNJ_FIREWALL_EXEMPT_LOOKUPS` and those whose body doesn't name uni200C, which are the lookups `_ensure_zwnj_coverage_for_calt_lookups` left unguarded.
    """
    lookup_open_pattern = re.compile(r"^(\s*)lookup\s+(calt_\S+)\s*\{\s*$")
    lookup_close_template = "{indent}}} {name};"
    sub_pattern = re.compile(
        r"^(\s*)sub\s+(?:\[[^\]]+\]\s+)?([A-Za-z0-9_.]+)'\s+([A-Za-z0-9_.]+)\s+\[[^\]]+\]\s+by\s+\S+;\s*$"
    )

    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = lookup_open_pattern.match(line)
        if match is None:
            result.append(line)
            i += 1
            continue
        indent = match.group(1)
        name = match.group(2)
        expected_close = lookup_close_template.format(indent=indent, name=name).strip()
        close_index = None
        for j in range(i + 1, len(lines)):
            if lines[j].strip() == expected_close:
                close_index = j
                break
        if close_index is None:
            result.append(line)
            i += 1
            continue
        if name in _ZWNJ_FIREWALL_EXEMPT_LOOKUPS:
            result.extend(lines[i : close_index + 1])
            i = close_index + 1
            continue
        body = lines[i + 1 : close_index]
        if not any("uni200C" in body_line for body_line in body):
            result.extend(lines[i : close_index + 1])
            i = close_index + 1
            continue
        existing_guards: set[tuple[str, str]] = set()
        guard_pattern = re.compile(
            r"^\s*ignore\s+sub\s+([A-Za-z0-9_.]+)'\s+([A-Za-z0-9_.]+)\s+uni200C\s*;\s*$"
        )
        for body_line in body:
            guard_match = guard_pattern.match(body_line)
            if guard_match:
                existing_guards.add((guard_match.group(1), guard_match.group(2)))
        new_body: list[str] = []
        for body_line in body:
            sub_match = sub_pattern.match(body_line)
            if sub_match is None:
                new_body.append(body_line)
                continue
            body_indent, target, mid = sub_match.group(1), sub_match.group(2), sub_match.group(3)
            if mid == "uni200C":
                new_body.append(body_line)
                continue
            if (target, mid) not in existing_guards:
                new_body.append(f"{body_indent}ignore sub {target}' {mid} uni200C;")
                existing_guards.add((target, mid))
            new_body.append(body_line)
        result.append(line)
        result.extend(new_body)
        result.append(lines[close_index])
        i = close_index + 1
    return result


def _format_post_liga_cleanup_rules(
    rules: list[tuple[str, str, str]],
) -> list[str]:
    grouped_rules: dict[tuple[str, str], list[str]] = {}
    for lig_target, candidate, replacement in rules:
        key = (candidate, replacement)
        grouped_rules.setdefault(key, []).append(lig_target)

    lines = []
    for (candidate, replacement), lig_targets in grouped_rules.items():
        unique_lig_targets = sorted(set(lig_targets))
        if len(unique_lig_targets) == 1:
            lig_target = unique_lig_targets[0]
            lines.append(f"        sub {lig_target} {candidate}' by {replacement};")
        else:
            lig_target_list = " ".join(unique_lig_targets)
            lines.append(f"        sub [{lig_target_list}] {candidate}' by {replacement};")
    return lines


def _format_post_liga_left_cleanup_rules(
    rules: list[tuple[str, str, str]],
) -> list[str]:
    grouped_rules: dict[tuple[str, str], list[str]] = {}
    for lig_target, candidate, replacement in rules:
        key = (candidate, replacement)
        grouped_rules.setdefault(key, []).append(lig_target)

    lines = []
    for (candidate, replacement), lig_targets in grouped_rules.items():
        unique_lig_targets = sorted(set(lig_targets))
        if len(unique_lig_targets) == 1:
            lig_target = unique_lig_targets[0]
            lines.append(f"        sub {candidate}' {lig_target} by {replacement};")
        else:
            lig_target_list = " ".join(unique_lig_targets)
            lines.append(f"        sub {candidate}' [{lig_target_list}] by {replacement};")
    return lines


_HOISTED_CLASS_PREFIX = "@ams"
_INLINE_CLASS_RE = re.compile(r"\[[^\[\]]*\]")
_CLASS_DEFINITION_RE = re.compile(r"^\s*@\S+ = \[")
_HOISTED_DEFINITION_RE = re.compile(r"^\s*(@ams\d+) = (\[[^\[\]]*\]);$")
_HOISTED_REFERENCE_RE = re.compile(r"@ams\d+")


def hoist_repeated_classes(fea: str) -> str:
    """Replace each inline `[...]` class that appears more than once in the calt block with a named class `@amsNNNN`.

    The definitions go after the class definitions that open the block. feaLib compiles a named class and an inline one to the same coverage, so the tables are unchanged, and the rules repeat the same neighbor lists many times, so the feature file and feaLib's parse memory shrink. Names are numbered by first appearance, so the output is deterministic. A body that names another class stays inline, so no hoisted class refers to a later definition. Class definition lines are not rewritten, and other feature blocks are left alone because a class defined inside a block is local to it. `expand_hoisted_classes` is the inverse.
    """
    start = fea.find("feature calt {\n")
    if start < 0:
        return fea
    end = fea.find("\n} calt;", start)
    if end < 0:
        return fea
    head, block, tail = fea[:start], fea[start:end], fea[end:]
    lines = block.split("\n")
    counts: Counter[str] = Counter()
    for line in lines[1:]:
        if not _CLASS_DEFINITION_RE.match(line):
            counts.update(_INLINE_CLASS_RE.findall(line))
    names: dict[str, str] = {}
    for body, count in counts.items():
        if count > 1 and "@" not in body:
            names[body] = f"{_HOISTED_CLASS_PREFIX}{len(names) + 1:04d}"
    if not names:
        return fea

    def _name(match: re.Match[str]) -> str:
        return names.get(match.group(0), match.group(0))

    out = [lines[0]]
    defined = False
    for line in lines[1:]:
        if _CLASS_DEFINITION_RE.match(line):
            out.append(line)
            continue
        if not defined:
            out.extend(f"    {name} = {body};" for body, name in names.items())
            defined = True
        out.append(_INLINE_CLASS_RE.sub(_name, line))
    return head + "\n".join(out) + tail


def expand_hoisted_classes(fea: str) -> str:
    """Undo `hoist_repeated_classes`: write each `@amsNNNN` class back inline and drop its definition. Tests use it to read neighbor lists from the built feature file."""
    bodies: dict[str, str] = {}
    kept: list[str] = []
    for line in fea.split("\n"):
        match = _HOISTED_DEFINITION_RE.match(line)
        if match:
            bodies[match.group(1)] = match.group(2)
        else:
            kept.append(line)
    if not bodies:
        return fea

    def _body(match: re.Match[str]) -> str:
        return bodies.get(match.group(0), match.group(0))

    return "\n".join(_HOISTED_REFERENCE_RE.sub(_body, line) for line in kept)


def _emit_quikscript_calt(analysis: _JoinAnalysis) -> str | None:
    global _active_contract_recorder
    plan = analysis
    glyph_meta = plan.glyph_meta
    glyph_names = plan.glyph_names
    base_to_variants = plan.base_to_variants

    # Installing the join-contract recorder (`_JoinContractRecorder`) makes `_select_rule_neighbors` classify every neighbor it sees and drop the `leak` neighbors from each rule's context. The recorder is flushed and removed just before this function returns.
    _active_contract_recorder = _JoinContractRecorder(
        glyph_meta,
        bk_replacements=plan.bk_replacements,
        fwd_replacements=plan.fwd_replacements,
        base_to_variants=plan.base_to_variants,
        reverse_only_upgrades=plan.reverse_only_upgrades,
    )

    # Function-local import: quikscript_join_analysis imports from quikscript_fea, so a top-of-module import here would cycle.
    from quikscript_join_analysis import (
        DerivedBkGuard,
        JoinReachability,
        _revert_keeps_reaching_exit,
        derive_pending_bk_entry_guards,
        derive_pending_fwd_strip_guards,
    )

    _reachability = JoinReachability.from_join_glyphs(glyph_meta)
    _derived_bk_guards = derive_pending_bk_entry_guards(_reachability)
    _derived_fwd_strip_guards = derive_pending_fwd_strip_guards(plan)
    # True while emitting lookups that run after `calt_cycle`. Only then does `_emit_narrow_mid_entry_strip_guards` apply the generic forward-strip guards to a bare mid glyph: before the cycle, the mid glyph still gains its entry in `calt_cycle`. Pair-specific forward strips don't depend on this flag, because the lookup sees the stripping right context in its own lookahead.
    _fwd_strip_guards_active = [False]

    generation_children: dict[str, list[str]] = defaultdict(list)
    for _gen_name, _gen_meta in glyph_meta.items():
        if _gen_meta.generated_from:
            generation_children[_gen_meta.generated_from].append(_gen_name)

    def _meta(name: str) -> JoinGlyph:
        return glyph_meta[name]

    def _base_name(name: str) -> str:
        if name in glyph_meta:
            return _meta(name).base_name
        return name

    bk_replacements = plan.bk_replacements
    bk_exclusions = plan.bk_exclusions
    pair_overrides = plan.pair_overrides
    fwd_upgrades = plan.fwd_upgrades
    fwd_replacements = plan.fwd_replacements
    fwd_exclusions = plan.fwd_exclusions
    fwd_pair_overrides = plan.fwd_pair_overrides
    exit_classes = plan.exit_classes
    entry_classes = plan.entry_classes
    entry_exclusive = plan.entry_exclusive
    fwd_use_exclusive = plan.fwd_use_exclusive
    fwd_preferred_lookahead = plan.fwd_preferred_lookahead
    cycle_bases = plan.cycle_bases
    all_bk_bases = plan.all_bk_bases
    lig_fwd_bases = plan.lig_fwd_bases
    early_pair_fwd_general = {
        base_name: set(plan.early_pair_fwd_general_exit_ys[base_name])
        for base_name in plan.early_pair_fwd_general
    }
    early_pair_upgrade_bases = plan.early_pair_upgrade_bases
    early_fwd_pairs = plan.early_fwd_pairs
    ligatures = plan.ligatures
    word_final_pairs = plan.word_final_pairs
    ligatures_by_first_component: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for lig_name, components in ligatures:
        if components:
            ligatures_by_first_component.setdefault(components[0], []).append((lig_name, components))

    if not bk_replacements and not fwd_replacements:
        return None

    def _expand_all_variants(glyphs, *, include_base=False):
        return _expand_join_variants(glyphs, plan, include_base=include_base)

    def _ligature_component_variants(lig_name: str, component: str, index: int) -> set[str]:
        lig_variants: set[str] = {component}
        if component in bk_replacements:
            lig_variants.update(bk_replacements[component].values())
        if component in pair_overrides:
            lig_variants.update(variant_name for variant_name, _ in pair_overrides[component])
        if component in fwd_pair_overrides:
            lig_variants.update(variant_name for variant_name, _, _ in fwd_pair_overrides[component])
        if (
            index == 0 or (index == 1 and lig_name in _LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS)
        ) and component in fwd_replacements:
            lig_variants.update(fwd_replacements[component].values())
        return lig_variants

    bk_used_ys = set()
    for variants in bk_replacements.values():
        bk_used_ys.update(variants.keys())

    fwd_used_ys = set()
    for variants in fwd_replacements.values():
        fwd_used_ys.update(variants.keys())
    for upgrade_list in fwd_upgrades.values():
        for _, _, exit_y, _ in upgrade_list:
            fwd_used_ys.add(exit_y)
    for entries in fwd_preferred_lookahead.values():
        for _, _, sibling_y in entries:
            fwd_used_ys.add(sibling_y)

    lines = ["feature calt {"]

    # Re-flip rules recorded alongside the backward guards and emitted later as `calt_pair_guard_reflip_*` lookups. A guard `ignore sub [prior_slot] candidate base';` keeps `base` plain, so the candidate's forward replacement is chosen against `base`'s plain entry Y. The re-flip `sub [prior_slot] pre_stance' base by isolated_form;` puts the candidate on the stance it would take if `base` had been upgraded, which is its stance when the pair is shaped in isolation.
    # Keyed by the candidate's base name (``qsIt``); each entry is ``(prior_slot, pre_stance, base_name, isolated_form)``.
    pair_guard_reflip: dict[str, list[tuple[frozenset[str], str, str, str]]] = {}

    def _record_pair_guard_reflip(
        prior_slot: frozenset[str],
        candidate_name: str,
        base_name: str,
        variant_entry_ys: set[int],
    ) -> None:
        # Record re-flips for the guard ``ignore sub [prior_slot] candidate base';``. Without the guard, ``base_name`` would take a variant entering at one of ``variant_entry_ys``, and the candidate's forward replacement would exit there. With it, the candidate is bare or on its forward replacement for one of ``base_name``'s plain entry Ys. Record a re-flip from each such stance to each isolated form.
        candidate_meta = glyph_meta.get(candidate_name)
        if candidate_meta is None:
            return
        # Skip candidates with an entry anchor. Their isolated form also depends on their own predecessor, which can give them a backward replacement, so a form derived from the right side alone could be wrong.
        if candidate_meta.entry:
            return
        candidate_base = candidate_meta.base_name
        base_meta = glyph_meta.get(base_name)
        if base_meta is None:
            return
        candidate_fwd = fwd_replacements.get(candidate_base, {})
        if not candidate_fwd:
            return
        # The isolated forms are the candidate's forward replacements at the upgraded variant's entry Ys.
        isolated_forms: set[str] = set()
        for variant_entry_y in variant_entry_ys:
            isolated_form = candidate_fwd.get(variant_entry_y)
            if isolated_form is not None and isolated_form != candidate_name:
                isolated_forms.add(isolated_form)
        if not isolated_forms:
            return
        # With the upgrade suppressed, `base_name` keeps its plain entry Ys. Drop an isolated form whose exit can't meet any of them, because it would break the join to `base_name`. Keep isolated forms with no exit: they join nothing on the right either way.
        plain_base_entry_ys = set(base_meta.entry_ys) - variant_entry_ys
        if plain_base_entry_ys:
            filtered_isolated_forms: set[str] = set()
            for isolated_form in isolated_forms:
                isolated_meta = glyph_meta.get(isolated_form)
                if isolated_meta is None:
                    filtered_isolated_forms.add(isolated_form)
                    continue
                isolated_exit_ys = set(isolated_meta.exit_ys)
                if not isolated_exit_ys or isolated_exit_ys & plain_base_entry_ys:
                    filtered_isolated_forms.add(isolated_form)
            isolated_forms = filtered_isolated_forms
            if not isolated_forms:
                return
        # The stance to replace is the bare candidate (when no forward replacement fired, for example because of a `not_before` guard) or its forward replacement at one of `base_name`'s plain entry Ys.
        pre_stances: set[str] = {candidate_name}
        for base_entry_y in base_meta.entry_ys:
            if base_entry_y in variant_entry_ys:
                continue
            pre_stance = candidate_fwd.get(base_entry_y)
            if pre_stance is not None:
                pre_stances.add(pre_stance)
        for isolated_form in isolated_forms:
            for pre_stance in pre_stances:
                if pre_stance == isolated_form:
                    continue
                pair_guard_reflip.setdefault(candidate_base, []).append(
                    (prior_slot, pre_stance, base_name, isolated_form)
                )

    def _record_fwd_pair_not_after_reflip(
        prior_slot: frozenset[str],
        target_name: str,
        follower_glyphs,
        isolated_form: str,
    ) -> None:
        # A forward-pair lookup's `not_after` guard ``ignore sub [prior_slot] target' [followers];`` leaves ``target_name`` unchanged, although shaping ``target follower`` in isolation gives ``isolated_form``. Record a re-flip ``sub [prior_slot] target_name' follower by isolated_form;`` for each follower. The entries use `_record_pair_guard_reflip`'s tuple, with the follower in the ``base_name`` slot.
        target_meta = glyph_meta.get(target_name)
        if target_meta is None:
            return
        candidate_base = target_meta.base_name
        bucket = pair_guard_reflip.setdefault(candidate_base, [])
        for follower in follower_glyphs:
            entry = (prior_slot, target_name, follower, isolated_form)
            if entry not in bucket:
                bucket.append(entry)

    for y in sorted(exit_classes):
        members = sorted(exit_classes[y])
        if members:
            lines.append(f"    @exit_y{y} = [{' '.join(members)}];")

    for y in sorted(fwd_used_ys):
        if y in entry_classes:
            members = sorted(entry_classes[y])
            lines.append(f"    @entry_y{y} = [{' '.join(members)}];")
        preferred_needs_excl = any(
            sibling_y == y for entries in fwd_preferred_lookahead.values() for _, _, sibling_y in entries
        )
        needs_excl = any(entry_y == y for _, entry_y in fwd_use_exclusive) or preferred_needs_excl
        if needs_excl and y in entry_exclusive:
            excl_members = sorted(entry_exclusive[y])
            if excl_members:
                lines.append(f"    @entry_only_y{y} = [{' '.join(excl_members)}];")

    for (exit_y, sibling_y), bridge_members in sorted(plan.preferred_lookahead_bridges.items()):
        if not bridge_members:
            continue
        lines.append(f"    @bridge_y{exit_y}_y{sibling_y} = [{' '.join(sorted(bridge_members))}];")

    def _expand_exclusions(excluded_glyphs: list[str]) -> set[str]:
        expanded = set()
        for excluded_glyph in excluded_glyphs:
            if excluded_glyph in glyph_meta and excluded_glyph != _base_name(excluded_glyph):
                expanded.add(excluded_glyph)
                continue
            excluded_base = _base_name(excluded_glyph)
            variants = base_to_variants.get(excluded_base)
            if variants:
                expanded.update(variants)
            else:
                expanded.add(excluded_glyph)
        return expanded

    zwnj = "uni200C"
    noentry_pairs = []
    for name in sorted(glyph_names):
        meta = _meta(name)
        if meta.is_noentry and name == f"{meta.base_name}.noentry":
            base = meta.base_name
            if base in glyph_names:
                noentry_pairs.append((base, name))
    if noentry_pairs:
        cursive_names = [base for base, _ in noentry_pairs]
        noentry_names = [name for _, name in noentry_pairs]
        lines.append(f"    @qs_has_entry = [{' '.join(cursive_names)}];")
        lines.append(f"    @qs_noentry = [{' '.join(noentry_names)}];")
        lines.append("")
        lines.append("    lookup calt_zwnj {")
        lines.append(f"        sub {zwnj} @qs_has_entry' by @qs_noentry;")
        lines.append("    } calt_zwnj;")

    if word_final_pairs:
        excluded_bases = {"qsAngleParenLeft", "qsAngleParenRight"}
        qs_letter_names = set()
        for name in glyph_names:
            if not name.startswith("qs"):
                continue
            meta = _meta(name)
            base = meta.base_name
            if base in excluded_bases:
                continue
            if name == base and not meta.sequence:
                qs_letter_names.add(name)
        qs_letter_names.update(word_final_pairs.values())
        qs_letters_sorted = " ".join(sorted(qs_letter_names))

        lines.append("")
        lines.append(f"    @qs_letters = [{qs_letters_sorted}];")

        for base, variant in sorted(word_final_pairs.items()):
            safe = variant.replace(".", "_")
            lines.append("")
            lines.append(f"    lookup calt_word_final_{safe} {{")
            lines.append(f"        sub {base} by {variant};")
            lines.append(f"    }} calt_word_final_{safe};")
            lines.append("")
            lines.append(f"    lookup calt_word_final_revert_{safe} {{")
            lines.append(f"        sub {variant}' @qs_letters by {base};")
            lines.append(f"    }} calt_word_final_revert_{safe};")

    def _excl_tokens(
        fwd_excl: list[str] | None,
        fwd_excl_sequences: list[tuple[str, ...]],
    ) -> list[str]:
        tokens: list[str] = []
        if fwd_excl:
            tokens.extend(sorted(_expand_exclusions(fwd_excl)))
        tokens.extend(" ".join(seq) for seq in fwd_excl_sequences)
        return tokens

    def _emit_pending_bk_entry_guards(
        source_name: str,
        replacement_name: str,
        right_context_glyphs: set[str],
    ) -> None:
        replacement_entry_ys = set(_meta(replacement_name).all_entry_ys)
        for entry_y in sorted(exit_classes):
            if entry_y in replacement_entry_ys:
                continue
            guards = _derived_bk_guards.get(
                (source_name, replacement_name, entry_y),
            )
            if not guards:
                continue
            for guard in guards:
                allowed_before = set(right_context_glyphs)
                if guard.before_bases:
                    allowed_before &= _expand_all_variants(guard.before_bases, include_base=True)
                if not allowed_before:
                    continue
                guard_list = " ".join(sorted(guard.guard_glyphs))
                before_list = " ".join(sorted(allowed_before))
                lines.append(f"        ignore sub [{guard_list}] {source_name}' [{before_list}];")

    def _preserved_before_contexts(
        source_name: str,
        replacement_name: str,
        entry_y: int,
        left_guard_name: str,
    ) -> set[str]:
        preserved: set[str] = set()
        for guard in _matching_pending_bk_guards(
            source_name,
            replacement_name,
            entry_y,
            left_guard_name,
        ):
            if left_guard_name not in guard.guard_glyphs:
                continue
            if not guard.before_bases:
                return set(glyph_names)
            preserved.update(_expand_all_variants(guard.before_bases, include_base=True))
        return preserved

    def _matching_pending_bk_guards(
        source_name: str,
        replacement_name: str,
        entry_y: int,
        left_guard_name: str,
    ) -> tuple[DerivedBkGuard, ...]:
        return tuple(
            guard
            for guard in _derived_bk_guards.get((source_name, replacement_name, entry_y), ())
            if left_guard_name in guard.guard_glyphs
        )

    def _source_strips_own_exit_before_mid(
        source_name: str,
        replacement_name: str,
        mid_source: str,
    ) -> bool:
        # The calling guard blocks `source_name → replacement_name` because `mid_source` loses its entry, through a forward substitution (·Thaw before ·-ing) or an entryless ligature (·Tea·Oy), leaving `replacement_name`'s exit unjoined. Return True when a forward pair override on `source_name`'s base has `replacement_name`'s entry Ys, has no exit, and lists the stripped `mid_source` stance in its lookahead. That override removes the exit later, so the promotion can stay and keep its left-side join. In ·Utter·Gay·Thaw·-ing and ·Utter·Gay·Tea·Oy, ·Gay keeps its ·Utter entry through `qsGay.en-y5.ex-noentry`.
        replacement_entry_ys = set(_meta(replacement_name).entry_ys)
        if not replacement_entry_ys:
            return False
        mid_base = _base_name(mid_source)
        # The entryless stances `mid_source` can become: its forward replacements, its forward pair overrides (`qsThaw.ex-y0` before ·-ing), and `mid_source` itself when it is an entryless variant.
        stripped_mid_stances = {
            fwd_var for fwd_var in fwd_replacements.get(mid_base, {}).values() if not _meta(fwd_var).entry
        }
        stripped_mid_stances.update(
            override_var
            for override_var, _, _ in fwd_pair_overrides.get(mid_base, [])
            if not _meta(override_var).entry
        )
        if mid_source != mid_base and not _meta(mid_source).entry:
            stripped_mid_stances.add(mid_source)
        # An entryless ligature led by `mid_base` (`qsTea_qsOy`) also leaves `replacement_name`'s exit unjoined once `calt_liga` runs. Before `calt_liga` the lead is still a separate glyph, so without this the guard would block the promotion even though the source's `.ex-noentry` override, whose `before` lists the ligature, removes the exit after ligation. Adding the ligature gives it the same opt-out the guard already grants for `noentry_after` ligatures.
        for lig_name, components in ligatures_by_first_component.get(mid_base, ()):
            if components and components[0] == mid_base and not _meta(lig_name).entry:
                stripped_mid_stances.add(lig_name)
        if not stripped_mid_stances:
            return False
        for override_variant, override_before, _ in fwd_pair_overrides.get(_base_name(source_name), []):
            override_meta = _meta(override_variant)
            if override_meta.exit:
                continue
            if set(override_meta.entry_ys) != replacement_entry_ys:
                continue
            admitted = _expand_all_variants(override_before, include_base=True)
            if stripped_mid_stances & admitted:
                return True
        return False

    def _emit_pending_fwd_exit_guards(
        source_name: str,
        replacement_name: str,
        exit_y: int,
        right_context_glyphs: set[str],
    ) -> None:
        if exit_y not in _meta(replacement_name).exit_ys:
            return

        emitted: set[tuple[str, tuple[str, ...]]] = set()

        def _emit_guard(mid_source: str, before_glyphs: set[str]) -> None:
            if not before_glyphs:
                return
            before_tuple = tuple(sorted(before_glyphs))
            key = (mid_source, before_tuple)
            if key in emitted:
                return
            emitted.add(key)
            before_list = " ".join(before_tuple)
            lines.append(f"        ignore sub {source_name}' {mid_source} [{before_list}];")

        def _matching_entry_only_source(mid_source: str) -> str | None:
            mid_base = _base_name(mid_source)
            for _, bk_var in sorted(bk_replacements.get(mid_base, {}).items()):
                if _meta(bk_var).exit:
                    continue
                if mid_source == bk_var:
                    return bk_var
                for ext_suffix in _ENTRY_EXTENSION_SUFFIXES:
                    ext_bk = f"{bk_var}{ext_suffix}"
                    if ext_bk in glyph_meta and not _meta(ext_bk).exit and mid_source == ext_bk:
                        return bk_var
            return None

        for mid_source in sorted(right_context_glyphs):
            mid_meta = _meta(mid_source)
            mid_base = mid_meta.base_name

            if _source_strips_own_exit_before_mid(source_name, replacement_name, mid_source):
                continue

            if mid_source == mid_base:
                for mid_exit_y, mid_replacement in sorted(fwd_replacements.get(mid_base, {}).items()):
                    if mid_exit_y not in entry_classes:
                        continue
                    if exit_y in set(_meta(mid_replacement).all_entry_ys):
                        continue
                    if not _matching_pending_bk_guards(
                        mid_source,
                        mid_replacement,
                        exit_y,
                        replacement_name,
                    ):
                        continue
                    fwd_bk_excl = plan.fwd_bk_exclusions.get(mid_base, {}).get(mid_exit_y)
                    if fwd_bk_excl and replacement_name in _expand_exclusions(fwd_bk_excl):
                        continue
                    use_excl = (mid_base, mid_exit_y) in fwd_use_exclusive
                    if use_excl and (mid_exit_y not in entry_exclusive or not entry_exclusive[mid_exit_y]):
                        continue
                    trigger_contexts = set(
                        entry_exclusive[mid_exit_y] if use_excl else entry_classes[mid_exit_y]
                    )
                    trigger_contexts -= _expand_exclusions(
                        fwd_exclusions.get(mid_base, {}).get(mid_exit_y, []),
                    )
                    trigger_contexts -= _preserved_before_contexts(
                        mid_source,
                        mid_replacement,
                        exit_y,
                        replacement_name,
                    )
                    # Drop followers whose backward upgrade at `mid_exit_y` lists `mid_base` in its `not_after`: they get no entry there, so `mid` is never stripped and needs no guard. `_emit_fwd_general`, `_emit_noentry_fwd_overrides`, and `_emit_narrow_mid_entry_strip_guards` apply the same filter.
                    blocked_by_mid_bk: set[str] = set()
                    for trigger_glyph in trigger_contexts:
                        trigger_meta = _meta(trigger_glyph)
                        if trigger_meta.entry and mid_exit_y in trigger_meta.entry_ys:
                            continue
                        trigger_base = trigger_meta.base_name
                        if mid_exit_y not in bk_replacements.get(trigger_base, {}):
                            continue
                        trigger_bk_excl_raw = bk_exclusions.get(trigger_base, {}).get(mid_exit_y, [])
                        if not trigger_bk_excl_raw:
                            continue
                        expanded_trigger_excl = _expand_exclusions(trigger_bk_excl_raw)
                        if mid_base in expanded_trigger_excl or (
                            set(base_to_variants.get(mid_base, ())) & expanded_trigger_excl
                        ):
                            blocked_by_mid_bk.add(trigger_glyph)
                    trigger_contexts -= blocked_by_mid_bk
                    _emit_guard(mid_source, trigger_contexts)
                    _emit_guard(mid_replacement, trigger_contexts)

            entry_only_source = _matching_entry_only_source(mid_source)
            if entry_only_source is None or mid_base not in fwd_replacements:
                continue
            if exit_y not in set(mid_meta.all_entry_ys):
                continue

            valid_overrides = []
            for mid_exit_y, mid_replacement in sorted(fwd_replacements[mid_base].items()):
                if mid_exit_y not in entry_classes:
                    continue
                if _meta(mid_replacement).entry:
                    continue
                has_upgrade = any(
                    entry_only == entry_only_source and ey == mid_exit_y
                    for _, entry_only, ey, _ in fwd_upgrades.get(mid_base, [])
                )
                if has_upgrade:
                    continue
                if exit_y in set(_meta(mid_replacement).all_entry_ys):
                    continue
                if not _matching_pending_bk_guards(
                    mid_source,
                    mid_replacement,
                    exit_y,
                    replacement_name,
                ):
                    continue
                fwd_bk_excl = plan.fwd_bk_exclusions.get(mid_base, {}).get(mid_exit_y)
                if fwd_bk_excl and replacement_name in _expand_exclusions(fwd_bk_excl):
                    continue
                valid_overrides.append((mid_exit_y, mid_replacement))

            if not valid_overrides:
                continue

            max_exit_y = max(mid_exit_y for mid_exit_y, _ in valid_overrides)
            for mid_exit_y, mid_replacement in valid_overrides:
                use_excl = len(valid_overrides) > 1 and mid_exit_y != max_exit_y
                if use_excl and (mid_exit_y not in entry_exclusive or not entry_exclusive[mid_exit_y]):
                    continue
                trigger_contexts = set(entry_exclusive[mid_exit_y] if use_excl else entry_classes[mid_exit_y])
                not_before = list(_meta(mid_replacement).not_before)
                if not_before:
                    resolved = resolve_known_glyph_names(not_before, glyph_names)
                    trigger_contexts -= _expand_exclusions(resolved)
                trigger_contexts -= _preserved_before_contexts(
                    mid_source,
                    mid_replacement,
                    exit_y,
                    replacement_name,
                )
                _emit_guard(mid_source, trigger_contexts)

    def _emit_narrow_mid_entry_strip_guards(
        source_name: str,
        replacement_name: str,
        exit_y: int,
        right_context_glyphs: set[str],
        *,
        left_context: str | None = None,
        require_mid_base_without_exit: bool = False,
    ) -> None:
        """Emit ``ignore sub`` rules that block ``source_name → replacement_name`` when the next glyph triggers the substitution with its entry but later loses that entry.

        The next glyph can lose its entry through a forward replacement (``fwd_replacements``), a forward pair override (``fwd_pair_overrides``), or a two-glyph ligature it leads. Without these guards, the source's exit joins nothing.
        """
        if replacement_name not in glyph_meta or exit_y not in _meta(replacement_name).exit_ys:
            return
        # Skip the guard when reverting to `source_name` would leave the same exit stroke, as with the Deep half stance `qsDay.half.en-y0.ex-y0`: it would remove nothing on the right and lose the left-side join. `_revert_keeps_reaching_exit`'s docstring has the `·It ~b~ ·Day.half` case.
        if _revert_keeps_reaching_exit(glyph_meta, source_name, replacement_name, exit_y):
            return

        emitted: set[tuple[str, tuple[str, ...]]] = set()

        def _emit_guard(mid_source: str, before_glyphs: set[str]) -> None:
            if not before_glyphs:
                return
            before_tuple = tuple(sorted(before_glyphs))
            key = (mid_source, before_tuple)
            if key in emitted:
                return
            emitted.add(key)
            before_list = " ".join(before_tuple)
            marked_source = (
                f"{left_context} {source_name}'" if left_context is not None else f"{source_name}'"
            )
            lines.append(f"        ignore sub {marked_source} {mid_source} [{before_list}];")

        def _fwd_pair_targets(base_name: str) -> set[str]:
            targets = {base_name}
            if base_name in bk_replacements:
                targets.update(bk_replacements[base_name].values())
            if base_name in fwd_replacements:
                targets.update(fwd_replacements[base_name].values())
            if base_name in pair_overrides:
                for pair_variant, _ in pair_overrides[base_name]:
                    targets.add(pair_variant)
            if base_name in fwd_upgrades:
                for entry_exit_var, _, _, _ in fwd_upgrades[base_name]:
                    targets.add(entry_exit_var)
            noentry_name = f"{base_name}.noentry"
            if noentry_name in glyph_names:
                targets.add(noentry_name)
            return targets

        def _entry_stripped_variants(stripped_name: str) -> set[str]:
            stripped_base = _base_name(stripped_name)
            prefix = stripped_name + "."
            candidates = {
                candidate
                for candidate in base_to_variants.get(stripped_base, ())
                if candidate == stripped_name or candidate.startswith(prefix)
            }
            candidates.add(stripped_name)
            return {
                candidate
                for candidate in candidates
                if candidate in glyph_meta and not set(_meta(candidate).all_entry_ys)
            }

        def _fwd_pair_actual_variant(
            target: str,
            pair_variant: str,
            expanded_before: set[str],
        ) -> tuple[str | None, set[str]]:
            pair_meta = _meta(pair_variant)
            target_meta = _meta(target)
            target_has_entry = bool(target_meta.entry)
            target_before: set[str] | None = None

            if pair_meta.entry:
                target_entry_ys = set(target_meta.entry_ys)
                pair_entry_ys = set(pair_meta.entry_ys)
                incompatible_ys = target_entry_ys - pair_entry_ys
                if incompatible_ys:
                    if incompatible_ys == target_entry_ys:
                        return None, set()
                    if exit_y in incompatible_ys and replacement_name in exit_classes.get(exit_y, set()):
                        return None, set()
            elif target_has_entry and target_meta.is_entry_variant:
                if target_meta.exit:
                    target_exit_ys = set(target_meta.exit_ys)
                    pair_exit_ys = set(pair_meta.exit_ys)
                    if pair_exit_ys <= target_exit_ys:
                        return None, set()
                    compatible = set()
                    for target_exit_y in target_exit_ys:
                        compatible.update(entry_classes.get(target_exit_y, set()) & expanded_before)
                    if compatible:
                        filtered = expanded_before - compatible
                        if not filtered:
                            return None, set()
                        target_before = filtered
                elif target_meta.after:
                    return None, set()

            actual_variant = pair_variant
            suffix = target_meta.extended_entry_suffix
            if suffix:
                extended = pair_variant + suffix
                if extended not in glyph_names:
                    extended = pair_variant + ".en-ext-1"
                if extended in glyph_names:
                    actual_variant = extended
            actual_variant = _resolve_noentry_replacement(
                glyph_meta,
                base_to_variants,
                target,
                actual_variant,
            )
            if actual_variant is None:
                return None, set()
            return actual_variant, set(target_before if target_before is not None else expanded_before)

        def _fwd_strip_guard_replacement_lookups(replacement_name: str) -> tuple[str, ...]:
            seen: set[str] = set()
            queue = deque([replacement_name.replace(".noentry", "")])
            ordered: list[str] = []
            while queue:
                candidate = queue.popleft()
                if candidate in seen:
                    continue
                seen.add(candidate)
                ordered.append(candidate)
                candidate_meta = glyph_meta.get(candidate)
                if candidate_meta is None:
                    continue
                suffixes = [
                    candidate_meta.extended_entry_suffix,
                    candidate_meta.contracted_entry_suffix,
                ]
                suffixes.extend(
                    f".{modifier}" for modifier in candidate_meta.modifiers if modifier.startswith("en-trim-")
                )
                for suffix in suffixes:
                    if suffix and suffix in candidate:
                        queue.append(candidate.replace(suffix, ""))
            if replacement_name not in seen:
                ordered.append(replacement_name)
            return tuple(ordered)

        # _derived_fwd_strip_guards is keyed by the predecessor's base name.
        source_meta = glyph_meta.get(source_name)
        source_lookup_base = source_meta.base_name if source_meta is not None else source_name
        guard_entries = tuple(
            guard
            for replacement_lookup in _fwd_strip_guard_replacement_lookups(replacement_name)
            for guard in _derived_fwd_strip_guards.get((source_lookup_base, replacement_lookup, exit_y), ())
        )
        derived_strip_bases = frozenset(guard.mid_base for guard in guard_entries)
        fwd_strip_bases = derived_strip_bases if _fwd_strip_guards_active[0] else frozenset()

        replacement_exit_ys = set(_meta(replacement_name).exit_ys) if _meta(replacement_name).exit else set()

        left_strip_protection_name: str | None = None
        if source_name in exit_classes.get(exit_y, set()):
            left_strip_protection_name = source_name
        elif not _fwd_strip_guards_active[0] and replacement_name in exit_classes.get(exit_y, set()):
            left_strip_protection_name = replacement_name

        def _left_context_protects_generic_fwd_strip(mid_base: str, mid_replacement: str) -> bool:
            if source_meta is not None and source_meta.is_noentry:
                return False
            if left_strip_protection_name is None:
                return False
            base_entry_ys = {y for y, members in entry_classes.items() if mid_base in members}
            replacement_entry_ys = set(_meta(mid_replacement).all_entry_ys)
            return exit_y in base_entry_ys - replacement_entry_ys

        def _left_context_protects_pair_fwd_strip(mid_base: str, actual_mid_variant: str) -> bool:
            if source_meta is not None and source_meta.is_noentry:
                return False
            if left_strip_protection_name is None:
                return False
            if exit_y in set(_meta(actual_mid_variant).all_entry_ys):
                return False
            bk_variant = bk_replacements.get(mid_base, {}).get(exit_y)
            if bk_variant is None:
                return False
            return exit_y in set(_meta(bk_variant).all_entry_ys)

        def _entry_preserving_followers(mid_base: str) -> set[str]:
            """Return the followers (third glyph of `source' mid follower`) before which `mid` keeps its entry, so the strip guard should leave them out.

            They are the `before` followers of each forward pair override of `mid_base` that has an entry meeting the replacement's exit and whose `not_after` doesn't list the replacement. Such an override fires either on the stance `mid` takes from a backward upgrade, sorting before the entry-stripping override because it has more modifiers, or directly on bare `mid_base` after a predecessor that exits at its entry Y.
            """
            preserved: set[str] = set()
            if not replacement_exit_ys:
                return preserved
            for pair_variant, pair_before, pair_not_after in fwd_pair_overrides.get(mid_base, []):
                pair_meta = _meta(pair_variant)
                if not pair_meta.entry:
                    continue
                if not (set(pair_meta.entry_ys) & replacement_exit_ys):
                    continue
                if pair_not_after:
                    expanded_not_after = _expand_all_variants(pair_not_after, include_base=True)
                    if replacement_name in expanded_not_after:
                        continue
                preserved.update(_expand_all_variants(pair_before))
            return preserved

        for mid_source in sorted(right_context_glyphs):
            if mid_source not in glyph_meta:
                continue
            mid_meta = _meta(mid_source)
            mid_base = mid_meta.base_name
            if source_lookup_base == "qsGay" and mid_base in {"qsIt", "qsI", "qsExam"}:
                continue
            if _source_strips_own_exit_before_mid(source_name, replacement_name, mid_source):
                continue
            has_entry_at_y = exit_y in set(mid_meta.all_entry_ys)
            # A bare base with no entry at `exit_y` fails `has_entry_at_y`. It still needs a guard when the derived forward-strip guards name it, in three cases. `bare_base_relax`: after `calt_cycle`, when it has already been forward-stripped. `bare_base_pair_relax`: before or after the cycle, when it has forward pair overrides, because this lookup sees the stripping right context in its lookahead. `bare_base_noentry_fwd_relax`: when the source is a `.noentry` stance.
            bare_base_relax = not has_entry_at_y and mid_source == mid_base and mid_source in fwd_strip_bases
            bare_base_pair_relax = (
                not has_entry_at_y
                and mid_source == mid_base
                and mid_source in derived_strip_bases
                and mid_base in fwd_pair_overrides
            )
            bare_base_noentry_fwd_relax = (
                not has_entry_at_y
                and mid_source == mid_base
                and mid_source in derived_strip_bases
                and source_meta is not None
                and source_meta.is_noentry
            )
            if (
                not has_entry_at_y
                and not bare_base_relax
                and not bare_base_pair_relax
                and not bare_base_noentry_fwd_relax
            ):
                continue
            if require_mid_base_without_exit and _meta(mid_base).exit:
                continue

            if mid_source == mid_base and (has_entry_at_y or bare_base_relax or bare_base_noentry_fwd_relax):
                for mid_exit_y, mid_replacement in sorted(fwd_replacements.get(mid_base, {}).items()):
                    if mid_exit_y not in entry_classes:
                        continue
                    if exit_y in set(_meta(mid_replacement).all_entry_ys):
                        continue
                    fwd_bk_excl = plan.fwd_bk_exclusions.get(mid_base, {}).get(mid_exit_y)
                    if fwd_bk_excl and replacement_name in _expand_exclusions(fwd_bk_excl):
                        continue
                    mid_use_excl = (mid_base, mid_exit_y) in fwd_use_exclusive
                    if mid_use_excl and (
                        mid_exit_y not in entry_exclusive or not entry_exclusive[mid_exit_y]
                    ):
                        continue
                    trigger_contexts = set(
                        entry_exclusive[mid_exit_y] if mid_use_excl else entry_classes[mid_exit_y]
                    )
                    trigger_contexts -= _expand_exclusions(
                        fwd_exclusions.get(mid_base, {}).get(mid_exit_y, []),
                    )
                    trigger_contexts -= _preserved_before_contexts(
                        mid_source,
                        mid_replacement,
                        exit_y,
                        replacement_name,
                    )
                    if _left_context_protects_generic_fwd_strip(mid_base, mid_replacement):
                        trigger_contexts.clear()
                    if not bare_base_noentry_fwd_relax:
                        trigger_contexts -= _entry_preserving_followers(mid_base)
                    # Drop followers whose backward upgrade at `mid_exit_y` lists `mid_base` in its `not_after`: they get no entry there, so `mid` is never stripped and needs no guard.
                    blocked_by_mid_bk: set[str] = set()
                    for trigger_glyph in trigger_contexts:
                        trigger_meta = _meta(trigger_glyph)
                        if trigger_meta.entry and mid_exit_y in trigger_meta.entry_ys:
                            continue
                        trigger_base = trigger_meta.base_name
                        if mid_exit_y not in bk_replacements.get(trigger_base, {}):
                            continue
                        trigger_bk_excl_raw = bk_exclusions.get(trigger_base, {}).get(mid_exit_y, [])
                        if not trigger_bk_excl_raw:
                            continue
                        expanded_trigger_excl = _expand_exclusions(trigger_bk_excl_raw)
                        if mid_base in expanded_trigger_excl or (
                            set(base_to_variants.get(mid_base, ())) & expanded_trigger_excl
                        ):
                            blocked_by_mid_bk.add(trigger_glyph)
                    trigger_contexts -= blocked_by_mid_bk
                    if bare_base_relax and not bare_base_noentry_fwd_relax:
                        # `entry_classes` also lists bare bases of entry-bearing variants and their entryless forward replacements, so post-cycle rules see entries added at run time. That is too broad here: when the third glyph is a bare base whose backward replacement at `mid_exit_y` has the entry, the backward replacement fires and `mid` is never forward-stripped. Keeping only glyphs with their own entry at `mid_exit_y` keeps ·Gay·Tea·Tea joined and keeps the guard for ·Gay·Tea·Ah (qsAh enters at y=0).
                        trigger_contexts = {
                            g for g in trigger_contexts if mid_exit_y in set(_meta(g).all_entry_ys)
                        }
                    _emit_guard(mid_source, trigger_contexts)
                    for stripped_variant in sorted(_entry_stripped_variants(mid_replacement)):
                        _emit_guard(stripped_variant, trigger_contexts)

            if bare_base_relax and not bare_base_pair_relax:
                continue

            pair_targets = _fwd_pair_targets(mid_base)
            if mid_source not in pair_targets:
                continue
            emitted_before_lists: set[tuple[str, ...]] = set()
            for mid_variant, mid_before_glyphs, not_after_glyphs in fwd_pair_overrides.get(mid_base, []):
                expanded_mid_before = _expand_all_variants(mid_before_glyphs)
                if not expanded_mid_before:
                    continue
                if not_after_glyphs:
                    expanded_not_after = _expand_all_variants(
                        not_after_glyphs,
                        include_base=True,
                    )
                    if replacement_name in expanded_not_after:
                        continue
                actual_mid_variant, effective_before = _fwd_pair_actual_variant(
                    mid_source,
                    mid_variant,
                    expanded_mid_before,
                )
                if actual_mid_variant is None:
                    continue
                if exit_y in set(_meta(actual_mid_variant).all_entry_ys):
                    continue
                if _left_context_protects_pair_fwd_strip(mid_base, actual_mid_variant):
                    continue
                effective_before -= _preserved_before_contexts(
                    mid_source,
                    actual_mid_variant,
                    exit_y,
                    replacement_name,
                )
                effective_before -= _entry_preserving_followers(mid_base)
                # Drop followers whose backward upgrade at `exit_y` lists `mid_base` in its `not_after`: they get no entry there, so `mid` is never stripped and needs no guard.
                pair_blocked_by_mid_bk: set[str] = set()
                for trigger_glyph in effective_before:
                    trigger_meta = _meta(trigger_glyph)
                    if trigger_meta.entry and exit_y in trigger_meta.entry_ys:
                        continue
                    trigger_base = trigger_meta.base_name
                    if exit_y not in bk_replacements.get(trigger_base, {}):
                        continue
                    trigger_bk_excl_raw = bk_exclusions.get(trigger_base, {}).get(exit_y, [])
                    if not trigger_bk_excl_raw:
                        continue
                    expanded_trigger_excl = _expand_exclusions(trigger_bk_excl_raw)
                    if mid_base in expanded_trigger_excl or (
                        set(base_to_variants.get(mid_base, ())) & expanded_trigger_excl
                    ):
                        pair_blocked_by_mid_bk.add(trigger_glyph)
                effective_before -= pair_blocked_by_mid_bk
                before_tuple = tuple(sorted(effective_before))
                if before_tuple in emitted_before_lists:
                    continue
                emitted_before_lists.add(before_tuple)
                _emit_guard(mid_source, set(before_tuple))
                if mid_source == mid_base:
                    for stripped_variant in sorted(_entry_stripped_variants(actual_mid_variant)):
                        _emit_guard(stripped_variant, set(before_tuple))

        for mid_source in sorted(right_context_glyphs):
            if mid_source not in glyph_meta:
                continue
            mid_meta = _meta(mid_source)
            mid_base = mid_meta.base_name
            if require_mid_base_without_exit and _meta(mid_base).exit:
                continue
            if _source_strips_own_exit_before_mid(source_name, replacement_name, mid_source):
                continue
            for lig_name, components in ligatures_by_first_component.get(mid_base, ()):
                if len(components) != 2:
                    continue
                lig_variant_entry_ys: set[int] = set()
                for lig_variant in base_to_variants.get(lig_name, {lig_name}):
                    if lig_variant in glyph_meta:
                        lig_variant_entry_ys.update(_meta(lig_variant).all_entry_ys)
                if exit_y in lig_variant_entry_ys:
                    continue
                first_component_variants = _ligature_component_variants(lig_name, components[0], 0)
                if mid_source not in first_component_variants:
                    continue
                trigger_contexts = _ligature_component_variants(lig_name, components[1], 1)
                if not trigger_contexts:
                    continue
                _emit_guard(mid_source, trigger_contexts)

    def _emit_entry_strip_guards_for_replacement_exit(
        source_name: str,
        replacement_name: str,
        *,
        left_context: str | None = None,
    ) -> None:
        for replacement_exit_y in sorted(set(_meta(replacement_name).exit_ys)):
            if replacement_exit_y not in entry_classes:
                continue
            _emit_narrow_mid_entry_strip_guards(
                source_name,
                replacement_name,
                replacement_exit_y,
                set(entry_classes[replacement_exit_y]),
                left_context=left_context,
                require_mid_base_without_exit=True,
            )

    def _exit_extension_refinements(
        fwd_var: str, right_context_glyphs: set[str]
    ) -> list[tuple[str, set[str]]]:
        # Return `(extended_fwd_var, trigger_glyphs)` for each `extend_exit_before` rule on `fwd_var` whose extended glyph exists and whose target variants appear in `right_context_glyphs`.
        fwd_meta = _meta(fwd_var)
        refinements: list[tuple[str, set[str]]] = []
        for spec in fwd_meta.extend_exit_before:
            if not spec.targets:
                continue
            suffix_word = _EXIT_EXTENSION_WORD_BY_COUNT.get(spec.by)
            if suffix_word is None:
                continue
            extended_fwd_var = f"{fwd_var}.ex-{suffix_word}"
            if extended_fwd_var not in glyph_meta:
                continue
            trigger_glyphs: set[str] = set()
            for target in spec.targets:
                trigger_glyphs.update(base_to_variants.get(target, ()))
            trigger_glyphs &= right_context_glyphs
            if not trigger_glyphs:
                continue
            refinements.append((extended_fwd_var, trigger_glyphs))
        return refinements

    def _expand_not_after_set(replacement_name: str) -> set[str]:
        meta = _meta(replacement_name)
        if not meta.not_after:
            return set()
        resolved = resolve_known_glyph_names(list(meta.not_after), glyph_names)
        return _expand_exclusions(resolved)

    def _emit_fpt_revert(
        fpt: str,
        default_replacement: str,
        *,
        member_set: set[str],
        member_list_token: str | None = None,
    ) -> None:
        # Emit the rules that replace the forward-pair variant `fpt` with a backward replacement after `member_set`. When `_refined_bk_replacement` picks a different glyph whose `not_after` lists some members, those members get `default_replacement` and the rest get the refined glyph.
        fpt_replacement = _refined_bk_replacement(fpt, default_replacement)
        if fpt_replacement == default_replacement:
            token = member_list_token or f"[{' '.join(sorted(member_set))}]"
            _emit_entry_strip_guards_for_replacement_exit(
                fpt,
                default_replacement,
                left_context=token,
            )
            lines.append(f"        sub {token} {fpt}' by {default_replacement};")
            return
        not_after_set = _expand_not_after_set(fpt_replacement)
        excluded = member_set & not_after_set
        usable = member_set - not_after_set
        if not excluded:
            token = member_list_token or f"[{' '.join(sorted(member_set))}]"
            _emit_entry_strip_guards_for_replacement_exit(
                fpt,
                fpt_replacement,
                left_context=token,
            )
            lines.append(f"        sub {token} {fpt}' by {fpt_replacement};")
            return
        if excluded:
            excl_list = " ".join(sorted(excluded))
            _emit_entry_strip_guards_for_replacement_exit(
                fpt,
                default_replacement,
                left_context=f"[{excl_list}]",
            )
            lines.append(f"        sub [{excl_list}] {fpt}' by {default_replacement};")
        if usable:
            usable_list = " ".join(sorted(usable))
            _emit_entry_strip_guards_for_replacement_exit(
                fpt,
                fpt_replacement,
                left_context=f"[{usable_list}]",
            )
            lines.append(f"        sub [{usable_list}] {fpt}' by {fpt_replacement};")

    def _refined_bk_replacement(fpt: str, default_replacement: str) -> str:
        """Return the backward replacement for the forward-pair variant `fpt`, keeping `fpt`'s exit extension when possible.

        `fpt` is replaced because its entry doesn't fit the preceding glyph; `default_replacement` is the backward replacement for that glyph. When `fpt` has an exit-extension suffix, the result is the first that applies:

        1. `default_replacement`, when ``default_replacement + ext_suffix`` exists but some follower family in `fpt`'s `before` has no entry at its exit Y, so the extension would reach toward nothing. In ·May·It·Owe, qsOwe never enters at the baseline, so ``qsIt.en-y5.ex-y0.ex-ext-1`` would leave its last pixel unjoined. The entryless-sibling fallback is skipped too, because it would only move that ink to the predecessor's side.
        2. ``default_replacement + ext_suffix``, when its exit Y is one that `fpt`'s base takes before those followers when shaped in isolation (`_isolated_exit_ys_for_fpt`), even if its bitmap differs from `fpt`'s. This keeps ``·Ah ·It ·Zoo`` matching ``·It ·Zoo`` on the ·It side: ``qsIt.en-y5.ex-y0.ex-ext-1``, not an entryless sibling.
        3. ``default_replacement + ext_suffix``, when its exit Ys are compatible with `fpt`'s and its bitmap and y offset match.
        4. An entryless, non-`.noentry`, ungated sibling of `fpt` with the same extension suffix, exit Ys, bitmap, and y offset, whose exit every follower family accepts (``qsTea.ex-y0.ex-ext-1`` for ``qsTea.en-y8.ex-y0.ex-ext-1``). It drops the entry that doesn't fit and keeps the extension into the next glyph.
        5. `default_replacement` otherwise.
        """
        fpt_meta = _meta(fpt)
        ext_suffix = fpt_meta.extended_exit_suffix
        if not ext_suffix:
            return default_replacement
        fpt_exit_ys = set(fpt_meta.exit_ys)
        candidate = default_replacement + ext_suffix
        cand_meta = glyph_meta.get(candidate)
        isolated_exit_ys = _isolated_exit_ys_for_fpt(fpt, fpt_meta)
        if cand_meta is not None:
            cand_exit_ys = set(cand_meta.exit_ys)
            if not _all_follower_families_accept(fpt, fpt_meta, cand_exit_ys):
                # Skip the sibling fallback too. It is for a candidate glyph that doesn't exist. Here the follower is the problem, and an entryless sibling would move the unjoined ink to the predecessor's side, because the predecessor took its `.ex-ext-1` stance expecting `fpt`'s old entry Y.
                return default_replacement
            if isolated_exit_ys and cand_exit_ys & isolated_exit_ys:
                return candidate
            if (
                (not fpt_exit_ys or not cand_exit_ys or fpt_exit_ys & cand_exit_ys)
                and cand_meta.bitmap == fpt_meta.bitmap
                and cand_meta.y_offset == fpt_meta.y_offset
            ):
                return candidate
        if fpt_meta.entry or fpt_meta.entry_curs_only:
            for sibling in sorted(base_to_variants.get(fpt_meta.base_name, ())):
                sib_meta = _meta(sibling)
                if sib_meta.entry or sib_meta.entry_curs_only:
                    continue
                if sib_meta.is_noentry:
                    continue
                if sib_meta.gate_feature:
                    continue
                if sib_meta.extended_exit_suffix != ext_suffix:
                    continue
                if set(sib_meta.exit_ys) != fpt_exit_ys:
                    continue
                if sib_meta.bitmap != fpt_meta.bitmap:
                    continue
                if sib_meta.y_offset != fpt_meta.y_offset:
                    continue
                if not _all_follower_families_accept(fpt, fpt_meta, set(sib_meta.exit_ys)):
                    continue
                return sibling
        return default_replacement

    def _all_follower_families_accept(fpt: str, fpt_meta, cand_exit_ys: set[int]) -> bool:
        """Return whether every follower family in `fpt`'s `before` has a variant entering at one of ``cand_exit_ys``, so an exit extension at those Ys always has something to join. Empty ``cand_exit_ys`` or an empty `before` returns True."""
        if not cand_exit_ys:
            return True
        before_glyphs = _resolve_fpt_before(fpt, fpt_meta.base_name)
        if not before_glyphs:
            return True
        seen_families: set[str] = set()
        for follower in before_glyphs:
            follower_meta = glyph_meta.get(follower)
            family_base = follower_meta.base_name if follower_meta is not None else follower
            if family_base in seen_families:
                continue
            seen_families.add(family_base)
            family_entry_ys: set[int] = set()
            for variant_name in base_to_variants.get(family_base, ()) or {follower}:
                variant_meta = glyph_meta.get(variant_name)
                if variant_meta is None:
                    continue
                family_entry_ys.update(variant_meta.all_entry_ys)
            if not (family_entry_ys & cand_exit_ys):
                return False
        return True

    def _isolated_exit_ys_for_fpt(fpt: str, fpt_meta) -> set[int]:
        """Return the exit Ys `fpt`'s base takes before `fpt`'s followers when shaped in isolation: the followers' entry Ys at which ``fwd_replacements`` has a replacement for the base."""
        base_name = fpt_meta.base_name
        base_fwd = fwd_replacements.get(base_name)
        if not base_fwd:
            return set()
        candidate_exit_ys = set(base_fwd.keys())
        if not candidate_exit_ys:
            return set()
        before_glyphs = _resolve_fpt_before(fpt, base_name)
        if not before_glyphs:
            return set()
        expanded_followers = _expand_all_variants(list(before_glyphs))
        follower_entry_ys: set[int] = set()
        for follower in expanded_followers:
            f_meta = glyph_meta.get(follower)
            if f_meta is None:
                continue
            follower_entry_ys.update(f_meta.all_entry_ys)
        return follower_entry_ys & candidate_exit_ys

    def _resolve_fpt_before(fpt: str, base_name: str) -> set[str]:
        """Return `fpt`'s resolved ``before`` glyphs from ``fwd_pair_overrides[base_name]``; the ``before`` on its metadata is unresolved."""
        for fwd_variant, before_glyphs, _not_after in fwd_pair_overrides.get(base_name, ()):
            if fwd_variant == fpt:
                return set(before_glyphs)
        return set()

    def _entry_extension_source_glyphs(actual_variant: str, extension_variant: str) -> set[str]:
        extension_meta = glyph_meta.get(extension_variant)
        actual_meta = glyph_meta.get(actual_variant)
        if extension_meta is None or actual_meta is None:
            return set()
        if extension_meta.extended_entry_suffix is None:
            return set()

        source_glyphs: set[str] = set()
        for sibling_name in sorted(base_to_variants.get(actual_meta.base_name, ())):
            sibling_meta = glyph_meta.get(sibling_name)
            if sibling_meta is None or not sibling_meta.extend_entry_after:
                continue
            sibling_extension = sibling_name + extension_meta.extended_entry_suffix
            if sibling_extension not in glyph_names:
                sibling_extension = sibling_name + ".en-ext-1"
            if sibling_extension not in glyph_names:
                continue
            sibling_extension_meta = glyph_meta.get(sibling_extension)
            if sibling_extension_meta is None:
                continue
            if sibling_extension_meta.bitmap != extension_meta.bitmap:
                continue
            flat_targets = [t for rule in sibling_meta.extend_entry_after for t in rule.targets]
            resolved = resolve_known_glyph_names(flat_targets, glyph_names)
            source_glyphs.update(_expand_all_variants(resolved, include_base=True))
        return source_glyphs & glyph_names

    def _emit_fwd_pairs(base_name: str, *, lookup_prefix: str = "calt_fwd_pair_"):
        if base_name in fwd_pair_overrides:
            sorted_overrides = sorted(
                fwd_pair_overrides[base_name],
                key=lambda item: _backward_pair_sort_key(glyph_meta, item[0], item[1]),
            )
            for variant_name, before_glyphs, not_after_glyphs in sorted_overrides:
                expanded_before = _expand_forward_before_variants(
                    variant_name,
                    before_glyphs,
                    analysis=plan,
                )
                source_meta = _meta(variant_name)
                before_list = " ".join(sorted(expanded_before))

                targets = {base_name}
                if base_name in bk_replacements:
                    targets.update(bk_replacements[base_name].values())
                if base_name in fwd_replacements:
                    targets.update(fwd_replacements[base_name].values())
                if base_name in pair_overrides:
                    for pair_variant, _ in pair_overrides[base_name]:
                        targets.add(pair_variant)
                if base_name in fwd_upgrades:
                    for entry_exit_var, _, _, _ in fwd_upgrades[base_name]:
                        targets.add(entry_exit_var)
                noentry_name = f"{base_name}.noentry"
                if noentry_name in glyph_names:
                    targets.add(noentry_name)

                variant_meta = _meta(variant_name)
                variant_entry_ys = set(variant_meta.entry_ys) if variant_meta.entry else None

                # When the variant changes only its exit, add the targets' entry-side derivations (extended, contracted, trimmed), and the reverse when it changes only its entry. The override then also matches a glyph an earlier lookup already changed on the other side: a ·Tea stance that took `en-ext-1` after ·Key still gets its exit contraction before ·Zoo.
                variant_is_exit_side = bool(
                    variant_meta.extended_exit_suffix or variant_meta.contracted_exit_suffix
                )
                variant_is_entry_side = bool(
                    variant_meta.extended_entry_suffix or variant_meta.contracted_entry_suffix
                )
                orthogonal_derivations: set[str] = set()
                if variant_is_exit_side != variant_is_entry_side:
                    if variant_is_exit_side:
                        orthogonal_kinds = {
                            "en-ext-1",
                            "en-con-1",
                            "entry-trimmed",
                        }
                    else:
                        orthogonal_kinds = {
                            "ex-ext-1",
                            "ex-con-1",
                            "exit-trimmed",
                        }
                    derivation_seeds = set(targets)
                    derivation_queue = deque(derivation_seeds)
                    while derivation_queue:
                        parent = derivation_queue.popleft()
                        for child in generation_children.get(parent, ()):
                            if child in orthogonal_derivations:
                                continue
                            child_meta = glyph_meta.get(child)
                            if child_meta is None:
                                continue
                            if child_meta.transform_kind not in orthogonal_kinds:
                                continue
                            orthogonal_derivations.add(child)
                            derivation_queue.append(child)
                    targets.update(orthogonal_derivations)

                expanded_not_after = _expand_all_variants(not_after_glyphs, include_base=True)
                # After a ZWNJ, `calt_zwnj` swaps a letter for its `.noentry` stance, which `_expand_all_variants` doesn't return. Add the `not_after` families' `.noentry` stances, or a predecessor that follows a ZWNJ would escape the `not_after` guard.
                for not_after_glyph in not_after_glyphs:
                    not_after_base = (
                        glyph_meta[not_after_glyph].base_name
                        if not_after_glyph in glyph_meta
                        else not_after_glyph
                    )
                    for sibling in base_to_variants.get(not_after_base, ()):
                        if glyph_meta[sibling].is_noentry:
                            expanded_not_after.add(sibling)

                safe = variant_name.replace(".", "_")
                lines.append("")
                lookup_name = f"{lookup_prefix}{safe}"
                lines.append(f"    lookup {lookup_name} {{")
                for target in sorted(targets):
                    guard_list = None
                    target_before = None
                    partial_ignores: list[tuple[str, str]] = []
                    target_meta = _meta(target)
                    target_has_entry = bool(target_meta.entry)
                    if target_meta.is_entry_variant and target_meta.exit:
                        target_exit_ys = set(target_meta.exit_ys)
                        before_entry_ys: set[int] = set()
                        for before_glyph in expanded_before:
                            before_meta = _meta(before_glyph)
                            if before_meta.entry:
                                before_entry_ys.update(before_meta.entry_ys)
                        if before_entry_ys and not (target_exit_ys & before_entry_ys):
                            continue
                    if variant_entry_ys is not None:
                        if target_has_entry:
                            target_entry_ys = set(target_meta.entry_ys)
                            if not target_entry_ys.issubset(variant_entry_ys):
                                incompatible_ys = target_entry_ys - variant_entry_ys
                                if incompatible_ys == target_entry_ys:
                                    continue
                                guard_glyphs = set()
                                for incompatible_y in incompatible_ys:
                                    guard_glyphs.update(exit_classes.get(incompatible_y, set()))
                                if guard_glyphs:
                                    guard_list = " ".join(sorted(guard_glyphs))
                    else:
                        if target_has_entry and target_meta.is_entry_variant:
                            if target_meta.exit:
                                target_exit_ys = set(target_meta.exit_ys)
                                variant_exit_ys = set(variant_meta.exit_ys)
                                if variant_exit_ys <= target_exit_ys:
                                    continue
                                compatible = set()
                                for ty in target_exit_ys:
                                    compatible.update(entry_classes.get(ty, set()) & expanded_before)
                                if compatible:
                                    filtered = expanded_before - compatible
                                    if not filtered:
                                        continue
                                    target_before = filtered
                            elif target_meta.after and target not in orthogonal_derivations:
                                continue
                        elif not target_has_entry:
                            if variant_meta.exit:
                                variant_exit_ys = set(variant_meta.exit_ys)
                                base_for_target = target_meta.base_name
                                # Followers the override's own exit can join. A follower that can join both a backward replacement's exit and the override's exit gets the override, which the author wrote for it, so it is left out of the partial ignores below.
                                variant_reachable_followers: set[str] = set()
                                for variant_exit_y in variant_exit_ys:
                                    variant_reachable_followers.update(
                                        entry_classes.get(variant_exit_y, set()) & expanded_before
                                    )
                                protect_ys = set()
                                for bk_y, bk_var in bk_replacements.get(base_for_target, {}).items():
                                    bk_meta = _meta(bk_var)
                                    if bk_meta.exit:
                                        bk_exit_ys = set(bk_meta.exit_ys)
                                        if variant_exit_ys <= bk_exit_ys:
                                            protect_ys.add(bk_y)
                                        else:
                                            bk_reachable_followers = set()
                                            for bk_exit_y in bk_exit_ys:
                                                bk_reachable_followers.update(
                                                    entry_classes.get(bk_exit_y, set()) & expanded_before
                                                )
                                            if not bk_reachable_followers:
                                                protect_ys.add(bk_y)
                                                continue
                                            # Only followers that join the backward replacement's exit and not the override's need a partial ignore.
                                            compatible = bk_reachable_followers - variant_reachable_followers
                                            if compatible:
                                                ig_glyphs = exit_classes.get(bk_y, set())
                                                if ig_glyphs:
                                                    partial_ignores.append(
                                                        (
                                                            " ".join(sorted(ig_glyphs)),
                                                            " ".join(sorted(compatible)),
                                                        )
                                                    )
                                if protect_ys:
                                    guard_glyphs = set()
                                    for protect_y in protect_ys:
                                        # The backward upgrade at this Y doesn't fire after predecessors in its `not_after` (`bk_exclusions`), so they need no guard.
                                        guard_glyphs.update(
                                            exit_classes.get(protect_y, set())
                                            - _expand_exclusions(
                                                bk_exclusions.get(base_for_target, {}).get(protect_y, [])
                                            )
                                        )
                                    if guard_glyphs:
                                        guard_list = " ".join(sorted(guard_glyphs))
                    actual_variant = variant_name
                    suffix = target_meta.extended_entry_suffix
                    if suffix:
                        extended = variant_name + suffix
                        if extended not in glyph_names:
                            extended = variant_name + ".en-ext-1"
                        if extended in glyph_names:
                            actual_variant = extended
                    actual_variant = _resolve_noentry_replacement(
                        glyph_meta,
                        base_to_variants,
                        target,
                        actual_variant,
                    )
                    if actual_variant is None:
                        continue
                    effective_before = target_before if target_before is not None else expanded_before
                    effective_before_list = (
                        " ".join(sorted(effective_before)) if target_before is not None else before_list
                    )
                    actual_variant_meta = _meta(actual_variant)
                    actual_entry_ys = (
                        set(actual_variant_meta.entry_ys) if actual_variant_meta.entry else set()
                    )
                    entry_backtrack_prefix = ""
                    entry_backtrack_glyphs: set[str] = set()
                    entry_extension_backtrack_glyphs: set[str] = set()
                    if actual_entry_ys and not target_has_entry:
                        for entry_y in actual_entry_ys:
                            entry_extension_backtrack_glyphs.update(exit_classes.get(entry_y, set()))
                        entry_extension_backtrack_glyphs -= expanded_not_after
                        if "ex-noentry" in actual_variant_meta.modifiers or _entry_anchor_is_visual_addition(
                            glyph_meta,
                            base_to_variants,
                            actual_variant,
                        ):
                            entry_backtrack_glyphs = set(entry_extension_backtrack_glyphs)
                            if not entry_backtrack_glyphs:
                                continue
                            entry_backtrack_prefix = f"[{' '.join(sorted(entry_backtrack_glyphs))}] "
                    for pi_guard, pi_before in partial_ignores:
                        lines.append(f"        ignore sub [{pi_guard}] {target}' [{pi_before}];")
                    if guard_list:
                        lines.append(
                            f"        ignore sub [{guard_list}] {target}' [{effective_before_list}];"
                        )
                    if expanded_not_after:
                        not_after_list = " ".join(sorted(expanded_not_after))
                        for (
                            override_prior,
                            override_target,
                            override_follower,
                            override_iso,
                        ) in plan.restore_isolated_form_overrides:
                            target_base = _meta(target).base_name
                            if target_base != override_target:
                                continue
                            prior_matches = {
                                g for g in expanded_not_after if _meta(g).base_name == override_prior
                            }
                            if not prior_matches:
                                continue
                            follower_matches = {
                                g for g in effective_before if _meta(g).base_name == override_follower
                            }
                            if not follower_matches:
                                continue
                            if override_iso not in glyph_names:
                                continue
                            _record_fwd_pair_not_after_reflip(
                                frozenset(prior_matches),
                                target,
                                follower_matches,
                                override_iso,
                            )
                        lines.append(
                            f"        ignore sub [{not_after_list}] {target}' [{effective_before_list}];"
                        )
                    converting_to_exit_noentry = (
                        "ex-noentry" in actual_variant_meta.modifiers and not actual_variant_meta.exit
                    )
                    for terminal in sorted(effective_before & plan.terminal_exit_only):
                        # An `.ex-noentry` conversion removes `target`'s exit before a follower that can't join it, so don't block it before a follower with no entry at all (the `qsTea_qsOy` ligature). A follower with an entry stays guarded, because `target`'s exit could join it.
                        if converting_to_exit_noentry and not _meta(terminal).all_entry_ys:
                            continue
                        lines.append(f"        ignore sub {target}' {terminal};")
                    _emit_pending_bk_entry_guards(
                        target,
                        actual_variant,
                        effective_before,
                    )
                    for actual_exit_y in sorted(set(_meta(actual_variant).exit_ys)):
                        if actual_exit_y not in entry_classes:
                            continue
                        _emit_narrow_mid_entry_strip_guards(
                            target,
                            actual_variant,
                            actual_exit_y,
                            effective_before,
                        )
                    # `expand_selectors_for_ligatures` adds ligature lead glyphs to `before` and records the families that must follow each lead in `before_lig_lead_followups`. Each such lead gets its own two-glyph lookahead rule that requires one of those families next, so `qsIt' qsDay` doesn't fire when the `qsDay` won't become `qsDay_qsUtter`. The rest of `effective_before` gets one single-glyph lookahead rule.
                    source_meta_for_split = _meta(variant_name)
                    raw_lead_followups = dict(source_meta_for_split.before_lig_lead_followups)
                    lead_only_followups: dict[str, set[str]] = {}
                    for lead_glyph, trailing_families in raw_lead_followups.items():
                        if lead_glyph not in effective_before:
                            continue
                        trailings: set[str] = set()
                        for trailing_family in trailing_families:
                            trailings.update(base_to_variants.get(trailing_family, ()))
                        trailings = {g for g in trailings if g in glyph_names}
                        if not trailings:
                            continue
                        lead_only_followups[lead_glyph] = trailings
                    non_lead_before = effective_before - set(lead_only_followups)

                    def _emit_positive_forward_pair(replacement_variant: str, backtrack_prefix: str) -> None:
                        kept_non_lead = _select_rule_neighbors(
                            target, replacement_variant, non_lead_before, direction="fwd"
                        )
                        if kept_non_lead:
                            non_lead_before_list = " ".join(sorted(kept_non_lead))
                            lines.append(
                                f"        sub {backtrack_prefix}{target}' [{non_lead_before_list}] by {replacement_variant};"
                            )
                        kept_leads = _select_rule_neighbors(
                            target, replacement_variant, set(lead_only_followups), direction="fwd"
                        )
                        for lead_glyph in sorted(kept_leads):
                            trailings_list = " ".join(sorted(lead_only_followups[lead_glyph]))
                            lines.append(
                                f"        sub {backtrack_prefix}{target}' {lead_glyph} [{trailings_list}] by {replacement_variant};"
                            )

                    if entry_extension_backtrack_glyphs:
                        for ext_suffix in _ENTRY_EXTENSION_SUFFIXES:
                            extended_variant = actual_variant + ext_suffix
                            if extended_variant not in glyph_names:
                                continue
                            source_glyphs = entry_extension_backtrack_glyphs & _entry_extension_source_glyphs(
                                actual_variant, extended_variant
                            )
                            if not source_glyphs:
                                continue
                            source_prefix = f"[{' '.join(sorted(source_glyphs))}] "
                            _emit_positive_forward_pair(extended_variant, source_prefix)

                    _emit_positive_forward_pair(actual_variant, entry_backtrack_prefix)
                lines.append(f"    }} {lookup_name};")

    def _emit_fwd_general(
        base_name: str,
        *,
        only_exit_ys: set[int] | None = None,
        skip_exit_ys: set[int] | None = None,
        lookup_prefix: str = "calt_fwd_",
    ):
        if base_name not in fwd_replacements:
            return

        variants = fwd_replacements[base_name]
        selected_exit_ys = [
            exit_y
            for exit_y in sorted(variants.keys(), reverse=True)
            if (only_exit_ys is None or exit_y in only_exit_ys)
            and (skip_exit_ys is None or exit_y not in skip_exit_ys)
        ]
        if not selected_exit_ys:
            return

        exclusions = fwd_exclusions.get(base_name, {})
        lookup_name = f"{lookup_prefix}{base_name}"
        emitted = False
        lines.append("")
        lines.append(f"    lookup {lookup_name} {{")

        def _entry_bearing_strip_targets(exit_y: int, replacement_name: str) -> list[str]:
            if not _meta(replacement_name).strip_entry_before:
                return []

            replacement_meta = _meta(replacement_name)
            if _has_left_entry(replacement_meta):
                return []

            targets = []
            for target_name in sorted(base_to_variants.get(base_name, ())):
                target_meta = _meta(target_name)
                if target_name == base_name:
                    continue
                if target_meta.is_noentry:
                    continue
                if "ex-noentry" in target_meta.modifiers:
                    continue
                if target_meta.gate_feature:
                    continue
                if not _has_left_entry(target_meta):
                    continue
                if exit_y in set(target_meta.exit_ys):
                    continue
                targets.append(target_name)
            return targets

        for exit_y in selected_exit_ys:
            variant_name = variants[exit_y]
            if exit_y not in entry_classes:
                continue
            use_excl = (base_name, exit_y) in fwd_use_exclusive
            if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                continue
            cls = f"@entry_only_y{exit_y}" if use_excl else f"@entry_y{exit_y}"
            base_ey = {y for y, members in entry_classes.items() if base_name in members}
            var_ey = {y for y, members in entry_classes.items() if variant_name in members}
            if var_ey:
                for hidden_y in sorted(base_ey - var_ey):
                    if hidden_y in exit_classes:
                        lines.append(f"        ignore sub @exit_y{hidden_y} {base_name}' {cls};")
            fwd_bk_excl = plan.fwd_bk_exclusions.get(base_name, {}).get(exit_y)
            if fwd_bk_excl:
                for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                    lines.append(f"        ignore sub {bg} {base_name}' {cls};")
            excluded = _expand_exclusions(exclusions.get(exit_y, []))
            for excluded_glyph in sorted(excluded):
                lines.append(f"        ignore sub {base_name}' {excluded_glyph};")
            right_context_glyphs = set(entry_exclusive[exit_y] if use_excl else entry_classes[exit_y])
            effective_right_context_glyphs = right_context_glyphs - excluded
            _emit_pending_fwd_exit_guards(base_name, variant_name, exit_y, right_context_glyphs)
            _emit_pending_bk_entry_guards(base_name, variant_name, right_context_glyphs)
            _emit_narrow_mid_entry_strip_guards(
                base_name,
                variant_name,
                exit_y,
                effective_right_context_glyphs,
            )
            # Block the upgrade before the variants of a follower F that have no entry at `exit_y` when F's backward upgrade at `exit_y` lists `base_name` in its `not_after`. F gets no entry there after `base_name`, so the exit would join nothing.
            blocked_follower_glyphs: set[str] = set()
            for f_base, f_bk_at_y in bk_replacements.items():
                if exit_y not in f_bk_at_y or f_base == base_name:
                    continue
                f_bk_excl_raw = bk_exclusions.get(f_base, {}).get(exit_y, [])
                if not f_bk_excl_raw:
                    continue
                expanded_excl = _expand_exclusions(f_bk_excl_raw)
                if base_name not in expanded_excl and not (
                    set(base_to_variants.get(base_name, ())) & expanded_excl
                ):
                    continue
                f_candidates = set(base_to_variants.get(f_base, ())) | {f_base}
                for f_variant in f_candidates:
                    if f_variant not in right_context_glyphs:
                        continue
                    f_var_meta = _meta(f_variant)
                    if f_var_meta.entry and exit_y in f_var_meta.entry_ys:
                        continue
                    blocked_follower_glyphs.add(f_variant)
            if blocked_follower_glyphs:
                lines.append(
                    f"        ignore sub {base_name}' [{' '.join(sorted(blocked_follower_glyphs))}];"
                )
            kept_followers = _select_rule_neighbors(
                base_name, variant_name, right_context_glyphs, direction="fwd"
            )
            if kept_followers == right_context_glyphs:
                lines.append(f"        sub {base_name}' {cls} by {variant_name};")
            elif kept_followers:
                lines.append(
                    f"        sub {base_name}' [{' '.join(sorted(kept_followers))}] by {variant_name};"
                )
            emitted = True

            for target_name in _entry_bearing_strip_targets(exit_y, variant_name):
                for excluded_glyph in sorted(excluded):
                    lines.append(f"        ignore sub {target_name}' {excluded_glyph};")
                if fwd_bk_excl:
                    for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                        lines.append(f"        ignore sub {bg} {target_name}' {cls};")
                for terminal in sorted(effective_right_context_glyphs & plan.terminal_exit_only):
                    lines.append(f"        ignore sub {target_name}' {terminal};")
                _emit_narrow_mid_entry_strip_guards(
                    target_name,
                    variant_name,
                    exit_y,
                    effective_right_context_glyphs,
                )
                if blocked_follower_glyphs:
                    lines.append(
                        f"        ignore sub {target_name}' [{' '.join(sorted(blocked_follower_glyphs))}];"
                    )
                kept_target_followers = _select_rule_neighbors(
                    target_name, variant_name, right_context_glyphs, direction="fwd"
                )
                if kept_target_followers == right_context_glyphs:
                    lines.append(f"        sub {target_name}' {cls} by {variant_name};")
                elif kept_target_followers:
                    lines.append(
                        f"        sub {target_name}' [{' '.join(sorted(kept_target_followers))}] by {variant_name};"
                    )
                emitted = True
        if base_name in fwd_preferred_lookahead:
            for variant_name, exit_y, sibling_y in fwd_preferred_lookahead[base_name]:
                if exit_y not in selected_exit_ys:
                    continue
                bridge_members = plan.preferred_lookahead_bridges.get((exit_y, sibling_y))
                if not bridge_members:
                    continue
                if exit_y in entry_classes and sibling_y in entry_exclusive and entry_exclusive[sibling_y]:
                    lines.append(
                        f"        sub {base_name}' @bridge_y{exit_y}_y{sibling_y} @entry_only_y{sibling_y} by {variant_name};"
                    )
                    emitted = True
        noentry_name = f"{base_name}.noentry"
        if noentry_name in glyph_names:
            for exit_y in selected_exit_ys:
                variant_name = variants[exit_y]
                if exit_y not in entry_classes:
                    continue
                use_excl = (base_name, exit_y) in fwd_use_exclusive
                if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                    continue
                cls = f"@entry_only_y{exit_y}" if use_excl else f"@entry_y{exit_y}"
                fwd_bk_excl = plan.fwd_bk_exclusions.get(base_name, {}).get(exit_y)
                if fwd_bk_excl and not _meta(variant_name).strip_entry_before:
                    for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                        lines.append(f"        ignore sub {bg} {noentry_name}' {cls};")
                # Apply the replacement's `not_before` to the `.noentry` stance as well, or the stance `calt_zwnj` produces would bypass it (`qsIt.noentry` would become `qsIt.ex-y5` before ·Day). Limit the exclusions to this rule's right-side class, so a sibling entering at another Y (`qsZoo.half` at y=0 for the `@entry_y5` rule) can still match a later rule in the lookup.
                right_context_glyphs = set(entry_exclusive[exit_y] if use_excl else entry_classes[exit_y])
                excluded = _expand_exclusions(exclusions.get(exit_y, [])) & right_context_glyphs
                for excluded_glyph in sorted(excluded):
                    lines.append(f"        ignore sub {noentry_name}' {excluded_glyph};")
                actual_variant = _resolve_noentry_replacement(
                    glyph_meta,
                    base_to_variants,
                    noentry_name,
                    variant_name,
                )
                if actual_variant is None:
                    continue
                _emit_narrow_mid_entry_strip_guards(
                    noentry_name,
                    actual_variant,
                    exit_y,
                    right_context_glyphs - excluded,
                )
                kept_noentry_followers = _select_rule_neighbors(
                    noentry_name, actual_variant, right_context_glyphs, direction="fwd"
                )
                if kept_noentry_followers == right_context_glyphs:
                    lines.append(f"        sub {noentry_name}' {cls} by {actual_variant};")
                elif kept_noentry_followers:
                    lines.append(
                        f"        sub {noentry_name}' [{' '.join(sorted(kept_noentry_followers))}] by {actual_variant};"
                    )
                emitted = True
        if emitted:
            lines.append(f"    }} {lookup_name};")
        else:
            lines.pop()
            lines.pop()

    def _emit_fwd(base_name: str):
        _emit_fwd_pairs(base_name)
        _emit_fwd_general(base_name)

    def _needs_post_cycle_fwd_pairs(base_name: str) -> bool:
        for _variant_name, before_glyphs, _not_after_glyphs in fwd_pair_overrides.get(
            base_name,
            (),
        ):
            for before_glyph in before_glyphs:
                before_base = (
                    glyph_meta[before_glyph].base_name if before_glyph in glyph_meta else before_glyph
                )
                if before_base in fwd_upgrades:
                    return True
        return False

    def _pending_prev_context_guards(
        prev_glyphs: list[str],
        candidate_name: str,
    ) -> set[str]:
        expanded_prev = set(_expand_all_variants(prev_glyphs))
        guards = set(expanded_prev)
        candidate_base = _meta(candidate_name).base_name
        for prior_base, fwd_overrides in fwd_pair_overrides.items():
            source_slot: set[str] | None = None
            for fwd_variant, fwd_lookahead, _not_after in fwd_overrides:
                if fwd_variant not in expanded_prev:
                    continue
                expanded_lookahead = _expand_all_variants(
                    fwd_lookahead,
                    include_base=True,
                )
                if candidate_name not in expanded_lookahead and candidate_base not in expanded_lookahead:
                    continue
                if source_slot is None:
                    source_slot = _fwd_pair_source_slot(prior_base)
                guards.update(source_slot)
        return guards

    def _pending_override_can_precede(pending_variant: str, right_base_name: str) -> bool:
        """Return whether the pair override `pending_variant` can fire before `right_base_name`: its `not_before` doesn't name that family, and its `before` is empty or names it. An override that can't fire there can't displace the candidate, so it adds no guard."""
        pending_meta = _meta(pending_variant)
        if right_base_name in pending_meta.not_before:
            return False
        before = pending_meta.before
        if not before:
            return True
        return any(_base_name(glyph) == right_base_name for glyph in before)

    def _collect_pending_bk_pair_guards(
        candidate_name: str,
        entry_ys: set[int],
        right_base_name: str,
    ) -> set[str]:
        candidate_meta = _meta(candidate_name)
        # A `.noentry` stance comes from `calt_zwnj` after a ZWNJ or from a `noentry_after` pair override. It gets none of the backward guards that would block the pair substitution.
        if candidate_meta.is_noentry:
            return set()

        candidate_base = candidate_meta.base_name
        guards: set[str] = set()
        if candidate_meta.exit:
            if candidate_name != candidate_base or not (set(candidate_meta.exit_ys) & entry_ys):
                return set()
            for pending_variant, prev_glyphs in pair_overrides.get(candidate_base, []):
                if _candidate_can_support_entry_ys(
                    pending_variant,
                    entry_ys,
                    right_base_name,
                ):
                    continue
                if not _pending_override_can_precede(pending_variant, right_base_name):
                    continue
                guards.update(_pending_prev_context_guards(prev_glyphs, candidate_name))
            return guards

        for prev_exit_y, pending_variant in bk_replacements.get(candidate_base, {}).items():
            if _candidate_can_support_entry_ys(
                pending_variant,
                entry_ys,
                right_base_name,
            ):
                continue
            guards.update(exit_classes.get(prev_exit_y, set()))

        for pending_variant, prev_glyphs in pair_overrides.get(candidate_base, []):
            if _candidate_can_support_entry_ys(
                pending_variant,
                entry_ys,
                right_base_name,
            ):
                continue
            if not _pending_override_can_precede(pending_variant, right_base_name):
                continue
            guards.update(_pending_prev_context_guards(prev_glyphs, candidate_name))

        needed_exit_ys = entry_ys - set(candidate_meta.exit_ys)
        if needed_exit_ys:
            for (source_name, replacement_name, _), pending_guards in _derived_bk_guards.items():
                if source_name != candidate_name:
                    continue
                if not (set(_meta(replacement_name).exit_ys) & needed_exit_ys):
                    continue
                for guard in pending_guards:
                    if guard.before_bases and right_base_name not in guard.before_bases:
                        continue
                    guards.update(guard.guard_glyphs)

        return guards

    def _candidate_can_support_entry_ys(
        candidate_name: str,
        entry_ys: set[int],
        right_base_name: str | None,
    ) -> bool:
        candidate_meta = _meta(candidate_name)
        if set(candidate_meta.exit_ys) & entry_ys:
            return True
        if candidate_meta.exit:
            return False
        return any(
            _can_eventually_exit_at(
                plan,
                candidate_name,
                entry_y,
                before_base=right_base_name,
            )
            for entry_y in entry_ys
        )

    def _fwd_pair_source_slot(prior_base: str) -> set[str]:
        """Return the stances of `prior_base` that a forward-pair rule matches in its input position: the base, its backward and forward replacements, pair overrides, forward upgrades, and `.noentry` stance. This is `_emit_fwd_pairs`'s `targets` set before it adds the derivations on the other side."""
        slot = {prior_base}
        if prior_base in bk_replacements:
            slot.update(bk_replacements[prior_base].values())
        if prior_base in fwd_replacements:
            slot.update(fwd_replacements[prior_base].values())
        if prior_base in pair_overrides:
            slot.update(variant for variant, _ in pair_overrides[prior_base])
        if prior_base in fwd_upgrades:
            slot.update(variant for variant, _, _, _ in fwd_upgrades[prior_base])
        noentry_name = f"{prior_base}.noentry"
        if noentry_name in glyph_names:
            slot.add(noentry_name)
        return slot

    def _emit_two_glyph_lookbehind_guards(
        member_iter,
        base_name: str,
        entry_ys: set[int],
    ) -> None:
        # Emit `ignore sub [prior] candidate base';` for each prior slot `_collect_two_glyph_lookbehind_guards` finds, and record the matching re-flips. Those guards cover a candidate whose own backward replacement, triggered by a later change to the glyph before it, would move its exit off `entry_ys`, so this rule's match would no longer hold. `_collect_pending_bk_pair_guards` covers the single-glyph case.
        if not entry_ys:
            return
        members = frozenset(member_iter)
        by_prior: dict[frozenset[str], set[str]] = {}
        for cand in members:
            for prior_slot, cand_name in _collect_two_glyph_lookbehind_guards(
                cand,
                entry_ys,
                base_name,
                expanded_after=members,
            ):
                by_prior.setdefault(prior_slot, set()).add(cand_name)
        for prior_slot in sorted(by_prior, key=lambda slot: sorted(slot)):
            cands = sorted(by_prior[prior_slot])
            prior_list = " ".join(sorted(prior_slot))
            cand_token = cands[0] if len(cands) == 1 else f"[{' '.join(cands)}]"
            lines.append(f"        ignore sub [{prior_list}] {cand_token} {base_name}';")
            for cand_name in cands:
                _record_pair_guard_reflip(
                    prior_slot,
                    cand_name,
                    base_name,
                    entry_ys,
                )

    def _collect_two_glyph_lookbehind_guards(
        candidate_name: str,
        entry_ys: set[int],
        right_base_name: str,
        *,
        expanded_after: frozenset[str] = frozenset(),
    ) -> list[tuple[frozenset[str], str]]:
        # Return `(prior_slot, candidate)` pairs for a backward rule on the follower. The candidate's own backward replacement can fire later and leave it unable to exit at `entry_ys`. That happens when the glyph before it takes a forward pair override or forward replacement that exits at the backward replacement's trigger Y and has the candidate in its lookahead, so the guard lists that glyph's stances.
        candidate_meta = _meta(candidate_name)
        if candidate_meta.is_noentry:
            return []
        candidate_base = candidate_meta.base_name
        bk_for_base = bk_replacements.get(candidate_base, {})
        if not bk_for_base:
            return []

        # Only the trigger Ys whose backward replacement can't exit at `entry_ys` need guards.
        invalidating_prev_exit_ys: set[int] = set()
        for prev_exit_y, pending_variant in bk_for_base.items():
            if _candidate_can_support_entry_ys(
                pending_variant,
                entry_ys,
                right_base_name,
            ):
                continue
            invalidating_prev_exit_ys.add(prev_exit_y)
        if not invalidating_prev_exit_ys:
            return []

        guards: list[tuple[frozenset[str], str]] = []
        for prior_base, fwd_overrides in fwd_pair_overrides.items():
            for fwd_variant, fwd_lookahead, _ in fwd_overrides:
                fwd_meta = _meta(fwd_variant)
                if not (set(fwd_meta.exit_ys) & invalidating_prev_exit_ys):
                    continue
                expanded_lookahead = _expand_all_variants(fwd_lookahead, include_base=True)
                if candidate_name not in expanded_lookahead and candidate_base not in expanded_lookahead:
                    continue
                slot = _fwd_pair_source_slot(prior_base)
                # The forward-pair lookup runs first and may already have changed the prior glyph to ``fwd_variant``.
                slot.add(fwd_variant)
                if slot:
                    guards.append((frozenset(slot), candidate_name))
        # A forward replacement on the prior glyph can start the same chain when the candidate is in its `@entry_y` class. Skip same-family chains (`prior_base == candidate_base`): there the prior glyph changes because of the candidate's right context, and the follower's backward upgrade doesn't depend on it (in ·It·It·No, ·No's `after-it-and-vie` upgrade is correct however the first ·It changes). Also skip a `prior_base` none of whose stances is in `expanded_after`, the predecessors the backward rule accepts.
        for prior_base, by_exit_y in fwd_replacements.items():
            if prior_base == candidate_base:
                continue
            source_slot = _fwd_pair_source_slot(prior_base)
            if source_slot.isdisjoint(expanded_after):
                continue
            for replacement_exit_y, replacement_variant in by_exit_y.items():
                if replacement_exit_y not in invalidating_prev_exit_ys:
                    continue
                # The forward replacement fires only when the candidate is in its `@entry_y` class.
                candidate_entry_class = entry_classes.get(replacement_exit_y, set())
                if (
                    candidate_name not in candidate_entry_class
                    and candidate_base not in candidate_entry_class
                ):
                    continue
                slot = set(source_slot)
                # The forward replacement may already have changed the prior glyph to ``replacement_variant``.
                slot.add(replacement_variant)
                if slot:
                    guards.append((frozenset(slot), candidate_name))
        return guards

    def _emit_bk_pairs(base_name: str):
        if base_name in pair_overrides:
            for variant_name, after_glyphs in sorted(
                pair_overrides[base_name],
                key=lambda item: _backward_pair_sort_key(glyph_meta, item[0], item[1]),
            ):
                variant_meta = _meta(variant_name)
                expanded_after = _expand_backward_after_variants(
                    variant_name,
                    after_glyphs,
                    expand_selector=lambda glyph: _expand_all_variants([glyph]),
                    analysis=plan,
                )
                if not expanded_after:
                    continue
                after_list = " ".join(sorted(expanded_after))
                lookahead = ""
                if variant_meta.before:
                    pair_before_followers = _select_rule_neighbors(
                        base_name,
                        variant_name,
                        set(_expand_all_variants(variant_meta.before)),
                        direction="fwd",
                    )
                    before_list = " ".join(sorted(pair_before_followers))
                    if not before_list:
                        continue
                    lookahead = f" [{before_list}]"
                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_pair_{safe} {{")
                not_before = list(variant_meta.not_before)
                if not_before:
                    resolved = resolve_known_glyph_names(not_before, glyph_names)
                    for not_before_glyph in sorted(_expand_exclusions(resolved)):
                        lines.append(f"        ignore sub [{after_list}] {base_name}' {not_before_glyph};")
                entry_ys = set(variant_meta.entry_ys)
                if entry_ys:
                    for candidate_name in sorted(expanded_after):
                        guard_glyphs = _collect_pending_bk_pair_guards(
                            candidate_name,
                            entry_ys,
                            variant_meta.base_name,
                        )
                        if guard_glyphs:
                            guard_list = " ".join(sorted(guard_glyphs))
                            lines.append(f"        ignore sub [{guard_list}] {candidate_name} {base_name}';")
                            _record_pair_guard_reflip(
                                frozenset(guard_glyphs),
                                candidate_name,
                                base_name,
                                entry_ys,
                            )
                    _emit_two_glyph_lookbehind_guards(expanded_after, base_name, entry_ys)
                for terminal in sorted(expanded_after & plan.terminal_entry_only):
                    lines.append(f"        ignore sub {terminal} {base_name}';")
                _emit_entry_strip_guards_for_replacement_exit(
                    base_name,
                    variant_name,
                    left_context=f"[{after_list}]",
                )
                lines.append(f"        sub [{after_list}] {base_name}'{lookahead} by {variant_name};")
                lines.append(f"    }} calt_pair_{safe};")

    def _fwd_pair_bk_targets(base_name: str, entry_y: int) -> list[str]:
        if base_name not in fwd_pair_overrides:
            return []
        targets = []
        for fwd_variant, _, _ in fwd_pair_overrides[base_name]:
            fwd_meta = _meta(fwd_variant)
            if fwd_meta.entry and entry_y not in set(fwd_meta.entry_ys):
                targets.append(fwd_variant)
        return targets

    def _emit_bk_general(base_name: str):
        if base_name in bk_replacements:
            variants = bk_replacements[base_name]
            exclusions = bk_exclusions.get(base_name, {})
            lookup_name = f"calt_{base_name}"
            lines.append("")
            lines.append(f"    lookup {lookup_name} {{")
            for entry_y in sorted(variants.keys()):
                variant_name = variants[entry_y]
                if entry_y in exit_classes:
                    excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                    if excluded:
                        filtered = sorted(
                            _select_rule_neighbors(
                                base_name,
                                variant_name,
                                exit_classes[entry_y] - excluded,
                                direction="bk",
                            )
                        )
                        if filtered:
                            member_list = " ".join(filtered)
                            _emit_entry_strip_guards_for_replacement_exit(
                                base_name,
                                variant_name,
                                left_context=f"[{member_list}]",
                            )
                            _emit_two_glyph_lookbehind_guards(filtered, base_name, {entry_y})
                            lines.append(f"        sub [{member_list}] {base_name}' by {variant_name};")
                            for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                                _emit_fpt_revert(
                                    fpt,
                                    variant_name,
                                    member_set=set(filtered),
                                    member_list_token=f"[{member_list}]",
                                )
                    else:
                        candidate_preds = set(exit_classes[entry_y])
                        kept_preds = _select_rule_neighbors(
                            base_name,
                            variant_name,
                            candidate_preds,
                            direction="bk",
                        )
                        _emit_entry_strip_guards_for_replacement_exit(
                            base_name,
                            variant_name,
                            left_context=f"@exit_y{entry_y}",
                        )
                        if kept_preds == candidate_preds:
                            lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
                        else:
                            lines.append(
                                f"        sub [{' '.join(sorted(kept_preds))}] {base_name}' by {variant_name};"
                            )
                        for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                            _emit_fpt_revert(
                                fpt,
                                variant_name,
                                member_set=set(exit_classes.get(entry_y, set())),
                                member_list_token=f"@exit_y{entry_y}",
                            )
            lines.append(f"    }} {lookup_name};")

    def _emit_bk_cycle(bases: list[str]):
        lines.append("")
        lines.append("    lookup calt_cycle {")
        bk_fwd_excl = plan.bk_fwd_exclusions
        bk_fwd_excl_seq = plan.bk_fwd_exclusion_sequences
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            exclusions = bk_exclusions.get(base_name, {})
            for entry_y in sorted(variants.keys()):
                variant_name = variants[entry_y]
                if entry_y in exit_classes:
                    excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                    fwd_excl = bk_fwd_excl.get(base_name, {}).get(entry_y)
                    fwd_excl_sequences = bk_fwd_excl_seq.get(base_name, {}).get(entry_y, [])
                    excl_tokens = _excl_tokens(fwd_excl, fwd_excl_sequences)
                    if excluded:
                        filtered = sorted(
                            _select_rule_neighbors(
                                base_name, variant_name, exit_classes[entry_y] - excluded, direction="bk"
                            )
                        )
                        if filtered:
                            member_list = " ".join(filtered)
                            for tok in excl_tokens:
                                lines.append(f"        ignore sub [{member_list}] {base_name}' {tok};")
                            _emit_entry_strip_guards_for_replacement_exit(
                                base_name,
                                variant_name,
                                left_context=f"[{member_list}]",
                            )
                            _emit_two_glyph_lookbehind_guards(filtered, base_name, {entry_y})
                            lines.append(f"        sub [{member_list}] {base_name}' by {variant_name};")
                            for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                                for tok in excl_tokens:
                                    lines.append(f"        ignore sub [{member_list}] {fpt}' {tok};")
                                _emit_fpt_revert(
                                    fpt,
                                    variant_name,
                                    member_set=set(filtered),
                                    member_list_token=f"[{member_list}]",
                                )
                    else:
                        candidate_preds = set(exit_classes[entry_y])
                        kept_preds = _select_rule_neighbors(
                            base_name, variant_name, candidate_preds, direction="bk"
                        )
                        for tok in excl_tokens:
                            lines.append(f"        ignore sub @exit_y{entry_y} {base_name}' {tok};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            base_name,
                            variant_name,
                            left_context=f"@exit_y{entry_y}",
                        )
                        if kept_preds == candidate_preds:
                            lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
                        else:
                            lines.append(
                                f"        sub [{' '.join(sorted(kept_preds))}] {base_name}' by {variant_name};"
                            )
                        for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                            for tok in excl_tokens:
                                lines.append(f"        ignore sub @exit_y{entry_y} {fpt}' {tok};")
                            _emit_fpt_revert(
                                fpt,
                                variant_name,
                                member_set=set(exit_classes.get(entry_y, set())),
                                member_list_token=f"@exit_y{entry_y}",
                            )
        for base_name in bases:
            if base_name in fwd_replacements:
                variants = fwd_replacements[base_name]
                exclusions = fwd_exclusions.get(base_name, {})
                for exit_y in sorted(variants.keys(), reverse=True):
                    variant_name = variants[exit_y]
                    if exit_y not in entry_classes:
                        continue
                    use_excl = (base_name, exit_y) in fwd_use_exclusive
                    if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                        continue
                    cls = f"@entry_only_y{exit_y}" if use_excl else f"@entry_y{exit_y}"
                    base_ey = {y for y, members in entry_classes.items() if base_name in members}
                    var_ey = {y for y, members in entry_classes.items() if variant_name in members}
                    if var_ey:
                        for hidden_y in sorted(base_ey - var_ey):
                            if hidden_y in exit_classes:
                                lines.append(f"        ignore sub @exit_y{hidden_y} {base_name}' {cls};")
                    fwd_bk_excl = plan.fwd_bk_exclusions.get(base_name, {}).get(exit_y)
                    if fwd_bk_excl:
                        for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                            lines.append(f"        ignore sub {bg} {base_name}' {cls};")
                    excluded = _expand_exclusions(exclusions.get(exit_y, []))
                    for excluded_glyph in sorted(excluded):
                        lines.append(f"        ignore sub {base_name}' {excluded_glyph};")
                    right_context_glyphs = set(entry_exclusive[exit_y] if use_excl else entry_classes[exit_y])
                    effective_right_context_glyphs = right_context_glyphs - excluded
                    _emit_pending_bk_entry_guards(base_name, variant_name, right_context_glyphs)
                    _emit_narrow_mid_entry_strip_guards(
                        base_name,
                        variant_name,
                        exit_y,
                        effective_right_context_glyphs,
                    )
                    # As in `_emit_fwd_general`, block the upgrade before the variants of a follower F that have no entry at `exit_y` when F's backward upgrade at `exit_y` lists `base_name` in its `not_after`.
                    blocked_follower_glyphs: set[str] = set()
                    for f_base, f_bk_at_y in bk_replacements.items():
                        if exit_y not in f_bk_at_y or f_base == base_name:
                            continue
                        f_bk_excl_raw = bk_exclusions.get(f_base, {}).get(exit_y, [])
                        if not f_bk_excl_raw:
                            continue
                        expanded_excl = _expand_exclusions(f_bk_excl_raw)
                        if base_name not in expanded_excl and not (
                            set(base_to_variants.get(base_name, ())) & expanded_excl
                        ):
                            continue
                        f_candidates = set(base_to_variants.get(f_base, ())) | {f_base}
                        for f_variant in f_candidates:
                            if f_variant not in right_context_glyphs:
                                continue
                            f_var_meta = _meta(f_variant)
                            if f_var_meta.entry and exit_y in f_var_meta.entry_ys:
                                continue
                            blocked_follower_glyphs.add(f_variant)
                    if blocked_follower_glyphs:
                        lines.append(
                            f"        ignore sub {base_name}' [{' '.join(sorted(blocked_follower_glyphs))}];"
                        )
                    kept_followers = _select_rule_neighbors(
                        base_name, variant_name, right_context_glyphs, direction="fwd"
                    )
                    if kept_followers == right_context_glyphs:
                        lines.append(f"        sub {base_name}' {cls} by {variant_name};")
                    elif kept_followers:
                        lines.append(
                            f"        sub {base_name}' [{' '.join(sorted(kept_followers))}] by {variant_name};"
                        )
        lines.append("    } calt_cycle;")

    def _emit_upgrades(base_name: str):
        if base_name not in fwd_upgrades:
            return
        for entry_exit_var, entry_only_var, exit_y, not_before in fwd_upgrades[base_name]:
            if exit_y not in entry_classes:
                continue
            cls = f"@entry_y{exit_y}"
            safe = entry_exit_var.replace(".", "_")
            lines.append("")
            lines.append(f"    lookup calt_upgrade_{safe} {{")
            if not_before:
                not_before_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                lines.append(f"        ignore sub {entry_only_var}' [{not_before_list}];")
            upgrade_followers = set(entry_classes[exit_y])
            kept_upgrade_followers = _select_rule_neighbors(
                entry_only_var, entry_exit_var, upgrade_followers, direction="fwd"
            )
            if kept_upgrade_followers == upgrade_followers:
                lines.append(f"        sub {entry_only_var}' {cls} by {entry_exit_var};")
            elif kept_upgrade_followers:
                lines.append(
                    f"        sub {entry_only_var}' [{' '.join(sorted(kept_upgrade_followers))}] by {entry_exit_var};"
                )
            lines.append(f"    }} calt_upgrade_{safe};")

    def _emit_post_upgrade_bk(bases: list[str]):
        upgrade_exit_ys: set[int] = set()
        for base_name in bases:
            if base_name in fwd_upgrades:
                for _, _, exit_y, _ in fwd_upgrades[base_name]:
                    upgrade_exit_ys.add(exit_y)
        if not upgrade_exit_ys:
            return
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            relevant = {
                y: variant for y, variant in variants.items() if y in upgrade_exit_ys and y in exit_classes
            }
            if not relevant:
                continue
            safe = base_name.replace(".", "_").replace("-", "_")
            lines.append("")
            exclusions = bk_exclusions.get(base_name, {})
            bk_fwd_excl = plan.bk_fwd_exclusions
            bk_fwd_excl_seq = plan.bk_fwd_exclusion_sequences
            lines.append(f"    lookup calt_post_upgrade_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                fwd_excl = bk_fwd_excl.get(base_name, {}).get(entry_y)
                fwd_excl_sequences = bk_fwd_excl_seq.get(base_name, {}).get(entry_y, [])
                excl_tokens = _excl_tokens(fwd_excl, fwd_excl_sequences)
                if excluded:
                    filtered = sorted(
                        _select_rule_neighbors(
                            base_name, relevant[entry_y], exit_classes[entry_y] - excluded, direction="bk"
                        )
                    )
                    if filtered:
                        member_list = " ".join(filtered)
                        for tok in excl_tokens:
                            lines.append(f"        ignore sub [{member_list}] {base_name}' {tok};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            base_name,
                            relevant[entry_y],
                            left_context=f"[{member_list}]",
                        )
                        _emit_two_glyph_lookbehind_guards(filtered, base_name, {entry_y})
                        lines.append(f"        sub [{member_list}] {base_name}' by {relevant[entry_y]};")
                        for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                            for tok in excl_tokens:
                                lines.append(f"        ignore sub [{member_list}] {fpt}' {tok};")
                            _emit_fpt_revert(
                                fpt,
                                relevant[entry_y],
                                member_set=set(filtered),
                                member_list_token=f"[{member_list}]",
                            )
                else:
                    candidate_preds = set(exit_classes[entry_y])
                    kept_preds = _select_rule_neighbors(
                        base_name, relevant[entry_y], candidate_preds, direction="bk"
                    )
                    for tok in excl_tokens:
                        lines.append(f"        ignore sub @exit_y{entry_y} {base_name}' {tok};")
                    _emit_entry_strip_guards_for_replacement_exit(
                        base_name,
                        relevant[entry_y],
                        left_context=f"@exit_y{entry_y}",
                    )
                    if kept_preds == candidate_preds:
                        lines.append(f"        sub @exit_y{entry_y} {base_name}' by {relevant[entry_y]};")
                    else:
                        lines.append(
                            f"        sub [{' '.join(sorted(kept_preds))}] {base_name}' by {relevant[entry_y]};"
                        )
                    for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                        for tok in excl_tokens:
                            lines.append(f"        ignore sub @exit_y{entry_y} {fpt}' {tok};")
                        _emit_fpt_revert(
                            fpt,
                            relevant[entry_y],
                            member_set=set(exit_classes.get(entry_y, set())),
                            member_list_token=f"@exit_y{entry_y}",
                        )
            lines.append(f"    }} calt_post_upgrade_bk_{safe};")

    def _emit_post_override_bk(bases: list[str]):
        override_fwd_exit_ys: set[int] = set()
        for base_name in bases:
            if base_name not in bk_replacements or base_name not in fwd_replacements:
                continue
            for _, bk_var in sorted(bk_replacements[base_name].items()):
                if _meta(bk_var).exit:
                    continue
                for _, fwd_var in fwd_replacements[base_name].items():
                    override_fwd_exit_ys.update(_meta(fwd_var).exit_ys)
        if not override_fwd_exit_ys:
            return
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            relevant = {}
            for y, variant in variants.items():
                if y not in override_fwd_exit_ys or y not in exit_classes:
                    continue
                if not _meta(variant).exit:
                    continue
                relevant[y] = variant
            if not relevant:
                continue
            fwd_exit_only = []
            fwd_exit_by_variant = {}
            for _, fwd_variant in fwd_replacements.get(base_name, {}).items():
                if not _meta(fwd_variant).entry:
                    fwd_exit_only.append(fwd_variant)
                    fwd_exit_by_variant[fwd_variant] = _meta(fwd_variant).exit[0][1]
            if not fwd_exit_only:
                continue
            safe = f"post_override_{base_name}".replace(".", "_").replace("-", "_")
            exclusions = bk_exclusions.get(base_name, {})
            bk_fwd_excl = plan.bk_fwd_exclusions
            bk_fwd_excl_seq = plan.bk_fwd_exclusion_sequences
            lines.append("")
            lines.append(f"    lookup calt_{safe} {{")
            # HarfBuzz treats ZWNJ as a default-ignorable glyph and would otherwise allow this backward-context lookup to match across a ZWNJ. Mentioning uni200C in an ignore rule forces it into the lookup's coverage so HarfBuzz stops skipping it.
            for fwd_variant in sorted(fwd_exit_only):
                lines.append(f"        ignore sub uni200C {fwd_variant}';")
            for entry_y in sorted(relevant.keys()):
                excluded = sorted(_expand_exclusions(exclusions.get(entry_y, [])))
                # Repeat the `not_before` exclusions of `relevant[entry_y]` (and the IR-derived `bk_fwd_exclusion_sequences`) here. Without them, an earlier `calt_fwd_*` lookup rewrites the bare base to an entryless `fwd_variant`, and this lookup then upgrades it to `relevant[entry_y]`, which has the entry anchor, whatever the follower, undoing `not_before`. When there are `not_before` exclusions (`excl_tokens`), the sub also gets a lookahead of every `@entry_y*` class whose Y is in `fwd_used_ys`. That puts the `not_before` ignore rules and the sub in the same compiled subtable, and the upgrade still fires for followers (such as bare qsIt at entry_y=0) whose entry Y differs from the target's exit Y.
                fwd_excl = bk_fwd_excl.get(base_name, {}).get(entry_y)
                fwd_excl_sequences = bk_fwd_excl_seq.get(base_name, {}).get(entry_y, [])
                excl_tokens = _excl_tokens(fwd_excl, fwd_excl_sequences)
                sub_la = ""
                if excl_tokens:
                    lookahead_classes = [
                        f"@entry_y{ey}" for ey in sorted(set(entry_classes) & set(fwd_used_ys))
                    ]
                    if lookahead_classes:
                        sub_la = " [" + " ".join(lookahead_classes) + "]"
                for fwd_variant in sorted(fwd_exit_only):
                    for excluded_glyph in excluded:
                        lines.append(f"        ignore sub {excluded_glyph} {fwd_variant}';")
                    if sub_la:
                        for tok in excl_tokens:
                            lines.append(f"        ignore sub @exit_y{entry_y} {fwd_variant}' {tok};")
                    fwd_exit_y = fwd_exit_by_variant.get(fwd_variant)
                    if (
                        fwd_exit_y == entry_y
                        and _meta(fwd_variant).strip_entry_before
                        and fwd_exit_y in entry_classes
                    ):
                        use_excl = (base_name, fwd_exit_y) in fwd_use_exclusive
                        if not (
                            use_excl
                            and (fwd_exit_y not in entry_exclusive or not entry_exclusive[fwd_exit_y])
                        ):
                            right_context = set(
                                entry_exclusive[fwd_exit_y] if use_excl else entry_classes[fwd_exit_y]
                            )
                            right_context -= _expand_exclusions(
                                fwd_exclusions.get(base_name, {}).get(fwd_exit_y, []),
                            )
                            if right_context:
                                right_list = " ".join(sorted(right_context))
                                lines.append(
                                    f"        ignore sub @exit_y{entry_y} {fwd_variant}' [{right_list}];"
                                )
                    # The join contract does not narrow `sub_la`. This rule adds an entry for the predecessor join, and `sub_la` (when present) only requires some entry-bearing follower and keeps the `not_before` ignore rules in the same subtable. Narrowing it to followers that join `relevant[entry_y]`'s exit would test the follower join, which this rule does not decide. An exit dangle this rule leaves is outside the per-rule contract's reach (Phase 4 of doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md).
                    lines.append(
                        f"        sub @exit_y{entry_y} {fwd_variant}'{sub_la} by {relevant[entry_y]};"
                    )
            lines.append(f"    }} calt_{safe};")

    def _emit_reverse_upgrades():
        def _forward_exit_derivatives(root: str) -> list[str]:
            """Return every stance generated from `root`, at any depth, that extends or contracts the exit, leaving out `.noentry` and entry-trimmed stances. A follower can force these onto `root` before its left context resolves."""
            found: list[str] = []
            queue = deque([root])
            seen = {root}
            while queue:
                parent = queue.popleft()
                for child in generation_children.get(parent, ()):
                    if child in seen:
                        continue
                    seen.add(child)
                    queue.append(child)
                    child_meta = glyph_meta.get(child)
                    if child_meta is None or child_meta.is_noentry:
                        continue
                    if child_meta.transform_kind == "entry-trimmed":
                        continue
                    if not (child_meta.extended_exit_suffix or child_meta.contracted_exit_suffix):
                        continue
                    found.append(child)
            return found

        def _exit_xy(name: str) -> tuple[int, int] | None:
            meta = glyph_meta.get(name)
            if meta is None or not meta.exit:
                return None
            anchor = meta.exit[0]
            return (anchor[0], anchor[1])

        for base_name in sorted(fwd_upgrades):
            for entry_exit_var, entry_only_var, exit_y, not_before in fwd_upgrades[base_name]:
                entry_meta = _meta(entry_only_var)
                if not entry_meta.entry:
                    continue
                entry_y_val = entry_meta.entry[0][1]
                exit_only_var = fwd_replacements.get(base_name, {}).get(exit_y)
                if not exit_only_var or entry_y_val not in exit_classes:
                    continue
                entry_exit_meta = _meta(entry_exit_var)
                after_glyphs = list(entry_exit_meta.after) if entry_exit_meta.after else []
                left_context_token = f"@exit_y{entry_y_val}"
                if after_glyphs:
                    expanded_after = _expand_backward_after_variants(
                        entry_exit_var,
                        after_glyphs,
                        expand_selector=lambda glyph: _expand_all_variants([glyph]),
                        analysis=plan,
                    )
                    expanded_after = {
                        candidate
                        for candidate in expanded_after
                        if _can_eventually_exit_at(
                            plan,
                            candidate,
                            entry_y_val,
                            before_base=entry_exit_meta.base_name,
                        )
                    }
                    if not expanded_after:
                        continue
                    left_context_token = f"[{' '.join(sorted(expanded_after))}]"
                safe = entry_exit_var.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_reverse_upgrade_{safe} {{")
                if not_before:
                    not_before_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                    if after_glyphs:
                        lines.append(
                            f"        ignore sub {left_context_token} {exit_only_var}' [{not_before_list}];"
                        )
                    else:
                        lines.append(f"        ignore sub {exit_only_var}' [{not_before_list}];")
                lines.append(f"        sub {left_context_token} {exit_only_var}' by {entry_exit_var};")
                if after_glyphs:
                    # The rule above applies the after-context shape only to the bare `exit_only_var`. When the after-context letter (e.g. ·See) resolves later than the follower, the follower has already given the exit-only stance an exit extension or contraction, so the buffer holds e.g. `qsOut.en-y0.ex-y5.ex-ext-1` and the rule above misses it. These rules map each such stance to the after-context stance with the same exit anchor, because the shifted after-context body needs one more contraction or one less extension to reach the same x. Without them, ·See·Out·J’ai and ·See·Out·Fee keep the unshifted body and collide with the preceding letter.
                    after_by_exit: dict[tuple[int, int], str] = {}
                    after_candidates = [entry_exit_var, *_forward_exit_derivatives(entry_exit_var)]
                    for candidate in sorted(
                        after_candidates, key=lambda name: (len(glyph_meta[name].modifiers), name)
                    ):
                        xy = _exit_xy(candidate)
                        if xy is not None:
                            after_by_exit.setdefault(xy, candidate)
                    for plain in sorted(_forward_exit_derivatives(exit_only_var)):
                        plain_meta = glyph_meta[plain]
                        if not (plain_meta.before or plain_meta.gated_before):
                            continue
                        xy = _exit_xy(plain)
                        if xy is None:
                            continue
                        target = after_by_exit.get(xy)
                        if target is None or target == plain:
                            continue
                        lines.append(f"        sub {left_context_token} {plain}' by {target};")
                lines.append(f"    }} calt_reverse_upgrade_{safe};")

        # Competing reverse-upgrade stances (same base, same source stances, same after-context) each emit a `sub … by …` with no lookahead, so the lookup emitted first takes the word-final input that the others' ignore rules don't exclude (e.g. qsOut's after-·See touch body and its +1px before-·Fee body). Within each such group a `terminal_default` stance moves to the front so it takes that input, whatever order `plan.reverse_only_upgrades` has. A group only reorders the emission slots it already occupies, so every other lookup keeps its position.
        competitor_slots: dict[tuple[str, tuple, tuple], list[int]] = {}
        competitor_members: dict[tuple[str, tuple, tuple], list[tuple[int, tuple]]] = {}
        for original_index, entry in enumerate(plan.reverse_only_upgrades):
            meta = _meta(entry[0])
            group = (meta.base_name, tuple(entry[1]), tuple(entry[3]))
            competitor_slots.setdefault(group, []).append(original_index)
            competitor_members.setdefault(group, []).append((original_index, entry))
        placement: dict[int, tuple] = {}
        for group, members in competitor_members.items():
            ordered_members = sorted(
                members,
                key=lambda im: (0 if _meta(im[1][0]).terminal_default else 1, im[0]),
            )
            for slot, (_, entry) in zip(competitor_slots[group], ordered_members):
                placement[slot] = entry
        ordered_reverse_only = [placement[i] for i in range(len(plan.reverse_only_upgrades))]
        for variant_name, source_variants, entry_ys, after_glyphs, not_before in ordered_reverse_only:
            valid_entry_ys = [y for y in sorted(set(entry_ys)) if y in exit_classes]
            if not valid_entry_ys:
                continue
            expanded_after = None
            if after_glyphs:
                expanded_after = _expand_backward_after_variants(
                    variant_name,
                    after_glyphs,
                    expand_selector=lambda glyph: _expand_all_variants([glyph]),
                    analysis=plan,
                )
                if not expanded_after:
                    continue
            safe = variant_name.replace(".", "_")
            lines.append("")
            lines.append(f"    lookup calt_reverse_upgrade_explicit_{safe} {{")
            not_before_list = (
                " ".join(sorted(_expand_all_variants(not_before, include_base=True))) if not_before else None
            )
            for entry_y in valid_entry_ys:
                if expanded_after is None:
                    for source_variant in source_variants:
                        if not_before_list:
                            lines.append(f"        ignore sub {source_variant}' [{not_before_list}];")
                        lines.append(f"        sub @exit_y{entry_y} {source_variant}' by {variant_name};")
                    continue

                expanded_after_for_y = {
                    candidate
                    for candidate in expanded_after
                    if _can_eventually_exit_at(
                        plan,
                        candidate,
                        entry_y,
                        before_base=_meta(variant_name).base_name,
                    )
                }
                if not expanded_after_for_y:
                    continue
                after_list = " ".join(sorted(expanded_after_for_y))
                for source_variant in source_variants:
                    for candidate_name in sorted(expanded_after_for_y):
                        guard_glyphs = _collect_pending_bk_pair_guards(
                            candidate_name,
                            {entry_y},
                            _meta(variant_name).base_name,
                        )
                        if guard_glyphs:
                            guard_list = " ".join(sorted(guard_glyphs))
                            lines.append(
                                f"        ignore sub [{guard_list}] {candidate_name} {source_variant}';"
                            )
                    if not_before_list:
                        lines.append(
                            f"        ignore sub [{after_list}] {source_variant}' [{not_before_list}];"
                        )
                    lines.append(f"        sub [{after_list}] {source_variant}' by {variant_name};")
            lines.append(f"    }} calt_reverse_upgrade_explicit_{safe};")

    def _emit_noentry_fwd_overrides(bases: list[str]):
        for base_name in bases:
            if base_name not in bk_replacements or base_name not in fwd_replacements:
                continue
            for entry_y, bk_var in sorted(bk_replacements[base_name].items()):
                if _meta(bk_var).exit:
                    continue
                valid_overrides = []
                for fwd_exit_y, fwd_var in sorted(fwd_replacements[base_name].items()):
                    if fwd_exit_y not in entry_classes:
                        continue
                    if _meta(fwd_var).entry:
                        continue
                    has_upgrade = any(
                        entry_only == bk_var and ey == fwd_exit_y
                        for _, entry_only, ey, _ in fwd_upgrades.get(base_name, [])
                    )
                    if has_upgrade:
                        continue
                    valid_overrides.append((fwd_exit_y, fwd_var))
                if not valid_overrides:
                    continue
                max_exit_y = max(entry_y for entry_y, _ in valid_overrides)
                for fwd_exit_y, fwd_var in valid_overrides:
                    use_exclusive = len(valid_overrides) > 1 and fwd_exit_y != max_exit_y
                    if use_exclusive:
                        if fwd_exit_y not in entry_exclusive or not entry_exclusive[fwd_exit_y]:
                            continue
                        cls = f"@entry_only_y{fwd_exit_y}"
                    else:
                        cls = f"@entry_y{fwd_exit_y}"
                    safe = f"{bk_var}_{fwd_exit_y}".replace(".", "_").replace("-", "_")
                    lines.append("")
                    lines.append(f"    lookup calt_fwd_override_{safe} {{")
                    fwd_bk_excl = plan.fwd_bk_exclusions.get(base_name, {}).get(fwd_exit_y)
                    if fwd_bk_excl:
                        for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                            lines.append(f"        ignore sub {bg} {bk_var}' {cls};")
                    not_before = list(_meta(fwd_var).not_before)
                    not_before_excluded: set[str] = set()
                    if not_before:
                        resolved = resolve_known_glyph_names(not_before, glyph_names)
                        not_before_excluded = _expand_exclusions(resolved)
                        for not_before_glyph in sorted(not_before_excluded):
                            lines.append(f"        ignore sub {bk_var}' {not_before_glyph};")
                    right_context_glyphs = set(
                        entry_exclusive[fwd_exit_y] if use_exclusive else entry_classes[fwd_exit_y]
                    )
                    effective_right_context_glyphs = right_context_glyphs - not_before_excluded
                    _emit_pending_bk_entry_guards(bk_var, fwd_var, right_context_glyphs)
                    _emit_narrow_mid_entry_strip_guards(
                        bk_var,
                        fwd_var,
                        fwd_exit_y,
                        effective_right_context_glyphs,
                    )
                    # As in `_emit_fwd_general`: when a follower base F has a backward upgrade at entry_y=fwd_exit_y whose `not_after` excludes base_name, F gets no entry at fwd_exit_y after base_name, so block this override before those F variants.
                    blocked_follower_glyphs: set[str] = set()
                    for f_base, f_bk_at_y in bk_replacements.items():
                        if fwd_exit_y not in f_bk_at_y or f_base == base_name:
                            continue
                        f_bk_excl_raw = bk_exclusions.get(f_base, {}).get(fwd_exit_y, [])
                        if not f_bk_excl_raw:
                            continue
                        expanded_excl = _expand_exclusions(f_bk_excl_raw)
                        if base_name not in expanded_excl and not (
                            set(base_to_variants.get(base_name, ())) & expanded_excl
                        ):
                            continue
                        f_candidates = set(base_to_variants.get(f_base, ())) | {f_base}
                        for f_variant in f_candidates:
                            if f_variant not in right_context_glyphs:
                                continue
                            f_var_meta = _meta(f_variant)
                            if f_var_meta.entry and fwd_exit_y in f_var_meta.entry_ys:
                                continue
                            blocked_follower_glyphs.add(f_variant)
                    for ext_fwd_var, trigger_glyphs in _exit_extension_refinements(
                        fwd_var, effective_right_context_glyphs
                    ):
                        trigger_list = " ".join(sorted(trigger_glyphs))
                        lines.append(f"        sub {bk_var}' [{trigger_list}] by {ext_fwd_var};")
                    if blocked_follower_glyphs:
                        lines.append(
                            f"        ignore sub {bk_var}' [{' '.join(sorted(blocked_follower_glyphs))}];"
                        )
                    kept_override_followers = _select_rule_neighbors(
                        bk_var, fwd_var, right_context_glyphs, direction="fwd"
                    )
                    if kept_override_followers == right_context_glyphs:
                        lines.append(f"        sub {bk_var}' {cls} by {fwd_var};")
                    elif kept_override_followers:
                        lines.append(
                            f"        sub {bk_var}' [{' '.join(sorted(kept_override_followers))}] by {fwd_var};"
                        )
                    lines.append(f"    }} calt_fwd_override_{safe};")
                    for ext_suffix in _ENTRY_EXTENSION_SUFFIXES:
                        ext_bk = f"{bk_var}{ext_suffix}"
                        if ext_bk not in glyph_meta:
                            continue
                        if _meta(ext_bk).exit:
                            continue
                        ext_safe = f"{ext_bk}_{fwd_exit_y}".replace(".", "_").replace("-", "_")
                        lines.append("")
                        lines.append(f"    lookup calt_fwd_override_{ext_safe} {{")
                        if fwd_bk_excl:
                            for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                                lines.append(f"        ignore sub {bg} {ext_bk}' {cls};")
                        if not_before:
                            resolved_nb = resolve_known_glyph_names(not_before, glyph_names)
                            not_before_excluded = _expand_exclusions(resolved_nb)
                            for nbg in sorted(not_before_excluded):
                                lines.append(f"        ignore sub {ext_bk}' {nbg};")
                        _emit_pending_bk_entry_guards(ext_bk, fwd_var, right_context_glyphs)
                        _emit_narrow_mid_entry_strip_guards(
                            ext_bk,
                            fwd_var,
                            fwd_exit_y,
                            right_context_glyphs - not_before_excluded,
                        )
                        for ext_fwd_var, trigger_glyphs in _exit_extension_refinements(
                            fwd_var, right_context_glyphs - not_before_excluded
                        ):
                            trigger_list = " ".join(sorted(trigger_glyphs))
                            lines.append(f"        sub {ext_bk}' [{trigger_list}] by {ext_fwd_var};")
                        if blocked_follower_glyphs:
                            lines.append(
                                f"        ignore sub {ext_bk}' [{' '.join(sorted(blocked_follower_glyphs))}];"
                            )
                        kept_ext_followers = _select_rule_neighbors(
                            ext_bk, fwd_var, right_context_glyphs, direction="fwd"
                        )
                        if kept_ext_followers == right_context_glyphs:
                            lines.append(f"        sub {ext_bk}' {cls} by {fwd_var};")
                        elif kept_ext_followers:
                            lines.append(
                                f"        sub {ext_bk}' [{' '.join(sorted(kept_ext_followers))}] by {fwd_var};"
                            )
                        lines.append(f"    }} calt_fwd_override_{ext_safe};")

    def _emit_pair_fwd_overrides(base_name: str):
        if base_name in bk_replacements:
            return
        if base_name not in pair_overrides or base_name not in fwd_replacements:
            return

        source_variants = sorted(
            {
                variant_name
                for variant_name, _ in pair_overrides[base_name]
                if not _meta(variant_name).exit and not _has_left_entry(_meta(variant_name))
            }
        )
        if not source_variants:
            return

        for source_variant in source_variants:
            valid_overrides = []
            for fwd_exit_y, fwd_var in sorted(fwd_replacements[base_name].items()):
                if fwd_exit_y not in entry_classes:
                    continue
                has_upgrade = any(
                    entry_only == source_variant and ey == fwd_exit_y
                    for _, entry_only, ey, _ in fwd_upgrades.get(base_name, [])
                )
                if has_upgrade:
                    continue
                actual_variant = _resolve_entryless_replacement(
                    glyph_meta,
                    base_to_variants,
                    source_variant,
                    fwd_var,
                )
                if actual_variant is None or actual_variant == source_variant:
                    continue
                valid_overrides.append((fwd_exit_y, actual_variant))
            if not valid_overrides:
                continue

            max_exit_y = max(exit_y for exit_y, _ in valid_overrides)
            for fwd_exit_y, actual_variant in valid_overrides:
                use_exclusive = len(valid_overrides) > 1 and fwd_exit_y != max_exit_y
                if use_exclusive:
                    if fwd_exit_y not in entry_exclusive or not entry_exclusive[fwd_exit_y]:
                        continue
                    cls = f"@entry_only_y{fwd_exit_y}"
                else:
                    cls = f"@entry_y{fwd_exit_y}"

                safe = f"{source_variant}_{fwd_exit_y}".replace(".", "_").replace("-", "_")
                lines.append("")
                lines.append(f"    lookup calt_fwd_override_{safe} {{")
                fwd_bk_excl = plan.fwd_bk_exclusions.get(base_name, {}).get(fwd_exit_y)
                if fwd_bk_excl:
                    for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                        lines.append(f"        ignore sub {bg} {source_variant}' {cls};")
                not_before = list(_meta(actual_variant).not_before)
                not_before_excluded: set[str] = set()
                if not_before:
                    resolved = resolve_known_glyph_names(not_before, glyph_names)
                    not_before_excluded = _expand_exclusions(resolved)
                    for not_before_glyph in sorted(not_before_excluded):
                        lines.append(f"        ignore sub {source_variant}' {not_before_glyph};")
                right_context_glyphs = set(
                    entry_exclusive[fwd_exit_y] if use_exclusive else entry_classes[fwd_exit_y]
                )
                _emit_narrow_mid_entry_strip_guards(
                    source_variant,
                    actual_variant,
                    fwd_exit_y,
                    right_context_glyphs - not_before_excluded,
                )
                kept_pair_fwd_followers = _select_rule_neighbors(
                    source_variant, actual_variant, right_context_glyphs, direction="fwd"
                )
                if kept_pair_fwd_followers == right_context_glyphs:
                    lines.append(f"        sub {source_variant}' {cls} by {actual_variant};")
                elif kept_pair_fwd_followers:
                    lines.append(
                        f"        sub {source_variant}' [{' '.join(sorted(kept_pair_fwd_followers))}] by {actual_variant};"
                    )
                lines.append(f"    }} calt_fwd_override_{safe};")

    def _late_context_glyphs() -> set[str]:
        """Return the stances that can appear after the first backward-pair pass.

        The set starts with the outputs of every substitution that can change a glyph during the forward calt passes (generic entry and exit substitutions, and pair overrides forward and backward, gated and ungated) and adds every stance generated from one of them. Pair-override outputs are included so the post-context pair lookups can match a consumer whose backtrack names a forward pair override's result (e.g. ``qsFee.en-y5`` after ``qsMay.ex-ext-1``). The post-context emitter keeps its own guards (``not_before``, entry-strip, terminal-entry-only ignores), so a wider set cannot revive a join that the original ``calt_pair_*`` rule blocks.
        """
        late: set[str] = set()
        for replacements in bk_replacements.values():
            late.update(replacements.values())
        for replacements in fwd_replacements.values():
            late.update(replacements.values())
        for upgrades in fwd_upgrades.values():
            late.update(entry_exit_var for entry_exit_var, _, _, _ in upgrades)
        for overrides in fwd_pair_overrides.values():
            late.update(variant_name for variant_name, _, _ in overrides)
        for overrides in pair_overrides.values():
            late.update(variant_name for variant_name, _ in overrides)
        for overrides in plan.gated_fwd_pair_overrides.values():
            late.update(variant_name for variant_name, _, _, _ in overrides)
        for overrides in plan.gated_pair_overrides.values():
            late.update(variant_name for variant_name, _, _ in overrides)

        changed = True
        while changed:
            changed = False
            for glyph_name, meta in glyph_meta.items():
                if meta.generated_from in late and glyph_name not in late:
                    late.add(glyph_name)
                    changed = True
        return late

    def _emit_post_context_bk_pairs():
        late_contexts = _late_context_glyphs()
        if not late_contexts:
            return

        for base_name in sorted(pair_overrides):
            for variant_name, after_glyphs in sorted(
                pair_overrides[base_name],
                key=lambda item: _backward_pair_sort_key(glyph_meta, item[0], item[1]),
            ):
                variant_meta = _meta(variant_name)
                expanded_after = _expand_backward_after_variants(
                    variant_name,
                    after_glyphs,
                    expand_selector=lambda glyph: _expand_all_variants([glyph]),
                    analysis=plan,
                )
                expanded_after &= late_contexts
                if not expanded_after:
                    continue
                after_list = " ".join(sorted(expanded_after))
                lookahead = ""
                if variant_meta.before:
                    pair_before_followers = _select_rule_neighbors(
                        base_name,
                        variant_name,
                        set(_expand_all_variants(variant_meta.before)),
                        direction="fwd",
                    )
                    before_list = " ".join(sorted(pair_before_followers))
                    if not before_list:
                        continue
                    lookahead = f" [{before_list}]"
                safe = variant_name.replace(".", "_").replace("-", "_")
                lines.append("")
                lines.append(f"    lookup calt_post_context_pair_{safe} {{")
                not_before = list(variant_meta.not_before)
                if not_before:
                    resolved = resolve_known_glyph_names(not_before, glyph_names)
                    for not_before_glyph in sorted(_expand_exclusions(resolved)):
                        lines.append(f"        ignore sub [{after_list}] {base_name}' {not_before_glyph};")
                entry_ys = set(variant_meta.entry_ys)
                if entry_ys:
                    for candidate_name in sorted(expanded_after):
                        guard_glyphs = _collect_pending_bk_pair_guards(
                            candidate_name,
                            entry_ys,
                            variant_meta.base_name,
                        )
                        if guard_glyphs:
                            guard_list = " ".join(sorted(guard_glyphs))
                            lines.append(f"        ignore sub [{guard_list}] {candidate_name} {base_name}';")
                for terminal in sorted(expanded_after & plan.terminal_entry_only):
                    lines.append(f"        ignore sub {terminal} {base_name}';")
                _emit_entry_strip_guards_for_replacement_exit(
                    base_name,
                    variant_name,
                    left_context=f"[{after_list}]",
                )
                lines.append(f"        sub [{after_list}] {base_name}'{lookahead} by {variant_name};")
                lines.append(f"    }} calt_post_context_pair_{safe};")

    def _emit_post_context_null_entry_revert():
        """Revert a follower's backward upgrade when its predecessor becomes an anchorless pair-override variant.

        A pair-override variant of a family X with `entry: null` and no exit (e.g. qsSee.after-ye) has no anchors. Until the late `calt_post_context_pair_*` lookup substitutes it, bare X is still in its `@exit_y*` classes, so a follower Y (e.g. qsZoo.half) takes a backward upgrade from X's exit. Once X is the anchorless variant nothing supports that upgrade, so these lookups revert Y to its base.
        """
        for base_name in sorted(pair_overrides):
            base_meta = glyph_meta.get(base_name)
            if base_meta is None:
                continue
            base_exit_ys = set(base_meta.exit_ys)
            if not base_exit_ys:
                continue
            for variant_name, after_glyphs in sorted(pair_overrides[base_name]):
                if variant_name not in glyph_names:
                    continue
                variant_meta = _meta(variant_name)
                if variant_meta.entry or variant_meta.entry_curs_only:
                    continue
                if variant_meta.exit:
                    continue
                expanded_after = _expand_backward_after_variants(
                    variant_name,
                    after_glyphs,
                    expand_selector=lambda glyph: _expand_all_variants([glyph]),
                    analysis=plan,
                )
                if not expanded_after:
                    continue
                emitted: list[tuple[str, str]] = []
                for follower_base in sorted(bk_replacements):
                    follower_meta = glyph_meta.get(follower_base)
                    if follower_meta is None:
                        continue
                    for entry_y, follower_variant in sorted(bk_replacements[follower_base].items()):
                        if entry_y not in base_exit_ys:
                            continue
                        if follower_variant == follower_base:
                            continue
                        if follower_variant not in glyph_names:
                            continue
                        follower_variant_meta = _meta(follower_variant)
                        if entry_y not in set(follower_variant_meta.entry_ys) and entry_y not in {
                            anchor[1] for anchor in follower_variant_meta.entry_curs_only
                        }:
                            continue
                        exclusions = bk_exclusions.get(follower_base, {}).get(entry_y, [])
                        if exclusions:
                            excluded = set(
                                _expand_exclusions(resolve_known_glyph_names(exclusions, glyph_names))
                            )
                            if base_name in excluded:
                                continue
                        emitted.append((follower_variant, follower_base))
                if not emitted:
                    continue
                safe = variant_name.replace(".", "_").replace("-", "_")
                lines.append("")
                lines.append(f"    lookup calt_post_context_revert_{safe} {{")
                for follower_variant, follower_base in sorted(set(emitted)):
                    lines.append(f"        sub {variant_name} {follower_variant}' by {follower_base};")
                lines.append(f"    }} calt_post_context_revert_{safe};")

    def _emit_block(bases: list[str], *, use_cycle: bool = False):
        for base_name in bases:
            if base_name not in early_pair_upgrade_bases:
                _emit_bk_pairs(base_name)
        for base_name in bases:
            if base_name in early_fwd_pairs:
                _emit_fwd_pairs(base_name)
        if use_cycle:
            _emit_bk_cycle(bases)
            # Lookups emitted after `calt_cycle` run after it, when mid glyphs have already taken their forward-stripping substitutions. Turning on the generic forward-strip guards makes the later lookups in this block, such as `calt_upgrade_*`, `calt_fwd_override_*`, and `calt_post_fwd_pair_*`, suppress predecessor promotions whose extension would reach a stripped mid.
            _fwd_strip_guards_active[0] = True
        else:
            for base_name in bases:
                _emit_bk_general(base_name)
        for base_name in bases:
            if base_name not in early_pair_upgrade_bases:
                _emit_upgrades(base_name)
            _emit_pair_fwd_overrides(base_name)
        _emit_noentry_fwd_overrides(bases)
        if use_cycle:
            _emit_post_upgrade_bk(bases)
            for base_name in bases:
                if base_name in early_fwd_pairs and _needs_post_cycle_fwd_pairs(base_name):
                    _emit_fwd_pairs(base_name, lookup_prefix="calt_post_fwd_pair_")
        for base_name in bases:
            if base_name not in plan.all_fwd_bases or base_name in early_pair_upgrade_bases:
                continue
            early_exit_ys = early_pair_fwd_general.get(base_name)
            if early_exit_ys is not None:
                if base_name not in early_fwd_pairs:
                    _emit_fwd_pairs(base_name)
                _emit_fwd_general(base_name, skip_exit_ys=early_exit_ys)
            elif base_name in early_fwd_pairs:
                _emit_fwd_general(base_name)
            else:
                _emit_fwd(base_name)

    for base_name in plan.fwd_only:
        if base_name in lig_fwd_bases:
            continue
        _emit_bk_pairs(base_name)
        _emit_pair_fwd_overrides(base_name)
        _emit_fwd(base_name)

    for base_name in plan.early_pair_fwd_general:
        if base_name in early_pair_upgrade_bases:
            continue
        _emit_fwd_general(
            base_name,
            only_exit_ys=early_pair_fwd_general[base_name],
            lookup_prefix="calt_fwd_early_",
        )

    for base_name in sorted(early_pair_upgrade_bases):
        _emit_bk_pairs(base_name)
        _emit_upgrades(base_name)
        _emit_fwd_general(base_name)

    pre_cycle: list[str] = []
    post_cycle: list[str] = []
    if cycle_bases:
        cycle_deps: set[str] = set()
        for cycle_base in cycle_bases:
            cycle_deps |= plan.edges.get(cycle_base, set())
        cycle_deps -= cycle_bases
        for base_name in all_bk_bases:
            if base_name in cycle_bases:
                continue
            if base_name in cycle_deps:
                pre_cycle.append(base_name)
            else:
                post_cycle.append(base_name)
    else:
        post_cycle = list(all_bk_bases)

    _emit_block(pre_cycle)

    cycle_list = sorted(cycle_bases) if cycle_bases else []
    if cycle_list:
        _emit_block(cycle_list, use_cycle=True)

    _fwd_strip_guards_active[0] = True

    early_post = [base for base in post_cycle if base in early_fwd_pairs]
    late_post = [base for base in post_cycle if base not in early_fwd_pairs]
    _emit_block(early_post)
    _emit_block(late_post)

    if cycle_list:
        _emit_post_override_bk(cycle_list)

    if cycle_bases:
        pair_only_new_exit_ys: set[int] = set()
        for pair_only_base in plan.pair_only:
            base_ys = set()
            if pair_only_base in glyph_meta:
                base_ys.update(glyph_meta[pair_only_base].exit_ys)
            for variant_name, _ in pair_overrides[pair_only_base]:
                for exit_y in _meta(variant_name).exit_ys:
                    if exit_y not in base_ys:
                        pair_only_new_exit_ys.add(exit_y)
        for cycle_base in sorted(cycle_bases):
            if cycle_base not in bk_replacements:
                continue
            variants = bk_replacements[cycle_base]
            relevant = {
                y: variant
                for y, variant in variants.items()
                if y in pair_only_new_exit_ys and y in exit_classes
            }
            if not relevant:
                continue
            safe = cycle_base.replace(".", "_").replace("-", "_")
            exclusions = bk_exclusions.get(cycle_base, {})
            lines.append("")
            bk_fwd_excl = plan.bk_fwd_exclusions
            bk_fwd_excl_seq = plan.bk_fwd_exclusion_sequences
            lines.append(f"    lookup calt_post_pair_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                fwd_excl = bk_fwd_excl.get(cycle_base, {}).get(entry_y)
                fwd_excl_sequences = bk_fwd_excl_seq.get(cycle_base, {}).get(entry_y, [])
                excl_tokens = _excl_tokens(fwd_excl, fwd_excl_sequences)
                if excluded:
                    filtered = sorted(
                        _select_rule_neighbors(
                            cycle_base, relevant[entry_y], exit_classes[entry_y] - excluded, direction="bk"
                        )
                    )
                    if filtered:
                        member_list = " ".join(filtered)
                        for tok in excl_tokens:
                            lines.append(f"        ignore sub [{member_list}] {cycle_base}' {tok};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            cycle_base,
                            relevant[entry_y],
                            left_context=f"[{member_list}]",
                        )
                        lines.append(f"        sub [{member_list}] {cycle_base}' by {relevant[entry_y]};")
                        for fpt in _fwd_pair_bk_targets(cycle_base, entry_y):
                            for tok in excl_tokens:
                                lines.append(f"        ignore sub [{member_list}] {fpt}' {tok};")
                            _emit_fpt_revert(
                                fpt,
                                relevant[entry_y],
                                member_set=set(filtered),
                                member_list_token=f"[{member_list}]",
                            )
                else:
                    candidate_preds = set(exit_classes[entry_y])
                    kept_preds = _select_rule_neighbors(
                        cycle_base, relevant[entry_y], candidate_preds, direction="bk"
                    )
                    for tok in excl_tokens:
                        lines.append(f"        ignore sub @exit_y{entry_y} {cycle_base}' {tok};")
                    _emit_entry_strip_guards_for_replacement_exit(
                        cycle_base,
                        relevant[entry_y],
                        left_context=f"@exit_y{entry_y}",
                    )
                    if kept_preds == candidate_preds:
                        lines.append(f"        sub @exit_y{entry_y} {cycle_base}' by {relevant[entry_y]};")
                    else:
                        lines.append(
                            f"        sub [{' '.join(sorted(kept_preds))}] {cycle_base}' by {relevant[entry_y]};"
                        )
                    for fpt in _fwd_pair_bk_targets(cycle_base, entry_y):
                        for tok in excl_tokens:
                            lines.append(f"        ignore sub @exit_y{entry_y} {fpt}' {tok};")
                        _emit_fpt_revert(
                            fpt,
                            relevant[entry_y],
                            member_set=set(exit_classes.get(entry_y, set())),
                            member_list_token=f"@exit_y{entry_y}",
                        )
            lines.append(f"    }} calt_post_pair_bk_{safe};")

    _emit_post_context_bk_pairs()

    _emit_post_context_null_entry_revert()

    _emit_reverse_upgrades()

    def _emit_exit_extended_bk_refinement():
        for base_name in sorted(fwd_pair_overrides):
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            exclusions = bk_exclusions.get(base_name, {})
            for fwd_var, _, _ in fwd_pair_overrides[base_name]:
                ext_suffix = _meta(fwd_var).extended_exit_suffix
                if not ext_suffix:
                    continue
                if _meta(fwd_var).entry:
                    continue
                emitted_any = False
                safe = fwd_var.replace(".", "_").replace("-", "_")
                fwd_exit_ys = set(_meta(fwd_var).exit_ys)
                for entry_y in sorted(variants.keys()):
                    bk_var = variants[entry_y]
                    combined = bk_var + ext_suffix
                    if combined not in glyph_names:
                        continue
                    if entry_y not in exit_classes:
                        continue
                    combined_exit_ys = set(_meta(combined).exit_ys)
                    if fwd_exit_ys and combined_exit_ys and not (fwd_exit_ys & combined_exit_ys):
                        continue
                    if not emitted_any:
                        lines.append("")
                        lines.append(f"    lookup calt_ext_bk_{safe} {{")
                        emitted_any = True
                    excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                    if excluded:
                        filtered = sorted(
                            _select_rule_neighbors(
                                fwd_var, combined, exit_classes[entry_y] - excluded, direction="bk"
                            )
                        )
                        if filtered:
                            member_list = " ".join(filtered)
                            lines.append(f"        sub [{member_list}] {fwd_var}' by {combined};")
                    else:
                        candidate_preds = set(exit_classes[entry_y])
                        kept_preds = _select_rule_neighbors(
                            fwd_var, combined, candidate_preds, direction="bk"
                        )
                        if kept_preds == candidate_preds:
                            lines.append(f"        sub @exit_y{entry_y} {fwd_var}' by {combined};")
                        else:
                            lines.append(
                                f"        sub [{' '.join(sorted(kept_preds))}] {fwd_var}' by {combined};"
                            )
                if emitted_any:
                    lines.append(f"    }} calt_ext_bk_{safe};")

    _emit_exit_extended_bk_refinement()

    pair_after_cache: dict[tuple[str, tuple[str, ...]], set[str]] = {}

    def _expanded_pair_after(
        variant_name: str,
        after_glyphs: list[str] | tuple[str, ...],
    ) -> set[str]:
        key = (variant_name, tuple(after_glyphs))
        expanded = pair_after_cache.get(key)
        if expanded is None:
            expanded = _expand_backward_after_variants(
                variant_name,
                after_glyphs,
                expand_selector=lambda glyph: _expand_all_variants([glyph]),
                analysis=plan,
            )
            pair_after_cache[key] = expanded
        return expanded

    def _post_liga_right_fallback(lig_target: str, base_name: str) -> str:
        if base_name not in bk_replacements:
            return base_name

        lig_exit_ys = set(_meta(lig_target).exit_ys)
        exclusions = bk_exclusions.get(base_name, {})
        for entry_y in sorted(bk_replacements[base_name]):
            if entry_y not in lig_exit_ys:
                continue
            excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
            if lig_target in excluded:
                continue
            return bk_replacements[base_name][entry_y]

        return base_name

    def _post_liga_left_fallback(lig_target: str, base_name: str) -> str:
        if base_name not in fwd_replacements:
            return base_name

        lig_entry_ys = set(_meta(lig_target).entry_ys)
        exclusions = fwd_exclusions.get(base_name, {})
        for exit_y in sorted(fwd_replacements[base_name]):
            if exit_y not in lig_entry_ys:
                continue
            excluded = set(_expand_exclusions(exclusions.get(exit_y, [])))
            if lig_target in excluded:
                continue
            return fwd_replacements[base_name][exit_y]

        return base_name

    def _exit_noentry_fallback(base_name: str, fallback_name: str) -> str | None:
        fallback_meta = glyph_meta.get(fallback_name)
        if fallback_meta is None:
            return None
        if not fallback_meta.exit:
            return None

        # The replacement must have the input's entry anchors, so a baseline-joining input (e.g. qsMay.en-y0) goes to a baseline-joining replacement (qsMay.en-y0.ex-noentry) and not to an entryless one. Among those, prefer the one whose modifiers are the input's without its ex-* modifiers, plus `ex-noentry`.
        expected_modifiers = frozenset(m for m in fallback_meta.modifiers if not m.startswith("ex-")) | {
            "ex-noentry"
        }

        candidates: list[tuple[tuple, str]] = []
        for candidate_name in sorted(base_to_variants.get(base_name, ())):
            candidate_meta = glyph_meta[candidate_name]
            if candidate_meta.is_noentry:
                continue
            if "ex-noentry" not in candidate_meta.modifiers:
                continue
            if candidate_meta.exit:
                continue
            if candidate_meta.entry != fallback_meta.entry:
                continue
            if candidate_meta.entry_curs_only != fallback_meta.entry_curs_only:
                continue
            if (
                candidate_meta.after
                or candidate_meta.before
                or candidate_meta.not_after
                or candidate_meta.not_before
            ):
                continue
            score = (
                frozenset(candidate_meta.modifiers) == expected_modifiers,
                candidate_meta.bitmap != fallback_meta.bitmap,
                -len(candidate_meta.modifiers),
            )
            candidates.append((score, candidate_name))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][1]

    def _entry_preserving_exit_noentry_handles_lig(candidate: str, lig_target: str) -> bool:
        """Return whether ``candidate``'s family has an ``.ex-noentry`` sibling that keeps ``candidate``'s entry, has no exit, and lists ``lig_target`` in its ``before``.

        The entryless ligature leaves ``candidate``'s exit unjoined, but a later forward pair lookup (`calt_*fwd_pair_<sibling>`) substitutes the sibling when the ligature follows. The post-liga left cleanup must then leave ``candidate`` alone, because demoting it to its entryless bare stance would remove the entry the sibling keeps. Like `has_entry_preserving_exit_noentry_sibling`, except that the sibling must list this ligature in its ``before``. The case it serves is `qsGay.en-y5.ex-noentry` before `qsTea_qsOy`.
        """
        candidate_meta = glyph_meta.get(candidate)
        if candidate_meta is None or not _has_left_entry(candidate_meta):
            return False
        expected_modifiers = frozenset(m for m in candidate_meta.modifiers if not m.startswith("ex-")) | {
            "ex-noentry"
        }
        for sibling_name in base_to_variants.get(candidate_meta.base_name, ()):
            sibling = glyph_meta.get(sibling_name)
            if sibling is None or sibling.is_noentry or sibling.exit:
                continue
            if "ex-noentry" not in sibling.modifiers:
                continue
            if sibling.entry != candidate_meta.entry:
                continue
            if sibling.entry_curs_only != candidate_meta.entry_curs_only:
                continue
            if frozenset(sibling.modifiers) != expected_modifiers:
                continue
            if lig_target in _expand_all_variants(sibling.before, include_base=True):
                return True
        return False

    def _collect_post_liga_right_cleanup_rules(
        lig_name: str,
        components: tuple[str, ...],
    ) -> list[tuple[str, str, str]]:
        if not components:
            return []

        component_targets = set(_ligature_component_variants(lig_name, components[-1], len(components) - 1))
        lig_targets = sorted(base_to_variants.get(lig_name, {lig_name}))
        seen: set[tuple[str, str, str]] = set()
        rules: list[tuple[str, str, str]] = []
        affected_bases = sorted(set(bk_replacements) | set(pair_overrides))

        for base_name in affected_bases:
            candidates: set[str] = set()

            for variant_name, after_glyphs in pair_overrides.get(base_name, []):
                if _expanded_pair_after(variant_name, after_glyphs) & component_targets:
                    candidates.add(variant_name)

            exclusions = bk_exclusions.get(base_name, {})
            for entry_y, variant_name in bk_replacements.get(base_name, {}).items():
                excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                if any(
                    entry_y in set(_meta(component_target).exit_ys) and component_target not in excluded
                    for component_target in component_targets
                ):
                    candidates.add(variant_name)

            if not candidates:
                continue

            for lig_target in lig_targets:
                fallback = _post_liga_right_fallback(lig_target, base_name)
                protected = {
                    variant_name
                    for variant_name, after_glyphs in pair_overrides.get(base_name, [])
                    if lig_target in _expanded_pair_after(variant_name, after_glyphs)
                }
                for candidate in sorted(candidates):
                    if candidate in protected:
                        continue
                    replacement = _resolve_entryless_replacement(
                        glyph_meta,
                        base_to_variants,
                        candidate,
                        fallback,
                    )
                    if replacement is None or replacement == candidate:
                        continue
                    rule = (lig_target, candidate, replacement)
                    if rule in seen:
                        continue
                    seen.add(rule)
                    rules.append(rule)

        return rules

    def _collect_post_liga_left_cleanup_rules(
        lig_name: str,
        components: tuple[str, ...],
    ) -> list[tuple[str, str, str]]:
        if not components:
            return []

        lead_targets = set(_ligature_component_variants(lig_name, components[0], 0))
        lig_targets = sorted(base_to_variants.get(lig_name, {lig_name}))
        seen: set[tuple[str, str, str]] = set()
        rules: list[tuple[str, str, str]] = []
        affected_bases = sorted(set(fwd_replacements) | set(fwd_pair_overrides) | set(pair_overrides))

        for base_name in affected_bases:
            candidates: set[str] = set()

            for variant_name, after_glyphs in pair_overrides.get(base_name, []):
                if _expanded_pair_after(variant_name, after_glyphs) & lead_targets:
                    candidates.add(variant_name)

            for variant_name, before_glyphs, _not_after_glyphs in fwd_pair_overrides.get(base_name, []):
                if _expand_all_variants(before_glyphs) & lead_targets:
                    candidates.add(variant_name)

            exclusions = fwd_exclusions.get(base_name, {})
            for exit_y, variant_name in fwd_replacements.get(base_name, {}).items():
                excluded = set(_expand_exclusions(exclusions.get(exit_y, [])))
                if any(
                    exit_y in set(_meta(lead_target).entry_ys) and lead_target not in excluded
                    for lead_target in lead_targets
                ):
                    candidates.add(variant_name)

            if not candidates:
                continue

            for lig_target in lig_targets:
                fallback = _post_liga_left_fallback(lig_target, base_name)
                protected = {
                    variant_name
                    for variant_name, after_glyphs in pair_overrides.get(base_name, [])
                    if lig_target in _expanded_pair_after(variant_name, after_glyphs)
                }
                protected |= {
                    variant_name
                    for variant_name, before_glyphs, _ in fwd_pair_overrides.get(base_name, [])
                    if lig_target in _expand_all_variants(before_glyphs)
                }
                for candidate in sorted(candidates):
                    if candidate in protected:
                        continue
                    if _entry_preserving_exit_noentry_handles_lig(candidate, lig_target):
                        continue
                    replacement = _resolve_entryless_replacement(
                        glyph_meta,
                        base_to_variants,
                        candidate,
                        fallback,
                    )
                    if replacement is None or replacement == candidate:
                        continue
                    rule = (lig_target, candidate, replacement)
                    if rule in seen:
                        continue
                    seen.add(rule)
                    rules.append(rule)

        return rules

    def _collect_noentry_after_left_cleanup_rules(
        lig_name: str,
    ) -> list[tuple[str, str, str]]:
        lig_meta = _meta(lig_name)
        if not lig_meta.noentry_after:
            return []

        noentry_name = lig_name + ".noentry"
        if noentry_name not in glyph_names:
            return []

        seen: set[tuple[str, str, str]] = set()
        rules: list[tuple[str, str, str]] = []
        for candidate in sorted(_expand_all_variants(lig_meta.noentry_after, include_base=True)):
            candidate_meta = glyph_meta.get(candidate)
            if candidate_meta is None or not candidate_meta.exit:
                continue
            replacement = _exit_noentry_fallback(candidate_meta.base_name, candidate)
            if replacement is None or replacement == candidate:
                continue
            rule = (noentry_name, candidate, replacement)
            if rule in seen:
                continue
            seen.add(rule)
            rules.append(rule)

        return rules

    def _collect_noentry_after_pre_predecessor_revert_rules(
        demotion_rules: list[tuple[str, str, str]],
    ) -> list[tuple[str, str, str]]:
        # When `_collect_noentry_after_left_cleanup_rules` demotes a predecessor to an entryless `.ex-noentry` replacement, a glyph whose `before:` clause selected its variant for the demoted family now extends toward a glyph with no entry. Revert that pre-predecessor variant to its bare base when the replacement follows it, so its join stroke doesn't dangle. This is a separate lookup after the demotion lookup so its lookahead can match the replacement. A replacement that keeps an entry is skipped, because the pre-predecessor's exit can still join it.
        seen: set[tuple[str, str, str]] = set()
        rules: list[tuple[str, str, str]] = []
        for _lig_target, candidate, replacement in demotion_rules:
            replacement_meta = glyph_meta.get(replacement)
            if replacement_meta is None:
                continue
            if replacement_meta.entry or replacement_meta.entry_curs_only:
                continue
            candidate_meta = glyph_meta.get(candidate)
            if candidate_meta is None:
                continue
            candidate_base = candidate_meta.base_name
            for pre_pred_base, entries in fwd_pair_overrides.items():
                if pre_pred_base not in glyph_names:
                    continue
                for variant_name, before_glyphs, _not_after_glyphs in entries:
                    expanded = _expand_all_variants(before_glyphs, include_base=True)
                    if candidate not in expanded and candidate_base not in expanded:
                        continue
                    if variant_name == pre_pred_base:
                        continue
                    rule = (replacement, variant_name, pre_pred_base)
                    if rule in seen:
                        continue
                    seen.add(rule)
                    rules.append(rule)
        return rules

    if ligatures:
        from itertools import product

        # Ligatures are formed inside `calt`, after `calt_cycle`'s stance selection, instead of in `liga`. A forward `calt` rule can then change a component first (e.g. qsUtter -> qsUtter.alt in ·Day·Utter·Low), which stops the `qsDay qsUtter` ligature from matching. Putting these rules in `liga` would run ligation as its own feature pass and lose that ordering.
        # A name built by appending a suffix (`lig_name + ".half"`, `actual_lig + ".ex-ext-1"`) lacks the anchor modifiers `_synthesize_anchor_modifiers` adds (e.g. `qsDay_qsUtter.half` compiles as `qsDay_qsUtter.half.en-y0.ex-y5`), so `heal_glyph_name` rewrites it to the compiled name before the lookup in `glyph_names`.
        _lig_family_names = family_names_from_compiled(glyph_names)
        _lig_available_names = frozenset(glyph_names)

        def _resolve(name: str) -> str:
            return heal_glyph_name(name, _lig_family_names, _lig_available_names)

        lines.append("")
        lines.append("    lookup calt_liga {")
        for lig_name, components in sorted(ligatures):
            variant_sets: list[list[str]] = []
            for index, component in enumerate(components):
                variant_sets.append(sorted(_ligature_component_variants(lig_name, component, index)))
            for combo in product(*variant_sets):
                component_str = " ".join(combo)
                actual_lig = lig_name
                suffix = _meta(combo[0]).extended_entry_suffix
                if suffix:
                    ext_lig = _resolve(lig_name + suffix)
                    if ext_lig not in glyph_names:
                        ext_lig = _resolve(lig_name + ".en-ext-1")
                    if ext_lig in glyph_names:
                        # Use the entry-extended ligature only when its entry Ys overlap the lead component's (or either has none). `.en-ext-1` extends a different entry on different stances (`qsDay.half.en-y0.ex-y0.en-ext-1` at y=0, `qsDay_qsEat.en-ext-1` at y=5), so copying it across Ys gives an extension unrelated to the predecessor join that triggered it.
                        ext_lig_entry_ys = set(_meta(ext_lig).entry_ys)
                        combo_entry_ys = set(_meta(combo[0]).entry_ys)
                        if not combo_entry_ys or not ext_lig_entry_ys or combo_entry_ys & ext_lig_entry_ys:
                            actual_lig = ext_lig
                contracted_entry_suffix = _meta(combo[0]).contracted_entry_suffix
                if contracted_entry_suffix:
                    contracted_lig = _resolve(lig_name + contracted_entry_suffix)
                    if contracted_lig in glyph_names:
                        actual_lig = contracted_lig
                if "half" in _meta(combo[0]).traits:
                    half_lig = _resolve(lig_name + ".half")
                    if half_lig in glyph_names:
                        actual_lig = half_lig
                exit_suffix = _meta(combo[-1]).extended_exit_suffix
                if exit_suffix:
                    ext_lig = _resolve(actual_lig + ".ex-ext-1")
                    if ext_lig in glyph_names:
                        actual_lig = ext_lig
                contracted_suffix = _meta(combo[-1]).contracted_exit_suffix
                if contracted_suffix:
                    contracted_lig = _resolve(actual_lig + contracted_suffix)
                    if contracted_lig in glyph_names:
                        actual_lig = contracted_lig
                lines.append(f"        sub {component_str} by {actual_lig};")
        lines.append("    } calt_liga;")

        lig_glyph_names = {lig_name for lig_name, _ in ligatures}
        post_liga_cleanup_rules: list[tuple[str, str, str]] = []
        for lig_name, components in sorted(ligatures):
            post_liga_cleanup_rules.extend(_collect_post_liga_right_cleanup_rules(lig_name, components))
        post_liga_rules: list[tuple[str, str, list[str]]] = []
        for base_name in sorted(pair_overrides):
            for variant_name, after_glyphs in pair_overrides[base_name]:
                if not any(glyph in lig_glyph_names for glyph in after_glyphs):
                    continue
                expanded_after = _expanded_pair_after(variant_name, after_glyphs)
                if base_name in lig_glyph_names:
                    # When the glyph being substituted is itself a ligature, it exists only after `calt_liga`, so its `calt_post_context_pair_*` lookup can never match, and a non-ligature predecessor (e.g. ·See's baseline exit before the ·Out·Tea ligature) has had no chance to trigger the upgrade. Keep the full predecessor class so the rule fires here.
                    ligature_after = sorted(expanded_after)
                else:
                    # Otherwise keep only the ligature predecessors. This lookup redoes stance selection when a ligature becomes the immediate predecessor. The non-ligature predecessors would make it fire on plain sequences whose predecessor changed during `calt_cycle` (e.g. in `qsUtter qsThey qsJay`, forward extension turns qsUtter into qsUtter.ex-ext-1 after qsThey's backward lookup declined to fire).
                    ligature_after = sorted(
                        glyph
                        for glyph in expanded_after
                        if glyph in glyph_meta and glyph_meta[glyph].base_name in lig_glyph_names
                    )
                if ligature_after:
                    post_liga_rules.append((base_name, variant_name, ligature_after))

        noentry_after_variants: set[str] = set()
        for lig_name in sorted(lig_glyph_names):
            noentry_after = _meta(lig_name).noentry_after
            if not noentry_after:
                continue
            noentry_name = lig_name + ".noentry"
            if noentry_name not in glyph_names:
                continue
            noentry_after_variants.add(noentry_name)
            post_liga_rules.append(
                (
                    lig_name,
                    noentry_name,
                    sorted(_expand_all_variants(noentry_after, include_base=True)),
                )
            )

        if post_liga_cleanup_rules:
            lines.append("")
            lines.append("    lookup calt_post_liga_cleanup {")
            lines.extend(_format_post_liga_cleanup_rules(post_liga_cleanup_rules))
            lines.append("    } calt_post_liga_cleanup;")

        # `calt_post_reflip_bk_*` re-fires a follower's backward substitution after a predecessor that reached a `restore_isolated_form_overrides` isolated form after the follower's backward lookups ran, but it names only the bare follower. A ligature led by that follower needs the same re-fire, and it must run before the ligature's `noentry_after` lookup claims it, so that `qsIt.en-y5.ex-y0 qsDay_qsEat` takes `qsDay_qsEat.half.en-y0.ex-y0` as `qsIt.en-y5.ex-y0 qsDay` takes `qsDay.half.en-y0.ex-y0`.
        lig_reflip_rules: dict[str, set[tuple[str, str]]] = {}
        for _prior, _target_base, follower_base, isolated_form in plan.restore_isolated_form_overrides:
            isolated_meta = glyph_meta.get(isolated_form)
            if isolated_form not in glyph_names or isolated_meta is None:
                continue
            for lig_name, _components in ligatures_by_first_component.get(follower_base, ()):
                lig_bk = bk_replacements.get(lig_name, {})
                for exit_y in sorted(set(isolated_meta.exit_ys)):
                    replacement = lig_bk.get(exit_y)
                    if replacement and replacement != lig_name and replacement in glyph_names:
                        lig_reflip_rules.setdefault(lig_name, set()).add((isolated_form, replacement))
        for lig_name in sorted(lig_reflip_rules):
            safe = lig_name.replace(".", "_").replace("-", "_")
            lines.append("")
            lines.append(f"    lookup calt_post_liga_reflip_bk_{safe} {{")
            for isolated_form, replacement in sorted(lig_reflip_rules[lig_name]):
                lines.append(f"        sub {isolated_form} {lig_name}' by {replacement};")
            lines.append(f"    }} calt_post_liga_reflip_bk_{safe};")

        # One lookup per variant. In a GSUB type-6 lookup, an `ignore` rule that matches at a position stops every later subtable of that lookup there, so if variants shared a lookup, one variant's `not_before` ignore could block another variant's substitution.
        for base_name, variant_name, after_glyphs in post_liga_rules:
            after_list = " ".join(sorted(after_glyphs))
            variant_meta = _meta(variant_name)
            lookahead = ""
            if variant_meta.before:
                pair_before_followers = _select_rule_neighbors(
                    base_name,
                    variant_name,
                    set(_expand_all_variants(variant_meta.before)),
                    direction="fwd",
                )
                before_list = " ".join(sorted(pair_before_followers))
                if not before_list:
                    continue
                lookahead = f" [{before_list}]"
            not_before_glyphs: list[str] = []
            if variant_meta.not_before:
                resolved_nb = resolve_known_glyph_names(variant_meta.not_before, glyph_names)
                not_before_glyphs = sorted(_expand_exclusions(resolved_nb))
            targets = {base_name}
            if base_name in bk_replacements:
                targets.update(bk_replacements[base_name].values())
            if variant_name in noentry_after_variants:
                # A ligature stance that declares no `noentry_after` of its own (`qsDay_qsEat.half`) keeps its entry after the listed families.
                targets = {target for target in targets if _meta(target).noentry_after}
            safe = variant_name.replace(".", "_").replace("-", "_")
            lines.append("")
            lines.append(f"    lookup calt_post_liga_{safe} {{")
            for target in sorted(targets):
                for nb_glyph in not_before_glyphs:
                    lines.append(f"        ignore sub [{after_list}] {target}' {nb_glyph};")
                lines.append(f"        sub [{after_list}] {target}'{lookahead} by {variant_name};")
            lines.append(f"    }} calt_post_liga_{safe};")

        post_liga_left_cleanup_rules: list[tuple[str, str, str]] = []
        post_liga_left_cleanup_pred_rules: list[tuple[str, str, str]] = []
        for lig_name, components in sorted(ligatures):
            if _meta(lig_name).entry_explicitly_none:
                post_liga_left_cleanup_rules.extend(
                    _collect_post_liga_left_cleanup_rules(lig_name, components)
                )
            noentry_rules = _collect_noentry_after_left_cleanup_rules(lig_name)
            post_liga_left_cleanup_rules.extend(noentry_rules)
            post_liga_left_cleanup_pred_rules.extend(
                _collect_noentry_after_pre_predecessor_revert_rules(noentry_rules)
            )

        if post_liga_left_cleanup_rules:
            lines.append("")
            lines.append("    lookup calt_post_liga_left_cleanup {")
            lines.extend(_format_post_liga_left_cleanup_rules(post_liga_left_cleanup_rules))
            lines.append("    } calt_post_liga_left_cleanup;")

        if post_liga_left_cleanup_pred_rules:
            lines.append("")
            lines.append("    lookup calt_post_liga_left_cleanup_pred {")
            lines.extend(_format_post_liga_left_cleanup_rules(post_liga_left_cleanup_pred_rules))
            lines.append("    } calt_post_liga_left_cleanup_pred;")

        for base_name in sorted(lig_fwd_bases):
            _emit_fwd(base_name)

    # Register a re-flip for every `restore_isolated_form_overrides` entry. These are cases the check in `_record_pair_guard_reflip` (the isolated form's exit Y must meet the follower's plain entry Y) would reject. For ·It before ·No (e.g. qsJay qsIt qsNo), that check sees qsNo's plain entry at the x-height and rejects qsIt.ex-y0, but the post-reflip follower backward lookup re-fires qsNo.alt anyway.
    for prior_base, target_base, follower_base, isolated_form in plan.restore_isolated_form_overrides:
        if isolated_form not in glyph_names:
            continue
        isolated_meta = glyph_meta.get(isolated_form)
        if isolated_meta is None or not isolated_meta.exit:
            continue
        isolated_exit_ys = set(isolated_meta.exit_ys)
        target_fwd = fwd_replacements.get(target_base, {})
        if not target_fwd:
            continue
        # The stance to replace is a forward replacement at an exit Y the isolated form doesn't have: the stance the follower's default entry selected for the target. Limiting pre_stances to these keeps the rules from covering every stance of the target.
        pre_stances: set[str] = set()
        for fwd_exit_y, fwd_variant in target_fwd.items():
            if fwd_exit_y in isolated_exit_ys:
                continue
            if fwd_variant in glyph_names and fwd_variant != isolated_form:
                pre_stances.add(fwd_variant)
        if not pre_stances:
            continue
        prior_slot = frozenset(_fwd_pair_source_slot(prior_base) & glyph_names)
        if not prior_slot:
            continue
        follower_variants = sorted(base_to_variants.get(follower_base, set()) & glyph_names)
        if not follower_variants:
            continue
        bucket = pair_guard_reflip.setdefault(target_base, [])
        for pre_stance in sorted(pre_stances):
            for follower_variant in follower_variants:
                entry = (prior_slot, pre_stance, follower_variant, isolated_form)
                if entry not in bucket:
                    bucket.append(entry)

    # Emit the re-flips recorded in `pair_guard_reflip`. Each rule changes the candidate's stance back to its isolated form in the (prior_slot, candidate, follower) context of the guard or override that caused it. These lookups come after the `calt_fwd_*` lookups, which produce the stance being replaced, and after the backward lookups that emitted the guards.
    for candidate_base in sorted(pair_guard_reflip):
        rules = pair_guard_reflip[candidate_base]
        reflip_seen: set[tuple[frozenset[str], str, str, str]] = set()
        reflip_unique: list[tuple[frozenset[str], str, str, str]] = []
        for entry in rules:
            if entry in reflip_seen:
                continue
            reflip_seen.add(entry)
            reflip_unique.append(entry)
        if not reflip_unique:
            continue
        safe = candidate_base.replace(".", "_").replace("-", "_")
        lines.append("")
        lines.append(f"    lookup calt_pair_guard_reflip_{safe} {{")
        for prior_slot, pre_stance, base_name, isolated_form in sorted(
            reflip_unique,
            key=lambda item: (item[2], item[1], item[3], sorted(item[0])),
        ):
            prior_list = " ".join(sorted(prior_slot))
            lines.append(f"        sub [{prior_list}] {pre_stance}' {base_name} by {isolated_form};")
        lines.append(f"    }} calt_pair_guard_reflip_{safe};")

    # Re-apply each forward replacement's `extend_exit_before` refinement, keyed only on the follower. A forward reselection (`calt_fwd_*`) or a pair-guard re-flip can move a glyph onto its bare baseline-exit forward replacement after the earlier `extend_exit_before` lookups ran on its previous stance, which loses the connecting pixel (e.g. `qsIt.en-y0.ex-y5 -> qsIt.ex-y0` before qsI, when ·It's predecessor joins it backward and the follower then reselects it). These lookups run before `calt_pred_demote_*`, so a predecessor that joined the extended glyph still demotes, because its trigger list includes the `.ex-ext-N` variants. A glyph already on its extended stance doesn't match the bare `variant'` input, so the extension cannot apply twice.
    for base in sorted(fwd_replacements):
        ext_rules: list[tuple[str, str, str]] = []
        ext_seen: set[tuple[str, str, str]] = set()
        for variant in sorted(set(fwd_replacements[base].values())):
            if variant not in glyph_meta:
                continue
            for extended_var, trigger_glyphs in _exit_extension_refinements(variant, set(glyph_names)):
                trigger_list = " ".join(sorted(trigger_glyphs))
                entry = (variant, trigger_list, extended_var)
                if entry in ext_seen:
                    continue
                ext_seen.add(entry)
                ext_rules.append(entry)
        if not ext_rules:
            continue
        safe = base.replace(".", "_").replace("-", "_")
        lines.append("")
        lines.append(f"    lookup calt_post_reflip_ext_{safe} {{")
        for variant, trigger_list, extended_var in ext_rules:
            lines.append(f"        sub {variant}' [{trigger_list}] by {extended_var};")
        lines.append(f"    }} calt_post_reflip_ext_{safe};")

    # For each `restore_isolated_form_overrides` entry whose isolated form has an exit at Y, the re-flip runs after the follower's backward lookups, which therefore missed `bk_replacements[follower][Y]`. Re-fire that substitution after the isolated form, with the same `not_before` ignore rules and entry-strip guards the earlier backward lookups use.
    post_reflip_emissions: dict[str, dict[tuple[int, str], set[str]]] = {}
    for _prior, _target_base, follower_base, isolated_form in plan.restore_isolated_form_overrides:
        if isolated_form not in glyph_names:
            continue
        isolated_meta = glyph_meta.get(isolated_form)
        if isolated_meta is None or not isolated_meta.exit:
            continue
        follower_bk = bk_replacements.get(follower_base)
        if not follower_bk:
            continue
        for exit_y in sorted(set(isolated_meta.exit_ys)):
            replacement = follower_bk.get(exit_y)
            if not replacement or replacement == follower_base:
                continue
            if replacement not in glyph_names:
                continue
            post_reflip_emissions.setdefault(follower_base, {}).setdefault((exit_y, replacement), set()).add(
                isolated_form
            )
    for follower_base in sorted(post_reflip_emissions):
        safe = follower_base.replace(".", "_").replace("-", "_")
        lines.append("")
        lines.append(f"    lookup calt_post_reflip_bk_{safe} {{")
        for (entry_y, replacement), isolated_forms in sorted(post_reflip_emissions[follower_base].items()):
            sorted_iso_forms = sorted(isolated_forms)
            prior_token = (
                sorted_iso_forms[0] if len(sorted_iso_forms) == 1 else "[" + " ".join(sorted_iso_forms) + "]"
            )
            fwd_excl = plan.bk_fwd_exclusions.get(follower_base, {}).get(entry_y)
            fwd_excl_seq = plan.bk_fwd_exclusion_sequences.get(follower_base, {}).get(entry_y, [])
            for token in _excl_tokens(fwd_excl, fwd_excl_seq):
                lines.append(f"        ignore sub {prior_token} {follower_base}' {token};")
            _emit_entry_strip_guards_for_replacement_exit(
                follower_base,
                replacement,
                left_context=prior_token,
            )
            lines.append(f"        sub {prior_token} {follower_base}' by {replacement};")
        lines.append(f"    }} calt_post_reflip_bk_{safe};")

    if cycle_bases:
        for cycle_base in sorted(cycle_bases):
            if cycle_base in early_fwd_pairs and _needs_post_cycle_fwd_pairs(cycle_base):
                _emit_fwd_pairs(cycle_base, lookup_prefix="calt_final_fwd_pair_")

    trailing_demote_by_base: dict[str, list[tuple[str, str, str]]] = {}
    for leader_stance, trailing_stance, isolated_form in plan.trailing_demote_overrides:
        if leader_stance not in glyph_names:
            continue
        if trailing_stance not in glyph_names:
            continue
        if isolated_form not in glyph_names:
            continue
        trailing_meta = glyph_meta.get(trailing_stance)
        if trailing_meta is None:
            continue
        trailing_demote_by_base.setdefault(trailing_meta.base_name, []).append(
            (leader_stance, trailing_stance, isolated_form)
        )

    def _emit_trailing_demote_lookups(prefix: str) -> None:
        for trailing_base in sorted(trailing_demote_by_base):
            rules = trailing_demote_by_base[trailing_base]
            seen: set[tuple[str, str, str]] = set()
            unique: list[tuple[str, str, str]] = []
            for rule in rules:
                if rule in seen:
                    continue
                seen.add(rule)
                unique.append(rule)
            if not unique:
                continue
            safe = trailing_base.replace(".", "_").replace("-", "_")
            lines.append("")
            lines.append(f"    lookup {prefix}_{safe} {{")
            for leader_stance, trailing_stance, isolated_form in sorted(unique):
                lines.append(f"        sub {leader_stance} {trailing_stance}' by {isolated_form};")
            lines.append(f"    }} {prefix}_{safe};")

    _emit_trailing_demote_lookups("calt_trailing_demote")

    # Collect predecessor-demote rules: the `predecessor_demote_overrides` entries and the rules derived below. Each rule returns an extended predecessor to its isolated form when the trigger after it is in a stance that can't receive its exit. The lookups run late, when the trigger's stance already shows whether the join is broken, so most rules need no backtrack glyph. Rules are grouped by predecessor base so lookup names are stable.
    pred_demote_by_base: dict[str, list[tuple[str | None, str, str, str]]] = {}
    for (
        backtrack_stance,
        predecessor_stance,
        trigger_stance,
        isolated_form,
    ) in plan.predecessor_demote_overrides:
        if predecessor_stance not in glyph_names:
            continue
        if trigger_stance not in glyph_names:
            continue
        if isolated_form not in glyph_names:
            continue
        if backtrack_stance is not None and backtrack_stance not in glyph_names:
            continue
        pred_meta = glyph_meta.get(predecessor_stance)
        if pred_meta is None:
            continue
        pred_demote_by_base.setdefault(pred_meta.base_name, []).append(
            (backtrack_stance, predecessor_stance, trigger_stance, isolated_form)
        )

    def _drop_exit_extension_suffix(name: str) -> str | None:
        meta = glyph_meta.get(name)
        if meta is None or meta.extended_exit_suffix is None:
            return None
        candidate = name.replace(meta.extended_exit_suffix, "", 1)
        return candidate if candidate in glyph_names else None

    def _derived_pred_demote_iso_form(name: str) -> str | None:
        isolated_form = _drop_exit_extension_suffix(name)
        if isolated_form is not None:
            return isolated_form
        meta = glyph_meta.get(name)
        if meta is not None and meta.base_name == "qsShe" and name == "qsShe.ex-y0":
            return meta.base_name
        return None

    def _drop_entry_extension_suffix(name: str) -> str | None:
        meta = glyph_meta.get(name)
        if meta is None or meta.extended_entry_suffix is None:
            return None
        candidate = name.replace(meta.extended_entry_suffix, "", 1)
        return candidate if candidate in glyph_names else None

    def _derived_strip_guard_lookup_names(replacement_name: str) -> tuple[str, ...]:
        seen: set[str] = set()
        queue = deque([replacement_name.replace(".noentry", "")])
        ordered: list[str] = []
        while queue:
            candidate = queue.popleft()
            if candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
            candidate_meta = glyph_meta.get(candidate)
            if candidate_meta is None:
                continue
            suffixes = [
                candidate_meta.extended_entry_suffix,
                candidate_meta.contracted_entry_suffix,
            ]
            suffixes.extend(
                f".{modifier}" for modifier in candidate_meta.modifiers if modifier.startswith("en-trim-")
            )
            for suffix in suffixes:
                if suffix and suffix in candidate:
                    queue.append(candidate.replace(suffix, ""))
        if replacement_name not in seen:
            ordered.append(replacement_name)
        return tuple(ordered)

    derived_pred_demote_bases = {"qsEight", "qsJai", "qsLow", "qsNo", "qsShe", "qsUtter"}

    def _derived_trigger_stances(mid_base: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name in glyph_names
                if name in glyph_meta
                and (
                    _meta(name).base_name == mid_base
                    or bool(_meta(name).sequence and _meta(name).sequence[0] == mid_base)
                )
            )
        )

    for predecessor_stance in sorted(glyph_names):
        pred_meta = glyph_meta.get(predecessor_stance)
        if pred_meta is None:
            continue
        isolated_form = _drop_exit_extension_suffix(predecessor_stance)
        if isolated_form is not None and pred_meta.before:
            trigger_stances = _expand_all_variants(pred_meta.before, include_base=True)
            for trigger_stance in sorted(trigger_stances):
                trigger_meta = glyph_meta.get(trigger_stance)
                if trigger_meta is None:
                    continue
                if any(exit_y in set(trigger_meta.all_entry_ys) for exit_y in pred_meta.exit_ys):
                    continue
                pred_demote_by_base.setdefault(pred_meta.base_name, []).append(
                    (None, predecessor_stance, trigger_stance, isolated_form)
                )
        if pred_meta.base_name not in derived_pred_demote_bases:
            continue
        derived_iso_form = _derived_pred_demote_iso_form(predecessor_stance)
        if derived_iso_form is None:
            continue
        for exit_y in set(pred_meta.exit_ys):
            guard_entries = tuple(
                guard
                for replacement_lookup in _derived_strip_guard_lookup_names(predecessor_stance)
                for guard in _derived_fwd_strip_guards.get(
                    (pred_meta.base_name, replacement_lookup, exit_y),
                    (),
                )
            )
            for guard in guard_entries:
                for trigger_stance in _derived_trigger_stances(guard.mid_base):
                    trigger_meta = glyph_meta.get(trigger_stance)
                    if trigger_meta is None:
                        continue
                    if exit_y in set(trigger_meta.all_entry_ys):
                        continue
                    pred_demote_by_base.setdefault(pred_meta.base_name, []).append(
                        (None, predecessor_stance, trigger_stance, derived_iso_form)
                    )

    # Map each stance to the exit-extension stances derived from it (e.g. qsIt.ex-y0 -> [qsIt.ex-y0.ex-ext-1]). A predecessor-demote trigger can't receive the predecessor's exit, and an exit extension adds no entry, so the trigger's `.ex-ext-N` variants must trigger the same demote. Without them, in ·Excite·It·I with ·It on qsIt.ex-y0.ex-ext-1, qsExcite keeps its before-vertical stance and its exit dangles.
    exit_ext_variants_by_stance: dict[str, list[str]] = {}
    for _candidate in glyph_names:
        _base_stance = _drop_exit_extension_suffix(_candidate)
        if _base_stance is not None:
            exit_ext_variants_by_stance.setdefault(_base_stance, []).append(_candidate)

    def _trigger_with_exit_extensions(trigger_stance: str) -> list[str]:
        out = [trigger_stance]
        queue = deque([trigger_stance])
        seen = {trigger_stance}
        while queue:
            current = queue.popleft()
            for variant in exit_ext_variants_by_stance.get(current, ()):
                if variant in seen:
                    continue
                seen.add(variant)
                out.append(variant)
                queue.append(variant)
        return out

    def _emit_pred_demote_lookups(prefix: str) -> None:
        for predecessor_base in sorted(pred_demote_by_base):
            pred_rules = pred_demote_by_base[predecessor_base]
            pred_seen: set[tuple[str | None, str, str, str]] = set()
            pred_unique: list[tuple[str | None, str, str, str]] = []
            for backtrack_stance, predecessor_stance, trigger_stance, isolated_form in pred_rules:
                for expanded_trigger in _trigger_with_exit_extensions(trigger_stance):
                    entry = (backtrack_stance, predecessor_stance, expanded_trigger, isolated_form)
                    if entry in pred_seen:
                        continue
                    pred_seen.add(entry)
                    pred_unique.append(entry)
            if not pred_unique:
                continue
            safe = predecessor_base.replace(".", "_").replace("-", "_")
            lines.append("")
            lines.append(f"    lookup {prefix}_{safe} {{")
            for backtrack_stance, predecessor_stance, trigger_stance, isolated_form in sorted(
                pred_unique, key=lambda e: (e[1], e[2], e[3], e[0] or "")
            ):
                if backtrack_stance is None:
                    lines.append(f"        sub {predecessor_stance}' {trigger_stance} by {isolated_form};")
                else:
                    lines.append(
                        f"        sub {backtrack_stance} {predecessor_stance}' {trigger_stance} by {isolated_form};"
                    )
            lines.append(f"    }} {prefix}_{safe};")

    _emit_pred_demote_lookups("calt_pred_demote")

    noentry_exit_contract_by_base: dict[str, list[tuple[str, str, str]]] = {}
    for source_stance in sorted(glyph_names):
        source_meta = glyph_meta.get(source_stance)
        if source_meta is None or source_meta.contract_exit_before is None:
            continue
        if not source_meta.exit:
            continue
        suffix_word = _EXIT_CONTRACTION_WORD_BY_COUNT.get(source_meta.contract_exit_before.by)
        if suffix_word is None:
            continue
        noentry_stance = f"{source_stance}.noentry"
        replacement = f"{noentry_stance}.ex-{suffix_word}"
        if noentry_stance not in glyph_names or replacement not in glyph_names:
            continue
        trigger_stances = _expand_all_variants(source_meta.contract_exit_before.targets, include_base=True)
        for trigger_stance in sorted(trigger_stances):
            trigger_meta = glyph_meta.get(trigger_stance)
            if trigger_meta is None:
                continue
            if not any(exit_y in set(trigger_meta.all_entry_ys) for exit_y in source_meta.exit_ys):
                continue
            noentry_exit_contract_by_base.setdefault(source_meta.base_name, []).append(
                (noentry_stance, trigger_stance, replacement)
            )

    for source_base in sorted(noentry_exit_contract_by_base):
        rules = noentry_exit_contract_by_base[source_base]
        seen: set[tuple[str, str, str]] = set()
        unique_rules: list[tuple[str, str, str]] = []
        for entry in rules:
            if entry in seen:
                continue
            seen.add(entry)
            unique_rules.append(entry)
        if not unique_rules:
            continue
        safe = source_base.replace(".", "_").replace("-", "_")
        lines.append("")
        lines.append(f"    lookup calt_noentry_exit_contract_{safe} {{")
        for noentry_stance, trigger_stance, replacement in sorted(unique_rules):
            lines.append(f"        sub {noentry_stance}' {trigger_stance} by {replacement};")
        lines.append(f"    }} calt_noentry_exit_contract_{safe};")

    def _find_demote_sibling(
        base_name: str,
        prior_stance: str,
        prior_meta,
        successor_stance: str,
        isolated_form: str,
    ) -> str | None:
        # Prefer a sibling whose entry matches the prior's exit and whose `select.after` names this prior. Falling back to the bare isolated form would drop a valid join (e.g. `qsIt.en-y5 qsRoe` should become `qsRoe.en-ext-1-at-0`, not bare `qsRoe`).
        if not prior_meta.exit_ys:
            return None
        prior_exit_ys = set(prior_meta.exit_ys)
        for name in sorted(glyph_names):
            if name in (successor_stance, isolated_form):
                continue
            sibling_meta = glyph_meta.get(name)
            if sibling_meta is None or sibling_meta.base_name != base_name:
                continue
            if not any(y in prior_exit_ys for y in sibling_meta.all_entry_ys):
                continue
            if not sibling_meta.after or prior_stance not in sibling_meta.after:
                continue
            return name
        return None

    successor_demote_by_base: dict[str, list[tuple[str, str, str]]] = {}
    for successor_stance in sorted(glyph_names):
        successor_meta = glyph_meta.get(successor_stance)
        if successor_meta is None or not successor_meta.after:
            continue
        isolated_form = _drop_entry_extension_suffix(successor_stance)
        if isolated_form is None:
            continue
        prior_stances = _expand_all_variants(successor_meta.after, include_base=True)
        for prior_stance in sorted(prior_stances):
            prior_meta = glyph_meta.get(prior_stance)
            if prior_meta is None:
                continue
            if any(entry_y in set(prior_meta.exit_ys) for entry_y in successor_meta.all_entry_ys):
                continue
            target_stance = (
                _find_demote_sibling(
                    successor_meta.base_name,
                    prior_stance,
                    prior_meta,
                    successor_stance,
                    isolated_form,
                )
                or isolated_form
            )
            successor_demote_by_base.setdefault(successor_meta.base_name, []).append(
                (prior_stance, successor_stance, target_stance)
            )

    # An entry-extension stance that also has an exit modifier (e.g. `qsRoe.ex-y0.en-ext-1-at-5`) has its `select.after` cleared, so the loop above skips it. It needs the same demotion: after a prior whose exit is at the wrong Y for its entry, its entry can't join. Copy each rule onto the stance's siblings that add modifiers to it, with the same target. The target has no exit, so the demoted glyph also gives up its follower join.
    for successor_base in sorted(successor_demote_by_base):
        sibling_rules: list[tuple[str, str, str]] = []
        for prior_stance, successor_stance, target_stance in successor_demote_by_base[successor_base]:
            successor_meta = glyph_meta.get(successor_stance)
            if successor_meta is None or successor_meta.extended_entry_suffix is None:
                continue
            for sibling_name in sorted(glyph_names):
                if sibling_name == successor_stance:
                    continue
                sibling_meta = glyph_meta.get(sibling_name)
                if sibling_meta is None or sibling_meta.after:
                    continue
                if sibling_meta.base_name != successor_base:
                    continue
                if sibling_meta.extended_entry_suffix != successor_meta.extended_entry_suffix:
                    continue
                if set(sibling_meta.all_entry_ys) != set(successor_meta.all_entry_ys):
                    continue
                if not (set(successor_meta.modifiers) < set(sibling_meta.modifiers)):
                    continue
                sibling_rules.append((prior_stance, sibling_name, target_stance))
        successor_demote_by_base[successor_base].extend(sibling_rules)

    for successor_base in sorted(successor_demote_by_base):
        successor_rules = successor_demote_by_base[successor_base]
        successor_seen: set[tuple[str, str, str]] = set()
        successor_unique: list[tuple[str, str, str]] = []
        for entry in successor_rules:
            if entry in successor_seen:
                continue
            successor_seen.add(entry)
            successor_unique.append(entry)
        if not successor_unique:
            continue
        safe = successor_base.replace(".", "_").replace("-", "_")
        lines.append("")
        lines.append(f"    lookup calt_successor_demote_{safe} {{")
        for prior_stance, successor_stance, target_stance in sorted(successor_unique):
            lines.append(f"        sub {prior_stance} {successor_stance}' by {target_stance};")
        lines.append(f"    }} calt_successor_demote_{safe};")

    # These hand-written names can lack the en-y0 / ex-y0 modifiers `_synthesize_anchor_modifiers` adds to compiled names, so `heal_glyph_name` rewrites each one to its compiled name.
    _entry_demote_family_names = family_names_from_compiled(glyph_names)
    _entry_demote_available_names = frozenset(glyph_names)
    entry_demote_rules = tuple(
        (
            heal_glyph_name(prior, _entry_demote_family_names, _entry_demote_available_names),
            heal_glyph_name(successor, _entry_demote_family_names, _entry_demote_available_names),
            heal_glyph_name(isolated, _entry_demote_family_names, _entry_demote_available_names),
        )
        for prior, successor, isolated in (
            ("qsOut_qsTea", "qsVie.ex-y0.en-ext-1", "qsVie.ex-y0"),
            ("qsOut_qsTea", "qsVie_qsUtter.en-ext-1", "qsVie_qsUtter"),
        )
    )
    emitted_entry_demote = False
    for prior_stance, successor_stance, isolated_form in entry_demote_rules:
        if (
            prior_stance not in glyph_names
            or successor_stance not in glyph_names
            or isolated_form not in glyph_names
        ):
            continue
        if not emitted_entry_demote:
            lines.append("")
            lines.append("    lookup calt_successor_demote_qsOut_qsTea {")
            emitted_entry_demote = True
        lines.append(f"        sub {prior_stance} {successor_stance}' by {isolated_form};")
    if emitted_entry_demote:
        lines.append("    } calt_successor_demote_qsOut_qsTea;")

    _emit_pred_demote_lookups("calt_final_pred_demote")

    # Glyphs with an entry anchor at Y, the only receivers an `extend_exit_when_entered` exit may attach to. `@entry_y{N}` is too wide: it also holds bare bases and entry-stripped forward replacements that could gain an entry at Y but don't have one in their final stance (e.g. the trailing qsMay in ·Bay·May·May·Ah settles on qsMay.ex-y0, with no entry). Extending toward those would leave the extra ink unjoined.
    def _literal_entry_receivers(target_y: int) -> list[str]:
        return sorted(
            g
            for g in glyph_names
            if any(a[1] == target_y for a in (*_meta(g).entry, *_meta(g).entry_curs_only))
        )

    # `extend_exit_when_entered`: lengthen the exit of a backward-entry-upgrade stance toward the receivers at its exit Y, only after the predecessor that gave it its entry join. The carrier and its entry-extension siblings appear only after that join (word-initial and non-baseline contexts settle on the bare base), so these lookups match them with no backtrack and never reach the bare stance. They come after the successor demotes and `calt_final_pred_demote_*`, so they see whichever entry-side stance the predecessor produced (plain or en-ext-1).
    receivers_by_exit_y: dict[int, str] = {}
    for carrier in sorted(n for n in glyph_names if _meta(n).extend_exit_when_entered):
        carrier_meta = _meta(carrier)
        by = carrier_meta.extend_exit_when_entered
        if by is None:
            continue
        suffix_word = _EXIT_EXTENSION_WORD_BY_COUNT.get(by)
        if suffix_word is None:
            continue
        carrier_mods = set(carrier_meta.modifiers)
        when_entered_rules: list[str] = []
        for variant in sorted(base_to_variants.get(carrier_meta.base_name, ())):
            vm = _meta(variant)
            # Match the carrier and its entry-side siblings (en-ext-1, …) but not the bare base nor any already exit-modified stance.
            if not vm.entry or not vm.exit or vm.is_noentry:
                continue
            if not carrier_mods <= set(vm.modifiers):
                continue
            if any(not extra.startswith("en-") for extra in set(vm.modifiers) - carrier_mods):
                continue
            if vm.extended_exit_suffix or vm.contracted_exit_suffix:
                continue
            combined = f"{variant}.ex-{suffix_word}"
            if combined not in glyph_names:
                continue
            for exit_y in sorted(set(vm.exit_ys)):
                if exit_y not in receivers_by_exit_y:
                    members = _literal_entry_receivers(exit_y)
                    receivers_by_exit_y[exit_y] = " ".join(members) if members else ""
                receivers = receivers_by_exit_y[exit_y]
                if receivers:
                    when_entered_rules.append(f"        sub {variant}' [{receivers}] by {combined};")
        if when_entered_rules:
            safe = carrier.replace(".", "_").replace("-", "_")
            lines.append("")
            lines.append(f"    lookup calt_when_entered_{safe} {{")
            lines.extend(when_entered_rules)
            lines.append(f"    }} calt_when_entered_{safe};")

    # Run the trailing and predecessor demote lookups once more, after every lookup that rewrites a neighbor (including `calt_when_entered_*` and `calt_final_pred_demote_*`). A demote `sub pred' trigger by iso` fires only while the trigger is in the stance it names. When the trigger settles after the predecessor's demote has run (e.g. qsJai's demote runs before qsNo settles, so ·J’ai·No keeps a dangling exit), the earlier run does nothing and this one applies the demote. A predecessor already on its isolated form doesn't match, and each rule names a trigger stance that doesn't join the predecessor, so this run can't break a join.
    _emit_trailing_demote_lookups("calt_final_trailing_demote")
    _emit_pred_demote_lookups("calt_final2_pred_demote")

    lines.append("} calt;")
    lines = _strip_post_zwnj_from_ignore_contexts(lines, base_to_variants)
    lines = _ensure_zwnj_coverage_for_calt_lookups(lines)
    lines = _add_zwnj_guards_for_two_position_forward_rules(lines)
    lines = _coalesce_consecutive_ignore_rules(lines)

    if _active_contract_recorder is not None:
        _active_contract_recorder.flush()
        _active_contract_recorder = None

    return hoist_repeated_classes("\n".join(lines))


def _emit_quikscript_curs(
    join_glyphs: dict[str, JoinGlyph],
    pixel_width: int,
    pixel_height: int,
) -> str | None:
    y_groups: dict[int, list[tuple[str, str, str]]] = {}

    for glyph_name, join_glyph in join_glyphs.items():
        entries = (*join_glyph.entry, *join_glyph.entry_curs_only)
        exits = join_glyph.exit
        if not entries and not exits:
            continue
        y_values = {anchor[1] for anchor in entries} | {anchor[1] for anchor in exits}
        for y in y_values:
            entry_anchor = "<anchor NULL>"
            exit_anchor = "<anchor NULL>"
            for anchor in entries:
                if anchor[1] == y:
                    entry_anchor = f"<anchor {anchor[0] * pixel_width} {anchor[1] * pixel_height}>"
                    break
            for anchor in exits:
                if anchor[1] == y:
                    exit_anchor = f"<anchor {anchor[0] * pixel_width} {anchor[1] * pixel_height}>"
                    break
            y_groups.setdefault(y, []).append((glyph_name, entry_anchor, exit_anchor))

    if not y_groups:
        return None

    for glyph_name, join_glyph in join_glyphs.items():
        if not join_glyph.is_noentry:
            continue
        if join_glyph.exit:
            continue
        original_name = join_glyph.noentry_for or join_glyph.generated_from
        if not original_name:
            continue
        original_glyph = join_glyphs.get(original_name)
        if not original_glyph:
            continue
        for anchor in original_glyph.entry:
            y = anchor[1]
            y_groups.setdefault(y, []).append((glyph_name, "<anchor NULL>", "<anchor NULL>"))

    lines = ["feature curs {"]
    for y in sorted(y_groups):
        lines.append(f"    lookup cursive_y{y} {{")
        for glyph_name, entry_anchor, exit_anchor in sorted(y_groups[y]):
            lines.append(f"        pos cursive {glyph_name} {entry_anchor} {exit_anchor};")
        lines.append(f"    }} cursive_y{y};")
    lines.append("} curs;")
    return "\n".join(lines)


def emit_quikscript_ss(glyph_meta: dict[str, JoinGlyph], *, ss10_reverts_stances: bool = True) -> str | None:
    """Emit the stylistic-set features that run after `calt`: each stance's `revert_feature`, each `replaces_family_feature`, and, when `ss10_reverts_stances` is set, an ss10 that substitutes every non-ligature stance by its bare glyph. Junior uses that ss10. Senior passes False and gets its ss10 from `emit_ss10_isolated_input`."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name, meta in glyph_meta.items():
        if meta.revert_feature:
            groups[meta.revert_feature].append((name, meta.base_name))

    NOJOIN_TAG = "ss10"
    for name, meta in glyph_meta.items():
        if ss10_reverts_stances and name != meta.base_name and len(meta.sequence) <= 1:
            groups[NOJOIN_TAG].append((name, meta.base_name))

    for name, meta in glyph_meta.items():
        if meta.replaces_family_feature:
            base = meta.base_name
            for other_name, other_meta in glyph_meta.items():
                if other_meta.base_name == base and other_name != name:
                    groups[meta.replaces_family_feature].append((other_name, name))

    if not groups:
        return None

    lines = []
    for feature_tag in sorted(groups):
        lines.append(f"feature {feature_tag} {{")
        for variant, base in sorted(groups[feature_tag]):
            lines.append(f"    sub {variant} by {base};")
        lines.append(f"}} {feature_tag};")

    return "\n".join(lines)


def emit_ss10_isolated_input(join_glyphs: dict[str, JoinGlyph]) -> str | None:
    """Emit Senior's ss10: one lookup that substitutes every glyph of a letter that has a twin (`ss10_twins` in tools/quikscript_ir.py) by that anchor-free twin. The caller places it ahead of every other GSUB lookup of the join pipeline, so under ss10 the twins reach the gated stylistic sets and `calt` instead of the letters. No twin is in any of their rules, so no ligature forms, no stance is chosen, and no two glyphs attach."""
    twin_bases = {meta.base_name for meta in ss10_twins(join_glyphs).values()}
    rules = sorted(
        (name, ss10_twin_name(meta.base_name))
        for name, meta in join_glyphs.items()
        if meta.base_name in twin_bases
    )
    if not rules:
        return None
    lines = ["lookup ss10_isolated_input {"]
    lines.extend(f"    sub {name} by {twin};" for name, twin in rules)
    lines.append("} ss10_isolated_input;")
    lines.append("")
    lines.append("feature ss10 {")
    lines.append("    lookup ss10_isolated_input;")
    lines.append("} ss10;")
    return "\n".join(lines)


def _emit_quikscript_ss_gate(analysis: _JoinAnalysis) -> str | None:
    plan = analysis
    if not plan.gated_pair_overrides and not plan.gated_fwd_pair_overrides:
        return None

    glyph_meta = plan.glyph_meta
    glyph_names = plan.glyph_names

    def _candidate_can_support_entry_ys(
        candidate_name: str,
        entry_ys: set[int],
        right_base_name: str,
        feature_tag: str,
    ) -> bool:
        candidate_meta = glyph_meta[candidate_name]
        if set(candidate_meta.exit_ys) & entry_ys:
            return True
        if candidate_meta.exit:
            return False
        return any(
            _can_eventually_exit_at(
                plan,
                candidate_name,
                entry_y,
                before_base=right_base_name,
                feature_tag=feature_tag,
            )
            for entry_y in entry_ys
        )

    def _collect_pending_bk_pair_guards(
        candidate_name: str,
        entry_ys: set[int],
        right_base_name: str,
        feature_tag: str,
    ) -> set[str]:
        candidate_meta = glyph_meta[candidate_name]

        candidate_base = candidate_meta.base_name
        guards: set[str] = set()
        if candidate_meta.exit:
            if candidate_name != candidate_base or not (set(candidate_meta.exit_ys) & entry_ys):
                return set()
            for pending_variant, prev_glyphs in plan.pair_overrides.get(candidate_base, []):
                if _candidate_can_support_entry_ys(
                    pending_variant,
                    entry_ys,
                    right_base_name,
                    feature_tag,
                ):
                    continue
                expanded_prev = set()
                for prev_glyph in prev_glyphs:
                    prev_base = glyph_meta[prev_glyph].base_name if prev_glyph in glyph_meta else prev_glyph
                    expanded_prev.update(plan.base_to_variants.get(prev_base, ()))
                guards.update(expanded_prev)
            return guards

        for prev_exit_y, pending_variant in plan.bk_replacements.get(candidate_base, {}).items():
            if _candidate_can_support_entry_ys(
                pending_variant,
                entry_ys,
                right_base_name,
                feature_tag,
            ):
                continue
            guards.update(plan.exit_classes.get(prev_exit_y, set()))

        for pending_variant, prev_glyphs in plan.pair_overrides.get(candidate_base, []):
            if _candidate_can_support_entry_ys(
                pending_variant,
                entry_ys,
                right_base_name,
                feature_tag,
            ):
                continue
            expanded_prev = set()
            for prev_glyph in prev_glyphs:
                prev_base = glyph_meta[prev_glyph].base_name if prev_glyph in glyph_meta else prev_glyph
                expanded_prev.update(plan.base_to_variants.get(prev_base, ()))
            guards.update(expanded_prev)

        return guards

    bk_features: dict[str, list[tuple[str, str, list[str]]]] = defaultdict(list)
    for base_name, overrides in plan.gated_pair_overrides.items():
        for variant_name, after_glyphs, feature_tag in overrides:
            expanded = _expand_backward_after_variants(
                variant_name,
                after_glyphs,
                expand_selector=lambda glyph: plan.base_to_variants.get(
                    glyph_meta[glyph].base_name if glyph in glyph_meta else glyph,
                    (),
                ),
                analysis=plan,
                feature_tag=feature_tag,
            )
            if not expanded:
                continue

            for terminal in sorted(expanded & plan.terminal_entry_only):
                expanded.discard(terminal)
            if not expanded:
                continue

            bk_features[feature_tag].append((base_name, variant_name, sorted(expanded)))

    fwd_features: dict[str, list[tuple[str, str, list[str], list[str], list[str], set[int] | None]]] = (
        defaultdict(list)
    )
    for base_name, overrides in plan.gated_fwd_pair_overrides.items():
        for variant_name, before_glyphs, not_after_glyphs, feature_tag in overrides:
            expanded_before = _expand_forward_before_variants(
                variant_name,
                before_glyphs,
                analysis=plan,
                feature_tag=feature_tag,
            )
            for terminal in sorted(expanded_before & plan.terminal_exit_only):
                expanded_before.discard(terminal)

            expanded_not_after = set()
            for glyph in not_after_glyphs:
                base = glyph_meta[glyph].base_name if glyph in glyph_meta else glyph
                expanded_not_after.update(plan.base_to_variants.get(base, ()))

            targets = {base_name}
            if base_name in plan.bk_replacements:
                targets.update(plan.bk_replacements[base_name].values())
            if base_name in plan.fwd_replacements:
                targets.update(plan.fwd_replacements[base_name].values())
            if base_name in plan.pair_overrides:
                for pair_variant, _ in plan.pair_overrides[base_name]:
                    targets.add(pair_variant)
            if base_name in plan.fwd_upgrades:
                for entry_exit_var, _, _, _ in plan.fwd_upgrades[base_name]:
                    targets.add(entry_exit_var)
            noentry_name = f"{base_name}.noentry"
            if noentry_name in glyph_names:
                targets.add(noentry_name)

            variant_meta = glyph_meta[variant_name]
            variant_entry_ys = set(variant_meta.entry_ys) if variant_meta.entry else None

            fwd_features[feature_tag].append(
                (
                    base_name,
                    variant_name,
                    sorted(expanded_before),
                    sorted(expanded_not_after),
                    sorted(targets),
                    variant_entry_ys,
                )
            )

    all_tags = sorted(set(bk_features) | set(fwd_features))
    lines = []
    for tag in all_tags:
        lines.append(f"feature {tag} {{")
        for base_name, variant_name, after_list in sorted(
            bk_features.get(tag, []),
            key=lambda item: (item[0],) + _backward_pair_sort_key(glyph_meta, item[1], item[2]),
        ):
            safe = variant_name.replace(".", "_")
            lines.append(f"    lookup {tag}_{safe} {{")
            variant_meta = glyph_meta[variant_name]
            not_before = list(variant_meta.not_before)
            if not_before:
                resolved = resolve_known_glyph_names(not_before, glyph_names)
                nb_expanded = set()
                for nb_glyph in resolved:
                    nb_base = glyph_meta[nb_glyph].base_name
                    nb_expanded.update(plan.base_to_variants.get(nb_base, ()))
                for nb in sorted(nb_expanded):
                    lines.append(f"        ignore sub [{' '.join(after_list)}] {base_name}' {nb};")
            entry_ys = set(variant_meta.entry_ys)
            if entry_ys:
                for candidate_name in after_list:
                    guard_glyphs = _collect_pending_bk_pair_guards(
                        candidate_name,
                        entry_ys,
                        variant_meta.base_name,
                        tag,
                    )
                    if guard_glyphs:
                        guard_list = " ".join(sorted(guard_glyphs))
                        lines.append(f"        ignore sub [{guard_list}] {candidate_name} {base_name}';")
            before_suffix = ""
            if variant_meta.before:
                resolved_before = resolve_known_glyph_names(list(variant_meta.before), glyph_names)
                before_expanded: set[str] = set()
                for before_glyph in resolved_before:
                    before_base = (
                        glyph_meta[before_glyph].base_name if before_glyph in glyph_meta else before_glyph
                    )
                    before_expanded.update(plan.base_to_variants.get(before_base, ()))
                for terminal in sorted(before_expanded & plan.terminal_exit_only):
                    before_expanded.discard(terminal)
                if before_expanded:
                    before_suffix = f" [{' '.join(sorted(before_expanded))}]"
            lines.append(
                f"        sub [{' '.join(after_list)}] {base_name}'{before_suffix} by {variant_name};"
            )
            lines.append(f"    }} {tag}_{safe};")
        for base_name, variant_name, before_list, not_after_list, targets, variant_entry_ys in sorted(
            fwd_features.get(tag, [])
        ):
            safe = variant_name.replace(".", "_")
            lines.append(f"    lookup {tag}_{safe} {{")
            for target in targets:
                target_meta = glyph_meta[target]
                target_has_entry = bool(target_meta.entry)
                if variant_entry_ys is not None and target_has_entry:
                    target_entry_ys = set(target_meta.entry_ys)
                    if not target_entry_ys.issubset(variant_entry_ys):
                        if (target_entry_ys - variant_entry_ys) == target_entry_ys:
                            continue
                actual_variant = variant_name
                suffix = target_meta.extended_entry_suffix
                if suffix:
                    extended = variant_name + suffix
                    if extended not in glyph_names:
                        extended = variant_name + ".en-ext-1"
                    if extended in glyph_names:
                        actual_variant = extended
                actual_variant = _resolve_noentry_replacement(
                    glyph_meta,
                    plan.base_to_variants,
                    target,
                    actual_variant,
                )
                if actual_variant is None:
                    continue
                actual_variant_meta = glyph_meta[actual_variant]
                actual_entry_ys = set(actual_variant_meta.entry_ys) if actual_variant_meta.entry else set()
                entry_backtrack_prefix = ""
                if actual_entry_ys and not target_has_entry:
                    if _entry_anchor_is_visual_addition(glyph_meta, plan.base_to_variants, actual_variant):
                        entry_backtrack_glyphs: set[str] = set()
                        for entry_y in actual_entry_ys:
                            entry_backtrack_glyphs.update(plan.exit_classes.get(entry_y, set()))
                        entry_backtrack_glyphs -= set(not_after_list)
                        if not entry_backtrack_glyphs:
                            continue
                        entry_backtrack_prefix = f"[{' '.join(sorted(entry_backtrack_glyphs))}] "
                if not_after_list:
                    lines.append(
                        f"        ignore sub [{' '.join(not_after_list)}] {target}' [{' '.join(before_list)}];"
                    )
                lines.append(
                    f"        sub {entry_backtrack_prefix}{target}' [{' '.join(before_list)}] by {actual_variant};"
                )
            lines.append(f"    }} {tag}_{safe};")
        lines.append(f"}} {tag};")

    return "\n".join(lines)


def emit_quikscript_senior_features(
    join_glyphs: dict[str, JoinGlyph],
    pixel_width: int,
    pixel_height: int,
    restore_isolated_form_overrides: tuple[tuple[str, str, str, str], ...] = (),
    predecessor_demote_overrides: tuple[tuple[str | None, str, str, str], ...] = (),
    trailing_demote_overrides: tuple[tuple[str, str, str], ...] = (),
) -> str | None:
    parts = []

    curs_fea = _emit_quikscript_curs(join_glyphs, pixel_width, pixel_height)
    if curs_fea:
        parts.append(curs_fea)

    analysis = _analyze_quikscript_joins(join_glyphs)
    analysis.restore_isolated_form_overrides = tuple(restore_isolated_form_overrides)
    analysis.predecessor_demote_overrides = tuple(predecessor_demote_overrides)
    analysis.trailing_demote_overrides = tuple(trailing_demote_overrides)

    ss10_fea = emit_ss10_isolated_input(join_glyphs)
    if ss10_fea:
        parts.append(ss10_fea)

    ss_gate_fea = _emit_quikscript_ss_gate(analysis)
    if ss_gate_fea:
        parts.append(ss_gate_fea)

    calt_fea = _emit_quikscript_calt(analysis)
    if calt_fea:
        parts.append(calt_fea)

    ss_fea = emit_quikscript_ss(join_glyphs, ss10_reverts_stances=False)
    if ss_fea:
        parts.append(ss_fea)

    if not parts:
        return None
    return "\n\n".join(parts)


def emit_namer_dot_calt(
    dot_glyph: str,
    lowered_glyph: str,
    follower_glyphs: list[str] | tuple[str, ...],
    midword_glyphs: list[str] | tuple[str, ...],
) -> str | None:
    """Emit a `calt` lookup that lowers the namer dot (`·`) at the start of a word whose first letter is Short.

    The dot becomes `lowered_glyph` when the next glyph is in `follower_glyphs` and the previous glyph is not in `midword_glyphs` (orthodox letters and digits). The `ignore` rule keeps a mid-word middot, such as Catalan `l·l` or a multiplication dot, at its normal height. OpenType can't match the start of a run directly, so the rule fires at the start of a run and after any other glyph, such as a space, ZWNJ, or punctuation. The caller appends this after the join lookups so the dot is lowered only after the following letter has settled into its final stance.
    """
    followers = sorted(set(follower_glyphs))
    if not followers:
        return None
    lines = [f"@namer_short_followers = [{' '.join(followers)}];"]
    midword = sorted(set(midword_glyphs))
    if midword:
        lines.append(f"@namer_midword = [{' '.join(midword)}];")
    lines.append("feature calt {")
    lines.append("    lookup namer_dot_word_start {")
    if midword:
        lines.append(f"        ignore sub @namer_midword {dot_glyph}' @namer_short_followers;")
    lines.append(f"        sub {dot_glyph}' @namer_short_followers by {lowered_glyph};")
    lines.append("    } namer_dot_word_start;")
    lines.append("} calt;")
    return "\n".join(lines)


__all__ = [
    "emit_namer_dot_calt",
    "emit_quikscript_senior_features",
    "emit_quikscript_ss",
    "emit_ss10_isolated_input",
    "expand_hoisted_classes",
    "hoist_repeated_classes",
]
