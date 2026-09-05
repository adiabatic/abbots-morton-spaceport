"""Read-back verification: the font that was just written, re-parsed from its own bytes and structurally proven against the plan the emitters held in memory (issue #73).

The build stage before this one hands feaLib a block of FEA text and gets an OTF back, and everything between those two — feaLib's parse, its lookup and subtable format choices, `pack_gsub`'s repack of the settlement lookup, fontTools' serialization, and the re-parse — is machinery no gate downstream reads structurally. `gate:conform` proves the font *shapes* what settlement says, through HarfBuzz, which is the behavioral claim and the one that matters; but it can only see what its sweep reaches, and it says nothing about a rule that is present and inert, a feature registered under the wrong tag, or a lookupFlag that skips a class nobody probed. This stage makes the transcription claim instead: every lookup's decompiled content equals what the emitter planned, every feature and script registration is the one the plan implies, the cross-feature LookupList order that pins application order on both shapers is the definition order the emitters chose, and every lookupFlag is zero. The two glyphs a word boundary is made of are proven inert on the same bytes: no substituted position of any lookup admits `uni200C` or `space` — a format-2 class 0 resolved to the complement of its ClassDef, so a rule that reaches a slot through the unnamed class is visible here — `uni200C` is zero-advance in `hmtx`, and neither glyph draws an outline, so gate:conform's belt no longer has to weigh a ZWNJ slot per shaped text. Zero divergences means the compiled font provably holds the rules the plan intended.

It is deliberately a transcription round-trip and nothing more. `pack_gsub`'s repack is proven here, over the written bytes, by decompiling the settlement lookup through `pack_gsub.per_glyph_sequences` and holding each input glyph's ordered rules to the plan the emitters held — the pass itself no longer replays its own output in memory. It predicts no cascade: it never asks what a buffer would do, never composes stages, never resolves which of two competing rules wins. Ordered rules are compared at the grain first-match-wins actually runs on (per input glyph for settlement, per lead glyph for formation), because rules that cannot share an input cannot compete and feaLib is free to regroup them — it picks whichever of the three chained-context subtable formats compiles smallest, so the guarded formation rides format 1 in a small font and format 3 in the shipped one, and the settlement lookup arrives packed into a format-2/format-3 mix. Shaping behavior stays gate:conform's.

The failure contract: `verify_font` never raises for a divergence, it accumulates human-readable strings and reports `pass`; `run_m1` writes the whole report to `readback_summary.json` and only then raises `ReadbackError`, so the evidence outlives the failure. The GSUB offset budget rides that same contract — the uint16 subtable-offset headroom the packing exists to protect is read straight off the raw table bytes in the parse this stage already makes, recorded under `checked["gsub_budget"]`, and a headroom under `SUBTABLE_OFFSET_HEADROOM_FLOOR` is one more divergence. The overflow itself can never ship, because fontTools' save refuses a lookup-level offset-array overflow outright, so the floor is an early warning rather than the wall: in the Extension-promoted settlement lookup each subtable costs 2 bytes of offset entry plus an 8-byte ExtensionSubst record, which puts a 16,384-byte floor roughly 1,500 subtables ahead of the wall. It has fired for real twice — on the depth-4 rules, which is what moved the lookup to Extension, and on the flag-on prospect table, which is what produced the format-2 repack — both times on a font fontTools would have written silently, and it is held at that value on that record.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

from rebuild.pipeline import pack_gsub
from rebuild.pipeline.emit_gpos import CURS_HEIGHT_YS, Anchor, Registration
from rebuild.pipeline.emit_gsub import GsubPlan, SettleRule

MAX_DIVERGENCES = 50
BOUNDARY_GLYPHS = ("uni200C", "space")
SUBTABLE_OFFSET_HEADROOM_FLOOR = 16_384
NO_REQUIRED_FEATURE = 0xFFFF

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


def gsub_offset_budget(data: bytes) -> dict:
    """The uint16 offset space the raw GSUB table is living on, walked off its own bytes rather than a decoded table: the LookupList's offsets to each lookup and each lookup's offsets to its own subtables are uint16 fields, so the headroom left under 65,535 is what any further growth has to fit in. A table too short to hold a header reports empty counts and full headroom rather than raising — the decoded-table checks already speak for a malformed GSUB."""
    if len(data) < 10:
        return {
            "gsub_bytes": len(data),
            "lookups": 0,
            "subtables": 0,
            "lookuplist_offset_headroom": 65_535,
            "subtable_offset_headroom": 65_535,
            "tightest_lookup_index": None,
        }
    (lookup_list,) = struct.unpack_from(">H", data, 8)
    (lookup_count,) = struct.unpack_from(">H", data, lookup_list)
    lookup_offsets = [
        struct.unpack_from(">H", data, lookup_list + 2 + 2 * index)[0] for index in range(lookup_count)
    ]
    subtables = 0
    widest_subtable_offset = 0
    tightest: int | None = None
    for index, lookup_offset in enumerate(lookup_offsets):
        lookup_table = lookup_list + lookup_offset
        (subtable_count,) = struct.unpack_from(">H", data, lookup_table + 4)
        subtables += subtable_count
        for position in range(subtable_count):
            (subtable_offset,) = struct.unpack_from(">H", data, lookup_table + 6 + 2 * position)
            if subtable_offset > widest_subtable_offset:
                widest_subtable_offset = subtable_offset
                tightest = index
    return {
        "gsub_bytes": len(data),
        "lookups": lookup_count,
        "subtables": subtables,
        "lookuplist_offset_headroom": 65_535 - max(lookup_offsets, default=0),
        "subtable_offset_headroom": 65_535 - widest_subtable_offset,
        "tightest_lookup_index": tightest,
    }


def _unwrapped(lookup: Any) -> list[Any]:
    """The lookup's subtables with any Extension wrapper (GSUB type 7, GPOS type 9) removed."""
    return [getattr(subtable, "ExtSubTable", subtable) for subtable in lookup.SubTable or []]


def _records_of(rule: Any) -> tuple[tuple[int, int], ...]:
    return tuple((record.SequenceIndex, record.LookupListIndex) for record in rule.SubstLookupRecord or [])


def _class_sets(class_defs: Mapping[str, int], all_glyphs: frozenset[str]) -> dict[int, frozenset[str]]:
    by_class: dict[int, set[str]] = {}
    for glyph, klass in class_defs.items():
        by_class.setdefault(klass, set()).add(glyph)
    by_class.setdefault(0, set()).update(all_glyphs - frozenset(class_defs))
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


def _chain_rows(lookup: Any, all_glyphs: frozenset[str]) -> tuple[list[_ChainRow], list[str]]:
    """Every chained-context rule the lookup expresses, in subtable order, across all three subtable formats — feaLib compiles each ruleset in whichever format is smallest, so a stage's shape on disk is not the shape its FEA was written in, and a format-2 class 0 resolves to its OpenType meaning, every glyph in the font's glyph order the ClassDef does not name, rather than to the empty set."""
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
            backtrack_sets = _class_sets(subtable.BacktrackClassDef.classDefs, all_glyphs)
            input_sets = _class_sets(subtable.InputClassDef.classDefs, all_glyphs)
            lookahead_sets = _class_sets(subtable.LookAheadClassDef.classDefs, all_glyphs)
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


def _check_boundary_glyphs(
    font: Any, lookups: list[Any], all_glyphs: frozenset[str], divergences: list[str]
) -> dict:
    """The two glyphs a word boundary is made of, proven inert on the bytes: no substituted position of any lookup admits `uni200C` or `space`, `uni200C` carries a zero advance in `hmtx`, and neither draws an outline. That is proven once per build, off the bytes, rather than per shaped text at every ZWNJ slot the conform sweep reaches. The state is unreachable from a rune edit — every substituted position is minted from rune names by `emit_gsub`, and `compile_font` supplies both boundary glyphs inkless with `uni200C` zero-advance — so the only way a ZWNJ slot could gain ink or an advance is an emitter, packer, compiler or shaper edit, and every one of those lands in the written lookups or in `hmtx`/`CFF `, on the very parse this stage already makes."""
    from fontTools.pens.boundsPen import BoundsPen

    stage = "boundary glyphs"
    boundary = set(BOUNDARY_GLYPHS)
    positions = 0
    for index, lookup in enumerate(lookups):
        subtables = _unwrapped(lookup)
        if subtables and type(subtables[0]).__name__ == "ChainContextSubst":
            rows, problems = _chain_rows(lookup, all_glyphs)
            for problem in problems:
                divergences.append(f"{stage}: lookup {index} {problem}")
            for row_index, row in enumerate(rows):
                for sequence_index, _inner in row.records:
                    positions += 1
                    if sequence_index >= len(row.input):
                        divergences.append(
                            f"{stage}: lookup {index} row {row_index} substitutes sequence index {sequence_index} past its {len(row.input)} input slots"
                        )
                        continue
                    hit = row.input[sequence_index] & boundary
                    if hit:
                        divergences.append(
                            f"{stage}: lookup {index} row {row_index} substitutes input slot {sequence_index}, whose class admits {sorted(hit)}"
                        )
            continue
        for position, subtable in enumerate(subtables):
            kind = type(subtable).__name__
            if kind == "SingleSubst":
                positions += len(subtable.mapping)
                hit = sorted(set(subtable.mapping) & boundary)
                if hit:
                    divergences.append(f"{stage}: lookup {index} substitutes {hit} itself")
            elif kind == "LigatureSubst":
                for first, ligatures in subtable.ligatures.items():
                    for ligature in ligatures:
                        sequence = (first, *ligature.Component)
                        positions += 1
                        hit = sorted(set(sequence) & boundary)
                        if hit:
                            divergences.append(
                                f"{stage}: lookup {index} forms {' '.join(sequence)}, whose input admits {hit}"
                            )
            else:
                divergences.append(
                    f"{stage}: lookup {index} subtable {position} is {kind}, which no stage emits"
                )
    metrics: dict[str, Any] = {}
    glyph_set = font.getGlyphSet()
    for name in BOUNDARY_GLYPHS:
        if name not in all_glyphs:
            divergences.append(f"{stage}: the font carries no {name}")
            metrics[name] = None
            continue
        advance = font["hmtx"][name][0]
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        inked = pen.bounds is not None
        metrics[name] = {"advance": advance, "inked": inked}
        if name == "uni200C" and advance:
            divergences.append(f"{stage}: {name} carries an advance of {advance}, expected 0")
        if inked:
            divergences.append(f"{stage}: {name} draws ink over {pen.bounds}, expected an empty outline")
    return {"substituted_positions": positions, **metrics}


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


def _check_guarded_formation(
    plan: GsubPlan, lookup: Any, lookups: list[Any], all_glyphs: frozenset[str], divergences: list[str]
) -> int:
    """The late-formation guard's rows as the font holds them: literal input slots, no backtrack, and either no substitution (an `ignore sub` guard row) or one at sequence index 0 resolving through the anonymous ligature lookup feaLib deduped the forming rows into."""
    stage = "formation guarded"
    rows, problems = _chain_rows(lookup, all_glyphs)
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


def _check_chokepoint(
    plan: GsubPlan, lookup: Any, lookups: list[Any], all_glyphs: frozenset[str], divergences: list[str]
) -> int:
    """The ZWNJ chokepoint: one row that matches every entry-live raw glyph behind a ZWNJ and substitutes its locked twin, so nothing downstream of a word boundary can join leftward."""
    stage = "zwnj chokepoint"
    rows, problems = _chain_rows(lookup, all_glyphs)
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
    """Settlement compared per input glyph, the grain first-match-wins runs on: for each glyph the ordered (backtrack, lookahead, outcome) triples the font holds, against the ones the plan emitted. Decompiling the on-disk lookup through `pack_gsub.per_glyph_sequences` is also what proves the repack — over the written bytes, at the grain the packing had to preserve."""
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


def _check_namer_dot(
    plan: GsubPlan, lookup: Any, lookups: list[Any], all_glyphs: frozenset[str], divergences: list[str]
) -> int:
    """The namer-dot mini-calt: the ZWNJ guard row that keeps the dot from lowering across a word boundary, then the row that lowers it before a Short letter."""
    stage = "namer dot"
    assert plan.namer_dot_stage is not None
    dot, lowered, followers = plan.namer_dot_stage
    rows, problems = _chain_rows(lookup, all_glyphs)
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
    """Re-parse the font at `font_path` and compare every GSUB/GPOS registration, lookup order, lookupFlag and lookup body against the emitters' plan, prove the boundary glyphs inert (no substituted position admits `uni200C` or `space`, zero advance, no outline), and read the GSUB's uint16 offset budget off the raw table bytes in the same parse; returns the JSON-ready report `run_m1` writes to `readback_summary.json`. Divergences are collected, never raised."""
    from fontTools.ttLib import TTFont

    divergences: list[str] = []
    checked: dict[str, Any] = {}
    font = TTFont(str(font_path))
    all_glyphs = frozenset(font.getGlyphOrder())
    try:
        if "GSUB" not in font:
            divergences.append("feature list: the font carries no GSUB table")
        else:
            gsub = font["GSUB"].table
            budget = gsub_offset_budget(cast(Any, font.reader)["GSUB"])
            checked["gsub_budget"] = {**budget, "floor": SUBTABLE_OFFSET_HEADROOM_FLOOR}
            if budget["subtable_offset_headroom"] < SUBTABLE_OFFSET_HEADROOM_FLOOR:
                divergences.append(
                    f"gsub budget: subtable offset headroom {budget['subtable_offset_headroom']:,} bytes in lookup {budget['tightest_lookup_index']}, under the {SUBTABLE_OFFSET_HEADROOM_FLOOR:,}-byte floor"
                )
            lookups = list(gsub.LookupList.Lookup or [])
            _check_script_list(gsub, "GSUB", divergences)
            checked["gsub_features"] = len(gsub.FeatureList.FeatureRecord or [])
            checked["gsub_lookups_flag_checked"] = _check_lookup_flags(gsub, "GSUB", divergences)
            checked["boundary_glyphs"] = _check_boundary_glyphs(font, lookups, all_glyphs, divergences)
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
                        checked["guarded_rows"] = _check_guarded_formation(
                            plan, lookup, lookups, all_glyphs, divergences
                        )
                    elif stage_name == "m1_formation":
                        checked["plain_ligatures"] = _check_plain_formation(plan, lookup, divergences)
                    elif stage_name == "m1_zwnj":
                        checked["chokepoint_members"] = _check_chokepoint(
                            plan, lookup, lookups, all_glyphs, divergences
                        )
                    elif stage_name == "m1_settle":
                        settle_rules, settle_inputs = _check_settle(plan, lookup, lookups, divergences)
                        checked["settle_rules"] = settle_rules
                        checked["settle_input_glyphs"] = settle_inputs
                        formats = [getattr(subtable, "Format", None) for subtable in _unwrapped(lookup)]
                        checked["settle_subtable_formats"] = {
                            "format2": formats.count(2),
                            "format3": formats.count(3),
                        }
                    elif stage_name == "m1_namer_dot_word_start":
                        checked["namer_rows"] = _check_namer_dot(
                            plan, lookup, lookups, all_glyphs, divergences
                        )
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
