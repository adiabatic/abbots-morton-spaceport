"""GSUB emission in the design section 7 shape (M1-PLAN section 5, Group 3).

Lookups are defined in the order they must apply, because definition order fixes their LookupList indices, and both HarfBuzz and CoreText apply lookups from different features in index order.

1. The ss10 isolated-input pre-empt: single substitutions from each letter's raw cmap glyph to its anchor-free `.ss10` twin. It comes first so that under ss10 it runs before formation. The twins appear in no formation sequence, marker line, chokepoint class, or settlement input, so under ss10 no ligature forms, nothing settles, and each letter keeps its own cluster.
2. Formation: a type-4 lookup over the registry's ligature sequences. A ligature that the design section 5.7 late-formation guard ever blocks moves into its own chaining-context lookup, `m1_formation_guarded`, which runs first. Its generated `ignore sub` rows implement the guard over the two raw lookahead slots. ZWNJ-explicit forming rows come before them, because HarfBuzz skips a ZWNJ in contextual matching and a guard class could otherwise match across one. The verdicts come from one `guard-sweep` call to the kernel crate, which does not depend on the configuration, so formation can run before the marker substitutions.
3. The stylistic-set marker substitutions: unconditional, one lookup per set, after formation so that turning on a set cannot undo a ligature. Composite markers represent several sets on at once.
4. The ZWNJ chokepoint: `sub uni200C @m1_entry_live' by @m1_entry_locked`.
5. One single-substitution lookup per distinct (input glyph, outcome) pair in the settlement rows, registered in no feature and referenced by name from those rows. feaLib resolves `lookup NAME` through a dict, while an inline `by` makes it rescan every rule since the last `subtable;` for a compatible inner lookup, which is quadratic in the rows per family.
6. One settlement lookup, `m1_settle`, of chained-context rows of the form `sub <backtrack> X' lookup NAME <lookahead>;`, with a `subtable;` break between input families and positive rules only. It is marked `useExtension` so that its per-rule format-3 subtables sit behind 32-bit Extension offsets. Without it, the depth-4 rules push the uint16 subtable-offset headroom below `readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`.
7. The namer-dot calt, after settlement. It is emitted here because `tools/build_font.py`'s own namer-dot calt emits nothing for the mini font: `compile_font` passes no context sets, so there is no `shorts` set. Its follower class includes the ss10 twins of the Short letters, so the dot still lowers under ss10.

The rules are read by duck typing against `table.Rule`: `input_glyph`, `backtrack`, `look1` to `look4` (tuples of glyph labels or None), `outcome`, `joint`, and `provenance`. `look3` and `look4` are read with `getattr`, so tables without them still work. A live `look3` compiles to a third lookahead class after `look2`, the raw third slot a depth-3 record reads, and a live `look4` to a fourth. The rule lists of all configurations in `tables_by_config` are folded into one by exact-duplicate union, after each configuration's raw labels are renamed to its marker twins (`model.raw_rename_map`). Two rules with the same window key and different outcomes raise EmitError.

Before returning, `emit_gsub` asserts that no locked twin or chokepoint output appears in a raw lookahead class, that every glyph a rule names is in the planned glyph set, that the only `ignore sub` rows are the namer-dot guard and the generated late-formation guard rows, and that every emitted row and every table rule are matched to each other (`_assert_fold_sources`).

Besides the FEA text, the plan holds a structured copy of every stage: the pre-empt map, the guarded formation rows and plain ligatures, the per-feature marker substitutions, the settlement rows, the namer-dot row pair, and the calt lookup order. Each structured row is recorded where its FEA line is appended, so the two are always in the same order. `rebuild/pipeline/readback.py` checks the compiled font against this copy, and `behavior_classes` summarizes the HarfBuzz-facing shapes in it for the deep sweep. Nothing is parsed back out of the FEA text.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from rebuild.pipeline import kernel_exec
from rebuild.pipeline.model import (
    CellId,
    GlyphRecord,
    ResolvedSpec,
    locked_glyph_name,
    marker_glyph_name,
    raw_rename_map,
    relevant_marker_features,
)
from rebuild.pipeline.settle import RightToken


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
    """One emitted row of the guarded formation lookup as glyph sets: the marked input sequence, the lookahead slots nearest first (a literal glyph is a one-member set), and the rune the row forms, or None for an `ignore sub` row."""

    sequence: tuple[str, ...]
    lookahead: tuple[frozenset[str], ...]
    ligature: str | None


@dataclass(frozen=True)
class SettleRule:
    """One emitted settlement row as glyph sets: the input glyph, the backtrack class if any, the non-empty lookahead slots in emitted order, and the outcome. `sources` lists the table rules folded into this row as `(configuration name, rule index in that configuration's table)` pairs, in fold order."""

    input_glyph: str
    backtrack: frozenset[str] | None
    lookahead: tuple[frozenset[str], ...]
    outcome: str
    sources: tuple[tuple[str, int], ...] = ()


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
    """The sorted class tokens for every HarfBuzz-facing shape in the plan, which arm the deep sweep. A token names a shape, such as a slot count, a guard arity, a ZWNJ in a backtrack, a locked input, or fall-through across per-family subtable breaks, and never a rule. When two builds produce the same tokens, neither asks HarfBuzz for a behavior the other did not, so a rune edit that adds no token leaves the deep sweep's green record valid. Which rules exist and where they sit is checked by read-back (rebuild/pipeline/readback.py) on every build.

    An unknown GsubPlan field, a lookahead depth over four, a guard row shape the emitter does not produce, or an unknown calt stage raises EmitError, because a shape that produces no token would never arm the deep sweep.
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
    """Each marker glyph name of the rune, mapped to the set of features it stands for: one entry per non-empty subset of `features`."""
    states: dict[str, frozenset[str]] = {}
    for mask in range(1, 1 << len(features)):
        active = frozenset(feature for index, feature in enumerate(features) if mask & (1 << index))
        states[marker_glyph_name(rune_name, active)] = active
    return states


def _marker_lookups(
    spec: ResolvedSpec,
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, dict[str, str]]]:
    """Return the marker substitution lines per stylistic set, the marker glyphs mapped to their runes, and the same substitutions as source-to-target maps for read-back. The lookup for set F maps the bare rune and every marker state over the sets before F to that state plus F, so several sets on at once compose in definition order."""
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


def _marker_names(spec: ResolvedSpec) -> frozenset[str]:
    """Every marker twin and its locked twin. A rule with one of these in a lookahead slot is sorted ahead of the bare-label rules that would otherwise match its windows first."""
    _per_feature, marker_glyphs, _pairs = _marker_lookups(spec)
    return frozenset(marker_glyphs) | frozenset(locked_glyph_name(name) for name in marker_glyphs)


def _formation_lines(
    spec: ResolvedSpec,
    registry: _ClassRegistry,
    guard_verdicts: Mapping[tuple[str, RightToken, RightToken], bool],
) -> tuple[list[str], list[str], list[str], list[FormationRow], list[tuple[tuple[str, ...], str]]]:
    """Formation lines split by the design section 5.7 late-formation guard. Returns the guarded chaining-context lookup lines, the plain type-4 lookup lines, the generated `ignore sub` statements (exempt from the no-`ignore sub` check), the guarded rows as glyph sets, and the plain (components, ligature) pairs. Each structured row is appended next to the line it describes.

    `guard_verdicts` is the crate's full `guard-sweep` result over the two raw slots after the sequence. For each follower of a ligature:

    - Blocked before every letter and every boundary: a one-slot `ignore sub` over a class of such followers.
    - Blocked only before some letters, and before no boundary: a two-slot `ignore sub` over a class of those letters. A boundary or text edge in the second slot falls through to the forming fallback, which matches its False verdict.
    - Blocked before every boundary but not before some letters: two-slot forming rows for the released letters, after a ZWNJ-explicit two-slot `ignore sub` and before a one-slot `ignore sub` that matches anything, text edge included.
    - Blocked before some boundaries but not others: EmitError, because the lookup cannot express it.

    ZWNJ-explicit forming rows come before the `ignore sub` rows because HarfBuzz skips default-ignorables in contextual matching. Without them a guard class could match across a ZWNJ that the model treats as a boundary.
    """
    from rebuild.pipeline.settle import EDGE, NAMER_DOT, SPACE, ZWNJ

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
                    if guard_verdicts[(name, follower_token, RightToken("letter", second))]
                )
            )
            blocked_boundaries = [
                guard_verdicts[(name, follower_token, boundary)] for boundary in boundary_tokens
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
    sources: tuple[tuple[str, int], ...] = ()


def _config_features(config) -> frozenset[str]:
    if isinstance(config, str):
        return frozenset(config.split("+")) - {"default"}
    return frozenset(config)


def _config_name(config) -> str:
    """The configuration's name as `conform.ACCEPTANCE_CONFIGS` writes it. A name passes through, the empty feature set is `default`, and any other set is its members sorted and joined with `+`."""
    if isinstance(config, str):
        return config
    features = sorted(config)
    return "+".join(features) if features else "default"


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


def _as_folded(rule, sources: tuple[tuple[str, int], ...]) -> _FoldedRule:
    def slot(members) -> tuple[str, ...] | None:
        return tuple(members) if members is not None else None

    return _FoldedRule(
        input_glyph=rule.input_glyph,
        backtrack=slot(rule.backtrack),
        look1=slot(rule.look1),
        look2=slot(rule.look2),
        look3=slot(getattr(rule, "look3", None)),
        look4=slot(getattr(rule, "look4", None)),
        outcome=rule.outcome,
        provenance=tuple(rule.provenance or ()),
        joint=bool(getattr(rule, "joint", False)),
        sources=sources,
    )


def _fold_rules(tables_by_config: Mapping, spec: ResolvedSpec | None = None) -> list[_FoldedRule]:
    """Fold the per-configuration tables into the one rule list the settlement lookup ships. Each row's `sources` names every table rule with the same window key, in fold order."""
    rules: list[_FoldedRule] = []
    positions: dict[tuple, int] = {}
    for config in sorted(tables_by_config, key=lambda c: sorted(_config_features(c))):
        table = tables_by_config[config]
        if isinstance(table, (tuple, list)):
            table = table[0]
        renames = raw_rename_map(spec, _config_features(config))
        for index, raw_rule in enumerate(getattr(table, "rules", ())):
            rule = _renamed(raw_rule, renames)
            key = (
                rule.input_glyph,
                rule.backtrack,
                rule.look1,
                rule.look2,
                getattr(rule, "look3", None),
                getattr(rule, "look4", None),
            )
            source = (_config_name(config), index)
            position = positions.get(key)
            if position is None:
                positions[key] = len(rules)
                rules.append(_as_folded(rule, (source,)))
                continue
            folded = rules[position]
            if folded.outcome != rule.outcome:
                raise EmitError(
                    f"feature fold conflict at {key}: {folded.outcome} vs {rule.outcome} — the marker encoding cannot express this"
                )
            rules[position] = dataclasses.replace(folded, sources=folded.sources + (source,))
    return rules


def _ordered_settle_rules(rules: Iterable, marker_names: frozenset[str] = frozenset()) -> list:
    """The folded rules in the order the settlement lines are emitted, which is the order read-back expects per input glyph and the order the shipped lookup matches in."""

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
        # The first matching rule wins, so across the configuration fold, rules with a backtrack (a committed left or a ZWNJ guard) stay ahead of rules that drop the left slot at a boundary. Within each of those two blocks, a rule whose lookahead names a marker twin goes ahead of bare-label rules that would otherwise match its windows through a dropped slot. This is safe because the marker substitution is unconditional, so a marker label and the bare label it replaces never occur in the same stream. The sort is stable, so each configuration's own order is kept.
        ordered = sorted(input_rules, key=lambda rule: (rule.backtrack is None, not mentions_marker(rule)))
        by_family.setdefault(input_glyph.split(".")[0], []).extend(ordered)
    return [rule for family_rules in by_family.values() for rule in family_rules]


def _settle_outcome_lookups(grouped: Iterable) -> tuple[dict[tuple[str, str], str], list[str]]:
    """One single-substitution lookup per distinct (input glyph, outcome) pair, in first-seen order so the FEA text is deterministic. Returns the lookup name per pair and the FEA blocks that define them. feaLib's `Builder.start_lookup_block` requires unique names, so the counter is keyed on the `_fea_safe` form of the glyph name, which can map two glyph names to one."""
    names: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    blocks: list[str] = []
    for rule in grouped:
        pair = (rule.input_glyph, rule.outcome)
        if pair in names:
            continue
        base = _fea_safe(rule.input_glyph)
        index = counters.get(base, 0)
        counters[base] = index + 1
        name = f"m1_settle_{base}_{index}"
        names[pair] = name
        blocks.append(f"lookup {name} {{\n    sub {rule.input_glyph} by {rule.outcome};\n}} {name};")
    return names, blocks


def _settle_lines(
    grouped: Iterable, registry: _ClassRegistry, outcome_lookups: Mapping[tuple[str, str], str]
) -> list[str]:
    """One FEA line per rule of an already ordered list, with a `subtable;` break wherever the input family changes. Each line names the lookup `outcome_lookups` holds for its (input glyph, outcome) pair, right after the marked input glyph as FEA requires."""
    lines: list[str] = []
    counters: dict[str, int] = {}
    current_family: str | None = None
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
        parts.append(f"lookup {outcome_lookups[(rule.input_glyph, rule.outcome)]}")
        if rule.look1:
            parts.append(registry.ref(tuple(rule.look1), f"s_{base}_la1_{index}"))
        if rule.look2:
            parts.append(registry.ref(tuple(rule.look2), f"s_{base}_la2_{index}"))
        if getattr(rule, "look3", None):
            parts.append(registry.ref(tuple(rule.look3), f"s_{base}_la3_{index}"))
        if getattr(rule, "look4", None):
            parts.append(registry.ref(tuple(rule.look4), f"s_{base}_la4_{index}"))
        provenance = "; ".join(dict.fromkeys(str(p) for p in (rule.provenance or ()) if p))
        comment_bits = [
            bit for bit in ("joint row" if getattr(rule, "joint", False) else "", provenance) if bit
        ]
        comment = f"  # {' | '.join(comment_bits)}" if comment_bits else ""
        lines.append("    " + " ".join(parts) + ";" + comment)
    return lines


def _settle_rule_of(rule) -> SettleRule:
    """One folded rule as the plan's structured row, with its slots as glyph sets and the sources the fold recorded."""
    return SettleRule(
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
        sources=tuple(getattr(rule, "sources", ())),
    )


def ordered_fold(spec: ResolvedSpec, tables_by_config: Mapping) -> list[_FoldedRule]:
    """The folded rules in the order the settlement lookup ships them, using the same fold, marker renaming, and ordering as `emit_gsub`, after `_assert_fold_sources`. It needs no glyph inventory and emits no FEA text."""
    grouped = _ordered_settle_rules(_fold_rules(tables_by_config, spec), _marker_names(spec))
    _assert_fold_sources(grouped, tables_by_config)
    return grouped


def fold_settle_rules(spec: ResolvedSpec, tables_by_config: Mapping) -> tuple[SettleRule, ...]:
    """`ordered_fold` as the plan's structured rows, each carrying the per-configuration table rules it folded from."""
    return tuple(_settle_rule_of(rule) for rule in ordered_fold(spec, tables_by_config))


EMITTED_ORDER_CONFIG = "emitted"


def _slot_text(members: tuple[str, ...] | None) -> str:
    return " ".join(members) if members else "-"


def emitted_order_tsv(spec: ResolvedSpec, tables_by_config: Mapping) -> str:
    """The shipped settlement order as a settlement TSV that the crate reads with `artifacts::read_settlement_tsv`. It has one line per emitted row in FEA order, with marker-renamed labels as they appear in the glyph stream. The provenance column lists the table rules the row was folded from as `<configuration>#<rule index>`, so an error can name them. The crate's `replay-emitted` subcommand checks each configuration's rows against this file (`run_m1.run_emitted_order`). The header names `EMITTED_ORDER_CONFIG` as its configuration because the order covers all of them."""
    lines = [
        f"# settlement table, config {EMITTED_ORDER_CONFIG}",
        "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance",
    ]
    for rule in ordered_fold(spec, tables_by_config):
        lines.append(
            "\t".join(
                (
                    rule.input_glyph,
                    _slot_text(rule.backtrack),
                    _slot_text(rule.look1),
                    _slot_text(rule.look2),
                    _slot_text(rule.look3),
                    _slot_text(rule.look4),
                    rule.outcome,
                    "joint" if rule.joint else "-",
                    "; ".join(f"{config}#{index}" for config, index in rule.sources),
                )
            )
        )
    return "\n".join(lines) + "\n"


def emitted_context_tsv(spec: ResolvedSpec, config, decision) -> str:
    """The label context of one configuration for the crate's `replay-emitted` subcommand (`shipped_order::read_context`). It has a `rename` record for each raw label that the marker renaming changes under this configuration (`model.raw_rename_map`, the map `_fold_rules` uses), and a `class` record for each deep class in the table's rows, with its members as raw labels."""
    lines = [
        f"rename\t{raw}\t{twin}"
        for raw, twin in sorted(raw_rename_map(spec, _config_features(config)).items())
    ]
    for token, members in sorted(getattr(decision, "deep_classes", {}).items()):
        lines.append(f"class\t{token}\t{' '.join(members)}")
    return "".join(f"{line}\n" for line in lines)


def _assert_fold_sources(rules: Iterable, tables_by_config: Mapping) -> None:
    """Check that every emitted row names at least one table rule it was folded from, that every table rule of every configuration is the source of exactly one row, and that the rows name exactly the configurations that have rules. A row with no source would ship a rule no table derived, and a table rule with no row would be missing from the font."""
    rules = list(rules)
    unsourced = [rule for rule in rules if not getattr(rule, "sources", ())]
    if unsourced:
        raise EmitError(
            f"{len(unsourced)} folded row(s) name no table rule they came from, starting at {unsourced[0].input_glyph} -> {unsourced[0].outcome}"
        )
    counts: dict[tuple[str, int], int] = {}
    for rule in rules:
        for source in rule.sources:
            counts[source] = counts.get(source, 0) + 1
    doubled = sorted(source for source, count in counts.items() if count != 1)
    if doubled:
        raise EmitError(f"{len(doubled)} table rule(s) source more than one folded row: {doubled[:5]}")
    contributing: list[str] = []
    for config in sorted(tables_by_config, key=lambda c: sorted(_config_features(c))):
        table = tables_by_config[config]
        if isinstance(table, (tuple, list)):
            table = table[0]
        name = _config_name(config)
        want = set(range(len(getattr(table, "rules", ()))))
        got = {index for source_name, index in counts if source_name == name}
        if got != want:
            raise EmitError(
                f"{name}: {len(want - got)} table rule(s) fold into no emitted row, {len(got - want)} recorded source(s) name no rule of the table"
            )
        if want:
            contributing.append(name)
    named = {source_name for rule in rules for source_name, _ in rule.sources}
    if named != set(contributing):
        raise EmitError(
            f"the folded rows name configurations {sorted(named)}, not the {contributing} the fold was handed"
        )


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
    """Emit the GSUB feature code and its structured plan. `glyphs` supplies the glyph names that the rules are checked against and the namer-dot follower class. Without it, the namer-dot stage is left out. `ss10_twins` maps raw cmap glyph names to their anchor-free `.ss10` twins for the ss10 pre-empt. Without it, the pre-empt is left out and the FEA says so in a comment."""
    registry = _ClassRegistry()
    rules = _fold_rules(tables_by_config, spec)
    per_feature_markers, marker_glyphs, marker_pairs = _marker_lookups(spec)
    marker_names = _marker_names(spec)
    guard_verdicts = kernel_exec.guard_sweep(spec)
    formation_guarded, formation_plain, formation_ignores, guarded_rows, plain_pairs = _formation_lines(
        spec, registry, guard_verdicts
    )
    grouped_rules = _ordered_settle_rules(rules, marker_names)
    outcome_lookups, outcome_blocks = _settle_outcome_lookups(grouped_rules)
    settle_lines = _settle_lines(grouped_rules, registry, outcome_lookups)
    rule_count = len(grouped_rules)

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
    if outcome_blocks:
        parts.append("\n" + "\n".join(outcome_blocks))
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
                # HarfBuzz skips default-ignorables such as ZWNJ in contextual matching unless a rule names them. Without this ignore the dot would lower across a ZWNJ, which must act as a word boundary (design section 3.4).
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
    _assert_fold_sources(grouped_rules, tables_by_config)
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
        settle_rules=tuple(_settle_rule_of(rule) for rule in grouped_rules),
        namer_dot_stage=namer_dot_stage,
        calt_stages=tuple(calt_lookups),
    )
