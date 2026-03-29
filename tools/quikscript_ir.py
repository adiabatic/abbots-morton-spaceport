from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any


Anchor = tuple[int, int]


@dataclass(frozen=True)
class JoinGlyph:
    name: str
    base_name: str
    family: str | None
    sequence: tuple[str, ...]
    traits: frozenset[str]
    modifiers: frozenset[str]
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
    generated_from: str | None = None
    transform_kind: str | None = None
    glyph_def: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def entry_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in self.entry)

    @property
    def all_entry_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in (*self.entry, *self.entry_curs_only))

    @property
    def exit_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in self.exit)


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
            generated_from=glyph_def.get("_generated_from"),
            transform_kind=glyph_def.get("_transform_kind"),
            glyph_def=deepcopy(glyph_def),
        )
    return metadata


def build_compiled_glyph_metadata(glyphs_def: dict) -> dict[str, CompiledGlyphMeta]:
    return build_join_glyphs(glyphs_def)


def _shift_anchor(entry, dx=-1):
    if isinstance(entry[0], list):
        return [[x + dx, y] for x, y in entry]
    return [entry[0] + dx, entry[1]]


def _widen_bitmap_with_connector(bitmap, entry_y, y_offset=0, count=1):
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = entry_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap = []
    for index, row in enumerate(bitmap):
        prefix = "#" * count if index == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(prefix + row)
        else:
            new_bitmap.append([int(char == "#") for char in prefix] + list(row))
    return new_bitmap


def _widen_bitmap_right_with_connector(bitmap, exit_y, y_offset=0, count=1):
    if not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = exit_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    new_bitmap = []
    for index, row in enumerate(bitmap):
        suffix = "#" * count if index == connecting_row_idx else " " * count
        if isinstance(row, str):
            new_bitmap.append(row + suffix)
        else:
            new_bitmap.append(list(row) + [int(char == "#") for char in suffix])
    return new_bitmap


def _generated_variant_seed_context(glyph_name: str, glyph_def: dict) -> dict:
    return {
        "base_name": _seeded_base_name(glyph_name, glyph_def),
        "family_name": glyph_def.get("_family"),
        "sequence": glyph_def.get("_sequence"),
        "traits": tuple(glyph_def.get("_traits", ())),
        "modifiers": _seeded_modifiers(glyph_name, glyph_def),
    }


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
    glyphs_def: dict,
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict:
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
        noentry_def = {
            key: value
            for key, value in gdef.items()
            if key not in ("cursive_entry", "extend_entry_after", "extend_exit_before")
        }
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
            generated_from=name,
            transform_kind="noentry",
        )
        variants[variant_name] = noentry_def
        _record_transform(
            transforms,
            kind="noentry",
            source_name=name,
            target_name=variant_name,
            preserves_entry=False,
        )
    return variants


def _generate_extended_entry_variants(
    glyphs_def: dict,
    *,
    count: int,
    yaml_key: str,
    suffix_word: str,
    transforms: list[JoinTransform] | None = None,
) -> dict:
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
                    variant_def = {key: value for key, value in gdef.items() if key != yaml_key}
                    variant_def["cursive_entry"] = [anchor[0], anchor[1]]
                    variant_def["bitmap"] = _widen_bitmap_with_connector(
                        variant_def["bitmap"],
                        anchor[1],
                        variant_def.get("y_offset", 0),
                        count=count,
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
                        generated_from=name,
                        transform_kind=f"entry-{suffix_word}",
                    )
                    variants[ext_name] = variant_def
                    _record_transform(
                        transforms,
                        kind=f"entry-{suffix_word}",
                        source_name=name,
                        target_name=ext_name,
                        count=count,
                        restricted_y=y,
                        preserves_exit="cursive_exit" in gdef,
                    )
        else:
            modifier = f"entry-{suffix_word}"
            ext_name = f"{name}.entry-{suffix_word}"
            if ext_name not in glyphs_def:
                variant_def = {key: value for key, value in gdef.items() if key != yaml_key}
                variant_def["bitmap"] = _widen_bitmap_with_connector(
                    variant_def["bitmap"],
                    entries[0][1],
                    variant_def.get("y_offset", 0),
                    count=count,
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
                    generated_from=name,
                    transform_kind=f"entry-{suffix_word}",
                )
                variants[ext_name] = variant_def
                _record_transform(
                    transforms,
                    kind=f"entry-{suffix_word}",
                    source_name=name,
                    target_name=ext_name,
                    count=count,
                    preserves_exit="cursive_exit" in gdef,
                )

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
                        extended = {key: value for key, value in other_gdef.items() if key not in _CALT_KEYS}
                        extended["cursive_entry"] = [anchor[0], anchor[1]]
                        extended["bitmap"] = _widen_bitmap_with_connector(
                            extended["bitmap"],
                            anchor[1],
                            extended.get("y_offset", 0),
                            count=count,
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
                            generated_from=other_name,
                            transform_kind=f"entry-{suffix_word}",
                        )
                        variants[sec_name] = extended
                        _record_transform(
                            transforms,
                            kind=f"entry-{suffix_word}",
                            source_name=other_name,
                            target_name=sec_name,
                            count=count,
                            restricted_y=y,
                            preserves_exit="cursive_exit" in other_gdef,
                        )
            else:
                modifier = f"entry-{suffix_word}"
                sec_name = f"{other_name}.entry-{suffix_word}"
                if sec_name not in glyphs_def:
                    extended = {key: value for key, value in other_gdef.items() if key not in _CALT_KEYS}
                    other_entries_norm = _normalize_anchors(other_entry)
                    extended["bitmap"] = _widen_bitmap_with_connector(
                        extended["bitmap"],
                        other_entries_norm[0][1],
                        extended.get("y_offset", 0),
                        count=count,
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
                        generated_from=other_name,
                        transform_kind=f"entry-{suffix_word}",
                    )
                    variants[sec_name] = extended
                    _record_transform(
                        transforms,
                        kind=f"entry-{suffix_word}",
                        source_name=other_name,
                        target_name=sec_name,
                        count=count,
                        preserves_exit="cursive_exit" in other_gdef,
                    )

    return variants


def _generate_extended_exit_variants(
    glyphs_def: dict,
    *,
    count: int,
    yaml_key: str,
    suffix_word: str,
    transforms: list[JoinTransform] | None = None,
) -> dict:
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
            variant_def = {key: value for key, value in gdef.items() if key not in skip_keys}
            variant_def["bitmap"] = _widen_bitmap_right_with_connector(
                variant_def["bitmap"],
                exit_y,
                variant_def.get("y_offset", 0),
                count=count,
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
                generated_from=name,
                transform_kind=f"exit-{suffix_word}",
            )
            variants[ext_name] = variant_def
            _record_transform(
                transforms,
                kind=f"exit-{suffix_word}",
                source_name=name,
                target_name=ext_name,
                count=count,
                restricted_y=exit_y,
                preserves_entry=not gdef.get("extend_exit_no_entry"),
            )

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
                extended = {key: value for key, value in other_gdef.items() if key not in _CALT_KEYS}
                other_exits_norm = _normalize_anchors(other_exit)
                extended["bitmap"] = _widen_bitmap_right_with_connector(
                    extended["bitmap"],
                    other_exits_norm[0][1],
                    extended.get("y_offset", 0),
                    count=count,
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
                    generated_from=other_name,
                    transform_kind=f"exit-{suffix_word}",
                )
                variants[sec_name] = extended
                _record_transform(
                    transforms,
                    kind=f"exit-{suffix_word}",
                    source_name=other_name,
                    target_name=sec_name,
                    count=count,
                )

    return variants


def generate_extended_entry_variants(
    glyphs_def: dict,
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict:
    return _generate_extended_entry_variants(
        glyphs_def,
        count=1,
        yaml_key="extend_entry_after",
        suffix_word="extended",
        transforms=transforms,
    )


def generate_extended_exit_variants(
    glyphs_def: dict,
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict:
    return _generate_extended_exit_variants(
        glyphs_def,
        count=1,
        yaml_key="extend_exit_before",
        suffix_word="extended",
        transforms=transforms,
    )


def generate_doubly_extended_entry_variants(
    glyphs_def: dict,
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict:
    return _generate_extended_entry_variants(
        glyphs_def,
        count=2,
        yaml_key="doubly_extend_entry_after",
        suffix_word="doubly-extended",
        transforms=transforms,
    )


def generate_doubly_extended_exit_variants(
    glyphs_def: dict,
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict:
    return _generate_extended_exit_variants(
        glyphs_def,
        count=2,
        yaml_key="doubly_extend_exit_before",
        suffix_word="doubly-extended",
        transforms=transforms,
    )


def expand_join_transforms(
    glyphs_def: dict,
) -> tuple[dict[str, dict], list[JoinTransform]]:
    expanded = dict(glyphs_def)
    transforms: list[JoinTransform] = []
    for generator in (
        generate_noentry_variants,
        generate_extended_entry_variants,
        generate_extended_exit_variants,
        generate_doubly_extended_entry_variants,
        generate_doubly_extended_exit_variants,
    ):
        expanded.update(generator(expanded, transforms=transforms))
    return expanded, transforms


def flatten_join_glyphs(join_glyphs: dict[str, JoinGlyph]) -> dict[str, dict]:
    return {
        glyph_name: deepcopy(join_glyph.glyph_def)
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
    transforms: list[JoinTransform] = []
    if variant == "senior":
        zwnj = glyph_data.get("glyphs", {}).get("uni200C")
        if zwnj is not None:
            compiled = {
                **compiled,
                "uni200C": deepcopy(zwnj),
            }
        compiled, transforms = expand_join_transforms(compiled)
        compiled.pop("uni200C", None)
    return build_join_glyphs(compiled), transforms


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
