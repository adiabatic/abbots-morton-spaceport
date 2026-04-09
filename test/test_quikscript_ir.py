from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_font
import glyph_compiler
from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_fea import emit_quikscript_calt, emit_quikscript_curs
from quikscript_ir import (
    _widen_bitmap_right_with_connector,
    compile_quikscript_ir,
    expand_join_transforms,
    get_base_glyph_name,
    resolve_known_glyph_names,
)
from quikscript_planner import plan_quikscript_joins


def test_widen_right_at_edge_widens_by_count():
    bitmap = ("  ###",)
    new_bitmap, dx = _widen_bitmap_right_with_connector(bitmap, exit_y=0, y_offset=0, count=1)
    assert new_bitmap == ("  ####",)
    assert dx == 1


def test_widen_right_trailing_space_fills_in_place():
    bitmap = (
        "  ## ",
        " #   ",
        " #   ",
        "  #  ",
        "   # ",
        "  ###",
    )
    new_bitmap, dx = _widen_bitmap_right_with_connector(bitmap, exit_y=5, y_offset=0, count=1)
    assert new_bitmap[0] == "  ###"
    assert dx == 0
    assert all(len(row) == 5 for row in new_bitmap)


def test_widen_right_one_trailing_space_count_2():
    bitmap = ("## ",)
    new_bitmap, dx = _widen_bitmap_right_with_connector(bitmap, exit_y=0, y_offset=0, count=2)
    assert new_bitmap == ("####",)
    assert dx == 1


def test_widen_right_two_trailing_spaces_count_2():
    bitmap = ("#  ",)
    new_bitmap, dx = _widen_bitmap_right_with_connector(bitmap, exit_y=0, y_offset=0, count=2)
    assert new_bitmap == ("###",)
    assert dx == 0


def test_widen_right_tuple_bitmap_row():
    bitmap = ((0, 1, 1, 0),)
    new_bitmap, dx = _widen_bitmap_right_with_connector(bitmap, exit_y=0, y_offset=0, count=1)
    assert new_bitmap == ((0, 1, 1, 1),)
    assert dx == 0


def test_widen_right_non_connector_rows_unchanged():
    bitmap = (
        "## ",
        "###",
    )
    new_bitmap, dx = _widen_bitmap_right_with_connector(bitmap, exit_y=1, y_offset=0, count=1)
    assert new_bitmap == ("###", "###")
    assert dx == 0


def test_widen_right_empty_bitmap():
    result, dx = _widen_bitmap_right_with_connector((), exit_y=0, y_offset=0, count=1)
    assert result == ()
    assert dx == 0


def test_compile_glyph_set_exposes_flat_definitions_and_metadata():
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_set(data, "senior")

    assert compiled.glyph_definitions
    assert compiled.glyph_meta
    assert compiled.join_glyphs


def test_compiled_glyph_definitions_do_not_export_compiler_metadata_keys():
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_set(data, "senior")

    for glyph_name in (
        "qsTea",
        "qsDay_qsUtter",
        "qsDay_qsUtter.noentry",
        "qsMay.entry-baseline.entry-extended",
    ):
        assert not any(key.startswith("_") for key in compiled.glyph_definitions[glyph_name])


def test_compiled_glyph_definitions_flatten_join_glyphs_once(monkeypatch):
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_set(data, "senior")
    calls = 0
    original_flatten = glyph_compiler.flatten_join_glyphs

    def counting_flatten(join_glyphs):
        nonlocal calls
        calls += 1
        return original_flatten(join_glyphs)

    monkeypatch.setattr(glyph_compiler, "flatten_join_glyphs", counting_flatten)

    first = compiled.glyph_definitions
    second = compiled.glyph_definitions

    assert first is second
    assert calls == 1


def test_glyph_name_normalization_handles_middle_embedded_prop_suffix():
    assert get_base_glyph_name("U.prop.narrow") == "U.narrow"
    assert resolve_known_glyph_names(("U.prop.narrow",), {"U.narrow"}) == ["U.narrow"]


def test_expand_join_transforms_tracks_generated_sources_and_kinds():
    glyphs, _ = compile_quikscript_ir(
        {
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                            "exit": [1, 0],
                        },
                        "derive": {
                            "extend_exit_before": [{"family": "qsFollow"}],
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                        },
                        "derive": {
                            "extend_entry_after": [{"family": "qsLead"}],
                        },
                    },
                },
            },
        },
        "junior",
    )

    expanded, transforms = expand_join_transforms(glyphs, has_zwnj=True)

    assert "qsLead.noentry" in expanded
    assert "qsLead.exit-extended" in expanded
    assert "qsFollow.entry-extended" in expanded
    assert expanded["qsLead.noentry"].generated_from == "qsLead"
    assert expanded["qsLead.exit-extended"].transform_kind == "exit-extended"
    assert expanded["qsFollow.entry-extended"].transform_kind == "entry-extended"
    assert {transform.kind for transform in transforms} == {
        "entry-extended",
        "exit-extended",
        "noentry",
    }


def test_join_planner_populates_lookup_indexes_and_emits_calt_from_plan():
    data = load_glyph_data(ROOT / "glyph_data")
    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    plan = plan_quikscript_joins(join_glyphs)
    calt = emit_quikscript_calt(plan)

    assert plan.bk_replacements
    assert plan.all_fwd_bases
    assert plan.fwd_upgrades
    assert plan.ligatures
    assert "feature calt {" in calt
    assert "lookup calt_zwnj {" in calt


def test_emit_quikscript_curs_uses_join_glyphs_and_noentry_links():
    join_glyphs, _ = compile_quikscript_ir(
        {
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                            "exit": [1, 0],
                        },
                    },
                },
            },
        },
        "junior",
    )

    expanded, _ = expand_join_transforms(join_glyphs, has_zwnj=True)
    curs = emit_quikscript_curs(expanded, 50, 50)

    assert "feature curs {" in curs
    assert "pos cursive qsLead <anchor 0 0> <anchor 50 0>;" in curs
    assert "pos cursive qsLead.noentry <anchor NULL> <anchor 50 0>;" in curs


def test_build_font_uses_compiled_join_glyphs_for_feature_generation(monkeypatch):
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_set(data, "senior")
    sentinel_plan = object()

    class FakeCompiledGlyphSet:
        def __init__(self, glyph_definitions, join_glyphs):
            self.glyph_definitions = glyph_definitions
            self.join_glyphs = join_glyphs

    monkeypatch.setattr(
        build_font,
        "compile_glyph_set",
        lambda glyph_data, variant: FakeCompiledGlyphSet(
            compiled.glyph_definitions,
            compiled.join_glyphs,
        ),
    )

    def fake_curs(join_glyphs, pixel_width, pixel_height):
        assert join_glyphs is compiled.join_glyphs
        return None

    def fake_plan(join_glyphs):
        assert join_glyphs is compiled.join_glyphs
        return sentinel_plan

    def fake_ss_gate(plan):
        assert plan is sentinel_plan
        return None

    def fake_calt(plan):
        assert plan is sentinel_plan
        return None

    def fake_ss(join_glyphs):
        assert join_glyphs is compiled.join_glyphs
        return None

    monkeypatch.setattr(build_font, "emit_quikscript_curs", fake_curs)
    monkeypatch.setattr(build_font, "plan_quikscript_joins", fake_plan)
    monkeypatch.setattr(build_font, "emit_quikscript_ss_gate", fake_ss_gate)
    monkeypatch.setattr(build_font, "emit_quikscript_calt", fake_calt)
    monkeypatch.setattr(build_font, "emit_quikscript_ss", fake_ss)

    build_font.build_font(data, variant="senior")
