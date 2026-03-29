from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
import re
from typing import Any


Anchor = tuple[int, int]
BitmapRow = str | tuple[int, ...]


@dataclass(frozen=True)
class JoinGlyph:
    name: str
    base_name: str
    family: str | None
    sequence: tuple[str, ...]
    traits: frozenset[str]
    modifiers: tuple[str, ...]
    compat_assertions: frozenset[str]
    entry: tuple[Anchor, ...]
    entry_curs_only: tuple[Anchor, ...]
    exit: tuple[Anchor, ...]
    after: tuple[str, ...]
    before: tuple[str, ...]
    not_after: tuple[str, ...]
    not_before: tuple[str, ...]
    reverse_upgrade_from: tuple[str, ...]
    preferred_over: tuple[str, ...]
    word_final: bool
    is_contextual: bool
    is_entry_variant: bool
    is_exit_variant: bool
    entry_suffix: str | None
    exit_suffix: str | None
    extended_entry_suffix: str | None
    extended_exit_suffix: str | None
    entry_restriction_y: int | None
    is_noentry: bool
    bitmap: tuple[BitmapRow, ...]
    y_offset: int
    advance_width: int | None
    extend_entry_after: tuple[str, ...]
    extend_exit_before: tuple[str, ...]
    doubly_extend_entry_after: tuple[str, ...]
    doubly_extend_exit_before: tuple[str, ...]
    noentry_after: tuple[str, ...]
    extend_exit_no_entry: bool
    noentry_for: str | None = None
    generated_from: str | None = None
    transform_kind: str | None = None

    @property
    def entry_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in self.entry)

    @property
    def all_entry_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in (*self.entry, *self.entry_curs_only))

    @property
    def exit_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in self.exit)

    @property
    def modifier_set(self) -> frozenset[str]:
        return frozenset(self.modifiers)


CompiledGlyphMeta = JoinGlyph


@dataclass(frozen=True)
class JoinTransform:
    kind: str
    source_name: str
    target_name: str
    count: int = 0
    restricted_y: int | None = None
    preserves_entry: bool = True
    preserves_exit: bool = True


_SOURCE_FAMILY_TRAITS = frozenset({"alt", "half"})
_ENTRY_EXIT_MODIFIER_RE = re.compile(
    r"^(?:entry|exit)-[a-z0-9]+(?:-[a-z0-9]+)*(?:-at-[a-z0-9]+)?$"
)
_BEFORE_AFTER_MODIFIER_RE = re.compile(r"^(?:before|after)-[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXTENDED_HEIGHT_LABELS = {0: "baseline", 5: "xheight", 6: "y6", 8: "top"}
_CALT_KEYS = frozenset(
    (
        "calt_after",
        "calt_before",
        "calt_not_after",
        "calt_not_before",
        "calt_word_final",
        "extend_entry_after",
        "extend_exit_before",
        "doubly_extend_entry_after",
        "doubly_extend_exit_before",
        "noentry_after",
        "reverse_upgrade_from",
    )
)


def get_base_glyph_name(prop_glyph_name: str) -> str:
    if prop_glyph_name.endswith(".prop"):
        return prop_glyph_name[:-5]
    if ".prop." in prop_glyph_name:
        return prop_glyph_name.replace(".prop.", ".", 1)
    return prop_glyph_name


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
    traits: Sequence[str] = (),
    modifiers: Sequence[str] = (),
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


def _normalize_anchors(raw) -> list[list[int]]:
    if raw is None:
        return []
    if isinstance(raw[0], list):
        return raw
    return [raw]


def _is_entry_variant(glyph_name: str) -> bool:
    return any(part.startswith("entry-") for part in glyph_name.split(".")[1:])


def _is_exit_variant(glyph_name: str) -> bool:
    return any(part.startswith("exit-") for part in glyph_name.split(".")[1:])


def _extended_entry_suffix(glyph_name: str) -> str | None:
    for part in glyph_name.split(".")[1:]:
        if part.startswith("entry-extended") or part.startswith("entry-doubly-extended"):
            return "." + part
    return None


def _extended_exit_suffix(glyph_name: str) -> str | None:
    for part in glyph_name.split(".")[1:]:
        if part.startswith("exit-extended") or part.startswith("exit-doubly-extended"):
            return "." + part
    return None


def _is_contextual_variant(glyph_name: str) -> bool:
    parts = glyph_name.split(".")[1:]
    return any(
        part.startswith("entry-") or part.startswith("exit-") or part == "half"
        for part in parts
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
    sequence: Sequence[str] | None = None,
    traits: Sequence[str] | frozenset[str] | None = None,
    contextual: bool,
    modifiers: Sequence[str] | None = None,
    is_noentry: bool | None = None,
    generated_from: str | None = None,
    transform_kind: str | None = None,
) -> None:
    glyph_def["_base_name"] = base_name
    if family_name is not None:
        glyph_def["_family"] = family_name
    if sequence is not None:
        if sequence:
            glyph_def["_sequence"] = list(sequence)
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

    resolved_modifiers = tuple(modifiers) if modifiers is not None else tuple(
        _glyph_name_modifiers(output_name)
    )
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
    if generated_from is not None:
        glyph_def["_generated_from"] = generated_from
    else:
        glyph_def.pop("_generated_from", None)
    if transform_kind is not None:
        glyph_def["_transform_kind"] = transform_kind
    else:
        glyph_def.pop("_transform_kind", None)


def _normalize_bitmap(bitmap: Sequence[Any] | None) -> tuple[BitmapRow, ...]:
    if not bitmap:
        return ()
    normalized: list[BitmapRow] = []
    for row in bitmap:
        if isinstance(row, str):
            normalized.append(row)
        else:
            normalized.append(tuple(row))
    return tuple(normalized)


def _materialize_bitmap(bitmap: Sequence[BitmapRow]) -> list[Any]:
    materialized: list[Any] = []
    for row in bitmap:
        if isinstance(row, str):
            materialized.append(row)
        else:
            materialized.append(list(row))
    return materialized


def build_join_glyphs(glyphs_def: dict) -> dict[str, JoinGlyph]:
    metadata: dict[str, JoinGlyph] = {}
    for glyph_name, glyph_def in glyphs_def.items():
        if glyph_def is None:
            continue

        modifiers = _seeded_modifiers(glyph_name, glyph_def)
        traits = frozenset(glyph_def.get("_traits", []))
        metadata[glyph_name] = JoinGlyph(
            name=glyph_name,
            base_name=_seeded_base_name(glyph_name, glyph_def),
            family=glyph_def.get("_family"),
            sequence=tuple(glyph_def.get("_sequence", ())),
            traits=traits,
            modifiers=tuple(modifiers),
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
            bitmap=_normalize_bitmap(glyph_def.get("bitmap", ())),
            y_offset=int(glyph_def.get("y_offset", 0)),
            advance_width=glyph_def.get("advance_width"),
            extend_entry_after=tuple(glyph_def.get("extend_entry_after", ())),
            extend_exit_before=tuple(glyph_def.get("extend_exit_before", ())),
            doubly_extend_entry_after=tuple(
                glyph_def.get("doubly_extend_entry_after", ())
            ),
            doubly_extend_exit_before=tuple(
                glyph_def.get("doubly_extend_exit_before", ())
            ),
            noentry_after=tuple(glyph_def.get("noentry_after", ())),
            extend_exit_no_entry=bool(glyph_def.get("extend_exit_no_entry")),
            noentry_for=glyph_def.get("_noentry_for"),
            generated_from=glyph_def.get("_generated_from"),
            transform_kind=glyph_def.get("_transform_kind"),
        )
    return metadata


def build_compiled_glyph_metadata(glyphs_def: dict) -> dict[str, CompiledGlyphMeta]:
    return build_join_glyphs(glyphs_def)


def _shift_anchors(anchors: tuple[Anchor, ...], *, dx: int = -1) -> tuple[Anchor, ...]:
    return tuple((x + dx, y) for x, y in anchors)


def _widen_bitmap_with_connector(
    bitmap: tuple[BitmapRow, ...],
    entry_y: int,
    y_offset: int = 0,
    count: int = 1,
) -> tuple[BitmapRow, ...]:
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = entry_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap: list[BitmapRow] = []
    for index, row in enumerate(bitmap):
        prefix = "#" * count if index == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(prefix + row)
        else:
            new_bitmap.append(tuple([int(char == "#") for char in prefix] + list(row)))
    return tuple(new_bitmap)


def _widen_bitmap_right_with_connector(
    bitmap: tuple[BitmapRow, ...],
    exit_y: int,
    y_offset: int = 0,
    count: int = 1,
) -> tuple[BitmapRow, ...]:
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = exit_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap: list[BitmapRow] = []
    for index, row in enumerate(bitmap):
        suffix = "#" * count if index == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(row + suffix)
        else:
            new_bitmap.append(tuple(list(row) + [int(char == "#") for char in suffix]))
    return tuple(new_bitmap)


_UNSET = object()


def _materialize_anchor_value(anchors: tuple[Anchor, ...]) -> list[int] | list[list[int]] | None:
    if not anchors:
        return None
    pairs = [[x, y] for x, y in anchors]
    if len(pairs) == 1:
        return pairs[0]
    return pairs


def _set_optional_list(glyph_def: dict[str, Any], key: str, values: Sequence[str]) -> None:
    if values:
        glyph_def[key] = list(values)
    else:
        glyph_def.pop(key, None)


def _set_optional_anchor(
    glyph_def: dict[str, Any],
    key: str,
    anchors: tuple[Anchor, ...],
) -> None:
    value = _materialize_anchor_value(anchors)
    if value is None:
        glyph_def.pop(key, None)
    else:
        glyph_def[key] = value


def _materialize_join_glyph(join_glyph: JoinGlyph) -> dict[str, Any]:
    glyph_def: dict[str, Any] = {
        "bitmap": _materialize_bitmap(join_glyph.bitmap),
    }
    if join_glyph.y_offset:
        glyph_def["y_offset"] = join_glyph.y_offset
    if join_glyph.advance_width is not None:
        glyph_def["advance_width"] = join_glyph.advance_width

    _set_optional_anchor(glyph_def, "cursive_entry", join_glyph.entry)
    _set_optional_anchor(
        glyph_def,
        "cursive_entry_curs_only",
        join_glyph.entry_curs_only,
    )
    _set_optional_anchor(glyph_def, "cursive_exit", join_glyph.exit)
    _set_optional_list(glyph_def, "calt_after", join_glyph.after)
    _set_optional_list(glyph_def, "calt_before", join_glyph.before)
    _set_optional_list(glyph_def, "calt_not_after", join_glyph.not_after)
    _set_optional_list(glyph_def, "calt_not_before", join_glyph.not_before)
    _set_optional_list(
        glyph_def,
        "reverse_upgrade_from",
        join_glyph.reverse_upgrade_from,
    )
    _set_optional_list(glyph_def, "preferred_over", join_glyph.preferred_over)
    _set_optional_list(
        glyph_def,
        "extend_entry_after",
        join_glyph.extend_entry_after,
    )
    _set_optional_list(
        glyph_def,
        "extend_exit_before",
        join_glyph.extend_exit_before,
    )
    _set_optional_list(
        glyph_def,
        "doubly_extend_entry_after",
        join_glyph.doubly_extend_entry_after,
    )
    _set_optional_list(
        glyph_def,
        "doubly_extend_exit_before",
        join_glyph.doubly_extend_exit_before,
    )
    _set_optional_list(glyph_def, "noentry_after", join_glyph.noentry_after)

    if join_glyph.word_final:
        glyph_def["calt_word_final"] = True
    if join_glyph.extend_exit_no_entry:
        glyph_def["extend_exit_no_entry"] = True
    if join_glyph.noentry_for is not None:
        glyph_def["_noentry_for"] = join_glyph.noentry_for

    _stamp_compiled_glyph_seed(
        glyph_def,
        output_name=join_glyph.name,
        base_name=join_glyph.base_name,
        family_name=join_glyph.family,
        sequence=join_glyph.sequence,
        traits=join_glyph.traits,
        contextual=join_glyph.is_contextual,
        modifiers=join_glyph.modifiers,
        is_noentry=join_glyph.is_noentry,
        generated_from=join_glyph.generated_from,
        transform_kind=join_glyph.transform_kind,
    )
    return glyph_def


def derive_join_glyph(
    source: JoinGlyph,
    *,
    name: str,
    bitmap: tuple[BitmapRow, ...] | object = _UNSET,
    y_offset: int | object = _UNSET,
    entry: tuple[Anchor, ...] | object = _UNSET,
    entry_curs_only: tuple[Anchor, ...] | object = _UNSET,
    exit: tuple[Anchor, ...] | object = _UNSET,
    after: tuple[str, ...] | object = _UNSET,
    before: tuple[str, ...] | object = _UNSET,
    not_after: tuple[str, ...] | object = _UNSET,
    not_before: tuple[str, ...] | object = _UNSET,
    reverse_upgrade_from: tuple[str, ...] | object = _UNSET,
    preferred_over: tuple[str, ...] | object = _UNSET,
    word_final: bool | object = _UNSET,
    extend_entry_after: tuple[str, ...] | object = _UNSET,
    extend_exit_before: tuple[str, ...] | object = _UNSET,
    doubly_extend_entry_after: tuple[str, ...] | object = _UNSET,
    doubly_extend_exit_before: tuple[str, ...] | object = _UNSET,
    noentry_after: tuple[str, ...] | object = _UNSET,
    extend_exit_no_entry: bool | object = _UNSET,
    add_modifiers: Sequence[str] = (),
    contextual: bool = True,
    is_noentry: bool | None = None,
    generated_from: str | None = None,
    transform_kind: str | None = None,
    noentry_for: str | None | object = _UNSET,
) -> JoinGlyph:
    resolved_bitmap = source.bitmap if bitmap is _UNSET else bitmap
    resolved_y_offset = source.y_offset if y_offset is _UNSET else y_offset
    resolved_advance_width = source.advance_width
    resolved_entry = source.entry if entry is _UNSET else entry
    resolved_entry_curs_only = (
        source.entry_curs_only if entry_curs_only is _UNSET else entry_curs_only
    )
    resolved_exit = source.exit if exit is _UNSET else exit
    resolved_after = source.after if after is _UNSET else after
    resolved_before = source.before if before is _UNSET else before
    resolved_not_after = source.not_after if not_after is _UNSET else not_after
    resolved_not_before = source.not_before if not_before is _UNSET else not_before
    resolved_reverse_upgrade_from = (
        source.reverse_upgrade_from
        if reverse_upgrade_from is _UNSET
        else reverse_upgrade_from
    )
    resolved_preferred_over = (
        source.preferred_over if preferred_over is _UNSET else preferred_over
    )
    resolved_word_final = source.word_final if word_final is _UNSET else word_final
    resolved_extend_entry_after = (
        source.extend_entry_after if extend_entry_after is _UNSET else extend_entry_after
    )
    resolved_extend_exit_before = (
        source.extend_exit_before if extend_exit_before is _UNSET else extend_exit_before
    )
    resolved_doubly_extend_entry_after = (
        source.doubly_extend_entry_after
        if doubly_extend_entry_after is _UNSET
        else doubly_extend_entry_after
    )
    resolved_doubly_extend_exit_before = (
        source.doubly_extend_exit_before
        if doubly_extend_exit_before is _UNSET
        else doubly_extend_exit_before
    )
    resolved_noentry_after = source.noentry_after if noentry_after is _UNSET else noentry_after
    resolved_extend_exit_no_entry = (
        source.extend_exit_no_entry
        if extend_exit_no_entry is _UNSET
        else extend_exit_no_entry
    )
    resolved_noentry_for = source.noentry_for if noentry_for is _UNSET else noentry_for

    resolved_modifiers = tuple([*source.modifiers, *add_modifiers])
    resolved_is_noentry = ("noentry" in resolved_modifiers) if is_noentry is None else is_noentry
    compat_assertions = _compat_assertions_from_modifiers(
        list(resolved_modifiers),
        source.traits,
    )

    return replace(
        source,
        name=name,
        modifiers=resolved_modifiers,
        compat_assertions=compat_assertions,
        entry=resolved_entry,
        entry_curs_only=resolved_entry_curs_only,
        exit=resolved_exit,
        after=resolved_after,
        before=resolved_before,
        not_after=resolved_not_after,
        not_before=resolved_not_before,
        reverse_upgrade_from=resolved_reverse_upgrade_from,
        preferred_over=resolved_preferred_over,
        word_final=resolved_word_final,
        is_contextual=contextual,
        is_entry_variant=any(modifier.startswith("entry-") for modifier in resolved_modifiers),
        is_exit_variant=any(modifier.startswith("exit-") for modifier in resolved_modifiers),
        entry_suffix=_entry_suffix_from_modifiers(list(resolved_modifiers)),
        exit_suffix=_exit_suffix_from_modifiers(list(resolved_modifiers)),
        extended_entry_suffix=_extended_entry_suffix_from_modifiers(
            list(resolved_modifiers)
        ),
        extended_exit_suffix=_extended_exit_suffix_from_modifiers(
            list(resolved_modifiers)
        ),
        entry_restriction_y=_entry_restriction_y_from_modifiers(list(resolved_modifiers)),
        is_noentry=resolved_is_noentry,
        bitmap=resolved_bitmap,
        y_offset=resolved_y_offset,
        advance_width=resolved_advance_width,
        extend_entry_after=resolved_extend_entry_after,
        extend_exit_before=resolved_extend_exit_before,
        doubly_extend_entry_after=resolved_doubly_extend_entry_after,
        doubly_extend_exit_before=resolved_doubly_extend_exit_before,
        noentry_after=resolved_noentry_after,
        extend_exit_no_entry=resolved_extend_exit_no_entry,
        noentry_for=resolved_noentry_for,
        generated_from=generated_from,
        transform_kind=transform_kind,
    )


def _record_transform(
    transforms: list[JoinTransform] | None,
    *,
    kind: str,
    source_name: str,
    target_name: str,
    count: int = 0,
    restricted_y: int | None = None,
    preserves_entry: bool = True,
    preserves_exit: bool = True,
) -> None:
    if transforms is None:
        return
    transforms.append(
        JoinTransform(
            kind=kind,
            source_name=source_name,
            target_name=target_name,
            count=count,
            restricted_y=restricted_y,
            preserves_entry=preserves_entry,
            preserves_exit=preserves_exit,
        )
    )


def generate_noentry_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    has_zwnj: bool = False,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    if not has_zwnj:
        return {}

    variants: dict[str, JoinGlyph] = {}
    for name, join_glyph in sorted(join_glyphs.items()):
        if join_glyph.modifiers:
            continue
        if not join_glyph.entry:
            continue
        variant_name = name + ".noentry"
        variants[variant_name] = derive_join_glyph(
            join_glyph,
            name=variant_name,
            entry=(),
            extend_entry_after=(),
            extend_exit_before=(),
            add_modifiers=("noentry",),
            is_noentry=True,
            generated_from=name,
            transform_kind="noentry",
            noentry_for=name,
        )
        _record_transform(
            transforms,
            kind="noentry",
            source_name=name,
            target_name=variant_name,
            preserves_entry=False,
        )
    return variants


def _generate_extended_entry_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    count: int,
    yaml_key: str,
    suffix_word: str,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    variants: dict[str, JoinGlyph] = {}
    for name, join_glyph in sorted(join_glyphs.items()):
        extend_after = getattr(join_glyph, yaml_key)
        if not extend_after:
            continue
        if not join_glyph.entry:
            continue
        entries = join_glyph.entry
        multi = len(entries) > 1

        if multi:
            for anchor in entries:
                y = anchor[1]
                label = _EXTENDED_HEIGHT_LABELS.get(y, f"y{y}")
                modifier = f"entry-{suffix_word}-at-{label}"
                ext_name = f"{name}.entry-{suffix_word}-at-{label}"
                if ext_name not in join_glyphs:
                    variants[ext_name] = derive_join_glyph(
                        join_glyph,
                        name=ext_name,
                        bitmap=_widen_bitmap_with_connector(
                            join_glyph.bitmap,
                            anchor[1],
                            join_glyph.y_offset,
                            count=count,
                        ),
                        entry=(anchor,),
                        exit=_shift_anchors(join_glyph.exit, dx=count),
                        after=tuple(extend_after),
                        extend_entry_after=(),
                        add_modifiers=(modifier,),
                        generated_from=name,
                        transform_kind=f"entry-{suffix_word}",
                    )
                    _record_transform(
                        transforms,
                        kind=f"entry-{suffix_word}",
                        source_name=name,
                        target_name=ext_name,
                        count=count,
                        restricted_y=y,
                        preserves_exit=bool(join_glyph.exit),
                    )
        else:
            modifier = f"entry-{suffix_word}"
            ext_name = f"{name}.entry-{suffix_word}"
            if ext_name not in join_glyphs:
                variants[ext_name] = derive_join_glyph(
                    join_glyph,
                    name=ext_name,
                    bitmap=_widen_bitmap_with_connector(
                        join_glyph.bitmap,
                        entries[0][1],
                        join_glyph.y_offset,
                        count=count,
                    ),
                    exit=_shift_anchors(join_glyph.exit, dx=count),
                    after=tuple(extend_after),
                    extend_entry_after=(),
                    add_modifiers=(modifier,),
                    generated_from=name,
                    transform_kind=f"entry-{suffix_word}",
                )
                _record_transform(
                    transforms,
                        kind=f"entry-{suffix_word}",
                        source_name=name,
                        target_name=ext_name,
                        count=count,
                        preserves_exit=bool(join_glyph.exit),
                )

        base_name = join_glyph.base_name
        for other_name, other_join_glyph in sorted(join_glyphs.items()):
            if other_name == name:
                continue
            if other_join_glyph.extended_entry_suffix is not None:
                continue
            other_base = other_join_glyph.base_name
            other_sequence = other_join_glyph.sequence
            is_variant = other_base == base_name and bool(other_join_glyph.modifiers)
            is_ligature = bool(other_sequence) and other_sequence[0] == base_name
            if not (is_variant or is_ligature):
                continue
            if not other_join_glyph.entry:
                continue
            if multi:
                for anchor in other_join_glyph.entry:
                    y = anchor[1]
                    label = _EXTENDED_HEIGHT_LABELS.get(y, f"y{y}")
                    modifier = f"entry-{suffix_word}-at-{label}"
                    sec_name = f"{other_name}.entry-{suffix_word}-at-{label}"
                    if sec_name not in join_glyphs:
                        variants[sec_name] = derive_join_glyph(
                            other_join_glyph,
                            name=sec_name,
                            bitmap=_widen_bitmap_with_connector(
                                other_join_glyph.bitmap,
                                anchor[1],
                                other_join_glyph.y_offset,
                                count=count,
                            ),
                            entry=(anchor,),
                            exit=_shift_anchors(other_join_glyph.exit, dx=count),
                            after=(),
                            before=(),
                            not_after=(),
                            not_before=(),
                            reverse_upgrade_from=(),
                            word_final=False,
                            extend_entry_after=(),
                            extend_exit_before=(),
                            doubly_extend_entry_after=(),
                            doubly_extend_exit_before=(),
                            noentry_after=(),
                            add_modifiers=(modifier,),
                            generated_from=other_name,
                            transform_kind=f"entry-{suffix_word}",
                        )
                        _record_transform(
                            transforms,
                            kind=f"entry-{suffix_word}",
                            source_name=other_name,
                            target_name=sec_name,
                            count=count,
                            restricted_y=y,
                            preserves_exit=bool(other_join_glyph.exit),
                        )
            else:
                modifier = f"entry-{suffix_word}"
                sec_name = f"{other_name}.entry-{suffix_word}"
                if sec_name not in join_glyphs:
                    variants[sec_name] = derive_join_glyph(
                        other_join_glyph,
                        name=sec_name,
                        bitmap=_widen_bitmap_with_connector(
                            other_join_glyph.bitmap,
                            other_join_glyph.entry[0][1],
                            other_join_glyph.y_offset,
                            count=count,
                        ),
                        exit=_shift_anchors(other_join_glyph.exit, dx=count),
                        after=(),
                        before=(),
                        not_after=(),
                        not_before=(),
                        reverse_upgrade_from=(),
                        word_final=False,
                        extend_entry_after=(),
                        extend_exit_before=(),
                        doubly_extend_entry_after=(),
                        doubly_extend_exit_before=(),
                        noentry_after=(),
                        add_modifiers=(modifier,),
                        generated_from=other_name,
                        transform_kind=f"entry-{suffix_word}",
                    )
                    _record_transform(
                        transforms,
                        kind=f"entry-{suffix_word}",
                        source_name=other_name,
                        target_name=sec_name,
                        count=count,
                        preserves_exit=bool(other_join_glyph.exit),
                    )

    return variants


def _generate_extended_exit_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    count: int,
    yaml_key: str,
    suffix_word: str,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    variants: dict[str, JoinGlyph] = {}
    for name, join_glyph in sorted(join_glyphs.items()):
        extend_before = getattr(join_glyph, yaml_key)
        if not extend_before:
            continue
        if not join_glyph.exit:
            continue
        exit_y = join_glyph.exit[0][1]

        modifier = f"exit-{suffix_word}"
        ext_name = f"{name}.exit-{suffix_word}"
        if ext_name not in join_glyphs:
            variants[ext_name] = derive_join_glyph(
                join_glyph,
                name=ext_name,
                bitmap=_widen_bitmap_right_with_connector(
                    join_glyph.bitmap,
                    exit_y,
                    join_glyph.y_offset,
                    count=count,
                ),
                entry=() if join_glyph.extend_exit_no_entry else join_glyph.entry,
                exit=_shift_anchors(join_glyph.exit, dx=count),
                before=tuple(extend_before),
                extend_exit_before=(),
                extend_exit_no_entry=False,
                add_modifiers=(modifier,),
                generated_from=name,
                transform_kind=f"exit-{suffix_word}",
            )
            _record_transform(
                transforms,
                kind=f"exit-{suffix_word}",
                source_name=name,
                target_name=ext_name,
                count=count,
                restricted_y=exit_y,
                preserves_entry=not join_glyph.extend_exit_no_entry,
            )

        base_name = join_glyph.base_name
        for other_name, other_join_glyph in sorted(join_glyphs.items()):
            if other_name == name:
                continue
            if other_join_glyph.extended_exit_suffix is not None:
                continue
            other_base = other_join_glyph.base_name
            other_sequence = other_join_glyph.sequence
            is_variant = other_base == base_name and bool(other_join_glyph.modifiers)
            is_ligature = bool(other_sequence) and other_sequence[0] == base_name
            if not (is_variant or is_ligature):
                continue
            if not other_join_glyph.exit:
                continue
            sec_name = f"{other_name}.exit-{suffix_word}"
            if sec_name not in join_glyphs:
                variants[sec_name] = derive_join_glyph(
                    other_join_glyph,
                    name=sec_name,
                    bitmap=_widen_bitmap_right_with_connector(
                        other_join_glyph.bitmap,
                        other_join_glyph.exit[0][1],
                        other_join_glyph.y_offset,
                        count=count,
                    ),
                    exit=_shift_anchors(other_join_glyph.exit, dx=count),
                    after=(),
                    before=(),
                    not_after=(),
                    not_before=(),
                    reverse_upgrade_from=(),
                    word_final=False,
                    extend_entry_after=(),
                    extend_exit_before=(),
                    doubly_extend_entry_after=(),
                    doubly_extend_exit_before=(),
                    noentry_after=(),
                    extend_exit_no_entry=False,
                    add_modifiers=(modifier,),
                    generated_from=other_name,
                    transform_kind=f"exit-{suffix_word}",
                )
                _record_transform(
                    transforms,
                    kind=f"exit-{suffix_word}",
                    source_name=other_name,
                    target_name=sec_name,
                    count=count,
                )

    return variants


def generate_extended_entry_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    return _generate_extended_entry_variants(
        join_glyphs,
        count=1,
        yaml_key="extend_entry_after",
        suffix_word="extended",
        transforms=transforms,
    )


def generate_extended_exit_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    return _generate_extended_exit_variants(
        join_glyphs,
        count=1,
        yaml_key="extend_exit_before",
        suffix_word="extended",
        transforms=transforms,
    )


def generate_doubly_extended_entry_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    return _generate_extended_entry_variants(
        join_glyphs,
        count=2,
        yaml_key="doubly_extend_entry_after",
        suffix_word="doubly-extended",
        transforms=transforms,
    )


def generate_doubly_extended_exit_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    return _generate_extended_exit_variants(
        join_glyphs,
        count=2,
        yaml_key="doubly_extend_exit_before",
        suffix_word="doubly-extended",
        transforms=transforms,
    )


def expand_join_transforms(
    join_glyphs: dict[str, JoinGlyph],
    *,
    has_zwnj: bool = False,
) -> tuple[dict[str, JoinGlyph], list[JoinTransform]]:
    expanded = dict(join_glyphs)
    transforms: list[JoinTransform] = []
    expanded.update(
        generate_noentry_variants(expanded, has_zwnj=has_zwnj, transforms=transforms)
    )
    for generator in (
        generate_extended_entry_variants,
        generate_extended_exit_variants,
        generate_doubly_extended_entry_variants,
        generate_doubly_extended_exit_variants,
    ):
        expanded.update(generator(expanded, transforms=transforms))
    return expanded, transforms


def flatten_join_glyphs(join_glyphs: dict[str, JoinGlyph]) -> dict[str, dict]:
    return {
        glyph_name: _materialize_join_glyph(join_glyph)
        for glyph_name, join_glyph in join_glyphs.items()
    }


def compile_quikscript_ir(
    glyph_data: dict,
    variant: str,
) -> tuple[dict[str, JoinGlyph], list[JoinTransform]]:
    compiled = compile_glyph_families(
        glyph_data.get("glyph_families", {}),
        variant,
        context_sets=glyph_data.get("context_sets", {}),
    )
    join_glyphs = build_join_glyphs(compiled)
    transforms: list[JoinTransform] = []
    if variant == "senior":
        join_glyphs, transforms = expand_join_transforms(
            join_glyphs,
            has_zwnj="uni200C" in glyph_data.get("glyphs", {}),
        )
    return join_glyphs, transforms


__all__ = [
    "CompiledGlyphMeta",
    "JoinGlyph",
    "JoinTransform",
    "build_compiled_glyph_metadata",
    "build_join_glyphs",
    "compile_glyph_families",
    "compile_quikscript_ir",
    "expand_join_transforms",
    "flatten_join_glyphs",
    "generate_doubly_extended_entry_variants",
    "generate_doubly_extended_exit_variants",
    "generate_extended_entry_variants",
    "generate_extended_exit_variants",
    "generate_noentry_variants",
    "get_base_glyph_name",
]
