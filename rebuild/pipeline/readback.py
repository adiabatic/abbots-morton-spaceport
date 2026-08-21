"""Read-back verification: the font that was just written, re-parsed from its own bytes and structurally proven against the plan the emitters held in memory (issue #73).

The build stage before this one hands feaLib a block of FEA text and gets an OTF back, and everything between those two — feaLib's parse, its lookup and subtable format choices, `pack_gsub`'s repack of the settlement lookup, fontTools' serialization, and the re-parse — is machinery no gate downstream reads structurally. `gate:conform` proves the font *shapes* what settlement says, through HarfBuzz, which is the behavioral claim and the one that matters; but it can only see what its sweep reaches, and it says nothing about a rule that is present and inert, a feature registered under the wrong tag, or a lookupFlag that skips a class nobody probed. This stage makes the transcription claim instead: every lookup's decompiled content equals what the emitter planned, every feature and script registration is the one the plan implies, the cross-feature LookupList order that pins application order on both shapers is the definition order the emitters chose, and every lookupFlag is zero. Zero divergences means the compiled font provably holds the rules the plan intended.

It is deliberately a transcription round-trip and nothing more — the `pack_gsub.pack_lookup` precedent, one stage further out. It predicts no cascade: it never asks what a buffer would do, never composes stages, never resolves which of two competing rules wins. Ordered rules are compared at the grain first-match-wins actually runs on (per input glyph for settlement, per lead glyph for formation), because rules that cannot share an input cannot compete and feaLib is free to regroup them — it picks whichever of the three chained-context subtable formats compiles smallest, so the guarded formation rides format 1 in a small font and format 3 in the shipped one, and the settlement lookup arrives packed into a format-2/format-3 mix. Shaping behavior stays gate:conform's.

The failure contract matches the budget gate's: `verify_font` never raises for a divergence, it accumulates human-readable strings and reports `pass`; `run_m1` writes the whole report to `readback_summary.json` and only then raises `ReadbackError`, so the evidence outlives the failure. Beside that summary `run_m1` also records the emitted settlement fold (`settle-fold.ndjson`), divergence or not, so the witness gate can enumerate its coverage over the very rows this stage held the font to.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from rebuild.pipeline import pack_gsub
from rebuild.pipeline.emit_gpos import CURS_HEIGHT_YS, Anchor, Registration
from rebuild.pipeline.emit_gsub import GsubPlan, SettleRule

MAX_DIVERGENCES = 50
NO_REQUIRED_FEATURE = 0xFFFF
SETTLE_FOLD_FORMAT = "ams-m1-settle-fold/1"
SETTLE_FOLD_FILENAME = "settle-fold.ndjson"

Row = tuple[tuple[str, ...], tuple[frozenset[str], ...], str | None]


class ReadbackError(Exception):
    pass


@dataclass(frozen=True)
class _ChainRow:
    """One chained-context rule decompiled to slot glyph-sets, whichever subtable format carried it: backtrack closest-first as stored, the input slots in order, lookahead near-to-far, and the substitutions as (sequence index, lookup index) pairs."""

    backtrack: tuple[frozenset[str], ...]
    input: tuple[frozenset[str], ...]
    lookahead: tuple[frozenset[str], ...]
    records: tuple[tuple[int, int], ...]


def _unwrapped(lookup: Any) -> list[Any]:
    """The lookup's subtables with any Extension wrapper (GSUB type 7, GPOS type 9) removed."""
    return [getattr(subtable, "ExtSubTable", subtable) for subtable in lookup.SubTable or []]


def _records_of(rule: Any) -> tuple[tuple[int, int], ...]:
    return tuple((record.SequenceIndex, record.LookupListIndex) for record in rule.SubstLookupRecord or [])


def _class_sets(class_defs: Mapping[str, int]) -> dict[int, frozenset[str]]:
    by_class: dict[int, set[str]] = {}
    for glyph, klass in class_defs.items():
        by_class.setdefault(klass, set()).add(glyph)
    return {klass: frozenset(glyphs) for klass, glyphs in by_class.items()}


def _lookup_at(lookups: list[Any], index: int) -> Any:
    return lookups[index] if 0 <= index < len(lookups) else None


def _stage_lookup(lookups: list[Any], index: int, stage: str, divergences: list[str]) -> Any:
    lookup = _lookup_at(lookups, index)
    if lookup is None:
        divergences.append(f"{stage}: registered as lookup {index}, which the lookup list does not hold")
    return lookup


def _single_mapping(lookup: Any) -> dict[str, str] | None:
    """The lookup's whole single-substitution mapping, or None when it is not one."""
    mapping: dict[str, str] = {}
    subtables = _unwrapped(lookup)
    if not subtables:
        return None
    for subtable in subtables:
        if type(subtable).__name__ != "SingleSubst":
            return None
        mapping.update(subtable.mapping)
    return mapping


def _ligature_map(lookup: Any) -> dict[tuple[str, ...], str] | None:
    """The lookup's ligatures keyed by their whole input sequence, or None when it is not a ligature lookup."""
    formed: dict[tuple[str, ...], str] = {}
    subtables = _unwrapped(lookup)
    if not subtables:
        return None
    for subtable in subtables:
        if type(subtable).__name__ != "LigatureSubst":
            return None
        for first, ligatures in subtable.ligatures.items():
            for ligature in ligatures:
                formed[(first, *ligature.Component)] = ligature.LigGlyph
    return formed


def _chain_rows(lookup: Any) -> tuple[list[_ChainRow], list[str]]:
    """Every chained-context rule the lookup expresses, in subtable order, across all three subtable formats — feaLib compiles each ruleset in whichever format is smallest, so a stage's shape on disk is not the shape its FEA was written in."""
    rows: list[_ChainRow] = []
    problems: list[str] = []
    for index, subtable in enumerate(_unwrapped(lookup)):
        subtable_format = getattr(subtable, "Format", None)
        if subtable_format == 3:
            rows.append(
                _ChainRow(
                    backtrack=tuple(frozenset(c.glyphs) for c in subtable.BacktrackCoverage or []),
                    input=tuple(frozenset(c.glyphs) for c in subtable.InputCoverage or []),
                    lookahead=tuple(frozenset(c.glyphs) for c in subtable.LookAheadCoverage or []),
                    records=_records_of(subtable),
                )
            )
        elif subtable_format == 1:
            coverage = subtable.Coverage.glyphs
            for position, rule_set in enumerate(subtable.ChainSubRuleSet or []):
                if rule_set is None:
                    continue
                lead = frozenset({coverage[position]})
                for rule in rule_set.ChainSubRule:
                    rows.append(
                        _ChainRow(
                            backtrack=tuple(frozenset({glyph}) for glyph in rule.Backtrack or []),
                            input=(lead,) + tuple(frozenset({glyph}) for glyph in rule.Input or []),
                            lookahead=tuple(frozenset({glyph}) for glyph in rule.LookAhead or []),
                            records=_records_of(rule),
                        )
                    )
        elif subtable_format == 2:
            covered = frozenset(subtable.Coverage.glyphs)
            backtrack_sets = _class_sets(subtable.BacktrackClassDef.classDefs)
            input_sets = _class_sets(subtable.InputClassDef.classDefs)
            lookahead_sets = _class_sets(subtable.LookAheadClassDef.classDefs)
            for input_class, class_set in enumerate(subtable.ChainSubClassSet or []):
                if class_set is None:
                    continue
                lead = input_sets.get(input_class, frozenset()) & covered
                for rule in class_set.ChainSubClassRule:
                    rows.append(
                        _ChainRow(
                            backtrack=tuple(
                                backtrack_sets.get(klass, frozenset()) for klass in rule.Backtrack or []
                            ),
                            input=(lead,)
                            + tuple(input_sets.get(klass, frozenset()) for klass in rule.Input or []),
                            lookahead=tuple(
                                lookahead_sets.get(klass, frozenset()) for klass in rule.LookAhead or []
                            ),
                            records=_records_of(rule),
                        )
                    )
        else:
            problems.append(
                f"subtable {index} is {type(subtable).__name__} format {subtable_format}, which no stage emits"
            )
    return rows, problems


def _slots_text(slots: tuple[frozenset[str], ...]) -> str:
    return "[" + ", ".join("{" + " ".join(sorted(slot)) + "}" for slot in slots) + "]"


def _row_text(row: Row) -> str:
    return f"{' '.join(row[0])} before {_slots_text(row[1])} -> {row[2]}"


def _compare_rows(stage: str, expected: list[Row], got: list[Row], divergences: list[str]) -> None:
    """Hold the two ordered row lists to the grain first-match-wins runs on: rows sharing a lead glyph must agree in order, rows that cannot share one cannot compete — which is also exactly what survives feaLib's format-1 regrouping of rules by coverage glyph."""
    by_lead_expected: dict[str, list[Row]] = {}
    for row in expected:
        by_lead_expected.setdefault(row[0][0], []).append(row)
    by_lead_got: dict[str, list[Row]] = {}
    for row in got:
        by_lead_got.setdefault(row[0][0], []).append(row)
    for lead in sorted(set(by_lead_expected) | set(by_lead_got)):
        want = by_lead_expected.get(lead, [])
        have = by_lead_got.get(lead, [])
        if len(want) != len(have):
            divergences.append(f"{stage}: {lead} carries {len(have)} rows, expected {len(want)}")
            continue
        for index, (one, other) in enumerate(zip(want, have)):
            if one != other:
                divergences.append(
                    f"{stage}: {lead} row {index} is {_row_text(other)}, expected {_row_text(one)}"
                )


def _check_script_list(table: Any, label: str, divergences: list[str]) -> None:
    """The one-script registration every M1 build compiles to, there being no `languagesystem` statement anywhere: DFLT with a DefaultLangSys, no language systems of its own, no required feature, and every feature in the list registered on it."""
    records = list(table.ScriptList.ScriptRecord or [])
    if len(records) != 1 or records[0].ScriptTag != "DFLT":
        divergences.append(
            f"script list ({label}): scripts are {[record.ScriptTag for record in records]}, expected exactly DFLT"
        )
        return
    script = records[0].Script
    if script.DefaultLangSys is None:
        divergences.append(f"script list ({label}): DFLT carries no DefaultLangSys")
        return
    if script.LangSysCount:
        divergences.append(
            f"script list ({label}): DFLT carries {script.LangSysCount} language systems, expected none"
        )
    if script.DefaultLangSys.ReqFeatureIndex != NO_REQUIRED_FEATURE:
        divergences.append(
            f"script list ({label}): ReqFeatureIndex is {script.DefaultLangSys.ReqFeatureIndex}, expected 0xFFFF"
        )
    registered = set(script.DefaultLangSys.FeatureIndex or [])
    listed = set(range(len(table.FeatureList.FeatureRecord or [])))
    if registered != listed:
        divergences.append(
            f"script list ({label}): DefaultLangSys registers features {sorted(registered)}, but the feature list holds {sorted(listed)}"
        )


def _check_lookup_flags(table: Any, label: str, divergences: list[str]) -> int:
    """Every lookup in the table, the anonymous inner ones included, must carry a zero flag and no mark filtering set — nothing the emitters write asks for either, so a nonzero flag is a rule silently skipping glyphs."""
    lookups = list(table.LookupList.Lookup or [])
    for index, lookup in enumerate(lookups):
        if lookup.LookupFlag:
            divergences.append(
                f"lookupFlag: {label} lookup {index} carries LookupFlag {lookup.LookupFlag}, expected 0"
            )
        if getattr(lookup, "MarkFilteringSet", None):
            divergences.append(
                f"lookupFlag: {label} lookup {index} carries MarkFilteringSet {lookup.MarkFilteringSet}, expected none"
            )
    return len(lookups)


def _feature_indices(table: Any) -> dict[str, list[int]]:
    indices: dict[str, list[int]] = {}
    for record in table.FeatureList.FeatureRecord or []:
        indices.setdefault(record.FeatureTag, []).extend(record.Feature.LookupListIndex or [])
    return indices


def _check_feature_list(plan: GsubPlan, gsub: Any, divergences: list[str]) -> dict[str, list[int]] | None:
    """The GSUB feature registration the plan implies: calt always, one stylistic-set feature per marker lookup, ss10 exactly when the pre-empt stage is live — and feaLib sorts the records by tag, which is what pins each set's own lookup ahead of nothing and behind everything."""
    tags = [record.FeatureTag for record in gsub.FeatureList.FeatureRecord or []]
    expected = sorted(["calt", *plan.marker_lines] + (["ss10"] if plan.ss10_preempt else []))
    if sorted(tags) != expected:
        divergences.append(f"feature list: GSUB registers {sorted(tags)}, expected {expected}")
        return None
    if tags != sorted(tags):
        divergences.append(f"feature list: GSUB feature records are ordered {tags}, expected them sorted")
    indices = _feature_indices(gsub)
    if len(indices["calt"]) != len(plan.calt_stages):
        divergences.append(
            f"calt registration: calt carries {len(indices['calt'])} lookups {indices['calt']}, expected {len(plan.calt_stages)} for stages {list(plan.calt_stages)}"
        )
        return None
    for tag in expected:
        if tag != "calt" and len(indices[tag]) != 1:
            divergences.append(f"feature list: {tag} carries lookups {indices[tag]}, expected exactly one")
            return None
    return indices


def _check_definition_order(
    plan: GsubPlan, indices: dict[str, list[int]], divergences: list[str]
) -> dict[str, int]:
    """Every stage's LookupList index, and the proof that they run in the order the emitters defined them: the pre-empt first so ss10 beats formation to the buffer, the formation stages next, the marker substitutions after them so enabling a set cannot un-form a ligature, then the chokepoint, settlement, and the namer dot. Application order on both shapers is LookupList order, so this chain is the whole staging claim."""
    stages = dict(zip(plan.calt_stages, indices["calt"]))
    chain: list[tuple[str, int]] = []
    if plan.ss10_preempt:
        chain.append(("m1_ss10_isolated_input", indices["ss10"][0]))
    for name in plan.calt_stages:
        if name == "m1_zwnj":
            for feature in sorted(plan.marker_lines):
                chain.append((f"m1_{feature}_marker", indices[feature][0]))
        chain.append((name, stages[name]))
    for (earlier, earlier_index), (later, later_index) in zip(chain, chain[1:]):
        if earlier_index >= later_index:
            divergences.append(
                f"lookup order: {earlier} is lookup {earlier_index} but {later} is lookup {later_index}, so the stages do not run in definition order"
            )
    return stages


def _check_single_stage(stage: str, lookup: Any, expected: Mapping[str, str], divergences: list[str]) -> int:
    mapping = _single_mapping(lookup)
    if mapping is None:
        divergences.append(f"{stage}: the lookup holds no single substitutions")
        return 0
    if mapping != dict(expected):
        missing = sorted(set(expected) - set(mapping))
        extra = sorted(set(mapping) - set(expected))
        wrong = sorted(
            f"{glyph} -> {mapping[glyph]} (expected {expected[glyph]})"
            for glyph in set(mapping) & set(expected)
            if mapping[glyph] != expected[glyph]
        )
        divergences.append(
            f"{stage}: {len(mapping)} substitutions, expected {len(expected)}; missing {missing[:5]}, unplanned {extra[:5]}, retargeted {wrong[:5]}"
        )
    return len(mapping)


def _check_guarded_formation(plan: GsubPlan, lookup: Any, lookups: list[Any], divergences: list[str]) -> int:
    """The late-formation guard's rows as the font holds them: literal input slots, no backtrack, and either no substitution (an `ignore sub` guard row) or one at sequence index 0 resolving through the anonymous ligature lookup feaLib deduped the forming rows into."""
    stage = "formation guarded"
    rows, problems = _chain_rows(lookup)
    for problem in problems:
        divergences.append(f"{stage}: {problem}")
    got: list[Row] = []
    for index, row in enumerate(rows):
        if row.backtrack:
            divergences.append(f"{stage}: row {index} carries a backtrack slot, which no formation row emits")
            continue
        sequence = tuple(next(iter(slot)) for slot in row.input if len(slot) == 1)
        if not sequence or len(sequence) != len(row.input):
            divergences.append(
                f"{stage}: row {index} has a non-singleton input slot {_slots_text(row.input)}"
            )
            continue
        ligature: str | None = None
        if row.records:
            if len(row.records) != 1 or row.records[0][0] != 0:
                divergences.append(
                    f"{stage}: row {index} ({' '.join(sequence)}) carries substitutions {list(row.records)}, expected one at sequence index 0"
                )
                continue
            inner = _lookup_at(lookups, row.records[0][1])
            formed = None if inner is None else _ligature_map(inner)
            if formed is None or sequence not in formed:
                divergences.append(
                    f"{stage}: row {index} ({' '.join(sequence)}) resolves through lookup {row.records[0][1]}, which forms no ligature for that sequence"
                )
                continue
            ligature = formed[sequence]
        got.append((sequence, row.lookahead, ligature))
    expected: list[Row] = [
        (tuple(row.sequence), row.lookahead, row.ligature) for row in plan.formation_guarded_rows
    ]
    _compare_rows(stage, expected, got, divergences)
    return len(got)


def _check_plain_formation(plan: GsubPlan, lookup: Any, divergences: list[str]) -> int:
    stage = "formation plain"
    formed = _ligature_map(lookup)
    if formed is None:
        divergences.append(f"{stage}: the lookup holds no ligature substitutions")
        return 0
    expected: dict[tuple[str, ...], str] = {}
    for sequence, name in plan.formation_plain:
        if sequence in expected:
            divergences.append(f"{stage}: the plan forms {' '.join(sequence)} twice")
        expected[sequence] = name
    if formed != expected:
        missing = sorted(" ".join(sequence) for sequence in set(expected) - set(formed))
        extra = sorted(" ".join(sequence) for sequence in set(formed) - set(expected))
        wrong = sorted(
            f"{' '.join(sequence)} -> {formed[sequence]} (expected {expected[sequence]})"
            for sequence in set(formed) & set(expected)
            if formed[sequence] != expected[sequence]
        )
        divergences.append(
            f"{stage}: {len(formed)} ligatures, expected {len(expected)}; missing {missing[:5]}, unplanned {extra[:5]}, retargeted {wrong[:5]}"
        )
    return len(formed)


def _check_chokepoint(plan: GsubPlan, lookup: Any, lookups: list[Any], divergences: list[str]) -> int:
    """The ZWNJ chokepoint: one row that matches every entry-live raw glyph behind a ZWNJ and substitutes its locked twin, so nothing downstream of a word boundary can join leftward."""
    stage = "zwnj chokepoint"
    rows, problems = _chain_rows(lookup)
    for problem in problems:
        divergences.append(f"{stage}: {problem}")
    if len(rows) != 1:
        divergences.append(f"{stage}: {len(rows)} rows, expected exactly one")
        return 0
    row = rows[0]
    expected_input = frozenset(plan.locked_glyphs.values())
    if row.backtrack != (frozenset({"uni200C"}),):
        divergences.append(f"{stage}: backtrack is {_slots_text(row.backtrack)}, expected [{{uni200C}}]")
    if row.lookahead:
        divergences.append(f"{stage}: lookahead is {_slots_text(row.lookahead)}, expected none")
    if len(row.input) != 1 or row.input[0] != expected_input:
        divergences.append(
            f"{stage}: the input class holds {sorted(row.input[0]) if len(row.input) == 1 else _slots_text(row.input)}, expected the {len(expected_input)} entry-live glyphs"
        )
        return 0
    if len(row.records) != 1 or row.records[0][0] != 0:
        divergences.append(
            f"{stage}: substitutions are {list(row.records)}, expected one at sequence index 0"
        )
        return 0
    inner = _lookup_at(lookups, row.records[0][1])
    if inner is None:
        divergences.append(f"{stage}: substitution names lookup {row.records[0][1]}, which does not exist")
        return 0
    expected_mapping = {raw: locked for locked, raw in plan.locked_glyphs.items()}
    return _check_single_stage(stage, inner, expected_mapping, divergences)


def _check_settle(plan: GsubPlan, lookup: Any, lookups: list[Any], divergences: list[str]) -> tuple[int, int]:
    """Settlement compared per input glyph, the grain first-match-wins runs on and the one `pack_gsub` states its own round trip at: for each glyph the ordered (backtrack, lookahead, outcome) triples the font holds, against the ones the plan emitted."""
    stage = "settle"
    expected: dict[str, list[tuple]] = {}
    for rule in plan.settle_rules:
        backtrack = (rule.backtrack,) if rule.backtrack else ()
        expected.setdefault(rule.input_glyph, []).append((backtrack, rule.lookahead, rule.outcome))
    try:
        sequences = pack_gsub.per_glyph_sequences(lookup)
    except pack_gsub.PackError as error:
        divergences.append(f"{stage}: the lookup does not decompile — {error}")
        return 0, 0
    got: dict[str, list[tuple]] = {}
    for glyph in sorted(sequences):
        for index, rule in enumerate(sequences[glyph]):
            if rule.input != frozenset({glyph}):
                divergences.append(
                    f"{stage}: {glyph} rule {index} matches the input class {sorted(rule.input)}, expected the single glyph"
                )
            outcome: str | None = None
            if len(rule.records) != 1 or rule.records[0][0] != 0:
                divergences.append(
                    f"{stage}: {glyph} rule {index} carries substitutions {list(rule.records)}, expected one at sequence index 0"
                )
            else:
                inner = _lookup_at(lookups, rule.records[0][1])
                mapping = None if inner is None else _single_mapping(inner)
                if mapping is None or glyph not in mapping:
                    divergences.append(
                        f"{stage}: {glyph} rule {index} resolves through lookup {rule.records[0][1]}, which substitutes nothing for {glyph}"
                    )
                else:
                    outcome = mapping[glyph]
            got.setdefault(glyph, []).append((rule.backtrack, rule.lookahead, outcome))
    reported = 0
    for glyph in sorted(set(expected) | set(got)):
        want = expected.get(glyph, [])
        have = got.get(glyph, [])
        if want == have:
            continue
        reported += 1
        if reported > 10:
            continue
        if len(want) != len(have):
            divergences.append(f"{stage}: {glyph} carries {len(have)} rules, expected {len(want)}")
            continue
        for index, (one, other) in enumerate(zip(want, have)):
            if one == other:
                continue
            divergences.append(
                f"{stage}: {glyph} rule {index} is {_slots_text(other[0])} {_slots_text(other[1])} -> {other[2]}, expected {_slots_text(one[0])} {_slots_text(one[1])} -> {one[2]}"
            )
            break
    total = sum(len(rules) for rules in got.values())
    if total != plan.rule_count:
        divergences.append(f"{stage}: {total} rules in the font, expected the plan's {plan.rule_count}")
    return total, len(got)


def _check_namer_dot(plan: GsubPlan, lookup: Any, lookups: list[Any], divergences: list[str]) -> int:
    """The namer-dot mini-calt: the ZWNJ guard row that keeps the dot from lowering across a word boundary, then the row that lowers it before a Short letter."""
    stage = "namer dot"
    assert plan.namer_dot_stage is not None
    dot, lowered, followers = plan.namer_dot_stage
    rows, problems = _chain_rows(lookup)
    for problem in problems:
        divergences.append(f"{stage}: {problem}")
    if len(rows) != 2:
        divergences.append(f"{stage}: {len(rows)} rows, expected the guard row and the lowering row")
        return len(rows)
    guard, lower = rows
    if guard.input != (frozenset({dot}),) or guard.lookahead != (frozenset({"uni200C"}),) or guard.records:
        divergences.append(
            f"{stage}: the guard row is {_slots_text(guard.input)} before {_slots_text(guard.lookahead)} with substitutions {list(guard.records)}, expected [{{{dot}}}] before [{{uni200C}}] with none"
        )
    if lower.input != (frozenset({dot}),) or lower.lookahead != (followers,):
        divergences.append(
            f"{stage}: the lowering row is {_slots_text(lower.input)} before a {len(lower.lookahead[0]) if lower.lookahead else 0}-glyph follower class, expected [{{{dot}}}] before the plan's {len(followers)} followers"
        )
    if len(lower.records) != 1 or lower.records[0][0] != 0:
        divergences.append(
            f"{stage}: the lowering row carries substitutions {list(lower.records)}, expected one at sequence index 0"
        )
        return len(rows)
    inner = _lookup_at(lookups, lower.records[0][1])
    mapping = None if inner is None else _single_mapping(inner)
    if mapping != {dot: lowered}:
        divergences.append(
            f"{stage}: the lowering row resolves through lookup {lower.records[0][1]} with mapping {mapping}, expected {{{dot!r}: {lowered!r}}}"
        )
    return len(rows)


def _anchor_pair(anchor: Any) -> Anchor:
    return None if anchor is None else (anchor.XCoordinate, anchor.YCoordinate)


def _check_cursive(
    gpos: Any,
    cursive: Mapping[int, Mapping[str, Registration]],
    divergences: list[str],
) -> dict[str, int]:
    """One `curs` lookup per registered height that has any anchors, in height order, each a format-1 CursivePos whose entry/exit records equal the emitter's registrations glyph for glyph."""
    counts: dict[str, int] = {}
    tags = [record.FeatureTag for record in gpos.FeatureList.FeatureRecord or []]
    if tags != ["curs"]:
        divergences.append(f"feature list: GPOS registers {tags}, expected exactly curs")
        return counts
    indices = _feature_indices(gpos)["curs"]
    heights = [y for y in CURS_HEIGHT_YS if cursive.get(y)]
    if len(indices) != len(heights):
        divergences.append(
            f"curs registration: curs carries {len(indices)} lookups {indices}, expected one per anchored height {heights}"
        )
        return counts
    if indices != sorted(indices):
        divergences.append(f"lookup order: curs lookups are {indices}, expected them in definition order")
    lookups = list(gpos.LookupList.Lookup or [])
    for y, index in zip(heights, indices):
        stage = f"cursive y{y}"
        expected = dict(cursive[y])
        lookup = _lookup_at(lookups, index)
        subtables = [] if lookup is None else _unwrapped(lookup)
        if len(subtables) != 1 or type(subtables[0]).__name__ != "CursivePos":
            divergences.append(
                f"{stage}: lookup {index} holds {[type(subtable).__name__ for subtable in subtables]}, expected one CursivePos"
            )
            continue
        subtable = subtables[0]
        if subtable.Format != 1:
            divergences.append(f"{stage}: the CursivePos is format {subtable.Format}, expected 1")
            continue
        coverage = list(subtable.Coverage.glyphs)
        records = list(subtable.EntryExitRecord or [])
        if len(coverage) != len(records):
            divergences.append(
                f"{stage}: {len(coverage)} covered glyphs against {len(records)} entry/exit records"
            )
            continue
        got = {
            glyph: (_anchor_pair(record.EntryAnchor), _anchor_pair(record.ExitAnchor))
            for glyph, record in zip(coverage, records)
        }
        counts[f"y{y}"] = len(got)
        if got == expected:
            continue
        missing = sorted(set(expected) - set(got))
        extra = sorted(set(got) - set(expected))
        if missing or extra:
            divergences.append(
                f"{stage}: {len(got)} registrations, expected {len(expected)}; missing {missing[:5]}, unplanned {extra[:5]}"
            )
        moved = sorted(glyph for glyph in set(got) & set(expected) if got[glyph] != expected[glyph])
        for glyph in moved[:5]:
            divergences.append(f"{stage}: {glyph} is anchored {got[glyph]}, expected {expected[glyph]}")
    return counts


def verify_font(
    font_path: Path,
    plan: GsubPlan,
    cursive: Mapping[int, Mapping[str, Registration]],
) -> dict:
    """Re-parse the font at `font_path` and compare every GSUB/GPOS registration, lookup order, lookupFlag and lookup body against the emitters' plan; returns the JSON-ready report `run_m1` writes to `readback_summary.json`. Divergences are collected, never raised."""
    from fontTools.ttLib import TTFont

    divergences: list[str] = []
    checked: dict[str, Any] = {}
    font = TTFont(str(font_path))
    try:
        if "GSUB" not in font:
            divergences.append("feature list: the font carries no GSUB table")
        else:
            gsub = font["GSUB"].table
            lookups = list(gsub.LookupList.Lookup or [])
            _check_script_list(gsub, "GSUB", divergences)
            checked["gsub_features"] = len(gsub.FeatureList.FeatureRecord or [])
            checked["gsub_lookups_flag_checked"] = _check_lookup_flags(gsub, "GSUB", divergences)
            indices = _check_feature_list(plan, gsub, divergences)
            if indices is not None:
                stages = _check_definition_order(plan, indices, divergences)
                if plan.ss10_preempt:
                    preempt = _stage_lookup(lookups, indices["ss10"][0], "ss10 pre-empt", divergences)
                    if preempt is not None:
                        checked["ss10_substitutions"] = _check_single_stage(
                            "ss10 pre-empt", preempt, plan.ss10_preempt, divergences
                        )
                marker_substitutions = 0
                for feature in sorted(plan.marker_lines):
                    marker = _stage_lookup(lookups, indices[feature][0], f"marker {feature}", divergences)
                    if marker is not None:
                        marker_substitutions += _check_single_stage(
                            f"marker {feature}", marker, plan.marker_lines[feature], divergences
                        )
                checked["marker_substitutions"] = marker_substitutions
                for stage_name, stage_index in stages.items():
                    lookup = _stage_lookup(lookups, stage_index, stage_name, divergences)
                    if lookup is None:
                        continue
                    if stage_name == "m1_formation_guarded":
                        checked["guarded_rows"] = _check_guarded_formation(plan, lookup, lookups, divergences)
                    elif stage_name == "m1_formation":
                        checked["plain_ligatures"] = _check_plain_formation(plan, lookup, divergences)
                    elif stage_name == "m1_zwnj":
                        checked["chokepoint_members"] = _check_chokepoint(plan, lookup, lookups, divergences)
                    elif stage_name == "m1_settle":
                        settle_rules, settle_inputs = _check_settle(plan, lookup, lookups, divergences)
                        checked["settle_rules"] = settle_rules
                        checked["settle_input_glyphs"] = settle_inputs
                    elif stage_name == "m1_namer_dot_word_start":
                        checked["namer_rows"] = _check_namer_dot(plan, lookup, lookups, divergences)
        if "GPOS" not in font:
            divergences.append("feature list: the font carries no GPOS table")
        else:
            gpos = font["GPOS"].table
            _check_script_list(gpos, "GPOS", divergences)
            checked["gpos_features"] = len(gpos.FeatureList.FeatureRecord or [])
            checked["gpos_lookups_flag_checked"] = _check_lookup_flags(gpos, "GPOS", divergences)
            checked["cursive_anchors"] = _check_cursive(gpos, cursive, divergences)
    finally:
        font.close()

    reported = divergences[:MAX_DIVERGENCES]
    if len(divergences) > MAX_DIVERGENCES:
        reported.append(f"… and {len(divergences) - MAX_DIVERGENCES} more")
    return {
        "pass": not divergences,
        "font": str(font_path),
        "checked": checked,
        "divergences": reported,
    }


def settle_fold_path(out_dir: Path) -> Path:
    return Path(out_dir) / SETTLE_FOLD_FILENAME


@dataclass(frozen=True)
class SettleFold:
    """One build's emitted settlement lookup as it was recorded: the fingerprint of the sources the tables came from, the font the rows were proven against, whether that proof passed, the configurations whose tables contributed, and the rows themselves in emitted order with their sources."""

    inputs: str | None
    font: str | None
    readback_pass: bool
    configs: tuple[str, ...]
    rules: tuple[SettleRule, ...]


def write_settle_fold(
    path: Path, plan: GsubPlan, inputs: str | None, readback_pass: bool, font: Path | None = None
) -> None:
    """Record the emitted settlement lookup — the rows the emitters planned and this stage just held the font to — with each row's per-configuration sources, stamped with the fingerprint of the sources the tables were built from, so the witness gate can enumerate its coverage over what ships instead of over the six tables the rows fold from. Written whether or not read-back passed, with the verdict in the head, because a failing build's fold is exactly what a reader needs to see. Diff-stable like the window enumerations: a head line then one JSON row per emitted rule, sorted slot members and no incidental ordering, so two builds of one fold are byte-identical."""
    configs = tuple(dict.fromkeys(name for rule in plan.settle_rules for name, _index in rule.sources))
    head = {
        "format": SETTLE_FOLD_FORMAT,
        "inputs": inputs,
        "font": None if font is None else str(font),
        "readback_pass": bool(readback_pass),
        "configs": list(configs),
        "rules": len(plan.settle_rules),
    }
    lines = [json.dumps(head, separators=(",", ":"))]
    for rule in plan.settle_rules:
        row = [
            rule.input_glyph,
            sorted(rule.backtrack) if rule.backtrack else None,
            [sorted(slot) for slot in rule.lookahead],
            rule.outcome,
            [[name, index] for name, index in rule.sources],
        ]
        lines.append(json.dumps(row, separators=(",", ":")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def read_settle_fold(path: Path) -> SettleFold:
    """The `write_settle_fold` inverse. Raises OSError when the record is absent and ValueError when it is not a fold this build understands or when the head's row count and the rows on disk disagree — a truncated file must not read as a shorter fold, which would understate what ships. It judges the stamp no more than `table.read_windows` does: a caller deciding whether to trust the record compares the returned fingerprint itself."""
    lines = path.read_text().splitlines()
    try:
        head = json.loads(lines[0]) if lines else None
    except ValueError:
        head = None
    if not isinstance(head, dict) or head.get("format") != SETTLE_FOLD_FORMAT:
        raise ValueError(f"{path}: not a {SETTLE_FOLD_FORMAT} record")
    rows = lines[1:]
    if head.get("rules") != len(rows):
        raise ValueError(f"{path}: the head names {head.get('rules')} rows, the file holds {len(rows)}")
    rules = []
    for line in rows:
        input_glyph, backtrack, lookahead, outcome, sources = json.loads(line)
        rules.append(
            SettleRule(
                input_glyph=input_glyph,
                backtrack=None if backtrack is None else frozenset(backtrack),
                lookahead=tuple(frozenset(slot) for slot in lookahead),
                outcome=outcome,
                sources=tuple((name, index) for name, index in sources),
            )
        )
    return SettleFold(
        inputs=head.get("inputs"),
        font=head.get("font"),
        readback_pass=bool(head.get("readback_pass")),
        configs=tuple(head.get("configs") or ()),
        rules=tuple(rules),
    )
