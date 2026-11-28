"""The rune order alphabets are cut in, and the sub-spec at each cut: the alphabets the memory series measures (`engine/tools/memory_series.py`). The order lists the ligatures alphabetically, each preceded by any of its components not already listed, then the remaining runes alphabetically, and a cut keeps a ligature only when it also keeps every component the ligature names.

The cuts are nested: the alphabet at a size holds the alphabet at every smaller size, so readings at two sizes are comparable, and a cut at a given size is the same alphabet wherever it is taken.
"""

from __future__ import annotations

import dataclasses

from engine.pipeline.model import ResolvedSpec


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


def sub_spec(spec: ResolvedSpec, order: list[str], size: int) -> ResolvedSpec:
    """Return the first `size` runes of `order` as a spec of their own, in the original spec's rune order, without any ligature whose components are not all among them. The cut carries `narrowed` (`model.ResolvedSpec.narrowed`) when it holds fewer runes than `spec`, or `spec` is already narrowed, as `spec_load.narrowed`'s result does, so the kern stage reads a cut as it reads the fault kit's small alphabets, and the whole alphabet's cut as it reads the live build's."""
    candidates = set(order[:size])
    keep: set[str] = set()
    for name in candidates:
        sequence = spec.runes[name].sequence
        if not sequence or set(sequence) <= candidates:
            keep.add(name)
    runes = {name: rune for name, rune in spec.runes.items() if name in keep}
    return dataclasses.replace(spec, runes=runes, narrowed=spec.narrowed or len(runes) < len(spec.runes))
