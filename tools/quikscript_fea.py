from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import product

from quikscript_ir import JoinGlyph, resolve_known_glyph_names


_ENTRY_EXTENSION_SUFFIXES = (
    ".entry-triply-extended",
    ".entry-doubly-extended",
    ".entry-extended",
)


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
    pair_overrides: dict[str, list[tuple[str, list[str]]]] = field(default_factory=dict)
    fwd_upgrades: dict[str, list[tuple[str, str, int, list[str]]]] = field(default_factory=dict)
    fwd_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    fwd_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    fwd_bk_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    fwd_pair_overrides: dict[str, list[tuple[str, list[str], list[str]]]] = field(default_factory=dict)
    reverse_only_upgrades: list[tuple[str, list[str], list[int], list[str], list[str]]] = field(default_factory=list)
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
    gated_fwd_pair_overrides: dict[str, list[tuple[str, list[str], list[str], str]]] = field(default_factory=dict)


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
            not_before = meta.not_before
            if not_before and "half" in meta.traits:
                bk_fwd_candidates.append((base_name, entry_y, glyph_name, list(not_before)))

    for base_name, entry_y, glyph_name, not_before in bk_fwd_candidates:
        if bk_replacements.get(base_name, {}).get(entry_y) == glyph_name:
            resolved_fwd = resolve_known_glyph_names(not_before, plan.glyph_names)
            plan.bk_fwd_exclusions.setdefault(base_name, {})[entry_y] = resolved_fwd

    # `derive.noentry_after` on a non-ligature family becomes a backward
    # pair override that swaps the base glyph for its `.noentry` variant
    # after the listed families. Ligatures route through the post-liga
    # cleanup pipeline below; this branch keeps plain families one-line.
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
        pair_overrides.setdefault(glyph_name, []).append(
            (noentry_name, list(meta.noentry_after))
        )

    for base_name, overrides in pair_overrides.items():
        by_after: dict[tuple, list[tuple[str, list[str]]]] = {}
        for variant_name, after in overrides:
            key = tuple(sorted(after))
            by_after.setdefault(key, []).append((variant_name, after))
        deferred_pair_exit_variants: set[str] = set()
        for group in by_after.values():
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
        extra_parts = meta.modifier_set - {"alt", "prop"}
        if extra_parts and "alt" in meta.traits and meta.entry and not meta.before:
            continue
        if not meta.exit:
            continue
        exit_y = meta.exit[0][1]
        base_name = meta.base_name
        if base_name not in glyph_meta:
            continue
        calt_before = meta.before
        gated_before = meta.gated_before
        if calt_before:
            resolved = resolve_known_glyph_names(calt_before, plan.glyph_names)
            not_after = meta.not_after
            resolved_not_after = (
                resolve_known_glyph_names(not_after, plan.glyph_names) if not_after else []
            )
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
            resolved_not_after = (
                resolve_known_glyph_names(not_after, plan.glyph_names) if not_after else []
            )
            for feature_tag, families in gated_before:
                resolved_gated = resolve_known_glyph_names(list(families), plan.glyph_names)
                plan.gated_fwd_pair_overrides.setdefault(base_name, []).append(
                    (glyph_name, resolved_gated, resolved_not_after, feature_tag)
                )
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
    pair_only_fwd_candidates = (
        pair_override_bases - set(bk_replacements) - entry_ext_fwd_only
    ) & set(fwd_replacements)
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
            # Emit only the plain forward-exit Ys that another base's
            # backward substitutions cannot see until this base has changed.
            if any(other_base != base_name and exit_y in set(entry_variants) for other_base, entry_variants in bk_replacements.items()):
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
        if base_meta and base_meta.sequence and all(component in glyph_meta for component in base_meta.sequence):
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

    return plan


def _can_eventually_exit_at(
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    name: str,
    y: int,
) -> bool:
    meta = glyph_meta[name]
    if y in meta.exit_ys:
        return True
    if meta.exit:
        return False
    return any(
        y in glyph_meta[candidate].exit_ys
        for candidate in base_to_variants.get(meta.base_name, ())
    )


def _has_left_entry(meta: JoinGlyph) -> bool:
    return bool(meta.entry or meta.entry_curs_only)


def _entry_anchor_is_visual_addition(
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
    variant_name: str,
) -> bool:
    """Return True when this variant's entry anchor goes with a left tail in
    the bitmap that no naturally-entryless sibling has.

    Returns False when there exists a naturally-entryless sibling sharing the
    same bitmap — meaning the entry anchor is purely positional (the bitmap
    looks identical with or without it). Auto-generated `.noentry` siblings
    do not count: they share the bitmap by construction, since they exist to
    strip the cursive anchor without redrawing the glyph.
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
            # Auto-generated noentry stripper — same bitmap as its source,
            # not an independent design.
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
    glyph_meta: dict[str, JoinGlyph],
    base_to_variants: dict[str, set[str]],
) -> set[str]:
    variant_meta = glyph_meta[variant_name]
    entry_ys = set(variant_meta.entry_ys)
    expanded: set[str] = set()

    for after_glyph in after_glyphs:
        candidates = set(expand_selector(after_glyph))
        # Also consider any generated .noentry variants of the same base.
        # calt_zwnj substitutes a qs letter with one of these forms after a
        # ZWNJ, and the standard variant expander does not return them, so
        # backward-context rules would otherwise miss those cases and fail to
        # fire.
        after_base = (
            glyph_meta[after_glyph].base_name
            if after_glyph in glyph_meta
            else after_glyph
        )
        for candidate_name in base_to_variants.get(after_base, ()):
            if glyph_meta[candidate_name].is_noentry:
                candidates.add(candidate_name)
        if entry_ys:
            candidates = {
                candidate for candidate in candidates
                if (
                    set(glyph_meta[candidate].exit_ys) & entry_ys
                    or (
                        not glyph_meta[candidate].exit
                        and any(
                            _can_eventually_exit_at(
                                glyph_meta,
                                base_to_variants,
                                candidate,
                                entry_y,
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
            candidate for candidate in expanded
            if _can_eventually_exit_at(glyph_meta, base_to_variants, candidate, entry_y)
        }

    return expanded


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
                if other_tokens[slot + 1:] != tokens[slot + 1:]:
                    continue
                group.append(j)
            if len(group) > len(best_group):
                best_group = group
                best_slot = slot

        if best_slot is not None and len(best_group) > 1:
            replacements = {
                deduped_entries[group_index][1][best_slot] for group_index in best_group
            }
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


def _emit_quikscript_calt(analysis: _JoinAnalysis) -> str | None:
    plan = analysis
    glyph_meta = plan.glyph_meta
    glyph_names = plan.glyph_names
    base_to_variants = plan.base_to_variants

    # Function-local import: quikscript_join_analysis imports from quikscript_fea,
    # so a top-of-module import here would cycle.
    from quikscript_join_analysis import (
        DerivedBkGuard,
        JoinReachability,
        derive_pending_bk_entry_guards,
        derive_pending_liga_entry_guards,
    )

    _reachability = JoinReachability.from_join_glyphs(glyph_meta)
    _derived_bk_guards = derive_pending_bk_entry_guards(_reachability)
    _derived_liga_guards = derive_pending_liga_entry_guards(_reachability)

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
        expanded = set(glyphs)
        for glyph in glyphs:
            base = _base_name(glyph)
            if base not in glyph_meta:
                continue
            form_specific = glyph != base
            if include_base:
                expanded.add(base)
            all_variants: set[str] = set()
            if base in bk_replacements:
                all_variants.update(bk_replacements[base].values())
            if base in fwd_replacements:
                all_variants.update(fwd_replacements[base].values())
            if base in pair_overrides:
                all_variants.update(variant_name for variant_name, _ in pair_overrides[base])
            if base in fwd_pair_overrides:
                all_variants.update(variant_name for variant_name, _, _ in fwd_pair_overrides[base])
            if form_specific:
                prefix = glyph + "."
                expanded.update(v for v in all_variants if v == glyph or v.startswith(prefix))
            else:
                expanded.update(all_variants)
        return expanded

    def _ligature_component_variants(lig_name: str, component: str, index: int) -> set[str]:
        lig_variants: set[str] = {component}
        if component in bk_replacements:
            lig_variants.update(bk_replacements[component].values())
        if component in pair_overrides:
            lig_variants.update(variant_name for variant_name, _ in pair_overrides[component])
        if component in fwd_pair_overrides:
            lig_variants.update(variant_name for variant_name, _, _ in fwd_pair_overrides[component])
        if (
            index == 0
            or (
                index == 1
                and lig_name in _LIGATURES_ALLOWING_SECOND_COMPONENT_FWD_VARIANTS
            )
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

    for y in sorted(exit_classes):
        members = sorted(exit_classes[y])
        if members:
            lines.append(f"    @exit_y{y} = [{' '.join(members)}];")

    for y in sorted(fwd_used_ys):
        if y in entry_classes:
            members = sorted(entry_classes[y])
            lines.append(f"    @entry_y{y} = [{' '.join(members)}];")
        preferred_needs_excl = any(
            sibling_y == y
            for entries in fwd_preferred_lookahead.values()
            for _, _, sibling_y in entries
        )
        needs_excl = any(entry_y == y for _, entry_y in fwd_use_exclusive) or preferred_needs_excl
        if needs_excl and y in entry_exclusive:
            excl_members = sorted(entry_exclusive[y])
            if excl_members:
                lines.append(f"    @entry_only_y{y} = [{' '.join(excl_members)}];")

    for (exit_y, sibling_y), bridge_members in sorted(plan.preferred_lookahead_bridges.items()):
        if not bridge_members:
            continue
        lines.append(
            f"    @bridge_y{exit_y}_y{sibling_y} = [{' '.join(sorted(bridge_members))}];"
        )

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
                trigger_contexts = set(
                    entry_exclusive[mid_exit_y] if use_excl else entry_classes[mid_exit_y]
                )
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
        """Emit ``ignore sub`` rules that block ``source_name → replacement``
        when the next glyph has an entry that triggers this substitution but
        later strips that same entry through its own forward substitution.

        Covers both general forward substitutions (``fwd_replacements``) and
        pair-specific ones (``fwd_pair_overrides``). Without these guards, the
        left glyph's exit ends up orphaned against the stripped mid glyph.
        """
        if replacement_name not in glyph_meta or exit_y not in _meta(replacement_name).exit_ys:
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
                f"{left_context} {source_name}'"
                if left_context is not None
                else f"{source_name}'"
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
                    if (
                        exit_y in incompatible_ys
                        and replacement_name in exit_classes.get(exit_y, set())
                    ):
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
                    extended = pair_variant + ".entry-extended"
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

        for mid_source in sorted(right_context_glyphs):
            if mid_source not in glyph_meta:
                continue
            mid_meta = _meta(mid_source)
            if exit_y not in set(mid_meta.all_entry_ys):
                continue
            mid_base = mid_meta.base_name
            if require_mid_base_without_exit and _meta(mid_base).exit:
                continue

            if mid_source == mid_base:
                for mid_exit_y, mid_replacement in sorted(
                    fwd_replacements.get(mid_base, {}).items()
                ):
                    if mid_exit_y not in entry_classes:
                        continue
                    if set(_meta(mid_replacement).all_entry_ys):
                        continue
                    fwd_bk_excl = plan.fwd_bk_exclusions.get(mid_base, {}).get(mid_exit_y)
                    if fwd_bk_excl and replacement_name in _expand_exclusions(fwd_bk_excl):
                        continue
                    mid_use_excl = (mid_base, mid_exit_y) in fwd_use_exclusive
                    if mid_use_excl and (
                        mid_exit_y not in entry_exclusive
                        or not entry_exclusive[mid_exit_y]
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
                    _emit_guard(mid_source, trigger_contexts)
                    for stripped_variant in sorted(_entry_stripped_variants(mid_replacement)):
                        _emit_guard(stripped_variant, trigger_contexts)

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
                if set(_meta(actual_mid_variant).all_entry_ys):
                    continue
                effective_before -= _preserved_before_contexts(
                    mid_source,
                    actual_mid_variant,
                    exit_y,
                    replacement_name,
                )
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
            for lig_name, components in ligatures_by_first_component.get(mid_base, ()):
                if len(components) != 2:
                    continue
                lig_variant_entry_ys: set[int] = set()
                for lig_variant in base_to_variants.get(lig_name, {lig_name}):
                    if lig_variant in glyph_meta:
                        lig_variant_entry_ys.update(_meta(lig_variant).all_entry_ys)
                if exit_y in lig_variant_entry_ys:
                    continue
                first_component_variants = _ligature_component_variants(
                    lig_name, components[0], 0
                )
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

    def _emit_fwd_pairs(base_name: str):
        if base_name in fwd_pair_overrides:
            sorted_overrides = sorted(
                fwd_pair_overrides[base_name],
                key=lambda item: _backward_pair_sort_key(glyph_meta, item[0], item[1]),
            )
            for variant_name, before_glyphs, not_after_glyphs in sorted_overrides:
                expanded_before = _expand_all_variants(before_glyphs)
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

                expanded_not_after = _expand_all_variants(not_after_glyphs, include_base=True)

                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_fwd_pair_{safe} {{")
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
                                    compatible.update(
                                        entry_classes.get(ty, set()) & expanded_before
                                    )
                                if compatible:
                                    filtered = expanded_before - compatible
                                    if not filtered:
                                        continue
                                    target_before = filtered
                            elif target_meta.after:
                                continue
                        elif not target_has_entry:
                            if variant_meta.exit:
                                variant_exit_ys = set(variant_meta.exit_ys)
                                base_for_target = target_meta.base_name
                                protect_ys = set()
                                for bk_y, bk_var in bk_replacements.get(base_for_target, {}).items():
                                    bk_meta = _meta(bk_var)
                                    if bk_meta.exit:
                                        bk_exit_ys = set(bk_meta.exit_ys)
                                        if variant_exit_ys <= bk_exit_ys:
                                            protect_ys.add(bk_y)
                                        else:
                                            compatible = set()
                                            for bk_exit_y in bk_exit_ys:
                                                compatible.update(
                                                    entry_classes.get(bk_exit_y, set())
                                                    & expanded_before
                                                )
                                            if compatible:
                                                ig_glyphs = exit_classes.get(bk_y, set())
                                                if ig_glyphs:
                                                    partial_ignores.append((
                                                        " ".join(sorted(ig_glyphs)),
                                                        " ".join(sorted(compatible)),
                                                    ))
                                            else:
                                                protect_ys.add(bk_y)
                                if protect_ys:
                                    guard_glyphs = set()
                                    for protect_y in protect_ys:
                                        guard_glyphs.update(exit_classes.get(protect_y, set()))
                                    if guard_glyphs:
                                        guard_list = " ".join(sorted(guard_glyphs))
                    actual_variant = variant_name
                    suffix = target_meta.extended_entry_suffix
                    if suffix:
                        extended = variant_name + suffix
                        if extended not in glyph_names:
                            extended = variant_name + ".entry-extended"
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
                    effective_before_list = " ".join(sorted(effective_before)) if target_before is not None else before_list
                    actual_variant_meta = _meta(actual_variant)
                    actual_entry_ys = (
                        set(actual_variant_meta.entry_ys) if actual_variant_meta.entry else set()
                    )
                    entry_backtrack_prefix = ""
                    if actual_entry_ys and not target_has_entry:
                        if _entry_anchor_is_visual_addition(
                            glyph_meta, base_to_variants, actual_variant
                        ):
                            entry_backtrack_glyphs: set[str] = set()
                            for entry_y in actual_entry_ys:
                                entry_backtrack_glyphs.update(exit_classes.get(entry_y, set()))
                            entry_backtrack_glyphs -= expanded_not_after
                            if not entry_backtrack_glyphs:
                                continue
                            entry_backtrack_prefix = (
                                f"[{' '.join(sorted(entry_backtrack_glyphs))}] "
                            )
                    for pi_guard, pi_before in partial_ignores:
                        lines.append(f"        ignore sub [{pi_guard}] {target}' [{pi_before}];")
                    if guard_list:
                        lines.append(f"        ignore sub [{guard_list}] {target}' [{effective_before_list}];")
                    if expanded_not_after:
                        not_after_list = " ".join(sorted(expanded_not_after))
                        lines.append(f"        ignore sub [{not_after_list}] {target}' [{effective_before_list}];")
                    for terminal in sorted(effective_before & plan.terminal_exit_only):
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
                    lines.append(
                        f"        sub {entry_backtrack_prefix}{target}' [{effective_before_list}] by {actual_variant};"
                    )
                lines.append(f"    }} calt_fwd_pair_{safe};")

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
            lines.append(f"        sub {base_name}' {cls} by {variant_name};")
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
                if fwd_bk_excl:
                    for bg in sorted(_expand_exclusions(fwd_bk_excl)):
                        lines.append(f"        ignore sub {bg} {noentry_name}' {cls};")
                actual_variant = _resolve_noentry_replacement(
                    glyph_meta,
                    base_to_variants,
                    noentry_name,
                    variant_name,
                )
                if actual_variant is None:
                    continue
                right_context_glyphs = set(
                    entry_exclusive[exit_y] if use_excl else entry_classes[exit_y]
                )
                _emit_narrow_mid_entry_strip_guards(
                    noentry_name,
                    actual_variant,
                    exit_y,
                    right_context_glyphs,
                )
                lines.append(f"        sub {noentry_name}' {cls} by {actual_variant};")
                emitted = True
        if emitted:
            lines.append(f"    }} {lookup_name};")
        else:
            lines.pop()
            lines.pop()

    def _emit_fwd(base_name: str):
        _emit_fwd_pairs(base_name)
        _emit_fwd_general(base_name)

    def _collect_pending_bk_pair_guards(
        candidate_name: str,
        entry_ys: set[int],
        right_base_name: str,
    ) -> set[str]:
        candidate_meta = _meta(candidate_name)
        if candidate_meta.exit:
            return set()
        # .noentry variants only appear after calt_zwnj substitutes the base
        # form following a ZWNJ. They should not inherit the backward-guard
        # rules that would otherwise block the pair substitution, because the
        # ZWNJ is what drives the .noentry form in the first place.
        if candidate_meta.is_noentry:
            return set()

        candidate_base = candidate_meta.base_name
        guards: set[str] = set()

        for prev_exit_y, pending_variant in bk_replacements.get(candidate_base, {}).items():
            if _candidate_can_support_entry_ys(pending_variant, entry_ys):
                continue
            guards.update(exit_classes.get(prev_exit_y, set()))

        for pending_variant, prev_glyphs in pair_overrides.get(candidate_base, []):
            if _candidate_can_support_entry_ys(pending_variant, entry_ys):
                continue
            guards.update(_expand_all_variants(prev_glyphs))

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

    def _candidate_can_support_entry_ys(candidate_name: str, entry_ys: set[int]) -> bool:
        candidate_meta = _meta(candidate_name)
        if set(candidate_meta.exit_ys) & entry_ys:
            return True
        if candidate_meta.exit:
            return False
        return any(
            _can_eventually_exit_at(glyph_meta, base_to_variants, candidate_name, entry_y)
            for entry_y in entry_ys
        )

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
                    glyph_meta=glyph_meta,
                    base_to_variants=base_to_variants,
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
                            lines.append(
                                f"        ignore sub [{guard_list}] {candidate_name} {base_name}';"
                            )
                lookahead = ""
                if variant_meta.before:
                    before_list = " ".join(sorted(_expand_all_variants(variant_meta.before)))
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
                        filtered = sorted(exit_classes[entry_y] - excluded)
                        if filtered:
                            member_list = " ".join(filtered)
                            _emit_entry_strip_guards_for_replacement_exit(
                                base_name,
                                variant_name,
                                left_context=f"[{member_list}]",
                            )
                            lines.append(f"        sub [{member_list}] {base_name}' by {variant_name};")
                            for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                                _emit_entry_strip_guards_for_replacement_exit(
                                    fpt,
                                    variant_name,
                                    left_context=f"[{member_list}]",
                                )
                                lines.append(f"        sub [{member_list}] {fpt}' by {variant_name};")
                    else:
                        _emit_entry_strip_guards_for_replacement_exit(
                            base_name,
                            variant_name,
                            left_context=f"@exit_y{entry_y}",
                        )
                        lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
                        for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                            _emit_entry_strip_guards_for_replacement_exit(
                                fpt,
                                variant_name,
                                left_context=f"@exit_y{entry_y}",
                            )
                            lines.append(f"        sub @exit_y{entry_y} {fpt}' by {variant_name};")
            lines.append(f"    }} {lookup_name};")

    def _emit_bk_cycle(bases: list[str]):
        lines.append("")
        lines.append("    lookup calt_cycle {")
        bk_fwd_excl = plan.bk_fwd_exclusions
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
                    if excluded:
                        filtered = sorted(exit_classes[entry_y] - excluded)
                        if filtered:
                            member_list = " ".join(filtered)
                            if fwd_excl:
                                for fg in sorted(_expand_exclusions(fwd_excl)):
                                    lines.append(f"        ignore sub [{member_list}] {base_name}' {fg};")
                            _emit_entry_strip_guards_for_replacement_exit(
                                base_name,
                                variant_name,
                                left_context=f"[{member_list}]",
                            )
                            lines.append(f"        sub [{member_list}] {base_name}' by {variant_name};")
                            for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                                if fwd_excl:
                                    for fg in sorted(_expand_exclusions(fwd_excl)):
                                        lines.append(f"        ignore sub [{member_list}] {fpt}' {fg};")
                                _emit_entry_strip_guards_for_replacement_exit(
                                    fpt,
                                    variant_name,
                                    left_context=f"[{member_list}]",
                                )
                                lines.append(f"        sub [{member_list}] {fpt}' by {variant_name};")
                    else:
                        if fwd_excl:
                            for fg in sorted(_expand_exclusions(fwd_excl)):
                                lines.append(f"        ignore sub @exit_y{entry_y} {base_name}' {fg};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            base_name,
                            variant_name,
                            left_context=f"@exit_y{entry_y}",
                        )
                        lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
                        for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                            if fwd_excl:
                                for fg in sorted(_expand_exclusions(fwd_excl)):
                                    lines.append(f"        ignore sub @exit_y{entry_y} {fpt}' {fg};")
                            _emit_entry_strip_guards_for_replacement_exit(
                                fpt,
                                variant_name,
                                left_context=f"@exit_y{entry_y}",
                            )
                            lines.append(f"        sub @exit_y{entry_y} {fpt}' by {variant_name};")
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
                    lines.append(f"        sub {base_name}' {cls} by {variant_name};")
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
            lines.append(f"        sub {entry_only_var}' {cls} by {entry_exit_var};")
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
                y: variant
                for y, variant in variants.items()
                if y in upgrade_exit_ys and y in exit_classes
            }
            if not relevant:
                continue
            safe = base_name.replace(".", "_").replace("-", "_")
            lines.append("")
            exclusions = bk_exclusions.get(base_name, {})
            bk_fwd_excl = plan.bk_fwd_exclusions
            lines.append(f"    lookup calt_post_upgrade_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                fwd_excl = bk_fwd_excl.get(base_name, {}).get(entry_y)
                if excluded:
                    filtered = sorted(exit_classes[entry_y] - excluded)
                    if filtered:
                        member_list = " ".join(filtered)
                        if fwd_excl:
                            for fg in sorted(_expand_exclusions(fwd_excl)):
                                lines.append(f"        ignore sub [{member_list}] {base_name}' {fg};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            base_name,
                            relevant[entry_y],
                            left_context=f"[{member_list}]",
                        )
                        lines.append(f"        sub [{member_list}] {base_name}' by {relevant[entry_y]};")
                        for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                            if fwd_excl:
                                for fg in sorted(_expand_exclusions(fwd_excl)):
                                    lines.append(f"        ignore sub [{member_list}] {fpt}' {fg};")
                            _emit_entry_strip_guards_for_replacement_exit(
                                fpt,
                                relevant[entry_y],
                                left_context=f"[{member_list}]",
                            )
                            lines.append(f"        sub [{member_list}] {fpt}' by {relevant[entry_y]};")
                else:
                    if fwd_excl:
                        for fg in sorted(_expand_exclusions(fwd_excl)):
                            lines.append(f"        ignore sub @exit_y{entry_y} {base_name}' {fg};")
                    _emit_entry_strip_guards_for_replacement_exit(
                        base_name,
                        relevant[entry_y],
                        left_context=f"@exit_y{entry_y}",
                    )
                    lines.append(f"        sub @exit_y{entry_y} {base_name}' by {relevant[entry_y]};")
                    for fpt in _fwd_pair_bk_targets(base_name, entry_y):
                        if fwd_excl:
                            for fg in sorted(_expand_exclusions(fwd_excl)):
                                lines.append(f"        ignore sub @exit_y{entry_y} {fpt}' {fg};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            fpt,
                            relevant[entry_y],
                            left_context=f"@exit_y{entry_y}",
                        )
                        lines.append(f"        sub @exit_y{entry_y} {fpt}' by {relevant[entry_y]};")
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
            for _, fwd_variant in fwd_replacements.get(base_name, {}).items():
                if not _meta(fwd_variant).entry:
                    fwd_exit_only.append(fwd_variant)
            if not fwd_exit_only:
                continue
            safe = f"post_override_{base_name}".replace(".", "_").replace("-", "_")
            exclusions = bk_exclusions.get(base_name, {})
            lines.append("")
            lines.append(f"    lookup calt_{safe} {{")
            # HarfBuzz treats ZWNJ as a default-ignorable glyph and would
            # otherwise allow this backward-context lookup to match across a
            # ZWNJ. Mentioning uni200C in an ignore rule forces it into the
            # lookup's coverage so HarfBuzz stops skipping it.
            for fwd_variant in sorted(fwd_exit_only):
                lines.append(f"        ignore sub uni200C {fwd_variant}';")
            for entry_y in sorted(relevant.keys()):
                excluded = sorted(_expand_exclusions(exclusions.get(entry_y, [])))
                for fwd_variant in sorted(fwd_exit_only):
                    for excluded_glyph in excluded:
                        lines.append(f"        ignore sub {excluded_glyph} {fwd_variant}';")
                    lines.append(f"        sub @exit_y{entry_y} {fwd_variant}' by {relevant[entry_y]};")
            lines.append(f"    }} calt_{safe};")

    def _emit_reverse_upgrades():
        for base_name in sorted(fwd_upgrades):
            for entry_exit_var, entry_only_var, exit_y, not_before in fwd_upgrades[base_name]:
                entry_meta = _meta(entry_only_var)
                if not entry_meta.entry:
                    continue
                entry_y_val = entry_meta.entry[0][1]
                exit_only_var = fwd_replacements.get(base_name, {}).get(exit_y)
                if not exit_only_var or entry_y_val not in exit_classes:
                    continue
                safe = entry_exit_var.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_reverse_upgrade_{safe} {{")
                if not_before:
                    not_before_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                    lines.append(f"        ignore sub {exit_only_var}' [{not_before_list}];")
                lines.append(f"        sub @exit_y{entry_y_val} {exit_only_var}' by {entry_exit_var};")
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
                    glyph_meta=glyph_meta,
                    base_to_variants=base_to_variants,
                )
                if not expanded_after:
                    continue
            not_before_list = (
                " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                if not_before else None
            )
            for entry_y in valid_entry_ys:
                if expanded_after is None:
                    for source_variant in source_variants:
                        if not_before_list:
                            lines.append(f"        ignore sub {source_variant}' [{not_before_list}];")
                        lines.append(f"        sub @exit_y{entry_y} {source_variant}' by {variant_name};")
                    continue

                expanded_after_for_y = {
                    candidate for candidate in expanded_after
                    if _can_eventually_exit_at(
                        glyph_meta,
                        base_to_variants,
                        candidate,
                        entry_y,
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
                    lines.append(f"        sub {bk_var}' {cls} by {fwd_var};")
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
                        lines.append(f"        sub {ext_bk}' {cls} by {fwd_var};")
                        lines.append(f"    }} calt_fwd_override_{ext_safe};")

    def _emit_pair_fwd_overrides(base_name: str):
        if base_name in bk_replacements:
            return
        if base_name not in pair_overrides or base_name not in fwd_replacements:
            return

        source_variants = sorted({
            variant_name
            for variant_name, _ in pair_overrides[base_name]
            if not _meta(variant_name).exit and not _has_left_entry(_meta(variant_name))
        })
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
                lines.append(f"        sub {source_variant}' {cls} by {actual_variant};")
                lines.append(f"    }} calt_fwd_override_{safe};")

    def _emit_block(bases: list[str], *, use_cycle: bool = False):
        for base_name in bases:
            if base_name not in early_pair_upgrade_bases and base_name not in early_pair_fwd_general:
                _emit_bk_pairs(base_name)
        for base_name in bases:
            if base_name in early_fwd_pairs:
                _emit_fwd_pairs(base_name)
        if use_cycle:
            _emit_bk_cycle(bases)
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
        _emit_bk_pairs(base_name)
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
            lines.append(f"    lookup calt_post_pair_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                fwd_excl = bk_fwd_excl.get(cycle_base, {}).get(entry_y)
                if excluded:
                    filtered = sorted(exit_classes[entry_y] - excluded)
                    if filtered:
                        member_list = " ".join(filtered)
                        if fwd_excl:
                            for fg in sorted(_expand_exclusions(fwd_excl)):
                                lines.append(f"        ignore sub [{member_list}] {cycle_base}' {fg};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            cycle_base,
                            relevant[entry_y],
                            left_context=f"[{member_list}]",
                        )
                        lines.append(f"        sub [{member_list}] {cycle_base}' by {relevant[entry_y]};")
                        for fpt in _fwd_pair_bk_targets(cycle_base, entry_y):
                            if fwd_excl:
                                for fg in sorted(_expand_exclusions(fwd_excl)):
                                    lines.append(f"        ignore sub [{member_list}] {fpt}' {fg};")
                            _emit_entry_strip_guards_for_replacement_exit(
                                fpt,
                                relevant[entry_y],
                                left_context=f"[{member_list}]",
                            )
                            lines.append(f"        sub [{member_list}] {fpt}' by {relevant[entry_y]};")
                else:
                    if fwd_excl:
                        for fg in sorted(_expand_exclusions(fwd_excl)):
                            lines.append(f"        ignore sub @exit_y{entry_y} {cycle_base}' {fg};")
                    _emit_entry_strip_guards_for_replacement_exit(
                        cycle_base,
                        relevant[entry_y],
                        left_context=f"@exit_y{entry_y}",
                    )
                    lines.append(f"        sub @exit_y{entry_y} {cycle_base}' by {relevant[entry_y]};")
                    for fpt in _fwd_pair_bk_targets(cycle_base, entry_y):
                        if fwd_excl:
                            for fg in sorted(_expand_exclusions(fwd_excl)):
                                lines.append(f"        ignore sub @exit_y{entry_y} {fpt}' {fg};")
                        _emit_entry_strip_guards_for_replacement_exit(
                            fpt,
                            relevant[entry_y],
                            left_context=f"@exit_y{entry_y}",
                        )
                        lines.append(f"        sub @exit_y{entry_y} {fpt}' by {relevant[entry_y]};")
            lines.append(f"    }} calt_post_pair_bk_{safe};")

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
                    if (
                        fwd_exit_ys
                        and combined_exit_ys
                        and not (fwd_exit_ys & combined_exit_ys)
                    ):
                        continue
                    if not emitted_any:
                        lines.append("")
                        lines.append(f"    lookup calt_ext_bk_{safe} {{")
                        emitted_any = True
                    excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                    if excluded:
                        filtered = sorted(exit_classes[entry_y] - excluded)
                        if filtered:
                            member_list = " ".join(filtered)
                            lines.append(f"        sub [{member_list}] {fwd_var}' by {combined};")
                    else:
                        lines.append(f"        sub @exit_y{entry_y} {fwd_var}' by {combined};")
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
                glyph_meta=glyph_meta,
                base_to_variants=base_to_variants,
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

    def _collect_post_liga_right_cleanup_rules(
        lig_name: str,
        components: tuple[str, ...],
    ) -> list[tuple[str, str, str]]:
        if not components:
            return []

        component_targets = set(
            _ligature_component_variants(lig_name, components[-1], len(components) - 1)
        )
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
                    entry_y in set(_meta(component_target).exit_ys)
                    and component_target not in excluded
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

    if ligatures:
        from itertools import product

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
                    ext_lig = lig_name + suffix
                    if ext_lig not in glyph_names:
                        ext_lig = lig_name + ".entry-extended"
                    if ext_lig in glyph_names:
                        actual_lig = ext_lig
                if "half" in _meta(combo[0]).traits:
                    half_lig = lig_name + ".half"
                    if half_lig in glyph_names:
                        actual_lig = half_lig
                exit_suffix = _meta(combo[-1]).extended_exit_suffix
                if exit_suffix:
                    ext_lig = actual_lig + ".exit-extended"
                    if ext_lig in glyph_names:
                        actual_lig = ext_lig
                contracted_suffix = _meta(combo[-1]).contracted_exit_suffix
                if contracted_suffix:
                    contracted_lig = actual_lig + contracted_suffix
                    if contracted_lig in glyph_names:
                        actual_lig = contracted_lig
                lines.append(f"        sub {component_str} by {actual_lig};")
        lines.append("    } calt_liga;")

        lig_glyph_names = {lig_name for lig_name, _ in ligatures}
        post_liga_cleanup_rules: list[tuple[str, str, str]] = []
        for lig_name, components in sorted(ligatures):
            post_liga_cleanup_rules.extend(
                _collect_post_liga_right_cleanup_rules(lig_name, components)
            )
        post_liga_rules: list[tuple[str, str, list[str]]] = []
        for base_name in sorted(pair_overrides):
            for variant_name, after_glyphs in pair_overrides[base_name]:
                if not any(glyph in lig_glyph_names for glyph in after_glyphs):
                    continue
                expanded_after = _expanded_pair_after(variant_name, after_glyphs)
                # Narrow the post-liga trigger class to ligature-derived glyphs
                # only. The whole point of this lookup is to re-fire form
                # selection when a ligature glyph is the new immediate
                # predecessor; including the variant's non-ligature after
                # entries here would over-fire on plain pre-liga sequences
                # whose predecessor mutated during `calt_cycle` (e.g.,
                # `qsUtter qsThey qsJay` where forward extension turns qsUtter
                # into qsUtter.exit-extended *after* qsThey's backward lookup
                # already declined to fire). Ligature-only ensures the rule
                # truly only triggers post-collapse.
                ligature_after = sorted(
                    glyph
                    for glyph in expanded_after
                    if glyph in glyph_meta
                    and glyph_meta[glyph].base_name in lig_glyph_names
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

        # Each variant gets its own FEA lookup. In OpenType GSUB type-6 lookups
        # an `ignore` rule that matches at a position blocks every later
        # subtable in the same lookup, so sharing one lookup across variants is
        # unsafe whenever any variant carries a `not_before` — one variant's
        # ignore can swallow another variant's substitution.
        for base_name, variant_name, after_glyphs in post_liga_rules:
            after_list = " ".join(sorted(after_glyphs))
            variant_meta = _meta(variant_name)
            lookahead = ""
            if variant_meta.before:
                before_list = " ".join(sorted(_expand_all_variants(variant_meta.before)))
                if not before_list:
                    continue
                lookahead = f" [{before_list}]"
            not_before_glyphs: list[str] = []
            if variant_meta.not_before:
                resolved_nb = resolve_known_glyph_names(
                    variant_meta.not_before, glyph_names
                )
                not_before_glyphs = sorted(_expand_exclusions(resolved_nb))
            targets = {base_name}
            if base_name in bk_replacements:
                targets.update(bk_replacements[base_name].values())
            safe = variant_name.replace(".", "_").replace("-", "_")
            lines.append("")
            lines.append(f"    lookup calt_post_liga_{safe} {{")
            for target in sorted(targets):
                for nb_glyph in not_before_glyphs:
                    lines.append(
                        f"        ignore sub [{after_list}] {target}' {nb_glyph};"
                    )
                lines.append(f"        sub [{after_list}] {target}'{lookahead} by {variant_name};")
            lines.append(f"    }} calt_post_liga_{safe};")

        for base_name in sorted(lig_fwd_bases):
            _emit_fwd(base_name)

    lines.append("} calt;")
    lines = _coalesce_consecutive_ignore_rules(lines)
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

    def _candidate_can_support_entry_ys(candidate_name: str, entry_ys: set[int]) -> bool:
        candidate_meta = glyph_meta[candidate_name]
        if set(candidate_meta.exit_ys) & entry_ys:
            return True
        if candidate_meta.exit:
            return False
        return any(
            _can_eventually_exit_at(glyph_meta, plan.base_to_variants, candidate_name, entry_y)
            for entry_y in entry_ys
        )

    def _collect_pending_bk_pair_guards(candidate_name: str, entry_ys: set[int]) -> set[str]:
        candidate_meta = glyph_meta[candidate_name]
        if candidate_meta.exit:
            return set()

        candidate_base = candidate_meta.base_name
        guards: set[str] = set()

        for prev_exit_y, pending_variant in plan.bk_replacements.get(candidate_base, {}).items():
            if _candidate_can_support_entry_ys(pending_variant, entry_ys):
                continue
            guards.update(plan.exit_classes.get(prev_exit_y, set()))

        for pending_variant, prev_glyphs in plan.pair_overrides.get(candidate_base, []):
            if _candidate_can_support_entry_ys(pending_variant, entry_ys):
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
                glyph_meta=glyph_meta,
                base_to_variants=plan.base_to_variants,
            )
            if not expanded:
                continue

            for terminal in sorted(expanded & plan.terminal_entry_only):
                expanded.discard(terminal)
            if not expanded:
                continue

            bk_features[feature_tag].append(
                (base_name, variant_name, sorted(expanded))
            )

    fwd_features: dict[str, list[tuple[str, str, list[str], list[str], list[str], set[int] | None]]] = defaultdict(list)
    for base_name, overrides in plan.gated_fwd_pair_overrides.items():
        for variant_name, before_glyphs, not_after_glyphs, feature_tag in overrides:
            expanded_before = set()
            for glyph in before_glyphs:
                base = glyph_meta[glyph].base_name if glyph in glyph_meta else glyph
                expanded_before.update(plan.base_to_variants.get(base, ()))
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
                (base_name, variant_name, sorted(expanded_before),
                 sorted(expanded_not_after), sorted(targets), variant_entry_ys)
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
                    lines.append(
                        f"        ignore sub [{' '.join(after_list)}] {base_name}' {nb};"
                    )
            entry_ys = set(variant_meta.entry_ys)
            if entry_ys:
                for candidate_name in after_list:
                    guard_glyphs = _collect_pending_bk_pair_guards(candidate_name, entry_ys)
                    if guard_glyphs:
                        guard_list = " ".join(sorted(guard_glyphs))
                        lines.append(
                            f"        ignore sub [{guard_list}] {candidate_name} {base_name}';"
                        )
            before_suffix = ""
            if variant_meta.before:
                resolved_before = resolve_known_glyph_names(
                    list(variant_meta.before), glyph_names
                )
                before_expanded: set[str] = set()
                for before_glyph in resolved_before:
                    before_base = (
                        glyph_meta[before_glyph].base_name
                        if before_glyph in glyph_meta
                        else before_glyph
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
        for base_name, variant_name, before_list, not_after_list, targets, variant_entry_ys in sorted(fwd_features.get(tag, [])):
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
                        extended = variant_name + ".entry-extended"
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
                actual_entry_ys = (
                    set(actual_variant_meta.entry_ys) if actual_variant_meta.entry else set()
                )
                entry_backtrack_prefix = ""
                if actual_entry_ys and not target_has_entry:
                    if _entry_anchor_is_visual_addition(
                        glyph_meta, plan.base_to_variants, actual_variant
                    ):
                        entry_backtrack_glyphs: set[str] = set()
                        for entry_y in actual_entry_ys:
                            entry_backtrack_glyphs.update(plan.exit_classes.get(entry_y, set()))
                        entry_backtrack_glyphs -= set(not_after_list)
                        if not entry_backtrack_glyphs:
                            continue
                        entry_backtrack_prefix = (
                            f"[{' '.join(sorted(entry_backtrack_glyphs))}] "
                        )
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
) -> str | None:
    parts = []

    curs_fea = _emit_quikscript_curs(join_glyphs, pixel_width, pixel_height)
    if curs_fea:
        parts.append(curs_fea)

    analysis = _analyze_quikscript_joins(join_glyphs)

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
