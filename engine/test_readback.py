"""Read-back tests over a real build of the mini fixture spec. The fixture runs the whole emit and compile path, so the font under test has every stage the shipped font has: the ss10 input substitution, the guarded and plain formation lookups, the marker lookups, the ZWNJ lock, a packed settlement lookup that holds refined rules, and the namer dot. A clean build must verify with zero divergences. Each corruption test edits the compiled font so it no longer matches the plan, and checks that read-back reports the mismatch under the right stage name."""

import dataclasses
import math
from types import SimpleNamespace
from typing import Any

import pytest

from engine.pipeline import (
    compile_font,
    conform,
    emit_gpos,
    emit_gsub,
    fingerprint,
    fixtures,
    geometry,
    kern,
    kernel_exec,
    readback,
    run_build,
)
from engine.pipeline.model import KernBlock, KernFollower, KernRecord, VariationSelector
from engine.tools import cycle_timings as ct

CONFIGS = ("default", "ss03")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    spec = fixtures.mini_spec()
    tables: dict[str, tuple] = {
        config: kernel_exec.build_tables(spec, conform.features_for_config(config)) for config in CONFIGS
    }
    cell_glyphs = run_build.mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = run_build.mint_raw_glyphs(spec)
    dots = run_build.namer_dot_glyphs()
    curs_glyphs = {**cell_glyphs, **bare, **copies}
    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_copies=ss10_copies)
    gpos_fea = emit_gpos.emit_gpos(curs_glyphs, spec=spec)
    font_path = compile_font.build_font(
        {**curs_glyphs, **dots},
        gsub_plan.fea_text + "\n" + gpos_fea,
        tmp_path_factory.mktemp("senior-readback") / "SeniorReadback.otf",
    )
    cursive = emit_gpos.cursive_registrations(curs_glyphs, spec=spec)
    return font_path, gsub_plan, cursive, ss10_copies


class TestOverlayCase:
    """The conformance sweep's overlay case on the real mini font. Under ss10, HarfBuzz shapes every text up to `OVERLAY_MAX_LENGTH` as per-letter copies at zero offset with their `hmtx` advances, with nothing formed and nothing attached."""

    def test_the_overlay_case_passes_on_the_mini_font(self, built):
        spec = fixtures.mini_spec()
        font_path, _plan, _cursive, _copies = built
        result = conform._conformance_config(
            conform.Shaper(font_path),
            spec,
            "ss10",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            {},
            None,
            conform.SWEEP_MAX_LENGTH,
            None,
        )
        assert result.divergences_by_kind == {}
        assert result.exemplars == []
        alphabet = len(conform.spec_alphabet(spec))
        assert result.sequences == alphabet + alphabet**2


def _feature_record(font, table_tag, feature_tag):
    for record in font[table_tag].table.FeatureList.FeatureRecord:
        if record.FeatureTag == feature_tag:
            return record
    raise AssertionError(f"{table_tag} registers no {feature_tag} feature")


def _stage_index(font, plan, stage):
    calt = _feature_record(font, "GSUB", "calt")
    return calt.Feature.LookupListIndex[plan.calt_stages.index(stage)]


def _inner(subtable):
    return getattr(subtable, "ExtSubTable", subtable)


def _compiled_size(font, table):
    from fontTools.ttLib.tables.otBase import OTTableWriter

    writer = OTTableWriter()
    table.compile(writer, font)
    return len(writer.getAllData())


def _rule_tables_size(font, subtable):
    return sum(
        _compiled_size(font, rule)
        for class_set in subtable.ChainSubClassSet or []
        if class_set is not None
        for rule in class_set.ChainSubClassRule
    )


def _corrupted_report(built, tmp_path, name, mutate):
    from fontTools.ttLib import TTFont

    font_path, plan, cursive, _copies = built
    out_path = tmp_path / f"{name}.otf"
    font = TTFont(str(font_path))
    try:
        mutate(font, plan)
        font.save(str(out_path))
    finally:
        font.close()
    return readback.verify_font(out_path, plan, cursive)


def _named(report, needle):
    return [line for line in report["divergences"] if needle in line]


def _plan_shapes(plan, glyph):
    return [
        ((rule.backtrack,) if rule.backtrack else (), rule.lookahead)
        for rule in plan.settle_rules
        if rule.input_glyph == glyph
    ]


def _whole_rule_site(sequences, plan):
    """The first input glyph whose font rules are its planned rules, unsplit, with a rule followed by another and holding a slot of two or more glyphs: (glyph, rule index, `backtrack` or `lookahead`, slot index)."""
    for glyph in sorted(sequences):
        rules = sequences[glyph]
        if [(rule.backtrack, rule.lookahead) for rule in rules] != _plan_shapes(plan, glyph):
            continue
        for index, rule in enumerate(rules[:-1]):
            for side in ("backtrack", "lookahead"):
                for slot, members in enumerate(getattr(rule, side)):
                    if len(members) > 1:
                        return glyph, index, side, slot
    raise AssertionError(
        "the fixture's settlement lookup holds no unsplit rule with a slot of two or more glyphs"
    )


def _with_slot(rule, side, slot, members):
    slots = list(getattr(rule, side))
    slots[slot] = members
    return dataclasses.replace(rule, **{side: tuple(slots)})


def _rewrite_settlement(font, plan, edit):
    """Replace the settlement lookup's subtables with one Extension-wrapped format-3 subtable per rule, from its decompiled per-glyph sequences once `edit` has changed them in place, input glyph by input glyph in sorted order."""
    from engine.pipeline import pack_gsub

    ot = pack_gsub._ot()
    lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_settle")]
    sequences = pack_gsub.per_glyph_sequences(lookup)
    edit(sequences)

    def coverage(glyphs):
        table = ot.Coverage()
        table.glyphs = sorted(glyphs, key=font.getGlyphID)
        return table

    wrappers = []
    for glyph in sorted(sequences):
        for rule in sequences[glyph]:
            subtable = ot.ChainContextSubst()
            subtable.Format = 3
            subtable.BacktrackCoverage = [coverage(members) for members in rule.backtrack]
            subtable.BacktrackGlyphCount = len(rule.backtrack)
            subtable.InputCoverage = [coverage({glyph})]
            subtable.InputGlyphCount = 1
            subtable.LookAheadCoverage = [coverage(members) for members in rule.lookahead]
            subtable.LookAheadGlyphCount = len(rule.lookahead)
            subtable.SubstLookupRecord = []
            for sequence_index, lookup_index in rule.records:
                record = ot.SubstLookupRecord()
                record.SequenceIndex = sequence_index
                record.LookupListIndex = lookup_index
                subtable.SubstLookupRecord.append(record)
            subtable.SubstCount = len(subtable.SubstLookupRecord)
            wrapper = ot.ExtensionSubst()
            wrapper.Format = 1
            wrapper.ExtensionLookupType = 6
            wrapper.ExtSubTable = subtable
            wrappers.append(wrapper)
    lookup.SubTable = wrappers
    lookup.SubTableCount = len(wrappers)


class TestReadback:
    def test_a_clean_build_verifies(self, built):
        font_path, plan, cursive, _copies = built
        report = readback.verify_font(font_path, plan, cursive)
        assert report["divergences"] == []
        assert report["pass"]
        assert report["checked"]["settle_rules"] == plan.rule_count
        assert report["checked"]["settle_font_rules"] > plan.rule_count
        assert report["checked"]["guarded_rows"] == len(plan.formation_guarded_rows) > 0
        assert report["checked"]["cursive_anchors"]

    def test_the_isolation_claim_is_recorded_off_the_bytes(self, built):
        """The isolation record: every letter cmap glyph has a copy, no copy appears at any position of another GSUB lookup except the namer-dot stage, and no copy has a cursive anchor. The record counts the positions it checked."""
        from fontTools.ttLib import TTFont

        font_path, plan, cursive, copies = built
        report = readback.verify_font(font_path, plan, cursive)
        isolation = report["checked"]["isolation"]
        font = TTFont(str(font_path))
        try:
            letters = {
                name
                for name in (font.getBestCmap() or {}).values()
                if name not in ("space", "uni200C", "periodcentered")
            }
        finally:
            font.close()
        assert letters == set(copies)
        assert isolation == {
            "cmap_letters": len(letters),
            "copies": len(copies),
            "positions_checked": isolation["positions_checked"],
        }
        assert isolation["positions_checked"] > report["checked"]["settle_rules"]
        assert report["checked"]["anchorless_copies"] == len(copies)

    def test_the_plan_carries_every_stage_in_definition_order(self, built):
        _font_path, plan, _cursive, copies = built
        assert plan.calt_stages == (
            "qs_formation_guarded",
            "qs_formation",
            "qs_zwnj",
            "qs_settle",
            "qs_namer_dot_word_start",
        )
        assert len(plan.settle_rules) == plan.rule_count > 0
        assert plan.ss10_input == dict(copies)
        assert sorted(plan.marker_lines) == ["ss02", "ss03", "ss04", "ss05"]
        assert plan.formation_plain == ((("qsTea", "qsOy"), "qsTea_qsOy"),)
        assert plan.namer_dot_stage is not None and plan.namer_dot_stage[0] == "periodcentered"

    def test_settlement_resolves_through_lookups_between_the_zwnj_lock_and_settlement(self, built):
        """Every settlement rule's outcome is a single-substitution lookup that the emitter defines after the ZWNJ lock and before the settlement lookup. The lookups between those two stages are these, plus the one feaLib creates for the ZWNJ lock's inline `by`, directly after the ZWNJ lock. The k-th of them maps each input glyph to the k-th distinct outcome its planned rules name, so there are as many as the most outcomes any input glyph takes."""
        from fontTools.ttLib import TTFont

        from engine.pipeline import pack_gsub

        font_path, plan, _cursive, _copies = built
        font = TTFont(str(font_path))
        try:
            lookups = font["GSUB"].table.LookupList.Lookup
            zwnj_lock = _stage_index(font, plan, "qs_zwnj")
            settlement = _stage_index(font, plan, "qs_settle")
            assert zwnj_lock + 1 < settlement
            sequences = pack_gsub.per_glyph_sequences(lookups[settlement])
            assert sequences
            reached: set[int] = set()
            for rules in sequences.values():
                for rule in rules:
                    assert len(rule.records) == 1 and rule.records[0][0] == 0
                    reached.add(rule.records[0][1])
            outcome_lookups = range(zwnj_lock + 2, settlement)
            assert reached == set(outcome_lookups)
            assert readback._single_mapping(lookups[zwnj_lock + 1]) is not None
            for index in outcome_lookups:
                assert lookups[index].LookupType == 1 and lookups[index].SubTableCount == 1
            mappings = [readback._single_mapping(lookups[index]) or {} for index in outcome_lookups]
        finally:
            font.close()
        first_seen: dict[str, list[str]] = {}
        for rule in plan.settle_rules:
            outcomes = first_seen.setdefault(rule.input_glyph, [])
            if rule.outcome not in outcomes:
                outcomes.append(rule.outcome)
        assert len(mappings) == max(len(outcomes) for outcomes in first_seen.values()) > 1
        for glyph, outcomes in first_seen.items():
            assert [mapping[glyph] for mapping in mappings if glyph in mapping] == outcomes
            assert all(glyph in mapping for mapping in mappings[: len(outcomes)])
        assert set().union(*mappings) == set(first_seen)

    def test_the_offset_budget_is_read_off_the_raw_table(self, built):
        """The lookup and subtable counts from the raw GSUB byte walk match the decoded table's counts, which checks that the walk reads the right uint16 fields. The same parse records how many settlement subtables use formats 2 and 3."""
        from fontTools.ttLib import TTFont

        font_path, plan, cursive, _copies = built
        report = readback.verify_font(font_path, plan, cursive)
        budget = report["checked"]["gsub_budget"]
        font = TTFont(str(font_path))
        try:
            lookup_list = font["GSUB"].table.LookupList
            assert budget["lookups"] == lookup_list.LookupCount
            assert budget["subtables"] == sum(lookup.SubTableCount for lookup in lookup_list.Lookup)
            settle_subtables = lookup_list.Lookup[_stage_index(font, plan, "qs_settle")].SubTableCount
        finally:
            font.close()
        assert 0 < budget["subtable_offset_headroom"] <= 65_535
        assert budget["floor"] == readback.SUBTABLE_OFFSET_HEADROOM_FLOOR
        formats = report["checked"]["settle_subtable_formats"]
        assert formats["format2"] >= 1
        assert formats["format3"] == 0
        assert formats["format2"] + formats["format3"] == settle_subtables

    def test_the_largest_group_is_measured_in_serialized_rule_bytes(self, built):
        """The largest-group figure is the serialized size of that subtable's ChainSubClassRule tables, which each rule's own compile measures independently of read-back's count arithmetic, and it names the largest format-2 settlement subtable."""
        from fontTools.ttLib import TTFont

        font_path, plan, cursive, _copies = built
        budget = readback.verify_font(font_path, plan, cursive)["checked"]["gsub_budget"]
        font = TTFont(str(font_path))
        try:
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_settle")]
            sizes = {
                index: _rule_tables_size(font, _inner(subtable))
                for index, subtable in enumerate(lookup.SubTable)
                if _inner(subtable).Format == 2
            }
        finally:
            font.close()
        assert budget["largest_group_rule_bytes"] == max(sizes.values()) > 0
        assert sizes[budget["largest_group_subtable_index"]] == max(sizes.values())
        assert budget["group_rule_bytes_ceiling"] == readback.GROUP_RULE_BYTES_CEILING == 49_151

    def test_the_boundary_glyphs_are_inert_on_the_bytes(self, built):
        """Checked once on the written font instead of at every shaped ZWNJ slot: no substituted position of any lookup admits a boundary glyph, `uni200C` has zero advance, and neither boundary glyph draws an outline."""
        font_path, plan, cursive, _copies = built
        report = readback.verify_font(font_path, plan, cursive)
        boundary = report["checked"]["boundary_glyphs"]
        assert boundary["substituted_positions"] > 0
        assert boundary["uni200C"]["advance"] == 0
        assert boundary["uni200C"]["inked"] is False
        assert boundary["space"]["inked"] is False
        assert report["pass"]

    def test_the_budget_figures_are_the_reports_own_under_the_names_the_journal_reads(self, built):
        """`budget_figures` hands the timings journal the report's own figures, under exactly the names `cycle_timings.READBACK_TOKENS` reads, so a renamed report key cannot drop a figure from the journal unnoticed. A report with no settlement lookup gives no figure."""
        font_path, plan, cursive, _copies = built
        report = readback.verify_font(font_path, plan, cursive)
        checked = report["checked"]
        figures = readback.budget_figures(report)
        assert figures == {
            "settle_format2": checked["settle_subtable_formats"]["format2"],
            "settle_format3": checked["settle_subtable_formats"]["format3"],
            "subtable_offset_headroom": checked["gsub_budget"]["subtable_offset_headroom"],
            "gsub_lookups": checked["gsub_budget"]["lookups"],
            "settle_rules": checked["settle_rules"],
            "largest_group_rule_bytes": checked["gsub_budget"]["largest_group_rule_bytes"],
        }
        assert tuple(figures) == ct.READBACK_FIGURES
        assert readback.budget_figures({"pass": False, "checked": {}}) == {}

    def test_verification_is_deterministic(self, built):
        font_path, plan, cursive, _copies = built
        assert readback.verify_font(font_path, plan, cursive) == readback.verify_font(
            font_path, plan, cursive
        )


class TestCorruptions:
    def test_unregistering_settlement_from_calt(self, built, tmp_path):
        def mutate(font, plan):
            calt = _feature_record(font, "GSUB", "calt").Feature
            del calt.LookupListIndex[plan.calt_stages.index("qs_settle")]
            calt.LookupCount = len(calt.LookupListIndex)

        report = _corrupted_report(built, tmp_path, "unregistered-settle", mutate)
        assert not report["pass"]
        assert _named(report, "calt registration:")

    def test_permuting_the_calt_stage_order(self, built, tmp_path):
        def mutate(font, _plan):
            calt = _feature_record(font, "GSUB", "calt").Feature
            calt.LookupListIndex[0], calt.LookupListIndex[1] = (
                calt.LookupListIndex[1],
                calt.LookupListIndex[0],
            )

        report = _corrupted_report(built, tmp_path, "permuted-calt", mutate)
        assert not report["pass"]
        assert _named(report, "lookup order:")

    def test_a_nonzero_lookup_flag(self, built, tmp_path):
        def mutate(font, plan):
            index = _stage_index(font, plan, "qs_settle")
            font["GSUB"].table.LookupList.Lookup[index].LookupFlag = 8

        report = _corrupted_report(built, tmp_path, "flagged-settle", mutate)
        assert not report["pass"]
        flagged = _named(report, "lookupFlag:")
        assert flagged and "LookupFlag 8" in flagged[0]

    def test_retargeting_a_settlement_outcome(self, built, tmp_path):
        def mutate(font, plan):
            from engine.pipeline import pack_gsub

            lookups = font["GSUB"].table.LookupList.Lookup
            settle = lookups[_stage_index(font, plan, "qs_settle")]
            sequences = pack_gsub.per_glyph_sequences(settle)
            glyph = sorted(sequences)[0]
            inner = _inner(lookups[sequences[glyph][0].records[0][1]].SubTable[0])
            inner.mapping[glyph] = "qsPea"

        report = _corrupted_report(built, tmp_path, "retargeted-settle", mutate)
        assert not report["pass"]
        assert _named(report, "settle:")

    def test_dropping_a_guarded_formation_subtable(self, built, tmp_path):
        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_formation_guarded")]
            lookup.SubTable = lookup.SubTable[:-1]
            lookup.SubTableCount = len(lookup.SubTable)

        report = _corrupted_report(built, tmp_path, "short-formation", mutate)
        assert not report["pass"]
        assert _named(report, "formation guarded:")

    def test_reordering_packed_settlement_rules(self, built, tmp_path):
        """First match wins, so a lookup that holds every planned rule in the wrong order shapes differently. The font holds the same rules as the clean build and only their order is wrong, so read-back reports a run that does not reassemble into its plan rule. The packing could produce this corruption, and read-back's decompile through `per_glyph_sequences` is there to catch it."""

        def mutate(font, plan):
            from engine.pipeline import pack_gsub

            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_settle")]
            before = pack_gsub.per_glyph_sequences(lookup)
            for subtable in lookup.SubTable:
                inner = _inner(subtable)
                if inner.Format != 2:
                    continue
                for class_set in inner.ChainSubClassSet or []:
                    if class_set is None or len(class_set.ChainSubClassRule) < 2:
                        continue
                    class_set.ChainSubClassRule.reverse()
                    if pack_gsub.per_glyph_sequences(lookup) != before:
                        return
                    class_set.ChainSubClassRule.reverse()
            subtables = lookup.SubTable
            format3 = [index for index, subtable in enumerate(subtables) if _inner(subtable).Format == 3]
            for position, first in enumerate(format3):
                for second in format3[position + 1 :]:
                    one, other = _inner(subtables[first]), _inner(subtables[second])
                    if not set(one.InputCoverage[0].glyphs) & set(other.InputCoverage[0].glyphs):
                        continue
                    if pack_gsub._format3_rule(one) == pack_gsub._format3_rule(other):
                        continue
                    subtables[first], subtables[second] = subtables[second], subtables[first]
                    if pack_gsub.per_glyph_sequences(lookup) != before:
                        return
                    subtables[first], subtables[second] = subtables[second], subtables[first]
            pytest.fail(
                "the fixture's settlement lookup holds no two rules whose order any glyph can tell apart"
            )

        report = _corrupted_report(built, tmp_path, "reordered-settle", mutate)
        font_path, plan, cursive, _copies = built
        assert not report["pass"]
        settled = _named(report, "settle:")
        assert settled and "of plan rule" in settled[0]
        clean = readback.verify_font(font_path, plan, cursive)
        assert report["checked"]["settle_font_rules"] == clean["checked"]["settle_font_rules"]

    @pytest.mark.parametrize(
        ("arrangement", "expected"),
        [
            ("consecutive", None),
            ("overlapping", "overlaps"),
            ("partial", "cover"),
            ("out of order", "cover"),
        ],
    )
    def test_split_pieces_reassemble_only_into_the_rule_they_partition(
        self, built, tmp_path, arrangement, expected
    ):
        """Read-back reassembles a plan rule from a run of consecutive font rules that lie inside it, miss each other, and cover it. The test rewrites the settlement lookup as one format-3 subtable per rule and splits a whole planned rule with a slot of two or more glyphs into the rest of that slot and one glyph of it. Consecutive pieces verify. Pieces that overlap, that leave a glyph of the slot uncovered, or whose second piece sits after the next planned rule are each a divergence."""
        site: dict[str, Any] = {}

        def mutate(font, plan):
            def split(sequences):
                glyph, index, side, slot = _whole_rule_site(sequences, plan)
                rule, after = sequences[glyph][index], sequences[glyph][index + 1]
                members = getattr(rule, side)[slot]
                single = frozenset({min(members)})
                rest, alone = _with_slot(rule, side, slot, members - single), _with_slot(
                    rule, side, slot, single
                )
                pieces = {
                    "consecutive": [rest, alone, after],
                    "overlapping": [rest, rule, after],
                    "partial": [rest, after],
                    "out of order": [rest, after, alone],
                }[arrangement]
                sequences[glyph][index : index + 2] = pieces
                site.update(
                    glyph=glyph,
                    index=index,
                    contexts=math.prod(len(member) for member in rule.backtrack + rule.lookahead),
                    remaining=math.prod(len(member) for member in rest.backtrack + rest.lookahead),
                )

            _rewrite_settlement(font, plan, split)

        report = _corrupted_report(built, tmp_path, f"split-{arrangement.replace(' ', '-')}", mutate)
        _font_path, plan, _cursive, _copies = built
        glyph, index = site["glyph"], site["index"]
        settled = _named(report, "settle:")
        if expected is None:
            assert report["divergences"] == []
            assert report["checked"]["settle_rules"] == plan.rule_count
            assert report["checked"]["settle_subtable_formats"] == {
                "format2": 0,
                "format3": report["checked"]["settle_font_rules"],
            }
            return
        assert not report["pass"]
        if expected == "overlaps":
            assert settled[0].startswith(
                f"settle: {glyph} font rule {index + 1} overlaps font rule {index}, both pieces of plan rule {index}, "
            )
        else:
            assert settled[0].startswith(
                f"settle: {glyph} font rule {index} covers {site['remaining']:,} of the {site['contexts']:,} contexts of plan rule {index}, "
            )

    def test_a_piece_that_drops_zwnj_from_a_nearer_slot_does_not_reassemble(self):
        """HarfBuzz skips a ZWNJ that a context slot does not hold, so a piece without `uni200C` in a slot where its plan rule holds it, short of the farthest on its side, can match a buffer the plan rule does not, though the pieces partition its glyph tuples. Without it in the farthest slot it cannot."""
        zwnj_or_b, wide = frozenset({"B", "uni200C"}), frozenset({"B", "C", "uni200C"})
        rest, after = frozenset({"C"}), frozenset({"D"})

        count, problem = readback._reassemble(
            "A",
            [((), (zwnj_or_b, wide, after), "A.alt1")],
            [((), (zwnj_or_b, rest, after), "A.alt1"), ((), (zwnj_or_b, zwnj_or_b, after), "A.alt1")],
        )

        assert count == 2
        assert problem is not None and problem.startswith("A font rule 0, a piece of plan rule 0, ")
        assert "lacks uni200C in its lookahead slot 2 of 3" in problem
        assert readback._reassemble(
            "A",
            [((), (zwnj_or_b, wide), "A.alt1")],
            [((), (zwnj_or_b, rest), "A.alt1"), ((), (zwnj_or_b, zwnj_or_b), "A.alt1")],
        ) == (1, None)

    def test_a_headroom_under_the_floor_is_a_divergence(self, built, monkeypatch):
        """Raising the floor above the uint16 range makes a clean font fall under it. That must be reported as a single named divergence, not raised as an exception."""
        font_path, plan, cursive, _copies = built
        monkeypatch.setattr(readback, "SUBTABLE_OFFSET_HEADROOM_FLOOR", 65_536)
        report = readback.verify_font(font_path, plan, cursive)
        assert not report["pass"]
        breached = _named(report, "gsub budget:")
        assert len(breached) == 1 and "65,536-byte floor" in breached[0]
        assert report["divergences"] == breached

    def test_a_packed_group_over_the_ceiling_is_a_divergence(self, built, tmp_path):
        """A format-2 settlement subtable whose rule tables pass `GROUP_RULE_BYTES_CEILING` is reported under the gsub budget, naming the subtable. The test pads one group with copies of one of its rules, each with a longer lookahead than the last, so every copy is a distinct table the serializer cannot share, and stops once the group passes the ceiling, well under the size hb.repack can serialize."""
        from copy import copy

        grown: dict[str, int] = {}

        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_settle")]
            index, inner = next(
                (index, _inner(subtable))
                for index, subtable in enumerate(lookup.SubTable)
                if _inner(subtable).Format == 2 and _inner(subtable).LookAheadClassDef.classDefs
            )
            class_set = next(class_set for class_set in inner.ChainSubClassSet if class_set is not None)
            rule = class_set.ChainSubClassRule[0]
            pad = min(inner.LookAheadClassDef.classDefs.values())
            size = _rule_tables_size(font, inner)
            while size <= readback.GROUP_RULE_BYTES_CEILING:
                padded = copy(rule)
                padded.LookAhead = list(rule.LookAhead or []) + [pad] * len(class_set.ChainSubClassRule)
                padded.LookAheadGlyphCount = len(padded.LookAhead)
                class_set.ChainSubClassRule.append(padded)
                size += _compiled_size(font, padded)
            class_set.ChainSubClassRuleCount = len(class_set.ChainSubClassRule)
            grown.update(index=index, size=size)

        report = _corrupted_report(built, tmp_path, "oversize-group", mutate)
        assert not report["pass"]
        budget = report["checked"]["gsub_budget"]
        assert budget["largest_group_rule_bytes"] == grown["size"]
        assert budget["largest_group_subtable_index"] == grown["index"]
        assert _named(report, "gsub budget:") == [
            f"gsub budget: settlement subtable {grown['index']} holds {grown['size']:,} bytes of format-2 rules, over the 49,151-byte ceiling"
        ]

    def test_the_report_counts_every_divergence_past_the_trimmed_list(self, built, monkeypatch):
        """When more divergences are found than `MAX_DIVERGENCES`, `divergences` holds the first ones and one line saying how many more, and `divergence_count` holds the total."""
        font_path, plan, cursive, _copies = built
        displaced = {
            y: {glyph: ((-1, -1), (-1, -1)) for glyph in registrations}
            for y, registrations in cursive.items()
        }
        monkeypatch.setattr(readback, "MAX_DIVERGENCES", 10_000)
        total = len(readback.verify_font(font_path, plan, displaced)["divergences"])
        assert total > 2
        monkeypatch.setattr(readback, "MAX_DIVERGENCES", 1)
        report = readback.verify_font(font_path, plan, displaced)
        assert report["divergence_count"] == total
        assert len(report["divergences"]) == 2
        assert report["divergences"][-1] == f"… and {total - 1} more"

    def test_a_single_substitution_of_the_zwnj(self, built, tmp_path):
        """An ss10 input substitution that substitutes the ZWNJ would replace a word boundary with a drawn letter. Read-back reports it without a shaping sweep."""

        def mutate(font, _plan):
            index = _feature_record(font, "GSUB", "ss10").Feature.LookupListIndex[0]
            _inner(font["GSUB"].table.LookupList.Lookup[index].SubTable[0]).mapping["uni200C"] = "qsPea"

        report = _corrupted_report(built, tmp_path, "zwnj-single-subst", mutate)
        assert not report["pass"]
        named = _named(report, "boundary glyphs:")
        assert named and any("uni200C" in line for line in named)

    def test_a_settlement_input_class_admitting_space(self, built, tmp_path):
        """A packed settlement rule whose input class includes a space would fire on a word boundary, where nothing may substitute. The test classes the space with a populated input class and adds it to the subtable's coverage."""

        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_settle")]
            inner = next(
                candidate
                for candidate in (_inner(subtable) for subtable in lookup.SubTable)
                if candidate.Format == 2
            )
            populated = next(
                index
                for index, class_set in enumerate(inner.ChainSubClassSet)
                if index > 0 and class_set is not None
            )
            inner.InputClassDef.classDefs["space"] = populated
            inner.Coverage.glyphs = sorted(set(inner.Coverage.glyphs) | {"space"}, key=font.getGlyphID)

        report = _corrupted_report(built, tmp_path, "space-in-input", mutate)
        assert not report["pass"]
        named = _named(report, "boundary glyphs:")
        assert named and any("space" in line for line in named)

    def test_a_format2_class_zero_lead_admitting_the_zwnj(self, built, tmp_path):
        """Class 0 of a ClassDef is every glyph it does not name, so a format-2 rule set on class 0 can substitute a lead slot that admits the ZWNJ. The test copies rules onto class 0 and adds the ZWNJ to the subtable's coverage. `uni200C` is absent from the InputClassDef, so it is in class 0."""

        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_settle")]
            inner = next(
                candidate
                for candidate in (_inner(subtable) for subtable in lookup.SubTable)
                if candidate.Format == 2
            )
            assert "uni200C" not in inner.InputClassDef.classDefs
            populated = next(
                index
                for index, class_set in enumerate(inner.ChainSubClassSet)
                if index > 0 and class_set is not None
            )
            inner.ChainSubClassSet[0] = inner.ChainSubClassSet[populated]
            inner.Coverage.glyphs = sorted(set(inner.Coverage.glyphs) | {"uni200C"}, key=font.getGlyphID)

        report = _corrupted_report(built, tmp_path, "class-zero-lead", mutate)
        assert not report["pass"]
        named = _named(report, "boundary glyphs:")
        assert named and any("uni200C" in line for line in named)

    def test_a_zwnj_with_an_advance(self, built, tmp_path):
        """The ZWNJ has to occupy no width, or every word boundary in the font moves the letters after it."""

        def mutate(font, _plan):
            font["hmtx"]["uni200C"] = (100, 0)

        report = _corrupted_report(built, tmp_path, "wide-zwnj", mutate)
        assert not report["pass"]
        named = _named(report, "boundary glyphs:")
        assert named and any("advance" in line for line in named)

    def test_an_inked_zwnj(self, built, tmp_path):
        """The ZWNJ has to draw nothing, or a word boundary shows up on the page as a letter."""

        def mutate(font, _plan):
            charstrings = font["CFF "].cff[0].CharStrings
            charstrings["uni200C"] = charstrings["qsPea"]

        report = _corrupted_report(built, tmp_path, "inked-zwnj", mutate)
        assert not report["pass"]
        named = _named(report, "boundary glyphs:")
        assert named and any("ink" in line for line in named)

    def test_dropping_the_ss10_feature(self, built, tmp_path):
        def mutate(font, _plan):
            table = font["GSUB"].table
            records = table.FeatureList.FeatureRecord
            index = [record.FeatureTag for record in records].index("ss10")
            del records[index]
            table.FeatureList.FeatureCount = len(records)
            for script in table.ScriptList.ScriptRecord:
                langsys = script.Script.DefaultLangSys
                langsys.FeatureIndex = [value for value in langsys.FeatureIndex if value != index]
                langsys.FeatureCount = len(langsys.FeatureIndex)

        report = _corrupted_report(built, tmp_path, "no-ss10", mutate)
        assert not report["pass"]
        assert _named(report, "feature list:")

    def test_a_letter_the_ss10_input_leaves_in_the_join_pipeline(self, built, tmp_path):
        """A cmap letter with no copy would still form, settle and attach under ss10. The isolation stage finds it from the cmap and the ss10 input substitution's mapping, independent of the plan."""

        def mutate(font, plan):
            stray = next(iter(plan.marker_lines["ss03"].values()))
            for table in font["cmap"].tables:
                table.cmap[0xE6FF] = stray

        report = _corrupted_report(built, tmp_path, "uncovered-letter", mutate)
        assert not report["pass"]
        named = _named(report, "isolation:")
        assert named and any("stay in the join pipeline" in line for line in named)

    def test_a_copy_with_a_cmap_entry(self, built, tmp_path):
        def mutate(font, plan):
            copy = next(iter(plan.ss10_input.values()))
            for table in font["cmap"].tables:
                table.cmap[0xE6FF] = copy

        report = _corrupted_report(built, tmp_path, "encoded-copy", mutate)
        assert not report["pass"]
        named = _named(report, "isolation:")
        assert named and any("cmap entries of their own" in line for line in named)

    def test_a_copy_in_a_formation_sequence(self, built, tmp_path):
        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_formation")]
            subtable = _inner(lookup.SubTable[0])
            subtable.ligatures[plan.ss10_input["qsTea"]] = subtable.ligatures["qsTea"]

        report = _corrupted_report(built, tmp_path, "copy-forms", mutate)
        assert not report["pass"]
        named = _named(report, "isolation:")
        assert named and any("forms" in line and "names a copy" in line for line in named)

    def test_a_copy_in_a_marker_line(self, built, tmp_path):
        def mutate(font, plan):
            index = _feature_record(font, "GSUB", "ss03").Feature.LookupListIndex[0]
            mapping = _inner(font["GSUB"].table.LookupList.Lookup[index].SubTable[0]).mapping
            mapping[plan.ss10_input["qsTea"]] = next(iter(plan.marker_lines["ss03"].values()))

        report = _corrupted_report(built, tmp_path, "copy-marker", mutate)
        assert not report["pass"]
        named = _named(report, "isolation:")
        assert named and any("substitutes a copy" in line for line in named)

    def test_a_copy_in_the_zwnj_lock_class(self, built, tmp_path):
        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "qs_zwnj")]
            inner = next(
                candidate
                for candidate in (_inner(subtable) for subtable in lookup.SubTable)
                if candidate.Format == 3
            )
            glyphs = inner.InputCoverage[0].glyphs
            glyphs.append(plan.ss10_input["qsTea"])
            glyphs.sort(key=font.getGlyphID)

        report = _corrupted_report(built, tmp_path, "copy-zwnj-lock", mutate)
        assert not report["pass"]
        named = _named(report, "isolation:")
        assert named and any("input slot" in line and "admits a copy" in line for line in named)

    def test_a_copy_with_a_cursive_anchor(self, built, tmp_path):
        def mutate(font, plan):
            index = _feature_record(font, "GPOS", "curs").Feature.LookupListIndex[0]
            subtable = _inner(font["GPOS"].table.LookupList.Lookup[index].SubTable[0])
            copy = plan.ss10_input["qsTea"]
            subtable.Coverage.glyphs = sorted(set(subtable.Coverage.glyphs) | {copy}, key=font.getGlyphID)
            record = subtable.EntryExitRecord[0]
            subtable.EntryExitRecord.insert(subtable.Coverage.glyphs.index(copy), record)
            subtable.EntryExitCount = len(subtable.EntryExitRecord)

        report = _corrupted_report(built, tmp_path, "anchored-copy", mutate)
        assert not report["pass"]
        named = _named(report, "isolation:")
        assert named and any("cursive anchors on copies" in line for line in named)

    def test_moving_a_cursive_anchor(self, built, tmp_path):
        def mutate(font, _plan):
            index = _feature_record(font, "GPOS", "curs").Feature.LookupListIndex[0]
            subtable = _inner(font["GPOS"].table.LookupList.Lookup[index].SubTable[0])
            anchor = next(
                record.EntryAnchor for record in subtable.EntryExitRecord if record.EntryAnchor is not None
            )
            anchor.XCoordinate += 50

        report = _corrupted_report(built, tmp_path, "moved-anchor", mutate)
        assert not report["pass"]
        assert _named(report, "cursive y0:")

    def test_dropping_a_cursive_registration(self, built, tmp_path):
        def mutate(font, _plan):
            index = _feature_record(font, "GPOS", "curs").Feature.LookupListIndex[0]
            subtable = _inner(font["GPOS"].table.LookupList.Lookup[index].SubTable[0])
            del subtable.Coverage.glyphs[0]
            del subtable.EntryExitRecord[0]
            subtable.EntryExitCount = len(subtable.EntryExitRecord)

        report = _corrupted_report(built, tmp_path, "short-coverage", mutate)
        assert not report["pass"]
        assert _named(report, "cursive y0:")


KERN_RECORDS = {
    "qsPea": (KernRecord(rune="qsPea", right=KernFollower(family=("qsIt", "qsLow", "qsDay")), by=-1),),
    "qsIt": (KernRecord(rune="qsIt", right=KernFollower(family=("qsPea",)), by=-1),),
}
IT, PEA, ZWNJ = chr(0xE670), chr(0xE650), "‌"


@pytest.fixture(scope="module")
def kerned(tmp_path_factory):
    """The mini fixture with two kern records, compiled twice from one set of tables: with the kern lookup and without it. The mini ·May's isolated cell keeps an exit, which the kern stage refuses for an ss10 copy, so only the letters whose isolated cells have no live side take part in the overlay's pairs here."""
    base = fixtures.mini_spec()
    runes = {
        name: (
            dataclasses.replace(rune, kern=KernBlock(records=KERN_RECORDS[name], inherits=rune.kern.inherits))
            if name in KERN_RECORDS
            else rune
        )
        for name, rune in base.runes.items()
    }
    spec = dataclasses.replace(base, runes=runes)
    tables: dict[str, tuple] = {
        config: kernel_exec.build_tables(spec, conform.features_for_config(config)) for config in CONFIGS
    }
    cell_glyphs = run_build.mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = run_build.mint_raw_glyphs(spec)
    dots = run_build.namer_dot_glyphs()
    sources = {
        name: copy
        for name, copy in ss10_copies.items()
        if geometry.isolated_cell(spec, name).entry is None
        and geometry.isolated_cell(spec, name).exit is None
    }
    resolution = kern.resolve(spec, tables, cell_glyphs, sources)
    plan = emit_gpos.kern_plan(resolution, cell_glyphs)
    curs_glyphs = {**cell_glyphs, **bare, **copies}
    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_copies=ss10_copies)
    directory = tmp_path_factory.mktemp("senior-kern")
    fonts = {}
    for name, kern_plan in (("kerned", plan), ("plain", None)):
        fonts[name] = compile_font.build_font(
            {**curs_glyphs, **dots},
            gsub_plan.fea_text + "\n" + emit_gpos.emit_gpos(curs_glyphs, spec=spec, kern=kern_plan),
            directory / f"Senior{name.title()}.otf",
        )
    kern.write_classes(resolution, cell_glyphs, directory)
    return SimpleNamespace(
        spec=spec,
        plan=plan,
        gsub_plan=gsub_plan,
        cursive=emit_gpos.cursive_registrations(curs_glyphs, spec=spec),
        fonts=fonts,
        cell_glyphs=cell_glyphs,
        sides=conform.load_kern_sides(directory / kern.KERN_CLASSES_JSON),
    )


class TestTheKernLookup:
    def test_a_kerned_build_reads_back_clean_in_both_rule_shapes(self, kerned):
        report = readback.verify_font(
            kerned.fonts["kerned"], kerned.gsub_plan, kerned.cursive, kern=kerned.plan
        )
        assert report["pass"], report["divergences"]
        checked = report["checked"]["kern"]
        assert checked["pairs"] == len(kerned.plan.relation()) > 0
        assert checked["adjacent_rules"] == checked["one_zwnj_rules"] == len(kerned.plan.rules)

    def test_read_back_expects_the_kern_feature_exactly_when_the_plan_has_a_rule(self, kerned):
        plain = readback.verify_font(
            kerned.fonts["plain"], kerned.gsub_plan, kerned.cursive, kern=kerned.plan
        )
        assert any("expected curs and kern" in line for line in plain["divergences"])
        unexpected = readback.verify_font(kerned.fonts["kerned"], kerned.gsub_plan, kerned.cursive)
        assert any("but the plan has no kern rule" in line for line in unexpected["divergences"])
        clean = readback.verify_font(
            kerned.fonts["plain"], kerned.gsub_plan, kerned.cursive, kern=emit_gpos.KernPlan()
        )
        assert clean["pass"], clean["divergences"]

    def test_a_wrong_value_is_reported_for_both_rule_shapes(self, kerned, tmp_path):
        from fontTools.ttLib import TTFont

        font = TTFont(str(kerned.fonts["kerned"]))
        for lookup in font["GPOS"].table.LookupList.Lookup:
            for subtable in lookup.SubTable:
                subtable = getattr(subtable, "ExtSubTable", subtable)
                if type(subtable).__name__ == "SinglePos" and subtable.Format == 1:
                    subtable.Value.XAdvance -= 50
        corrupted = tmp_path / "corrupted.otf"
        font.save(str(corrupted))
        report = readback.verify_font(corrupted, kerned.gsub_plan, kerned.cursive, kern=kerned.plan)
        assert not report["pass"]
        assert any(line.startswith("kern (adjacent)") for line in report["divergences"])
        assert any(line.startswith("kern (one-zwnj)") for line in report["divergences"])

    def test_harfbuzz_kerns_a_break_once_adjacent_across_one_zwnj_and_under_ss10(self, kerned):
        shaper = conform.Shaper(kerned.fonts["kerned"])
        none = frozenset()
        plain = shaper.shape(IT + PEA, none, kern=False)[0]["x_advance"]
        assert shaper.shape(IT + PEA, none)[0]["x_advance"] == plain - 50
        assert shaper.shape(IT + ZWNJ + PEA, none)[0]["x_advance"] == plain - 50
        assert shaper.shape(IT + ZWNJ + " " + PEA, none)[0]["x_advance"] == plain
        assert shaper.shape(IT + ZWNJ, none)[0]["x_advance"] == plain
        ss10 = frozenset({"ss10"})
        copy = shaper.shape(IT + PEA, ss10, kern=False)[0]["x_advance"]
        assert shaper.shape(IT + PEA, ss10)[0]["x_advance"] == copy - 50
        assert shaper.shape(IT + ZWNJ + PEA, ss10)[0]["x_advance"] == copy - 50

    def test_the_sweep_checks_every_break_against_the_compiled_assignment(self, kerned):
        spec = kerned.spec
        glyphs = kerned.cell_glyphs

        def sweep(sides):
            return conform._conformance_config(
                conform.Shaper(kerned.fonts["kerned"]),
                spec,
                "default",
                conform.spec_alphabet(spec),
                conform.splitting_boundary_chars(spec),
                {cell: record.name for cell, record in glyphs.items()},
                conform.anchors_in_font_units({record.name: record for record in glyphs.values()}),
                3,
                None,
                kern_sides=sides,
            )

        assert sweep(kerned.sides).divergences_by_kind == {}
        unkerned = conform.KernSides(
            kerned.sides.leading, kerned.sides.following, {pair: 0 for pair in kerned.sides.values}
        )
        assert set(sweep(unkerned).divergences_by_kind) >= {"kern", "split"}
        uncovered = conform.KernSides({}, kerned.sides.following, kerned.sides.values)
        assert "kern-side" in sweep(uncovered).divergences_by_kind
        dropped = min(pair for pair, value in kerned.sides.values.items() if value == 0)
        missing = conform.KernSides(
            kerned.sides.leading,
            kerned.sides.following,
            {pair: value for pair, value in kerned.sides.values.items() if pair != dropped},
        )
        assert set(missing.leading.values()) >= {dropped[0]} and set(missing.following.values()) >= {
            dropped[1]
        }
        assert "kern-side" in sweep(missing).divergences_by_kind

    def test_the_kern_lookup_moves_the_font_content_digest(self, kerned):
        plain, kerned_font = kerned.fonts["plain"], kerned.fonts["kerned"]
        assert fingerprint.font_content_digest(plain) != fingerprint.font_content_digest(kerned_font)


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    """The mini fixture with two kern records and a variation sequence, compiled as run_build compiles the shipped font: with the non-Quikscript part of glyph_data/ (`compile_font.load_font_sources`), the namer dot's mid-word glyphs, the variation glyphs, and Bold beside Regular."""
    base = fixtures.mini_spec()
    runes = {
        name: (
            dataclasses.replace(rune, kern=KernBlock(records=KERN_RECORDS[name], inherits=rune.kern.inherits))
            if name in KERN_RECORDS
            else rune
        )
        for name, rune in base.runes.items()
    }
    registry = dataclasses.replace(
        base.registry,
        variation_sequences={0xFE01: VariationSelector(fonts=("senior",), runes={"qsTea": "half"})},
    )
    spec = dataclasses.replace(base, runes=runes, registry=registry)
    tables: dict[str, tuple] = {
        config: kernel_exec.build_tables(spec, conform.features_for_config(config)) for config in CONFIGS
    }
    cell_glyphs = run_build.mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = run_build.mint_raw_glyphs(spec)
    variation = run_build.mint_variation_glyphs(spec, cell_glyphs)
    dots = run_build.namer_dot_glyphs()
    sources = compile_font.load_font_sources()
    eligible = {
        name: copy
        for name, copy in ss10_copies.items()
        if geometry.isolated_cell(spec, name).entry is None
        and geometry.isolated_cell(spec, name).exit is None
    }
    kern_plan = emit_gpos.kern_plan(kern.resolve(spec, tables, cell_glyphs, eligible), cell_glyphs)
    curs_glyphs = {**cell_glyphs, **bare, **copies}
    gsub_plan = emit_gsub.emit_gsub(
        spec,
        tables,
        glyphs={**cell_glyphs, **bare, **variation.glyphs},
        ss10_copies=ss10_copies,
        namer_midword=sources.midword_glyphs(),
    )
    directory = tmp_path_factory.mktemp("senior-full")
    font_path = compile_font.build_font(
        {**curs_glyphs, **variation.glyphs, **dots},
        gsub_plan.fea_text + "\n" + emit_gpos.emit_gpos(curs_glyphs, spec=spec, kern=kern_plan),
        directory / "Senior-Regular.otf",
        sources=sources,
        variation_sequences=variation.sequences,
        bold_path=directory / "Senior-Bold.otf",
    )
    return SimpleNamespace(
        font=font_path,
        bold=directory / "Senior-Bold.otf",
        plan=gsub_plan,
        kern=kern_plan,
        cursive=emit_gpos.cursive_registrations(curs_glyphs, spec=spec),
        variation=variation,
    )


def _full_report(full, font=None, **overrides):
    arguments = {
        "kern": full.kern,
        "variation_sequences": full.variation.sequences,
        "bold_path": full.bold,
        **overrides,
    }
    return readback.verify_font(font or full.font, full.plan, full.cursive, **arguments)


def _full_corrupted(full, tmp_path, mutate, **overrides):
    from fontTools.ttLib import TTFont

    font = TTFont(str(full.font))
    try:
        mutate(font)
        out_path = tmp_path / "Senior-Regular.otf"
        font.save(str(out_path))
    finally:
        font.close()
    return _full_report(full, out_path, **{"bold_path": None, **overrides})


def _feature_lookups(font, table, tag):
    return list(_feature_record(font, table, tag).Feature.LookupListIndex)


class TestTheFullFont:
    """Read-back over a font that carries the Latin features the plan does not own (`readback._check_foreign`), the namer dot's mid-word row, a variation sequence, and Bold."""

    def test_a_clean_full_build_verifies_and_records_the_foreign_lookups(self, full):
        report = _full_report(full)
        assert report["divergences"] == []
        checked = report["checked"]
        foreign = checked["foreign_lookups"]
        assert foreign["gsub_lookups"] > 0 and foreign["ccmp"] > 0
        assert (
            foreign["kern"] > 0
            and foreign["mark"] > 0
            and foreign["gpos_lookups"] >= foreign["kern"] + foreign["mark"]
        )
        assert checked["namer_rows"] == 3
        assert checked["kern"]["pairs"] == len(full.kern.relation()) > 0
        assert checked["variation_sequences"] == {"sequences": 1, "glyphs": 1}
        assert checked["bold"]["overstruck"] > 0
        assert checked["isolation"]["cmap_letters"] == len(full.plan.ss10_input)

    def test_the_mini_font_carries_no_foreign_lookup(self, built):
        font_path, plan, cursive, _copies = built
        report = readback.verify_font(font_path, plan, cursive)
        assert report["checked"]["foreign_lookups"] == {"gsub_lookups": 0, "gpos_lookups": 0, "kern": 0}

    def test_the_namer_dot_keeps_its_height_after_a_latin_letter_or_digit(self, full):
        """At a word start a `·` before a Short letter is lowered; after a Latin letter or digit it is a mid-word middot and keeps its height, as in the shipped font. A ZWNJ after the dot still keeps it from lowering."""
        shaper = conform.Shaper(full.font)
        none = frozenset()
        it = chr(0xE670)

        def dot(text):
            return [
                glyph["name"]
                for glyph in shaper.shape(text, none)
                if glyph["name"].startswith("periodcentered")
            ]

        assert dot("\u00b7" + it) == ["periodcentered.lowered"]
        assert dot(" \u00b7" + it) == ["periodcentered.lowered"]
        assert dot("a\u00b7" + it) == ["periodcentered"]
        assert dot("3\u00b7" + it) == ["periodcentered"]
        assert dot("\u00e9\u00b7" + it) == ["periodcentered"]
        assert dot("\u00b7\u200c" + it) == ["periodcentered"]

    def test_a_dropped_midword_row_is_a_divergence(self, full):
        plan = dataclasses.replace(full.plan, namer_midword=frozenset())
        report = readback.verify_font(
            full.font, plan, full.cursive, kern=full.kern, variation_sequences=full.variation.sequences
        )
        assert _named(report, "namer dot: 3 rows, expected the guard row, and the lowering row")

    def test_a_foreign_lookup_that_substitutes_a_rune_glyph(self, full, tmp_path):
        def mutate(font):
            lookups = font["GSUB"].table.LookupList.Lookup
            for index in _feature_lookups(font, "GSUB", "ccmp"):
                for inner in readback._invoked(lookups[index]):
                    for subtable in lookups[inner].SubTable:
                        _inner(subtable).mapping["qsIt"] = "dotlessi"

        report = _full_corrupted(full, tmp_path, mutate)
        assert _named(report, "substitutes rune-owned glyphs ['qsIt']")

    def test_a_foreign_kern_between_two_rune_glyphs(self, full, tmp_path):
        """The Latin rule that kerns every glyph before `j` passes; the same rule with ·It added beside `j` kerns rune-owned pairs."""

        def mutate(font):
            lookups = font["GPOS"].table.LookupList.Lookup
            curs = _feature_lookups(font, "GPOS", "curs")
            for index in _feature_lookups(font, "GPOS", "kern"):
                if index > min(curs):
                    continue
                for subtable in lookups[index].SubTable:
                    subtable = _inner(subtable)
                    if subtable.Format == 2 and "j" in subtable.ClassDef2.classDefs:
                        subtable.ClassDef2.classDefs["qsIt"] = subtable.ClassDef2.classDefs["j"]

        report = _full_corrupted(full, tmp_path, mutate)
        assert _named(report, "kerns rune-owned pairs")

    def test_a_foreign_lookup_after_the_plans_lookups(self, full, tmp_path):
        """Registering ccmp's lookups under calt's last lookup too reaches them from a planned feature, and the plan's lookups no longer all follow the foreign ones."""

        def mutate(font):
            ccmp = _feature_lookups(font, "GSUB", "ccmp")
            _feature_record(font, "GSUB", "calt").Feature.LookupListIndex.append(ccmp[0])

        report = _full_corrupted(full, tmp_path, mutate)
        assert _named(report, "are reached by both a planned and a foreign feature")

    def test_a_variation_sequence_the_cmap_does_not_carry(self, full, tmp_path):
        def mutate(font):
            font["cmap"].tables = [table for table in font["cmap"].tables if table.format != 14]

        report = _full_corrupted(full, tmp_path, mutate)
        assert _named(report, "variation sequences: cmap format 14 maps {}")

    def test_a_variation_glyph_settlement_names(self, full):
        (glyph,) = {name for mapping in full.variation.sequences.values() for name in mapping.values()}
        rule = full.plan.settle_rules[0]
        plan = dataclasses.replace(
            full.plan, settle_rules=(dataclasses.replace(rule, outcome=glyph), *full.plan.settle_rules[1:])
        )
        report = readback.verify_font(
            full.font, plan, full.cursive, kern=full.kern, variation_sequences=full.variation.sequences
        )
        assert _named(report, f"the plan's settlement, formation or substitutions name ['{glyph}']")

    def test_a_bold_whose_layout_is_not_regulars(self, full, tmp_path):
        from fontTools.ttLib import TTFont

        bold = TTFont(str(full.bold))
        bold["GPOS"].table.LookupList.Lookup[0].LookupFlag = 8
        corrupted = tmp_path / "Senior-Bold.otf"
        bold.save(str(corrupted))
        report = _full_report(full, bold_path=corrupted)
        assert _named(report, "bold: Bold's GPOS is not byte for byte Regular's")
