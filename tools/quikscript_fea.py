import re
import warnings
from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

from quikscript_ir import (
    _SYNTHESIZED_MODIFIER_TOKENS,
    JoinGlyph,
    family_names_from_compiled,
    heal_glyph_name,
    resolve_known_glyph_names,
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
    # Each entry is (prior_family, target_family, follower_family, isolated_form). When a fwd-pair YAML `not_after` blocks a target's pre-follower upgrade against a member of `prior_family`, but isolated shaping of `target follower` would still render `isolated_form`, a post-pass rule restores the isolated form. See `_record_fwd_pair_not_after_reflip` for the wiring.
    restore_isolated_form_overrides: tuple[tuple[str, str, str, str], ...] = ()
    # Each entry is (predecessor_form, trigger_form, isolated_form). When the rendered chain after every earlier lookup is `predecessor_form trigger_form ...` and `trigger_form` is an entryless variant, the predecessor's extension is reaching into empty air. Emit a final-pass rule `sub predecessor_form' trigger_form by isolated_form;` to demote the predecessor back to its isolated shape so the render matches what `trigger ...` would render on its own to the right of the predecessor's isolated form. No third-glyph guard is needed because the trigger's entryless state at this post-pass already implies the join is broken.
    predecessor_demote_overrides: tuple[tuple[str, str, str], ...] = ()
    # Each entry is (leader_form, trailing_form, isolated_form). When a forward-pair override changes the leader into an entry-preserving form with no exit, any earlier backward upgrade on the trailing glyph can be left with a now-false joining shape. Emit a post-pass rule `sub leader_form trailing_form' by isolated_form;` to restore the trailing glyph to the split-shaping form.
    trailing_demote_overrides: tuple[tuple[str, str, str], ...] = ()


def _backward_pair_sort_key(
    glyph_meta: dict[str, JoinGlyph],
    variant_name: str,
    selector_glyphs: list[str],
) -> tuple[int, int, int, int, str]:
    meta = glyph_meta[variant_name]
    return (
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
            # `fwd_upgrades` holds entry+exit forms whose entry_y collides with an entry-only sibling already in `bk_replacements`; without this branch a `{family: qsX}` selector silently drops these forms (e.g. qsTea.half.en-y8.ex-y5), so derive lookups that should fire after them — like qsRoe.entry_xheight.contract_entry_after — miss the bare form and fall through to a broader competing lookup.
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
        if "ex-noentry" in meta.modifiers:
            # ex-noentry forms exist only as post-liga substitution targets (the calt cleanup pass routes the predecessor of a noentry_after ligature here). They must not enter bk_replacements / fwd_upgrades — the entry-bearing variants of these forms would otherwise displace the regular entry-only forms from pre-liga selection.
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

    # `derive.noentry_after` on a non-ligature family becomes a backward pair override that swaps the base glyph for its `.noentry` variant after the listed families. Ligatures route through the post-liga cleanup pipeline below; this branch keeps plain families one-line.
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
            # When one left-context group holds two complete body lineages — each with its own entry-only and entry+exit form — the upgrade pairing must stay within a lineage so a bare entry-only form isn't lifted into a lead-specific exit body (·Out's bare en-trim vs. its after-·See en-trim, both firing after a contracted ·See). Such self-contained lineages are split out by their `after-…` signature. Lopsided signatures (only an entry-only or only an entry+exit member, as with ·Excite's `noexit` paired against `before-vertical.after-baseline-letter`) must keep cross-matching, so they fall back into one residual group that reproduces the original single-pair behavior.
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

    # Mirror `_emit_noentry_fwd_overrides`: every entry-only backward replacement (e.g. `qsTea.en-y0`) gets a context-gated forward override to the matching exit-only forward replacement of its base (e.g. → `qsTea.ex-y0` when the follower is in `@entry_only_y0`, or → `qsTea.half.ex-y5` when it's in `@entry_y5`). Without recording these paths here, `_can_eventually_exit_at(qsTea.en-y0, 0, before_base=qsDay)` returns False, so qsTea.en-y0 is dropped from the lookbehind class for qsDay.half.en-ext-1. That drop is what makes the in-context shaping of `qsBay qsTea qsDay` flip to (qsTea.half.ex-y5, qsDay.en-ext-1) at y=5 while isolated shaping of `qsTea qsDay` joins at y=0. The same override fires on the `.en-ext-{N}` derivatives of the entry-only variant, so propagate the reachability to them too.
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


@dataclass
class _JoinContractRecorder:
    """Classifier and enforcer for the derived join contract (doc/leak-prevention-plan.md).

    Installed as the module-level `_active_contract_recorder` for the duration of one `_emit_quikscript_calt` run, it observes every neighbor the selection chokepoint (`_select_rule_neighbors`) considers and partitions each `(variant, neighbor, direction)` triple into one of three verdicts:

    - `joining`: the selected variant `V` cursively joins the neighbor `N` — `exit_ys(V) & entry_ys(N)` (forward) or `exit_ys(N) & entry_ys(V)` (backward) is non-empty.
    - `cosmetic`: `V` carries a directional `before-<fam>` (forward) / `after-<fam>` (backward) modifier whose trigger list names `N`'s family, so this cross-break shape change is author-declared and the contract keeps it.
    - `leak`: a non-joining, non-cosmetic selection — the single-rule isolation-leak class the contract drops.

    Phase 2 enforces: `keep` returns the candidate set minus the `leak` verdicts, so the chokepoint drops every non-joining, non-cosmetic neighbor from the rule that would have selected a variant across a break it cannot cursively join. `flush` still dumps the full partition under `tmp/` and warns a one-line summary of what was dropped. The verdicts mirror `tools/leak_contract_report.py`'s `joins` and `_is_cosmetic` exactly, so this in-emitter pass and that standalone snapshot oracle agree on every triple.
    """

    glyph_meta: dict[str, JoinGlyph]
    # The plan's replacement/variant maps, threaded in so the contract can judge a neighbor by the entry/exit Ys it can *reach in context* (its backward/forward upgrade forms, source-gated pair-overrides, and led/trailed ligatures), not just its bare anchors. A bare class-proxy like `qsIt` enters nowhere on its own, but in context it is backward-upgraded to `qsIt.en-y0`, which does — so the contract must credit it with that reachable entry, or it falsely drops the cyclic `qsShe.ex-y0 -> qsIt` join.
    bk_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    fwd_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    base_to_variants: dict[str, set[str]] = field(default_factory=dict)
    verdicts: dict[tuple[str, str, str], str] = field(default_factory=dict)
    pivots: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)
    # Reachability caches, keyed by (neighbor, source_family), plus the led/trailed-ligature index built in __post_init__.
    _entry_cache: dict[tuple[str, str], frozenset[int]] = field(default_factory=dict)
    _exit_cache: dict[tuple[str, str], frozenset[int]] = field(default_factory=dict)
    _leads: dict[str, set[str]] = field(default_factory=dict)
    _trails: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
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
        """The subset of `candidate_members` the contract allows this rule to keep: everything except the `leak` verdicts (joining, cosmetic, and unclassifiable `unknown` neighbors all stay). `observe` must have run for these triples first."""
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
        """Entry Ys the follower `neighbor` can present once shaping settles, judged from the rule whose variant belongs to `source_family`. A concrete shaped form (e.g. `qsFee.ex-y5`) keeps only its own anchors, so genuine entry-stripped forms still read as non-joining; a bare class-proxy (`neighbor == base`) is additionally credited with the entries of its backward-upgrade forms (the exact `entry_classes` membership rule at the top of `_analyze_quikscript_joins`), its source-family-gated entry pair-overrides (the `qsFee.en-y5 after qsLow…` case), and any ligature it leads."""
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
        for lig in self._leads.get(base, ()):
            lm = self.glyph_meta.get(lig)
            if lm is not None:
                ys.update(lm.all_entry_ys)
        result = frozenset(ys)
        self._entry_cache[key] = result
        return result

    def _reachable_exit_ys(self, neighbor: str, source_family: str) -> frozenset[int]:
        """Mirror of `_reachable_entry_ys` for a backward predecessor: the exit Ys `neighbor` can present in context — its own exit anchors, plus (for a bare class-proxy) its forward-upgrade exit forms, its source-family-gated exit pair-overrides, and any ligature it trails."""
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
        # A variant with no exit anchor (a surrendered `ex-noentry`/`noexit` form) has nothing to dangle forward; one with no entry anchor has nothing to dangle backward. The emitter selects such a stripped form *because* the neighbor cannot receive a join — e.g. `qsGay.en-y5.ex-noentry` before the entryless `qsTea_qsOy` ligature in ·Utter·Gay·Tea·Oy. That is the opposite of a leak, so exempt it before the join test; otherwise every exit-less forward rule would drop its entire follower set and never fire, undoing the surrender machinery.
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
        """Dump the full partition to `tmp/` and raise one summary warning for the leak class."""
        counts: dict[str, int] = {"joining": 0, "cosmetic": 0, "leak": 0, "unknown": 0}
        for verdict in self.verdicts.values():
            counts[verdict] = counts.get(verdict, 0) + 1

        lines = [
            "# Leak-contract in-emitter report. Generated by _emit_quikscript_calt; do not hand-edit.",
            "# Each row is a (variant, neighbor, direction) triple the calt selection chokepoint considered,",
            "# classified by the derived join contract (doc/leak-prevention-plan.md). Phase 2 enforces: the",
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

        if counts["leak"]:
            # Function-local import: quikscript_join_analysis imports from quikscript_fea, so a top-of-module import here would cycle.
            from quikscript_join_analysis import NonJoiningNeighborSelectionWarning

            warnings.warn(
                f"Derived join contract: dropped {counts['leak']} single-rule cross-break selections that "
                f"named a non-joining, non-cosmetic neighbor. Full breakdown: {_CONTRACT_EMIT_DUMP_PATH}.",
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
    """Selection chokepoint for the derived join contract (see doc/leak-prevention-plan.md).

    Every contextual `sub … by V` rule in `_emit_quikscript_calt` routes its selection-driving neighbor set through here before the rule string is built: the followers for `direction="fwd"` (the `@entry_y…` class members a forward upgrade fires against), the predecessors for `direction="bk"` (the `@exit_y…` class members a backward upgrade fires against). Returns the subset of `candidate_members` the rule may keep.

    Phase 2 enforces the derived join contract through the installed `_active_contract_recorder`: it classifies each `(variant_name, neighbor, direction)` triple (joining / cosmetic / leak) and returns the candidate set minus the leaks, so the rule can no longer select a variant across a break it does not cursively join (unless the variant's `before-<fam>`/`after-<fam>` modifier marks the interaction as an author-declared cosmetic tuck, which stays). Outside an emit run (e.g. the unit tests that call this directly) no recorder is installed, so this is a pure identity passthrough — it still returns a fresh set so callers can compare `kept == candidate_members` without aliasing their live set.
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
    """Return True when this variant's entry anchor goes with a left tail in the bitmap that no naturally-entryless sibling has.

    Returns False when there exists a naturally-entryless sibling sharing the same bitmap — meaning the entry anchor is purely positional (the bitmap looks identical with or without it). Auto-generated `.noentry` siblings do not count: they share the bitmap by construction, since they exist to strip the cursive anchor without redrawing the glyph.
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
            # Auto-generated noentry stripper — same bitmap as its source, not an independent design.
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
        # Also consider any generated .noentry variants of the same base. calt_zwnj substitutes a qs letter with one of these forms after a ZWNJ, and the standard variant expander does not return them, so backward-context rules would otherwise miss those cases and fail to fire.
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
                        # The "could eventually exit at Y" rescue is only safe when this candidate would actually pick up the right exit later. For `.noentry` strippers that fallback is unreliable: a stripper of an unrelated family member (e.g., bare `qsTea.noentry` of bare `qsTea`) will never reach a half-form ·Tea exit, no matter what siblings the family has. Require the entry-bearing counterpart of the stripper to itself be a candidate — meaning the rule already targets that source — before trusting the rescue.
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
        # calt_zwnj is the rule that creates `.noentry` forms after a ZWNJ; it
        # needs to match across the ZWNJ by design and already lists uni200C in
        # its substitution pattern, so HarfBuzz keeps it in coverage anyway.
        "calt_zwnj",
    }
)


_POST_ZWNJ_NOENTRY_SUFFIX = ".noentry"


def _strip_post_zwnj_token(
    token: str,
    base_to_variants: dict[str, set[str]],
) -> str | None:
    """Strip ``calt_zwnj``-synthesized ``.noentry`` variants from a single FEA context token (backtrack or lookahead position).

    A token is either a single glyph name (``qsX.noentry``), a bracketed class (``[qsA qsB.noentry qsC]``), or a class reference (``@cls``). Class references are left untouched here; the underlying ``@cls`` definitions live elsewhere and are emitted by other paths that already skip ``.noentry`` glyphs.

    Returns the rewritten token, or ``None`` if every member was a ``.noentry`` variant and the position would become empty (in which case the surrounding rule should be dropped).
    """

    def is_post_zwnj(name: str) -> bool:
        if not name.endswith(_POST_ZWNJ_NOENTRY_SUFFIX):
            return False
        base = name[: -len(_POST_ZWNJ_NOENTRY_SUFFIX)]
        variants = base_to_variants.get(base)
        # The post-calt_zwnj variant is always ``<base>.noentry`` where
        # ``<base>`` is the bare family glyph. Some noentry glyphs are emitted
        # as generated siblings rather than stored in the base variant set, so
        # the stripped base is the stable signal here.
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
    """Remove ``calt_zwnj``-synthesized ``.noentry`` variants from the lookahead positions of every ``ignore sub`` rule.

    These ``.noentry`` glyphs can only appear immediately after a ZWNJ. In lookahead, they let an ignore rule for a left-side input match across the ZWNJ after HarfBuzz skips it as a default-ignorable. Backtrack positions stay intact because they also describe valid right-side-internal guards, such as blocking ``ZWNJ ·Ye ·It`` from joining at the baseline.

    The input position (token ending in ``'``) is left alone; rules that operate on a ``.noentry`` glyph itself remain valid.
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
    """Bring uni200C into the coverage of every chained calt lookup so HarfBuzz stops treating ZWNJ as a default-ignorable for that lookup.

    HarfBuzz skips default-ignorable glyphs when matching a lookup's context unless the lookup itself references the glyph. Without this firewall, chained context rules (``sub @bk X' @fwd by Y;`` and the ``ignore sub`` rules around them) silently match across a ZWNJ, which breaks ZWNJ's promise to act as a hard shaping boundary.

    Strategy: for each ``lookup calt_NAME { ... } calt_NAME;`` block that contains a chained rule and does not already mention ``uni200C``, prepend any run-initial ``.noentry`` rules replayed with ``uni200C`` as explicit backtrack and any run-final rules replayed with ``uni200C`` as explicit lookahead, then add ``ignore sub uni200C TARGET';`` rules for marked inputs that have backtrack context. For rules with lookahead context, also add ``ignore sub TARGET' uni200C;`` so the lookup covers ZWNJ on the far side of that input.

    The replay rules preserve normal shaping immediately after ZWNJ. The guard rules then stop the remaining chained contexts from skipping across ZWNJ.

    The ``calt_zwnj`` lookup is exempt because it must match across ZWNJ by design.
    """
    lookup_open_pattern = re.compile(r"^(\s*)lookup\s+(calt_\S+)\s*\{\s*$")
    lookup_close_template = "{indent}}} {name};"

    # The firewall only matters when the font actually wires up calt_zwnj.
    # When it doesn't (e.g. minimal test fixtures), referencing uni200C would
    # fail at FEA compile time because that glyph isn't in the font.
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
        # Find the matching close line.
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
        # HarfBuzz treats ZWNJ as a default-ignorable glyph and would otherwise
        # allow this lookup's chained context rules to match across a ZWNJ.
        # Replay rules at the start or end of a ZWNJ-delimited run first so the
        # guard rules can firewall the lookup without suppressing valid shaping
        # inside that run.
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
    """Block two-position forward chains like `sub TARGET' MID [LIST] by REPLACEMENT;` from matching across an intervening ZWNJ.

    The standard ZWNJ firewall (`_ensure_zwnj_coverage_for_calt_lookups`) only emits single-position `ignore sub TARGET' uni200C;` guards, so a buffer `TARGET MID uni200C LIST_MEMBER` still triggers the two-position rule because HarfBuzz skips the default-ignorable uni200C at the second lookahead position. Walk every calt lookup that contains uni200C in its body, find each two-position forward rule, and inject `ignore sub TARGET' MID uni200C;` immediately ahead of it so the lookup's coverage forces uni200C onto the same footing as MID and the cross-ZWNJ match drops away.

    Only fires when the rule's second lookahead position is a glyph class (`[...]`), since that's the shape `_emit_fwd_pairs` produces for the IR's `before_lig_lead_followups` records. Skips lookups exempt from the firewall (`calt_zwnj`) and lookups whose body does not already mention uni200C (those have nothing to firewall against).
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
            # Lookup doesn't reference uni200C at all, so it has no ZWNJ guarding to extend.
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


def _emit_quikscript_calt(analysis: _JoinAnalysis) -> str | None:
    global _active_contract_recorder
    plan = analysis
    glyph_meta = plan.glyph_meta
    glyph_names = plan.glyph_names
    base_to_variants = plan.base_to_variants

    # Phase-1 join-contract reporting (doc/leak-prevention-plan.md). Installing the recorder makes `_select_rule_neighbors` classify every neighbor it sees; the chokepoint still returns the full candidate set, so this changes no FEA bytes. Flushed (dump + summary warning) just before this function returns.
    _active_contract_recorder = _JoinContractRecorder(
        glyph_meta,
        bk_replacements=plan.bk_replacements,
        fwd_replacements=plan.fwd_replacements,
        base_to_variants=plan.base_to_variants,
    )

    # Function-local import: quikscript_join_analysis imports from quikscript_fea, so a top-of-module import here would cycle.
    from quikscript_join_analysis import (
        DerivedBkGuard,
        JoinReachability,
        _revert_keeps_reaching_exit,
        derive_pending_bk_entry_guards,
        derive_pending_fwd_strip_guards,
        derive_pending_liga_entry_guards,
    )

    _reachability = JoinReachability.from_join_glyphs(glyph_meta)
    _derived_bk_guards = derive_pending_bk_entry_guards(_reachability)
    _derived_fwd_strip_guards = derive_pending_fwd_strip_guards(plan)
    _derived_liga_guards = derive_pending_liga_entry_guards(_reachability)
    # Tracks whether the next emission belongs to a post-calt_cycle lookup. `_emit_narrow_mid_entry_strip_guards` only relaxes its bare-base skip for generic fwd_strip_guards once cycle has finished — pre-cycle predecessors fire before mid's bk_replacement has run, so their mid_source still picks up an entry from `calt_cycle` and no generic guard is warranted. Pair-specific forward strips are checked separately because the same lookup can see the stripping right context in lookahead.
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

    # Accumulator for paired re-flips emitted alongside bk-pair / bk-general guards. When a guard `ignore sub [prior_slot] candidate base';` blocks the bk upgrade from firing on `base`, the candidate sitting at [pos-1] gets its fwd_replacement picked against base's now-plain entry_y instead of the bk-pair variant's entry_y. The accumulated re-flip rule restores the isolated form (the variant the candidate would carry if bk had fired) by substituting the post-suppressed form back to the isolated form, keyed on the same (prior_slot, candidate, base) triple that motivated the guard.
    #
    # Keyed by candidate_base (e.g. ``qsIt``); each entry is a list of ``(prior_slot_frozenset, candidate_pre_form, base_name, isolated_form)``.
    pair_guard_reflip: dict[str, list[tuple[frozenset[str], str, str, str]]] = {}

    def _record_pair_guard_reflip(
        prior_slot: frozenset[str],
        candidate_name: str,
        base_name: str,
        variant_entry_ys: set[int],
    ) -> None:
        # The bk-pair / bk-general rule on ``base_name`` was guarded by ``ignore sub [prior_slot] candidate base';``. In isolation, that rule would have upgraded ``base_name`` to a variant with one of the ``variant_entry_ys`` as its entry_y; ``candidate``'s fwd_replacement would then pick the variant whose exit_y matches that entry_y. The post-suppressed form is what ``candidate`` ends up with after the guard fires — it's the fwd_replacement for ``base_name``'s plain entry_ys (or just the bare candidate if no fwd_replacement applies for that y). Emit a re-flip for each plausible (pre, isolated) pair so the rendered shape matches the isolated render.
        candidate_meta = glyph_meta.get(candidate_name)
        if candidate_meta is None:
            return
        # Only re-flip candidates with no entry anchor. When the candidate has an entry anchor, its isolated form depends on the predecessor of the isolated left half (which may push it through a bk_replacement that diverges from what fwd_replacements would produce here), so the isolated form we'd derive from the right half alone is unsafe to apply.
        if candidate_meta.entry:
            return
        candidate_base = candidate_meta.base_name
        base_meta = glyph_meta.get(base_name)
        if base_meta is None:
            return
        candidate_fwd = fwd_replacements.get(candidate_base, {})
        if not candidate_fwd:
            return
        # Isolated forms: fwd_replacement variants matching variant.entry_y.
        isolated_forms: set[str] = set()
        for variant_entry_y in variant_entry_ys:
            isolated_form = candidate_fwd.get(variant_entry_y)
            if isolated_form is not None and isolated_form != candidate_name:
                isolated_forms.add(isolated_form)
        if not isolated_forms:
            return
        # When the bk-pair guard suppresses base's upgrade, base stays plain — its entry sits at one of base_meta's plain (non-variant) entry_ys. Drop any `isolated_form` whose exit_y can't meet that plain entry: applying it would visibly disconnect the candidate from base on the right side, which is worse than leaving the pre_form in place. `isolated_forms` with no exit are fine to keep (their right side is dangling either way, so they at least match the isolated bitmap).
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
        # Pre forms: what the candidate is at this point. The candidate is already mutated by fwd_replacements based on base's plain entry_y, or it could still be bare (if no fwd_replacement applies for base's plain entry_y, e.g. when there's an existing not_before guard on the candidate's fwd lookup blocking it). Try both:
        # - bare candidate_name itself
        # - candidate_fwd[base_entry_y] for each base entry_y not in variant_entry_ys (the "plain" entry_ys that survive when bk-pair is suppressed).
        pre_forms: set[str] = {candidate_name}
        for base_entry_y in base_meta.entry_ys:
            if base_entry_y in variant_entry_ys:
                continue
            pre_form = candidate_fwd.get(base_entry_y)
            if pre_form is not None:
                pre_forms.add(pre_form)
        for isolated_form in isolated_forms:
            for pre_form in pre_forms:
                if pre_form == isolated_form:
                    continue
                pair_guard_reflip.setdefault(candidate_base, []).append(
                    (prior_slot, pre_form, base_name, isolated_form)
                )

    def _record_fwd_pair_not_after_reflip(
        prior_slot: frozenset[str],
        target_name: str,
        follower_glyphs,
        isolated_form: str,
    ) -> None:
        # When a fwd-pair lookup emits ``ignore sub [prior_slot] target' [follower];`` via YAML `not_after`, the target stays as ``target_name`` (the bare base) instead of being upgraded to the variant the lookup would otherwise pick. Isolated shaping of ``target follower`` selects ``isolated_form`` on its own — record a re-flip so the in-context render matches the isolated render. The shape mirrors `_record_pair_guard_reflip`'s tuple: ``(prior_slot, pre_form, base_name, isolated_form)`` where the emitted rule is ``sub [prior_slot] pre_form' base_name by isolated_form;``. Here ``base_name`` is the follower glyph (the lookahead position), and ``pre_form`` is ``target_name`` (the bare target left behind by the ignore rule).
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

    def _matching_pending_liga_guards(
        source_name: str,
        lig_name: str,
        entry_y: int,
        left_guard_name: str,
    ) -> tuple[DerivedBkGuard, ...]:
        return tuple(
            guard
            for guard in _derived_liga_guards.get((source_name, lig_name, entry_y), ())
            if left_guard_name in guard.guard_glyphs
        )

    def _source_strips_own_exit_before_mid(
        source_name: str,
        replacement_name: str,
        mid_source: str,
    ) -> bool:
        # The guard that calls this blocks `source_name → replacement_name` because `replacement_name`'s exit orphans once `mid_source` forward-strips its own entry (e.g. ·Thaw dropping its baseline entry before ·-ing, or the ·Tea·Oy ligature having no entry). But if `source_name`'s base owns an entry-preserving no-exit fwd-pair override that keeps `replacement_name`'s entry while dropping its exit, and that override's lookahead admits the forward-stripped `mid_source` form, the orphaned exit is removed by the override rather than left dangling. The promotion is then safe to keep — blocking it would needlessly revert `source_name` to bare and lose the left-side join. ·Utter·Gay·Thaw·-ing and ·Utter·Gay·Tea·Oy are the worked cases: ·Gay keeps the ·Utter entry through `qsGay.en-y5.ex-noentry` / `qsGay.en-y0.ex-noentry` instead of collapsing.
        replacement_entry_ys = set(_meta(replacement_name).entry_ys)
        if not replacement_entry_ys:
            return False
        mid_base = _base_name(mid_source)
        # The forms `mid_source` can forward-strip to that drop its entry: generic forward replacements, pair-override targets (e.g. ·Thaw → `qsThaw.ex-y0` before ·-ing), and `mid_source` itself when it is already an entryless variant.
        stripped_mid_forms = {
            fwd_var for fwd_var in fwd_replacements.get(mid_base, {}).values() if not _meta(fwd_var).entry
        }
        stripped_mid_forms.update(
            override_var
            for override_var, _, _ in fwd_pair_overrides.get(mid_base, [])
            if not _meta(override_var).entry
        )
        if mid_source != mid_base and not _meta(mid_source).entry:
            stripped_mid_forms.add(mid_source)
        # An entryless ligature led by `mid_source` (e.g. qsTea before qsOy collapsing to the entryless `qsTea_qsOy`) also voids `replacement_name`'s forward exit once `calt_liga` fires. The lead component appears unligated pre-liga, so the forward-strip guard would otherwise suppress `source_name`'s promotion even though the source's entry-preserving `.ex-noentry` override (whose `before` lists the ligature) cleans the orphaned exit post-liga. Add those ligatures so the guard recognizes the same opt-out it already grants for `noentry_after` ligatures.
        for lig_name, components in ligatures_by_first_component.get(mid_base, ()):
            if components and components[0] == mid_base and not _meta(lig_name).entry:
                stripped_mid_forms.add(lig_name)
        if not stripped_mid_forms:
            return False
        for override_variant, override_before, _ in fwd_pair_overrides.get(_base_name(source_name), []):
            override_meta = _meta(override_variant)
            if override_meta.exit:
                continue
            if set(override_meta.entry_ys) != replacement_entry_ys:
                continue
            admitted = _expand_all_variants(override_before, include_base=True)
            if stripped_mid_forms & admitted:
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
                    # Drop followers whose bk-upgrade at mid_exit_y is blocked by `mid_base`. They can't reach entry at mid_exit_y, so the strip never fires and the guard isn't needed for them. Mirrors the partial-ignore in `_emit_fwd_general` / `_emit_noentry_fwd_overrides` / `_emit_narrow_mid_entry_strip_guards`.
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

            for lig_name, components in ligatures_by_first_component.get(mid_base, ()):
                if len(components) != 2:
                    continue
                if exit_y in set(_meta(lig_name).all_entry_ys):
                    continue
                guards = _matching_pending_liga_guards(
                    mid_source,
                    lig_name,
                    exit_y,
                    replacement_name,
                )
                if not guards:
                    continue
                trigger_contexts = _ligature_component_variants(lig_name, components[1], 1)
                for guard in guards:
                    allowed_before = set(trigger_contexts)
                    if guard.before_bases:
                        allowed_before &= _expand_all_variants(guard.before_bases, include_base=True)
                    _emit_guard(mid_source, allowed_before)

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
        """Emit ``ignore sub`` rules that block ``source_name → replacement`` when the next glyph has an entry that triggers this substitution but later strips that same entry through its own forward substitution.

        Covers both general forward substitutions (``fwd_replacements``) and pair-specific ones (``fwd_pair_overrides``). Without these guards, the left glyph's exit ends up orphaned against the stripped mid glyph.
        """
        if replacement_name not in glyph_meta or exit_y not in _meta(replacement_name).exit_ys:
            return
        # When demoting `replacement_name` back to bare `source_name` would leave the very same reaching exit stroke (a deep half/entry form that shares its full base's lower body, like qsDay.half), the guard removes nothing on the right yet drops a left-side join the bare form can't make. Skip it and let the joining form stand. See `_revert_keeps_reaching_exit`'s docstring for the worked ·It·Day case.
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
            # Followers (the third position of `source' mid follower`) for which the runtime
            # routes mid through an entry-preserving rule instead of the entry-stripping
            # `fwd_replacement`. Two paths apply:
            #   * Bk-upgrade then chained pair-override: when the predecessor exits at a Y
            #     where mid has a bk_replacement with entry, mid is bk-upgraded first.
            #     Any chained pair-override on the bk-upgraded form sorts before the
            #     entry-stripping pair-override (more modifiers ⇒ earlier in calt), so it
            #     fires for any follower listed in its `before`.
            #   * Direct entry-backtrack: an entry-bearing pair-override of bare mid_base
            #     fires when the predecessor sits in the backtrack class (anything exiting
            #     at the pair's entry_y, minus its not_after). Its `before` list specifies
            #     which followers it covers.
            # The strip-guard ignore should drop these followers from its trigger class —
            # for them, mid keeps its entry and the predecessor's promotion is safe.
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
            # Bare bases never satisfy `has_entry_at_y` (their own entry list is empty). When the structural pass identifies a bare base whose generic forward upgrade strips entries at this exit_y AND the predecessor's substitution fires after `calt_cycle` (so mid has already been forward-stripped), fall through to the `fwd_replacements` branch. Pair-specific strips may also need a bare-base guard before cycle because this lookup can see the stripping right context directly.
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
                    # Followers whose bk-upgrade at mid_exit_y has a `not_after` blocking `mid_base` can't actually reach entry at mid_exit_y when mid precedes — so the entry-strip never fires and the leader doesn't need protection from them. Mirrors the partial-ignore in `_emit_fwd_general` / `_emit_noentry_fwd_overrides`.
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
                        # The FEA emitter expands `entry_classes` to include bare bases of entry-bearing variants and their entry-stripped forward replacements (so post-cycle rules see the runtime-promoted entry). For the fwd-strip guard, that broader set is wrong: when the third position is itself a bare base whose `bk_replacements[mid_exit_y]` carries the entry, the runtime picks bk over fwd at that slot, so mid never actually forward-strips. Restricting to glyphs that literally carry an entry at mid_exit_y keeps `·Gay·Tea·Tea` joined while preserving the ·Gay·Tea·Ah guard (qsAh has a real entry at y=0).
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
                # Drop followers whose bk-upgrade at exit_y is blocked by `mid_base`. They can't reach entry at exit_y, so the strip never fires and the guard isn't needed for them.
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
        # For each `extend_exit_before` rule on `fwd_var` whose targets intersect `right_context_glyphs`, yield (extended_fwd_var, trigger_glyphs) so the caller can emit a more specific rule per rule. Returns an empty list when no refinements apply.
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
        # Emit the post-bk revert substitution(s) for fpt → some replacement. When `_refined_bk_replacement` returns a different glyph and that glyph carries `not_after` restrictions that intersect `member_set`, split into two rules so the restricted predecessors fall back to the default replacement and the rest get the refined one.
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
        # When `fpt` (a fwd-pair-override variant) is being reverted to the bk_replacement `default_replacement` because its entry doesn't fit the preceding glyph, prefer a replacement that still carries fpt's exit-extension. First try `default_replacement + ext_suffix` (e.g. ``qsX.en-y0.ex-ext-1`` when the family declares such a variant); failing that, fall back to an entryless sibling of fpt whose bitmap matches (e.g. ``qsTea.ex-y0.ex-ext-1`` for ``qsTea.en-y8.ex-y0.ex-ext-1``). The sibling drops the wrong entry — same bitmap, no cursive attachment to the preceding glyph either way — but preserves the join extension into the next glyph.
        #
        # Preferred path: when ``default_replacement + ext_suffix`` exists and its exit_y matches the isolated-shaping path's choice for fpt's followers (each follower in ``fpt_meta.before`` proposes an entry_y; isolated shaping of the base before that follower picks ``fwd_replacements[base][entry_y]`` whose exit_y is the isolated exit_y), return the candidate even if its bitmap doesn't match fpt's. That keeps the in-context render aligned with the isolated render (``·Ah ·It ·Zoo`` now matches ``·It ·Zoo`` on the qsIt side: qsIt.en-y5.ex-ext-1 instead of the entryless qsIt.ex-y5.ex-ext-1 sibling, which loses the lead's baseline reach).
        #
        # Stranded-extension guard: if any follower-family in fpt's before list has no variant whose entry matches the candidate's exit_y, the extension would be wasted ink for that follower (·May·It·Owe is the worked example — qsOwe never accepts a baseline entry, so the swap that turns qsIt.en-y0.ex-ext-1 into qsIt.en-y5.ex-ext-1 would leave the trailing pixel dangling). In that case fall through to the suffixless ``default_replacement`` rather than preserving an extension that no follower can land.
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
                # The candidate-with-suffix points at an exit Y that at least one follower-family can't receive. Preserving the suffix would leave that follower with stranded extension ink; drop straight to the suffixless default. Don't fall through to the entryless-sibling fallback either: that fallback exists for cases where the candidate glyph simply doesn't exist, not for cases where the partner's reception is the problem (preserving the right-side extension via an entryless sibling just relocates the stranded ink to the predecessor's side, since whatever pushed the predecessor toward `.ex-ext-1` expected fpt to receive at its old entry Y).
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
        # Return True only when every follower-family in fpt's before list has at least one variant whose entry sits at one of ``cand_exit_ys``. Used to gate preserving an exit-extension across a backward-context swap: if even one follower-family can't land the extension, the candidate would emit stranded ink (the trailing pixel reaches toward a follower that never accepts at that Y), so the caller should fall back to the suffixless replacement.
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
        # Isolated shaping of ``base + follower`` picks ``fwd_replacements[base][N]`` whose exit_y matches one of follower's entry_ys (N). Return the union of those isolated exit_ys across fpt's resolved followers, restricted to the exit_ys that ``fwd_replacements`` actually declares for the base.
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
        # The fpt's ``before`` list on its meta is the unresolved selector; the resolved form lives in ``fwd_pair_overrides[base]``. Look up by fpt name.
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

                # Walk extension/contraction/trim derivations on the side orthogonal to the variant's own modification, so pair overrides also fire on inputs already pre-modified by an earlier same-direction lookup (e.g. a ·Tea form that picked up `en-ext-1` after `qsKey` should still receive the exit-side contraction before `qsZoo`).
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
                # calt_zwnj substitutes a qs letter with its `.noentry` form after a ZWNJ, and the standard variant expander does not return those forms. Without this augmentation a forward-pair `not_after` guard would miss `qsX.noentry` and the rule would still fire, joining a word-initial qsX to its successor at the forbidden anchor.
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
                                # Followers the forward override itself can attach to via the variant's own exit Y. If a follower can reach both the bk path (via `bk_var`'s exit Y) and the fwd path (via `variant`'s exit Y), the author's choice to define an explicit forward-pair override for those followers should win — narrowing `compatible` here prevents the partial-ignore from blocking the override for followers that have a viable fwd-side attachment.
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
                                            # Drop followers that the forward override itself can serve; those are not "bk-only" followers, so the bk path doesn't need protection from the override for them. If every bk-reachable follower is also fwd-reachable, the override wins for all of them and no partial-ignore is emitted.
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
                                        # The bk-upgrade at this Y already refuses to fire for predecessors listed in the bk_replacement's `not_after`. Those predecessors therefore don't need protection from this entryless override: nothing downstream was going to attach the bk_replacement's entry for them.
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
                        # An `.ex-noentry` conversion exists precisely to surrender `target`'s exit before a follower that can't receive it. When that follower is itself entryless (e.g. the `qsTea_qsOy` ligature), the conversion is the intended outcome, so don't let the terminal-exit-only guard block it. Followers that still carry an entry stay guarded: there the exit could attach, so `target` should keep its joining form.
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
                    # Split the positive substitution into a single-lookahead rule for the bulk of `effective_before` plus one two-lookahead rule per ligature-lead-only entry that `expand_selectors_for_ligatures` introduced. Each lead glyph must be followed by an actual trailing-component variant for the calt rule to fire meaningfully; without that constraint the rule over-fires whenever the lead appears alone (e.g., qsIt' qsDay alone, when qsDay isn't about to become qsDay_qsUtter). The IR records the trailing families on `before_lig_lead_followups`; the FEA emitter expands them into glyph-set lookaheads here.
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
            # When a follower base F has a backward upgrade at entry_y=exit_y whose `not_after` excludes base_name (the leader), F's entry won't be activated when base_name precedes. Suppress the forward upgrade in that case: otherwise base_name reaches its exit at this Y toward an entry that never materializes, and the in-context render diverges from the isolated split.
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
                # Apply the YAML `not_before` exclusions on the `.noentry` upgrade too: without these, calt_zwnj's swap to the `.noentry` form would smuggle the base form past its own `not_before` guard (e.g., qsIt.noentry → qsIt.ex-y5 before qsDay even though the YAML says not_before qsDay). Intersect the excluded set with this rule's right-side class so siblings whose entry is at a different Y (e.g., qsZoo.half at y=0 when this rule is the @entry_y5 upgrade) can still be picked up by a later upgrade in the same lookup.
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
        # A pair-override form only displaces the bare candidate when its own `before` lookahead admits the follower actually sitting to its right. An override whose `before` is restricted to a follower set that excludes `right_base_name` can never fire in this context, so it cannot block the candidate from handing off to that follower and must not contribute a guard. An empty `before` is unconstrained and always applies.
        before = _meta(pending_variant).before
        if not before:
            return True
        return any(_base_name(glyph) == right_base_name for glyph in before)

    def _collect_pending_bk_pair_guards(
        candidate_name: str,
        entry_ys: set[int],
        right_base_name: str,
    ) -> set[str]:
        candidate_meta = _meta(candidate_name)
        # .noentry variants only appear after calt_zwnj substitutes the base form following a ZWNJ. They should not inherit the backward-guard rules that would otherwise block the pair substitution, because the ZWNJ is what drives the .noentry form in the first place.
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
        # Mirrors the `targets` set built inside `_emit_fwd_pairs`: the glyph forms that the forward-pair rule's source slot accepts. These are the variants that could legitimately sit at [pos-1] when the bk-pair lookup fires, and that the later forward-pair rule will consume.
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
        # Block when a chain of later forward-pair + bk_general rules would mutate the predecessor candidate's exit_y away from the follower's entry_ys; the bk_replacement / bk-pair rule would otherwise fire on a now-stale after-match. This is the two-glyph lookbehind variant of `_collect_pending_bk_pair_guards` — that helper only handles candidates with no exit, and short-circuits on entry-bearing ones.
        if not entry_ys:
            return
        # Materialize the member iterable so we can both iterate it and use it as a membership filter for the fwd_replacements guard loop.
        members = frozenset(member_iter)
        # Group candidates that share a prior slot so we emit one ignore rule per prior (with the candidate set as a class) instead of N near-duplicate lines.
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
        # When a candidate sits at [pos] in a bk-pair rule but the candidate's own bk_replacement (firing later) would mutate it to a form that no longer supports the follower's entry_ys, the bk-pair rule is firing too early. Block it whenever a glyph at [pos-1] has a forward-pair rule whose target exit_y matches the bk_replacement's prev_exit_y *and* whose lookahead lists this candidate — because that chain will invalidate the after-match.
        candidate_meta = _meta(candidate_name)
        if candidate_meta.is_noentry:
            return []
        candidate_base = candidate_meta.base_name
        bk_for_base = bk_replacements.get(candidate_base, {})
        if not bk_for_base:
            return []

        # The bk_general rule keys on prev_exit_y; we only need to guard against the prev_exit_ys whose pending_variant fails the entry-ys support check (i.e., the chain would invalidate the after-match).
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
                # Include the fwd-pair target itself; by the time this bk-pair rule fires, the earlier fwd-pair lookup has already mutated the predecessor to ``fwd_variant``, so that form can still sit at [pos-1] in the buffer.
                slot.add(fwd_variant)
                if slot:
                    guards.append((frozenset(slot), candidate_name))
        # Mirror the loop above for `fwd_replacements`: a fwd_replacement on the prior glyph also mutates [pos-1]'s exit_y when the candidate at [pos] sits in the matching @entry_y{exit_y} class, and that mutation can feed the same invalidating chain through `bk_replacements[candidate_base]`. Skip same-family chains (prior_base == candidate_base) — those describe a predecessor mutating itself based on the candidate's right context, not a true two-glyph lookbehind, and the bk-pair upgrade on the follower-of-the-candidate is unaffected by that mutation in practice (e.g. `·It·It·No`'s qsNo.alt.after-it-and-vie upgrade is correct regardless of how the leading qsIt mutates). Also require `prior_base` to be one of the predecessors the bk-pair rule actually accepts, so a fwd_replacement on an unrelated lead doesn't fire a guard for this bk-pair.
        for prior_base, by_exit_y in fwd_replacements.items():
            if prior_base == candidate_base:
                continue
            source_slot = _fwd_pair_source_slot(prior_base)
            if source_slot.isdisjoint(expanded_after):
                continue
            for replacement_exit_y, replacement_variant in by_exit_y.items():
                if replacement_exit_y not in invalidating_prev_exit_ys:
                    continue
                # The fwd_replacement only fires when the candidate at [pos] is a member of the matching @entry_y{exit_y} class; if it isn't, the chain that would invalidate the bk-pair after-match never starts.
                candidate_entry_class = entry_classes.get(replacement_exit_y, set())
                if (
                    candidate_name not in candidate_entry_class
                    and candidate_base not in candidate_entry_class
                ):
                    continue
                slot = set(source_slot)
                # By the time the bk-pair rule fires, the fwd_replacement lookup has already mutated the predecessor to ``replacement_variant``, so that form must also be in the ignore-rule's prior class.
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
                    # Mirror the partial-ignore in `_emit_fwd_general`: when a follower base F has a backward upgrade at entry_y=exit_y whose `not_after` excludes base_name, F's entry never materializes when base_name precedes. Suppress the forward upgrade in calt_cycle for those follower variants too, otherwise base_name reaches its exit at this Y toward an entry that won't be added.
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
                # Mirror the YAML `not_before` (and IR-derived `bk_fwd_exclusion_sequences`) of `relevant[entry_y]` here too. Without it, an earlier `calt_fwd_{base}` lookup rewrites bare `base` to a `fwd_variant` (entryless), and this post-override pass then bk-upgrades that fwd_variant to `relevant[entry_y]` (which carries the entry anchor) regardless of follower — silently undoing `not_before`. The sub gets a broad lookahead (the union of every `@entry_y*` class — anything that could appear here as an entry-bearing follower) so the ignore rules for `not_before` and the sub end up in the same compiled subtable, while the upgrade still fires for followers (like bare qsIt at entry_y=0) whose entry sits at a Y different from the target's exit Y.
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
                    # This rule pairs a backward predecessor (`@exit_y{entry_y}`) with a forward lookahead (`sub_la`). The join contract deliberately does NOT prune `sub_la` here: it is a backward (entry-adding) upgrade whose forward lookahead is the deliberately-broad class union (see the comment above on `sub_la`'s construction), used only as a "there exists an entry-bearing follower" gate so the entryless `fwd_variant` is not upgraded when standing alone — and to keep the `not_before` ignore rules and this sub in the same compiled subtable. Narrowing it to followers that join `relevant[entry_y]`'s exit would break that gate (the upgrade is about the predecessor join on the entry side, not the follower's exit join), so the per-rule contract leaves this rule intact; any residual exit dangle here is a downstream concern for the second-order pass (Phase 4), not this one.
                    lines.append(
                        f"        sub @exit_y{entry_y} {fwd_variant}'{sub_la} by {relevant[entry_y]};"
                    )
            lines.append(f"    }} calt_{safe};")

    def _emit_reverse_upgrades():
        def _forward_exit_derivatives(root: str) -> list[str]:
            # Transitive exit-side derivatives (extend/contract) of `root`, skipping
            # entry-side and noentry forms. These are the forms a follower can force
            # onto `root` before the left context resolves.
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
                    # The base rule above re-applies the after-context shape only to the bare
                    # `exit_only_var`. But when the after-context (e.g. ·See) resolves later than the
                    # follower, the follower has already forced a forward exit-treatment onto the
                    # exit-only form, so the buffer holds e.g. `qsOut.en-y0.ex-y5.ex-con-2`, not the bare
                    # `qsOut.en-y0.ex-y5`, and the base rule misses it. Re-apply the shape to those
                    # forward-treated forms too, pairing each to the after-context variant that lands the
                    # follower at the same exit anchor — the shifted after-context body needs one more
                    # contraction / one less extension to reach the same X. Without this, ·See·Out·{J'ai,Fee}
                    # keep the un-shifted body and collide with the preceding letter.
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

        for variant_name, source_variants, entry_ys, after_glyphs, not_before in plan.reverse_only_upgrades:
            valid_entry_ys = [y for y in sorted(set(entry_ys)) if y in exit_classes]
            if not valid_entry_ys:
                continue
            safe = variant_name.replace(".", "_")
            lines.append("")
            lines.append(f"    lookup calt_reverse_upgrade_explicit_{safe} {{")
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
                    # Mirror the partial-ignore in `_emit_fwd_general`: when a follower base F has a backward upgrade at entry_y=fwd_exit_y whose `not_after` excludes base_name, F won't end up with an entry at fwd_exit_y after base_name. Block the entry-only → exit-only override for those F variants too.
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
        """Variants that can appear after the first backward-pair pass.

        Seeds with outputs of every channel that can mutate a glyph during the forward calt passes — generic entry/exit substitutions plus pair-specific overrides (forward and backward, gated and ungated). Pair-specific outputs are kept here so post-context bk-pair re-emission can match consumers whose backtrack names a fwd-pair-override result (e.g. ``qsFee.en-y5`` after ``qsMay.ex-ext-1``). The post-context emitter still applies its own guards (``not_before``, entry-strip, terminal-entry-only ignores), so a wider seed cannot revive a join that the original ``calt_pair_*`` rule blocks.
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
        # When a pair_override variant on family X has `entry: null` (e.g. qsSee.after-ye), bare X is still in @exit_y0 at the time the cycle's BK substitution fires for a follower Y (qsZoo.half etc.), so Y is upgraded based on X's transient exit. After the late `calt_post_context_pair_*` lookup turns X into the null-entry variant, that variant has neither entry nor exit anchors — its presence can't justify Y's upgrade. Revert Y back to its base.
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
            # Lookups that emit AFTER calt_cycle within this block run at runtime after calt_cycle, so mid glyphs have already gone through their forward-stripping substitutions. Enable the generic fwd-strip guards so the post-cycle pair lookups in this same block (calt_upgrades, calt_pair_fwd_overrides, late _emit_fwd_pairs) suppress predecessor promotions whose extension would land on a stripped mid.
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

        # Match the candidate's entry side so a baseline-joining input (e.g. qsMay.en-y0) routes to a baseline-joining replacement (qsMay.en-y0.ex-noentry), not the entryless fallback. The replacement's modifier set should match the input's minus any ex-* modifiers (those describe the exit shape we're discarding) plus 'ex-noentry'.
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
        # True when ``candidate``'s family owns an entry-preserving ``.ex-noentry`` sibling that keeps ``candidate``'s entry, drops the exit, and lists ``lig_target`` in its `before`. The entryless ligature voids ``candidate``'s forward exit, but a later forward-pair pass (`calt_*fwd_pair_<sibling>`) substitutes the sibling once the ligature is the immediate follower — so the post-liga left cleanup should leave ``candidate`` alone (demoting it to its entryless bare form would strip the entry the sibling is meant to keep). Mirrors `has_entry_preserving_exit_noentry_sibling`, but scoped to a sibling whose `before` admits this specific ligature. `qsGay.en-y5.ex-noentry` before `qsTea_qsOy` is the worked case.
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
        # When `_collect_noentry_after_left_cleanup_rules` demotes a predecessor (e.g. qsMay.en-y0 -> qsMay.ex-noentry), any glyph whose `before:` clause selected its variant on the demoted family is now extending toward an entryless follower. Revert those pre-predecessor variants to their bare base in the context of the demoted replacement so the would-be join stroke doesn't dangle. Runs as its own lookup after the demotion pass so the lookahead can match the freshly substituted replacement. Skip when the replacement still carries an entry: the pre-predecessor's exit can still attach there, so the `before:`-driven shape selection remains correct.
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

        # Ligation lives inside `calt`, not the dedicated `liga` feature, so it runs after `calt_cycle`'s contextual form selection within the same feature pass. That ordering lets a forward `calt` rule change a component's glyph identity (e.g., qsUtter -> qsUtter.alt in ·Day·Utter·Low) before the ligature lookup sees it, which in turn blocks the `qsDay qsUtter` ligature from firing because the matched sequence is now `qsDay qsUtter.alt`. Putting these rules in `liga` would force ligation to run as its own feature pass and lose that interleaving.
        # Constructed variant names (`lig_name + ".half"`, `actual_lig + ".ex-ext-1"`, etc.) miss the post-synthesis forms whose anchor-Y labels were filled in by `_synthesize_anchor_modifiers` — e.g. `qsDay_qsUtter.half` is really `qsDay_qsUtter.half.en-y0.ex-y5`. `heal_glyph_name` rewrites the literal name to its post-synthesis counterpart before we test membership in `glyph_names`.
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
                        # Only inherit the entry-extension onto the ligature when the source's entry Y matches the target's. The `.en-ext-1` suffix means different things on forms with different entry geometries (e.g., `qsDay.half.en-ext-1` extends entry at y=0 while `qsDay_qsEat.en-ext-1` extends entry at y=5) — copying it across mismatched Ys produces a ligature variant whose extension is geometrically unrelated to the predecessor join that triggered it.
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
                    # When the after-context form belongs to a ligature, the ligature glyph only exists *after* liga, so its pre-liga `calt_post_context_pair` can never match — a non-ligature predecessor (e.g. ·See's baseline exit before ·See·Out·Tea) has no earlier chance to trigger the upgrade. Keep the full predecessor class so it fires here, post-collapse.
                    ligature_after = sorted(expanded_after)
                else:
                    # Narrow the post-liga trigger class to ligature-derived glyphs only. The whole point of this lookup is to re-fire form selection when a ligature glyph is the new immediate predecessor; including the variant's non-ligature after entries here would over-fire on plain pre-liga sequences whose predecessor mutated during `calt_cycle` (e.g., `qsUtter qsThey qsJay` where forward extension turns qsUtter into qsUtter.ex-ext-1 *after* qsThey's backward lookup already declined to fire). Ligature-only ensures the rule truly only triggers post-collapse.
                    ligature_after = sorted(
                        glyph
                        for glyph in expanded_after
                        if glyph in glyph_meta and glyph_meta[glyph].base_name in lig_glyph_names
                    )
                if ligature_after:
                    post_liga_rules.append((base_name, variant_name, ligature_after))

        for lig_name in sorted(lig_glyph_names):
            noentry_after = _meta(lig_name).noentry_after
            if not noentry_after:
                continue
            noentry_name = lig_name + ".noentry"
            if noentry_name not in glyph_names:
                continue
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

        # Each variant gets its own FEA lookup. In OpenType GSUB type-6 lookups an `ignore` rule that matches at a position blocks every later subtable in the same lookup, so sharing one lookup across variants is unsafe whenever any variant carries a `not_before` — one variant's ignore can swallow another variant's substitution.
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

    # Register reflip entries for every `restore_isolated_form_overrides` entry whose intermediate heuristic in `_record_pair_guard_reflip` (the "isolated_form's exit_y must reach the follower's plain entry_y" check) would otherwise reject the registration. The qsEat/qsJay/qsYe/qsIt before qsIt before qsNo cases hit this: the heuristic sees qsNo's plain entry at the x-height and refuses to commit qsIt.ex-y0, but the post-reflip follower bk pass would re-fire qsNo.alt anyway. The pre_form here is the sibling fwd_replacement at the opposite exit_y from isolated_form — that's the form the chain settles into pre-reflip when the heuristic blocked the upgrade — so the emission stays narrow instead of crossing every (pre_form × follower_variant) combination.
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
        # The chain's settled "wrong" pre_form is the fwd_replacement at a different exit_y from the isolated_form's exit_y — that's what the follower's default entry coaxed the target into before the heuristic kicked in.
        pre_forms: set[str] = set()
        for fwd_exit_y, fwd_variant in target_fwd.items():
            if fwd_exit_y in isolated_exit_ys:
                continue
            if fwd_variant in glyph_names and fwd_variant != isolated_form:
                pre_forms.add(fwd_variant)
        if not pre_forms:
            continue
        prior_slot = frozenset(_fwd_pair_source_slot(prior_base) & glyph_names)
        if not prior_slot:
            continue
        follower_variants = sorted(base_to_variants.get(follower_base, set()) & glyph_names)
        if not follower_variants:
            continue
        bucket = pair_guard_reflip.setdefault(target_base, [])
        for pre_form in sorted(pre_forms):
            for follower_variant in follower_variants:
                entry = (prior_slot, pre_form, follower_variant, isolated_form)
                if entry not in bucket:
                    bucket.append(entry)

    # Emit paired re-flip lookups for bk-pair / bk-general guards. Each rule substitutes the candidate's post-suppressed form back to its isolated form when the same (prior_slot, candidate, base) triple that motivated the guard fires. Place this AFTER `calt_fwd_*` (which mutates the candidate in the first place) so we see the post-fwd form, and AFTER the bk lookups that emitted the guards.
    for candidate_base in sorted(pair_guard_reflip):
        rules = pair_guard_reflip[candidate_base]
        # Deduplicate while preserving stable order.
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
        for prior_slot, pre_form, base_name, isolated_form in sorted(
            reflip_unique,
            key=lambda item: (item[2], item[1], item[3], sorted(item[0])),
        ):
            prior_list = " ".join(sorted(prior_slot))
            lines.append(f"        sub [{prior_list}] {pre_form}' {base_name} by {isolated_form};")
        lines.append(f"    }} calt_pair_guard_reflip_{safe};")

    # Emit post-reflip follower bk passes. When `calt_pair_guard_reflip_*` swaps a predecessor into a glyph that DOES carry an `@exit_y<n>` anchor (the isolated form), the follower's `calt_*_bk_*` passes have already run against the buffer's pre-reflip state and so missed the chance to fire `bk_replacements[follower][n]`. Re-fire that single substitution here, gated on the isolated form as the prior, with the same cycle-prevention lookahead guards the earlier bk passes use. Followers without a matching `bk_replacements[follower][exit_y]` upgrade emit no rules — silent no-op.
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
    for leader_form, trailing_form, isolated_form in plan.trailing_demote_overrides:
        if leader_form not in glyph_names:
            continue
        if trailing_form not in glyph_names:
            continue
        if isolated_form not in glyph_names:
            continue
        trailing_meta = glyph_meta.get(trailing_form)
        if trailing_meta is None:
            continue
        trailing_demote_by_base.setdefault(trailing_meta.base_name, []).append(
            (leader_form, trailing_form, isolated_form)
        )
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
        lines.append(f"    lookup calt_trailing_demote_{safe} {{")
        for leader_form, trailing_form, isolated_form in sorted(unique):
            lines.append(f"        sub {leader_form} {trailing_form}' by {isolated_form};")
        lines.append(f"    }} calt_trailing_demote_{safe};")

    # Emit predecessor-demote lookups for `predecessor_demote_overrides`. Each rule fires after all earlier lookups have settled. Demote the now-stale extended predecessor back to its isolated form whenever the trigger sits in its entryless variant — at this post-pass the trigger's form already implies whether the join is broken, so no third-glyph guard is needed. Group rules by predecessor base for stable lookup names and counts.
    pred_demote_by_base: dict[str, list[tuple[str, str, str]]] = {}
    for predecessor_form, trigger_form, isolated_form in plan.predecessor_demote_overrides:
        if predecessor_form not in glyph_names:
            continue
        if trigger_form not in glyph_names:
            continue
        if isolated_form not in glyph_names:
            continue
        pred_meta = glyph_meta.get(predecessor_form)
        if pred_meta is None:
            continue
        pred_demote_by_base.setdefault(pred_meta.base_name, []).append(
            (predecessor_form, trigger_form, isolated_form)
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

    def _derived_trigger_forms(mid_base: str) -> tuple[str, ...]:
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

    for predecessor_form in sorted(glyph_names):
        pred_meta = glyph_meta.get(predecessor_form)
        if pred_meta is None:
            continue
        isolated_form = _drop_exit_extension_suffix(predecessor_form)
        if isolated_form is not None and pred_meta.before:
            trigger_forms = _expand_all_variants(pred_meta.before, include_base=True)
            for trigger_form in sorted(trigger_forms):
                trigger_meta = glyph_meta.get(trigger_form)
                if trigger_meta is None:
                    continue
                if any(exit_y in set(trigger_meta.all_entry_ys) for exit_y in pred_meta.exit_ys):
                    continue
                pred_demote_by_base.setdefault(pred_meta.base_name, []).append(
                    (predecessor_form, trigger_form, isolated_form)
                )
        if pred_meta.base_name not in derived_pred_demote_bases:
            continue
        derived_iso_form = _derived_pred_demote_iso_form(predecessor_form)
        if derived_iso_form is None:
            continue
        for exit_y in set(pred_meta.exit_ys):
            guard_entries = tuple(
                guard
                for replacement_lookup in _derived_strip_guard_lookup_names(predecessor_form)
                for guard in _derived_fwd_strip_guards.get(
                    (pred_meta.base_name, replacement_lookup, exit_y),
                    (),
                )
            )
            for guard in guard_entries:
                for trigger_form in _derived_trigger_forms(guard.mid_base):
                    trigger_meta = glyph_meta.get(trigger_form)
                    if trigger_meta is None:
                        continue
                    if exit_y in set(trigger_meta.all_entry_ys):
                        continue
                    pred_demote_by_base.setdefault(pred_meta.base_name, []).append(
                        (predecessor_form, trigger_form, derived_iso_form)
                    )

    def _emit_pred_demote_lookups(prefix: str) -> None:
        for predecessor_base in sorted(pred_demote_by_base):
            pred_rules = pred_demote_by_base[predecessor_base]
            pred_seen: set[tuple[str, str, str]] = set()
            pred_unique: list[tuple[str, str, str]] = []
            for entry in pred_rules:
                if entry in pred_seen:
                    continue
                pred_seen.add(entry)
                pred_unique.append(entry)
            if not pred_unique:
                continue
            safe = predecessor_base.replace(".", "_").replace("-", "_")
            lines.append("")
            lines.append(f"    lookup {prefix}_{safe} {{")
            for predecessor_form, trigger_form, isolated_form in sorted(pred_unique):
                lines.append(f"        sub {predecessor_form}' {trigger_form} by {isolated_form};")
            lines.append(f"    }} {prefix}_{safe};")

    _emit_pred_demote_lookups("calt_pred_demote")

    noentry_exit_contract_by_base: dict[str, list[tuple[str, str, str]]] = {}
    for source_form in sorted(glyph_names):
        source_meta = glyph_meta.get(source_form)
        if source_meta is None or source_meta.contract_exit_before is None:
            continue
        if not source_meta.exit:
            continue
        suffix_word = _EXIT_CONTRACTION_WORD_BY_COUNT.get(source_meta.contract_exit_before.by)
        if suffix_word is None:
            continue
        noentry_form = f"{source_form}.noentry"
        replacement = f"{noentry_form}.ex-{suffix_word}"
        if noentry_form not in glyph_names or replacement not in glyph_names:
            continue
        trigger_forms = _expand_all_variants(source_meta.contract_exit_before.targets, include_base=True)
        for trigger_form in sorted(trigger_forms):
            trigger_meta = glyph_meta.get(trigger_form)
            if trigger_meta is None:
                continue
            if not any(exit_y in set(trigger_meta.all_entry_ys) for exit_y in source_meta.exit_ys):
                continue
            noentry_exit_contract_by_base.setdefault(source_meta.base_name, []).append(
                (noentry_form, trigger_form, replacement)
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
        for noentry_form, trigger_form, replacement in sorted(unique_rules):
            lines.append(f"        sub {noentry_form}' {trigger_form} by {replacement};")
        lines.append(f"    }} calt_noentry_exit_contract_{safe};")

    def _find_demote_sibling(
        base_name: str,
        prior_form: str,
        prior_meta,
        successor_form: str,
        isolated_form: str,
    ) -> str | None:
        # When the successor needs to be demoted because the prior's exit doesn't
        # match the successor's entry, prefer a sibling whose entry actually does
        # match the prior's exit and whose own `select.after` claims this prior —
        # falling back on the bare isolated form silently drops the cursive-join
        # upgrade for an otherwise-valid pair (e.g. `qsIt.en-y5 qsRoe`
        # should land on `qsRoe.en-ext-1-at-0`, not bare `qsRoe`).
        if not prior_meta.exit_ys:
            return None
        prior_exit_ys = set(prior_meta.exit_ys)
        for name in sorted(glyph_names):
            if name in (successor_form, isolated_form):
                continue
            sibling_meta = glyph_meta.get(name)
            if sibling_meta is None or sibling_meta.base_name != base_name:
                continue
            if not any(y in prior_exit_ys for y in sibling_meta.all_entry_ys):
                continue
            if not sibling_meta.after or prior_form not in sibling_meta.after:
                continue
            return name
        return None

    successor_demote_by_base: dict[str, list[tuple[str, str, str]]] = {}
    for successor_form in sorted(glyph_names):
        successor_meta = glyph_meta.get(successor_form)
        if successor_meta is None or not successor_meta.after:
            continue
        isolated_form = _drop_entry_extension_suffix(successor_form)
        if isolated_form is None:
            continue
        prior_forms = _expand_all_variants(successor_meta.after, include_base=True)
        for prior_form in sorted(prior_forms):
            prior_meta = glyph_meta.get(prior_form)
            if prior_meta is None:
                continue
            if any(entry_y in set(prior_meta.exit_ys) for entry_y in successor_meta.all_entry_ys):
                continue
            target_form = (
                _find_demote_sibling(
                    successor_meta.base_name,
                    prior_form,
                    prior_meta,
                    successor_form,
                    isolated_form,
                )
                or isolated_form
            )
            successor_demote_by_base.setdefault(successor_meta.base_name, []).append(
                (prior_form, successor_form, target_form)
            )

    # An entry-extension form that also picked up an exit modifier (e.g. `qsRoe.ex-y0.en-ext-1-at-5`, which exits toward a baseline follower) has its `select.after` cleared on the derived variant, so the loop above skips it. But it needs the same demotion: after a prior whose exit lands at the wrong Y for its entry, the entry side can't actually join, and the in-context render must fall back to the same target the bare entry-extension form does. Mirror every rule onto the form's exit-modifier siblings, dropping the now-unreachable exit by reusing the parent's target. The target (an entry-extension form at the prior-matching Y) carries no exit itself, so the demoted glyph surrenders its follower join — correct, since the letter can't enter and exit at the same Y.
    for successor_base in sorted(successor_demote_by_base):
        sibling_rules: list[tuple[str, str, str]] = []
        for prior_form, successor_form, target_form in successor_demote_by_base[successor_base]:
            successor_meta = glyph_meta.get(successor_form)
            if successor_meta is None or successor_meta.extended_entry_suffix is None:
                continue
            for sibling_name in sorted(glyph_names):
                if sibling_name == successor_form:
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
                # The sibling is the entry-extension form plus one or more exit modifiers; demote it the same way (the parent had no exit, so the sibling drops its exit on the way down).
                if not (set(successor_meta.modifiers) < set(sibling_meta.modifiers)):
                    continue
                sibling_rules.append((prior_form, sibling_name, target_form))
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
        for prior_form, successor_form, target_form in sorted(successor_unique):
            lines.append(f"        sub {prior_form} {successor_form}' by {target_form};")
        lines.append(f"    }} calt_successor_demote_{safe};")

    # Heal the curated names against the live glyph set: the source-of-truth form names changed when `_synthesize_anchor_modifiers` started filling in en-y0 / ex-y0, so a literal `qsVie.ex-y0` no longer compiles. `heal_glyph_name` rewrites each side of the rule to its post-synthesis counterpart.
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
    for prior_form, successor_form, isolated_form in entry_demote_rules:
        if (
            prior_form not in glyph_names
            or successor_form not in glyph_names
            or isolated_form not in glyph_names
        ):
            continue
        if not emitted_entry_demote:
            lines.append("")
            lines.append("    lookup calt_successor_demote_qsOut_qsTea {")
            emitted_entry_demote = True
        lines.append(f"        sub {prior_form} {successor_form}' by {isolated_form};")
    if emitted_entry_demote:
        lines.append("    } calt_successor_demote_qsOut_qsTea;")

    _emit_pred_demote_lookups("calt_final_pred_demote")

    # Glyphs that literally carry an entry anchor at Y — the receivers a `extend_exit_when_entered` exit may attach to. NOT `@entry_y{N}`: that class also holds bare bases and entry-stripped forward replacements that could promote to an entry at Y but don't in their final form (e.g. the trailing qsMay in ·Bay·May·May·Ah settles on qsMay.ex-y0, no entry). Extending toward those would strand the extra ink, so the lookahead is restricted to glyphs whose final form actually receives.
    def _literal_entry_receivers(target_y: int) -> list[str]:
        return sorted(
            g
            for g in glyph_names
            if any(a[1] == target_y for a in (*_meta(g).entry, *_meta(g).entry_curs_only))
        )

    # `extend_exit_when_entered`: lengthen a backward-entry-upgrade target's exit toward its x-height receivers, kept gated on the predecessor that supplied the entry join. The carrier form and its entry-extension siblings only ever appear after that join (word-initial / non-baseline contexts settle on the bare base), so this final lookup matches them directly — no backtrack — without leaking onto the bare form. Placed last so it sees whichever entry-side form the predecessor produced (plain or en-ext-1).
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
            # Match the carrier and its entry-side siblings (en-ext-1, …) but not the bare base nor any already exit-modified form.
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

    lines.append("} calt;")
    lines = _strip_post_zwnj_from_ignore_contexts(lines, base_to_variants)
    lines = _ensure_zwnj_coverage_for_calt_lookups(lines)
    lines = _add_zwnj_guards_for_two_position_forward_rules(lines)
    lines = _coalesce_consecutive_ignore_rules(lines)

    if _active_contract_recorder is not None:
        _active_contract_recorder.flush()
        _active_contract_recorder = None

    return "\n".join(lines)


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


def emit_quikscript_ss(glyph_meta: dict[str, JoinGlyph]) -> str | None:
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name, meta in glyph_meta.items():
        if meta.revert_feature:
            groups[meta.revert_feature].append((name, meta.base_name))

    NOJOIN_TAG = "ss10"
    for name, meta in glyph_meta.items():
        if name != meta.base_name and len(meta.sequence) <= 1:
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
    predecessor_demote_overrides: tuple[tuple[str, str, str], ...] = (),
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

    ss_gate_fea = _emit_quikscript_ss_gate(analysis)
    if ss_gate_fea:
        parts.append(ss_gate_fea)

    calt_fea = _emit_quikscript_calt(analysis)
    if calt_fea:
        parts.append(calt_fea)

    ss_fea = emit_quikscript_ss(join_glyphs)
    if ss_fea:
        parts.append(ss_fea)

    if not parts:
        return None
    return "\n\n".join(parts)


__all__ = ["emit_quikscript_senior_features", "emit_quikscript_ss"]
