"""GSUB emission in the prototype-proven section 7 shape (M1-PLAN section 5, Group 3).

Stage order, fixed by lookup definition order (which fixes LookupList indices and hence cross-feature application order on both shapers): formation (unconditional type-4 over the registry's ligature sequences) → ss marker substitutions (unconditional, per set, staged after formation so enabling a set cannot un-form a ligature; composite markers render multi-set union states) → the ZWNJ chokepoint (`sub uni200C @entry-live' by @entry-locked`) → ONE settlement lookup of chained-context single substitutions with per-family `subtable;` breaks, positive rules only — then, post-settlement, the ss10 overlay (cell → isolated cell) and the namer-dot mini-calt (supplied here because `_namer_dot_calt_fea` is a no-op on the `senior_fea` path).

Rule consumption is duck-typed against Group 2's `table.DecisionTable`: each rule exposes `input_glyph`, `backtrack` / `look1` / `look2` (tuples of glyph labels or None), `outcome`, `joint`, `provenance`. When `tables_by_config` carries several configurations, their rule lists are folded by exact-duplicate union with a conflict assertion — sound exactly when the table builder already disambiguates inputs by marker labels per configuration (the prototype's feature-fold invariant); a same-window different-outcome collision raises.

Invariants asserted before returning: no locked twin and no chokepoint output appears in any raw lookahead class; every glyph named by any rule exists in the supplied glyph inventory; zero selection-semantics `ignore sub` (the namer-dot stage's guard, today's proven shape, is the one sanctioned exemption and is absent from the M1 mini-font anyway, which has no mid-word guard glyphs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from rebuild.pipeline.model import (
    CellId,
    GlyphRecord,
    ResolvedSpec,
    locked_glyph_name,
    marker_glyph_name,
    relevant_marker_features,
)


class EmitError(Exception):
    pass


@dataclass
class GsubPlan:
    fea_text: str
    class_definitions: list[str] = field(default_factory=list)
    rule_count: int = 0
    marker_glyphs: dict[str, str] = field(default_factory=dict)  # marker glyph -> base raw glyph
    locked_glyphs: dict[str, str] = field(default_factory=dict)  # locked twin -> raw glyph
    named_glyphs: frozenset[str] = frozenset()


class _ClassRegistry:
    def __init__(self) -> None:
        self.by_members: dict[tuple[str, ...], str] = {}
        self.definitions: list[str] = []

    def ref(self, members: tuple[str, ...], hint: str) -> str:
        if len(members) == 1:
            return members[0]
        members = tuple(sorted(members))
        name = self.by_members.get(members)
        if name is None:
            name = f"@{hint}"
            suffix = 0
            while any(line.startswith(name + " ") for line in self.definitions):
                suffix += 1
                name = f"@{hint}_{suffix}"
            self.by_members[members] = name
            self.definitions.append(f"{name} = [{' '.join(members)}];")
        return name


def _fea_safe(label: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in "._") else "_" for ch in label)


def marker_states(rune_name: str, features: tuple[str, ...]) -> dict[str, frozenset[str]]:
    """Every marker glyph name the rune can wear, keyed by glyph name, valued by the active relevant set."""
    states: dict[str, frozenset[str]] = {}
    for mask in range(1, 1 << len(features)):
        active = frozenset(feature for index, feature in enumerate(features) if mask & (1 << index))
        states[marker_glyph_name(rune_name, active)] = active
    return states


def _marker_lookups(spec: ResolvedSpec) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Per stylistic set, the marker substitution lines; plus the marker-glyph registry. The lookup for set F maps every union state over the sets emitted before F (and the bare rune) to the state plus F, so multi-set configurations compose in definition order."""
    per_feature: dict[str, list[str]] = {}
    marker_glyphs: dict[str, str] = {}
    all_features = sorted(
        {feature for rune in spec.runes.values() for feature in relevant_marker_features(rune)}
    )
    for rune_name, rune in spec.runes.items():
        relevant = relevant_marker_features(rune)
        if not relevant:
            continue
        for glyph in marker_states(rune_name, relevant):
            marker_glyphs[glyph] = rune_name
        for index, feature in enumerate(sorted(relevant, key=all_features.index)):
            earlier = tuple(sorted(relevant, key=all_features.index)[:index])
            lines = per_feature.setdefault(feature, [])
            for mask in range(1 << len(earlier)):
                state = frozenset(f for bit, f in enumerate(earlier) if mask & (1 << bit))
                source = marker_glyph_name(rune_name, state)
                target = marker_glyph_name(rune_name, state | {feature})
                lines.append(f"    sub {source} by {target};")
    return per_feature, marker_glyphs


def _entry_live_members(spec: ResolvedSpec) -> list[str]:
    members: list[str] = []
    for rune_name, rune in spec.runes.items():
        if not any(stance.surface.entries for stance in rune.stances.values()):
            continue
        members.append(rune_name)
        for glyph in marker_states(rune_name, relevant_marker_features(rune)):
            members.append(glyph)
    return sorted(members)


@dataclass(frozen=True)
class _FoldedRule:
    input_glyph: str
    backtrack: tuple[str, ...] | None
    look1: tuple[str, ...] | None
    look2: tuple[str, ...] | None
    outcome: str
    provenance: tuple[str, ...]
    joint: bool


def _config_features(config) -> frozenset[str]:
    if isinstance(config, str):
        return frozenset(config.split("+")) - {"default"}
    return frozenset(config)


def _raw_rename_map(spec: ResolvedSpec | None, features: frozenset[str]) -> dict[str, str]:
    """The marker fold: under a configuration, every raw label of a rune whose own capability the active sets change is worn as the marker twin (and its chokepoint twin follows), because the marker lookups run unconditionally before settlement."""
    renames: dict[str, str] = {}
    if spec is None:
        return renames
    for rune_name, rune in spec.runes.items():
        relevant = frozenset(relevant_marker_features(rune)) & features
        if not relevant:
            continue
        marker = marker_glyph_name(rune_name, relevant)
        renames[rune_name] = marker
        renames[locked_glyph_name(rune_name)] = locked_glyph_name(marker)
    return renames


def _renamed(rule, renames: dict[str, str]):
    if not renames:
        return rule

    def slot(members):
        if members is None:
            return None
        return tuple(renames.get(member, member) for member in members)

    return _FoldedRule(
        input_glyph=renames.get(rule.input_glyph, rule.input_glyph),
        backtrack=slot(rule.backtrack),
        look1=slot(rule.look1),
        look2=slot(rule.look2),
        outcome=renames.get(rule.outcome, rule.outcome),
        provenance=tuple(rule.provenance or ()),
        joint=bool(getattr(rule, "joint", False)),
    )


def _fold_rules(tables_by_config: Mapping, spec: ResolvedSpec | None = None) -> list:
    rules: list = []
    seen: dict[tuple, str] = {}
    for config in sorted(tables_by_config, key=lambda c: sorted(_config_features(c))):
        table = tables_by_config[config]
        if isinstance(table, (tuple, list)):
            table = table[0]
        renames = _raw_rename_map(spec, _config_features(config))
        for raw_rule in getattr(table, "rules", ()):
            rule = _renamed(raw_rule, renames)
            key = (rule.input_glyph, rule.backtrack, rule.look1, rule.look2)
            existing = seen.get(key)
            if existing is not None:
                if existing != rule.outcome:
                    raise EmitError(
                        f"feature fold conflict at {key}: {existing} vs {rule.outcome} — the marker encoding cannot express this"
                    )
                continue
            seen[key] = rule.outcome
            rules.append(rule)
    return rules


def _settle_lines(
    rules: Iterable, registry: _ClassRegistry, marker_names: frozenset[str] = frozenset()
) -> tuple[list[str], int]:
    def mentions_marker(rule) -> bool:
        return any(label in marker_names for slot in (rule.look1, rule.look2) for label in slot or ())

    by_input: dict[str, list] = {}
    for rule in rules:
        by_input.setdefault(rule.input_glyph, []).append(rule)
    by_family: dict[str, list] = {}
    for input_glyph, input_rules in by_input.items():
        # First-match-wins discipline across the config fold: backtracked (committed-left and ZWNJ-guard) rules keep their precedence over slot-dropped boundary-left rules, and within each block a rule whose lookahead names a marker twin sorts ahead of the bare-label rules that would otherwise swallow its windows via a dropped slot — sound because the marker substitution is unconditional, so a marker label and the bare label it shadows never occur in the same stream. Stable, preserving every config's internal ordering.
        ordered = sorted(input_rules, key=lambda rule: (rule.backtrack is None, not mentions_marker(rule)))
        by_family.setdefault(input_glyph.split(".")[0], []).extend(ordered)
    grouped = [rule for family_rules in by_family.values() for rule in family_rules]
    lines: list[str] = []
    counters: dict[str, int] = {}
    current_family: str | None = None
    count = 0
    for rule in grouped:
        family = rule.input_glyph.split(".")[0]
        if current_family is not None and family != current_family:
            lines.append("    subtable;")
        current_family = family
        base = _fea_safe(rule.input_glyph)
        index = counters.get(base, 0)
        counters[base] = index + 1
        parts = ["sub"]
        if rule.backtrack:
            parts.append(registry.ref(tuple(rule.backtrack), f"s_{base}_bk{index}"))
        parts.append(f"{rule.input_glyph}'")
        if rule.look1:
            parts.append(registry.ref(tuple(rule.look1), f"s_{base}_la1_{index}"))
        if rule.look2:
            parts.append(registry.ref(tuple(rule.look2), f"s_{base}_la2_{index}"))
        parts.append(f"by {rule.outcome};")
        provenance = "; ".join(dict.fromkeys(str(p) for p in (rule.provenance or ()) if p))
        comment_bits = [
            bit for bit in ("joint row" if getattr(rule, "joint", False) else "", provenance) if bit
        ]
        comment = f"  # {' | '.join(comment_bits)}" if comment_bits else ""
        lines.append("    " + " ".join(parts) + comment)
        count += 1
    return lines, count


def _assert_invariants(
    rules: Iterable, named_glyphs: frozenset[str], fea: str, locked: frozenset[str]
) -> None:
    for rule in rules:
        for slot in (rule.look1, rule.look2):
            if not slot:
                continue
            leaked = set(slot) & locked
            if leaked:
                raise EmitError(
                    f"locked twin or chokepoint output in a raw lookahead class: {sorted(leaked)}"
                )
    missing: set[str] = set()
    for rule in rules:
        for name in (rule.input_glyph, rule.outcome):
            if name not in named_glyphs:
                missing.add(name)
        for slot in (rule.backtrack, rule.look1, rule.look2):
            for name in slot or ():
                if name not in named_glyphs and name not in ("uni200C", "space", "periodcentered"):
                    missing.add(name)
    if missing:
        raise EmitError(f"rules name glyphs outside the planned glyph set: {sorted(missing)}")
    selection_lines = [line for line in fea.split("\n") if "ignore sub" in line and "namer_dot" not in line]
    if selection_lines:
        raise EmitError(f"selection-semantics ignore sub leaked into the emitted FEA: {selection_lines[:3]}")


def emit_gsub(
    spec: ResolvedSpec,
    tables_by_config: Mapping,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    isolated_cells: Mapping[str, CellId] | None = None,
    namer_dot: tuple[str, str] | None = ("periodcentered", "periodcentered.lowered"),
) -> GsubPlan:
    """`glyphs` and `isolated_cells` (rune name → the cell its isolated form lands in) feed the ss10 overlay and the namer-dot follower class; both stages are skipped with a comment when the inputs are absent (a recorded M1-PLAN section 5 signature extension — the plan's two-argument form cannot reach the glyph inventory)."""
    registry = _ClassRegistry()
    rules = _fold_rules(tables_by_config, spec)
    per_feature_markers, marker_glyphs = _marker_lookups(spec)
    marker_names = frozenset(marker_glyphs) | frozenset(locked_glyph_name(name) for name in marker_glyphs)
    settle_lines, rule_count = _settle_lines(rules, registry, marker_names)

    live_members = _entry_live_members(spec)
    locked_members = [locked_glyph_name(name) for name in live_members]

    names_by_cell: dict[CellId, str] = {}
    if glyphs:
        names_by_cell = {cell: record.name for cell, record in glyphs.items()}

    parts: list[str] = []
    parts.append(
        "# Generated by rebuild/pipeline/emit_gsub.py — the section 7 transducer encoding. Do not hand-edit."
    )
    parts.append("")
    parts.extend(registry.definitions)
    parts.append(f"@m1_entry_live = [{' '.join(live_members)}];")
    parts.append(f"@m1_entry_locked = [{' '.join(locked_members)}];")
    parts.append("")

    formation_lines = []
    for rune_name, rune in spec.runes.items():
        if rune.sequence:
            formation_lines.append(f"    sub {' '.join(rune.sequence)} by {rune_name};")
    parts.append("lookup m1_formation {\n" + "\n".join(formation_lines) + "\n} m1_formation;")

    feature_lookup_names: dict[str, str] = {}
    for feature in sorted(per_feature_markers):
        lookup_name = f"m1_{feature}_marker"
        feature_lookup_names[feature] = lookup_name
        parts.append(
            f"\nlookup {lookup_name} {{\n" + "\n".join(per_feature_markers[feature]) + f"\n}} {lookup_name};"
        )

    parts.append("\nlookup m1_zwnj {\n    sub uni200C @m1_entry_live' by @m1_entry_locked;\n} m1_zwnj;")
    parts.append("\nlookup m1_settle {\n" + "\n".join(settle_lines) + "\n} m1_settle;")

    namer_lines: list[str] = []
    if namer_dot is not None and names_by_cell:
        dot_glyph, lowered_glyph = namer_dot
        shorts = spec.registry.predicate_classes.get("shorts", frozenset())
        followers = sorted(
            {record_name for cell, record_name in names_by_cell.items() if cell.rune in shorts}
        )
        if followers:
            namer_lines.append(f"@m1_namer_short_followers = [{' '.join(followers)}];")
            namer_lines.append(
                "lookup m1_namer_dot_word_start {\n"
                f"    sub {dot_glyph}' @m1_namer_short_followers by {lowered_glyph};\n"
                "} m1_namer_dot_word_start;"
            )
    if namer_lines:
        parts.append("")
        parts.extend(namer_lines)

    calt_lookups = ["m1_formation", "m1_zwnj", "m1_settle"]
    if namer_lines:
        calt_lookups.append("m1_namer_dot_word_start")
    parts.append(
        "\nfeature calt {\n" + "\n".join(f"    lookup {name};" for name in calt_lookups) + "\n} calt;"
    )

    for feature in sorted(feature_lookup_names):
        parts.append(f"\nfeature {feature} {{\n    lookup {feature_lookup_names[feature]};\n}} {feature};")

    if glyphs and isolated_cells:
        overlay_lines: list[str] = []
        for cell, record in sorted(glyphs.items(), key=lambda item: item[1].name):
            if cell.rune not in isolated_cells:
                continue
            isolated_name = names_by_cell.get(isolated_cells[cell.rune])
            if isolated_name is None or record.name == isolated_name:
                continue
            overlay_lines.append(f"    sub {record.name} by {isolated_name};")
        if overlay_lines:
            parts.append(
                "\nfeature ss10 {\n    lookup m1_ss10_isolated {\n"
                + "\n".join("    " + line for line in overlay_lines)
                + "\n    } m1_ss10_isolated;\n} ss10;"
            )
    else:
        parts.append("\n# ss10 overlay skipped: no glyph inventory / isolated-cell map supplied.")

    fea = "\n".join(parts) + "\n"

    named_glyphs: set[str] = set(live_members) | set(locked_members) | set(marker_glyphs)
    named_glyphs.update(names_by_cell.values())
    named_glyphs.update(spec.runes)
    locked_set = frozenset(name for name in named_glyphs if ".noentry" in name) | frozenset(locked_members)
    _assert_invariants(rules, frozenset(named_glyphs), fea, locked_set)

    return GsubPlan(
        fea_text=fea,
        class_definitions=list(registry.definitions),
        rule_count=rule_count,
        marker_glyphs=marker_glyphs,
        locked_glyphs={locked_glyph_name(name): name for name in live_members},
        named_glyphs=frozenset(named_glyphs),
    )
