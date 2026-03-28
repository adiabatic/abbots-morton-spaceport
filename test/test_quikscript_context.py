from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_font import build_compiled_glyph_metadata, compile_glyph_definitions, load_glyph_data


def _compiled_meta():
    data = load_glyph_data(ROOT / "glyph_data")
    glyphs = compile_glyph_definitions(data, "senior")
    return build_compiled_glyph_metadata(glyphs)


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
