from pathlib import Path

import pytest

from reflow_yaml import reflow

RUNE_WITH_KERN = """\
rune: qsNo
policy:
  order: [loop, flipped]
kern:
  - right:
      family: qsBay
    by: -2
  - {stance: flipped, right: {family: qsTea, stance: half, except: [{family: qsTea, exit: none}]}, by: -1, why: A rationale long enough that the width rule would have broken its line in two places.}
"""


def test_a_rune_kern_block_is_one_flow_record_per_line(tmp_path: Path):
    path = tmp_path / "runes" / "qsNo.yaml"
    path.parent.mkdir()
    path.write_text(RUNE_WITH_KERN)
    assert reflow(path)
    assert path.read_text().split("kern:\n")[1] == (
        "- {right: {family: qsBay}, by: -2}\n"
        "- {stance: flipped, right: {family: qsTea, stance: half, except: [{family: qsTea, exit: none}]}, by: -1, why: A rationale long enough that the width rule would have broken its line in two places.}\n"
    )
    assert "policy:\n  order:\n  - loop\n  - flipped\n" in path.read_text()
    assert not reflow(path)


def test_a_file_outside_the_runes_is_refused(tmp_path: Path):
    path = tmp_path / "glyph_data" / "latin_letters.yaml"
    path.parent.mkdir()
    path.write_text("glyphs:\n  a: {bitmap: ['#']}\n")
    with pytest.raises(ValueError, match="not a rune file"):
        reflow(path)
    assert path.read_text() == "glyphs:\n  a: {bitmap: ['#']}\n"


def test_a_collection_turns_block_unless_it_is_one_of_the_three_flow_leaf_shapes(tmp_path: Path):
    path = tmp_path / "runes" / "qsNo.yaml"
    path.parent.mkdir()
    path.write_text("rune: qsNo\npolicy: {refuse: [{right: {family: qsBay}, at: [1, 2]}], empty: []}\n")
    assert reflow(path)
    assert path.read_text() == (
        "rune: qsNo\npolicy:\n  refuse:\n  - right: {family: qsBay}\n    at: [1, 2]\n  empty: []\n"
    )
    assert not reflow(path)
