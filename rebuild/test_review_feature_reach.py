"""Tests for the corpus build's configuration fold (rebuild/review/feature_reach.py): which configurations take `default`'s shapes for a window without their trigger letters, and the fallback that shapes a configuration in every window when its features reach past them.

The live fonts always pass the derivation's checks, so neither the mini corpus's tests nor rebuild/test_review_ink.py runs the fallback. The fonts here are built from a few rectangles, with a stylistic set that either stays on ·Tea or also reaches ·Fee, the corpus form of the deep sweep's test that a record gated on ss03 whose window can lack ·Tea makes ss03 shape every text (rebuild/test_deep_sweep.py). The old Senior font and the frozen mini bundle's font check the rule-by-rule judgment on today's class-based and single-substitution lookups.
"""

from pathlib import Path

import pytest

from rebuild.review import build as review_build
from rebuild.review.audit import ACCEPTANCE_CONFIGS
from rebuild.review.feature_reach import derive_fold
from rebuild.review.ink import IDENTITY_DIFF, InkComparator

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI_FONT = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini" / "M1.otf"

TEA = chr(0xE652)
FEE = chr(0xE658)


def _rectangle(width: int, height: int) -> tuple:
    return (((0, 0), (width, 0), (width, height), (0, height)),)


GLYPHS = {
    "qsTea": (_rectangle(100, 150), 200),
    "qsTea.ss03": (_rectangle(100, 250), 200),
    "qsFee": (_rectangle(150, 150), 200),
    "qsFee.alt": (_rectangle(150, 250), 200),
}
CMAP = {0xE652: "qsTea", 0xE658: "qsFee"}

# ss03 renames ·Tea alone, so it cannot fire on a window without ·Tea.
CONFINED = "feature ss03 { sub qsTea by qsTea.ss03; } ss03;"
# ss03's one chaining rule takes ·Tea or ·Fee in both its input and its lookahead slot, so it fires on a window of ·Fee alone.
REACHING = """
lookup SWAP { sub qsTea by qsTea.ss03; sub qsFee by qsFee.alt; } SWAP;
feature ss03 { sub [qsTea qsFee]' lookup SWAP [qsTea qsFee]; } ss03;
"""


def _build_font(path: Path, features: str = "") -> Path:
    """Build a small TTF from `GLYPHS` and `CMAP` with `features` compiled into its layout tables."""
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", *GLYPHS]
    outlines = {}
    metrics = {}
    for name in order:
        contours, advance = GLYPHS.get(name, ((), 500))
        pen = TTGlyphPen(None)
        for contour in contours:
            pen.moveTo(contour[0])
            for point in contour[1:]:
                pen.lineTo(point)
            pen.closePath()
        outlines[name] = pen.glyph()
        metrics[name] = (advance, 0)
    builder = FontBuilder(1000)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(CMAP)
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "FoldTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    if features:
        addOpenTypeFeaturesFromString(builder.font, features)
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 0  # pyright: ignore[reportAttributeAccessIssue]
    builder.font["head"].modified = 0
    builder.save(str(path))
    return path


def test_a_feature_that_reaches_past_its_trigger_is_shaped_in_every_window(tmp_path, capsys):
    """With ss03 confined to ·Tea, ss03 and ss03+ss05 take `default`'s shapes for a window without ·Tea (`ConfigFold.effective`), so a ·Fee·Fee unit that holds `default` and ss03 is compared once, and a ·Tea·Fee unit under both. Pointed at ·Fee too, ss03 and ss03+ss05 fail the check, the line names the rule, and the ·Fee·Fee unit is compared under ss03, where the after font draws the other ·Fee: its ss03 diff is a change, where `default`'s comparison would have read none. In both, the readings each configuration gets under its effective configuration equal the ones it gives compared on its own, and a unit that does not hold `default` is compared under every configuration it holds."""
    before = _build_font(tmp_path / "before.ttf")
    configs = ("default", "ss03")
    for features, reaches in ((CONFINED, False), (REACHING, True)):
        after = _build_font(tmp_path / f"after-{'reaching' if reaches else 'confined'}.ttf", features)
        fold = review_build._config_fold(before, after)
        line = capsys.readouterr().err
        assert set(fold.refused) == ({"ss03", "ss03+ss05"} if reaches else set())
        if reaches:
            assert f"ss03 is shaped in every window because a rule in {after.name}'s lookup" in line
            assert "has no slot of one family" in line
        else:
            assert fold.triggers["ss03"] == fold.triggers["ss03+ss05"] == frozenset(TEA)
            assert "ss03 and ss03+ss05 take default's shapes for a window with no qsTea" in line
        comparator = InkComparator(before, after)
        for text, unit_configs, shaped in (
            (FEE + FEE, configs, list(configs) if reaches else ["default"]),
            (TEA + FEE, configs, list(configs)),
            (FEE + FEE, ("ss03",), ["ss03"]),
        ):
            effective = fold.effective(unit_configs, text)
            assert list(dict.fromkeys(effective)) == shaped
            assert [comparator.config_diff(text, config) for config in effective] == [
                comparator.config_diff(text, config) for config in unit_configs
            ]
            assert comparator.ink_identical(
                text, tuple(dict.fromkeys(effective))
            ) == comparator.ink_identical(text, unit_configs)
        effective = fold.effective(configs, FEE + FEE)
        diffs = [comparator.config_diff(FEE + FEE, config) for config in effective]
        assert diffs[0] == IDENTITY_DIFF
        assert (diffs[1] != IDENTITY_DIFF) is reaches
        assert comparator.ink_identical(FEE + FEE, tuple(dict.fromkeys(effective))) is not reaches


def test_todays_old_font_confines_each_stylistic_set_to_one_letter():
    """The old Senior font's ss03 lookups are class-based and coverage-based chaining lookups, many of whose rules take ·Fee, ·Owe or another letter in an input slot beside a slot of ·Tea glyphs alone. Its ss05 rules name a slot of ·Et glyphs beside one of ·Tea, so either letter confines them, and its ss04 rules take ·It. The mini bundle's font renames ·Tea and ·It by single substitution. Judged rule by rule and chosen across both fonts, ss03, ss05 and ss03+ss05 need ·Tea and ss04 ·It, and no configuration a unit can hold fails a check."""
    fold = derive_fold((review_build.SITE_BEFORE_FONT, MINI_FONT), ACCEPTANCE_CONFIGS)
    assert fold.refused == {}
    assert {config: fold.families[config] for config in ("ss03", "ss04", "ss05", "ss03+ss05")} == {
        "ss03": ("qsTea",),
        "ss04": ("qsIt",),
        "ss05": ("qsTea",),
        "ss03+ss05": ("qsTea",),
    }
    assert fold.triggers["ss04"] == frozenset(chr(0xE670))
    assert fold.effective(("default", "ss03", "ss04", "ss10"), TEA + FEE) == (
        "default",
        "ss03",
        "default",
        "ss10",
    )


@pytest.mark.parametrize(
    ("features", "refusal"),
    (
        (
            "feature ss03 { sub qsTea by qsTea.ss03; } ss03; feature ss03 { pos qsFee 50; } ss03;",
            "GPOS has a ss03 feature record",
        ),
        (
            "feature ss03 { sub qsTea by qsTea.ss03; } ss03; feature calt { sub qsFee by qsTea.ss03; } calt;",
            "makes qsTea.ss03 from qsFee",
        ),
        ("feature ss03 { sub qsTea qsFee by qsTea.ss03; } ss03;", "is of GSUB type 4"),
    ),
    ids=("gpos-record", "made-from-another-family", "ligature-lookup"),
)
def test_a_configuration_fails_each_check_alone(tmp_path, features, refusal):
    """Each check refuses on its own: a GPOS feature record for the set, a substitution anywhere in GSUB that makes a glyph of a trigger family from glyphs with none, and a lookup under the set that is neither a single nor a chaining substitution."""
    before = _build_font(tmp_path / "before.ttf")
    after = _build_font(tmp_path / "after.ttf", features)
    fold = derive_fold((before, after), ("ss03",))
    assert "ss03" not in fold.triggers
    assert refusal in fold.refused["ss03"]
    assert fold.effective(("default", "ss03"), FEE) == ("default", "ss03")
