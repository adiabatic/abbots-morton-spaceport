"""The column kit the surface build's packed piles share: the string table every id column names into, the tuple pool the name-tuple columns name into, and the mapping pool the per-config class maps name into. A leaf module — it imports nothing from the repository — so the audit's row columns (`audit.RowColumns`), the workload table (`audit.UnitTable`) and the per-unit store (`unit_store.UnitStore`) can all build on it while `unit_store` keeps importing `audit`, and so the verdict chain, which reaches `audit` through `status`, reaches no telemetry through it: the tables count what they hold, and `pile_tally.column_census` is what prices a pile built over them for the debug tally.

Every table assigns ids in first-seen order and reserves id 0 for the empty value, and each keeps a running total of its members' width — the strings' UTF-8 bytes, the tuples' elements, a mapping's entries as two ids each — so a pile built over them reports its tables exactly rather than by a sampled walk.
"""

from __future__ import annotations

import sys
from collections.abc import Hashable, Mapping
from typing import Generic, TypeVar

_T = TypeVar("_T", bound=Hashable)


class StringTable:
    """One interning table for every string a column names: ids in first-seen order, the empty string at id 0, each string interned through `sys.intern` before the lookup so a name a worker's reply hands back as a fresh copy hits the entry the audit's own copy made. `chars` is the distinct strings' UTF-8 bytes summed, and `len` counts the distinct strings, the empty one excluded, as the tally does. `find` answers the id a string already holds, or None, without assigning one."""

    __slots__ = ("_strings", "_ids", "chars")

    def __init__(self) -> None:
        self._strings: list[str] = [""]
        self._ids: dict[str, int] = {"": 0}
        self.chars = 0

    def id(self, value: str) -> int:
        value = sys.intern(value)
        found = self._ids.get(value)
        if found is None:
            found = len(self._strings)
            self._strings.append(value)
            self._ids[value] = found
            self.chars += len(value.encode())
        return found

    def find(self, value: str) -> int | None:
        return self._ids.get(value)

    def optional(self, value: str | None) -> int:
        return 0 if value is None else self.id(value)

    def __getitem__(self, index: int) -> str:
        return self._strings[index]

    def __len__(self) -> int:
        return len(self._strings) - 1


class TuplePool(Generic[_T]):
    """One pool for every tuple a column points at — a row's kinds, a window's rendered names in either font, a unit's config set, its render groups — with ids in first-seen order and the empty tuple at id 0. `pooled` hands back the one instance the pool holds for a value, so a record built from the pool shares its tuple with every other record stating the same members; `id` is what a column stores. `elements` is the distinct tuples' lengths summed and `len` their number, the empty one excluded, which is what a packed side column over the pool is priced from; the strings are charged to the string table they intern through and never here. The lookup from tuple to id is a dict over every distinct tuple, the largest thing the pool holds — at the live audit's scale, a dict of millions of name tuples is well over a hundred megabytes beside a list of the same pointers — and it exists only to assign ids: `seal` drops it once the columns over the pool are written, after which the pool is the id list alone, answers `__getitem__` as before, and refuses a further `id`. A pool over a vocabulary of a few dozen tuples is never sealed."""

    __slots__ = ("_tuples", "_ids", "elements")

    def __init__(self) -> None:
        self._tuples: list[tuple[_T, ...]] = [()]
        self._ids: dict[tuple[_T, ...], int] | None = {(): 0}
        self.elements = 0

    def seal(self) -> None:
        self._ids = None

    @property
    def sealed(self) -> bool:
        return self._ids is None

    def id(self, value: tuple[_T, ...]) -> int:
        if self._ids is None:
            raise ValueError("the tuple pool is sealed: its columns are written and it assigns no further id")
        found = self._ids.get(value)
        if found is None:
            found = len(self._tuples)
            self._tuples.append(value)
            self._ids[value] = found
            self.elements += len(value)
        return found

    def pooled(self, value: tuple[_T, ...]) -> tuple[_T, ...]:
        return self._tuples[self.id(value)]

    def __getitem__(self, index: int) -> tuple[_T, ...]:
        return self._tuples[index]

    def __len__(self) -> int:
        return len(self._tuples) - 1


class MappingPool:
    """One pool for every per-config class map a unit carries (`audit.Unit.config_classes`): ids in first-seen order, the empty mapping at id 0, keyed on `tuple(mapping.items())` in the mapping's own insertion order and never sorted, because that order — the order the audit states a unit's configs in — is in the shipped fragment's bytes while the content key hashes order-blind, so a pool that sorted would move `units/*.json` without moving an id. `pooled` hands back the one instance the pool holds for a value, typed `Mapping[str, str]` so that a write through it is a type error at the line that makes it rather than a value every unit sharing the instance sees; the pool's own test holds that no instance is ever mutated. A mapping whose keys or values are not strings is refused when it is first seen, since an id column could not name them. `elements` is twice the distinct mappings' entries summed — a key id and a value id an entry — and `len` their number, the empty one excluded, so `pile_tally.pool_bytes` prices the pool as it prices a tuple pool."""

    __slots__ = ("_mappings", "_ids", "elements")

    def __init__(self) -> None:
        self._mappings: list[Mapping[str, str]] = [{}]
        self._ids: dict[tuple[tuple[str, str], ...], int] = {(): 0}
        self.elements = 0

    def id(self, value: Mapping[str, str]) -> int:
        key = tuple(value.items())
        found = self._ids.get(key)
        if found is None:
            if not all(isinstance(name, str) and isinstance(member, str) for name, member in key):
                raise TypeError("a mapping pool holds string keys and string values")
            found = len(self._mappings)
            self._mappings.append(dict(key))
            self._ids[key] = found
            self.elements += 2 * len(key)
        return found

    def pooled(self, value: Mapping[str, str]) -> Mapping[str, str]:
        return self._mappings[self.id(value)]

    def __getitem__(self, index: int) -> Mapping[str, str]:
        return self._mappings[index]

    def __len__(self) -> int:
        return len(self._mappings) - 1
