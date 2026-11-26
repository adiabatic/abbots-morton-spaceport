"""Regression checks on the Senior's shaping that no `data-expect` pin and no any-of check in test/test_calt_expectations.py states: pairs the runes keep apart in every context, joins that hold in every context, and stylistic-set behavior. Most sweeps shape the letters with one symbol of context on each side, drawn from the plain Quikscript letters, a space and a ZWNJ; every check reads joins from the font's `curs` lookups and glyph facts from the sidecar (`engine/validation/check.py`). The root conftest publishes the fonts into site/ at the start of the session (`pin_fonts`)."""

from collections.abc import Callable, Iterator, Sequence
from functools import cache

import pytest
from fontTools.ttLib import TTFont

from engine.validation.check import CheckFont, assert_expect_any, parse_glyph_name
from engine.validation.expect import CHAR_TO_GLYPH
from engine.validation.shaping import ShapeResult, first_glyph_covering, last_glyph_covering

ZWNJ = "\u200c"
_CHARS = {name: char for char, name in CHAR_TO_GLYPH.items()}
_LETTERS = tuple(
    sorted(
        name
        for name in _CHARS
        if name.startswith("qs")
        and "_" not in name
        and "." not in name
        and name not in {"qsAngleParenLeft", "qsAngleParenRight"}
    )
)
# The symbols a sweep draws its context from: every plain letter, a space and a ZWNJ.
_CONTEXT = tuple(_CHARS[name] for name in _LETTERS) + (" ", ZWNJ)
_FONT: dict[str, CheckFont] = {}


@pytest.fixture(autouse=True, scope="module")
def _senior(pin_fonts: dict[str, CheckFont]) -> None:
    _FONT["senior"] = pin_fonts["senior"]


def _qs_text(*parts: str) -> str:
    """Spell family names (`qsMay`) and the two boundaries (`space`, `ZWNJ`) as text."""
    return "".join(" " if part == "space" else ZWNJ if part == "ZWNJ" else _CHARS[part] for part in parts)


def _heights(junction: str) -> set[int]:
    if not junction.startswith("y"):
        return set()
    return {int(part[1:]) for part in junction.split("+")}


def _shape(text: str, features: dict[str, bool] | None = None) -> tuple[ShapeResult, tuple[str, ...]]:
    font = _FONT["senior"]
    result = font.shape(text, features)
    return result, font.junctions(result, len(text))


def _left_glyph(result: ShapeResult, k: int) -> str:
    """The glyph that holds character `k`, as the left side of the junction after it."""
    return result.names[last_glyph_covering(result.clusters, k)]


def _right_glyph(result: ShapeResult, k: int) -> str:
    """The glyph that holds character `k`, as the right side of the junction before it."""
    return result.names[first_glyph_covering(result.clusters, k)]


def _base(glyph: str) -> str:
    return parse_glyph_name(glyph).base


def _traits(glyph: str) -> frozenset[str]:
    facts = _FONT["senior"].glyph_facts(glyph)
    return facts.traits if facts is not None else frozenset()


def _describe(text: str, result: ShapeResult, junctions: tuple[str, ...]) -> str:
    return f"{text!r}: {list(result.names)} {list(junctions)}"


def _texts(
    core: str, before: Sequence[str] = ("", *_CONTEXT), after: Sequence[str] = ("", *_CONTEXT)
) -> Iterator[str]:
    for left in before:
        for right in after:
            yield left + core + right


def _sweep_pair(
    left: str,
    right: str,
    check: Callable[[str, int, ShapeResult, tuple[str, ...]], str | None],
    **contexts: Sequence[str],
) -> list[str]:
    """Shape `left` `right` in every context and return a failure for each place the two letters meet where `check` returns one."""
    pair = _CHARS[left] + _CHARS[right]
    failures: list[str] = []
    for text in _texts(pair, **contexts):
        result, junctions = _shape(text)
        for k in range(len(text) - 1):
            if text[k] == pair[0] and text[k + 1] == pair[1]:
                error = check(text, k, result, junctions)
                if error:
                    failures.append(f"{error}: {_describe(text, result, junctions)}")
    return failures


def _label(name: str) -> str:
    return "·-ing" if name == "qsIng" else f"·{name[2:]}"


@cache
def _glyph_bases() -> frozenset[str]:
    return frozenset(name.split(".")[0] for name in TTFont(str(_FONT["senior"].path)).getGlyphOrder())


def _ligates(left: str, right: str) -> bool:
    """Whether the font has a ligature of `left` then `right`, read from its glyph names."""
    return f"{left}_{right}" in _glyph_bases()


def _either(left: str, right: str, ligated: str, apart: str) -> str:
    """The expect for text where `left` and `right` may form a ligature (`+?`), or the one for text where they cannot."""
    return ligated if _ligates(left, right) else apart


def _assert_none(failures: list[str]) -> None:
    assert not failures, f"{len(failures)} failures:\n" + "\n".join(failures[:20])


_NEVER_JOINING_PAIRS = (
    ("qsPea", "qsOwe"),
    ("qsTea", "qsThaw"),
    ("qsTea", "qsCheer"),
    ("qsTea", "qsExcite"),
    ("qsTea", "qsExam"),
    ("qsTea", "qsOwe"),
    ("qsGay", "qsExcite"),
    ("qsThey", "qsJay"),
    ("qsShe", "qsThaw"),
    ("qsJay", "qsHe"),
    ("qsYe", "qsOwe"),
    ("qsWay", "qsTea"),
    ("qsWay", "qsThaw"),
    ("qsWay", "qsVie"),
    ("qsWay", "qsSee"),
    ("qsHe", "qsOwe"),
    ("qsWhy", "qsTea"),
    ("qsWhy", "qsThaw"),
    ("qsWhy", "qsVie"),
    ("qsWhy", "qsSee"),
    ("qsExam", "qsTea"),
    ("qsIt", "qsExam"),
    ("qsIt", "qsIt"),
    ("qsEat", "qsYe"),
    ("qsOwe", "qsTea"),
    ("qsOwe", "qsDay"),
)


@pytest.mark.parametrize(("left", "right"), _NEVER_JOINING_PAIRS, ids=lambda name: name[2:])
def test_pair_never_joins_in_any_context(left: str, right: str):
    """The two letters never join, whatever letter, space or ZWNJ stands on either side of them."""
    _assert_none(
        _sweep_pair(
            left, right, lambda text, k, result, junctions: junctions[k] if _heights(junctions[k]) else None
        )
    )


@pytest.mark.parametrize(
    ("left", "right", "height"),
    [("qsIt", "qsDay", 5), ("qsYe", "qsIt", 0)],
    ids=["it-day-xheight", "ye-it-baseline"],
)
def test_pair_never_joins_at_one_height_in_any_context(left: str, right: str, height: int):
    """The two letters never join at this height, whatever stands on either side of them."""
    _assert_none(
        _sweep_pair(
            left,
            right,
            lambda text, k, result, junctions: junctions[k] if height in _heights(junctions[k]) else None,
        )
    )


@pytest.mark.parametrize("left", ["qsMay", "qsNo", "qsFoot"], ids=lambda name: name[2:])
def test_may_no_and_foot_never_join_they_utter(left: str):
    """·May, ·No and ·Foot never join a following ·They+Utter, whatever letter stands on either side."""
    plain = ("", *(_CHARS[name] for name in _LETTERS))
    failures = []
    for outer_left in plain:
        for outer_right in plain:
            text = outer_left + _qs_text(left, "qsThey", "qsUtter") + outer_right
            result, junctions = _shape(text)
            k = len(outer_left)
            if _base(_right_glyph(result, k + 1)) != "qsThey_qsUtter":
                continue
            if _heights(junctions[k]):
                failures.append(_describe(text, result, junctions))
    _assert_none(failures)


def test_nothing_joins_at_before_may():
    """No letter joins an ·At that a ·May follows."""
    failures = []
    for name in _LETTERS:
        text = _qs_text(name, "qsAt", "qsMay")
        result, junctions = _shape(text)
        if _heights(junctions[0]):
            failures.append(_describe(text, result, junctions))
    _assert_none(failures)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("qsJay", "qsYe"),
        ("qsJai", "qsYe"),
        ("qsYe", "qsI"),
        ("qsIt", "qsCheer"),
        ("qsIt", "qsJai"),
        ("qsIt", "qsI"),
        ("qsIt", "qsOwe"),
    ],
    ids=lambda name: name[2:],
)
def test_joined_pair_is_one_pixel_longer(left: str, right: str):
    """Wherever the two letters join, the join carries one pixel of extension between them, whatever stands on either side."""

    def check(text: str, k: int, result: ShapeResult, junctions: tuple[str, ...]) -> str | None:
        if not _heights(junctions[k]):
            return None
        pixels = sum(
            int(part.rsplit("-", 1)[1])
            for part in parse_glyph_name(_left_glyph(result, k)).adjustments
            if part.startswith("ex-ext-")
        )
        pixels += sum(
            int(part.rsplit("-", 1)[1])
            for part in parse_glyph_name(_right_glyph(result, k + 1)).adjustments
            if part.startswith("en-ext-")
        )
        return None if pixels == 1 else f"{pixels} pixels of extension"

    _assert_none(_sweep_pair(left, right, check))


def test_it_joins_half_day_at_the_baseline():
    """·It joins a following ·Day only through the half ·Day at the baseline, and always does so when nothing joins ·It at the baseline from the left."""

    def check(text: str, k: int, result: ShapeResult, junctions: tuple[str, ...]) -> str | None:
        it, day = _left_glyph(result, k), _right_glyph(result, k + 1)
        if _base(it) != "qsIt" or _base(day) != "qsDay":
            return None
        joined = _heights(junctions[k])
        if joined and (joined != {0} or "half" not in _traits(day)):
            return "joined other than to the half ·Day at the baseline"
        if not joined and not (k > 0 and 0 in _heights(junctions[k - 1])):
            return "unjoined though ·It is free at the baseline"
        return None

    _assert_none(_sweep_pair("qsIt", "qsDay", check))


def test_utter_joins_gay_at_the_height_its_stance_leaves_from():
    """·Utter, alone or ending a ligature, always joins a following ·Gay: at the baseline from the alternate ·Utter and at the x-height otherwise."""

    def check(text: str, k: int, result: ShapeResult, junctions: tuple[str, ...]) -> str | None:
        utter, gay = _left_glyph(result, k), _right_glyph(result, k + 1)
        if not (_base(utter) == "qsUtter" or _base(utter).endswith("_qsUtter")) or _base(gay) != "qsGay":
            return "·Utter or ·Gay is inside another ligature"
        height = 0 if "alt" in _traits(utter) else 5
        return None if height in _heights(junctions[k]) else f"expected a join at y{height}"

    _assert_none(_sweep_pair("qsUtter", "qsGay", check))


def test_middle_pea_joins_left_at_the_xheight_only_after_may_and_utter():
    """Where ·Pea joins the next letter at the x-height, it also joins the letter before at the x-height exactly when that letter is ·May or ·Utter."""
    failures, seen = [], set()
    for left in _LETTERS:
        for right in _LETTERS:
            text = _qs_text(left, "qsPea", right)
            result, junctions = _shape(text)
            if 5 not in _heights(junctions[1]) or "lig" in junctions:
                continue
            joined_left = 5 in _heights(junctions[0])
            if left in ("qsMay", "qsUtter"):
                seen.add(left)
            if joined_left != (left in ("qsMay", "qsUtter")):
                failures.append(_describe(text, result, junctions))
    assert seen == {"qsMay", "qsUtter"}
    _assert_none(failures)


def test_baseline_join_into_no_takes_the_alternate_no():
    """A letter that joins ·No at the baseline always meets the alternate ·No."""
    failures = []
    for left in _LETTERS:
        for right in _LETTERS:
            text = _qs_text(left, "qsNo", right)
            result, junctions = _shape(text)
            no = _right_glyph(result, 1)
            if _base(no) == "qsNo" and 0 in _heights(junctions[0]) and "alt" not in _traits(no):
                failures.append(_describe(text, result, junctions))
    _assert_none(failures)


# The letters before which ·Way and ·Why keep their full stance.
_FULL_BEFORE = {
    "qsWay": (
        "qsDay",
        "qsEat",
        "qsExcite",
        "qsIt",
        "qsNo",
        "qsOoze",
        "qsPea",
        "qsSee",
        "qsTea",
        "qsThaw",
        "qsUtter",
        "qsVie",
        "qsYe",
        "qsZoo",
    ),
    "qsWhy": (
        "qsAh",
        "qsAt",
        "qsEat",
        "qsExam",
        "qsExcite",
        "qsI",
        "qsIng",
        "qsIt",
        "qsLow",
        "qsMay",
        "qsOoze",
        "qsOut",
        "qsOwe",
        "qsSee",
        "qsTea",
        "qsThaw",
        "qsUtter",
        "qsVie",
    ),
}


@pytest.mark.parametrize("left", ["qsWay", "qsWhy"], ids=lambda name: name[2:])
def test_way_and_why_stay_full_before_letters_their_half_does_not_reach(left: str):
    """·Way and ·Why draw their full stance before each of these letters, whatever letter stands on either side."""
    plain = ("", *(_CHARS[name] for name in _LETTERS))

    def check(text: str, k: int, result: ShapeResult, junctions: tuple[str, ...]) -> str | None:
        glyph = _left_glyph(result, k)
        return "half" if _base(glyph) == left and "half" in _traits(glyph) else None

    failures = []
    for right in _FULL_BEFORE[left]:
        failures += _sweep_pair(left, right, check, before=plain, after=plain)
    _assert_none(failures)


@pytest.mark.parametrize("left", ["qsWay", "qsWhy"], ids=lambda name: name[2:])
def test_half_way_and_why_never_meet_the_alternate_utter(left: str):
    """A half ·Way or ·Why never stands right before an alternate ·Utter."""

    def check(text: str, k: int, result: ShapeResult, junctions: tuple[str, ...]) -> str | None:
        if junctions[k] == "lig":
            return None
        if "half" in _traits(_left_glyph(result, k)) and "alt" in _traits(_right_glyph(result, k + 1)):
            return "half before alternate"
        return None

    _assert_none(_sweep_pair(left, "qsUtter", check))


@pytest.mark.parametrize(
    ("left", "right"),
    [("qsWay", "qsVie"), ("qsWay", "qsSee"), ("qsWhy", "qsVie"), ("qsWhy", "qsSee")],
    ids=lambda name: name[2:],
)
def test_vie_and_see_shape_the_same_after_way_and_why(left: str, right: str):
    """·Vie and ·See draw and sit the same after ·Way or ·Why as they do without it, before any letter or none."""
    font = _FONT["senior"]
    unkerned = {"kern": False}
    failures = []
    for follower in ("", *(_CHARS[name] for name in _LETTERS)):
        with_left = font.shape(_CHARS[left] + _CHARS[right] + follower, unkerned)
        alone = font.shape(_CHARS[right] + follower, unkerned)
        got = [
            (font.outline(name), pos)
            for name, cluster, pos in zip(with_left.names, with_left.clusters, with_left.positions)
            if cluster == 1
        ]
        want = [
            (font.outline(name), pos)
            for name, cluster, pos in zip(alone.names, alone.clusters, alone.positions)
            if cluster == 0
        ]
        if got != want:
            failures.append(f"{follower!r}: {list(with_left.names)} against {list(alone.names)}")
    _assert_none(failures)


def test_she_draws_its_isolated_shape_before_thaw():
    """·She before ·Thaw draws the shape it has standing alone, whatever stands on either side."""
    font = _FONT["senior"]
    isolated = font.outline(font.shape(_CHARS["qsShe"]).names[0])
    _assert_none(
        _sweep_pair(
            "qsShe",
            "qsThaw",
            lambda text, k, result, junctions: (
                None if font.outline(_left_glyph(result, k)) == isolated else "not the isolated ·She"
            ),
        )
    )


@pytest.mark.parametrize("center", ["qsHe", "qsYe"], ids=lambda name: name[2:])
def test_he_and_ye_join_at_the_baseline_on_one_side_at_most(center: str):
    """·He and ·Ye never join both neighbors at the baseline, and where each neighbor alone would take a baseline join, the following one gets it."""
    char = _CHARS[center]
    back = {name: 0 in _heights(_shape(_CHARS[name] + char)[1][0]) for name in _LETTERS}
    forward = {name: 0 in _heights(_shape(char + _CHARS[name])[1][0]) for name in _LETTERS}
    failures = []
    for left in (None, *_LETTERS):
        for right in (None, *_LETTERS):
            text = (_CHARS[left] if left else "") + char + (_CHARS[right] if right else "")
            result, junctions = _shape(text)
            k = 1 if left else 0
            joins_back = left is not None and 0 in _heights(junctions[k - 1])
            joins_forward = right is not None and 0 in _heights(junctions[k])
            if joins_back and joins_forward:
                failures.append("both sides: " + _describe(text, result, junctions))
            if left and right and back[left] and forward[right] and not (joins_forward and not joins_back):
                failures.append("not the follower: " + _describe(text, result, junctions))
    _assert_none(failures)


@pytest.mark.parametrize(
    ("middle", "height"),
    [("qsIt", 5), ("qsIt", 0), ("qsPea", 0)],
    ids=["it-xheight", "it-baseline", "pea-baseline"],
)
def test_letter_never_joins_both_neighbors_at_one_height(middle: str, height: int):
    """The letter never joins the letters on both sides of it at this height."""
    char = _CHARS[middle]
    failures = []
    for text in _texts(char):
        result, junctions = _shape(text)
        for k in range(1, len(text) - 1):
            if text[k] == char and height in _heights(junctions[k - 1]) and height in _heights(junctions[k]):
                failures.append(_describe(text, result, junctions))
    _assert_none(failures)


_GAY_NEIGHBORS = (
    "qsPea",
    "qsBay",
    "qsTea",
    "qsDay",
    "qsKey",
    "qsFee",
    "qsVie",
    "qsSee",
    "qsZoo",
    "qsShe",
    "qsMay",
    "qsNo",
    "qsLow",
    "qsRoe",
    "qsEat",
    "qsAt",
    "qsAh",
    "qsOx",
    "qsOwe",
    "qsOoze",
)


@pytest.mark.parametrize("target", ["qsIt", "qsI", "qsExam"], ids=lambda name: name[2:])
def test_gay_joins_it_i_and_exam_at_the_baseline_one_pixel_longer(target: str):
    """After each of these letters ·Gay joins ·It, ·I or ·Exam at the baseline with its exit a pixel longer, and wherever ·Gay joins one of them before another letter it joins the same way."""
    failures = []
    for neighbor in _GAY_NEIGHBORS:
        for parts, k, required in (
            ((neighbor, "qsGay", target), 1, True),
            (("qsGay", target, neighbor), 0, False),
        ):
            text = _qs_text(*parts)
            result, junctions = _shape(text)
            if not _heights(junctions[k]) and not required:
                continue
            if junctions[k] != "y0" or "ex-ext-1" not in parse_glyph_name(_left_glyph(result, k)).adjustments:
                failures.append(_describe(text, result, junctions))
    _assert_none(failures)


def test_unjoined_left_letter_leaves_excite_joined_to_the_right():
    """Where a letter does not join ·Excite and ·Excite joins the next letter, putting the three together keeps both."""
    excite = _CHARS["qsExcite"]
    joins_right = {}
    for name in _LETTERS:
        result, junctions = _shape(excite + _CHARS[name])
        if len(result.names) == 2 and _heights(junctions[0]):
            joins_right[name] = _heights(junctions[0])
    unjoined_left = [name for name in _LETTERS if _shape(_CHARS[name] + excite)[1][0] == "break"]
    failures = []
    for left in unjoined_left:
        for right, heights in joins_right.items():
            text = _CHARS[left] + excite + _CHARS[right]
            result, junctions = _shape(text)
            if len(result.names) != 3:
                continue
            if _heights(junctions[0]) or not heights <= _heights(junctions[1]):
                failures.append(_describe(text, result, junctions))
    _assert_none(failures)


def test_out_tea_never_joins_onward_unligated():
    """Before any letter, ·Out·Tea either forms ·Out+Tea or leaves ·Tea unjoined to that letter."""
    failures = []
    for name in _LETTERS:
        text = _qs_text("qsOut", "qsTea", name)
        result, junctions = _shape(text)
        if junctions[0] != "lig" and _heights(junctions[1]):
            failures.append(_describe(text, result, junctions))
    _assert_none(failures)


def test_et_tea_joins_left_and_breaks_before_any_letter():
    """·Et joins ·Tea at the baseline and ·Tea breaks before any letter after it but ·Oy, which shapes there as it does alone."""
    failures = []
    for name in _LETTERS:
        if name == "qsOy":
            continue
        expect = f"·Et ~b~ ·Tea | {_label(name)}"
        try:
            assert_expect_any(_FONT["senior"], _qs_text("qsEt", "qsTea", name), [expect])
        except AssertionError as error:
            failures.append(f"{expect!r}: {str(error).splitlines()[1]}")
    _assert_none(failures)


def test_excite_keeps_its_tea_join_before_a_second_tea():
    """In ·Excite·Tea·Tea, ·Excite joins the first ·Tea at the baseline and the second ·Tea stands apart."""
    assert_expect_any(_FONT["senior"], _qs_text("qsExcite", "qsTea", "qsTea"), ["·Excite ~b~ ·Tea | ·Tea"])


def test_excite_lets_go_of_tea_when_tea_joins_out():
    """In ·Excite·Tea·Out, ·Tea joins ·Out at the baseline and ·Excite, unjoined, draws no exit."""
    assert_expect_any(
        _FONT["senior"], _qs_text("qsExcite", "qsTea", "qsOut"), ["·Excite.!exit | ·Tea ~b~ ·Out"]
    )


@pytest.mark.parametrize(
    ("parts", "expect"),
    [
        (("qsSee", "qsOut"), "·See ~b~ ·Out.!exit"),
        (("qsSee", "qsOut", "qsOy"), "·See ~b~ ·Out.!extended ~x~ ·Oy"),
        (("qsSee", "qsOut", "qsFee"), "·See ~b~ ·Out.ex-ext-1 ~x~ ·Fee"),
    ],
    ids=["word-end", "before-oy", "before-fee"],
)
def test_see_out_reaches_fee_a_pixel_farther(parts: tuple[str, ...], expect: str):
    """After ·See, ·Out leaves at the x-height only toward a letter that joins it there, and reaches a pixel farther toward ·Fee than toward ·Oy."""
    assert_expect_any(_FONT["senior"], _qs_text(*parts), [expect])


def test_way_does_not_join_tea_under_ss03():
    """·Way and ·Tea stay apart with ss03 on."""
    assert_expect_any(_FONT["senior"], _qs_text("qsWay", "qsTea"), ["·Way | ·Tea"], {"ss03": True})


def test_et_tea_i_extends_tea_under_ss05():
    """With ss05 on, ·Tea joins ·Et and ·I at the baseline, its exit a pixel longer."""
    assert_expect_any(
        _FONT["senior"], _qs_text("qsEt", "qsTea", "qsI"), ["·Et ~b~ ·Tea.ex-ext-1 ~b~ ·I"], {"ss05": True}
    )


@pytest.mark.parametrize(
    ("parts", "expect"),
    [
        (("qsOwe", "qsDay"), "·Owe ~x~ ·Day"),
        (("qsOwe", "qsDay", "qsUtter"), "·Owe ~x~ ·Day+Utter"),
        (("qsOwe", "qsDay", "qsEat"), "·Owe ~x~ ·Day+Eat"),
    ],
    ids=["day", "day-utter", "day-eat"],
)
def test_owe_joins_day_and_its_ligatures_under_ss07(parts: tuple[str, ...], expect: str):
    """With ss07 on, ·Owe joins ·Day, ·Day+Utter and ·Day+Eat at the x-height."""
    assert_expect_any(_FONT["senior"], _qs_text(*parts), [expect], {"ss07": True})


def test_owe_joins_day_under_ss07_beside_any_letter():
    """With ss07 on, ·Owe joins a following ·Day, or the ligature it starts, at the x-height whatever letter comes before ·Owe or after ·Day."""
    failures = []
    owe_day = _qs_text("qsOwe", "qsDay")
    for name in _LETTERS:
        for text in (owe_day + _CHARS[name], _CHARS[name] + owe_day):
            result, junctions = _shape(text, {"ss07": True})
            k = text.index(owe_day)
            if 5 not in _heights(junctions[k]):
                failures.append(_describe(text, result, junctions))
    _assert_none(failures)


def test_ss06_regaps_only_an_owe_that_joins_neither_neighbor():
    """ss06 swaps an ·Owe that joins neither neighbor for the gapped ·Owe and changes nothing else, with or without ss07."""
    font = _FONT["senior"]
    owe = _CHARS["qsOwe"]
    # The contexts are nothing, a space, a ZWNJ, the namer dot and each letter.
    contexts = ("", " ", ZWNJ, "\u00b7", *(_CHARS[name] for name in _LETTERS))
    failures = []
    for base in ({}, {"ss07": True}):
        for left in contexts:
            for right in contexts:
                text = left + owe + right
                plain = font.shape(text, base or None).names
                regapped = font.shape(text, {**base, "ss06": True}).names
                expected_ok = len(plain) == len(regapped)
                for before, after in zip(plain, regapped):
                    glyph = parse_glyph_name(before)
                    if glyph.base == "qsOwe" and glyph.entry is None and glyph.exit is None:
                        expected_ok &= (
                            parse_glyph_name(after).base == "qsOwe"
                            and "ss06" in parse_glyph_name(after).features
                        )
                    else:
                        expected_ok &= before == after
                if not expected_ok:
                    failures.append(f"{base} {text!r}: {list(plain)} then {list(regapped)}")
    _assert_none(failures)


# Each pair's expect alone, with a letter `L` before it, and with a letter `R` after it, where the letter may join the pair or form a ligature with its neighbor in it.
_FAMILIES: dict[str, tuple[str, str, Callable[[str], str], Callable[[str], str]]] = {
    "tea-tea": (
        "qsTea qsTea",
        "·Tea.!half ? ·Tea.!half",
        lambda L: _either(
            L, "qsTea", f"{_label(L)}+?Tea ? ·Tea.!half", f"{_label(L)} ? ·Tea.!half ? ·Tea.!half"
        ),
        lambda R: _either(
            "qsTea", R, f"·Tea.!half ? ·Tea+?{_label(R)[1:]}", f"·Tea.!half ? ·Tea ? {_label(R)}"
        ),
    ),
    "tea-cheer": (
        "qsTea qsCheer",
        "·Tea.!half | ·Cheer",
        lambda L: _either(L, "qsTea", f"{_label(L)}+?Tea | ·Cheer", f"{_label(L)} ? ·Tea | ·Cheer"),
        lambda R: f"·Tea.!half | ·Cheer ? {_label(R)}",
    ),
    "he-day": (
        "qsHe qsDay",
        "·He.!half ~b~ ·Day.half",
        lambda L: f"{_label(L)} ? ·He.!half ~b~ ·Day.half",
        lambda R: _either(
            "qsDay", R, f"·He.!half ~b~ ·Day+?{_label(R)[1:]}.half", f"·He.!half ~b~ ·Day.half ? {_label(R)}"
        ),
    ),
    "way-day": (
        "qsWay qsDay",
        "·Way.!half ~x~ ·Day.!half",
        lambda L: f"{_label(L)} ? ·Way.!half ~x~ ·Day.!half",
        lambda R: _either(
            "qsDay", R, f"·Way.!half ? ·Day+?{_label(R)[1:]}", f"·Way.!half ~x~ ·Day.!half ? {_label(R)}"
        ),
    ),
    "way-thaw": (
        "qsWay qsThaw",
        "·Way.!half | ·Thaw",
        lambda L: f"{_label(L)} ? ·Way.!half | ·Thaw",
        lambda R: f"·Way.!half | ·Thaw ? {_label(R)}",
    ),
    "owe-day": (
        "qsOwe qsDay",
        "·Owe | ·Day",
        lambda L: f"{_label(L)} ? ·Owe | ·Day",
        lambda R: _either("qsDay", R, f"·Owe | ·Day+?{_label(R)[1:]}", f"·Owe | ·Day ? {_label(R)}"),
    ),
    # ·Utter leaves at the x-height, where ·They takes it.
    "they-jay": (
        "qsThey qsJay",
        "·They | ·Jay",
        lambda L: f"{_label(L)} {'~x~' if L == 'qsUtter' else '?'} ·They | ·Jay",
        lambda R: _either("qsJay", R, f"·They | ·Jay+?{_label(R)[1:]}", f"·They | ·Jay ? {_label(R)}"),
    ),
}


@pytest.mark.parametrize("family", list(_FAMILIES))
def test_pair_keeps_its_shapes_beside_any_letter(family: str):
    """The pair keeps its stances and its join or break with any letter before or after it: never two half ·Teas in a row, ·Tea·Cheer, ·Way·Thaw, ·Owe·Day and ·They·Jay apart, ·He joined to the half ·Day at the baseline, and the full ·Way joined to the full ·Day at the x-height."""
    pair, alone, before, after = _FAMILIES[family]
    letters = pair.split()
    cases = [(_qs_text(*letters), alone)]
    cases += [(_qs_text(name, *letters), before(name)) for name in _LETTERS]
    cases += [(_qs_text(*letters, name), after(name)) for name in _LETTERS]
    failures = []
    for text, expect in cases:
        try:
            assert_expect_any(_FONT["senior"], text, [expect])
        except AssertionError as error:
            failures.append(f"{expect!r}: {str(error).splitlines()[1]}")
    _assert_none(failures)
