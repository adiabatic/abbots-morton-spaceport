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


@lru_cache(maxsize=None)
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


def _base_names(glyph_names: list[str]) -> tuple[str, ...]:
    meta_map = _compiled_meta()
    result = []
    for glyph_name in glyph_names:
        glyph_meta = meta_map.get(glyph_name)
        result.append(glyph_meta.base_name if glyph_meta is not None else glyph_name)
    return tuple(result)


def _pair_join_ys(glyph_names: list[str], index: int) -> set[int]:
    if index + 1 >= len(glyph_names):
        return set()
    return _exit_ys(glyph_names[index]) & _entry_ys(glyph_names[index + 1])


def _assert_join_preserved(
    label: str,
    pair_glyphs: list[str],
    triple_glyphs: list[str],
    *,
    pair_index_in_triple: int,
) -> None:
    pair_ys = _pair_join_ys(pair_glyphs, 0)
    triple_ys = _pair_join_ys(triple_glyphs, pair_index_in_triple)
    missing = pair_ys - triple_ys
    assert not missing, (
        f"{label}: expected established join Ys {sorted(pair_ys)} from {pair_glyphs} "
        f"to remain in {triple_glyphs}, but lost Ys {sorted(missing)}"
    )


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


def _append_terminal_owe_failures(failures: list[str], label: str, text: str) -> None:
    glyphs = _shape(text)
    meta_map = _compiled_meta()

    if not glyphs:
        failures.append(f"{label}: expected a terminal qsOwe glyph")
        return

    index = len(glyphs) - 1
    glyph_name = glyphs[index]
    meta = meta_map.get(glyph_name)
    if meta is None or meta.base_name != "qsOwe":
        failures.append(f"{label}: expected terminal qsOwe glyph, got {glyphs}")
        return
    entry_ys = _entry_ys(glyph_name)
    exit_ys = _exit_ys(glyph_name)

    if not entry_ys:
        failures.append(f"{label}: {glyph_name} has no left-entry Ys in {glyphs}")
    elif index == 0:
        failures.append(f"{label}: {glyph_name} starts the run in {glyphs}")
    else:
        prev_name = glyphs[index - 1]
        common = _exit_ys(prev_name) & entry_ys
        if not common:
            failures.append(
                f"{label}: {glyph_name} does not join left to {prev_name} "
                f"(prev exits={sorted(_exit_ys(prev_name))}, entry={sorted(entry_ys)}) in {glyphs}"
            )
    if exit_ys:
        failures.append(
            f"{label}: {glyph_name} has right-exit Ys {sorted(exit_ys)} at end in {glyphs}"
        )


def _owe_terminal_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    pea = chars["qsPea"]
    owe = chars["qsOwe"]

    for left_name, left_char in _plain_quikscript_letters():
        _append_terminal_owe_failures(
            failures,
            f"{left_name} / qsPea / qsOwe / end",
            left_char + pea + owe,
        )

    return failures


def _middle_pea_xheight_left_gate_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    pea = chars["qsPea"]
    allowed_left = {"qsMay", "qsUtter"}
    saw_allowed: set[str] = set()
    saw_disallowed_case = False
    meta_map = _compiled_meta()

    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            glyphs = _shape(left_char + pea + right_char)
            pea_index = next(
                (
                    index for index, glyph_name in enumerate(glyphs)
                    if meta_map.get(glyph_name) and meta_map[glyph_name].base_name == "qsPea"
                ),
                None,
            )
            if pea_index is None or pea_index == 0 or pea_index + 1 >= len(glyphs):
                continue

            prev_name = glyphs[pea_index - 1]
            pea_name = glyphs[pea_index]
            next_name = glyphs[pea_index + 1]
            right_common = _exit_ys(pea_name) & _entry_ys(next_name)
            if 5 not in right_common:
                continue

            left_common = _exit_ys(prev_name) & _entry_ys(pea_name)
            label = f"{left_name} / qsPea / {right_name}"
            if left_name in allowed_left:
                saw_allowed.add(left_name)
                if 5 not in left_common:
                    failures.append(
                        f"{label}: expected x-height left join into {pea_name} from {prev_name} "
                        f"while keeping right join to {next_name} in {glyphs}"
                    )
            else:
                saw_disallowed_case = True
                if 5 in left_common:
                    failures.append(
                        f"{label}: unexpected x-height left join into {pea_name} from {prev_name} "
                        f"while keeping right join to {next_name} in {glyphs}"
                    )

    missing_allowed = sorted(allowed_left - saw_allowed)
    if missing_allowed:
        failures.append(f"Missing allowed middle-Pea x-height cases for {missing_allowed}")
    if not saw_disallowed_case:
        failures.append("Did not exercise any disallowed middle-Pea x-height left contexts")

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


def test_qs_owe_after_pea_stays_left_only_at_word_end():
    chars = _char_map()

    assert _shape(chars["qsPea"] + chars["qsOwe"]) == [
        "qsPea.half",
        "qsOwe.entry-xheight.entry-extended",
    ]


def test_qs_owe_after_bay_pea_stays_left_only_at_word_end():
    chars = _char_map()

    assert _shape(chars["qsBay"] + chars["qsPea"] + chars["qsOwe"]) == [
        "qsBay",
        "qsPea.half",
        "qsOwe.entry-xheight.entry-extended",
    ]


def test_qs_owe_after_tea_pea_stays_left_only_at_word_end():
    chars = _char_map()

    assert _shape(chars["qsTea"] + chars["qsPea"] + chars["qsOwe"]) == [
        "qsTea",
        "qsPea.half",
        "qsOwe.entry-xheight.entry-extended",
    ]


def test_qs_owe_after_pea_keeps_right_exit_with_real_follower():
    chars = _char_map()

    assert _shape(chars["qsPea"] + chars["qsOwe"] + chars["qsNo"]) == [
        "qsPea.half",
        "qsOwe.entry-xheight.exit-xheight.entry-extended",
        "qsNo",
    ]


def test_qs_owe_stays_left_only_at_word_end_after_any_plain_letter_then_pea():
    failures = _owe_terminal_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_utter_keeps_middle_pea_xheight_left_join_when_pea_also_joins_right():
    chars = _char_map()

    assert _shape(chars["qsUtter"] + chars["qsPea"] + chars["qsAwe"]) == [
        "qsUtter",
        "qsPea.half.entry-xheight.exit-xheight",
        "qsAwe.entry-extended",
    ]


def test_qs_ah_does_not_gain_middle_pea_xheight_left_join_when_pea_joins_right():
    chars = _char_map()

    assert _shape(chars["qsAh"] + chars["qsPea"] + chars["qsAwe"]) == [
        "qsAh",
        "qsPea.half",
        "qsAwe.entry-extended",
    ]


def test_middle_pea_xheight_left_join_is_limited_to_utter_and_may():
    failures = _middle_pea_xheight_left_gate_failures()
    assert not failures, "\n".join(failures[:50])


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


def test_qs_way_tea_do_not_connect():
    glyphs = _shape("\uE661\uE652")
    way_exits = _exit_ys(glyphs[0])
    tea_entries = _entry_ys(glyphs[1])
    assert not (way_exits & tea_entries), (
        f"Way exits={sorted(way_exits)} should not overlap Tea entries={sorted(tea_entries)} in {glyphs}"
    )


def test_qs_way_not_half_before_tea():
    glyphs = _shape("\uE661\uE652")
    meta = _compiled_meta()
    way_meta = meta[glyphs[0]]
    assert way_meta.base_name == "qsWay"
    assert "half" not in way_meta.traits, (
        f"Expected non-half Way before Tea, got {glyphs[0]}"
    )


def _way_tea_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    way = chars["qsWay"]
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            text = left_char + way + tea + right_char
            glyphs = _shape(text)
            for index, glyph_name in enumerate(glyphs):
                glyph_meta = meta_map.get(glyph_name)
                if glyph_meta is None or glyph_meta.base_name != "qsWay":
                    continue
                label = f"{left_name} / qsWay / qsTea / {right_name}"
                if "half" in glyph_meta.traits:
                    failures.append(
                        f"{label}: half-Way selected before Tea: {glyphs}"
                    )
                if index + 1 < len(glyphs):
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{label}: Way joins to next glyph {glyphs[index + 1]} "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def test_qs_way_tea_do_not_connect_in_context():
    failures = _way_tea_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_why_tea_do_not_connect():
    glyphs = _shape("\uE663\uE652")
    why_exits = _exit_ys(glyphs[0])
    tea_entries = _entry_ys(glyphs[1])
    assert not (why_exits & tea_entries), (
        f"Why exits={sorted(why_exits)} should not overlap Tea entries={sorted(tea_entries)} in {glyphs}"
    )


def test_qs_why_not_half_before_tea():
    glyphs = _shape("\uE663\uE652")
    meta = _compiled_meta()
    why_meta = meta[glyphs[0]]
    assert why_meta.base_name == "qsWhy"
    assert "half" not in why_meta.traits, (
        f"Expected non-half Why before Tea, got {glyphs[0]}"
    )


def _why_tea_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    why = chars["qsWhy"]
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            text = left_char + why + tea + right_char
            glyphs = _shape(text)
            for index, glyph_name in enumerate(glyphs):
                glyph_meta = meta_map.get(glyph_name)
                if glyph_meta is None or glyph_meta.base_name != "qsWhy":
                    continue
                label = f"{left_name} / qsWhy / qsTea / {right_name}"
                if "half" in glyph_meta.traits:
                    failures.append(
                        f"{label}: half-Why selected before Tea: {glyphs}"
                    )
                if index + 1 < len(glyphs):
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{label}: Why joins to next glyph {glyphs[index + 1]} "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def test_qs_why_tea_do_not_connect_in_context():
    failures = _why_tea_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_owe_tea_do_not_connect():
    glyphs = _shape("\uE67C\uE652")
    owe_exits = _exit_ys(glyphs[0])
    tea_entries = _entry_ys(glyphs[1])
    assert not (owe_exits & tea_entries), (
        f"Owe exits={sorted(owe_exits)} should not overlap Tea entries={sorted(tea_entries)} in {glyphs}"
    )


def test_qs_tea_owe_do_not_connect():
    glyphs = _shape("\uE652\uE67C")
    tea_exits = _exit_ys(glyphs[0])
    owe_entries = _entry_ys(glyphs[1])
    assert not (tea_exits & owe_entries), (
        f"Tea exits={sorted(tea_exits)} should not overlap Owe entries={sorted(owe_entries)} in {glyphs}"
    )


def _owe_tea_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    owe = chars["qsOwe"]
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = owe + tea + right_char
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsOwe" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsTea":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"qsOwe / qsTea / {right_name}: Owe joins Tea "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + owe + tea
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsOwe" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsTea":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{left_name} / qsOwe / qsTea: Owe joins Tea "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def _tea_owe_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    owe = chars["qsOwe"]
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = tea + owe + right_char
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsOwe":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"qsTea / qsOwe / {right_name}: Tea joins Owe "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + tea + owe
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsOwe":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{left_name} / qsTea / qsOwe: Tea joins Owe "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def test_qs_owe_tea_do_not_connect_in_context():
    failures = _owe_tea_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_tea_owe_do_not_connect_in_context():
    failures = _tea_owe_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_excite_tea_connect_at_baseline():
    glyphs = _shape("\uE66B\uE652")
    excite_exits = _exit_ys(glyphs[0])
    tea_entries = _entry_ys(glyphs[1])
    assert 0 in (excite_exits & tea_entries), (
        f"Excite exits={sorted(excite_exits)} should overlap Tea entries={sorted(tea_entries)} at Y=0 in {glyphs}"
    )


def test_qs_excite_tea_keeps_left_join_before_qs_ah():
    chars = _char_map()
    pair = _shape(chars["qsExcite"] + chars["qsTea"])
    triple = _shape(chars["qsExcite"] + chars["qsTea"] + chars["qsAh"])
    assert _base_names(pair) == ("qsExcite", "qsTea")
    assert _base_names(triple[:2]) == ("qsExcite", "qsTea")
    _assert_join_preserved("qsExcite / qsTea / qsAh", pair, triple, pair_index_in_triple=0)


def test_qs_excite_tea_keeps_left_join_before_qs_awe():
    chars = _char_map()
    pair = _shape(chars["qsExcite"] + chars["qsTea"])
    triple = _shape(chars["qsExcite"] + chars["qsTea"] + chars["qsAwe"])
    assert _base_names(pair) == ("qsExcite", "qsTea")
    assert _base_names(triple[:2]) == ("qsExcite", "qsTea")
    _assert_join_preserved("qsExcite / qsTea / qsAwe", pair, triple, pair_index_in_triple=0)


def test_qs_excite_tea_does_not_keep_the_baseline_exit_before_qs_ox():
    chars = _char_map()
    glyphs = _shape(chars["qsExcite"] + chars["qsTea"] + chars["qsOx"])
    assert _base_names(glyphs) == ("qsExcite", "qsTea", "qsOx")
    assert not _pair_join_ys(glyphs, 0)
    assert _pair_join_ys(glyphs, 1) == {5}
    assert not _exit_ys(glyphs[0]), glyphs


def test_qs_excite_tea_keeps_the_baseline_join_before_qs_tea():
    chars = _char_map()
    pair = _shape(chars["qsExcite"] + chars["qsTea"])
    triple = _shape(chars["qsExcite"] + chars["qsTea"] + chars["qsTea"])
    assert _base_names(pair) == ("qsExcite", "qsTea")
    assert _base_names(triple) == ("qsExcite", "qsTea", "qsTea")
    _assert_join_preserved("qsExcite / qsTea / qsTea", pair, triple, pair_index_in_triple=0)
    assert not _pair_join_ys(triple, 1)


def test_qs_excite_tea_does_not_keep_the_baseline_exit_before_qs_out():
    chars = _char_map()
    pair = _shape(chars["qsTea"] + chars["qsOut"])
    triple = _shape(chars["qsExcite"] + chars["qsTea"] + chars["qsOut"])
    assert _base_names(pair) == ("qsTea", "qsOut")
    assert _base_names(triple) == ("qsExcite", "qsTea", "qsOut")
    assert not _pair_join_ys(triple, 0)
    _assert_join_preserved("qsExcite / qsTea / qsOut", pair, triple[1:], pair_index_in_triple=0)
    assert not _exit_ys(triple[0]), triple


def test_qs_it_excite_does_not_force_qs_tea_out_of_half_before_qs_it():
    chars = _char_map()
    glyphs = _shape(chars["qsIt"] + chars["qsExcite"] + chars["qsTea"] + chars["qsIt"])
    assert _base_names(glyphs) == ("qsIt", "qsExcite", "qsTea", "qsIt")
    assert _pair_join_ys(glyphs, 0) == {0}
    assert not _pair_join_ys(glyphs, 1)
    assert _pair_join_ys(glyphs, 2) == {5}
    assert "half" in _compiled_meta()[glyphs[2]].traits, glyphs


def test_qs_tea_excite_keeps_right_join_to_qs_ah():
    chars = _char_map()
    pair = _shape(chars["qsExcite"] + chars["qsAh"])
    triple = _shape(chars["qsTea"] + chars["qsExcite"] + chars["qsAh"])
    assert _base_names(pair) == ("qsExcite", "qsAh")
    assert _base_names(triple[1:]) == ("qsExcite", "qsAh")
    assert not _pair_join_ys(triple, 0)
    _assert_join_preserved("qsTea / qsExcite / qsAh", pair, triple, pair_index_in_triple=1)


def test_qs_pea_excite_keeps_right_join_to_qs_ah():
    chars = _char_map()
    pair = _shape(chars["qsExcite"] + chars["qsAh"])
    triple = _shape(chars["qsPea"] + chars["qsExcite"] + chars["qsAh"])
    assert _base_names(pair) == ("qsExcite", "qsAh")
    assert _base_names(triple[1:]) == ("qsExcite", "qsAh")
    assert not _pair_join_ys(triple, 0)
    _assert_join_preserved("qsPea / qsExcite / qsAh", pair, triple, pair_index_in_triple=1)


def test_qs_ye_excite_keeps_right_join_to_qs_ah():
    chars = _char_map()
    pair = _shape(chars["qsExcite"] + chars["qsAh"])
    triple = _shape(chars["qsYe"] + chars["qsExcite"] + chars["qsAh"])
    assert _base_names(pair) == ("qsExcite", "qsAh")
    assert _base_names(triple[1:]) == ("qsExcite", "qsAh")
    assert not _pair_join_ys(triple, 0)
    _assert_join_preserved("qsYe / qsExcite / qsAh", pair, triple, pair_index_in_triple=1)


def test_qs_tea_excite_do_not_connect():
    glyphs = _shape("\uE652\uE66B")
    tea_exits = _exit_ys(glyphs[0])
    excite_entries = _entry_ys(glyphs[1])
    assert not (tea_exits & excite_entries), (
        f"Tea exits={sorted(tea_exits)} should not overlap Excite entries={sorted(excite_entries)} in {glyphs}"
    )


def test_qs_exam_tea_do_not_connect():
    glyphs = _shape("\uE66C\uE652")
    exam_exits = _exit_ys(glyphs[0])
    tea_entries = _entry_ys(glyphs[1])
    assert not (exam_exits & tea_entries), (
        f"Exam exits={sorted(exam_exits)} should not overlap Tea entries={sorted(tea_entries)} in {glyphs}"
    )


def test_qs_tea_exam_do_not_connect():
    glyphs = _shape("\uE652\uE66C")
    tea_exits = _exit_ys(glyphs[0])
    exam_entries = _entry_ys(glyphs[1])
    assert not (tea_exits & exam_entries), (
        f"Tea exits={sorted(tea_exits)} should not overlap Exam entries={sorted(exam_entries)} in {glyphs}"
    )




def _tea_excite_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    excite = chars["qsExcite"]
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = tea + excite + right_char
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsExcite":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"qsTea / qsExcite / {right_name}: Tea joins Excite "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + tea + excite
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsExcite":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{left_name} / qsTea / qsExcite: Tea joins Excite "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def _exam_tea_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    exam = chars["qsExam"]
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = exam + tea + right_char
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsExam" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsTea":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"qsExam / qsTea / {right_name}: Exam joins Tea "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + exam + tea
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsExam" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsTea":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{left_name} / qsExam / qsTea: Exam joins Tea "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def _tea_exam_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    exam = chars["qsExam"]
    tea = chars["qsTea"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = tea + exam + right_char
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsExam":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"qsTea / qsExam / {right_name}: Tea joins Exam "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + tea + exam
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsExam":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{left_name} / qsTea / qsExam: Tea joins Exam "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def test_qs_tea_excite_do_not_connect_in_context():
    failures = _tea_excite_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_exam_tea_do_not_connect_in_context():
    failures = _exam_tea_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_tea_exam_do_not_connect_in_context():
    failures = _tea_exam_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_tea_thaw_do_not_connect():
    glyphs = _shape("\uE652\uE656")
    tea_exits = _exit_ys(glyphs[0])
    thaw_entries = _entry_ys(glyphs[1])
    assert not (tea_exits & thaw_entries), (
        f"Tea exits={sorted(tea_exits)} should not overlap Thaw entries={sorted(thaw_entries)} in {glyphs}"
    )


def _tea_thaw_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    tea = chars["qsTea"]
    thaw = chars["qsThaw"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = tea + thaw + right_char
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsThaw":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"qsTea / qsThaw / {right_name}: Tea joins Thaw "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + tea + thaw
        glyphs = _shape(text)
        for index, glyph_name in enumerate(glyphs):
            glyph_meta = meta_map.get(glyph_name)
            if glyph_meta is None:
                continue
            if glyph_meta.base_name == "qsTea" and index + 1 < len(glyphs):
                next_meta = meta_map.get(glyphs[index + 1])
                if next_meta and next_meta.base_name == "qsThaw":
                    common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
                    if common:
                        failures.append(
                            f"{left_name} / qsTea / qsThaw: Tea joins Thaw "
                            f"at Y={sorted(common)} in {glyphs}"
                        )

    return failures


def test_qs_tea_thaw_do_not_connect_in_context():
    failures = _tea_thaw_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def _excite_nonjoining_left_context_preserves_right_join_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    excite = chars["qsExcite"]

    for right_name, right_char in _plain_quikscript_letters():
        right_pair_glyphs = _shape(excite + right_char)
        if len(right_pair_glyphs) != 2:
            continue
        if _base_names(right_pair_glyphs) != ("qsExcite", right_name):
            continue
        pair_ys = _pair_join_ys(right_pair_glyphs, 0)
        if not pair_ys:
            continue

        for left_name, left_char in _plain_quikscript_letters():
            left_pair_glyphs = _shape(left_char + excite)
            if len(left_pair_glyphs) != 2:
                continue
            if _base_names(left_pair_glyphs) != (left_name, "qsExcite"):
                continue
            if _pair_join_ys(left_pair_glyphs, 0):
                continue

            triple_glyphs = _shape(left_char + excite + right_char)
            if len(triple_glyphs) != 3:
                continue
            if _base_names(triple_glyphs) != (left_name, "qsExcite", right_name):
                continue
            left_triple_ys = _pair_join_ys(triple_glyphs, 0)
            right_triple_ys = _pair_join_ys(triple_glyphs, 1)
            missing = pair_ys - right_triple_ys
            if left_triple_ys or missing:
                failures.append(
                    f"{left_name} / qsExcite / {right_name}: nonjoining left context "
                    f"{left_pair_glyphs} changed the right join from {right_pair_glyphs} "
                    f"to {triple_glyphs}; left Ys={sorted(left_triple_ys)}, "
                    f"right pair Ys={sorted(pair_ys)}, triple Ys={sorted(right_triple_ys)}, "
                    f"missing {sorted(missing)}"
                )

    return failures


def test_qs_excite_nonjoining_left_context_preserves_right_join_in_plain_triples():
    failures = _excite_nonjoining_left_context_preserves_right_join_failures()
    assert not failures, "\n".join(failures[:50])


def _excite_tea_only_keeps_the_left_join_when_the_final_tea_still_supports_it_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()

    for right_name, right_char in _plain_quikscript_letters():
        glyphs = _shape(chars["qsExcite"] + chars["qsTea"] + right_char)
        if len(glyphs) != 3:
            continue
        if _base_names(glyphs) != ("qsExcite", "qsTea", right_name):
            continue
        if _pair_join_ys(glyphs, 0):
            continue
        if _exit_ys(glyphs[0]):
            failures.append(
                f"qsExcite / qsTea / {right_name}: left pair does not join in {glyphs}, "
                f"but qsExcite still has exits {sorted(_exit_ys(glyphs[0]))}"
            )

    return failures


def test_qs_excite_tea_only_keeps_the_left_join_when_the_final_tea_still_supports_it():
    failures = _excite_tea_only_keeps_the_left_join_when_the_final_tea_still_supports_it_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_see_pea_keeps_the_y6_join():
    assert _shape("\uE65A\uE650") == [
        "qsSee.exit-y6",
        "qsPea.entry-y6",
    ]


def test_qs_utter_alt_variants_always_keep_the_joins_they_require():
    failures = _utter_alt_invariant_failures()
    assert not failures, "\n".join(failures[:50])
