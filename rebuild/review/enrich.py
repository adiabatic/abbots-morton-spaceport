"""The review corpus's letter names and the spec it enriches against (rebuild/REVIEW-PLAN.md §2.2). The review crate enriches each unit: the old junctions from the §13.1 baseline subsets, the settle and explain results, the divergent positions computed against the alias map, and the highlight x-ranges in font units, from kern-neutral shaping of both fonts, matching the app's `font-kerning: none` rendering. This module gives the build and the corpus's readers what they share of that: each Quikscript code point's family (`LETTERS`), a window's letter-name notation (`notation`, `notation_tokens`), and the resolved spec (`load_spec`), which the build dumps for the crate."""

from __future__ import annotations

from pathlib import Path

from rebuild.pipeline import spec_load
from rebuild.pipeline.model import ResolvedSpec

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

LETTERS: dict[int, str] = {
    0xE650: "qsPea",
    0xE651: "qsBay",
    0xE652: "qsTea",
    0xE653: "qsDay",
    0xE654: "qsKey",
    0xE655: "qsGay",
    0xE656: "qsThaw",
    0xE657: "qsThey",
    0xE658: "qsFee",
    0xE659: "qsVie",
    0xE65A: "qsSee",
    0xE65B: "qsZoo",
    0xE65C: "qsShe",
    0xE65D: "qsJai",
    0xE65E: "qsCheer",
    0xE65F: "qsJay",
    0xE660: "qsYe",
    0xE661: "qsWay",
    0xE662: "qsHe",
    0xE663: "qsWhy",
    0xE664: "qsIng",
    0xE665: "qsMay",
    0xE666: "qsNo",
    0xE667: "qsLow",
    0xE668: "qsRoe",
    0xE669: "qsLoch",
    0xE66A: "qsLlan",
    0xE66B: "qsExcite",
    0xE66C: "qsExam",
    0xE670: "qsIt",
    0xE671: "qsEat",
    0xE672: "qsEt",
    0xE673: "qsEight",
    0xE674: "qsAt",
    0xE675: "qsI",
    0xE676: "qsAh",
    0xE677: "qsAwe",
    0xE678: "qsOx",
    0xE679: "qsOy",
    0xE67A: "qsUtter",
    0xE67B: "qsOut",
    0xE67C: "qsOwe",
    0xE67D: "qsFoot",
    0xE67E: "qsOoze",
}

SPACE = 0x0020
NAMER_DOT = 0x00B7
ZWNJ = 0x200C
# The units the crate settles and explains together, which it reads through the units init's vocabulary (`units_exec.VOCABULARY`), and the most recomputed units the build hands a thread at a time (`build.PHASE1_HANDOUT_UNITS`).
EXPLAIN_UNIT_BATCH_SIZE = 8192

_SPECIAL_DISPLAY = {"qsIng": "·-ing", "qsJai": "·J’ai"}
_BOUNDARY_NOTATION = {SPACE: "␣", NAMER_DOT: "·", ZWNJ: "◊ZWNJ"}


def letter_display(family: str) -> str:
    return _SPECIAL_DISPLAY.get(family, "·" + family[2:])


def notation(codepoint_values: tuple[int, ...]) -> str:
    """The caption form: letters concatenate (·Tea·Oy), boundary tokens are space-separated (◊ZWNJ ·Tea·Oy, ␣, ·)."""
    parts: list[str] = []
    previous_was_letter = False
    for value in codepoint_values:
        if value in LETTERS:
            token = letter_display(LETTERS[value])
            parts.append(token if previous_was_letter else (" " + token if parts else token))
            previous_was_letter = True
        else:
            token = _BOUNDARY_NOTATION.get(value, f"U+{value:04X}")
            parts.append((" " if parts else "") + token)
            previous_was_letter = False
    return "".join(parts)


def notation_tokens(codepoint_values: tuple[int, ...]) -> tuple[str, ...]:
    """Display tokens aligned one-to-one with codepoint positions: letter names (·May) and the boundary tokens (◊ZWNJ, ␣, ·) as `notation` renders them, so joining them with `notation`'s spacing rule reproduces the caption string."""
    return tuple(
        (
            letter_display(LETTERS[value])
            if value in LETTERS
            else _BOUNDARY_NOTATION.get(value, f"U+{value:04X}")
        )
        for value in codepoint_values
    )


def text_entities(codepoint_values: tuple[int, ...]) -> str:
    return "".join(f"&#x{value:04X};" for value in codepoint_values)


def load_spec(repo_root: Path = REPO_ROOT) -> ResolvedSpec:
    return spec_load.load_spec(
        repo_root / "glyph_data" / "runes",
        repo_root / "rebuild" / "script.yaml",
        repo_root / "rebuild" / "schema",
    )
