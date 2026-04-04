from collections import deque
from dataclasses import dataclass, field

from quikscript_ir import JoinGlyph, resolve_known_glyph_names


@dataclass(frozen=True)
class JoinRule:
    phase: str
    kind: str
    target_base: str
    replacement: str
    match_prev_exit_y: int | None = None
    match_next_entry_y: int | None = None
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()
    not_after: tuple[str, ...] = ()
    not_before: tuple[str, ...] = ()
    source_variants: tuple[str, ...] = ()


@dataclass
class JoinPlan:
    glyph_meta: dict[str, JoinGlyph]
    glyph_names: set[str] = field(default_factory=set)
    base_to_variants: dict[str, set[str]] = field(default_factory=dict)
    bk_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    bk_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    pair_overrides: dict[str, list[tuple[str, list[str]]]] = field(default_factory=dict)
    fwd_upgrades: dict[str, list[tuple[str, str, int, list[str]]]] = field(default_factory=dict)
    fwd_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    fwd_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    fwd_pair_overrides: dict[str, list[tuple[str, list[str], list[str]]]] = field(default_factory=dict)
    reverse_only_upgrades: list[tuple[str, list[str], list[int], list[str]]] = field(default_factory=list)
    terminal_entry_only: set[str] = field(default_factory=set)
    terminal_exit_only: set[str] = field(default_factory=set)
    exit_classes: dict[int, set[str]] = field(default_factory=dict)
    entry_classes: dict[int, set[str]] = field(default_factory=dict)
    entry_exclusive: dict[int, set[str]] = field(default_factory=dict)
    fwd_use_exclusive: set[tuple[str, int]] = field(default_factory=set)
    fwd_preferred_lookahead: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)
    sorted_bases: list[str] = field(default_factory=list)
    cycle_bases: set[str] = field(default_factory=set)
    edges: dict[str, set[str]] = field(default_factory=dict)
    pair_only: list[str] = field(default_factory=list)
    all_bk_bases: list[str] = field(default_factory=list)
    all_fwd_bases: set[str] = field(default_factory=set)
    fwd_only: list[str] = field(default_factory=list)
    lig_fwd_bases: set[str] = field(default_factory=set)
    early_pair_upgrade_bases: set[str] = field(default_factory=set)
    early_fwd_pairs: set[str] = field(default_factory=set)
    ligatures: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    word_final_pairs: dict[str, str] = field(default_factory=dict)
    gated_pair_overrides: dict[str, list[tuple[str, list[str], str]]] = field(default_factory=dict)
    rules: list[JoinRule] = field(default_factory=list)

def _record_rule(plan: JoinPlan, rule: JoinRule) -> None:
    plan.rules.append(rule)


def plan_quikscript_joins(join_glyphs: dict[str, JoinGlyph]) -> JoinPlan:
    glyph_meta = join_glyphs

    def _meta(name: str) -> JoinGlyph:
        return glyph_meta[name]

    plan = JoinPlan(glyph_meta=glyph_meta, glyph_names=set(glyph_meta))
    for glyph_name, glyph_meta_entry in glyph_meta.items():
        plan.base_to_variants.setdefault(glyph_meta_entry.base_name, set()).add(glyph_name)

    bk_replacements = plan.bk_replacements
    bk_exclusions = plan.bk_exclusions
    pair_overrides = plan.pair_overrides
    fwd_upgrades = plan.fwd_upgrades

    for glyph_name, meta in glyph_meta.items():
        if meta.word_final:
            continue
        if not meta.is_entry_variant:
            if not meta.entry:
                continue
            if "half" not in meta.traits and "alt" not in meta.traits and not meta.after:
                continue
        if not meta.entry:
            continue
        if meta.reverse_upgrade_from:
            continue
        entry_y = meta.entry[0][1]
        base_name = meta.base_name
        if base_name not in glyph_meta:
            continue
        if "alt" in meta.traits:
            base_meta = glyph_meta.get(base_name)
            if base_meta and entry_y in base_meta.entry_ys:
                continue
        if meta.before:
            continue
        calt_after = meta.after
        if calt_after:
            if meta.gate_feature:
                plan.gated_pair_overrides.setdefault(base_name, []).append(
                    (glyph_name, list(calt_after), meta.gate_feature)
                )
            else:
                pair_overrides.setdefault(base_name, []).append((glyph_name, list(calt_after)))
            _record_rule(
                plan,
                JoinRule(
                    phase="backward",
                    kind="pair_override",
                    target_base=base_name,
                    replacement=glyph_name,
                    after=tuple(calt_after),
                    not_before=tuple(meta.not_before),
                ),
            )
        elif meta.extended_entry_suffix is not None:
            pass
        elif meta.extended_exit_suffix is not None:
            pass
        else:
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
                        _record_rule(
                            plan,
                            JoinRule(
                                phase="upgrade",
                                kind="forward_upgrade",
                                target_base=base_name,
                                replacement=glyph_name,
                                match_next_entry_y=exit_y_val,
                                not_before=tuple(nb),
                                source_variants=(existing,),
                            ),
                        )
                    else:
                        exit_y_val = existing_meta.exit[0][1]
                        nb = list(existing_meta.not_before)
                        fwd_upgrades.setdefault(base_name, []).append(
                            (existing, glyph_name, exit_y_val, list(nb))
                        )
                        bk_replacements[base_name][entry_y] = glyph_name
                        _record_rule(
                            plan,
                            JoinRule(
                                phase="upgrade",
                                kind="forward_upgrade",
                                target_base=base_name,
                                replacement=existing,
                                match_next_entry_y=exit_y_val,
                                not_before=tuple(nb),
                                source_variants=(glyph_name,),
                            ),
                        )
                else:
                    bk_replacements.setdefault(base_name, {})[entry_y] = glyph_name
            else:
                bk_replacements.setdefault(base_name, {})[entry_y] = glyph_name
            _record_rule(
                plan,
                JoinRule(
                    phase="backward",
                    kind="replacement",
                    target_base=base_name,
                    replacement=glyph_name,
                    match_prev_exit_y=entry_y,
                    not_after=tuple(meta.not_after),
                ),
            )
            not_after = meta.not_after
            if not_after:
                resolved = resolve_known_glyph_names(not_after, plan.glyph_names)
                bk_exclusions.setdefault(base_name, {})[entry_y] = resolved

    for base_name, overrides in pair_overrides.items():
        by_after: dict[tuple, list[tuple[str, list[str]]]] = {}
        for variant_name, after in overrides:
            key = tuple(sorted(after))
            by_after.setdefault(key, []).append((variant_name, after))
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
                entry_exit_var = with_exit[0][0]
                exit_y = with_exit[0][1].exit[0][1]
                nb = list(with_exit[0][1].not_before)
                fwd_upgrades.setdefault(base_name, []).append(
                    (entry_exit_var, entry_only_var, exit_y, list(nb))
                )
                _record_rule(
                    plan,
                    JoinRule(
                        phase="upgrade",
                        kind="pair_upgrade",
                        target_base=base_name,
                        replacement=entry_exit_var,
                        match_next_entry_y=exit_y,
                        not_before=tuple(nb),
                        source_variants=(entry_only_var,),
                    ),
                )

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
        if meta.extended_exit_suffix is not None and not meta.before:
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
        if calt_before:
            resolved = resolve_known_glyph_names(calt_before, plan.glyph_names)
            not_after = meta.not_after
            resolved_not_after = (
                resolve_known_glyph_names(not_after, plan.glyph_names) if not_after else []
            )
            fwd_pair_overrides.setdefault(base_name, []).append(
                (glyph_name, resolved, resolved_not_after)
            )
            _record_rule(
                plan,
                JoinRule(
                    phase="forward",
                    kind="pair_override",
                    target_base=base_name,
                    replacement=glyph_name,
                    before=tuple(resolved),
                    not_after=tuple(resolved_not_after),
                ),
            )
        else:
            fwd_replacements.setdefault(base_name, {})[exit_y] = glyph_name
            _record_rule(
                plan,
                JoinRule(
                    phase="forward",
                    kind="replacement",
                    target_base=base_name,
                    replacement=glyph_name,
                    match_next_entry_y=exit_y,
                    not_before=tuple(meta.not_before),
                ),
            )
            not_before = meta.not_before
            if not_before:
                resolved = resolve_known_glyph_names(not_before, plan.glyph_names)
                fwd_exclusions.setdefault(base_name, {})[exit_y] = resolved

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
                    list(meta.not_before),
                )
            )
            _record_rule(
                plan,
                JoinRule(
                    phase="reverse",
                    kind="explicit_reverse_upgrade",
                    target_base=meta.base_name,
                    replacement=glyph_name,
                    not_before=tuple(meta.not_before),
                    source_variants=tuple(matching_sources),
                ),
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
        if meta.is_noentry:
            continue
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
            if _meta(fwd_var).entry:
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
            base_exit_ys = set(base_meta.exit_ys)
            min_base_exit = min(base_exit_ys)
            for exit_y in fwd_replacements[base_name]:
                if exit_y not in base_exit_ys and exit_y < min_base_exit:
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

    all_fwd_bases = set(fwd_replacements) | set(fwd_pair_overrides)
    entry_ext_fwd_only = entry_ext_pair_only & all_fwd_bases

    pair_only = sorted(set(pair_overrides) - set(bk_replacements) - entry_ext_fwd_only)
    all_bk_bases = sorted_bases + pair_only

    fwd_only_set = all_fwd_bases - set(bk_replacements) - (set(pair_overrides) - entry_ext_pair_only)

    fwd_fwd_edges: dict[str, set[str]] = {base: set() for base in fwd_only_set}
    for base_a in fwd_only_set:
        for exit_y in fwd_replacements.get(base_a, {}):
            use_excl = (base_a, exit_y) in fwd_use_exclusive
            if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                continue
            cls = entry_exclusive[exit_y] if use_excl else entry_classes.get(exit_y, set())
            for base_b in fwd_only_set:
                if base_b == base_a or base_b not in cls:
                    continue
                for b_variant in fwd_replacements.get(base_b, {}).values():
                    if b_variant not in cls:
                        fwd_fwd_edges[base_a].add(base_b)
                        break

    for base_a in fwd_only_set:
        if base_a not in pair_overrides:
            continue
        for _, after_glyphs in pair_overrides[base_a]:
            for after_glyph in after_glyphs:
                base_b = glyph_meta[after_glyph].base_name
                if base_b != base_a and base_b in fwd_only_set:
                    fwd_fwd_edges[base_a].add(base_b)

    fwd_out: dict[str, set[str]] = {base: set() for base in fwd_only_set}
    fwd_in_deg: dict[str, int] = {base: len(fwd_fwd_edges[base]) for base in fwd_only_set}
    for base in fwd_only_set:
        for dependency in fwd_fwd_edges[base]:
            fwd_out[dependency].add(base)

    fwd_queue = deque(sorted(base for base in fwd_only_set if fwd_in_deg[base] == 0))
    fwd_only: list[str] = []
    while fwd_queue:
        node = fwd_queue.popleft()
        fwd_only.append(node)
        for neighbor in sorted(fwd_out[node]):
            fwd_in_deg[neighbor] -= 1
            if fwd_in_deg[neighbor] == 0:
                fwd_queue.append(neighbor)

    fwd_only.extend(sorted(fwd_only_set - set(fwd_only)))

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
            if base_name in {glyph_meta[glyph].base_name for glyph in before_glyphs}:
                early_fwd_pairs.add(base_name)
                found = True
                break
            variant_meta = _meta(variant_name)
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
                _record_rule(
                    plan,
                    JoinRule(
                        phase="word_final",
                        kind="replacement",
                        target_base=base_name,
                        replacement=glyph_name,
                    ),
                )

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
    plan.lig_fwd_bases = lig_fwd_bases
    plan.early_pair_upgrade_bases = early_pair_upgrade_bases
    plan.early_fwd_pairs = early_fwd_pairs
    plan.word_final_pairs = word_final_pairs
    plan.ligatures = ligatures

    return plan


__all__ = [
    "JoinPlan",
    "JoinRule",
    "plan_quikscript_joins",
]
