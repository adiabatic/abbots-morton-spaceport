from itertools import product

from quikscript_ir import JoinGlyph, resolve_known_glyph_names
from quikscript_planner import JoinPlan


def emit_quikscript_calt(plan: JoinPlan) -> str | None:
    glyph_meta = plan.glyph_meta
    glyph_names = plan.glyph_names
    base_to_variants = plan.base_to_variants

    def _meta(name: str) -> JoinGlyph:
        return glyph_meta[name]

    def _base_name(name: str) -> str:
        return _meta(name).base_name

    bk_replacements = plan.bk_replacements
    bk_exclusions = plan.bk_exclusions
    pair_overrides = plan.pair_overrides
    fwd_upgrades = plan.fwd_upgrades
    fwd_replacements = plan.fwd_replacements
    fwd_exclusions = plan.fwd_exclusions
    fwd_pair_overrides = plan.fwd_pair_overrides
    reverse_only_upgrades = plan.reverse_only_upgrades
    terminal_entry_only = plan.terminal_entry_only
    terminal_exit_only = plan.terminal_exit_only
    exit_classes = plan.exit_classes
    entry_classes = plan.entry_classes
    entry_exclusive = plan.entry_exclusive
    fwd_use_exclusive = plan.fwd_use_exclusive
    fwd_preferred_lookahead = plan.fwd_preferred_lookahead
    sorted_bases = plan.sorted_bases
    cycle_bases = plan.cycle_bases
    edges = plan.edges
    pair_only = plan.pair_only
    all_bk_bases = plan.all_bk_bases
    all_fwd_bases = plan.all_fwd_bases
    fwd_only = plan.fwd_only
    lig_fwd_bases = plan.lig_fwd_bases
    early_pair_upgrade_bases = plan.early_pair_upgrade_bases
    early_fwd_pairs = plan.early_fwd_pairs
    ligatures = plan.ligatures
    word_final_pairs = plan.word_final_pairs

    if not bk_replacements and not fwd_replacements:
        return None

    def _expand_all_variants(glyphs, *, include_base=False):
        expanded = set(glyphs)
        for glyph in glyphs:
            base = _base_name(glyph)
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

    def _can_match_pair_exit(name: str, exit_y: int) -> bool:
        meta = _meta(name)
        if exit_y in meta.exit_ys:
            return True
        if meta.exit:
            return False
        return any(
            exit_y in _meta(candidate).exit_ys
            for candidate in base_to_variants.get(meta.base_name, ())
        )

    def _expand_exclusions(excluded_glyphs: list[str]) -> set[str]:
        expanded = set()
        for excluded_glyph in excluded_glyphs:
            excluded_base = _base_name(excluded_glyph)
            expanded.update(base_to_variants.get(excluded_base, ()))
        return expanded

    def _emit_fwd_pairs(base_name: str):
        if base_name in fwd_pair_overrides:
            for variant_name, before_glyphs, not_after_glyphs in fwd_pair_overrides[base_name]:
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
                    effective_before = target_before if target_before is not None else expanded_before
                    effective_before_list = " ".join(sorted(effective_before)) if target_before is not None else before_list
                    for pi_guard, pi_before in partial_ignores:
                        lines.append(f"        ignore sub [{pi_guard}] {target}' [{pi_before}];")
                    if guard_list:
                        lines.append(f"        ignore sub [{guard_list}] {target}' [{effective_before_list}];")
                    if expanded_not_after:
                        not_after_list = " ".join(sorted(expanded_not_after))
                        lines.append(f"        ignore sub [{not_after_list}] {target}' [{effective_before_list}];")
                    for terminal in sorted(effective_before & terminal_exit_only):
                        lines.append(f"        ignore sub {target}' {terminal};")
                    lines.append(f"        sub {target}' [{effective_before_list}] by {actual_variant};")
                lines.append(f"    }} calt_fwd_pair_{safe};")

    def _emit_fwd_general(base_name: str):
        if base_name in fwd_replacements:
            variants = fwd_replacements[base_name]
            exclusions = fwd_exclusions.get(base_name, {})
            lookup_name = f"calt_fwd_{base_name}"
            lines.append("")
            lines.append(f"    lookup {lookup_name} {{")
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
                excluded = _expand_exclusions(exclusions.get(exit_y, []))
                for excluded_glyph in sorted(excluded):
                    lines.append(f"        ignore sub {base_name}' {excluded_glyph};")
                lines.append(f"        sub {base_name}' {cls} by {variant_name};")
            if base_name in fwd_preferred_lookahead:
                for variant_name, exit_y, sibling_y in fwd_preferred_lookahead[base_name]:
                    if exit_y in entry_classes and sibling_y in entry_exclusive and entry_exclusive[sibling_y]:
                        lines.append(
                            f"        sub {base_name}' @entry_y{exit_y} @entry_only_y{sibling_y} by {variant_name};"
                        )
            noentry_name = f"{base_name}.noentry"
            if noentry_name in glyph_names:
                for exit_y in sorted(variants.keys(), reverse=True):
                    variant_name = variants[exit_y]
                    if exit_y not in entry_classes:
                        continue
                    use_excl = (base_name, exit_y) in fwd_use_exclusive
                    if use_excl and (exit_y not in entry_exclusive or not entry_exclusive[exit_y]):
                        continue
                    cls = f"@entry_only_y{exit_y}" if use_excl else f"@entry_y{exit_y}"
                    lines.append(f"        sub {noentry_name}' {cls} by {variant_name};")
            lines.append(f"    }} {lookup_name};")

    def _emit_fwd(base_name: str):
        _emit_fwd_pairs(base_name)
        _emit_fwd_general(base_name)

    def _emit_bk_pairs(base_name: str):
        if base_name in pair_overrides:
            for variant_name, after_glyphs in pair_overrides[base_name]:
                expanded_after = _expand_all_variants(after_glyphs)
                variant_meta = _meta(variant_name)
                if variant_meta.entry_restriction_y is not None:
                    entry_y = variant_meta.entry_restriction_y
                    expanded_after = {
                        name for name in expanded_after if _can_match_pair_exit(name, entry_y)
                    }
                after_list = " ".join(sorted(expanded_after))
                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_pair_{safe} {{")
                not_before = list(variant_meta.not_before)
                if not_before:
                    resolved = resolve_known_glyph_names(not_before, glyph_names)
                    for not_before_glyph in sorted(_expand_exclusions(resolved)):
                        lines.append(f"        ignore sub [{after_list}] {base_name}' {not_before_glyph};")
                for terminal in sorted(expanded_after & terminal_entry_only):
                    lines.append(f"        ignore sub {terminal} {base_name}';")
                lines.append(f"        sub [{after_list}] {base_name}' by {variant_name};")
                lines.append(f"    }} calt_pair_{safe};")

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
                    for excluded_glyph in sorted(_expand_exclusions(exclusions.get(entry_y, []))):
                        lines.append(f"        ignore sub {excluded_glyph} {base_name}';")
                    lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
            lines.append(f"    }} {lookup_name};")

    def _emit_bk_cycle(bases: list[str]):
        lines.append("")
        lines.append("    lookup calt_cycle {")
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            exclusions = bk_exclusions.get(base_name, {})
            for entry_y in sorted(variants.keys()):
                variant_name = variants[entry_y]
                if entry_y in exit_classes:
                    excluded = set(_expand_exclusions(exclusions.get(entry_y, [])))
                    if excluded:
                        filtered = sorted(exit_classes[entry_y] - excluded)
                        if filtered:
                            member_list = " ".join(filtered)
                            lines.append(f"        sub [{member_list}] {base_name}' by {variant_name};")
                    else:
                        lines.append(f"        sub @exit_y{entry_y} {base_name}' by {variant_name};")
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
                    excluded = _expand_exclusions(exclusions.get(exit_y, []))
                    for excluded_glyph in sorted(excluded):
                        lines.append(f"        ignore sub {base_name}' {excluded_glyph};")
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
            lines.append(f"    lookup calt_post_upgrade_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                for excluded_glyph in sorted(_expand_exclusions(exclusions.get(entry_y, []))):
                    lines.append(f"        ignore sub {excluded_glyph} {base_name}';")
                lines.append(f"        sub @exit_y{entry_y} {base_name}' by {relevant[entry_y]};")
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

        for variant_name, source_variants, entry_ys, not_before in reverse_only_upgrades:
            valid_entry_ys = [y for y in sorted(set(entry_ys)) if y in exit_classes]
            if not valid_entry_ys:
                continue
            safe = variant_name.replace(".", "_")
            lines.append("")
            lines.append(f"    lookup calt_reverse_upgrade_explicit_{safe} {{")
            if not_before:
                not_before_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                for source_variant in source_variants:
                    lines.append(f"        ignore sub {source_variant}' [{not_before_list}];")
            for entry_y in valid_entry_ys:
                for source_variant in source_variants:
                    lines.append(f"        sub @exit_y{entry_y} {source_variant}' by {variant_name};")
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
                    not_before = list(_meta(fwd_var).not_before)
                    if not_before:
                        resolved = resolve_known_glyph_names(not_before, glyph_names)
                        for not_before_glyph in sorted(_expand_exclusions(resolved)):
                            lines.append(f"        ignore sub {bk_var}' {not_before_glyph};")
                    lines.append(f"        sub {bk_var}' {cls} by {fwd_var};")
                    lines.append(f"    }} calt_fwd_override_{safe};")

    def _emit_block(bases: list[str], *, use_cycle: bool = False):
        for base_name in bases:
            if base_name not in early_pair_upgrade_bases:
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
        _emit_noentry_fwd_overrides(bases)
        if use_cycle:
            _emit_post_upgrade_bk(bases)
            _emit_post_override_bk(bases)
        for base_name in bases:
            if base_name in all_fwd_bases and base_name not in early_pair_upgrade_bases:
                if base_name in early_fwd_pairs:
                    _emit_fwd_general(base_name)
                else:
                    _emit_fwd(base_name)

    for base_name in fwd_only:
        if base_name in lig_fwd_bases:
            continue
        _emit_bk_pairs(base_name)
        _emit_fwd(base_name)

    for base_name in sorted(early_pair_upgrade_bases):
        _emit_bk_pairs(base_name)
        _emit_upgrades(base_name)
        _emit_fwd_general(base_name)

    pre_cycle: list[str] = []
    post_cycle: list[str] = []
    if cycle_bases:
        cycle_deps: set[str] = set()
        for cycle_base in cycle_bases:
            cycle_deps |= edges.get(cycle_base, set())
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

    if cycle_bases:
        cycle_list = sorted(cycle_bases)
        _emit_block(cycle_list, use_cycle=True)

    early_post = [base for base in post_cycle if base in early_fwd_pairs]
    late_post = [base for base in post_cycle if base not in early_fwd_pairs]
    _emit_block(early_post)
    _emit_block(late_post)

    if cycle_bases:
        pair_only_new_exit_ys: set[int] = set()
        for pair_only_base in pair_only:
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
            lines.append(f"    lookup calt_post_pair_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                for excluded_glyph in sorted(_expand_exclusions(exclusions.get(entry_y, []))):
                    lines.append(f"        ignore sub {excluded_glyph} {cycle_base}';")
                lines.append(f"        sub @exit_y{entry_y} {cycle_base}' by {relevant[entry_y]};")
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
                for entry_y in sorted(variants.keys()):
                    bk_var = variants[entry_y]
                    combined = bk_var + ext_suffix
                    if combined not in glyph_names:
                        continue
                    if entry_y not in exit_classes:
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

    if ligatures:
        from itertools import product

        lines.append("")
        lines.append("    lookup calt_liga {")
        for lig_name, components in sorted(ligatures):
            variant_sets: list[list[str]] = []
            for index, component in enumerate(components):
                variants: set[str] = set()
                if component in bk_replacements:
                    variants.update(bk_replacements[component].values())
                if component in pair_overrides:
                    for variant_name, _ in pair_overrides[component]:
                        variants.add(variant_name)
                if component in fwd_pair_overrides:
                    for variant_name, _, _ in fwd_pair_overrides[component]:
                        variants.add(variant_name)
                if index == 0 and component in fwd_replacements:
                    variants.update(fwd_replacements[component].values())
                variant_sets.append([component] + sorted(variants))
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
                exit_suffix = _meta(combo[-1]).extended_exit_suffix
                if exit_suffix:
                    ext_lig = actual_lig + ".exit-extended"
                    if ext_lig in glyph_names:
                        actual_lig = ext_lig
                lines.append(f"        sub {component_str} by {actual_lig};")
        lines.append("    } calt_liga;")

        lig_glyph_names = {lig_name for lig_name, _ in ligatures}
        post_liga_rules: list[tuple[str, str, list[str]]] = []
        for base_name in sorted(pair_overrides):
            for variant_name, after_glyphs in pair_overrides[base_name]:
                if any(glyph in lig_glyph_names for glyph in after_glyphs):
                    post_liga_rules.append((base_name, variant_name, after_glyphs))

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

        if post_liga_rules:
            lines.append("")
            lines.append("    lookup calt_post_liga {")
            for base_name, variant_name, after_glyphs in post_liga_rules:
                after_list = " ".join(sorted(after_glyphs))
                targets = {base_name}
                if base_name in bk_replacements:
                    targets.update(bk_replacements[base_name].values())
                for target in sorted(targets):
                    lines.append(f"        sub [{after_list}] {target}' by {variant_name};")
            lines.append("    } calt_post_liga;")

        for base_name in sorted(lig_fwd_bases):
            _emit_fwd(base_name)

    lines.append("} calt;")
    return "\n".join(lines)


def emit_quikscript_curs(
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


def emit_quikscript_ss(glyph_meta: dict) -> str | None:
    from collections import defaultdict

    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name, meta in glyph_meta.items():
        if meta.revert_feature:
            groups[meta.revert_feature].append((name, meta.base_name))

    if not groups:
        return None

    lines = []
    for feature_tag in sorted(groups):
        lines.append(f"feature {feature_tag} {{")
        for variant, base in sorted(groups[feature_tag]):
            lines.append(f"    sub {variant} by {base};")
        lines.append(f"}} {feature_tag};")

    return "\n".join(lines)


def emit_quikscript_ss_gate(plan: JoinPlan) -> str | None:
    from collections import defaultdict

    if not plan.gated_pair_overrides and not plan.gated_fwd_pair_overrides:
        return None

    glyph_meta = plan.glyph_meta
    glyph_names = plan.glyph_names

    def _can_exit_at(name: str, y: int) -> bool:
        meta = glyph_meta[name]
        if y in meta.exit_ys:
            return True
        if meta.exit:
            return False
        return any(
            y in glyph_meta[c].exit_ys
            for c in plan.base_to_variants.get(meta.base_name, ())
        )

    bk_features: dict[str, list[tuple[str, str, list[str]]]] = defaultdict(list)
    for base_name, overrides in plan.gated_pair_overrides.items():
        for variant_name, after_glyphs, feature_tag in overrides:
            expanded = set()
            for glyph in after_glyphs:
                base = glyph_meta[glyph].base_name if glyph in glyph_meta else glyph
                expanded.update(plan.base_to_variants.get(base, ()))

            variant_meta = glyph_meta[variant_name]
            if variant_meta.entry_restriction_y is not None:
                ey = variant_meta.entry_restriction_y
                expanded = {g for g in expanded if _can_exit_at(g, ey)}

            for terminal in sorted(expanded & plan.terminal_entry_only):
                expanded.discard(terminal)

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
        for base_name, variant_name, after_list in sorted(bk_features.get(tag, [])):
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
            lines.append(
                f"        sub [{' '.join(after_list)}] {base_name}' by {variant_name};"
            )
            lines.append(f"    }} {tag}_{safe};")
        for base_name, variant_name, before_list, not_after_list, targets, variant_entry_ys in sorted(fwd_features.get(tag, [])):
            safe = variant_name.replace(".", "_")
            lines.append(f"    lookup {tag}_{safe} {{")
            for target in targets:
                target_meta = glyph_meta[target]
                if variant_entry_ys is not None and target_meta.entry:
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
                if not_after_list:
                    lines.append(
                        f"        ignore sub [{' '.join(not_after_list)}] {target}' [{' '.join(before_list)}];"
                    )
                lines.append(
                    f"        sub {target}' [{' '.join(before_list)}] by {actual_variant};"
                )
            lines.append(f"    }} {tag}_{safe};")
        lines.append(f"}} {tag};")

    return "\n".join(lines)


__all__ = [
    "emit_quikscript_calt",
    "emit_quikscript_curs",
    "emit_quikscript_ss",
    "emit_quikscript_ss_gate",
]
