#!/usr/bin/env python3
"""
Build a pixel font from bitmap glyph definitions.
Uses fonttools FontBuilder to create OTF output.

Usage:
    uv run python tools/build_font.py <glyph_data.yaml|glyph_data/> [output_dir]

    The first argument can be a single YAML file or a directory of YAML files.
    When a directory is given, all *.yaml files are loaded and merged.

Outputs:
    output_dir/AbbotsMortonSpaceportMono.otf        - Monospace font
    output_dir/AbbotsMortonSpaceportSansJunior.otf  - Proportional font (no cursive/calt)
    output_dir/AbbotsMortonSpaceportSansSenior.otf  - Proportional font (with cursive/calt)
"""

import sys
from collections import deque
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re

import yaml

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import newTable
from fontTools.ttLib.tables._c_m_a_p import cmap_format_14
from quikscript_ir import CompiledGlyphMeta
from quikscript_planner import CaltPlan


def load_postscript_glyph_names() -> dict:
    """Load PostScript glyph name to Unicode codepoint mapping from YAML."""
    path = Path(__file__).parent.parent / "postscript_glyph_names.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_glyph_data(path: Path) -> dict:
    """Load glyph definitions from a YAML file or directory of YAML files."""
    if path.is_dir():
        metadata = {}
        glyphs = {}
        glyph_families = {}
        context_sets = {}
        kerning_defs = {}
        for yaml_file in sorted(path.glob("*.yaml")):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            if data and "metadata" in data:
                metadata = data["metadata"]
            if data and "glyphs" in data:
                glyphs.update(data["glyphs"])
            if data and "glyph_families" in data:
                glyph_families.update(data["glyph_families"])
            if data and "context_sets" in data:
                context_sets.update(data["context_sets"])
            if data and "kerning" in data:
                kerning_defs.update(data["kerning"])
        return {
            "metadata": metadata,
            "glyphs": glyphs,
            "glyph_families": glyph_families,
            "context_sets": context_sets,
            "kerning": kerning_defs,
        }
    else:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return {
            "metadata": data.get("metadata", {}),
            "glyphs": data.get("glyphs", {}),
            "glyph_families": data.get("glyph_families", {}),
            "context_sets": data.get("context_sets", {}),
            "kerning": data.get("kerning", {}),
        }


def _resolve_codepoint(glyph_name: str, postscript_names: dict) -> int | None:
    if len(glyph_name) == 1:
        return ord(glyph_name)
    if glyph_name.startswith("uni") and len(glyph_name) == 7:
        try:
            return int(glyph_name[3:], 16)
        except ValueError:
            return None
    if glyph_name.startswith("u") and not glyph_name.startswith("uni") and len(glyph_name) == 6:
        try:
            cp = int(glyph_name[1:], 16)
            return cp if cp > 0xFFFF else None
        except ValueError:
            return None
    return postscript_names.get(glyph_name)


def build_cmap14(variation_sequences: dict, glyphs_def: dict, name_to_codepoint: dict):
    """Build a cmap format 14 subtable for Unicode Variation Sequences."""
    if not variation_sequences:
        return None

    uvsDict = {}
    for vs_cp, mappings in variation_sequences.items():
        entries = []
        for base_name, target_name in mappings.items():
            base_cp = name_to_codepoint.get(base_name)
            if base_cp is None:
                continue
            resolved = target_name
            if resolved not in glyphs_def:
                resolved = get_base_glyph_name(resolved)
            if resolved not in glyphs_def:
                continue
            entries.append((base_cp, resolved))
        if entries:
            uvsDict[vs_cp] = entries

    if not uvsDict:
        return None

    subtable = cmap_format_14(14)
    subtable.platformID = 0
    subtable.platEncID = 5
    subtable.language = 0
    subtable.cmap = {}
    subtable.uvsDict = uvsDict
    return subtable


def is_proportional_glyph(glyph_name: str) -> bool:
    """Check if a glyph is a proportional variant."""
    return glyph_name.endswith(".prop") or ".prop." in glyph_name or ".fina" in glyph_name


def get_base_glyph_name(prop_glyph_name: str) -> str:
    """Get the base glyph name from a proportional glyph name.

    Strips .prop from the end or middle of the name:
      qsPea.prop       → qsPea
      qsFee_qsMay.prop → qsFee_qsMay
      U.prop.narrow    → U.narrow
    """
    if prop_glyph_name.endswith(".prop"):
        return prop_glyph_name[:-5]
    if ".prop." in prop_glyph_name:
        return prop_glyph_name.replace(".prop.", ".", 1)
    return prop_glyph_name


def prepare_proportional_glyphs(glyphs_def: dict) -> dict:
    """
    Prepare glyph definitions for the proportional font variant.

    For the proportional font:
    - .prop glyphs are renamed to their base names (e.g., qsPea.prop → qsPea)
    - Base glyphs that have .prop variants are excluded
    - Glyphs without .prop variants remain unchanged
    - Glyph name references inside definitions are updated accordingly
    """
    # Build rename map: old .prop name → new base name
    rename_map: dict[str, str] = {}
    prop_base_names = set()
    for glyph_name in glyphs_def.keys():
        if is_proportional_glyph(glyph_name):
            base_name = get_base_glyph_name(glyph_name)
            prop_base_names.add(base_name)
            rename_map[glyph_name] = base_name

    def _rename_refs(glyph_def: dict | None) -> dict | None:
        if not glyph_def or not rename_map:
            return glyph_def
        list_keys = (
            "calt_after", "calt_before", "calt_not_after", "calt_not_before",
            "extend_entry_after", "extend_exit_before",
            "doubly_extend_entry_after", "doubly_extend_exit_before",
            "noentry_after", "reverse_upgrade_from",
        )
        scalar_keys = ("base",)
        changed = False
        for key in list_keys:
            val = glyph_def.get(key)
            if not val:
                continue
            new_val = [rename_map.get(g, g) for g in val]
            if new_val != list(val):
                if not changed:
                    glyph_def = dict(glyph_def)
                    changed = True
                glyph_def[key] = new_val
        for key in scalar_keys:
            val = glyph_def.get(key)
            if val and val in rename_map:
                if not changed:
                    glyph_def = dict(glyph_def)
                    changed = True
                glyph_def[key] = rename_map[val]
        return glyph_def

    # Build new glyph dict
    new_glyphs = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            # Rename .prop glyph to its base name
            base_name = get_base_glyph_name(glyph_name)
            new_glyphs[base_name] = _rename_refs(glyph_def)
        elif glyph_name in prop_base_names:
            # Skip base glyphs that have .prop variants
            continue
        else:
            # Keep glyphs without .prop variants unchanged
            new_glyphs[glyph_name] = _rename_refs(glyph_def)

    return new_glyphs


def _merge_family_records(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in {"anchors", "select", "derive"}:
            merged.setdefault(key, {})
            for nested_key, nested_value in value.items():
                if nested_value is None:
                    merged[key].pop(nested_key, None)
                else:
                    merged[key][nested_key] = deepcopy(nested_value)
            if not merged[key]:
                merged.pop(key, None)
        elif key in {"traits", "modifiers"}:
            merged[key] = list(dict.fromkeys([*merged.get(key, []), *value]))
        elif value is None:
            merged.pop(key, None)
        else:
            merged[key] = deepcopy(value)
    return merged


def _resolve_family_record(
    family_name: str,
    family_def: dict,
    record_name: str,
    cache: dict[str, dict],
    stack: list[str],
) -> dict:
    if record_name in cache:
        return cache[record_name]
    if record_name in stack:
        cycle = " -> ".join([*stack, record_name])
        raise ValueError(f"Cyclic form inheritance in {family_name}: {cycle}")

    records = {}
    if family_def.get("mono"):
        records["mono"] = family_def["mono"]
    if family_def.get("prop"):
        records["prop"] = family_def["prop"]
    records.update(family_def.get("forms", {}))

    raw = records.get(record_name)
    if raw is None:
        raise ValueError(f"Unknown form '{record_name}' in glyph family '{family_name}'")

    stack.append(record_name)
    resolved: dict = {}
    inherits = raw.get("inherits")
    if inherits:
        parents = [inherits] if isinstance(inherits, str) else inherits
        for parent_name in parents:
            parent = _resolve_family_record(family_name, family_def, parent_name, cache, stack)
            resolved = _merge_family_records(resolved, parent)

    own = {k: v for k, v in raw.items() if k != "inherits"}
    resolved = _merge_family_records(resolved, own)

    shape_name = resolved.pop("shape", None)
    if shape_name:
        if shape_name in family_def.get("shapes", {}):
            shape_def = family_def["shapes"][shape_name]
        elif shape_name in {"mono", "prop"} and family_def.get(shape_name):
            source_record = _resolve_family_record(
                family_name,
                family_def,
                shape_name,
                cache,
                stack,
            )
            shape_def = {
                key: deepcopy(source_record[key])
                for key in ("bitmap", "y_offset", "advance_width")
                if key in source_record
            }
        else:
            raise ValueError(f"Unknown shape '{shape_name}' in glyph family '{family_name}'")
        resolved = _merge_family_records(shape_def, resolved)

    cache[record_name] = resolved
    stack.pop()
    return resolved


def _is_contextual_family_form(form_def: dict, *, is_base_record: bool = False) -> bool:
    contextual = form_def.get("contextual")
    if contextual is not None:
        return bool(contextual)
    if is_base_record:
        return False
    if form_def.get("traits"):
        return True
    if form_def.get("select") or form_def.get("derive"):
        return True
    anchors = form_def.get("anchors", {})
    if any(key in anchors for key in ("entry", "entry_curs_only", "exit")):
        return True
    return any(key in form_def for key in ("shape", "bitmap"))


_SOURCE_FAMILY_TRAITS = frozenset({"alt", "half"})
_ENTRY_EXIT_MODIFIER_RE = re.compile(
    r"^(?:entry|exit)-[a-z0-9]+(?:-[a-z0-9]+)*(?:-at-[a-z0-9]+)?$"
)
_BEFORE_AFTER_MODIFIER_RE = re.compile(r"^(?:before|after)-[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_source_trait(
    trait: str,
    *,
    family_name: str,
    context: str,
) -> None:
    if not isinstance(trait, str) or not trait:
        raise ValueError(f"{family_name} {context} has an invalid trait {trait!r}")
    if trait not in _SOURCE_FAMILY_TRAITS:
        raise ValueError(
            f"{family_name} {context} uses unsupported trait {trait!r}; "
            f"expected one of {sorted(_SOURCE_FAMILY_TRAITS)!r}"
        )


def _validate_source_modifier(
    modifier: str,
    *,
    family_name: str,
    context: str,
) -> None:
    if not isinstance(modifier, str) or not modifier:
        raise ValueError(f"{family_name} {context} has an invalid modifier {modifier!r}")
    if modifier in _SOURCE_FAMILY_TRAITS:
        raise ValueError(
            f"{family_name} {context} uses trait-like token {modifier!r} in modifiers; "
            "put it under traits instead"
        )
    if modifier in {"extended", "widebase", "reaches-way-back", "smaller-loop", "noentry"}:
        return
    if _ENTRY_EXIT_MODIFIER_RE.fullmatch(modifier):
        return
    if _BEFORE_AFTER_MODIFIER_RE.fullmatch(modifier):
        return
    raise ValueError(f"{family_name} {context} uses unsupported modifier {modifier!r}")


def _normalize_source_traits(
    raw_traits,
    *,
    family_name: str,
    context: str,
) -> tuple[str, ...]:
    if raw_traits is None:
        return ()
    if not isinstance(raw_traits, (list, tuple)):
        raise ValueError(f"{family_name} {context} traits must be a list")

    seen = set()
    traits = []
    for trait in raw_traits:
        _validate_source_trait(trait, family_name=family_name, context=context)
        if trait in seen:
            raise ValueError(f"{family_name} {context} repeats trait {trait!r}")
        seen.add(trait)
        traits.append(trait)
    return tuple(traits)


def _normalize_source_modifiers(
    raw_modifiers,
    *,
    family_name: str,
    context: str,
) -> tuple[str, ...]:
    if raw_modifiers is None:
        return ()
    if not isinstance(raw_modifiers, (list, tuple)):
        raise ValueError(f"{family_name} {context} modifiers must be a list")

    seen = set()
    modifiers = []
    for modifier in raw_modifiers:
        _validate_source_modifier(modifier, family_name=family_name, context=context)
        if modifier in seen:
            raise ValueError(f"{family_name} {context} repeats modifier {modifier!r}")
        seen.add(modifier)
        modifiers.append(modifier)
    return tuple(modifiers)


def _compiled_family_glyph_name(
    family_name: str,
    traits: tuple[str, ...] | list[str] = (),
    modifiers: tuple[str, ...] | list[str] = (),
) -> str:
    parts = [family_name, *traits, *modifiers]
    return ".".join(parts)


def _split_family_compiled_name(
    glyph_name: str,
    family_names: set[str],
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    normalized = get_base_glyph_name(glyph_name)
    family_name = None
    for candidate in sorted(family_names, key=len, reverse=True):
        if normalized == candidate or normalized.startswith(candidate + "."):
            family_name = candidate
            break
    if family_name is None:
        return None

    suffix = normalized[len(family_name):].removeprefix(".")
    if not suffix:
        return family_name, (), ()

    traits = []
    modifiers = []
    for token in suffix.split("."):
        if token in _SOURCE_FAMILY_TRAITS:
            traits.append(token)
        else:
            modifiers.append(token)
    return family_name, tuple(traits), tuple(modifiers)


def _resolve_family_selector_name(
    value,
    family_names: set[str],
    *,
    context_family: str,
    context_label: str,
    field_name: str,
) -> str:
    context = f"{context_label} {field_name}"
    if isinstance(value, str):
        resolved_family = _split_family_compiled_name(value, family_names)
        if resolved_family is not None:
            family_name, traits, modifiers = resolved_family
            return _compiled_family_glyph_name(family_name, traits, modifiers)
        return get_base_glyph_name(value)

    if not isinstance(value, dict):
        raise ValueError(f"{context_family} {context} must contain strings or selector mappings")

    unknown_keys = set(value) - {"family", "traits", "modifiers"}
    if unknown_keys:
        keys = ", ".join(sorted(unknown_keys))
        raise ValueError(f"{context_family} {context} uses unsupported selector keys: {keys}")

    target_family = value.get("family")
    if not isinstance(target_family, str) or not target_family:
        raise ValueError(f"{context_family} {context} selector must include a family name")
    if target_family not in family_names:
        raise ValueError(
            f"{context_family} {context} refers to unknown glyph family {target_family!r}"
        )

    traits = _normalize_source_traits(
        value.get("traits", ()),
        family_name=context_family,
        context=context,
    )
    modifiers = _normalize_source_modifiers(
        value.get("modifiers", ()),
        family_name=context_family,
        context=context,
    )
    return _compiled_family_glyph_name(target_family, traits, modifiers)


def _normalize_family_refs(
    values,
    family_names: set[str],
    *,
    context_sets: dict[str, list],
    context_family: str,
    context_label: str,
    field_name: str,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{context_family} {context_label} {field_name} must be a list")

    def _expand_value(value, stack: tuple[str, ...]) -> list[str]:
        if isinstance(value, dict) and "context_set" in value:
            if set(value) != {"context_set"}:
                extra = ", ".join(sorted(set(value) - {"context_set"}))
                raise ValueError(
                    f"{context_family} {context_label} {field_name} context_set refs "
                    f"cannot include extra keys: {extra}"
                )
            set_name = value["context_set"]
            if not isinstance(set_name, str) or not set_name:
                raise ValueError(
                    f"{context_family} {context_label} {field_name} uses an invalid "
                    "context_set name"
                )
            if set_name not in context_sets:
                raise ValueError(
                    f"{context_family} {context_label} {field_name} refers to unknown "
                    f"context_set {set_name!r}"
                )
            if set_name in stack:
                cycle = " -> ".join([*stack, set_name])
                raise ValueError(f"Cyclic context_set expansion in {context_family}: {cycle}")
            raw_values = context_sets[set_name]
            if not isinstance(raw_values, list):
                raise ValueError(f"context_set {set_name!r} must be a list")
            expanded = []
            for raw_value in raw_values:
                expanded.extend(_expand_value(raw_value, (*stack, set_name)))
            return expanded

        return [
            _resolve_family_selector_name(
                value,
                family_names,
                context_family=context_family,
                context_label=context_label,
                field_name=field_name,
            )
        ]

    expanded = []
    for value in values:
        expanded.extend(_expand_value(value, ()))
    return expanded


def _family_form_to_glyph_def(
    family_name: str,
    family_def: dict,
    form_def: dict,
    *,
    form_name: str | None = None,
    output_name: str,
    contextual: bool,
    family_names: set[str],
    context_sets: dict[str, list],
) -> dict:
    glyph_def: dict = {}

    if "bitmap" in form_def:
        glyph_def["bitmap"] = deepcopy(form_def["bitmap"])

    for key in (
        "y_offset",
        "advance_width",
        "kerning",
        "top_mark_y",
        "bottom_mark_y",
        "is_mark",
        "base_x_adjust",
        "base_y_adjust",
        "base",
        "top",
        "bottom",
    ):
        if key in form_def:
            glyph_def[key] = deepcopy(form_def[key])

    anchors = form_def.get("anchors", {})
    if "entry" in anchors:
        glyph_def["cursive_entry"] = deepcopy(anchors["entry"])
    if "entry_curs_only" in anchors:
        glyph_def["cursive_entry_curs_only"] = deepcopy(anchors["entry_curs_only"])
    if "exit" in anchors:
        glyph_def["cursive_exit"] = deepcopy(anchors["exit"])

    select = form_def.get("select", {})
    select_map = {
        "after": "calt_after",
        "before": "calt_before",
        "not_after": "calt_not_after",
        "not_before": "calt_not_before",
    }
    for source_key, glyph_key in select_map.items():
        if source_key in select:
            glyph_def[glyph_key] = _normalize_family_refs(
                select[source_key],
                family_names,
                context_sets=context_sets,
                context_family=family_name,
                context_label=f"form {form_name!r}" if form_name else "base record",
                field_name=source_key,
            )

    derive = form_def.get("derive", {})
    derive_map = {
        "extend_entry_after": "extend_entry_after",
        "extend_exit_before": "extend_exit_before",
        "doubly_extend_entry_after": "doubly_extend_entry_after",
        "doubly_extend_exit_before": "doubly_extend_exit_before",
        "noentry_after": "noentry_after",
        "reverse_upgrade_from": "reverse_upgrade_from",
        "preferred_over": "preferred_over",
    }
    for source_key, glyph_key in derive_map.items():
        if source_key in derive:
            glyph_def[glyph_key] = _normalize_family_refs(
                derive[source_key],
                family_names,
                context_sets=context_sets,
                context_family=family_name,
                context_label=f"form {form_name!r}" if form_name else "base record",
                field_name=source_key,
            )

    traits = _normalize_source_traits(
        form_def.get("traits", ()),
        family_name=family_name,
        context=f"form {form_name!r}" if form_name else "base record",
    )
    modifiers = _normalize_source_modifiers(
        form_def.get("modifiers", ()),
        family_name=family_name,
        context=f"form {form_name!r}" if form_name else "base record",
    )

    _stamp_compiled_glyph_seed(
        glyph_def,
        output_name=output_name,
        base_name=family_name,
        family_name=family_name,
        sequence=family_def.get("sequence"),
        traits=traits,
        contextual=contextual,
        modifiers=[*traits, *modifiers],
    )

    return glyph_def


def compile_glyph_families(
    glyph_families: dict,
    variant: str,
    context_sets: dict[str, list] | None = None,
) -> dict:
    if not glyph_families:
        return {}

    is_senior = variant == "senior"
    compiled: dict[str, dict] = {}
    family_names = set(glyph_families)
    context_sets = context_sets or {}

    for family_name, family_def in glyph_families.items():
        cache: dict[str, dict] = {}

        if variant == "mono":
            if family_def.get("mono"):
                compiled[family_name] = _family_form_to_glyph_def(
                    family_name,
                    family_def,
                    _resolve_family_record(family_name, family_def, "mono", cache, []),
                    output_name=family_name,
                    contextual=False,
                    family_names=family_names,
                    context_sets=context_sets,
                )
        else:
            base_record_name = "prop" if family_def.get("prop") else "mono"
            if family_def.get(base_record_name):
                compiled[family_name] = _family_form_to_glyph_def(
                    family_name,
                    family_def,
                    _resolve_family_record(family_name, family_def, base_record_name, cache, []),
                    output_name=family_name,
                    contextual=False,
                    family_names=family_names,
                    context_sets=context_sets,
                )

        for form_name in family_def.get("forms", {}):
            resolved = _resolve_family_record(family_name, family_def, form_name, cache, [])
            traits = _normalize_source_traits(
                resolved.get("traits", ()),
                family_name=family_name,
                context=f"form {form_name!r}",
            )
            modifiers = _normalize_source_modifiers(
                resolved.get("modifiers", ()),
                family_name=family_name,
                context=f"form {form_name!r}",
            )
            output_name = _compiled_family_glyph_name(family_name, traits, modifiers)
            if output_name == family_name:
                raise ValueError(
                    f"Glyph family '{family_name}' form '{form_name}' must declare traits or modifiers"
                )
            variants = set(resolved.get("variants", []))
            if variant == "mono":
                if "mono" not in variants:
                    continue
            elif variants and variant not in variants:
                continue
            elif not is_senior and _is_contextual_family_form(resolved):
                continue
            if output_name in compiled:
                raise ValueError(
                    f"Glyph family '{family_name}' form '{form_name}' duplicates compiled glyph "
                    f"name {output_name!r}"
                )
            compiled[output_name] = _family_form_to_glyph_def(
                family_name,
                family_def,
                resolved,
                form_name=form_name,
                output_name=output_name,
                contextual=_is_contextual_family_form(resolved),
                family_names=family_names,
                context_sets=context_sets,
            )

    return compiled


def _prepare_legacy_glyph_def(name: str, glyph_def: dict | None) -> dict | None:
    if glyph_def is None:
        return None

    prepared = dict(glyph_def)
    prepared.setdefault("_base_name", get_base_glyph_name(name).split(".")[0])
    prepared.setdefault("_contextual", _is_contextual_variant(name))
    return prepared


def compile_glyph_definitions(glyph_data: dict, variant: str) -> dict:
    """Compile source glyph data into the flat glyph map used by the build."""
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

    glyphs_def = dict(legacy_glyphs)
    glyphs_def.update(
        compile_glyph_families(
            glyph_data.get("glyph_families", {}),
            variant,
            context_sets=glyph_data.get("context_sets", {}),
        )
    )

    if is_senior:
        glyphs_def.update(generate_noentry_variants(glyphs_def))
        glyphs_def.update(generate_extended_entry_variants(glyphs_def))
        glyphs_def.update(generate_extended_exit_variants(glyphs_def))
        glyphs_def.update(generate_doubly_extended_entry_variants(glyphs_def))
        glyphs_def.update(generate_doubly_extended_exit_variants(glyphs_def))

    if is_senior:
        _validate_compiled_glyph_references(glyphs_def)
    return glyphs_def


def _validate_compiled_glyph_references(glyphs_def: dict) -> None:
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
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        for key in list_keys:
            values = glyph_def.get(key, ())
            for value in values:
                if value not in glyphs_def:
                    raise ValueError(
                        f"Glyph {glyph_name!r} {key} refers to missing glyph {value!r}"
                    )


def collect_kerning_groups(glyphs_def: dict) -> dict[str, list[str]]:
    """Build a mapping of {tag_name: [glyph_name, ...]} from kerning tags on glyphs."""
    groups = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        for tag in glyph_def.get("kerning", []):
            groups.setdefault(tag, []).append(glyph_name)
    return groups


def generate_kern_fea(
    kerning_defs: dict,
    kerning_groups: dict[str, list[str]],
    all_glyph_names: list[str],
    pixel_width: int,
) -> str:
    """Generate OpenType feature code for kern feature from kerning definitions."""
    lines = ["feature kern {"]
    for tag_name, definition in kerning_defs.items():
        if "left" in definition:
            left_glyphs = definition["left"]
        else:
            excluded = set(kerning_groups.get(tag_name, []))
            left_glyphs = [g for g in all_glyph_names if g not in excluded]
        if not left_glyphs:
            continue
        right_glyphs = definition["right"]
        value = definition["value"] * pixel_width
        left = " ".join(sorted(left_glyphs))
        right = " ".join(right_glyphs)
        lines.append(f"    lookup kern_{tag_name} {{")
        lines.append(f"        pos [{left}] [{right}] {value};")
        lines.append(f"    }} kern_{tag_name};")
    lines.append("} kern;")
    return "\n".join(lines)


def generate_ccmp_fea(glyphs_def: dict) -> str | None:
    """Generate OpenType feature code for dotted-base substitutions.

    Rewrites dotted lowercase bases to their dotless forms before top
    combining marks are attached.
    """
    top_marks = [
        glyph_name
        for glyph_name, glyph_def in glyphs_def.items()
        if glyph_def is not None
        and glyph_def.get("is_mark")
        and glyph_def.get("y_offset", 0) >= 0
    ]
    if not top_marks:
        return None

    substitutions = [
        (base_name, dotless_name)
        for base_name, dotless_name in (("i", "dotlessi"), ("j", "dotlessj"))
        if base_name in glyphs_def and dotless_name in glyphs_def
    ]
    if not substitutions:
        return None

    lines = ["feature ccmp {"]
    lines.append(f"    @top_marks = [{' '.join(sorted(top_marks))}];")
    for base_name, dotless_name in substitutions:
        lines.append("")
        lines.append(f"    lookup ccmp_{base_name}_before_top_marks {{")
        lines.append(f"        sub {base_name}' @top_marks by {dotless_name};")
        lines.append(f"    }} ccmp_{base_name}_before_top_marks;")
    lines.append("} ccmp;")
    return "\n".join(lines)


def generate_mark_fea(glyphs_def: dict, pixel_width: int, pixel_height: int) -> str | None:
    """Generate OpenType feature code for mark positioning (combining diacriticals).

    Scans glyphs_def for marks (is_mark: true) and base glyphs with
    top_mark_y / bottom_mark_y anchors, then emits a GPOS 'mark' feature.

    Returns the FEA string, or None if there are no marks.
    """
    # Collect mark glyphs, split into top vs bottom.
    # Marks with base_x_adjust or base_y_adjust get their own mark class and lookup.
    top_marks = {}       # glyph_name -> (anchor_x, anchor_y)
    bottom_marks = {}
    adjusted_marks = {}  # glyph_name -> (anchor_x, anchor_y, is_top, base_x_adjust, base_y_adjust)
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or not glyph_def.get("is_mark"):
            continue
        bitmap = glyph_def.get("bitmap", [])
        y_offset = glyph_def.get("y_offset", 0)
        # Mark anchor x = 0 (bitmap is centered on origin by zero-width drawing)
        anchor_x = 0
        # Resolve base_x_adjust with mark-only overrides
        base_x_adjust = glyph_def.get("base_x_adjust")
        mark_x_override = glyph_def.get("mark_base_x_adjust")
        if mark_x_override and base_x_adjust:
            base_x_adjust = {**base_x_adjust, **mark_x_override}
        elif mark_x_override:
            base_x_adjust = mark_x_override
        # Resolve base_y_adjust with mark-only overrides
        base_y_adjust = glyph_def.get("base_y_adjust")
        mark_y_override = glyph_def.get("mark_base_y_adjust")
        if mark_y_override and base_y_adjust:
            base_y_adjust = {**base_y_adjust, **mark_y_override}
        elif mark_y_override:
            base_y_adjust = mark_y_override
        has_adjustments = base_x_adjust or base_y_adjust
        if y_offset >= 0:
            # Top mark: anchor at the bottom of the drawn pixels
            anchor_y = y_offset * pixel_height
            if has_adjustments:
                adjusted_marks[glyph_name] = (anchor_x, anchor_y, True, base_x_adjust or {}, base_y_adjust or {})
            else:
                top_marks[glyph_name] = (anchor_x, anchor_y)
        else:
            # Bottom mark: anchor at the top of the drawn pixels
            bitmap_height = len(bitmap) if bitmap else 0
            anchor_y = (y_offset + bitmap_height) * pixel_height
            if has_adjustments:
                adjusted_marks[glyph_name] = (anchor_x, anchor_y, False, base_x_adjust or {}, base_y_adjust or {})
            else:
                bottom_marks[glyph_name] = (anchor_x, anchor_y)

    if not top_marks and not bottom_marks and not adjusted_marks:
        return None

    # Collect base glyphs with anchors
    top_bases = {}    # glyph_name -> (anchor_x, anchor_y)
    bottom_bases = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or glyph_def.get("is_mark"):
            continue
        advance_width = glyph_def.get("advance_width")
        if advance_width is not None:
            aw = advance_width * pixel_width
        else:
            bitmap = glyph_def.get("bitmap", [])
            if bitmap:
                max_col = max((len(row) for row in bitmap), default=0)
                aw = (max_col + 2) * pixel_width
            else:
                continue
        base_x = aw // 2
        if "top_mark_y" in glyph_def:
            top_x = base_x + glyph_def.get("top_mark_x", 0) * pixel_width
            base_y = glyph_def["top_mark_y"] * pixel_height
            top_bases[glyph_name] = (top_x, base_y)
        if "bottom_mark_y" in glyph_def:
            bottom_x = base_x + glyph_def.get("bottom_mark_x", 0) * pixel_width
            base_y = glyph_def["bottom_mark_y"] * pixel_height
            bottom_bases[glyph_name] = (bottom_x, base_y)

    if not top_bases and not bottom_bases:
        return None

    lines = ["feature mark {"]

    # Emit markClass definitions for standard marks
    for glyph_name in sorted(top_marks):
        ax, ay = top_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_top;")
    for glyph_name in sorted(bottom_marks):
        ax, ay = bottom_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_bottom;")
    # Emit markClass definitions for adjusted marks (each gets its own class)
    for glyph_name in sorted(adjusted_marks):
        ax, ay, _, _, _ = adjusted_marks[glyph_name]
        lines.append(f"    markClass {glyph_name} <anchor {ax} {ay}> @mark_{glyph_name};")

    # Emit lookup for standard top marks
    if top_marks and top_bases:
        lines.append("")
        lines.append("    lookup mark_top {")
        for glyph_name in sorted(top_bases):
            bx, by = top_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_top;")
        lines.append("    } mark_top;")

    # Emit lookup for standard bottom marks
    if bottom_marks and bottom_bases:
        lines.append("")
        lines.append("    lookup mark_bottom {")
        for glyph_name in sorted(bottom_bases):
            bx, by = bottom_bases[glyph_name]
            lines.append(f"        pos base {glyph_name} <anchor {bx} {by}> mark @mark_bottom;")
        lines.append("    } mark_bottom;")

    # Emit lookups for adjusted marks (per-base anchor overrides)
    for mark_name in sorted(adjusted_marks):
        _, _, is_top, base_x_adjust, base_y_adjust = adjusted_marks[mark_name]
        bases = top_bases if is_top else bottom_bases
        if not bases:
            continue
        lines.append("")
        lines.append(f"    lookup mark_{mark_name} {{")
        for glyph_name in sorted(bases):
            bx, by = bases[glyph_name]
            x_adj = base_x_adjust.get(glyph_name, 0) * pixel_width
            y_adj = base_y_adjust.get(glyph_name, 0) * pixel_height
            lines.append(f"        pos base {glyph_name} <anchor {int(bx + x_adj)} {int(by + y_adj)}> mark @mark_{mark_name};")
        lines.append(f"    }} mark_{mark_name};")

    lines.append("} mark;")
    return "\n".join(lines)


def _normalize_anchors(raw) -> list[list[int]]:
    """Normalize a single [x, y] or list of [x, y] pairs to a list of pairs."""
    if raw is None:
        return []
    if isinstance(raw[0], list):
        return raw
    return [raw]


def _is_entry_variant(glyph_name: str) -> bool:
    """Check if a glyph name contains an entry-* modifier segment."""
    return any(p.startswith("entry-") for p in glyph_name.split(".")[1:])


def _is_exit_variant(glyph_name: str) -> bool:
    """Check if a glyph name contains an exit-* modifier segment."""
    return any(p.startswith("exit-") for p in glyph_name.split(".")[1:])


def _extended_entry_suffix(glyph_name: str) -> str | None:
    """Return the .entry-extended* or .entry-doubly-extended* suffix segment if present, else None."""
    for part in glyph_name.split(".")[1:]:
        if part.startswith("entry-extended") or part.startswith("entry-doubly-extended"):
            return "." + part
    return None


def _extended_exit_suffix(glyph_name: str) -> str | None:
    """Return the .exit-extended* or .exit-doubly-extended* suffix segment if present, else None."""
    for part in glyph_name.split(".")[1:]:
        if part.startswith("exit-extended") or part.startswith("exit-doubly-extended"):
            return "." + part
    return None


_EXTENDED_HEIGHT_LABELS = {0: "baseline", 5: "xheight", 6: "y6", 8: "top"}


def _is_contextual_variant(glyph_name: str) -> bool:
    """Check if a glyph name is a contextual variant (entry-*, exit-*, or half)."""
    parts = glyph_name.split(".")[1:]
    return any(
        p.startswith("entry-") or p.startswith("exit-") or p == "half"
        for p in parts
    )


def _glyph_name_modifiers(glyph_name: str) -> list[str]:
    return glyph_name.split(".")[1:]


def _compat_assertions_from_modifiers(
    modifiers: list[str],
    traits: frozenset[str],
) -> frozenset[str]:
    compat = set(modifiers) | set(traits)
    for modifier in modifiers:
        if modifier.startswith("entry-"):
            compat.update({"entry", modifier.removeprefix("entry-")})
        elif modifier.startswith("exit-"):
            compat.update({"exit", modifier.removeprefix("exit-")})
        if modifier.startswith("entry-doubly-extended"):
            compat.update({"entry", "extended", "doubly-extended", "entry-doubly-extended"})
            continue
        if modifier.startswith("exit-doubly-extended"):
            compat.update({"exit", "extended", "doubly-extended", "exit-doubly-extended"})
            continue
        if modifier.startswith("entry-extended"):
            compat.update({"entry", "extended", "entry-extended"})
            continue
        if modifier.startswith("exit-extended"):
            compat.update({"exit", "extended", "exit-extended"})
            continue
    return frozenset(compat)


def _base_name_for_compiled_glyph(glyph_name: str, glyph_def: dict) -> str:
    if glyph_def.get("_base_name"):
        return glyph_def["_base_name"]
    if glyph_def.get("_family"):
        return glyph_def["_family"]
    return get_base_glyph_name(glyph_name).split(".")[0]


def _entry_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith("entry-"):
            return "." + modifier
    return None


def _exit_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith("exit-"):
            return "." + modifier
    return None


def _extended_entry_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith("entry-extended") or modifier.startswith("entry-doubly-extended"):
            return "." + modifier
    return None


def _extended_exit_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith("exit-extended") or modifier.startswith("exit-doubly-extended"):
            return "." + modifier
    return None


def _entry_restriction_y_from_modifiers(modifiers: list[str]) -> int | None:
    for modifier in modifiers:
        if not modifier.startswith("entry-extended-at-"):
            continue
        label = modifier.removeprefix("entry-extended-at-")
        return next(
            (y for y, known_label in _EXTENDED_HEIGHT_LABELS.items() if known_label == label),
            None,
        )
    return None


def _seeded_base_name(glyph_name: str, glyph_def: dict) -> str:
    return glyph_def.get("_base_name", get_base_glyph_name(glyph_name).split(".")[0])


def _seeded_modifiers(glyph_name: str, glyph_def: dict) -> tuple[str, ...]:
    modifiers = glyph_def.get("_modifiers")
    if modifiers is not None:
        return tuple(modifiers)
    return tuple(_glyph_name_modifiers(glyph_name))


def _seeded_extended_entry_suffix(glyph_name: str, glyph_def: dict) -> str | None:
    return glyph_def.get("_extended_entry_suffix", _extended_entry_suffix(glyph_name))


def _seeded_extended_exit_suffix(glyph_name: str, glyph_def: dict) -> str | None:
    return glyph_def.get("_extended_exit_suffix", _extended_exit_suffix(glyph_name))


def _stamp_compiled_glyph_seed(
    glyph_def: dict,
    *,
    output_name: str,
    base_name: str,
    family_name: str | None = None,
    sequence: list[str] | tuple[str, ...] | None = None,
    traits: list[str] | tuple[str, ...] | frozenset[str] | None = None,
    contextual: bool,
    modifiers: tuple[str, ...] | list[str] | None = None,
    is_noentry: bool | None = None,
) -> None:
    glyph_def["_base_name"] = base_name
    if family_name is not None:
        glyph_def["_family"] = family_name
    if sequence is not None:
        if sequence:
            glyph_def["_sequence"] = deepcopy(sequence)
        else:
            glyph_def.pop("_sequence", None)

    if traits is None:
        resolved_traits = tuple(glyph_def.get("_traits", ()))
    else:
        resolved_traits = tuple(traits)
    if resolved_traits:
        glyph_def["_traits"] = list(resolved_traits)
    else:
        glyph_def.pop("_traits", None)

    glyph_def["_contextual"] = bool(contextual)

    resolved_modifiers = tuple(modifiers) if modifiers is not None else tuple(_glyph_name_modifiers(output_name))
    glyph_def["_modifiers"] = list(resolved_modifiers)

    trait_set = frozenset(resolved_traits)
    glyph_def["_compat_assertions"] = sorted(
        _compat_assertions_from_modifiers(list(resolved_modifiers), trait_set)
    )
    glyph_def["_is_entry_variant"] = any(
        modifier.startswith("entry-") for modifier in resolved_modifiers
    )
    glyph_def["_is_exit_variant"] = any(
        modifier.startswith("exit-") for modifier in resolved_modifiers
    )
    glyph_def["_entry_suffix"] = _entry_suffix_from_modifiers(list(resolved_modifiers))
    glyph_def["_exit_suffix"] = _exit_suffix_from_modifiers(list(resolved_modifiers))
    glyph_def["_extended_entry_suffix"] = _extended_entry_suffix_from_modifiers(
        list(resolved_modifiers)
    )
    glyph_def["_extended_exit_suffix"] = _extended_exit_suffix_from_modifiers(
        list(resolved_modifiers)
    )
    glyph_def["_entry_restriction_y"] = _entry_restriction_y_from_modifiers(
        list(resolved_modifiers)
    )
    glyph_def["_is_noentry"] = bool(
        ("noentry" in resolved_modifiers) if is_noentry is None else is_noentry
    )


def build_compiled_glyph_metadata(glyphs_def: dict) -> dict[str, CompiledGlyphMeta]:
    metadata: dict[str, CompiledGlyphMeta] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue

        modifiers = _seeded_modifiers(glyph_name, glyph_def)
        traits = frozenset(glyph_def.get("_traits", []))
        metadata[glyph_name] = CompiledGlyphMeta(
            name=glyph_name,
            base_name=_seeded_base_name(glyph_name, glyph_def),
            family=glyph_def.get("_family"),
            sequence=tuple(glyph_def.get("_sequence", ())),
            traits=traits,
            modifiers=frozenset(modifiers),
            compat_assertions=frozenset(
                glyph_def.get(
                    "_compat_assertions",
                    _compat_assertions_from_modifiers(list(modifiers), traits),
                )
            ),
            entry=tuple(tuple(anchor) for anchor in _normalize_anchors(glyph_def.get("cursive_entry"))),
            entry_curs_only=tuple(
                tuple(anchor)
                for anchor in _normalize_anchors(glyph_def.get("cursive_entry_curs_only"))
            ),
            exit=tuple(tuple(anchor) for anchor in _normalize_anchors(glyph_def.get("cursive_exit"))),
            after=tuple(glyph_def.get("calt_after", ())),
            before=tuple(glyph_def.get("calt_before", ())),
            not_after=tuple(glyph_def.get("calt_not_after", ())),
            not_before=tuple(glyph_def.get("calt_not_before", ())),
            reverse_upgrade_from=tuple(glyph_def.get("reverse_upgrade_from", ())),
            preferred_over=tuple(glyph_def.get("preferred_over", ())),
            word_final=bool(glyph_def.get("calt_word_final")),
            is_contextual=bool(glyph_def.get("_contextual", _is_contextual_variant(glyph_name))),
            is_entry_variant=bool(
                glyph_def.get(
                    "_is_entry_variant",
                    any(modifier.startswith("entry-") for modifier in modifiers),
                )
            ),
            is_exit_variant=bool(
                glyph_def.get(
                    "_is_exit_variant",
                    any(modifier.startswith("exit-") for modifier in modifiers),
                )
            ),
            entry_suffix=glyph_def.get("_entry_suffix", _entry_suffix_from_modifiers(list(modifiers))),
            exit_suffix=glyph_def.get("_exit_suffix", _exit_suffix_from_modifiers(list(modifiers))),
            extended_entry_suffix=glyph_def.get(
                "_extended_entry_suffix",
                _extended_entry_suffix_from_modifiers(list(modifiers)),
            ),
            extended_exit_suffix=glyph_def.get(
                "_extended_exit_suffix",
                _extended_exit_suffix_from_modifiers(list(modifiers)),
            ),
            entry_restriction_y=glyph_def.get(
                "_entry_restriction_y",
                _entry_restriction_y_from_modifiers(list(modifiers)),
            ),
            is_noentry=bool(glyph_def.get("_is_noentry", "noentry" in modifiers)),
        )
    return metadata


def _resolve_known_glyph_names(values: tuple[str, ...] | list[str], glyphs_def: dict) -> list[str]:
    resolved = []
    for value in values:
        resolved.append(value if value in glyphs_def else get_base_glyph_name(value))
    return resolved


def generate_calt_fea(glyphs_def: dict, pixel_width: int) -> str | None:
    """Generate OpenType feature code for contextual alternates (calt).

    Generates two kinds of contextual substitution rules:

    1. Backward-looking: scans for entry-* variants with cursive_entry anchors,
       substitutes based on the preceding glyph's exit height.
    2. Forward-looking: scans for exit-* variants with cursive_exit anchors,
       substitutes based on the following glyph's entry height. These run after
       backward rules, so they only fire for glyphs not already substituted.

    Returns the FEA string, or None if no contextual variants are found.
    """
    glyph_meta = build_compiled_glyph_metadata(glyphs_def)

    def _meta(name: str) -> CompiledGlyphMeta:
        return glyph_meta[name]

    def _base_name(name: str) -> str:
        return _meta(name).base_name

    plan = CaltPlan(glyph_meta=glyph_meta)

    # --- Backward-looking: entry variants keyed by entry Y ---
    bk_replacements = plan.bk_replacements
    bk_exclusions = plan.bk_exclusions
    # Pair-specific overrides: entry variants with calt_after lists
    pair_overrides = plan.pair_overrides
    # Each entry is (entry_exit_var, entry_only_var, exit_y, not_before_glyphs)
    fwd_upgrades = plan.fwd_upgrades
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        meta = _meta(glyph_name)
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
        if base_name not in glyphs_def:
            continue
        if "alt" in meta.traits:
            base_meta = glyph_meta.get(base_name)
            if base_meta and entry_y in base_meta.entry_ys:
                continue
        if meta.before:
            continue
        calt_after = meta.after
        if calt_after:
            pair_overrides.setdefault(base_name, []).append(
                (glyph_name, list(calt_after))
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
                resolved = _resolve_known_glyph_names(not_after, glyphs_def)
                bk_exclusions.setdefault(base_name, {})[entry_y] = resolved

    # --- Pair override upgrades ---
    # When two pair overrides share the same base and calt_after list but
    # differ in having a cursive_exit, register an upgrade: the entry-only
    # variant is selected by the backward pair rule, and a forward upgrade
    # rule later converts it to the entry+exit variant when needed.
    for base_name, overrides in pair_overrides.items():
        by_after: dict[tuple, list[tuple[str, list[str]]]] = {}
        for vn, after in overrides:
            key = tuple(sorted(after))
            by_after.setdefault(key, []).append((vn, after))
        for group in by_after.values():
            with_exit = []
            without_exit = []
            for vn, after in group:
                vmeta = _meta(vn)
                if vmeta.exit:
                    with_exit.append((vn, vmeta))
                else:
                    without_exit.append((vn, vmeta))
            if with_exit and without_exit:
                entry_only_var = without_exit[0][0]
                entry_exit_var = with_exit[0][0]
                exit_y = with_exit[0][1].exit[0][1]
                nb = list(with_exit[0][1].not_before)
                fwd_upgrades.setdefault(base_name, []).append(
                    (entry_exit_var, entry_only_var, exit_y, list(nb))
                )

    # --- Forward-looking: exit variants keyed by exit Y ---
    # Detects any variant with cursive_exit (catches .exit-* names and
    # .half variants alike). Entry variants (entry-* names) are excluded
    # since they are handled by the backward-looking rules.
    fwd_replacements = plan.fwd_replacements
    fwd_exclusions = plan.fwd_exclusions
    # Pair-specific forward overrides: variants with calt_before lists
    # Each entry is (variant_name, before_glyphs, not_after_glyphs)
    fwd_pair_overrides = plan.fwd_pair_overrides
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        meta = _meta(glyph_name)
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
        extra_parts = meta.modifiers - {"alt", "prop"}
        if extra_parts and "alt" in meta.traits and meta.entry and not meta.before:
            continue
        if not meta.exit:
            continue
        exit_y = meta.exit[0][1]
        base_name = meta.base_name
        if base_name not in glyphs_def:
            continue
        calt_before = meta.before
        if calt_before:
            resolved = _resolve_known_glyph_names(calt_before, glyphs_def)
            not_after = meta.not_after
            resolved_not_after = (
                _resolve_known_glyph_names(not_after, glyphs_def)
                if not_after else []
            )
            fwd_pair_overrides.setdefault(base_name, []).append(
                (glyph_name, resolved, resolved_not_after)
            )
        else:
            fwd_replacements.setdefault(base_name, {})[exit_y] = glyph_name
            not_before = meta.not_before
            if not_before:
                resolved = _resolve_known_glyph_names(not_before, glyphs_def)
                fwd_exclusions.setdefault(base_name, {})[exit_y] = resolved

    # Variants that should only gain an entry anchor after a forward rule
    # has already selected an exit-only form.
    reverse_only_upgrades = plan.reverse_only_upgrades
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        meta = _meta(glyph_name)
        reverse_from = meta.reverse_upgrade_from
        if not reverse_from:
            continue
        entries = list(meta.entry)
        exits = list(meta.exit)
        if not entries or not exits:
            continue
        exit_ys = {a[1] for a in exits}
        resolved_sources = _resolve_known_glyph_names(reverse_from, glyphs_def)
        matching_sources = []
        for source_name in resolved_sources:
            source_exits = list(_meta(source_name).exit)
            if source_exits and exit_ys & {a[1] for a in source_exits}:
                matching_sources.append(source_name)
        if matching_sources:
            reverse_only_upgrades.append(
                (
                    glyph_name,
                    matching_sources,
                    [a[1] for a in entries],
                    list(glyph_def.get("calt_not_before", [])),
                )
            )

    if not bk_replacements and not fwd_replacements:
        return None

    def _expand_all_variants(glyphs, *, include_base=False):
        """Expand glyph names to include all known variants from replacement dicts."""
        expanded = set(glyphs)
        for g in glyphs:
            base = _base_name(g)
            if include_base:
                expanded.add(base)
            if base in bk_replacements:
                expanded.update(bk_replacements[base].values())
            if base in fwd_replacements:
                expanded.update(fwd_replacements[base].values())
            if base in pair_overrides:
                expanded.update(pv for pv, _ in pair_overrides[base])
            if base in fwd_pair_overrides:
                expanded.update(pv for pv, _, _ in fwd_pair_overrides[base])
        return expanded

    # --- Build terminal sets ---
    # Entry-only terminal: has cursive_entry but no cursive_exit, and no
    # sibling variant has both entry at the same Y and any exit (i.e., no
    # upgrade path exists).  Exit-only terminal: the reverse.
    _base_anchors: dict[str, list[tuple[str, set[int], set[int]]]] = {}
    for gn, gd in glyphs_def.items():
        if gd is None or _meta(gn).is_noentry:
            continue
        meta = _meta(gn)
        eys = set(meta.entry_ys)
        exs = set(meta.exit_ys)
        _base_anchors.setdefault(meta.base_name, []).append((gn, eys, exs))

    terminal_entry_only = plan.terminal_entry_only
    terminal_exit_only = plan.terminal_exit_only
    for base, siblings in _base_anchors.items():
        for gn, eys, exs in siblings:
            if eys and not exs:
                for y in eys:
                    if not any(
                        sn != gn and y in se and sx
                        for sn, se, sx in siblings
                    ):
                        terminal_entry_only.add(gn)
                        break
            if exs and not eys:
                for y in exs:
                    if not any(
                        sn != gn and y in sx2 and se2
                        for sn, se2, sx2 in siblings
                    ):
                        terminal_exit_only.add(gn)
                        break

    # --- Build exit classes (for backward rules) ---
    exit_classes = plan.exit_classes
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None or _meta(glyph_name).is_noentry:
            continue
        meta = _meta(glyph_name)
        if not meta.exit:
            continue
        for anchor in meta.exit:
            exit_classes.setdefault(anchor[1], set()).add(glyph_name)

    # --- Build entry classes (for forward rules) ---
    # Includes both glyphs with explicit cursive_entry anchors and base
    # glyphs that have entry-* variants (so forward rules can match the
    # base glyph before the backward rule substitutes it).
    entry_classes = plan.entry_classes
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        meta = _meta(glyph_name)
        if not meta.entry:
            continue
        for anchor in meta.entry:
            entry_classes.setdefault(anchor[1], set()).add(glyph_name)
            is_bk = meta.is_entry_variant
            if not is_bk:
                is_bk = ("half" in meta.traits or "alt" in meta.traits) and bool(meta.entry)
            if is_bk:
                base_name = meta.base_name
                if base_name in glyphs_def and anchor[1] in bk_replacements.get(base_name, {}):
                    entry_classes[anchor[1]].add(base_name)

    # --- Add exit-only variants to entry classes ---
    # If a base glyph is in an entry class (because it has entry variants),
    # its exit-only variants should also be in the class — they can be
    # reverse-upgraded to entry+exit variants by a later backward rule.
    for base_name in fwd_upgrades:
        for entry_exit_var, entry_only_var, exit_y, _ in fwd_upgrades[base_name]:
            entry_meta = _meta(entry_only_var)
            if not entry_meta.entry:
                continue
            entry_y_val = entry_meta.entry[0][1]
            exit_only_var = fwd_replacements.get(base_name, {}).get(exit_y)
            if exit_only_var and entry_y_val in entry_classes:
                entry_classes[entry_y_val].add(exit_only_var)

    # --- Add forward-selected exit-only variants to entry classes ---
    # When the cycle's forward rules convert a glyph to an exit-only variant
    # (e.g., qsIt → qsIt.exit-baseline), later overrides still need to
    # recognise the converted glyph as "entering" at the base's entry height.
    for base_name, fwd_vars in fwd_replacements.items():
        base_entry_ys = {y for y, members in entry_classes.items() if base_name in members}
        if not base_entry_ys:
            continue
        for fwd_exit_y, fwd_var in fwd_vars.items():
            if _meta(fwd_var).entry:
                continue
            for y in base_entry_ys:
                entry_classes[y].add(fwd_var)

    # --- Exclusive entry classes (for restricted forward rules) ---
    # A glyph is in @entry_only_yN when it enters at y=N but at NO other
    # height.  Forward rules use this restricted class when the selected
    # variant is also a backward variant — preventing the forward rule from
    # firing when the next glyph could enter at a different height that
    # the backward rule would prefer.
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
            for exit_y, variant_name in fwd_replacements[base_name].items():
                if exit_y not in base_exit_ys and exit_y < min_base_exit:
                    fwd_use_exclusive.add((base_name, exit_y))

    # --- preferred_over: two-glyph lookahead for exclusive variants ---
    # When a variant has preferred_over and uses exclusive matching, add a
    # two-glyph rule so it also fires when the next glyph is ambiguous but
    # the glyph after that exclusively enters at the sibling's exit height.
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

    # --- Topological sort for backward-looking lookups ---
    # Consider exit heights INTRODUCED by both entry (backward) and exit
    # (forward) variants — any variant that changes the base glyph's exit
    # height creates a dependency for bases whose backward rules consume
    # that height.
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
    edges: dict[str, set[str]] = {b: set() for b in base_order}
    for base_a in base_order:
        for base_b in base_order:
            if base_a == base_b:
                continue
            b_entry_ys = set(bk_replacements[base_b].keys())
            if base_exit_ys[base_a] & b_entry_ys:
                edges[base_b].add(base_a)

    # Kahn's algorithm: topological sort that collects cycle members
    # instead of raising an error.  Cycle members are merged into a
    # single lookup so left-to-right rule processing resolves them.
    out_edges: dict[str, set[str]] = {b: set() for b in base_order}
    in_degree: dict[str, int] = {b: len(edges[b]) for b in base_order}
    for b in base_order:
        for dep in edges[b]:
            out_edges[dep].add(b)

    queue = deque(sorted(b for b in base_order if in_degree[b] == 0))
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

    # --- Generate FEA ---
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
        for _, exit_y, sibling_y in entries:
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
            sy == y for entries in fwd_preferred_lookahead.values() for _, _, sy in entries
        )
        needs_excl = any(ey == y for _, ey in fwd_use_exclusive) or preferred_needs_excl
        if needs_excl and y in entry_exclusive:
            excl_members = sorted(entry_exclusive[y])
            if excl_members:
                lines.append(f"    @entry_only_y{y} = [{' '.join(excl_members)}];")

    # ZWNJ chain-breaking: substitute glyphs after ZWNJ with .noentry
    # variants so the curs feature sees NULL entry and breaks the chain.
    zwnj = "uni200C"
    noentry_pairs = []
    for name in sorted(glyphs_def):
        meta = _meta(name)
        if meta.is_noentry:
            base = meta.base_name
            if base in glyphs_def:
                noentry_pairs.append((base, name))
    if zwnj in glyphs_def and noentry_pairs:
        cursive_names = [b for b, _ in noentry_pairs]
        noentry_names = [n for _, n in noentry_pairs]
        lines.append(f"    @qs_has_entry = [{' '.join(cursive_names)}];")
        lines.append(f"    @qs_noentry = [{' '.join(noentry_names)}];")
        lines.append("")
        lines.append("    lookup calt_zwnj {")
        lines.append(f"        sub {zwnj} @qs_has_entry' by @qs_noentry;")
        lines.append("    } calt_zwnj;")

    # Word-final substitutions: mark glyphs as word-final, then revert
    # when followed by another Quikscript letter. Net effect: .fina
    # variants only survive at end-of-word (before space/punctuation/EOT).
    word_final_pairs = {}
    for name, gdef in glyphs_def.items():
        if gdef and _meta(name).word_final:
            base = _base_name(name)
            if base in glyphs_def:
                word_final_pairs[base] = name

    if word_final_pairs:
        excluded_bases = {"qsAngleParenLeft", "qsAngleParenRight"}
        qs_letter_names = set()
        for name in glyphs_def:
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

    # Lookup ordering: forward-only bases run first so they can change a
    # glyph's exit before backward rules for the *following* glyph commit to
    # an entry variant.  For bases with both backward and forward rules, the
    # backward lookup runs first (so a preceding exit wins over a following
    # entry), then its forward companion.
    _base_to_variants: dict[str, set[str]] = {}
    for _gn in glyphs_def:
        _base_to_variants.setdefault(_base_name(_gn), set()).add(_gn)

    def _can_match_pair_exit(name: str, exit_y: int) -> bool:
        meta = _meta(name)
        if exit_y in meta.exit_ys:
            return True
        if meta.exit:
            return False
        return any(
            exit_y in _meta(candidate).exit_ys
            for candidate in _base_to_variants.get(meta.base_name, ())
        )

    def _expand_exclusions(eg_list: list[str]) -> set[str]:
        expanded = set()
        for eg in eg_list:
            eg_base = _base_name(eg)
            expanded.update(_base_to_variants.get(eg_base, ()))
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
                    for pv, _ in pair_overrides[base_name]:
                        targets.add(pv)

                variant_meta = _meta(variant_name)
                variant_entry_ys = set(variant_meta.entry_ys) if variant_meta.entry else None

                expanded_not_after = _expand_all_variants(not_after_glyphs, include_base=True)

                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_fwd_pair_{safe} {{")
                for target in sorted(targets):
                    guard_list = None
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
                                for iy in incompatible_ys:
                                    guard_glyphs.update(exit_classes.get(iy, set()))
                                if guard_glyphs:
                                    guard_list = " ".join(sorted(guard_glyphs))
                    else:
                        if target_has_entry and target_meta.is_entry_variant:
                            if target_meta.exit:
                                target_exit_ys = set(target_meta.exit_ys)
                                variant_exit_ys = set(variant_meta.exit_ys)
                                if variant_exit_ys <= target_exit_ys:
                                    continue
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
                                if protect_ys:
                                    guard_glyphs = set()
                                    for py in protect_ys:
                                        guard_glyphs.update(exit_classes.get(py, set()))
                                    if guard_glyphs:
                                        guard_list = " ".join(sorted(guard_glyphs))
                    actual_variant = variant_name
                    suffix = target_meta.extended_entry_suffix
                    if suffix:
                        extended = variant_name + suffix
                        if extended not in glyphs_def:
                            extended = variant_name + ".entry-extended"
                        if extended in glyphs_def:
                            actual_variant = extended
                    if guard_list:
                        lines.append(
                            f"        ignore sub [{guard_list}] {target}' [{before_list}];"
                        )
                    if expanded_not_after:
                        na_list = " ".join(sorted(expanded_not_after))
                        lines.append(
                            f"        ignore sub [{na_list}] {target}' [{before_list}];"
                        )
                    for teo in sorted(expanded_before & terminal_exit_only):
                        lines.append(f"        ignore sub {target}' {teo};")
                    lines.append(
                        f"        sub {target}' [{before_list}] by {actual_variant};"
                    )
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
                base_ey = {y for y, m in entry_classes.items() if base_name in m}
                var_ey = {y for y, m in entry_classes.items() if variant_name in m}
                if var_ey:
                    for h in sorted(base_ey - var_ey):
                        if h in exit_classes:
                            lines.append(f"        ignore sub @exit_y{h} {base_name}' {cls};")
                excluded = _expand_exclusions(exclusions.get(exit_y, []))
                for eg in sorted(excluded):
                    lines.append(f"        ignore sub {base_name}' {eg};")
                lines.append(f"        sub {base_name}' {cls} by {variant_name};")
            if base_name in fwd_preferred_lookahead:
                for variant_name, exit_y, sibling_y in fwd_preferred_lookahead[base_name]:
                    if exit_y in entry_classes and sibling_y in entry_exclusive and entry_exclusive[sibling_y]:
                        lines.append(
                            f"        sub {base_name}' @entry_y{exit_y} @entry_only_y{sibling_y} by {variant_name};"
                        )
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
                        name for name in expanded_after
                        if _can_match_pair_exit(name, entry_y)
                    }
                after_list = " ".join(sorted(expanded_after))
                safe = variant_name.replace(".", "_")
                lines.append("")
                lines.append(f"    lookup calt_pair_{safe} {{")
                not_before = list(variant_meta.not_before)
                if not_before:
                    resolved = _resolve_known_glyph_names(not_before, glyphs_def)
                    for nb in sorted(_expand_exclusions(resolved)):
                        lines.append(f"        ignore sub [{after_list}] {base_name}' {nb};")
                for tei in sorted(expanded_after & terminal_entry_only):
                    lines.append(f"        ignore sub {tei} {base_name}';")
                lines.append(
                    f"        sub [{after_list}] {base_name}' by {variant_name};"
                )
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
                    for eg in sorted(_expand_exclusions(exclusions.get(entry_y, []))):
                        lines.append(f"        ignore sub {eg} {base_name}';")
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
                    base_ey = {y for y, m in entry_classes.items() if base_name in m}
                    var_ey = {y for y, m in entry_classes.items() if variant_name in m}
                    if var_ey:
                        for h in sorted(base_ey - var_ey):
                            if h in exit_classes:
                                lines.append(f"        ignore sub @exit_y{h} {base_name}' {cls};")
                    excluded = _expand_exclusions(exclusions.get(exit_y, []))
                    for eg in sorted(excluded):
                        lines.append(f"        ignore sub {base_name}' {eg};")
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
                nb_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                lines.append(f"        ignore sub {entry_only_var}' [{nb_list}];")
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
            relevant = {y: v for y, v in variants.items()
                        if y in upgrade_exit_ys and y in exit_classes}
            if not relevant:
                continue
            safe = base_name.replace(".", "_").replace("-", "_")
            lines.append("")
            exclusions = bk_exclusions.get(base_name, {})
            lines.append(f"    lookup calt_post_upgrade_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                for eg in sorted(_expand_exclusions(exclusions.get(entry_y, []))):
                    lines.append(f"        ignore sub {eg} {base_name}';")
                lines.append(f"        sub @exit_y{entry_y} {base_name}' by {relevant[entry_y]};")
            lines.append(f"    }} calt_post_upgrade_bk_{safe};")

    def _emit_post_override_bk(bases: list[str]):
        """Re-run backward rules on exit-only variants after noentry overrides.

        When a noentry override converts a backward variant to a forward
        variant with a new exit height, the following glyph may already have
        been converted to an exit-only variant by the cycle.  This lookup
        re-processes those exit-only variants so they see the new exit.

        Only targets bases whose backward variant at the relevant height has
        BOTH entry and exit (i.e., is a complete variant), avoiding
        re-conversion of override targets whose backward variants are
        entry-only.
        """
        override_fwd_exit_ys: set[int] = set()
        for base_name in bases:
            if base_name not in bk_replacements or base_name not in fwd_replacements:
                continue
            for entry_y, bk_var in sorted(bk_replacements[base_name].items()):
                if _meta(bk_var).exit:
                    continue
                for fwd_exit_y, fwd_var in fwd_replacements[base_name].items():
                    override_fwd_exit_ys.update(_meta(fwd_var).exit_ys)
        if not override_fwd_exit_ys:
            return
        for base_name in bases:
            if base_name not in bk_replacements:
                continue
            variants = bk_replacements[base_name]
            relevant = {}
            for y, v in variants.items():
                if y not in override_fwd_exit_ys or y not in exit_classes:
                    continue
                if not _meta(v).exit:
                    continue
                relevant[y] = v
            if not relevant:
                continue
            fwd_exit_only = []
            for fv_y, fv in fwd_replacements.get(base_name, {}).items():
                if not _meta(fv).entry:
                    fwd_exit_only.append(fv)
            if not fwd_exit_only:
                continue
            safe = f"post_override_{base_name}".replace(".", "_").replace("-", "_")
            exclusions = bk_exclusions.get(base_name, {})
            lines.append("")
            lines.append(f"    lookup calt_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                excl = sorted(_expand_exclusions(exclusions.get(entry_y, [])))
                for fv in sorted(fwd_exit_only):
                    for eg in excl:
                        lines.append(f"        ignore sub {eg} {fv}';")
                    lines.append(f"        sub @exit_y{entry_y} {fv}' by {relevant[entry_y]};")
            lines.append(f"    }} calt_{safe};")

    def _emit_reverse_upgrades():
        """Emit rules that convert exit-only variants to entry+exit variants.

        When a preceding glyph's forward rule fires late (after the cycle
        has already applied the exit-only variant to the current glyph),
        this rule upgrades the exit-only variant to entry+exit so the
        connection is established.
        """
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
                    nb_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                    lines.append(f"        ignore sub {exit_only_var}' [{nb_list}];")
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
                nb_list = " ".join(sorted(_expand_all_variants(not_before, include_base=True)))
                for source_variant in source_variants:
                    lines.append(f"        ignore sub {source_variant}' [{nb_list}];")
            for entry_y in valid_entry_ys:
                for source_variant in source_variants:
                    lines.append(f"        sub @exit_y{entry_y} {source_variant}' by {variant_name};")
            lines.append(f"    }} calt_reverse_upgrade_explicit_{safe};")

    def _emit_noentry_fwd_overrides(bases: list[str]):
        """Replace no-exit backward variants with forward-only variants."""
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
                max_exit_y = max(ey for ey, _ in valid_overrides)
                for fwd_exit_y, fwd_var in valid_overrides:
                    use_exclusive = (
                        len(valid_overrides) > 1
                        and fwd_exit_y != max_exit_y
                    )
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
                        resolved = _resolve_known_glyph_names(not_before, glyphs_def)
                        for nb in sorted(_expand_exclusions(resolved)):
                            lines.append(f"        ignore sub {bk_var}' {nb};")
                    lines.append(f"        sub {bk_var}' {cls} by {fwd_var};")
                    lines.append(f"    }} calt_fwd_override_{safe};")

    # Bases whose pair_overrides come entirely from entry-extended variants
    # and also have forward behaviour should remain forward-only —
    # entry-extended pairs are about the entry side and don't affect the
    # base's forward exit behaviour. Pair-only bases still need their
    # backward pair lookups emitted.
    entry_ext_pair_only: set[str] = set()
    for base_name, overrides in pair_overrides.items():
        if all(_meta(vn).extended_entry_suffix is not None for vn, _ in overrides):
            entry_ext_pair_only.add(base_name)

    all_fwd_bases = set(fwd_replacements) | set(fwd_pair_overrides)
    entry_ext_fwd_only = entry_ext_pair_only & all_fwd_bases

    # Add pair-override-only bases to sorted_bases (after the topo-sorted ones)
    pair_only = sorted(set(pair_overrides) - set(bk_replacements) - entry_ext_fwd_only)
    all_bk_bases = sorted_bases + pair_only

    fwd_only_set = all_fwd_bases - set(bk_replacements) - (set(pair_overrides) - entry_ext_pair_only)

    # --- Topological sort for forward-only lookups ---
    # If base A's forward rule checks an entry class containing base B,
    # and B's forward rule substitutes B with a variant NOT in that class,
    # then B must fire before A (so A sees B's final form).
    fwd_fwd_edges: dict[str, set[str]] = {b: set() for b in fwd_only_set}
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

    # If base A has a backward pair whose calt_after references glyphs
    # belonging to base B (which has forward rules that substitute B into a
    # different variant), then B's forward rules must fire first so A's
    # backward pair sees the substituted form.
    for base_a in fwd_only_set:
        if base_a not in pair_overrides:
            continue
        for _, after_glyphs in pair_overrides[base_a]:
            for ag in after_glyphs:
                base_b = _base_name(ag)
                if base_b != base_a and base_b in fwd_only_set:
                    fwd_fwd_edges[base_a].add(base_b)

    fwd_out: dict[str, set[str]] = {b: set() for b in fwd_only_set}
    fwd_in_deg: dict[str, int] = {b: len(fwd_fwd_edges[b]) for b in fwd_only_set}
    for b in fwd_only_set:
        for dep in fwd_fwd_edges[b]:
            fwd_out[dep].add(b)

    fwd_queue = deque(sorted(b for b in fwd_only_set if fwd_in_deg[b] == 0))
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
        if base_meta and base_meta.sequence and all(c in glyphs_def for c in base_meta.sequence):
            lig_fwd_bases.add(base_name)

    # Pair-only bases whose fwd_upgrades come from pair overrides need their
    # backward pairs, upgrades, and forward rules emitted early — before the
    # main block's backward general rules — so other glyphs' backward rules
    # see the correct exit values.
    early_pair_upgrade_bases: set[str] = set()
    for base_name in pair_only:
        if base_name not in fwd_upgrades or base_name not in all_fwd_bases:
            continue
        pair_var_names = {vn for vn, _ in pair_overrides.get(base_name, [])}
        if any(entry_only in pair_var_names
               for _, entry_only, _, _ in fwd_upgrades[base_name]):
            early_pair_upgrade_bases.add(base_name)

    for base_name in fwd_only:
        if base_name in lig_fwd_bases:
            continue
        _emit_bk_pairs(base_name)
        _emit_fwd(base_name)

    for base_name in sorted(early_pair_upgrade_bases):
        _emit_bk_pairs(base_name)
        _emit_upgrades(base_name)
        _emit_fwd_general(base_name)

    # Split non-cycle bases into those the cycle depends on (emit before
    # the cycle) and the rest (emit after).  The topological order already
    # places dependencies first, so we just need to find the split point.
    pre_cycle: list[str] = []
    post_cycle: list[str] = []
    if cycle_bases:
        cycle_deps: set[str] = set()
        for cb in cycle_bases:
            cycle_deps |= edges.get(cb, set())
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

    # Identify bases whose forward pair lookups must run before backward
    # rules.  Two cases:
    # 1. Self-referencing: calt_before targets include the same base glyph.
    # 2. Cross-referencing: the variant's exit Y feeds into a before_glyph's
    #    backward entry Y, so the exit must be visible when that glyph's
    #    backward rule fires.
    early_fwd_pairs: set[str] = set()
    for base_name, overrides in fwd_pair_overrides.items():
        found = False
        for variant_name, before_glyphs, _ in overrides:
            if base_name in {_base_name(g) for g in before_glyphs}:
                early_fwd_pairs.add(base_name)
                found = True
                break
            variant_meta = _meta(variant_name)
            if variant_meta.exit:
                exit_ys = set(variant_meta.exit_ys)
                for bg in before_glyphs:
                    bg_base = _base_name(bg)
                    bk_ys = set(bk_replacements.get(bg_base, {}))
                    for pv, _ in pair_overrides.get(bg_base, []):
                        bk_ys.update(_meta(pv).entry_ys)
                    if exit_ys & bk_ys:
                        early_fwd_pairs.add(base_name)
                        found = True
                        break
            if found:
                break

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

    _emit_block(pre_cycle)

    if cycle_bases:
        cycle_list = sorted(cycle_bases)
        _emit_block(cycle_list, use_cycle=True)

    early_post = [b for b in post_cycle if b in early_fwd_pairs]
    late_post = [b for b in post_cycle if b not in early_fwd_pairs]
    _emit_block(early_post)
    _emit_block(late_post)

    if cycle_bases:
        pair_only_new_exit_ys: set[int] = set()
        for po_base in pair_only:
            base_ys = set()
            if po_base in glyph_meta:
                base_ys.update(glyph_meta[po_base].exit_ys)
            for variant_name, _ in pair_overrides[po_base]:
                for exit_y in _meta(variant_name).exit_ys:
                    if exit_y not in base_ys:
                        pair_only_new_exit_ys.add(exit_y)
        for cb in sorted(cycle_bases):
            if cb not in bk_replacements:
                continue
            variants = bk_replacements[cb]
            relevant = {y: v for y, v in variants.items()
                        if y in pair_only_new_exit_ys and y in exit_classes}
            if not relevant:
                continue
            safe = cb.replace(".", "_").replace("-", "_")
            exclusions = bk_exclusions.get(cb, {})
            lines.append("")
            lines.append(f"    lookup calt_post_pair_bk_{safe} {{")
            for entry_y in sorted(relevant.keys()):
                for eg in sorted(_expand_exclusions(
                        exclusions.get(entry_y, []))):
                    lines.append(f"        ignore sub {eg} {cb}';")
                lines.append(
                    f"        sub @exit_y{entry_y} {cb}'"
                    f" by {relevant[entry_y]};")
            lines.append(f"    }} calt_post_pair_bk_{safe};")

    _emit_reverse_upgrades()

    def _emit_exit_extended_bk_refinement():
        """Refine exit-extended variants with backward entry anchors.

        When a forward pair lookup replaces a base glyph with its
        exit-extended variant (which may lack an entry anchor), and a
        later lookup changes the preceding glyph to one that exits at a
        specific Y, this lookup converts the exit-extended variant to a
        combined entry+exit-extended variant so the connection works.
        """
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
                    if combined not in glyphs_def:
                        continue
                    if entry_y not in exit_classes:
                        continue
                    if not emitted_any:
                        lines.append("")
                        lines.append(
                            f"    lookup calt_ext_bk_{safe} {{"
                        )
                        emitted_any = True
                    excluded = set(
                        _expand_exclusions(exclusions.get(entry_y, []))
                    )
                    if excluded:
                        filtered = sorted(
                            exit_classes[entry_y] - excluded
                        )
                        if filtered:
                            member_list = " ".join(filtered)
                            lines.append(
                                f"        sub [{member_list}]"
                                f" {fwd_var}' by {combined};"
                            )
                    else:
                        lines.append(
                            f"        sub @exit_y{entry_y}"
                            f" {fwd_var}' by {combined};"
                        )
                if emitted_any:
                    lines.append(
                        f"    }} calt_ext_bk_{safe};"
                    )

    _emit_exit_extended_bk_refinement()

    # Ligature substitutions live in calt (not liga) so that forward
    # rules selecting alternate glyphs can block the ligature.  For
    # example, ·Day·Utter·Low: the forward rule replaces ·Utter with
    # alt ·Utter first, so the ·Day·Utter ligature pattern no longer
    # matches.
    ligatures = []
    for glyph_name in glyphs_def:
        meta = _meta(glyph_name)
        if not meta.sequence:
            continue
        if glyph_name != meta.base_name:
            continue
        if meta.is_noentry or meta.extended_entry_suffix is not None:
            continue
        if meta.extended_exit_suffix is not None:
            continue
        if all(component in glyphs_def for component in meta.sequence):
            ligatures.append((glyph_name, meta.sequence))
    if ligatures:
        from itertools import product as _product

        lines.append("")
        lines.append("    lookup calt_liga {")
        for lig_name, components in sorted(ligatures):
            variant_sets: list[list[str]] = []
            for i, comp in enumerate(components):
                variants: set[str] = set()
                if comp in bk_replacements:
                    variants.update(bk_replacements[comp].values())
                if comp in pair_overrides:
                    for variant_name, _ in pair_overrides[comp]:
                        variants.add(variant_name)
                if comp in fwd_pair_overrides:
                    for variant_name, _, _ in fwd_pair_overrides[comp]:
                        variants.add(variant_name)
                if i == 0:
                    if comp in fwd_replacements:
                        variants.update(fwd_replacements[comp].values())
                variant_sets.append([comp] + sorted(variants))
            for combo in _product(*variant_sets):
                component_str = " ".join(combo)
                actual_lig = lig_name
                suffix = _meta(combo[0]).extended_entry_suffix
                if suffix:
                    ext_lig = lig_name + suffix
                    if ext_lig not in glyphs_def:
                        ext_lig = lig_name + ".entry-extended"
                    if ext_lig in glyphs_def:
                        actual_lig = ext_lig
                exit_suffix = _meta(combo[-1]).extended_exit_suffix
                if exit_suffix:
                    ext_lig = actual_lig + ".exit-extended"
                    if ext_lig in glyphs_def:
                        actual_lig = ext_lig
                lines.append(f"        sub {component_str} by {actual_lig};")
        lines.append("    } calt_liga;")

        lig_glyph_names = {lig_name for lig_name, _ in ligatures}
        post_liga_rules: list[tuple[str, str, list[str]]] = []
        for base_name in sorted(pair_overrides):
            for variant_name, after_glyphs in pair_overrides[base_name]:
                if any(g in lig_glyph_names for g in after_glyphs):
                    post_liga_rules.append((base_name, variant_name, after_glyphs))

        # noentry_after: select .noentry variant of ligature glyphs when
        # preceded by specific glyphs, blocking cursive attachment.
        for lig_name in sorted(lig_glyph_names):
            lig_def = glyphs_def.get(lig_name, {}) or {}
            noentry_after = lig_def.get("noentry_after")
            if not noentry_after:
                continue
            noentry_name = lig_name + ".noentry"
            if noentry_name not in glyphs_def:
                continue
            post_liga_rules.append((lig_name, noentry_name, sorted(_expand_all_variants(noentry_after, include_base=True))))

        if post_liga_rules:
            lines.append("")
            lines.append("    lookup calt_post_liga {")
            for base_name, variant_name, after_glyphs in post_liga_rules:
                after_list = " ".join(sorted(after_glyphs))
                targets = {base_name}
                if base_name in bk_replacements:
                    targets.update(bk_replacements[base_name].values())
                for target in sorted(targets):
                    lines.append(
                        f"        sub [{after_list}] {target}' by {variant_name};"
                    )
            lines.append("    } calt_post_liga;")

        for base_name in sorted(lig_fwd_bases):
            _emit_fwd(base_name)

    lines.append("} calt;")
    return "\n".join(lines)


def generate_liga_fea(glyphs_def: dict) -> str | None:
    """Generate OpenType feature code for ligatures (liga).

    Finds glyphs whose names contain underscores (e.g., qsDay_qsUtter),
    splits on underscores to get component glyph names, and emits
    a liga feature that substitutes the component sequence with the ligature.

    Returns the FEA string, or None if no ligature glyphs exist.
    """
    ligatures = []
    for glyph_name in glyphs_def:
        if "_" not in glyph_name:
            continue
        components = glyph_name.split("_")
        if all(c in glyphs_def for c in components):
            ligatures.append((glyph_name, components))

    if not ligatures:
        return None

    lines = ["feature liga {"]
    for lig_name, components in sorted(ligatures):
        component_str = " ".join(components)
        lines.append(f"    sub {component_str} by {lig_name};")
    lines.append("} liga;")
    return "\n".join(lines)


def generate_curs_fea(glyphs_def: dict, pixel_width: int, pixel_height: int) -> str | None:
    """Generate OpenType feature code for cursive attachment (curs).

    Scans glyphs_def for cursive_entry / cursive_exit anchors and emits
    a GPOS 'curs' feature with separate lookups grouped by Y value.
    This prevents cross-pair attachment between glyphs at different heights.

    Pixel coordinates: x in pixels from x=0 of the glyph coordinate space,
    y in pixels from baseline (0 = baseline, positive = up).

    Returns the FEA string, or None if no glyphs declare cursive anchors.
    """
    # Group glyphs by Y value: y_groups[y] = list of (glyph_name, entry_anchor, exit_anchor)
    y_groups: dict[int, list[tuple[str, str, str]]] = {}

    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        raw_entry = glyph_def.get("cursive_entry")
        raw_entry_curs = glyph_def.get("cursive_entry_curs_only")
        raw_exit = glyph_def.get("cursive_exit")
        if raw_entry is None and raw_entry_curs is None and raw_exit is None:
            continue
        entries = _normalize_anchors(raw_entry) + _normalize_anchors(raw_entry_curs)
        exits = _normalize_anchors(raw_exit)
        y_values = {a[1] for a in entries} | {a[1] for a in exits}
        for y in y_values:
            entry_anchor = "<anchor NULL>"
            exit_anchor = "<anchor NULL>"
            for a in entries:
                if a[1] == y:
                    entry_anchor = f"<anchor {a[0] * pixel_width} {a[1] * pixel_height}>"
                    break
            for a in exits:
                if a[1] == y:
                    exit_anchor = f"<anchor {a[0] * pixel_width} {a[1] * pixel_height}>"
                    break
            y_groups.setdefault(y, []).append((glyph_name, entry_anchor, exit_anchor))

    if not y_groups:
        return None

    # Add .noentry glyphs (no cursive_exit) to the Y-groups they need to
    # appear in so they break the cursive chain.  .noentry glyphs WITH
    # cursive_exit are already picked up by the normal scan above.
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue
        if not glyph_def.get("_is_noentry", glyph_name.endswith(".noentry")):
            continue
        if glyph_def.get("cursive_exit"):
            continue
        original_name = glyph_def.get("_noentry_for")
        if not original_name:
            continue
        original_def = glyphs_def.get(original_name)
        if not original_def:
            continue
        for anchor in _normalize_anchors(original_def.get("cursive_entry")):
            y = anchor[1]
            y_groups.setdefault(y, []).append(
                (glyph_name, "<anchor NULL>", "<anchor NULL>")
            )

    lines = ["feature curs {"]
    for y in sorted(y_groups):
        lines.append(f"    lookup cursive_y{y} {{")
        for glyph_name, entry_anchor, exit_anchor in sorted(y_groups[y]):
            lines.append(f"        pos cursive {glyph_name} {entry_anchor} {exit_anchor};")
        lines.append(f"    }} cursive_y{y};")
    lines.append("} curs;")
    return "\n".join(lines)


def generate_noentry_variants(glyphs_def: dict) -> dict:
    """Create .noentry glyph variants for ZWNJ chain-breaking.

    For each base glyph with cursive_entry (no dot in name), creates a copy
    without cursive_entry.  These are substituted by calt when ZWNJ precedes
    them, so the curs feature sees NULL entry and breaks the chain.
    """
    if "uni200C" not in glyphs_def:
        return {}

    variants = {}
    for name, gdef in sorted(glyphs_def.items()):
        if gdef is None:
            continue
        if _seeded_modifiers(name, gdef):
            continue
        if not gdef.get("cursive_entry"):
            continue
        seed_context = _generated_variant_seed_context(name, gdef)
        noentry_def = {k: v for k, v in gdef.items() if k not in ("cursive_entry", "extend_entry_after", "extend_exit_before")}
        noentry_def["_noentry_for"] = name
        variant_name = name + ".noentry"
        _stamp_compiled_glyph_seed(
            noentry_def,
            output_name=variant_name,
            base_name=seed_context["base_name"],
            family_name=seed_context["family_name"],
            sequence=seed_context["sequence"],
            traits=seed_context["traits"],
            contextual=True,
            modifiers=seed_context["modifiers"] + ("noentry",),
            is_noentry=True,
        )
        variants[variant_name] = noentry_def
    return variants


def _shift_anchor(entry, dx=-1):
    if isinstance(entry[0], list):
        return [[x + dx, y] for x, y in entry]
    return [entry[0] + dx, entry[1]]


def _widen_bitmap_with_connector(bitmap, entry_y, y_offset=0, count=1):
    """Widen bitmap by `count` pixels on the left, adding connecting pixels at entry_y."""
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = entry_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap = []
    for i, row in enumerate(bitmap):
        prefix = "#" * count if i == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(prefix + row)
        else:
            new_bitmap.append([int(c == "#") for c in prefix] + list(row))
    return new_bitmap


def _widen_bitmap_right_with_connector(bitmap, exit_y, y_offset=0, count=1):
    """Widen bitmap by `count` pixels on the right, adding connecting pixels at exit_y."""
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = exit_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap = []
    for i, row in enumerate(bitmap):
        suffix = "#" * count if i == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(row + suffix)
        else:
            new_bitmap.append(list(row) + [int(c == "#") for c in suffix])
    return new_bitmap


_CALT_KEYS = frozenset((
    "calt_after", "calt_before", "calt_not_after", "calt_not_before",
    "calt_word_final", "extend_entry_after", "extend_exit_before",
    "doubly_extend_entry_after", "doubly_extend_exit_before",
    "noentry_after", "reverse_upgrade_from",
))


def _generated_variant_seed_context(glyph_name: str, glyph_def: dict) -> dict:
    return {
        "base_name": _seeded_base_name(glyph_name, glyph_def),
        "family_name": glyph_def.get("_family"),
        "sequence": glyph_def.get("_sequence"),
        "traits": tuple(glyph_def.get("_traits", ())),
        "modifiers": _seeded_modifiers(glyph_name, glyph_def),
    }


def _generate_extended_entry_variants(
    glyphs_def: dict, *, count: int, yaml_key: str, suffix_word: str,
) -> dict:
    """Create entry-extended variants for glyphs with the given yaml_key.

    For each glyph with the yaml_key, creates a copy whose bitmap is widened
    by `count` pixels on the left with a connecting pixel at the entry y-coordinate.
    The entry anchor stays unchanged; the exit anchor (if any) shifts right.

    Also generates extended versions of related glyphs (exit variants, ligatures)
    so that forward substitutions and ligatures preserve the extension.
    """
    variants = {}
    for name, gdef in sorted(glyphs_def.items()):
        if gdef is None:
            continue
        extend_after = gdef.get(yaml_key)
        if not extend_after:
            continue
        entry = gdef.get("cursive_entry")
        if not entry:
            continue
        seed_context = _generated_variant_seed_context(name, gdef)

        entries = _normalize_anchors(entry)
        multi = len(entries) > 1

        if multi:
            for anchor in entries:
                y = anchor[1]
                label = _EXTENDED_HEIGHT_LABELS.get(y, f"y{y}")
                modifier = f"entry-{suffix_word}-at-{label}"
                ext_name = f"{name}.entry-{suffix_word}-at-{label}"
                if ext_name not in glyphs_def:
                    variant_def = {k: v for k, v in gdef.items() if k != yaml_key}
                    variant_def["cursive_entry"] = [anchor[0], anchor[1]]
                    variant_def["bitmap"] = _widen_bitmap_with_connector(
                        variant_def["bitmap"], anchor[1], variant_def.get("y_offset", 0), count=count
                    )
                    if "cursive_exit" in variant_def:
                        variant_def["cursive_exit"] = _shift_anchor(variant_def["cursive_exit"], dx=count)
                    variant_def["calt_after"] = list(extend_after)
                    _stamp_compiled_glyph_seed(
                        variant_def,
                        output_name=ext_name,
                        base_name=seed_context["base_name"],
                        family_name=seed_context["family_name"],
                        sequence=seed_context["sequence"],
                        traits=seed_context["traits"],
                        contextual=True,
                        modifiers=seed_context["modifiers"] + (modifier,),
                    )
                    variants[ext_name] = variant_def
        else:
            modifier = f"entry-{suffix_word}"
            ext_name = f"{name}.entry-{suffix_word}"
            if ext_name not in glyphs_def:
                variant_def = {k: v for k, v in gdef.items() if k != yaml_key}
                variant_def["bitmap"] = _widen_bitmap_with_connector(
                    variant_def["bitmap"], entries[0][1], variant_def.get("y_offset", 0), count=count
                )
                if "cursive_exit" in variant_def:
                    variant_def["cursive_exit"] = _shift_anchor(variant_def["cursive_exit"], dx=count)
                variant_def["calt_after"] = list(extend_after)
                _stamp_compiled_glyph_seed(
                    variant_def,
                    output_name=ext_name,
                    base_name=seed_context["base_name"],
                    family_name=seed_context["family_name"],
                    sequence=seed_context["sequence"],
                    traits=seed_context["traits"],
                    contextual=True,
                    modifiers=seed_context["modifiers"] + (modifier,),
                )
                variants[ext_name] = variant_def

        base_name = seed_context["base_name"]
        for other_name, other_gdef in sorted(glyphs_def.items()):
            if other_gdef is None or other_name == name:
                continue
            if _seeded_extended_entry_suffix(other_name, other_gdef) is not None:
                continue
            other_seed_context = _generated_variant_seed_context(other_name, other_gdef)
            other_base = other_seed_context["base_name"]
            other_sequence = tuple(other_seed_context["sequence"] or ())
            is_variant = other_base == base_name and bool(other_seed_context["modifiers"])
            is_ligature = bool(other_sequence) and other_sequence[0] == base_name
            if not (is_variant or is_ligature):
                continue
            other_entry = other_gdef.get("cursive_entry")
            if not other_entry:
                continue
            if multi:
                other_entries = _normalize_anchors(other_entry)
                for anchor in other_entries:
                    y = anchor[1]
                    label = _EXTENDED_HEIGHT_LABELS.get(y, f"y{y}")
                    modifier = f"entry-{suffix_word}-at-{label}"
                    sec_name = f"{other_name}.entry-{suffix_word}-at-{label}"
                    if sec_name not in glyphs_def:
                        extended = {k: v for k, v in other_gdef.items() if k not in _CALT_KEYS}
                        extended["cursive_entry"] = [anchor[0], anchor[1]]
                        extended["bitmap"] = _widen_bitmap_with_connector(
                            extended["bitmap"], anchor[1], extended.get("y_offset", 0), count=count
                        )
                        if "cursive_exit" in extended:
                            extended["cursive_exit"] = _shift_anchor(extended["cursive_exit"], dx=count)
                        _stamp_compiled_glyph_seed(
                            extended,
                            output_name=sec_name,
                            base_name=other_base,
                            family_name=other_seed_context["family_name"],
                            sequence=other_seed_context["sequence"],
                            traits=other_seed_context["traits"],
                            contextual=True,
                            modifiers=other_seed_context["modifiers"] + (modifier,),
                        )
                        variants[sec_name] = extended
            else:
                modifier = f"entry-{suffix_word}"
                sec_name = f"{other_name}.entry-{suffix_word}"
                if sec_name not in glyphs_def:
                    extended = {k: v for k, v in other_gdef.items() if k not in _CALT_KEYS}
                    other_entries_norm = _normalize_anchors(other_entry)
                    extended["bitmap"] = _widen_bitmap_with_connector(
                        extended["bitmap"], other_entries_norm[0][1], extended.get("y_offset", 0), count=count
                    )
                    if "cursive_exit" in extended:
                        extended["cursive_exit"] = _shift_anchor(extended["cursive_exit"], dx=count)
                    _stamp_compiled_glyph_seed(
                        extended,
                        output_name=sec_name,
                        base_name=other_base,
                        family_name=other_seed_context["family_name"],
                        sequence=other_seed_context["sequence"],
                        traits=other_seed_context["traits"],
                        contextual=True,
                        modifiers=other_seed_context["modifiers"] + (modifier,),
                    )
                    variants[sec_name] = extended

    return variants


def _generate_extended_exit_variants(
    glyphs_def: dict, *, count: int, yaml_key: str, suffix_word: str,
) -> dict:
    """Create exit-extended variants for glyphs with the given yaml_key.

    For each glyph with the yaml_key, creates a copy whose bitmap is widened
    by `count` pixels on the right with a connecting pixel at the exit y-coordinate.
    The exit anchor shifts right; the entry anchor stays unchanged.

    Also generates extended versions of related glyphs (entry variants, ligatures)
    so that substitutions preserve the extension.
    """
    variants = {}
    for name, gdef in sorted(glyphs_def.items()):
        if gdef is None:
            continue
        extend_before = gdef.get(yaml_key)
        if not extend_before:
            continue
        raw_exit = gdef.get("cursive_exit")
        if not raw_exit:
            continue
        seed_context = _generated_variant_seed_context(name, gdef)

        exits = _normalize_anchors(raw_exit)
        exit_y = exits[0][1]

        modifier = f"exit-{suffix_word}"
        ext_name = f"{name}.exit-{suffix_word}"
        if ext_name not in glyphs_def:
            skip_keys = {yaml_key, "extend_exit_no_entry"}
            variant_def = {k: v for k, v in gdef.items() if k not in skip_keys}
            variant_def["bitmap"] = _widen_bitmap_right_with_connector(
                variant_def["bitmap"], exit_y, variant_def.get("y_offset", 0), count=count
            )
            variant_def["cursive_exit"] = _shift_anchor(variant_def["cursive_exit"], dx=count)
            variant_def["calt_before"] = list(extend_before)
            if gdef.get("extend_exit_no_entry"):
                variant_def.pop("cursive_entry", None)
            _stamp_compiled_glyph_seed(
                variant_def,
                output_name=ext_name,
                base_name=seed_context["base_name"],
                family_name=seed_context["family_name"],
                sequence=seed_context["sequence"],
                traits=seed_context["traits"],
                contextual=True,
                modifiers=seed_context["modifiers"] + (modifier,),
            )
            variants[ext_name] = variant_def

        base_name = seed_context["base_name"]
        for other_name, other_gdef in sorted(glyphs_def.items()):
            if other_gdef is None or other_name == name:
                continue
            if _seeded_extended_exit_suffix(other_name, other_gdef) is not None:
                continue
            other_seed_context = _generated_variant_seed_context(other_name, other_gdef)
            other_base = other_seed_context["base_name"]
            other_sequence = tuple(other_seed_context["sequence"] or ())
            is_variant = other_base == base_name and bool(other_seed_context["modifiers"])
            is_ligature = bool(other_sequence) and other_sequence[0] == base_name
            if not (is_variant or is_ligature):
                continue
            other_exit = other_gdef.get("cursive_exit")
            if not other_exit:
                continue
            sec_name = f"{other_name}.exit-{suffix_word}"
            if sec_name not in glyphs_def:
                extended = {k: v for k, v in other_gdef.items() if k not in _CALT_KEYS}
                other_exits_norm = _normalize_anchors(other_exit)
                extended["bitmap"] = _widen_bitmap_right_with_connector(
                    extended["bitmap"], other_exits_norm[0][1], extended.get("y_offset", 0), count=count
                )
                extended["cursive_exit"] = _shift_anchor(extended["cursive_exit"], dx=count)
                _stamp_compiled_glyph_seed(
                    extended,
                    output_name=sec_name,
                    base_name=other_base,
                    family_name=other_seed_context["family_name"],
                    sequence=other_seed_context["sequence"],
                    traits=other_seed_context["traits"],
                    contextual=True,
                    modifiers=other_seed_context["modifiers"] + (modifier,),
                )
                variants[sec_name] = extended

    return variants


def generate_extended_entry_variants(glyphs_def: dict) -> dict:
    return _generate_extended_entry_variants(
        glyphs_def, count=1, yaml_key="extend_entry_after", suffix_word="extended",
    )


def generate_extended_exit_variants(glyphs_def: dict) -> dict:
    return _generate_extended_exit_variants(
        glyphs_def, count=1, yaml_key="extend_exit_before", suffix_word="extended",
    )


def generate_doubly_extended_entry_variants(glyphs_def: dict) -> dict:
    return _generate_extended_entry_variants(
        glyphs_def, count=2, yaml_key="doubly_extend_entry_after", suffix_word="doubly-extended",
    )


def generate_doubly_extended_exit_variants(glyphs_def: dict) -> dict:
    return _generate_extended_exit_variants(
        glyphs_def, count=2, yaml_key="doubly_extend_exit_before", suffix_word="doubly-extended",
    )


def parse_bitmap(bitmap: list) -> list[list[int]]:
    """
    Convert bitmap to a 2D array of 0s and 1s.
    Accepts either string rows ("#" = on) or int arrays.
    """
    if not bitmap:
        return []

    if isinstance(bitmap[0], str):
        return [
            [1 if c == '#' or c == '1' else 0 for c in row]
            for row in bitmap
        ]
    return bitmap


def bitmap_to_rectangles(
    bitmap: list[list[int]],
    pixel_width: int,
    pixel_height: int,
    y_offset: int = 0,
) -> list[tuple[int, int, int, int]]:
    """
    Convert a 2D bitmap array to a list of rectangle coordinates.

    Args:
        bitmap: 2D array of 0s and 1s
        pixel_width: width of each pixel in font units
        pixel_height: height of each pixel in font units
        y_offset: vertical offset in pixels (negative for descenders)
                  0 = bottom of bitmap on baseline
                  -3 = bottom of bitmap is 3 pixels below baseline

    Returns list of (x, y, width, height) tuples for each "on" pixel.
    Coordinates are in font units, with y=0 at baseline.
    """
    rectangles = []
    height = len(bitmap)

    for row_idx, row in enumerate(bitmap):
        # Flip y-axis: bitmap row 0 is top, font y increases upward
        # y_offset shifts the whole glyph (negative = below baseline)
        y = (y_offset + height - 1 - row_idx) * pixel_height

        for col_idx, pixel in enumerate(row):
            if pixel:  # Pixel is "on"
                x = col_idx * pixel_width
                rectangles.append((x, y, pixel_width, pixel_height))

    return rectangles


def draw_rectangles_to_glyph(rectangles: list[tuple], glyph_set):
    """
    Draw rectangles as a TrueType glyph using T2CharStringPen.
    Returns a T2CharString for CFF/OTF fonts.
    """
    pen = T2CharStringPen(width=0, glyphSet=glyph_set)

    for x, y, w, h in rectangles:
        # Draw counter-clockwise for CFF (outer contour)
        pen.moveTo((x, y))
        pen.lineTo((x, y + h))
        pen.lineTo((x + w, y + h))
        pen.lineTo((x + w, y))
        pen.closePath()

    return pen.getCharString()


def compose_bitmaps(
    base_bitmap: list[list[int]],
    base_y_offset: int,
    accent_bitmap: list[list[int]],
    mark_y: int,
    is_top: bool,
    accent_x_adjust: int = 0,
) -> tuple[list[list[int]], int]:
    """
    Overlay an accent bitmap onto a base bitmap.

    Args:
        base_bitmap: Parsed 2D bitmap of the base glyph
        base_y_offset: y_offset of the base glyph (negative for descenders)
        accent_bitmap: Parsed 2D bitmap of the accent glyph
        mark_y: The anchor y position (in pixels above baseline) from the base glyph
        is_top: True for top accents, False for bottom accents

    Returns:
        (combined_bitmap, combined_y_offset) where combined_bitmap is the
        merged 2D array and combined_y_offset is the y_offset for the result.
    """
    base_h = len(base_bitmap)
    accent_h = len(accent_bitmap)
    base_w = max((len(row) for row in base_bitmap), default=0)
    accent_w = max((len(row) for row in accent_bitmap), default=0)

    canvas_w = max(base_w, accent_w)

    # Base occupies pixel rows [base_y_offset, base_y_offset + base_h)
    # (in font-pixel coordinates where 0 = baseline, positive = up)
    base_bottom = base_y_offset
    base_top = base_y_offset + base_h

    if is_top:
        # Top accent: its bottom edge sits at mark_y
        accent_bottom = mark_y
        accent_top = mark_y + accent_h
    else:
        # Bottom accent: its top edge sits at mark_y, extending downward
        accent_top = mark_y
        accent_bottom = mark_y - accent_h

    # Combined extent in font-pixel coordinates
    combined_bottom = min(base_bottom, accent_bottom)
    combined_top = max(base_top, accent_top)
    combined_h = combined_top - combined_bottom

    # Build canvas (row 0 = top of combined glyph)
    canvas = [[0] * canvas_w for _ in range(combined_h)]

    def blit(bitmap, bm_w, bm_bottom, x_adjust=0):
        """Blit a bitmap onto the canvas, centered horizontally."""
        x_off = (canvas_w - bm_w) // 2 + x_adjust
        bm_h = len(bitmap)
        for row_idx, row in enumerate(bitmap):
            # bitmap row 0 is top; font-pixel y for this row:
            pixel_y = bm_bottom + bm_h - 1 - row_idx
            # canvas row for this pixel_y:
            canvas_row = combined_top - 1 - pixel_y
            for col_idx, val in enumerate(row):
                if val:
                    canvas[canvas_row][x_off + col_idx] = 1

    blit(base_bitmap, base_w, base_bottom)
    blit(accent_bitmap, accent_w, accent_bottom, accent_x_adjust)

    return canvas, combined_bottom


def resolve_composite(
    glyph_name: str,
    glyph_def: dict,
    glyphs_def: dict,
    is_proportional: bool,
) -> tuple[list[list[int]], int]:
    """
    Resolve a composite glyph definition into a bitmap and y_offset.

    Args:
        glyph_name: Name of the composite glyph (for error messages)
        glyph_def: The composite glyph definition (has 'base', 'top'/'bottom')
        glyphs_def: All glyph definitions (to look up base and accent)
        is_proportional: Whether building the proportional variant

    Returns:
        (bitmap, y_offset) for the resolved composite
    """
    base_name = glyph_def["base"]

    # Try .prop variant first if building proportional font
    if is_proportional and base_name + ".prop" in glyphs_def:
        base_ref = base_name + ".prop"
    else:
        base_ref = base_name

    base_glyph = glyphs_def.get(base_ref)
    if base_glyph is None:
        raise ValueError(
            f"Composite glyph '{glyph_name}' references base '{base_name}' which doesn't exist"
        )
    base_bitmap = parse_bitmap(base_glyph.get("bitmap", []))
    base_y_offset = base_glyph.get("y_offset", 0)

    result_bitmap = base_bitmap
    result_y_offset = base_y_offset

    # Build mapping from spacing accent bitmap identity -> adjustments
    # (YAML aliases make the combining mark's bitmap the same object as the
    # spacing accent's bitmap, so we can use 'is' for matching)
    accent_x_adjusts = {}
    accent_y_adjusts = {}
    for gn, gd in glyphs_def.items():
        if not gd.get("is_mark"):
            continue
        bitmap_obj = gd.get("bitmap")
        if bitmap_obj is None:
            continue
        if "base_x_adjust" in gd:
            accent_x_adjusts[id(bitmap_obj)] = gd["base_x_adjust"]
        if "base_y_adjust" in gd:
            accent_y_adjusts[id(bitmap_obj)] = gd["base_y_adjust"]

    if "top" in glyph_def:
        accent_name = glyph_def["top"]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references top accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get("top_mark_y")
        if mark_y is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' needs top_mark_y on base '{base_ref}'"
            )
        bitmap_id = id(accent_glyph.get("bitmap"))
        accent_x_adjust = accent_x_adjusts.get(bitmap_id, {}).get(base_name, 0)
        accent_y_adjust = accent_y_adjusts.get(bitmap_id, {}).get(base_name, 0)
        result_bitmap, result_y_offset = compose_bitmaps(
            result_bitmap, result_y_offset, accent_bitmap,
            mark_y + accent_y_adjust, is_top=True,
            accent_x_adjust=accent_x_adjust,
        )

    if "bottom" in glyph_def:
        accent_name = glyph_def["bottom"]
        if is_proportional and accent_name + ".prop" in glyphs_def:
            accent_ref = accent_name + ".prop"
        else:
            accent_ref = accent_name
        accent_glyph = glyphs_def.get(accent_ref)
        if accent_glyph is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' references bottom accent '{accent_name}' which doesn't exist"
            )
        accent_bitmap = parse_bitmap(accent_glyph.get("bitmap", []))
        mark_y = base_glyph.get("bottom_mark_y")
        if mark_y is None:
            raise ValueError(
                f"Composite glyph '{glyph_name}' needs bottom_mark_y on base '{base_ref}'"
            )
        bitmap_id = id(accent_glyph.get("bitmap"))
        accent_x_adjust = accent_x_adjusts.get(bitmap_id, {}).get(base_name, 0)
        accent_y_adjust = accent_y_adjusts.get(bitmap_id, {}).get(base_name, 0)
        result_bitmap, result_y_offset = compose_bitmaps(
            result_bitmap, result_y_offset, accent_bitmap,
            mark_y + accent_y_adjust, is_top=False,
            accent_x_adjust=accent_x_adjust,
        )

    return result_bitmap, result_y_offset


def build_font(
    glyph_data: dict,
    output_path: Path | None = None,
    variant: str = "mono",
    pixel_width: int | None = None,
):
    """
    Build font from glyph data dictionary.
    Creates a CFF-based OpenType font (.otf).

    Args:
        glyph_data: Dictionary containing metadata and glyph definitions
        output_path: Path to write the font file (None to skip saving)
        variant: "mono", "junior", or "senior"
        pixel_width: Width of each pixel in font units. Defaults to
                     metadata["pixel_size"]. Height is always metadata["pixel_size"].

    Returns:
        The built TTFont object.
    """
    metadata = glyph_data.get("metadata", {})
    is_proportional = variant != "mono"
    is_senior = variant == "senior"
    glyphs_def = compile_glyph_definitions(glyph_data, variant)

    # Font name differs per variant
    base_font_name = metadata["font_name"]
    suffixes = {"mono": " Mono", "junior": " Sans Junior", "senior": " Sans Senior"}
    font_name = base_font_name + suffixes[variant]
    version = metadata["version"]
    units_per_em = metadata["units_per_em"]
    pixel_height = metadata["pixel_size"]
    if pixel_width is None:
        pixel_width = pixel_height
    ascender = metadata["ascender"]
    descender = metadata["descender"]
    cap_height = metadata["cap_height"]
    x_height = metadata["x_height"]

    # Build glyph order (must include .notdef first)
    # For mono font, exclude .prop glyphs entirely
    glyph_names = [
        name for name in glyphs_def.keys()
        if name not in (".notdef", "space")
        and (is_proportional or (not is_proportional_glyph(name) and not _is_contextual_variant(name)))
        and (is_senior or not _is_contextual_variant(name))
    ]
    postscript_glyph_names = load_postscript_glyph_names()

    name_to_codepoint = {}
    for name in glyphs_def:
        cp = _resolve_codepoint(name, postscript_glyph_names)
        if cp is not None:
            name_to_codepoint[name] = cp

    def _sort_key(name):
        cp = name_to_codepoint.get(name)
        if cp is not None:
            return (cp, name)
        base = name.split(".")[0]
        if "_" in base:
            base = base.split("_")[0]
        cp = name_to_codepoint.get(base)
        if cp is not None:
            return (cp, name)
        return (float('inf'), name)

    glyph_order = [".notdef", "space"] + sorted(glyph_names, key=_sort_key)

    # Build character map (Unicode codepoint -> glyph name)
    # Exclude .prop glyphs - they have no direct Unicode mapping
    cmap = {32: "space"}  # Always include space
    for glyph_name, glyph_def in glyphs_def.items():
        if is_proportional_glyph(glyph_name):
            continue  # Proportional variants are accessed via ss01, not cmap
        if len(glyph_name) == 1:
            cmap[ord(glyph_name)] = glyph_name
        elif glyph_name.startswith("uni") and len(glyph_name) == 7:
            # Handle uniXXXX naming convention (4 hex digits)
            try:
                codepoint = int(glyph_name[3:], 16)
                cmap[codepoint] = glyph_name
            except ValueError:
                pass  # Not a valid hex code, skip
        elif glyph_name.startswith("u") and not glyph_name.startswith("uni") and len(glyph_name) == 6:
            try:
                codepoint = int(glyph_name[1:], 16)
                if codepoint > 0xFFFF:
                    cmap[codepoint] = glyph_name
            except ValueError:
                pass
        elif glyph_name in postscript_glyph_names:
            cmap[postscript_glyph_names[glyph_name]] = glyph_name

    # Initialize FontBuilder for CFF-based OTF
    fb = FontBuilder(units_per_em, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)

    # Build charstrings and metrics
    charstrings = {}
    metrics = {}

    # Placeholder glyph set for pen (not strictly needed for simple drawing)
    class GlyphSet:
        pass
    glyph_set = GlyphSet()

    # Standard monospace width: 7 pixels (bitmap 5 + 2 spacing)
    mono_width = 7 * pixel_width

    # Create .notdef glyph (simple rectangle, sized to fit mono_width)
    pen = T2CharStringPen(width=mono_width, glyphSet=glyph_set)
    pen.moveTo((pixel_width, 0))
    pen.lineTo((pixel_width, 5 * pixel_height))
    pen.lineTo((5 * pixel_width, 5 * pixel_height))
    pen.lineTo((5 * pixel_width, 0))
    pen.closePath()
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (mono_width, pixel_width)

    # Create space glyph (empty)
    space_def = glyphs_def.get("space", {})
    space_width = space_def["advance_width"] * pixel_width
    pen = T2CharStringPen(width=space_width, glyphSet=glyph_set)
    charstrings["space"] = pen.getCharString()
    metrics["space"] = (space_width, 0)

    # Create all other glyphs
    for glyph_name in glyph_order:
        if glyph_name in (".notdef", "space"):
            continue

        glyph_def = glyphs_def.get(glyph_name, {})

        # Handle composite glyphs (have 'base' key, no 'bitmap')
        if "base" in glyph_def and not glyph_def.get("bitmap"):
            composed_bitmap, composed_y_offset = resolve_composite(
                glyph_name, glyph_def, glyphs_def, is_proportional
            )
            # Inject the resolved bitmap so the rest of the loop handles it normally
            glyph_def = dict(glyph_def)
            glyph_def["bitmap"] = composed_bitmap
            glyph_def["y_offset"] = composed_y_offset
            # Remove 'base'/'top'/'bottom' so they don't confuse later logic
            glyph_def.pop("base", None)
            glyph_def.pop("top", None)
            glyph_def.pop("bottom", None)

        bitmap = glyph_def.get("bitmap", [])

        # Validate bitmap width
        # In proportional font, all glyphs use proportional validation
        # In monospace font, only .prop suffixed glyphs use proportional validation
        is_prop_glyph = is_proportional or is_proportional_glyph(glyph_name)
        if bitmap:
            if is_prop_glyph:
                # Proportional glyphs: all rows must have consistent width
                row_widths = [len(row) for row in bitmap]
                if len(set(row_widths)) > 1:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}"
                    )
            else:
                # Monospace glyphs: check width requirements
                base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
                is_quikscript_glyph = base_name.startswith("uniE6") or base_name.startswith("qs")
                if is_quikscript_glyph:
                    # Quikscript glyphs: all rows must be exactly 5 characters wide
                    for row_idx, row in enumerate(bitmap):
                        row_len = len(row)
                        if row_len != 5:
                            raise ValueError(
                                f"Glyph '{glyph_name}' row {row_idx} has width {row_len}, expected 5"
                            )
                else:
                    # Non-Quikscript glyphs: all rows must have consistent width
                    row_widths = [len(row) for row in bitmap]
                    if len(set(row_widths)) > 1:
                        raise ValueError(
                            f"Glyph '{glyph_name}' has inconsistent row widths: {row_widths}"
                        )

        if not bitmap:
            # Empty glyph — use explicit advance_width if set, else mono_width
            width = glyph_def.get("advance_width")
            if width is not None:
                width = int(width * pixel_width)
            else:
                width = mono_width
            pen = T2CharStringPen(width=width, glyphSet=glyph_set)
            charstrings[glyph_name] = pen.getCharString()
            metrics[glyph_name] = (width, 0)
            continue

        # Parse and convert bitmap
        bitmap = parse_bitmap(bitmap)
        y_offset = glyph_def.get("y_offset", 0)  # negative for descenders

        # Validate bitmap height
        row_count = len(bitmap)

        # Check if this is a Quikscript glyph (uniE6xx or uniE6xx.prop)
        base_name = glyph_name.split(".")[0] if "." in glyph_name else glyph_name
        is_quikscript = base_name.startswith("uniE6") or base_name.startswith("qs")

        if is_quikscript:
            # Strict height validation for Quikscript glyphs
            if glyph_name in ("uniE66E", "uniE66F", "qsAngleParenLeft", "qsAngleParenRight"):
                if row_count != 12:
                    raise ValueError(
                        f"Glyph '{glyph_name}' has {row_count} rows, expected 12 (angled parenthesis)"
                    )
            elif y_offset == -3:
                if row_count not in (9, 12):
                    raise ValueError(
                        f"Glyph '{glyph_name}' has y_offset=-3 but bitmap has {row_count} rows, expected 9 or 12"
                    )
            elif row_count not in (6, 9):
                raise ValueError(
                    f"Glyph '{glyph_name}' has {row_count} rows, expected 6 or 9"
                )
        # Non-Quikscript glyphs: no height restrictions

        rectangles = bitmap_to_rectangles(bitmap, pixel_width, pixel_height, y_offset)

        # Calculate advance width
        advance_width = glyph_def.get("advance_width")
        if advance_width is None:
            if is_prop_glyph:
                # Proportional glyphs: bitmap width + 2 pixel spacing
                max_col = max((len(row) for row in bitmap), default=0)
                advance_width = (max_col + 2) * pixel_width
            else:
                # Monospace glyphs: use fixed mono_width
                advance_width = mono_width
        else:
            advance_width *= pixel_width

        # Calculate x_offset: center glyph within advance width
        bitmap_width = max((len(row) for row in bitmap), default=0) * pixel_width
        if advance_width == 0:
            # Zero-width (combining mark): center bitmap on the origin
            x_offset = -(bitmap_width // 2)
        else:
            x_offset = (advance_width - bitmap_width) // 2

        # Calculate left side bearing (LSB) with offset applied
        if advance_width == 0:
            lsb = 0
        elif rectangles:
            lsb = min(r[0] for r in rectangles) + x_offset
        else:
            lsb = x_offset

        # Draw glyph with x_offset applied
        pen = T2CharStringPen(width=advance_width, glyphSet=glyph_set)
        for x, y, w, h in rectangles:
            pen.moveTo((x + x_offset, y))
            pen.lineTo((x + x_offset, y + h))
            pen.lineTo((x + x_offset + w, y + h))
            pen.lineTo((x + x_offset + w, y))
            pen.closePath()

        charstrings[glyph_name] = pen.getCharString()
        metrics[glyph_name] = (advance_width, lsb)

    # Setup CFF table
    ps_name = font_name.replace(" ", "") + "-Regular"
    fb.setupCFF(
        psName=ps_name,
        fontInfo={"FamilyName": font_name, "FullName": f"{font_name} Regular"},
        charStringsDict=charstrings,
        privateDict={}
    )

    # Setup horizontal metrics
    fb.setupHorizontalMetrics(metrics)

    # Setup horizontal header
    fb.setupHorizontalHeader(ascent=ascender, descent=descender)

    # Setup name table
    name_strings = {
        "familyName": {"en": font_name},
        "styleName": {"en": "Regular"},
        "uniqueFontIdentifier": f"FontBuilder:{font_name}.Regular",
        "fullName": {"en": f"{font_name} Regular"},
        "psName": ps_name,
        "version": f"Version {version}",
    }

    if "copyright" in metadata:
        copyright_str = metadata["copyright"]
        if "© " in copyright_str:
            year = datetime.now().year
            copyright_str = copyright_str.replace("© ", f"© {year} ", 1)
        name_strings["copyright"] = {"en": copyright_str}
    if "license" in metadata:
        name_strings["licenseDescription"] = {"en": metadata["license"]}
    if "license_url" in metadata:
        name_strings["licenseInfoURL"] = {"en": metadata["license_url"]}
    if "sample_text" in metadata:
        name_strings["sampleText"] = {"en": metadata["sample_text"]}
    if "vendor_url" in metadata:
        name_strings["vendorURL"] = {"en": metadata["vendor_url"]}
    if "description" in metadata:
        name_strings["description"] = {"en": metadata["description"]}
    if "designer" in metadata:
        name_strings["designer"] = {"en": metadata["designer"]}
    if "manufacturer" in metadata:
        name_strings["manufacturer"] = {"en": metadata["manufacturer"]}

    fb.setupNameTable(name_strings)

    # Setup OS/2 table
    fb.setupOS2(
        sTypoAscender=ascender,
        sTypoDescender=descender,
        sTypoLineGap=0,
        usWinAscent=ascender,
        usWinDescent=abs(descender),
        sxHeight=x_height,
        sCapHeight=cap_height,
        fsType=0,  # Installable embedding - no restrictions
    )

    # Setup post table
    # Monospace font: isFixedPitch=1, Proportional font: isFixedPitch=0
    fb.setupPost(isFixedPitch=0 if is_proportional else 1)

    # Setup gasp table for pixel-crisp rendering
    gasp = newTable("gasp")
    gasp.gaspRange = {0xFFFF: 0x0001}  # Grid-fit only, no antialiasing
    fb.font["gasp"] = gasp

    # Add head table (required)
    fb.setupHead(unitsPerEm=units_per_em, fontRevision=version)

    vs_defs = metadata.get("variation_sequences", {})
    cmap14 = build_cmap14(vs_defs, glyphs_def, name_to_codepoint)
    if cmap14:
        fb.font["cmap"].tables.append(cmap14)

    # Compile OpenType features into the proportional font only
    fea_code_parts = []

    kerning_defs = glyph_data.get("kerning", {})
    if is_proportional and kerning_defs:
        kerning_groups = collect_kerning_groups(glyphs_def)
        fea_code_parts.append(generate_kern_fea(
            kerning_defs, kerning_groups, list(glyphs_def.keys()), pixel_width
        ))

    if is_proportional:
        ccmp_fea = generate_ccmp_fea(glyphs_def)
        if ccmp_fea:
            fea_code_parts.append(ccmp_fea)

        mark_fea = generate_mark_fea(glyphs_def, pixel_width, pixel_height)
        if mark_fea:
            fea_code_parts.append(mark_fea)

    if is_senior:
        curs_fea = generate_curs_fea(glyphs_def, pixel_width, pixel_height)
        if curs_fea:
            fea_code_parts.append(curs_fea)

    if is_senior:
        calt_fea = generate_calt_fea(glyphs_def, pixel_width)
        if calt_fea:
            fea_code_parts.append(calt_fea)

    fea_code = None
    if fea_code_parts:
        fea_code = "\n\n".join(fea_code_parts)
        addOpenTypeFeaturesFromString(fb.font, fea_code)

        if output_path is not None:
            fea_path = output_path.with_suffix(".fea")
            fea_path.write_text(fea_code + "\n")
            print(f"  Feature code saved to: {fea_path}")

    if output_path is not None:
        fb.save(str(output_path))
        print(f"Font saved to: {output_path}")

    print(f"  Variant: {variant}")
    print(f"  Glyphs: {len(glyph_order)}")
    print(f"  Units per em: {units_per_em}")
    print(f"  Pixel: {pixel_width}×{pixel_height} units")

    font = fb.font
    font._fea_code = fea_code
    return font


def build_variable_font(glyph_data: dict, output_path: Path, variant: str):
    """
    Build a variable font with a wght axis (ExtraLight 200, Regular 400, Bold 800).

    Weight controls pixel_width relative to a constant pixel_height of 50:
      200 → pixel_width=25  (half-wide pixels)
      400 → pixel_width=50  (square pixels)
      800 → pixel_width=100 (2× wide pixels)

    All three masters share the same bitmap data and feature structure,
    differing only in x-coordinates and advance widths.
    """
    from fontTools.designspaceLib import (
        AxisDescriptor,
        DesignSpaceDocument,
        InstanceDescriptor,
        SourceDescriptor,
    )
    from fontTools.varLib import build as varLib_build

    metadata = glyph_data["metadata"]
    pixel_height = metadata["pixel_size"]

    print(f"\nBuilding variable font: {output_path.name}")

    thin = build_font(glyph_data, variant=variant, pixel_width=pixel_height // 2)
    regular = build_font(glyph_data, variant=variant, pixel_width=pixel_height)
    bold = build_font(glyph_data, variant=variant, pixel_width=pixel_height * 2)

    ds = DesignSpaceDocument()

    axis = AxisDescriptor()
    axis.tag = "wght"
    axis.name = "Weight"
    axis.minimum = 200
    axis.default = 400
    axis.maximum = 800
    ds.addAxis(axis)

    src_thin = SourceDescriptor()
    src_thin.font = thin
    src_thin.location = {"Weight": 200}
    ds.addSource(src_thin)

    src_regular = SourceDescriptor()
    src_regular.font = regular
    src_regular.location = {"Weight": 400}
    ds.addSource(src_regular)

    src_bold = SourceDescriptor()
    src_bold.font = bold
    src_bold.location = {"Weight": 800}
    ds.addSource(src_bold)

    for style_name, wght in (("ExtraLight", 200), ("Regular", 400), ("Bold", 800)):
        inst = InstanceDescriptor()
        inst.name = f"{regular['name'].getDebugName(1)} {style_name}"
        inst.familyName = regular["name"].getDebugName(1)
        inst.styleName = style_name
        inst.location = {"Weight": wght}
        ds.addInstance(inst)

    vf, _, _ = varLib_build(ds)

    fea_code = getattr(regular, "_fea_code", None)
    if fea_code:
        fea_path = output_path.with_suffix(".fea")
        fea_path.write_text(fea_code + "\n")
        print(f"  Feature code saved to: {fea_path}")

    vf.save(str(output_path))
    print(f"Variable font saved to: {output_path}")


from quikscript_fea import (
    generate_calt_fea as _generate_calt_fea,
    generate_curs_fea as _generate_curs_fea,
    generate_liga_fea as _generate_liga_fea,
)
from quikscript_ir import (
    build_compiled_glyph_metadata as _build_compiled_glyph_metadata,
    compile_glyph_families as _compile_glyph_families,
    generate_doubly_extended_entry_variants as _generate_doubly_extended_entry_variants,
    generate_doubly_extended_exit_variants as _generate_doubly_extended_exit_variants,
    generate_extended_entry_variants as _generate_extended_entry_variants,
    generate_extended_exit_variants as _generate_extended_exit_variants,
    generate_noentry_variants as _generate_noentry_variants,
)

build_compiled_glyph_metadata = _build_compiled_glyph_metadata
compile_glyph_families = _compile_glyph_families
generate_noentry_variants = _generate_noentry_variants
generate_extended_entry_variants = _generate_extended_entry_variants
generate_extended_exit_variants = _generate_extended_exit_variants
generate_doubly_extended_entry_variants = _generate_doubly_extended_entry_variants
generate_doubly_extended_exit_variants = _generate_doubly_extended_exit_variants
generate_calt_fea = _generate_calt_fea
generate_curs_fea = _generate_curs_fea
generate_liga_fea = _generate_liga_fea


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python tools/build_font.py <glyph_data.yaml|glyph_data/> [output_dir]")
        print("\nOutputs:")
        print("  output_dir/AbbotsMortonSpaceportMono.otf")
        print("  output_dir/AbbotsMortonSpaceportSansJunior.otf  (variable, wght 200-800)")
        print("  output_dir/AbbotsMortonSpaceportSansSenior.otf  (variable, wght 200-800)")
        print("\nExample:")
        print("  uv run python tools/build_font.py glyph_data/ build/")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if len(sys.argv) > 2:
        output_dir = Path(sys.argv[2])
    else:
        output_dir = Path(".")

    if not input_path.exists():
        print(f"Error: Input path not found: {input_path}")
        sys.exit(1)

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    glyph_data = load_glyph_data(input_path)

    # Build monospace font (static, no variable font)
    mono_path = output_dir / "AbbotsMortonSpaceportMono.otf"
    build_font(glyph_data, mono_path, variant="mono")

    # Build proportional Junior font (variable, wght 400-800)
    junior_path = output_dir / "AbbotsMortonSpaceportSansJunior.otf"
    build_variable_font(glyph_data, junior_path, variant="junior")

    # Build proportional Senior font (variable, wght 400-800)
    senior_path = output_dir / "AbbotsMortonSpaceportSansSenior.otf"
    build_variable_font(glyph_data, senior_path, variant="senior")


if __name__ == "__main__":
    main()
