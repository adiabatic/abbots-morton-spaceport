"""The any-of `data-expect` regression checks: each test gives a text and the expect strings any one of which its shaping must match, checked against the Senior under site/ by the checker the pin items use (`rebuild/validation/check.py`, `assert_expect_any`).

A check the rebuilt Senior fails is a strict expected failure whose reason names what the rebuild shapes instead, pending the author's ruling (`WHATNEXT.md`), so it fails once the rebuild shapes it as the check expects. The root conftest publishes the fonts into site/ at the start of the session (`pin_fonts`).
"""

from collections.abc import Sequence

import pytest

from rebuild.validation.check import CheckFont, assert_expect_any
from rebuild.validation.expect import CHAR_TO_GLYPH

ZWNJ = "‌"
_CHARS = {name: char for char, name in CHAR_TO_GLYPH.items()}
_FONT: dict[str, CheckFont] = {}


@pytest.fixture(autouse=True, scope="module")
def _senior(pin_fonts: dict[str, CheckFont]) -> None:
    _FONT["senior"] = pin_fonts["senior"]


def _char_map() -> dict[str, str]:
    return _CHARS


def _qs_text(*parts: str) -> str:
    """Spell family names (`qsMay`) and the two boundaries (`space`, `ZWNJ`) as text; any other part stands for itself."""
    result = []
    for part in parts:
        if part == "space":
            result.append(" ")
        elif part == "ZWNJ":
            result.append(ZWNJ)
        elif part in _CHARS:
            result.append(_CHARS[part])
        elif part.startswith("qs"):
            raise KeyError(f"Unknown Quikscript glyph name: {part}")
        else:
            result.append(part)
    return "".join(result)


def _family_to_label(family: str) -> str:
    base = family.removeprefix("qs")
    return "-ing" if base == "Ing" else base


def _assert_expect_any(text: str, expects: Sequence[str]) -> None:
    assert_expect_any(_FONT["senior"], text, expects)


# The checks the rebuilt Senior fails, each by its test id with what the rebuild shapes instead, pending the author's ruling (WHATNEXT.md). Each is a strict expected failure, so it fails once the rebuild shapes the text as the check expects.
REBUILD_SHAPES: dict[str, str] = {
    "test_ah_fee_utter_keeps_left_join": "the rebuild shapes ·Ah ~x~ ·Fee | ·Utter (qsAh.sole.ex-y5 qsFee.reversed-loop.en-y5 qsUtter.mono)",
    "test_at_may_they_utter_looks_ok": "the rebuild shapes ·At.ex-ext-3 ~b~ ·May | ·They+Utter (qsAt.falling.ex-y0.ex-ext-3 qsMay.loop.en-y0 qsThey_qsUtter.sole)",
    "test_day_eat_takes_the_half_day_where_day_does[out-tea-jai-day-eat]": "the rebuild shapes ·Out+Tea | ·J’ai ~b~ ·Day+Eat.half (qsOut_qsTea.sole qsJai.sole.ex-y0 qsDay_qsEat.half.en-y0)",
    "test_day_eat_takes_the_half_day_where_day_does[pea-day-eat-gay-owe]": "the rebuild shapes ·Pea ~b~ ·Day+Eat.half | ·Gay.ex-ext-2 ~x~ ·Owe (qsPea.full.ex-y0 qsDay_qsEat.half.en-y0 qsGay.sole.ex-y5.ex-ext-2 qsOwe.sole.en-y5)",
    "test_day_eat_takes_the_half_day_where_day_does[tea-day-eat-gay-owe]": "the rebuild shapes ·Tea ~b~ ·Day+Eat.half | ·Gay.ex-ext-2 ~x~ ·Owe (qsTea.full.ex-y0 qsDay_qsEat.half.en-y0 qsGay.sole.ex-y5.ex-ext-2 qsOwe.sole.en-y5)",
    "test_day_eat_takes_the_half_day_where_day_does[they-zoo-excite-day-eat]": "the rebuild shapes ·They+Zoo ~b~ ·Excite ~b~ ·Day+Eat.half (qsThey_qsZoo.sole.ex-y0 qsExcite.sole.en-y0.ex-y0 qsDay_qsEat.half.en-y0)",
    "test_day_eat_takes_the_half_day_where_day_does[they-zoo-no-day-eat]": "the rebuild shapes ·They+Zoo ~b~ ·No.alt ~b~ ·Day+Eat.half (qsThey_qsZoo.sole.ex-y0 qsNo.flipped.en-y0.ex-y0 qsDay_qsEat.half.en-y0)",
    "test_fee_entry_xheight_after_extended_predecessor[qsAh]": "the rebuild shapes ·Ah ~x~ ·Fee (qsAh.sole.ex-y5 qsFee.reversed-loop.en-y5)",
    "test_fee_tea_i_extends_exit": "the rebuild shapes ·Fee | ·Tea.ex-ext-1 ~b~ ·I (qsFee.loop qsTea.full.ex-y0.ex-ext-1 qsI.loop.en-y0)",
    "test_he_vie_utter_day_joins_baseline_then_xheight": "the rebuild shapes ·He ~b~ ·Vie.en-ext-1.ex-con-1 ~b~ ·Utter ~x~ ·Day (qsHe.full.ex-y0 qsVie.swept-out.en-y0.ex-y0.en-ext-1.ex-con-1 qsUtter.mono.en-y0.ex-y5 qsDay.full.en-y5)",
    "test_jay_utter_contracts_before_jai": "the rebuild shapes ·Ah ~x~ ·Jay+Utter ~x~ ·J’ai.en-con-2 (qsAh.sole.ex-y5 qsJay_qsUtter.sole.en-y5.ex-y5 qsJai.sole.en-y5.en-con-2)",
    "test_owe_fee_may_owe_joins_fee_at_xheight": "the rebuild shapes ·Owe | ·Fee.ex-ext-3 ~x~ ·May (qsOwe.sole qsFee.loop.ex-y5.ex-ext-3 qsMay.loop.en-y5)",
    "test_pea_joins_a_pea_that_dips_into_its_follower_at_y6[et-pea-pea-cheer]": "the rebuild shapes ·Et ~b~ ·Pea | ·Pea.half ~x~ ·Cheer (qsEt.sole.ex-y0 qsPea.full.en-y0 qsPea.half.ex-y5 qsCheer.sole.en-y5)",
    "test_pea_joins_a_pea_that_dips_into_its_follower_at_y6[pea-pea-no]": "the rebuild shapes ·Pea.half ~6~ ·Pea ~b~ ·No.alt (qsPea.half.ex-y6 qsPea.full.en-y6.ex-y0 qsNo.flipped.en-y0)",
    "test_pea_joins_neither_side_after_et_before_the_alternate_utter": "the rebuild shapes ·Et ~b~ ·Pea | ·Utter.alt ~b~ ·Ah (qsEt.sole.ex-y0 qsPea.full.en-y0 qsUtter.alternate.ex-y0 qsAh.sole.en-y0)",
    "test_pea_keeps_its_onward_join_after_et_and_awe[awe-pea-vie]": "the rebuild shapes ·Awe | ·Pea ~b~ ·Vie (qsAwe.sole qsPea.full.ex-y0 qsVie.normal.en-y0)",
    "test_pea_keeps_its_onward_join_after_et_and_awe[et-pea-day-eat]": "the rebuild shapes ·Et ~b~ ·Pea | ·Day+Eat (qsEt.sole.ex-y0 qsPea.full.en-y0 qsDay_qsEat.full)",
    "test_pea_keeps_its_onward_join_after_et_and_awe[et-pea-day]": "the rebuild shapes ·Et ~b~ ·Pea | ·Day (qsEt.sole.ex-y0 qsPea.full.en-y0 qsDay.full)",
    "test_pea_keeps_its_onward_join_after_et_and_awe[et-pea-it]": "the rebuild shapes ·Et ~b~ ·Pea | ·It (qsEt.sole.ex-y0 qsPea.full.en-y0 qsIt.sole)",
    "test_pea_keeps_its_onward_join_after_et_and_awe[et-pea-may]": "the rebuild shapes ·Et ~b~ ·Pea | ·May (qsEt.sole.ex-y0 qsPea.full.en-y0 qsMay.loop)",
    "test_tea_before_i_extends_exit": "the rebuild shapes ·Tea.ex-ext-1 ~b~ ·I (qsTea.full.ex-y0.ex-ext-1 qsI.loop.en-y0)",
    "test_zwnj_keeps_it_entryless_while_still_joining_zoo": "the rebuild shapes ·Day | ◊ZWNJ | ·It.locked ~b~ ·Zoo.half ~b~ ·I.ex-ext-1 ~x~ ·Roe (qsDay.full space qsIt.sole.ex-y0.locked qsZoo.half.en-y0.ex-y0 qsI.loop.en-y0.ex-y5.ex-ext-1 qsRoe.sole.en-y5)",
}


def _pending(test: str, case: str) -> tuple:
    reason = REBUILD_SHAPES.get(f"{test}[{case}]")
    return () if reason is None else (pytest.mark.xfail(strict=True, reason=reason),)


def test_see_ooze_is_sensible():
    _assert_expect_any(
        _qs_text("qsSee", "qsOoze"),
        [
            "·See ~b~ ·Ooze",
        ],
    )


def test_jay_it_no_is_sensible():
    _assert_expect_any(
        _qs_text("qsJay", "qsIt", "qsNo"),
        [
            "·Jay ~b~ ·It | ·No",
            "·Jay  |  ·It   ·No",
        ],
    )


def test_jay_it_zoo_is_sensible():
    _assert_expect_any(
        _qs_text("qsJay", "qsIt", "qsZoo"),
        [
            "·Jay ~b~ ·It ~x~ ·Zoo.!half",
            "·Jay  |  ·It ~b~ ·Zoo.half",
        ],
    )


def test_ye_it_zoo_is_sensible():
    _assert_expect_any(
        _qs_text("qsYe", "qsIt", "qsZoo"),
        [
            "·Ye | ·It ·Zoo",
        ],
    )


def test_it_zoo_is_sensible():
    _assert_expect_any(
        _qs_text("qsIt", "qsZoo"),
        [
            "·It ~b~ ·Zoo.half",
        ],
    )


def test_may_it_see_low_is_sensible():
    _assert_expect_any(
        _qs_text("qsMay", "qsIt", "qsSee", "qsLow"),
        [
            "·May.ex-ext-1 ~x~ ·It ~b~ ·See  |  ·Low",
            "·May.ex-ext-1 ~x~ ·It  |  ·See ~b~ ·Low",
        ],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsPea", "qsOwe"),
            ["·Pea | ·Owe"],
            id="bare",
        ),
        pytest.param(
            _qs_text("qsBay", "qsPea", "qsOwe"),
            ["·Bay | ·Pea | ·Owe"],
            id="after-bay",
        ),
        pytest.param(
            _qs_text("qsTea", "qsPea", "qsOwe"),
            ["·Tea | ·Pea | ·Owe"],
            id="after-tea",
        ),
    ],
)
def test_pea_never_joins_owe_at_word_end(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_owe_keeps_right_exit_with_real_follower_after_pea():
    """·Pea·Owe never joins, but ·Owe still takes its right exit into a real follower."""
    _assert_expect_any(_qs_text("qsPea", "qsOwe", "qsNo"), ["·Pea | ·Owe.ex-y5 ~x~ ·No"])


def test_owe_at_word_start_before_fee_has_no_left_anchor():
    _assert_expect_any(
        _qs_text("qsOwe", "qsFee"),
        ["·Owe ~x~ ·Fee"],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsMay", "qsFee"),
            ["·May.ex-ext-1 ~x~ ·Fee"],
            marks=_pending("test_fee_entry_xheight_after_extended_predecessor", "qsMay"),
            id="qsMay",
        ),
        pytest.param(
            _qs_text("qsNo", "qsFee"),
            ["·No.ex-ext-1 ~x~ ·Fee"],
            marks=_pending("test_fee_entry_xheight_after_extended_predecessor", "qsNo"),
            id="qsNo",
        ),
        pytest.param(
            _qs_text("qsLow", "qsFee"),
            ["·Low.ex-ext-1 ~x~ ·Fee"],
            marks=_pending("test_fee_entry_xheight_after_extended_predecessor", "qsLow"),
            id="qsLow",
        ),
        pytest.param(
            _qs_text("qsAh", "qsFee"),
            ["·Ah.ex-ext-1 ~x~ ·Fee"],
            marks=_pending("test_fee_entry_xheight_after_extended_predecessor", "qsAh"),
            id="qsAh",
        ),
        pytest.param(
            _qs_text("qsUtter", "qsFee"),
            ["·Utter.ex-ext-1 ~x~ ·Fee"],
            marks=_pending("test_fee_entry_xheight_after_extended_predecessor", "qsUtter"),
            id="qsUtter",
        ),
    ],
)
def test_fee_entry_xheight_after_extended_predecessor(text: str, expects: list[str]):
    """When a predecessor extends its exit before ·Fee, ·Fee must take its ``en-y5`` stance and join it at the x-height. A bare ·Fee there leaves a 1-pixel gap."""
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsOut", "qsFee", "qsJai"),
            ["·Out.∅ |?| ·Fee ~x~ ·Jai"],
            id="qsJai",
        ),
        pytest.param(
            _qs_text("qsOut", "qsFee", "qsCheer"),
            ["·Out.∅ |?| ·Fee.ex-ext-1 ~x~ ·Cheer"],
            id="qsCheer",
        ),
        pytest.param(
            _qs_text("qsOut", "qsFee", "qsAwe"),
            ["·Out.∅ |?| ·Fee ~x~ ·Awe"],
            id="qsAwe",
        ),
    ],
)
def test_out_does_not_reach_for_fee_when_fee_connects_right(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_out_fee_utter_lets_out_reach_for_fee():
    """``qsFee.exit_xheight_before_utter`` has no entry, and its ``not_after`` list names the letters that join ·Fee at the x-height, ·Out among them. So ·Out·Fee·Utter keeps the ·Out ~x~ ·Fee join."""
    _assert_expect_any(
        _qs_text("qsOut", "qsFee", "qsUtter"),
        [
            "·Out.ex-ext-1 ~x~ ·Fee | ·Utter",
        ],
    )


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_ah_fee_utter_keeps_left_join"])
def test_ah_fee_utter_keeps_left_join():
    """·Ah·Fee·Utter keeps the ·Ah ~x~ ·Fee join: a following ·Utter does not switch ·Fee to the entry-less ``exit_xheight_before_utter`` stance."""
    _assert_expect_any(
        _qs_text("qsAh", "qsFee", "qsUtter"),
        [
            "·Ah.ex-ext-1 ~x~ ·Fee | ·Utter.∅",
        ],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsAh", "qsMay", "qsPea"),
            ["·Ah ~x~ ·May | ·Pea.∅"],
            id="ah",
        ),
        pytest.param(
            _qs_text("qsFee", "qsMay", "qsPea"),
            ["·Fee ~x~ ·May | ·Pea.∅"],
            id="fee",
        ),
        pytest.param(
            _qs_text("qsI", "qsMay", "qsPea"),
            ["·I ~x~ ·May | ·Pea.∅"],
            id="i",
        ),
    ],
)
def test_may_pea_does_not_select_pea_entry_after_entry_only_may(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_fee_may_uses_extension_pair():
    """·Fee joins ·May at the x-height. The assertion leaves out the extension width and the stances the two take, the old font's ``before-may`` and ``after-fee``, which have no form in the rune vocabulary, so that the extension can be tuned without editing this test."""
    _assert_expect_any(
        _qs_text("qsFee", "qsMay"),
        ["·Fee ~x~ ·May"],
    )


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_owe_fee_may_owe_joins_fee_at_xheight"])
def test_owe_fee_may_owe_joins_fee_at_xheight():
    """·Owe joins ·Fee at the x-height, and ·Fee.en-y5 has no exit, so ·May stays unjoined. ·Fee cannot both enter and exit at the x-height (qsFee's ``notes``)."""
    _assert_expect_any(
        _qs_text("qsOwe", "qsFee", "qsMay"),
        ["·Owe ~x~ ·Fee | ·May"],
    )


def test_utter_keeps_middle_pea_xheight_left_join_when_pea_also_joins_right():
    _assert_expect_any(
        _qs_text("qsUtter", "qsPea", "qsAwe"),
        [
            "·Utter ~x~ ·Pea.half ~x~ ·Awe",
        ],
    )


def test_ah_does_not_gain_middle_pea_xheight_left_join_when_pea_joins_right():
    _assert_expect_any(
        _qs_text("qsAh", "qsPea", "qsAwe"),
        [
            "·Ah | ·Pea.half ~x~ ·Awe",
        ],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsIng", "qsThaw"),
            ["·-ing.ex-ext-2 ~b~ ·Thaw"],
            id="ing-before-thaw",
        ),
        pytest.param(
            _qs_text("qsThaw", "qsIng"),
            ["·Thaw ~b~ ·-ing.en-ext-2"],
            id="thaw-before-ing",
        ),
    ],
)
def test_ing_and_thaw_in_either_order_extend_their_baseline_join(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_it_strips_entry_before_ing_when_left_join_conflicts():
    _assert_expect_any(
        _qs_text("qsOoze", "qsIt", "qsIng"),
        [
            "·Ooze |?| ·It ~b~ ·-ing",
        ],
    )


def test_it_preserves_baseline_entry_when_ing_join_is_blocked():
    _assert_expect_any(
        _qs_text("qsBay", "qsIt", "qsIng"),
        [
            "·Bay ~b~ ·It |?| ·-ing",
        ],
    )


def test_it_preserves_baseline_entry_before_zoo():
    _assert_expect_any(
        _qs_text("qsBay", "qsIt", "qsZoo"),
        ["·Bay ~b~ ·It.ex-ext-1 ~x~ ·Zoo"],
    )


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_zwnj_keeps_it_entryless_while_still_joining_zoo"])
def test_zwnj_keeps_it_entryless_while_still_joining_zoo():
    _assert_expect_any(
        _qs_text("qsDay", ZWNJ, "qsIt", "qsZoo", "qsI", "qsRoe"),
        [
            "·Day | ◊ZWNJ | ·It.locked.ex-ext-1 ~x~ ·Zoo ~b~ ·I.ex-ext-1 ~x~ ·Roe",
        ],
    )


def test_no_alt_selected_after_ox_before_fee():
    _assert_expect_any(
        _qs_text("qsOx", "qsNo", "qsFee"),
        ["·Ox ~b~ ·No.alt |?| ·Fee"],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsHe", "qsYe"),
            ["·He.∅ | ·Ye.∅"],
            id="he-ye",
        ),
        pytest.param(
            _qs_text("qsIt", "qsYe"),
            ["·It.∅ | ·Ye.∅"],
            id="it-ye",
        ),
        pytest.param(
            _qs_text("qsPea", "qsYe"),
            ["·Pea.∅ | ·Ye.∅"],
            id="pea-ye",
        ),
        pytest.param(
            _qs_text("qsTea", "qsYe"),
            ["·Tea.∅ | ·Ye.∅"],
            id="tea-ye",
        ),
        pytest.param(
            _qs_text("qsThey", "qsYe"),
            ["·They.∅ | ·Ye.∅"],
            id="they-ye",
        ),
        pytest.param(
            _qs_text("qsWay", "qsYe"),
            ["·Way.∅ | ·Ye.∅"],
            id="way-ye",
        ),
        pytest.param(
            _qs_text("qsWhy", "qsYe"),
            ["·Why.∅ | ·Ye.∅"],
            id="why-ye",
        ),
        pytest.param(
            _qs_text("qsYe", "qsExam"),
            ["·Ye.∅ |?| ·Exam"],
            id="ye-exam",
        ),
        pytest.param(
            _qs_text("qsYe", "qsExcite"),
            ["·Ye.∅ |?| ·Excite"],
            id="ye-excite",
        ),
        pytest.param(
            _qs_text("qsYe", "qsIng"),
            ["·Ye.∅ |?| ·-ing.!entry"],
            id="ye-ing",
        ),
        pytest.param(
            _qs_text("qsYe", "qsIt"),
            ["·Ye.∅ | ·It.∅"],
            id="ye-it",
        ),
        pytest.param(
            _qs_text("qsYe", "qsSee"),
            ["·Ye.∅ |?| ·See"],
            id="ye-see",
        ),
    ],
)
def test_ye_sequences_keep_the_nonjoining_stances(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsYe", "qsIng", "qsThaw"), ["·Ye.∅ |?| ·-ing.!entry ~b~ ·Thaw"], id="ye-ing-thaw"
        ),
        pytest.param(
            _qs_text("qsHe", "qsIng", "qsThaw"), ["·He |?| ·-ing.!entry ~b~ ·Thaw"], id="he-ing-thaw"
        ),
    ],
)
def test_ing_never_joins_after_he_or_ye_even_before_thaw(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_they_may_keeps_manual_baseline_join():
    _assert_expect_any(
        _qs_text("qsThey", "qsMay"),
        ["·They ~b~ ·May"],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsBay", "qsMay", "qsNo"), ["·Bay ~b~ ·May.ex-ext-1 ~x~ ·No"], id="bay-may-no"),
        pytest.param(
            _qs_text("qsBay", "qsMay", "qsOwe"), ["·Bay ~b~ ·May.ex-ext-1 ~x~ ·Owe"], id="bay-may-owe"
        ),
        # After ·He, ·It, ·Pea, ·Tea, or ·Ye, ·May also extends its entry (`en-ext-1`); its exit extension is still `ex-ext-1`.
        pytest.param(_qs_text("qsHe", "qsMay", "qsNo"), ["·He ~b~ ·May.ex-ext-1 ~x~ ·No"], id="he-may-no"),
        pytest.param(_qs_text("qsTea", "qsMay", "qsNo"), ["·Tea ~b~ ·May.ex-ext-1 ~x~ ·No"], id="tea-may-no"),
    ],
)
def test_may_exit_extends_one_pixel_after_a_baseline_join(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsMay", "qsNo"),
            ["·May.!extended ~x~ ·No"],
            id="may-no",
        ),
        pytest.param(
            _qs_text("qsMay", "qsOwe"),
            ["·May.!extended ~x~ ·Owe"],
            id="may-owe",
        ),
    ],
)
def test_may_exit_unextended_without_a_baseline_predecessor(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsHe", "qsExcite"),
            ["·He.∅ | ·Excite.∅"],
            id="he-excite",
        ),
        pytest.param(
            _qs_text("qsOwe", "qsTea"),
            ["·Owe.∅ | ·Tea.∅"],
            id="owe-tea",
        ),
        pytest.param(
            _qs_text("qsShe", "qsThaw"),
            ["·She.∅ |?| ·Thaw"],
            id="she-thaw",
        ),
        pytest.param(
            _qs_text("qsTea", "qsOwe"),
            ["·Tea.∅ | ·Owe.∅"],
            id="tea-owe",
        ),
        pytest.param(
            _qs_text("qsWay", "qsExcite"),
            ["·Way.∅ | ·Excite.∅"],
            id="way-excite",
        ),
        pytest.param(
            _qs_text("qsWay", "qsSee"),
            ["·Way.∅ | ·See.∅"],
            id="way-see",
        ),
        pytest.param(
            _qs_text("qsWay", "qsTea"),
            ["·Way.∅ | ·Tea.∅"],
            id="way-tea",
        ),
        pytest.param(
            _qs_text("qsWay", "qsVie"),
            ["·Way.∅ | ·Vie.∅"],
            id="way-vie",
        ),
        pytest.param(
            _qs_text("qsWhy", "qsExcite"),
            ["·Why.∅ | ·Excite.∅"],
            id="why-excite",
        ),
        pytest.param(
            _qs_text("qsWhy", "qsSee"),
            ["·Why.∅ | ·See.∅"],
            id="why-see",
        ),
        pytest.param(
            _qs_text("qsWhy", "qsTea"),
            ["·Why.∅ | ·Tea.∅"],
            id="why-tea",
        ),
        pytest.param(
            _qs_text("qsWhy", "qsThaw"),
            ["·Why.∅ | ·Thaw.∅"],
            id="why-thaw",
        ),
        pytest.param(
            _qs_text("qsWhy", "qsVie"),
            ["·Why.∅ | ·Vie.∅"],
            id="why-vie",
        ),
    ],
)
def test_nonjoining_pairs_do_not_connect(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_utter_gay_tea_oy_uses_normal_utter():
    """In ·Utter·Gay·Tea·Oy, the non-alt ·Utter joins ·Gay at the x-height. ·Gay drops its exit because the ``qsTea_qsOy`` ligature has no entry."""
    _assert_expect_any(
        _qs_text("qsUtter", "qsGay", "qsTea", "qsOy"),
        ["·Utter.!alt ~x~ ·Gay | ·Tea+Oy"],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsJay", "qsYe"), ["·Jay.ex-ext-1 ~b~ ·Ye"], id="jay-ye"),
        pytest.param(_qs_text("qsJai", "qsYe"), ["·J’ai.ex-ext-1 ~b~ ·Ye"], id="jai-ye"),
    ],
)
def test_jay_and_jai_join_ye_at_the_baseline_with_one_extra_pixel(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsEt", "qsPea"), ["·Et ~b~ ·Pea"], id="et-pea"),
        pytest.param(_qs_text("qsAwe", "qsPea"), ["·Awe ~b~ ·Pea"], id="awe-pea"),
        pytest.param(_qs_text("qsEt", "qsPea", "qsTea"), ["·Et ~b~ ·Pea | ·Tea"], id="et-pea-tea"),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsAt", "qsMay"), ["·Et ~b~ ·Pea | ·At ~b~ ·May"], id="et-pea-at-may"
        ),
        pytest.param(
            _qs_text("qsAwe", "qsPea", "qsSee", "qsEat"), ["·Awe ~b~ ·Pea | ·See+Eat"], id="awe-pea-see-eat"
        ),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsSee", "qsUtter"),
            ["·Et ~b~ ·Pea | ·See+Utter"],
            id="et-pea-see-utter",
        ),
    ],
)
def test_pea_joins_et_and_awe_at_baseline_when_it_joins_nothing_after(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsIt"),
            ["·Et | ·Pea.half ~x~ ·It"],
            marks=_pending("test_pea_keeps_its_onward_join_after_et_and_awe", "et-pea-it"),
            id="et-pea-it",
        ),
        pytest.param(
            _qs_text("qsAwe", "qsPea", "qsPea"),
            ["·Awe | ·Pea.half ~6~ ·Pea"],
            marks=_pending("test_pea_keeps_its_onward_join_after_et_and_awe", "awe-pea-pea"),
            id="awe-pea-pea",
        ),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsDay"),
            ["·Et | ·Pea ~b~ ·Day.half"],
            marks=_pending("test_pea_keeps_its_onward_join_after_et_and_awe", "et-pea-day"),
            id="et-pea-day",
        ),
        pytest.param(
            _qs_text("qsAwe", "qsPea", "qsVie"),
            ["·Awe | ·Pea ~b~ ·Vie.en-ext-1"],
            marks=_pending("test_pea_keeps_its_onward_join_after_et_and_awe", "awe-pea-vie"),
            id="awe-pea-vie",
        ),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsMay"),
            ["·Et | ·Pea ~b~ ·May.en-ext-1"],
            marks=_pending("test_pea_keeps_its_onward_join_after_et_and_awe", "et-pea-may"),
            id="et-pea-may",
        ),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsDay", "qsEat"),
            ["·Et | ·Pea ~b~ ·Day+Eat.half"],
            marks=_pending("test_pea_keeps_its_onward_join_after_et_and_awe", "et-pea-day-eat"),
            id="et-pea-day-eat",
        ),
        pytest.param(
            _qs_text("qsAwe", "qsPea", "qsDay", "qsEat"),
            ["·Awe | ·Pea ~b~ ·Day+Eat.half"],
            marks=_pending("test_pea_keeps_its_onward_join_after_et_and_awe", "awe-pea-day-eat"),
            id="awe-pea-day-eat",
        ),
    ],
)
def test_pea_keeps_its_onward_join_after_et_and_awe(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsPea", "qsDay", "qsEat"),
            ["·Pea ~b~ ·Day+Eat.half"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "pea-day-eat"),
            id="pea-day-eat",
        ),
        pytest.param(
            _qs_text("qsIt", "qsDay", "qsEat"),
            ["·It ~b~ ·Day+Eat.half"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "it-day-eat"),
            id="it-day-eat",
        ),
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsDay", "qsEat"),
            ["·Et ~b~ ·Tea | ·Day+Eat.∅"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "et-tea-day-eat"),
            id="et-tea-day-eat",
        ),
        pytest.param(
            _qs_text("qsOut", "qsTea", "qsDay", "qsEat"),
            ["·Out+Tea | ·Day+Eat.∅"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "out-tea-day-eat"),
            id="out-tea-day-eat",
        ),
        pytest.param(
            _qs_text("qsSee", "qsOut", "qsTea", "qsDay", "qsEat"),
            ["·See ~b~ ·Out+Tea | ·Day+Eat.∅"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "see-out-tea-day-eat"),
            id="see-out-tea-day-eat",
        ),
        pytest.param(
            _qs_text("qsThey", "qsZoo", "qsNo", "qsDay", "qsEat"),
            ["·They+Zoo | ·No ~x~ ·Day+Eat.!half"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "they-zoo-no-day-eat"),
            id="they-zoo-no-day-eat",
        ),
        pytest.param(
            _qs_text("qsThey", "qsZoo", "qsExcite", "qsDay", "qsEat"),
            ["·They+Zoo | ·Excite | ·Day+Eat.!half"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "they-zoo-excite-day-eat"),
            id="they-zoo-excite-day-eat",
        ),
        pytest.param(
            _qs_text("qsOut", "qsTea", "qsJai", "qsDay", "qsEat"),
            ["·Out+Tea | ·J’ai | ·Day+Eat.!half"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "out-tea-jai-day-eat"),
            id="out-tea-jai-day-eat",
        ),
        pytest.param(
            _qs_text("qsHe", "qsNo", "qsIt", "qsDay", "qsEat"),
            ["·He.half ~x~ ·No ~x~ ·It ~b~ ·Day+Eat.half"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "he-no-it-day-eat"),
            id="he-no-it-day-eat",
        ),
        pytest.param(
            _qs_text("qsPea", "qsDay", "qsEat", "qsGay", "qsOwe"),
            ["·Pea ~b~ ·Day+Eat.half | ·Gay | ·Owe"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "pea-day-eat-gay-owe"),
            id="pea-day-eat-gay-owe",
        ),
        pytest.param(
            _qs_text("qsTea", "qsDay", "qsEat", "qsGay", "qsOwe"),
            ["·Tea ~b~ ·Day+Eat.half.en-ext-1 | ·Gay | ·Owe"],
            marks=_pending("test_day_eat_takes_the_half_day_where_day_does", "tea-day-eat-gay-owe"),
            id="tea-day-eat-gay-owe",
        ),
    ],
)
def test_day_eat_takes_the_half_day_where_day_does(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.xfail(
    strict=True, reason=REBUILD_SHAPES["test_pea_joins_neither_side_after_et_before_the_alternate_utter"]
)
def test_pea_joins_neither_side_after_et_before_the_alternate_utter():
    _assert_expect_any(_qs_text("qsEt", "qsPea", "qsUtter", "qsAh"), ["·Et | ·Pea | ·Utter.alt ~b~ ·Ah"])


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsPea", "qsPea", "qsCheer"),
            ["·Pea.half ~6~ ·Pea.half ~x~ ·Cheer"],
            marks=_pending("test_pea_joins_a_pea_that_dips_into_its_follower_at_y6", "pea-pea-cheer"),
            id="pea-pea-cheer",
        ),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsPea", "qsCheer"),
            ["·Et | ·Pea.half ~6~ ·Pea.half ~x~ ·Cheer"],
            marks=_pending("test_pea_joins_a_pea_that_dips_into_its_follower_at_y6", "et-pea-pea-cheer"),
            id="et-pea-pea-cheer",
        ),
        pytest.param(
            _qs_text("qsPea", "qsPea", "qsIt"),
            ["·Pea.half ~6~ ·Pea.half ~x~ ·It"],
            marks=_pending("test_pea_joins_a_pea_that_dips_into_its_follower_at_y6", "pea-pea-it"),
            id="pea-pea-it",
        ),
        pytest.param(
            _qs_text("qsPea", "qsPea", "qsNo"),
            ["·Pea.half ~6~ ·Pea.half ~x~ ·No"],
            marks=_pending("test_pea_joins_a_pea_that_dips_into_its_follower_at_y6", "pea-pea-no"),
            id="pea-pea-no",
        ),
    ],
)
def test_pea_joins_a_pea_that_dips_into_its_follower_at_y6(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsWay", "qsSee"), ["·Way.!half | ·See"], id="way-before-see"),
        pytest.param(_qs_text("qsWay", "qsTea"), ["·Way.!half | ·Tea"], id="way-before-tea"),
        pytest.param(_qs_text("qsWay", "qsVie"), ["·Way.!half | ·Vie"], id="way-before-vie"),
        pytest.param(_qs_text("qsWhy", "qsSee"), ["·Why.!half | ·See"], id="why-before-see"),
        pytest.param(_qs_text("qsWhy", "qsTea"), ["·Why.!half | ·Tea"], id="why-before-tea"),
        pytest.param(_qs_text("qsWhy", "qsThaw"), ["·Why.!half | ·Thaw"], id="why-before-thaw"),
        pytest.param(_qs_text("qsWhy", "qsVie"), ["·Why.!half | ·Vie"], id="why-before-vie"),
    ],
)
def test_way_and_why_stay_full_before_right_base(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_may_thaw_joins_at_baseline_when_alone():
    _assert_expect_any(
        _qs_text("qsMay", "qsThaw"),
        [
            "·May ~b~ ·Thaw",
        ],
    )


def test_may_thaw_ing_is_sensible():
    _assert_expect_any(
        _qs_text("qsMay", "qsThaw", "qsIng"),
        [
            "·May.!ex-y0  |  ·Thaw ~b~ ·-ing",
            "·May                ~b~ ·Thaw  |  ·-ing",
        ],
    )


def test_may_thaw_stays_isolated_across_zwnj():
    chars = _char_map()
    text = chars["qsTea"] + ZWNJ + chars["qsMay"] + chars["qsThaw"] + chars["qsIng"]
    _assert_expect_any(
        text,
        [
            "·Tea | ◊ZWNJ | ·May.locked.!ex-y0 | ·Thaw ~b~ ·-ing",
        ],
    )


def test_may_thaw_before_ing_stays_plain():
    _assert_expect_any(
        _qs_text("qsMay", "qsThaw", "qsIng"),
        [
            "·May.∅ | ·Thaw ~b~ ·-ing",
        ],
    )


def test_no_does_not_take_alt_before_thaw_that_loses_entry():
    _assert_expect_any(
        _qs_text("qsNo", "qsThaw", "qsIng"),
        ["·No.!alt | ·Thaw ~b~ ·-ing"],
    )


def test_no_thaw_stays_non_alt_across_zwnj():
    chars = _char_map()
    text = chars["qsTea"] + ZWNJ + chars["qsNo"] + chars["qsThaw"] + chars["qsIng"]
    _assert_expect_any(
        text,
        [
            "·Tea | ◊ZWNJ | ·No.locked.!alt | ·Thaw ~b~ ·-ing",
        ],
    )


def test_excite_tea_connect_at_baseline():
    _assert_expect_any(_qs_text("qsExcite", "qsTea"), ["·Excite ~b~ ·Tea"])


@pytest.mark.parametrize(
    "text,expects",
    [
        pytest.param(
            _qs_text("qsShe", "qsExcite", "qsBay"),
            ["·She ~b~ ·Excite.!exit | ·Bay"],
            id="after-she",
        ),
        pytest.param(
            _qs_text("qsDay", "qsExcite", "qsBay"),
            ["·Day ~b~ ·Excite.!exit | ·Bay"],
            id="after-day",
        ),
    ],
)
def test_excite_reaches_left_only_before_xheight_entry_letters(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    "text,expects",
    [
        pytest.param(
            _qs_text("qsShe", "qsExcite", "qsTea"),
            ["·She ~b~ ·Excite ~b~ ·Tea"],
            id="after-she",
        ),
        pytest.param(
            _qs_text("qsDay", "qsExcite", "qsTea"),
            ["·Day ~b~ ·Excite ~b~ ·Tea"],
            id="after-day",
        ),
    ],
)
def test_excite_reaches_both_sides_when_neighbors_offer_baseline(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_excite_reaches_left_only_at_word_end_after_baseline_exit():
    _assert_expect_any(_qs_text("qsShe", "qsExcite"), ["·She ~b~ ·Excite.!exit"])


def test_excite_stays_mono_at_word_start_before_xheight_entry():
    _assert_expect_any(_qs_text("qsExcite", "qsBay"), ["·Excite.∅ | ·Bay.∅"])


def test_excite_reaches_left_only_before_thaw():
    _assert_expect_any(
        _qs_text("qsShe", "qsExcite", "qsThaw"),
        ["·She ~b~ ·Excite.!exit |?| ·Thaw"],
    )


def test_excite_thaw_does_not_leak_shape_across_break():
    """·Excite before ·Thaw must not leak shape across the non-join; ·Excite stays proportional and the pair breaks."""
    _assert_expect_any(
        _qs_text("qsExcite", "qsThaw"),
        ["·Excite | ·Thaw"],
    )


def test_it_excite_thaw_keeps_excite_proportional():
    """·It joins ·Excite at the baseline, and ·Excite stays proportional as it breaks before ·Thaw."""
    _assert_expect_any(
        _qs_text("qsIt", "qsExcite", "qsThaw"),
        ["·It ~b~ ·Excite | ·Thaw"],
    )


def test_it_excite_uses_the_visible_baseline_entry_shape():
    _assert_expect_any(
        _qs_text("qsIt", "qsExcite"),
        ["·It ~b~ ·Excite.!exit"],
    )


def test_pea_excite_excite_uses_the_visible_final_excite_entry_shape():
    _assert_expect_any(
        _qs_text("qsPea", "qsExcite", "qsExcite"),
        ["·Pea.∅ | ·Excite.!entry ~b~ ·Excite.!exit"],
    )


def test_excite_tea_does_not_keep_the_baseline_exit_before_ox():
    _assert_expect_any(
        _qs_text("qsExcite", "qsTea", "qsOx"),
        ["·Excite.∅ | ·Tea.half ~x~ ·Ox"],
    )


def test_excite_tea_does_not_keep_the_baseline_exit_before_oy():
    _assert_expect_any(_qs_text("qsTea", "qsOy"), ["·Tea+Oy"])
    _assert_expect_any(_qs_text("qsExcite", "qsTea", "qsOy"), ["·Excite.∅ | ·Tea+Oy"])


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsOut", "qsTea"), ["·Out+Tea"], id="pair"),
        pytest.param(_qs_text("qsOut", "qsTea", "qsRoe"), ["·Out+Tea | ·Roe"], id="before-roe"),
        pytest.param(_qs_text("qsOut", "qsTea", "qsDay"), ["·Out+Tea | ·Day"], id="before-day"),
    ],
)
def test_out_tea_prefers_the_ligature_before_nonjoining_followers(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_et_tea_keeps_the_tea_oy_ligature():
    _assert_expect_any(_qs_text("qsEt", "qsTea", "qsOy"), ["·Et.∅ | ·Tea+Oy"])


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsAh"),
            ["·Et ~b~ ·Tea | ·Ah.∅"],
            id="before-ah",
        ),
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsOut"),
            ["·Et ~b~ ·Tea | ·Out.∅"],
            id="before-out",
        ),
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsMay"),
            ["·Et ~b~ ·Tea | ·May.∅"],
            id="before-may",
        ),
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsIng"),
            ["·Et ~b~ ·Tea | ·-ing.∅"],
            id="before-ing",
        ),
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsVie"),
            ["·Et ~b~ ·Tea | ·Vie.∅"],
            id="before-vie",
        ),
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsDay"),
            ["·Et ~b~ ·Tea | ·Day.∅"],
            id="before-day",
        ),
    ],
)
def test_et_tea_keeps_only_the_left_baseline_join_in_plain_right_contexts(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_it_excite_does_not_force_tea_out_of_half_before_it():
    _assert_expect_any(
        _qs_text("qsIt", "qsExcite", "qsTea", "qsIt"),
        ["·It ~b~ ·Excite | ·Tea.half ~x~ ·It"],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsTea", "qsExcite"), ["·Tea |?| ·Excite"], id="tea-excite"),
        pytest.param(_qs_text("qsExam", "qsTea"), ["·Exam |?| ·Tea"], id="exam-tea"),
        pytest.param(_qs_text("qsTea", "qsExam"), ["·Tea |?| ·Exam"], id="tea-exam"),
        pytest.param(_qs_text("qsTea", "qsThaw"), ["·Tea |?| ·Thaw"], id="tea-thaw"),
        pytest.param(_qs_text("qsIt", "qsExam"), ["·It |?| ·Exam"], id="it-exam"),
    ],
)
def test_qs_nonjoining_pairs_keep_their_edges_separate(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_see_pea_keeps_the_y6_join():
    _assert_expect_any(
        _qs_text("qsSee", "qsPea"),
        ["·See ~6~ ·Pea"],
    )


def test_pea_pea_low_keeps_y6_then_baseline_joins():
    _assert_expect_any(
        _qs_text("qsPea", "qsPea", "qsLow"),
        ["·Pea.half ~6~ ·Pea ~b~ ·Low"],
    )


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_tea_before_i_extends_exit"])
def test_tea_before_i_extends_exit():
    _assert_expect_any(
        _qs_text("qsTea", "qsI"),
        ["·Tea.en-y8.ex-ext-1 ~b~ ·I"],
    )


def test_see_tea_i_extends_exit():
    _assert_expect_any(
        _qs_text("qsSee", "qsTea", "qsI"),
        ["·See ~t~ ·Tea.ex-ext-1 ~b~ ·I"],
    )


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_fee_tea_i_extends_exit"])
def test_fee_tea_i_extends_exit():
    _assert_expect_any(
        _qs_text("qsFee", "qsTea", "qsI"),
        ["·Fee.∅ |?| ·Tea.en-y8.ex-ext-1 ~b~ ·I"],
    )


def test_et_tea_i_preserves_left_only_invariant():
    _assert_expect_any(
        _qs_text("qsEt", "qsTea", "qsI"),
        ["·Et ~b~ ·Tea | ·I.∅"],
    )


def test_i_before_tea_unchanged_by_forward_extension():
    _assert_expect_any(_qs_text("qsI", "qsTea"), ["·I.∅ | ·Tea.!ex-ext-1"])


@pytest.mark.parametrize(
    ("target", "expect"),
    [
        pytest.param(
            "qsTea",
            "·Gay.ex-ext-1 ~b~ ·Tea",
            id="tea",
        ),
        pytest.param(
            "qsIt",
            "·Gay.ex-ext-1 ~b~ ·It",
            id="it",
        ),
        pytest.param(
            "qsI",
            "·Gay.ex-ext-1 ~b~ ·I",
            id="i",
        ),
        pytest.param(
            "qsExam",
            "·Gay.ex-ext-1 ~b~ ·Exam",
            id="exam",
        ),
    ],
)
def test_gay_extends_before_selected_targets(target: str, expect: str):
    _assert_expect_any(_qs_text("qsGay", target), [expect])


@pytest.mark.parametrize(
    ("target_base", "target_token"),
    [
        pytest.param("qsExcite", "·Excite", id="excite"),
        pytest.param("qsOoze", "·Ooze", id="ooze"),
    ],
)
def test_gay_nonjoining_targets_do_not_join(target_base, target_token):
    _assert_expect_any(
        _qs_text("qsGay", target_base),
        [f"·Gay.∅ | {target_token}.∅"],
    )


_JAI_XHEIGHT_LEFTS = [
    "qsFee",
    "qsMay",
    "qsNo",
    "qsRoe",
    "qsLow",
    "qsAt",
    "qsI",
    "qsAh",
    "qsUtter",
    "qsOut",
    "qsFoot",
]


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text(left_base, "qsJai"),
            [f"·{_family_to_label(left_base)} ~x~ ·J’ai"],
            id=_family_to_label(left_base).lower(),
        )
        for left_base in _JAI_XHEIGHT_LEFTS
    ],
)
def test_jai_joins_designated_left_letters_at_xheight(text: str, expects: list[str]) -> None:
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text(left_base, "qsJai", "qsUtter"),
            [f"·{_family_to_label(left_base)} ~x~ ·J’ai+Utter"],
            id=_family_to_label(left_base).lower(),
        )
        for left_base in _JAI_XHEIGHT_LEFTS
    ],
)
def test_jai_utter_ligature_joins_designated_left_letters_at_xheight(text: str, expects: list[str]) -> None:
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsAh", "qsJay", "qsUtter"), ["·Ah ~x~ ·Jay+Utter"], id="ah"),
        pytest.param(_qs_text("qsNo", "qsJay", "qsUtter"), ["·No ~x~ ·Jay+Utter"], id="no"),
    ],
)
def test_jay_utter_ligature_joins_designated_left_letters_at_xheight(text: str, expects: list[str]) -> None:
    """·Ah and ·No connect to the ·Jay+Utter ligature at the x-height just as they do to bare ·Jay."""
    _assert_expect_any(text, expects)


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_jay_utter_contracts_before_jai"])
def test_jay_utter_contracts_before_jai():
    """·Jay+Utter contracts its exit (ex-con-2) before ·J’ai and joins at the x-height."""
    _assert_expect_any(
        _qs_text("qsAh", "qsJay", "qsUtter", "qsJai"),
        ["·Ah ~x~ ·Jay+Utter.ex-con-2 ~x~ ·J’ai"],
    )


def test_jay_utter_extends_before_fee():
    """·Jay+Utter extends its exit (ex-ext-1) before ·Fee, just as bare ·Utter does."""
    _assert_expect_any(
        _qs_text("qsAh", "qsJay", "qsUtter", "qsFee"),
        ["·Ah ~x~ ·Jay+Utter.ex-ext-1 ~x~ ·Fee"],
    )


def test_roe_stays_bare_before_at_may():
    _assert_expect_any(
        _qs_text("qsRoe", "qsAt", "qsMay"),
        ["·Roe.∅ | ·At ~b~ ·May"],
    )


def test_see_stays_bare_before_at_may():
    """·See stays unjoined before ·At·May, as ·Roe does, because the ·At ``before-may`` stance has no entry."""
    _assert_expect_any(
        _qs_text("qsSee", "qsAt", "qsMay"),
        ["·See | ·At ~b~ ·May"],
    )


def test_roe_ah_connects_at_baseline():
    _assert_expect_any(
        _qs_text("qsRoe", "qsAh"),
        ["·Roe ~b~ ·Ah"],
    )


def test_eat_roe_uses_shortened_top_by_default():
    """·Eat exits at the baseline, so ·Eat·Roe takes ·Roe's ``shortened_top`` shape: its baseline entry extended a pixel (``en-y0.en-ext-1``)."""
    _assert_expect_any(
        _qs_text("qsEat", "qsRoe"),
        ["·Eat ~b~ ·Roe.en-y0.en-ext-1"],
    )


def test_eat_roe_stays_shortened_top_before_non_xheight_follower():
    """Before ·Ah, which does not enter at the x-height, ·Roe keeps its ``shortened_top`` shape, takes no x-height exit, and does not join ·Ah."""
    _assert_expect_any(
        _qs_text("qsEat", "qsRoe", "qsAh"),
        ["·Eat ~b~ ·Roe.en-y0.en-ext-1 | ·Ah"],
    )


@pytest.mark.parametrize(
    "follower, expect",
    [
        pytest.param("qsNo", "·Eat ~b~ ·Roe.ex-y5 ~x~ ·No", id="no"),
        pytest.param("qsEight", "·Eat ~b~ ·Roe.ex-y5 ~x~ ·Eight", id="eight"),
    ],
)
def test_eat_roe_kicks_up_to_xheight_before_canonical_follower(follower: str, expect: str):
    """Before a letter it joins at the x-height, ·Roe takes its ``giga_extended_short_height`` shape (``ex-y5``) instead of ``shortened_top`` and joins the follower."""
    _assert_expect_any(_qs_text("qsEat", "qsRoe", follower), [expect])


def test_roe_may_they_utter_looks_ok():
    _assert_expect_any(
        _qs_text("qsRoe", "qsMay", "qsThey", "qsUtter"),
        ["·Roe ~b~ ·May |?| ·They+Utter.!entry"],
    )


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_at_may_they_utter_looks_ok"])
def test_at_may_they_utter_looks_ok():
    _assert_expect_any(
        _qs_text("qsAt", "qsMay", "qsThey", "qsUtter"),
        ["·At.ex-ext-5 ~b~ ·May |?| ·They+Utter.!entry"],
    )


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsWhy", "qsIt", "qsCheer"), ["·Why ~x~ ·It | ·Cheer"], id="which"),
        pytest.param(_qs_text("qsWay", "qsIt", "qsCheer"), ["·Way ~x~ ·It | ·Cheer"], id="witch"),
    ],
)
def test_letter_before_it_cheer_joins_then_breaks(text: str, expects: list[str]):
    """·Why/·Way join ·It at the x-height, then ·It breaks cleanly before ·Cheer ("which", "witch")."""
    _assert_expect_any(text, expects)


def test_tea_foot_key_stays_unjoined():
    """The word "took": ·Tea, ·Foot, and ·Key do not join."""
    _assert_expect_any(
        _qs_text("qsTea", "qsFoot", "qsKey"),
        ["·Tea | ·Foot | ·Key"],
    )


@pytest.mark.xfail(strict=True, reason=REBUILD_SHAPES["test_he_vie_utter_day_joins_baseline_then_xheight"])
def test_he_vie_utter_day_joins_baseline_then_xheight():
    """The word "Hvəd": ·He joins the ·Vie+Utter ligature at the baseline, and the ligature's extended exit reaches ·Day at the x-height."""
    _assert_expect_any(
        _qs_text("qsHe", "qsVie", "qsUtter", "qsDay"),
        ["·He ~b~ ·Vie+Utter.en-ext-1 ~x~ ·Day"],
    )


def test_at_pea_half_eight_uses_xheight_half_pea():
    """·At breaks before ·Pea, which takes its x-height half stance to join ·Eight."""
    _assert_expect_any(
        _qs_text("qsAt", "qsPea", "qsEight"),
        ["·At | ·Pea.half ~x~ ·Eight"],
    )
