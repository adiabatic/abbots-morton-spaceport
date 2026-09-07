"""Post-compile GSUB surgery: repack the settlement lookup's per-rule format-3 chained-context subtables into shared-ClassDef format-2 subtables, because feaLib has no syntax or knob that emits chain-context format 2 and its per-rule format-3 fallback costs ten uint16-space bytes per rule — which pushed the flag-on simulated-prospect table (issue #28 stage 2) to 5,096 subtables and 12,783 bytes of subtable-offset headroom, under the 16,384-byte subtable-offset headroom floor read-back now holds the font to (`readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`), with a measured ceiling proving no liveness-filter tightening can recover it. Packing is the design's sanctioned shape: section 7 draws the soundness line at subtables, never per-family lookups — subtables share the one left-to-right pass, so backtrack still sees settled neighbors — and format 2 spends its ten bytes per *group* of class-compatible rules rather than per rule, which also gives the alphabet's remaining migrations their growth runway.

The pass is encoding-only by construction. It reads each qualifying lookup's format-3 subtables in order (each is one rule: coverage sets per slot plus SubstLookupRecords already pointing at feaLib's deduped inner lookups), groups consecutive-compatible rules with an order-preserving greedy — within a group every backtrack set must be equal-or-disjoint with every other (they share the group's backtrack ClassDef), likewise all lookahead sets against the single lookahead ClassDef and all input sets against the input ClassDef, and a rule may only land in a group at or after the last group any of its input glyphs used, so cross-subtable fallthrough preserves per-glyph first-match-wins — then replaces the lookup's SubTable array with one ChainContextSubst format 2 per group, reusing the original SubstLookupRecords verbatim and re-wrapping in Extension when the lookup rides type 7. A rule whose own slots defeat shared ClassDefs — the real table carries lookahead pairs like a {qsNo} singleton beside a broad class that also holds qsNo — is inexpressible in format 2 and passes through as its original format-3 subtable, a singleton group in sequence position (`_self_compatible`); the two formats mix freely inside one lookup. Rules never reference class 0 (the unclassed-glyph catch-all), every referenced glyph is explicitly classed, and rule order within a ChainSubClassSet is the original per-glyph order. A qualifying lookup is chained-context with every subtable format 3, a single input slot, and all substitutions at sequence index 0, at or above `min_subtables` — at M1 scale exactly `m1_settle`; the formation-guard lookup's multi-input forming rows disqualify it by shape.

Verification is read-back's, and empirical rather than trusted: `rebuild/pipeline/readback.py` decompiles the settlement lookup off the written font through `per_glyph_sequences` and holds every input glyph's ordered rule sequence to the plan's, so the packing is proven over the bytes that shipped rather than by replaying its own output in memory, and the compiled font then faces the same conform sweep as before — the packing changes what read-back measures, never what shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MIN_SUBTABLES = 64


class PackError(Exception):
    pass


@dataclass(frozen=True)
class LogicalRule:
    """One chained-context rule as slot glyph-sets: backtrack closest-first exactly as stored, one input set, lookahead near-to-far, and the substitution records as (sequence index, lookup index) pairs."""

    backtrack: tuple[frozenset[str], ...]
    input: frozenset[str]
    lookahead: tuple[frozenset[str], ...]
    records: tuple[tuple[int, int], ...]


def _inner_subtables(lookup: Any) -> list[Any]:
    if lookup.LookupType == 7:
        return [subtable.ExtSubTable for subtable in lookup.SubTable]
    return list(lookup.SubTable)


def _records_of(subtable: Any) -> tuple[tuple[int, int], ...]:
    return tuple(
        (record.SequenceIndex, record.LookupListIndex) for record in subtable.SubstLookupRecord or []
    )


def _format3_rule(subtable: Any) -> LogicalRule:
    return LogicalRule(
        backtrack=tuple(frozenset(coverage.glyphs) for coverage in subtable.BacktrackCoverage or []),
        input=frozenset(subtable.InputCoverage[0].glyphs),
        lookahead=tuple(frozenset(coverage.glyphs) for coverage in subtable.LookAheadCoverage or []),
        records=_records_of(subtable),
    )


def _class_sets(class_defs: dict[str, int]) -> dict[int, frozenset[str]]:
    by_class: dict[int, set[str]] = {}
    for glyph, klass in class_defs.items():
        by_class.setdefault(klass, set()).add(glyph)
    return {klass: frozenset(glyphs) for klass, glyphs in by_class.items()}


def _classed(sets: dict[int, frozenset[str]], klass: int, slot: str) -> frozenset[str]:
    """The glyphs a packed rule's class number stands for, refused as a `PackError` when the ClassDef classes no glyph under that number — a rule no glyph can ever satisfy, which read-back reports as a divergence rather than a traceback."""
    members = sets.get(klass)
    if members is None:
        raise PackError(f"packed rule references {slot} class {klass}, which classes no glyph")
    return members


def _format2_rules(subtable: Any) -> dict[str, list[LogicalRule]]:
    """The per-input-glyph logical rule sequences a format-2 subtable expresses, which is what read-back decompiles the packed lookup back through: rules of one ChainSubClassSet apply, in order, to every covered glyph of that input class."""
    backtrack_sets = _class_sets(subtable.BacktrackClassDef.classDefs)
    input_sets = _class_sets(subtable.InputClassDef.classDefs)
    lookahead_sets = _class_sets(subtable.LookAheadClassDef.classDefs)
    covered = set(subtable.Coverage.glyphs)
    out: dict[str, list[LogicalRule]] = {}
    for input_class, class_set in enumerate(subtable.ChainSubClassSet or []):
        if class_set is None:
            continue
        input_glyphs = input_sets.get(input_class, frozenset()) & covered
        if not input_glyphs:
            raise PackError(f"format-2 ChainSubClassSet {input_class} matches no covered glyph")
        for rule in class_set.ChainSubClassRule:
            if 0 in (rule.Backtrack or []) or 0 in (rule.LookAhead or []):
                raise PackError("packed rule references class 0")
            logical = LogicalRule(
                backtrack=tuple(
                    _classed(backtrack_sets, klass, "backtrack") for klass in rule.Backtrack or []
                ),
                input=frozenset(input_glyphs),
                lookahead=tuple(
                    _classed(lookahead_sets, klass, "lookahead") for klass in rule.LookAhead or []
                ),
                records=_records_of(rule),
            )
            for glyph in input_glyphs:
                out.setdefault(glyph, []).append(logical)
    return out


def per_glyph_sequences(lookup: Any) -> dict[str, list[LogicalRule]]:
    """The lookup's semantics at the grain that first-match-wins actually runs on: for each glyph that can sit at the input slot, the ordered rules that cover it. Rules whose input sets never share a glyph cannot compete, so this is the whole behavioral content of subtable and rule order."""
    out: dict[str, list[LogicalRule]] = {}
    for subtable in _inner_subtables(lookup):
        if subtable.Format == 3:
            rule = _format3_rule(subtable)
            for glyph in sorted(rule.input):
                out.setdefault(glyph, []).append(rule)
        elif subtable.Format == 2:
            for glyph, rules in _format2_rules(subtable).items():
                out.setdefault(glyph, []).extend(rules)
        else:
            raise PackError(f"unexpected chained-context subtable format {subtable.Format}")
    return out


def _qualifies(lookup: Any, min_subtables: int) -> bool:
    subtables = _inner_subtables(lookup)
    if len(subtables) < min_subtables:
        return False
    for subtable in subtables:
        if type(subtable).__name__ != "ChainContextSubst" or subtable.Format != 3:
            return False
        if len(subtable.InputCoverage or []) != 1:
            return False
        if any(record.SequenceIndex != 0 for record in subtable.SubstLookupRecord or []):
            return False
    return True


class _Group:
    __slots__ = ("backtrack_sets", "lookahead_sets", "input_sets", "rules")

    def __init__(self) -> None:
        self.backtrack_sets: dict[str, frozenset[str]] = {}
        self.lookahead_sets: dict[str, frozenset[str]] = {}
        self.input_sets: dict[str, frozenset[str]] = {}
        self.rules: list[LogicalRule] = []

    @staticmethod
    def _admits(owner: dict[str, frozenset[str]], sets: list[frozenset[str]]) -> bool:
        pending: dict[str, frozenset[str]] = {}
        for candidate in sets:
            for glyph in candidate:
                prior = owner.get(glyph, pending.get(glyph))
                if prior is not None and prior != candidate:
                    return False
                pending[glyph] = candidate
        return True

    def accepts(self, rule: LogicalRule) -> bool:
        return (
            self._admits(self.backtrack_sets, list(rule.backtrack))
            and self._admits(self.lookahead_sets, list(rule.lookahead))
            and self._admits(self.input_sets, [rule.input])
        )

    @staticmethod
    def _own(owner: dict[str, frozenset[str]], sets: list[frozenset[str]]) -> None:
        for candidate in sets:
            for glyph in candidate:
                owner[glyph] = candidate

    def add(self, rule: LogicalRule) -> None:
        self._own(self.backtrack_sets, list(rule.backtrack))
        self._own(self.lookahead_sets, list(rule.lookahead))
        self._own(self.input_sets, [rule.input])
        self.rules.append(rule)


def _self_compatible(rule: LogicalRule) -> bool:
    """Whether one rule's own slot sets can live in shared per-position ClassDefs at all: within each position kind, every pair of sets must be equal or disjoint. The real settlement table does carry violators — a rule whose second lookahead is a singleton and whose third is a broad class containing that same glyph — and such a rule is inexpressible in format 2, so it keeps its original format-3 subtable as a singleton group."""
    for sets in (rule.backtrack, rule.lookahead):
        owner: dict[str, frozenset[str]] = {}
        for candidate in sets:
            for glyph in candidate:
                prior = owner.get(glyph)
                if prior is not None and prior != candidate:
                    return False
                owner[glyph] = candidate
    return True


def _group_rules(entries: list[tuple[LogicalRule, Any]]) -> list[_Group | Any]:
    """Order-preserving greedy over (logical rule, original subtable) pairs: packable rules land in the earliest compatible `_Group` at or after their input glyphs' last group; self-incompatible rules become passthrough singletons holding their original subtable object."""
    groups: list[_Group | Any] = []
    last_group_of_glyph: dict[str, int] = {}
    for rule, original in entries:
        start = max((last_group_of_glyph.get(glyph, 0) for glyph in rule.input), default=0)
        if not _self_compatible(rule):
            placed_index = len(groups)
            groups.append(original)
        else:
            placed = None
            placed_index = len(groups)
            for index in range(start, len(groups)):
                candidate_group = groups[index]
                if isinstance(candidate_group, _Group) and candidate_group.accepts(rule):
                    placed = candidate_group
                    placed_index = index
                    break
            if placed is None:
                placed = _Group()
                groups.append(placed)
            placed.add(rule)
        for glyph in rule.input:
            last_group_of_glyph[glyph] = placed_index
    return groups


def _class_numbering(owner: dict[str, frozenset[str]], order: dict[str, int]) -> dict[frozenset[str], int]:
    distinct = sorted({candidate for candidate in owner.values()}, key=lambda s: min(order[g] for g in s))
    return {candidate: index + 1 for index, candidate in enumerate(distinct)}


def _ot() -> Any:
    from fontTools.ttLib.tables import otTables

    return otTables


def _format2_subtable(group: _Group, order: dict[str, int]) -> Any:
    ot = _ot()

    backtrack_classes = _class_numbering(group.backtrack_sets, order)
    lookahead_classes = _class_numbering(group.lookahead_sets, order)
    input_classes = _class_numbering(group.input_sets, order)

    subtable = ot.ChainContextSubst()
    subtable.Format = 2
    coverage = ot.Coverage()
    coverage.glyphs = sorted(
        {glyph for input_set in group.input_sets.values() for glyph in input_set}, key=order.__getitem__
    )
    subtable.Coverage = coverage
    for attribute, classes in (
        ("BacktrackClassDef", backtrack_classes),
        ("InputClassDef", input_classes),
        ("LookAheadClassDef", lookahead_classes),
    ):
        class_def = ot.ClassDef()
        class_def.classDefs = {glyph: index for candidate, index in classes.items() for glyph in candidate}
        setattr(subtable, attribute, class_def)

    rule_sets: dict[int, list[Any]] = {}
    for rule in group.rules:
        packed = ot.ChainSubClassRule()
        packed.Backtrack = [backtrack_classes[candidate] for candidate in rule.backtrack]
        packed.BacktrackGlyphCount = len(packed.Backtrack)
        packed.Input = []
        packed.InputGlyphCount = 1
        packed.LookAhead = [lookahead_classes[candidate] for candidate in rule.lookahead]
        packed.LookAheadGlyphCount = len(packed.LookAhead)
        packed.SubstLookupRecord = []
        for sequence_index, lookup_index in rule.records:
            record = ot.SubstLookupRecord()
            record.SequenceIndex = sequence_index
            record.LookupListIndex = lookup_index
            packed.SubstLookupRecord.append(record)
        packed.SubstCount = len(packed.SubstLookupRecord)
        rule_sets.setdefault(input_classes[rule.input], []).append(packed)

    class_sets: list[Any] = []
    for input_class in range(max(rule_sets) + 1):
        rules = rule_sets.get(input_class)
        if not rules:
            class_sets.append(None)
            continue
        class_set = ot.ChainSubClassSet()
        class_set.ChainSubClassRule = rules
        class_set.ChainSubClassRuleCount = len(rules)
        class_sets.append(class_set)
    subtable.ChainSubClassSet = class_sets
    subtable.ChainSubClassSetCount = len(class_sets)
    return subtable


def pack_lookup(lookup: Any, glyph_order: list[str]) -> tuple[int, int, int]:
    """Repack one qualifying lookup in place; returns (rule count, format-2 group count, kept format-3 count)."""
    ot = _ot()

    order = {glyph: index for index, glyph in enumerate(glyph_order)}
    inner = _inner_subtables(lookup)
    originals = list(lookup.SubTable)
    entries = [(_format3_rule(inner[index]), originals[index]) for index in range(len(inner))]
    groups = _group_rules(entries)
    packed_subtables = []
    kept = 0
    for group in groups:
        if not isinstance(group, _Group):
            kept += 1
            packed_subtables.append(group)
            continue
        subtable = _format2_subtable(group, order)
        if lookup.LookupType == 7:
            extension = ot.ExtensionSubst()
            extension.Format = 1
            extension.ExtensionLookupType = 6
            extension.ExtSubTable = subtable
            packed_subtables.append(extension)
        else:
            packed_subtables.append(subtable)
    lookup.SubTable = packed_subtables
    lookup.SubTableCount = len(packed_subtables)
    return len(entries), len(groups) - kept, kept


def pack_font(font: Any, min_subtables: int = MIN_SUBTABLES) -> dict:
    """Pack every qualifying chained-context lookup in the font's GSUB; returns the per-lookup packing stats — rules, format-2 groups, kept format-3 subtables — that the tests read."""
    packed: list[dict] = []
    if "GSUB" in font:
        lookups = font["GSUB"].table.LookupList.Lookup
        glyph_order = font.getGlyphOrder()
        for index, lookup in enumerate(lookups):
            if lookup.LookupType not in (6, 7):
                continue
            if lookup.LookupType == 7 and any(
                subtable.ExtensionLookupType != 6 for subtable in lookup.SubTable
            ):
                continue
            if not _qualifies(lookup, min_subtables):
                continue
            rule_count, group_count, kept_count = pack_lookup(lookup, glyph_order)
            packed.append(
                {
                    "lookup_index": index,
                    "rules": rule_count,
                    "format2_subtables": group_count,
                    "kept_format3": kept_count,
                }
            )
    return {"packed_lookups": packed}
