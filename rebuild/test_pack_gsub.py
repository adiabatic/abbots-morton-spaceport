"""Round-trip tests for pack_gsub, which repacks a lookup's per-rule format-3 chained-context subtables (the shape feaLib compiles most `m1_settle` rules to) into format-2 groups. The packed font must shape every string in `PROBES` the same, choose the same substitution as the unpacked font in every context (`_first_matches`), reference no class 0, leave the inner lookups unchanged, and pack deterministically.

`FEA` covers the cases that constrain packing: several rules on the same input glyph whose order matters, overlapping but unequal lookahead classes (which force a second group), a rule with backtrack, a ZWNJ-lookahead row ordered first, a no-lookahead fallback row, and a rule whose own lookahead sets overlap without being equal, which splits into pieces that join format-2 groups. A rule whose split would take ZWNJ out of a slot short of the farthest on its side stays whole in format 3, and the font shapes the same across a ZWNJ. `_mixed_font` puts an existing format-2 subtable between format-3 runs; it must stay an ordered barrier through packing and serialization, and a multi-input rule in it disqualifies the lookup. `FEA_NAMED` writes the seven `FEA` rules with each outcome as a `lookup NAME` reference to a standalone single substitution, the form `m1_settle` uses, and must pack and shape the same as the inline form.
"""

import io
from copy import copy
from itertools import product

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


def _shape_all(font, tmp_path, probes=PROBES):
    import uharfbuzz as hb

    font_path = tmp_path / "pack-test.otf"
    font.save(str(font_path))
    face = hb.Face(hb.Blob.from_file_path(str(font_path)))
    hb_font = hb.Font(face)
    shaped = {}
    for probe in probes:
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
    """Return `per_glyph_sequences` with each record replaced by the glyph its inner lookup substitutes, so that two fonts whose LookupLists are numbered differently can be compared."""
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


def _first_matches(lookup, glyphs=GLYPHS):
    """Map every context the lookup's rules can read to the records of the first rule that matches it: for each input glyph, every backtrack and lookahead of the glyphs in `glyphs`, as long as the longest rule reads, and every shorter one, which stands for a buffer edge. Two lookups with the same map choose the same substitution in every context of those glyphs read as plain glyph positions, however their rules are split or grouped. HarfBuzz also skips a ZWNJ that a context slot does not hold, which this map does not model; the tests shape that through HarfBuzz."""
    sequences = pack_gsub.per_glyph_sequences(lookup)
    rules = [rule for glyph_rules in sequences.values() for rule in glyph_rules]
    backtrack_depth = max((len(rule.backtrack) for rule in rules), default=0)
    lookahead_depth = max((len(rule.lookahead) for rule in rules), default=0)
    contexts = [
        (backtrack, lookahead)
        for backtrack_length in range(backtrack_depth + 1)
        for backtrack in product(glyphs, repeat=backtrack_length)
        for lookahead_length in range(lookahead_depth + 1)
        for lookahead in product(glyphs, repeat=lookahead_length)
    ]
    matches = {}
    for glyph, glyph_rules in sequences.items():
        for backtrack, lookahead in contexts:
            for rule in glyph_rules:
                if (
                    len(rule.backtrack) <= len(backtrack)
                    and len(rule.lookahead) <= len(lookahead)
                    and all(member in slot for member, slot in zip(backtrack, rule.backtrack))
                    and all(member in slot for member, slot in zip(lookahead, rule.lookahead))
                ):
                    matches[(glyph, backtrack, lookahead)] = rule.records
                    break
    return matches


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
    @pytest.mark.parametrize("kept", [False, True])
    def test_smaller_stream_inserts_before_groups_its_later_rules_can_share(self, kept):
        early_context = "[B uni200C] [B C uni200C] D" if kept else "[B C]"
        font = _build_font(f"""
lookup t_settle useExtension {{
    sub A' B by A.alt1;
    sub A' B C by A.alt2;
    sub A' B D by A.alt3;
    sub B' {early_context} by B.alt1;
    sub B' B by B.alt1;
}} t_settle;
feature calt {{ lookup t_settle; }} calt;
""")
        lookup = _settle_lookup(font)
        before = pack_gsub.per_glyph_sequences(lookup)

        pack_gsub.pack_lookup(lookup, font.getGlyphOrder())

        assert lookup.SubTableCount == 2
        assert [subtable.ExtSubTable.Format for subtable in lookup.SubTable] == [3 if kept else 2, 2]
        assert pack_gsub.per_glyph_sequences(lookup) == before

    def test_a_singleton_slot_inside_a_later_broad_class_splits_the_broad_class(self):
        """The real table's shape: one lookahead slot holds a singleton and a later one a broad class holding the same glyph, so the rule's own lookahead sets cannot share a ClassDef. The broad slot splits into the rest of the class, then the singleton, and the rule's other slots stay as they are."""
        singleton, broad = frozenset({"B"}), frozenset({"B", "C", "D"})
        rule = pack_gsub.LogicalRule(
            (frozenset({"C"}),), frozenset({"A"}), (frozenset({"A"}), singleton, broad), ((0, 3),)
        )

        assert pack_gsub._split_rule(rule) == [
            pack_gsub.LogicalRule(
                rule.backtrack, rule.input, (frozenset({"A"}), singleton, broad - singleton), rule.records
            ),
            pack_gsub.LogicalRule(
                rule.backtrack, rule.input, (frozenset({"A"}), singleton, singleton), rule.records
            ),
        ]

    def test_sets_that_each_hold_glyphs_the_other_lacks_split_both(self):
        first, second = frozenset({"B", "C"}), frozenset({"C", "D"})

        assert pack_gsub._split_slots((first, second)) == [
            (frozenset({"B"}), second),
            (frozenset({"C"}), frozenset({"D"})),
            (frozenset({"C"}), frozenset({"C"})),
        ]
        assert pack_gsub._split_slots((first, frozenset({"A"}), first)) == [(first, frozenset({"A"}), first)]

    def test_a_split_rule_packs_into_format2_and_shapes_the_same(self, tmp_path):
        """Two rules of the singleton-inside-a-broad-class shape, between rules of the same input glyph that they must stay ahead of and behind, split into two pieces each and pack with every other rule into format-2 groups, and the packed lookup chooses the same substitution as the unpacked one in every context."""
        fea = """
lookup t_settle useExtension {
    sub A' B B by A.alt1;
    sub A' C [B C D] by A.alt3;
    sub A' B [B C D] by A.alt2;
    sub A' [B C D] by A.alt3;
    sub B' C by B.alt1;
} t_settle;
feature calt { lookup t_settle; } calt;
"""
        unpacked = _build_font(fea)
        reference = _first_matches(_settle_lookup(unpacked))
        shaped = _shape_all(unpacked, tmp_path)
        font = _build_font(fea)

        stats = pack_gsub.pack_font(font, min_subtables=2)

        assert stats["packed_lookups"][0]["rules"] == 5
        assert stats["packed_lookups"][0]["packed_rules"] == 7
        lookup = _settle_lookup(font)
        assert {subtable.ExtSubTable.Format for subtable in lookup.SubTable} == {2}
        assert _first_matches(lookup) == reference
        assert _shape_all(font, tmp_path) == shaped

    def test_a_split_that_would_drop_zwnj_from_a_nearer_slot_keeps_the_rule_whole(self, tmp_path):
        """HarfBuzz skips a ZWNJ that a context slot does not hold and tries the slot on the glyph beyond it. Splitting the first rule's second lookahead slot, or the second rule's nearer backtrack slot, would leave a piece without ZWNJ there, which skips the ZWNJ in the text A B ZWNJ C D or B C ZWNJ A and fires where the rule does not, so both rules keep their format-3 subtables. The third rule's split slot is its farthest, where the rule has already matched a ZWNJ the piece skips, so it splits and packs into format 2."""
        fea = """
lookup t_settle useExtension {
    sub A' [B uni200C] [B C uni200C] D by A.alt1;
    sub [B uni200C] [B C uni200C] A' by A.alt3;
    sub B' [B uni200C] [B C uni200C] by B.alt1;
    sub A' by A.alt2;
} t_settle;
feature calt { lookup t_settle; } calt;
"""
        probes = [
            "AB\u200cCD",
            "ABCD",
            "AB\u200cBD",
            "BC\u200cA",
            "BBA",
            "B\u200cBA",
            "BB\u200cC",
            "BBC",
            "BCC",
        ]
        shaped = _shape_all(_build_font(fea), tmp_path, probes)
        assert shaped["AB\u200cCD"][0] == shaped["BC\u200cA"][-1] == "A.alt2"
        font = _build_font(fea)

        stats = pack_gsub.pack_font(font, min_subtables=2)

        entry = stats["packed_lookups"][0]
        assert (entry["rules"], entry["packed_rules"], entry["kept_format3"]) == (4, 5, 2)
        assert [subtable.ExtSubTable.Format for subtable in _settle_lookup(font).SubTable] == [3, 3, 2]
        assert _shape_all(font, tmp_path, probes) == shaped

    def test_a_group_stops_at_the_byte_cap(self, monkeypatch):
        """A group refuses a rule that would take its rule tables past `GROUP_RULE_BYTES_CAP`, and the rule starts a new group after it, so a stream's rules keep their order. Each group's running total is the rule bytes `group_rule_bytes` measures on the subtable written from it. The cap sits under read-back's ceiling, so no group the packer writes trips it."""
        from rebuild.pipeline import readback

        assert pack_gsub.GROUP_RULE_BYTES_CAP < readback.GROUP_RULE_BYTES_CEILING
        ot = pack_gsub._ot()
        rules = [
            pack_gsub.LogicalRule((), frozenset({"A"}), (frozenset({glyph}),), ((0, index),))
            for index, glyph in enumerate(["B", "C", "D", "A.alt1", "A.alt2", "A.alt3", "B.alt1"])
        ]
        rule_bytes = 8 + 2 + 4
        assert len(pack_gsub._group_rules([(rule, None) for rule in rules])) == 1

        monkeypatch.setattr(pack_gsub, "GROUP_RULE_BYTES_CAP", 3 * rule_bytes)
        groups = pack_gsub._group_rules([(rule, None) for rule in rules])

        assert [group.rules for group in groups] == [rules[:3], rules[3:6], rules[6:]]
        order = {glyph: index for index, glyph in enumerate(GLYPHS)}
        subtables = [pack_gsub._format2_subtable(group, order) for group in groups]
        assert [pack_gsub.group_rule_bytes(subtable) for subtable in subtables] == [
            group.rule_bytes for group in groups
        ]
        assert max(group.rule_bytes for group in groups) <= 3 * rule_bytes
        lookup = ot.Lookup()
        lookup.LookupType = 6
        lookup.SubTable = subtables
        assert pack_gsub.per_glyph_sequences(lookup) == {"A": rules}

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
        assert entry["packed_rules"] == 8
        assert entry["format2_subtables"] < entry["rules"]
        assert entry["kept_format3"] == 0
        assert _settle_lookup(packed).SubTableCount == entry["format2_subtables"]
        assert _settle_lookup(unpacked).SubTableCount == entry["rules"]

    def test_shaping_is_identical(self, packed_pair):
        _unpacked, packed, _stats, reference, tmp_path = packed_pair
        assert _shape_all(packed, tmp_path) == reference

    def test_every_context_first_matches_the_same_substitution(self, packed_pair):
        unpacked, packed, _stats, _reference, _tmp_path = packed_pair
        assert _first_matches(_settle_lookup(packed)) == _first_matches(_settle_lookup(unpacked))

    def test_no_class_zero_and_extension_kept(self, packed_pair):
        _unpacked, packed, _stats, _reference, _tmp_path = packed_pair
        lookup = _settle_lookup(packed)
        assert lookup.LookupType == 7
        for wrapper in lookup.SubTable:
            assert wrapper.ExtensionLookupType == 6
            subtable = wrapper.ExtSubTable
            assert subtable.Format == 2
            for class_set in subtable.ChainSubClassSet:
                if class_set is None:
                    continue
                for rule in class_set.ChainSubClassRule:
                    assert 0 not in (rule.Backtrack or [])
                    assert 0 not in (rule.LookAhead or [])

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
        """The same rules written with `lookup NAME` outcome references, the form `m1_settle` uses, pack to the same groups and shape every probe the same. The packer copies the SubstLookupRecords unchanged, whichever form produced them."""
        _unpacked, packed, stats, reference, _tmp_path = packed_pair
        named = _build_font(FEA_NAMED)
        assert _resolved_sequences(named) == _resolved_sequences(_unpacked)
        named_stats = pack_gsub.pack_font(named, min_subtables=2)
        assert (
            named_stats["packed_lookups"][0]["format2_subtables"]
            == stats["packed_lookups"][0]["format2_subtables"]
        )
        assert named_stats["packed_lookups"][0]["packed_rules"] == stats["packed_lookups"][0]["packed_rules"]
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
        expected = _first_matches(lookup)
        expected_prefix = _first_matches(_lookup_part(lookup, original[:3]))
        expected_suffix = _first_matches(_lookup_part(lookup, original[4:]))
        reference = _shape_all(font, tmp_path)

        stats = pack_gsub.pack_font(font, min_subtables=2)

        assert len(stats["packed_lookups"]) == 1
        assert lookup.SubTableCount < len(original)
        barrier_index = next(index for index, subtable in enumerate(lookup.SubTable) if subtable is barrier)
        assert 0 < barrier_index < lookup.SubTableCount - 1
        assert _first_matches(_lookup_part(lookup, lookup.SubTable[:barrier_index])) == expected_prefix
        assert _first_matches(_lookup_part(lookup, lookup.SubTable[barrier_index + 1 :])) == expected_suffix
        assert _first_matches(lookup) == expected
        assert _shape_all(font, tmp_path) == reference
        with TTFont(tmp_path / "pack-test.otf") as roundtrip:
            assert _first_matches(_settle_lookup(roundtrip)) == expected

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
