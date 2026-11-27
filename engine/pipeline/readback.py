"""Re-parse the font just written and check its structure against the plan the emitters held in memory.

Between the FEA text and the written OTF lie feaLib's parse and its choice of lookup and subtable formats, `pack_gsub`'s repack of the settlement lookup, fontTools' serialization, and the re-parse. gate:conform checks through HarfBuzz that the font shapes what settlement says, but only over the texts its sweep reaches. It cannot see a rule that is present but never fires, a feature registered under the wrong tag, or a lookupFlag that skips a class no text probes. This stage makes a structural comparison with the plan instead: every lookup's decompiled content equals the emitter's plan, every feature and script registration is the one the plan implies, the LookupList order, which sets application order across features, is the emitters' definition order, and every lookupFlag is zero.

On the same bytes it checks that the two word-boundary glyphs are inert. No substituted position of any lookup admits `uni200C` or `space`, `uni200C` has zero advance in `hmtx`, and neither glyph has an outline. A format-2 class 0 is resolved to every glyph its ClassDef does not name, so a rule that reaches a slot through class 0 is caught. gate:conform's sweep relies on this check and does not test the ZWNJ glyph's advance or outline in each shaped text.

It also checks that the ss10 overlay is isolated (`_check_isolation`, `_check_anchorless_copies`). The ss10 input substitution covers every letter cmap glyph, and no `.ss10` copy has its own cmap entry, appears in any formation sequence, marker line, ZWNJ lock class, or settlement input, or carries a cursive anchor. So under ss10 nothing forms, settles, or attaches. That is why the overlay configuration needs no settlement table, and why gate:conform's sweep covers it with texts of at most two letters.

It checks each cosmetic set's lookup (`_check_cosmetic`, `_check_cosmetic_registrations`): registered under its set alone, after every lookup another feature registers, substituting exactly the planned unjoined cell glyphs by their same-advance copies, with no cursive anchor on either and each copy covered by the cursive lookups that cover its cell.

It checks the `kern` feature against the kern plan (`_check_kern`): the feature holds no lookup after the `curs` lookups when the plan has no rule and the plan's one lookup there otherwise; that lookup's adjacent rules and its one-ZWNJ rules each give exactly the plan's (first glyph, second glyph) to value relation through their value lookups; and no glyph the lookup adjusts has an exit anchor, and no glyph it reads after one has an entry anchor, so the lookup cannot move a join.

The full font also carries features the rune pipeline does not plan, from glyph_data/ (`compile_font`): GSUB's `ccmp`, and GPOS's `mark` and the Latin lookups of `kern` (`FOREIGN_GSUB_FEATURES`, `FOREIGN_GPOS_FEATURES`). They are allowed beside the planned ones, which must still be exactly as planned and in definition order, and `_check_foreign` holds them apart from the runes: each table's lookups split cleanly into those the planned features reach and those the foreign ones reach, with every foreign lookup defined first, as `compile_font` writes them; no foreign lookup substitutes a rune-owned glyph (`rune_owned`: every `qs` glyph, the boundary glyphs and the namer dot pair); and none positions a pair of two rune-owned glyphs, though one may move a rune glyph before a non-rune glyph, as the Latin rule that kerns every glyph before `j` does. The foreign lookup counts are recorded under `checked["foreign_lookups"]`. The namer dot's mid-word row (`emit_gsub`'s `namer_midword`) is checked with the namer-dot stage. The variation sequences are checked against the glyphs `run_build.mint_variation_glyphs` minted (`_check_variation_glyphs`): the cmap format 14 is exactly theirs, and no settlement class, outcome or other stage of the plan names one and no cursive lookup anchors one. Bold, when the build writes one, is checked against Regular (`verify_bold`): byte-identical layout tables and the same `usMaxContext`, cmap, glyph order and advances, and every outline Regular's widened by the overstrike, so every check above holds for Bold too.

The stage is a structural comparison with the plan only. It does not simulate shaping: it does not apply lookups to a buffer, compose stages, or decide which of two competing rules wins. It checks `pack_gsub`'s repack on the written bytes by decompiling the settlement lookup through `pack_gsub.per_glyph_sequences` and reassembling each input glyph's ordered rules into the plan's (`_reassemble`). The packer refines every rule into disjoint pieces over its family's atoms (`pack_gsub._refine`), so a plan rule is matched to a run of consecutive font rules with its outcome that lie inside it, miss each other, together cover it, and hold `uni200C` wherever it does short of the farthest slot on a side, since HarfBuzz skips a ZWNJ that a context slot does not hold. Pieces that overlap, that leave part of the plan rule uncovered, that sit out of order, or that drop such a `uni200C` are divergences. The reassembly never calls the packer's refinement, which would then be checking itself. Ordered rules are compared per input glyph for settlement and per lead glyph for formation, because first-match-wins only orders rules that share an input glyph, and feaLib may regroup the others. feaLib compiles each chained-context ruleset in whichever of the three formats is smallest, so the guarded formation is format 1 in a small font and format 3 in the shipped one, and the packed settlement lookup arrives as format 2, with a format-3 subtable for any rule the packer keeps whole, beside any format-2 subtable feaLib wrote itself.

`verify_font` does not raise on a divergence. It collects messages and reports `pass`, the first `MAX_DIVERGENCES` messages, and the total count. `run_build` writes the report to `readback_summary.json` and then raises `ReadbackError` naming that total, so the evidence survives the failure. The GSUB offset budget is reported the same way: `gsub_offset_budget` reads the uint16 subtable-offset headroom from the raw table bytes of this same parse, the report records it under `checked["gsub_budget"]`, and headroom below `SUBTABLE_OFFSET_HEADROOM_FLOOR` is a divergence. An actual overflow cannot ship, because fontTools' save fails on an overflow of a lookup's subtable-offset array, so the floor is an early warning. In the Extension-wrapped settlement lookup each subtable costs a 2-byte offset entry plus an 8-byte ExtensionSubst record, so the 16,384-byte floor sits about 1,600 subtables short of the overflow. The floor has twice caught a font that fontTools would have saved without error: the depth-4 rules, which led `qs_settle` to use Extension, and the simulated-prospect table, which led to `pack_gsub`. Those two catches are why it stays at this value.

The same parse gates a second size limit, the rule tables of one packed group. hb.repack cannot split a chained-context subtable, so a format-2 group whose rule tables pass about 65 KB cannot be serialized, and `compile_font` stops such a build before the save (`compile_font.GROUP_RULE_BYTES_LIMIT`; `compile_font`'s module docstring has the measurement). Read-back computes each format-2 settlement subtable's rule bytes from the decompiled rules (`pack_gsub.largest_group`), records the largest and its subtable index under `checked["gsub_budget"]`, and reports a divergence when it passes `GROUP_RULE_BYTES_CEILING`. The ceiling is 65,535 less `SUBTABLE_OFFSET_HEADROOM_FLOOR`, so it keeps the margin the floor keeps, and it sits well under the largest group that serialized. The packer caps its own groups under it (`pack_gsub.GROUP_RULE_BYTES_CAP`), so the ceiling checks the written bytes rather than limiting growth.

The widest offset inside a packed subtable is not gated, because it is not headroom. hb.repack lets a region of tables shared across Extension subtables fill up to the uint16 limit, then splits the region and duplicates the shared tables, so that offset sits just under 65,535 in every font whatever the size of its groups. A floor on it would fail builds that save without trouble.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from engine.pipeline import pack_gsub
from engine.pipeline.emit_gpos import (
    CURS_HEIGHT_YS,
    KERN_FEATURE,
    PIXEL,
    ZWNJ_GLYPH,
    Anchor,
    KernPlan,
    Registration,
    value_lookup_name,
)
from engine.pipeline.emit_gsub import GsubPlan
from engine.pipeline.model import cosmetic_copy_name

MAX_DIVERGENCES = 50
BOUNDARY_GLYPHS = ("uni200C", "space")
NAMER_DOT_GLYPHS = ("periodcentered", "periodcentered.lowered")
SUBTABLE_OFFSET_HEADROOM_FLOOR = 16_384
GROUP_RULE_BYTES_CEILING = 65_535 - SUBTABLE_OFFSET_HEADROOM_FLOOR
NO_REQUIRED_FEATURE = 0xFFFF
# The features the rune pipeline does not plan, which the full font carries from glyph_data/ (`compile_font`): GSUB's `ccmp`, and GPOS's `mark` and the Latin lookups of `kern`, which the plan's own kern lookup shares.
FOREIGN_GSUB_FEATURES = ("ccmp",)
FOREIGN_GPOS_FEATURES = ("kern", "mark")
LAYOUT_TABLES = ("GDEF", "GSUB", "GPOS")
_VALUE_FIELDS = ("XPlacement", "YPlacement", "XAdvance", "YAdvance")

Row = tuple[tuple[str, ...], tuple[frozenset[str], ...], str | None]


class ReadbackError(Exception):
    pass


@dataclass(frozen=True)
class _ChainRow:
    """One chained-context rule decompiled to slot glyph-sets from any subtable format: backtrack closest-first as stored, the input slots in order, lookahead near-to-far, and the substitutions as (sequence index, lookup index) pairs."""

    backtrack: tuple[frozenset[str], ...]
    input: tuple[frozenset[str], ...]
    lookahead: tuple[frozenset[str], ...]
    records: tuple[tuple[int, int], ...]


def gsub_offset_budget(data: bytes) -> dict:
    """Return the uint16 offset headroom of the raw GSUB table bytes. The LookupList's offsets to each lookup and each lookup's offsets to its subtables are uint16 fields, so the headroom under 65,535 is the room left for growth. A table too short to hold a header reports zero counts and full headroom without raising; the decoded-table checks report a malformed GSUB."""
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
    """The (sequence index, lookup index) records of a chained-context rule, GSUB's `SubstLookupRecord` or GPOS's `PosLookupRecord`."""
    records = getattr(rule, "SubstLookupRecord", None)
    if records is None:
        records = getattr(rule, "PosLookupRecord", None)
    return tuple((record.SequenceIndex, record.LookupListIndex) for record in records or [])


def _either(node: Any, gsub_name: str, gpos_name: str) -> Any:
    """The field a GSUB chained-context table names `gsub_name` and its GPOS counterpart names `gpos_name`."""
    value = getattr(node, gsub_name, None)
    return value if value is not None else getattr(node, gpos_name, None)


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
    """The lookup's single-substitution mapping, or None when it is not a single-substitution lookup."""
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
    """Every chained-context rule the lookup holds, in subtable order, from any of the three subtable formats, in GSUB (type 6) or GPOS (type 8). feaLib compiles each ruleset in whichever format is smallest, so a stage's format on disk can differ from the form its FEA was written in. A format-2 class 0 resolves to its OpenType meaning: every glyph in the font's glyph order that the ClassDef does not name."""
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
            for position, rule_set in enumerate(
                _either(subtable, "ChainSubRuleSet", "ChainPosRuleSet") or []
            ):
                if rule_set is None:
                    continue
                lead = frozenset({coverage[position]})
                for rule in _either(rule_set, "ChainSubRule", "ChainPosRule") or []:
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
            for input_class, class_set in enumerate(
                _either(subtable, "ChainSubClassSet", "ChainPosClassSet") or []
            ):
                if class_set is None:
                    continue
                lead = input_sets.get(input_class, frozenset()) & covered
                for rule in _either(class_set, "ChainSubClassRule", "ChainPosClassRule") or []:
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
    """Compare the two ordered row lists per lead glyph. Rows that share a lead glyph must agree in order; rows with different lead glyphs cannot compete. This is also the order that feaLib's format-1 grouping of rules by coverage glyph preserves."""
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
    """Check the script registration every build compiles to, since the emitted FEA has no `languagesystem` statement: DFLT alone, with a DefaultLangSys, no language systems of its own, no required feature, and every feature in the list registered on it."""
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
    """Check that every lookup in the table, anonymous inner lookups included, has a zero LookupFlag and no mark filtering set. The emitters never ask for either, so a nonzero flag means a rule skips glyphs without saying so. Returns the number of lookups checked."""
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
    """Check that the word-boundary glyphs are inert: no substituted position of any lookup admits `uni200C` or `space`, `uni200C` has zero advance in `hmtx`, and neither glyph has an outline. A rune edit cannot break this, because `emit_gsub` mints every substituted position from rune names and `compile_font` adds both boundary glyphs without ink, `uni200C` at zero advance, when the inventory lacks them. Only an emitter, packer, or compiler change could give a ZWNJ slot ink or an advance, and any such change shows up in the written lookups or in `hmtx` and `CFF `, which this check reads."""
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


def _check_isolation(
    font: Any,
    plan: GsubPlan,
    lookups: list[Any],
    all_glyphs: frozenset[str],
    ss10_input: Any,
    exempt: frozenset[int],
    divergences: list[str],
) -> dict:
    """Check the GSUB half of the overlay's isolation. The ss10 input substitution's mapping covers exactly the letter cmap glyphs (every cmap entry the plan names, which is every rune's raw glyph, except the boundary glyphs and the namer dot; a Latin glyph's cmap entry is none of the plan's), no copy has a cmap entry, and no copy appears at any position of any other GSUB lookup: the keys and values of every single substitution, every ligature's components and result, and every input slot of every chained-context rule, with a format-2 class 0 resolved to the glyphs its ClassDef does not name. `exempt` names the lookups allowed to see copies, which is only the namer-dot stage: its follower class holds the Short copies so that the dot still lowers under ss10. Backtrack and lookahead slots are not checked, because a copy there can never match: under ss10 every letter is a copy and no input slot admits one, and outside ss10 no copy is ever in the buffer. `_check_anchorless_copies` checks the GPOS half."""
    stage = "isolation"
    cmap = font.getBestCmap() or {}
    boundary: set[str] = set(BOUNDARY_GLYPHS)
    if plan.namer_dot_stage is not None:
        boundary.add(plan.namer_dot_stage[0])
    letters = frozenset(name for name in cmap.values() if name in plan.named_glyphs and name not in boundary)
    mapping = _single_mapping(ss10_input)
    if mapping is None:
        divergences.append(f"{stage}: the ss10 input substitution holds no single substitutions")
        return {"cmap_letters": len(letters), "copies": 0, "positions_checked": 0}
    uncovered = sorted(letters - set(mapping))
    if uncovered:
        divergences.append(
            f"{stage}: {len(uncovered)} letter cmap glyphs stay in the join pipeline under ss10: {uncovered[:5]}"
        )
    stray = sorted(set(mapping) - letters)
    if stray:
        divergences.append(
            f"{stage}: the ss10 input substitution replaces {stray[:5]}, which no codepoint reaches"
        )
    copies = frozenset(mapping.values())
    encoded = sorted(copies & set(cmap.values()))
    if encoded:
        divergences.append(f"{stage}: copies {encoded[:5]} carry cmap entries of their own")
    positions = 0
    for index, lookup in enumerate(lookups):
        if lookup is ss10_input or index in exempt:
            continue
        for subtable in _unwrapped(lookup):
            kind = type(subtable).__name__
            if kind == "SingleSubst":
                positions += len(subtable.mapping)
                hit = sorted((set(subtable.mapping) | set(subtable.mapping.values())) & copies)
                if hit:
                    divergences.append(f"{stage}: lookup {index} substitutes a copy ({hit[:5]})")
            elif kind == "LigatureSubst":
                for first, ligatures in subtable.ligatures.items():
                    for ligature in ligatures:
                        positions += 1
                        hit = sorted(({first, *ligature.Component, ligature.LigGlyph}) & copies)
                        if hit:
                            divergences.append(
                                f"{stage}: lookup {index} forms {' '.join((first, *ligature.Component))} -> {ligature.LigGlyph}, which names a copy ({hit[:5]})"
                            )
            elif kind == "ChainContextSubst":
                rows, problems = _chain_rows(lookup, all_glyphs)
                for problem in problems:
                    divergences.append(f"{stage}: lookup {index} {problem}")
                for row_index, row in enumerate(rows):
                    for slot_index, slot in enumerate(row.input):
                        positions += 1
                        hit = sorted(slot & copies)
                        if hit:
                            divergences.append(
                                f"{stage}: lookup {index} row {row_index} input slot {slot_index} admits a copy ({hit[:5]})"
                            )
                break
    return {"cmap_letters": len(letters), "copies": len(copies), "positions_checked": positions}


def _check_anchorless_copies(gpos: Any, copies: frozenset[str], divergences: list[str]) -> int:
    """Check the GPOS half of the overlay's isolation: no cursive-attachment subtable covers a copy, so a copy attaches to nothing and advances the pen by its `hmtx` advance alone. Returns the number of copies without anchors."""
    stage = "isolation"
    anchored: set[str] = set()
    for index, lookup in enumerate(gpos.LookupList.Lookup or []):
        for subtable in _unwrapped(lookup):
            if type(subtable).__name__ != "CursivePos":
                continue
            hit = set(subtable.Coverage.glyphs) & copies
            if hit:
                anchored |= hit
                divergences.append(
                    f"{stage}: GPOS lookup {index} registers cursive anchors on copies {sorted(hit)[:5]}"
                )
    return len(copies - anchored)


def _check_cosmetic(
    font: Any, plan: GsubPlan, lookups: list[Any], indices: Mapping[str, list[int]], divergences: list[str]
) -> int:
    """Check each cosmetic set's lookup (`emit_gsub` stage 8) against the plan: it substitutes exactly the planned cell glyphs, each by `model.cosmetic_copy_name` of itself under the set, with the same `hmtx` advance, and it comes after every lookup any other feature registers, so it redraws only settled glyphs and no later lookup reads a copy. That every glyph it reads or writes is anchorless, and each copy covered as its cell is, is checked in the GPOS half (`_check_cosmetic_registrations`). With spec_load's rule that no condition or unlock names a cosmetic set, these make a cosmetic configuration's every text its settlement configuration's with the copies substituted. Returns the number of substitutions checked."""
    stage = "cosmetic"
    metrics = font["hmtx"].metrics
    others = [index for tag, listed in indices.items() if tag not in plan.cosmetic_lines for index in listed]
    count = 0
    for feature, pairs in sorted(plan.cosmetic_lines.items()):
        index = indices[feature][0]
        if others and index <= max(others):
            divergences.append(
                f"{stage}: {feature}'s lookup {index} runs before lookup {max(others)} of another feature, so a later lookup could read its copies"
            )
        lookup = _stage_lookup(lookups, index, f"cosmetic {feature}", divergences)
        if lookup is None:
            continue
        count += _check_single_stage(f"cosmetic {feature}", lookup, pairs, divergences)
        for source, copy in sorted(pairs.items()):
            if copy != cosmetic_copy_name(source, feature):
                divergences.append(
                    f"{stage}: {source} is redrawn as {copy}, not {cosmetic_copy_name(source, feature)}"
                )
            if source not in metrics or copy not in metrics:
                divergences.append(f"{stage}: {source} or {copy} is missing from hmtx")
            elif metrics[source][0] != metrics[copy][0]:
                divergences.append(
                    f"{stage}: {copy} advances {metrics[copy][0]} but {source} advances {metrics[source][0]}, so the set would move the letters after it"
                )
    return count


def _cursive_coverage(gpos: Any, names: frozenset[str]) -> dict[str, list[tuple[int, bool, bool]]]:
    """Return, for each of `names`, every cursive-attachment subtable that covers it, as (lookup index, has an entry anchor, has an exit anchor)."""
    found: dict[str, list[tuple[int, bool, bool]]] = {name: [] for name in names}
    for index, lookup in enumerate(gpos.LookupList.Lookup or []):
        for subtable in _unwrapped(lookup):
            if type(subtable).__name__ != "CursivePos":
                continue
            for position, glyph in enumerate(subtable.Coverage.glyphs):
                if glyph in found:
                    record = subtable.EntryExitRecord[position]
                    found[glyph].append(
                        (index, record.EntryAnchor is not None, record.ExitAnchor is not None)
                    )
    return found


def _check_cosmetic_registrations(gpos: Any, plan: GsubPlan, divergences: list[str]) -> int:
    """Check the GPOS half of the cosmetic lookups: no cursive-attachment subtable gives a redrawn cell or its copy an entry or exit anchor, and each copy is covered by exactly the subtables that cover its cell, so a locked cell's coverage-only registrations carry over and the copy attaches to nothing its cell would not. Returns the number of copies checked."""
    stage = "cosmetic"
    pairs = {source: copy for lines in plan.cosmetic_lines.values() for source, copy in lines.items()}
    coverage = _cursive_coverage(gpos, frozenset(pairs) | frozenset(pairs.values()))
    for source, copy in sorted(pairs.items()):
        for name in (source, copy):
            anchored = [index for index, entry, exit_ in coverage[name] if entry or exit_]
            if anchored:
                divergences.append(
                    f"{stage}: GPOS lookups {anchored} give {name} a cursive anchor, so it is not an unjoined glyph"
                )
        copy_lookups = [index for index, _entry, _exit in coverage[copy]]
        source_lookups = [index for index, _entry, _exit in coverage[source]]
        if copy_lookups != source_lookups:
            divergences.append(
                f"{stage}: {copy} is covered by cursive lookups {copy_lookups} but {source} by {source_lookups}"
            )
    return len(pairs)


def _feature_indices(table: Any) -> dict[str, list[int]]:
    indices: dict[str, list[int]] = {}
    for record in table.FeatureList.FeatureRecord or []:
        indices.setdefault(record.FeatureTag, []).extend(record.Feature.LookupListIndex or [])
    return indices


def _plan_gsub_tags(plan: GsubPlan) -> list[str]:
    return sorted(["calt", *plan.marker_lines, *plan.cosmetic_lines] + (["ss10"] if plan.ss10_input else []))


def _check_feature_list(plan: GsubPlan, gsub: Any, divergences: list[str]) -> dict[str, list[int]] | None:
    """Check the GSUB feature registration the plan implies: calt always, one stylistic-set feature per marker lookup, and ss10 when the plan has an ss10 input substitution. Beside them the font may register the features `FOREIGN_GSUB_FEATURES` names, which `_check_foreign` checks; any other tag is a divergence. feaLib writes the feature records sorted by tag, so an unsorted list is also a divergence. Returns each tag's lookup indices, or None when the tags or their lookup counts do not match the plan."""
    tags = [record.FeatureTag for record in gsub.FeatureList.FeatureRecord or []]
    expected = _plan_gsub_tags(plan)
    planned = sorted(tag for tag in tags if tag not in FOREIGN_GSUB_FEATURES)
    if planned != expected:
        divergences.append(
            f"feature list: GSUB registers {sorted(tags)}, expected {expected} and any of {list(FOREIGN_GSUB_FEATURES)}"
        )
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
    """Check that the stages run in the order the emitters defined them, and return each calt stage's LookupList index. The order is the ss10 input substitution first, so ss10 substitutes before formation; the formation stages; the marker substitutions, after formation so that enabling a set cannot undo a ligature; then the ZWNJ lock, settlement, and the namer dot. Shapers apply lookups in LookupList order, so increasing indices are all this check needs."""
    stages = dict(zip(plan.calt_stages, indices["calt"]))
    chain: list[tuple[str, int]] = []
    if plan.ss10_input:
        chain.append(("qs_ss10_isolated_input", indices["ss10"][0]))
    for name in plan.calt_stages:
        if name == "qs_zwnj":
            for feature in sorted(plan.marker_lines):
                chain.append((f"qs_{feature}_marker", indices[feature][0]))
        chain.append((name, stages[name]))
    for feature in sorted(plan.cosmetic_lines):
        chain.append((f"qs_{feature}_cosmetic", indices[feature][0]))
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
    """Check the late-formation guard's rows against the plan. Each row has literal input slots, no backtrack, and either no substitution (an `ignore sub` guard row) or one at sequence index 0 that resolves through the anonymous ligature lookup feaLib deduped the forming rows into."""
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


def _check_zwnj_lock(
    plan: GsubPlan, lookup: Any, lookups: list[Any], all_glyphs: frozenset[str], divergences: list[str]
) -> int:
    """Check the ZWNJ lock: one row that matches every entry-capable raw glyph after a ZWNJ and substitutes its locked copy, so no letter after a word boundary can join leftward."""
    stage = "zwnj lock"
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
            f"{stage}: the input class holds {sorted(row.input[0]) if len(row.input) == 1 else _slots_text(row.input)}, expected the {len(expected_input)} entry-capable glyphs"
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


def _contexts(slots: tuple[frozenset[str], ...]) -> int:
    """How many glyph tuples a rule's context slots match: the product of the slot sizes."""
    count = 1
    for slot in slots:
        count *= len(slot)
    return count


def _settle_text(rule: tuple) -> str:
    return f"{_slots_text(rule[0])} {_slots_text(rule[1])} -> {rule[2]}"


def _within(piece: tuple, rule: tuple) -> bool:
    """Whether a font rule can be a piece of a plan rule: the same outcome, as many backtrack and lookahead slots, and each slot a nonempty subset of the plan rule's."""
    return (
        piece[2] == rule[2]
        and len(piece[0]) == len(rule[0])
        and len(piece[1]) == len(rule[1])
        and all(part and part <= whole for part, whole in zip(piece[0] + piece[1], rule[0] + rule[1]))
    )


def _disjoint(one: tuple, other: tuple) -> bool:
    return any(not first & second for first, second in zip(one[0] + one[1], other[0] + other[1]))


def _dropped_zwnj(piece: tuple, rule: tuple) -> str | None:
    """The first slot, short of the farthest on its side, where the plan rule holds `uni200C` and the font rule does not, as `lookahead slot 2 of 3`, or None. Backtrack slots count outward from the input, as the font stores them. HarfBuzz skips a ZWNJ that a context slot does not hold and tries the slot on the glyph beyond it, so such a piece can skip a ZWNJ that the plan rule matches in that slot, read its later slots one glyph further on, and match a buffer the plan rule does not. Where the plan rule matches a ZWNJ in its farthest slot it has already matched, so a piece may drop `uni200C` there."""
    for side, parts, wholes in (("backtrack", piece[0], rule[0]), ("lookahead", piece[1], rule[1])):
        for slot, (part, whole) in enumerate(zip(parts[:-1], wholes[:-1])):
            if "uni200C" in whole and "uni200C" not in part:
                return f"{side} slot {slot + 1} of {len(wholes)}"
    return None


def _reassemble(glyph: str, want: list[tuple], have: list[tuple]) -> tuple[int, str | None]:
    """Match one input glyph's plan rules, in order, to runs of its consecutive font rules, as (backtrack, lookahead, outcome) triples. A plan rule's run starts at the first font rule its predecessor's run left, and takes font rules while they have its outcome and slot shape, lie inside its slots, miss every earlier piece of the run, and keep `uni200C` in every slot short of the farthest on a side where the plan rule holds it (`_dropped_zwnj`), until their contexts add up to all of the plan rule's. Pieces that lie inside a product and miss each other cover it exactly when their context counts sum to its own, so a run partitions its plan rule's contexts read as plain glyph positions. HarfBuzz also skips a ZWNJ that a context slot does not hold, and with `uni200C` kept in those slots a run matches exactly the buffers its plan rule matches, ZWNJs included, so first-match-wins picks the same outcome in the font as in the plan in every buffer. `pack_gsub` refines every rule into such pieces over its family's atoms; this walk never calls its refinement, so the refinement cannot check itself. Returns the rule count, with each run counted once and, after a failure, each font rule from the failed run on counted alone, and the first failure, or None when every plan rule has its run and no font rule is left over."""
    position = 0
    for index, rule in enumerate(want):
        start = position
        total = _contexts(rule[0] + rule[1])
        covered = 0
        while position == start or covered < total:
            piece = have[position] if position < len(have) else None
            if piece is None or not _within(piece, rule):
                if position == start:
                    found = (
                        "has no more font rules"
                        if piece is None
                        else f"font rule {position} is {_settle_text(piece)}"
                    )
                    problem = f"{glyph} {found}, expected a piece of plan rule {index}, {_settle_text(rule)}"
                else:
                    run = (
                        f"font rule {start} covers"
                        if position - start == 1
                        else f"font rules {start}–{position - 1} cover"
                    )
                    after = "" if piece is None else f", and font rule {position} is {_settle_text(piece)}"
                    problem = f"{glyph} {run} {covered:,} of the {total:,} contexts of plan rule {index}, {_settle_text(rule)}{after}"
                return index + len(have) - start, problem
            overlapped = next(
                (other for other in range(start, position) if not _disjoint(have[other], piece)), None
            )
            if overlapped is not None:
                return (
                    index + len(have) - start,
                    f"{glyph} font rule {position} overlaps font rule {overlapped}, both pieces of plan rule {index}, {_settle_text(rule)}",
                )
            dropped = _dropped_zwnj(piece, rule)
            if dropped is not None:
                return (
                    index + len(have) - start,
                    f"{glyph} font rule {position}, a piece of plan rule {index}, {_settle_text(rule)}, lacks uni200C in its {dropped}, where HarfBuzz would skip a ZWNJ that the plan rule matches",
                )
            covered += _contexts(piece[0] + piece[1])
            position += 1
    if position < len(have):
        return (
            len(want) + len(have) - position,
            f"{glyph} carries {len(have) - position} font rules past the pieces of its {len(want)} plan rules",
        )
    return len(want), None


def _check_settle(
    plan: GsubPlan, lookup: Any, lookups: list[Any], divergences: list[str]
) -> tuple[int, int, int]:
    """Compare settlement per input glyph: the ordered (backtrack, lookahead, outcome) triples the font holds, reassembled into the plan's rules by `_reassemble`, against the ones the plan emitted. Decompiling the written lookup through `pack_gsub.per_glyph_sequences` also checks the repack, since per-glyph order is what the packing must preserve, and the reassembly checks its refinement. Returns the reassembled rule count, the number of input glyphs, and the font's own rule count, in which each piece of a refined rule counts."""
    stage = "settle"
    expected: dict[str, list[tuple]] = {}
    for rule in plan.settle_rules:
        backtrack = (rule.backtrack,) if rule.backtrack else ()
        expected.setdefault(rule.input_glyph, []).append((backtrack, rule.lookahead, rule.outcome))
    try:
        sequences = pack_gsub.per_glyph_sequences(lookup)
    except pack_gsub.PackError as error:
        divergences.append(f"{stage}: the lookup does not decompile — {error}")
        return 0, 0, 0
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
    total = 0
    for glyph in sorted(set(expected) | set(got)):
        count, problem = _reassemble(glyph, expected.get(glyph, []), got.get(glyph, []))
        total += count
        if problem is None:
            continue
        reported += 1
        if reported <= 10:
            divergences.append(f"{stage}: {problem}")
    if total != plan.rule_count:
        divergences.append(f"{stage}: {total} rules in the font, expected the plan's {plan.rule_count}")
    return total, len(got), sum(len(rules) for rules in got.values())


def _check_namer_dot(
    plan: GsubPlan, lookup: Any, lookups: list[Any], all_glyphs: frozenset[str], divergences: list[str]
) -> int:
    """Check the namer-dot lookup: the ZWNJ guard row that keeps the dot from lowering across a word boundary, then, when the plan has mid-word glyphs, the row that keeps a dot after one of them (`plan.namer_midword`) at its height before a Short letter, then the row that lowers it before a Short letter."""
    stage = "namer dot"
    assert plan.namer_dot_stage is not None
    dot, lowered, followers = plan.namer_dot_stage
    rows, problems = _chain_rows(lookup, all_glyphs)
    for problem in problems:
        divergences.append(f"{stage}: {problem}")
    expected_rows = 3 if plan.namer_midword else 2
    if len(rows) != expected_rows:
        divergences.append(
            f"{stage}: {len(rows)} rows, expected the guard row, {'the mid-word row, ' if plan.namer_midword else ''}and the lowering row"
        )
        return len(rows)
    guard, *midword, lower = rows
    if guard.input != (frozenset({dot}),) or guard.lookahead != (frozenset({"uni200C"}),) or guard.records:
        divergences.append(
            f"{stage}: the guard row is {_slots_text(guard.input)} before {_slots_text(guard.lookahead)} with substitutions {list(guard.records)}, expected [{{{dot}}}] before [{{uni200C}}] with none"
        )
    for row in midword:
        if (
            row.backtrack != (plan.namer_midword,)
            or row.input != (frozenset({dot}),)
            or row.lookahead != (followers,)
            or row.records
        ):
            divergences.append(
                f"{stage}: the mid-word row is {len(row.backtrack[0]) if row.backtrack else 0} backtrack glyphs and {_slots_text(row.input)} before a {len(row.lookahead[0]) if row.lookahead else 0}-glyph class with substitutions {list(row.records)}, expected the plan's {len(plan.namer_midword)} mid-word glyphs and [{{{dot}}}] before the plan's {len(followers)} followers with none"
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


def _gpos_tags(kern: KernPlan | None) -> list[str]:
    """The GPOS feature tags the plan implies, in the sorted order feaLib writes them: `curs`, and `kern` when the kern plan has a rule."""
    return ["curs", KERN_FEATURE] if kern is not None and kern.rules else ["curs"]


def _check_gpos_feature_list(gpos: Any, kern: KernPlan | None, divergences: list[str]) -> bool:
    """Check that GPOS registers the features the plan implies (`_gpos_tags`) and otherwise only the ones `FOREIGN_GPOS_FEATURES` names, sorted as feaLib writes them. `kern` may be registered with no kern rule in the plan, for the Latin kern lookups alone; `_kern_partition` tells the plan's kern lookup from those. Returns whether the list passed."""
    tags = [record.FeatureTag for record in gpos.FeatureList.FeatureRecord or []]
    expected = _gpos_tags(kern)
    allowed = set(expected) | set(FOREIGN_GPOS_FEATURES)
    if not set(expected) <= set(tags) or not set(tags) <= allowed or tags != sorted(tags):
        divergences.append(
            f"feature list: GPOS registers {tags}, expected {' and '.join(expected)}, beside any of {list(FOREIGN_GPOS_FEATURES)}, sorted"
        )
        return False
    return True


def _kern_partition(gpos: Any, divergences: list[str]) -> tuple[list[int], list[int]]:
    """Split the `kern` feature's lookups into the plan's and the Latin ones by where they are defined. `compile_font` writes the Latin features ahead of the rune FEA, so a Latin kern lookup comes before every `curs` lookup and the plan's kern lookup after every one (`emit_gpos`); a kern lookup between two `curs` lookups is a divergence. Returns (the plan's, the Latin ones)."""
    indices = _feature_indices(gpos)
    curs = indices.get("curs", [])
    kern = indices.get(KERN_FEATURE, [])
    if not curs:
        return kern, []
    planned = [index for index in kern if index > max(curs)]
    latin = [index for index in kern if index < min(curs)]
    stray = sorted(set(kern) - set(planned) - set(latin))
    if stray:
        divergences.append(
            f"lookup order: kern lookups {stray} sit among the curs lookups {curs}, neither before them with the Latin features nor after them with the plan's"
        )
    return planned, latin


def _check_cursive(
    gpos: Any,
    cursive: Mapping[int, Mapping[str, Registration]],
    divergences: list[str],
    kern: KernPlan | None = None,
) -> dict[str, int]:
    """Check that GPOS registers the features the plan implies (`_check_gpos_feature_list`) and has one `curs` lookup per registered height that has any anchors, in height order, each a format-1 CursivePos whose entry and exit records equal the emitter's registrations for every glyph."""
    counts: dict[str, int] = {}
    if not _check_gpos_feature_list(gpos, kern, divergences):
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


def _single_values(lookup: Any) -> dict[str, int] | None:
    """The x-advance each glyph of a single-adjustment lookup gets, or None when the lookup holds anything but single adjustments that set the x-advance alone."""
    values: dict[str, int] = {}
    subtables = _unwrapped(lookup)
    if not subtables:
        return None
    for subtable in subtables:
        if type(subtable).__name__ != "SinglePos" or subtable.ValueFormat != 0x0004:
            return None
        glyphs = list(subtable.Coverage.glyphs)
        if subtable.Format == 1:
            for glyph in glyphs:
                values.setdefault(glyph, subtable.Value.XAdvance)
        else:
            for glyph, value in zip(glyphs, subtable.Value):
                values.setdefault(glyph, value.XAdvance)
    return values


def _anchored_glyphs(gpos: Any) -> tuple[frozenset[str], frozenset[str]]:
    """The glyphs any cursive-attachment subtable gives a non-NULL entry anchor, and those it gives a non-NULL exit anchor."""
    entries: set[str] = set()
    exits: set[str] = set()
    for lookup in gpos.LookupList.Lookup or []:
        for subtable in _unwrapped(lookup):
            if type(subtable).__name__ != "CursivePos":
                continue
            for glyph, record in zip(subtable.Coverage.glyphs, subtable.EntryExitRecord or []):
                if record.EntryAnchor is not None:
                    entries.add(glyph)
                if record.ExitAnchor is not None:
                    exits.add(glyph)
    return frozenset(entries), frozenset(exits)


def _relation_text(pairs: Sequence[tuple[tuple[str, str], int | None]]) -> str:
    return ", ".join(f"{lead}|{follow}={value}" for (lead, follow), value in pairs[:5])


def _check_kern(gpos: Any, plan: KernPlan | None, all_glyphs: frozenset[str], divergences: list[str]) -> dict:
    """Check the `kern` feature's planned lookup against the kern plan (doc/kerning-design.md section 5). The Latin kern lookups the feature also holds are defined before every `curs` lookup (`_kern_partition`) and checked as foreign lookups (`_check_foreign`). With no rule in the plan the feature holds no lookup after the `curs` lookups. Otherwise it holds one chained-context lookup there, whose rules each adjust their one input glyph through a single-adjustment value lookup registered in no feature, before either one following glyph (the adjacent shape) or `uni200C` and then one following glyph (the one-ZWNJ shape). Read-back rebuilds, rule by rule and first match first, the (first glyph, second glyph) to value relation each shape gives, the value being the x-advance the value lookup gives the first glyph, and compares each with the plan's relation. It also checks that the lookup cannot move a join: no glyph it adjusts has an exit anchor in any cursive lookup, and no glyph it reads after an adjusted one has an entry anchor."""
    stage = "kern"
    kern_indices, _latin = _kern_partition(gpos, [])
    if plan is None or not plan.rules:
        if kern_indices:
            divergences.append(
                f"{stage}: the kern feature carries lookups {kern_indices} after the curs lookups, but the plan has no kern rule"
            )
        return {}
    indices = _feature_indices(gpos)
    if len(kern_indices) != 1:
        divergences.append(
            f"{stage}: the kern feature carries lookups {kern_indices} after the curs lookups, expected exactly one"
        )
        return {}
    index = kern_indices[0]
    curs_indices = indices.get("curs", [])
    if curs_indices and index <= max(curs_indices):
        divergences.append(
            f"lookup order: the kern lookup is {index}, but curs lookups {curs_indices} are defined after it"
        )
    lookups = list(gpos.LookupList.Lookup or [])
    lookup = _lookup_at(lookups, index)
    if lookup is None:
        divergences.append(f"{stage}: registered as lookup {index}, which the lookup list does not hold")
        return {}
    rows, problems = _chain_rows(lookup, all_glyphs)
    for problem in problems:
        divergences.append(f"{stage}: {problem}")
    if problems:
        return {}
    registered = {lookup_index for found in indices.values() for lookup_index in found}
    value_maps: dict[int, dict[str, int] | None] = {}
    adjacent: dict[tuple[str, str], int] = {}
    across: dict[tuple[str, str], int] = {}
    adjusted: set[str] = set()
    followers: set[str] = set()
    shapes = {"adjacent": 0, "one-zwnj": 0}
    for row_index, row in enumerate(rows):
        lookahead = row.lookahead
        if len(lookahead) == 1:
            shape, relation, follow = "adjacent", adjacent, lookahead[0]
        elif len(lookahead) == 2 and lookahead[0] == frozenset({ZWNJ_GLYPH}):
            shape, relation, follow = "one-zwnj", across, lookahead[1]
        else:
            divergences.append(
                f"{stage}: rule {row_index} reads {_slots_text(lookahead)} after its input, which is neither a following glyph nor uni200C and one"
            )
            continue
        if row.backtrack or len(row.input) != 1 or len(row.records) != 1 or row.records[0][0] != 0:
            divergences.append(
                f"{stage}: rule {row_index} is not one input glyph adjusted by one value lookup with no backtrack"
            )
            continue
        value_index = row.records[0][1]
        if value_index in registered:
            divergences.append(
                f"{stage}: rule {row_index} invokes lookup {value_index}, which a feature registers"
            )
        if value_index not in value_maps:
            target = _lookup_at(lookups, value_index)
            value_maps[value_index] = None if target is None else _single_values(target)
        values = value_maps[value_index]
        if values is None:
            divergences.append(
                f"{stage}: rule {row_index} invokes lookup {value_index}, which is not a single adjustment of the x-advance"
            )
            continue
        shapes[shape] += 1
        followers |= follow
        for lead in row.input[0]:
            adjusted.add(lead)
            value = values.get(lead)
            if value is None:
                divergences.append(
                    f"{stage}: rule {row_index} adjusts {lead}, which its value lookup {value_index} does not cover"
                )
                continue
            for glyph in follow:
                relation.setdefault((lead, glyph), value)
    expected = plan.relation()
    for shape, relation in (("adjacent", adjacent), ("one-zwnj", across)):
        if relation == expected:
            continue
        missing = sorted((pair, expected[pair]) for pair in set(expected) - set(relation))
        extra = sorted((pair, relation[pair]) for pair in set(relation) - set(expected))
        moved = sorted(
            (pair, relation[pair])
            for pair in set(relation) & set(expected)
            if relation[pair] != expected[pair]
        )
        divergences.append(
            f"{stage} ({shape}): {len(relation)} kerned pairs, expected {len(expected)}; missing {_relation_text(missing)}; unplanned {_relation_text(extra)}; other values {_relation_text(moved)}"
        )
    entries, exits = _anchored_glyphs(gpos)
    exiting = sorted(adjusted & exits)
    if exiting:
        divergences.append(
            f"{stage}: the lookup adjusts glyphs with an exit anchor, which could move a join: {exiting[:5]}"
        )
    entering = sorted((followers - {ZWNJ_GLYPH}) & entries)
    if entering:
        divergences.append(
            f"{stage}: the lookup reads glyphs with an entry anchor after an adjusted glyph, which could move a join: {entering[:5]}"
        )
    return {
        "rules": len(rows),
        "adjacent_rules": shapes["adjacent"],
        "one_zwnj_rules": shapes["one-zwnj"],
        "value_lookups": len(value_maps),
        "pairs": len(adjacent),
        "values": [value_lookup_name(value) for value in plan.values],
    }


def rune_owned(all_glyphs: frozenset[str], plan: GsubPlan) -> frozenset[str]:
    """The glyphs the rune pipeline owns: every `qs` glyph (letters, cells, copies, the variation glyphs and the angle parentheses), the boundary glyphs, and the namer dot pair. A lookup the plan does not own may substitute none of them and position no pair of two of them (`_check_foreign`)."""
    dots = plan.namer_dot_stage[:2] if plan.namer_dot_stage is not None else NAMER_DOT_GLYPHS
    return frozenset(name for name in all_glyphs if name.startswith("qs")) | (
        frozenset((*BOUNDARY_GLYPHS, *dots)) & all_glyphs
    )


def _invoked(lookup: Any) -> list[int]:
    """The lookup indices a contextual lookup's rules invoke, in any of the three formats of a (chained) contextual GSUB or GPOS subtable."""
    found: list[int] = []
    for subtable in _unwrapped(lookup):
        holders: list[Any] = [subtable]
        for set_name, rule_name in (
            ("ChainSubRuleSet", "ChainSubRule"),
            ("ChainSubClassSet", "ChainSubClassRule"),
            ("SubRuleSet", "SubRule"),
            ("SubClassSet", "SubClassRule"),
            ("ChainPosRuleSet", "ChainPosRule"),
            ("ChainPosClassSet", "ChainPosClassRule"),
            ("PosRuleSet", "PosRule"),
            ("PosClassSet", "PosClassRule"),
        ):
            for rule_set in getattr(subtable, set_name, None) or []:
                if rule_set is not None:
                    holders.extend(getattr(rule_set, rule_name, None) or [])
        for holder in holders:
            found.extend(index for _sequence, index in _records_of(holder))
    return found


def _reach(lookups: list[Any], roots: Sequence[int]) -> set[int]:
    seen: set[int] = set()
    queue = list(roots)
    while queue:
        index = queue.pop()
        if index in seen or not 0 <= index < len(lookups):
            continue
        seen.add(index)
        queue.extend(_invoked(lookups[index]))
    return seen


def _partition(
    table: Any, plan_roots: Sequence[int], foreign_roots: Sequence[int], label: str, divergences: list[str]
) -> tuple[set[int], set[int]]:
    """Split a layout table's lookups into the plan's and the foreign ones: those its planned features reach, directly or through the rules of a contextual lookup, and those the foreign features reach. No lookup may be both or neither, and every foreign lookup comes before every planned one, which is where `compile_font` writes the Latin features. Returns (the planned lookups, the foreign ones)."""
    lookups = list(table.LookupList.Lookup or [])
    planned = _reach(lookups, plan_roots)
    foreign = _reach(lookups, foreign_roots)
    shared = sorted(planned & foreign)
    if shared:
        divergences.append(
            f"foreign lookups: {label} lookups {shared[:5]} are reached by both a planned and a foreign feature"
        )
    orphans = sorted(set(range(len(lookups))) - planned - foreign)
    if orphans:
        divergences.append(f"foreign lookups: {label} lookups {orphans[:5]} are reached by no feature")
    if planned and foreign and max(foreign) > min(planned):
        divergences.append(
            f"lookup order: {label} foreign lookup {max(foreign)} is defined after the plan's lookup {min(planned)}, so the Latin features no longer come first"
        )
    return planned, foreign - planned


def _substituted(subtable: Any) -> set[str] | None:
    """Every glyph a non-contextual GSUB subtable reads or writes, or None for a contextual subtable, whose substitutions are its invoked lookups'."""
    kind = type(subtable).__name__
    if kind == "SingleSubst":
        return set(subtable.mapping) | set(subtable.mapping.values())
    if kind == "MultipleSubst":
        return set(subtable.mapping) | {glyph for out in subtable.mapping.values() for glyph in out}
    if kind == "AlternateSubst":
        return set(subtable.alternates) | {glyph for out in subtable.alternates.values() for glyph in out}
    if kind == "LigatureSubst":
        return {
            glyph
            for first, ligatures in subtable.ligatures.items()
            for ligature in ligatures
            for glyph in (first, *ligature.Component, ligature.LigGlyph)
        }
    if kind == "ReverseChainSingleSubst":
        return set(subtable.Coverage.glyphs) | set(subtable.Substitute or [])
    return None


def _check_foreign_gsub(
    lookups: list[Any], foreign: set[int], owned: frozenset[str], divergences: list[str]
) -> None:
    """Check that no foreign GSUB lookup reads or writes a rune-owned glyph at a substituted position. A contextual lookup substitutes only through the lookups it invokes, which `_partition` holds to the foreign set, so each of those is checked here on its own."""
    for index in sorted(foreign):
        for subtable in _unwrapped(lookups[index]):
            touched = _substituted(subtable)
            if touched is None:
                continue
            hit = sorted(touched & owned)
            if hit:
                divergences.append(
                    f"foreign lookups: GSUB lookup {index} substitutes rune-owned glyphs {hit[:5]}, which only the plan's lookups may"
                )


def _moves(value: Any) -> bool:
    return value is not None and any(getattr(value, name, 0) for name in _VALUE_FIELDS)


def _single_movers(lookup: Any) -> set[str] | None:
    """The glyphs a single-adjustment lookup moves, or None when the lookup holds anything but single adjustments."""
    moved: set[str] = set()
    for subtable in _unwrapped(lookup):
        if type(subtable).__name__ != "SinglePos":
            return None
        glyphs = list(subtable.Coverage.glyphs)
        if subtable.Format == 1:
            if _moves(subtable.Value):
                moved.update(glyphs)
        else:
            moved.update(glyph for glyph, value in zip(glyphs, subtable.Value) if _moves(value))
    return moved


def _moved_pairs(subtable: Any, all_glyphs: frozenset[str]) -> list[tuple[frozenset[str], frozenset[str]]]:
    """The (first glyphs, second glyphs) of every pair a PairPos subtable moves either glyph of, a format-2 class 0 resolved as `_class_sets` resolves it."""
    pairs: list[tuple[frozenset[str], frozenset[str]]] = []
    coverage = frozenset(subtable.Coverage.glyphs)
    if subtable.Format == 1:
        for first, pair_set in zip(subtable.Coverage.glyphs, subtable.PairSet or []):
            seconds = frozenset(
                record.SecondGlyph
                for record in pair_set.PairValueRecord or []
                if _moves(record.Value1) or _moves(record.Value2)
            )
            if seconds:
                pairs.append((frozenset({first}), seconds))
        return pairs
    firsts = _class_sets(subtable.ClassDef1.classDefs, coverage)
    seconds = _class_sets(subtable.ClassDef2.classDefs, all_glyphs)
    for class1, record in enumerate(subtable.Class1Record or []):
        for class2, values in enumerate(record.Class2Record or []):
            if _moves(values.Value1) or _moves(values.Value2):
                pairs.append((firsts.get(class1, frozenset()) & coverage, seconds.get(class2, frozenset())))
    return pairs


def _check_foreign_gpos(
    gpos: Any,
    roots: Sequence[int],
    all_glyphs: frozenset[str],
    owned: frozenset[str],
    divergences: list[str],
) -> None:
    """Check that no foreign GPOS lookup positions a pair of two rune-owned glyphs. A foreign lookup a feature registers is checked by its type: a single adjustment may move no rune-owned glyph, since it moves it before every glyph; a pair adjustment no pair whose two sides are both rune-owned; a mark attachment no rune-owned mark on a rune-owned base; a cursive attachment no rune-owned glyph; and a chained-context lookup no rune-owned glyph its single-adjustment value lookup moves when the glyph after it can be rune-owned. So the Latin `kern` rule that kerns every glyph before `j` passes, and the old font's ·He-before-ZWNJ rule, which `compile_font` leaves out, would not. A lookup reached only through a rule is checked in that rule's context."""
    stage = "foreign lookups"
    lookups = list(gpos.LookupList.Lookup or [])
    for index in sorted(set(roots)):
        lookup = lookups[index]
        for subtable in _unwrapped(lookup):
            kind = type(subtable).__name__
            if kind == "SinglePos":
                hit = sorted((_single_movers(lookup) or set()) & owned)
                if hit:
                    divergences.append(
                        f"{stage}: GPOS lookup {index} moves rune-owned glyphs {hit[:5]} before any glyph"
                    )
                break
            if kind == "PairPos":
                for firsts, seconds in _moved_pairs(subtable, all_glyphs):
                    lead, follow = sorted(firsts & owned), sorted(seconds & owned)
                    if lead and follow:
                        divergences.append(
                            f"{stage}: GPOS lookup {index} kerns rune-owned pairs such as {lead[0]}|{follow[0]}, which only the plan's kern lookup may"
                        )
                        break
            elif kind in ("MarkBasePos", "MarkLigPos", "MarkMarkPos"):
                marks = frozenset(_either(subtable, "MarkCoverage", "Mark1Coverage").glyphs)
                bases = (
                    subtable.BaseCoverage
                    if kind == "MarkBasePos"
                    else _either(subtable, "LigatureCoverage", "Mark2Coverage")
                )
                mark_hit, base_hit = sorted(marks & owned), sorted(frozenset(bases.glyphs) & owned)
                if mark_hit and base_hit:
                    divergences.append(
                        f"{stage}: GPOS lookup {index} attaches rune-owned marks {mark_hit[:5]} to rune-owned glyphs {base_hit[:5]}"
                    )
            elif kind == "CursivePos":
                hit = sorted(frozenset(subtable.Coverage.glyphs) & owned)
                if hit:
                    divergences.append(
                        f"{stage}: GPOS lookup {index} gives rune-owned glyphs {hit[:5]} cursive anchors"
                    )
            elif kind == "ChainContextPos":
                rows, problems = _chain_rows(lookup, all_glyphs)
                for problem in problems:
                    divergences.append(f"{stage}: GPOS lookup {index} {problem}")
                for row_index, row in enumerate(rows):
                    for sequence, target in row.records:
                        movers = (
                            None if _lookup_at(lookups, target) is None else _single_movers(lookups[target])
                        )
                        if movers is None:
                            divergences.append(
                                f"{stage}: GPOS lookup {index} rule {row_index} invokes lookup {target}, which is not a single adjustment"
                            )
                            continue
                        moved = (
                            row.input[sequence] & movers & owned if sequence < len(row.input) else frozenset()
                        )
                        if sequence + 1 < len(row.input):
                            after = row.input[sequence + 1]
                        else:
                            after = row.lookahead[0] if row.lookahead else all_glyphs
                        if moved and after & owned:
                            divergences.append(
                                f"{stage}: GPOS lookup {index} rule {row_index} moves rune-owned glyphs {sorted(moved)[:5]} before rune-owned glyphs {sorted(after & owned)[:5]}"
                            )
                break
            else:
                divergences.append(
                    f"{stage}: GPOS lookup {index} subtable is {kind}, which read-back does not check"
                )


def _check_foreign(font: Any, plan: GsubPlan, all_glyphs: frozenset[str], divergences: list[str]) -> dict:
    """Check the lookups the plan does not own, the Latin features of the full font (`FOREIGN_GSUB_FEATURES`, `FOREIGN_GPOS_FEATURES`). Each table's lookups split cleanly into the plan's and the foreign ones, foreign first (`_partition`); no foreign GSUB lookup substitutes a rune-owned glyph (`rune_owned`, `_check_foreign_gsub`), and no foreign GPOS lookup positions a pair of two (`_check_foreign_gpos`). Returns each table's foreign lookup count and each foreign feature's registered lookup count, for `readback_summary.json`."""
    owned = rune_owned(all_glyphs, plan)
    counts: dict[str, Any] = {}
    if "GSUB" in font:
        gsub = font["GSUB"].table
        indices = _feature_indices(gsub)
        plan_roots = [index for tag in _plan_gsub_tags(plan) for index in indices.get(tag, [])]
        foreign_roots = [index for tag in FOREIGN_GSUB_FEATURES for index in indices.get(tag, [])]
        _planned, foreign = _partition(gsub, plan_roots, foreign_roots, "GSUB", divergences)
        _check_foreign_gsub(list(gsub.LookupList.Lookup or []), foreign, owned, divergences)
        counts["gsub_lookups"] = len(foreign)
        counts.update({tag: len(indices[tag]) for tag in FOREIGN_GSUB_FEATURES if tag in indices})
    if "GPOS" in font:
        gpos = font["GPOS"].table
        indices = _feature_indices(gpos)
        planned_kern, latin_kern = _kern_partition(gpos, divergences)
        plan_roots = [*indices.get("curs", []), *planned_kern]
        foreign_roots = [
            *latin_kern,
            *(
                index
                for tag in FOREIGN_GPOS_FEATURES
                if tag != KERN_FEATURE
                for index in indices.get(tag, [])
            ),
        ]
        _planned, foreign = _partition(gpos, plan_roots, foreign_roots, "GPOS", divergences)
        _check_foreign_gpos(gpos, foreign_roots, all_glyphs, owned, divergences)
        counts["gpos_lookups"] = len(foreign)
        counts["kern"] = len(latin_kern)
        counts.update(
            {
                tag: len(indices[tag])
                for tag in FOREIGN_GPOS_FEATURES
                if tag != KERN_FEATURE and tag in indices
            }
        )
    return counts


def _cmap14_sequences(font: Any) -> dict[int, dict[int, str]]:
    sequences: dict[int, dict[int, str]] = {}
    for subtable in font["cmap"].tables:
        if subtable.format != 14:
            continue
        for selector, entries in subtable.uvsDict.items():
            for base, glyph in entries:
                if glyph is not None:
                    sequences.setdefault(selector, {})[base] = glyph
    return sequences


def _check_variation_glyphs(
    font: Any,
    plan: GsubPlan,
    expected: Mapping[int, Mapping[int, str]],
    all_glyphs: frozenset[str],
    divergences: list[str],
) -> dict:
    """Check the variation sequences (`run_build.mint_variation_glyphs`): the font's cmap format 14 maps exactly the expected (selector, base code point) pairs to their glyphs, and each such glyph is in the font, is in none of settlement's classes or outcomes and no formation sequence, marker line, ZWNJ lock or ss10 or cosmetic substitution of the plan, and carries no cursive anchor. The stages above hold every planned lookup to the plan and `_check_foreign` holds every other lookup off the rune-owned glyphs, so no GSUB lookup reads or writes one at a substituted position either: a sequence selects a glyph that nothing settles, joins or redraws. The namer dot's follower class may hold one, which only lowers a dot before it."""
    stage = "variation sequences"
    got = _cmap14_sequences(font)
    want = {selector: dict(mapping) for selector, mapping in expected.items() if mapping}
    if got != want:
        divergences.append(f"{stage}: cmap format 14 maps {got}, expected {want}")
    glyphs = frozenset(name for mapping in want.values() for name in mapping.values())
    missing = sorted(glyphs - all_glyphs)
    if missing:
        divergences.append(f"{stage}: the font carries no glyph {missing[:5]}")
    named: set[str] = set()
    for rule in plan.settle_rules:
        named.update((rule.input_glyph, rule.outcome, *(rule.backtrack or ())))
        named.update(glyph for slot in rule.lookahead for glyph in slot)
    for row in plan.formation_guarded_rows:
        named.update(row.sequence)
        named.update(glyph for slot in row.lookahead for glyph in slot)
        if row.ligature is not None:
            named.add(row.ligature)
    for sequence, ligature in plan.formation_plain:
        named.update((*sequence, ligature))
    for pairs in (
        *plan.marker_lines.values(),
        *plan.cosmetic_lines.values(),
        plan.ss10_input,
        plan.locked_glyphs,
    ):
        named.update(pairs)
        named.update(pairs.values())
    settled = sorted(glyphs & named)
    if settled:
        divergences.append(f"{stage}: the plan's settlement, formation or substitutions name {settled[:5]}")
    if "GPOS" in font:
        anchored = sorted(
            name for name, found in _cursive_coverage(font["GPOS"].table, glyphs).items() if found
        )
        if anchored:
            divergences.append(f"{stage}: cursive lookups cover {anchored[:5]}")
    return {"sequences": sum(len(mapping) for mapping in want.values()), "glyphs": len(glyphs)}


def _filled_bands(glyph_set: Any, name: str) -> list[tuple[int, int, tuple[tuple[int, int], ...]]]:
    """A glyph's outline as horizontal bands, each (bottom, top, its filled x intervals): the outline's own y coordinates cut it into bands, and the nonzero winding of the vertical edges at a band's middle gives the intervals. Every outline here is a union of axis-aligned rectangles traced into straight lines (`font_assembly.union_contours`), so the bands describe it exactly."""
    from fontTools.pens.recordingPen import RecordingPen

    pen = RecordingPen()
    glyph_set[name].draw(pen)
    edges: list[tuple[int, int, int, int]] = []
    start: tuple[int, int] | None = None
    current: tuple[int, int] | None = None

    def edge(end: tuple[int, int]) -> None:
        assert current is not None
        (x0, y0), (x1, y1) = current, end
        if x0 != x1 and y0 != y1:
            raise ValueError(f"{name}: the outline holds a diagonal line, which no pixel outline draws")
        if x0 == x1 and y0 != y1:
            edges.append((x0, min(y0, y1), max(y0, y1), 1 if y1 > y0 else -1))

    for operator, points in pen.value:
        if operator == "moveTo":
            start = current = points[0]
        elif operator == "lineTo" and current is not None:
            edge(points[0])
            current = points[0]
        elif operator in ("closePath", "endPath") and current is not None and start is not None:
            edge(start)
            current = start
        else:
            raise ValueError(f"{name}: the outline holds a {operator}, which no pixel outline draws")
    ys = sorted({y for _x, bottom, top, _direction in edges for y in (bottom, top)})
    bands: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
    for bottom, top in zip(ys, ys[1:]):
        middle = (bottom + top) / 2
        crossings = sorted((x, direction) for x, low, high, direction in edges if low < middle < high)
        intervals: list[tuple[int, int]] = []
        winding = 0
        left = 0
        for x, direction in crossings:
            before = winding
            winding += direction
            if before == 0 and winding != 0:
                left = x
            elif before != 0 and winding == 0:
                intervals.append((left, x))
        bands.append((bottom, top, tuple(intervals)))
    return bands


def _overstruck(
    bands: list[tuple[int, int, tuple[tuple[int, int], ...]]], overstrike: int
) -> list[tuple[int, int, tuple[tuple[int, int], ...]]]:
    """The bands with every interval widened by `overstrike` units to the right and overlapping intervals merged, which is what Bold draws (`font_assembly.build_font(bold=True)`)."""
    widened: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
    for bottom, top, intervals in bands:
        merged: list[list[int]] = []
        for left, right in intervals:
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right + overstrike)
            else:
                merged.append([left, right + overstrike])
        widened.append((bottom, top, tuple((left, right) for left, right in merged)))
    return widened


def _coalesced(
    bands: list[tuple[int, int, tuple[tuple[int, int], ...]]],
) -> list[tuple[int, int, tuple[tuple[int, int], ...]]]:
    """The bands with each run of vertically adjacent bands that share their intervals joined into one, so two outlines cut at different y coordinates compare alike."""
    joined: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
    for bottom, top, intervals in bands:
        if not intervals:
            continue
        if joined and joined[-1][1] == bottom and joined[-1][2] == intervals:
            joined[-1] = (joined[-1][0], top, intervals)
        else:
            joined.append((bottom, top, intervals))
    return joined


def verify_bold(
    regular_path: Path, bold_path: Path, overstrike: int = PIXEL // 2, divergences: list[str] | None = None
) -> dict:
    """Check Bold against Regular (`compile_font.build_font(bold_path=…)`): the two fonts hold byte-identical GDEF, GSUB and GPOS tables, the same `usMaxContext`, the same cmap table and glyph order, and every glyph's advance, and Bold draws each glyph as Regular's outline with every filled run `overstrike` units wider to the right (`_filled_bands`, `_overstruck`). Read-back's plan checks on Regular then hold for Bold too. Appends each divergence to `divergences` and returns the counts it checked."""
    from fontTools.ttLib import TTFont

    stage = "bold"
    found = divergences if divergences is not None else []
    regular = TTFont(str(regular_path), lazy=True)
    bold = TTFont(str(bold_path), lazy=True)
    try:
        regular_reader, bold_reader = cast(Any, regular.reader), cast(Any, bold.reader)
        for tag in (*LAYOUT_TABLES, "cmap"):
            if (tag in regular_reader) != (tag in bold_reader):
                found.append(f"{stage}: {tag} is in one font and not the other")
            elif tag in regular_reader and regular_reader[tag] != bold_reader[tag]:
                found.append(f"{stage}: Bold's {tag} is not byte for byte Regular's")
        if (
            regular["OS/2"].usMaxContext != bold["OS/2"].usMaxContext
        ):  # pyright: ignore[reportAttributeAccessIssue]
            found.append(
                f"{stage}: Bold's usMaxContext {bold['OS/2'].usMaxContext} is not Regular's {regular['OS/2'].usMaxContext}"
            )  # pyright: ignore[reportAttributeAccessIssue]
        order = regular.getGlyphOrder()
        if bold.getGlyphOrder() != order:
            found.append(f"{stage}: Bold's glyph order is not Regular's")
            return {"glyphs": 0}
        regular_metrics = regular["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
        bold_metrics = bold["hmtx"].metrics  # pyright: ignore[reportAttributeAccessIssue]
        regular_set, bold_set = regular.getGlyphSet(), bold.getGlyphSet()
        widened = 0
        for name in order:
            if regular_metrics[name][0] != bold_metrics[name][0]:
                found.append(
                    f"{stage}: {name} advances {bold_metrics[name][0]} in Bold and {regular_metrics[name][0]} in Regular"
                )
            try:
                expected = _coalesced(_overstruck(_filled_bands(regular_set, name), overstrike))
                drawn = _coalesced(_filled_bands(bold_set, name))
            except ValueError as error:
                found.append(f"{stage}: {error}")
                continue
            if drawn != expected:
                found.append(
                    f"{stage}: Bold's {name} is not Regular's outline widened {overstrike} units to the right"
                )
            elif expected:
                widened += 1
        return {"glyphs": len(order), "overstruck": widened, "overstrike": overstrike}
    finally:
        regular.close()
        bold.close()


def verify_font(
    font_path: Path,
    plan: GsubPlan,
    cursive: Mapping[int, Mapping[str, Registration]],
    kern: KernPlan | None = None,
    variation_sequences: Mapping[int, Mapping[int, str]] | None = None,
    bold_path: Path | None = None,
) -> dict:
    """Re-parse the font at `font_path` and run every check in this module against the emitters' plan: registrations, lookup order, lookupFlags, lookup contents, the kern lookup against `kern` (`_check_kern`; with no plan or a plan with no rule the font must carry no planned kern lookup), the boundary glyphs, the overlay's isolation, the GSUB offset budget, the settlement lookup's largest packed group, the lookups the plan does not own (`_check_foreign`), the variation sequences against `variation_sequences` (`_check_variation_glyphs`; with none the font must carry no cmap format 14), and Bold against Regular when `bold_path` names it (`verify_bold`). Returns the JSON-ready report `run_build` writes to `readback_summary.json`. Divergences are collected in the report, not raised: `divergences` holds the first `MAX_DIVERGENCES` messages and, when there are more, one line saying how many more, and `divergence_count` holds the total."""
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
                if plan.ss10_input:
                    ss10_input = _stage_lookup(
                        lookups, indices["ss10"][0], "ss10 input substitution", divergences
                    )
                    if ss10_input is not None:
                        checked["ss10_substitutions"] = _check_single_stage(
                            "ss10 input substitution", ss10_input, plan.ss10_input, divergences
                        )
                        exempt = frozenset(
                            index for name, index in stages.items() if name == "qs_namer_dot_word_start"
                        )
                        checked["isolation"] = _check_isolation(
                            font, plan, lookups, all_glyphs, ss10_input, exempt, divergences
                        )
                marker_substitutions = 0
                for feature in sorted(plan.marker_lines):
                    marker = _stage_lookup(lookups, indices[feature][0], f"marker {feature}", divergences)
                    if marker is not None:
                        marker_substitutions += _check_single_stage(
                            f"marker {feature}", marker, plan.marker_lines[feature], divergences
                        )
                checked["marker_substitutions"] = marker_substitutions
                if plan.cosmetic_lines:
                    checked["cosmetic_substitutions"] = _check_cosmetic(
                        font, plan, lookups, indices, divergences
                    )
                for stage_name, stage_index in stages.items():
                    lookup = _stage_lookup(lookups, stage_index, stage_name, divergences)
                    if lookup is None:
                        continue
                    if stage_name == "qs_formation_guarded":
                        checked["guarded_rows"] = _check_guarded_formation(
                            plan, lookup, lookups, all_glyphs, divergences
                        )
                    elif stage_name == "qs_formation":
                        checked["plain_ligatures"] = _check_plain_formation(plan, lookup, divergences)
                    elif stage_name == "qs_zwnj":
                        checked["zwnj_lock_members"] = _check_zwnj_lock(
                            plan, lookup, lookups, all_glyphs, divergences
                        )
                    elif stage_name == "qs_settle":
                        settle_rules, settle_inputs, settle_pieces = _check_settle(
                            plan, lookup, lookups, divergences
                        )
                        checked["settle_rules"] = settle_rules
                        checked["settle_input_glyphs"] = settle_inputs
                        checked["settle_font_rules"] = settle_pieces
                        formats = [getattr(subtable, "Format", None) for subtable in _unwrapped(lookup)]
                        checked["settle_subtable_formats"] = {
                            "format2": formats.count(2),
                            "format3": formats.count(3),
                        }
                        group_bytes, group_index = pack_gsub.largest_group(lookup)
                        checked["gsub_budget"].update(
                            largest_group_rule_bytes=group_bytes,
                            largest_group_subtable_index=group_index,
                            group_rule_bytes_ceiling=GROUP_RULE_BYTES_CEILING,
                        )
                        if group_bytes > GROUP_RULE_BYTES_CEILING:
                            divergences.append(
                                f"gsub budget: settlement subtable {group_index} holds {group_bytes:,} bytes of format-2 rules, over the {GROUP_RULE_BYTES_CEILING:,}-byte ceiling"
                            )
                    elif stage_name == "qs_namer_dot_word_start":
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
            checked["cursive_anchors"] = _check_cursive(gpos, cursive, divergences, kern)
            checked["kern"] = _check_kern(gpos, kern, all_glyphs, divergences)
            if plan.ss10_input:
                checked["anchorless_copies"] = _check_anchorless_copies(
                    gpos, frozenset(plan.ss10_input.values()), divergences
                )
            if plan.cosmetic_lines:
                checked["cosmetic_registrations"] = _check_cosmetic_registrations(gpos, plan, divergences)
        checked["foreign_lookups"] = _check_foreign(font, plan, all_glyphs, divergences)
        checked["variation_sequences"] = _check_variation_glyphs(
            font, plan, variation_sequences or {}, all_glyphs, divergences
        )
    finally:
        font.close()
    if bold_path is not None:
        checked["bold"] = verify_bold(font_path, bold_path, divergences=divergences)

    reported = divergences[:MAX_DIVERGENCES]
    if len(divergences) > MAX_DIVERGENCES:
        reported.append(f"… and {len(divergences) - MAX_DIVERGENCES} more")
    return {
        "pass": not divergences,
        "font": str(font_path),
        "checked": checked,
        "divergences": reported,
        "divergence_count": len(divergences),
    }


def budget_figures(report: Mapping) -> dict[str, int]:
    """Return the settlement lookup's size figures from a `verify_font` report, under the names the timings journal records them by (`cycle_timings.READBACK_TOKENS`): its format-2 and format-3 subtable counts, whose sum N is what the subtable-offset headroom is spent on, that headroom, the GSUB lookup count, the merged rule count, and the largest packed group's rule bytes. A figure the report lacks, as when the font has no settlement lookup, is left out."""
    checked = report.get("checked") or {}
    budget = checked.get("gsub_budget") or {}
    formats = checked.get("settle_subtable_formats") or {}
    figures = {
        "settle_format2": formats.get("format2"),
        "settle_format3": formats.get("format3"),
        "subtable_offset_headroom": budget.get("subtable_offset_headroom"),
        "gsub_lookups": budget.get("lookups"),
        "settle_rules": checked.get("settle_rules"),
        "largest_group_rule_bytes": budget.get("largest_group_rule_bytes"),
    }
    return {name: int(value) for name, value in figures.items() if isinstance(value, int)}
