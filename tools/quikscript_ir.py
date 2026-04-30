from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
import re
import warnings
from typing import Any, TypedDict, cast


class LigatureEntryInheritanceWarning(UserWarning):
    """A ligature's explicit `entry` anchor duplicates (or contradicts) what
    `_inherit_ligature_entries_from_lead` would supply. Surface it so we can
    eventually strip the redundant declarations."""


Anchor = tuple[int, int]
BitmapRow = str | tuple[int, ...]
GlyphDef = dict[str, Any]


_EXTENSION_SUFFIX = {1: "extended", 2: "doubly-extended", 3: "triply-extended", 4: "quadruply-extended"}
_CONTRACTION_SUFFIX = {1: "contracted", 2: "doubly-contracted", 3: "triply-contracted", 4: "quadruply-contracted"}

# Sentinel used by `expand_selectors_for_ligatures` to mark a ligature-glyph
# endpoint addition. The novelty filter inside `_additions` subtracts this set
# from the candidate Y intersection; an empty set leaves the intersection alone
# so the addition fires whenever the ligature has an anchor Y the source can
# meet. Pre-liga literal endpoints carry the canonical component's Ys here
# instead, which lets the novelty filter suppress redundant additions.
_LIG_ENDPOINT_BYPASS: frozenset[int] = frozenset()


@dataclass(frozen=True)
class ExtensionSpec:
    by: int
    targets: tuple[str, ...]


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
    exit_ink_y: int | None
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
    extend_entry_after: ExtensionSpec | None
    extend_exit_before: ExtensionSpec | None
    noentry_after: tuple[str, ...]
    extend_exit_no_entry: bool
    extend_exit_before_gated: tuple[tuple[str, tuple[str, ...]], ...] = ()
    noentry_for: str | None = None
    generated_from: str | None = None
    transform_kind: str | None = None
    revert_feature: str | None = None
    gate_feature: str | None = None
    replaces_family_feature: str | None = None
    gated_before: tuple[tuple[str, tuple[str, ...]], ...] = ()
    contracted_entry_suffix: str | None = None
    contracted_exit_suffix: str | None = None
    contract_entry_after: ExtensionSpec | None = None
    contract_exit_before: ExtensionSpec | None = None

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
_KNOWN_DERIVE_DIRECTIVES = frozenset({
    "extend_entry_after",
    "extend_exit_before",
    "extend_exit_before_gated",
    "contract_entry_after",
    "contract_exit_before",
    "noentry_after",
    "reverse_upgrade_from",
    "preferred_over",
})


_ANCHOR_SENTINEL_PREFIX = "<<anchor "


def _make_anchor_sentinel(
    kind: str,
    y: int,
    excluded_families: tuple[str, ...] = (),
) -> str:
    if excluded_families:
        suffix = "|except=" + ",".join(excluded_families)
    else:
        suffix = ""
    return f"{_ANCHOR_SENTINEL_PREFIX}{kind}={y}{suffix}>>"


def _parse_anchor_sentinel(
    value: str,
) -> tuple[str, int, tuple[str, ...]] | None:
    if not value.startswith(_ANCHOR_SENTINEL_PREFIX) or not value.endswith(">>"):
        return None
    body = value[len(_ANCHOR_SENTINEL_PREFIX):-2]
    head, _, except_part = body.partition("|except=")
    kind, _, y_str = head.partition("=")
    if kind not in {"exit_y", "entry_y"} or not y_str:
        return None
    try:
        y = int(y_str)
    except ValueError:
        return None
    excluded = tuple(except_part.split(",")) if except_part else ()
    return kind, y, excluded


def is_anchor_sentinel(value: str) -> bool:
    return _parse_anchor_sentinel(value) is not None


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


def _validate_family_derive(family_name: str, family_def: dict[str, Any]) -> None:
    family_derive = family_def.get("derive")
    if family_derive is None:
        return
    if not isinstance(family_derive, dict):
        raise ValueError(
            f"Glyph family {family_name!r} family-level derive must be a mapping"
        )
    unknown = sorted(set(family_derive) - _KNOWN_DERIVE_DIRECTIVES)
    if unknown:
        raise ValueError(
            f"Glyph family {family_name!r} family-level derive has unknown "
            f"directives {unknown!r}; expected keys from "
            f"{sorted(_KNOWN_DERIVE_DIRECTIVES)!r}"
        )


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
    *,
    glyph_families: dict[str, Any] | None = None,
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

    raw_derive = raw.get("derive") if isinstance(raw, dict) else None
    explicit_null_derive_keys: set[str] = (
        {k for k, v in raw_derive.items() if v is None}
        if isinstance(raw_derive, dict)
        else set()
    )

    stack.append(record_name)
    resolved: dict[str, Any] = {}

    inherits = raw.get("inherits")
    if inherits:
        parents = [inherits] if isinstance(inherits, str) else inherits
        for parent_name in parents:
            parent = _resolve_family_record(
                family_name, family_def, parent_name, cache, stack,
                glyph_families=glyph_families,
            )
            parent_for_merge = {k: v for k, v in parent.items() if k != "derive"}
            resolved = _merge_family_records(resolved, parent_for_merge)

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
                glyph_families=glyph_families,
            )
            shape_def = {
                key: deepcopy(source_record[key])
                for key in ("bitmap", "y_offset", "advance_width")
                if key in source_record
            }
        else:
            raise ValueError(f"Unknown shape '{shape_name}' in glyph family '{family_name}'")
        resolved = _merge_family_records(shape_def, resolved)

    family_derive = family_def.get("derive")
    if family_derive:
        applicable = _select_applicable_family_derive(
            family_derive,
            form_anchors=resolved.get("anchors", {}) or {},
            explicit_null_keys=explicit_null_derive_keys,
            glyph_families=glyph_families,
        )
        if applicable:
            resolved = _merge_family_records({"derive": applicable}, resolved)

    cache[record_name] = resolved
    stack.pop()
    return resolved


def _anchor_ys(anchor: Any) -> tuple[int, ...]:
    if not anchor:
        return ()
    if isinstance(anchor[0], list):
        return tuple(a[1] for a in anchor if isinstance(a, list) and len(a) >= 2)
    if len(anchor) >= 2 and not isinstance(anchor[0], list):
        return (anchor[1],)
    return ()


def _form_anchor_ys(form_anchors: dict[str, Any]) -> tuple[set[int], set[int]]:
    entry_ys = set(_anchor_ys(form_anchors.get("entry")))
    entry_ys.update(_anchor_ys(form_anchors.get("entry_curs_only")))
    exit_ys = set(_anchor_ys(form_anchors.get("exit")))
    return entry_ys, exit_ys


def _collect_family_anchor_ys(
    family_def: dict[str, Any],
) -> tuple[set[int], set[int]]:
    records: dict[str, Any] = {}
    if family_def.get("mono"):
        records["mono"] = family_def["mono"]
    if family_def.get("prop"):
        records["prop"] = family_def["prop"]
    records.update(family_def.get("forms", {}) or {})

    entry_ys: set[int] = set()
    exit_ys: set[int] = set()

    def walk(record_name: str, visited: set[str]) -> tuple[set[int], set[int]]:
        if record_name in visited:
            return set(), set()
        visited.add(record_name)
        raw = records.get(record_name)
        if not isinstance(raw, dict):
            return set(), set()
        rec_entry: set[int] = set()
        rec_exit: set[int] = set()
        inherits = raw.get("inherits")
        if inherits:
            parents = [inherits] if isinstance(inherits, str) else inherits
            for parent_name in parents:
                p_entry, p_exit = walk(parent_name, visited)
                rec_entry.update(p_entry)
                rec_exit.update(p_exit)
        anchors = raw.get("anchors", {}) or {}
        rec_entry.update(_anchor_ys(anchors.get("entry")))
        rec_entry.update(_anchor_ys(anchors.get("entry_curs_only")))
        rec_exit.update(_anchor_ys(anchors.get("exit")))
        return rec_entry, rec_exit

    for record_name in records:
        rec_entry, rec_exit = walk(record_name, set())
        entry_ys.update(rec_entry)
        exit_ys.update(rec_exit)

    return entry_ys, exit_ys


def _filter_targets_by_reachability(
    targets: list[Any] | tuple[Any, ...],
    *,
    form_ys: set[int],
    glyph_families: dict[str, Any],
    target_anchor: str,
) -> list[Any]:
    kept: list[Any] = []
    for target in targets:
        family = target.get("family") if isinstance(target, dict) else None
        if not family:
            kept.append(target)
            continue
        target_def = glyph_families.get(family)
        if target_def is None:
            kept.append(target)
            continue
        entry_ys, exit_ys = _collect_family_anchor_ys(target_def)
        target_ys = entry_ys if target_anchor == "entry" else exit_ys
        if form_ys & target_ys:
            kept.append(target)
    return kept


def _select_applicable_family_derive(
    family_derive: dict[str, Any],
    *,
    form_anchors: dict[str, Any],
    explicit_null_keys: set[str],
    glyph_families: dict[str, Any] | None,
) -> dict[str, Any]:
    form_entry_ys, form_exit_ys = _form_anchor_ys(form_anchors)

    applicable: dict[str, Any] = {}
    for key, value in family_derive.items():
        if key in explicit_null_keys or value is None:
            continue

        if glyph_families is None:
            applicable[key] = deepcopy(value)
            continue

        if key in {"extend_exit_before", "contract_exit_before"}:
            if not form_exit_ys:
                continue
            kept_targets = _filter_targets_by_reachability(
                value.get("targets", ()),
                form_ys=form_exit_ys,
                glyph_families=glyph_families,
                target_anchor="entry",
            )
            if not kept_targets:
                continue
            applicable[key] = {**deepcopy(value), "targets": kept_targets}
        elif key in {"extend_entry_after", "contract_entry_after"}:
            if not form_entry_ys:
                continue
            kept_targets = _filter_targets_by_reachability(
                value.get("targets", ()),
                form_ys=form_entry_ys,
                glyph_families=glyph_families,
                target_anchor="exit",
            )
            if not kept_targets:
                continue
            applicable[key] = {**deepcopy(value), "targets": kept_targets}
        elif key == "extend_exit_before_gated":
            if not form_exit_ys or not isinstance(value, dict):
                continue
            kept_gated: dict[str, list[Any]] = {}
            for tag, refs in value.items():
                kept_refs = _filter_targets_by_reachability(
                    refs or (),
                    form_ys=form_exit_ys,
                    glyph_families=glyph_families,
                    target_anchor="entry",
                )
                if kept_refs:
                    kept_gated[tag] = kept_refs
            if kept_gated:
                applicable[key] = kept_gated
        else:
            applicable[key] = deepcopy(value)

    return applicable


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
    if modifier in {"extended", "widebase", "reaches-way-back", "smaller-loop", "noentry", "noexit", "gapped"}:
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
        if isinstance(value, dict) and ("exit_y" in value or "entry_y" in value):
            keys = set(value)
            anchor_keys = keys & {"exit_y", "entry_y"}
            allowed = anchor_keys | {"except"}
            if len(anchor_keys) != 1 or not keys <= allowed:
                raise ValueError(
                    f"{context_family} {context_label} {field_name} anchor selector "
                    "must have exactly one of exit_y/entry_y and an optional 'except' list"
                )
            kind = next(iter(anchor_keys))
            y = value[kind]
            if not isinstance(y, int) or isinstance(y, bool):
                raise ValueError(
                    f"{context_family} {context_label} {field_name} {kind} must be an integer"
                )
            excluded_families: list[str] = []
            for excluded in value.get("except", []):
                if not isinstance(excluded, dict) or set(excluded) != {"family"}:
                    raise ValueError(
                        f"{context_family} {context_label} {field_name} anchor selector "
                        "'except' entries must be {family: <name>} mappings"
                    )
                family_name = excluded["family"]
                if not isinstance(family_name, str) or family_name not in family_names:
                    raise ValueError(
                        f"{context_family} {context_label} {field_name} anchor selector "
                        f"'except' refers to unknown family {family_name!r}"
                    )
                excluded_families.append(family_name)
            return [_make_anchor_sentinel(kind, y, tuple(excluded_families))]

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
    if "exit_ink_y" in anchors:
        glyph_def["cursive_exit_ink_y"] = deepcopy(anchors["exit_ink_y"])

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

    for key in (
        "extend_entry_after",
        "extend_exit_before",
        "contract_entry_after",
        "contract_exit_before",
    ):
        if key not in derive:
            continue
        raw = derive[key]
        if raw is None:
            glyph_def[key] = None
        else:
            targets = _normalize_family_refs(
                raw["targets"],
                family_names,
                context_sets=context_sets,
                context_family=family_name,
                context_label=f"form {form_name!r}" if form_name else "base record",
                field_name=key,
            )
            glyph_def[key] = ExtensionSpec(by=raw["by"], targets=tuple(targets))

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

    replaces_family_feature = form_def.get("replaces_family_feature")
    if replaces_family_feature is not None:
        glyph_def["replaces_family_feature"] = replaces_family_feature

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
        _validate_family_derive(family_name, family_def)
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
                    glyph_families=glyph_families,
                ),
                "form_name": None,
                "output_name": family_name,
                "contextual": False,
                "traits": (),
                "modifiers": (),
            }

        for form_name in family_def.get("forms", {}):
            resolved = _resolve_family_record(
                family_name, family_def, form_name, cache, [],
                glyph_families=glyph_families,
            )
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
            elif variants:
                if variant not in variants:
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


def _normalize_anchors(raw: list[list[int]] | list[int] | None) -> list[list[int]]:
    if raw is None:
        return []
    if isinstance(raw[0], list):
        return cast(list[list[int]], raw)
    return [cast(list[int], raw)]



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
        for side in ("entry", "exit"):
            for suffix in _EXTENSION_SUFFIX.values():
                prefix = f"{side}-{suffix}"
                if modifier.startswith(prefix):
                    compat.update({side, "extended", suffix, prefix})
                    break
            for suffix in _CONTRACTION_SUFFIX.values():
                prefix = f"{side}-{suffix}"
                if modifier.startswith(prefix):
                    compat.update({side, "contracted", suffix, prefix})
                    break
        for side in ("entry", "exit"):
            prefix = f"{side}-trimmed"
            if modifier.startswith(prefix):
                compat.update({side, "trimmed", prefix})
                break
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


_EXTENDED_ENTRY_PREFIXES = tuple(f"entry-{s}" for s in _EXTENSION_SUFFIX.values())
_EXTENDED_EXIT_PREFIXES = tuple(f"exit-{s}" for s in _EXTENSION_SUFFIX.values())
_CONTRACTED_ENTRY_PREFIXES = tuple(f"entry-{s}" for s in _CONTRACTION_SUFFIX.values())
_CONTRACTED_EXIT_PREFIXES = tuple(f"exit-{s}" for s in _CONTRACTION_SUFFIX.values())


def _extended_entry_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith(_EXTENDED_ENTRY_PREFIXES):
            return "." + modifier
    return None


def _extended_exit_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith(_EXTENDED_EXIT_PREFIXES):
            return "." + modifier
    return None


def _contracted_entry_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith(_CONTRACTED_ENTRY_PREFIXES):
            return "." + modifier
    return None


def _contracted_exit_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith(_CONTRACTED_EXIT_PREFIXES):
            return "." + modifier
    return None


def _entry_restriction_y_from_modifiers(modifiers: list[str]) -> int | None:
    for modifier in modifiers:
        for prefix in ("entry-extended-at-", "entry-contracted-at-"):
            if not modifier.startswith(prefix):
                continue
            label = modifier.removeprefix(prefix)
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
        exit_ink_y=glyph_def.get("cursive_exit_ink_y"),
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
        extend_entry_after=glyph_def.get("extend_entry_after"),
        extend_exit_before=glyph_def.get("extend_exit_before"),
        extend_exit_before_gated=tuple(glyph_def.get("extend_exit_before_gated", ())),
        noentry_after=tuple(glyph_def.get("noentry_after", ())),
        extend_exit_no_entry=bool(glyph_def.get("extend_exit_no_entry")),
        noentry_for=noentry_for,
        generated_from=generated_from,
        transform_kind=transform_kind,
        revert_feature=glyph_def.get("revert_feature"),
        gate_feature=glyph_def.get("gate_feature"),
        replaces_family_feature=glyph_def.get("replaces_family_feature"),
        contracted_entry_suffix=_contracted_entry_suffix_from_modifiers(list(resolved_modifiers)),
        contracted_exit_suffix=_contracted_exit_suffix_from_modifiers(list(resolved_modifiers)),
        contract_entry_after=glyph_def.get("contract_entry_after"),
        contract_exit_before=glyph_def.get("contract_exit_before"),
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
    return _expand_anchor_sentinels(metadata)


def _collect_anchor_classes(
    metadata: dict[str, JoinGlyph],
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    exit_classes: dict[int, set[str]] = {}
    entry_classes: dict[int, set[str]] = {}
    for glyph_name, meta in metadata.items():
        for anchor in meta.exit:
            exit_classes.setdefault(anchor[1], set()).add(glyph_name)
        for anchor in meta.entry:
            entry_classes.setdefault(anchor[1], set()).add(glyph_name)
    return (
        {y: sorted(members) for y, members in exit_classes.items()},
        {y: sorted(members) for y, members in entry_classes.items()},
    )


def _expand_anchor_sentinels(metadata: dict[str, JoinGlyph]) -> dict[str, JoinGlyph]:
    exit_classes, entry_classes = _collect_anchor_classes(metadata)

    def _expand_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            return values
        if not any(is_anchor_sentinel(v) for v in values):
            return values
        expanded: list[str] = []
        seen: set[str] = set()
        for value in values:
            parsed = _parse_anchor_sentinel(value)
            if parsed is None:
                if value not in seen:
                    seen.add(value)
                    expanded.append(value)
                continue
            kind, y, excluded_families = parsed
            members = (exit_classes if kind == "exit_y" else entry_classes).get(y, ())
            for member in members:
                if excluded_families:
                    member_family = metadata[member].family if member in metadata else None
                    if member_family in excluded_families:
                        continue
                if member not in seen:
                    seen.add(member)
                    expanded.append(member)
        return tuple(expanded)

    expanded_metadata: dict[str, JoinGlyph] = {}
    for glyph_name, meta in metadata.items():
        new_after = _expand_tuple(meta.after)
        new_before = _expand_tuple(meta.before)
        new_not_after = _expand_tuple(meta.not_after)
        new_not_before = _expand_tuple(meta.not_before)
        if (
            new_after is meta.after
            and new_before is meta.before
            and new_not_after is meta.not_after
            and new_not_before is meta.not_before
        ):
            expanded_metadata[glyph_name] = meta
            continue
        expanded_metadata[glyph_name] = replace(
            meta,
            after=new_after,
            before=new_before,
            not_after=new_not_after,
            not_before=new_not_before,
        )
    return expanded_metadata


def _shift_anchors(anchors: tuple[Anchor, ...], *, dx: int = -1) -> tuple[Anchor, ...]:
    return tuple((x + dx, y) for x, y in anchors)


def _widen_bitmap_with_connector(
    bitmap: tuple[BitmapRow, ...],
    entry_y: int,
    y_offset: int = 0,
    count: int = 1,
) -> tuple[tuple[BitmapRow, ...], int]:
    if not bitmap:
        return bitmap, 0
    height = len(bitmap)
    row_from_bottom = entry_y - y_offset
    connecting_row_idx = (height - 1) - row_from_bottom
    connector_row = bitmap[connecting_row_idx]
    if isinstance(connector_row, str):
        try:
            leftmost_x = connector_row.index("#")
        except ValueError:
            leftmost_x = 0
    else:
        leftmost_x = len(connector_row)
        for i in range(len(connector_row)):
            if connector_row[i]:
                leftmost_x = i
                break
    prepend = max(0, count - leftmost_x)
    new_bitmap: list[BitmapRow] = []
    for index, row in enumerate(bitmap):
        if isinstance(row, str):
            if prepend > 0:
                row = " " * prepend + row
            if index == connecting_row_idx:
                row_list = list(row)
                for pos in range(leftmost_x + prepend - count, leftmost_x + prepend):
                    row_list[pos] = "#"
                new_bitmap.append("".join(row_list))
            else:
                new_bitmap.append(row)
        else:
            if prepend > 0:
                row = tuple([0] * prepend + list(row))
            if index == connecting_row_idx:
                row_list = list(row)
                for pos in range(leftmost_x + prepend - count, leftmost_x + prepend):
                    row_list[pos] = 1
                new_bitmap.append(tuple(row_list))
            else:
                new_bitmap.append(row)
    return tuple(new_bitmap), prepend


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


def _trim_bitmap_left_at(
    bitmap: tuple[BitmapRow, ...],
    entry_y: int,
    y_offset: int,
    trim: int,
) -> tuple[BitmapRow, ...]:
    if trim <= 0 or not bitmap:
        return bitmap
    height = len(bitmap)
    row_from_bottom = entry_y - y_offset
    row_idx = (height - 1) - row_from_bottom
    if not (0 <= row_idx < height):
        return bitmap
    row = bitmap[row_idx]
    limit = min(trim, len(row))
    if limit <= 0:
        return bitmap
    if isinstance(row, str):
        if all(ch == " " for ch in row[:limit]):
            return bitmap
        new_row: BitmapRow = " " * limit + row[limit:]
    else:
        if all(value == 0 for value in row[:limit]):
            return bitmap
        new_row = tuple([0] * limit + list(row[limit:]))
    return tuple(
        new_row if i == row_idx else existing for i, existing in enumerate(bitmap)
    )


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
    for key in (
        "extend_entry_after",
        "extend_exit_before",
        "contract_entry_after",
        "contract_exit_before",
    ):
        spec: ExtensionSpec | None = getattr(join_glyph, key)
        if spec is not None:
            glyph_def[key] = {"by": spec.by, "targets": list(spec.targets)}
    if join_glyph.extend_exit_before_gated:
        glyph_def["extend_exit_before_gated"] = dict(join_glyph.extend_exit_before_gated)
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
    exit_ink_y: int | None | object = _UNSET,
    after: tuple[str, ...] | object = _UNSET,
    before: tuple[str, ...] | object = _UNSET,
    not_after: tuple[str, ...] | object = _UNSET,
    not_before: tuple[str, ...] | object = _UNSET,
    reverse_upgrade_from: tuple[str, ...] | object = _UNSET,
    preferred_over: tuple[str, ...] | object = _UNSET,
    word_final: bool | object = _UNSET,
    extend_entry_after: ExtensionSpec | None | object = _UNSET,
    extend_exit_before: ExtensionSpec | None | object = _UNSET,
    extend_exit_before_gated: tuple[tuple[str, tuple[str, ...]], ...] | object = _UNSET,
    gated_before: tuple[tuple[str, tuple[str, ...]], ...] | object = _UNSET,
    contract_entry_after: ExtensionSpec | None | object = _UNSET,
    contract_exit_before: ExtensionSpec | None | object = _UNSET,
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
    resolved_exit_ink_y = source.exit_ink_y if exit_ink_y is _UNSET else exit_ink_y
    if exit is not _UNSET and not resolved_exit:
        resolved_exit_ink_y = None
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
    resolved_contract_entry_after = (
        source.contract_entry_after if contract_entry_after is _UNSET else contract_entry_after
    )
    resolved_contract_exit_before = (
        source.contract_exit_before if contract_exit_before is _UNSET else contract_exit_before
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
        exit_ink_y=resolved_exit_ink_y,
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
        noentry_after=resolved_noentry_after,
        extend_exit_no_entry=resolved_extend_exit_no_entry,
        noentry_for=resolved_noentry_for,
        generated_from=generated_from,
        transform_kind=transform_kind,
        contracted_entry_suffix=_contracted_entry_suffix_from_modifiers(
            list(resolved_modifiers)
        ),
        contracted_exit_suffix=_contracted_exit_suffix_from_modifiers(
            list(resolved_modifiers)
        ),
        contract_entry_after=resolved_contract_entry_after,
        contract_exit_before=resolved_contract_exit_before,
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
            extend_entry_after=None,
            extend_exit_before=None,
            extend_exit_before_gated=(),
            gated_before=(),
            contract_entry_after=None,
            contract_exit_before=None,
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
    kind: str = "extended",
) -> list[tuple[str, JoinGlyph, bool]]:
    suffix_attr = f"{kind}_{side}_suffix"
    anchor_attr = side
    targets = [(source_name, source_glyph, True)]
    base_name = source_glyph.base_name
    # Entry-side rules flow from the lead component (sequence[0]) of a
    # ligature; exit-side rules flow from the trailing component
    # (sequence[-1]). The other end of the ligature is buried internally
    # and its anchor never reaches a joining boundary, so propagating
    # there would only generate dead variants.
    ligature_position = -1 if side == "exit" else 0

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
        is_ligature = (
            bool(other_sequence) and other_sequence[ligature_position] == base_name
        )
        # A ligature that declares its own `noentry_after` is taking
        # explicit responsibility for its exit-side behavior; the post-liga
        # cleanup that routes `lig → lig.noentry` after specific
        # predecessors is only emitted for the base ligature glyph, so
        # propagating an extension/contraction here would generate a
        # variant that bypasses that cleanup. Stick with the explicit
        # YAML-declared rules in that case.
        if (
            is_ligature
            and side == "exit"
            and other_join_glyph.noentry_after
        ):
            continue
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
        "extend_entry_after": None,
        "extend_exit_before": None,
        "extend_exit_before_gated": (),
        "gated_before": (),
        "contract_entry_after": None,
        "contract_exit_before": None,
        "noentry_after": (),
    }


def _compatible_generated_context(
    target_context: tuple[str, ...],
    source_context: tuple[str, ...],
) -> tuple[str, ...]:
    if not source_context:
        return ()
    if not target_context:
        return ()
    source_names = set(source_context)
    return tuple(name for name in target_context if name in source_names)


def _compatible_generated_gated_context(
    target_context: tuple[str, ...],
    source_gated_context: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not source_gated_context:
        return ()
    if not target_context:
        return ()
    target_names = set(target_context)
    rebuilt: list[tuple[str, tuple[str, ...]]] = []
    for feature_tag, names in source_gated_context:
        kept = tuple(name for name in names if name in target_names)
        if kept:
            rebuilt.append((feature_tag, kept))
    return tuple(rebuilt)


def _selector_has_anchor_y(
    join_glyphs: dict[str, JoinGlyph],
    selector: str,
    y: int,
    *,
    side: str,
) -> bool:
    sentinel = _parse_anchor_sentinel(selector)
    if sentinel is not None:
        sentinel_kind, sentinel_y, _excluded = sentinel
        return sentinel_kind == f"{side}_y" and sentinel_y == y

    meta = join_glyphs.get(selector)
    if meta is not None:
        anchors = meta.exit if side == "exit" else (*meta.entry, *meta.entry_curs_only)
        if any(anchor[1] == y for anchor in anchors):
            return True
        if selector != meta.base_name:
            return False

    family_name = selector.split(".")[0]
    for candidate in join_glyphs.values():
        if candidate.base_name != family_name:
            continue
        anchors = (
            candidate.exit
            if side == "exit"
            else (*candidate.entry, *candidate.entry_curs_only)
        )
        if any(anchor[1] == y for anchor in anchors):
            return True
    return False


def _filter_context_by_anchor_y(
    join_glyphs: dict[str, JoinGlyph],
    source_context: tuple[str, ...],
    y: int,
    *,
    side: str,
) -> tuple[str, ...]:
    return tuple(
        selector
        for selector in source_context
        if _selector_has_anchor_y(join_glyphs, selector, y, side=side)
    )


def _filter_gated_context_by_anchor_y(
    join_glyphs: dict[str, JoinGlyph],
    source_gated_context: tuple[tuple[str, tuple[str, ...]], ...],
    y: int,
    *,
    side: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    rebuilt: list[tuple[str, tuple[str, ...]]] = []
    for feature_tag, selectors in source_gated_context:
        kept = _filter_context_by_anchor_y(
            join_glyphs,
            selectors,
            y,
            side=side,
        )
        if kept:
            rebuilt.append((feature_tag, kept))
    return tuple(rebuilt)


def _add_entry_extension_variants(
    variants: dict[str, JoinGlyph],
    join_glyphs: dict[str, JoinGlyph],
    *,
    target_name: str,
    target_glyph: JoinGlyph,
    source_after: tuple[str, ...],
    source_entry_ys: frozenset[int],
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

        new_bitmap, prepend = _widen_bitmap_with_connector(
            target_glyph.bitmap,
            y,
            target_glyph.y_offset,
            count=count,
        )
        kwargs = {
            "bitmap": new_bitmap,
            "exit": _shift_anchors(target_glyph.exit, dx=prepend),
            "add_modifiers": (modifier,),
            "generated_from": target_name,
            "transform_kind": kind,
        }
        if use_height_specific_names:
            kwargs["entry"] = (anchor,)

        filtered_after = _filter_context_by_anchor_y(
            join_glyphs,
            source_after,
            y,
            side="exit",
        )
        if is_source:
            kwargs["after"] = filtered_after
            kwargs["extend_entry_after"] = None
            kwargs["contract_entry_after"] = None
        else:
            kwargs.update(_cleared_extension_context())
            if y in source_entry_ys:
                kwargs["after"] = _compatible_generated_context(
                    target_glyph.after,
                    filtered_after,
                )

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
    source_exit_ys: frozenset[int],
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
        "exit": _shift_anchors(target_glyph.exit, dx=count),
        "extend_exit_no_entry": False,
        "add_modifiers": (f"exit-{suffix_word}",),
        "generated_from": target_name,
        "transform_kind": kind,
    }
    if is_source:
        kwargs["entry"] = () if target_glyph.extend_exit_no_entry else target_glyph.entry
        kwargs["before"] = _filter_context_by_anchor_y(
            join_glyphs,
            source_before,
            exit_y,
            side="entry",
        )
        kwargs["extend_exit_before"] = None
        kwargs["extend_exit_before_gated"] = ()
        kwargs["gated_before"] = _filter_gated_context_by_anchor_y(
            join_glyphs,
            source_gated_before,
            exit_y,
            side="entry",
        )
        kwargs["contract_exit_before"] = None
    else:
        kwargs.update(_cleared_extension_context())
        if exit_y in source_exit_ys:
            filtered_before = _filter_context_by_anchor_y(
                join_glyphs,
                source_before,
                exit_y,
                side="entry",
            )
            filtered_gated_before = _filter_gated_context_by_anchor_y(
                join_glyphs,
                source_gated_before,
                exit_y,
                side="entry",
            )
            kwargs["before"] = _compatible_generated_context(
                target_glyph.before,
                filtered_before,
            )
            kwargs["gated_before"] = _compatible_generated_gated_context(
                target_glyph.before,
                filtered_gated_before,
            )

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
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    field = "extend_entry_after" if side == "entry" else "extend_exit_before"
    variants: dict[str, JoinGlyph] = {}
    for name, join_glyph in sorted(join_glyphs.items()):
        spec: ExtensionSpec | None = getattr(join_glyph, field)
        gated_entries = (
            join_glyph.extend_exit_before_gated
            if side == "exit" and spec is not None
            else ()
        )
        context_glyphs = spec.targets if spec is not None else ()
        if not context_glyphs and not gated_entries:
            continue
        if not getattr(join_glyph, side):
            continue

        count = spec.by if spec is not None else 1
        suffix_word = _EXTENSION_SUFFIX[count]
        source_anchor_ys = frozenset(anchor[1] for anchor in getattr(join_glyph, side))

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
                    source_after=context_glyphs,
                    source_entry_ys=source_anchor_ys,
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
                    source_before=context_glyphs,
                    source_gated_before=gated_entries,
                    source_exit_ys=source_anchor_ys,
                    count=count,
                    suffix_word=suffix_word,
                    transforms=transforms,
                    is_source=is_source,
                )

    return variants


def _add_entry_contraction_variants(
    variants: dict[str, JoinGlyph],
    join_glyphs: dict[str, JoinGlyph],
    *,
    target_name: str,
    target_glyph: JoinGlyph,
    source_after: tuple[str, ...],
    source_entry_ys: frozenset[int],
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

        contracted_anchor = (anchor[0] + count, anchor[1])
        kwargs = {
            "add_modifiers": (modifier,),
            "generated_from": target_name,
            "transform_kind": kind,
        }
        if use_height_specific_names:
            kwargs["entry"] = (contracted_anchor,)
        else:
            kwargs["entry"] = _shift_anchors(target_glyph.entry, dx=count)

        filtered_after = _filter_context_by_anchor_y(
            join_glyphs,
            source_after,
            y,
            side="exit",
        )
        if is_source:
            kwargs["after"] = filtered_after
            kwargs["contract_entry_after"] = None
        else:
            kwargs.update(_cleared_extension_context())
            if y in source_entry_ys:
                kwargs["after"] = _compatible_generated_context(
                    target_glyph.after,
                    filtered_after,
                )

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


def _add_exit_contraction_variant(
    variants: dict[str, JoinGlyph],
    join_glyphs: dict[str, JoinGlyph],
    *,
    target_name: str,
    target_glyph: JoinGlyph,
    source_before: tuple[str, ...],
    source_exit_ys: frozenset[int],
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
    kwargs = {
        "exit": _shift_anchors(target_glyph.exit, dx=-count),
        "add_modifiers": (f"exit-{suffix_word}",),
        "generated_from": target_name,
        "transform_kind": kind,
    }
    if is_source:
        kwargs["before"] = _filter_context_by_anchor_y(
            join_glyphs,
            source_before,
            exit_y,
            side="entry",
        )
        kwargs["contract_exit_before"] = None
    else:
        kwargs.update(_cleared_extension_context())
        if exit_y in source_exit_ys:
            filtered_before = _filter_context_by_anchor_y(
                join_glyphs,
                source_before,
                exit_y,
                side="entry",
            )
            kwargs["before"] = _compatible_generated_context(
                target_glyph.before,
                filtered_before,
            )

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
    )


def _add_entry_trimmed_variant(
    variants: dict[str, JoinGlyph],
    join_glyphs: dict[str, JoinGlyph],
    *,
    source_contracted_name: str,
    receiver_name: str,
    receiver_glyph: JoinGlyph,
    join_y: int,
    count: int,
    transforms: list[JoinTransform] | None,
) -> None:
    for entry_anchor in receiver_glyph.entry:
        if entry_anchor[1] != join_y:
            continue
        entry_y = entry_anchor[1]
        modifier = f"entry-trimmed-by-{count}"
        variant_name = f"{receiver_name}.{modifier}"

        existing = variants.get(variant_name)
        if existing is not None:
            if source_contracted_name in existing.after:
                continue
            merged_after = tuple(sorted({*existing.after, source_contracted_name}))
            variants[variant_name] = replace(existing, after=merged_after)
            continue
        if variant_name in join_glyphs:
            continue

        new_bitmap = _trim_bitmap_left_at(
            receiver_glyph.bitmap, entry_y, receiver_glyph.y_offset, count
        )
        if new_bitmap is receiver_glyph.bitmap:
            continue
        kwargs = {
            "bitmap": new_bitmap,
            "add_modifiers": (modifier,),
            "generated_from": receiver_name,
            "transform_kind": "entry-trimmed",
        }
        kwargs.update(_cleared_extension_context())
        kwargs["after"] = (source_contracted_name,)
        variants[variant_name] = derive_join_glyph(
            receiver_glyph,
            name=variant_name,
            **kwargs,
        )
        _record_transform(
            transforms,
            kind="entry-trimmed",
            source_name=receiver_name,
            target_name=variant_name,
            count=count,
            restricted_y=entry_y,
        )


def _contraction_source_matches_after(
    after_tuple: tuple[str, ...],
    source_name: str,
    source_family: str | None,
) -> bool:
    """True if the contraction source belongs in an extended receiver's after-list.

    A receiver variant whose `extended_entry_suffix` is set has a bitmap
    authored for a specific set of left-context families/forms (the receiver's
    `extend_entry_after.targets`). The trim sibling is only meaningful when
    the contracting source is one of those — otherwise the trimmed bitmap
    geometry doesn't correspond to the actual left context the trim will fire
    in.
    """
    if source_name in after_tuple:
        return True
    if source_family is not None and source_family in after_tuple:
        return True
    return False


def _generate_contracted_variants(
    join_glyphs: dict[str, JoinGlyph],
    *,
    side: str,
    transforms: list[JoinTransform] | None = None,
) -> dict[str, JoinGlyph]:
    field = "contract_entry_after" if side == "entry" else "contract_exit_before"
    variants: dict[str, JoinGlyph] = {}
    for name, join_glyph in sorted(join_glyphs.items()):
        spec: ExtensionSpec | None = getattr(join_glyph, field)
        if spec is None:
            continue
        context_glyphs = spec.targets
        if not context_glyphs:
            continue
        if not getattr(join_glyph, side):
            continue

        count = spec.by
        suffix_word = _CONTRACTION_SUFFIX[count]
        source_anchor_ys = frozenset(anchor[1] for anchor in getattr(join_glyph, side))

        for target_name, target_glyph, is_source in _iter_related_extension_targets(
            join_glyphs,
            source_name=name,
            source_glyph=join_glyph,
            side=side,
            kind="contracted",
        ):
            if side == "entry":
                _add_entry_contraction_variants(
                    variants,
                    join_glyphs,
                    target_name=target_name,
                    target_glyph=target_glyph,
                    source_after=context_glyphs,
                    source_entry_ys=source_anchor_ys,
                    use_height_specific_names=len(join_glyph.entry) > 1,
                    count=count,
                    suffix_word=suffix_word,
                    transforms=transforms,
                    is_source=is_source,
                )
            else:
                _add_exit_contraction_variant(
                    variants,
                    join_glyphs,
                    target_name=target_name,
                    target_glyph=target_glyph,
                    source_before=context_glyphs,
                    source_exit_ys=source_anchor_ys,
                    count=count,
                    suffix_word=suffix_word,
                    transforms=transforms,
                    is_source=is_source,
                )

        if side == "exit":
            contracted_source_name = f"{name}.exit-{suffix_word}"
            if contracted_source_name in variants:
                join_y = join_glyph.exit[0][1]
                for receiver_family in context_glyphs:
                    family_glyph = join_glyphs.get(receiver_family)
                    if family_glyph is None:
                        continue
                    for receiver_name, receiver_glyph, _is_receiver_source in (
                        _iter_related_extension_targets(
                            join_glyphs,
                            source_name=receiver_family,
                            source_glyph=family_glyph,
                            side="entry",
                            kind="contracted",
                        )
                    ):
                        if receiver_glyph.extended_exit_suffix is not None:
                            continue
                        if receiver_glyph.extended_entry_suffix is not None:
                            if not _contraction_source_matches_after(
                                receiver_glyph.after, name, join_glyph.family
                            ):
                                continue
                        _add_entry_trimmed_variant(
                            variants,
                            join_glyphs,
                            source_contracted_name=contracted_source_name,
                            receiver_name=receiver_name,
                            receiver_glyph=receiver_glyph,
                            join_y=join_y,
                            count=count,
                            transforms=transforms,
                        )
        # TODO(contract_entry_after): mirror the receiver-trim pass on the
        # entry side when a use case appears (clear the rightmost
        # `(by - rightward_stub)` pixels at the receiver's exit-Y row).

    return variants


def expand_selectors_for_ligatures(
    join_glyphs: dict[str, JoinGlyph],
) -> dict[str, JoinGlyph]:
    """Expand positive calt selectors so they fire on the first/last component
    of any ligature whose non-first/non-last components were named explicitly.

    `before:` / `gated_before:` are forward-context lookups in `calt`, which
    runs before `calt_liga` collapses sequences into ligature glyphs. So a
    selector that names a ligature's second (or later) component misses,
    because the immediate next glyph in the pre-liga stream is the ligature's
    *first* component. Likewise backward-context `after:` lookups only see the
    ligature's *last* component pre-liga.

    This pass adds the missing endpoints so the YAML can stay declarative
    ("fire before qsUtter") and stay correct as new ligatures land. Each
    candidate addition is gated by Y compatibility: an endpoint family is only
    added when at least one of its reachable variants (or the ligature's
    canonical record) carries a matching cursive anchor on the side the
    source needs. This keeps spurious entries out of the join-consistency
    check while still covering the cases where the ligature's half/baseline
    form actually accepts the source.

    Negative selectors (`not_before:` / `not_after:`) are left untouched: the
    intent of those lists is almost always literal ("don't fire before this
    specific glyph"), and expanding them tends to suppress shaping in cases
    where the original author wanted the lookup to keep firing. If a
    negative-side ligature exception is genuinely needed it can be authored by
    hand.
    """
    ligature_records: list[JoinGlyph] = []
    for record in join_glyphs.values():
        if len(record.sequence) < 2:
            continue
        if record.name != record.base_name:
            continue
        if record.is_noentry:
            continue
        if record.extended_entry_suffix is not None:
            continue
        if record.extended_exit_suffix is not None:
            continue
        if not all(component in join_glyphs for component in record.sequence):
            continue
        ligature_records.append(record)

    if not ligature_records:
        return join_glyphs

    base_to_variants: dict[str, set[str]] = {}
    for record in join_glyphs.values():
        base_to_variants.setdefault(record.base_name, set()).add(record.name)

    def _ligature_entry_ys(lig_base: str) -> frozenset[int]:
        ys: set[int] = set()
        for variant_name in base_to_variants.get(lig_base, ()):
            variant = join_glyphs[variant_name]
            for anchor in (*variant.entry, *variant.entry_curs_only):
                ys.add(anchor[1])
        return frozenset(ys)

    def _ligature_exit_ys(lig_base: str) -> frozenset[int]:
        ys: set[int] = set()
        for variant_name in base_to_variants.get(lig_base, ()):
            variant = join_glyphs[variant_name]
            for anchor in variant.exit:
                ys.add(anchor[1])
        return frozenset(ys)

    def _canonical_entry_ys(family: str) -> frozenset[int]:
        canonical = join_glyphs.get(family)
        if canonical is None or canonical.name != canonical.base_name:
            return frozenset()
        canonical_ys = frozenset(
            anchor[1] for anchor in (*canonical.entry, *canonical.entry_curs_only)
        )
        if canonical_ys:
            return canonical_ys
        # Some families (e.g., qsJai) carry no anchors on the canonical record
        # itself and split entry coverage across forms. Fall back to the union
        # of form-level anchors so the novelty filter still recognizes Ys that
        # the family already reaches as a non-ligature.
        ys: set[int] = set()
        for variant_name in base_to_variants.get(family, ()):
            variant = join_glyphs[variant_name]
            for anchor in (*variant.entry, *variant.entry_curs_only):
                ys.add(anchor[1])
        return frozenset(ys)

    def _canonical_exit_ys(family: str) -> frozenset[int]:
        canonical = join_glyphs.get(family)
        if canonical is None or canonical.name != canonical.base_name:
            return frozenset()
        canonical_ys = frozenset(anchor[1] for anchor in canonical.exit)
        if canonical_ys:
            return canonical_ys
        ys: set[int] = set()
        for variant_name in base_to_variants.get(family, ()):
            variant = join_glyphs[variant_name]
            for anchor in variant.exit:
                ys.add(anchor[1])
        return frozenset(ys)

    # Forward expansion entries: keyed by component name (base or variant),
    # value is a list of (first_component_family, candidate_entry_ys,
    # canonical_entry_ys) triples.
    #
    # candidate_entry_ys are the Ys reachable through the *ligature's* own
    # variants — those are the Ys the source actually meets after `calt_liga`
    # collapses the components. canonical_entry_ys are the Ys the first
    # component's own variants already provide pre-liga (across the canonical
    # record and any forms). The expansion only earns its keep when the
    # ligature opens up a Y the first component's variants do not already
    # cover; otherwise the lookup would just over-fire on adjacent
    # first-components without unlocking any new join, crowding out broader
    # fallback forms in the process.
    forward_entries_by_component: dict[
        str, list[tuple[str, frozenset[int], frozenset[int]]]
    ] = {}
    backward_entries_by_component: dict[
        str, list[tuple[str, frozenset[int], frozenset[int]]]
    ] = {}

    for record in ligature_records:
        sequence = tuple(record.sequence)
        first, last = sequence[0], sequence[-1]
        lig_entry_ys = _ligature_entry_ys(record.base_name)
        lig_exit_ys = _ligature_exit_ys(record.base_name)
        first_canonical_entry_ys = _canonical_entry_ys(first)
        last_canonical_exit_ys = _canonical_exit_ys(last)

        for component in sequence[1:]:
            keys = {component, *base_to_variants.get(component, ())}
            for key in keys:
                forward_entries_by_component.setdefault(key, []).append(
                    (first, lig_entry_ys, first_canonical_entry_ys)
                )
        for component in sequence[:-1]:
            keys = {component, *base_to_variants.get(component, ())}
            for key in keys:
                backward_entries_by_component.setdefault(key, []).append(
                    (last, lig_exit_ys, last_canonical_exit_ys)
                )

        # Post-liga matching: register every variant of the ligature itself
        # under the component-variant key it represents, so a successor's
        # selector that names that component variant matches the ligature
        # glyph after `calt_liga` collapses it. Without this, the post-liga
        # cleanup at `quikscript_fea.py::_collect_post_liga_right_cleanup_rules`
        # finds no ligature glyph in the source's `after_glyphs` and downgrades
        # the variant back to base. Adding the ligature glyphs also unlocks
        # the calt-post-liga forward-rule emission, which only fires when a
        # ligature glyph already appears in `after_glyphs`.
        #
        # Each ligature variant is keyed under the trailing component's base
        # name (covers `after: [qsY]` which means "any qsY variant") and, if
        # the variant carries an exit-side suffix (`extended_exit_suffix` or
        # `contracted_exit_suffix`), also under the trailing component variant
        # carrying that same suffix (covers `after: [qsY.exit-extended]` etc.,
        # which discriminate by component state). Symmetric for entry-side
        # suffix and the lead component on the forward side. This mirrors the
        # `calt_liga` glyph-selection rule that maps `(qsX, qsY.<suffix>)` to
        # `qsX_qsY.<suffix>`: the same suffix carries from component-variant
        # to ligature-variant, so the inverse mapping is unambiguous.
        for lig_variant_name in sorted(base_to_variants.get(record.base_name, {record.name})):
            lig_variant = join_glyphs[lig_variant_name]
            lig_variant_entry_ys = frozenset(
                anchor[1]
                for anchor in (*lig_variant.entry, *lig_variant.entry_curs_only)
            )
            lig_variant_exit_ys = frozenset(
                anchor[1] for anchor in lig_variant.exit
            )

            # Register under every component-base name in the sequence so a
            # selector that mentions any component matches the ligature
            # post-liga. For example, a 3-component ligature qsA_qsB_qsC is
            # exposed under qsA, qsB, and qsC equally — pre-liga, calt only
            # ever sees qsA at the boundary, but post-liga the ligature glyph
            # should satisfy any of the component selectors.
            forward_keys: set[str] = set(sequence)
            backward_keys: set[str] = set(sequence)

            # Suffix-aware keying lets a selector that names a specific
            # component variant (e.g., `qsUtter.exit-doubly-contracted`)
            # match only the ligature variants that represent the same
            # state — `calt_liga` maps `(qsX, qsUtter.exit-doubly-contracted)`
            # to `qsX_qsUtter.exit-doubly-contracted`, so the same suffix
            # carries through. Without this, a base ligature like
            # `qsX_qsUtter` would also match `qsUtter.exit-doubly-contracted`
            # selectors and leave bitmap-misalignment warnings.
            entry_suffix = lig_variant.extended_entry_suffix or ""
            if entry_suffix:
                first_variant = first + entry_suffix
                if first_variant in join_glyphs:
                    forward_keys.add(first_variant)

            exit_suffix = (
                lig_variant.extended_exit_suffix
                or lig_variant.contracted_exit_suffix
                or ""
            )
            if exit_suffix:
                last_variant = last + exit_suffix
                if last_variant in join_glyphs:
                    backward_keys.add(last_variant)

            if lig_variant_entry_ys:
                for key in forward_keys:
                    forward_entries_by_component.setdefault(key, []).append(
                        (lig_variant_name, lig_variant_entry_ys, _LIG_ENDPOINT_BYPASS)
                    )
            if lig_variant_exit_ys:
                for key in backward_keys:
                    backward_entries_by_component.setdefault(key, []).append(
                        (lig_variant_name, lig_variant_exit_ys, _LIG_ENDPOINT_BYPASS)
                    )

    def _expected_runtime_exit_suffix(source_family: str, last_family: str) -> str:
        """When source family `source_family` follows a glyph in family
        `last_family`, what exit suffix does `last_family`'s base form mutate
        into at runtime? Return "" if no rule applies. Used to filter ligature
        endpoints so a source whose family triggers contraction/extension on
        the trailing component only matches ligature variants representing the
        same runtime state. Without this filter, a base ligature like
        `qsX_qsUtter` would be added to `qsJai`'s after list even though the
        runtime `qsUtter.exit-doubly-contracted` always wins before `qsJai`."""
        last_meta = join_glyphs.get(last_family)
        if last_meta is None:
            return ""
        if last_meta.contract_exit_before and source_family in last_meta.contract_exit_before.targets:
            suffix_word = _CONTRACTION_SUFFIX.get(last_meta.contract_exit_before.by)
            if suffix_word:
                return f".exit-{suffix_word}"
        if last_meta.extend_exit_before and source_family in last_meta.extend_exit_before.targets:
            suffix_word = _EXTENSION_SUFFIX.get(last_meta.extend_exit_before.by)
            if suffix_word:
                return f".exit-{suffix_word}"
        return ""

    def _expected_runtime_entry_suffix(source_family: str, first_family: str) -> str:
        """Mirror of `_expected_runtime_exit_suffix` for the lead-component
        side. When source family `source_family` precedes a ligature whose
        first component is in family `first_family`, this is the entry suffix
        that `first_family`'s base form mutates into."""
        first_meta = join_glyphs.get(first_family)
        if first_meta is None:
            return ""
        if first_meta.contract_entry_after and source_family in first_meta.contract_entry_after.targets:
            suffix_word = _CONTRACTION_SUFFIX.get(first_meta.contract_entry_after.by)
            if suffix_word:
                return f".entry-{suffix_word}"
        if first_meta.extend_entry_after and source_family in first_meta.extend_entry_after.targets:
            suffix_word = _EXTENSION_SUFFIX.get(first_meta.extend_entry_after.by)
            if suffix_word:
                return f".entry-{suffix_word}"
        return ""

    def _selector_families(selectors: tuple[str, ...]) -> set[str]:
        families: set[str] = set()
        for selector in selectors:
            meta = join_glyphs.get(selector)
            if meta is not None:
                families.add(meta.base_name)
            else:
                families.add(selector.split(".", 1)[0])
        return families

    def _endpoint_accepts_source_family(
        endpoint_meta: JoinGlyph, source_family: str, side: str
    ) -> bool:
        # When the source's `before` adds a ligature endpoint, the source
        # precedes the ligature. The ligature must accept the source as a
        # left-side neighbor — i.e., source_family must be in the ligature's
        # own `after` (positive selector) or the ligature must have no
        # `after` constraint at all. Mirror for the `after` side.
        if side == "entry":
            if endpoint_meta.after:
                if source_family not in _selector_families(endpoint_meta.after):
                    return False
        else:  # side == "exit"
            if endpoint_meta.before:
                if source_family not in _selector_families(endpoint_meta.before):
                    return False
        return True

    def _ligature_variant_matches_runtime(
        endpoint_meta: JoinGlyph, source_family: str, side: str
    ) -> bool:
        sequence = endpoint_meta.sequence
        if not sequence:
            return True
        if side == "exit":
            expected = _expected_runtime_exit_suffix(source_family, sequence[-1])
            actual = (
                endpoint_meta.contracted_exit_suffix
                or endpoint_meta.extended_exit_suffix
                or ""
            )
        else:
            expected = _expected_runtime_entry_suffix(source_family, sequence[0])
            actual = endpoint_meta.extended_entry_suffix or ""
        return actual == expected

    def _additions(
        field: tuple[str, ...],
        index: dict[str, list[tuple[str, frozenset[int], frozenset[int]]]],
        anchor_ys: frozenset[int],
        *,
        source_family: str | None,
        side: str,
    ) -> set[str]:
        existing = set(field)
        additions: set[str] = set()
        for glyph in field:
            for endpoint, candidate_ys, canonical_ys in index.get(glyph, ()):
                if endpoint in existing:
                    continue
                novel_ys = (anchor_ys & candidate_ys) - canonical_ys
                if not novel_ys:
                    continue
                if source_family is not None:
                    endpoint_meta = join_glyphs.get(endpoint)
                    if endpoint_meta is not None:
                        if (
                            side == "entry"
                            and source_family in endpoint_meta.noentry_after
                        ):
                            # The ligature drops its entry when this source
                            # family precedes it, so the source can never
                            # actually join forward into it. Adding it to
                            # the source's `before` would just confuse the
                            # join validator and emit dead post-liga rules.
                            continue
                        if endpoint_meta.sequence and not _ligature_variant_matches_runtime(
                            endpoint_meta, source_family, side
                        ):
                            # The trailing (or lead) component's base form
                            # mutates into a specific suffix variant when
                            # this source family follows (or precedes), so
                            # only the matching ligature variant can actually
                            # appear in the buffer at runtime. Skip ligature
                            # variants whose state can't be reached.
                            continue
                        if not _endpoint_accepts_source_family(
                            endpoint_meta, source_family, side
                        ):
                            # Some ligatures carry their own `select.after`
                            # / `select.before` constraint that gates whether
                            # the ligature ever fires (e.g.,
                            # `qsThey_qsUtter` only collapses when its
                            # `after` lists a context-set match). If the
                            # source's family isn't in that list, the
                            # ligature never appears in the buffer next to
                            # this source — adding it to the selector list
                            # would generate a one-sided join warning.
                            continue
                additions.add(endpoint)
        return additions

    def _expand_filtered(
        field: tuple[str, ...],
        index: dict[str, list[tuple[str, frozenset[int], frozenset[int]]]],
        anchor_ys: frozenset[int] | None,
        *,
        source_family: str | None,
        side: str,
    ) -> tuple[str, ...]:
        # An empty anchor set means the source has no cursive anchor on the
        # side the selector cares about, so a ligature-bridged join can never
        # form. Skip expansion entirely; the selector keeps its original
        # literal interpretation.
        if not field or not anchor_ys:
            return field
        new_additions = _additions(
            field,
            index,
            anchor_ys,
            source_family=source_family,
            side=side,
        )
        if not new_additions:
            return field
        return tuple([*field, *sorted(new_additions)])

    def _expand_gated(
        gated: tuple[tuple[str, tuple[str, ...]], ...],
        anchor_ys: frozenset[int] | None,
        *,
        source_family: str | None,
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        if not gated:
            return gated
        rebuilt: list[tuple[str, tuple[str, ...]]] = []
        changed = False
        for feature_tag, families in gated:
            new_families = _expand_filtered(
                families,
                forward_entries_by_component,
                anchor_ys,
                source_family=source_family,
                side="entry",
            )
            if new_families is not families:
                changed = True
            rebuilt.append((feature_tag, new_families))
        if not changed:
            return gated
        return tuple(rebuilt)

    updated: dict[str, JoinGlyph] = {}
    for name, record in join_glyphs.items():
        source_exit_ys = (
            frozenset(anchor[1] for anchor in record.exit) if record.exit else None
        )
        source_entry_ys = (
            frozenset(
                anchor[1] for anchor in (*record.entry, *record.entry_curs_only)
            )
            if record.entry or record.entry_curs_only
            else None
        )
        source_family = record.family or record.base_name
        new_before = _expand_filtered(
            record.before,
            forward_entries_by_component,
            source_exit_ys,
            source_family=source_family,
            side="entry",
        )
        new_gated_before = _expand_gated(
            record.gated_before,
            source_exit_ys,
            source_family=source_family,
        )
        new_after = _expand_filtered(
            record.after,
            backward_entries_by_component,
            source_entry_ys,
            source_family=source_family,
            side="exit",
        )
        if (
            new_before is record.before
            and new_gated_before is record.gated_before
            and new_after is record.after
        ):
            updated[name] = record
            continue
        updated[name] = replace(
            record,
            before=new_before,
            gated_before=new_gated_before,
            after=new_after,
        )
    return updated


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
    for side in ("entry", "exit"):
        expanded.update(
            _generate_extended_variants(expanded, side=side, transforms=transforms)
        )
    for side in ("entry", "exit"):
        expanded.update(
            _generate_contracted_variants(expanded, side=side, transforms=transforms)
        )
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
        join_glyphs = _inherit_ligature_entries_from_lead(join_glyphs)
        join_glyphs, transforms = expand_join_transforms(
            join_glyphs,
            has_zwnj="uni200C" in glyph_data.get("glyphs", {}),
        )
    join_glyphs = expand_selectors_for_ligatures(join_glyphs)
    join_glyphs = _expand_anchor_sentinels(join_glyphs)
    join_glyphs = _propagate_noentry_after_to_not_before(join_glyphs)
    return join_glyphs, transforms


def _format_anchor(anchor: Anchor) -> str:
    return f"[{anchor[0]}, {anchor[1]}]"


def _format_anchors(anchors: tuple[Anchor, ...]) -> str:
    if len(anchors) == 1:
        return _format_anchor(anchors[0])
    return "[" + ", ".join(_format_anchor(a) for a in anchors) + "]"


def _leftmost_ink_column(row: BitmapRow) -> int | None:
    """Return the column index of the leftmost ink pixel in a bitmap row,
    or ``None`` if the row is entirely blank."""
    if isinstance(row, str):
        for i, ch in enumerate(row):
            if ch == "#":
                return i
        return None
    for i, value in enumerate(row):
        if value:
            return i
    return None


def _bitmap_row_at_y(
    bitmap: tuple[BitmapRow, ...],
    y_offset: int,
    y: int,
) -> BitmapRow | None:
    """Return the bitmap row that lives at glyph-space ``y``, or ``None``
    if no such row exists. Top row is at ``y = (len(bitmap) - 1) + y_offset``;
    bottom row is at ``y = y_offset``."""
    if not bitmap:
        return None
    top_y = (len(bitmap) - 1) + y_offset
    index = top_y - y
    if index < 0 or index >= len(bitmap):
        return None
    return bitmap[index]


def _bitmaps_align_at_y(
    source_glyph: JoinGlyph,
    target_glyph: JoinGlyph,
    y: int,
) -> bool:
    """Check that ``source_glyph`` and ``target_glyph`` share the same
    leftmost-ink column at glyph-space ``y``. Used as a safety guard before
    copying an entry anchor's x-coordinate from one glyph onto another:
    if the lead's entry coordinates land in different bitmap territory on
    the ligature, the join would leave a visible bitmap gap."""
    source_row = _bitmap_row_at_y(source_glyph.bitmap, source_glyph.y_offset, y)
    target_row = _bitmap_row_at_y(target_glyph.bitmap, target_glyph.y_offset, y)
    if source_row is None or target_row is None:
        return False
    return _leftmost_ink_column(source_row) == _leftmost_ink_column(target_row)


def _find_lead_entry_source(
    join_glyphs: dict[str, JoinGlyph],
    lead_family: str,
) -> tuple[tuple[Anchor, ...], JoinGlyph] | None:
    """Pick the entry anchor a ligature should inherit from its lead.

    Preference order:
      1. The compiled lead-prop glyph (named exactly ``lead_family``) if its
         entry is non-empty. The prop is the unrestricted default form, so
         its entry coordinates are always safe to copy.
      2. The compiled glyph named ``f"{lead_family}.entry-xheight"`` if it
         exists, has an entry, and **has no ``after`` constraint**. An
         ``after``-restricted entry-xheight form (e.g. ``qsFee.entry-xheight``,
         ``qsJai.entry-xheight``) only fires for specific predecessors;
         copying its anchor onto a ligature would silently grant the
         ligature an entry it isn't supposed to advertise to other
         predecessors. The current explicit declarations on
         ``qsThey_qsUtter`` / ``qsJai_qsUtter`` reflect that nuance and
         must keep their own ``select.after`` to remain context-equivalent.

    More-specific entry forms (e.g. ``qsTea.entry-xheight.after-fee``) are
    intentionally **not** candidates: they layer extra context on top of the
    canonical form. We only need *one* unconditional anchor to seed the
    ligature; ``_iter_related_extension_targets`` will still propagate
    `extend_entry_after` rules from any compatible source by matching on
    ``base_name``.

    Returns ``(entries, source_glyph)`` or ``None`` if no inheritable entry
    exists.
    """
    lead_prop = join_glyphs.get(lead_family)
    if lead_prop is not None and lead_prop.entry:
        return lead_prop.entry, lead_prop

    canonical = join_glyphs.get(f"{lead_family}.entry-xheight")
    if canonical is not None and canonical.entry and not canonical.after:
        return canonical.entry, canonical

    return None


def _inherit_ligature_entries_from_lead(
    join_glyphs: dict[str, JoinGlyph],
) -> dict[str, JoinGlyph]:
    """Fill in missing entry anchors on ligatures from the lead component.

    For every JoinGlyph whose ``sequence`` has length ≥ 2 and whose ``entry``
    is empty, copy the entry anchor from the lead component's compiled glyph
    (see ``_find_lead_entry_source`` for the lookup rule). This lets the
    existing ``_iter_related_extension_targets`` machinery propagate
    ``extend_entry_after`` rules onto the ligature without YAML duplication.

    Also emits ``LigatureEntryInheritanceWarning`` for every ligature that
    declares its own ``entry`` anchor — the explicit declaration is now a
    candidate for cleanup (matching inheritance) or a drift hazard
    (mismatched). The warning is informational; the explicit YAML always
    wins until manually removed.

    Half/alt-trait ligature variants (e.g. ``qsDay_qsUtter.half``) are
    skipped: their entry coordinates legitimately differ from the lead's
    prop entry (a half lead exits at the baseline, not x-height), so prop
    inheritance is never the right rule for them.

    Bitmap alignment is checked at the entry's Y row: if the ligature's
    leftmost ink column at that row differs from the lead's, inheritance
    would create a join with a visible gap, so we skip it. The ligature
    stays entry-less unless its YAML explicitly declares one.
    """
    updated = dict(join_glyphs)
    for name, glyph in sorted(join_glyphs.items()):
        if len(glyph.sequence) < 2:
            continue
        if glyph.generated_from is not None:
            continue
        if glyph.traits:
            continue
        lead = glyph.sequence[0]
        inheritable = _find_lead_entry_source(join_glyphs, lead)

        if not glyph.entry:
            if inheritable is None:
                continue
            inherited_entries, source_glyph = inheritable
            entry_y = inherited_entries[0][1]
            if not _bitmaps_align_at_y(source_glyph, glyph, entry_y):
                continue
            updated[name] = replace(glyph, entry=inherited_entries)
            continue

        if inheritable is None:
            warnings.warn(
                f"{name}: declares entry {_format_anchors(glyph.entry)}; "
                f"lead {lead} has no auto-inheritable entry-bearing form. "
                f"The explicit declaration is therefore load-bearing; "
                f"consider adding an entry-xheight form on {lead} or "
                f"documenting why this ligature is special.",
                LigatureEntryInheritanceWarning,
                stacklevel=2,
            )
            continue

        inherited_entries, source_glyph = inheritable
        entry_y = inherited_entries[0][1]
        if not _bitmaps_align_at_y(source_glyph, glyph, entry_y):
            warnings.warn(
                f"{name}: declares entry {_format_anchors(glyph.entry)}; "
                f"lead {lead} has an inheritable entry "
                f"{_format_anchors(inherited_entries)} from {source_glyph.name}, "
                f"but the ligature's bitmap at y={entry_y} doesn't align with "
                f"the lead's. The explicit declaration is therefore "
                f"load-bearing; review whether the bitmap or the entry is "
                f"correct.",
                LigatureEntryInheritanceWarning,
                stacklevel=2,
            )
            continue

        if tuple(glyph.entry) == tuple(inherited_entries):
            warnings.warn(
                f"{name}: declares entry {_format_anchors(glyph.entry)}; "
                f"would inherit {_format_anchors(inherited_entries)} from "
                f"{source_glyph.name}. Consider removing the explicit "
                f"declaration.",
                LigatureEntryInheritanceWarning,
                stacklevel=2,
            )
        else:
            warnings.warn(
                f"{name}: declares entry {_format_anchors(glyph.entry)}; "
                f"would inherit {_format_anchors(inherited_entries)} from "
                f"{source_glyph.name} (differs!). Either fix the YAML or "
                f"document why the override is intentional.",
                LigatureEntryInheritanceWarning,
                stacklevel=2,
            )
    return updated


def _propagate_noentry_after_to_not_before(
    join_glyphs: dict[str, JoinGlyph],
) -> dict[str, JoinGlyph]:
    """Mirror `derive.noentry_after` into `not_before` on the left families.

    For every glyph ``V_R`` carrying ``noentry_after: [F_1, …]`` and at
    least one entry-side anchor at Y, append ``V_R``'s family name to
    ``not_before`` on every variant of every named family ``F_i`` whose
    exit Y matches — making the joining-shape selection on the left side
    impossible whenever ``V_R``'s `.noentry` substitution would void the
    join. This subsumes the manual ``not_before`` edits that previously
    had to accompany each `noentry_after` directive.
    """
    base_to_variants: dict[str, set[str]] = {}
    for name, glyph in join_glyphs.items():
        base_to_variants.setdefault(glyph.base_name, set()).add(name)

    additions: dict[str, set[str]] = {}
    for r_name, r_meta in join_glyphs.items():
        if r_meta.generated_from is not None or r_meta.is_noentry:
            continue
        if not r_meta.noentry_after:
            continue
        r_family = r_meta.base_name
        r_entry_ys = {
            anchor[1] for anchor in (*r_meta.entry, *r_meta.entry_curs_only)
        }
        if not r_entry_ys:
            continue
        for f_family in r_meta.noentry_after:
            for l_name in base_to_variants.get(f_family, frozenset()):
                l_meta = join_glyphs.get(l_name)
                if l_meta is None:
                    continue
                if l_meta.generated_from is not None or l_meta.is_noentry:
                    continue
                if r_family in l_meta.not_before:
                    continue
                if l_meta.before and r_family not in l_meta.before:
                    continue
                exit_ys = {anchor[1] for anchor in l_meta.exit}
                if not (exit_ys & r_entry_ys):
                    continue
                additions.setdefault(l_name, set()).add(r_family)

    if not additions:
        return join_glyphs

    updated = dict(join_glyphs)
    for l_name, families_to_add in additions.items():
        l_meta = updated[l_name]
        merged = tuple(sorted(set(l_meta.not_before) | families_to_add))
        updated[l_name] = replace(l_meta, not_before=merged)
    return updated


__all__ = [
    "ExtensionSpec",
    "GlyphData",
    "GlyphDef",
    "JoinGlyph",
    "JoinTransform",
    "LigatureEntryInheritanceWarning",
    "build_join_glyphs",
    "compile_glyph_families",
    "compile_quikscript_ir",
    "expand_join_transforms",
    "expand_selectors_for_ligatures",
    "flatten_join_glyphs",
    "generate_noentry_variants",
    "get_base_glyph_name",
    "resolve_known_glyph_names",
]
