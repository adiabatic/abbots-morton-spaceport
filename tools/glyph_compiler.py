from dataclasses import dataclass, field

from quikscript_ir import (
    JoinGlyph,
    _is_contextual_variant,
    build_join_glyphs,
    compile_quikscript_ir,
    flatten_join_glyphs,
    get_base_glyph_name,
)


@dataclass(slots=True)
class CompiledGlyphSet:
    legacy_glyphs: dict[str, dict | None]
    join_glyphs: dict[str, JoinGlyph]
    glyph_meta: dict[str, JoinGlyph]
    _glyph_definitions: dict[str, dict] | None = field(default=None, init=False, repr=False)

    @property
    def glyph_definitions(self) -> dict[str, dict]:
        if self._glyph_definitions is None:
            glyph_definitions = dict(self.legacy_glyphs)
            glyph_definitions.update(flatten_join_glyphs(self.join_glyphs))
            self._glyph_definitions = glyph_definitions
        return self._glyph_definitions


def is_proportional_glyph(glyph_name: str) -> bool:
    return glyph_name.endswith(".prop") or ".prop." in glyph_name or ".fina" in glyph_name


def prepare_proportional_glyphs(glyphs_def: dict) -> dict:
    rename_map: dict[str, str] = {}
    prop_base_names = set()
    for glyph_name in glyphs_def:
        if is_proportional_glyph(glyph_name):
            base_name = get_base_glyph_name(glyph_name)
            prop_base_names.add(base_name)
            rename_map[glyph_name] = base_name

    def _rename_refs(glyph_def: dict | None) -> dict | None:
        if not glyph_def or not rename_map:
            return glyph_def

        list_keys = (
            "calt_after",
            "calt_before",
            "calt_not_after",
            "calt_not_before",
            "extend_entry_after",
            "extend_exit_before",
            "doubly_extend_entry_after",
            "doubly_extend_exit_before",
            "noentry_after",
            "reverse_upgrade_from",
        )
        scalar_keys = ("base",)
        changed = False

        for key in list_keys:
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


def _prepare_legacy_glyph_def(name: str, glyph_def: dict | None) -> dict | None:
    if glyph_def is None:
        return None

    prepared = dict(glyph_def)
    prepared.setdefault("_base_name", get_base_glyph_name(name).split(".")[0])
    prepared.setdefault("_contextual", _is_contextual_variant(name))
    return prepared


def _compile_legacy_glyphs(glyph_data: dict, variant: str) -> dict[str, dict | None]:
    is_proportional = variant != "mono"
    is_senior = variant == "senior"

    legacy_glyphs = {
        name: _prepare_legacy_glyph_def(name, glyph_def)
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
    legacy_glyphs: dict[str, dict | None],
    join_glyphs: dict[str, JoinGlyph],
) -> None:
    list_keys = (
        "calt_after",
        "calt_before",
        "calt_not_after",
        "calt_not_before",
        "extend_entry_after",
        "extend_exit_before",
        "doubly_extend_entry_after",
        "doubly_extend_exit_before",
        "noentry_after",
        "reverse_upgrade_from",
        "preferred_over",
    )

    all_glyph_names = set(legacy_glyphs) | set(join_glyphs)

    def _validate_refs(glyph_name: str, key: str, values) -> None:
        for value in values:
            if value not in all_glyph_names:
                raise ValueError(
                    f"Glyph {glyph_name!r} {key} refers to missing glyph {value!r}"
                )

    for glyph_name, glyph_def in legacy_glyphs.items():
        if glyph_def is None:
            continue
        for key in list_keys:
            _validate_refs(glyph_name, key, glyph_def.get(key, ()))

    for glyph_name, join_glyph in join_glyphs.items():
        _validate_refs(glyph_name, "calt_after", join_glyph.after)
        _validate_refs(glyph_name, "calt_before", join_glyph.before)
        _validate_refs(glyph_name, "calt_not_after", join_glyph.not_after)
        _validate_refs(glyph_name, "calt_not_before", join_glyph.not_before)
        _validate_refs(glyph_name, "extend_entry_after", join_glyph.extend_entry_after)
        _validate_refs(glyph_name, "extend_exit_before", join_glyph.extend_exit_before)
        _validate_refs(
            glyph_name,
            "doubly_extend_entry_after",
            join_glyph.doubly_extend_entry_after,
        )
        _validate_refs(
            glyph_name,
            "doubly_extend_exit_before",
            join_glyph.doubly_extend_exit_before,
        )
        _validate_refs(glyph_name, "noentry_after", join_glyph.noentry_after)
        _validate_refs(
            glyph_name,
            "reverse_upgrade_from",
            join_glyph.reverse_upgrade_from,
        )
        _validate_refs(glyph_name, "preferred_over", join_glyph.preferred_over)


def compile_glyph_set(glyph_data: dict, variant: str) -> CompiledGlyphSet:
    legacy_glyphs = _compile_legacy_glyphs(glyph_data, variant)
    join_glyphs, _ = compile_quikscript_ir(glyph_data, variant)

    if variant == "senior":
        _validate_compiled_glyph_references(legacy_glyphs, join_glyphs)

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
