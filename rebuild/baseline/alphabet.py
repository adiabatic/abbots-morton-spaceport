"""The 47-symbol basis alphabet (44 Quikscript runes, space, ZWNJ, namer dot), basis enumeration in the canonical row order, and shard partitioning by (length, first symbol). Concatenating shards in shard-key order reproduces the canonical order exactly, which is what makes the parallel extraction deterministic."""

from __future__ import annotations

import hashlib
from functools import cache
from itertools import product

import yaml

from .model import REPO_ROOT

SPACE = 0x0020
NAMER_DOT = 0x00B7
ZWNJ = 0x200C

RUNE_CODEPOINTS: tuple[int, ...] = tuple(range(0xE650, 0xE66D)) + tuple(range(0xE670, 0xE67F))

ALPHABET: tuple[int, ...] = tuple(sorted((SPACE, NAMER_DOT, ZWNJ) + RUNE_CODEPOINTS))

MAX_LENGTH = 4

assert len(RUNE_CODEPOINTS) == 44
assert len(ALPHABET) == 47


def alphabet_sha256() -> str:
    listing = "\n".join(f"{cp:04X}" for cp in ALPHABET)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


@cache
def symbol_names() -> dict[int, str]:
    """Codepoint-to-name mapping for the human-readable legend in SUMMARY.md. Rune names come from postscript_glyph_names.yaml; the three boundary-ish symbols get their PostScript-style names directly."""
    with (REPO_ROOT / "postscript_glyph_names.yaml").open() as f:
        ps_names = yaml.safe_load(f)
    by_codepoint = {codepoint: name for name, codepoint in ps_names.items()}
    names = {cp: by_codepoint[cp] for cp in RUNE_CODEPOINTS}
    names[SPACE] = "space"
    names[NAMER_DOT] = "periodcentered"
    names[ZWNJ] = "uni200C"
    return names


def basis_size(max_length: int = MAX_LENGTH) -> int:
    return sum(len(ALPHABET) ** length for length in range(1, max_length + 1))


def shard_keys(max_length: int = MAX_LENGTH) -> list[tuple[int, int]]:
    return [(length, index) for length in range(1, max_length + 1) for index in range(len(ALPHABET))]


def shard_size(length: int) -> int:
    return len(ALPHABET) ** (length - 1)


def shard_strings(length: int, first_index: int):
    first = ALPHABET[first_index]
    for rest in product(ALPHABET, repeat=length - 1):
        yield (first, *rest)


def enumerate_basis(max_length: int = MAX_LENGTH):
    for length, first_index in shard_keys(max_length):
        yield from shard_strings(length, first_index)


def string_text(codepoints: tuple[int, ...]) -> str:
    return "".join(chr(cp) for cp in codepoints)
