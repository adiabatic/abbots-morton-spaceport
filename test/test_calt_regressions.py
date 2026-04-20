import sys
from functools import lru_cache
from pathlib import Path

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"
ZWNJ = "\u200C"
sys.path.insert(0, str(ROOT / "tools"))

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import JoinGlyph


@lru_cache(maxsize=1)
def _font() -> hb.Font:
    blob = hb.Blob.from_file_path(str(FONT_PATH))
    face = hb.Face(blob)
    return hb.Font(face)


def _shape(text: str) -> list[str]:
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [font.glyph_to_string(info.codepoint) for info in buf.glyph_infos]


@lru_cache(maxsize=1)
def _compiled_meta() -> dict[str, JoinGlyph]:
    data = load_glyph_data(ROOT / "glyph_data")
    return compile_glyph_set(data, "senior").glyph_meta


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


def _append_utter_alt_failures(failures: list[str], label: str, text: str) -> None:
    glyphs = _shape(text)
    meta_map = _compiled_meta()

    for index, glyph_name in enumerate(glyphs):
        meta = meta_map.get(glyph_name)
        if meta is None or meta.base_name != "qsUtter" or "alt" not in meta.traits:
            continue

        entry_ys = _entry_ys(glyph_name)
        exit_ys = _exit_ys(glyph_name)

        if entry_ys:
            if index == 0:
                failures.append(
                    f"{label}: {glyph_name} has left-entry Ys {sorted(entry_ys)} at start in {glyphs}"
                )
            else:
                prev_name = glyphs[index - 1]
                common = _exit_ys(prev_name) & entry_ys
                if not common:
                    failures.append(
                        f"{label}: {glyph_name} does not join left to {prev_name} "
                        f"(prev exits={sorted(_exit_ys(prev_name))}, entry={sorted(entry_ys)}) in {glyphs}"
                    )

        if exit_ys:
            if index + 1 >= len(glyphs):
                failures.append(
                    f"{label}: {glyph_name} has right-exit Ys {sorted(exit_ys)} at end in {glyphs}"
                )
            else:
                next_name = glyphs[index + 1]
                common = exit_ys & _entry_ys(next_name)
                if not common:
                    failures.append(
                        f"{label}: {glyph_name} does not join right to {next_name} "
                        f"(exit={sorted(exit_ys)}, next entries={sorted(_entry_ys(next_name))}) in {glyphs}"
                    )


def _utter_alt_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    utter = chars["qsUtter"]
    boundary_states = (
        ("plain", "", ""),
        ("zwnj-left", ZWNJ, ""),
        ("zwnj-right", "", ZWNJ),
        ("zwnj-both", ZWNJ, ZWNJ),
    )

    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            for state_name, left_boundary, right_boundary in boundary_states:
                text = left_char + left_boundary + utter + right_boundary + right_char
                label = f"{left_name} / {state_name} / qsUtter / {right_name}"
                _append_utter_alt_failures(failures, label, text)

    for right_name, right_char in _plain_quikscript_letters():
        _append_utter_alt_failures(
            failures,
            f"start / plain / qsUtter / {right_name}",
            utter + right_char,
        )
        _append_utter_alt_failures(
            failures,
            f"start / zwnj-right / qsUtter / {right_name}",
            utter + ZWNJ + right_char,
        )

    for left_name, left_char in _plain_quikscript_letters():
        _append_utter_alt_failures(
            failures,
            f"{left_name} / plain / qsUtter / end",
            left_char + utter,
        )
        _append_utter_alt_failures(
            failures,
            f"{left_name} / zwnj-left / qsUtter / end",
            left_char + ZWNJ + utter,
        )

    _append_utter_alt_failures(failures, "start / isolated / qsUtter / end", utter)
    _append_utter_alt_failures(failures, "start / isolated-zwnj / qsUtter / end", utter + ZWNJ)
    _append_utter_alt_failures(failures, "start / zwnj-isolated / qsUtter / end", ZWNJ + utter)

    return failures


def test_qs_see_exit_baseline_right_before_qs_ooze():
    assert _shape("\uE65A\uE67E") == [
        "qsSee.exit-baseline-right",
        "qsOoze",
    ]


def test_qs_no_alt_requires_a_compatible_it_exit():
    assert _shape("\uE65F\uE670\uE666") == [
        "qsJay",
        "qsIt.exit-xheight",
        "qsNo",
    ]


def test_qs_low_entry_extended_requires_a_compatible_see_exit():
    assert _shape("\uE665\uE670\uE65A\uE667") == [
        "qsMay.exit-extended",
        "qsIt.entry-xheight",
        "qsSee",
        "qsLow",
    ]


def test_qs_ing_before_thaw_uses_triply_extended_exit():
    chars = _char_map()

    assert _shape(chars["qsIng"] + chars["qsThaw"]) == [
        "qsIng.exit-triply-extended",
        "qsThaw.after-ing",
    ]


def test_zwnj_keeps_qs_it_entryless_while_still_joining_qs_zoo():
    glyphs = _shape("\uE653\u200C\uE670\uE65B\uE675\uE668")

    assert glyphs[0:2] == ["qsDay", "space"]
    assert glyphs[3:] == ["qsZoo", "qsI.exit-extended", "qsRoe"]

    meta = _compiled_meta()
    it_meta = meta[glyphs[2]]
    zoo_meta = meta[glyphs[3]]

    assert not it_meta.entry
    assert not it_meta.entry_curs_only
    assert {anchor[1] for anchor in it_meta.exit} == {5}
    assert {anchor[1] for anchor in it_meta.exit} & {anchor[1] for anchor in zoo_meta.entry} == {5}


def test_qs_no_alt_selected_after_ox_before_fee():
    glyphs = _shape("\uE678\uE666\uE658")
    meta = _compiled_meta()
    no_glyph = glyphs[1]
    no_meta = meta[no_glyph]
    assert "alt" in no_meta.traits, (
        f"Expected No.alt after Ox, got {no_glyph}"
    )
    ox_exits = _exit_ys(glyphs[0])
    no_entries = _entry_ys(no_glyph)
    assert ox_exits & no_entries, (
        f"Ox exits={sorted(ox_exits)} should overlap No.alt entries={sorted(no_entries)}"
    )


def _no_alt_selection_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    no = chars["qsNo"]
    meta_map = _compiled_meta()

    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            text = left_char + no + right_char
            glyphs = _shape(text)
            for index, glyph_name in enumerate(glyphs):
                meta = meta_map.get(glyph_name)
                if meta is None or meta.base_name != "qsNo":
                    continue
                if index == 0:
                    continue
                prev_name = glyphs[index - 1]
                prev_meta = meta_map.get(prev_name)
                prev_exits = _exit_ys(prev_name)
                prev_is_zoo = prev_meta and prev_meta.base_name == "qsZoo"
                if 0 in prev_exits and not prev_is_zoo and "alt" not in meta.traits:
                    label = f"{left_name} / qsNo / {right_name}"
                    failures.append(
                        f"{label}: expected No.alt after {prev_name} (exits at y=0) "
                        f"but got {glyph_name} in {glyphs}"
                    )

    return failures


def test_qs_no_alt_selected_when_preceded_by_baseline_exit():
    failures = _no_alt_selection_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_pea_ye_do_not_connect():
    assert _shape("\uE650\uE660") == [
        "qsPea",
        "qsYe",
    ]


def test_qs_tea_ye_do_not_connect():
    assert _shape("\uE652\uE660") == [
        "qsTea",
        "qsYe",
    ]


def test_qs_they_ye_do_not_connect():
    assert _shape("\uE657\uE660") == [
        "qsThey",
        "qsYe",
    ]


def test_qs_why_ye_do_not_connect():
    assert _shape("\uE663\uE660") == [
        "qsWhy",
        "qsYe",
    ]


def test_qs_way_ye_do_not_connect():
    assert _shape("\uE661\uE660") == [
        "qsWay",
        "qsYe",
    ]


def test_qs_he_ye_do_not_connect():
    assert _shape("\uE662\uE660") == [
        "qsHe",
        "qsYe",
    ]


def test_qs_it_ye_do_not_connect():
    assert _shape("\uE670\uE660") == [
        "qsIt",
        "qsYe",
    ]


def test_qs_ye_it_do_not_connect():
    assert _shape("\uE660\uE670") == [
        "qsYe",
        "qsIt",
    ]


def test_qs_ye_see_do_not_connect():
    assert _shape("\uE660\uE65A") == [
        "qsYe",
        "qsSee.after-ye",
    ]


def test_qs_ye_ing_do_not_connect():
    assert _shape("\uE660\uE664") == [
        "qsYe",
        "qsIng.after-ye",
    ]


def test_qs_ye_excite_do_not_connect():
    assert _shape("\uE660\uE66B") == [
        "qsYe",
        "qsExcite.after-ye",
    ]


def test_qs_ye_exam_do_not_connect():
    assert _shape("\uE660\uE66C") == [
        "qsYe",
        "qsExam.after-ye",
    ]


def test_qs_see_pea_keeps_the_y6_join():
    assert _shape("\uE65A\uE650") == [
        "qsSee.exit-y6",
        "qsPea.entry-y6",
    ]


def test_qs_utter_alt_variants_always_keep_the_joins_they_require():
    failures = _utter_alt_invariant_failures()
    assert not failures, "\n".join(failures[:50])
