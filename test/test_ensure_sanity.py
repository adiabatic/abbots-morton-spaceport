"""Sanity-check shaping invariants for selected pair contexts.

Each test enumerates every Quikscript letter as a left or right neighbour
of a target pair (e.g. ·Tea·Tea, ·Way·Day) and asserts the expected
glyph variants and connection types via run_shaping_test_runs.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
# noqa E402: must follow sys.path tweak above
from build_font import load_glyph_data  # noqa: E402

from test_shaping import run_shaping_test_runs  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent

LETTERS: list[tuple[str, int]] = [
    ("Pea", 0xE650),
    ("Bay", 0xE651),
    ("Tea", 0xE652),
    ("Day", 0xE653),
    ("Key", 0xE654),
    ("Gay", 0xE655),
    ("Thaw", 0xE656),
    ("They", 0xE657),
    ("Fee", 0xE658),
    ("Vie", 0xE659),
    ("See", 0xE65A),
    ("Zoo", 0xE65B),
    ("She", 0xE65C),
    ("Jai", 0xE65D),
    ("Cheer", 0xE65E),
    ("Jay", 0xE65F),
    ("Ye", 0xE660),
    ("Way", 0xE661),
    ("He", 0xE662),
    ("Why", 0xE663),
    ("-ing", 0xE664),
    ("May", 0xE665),
    ("No", 0xE666),
    ("Low", 0xE667),
    ("Roe", 0xE668),
    ("Loch", 0xE669),
    ("Llan", 0xE66A),
    ("Excite", 0xE66B),
    ("Exam", 0xE66C),
    ("It", 0xE670),
    ("Eat", 0xE671),
    ("Et", 0xE672),
    ("Eight", 0xE673),
    ("At", 0xE674),
    ("I", 0xE675),
    ("Ah", 0xE676),
    ("Awe", 0xE677),
    ("Ox", 0xE678),
    ("Oy", 0xE679),
    ("Utter", 0xE67A),
    ("Out", 0xE67B),
    ("Owe", 0xE67C),
    ("Foot", 0xE67D),
    ("Ooze", 0xE67E),
]

TEA = 0xE652
DAY = 0xE653
HE = 0xE662
WAY = 0xE661
THAW = 0xE656
CHEER = 0xE65E
OWE = 0xE67C
THEY = 0xE657
JAY = 0xE65F


def _family_to_label(family: str) -> str:
    base = family.removeprefix("qs")
    return "-ing" if base == "Ing" else base


def _compute_ligature_pairs() -> set[tuple[str, str]]:
    data = load_glyph_data(ROOT / "glyph_data")
    pairs: set[tuple[str, str]] = set()
    for family in data["glyph_families"].values():
        seq = family.get("sequence")
        if isinstance(seq, list) and len(seq) == 2:
            first, second = seq
            pairs.add((_family_to_label(first), _family_to_label(second)))
    return pairs


LIGATURE_PAIRS = _compute_ligature_pairs()


def _expect_tok(name: str) -> str:
    return f"·{name}"


def _join_expect(names_and_tokens: list[tuple[str, str]]) -> str:
    """Join tokens with `?`, switching to `+?` for ligature pairs.

    When a token participates in a ligature pair its modifiers are
    stripped, because `data-expect` applies modifiers to the whole
    ligature group and they are dropped in the separated interpretation.
    """
    in_liga_first: set[int] = set()
    in_liga_second: set[int] = set()
    for i in range(1, len(names_and_tokens)):
        prev_name = names_and_tokens[i - 1][0]
        cur_name = names_and_tokens[i][0]
        if (prev_name, cur_name) in LIGATURE_PAIRS:
            in_liga_first.add(i - 1)
            in_liga_second.add(i)

    parts: list[str] = []
    for i, (name, tok) in enumerate(names_and_tokens):
        if i in in_liga_first or i in in_liga_second:
            tok = _expect_tok(name)

        if i in in_liga_second:
            parts.append(f"+?{name}")
        elif i > 0:
            parts.append(f" ? {tok}")
        else:
            parts.append(tok)
    return "".join(parts)


def _case_id(*names: str) -> str:
    return "|".join(names)


# --- Panel 1: ·Tea + ·Tea: no double halves ----------------------------------


def _tea_tea_cases() -> list[tuple[str, str, str]]:
    tea_nhalf = "·Tea.!half"
    out: list[tuple[str, str, str]] = []

    out.append(
        (
            _case_id("Tea", "Tea"),
            chr(TEA) + chr(TEA),
            _join_expect([("Tea", tea_nhalf), ("Tea", tea_nhalf)]),
        )
    )
    for name, code in LETTERS:
        out.append(
            (
                _case_id(name, "Tea", "Tea"),
                chr(code) + chr(TEA) + chr(TEA),
                _join_expect(
                    [(name, _expect_tok(name)), ("Tea", tea_nhalf), ("Tea", tea_nhalf)]
                ),
            )
        )
    for name, code in LETTERS:
        out.append(
            (
                _case_id("Tea", "Tea", name),
                chr(TEA) + chr(TEA) + chr(code),
                _join_expect(
                    [("Tea", tea_nhalf), ("Tea", tea_nhalf), (name, _expect_tok(name))]
                ),
            )
        )
    return out


# --- Panel 2: ·Tea + ·Cheer: never joins -------------------------------------


def _tea_cheer_cases() -> list[tuple[str, str, str]]:
    tea_nhalf = "·Tea.!half"
    tea_tok = _expect_tok("Tea")
    cheer_tok = _expect_tok("Cheer")
    out: list[tuple[str, str, str]] = []

    out.append(
        (
            _case_id("Tea", "Cheer"),
            chr(TEA) + chr(CHEER),
            f"{tea_nhalf} | {cheer_tok}",
        )
    )
    for name, code in LETTERS:
        if name == "Tea":
            continue
        if (name, "Tea") in LIGATURE_PAIRS:
            expect = f"{_expect_tok(name)}+?Tea | {cheer_tok}"
        else:
            expect = f"{_expect_tok(name)} ? {tea_tok} | {cheer_tok}"
        out.append(
            (
                _case_id(name, "Tea", "Cheer"),
                chr(code) + chr(TEA) + chr(CHEER),
                expect,
            )
        )
    for name, code in LETTERS:
        out.append(
            (
                _case_id("Tea", "Cheer", name),
                chr(TEA) + chr(CHEER) + chr(code),
                f"{tea_nhalf} | {cheer_tok} ? {_expect_tok(name)}",
            )
        )
    return out


# --- Panel 3: ·He + ·Day: full He, half Day ----------------------------------


def _he_day_cases() -> list[tuple[str, str, str]]:
    he_nhalf = "·He.!half"
    day_half = "·Day.half"
    out: list[tuple[str, str, str]] = []

    out.append(
        (
            _case_id("He", "Day"),
            chr(HE) + chr(DAY),
            f"{he_nhalf} ~b~ {day_half}",
        )
    )
    for name, code in LETTERS:
        out.append(
            (
                _case_id(name, "He", "Day"),
                chr(code) + chr(HE) + chr(DAY),
                f"{_expect_tok(name)} ? {he_nhalf} ~b~ {day_half}",
            )
        )
    for name, code in LETTERS:
        if ("Day", name) in LIGATURE_PAIRS:
            if name == "Utter":
                expect = f"{he_nhalf} ~b~ ·Day+?{name}.half"
            else:
                expect = f"·He.half ~x~ ·Day+?{name}"
        else:
            expect = f"{he_nhalf} ~b~ {day_half} ? {_expect_tok(name)}"
        out.append(
            (
                _case_id("He", "Day", name),
                chr(HE) + chr(DAY) + chr(code),
                expect,
            )
        )
    return out


# --- Panel 4: ·Way + ·Day: full Way, full Day --------------------------------


def _way_day_cases() -> list[tuple[str, str, str]]:
    way_nhalf = "·Way.!half"
    day_nhalf = "·Day.!half"
    out: list[tuple[str, str, str]] = []

    out.append(
        (
            _case_id("Way", "Day"),
            chr(WAY) + chr(DAY),
            f"{way_nhalf} ~x~ {day_nhalf}",
        )
    )
    for name, code in LETTERS:
        out.append(
            (
                _case_id(name, "Way", "Day"),
                chr(code) + chr(WAY) + chr(DAY),
                f"{_expect_tok(name)} ? {way_nhalf} ~x~ {day_nhalf}",
            )
        )
    for name, code in LETTERS:
        if ("Day", name) in LIGATURE_PAIRS:
            expect = f"{way_nhalf} ? ·Day+?{name}"
        else:
            expect = f"{way_nhalf} ~x~ {day_nhalf} ? {_expect_tok(name)}"
        out.append(
            (
                _case_id("Way", "Day", name),
                chr(WAY) + chr(DAY) + chr(code),
                expect,
            )
        )
    return out


# --- Panel 5: ·Way + ·Thaw: full Way, never joins ----------------------------


def _way_thaw_cases() -> list[tuple[str, str, str]]:
    way_nhalf = "·Way.!half"
    thaw_tok = _expect_tok("Thaw")
    out: list[tuple[str, str, str]] = []

    out.append(
        (
            _case_id("Way", "Thaw"),
            chr(WAY) + chr(THAW),
            f"{way_nhalf} | {thaw_tok}",
        )
    )
    for name, code in LETTERS:
        out.append(
            (
                _case_id(name, "Way", "Thaw"),
                chr(code) + chr(WAY) + chr(THAW),
                f"{_expect_tok(name)} ? {way_nhalf} | {thaw_tok}",
            )
        )
    for name, code in LETTERS:
        out.append(
            (
                _case_id("Way", "Thaw", name),
                chr(WAY) + chr(THAW) + chr(code),
                f"{way_nhalf} | {thaw_tok} ? {_expect_tok(name)}",
            )
        )
    return out


# --- Panel 6: ·Owe + ·Day: never joins (opt back in with ss07) ---------------


def _owe_day_cases() -> list[tuple[str, str, str]]:
    owe_tok = _expect_tok("Owe")
    day_tok = _expect_tok("Day")
    out: list[tuple[str, str, str]] = []

    out.append(
        (
            _case_id("Owe", "Day"),
            chr(OWE) + chr(DAY),
            f"{owe_tok} | {day_tok}",
        )
    )
    for name, code in LETTERS:
        out.append(
            (
                _case_id(name, "Owe", "Day"),
                chr(code) + chr(OWE) + chr(DAY),
                f"{_expect_tok(name)} ? {owe_tok} | {day_tok}",
            )
        )
    for name, code in LETTERS:
        if ("Day", name) in LIGATURE_PAIRS:
            expect = f"{owe_tok} | ·Day+?{name}"
        else:
            expect = f"{owe_tok} | {day_tok} ? {_expect_tok(name)}"
        out.append(
            (
                _case_id("Owe", "Day", name),
                chr(OWE) + chr(DAY) + chr(code),
                expect,
            )
        )
    return out


# --- Panel 7: ·They + ·Jay: never joins --------------------------------------


def _they_jay_cases() -> list[tuple[str, str, str]]:
    they_tok = _expect_tok("They")
    jay_tok = _expect_tok("Jay")
    out: list[tuple[str, str, str]] = []

    out.append(
        (
            _case_id("They", "Jay"),
            chr(THEY) + chr(JAY),
            f"{they_tok} | {jay_tok}",
        )
    )
    for name, code in LETTERS:
        left_connection = "|?|" if name == "Utter" else "?"
        out.append(
            (
                _case_id(name, "They", "Jay"),
                chr(code) + chr(THEY) + chr(JAY),
                f"{_expect_tok(name)} {left_connection} {they_tok} | {jay_tok}",
            )
        )
    for name, code in LETTERS:
        if ("Jay", name) in LIGATURE_PAIRS:
            expect = f"{they_tok} | ·Jay+?{name}"
        else:
            expect = f"{they_tok} | {jay_tok} ? {_expect_tok(name)}"
        out.append(
            (
                _case_id("They", "Jay", name),
                chr(THEY) + chr(JAY) + chr(code),
                expect,
            )
        )
    return out


def _params(cases: list[tuple[str, str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    return [(text, expect) for _id, text, expect in cases], [c[0] for c in cases]


def _run(env: dict, text: str, expect: str) -> None:
    run_shaping_test_runs(
        env["fonts"],
        env["anchor_maps"],
        [{"font": "senior", "text": text}],
        expect,
        base_potential_entries=env["potentials"],
    )


_TEA_TEA_PARAMS, _TEA_TEA_IDS = _params(_tea_tea_cases())
_TEA_CHEER_PARAMS, _TEA_CHEER_IDS = _params(_tea_cheer_cases())
_HE_DAY_PARAMS, _HE_DAY_IDS = _params(_he_day_cases())
_WAY_DAY_PARAMS, _WAY_DAY_IDS = _params(_way_day_cases())
_WAY_THAW_PARAMS, _WAY_THAW_IDS = _params(_way_thaw_cases())
_OWE_DAY_PARAMS, _OWE_DAY_IDS = _params(_owe_day_cases())
_THEY_JAY_PARAMS, _THEY_JAY_IDS = _params(_they_jay_cases())


@pytest.mark.parametrize(("text", "expect"), _TEA_TEA_PARAMS, ids=_TEA_TEA_IDS)
def test_tea_tea_no_double_halves(shaping_env: dict, text: str, expect: str) -> None:
    _run(shaping_env, text, expect)


@pytest.mark.parametrize(("text", "expect"), _TEA_CHEER_PARAMS, ids=_TEA_CHEER_IDS)
def test_tea_cheer_never_joins(shaping_env: dict, text: str, expect: str) -> None:
    _run(shaping_env, text, expect)


@pytest.mark.parametrize(("text", "expect"), _HE_DAY_PARAMS, ids=_HE_DAY_IDS)
def test_he_day_full_he_half_day(shaping_env: dict, text: str, expect: str) -> None:
    _run(shaping_env, text, expect)


@pytest.mark.parametrize(("text", "expect"), _WAY_DAY_PARAMS, ids=_WAY_DAY_IDS)
def test_way_day_full_way_full_day(shaping_env: dict, text: str, expect: str) -> None:
    _run(shaping_env, text, expect)


@pytest.mark.parametrize(("text", "expect"), _WAY_THAW_PARAMS, ids=_WAY_THAW_IDS)
def test_way_thaw_full_way_never_joins(shaping_env: dict, text: str, expect: str) -> None:
    _run(shaping_env, text, expect)


@pytest.mark.parametrize(("text", "expect"), _OWE_DAY_PARAMS, ids=_OWE_DAY_IDS)
def test_owe_day_never_joins(shaping_env: dict, text: str, expect: str) -> None:
    _run(shaping_env, text, expect)


@pytest.mark.parametrize(("text", "expect"), _THEY_JAY_PARAMS, ids=_THEY_JAY_IDS)
def test_they_jay_never_joins(shaping_env: dict, text: str, expect: str) -> None:
    _run(shaping_env, text, expect)
