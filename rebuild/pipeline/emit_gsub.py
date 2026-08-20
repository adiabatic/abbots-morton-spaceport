"""GSUB emission in the prototype-proven section 7 shape (M1-PLAN section 5, Group 3).

Stage order, fixed by lookup definition order (which fixes LookupList indices and hence cross-feature application order on both shapers): the ss10 isolated-input pre-empt (single substitutions replacing every letter's raw cmap glyph by its anchor-free `.ss10` twin; defined first so that under ss10 it applies before formation can see the buffer — the twins appear in no formation sequence, marker line, chokepoint class, or settlement input, so under ss10 no ligature ever forms, nothing settles, and each letter keeps its own cluster) → formation (type-4 over the registry's ligature sequences; a ligature the section 5.7 late-formation guard ever blocks moves into its own chaining-context lookup `m1_formation_guarded`, staged first, whose generated `ignore sub` rows realize the guard over the two raw lookahead slots — with ZWNJ-explicit forming rows ordered ahead of them so a skipped ZWNJ can never satisfy a guard class, per the table builder's boundary-row discipline — and whose verdicts come straight from `settle.formation_blocked`, config-blind by that function's construction, so the pre-marker staging loses nothing) → ss marker substitutions (unconditional, per set, staged after formation so enabling a set cannot un-form a ligature; composite markers render multi-set union states) → the ZWNJ chokepoint (`sub uni200C @entry-live' by @entry-locked`) → ONE settlement lookup of chained-context single substitutions with per-family `subtable;` breaks, positive rules only, `useExtension` so its per-rule format-3 subtables ride 32-bit Extension offsets (the depth-4 rules pushed the uint16 subtable-offset headroom under the K2 floor; the budget gate yellow-flags the promotion by design) — then, post-settlement, the namer-dot mini-calt (supplied here because `_namer_dot_calt_fea` is a no-op on the `senior_fea` path; its follower class includes the ss10 twins of the Short letters so the dot still lowers under ss10).

Rule consumption is duck-typed against Group 2's `table.DecisionTable`: each rule exposes `input_glyph`, `backtrack` / `look1` / `look2` / `look3` / `look4` (tuples of glyph labels or None; `look3` and `look4` are read via getattr so pre-depth duck-typed tables keep working), `outcome`, `joint`, `provenance`. A rule with a live `look3` compiles to one further lookahead class after `look2` — the raw third slot a depth-3 prefer record reads — and a live `look4` to one more after that, the raw fourth slot a depth-4 record reads. When `tables_by_config` carries several configurations, their rule lists are folded by exact-duplicate union with a conflict assertion — sound exactly when the table builder already disambiguates inputs by marker labels per configuration (the prototype's feature-fold invariant); a same-window different-outcome collision raises.

Invariants asserted before returning: no locked twin and no chokepoint output appears in any raw lookahead class; every glyph named by any rule exists in the supplied glyph inventory; zero selection-semantics `ignore sub` (the namer-dot stage's guard and the generated late-formation guard rows are the sanctioned exemptions — both are formation/boundary machinery, not selection semantics).

Beside the FEA text the plan carries a structured mirror of every stage — the pre-empt map, the guarded formation rows and the plain ligatures, the per-feature marker substitutions, the settlement rows, the namer-dot row pair, and the calt lookup order — built at the same statement sites that append the lines, so line order and row order cannot drift. `rebuild/pipeline/readback.py` compares the compiled font against exactly that mirror, and `behavior_classes` enumerates the HarfBuzz-facing shapes it holds so the periodic deep sweep knows when a build has asked the shaper something new; nothing else reads it, and nothing in it is parsed back out of the emitted text.
"""

from __future__ import annotations

import dataclasses
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


BEHAVIOR_CLASSES_FORMAT = "ams-m1-behavior-classes/1"

_KNOWN_PLAN_FIELDS = frozenset(
    {
        "fea_text",
        "class_definitions",
        "rule_count",
        "marker_glyphs",
        "locked_glyphs",
        "named_glyphs",
        "ss10_preempt",
        "formation_guarded_rows",
        "formation_plain",
        "marker_lines",
        "settle_rules",
        "namer_dot_stage",
        "calt_stages",
    }
)
_CALT_STAGE_NAMES = frozenset(
    {"m1_formation_guarded", "m1_formation", "m1_zwnj", "m1_settle", "m1_namer_dot_word_start"}
)


@dataclass(frozen=True)
class FormationRow:
    """One emitted row of the guarded formation lookup as slot glyph-sets: the marked input sequence, the lookahead slots near-to-far (a literal glyph is a singleton set), and the rune the row forms — None for a guard's `ignore sub` row."""

    sequence: tuple[str, ...]
    lookahead: tuple[frozenset[str], ...]
    ligature: str | None


@dataclass(frozen=True)
class SettleRule:
    """One emitted settlement row as slot glyph-sets: the single input glyph, the backtrack class when the row carries one, the non-empty lookahead slots in emitted order, and the outcome."""

    input_glyph: str
    backtrack: frozenset[str] | None
    lookahead: tuple[frozenset[str], ...]
    outcome: str


@dataclass
class GsubPlan:
    fea_text: str
    class_definitions: list[str] = field(default_factory=list)
    rule_count: int = 0
    marker_glyphs: dict[str, str] = field(default_factory=dict)  # marker glyph -> base raw glyph
    locked_glyphs: dict[str, str] = field(default_factory=dict)  # locked twin -> raw glyph
    named_glyphs: frozenset[str] = frozenset()
    ss10_preempt: dict[str, str] = field(default_factory=dict)  # raw cmap glyph -> .ss10 twin
    formation_guarded_rows: tuple[FormationRow, ...] = ()
    formation_plain: tuple[tuple[tuple[str, ...], str], ...] = ()  # (components, ligature)
    marker_lines: dict[str, dict[str, str]] = field(default_factory=dict)  # feature -> {source: target}
    settle_rules: tuple[SettleRule, ...] = ()
    namer_dot_stage: tuple[str, str, frozenset[str]] | None = None  # (dot, lowered, followers)
    calt_stages: tuple[str, ...] = ()  # lookup names in calt definition order


def behavior_classes(plan: GsubPlan) -> tuple[str, ...]:
    """The deep sweep's arming enumeration: every HarfBuzz-facing shape the emitted lookup contains, stated as class tokens rather than as rules. A slot count, a guard arity, a ZWNJ in a backtrack, a locked input, the fall-through across per-family subtable breaks — each is a distinct way the shaper can be asked to behave, and two builds whose token sets agree ask nothing of HarfBuzz that the other did not. That is what lets a deep sweep's green survive a rune edit: the edit moves rules, but if it mints no new token it samples nothing the deep sweep has not already shaped.

    Fail-closed on the `classify_divergence` hard-None idiom, one level up: an unrecognized field on GsubPlan, a lookahead depth past the emitter's own ceiling, a guard arity nobody has emitted, a calt stage under an unknown name — each raises EmitError rather than passing silently, because a shape that enumerates to nothing would arm nothing and the deep sweep's green would quietly stop meaning what it says. The tokens name shapes, never contents: which rules exist and where they sit is read-back's claim (rebuild/pipeline/readback.py), re-proved on every build.
    """
    for candidate in dataclasses.fields(plan):
        if candidate.name not in _KNOWN_PLAN_FIELDS:
            raise EmitError(
                f"GsubPlan grew a field the behavior-class enumeration does not know: {candidate.name} — teach behavior_classes its shape so the deep sweep can arm on it"
            )
    tokens: set[str] = set()
    if plan.ss10_preempt:
        tokens.add("ss10-preempt")
    sequences = [sequence for sequence, _ligature in plan.formation_plain]
    sequences += [row.sequence for row in plan.formation_guarded_rows]
    for sequence in sequences:
        if len(sequence) < 2:
            raise EmitError(
                f"formation row with fewer than two components: {sequence} — teach behavior_classes what that shape asks of the shaper"
            )
        tokens.add(f"formation:{len(sequence)}")
    for row in plan.formation_guarded_rows:
        depth = len(row.lookahead)
        if row.ligature is None:
            if depth not in (1, 2):
                raise EmitError(
                    f"guard ignore row over {depth} lookahead slots: {row.sequence} — the guard emits one- and two-slot rows only"
                )
            tokens.add(f"guard-ignore:{depth}-slot")
        elif depth == 0:
            tokens.add("guard-form:fallback")
        elif depth == 1:
            if row.lookahead[0] != frozenset({"uni200C"}):
                raise EmitError(
                    f"one-slot forming row over {sorted(row.lookahead[0])}: {row.sequence} — the guard forms at one slot only against an explicit ZWNJ"
                )
            tokens.add("guard-form:zwnj")
        elif depth == 2:
            tokens.add("guard-form:2-slot")
        else:
            raise EmitError(
                f"forming row over {depth} lookahead slots: {row.sequence} — the guard emits at most two"
            )
    for feature in plan.marker_lines:
        tokens.add(f"marker-fold:{feature}")
    for rule in plan.settle_rules:
        depth = len(rule.lookahead)
        if depth > 4:
            raise EmitError(
                f"settlement rule over {depth} lookahead slots: {rule.input_glyph} — the window carries four"
            )
        tokens.add(f"settle:bk{1 if rule.backtrack is not None else 0}-la{depth}")
        if "uni200C" in (rule.backtrack or frozenset()):
            tokens.add("settle:zwnj-in-backtrack")
        if any("uni200C" in slot for slot in rule.lookahead):
            tokens.add("settle:zwnj-in-lookahead")
        if ".noentry" in rule.input_glyph:
            tokens.add("settle:locked-input")
    if len({rule.input_glyph.split(".")[0] for rule in plan.settle_rules}) > 1:
        tokens.add("settle:cross-subtable")
    if plan.namer_dot_stage is not None:
        tokens.add("namer-dot")
    for name in plan.calt_stages:
        if name not in _CALT_STAGE_NAMES:
            raise EmitError(
                f"calt stage under an unknown name: {name} — teach behavior_classes what that lookup asks of the shaper"
            )
    return tuple(sorted(tokens))


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


def _marker_lookups(
    spec: ResolvedSpec,
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, dict[str, str]]]:
    """Per stylistic set, the marker substitution lines; plus the marker-glyph registry and the same substitutions as source→target mappings for the read-back. The lookup for set F maps every union state over the sets emitted before F (and the bare rune) to the state plus F, so multi-set configurations compose in definition order."""
    per_feature: dict[str, list[str]] = {}
    per_feature_pairs: dict[str, dict[str, str]] = {}
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
            pairs = per_feature_pairs.setdefault(feature, {})
            for mask in range(1 << len(earlier)):
                state = frozenset(f for bit, f in enumerate(earlier) if mask & (1 << bit))
                source = marker_glyph_name(rune_name, state)
                target = marker_glyph_name(rune_name, state | {feature})
                lines.append(f"    sub {source} by {target};")
                pairs[source] = target
    return per_feature, marker_glyphs, per_feature_pairs


def _formation_lines(
    spec: ResolvedSpec, registry: _ClassRegistry
) -> tuple[list[str], list[str], list[str], list[FormationRow], list[tuple[tuple[str, ...], str]]]:
    """Formation lines split by the section 5.7 late-formation guard: (guarded chaining-context lookup lines, plain type-4 lookup lines, the generated `ignore sub` statements for the invariant exemption, the guarded rows as slot glyph-sets, the plain (components, ligature) pairs). Each structured row is appended beside the line it describes, so the read-back's expectation cannot drift from the emitted text. Guard verdicts come from `settle.formation_blocked` over the two raw slots past the sequence — the same function the kernel and the table builder consult, so model, table, and font agree by construction. A blocked follower whose second-slot verdicts cover every letter and every boundary gets a one-slot ignore; a follower blocked only under specific second slots gets a two-slot ignore over a letter class (a boundary or text-edge second slot then falls through to the forming fallback, matching its False verdict); a follower blocked at every boundary second slot but released under specific letter seconds inverts the discipline — explicit two-slot forming rows for the released letters, behind a ZWNJ-explicit two-slot ignore so a skipped ZWNJ cannot satisfy a released slot, ahead of a blanket one-slot ignore whose match-at-anything (text edge included) realizes the boundary blocks. A verdict that differs among the boundary second slots themselves remains inexpressible and errors. ZWNJ-explicit forming rows precede the ignores because HarfBuzz skips default-ignorables in contextual matching — without them a guard class could match across a skipped ZWNJ that the model treats as a boundary."""
    from rebuild.pipeline import settle as settle_module
    from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, ZWNJ, RightToken

    letters = sorted(name for name, rune in spec.runes.items() if not rune.sequence)
    boundary_tokens = (EDGE, SPACE, ZWNJ, NAMER_DOT)
    guarded_lines: list[str] = []
    plain_lines: list[str] = []
    ignores: list[str] = []
    guarded_rows: list[FormationRow] = []
    plain_pairs: list[tuple[tuple[str, ...], str]] = []
    for name, rune in spec.runes.items():
        if not rune.sequence:
            continue
        full_followers: list[str] = []
        partial_followers: list[tuple[str, tuple[str, ...]]] = []
        released_followers: list[tuple[str, tuple[str, ...]]] = []
        for follower in letters:
            follower_token = RightToken("letter", follower)
            blocked_letters = tuple(
                sorted(
                    second
                    for second in letters
                    if settle_module.formation_blocked(
                        spec, name, follower_token, RightToken("letter", second)
                    )
                )
            )
            blocked_boundaries = [
                settle_module.formation_blocked(spec, name, follower_token, boundary)
                for boundary in boundary_tokens
            ]
            if not blocked_letters and not any(blocked_boundaries):
                continue
            if len(blocked_letters) == len(letters) and all(blocked_boundaries):
                full_followers.append(follower)
            elif all(blocked_boundaries):
                released = tuple(sorted(set(letters) - set(blocked_letters)))
                released_followers.append((follower, released))
            elif any(blocked_boundaries):
                raise EmitError(
                    f"late-formation guard for {name} before {follower} blocks at some but not all boundary second slots — inexpressible in the pre-marker formation lookup"
                )
            else:
                partial_followers.append((follower, blocked_letters))
        if not full_followers and not partial_followers and not released_followers:
            plain_lines.append(f"    sub {' '.join(rune.sequence)} by {name};")
            plain_pairs.append((tuple(rune.sequence), name))
            continue
        sequence = tuple(rune.sequence)
        marked_input = " ".join(f"{part}'" for part in rune.sequence)
        guarded_lines.append(f"    sub {marked_input} uni200C by {name};")
        guarded_rows.append(FormationRow(sequence, (frozenset({"uni200C"}),), name))
        for follower, _blocked in partial_followers:
            guarded_lines.append(f"    sub {marked_input} {follower} uni200C by {name};")
            guarded_rows.append(FormationRow(sequence, (frozenset({follower}), frozenset({"uni200C"})), name))
        for follower, _released in released_followers:
            line = f"ignore sub {marked_input} {follower} uni200C;"
            guarded_lines.append(f"    {line}")
            guarded_rows.append(FormationRow(sequence, (frozenset({follower}), frozenset({"uni200C"})), None))
            ignores.append(line)
        for follower, released in released_followers:
            for second in released:
                guarded_lines.append(f"    sub {marked_input} {follower} {second} by {name};")
                guarded_rows.append(
                    FormationRow(sequence, (frozenset({follower}), frozenset({second})), name)
                )
        if full_followers:
            ref = registry.ref(tuple(full_followers), f"m1_form_guard_{_fea_safe(name)}")
            line = f"ignore sub {marked_input} {ref};"
            guarded_lines.append(f"    {line}")
            guarded_rows.append(FormationRow(sequence, (frozenset(full_followers),), None))
            ignores.append(line)
        for follower, blocked in partial_followers:
            ref = registry.ref(blocked, f"m1_form_guard_{_fea_safe(name)}_{_fea_safe(follower)}")
            line = f"ignore sub {marked_input} {follower} {ref};"
            guarded_lines.append(f"    {line}")
            guarded_rows.append(FormationRow(sequence, (frozenset({follower}), frozenset(blocked)), None))
            ignores.append(line)
        for follower, _released in released_followers:
            line = f"ignore sub {marked_input} {follower};"
            guarded_lines.append(f"    {line}")
            guarded_rows.append(FormationRow(sequence, (frozenset({follower}),), None))
            ignores.append(line)
        guarded_lines.append(f"    sub {marked_input} by {name};")
        guarded_rows.append(FormationRow(sequence, (), name))
    return guarded_lines, plain_lines, ignores, guarded_rows, plain_pairs


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
    look3: tuple[str, ...] | None
    look4: tuple[str, ...] | None
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

    def relabel(member: str) -> str:
        return renames.get(member, member)

    def slot(members: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if members is None:
            return None
        return tuple(relabel(member) for member in members)

    return _FoldedRule(
        input_glyph=relabel(rule.input_glyph),
        backtrack=slot(rule.backtrack),
        look1=slot(rule.look1),
        look2=slot(rule.look2),
        look3=slot(getattr(rule, "look3", None)),
        look4=slot(getattr(rule, "look4", None)),
        outcome=relabel(rule.outcome),
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
            key = (
                rule.input_glyph,
                rule.backtrack,
                rule.look1,
                rule.look2,
                getattr(rule, "look3", None),
                getattr(rule, "look4", None),
            )
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
) -> tuple[list[str], int, list]:
    """The settlement lines, their count, and the folded rules in the exact order the lines were emitted from — the order the read-back rebuilds its per-input-glyph expectations in."""

    def mentions_marker(rule) -> bool:
        return any(
            label in marker_names
            for slot in (rule.look1, rule.look2, getattr(rule, "look3", None), getattr(rule, "look4", None))
            for label in slot or ()
        )

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
        if getattr(rule, "look3", None):
            parts.append(registry.ref(tuple(rule.look3), f"s_{base}_la3_{index}"))
        if getattr(rule, "look4", None):
            parts.append(registry.ref(tuple(rule.look4), f"s_{base}_la4_{index}"))
        parts.append(f"by {rule.outcome};")
        provenance = "; ".join(dict.fromkeys(str(p) for p in (rule.provenance or ()) if p))
        comment_bits = [
            bit for bit in ("joint row" if getattr(rule, "joint", False) else "", provenance) if bit
        ]
        comment = f"  # {' | '.join(comment_bits)}" if comment_bits else ""
        lines.append("    " + " ".join(parts) + comment)
        count += 1
    return lines, count, grouped


def _assert_invariants(
    rules: Iterable,
    named_glyphs: frozenset[str],
    fea: str,
    locked: frozenset[str],
    allowed_ignores: frozenset[str] = frozenset(),
) -> None:
    for rule in rules:
        for slot in (rule.look1, rule.look2, getattr(rule, "look3", None), getattr(rule, "look4", None)):
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
        for slot in (
            rule.backtrack,
            rule.look1,
            rule.look2,
            getattr(rule, "look3", None),
            getattr(rule, "look4", None),
        ):
            for name in slot or ():
                if name not in named_glyphs and name not in ("uni200C", "space", "periodcentered"):
                    missing.add(name)
    if missing:
        raise EmitError(f"rules name glyphs outside the planned glyph set: {sorted(missing)}")
    selection_lines = [
        line
        for line in fea.split("\n")
        if "ignore sub" in line and line.split("#")[0].strip() not in allowed_ignores
    ]
    if selection_lines:
        raise EmitError(f"selection-semantics ignore sub leaked into the emitted FEA: {selection_lines[:3]}")


def emit_gsub(
    spec: ResolvedSpec,
    tables_by_config: Mapping,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    ss10_twins: Mapping[str, str] | None = None,
    namer_dot: tuple[str, str] | None = ("periodcentered", "periodcentered.lowered"),
) -> GsubPlan:
    """`glyphs` feeds the settlement outcomes and the namer-dot follower class; `ss10_twins` (raw cmap glyph name → anchor-free `.ss10` twin name) feeds the ss10 pre-empt lookup; each stage is skipped with a comment when its input is absent (a recorded M1-PLAN section 5 signature extension — the plan's two-argument form cannot reach the glyph inventory)."""
    registry = _ClassRegistry()
    rules = _fold_rules(tables_by_config, spec)
    per_feature_markers, marker_glyphs, marker_pairs = _marker_lookups(spec)
    marker_names = frozenset(marker_glyphs) | frozenset(locked_glyph_name(name) for name in marker_glyphs)
    formation_guarded, formation_plain, formation_ignores, guarded_rows, plain_pairs = _formation_lines(
        spec, registry
    )
    settle_lines, rule_count, grouped_rules = _settle_lines(rules, registry, marker_names)

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

    if ss10_twins:
        preempt_lines = [
            f"    sub {raw_name} by {twin_name};" for raw_name, twin_name in sorted(ss10_twins.items())
        ]
        parts.append(
            "lookup m1_ss10_isolated_input {\n" + "\n".join(preempt_lines) + "\n} m1_ss10_isolated_input;"
        )
        parts.append("")

    if formation_guarded:
        parts.append(
            "lookup m1_formation_guarded {\n" + "\n".join(formation_guarded) + "\n} m1_formation_guarded;"
        )
        parts.append("")
    if formation_plain or not formation_guarded:
        parts.append("lookup m1_formation {\n" + "\n".join(formation_plain) + "\n} m1_formation;")

    feature_lookup_names: dict[str, str] = {}
    for feature in sorted(per_feature_markers):
        lookup_name = f"m1_{feature}_marker"
        feature_lookup_names[feature] = lookup_name
        parts.append(
            f"\nlookup {lookup_name} {{\n" + "\n".join(per_feature_markers[feature]) + f"\n}} {lookup_name};"
        )

    parts.append("\nlookup m1_zwnj {\n    sub uni200C @m1_entry_live' by @m1_entry_locked;\n} m1_zwnj;")
    parts.append("\nlookup m1_settle useExtension {\n" + "\n".join(settle_lines) + "\n} m1_settle;")

    namer_lines: list[str] = []
    namer_dot_stage: tuple[str, str, frozenset[str]] | None = None
    if namer_dot is not None and names_by_cell:
        dot_glyph, lowered_glyph = namer_dot
        shorts = spec.registry.predicate_classes.get("shorts", frozenset())
        follower_names = {record_name for cell, record_name in names_by_cell.items() if cell.rune in shorts}
        if ss10_twins:
            follower_names.update(twin for raw_name, twin in ss10_twins.items() if raw_name in shorts)
        followers = sorted(follower_names)
        if followers:
            namer_dot_stage = (dot_glyph, lowered_glyph, frozenset(followers))
            namer_lines.append(f"@m1_namer_short_followers = [{' '.join(followers)}];")
            namer_lines.append(
                "lookup m1_namer_dot_word_start {\n"
                # HarfBuzz skips default-ignorables (ZWNJ) in contextual matching unless a rule names them, so without this ignore the dot would lower "through" a ZWNJ, breaking the ZWNJ-equals-word-boundary invariant (design section 3.4).
                f"    ignore sub {dot_glyph}' uni200C;\n"
                f"    sub {dot_glyph}' @m1_namer_short_followers by {lowered_glyph};\n"
                "} m1_namer_dot_word_start;"
            )
    if namer_lines:
        parts.append("")
        parts.extend(namer_lines)

    calt_lookups = []
    if formation_guarded:
        calt_lookups.append("m1_formation_guarded")
    if formation_plain or not formation_guarded:
        calt_lookups.append("m1_formation")
    calt_lookups.extend(["m1_zwnj", "m1_settle"])
    if namer_lines:
        calt_lookups.append("m1_namer_dot_word_start")
    parts.append(
        "\nfeature calt {\n" + "\n".join(f"    lookup {name};" for name in calt_lookups) + "\n} calt;"
    )

    for feature in sorted(feature_lookup_names):
        parts.append(f"\nfeature {feature} {{\n    lookup {feature_lookup_names[feature]};\n}} {feature};")

    if ss10_twins:
        parts.append("\nfeature ss10 {\n    lookup m1_ss10_isolated_input;\n} ss10;")
    else:
        parts.append("\n# ss10 pre-empt skipped: no ss10 twin inventory supplied.")

    fea = "\n".join(parts) + "\n"

    named_glyphs: set[str] = set(live_members) | set(locked_members) | set(marker_glyphs)
    named_glyphs.update(names_by_cell.values())
    named_glyphs.update(spec.runes)
    if ss10_twins:
        named_glyphs.update(ss10_twins.values())
    locked_set = frozenset(name for name in named_glyphs if ".noentry" in name) | frozenset(locked_members)
    allowed_ignores = (
        frozenset({f"ignore sub {namer_dot[0]}' uni200C;"}) if namer_dot is not None else frozenset()
    ) | frozenset(formation_ignores)
    _assert_invariants(rules, frozenset(named_glyphs), fea, locked_set, allowed_ignores)

    return GsubPlan(
        fea_text=fea,
        class_definitions=list(registry.definitions),
        rule_count=rule_count,
        marker_glyphs=marker_glyphs,
        locked_glyphs={locked_glyph_name(name): name for name in live_members},
        named_glyphs=frozenset(named_glyphs),
        ss10_preempt=dict(ss10_twins) if ss10_twins else {},
        formation_guarded_rows=tuple(guarded_rows),
        formation_plain=tuple(plain_pairs),
        marker_lines={feature: dict(pairs) for feature, pairs in marker_pairs.items()},
        settle_rules=tuple(
            SettleRule(
                input_glyph=rule.input_glyph,
                backtrack=frozenset(rule.backtrack) if rule.backtrack else None,
                lookahead=tuple(
                    frozenset(slot)
                    for slot in (
                        rule.look1,
                        rule.look2,
                        getattr(rule, "look3", None),
                        getattr(rule, "look4", None),
                    )
                    if slot
                ),
                outcome=rule.outcome,
            )
            for rule in grouped_rules
        ),
        namer_dot_stage=namer_dot_stage,
        calt_stages=tuple(calt_lookups),
    )
