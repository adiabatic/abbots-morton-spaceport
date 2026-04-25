import sys
from functools import lru_cache
from pathlib import Path

import pytest
import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"
ZWNJ = "\u200C"
_SS05_FEATURE = (("ss05", True),)
_SS07_FEATURE = (("ss07", True),)
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


@lru_cache(maxsize=None)
def _shape_with_clusters(text: str) -> tuple[tuple[str, int], ...]:
    """Shape `text` and return ((glyph_name, cluster), ...).

    Cluster values are the character indices from the input; ligatures
    report the cluster of their first component.
    """
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return tuple(
        (font.glyph_to_string(info.codepoint), info.cluster)
        for info in buf.glyph_infos
    )


@lru_cache(maxsize=None)
def _shape_with_features(
    text: str,
    feature_items: tuple[tuple[str, bool], ...],
) -> list[str]:
    font = _font()
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, dict(feature_items))
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


def _qs_text(*parts: str) -> str:
    chars = _char_map()
    result = []
    for part in parts:
        if part in chars:
            result.append(chars[part])
            continue
        if part.startswith("qs"):
            raise KeyError(f"Unknown Quikscript glyph name: {part}")
        result.append(part)
    return "".join(result)


def _shape_qs(
    *parts: str,
    features: tuple[tuple[str, bool], ...] = (),
) -> list[str]:
    text = _qs_text(*parts)
    return _shape_with_features(text, features) if features else _shape(text)


def _shape_qs_with_clusters(*parts: str) -> tuple[tuple[str, int], ...]:
    return _shape_with_clusters(_qs_text(*parts))


def _assert_no_failures(failures: list[str], *, limit: int | None = 50) -> None:
    excerpt = failures if limit is None else failures[:limit]
    assert not failures, "\n".join(excerpt)


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


def _find_base_index(glyph_names: list[str], base_name: str) -> int | None:
    meta_map = _compiled_meta()
    for index, glyph_name in enumerate(glyph_names):
        glyph_meta = meta_map.get(glyph_name)
        if glyph_meta is not None and glyph_meta.base_name == base_name:
            return index
    return None


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


def _append_nonjoining_pair_failures(
    failures: list[str],
    label: str,
    glyphs: list[str],
    left_base: str,
    right_base: str,
) -> None:
    meta_map = _compiled_meta()
    for index, glyph_name in enumerate(glyphs[:-1]):
        left_meta = meta_map.get(glyph_name)
        right_meta = meta_map.get(glyphs[index + 1])
        if left_meta is None or right_meta is None:
            continue
        if left_meta.base_name != left_base or right_meta.base_name != right_base:
            continue
        common = _pair_join_ys(glyphs, index)
        if common:
            failures.append(
                f"{label}: {left_base} joins {right_base} at Y={sorted(common)} in {glyphs}"
            )


def _collect_nonjoining_pair_context_failures(
    left_base: str,
    right_base: str,
) -> list[str]:
    failures: list[str] = []

    for right_name, _ in _plain_quikscript_letters():
        glyphs = _shape_qs(left_base, right_base, right_name)
        _append_nonjoining_pair_failures(
            failures,
            f"{left_base} / {right_base} / {right_name}",
            glyphs,
            left_base,
            right_base,
        )

    for left_name, _ in _plain_quikscript_letters():
        glyphs = _shape_qs(left_name, left_base, right_base)
        _append_nonjoining_pair_failures(
            failures,
            f"{left_name} / {left_base} / {right_base}",
            glyphs,
            left_base,
            right_base,
        )

    return failures


def _collect_surrounded_nonjoining_pair_failures(
    left_base: str,
    right_base: str,
    *,
    require_full_left: bool = False,
    require_isolated_left: bool = False,
) -> list[str]:
    failures: list[str] = []
    meta_map = _compiled_meta()
    left_label = left_base[2:]
    right_label = right_base[2:]
    isolated_left_glyph = _shape_qs(left_base)[0] if require_isolated_left else None

    for outer_left_name, _ in _plain_quikscript_letters():
        for outer_right_name, _ in _plain_quikscript_letters():
            glyphs = _shape_qs(outer_left_name, left_base, right_base, outer_right_name)
            label = f"{outer_left_name} / {left_base} / {right_base} / {outer_right_name}"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                if left_meta.base_name != left_base or right_meta.base_name != right_base:
                    continue
                if require_full_left and "half" in left_meta.traits:
                    failures.append(
                        f"{label}: half-{left_label} selected before {right_label}: {glyphs}"
                    )
                if require_isolated_left and glyph_name != isolated_left_glyph:
                    failures.append(
                        f"{label}: expected isolated {left_label} glyph "
                        f"{isolated_left_glyph}, got {glyph_name} in {glyphs}"
                    )
                common = _pair_join_ys(glyphs, index)
                if common:
                    failures.append(
                        f"{label}: {left_label} joins to next glyph {glyphs[index + 1]} "
                        f"at Y={sorted(common)} in {glyphs}"
                    )

    return failures


def _collect_nonjoining_left_ligature_failures(
    left_base: str,
    ligature_base: str,
    ligature_parts: tuple[str, ...],
) -> list[str]:
    failures: list[str] = []
    meta_map = _compiled_meta()
    contexts: list[tuple[str | None, str | None]] = [(None, None)]
    contexts.extend((None, right_name) for right_name, _ in _plain_quikscript_letters())
    contexts.extend((left_name, None) for left_name, _ in _plain_quikscript_letters())
    contexts.extend(
        (outer_left_name, outer_right_name)
        for outer_left_name, _ in _plain_quikscript_letters()
        for outer_right_name, _ in _plain_quikscript_letters()
    )

    for outer_left_name, outer_right_name in contexts:
        parts = (
            ([outer_left_name] if outer_left_name else [])
            + [left_base]
            + list(ligature_parts)
            + ([outer_right_name] if outer_right_name else [])
        )
        glyphs = _shape_qs(*parts)
        label = " / ".join(parts)
        saw_pair = False

        for index, glyph_name in enumerate(glyphs[:-1]):
            left_meta = meta_map.get(glyph_name)
            right_meta = meta_map.get(glyphs[index + 1])
            if left_meta is None or right_meta is None:
                continue
            left_is_target = (
                left_meta.base_name == left_base
                or (
                    left_meta.sequence
                    and left_meta.sequence[-1] == left_base
                )
            )
            if not left_is_target or right_meta.base_name != ligature_base:
                continue
            saw_pair = True
            common = _pair_join_ys(glyphs, index)
            if common:
                failures.append(
                    f"{label}: {glyph_name} joins {glyphs[index + 1]} "
                    f"at Y={sorted(common)} in {glyphs}"
                )

        if not saw_pair and outer_left_name is None and outer_right_name is None:
            failures.append(
                f"{label}: expected {left_base} immediately before {ligature_base}, got {glyphs}"
            )

    return failures


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


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        pytest.param(
            ("qsPea", "qsOwe"),
            ["qsPea.half", "qsOwe.entry-xheight.entry-extended"],
            id="bare",
        ),
        pytest.param(
            ("qsBay", "qsPea", "qsOwe"),
            ["qsBay", "qsPea.half", "qsOwe.entry-xheight.entry-extended"],
            id="after-bay",
        ),
        pytest.param(
            ("qsTea", "qsPea", "qsOwe"),
            ["qsTea", "qsPea.half", "qsOwe.entry-xheight.entry-extended"],
            id="after-tea",
        ),
    ],
)
def test_qs_owe_after_pea_stays_left_only_at_word_end(parts: tuple[str, ...], expected: list[str]):
    assert _shape_qs(*parts) == expected


def test_qs_owe_after_pea_keeps_right_exit_with_real_follower():
    assert _shape_qs("qsPea", "qsOwe", "qsNo") == [
        "qsPea.half",
        "qsOwe.entry-xheight.exit-xheight.entry-extended",
        "qsNo",
    ]


def test_qs_owe_stays_left_only_at_word_end_after_any_plain_letter_then_pea():
    _assert_no_failures(_owe_terminal_invariant_failures())


def test_qs_utter_keeps_middle_pea_xheight_left_join_when_pea_also_joins_right():
    assert _shape_qs("qsUtter", "qsPea", "qsAwe") == [
        "qsUtter",
        "qsPea.half.entry-xheight.exit-xheight",
        "qsAwe.entry-extended",
    ]


def test_qs_ah_does_not_gain_middle_pea_xheight_left_join_when_pea_joins_right():
    assert _shape_qs("qsAh", "qsPea", "qsAwe") == [
        "qsAh",
        "qsPea.half",
        "qsAwe.entry-extended",
    ]


def test_middle_pea_xheight_left_join_is_limited_to_utter_and_may():
    _assert_no_failures(_middle_pea_xheight_left_gate_failures())


def test_qs_ing_before_thaw_uses_triply_extended_exit():
    assert _shape_qs("qsIng", "qsThaw") == [
        "qsIng.exit-triply-extended",
        "qsThaw.after-ing",
    ]


def test_qs_thaw_before_ing_uses_doubly_extended_entry_ing():
    assert _shape_qs("qsThaw", "qsIng") == [
        "qsThaw.exit-baseline",
        "qsIng.after-thaw.entry-doubly-extended",
    ]


def test_zwnj_keeps_qs_it_entryless_while_still_joining_qs_zoo():
    glyphs = _shape_qs("qsDay", ZWNJ, "qsIt", "qsZoo", "qsI", "qsRoe")

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
    glyphs = _shape_qs("qsOx", "qsNo", "qsFee")
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
    _assert_no_failures(_no_alt_selection_failures())


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        pytest.param(("qsHe", "qsYe"), ["qsHe", "qsYe"], id="he-ye"),
        pytest.param(("qsIt", "qsYe"), ["qsIt", "qsYe"], id="it-ye"),
        pytest.param(("qsPea", "qsYe"), ["qsPea", "qsYe"], id="pea-ye"),
        pytest.param(("qsTea", "qsYe"), ["qsTea", "qsYe"], id="tea-ye"),
        pytest.param(("qsThey", "qsYe"), ["qsThey", "qsYe"], id="they-ye"),
        pytest.param(("qsWay", "qsYe"), ["qsWay", "qsYe"], id="way-ye"),
        pytest.param(("qsWhy", "qsYe"), ["qsWhy", "qsYe"], id="why-ye"),
        pytest.param(("qsYe", "qsExam"), ["qsYe", "qsExam.after-ye"], id="ye-exam"),
        pytest.param(("qsYe", "qsExcite"), ["qsYe", "qsExcite.after-ye"], id="ye-excite"),
        pytest.param(("qsYe", "qsIng"), ["qsYe", "qsIng.after-ye"], id="ye-ing"),
        pytest.param(("qsYe", "qsIt"), ["qsYe", "qsIt"], id="ye-it"),
        pytest.param(("qsYe", "qsSee"), ["qsYe", "qsSee.after-ye"], id="ye-see"),
    ],
)
def test_qs_ye_sequences_keep_the_nonjoining_forms(
    parts: tuple[str, ...],
    expected: list[str],
):
    assert _shape_qs(*parts) == expected


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsOwe", "qsTea", id="owe-tea"),
        pytest.param("qsShe", "qsThaw", id="she-thaw"),
        pytest.param("qsTea", "qsOwe", id="tea-owe"),
        pytest.param("qsWay", "qsTea", id="way-tea"),
        pytest.param("qsWay", "qsThaw", id="way-thaw"),
        pytest.param("qsWhy", "qsTea", id="why-tea"),
        pytest.param("qsWhy", "qsThaw", id="why-thaw"),
    ],
)
def test_qs_nonjoining_pairs_do_not_connect(left_base: str, right_base: str):
    glyphs = _shape_qs(left_base, right_base)
    left_exits = _exit_ys(glyphs[0])
    right_entries = _entry_ys(glyphs[1])
    assert not (left_exits & right_entries), (
        f"{left_base} exits={sorted(left_exits)} should not overlap "
        f"{right_base} entries={sorted(right_entries)} in {glyphs}"
    )


def test_qs_she_stays_plain_before_qs_thaw():
    isolated = _shape_qs("qsShe")[0]
    glyphs = _shape_qs("qsShe", "qsThaw")

    assert isolated == "qsShe"
    assert glyphs[0] == isolated, f"Expected qsShe before qsThaw, got {glyphs}"
    assert _compiled_meta()[glyphs[1]].base_name == "qsThaw"


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsWay", "qsTea", id="way-before-tea"),
        pytest.param("qsWay", "qsThaw", id="way-before-thaw"),
        pytest.param("qsWhy", "qsTea", id="why-before-tea"),
        pytest.param("qsWhy", "qsThaw", id="why-before-thaw"),
    ],
)
def test_qs_way_and_qs_why_stay_full_before_right_base(left_base: str, right_base: str):
    glyphs = _shape_qs(left_base, right_base)
    left_meta = _compiled_meta()[glyphs[0]]
    assert left_meta.base_name == left_base
    assert "half" not in left_meta.traits, (
        f"Expected non-half {left_base} before {right_base}, got {glyphs[0]}"
    )


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsWay", "qsTea", id="way-before-tea"),
        pytest.param("qsWay", "qsThaw", id="way-before-thaw"),
        pytest.param("qsWhy", "qsTea", id="why-before-tea"),
        pytest.param("qsWhy", "qsThaw", id="why-before-thaw"),
    ],
)
def test_qs_way_and_qs_why_stay_full_and_nonjoining_before_right_base_in_context(
    left_base: str, right_base: str
):
    _assert_no_failures(
        _collect_surrounded_nonjoining_pair_failures(
            left_base,
            right_base,
            require_full_left=True,
        )
    )


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsOwe", "qsTea", id="owe-tea"),
        pytest.param("qsShe", "qsThaw", id="she-thaw"),
        pytest.param("qsTea", "qsOwe", id="tea-owe"),
        pytest.param("qsWay", "qsThaw", id="way-thaw"),
        pytest.param("qsWhy", "qsThaw", id="why-thaw"),
    ],
)
def test_qs_nonjoining_pairs_do_not_connect_in_context(left_base: str, right_base: str):
    _assert_no_failures(_collect_nonjoining_pair_context_failures(left_base, right_base))


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsMay", id="may"),
        pytest.param("qsNo", id="no"),
        pytest.param("qsFoot", id="foot"),
    ],
)
def test_qs_may_no_and_foot_never_join_to_qs_they_utter(left_base: str):
    _assert_no_failures(
        _collect_nonjoining_left_ligature_failures(
            left_base,
            "qsThey_qsUtter",
            ("qsThey", "qsUtter"),
        ),
        limit=None,
    )


def test_qs_she_stays_plain_and_nonjoining_before_qs_thaw_in_context():
    _assert_no_failures(
        _collect_surrounded_nonjoining_pair_failures(
            "qsShe",
            "qsThaw",
            require_isolated_left=True,
        )
    )


def test_qs_way_stays_plain_before_qs_thaw():
    isolated = _shape_qs("qsWay")[0]
    glyphs = _shape_qs("qsWay", "qsThaw")

    assert isolated == "qsWay"
    assert glyphs[0] == isolated, f"Expected qsWay before qsThaw, got {glyphs}"
    assert "half" not in _compiled_meta()[glyphs[0]].traits, (
        f"Expected non-half qsWay before qsThaw, got {glyphs[0]}"
    )
    assert _compiled_meta()[glyphs[1]].base_name == "qsThaw"


def test_qs_way_stays_plain_and_nonjoining_before_qs_thaw_in_context():
    _assert_no_failures(
        _collect_surrounded_nonjoining_pair_failures(
            "qsWay",
            "qsThaw",
            require_isolated_left=True,
        )
    )


def test_qs_may_thaw_joins_at_baseline_when_alone():
    # Sanity check: ·May·Thaw alone is still a valid baseline join.
    # The orphan-exit guard must not fire when qsThaw keeps its entry.
    glyphs = _shape_qs("qsMay", "qsThaw")
    assert glyphs == ["qsMay.exit-baseline", "qsThaw"], glyphs
    assert _pair_join_ys(glyphs, 0) == {0}


def test_qs_may_does_not_take_exit_baseline_before_qs_thaw_that_loses_entry():
    # The reported bug: in ·May·Thaw·-ing, qsThaw forward-subs to
    # qsThaw.exit-baseline (which strips its entry) so ·May must not keep
    # picking qsMay.exit-baseline. That variant would otherwise dangle
    # below the baseline into an empty join.
    glyphs = _shape_qs("qsMay", "qsThaw", "qsIng")
    assert _base_names(glyphs) == ("qsMay", "qsThaw", "qsIng"), glyphs
    assert glyphs[0] == "qsMay", glyphs
    assert "exit-baseline" not in _compiled_meta()[glyphs[0]].modifiers, glyphs
    assert not _pair_join_ys(glyphs, 0), glyphs
    # The qsThaw ~ qsIng join itself must still hold at the baseline.
    assert _pair_join_ys(glyphs, 1) == {0}


def _qs_may_thaw_orphan_failures(glyphs: list[str], label: str) -> list[str]:
    """Return a failure for every adjacent (qsMay, qsThaw) pair where qsMay
    picked a contextual ``exit-baseline`` variant even though the following
    qsThaw variant no longer accepts a baseline entry.

    Flagging the ``exit-baseline`` modifier specifically — rather than any
    mismatched exit — is intentional: qsMay's default (and ``.noentry``) form
    has a y-height exit that could never attach to qsThaw anyway. The bug is
    narrower: qsMay's lookup saw qsThaw's default baseline entry and moved
    qsMay to ``.exit-baseline`` on the assumption that a baseline join was
    about to form, and then qsThaw's own forward substitution stripped the
    entry out from under it.
    """
    failures: list[str] = []
    meta = _compiled_meta()
    for index, glyph in enumerate(glyphs[:-1]):
        left_meta = meta.get(glyph)
        right_meta = meta.get(glyphs[index + 1])
        if left_meta is None or right_meta is None:
            continue
        if left_meta.base_name != "qsMay" or right_meta.base_name != "qsThaw":
            continue
        if "exit-baseline" not in left_meta.modifiers:
            continue
        right_entries = set(right_meta.entry_ys) | {
            anchor[1] for anchor in right_meta.entry_curs_only
        }
        if 0 not in right_entries:
            failures.append(
                f"{label}: qsMay picked exit-baseline ({glyph}) but adjacent "
                f"qsThaw variant {glyphs[index + 1]} has no baseline entry "
                f"(entries Y={sorted(right_entries)}) in {glyphs}"
            )
    return failures


def _qs_no_thaw_alt_failures(glyphs: list[str], label: str) -> list[str]:
    failures: list[str] = []
    meta = _compiled_meta()
    for index, glyph in enumerate(glyphs[:-1]):
        left_meta = meta.get(glyph)
        right_meta = meta.get(glyphs[index + 1])
        if left_meta is None or right_meta is None:
            continue
        if left_meta.base_name != "qsNo" or right_meta.base_name != "qsThaw":
            continue
        if "alt" not in left_meta.traits:
            continue
        right_entries = set(right_meta.entry_ys) | {
            anchor[1] for anchor in right_meta.entry_curs_only
        }
        if 0 not in right_entries:
            failures.append(
                f"{label}: qsNo picked alt ({glyph}) but adjacent "
                f"qsThaw variant {glyphs[index + 1]} has no baseline entry "
                f"(entries Y={sorted(right_entries)}) in {glyphs}"
            )
    return failures


@pytest.mark.parametrize(
    "suffix_name",
    [pytest.param(name, id=name[2:].lower()) for name, _ in _plain_quikscript_letters()],
)
def test_qs_may_thaw_pair_never_orphans_in_left_context(suffix_name: str):
    # For every possible left-side neighbour, ·X·May·Thaw·-ing (i.e. whenever
    # qsThaw forward-subs away its entry) must not leave qsMay with an exit
    # that has nowhere to land on qsThaw.
    failures = _qs_may_thaw_orphan_failures(
        _shape_qs(suffix_name, "qsMay", "qsThaw", "qsIng"),
        f"{suffix_name} / qsMay / qsThaw / qsIng",
    )
    _assert_no_failures(failures)


@pytest.mark.parametrize(
    "left_name",
    [pytest.param(name, id=name[2:].lower()) for name, _ in _plain_quikscript_letters()],
)
@pytest.mark.parametrize(
    "right_name",
    [pytest.param(name, id=name[2:].lower()) for name, _ in _plain_quikscript_letters()],
)
def test_qs_may_thaw_ing_surrounded_is_never_orphaned(left_name: str, right_name: str):
    # Surround ·May·Thaw·-ing with every letter pair. qsMay must never dangle
    # an orphan exit into qsThaw, regardless of outer context.
    failures = _qs_may_thaw_orphan_failures(
        _shape_qs(left_name, "qsMay", "qsThaw", "qsIng", right_name),
        f"{left_name} / qsMay / qsThaw / qsIng / {right_name}",
    )
    _assert_no_failures(failures)


def test_qs_may_thaw_stays_isolated_across_zwnj():
    # A ZWNJ before qsMay shapes it to qsMay.noentry; the same forward-sub
    # path would otherwise promote it to qsMay.exit-baseline before
    # qsThaw → qsThaw.exit-baseline, leaving the same orphan exit.
    chars = _char_map()
    text = chars["qsTea"] + ZWNJ + chars["qsMay"] + chars["qsThaw"] + chars["qsIng"]
    glyphs = _shape(text)
    may_index = _find_base_index(glyphs, "qsMay")
    assert may_index is not None, glyphs
    may_glyph = glyphs[may_index]
    assert "exit-baseline" not in _compiled_meta()[may_glyph].modifiers, glyphs
    _assert_no_failures(
        _qs_may_thaw_orphan_failures(glyphs, "qsTea / ZWNJ / qsMay / qsThaw / qsIng")
    )


@pytest.mark.parametrize(
    "ing_variant",
    [
        pytest.param("qsIng", id="plain-ing"),
    ],
)
def test_qs_may_thaw_before_ing_variants_stays_plain(ing_variant: str):
    # Every variant of qsIng that triggers qsThaw's forward sub must keep
    # qsMay out of exit-baseline.
    glyphs = _shape_qs("qsMay", "qsThaw", ing_variant)
    assert glyphs[0] == "qsMay", glyphs
    _assert_no_failures(
        _qs_may_thaw_orphan_failures(glyphs, f"qsMay / qsThaw / {ing_variant}")
    )


def test_qs_no_does_not_take_alt_before_qs_thaw_that_loses_entry():
    glyphs = _shape_qs("qsNo", "qsThaw", "qsIng")
    assert _base_names(glyphs) == ("qsNo", "qsThaw", "qsIng"), glyphs
    assert glyphs[0] == "qsNo", glyphs
    assert "alt" not in _compiled_meta()[glyphs[0]].traits, glyphs
    assert not _pair_join_ys(glyphs, 0), glyphs
    assert _pair_join_ys(glyphs, 1) == {0}


@pytest.mark.parametrize(
    "left_name",
    [pytest.param(name, id=name[2:].lower()) for name, _ in _plain_quikscript_letters()],
)
@pytest.mark.parametrize(
    "right_name",
    [pytest.param(name, id=name[2:].lower()) for name, _ in _plain_quikscript_letters()],
)
def test_qs_no_thaw_ing_surrounded_does_not_select_alt(left_name: str, right_name: str):
    failures = _qs_no_thaw_alt_failures(
        _shape_qs(left_name, "qsNo", "qsThaw", "qsIng", right_name),
        f"{left_name} / qsNo / qsThaw / qsIng / {right_name}",
    )
    _assert_no_failures(failures)


def test_qs_no_thaw_stays_non_alt_across_zwnj():
    chars = _char_map()
    text = chars["qsTea"] + ZWNJ + chars["qsNo"] + chars["qsThaw"] + chars["qsIng"]
    glyphs = _shape(text)
    no_index = _find_base_index(glyphs, "qsNo")
    assert no_index is not None, glyphs
    no_glyph = glyphs[no_index]
    assert "alt" not in _compiled_meta()[no_glyph].traits, glyphs
    _assert_no_failures(
        _qs_no_thaw_alt_failures(glyphs, "qsTea / ZWNJ / qsNo / qsThaw / qsIng")
    )


def test_qs_excite_tea_connect_at_baseline():
    glyphs = _shape_qs("qsExcite", "qsTea")
    excite_exits = _exit_ys(glyphs[0])
    tea_entries = _entry_ys(glyphs[1])
    assert 0 in (excite_exits & tea_entries), (
        f"Excite exits={sorted(excite_exits)} should overlap Tea entries={sorted(tea_entries)} at Y=0 in {glyphs}"
    )


@pytest.mark.parametrize(
    "right_base",
    [
        pytest.param("qsAh", id="before-ah"),
        pytest.param("qsAwe", id="before-awe"),
    ],
)
def test_qs_excite_tea_keeps_left_join_when_the_follower_still_supports_it(right_base: str):
    pair = _shape_qs("qsExcite", "qsTea")
    triple = _shape_qs("qsExcite", "qsTea", right_base)
    assert _base_names(pair) == ("qsExcite", "qsTea")
    assert _base_names(triple[:2]) == ("qsExcite", "qsTea")
    _assert_join_preserved(
        f"qsExcite / qsTea / {right_base}",
        pair,
        triple,
        pair_index_in_triple=0,
    )


def test_qs_excite_tea_does_not_keep_the_baseline_exit_before_qs_ox():
    glyphs = _shape_qs("qsExcite", "qsTea", "qsOx")
    assert _base_names(glyphs) == ("qsExcite", "qsTea", "qsOx")
    assert not _pair_join_ys(glyphs, 0)
    assert _pair_join_ys(glyphs, 1) == {5}
    assert not _exit_ys(glyphs[0]), glyphs


def test_qs_excite_tea_keeps_the_baseline_join_before_qs_tea():
    pair = _shape_qs("qsExcite", "qsTea")
    triple = _shape_qs("qsExcite", "qsTea", "qsTea")
    assert _base_names(pair) == ("qsExcite", "qsTea")
    assert _base_names(triple) == ("qsExcite", "qsTea", "qsTea")
    _assert_join_preserved("qsExcite / qsTea / qsTea", pair, triple, pair_index_in_triple=0)
    assert not _pair_join_ys(triple, 1)


def test_qs_excite_tea_does_not_keep_the_baseline_exit_before_qs_out():
    pair = _shape_qs("qsTea", "qsOut")
    triple = _shape_qs("qsExcite", "qsTea", "qsOut")
    assert _base_names(pair) == ("qsTea", "qsOut")
    assert _base_names(triple) == ("qsExcite", "qsTea", "qsOut")
    assert triple[0] == "qsExcite"
    assert not _pair_join_ys(triple, 0)
    _assert_join_preserved("qsExcite / qsTea / qsOut", pair, triple[1:], pair_index_in_triple=0)
    assert not _exit_ys(triple[0]), triple


def test_qs_excite_tea_does_not_keep_the_baseline_exit_before_qs_oy():
    pair = _shape_qs("qsTea", "qsOy")
    triple = _shape_qs("qsExcite", "qsTea", "qsOy")
    assert pair == ["qsTea_qsOy"]
    assert triple == ["qsExcite", "qsTea_qsOy"]
    assert not _exit_ys(triple[0]), triple


@pytest.mark.parametrize(
    "right_base",
    [
        pytest.param("qsRoe", id="before-roe"),
        pytest.param("qsDay", id="before-day"),
    ],
)
def test_qs_out_tea_prefers_the_ligature_before_nonjoining_followers(right_base: str):
    pair = _shape_qs("qsOut", "qsTea")
    triple = _shape_qs("qsOut", "qsTea", right_base)
    assert pair == ["qsOut_qsTea"]
    assert triple == ["qsOut_qsTea", right_base]


def test_qs_et_tea_keeps_the_qs_tea_qs_oy_ligature():
    glyphs = _shape_qs("qsEt", "qsTea", "qsOy")
    assert glyphs == ["qsEt", "qsTea_qsOy"]


@pytest.mark.parametrize(
    "right_base",
    [
        pytest.param("qsAh", id="before-ah"),
        pytest.param("qsOut", id="before-out"),
        pytest.param("qsMay", id="before-may"),
        pytest.param("qsIng", id="before-ing"),
        pytest.param("qsVie", id="before-vie"),
        pytest.param("qsDay", id="before-day"),
    ],
)
def test_qs_et_tea_keeps_only_the_left_baseline_join_in_plain_right_contexts(right_base: str):
    glyphs = _shape_qs("qsEt", "qsTea", right_base)
    assert glyphs == ["qsEt", "qsTea.entry-baseline", right_base]
    assert _pair_join_ys(glyphs, 0) == {0}
    assert not _pair_join_ys(glyphs, 1)


def test_qs_et_tea_can_double_join_at_baseline_in_ss05():
    glyphs = _shape_qs(
        "qsBay",
        "qsEt",
        "qsTea",
        "qsUtter",
        "qsRoe",
        features=_SS05_FEATURE,
    )
    assert glyphs == ["qsBay", "qsEt", "qsTea.entry-baseline.exit-baseline", "qsUtter", "qsRoe"]
    assert not _pair_join_ys(glyphs, 0)
    assert _pair_join_ys(glyphs, 1) == {0}
    assert _pair_join_ys(glyphs, 2) == {0}
    assert _pair_join_ys(glyphs, 3) == {5}


def test_qs_it_excite_does_not_force_qs_tea_out_of_half_before_qs_it():
    glyphs = _shape_qs("qsIt", "qsExcite", "qsTea", "qsIt")
    assert _base_names(glyphs) == ("qsIt", "qsExcite", "qsTea", "qsIt")
    assert _pair_join_ys(glyphs, 0) == {0}
    assert not _pair_join_ys(glyphs, 1)
    assert _pair_join_ys(glyphs, 2) == {5}
    assert "half" in _compiled_meta()[glyphs[2]].traits, glyphs


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsTea", id="tea"),
        pytest.param("qsPea", id="pea"),
        pytest.param("qsYe", id="ye"),
    ],
)
def test_nonjoining_left_context_preserves_qs_excite_qs_ah_join(left_base: str):
    pair = _shape_qs("qsExcite", "qsAh")
    triple = _shape_qs(left_base, "qsExcite", "qsAh")
    assert _base_names(pair) == ("qsExcite", "qsAh")
    assert _base_names(triple[1:]) == ("qsExcite", "qsAh")
    assert not _pair_join_ys(triple, 0)
    _assert_join_preserved(
        f"{left_base} / qsExcite / qsAh",
        pair,
        triple,
        pair_index_in_triple=1,
    )


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsTea", "qsExcite", id="tea-excite"),
        pytest.param("qsExam", "qsTea", id="exam-tea"),
        pytest.param("qsTea", "qsExam", id="tea-exam"),
        pytest.param("qsTea", "qsThaw", id="tea-thaw"),
    ],
)
def test_qs_nonjoining_pairs_keep_their_edges_separate(left_base: str, right_base: str):
    glyphs = _shape_qs(left_base, right_base)
    left_exits = _exit_ys(glyphs[0])
    right_entries = _entry_ys(glyphs[1])
    assert not (left_exits & right_entries), (
        f"{left_base} exits={sorted(left_exits)} should not overlap "
        f"{right_base} entries={sorted(right_entries)} in {glyphs}"
    )


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsTea", "qsExcite", id="tea-excite"),
        pytest.param("qsExam", "qsTea", id="exam-tea"),
        pytest.param("qsTea", "qsExam", id="tea-exam"),
        pytest.param("qsTea", "qsThaw", id="tea-thaw"),
    ],
)
def test_qs_nonjoining_pairs_stay_nonjoining_in_context(left_base: str, right_base: str):
    _assert_no_failures(_collect_nonjoining_pair_context_failures(left_base, right_base))


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


def _out_tea_prefers_the_ligature_over_right_joins_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()

    for right_name, right_char in _plain_quikscript_letters():
        glyphs = _shape(chars["qsOut"] + chars["qsTea"] + right_char)
        if not glyphs or glyphs[0] == "qsOut_qsTea":
            continue
        if len(glyphs) < 3:
            continue
        if _base_names(glyphs)[:2] != ("qsOut", "qsTea"):
            continue
        if not _exit_ys(glyphs[1]):
            continue
        failures.append(
            f"qsOut / qsTea / {right_name}: expected qsOut_qsTea to win over the right join, "
            f"but got {glyphs} with tea exits {sorted(_exit_ys(glyphs[1]))}"
        )

    return failures


def test_qs_out_tea_prefers_the_ligature_over_right_joins():
    failures = _out_tea_prefers_the_ligature_over_right_joins_failures()
    assert not failures, "\n".join(failures[:50])


def _et_tea_keeps_the_left_join_and_blocks_right_join_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()

    for right_name, right_char in _plain_quikscript_letters():
        glyphs = _shape(chars["qsEt"] + chars["qsTea"] + right_char)
        if right_name == "qsOy":
            if glyphs != ["qsEt", "qsTea_qsOy"]:
                failures.append(
                    f"qsEt / qsTea / qsOy: expected ['qsEt', 'qsTea_qsOy'], got {glyphs}"
                )
            continue

        if len(glyphs) != 3:
            failures.append(
                f"qsEt / qsTea / {right_name}: expected three glyphs with plain qsTea sequencing, got {glyphs}"
            )
            continue
        if _base_names(glyphs) != ("qsEt", "qsTea", right_name):
            failures.append(
                f"qsEt / qsTea / {right_name}: unexpected base sequence {_base_names(glyphs)} in {glyphs}"
            )
            continue

        left_ys = _pair_join_ys(glyphs, 0)
        right_ys = _pair_join_ys(glyphs, 1)
        tea_exits = _exit_ys(glyphs[1])
        if left_ys != {0} or right_ys or tea_exits:
            failures.append(
                f"qsEt / qsTea / {right_name}: expected Et to keep the left baseline join and Tea to stop on the right, "
                f"but got {glyphs} with left Ys={sorted(left_ys)}, right Ys={sorted(right_ys)}, tea exits={sorted(tea_exits)}"
            )

    return failures


def test_qs_et_tea_only_keeps_the_left_baseline_join_except_before_qs_oy():
    failures = _et_tea_keeps_the_left_join_and_blocks_right_join_failures()
    assert not failures, "\n".join(failures[:50])


def _et_tea_nonjoining_right_context_keeps_right_glyph_plain_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()

    for right_name, right_char in _plain_quikscript_letters():
        if right_name == "qsOy":
            continue
        glyphs = _shape(chars["qsEt"] + chars["qsTea"] + right_char)
        if len(glyphs) != 3:
            continue
        if _base_names(glyphs) != ("qsEt", "qsTea", right_name):
            continue
        if _pair_join_ys(glyphs, 1):
            continue

        solo = _shape(right_char)
        if glyphs[2:] == solo:
            continue

        right_meta = _compiled_meta()[glyphs[2]]
        if "entry" not in right_meta.compat_assertions and "half" not in right_meta.traits:
            continue

        failures.append(
            f"qsEt / qsTea / {right_name}: nonjoining right glyph changed from {solo} to {glyphs[2:]}"
        )

    return failures


def test_qs_et_tea_nonjoining_right_context_keeps_right_glyph_plain():
    failures = _et_tea_nonjoining_right_context_keeps_right_glyph_plain_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_see_pea_keeps_the_y6_join():
    assert _shape("\uE65A\uE650") == [
        "qsSee.exit-y6",
        "qsPea.entry-y6",
    ]


def test_qs_utter_alt_variants_always_keep_the_joins_they_require():
    failures = _utter_alt_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_tea_before_qs_i_extends_exit():
    chars = _char_map()
    glyphs = _shape(chars["qsTea"] + chars["qsI"])
    assert glyphs == ["qsTea.entry-top.exit-baseline.exit-extended", "qsI"]
    assert _pair_join_ys(glyphs, 0) == {0}


def test_qs_see_tea_i_extends_exit():
    chars = _char_map()
    glyphs = _shape(chars["qsSee"] + chars["qsTea"] + chars["qsI"])
    assert glyphs == [
        "qsSee",
        "qsTea.entry-top.exit-baseline.exit-extended",
        "qsI",
    ]
    assert _pair_join_ys(glyphs, 0) == {8}
    assert _pair_join_ys(glyphs, 1) == {0}


def test_qs_fee_tea_i_extends_exit():
    chars = _char_map()
    glyphs = _shape(chars["qsFee"] + chars["qsTea"] + chars["qsI"])
    assert glyphs[1] == "qsTea.entry-top.exit-baseline.exit-extended"
    assert glyphs[2] == "qsI"
    assert _pair_join_ys(glyphs, 1) == {0}


def test_qs_et_tea_i_preserves_left_only_invariant():
    chars = _char_map()
    glyphs = _shape(chars["qsEt"] + chars["qsTea"] + chars["qsI"])
    assert glyphs == ["qsEt", "qsTea.entry-baseline", "qsI"]
    assert _pair_join_ys(glyphs, 0) == {0}
    assert not _pair_join_ys(glyphs, 1)


def test_qs_et_tea_i_extends_exit_in_ss05():
    chars = _char_map()
    glyphs = _shape_with_features(
        chars["qsEt"] + chars["qsTea"] + chars["qsI"],
        (("ss05", True),),
    )
    assert glyphs == [
        "qsEt",
        "qsTea.entry-baseline.exit-baseline.exit-extended",
        "qsI",
    ]
    assert _pair_join_ys(glyphs, 0) == {0}
    assert _pair_join_ys(glyphs, 1) == {0}


def test_qs_i_before_qs_tea_unchanged_by_forward_extension():
    chars = _char_map()
    glyphs = _shape(chars["qsI"] + chars["qsTea"])
    tea_meta = _compiled_meta()[glyphs[1]]
    assert tea_meta.base_name == "qsTea"
    assert "exit-extended" not in tea_meta.modifiers


# ---------------------------------------------------------------------------
# ·Way·Day must always use full-height Way and full-height Day.
#
# Regression guard against a 2-glyph preferred-lookahead FEA rule that used to
# substitute qsWay.half whenever the third glyph had only a y=5 entry, even if
# the middle glyph (qsDay) could not actually bridge qsWay.half's y=0 exit to
# the third glyph's y=5 entry. That produced qsWay.half·qsDay.half·X with no
# cursive join between Day.half and X.
# ---------------------------------------------------------------------------


_DAY_PAIR_LIGATURES = frozenset({
    # (day_prefix_base, follower_base) pairs that combine into a ligature,
    # consuming qsDay into qsDay_qs<follower>. In those outputs there is no
    # standalone qsDay glyph to inspect.
    ("qsDay", "qsEat"),
    ("qsDay", "qsUtter"),
})


def test_qs_way_day_joins_at_xheight():
    chars = _char_map()
    glyphs = _shape(chars["qsWay"] + chars["qsDay"])
    assert len(glyphs) == 2, f"Expected 2 glyphs, got {glyphs}"
    meta = _compiled_meta()
    way_meta = meta[glyphs[0]]
    day_meta = meta[glyphs[1]]
    assert way_meta.base_name == "qsWay"
    assert day_meta.base_name == "qsDay"
    assert "half" not in way_meta.traits, f"Expected full Way, got {glyphs[0]}"
    assert "half" not in day_meta.traits, f"Expected full Day, got {glyphs[1]}"
    assert 5 in _exit_ys(glyphs[0]) & _entry_ys(glyphs[1]), (
        f"Expected ·Way·Day to join at y=5, got exits={_exit_ys(glyphs[0])} "
        f"entries={_entry_ys(glyphs[1])}"
    )


def _way_day_base_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    way = chars["qsWay"]
    day = chars["qsDay"]
    meta_map = _compiled_meta()

    for right_name, right_char in _plain_quikscript_letters():
        text = way + day + right_char
        glyphs = _shape(text)
        label = f"qsWay / qsDay / {right_name}"

        way_meta = meta_map.get(glyphs[0])
        if way_meta is None or way_meta.base_name != "qsWay":
            failures.append(f"{label}: first glyph is not qsWay: {glyphs}")
            continue
        if "half" in way_meta.traits:
            failures.append(f"{label}: half-Way selected: {glyphs}")

        if len(glyphs) < 2:
            failures.append(f"{label}: too few glyphs: {glyphs}")
            continue

        day_meta = meta_map.get(glyphs[1])
        if day_meta is None:
            failures.append(f"{label}: unknown second glyph: {glyphs}")
            continue

        # Day may legitimately be consumed into a ligature with the follower.
        if day_meta.sequence and ("qsDay", right_name) in _DAY_PAIR_LIGATURES:
            continue
        if day_meta.base_name != "qsDay":
            failures.append(f"{label}: second glyph is not qsDay: {glyphs}")
            continue
        if "half" in day_meta.traits:
            failures.append(f"{label}: half-Day selected: {glyphs}")

    return failures


def test_qs_way_day_never_half():
    failures = _way_day_base_failures()
    assert not failures, "\n".join(failures[:50])


def _way_day_context_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    way = chars["qsWay"]
    day = chars["qsDay"]
    meta_map = _compiled_meta()

    # Clusters for the L·Way·Day·R input: L=0, Way=1, Day=2, R=3. Only flag
    # glyphs whose cluster is 1 (the Way we are testing) or 2 (the Day we
    # are testing). A qsDay that shows up at cluster 3 is the R position
    # and is unrelated to the Way·Day invariant under test.
    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            text = left_char + way + day + right_char
            shaped = _shape_with_clusters(text)
            label = f"{left_name} / qsWay / qsDay / {right_name}"

            for glyph_name, cluster in shaped:
                if cluster not in (1, 2):
                    continue
                g_meta = meta_map.get(glyph_name)
                if g_meta is None:
                    continue
                # qsDay consumed into qsDay_qs<X> keeps its full-height body;
                # the ligature is a separate form.
                if g_meta.sequence and g_meta.sequence[0] == "qsDay":
                    continue
                if g_meta.base_name == "qsWay" and "half" in g_meta.traits:
                    failures.append(
                        f"{label}: half-Way selected: {[g for g, _ in shaped]}"
                    )
                if (
                    g_meta.base_name == "qsDay"
                    and cluster == 2
                    and "half" in g_meta.traits
                ):
                    failures.append(
                        f"{label}: half-Day selected: {[g for g, _ in shaped]}"
                    )

    return failures


def test_qs_way_day_never_half_in_context():
    failures = _way_day_context_failures()
    assert not failures, "\n".join(failures[:50])


def _non_bridging_middle_bases() -> list[tuple[str, str]]:
    """Quikscript bases that are *multi-entry* (accept both y=0 and y=5 entry
    across their variants) but have no single variant combining y=0 entry
    with y=5 exit — i.e. cannot bridge Way.half's y=0 exit up to a
    y=5-only-entry follower, so the 2-glyph preferred-lookahead must not
    fire for them.

    Single-y=0-entry letters (qsAh, qsExam, qsExcite, …) are excluded: for
    those the 1-glyph rule `sub qsWay' @entry_only_y0 by qsWay.half;`
    correctly fires and selecting half-Way is fine.
    """
    meta_map = _compiled_meta()
    variants_by_base: dict[str, list] = {}
    for glyph_meta in meta_map.values():
        variants_by_base.setdefault(glyph_meta.base_name, []).append(glyph_meta)

    result: list[tuple[str, str]] = []
    for base_name, base_char in _plain_quikscript_letters():
        variants = variants_by_base.get(base_name, [])
        can_enter_y0 = any(0 in v.entry_ys for v in variants)
        can_enter_y5 = any(5 in v.entry_ys for v in variants)
        can_bridge_y0_to_y5 = any(
            0 in v.entry_ys and 5 in v.exit_ys for v in variants
        )
        if can_enter_y0 and can_enter_y5 and not can_bridge_y0_to_y5:
            result.append((base_name, base_char))
    return result


def _way_not_half_before_non_bridging_failures() -> list[str]:
    """For every non-bridging middle M and every right-context X, ·Way·M·X
    must not pick half-Way — the Way.half → M → X chain cannot actually join
    at the x-height entry X needs.
    """
    failures: list[str] = []
    chars = _char_map()
    way = chars["qsWay"]
    meta_map = _compiled_meta()

    for middle_name, middle_char in _non_bridging_middle_bases():
        for right_name, right_char in _plain_quikscript_letters():
            text = way + middle_char + right_char
            glyphs = _shape(text)
            first_meta = meta_map.get(glyphs[0])
            if first_meta is None:
                continue
            # qsWay+qsUtter ligates into qsWay_qsUtter; that's a full-size Way
            # body and not a `.half` variant, so skip sequences where qsWay
            # is consumed.
            if first_meta.sequence and first_meta.sequence[0] == "qsWay":
                continue
            if first_meta.base_name != "qsWay":
                continue
            if "half" in first_meta.traits:
                failures.append(
                    f"qsWay / {middle_name} / {right_name}: half-Way selected "
                    f"before a non-bridging middle: {glyphs}"
                )
    return failures


def test_qs_way_full_before_any_non_bridging_middle():
    failures = _way_not_half_before_non_bridging_failures()
    assert not failures, "\n".join(failures[:50])


# ---------------------------------------------------------------------------
# ·Owe must never join onto a following ·Day (or any ligature starting with
# ·Day) in the default shaping. Stylistic set ss07 restores the join for
# users who want Read's manual-style ·Owe·Day rendering back.
# ---------------------------------------------------------------------------


_DAY_BASES = frozenset({"qsDay", "qsDay_qsUtter", "qsDay_qsEat"})


def _owe_day_failures_in(glyphs: list[str], label: str) -> list[str]:
    failures: list[str] = []
    meta_map = _compiled_meta()
    for index, glyph_name in enumerate(glyphs):
        if index + 1 >= len(glyphs):
            continue
        left_meta = meta_map.get(glyph_name)
        right_meta = meta_map.get(glyphs[index + 1])
        if left_meta is None or right_meta is None:
            continue
        if left_meta.base_name != "qsOwe":
            continue
        if right_meta.base_name not in _DAY_BASES:
            continue
        common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
        if common:
            failures.append(
                f"{label}: Owe joins {right_meta.base_name} at Y={sorted(common)} in {glyphs}"
            )
    return failures


def test_qs_owe_day_do_not_connect():
    chars = _char_map()
    glyphs = _shape(chars["qsOwe"] + chars["qsDay"])
    failures = _owe_day_failures_in(glyphs, "bare qsOwe / qsDay")
    assert not failures, "\n".join(failures)


def _owe_day_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    owe = chars["qsOwe"]
    day = chars["qsDay"]

    for right_name, right_char in _plain_quikscript_letters():
        text = owe + day + right_char
        failures.extend(
            _owe_day_failures_in(_shape(text), f"qsOwe / qsDay / {right_name}")
        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + owe + day
        failures.extend(
            _owe_day_failures_in(_shape(text), f"{left_name} / qsOwe / qsDay")
        )

    return failures


def test_qs_owe_day_do_not_connect_in_context():
    failures = _owe_day_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_owe_day_utter_ligature_does_not_connect():
    chars = _char_map()
    glyphs = _shape(chars["qsOwe"] + chars["qsDay"] + chars["qsUtter"])
    failures = _owe_day_failures_in(glyphs, "qsOwe / qsDay / qsUtter")
    assert not failures, "\n".join(failures)


def test_qs_owe_day_eat_ligature_does_not_connect():
    chars = _char_map()
    glyphs = _shape(chars["qsOwe"] + chars["qsDay"] + chars["qsEat"])
    failures = _owe_day_failures_in(glyphs, "qsOwe / qsDay / qsEat")
    assert not failures, "\n".join(failures)

def _owe_day_joins_at_y5_in(glyphs: list[str], label: str) -> list[str]:
    """Return failures for positions where Owe should join a Day-base at Y=5 but doesn't."""
    failures: list[str] = []
    meta_map = _compiled_meta()
    found_pair = False
    for index, glyph_name in enumerate(glyphs):
        if index + 1 >= len(glyphs):
            continue
        left_meta = meta_map.get(glyph_name)
        right_meta = meta_map.get(glyphs[index + 1])
        if left_meta is None or right_meta is None:
            continue
        if left_meta.base_name != "qsOwe":
            continue
        if right_meta.base_name not in _DAY_BASES:
            continue
        found_pair = True
        common = _exit_ys(glyph_name) & _entry_ys(glyphs[index + 1])
        if 5 not in common:
            failures.append(
                f"{label}: Owe does not join {right_meta.base_name} at Y=5 "
                f"(exit_ys={sorted(_exit_ys(glyph_name))}, "
                f"entry_ys={sorted(_entry_ys(glyphs[index + 1]))}) in {glyphs}"
            )
    if not found_pair:
        failures.append(f"{label}: expected an Owe-followed-by-Day pair, got {glyphs}")
    return failures


def test_qs_owe_day_connects_under_ss07():
    chars = _char_map()
    glyphs = _shape_with_features(chars["qsOwe"] + chars["qsDay"], _SS07_FEATURE)
    failures = _owe_day_joins_at_y5_in(glyphs, "bare qsOwe / qsDay (ss07)")
    assert not failures, "\n".join(failures)


def _owe_day_ss07_invariant_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    owe = chars["qsOwe"]
    day = chars["qsDay"]

    for right_name, right_char in _plain_quikscript_letters():
        text = owe + day + right_char
        failures.extend(
            _owe_day_joins_at_y5_in(
                _shape_with_features(text, _SS07_FEATURE),
                f"qsOwe / qsDay / {right_name} (ss07)",
            )
        )

    for left_name, left_char in _plain_quikscript_letters():
        text = left_char + owe + day
        failures.extend(
            _owe_day_joins_at_y5_in(
                _shape_with_features(text, _SS07_FEATURE),
                f"{left_name} / qsOwe / qsDay (ss07)",
            )
        )

    return failures


def test_qs_owe_day_connects_under_ss07_in_context():
    failures = _owe_day_ss07_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_qs_owe_day_utter_ligature_connects_under_ss07():
    chars = _char_map()
    glyphs = _shape_with_features(
        chars["qsOwe"] + chars["qsDay"] + chars["qsUtter"], _SS07_FEATURE
    )
    failures = _owe_day_joins_at_y5_in(glyphs, "qsOwe / qsDay / qsUtter (ss07)")
    assert not failures, "\n".join(failures)


def test_qs_owe_day_eat_ligature_connects_under_ss07():
    chars = _char_map()
    glyphs = _shape_with_features(
        chars["qsOwe"] + chars["qsDay"] + chars["qsEat"], _SS07_FEATURE
    )
    failures = _owe_day_joins_at_y5_in(glyphs, "qsOwe / qsDay / qsEat (ss07)")
    assert not failures, "\n".join(failures)


_GAY_CONTEXTS = (
    "qsPea", "qsBay", "qsTea", "qsDay", "qsKey", "qsFee", "qsVie",
    "qsSee", "qsZoo", "qsShe", "qsMay", "qsNo", "qsLow", "qsRoe",
    "qsEat", "qsAt", "qsAh", "qsOx", "qsOwe", "qsOoze",
)

_GAY_JOINING_PAIR_CASES = (
    pytest.param(
        "qsTea",
        ["qsGay.exit-baseline.exit-extended", "qsTea.entry-baseline"],
        id="tea",
    ),
    pytest.param(
        "qsIt",
        ["qsGay.exit-baseline.exit-extended", "qsIt.entry-baseline"],
        id="it",
    ),
    pytest.param(
        "qsI",
        ["qsGay.exit-baseline.exit-extended", "qsI"],
        id="i",
    ),
    pytest.param(
        "qsExam",
        ["qsGay.exit-baseline.exit-extended", "qsExam"],
        id="exam",
    ),
)

_GAY_CONTEXT_JOIN_CASES = (
    pytest.param("qsIt", "qsIt.entry-baseline", id="it"),
    pytest.param("qsI", None, id="i"),
    pytest.param("qsExam", None, id="exam"),
)

_GAY_CONTEXT_JOIN_TARGETS = (
    pytest.param("qsIt", id="it"),
    pytest.param("qsI", id="i"),
    pytest.param("qsExam", id="exam"),
)

_GAY_NONJOINING_TARGETS = (
    pytest.param("qsExcite", id="excite"),
    pytest.param("qsOoze", id="ooze"),
)

_GAY_BASELINE_EXTENDED_TARGETS = (
    pytest.param("qsTea", id="tea"),
    pytest.param("qsIt", id="it"),
    pytest.param("qsI", id="i"),
    pytest.param("qsExam", id="exam"),
)


def _append_gay_joining_context_failures(
    failures: list[str],
    label: str,
    glyphs: list[str],
    target_base: str,
    *,
    expected_target_glyph: str | None = None,
) -> None:
    gay_index = _find_base_index(glyphs, "qsGay")
    if gay_index is None:
        failures.append(f"{label}: no qsGay glyph found ({glyphs!r})")
        return
    if glyphs[gay_index] != "qsGay.exit-baseline.exit-extended":
        failures.append(
            f"{label}: expected qsGay.exit-baseline.exit-extended, "
            f"got {glyphs[gay_index]} (full: {glyphs!r})"
        )
        return
    if gay_index + 1 >= len(glyphs):
        failures.append(f"{label}: {target_base} did not follow qsGay ({glyphs!r})")
        return

    target_name = glyphs[gay_index + 1]
    target_meta = _compiled_meta().get(target_name)
    if target_meta is None or target_meta.base_name != target_base:
        failures.append(f"{label}: {target_base} did not follow qsGay ({glyphs!r})")
        return

    if expected_target_glyph is not None and target_name != expected_target_glyph:
        failures.append(
            f"{label}: expected {expected_target_glyph} after qsGay, "
            f"got {target_name} (full: {glyphs!r})"
        )

    entry_ys = _entry_ys(target_name)
    if entry_ys and 0 not in entry_ys:
        failures.append(
            f"{label}: {target_base} ({target_name}) has an entry anchor but "
            f"not at baseline; entry_ys={entry_ys} (full: {glyphs!r})"
        )


def _append_gay_nonjoining_context_failures(
    failures: list[str],
    label: str,
    glyphs: list[str],
    target_base: str,
) -> None:
    gay_index = _find_base_index(glyphs, "qsGay")
    if gay_index is None:
        failures.append(f"{label}: no qsGay glyph found ({glyphs!r})")
        return
    if gay_index + 1 >= len(glyphs):
        failures.append(f"{label}: {target_base} did not follow qsGay ({glyphs!r})")
        return

    target_name = glyphs[gay_index + 1]
    target_meta = _compiled_meta().get(target_name)
    if target_meta is None or target_meta.base_name != target_base:
        failures.append(f"{label}: {target_base} did not follow qsGay ({glyphs!r})")
        return

    shared = _exit_ys(glyphs[gay_index]) & _entry_ys(target_name)
    if shared:
        failures.append(
            f"{label}: qsGay ({glyphs[gay_index]}) and {target_base} ({target_name}) "
            f"share y={shared}; should not join (full: {glyphs!r})"
        )


@pytest.mark.parametrize(("target_base", "expected"), _GAY_JOINING_PAIR_CASES)
def test_qs_gay_extends_before_selected_targets(target_base: str, expected: list[str]):
    assert _shape_qs("qsGay", target_base) == expected


@pytest.mark.parametrize(
    ("target_base", "expected_target_glyph"),
    _GAY_CONTEXT_JOIN_CASES,
)
def test_qs_gay_joining_targets_keep_extension_in_any_leading_context(
    target_base: str,
    expected_target_glyph: str | None,
):
    failures: list[str] = []
    for leader in _GAY_CONTEXTS:
        glyphs = _shape_qs(leader, "qsGay", target_base)
        _append_gay_joining_context_failures(
            failures,
            f"{leader} + qsGay + {target_base}",
            glyphs,
            target_base,
            expected_target_glyph=expected_target_glyph,
        )
    _assert_no_failures(failures, limit=None)


@pytest.mark.parametrize("target_base", _GAY_CONTEXT_JOIN_TARGETS)
def test_qs_gay_joining_targets_keep_extension_in_any_trailing_context(target_base: str):
    failures: list[str] = []
    for follower in _GAY_CONTEXTS:
        glyphs = _shape_qs("qsGay", target_base, follower)
        _append_gay_joining_context_failures(
            failures,
            f"qsGay + {target_base} + {follower}",
            glyphs,
            target_base,
        )
    _assert_no_failures(failures, limit=None)


@pytest.mark.parametrize("target_base", _GAY_CONTEXT_JOIN_TARGETS)
def test_qs_gay_joining_targets_share_shifted_baseline_anchor(target_base: str):
    glyphs = _shape_qs("qsGay", target_base)
    meta = _compiled_meta()
    gay = meta[glyphs[0]]
    target = meta[glyphs[1]]
    gay_exit_ys = {anchor[1] for anchor in gay.exit}
    target_entry_ys = {anchor[1] for anchor in (*target.entry, *target.entry_curs_only)}
    assert gay_exit_ys & target_entry_ys, (
        f"qsGay.exit {gay.exit} and {target_base}.entry {target.entry} share no y-coordinate"
    )
    assert gay.exit == ((7, 0),), (
        f"qsGay exit anchor should be shifted one pixel right by the extension, "
        f"got {gay.exit}"
    )


@pytest.mark.parametrize("target_base", _GAY_BASELINE_EXTENDED_TARGETS)
def test_qs_gay_exit_baseline_extended_targets_include_joining_followers(target_base):
    meta = _compiled_meta()
    variant = meta["qsGay.exit-baseline.exit-extended"]
    assert variant.exit == ((7, 0),), (
        f"qsGay.exit-baseline.exit-extended should exit at x=7, y=0; got {variant.exit}"
    )
    assert target_base in set(variant.before), (
        f"qsGay.exit-baseline.exit-extended should name {target_base} in `before`; "
        f"got {variant.before}"
    )


def test_qs_gay_exit_xheight_extended_exists_for_it():
    meta = _compiled_meta()
    variant = meta["qsGay.exit-xheight.exit-extended"]
    assert variant.exit == ((7, 5),), (
        f"qsGay.exit-xheight.exit-extended should exit at x=7, y=5; got {variant.exit}"
    )
    assert "qsIt" in set(variant.before), (
        f"qsGay.exit-xheight.exit-extended should name qsIt in `before`; "
        f"got {variant.before}"
    )


@pytest.mark.parametrize("target_base", _GAY_NONJOINING_TARGETS)
def test_qs_gay_nonjoining_targets_do_not_join(target_base):
    glyphs = _shape_qs("qsGay", target_base)
    assert glyphs == ["qsGay", target_base], (
        f"qsGay + {target_base} should render without a join; got {glyphs!r}"
    )
    assert not _pair_join_ys(glyphs, 0), (
        f"qsGay ({glyphs[0]}) and {target_base} ({glyphs[1]}) must not share a join y; "
        f"gay exit ys={_exit_ys(glyphs[0])}, {target_base} entry ys={_entry_ys(glyphs[1])}"
    )


@pytest.mark.parametrize("target_base", _GAY_NONJOINING_TARGETS)
def test_qs_gay_nonjoining_targets_do_not_join_in_any_leading_context(target_base):
    failures: list[str] = []
    for leader in _GAY_CONTEXTS:
        glyphs = _shape_qs(leader, "qsGay", target_base)
        _append_gay_nonjoining_context_failures(
            failures,
            f"{leader} + qsGay + {target_base}",
            glyphs,
            target_base,
        )
    _assert_no_failures(failures, limit=None)


@pytest.mark.parametrize("target_base", _GAY_NONJOINING_TARGETS)
def test_qs_gay_nonjoining_targets_do_not_join_in_any_trailing_context(target_base):
    failures: list[str] = []
    for follower in _GAY_CONTEXTS:
        glyphs = _shape_qs("qsGay", target_base, follower)
        _append_gay_nonjoining_context_failures(
            failures,
            f"qsGay + {target_base} + {follower}",
            glyphs,
            target_base,
        )
    _assert_no_failures(failures, limit=None)


@pytest.mark.parametrize("target_base", _GAY_NONJOINING_TARGETS)
def test_qs_gay_extended_variants_exclude_nonjoining_targets(target_base):
    meta = _compiled_meta()
    for name, variant in meta.items():
        if name.startswith("qsGay") and "exit-extended" in name:
            assert target_base not in set(variant.before), (
                f"{name}.before should not include {target_base}; got {variant.before}"
            )
