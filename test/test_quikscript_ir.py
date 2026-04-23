from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_font
import glyph_compiler
from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_fea import _analyze_quikscript_joins, emit_quikscript_senior_features
from quikscript_ir import (
    _widen_bitmap_right_with_connector,
    compile_quikscript_ir,
    expand_join_transforms,
    get_base_glyph_name,
    resolve_known_glyph_names,
)


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


def test_qs_see_keeps_its_y6_forward_lookup_early_when_ye_blocks_its_entry():
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_set(data, "senior")
    analysis = _analyze_quikscript_joins(compiled.glyph_meta)

    assert "qsSee" in analysis.early_pair_fwd_general
    assert analysis.early_pair_fwd_general_exit_ys["qsSee"] == {6}


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
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                            "exit": [1, 0],
                        },
                        "derive": {
                            "extend_exit_before": {"by": 1, "targets": [{"family": "qsFollow"}]},
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
                            "extend_entry_after": {"by": 1, "targets": [{"family": "qsLead"}]},
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
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


def test_senior_feature_emitter_includes_join_and_gate_features():
    data = load_glyph_data(ROOT / "glyph_data")
    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    fea = emit_quikscript_senior_features(join_glyphs, 50, 50)
    assert fea is not None

    assert "feature curs {" in fea
    assert "feature calt {" in fea
    assert "feature ss03 {" in fea
    assert "feature ss05 {" in fea
    assert "feature ss10 {" in fea
    assert "lookup calt_zwnj {" in fea


def test_senior_feature_emitter_prefers_narrower_pair_overrides():
    data = load_glyph_data(ROOT / "glyph_data")
    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    fea = emit_quikscript_senior_features(join_glyphs, 50, 50)
    assert fea is not None

    assert fea.index("lookup calt_pair_qsThaw_after-ing {") < fea.index(
        "lookup calt_pair_qsThaw_after-tall {"
    )


def test_senior_feature_emitter_uses_upgrade_for_terminal_qs_owe_pair_exit():
    data = load_glyph_data(ROOT / "glyph_data")
    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    fea = emit_quikscript_senior_features(join_glyphs, 50, 50)
    assert fea is not None

    pair_lookup = "lookup calt_pair_qsOwe_entry-xheight_entry-extended {"
    upgrade_lookup = "lookup calt_upgrade_qsOwe_entry-xheight_exit-xheight_entry-extended {"

    assert pair_lookup in fea
    assert upgrade_lookup in fea
    assert "lookup calt_pair_qsOwe_entry-xheight_exit-xheight_entry-extended {" not in fea
    assert fea.index(pair_lookup) < fea.index(upgrade_lookup)


def test_senior_feature_emitter_keeps_thaw_exit_baseline_before_ing_entry_extended():
    data = load_glyph_data(ROOT / "glyph_data")
    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    fea = emit_quikscript_senior_features(join_glyphs, 50, 50)
    assert fea is not None

    assert fea.index("lookup calt_fwd_pair_qsThaw_exit-baseline {") < fea.index(
        "lookup calt_pair_qsIng_entry-extended {"
    )


def test_senior_feature_emitter_uses_join_glyphs_and_noentry_links():
    join_glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
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
            "context_sets": {},
            "kerning": {},
        },
        "junior",
    )

    expanded, _ = expand_join_transforms(join_glyphs, has_zwnj=True)
    fea = emit_quikscript_senior_features(expanded, 50, 50)
    assert fea is not None

    assert "feature curs {" in fea
    assert "pos cursive qsLead <anchor 0 0> <anchor 50 0>;" in fea
    assert "pos cursive qsLead.noentry <anchor NULL> <anchor 50 0>;" in fea


def test_contextual_noentry_substitutions_stay_entryless():
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_set(data, "senior")
    fea = emit_quikscript_senior_features(
        compiled.join_glyphs,
        data["metadata"]["pixel_size"],
        data["metadata"]["pixel_size"],
    )
    assert fea is not None

    contextual_sub = re.compile(r"sub ([A-Za-z0-9_.-]+)' .* by ([A-Za-z0-9_.-]+);")
    for line in fea.splitlines():
        match = contextual_sub.search(line)
        if not match:
            continue
        lhs, rhs = match.groups()
        if not lhs.endswith(".noentry"):
            continue
        rhs_meta = compiled.glyph_meta[rhs]
        assert not rhs_meta.entry, line
        assert not rhs_meta.entry_curs_only, line


def test_build_font_uses_compiled_join_glyphs_for_feature_generation(monkeypatch):
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_set(data, "senior")

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

    def fake_emit(join_glyphs, pixel_width, pixel_height):
        assert join_glyphs is compiled.join_glyphs
        assert pixel_width == data["metadata"]["pixel_size"]
        assert pixel_height == data["metadata"]["pixel_size"]
        return None

    monkeypatch.setattr(build_font, "emit_quikscript_senior_features", fake_emit)

    build_font.build_font(data, variant="senior")
