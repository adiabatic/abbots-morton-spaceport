from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_font
import glyph_compiler
from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_fea import (
    _analyze_quikscript_joins,
    _coalesce_consecutive_ignore_rules,
    _format_post_liga_cleanup_rules,
    emit_quikscript_senior_features,
)
from quikscript_ir import (
    JoinGlyph,
    _resolve_family_record,
    _widen_bitmap_right_with_connector,
    compile_glyph_families,
    compile_quikscript_ir,
    expand_join_transforms,
    expand_selectors_for_ligatures,
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


def test_contract_exit_before_shifts_anchor_left_without_widening_bitmap():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": [" ##", "#  ", "#  "],
                        "anchors": {
                            "entry": [0, 0],
                            "exit": [2, 2],
                        },
                        "derive": {
                            "contract_exit_before": {"by": 1, "targets": [{"family": "qsFollow"}]},
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["##"],
                        "anchors": {
                            "entry": [0, 2],
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "junior",
    )

    expanded, transforms = expand_join_transforms(glyphs, has_zwnj=False)

    assert "qsLead.exit-contracted" in expanded
    contracted = expanded["qsLead.exit-contracted"]
    assert contracted.exit == ((1, 2),)
    assert contracted.bitmap == (" ##", "#  ", "#  ")
    assert contracted.before == ("qsFollow",)
    assert contracted.transform_kind == "exit-contracted"
    assert "exit-contracted" in contracted.modifiers
    assert "exit-contracted" in contracted.compat_assertions
    assert "contracted" in contracted.compat_assertions
    assert any(t.kind == "exit-contracted" for t in transforms)


def test_contract_exit_before_preserves_compatible_context_on_generated_sibling():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "exit": [3, 0],
                        },
                        "derive": {
                            "contract_exit_before": {
                                "by": 1,
                                "targets": [{"family": "qsFollow"}],
                            },
                        },
                    },
                    "forms": {
                        "entry_top": {
                            "shape": "prop",
                            "anchors": {
                                "entry": [0, 0],
                                "exit": [3, 0],
                            },
                            "select": {
                                "before": [
                                    {"family": "qsFollow"},
                                    {"family": "qsOther"},
                                ],
                            },
                            "modifiers": ["entry-top"],
                        },
                        "before_other": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [3, 0],
                            },
                            "select": {
                                "before": [{"family": "qsOther"}],
                            },
                            "modifiers": ["before-other"],
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                        },
                    },
                },
                "qsOther": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "senior",
    )

    expanded, _ = expand_join_transforms(glyphs, has_zwnj=False)

    assert expanded["qsLead.entry-top.exit-contracted"].before == ("qsFollow",)
    assert expanded["qsLead.before-other.exit-contracted"].before == ()


def test_contract_exit_before_emits_paired_trimmed_receiver():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": [" ##", "#  ", "#  "],
                        "anchors": {
                            "entry": [0, 0],
                            "exit": [2, 2],
                        },
                        "derive": {
                            "contract_exit_before": {"by": 2, "targets": [{"family": "qsFollow"}]},
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["##  ", "##  ", "##  "],
                        "anchors": {
                            "entry": [1, 2],
                            "exit": [3, 0],
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "junior",
    )

    expanded, transforms = expand_join_transforms(glyphs, has_zwnj=False)

    assert "qsLead.exit-doubly-contracted" in expanded
    contracted = expanded["qsLead.exit-doubly-contracted"]
    assert contracted.exit == ((0, 2),)
    assert contracted.before == ("qsFollow",)
    assert "qsLead.exit-contracted" not in expanded

    assert "qsFollow.entry-trimmed-by-2" in expanded
    trimmed = expanded["qsFollow.entry-trimmed-by-2"]
    assert trimmed.bitmap == ("    ", "##  ", "##  ")
    assert trimmed.entry == ((1, 2),)
    assert trimmed.exit == ((3, 0),)
    assert trimmed.y_offset == 0
    assert trimmed.after == ("qsLead.exit-doubly-contracted",)
    assert trimmed.before == ()
    assert trimmed.transform_kind == "entry-trimmed"
    assert "entry-trimmed-by-2" in trimmed.modifiers
    assert {
        "entry",
        "trimmed",
        "entry-trimmed",
        "entry-trimmed-by-2",
    } <= trimmed.compat_assertions
    assert any(
        t.kind == "entry-trimmed" and t.target_name == "qsFollow.entry-trimmed-by-2"
        for t in transforms
    )


def test_contract_exit_before_trim_depth_matches_by():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": [" ##", "#  ", "#  "],
                        "anchors": {"entry": [0, 0], "exit": [2, 2]},
                        "derive": {
                            "contract_exit_before": {"by": 1, "targets": [{"family": "qsFollow"}]},
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["###  ", "###  ", "###  "],
                        "anchors": {"entry": [1, 2], "exit": [4, 0]},
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "junior",
    )

    expanded, _ = expand_join_transforms(glyphs, has_zwnj=False)

    assert "qsFollow.entry-trimmed-by-1" in expanded
    trimmed = expanded["qsFollow.entry-trimmed-by-1"]
    assert trimmed.bitmap == (" ##  ", "###  ", "###  ")
    assert trimmed.after == ("qsLead.exit-contracted",)


def test_contract_exit_before_merges_trimmed_receivers_across_sources():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLeadA": {
                    "prop": {
                        "bitmap": [" ##", "#  ", "#  "],
                        "anchors": {"entry": [0, 0], "exit": [2, 2]},
                        "derive": {
                            "contract_exit_before": {"by": 2, "targets": [{"family": "qsFollow"}]},
                        },
                    },
                },
                "qsLeadB": {
                    "prop": {
                        "bitmap": [" ##", "#  ", "#  "],
                        "anchors": {"entry": [0, 0], "exit": [2, 2]},
                        "derive": {
                            "contract_exit_before": {"by": 2, "targets": [{"family": "qsFollow"}]},
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["##  ", "##  ", "##  "],
                        "anchors": {"entry": [1, 2], "exit": [3, 0]},
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "junior",
    )

    expanded, _ = expand_join_transforms(glyphs, has_zwnj=False)

    assert "qsFollow.entry-trimmed-by-2" in expanded
    trimmed = expanded["qsFollow.entry-trimmed-by-2"]
    assert trimmed.after == (
        "qsLeadA.exit-doubly-contracted",
        "qsLeadB.exit-doubly-contracted",
    )
    assert trimmed.bitmap == ("    ", "##  ", "##  ")


def test_contract_entry_after_shifts_entry_right_without_widening_bitmap():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"exit": [1, 0]},
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["###"],
                        "anchors": {"entry": [0, 0]},
                        "derive": {
                            "contract_entry_after": {"by": 1, "targets": [{"family": "qsLead"}]},
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "junior",
    )

    expanded, _ = expand_join_transforms(glyphs, has_zwnj=False)

    assert "qsFollow.entry-contracted" in expanded
    contracted = expanded["qsFollow.entry-contracted"]
    assert contracted.entry == ((1, 0),)
    assert contracted.bitmap == ("###",)
    assert contracted.after == ("qsLead",)
    assert contracted.transform_kind == "entry-contracted"


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


def test_coalesce_consecutive_ignore_rules_groups_unmarked_context_glyphs():
    lines = [
        "    lookup calt_sample {",
        "        ignore sub qsA qsTarget' @entry_y5;",
        "        ignore sub qsB qsTarget' @entry_y5;",
        "        sub qsTarget' @entry_y5 by qsTarget.exit-xheight;",
        "        ignore sub qsC qsOther' @entry_y0;",
        "        ignore sub qsD qsOther' @entry_y0;",
        "    } calt_sample;",
    ]

    assert _coalesce_consecutive_ignore_rules(lines) == [
        "    lookup calt_sample {",
        "        ignore sub [qsA qsB] qsTarget' @entry_y5;",
        "        sub qsTarget' @entry_y5 by qsTarget.exit-xheight;",
        "        ignore sub [qsC qsD] qsOther' @entry_y0;",
        "    } calt_sample;",
    ]


def test_coalesce_consecutive_ignore_rules_does_not_group_marked_glyphs():
    lines = [
        "        ignore sub qsA' qsTarget;",
        "        ignore sub qsB' qsTarget;",
    ]

    assert _coalesce_consecutive_ignore_rules(lines) == lines


def test_coalesce_consecutive_ignore_rules_keeps_unparsed_lines_as_barriers():
    lines = [
        "        ignore sub qsA qsTarget' @entry_y5;",
        "        ignore sub [qsBroken qsTarget' @entry_y5;",
        "        ignore sub qsB qsTarget' @entry_y5;",
    ]

    assert _coalesce_consecutive_ignore_rules(lines) == lines


def test_format_post_liga_cleanup_rules_groups_ligature_contexts():
    assert _format_post_liga_cleanup_rules([
        ("qsLigOne", "qsRight.entry-xheight", "qsRight"),
        ("qsLigTwo", "qsRight.entry-xheight", "qsRight"),
        ("qsLigTwo", "qsOther.entry-baseline", "qsOther"),
    ]) == [
        "        sub [qsLigOne qsLigTwo] qsRight.entry-xheight' by qsRight;",
        "        sub qsLigTwo qsOther.entry-baseline' by qsOther;",
    ]


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


def test_fwd_pair_skips_entry_variant_with_unreachable_exit():
    data = load_glyph_data(ROOT / "glyph_data")
    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    fea = emit_quikscript_senior_features(join_glyphs, 50, 50)
    assert fea is not None

    assert "sub qsIt.entry-xheight' [qsCheer" not in fea
    assert "sub qsIt.entry-xheight.entry-extended' [qsCheer" not in fea
    assert "by qsIt.entry-xheight.exit-extended;" not in fea

    assert (
        "sub qsIt' [qsCheer qsCheer.entry-extended qsCheer.noentry]"
        " by qsIt.exit-xheight.exit-extended;"
    ) in fea


def test_senior_feature_emitter_derives_mid_entry_strip_guards():
    join_glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {
                "uni200C": {
                    "bitmap": [],
                    "advance_width": 0,
                },
            },
            "glyph_families": {
                "qsLeft": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 5],
                            "exit": [1, 5],
                        },
                    },
                    "forms": {
                        "exit_baseline": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [1, 0],
                            },
                            "modifiers": ["exit-baseline"],
                        },
                    },
                },
                "qsMid": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                        },
                    },
                    "forms": {
                        "exit_baseline": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [1, 0],
                            },
                            "select": {
                                "before": [{"family": "qsRight"}],
                            },
                            "modifiers": ["exit-baseline"],
                        },
                    },
                },
                "qsRight": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "senior",
    )

    fea = emit_quikscript_senior_features(join_glyphs, 50, 50)
    assert fea is not None

    assert "ignore sub qsLeft' [qsMid qsMid.exit-baseline] [qsRight];" in fea
    assert "ignore sub qsLeft.noentry' [qsMid qsMid.exit-baseline] [qsRight];" in fea


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


def _make_join_glyph(
    name: str,
    *,
    base_name: str | None = None,
    sequence: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
    not_before: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
    not_after: tuple[str, ...] = (),
    gated_before: tuple[tuple[str, tuple[str, ...]], ...] = (),
    is_noentry: bool = False,
    extended_entry_suffix: str | None = None,
    extended_exit_suffix: str | None = None,
    entry: tuple[tuple[int, int], ...] = (),
    exit: tuple[tuple[int, int], ...] = (),
) -> JoinGlyph:
    return JoinGlyph(
        name=name,
        base_name=base_name or name.split(".")[0],
        family=None,
        sequence=tuple(sequence),
        traits=frozenset(),
        modifiers=(),
        compat_assertions=frozenset(),
        entry=tuple(entry),
        entry_curs_only=(),
        exit=tuple(exit),
        exit_ink_y=None,
        after=tuple(after),
        before=tuple(before),
        not_after=tuple(not_after),
        not_before=tuple(not_before),
        reverse_upgrade_from=(),
        preferred_over=(),
        word_final=False,
        is_contextual=False,
        is_entry_variant=False,
        entry_suffix=None,
        exit_suffix=None,
        extended_entry_suffix=extended_entry_suffix,
        extended_exit_suffix=extended_exit_suffix,
        entry_restriction_y=None,
        is_noentry=is_noentry,
        bitmap=(),
        y_offset=0,
        advance_width=None,
        extend_entry_after=None,
        extend_exit_before=None,
        noentry_after=(),
        extend_exit_no_entry=False,
        gated_before=tuple(gated_before),
    )


def test_expand_selectors_adds_first_component_to_forward_selector():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB", "qsA")


def test_expand_selectors_does_not_add_first_component_when_only_first_named():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsA",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsA",)


def test_expand_selectors_skips_when_source_has_no_exit_anchor():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB",)


def test_expand_selectors_skips_endpoint_when_ligature_lacks_matching_entry():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",), exit=((1, 5),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB",)


def test_expand_selectors_adds_last_component_to_backward_selector():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsA",), entry=((0, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsRight"].after == ("qsA", "qsB")


def test_expand_selectors_does_not_add_last_when_only_last_named():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsB",), entry=((0, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsRight"].after == ("qsB",)


def test_expand_selectors_skips_when_source_has_no_entry_anchor():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsA",)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsRight"].after == ("qsA",)


def test_expand_selectors_recognizes_family_variants_in_selector_lists():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB.alt",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsB.alt": _make_join_glyph("qsB.alt", base_name="qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB.alt", "qsA")


def test_expand_selectors_handles_multi_component_ligatures():
    metadata = {
        "qsLeftBeforeMid": _make_join_glyph(
            "qsLeftBeforeMid", before=("qsB",), exit=((1, 0),)
        ),
        "qsLeftBeforeLast": _make_join_glyph(
            "qsLeftBeforeLast", before=("qsC",), exit=((1, 0),)
        ),
        "qsRightAfterMid": _make_join_glyph(
            "qsRightAfterMid", after=("qsB",), entry=((0, 0),)
        ),
        "qsRightAfterFirst": _make_join_glyph(
            "qsRightAfterFirst", after=("qsA",), entry=((0, 0),)
        ),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsC": _make_join_glyph("qsC"),
        "qsA_qsB_qsC": _make_join_glyph(
            "qsA_qsB_qsC",
            sequence=("qsA", "qsB", "qsC"),
            entry=((0, 0),),
            exit=((3, 0),),
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeftBeforeMid"].before == ("qsB", "qsA")
    assert expanded["qsLeftBeforeLast"].before == ("qsC", "qsA")
    assert expanded["qsRightAfterMid"].after == ("qsB", "qsC")
    assert expanded["qsRightAfterFirst"].after == ("qsA", "qsC")


def test_expand_selectors_leaves_negative_selectors_untouched():
    metadata = {
        "qsLeft": _make_join_glyph(
            "qsLeft", not_before=("qsB",), not_after=("qsA",)
        ),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB")),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].not_before == ("qsB",)
    assert expanded["qsLeft"].not_after == ("qsA",)


def test_expand_selectors_preserves_gated_before_keys_and_expands_per_tag():
    metadata = {
        "qsLeft": _make_join_glyph(
            "qsLeft",
            gated_before=(("ss03", ("qsB",)),),
            exit=((1, 0),),
        ),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].gated_before == (("ss03", ("qsB", "qsA")),)


def test_expand_selectors_is_idempotent():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph(
            "qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)
        ),
    }

    once = expand_selectors_for_ligatures(metadata)
    twice = expand_selectors_for_ligatures(once)

    assert twice["qsLeft"].before == once["qsLeft"].before == ("qsB", "qsA")


def test_expand_selectors_skips_noentry_and_extended_ligature_records():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB.noentry": _make_join_glyph(
            "qsA_qsB.noentry",
            base_name="qsA_qsB",
            sequence=("qsA", "qsB"),
            is_noentry=True,
            entry=((0, 0),),
        ),
        "qsA_qsB.entry-extended": _make_join_glyph(
            "qsA_qsB.entry-extended",
            base_name="qsA_qsB",
            sequence=("qsA", "qsB"),
            extended_entry_suffix="entry-extended",
            entry=((0, 0),),
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB",)


def test_qs_it_before_utter_picks_up_qs_day_via_expansion_pass():
    data = load_glyph_data(ROOT / "glyph_data")
    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    record = join_glyphs["qsIt.before-utter"]
    assert "qsUtter" in record.before
    assert "qsDay" in record.before


def _family_level_derive_fixture():
    base_bitmap = [
        " ### ",
        "#   #",
        "#   #",
        "#   #",
        "#   #",
        " ### ",
    ]
    return {
        "prop": {"bitmap": list(base_bitmap), "y_offset": 0},
        "derive": {
            "extend_exit_before": {
                "by": 1,
                "targets": [{"family": "qsTea"}, {"family": "qsThaw"}],
            },
        },
        "forms": {
            "plain": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "modifiers": ["plain"],
            },
            "with_form_derive": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "derive": {
                    "extend_entry_after": {
                        "by": 1,
                        "targets": [{"family": "qsMay"}],
                    },
                },
                "modifiers": ["with-form-derive"],
            },
            "overrides_family_derive": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "derive": {
                    "extend_exit_before": {
                        "by": 2,
                        "targets": [{"family": "qsSee"}],
                    },
                },
                "modifiers": ["overrides-family-derive"],
            },
            "opts_out_of_family_derive": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "derive": {"extend_exit_before": None},
                "modifiers": ["opts-out-of-family-derive"],
            },
        },
    }


def test_family_level_derive_merges_into_each_form():
    family_def = _family_level_derive_fixture()

    plain = _resolve_family_record("qsTest", family_def, "plain", {}, [])
    with_form = _resolve_family_record("qsTest", family_def, "with_form_derive", {}, [])

    assert plain["derive"]["extend_exit_before"] == {
        "by": 1,
        "targets": [{"family": "qsTea"}, {"family": "qsThaw"}],
    }
    assert with_form["derive"]["extend_exit_before"] == {
        "by": 1,
        "targets": [{"family": "qsTea"}, {"family": "qsThaw"}],
    }
    assert with_form["derive"]["extend_entry_after"] == {
        "by": 1,
        "targets": [{"family": "qsMay"}],
    }


def test_form_level_derive_overrides_family_level():
    family_def = _family_level_derive_fixture()

    overridden = _resolve_family_record(
        "qsTest", family_def, "overrides_family_derive", {}, []
    )

    assert overridden["derive"]["extend_exit_before"] == {
        "by": 2,
        "targets": [{"family": "qsSee"}],
    }


def test_form_level_null_clears_family_level_derive():
    family_def = _family_level_derive_fixture()

    cleared = _resolve_family_record(
        "qsTest", family_def, "opts_out_of_family_derive", {}, []
    )

    assert "extend_exit_before" not in cleared.get("derive", {})


def test_family_level_derive_applies_to_bare_record():
    family_def = _family_level_derive_fixture()

    bare = _resolve_family_record("qsTest", family_def, "prop", {}, [])

    assert bare["derive"]["extend_exit_before"] == {
        "by": 1,
        "targets": [{"family": "qsTea"}, {"family": "qsThaw"}],
    }


def test_inherits_does_not_leak_parent_derive_to_child():
    family_def = {
        "prop": {"bitmap": [" ### "], "y_offset": 0},
        "forms": {
            "parent": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "derive": {
                    "extend_exit_before": {
                        "by": 1,
                        "targets": [{"family": "qsTea"}],
                    },
                },
                "modifiers": ["parent"],
            },
            "child": {
                "inherits": "parent",
                "modifiers": ["child"],
            },
        },
    }

    child = _resolve_family_record("qsTest", family_def, "child", {}, [])

    assert "derive" not in child or child["derive"] == {}


def test_family_level_derive_filters_unreachable_targets_per_form():
    glyph_families = {
        "qsSource": {
            "prop": {
                "bitmap": [" ### ", " ### "],
                "y_offset": 0,
            },
            "derive": {
                "extend_exit_before": {
                    "by": 1,
                    "targets": [{"family": "qsBaseline"}, {"family": "qsXheight"}],
                },
            },
            "forms": {
                "exits_baseline": {
                    "shape": "prop",
                    "anchors": {"exit": [3, 0]},
                    "modifiers": ["exits-baseline"],
                },
                "exits_xheight": {
                    "shape": "prop",
                    "anchors": {"exit": [3, 5]},
                    "modifiers": ["exits-xheight"],
                },
            },
        },
        "qsBaseline": {
            "prop": {
                "bitmap": [" ### "],
                "y_offset": 0,
                "anchors": {"entry": [1, 0]},
            },
        },
        "qsXheight": {
            "prop": {
                "bitmap": [" ### "],
                "y_offset": 0,
                "anchors": {"entry": [1, 5]},
            },
        },
    }
    source_def = glyph_families["qsSource"]

    exits_baseline = _resolve_family_record(
        "qsSource", source_def, "exits_baseline", {}, [],
        glyph_families=glyph_families,
    )
    exits_xheight = _resolve_family_record(
        "qsSource", source_def, "exits_xheight", {}, [],
        glyph_families=glyph_families,
    )

    assert exits_baseline["derive"]["extend_exit_before"]["targets"] == [
        {"family": "qsBaseline"},
    ]
    assert exits_xheight["derive"]["extend_exit_before"]["targets"] == [
        {"family": "qsXheight"},
    ]


def test_family_level_derive_drops_directive_when_no_targets_reachable():
    glyph_families = {
        "qsSource": {
            "prop": {"bitmap": [" ### "], "y_offset": 0},
            "derive": {
                "extend_exit_before": {
                    "by": 1,
                    "targets": [{"family": "qsXheightOnly"}],
                },
            },
            "forms": {
                "exits_baseline": {
                    "shape": "prop",
                    "anchors": {"exit": [3, 0]},
                    "modifiers": ["exits-baseline"],
                },
            },
        },
        "qsXheightOnly": {
            "prop": {
                "bitmap": [" ### "],
                "y_offset": 0,
                "anchors": {"entry": [1, 5]},
            },
        },
    }
    source_def = glyph_families["qsSource"]

    resolved = _resolve_family_record(
        "qsSource", source_def, "exits_baseline", {}, [],
        glyph_families=glyph_families,
    )

    assert "extend_exit_before" not in resolved.get("derive", {})


def test_unknown_family_level_derive_directive_errors_at_compile_time():
    family_def = {
        "prop": {"bitmap": [" ### "], "y_offset": 0},
        "derive": {"not_a_real_directive": {"foo": "bar"}},
        "forms": {
            "plain": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "modifiers": ["plain"],
            },
        },
    }

    import pytest

    with pytest.raises(ValueError, match="not_a_real_directive"):
        compile_glyph_families({"qsTest": family_def}, "senior")
