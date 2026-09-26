from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
import re
import warnings
from typing import Any, NotRequired, TypedDict, cast


class LigatureEntryInheritanceWarning(UserWarning):
    """Warning that a ligature stance declares its own `entry` anchor. `_inherit_ligature_entries_from_lead` issues it, and the message says whether inheritance from the lead would give the same anchor, a different one, or none, so redundant declarations can be found and removed."""


Anchor = tuple[int, int]
BitmapRow = str | tuple[int, ...]
GlyphDef = dict[str, Any]


_EXTENSION_SUFFIX = {
    1: "ext-1",
    2: "ext-2",
    3: "ext-3",
    4: "ext-4",
    5: "ext-5",
    6: "ext-6",
}
_CONTRACTION_SUFFIX = {
    1: "con-1",
    2: "con-2",
    3: "con-3",
    4: "con-4",
    5: "con-5",
    6: "con-6",
}

# The `canonical_ys` that `expand_selectors_for_ligatures` stores for a ligature-glyph endpoint. `_additions` subtracts `canonical_ys` from the anchor Ys the source shares with the ligature, so an empty set adds the ligature whenever it has an anchor Y the source can meet. Lead- and trailing-component endpoints store that component's own Ys instead, so they are added only for a Y the component does not already reach.
_LIG_ENDPOINT_BYPASS: frozenset[int] = frozenset()


@dataclass(frozen=True)
class ExtensionSpec:
    """One `by` / `targets` rule of an `extend_*` or `contract_*` directive. `extend_exit_before` and `extend_entry_after` hold a tuple of these, so one stance can extend by different amounts toward different neighbors. Each `contract_*` directive holds at most one."""

    by: int
    targets: tuple[str, ...]


class GlyphData(TypedDict):
    metadata: dict[str, Any]
    glyphs: dict[str, GlyphDef | None]
    glyph_families: dict[str, Any]
    context_sets: dict[str, list[Any]]
    kerning: dict[str, Any]
    senior_kerning: NotRequired[list[dict[str, Any]]]
    restore_isolated_form_overrides: NotRequired[list[dict[str, str]]]
    predecessor_demote_overrides: NotRequired[list[dict[str, str]]]
    trailing_demote_overrides: NotRequired[list[dict[str, str]]]


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
    extend_entry_after: tuple[ExtensionSpec, ...]
    extend_exit_before: tuple[ExtensionSpec, ...]
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
    entry_explicitly_none: bool = False
    not_before_from_noentry_after: tuple[str, ...] = ()
    strip_entry_before: bool = False
    # Marks the stance that takes the no-follower (word-final) input among a base's competing reverse-upgrade or pair-override stances. The FEA emitter puts it first among those siblings, so renaming a sibling cannot hand word-final to whichever compiled name sorts first. See `qsOut.exit_xheight_after_see_before_other`.
    terminal_default: bool = False
    # `(lead glyph, trailing families)` pairs. `expand_selectors_for_ligatures` adds a lead glyph to `before` when the source's selector names a later component of a ligature that glyph leads, and records that component's family here. The FEA emitter gives each lead its own two-position rule that requires a variant of one of those families next, so `qsIt' qsDay` does not fire when the `qsDay` will not become `qsDay_qsUtter`.
    before_lig_lead_followups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # N pixels to extend the exit of a backward-entry-upgrade target stance, only after the predecessor that gave it its entry join. The FEA emitter's `calt_when_entered_*` lookups match this stance and its entry-extension siblings, which appear only after that join, and swap in the `ex-ext-N` variant when the follower has an entry anchor at this stance's exit Y. Unlike `extend_exit_before`, it never matches the bare stance, so the extension cannot reach a word-initial glyph (see `qsMay.entry_baseline`).
    extend_exit_when_entered: int | None = None

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
_ENTRY_EXIT_MODIFIER_RE = re.compile(r"^(?:en|ex)-[a-z0-9]+(?:-[a-z0-9]+)*(?:-at-[a-z0-9]+)?$")
_BEFORE_AFTER_MODIFIER_RE = re.compile(r"^(?:before|after)-[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXTENDED_HEIGHT_LABELS = {0: "y0", 5: "y5", 6: "y6", 8: "y8"}
_KNOWN_DERIVE_DIRECTIVES = frozenset(
    {
        "extend_entry_after",
        "extend_exit_before",
        "extend_exit_before_gated",
        "extend_exit_when_entered",
        "contract_entry_after",
        "contract_exit_before",
        "noentry_after",
        "reverse_upgrade_from",
        "preferred_over",
    }
)


_ANCHOR_SENTINEL_PREFIX = "<<anchor "


@dataclass(frozen=True)
class _AnchorSelector:
    kind: str
    y: int
    excluded_families: tuple[str, ...] = ()
    family_scope: str | None = None


def _make_anchor_sentinel(
    kind: str,
    y: int,
    excluded_families: tuple[str, ...] = (),
    *,
    family_scope: str | None = None,
) -> str:
    suffix_parts = []
    if family_scope:
        suffix_parts.append(f"family={family_scope}")
    if excluded_families:
        suffix_parts.append("except=" + ",".join(excluded_families))
    suffix = "|" + "|".join(suffix_parts) if suffix_parts else ""
    return f"{_ANCHOR_SENTINEL_PREFIX}{kind}={y}{suffix}>>"


def _parse_anchor_sentinel(
    value: str,
) -> _AnchorSelector | None:
    if not value.startswith(_ANCHOR_SENTINEL_PREFIX) or not value.endswith(">>"):
        return None
    body = value[len(_ANCHOR_SENTINEL_PREFIX) : -2]
    parts = body.split("|")
    head = parts[0]
    kind, _, y_str = head.partition("=")
    if kind not in {"exit_y", "entry_y"} or not y_str:
        return None
    try:
        y = int(y_str)
    except ValueError:
        return None
    excluded: tuple[str, ...] = ()
    family_scope: str | None = None
    for part in parts[1:]:
        key, sep, raw_value = part.partition("=")
        if sep != "=" or not raw_value:
            return None
        if key == "except":
            excluded = tuple(raw_value.split(","))
        elif key == "family":
            family_scope = raw_value
        else:
            return None
    return _AnchorSelector(kind, y, excluded, family_scope)


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
        raise ValueError(f"Glyph family {family_name!r} family-level derive must be a mapping")
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
                    if key == "anchors":
                        # Keep an explicit None so later code can tell "declares no anchor" from "says nothing about it" (`entry_explicitly_none`).
                        merged[key][nested_key] = None
                    else:
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
        raise ValueError(f"Cyclic stance inheritance in {family_name}: {cycle}")

    records = {}
    if family_def.get("mono"):
        records["mono"] = family_def["mono"]
    if family_def.get("prop"):
        records["prop"] = family_def["prop"]
    records.update(family_def.get("stances", {}))

    raw = records.get(record_name)
    if raw is None:
        raise ValueError(f"Unknown stance '{record_name}' in glyph family '{family_name}'")

    raw_derive = raw.get("derive") if isinstance(raw, dict) else None
    explicit_null_derive_keys: set[str] = (
        {k for k, v in raw_derive.items() if v is None} if isinstance(raw_derive, dict) else set()
    )

    stack.append(record_name)
    resolved: dict[str, Any] = {}

    inherits = raw.get("inherits")
    if inherits:
        parents = [inherits] if isinstance(inherits, str) else inherits
        for parent_name in parents:
            parent = _resolve_family_record(
                family_name,
                family_def,
                parent_name,
                cache,
                stack,
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
            stance_anchors=resolved.get("anchors", {}) or {},
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


def _stance_anchor_ys(stance_anchors: dict[str, Any]) -> tuple[set[int], set[int]]:
    entry_ys = set(_anchor_ys(stance_anchors.get("entry")))
    entry_ys.update(_anchor_ys(stance_anchors.get("entry_curs_only")))
    exit_ys = set(_anchor_ys(stance_anchors.get("exit")))
    return entry_ys, exit_ys


def _collect_family_anchor_ys(
    family_def: dict[str, Any],
) -> tuple[set[int], set[int]]:
    records: dict[str, Any] = {}
    if family_def.get("mono"):
        records["mono"] = family_def["mono"]
    if family_def.get("prop"):
        records["prop"] = family_def["prop"]
    records.update(family_def.get("stances", {}) or {})

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
    stance_ys: set[int],
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
        if stance_ys & target_ys:
            kept.append(target)
    return kept


def _filter_extension_rules_by_reachability(
    value: Any,
    *,
    stance_ys: set[int],
    glyph_families: dict[str, Any],
    target_anchor: str,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    raw_rules = value if isinstance(value, list) else [value]
    kept_rules: list[dict[str, Any]] = []
    for rule in raw_rules:
        kept_targets = _filter_targets_by_reachability(
            rule.get("targets", ()),
            stance_ys=stance_ys,
            glyph_families=glyph_families,
            target_anchor=target_anchor,
        )
        if kept_targets:
            kept_rules.append({**deepcopy(rule), "targets": kept_targets})
    if not kept_rules:
        return None
    if isinstance(value, list):
        return kept_rules
    return kept_rules[0]


def _select_applicable_family_derive(
    family_derive: dict[str, Any],
    *,
    stance_anchors: dict[str, Any],
    explicit_null_keys: set[str],
    glyph_families: dict[str, Any] | None,
) -> dict[str, Any]:
    stance_entry_ys, stance_exit_ys = _stance_anchor_ys(stance_anchors)

    applicable: dict[str, Any] = {}
    for key, value in family_derive.items():
        if key in explicit_null_keys or value is None:
            continue

        if glyph_families is None:
            applicable[key] = deepcopy(value)
            continue

        if key == "extend_exit_before":
            if not stance_exit_ys:
                continue
            kept_rules = _filter_extension_rules_by_reachability(
                value,
                stance_ys=stance_exit_ys,
                glyph_families=glyph_families,
                target_anchor="entry",
            )
            if kept_rules is not None:
                applicable[key] = kept_rules
        elif key == "extend_entry_after":
            if not stance_entry_ys:
                continue
            kept_rules = _filter_extension_rules_by_reachability(
                value,
                stance_ys=stance_entry_ys,
                glyph_families=glyph_families,
                target_anchor="exit",
            )
            if kept_rules is not None:
                applicable[key] = kept_rules
        elif key == "contract_exit_before":
            if not stance_exit_ys:
                continue
            kept_targets = _filter_targets_by_reachability(
                value.get("targets", ()),
                stance_ys=stance_exit_ys,
                glyph_families=glyph_families,
                target_anchor="entry",
            )
            if not kept_targets:
                continue
            applicable[key] = {**deepcopy(value), "targets": kept_targets}
        elif key == "contract_entry_after":
            if not stance_entry_ys:
                continue
            kept_targets = _filter_targets_by_reachability(
                value.get("targets", ()),
                stance_ys=stance_entry_ys,
                glyph_families=glyph_families,
                target_anchor="exit",
            )
            if not kept_targets:
                continue
            applicable[key] = {**deepcopy(value), "targets": kept_targets}
        elif key == "extend_exit_before_gated":
            if not stance_exit_ys or not isinstance(value, dict):
                continue
            kept_gated: dict[str, list[Any]] = {}
            for tag, refs in value.items():
                kept_refs = _filter_targets_by_reachability(
                    refs or (),
                    stance_ys=stance_exit_ys,
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


def _is_contextual_family_stance(stance_def: dict[str, Any], *, is_base_record: bool = False) -> bool:
    contextual = stance_def.get("contextual")
    if contextual is not None:
        return bool(contextual)
    if is_base_record:
        return False
    if stance_def.get("traits"):
        return True
    if stance_def.get("select") or stance_def.get("derive"):
        return True
    anchors = stance_def.get("anchors", {})
    if any(key in anchors for key in ("entry", "entry_curs_only", "exit")):
        return True
    return any(key in stance_def for key in ("shape", "bitmap"))


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
    if modifier in {
        "extended",
        "widebase",
        "reaches-way-back",
        "smaller-loop",
        "noentry",
        "nonjoining-left",
        "noexit",
        "gapped",
    }:
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


# HarfBuzz reads CFF1 PostScript names truncated to 63 bytes, so two stances whose names share a 63-byte prefix collide at shaping time. The cap applies only to a stance's base compiled name. Generated variants (`.noentry`, `.en-ext-1`, …) can still exceed it, but they are usually not the glyphs a plain-text shape produces. Lower the cap if a real collision appears.
_SYNTHESIZED_NAME_LENGTH_CAP = 63


def _synthesize_anchor_modifiers(
    anchors: dict[str, Any] | None,
    authored: Sequence[str],
) -> tuple[str, ...]:
    """Return the stance's modifiers with `en-<label>` / `ex-<label>` added for each anchor whose Y has a label in `_EXTENDED_HEIGHT_LABELS`.

    Trait-only stances (`qsNo.alt`, `qsTea.half`) get these modifiers too, so every compiled name shows where the stance joins. Hand-written names that lack them are resolved by `_heal_renamed_selector` and `heal_glyph_name`.

    An authored `en-…-at-<Y>` (or `ex-…-at-<Y>`) suppresses the label on that side, since it already encodes the Y. The result is ordered `[en-<label>, ex-<label>, *other authored modifiers]`, with authored copies of the added labels removed. When the authored list already contains every label, it is returned unchanged.
    """
    synthesized: list[str] = []
    if anchors:
        for side, short in (("entry", "en"), ("exit", "ex")):
            anchor = anchors.get(side)
            if not isinstance(anchor, (list, tuple)) or len(anchor) < 2:
                continue
            y = anchor[1]
            label = _EXTENDED_HEIGHT_LABELS.get(y)
            if label is None:
                continue
            qualifier_suffix = f"-at-{y}"
            if any(
                isinstance(modifier, str)
                and modifier.startswith(f"{short}-")
                and modifier.endswith(qualifier_suffix)
                for modifier in authored
            ):
                continue
            synthesized.append(f"{short}-{label}")

    if not synthesized:
        return tuple(authored)
    synth_set = set(synthesized)
    authored_set = set(authored)
    if synth_set <= authored_set:
        return tuple(authored)
    return (*synthesized, *(m for m in authored if m not in synth_set))


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

    suffix = normalized[len(family_name) :].removeprefix(".")
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
    available_names: frozenset[str] | None = None,
) -> str:
    context = f"{context_label} {field_name}"
    if isinstance(value, str):
        resolved_family = _split_family_compiled_name(value, family_names)
        if resolved_family is not None:
            family_name, traits, modifiers = resolved_family
            return _heal_renamed_selector(
                _compiled_family_glyph_name(family_name, traits, modifiers),
                family_name,
                traits,
                modifiers,
                family_names=family_names,
                available_names=available_names,
            )
        return get_base_glyph_name(value)

    if not isinstance(value, dict):
        raise ValueError(f"{context_family} {context} must contain strings or selector mappings")

    unknown_keys = set(value) - {"family", "traits", "modifiers", "why_not_narrower"}
    if unknown_keys:
        keys = ", ".join(sorted(unknown_keys))
        raise ValueError(f"{context_family} {context} uses unsupported selector keys: {keys}")

    if "why_not_narrower" in value:
        why = value["why_not_narrower"]
        if not isinstance(why, str) or not why.strip():
            raise ValueError(f"{context_family} {context} why_not_narrower must be a non-empty string")

    target_family = value.get("family")
    if not isinstance(target_family, str) or not target_family:
        raise ValueError(f"{context_family} {context} selector must include a family name")
    if target_family not in family_names:
        raise ValueError(f"{context_family} {context} refers to unknown glyph family {target_family!r}")

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
    return _heal_renamed_selector(
        _compiled_family_glyph_name(target_family, traits, modifiers),
        target_family,
        traits,
        modifiers,
        family_names=family_names,
        available_names=available_names,
    )


_RUNTIME_EXTENSION_MODIFIER_RE = re.compile(r"^(?:en|ex)-(?:ext|con)-\d+$|^(?:en|ex)-trim-\d+$")
_SYNTHESIZED_MODIFIER_TOKENS = frozenset(
    f"{side}-{label}" for side in ("en", "ex") for label in _EXTENDED_HEIGHT_LABELS.values()
)


def _split_selector_extensions(
    modifiers: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a selector's modifiers into `(base_modifiers, extension_modifiers)`.

    Extension modifiers (`ex-ext-1`, `en-con-2`, `en-trim-2`) name variants that `expand_join_transforms` generates after selectors are resolved, so they are not yet in `available_names`. `_heal_renamed_selector` matches the base part and then appends the extensions to the healed name.
    """
    base: list[str] = []
    extensions: list[str] = []
    for modifier in modifiers:
        if isinstance(modifier, str) and _RUNTIME_EXTENSION_MODIFIER_RE.fullmatch(modifier):
            extensions.append(modifier)
        else:
            base.append(modifier)
    return tuple(base), tuple(extensions)


def _heal_renamed_selector(
    candidate: str,
    target_family: str,
    traits: Sequence[str],
    modifiers: Sequence[str],
    *,
    family_names: set[str],
    available_names: frozenset[str] | None,
) -> str:
    """Return the compiled stance name a selector refers to.

    `candidate` is the name built from the selector. It is returned as is when it is in `available_names`. Otherwise it usually lacks anchor-Y modifiers that `_synthesize_anchor_modifiers` added to the stance's name, and this function finds that stance.

    For example, `{family: qsOut, modifiers: [ex-y5, ex-ext-1]}` builds `qsOut.ex-y5.ex-ext-1`, but the stance compiles as `qsOut.en-y0.ex-y5`. A match must have the selector's traits exactly and every selector modifier, and its extra modifiers may only be anchor-Y tokens (`_SYNTHESIZED_MODIFIER_TOKENS`).

    1. The first pass matches the full modifier set. This finds authored stances whose YAML lists an extension modifier (`modifiers: [ex-ext-1]` with an exit at y=0 compiles as `qsX.ex-y0.ex-ext-1`).
    2. Otherwise the extension modifiers (`_split_selector_extensions`) are set aside, the base modifiers are matched against stances without extension modifiers, and the extensions are appended to the result in their original order.

    Each pass picks the match with the fewest extra modifiers and raises `ValueError` on a tie. When nothing matches, `candidate` is returned unchanged.
    """
    if available_names is None or candidate in available_names:
        return candidate

    selector_traits = frozenset(traits)
    selector_modifiers_set = frozenset(modifiers)
    prefix = target_family + "."

    full_matches: list[tuple[int, str]] = []
    for name in available_names:
        if name != target_family and not name.startswith(prefix):
            continue
        parsed = _split_family_compiled_name(name, family_names)
        if parsed is None:
            continue
        fam, name_traits, name_modifiers = parsed
        if fam != target_family:
            continue
        name_traits_set = frozenset(name_traits)
        if selector_traits != name_traits_set:
            continue
        name_modifiers_set = frozenset(name_modifiers)
        if not (selector_modifiers_set <= name_modifiers_set):
            continue
        extra_modifiers = name_modifiers_set - selector_modifiers_set
        if not extra_modifiers <= _SYNTHESIZED_MODIFIER_TOKENS:
            continue
        full_matches.append((len(extra_modifiers), name))

    if full_matches:
        full_matches.sort(key=lambda entry: (entry[0], entry[1]))
        closest = full_matches[0][0]
        tied = [name for extra_count, name in full_matches if extra_count == closest]
        if len(tied) > 1:
            raise ValueError(
                f"Selector for {target_family} with traits {list(traits)!r} modifiers {list(modifiers)!r} "
                f"matches multiple post-synthesis stances equally ({sorted(tied)}); add a disambiguator."
            )
        return tied[0]

    base_modifiers, extension_modifiers = _split_selector_extensions(modifiers)
    base_candidate = _compiled_family_glyph_name(target_family, traits, base_modifiers)
    if base_candidate in available_names:
        if not extension_modifiers:
            return candidate
        return _compiled_family_glyph_name(target_family, traits, (*base_modifiers, *extension_modifiers))

    selector_base = frozenset(base_modifiers)
    matches: list[tuple[int, str, tuple[str, ...], tuple[str, ...]]] = []
    for name in available_names:
        if name != target_family and not name.startswith(prefix):
            continue
        parsed = _split_family_compiled_name(name, family_names)
        if parsed is None:
            continue
        fam, name_traits, name_modifiers = parsed
        if fam != target_family:
            continue
        name_base_modifiers, name_extensions = _split_selector_extensions(name_modifiers)
        if name_extensions:
            continue
        name_traits_set = frozenset(name_traits)
        name_base_set = frozenset(name_base_modifiers)
        # A selector without traits names the plain stance, so `qsTea.en-y5` must not match `qsTea.half.en-y5`.
        if selector_traits != name_traits_set:
            continue
        if not (selector_base <= name_base_set):
            continue
        # Only anchor-Y tokens may differ, so a selector cannot pick up a distinct sibling such as an `ex-noentry` stance.
        extra_modifiers = name_base_set - selector_base
        if not extra_modifiers <= _SYNTHESIZED_MODIFIER_TOKENS:
            continue
        extra = len(name_base_set) - len(selector_base)
        matches.append((extra, name, name_traits, name_base_modifiers))

    if not matches:
        return candidate

    matches.sort(key=lambda entry: entry[0])
    closest = matches[0][0]
    tied = [entry for entry in matches if entry[0] == closest]
    if len(tied) > 1:
        tied_names = [entry[1] for entry in tied]
        raise ValueError(
            f"Selector for {target_family} with traits {list(traits)!r} modifiers {list(modifiers)!r} "
            f"matches multiple post-synthesis stances equally ({tied_names}); add a disambiguator."
        )

    _, _, healed_traits, healed_base = tied[0]
    return _compiled_family_glyph_name(target_family, healed_traits, (*healed_base, *extension_modifiers))


def family_names_from_compiled(compiled_names: set[str] | frozenset[str]) -> set[str]:
    return {name.split(".", 1)[0] for name in compiled_names}


def heal_glyph_name(
    name: str,
    family_names: set[str],
    available_names: frozenset[str] | set[str],
) -> str:
    """Return the compiled name for a hand-written glyph name that may lack the anchor-Y modifiers `_synthesize_anchor_modifiers` adds.

    It serves names written as plain strings instead of selectors, such as the entries in `predecessor_demote_overrides`. A name that exists or names no known family is returned unchanged; any other name is resolved by `_heal_renamed_selector`.
    """
    if not isinstance(available_names, frozenset):
        available_names = frozenset(available_names)
    if name in available_names:
        return name
    parsed = _split_family_compiled_name(name, family_names)
    if parsed is None:
        return name
    family_name, traits, modifiers = parsed
    if family_name not in family_names:
        return name
    return _heal_renamed_selector(
        name,
        family_name,
        traits,
        modifiers,
        family_names=family_names,
        available_names=available_names,
    )


def _normalize_family_refs(
    values,
    family_names: set[str],
    *,
    context_sets: dict[str, list],
    context_family: str,
    context_label: str,
    field_name: str,
    available_names: frozenset[str] | None = None,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{context_family} {context_label} {field_name} must be a list")

    def _expand_value(value, stack: tuple[str, ...]) -> list[str]:
        if isinstance(value, dict) and ("exit_y" in value or "entry_y" in value):
            keys = set(value)
            anchor_keys = keys & {"exit_y", "entry_y"}
            kind = next(iter(anchor_keys))
            if len(anchor_keys) != 1:
                raise ValueError(
                    f"{context_family} {context_label} {field_name} anchor selector "
                    "must have exactly one of exit_y/entry_y"
                )
            y = value[kind]
            if not isinstance(y, int) or isinstance(y, bool):
                raise ValueError(f"{context_family} {context_label} {field_name} {kind} must be an integer")

            if "family" in value:
                allowed = anchor_keys | {"family", "traits", "modifiers"}
                if not keys <= allowed:
                    extra = ", ".join(sorted(keys - allowed))
                    raise ValueError(
                        f"{context_family} {context_label} {field_name} "
                        f"family-scoped anchor selector cannot include extra keys: {extra}"
                    )
                target_family = value.get("family")
                if not isinstance(target_family, str) or not target_family:
                    raise ValueError(
                        f"{context_family} {context_label} {field_name} "
                        "family-scoped anchor selector must include a family name"
                    )
                if target_family not in family_names:
                    raise ValueError(
                        f"{context_family} {context_label} {field_name} "
                        f"family-scoped anchor selector refers to unknown family {target_family!r}"
                    )
                traits = _normalize_source_traits(
                    value.get("traits", ()),
                    family_name=context_family,
                    context=f"{context_label} {field_name}",
                )
                modifiers = _normalize_source_modifiers(
                    value.get("modifiers", ()),
                    family_name=context_family,
                    context=f"{context_label} {field_name}",
                )
                family_scope = _compiled_family_glyph_name(target_family, traits, modifiers)
                return [_make_anchor_sentinel(kind, y, family_scope=family_scope)]

            allowed = anchor_keys | {"except"}
            if not keys <= allowed:
                raise ValueError(
                    f"{context_family} {context_label} {field_name} anchor selector "
                    "must have exactly one of exit_y/entry_y and an optional 'except' list"
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
                    f"{context_family} {context_label} {field_name} uses an invalid " "context_set name"
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
                available_names=available_names,
            )
        ]

    expanded = []
    for value in values:
        expanded.extend(_expand_value(value, ()))
    return expanded


def _check_select_family_overlap(
    select: dict[str, Any],
    *,
    family_name: str,
    stance_name: str | None,
) -> None:
    def _literal_families(values) -> set[str]:
        if not isinstance(values, list):
            return set()
        families: set[str] = set()
        for value in values:
            if isinstance(value, dict) and set(value) == {"family"}:
                fam = value["family"]
                if isinstance(fam, str):
                    families.add(fam)
            elif isinstance(value, dict) and "family" in value and ({"entry_y", "exit_y"} & set(value)):
                fam = value["family"]
                if isinstance(fam, str):
                    families.add(fam)
        return families

    context = f"stance {stance_name!r}" if stance_name else "base record"
    for positive_key, negative_key in (("before", "not_before"), ("after", "not_after")):
        positive = _literal_families(select.get(positive_key))
        negative = _literal_families(select.get(negative_key))
        overlap = positive & negative
        if overlap:
            family_list = ", ".join(sorted(overlap))
            raise ValueError(
                f"{family_name} {context}: select.{positive_key} and "
                f"select.{negative_key} both list family {family_list}; "
                f"a single stance cannot list the same family in both a "
                f"positive and a negative selector"
            )


def _family_stance_to_glyph_def(
    family_name: str,
    family_def: dict[str, Any],
    stance_def: dict[str, Any],
    *,
    stance_name: str | None = None,
    contextual: bool,
    family_names: set[str],
    context_sets: dict[str, list[Any]],
    available_names: frozenset[str] | None = None,
) -> GlyphDef:
    glyph_def: GlyphDef = {}

    if "bitmap" in stance_def:
        glyph_def["bitmap"] = deepcopy(stance_def["bitmap"])

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
        if key in stance_def:
            glyph_def[key] = deepcopy(stance_def[key])

    anchors = stance_def.get("anchors", {})
    if "entry" in anchors:
        glyph_def["cursive_entry"] = deepcopy(anchors["entry"])
    if "entry_curs_only" in anchors:
        glyph_def["cursive_entry_curs_only"] = deepcopy(anchors["entry_curs_only"])
    if "exit" in anchors:
        glyph_def["cursive_exit"] = deepcopy(anchors["exit"])
    if "exit_ink_y" in anchors:
        glyph_def["cursive_exit_ink_y"] = deepcopy(anchors["exit_ink_y"])

    select = stance_def.get("select", {})
    _check_select_family_overlap(select, family_name=family_name, stance_name=stance_name)
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
                context_label=f"stance {stance_name!r}" if stance_name else "base record",
                field_name=source_key,
                available_names=available_names,
            )

    # A `context_set` in `after` can expand to a name that `not_after` also lists (likewise `before` / `not_before`). Remove those names from the positive list so the emitters can use it as the trigger list. `_check_select_family_overlap` already rejects a family named literally in both.
    for positive_key, negative_key in (
        ("calt_after", "calt_not_after"),
        ("calt_before", "calt_not_before"),
    ):
        positive = glyph_def.get(positive_key)
        negative = glyph_def.get(negative_key)
        if positive and negative:
            excluded = set(negative)
            filtered = tuple(family for family in positive if family not in excluded)
            if filtered != tuple(positive):
                glyph_def[positive_key] = filtered

    derive = stance_def.get("derive", {})
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
                context_label=f"stance {stance_name!r}" if stance_name else "base record",
                field_name=source_key,
                available_names=available_names,
            )

    for key in ("extend_entry_after", "extend_exit_before"):
        if key not in derive:
            continue
        raw = derive[key]
        if raw is None:
            glyph_def[key] = ()
            continue
        raw_rules = raw if isinstance(raw, list) else [raw]
        rules: list[ExtensionSpec] = []
        for rule in raw_rules:
            targets = _normalize_family_refs(
                rule["targets"],
                family_names,
                context_sets=context_sets,
                context_family=family_name,
                context_label=f"stance {stance_name!r}" if stance_name else "base record",
                field_name=key,
                available_names=available_names,
            )
            rules.append(ExtensionSpec(by=rule["by"], targets=tuple(targets)))
        glyph_def[key] = tuple(rules)

    for key in ("contract_entry_after", "contract_exit_before"):
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
                context_label=f"stance {stance_name!r}" if stance_name else "base record",
                field_name=key,
                available_names=available_names,
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
                    context_label=f"stance {stance_name!r}" if stance_name else "base record",
                    field_name="extend_exit_before_gated",
                    available_names=available_names,
                )
            )
        glyph_def["extend_exit_before_gated"] = tuple(sorted(resolved_gated.items()))

    when_entered = derive.get("extend_exit_when_entered")
    if when_entered is not None:
        glyph_def["extend_exit_when_entered"] = int(when_entered["by"])

    revert_feature = stance_def.get("revert_feature")
    if revert_feature is not None:
        glyph_def["revert_feature"] = revert_feature

    gate_feature = stance_def.get("gate_feature_behind")
    if gate_feature is not None:
        glyph_def["gate_feature"] = gate_feature

    replaces_family_feature = stance_def.get("replaces_family_feature")
    if replaces_family_feature is not None:
        glyph_def["replaces_family_feature"] = replaces_family_feature

    if stance_def.get("strip_entry_before"):
        glyph_def["strip_entry_before"] = True

    if stance_def.get("terminal_default"):
        glyph_def["terminal_default"] = True

    return glyph_def


def _iter_compiled_family_stances(
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
                "stance_def": _resolve_family_record(
                    family_name,
                    family_def,
                    base_record_name,
                    cache,
                    [],
                    glyph_families=glyph_families,
                ),
                "stance_name": None,
                "output_name": family_name,
                "contextual": False,
                "traits": (),
                "modifiers": (),
                "authored_modifiers": (),
            }

        for stance_name in family_def.get("stances", {}):
            resolved = _resolve_family_record(
                family_name,
                family_def,
                stance_name,
                cache,
                [],
                glyph_families=glyph_families,
            )
            traits = _normalize_source_traits(
                resolved.get("traits", ()),
                family_name=family_name,
                context=f"stance {stance_name!r}",
            )
            authored_modifiers = _normalize_source_modifiers(
                resolved.get("modifiers", ()),
                family_name=family_name,
                context=f"stance {stance_name!r}",
            )
            modifiers = _synthesize_anchor_modifiers(resolved.get("anchors", {}), authored_modifiers)
            output_name = _compiled_family_glyph_name(family_name, traits, modifiers)
            if len(output_name) > _SYNTHESIZED_NAME_LENGTH_CAP and modifiers != authored_modifiers:
                # The added anchor-Y modifiers would push the name past `_SYNTHESIZED_NAME_LENGTH_CAP`, so keep the authored name.
                modifiers = authored_modifiers
                output_name = _compiled_family_glyph_name(family_name, traits, modifiers)
            if output_name == family_name:
                raise ValueError(
                    f"Glyph family '{family_name}' stance '{stance_name}' must declare traits or modifiers"
                )
            variants = set(resolved.get("variants", ()))
            if variant == "mono":
                if "mono" not in variants:
                    continue
            elif variants:
                if variant not in variants:
                    continue
            else:
                contextual = _is_contextual_family_stance(resolved)
                if not is_senior and contextual:
                    continue
            yield {
                "family_name": family_name,
                "family_def": family_def,
                "stance_def": resolved,
                "stance_name": stance_name,
                "output_name": output_name,
                "contextual": _is_contextual_family_stance(resolved),
                "traits": traits,
                "modifiers": modifiers,
                "authored_modifiers": authored_modifiers,
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

    # Collect every compiled name before resolving selectors, so `_heal_renamed_selector` can map a name without anchor-Y modifiers (`qsOut.ex-y5`) to its compiled stance (`qsOut.en-y0.ex-y5`).
    records = list(_iter_compiled_family_stances(glyph_families, variant, context_sets=context_sets))
    available_names = frozenset(record["output_name"] for record in records)

    for record in records:
        output_name = record["output_name"]
        if output_name in compiled:
            raise ValueError(f"Duplicate compiled glyph name {output_name!r}")
        compiled[output_name] = _family_stance_to_glyph_def(
            record["family_name"],
            record["family_def"],
            record["stance_def"],
            stance_name=record["stance_name"],
            contextual=record["contextual"],
            family_names=family_names,
            context_sets=context_sets,
            available_names=available_names,
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
    return any(part.startswith("en-") or part.startswith("ex-") or part == "half" for part in parts)


def _glyph_name_modifiers(glyph_name: str) -> list[str]:
    return glyph_name.split(".")[1:]


def _compat_assertions_from_modifiers(
    modifiers: list[str],
    traits: frozenset[str],
) -> frozenset[str]:
    compat = set(modifiers) | set(traits)
    for modifier in modifiers:
        if modifier.startswith("en-"):
            compat.update({"entry", modifier.removeprefix("en-")})
        elif modifier.startswith("ex-"):
            compat.update({"exit", modifier.removeprefix("ex-")})
        for side, short in (("entry", "en"), ("exit", "ex")):
            for suffix in _EXTENSION_SUFFIX.values():
                prefix = f"{short}-{suffix}"
                if modifier.startswith(prefix):
                    compat.update({side, "extended", suffix, prefix})
                    break
            for suffix in _CONTRACTION_SUFFIX.values():
                prefix = f"{short}-{suffix}"
                if modifier.startswith(prefix):
                    compat.update({side, "contracted", suffix, prefix})
                    break
        for side, short in (("entry", "en"), ("exit", "ex")):
            prefix = f"{short}-trim"
            if modifier.startswith(prefix):
                compat.update({side, "trimmed", prefix})
                break
    return frozenset(compat)


def _entry_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith("en-"):
            return "." + modifier
    return None


def _exit_suffix_from_modifiers(modifiers: list[str]) -> str | None:
    for modifier in modifiers:
        if modifier.startswith("ex-"):
            return "." + modifier
    return None


_EXTENDED_ENTRY_PREFIXES = tuple(f"en-{s}" for s in _EXTENSION_SUFFIX.values())
_EXTENDED_EXIT_PREFIXES = tuple(f"ex-{s}" for s in _EXTENSION_SUFFIX.values())
_CONTRACTED_ENTRY_PREFIXES = tuple(f"en-{s}" for s in _CONTRACTION_SUFFIX.values())
_CONTRACTED_EXIT_PREFIXES = tuple(f"ex-{s}" for s in _CONTRACTION_SUFFIX.values())


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


_ENTRY_RESTRICTION_AT_RE = re.compile(r"^en-(?:ext|con)-\d+-at-(\d+)$")


def _entry_restriction_y_from_modifiers(modifiers: list[str]) -> int | None:
    for modifier in modifiers:
        match = _ENTRY_RESTRICTION_AT_RE.match(modifier)
        if match is not None:
            return int(match.group(1))
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
    authored_modifiers: Sequence[str] | None = None,
) -> JoinGlyph:
    resolved_traits = frozenset(traits)
    resolved_modifiers = (
        tuple(modifiers) if modifiers is not None else tuple(_glyph_name_modifiers(glyph_name))
    )
    # `is_entry_variant` means the author wrote an `en-*` modifier. The `en-*` tokens `_synthesize_anchor_modifiers` adds must not set it, so classify from `authored_modifiers` when the caller passes them.
    classifier_source = tuple(authored_modifiers) if authored_modifiers is not None else resolved_modifiers
    resolved_contextual = _is_contextual_variant(glyph_name) if contextual is None else bool(contextual)
    resolved_is_noentry = ("noentry" in resolved_modifiers) if is_noentry is None else bool(is_noentry)

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
            (a[0], a[1]) for a in _normalize_anchors(glyph_def.get("cursive_entry_curs_only"))
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
        is_entry_variant=any(modifier.startswith("en-") for modifier in classifier_source),
        entry_suffix=_entry_suffix_from_modifiers(list(resolved_modifiers)),
        exit_suffix=_exit_suffix_from_modifiers(list(resolved_modifiers)),
        extended_entry_suffix=_extended_entry_suffix_from_modifiers(list(resolved_modifiers)),
        extended_exit_suffix=_extended_exit_suffix_from_modifiers(list(resolved_modifiers)),
        entry_restriction_y=_entry_restriction_y_from_modifiers(list(resolved_modifiers)),
        is_noentry=resolved_is_noentry,
        bitmap=_normalize_bitmap(glyph_def.get("bitmap", ())),
        y_offset=int(glyph_def.get("y_offset", 0)),
        advance_width=glyph_def.get("advance_width"),
        extend_entry_after=_normalize_extension_rules(glyph_def.get("extend_entry_after")),
        extend_exit_before=_normalize_extension_rules(glyph_def.get("extend_exit_before")),
        extend_exit_before_gated=tuple(glyph_def.get("extend_exit_before_gated", ())),
        extend_exit_when_entered=glyph_def.get("extend_exit_when_entered"),
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
        entry_explicitly_none=("cursive_entry" in glyph_def and glyph_def["cursive_entry"] is None),
        strip_entry_before=bool(glyph_def.get("strip_entry_before")),
        terminal_default=bool(glyph_def.get("terminal_default")),
    )


def _normalize_extension_rules(raw: object) -> tuple[ExtensionSpec, ...]:
    if raw is None:
        return ()
    if isinstance(raw, ExtensionSpec):
        return (raw,)
    if isinstance(raw, tuple):
        return cast(tuple[ExtensionSpec, ...], raw)
    if isinstance(raw, list):
        return tuple(cast(list[ExtensionSpec], raw))
    raise TypeError(f"Unexpected extension-rules value {raw!r}")


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


def _make_sentinel_expander(
    metadata: dict[str, JoinGlyph],
) -> Callable[[tuple[str, ...]], tuple[str, ...]]:
    exit_classes, entry_classes = _collect_anchor_classes(metadata)
    base_to_variants: dict[str, set[str]] = {}
    for glyph_name, meta in metadata.items():
        base_to_variants.setdefault(meta.base_name, set()).add(glyph_name)

    def _member_matches_scope(member: str, scope: str) -> bool:
        meta = metadata.get(member)
        if "." not in scope:
            return meta is not None and meta.base_name == scope
        return member == scope or member.startswith(scope + ".")

    def _is_unrestricted_entry_upgrade(meta: JoinGlyph, y: int) -> bool:
        if y not in meta.entry_ys:
            return False
        if meta.word_final or meta.is_noentry or meta.reverse_upgrade_from:
            return False
        if meta.after or meta.before:
            return False
        if meta.extended_entry_suffix or meta.extended_exit_suffix:
            return False
        if meta.contracted_entry_suffix or meta.contracted_exit_suffix:
            return False
        if "half" in meta.traits and not meta.exit:
            return False
        return True

    def _is_unrestricted_exit_upgrade(meta: JoinGlyph, y: int) -> bool:
        if y not in meta.exit_ys:
            return False
        if meta.word_final or meta.is_noentry:
            return False
        if meta.after or meta.before or meta.gated_before:
            return False
        if meta.entry or meta.entry_curs_only:
            return False
        if meta.extended_entry_suffix or meta.extended_exit_suffix:
            return False
        if meta.contracted_entry_suffix or meta.contracted_exit_suffix:
            return False
        return True

    def _potential_scoped_anchor_members(parsed: _AnchorSelector) -> tuple[str, ...]:
        scope = parsed.family_scope
        if scope is None or scope not in metadata:
            return ()

        scope_meta = metadata[scope]
        if parsed.kind == "entry_y":
            if parsed.y in scope_meta.entry_ys:
                return ()
            matches = _is_unrestricted_entry_upgrade
        else:
            if parsed.y in scope_meta.exit_ys:
                return ()
            matches = _is_unrestricted_exit_upgrade

        for variant_name in base_to_variants.get(scope_meta.base_name, ()):
            if not _member_matches_scope(variant_name, scope):
                continue
            if matches(metadata[variant_name], parsed.y):
                return (scope,)
        return ()

    def expand(values: tuple[str, ...]) -> tuple[str, ...]:
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
            members = (exit_classes if parsed.kind == "exit_y" else entry_classes).get(parsed.y, ())
            scoped_potentials = _potential_scoped_anchor_members(parsed)
            for member in sorted({*members, *scoped_potentials}):
                if parsed.family_scope and not _member_matches_scope(
                    member,
                    parsed.family_scope,
                ):
                    continue
                if parsed.excluded_families:
                    member_family = metadata[member].family if member in metadata else None
                    if member_family in parsed.excluded_families:
                        continue
                if member not in seen:
                    seen.add(member)
                    expanded.append(member)
        return tuple(expanded)

    return expand


def _expand_anchor_sentinels(metadata: dict[str, JoinGlyph]) -> dict[str, JoinGlyph]:
    expand = _make_sentinel_expander(metadata)

    expanded_metadata: dict[str, JoinGlyph] = {}
    for glyph_name, meta in metadata.items():
        new_after = expand(meta.after)
        new_before = expand(meta.before)
        new_not_after = expand(meta.not_after)
        new_not_before = expand(meta.not_before)
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


_EXTENSION_RULES_FIELDS: tuple[str, ...] = (
    "extend_entry_after",
    "extend_exit_before",
)
_CONTRACTION_SPEC_FIELDS: tuple[str, ...] = (
    "contract_entry_after",
    "contract_exit_before",
)


def _expand_anchor_sentinels_in_extension_targets(
    metadata: dict[str, JoinGlyph],
) -> dict[str, JoinGlyph]:
    expand = _make_sentinel_expander(metadata)

    rewritten: dict[str, JoinGlyph] = {}
    for glyph_name, meta in metadata.items():
        changes: dict[str, Any] = {}
        for field in _EXTENSION_RULES_FIELDS:
            rules: tuple[ExtensionSpec, ...] = getattr(meta, field)
            if not rules:
                continue
            new_rules: list[ExtensionSpec] = []
            rule_changed = False
            for rule in rules:
                if not rule.targets:
                    new_rules.append(rule)
                    continue
                new_targets = expand(rule.targets)
                if new_targets is rule.targets:
                    new_rules.append(rule)
                    continue
                rule_changed = True
                new_rules.append(ExtensionSpec(by=rule.by, targets=new_targets))
            if rule_changed:
                changes[field] = tuple(new_rules)
        for field in _CONTRACTION_SPEC_FIELDS:
            spec: ExtensionSpec | None = getattr(meta, field)
            if spec is None or not spec.targets:
                continue
            new_targets = expand(spec.targets)
            if new_targets is spec.targets:
                continue
            changes[field] = ExtensionSpec(by=spec.by, targets=new_targets)
        if changes:
            rewritten[glyph_name] = replace(meta, **changes)
        else:
            rewritten[glyph_name] = meta
    return rewritten


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
    return tuple(new_row if i == row_idx else existing for i, existing in enumerate(bitmap))


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
    for key in ("extend_entry_after", "extend_exit_before"):
        rules: tuple[ExtensionSpec, ...] = getattr(join_glyph, key)
        if not rules:
            continue
        if len(rules) == 1:
            rule = rules[0]
            glyph_def[key] = {"by": rule.by, "targets": list(rule.targets)}
        else:
            glyph_def[key] = [{"by": rule.by, "targets": list(rule.targets)} for rule in rules]
    for key in ("contract_entry_after", "contract_exit_before"):
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
    if join_glyph.strip_entry_before:
        glyph_def["strip_entry_before"] = True
    if join_glyph.terminal_default:
        glyph_def["terminal_default"] = True
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
    extend_entry_after: tuple[ExtensionSpec, ...] | object = _UNSET,
    extend_exit_before: tuple[ExtensionSpec, ...] | object = _UNSET,
    extend_exit_before_gated: tuple[tuple[str, tuple[str, ...]], ...] | object = _UNSET,
    extend_exit_when_entered: int | None | object = _UNSET,
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
    resolved_entry_curs_only = source.entry_curs_only if entry_curs_only is _UNSET else entry_curs_only
    resolved_exit = source.exit if exit is _UNSET else exit
    resolved_exit_ink_y = source.exit_ink_y if exit_ink_y is _UNSET else exit_ink_y
    if exit is not _UNSET and not resolved_exit:
        resolved_exit_ink_y = None
    resolved_after = source.after if after is _UNSET else after
    resolved_before = source.before if before is _UNSET else before
    resolved_not_after = source.not_after if not_after is _UNSET else not_after
    resolved_not_before = source.not_before if not_before is _UNSET else not_before
    resolved_reverse_upgrade_from = (
        source.reverse_upgrade_from if reverse_upgrade_from is _UNSET else reverse_upgrade_from
    )
    resolved_preferred_over = source.preferred_over if preferred_over is _UNSET else preferred_over
    resolved_word_final = source.word_final if word_final is _UNSET else word_final
    resolved_extend_entry_after = (
        source.extend_entry_after if extend_entry_after is _UNSET else extend_entry_after
    )
    resolved_extend_exit_before = (
        source.extend_exit_before if extend_exit_before is _UNSET else extend_exit_before
    )
    resolved_extend_exit_before_gated = (
        source.extend_exit_before_gated if extend_exit_before_gated is _UNSET else extend_exit_before_gated
    )
    # Unlike most fields, this is not inherited from `source`. It applies only to the authored backward-upgrade target, and copying it onto the `.ex-ext-1`, `.en-ext-1`, or `.noentry` variants could extend an already extended stance again.
    resolved_extend_exit_when_entered = (
        None if extend_exit_when_entered is _UNSET else extend_exit_when_entered
    )
    resolved_gated_before = source.gated_before if gated_before is _UNSET else gated_before
    resolved_contract_entry_after = (
        source.contract_entry_after if contract_entry_after is _UNSET else contract_entry_after
    )
    resolved_contract_exit_before = (
        source.contract_exit_before if contract_exit_before is _UNSET else contract_exit_before
    )
    resolved_noentry_after = source.noentry_after if noentry_after is _UNSET else noentry_after
    resolved_extend_exit_no_entry = (
        source.extend_exit_no_entry if extend_exit_no_entry is _UNSET else extend_exit_no_entry
    )
    resolved_noentry_for = source.noentry_for if noentry_for is _UNSET else noentry_for

    # When the caller sets `after` (or `before`) and leaves the negative list inherited, drop inherited `not_after` / `not_before` names that the new positive list contains, since they would suppress it. For example, `_add_entry_extension_variants` gives `qsDay.half.en-y0.ex-y0.en-ext-1` `after = (qsTea, qsYe, qsTea_qsOy)`, and its inherited `not_after = (qsTea, qsYe, qsWay)` becomes `(qsWay,)`.
    if (
        after is not _UNSET
        and not_after is _UNSET
        and isinstance(after, tuple)
        and after
        and isinstance(resolved_not_after, tuple)
        and resolved_not_after
    ):
        after_set = set(after)
        filtered_not_after = tuple(family for family in resolved_not_after if family not in after_set)
        if filtered_not_after != resolved_not_after:
            resolved_not_after = filtered_not_after
    if (
        before is not _UNSET
        and not_before is _UNSET
        and isinstance(before, tuple)
        and before
        and isinstance(resolved_not_before, tuple)
        and resolved_not_before
    ):
        before_set = set(before)
        filtered_not_before = tuple(family for family in resolved_not_before if family not in before_set)
        if filtered_not_before != resolved_not_before:
            resolved_not_before = filtered_not_before

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
        # Inherited from `source`, or set when this derivation adds an `en-*` modifier (such as `en-ext-1`). The `en-*` tokens already in the source's name do not count on their own.
        is_entry_variant=source.is_entry_variant
        or any(modifier.startswith("en-") for modifier in add_modifiers),
        entry_suffix=_entry_suffix_from_modifiers(list(resolved_modifiers)),
        exit_suffix=_exit_suffix_from_modifiers(list(resolved_modifiers)),
        extended_entry_suffix=_extended_entry_suffix_from_modifiers(list(resolved_modifiers)),
        extended_exit_suffix=_extended_exit_suffix_from_modifiers(list(resolved_modifiers)),
        entry_restriction_y=_entry_restriction_y_from_modifiers(list(resolved_modifiers)),
        is_noentry=resolved_is_noentry,
        bitmap=resolved_bitmap,
        y_offset=resolved_y_offset,
        advance_width=resolved_advance_width,
        extend_entry_after=resolved_extend_entry_after,
        extend_exit_before=resolved_extend_exit_before,
        extend_exit_before_gated=resolved_extend_exit_before_gated,
        extend_exit_when_entered=resolved_extend_exit_when_entered,
        gated_before=resolved_gated_before,
        noentry_after=resolved_noentry_after,
        extend_exit_no_entry=resolved_extend_exit_no_entry,
        noentry_for=resolved_noentry_for,
        generated_from=generated_from,
        transform_kind=transform_kind,
        contracted_entry_suffix=_contracted_entry_suffix_from_modifiers(list(resolved_modifiers)),
        contracted_exit_suffix=_contracted_exit_suffix_from_modifiers(list(resolved_modifiers)),
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
            extend_entry_after=(),
            extend_exit_before=(),
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
    # A ligature takes entry-side rules from its lead component and exit-side rules from its trailing component. Its other components are inside the ligature and never join a neighbor, so variants for them could never be used.
    ligature_position = -1 if side == "exit" else 0
    noentry_ligature_bases = {
        glyph.base_name for glyph in join_glyphs.values() if glyph.sequence and glyph.noentry_after
    }

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
        is_ligature = bool(other_sequence) and other_sequence[ligature_position] == base_name
        # The FEA emitter's `lig → lig.noentry` rule for a ligature's `noentry_after` matches only the base ligature glyph, so an extended or contracted exit variant of such a ligature would escape it. Those ligatures get only the variants their YAML declares, and so do their stances (`qsDay_qsEat.half`), so that every form of the ligature extends its exit before the same followers.
        if is_ligature and side == "exit" and other_base in noentry_ligature_bases:
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
        "extend_entry_after": (),
        "extend_exit_before": (),
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
        return sentinel.kind == f"{side}_y" and sentinel.y == y

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
        anchors = candidate.exit if side == "exit" else (*candidate.entry, *candidate.entry_curs_only)
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
        selector for selector in source_context if _selector_has_anchor_y(join_glyphs, selector, y, side=side)
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
    kind = f"en-{suffix_word}"

    for anchor in entries if use_height_specific_names else (entries[0],):
        y = anchor[1]
        if use_height_specific_names:
            label = str(y)
            modifier = f"en-{suffix_word}-at-{label}"
            variant_name = f"{target_name}.en-{suffix_word}-at-{label}"
        else:
            modifier = f"en-{suffix_word}"
            variant_name = f"{target_name}.en-{suffix_word}"

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
            kwargs["extend_entry_after"] = ()
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
    kind = f"ex-{suffix_word}"
    variant_name = f"{target_name}.ex-{suffix_word}"
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
        "add_modifiers": (f"ex-{suffix_word}",),
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
        kwargs["extend_exit_before"] = ()
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
        rules: tuple[ExtensionSpec, ...] = getattr(join_glyph, field)
        gated_entries = join_glyph.extend_exit_before_gated if side == "exit" and rules else ()
        if not rules:
            continue
        if not getattr(join_glyph, side):
            continue

        source_anchor_ys = frozenset(anchor[1] for anchor in getattr(join_glyph, side))

        for rule in rules:
            context_glyphs = rule.targets
            if not context_glyphs and not gated_entries:
                continue

            count = rule.by
            suffix_word = _EXTENSION_SUFFIX.get(count)
            if suffix_word is None:
                raise ValueError(
                    f"by: {count} exceeds the supported extension ladder "
                    f"(max: {max(_EXTENSION_SUFFIX)}); add a new rung to "
                    f"_EXTENSION_SUFFIX (and the matching tables in "
                    f"tools/quikscript_fea.py and .vscode/quikscript.schema.json) "
                    f"if a larger reach is needed."
                )

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
    kind = f"en-{suffix_word}"

    for anchor in entries if use_height_specific_names else (entries[0],):
        y = anchor[1]
        if use_height_specific_names:
            label = str(y)
            modifier = f"en-{suffix_word}-at-{label}"
            variant_name = f"{target_name}.en-{suffix_word}-at-{label}"
        else:
            modifier = f"en-{suffix_word}"
            variant_name = f"{target_name}.en-{suffix_word}"

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

        if count >= 2:
            trimmed_bitmap = _trim_bitmap_left_at(
                target_glyph.bitmap,
                y,
                target_glyph.y_offset,
                anchor[0] + count - 1,
            )
            if trimmed_bitmap is not target_glyph.bitmap:
                kwargs["bitmap"] = trimmed_bitmap

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
    kind = f"ex-{suffix_word}"
    variant_name = f"{target_name}.ex-{suffix_word}"
    if variant_name in join_glyphs or (not is_source and variant_name in variants):
        return

    exit_y = target_glyph.exit[0][1]
    kwargs = {
        "exit": _shift_anchors(target_glyph.exit, dx=-count),
        "add_modifiers": (f"ex-{suffix_word}",),
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
        modifier = f"en-trim-{count}"
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

        new_bitmap = _trim_bitmap_left_at(receiver_glyph.bitmap, entry_y, receiver_glyph.y_offset, count)
        if new_bitmap is receiver_glyph.bitmap:
            continue
        kwargs = {
            "bitmap": new_bitmap,
            "add_modifiers": (modifier,),
            "generated_from": receiver_name,
            "transform_kind": "entry-trimmed",
        }
        kwargs.update(_cleared_extension_context())
        # An entry-side trim leaves the exit unchanged, so the receiver's `before`, `not_before`, and `reverse_upgrade_from` still apply and are inherited. Clearing them would let one trimmed stance match every follower and draw its connecting stroke whatever follows.
        for inherited in ("before", "not_before", "reverse_upgrade_from"):
            kwargs.pop(inherited)
        kwargs["after"] = (source_contracted_name,)
        # `reverse_upgrade_from` holds glyph names. After a contracted lead, each of those glyphs appears only as its `en-trim` variant, so point each name at that variant.
        if receiver_glyph.reverse_upgrade_from:
            kwargs["reverse_upgrade_from"] = tuple(
                f"{target}.{modifier}" for target in receiver_glyph.reverse_upgrade_from
            )
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
    """True if `after_tuple` names the contracting source glyph or its family.

    An entry-extended receiver's `after` lists the left neighbors its extension was made for. A trimmed copy of it fits only when the contracting source is one of them.
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
        suffix_word = _CONTRACTION_SUFFIX.get(count)
        if suffix_word is None:
            raise ValueError(
                f"by: {count} exceeds the supported contraction ladder "
                f"(max: {max(_CONTRACTION_SUFFIX)}); add a new rung to "
                f"_CONTRACTION_SUFFIX (and the matching tables in "
                f"tools/quikscript_fea.py and .vscode/quikscript.schema.json) "
                f"if a larger reach is needed."
            )
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
            contracted_source_name = f"{name}.ex-{suffix_word}"
            if contracted_source_name in variants:
                join_y = join_glyph.exit[0][1]
                for receiver_family in context_glyphs:
                    family_glyph = join_glyphs.get(receiver_family)
                    if family_glyph is None:
                        continue
                    for receiver_name, receiver_glyph, _is_receiver_source in _iter_related_extension_targets(
                        join_glyphs,
                        source_name=receiver_family,
                        source_glyph=family_glyph,
                        side="entry",
                        kind="contracted",
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

    return variants


def expand_selectors_for_ligatures(
    join_glyphs: dict[str, JoinGlyph],
) -> dict[str, JoinGlyph]:
    """Return the glyphs with `before`, `gated_before`, and `after` extended so a selector that names a ligature component also matches the ligature's neighbor-facing glyphs.

    The `calt` lookups that run before `calt_liga` see the uncollapsed components. A `before` selector naming a ligature's second or later component therefore misses, because the next glyph is the ligature's lead component. An `after` selector likewise sees only the trailing component. After `calt_liga`, the neighbor is the ligature glyph itself. This pass adds the lead (or trailing) component and the ligature's variants, so a selector such as "before qsUtter" also covers each ligature that contains ·Utter.

    An addition needs an anchor Y that the source shares with the ligature. A lead or trailing component is added only for a shared Y that the component does not already reach on its own. Additions are also skipped when a `before` addition's `noentry_after` names the source's family, when a ligature variant's extension or contraction suffix differs from the one the source triggers on that component, and when the added glyph has its own `after` (or `before`) list that leaves out the source's family.

    Negative selectors (`not_before`, `not_after`) are not expanded, because they usually mean one literal glyph and expanding them would suppress joins the author wanted. A negative ligature exception is written by hand.
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
        canonical_ys = frozenset(anchor[1] for anchor in (*canonical.entry, *canonical.entry_curs_only))
        if canonical_ys:
            return canonical_ys
        # Some families (such as qsJai) have no entry anchor on the base record, only on stances, so use the union of the stances' entry Ys.
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

    # Keyed by component name (base or variant). Each value lists `(endpoint, candidate_ys, canonical_ys)`: `candidate_ys` are the Ys the ligature's variants reach, which the source meets after `calt_liga`, and `canonical_ys` are the Ys the endpoint component already reaches on its own. A component endpoint is added only for a Y the ligature reaches and the component does not. Otherwise the rule would fire on the bare component without enabling a new join, and would take precedence over broader fallback stances.
    forward_entries_by_component: dict[str, list[tuple[str, frozenset[int], frozenset[int]]]] = {}
    backward_entries_by_component: dict[str, list[tuple[str, frozenset[int], frozenset[int]]]] = {}

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

        # Register each ligature variant under the components it represents, so a selector naming a component also matches the ligature glyph after `calt_liga`. Without this, `_collect_post_liga_right_cleanup_rules` in `quikscript_fea.py` finds no ligature in the source's `after` and demotes the source, and the `calt_post_liga_*` rules, which are emitted only for an `after` list that contains a ligature, are never written.
        for lig_variant_name in sorted(base_to_variants.get(record.base_name, {record.name})):
            lig_variant = join_glyphs[lig_variant_name]
            lig_variant_entry_ys = frozenset(
                anchor[1] for anchor in (*lig_variant.entry, *lig_variant.entry_curs_only)
            )
            lig_variant_exit_ys = frozenset(anchor[1] for anchor in lig_variant.exit)

            # A three-component `qsA_qsB_qsC` is registered under `qsA`, `qsB`, and `qsC`, since after `calt_liga` a selector naming any of them should match it.
            forward_keys: set[str] = set(sequence)
            backward_keys: set[str] = set(sequence)

            # `calt_liga` carries a component's suffix onto the ligature (`qsX` + `qsUtter.ex-con-2` becomes `qsX_qsUtter.ex-con-2`). A variant with an entry-extension suffix is also registered under the lead component with that suffix, and one with an exit-extension or exit-contraction suffix under the trailing component with that suffix. A selector naming `qsUtter.ex-con-2` then matches only ligature variants in the same state; matching the base `qsX_qsUtter` would cause bitmap-misalignment warnings.
            entry_suffix = lig_variant.extended_entry_suffix or ""
            if entry_suffix:
                first_variant = first + entry_suffix
                if first_variant in join_glyphs:
                    forward_keys.add(first_variant)

            exit_suffix = lig_variant.extended_exit_suffix or lig_variant.contracted_exit_suffix or ""
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
        """Return the exit suffix (`.ex-con-N` or `.ex-ext-N`) that `last_family`'s base stance takes when `source_family` follows it, or "" if none. Ligature variants in any other state are not added, so `qsJai`'s `after` gets `qsX_qsUtter.ex-con-2` but not `qsX_qsUtter`, because ·Utter always contracts before ·J’ai."""
        last_meta = join_glyphs.get(last_family)
        if last_meta is None:
            return ""
        if last_meta.contract_exit_before and source_family in last_meta.contract_exit_before.targets:
            suffix_word = _CONTRACTION_SUFFIX.get(last_meta.contract_exit_before.by)
            if suffix_word:
                return f".ex-{suffix_word}"
        for rule in last_meta.extend_exit_before:
            if source_family in rule.targets:
                suffix_word = _EXTENSION_SUFFIX.get(rule.by)
                if suffix_word:
                    return f".ex-{suffix_word}"
        return ""

    def _expected_runtime_entry_suffix(source_family: str, first_family: str) -> str:
        """Return the entry suffix (`.en-con-N` or `.en-ext-N`) that `first_family`'s base stance takes when `source_family` precedes it, or "" if none."""
        first_meta = join_glyphs.get(first_family)
        if first_meta is None:
            return ""
        if first_meta.contract_entry_after and source_family in first_meta.contract_entry_after.targets:
            suffix_word = _CONTRACTION_SUFFIX.get(first_meta.contract_entry_after.by)
            if suffix_word:
                return f".en-{suffix_word}"
        for rule in first_meta.extend_entry_after:
            if source_family in rule.targets:
                suffix_word = _EXTENSION_SUFFIX.get(rule.by)
                if suffix_word:
                    return f".en-{suffix_word}"
        return ""

    def _selector_families(selectors: tuple[str, ...]) -> set[str]:
        families: set[str] = set()
        for selector in selectors:
            sentinel = _parse_anchor_sentinel(selector)
            if sentinel is not None:
                if sentinel.family_scope:
                    families.add(sentinel.family_scope.split(".", 1)[0])
                continue
            meta = join_glyphs.get(selector)
            if meta is not None:
                families.add(meta.base_name)
            else:
                families.add(selector.split(".", 1)[0])
        return families

    def _endpoint_accepts_source_family(endpoint_meta: JoinGlyph, source_family: str, side: str) -> bool:
        """True if the endpoint accepts the source as its neighbor. For an entry-side (`before`) addition, the endpoint's `after` must be empty or name the source's family. For an exit-side addition, the same holds for its `before`."""
        if side == "entry":
            if endpoint_meta.after:
                if source_family not in _selector_families(endpoint_meta.after):
                    return False
        else:
            if endpoint_meta.before:
                if source_family not in _selector_families(endpoint_meta.before):
                    return False
        return True

    def _ligature_variant_matches_runtime(endpoint_meta: JoinGlyph, source_family: str, side: str) -> bool:
        sequence = endpoint_meta.sequence
        if not sequence:
            return True
        if side == "exit":
            expected = _expected_runtime_exit_suffix(source_family, sequence[-1])
            actual = endpoint_meta.contracted_exit_suffix or endpoint_meta.extended_exit_suffix or ""
        else:
            expected = _expected_runtime_entry_suffix(source_family, sequence[0])
            actual = endpoint_meta.extended_entry_suffix or ""
        return actual == expected

    def _scoped_anchor_selector(selector: str) -> _AnchorSelector | None:
        sentinel = _parse_anchor_sentinel(selector)
        if sentinel is None or sentinel.family_scope is None:
            return None
        return sentinel

    def _additions(
        field: tuple[str, ...],
        index: dict[str, list[tuple[str, frozenset[int], frozenset[int]]]],
        anchor_ys: frozenset[int],
        *,
        source_family: str | None,
        side: str,
    ) -> tuple[set[str], dict[str, set[str]]]:
        existing = set(field)
        additions: set[str] = set()
        # Lead-component endpoint -> the families, one of which must follow it for the join to happen. Filled only on the entry side, since only the FEA emitter's forward rules use it (`before_lig_lead_followups`).
        lead_followups: dict[str, set[str]] = {}
        for glyph in field:
            scoped_anchor = _scoped_anchor_selector(glyph)
            if scoped_anchor is None:
                lookup_key = glyph
            else:
                assert scoped_anchor.family_scope is not None
                lookup_key = scoped_anchor.family_scope
            # `lookup_base` is the family of the component the selector names; `lookup_key` may be a variant such as `qsUtter.ex-ext-1`. A non-ligature endpoint from another family is a lead component added for a ligature, and it is recorded in `lead_followups` below.
            lookup_meta = join_glyphs.get(lookup_key)
            lookup_base = lookup_meta.base_name if lookup_meta is not None else lookup_key.split(".", 1)[0]
            for endpoint, candidate_ys, canonical_ys in index.get(lookup_key, ()):
                if endpoint in existing:
                    continue
                if scoped_anchor is not None:
                    if scoped_anchor.kind != f"{side}_y":
                        continue
                    if scoped_anchor.y not in candidate_ys:
                        continue
                novel_ys = (anchor_ys & candidate_ys) - canonical_ys
                if not novel_ys:
                    continue
                endpoint_meta = join_glyphs.get(endpoint)
                if source_family is not None:
                    if endpoint_meta is not None:
                        if side == "entry" and source_family in endpoint_meta.noentry_after:
                            # The endpoint drops its entry after this source family, so the source can never join into it. Adding it would give the join validator a join that never happens and emit post-liga rules that never fire.
                            continue
                        if endpoint_meta.sequence and not _ligature_variant_matches_runtime(
                            endpoint_meta, source_family, side
                        ):
                            # This source family extends or contracts the component, so only the ligature variant with that suffix can appear next to it.
                            continue
                        if not _endpoint_accepts_source_family(endpoint_meta, source_family, side):
                            # The endpoint's own `select.after` / `select.before` leaves out the source's family (as `qsThey_qsUtter`'s `after` does for most families), so it never appears next to this source. Adding it would cause a one-sided join warning.
                            continue
                additions.add(endpoint)
                if (
                    side == "entry"
                    and endpoint_meta is not None
                    and not endpoint_meta.sequence
                    and endpoint_meta.base_name != lookup_base
                ):
                    # Record the named component's family so the FEA emitter requires a glyph of that family right after the lead before it substitutes the source.
                    lead_followups.setdefault(endpoint, set()).add(lookup_base)
        return additions, lead_followups

    def _expand_filtered(
        field: tuple[str, ...],
        index: dict[str, list[tuple[str, frozenset[int], frozenset[int]]]],
        anchor_ys: frozenset[int] | None,
        *,
        source_family: str | None,
        side: str,
    ) -> tuple[tuple[str, ...], dict[str, set[str]]]:
        # A source with no anchor on this side cannot join a ligature, so its selector stays as written.
        if not field or not anchor_ys:
            return field, {}
        new_additions, lead_followups = _additions(
            field,
            index,
            anchor_ys,
            source_family=source_family,
            side=side,
        )
        if not new_additions:
            return field, lead_followups
        return tuple([*field, *sorted(new_additions)]), lead_followups

    def _expand_gated(
        gated: tuple[tuple[str, tuple[str, ...]], ...],
        anchor_ys: frozenset[int] | None,
        *,
        source_family: str | None,
    ) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], dict[str, set[str]]]:
        if not gated:
            return gated, {}
        rebuilt: list[tuple[str, tuple[str, ...]]] = []
        merged_lead_followups: dict[str, set[str]] = {}
        changed = False
        for feature_tag, families in gated:
            new_families, lead_followups = _expand_filtered(
                families,
                forward_entries_by_component,
                anchor_ys,
                source_family=source_family,
                side="entry",
            )
            if new_families is not families:
                changed = True
            rebuilt.append((feature_tag, new_families))
            for lead, trailings in lead_followups.items():
                merged_lead_followups.setdefault(lead, set()).update(trailings)
        if not changed:
            return gated, merged_lead_followups
        return tuple(rebuilt), merged_lead_followups

    updated: dict[str, JoinGlyph] = {}
    for name, record in join_glyphs.items():
        source_exit_ys = frozenset(anchor[1] for anchor in record.exit) if record.exit else None
        source_entry_ys = (
            frozenset(anchor[1] for anchor in (*record.entry, *record.entry_curs_only))
            if record.entry or record.entry_curs_only
            else None
        )
        source_family = record.family or record.base_name
        new_before, lead_followups_before = _expand_filtered(
            record.before,
            forward_entries_by_component,
            source_exit_ys,
            source_family=source_family,
            side="entry",
        )
        new_gated_before, lead_followups_gated = _expand_gated(
            record.gated_before,
            source_exit_ys,
            source_family=source_family,
        )
        new_after, _ = _expand_filtered(
            record.after,
            backward_entries_by_component,
            source_entry_ys,
            source_family=source_family,
            side="exit",
        )
        combined_lead_followups: dict[str, set[str]] = {}
        for lead, trailings in lead_followups_before.items():
            combined_lead_followups.setdefault(lead, set()).update(trailings)
        for lead, trailings in lead_followups_gated.items():
            combined_lead_followups.setdefault(lead, set()).update(trailings)
        before_lig_lead_followups: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
            (lead, tuple(sorted(trailings))) for lead, trailings in sorted(combined_lead_followups.items())
        )
        if (
            new_before is record.before
            and new_gated_before is record.gated_before
            and new_after is record.after
            and before_lig_lead_followups == record.before_lig_lead_followups
        ):
            updated[name] = record
            continue
        updated[name] = replace(
            record,
            before=new_before,
            gated_before=new_gated_before,
            after=new_after,
            before_lig_lead_followups=before_lig_lead_followups,
        )
    return updated


def expand_join_transforms(
    join_glyphs: dict[str, JoinGlyph],
    *,
    has_zwnj: bool = False,
) -> tuple[dict[str, JoinGlyph], list[JoinTransform]]:
    expanded = dict(join_glyphs)
    transforms: list[JoinTransform] = []
    expanded.update(generate_noentry_variants(expanded, has_zwnj=has_zwnj, transforms=transforms))
    for side in ("entry", "exit"):
        expanded.update(_generate_extended_variants(expanded, side=side, transforms=transforms))
    for side in ("entry", "exit"):
        expanded.update(_generate_contracted_variants(expanded, side=side, transforms=transforms))
    return expanded, transforms


def flatten_join_glyphs(join_glyphs: dict[str, JoinGlyph]) -> dict[str, GlyphDef]:
    return {glyph_name: _materialize_join_glyph(join_glyph) for glyph_name, join_glyph in join_glyphs.items()}


SS10_TWIN_MODIFIER = "ss10"


def ss10_twin_name(base_name: str) -> str:
    """Return the name of a letter's ss10 twin: `qsDay.ss10` for qsDay."""
    return f"{base_name}.{SS10_TWIN_MODIFIER}"


def ss10_twins(join_glyphs: dict[str, JoinGlyph]) -> dict[str, JoinGlyph]:
    """Return the ss10 twin of each letter that can join, keyed by twin name. A letter can join when a glyph of its family has a cursive anchor or when it is a ligature's component. A twin is drawn like the letter's bare glyph, with the same advance and the same kerning (`generate_kern_fea` in tools/build_font.py), but it has no cursive anchors, no cmap entry, and is in no join lookup. Under ss10 every glyph of such a letter becomes the letter's twin before any join lookup runs (`emit_ss10_isolated_input` in tools/quikscript_fea.py), so no ligature forms and no two glyphs attach."""
    joining = {
        meta.base_name for meta in join_glyphs.values() if meta.entry or meta.entry_curs_only or meta.exit
    }
    joining.update(
        component for meta in join_glyphs.values() if len(meta.sequence) > 1 for component in meta.sequence
    )
    return {
        ss10_twin_name(name): replace(
            meta,
            name=ss10_twin_name(name),
            modifiers=(SS10_TWIN_MODIFIER,),
            compat_assertions=_compat_assertions_from_modifiers([SS10_TWIN_MODIFIER], meta.traits),
            entry=(),
            entry_curs_only=(),
            exit=(),
            exit_ink_y=None,
        )
        for name, meta in join_glyphs.items()
        if name == meta.base_name and len(meta.sequence) <= 1 and name in joining
    }


def compile_quikscript_ir(
    glyph_data: GlyphData,
    variant: str,
) -> tuple[dict[str, JoinGlyph], list[JoinTransform]]:
    glyph_families = glyph_data.get("glyph_families", {})
    context_sets = glyph_data.get("context_sets", {})
    family_names = set(glyph_families)

    join_glyphs = {}
    records = list(
        _iter_compiled_family_stances(
            glyph_families,
            variant,
            context_sets=context_sets,
        )
    )
    available_names = frozenset(record["output_name"] for record in records)
    for record in records:
        glyph_def = _family_stance_to_glyph_def(
            record["family_name"],
            record["family_def"],
            record["stance_def"],
            stance_name=record["stance_name"],
            contextual=record["contextual"],
            family_names=family_names,
            context_sets=context_sets,
            available_names=available_names,
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
            authored_modifiers=[*record["traits"], *record.get("authored_modifiers", record["modifiers"])],
        )
    join_glyphs = _expand_anchor_sentinels_in_extension_targets(join_glyphs)
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
    """Return the bitmap row that lives at glyph-space ``y``, or ``None`` if no such row exists. Top row is at ``y = (len(bitmap) - 1) + y_offset``; bottom row is at ``y = y_offset``."""
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
    """True if both glyphs have a row at glyph-space ``y`` and their leftmost ink in it is in the same column. An entry anchor copied between glyphs that fail this check would leave a visible gap at the join."""
    source_row = _bitmap_row_at_y(source_glyph.bitmap, source_glyph.y_offset, y)
    target_row = _bitmap_row_at_y(target_glyph.bitmap, target_glyph.y_offset, y)
    if source_row is None or target_row is None:
        return False
    return _leftmost_ink_column(source_row) == _leftmost_ink_column(target_row)


def _find_lead_entry_source(
    join_glyphs: dict[str, JoinGlyph],
    lead_family: str,
    ligature_after: frozenset[str] = frozenset(),
) -> tuple[tuple[Anchor, ...], JoinGlyph] | None:
    """Return ``(entries, source_glyph)`` for the entry anchor a ligature should inherit from its lead, or ``None``.

    The candidates, in order:

    1. The lead's base glyph (named ``lead_family``), if it has an entry. It is the unrestricted default stance.
    2. The lead's ``en-y5`` stance (found through ``heal_glyph_name``), if it has an entry and either has no ``after`` or has the same ``after`` set as the ligature. An ``en-y5`` restricted to other predecessors is rejected, because copying its anchor would give the ligature an entry after predecessors the lead does not accept.

    Stances with other authored modifiers, such as ``qsTea.en-y5.ex-y0.after-fee``, are not candidates. One anchor is enough to give the ligature an entry, and ``_iter_related_extension_targets`` then applies the lead's ``extend_entry_after`` rules to it.
    """
    lead_prop = join_glyphs.get(lead_family)
    if lead_prop is not None and lead_prop.entry:
        return lead_prop.entry, lead_prop

    # The en-y5 stance may compile with more anchor-Y modifiers, such as `qsJai.en-y5.ex-y0`.
    canonical_name = heal_glyph_name(
        f"{lead_family}.en-y5",
        family_names_from_compiled(set(join_glyphs)),
        frozenset(join_glyphs),
    )
    canonical = join_glyphs.get(canonical_name)
    if canonical is not None and canonical.entry:
        if not canonical.after:
            return canonical.entry, canonical
        if frozenset(canonical.after) == ligature_after:
            return canonical.entry, canonical

    return None


def _inherit_ligature_entries_from_lead(
    join_glyphs: dict[str, JoinGlyph],
) -> dict[str, JoinGlyph]:
    """Return the glyphs with an entry anchor copied from the lead component (``_find_lead_entry_source``) onto each authored ligature stance that has none.

    The copied entry lets ``_iter_related_extension_targets`` apply the lead's ``extend_entry_after`` rules to the ligature without repeating them in the YAML. It is copied only when ``_bitmaps_align_at_y`` passes at the entry's Y.

    A ligature stance that declares its own entry keeps it and gets a ``LigatureEntryInheritanceWarning`` saying whether inheritance would give the same anchor, a different one, or none (the lead has no candidate stance, or the bitmaps do not align).

    Stances with a trait (``qsDay_qsUtter.half``) are skipped, because their entry differs from the lead's: a half lead exits at the baseline, not the x-height. Writing ``entry: null`` in the ligature's anchors also skips it with no warning, for a ligature that has no entry even though its lead does.
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
        inheritable = _find_lead_entry_source(join_glyphs, lead, frozenset(glyph.after))

        if not glyph.entry:
            if glyph.entry_explicitly_none:
                continue
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
                f"lead {lead} has no auto-inheritable entry-bearing stance. "
                f"The explicit declaration is therefore load-bearing; "
                f"consider adding an en-y5 stance on {lead} or "
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


def has_entry_preserving_exit_noentry_sibling(
    l_meta: JoinGlyph,
    base_to_variants: dict[str, set[str]],
    join_glyphs: dict[str, JoinGlyph],
) -> bool:
    """True if ``l_meta`` has an ``ex-noentry`` sibling: a stance with the same entry anchors, no exit, no selectors, and ``l_meta``'s modifiers with the ``ex-*`` ones replaced by ``ex-noentry``. When a following ``noentry_after`` glyph voids the join, the post-liga cleanup can switch to that sibling, so ``l_meta`` needs no ``not_before`` for it."""
    expected_modifiers = frozenset(m for m in l_meta.modifiers if not m.startswith("ex-")) | {"ex-noentry"}
    for sibling_name in base_to_variants.get(l_meta.base_name, frozenset()):
        sibling = join_glyphs.get(sibling_name)
        if sibling is None or sibling.is_noentry:
            continue
        if sibling.exit:
            continue
        if sibling.entry != l_meta.entry:
            continue
        if sibling.entry_curs_only != l_meta.entry_curs_only:
            continue
        if sibling.after or sibling.before or sibling.not_after or sibling.not_before:
            continue
        if "ex-noentry" not in sibling.modifiers:
            continue
        if frozenset(sibling.modifiers) == expected_modifiers:
            return True
    return False


def _propagate_noentry_after_to_not_before(
    join_glyphs: dict[str, JoinGlyph],
) -> dict[str, JoinGlyph]:
    """Add each ``noentry_after`` glyph's family to ``not_before`` on the left glyphs whose join it voids.

    For an authored glyph R with ``noentry_after: [F, …]``, every authored stance of each family F whose exit Y matches one of R's entry Ys gets R's family in ``not_before`` (and in ``not_before_from_noentry_after``). This keeps F from taking a joining stance before R when R's ``.noentry`` substitution would void the join. A stance with a ``before`` list that does not name R's family is left alone.

    A stance with an ``ex-noentry`` sibling (``has_entry_preserving_exit_noentry_sibling``) is also left alone. The post-liga cleanup switches it to the sibling, and a ``not_before`` would only cost its predecessor the join into it.
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
        r_entry_ys = {anchor[1] for anchor in (*r_meta.entry, *r_meta.entry_curs_only)}
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
                if has_entry_preserving_exit_noentry_sibling(l_meta, base_to_variants, join_glyphs):
                    continue
                additions.setdefault(l_name, set()).add(r_family)

    if not additions:
        return join_glyphs

    updated = dict(join_glyphs)
    for l_name, families_to_add in additions.items():
        l_meta = updated[l_name]
        merged = tuple(sorted(set(l_meta.not_before) | families_to_add))
        propagated = tuple(sorted(set(l_meta.not_before_from_noentry_after) | families_to_add))
        updated[l_name] = replace(
            l_meta,
            not_before=merged,
            not_before_from_noentry_after=propagated,
        )
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
    "has_entry_preserving_exit_noentry_sibling",
    "resolve_known_glyph_names",
    "ss10_twin_name",
    "ss10_twins",
]
