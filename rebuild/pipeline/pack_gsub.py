"""Repack the settlement lookup's per-rule format-3 chained-context subtables into shared-ClassDef format-2 subtables after compilation.

FEA has no syntax that requests chain-context format 2. feaLib builds each ruleset (the rules between `subtable;` breaks) in every format it can and keeps the smallest. Format 2 is possible only when all the ruleset's class sets fit shared ClassDefs; when they do not, feaLib writes one format-3 subtable per rule. In the Extension-wrapped settlement lookup each subtable costs 10 bytes of uint16 offset space. Unpacked, the simulated-prospect table measured 5,096 subtables and 12,783 bytes of subtable-offset headroom, below the 16,384-byte floor that read-back enforces (`readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`), and a measured ceiling showed that tightening the liveness filter could not recover the difference. Format 2 spends those 10 bytes once per group of class-compatible rules, which also leaves room for the letters not yet migrated. Section 7 of `doc/rebuild-design.md` permits this split: the settlement lookup may be divided into subtables but not into per-family lookups, because subtables share the lookup's single left-to-right pass, so backtrack still sees settled neighbors.

The pass changes only the encoding. Each format-3 subtable is one rule: a coverage set per slot, plus SubstLookupRecords that point at the single-substitution lookups the emitter defines by name, one per distinct (input glyph, outcome) pair. The pass groups class-compatible rules and replaces each group with one ChainContextSubst format 2 subtable that reuses those SubstLookupRecords unchanged, wrapped in Extension when the lookup is type 7. Within a group, every backtrack set must be equal to or disjoint from every other, because they share the group's one backtrack ClassDef. The same holds for the lookahead sets and for the input sets. A rule may join only a group at or after the last group any of its input glyphs used, so each input glyph still meets its rules in their original order. Generated rules never reference class 0 (the class of unclassed glyphs), every referenced glyph is explicitly classed, and the rules in a ChainSubClassSet keep their original per-glyph order.

A rule whose own slot sets cannot share ClassDefs cannot be written in format 2. The real table has such rules: one lookahead slot holds the singleton {qsNo} and another holds a broad class that also contains qsNo. Such a rule keeps its original format-3 subtable as a singleton group at its position in its input stream (`_self_compatible`). The two formats can mix inside one lookup.

When every rule in a run has a single input glyph, the rules split into one stream per input glyph. Streams are processed largest first, with ties in first-seen order, and each stream keeps its rules in their original order. A new group or format-3 passthrough goes immediately after the stream's previous group, or at the beginning for the stream's first rule. This is the earliest legal position, and it leaves the groups after it free for the stream's later rules to share. A run that contains any multi-glyph input keeps its original order and appends new groups.

Native format-2 subtables from feaLib stay in place as barriers. Each contiguous run of format-3 subtables packs on its own, so no rule crosses a native subtable. A lookup qualifies when it is chained-context, has at least `min_subtables` subtables, all of format 2 or 3 and at least one of format 3, and every rule has one input slot and substitutes only at sequence index 0. At M1 scale only `m1_settle` qualifies; the guarded-formation lookup's multi-input forming rows disqualify it.

Read-back checks the packing on the written font: `rebuild/pipeline/readback.py` decompiles the settlement lookup through `per_glyph_sequences` and compares each input glyph's ordered rules with the plan. gate:conform then shapes the packed font like any other build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MIN_SUBTABLES = 64


class PackError(Exception):
    pass


@dataclass(frozen=True)
class LogicalRule:
    """One chained-context rule as slot glyph-sets: backtrack closest-first as stored, one input set, lookahead near-to-far, and the substitution records as (sequence index, lookup index) pairs."""

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
    """Return the glyphs a packed rule's class number stands for. Raise `PackError` when the ClassDef assigns no glyph to that class, since no glyph could ever match the rule; read-back catches the error and reports it as a divergence."""
    members = sets.get(klass)
    if members is None:
        raise PackError(f"packed rule references {slot} class {klass}, which classes no glyph")
    return members


def _format2_rules(subtable: Any) -> dict[str, list[LogicalRule]]:
    """Return the ordered rules a format-2 subtable applies to each input glyph. The rules of one ChainSubClassSet apply, in order, to every covered glyph of that input class."""
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
    """Return, for each glyph that can occupy the input slot, the ordered rules that cover it. First-match-wins only orders rules that share an input glyph, so these sequences capture everything subtable and rule order decide."""
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
        if type(subtable).__name__ != "ChainContextSubst" or subtable.Format not in (2, 3):
            return False
        if subtable.Format == 2:
            for class_set in subtable.ChainSubClassSet or []:
                if class_set is None:
                    continue
                for rule in class_set.ChainSubClassRule:
                    if rule.InputGlyphCount != 1 or any(
                        record.SequenceIndex != 0 for record in rule.SubstLookupRecord or []
                    ):
                        return False
            continue
        if len(subtable.InputCoverage or []) != 1:
            return False
        if any(record.SequenceIndex != 0 for record in subtable.SubstLookupRecord or []):
            return False
    return any(subtable.Format == 3 for subtable in subtables)


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
    """Whether one rule's own slot sets fit shared ClassDefs: within the backtrack slots, and within the lookahead slots, every pair of sets must be equal or disjoint. The real settlement table has rules that fail this, such as one whose second lookahead is a singleton and whose third is a broad class holding the same glyph. Such a rule cannot be written in format 2, so it keeps its original format-3 subtable as a singleton group."""
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
    """Group (logical rule, original subtable) pairs greedily, preserving per-glyph rule order. Singleton-input streams run largest first, with ties in first-seen order and each stream in original rule order. Each rule joins the earliest compatible group at or after its stream's last group, or inserts a new group immediately after that position (at the beginning for a stream's first rule). A rule that fails `_self_compatible` inserts its original subtable at that same position. A run containing any multi-glyph input keeps its original order and appends new groups, because input sets that intersect can compete."""
    groups: list[_Group | Any] = []
    if all(len(rule.input) == 1 for rule, _original in entries):
        streams: dict[frozenset[str], list[tuple[LogicalRule, Any]]] = {}
        for entry in entries:
            streams.setdefault(entry[0].input, []).append(entry)
        for stream in sorted(streams.values(), key=len, reverse=True):
            last_index = -1
            for rule, original in stream:
                if _self_compatible(rule):
                    for index in range(max(last_index, 0), len(groups)):
                        candidate = groups[index]
                        if isinstance(candidate, _Group) and candidate.accepts(rule):
                            candidate.add(rule)
                            last_index = index
                            break
                    else:
                        group = _Group()
                        group.add(rule)
                        last_index += 1
                        groups.insert(last_index, group)
                else:
                    last_index += 1
                    groups.insert(last_index, original)
        return groups
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
    """Repack one qualifying lookup in place, leaving native format-2 subtables as barriers. Returns (rule count, format-2 subtable count including native ones, kept format-3 count)."""
    ot = _ot()

    order = {glyph: index for index, glyph in enumerate(glyph_order)}
    inner = _inner_subtables(lookup)
    originals = list(lookup.SubTable)
    entries: list[tuple[LogicalRule, Any]] = []
    groups: list[_Group | Any] = []
    rule_count = 0
    for subtable, original in zip(inner, originals):
        if subtable.Format == 3:
            entries.append((_format3_rule(subtable), original))
            rule_count += 1
        else:
            groups.extend(_group_rules(entries))
            entries.clear()
            groups.append(original)
            rule_count += sum(
                len(class_set.ChainSubClassRule)
                for class_set in subtable.ChainSubClassSet or []
                if class_set is not None
            )
    groups.extend(_group_rules(entries))
    packed_subtables = []
    kept = 0
    for group in groups:
        if not isinstance(group, _Group):
            inner_group = group.ExtSubTable if lookup.LookupType == 7 else group
            kept += inner_group.Format == 3
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
    return rule_count, len(groups) - kept, kept


def pack_font(font: Any, min_subtables: int = MIN_SUBTABLES) -> dict:
    """Pack every qualifying chained-context lookup in the font's GSUB in place. Returns, per packed lookup, its index and its counts of rules, format-2 subtables, and kept format-3 subtables."""
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
