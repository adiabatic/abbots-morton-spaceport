from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
import re
from typing import Any, TypedDict


Anchor = tuple[int, int]
BitmapRow = str | tuple[int, ...]
GlyphDef = dict[str, Any]


class GlyphData(TypedDict):
    metadata: dict[str, Any]
    glyphs: dict[str, GlyphDef | None]
    glyph_families: dict[str, Any]
    context_sets: dict[str, list[Any]]
    kerning: dict[str, Any]


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
    extend_exit_before_gated: tuple[tuple[str, tuple[str, ...]], ...] = ()
    noentry_for: str | None = None
    generated_from: str | None = None
    transform_kind: str | None = None
    revert_feature: str | None = None
    gate_feature: str | None = None
    gated_before: tuple[tuple[str, tuple[str, ...]], ...] = ()

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


def get_base_glyph_name(prop_glyph_name: str) -> str:
    if prop_glyph_name.endswith(".prop"):
        return prop_glyph_name[:-5]
    if ".prop." in prop_glyph_name:
        return prop_glyph_name.replace(".prop.", ".", 1)
    return prop_glyph_name


def resolve_known_glyph_names(
    values: tuple[str, ...] | list[str],
    glyph_names: set[str],
) -> list[str]:
    return [value if value in glyph_names else get_base_glyph_name(value) for value in values]


def _merge_family_records(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
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
    family_def: dict[str, Any],
    record_name: str,
    cache: dict[str, dict[str, Any]],
    stack: list[str],
) -> dict[str, Any]:
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
    resolved: dict[str, Any] = {}
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
        for k in ("bitmap", "y_offset", "advance_width"):
            resolved.pop(k, None)
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


def _is_contextual_family_form(form_def: dict[str, Any], *, is_base_record: bool = False) -> bool:
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
    raw_traits: list[str] | tuple[str, ...] | None,
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
    raw_modifiers: list[str] | tuple[str, ...] | None,
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
    family_def: dict[str, Any],
    form_def: dict[str, Any],
    *,
    form_name: str | None = None,
    contextual: bool,
    family_names: set[str],
    context_sets: dict[str, list[Any]],
) -> GlyphDef:
    glyph_def: GlyphDef = {}

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

    gated_exit = derive.get("extend_exit_before_gated")
    if gated_exit:
        resolved_gated: dict[str, tuple[str, ...]] = {}
        for feature_tag, refs in gated_exit.items():
            resolved_gated[feature_tag] = tuple(
                _normalize_family_refs(
                    refs,
                    family_names,
                    context_sets=context_sets,
                    context_family=family_name,
                    context_label=f"form {form_name!r}" if form_name else "base record",
                    field_name="extend_exit_before_gated",
                )
            )
        glyph_def["extend_exit_before_gated"] = tuple(sorted(resolved_gated.items()))

    revert_feature = form_def.get("revert_feature")
    if revert_feature is not None:
        glyph_def["revert_feature"] = revert_feature

    gate_feature = form_def.get("gate_feature_behind")
    if gate_feature is not None:
        glyph_def["gate_feature"] = gate_feature

    return glyph_def


def _iter_compiled_family_forms(
    glyph_families: dict[str, Any],
    variant: str,
    context_sets: dict[str, list[Any]] | None = None,
):
    if not glyph_families:
        return

    is_senior = variant == "senior"
    family_names = set(glyph_families)
    context_sets = context_sets or {}

    for family_name, family_def in glyph_families.items():
        cache: dict[str, dict[str, Any]] = {}

        if variant == "mono":
            base_record_name = "mono" if family_def.get("mono") else None
        else:
            base_record_name = "prop" if family_def.get("prop") else "mono"
            if not family_def.get(base_record_name):
                base_record_name = None

        if base_record_name is not None:
            yield {
                "family_name": family_name,
                "family_def": family_def,
                "form_def": _resolve_family_record(
                    family_name,
                    family_def,
                    base_record_name,
                    cache,
                    [],
                ),
                "form_name": None,
                "output_name": family_name,
                "contextual": False,
                "traits": (),
                "modifiers": (),
            }

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
            variants = set(resolved.get("variants", ()))
            if variant == "mono":
                if "mono" not in variants:
                    continue
            elif variants and variant not in variants:
                continue
            else:
                contextual = _is_contextual_family_form(resolved)
                if not is_senior and contextual:
                    continue
            yield {
                "family_name": family_name,
                "family_def": family_def,
                "form_def": resolved,
                "form_name": form_name,
                "output_name": output_name,
                "contextual": _is_contextual_family_form(resolved),
                "traits": traits,
                "modifiers": modifiers,
            }


def compile_glyph_families(
    glyph_families: dict[str, Any],
    variant: str,
    context_sets: dict[str, list[Any]] | None = None,
) -> dict[str, GlyphDef]:
    if not glyph_families:
        return {}

    compiled: dict[str, GlyphDef] = {}
    family_names = set(glyph_families)
    context_sets = context_sets or {}

    for record in _iter_compiled_family_forms(glyph_families, variant, context_sets=context_sets):
        output_name = record["output_name"]
        if output_name in compiled:
            raise ValueError(f"Duplicate compiled glyph name {output_name!r}")
        compiled[output_name] = _family_form_to_glyph_def(
            record["family_name"],
            record["family_def"],
            record["form_def"],
            form_name=record["form_name"],
            contextual=record["contextual"],
            family_names=family_names,
            context_sets=context_sets,
        )

    return compiled


def _normalize_anchors(raw: list[Any] | None) -> list[list[int]]:
    if raw is None:
        return []
    if isinstance(raw[0], list):
        return raw
    return [raw]



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


def _glyph_def_to_join_glyph(
    glyph_name: str,
    glyph_def: GlyphDef,
    *,
    base_name: str | None = None,
    family_name: str | None = None,
    sequence: Sequence[str] | None = None,
    traits: Sequence[str] = (),
    modifiers: Sequence[str] | None = None,
    contextual: bool | None = None,
    is_noentry: bool | None = None,
    noentry_for: str | None = None,
    generated_from: str | None = None,
    transform_kind: str | None = None,
) -> JoinGlyph:
    resolved_traits = frozenset(traits)
    resolved_modifiers = tuple(modifiers) if modifiers is not None else tuple(
        _glyph_name_modifiers(glyph_name)
    )
    resolved_contextual = (
        _is_contextual_variant(glyph_name) if contextual is None else bool(contextual)
    )
    resolved_is_noentry = (
        ("noentry" in resolved_modifiers) if is_noentry is None else bool(is_noentry)
    )

    return JoinGlyph(
        name=glyph_name,
        base_name=base_name or get_base_glyph_name(glyph_name).split(".")[0],
        family=family_name,
        sequence=tuple(sequence or ()),
        traits=resolved_traits,
        modifiers=resolved_modifiers,
        compat_assertions=_compat_assertions_from_modifiers(
            list(resolved_modifiers),
            resolved_traits,
        ),
        entry=tuple((a[0], a[1]) for a in _normalize_anchors(glyph_def.get("cursive_entry"))),
        entry_curs_only=tuple(
            (a[0], a[1])
            for a in _normalize_anchors(glyph_def.get("cursive_entry_curs_only"))
        ),
        exit=tuple((a[0], a[1]) for a in _normalize_anchors(glyph_def.get("cursive_exit"))),
        after=tuple(glyph_def.get("calt_after", ())),
        before=tuple(glyph_def.get("calt_before", ())),
        not_after=tuple(glyph_def.get("calt_not_after", ())),
        not_before=tuple(glyph_def.get("calt_not_before", ())),
        reverse_upgrade_from=tuple(glyph_def.get("reverse_upgrade_from", ())),
        preferred_over=tuple(glyph_def.get("preferred_over", ())),
        word_final=bool(glyph_def.get("calt_word_final")),
        is_contextual=resolved_contextual,
        is_entry_variant=any(modifier.startswith("entry-") for modifier in resolved_modifiers),
        entry_suffix=_entry_suffix_from_modifiers(list(resolved_modifiers)),
        exit_suffix=_exit_suffix_from_modifiers(list(resolved_modifiers)),
        extended_entry_suffix=_extended_entry_suffix_from_modifiers(list(resolved_modifiers)),
        extended_exit_suffix=_extended_exit_suffix_from_modifiers(list(resolved_modifiers)),
        entry_restriction_y=_entry_restriction_y_from_modifiers(list(resolved_modifiers)),
        is_noentry=resolved_is_noentry,
        bitmap=_normalize_bitmap(glyph_def.get("bitmap", ())),
        y_offset=int(glyph_def.get("y_offset", 0)),
        advance_width=glyph_def.get("advance_width"),
        extend_entry_after=tuple(glyph_def.get("extend_entry_after", ())),
        extend_exit_before=tuple(glyph_def.get("extend_exit_before", ())),
        extend_exit_before_gated=tuple(glyph_def.get("extend_exit_before_gated", ())),
        doubly_extend_entry_after=tuple(glyph_def.get("doubly_extend_entry_after", ())),
        doubly_extend_exit_before=tuple(glyph_def.get("doubly_extend_exit_before", ())),
        noentry_after=tuple(glyph_def.get("noentry_after", ())),
        extend_exit_no_entry=bool(glyph_def.get("extend_exit_no_entry")),
        noentry_for=noentry_for,
        generated_from=generated_from,
        transform_kind=transform_kind,
        revert_feature=glyph_def.get("revert_feature"),
        gate_feature=glyph_def.get("gate_feature"),
    )


def _normalize_bitmap(bitmap: Sequence[str | list[int]] | None) -> tuple[BitmapRow, ...]:
    if not bitmap:
        return ()
    normalized: list[BitmapRow] = []
    for row in bitmap:
        if isinstance(row, str):
            normalized.append(row)
        else:
            normalized.append(tuple(row))
    return tuple(normalized)


def _materialize_bitmap(bitmap: Sequence[BitmapRow]) -> list[str | list[int]]:
    materialized: list[str | list[int]] = []
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
        metadata[glyph_name] = _glyph_def_to_join_glyph(glyph_name, glyph_def)
    return metadata


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
) -> tuple[tuple[BitmapRow, ...], int]:
    if not bitmap:
        return bitmap, 0
    height = len(bitmap)
    row_from_bottom = exit_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    connector_row = bitmap[connecting_row_idx]
    if isinstance(connector_row, str):
        bitmap_width = len(connector_row)
        try:
            rightmost_x = connector_row.rindex("#")
        except ValueError:
            rightmost_x = bitmap_width - 1
    else:
        bitmap_width = len(connector_row)
        rightmost_x = bitmap_width - 1
        for i in range(bitmap_width - 1, -1, -1):
            if connector_row[i]:
                rightmost_x = i
                break
    widen_by = max(0, rightmost_x + count + 1 - bitmap_width)
    new_bitmap: list[BitmapRow] = []
    for index, row in enumerate(bitmap):
        if isinstance(row, str):
            if widen_by > 0:
                row = row + " " * widen_by
            if index == connecting_row_idx:
                row_list = list(row)
                for pos in range(rightmost_x + 1, rightmost_x + 1 + count):
                    row_list[pos] = "#"
                new_bitmap.append("".join(row_list))
            else:
                new_bitmap.append(row)
        else:
            if widen_by > 0:
                row = tuple(list(row) + [0] * widen_by)
            if index == connecting_row_idx:
                row_list = list(row)
                for pos in range(rightmost_x + 1, rightmost_x + 1 + count):
                    row_list[pos] = 1
                new_bitmap.append(tuple(row_list))
            else:
                new_bitmap.append(row)
    return tuple(new_bitmap), widen_by


_UNSET = object()


def _materialize_anchor_value(anchors: tuple[Anchor, ...]) -> list[int] | list[list[int]] | None:
    if not anchors:
        return None
    pairs = [[x, y] for x, y in anchors]
    if len(pairs) == 1:
        return pairs[0]
    return pairs


def _set_optional_list(glyph_def: GlyphDef, key: str, values: Sequence[str]) -> None:
    if values:
        glyph_def[key] = list(values)
    else:
        glyph_def.pop(key, None)


def _set_optional_anchor(
    glyph_def: GlyphDef,
    key: str,
    anchors: tuple[Anchor, ...],
) -> None:
    value = _materialize_anchor_value(anchors)
    if value is None:
        glyph_def.pop(key, None)
    else:
        glyph_def[key] = value


def _materialize_join_glyph(join_glyph: JoinGlyph) -> GlyphDef:
    glyph_def: GlyphDef = {
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
    if join_glyph.extend_exit_before_gated:
        glyph_def["extend_exit_before_gated"] = dict(join_glyph.extend_exit_before_gated)
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
    extend_exit_before_gated: tuple[tuple[str, tuple[str, ...]], ...] | object = _UNSET,
    gated_before: tuple[tuple[str, tuple[str, ...]], ...] | object = _UNSET,
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
    resolved_extend_exit_before_gated = (
        source.extend_exit_before_gated
        if extend_exit_before_gated is _UNSET
        else extend_exit_before_gated
    )
    resolved_gated_before = (
        source.gated_before if gated_before is _UNSET else gated_before
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
        extend_exit_before_gated=resolved_extend_exit_before_gated,
        gated_before=resolved_gated_before,
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

    bases_with_entry_forms: set[str] = set()
    for vname, vglyph in join_glyphs.items():
        if vglyph.entry and vglyph.modifiers:
            bases_with_entry_forms.add(vname.split(".")[0])

    variants: dict[str, JoinGlyph] = {}
    for name, join_glyph in sorted(join_glyphs.items()):
        if join_glyph.is_noentry:
            continue
        if join_glyph.modifiers:
            if not join_glyph.entry or not join_glyph.exit:
                continue
        elif not join_glyph.entry and name not in bases_with_entry_forms:
            continue
        variant_name = name + ".noentry"
        variants[variant_name] = derive_join_glyph(
            join_glyph,
            name=variant_name,
            entry=(),
            extend_entry_after=(),
            extend_exit_before=(),
            extend_exit_before_gated=(),
            gated_before=(),
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


def _iter_related_extension_targets(
    join_glyphs: dict[str, JoinGlyph],
    *,
    source_name: str,
    source_glyph: JoinGlyph,
    side: str,
) -> list[tuple[str, JoinGlyph, bool]]:
    suffix_attr = "extended_entry_suffix" if side == "entry" else "extended_exit_suffix"
    anchor_attr = side
    targets = [(source_name, source_glyph, True)]
    base_name = source_glyph.base_name

    for other_name, other_join_glyph in sorted(join_glyphs.items()):
        if other_name == source_name:
            continue
        if getattr(other_join_glyph, suffix_attr) is not None:
            continue
        if not getattr(other_join_glyph, anchor_attr):
            continue

        other_base = other_join_glyph.base_name
        other_sequence = other_join_glyph.sequence
        is_variant = other_base == base_name and bool(other_join_glyph.modifiers)
        is_ligature = bool(other_sequence) and other_sequence[0] == base_name
        if is_variant or is_ligature:
            targets.append((other_name, other_join_glyph, False))

    return targets


def _cleared_extension_context() -> dict[str, object]:
    return {
        "after": (),
        "before": (),
        "not_after": (),
        "not_before": (),
        "reverse_upgrade_from": (),
        "word_final": False,
        "extend_entry_after": (),
        "extend_exit_before": (),
        "extend_exit_before_gated": (),
        "gated_before": (),
        "doubly_extend_entry_after": (),
        "doubly_extend_exit_before": (),
        "noentry_after": (),
    }


def _add_entry_extension_variants(
    variants: dict[str, JoinGlyph],
    join_glyphs: dict[str, JoinGlyph],
    *,
    target_name: str,
    target_glyph: JoinGlyph,
    source_after: tuple[str, ...],
    use_height_specific_names: bool,
    count: int,
    suffix_word: str,
    transforms: list[JoinTransform] | None,
    is_source: bool,
) -> None:
    entries = target_glyph.entry
    kind = f"entry-{suffix_word}"

    for anchor in entries if use_height_specific_names else (entries[0],):
        y = anchor[1]
        if use_height_specific_names:
            label = _EXTENDED_HEIGHT_LABELS.get(y, f"y{y}")
            modifier = f"entry-{suffix_word}-at-{label}"
            variant_name = f"{target_name}.entry-{suffix_word}-at-{label}"
        else:
            modifier = f"entry-{suffix_word}"
            variant_name = f"{target_name}.entry-{suffix_word}"

        if variant_name in join_glyphs or (not is_source and variant_name in variants):
            continue

        kwargs = {
            "bitmap": _widen_bitmap_with_connector(
                target_glyph.bitmap,
                y,
                target_glyph.y_offset,
                count=count,
            ),
            "exit": _shift_anchors(target_glyph.exit, dx=count),
            "add_modifiers": (modifier,),
            "generated_from": target_name,
            "transform_kind": kind,
        }
        if use_height_specific_names:
            kwargs["entry"] = (anchor,)

        if is_source:
            kwargs["after"] = source_after
            kwargs["extend_entry_after"] = ()
        else:
            kwargs.update(_cleared_extension_context())

        variants[variant_name] = derive_join_glyph(
            target_glyph,
            name=variant_name,
            **kwargs,
        )
        _record_transform(
            transforms,
            kind=kind,
            source_name=target_name,
            target_name=variant_name,
            count=count,
            restricted_y=y if use_height_specific_names else None,
            preserves_exit=bool(target_glyph.exit),
        )


def _add_exit_extension_variant(
    variants: dict[str, JoinGlyph],
    join_glyphs: dict[str, JoinGlyph],
    *,
    target_name: str,
    target_glyph: JoinGlyph,
    source_before: tuple[str, ...],
    source_gated_before: tuple[tuple[str, tuple[str, ...]], ...],
    count: int,
    suffix_word: str,
    transforms: list[JoinTransform] | None,
    is_source: bool,
) -> None:
    kind = f"exit-{suffix_word}"
    variant_name = f"{target_name}.exit-{suffix_word}"
    if variant_name in join_glyphs or (not is_source and variant_name in variants):
        return

    exit_y = target_glyph.exit[0][1]
    new_bitmap, actual_dx = _widen_bitmap_right_with_connector(
        target_glyph.bitmap,
        exit_y,
        target_glyph.y_offset,
        count=count,
    )

    kwargs = {
        "bitmap": new_bitmap,
        "exit": _shift_anchors(target_glyph.exit, dx=actual_dx),
        "extend_exit_no_entry": False,
        "add_modifiers": (f"exit-{suffix_word}",),
        "generated_from": target_name,
        "transform_kind": kind,
    }
    if is_source:
        kwargs["entry"] = () if target_glyph.extend_exit_no_entry else target_glyph.entry
        kwargs["before"] = source_before
        kwargs["extend_exit_before"] = ()
        kwargs["extend_exit_before_gated"] = ()
        kwargs["gated_before"] = source_gated_before
    else:
        kwargs.update(_cleared_extension_context())

    variants[variant_name] = derive_join_glyph(
        target_glyph,
        name=variant_name,
        **kwargs,
    )
    _record_transform(
        transforms,
        kind=kind,
        source_name=target_name,
        target_name=variant_name,
        count=count,
        restricted_y=exit_y if is_source else None,
        preserves_entry=not target_glyph.extend_exit_no_entry if is_source else True,
    )


def _generate_extended_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    side: str,
    count: int,
    yaml_key: str,
    suffix_word: str,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    variants: dict[str, JoinGlyph] = {}
    for name, join_glyph in sorted(join_glyphs.items()):
        context_glyphs = tuple(getattr(join_glyph, yaml_key))
        gated_entries = (
            join_glyph.extend_exit_before_gated
            if side == "exit" and yaml_key == "extend_exit_before"
            else ()
        )
        if not context_glyphs and not gated_entries:
            continue
        if not getattr(join_glyph, side):
            continue

        for target_name, target_glyph, is_source in _iter_related_extension_targets(
            join_glyphs,
            source_name=name,
            source_glyph=join_glyph,
            side=side,
        ):
            if side == "entry":
                _add_entry_extension_variants(
                    variants,
                    join_glyphs,
                    target_name=target_name,
                    target_glyph=target_glyph,
                    source_after=context_glyphs if is_source else (),
                    use_height_specific_names=len(join_glyph.entry) > 1,
                    count=count,
                    suffix_word=suffix_word,
                    transforms=transforms,
                    is_source=is_source,
                )
            else:
                _add_exit_extension_variant(
                    variants,
                    join_glyphs,
                    target_name=target_name,
                    target_glyph=target_glyph,
                    source_before=context_glyphs if is_source else (),
                    source_gated_before=gated_entries if is_source else (),
                    count=count,
                    suffix_word=suffix_word,
                    transforms=transforms,
                    is_source=is_source,
                )

    return variants


def generate_extended_entry_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    return _generate_extended_variants(
        join_glyphs,
        side="entry",
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
    return _generate_extended_variants(
        join_glyphs,
        side="exit",
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
    return _generate_extended_variants(
        join_glyphs,
        side="entry",
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
    return _generate_extended_variants(
        join_glyphs,
        side="exit",
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


def flatten_join_glyphs(join_glyphs: dict[str, JoinGlyph]) -> dict[str, GlyphDef]:
    return {
        glyph_name: _materialize_join_glyph(join_glyph)
        for glyph_name, join_glyph in join_glyphs.items()
    }


def compile_quikscript_ir(
    glyph_data: GlyphData,
    variant: str,
) -> tuple[dict[str, JoinGlyph], list[JoinTransform]]:
    glyph_families = glyph_data.get("glyph_families", {})
    context_sets = glyph_data.get("context_sets", {})
    family_names = set(glyph_families)

    join_glyphs = {}
    for record in _iter_compiled_family_forms(
        glyph_families,
        variant,
        context_sets=context_sets,
    ):
        glyph_def = _family_form_to_glyph_def(
            record["family_name"],
            record["family_def"],
            record["form_def"],
            form_name=record["form_name"],
            contextual=record["contextual"],
            family_names=family_names,
            context_sets=context_sets,
        )
        if record["output_name"] in join_glyphs:
            raise ValueError(f"Duplicate compiled glyph name {record['output_name']!r}")
        join_glyphs[record["output_name"]] = _glyph_def_to_join_glyph(
            record["output_name"],
            glyph_def,
            base_name=record["family_name"],
            family_name=record["family_name"],
            sequence=record["family_def"].get("sequence"),
            traits=record["traits"],
            modifiers=[*record["traits"], *record["modifiers"]],
            contextual=record["contextual"],
        )
    transforms: list[JoinTransform] = []
    if variant == "senior":
        join_glyphs, transforms = expand_join_transforms(
            join_glyphs,
            has_zwnj="uni200C" in glyph_data.get("glyphs", {}),
        )
    return join_glyphs, transforms


__all__ = [
    "GlyphData",
    "GlyphDef",
    "JoinGlyph",
    "JoinTransform",
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
    "resolve_known_glyph_names",
]
