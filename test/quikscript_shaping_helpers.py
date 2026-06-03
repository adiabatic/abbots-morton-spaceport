import sys
from functools import cache, lru_cache
from pathlib import Path

import uharfbuzz as hb
import yaml
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior-Regular.otf"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"
ZWNJ = "\u200c"

TOOLS_PATH = str(ROOT / "tools")
if TOOLS_PATH not in sys.path:
    sys.path.insert(0, TOOLS_PATH)

from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import JoinGlyph


@cache
def _font() -> hb.Font:
    blob = hb.Blob.from_file_path(str(FONT_PATH))
    face = hb.Face(blob)
    return hb.Font(face)


@cache
def _tt_font() -> TTFont:
    # HarfBuzz's `glyph_to_string` truncates names to 63 bytes, so we resolve GIDs to full glyph names through fontTools instead.
    return TTFont(str(FONT_PATH))


def _gid_to_full_name(gid: int) -> str:
    return _tt_font().getGlyphName(gid)


# pytest-xdist runs each worker in its own subprocess, so there's no cross-thread reuse risk.
# Invariant for callers of `_BUF`: materialize `buf.glyph_infos` / `buf.glyph_positions` into a list (comprehension or `list(...)`) before the function returns. Never return the property itself or a generator over it — the next `_shape()` call will `clear_contents()` and overwrite the buffer, invalidating any unmaterialized view.
_BUF: hb.Buffer = hb.Buffer()


@lru_cache(maxsize=None)
def _shape(text: str) -> list[str]:
    font = _font()
    buf = _BUF
    buf.clear_contents()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [_gid_to_full_name(info.codepoint) for info in buf.glyph_infos]


@lru_cache(maxsize=None)
def _shape_with_clusters(text: str) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Shape ``text`` and return ``(glyph_names, clusters)`` in parallel. Each glyph's cluster is the index of the earliest input codepoint it covers, so ligatures report the cluster of their first component. Clusters are monotonic non-decreasing, which is what lets a caller locate the output glyphs belonging to a known input codepoint range even when a neighbor ligates across the boundary."""
    font = _font()
    buf = _BUF
    buf.clear_contents()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    infos = buf.glyph_infos
    names = tuple(_gid_to_full_name(info.codepoint) for info in infos)
    clusters = tuple(info.cluster for info in infos)
    return names, clusters


@lru_cache(maxsize=None)
def _shape_with_features(
    text: str,
    feature_items: tuple[tuple[str, bool], ...],
) -> list[str]:
    font = _font()
    buf = _BUF
    buf.clear_contents()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, dict(feature_items))
    return [_gid_to_full_name(info.codepoint) for info in buf.glyph_infos]


@cache
def _compiled_meta() -> dict[str, JoinGlyph]:
    data = load_glyph_data(ROOT / "glyph_data")
    return compile_glyph_set(data, "senior").glyph_meta


@cache
def _char_map() -> dict[str, str]:
    with PS_NAMES_PATH.open() as f:
        ps_names = yaml.safe_load(f)
    return {name: chr(codepoint) for name, codepoint in ps_names.items()}


@cache
def _plain_quikscript_letters() -> tuple[tuple[str, str], ...]:
    chars = _char_map()
    names = [
        name
        for name in sorted(chars)
        if name.startswith("qs")
        and "_" not in name
        and "." not in name
        and name not in {"qsAngleParenLeft", "qsAngleParenRight"}
    ]
    return tuple((name, chars[name]) for name in names)


@cache
def _context_chars() -> tuple[tuple[str, str], ...]:
    """Plain Quikscript letters plus ZWNJ, for context-saturated sweeps."""
    return _plain_quikscript_letters() + (("ZWNJ", ZWNJ),)


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


def _declared_exit_ys(glyph_name: str) -> set[int]:
    """The exit heights a glyph's compiled ``modifiers`` explicitly promise via an ``ex-yN`` modifier, read independently of the glyph's actual exit anchors. Matches ``ex-yN`` exactly, so the extension/contraction siblings ``ex-ext-N`` / ``ex-con-N`` (and ``ex-dips``) are never mistaken for a connector-height declaration. Keying on this declared identity — rather than re-deriving "is this glyph connecting?" from the silhouette — is what generalizes across families: a shape rule like "the ink reaches farthest right at the exit row" misfires on every letter that exits from the left or middle (·He, ·Ye, ·Gay, ·They, …), whereas the modifier reads the same everywhere and survives the exit anchor being stripped."""
    meta = _compiled_meta().get(glyph_name)
    if meta is None:
        return set()
    declared: set[int] = set()
    for modifier in meta.modifiers:
        height = modifier[len("ex-y") :]
        if modifier.startswith("ex-y") and height.isdigit():
            declared.add(int(height))
    return declared


def _declares_xheight_exit(glyph_name: str) -> bool:
    """True when the glyph's compiled form identity declares an x-height (glyph-space y=5) forward exit, i.e. it carries the ``ex-y5`` modifier — the connecting body a letter shows when it means to hand its stroke off to a follower's x-height entry. See ``_declared_exit_ys`` for why this declared-identity test is preferred over reading the bitmap, and ``test_declared_exit_height_matches_exit_anchor`` for the invariant that keeps the modifier honest about the real anchor."""
    return 5 in _declared_exit_ys(glyph_name)


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


@cache
def _senior_shaping_env() -> tuple[dict, dict, dict]:
    from test_shaping import build_anchor_map

    anchor_map, potentials = build_anchor_map("senior")
    return {"senior": _font()}, {"senior": anchor_map}, {"senior": potentials}


def _assert_expect_any(text: str, expects: list[str]) -> None:
    """Pass if any of ``expects`` matches the senior shaping of ``text``.

    Each entry in ``expects`` is a ``data-expect`` string (see ``test/data-expect.md``). Tries them in order and returns on the first match; if none match, raises ``AssertionError`` listing every attempt.
    """
    from test_shaping import Run, run_shaping_test_runs

    fonts, anchor_maps, potentials = _senior_shaping_env()
    runs: list[Run] = [{"font": "senior", "text": text}]
    errors: list[str] = []
    for expect in expects:
        try:
            run_shaping_test_runs(
                fonts,
                anchor_maps,
                runs,
                expect,
                base_potential_entries=potentials,
            )
            return
        except AssertionError as exc:
            errors.append(f"  {expect!r}: {exc}")
    joined = "\n".join(errors)
    raise AssertionError(f"No candidate data-expect matched shaping of {text!r}:\n{joined}")
