"""Read-back tests over a real mini-world build: the fixture drives the whole emit → compile path on the fixture spec, so the font under test carries every stage the shipped one does — the ss10 pre-empt, the guarded and plain formation lookups, four marker lookups, the chokepoint, a packed settlement lookup, and the namer dot. A clean build must verify with zero divergences; each corruption below is a lie the compiled font could tell about the plan, and must be caught and named."""

import json

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

    def test_verification_is_deterministic(self, built):
        font_path, plan, cursive, _twins = built
        assert readback.verify_font(font_path, plan, cursive) == readback.verify_font(
            font_path, plan, cursive
        )


class TestSettleFold:
    """The record the build leaves beside its read-back summary: the settlement rows as the emitters planned them and this stage just held the font to, with the per-configuration sources the witness gate counts coverage over."""

    def test_the_settle_fold_round_trips(self, built, tmp_path):
        font_path, plan, _cursive, _twins = built
        path = readback.settle_fold_path(tmp_path)
        readback.write_settle_fold(path, plan, "fp-sources", True, font=font_path)
        fold = readback.read_settle_fold(path)
        assert fold.inputs == "fp-sources"
        assert fold.readback_pass is True
        assert fold.font == str(font_path)
        assert fold.rules == plan.settle_rules
        assert all(rule.sources for rule in fold.rules)
        assert fold.configs == tuple(
            dict.fromkeys(name for rule in plan.settle_rules for name, _index in rule.sources)
        )
        assert set(fold.configs) == set(CONFIGS)
        lines = path.read_text().splitlines()
        assert json.loads(lines[0])["rules"] == len(lines) - 1 == len(fold.rules)

    def test_a_failed_readback_is_recorded_as_one(self, built, tmp_path):
        _font_path, plan, _cursive, _twins = built
        path = readback.settle_fold_path(tmp_path)
        readback.write_settle_fold(path, plan, "fp-sources", False)
        fold = readback.read_settle_fold(path)
        assert fold.readback_pass is False
        assert fold.font is None
        assert fold.rules == plan.settle_rules

    def test_a_settle_fold_is_byte_identical_across_writes(self, built, tmp_path):
        _font_path, plan, _cursive, _twins = built
        first, second = tmp_path / "first.ndjson", tmp_path / "second.ndjson"
        readback.write_settle_fold(first, plan, "fp-sources", True)
        readback.write_settle_fold(second, plan, "fp-sources", True)
        assert first.read_bytes() == second.read_bytes()

    def test_a_truncated_settle_fold_refuses_to_load(self, built, tmp_path):
        _font_path, plan, _cursive, _twins = built
        truncated = tmp_path / "truncated.ndjson"
        readback.write_settle_fold(truncated, plan, "fp-sources", True)
        truncated.write_text("\n".join(truncated.read_text().splitlines()[:-1]) + "\n")
        with pytest.raises(ValueError):
            readback.read_settle_fold(truncated)
        headless = tmp_path / "headless.ndjson"
        headless.write_text('{"format":"ams-m1-settle-fold/0","rules":0}\n')
        with pytest.raises(ValueError):
            readback.read_settle_fold(headless)
        with pytest.raises(OSError):
            readback.read_settle_fold(tmp_path / "absent.ndjson")


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
