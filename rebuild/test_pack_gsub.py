"""pack_gsub round-trip tests: a feaLib-compiled chained-context lookup (the per-rule format-3 shape m1_settle rides) is packed into format-2 groups, and the packed font must shape every probe string identically, reference no class 0, leave the inner lookups untouched, and compress deterministically. The FEA below deliberately exercises the shapes that constrain the packing: same-input rule order, overlapping-but-unequal lookahead classes (which force a second group), a backtracked rule, a ZWNJ-explicit row ordered ahead of the bare row it shadows, a no-lookahead fallback row, and a self-incompatible rule (its own lookahead sets overlap without being equal) that must pass through as format 3. A partially packed fixture holds an existing format-2 subtable between format-3 runs: it remains an ordered barrier through packing and serialization, and a multi-input rule disqualifies the lookup. A third fixture spells the first fixture's seven rules with each outcome as a `lookup NAME` reference over standalone single substitutions, the shape m1_settle ships, and must pack and shape exactly as the inline spelling does."""

import io
from copy import copy

import pytest

from rebuild.pipeline import pack_gsub

GLYPHS = ["A", "B", "C", "D", "A.alt1", "A.alt2", "A.alt3", "B.alt1", "uni200C", "space"]
CMAP = {ord("A"): "A", ord("B"): "B", ord("C"): "C", ord("D"): "D", 0x200C: "uni200C", 0x20: "space"}

FEA = """
lookup t_settle useExtension {
    sub A' uni200C by A.alt3;
    sub B A' [B C] by A.alt1;
    sub A' [B D] [B] by A.alt1;
    sub A' [B C] by A.alt2;
    sub A' [B D] by A.alt3;
    sub B' [C] by B.alt1;
    sub B' by B.alt1;
} t_settle;
feature calt {
    lookup t_settle;
} calt;
"""

FEA_NAMED = """
lookup t_out_0 {
    sub A by A.alt3;
} t_out_0;
lookup t_out_1 {
    sub A by A.alt1;
} t_out_1;
lookup t_out_2 {
    sub A by A.alt2;
} t_out_2;
lookup t_out_3 {
    sub B by B.alt1;
} t_out_3;
lookup t_settle useExtension {
    sub A' lookup t_out_0 uni200C;
    sub B A' lookup t_out_1 [B C];
    sub A' lookup t_out_1 [B D] [B];
    sub A' lookup t_out_2 [B C];
    sub A' lookup t_out_0 [B D];
    sub B' lookup t_out_3 [C];
    sub B' lookup t_out_3;
} t_settle;
feature calt {
    lookup t_settle;
} calt;
"""

PROBES = [
    "AB",
    "AC",
    "AD",
    "AA",
    "BAC",
    "BAB",
    "BC",
    "BD",
    "B",
    "A‌B",
    "A B",
    "ABCD",
    "BACD",
    "ABB",
    "ABC",
    "ADB",
    "ADC",
    "ABDB",
]


def _build_font(fea=FEA):
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    builder = FontBuilder(1000)
    order = [".notdef"] + GLYPHS
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(CMAP)
    pen = TTGlyphPen(None)
    empty = pen.glyph()
    builder.setupGlyf({name: empty for name in order})
    builder.setupHorizontalMetrics({name: (500, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "PackTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    addOpenTypeFeaturesFromString(builder.font, fea)
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 0  # pyright: ignore[reportAttributeAccessIssue]
    builder.font["head"].modified = 0
    return builder.font


def _shape_all(font, tmp_path):
    import uharfbuzz as hb

    font_path = tmp_path / "pack-test.otf"
    font.save(str(font_path))
    face = hb.Face(hb.Blob.from_file_path(str(font_path)))
    hb_font = hb.Font(face)
    shaped = {}
    for probe in PROBES:
        buf = hb.Buffer()
        buf.add_str(probe)
        buf.guess_segment_properties()
        hb.shape(hb_font, buf, {"calt": True})
        shaped[probe] = [hb_font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]
    return shaped


def _settle_lookup(font):
    lookups = font["GSUB"].table.LookupList.Lookup
    return max(lookups, key=lambda lookup: lookup.SubTableCount)


def _resolved_sequences(font):
    """`per_glyph_sequences` with each record replaced by the outcome glyph its inner lookup substitutes, so two fonts whose LookupLists differ in numbering compare on what they shape."""
    lookups = font["GSUB"].table.LookupList.Lookup
    resolved = {}
    for glyph, rules in pack_gsub.per_glyph_sequences(_settle_lookup(font)).items():
        resolved[glyph] = []
        for rule in rules:
            outcomes = []
            for _sequence_index, lookup_index in rule.records:
                subtable = lookups[lookup_index].SubTable[0]
                outcomes.append(getattr(subtable, "ExtSubTable", subtable).mapping[glyph])
            resolved[glyph].append((rule.backtrack, rule.input, rule.lookahead, tuple(outcomes)))
    return resolved


def _lookup_part(lookup, subtables):
    part = copy(lookup)
    part.SubTable = subtables
    part.SubTableCount = len(subtables)
    return part


def _mixed_font():
    font = _build_font()
    lookup = _settle_lookup(font)
    original = list(lookup.SubTable)
    middle = _lookup_part(lookup, original[3:4])
    pack_gsub.pack_lookup(middle, font.getGlyphOrder())
    assert len(middle.SubTable) == 1
    barrier = middle.SubTable[0]
    assert barrier.ExtSubTable.Format == 2
    lookup.SubTable = original[:3] + [barrier] + original[4:]
    lookup.SubTableCount = len(lookup.SubTable)
    return font, barrier


@pytest.fixture(scope="module")
def packed_pair(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("pack-gsub")
    unpacked = _build_font()
    reference = _shape_all(unpacked, tmp_path)
    packed = _build_font()
    stats = pack_gsub.pack_font(packed, min_subtables=2)
    return unpacked, packed, stats, reference, tmp_path


class TestPackGsub:
    def test_larger_input_streams_pack_first_without_reordering_competing_rules(self):
        ot = pack_gsub._ot()

        first_class = frozenset({"C", "D"})
        second_class = frozenset({"B", "C"})
        rules = [
            pack_gsub.LogicalRule((), frozenset({"A"}), (first_class,), ((0, 0),)),
            pack_gsub.LogicalRule((), frozenset({"B"}), (second_class,), ((0, 1),)),
            pack_gsub.LogicalRule((), frozenset({"B"}), (first_class,), ((0, 2),)),
        ]

        groups = pack_gsub._group_rules([(rule, None) for rule in rules])

        assert len(groups) == 2
        lookup = ot.Lookup()
        lookup.LookupType = 6
        order = {glyph: index for index, glyph in enumerate(GLYPHS)}
        lookup.SubTable = [pack_gsub._format2_subtable(group, order) for group in groups]
        assert pack_gsub.per_glyph_sequences(lookup) == {"A": rules[:1], "B": rules[1:]}

    def test_overlapping_input_sets_keep_competing_rule_order(self):
        ot = pack_gsub._ot()

        rules = [
            pack_gsub.LogicalRule((), frozenset({"A", "B"}), (frozenset({"C"}),), ((0, 0),)),
            pack_gsub.LogicalRule((), frozenset({"A"}), (frozenset({"B", "C"}),), ((0, 1),)),
            pack_gsub.LogicalRule((), frozenset({"A"}), (frozenset({"C", "D"}),), ((0, 2),)),
        ]

        groups = pack_gsub._group_rules([(rule, None) for rule in rules])

        lookup = ot.Lookup()
        lookup.LookupType = 6
        order = {glyph: index for index, glyph in enumerate(GLYPHS)}
        lookup.SubTable = [pack_gsub._format2_subtable(group, order) for group in groups]
        assert pack_gsub.per_glyph_sequences(lookup) == {"A": rules, "B": rules[:1]}

    def test_pack_stats_and_compression(self, packed_pair):
        unpacked, packed, stats, _reference, _tmp_path = packed_pair
        assert len(stats["packed_lookups"]) == 1
        entry = stats["packed_lookups"][0]
        assert entry["rules"] == 7
        assert entry["format2_subtables"] < entry["rules"]
        assert entry["kept_format3"] == 1
        assert _settle_lookup(packed).SubTableCount == entry["format2_subtables"] + entry["kept_format3"]
        assert _settle_lookup(unpacked).SubTableCount == entry["rules"]

    def test_shaping_is_identical(self, packed_pair):
        _unpacked, packed, _stats, reference, tmp_path = packed_pair
        assert _shape_all(packed, tmp_path) == reference

    def test_per_glyph_sequences_survive(self, packed_pair):
        unpacked, packed, _stats, _reference, _tmp_path = packed_pair
        assert pack_gsub.per_glyph_sequences(_settle_lookup(packed)) == pack_gsub.per_glyph_sequences(
            _settle_lookup(unpacked)
        )

    def test_no_class_zero_and_extension_kept(self, packed_pair):
        _unpacked, packed, _stats, _reference, _tmp_path = packed_pair
        lookup = _settle_lookup(packed)
        assert lookup.LookupType == 7
        kept = 0
        for wrapper in lookup.SubTable:
            assert wrapper.ExtensionLookupType == 6
            subtable = wrapper.ExtSubTable
            assert subtable.Format in (2, 3)
            if subtable.Format == 3:
                kept += 1
                continue
            for class_set in subtable.ChainSubClassSet:
                if class_set is None:
                    continue
                for rule in class_set.ChainSubClassRule:
                    assert 0 not in (rule.Backtrack or [])
                    assert 0 not in (rule.LookAhead or [])
        assert kept == 1

    def test_inner_lookups_untouched(self, packed_pair):
        unpacked, packed, _stats, _reference, _tmp_path = packed_pair
        before = unpacked["GSUB"].table.LookupList
        after = packed["GSUB"].table.LookupList
        assert before.LookupCount == after.LookupCount
        for index in range(before.LookupCount):
            if after.Lookup[index] is _settle_lookup(packed):
                continue
            assert before.Lookup[index].LookupType == after.Lookup[index].LookupType
            assert before.Lookup[index].SubTableCount == after.Lookup[index].SubTableCount

    def test_packing_is_deterministic(self, packed_pair):
        _unpacked, _packed, stats, _reference, _tmp_path = packed_pair
        again = _build_font()
        stats_again = pack_gsub.pack_font(again, min_subtables=2)
        assert stats_again == stats
        first, second = io.BytesIO(), io.BytesIO()
        _packed.save(first)
        again.save(second)
        assert first.getvalue() == second.getvalue()

    def test_named_outcome_lookups_pack_exactly_as_inline_outcomes_do(self, packed_pair, tmp_path):
        """The same rules with each outcome spelled as a `lookup NAME` reference over standalone single substitutions, the shape m1_settle ships, pack to the same groups and shape every probe identically: the packer reuses the SubstLookupRecords verbatim and is indifferent to how they got there."""
        _unpacked, packed, stats, reference, _tmp_path = packed_pair
        named = _build_font(FEA_NAMED)
        assert _resolved_sequences(named) == _resolved_sequences(_unpacked)
        named_stats = pack_gsub.pack_font(named, min_subtables=2)
        assert (
            named_stats["packed_lookups"][0]["format2_subtables"]
            == stats["packed_lookups"][0]["format2_subtables"]
        )
        assert named_stats["packed_lookups"][0]["kept_format3"] == stats["packed_lookups"][0]["kept_format3"]
        assert _resolved_sequences(named) == _resolved_sequences(packed)
        assert _shape_all(named, tmp_path) == reference

    def test_a_rule_over_a_glyphless_class_refuses_to_decompile(self):
        font = _build_font()
        pack_gsub.pack_font(font, min_subtables=2)
        lookup = _settle_lookup(font)
        subtable = next(
            wrapper.ExtSubTable
            for wrapper in lookup.SubTable
            if wrapper.ExtSubTable.Format == 2 and wrapper.ExtSubTable.LookAheadClassDef.classDefs
        )
        class_defs = subtable.LookAheadClassDef.classDefs
        members: dict[int, list[str]] = {}
        for glyph, klass in class_defs.items():
            members.setdefault(klass, []).append(glyph)
        klass, (glyph,) = next((klass, glyphs) for klass, glyphs in members.items() if len(glyphs) == 1)
        class_defs[glyph] = max(class_defs.values()) + 1
        with pytest.raises(pack_gsub.PackError, match=f"lookahead class {klass}"):
            pack_gsub.per_glyph_sequences(lookup)

    def test_below_threshold_untouched(self):
        font = _build_font()
        stats = pack_gsub.pack_font(font, min_subtables=64)
        assert stats == {"packed_lookups": []}
        assert _settle_lookup(font).SubTableCount == 7

    def test_mixed_formats_preserve_barrier_rules_and_shaping(self, tmp_path):
        from fontTools.ttLib import TTFont

        font, barrier = _mixed_font()
        lookup = _settle_lookup(font)
        original = list(lookup.SubTable)
        expected = pack_gsub.per_glyph_sequences(lookup)
        expected_prefix = pack_gsub.per_glyph_sequences(_lookup_part(lookup, original[:3]))
        expected_suffix = pack_gsub.per_glyph_sequences(_lookup_part(lookup, original[4:]))
        reference = _shape_all(font, tmp_path)

        stats = pack_gsub.pack_font(font, min_subtables=2)

        assert len(stats["packed_lookups"]) == 1
        assert lookup.SubTableCount < len(original)
        barrier_index = next(index for index, subtable in enumerate(lookup.SubTable) if subtable is barrier)
        assert 0 < barrier_index < lookup.SubTableCount - 1
        assert (
            pack_gsub.per_glyph_sequences(_lookup_part(lookup, lookup.SubTable[:barrier_index]))
            == expected_prefix
        )
        assert (
            pack_gsub.per_glyph_sequences(_lookup_part(lookup, lookup.SubTable[barrier_index + 1 :]))
            == expected_suffix
        )
        assert pack_gsub.per_glyph_sequences(lookup) == expected
        assert _shape_all(font, tmp_path) == reference
        with TTFont(tmp_path / "pack-test.otf") as roundtrip:
            assert pack_gsub.per_glyph_sequences(_settle_lookup(roundtrip)) == expected

    def test_mixed_formats_with_multiple_inputs_are_untouched(self):
        font, barrier = _mixed_font()
        lookup = _settle_lookup(font)
        original = list(lookup.SubTable)
        subtable = barrier.ExtSubTable
        class_set = next(class_set for class_set in subtable.ChainSubClassSet if class_set is not None)
        rule = class_set.ChainSubClassRule[0]
        rule.Input = [next(iter(subtable.InputClassDef.classDefs.values()))]
        rule.InputGlyphCount = 2

        stats = pack_gsub.pack_font(font, min_subtables=2)

        assert stats == {"packed_lookups": []}
        assert lookup.SubTableCount == len(original)
        assert all(actual is expected for actual, expected in zip(lookup.SubTable, original, strict=True))
