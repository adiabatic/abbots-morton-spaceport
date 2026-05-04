from functools import lru_cache

import pytest

from quikscript_shaping_helpers import (
    ROOT,
    ZWNJ,
    _assert_join_preserved,
    _assert_no_failures,
    _base_names,
    _char_map,
    _compiled_meta,
    _entry_ys,
    _exit_ys,
    _find_base_index,
    _pair_join_ys,
    _plain_quikscript_letters,
    _shape,
    _shape_qs,
    _shape_with_features,
)

_SS03_FEATURE = (("ss03", True),)
_SS05_FEATURE = (("ss05", True),)
_SS07_FEATURE = (("ss07", True),)

from build_font import load_glyph_data
from test_shaping import run_shaping_test_runs


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


def test_qs_owe_at_word_start_before_fee_has_no_left_anchor():
    # Word-initial qsOwe + qsFee must shape with the exit-only form
    # (shape_2). The previous bug promoted qsOwe to shape_3, leaving a
    # phantom left tail / entry anchor pointing at nothing.
    glyphs = _shape_qs("qsOwe", "qsFee")
    assert glyphs == ["qsOwe.exit-xheight.exit-extended", "qsFee.entry-xheight"]
    assert _entry_ys(glyphs[0]) == set()
    assert _pair_join_ys(glyphs, 0) == {5}


@pytest.mark.parametrize(
    ("predecessor", "expected_left"),
    [
        pytest.param("qsMay", "qsMay.exit-extended", id="qsMay"),
        pytest.param("qsNo", "qsNo.exit-extended", id="qsNo"),
        pytest.param("qsLow", "qsLow.exit-extended", id="qsLow"),
        pytest.param("qsAh", "qsAh.exit-extended", id="qsAh"),
        pytest.param("qsUtter", "qsUtter.exit-extended", id="qsUtter"),
    ],
)
def test_qs_fee_entry_xheight_after_extended_predecessor(predecessor, expected_left):
    # When a predecessor extends its exit before qsFee, qsFee must take its
    # entry-xheight form so the left stub bridges the extension. Previously
    # the post-context bk-pair re-emission filtered out fwd_pair_overrides
    # outputs (e.g., qsMay.exit-extended) from late_contexts, so the
    # qsFee.entry-xheight substitution never matched and qsFee stayed bare,
    # leaving a 1-pixel gap at x-height.
    glyphs = _shape_qs(predecessor, "qsFee")
    assert glyphs == [expected_left, "qsFee.entry-xheight"]
    assert _pair_join_ys(glyphs, 0) == {5}


def test_qs_owe_at_word_start_before_tea_with_ss03_has_no_left_anchor():
    # Same bug, ss03 path: extend_exit_before_gated.ss03 wires qsTea into
    # the same forward-pair lookup that promotes qsOwe to shape_3.
    glyphs = _shape_qs("qsOwe", "qsTea", features=_SS03_FEATURE)
    assert _entry_ys(glyphs[0]) == set(), (
        f"word-initial qsOwe must not gain an entry anchor under ss03; "
        f"got glyphs={glyphs}"
    )


def test_qs_way_does_not_join_qs_tea_under_ss03():
    # ·Way·Tea must stay separate even with ss03 on. qsWay was previously in
    # qsTea.half_entry_xheight_ss03's after-list and qsWay carried a gated
    # exit-extension toward qsTea; both were dropped so the pair no longer
    # connects.
    glyphs = _shape_qs("qsWay", "qsTea", features=_SS03_FEATURE)
    assert _pair_join_ys(glyphs, 0) == set(), (
        f"·Way·Tea must not connect under ss03; got {glyphs}"
    )


def test_qs_owe_fee_may_owe_does_not_reach_for_ligature():
    # ·Owe·Fee·May ligates ·Fee+·May into qsFee_qsMay (no entry anchor).
    # ·Owe must not pick a forward-extending variant whose exit anchor
    # would point at the missing entry.
    glyphs = _shape_qs("qsOwe", "qsFee", "qsMay")
    assert glyphs[1] == "qsFee_qsMay", (
        f"expected qsFee_qsMay ligature in second position; got {glyphs}"
    )
    assert _exit_ys(glyphs[0]) == set(), (
        f"·Owe must have no exit anchor before qsFee_qsMay; got {glyphs}"
    )
    assert _pair_join_ys(glyphs, 0) == set(), (
        f"·Owe must not join into qsFee_qsMay; got {glyphs}"
    )


@pytest.mark.parametrize(
    "feature_label,feature_items",
    [
        pytest.param("default", (), id="default"),
        pytest.param("ss03", _SS03_FEATURE, id="ss03"),
        pytest.param("ss05", _SS05_FEATURE, id="ss05"),
        pytest.param("ss07", _SS07_FEATURE, id="ss07"),
    ],
)
def test_qs_owe_fee_may_under_each_stylistic_set(feature_label, feature_items):
    glyphs = _shape_qs("qsOwe", "qsFee", "qsMay", features=feature_items)
    assert glyphs[1] == "qsFee_qsMay", (
        f"expected qsFee_qsMay ligature in second position under "
        f"features={feature_label}; got {glyphs}"
    )
    assert _pair_join_ys(glyphs, 0) == set(), (
        f"·Owe joins into qsFee_qsMay under features={feature_label}; "
        f"got {glyphs}"
    )
    assert _exit_ys(glyphs[0]) == set(), (
        f"·Owe gained an exit anchor before qsFee_qsMay under "
        f"features={feature_label}; got {glyphs}"
    )


@lru_cache(maxsize=1)
def _two_component_ligatures() -> tuple[tuple[str, tuple[str, str]], ...]:
    meta_map = _compiled_meta()
    out: list[tuple[str, tuple[str, str]]] = []
    for name, meta in meta_map.items():
        if not meta.sequence or len(meta.sequence) != 2:
            continue
        if name != meta.base_name:
            continue
        out.append((name, (meta.sequence[0], meta.sequence[1])))
    return tuple(sorted(out))


def _no_orphan_exit_into_ligature_failures(
    feature_items: tuple[tuple[str, bool], ...] = (),
) -> list[str]:
    failures: list[str] = []
    chars = _char_map()
    meta_map = _compiled_meta()

    for lig_name, (mid_base, right_base) in _two_component_ligatures():
        if mid_base not in chars or right_base not in chars:
            continue
        mid_char = chars[mid_base]
        right_char = chars[right_base]

        for left_name, left_char in _plain_quikscript_letters():
            text = left_char + mid_char + right_char
            glyphs = (
                _shape_with_features(text, feature_items)
                if feature_items
                else _shape(text)
            )
            for index, glyph in enumerate(glyphs[:-1]):
                next_glyph = glyphs[index + 1]
                next_meta = meta_map.get(next_glyph)
                left_meta = meta_map.get(glyph)
                if next_meta is None or left_meta is None:
                    continue
                if next_meta.base_name != lig_name:
                    continue
                if next_meta.is_noentry:
                    # Ligature was stripped of its entry by a separate
                    # backward substitution (e.g. noentry_after); that's
                    # a different bug pattern than the one this test
                    # guards against.
                    continue
                base_meta = meta_map.get(left_meta.base_name)
                if base_meta is None:
                    continue
                left_exit_ys = _exit_ys(glyph)
                base_exit_ys = set(base_meta.exit_ys)
                contextual_exit_ys = left_exit_ys - base_exit_ys
                has_extended_exit = left_meta.extended_exit_suffix is not None
                if not contextual_exit_ys and not has_extended_exit:
                    continue
                next_entry_ys = _entry_ys(next_glyph)
                orphan = left_exit_ys - next_entry_ys
                if orphan:
                    failures.append(
                        f"{left_name} / {mid_base} / {right_base} "
                        f"(features={dict(feature_items) or None}): "
                        f"{glyph} has exit Y={sorted(orphan)} not present in "
                        f"{next_glyph} entries Y={sorted(next_entry_ys)} "
                        f"(base {left_meta.base_name!r} exits Y={sorted(base_exit_ys)}, "
                        f"extended_exit_suffix={left_meta.extended_exit_suffix!r}) "
                        f"in {glyphs}"
                    )

    return failures


@pytest.mark.parametrize(
    "feature_items",
    [
        pytest.param((), id="default"),
        pytest.param(_SS03_FEATURE, id="ss03"),
        pytest.param(_SS05_FEATURE, id="ss05"),
        pytest.param(_SS07_FEATURE, id="ss07"),
    ],
)
def test_qs_letter_does_not_reach_into_two_glyph_ligature(feature_items):
    _assert_no_failures(_no_orphan_exit_into_ligature_failures(feature_items))


def _word_initial_promoted_entry_failures(
    feature_items: tuple[tuple[str, bool], ...] = (),
) -> list[str]:
    failures: list[str] = []
    meta_map = _compiled_meta()
    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            if left_name == right_name:
                continue
            text = left_char + right_char
            glyphs = (
                _shape_with_features(text, feature_items)
                if feature_items
                else _shape(text)
            )
            if not glyphs:
                continue
            head = glyphs[0]
            head_meta = meta_map.get(head)
            if head_meta is None or not head_meta.entry:
                continue
            base_meta = meta_map.get(head_meta.base_name)
            if base_meta is None or base_meta.entry:
                # Family's bare form already carries an entry anchor as part
                # of its natural design — that's fine at word start.
                continue
            target_bitmap = head_meta.bitmap
            has_natural_no_entry_match = any(
                sibling_meta.bitmap == target_bitmap
                and not sibling_meta.entry
                and not sibling_meta.entry_curs_only
                and sibling_meta.noentry_for is None
                for sibling_name, sibling_meta in meta_map.items()
                if (
                    sibling_meta.base_name == head_meta.base_name
                    and sibling_name != head
                )
            )
            if has_natural_no_entry_match:
                # The entry anchor is purely positional: a natural sibling
                # has the same bitmap with no entry, so picking the variant
                # with the entry anchor doesn't add any visible left tail.
                continue
            head_entry_ys = sorted({anchor[1] for anchor in head_meta.entry})
            failures.append(
                f"word-initial {left_name} + {right_name} "
                f"(features={dict(feature_items) or None}) "
                f"shaped {head!r} with entry_ys={head_entry_ys}; "
                f"base {head_meta.base_name!r} has no entry anchor and no "
                f"sibling shares this bitmap, so this variant is a "
                f"phantom-entry promotion. Glyphs: {glyphs}"
            )
    return failures


@pytest.mark.parametrize(
    "feature_items",
    [
        pytest.param((), id="default"),
        pytest.param(_SS03_FEATURE, id="ss03"),
        pytest.param(_SS05_FEATURE, id="ss05"),
        pytest.param(_SS07_FEATURE, id="ss07"),
    ],
)
def test_word_initial_quikscript_glyph_never_promotes_to_phantom_entry_anchor(feature_items):
    _assert_no_failures(_word_initial_promoted_entry_failures(feature_items))


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
        "qsThaw",
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
        pytest.param(("qsYe", "qsIng"), ["qsYe", "qsIng.after-he-or-ye"], id="ye-ing"),
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
        pytest.param("qsHe", "qsExcite", id="he-excite"),
        pytest.param("qsOwe", "qsTea", id="owe-tea"),
        pytest.param("qsShe", "qsThaw", id="she-thaw"),
        pytest.param("qsTea", "qsOwe", id="tea-owe"),
        pytest.param("qsWay", "qsExcite", id="way-excite"),
        pytest.param("qsWay", "qsSee", id="way-see"),
        pytest.param("qsWay", "qsTea", id="way-tea"),
        pytest.param("qsWay", "qsVie", id="way-vie"),
        pytest.param("qsWhy", "qsExcite", id="why-excite"),
        pytest.param("qsWhy", "qsSee", id="why-see"),
        pytest.param("qsWhy", "qsTea", id="why-tea"),
        pytest.param("qsWhy", "qsThaw", id="why-thaw"),
        pytest.param("qsWhy", "qsVie", id="why-vie"),
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
        pytest.param("qsWay", "qsSee", id="way-before-see"),
        pytest.param("qsWay", "qsTea", id="way-before-tea"),
        pytest.param("qsWay", "qsVie", id="way-before-vie"),
        pytest.param("qsWhy", "qsSee", id="why-before-see"),
        pytest.param("qsWhy", "qsTea", id="why-before-tea"),
        pytest.param("qsWhy", "qsThaw", id="why-before-thaw"),
        pytest.param("qsWhy", "qsVie", id="why-before-vie"),
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
        pytest.param("qsWay", "qsSee", id="way-before-see"),
        pytest.param("qsWay", "qsTea", id="way-before-tea"),
        pytest.param("qsWay", "qsVie", id="way-before-vie"),
        pytest.param("qsWhy", "qsSee", id="why-before-see"),
        pytest.param("qsWhy", "qsTea", id="why-before-tea"),
        pytest.param("qsWhy", "qsThaw", id="why-before-thaw"),
        pytest.param("qsWhy", "qsVie", id="why-before-vie"),
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
        pytest.param("qsWay", "qsSee", id="way-see"),
        pytest.param("qsWay", "qsVie", id="way-vie"),
        pytest.param("qsWhy", "qsSee", id="why-see"),
        pytest.param("qsWhy", "qsThaw", id="why-thaw"),
        pytest.param("qsWhy", "qsVie", id="why-vie"),
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
    "left_base",
    [
        pytest.param("qsShe", id="after-she"),
        pytest.param("qsDay", id="after-day"),
    ],
)
def test_qs_excite_reaches_left_only_before_xheight_entry_letters(left_base: str):
    glyphs = _shape_qs(left_base, "qsExcite", "qsBay")
    assert _base_names(glyphs) == (left_base, "qsExcite", "qsBay")
    assert "noexit" in _compiled_meta()[glyphs[1]].compat_assertions, glyphs
    assert _pair_join_ys(glyphs, 0) == {0}
    assert not _pair_join_ys(glyphs, 1)


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsShe", id="after-she"),
        pytest.param("qsDay", id="after-day"),
    ],
)
def test_qs_excite_reaches_both_sides_when_neighbors_offer_baseline(left_base: str):
    glyphs = _shape_qs(left_base, "qsExcite", "qsTea")
    assert _base_names(glyphs) == (left_base, "qsExcite", "qsTea")
    assert "after-baseline-letter" in _compiled_meta()[glyphs[1]].compat_assertions, glyphs
    assert _pair_join_ys(glyphs, 0) == {0}
    assert _pair_join_ys(glyphs, 1) == {0}


def test_qs_excite_reaches_left_only_at_word_end_after_baseline_exit():
    glyphs = _shape_qs("qsShe", "qsExcite")
    assert _base_names(glyphs) == ("qsShe", "qsExcite")
    assert "noexit" in _compiled_meta()[glyphs[1]].compat_assertions, glyphs
    assert _pair_join_ys(glyphs, 0) == {0}


def test_qs_excite_stays_mono_at_word_start_before_xheight_entry():
    glyphs = _shape_qs("qsExcite", "qsBay")
    assert glyphs == ["qsExcite", "qsBay"]


def test_qs_excite_reaches_left_only_before_qs_thaw():
    glyphs = _shape_qs("qsShe", "qsExcite", "qsThaw")
    assert _base_names(glyphs) == ("qsShe", "qsExcite", "qsThaw")
    assert "noexit" in _compiled_meta()[glyphs[1]].compat_assertions, glyphs
    assert _pair_join_ys(glyphs, 0) == {0}
    assert not _pair_join_ys(glyphs, 1)


def test_qs_it_excite_uses_the_visible_baseline_entry_shape():
    assert _shape_qs("qsIt", "qsExcite") == [
        "qsIt.exit-baseline",
        "qsExcite.entry-baseline.noexit",
    ]


def test_qs_pea_excite_excite_uses_the_visible_final_excite_entry_shape():
    glyphs = _shape_qs("qsPea", "qsExcite", "qsExcite")
    assert glyphs == [
        "qsPea",
        "qsExcite.exit-baseline.before-vertical.noentry",
        "qsExcite.entry-baseline.noexit",
    ]
    assert not _pair_join_ys(glyphs, 0)
    assert _pair_join_ys(glyphs, 1) == {0}


def _excite_baseline_receiver_shape_failures() -> list[str]:
    failures: list[str] = []
    meta = _compiled_meta()
    sequences: list[tuple[str, ...]] = []
    plain = [name for name, _ in _plain_quikscript_letters()]

    for left_name in plain:
        sequences.append((left_name, "qsExcite"))
    for left_name in plain:
        for right_name in plain:
            sequences.append((left_name, "qsExcite", right_name))
            sequences.append((left_name, right_name, "qsExcite"))

    for parts in sequences:
        glyphs = _shape_qs(*parts)
        for index, glyph_name in enumerate(glyphs[:-1]):
            left_meta = meta.get(glyph_name)
            if left_meta is None or left_meta.after or left_meta.before:
                continue
            right_name = glyphs[index + 1]
            right_meta = meta.get(right_name)
            if right_meta is None or right_meta.base_name != "qsExcite":
                continue
            if 0 not in _pair_join_ys(glyphs, index):
                continue

            assertions = right_meta.compat_assertions
            joins_right_at_baseline = 0 in _pair_join_ys(glyphs, index + 1)
            if joins_right_at_baseline:
                if "after-baseline-letter" not in assertions:
                    failures.append(
                        f"{' / '.join(parts)}: {right_name} joins left and right at "
                        f"baseline but is not the both-side visible shape in {glyphs}"
                    )
            elif "entry-baseline" not in assertions and "after-baseline-letter" not in assertions:
                failures.append(
                    f"{' / '.join(parts)}: {right_name} joins left at baseline but "
                    f"does not use a visible baseline-entry shape in {glyphs}"
                )

    return failures


def test_qs_excite_baseline_receivers_use_visible_entry_shapes():
    _assert_no_failures(_excite_baseline_receiver_shape_failures(), limit=None)


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
# ·Way and ·Why must stay full before ·Vie and ·See, the pair must not
# connect, and the right glyph must not change shape because of a preceding
# ·Way / ·Why.
#
# Way's prop exits only at y=5; Why's prop has no exit. Both Vie's and
# See's prop enter only at y=0. With the half-form fix in place, neither
# pair forms a join and neither side reaches across the seam — these tests
# pin that down.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsWay", id="way"),
        pytest.param("qsWhy", id="why"),
    ],
)
@pytest.mark.parametrize(
    "right_base",
    [
        pytest.param("qsVie", id="vie"),
        pytest.param("qsSee", id="see"),
    ],
)
def test_qs_right_glyph_unchanged_after_qs_way_or_qs_why(
    left_base: str, right_base: str
):
    isolated_right = _shape_qs(right_base)[0]
    failures: list[str] = []

    pair = _shape_qs(left_base, right_base)
    pair_right_index = _find_base_index(pair, right_base)
    if pair_right_index is None:
        failures.append(f"{left_base} / {right_base}: {right_base} missing from {pair}")
    elif pair[pair_right_index] != isolated_right:
        failures.append(
            f"{left_base} / {right_base}: {right_base} glyph leaks "
            f"(isolated={isolated_right}, after-{left_base}={pair[pair_right_index]})"
        )

    for outer_right_name, _ in _plain_quikscript_letters():
        with_left = _shape_qs(left_base, right_base, outer_right_name)
        without_left = _shape_qs(right_base, outer_right_name)
        with_index = _find_base_index(with_left, right_base)
        without_index = _find_base_index(without_left, right_base)
        if with_index is None or without_index is None:
            continue
        if with_left[with_index] != without_left[without_index]:
            failures.append(
                f"{left_base} / {right_base} / {outer_right_name}: "
                f"{right_base} glyph leaks "
                f"({with_left[with_index]} with {left_base} prefix vs "
                f"{without_left[without_index]} without)"
            )

    _assert_no_failures(failures)


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsWay", id="way"),
        pytest.param("qsWhy", id="why"),
    ],
)
def test_half_form_not_before_list_keeps_left_full(left_base: str):
    """Auto-derived deny-set guard: every family declared in
    ``<base>.half``'s ``not_before`` list must keep ``<base>`` in a
    non-half variant, both as a bare pair and surrounded by every plain
    Quikscript outer context. Adding a family to ``not_before`` extends
    coverage automatically.

    Only the "not half" half of the invariant is universal: families end
    up in ``not_before`` for two distinct reasons — either the full form
    legitimately joins at x-height (e.g. qsIt, qsDay), or the pair is
    meant to stay disconnected (qsSee, qsTea, qsThaw, qsVie). Connection
    behaviour is asserted in the targeted parametrizations above; this
    test covers the part they share.
    """
    half_meta = _compiled_meta()[f"{left_base}.half"]
    deny_families = sorted(half_meta.not_before)
    assert deny_families, f"{left_base}.half should declare a non-empty not_before"

    meta_map = _compiled_meta()
    failures: list[str] = []
    for right_base in deny_families:
        assert any(
            m.base_name == right_base for m in meta_map.values()
        ), f"{right_base} declared in {left_base}.half not_before but absent from compiled meta"

        pair = _shape_qs(left_base, right_base)
        pair_meta = meta_map[pair[0]]
        if pair_meta.base_name == left_base and "half" in pair_meta.traits:
            failures.append(
                f"{left_base} / {right_base}: half-{left_base} selected: {pair}"
            )

        for outer_left_name, _ in _plain_quikscript_letters():
            for outer_right_name, _ in _plain_quikscript_letters():
                glyphs = _shape_qs(outer_left_name, left_base, right_base, outer_right_name)
                for index, glyph_name in enumerate(glyphs[:-1]):
                    left_glyph_meta = meta_map.get(glyph_name)
                    right_glyph_meta = meta_map.get(glyphs[index + 1])
                    if left_glyph_meta is None or right_glyph_meta is None:
                        continue
                    if (
                        left_glyph_meta.base_name != left_base
                        or right_glyph_meta.base_name != right_base
                    ):
                        continue
                    if "half" in left_glyph_meta.traits:
                        failures.append(
                            f"{outer_left_name} / {left_base} / {right_base} / "
                            f"{outer_right_name}: half-{left_base} selected: {glyphs}"
                        )

    _assert_no_failures(failures)


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
    assert gay.exit == ((6, 0),), (
        f"qsGay extended baseline exit should land at x=6, y=0 "
        f"(max_ink_x+1 of the extended bitmap), got {gay.exit}"
    )


@pytest.mark.parametrize("target_base", _GAY_BASELINE_EXTENDED_TARGETS)
def test_qs_gay_exit_baseline_extended_targets_include_joining_followers(target_base):
    meta = _compiled_meta()
    variant = meta["qsGay.exit-baseline.exit-extended"]
    assert variant.exit == ((6, 0),), (
        f"qsGay.exit-baseline.exit-extended should exit at x=6, y=0; got {variant.exit}"
    )
    assert target_base in set(variant.before), (
        f"qsGay.exit-baseline.exit-extended should name {target_base} in `before`; "
        f"got {variant.before}"
    )


def test_qs_gay_exit_xheight_extended_exists_for_it():
    meta = _compiled_meta()
    variant = meta["qsGay.exit-xheight.exit-extended"]
    assert variant.exit == ((6, 5),), (
        f"qsGay.exit-xheight.exit-extended should exit at x=6, y=5; got {variant.exit}"
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


# --- Restored ensure-sanity parametrized cases -------------------------------
#
# These are the exact data-expect cases from the former
# test/test_ensure_sanity.py, kept here so the collapsed semantic tests above
# do not lose the original pytest case matrix or data-expect assertions.


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
                    [("Tea", tea_nhalf), ("Tea", _expect_tok("Tea")), (name, _expect_tok(name))]
                ),
            )
        )
    return out


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
        left_connection = "~x~" if name == "Utter" else "?"
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


def _ink_bounds_at_y(glyph_name: str, y: int) -> tuple[int, int] | None:
    meta = _compiled_meta()[glyph_name]
    top_y = meta.y_offset + len(meta.bitmap) - 1
    row_index = top_y - y
    if row_index < 0 or row_index >= len(meta.bitmap):
        return None
    row = meta.bitmap[row_index]
    if isinstance(row, str):
        ink_xs = [index for index, value in enumerate(row) if value == "#"]
    else:
        ink_xs = [index for index, value in enumerate(row) if value]
    if not ink_xs:
        return None
    return min(ink_xs), max(ink_xs)


def _bitmap_join_gap(glyphs: list[str], index: int, y: int) -> int | None:
    meta = _compiled_meta()
    left = meta[glyphs[index]]
    right = meta[glyphs[index + 1]]
    left_anchor = next(anchor for anchor in left.exit if anchor[1] == y)
    right_anchor = next(
        anchor for anchor in (*right.entry, *right.entry_curs_only) if anchor[1] == y
    )
    left_bounds = _ink_bounds_at_y(glyphs[index], y)
    right_bounds = _ink_bounds_at_y(glyphs[index + 1], y)
    if left_bounds is None or right_bounds is None:
        return None
    _, left_max = left_bounds
    right_min, _ = right_bounds
    return (right_min - right_anchor[0]) - (left_max - left_anchor[0]) - 1


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
    "left_base",
    _JAI_XHEIGHT_LEFTS,
)
def test_qs_jai_joins_designated_left_letters_at_xheight(left_base: str) -> None:
    glyphs = _shape_qs(left_base, "qsJai")
    assert len(glyphs) == 2, glyphs
    assert (
        glyphs[0] != _shape_qs(left_base)[0]
        or glyphs[1] != "qsJai.entry-xheight"
    ), glyphs
    assert glyphs[1].startswith("qsJai.entry-xheight"), glyphs
    assert _pair_join_ys(glyphs, 0) == {5}, glyphs
    gap = _bitmap_join_gap(glyphs, 0, 5)
    assert gap is not None and gap <= 0, glyphs


@pytest.mark.parametrize(
    "left_base",
    _JAI_XHEIGHT_LEFTS,
)
def test_qs_jai_utter_ligature_joins_designated_left_letters_at_xheight(
    left_base: str,
) -> None:
    glyphs = _shape_qs(left_base, "qsJai", "qsUtter")
    assert len(glyphs) == 2, glyphs
    assert glyphs[1].startswith("qsJai_qsUtter"), glyphs
    assert _pair_join_ys(glyphs, 0) == {5}, glyphs
    gap = _bitmap_join_gap(glyphs, 0, 5)
    assert gap is not None and gap <= 0, glyphs


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsRoe", id="roe"),
        pytest.param("qsSee", id="see"),
    ],
)
def test_predecessors_never_join_to_qs_at_qs_may(left_base: str):
    _assert_no_failures(
        _collect_nonjoining_left_ligature_failures(
            left_base,
            "qsAt_qsMay",
            ("qsAt", "qsMay"),
        ),
        limit=None,
    )


def test_qs_roe_returns_to_base_before_qs_at_qs_may():
    # Pre-liga, qsRoe.exit-baseline.select.before matches the literal qsAt.
    # calt_post_liga_left_cleanup must revert qsRoe to its default form
    # once qsAt qsMay collapses to qsAt_qsMay (which has no entry anchor).
    glyphs = _shape_qs("qsRoe", "qsAt", "qsMay")
    assert glyphs == ["qsRoe", "qsAt_qsMay"], glyphs
    assert not _pair_join_ys(glyphs, 0), glyphs


def test_qs_roe_keeps_exit_baseline_before_plain_qs_ah():
    # qsRoe.exit-baseline is also legitimately triggered by {family: qsAh}.
    # The post-liga cleanup must NOT over-fire when the right neighbor is
    # not a no-entry ligature.
    glyphs = _shape_qs("qsRoe", "qsAh")
    assert glyphs[0] == "qsRoe.exit-baseline", glyphs


def test_qs_at_qs_may_has_no_entry_anchor():
    # The whole left-cleanup mechanism is gated on entry_explicitly_none.
    # If qsAt_qsMay ever silently regains an entry anchor, the cleanup
    # stops emitting and predecessors revert to baseline-joining behavior.
    meta = _compiled_meta()["qsAt_qsMay"]
    assert meta.entry_explicitly_none, meta
    assert meta.entry == (), meta


def test_qs_may_uses_exit_noentry_before_qs_they_qs_utter_noentry():
    glyphs = _shape_qs("qsRoe", "qsMay", "qsThey", "qsUtter")
    # ·Roe·May joins at the baseline in isolation, so the same join
    # should survive when ·They+Utter follows. qsMay routes to its
    # entry-preserving `.exit-noentry` form, dropping the dangling
    # x-height exit but keeping the baseline entry that receives
    # qsRoe.exit-baseline. The ligature is entryless on the right.
    assert glyphs == [
        "qsRoe.exit-baseline",
        "qsMay.entry-baseline.exit-noentry",
        "qsThey_qsUtter.noentry",
    ], glyphs
    assert _pair_join_ys(glyphs, 0) == {0}, glyphs
    assert not _pair_join_ys(glyphs, 1), glyphs


def test_qs_at_qs_may_stays_whole_before_qs_they_qs_utter_noentry():
    glyphs = _shape_qs("qsAt", "qsMay", "qsThey", "qsUtter")
    assert glyphs == ["qsAt_qsMay", "qsThey_qsUtter.noentry"], glyphs
    assert not _pair_join_ys(glyphs, 0), glyphs
