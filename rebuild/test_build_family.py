"""Tests for `rebuild/pipeline/build_family.py`: Mono's and Junior's Quikscript glyphs, variation sequences and features over the checked-in spec, and the checks `family_glyphs` makes. rebuild/test_family_against_old_build.py compares the built fonts with the old build's."""

from __future__ import annotations

from dataclasses import replace

import pytest

from rebuild.pipeline import build_family, run_m1
from rebuild.pipeline.build_family import FamilyBuildError
from rebuild.pipeline.model import Bitmap, CellId
from rebuild.pipeline.settle import cell_label


@pytest.fixture(scope="module")
def spec():
    return build_family.load_family_spec()


@pytest.fixture(scope="module")
def family(spec):
    return build_family.family_glyphs(spec)


def _letters(spec) -> list[str]:
    return [name for name, rune in spec.runes.items() if rune.codepoint is not None and not rune.sequence]


class TestTheFamilyGlyphs:
    def test_every_letter_has_its_mono_drawing_and_its_isolated_cell_and_no_ligature_has_a_glyph(
        self, spec, family
    ):
        for name in _letters(spec):
            mono = spec.runes[name].mono
            assert family.records[name]["bitmap"] == list(mono.rows)
            assert family.records[name].get("y_offset", 0) == mono.y_offset
            assert f"{name}.prop" in family.records
            assert family.letters[name] == name
        ligatures = [name for name, rune in spec.runes.items() if rune.sequence]
        assert ligatures
        assert not [key for key in family.records if key.removesuffix(".prop") in ligatures]

    def test_the_variation_sequences_are_the_registrys_for_each_font_under_seniors_names(self, spec, family):
        for font in ("mono", "junior"):
            expected = {
                selector: {
                    spec.runes[rune].codepoint: cell_label(spec, CellId(rune, stance, None, None, ()))
                    for rune, stance in entry.runes.items()
                }
                for selector, entry in spec.registry.variation_sequences.items()
                if font in entry.fonts
            }
            assert expected
            assert family.sequences[font] == expected
        senior_only = [
            selector
            for selector, entry in spec.registry.variation_sequences.items()
            if entry.fonts == ("senior",)
        ]
        assert senior_only and not set(senior_only) & set(family.sequences["junior"])

    def test_junior_draws_a_sequence_as_senior_does_and_mono_from_the_stances_base_bitmap(self, spec, family):
        minted = run_m1.mint_variation_glyphs(spec, {}, font="junior")
        assert minted.sequences == family.sequences["junior"]
        for cell, record in minted.glyphs.items():
            junior = family.records[f"{record.name}.prop"]
            assert junior["bitmap"] == list(record.bitmap)
            assert junior.get("y_offset", 0) == record.y_offset
            base = spec.runes[cell.rune].stances[cell.stance].bitmap
            assert family.records[record.name]["bitmap"] == list(base.rows)
            assert family.letters[record.name] == cell.rune

    def test_an_alternate_whose_base_bitmap_is_not_five_columns_wide_is_refused_for_mono(self, spec):
        selector, entry = next(
            (selector, entry)
            for selector, entry in sorted(spec.registry.variation_sequences.items())
            if "mono" in entry.fonts
        )
        rune_name, stance_name = next(iter(entry.runes.items()))
        rune = spec.runes[rune_name]
        stance = rune.stances[stance_name]
        wide = replace(
            stance, bitmap=Bitmap(tuple(row + " " for row in stance.bitmap.rows), stance.bitmap.y_offset)
        )
        widened = replace(
            spec, runes={**spec.runes, rune_name: replace(rune, stances={**rune.stances, stance_name: wide})}
        )
        with pytest.raises(
            FamilyBuildError, match=f"U\\+{selector:04X} selects {rune_name}'s '{stance_name}' in Mono"
        ):
            build_family.family_glyphs(widened)

    def test_a_letter_without_a_mono_drawing_is_refused(self, spec):
        name = _letters(spec)[0]
        bare = replace(spec, runes={**spec.runes, name: replace(spec.runes[name], mono=None)})
        with pytest.raises(FamilyBuildError, match=f"{name} has no `mono:` drawing"):
            build_family.family_glyphs(bare)

    def test_a_cosmetic_copy_takes_the_name_senior_gives_the_copy_of_the_unjoined_cell(self, spec, family):
        assert family.cosmetic == {"ss06": {"qsOwe": "qsOwe.sole.ss06"}}
        record = spec.runes["qsOwe"].cosmetic[0]
        assert family.records["qsOwe.sole.ss06.prop"]["bitmap"] == list(record.bitmap.rows)
        assert "qsOwe.sole.ss06" not in family.records
        assert family.letters["qsOwe.sole.ss06"] == "qsOwe"


class TestJuniorsFeatures:
    def test_the_cosmetic_sets_come_first_then_the_isolated_overlay_then_the_namer_dot(self, spec, family):
        fea = build_family.junior_fea(spec, family, ["a", "b"])
        assert fea.index("feature ss06 {") < fea.index("feature ss10 {") < fea.index("periodcentered'")
        assert "    sub qsOwe by qsOwe.sole.ss06;" in fea

    def test_the_isolated_overlay_takes_every_junior_glyph_that_is_not_a_bare_letter_to_its_letter(
        self, spec, family
    ):
        fea = build_family.junior_fea(spec, family, [])
        ss10 = fea[fea.index("feature ss10 {") : fea.index("} ss10;")]
        expected = sorted((glyph, letter) for glyph, letter in family.letters.items() if glyph != letter)
        assert expected
        assert ss10.splitlines()[1:] == [f"    sub {glyph} by {letter};" for glyph, letter in expected]

    def test_the_namer_dot_lowers_before_the_junior_glyphs_of_the_shorts_and_keeps_the_mid_word_exception(
        self, spec, family
    ):
        fea = build_family.junior_fea(spec, family, ["a", "zero"])
        shorts = spec.registry.predicate_classes["shorts"]
        followers = sorted(glyph for glyph, letter in family.letters.items() if letter in shorts)
        assert f"[{' '.join(followers)}]" in fea
        assert "qsOwe.sole.ss06" in followers and "qsNo.flipped" in followers
        assert "[a zero]" in fea
        assert "ignore sub @namer_midword periodcentered' @namer_short_followers;" in fea
