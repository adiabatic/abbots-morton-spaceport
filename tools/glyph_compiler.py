from dataclasses import dataclass, field
from typing import Any

from quikscript_ir import (
    GlyphData,
    GlyphDef,
    JoinGlyph,
    _is_contextual_variant,
    build_join_glyphs,
    compile_quikscript_ir,
    flatten_join_glyphs,
    get_base_glyph_name,
)
from quikscript_join_analysis import validate_join_consistency, warn_join_contract_issues

_JOIN_REF_KEYS = (
    "calt_after",
    "calt_before",
    "calt_not_after",
    "calt_not_before",
    "noentry_after",
    "reverse_upgrade_from",
)


@dataclass(slots=True)
class CompiledGlyphSet:
    legacy_glyphs: dict[str, GlyphDef | None]
    join_glyphs: dict[str, JoinGlyph]
    glyph_meta: dict[str, JoinGlyph]
    _glyph_definitions: dict[str, GlyphDef] | None = field(default=None, init=False, repr=False)

    @property
    def glyph_definitions(self) -> dict[str, GlyphDef]:
        if self._glyph_definitions is None:
            glyph_definitions: dict[str, GlyphDef] = {
                k: v for k, v in self.legacy_glyphs.items() if v is not None
            }
            glyph_definitions.update(flatten_join_glyphs(self.join_glyphs))
            self._glyph_definitions = glyph_definitions
        return self._glyph_definitions


def is_proportional_glyph(glyph_name: str) -> bool:
    return glyph_name.endswith(".prop") or ".prop." in glyph_name or ".fina" in glyph_name


def prepare_proportional_glyphs(glyphs_def: dict[str, GlyphDef | None]) -> dict[str, GlyphDef | None]:
    rename_map: dict[str, str] = {}
    prop_base_names = set()
    for glyph_name in glyphs_def:
        if is_proportional_glyph(glyph_name):
            base_name = get_base_glyph_name(glyph_name)
            prop_base_names.add(base_name)
            rename_map[glyph_name] = base_name

    def _rename_refs(glyph_def: GlyphDef | None) -> GlyphDef | None:
        if not glyph_def or not rename_map:
            return glyph_def

        scalar_keys = ("base",)
        changed = False

        for key in _JOIN_REF_KEYS:
            values = glyph_def.get(key)
            if not values:
                continue
            renamed = [rename_map.get(value, value) for value in values]
            if renamed == list(values):
                continue
            if not changed:
                glyph_def = dict(glyph_def)
                changed = True
            glyph_def[key] = renamed

        for key in (
            "extend_entry_after",
            "extend_exit_before",
            "contract_entry_after",
            "contract_exit_before",
        ):
            spec = glyph_def.get(key)
            if not spec:
                continue
            spec_groups = spec if isinstance(spec, list) else [spec]
            renamed_groups: list[dict[str, Any]] = []
            group_changed = False
            for group in spec_groups:
                targets = group["targets"]
                renamed = [rename_map.get(value, value) for value in targets]
                if renamed != list(targets):
                    group_changed = True
                renamed_groups.append({"by": group["by"], "targets": renamed})
            if not group_changed:
                continue
            if not changed:
                glyph_def = dict(glyph_def)
                changed = True
            glyph_def[key] = (
                renamed_groups if isinstance(spec, list) else renamed_groups[0]
            )

        for key in scalar_keys:
            value = glyph_def.get(key)
            if not value or value not in rename_map:
                continue
            if not changed:
                glyph_def = dict(glyph_def)
                changed = True
            glyph_def[key] = rename_map[value]

        return glyph_def

    new_glyphs = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            new_glyphs[get_base_glyph_name(glyph_name)] = _rename_refs(glyph_def)
        elif glyph_name not in prop_base_names:
            new_glyphs[glyph_name] = _rename_refs(glyph_def)

    return new_glyphs


def _compile_legacy_glyphs(glyph_data: GlyphData, variant: str) -> dict[str, GlyphDef | None]:
    is_proportional = variant != "mono"
    is_senior = variant == "senior"

    legacy_glyphs = {
        name: (dict(glyph_def) if glyph_def is not None else None)
        for name, glyph_def in glyph_data.get("glyphs", {}).items()
        if ".unused" not in name
    }

    if not is_senior:
        legacy_glyphs = {
            name: glyph_def
            for name, glyph_def in legacy_glyphs.items()
            if not _is_contextual_variant(name)
        }

    if is_proportional:
        legacy_glyphs = prepare_proportional_glyphs(legacy_glyphs)

    return legacy_glyphs


def _validate_compiled_glyph_references(
    legacy_glyphs: dict[str, GlyphDef | None],
    join_glyphs: dict[str, JoinGlyph],
) -> None:
    all_glyph_names = set(legacy_glyphs) | set(join_glyphs)

    def _validate_refs(glyph_name: str, key: str, values: tuple[str, ...] | list[str]) -> None:
        for value in values:
            if value not in all_glyph_names:
                raise ValueError(
                    f"Glyph {glyph_name!r} {key} refers to missing glyph {value!r}"
                )

    for glyph_name, glyph_def in legacy_glyphs.items():
        if glyph_def is None:
            continue
        for key in (*_JOIN_REF_KEYS, "preferred_over"):
            _validate_refs(glyph_name, key, glyph_def.get(key, ()))
        for key in (
            "extend_entry_after",
            "extend_exit_before",
            "contract_entry_after",
            "contract_exit_before",
        ):
            spec = glyph_def.get(key)
            if not spec:
                continue
            spec_groups = spec if isinstance(spec, list) else [spec]
            for group in spec_groups:
                _validate_refs(glyph_name, key, group.get("targets", ()))

    for glyph_name, join_glyph in join_glyphs.items():
        _validate_refs(glyph_name, "calt_after", join_glyph.after)
        _validate_refs(glyph_name, "calt_before", join_glyph.before)
        _validate_refs(glyph_name, "calt_not_after", join_glyph.not_after)
        _validate_refs(glyph_name, "calt_not_before", join_glyph.not_before)
        if join_glyph.extend_entry_after is not None:
            _validate_refs(
                glyph_name, "extend_entry_after", join_glyph.extend_entry_after.targets
            )
        if join_glyph.extend_exit_before is not None:
            _validate_refs(
                glyph_name, "extend_exit_before", join_glyph.extend_exit_before.targets
            )
        if join_glyph.contract_entry_after is not None:
            _validate_refs(
                glyph_name, "contract_entry_after", join_glyph.contract_entry_after.targets
            )
        if join_glyph.contract_exit_before is not None:
            _validate_refs(
                glyph_name, "contract_exit_before", join_glyph.contract_exit_before.targets
            )
        _validate_refs(glyph_name, "noentry_after", join_glyph.noentry_after)
        _validate_refs(
            glyph_name,
            "reverse_upgrade_from",
            join_glyph.reverse_upgrade_from,
        )
        _validate_refs(glyph_name, "preferred_over", join_glyph.preferred_over)


def _validate_extensions_reach_targets(
    join_glyphs: dict[str, JoinGlyph],
) -> None:
    glyph_to_family: dict[str, str] = {}
    for g in join_glyphs.values():
        if g.family:
            glyph_to_family[g.name] = g.family

    def _candidate_has_entry_at(candidate: JoinGlyph, y: int) -> bool:
        return any(a[1] == y for a in (*candidate.entry, *candidate.entry_curs_only))

    def _candidate_has_exit_at(candidate: JoinGlyph, y: int) -> bool:
        return any(a[1] == y for a in candidate.exit)

    def _after_includes_family(candidate: JoinGlyph, family: str) -> bool:
        check = candidate
        if candidate.is_entry_variant and candidate.generated_from:
            source = join_glyphs.get(candidate.generated_from)
            if source:
                check = source
        if not check.after:
            return True
        return any(glyph_to_family.get(n) == family for n in check.after)

    errors: list[str] = []

    for g in join_glyphs.values():
        if not g.exit or not g.family:
            continue
        exit_y = g.exit[0][1]

        for feature_tag, target_names in g.extend_exit_before_gated:
            for target_name in target_names:
                target = join_glyphs.get(target_name)
                if not target or not target.family:
                    continue
                has_match = any(
                    c.family == target.family
                    and _candidate_has_entry_at(c, exit_y)
                    and c.gate_feature == feature_tag
                    and _after_includes_family(c, g.family)
                    for c in join_glyphs.values()
                )
                if not has_match:
                    errors.append(
                        f"{g.name} extends exit (y={exit_y}) toward "
                        f"{target.family} behind {feature_tag}, but "
                        f"{target.family} has no {feature_tag}-gated "
                        f"entry at y={exit_y} reachable after {g.family}"
                    )

        exit_targets = g.extend_exit_before.targets if g.extend_exit_before else ()
        for target_name in exit_targets:
            target = join_glyphs.get(target_name)
            if not target or not target.family:
                continue
            has_match = any(
                c.family == target.family
                and _candidate_has_entry_at(c, exit_y)
                for c in join_glyphs.values()
            )
            if not has_match:
                errors.append(
                    f"{g.name} extends exit (y={exit_y}) toward "
                    f"{target.family}, but {target.family} has no "
                    f"entry at y={exit_y}"
                )

    for g in join_glyphs.values():
        entry_anchors = (*g.entry, *g.entry_curs_only)
        if not entry_anchors:
            continue
        entry_y = entry_anchors[0][1]

        entry_targets = g.extend_entry_after.targets if g.extend_entry_after else ()
        for target_name in entry_targets:
            target = join_glyphs.get(target_name)
            if not target or not target.family:
                continue
            has_match = any(
                c.family == target.family
                and _candidate_has_exit_at(c, entry_y)
                for c in join_glyphs.values()
            )
            if not has_match:
                errors.append(
                    f"{g.name} extends entry (y={entry_y}) toward "
                    f"{target.family}, but {target.family} has no "
                    f"exit at y={entry_y}"
                )

    if errors:
        raise ValueError(
            "Extension-target anchor mismatches:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


def compile_glyph_set(glyph_data: GlyphData, variant: str) -> CompiledGlyphSet:
    legacy_glyphs = _compile_legacy_glyphs(glyph_data, variant)
    join_glyphs, _ = compile_quikscript_ir(glyph_data, variant)

    if variant == "senior":
        _validate_compiled_glyph_references(legacy_glyphs, join_glyphs)
        _validate_extensions_reach_targets(join_glyphs)
        validate_join_consistency(join_glyphs)
        warn_join_contract_issues(join_glyphs)

    glyph_meta = build_join_glyphs(legacy_glyphs)
    glyph_meta.update(join_glyphs)

    return CompiledGlyphSet(
        legacy_glyphs=legacy_glyphs,
        join_glyphs=join_glyphs,
        glyph_meta=glyph_meta,
    )


__all__ = [
    "CompiledGlyphSet",
    "compile_glyph_set",
    "is_proportional_glyph",
    "prepare_proportional_glyphs",
]
