"""The old engine's `data-expect` harness: shapes the old build's Senior and Junior fonts under `site/` and checks a run against the compiled glyph metadata of `glyph_data/quikscript.yaml` (`build_anchor_map`, `_try_interpretation`, `run_shaping_test_runs`).

The pin suite does not use it: the root conftest checks the `data-expect` pins against the rebuilt fonts through `rebuild/validation/check.py`, and `rebuild/validation/expect.py` and `rebuild/validation/corpus.py` hold the grammar and the collector this module imports. What still imports it reads the old build's glyph names or anchors: the tests in test/test_calt_regressions.py that do, test/quikscript_shaping_helpers.py, and tools/manual_letters_prefill.py.
"""

import sys
from pathlib import Path
from typing import Any

import uharfbuzz as hb
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONT_PATHS = {
    "senior": ROOT / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
    "junior": ROOT / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf",
}
GLYPH_DATA_DIR = ROOT / "glyph_data"

for _path in (str(ROOT), str(ROOT / "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import Anchor, JoinGlyph

from rebuild.validation.corpus import partition_by_runs as _partition_by_runs
from rebuild.validation.expect import (
    Connection,
    ExpectToken,
    PartitionedRun,
    Run,
    parse_expect,
)
from rebuild.validation.expect import expand_maybe_ligatures as _expand_maybe_ligatures
from rebuild.validation.expect import token_char_spans as _token_char_spans

__all__ = ["Connection", "ExpectToken", "PartitionedRun", "Run", "parse_expect"]

AnchorMap = dict[str, dict[str, list[Anchor]]]

_COMPILED_GLYPH_META: dict[str, dict[str, JoinGlyph]] = {}
_ANCHOR_MAPS: dict[str, tuple[AnchorMap, dict[str, set[int]]]] = {}
_TT_FONTS: dict[str, TTFont] = {}
_TT_GLYPH_SETS: dict[str, Any] = {}
_OUTLINE_SIG_CACHE: dict[tuple[str, str], tuple] = {}


# ---------------------------------------------------------------------------
# Font/anchor loading
# ---------------------------------------------------------------------------


def load_font(variant: str = "senior") -> hb.Font:
    blob = hb.Blob.from_file_path(str(FONT_PATHS[variant]))
    face = hb.Face(blob)
    return hb.Font(face)


def _tt_font(variant: str) -> TTFont:
    if variant not in _TT_FONTS:
        _TT_FONTS[variant] = TTFont(str(FONT_PATHS[variant]))
    return _TT_FONTS[variant]


def shaped_glyph_name(variant: str, gid: int) -> str:
    """Return the full compiled glyph name for a GID, bypassing HarfBuzz's 63-byte truncation in `font.glyph_to_string`."""
    return _tt_font(variant).getGlyphName(gid)


def build_anchor_map(variant: str = "senior") -> tuple[AnchorMap, dict[str, set[int]]]:
    if variant in _ANCHOR_MAPS:
        return _ANCHOR_MAPS[variant]
    data = load_glyph_data(GLYPH_DATA_DIR)
    compiled = compile_glyph_set(data, variant)
    _COMPILED_GLYPH_META[variant] = {**compiled.glyph_meta, **compiled.ss10_copies}

    result: AnchorMap = {}
    base_potential_entries: dict[str, set[int]] = {}
    for name, meta in _COMPILED_GLYPH_META[variant].items():
        entry = list(meta.entry) + list(meta.entry_curs_only)
        exit_ = list(meta.exit)
        if entry or exit_:
            result[name] = {"entry": entry, "exit": exit_}
        if entry:
            for _, y in entry:
                base_potential_entries.setdefault(meta.base_name, set()).add(y)
    _ANCHOR_MAPS[variant] = (result, base_potential_entries)
    return _ANCHOR_MAPS[variant]


def _compiled_glyph_meta(name: str, variant: str = "senior") -> JoinGlyph:
    if variant not in _COMPILED_GLYPH_META:
        build_anchor_map(variant)
    meta = _COMPILED_GLYPH_META[variant].get(name)
    if meta is None:
        raise AssertionError(f"Missing compiled glyph metadata for {name!r} in {variant}")
    return meta


def _modifier_matches(meta: JoinGlyph, modifier: str) -> bool:
    if modifier in {"alt", "half"}:
        return modifier in meta.traits
    return modifier in meta.compat_assertions


def _modifier_not_matches(meta: JoinGlyph, modifier: str) -> bool:
    if modifier in {"alt", "half"}:
        return modifier not in meta.traits
    return modifier not in meta.compat_assertions


def _glyph_base_name(meta: JoinGlyph) -> str:
    return meta.base_name


def _is_ligature_match(meta: JoinGlyph, base: str, lig: str) -> bool:
    return meta.sequence == (base, lig)


def _expected_exact_glyph_name(tok: ExpectToken) -> str:
    if tok["lig_base"]:
        return f"{tok['base']}_{tok['lig_base']}"
    return tok["base"]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def _try_interpretation(
    font: hb.Font,
    anchor_map: AnchorMap,
    glyph_names: list[str],
    tokens: list[ExpectToken],
    connections: list[Connection],
    base_potential_entries: dict[str, set[int]] | None = None,
    variant: str = "senior",
) -> str | None:
    if len(glyph_names) != len(tokens):
        return f"Glyph count mismatch: got {glyph_names}, " f"expected {len(tokens)} tokens"

    for i, (gname, tok) in enumerate(zip(glyph_names, tokens)):
        meta = _compiled_glyph_meta(gname, variant)
        base = tok["base"]
        lig = tok["lig_base"]

        if lig:
            if not _is_ligature_match(meta, base, lig):
                return f"Glyph {i}: expected ligature {base}+{lig}, got {gname!r}"
        else:
            if _glyph_base_name(meta) != base:
                return f"Glyph {i}: expected base {base}, got {gname!r}"

        if tok["exact_glyph"]:
            expected_glyph_name = _expected_exact_glyph_name(tok)
            if gname != expected_glyph_name:
                return f"Glyph {i}: expected exact glyph {expected_glyph_name!r}, got {gname!r}"

        for v in tok["variants"]:
            if not _modifier_matches(meta, v):
                return f"Glyph {i}: expected variant '{v}' in {gname!r}"

        for v in tok.get("neg_variants", []):
            if not _modifier_not_matches(meta, v):
                return f"Glyph {i}: variant '{v}' must NOT appear in {gname!r}"

    for i, conn in enumerate(connections):
        left = glyph_names[i]
        right = glyph_names[i + 1]
        left_meta = _compiled_glyph_meta(left, variant)
        right_meta = _compiled_glyph_meta(right, variant)
        left_anchors = anchor_map.get(left, {})
        right_anchors = anchor_map.get(right, {})
        left_exits = {a[1] for a in left_anchors.get("exit", [])}
        right_entries = {a[1] for a in right_anchors.get("entry", [])}
        common_ys = left_exits & right_entries

        if conn["kind"] == "maybe":
            continue
        if conn["kind"] in ("break", "break_no_isolation"):
            if common_ys:
                return (
                    f"Connection {i}: expected break between {left} and {right}, "
                    f"but found common Y values {common_ys}"
                )
            if conn["kind"] == "break_no_isolation":
                continue
            if base_potential_entries and left_exits and "half" in left_meta.traits:
                left_base = left_meta.base_name
                base_key = left_base if left_base in anchor_map else f"{left_base}.prop"
                base_exits = {a[1] for a in anchor_map.get(base_key, {}).get("exit", [])}
                extra_exits = left_exits - base_exits
                if extra_exits:
                    right_base = right_meta.base_name
                    potential = base_potential_entries.get(right_base, set())
                    suspect_ys = extra_exits & potential
                    if suspect_ys:
                        return (
                            f"Connection {i}: break between {left} and {right}, "
                            f"but {left} exits at Y={suspect_ys} (not on base stance) "
                            f"matching potential entries for {right_base} — forward "
                            f"calt may have selected the half stance across a break"
                        )
        elif conn["kind"] == "join":
            if not common_ys:
                return (
                    f"Connection {i}: expected join between {left} and {right}, "
                    f"but no common Y values (exits={left_exits}, entries={right_entries})"
                )
        elif conn["kind"] == "height":
            expected_y = conn["y"]
            if expected_y not in common_ys:
                return (
                    f"Connection {i}: expected join at y={expected_y} between "
                    f"{left} and {right}, common Ys={common_ys}"
                )

    return None


def run_shaping_test(
    font: hb.Font,
    anchor_map: AnchorMap,
    text: str,
    expect_str: str,
    base_potential_entries: dict[str, set[int]] | None = None,
    features: dict[str, bool] | None = None,
    variant: str = "senior",
) -> None:
    tokens, connections = parse_expect(expect_str)

    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    if features:
        hb.shape(font, buf, features)
    else:
        hb.shape(font, buf)

    infos = buf.glyph_infos
    glyph_names = [shaped_glyph_name(variant, info.codepoint) for info in infos]

    interpretations = _expand_maybe_ligatures(tokens, connections)

    if len(interpretations) == 1:
        error = _try_interpretation(
            font,
            anchor_map,
            glyph_names,
            interpretations[0][0],
            interpretations[0][1],
            base_potential_entries,
            variant=variant,
        )
        if error:
            raise AssertionError(error)
        return

    errors = []
    for interp_tokens, interp_connections in interpretations:
        error = _try_interpretation(
            font,
            anchor_map,
            glyph_names,
            interp_tokens,
            interp_connections,
            base_potential_entries,
            variant=variant,
        )
        if error is None:
            return
        errors.append(error)

    raise AssertionError(
        f"No interpretation matched for {expect_str!r} "
        f"(shaped: {glyph_names}).\n"
        + "\n".join(f"  Interpretation {i+1}: {e}" for i, e in enumerate(errors))
    )


# ---------------------------------------------------------------------------
# Multi-run test runner
# ---------------------------------------------------------------------------


def _shape_text_glyph_names(
    font: hb.Font, text: str, features: dict[str, bool] | None, variant: str = "senior"
) -> tuple[list[str], list]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    if features:
        hb.shape(font, buf, features)
    else:
        hb.shape(font, buf)
    names = [shaped_glyph_name(variant, info.codepoint) for info in buf.glyph_infos]
    positions = list(buf.glyph_positions)
    return names, positions


def _isolation_glyphs_split(
    font: hb.Font,
    text: str,
    segment_breaks: list[int],
    features: dict[str, bool] | None,
    variant: str = "senior",
) -> tuple[list[str], list]:
    """Shape each segment of `text` in its own HarfBuzz buffer and return the concatenated glyph names and position records.

    `segment_breaks` are character offsets into `text`. Because each segment has its own buffer, no contextual lookup can match across a break.
    """
    bounds = [0, *segment_breaks, len(text)]
    names: list[str] = []
    positions: list = []
    for start, end in zip(bounds, bounds[1:]):
        if start >= end:
            continue
        seg_names, seg_positions = _shape_text_glyph_names(
            font,
            text[start:end],
            features,
            variant,
        )
        names.extend(seg_names)
        positions.extend(seg_positions)
    return names, positions


def _get_tt_glyph_set(variant: str) -> Any:
    glyph_set = _TT_GLYPH_SETS.get(variant)
    if glyph_set is None:
        tt = TTFont(str(FONT_PATHS[variant]))
        _TT_FONTS[variant] = tt
        glyph_set = tt.getGlyphSet()
        _TT_GLYPH_SETS[variant] = glyph_set
    return glyph_set


def _glyph_outline_signature(variant: str, glyph_name: str) -> tuple:
    key = (variant, glyph_name)
    sig = _OUTLINE_SIG_CACHE.get(key)
    if sig is None:
        glyph_set = _get_tt_glyph_set(variant)
        pen = RecordingPen()
        glyph_set[glyph_name].draw(pen)
        sig = tuple(pen.value)
        _OUTLINE_SIG_CACHE[key] = sig
    return sig


def _positions_equivalent(pos_a, pos_b) -> bool:
    return (
        pos_a.x_offset == pos_b.x_offset
        and pos_a.y_offset == pos_b.y_offset
        and pos_a.x_advance == pos_b.x_advance
        and pos_a.y_advance == pos_b.y_advance
    )


def _check_break_isolation(
    text: str,
    tokens: list[ExpectToken],
    connections: list[Connection],
    anchor_map: AnchorMap,
    glyph_names_full: list[str],
    glyph_positions_full: list,
    font: hb.Font,
    features: dict[str, bool] | None,
    variant: str,
) -> str | None:
    """Return an error message if a letter beside a non-join shapes differently when the text is split at the non-join, and None otherwise.

    A non-join is a "break" connection between two Quikscript letter tokens, or a "maybe" connection between two whose shaped glyphs have no Y where the left one exits and the right one enters. The text is reshaped with a buffer boundary at every non-join. Each glyph beside a non-join must then have the same outline and position record as in the full shaping, though its glyph name may differ. A difference means a contextual lookup reaches across the non-join. This check catches such leaks, so corpus authors do not need `.!half` and `.!alt` assertions for them.

    Inserting a ZWNJ is not an equivalent reference. The font's calt lookups apply `.noentry` rules after a literal `uni200C` (`_ensure_zwnj_coverage_for_calt_lookups` in tools/quikscript_fea.py), so a ZWNJ changes shapes that splitting the buffer does not.
    """

    def _is_qs_letter(tok: ExpectToken) -> bool:
        # Tokens that are not Quikscript letters (◊space, ◊ZWNJ, `\-`) are skipped. The font may change a letter's shape beside them, as with the `.noentry` forms after a ZWNJ, so checking them would report false failures.
        return tok["base"].startswith("qs")

    isolating_indices: list[int] = []
    for i, conn in enumerate(connections):
        if not (_is_qs_letter(tokens[i]) and _is_qs_letter(tokens[i + 1])):
            continue
        kind = conn["kind"]
        if kind == "break":
            isolating_indices.append(i)
        elif kind == "maybe":
            left = glyph_names_full[i]
            right = glyph_names_full[i + 1]
            left_exits = {a[1] for a in anchor_map.get(left, {}).get("exit", [])}
            right_entries = {a[1] for a in anchor_map.get(right, {}).get("entry", [])}
            if not (left_exits & right_entries):
                isolating_indices.append(i)
    if not isolating_indices:
        return None

    spans = _token_char_spans(text, tokens)
    segment_breaks = sorted({spans[i + 1][0] for i in isolating_indices})

    split_names, split_positions = _isolation_glyphs_split(
        font,
        text,
        segment_breaks,
        features,
        variant,
    )

    expected_len = len(tokens)
    if len(split_names) != expected_len:
        return (
            "Isolation check produced unexpected glyph count "
            f"(full={len(glyph_names_full)}, split={len(split_names)}, "
            f"expected={expected_len}). A contextual lookup probably ate or "
            "duplicated glyphs across a split boundary."
        )

    for break_idx, ci in enumerate(isolating_indices, start=1):
        for side, ti in (("left", ci), ("right", ci + 1)):
            f = glyph_names_full[ti]
            s = split_names[ti]
            if f == s:
                continue
            f_sig = _glyph_outline_signature(variant, f)
            s_sig = _glyph_outline_signature(variant, s)
            outline_same = f_sig == s_sig
            position_same = _positions_equivalent(
                glyph_positions_full[ti],
                split_positions[ti],
            )
            if outline_same and position_same:
                continue
            diff_parts = []
            if not outline_same:
                diff_parts.append("outline")
            if not position_same:
                diff_parts.append("position")
            diff_summary = " + ".join(diff_parts)
            f_pos = glyph_positions_full[ti]
            s_pos = split_positions[ti]
            pos_fmt = (
                f"x_offset={f_pos.x_offset}/{s_pos.x_offset}, "
                f"y_offset={f_pos.y_offset}/{s_pos.y_offset}, "
                f"x_advance={f_pos.x_advance}/{s_pos.x_advance}, "
                f"y_advance={f_pos.y_advance}/{s_pos.y_advance}"
            )
            return (
                f"Break #{break_idx} (between tokens {ci} and {ci + 1}): "
                f"{side} glyph visual differs in isolation ({diff_summary}) —\n"
                f"  full:  {f}\n"
                f"  split: {s}\n"
                f"  positions (full/split): {pos_fmt}\n"
                "Non-joining pair must render the same in isolation. A "
                "contextual lookup is leaking across the break."
            )
    return None


def run_shaping_test_runs(
    fonts: dict[str, hb.Font],
    anchor_maps: dict[str, AnchorMap],
    runs: list[Run],
    expect_str: str,
    base_potential_entries: dict[str, dict[str, set[int]]] | None = None,
    features: dict[str, bool] | None = None,
) -> None:
    """Shape each run with its own font and check it against the tokens and connections of `expect_str` that fall in it, raising AssertionError on the first failure.

    `fonts`, `anchor_maps`, and `base_potential_entries` are keyed by variant ("senior" or "junior"). A run's own `features` are merged over `features`.
    """
    tokens, connections = parse_expect(expect_str)
    slices = _partition_by_runs(runs, tokens, connections)

    potential = base_potential_entries or {}

    for sl in slices:
        variant = sl["font"]
        text = sl["text"]
        if not text:
            continue
        font = fonts[variant]
        anchor_map = anchor_maps[variant]
        sub_tokens = sl["tokens"]
        sub_conns = sl["connections"]

        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        run_features = sl.get("features")
        if run_features and features:
            merged = {**features, **run_features}
        elif run_features:
            merged = run_features
        else:
            merged = features
        if merged:
            hb.shape(font, buf, merged)
        else:
            hb.shape(font, buf)

        infos = buf.glyph_infos
        glyph_names = [shaped_glyph_name(variant, info.codepoint) for info in infos]
        glyph_positions = list(buf.glyph_positions)

        interpretations = _expand_maybe_ligatures(sub_tokens, sub_conns)

        # Junior has no cursive attachment, so every connection becomes "maybe" and only glyph identity is checked.
        if variant == "junior":
            interpretations = [(t, [Connection(kind="maybe", y=None) for _ in c]) for t, c in interpretations]

        errors = []
        matched_interp: tuple[list[ExpectToken], list[Connection]] | None = None
        for interp_tokens, interp_connections in interpretations:
            error = _try_interpretation(
                font,
                anchor_map,
                glyph_names,
                interp_tokens,
                interp_connections,
                potential.get(variant),
                variant=variant,
            )
            if error is None:
                matched_interp = (interp_tokens, interp_connections)
                break
            errors.append(error)
        if matched_interp is None:
            raise AssertionError(
                f"[{variant}] No interpretation matched for run {text!r} "
                f"in {expect_str!r} (shaped: {glyph_names}).\n"
                + "\n".join(f"  Interpretation {i+1}: {e}" for i, e in enumerate(errors))
            )

        # Only Senior runs get the isolation check. Junior has no cursive attachment, so its anchors do not say which pairs join, and the check could not pick out the non-joins.
        if variant == "senior":
            interp_tokens, interp_connections = matched_interp
            isolation_error = _check_break_isolation(
                text,
                interp_tokens,
                interp_connections,
                anchor_map,
                glyph_names,
                glyph_positions,
                font,
                merged,
                variant,
            )
            if isolation_error:
                raise AssertionError(
                    f"[{variant}] Isolation check failed for run {text!r} "
                    f"in {expect_str!r} (shaped: {glyph_names}).\n{isolation_error}"
                )
