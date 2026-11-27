"""The build's alphabet: the code points of the letters with rune files and the three boundary tokens (`0x0020` space, `0x00B7` namer dot, `0x200C` ZWNJ).

`ALPHABET` is the set written out below. While the alphabet switch (`spec_load.RUNES_ENV`) is set, it is instead the alphabet of the spec `spec_load.load_default_spec` narrows to, computed when this module is imported, so every module that imports the name takes the narrowed set.
"""

from __future__ import annotations

from engine.pipeline import spec_load
from engine.pipeline.labels import spec_alphabet


def _switch_alphabet() -> frozenset[int] | None:
    """Return the alphabet of the spec `spec_load.load_default_spec` narrows to while the alphabet switch (`spec_load.RUNES_ENV`) is set, the code points `labels.spec_alphabet` reads from it, or None when the switch is unset."""
    if spec_load.switch_runes() is None:
        return None
    return frozenset(ord(symbol) for symbol in spec_alphabet(spec_load.load_default_spec()))


ALPHABET = _switch_alphabet() or frozenset(
    {
        0x0020,
        0x00B7,
        0x200C,
        0xE650,
        0xE651,
        0xE652,
        0xE653,
        0xE654,
        0xE655,
        0xE656,
        0xE657,
        0xE658,
        0xE659,
        0xE65A,
        0xE65B,
        0xE65C,
        0xE65D,
        0xE65E,
        0xE65F,
        0xE660,
        0xE661,
        0xE662,
        0xE663,
        0xE664,
        0xE665,
        0xE666,
        0xE667,
        0xE668,
        0xE669,
        0xE66A,
        0xE66B,
        0xE66C,
        0xE670,
        0xE671,
        0xE672,
        0xE673,
        0xE674,
        0xE675,
        0xE676,
        0xE677,
        0xE678,
        0xE679,
        0xE67A,
        0xE67B,
        0xE67C,
        0xE67D,
        0xE67E,
    }
)
