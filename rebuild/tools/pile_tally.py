"""A debug tally of the piles a surface build holds, read at its phase boundaries, so a peak the journal records can be attributed to the pile that made it (issue #156). Off unless the caller's environment carries `AMS_SURFACE_PILE_TALLY=1` (`TALLY_ENV`), which the artifact cycle never sets and which reaches the build child through the environment it inherits; with the variable unset `from_environment` answers None and the build's call sites are a handful of `if tally:` misses, so the shards and every other line the build prints are exactly what they were.

The figures are attribution, never precision. Each pile is a `sys.getsizeof` walk over a bounded, evenly spaced sample of its members (`SAMPLE_SIZE` of them), scaled by the member count and added to the container's own size — seconds over a corpus of a million units, where a walk over every member would be minutes. The walk descends into the stdlib containers, into `__dict__` and every slot, and stops at strings, bytes and numbers; an object seen once in a sample is counted once, so members that share a pooled tuple or an interned name are charged for one copy between them, which is the same discount the process gets. A pile can name leaf types the walk counts shallow rather than entering, which is how one pile is kept from subsuming another it holds pointers into — the workload's units carry their audit rows, and a units pile that entered the rows would always outrank the rows pile by construction. A pile whose members are themselves tables — the enricher's subset rows, one whole table per configuration — is held `nested`, so each sampled member is estimated by the same bounded sample rather than walked whole, its count is the members' rows summed, and a boundary stays seconds-cheap over tables of a million rows apiece. A pile held (`hold`) is re-estimated at every boundary after it, since the build mutates and drains what it holds, and a pile the build empties reads as empty at the boundaries past that point rather than dropping off the record — the audit rows once the content keys have read them, the fresh spool's addresses once the runner has closed; one read (`hold_reading`) is a callable answering (count, bytes) itself, for an instrument like `ink.shape_memo_census` that already keeps its own census, or for a pile the build holds only through what else it holds, like those rows, which are re-gathered off the units at each boundary rather than kept in a list the tally would be keeping alive. The roster is the build's own: `build_m1`, `_FreshRunner.hold_piles`, `_write_surface` and `_surface_worker` in `rebuild/review/build.py` name what they hold, and what the fresh units leave in any process past their batch is the spool address per unit (`runner.spooled` in the parent, `worker.spooled` in a pooled worker until it is answered with), never a pile of enrichments — a fresh unit's fragment goes to the spool as it is drafted.

The format, one line per pile per boundary and sorted largest first within a boundary, then one line naming the largest — every line prefixed `[tally] ` (`TALLY`) and whitespace-tokenized, so a `grep '^\\[tally\\]'` over the cycle's per-step log (`var/build-logs/<run>/<nn>-surface-build.log`, where the build's stdout lands) reads the whole record back:

    [tally] <boundary> <pile> count=<n> est_bytes=<n> est_gb=<x.xx>
    [tally] <boundary> largest=<pile>

`<boundary>` is the build phase whose end the reading was taken at (`load`, `plan`, `units`, `manifest+check`, `census-facts`, `cache`), or `w<i>/<phase>` for a pooled worker's own piles at the end of one of its phases; neither carries whitespace. `est_gb` is `peak_rss`'s decimal gigabyte, the unit every peak in the journal is stated in, so a pile's figure reads beside the `rss_gb=` token on the same phase's `[t]` line without conversion. A boundary with nothing held prints only the `largest=` line, with `-` for the name, so the boundary is still on the record.

Stdlib-only, like `peak_rss`: the build imports this beside its other cost readings, and rebuild/test_review_code_closure.py pins it among the width and telemetry modules that may not move a byte of a unit's products.
"""

from __future__ import annotations

import os
import sys
from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from itertools import islice
from typing import IO

TALLY_ENV = "AMS_SURFACE_PILE_TALLY"
TALLY = "[tally] "
SAMPLE_SIZE = 256

_BYTES_PER_GB = 1e9
_LEAVES = (str, bytes, bytearray, int, float, complex, bool, type(None))
_SEQUENCES = (list, tuple, set, frozenset, deque)


def enabled(environ: Mapping[str, str] = os.environ) -> bool:
    return environ.get(TALLY_ENV) == "1"


def deep_size(root: object, seen: set[int], leaf_types: tuple[type, ...] = ()) -> int:
    """The bytes reachable from `root` that `seen` has not already charged: the container sizes, the slots and instance dicts, and the leaves under them. `seen` is the caller's, shared across one sample so a shared object is charged once per pile rather than once per member."""
    total = 0
    stack: list[object] = [root]
    while stack:
        item = stack.pop()
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        total += sys.getsizeof(item)
        if isinstance(item, _LEAVES) or (leaf_types and isinstance(item, leaf_types)):
            continue
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, _SEQUENCES):
            stack.extend(item)
        else:
            instance_dict = getattr(item, "__dict__", None)
            if instance_dict is not None:
                stack.append(instance_dict)
            for cls in type(item).__mro__:
                slots = cls.__dict__.get("__slots__", ())
                for name in (slots,) if isinstance(slots, str) else slots:
                    if name in ("__dict__", "__weakref__"):
                        continue
                    try:
                        stack.append(getattr(item, name))
                    except AttributeError:
                        pass
    return total


def _sample(pile: Collection, size: int) -> list:
    members: Iterable = pile.items() if isinstance(pile, Mapping) else pile
    step = max(1, len(pile) // size)
    return list(islice(members, 0, None, step))[:size]


def _is_table(value: object) -> bool:
    return isinstance(value, Collection) and not isinstance(value, _LEAVES)


def estimate(
    pile: Collection,
    *,
    leaf_types: tuple[type, ...] = (),
    sample_size: int = SAMPLE_SIZE,
    nested: bool = False,
) -> tuple[int, int]:
    """(count, estimated bytes) for one pile: the container's own size plus the deep size of an evenly spaced sample of its members, scaled by the count. A mapping's sample is its items, so keys and values are both charged. `nested` reads a pile of tables: each sampled member that is a collection is estimated by its own bounded sample instead of walked in full, and the count answered is the members' lengths summed, so the figure is the rows the pile holds rather than the tables."""
    members = len(pile)
    total = sys.getsizeof(pile)
    if members == 0:
        return 0, total
    sample = _sample(pile, sample_size)
    seen: set[int] = set()
    sampled = 0
    for member in sample:
        key, value = member if isinstance(pile, Mapping) else (None, member)
        if key is not None:
            sampled += deep_size(key, seen, leaf_types)
        if nested and _is_table(value):
            sampled += estimate(value, leaf_types=leaf_types, sample_size=sample_size)[1]
        else:
            sampled += deep_size(value, seen, leaf_types)
    count = members
    if nested:
        values: Iterable = pile.values() if isinstance(pile, Mapping) else pile
        count = sum(len(value) if _is_table(value) else 1 for value in values)
    return count, total + round(sampled * members / len(sample))


@dataclass(frozen=True)
class Reading:
    pile: str
    count: int
    est_bytes: int

    @property
    def line_body(self) -> str:
        return f"{self.pile} count={self.count} est_bytes={self.est_bytes} est_gb={self.est_bytes / _BYTES_PER_GB:.2f}"


class PileTally:
    """The piles one process holds, named as they come into being and read at every boundary after. `out` is resolved at write time, never bound, for the reason `rebuild.tools.console` gives: the cycle tees `sys.stdout` after the module is imported."""

    def __init__(self, *, out: IO[str] | None = None, sample_size: int = SAMPLE_SIZE) -> None:
        self._out = out
        self._sample_size = sample_size
        self._held: dict[str, tuple[Collection, tuple[type, ...], bool]] = {}
        self._readings: dict[str, Callable[[], tuple[int, int]]] = {}

    def hold(
        self, name: str, pile: Collection, *, leaf_types: tuple[type, ...] = (), nested: bool = False
    ) -> None:
        self._readings.pop(name, None)
        self._held[name] = (pile, leaf_types, nested)

    def hold_reading(self, name: str, read: Callable[[], tuple[int, int]]) -> None:
        self._held.pop(name, None)
        self._readings[name] = read

    def release(self, name: str) -> None:
        self._held.pop(name, None)
        self._readings.pop(name, None)

    def readings(self) -> list[Reading]:
        readings = [
            Reading(
                name,
                *estimate(pile, leaf_types=leaf_types, sample_size=self._sample_size, nested=nested),
            )
            for name, (pile, leaf_types, nested) in self._held.items()
        ]
        for name, read in self._readings.items():
            count, est_bytes = read()
            readings.append(Reading(name, int(count), int(est_bytes)))
        return sorted(readings, key=lambda reading: (-reading.est_bytes, reading.pile))

    def boundary(self, name: str) -> list[Reading]:
        readings = self.readings()
        lines = [f"{TALLY}{name} {reading.line_body}" for reading in readings]
        lines.append(f"{TALLY}{name} largest={readings[0].pile if readings else '-'}")
        stream = sys.stdout if self._out is None else self._out
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        return readings


def from_environment(
    environ: Mapping[str, str] = os.environ, *, out: IO[str] | None = None
) -> PileTally | None:
    """The tally the build works through when `AMS_SURFACE_PILE_TALLY=1` is in its environment, and None otherwise — the None is the whole of the off switch, so a build that has one prints nothing and estimates nothing."""
    return PileTally(out=out) if enabled(environ) else None
