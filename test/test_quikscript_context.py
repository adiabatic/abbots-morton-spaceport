import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from typing import Any

from build_font import load_glyph_data
from glyph_compiler import CompiledGlyphSet, compile_glyph_set
from quikscript_ir import JoinGlyph, generate_noentry_variants
from quikscript_join_analysis import JoinContractWarning, OrphanAnchorWarning


def _compiled_set(data: Any = None) -> CompiledGlyphSet:
    if data is None:
        return compile_glyph_set(load_glyph_data(ROOT / "glyph_data"), "senior")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OrphanAnchorWarning)
        warnings.simplefilter("ignore", JoinContractWarning)
        return compile_glyph_set(data, "senior")

def _compiled_meta() -> dict[str, JoinGlyph]:
    return _compiled_set().glyph_meta


def test_quikscript_family_and_generated_variants_keep_logical_metadata():
    meta = _compiled_meta()

    tea = meta["qsTea"]
    assert tea.base_name == "qsTea"
    assert tea.modifiers == ()
    assert not tea.is_contextual

    utter = meta["qsUtter.alt.reaches-way-back"]
    assert utter.modifiers == ("alt", "reaches-way-back")
    assert utter.is_contextual
    assert "reaches-way-back" in utter.compat_assertions

    may = meta["qsMay.entry-baseline.entry-extended"]
    assert may.modifiers == ("entry-baseline", "entry-extended")
    assert may.entry_suffix == ".entry-baseline"
    assert may.extended_entry_suffix == ".entry-extended"
    assert may.is_entry_variant
    assert {"entry", "extended", "entry-extended"} <= may.compat_assertions

    ing = meta["qsIng.exit-triply-extended"]
    assert {
        "exit",
        "extended",
        "triply-extended",
        "exit-triply-extended",
    } <= ing.compat_assertions

    ing_entry = meta["qsIng.entry-extended"]
    assert ing_entry.modifiers == ("entry-extended",)
    assert ing_entry.extended_entry_suffix == ".entry-extended"
    assert ing_entry.is_entry_variant
    assert {"entry", "extended", "entry-extended"} <= ing_entry.compat_assertions
    assert ing_entry.after == ("qsTea", "qsTea_qsOy")

    day_lig = meta["qsDay_qsUtter.entry-extended"]
    assert day_lig.base_name == "qsDay_qsUtter"
    assert day_lig.sequence == ("qsDay", "qsUtter")

    noentry = meta["qsDay_qsUtter.noentry"]
    assert noentry.is_noentry
    assert noentry.is_contextual


def test_family_form_modifiers_are_preserved_from_authored_form_data():
    meta = _compiled_set(
        {
            "glyph_families": {
                "qsTest": {
                    "prop": {
                        "bitmap": ["#"],
                    },
                    "forms": {
                        "local_form_label": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [2, 0],
                            },
                            "traits": ["alt"],
                            "modifiers": ["reaches-way-back"],
                        },
                    },
                },
            },
        }
    ).glyph_meta

    form = meta["qsTest.alt.reaches-way-back"]
    assert form.modifiers == ("alt", "reaches-way-back")
    assert {"alt", "reaches-way-back"} <= form.compat_assertions


def test_form_keys_are_local_labels():
    left = _compiled_set(
        {
            "glyph_families": {
                "qsTest": {
                    "prop": {
                        "bitmap": ["#"],
                    },
                    "forms": {
                        "first_label": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [2, 0],
                            },
                            "traits": ["alt"],
                            "modifiers": ["reaches-way-back"],
                        },
                    },
                },
            },
        }
    ).glyph_meta
    right = _compiled_set(
        {
            "glyph_families": {
                "qsTest": {
                    "prop": {
                        "bitmap": ["#"],
                    },
                    "forms": {
                        "renamed_local_label": {
                            "shape": "prop",
                            "anchors": {
                                "exit": [2, 0],
                            },
                            "traits": ["alt"],
                            "modifiers": ["reaches-way-back"],
                        },
                    },
                },
            },
        }
    ).glyph_meta

    assert left.keys() == right.keys()
    assert left["qsTest.alt.reaches-way-back"].modifiers == right["qsTest.alt.reaches-way-back"].modifiers


def test_noentry_generation_uses_authored_modifiers_not_compiled_name():
    glyph_meta = _compiled_set(
        {
            "glyphs": {
                "uni200C": {
                    "bitmap": [],
                    "advance_width": 0,
                },
            },
            "glyph_families": {
                "qsBase": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                        },
                    },
                    "forms": {
                        "local_alt_form": {
                            "shape": "prop",
                            "anchors": {
                                "entry": [0, 0],
                            },
                            "traits": ["alt"],
                        },
                    },
                },
            },
        }
    ).glyph_meta

    variants = generate_noentry_variants(glyph_meta, has_zwnj=True)

    assert "qsBase.noentry" in variants
    assert "qsBase.alt.noentry" not in variants


def test_noentry_generation_keeps_exit_bearing_variants_usable_after_zwnj():
    glyph_meta = _compiled_set(
        {
            "glyphs": {
                "uni200C": {
                    "bitmap": [],
                    "advance_width": 0,
                },
            },
            "glyph_families": {
                "qsBase": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {
                            "entry": [0, 0],
                        },
                    },
                    "forms": {
                        "exit_form": {
                            "shape": "prop",
                            "anchors": {
                                "entry": [0, 0],
                                "exit": [1, 0],
                            },
                            "modifiers": ["exit-baseline"],
                        },
                    },
                },
            },
        }
    ).glyph_meta

    variants = generate_noentry_variants(glyph_meta, has_zwnj=True)

    assert "qsBase.exit-baseline.noentry" in variants
    assert variants["qsBase.exit-baseline.noentry"].entry == ()
    assert variants["qsBase.exit-baseline.noentry"].exit == ((1, 0),)


def test_structured_family_selectors_resolve_to_compiled_names():
    glyphs = _compiled_set(
        {
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
                            "entry": [0, 0],
                            "exit": [1, 0],
                        },
                        "derive": {
                            "extend_exit_before": {
                                "by": 1,
                                "targets": [{"family": "qsRight"}],
                            },
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
                    "forms": {
                        "baseline_entry_after_extended_left": {
                            "shape": "prop",
                            "anchors": {
                                "entry": [0, 0],
                            },
                            "modifiers": ["entry-baseline"],
                            "select": {
                                "after": [
                                    {"family": "qsLeft", "modifiers": ["exit-extended"]},
                                ],
                            },
                        },
                    },
                },
            },
        }
    ).glyph_definitions

    assert glyphs["qsRight.entry-baseline"]["calt_after"] == ["qsLeft.exit-extended"]


def test_qs_he_half_contracted_pairs_with_trimmed_qs_zoo():
    compiled = _compiled_set()
    meta = compiled.glyph_meta
    glyphs = compiled.glyph_definitions

    contracted = meta["qsHe.half.exit-contracted"]
    plain_half = meta["qsHe.half"]
    assert contracted.bitmap == plain_half.bitmap
    assert contracted.exit == ((0, 5),)
    assert plain_half.exit == ((1, 5),)
    assert contracted.before == ("qsZoo",)
    assert "half" in contracted.traits
    assert glyphs["qsHe.half.exit-contracted"]["calt_before"] == ["qsZoo"]

    extended_zoo = meta["qsZoo.entry-extended"]
    trimmed = meta["qsZoo.entry-extended.entry-trimmed-by-1"]
    assert extended_zoo.bitmap[0] == "####  "
    assert trimmed.bitmap[0] == " ###  "
    assert trimmed.bitmap[1:] == extended_zoo.bitmap[1:]
    assert trimmed.entry == extended_zoo.entry
    assert trimmed.exit == extended_zoo.exit
    assert trimmed.y_offset == extended_zoo.y_offset
    assert trimmed.after == (
        "qsHe.half.exit-contracted",
        "qsTea.half.exit-xheight.exit-contracted",
    )
    assert trimmed.transform_kind == "entry-trimmed"
    assert {"entry", "trimmed", "entry-trimmed", "entry-trimmed-by-1"} <= trimmed.compat_assertions
    assert glyphs["qsZoo.entry-extended.entry-trimmed-by-1"]["calt_after"] == [
        "qsHe.half.exit-contracted",
        "qsTea.half.exit-xheight.exit-contracted",
    ]


def test_context_sets_expand_and_compose_inside_select_and_derive():
    glyphs = _compiled_set(
        {
            "context_sets": {
                "extended_leads": [
                    {"family": "qsLead", "modifiers": ["exit-extended"]},
                ],
                "after_sources": [
                    {"family": "qsPrimary"},
                    {"context_set": "extended_leads"},
                ],
                "more_extenders": [
                    {"family": "qsExtTwo"},
                ],
                "all_extenders": [
                    {"family": "qsExtOne"},
                    {"context_set": "more_extenders"},
                ],
            },
            "glyph_families": {
                "qsPrimary": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"exit": [1, 0]},
                    },
                },
                "qsLead": {
                    "prop": {"bitmap": ["#"]},
                    "forms": {
                        "extended_exit": {
                            "shape": "prop",
                            "anchors": {"exit": [1, 0]},
                            "modifiers": ["exit-extended"],
                        },
                    },
                },
                "qsExtOne": {
                    "prop": {"bitmap": ["#"]},
                },
                "qsExtTwo": {
                    "prop": {"bitmap": ["#"]},
                },
                "qsTarget": {
                    "prop": {
                        "bitmap": ["#"],
                        "derive": {
                            "extend_entry_after": {
                                "by": 1,
                                "targets": [{"context_set": "all_extenders"}],
                            },
                        },
                    },
                    "forms": {
                        "baseline_entry": {
                            "shape": "prop",
                            "anchors": {"entry": [0, 0]},
                            "modifiers": ["entry-baseline"],
                            "select": {
                                "after": [{"context_set": "after_sources"}],
                            },
                        },
                    },
                },
            },
        }
    ).glyph_definitions

    assert glyphs["qsTarget"]["extend_entry_after"] == {
        "by": 1,
        "targets": ["qsExtOne", "qsExtTwo"],
    }
    assert glyphs["qsTarget.entry-baseline"]["calt_after"] == [
        "qsPrimary",
        "qsLead.exit-extended",
    ]


def test_inherits_reuses_and_clears_nested_form_context():
    glyphs = _compiled_set(
        {
            "glyph_families": {
                "qsBlocker": {
                    "prop": {"bitmap": ["#"]},
                },
                "qsOther": {
                    "prop": {
                        "bitmap": ["#"],
                        "anchors": {"entry": [0, 0]},
                    },
                },
                "qsBase": {
                    "prop": {
                        "bitmap": ["#"],
                    },
                    "forms": {
                        "half": {
                            "shape": "prop",
                            "anchors": {"exit": [1, 0]},
                            "select": {
                                "not_before": [{"family": "qsBlocker"}],
                            },
                            "traits": ["half"],
                        },
                        "half_entry": {
                            "inherits": "half",
                            "anchors": {"entry": [0, 0]},
                            "modifiers": ["entry-baseline"],
                        },
                        "half_entry_exit": {
                            "inherits": "half",
                            "anchors": {"entry": [0, 0], "exit": [1, 0]},
                            "select": {
                                "not_before": None,
                                "before": [{"family": "qsOther"}],
                            },
                            "modifiers": ["entry-baseline", "exit-baseline"],
                        },
                    },
                },
            },
        }
    ).glyph_definitions

    assert glyphs["qsBase.half.entry-baseline"]["calt_not_before"] == ["qsBlocker"]
    assert "calt_not_before" not in glyphs["qsBase.half.entry-baseline.exit-baseline"]
    assert glyphs["qsBase.half.entry-baseline.exit-baseline"]["calt_before"] == ["qsOther"]


def test_inherited_form_shape_override_replaces_visual_fields():
    compiled = _compiled_set(
        {
            "glyph_families": {
                "qsBase": {
                    "prop": {"bitmap": ["#"]},
                    "shapes": {
                        "first": {
                            "bitmap": ["11"],
                            "y_offset": -2,
                            "advance_width": 4,
                        },
                        "second": {
                            "bitmap": ["22", " 2"],
                            "y_offset": 3,
                            "advance_width": 7,
                        },
                    },
                    "forms": {
                        "half": {
                            "shape": "first",
                            "anchors": {"exit": [1, 0]},
                            "traits": ["half"],
                        },
                        "half_entry": {
                            "inherits": "half",
                            "shape": "second",
                            "anchors": {"entry": [0, 0]},
                            "modifiers": ["entry-baseline"],
                        },
                    },
                },
            },
        }
    )
    glyphs = compiled.glyph_definitions
    meta = compiled.glyph_meta

    assert glyphs["qsBase.half"]["bitmap"] == ["11"]
    assert glyphs["qsBase.half"]["y_offset"] == -2
    assert glyphs["qsBase.half"]["advance_width"] == 4

    assert glyphs["qsBase.half.entry-baseline"]["bitmap"] == ["22", " 2"]
    assert glyphs["qsBase.half.entry-baseline"]["y_offset"] == 3
    assert glyphs["qsBase.half.entry-baseline"]["advance_width"] == 7
    assert meta["qsBase.half.entry-baseline"].modifiers == ("half", "entry-baseline")


def test_alt_and_half_are_semantic_traits():
    meta = _compiled_meta()

    assert "alt" in meta["qsNo.alt"].traits
    assert "half" in meta["qsPea.half"].traits
    assert "alt" in meta["qsUtter.alt.reaches-way-back"].traits
    assert "reaches-way-back" in meta["qsUtter.alt.reaches-way-back"].compat_assertions


def test_generated_entry_and_ligature_metadata_keep_logical_identity():
    meta = _compiled_meta()

    ligature = meta["qsDay_qsUtter.noentry"]
    assert ligature.base_name == "qsDay_qsUtter"
    assert ligature.sequence == ("qsDay", "qsUtter")
    assert ligature.is_noentry


def test_height_suffixes_are_available_as_compat_assertions():
    meta = _compiled_meta()

    roe = meta["qsRoe.exit-baseline"]
    assert {"exit", "exit-baseline", "baseline"} <= roe.compat_assertions


def test_reverse_upgrade_metadata_is_preserved():
    meta = _compiled_meta()

    pea = meta["qsPea.half.entry-xheight.exit-xheight"]
    assert pea.base_name == "qsPea"
    assert pea.reverse_upgrade_from
    assert pea.entry_suffix == ".entry-xheight"
    assert pea.exit_suffix == ".exit-xheight"

    roe = meta["qsRoe.exit-baseline"]
    assert "baseline" in roe.compat_assertions
