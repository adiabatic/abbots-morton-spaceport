from functools import cache
from itertools import product

import pytest
import uharfbuzz as hb

from quikscript_shaping_helpers import (
    ROOT,
    ZWNJ,
    _assert_expect_any,
    _assert_join_preserved,
    _assert_no_failures,
    _base_names,
    _char_map,
    _compiled_meta,
    _context_chars,
    _declared_exit_ys,
    _declares_xheight_exit,
    _entry_ys,
    _exit_ys,
    _find_base_index,
    _font,
    _gid_to_full_name,
    _pair_join_ys,
    _plain_quikscript_letters,
    _qs_text,
    _senior_shaping_env,
    _shape,
    _shape_qs,
    _shape_with_clusters,
    _shape_with_features,
)

_SS03_FEATURE = (("ss03", True),)
_SS05_FEATURE = (("ss05", True),)
_SS07_FEATURE = (("ss07", True),)
_SS10_FEATURE = (("ss10", True),)

from build_font import load_glyph_data
from quikscript_ir import _EXTENSION_SUFFIX
from test_shaping import (
    ExpectToken,
    _expand_maybe_ligatures,
    _try_interpretation,
    parse_expect,
    run_shaping_test_runs,
)
from test_join_ink import (
    PIXEL_SIZE,
    _bitmap_origin_x_offset,
    _ink_bounds_at_y,
    _origin_xs,
    _shape as _shape_with_positions,
)

_EXIT_EXTENSION_PIXELS_BY_SUFFIX: dict[str | None, int] = {
    None: 0,
    **{f".ex-{word}": count for count, word in _EXTENSION_SUFFIX.items()},
}
_ENTRY_EXTENSION_PIXELS_BY_SUFFIX: dict[str | None, int] = {
    None: 0,
    **{f".en-{word}": count for count, word in _EXTENSION_SUFFIX.items()},
}
_INTENTIONAL_QS_GAY_EXIT_EXTENSION_TARGETS = frozenset({"qsIt", "qsI", "qsExam"})


def _is_intentional_gay_exit_extension(left_meta, right_meta) -> bool:
    return left_meta.base_name == "qsGay" and (
        right_meta.base_name in _INTENTIONAL_QS_GAY_EXIT_EXTENSION_TARGETS
        or bool(right_meta.sequence and right_meta.sequence[0] in _INTENTIONAL_QS_GAY_EXIT_EXTENSION_TARGETS)
    )


def _surround_combos(context_set, max_chars: int, *, first_only: str | None = None) -> tuple:
    """Return every combination of 0 to ``max_chars`` entries drawn from ``context_set``. The ``first_only`` shard filter keeps only the non-empty combinations whose first entry is named ``first_only``, plus the empty combination in the one shard named for ``context_set[0]``, so a parametrization over every entry sweeps each combination exactly once."""
    combos = tuple(combo for n in range(max_chars + 1) for combo in product(context_set, repeat=n))
    if first_only is not None:
        keeps_empty = first_only == context_set[0][0]
        combos = tuple(combo for combo in combos if (combo[0][0] == first_only if combo else keeps_empty))
    return combos


def _collect_left_must_stay_isolated_before_right_failures(
    left_base: str,
    right_base: str,
    *,
    max_chars_before: int = 1,
    max_chars_after: int = 1,
) -> list[str]:
    """Flag every position where ``left_base`` immediately before ``right_base`` is shaped as anything other than its isolated form. The surrounds are the same as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``."""
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()
    left_label = _family_to_label(left_base)
    isolated_left_glyph = _shape_qs(left_base)[0]

    before_combos = _surround_combos(context_set, max_chars_before)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + _qs_text(left_base, right_base) + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / {left_base} / {right_base} / [{after_label}]"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                if left_meta.base_name != left_base or right_meta.base_name != right_base:
                    continue
                if glyph_name != isolated_left_glyph:
                    failures.append(
                        f"{label}: expected isolated {left_label} glyph "
                        f"{isolated_left_glyph}, got {glyph_name} in {glyphs}"
                    )

    return failures


def _collect_pair_with_forbidden_trait_co_occurrence_failures(
    left_base: str,
    right_base: str,
    *,
    forbidden_left_traits: frozenset[str] = frozenset(),
    forbidden_right_traits: frozenset[str] = frozenset(),
    max_chars_before: int = 1,
    max_chars_after: int = 1,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every adjacent (``left_base``, ``right_base``) pair whose left glyph carries all of ``forbidden_left_traits`` while its right glyph carries all of ``forbidden_right_traits``, over every surround drawn from ``_context_chars()``. `doc/joint-variant-invariants.md` explains the set math, the empty-set cases, and how to read a failure message."""
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + _qs_text(left_base, right_base) + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / {left_base} / {right_base} / [{after_label}]"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                left_is_target = left_meta.base_name == left_base or (
                    left_meta.sequence and left_meta.sequence[-1] == left_base
                )
                right_is_target = right_meta.base_name == right_base or (
                    right_meta.sequence and right_meta.sequence[0] == right_base
                )
                if not left_is_target or not right_is_target:
                    continue
                if not forbidden_left_traits.issubset(left_meta.traits):
                    continue
                if not forbidden_right_traits.issubset(right_meta.traits):
                    continue
                failures.append(
                    f"{label}: {glyph_name} (traits={sorted(left_meta.traits)}) "
                    f"before {glyphs[index + 1]} (traits={sorted(right_meta.traits)}) "
                    f"matches forbidden co-occurrence "
                    f"(left ⊇ {sorted(forbidden_left_traits)}, "
                    f"right ⊇ {sorted(forbidden_right_traits)}) in {glyphs}"
                )

    return failures


def _collect_joined_right_not_half_failures(
    left_base: str,
    right_base: str,
) -> list[str]:
    failures: list[str] = []
    meta_map = _compiled_meta()
    left_label = _family_to_label(left_base)
    right_label = _family_to_label(right_base)

    contexts: list[tuple[str | None, str | None]] = [(None, None)]
    contexts.extend((None, outer_right) for outer_right, _ in _plain_quikscript_letters())
    contexts.extend((outer_left, None) for outer_left, _ in _plain_quikscript_letters())
    contexts.extend(
        (outer_left, outer_right)
        for outer_left, _ in _plain_quikscript_letters()
        for outer_right, _ in _plain_quikscript_letters()
    )

    for outer_left_name, outer_right_name in contexts:
        parts = (
            ([outer_left_name] if outer_left_name else [])
            + [left_base, right_base]
            + ([outer_right_name] if outer_right_name else [])
        )
        glyphs = _shape_qs(*parts)
        label = " / ".join(parts) if parts else f"{left_base} / {right_base}"

        for index, glyph_name in enumerate(glyphs[:-1]):
            left_meta = meta_map.get(glyph_name)
            right_meta = meta_map.get(glyphs[index + 1])
            if left_meta is None or right_meta is None:
                continue
            if left_meta.base_name != left_base or right_meta.base_name != right_base:
                continue
            if not _pair_join_ys(glyphs, index):
                continue
            if "half" not in right_meta.traits:
                failures.append(
                    f"{label}: full-{right_label} ({glyphs[index + 1]}) "
                    f"selected after {left_label} ({glyph_name}); "
                    f"expected half-{right_label} or no join in {glyphs}"
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
            left_is_target = left_meta.base_name == left_base or (
                left_meta.sequence and left_meta.sequence[-1] == left_base
            )
            if not left_is_target or right_meta.base_name != ligature_base:
                continue
            saw_pair = True
            common = _pair_join_ys(glyphs, index)
            if common:
                failures.append(
                    f"{label}: {glyph_name} joins {glyphs[index + 1]} " f"at Y={sorted(common)} in {glyphs}"
                )

        if not saw_pair and outer_left_name is None and outer_right_name is None:
            failures.append(f"{label}: expected {left_base} immediately before {ligature_base}, got {glyphs}")

    return failures


def _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
    left_base: str,
    right_base: str,
    *,
    max_chars_before: int = 1,
    max_chars_after: int = 1,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every (left_base, right_base) pair that joins at any Y, with every prefix of 0 to ``max_chars_before`` characters and every suffix of 0 to ``max_chars_after`` characters. The characters come from ``_context_chars()``: every plain Quikscript letter plus space and ZWNJ.

    A ligature whose sequence starts with ``right_base``, or ends with ``left_base``, also matches, because it carries that letter's entry or exit anchor.

    For "·A·B may join, but not at this height", use ``_collect_pair_must_not_join_at_y_regardless_of_what_comes_before_or_after``.

    ``before_first_only`` keeps only the non-empty prefixes whose first entry is the named context entry (such as ``"qsPea"`` or ``"ZWNJ"``); the empty prefix is swept only in the shard named for the first entry of ``_context_chars()``. Parametrized callers use it to split one sweep across pytest-xdist workers.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + _qs_text(left_base, right_base) + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / {left_base} / {right_base} / [{after_label}]"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                left_is_target = left_meta.base_name == left_base or (
                    left_meta.sequence and left_meta.sequence[-1] == left_base
                )
                right_is_target = right_meta.base_name == right_base or (
                    right_meta.sequence and right_meta.sequence[0] == right_base
                )
                if not left_is_target or not right_is_target:
                    continue
                common = _pair_join_ys(glyphs, index)
                if common:
                    failures.append(
                        f"{label}: {glyph_name} joins {glyphs[index + 1]} "
                        f"in {glyphs} (join Ys={sorted(common)})"
                    )

    return failures


def _collect_pair_must_not_join_at_y_regardless_of_what_comes_before_or_after(
    left_base: str,
    right_base: str,
    *,
    forbidden_y: int,
    max_chars_before: int = 1,
    max_chars_after: int = 1,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every (left_base, right_base) pair that joins at ``forbidden_y``. Joins at other heights pass. The surrounds, the ligature matching, and ``before_first_only`` work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``."""
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + _qs_text(left_base, right_base) + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / {left_base} / {right_base} / [{after_label}]"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                left_is_target = left_meta.base_name == left_base or (
                    left_meta.sequence and left_meta.sequence[-1] == left_base
                )
                right_is_target = right_meta.base_name == right_base or (
                    right_meta.sequence and right_meta.sequence[0] == right_base
                )
                if not left_is_target or not right_is_target:
                    continue
                common = _pair_join_ys(glyphs, index)
                if forbidden_y in common:
                    failures.append(
                        f"{label}: {glyph_name} joins {glyphs[index + 1]} "
                        f"at Y={forbidden_y} in {glyphs} (join Ys={sorted(common)})"
                    )

    return failures


def _target_slice_bounds(clusters: tuple[int, ...], before_len: int, target_len: int) -> tuple[int, int]:
    """Return the inclusive ``(lead, trail)`` glyph indices that cover the target's input codepoints ``[before_len, before_len + target_len)`` in a shaped ``before + target + after`` run. ``lead`` and ``trail`` are the last glyphs whose cluster is at or before the target's first and last codepoint. So ``lead`` can be a predecessor ligature that absorbed the target's first letter, whose cluster lies before the target range."""
    first_cp = before_len
    last_cp = before_len + target_len - 1
    lead = max(i for i, c in enumerate(clusters) if c <= first_cp)
    trail = max(i for i, c in enumerate(clusters) if c <= last_cp)
    return lead, trail


def _retarget_boundary_tokens(
    tokens: list[ExpectToken], slice_glyphs: list[str], meta_map
) -> list[ExpectToken] | None:
    """Return a copy of ``tokens`` with the first or last token rewritten as a two-component ligature token when the slice's edge glyph is a ligature that absorbed that letter. The lead glyph absorbs a letter when its ``sequence`` ends with the token's base, and the trail glyph when its ``sequence`` starts with it. This lets ``·Utter ~x~ ·Gay`` match when a predecessor forms ``qsThey_qsUtter``. Returns None when an absorbing ligature has more than two components."""
    result: list[ExpectToken] = [{**tok} for tok in tokens]

    def retarget(index: int, edge: str) -> bool:
        tok = result[index]
        if tok["lig_base"] is not None:
            return True
        meta = meta_map.get(slice_glyphs[index])
        if meta is None or meta.base_name == tok["base"]:
            return True
        seq = meta.sequence
        absorbs = bool(seq) and (seq[-1] == tok["base"] if edge == "lead" else seq[0] == tok["base"])
        if not absorbs:
            return True  # base mismatch: _try_interpretation reports it
        if len(seq) != 2:
            return False
        tok["base"] = seq[0]
        tok["lig_base"] = seq[1]
        tok["exact_glyph"] = False
        return True

    if not retarget(0, "lead"):
        return None
    if len(result) > 1 and not retarget(len(result) - 1, "trail"):
        return None
    return result


def _slice_matches_any_expect(slice_glyphs, parsed_expects, font, anchor_map, potential, meta_map):
    """Return None if ``slice_glyphs`` matches at least one of ``parsed_expects`` (a list of ``(expect_str, interpretations)``), otherwise one failure string per expect. Each interpretation's edge tokens are retargeted onto an absorbing edge ligature before ``_try_interpretation`` checks it. Only the joins inside the target are checked, not how the target joins its surround."""
    slice_list = list(slice_glyphs)
    errors = []
    for expect_str, interps in parsed_expects:
        interp_errors = []
        for tokens, connections in interps:
            retargeted = _retarget_boundary_tokens(tokens, slice_list, meta_map)
            if retargeted is None:
                interp_errors.append("edge ligature has more than two components")
                continue
            err = _try_interpretation(
                font, anchor_map, slice_list, retargeted, connections, potential, variant="senior"
            )
            if err is None:
                return None
            interp_errors.append(err)
        errors.append(f"{expect_str!r}: " + " / ".join(interp_errors))
    return errors


def _collect_sequence_must_match_any_expect_regardless_of_what_comes_before_or_after(
    sequence: list[str],
    expects: list[str],
    *,
    max_chars_before: int = 1,
    max_chars_after: int = 1,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every surround under which the ``sequence`` of Quikscript families matches none of the ``data-expect`` strings in ``expects``. The surrounds and ``before_first_only`` work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.

    Each ``expects`` entry describes only the target ``sequence`` (such as ``"·Utter ~x~ ·Gay"``). The helper finds the target's output glyphs by HarfBuzz cluster, including an edge ligature that absorbed the target's first or last letter, and passes if any one expect matches that slice. Only the joins inside the target are checked. Use it when a sequence has a few valid shapings that depend on context.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    fonts, anchor_maps, potentials = _senior_shaping_env()
    font = fonts["senior"]
    anchor_map = anchor_maps["senior"]
    potential = potentials["senior"]

    parsed_expects = [(expect, _expand_maybe_ligatures(*parse_expect(expect))) for expect in expects]

    sequence_label = "·" + "·".join(_family_to_label(family) for family in sequence)
    target_text = _qs_text(*sequence)
    target_len = len(target_text)

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        before_len = len(before_text)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + target_text + after_text
            names, clusters = _shape_with_clusters(text)
            lead, trail = _target_slice_bounds(clusters, before_len, target_len)
            slice_glyphs = names[lead : trail + 1]
            errors = _slice_matches_any_expect(
                slice_glyphs, parsed_expects, font, anchor_map, potential, meta_map
            )
            if errors is not None:
                label = f"[{before_label}] / {sequence_label} / [{after_label}]"
                detail = "\n      ".join(errors)
                failures.append(
                    f"{label}: no expect matched slice {list(slice_glyphs)} in {list(names)}\n      {detail}"
                )

    return failures


def _collect_see_out_connecting_body_without_xheight_receiver_failures(
    *,
    max_chars_before: int = 2,
    max_chars_after: int = 2,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every ·See·Out where ·Out has its x-height connecting body but the next glyph has no entry at the x-height (y=5). A word-final ·Out with that body is also a failure.

    ·Out counts as connecting when it carries the ``ex-y5`` modifier (``_declares_xheight_exit``). Checking its exit anchor instead would miss a glyph whose anchor was removed while its connecting body is still drawn. ``test_declared_exit_height_matches_exit_anchor`` checks that every ``ex-y5`` modifier has a matching exit anchor.

    A ligature whose sequence ends with ``qsSee`` counts as ·See, and one whose sequence starts with ``qsOut`` counts as ·Out. The surrounds and ``before_first_only`` work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + _qs_text("qsSee", "qsOut") + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / qsSee / qsOut / [{after_label}]"

            for out_index in range(1, len(glyphs)):
                left_meta = meta_map.get(glyphs[out_index - 1])
                out_meta = meta_map.get(glyphs[out_index])
                if left_meta is None or out_meta is None:
                    continue
                left_is_see = left_meta.base_name == "qsSee" or (
                    left_meta.sequence and left_meta.sequence[-1] == "qsSee"
                )
                out_is_out = out_meta.base_name == "qsOut" or (
                    out_meta.sequence and out_meta.sequence[0] == "qsOut"
                )
                if not left_is_see or not out_is_out:
                    continue
                out_glyph_name = glyphs[out_index]
                connecting = _declares_xheight_exit(out_glyph_name)
                follower_receives_at_xheight = (out_index + 1 < len(glyphs)) and (
                    5 in _entry_ys(glyphs[out_index + 1])
                )
                if connecting and not follower_receives_at_xheight:
                    follower = glyphs[out_index + 1] if out_index + 1 < len(glyphs) else "word-final"
                    follower_entry_ys = (
                        sorted(_entry_ys(glyphs[out_index + 1])) if out_index + 1 < len(glyphs) else []
                    )
                    failures.append(
                        f"{label}: {out_glyph_name} declares the x-height connecting body "
                        f"(ex-y5) but its follower {follower!r} does not receive at the x-height "
                        f"(follower entry Ys={follower_entry_ys}) in {glyphs}"
                    )

    return failures


def _collect_declared_exit_height_without_matching_anchor_failures() -> list[str]:
    """Flag every compiled glyph with an ``ex-yN`` modifier but no exit anchor at y=N. Such a glyph draws a connecting body that nothing can attach to, which leaves a dangling stub.

    Other tests use the ``ex-y5`` modifier to decide whether a glyph is connecting (see ``_declares_xheight_exit``), and this check keeps that modifier consistent with the anchors.

    The converse is not checked: a stance's default exit height carries no ``ex-yN`` modifier, so many valid exit-bearing glyphs have no such modifier.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    for name in sorted(meta_map):
        exit_ys = _exit_ys(name)
        for declared_y in sorted(_declared_exit_ys(name)):
            if declared_y not in exit_ys:
                failures.append(
                    f"{name}: declares ex-y{declared_y} but carries no exit anchor at y={declared_y} "
                    f"(exit Ys={sorted(exit_ys)}, modifiers={list(meta_map[name].modifiers)})"
                )
    return failures


def _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
    left_base: str,
    right_base: str,
    *,
    pixels: int,
    max_chars_before: int = 1,
    max_chars_after: int = 1,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every joined (left_base, right_base) pair whose total extension is not ``pixels``. Pairs that do not join are skipped. The surrounds, the ligature matching, and ``before_first_only`` work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.

    The total is the sum of both sides: the left glyph's ``.ex-ext-N`` suffix (``extended_exit_suffix``, from ``extend_exit_before``) and the right glyph's ``.en-ext-N`` suffix (``extended_entry_suffix``, from ``extend_entry_after``). Any split works, so 3 pixels can be ``.ex-ext-2`` plus ``.en-ext-1``. With ``pixels=0``, neither side of a joined pair may carry an extension suffix.
    """
    if pixels < 0:
        raise ValueError(f"pixels must be non-negative, got {pixels!r}")

    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + _qs_text(left_base, right_base) + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / {left_base} / {right_base} / [{after_label}]"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                left_is_target = left_meta.base_name == left_base or (
                    left_meta.sequence and left_meta.sequence[-1] == left_base
                )
                right_is_target = right_meta.base_name == right_base or (
                    right_meta.sequence and right_meta.sequence[0] == right_base
                )
                if not left_is_target or not right_is_target:
                    continue
                common = _pair_join_ys(glyphs, index)
                if not common:
                    continue
                left_pixels = _EXIT_EXTENSION_PIXELS_BY_SUFFIX.get(left_meta.extended_exit_suffix)
                right_pixels = _ENTRY_EXTENSION_PIXELS_BY_SUFFIX.get(right_meta.extended_entry_suffix)
                if left_pixels is None:
                    raise AssertionError(
                        f"unknown extended_exit_suffix={left_meta.extended_exit_suffix!r} on {glyph_name}"
                    )
                if right_pixels is None:
                    raise AssertionError(
                        f"unknown extended_entry_suffix={right_meta.extended_entry_suffix!r} on {glyphs[index + 1]}"
                    )
                total = left_pixels + right_pixels
                if total != pixels:
                    failures.append(
                        f"{label}: {glyph_name} joins {glyphs[index + 1]} at Y={sorted(common)} "
                        f"with {total} pixel(s) of extension (left={left_pixels} from "
                        f"{left_meta.extended_exit_suffix}, right={right_pixels} from "
                        f"{right_meta.extended_entry_suffix}); expected {pixels} in {glyphs}"
                    )

    return failures


def _collect_stranded_extension_joins(
    *,
    max_chars_before: int,
    max_chars_after: int,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every unjoined adjacent pair where one side carries an extension suffix (``extended_exit_suffix`` on the left, ``extended_entry_suffix`` on the right) toward a partner that has no matching anchor. The sweep covers every ordered pair of plain Quikscript letters. The surrounds, the ligature matching, and ``before_first_only`` work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.

    An extension suffix means the bitmap grew toward the partner, so without a join that ink dangles. A glyph without an extension suffix draws no extra ink, so an unjoined pair is fine there. ``_is_intentional_gay_exit_extension`` exempts ·Gay's extended exit before ·It, ·I, ·Exam, or a ligature that starts with one of them.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()
    letters = _plain_quikscript_letters()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for left_base, _left_char in letters:
        for right_base, _right_char in letters:
            pair_text = _qs_text(left_base, right_base)
            for before in before_combos:
                before_label = "·".join(name for name, _ in before) if before else "∅"
                before_text = "".join(char for _, char in before)
                for after in after_combos:
                    after_label = "·".join(name for name, _ in after) if after else "∅"
                    after_text = "".join(char for _, char in after)
                    text = before_text + pair_text + after_text
                    glyphs = _shape(text)
                    label = f"[{before_label}] / {left_base} / {right_base} / [{after_label}]"

                    for index, glyph_name in enumerate(glyphs[:-1]):
                        left_meta = meta_map.get(glyph_name)
                        right_meta = meta_map.get(glyphs[index + 1])
                        if left_meta is None or right_meta is None:
                            continue
                        left_is_target = left_meta.base_name == left_base or (
                            left_meta.sequence and left_meta.sequence[-1] == left_base
                        )
                        right_is_target = right_meta.base_name == right_base or (
                            right_meta.sequence and right_meta.sequence[0] == right_base
                        )
                        if not left_is_target or not right_is_target:
                            continue

                        l_exit = _exit_ys(glyph_name)
                        r_entry = _entry_ys(glyphs[index + 1])
                        if l_exit & r_entry:
                            continue

                        if l_exit and left_meta.extended_exit_suffix is not None:
                            if _is_intentional_gay_exit_extension(left_meta, right_meta):
                                continue
                            failures.append(
                                f"{label}: {glyph_name} exits at Y={sorted(l_exit)} "
                                f"with extension {left_meta.extended_exit_suffix!r} but "
                                f"{glyphs[index + 1]} has no matching entry "
                                f"(entry Ys={sorted(r_entry)}) in {glyphs}"
                            )
                        if r_entry and right_meta.extended_entry_suffix is not None:
                            failures.append(
                                f"{label}: {glyphs[index + 1]} enters at "
                                f"Y={sorted(r_entry)} with extension "
                                f"{right_meta.extended_entry_suffix!r} but {glyph_name} has no "
                                f"matching exit (exit Ys={sorted(l_exit)}) in {glyphs}"
                            )

    return failures


def _it_roe_touching_rows(
    left_name: str,
    right_name: str,
    left_meta,
    right_meta,
    left_origin: int,
    right_origin: int,
) -> set[int]:
    """Return the glyph-space Y values where the rendered ink of two adjacent glyphs touches.

    For each row both bitmaps cover, the gap is the right glyph's leftmost ink edge minus the left glyph's rightmost ink edge, measured from the positioned origins. A gap of zero or less counts as touching. The gap math is the same as in `test_join_ink._check_ink_gap_at_y`, applied to every shared row.
    """
    touching: set[int] = set()
    left_top_y = left_meta.y_offset + len(left_meta.bitmap) - 1
    right_top_y = right_meta.y_offset + len(right_meta.bitmap) - 1
    y_min = max(left_meta.y_offset, right_meta.y_offset)
    y_max = min(left_top_y, right_top_y)
    left_bx = _bitmap_origin_x_offset(left_name, left_meta)
    right_bx = _bitmap_origin_x_offset(right_name, right_meta)
    for y in range(y_min, y_max + 1):
        left_ink = _ink_bounds_at_y(left_meta, y)
        right_ink = _ink_bounds_at_y(right_meta, y)
        if left_ink is None or right_ink is None:
            continue
        left_right_edge = left_origin + left_bx + (left_ink[1] + 1) * PIXEL_SIZE
        right_left_edge = right_origin + right_bx + right_ink[0] * PIXEL_SIZE
        if right_left_edge - left_right_edge <= 0:
            touching.add(y)
    return touching


def _collect_it_roe_join_only_at_cursive_join_row_failures(
    *,
    max_chars_before: int,
    max_chars_after: int,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every surround where ·It·Roe joins but its ink does not touch at the join row and only there.

    ·It·Roe has two valid joins in Senior:

    - At the x-height, as in ·Loch·It·Roe: ``qsIt.ex-y5`` meets ``qsRoe.en-ext-1-at-5``, whose top row is widened to ``####``. The ink touches only at y=5.
    - At the baseline, as in ·Low·It·Roe: ``qsIt.en-y5.ex-y0.ex-ext-1`` meets ``qsRoe.en-ext-1-at-0``, whose bottom row is widened. The ink touches only at y=0.

    The pair must join at exactly one height, 0 or 5, and the ink must touch at that row and no other. Bare ``qsRoe`` fails, because its ``###`` top and bottom rows both touch ·It's full-height column. Surrounds where ·It·Roe does not join (after ·Out·Tea, for example) are skipped. The surrounds and ``before_first_only`` work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()
    pair_text = _qs_text("qsIt", "qsRoe")

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + pair_text + after_text
            glyphs, positions = _shape_with_positions(text)
            origins = _origin_xs(positions)
            label = f"[{before_label}] / qsIt / qsRoe / [{after_label}]"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                if left_meta.base_name != "qsIt" or right_meta.base_name != "qsRoe":
                    continue

                join_ys = _pair_join_ys(glyphs, index)
                if not join_ys:
                    continue

                if join_ys - {0, 5}:
                    failures.append(
                        f"{label}: {glyph_name} -> {glyphs[index + 1]} cursive-joins at "
                        f"Y={sorted(join_ys)}, outside the expected x-height/baseline set "
                        f"in {glyphs}"
                    )
                    continue
                if len(join_ys) != 1:
                    failures.append(
                        f"{label}: {glyph_name} -> {glyphs[index + 1]} cursive-joins at "
                        f"multiple Ys {sorted(join_ys)} in {glyphs}"
                    )
                    continue
                (join_y,) = join_ys

                touching = _it_roe_touching_rows(
                    glyph_name,
                    glyphs[index + 1],
                    left_meta,
                    right_meta,
                    origins[index],
                    origins[index + 1],
                )
                if touching != {join_y}:
                    touching_label = sorted(touching) if touching else "(none)"
                    failures.append(
                        f"{label}: {glyph_name} -> {glyphs[index + 1]} cursive-joins at "
                        f"y={join_y} but rendered ink touches at Ys={touching_label} "
                        f"in {glyphs}"
                    )

    return failures


_IT_DAY_BASELINE_EXPECT = "·It ~b~ ·Day.half"


def _collect_it_day_baseline_uses_half_day_failures(
    *,
    max_chars_before: int = 1,
    max_chars_after: int = 1,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every surround where ·It does not join its predecessor at the baseline and ·It·Day does not shape as ``·It ~b~ ·Day.half``.

    An ·It that joins its predecessor at the baseline exits at the x-height; otherwise it exits at the baseline. ·Day's full stance enters at the x-height, where ·It·Day must not join (``test_it_day_never_joins_at_xheight``), so a baseline-exiting ·It can join ·Day only through ·Day's half stance, which enters at the baseline. A word-initial ·It counts as not joining its predecessor. Surrounds where ·It joins its predecessor at the baseline are skipped.

    The expect is checked with ``parse_expect`` and ``_try_interpretation``. The surrounds and ``before_first_only`` work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()
    pair_text = _qs_text("qsIt", "qsDay")

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    tokens, connections = parse_expect(_IT_DAY_BASELINE_EXPECT)
    fonts, anchor_maps, potentials = _senior_shaping_env()

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + pair_text + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / qsIt / qsDay / [{after_label}]"

            for index, glyph_name in enumerate(glyphs[:-1]):
                left_meta = meta_map.get(glyph_name)
                right_meta = meta_map.get(glyphs[index + 1])
                if left_meta is None or right_meta is None:
                    continue
                if left_meta.base_name != "qsIt" or right_meta.base_name != "qsDay":
                    continue

                joins_predecessor_at_baseline = index > 0 and 0 in _pair_join_ys(glyphs, index - 1)
                if joins_predecessor_at_baseline:
                    continue

                error = _try_interpretation(
                    fonts["senior"],
                    anchor_maps["senior"],
                    [glyph_name, glyphs[index + 1]],
                    tokens,
                    connections,
                    potentials["senior"],
                )
                if error:
                    failures.append(
                        f"{label}: expected {_IT_DAY_BASELINE_EXPECT!r} for the ·It·Day pair "
                        f"(·It does not join its predecessor at the baseline), but {error} in {glyphs}"
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
                    index
                    for index, glyph_name in enumerate(glyphs)
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


def test_see_ooze_is_sensible():
    _assert_expect_any(
        _qs_text("qsSee", "qsOoze"),
        [
            "·See.ex-y0-right ~b~ ·Ooze",
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
        pytest.param(_qs_text("qsMay", "qsFee"), ["·May.ex-ext-1 ~x~ ·Fee"], id="qsMay"),
        pytest.param(_qs_text("qsNo", "qsFee"), ["·No.ex-ext-1 ~x~ ·Fee"], id="qsNo"),
        pytest.param(_qs_text("qsLow", "qsFee"), ["·Low.ex-ext-1 ~x~ ·Fee"], id="qsLow"),
        pytest.param(_qs_text("qsAh", "qsFee"), ["·Ah.ex-ext-1 ~x~ ·Fee"], id="qsAh"),
        pytest.param(_qs_text("qsUtter", "qsFee"), ["·Utter.ex-ext-1 ~x~ ·Fee"], id="qsUtter"),
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


def test_owe_at_word_start_before_tea_with_ss03_has_no_left_anchor():
    """The ss03 counterpart of ``test_owe_at_word_start_before_fee_has_no_left_anchor``: under ss03, qsOwe's ``extend_exit_before_gated`` extends its exit before ·Tea, and a word-initial ·Owe must still have no entry anchor."""
    glyphs = _shape_qs("qsOwe", "qsTea", features=_SS03_FEATURE)
    assert _entry_ys(glyphs[0]) == set(), (
        f"word-initial qsOwe must not gain an entry anchor under ss03; " f"got glyphs={glyphs}"
    )


def test_way_does_not_join_tea_under_ss03():
    """·Way·Tea stays unjoined under ss03."""
    glyphs = _shape_qs("qsWay", "qsTea", features=_SS03_FEATURE)
    assert _pair_join_ys(glyphs, 0) == set(), f"·Way·Tea must not connect under ss03; got {glyphs}"


def test_fee_may_uses_extension_pair():
    """·Fee ~x~ ·May is drawn with ·Fee's extended ``before-may`` exit and ·May's ``after-fee`` entry stance, which uses the ``pulled_back_a_bit_for_entry_at_short_height_without_stubbie`` shape. The assertion leaves out the extension width (``extend_exit_before`` on qsFee's ``exit_xheight_before_may`` stance) so that it can be tuned without editing this test."""
    _assert_expect_any(
        _qs_text("qsFee", "qsMay"),
        ["·Fee.before-may ~x~ ·May.after-fee"],
    )


def test_owe_fee_may_owe_joins_fee_at_xheight():
    """·Owe joins ·Fee at the x-height, and ·Fee.en-y5 has no exit, so ·May stays unjoined. ·Fee cannot both enter and exit at the x-height (qsFee's ``notes``)."""
    _assert_expect_any(
        _qs_text("qsOwe", "qsFee", "qsMay"),
        ["·Owe ~x~ ·Fee | ·May"],
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
def test_owe_fee_may_under_each_stylistic_set(feature_label, feature_items):
    """Under each stylistic set, ·Owe·Fee·May forms no ligature, ·Owe joins ·Fee at the x-height, and ·Fee·May stays unjoined."""
    glyphs = _shape_qs("qsOwe", "qsFee", "qsMay", features=feature_items)
    assert len(glyphs) == 3, (
        f"·Owe·Fee·May should not ligate under features={feature_label}; " f"got {glyphs}"
    )
    assert _pair_join_ys(glyphs, 0) == {5}, (
        f"·Owe must reach into ·Fee at the x-height under features={feature_label}; " f"got {glyphs}"
    )
    assert _pair_join_ys(glyphs, 1) == set(), (
        f"·Fee.en-y5 has no exit; ·Fee→·May must not join under " f"features={feature_label}; got {glyphs}"
    )


@pytest.mark.parametrize(
    "letters",
    [
        pytest.param(("qsEt", "qsDay", "qsUtter"), id="et-day-utter"),
        pytest.param(("qsEt", "qsDay", "qsEat"), id="et-day-eat"),
        pytest.param(("qsEt", "qsEat"), id="et-eat"),
        pytest.param(("qsDay", "qsUtter"), id="day-utter"),
    ],
)
def test_ss10_forms_no_ligature_and_joins_nothing(letters):
    """Under ss10 each letter shapes as its own anchor-free twin: no ligature forms and no two letters join."""
    glyphs = _shape_qs(*letters, features=_SS10_FEATURE)
    assert _base_names(glyphs) == letters, f"ss10 must keep every letter separate; got {glyphs}"
    assert all(glyph.endswith(".ss10") for glyph in glyphs), f"ss10 must use each letter's twin; got {glyphs}"
    joined = [index for index in range(len(glyphs) - 1) if _pair_join_ys(glyphs, index)]
    assert not joined, f"ss10 must join nothing; got {glyphs}"


@cache
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
            glyphs = _shape_with_features(text, feature_items) if feature_items else _shape(text)
            for index, glyph in enumerate(glyphs[:-1]):
                next_glyph = glyphs[index + 1]
                next_meta = meta_map.get(next_glyph)
                left_meta = meta_map.get(glyph)
                if next_meta is None or left_meta is None:
                    continue
                if next_meta.base_name != lig_name:
                    continue
                if next_meta.is_noentry:
                    # A `.noentry` ligature lost its entry to a separate substitution (such as `noentry_after`), which is a different defect from the one this test checks.
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
def test_letter_does_not_reach_into_two_glyph_ligature(feature_items):
    _assert_no_failures(_no_orphan_exit_into_ligature_failures(feature_items))


def _word_initial_promoted_entry_failures(
    feature_items: tuple[tuple[str, bool], ...] = (),
) -> list[str]:
    """Return a failure for every word-initial glyph shaped as a variant with an entry anchor, over every ordered pair of distinct plain letters under the given features.

    The glyph passes if its family's bare stance has an entry anchor, or if a sibling other than a `.noentry` variant has the same bitmap and no entry, because then the entry anchor adds no visible ink.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    for left_name, left_char in _plain_quikscript_letters():
        for right_name, right_char in _plain_quikscript_letters():
            if left_name == right_name:
                continue
            text = left_char + right_char
            glyphs = _shape_with_features(text, feature_items) if feature_items else _shape(text)
            if not glyphs:
                continue
            head = glyphs[0]
            head_meta = meta_map.get(head)
            if head_meta is None or not head_meta.entry:
                continue
            base_meta = meta_map.get(head_meta.base_name)
            if base_meta is None or base_meta.entry:
                continue
            target_bitmap = head_meta.bitmap
            has_natural_no_entry_match = any(
                sibling_meta.bitmap == target_bitmap
                and not sibling_meta.entry
                and not sibling_meta.entry_curs_only
                and sibling_meta.noentry_for is None
                for sibling_name, sibling_meta in meta_map.items()
                if (sibling_meta.base_name == head_meta.base_name and sibling_name != head)
            )
            if has_natural_no_entry_match:
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


def test_middle_pea_xheight_left_join_is_limited_to_utter_and_may():
    _assert_no_failures(_middle_pea_xheight_left_gate_failures())


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


def test_zwnj_keeps_it_entryless_while_still_joining_zoo():
    _assert_expect_any(
        _qs_text("qsDay", ZWNJ, "qsIt", "qsZoo", "qsI", "qsRoe"),
        [
            "·Day | ◊ZWNJ | ·It.noentry.ex-ext-1 ~x~ ·Zoo ~b~ ·I.ex-ext-1 ~x~ ·Roe",
        ],
    )


def test_no_alt_selected_after_ox_before_fee():
    _assert_expect_any(
        _qs_text("qsOx", "qsNo", "qsFee"),
        ["·Ox ~b~ ·No.alt |?| ·Fee"],
    )


def _no_alt_after_baseline_exit_failures() -> list[str]:
    """Return a failure for every ·No that does not take its ``alt`` stance after a glyph with a baseline exit, over every ordered pair of plain letters around ·No. A ·Zoo predecessor is exempt."""
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


def test_no_alt_selected_when_preceded_by_baseline_exit():
    _assert_no_failures(_no_alt_after_baseline_exit_failures())


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsHe", "qsYe"), ["·He.∅ | ·Ye.∅"], id="he-ye"),
        pytest.param(_qs_text("qsIt", "qsYe"), ["·It.∅ | ·Ye.∅"], id="it-ye"),
        pytest.param(_qs_text("qsPea", "qsYe"), ["·Pea.∅ | ·Ye.∅"], id="pea-ye"),
        pytest.param(_qs_text("qsTea", "qsYe"), ["·Tea.∅ | ·Ye.∅"], id="tea-ye"),
        pytest.param(_qs_text("qsThey", "qsYe"), ["·They.∅ | ·Ye.∅"], id="they-ye"),
        pytest.param(_qs_text("qsWay", "qsYe"), ["·Way.∅ | ·Ye.∅"], id="way-ye"),
        pytest.param(_qs_text("qsWhy", "qsYe"), ["·Why.∅ | ·Ye.∅"], id="why-ye"),
        pytest.param(_qs_text("qsYe", "qsExam"), ["·Ye.∅ |?| ·Exam.nonjoining-left"], id="ye-exam"),
        pytest.param(_qs_text("qsYe", "qsExcite"), ["·Ye.∅ |?| ·Excite.nonjoining-left"], id="ye-excite"),
        pytest.param(_qs_text("qsYe", "qsIng"), ["·Ye.∅ |?| ·-ing.noentry"], id="ye-ing"),
        pytest.param(_qs_text("qsYe", "qsIt"), ["·Ye.∅ | ·It.∅"], id="ye-it"),
        pytest.param(_qs_text("qsYe", "qsSee"), ["·Ye.∅ |?| ·See.after-ye"], id="ye-see"),
    ],
)
def test_ye_sequences_keep_the_nonjoining_stances(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsYe", "qsIng", "qsThaw"), ["·Ye.∅ |?| ·-ing.noentry ~b~ ·Thaw"], id="ye-ing-thaw"
        ),
        pytest.param(
            _qs_text("qsHe", "qsIng", "qsThaw"), ["·He |?| ·-ing.noentry ~b~ ·Thaw"], id="he-ing-thaw"
        ),
    ],
)
def test_ing_never_joins_after_he_or_ye_even_before_thaw(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_they_may_keeps_manual_baseline_join():
    _assert_expect_any(
        _qs_text("qsThey", "qsMay"),
        ["·They.before-may ~b~ ·May"],
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
        pytest.param(_qs_text("qsMay", "qsNo"), ["·May.∅ ~x~ ·No"], id="may-no"),
        pytest.param(_qs_text("qsMay", "qsOwe"), ["·May.∅ ~x~ ·Owe"], id="may-owe"),
    ],
)
def test_may_exit_unextended_without_a_baseline_predecessor(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsHe", "qsExcite"), ["·He.∅ | ·Excite.∅"], id="he-excite"),
        pytest.param(_qs_text("qsOwe", "qsTea"), ["·Owe.∅ | ·Tea.∅"], id="owe-tea"),
        pytest.param(_qs_text("qsShe", "qsThaw"), ["·She.∅ |?| ·Thaw.after-tall"], id="she-thaw"),
        pytest.param(_qs_text("qsTea", "qsOwe"), ["·Tea.∅ | ·Owe.∅"], id="tea-owe"),
        pytest.param(_qs_text("qsWay", "qsExcite"), ["·Way.∅ | ·Excite.∅"], id="way-excite"),
        pytest.param(_qs_text("qsWay", "qsSee"), ["·Way.∅ | ·See.∅"], id="way-see"),
        pytest.param(_qs_text("qsWay", "qsTea"), ["·Way.∅ | ·Tea.∅"], id="way-tea"),
        pytest.param(_qs_text("qsWay", "qsVie"), ["·Way.∅ | ·Vie.∅"], id="way-vie"),
        pytest.param(_qs_text("qsWhy", "qsExcite"), ["·Why.∅ | ·Excite.∅"], id="why-excite"),
        pytest.param(_qs_text("qsWhy", "qsSee"), ["·Why.∅ | ·See.∅"], id="why-see"),
        pytest.param(_qs_text("qsWhy", "qsTea"), ["·Why.∅ | ·Tea.∅"], id="why-tea"),
        pytest.param(_qs_text("qsWhy", "qsThaw"), ["·Why.∅ | ·Thaw.∅"], id="why-thaw"),
        pytest.param(_qs_text("qsWhy", "qsVie"), ["·Why.∅ | ·Vie.∅"], id="why-vie"),
    ],
)
def test_nonjoining_pairs_do_not_connect(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


_PAIR_SWEEP_BEFORE_FIRSTS = tuple(name for name, _ in _context_chars())


def test_pair_sweep_shards_cover_every_surround_exactly_once():
    context_set = _context_chars()
    sharded = [
        combo
        for name in _PAIR_SWEEP_BEFORE_FIRSTS
        for combo in _surround_combos(context_set, 2, first_only=name)
    ]
    assert sorted(sharded) == sorted(_surround_combos(context_set, 2))


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_day_never_joins_at_xheight(before_first: str):
    _assert_no_failures(
        _collect_pair_must_not_join_at_y_regardless_of_what_comes_before_or_after(
            "qsIt",
            "qsDay",
            forbidden_y=5,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


def test_declared_exit_height_matches_exit_anchor():
    _assert_no_failures(_collect_declared_exit_height_without_matching_anchor_failures())


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_utter_gay_is_sensible_regardless_of_surroundings(before_first: str):
    _assert_no_failures(
        _collect_sequence_must_match_any_expect_regardless_of_what_comes_before_or_after(
            ["qsUtter", "qsGay"],
            ["·Utter.!alt ~x~ ·Gay", "·Utter.alt  ~b~ ·Gay", "·They+Utter ~x~ ·Gay"],
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


def test_utter_gay_tea_oy_uses_normal_utter():
    """In ·Utter·Gay·Tea·Oy, the non-alt ·Utter joins ·Gay at the x-height. ·Gay drops its exit because the ``qsTea_qsOy`` ligature has no entry."""
    _assert_expect_any(
        _qs_text("qsUtter", "qsGay", "qsTea", "qsOy"),
        ["·Utter.!alt ~x~ ·Gay.ex-noentry | ·Tea+Oy"],
    )


@pytest.mark.parametrize(
    "letters",
    [
        pytest.param(("qsMay", "qsGay", "qsOy", "qsTea", "qsOy"), id="may-gay-oy-tea-oy"),
        pytest.param(("qsMay", "qsGay", "qsOy", "qsThaw", "qsIng"), id="may-gay-oy-thaw-ing"),
    ],
)
def test_may_stays_bare_before_a_gay_with_no_baseline_entry(letters: tuple[str, ...]):
    """In ·May·Gay·Oy·Tea·Oy and ·May·Gay·Oy·Thaw·Ing, ·Gay falls back to `qsGay.ex-y5`, which has no baseline entry, so ·May keeps no baseline exit. A data-expect `|` can't catch the dangling exit, so this checks the glyph names."""
    glyphs = _shape_qs(*letters)
    assert glyphs[:2] == ["qsMay", "qsGay.ex-y5"], glyphs


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_see_out_connecting_body_only_when_next_receives_at_xheight(before_first: str):
    _assert_no_failures(
        _collect_see_out_connecting_body_without_xheight_receiver_failures(
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


def test_see_out_touch_body_wins_word_final_by_declared_precedence():
    """·Out has two connecting bodies after ·See: the touch body (`before-other`, +0px, as in ·See·Out·Oy) and the +1px body (`before-fee`, as in ·See·Out·Fee). Both are reverse upgrades with no lookahead, so the lookup emitted first also takes a word-final ·Out. The touch body's `terminal_default` flag puts its lookup first even though `before-other` sorts after `before-fee`. If that flag is dropped, or a sibling that sorts earlier is added, a word-final ·Out can take the +1px body and this test fails.

    Word-final ·See·Out ends up with the entry-trimmed after-·See stance, which has no x-height exit.
    """
    word_final = _shape(_qs_text("qsSee", "qsOut"))
    assert word_final[-1] == "qsOut.en-y0.after-see.en-trim-1", word_final
    assert "before-fee" not in word_final[-1]

    see_out_oy = _shape(_qs_text("qsSee", "qsOut", "qsOy"))
    assert "qsOut.en-y0.ex-y5.after-see.before-other" in see_out_oy, see_out_oy

    see_out_fee = _shape(_qs_text("qsSee", "qsOut", "qsFee"))
    assert "qsOut.en-y0.ex-y5.after-see.before-fee" in see_out_fee, see_out_fee


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_day_joins_half_day_at_baseline_when_it_has_no_baseline_predecessor(before_first: str):
    _assert_no_failures(
        _collect_it_day_baseline_uses_half_day_failures(
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_ye_it_never_joins_at_baseline(before_first: str):
    _assert_no_failures(
        _collect_pair_must_not_join_at_y_regardless_of_what_comes_before_or_after(
            "qsYe",
            "qsIt",
            forbidden_y=0,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_it_never_joins(before_first: str):
    _assert_no_failures(
        _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
            "qsIt",
            "qsIt",
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_eat_ye_never_joins(before_first: str):
    _assert_no_failures(
        _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
            "qsEat",
            "qsYe",
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_jay_he_never_joins(before_first: str):
    _assert_no_failures(
        _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
            "qsJay",
            "qsHe",
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_he_owe_never_joins(before_first: str):
    _assert_no_failures(
        _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
            "qsHe",
            "qsOwe",
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_ye_owe_never_joins(before_first: str):
    _assert_no_failures(
        _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
            "qsYe",
            "qsOwe",
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
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


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_jay_ye_extends_by_one_pixel_when_joined(before_first: str):
    _assert_no_failures(
        _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
            "qsJay",
            "qsYe",
            pixels=1,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_jai_ye_extends_by_one_pixel_when_joined(before_first: str):
    _assert_no_failures(
        _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
            "qsJai",
            "qsYe",
            pixels=1,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_owe_extends_by_one_pixel_when_joined(before_first: str):
    _assert_no_failures(
        _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
            "qsIt",
            "qsOwe",
            pixels=1,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_cheer_extends_by_one_pixel_when_joined(before_first: str):
    _assert_no_failures(
        _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
            "qsIt",
            "qsCheer",
            pixels=1,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_jai_extends_by_one_pixel_when_joined(before_first: str):
    _assert_no_failures(
        _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
            "qsIt",
            "qsJai",
            pixels=1,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_ye_i_extends_by_one_pixel_when_joined(before_first: str):
    _assert_no_failures(
        _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
            "qsYe",
            "qsI",
            pixels=1,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_i_extends_by_one_pixel_when_joined(before_first: str):
    _assert_no_failures(
        _collect_pair_extension_must_be_exactly_n_pixels_regardless_of_what_comes_before_or_after_if_they_join_at_all(
            "qsIt",
            "qsI",
            pixels=1,
            max_chars_before=2,
            max_chars_after=2,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_no_stranded_extension_joins_anywhere(before_first: str):
    _assert_no_failures(
        _collect_stranded_extension_joins(
            max_chars_before=1,
            max_chars_after=1,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_it_roe_join_only_touches_at_cursive_join_row(before_first: str):
    _assert_no_failures(
        _collect_it_roe_join_only_at_cursive_join_row_failures(
            max_chars_before=2,
            max_chars_after=0,
            before_first_only=before_first,
        ),
        limit=None,
    )


def _collect_letter_must_not_join_on_both_sides_at_the_same_height(
    middle_base: str,
    *,
    forbidden_y: int,
    max_chars_before: int = 1,
    max_chars_after: int = 1,
) -> list[str]:
    """Flag every ``middle_base`` glyph that joins both its neighbors at ``forbidden_y``. A join on one side only, or at another height, passes. The surrounds work as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.

    Ligatures are skipped, because a ligature carries ``middle_base``'s anchor on one side only.
    """
    failures: list[str] = []
    meta_map = _compiled_meta()
    context_set = _context_chars()
    middle_text = _qs_text(middle_base)

    before_combos = _surround_combos(context_set, max_chars_before)
    after_combos = _surround_combos(context_set, max_chars_after)

    for before in before_combos:
        before_label = "·".join(name for name, _ in before) if before else "∅"
        before_text = "".join(char for _, char in before)
        for after in after_combos:
            after_label = "·".join(name for name, _ in after) if after else "∅"
            after_text = "".join(char for _, char in after)
            text = before_text + middle_text + after_text
            glyphs = _shape(text)
            label = f"[{before_label}] / {middle_base} / [{after_label}]"

            for index in range(1, len(glyphs) - 1):
                middle_meta = meta_map.get(glyphs[index])
                if middle_meta is None or middle_meta.base_name != middle_base:
                    continue
                left_ys = _pair_join_ys(glyphs, index - 1)
                right_ys = _pair_join_ys(glyphs, index)
                if forbidden_y in left_ys and forbidden_y in right_ys:
                    failures.append(
                        f"{label}: {glyphs[index]} joined on both sides at height Y={forbidden_y} "
                        f"in {glyphs} (left Ys={sorted(left_ys)}, right Ys={sorted(right_ys)})"
                    )

    return failures


def test_it_never_joins_on_both_sides_at_xheight():
    _assert_no_failures(
        _collect_letter_must_not_join_on_both_sides_at_the_same_height("qsIt", forbidden_y=5),
        limit=None,
    )


def test_it_never_joins_on_both_sides_at_baseline():
    _assert_no_failures(
        _collect_letter_must_not_join_on_both_sides_at_the_same_height("qsIt", forbidden_y=0),
        limit=None,
    )


def test_pea_never_joins_on_both_sides_at_baseline():
    _assert_no_failures(
        _collect_letter_must_not_join_on_both_sides_at_the_same_height(
            "qsPea", forbidden_y=0, max_chars_before=1, max_chars_after=1
        ),
        limit=None,
    )


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
        pytest.param(_qs_text("qsEt", "qsPea", "qsIt"), ["·Et | ·Pea.half ~x~ ·It"], id="et-pea-it"),
        pytest.param(_qs_text("qsAwe", "qsPea", "qsPea"), ["·Awe | ·Pea.half ~6~ ·Pea"], id="awe-pea-pea"),
        pytest.param(_qs_text("qsEt", "qsPea", "qsDay"), ["·Et | ·Pea ~b~ ·Day.half"], id="et-pea-day"),
        pytest.param(
            _qs_text("qsAwe", "qsPea", "qsVie"), ["·Awe | ·Pea ~b~ ·Vie.en-ext-1"], id="awe-pea-vie"
        ),
        pytest.param(_qs_text("qsEt", "qsPea", "qsMay"), ["·Et | ·Pea ~b~ ·May.en-ext-1"], id="et-pea-may"),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsDay", "qsEat"), ["·Et | ·Pea ~b~ ·Day+Eat.half"], id="et-pea-day-eat"
        ),
        pytest.param(
            _qs_text("qsAwe", "qsPea", "qsDay", "qsEat"),
            ["·Awe | ·Pea ~b~ ·Day+Eat.half"],
            id="awe-pea-day-eat",
        ),
    ],
)
def test_pea_keeps_its_onward_join_after_et_and_awe(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def _pea_offset_from_its_predecessor(text: str, *, kern: bool) -> tuple[list[str], int]:
    """Shape `text`, whose first two glyphs are ·Et or ·Awe and then ·Pea, with the `kern` feature on or off, and return the glyph names and the font-unit distance from the first glyph's origin to ·Pea's."""
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(_font(), buf, {"kern": kern})
    glyphs = [_gid_to_full_name(info.codepoint) for info in buf.glyph_infos]
    origins = _origin_xs(buf.glyph_positions)
    return glyphs, origins[1] - origins[0]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_qs_text("qsEt", "qsPea"), id="et-pea"),
        pytest.param(_qs_text("qsAwe", "qsPea"), id="awe-pea"),
        pytest.param(_qs_text("qsEt", "qsPea", "qsTea"), id="et-pea-tea"),
        pytest.param(_qs_text("qsEt", "qsPea", "qsAt", "qsMay"), id="et-pea-at-may"),
        pytest.param(_qs_text("qsAwe", "qsPea", "qsSee", "qsEat"), id="awe-pea-see-eat"),
    ],
)
def test_pea_joined_to_et_and_awe_at_baseline_is_not_kerned(text: str):
    glyphs, kerned = _pea_offset_from_its_predecessor(text, kern=True)
    assert _pair_join_ys(glyphs, 0) == {0}, glyphs
    _, unkerned = _pea_offset_from_its_predecessor(text, kern=False)
    assert (
        kerned == unkerned
    ), f"{glyphs}: ·Pea sits {kerned - unkerned} units from where its baseline join puts it"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_qs_text("qsEt", "qsPea", "qsIt"), id="et-pea-it"),
        pytest.param(_qs_text("qsAwe", "qsPea", "qsDay"), id="awe-pea-day"),
        pytest.param(_qs_text("qsEt", "qsPea", "qsUtter", "qsAh"), id="et-pea-utter-ah"),
    ],
)
def test_pea_not_joined_to_et_and_awe_keeps_its_kern(text: str):
    glyphs, kerned = _pea_offset_from_its_predecessor(text, kern=True)
    assert not _pair_join_ys(glyphs, 0), glyphs
    _, unkerned = _pea_offset_from_its_predecessor(text, kern=False)
    assert kerned - unkerned == -3 * PIXEL_SIZE, glyphs


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(_qs_text("qsPea", "qsDay", "qsEat"), ["·Pea ~b~ ·Day+Eat.half"], id="pea-day-eat"),
        pytest.param(_qs_text("qsIt", "qsDay", "qsEat"), ["·It ~b~ ·Day+Eat.half"], id="it-day-eat"),
        pytest.param(
            _qs_text("qsEt", "qsTea", "qsDay", "qsEat"), ["·Et ~b~ ·Tea | ·Day+Eat.∅"], id="et-tea-day-eat"
        ),
        pytest.param(
            _qs_text("qsOut", "qsTea", "qsDay", "qsEat"), ["·Out+Tea | ·Day+Eat.∅"], id="out-tea-day-eat"
        ),
        pytest.param(
            _qs_text("qsSee", "qsOut", "qsTea", "qsDay", "qsEat"),
            ["·See ~b~ ·Out+Tea | ·Day+Eat.∅"],
            id="see-out-tea-day-eat",
        ),
        pytest.param(
            _qs_text("qsThey", "qsZoo", "qsNo", "qsDay", "qsEat"),
            ["·They+Zoo | ·No ~x~ ·Day+Eat.∅"],
            id="they-zoo-no-day-eat",
        ),
        pytest.param(
            _qs_text("qsThey", "qsZoo", "qsExcite", "qsDay", "qsEat"),
            ["·They+Zoo | ·Excite | ·Day+Eat.∅"],
            id="they-zoo-excite-day-eat",
        ),
        pytest.param(
            _qs_text("qsOut", "qsTea", "qsJai", "qsDay", "qsEat"),
            ["·Out+Tea | ·J’ai | ·Day+Eat.∅"],
            id="out-tea-jai-day-eat",
        ),
        pytest.param(
            _qs_text("qsHe", "qsNo", "qsIt", "qsDay", "qsEat"),
            ["·He.half ~x~ ·No ~x~ ·It ~b~ ·Day+Eat.half"],
            id="he-no-it-day-eat",
        ),
        pytest.param(
            _qs_text("qsPea", "qsDay", "qsEat", "qsGay", "qsOwe"),
            ["·Pea ~b~ ·Day+Eat.half | ·Gay | ·Owe"],
            id="pea-day-eat-gay-owe",
        ),
        pytest.param(
            _qs_text("qsTea", "qsDay", "qsEat", "qsGay", "qsOwe"),
            ["·Tea ~b~ ·Day+Eat.half.en-ext-1 | ·Gay | ·Owe"],
            id="tea-day-eat-gay-owe",
        ),
    ],
)
def test_day_eat_takes_the_half_day_where_day_does(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_pea_joins_neither_side_after_et_before_the_alternate_utter():
    _assert_expect_any(_qs_text("qsEt", "qsPea", "qsUtter", "qsAh"), ["·Et | ·Pea | ·Utter.alt ~b~ ·Ah"])


@pytest.mark.parametrize(
    ("text", "expects"),
    [
        pytest.param(
            _qs_text("qsPea", "qsPea", "qsCheer"),
            ["·Pea.half ~6~ ·Pea.half ~x~ ·Cheer"],
            id="pea-pea-cheer",
        ),
        pytest.param(
            _qs_text("qsEt", "qsPea", "qsPea", "qsCheer"),
            ["·Et | ·Pea.half ~6~ ·Pea.half ~x~ ·Cheer"],
            id="et-pea-pea-cheer",
        ),
        pytest.param(
            _qs_text("qsPea", "qsPea", "qsIt"), ["·Pea.half ~6~ ·Pea.half ~x~ ·It"], id="pea-pea-it"
        ),
        pytest.param(
            _qs_text("qsPea", "qsPea", "qsNo"), ["·Pea.half ~6~ ·Pea.half ~x~ ·No"], id="pea-pea-no"
        ),
    ],
)
def test_pea_joins_a_pea_that_dips_into_its_follower_at_y6(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def _pea_joins_neither_side_before(glyph: str) -> bool:
    """Return whether ·Pea after ·Et or ·Awe joins neither side before `glyph`: ·Utter's alternate or an exit-only ·See stance. doc/quikscript-yaml-conventions.md describes this accepted limit."""
    meta = _compiled_meta().get(glyph)
    if meta is None:
        return False
    if meta.base_name == "qsUtter":
        return "alt" in meta.traits
    if meta.base_name == "qsSee":
        return not meta.entry
    return False


def _pea_after_et_and_awe_seam_failures() -> list[str]:
    letters = [name for name, _ in _plain_quikscript_letters()]
    followers = [(name,) for name, _ in _context_chars()] + [()]
    followers += list(product(letters, repeat=2))
    failures: list[str] = []
    for follower in followers:
        right_ys = _pair_join_ys(_shape_qs("qsPea", *follower), 0)
        for left in ("qsEt", "qsAwe"):
            glyphs = _shape_qs(left, "qsPea", *follower)
            label = "·".join((left, "qsPea", *follower))
            if _base_names(glyphs)[:2] != (left, "qsPea"):
                failures.append(f"{label}: expected {left} then qsPea at the start of {glyphs}")
                continue
            seam_ys = _pair_join_ys(glyphs, 1)
            if seam_ys != right_ys:
                failures.append(
                    f"{label}: qsPea's right seam Ys {sorted(seam_ys)} differ from {sorted(right_ys)} without {left} in {glyphs}"
                )
            expects_join = not right_ys and not (
                len(glyphs) > 2 and _pea_joins_neither_side_before(glyphs[2])
            )
            if (0 in _pair_join_ys(glyphs, 0)) != expects_join:
                failures.append(
                    f"{label}: {left} should join qsPea at Y=0 exactly when qsPea breaks after, in {glyphs}"
                )
    return failures


def test_et_and_awe_join_pea_at_baseline_exactly_when_pea_breaks_after():
    _assert_no_failures(_pea_after_et_and_awe_seam_failures(), limit=None)


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
def test_way_and_why_stay_full_and_nonjoining_before_right_base_in_context(left_base: str, right_base: str):
    failures = _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(left_base, right_base)
    failures += _collect_pair_with_forbidden_trait_co_occurrence_failures(
        left_base, right_base, forbidden_left_traits=frozenset({"half"})
    )
    _assert_no_failures(failures)


@pytest.mark.parametrize(
    ("left_base", "left_traits", "right_base", "right_traits"),
    [
        pytest.param(
            "qsWay",
            frozenset({"half"}),
            "qsUtter",
            frozenset({"alt"}),
            id="way-half-before-utter-alt",
        ),
        pytest.param(
            "qsWhy",
            frozenset({"half"}),
            "qsUtter",
            frozenset({"alt"}),
            id="why-half-before-utter-alt",
        ),
    ],
)
def test_no_forbidden_trait_co_occurrence_on_adjacent_pair(
    left_base: str,
    left_traits: frozenset[str],
    right_base: str,
    right_traits: frozenset[str],
):
    failures = _collect_pair_with_forbidden_trait_co_occurrence_failures(
        left_base,
        right_base,
        forbidden_left_traits=left_traits,
        forbidden_right_traits=right_traits,
    )
    _assert_no_failures(failures)


def test_day_always_picks_half_after_it_in_any_context():
    _assert_no_failures(_collect_joined_right_not_half_failures("qsIt", "qsDay"))


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsOwe", "qsTea", id="owe-tea"),
        pytest.param("qsPea", "qsOwe", id="pea-owe"),
        pytest.param("qsShe", "qsThaw", id="she-thaw"),
        pytest.param("qsTea", "qsOwe", id="tea-owe"),
        pytest.param("qsWay", "qsSee", id="way-see"),
        pytest.param("qsWay", "qsVie", id="way-vie"),
        pytest.param("qsWhy", "qsSee", id="why-see"),
        pytest.param("qsWhy", "qsThaw", id="why-thaw"),
        pytest.param("qsWhy", "qsVie", id="why-vie"),
    ],
)
def test_nonjoining_pairs_do_not_connect_in_context(left_base: str, right_base: str):
    failures = _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
        left_base, right_base, max_chars_before=1, max_chars_after=0
    )
    failures += _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
        left_base, right_base, max_chars_before=0, max_chars_after=1
    )
    _assert_no_failures(failures)


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsMay", id="may"),
        pytest.param("qsNo", id="no"),
        pytest.param("qsFoot", id="foot"),
    ],
)
def test_may_no_and_foot_never_join_to_they_utter(left_base: str):
    _assert_no_failures(
        _collect_nonjoining_left_ligature_failures(
            left_base,
            "qsThey_qsUtter",
            ("qsThey", "qsUtter"),
        ),
        limit=None,
    )


def test_she_stays_plain_and_nonjoining_before_thaw_in_context():
    failures = _collect_pair_must_not_join_regardless_of_what_comes_before_or_after("qsShe", "qsThaw")
    failures += _collect_left_must_stay_isolated_before_right_failures("qsShe", "qsThaw")
    _assert_no_failures(failures)


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


def _may_thaw_orphan_failures(glyphs: list[str], label: str) -> list[str]:
    """Return a failure for every adjacent ·May·Thaw where ·May took an ``ex-y0`` variant but the ·Thaw variant has no baseline entry.

    Only ``ex-y0`` is checked because ·May's default and ``.noentry`` stances exit at the x-height, which never joins ·Thaw. The defect is ·May switching to ``ex-y0`` for ·Thaw's default baseline entry and ·Thaw's own substitution then removing that entry.
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
        if "ex-y0" not in left_meta.modifiers:
            continue
        right_entries = set(right_meta.entry_ys) | {anchor[1] for anchor in right_meta.entry_curs_only}
        if 0 not in right_entries:
            failures.append(
                f"{label}: qsMay picked ex-y0 ({glyph}) but adjacent "
                f"qsThaw variant {glyphs[index + 1]} has no baseline entry "
                f"(entries Y={sorted(right_entries)}) in {glyphs}"
            )
    return failures


def _no_thaw_alt_failures(glyphs: list[str], label: str) -> list[str]:
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
        right_entries = set(right_meta.entry_ys) | {anchor[1] for anchor in right_meta.entry_curs_only}
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
def test_may_thaw_pair_never_orphans_in_left_context(suffix_name: str):
    failures = _may_thaw_orphan_failures(
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
def test_may_thaw_ing_surrounded_is_never_orphaned(left_name: str, right_name: str):
    failures = _may_thaw_orphan_failures(
        _shape_qs(left_name, "qsMay", "qsThaw", "qsIng", right_name),
        f"{left_name} / qsMay / qsThaw / qsIng / {right_name}",
    )
    _assert_no_failures(failures)


def test_may_thaw_stays_isolated_across_zwnj():
    chars = _char_map()
    text = chars["qsTea"] + ZWNJ + chars["qsMay"] + chars["qsThaw"] + chars["qsIng"]
    _assert_expect_any(
        text,
        [
            "·Tea | ◊ZWNJ | ·May.noentry.!ex-y0 | ·Thaw ~b~ ·-ing",
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


@pytest.mark.parametrize(
    "left_name",
    [pytest.param(name, id=name[2:].lower()) for name, _ in _plain_quikscript_letters()],
)
@pytest.mark.parametrize(
    "right_name",
    [pytest.param(name, id=name[2:].lower()) for name, _ in _plain_quikscript_letters()],
)
def test_no_thaw_ing_surrounded_does_not_select_alt(left_name: str, right_name: str):
    failures = _no_thaw_alt_failures(
        _shape_qs(left_name, "qsNo", "qsThaw", "qsIng", right_name),
        f"{left_name} / qsNo / qsThaw / qsIng / {right_name}",
    )
    _assert_no_failures(failures)


def test_no_thaw_stays_non_alt_across_zwnj():
    chars = _char_map()
    text = chars["qsTea"] + ZWNJ + chars["qsNo"] + chars["qsThaw"] + chars["qsIng"]
    _assert_expect_any(
        text,
        [
            "·Tea | ◊ZWNJ | ·No.noentry.!alt | ·Thaw ~b~ ·-ing",
        ],
    )


def test_excite_tea_connect_at_baseline():
    _assert_expect_any(_qs_text("qsExcite", "qsTea"), ["·Excite ~b~ ·Tea"])


@pytest.mark.parametrize(
    "text,expects",
    [
        pytest.param(
            _qs_text("qsShe", "qsExcite", "qsBay"),
            ["·She ~b~ ·Excite.noexit | ·Bay"],
            id="after-she",
        ),
        pytest.param(
            _qs_text("qsDay", "qsExcite", "qsBay"),
            ["·Day ~b~ ·Excite.noexit | ·Bay"],
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
            ["·She ~b~ ·Excite.after-baseline-letter ~b~ ·Tea"],
            id="after-she",
        ),
        pytest.param(
            _qs_text("qsDay", "qsExcite", "qsTea"),
            ["·Day ~b~ ·Excite.after-baseline-letter ~b~ ·Tea"],
            id="after-day",
        ),
    ],
)
def test_excite_reaches_both_sides_when_neighbors_offer_baseline(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_excite_reaches_left_only_at_word_end_after_baseline_exit():
    _assert_expect_any(_qs_text("qsShe", "qsExcite"), ["·She ~b~ ·Excite.noexit"])


def test_excite_stays_mono_at_word_start_before_xheight_entry():
    _assert_expect_any(_qs_text("qsExcite", "qsBay"), ["·Excite.∅ | ·Bay.∅"])


def test_excite_reaches_left_only_before_thaw():
    _assert_expect_any(
        _qs_text("qsShe", "qsExcite", "qsThaw"),
        ["·She ~b~ ·Excite.noexit |?| ·Thaw"],
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
        ["·It ~b~ ·Excite.noexit"],
    )


def test_pea_excite_excite_uses_the_visible_final_excite_entry_shape():
    _assert_expect_any(
        _qs_text("qsPea", "qsExcite", "qsExcite"),
        ["·Pea.∅ | ·Excite.before-vertical.noentry ~b~ ·Excite.noexit"],
    )


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
            elif "en-y0" not in assertions and "after-baseline-letter" not in assertions:
                failures.append(
                    f"{' / '.join(parts)}: {right_name} joins left at baseline but "
                    f"does not use a visible baseline-entry shape in {glyphs}"
                )

    return failures


def test_excite_baseline_receivers_use_visible_entry_shapes():
    _assert_no_failures(_excite_baseline_receiver_shape_failures(), limit=None)


@pytest.mark.parametrize(
    "right_base",
    [
        pytest.param("qsAh", id="before-ah"),
        pytest.param("qsAwe", id="before-awe"),
    ],
)
def test_excite_tea_keeps_left_join_when_the_follower_still_supports_it(right_base: str):
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


def test_excite_tea_does_not_keep_the_baseline_exit_before_ox():
    _assert_expect_any(
        _qs_text("qsExcite", "qsTea", "qsOx"),
        ["·Excite.∅ | ·Tea.half ~x~ ·Ox"],
    )


def test_excite_tea_keeps_the_baseline_join_before_tea():
    pair = _shape_qs("qsExcite", "qsTea")
    triple = _shape_qs("qsExcite", "qsTea", "qsTea")
    assert _base_names(pair) == ("qsExcite", "qsTea")
    assert _base_names(triple) == ("qsExcite", "qsTea", "qsTea")
    _assert_join_preserved("qsExcite / qsTea / qsTea", pair, triple, pair_index_in_triple=0)
    assert not _pair_join_ys(triple, 1)


def test_excite_tea_does_not_keep_the_baseline_exit_before_out():
    pair = _shape_qs("qsTea", "qsOut")
    triple = _shape_qs("qsExcite", "qsTea", "qsOut")
    assert _base_names(pair) == ("qsTea", "qsOut")
    assert _base_names(triple) == ("qsExcite", "qsTea", "qsOut")
    assert triple[0] == "qsExcite"
    assert not _pair_join_ys(triple, 0)
    _assert_join_preserved("qsExcite / qsTea / qsOut", pair, triple[1:], pair_index_in_triple=0)
    assert not _exit_ys(triple[0]), triple


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
        pytest.param(_qs_text("qsEt", "qsTea", "qsAh"), ["·Et.∅ ~b~ ·Tea | ·Ah.∅"], id="before-ah"),
        pytest.param(_qs_text("qsEt", "qsTea", "qsOut"), ["·Et.∅ ~b~ ·Tea | ·Out.∅"], id="before-out"),
        pytest.param(_qs_text("qsEt", "qsTea", "qsMay"), ["·Et.∅ ~b~ ·Tea | ·May.∅"], id="before-may"),
        pytest.param(_qs_text("qsEt", "qsTea", "qsIng"), ["·Et.∅ ~b~ ·Tea | ·-ing.∅"], id="before-ing"),
        pytest.param(_qs_text("qsEt", "qsTea", "qsVie"), ["·Et.∅ ~b~ ·Tea | ·Vie.∅"], id="before-vie"),
        pytest.param(_qs_text("qsEt", "qsTea", "qsDay"), ["·Et.∅ ~b~ ·Tea | ·Day.∅"], id="before-day"),
    ],
)
def test_et_tea_keeps_only_the_left_baseline_join_in_plain_right_contexts(text: str, expects: list[str]):
    _assert_expect_any(text, expects)


def test_et_tea_can_double_join_at_baseline_in_ss05():
    glyphs = _shape_qs(
        "qsBay",
        "qsEt",
        "qsTea",
        "qsUtter",
        "qsRoe",
        features=_SS05_FEATURE,
    )
    assert glyphs == ["qsBay", "qsEt", "qsTea.en-y0.ex-y0", "qsUtter", "qsRoe"]
    assert not _pair_join_ys(glyphs, 0)
    assert _pair_join_ys(glyphs, 1) == {0}
    assert _pair_join_ys(glyphs, 2) == {0}
    assert _pair_join_ys(glyphs, 3) == {5}


def test_it_excite_does_not_force_tea_out_of_half_before_it():
    _assert_expect_any(
        _qs_text("qsIt", "qsExcite", "qsTea", "qsIt"),
        ["·It ~b~ ·Excite | ·Tea.half ~x~ ·It"],
    )


@pytest.mark.parametrize(
    "left_base",
    [
        pytest.param("qsTea", id="tea"),
        pytest.param("qsPea", id="pea"),
        pytest.param("qsYe", id="ye"),
    ],
)
def test_nonjoining_left_context_preserves_excite_ah_join(left_base: str):
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


@pytest.mark.parametrize(
    ("left_base", "right_base"),
    [
        pytest.param("qsTea", "qsExcite", id="tea-excite"),
        pytest.param("qsExam", "qsTea", id="exam-tea"),
        pytest.param("qsTea", "qsExam", id="tea-exam"),
        pytest.param("qsTea", "qsThaw", id="tea-thaw"),
        pytest.param("qsIt", "qsExam", id="it-exam"),
    ],
)
def test_qs_nonjoining_pairs_stay_nonjoining_in_context(left_base: str, right_base: str):
    failures = _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
        left_base, right_base, max_chars_before=1, max_chars_after=0
    )
    failures += _collect_pair_must_not_join_regardless_of_what_comes_before_or_after(
        left_base, right_base, max_chars_before=0, max_chars_after=1
    )
    _assert_no_failures(failures)


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


def test_excite_nonjoining_left_context_preserves_right_join_in_plain_triples():
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


def test_excite_tea_only_keeps_the_left_join_when_the_final_tea_still_supports_it():
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


def test_out_tea_prefers_the_ligature_over_right_joins():
    failures = _out_tea_prefers_the_ligature_over_right_joins_failures()
    assert not failures, "\n".join(failures[:50])


def _et_tea_keeps_the_left_join_and_blocks_right_join_failures() -> list[str]:
    failures: list[str] = []
    chars = _char_map()

    for right_name, right_char in _plain_quikscript_letters():
        glyphs = _shape(chars["qsEt"] + chars["qsTea"] + right_char)
        if right_name == "qsOy":
            if glyphs != ["qsEt", "qsTea_qsOy"]:
                failures.append(f"qsEt / qsTea / qsOy: expected ['qsEt', 'qsTea_qsOy'], got {glyphs}")
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


def test_et_tea_only_keeps_the_left_baseline_join_except_before_oy():
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


def test_et_tea_nonjoining_right_context_keeps_right_glyph_plain():
    failures = _et_tea_nonjoining_right_context_keeps_right_glyph_plain_failures()
    assert not failures, "\n".join(failures[:50])


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


def test_utter_alt_variants_always_keep_the_joins_they_require():
    failures = _utter_alt_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_tea_before_i_extends_exit():
    _assert_expect_any(
        _qs_text("qsTea", "qsI"),
        ["·Tea.en-y8.ex-ext-1 ~b~ ·I"],
    )


def test_see_tea_i_extends_exit():
    _assert_expect_any(
        _qs_text("qsSee", "qsTea", "qsI"),
        ["·See.∅ ~t~ ·Tea.ex-ext-1 ~b~ ·I.∅"],
    )


def test_fee_tea_i_extends_exit():
    _assert_expect_any(
        _qs_text("qsFee", "qsTea", "qsI"),
        ["·Fee.∅ |?| ·Tea.en-y8.ex-ext-1 ~b~ ·I.∅"],
    )


def test_et_tea_i_preserves_left_only_invariant():
    _assert_expect_any(
        _qs_text("qsEt", "qsTea", "qsI"),
        ["·Et ~b~ ·Tea | ·I.∅"],
    )


def test_et_tea_i_extends_exit_in_ss05():
    chars = _char_map()
    glyphs = _shape_with_features(
        chars["qsEt"] + chars["qsTea"] + chars["qsI"],
        (("ss05", True),),
    )
    assert glyphs == [
        "qsEt",
        "qsTea.en-y0.ex-y0.ex-ext-1",
        "qsI",
    ]
    assert _pair_join_ys(glyphs, 0) == {0}
    assert _pair_join_ys(glyphs, 1) == {0}


def test_i_before_tea_unchanged_by_forward_extension():
    _assert_expect_any(_qs_text("qsI", "qsTea"), ["·I.∅ | ·Tea.!ex-ext-1"])


# ---------------------------------------------------------------------------
# ·Way·Day must always use full-height ·Way and full-height ·Day.
#
# The tests below check that the 2-glyph preferred-lookahead rule does not pick qsWay.half before a middle letter, such as ·Day, that cannot carry qsWay.half's y=0 exit up to a y=5 entry on the third letter. Otherwise ·Way·Day·X would shape as qsWay.half·qsDay.half·X with no join between ·Day and X.
# ---------------------------------------------------------------------------


_DAY_PAIR_LIGATURES = frozenset(
    {
        # (qsDay, follower) pairs that form a qsDay_qs<follower> ligature, which leaves no standalone qsDay glyph to inspect.
        ("qsDay", "qsEat"),
        ("qsDay", "qsUtter"),
    }
)


def _non_bridging_middle_bases() -> list[tuple[str, str]]:
    """Return the plain letters whose variants enter at both y=0 and y=5 but none of which enters at y=0 and exits at y=5. Such a letter cannot carry qsWay.half's y=0 exit on to a follower that enters only at y=5.

    Letters that enter only at y=0 (such as ·Ah, ·Exam, and ·Excite) are excluded: the 1-glyph rule `sub qsWay' @entry_only_y0 by qsWay.half.ex-y0;` picks half-·Way before them, which is correct.
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
        can_bridge_y0_to_y5 = any(0 in v.entry_ys and 5 in v.exit_ys for v in variants)
        if can_enter_y0 and can_enter_y5 and not can_bridge_y0_to_y5:
            result.append((base_name, base_char))
    return result


def _way_not_half_before_non_bridging_failures() -> list[str]:
    """Return a failure for every ·Way·M·X that picks half-·Way, where M is from ``_non_bridging_middle_bases`` and X is any plain letter."""
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
            # A ligature led by ·Way, such as qsWay_qsUtter, draws a full ·Way.
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


def test_way_full_before_any_non_bridging_middle():
    failures = _way_not_half_before_non_bridging_failures()
    assert not failures, "\n".join(failures[:50])


# ---------------------------------------------------------------------------
# ·Way and ·Why must stay full before ·Vie and ·See, the pair must not connect, and the right glyph must not change shape because of a preceding ·Way / ·Why.
#
# ·Way's prop exits only at y=5 and ·Why's prop has no exit. ·Vie's and ·See's props enter only at y=0.
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
def test_right_glyph_unchanged_after_way_or_why(left_base: str, right_base: str):
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
def test_half_stance_not_before_list_keeps_left_full(left_base: str):
    """Before every family in the ``not_before`` list of ``<base>.half.ex-y0``, ``<base>`` must not take a half variant, both as a bare pair and between any two plain letters. The test reads the list from the compiled glyph, so a family added to it is covered automatically.

    A family is in ``not_before`` either because the full stance joins it at the x-height (such as ·It) or because the pair must stay unjoined (such as ·See, ·Tea, ·Thaw, and ·Vie). The tests above check the joins; this test checks only that ``<base>`` stays full.
    """
    half_meta = _compiled_meta()[f"{left_base}.half.ex-y0"]
    deny_families = sorted(half_meta.not_before)
    assert deny_families, f"{left_base}.half.ex-y0 should declare a non-empty not_before"

    meta_map = _compiled_meta()
    failures: list[str] = []
    for right_base in deny_families:
        assert any(
            m.base_name == right_base for m in meta_map.values()
        ), f"{right_base} declared in {left_base}.half not_before but absent from compiled meta"

        pair = _shape_qs(left_base, right_base)
        pair_meta = meta_map[pair[0]]
        if pair_meta.base_name == left_base and "half" in pair_meta.traits:
            failures.append(f"{left_base} / {right_base}: half-{left_base} selected: {pair}")

        for outer_left_name, _ in _plain_quikscript_letters():
            for outer_right_name, _ in _plain_quikscript_letters():
                glyphs = _shape_qs(outer_left_name, left_base, right_base, outer_right_name)
                for index, glyph_name in enumerate(glyphs[:-1]):
                    left_glyph_meta = meta_map.get(glyph_name)
                    right_glyph_meta = meta_map.get(glyphs[index + 1])
                    if left_glyph_meta is None or right_glyph_meta is None:
                        continue
                    if left_glyph_meta.base_name != left_base or right_glyph_meta.base_name != right_base:
                        continue
                    if "half" in left_glyph_meta.traits:
                        failures.append(
                            f"{outer_left_name} / {left_base} / {right_base} / "
                            f"{outer_right_name}: half-{left_base} selected: {glyphs}"
                        )

    _assert_no_failures(failures)


# ---------------------------------------------------------------------------
# By default, ·Owe never joins a following ·Day or a ligature that starts with ·Day. Stylistic set ss07 joins them at the x-height, as The Manual does.
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
            failures.append(f"{label}: Owe joins {right_meta.base_name} at Y={sorted(common)} in {glyphs}")
    return failures


def test_owe_day_utter_ligature_does_not_connect():
    chars = _char_map()
    glyphs = _shape(chars["qsOwe"] + chars["qsDay"] + chars["qsUtter"])
    failures = _owe_day_failures_in(glyphs, "qsOwe / qsDay / qsUtter")
    assert not failures, "\n".join(failures)


def test_owe_day_eat_ligature_does_not_connect():
    chars = _char_map()
    glyphs = _shape(chars["qsOwe"] + chars["qsDay"] + chars["qsEat"])
    failures = _owe_day_failures_in(glyphs, "qsOwe / qsDay / qsEat")
    assert not failures, "\n".join(failures)


def _owe_day_joins_at_y5_in(glyphs: list[str], label: str) -> list[str]:
    """Return a failure for every ·Owe followed by a ``_DAY_BASES`` glyph that does not join it at y=5, plus one failure if no such pair appears. This is the ss07 counterpart of ``_owe_day_failures_in``."""
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


def test_owe_day_connects_under_ss07():
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


def test_owe_day_connects_under_ss07_in_context():
    failures = _owe_day_ss07_invariant_failures()
    assert not failures, "\n".join(failures[:50])


def test_owe_day_utter_ligature_connects_under_ss07():
    chars = _char_map()
    glyphs = _shape_with_features(chars["qsOwe"] + chars["qsDay"] + chars["qsUtter"], _SS07_FEATURE)
    failures = _owe_day_joins_at_y5_in(glyphs, "qsOwe / qsDay / qsUtter (ss07)")
    assert not failures, "\n".join(failures)


def test_owe_day_eat_ligature_connects_under_ss07():
    chars = _char_map()
    glyphs = _shape_with_features(chars["qsOwe"] + chars["qsDay"] + chars["qsEat"], _SS07_FEATURE)
    failures = _owe_day_joins_at_y5_in(glyphs, "qsOwe / qsDay / qsEat (ss07)")
    assert not failures, "\n".join(failures)


_GAY_CONTEXTS = (
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

_GAY_CONTEXT_JOIN_CASES = (
    pytest.param("qsIt", "qsIt.en-y0.ex-y5", id="it"),
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
            f"{label}: expected {expected_target_glyph} after qsGay, " f"got {target_name} (full: {glyphs!r})"
        )

    entry_ys = _entry_ys(target_name)
    if not entry_ys:
        if glyphs[gay_index] == "qsGay.ex-y0.ex-ext-1":
            failures.append(
                f"{label}: qsGay kept a baseline extension before entryless "
                f"{target_name} (full: {glyphs!r})"
            )
        return
    if entry_ys and 0 not in entry_ys:
        failures.append(
            f"{label}: {target_base} ({target_name}) has an entry anchor but "
            f"not at baseline; entry_ys={entry_ys} (full: {glyphs!r})"
        )
        return
    if glyphs[gay_index] != "qsGay.ex-y0.ex-ext-1":
        failures.append(
            f"{label}: expected qsGay.ex-y0.ex-ext-1, " f"got {glyphs[gay_index]} (full: {glyphs!r})"
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


@pytest.mark.parametrize(
    ("target", "expect"),
    [
        pytest.param("qsTea", "·Gay.ex-ext-1 ~b~ ·Tea", id="tea"),
        pytest.param("qsIt", "·Gay.ex-ext-1 ~b~ ·It", id="it"),
        pytest.param("qsI", "·Gay.ex-ext-1 ~b~ ·I.∅", id="i"),
        pytest.param("qsExam", "·Gay.ex-ext-1 ~b~ ·Exam.∅", id="exam"),
    ],
)
def test_gay_extends_before_selected_targets(target: str, expect: str):
    _assert_expect_any(_qs_text("qsGay", target), [expect])


@pytest.mark.parametrize(
    ("target_base", "expected_target_glyph"),
    _GAY_CONTEXT_JOIN_CASES,
)
def test_gay_joining_targets_keep_extension_in_any_leading_context(
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
def test_gay_joining_targets_keep_extension_in_any_trailing_context(target_base: str):
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
def test_gay_joining_targets_share_shifted_baseline_anchor(target_base: str):
    glyphs = _shape_qs("qsGay", target_base)
    meta = _compiled_meta()
    gay = meta[glyphs[0]]
    target = meta[glyphs[1]]
    gay_exit_ys = {anchor[1] for anchor in gay.exit}
    target_entry_ys = {anchor[1] for anchor in (*target.entry, *target.entry_curs_only)}
    assert (
        gay_exit_ys & target_entry_ys
    ), f"qsGay.exit {gay.exit} and {target_base}.entry {target.entry} share no y-coordinate"
    assert gay.exit == ((6, 0),), (
        f"qsGay extended baseline exit should land at x=6, y=0 "
        f"(max_ink_x+1 of the extended bitmap), got {gay.exit}"
    )


@pytest.mark.parametrize("target_base", _GAY_BASELINE_EXTENDED_TARGETS)
def test_gay_exit_baseline_extended_targets_include_joining_followers(target_base):
    meta = _compiled_meta()
    variant = meta["qsGay.ex-y0.ex-ext-1"]
    assert variant.exit == ((6, 0),), f"qsGay.ex-y0.ex-ext-1 should exit at x=6, y=0; got {variant.exit}"
    assert target_base in set(variant.before), (
        f"qsGay.ex-y0.ex-ext-1 should name {target_base} in `before`; " f"got {variant.before}"
    )


def test_gay_exit_xheight_extended_exists_for_it():
    meta = _compiled_meta()
    variant = meta["qsGay.ex-y5.ex-ext-2"]
    assert variant.exit == ((6, 5),), f"qsGay.ex-y5.ex-ext-2 should exit at x=6, y=5; got {variant.exit}"
    assert "qsIt" in set(variant.before), (
        f"qsGay.ex-y5.ex-ext-2 should name qsIt in `before`; " f"got {variant.before}"
    )


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


@pytest.mark.parametrize("target_base", _GAY_NONJOINING_TARGETS)
def test_gay_nonjoining_targets_do_not_join_in_any_leading_context(target_base):
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
def test_gay_nonjoining_targets_do_not_join_in_any_trailing_context(target_base):
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
def test_gay_extended_variants_exclude_nonjoining_targets(target_base):
    meta = _compiled_meta()
    for name, variant in meta.items():
        if name.startswith("qsGay") and "ex-ext-1" in name:
            assert target_base not in set(
                variant.before
            ), f"{name}.before should not include {target_base}; got {variant.before}"


# --- Per-letter data-expect cases --------------------------------------------
#
# Each pair below gets a data-expect case on its own and one case for each letter placed before or after it.


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
    """Join tokens with the ` ? ` operator, or with `+?` between the two letters of a ligature pair.

    A token in a ligature pair loses its modifiers, because `data-expect` applies modifiers to the whole ligature group and drops them in the separated interpretation.
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
                _join_expect([(name, _expect_tok(name)), ("Tea", tea_nhalf), ("Tea", tea_nhalf)]),
            )
        )
    for name, code in LETTERS:
        out.append(
            (
                _case_id("Tea", "Tea", name),
                chr(TEA) + chr(TEA) + chr(code),
                _join_expect([("Tea", tea_nhalf), ("Tea", _expect_tok("Tea")), (name, _expect_tok(name))]),
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

    # ·He prefers the following letter at the baseline, so it joins a half ·Day whatever precedes it, and a baseline-exit predecessor loses its join into ·He (test_he_joins_at_most_one_baseline_direction). The predecessor's connection is therefore written as `?`.
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
            expect = f"{he_nhalf} ~b~ ·Day+?{name}.half"
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


def _baseline_join_at(glyphs: list[str], boundary_index: int) -> bool:
    """Return whether ``glyphs[boundary_index]`` joins the next glyph at the baseline (y=0)."""
    if boundary_index < 0 or boundary_index + 1 >= len(glyphs):
        return False
    return 0 in _pair_join_ys(glyphs, boundary_index)


def _collect_he_ye_single_baseline_direction_failures(center: str) -> list[str]:
    """Flag every ``pred + center + follower`` where ·He or ·Ye (``center``) breaks the one-baseline-direction rule. The predecessor and the follower each range over every letter and the word edge.

    ·He and ·Ye are drawn with a stroke that meets the baseline in one direction only, so each can join at the baseline on at most one side, and it prefers the following letter. The check reads the chosen glyphs' anchors at y=0:

    1. ``center`` never joins at the baseline on both sides.
    2. When the two-letter shapings ``pred+center`` and ``center+follower`` both join at the baseline, the three-letter shaping joins the follower and not the predecessor.

    x-height joins are not checked. ·He and ·Ye form no ligatures, so ``center`` is always one glyph between its two neighbors.
    """
    failures: list[str] = []
    letters: list[tuple[str, str]] = [(name, chr(code)) for name, code in LETTERS]
    surround: list[tuple[str | None, str | None]] = [(None, None), *letters]

    back_possible: dict[str | None, bool] = {None: False}
    for pred_name, pred_char in letters:
        pair = _shape_qs(pred_char, center)
        center_index = _find_base_index(pair, center)
        back_possible[pred_name] = center_index is not None and _baseline_join_at(pair, center_index - 1)

    fwd_possible: dict[str | None, bool] = {None: False}
    for follower_name, follower_char in letters:
        pair = _shape_qs(center, follower_char)
        center_index = _find_base_index(pair, center)
        fwd_possible[follower_name] = center_index is not None and _baseline_join_at(pair, center_index)

    center_label = center.removeprefix("qs")
    for pred_name, pred_char in surround:
        for follower_name, follower_char in surround:
            parts = [p for p in (pred_char, center, follower_char) if p is not None]
            glyphs = _shape_qs(*parts)
            center_index = _find_base_index(glyphs, center)
            if center_index is None:
                failures.append(
                    f"[{pred_name}] / {center_label} / [{follower_name}]: no {center} glyph in {glyphs}"
                )
                continue

            back = pred_char is not None and _baseline_join_at(glyphs, center_index - 1)
            fwd = follower_char is not None and _baseline_join_at(glyphs, center_index)
            label = f"[{pred_name or '∅'}] / {center_label} / [{follower_name or '∅'}]"

            if back and fwd:
                failures.append(
                    f"{label}: {center_label} double-backs — joins at the baseline on both sides in {glyphs}"
                )
            if back_possible[pred_name] and fwd_possible[follower_name] and not (fwd and not back):
                failures.append(
                    f"{label}: baseline join possible in both directions, so {center_label} should join the "
                    f"follower forward and drop the backward join, but got back={back} fwd={fwd} in {glyphs}"
                )

    return failures


def test_he_joins_at_most_one_baseline_direction():
    _assert_no_failures(_collect_he_ye_single_baseline_direction_failures("qsHe"))


def test_ye_joins_at_most_one_baseline_direction():
    _assert_no_failures(_collect_he_ye_single_baseline_direction_failures("qsYe"))


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


@pytest.mark.parametrize(
    "left_base",
    [pytest.param(name, id=_family_to_label(name).lower()) for name, _ in _plain_quikscript_letters()],
)
def test_nothing_joins_to_at_may(left_base: str):
    glyphs = _shape_qs(left_base, "qsAt", "qsMay")
    left_index = _find_base_index(glyphs, left_base)
    assert left_index is not None, glyphs
    assert _pair_join_ys(glyphs, left_index) == set(), f"{left_base} joined into ·At before ·May in {glyphs}"


def test_roe_stays_bare_before_at_may():
    _assert_expect_any(
        _qs_text("qsRoe", "qsAt", "qsMay"),
        ["·Roe.∅ | ·At.before-may ~b~ ·May"],
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
    """·Eat exits at the baseline, so ·Eat·Roe takes ·Roe's ``shortened_top`` shape (``en-ext-1-at-0``)."""
    _assert_expect_any(
        _qs_text("qsEat", "qsRoe"),
        ["·Eat ~b~ ·Roe.en-ext-1-at-0"],
    )


def test_eat_roe_stays_shortened_top_before_non_xheight_follower():
    """Before ·Ah, which does not enter at the x-height, ·Roe keeps its ``shortened_top`` shape, takes no x-height exit, and does not join ·Ah."""
    _assert_expect_any(
        _qs_text("qsEat", "qsRoe", "qsAh"),
        ["·Eat ~b~ ·Roe.en-ext-1-at-0 | ·Ah"],
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


def test_at_may_has_no_entry_anchor():
    meta_map = _compiled_meta()
    base_name = "qsAt.ex-y0.before-may"
    variants = {
        name: meta for name, meta in meta_map.items() if name == base_name or name.startswith(base_name + ".")
    }
    assert base_name in variants, f"{base_name} missing from compiled meta"
    for name, meta in sorted(variants.items()):
        assert meta.entry == (), f"{name} should have no entry anchor; got {meta.entry}"
    qs_may_targets = [
        target
        for target in variants[base_name].before
        if (target_meta := meta_map.get(target)) is not None and target_meta.base_name == "qsMay"
    ]
    assert (
        qs_may_targets
    ), f"{base_name}.before should resolve to at least one qsMay variant; got {variants[base_name].before}"


def test_roe_may_they_utter_looks_ok():
    _assert_expect_any(
        _qs_text("qsRoe", "qsMay", "qsThey", "qsUtter"),
        ["·Roe ~b~ ·May.ex-noentry |?| ·They+Utter.noentry"],
    )


def test_at_may_they_utter_looks_ok():
    _assert_expect_any(
        _qs_text("qsAt", "qsMay", "qsThey", "qsUtter"),
        ["·At.before-may.ex-ext-5 ~b~ ·May.ex-noentry |?| ·They+Utter.noentry"],
    )


# ---------------------------------------------------------------------------
# Senior joins in words that The Manual does not show.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ZWNJ isolation sweeps.
#
# Nothing on one side of a ZWNJ may change the glyphs chosen on the other side. The isolation check in ``test_shaping`` splits the HarfBuzz buffer at non-joins and does not use ZWNJ as its reference, because the font fires ``.noentry`` rules after a literal ``uni200C``. These sweeps instead shape a pair of letters on one side of a ZWNJ and check that changing the text on the other side leaves the pair's glyphs unchanged.
# ---------------------------------------------------------------------------


def _find_zwnj_indices(glyphs: list[str]) -> list[int]:
    """Return the indices of the ``space`` glyphs in ``glyphs``. HarfBuzz outputs both a ZWNJ and a literal space as the ``space`` glyph, and ``_context_chars()`` includes the space. The callers rely on the injected ZWNJ being the ``space`` glyph nearest the letter pair."""
    return [i for i, g in enumerate(glyphs) if g == "space"]


def _collect_left_context_changes_right_pair_across_zwnj_failures(
    *,
    max_chars_before: int = 1,
    before_first_only: str | None = None,
) -> list[str]:
    """Flag every pair of plain letters (L1, L2) whose glyphs after a ZWNJ change when a prefix is added before the ZWNJ.

    The reference is ``ZWNJ + L1 + L2`` with nothing before it. Each prefix of 0 to ``max_chars_before`` entries from ``_context_chars()`` is compared against it; the empty prefix compares the reference with itself. The compared glyphs are those after the rightmost ``space`` glyph, which is the injected ZWNJ. ``before_first_only`` works as in ``_collect_pair_must_not_join_regardless_of_what_comes_before_or_after``.
    """
    failures: list[str] = []
    context_set = _context_chars()
    letters = _plain_quikscript_letters()

    if before_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if before_first_only not in valid_names:
            raise ValueError(f"before_first_only={before_first_only!r} not in context set")

    before_combos = _surround_combos(context_set, max_chars_before, first_only=before_first_only)

    for l1_name, l1_char in letters:
        for l2_name, l2_char in letters:
            baseline_text = ZWNJ + l1_char + l2_char
            baseline_glyphs = _shape(baseline_text)
            baseline_zwnj_indices = _find_zwnj_indices(baseline_glyphs)
            if not baseline_zwnj_indices:
                failures.append(
                    f"baseline ZWNJ / {l1_name} / {l2_name}: "
                    f"expected the injected ZWNJ to survive baseline shaping, got {baseline_glyphs}"
                )
                continue
            baseline_right = tuple(baseline_glyphs[baseline_zwnj_indices[-1] + 1 :])

            for before in before_combos:
                before_label = "·".join(name for name, _ in before) if before else "∅"
                before_text = "".join(char for _, char in before)
                text = before_text + ZWNJ + l1_char + l2_char
                glyphs = _shape(text)
                zwnj_indices = _find_zwnj_indices(glyphs)
                if not zwnj_indices:
                    failures.append(
                        f"[{before_label}] / ZWNJ / {l1_name} / {l2_name}: "
                        f"expected the injected ZWNJ to survive shaping, got {glyphs}"
                    )
                    continue
                test_right = tuple(glyphs[zwnj_indices[-1] + 1 :])
                if test_right != baseline_right:
                    failures.append(
                        f"[{before_label}] / ZWNJ / {l1_name} / {l2_name}: "
                        f"right side shaped as {list(test_right)} after prefix, "
                        f"but as {list(baseline_right)} with no prefix (full: {glyphs})"
                    )

    return failures


def _collect_right_context_changes_left_pair_across_zwnj_failures(
    *,
    max_chars_after: int = 1,
    after_first_only: str | None = None,
) -> list[str]:
    """Flag every pair of plain letters (L1, L2) whose glyphs before a ZWNJ change when a suffix is added after the ZWNJ. This is the mirror image of ``_collect_left_context_changes_right_pair_across_zwnj_failures``.

    The reference is ``L1 + L2 + ZWNJ``, and the compared glyphs are those before the leftmost ``space`` glyph, which is the injected ZWNJ. ``after_first_only`` filters the suffixes as ``before_first_only`` filters prefixes.
    """
    failures: list[str] = []
    context_set = _context_chars()
    letters = _plain_quikscript_letters()

    if after_first_only is not None:
        valid_names = {name for name, _ in context_set}
        if after_first_only not in valid_names:
            raise ValueError(f"after_first_only={after_first_only!r} not in context set")

    after_combos = _surround_combos(context_set, max_chars_after, first_only=after_first_only)

    for l1_name, l1_char in letters:
        for l2_name, l2_char in letters:
            baseline_text = l1_char + l2_char + ZWNJ
            baseline_glyphs = _shape(baseline_text)
            baseline_zwnj_indices = _find_zwnj_indices(baseline_glyphs)
            if not baseline_zwnj_indices:
                failures.append(
                    f"{l1_name} / {l2_name} / ZWNJ baseline: "
                    f"expected the injected ZWNJ to survive baseline shaping, got {baseline_glyphs}"
                )
                continue
            baseline_left = tuple(baseline_glyphs[: baseline_zwnj_indices[0]])

            for after in after_combos:
                after_label = "·".join(name for name, _ in after) if after else "∅"
                after_text = "".join(char for _, char in after)
                text = l1_char + l2_char + ZWNJ + after_text
                glyphs = _shape(text)
                zwnj_indices = _find_zwnj_indices(glyphs)
                if not zwnj_indices:
                    failures.append(
                        f"{l1_name} / {l2_name} / ZWNJ / [{after_label}]: "
                        f"expected the injected ZWNJ to survive shaping, got {glyphs}"
                    )
                    continue
                test_left = tuple(glyphs[: zwnj_indices[0]])
                if test_left != baseline_left:
                    failures.append(
                        f"{l1_name} / {l2_name} / ZWNJ / [{after_label}]: "
                        f"left side shaped as {list(test_left)} before suffix, "
                        f"but as {list(baseline_left)} with no suffix (full: {glyphs})"
                    )

    return failures


@pytest.mark.parametrize("before_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_right_pair_after_zwnj_is_unaffected_by_left_context(before_first: str):
    _assert_no_failures(
        _collect_left_context_changes_right_pair_across_zwnj_failures(
            max_chars_before=1,
            before_first_only=before_first,
        ),
        limit=None,
    )


@pytest.mark.parametrize("after_first", _PAIR_SWEEP_BEFORE_FIRSTS)
def test_left_pair_before_zwnj_is_unaffected_by_right_context(after_first: str):
    _assert_no_failures(
        _collect_right_context_changes_left_pair_across_zwnj_failures(
            max_chars_after=1,
            after_first_only=after_first,
        ),
        limit=None,
    )


# The early `calt_trailing_demote` lookups (`_emit_trailing_demote_lookups("calt_trailing_demote")` in tools/quikscript_fea.py) are required alongside the final `calt_final_trailing_demote` ones. In `<word-boundary> ·Excite ·No ·X`, the word-initial ·Excite has no exit, so ·No takes its `qsNo.alt.en-y0.ex-y0` backward upgrade and ·X takes a baseline entry to join it. ·No then reverts to bare `qsNo`, which exits only at the x-height, and leaves ·X's entry stranded. The early trailing demote reverts ·X to bare in an earlier round. The final trailing demote runs before ·No's last demote (`calt_final2_pred_demote_qsNo`), so it cannot catch this case. The depth-4 isolation-leak gate cannot reach this 5-glyph context, so this test is the only check on these three cases.
@pytest.mark.parametrize(
    "prefix, trailing",
    [
        (("qsAh", "space"), "qsExcite"),
        (("qsAh", "space"), "qsGay"),
        (("qsThey", "qsZoo"), "qsDay"),
    ],
)
def test_excite_no_trailing_stays_bare_across_word_boundary(prefix, trailing):
    glyphs = _shape_qs(*prefix, "qsExcite", "qsNo", trailing)
    assert glyphs[-1] == trailing, (
        f"trailing ·{trailing[2:]} after a word-initial ·Excite·No must demote to bare "
        f"{trailing} (early calt_trailing_demote), got a dangling backward entry: {glyphs}"
    )
