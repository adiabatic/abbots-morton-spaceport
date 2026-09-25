"""Interning tables for the surface build's column-packed piles: `StringTable` for the strings every id column names, `TuplePool` for the name-tuple columns, and `MappingPool` for the per-config class maps. The module imports nothing from the repository, so `audit.RowColumns`, `audit.UnitTable`, and `unit_store.UnitStore` can all build on it, and the verdict chain, which imports `audit` through `status`, does not import the pile tally through it. `pile_tally.column_census` estimates the memory of a pile built on these tables.

Every table assigns ids in first-seen order and reserves id 0 for the empty value. Each keeps a running total of its members' size (UTF-8 bytes for strings, elements for tuples, two ids per entry for mappings), so the tally reports the tables exactly instead of by sampling.
"""

from __future__ import annotations

import sys
from collections.abc import Hashable, Mapping
from typing import Generic, TypeVar

_T = TypeVar("_T", bound=Hashable)


class StringTable:
    """Interning table for the strings an id column names, with the empty string at id 0. Each string goes through `sys.intern` before the lookup, so the table stores the one object that every interned copy of the name shares, including fresh copies from worker replies. `chars` is the distinct strings' UTF-8 bytes summed, and `len` counts the distinct strings without the empty one, as the tally does. `find` returns a string's existing id, or None, without assigning one."""

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
    """Interning pool for the tuples a column names (a row's kinds, a window's rendered names in either font, a unit's config set, its render groups), with the empty tuple at id 0. `pooled` returns the pool's own instance of a value, so records built from the pool share one tuple per distinct value; `id` is what a column stores. `elements` is the distinct tuples' lengths summed and `len` their count without the empty one, which is what `pile_tally.pool_bytes` estimates a packed side column from; the strings inside are counted against the string table, not here. The tuple-to-id dict is the largest part of the pool (at the live audit's scale, a dict of millions of name tuples is well over a hundred megabytes beside a list of the same pointers) and is needed only to assign ids. `seal` drops it once the columns over the pool are written; after that `__getitem__` still works and `id` raises. Pools over a vocabulary of a few dozen tuples are never sealed."""

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
    """Interning pool for the per-config class maps (`audit.Unit.config_classes`), with the empty mapping at id 0. It is keyed on `tuple(mapping.items())` in insertion order, unsorted. That order is the order the audit lists a unit's configs, and it appears in the shipped fragment's bytes, while the content key hashes the map without regard to order; a pool keyed on sorted items would change `units/*.json` without changing an id. `pooled` returns the pool's instance typed `Mapping[str, str]`, so a write through it is a type error instead of a change every unit sharing the instance sees; `rebuild/test_unit_cache.py` checks that no build, cold or served, writes through a pooled map. A mapping with a non-string key or value raises `TypeError` when first seen, since an id column could not name it. `elements` is twice the distinct mappings' entries summed (a key id and a value id per entry) and `len` their count without the empty one, so `pile_tally.pool_bytes` estimates the pool as it does a tuple pool."""

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
