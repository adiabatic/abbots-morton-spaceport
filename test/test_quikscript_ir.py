from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_font import compile_glyph_definitions, load_glyph_data
from quikscript_fea import emit_quikscript_calt, emit_quikscript_curs
from quikscript_ir import (
    _widen_bitmap_right_with_connector,
    build_join_glyphs,
    compile_glyph_families,
    compile_quikscript_ir,
    expand_join_transforms,
    flatten_join_glyphs,
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


def test_compile_quikscript_ir_matches_family_inventory_in_senior_build():
    data = load_glyph_data(ROOT / "glyph_data")
    compiled = compile_glyph_definitions(data, "senior")
    join_glyphs, transforms = compile_quikscript_ir(data, "senior")
    flattened = flatten_join_glyphs(join_glyphs)

    compiled_family = {
        glyph_name: glyph_def
        for glyph_name, glyph_def in compiled.items()
        if glyph_def is not None and glyph_def.get("_family") is not None
    }

    assert flattened.keys() == compiled_family.keys()
    assert transforms
    assert flattened == compiled_family


def test_glyph_name_normalization_handles_middle_embedded_prop_suffix():
    assert get_base_glyph_name("U.prop.narrow") == "U.narrow"
    assert resolve_known_glyph_names(("U.prop.narrow",), {"U.narrow"}) == ["U.narrow"]


def test_expand_join_transforms_tracks_generated_sources_and_kinds():
    glyphs = build_join_glyphs(
        compile_glyph_families(
            {
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
            "senior",
        )
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
    join_glyphs = build_join_glyphs(
        compile_glyph_families(
            {
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
            "senior",
        )
    )

    expanded, _ = expand_join_transforms(join_glyphs, has_zwnj=True)
    curs = emit_quikscript_curs(expanded, 50, 50)

    assert "feature curs {" in curs
    assert "pos cursive qsLead <anchor 0 0> <anchor 50 0>;" in curs
    assert "pos cursive qsLead.noentry <anchor NULL> <anchor 50 0>;" in curs
