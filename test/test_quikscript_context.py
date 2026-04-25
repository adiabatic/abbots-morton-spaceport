import sys
from functools import lru_cache
from pathlib import Path

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from typing import Any

from build_font import load_glyph_data
from glyph_compiler import CompiledGlyphSet, compile_glyph_set
from quikscript_ir import JoinGlyph, generate_noentry_variants

FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"


def _compiled_set(data: Any = None) -> CompiledGlyphSet:
    if data is None:
        data = load_glyph_data(ROOT / "glyph_data")
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
    assert ing_entry.after == ("qsTea",)

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
    assert "half" in meta["qsTea.half"].traits
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


# --- Shaping invariants ------------------------------------------------------
#
# The following section verifies pair-and-context shape selection invariants
# by actually shaping the font with HarfBuzz, then inspecting the compiled
# glyph metadata. The helpers below mirror the ones in test_calt_regressions.py
# (the file that owns the never-join family of pair invariants); the duplication
# is intentional so this file remains self-contained for its variant-selection
# focus.


@lru_cache(maxsize=1)
def _font() -> hb.Font:
    blob = hb.Blob.from_file_path(str(FONT_PATH))
    face = hb.Face(blob)
    return hb.Font(face)


@lru_cache(maxsize=None)
def _shape(text: str) -> list[str]:
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


@lru_cache(maxsize=None)
def _shape_with_clusters(text: str) -> tuple[tuple[str, int], ...]:
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return tuple(
        (font.glyph_to_string(info.codepoint), info.cluster)
        for info in buf.glyph_infos
    )


@lru_cache(maxsize=1)
def _char_map() -> dict[str, str]:
    with PS_NAMES_PATH.open() as f:
        ps_names = yaml.safe_load(f)
    return {name: chr(codepoint) for name, codepoint in ps_names.items()}


@lru_cache(maxsize=1)
def _plain_quikscript_letters() -> tuple[tuple[str, str], ...]:
    chars = _char_map()
    names = [
        name for name in sorted(chars)
        if name.startswith("qs")
        and "_" not in name
        and "." not in name
        and name not in {"qsAngleParenLeft", "qsAngleParenRight"}
    ]
    return tuple((name, chars[name]) for name in names)


def _entry_ys(glyph_name: str) -> set[int]:
    meta = _compiled_meta().get(glyph_name)
    if meta is None:
        return set()
    return {anchor[1] for anchor in meta.entry} | {anchor[1] for anchor in meta.entry_curs_only}


def _exit_ys(glyph_name: str) -> set[int]:
    meta = _compiled_meta().get(glyph_name)
    if meta is None:
        return set()
    return {anchor[1] for anchor in meta.exit}


def _assert_no_failures(failures: list[str], *, limit: int | None = 50) -> None:
    excerpt = failures if limit is None else failures[:limit]
    assert not failures, "\n".join(excerpt)


# Day forms a 2-letter ligature with these followers; when the pair fires, Day
# is consumed into qsDay_qs<follower> and the rules for the resulting glyph
# differ from the standalone-Day rules. Mirrors _DAY_PAIR_LIGATURES in
# test_calt_regressions.py.
_DAY_PAIR_LIGATURES = frozenset({
    ("qsDay", "qsEat"),
    ("qsDay", "qsUtter"),
})


# --- Panel 1: ·Tea·Tea never produces double halves --------------------------


def test_qs_tea_tea_never_takes_double_halves():
    """Bare ·Tea·Tea: both glyphs are full Tea (not .half)."""
    chars = _char_map()
    glyphs = _shape(chars["qsTea"] + chars["qsTea"])
    meta_map = _compiled_meta()
    assert len(glyphs) == 2, f"Expected 2 glyphs, got {glyphs}"
    for index, glyph_name in enumerate(glyphs):
        meta = meta_map[glyph_name]
        assert meta.base_name == "qsTea", (
            f"Glyph {index} is not qsTea: {glyphs}"
        )
        assert "half" not in meta.traits, (
            f"Glyph {index} is half-Tea: {glyphs}"
        )


def _tea_tea_in_context_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    def append_for_clusters(text: str, target_clusters: set[int], label: str) -> None:
        shaped = _shape_with_clusters(text)
        glyph_names = [g for g, _ in shaped]
        for glyph_name, cluster in shaped:
            if cluster not in target_clusters:
                continue
            meta = meta_map.get(glyph_name)
            if meta is None or meta.base_name != "qsTea":
                continue
            if "half" in meta.traits:
                failures.append(
                    f"{label}: half-Tea selected at cluster {cluster}: {glyph_names}"
                )

    for left_name, left_char in _plain_quikscript_letters():
        # X·Tea·Tea: the two Teas are at clusters 1 and 2.
        append_for_clusters(
            left_char + tea + tea,
            {1, 2},
            f"{left_name} / qsTea / qsTea",
        )

    for right_name, right_char in _plain_quikscript_letters():
        # Tea·Tea·X: the two Teas are at clusters 0 and 1.
        append_for_clusters(
            tea + tea + right_char,
            {0, 1},
            f"qsTea / qsTea / {right_name}",
        )

    return failures


def test_qs_tea_tea_never_takes_double_halves_in_context():
    _assert_no_failures(_tea_tea_in_context_failures())


# --- Panel 3: ·He·Day stays full He, half Day -------------------------------


def test_qs_he_day_keeps_full_he_and_half_day():
    """Bare ·He·Day: full He joined at the baseline to half-Day."""
    chars = _char_map()
    glyphs = _shape(chars["qsHe"] + chars["qsDay"])
    meta_map = _compiled_meta()
    assert len(glyphs) == 2, f"Expected 2 glyphs, got {glyphs}"
    he_meta = meta_map[glyphs[0]]
    day_meta = meta_map[glyphs[1]]
    assert he_meta.base_name == "qsHe", f"First glyph is not qsHe: {glyphs}"
    assert day_meta.base_name == "qsDay", f"Second glyph is not qsDay: {glyphs}"
    assert "half" not in he_meta.traits, f"Expected full He, got {glyphs[0]}"
    assert "half" in day_meta.traits, f"Expected half Day, got {glyphs[1]}"
    assert 0 in _exit_ys(glyphs[0]) & _entry_ys(glyphs[1]), (
        f"Expected ·He·Day to join at Y=0, got exits={sorted(_exit_ys(glyphs[0]))} "
        f"entries={sorted(_entry_ys(glyphs[1]))} in {glyphs}"
    )


def _he_day_left_context_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    he = chars["qsHe"]
    day = chars["qsDay"]
    meta_map = _compiled_meta()

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + he + day
        shaped = _shape_with_clusters(text)
        glyph_names = [g for g, _ in shaped]
        label = f"{left_name} / qsHe / qsDay"

        he_glyph: str | None = None
        day_glyph: str | None = None
        for glyph_name, cluster in shaped:
            meta = meta_map.get(glyph_name)
            if meta is None:
                continue
            if cluster == 1 and meta.base_name == "qsHe":
                he_glyph = glyph_name
            elif cluster == 2 and meta.base_name == "qsDay":
                day_glyph = glyph_name

        if he_glyph is None:
            failures.append(f"{label}: no qsHe at cluster 1: {glyph_names}")
            continue
        if day_glyph is None:
            failures.append(f"{label}: no qsDay at cluster 2: {glyph_names}")
            continue

        if "half" in meta_map[he_glyph].traits:
            failures.append(f"{label}: half-He selected: {glyph_names}")
        if "half" not in meta_map[day_glyph].traits:
            failures.append(f"{label}: full-Day selected: {glyph_names}")
        if 0 not in _exit_ys(he_glyph) & _entry_ys(day_glyph):
            failures.append(
                f"{label}: ·He·Day does not join at Y=0: exit={sorted(_exit_ys(he_glyph))} "
                f"entry={sorted(_entry_ys(day_glyph))} in {glyph_names}"
            )
    return failures


def _he_day_right_context_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    he = chars["qsHe"]
    day = chars["qsDay"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = he + day + right_char
        glyphs = _shape(text)
        glyph_names = list(glyphs)
        label = f"qsHe / qsDay / {right_name}"

        if len(glyphs) < 2:
            failures.append(f"{label}: too few glyphs: {glyph_names}")
            continue

        he_meta = meta_map.get(glyphs[0])
        if he_meta is None or he_meta.base_name != "qsHe":
            failures.append(f"{label}: first glyph is not qsHe: {glyph_names}")
            continue

        if ("qsDay", right_name) in _DAY_PAIR_LIGATURES:
            # Day was consumed into qsDay_qs<right>; assertions diverge by
            # follower. qsDay_qsUtter behaves like a tall right-only join, so
            # He stays full and joins at the baseline. qsDay_qsEat presents a
            # short, x-height-only entry, so He flips to half and joins at Y=5.
            lig_meta = meta_map.get(glyphs[1])
            if lig_meta is None or lig_meta.sequence != ("qsDay", right_name):
                failures.append(
                    f"{label}: second glyph is not the qsDay·{right_name} ligature: "
                    f"{glyph_names}"
                )
                continue
            if right_name == "qsUtter":
                if "half" in he_meta.traits:
                    failures.append(f"{label}: half-He selected: {glyph_names}")
                if "half" not in lig_meta.traits:
                    failures.append(
                        f"{label}: qsDay_qsUtter ligature is not half: {glyph_names}"
                    )
                if 0 not in _exit_ys(glyphs[0]) & _entry_ys(glyphs[1]):
                    failures.append(
                        f"{label}: ·He·qsDay_qsUtter does not join at Y=0 in {glyph_names}"
                    )
            else:  # qsEat
                if "half" not in he_meta.traits:
                    failures.append(f"{label}: full-He selected: {glyph_names}")
                if 5 not in _exit_ys(glyphs[0]) & _entry_ys(glyphs[1]):
                    failures.append(
                        f"{label}: ·He·qsDay_{right_name} does not join at Y=5 in "
                        f"{glyph_names}"
                    )
            continue

        # Standard case: full He joined at Y=0 to half-Day.
        day_meta = meta_map.get(glyphs[1])
        if day_meta is None or day_meta.base_name != "qsDay":
            failures.append(f"{label}: second glyph is not qsDay: {glyph_names}")
            continue
        if "half" in he_meta.traits:
            failures.append(f"{label}: half-He selected: {glyph_names}")
        if "half" not in day_meta.traits:
            failures.append(f"{label}: full-Day selected: {glyph_names}")
        if 0 not in _exit_ys(glyphs[0]) & _entry_ys(glyphs[1]):
            failures.append(
                f"{label}: ·He·Day does not join at Y=0: exit={sorted(_exit_ys(glyphs[0]))} "
                f"entry={sorted(_entry_ys(glyphs[1]))} in {glyph_names}"
            )

    return failures


def test_qs_he_day_keeps_full_he_and_half_day_in_context():
    failures = _he_day_left_context_failures() + _he_day_right_context_failures()
    _assert_no_failures(failures)
