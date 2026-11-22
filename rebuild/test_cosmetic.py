"""Cosmetic stylistic sets: a rune's `cosmetic:` block, which redraws a stance's unjoined cells under a set the registry marks `overlay: cosmetic` after settlement and never steers a join. The loader's rules, the emitted lookup and read-back's checks of it, the conformance sweep's cosmetic case, and the kern sides a copy takes."""

import dataclasses
import textwrap
import warnings

import pytest

from rebuild.pipeline import (
    compile_font,
    conform,
    emit_gpos,
    emit_gsub,
    fixtures,
    kern,
    kernel_exec,
    kernel_io,
    model,
    readback,
    run_m1,
    spec_load,
)
from rebuild.pipeline.spec_load import SpecError, SpecWarning, load_spec

REGISTRY = textwrap.dedent("""\
    heights: {baseline: 0, x-height: 5, y6: 6, top: 8}
    boundary_tokens:
      space: {codepoint: 0x0020, splits_runs: true}
    features:
      ss04: {kind: capability, description: "test capability"}
      ss06: {kind: taste, description: "test cosmetic", overlay: cosmetic}
    predicate_classes:
      can-enter-at-baseline: {can_enter_at: baseline}
    families:
      qsIt: {codepoint: 0xE670}
    """)

RUNE = textwrap.dedent("""\
    rune: qsIt
    codepoint: 0xE670
    ductus:
      sole: |
        A vertical stroke.
    stances:
      sole:
        motion: sole
        bitmap:
        - "#"
        - "#"
        - "#"
        - "#"
        - "#"
        - "#"
        surface:
          entries:
            baseline: {x: 0}
          exits:
            baseline: {x: 1, unjoined: safe}
    """)

GAPPED = textwrap.dedent("""\
    cosmetic:
    - feature: {feature}
      stance: {stance}
      bitmap:
    {rows}
    """)


def _cosmetic(feature="ss06", stance="sole", rows=("#", "#", " ", "#", "#", "#")) -> str:
    return GAPPED.format(feature=feature, stance=stance, rows="\n".join(f'  - "{row}"' for row in rows))


def _load(tmp_path, text):
    runes_dir = tmp_path / "runes"
    runes_dir.mkdir(parents=True, exist_ok=True)
    (runes_dir / "qsIt.yaml").write_text(text)
    registry = tmp_path / "script.yaml"
    registry.write_text(REGISTRY)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SpecWarning)
        return load_spec(runes_dir, registry, spec_load.DEFAULT_SCHEMA_DIR)


def _error(tmp_path, text) -> str:
    with pytest.raises(SpecError) as caught:
        _load(tmp_path, text)
    return str(caught.value)


class TestTheLoader:
    def test_a_cosmetic_record_loads_with_its_drawing_and_provenance(self, tmp_path):
        spec = _load(tmp_path, RUNE + _cosmetic())
        (record,) = spec.runes["qsIt"].cosmetic
        assert (record.rune, record.feature, record.stance) == ("qsIt", "ss06", "sole")
        assert record.bitmap.rows == ("#", "#", " ", "#", "#", "#")
        assert record.provenance is not None and record.provenance.path == "cosmetic[0]"
        assert model.cosmetic_features(spec) == frozenset({"ss06"})
        assert model.settlement_features(spec, {"ss04", "ss06"}) == frozenset({"ss04"})

    def test_only_a_cosmetic_set_can_redraw(self, tmp_path):
        assert "not a cosmetic set" in _error(tmp_path, RUNE + _cosmetic(feature="ss04"))

    def test_the_stance_must_exist(self, tmp_path):
        assert "unknown stance 'loop'" in _error(tmp_path, RUNE + _cosmetic(stance="loop"))

    def test_the_drawing_keeps_its_stance_size(self, tmp_path):
        assert "keeps its stance's size" in _error(tmp_path, RUNE + _cosmetic(rows=("##",) * 6))
        assert "keeps its stance's size" in _error(tmp_path, RUNE + _cosmetic(rows=("#",) * 5))

    def test_a_condition_cannot_name_a_cosmetic_set(self, tmp_path):
        text = RUNE + "policy:\n  refuse:\n  - when: {feature: ss06, right: {family: qsIt}}\n"
        assert "is a cosmetic set" in _error(tmp_path, text)

    def test_an_unlock_cannot_name_a_cosmetic_set(self, tmp_path):
        anchor = "        baseline: {x: 1, unjoined: safe}\n"
        assert RUNE.count(anchor) == 1
        text = RUNE.replace(
            anchor,
            anchor + "      unlocks:\n      - {feature: ss06, pairing: {entry: baseline, exit: baseline}}\n",
        )
        assert "is a cosmetic set" in _error(tmp_path, text)

    def test_the_spec_dump_leaves_the_block_out(self, tmp_path):
        plain = kernel_io.spec_json(_load(tmp_path, RUNE))
        redrawn = kernel_io.spec_json(_load(tmp_path, RUNE + _cosmetic()))
        assert redrawn == plain


def _redrawn_spec() -> tuple[model.ResolvedSpec, model.CellId]:
    """The mini fixture spec with ss06 registered as a cosmetic set and a `cosmetic:` record on the stance of the first unjoined cell the default table reaches, whose drawing drops one ink pixel."""
    base = fixtures.mini_spec()
    tables: dict[str, tuple] = {"default": kernel_exec.build_tables(base, frozenset())}
    cells = sorted(
        (
            cell
            for cell in run_m1.mint_cell_glyphs(base, tables)
            if cell.entry is None
            and cell.exit is None
            and not cell.adjustments
            and base.runes[cell.rune].codepoint is not None
        ),
        key=repr,
    )
    target = cells[0]
    stance = base.runes[target.rune].stances[target.stance]
    rows = list(stance.bitmap.rows)
    for index, row in enumerate(rows):
        if "#" in row:
            column = row.index("#")
            rows[index] = row[:column] + " " + row[column + 1 :]
            break
    record = model.CosmeticRecord(
        rune=target.rune,
        feature="ss06",
        stance=target.stance,
        bitmap=model.Bitmap(rows=tuple(rows), y_offset=stance.bitmap.y_offset),
    )
    registry = dataclasses.replace(
        base.registry,
        features={**base.registry.features, "ss06": model.FeatureInfo(kind="taste", overlay="cosmetic")},
    )
    runes = {**base.runes, target.rune: dataclasses.replace(base.runes[target.rune], cosmetic=(record,))}
    return dataclasses.replace(base, runes=runes, registry=registry), target


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    spec, target = _redrawn_spec()
    tables: dict[str, tuple] = {"default": kernel_exec.build_tables(spec, frozenset())}
    cell_glyphs = run_m1.mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = run_m1.mint_raw_glyphs(spec)
    cosmetic, substitutions = run_m1.mint_cosmetic_glyphs(spec, cell_glyphs)
    dots = run_m1.namer_dot_glyphs()
    curs_glyphs = {**cell_glyphs, **bare, **copies, **cosmetic}
    plan = emit_gsub.emit_gsub(
        spec,
        tables,
        glyphs={**cell_glyphs, **bare},
        ss10_copies=ss10_copies,
        cosmetic_substitutions=substitutions,
    )
    font_path = compile_font.build_mini_font(
        {**curs_glyphs, **dots},
        plan.fea_text + "\n" + emit_gpos.emit_gpos(curs_glyphs, spec=spec),
        tmp_path_factory.mktemp("m1-cosmetic") / "M1Cosmetic.otf",
    )
    cursive = emit_gpos.cursive_registrations(curs_glyphs, spec=spec)
    return dataclasses.make_dataclass(
        "Built", ["spec", "target", "cell_glyphs", "cosmetic", "plan", "font_path", "cursive"]
    )(spec, target, cell_glyphs, cosmetic, plan, font_path, cursive)


class TestTheEmittedFont:
    def test_each_unjoined_cell_of_the_stance_has_a_copy_and_nothing_else_does(self, built):
        (pairs,) = built.plan.cosmetic_lines.values()
        redrawn = {cell for cell, record in built.cell_glyphs.items() if record.name in pairs}
        assert built.target in redrawn
        for cell in redrawn:
            assert (cell.rune, cell.stance, cell.entry, cell.exit) == (
                built.target.rune,
                built.target.stance,
                None,
                None,
            )
        for source, copy in pairs.items():
            assert copy == model.cosmetic_copy_name(source, "ss06")
        assert {record.name for record in built.cosmetic.values()} == set(pairs.values())
        assert all(record.entry is None and record.exit is None for record in built.cosmetic.values())

    def test_read_back_passes_and_checks_the_lookup(self, built):
        report = readback.verify_font(built.font_path, built.plan, built.cursive)
        assert report["pass"], report["divergences"]
        assert report["checked"]["cosmetic_substitutions"] == len(
            next(iter(built.plan.cosmetic_lines.values()))
        )
        assert report["checked"]["cosmetic_registrations"] == report["checked"]["cosmetic_substitutions"]

    def test_read_back_refuses_a_copy_that_moves_the_letters_after_it(self, built, tmp_path):
        from fontTools.ttLib import TTFont

        copy = next(iter(next(iter(built.plan.cosmetic_lines.values())).values()))
        font = TTFont(str(built.font_path))
        advance, lsb = font["hmtx"].metrics[copy]
        font["hmtx"].metrics[copy] = (advance + 50, lsb)
        moved = tmp_path / "moved.otf"
        font.save(str(moved))
        font.close()
        report = readback.verify_font(moved, built.plan, built.cursive)
        assert any("would move the letters after it" in line for line in report["divergences"])

    def test_read_back_refuses_a_plan_whose_lookup_the_font_does_not_hold(self, built):
        ((feature, pairs),) = built.plan.cosmetic_lines.items()
        extra = next(record.name for cell, record in built.cell_glyphs.items() if record.name not in pairs)
        plan = dataclasses.replace(
            built.plan, cosmetic_lines={feature: {**pairs, extra: model.cosmetic_copy_name(extra, feature)}}
        )
        report = readback.verify_font(built.font_path, plan, built.cursive)
        assert any(line.startswith("cosmetic ss06") for line in report["divergences"]), report["divergences"]

    def test_the_sweep_shapes_the_cosmetic_configuration_as_default_with_the_copies(self, built):
        spec = built.spec
        glyph_names = {cell: record.name for cell, record in built.cell_glyphs.items()}
        result = conform._conformance_config(
            conform.Shaper(built.font_path),
            spec,
            "ss06",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            glyph_names,
            None,
            conform.SWEEP_MAX_LENGTH,
            kernel_exec.guard_sweep(spec),
        )
        assert result.divergences_by_kind == {}
        alphabet = len(conform.spec_alphabet(spec))
        assert result.sequences == sum(
            alphabet**length for length in range(1, conform.COSMETIC_MAX_LENGTH + 1)
        )

    def test_the_sweep_catches_a_font_whose_cosmetic_lookup_is_missing(self, built, tmp_path):
        spec = built.spec
        glyph_names = {cell: record.name for cell, record in built.cell_glyphs.items()}
        plain = dataclasses.replace(
            spec, runes={name: dataclasses.replace(rune, cosmetic=()) for name, rune in spec.runes.items()}
        )
        tables: dict[str, tuple] = {"default": kernel_exec.build_tables(plain, frozenset())}
        bare, copies, ss10_copies = run_m1.mint_raw_glyphs(plain)
        curs_glyphs = {**built.cell_glyphs, **bare, **copies}
        gsub = emit_gsub.emit_gsub(
            plain, tables, glyphs={**built.cell_glyphs, **bare}, ss10_copies=ss10_copies
        )
        font_path = compile_font.build_mini_font(
            {**curs_glyphs, **run_m1.namer_dot_glyphs()},
            gsub.fea_text + "\n" + emit_gpos.emit_gpos(curs_glyphs, spec=plain),
            tmp_path / "M1Plain.otf",
        )
        result = conform._conformance_config(
            conform.Shaper(font_path),
            spec,
            "ss06",
            conform.spec_alphabet(spec),
            conform.splitting_boundary_chars(spec),
            glyph_names,
            None,
            conform.SWEEP_MAX_LENGTH,
            kernel_exec.guard_sweep(spec),
        )
        assert result.divergences_by_kind.get("name", 0) > 0


def test_a_cosmetic_copy_kerns_as_its_cell():
    cell = model.CellId("qsIt", "sole", None, None, ())
    lead, follow = kern.Side("qsIt", kern.LEADING), kern.Side("qsIt", kern.FOLLOWING)
    resolution = kern.KernResolution(
        leading={cell: lead},
        following={cell: follow},
        ss10_sources={},
        pairs=(),
        cosmetic_sources={"qsIt.sole.ss06": cell},
    )
    leading, following = resolution.glyph_sides({cell: model.GlyphRecord(name="qsIt.sole", bitmap=("#",))})
    assert leading == {"qsIt.sole": lead, "qsIt.sole.ss06": lead}
    assert following == {"qsIt.sole": follow, "qsIt.sole.ss06": follow}
