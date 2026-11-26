"""The review corpus's unit ids (rebuild/REVIEW-PLAN.md §2.1): a unit's id is `u-` and the first 64 bits of its content key, a sha256 hex digest of what the unit is (`diff_units.unit_identity`), as eleven base58 symbols. The id says nothing about the unit's place in the queue, so the same content has the same id on every corpus it appears on, and a verdict follows its unit across rebuilds by id."""

from __future__ import annotations

import re

# Bitcoin's base58 alphabet: the digits and both cases minus 0, O, I and l, which are easily confused. Case is significant, which keeps ids short; ids are copied and pasted, not transcribed.
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
# 58**10 < 2**64 <= 58**11, so eleven symbols hold any 64-bit value and the width never varies.
ID_SYMBOLS = 11
_ID_PATTERN = re.compile(rf"^u-[{BASE58_ALPHABET}]{{{ID_SYMBOLS}}}$")
# Every two-symbol string indexed by its value, so `base58_64` spells two symbols a division: the leading symbol and five pairs make `ID_SYMBOLS`.
_PAIR_BASE = 58 * 58
_BASE58_PAIRS = tuple(high + low for high in BASE58_ALPHABET for low in BASE58_ALPHABET)


def base58_64(digest_hex: str) -> str:
    """Return the first 64 bits of a hex digest as `ID_SYMBOLS` base58 symbols, most significant first, padded with the zero symbol `1`. The width is fixed and the alphabet is in ascending ASCII order, so the strings sort in the same order as the numbers. Case is significant."""
    pairs = _BASE58_PAIRS
    value, p0 = divmod(int(digest_hex[:16], 16), _PAIR_BASE)
    value, p1 = divmod(value, _PAIR_BASE)
    value, p2 = divmod(value, _PAIR_BASE)
    value, p3 = divmod(value, _PAIR_BASE)
    value, p4 = divmod(value, _PAIR_BASE)
    return BASE58_ALPHABET[value] + pairs[p4] + pairs[p3] + pairs[p2] + pairs[p1] + pairs[p0]


def unit_id_for(content_key: str) -> str:
    """Return a unit's id: `u-` followed by the first 64 bits of its content key in base58, for example `u-3mJ7kPq2Xw9`. Two units with the same content key have the same id."""
    return "u-" + base58_64(content_key)


def is_content_id(value: object) -> bool:
    """Return whether `value` is a unit id: `u-`, then exactly `ID_SYMBOLS` base58 symbols."""
    return isinstance(value, str) and _ID_PATTERN.match(value) is not None
