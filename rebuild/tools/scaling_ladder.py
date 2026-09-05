"""The nested scaling ladder's one authority: the rune order the rungs are cut from, the rung sizes, and the sub-spec one rung stands for. Ligature closure first — every ligature preceded by the components it names — and then the remaining runes alphabetically, with rungs at `sorted({*range(6, len(order), 2), len(order)})` and each rung's keep-set filtered so a ligature rides only when every component it names rides with it.

`bench-the-rebuild/scaling/scaling.py` and `bench-the-rebuild/levers/kernel_all_configs.py` both import this, so the sweep and the lever cut the same rungs rather than two ladders that could drift apart: a lever reading is comparable with a sweep reading only because rung k means one alphabet and not two. Nesting is what makes the rungs comparable to each other as well — rung k is rung k-2 plus two more runes, never a different alphabet.

The spec-ingest parity check is `rebuild/test_kernel_io.py`'s spec-echo test, in the contracts lane of every `make test-rebuild`, not part of the ladder.
"""

from __future__ import annotations

import dataclasses

from rebuild.pipeline.model import ResolvedSpec


def ladder_order(spec: ResolvedSpec) -> list[str]:
    """The nested subset order the rungs are cut from: every ligature preceded by the components it names, then the remaining runes alphabetically. Nesting is what makes the rungs comparable to each other — rung k is rung k-2 plus two more runes, never a different alphabet."""
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


def ladder_rungs(order: list[str]) -> list[int]:
    """Rune counts to cut sub-specs at: every even count from 6 up, plus the whole alphabet however odd its size."""
    return sorted({*range(6, len(order), 2), len(order)})


def sub_spec(spec: ResolvedSpec, order: list[str], rung: int) -> ResolvedSpec:
    """The first `rung` runes of the nested order as a spec of their own, in the original spec's rune order, with any ligature whose components did not make the cut dropped."""
    candidates = set(order[:rung])
    keep: set[str] = set()
    for name in candidates:
        sequence = spec.runes[name].sequence
        if not sequence or set(sequence) <= candidates:
            keep.add(name)
    return dataclasses.replace(spec, runes={name: rune for name, rune in spec.runes.items() if name in keep})
