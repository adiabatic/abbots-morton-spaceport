"""The `data-expect` grammar (`doc/data-expect.md`): `parse_expect` turns an expect string into tokens and the connections between them, `expand_maybe_ligatures` gives each reading of its maybe-ligatures, and `token_char_spans` maps each token onto the characters of the text it describes.

This module is the grammar's authority. The checker (`engine/validation/check.py`) and the corpus collector (`engine/validation/corpus.py`) import it. It is a leaf: it imports nothing from `engine/pipeline/`, `engine/review/`, `tools/` or `test/`.

The character map (`CHAR_TO_GLYPH`) is read from postscript_glyph_names.yaml when the module is imported. An escaped character (`\\X`) names the glyph that map gives it, or `uniXXXX` when the map has none.
"""

from __future__ import annotations

import re
from itertools import product
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PS_NAMES_PATH = REPO_ROOT / "postscript_glyph_names.yaml"


class ExpectToken(TypedDict):
    base: str
    lig_base: str | None
    lig_mode: Literal["maybe", "maybe_break"] | None
    variants: list[str]
    neg_variants: list[str]
    exact_glyph: bool


class Connection(TypedDict):
    kind: Literal["join", "break", "break_no_isolation", "height", "maybe"]
    y: int | None


class Run(TypedDict):
    font: str
    text: str
    features: NotRequired[dict[str, bool]]


class PartitionedRun(Run):
    tokens: list[ExpectToken]
    connections: list[Connection]


def build_char_to_glyph_name(path: Path = PS_NAMES_PATH) -> dict[str, str]:
    """Return the map from each character to its glyph name, read from postscript_glyph_names.yaml."""
    with open(path) as f:
        ps_names = yaml.safe_load(f)
    result: dict[str, str] = {}
    for name, codepoint in ps_names.items():
        result[chr(codepoint)] = name
    return result


CHAR_TO_GLYPH = build_char_to_glyph_name()

TOKEN_RE = re.compile(
    r"""
    ·(-ing|J['’]?ai|[A-Z][a-z]*)  # letter name (·Bay, ·-ing, ·J'ai)
    (?:\+([?|]?)([A-Z][a-z]*))?         # optional ligature partner (+Utter, +?Utter, +|Utter)
    ((?:\.(?:!?[a-z][-a-z0-9]*|∅))*)    # optional variant assertions (.half.extended, .!exit, .∅)
    """,
    re.VERBOSE,
)

ESCAPE_RE = re.compile(r"\\(.)")

LOZENGE_RE = re.compile(r"◊([A-Za-z]+)")
LOZENGE_MAP = {
    "space": "space",
    "ZWNJ": "space",
}

CONN_RE = re.compile(r"\s*~([xbt6])~\s*|\s*(\|\?\|)\s*|\s*(\?)\s*|\s*\|\s*")

# Each height connector's letter and the junction height it asserts.
HEIGHT_MAP = {"x": 5, "b": 0, "t": 8, "6": 6}


def letter_to_qs(name: str) -> str:
    if name == "-ing":
        return "qsIng"
    name = name.replace("’", "").replace("'", "")
    return "qs" + name


def _plain_token(base: str) -> ExpectToken:
    return {
        "base": base,
        "lig_base": None,
        "lig_mode": None,
        "variants": [],
        "neg_variants": [],
        "exact_glyph": False,
    }


def parse_expect(raw: str) -> tuple[list[ExpectToken], list[Connection]]:
    """Parse a data-expect string into tokens and connections, with one connection between each pair of adjacent tokens.

    A token's `lig_mode` is None for a required ligature (`+`), "maybe" for `+?`, and "maybe_break" for `+|`. `variants` and `neg_variants` hold the `.name` and `.!name` assertions, which the checker reads (`check.MODIFIERS`). `exact_glyph` is set by `.∅`, which cannot be combined with another assertion.

    A connection's `kind` is "join" for whitespace alone, "height" for `~x~`, `~b~`, `~t~`, or `~6~` (with `y` set to that height), "break" for `|`, "maybe" for `?`, and "break_no_isolation" for `|?|`, a break the checker does not hold to the isolation invariant.
    """
    raw = raw.strip()
    tokens: list[ExpectToken] = []
    connections: list[Connection] = []
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
                    raise ValueError(f"Expected connection operator at pos {pos}: {remaining!r}")
            else:
                if conn_m.group(1):
                    h = conn_m.group(1)
                    connections.append({"kind": "height", "y": HEIGHT_MAP[h]})
                elif conn_m.group(2):
                    connections.append({"kind": "break_no_isolation", "y": None})
                elif conn_m.group(3):
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
            glyph_name = CHAR_TO_GLYPH.get(char)
            if glyph_name is None:
                glyph_name = f"uni{ord(char):04X}"
            tokens.append(_plain_token(glyph_name))
            pos += esc_m.end()
            continue

        loz_m = LOZENGE_RE.match(remaining)
        if loz_m:
            key = loz_m.group(1)
            glyph_name = LOZENGE_MAP.get(key)
            if glyph_name is None:
                raise ValueError(f"Unknown lozenge name: ◊{key}")
            tokens.append(_plain_token(glyph_name))
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
        exact_glyph = False
        if variant_str:
            for v in variant_str.split("."):
                if not v:
                    continue
                if v == "∅":
                    if exact_glyph:
                        raise ValueError(f"Exact glyph assertion .∅ appears more than once in {raw!r}")
                    exact_glyph = True
                elif v.startswith("!"):
                    neg_variants.append(v[1:])
                else:
                    pos_variants.append(v)
        if exact_glyph and (pos_variants or neg_variants):
            raise ValueError(
                f"Exact glyph assertion .∅ cannot be combined with other variant assertions in {raw!r}"
            )

        tokens.append(
            {
                "base": letter_to_qs(letter),
                "lig_base": letter_to_qs(lig_partner) if lig_partner else None,
                "lig_mode": lig_mode,
                "variants": pos_variants,
                "neg_variants": neg_variants,
                "exact_glyph": exact_glyph,
            }
        )
        pos += tok_m.end()

    return tokens, connections


def expand_maybe_ligatures(
    tokens: list[ExpectToken], connections: list[Connection]
) -> list[tuple[list[ExpectToken], list[Connection]]]:
    """Return every reading of the maybe-ligatures (`+?`, `+|`) among `tokens`: the parse itself when there are none, and otherwise one interpretation per way of keeping or splitting each, in `itertools.product([True, False], ...)` order, kept first. A split token becomes its two letters with no assertions, joined by a "maybe" connection for `+?` and a "break" for `+|`; a kept one loses its `lig_mode`."""
    maybe_indices = [i for i, tok in enumerate(tokens) if tok.get("lig_mode") in ("maybe", "maybe_break")]
    if not maybe_indices:
        return [(tokens, connections)]

    interpretations = []
    for combo in product([True, False], repeat=len(maybe_indices)):
        maybe_map = dict(zip(maybe_indices, combo))
        new_tokens: list[ExpectToken] = []
        new_connections: list[Connection] = []
        for i, tok in enumerate(tokens):
            if i > 0:
                new_connections.append(connections[i - 1])
            if i in maybe_map and not maybe_map[i]:
                conn_kind = "maybe" if tok["lig_mode"] == "maybe" else "break"
                new_tokens.append(_plain_token(tok["base"]))
                new_connections.append({"kind": conn_kind, "y": None})
                lig_base = tok["lig_base"]
                assert lig_base is not None
                new_tokens.append(_plain_token(lig_base))
            else:
                new_tokens.append({**tok, "lig_mode": None})
        interpretations.append((new_tokens, new_connections))
    return interpretations


def token_input_char_count(tok: ExpectToken) -> int:
    """Return the number of input characters the token covers, not counting variation selectors: two for a ligature, one otherwise."""
    if tok.get("lig_base"):
        return 2
    return 1


def is_modifier_char(c: str) -> bool:
    """Return whether `c` is a variation selector (U+FE00 to U+FE0F), which attaches to the character before it instead of forming a token."""
    return 0xFE00 <= ord(c) <= 0xFE0F


def token_char_spans(text: str, tokens: list[ExpectToken]) -> list[tuple[int, int]]:
    """Return one half-open `(start, end)` character range per token in `text`.

    Each range covers the token's characters and the variation selectors after its last one. Raises ValueError when `text` runs out before the last token ends or has characters left over.
    """
    spans: list[tuple[int, int]] = []
    char_idx = 0
    for tok in tokens:
        while char_idx < len(text) and is_modifier_char(text[char_idx]):
            char_idx += 1
        if char_idx >= len(text):
            raise ValueError(f"Not enough input chars for token {tok!r} at char {char_idx}")
        start = char_idx
        base = token_input_char_count(tok)
        consumed = 0
        while consumed < base:
            if char_idx >= len(text):
                raise ValueError(f"Token {tok!r} overruns text at char {char_idx}")
            if is_modifier_char(text[char_idx]):
                char_idx += 1
                continue
            char_idx += 1
            consumed += 1
        while char_idx < len(text) and is_modifier_char(text[char_idx]):
            char_idx += 1
        spans.append((start, char_idx))
    if char_idx != len(text):
        raise ValueError(f"Text not fully consumed by tokens: used {char_idx} of " f"{len(text)} chars")
    return spans
