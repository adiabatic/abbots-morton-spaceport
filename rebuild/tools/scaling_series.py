"""The rune order, the alphabet sizes, and the sub-spec at each size of the nested scaling series. The order lists the ligatures alphabetically, each preceded by any of its components not already listed, then the remaining runes alphabetically. The sizes are `sorted({*range(6, len(order), 2), len(order)})`, and an alphabet size keeps a ligature only when it also keeps every component the ligature names.

`scaling_sweep.py` and `kernel_all_configs.py --alphabet-size` both import this module, so a given size is the same alphabet in both and their readings at that size are comparable. Because the sizes are nested, they are also comparable with each other: the alphabet at size k is the alphabet at size k-2 plus two more runes.
"""

from __future__ import annotations

import dataclasses

from rebuild.pipeline.model import ResolvedSpec


def series_order(spec: ResolvedSpec) -> list[str]:
    """Return the order the alphabet sizes are cut from: the ligatures alphabetically, each preceded by its components, then the remaining runes alphabetically."""
    names = sorted(spec.runes)
    order: list[str] = []
    for name in names:
        sequence = spec.runes[name].sequence
        if not sequence:
            continue
        for part in sequence:
            if part not in order:
                order.append(part)
        if name not in order:
            order.append(name)
    for name in names:
        if name not in order:
            order.append(name)
    return order


def series_sizes(order: list[str]) -> list[int]:
    """Return the rune counts to cut sub-specs at: every even count from 6 below the alphabet's size, plus the whole alphabet."""
    return sorted({*range(6, len(order), 2), len(order)})


def sub_spec(spec: ResolvedSpec, order: list[str], size: int) -> ResolvedSpec:
    """Return the first `size` runes of `order` as a spec of their own, in the original spec's rune order, without any ligature whose components are not all among them."""
    candidates = set(order[:size])
    keep: set[str] = set()
    for name in candidates:
        sequence = spec.runes[name].sequence
        if not sequence or set(sequence) <= candidates:
            keep.add(name)
    return dataclasses.replace(spec, runes={name: rune for name, rune in spec.runes.items() if name in keep})
