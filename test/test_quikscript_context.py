from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_font import (
    compile_glyph_definitions,
    load_glyph_data,
)
from quikscript_ir import build_join_glyphs, generate_noentry_variants


def _compiled_glyphs():
    data = load_glyph_data(ROOT / "glyph_data")
    return compile_glyph_definitions(data, "senior")


def _compiled_meta():
    return build_join_glyphs(_compiled_glyphs())


def test_quikscript_family_and_generated_variants_stamp_metadata_seed():
    glyphs = _compiled_glyphs()

    tea = glyphs["qsTea"]
    assert tea["_base_name"] == "qsTea"
    assert tea["_modifiers"] == []
    assert not tea["_contextual"]

    utter = glyphs["qsUtter.alt.reaches-way-back"]
    assert utter["_modifiers"] == ["alt", "reaches-way-back"]
    assert utter["_contextual"]
    assert "reaches-way-back" in utter["_compat_assertions"]

    may = glyphs["qsMay.entry-baseline.entry-extended"]
    assert may["_modifiers"] == ["entry-baseline", "entry-extended"]
    assert may["_entry_suffix"] == ".entry-baseline"
    assert may["_extended_entry_suffix"] == ".entry-extended"
    assert may["_is_entry_variant"]

    day_lig = glyphs["qsDay_qsUtter.entry-extended"]
    assert day_lig["_base_name"] == "qsDay_qsUtter"
    assert day_lig["_sequence"] == ["qsDay", "qsUtter"]

    noentry = glyphs["qsDay_qsUtter.noentry"]
    assert noentry["_is_noentry"]
    assert noentry["_contextual"]


def test_family_form_modifiers_are_seeded_from_authored_form_data():
    glyphs = compile_glyph_definitions(
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
        },
        "senior",
    )

    form = glyphs["qsTest.alt.reaches-way-back"]
    assert form["_modifiers"] == ["alt", "reaches-way-back"]
    assert {"alt", "reaches-way-back"} <= set(form["_compat_assertions"])


def test_form_keys_are_local_labels():
    left = compile_glyph_definitions(
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
        },
        "senior",
    )
    right = compile_glyph_definitions(
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
        },
        "senior",
    )

    assert left.keys() == right.keys()
    assert left["qsTest.alt.reaches-way-back"]["_modifiers"] == right["qsTest.alt.reaches-way-back"]["_modifiers"]


def test_noentry_generation_uses_seeded_modifiers_not_compiled_name():
    glyphs = compile_glyph_definitions(
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
        },
        "senior",
    )

    variants = generate_noentry_variants(build_join_glyphs(glyphs), has_zwnj=True)

    assert "qsBase.noentry" in variants
    assert "qsBase.alt.noentry" not in variants


def test_structured_family_selectors_resolve_to_compiled_names():
    glyphs = compile_glyph_definitions(
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
                            "extend_exit_before": [
                                {"family": "qsRight"},
                            ],
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
        },
        "senior",
    )

    assert glyphs["qsRight.entry-baseline"]["calt_after"] == ["qsLeft.exit-extended"]


def test_context_sets_expand_and_compose_inside_select_and_derive():
    glyphs = compile_glyph_definitions(
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
                    "prop": {"bitmap": ["#"]},
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
                            "extend_entry_after": [{"context_set": "all_extenders"}],
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
        },
        "senior",
    )

    assert glyphs["qsTarget"]["extend_entry_after"] == ["qsExtOne", "qsExtTwo"]
    assert glyphs["qsTarget.entry-baseline"]["calt_after"] == [
        "qsPrimary",
        "qsLead.exit-extended",
    ]


def test_inherits_reuses_and_clears_nested_form_context():
    glyphs = compile_glyph_definitions(
        {
            "glyph_families": {
                "qsBlocker": {
                    "prop": {"bitmap": ["#"]},
                },
                "qsOther": {
                    "prop": {"bitmap": ["#"]},
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
        },
        "senior",
    )

    assert glyphs["qsBase.half.entry-baseline"]["calt_not_before"] == ["qsBlocker"]
    assert "calt_not_before" not in glyphs["qsBase.half.entry-baseline.exit-baseline"]
    assert glyphs["qsBase.half.entry-baseline.exit-baseline"]["calt_before"] == ["qsOther"]


def test_alt_and_half_are_semantic_traits():
    meta = _compiled_meta()

    assert "alt" in meta["qsNo.alt"].traits
    assert "half" in meta["qsTea.half"].traits
    assert "alt" in meta["qsUtter.alt.reaches-way-back"].traits
    assert "reaches-way-back" in meta["qsUtter.alt.reaches-way-back"].compat_assertions


def test_generated_entry_and_ligature_metadata_keep_logical_identity():
    meta = _compiled_meta()

    ooze = meta["qsOoze.entry-extended-at-baseline"]
    assert ooze.base_name == "qsOoze"
    assert ooze.entry_suffix == ".entry-extended-at-baseline"
    assert ooze.entry_restriction_y == 0
    assert {"entry", "entry-extended", "extended"} <= ooze.compat_assertions

    ligature = meta["qsDay_qsUtter.noentry"]
    assert ligature.base_name == "qsDay_qsUtter"
    assert ligature.sequence == ("qsDay", "qsUtter")
    assert ligature.is_noentry


def test_height_suffixes_are_available_as_compat_assertions():
    meta = _compiled_meta()

    roe = meta["qsRoe.exit-y1"]
    assert {"exit", "exit-y1", "y1"} <= roe.compat_assertions


def test_reverse_upgrade_metadata_is_preserved():
    meta = _compiled_meta()

    pea = meta["qsPea.half.entry-xheight.exit-xheight"]
    assert pea.base_name == "qsPea"
    assert pea.reverse_upgrade_from
    assert pea.entry_suffix == ".entry-xheight"
    assert pea.exit_suffix == ".exit-xheight"

    roe = meta["qsRoe.exit-y1"]
    assert "y1" in roe.compat_assertions
