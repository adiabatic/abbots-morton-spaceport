"""Read-back tests over a real mini-world build: the fixture drives the whole emit → compile path on the fixture spec, so the font under test carries every stage the shipped one does — the ss10 pre-empt, the guarded and plain formation lookups, four marker lookups, the chokepoint, a packed settlement lookup, and the namer dot. A clean build must verify with zero divergences; each corruption below is a lie the compiled font could tell about the plan, and must be caught and named."""

import pytest

from rebuild.pipeline import (
    compile_font,
    conform,
    emit_gpos,
    emit_gsub,
    fixtures,
    kernel_exec,
    readback,
    run_m1,
)

CONFIGS = ("default", "ss03")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    spec = fixtures.mini_spec()
    tables: dict[str, tuple] = {
        config: kernel_exec.build_tables(spec, conform.features_for_config(config)) for config in CONFIGS
    }
    cell_glyphs = run_m1.mint_cell_glyphs(spec, tables)
    bare, twins, ss10_twins = run_m1.mint_raw_glyphs(spec)
    dots = run_m1.namer_dot_glyphs()
    curs_glyphs = {**cell_glyphs, **bare, **twins}
    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_twins=ss10_twins)
    gpos_fea = emit_gpos.emit_gpos(curs_glyphs, spec=spec)
    font_path = compile_font.build_mini_font(
        {**curs_glyphs, **dots},
        gsub_plan.fea_text + "\n" + gpos_fea,
        tmp_path_factory.mktemp("m1-readback") / "M1Readback.otf",
    )
    cursive = emit_gpos.cursive_registrations(curs_glyphs, spec=spec)
    return font_path, gsub_plan, cursive, ss10_twins


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


def _corrupted_report(built, tmp_path, name, mutate):
    from fontTools.ttLib import TTFont

    font_path, plan, cursive, _twins = built
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


class TestReadback:
    def test_a_clean_build_verifies(self, built):
        font_path, plan, cursive, _twins = built
        report = readback.verify_font(font_path, plan, cursive)
        assert report["divergences"] == []
        assert report["pass"]
        assert report["checked"]["settle_rules"] == plan.rule_count
        assert report["checked"]["guarded_rows"] == len(plan.formation_guarded_rows) > 0
        assert report["checked"]["cursive_anchors"]

    def test_the_plan_carries_every_stage_in_definition_order(self, built):
        _font_path, plan, _cursive, twins = built
        assert plan.calt_stages == (
            "m1_formation_guarded",
            "m1_formation",
            "m1_zwnj",
            "m1_settle",
            "m1_namer_dot_word_start",
        )
        assert len(plan.settle_rules) == plan.rule_count > 0
        assert plan.ss10_preempt == dict(twins)
        assert sorted(plan.marker_lines) == ["ss02", "ss03", "ss04", "ss05"]
        assert plan.formation_plain == ((("qsTea", "qsOy"), "qsTea_qsOy"),)
        assert plan.namer_dot_stage is not None and plan.namer_dot_stage[0] == "periodcentered"

    def test_the_offset_budget_is_read_off_the_raw_table(self, built):
        """The byte walk over the raw GSUB against the decoded table's own counts, which is the independent witness that it lands on the uint16 fields it means to; the settlement lookup's format census rides the same parse, so the packed reality stays legible in the summary."""
        from fontTools.ttLib import TTFont

        font_path, plan, cursive, _twins = built
        report = readback.verify_font(font_path, plan, cursive)
        budget = report["checked"]["gsub_budget"]
        font = TTFont(str(font_path))
        try:
            lookup_list = font["GSUB"].table.LookupList
            assert budget["lookups"] == lookup_list.LookupCount
            assert budget["subtables"] == sum(lookup.SubTableCount for lookup in lookup_list.Lookup)
            settle_subtables = lookup_list.Lookup[_stage_index(font, plan, "m1_settle")].SubTableCount
        finally:
            font.close()
        assert 0 < budget["subtable_offset_headroom"] <= 65_535
        assert budget["floor"] == readback.SUBTABLE_OFFSET_HEADROOM_FLOOR
        formats = report["checked"]["settle_subtable_formats"]
        assert formats["format2"] >= 1
        assert formats["format2"] + formats["format3"] == settle_subtables

    def test_the_boundary_glyphs_are_inert_on_the_bytes(self, built):
        """The boundary claim, made once off the written font rather than per shaped ZWNJ slot: every substituted position of every lookup was examined and none of them admits a boundary glyph, `uni200C` carries no advance, and neither glyph draws an outline."""
        font_path, plan, cursive, _twins = built
        report = readback.verify_font(font_path, plan, cursive)
        boundary = report["checked"]["boundary_glyphs"]
        assert boundary["substituted_positions"] > 0
        assert boundary["uni200C"]["advance"] == 0
        assert boundary["uni200C"]["inked"] is False
        assert boundary["space"]["inked"] is False
        assert report["pass"]

    def test_verification_is_deterministic(self, built):
        font_path, plan, cursive, _twins = built
        assert readback.verify_font(font_path, plan, cursive) == readback.verify_font(
            font_path, plan, cursive
        )


class TestCorruptions:
    def test_unregistering_settlement_from_calt(self, built, tmp_path):
        def mutate(font, plan):
            calt = _feature_record(font, "GSUB", "calt").Feature
            del calt.LookupListIndex[plan.calt_stages.index("m1_settle")]
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
            index = _stage_index(font, plan, "m1_settle")
            font["GSUB"].table.LookupList.Lookup[index].LookupFlag = 8

        report = _corrupted_report(built, tmp_path, "flagged-settle", mutate)
        assert not report["pass"]
        flagged = _named(report, "lookupFlag:")
        assert flagged and "LookupFlag 8" in flagged[0]

    def test_retargeting_a_settlement_outcome(self, built, tmp_path):
        def mutate(font, plan):
            from rebuild.pipeline import pack_gsub

            lookups = font["GSUB"].table.LookupList.Lookup
            settle = lookups[_stage_index(font, plan, "m1_settle")]
            sequences = pack_gsub.per_glyph_sequences(settle)
            glyph = sorted(sequences)[0]
            inner = _inner(lookups[sequences[glyph][0].records[0][1]].SubTable[0])
            inner.mapping[glyph] = "qsPea"

        report = _corrupted_report(built, tmp_path, "retargeted-settle", mutate)
        assert not report["pass"]
        assert _named(report, "settle:")

    def test_dropping_a_guarded_formation_subtable(self, built, tmp_path):
        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "m1_formation_guarded")]
            lookup.SubTable = lookup.SubTable[:-1]
            lookup.SubTableCount = len(lookup.SubTable)

        report = _corrupted_report(built, tmp_path, "short-formation", mutate)
        assert not report["pass"]
        assert _named(report, "formation guarded:")

    def test_reordering_packed_settlement_rules(self, built, tmp_path):
        """Rule order is the whole of first-match-wins, so a lookup that holds every planned rule in the wrong order is a font that shapes something else. The count stays honest and only the order lies, which is the corruption the packing itself could commit — and the one read-back's decompile through `per_glyph_sequences` exists to catch."""

        def mutate(font, plan):
            from rebuild.pipeline import pack_gsub

            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "m1_settle")]
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
        _font_path, plan, _cursive, _twins = built
        assert not report["pass"]
        settled = _named(report, "settle:")
        assert settled and any("expected" in line for line in settled)
        assert report["checked"]["settle_rules"] == plan.rule_count

    def test_a_headroom_under_the_floor_is_a_divergence(self, built, monkeypatch):
        """Lifting the floor over the whole uint16 space makes a clean font breach it: the breach must read as one more divergence, named and alone, rather than as a raise."""
        font_path, plan, cursive, _twins = built
        monkeypatch.setattr(readback, "SUBTABLE_OFFSET_HEADROOM_FLOOR", 65_536)
        report = readback.verify_font(font_path, plan, cursive)
        assert not report["pass"]
        breached = _named(report, "gsub budget:")
        assert len(breached) == 1 and "65,536-byte floor" in breached[0]
        assert report["divergences"] == breached

    def test_a_single_substitution_of_the_zwnj(self, built, tmp_path):
        """A pre-empt lookup that substitutes the ZWNJ itself: the slot a word boundary is made of would be replaced by a drawn letter, and no shaping sweep has to be run to see it."""

        def mutate(font, _plan):
            index = _feature_record(font, "GSUB", "ss10").Feature.LookupListIndex[0]
            _inner(font["GSUB"].table.LookupList.Lookup[index].SubTable[0]).mapping["uni200C"] = "qsPea"

        report = _corrupted_report(built, tmp_path, "zwnj-single-subst", mutate)
        assert not report["pass"]
        named = _named(report, "boundary glyphs:")
        assert named and any("uni200C" in line for line in named)

    def test_a_settlement_input_coverage_admitting_space(self, built, tmp_path):
        """A format-3 settlement rule whose substituted input coverage has grown a space: the rule would fire on a word boundary, which is exactly the position nothing may substitute."""

        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "m1_settle")]
            inner = next(
                candidate
                for candidate in (_inner(subtable) for subtable in lookup.SubTable)
                if candidate.Format == 3
            )
            glyphs = inner.InputCoverage[0].glyphs
            glyphs.append("space")
            glyphs.sort(key=font.getGlyphID)

        report = _corrupted_report(built, tmp_path, "space-in-input", mutate)
        assert not report["pass"]
        named = _named(report, "boundary glyphs:")
        assert named and any("space" in line for line in named)

    def test_a_format2_class_zero_lead_admitting_the_zwnj(self, built, tmp_path):
        """Class 0 of a ClassDef is every glyph it does not name, which is the class the old decompile read as empty — so a format-2 ruleset hung off class 0 could substitute a lead slot that admits the ZWNJ and nothing structural would say so. The rules are copied onto class 0 and the ZWNJ added to the subtable's coverage; `uni200C` is absent from the InputClassDef, so it is class 0 by definition."""

        def mutate(font, plan):
            lookup = font["GSUB"].table.LookupList.Lookup[_stage_index(font, plan, "m1_settle")]
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
