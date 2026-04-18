"""HarfBuzz shaping test helpers for the Senior Sans font.

Parses data-expect attributes from test/index.html and verifies that
HarfBuzz produces the expected glyph sequence and cursive connections.
"""

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import uharfbuzz as hb
import yaml

ROOT = Path(__file__).resolve().parent.parent
FONT_PATHS = {
    "senior": ROOT / "test" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
    "junior": ROOT / "test" / "AbbotsMortonSpaceportSansJunior-Regular.otf",
}
FONT_PATH = FONT_PATHS["senior"]  # legacy alias
GLYPH_DATA_DIR = ROOT / "glyph_data"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"

sys.path.insert(0, str(ROOT / "tools"))
from build_font import load_glyph_data
from glyph_compiler import compile_glyph_set
from quikscript_ir import Anchor, JoinGlyph


class ExpectToken(TypedDict):
    base: str
    lig_base: str | None
    lig_mode: Literal["maybe", "maybe_break"] | None
    variants: list[str]
    neg_variants: list[str]


class Connection(TypedDict):
    kind: Literal["join", "break", "height", "maybe"]
    y: int | None


class Run(TypedDict):
    font: str
    text: str
    features: NotRequired[dict[str, bool]]


class PartitionedRun(Run):
    tokens: list[ExpectToken]
    connections: list[Connection]


AnchorMap = dict[str, dict[str, list[Anchor]]]


class _CellInfo(TypedDict):
    expect: str | None
    line: int
    stylistic_set: str | None

def _build_char_to_glyph_name() -> dict[str, str]:
    with open(PS_NAMES_PATH) as f:
        ps_names = yaml.safe_load(f)
    result: dict[str, str] = {}
    for name, codepoint in ps_names.items():
        result[chr(codepoint)] = name
    return result

_CHAR_TO_GLYPH = _build_char_to_glyph_name()
_COMPILED_GLYPH_META: dict[str, dict[str, JoinGlyph]] = {}


# ---------------------------------------------------------------------------
# data-expect parser
# ---------------------------------------------------------------------------

TOKEN_RE = re.compile(
    r"""
    ·(-ing|J['\u2019]?ai|[A-Z][a-z]*)  # letter name (·Bay, ·-ing, ·J'ai)
    (?:\+([?|]?)([A-Z][a-z]*))?         # optional ligature partner (+Utter, +?Utter, +|Utter)
    ((?:\.!?[a-z][-a-z0-9]*)*)          # optional variant assertions (.half.extended, .!exit, .entry-extended, .exit.y1)
    """,
    re.VERBOSE,
)

ESCAPE_RE = re.compile(r"\\(.)")

LOZENGE_RE = re.compile(r"◊([A-Za-z]+)")
LOZENGE_MAP = {
    "space": "space",
    "ZWNJ": "space",
}

CONN_RE = re.compile(r"\s*~([xbt6])~\s*|\s*(\?)\s*|\s*\|\s*")


def _letter_to_qs(name: str) -> str:
    if name == "-ing":
        return "qsIng"
    name = name.replace("\u2019", "").replace("'", "")
    return "qs" + name


def parse_expect(raw: str) -> tuple[list[ExpectToken], list[Connection]]:
    """Parse a data-expect string into (tokens, connections).

    tokens:  list of dicts with keys:
        base      – e.g. "qsBay"
        lig_base  – e.g. "qsUtter" if ligature, else None
        lig_mode  – None (must-ligate), "maybe" (+?), or "maybe_break" (+|)
        variants  – list of variant assertion strings, e.g. ["half"]
    connections: list of dicts (len = len(tokens) - 1) with keys:
        kind      – "join", "break", "height", or "maybe"
        y         – int or None (only for "height")
    """
    HEIGHT_MAP = {"x": 5, "b": 0, "t": 8, "6": 6}

    raw = raw.strip()
    tokens = []
    connections = []
    pos = 0
    first = True

    while pos < len(raw):
        remaining = raw[pos:]

        if first:
            remaining = remaining.lstrip()
            first = False
        else:
            conn_m = CONN_RE.match(remaining)
            if conn_m is None:
                ws = re.match(r"\s+", remaining)
                if ws:
                    connections.append({"kind": "join", "y": None})
                    pos += ws.end()
                    remaining = raw[pos:]
                else:
                    raise ValueError(
                        f"Expected connection operator at pos {pos}: {remaining!r}"
                    )
            else:
                if conn_m.group(1):
                    h = conn_m.group(1)
                    connections.append({"kind": "height", "y": HEIGHT_MAP[h]})
                elif conn_m.group(2):
                    connections.append({"kind": "maybe", "y": None})
                else:
                    connections.append({"kind": "break", "y": None})
                pos += conn_m.end()
                remaining = raw[pos:]

        remaining = remaining.lstrip()
        pos = len(raw) - len(remaining)

        esc_m = ESCAPE_RE.match(remaining)
        if esc_m:
            char = esc_m.group(1)
            glyph_name = _CHAR_TO_GLYPH.get(char)
            if glyph_name is None:
                glyph_name = f"uni{ord(char):04X}"
            tokens.append({
                "base": glyph_name,
                "lig_base": None,
                "lig_mode": None,
                "variants": [],
                "neg_variants": [],
            })
            pos += esc_m.end()
            continue

        loz_m = LOZENGE_RE.match(remaining)
        if loz_m:
            key = loz_m.group(1)
            glyph_name = LOZENGE_MAP.get(key)
            if glyph_name is None:
                raise ValueError(f"Unknown lozenge name: ◊{key}")
            tokens.append({
                "base": glyph_name,
                "lig_base": None,
                "lig_mode": None,
                "variants": [],
                "neg_variants": [],
            })
            pos += loz_m.end()
            continue

        tok_m = TOKEN_RE.match(remaining)
        if tok_m is None:
            raise ValueError(f"Expected glyph token at pos {pos}: {remaining!r}")

        letter = tok_m.group(1)
        lig_mode_char = tok_m.group(2)
        lig_partner = tok_m.group(3)
        variant_str = tok_m.group(4)

        if lig_partner:
            lig_mode = {"": None, "?": "maybe", "|": "maybe_break"}[lig_mode_char or ""]
        else:
            lig_mode = None

        pos_variants = []
        neg_variants = []
        if variant_str:
            for v in variant_str.split("."):
                if not v:
                    continue
                if v.startswith("!"):
                    neg_variants.append(v[1:])
                else:
                    pos_variants.append(v)

        tokens.append({
            "base": _letter_to_qs(letter),
            "lig_base": _letter_to_qs(lig_partner) if lig_partner else None,
            "lig_mode": lig_mode,
            "variants": pos_variants,
            "neg_variants": neg_variants,
        })
        pos += tok_m.end()

    return tokens, connections


# ---------------------------------------------------------------------------
# HTML collector
# ---------------------------------------------------------------------------

class _DataExpectCollector(HTMLParser):
    """Collect data-expect="..." from <td>, <span>, and <dd> elements in HTML.

    Each collected cell records a list of ``runs`` — one per contiguous font
    context inside the cell. A ``<span class="force-junior">`` descendant
    switches the enclosed text to the Junior font; a ``<span
    data-stylistic-set="...">`` descendant starts a new run with per-run
    features. Anything else stays in Senior (the cell's default).
    """

    _TAGS = {"td", "span", "dd"}

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[tuple[str, str | None, int, str | None, list[Run]]] = []
        self._open_tags: list[tuple[str, bool, bool, str | None]] = []
        self._cell_active = False
        self._cell_info: _CellInfo | None = None
        self._runs: list[Run] = []

    def _current_font(self) -> str:
        for _tag, _is_cell, is_junior, _inner_ss in reversed(self._open_tags):
            if is_junior:
                return "junior"
        return "senior"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._TAGS:
            return
        attr_dict = dict(attrs)
        expect = attr_dict.get("data-expect", attr_dict.get("data-expect-noncanonically"))
        css_class = attr_dict.get("class", "") or ""
        is_force_junior = "force-junior" in css_class.split()

        if expect is not None and not self._cell_active:
            self._cell_active = True
            self._cell_info = {
                "expect": expect,
                "line": self.getpos()[0],
                "stylistic_set": attr_dict.get("data-stylistic-set"),
            }
            self._runs = [{"font": "senior", "text": ""}]
            self._open_tags.append((tag, True, False, None))
            return

        if self._cell_active:
            inner_ss = attr_dict.get("data-stylistic-set")
            self._open_tags.append((tag, False, is_force_junior, inner_ss))
            if is_force_junior:
                self._runs.append({"font": "junior", "text": ""})
            elif inner_ss:
                features = {f"ss{ss.zfill(2)}": True for ss in inner_ss.split()}
                self._runs.append({"font": self._current_font(), "text": "", "features": features})

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._TAGS or not self._open_tags:
            return
        open_tag, is_cell_start, was_force_junior, was_inner_ss = self._open_tags[-1]
        if open_tag != tag:
            return
        self._open_tags.pop()

        if not self._cell_active:
            return

        if is_cell_start:
            non_empty: list[Run] = []
            for r in self._runs:
                text = r["text"].strip()
                if not text:
                    continue
                entry: Run = {"font": r["font"], "text": text}
                features = r.get("features")
                if features:
                    entry["features"] = features
                non_empty.append(entry)
            if not non_empty:
                non_empty = [{"font": "senior", "text": ""}]
            full_text = "".join(r["text"] for r in self._runs).strip()
            assert self._cell_info is not None
            self.cells.append((
                full_text,
                self._cell_info["expect"],
                self._cell_info["line"],
                self._cell_info["stylistic_set"],
                non_empty,
            ))
            self._cell_active = False
            self._cell_info = None
            self._runs = []
        elif was_force_junior:
            resume_font = self._current_font()
            self._runs.append({"font": resume_font, "text": ""})
        elif was_inner_ss:
            self._runs.append({"font": self._current_font(), "text": ""})

    def handle_data(self, data: str) -> None:
        if self._cell_active:
            self._runs[-1]["text"] += data


# ---------------------------------------------------------------------------
# Font/anchor loading
# ---------------------------------------------------------------------------

def load_font(variant: str = "senior") -> hb.Font:
    blob = hb.Blob.from_file_path(str(FONT_PATHS[variant]))
    face = hb.Face(blob)
    return hb.Font(face)


def build_anchor_map(variant: str = "senior") -> tuple[AnchorMap, dict[str, set[int]]]:
    data = load_glyph_data(GLYPH_DATA_DIR)
    _COMPILED_GLYPH_META[variant] = compile_glyph_set(data, variant).glyph_meta

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
    return result, base_potential_entries


def _compiled_glyph_meta(name: str, variant: str = "senior") -> JoinGlyph:
    if variant not in _COMPILED_GLYPH_META:
        build_anchor_map(variant)
    meta = _COMPILED_GLYPH_META[variant].get(name)
    if meta is None:
        raise AssertionError(
            f"Missing compiled glyph metadata for {name!r} in {variant}"
        )
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


# ---------------------------------------------------------------------------
# Maybe-ligature expansion
# ---------------------------------------------------------------------------

def _expand_maybe_ligatures(tokens: list[ExpectToken], connections: list[Connection]) -> list[tuple[list[ExpectToken], list[Connection]]]:
    maybe_indices = [
        i for i, tok in enumerate(tokens)
        if tok.get("lig_mode") in ("maybe", "maybe_break")
    ]
    if not maybe_indices:
        return [(tokens, connections)]

    from itertools import product

    interpretations = []
    for combo in product([True, False], repeat=len(maybe_indices)):
        maybe_map = dict(zip(maybe_indices, combo))
        new_tokens = []
        new_connections = []
        for i, tok in enumerate(tokens):
            if i > 0:
                new_connections.append(connections[i - 1])
            if i in maybe_map and not maybe_map[i]:
                conn_kind = "maybe" if tok["lig_mode"] == "maybe" else "break"
                new_tokens.append({
                    "base": tok["base"],
                    "lig_base": None,
                    "lig_mode": None,
                    "variants": [],
                    "neg_variants": [],
                })
                new_connections.append({"kind": conn_kind, "y": None})
                new_tokens.append({
                    "base": tok["lig_base"],
                    "lig_base": None,
                    "lig_mode": None,
                    "variants": [],
                    "neg_variants": [],
                })
            else:
                new_tokens.append({**tok, "lig_mode": None})
        interpretations.append((new_tokens, new_connections))
    return interpretations


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def _try_interpretation(font: hb.Font, anchor_map: AnchorMap,
                        glyph_names: list[str],
                        tokens: list[ExpectToken],
                        connections: list[Connection],
                        base_potential_entries: dict[str, set[int]] | None = None,
                        variant: str = "senior") -> str | None:
    if len(glyph_names) != len(tokens):
        return (
            f"Glyph count mismatch: got {glyph_names}, "
            f"expected {len(tokens)} tokens"
        )

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
        if conn["kind"] == "break":
            if common_ys:
                return (
                    f"Connection {i}: expected break between {left} and {right}, "
                    f"but found common Y values {common_ys}"
                )
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
                            f"but {left} exits at Y={suspect_ys} (not on base form) "
                            f"matching potential entries for {right_base} — forward "
                            f"calt may have selected the half form across a break"
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


def run_shaping_test(font: hb.Font, anchor_map: AnchorMap, text: str,
                     expect_str: str,
                     base_potential_entries: dict[str, set[int]] | None = None,
                     features: dict[str, bool] | None = None,
                     variant: str = "senior") -> None:
    tokens, connections = parse_expect(expect_str)

    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    if features:
        hb.shape(font, buf, features)
    else:
        hb.shape(font, buf)

    infos = buf.glyph_infos
    glyph_names = [font.glyph_to_string(info.codepoint) for info in infos]

    interpretations = _expand_maybe_ligatures(tokens, connections)

    if len(interpretations) == 1:
        error = _try_interpretation(
            font, anchor_map, glyph_names,
            interpretations[0][0], interpretations[0][1],
            base_potential_entries, variant=variant,
        )
        if error:
            raise AssertionError(error)
        return

    errors = []
    for interp_tokens, interp_connections in interpretations:
        error = _try_interpretation(
            font, anchor_map, glyph_names,
            interp_tokens, interp_connections,
            base_potential_entries, variant=variant,
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

def _token_input_char_count(tok: ExpectToken) -> int:
    """How many base (non-modifier) input chars this token consumes."""
    if tok.get("lig_base"):
        return 2
    return 1


def _is_modifier_char(c: str) -> bool:
    """Characters that attach to the previous char instead of forming their own token.

    Currently: variation selectors (U+FE00..U+FE0F).
    """
    return 0xFE00 <= ord(c) <= 0xFE0F


def _partition_by_runs(runs: list[Run], tokens: list[ExpectToken], connections: list[Connection]) -> list[PartitionedRun]:
    """Partition ``tokens`` and ``connections`` across ``runs``.

    Walks the concatenated run text char-by-char, attributing each token to
    the run where its base characters land. Variation selectors and similar
    modifier chars are eaten by the preceding token. Connections between
    tokens that end up in different runs are dropped (the inter-run gap is
    implicit, since runs are shaped independently).

    Returns a list of dicts with keys ``font``, ``text``, ``tokens``,
    ``connections``.
    """
    full_text = ""
    run_for_char = []
    for run_idx, run in enumerate(runs):
        for _ in run["text"]:
            run_for_char.append(run_idx)
        full_text += run["text"]

    token_to_run = []
    char_idx = 0
    for tok in tokens:
        base = _token_input_char_count(tok)
        # Skip any leading modifier chars (shouldn't happen, but defend).
        while char_idx < len(full_text) and _is_modifier_char(full_text[char_idx]):
            char_idx += 1
        if char_idx >= len(full_text):
            raise ValueError(
                f"Not enough input chars for token {tok!r} at char {char_idx}"
            )
        token_run = run_for_char[char_idx]
        consumed = 0
        while consumed < base:
            if char_idx >= len(full_text):
                raise ValueError(
                    f"Token {tok!r} overruns text at char {char_idx}"
                )
            c = full_text[char_idx]
            if _is_modifier_char(c):
                char_idx += 1
                continue
            if run_for_char[char_idx] != token_run:
                raise ValueError(
                    f"Token {tok!r} straddles run boundary at char {char_idx}"
                )
            char_idx += 1
            consumed += 1
        # Eat trailing modifier chars that attach to this token's last char.
        while char_idx < len(full_text) and _is_modifier_char(full_text[char_idx]):
            char_idx += 1
        token_to_run.append(token_run)

    if char_idx != len(full_text):
        raise ValueError(
            f"Run text not fully consumed: used {char_idx} of "
            f"{len(full_text)} chars"
        )

    slices: list[PartitionedRun] = []
    for run_idx, run in enumerate(runs):
        tok_indices = [i for i, r in enumerate(token_to_run) if r == run_idx]
        if not tok_indices:
            run_tokens: list[ExpectToken] = []
            run_conns: list[Connection] = []
        else:
            run_tokens = [tokens[i] for i in tok_indices]
            run_conns = []
            for i in range(len(tok_indices) - 1):
                if tok_indices[i + 1] != tok_indices[i] + 1:
                    raise ValueError(
                        f"Non-contiguous tokens in run {run_idx}: "
                        f"{tok_indices[i]} -> {tok_indices[i+1]}"
                    )
                run_conns.append(connections[tok_indices[i]])
        sl: PartitionedRun = {"font": run["font"], "text": run["text"], "tokens": run_tokens, "connections": run_conns}
        run_features = run.get("features")
        if run_features:
            sl["features"] = run_features
        slices.append(sl)
    return slices


def run_shaping_test_runs(fonts: dict[str, hb.Font],
                          anchor_maps: dict[str, AnchorMap],
                          runs: list[Run], expect_str: str,
                          base_potential_entries: dict[str, dict[str, set[int]]] | None = None,
                          features: dict[str, bool] | None = None) -> None:
    """Shape each font-variant run independently and verify against expect_str.

    ``fonts`` and ``anchor_maps`` are dicts keyed by variant ("senior", "junior").
    ``runs`` is a list of {"font": variant, "text": str}. Each run is shaped
    against its own font, and the corresponding slice of tokens/connections
    from ``expect_str`` is verified against that run.
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
        glyph_names = [font.glyph_to_string(info.codepoint) for info in infos]

        interpretations = _expand_maybe_ligatures(sub_tokens, sub_conns)

        # Junior has no cursive attachment — suppress connection assertions
        # (glyph-identity assertions still run).
        if variant == "junior":
            interpretations = [
                (t, [Connection(kind="maybe", y=None) for _ in c])
                for t, c in interpretations
            ]

        errors = []
        matched = False
        for interp_tokens, interp_connections in interpretations:
            error = _try_interpretation(
                font, anchor_map, glyph_names,
                interp_tokens, interp_connections,
                potential.get(variant), variant=variant,
            )
            if error is None:
                matched = True
                break
            errors.append(error)
        if matched:
            continue
        raise AssertionError(
            f"[{variant}] No interpretation matched for run {text!r} "
            f"in {expect_str!r} (shaped: {glyph_names}).\n"
            + "\n".join(f"  Interpretation {i+1}: {e}" for i, e in enumerate(errors))
        )
