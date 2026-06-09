from functools import cache
from pathlib import Path
import re
import sys
import warnings
from typing import Any

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
    _select_rule_neighbors,
    emit_quikscript_senior_features,
)
from quikscript_ir import (
    GlyphData,
    JoinGlyph,
    LigatureEntryInheritanceWarning,
    _resolve_family_record,
    _widen_bitmap_right_with_connector,
    compile_glyph_families,
    compile_quikscript_ir,
    expand_join_transforms,
    expand_selectors_for_ligatures,
    get_base_glyph_name,
    resolve_known_glyph_names,
)
from review_scoped_anchor_selectors import (
    VariantExample,
    VariantExampleFinder,
    _build_review_font,
    _glyph_name_html,
    _hb_font,
    _load_ps_names,
    _review_context_sequences,
    _rows_for_variants,
    apply_suggestions_to_glyph_data,
)
from suggest_scoped_anchor_selectors import (
    ScopedAnchorSuggestion,
    suggest_scoped_anchor_selectors,
)


@cache
def _real_glyph_data() -> GlyphData:
    return load_glyph_data(ROOT / "glyph_data")


@cache
def _real_senior_join_glyphs() -> dict[str, JoinGlyph]:
    join_glyphs, _ = compile_quikscript_ir(_real_glyph_data(), "senior")
    return join_glyphs


@cache
def _real_senior_compiled():
    return compile_glyph_set(_real_glyph_data(), "senior")


@cache
def _real_senior_fea() -> str:
    fea = emit_quikscript_senior_features(_real_senior_join_glyphs(), 50, 50)
    assert fea is not None
    return fea


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
    compiled = _real_senior_compiled()

    assert compiled.glyph_definitions
    assert compiled.glyph_meta
    assert compiled.join_glyphs


def test_qs_see_keeps_its_y6_forward_lookup_early_when_ye_blocks_its_entry():
    compiled = _real_senior_compiled()
    analysis = _analyze_quikscript_joins(compiled.glyph_meta)

    assert "qsSee" in analysis.early_pair_fwd_general
    assert analysis.early_pair_fwd_general_exit_ys["qsSee"] == {6}


def test_compiled_glyph_definitions_do_not_export_compiler_metadata_keys():
    compiled = _real_senior_compiled()

    for glyph_name in (
        "qsTea",
        "qsDay_qsUtter",
        "qsDay_qsUtter.noentry",
        "qsMay.en-y0.ex-y5.en-ext-1",
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
    assert "qsLead.ex-ext-1" in expanded
    assert "qsFollow.en-ext-1" in expanded
    assert expanded["qsLead.noentry"].generated_from == "qsLead"
    assert expanded["qsLead.ex-ext-1"].transform_kind == "ex-ext-1"
    assert expanded["qsFollow.en-ext-1"].transform_kind == "en-ext-1"
    assert {transform.kind for transform in transforms} == {
        "en-ext-1",
        "ex-ext-1",
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

    assert "qsLead.ex-con-1" in expanded
    contracted = expanded["qsLead.ex-con-1"]
    assert contracted.exit == ((1, 2),)
    assert contracted.bitmap == (" ##", "#  ", "#  ")
    assert contracted.before == ("qsFollow",)
    assert contracted.transform_kind == "ex-con-1"
    assert "ex-con-1" in contracted.modifiers
    assert "ex-con-1" in contracted.compat_assertions
    assert "contracted" in contracted.compat_assertions
    assert any(t.kind == "ex-con-1" for t in transforms)


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
                    "stances": {
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
                            "modifiers": ["en-y0"],
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

    assert expanded["qsLead.en-y0.ex-y0.ex-con-1"].before == ("qsFollow",)
    assert expanded["qsLead.ex-y0.before-other.ex-con-1"].before == ()


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

    assert "qsLead.ex-con-2" in expanded
    contracted = expanded["qsLead.ex-con-2"]
    assert contracted.exit == ((0, 2),)
    assert contracted.before == ("qsFollow",)
    assert "qsLead.ex-con-1" not in expanded

    assert "qsFollow.en-trim-2" in expanded
    trimmed = expanded["qsFollow.en-trim-2"]
    assert trimmed.bitmap == ("    ", "##  ", "##  ")
    assert trimmed.entry == ((1, 2),)
    assert trimmed.exit == ((3, 0),)
    assert trimmed.y_offset == 0
    assert trimmed.after == ("qsLead.ex-con-2",)
    assert trimmed.before == ()
    assert trimmed.transform_kind == "entry-trimmed"
    assert "en-trim-2" in trimmed.modifiers
    assert {
        "entry",
        "trimmed",
        "en-trim",
        "en-trim-2",
    } <= trimmed.compat_assertions
    assert any(t.kind == "entry-trimmed" and t.target_name == "qsFollow.en-trim-2" for t in transforms)


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

    assert "qsFollow.en-trim-1" in expanded
    trimmed = expanded["qsFollow.en-trim-1"]
    assert trimmed.bitmap == (" ##  ", "###  ", "###  ")
    assert trimmed.after == ("qsLead.ex-con-1",)


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

    assert "qsFollow.en-trim-2" in expanded
    trimmed = expanded["qsFollow.en-trim-2"]
    assert trimmed.after == (
        "qsLeadA.ex-con-2",
        "qsLeadB.ex-con-2",
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

    assert "qsFollow.en-con-1" in expanded
    contracted = expanded["qsFollow.en-con-1"]
    assert contracted.entry == ((1, 0),)
    assert contracted.bitmap == ("###",)
    assert contracted.after == ("qsLead",)
    assert contracted.transform_kind == "en-con-1"


def test_extend_entry_after_targets_accept_family_scoped_anchor_selector():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"exit": [1, 5]},
                    },
                    "stances": {
                        "exit_baseline": {
                            "shape": "prop",
                            "anchors": {"exit": [1, 0]},
                            "modifiers": ["ex-y0"],
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["###"],
                        "anchors": {"entry": [0, 0]},
                        "derive": {
                            "extend_entry_after": {
                                "by": 1,
                                "targets": [{"family": "qsLead", "exit_y": 0}],
                            },
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "senior",
    )

    rules = glyphs["qsFollow"].extend_entry_after
    assert len(rules) == 1
    assert rules[0].targets == ("qsLead", "qsLead.ex-y0")
    assert glyphs["qsFollow.en-ext-1"].after == ("qsLead", "qsLead.ex-y0")


def test_extend_exit_before_targets_accept_family_scoped_anchor_selector():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"exit": [1, 0]},
                        "derive": {
                            "extend_exit_before": {
                                "by": 1,
                                "targets": [{"family": "qsFollow", "entry_y": 0}],
                            },
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"entry": [0, 5]},
                    },
                    "stances": {
                        "entry_baseline": {
                            "shape": "prop",
                            "anchors": {"entry": [0, 0]},
                            "modifiers": ["en-y0"],
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "senior",
    )

    rules = glyphs["qsLead"].extend_exit_before
    assert len(rules) == 1
    assert rules[0].targets == ("qsFollow", "qsFollow.en-y0")
    assert glyphs["qsLead.ex-ext-1"].before == ("qsFollow", "qsFollow.en-y0")


def test_contract_entry_after_targets_accept_family_scoped_anchor_selector():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"exit": [1, 5]},
                    },
                    "stances": {
                        "exit_baseline": {
                            "shape": "prop",
                            "anchors": {"exit": [1, 0]},
                            "modifiers": ["ex-y0"],
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["###"],
                        "anchors": {"entry": [0, 0]},
                        "derive": {
                            "contract_entry_after": {
                                "by": 1,
                                "targets": [{"family": "qsLead", "exit_y": 0}],
                            },
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "senior",
    )

    spec = glyphs["qsFollow"].contract_entry_after
    assert spec is not None
    assert spec.targets == ("qsLead", "qsLead.ex-y0")
    assert glyphs["qsFollow.en-con-1"].after == ("qsLead", "qsLead.ex-y0")


def test_contract_exit_before_targets_accept_family_scoped_anchor_selector():
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
                            "contract_exit_before": {
                                "by": 1,
                                "targets": [{"family": "qsFollow", "entry_y": 2}],
                            },
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["##"],
                        "anchors": {"entry": [0, 5]},
                    },
                    "stances": {
                        "entry_high": {
                            "shape": "prop",
                            "anchors": {"entry": [0, 2]},
                            "modifiers": ["en-high"],
                        },
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "senior",
    )

    spec = glyphs["qsLead"].contract_exit_before
    assert spec is not None
    assert spec.targets == ("qsFollow", "qsFollow.en-high")
    assert glyphs["qsLead.ex-con-1"].before == ("qsFollow", "qsFollow.en-high")


def test_extend_exit_before_targets_accept_bare_anchor_selector_with_except():
    glyphs, _ = compile_quikscript_ir(
        {
            "metadata": {},
            "glyphs": {},
            "glyph_families": {
                "qsLead": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"exit": [1, 0]},
                        "derive": {
                            "extend_exit_before": {
                                "by": 1,
                                "targets": [
                                    {
                                        "entry_y": 0,
                                        "except": [{"family": "qsExempt"}],
                                    }
                                ],
                            },
                        },
                    },
                },
                "qsFollow": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"entry": [0, 0]},
                    },
                },
                "qsExempt": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"entry": [0, 0]},
                    },
                },
            },
            "context_sets": {},
            "kerning": {},
        },
        "senior",
    )

    rules = glyphs["qsLead"].extend_exit_before
    assert len(rules) == 1
    assert "qsFollow" in rules[0].targets
    assert "qsExempt" not in rules[0].targets
    assert "qsFollow" in glyphs["qsLead.ex-ext-1"].before
    assert "qsExempt" not in glyphs["qsLead.ex-ext-1"].before


def test_contract_entry_after_by_two_trims_receivers_left_ink_at_entry_row():
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
                        "bitmap": ["###  "],
                        "anchors": {"entry": [0, 0]},
                        "derive": {
                            "contract_entry_after": {"by": 2, "targets": [{"family": "qsLead"}]},
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

    assert "qsFollow.en-con-2" in expanded
    contracted = expanded["qsFollow.en-con-2"]
    assert contracted.entry == ((2, 0),)
    assert contracted.bitmap == (" ##  ",)
    assert contracted.after == ("qsLead",)
    assert contracted.transform_kind == "en-con-2"


def test_senior_feature_emitter_includes_join_and_gate_features():
    fea = _real_senior_fea()

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
        "        sub qsTarget' @entry_y5 by qsTarget.ex-y5;",
        "        ignore sub qsC qsOther' @entry_y0;",
        "        ignore sub qsD qsOther' @entry_y0;",
        "    } calt_sample;",
    ]

    assert _coalesce_consecutive_ignore_rules(lines) == [
        "    lookup calt_sample {",
        "        ignore sub [qsA qsB] qsTarget' @entry_y5;",
        "        sub qsTarget' @entry_y5 by qsTarget.ex-y5;",
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
    assert _format_post_liga_cleanup_rules(
        [
            ("qsLigOne", "qsRight.en-y5", "qsRight"),
            ("qsLigTwo", "qsRight.en-y5", "qsRight"),
            ("qsLigTwo", "qsOther.en-y0", "qsOther"),
        ]
    ) == [
        "        sub [qsLigOne qsLigTwo] qsRight.en-y5' by qsRight;",
        "        sub qsLigTwo qsOther.en-y0' by qsOther;",
    ]


def test_senior_feature_emitter_excludes_not_after_families_from_pair_after_class():
    join_glyphs = _real_senior_join_glyphs()
    fea = _real_senior_fea()

    start = fea.index("lookup calt_pair_qsThaw_after-tall {")
    end = fea.index("} calt_pair_qsThaw_after-tall;", start)
    block = fea[start:end]

    sub_line = next(
        line
        for line in block.splitlines()
        if line.strip().startswith("sub [") and line.rstrip().endswith("by qsThaw.after-tall;")
    )
    after_class = sub_line[sub_line.index("[") + 1 : sub_line.index("]")]
    after_glyphs = after_class.split()

    qsing_variants = {glyph for glyph in join_glyphs if glyph == "qsIng" or glyph.startswith("qsIng.")}
    assert qsing_variants  # sanity check the family exists
    assert not (set(after_glyphs) & qsing_variants), (
        f"calt_pair_qsThaw_after-tall must not fire after qsIng variants; "
        f"found {sorted(set(after_glyphs) & qsing_variants)}"
    )


def test_senior_feature_emitter_requires_concrete_exit_reachability_for_after_class():
    fea = _real_senior_fea()

    start = fea.index("lookup calt_pair_qsPea_en-y5_ex-y0 {")
    end = fea.index("} calt_pair_qsPea_en-y5_ex-y0;", start)
    block = fea[start:end]

    sub_line = next(
        line
        for line in block.splitlines()
        if line.strip().startswith("sub [") and line.rstrip().endswith("by qsPea.en-y5.ex-y0;")
    )
    after_class = sub_line[sub_line.index("[") + 1 : sub_line.index("]")]
    after_glyphs = set(after_class.split())

    assert {
        "qsMay",
        "qsMay.en-y0.ex-y5",
        "qsMay.en-y0.ex-y5.en-ext-1",
        "qsMay.ex-ext-1",
    } <= after_glyphs
    assert not (
        {
            "qsMay.en-y5",
            "qsMay.en-y5.after-fee",
            "qsMay.en-y5.after-fee.en-ext-1",
            "qsMay.en-y5.after-i",
            "qsMay.en-y5.after-i.en-ext-1",
            "qsMay.en-y5.en-ext-1",
        }
        & after_glyphs
    )


def test_senior_feature_emitter_uses_upgrade_for_terminal_qs_owe_pair_exit():
    fea = _real_senior_fea()

    pair_lookup = "lookup calt_pair_qsOwe_en-y5_en-ext-1 {"
    upgrade_lookup = "lookup calt_upgrade_qsOwe_en-y5_ex-y5_en-ext-1 {"

    assert pair_lookup in fea
    assert upgrade_lookup in fea
    assert "lookup calt_pair_qsOwe_en-y5_ex-y5_en-ext-1 {" not in fea
    assert fea.index(pair_lookup) < fea.index(upgrade_lookup)


def test_senior_feature_emitter_keeps_thaw_exit_baseline_before_ing_entry_extended():
    fea = _real_senior_fea()

    assert fea.index("lookup calt_fwd_pair_qsThaw_ex-y0 {") < fea.index("lookup calt_pair_qsIng_en-ext-1 {")


def test_fwd_pair_skips_entry_variant_with_unreachable_exit():
    fea = _real_senior_fea()

    assert "sub qsIt.en-y5.ex-y0' [qsCheer" not in fea
    assert "sub qsIt.en-y5.ex-y0.en-ext-1' [qsCheer" not in fea
    for line in fea.splitlines():
        if "by qsIt.en-y5.ex-y0.ex-ext-1;" in line:
            assert "qsCheer" not in line

    upgrade_lines = [
        line for line in fea.splitlines() if "sub qsIt'" in line and "by qsIt.ex-y5.ex-ext-1;" in line
    ]
    assert upgrade_lines, "expected qsIt -> qsIt.ex-y5.ex-ext-1 upgrade substitution"
    assert any("qsCheer" in line and "qsCheer.en-ext-1" in line for line in upgrade_lines)
    # The derived join contract drops qsCheer.noentry from this exit-y5 upgrade: it enters nowhere (entry_ys == ()), so it cannot cursively join qsIt's exit and was a single-rule leak. qsCheer and qsCheer.en-ext-1 enter at y=5 and stay.
    assert all("qsCheer.noentry" not in line for line in upgrade_lines)


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
                    "stances": {
                        "exit_baseline": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [1, 0],
                            },
                            "modifiers": ["ex-y0"],
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
                    "stances": {
                        "exit_baseline": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [1, 0],
                            },
                            "select": {
                                "before": [{"family": "qsRight"}],
                            },
                            "modifiers": ["ex-y0"],
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

    assert "ignore sub qsLeft' [qsMid qsMid.ex-y0] qsRight;" in fea
    assert "ignore sub qsLeft.noentry' [qsMid qsMid.ex-y0] qsRight;" in fea


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
    compiled = _real_senior_compiled()
    fea = _real_senior_fea()

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

    def fake_emit(
        join_glyphs,
        pixel_width,
        pixel_height,
        restore_isolated_form_overrides=(),
        predecessor_demote_overrides=(),
        trailing_demote_overrides=(),
    ):
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
        extend_entry_after=(),
        extend_exit_before=(),
        noentry_after=(),
        extend_exit_no_entry=False,
        gated_before=tuple(gated_before),
    )


def test_expand_selectors_adds_first_component_to_forward_selector():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB", "qsA", "qsA_qsB")


def test_expand_selectors_does_not_add_first_component_when_only_first_named():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsA",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsA", "qsA_qsB")


def test_expand_selectors_skips_when_source_has_no_exit_anchor():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB",)


def test_expand_selectors_skips_endpoint_when_ligature_lacks_matching_entry():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",), exit=((1, 5),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB",)


def test_expand_selectors_adds_last_component_to_backward_selector():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsA",), entry=((0, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsRight"].after == ("qsA", "qsA_qsB", "qsB")


def test_expand_selectors_does_not_add_last_when_only_last_named():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsB",), entry=((0, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsRight"].after == ("qsB", "qsA_qsB")


def test_expand_selectors_skips_when_source_has_no_entry_anchor():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsA",)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsRight"].after == ("qsA",)


def test_expand_selectors_recognizes_family_variants_in_selector_lists():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB.alt",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsB.alt": _make_join_glyph("qsB.alt", base_name="qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    # `qsB.alt` is a specific trailing-component variant. With suffix-aware ligature keying, it doesn't match the base `qsA_qsB` (which doesn't represent an `alt` state). The existing pre-liga expansion still adds the lead component `qsA` so the selector fires when qsA literally precedes pre-liga.
    assert expanded["qsLeft"].before == ("qsB.alt", "qsA")


def test_expand_selectors_handles_multi_component_ligatures():
    metadata = {
        "qsLeftBeforeMid": _make_join_glyph("qsLeftBeforeMid", before=("qsB",), exit=((1, 0),)),
        "qsLeftBeforeLast": _make_join_glyph("qsLeftBeforeLast", before=("qsC",), exit=((1, 0),)),
        "qsRightAfterMid": _make_join_glyph("qsRightAfterMid", after=("qsB",), entry=((0, 0),)),
        "qsRightAfterFirst": _make_join_glyph("qsRightAfterFirst", after=("qsA",), entry=((0, 0),)),
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

    assert expanded["qsLeftBeforeMid"].before == ("qsB", "qsA", "qsA_qsB_qsC")
    assert expanded["qsLeftBeforeLast"].before == ("qsC", "qsA", "qsA_qsB_qsC")
    assert expanded["qsRightAfterMid"].after == ("qsB", "qsA_qsB_qsC", "qsC")
    assert expanded["qsRightAfterFirst"].after == ("qsA", "qsA_qsB_qsC", "qsC")


def test_expand_selectors_leaves_negative_selectors_untouched():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", not_before=("qsB",), not_after=("qsA",)),
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
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].gated_before == (("ss03", ("qsB", "qsA", "qsA_qsB")),)


def test_expand_selectors_is_idempotent():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsB",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    once = expand_selectors_for_ligatures(metadata)
    twice = expand_selectors_for_ligatures(once)

    assert twice["qsLeft"].before == once["qsLeft"].before == ("qsB", "qsA", "qsA_qsB")


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
        "qsA_qsB.en-ext-1": _make_join_glyph(
            "qsA_qsB.en-ext-1",
            base_name="qsA_qsB",
            sequence=("qsA", "qsB"),
            extended_entry_suffix="en-ext-1",
            entry=((0, 0),),
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert expanded["qsLeft"].before == ("qsB",)


def test_expand_selectors_adds_ligature_glyph_to_trailing_after_for_post_liga_match():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsB",), entry=((0, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert "qsA_qsB" in expanded["qsRight"].after


def test_expand_selectors_adds_ligature_glyph_to_leading_before_for_post_liga_match():
    metadata = {
        "qsLeft": _make_join_glyph("qsLeft", before=("qsA",), exit=((1, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), entry=((0, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert "qsA_qsB" in expanded["qsLeft"].before


def test_expand_selectors_adds_ligature_variants_to_trailing_after():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsB",), entry=((0, 0),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)),
        "qsA_qsB.ex-ext-1": _make_join_glyph(
            "qsA_qsB.ex-ext-1",
            base_name="qsA_qsB",
            sequence=("qsA", "qsB"),
            exit=((3, 0),),
        ),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert "qsA_qsB" in expanded["qsRight"].after
    assert "qsA_qsB.ex-ext-1" in expanded["qsRight"].after


def test_expand_selectors_skips_ligature_glyph_when_anchor_y_does_not_match():
    metadata = {
        "qsRight": _make_join_glyph("qsRight", after=("qsB",), entry=((0, 5),)),
        "qsA": _make_join_glyph("qsA"),
        "qsB": _make_join_glyph("qsB"),
        "qsA_qsB": _make_join_glyph("qsA_qsB", sequence=("qsA", "qsB"), exit=((2, 0),)),
    }

    expanded = expand_selectors_for_ligatures(metadata)

    assert "qsA_qsB" not in expanded["qsRight"].after


def test_qs_it_before_utter_picks_up_qs_day_via_expansion_pass():
    join_glyphs = _real_senior_join_glyphs()

    record = join_glyphs["qsIt.en-y0.ex-y0.before-utter"]
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
        "stances": {
            "plain": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "modifiers": ["plain"],
            },
            "with_stance_derive": {
                "shape": "prop",
                "anchors": {"exit": [5, 0]},
                "derive": {
                    "extend_entry_after": {
                        "by": 1,
                        "targets": [{"family": "qsMay"}],
                    },
                },
                "modifiers": ["with-stance-derive"],
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


def test_family_level_derive_merges_into_each_stance():
    family_def = _family_level_derive_fixture()

    plain = _resolve_family_record("qsTest", family_def, "plain", {}, [])
    with_stance = _resolve_family_record("qsTest", family_def, "with_stance_derive", {}, [])

    assert plain["derive"]["extend_exit_before"] == {
        "by": 1,
        "targets": [{"family": "qsTea"}, {"family": "qsThaw"}],
    }
    assert with_stance["derive"]["extend_exit_before"] == {
        "by": 1,
        "targets": [{"family": "qsTea"}, {"family": "qsThaw"}],
    }
    assert with_stance["derive"]["extend_entry_after"] == {
        "by": 1,
        "targets": [{"family": "qsMay"}],
    }


def test_stance_level_derive_overrides_family_level():
    family_def = _family_level_derive_fixture()

    overridden = _resolve_family_record("qsTest", family_def, "overrides_family_derive", {}, [])

    assert overridden["derive"]["extend_exit_before"] == {
        "by": 2,
        "targets": [{"family": "qsSee"}],
    }


def test_stance_level_null_clears_family_level_derive():
    family_def = _family_level_derive_fixture()

    cleared = _resolve_family_record("qsTest", family_def, "opts_out_of_family_derive", {}, [])

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
        "stances": {
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


def test_family_level_derive_filters_unreachable_targets_per_stance():
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
            "stances": {
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
        "qsSource",
        source_def,
        "exits_baseline",
        {},
        [],
        glyph_families=glyph_families,
    )
    exits_xheight = _resolve_family_record(
        "qsSource",
        source_def,
        "exits_xheight",
        {},
        [],
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
            "stances": {
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
        "qsSource",
        source_def,
        "exits_baseline",
        {},
        [],
        glyph_families=glyph_families,
    )

    assert "extend_exit_before" not in resolved.get("derive", {})


def test_unknown_family_level_derive_directive_errors_at_compile_time():
    family_def = {
        "prop": {"bitmap": [" ### "], "y_offset": 0},
        "derive": {"not_a_real_directive": {"foo": "bar"}},
        "stances": {
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


def test_select_before_and_not_before_cannot_share_family():
    families = {
        "qsLeft": {
            "prop": {"bitmap": [" ### "], "y_offset": 0},
            "stances": {
                "exit_baseline": {
                    "shape": "prop",
                    "anchors": {"exit": [5, 0]},
                    "select": {
                        "before": [{"family": "qsRight"}],
                        "not_before": [{"family": "qsRight"}],
                    },
                    "modifiers": ["ex-y0"],
                },
            },
        },
        "qsRight": {
            "prop": {"bitmap": [" ### "], "y_offset": 0},
        },
    }

    import pytest

    with pytest.raises(
        ValueError,
        match=r"qsLeft stance 'exit_baseline'.*select\.before.*select\.not_before.*qsRight",
    ):
        compile_glyph_families(families, "senior")


def test_select_after_and_not_after_cannot_share_family():
    families = {
        "qsLeft": {
            "prop": {"bitmap": [" ### "], "y_offset": 0},
        },
        "qsRight": {
            "prop": {"bitmap": [" ### "], "y_offset": 0},
            "stances": {
                "entry_baseline": {
                    "shape": "prop",
                    "anchors": {"entry": [0, 0]},
                    "select": {
                        "after": [{"family": "qsLeft"}],
                        "not_after": [{"family": "qsLeft"}],
                    },
                    "modifiers": ["en-y0"],
                },
            },
        },
    }

    import pytest

    with pytest.raises(
        ValueError,
        match=r"qsRight stance 'entry_baseline'.*select\.after.*select\.not_after.*qsLeft",
    ):
        compile_glyph_families(families, "senior")


def test_select_before_and_not_before_anchor_sentinel_does_not_collide_with_family():
    families = {
        "qsLeft": {
            "prop": {"bitmap": [" ### "], "y_offset": 0},
            "stances": {
                "exit_baseline": {
                    "shape": "prop",
                    "anchors": {"exit": [5, 0]},
                    "select": {
                        "before": [{"family": "qsRight"}],
                        "not_before": [{"entry_y": 0, "except": [{"family": "qsRight"}]}],
                    },
                    "modifiers": ["ex-y0"],
                },
            },
        },
        "qsRight": {
            "prop": {
                "bitmap": [" ### "],
                "y_offset": 0,
                "anchors": {"entry": [0, 0]},
            },
        },
    }

    compiled = compile_glyph_families(families, "senior")
    assert "qsLeft.ex-y0" in compiled


def test_family_scoped_anchor_selector_expands_only_matching_family_variants():
    data: GlyphData = {
        "metadata": {},
        "glyphs": {},
        "context_sets": {},
        "kerning": {},
        "glyph_families": {
            "qsLeft": {
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"exit": [1, 5]},
                },
                "stances": {
                    "entry_xheight": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 5]},
                        "modifiers": ["en-y5"],
                    },
                    "exit_baseline": {
                        "shape": "prop",
                        "anchors": {"exit": [1, 0]},
                        "modifiers": ["ex-y0"],
                    },
                },
            },
            "qsRight": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "entry_xheight": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 5]},
                        "select": {"after": [{"family": "qsLeft", "exit_y": 5}]},
                        "modifiers": ["en-y5"],
                    },
                },
            },
        },
    }

    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    assert join_glyphs["qsRight.en-y5"].after == ("qsLeft",)


def test_family_scoped_anchor_selector_respects_traits_and_modifiers():
    data: GlyphData = {
        "metadata": {},
        "glyphs": {},
        "context_sets": {},
        "kerning": {},
        "glyph_families": {
            "qsLeft": {
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"exit": [1, 5]},
                },
                "stances": {
                    "alt": {
                        "shape": "prop",
                        "anchors": {"exit": [1, 5]},
                        "traits": ["alt"],
                    },
                    "alt_before_right": {
                        "inherits": "alt",
                        "modifiers": ["before-right"],
                    },
                    "half": {
                        "shape": "prop",
                        "anchors": {"exit": [1, 5]},
                        "traits": ["half"],
                    },
                },
            },
            "qsRight": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "entry_xheight": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 5]},
                        "select": {
                            "after": [
                                {
                                    "family": "qsLeft",
                                    "traits": ["alt"],
                                    "exit_y": 5,
                                }
                            ]
                        },
                        "modifiers": ["en-y5"],
                    },
                },
            },
        },
    }

    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    assert join_glyphs["qsRight.en-y5"].after == (
        "qsLeft.alt.ex-y5",
        "qsLeft.alt.ex-y5.before-right",
    )


def test_family_scoped_anchor_selector_mirrors_ligature_family_expansion():
    data: GlyphData = {
        "metadata": {},
        "glyphs": {},
        "context_sets": {},
        "kerning": {},
        "glyph_families": {
            "qsRight": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "entry_baseline": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 0]},
                        "select": {"after": [{"family": "qsA", "exit_y": 0}]},
                        "modifiers": ["en-y0"],
                    },
                },
            },
            "qsA": {
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"exit": [1, 0]},
                },
            },
            "qsB": {
                "prop": {"bitmap": ["#"]},
            },
            "qsA_qsB": {
                "sequence": ["qsA", "qsB"],
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"exit": [2, 0]},
                },
            },
        },
    }

    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    assert join_glyphs["qsRight.en-y0"].after == ("qsA", "qsA_qsB", "qsB")


def test_family_scoped_anchor_selector_filters_ligature_expansion_by_y():
    data: GlyphData = {
        "metadata": {},
        "glyphs": {},
        "context_sets": {},
        "kerning": {},
        "glyph_families": {
            "qsRight": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "entry_xheight": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 5]},
                        "select": {"after": [{"family": "qsA", "exit_y": 5}]},
                        "modifiers": ["en-y5"],
                    },
                },
            },
            "qsA": {
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"exit": [1, 0]},
                },
            },
            "qsB": {
                "prop": {"bitmap": ["#"]},
            },
            "qsA_qsB": {
                "sequence": ["qsA", "qsB"],
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"exit": [2, 0]},
                },
            },
        },
    }

    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    assert join_glyphs["qsRight.en-y5"].after == ()


def test_family_scoped_entry_selector_includes_bare_upgradeable_follower():
    data: GlyphData = {
        "metadata": {},
        "glyphs": {},
        "context_sets": {},
        "kerning": {},
        "glyph_families": {
            "qsLeft": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "exit_baseline": {
                        "shape": "prop",
                        "anchors": {"exit": [1, 0]},
                        "select": {"before": [{"family": "qsRight", "entry_y": 0}]},
                        "modifiers": ["ex-y0"],
                    },
                },
            },
            "qsRight": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "entry_baseline": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 0]},
                        "modifiers": ["en-y0"],
                    },
                },
            },
        },
    }

    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    assert join_glyphs["qsLeft.ex-y0"].before == (
        "qsRight",
        "qsRight.en-y0",
    )


def test_family_scoped_exit_selector_includes_bare_upgradeable_predecessor():
    data: GlyphData = {
        "metadata": {},
        "glyphs": {},
        "context_sets": {},
        "kerning": {},
        "glyph_families": {
            "qsLeft": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "exit_baseline": {
                        "shape": "prop",
                        "anchors": {"exit": [1, 0]},
                        "modifiers": ["ex-y0"],
                    },
                },
            },
            "qsRight": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "entry_baseline": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 0]},
                        "select": {"after": [{"family": "qsLeft", "exit_y": 0}]},
                        "modifiers": ["en-y0"],
                    },
                },
            },
        },
    }

    join_glyphs, _ = compile_quikscript_ir(data, "senior")

    assert join_glyphs["qsRight.en-y0"].after == (
        "qsLeft",
        "qsLeft.ex-y0",
    )


def test_family_scoped_anchor_selector_rejects_invalid_y():
    import pytest

    families = {
        "qsLeft": {"prop": {"bitmap": ["#"]}},
        "qsRight": {
            "prop": {"bitmap": ["#"]},
            "stances": {
                "entry_xheight": {
                    "shape": "prop",
                    "anchors": {"entry": [0, 5]},
                    "select": {"after": [{"family": "qsLeft", "exit_y": "5"}]},
                    "modifiers": ["en-y5"],
                },
            },
        },
    }

    with pytest.raises(ValueError, match="exit_y must be an integer"):
        compile_glyph_families(families, "senior")


def test_family_scoped_anchor_selector_rejects_unknown_family():
    import pytest

    families = {
        "qsRight": {
            "prop": {"bitmap": ["#"]},
            "stances": {
                "entry_xheight": {
                    "shape": "prop",
                    "anchors": {"entry": [0, 5]},
                    "select": {"after": [{"family": "qsMissing", "exit_y": 5}]},
                    "modifiers": ["en-y5"],
                },
            },
        },
    }

    with pytest.raises(ValueError, match="unknown family 'qsMissing'"):
        compile_glyph_families(families, "senior")


def test_family_scoped_anchor_selector_rejects_invalid_trait():
    import pytest

    families = {
        "qsLeft": {"prop": {"bitmap": ["#"]}},
        "qsRight": {
            "prop": {"bitmap": ["#"]},
            "stances": {
                "entry_xheight": {
                    "shape": "prop",
                    "anchors": {"entry": [0, 5]},
                    "select": {
                        "after": [
                            {
                                "family": "qsLeft",
                                "traits": ["not-a-real-trait"],
                                "exit_y": 5,
                            }
                        ]
                    },
                    "modifiers": ["en-y5"],
                },
            },
        },
    }

    with pytest.raises(ValueError, match="unsupported trait 'not-a-real-trait'"):
        compile_glyph_families(families, "senior")


def test_family_scoped_anchor_selector_rejects_both_anchor_sides():
    import pytest

    families = {
        "qsLeft": {"prop": {"bitmap": ["#"]}},
        "qsRight": {
            "prop": {"bitmap": ["#"]},
            "stances": {
                "entry_xheight": {
                    "shape": "prop",
                    "anchors": {"entry": [0, 5]},
                    "select": {"after": [{"family": "qsLeft", "exit_y": 5, "entry_y": 5}]},
                    "modifiers": ["en-y5"],
                },
            },
        },
    }

    with pytest.raises(ValueError, match="exactly one of exit_y/entry_y"):
        compile_glyph_families(families, "senior")


def test_family_scoped_anchor_selector_collides_with_negative_family_selector():
    families = {
        "qsLeft": {
            "prop": {"bitmap": ["#"]},
            "stances": {
                "exit_baseline": {
                    "shape": "prop",
                    "anchors": {"exit": [1, 0]},
                    "select": {
                        "before": [{"family": "qsRight", "entry_y": 0}],
                        "not_before": [{"family": "qsRight"}],
                    },
                    "modifiers": ["ex-y0"],
                },
            },
        },
        "qsRight": {
            "prop": {
                "bitmap": ["#"],
                "anchors": {"entry": [0, 0]},
            },
        },
    }

    import pytest

    with pytest.raises(
        ValueError,
        match=r"select\.before.*select\.not_before.*qsRight",
    ):
        compile_glyph_families(families, "senior")


def _scoped_selector_suggester_fixture(
    selector,
    *,
    target_family: str = "qsLeft",
    source_family: str = "qsRight",
    bitmap: list[str] | None = None,
    include_font_metadata: bool = False,
) -> GlyphData:
    bitmap = bitmap or ["#"]
    glyph_families: dict[str, Any] = {
        target_family: {
            "prop": {
                "bitmap": bitmap,
                "anchors": {"exit": [1, 5]},
            },
            "stances": {
                "entry_xheight": {
                    "shape": "prop",
                    "anchors": {"entry": [0, 5]},
                    "modifiers": ["en-y5"],
                },
                "exit_baseline": {
                    "shape": "prop",
                    "anchors": {"exit": [1, 0]},
                    "modifiers": ["ex-y0"],
                },
            },
        },
        source_family: {
            "prop": {"bitmap": bitmap},
            "stances": {
                "entry_xheight": {
                    "shape": "prop",
                    "anchors": {"entry": [0, 5]},
                    "select": {"after": [selector]},
                    "modifiers": ["en-y5"],
                },
            },
        },
    }
    if include_font_metadata:
        glyph_families["space"] = {"mono": {"bitmap": [], "advance_width": 7}}
    if include_font_metadata:
        metadata = _real_glyph_data()["metadata"]
    else:
        metadata = {}

    return {
        "metadata": metadata,
        "glyphs": {},
        "context_sets": {
            "lefts": [{"family": target_family}],
        },
        "kerning": {},
        "glyph_families": glyph_families,
    }


def test_scoped_anchor_suggester_reports_overbroad_family_selector():
    suggestions = suggest_scoped_anchor_selectors(_scoped_selector_suggester_fixture({"family": "qsLeft"}))

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.path == "glyph_families.qsRight.stances.entry_xheight.select.after[0]"
    assert suggestion.current == "{family: qsLeft}"
    assert suggestion.suggested == "{family: qsLeft, exit_y: 5}"
    assert suggestion.compatible == ("qsLeft",)
    assert "qsLeft.en-y5" in suggestion.incompatible
    assert suggestion.family_name == "qsRight"
    assert suggestion.record_name == "entry_xheight"
    assert suggestion.record_kind == "stances"
    assert suggestion.field_name == "after"
    assert suggestion.selector_index == 0
    assert suggestion.selected_name == "qsRight.en-y5"
    assert suggestion.target_family == "qsLeft"
    assert suggestion.anchor_key == "exit_y"
    assert suggestion.required_y == 5


def test_scoped_anchor_reviewer_applies_suggestion_to_copy_only():
    data = _scoped_selector_suggester_fixture({"family": "qsLeft"})
    suggestion = suggest_scoped_anchor_selectors(data)[0]

    patched = apply_suggestions_to_glyph_data(data, [suggestion])

    original_selector = data["glyph_families"]["qsRight"]["stances"]["entry_xheight"]["select"]["after"][0]
    patched_selector = patched["glyph_families"]["qsRight"]["stances"]["entry_xheight"]["select"]["after"][0]
    assert original_selector == {"family": "qsLeft"}
    assert patched_selector == {"family": "qsLeft", "exit_y": 5}

    join_glyphs, _ = compile_quikscript_ir(patched, "senior")
    assert join_glyphs["qsRight.en-y5"].after == ("qsLeft",)


def _scoped_selector_review_fixture(selector) -> GlyphData:
    return _scoped_selector_suggester_fixture(
        selector,
        target_family="qsMay",
        source_family="qsPea",
        bitmap=["#", "#", "#", "#", "#", "#"],
        include_font_metadata=True,
    )


def _variant_example_finder(
    data: GlyphData,
    tmp_path: Path,
) -> tuple[VariantExampleFinder, dict[str, JoinGlyph]]:
    font_path = tmp_path / "review-font.otf"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _build_review_font(data, font_path)
    ps_names = _load_ps_names()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        meta = compile_glyph_set(data, "senior").glyph_meta
    return (
        VariantExampleFinder(
            glyph_data=data,
            ps_names=ps_names,
            context_sequences=_review_context_sequences(ps_names, data),
            current_font=_hb_font(font_path),
            current_meta=meta,
            max_len=2,
        ),
        meta,
    )


def test_variant_example_finder_prefers_exact_suggestion_context(tmp_path):
    data = _scoped_selector_review_fixture({"family": "qsMay"})
    suggestion = suggest_scoped_anchor_selectors(data)[0]
    finder, _ = _variant_example_finder(data, tmp_path)

    example = finder.find(suggestion, "qsMay")

    assert example.status == "exact"
    assert example.label == "Reviewed context"
    assert "reviewed selector context" in example.title
    assert example.families == ("qsMay", "qsPea")
    assert example.glyphs == ("qsMay", "qsPea.en-y5")


def test_variant_example_finder_falls_back_to_variant_only_context(tmp_path):
    data = _scoped_selector_review_fixture({"family": "qsMay"})
    suggestion = suggest_scoped_anchor_selectors(data)[0]
    finder, _ = _variant_example_finder(data, tmp_path)

    example = finder.find(suggestion, "qsPea.en-y5")

    assert example.status == "variant"
    assert example.label == "Glyph-only example\n(different position)"
    assert "not next to this qsMay glyph in the reviewed position" in example.title
    assert example.families == ("qsMay", "qsPea")
    assert example.glyphs == ("qsMay", "qsPea.en-y5")


def test_variant_example_finder_explains_elsewhere_example_without_source_family():
    data = _scoped_selector_review_fixture({"family": "qsMay"})
    suggestion = suggest_scoped_anchor_selectors(data)[0]
    finder = object.__new__(VariantExampleFinder)
    finder.glyph_data = data
    finder.current_meta = {}
    example = VariantExample(
        status="variant",
        label="Glyph-only example",
        families=("qsMay", "qsThey", "qsUtter"),
        glyphs=("qsMay.en-y0.ex-y5.ex-noentry", "qsThey_qsUtter.noentry"),
    )

    annotated = finder._with_variant_only_context(
        suggestion,
        "qsMay.en-y0.ex-y5.ex-noentry",
        example,
    )

    assert annotated.label == "Glyph-only example\n(no ·Pea input)"
    assert "does not include ·Pea" in annotated.title


def test_variant_example_finder_explains_elsewhere_example_with_other_source_stance():
    data = _scoped_selector_review_fixture({"family": "qsMay"})
    suggestion = suggest_scoped_anchor_selectors(data)[0]
    finder = object.__new__(VariantExampleFinder)
    finder.glyph_data = data
    finder.current_meta = {}
    example = VariantExample(
        status="variant",
        label="Glyph-only example",
        families=("qsPea", "qsMay", "qsThey", "qsUtter"),
        glyphs=(
            "qsPea",
            "qsMay.en-y0.ex-y5.ex-noentry.en-ext-1",
            "qsThey_qsUtter.noentry",
        ),
    )

    annotated = finder._with_variant_only_context(
        suggestion,
        "qsMay.en-y0.ex-y5.ex-noentry.en-ext-1",
        example,
    )

    assert annotated.label == "Glyph-only example\n(different ·Pea stance)"
    assert "not qsPea.en-y5" in annotated.title


def test_variant_rows_are_not_truncated_and_mark_internal_only_examples():
    names = tuple(f"qsMay.synthetic-{index}" for index in range(24))
    examples = {
        name: VariantExample(
            status="internal",
            label="No typed example found",
        )
        for name in names
    }
    suggestion = ScopedAnchorSuggestion(
        path="glyph_families.qsPea.stances.entry_xheight.select.after[0]",
        current="{family: qsMay}",
        suggested="{family: qsMay, exit_y: 5}",
        incompatible=(),
        family_name="qsPea",
        target_family="qsMay",
    )

    rows = _rows_for_variants(names, {}, examples, suggestion)

    assert _glyph_name_html("qsMay.synthetic-0") in rows
    assert _glyph_name_html("qsMay.synthetic-23") in rows
    assert "and 6 more" not in rows
    assert "No typed example" in rows


def test_variant_rows_render_label_line_breaks():
    suggestion = ScopedAnchorSuggestion(
        path="glyph_families.qsPea.stances.entry_xheight.select.after[0]",
        current="{family: qsMay}",
        suggested="{family: qsMay, exit_y: 5}",
        incompatible=(),
        family_name="qsPea",
        target_family="qsMay",
    )
    examples = {
        "qsMay": VariantExample(
            status="variant",
            label="Glyph-only example\n(no ·Pea input)",
            title="This is the longer hover explanation.",
        )
    }

    rows = _rows_for_variants(("qsMay",), {}, examples, suggestion)

    assert "Glyph-only example<br>(no ·Pea input)" in rows
    assert 'title="This is the longer hover explanation."' in rows


def test_scoped_anchor_suggester_skips_already_scoped_selector():
    suggestions = suggest_scoped_anchor_selectors(
        _scoped_selector_suggester_fixture({"family": "qsLeft", "exit_y": 5})
    )

    assert suggestions == []


def test_scoped_anchor_suggester_skips_context_set_refs():
    suggestions = suggest_scoped_anchor_selectors(
        _scoped_selector_suggester_fixture({"context_set": "lefts"})
    )

    assert suggestions == []


def test_scoped_anchor_suggester_skips_all_compatible_family_selector():
    data: GlyphData = {
        "metadata": {},
        "glyphs": {},
        "context_sets": {},
        "kerning": {},
        "glyph_families": {
            "qsLeft": {
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"exit": [1, 5]},
                },
            },
            "qsRight": {
                "prop": {"bitmap": ["#"]},
                "stances": {
                    "entry_xheight": {
                        "shape": "prop",
                        "anchors": {"entry": [0, 5]},
                        "select": {"after": [{"family": "qsLeft"}]},
                        "modifiers": ["en-y5"],
                    },
                },
            },
        },
    }

    assert suggest_scoped_anchor_selectors(data) == []


def _ligature_inheritance_glyph_data(
    *,
    lead_family: dict,
    ligature_prop: dict,
) -> GlyphData:
    return {
        "metadata": {},
        "glyphs": {},
        "glyph_families": {
            "qsLead": lead_family,
            "qsFollow": {
                "prop": {
                    "bitmap": ["#"],
                    "anchors": {"entry": [0, 0]},
                },
            },
            "qsLead_qsFollow": {
                "sequence": ["qsLead", "qsFollow"],
                "prop": ligature_prop,
            },
        },
        "context_sets": {},
        "kerning": {},
    }


def test_inherit_ligature_entry_from_lead_prop():
    glyphs, _ = compile_quikscript_ir(
        _ligature_inheritance_glyph_data(
            lead_family={
                "prop": {
                    "bitmap": ["##"],
                    "anchors": {"entry": [1, 0], "exit": [3, 0]},
                },
            },
            ligature_prop={
                "bitmap": ["####"],
                "anchors": {"exit": [5, 0]},
            },
        ),
        "senior",
    )
    assert glyphs["qsLead_qsFollow"].entry == ((1, 0),)


def test_inherit_ligature_entry_from_entry_xheight_stance():
    glyphs, _ = compile_quikscript_ir(
        _ligature_inheritance_glyph_data(
            lead_family={
                "prop": {
                    "bitmap": ["##    ", "##    ", "##    ", "##    ", "##    ", "##    "],
                    "anchors": {"exit": [3, 0]},
                },
                "stances": {
                    "entry_xheight": {
                        "shape": "prop",
                        "anchors": {"entry": [1, 5], "exit": [3, 0]},
                        "modifiers": ["en-y5"],
                    },
                },
            },
            ligature_prop={
                "bitmap": ["##    ##", "##    ##", "##    ##", "##    ##", "##    ##", "##    ##"],
                "anchors": {"exit": [9, 5]},
            },
        ),
        "senior",
    )
    assert glyphs["qsLead_qsFollow"].entry == ((1, 5),)


def test_no_inheritance_when_entry_xheight_is_after_restricted():
    glyphs, _ = compile_quikscript_ir(
        _ligature_inheritance_glyph_data(
            lead_family={
                "prop": {
                    "bitmap": ["##    ", "##    ", "##    ", "##    ", "##    ", "##    "],
                    "anchors": {"exit": [3, 0]},
                },
                "stances": {
                    "entry_xheight": {
                        "shape": "prop",
                        "anchors": {"entry": [1, 5], "exit": [3, 0]},
                        "select": {"after": [{"family": "qsFollow"}]},
                        "modifiers": ["en-y5"],
                    },
                },
            },
            ligature_prop={
                "bitmap": ["##    ##", "##    ##", "##    ##", "##    ##", "##    ##", "##    ##"],
                "anchors": {"exit": [9, 5]},
            },
        ),
        "senior",
    )
    assert glyphs["qsLead_qsFollow"].entry == ()


def test_no_inheritance_when_lead_has_no_entry_stance():
    glyphs, _ = compile_quikscript_ir(
        _ligature_inheritance_glyph_data(
            lead_family={
                "prop": {
                    "bitmap": ["##"],
                    "anchors": {"exit": [3, 0]},
                },
            },
            ligature_prop={
                "bitmap": ["####"],
                "anchors": {"exit": [5, 0]},
            },
        ),
        "senior",
    )
    assert glyphs["qsLead_qsFollow"].entry == ()


def test_explicit_entry_matching_inheritance_warns():
    import pytest

    with pytest.warns(LigatureEntryInheritanceWarning, match="Consider removing"):
        compile_quikscript_ir(
            _ligature_inheritance_glyph_data(
                lead_family={
                    "prop": {
                        "bitmap": ["##"],
                        "anchors": {"entry": [1, 0], "exit": [3, 0]},
                    },
                },
                ligature_prop={
                    "bitmap": ["####"],
                    "anchors": {"entry": [1, 0], "exit": [5, 0]},
                },
            ),
            "senior",
        )


def test_explicit_entry_differing_from_inheritance_warns_sharply():
    import pytest

    with pytest.warns(LigatureEntryInheritanceWarning, match="differs!"):
        compile_quikscript_ir(
            _ligature_inheritance_glyph_data(
                lead_family={
                    "prop": {
                        "bitmap": ["##"],
                        "anchors": {"entry": [1, 0], "exit": [3, 0]},
                    },
                },
                ligature_prop={
                    "bitmap": ["####"],
                    "anchors": {"entry": [2, 0], "exit": [5, 0]},
                },
            ),
            "senior",
        )


def test_bitmap_misalignment_blocks_inheritance():
    glyphs, _ = compile_quikscript_ir(
        _ligature_inheritance_glyph_data(
            lead_family={
                "prop": {
                    # Lead has ink starting at column 0 at y=0.
                    "bitmap": ["##"],
                    "anchors": {"entry": [1, 0], "exit": [3, 0]},
                },
            },
            ligature_prop={
                # Ligature's bitmap leftmost ink at y=0 is column 1, not 0, so copying entry x=1 onto it would create a join gap.
                "bitmap": [" #####"],
                "anchors": {"exit": [7, 0]},
            },
        ),
        "senior",
    )
    assert glyphs["qsLead_qsFollow"].entry == ()


def test_inheritance_skipped_in_junior_variant():
    # Junior has no contextual en-y5 stances compiled, and ligatures are not formed there anyway, so the pass shouldn't run and shouldn't warn about ligatures whose explicit YAML entry can't be reconciled.
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", LigatureEntryInheritanceWarning)
        compile_quikscript_ir(
            _ligature_inheritance_glyph_data(
                lead_family={
                    "prop": {
                        "bitmap": ["##"],
                        "anchors": {"entry": [1, 0], "exit": [3, 0]},
                    },
                },
                ligature_prop={
                    "bitmap": ["####"],
                    "anchors": {"entry": [1, 0], "exit": [5, 0]},
                },
            ),
            "junior",
        )


def test_select_rule_neighbors_is_identity_without_a_recorder():
    # The join contract (doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md) routes every calt selection point through this chokepoint. It enforces (drops non-joining, non-cosmetic neighbors) only while an `_active_contract_recorder` is installed for an emit run; called directly with no recorder, it is a pure identity passthrough. The point of lifting it to a module-level function is exactly this: the selection decision is testable in isolation, without standing up the 4,000-line emitter.
    followers = {"qsTea_qsOy", "qsThaw.ex-y0", "qsDay.half"}
    kept = _select_rule_neighbors("qsGay", "qsGay.en-y5", followers, direction="fwd")
    assert kept == followers

    preds = {"qsRoe.ex-y0", "qsMay.en-y5"}
    assert _select_rule_neighbors("qsSee", "qsSee.ex-y0", preds, direction="bk") == preds

    # Empty candidate set passes through cleanly (a rule whose context is already empty).
    assert _select_rule_neighbors("qsAt", "qsAt.ex-y0.before-may", set(), direction="fwd") == set()


def test_select_rule_neighbors_returns_a_distinct_set():
    # Callers compare `kept == candidate_members` to decide whether to emit the bare class token (`@entry_y…` / `@exit_y…`) or fall back to an explicit member list, and the result must not alias the caller's live set. Return a fresh set so a future drop pass can't mutate the input in place.
    candidate = {"qsTea", "qsDay"}
    kept = _select_rule_neighbors("qsIt", "qsIt.en-y0", candidate, direction="fwd")
    assert kept == candidate
    assert kept is not candidate
