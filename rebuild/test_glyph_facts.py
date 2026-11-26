"""Tests for the glyph-facts sidecar (`rebuild/pipeline/glyph_facts.py`): what it records for the cells, the raw glyphs and the variation glyphs of the live spec, its coverage check, and that the checker (`rebuild/validation/check.py`) reads what it writes under the format and the name both modules give it."""

from __future__ import annotations

from pathlib import Path

import pytest

from rebuild.pipeline import geometry, glyph_facts, run_m1, surface
from rebuild.pipeline.model import CellId, GlyphRecord
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.validation import check


@pytest.fixture(scope="module")
def spec():
    return load_default_spec()


def _cells(spec, rune: str) -> dict[CellId, GlyphRecord]:
    features = surface._all_features(spec)
    return {
        cell: GlyphRecord(name=cell_label(spec, cell), bitmap=())
        for cell in surface.enumerate_cells(spec, rune, features)
    }


def _document(spec, runes=("qsUtter", "qsNo", "qsMay")) -> dict:
    cells: dict[CellId, GlyphRecord] = {}
    for rune in runes:
        cells.update(_cells(spec, rune))
    bare = {
        CellId(rune, run_m1.RAW_STANCE, None, None, ()): GlyphRecord(name=rune, bitmap=()) for rune in runes
    }
    locked = {
        CellId(rune, run_m1.RAW_STANCE, None, None, ("locked", rune)): GlyphRecord(
            name=f"{rune}.noentry", bitmap=()
        )
        for rune in runes
    }
    return glyph_facts.document(spec, cells, {**bare, **locked}, {}, ("periodcentered",))


def test_the_format_and_the_file_name_are_the_checkers(tmp_path):
    assert glyph_facts.GLYPH_FACTS_FORMAT == check.GLYPH_FACTS_FORMAT
    assert check.glyph_facts_path(tmp_path / "M1.otf").name == glyph_facts.FILE_NAME
    assert check.glyph_facts_path(tmp_path / "M1-Bold.otf").name == glyph_facts.FILE_NAME


def test_a_stance_traits_reach_every_cell_and_the_bound_bitmap_the_entered_alternate(spec):
    glyphs = _document(spec)["glyphs"]
    for cell in _cells(spec, "qsNo"):
        traits = set(spec.runes["qsNo"].stances[cell.stance].traits)
        assert set(glyphs[cell_label(spec, cell)].get("traits", ())) == traits
    alternate = [name for name in glyphs if name.startswith("qsUtter.alternate.")]
    assert alternate
    for name in alternate:
        assert "alt" in glyphs[name]["traits"]
        entered = ".en-y" in name
        assert (glyphs[name].get("bitmap") == "reaches-way-back") == entered, name


def test_the_bare_glyph_and_the_isolated_cell_are_isolated_and_nothing_else_is(spec):
    glyphs = _document(spec)["glyphs"]
    for rune in ("qsUtter", "qsNo", "qsMay"):
        isolated = {rune, cell_label(spec, geometry.isolated_cell(spec, rune))}
        marked = {
            name for name, entry in glyphs.items() if entry.get("isolated") and name.split(".")[0] == rune
        }
        assert marked == isolated
        assert glyphs[f"{rune}.noentry"].get("traits", []) == glyphs[rune].get("traits", [])
    assert glyphs["periodcentered"] == {}


def test_coverage_names_a_rune_glyph_without_facts_and_facts_for_no_glyph(spec):
    facts = _document(spec)
    order = [*facts["glyphs"], "A", "qsAngleParenLeft"]
    glyph_facts.check_coverage(order, facts, spec.runes)
    with pytest.raises(glyph_facts.GlyphFactsError, match="no facts for 1 rune glyph"):
        glyph_facts.check_coverage([*order, "qsMay.loop.en-y9"], facts, spec.runes)
    with pytest.raises(glyph_facts.GlyphFactsError, match="facts for 1 glyph"):
        glyph_facts.check_coverage([name for name in order if name != "qsMay"], facts, spec.runes)


def test_the_checker_reads_what_the_build_writes(spec, tmp_path: Path):
    facts = _document(spec)
    path = glyph_facts.write(tmp_path, facts)
    read = check.read_glyph_facts(path)
    assert set(read) == set(facts["glyphs"])
    assert read["qsNo.flipped.en-y0"].traits == frozenset({"alt"})
    assert read["qsMay"].isolated
    inode = path.stat().st_ino
    glyph_facts.write(tmp_path, facts)
    assert path.stat().st_ino == inode
