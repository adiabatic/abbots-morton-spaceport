"""Repack the settlement lookup's per-rule format-3 chained-context subtables into shared-ClassDef format-2 subtables after compilation.

FEA has no syntax that requests chain-context format 2. feaLib builds each ruleset (the rules between `subtable;` breaks) in every format it can and keeps the smallest. Format 2 is possible only when all the ruleset's class sets fit shared ClassDefs; when they do not, feaLib writes one format-3 subtable per rule. In the Extension-wrapped settlement lookup each subtable costs 10 bytes of uint16 offset space. Unpacked, the simulated-prospect table measured less subtable-offset headroom than the 16,384-byte floor that read-back enforces (`readback.SUBTABLE_OFFSET_HEADROOM_FLOOR`), and a measured ceiling showed that tightening the liveness filter could not recover the difference. Format 2 spends those 10 bytes once per group of class-compatible rules. Rules grouped as they come share a group only when their sets happen to be equal or disjoint, so the group count grows faster than the alphabet; the per-family refinement below makes every rule of a family compatible with every other, so the group count tracks the rules' bytes over `GROUP_RULE_BYTES_CAP` instead. Section 7 of `doc/rebuild-design.md` permits this split: the settlement lookup may be divided into subtables but not into per-family lookups, because subtables share the lookup's single left-to-right pass, so backtrack still sees settled neighbors.

The pass changes only the encoding. Each format-3 subtable is one rule: a coverage set per slot, plus SubstLookupRecords that point at the single-substitution lookups the emitter defines by name (`emit_gsub._settle_outcome_lookups`). The pass groups class-compatible rules and replaces each group with one ChainContextSubst format 2 subtable that reuses those SubstLookupRecords unchanged, wrapped in Extension when the lookup is type 7. Within a group, every backtrack set must be equal to or disjoint from every other, because they share the group's one backtrack ClassDef. The same holds for the lookahead sets and for the input sets. A rule may join only a group at or after the last group any of its input glyphs used, so each input glyph still meets its rules in their original order. Generated rules never reference class 0 (the class of unclassed glyphs), every referenced glyph is explicitly classed, and the rules in a ChainSubClassSet keep their original per-glyph order.

Before grouping, the pass refines each family's sets (`_refine`). A rule's family is its input glyph's name up to the first dot (`_family`), the key `emit_gsub` puts its `subtable;` breaks at, or the set of those names for a rule with several input glyphs, so a letter's plain, locked (`.noentry`) and marker inputs are one family. Within a family the backtrack sets of every rule, and separately its lookahead sets, are cut into atoms, the coarsest partition in which every set is a union of parts (`_atoms`), and each rule becomes the product of the atoms inside its slots, one piece per combination. Every piece of a family takes its classes from the same two partitions, so any two of them can share a group's ClassDefs; what leaves a group well short of the cap is a conflict with another family's pieces or the order an input glyph's stream must keep. Refinement also covers the rules whose own slot sets cannot share a ClassDef, which format 2 cannot write as they stand: in the real table one lookahead slot holds the singleton {qsNo} and another a broad class that also holds qsNo. It multiplies the rules the font holds (`readback_summary.json` reports `checked.settle_font_rules` beside the plan's `checked.settle_rules`) and grows the OTF with them, while the WOFF2 grows far less; HarfBuzz shapes random text faster than with rules grouped as they come, and the six-glyph contexts of the `settle:bk1-la4` rules somewhat slower (measured on the settlement lookup at 44 runes). Read as plain glyph positions, a rule's pieces are disjoint and together match exactly the contexts the rule matched. HarfBuzz does not read context slots as plain positions, though: it skips a ZWNJ (`uni200C`, the only default-ignorable glyph the font maps) that a backtrack or lookahead slot does not hold, and tries that slot on the glyph beyond it; the formation guard's ZWNJ-explicit rows and the settlement table's boundary rows are ordered for this (the ZWNJ row of section 7's table). So a piece that lacks `uni200C` in a slot where the rule holds it can skip a ZWNJ that the rule matches in that slot, read the later slots one glyph further on, and match a buffer the rule does not. In the farthest slot on a side the rule has already matched, so dropping `uni200C` there is harmless. A rule with a piece that would drop it from a nearer slot is not refined (`_keeps_zwnj`): it keeps its original format-3 subtable, placed in its input stream where a new group would go. Otherwise the pieces match exactly the buffers the rule matched, and since they share its outcome their order among themselves changes nothing; they take the rule's place in its input stream in product order, backtrack outermost, and join groups like any other rule. The refinement happens here, after the plan and never in `emit_gsub` or the crate, because the crate's fold (`fold::assert_outcome_partition`) and the witness stage fail the build on a rule that no window first-matches, and many pieces are reached by no string.

When every rule in a run has a single input glyph, the rules split into one stream per input glyph. Streams are processed largest first, with ties in first-seen order, and each stream keeps its rules in their original order. A new group or a kept format-3 subtable goes immediately after the stream's previous group, or at the beginning for the stream's first rule. This is the earliest legal position, and it leaves the groups after it free for the stream's later rules to share. A run that contains any multi-glyph input keeps its original order and appends new groups and kept subtables.

Native format-2 subtables from feaLib stay in place as barriers. Each contiguous run of format-3 subtables is refined and packed on its own, so no rule crosses a native subtable. A lookup qualifies when it is chained-context, has at least `min_subtables` subtables, all of format 2 or 3 and at least one of format 3, and every rule has one input slot and substitutes only at sequence index 0. At M1 scale only `m1_settle` qualifies; the guarded-formation lookup's multi-input forming rows disqualify it.

hb.repack cannot split a chained-context subtable, so the rule tables of one group must stay under the uint16 offsets inside it. A group keeps a running total of its rules' bytes and refuses a rule that would take it past `GROUP_RULE_BYTES_CAP`, which sends the rule to a later group or a new one, as a class conflict does. The cap sits under read-back's ceiling (`readback.GROUP_RULE_BYTES_CEILING`), so a group this pass writes never trips it, and each group it adds costs 10 bytes of the settlement lookup's subtable-offset headroom. Refined, the group count tracks the rules' bytes over the cap; at 44 runes this cap packs into fewer groups and a smaller OTF and WOFF2 than a 32,000-byte cap, and HarfBuzz shapes both alike. `largest_group` measures the written groups; `compile_font` stops a build whose group passes hb.repack's limit before the save, and read-back holds the settlement lookup's largest group to its ceiling.

Read-back checks the packing on the written font: `rebuild/pipeline/readback.py` decompiles the settlement lookup through `per_glyph_sequences` and reassembles each input glyph's planned rules from their pieces without calling this module's refinement. gate:conform then shapes the packed font like any other build.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import product
from typing import Any

MIN_SUBTABLES = 64
GROUP_RULE_BYTES_CAP = 48_000
ZWNJ = "uni200C"


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


def group_rule_bytes(subtable: Any) -> int:
    """Return the bytes of a format-2 chained-context subtable's ChainSubClassRule tables, from its decompiled or packed rules. A rule is four uint16 counts, a uint16 class per backtrack slot, per input slot after the first, and per lookahead slot, and a 4-byte SubstLookupRecord per substitution."""
    total = 0
    for class_set in subtable.ChainSubClassSet or []:
        if class_set is None:
            continue
        for rule in class_set.ChainSubClassRule:
            slots = len(rule.Backtrack or []) + len(rule.Input or []) + len(rule.LookAhead or [])
            total += 8 + 2 * slots + 4 * len(rule.SubstLookupRecord or [])
    return total


def _rule_bytes(rule: LogicalRule) -> int:
    """The bytes `group_rule_bytes` counts for this rule once it is written into a format-2 group, whose rules have one input slot."""
    return 8 + 2 * (len(rule.backtrack) + len(rule.lookahead)) + 4 * len(rule.records)


def largest_group(lookup: Any) -> tuple[int, int | None]:
    """Return the `group_rule_bytes` of the lookup's largest format-2 chained-context subtable and that subtable's index in the lookup, the first of equals, or (0, None) when the lookup holds none."""
    largest, largest_index = 0, None
    for index, subtable in enumerate(_inner_subtables(lookup)):
        if type(subtable).__name__ != "ChainContextSubst" or subtable.Format != 2:
            continue
        size = group_rule_bytes(subtable)
        if largest_index is None or size > largest:
            largest, largest_index = size, index
    return largest, largest_index


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
    __slots__ = ("backtrack_sets", "lookahead_sets", "input_sets", "rules", "rule_bytes")

    def __init__(self) -> None:
        self.backtrack_sets: dict[str, frozenset[str]] = {}
        self.lookahead_sets: dict[str, frozenset[str]] = {}
        self.input_sets: dict[str, frozenset[str]] = {}
        self.rules: list[LogicalRule] = []
        self.rule_bytes = 0

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
            self.rule_bytes + _rule_bytes(rule) <= GROUP_RULE_BYTES_CAP
            and self._admits(self.backtrack_sets, list(rule.backtrack))
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
        self.rule_bytes += _rule_bytes(rule)


def _family(glyph: str) -> str:
    """The input family a glyph belongs to: its name up to the first dot, the key `emit_gsub` breaks the settlement rules into subtables by, so a letter's plain, locked and marker inputs are one family."""
    return glyph.split(".")[0]


def _atoms(
    sets: Iterable[frozenset[str]], order: dict[str, int]
) -> dict[frozenset[str], tuple[frozenset[str], ...]]:
    """Map each of the sets to the atoms it holds, in glyph order. The atoms are the coarsest common refinement of the sets: two glyphs share an atom exactly when the same sets hold them, so every set is the union of the atoms it holds, and atoms never overlap."""
    distinct = list(dict.fromkeys(sets))
    holders: dict[str, list[int]] = {}
    for index, candidate in enumerate(distinct):
        for glyph in candidate:
            holders.setdefault(glyph, []).append(index)
    by_holders: dict[tuple[int, ...], list[str]] = {}
    for glyph, indices in holders.items():
        by_holders.setdefault(tuple(indices), []).append(glyph)
    held: list[list[frozenset[str]]] = [[] for _ in distinct]
    for indices, glyphs in by_holders.items():
        atom = frozenset(glyphs)
        for index in indices:
            held[index].append(atom)
    return {
        candidate: tuple(sorted(atoms, key=lambda atom: min(order[glyph] for glyph in atom)))
        for candidate, atoms in zip(distinct, held)
    }


def _keeps_zwnj(rule: LogicalRule, piece: LogicalRule) -> bool:
    """Whether the piece holds `ZWNJ` in every slot where the rule holds it, the farthest slot on each side aside. HarfBuzz skips a ZWNJ that a context slot does not hold and tries the slot on the glyph beyond it, so a piece without `ZWNJ` in a nearer slot can skip a ZWNJ that the rule matches there, read its later slots one glyph further on, and match a buffer the rule does not. Where the rule matches a ZWNJ in its farthest slot it has already matched, whatever the piece skips to."""
    return all(
        ZWNJ in part or ZWNJ not in whole
        for parts, wholes in ((piece.backtrack, rule.backtrack), (piece.lookahead, rule.lookahead))
        for part, whole in zip(parts[:-1], wholes[:-1])
    )


def _refine(rules: list[LogicalRule], order: dict[str, int]) -> list[list[LogicalRule] | None]:
    """Each rule's pieces over its family's atoms, in rule order. A rule's family is the set of its input glyphs' families (`_family`). Within one family the backtrack sets of every rule, and separately the lookahead sets, are refined into atoms (`_atoms`), and each rule becomes the product of the atoms inside its slots, backtrack outermost, with its input and records. Every piece of a family then takes its backtrack and lookahead classes from the same two partitions, so any of the family's pieces can share a group's ClassDefs with any other. None in place of a rule's pieces when a piece would lack `ZWNJ` in a slot short of the farthest on its side where the rule holds it (`_keeps_zwnj`), since that piece could match a buffer the rule does not; the caller keeps such a rule whole, in its original format-3 subtable."""
    members: dict[frozenset[str], list[LogicalRule]] = {}
    for rule in rules:
        members.setdefault(frozenset(_family(glyph) for glyph in rule.input), []).append(rule)
    atoms = {
        family: (
            _atoms((candidate for rule in family_rules for candidate in rule.backtrack), order),
            _atoms((candidate for rule in family_rules for candidate in rule.lookahead), order),
        )
        for family, family_rules in members.items()
    }
    refined: list[list[LogicalRule] | None] = []
    for rule in rules:
        backtrack_atoms, lookahead_atoms = atoms[frozenset(_family(glyph) for glyph in rule.input)]
        pieces = [
            LogicalRule(backtrack=backtrack, input=rule.input, lookahead=lookahead, records=rule.records)
            for backtrack in product(*(backtrack_atoms[candidate] for candidate in rule.backtrack))
            for lookahead in product(*(lookahead_atoms[candidate] for candidate in rule.lookahead))
        ]
        refined.append(pieces if all(_keeps_zwnj(rule, piece) for piece in pieces) else None)
    return refined


def _refined_entries(
    run: list[tuple[LogicalRule, Any]], order: dict[str, int]
) -> list[tuple[LogicalRule, Any]]:
    """The (rule, kept subtable) entries `_group_rules` takes for one run of format-3 rules, given as (rule, original subtable) pairs: each refined rule's pieces paired with None, or the rule paired with its original subtable when `_refine` keeps it whole."""
    entries: list[tuple[LogicalRule, Any]] = []
    for (rule, original), pieces in zip(run, _refine([rule for rule, _original in run], order)):
        if pieces is None:
            entries.append((rule, original))
        else:
            entries.extend((piece, None) for piece in pieces)
    return entries


def _group_rules(entries: list[tuple[LogicalRule, Any]]) -> list[_Group | Any]:
    """Group (rule, kept subtable) pairs greedily, preserving per-glyph rule order. A rule paired with None has slot sets that fit shared ClassDefs; a rule `_refine` keeps whole is paired with its original format-3 subtable, which goes where a new group for the rule would. Singleton-input streams run largest first, with ties in first-seen order and each stream in original rule order. Each rule joins the earliest group at or after its stream's last group that accepts it, or inserts a new group immediately after that position (at the beginning for a stream's first rule). A run containing any multi-glyph input keeps its original order and appends new groups and kept subtables, because input sets that intersect can compete."""
    groups: list[_Group | Any] = []
    if all(len(rule.input) == 1 for rule, _kept in entries):
        streams: dict[frozenset[str], list[tuple[LogicalRule, Any]]] = {}
        for entry in entries:
            streams.setdefault(entry[0].input, []).append(entry)
        for stream in sorted(streams.values(), key=len, reverse=True):
            last_index = -1
            for rule, kept in stream:
                if kept is not None:
                    last_index += 1
                    groups.insert(last_index, kept)
                    continue
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
        return groups
    last_group_of_glyph: dict[str, int] = {}
    for rule, kept in entries:
        start = max((last_group_of_glyph.get(glyph, 0) for glyph in rule.input), default=0)
        placed_index = len(groups)
        if kept is not None:
            groups.append(kept)
        else:
            placed_index = next(
                (
                    index
                    for index in range(start, len(groups))
                    if isinstance(groups[index], _Group) and groups[index].accepts(rule)
                ),
                len(groups),
            )
            if placed_index == len(groups):
                groups.append(_Group())
            groups[placed_index].add(rule)
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


def pack_lookup(lookup: Any, glyph_order: list[str]) -> tuple[int, int, int, int]:
    """Repack one qualifying lookup in place, leaving native format-2 subtables as barriers. Returns (rule count, rule count once refined rules count their pieces, format-2 subtable count including native ones, count of rules kept whole in format 3)."""
    ot = _ot()

    order = {glyph: index for index, glyph in enumerate(glyph_order)}
    inner = _inner_subtables(lookup)
    originals = list(lookup.SubTable)
    runs: list[list[tuple[LogicalRule, Any]]] = [[]]
    barriers: list[Any] = []
    rule_count = 0
    packed_rule_count = 0
    for subtable, original in zip(inner, originals):
        if subtable.Format == 3:
            runs[-1].append((_format3_rule(subtable), original))
            rule_count += 1
            continue
        barriers.append(original)
        runs.append([])
        native = sum(
            len(class_set.ChainSubClassRule)
            for class_set in subtable.ChainSubClassSet or []
            if class_set is not None
        )
        rule_count += native
        packed_rule_count += native
    groups: list[_Group | Any] = []
    kept_count = 0
    for index, run in enumerate(runs):
        entries = _refined_entries(run, order)
        packed_rule_count += len(entries)
        kept_count += sum(1 for _rule, kept in entries if kept is not None)
        groups.extend(_group_rules(entries))
        if index < len(barriers):
            groups.append(barriers[index])
    packed_subtables = []
    for group in groups:
        if not isinstance(group, _Group):
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
    return rule_count, packed_rule_count, len(groups) - kept_count, kept_count


def pack_font(font: Any, min_subtables: int = MIN_SUBTABLES) -> dict:
    """Pack every qualifying chained-context lookup in the font's GSUB in place. Returns, per packed lookup, its index, its rule count, its rule count once refined rules count their pieces, its format-2 subtable count, and its count of rules kept whole in format 3."""
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
            rule_count, packed_rule_count, group_count, kept_count = pack_lookup(lookup, glyph_order)
            packed.append(
                {
                    "lookup_index": index,
                    "rules": rule_count,
                    "packed_rules": packed_rule_count,
                    "format2_subtables": group_count,
                    "kept_format3": kept_count,
                }
            )
    return {"packed_lookups": packed}
