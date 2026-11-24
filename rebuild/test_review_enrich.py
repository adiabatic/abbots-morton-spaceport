"""Tests for the review corpus's letter names and notation: the letter table against doc/glyph-names.md, the caption notation and its tokens, the numeric character references, the order of the content-derived unit ids, and the subset tables' reader over the frozen mini bundle, so no test reads the live corpus."""

import random
import re
from pathlib import Path

from rebuild.review.enrich import LETTERS, letter_display, notation, notation_tokens, text_entities
from rebuild.review.unit_cache import BASE58_ALPHABET, id_word, unit_id_for
from rebuild.validation.rowmodel import iter_rows

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"


def test_letter_table_matches_glyph_names_doc():
    doc = (REPO_ROOT / "doc" / "glyph-names.md").read_text(encoding="utf-8")
    rows = re.findall(r"\|\s*(·\S+)\s*\|\s*U\+([0-9A-F]{4})\s*\|\s*(qs\w+)\s*\|", doc)
    assert len(rows) == len(LETTERS)
    for display, hex_value, family in rows:
        codepoint = int(hex_value, 16)
        assert LETTERS[codepoint] == family
        assert letter_display(family) == display


def test_notation_examples():
    assert notation((0x200C, 0xE652, 0xE679)) == "◊ZWNJ ·Tea·Oy"
    assert notation((0xE650, 0xE665)) == "·Pea·May"
    assert notation((0x00B7, 0xE679)) == "· ·Oy"
    assert notation((0xE650, 0x0020, 0xE650)) == "·Pea ␣ ·Pea"


def test_text_entities_are_numeric_references():
    assert text_entities((0x200C, 0xE652)) == "&#x200C;&#xE652;"


def test_notation_tokens_align_one_to_one_with_codepoints():
    assert notation_tokens((0x200C, 0xE652, 0xE679)) == ("◊ZWNJ", "·Tea", "·Oy")
    assert notation_tokens((0x00B7, 0xE679)) == ("·", "·Oy")
    assert notation_tokens((0xE650, 0x0020, 0xE650)) == ("·Pea", "␣", "·Pea")
    assert notation_tokens((0xE664, 0xE65D)) == ("·-ing", "·J’ai")


def test_a_unit_ids_string_order_is_the_integer_order_of_the_word_it_encodes():
    """The build's primary-unit resolution breaks a tie between primary units on a unit's id word (`unit_cache.id_word`), so that integer order must match the order of the `unit_id` strings, or a tie could resolve to a different primary unit. `unit_cache.base58_64` writes a fixed `ID_SYMBOLS` symbols from an alphabet in ascending ASCII order, so the base58 form of a 64-bit word sorts as the word does. With the constant `u-` prefix and that fixed width, the whole id string also sorts as its bytes read as a big-endian integer, and its word is the integer it encodes."""
    draws = random.Random(299)
    boundaries = {0, 1, 57, 58, 58**10 - 1, 58**10, 2**63, 2**64 - 1}
    words = sorted(boundaries | {draws.getrandbits(64) for _ in range(4096)})
    ids = [unit_id_for(f"{word:016x}" + "0" * 48) for word in words]
    assert list(BASE58_ALPHABET) == sorted(BASE58_ALPHABET)
    assert ids == sorted(ids)
    assert [id_word(unit_id) for unit_id in ids] == words
    assert len(set(len(unit_id) for unit_id in ids)) == 1
    assert [int.from_bytes(unit_id.encode(), "big") for unit_id in ids] == sorted(
        int.from_bytes(unit_id.encode(), "big") for unit_id in ids
    )


def test_subset_tables_iterate():
    """`iter_rows` reads a subset table's windows: the windows it yields over the bundle's default slice include every window the bundle's audit names. A subset table has a row for every window the configuration renders, divergent or not, so the slice is a superset of the audit's windows. The test checks containment instead of a row count, because a row count would describe this bundle and not the reader."""
    windows = {
        row.split("\t")[1] for row in (MINI / "audit.tsv").read_text(encoding="utf-8").splitlines()[1:]
    }
    yielded = {
        ":".join(f"{value:04X}" for value in row.codepoints)
        for row in iter_rows(MINI / "baseline-default.subset.tsv.gz")
    }
    assert windows <= yielded
