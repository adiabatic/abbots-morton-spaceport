"""Diff two builds' settlement and join tables row by row for the review corpus's table-diff mode (doc/review-design.md §2.3, design §8). Rows are matched by key, removals and additions that share an input are paired into one regrouped entry, and settlement changes that move only provenance go to a `provenance-only` bucket of their own. A changed row is evidence: `engine/review/diff_units.py` turns the entries into the visible changes the corpus shows, and the bucket names are the `kinds` its units carry. The loaders read a table as run_build writes it or gzipped, as a snapshot holds it (`engine/review/snapshot.py`)."""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal, Self

# Restates `conform.SETTLEMENT_CONFIGS`, the configurations the build settles and writes tables for, because importing conform would pull the conformance sweep into the corpus build's import closure; `engine/test_review_tablediff.py` ties the two.
SETTLED_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss07")

DIFF_BUCKETS = ("added", "removed", "regrouped", "changed", "provenance-only")


@dataclass(frozen=True)
class SettlementKey:
    config: str
    input: str
    backtrack: frozenset[str] | None
    look1: frozenset[str] | None
    look2: frozenset[str] | None
    look3: frozenset[str] | None = None
    look4: frozenset[str] | None = None

    def label(self) -> str:
        def part(value: frozenset[str] | None) -> str:
            return " ".join(sorted(value)) if value is not None else "-"

        label = f"{self.input} / {part(self.backtrack)} / {part(self.look1)} / {part(self.look2)}"
        if self.look3 is not None:
            label += f" / {part(self.look3)}"
        if self.look4 is not None:
            label += f" / {part(self.look4)}"
        return label


@dataclass(frozen=True)
class SettlementValue:
    outcome: str
    joint: bool
    provenance: str


@dataclass(frozen=True)
class JoinKey:
    config: str
    left: str
    right: str

    def label(self) -> str:
        return f"{self.left} + {self.right}"


@dataclass(frozen=True)
class JoinValue:
    junction: str
    extension: int


@dataclass(kw_only=True)
class _DiffEntryBase:
    bucket: str
    config: str
    paired: tuple[Self, ...] = ()


@dataclass(kw_only=True)
class SettlementDiffEntry(_DiffEntryBase):
    table: Literal["settlement"] = "settlement"
    key: SettlementKey
    old: SettlementValue | None
    new: SettlementValue | None


@dataclass(kw_only=True)
class JoinDiffEntry(_DiffEntryBase):
    table: Literal["join"] = "join"
    key: JoinKey
    old: JoinValue | None
    new: JoinValue | None


DiffEntry = SettlementDiffEntry | JoinDiffEntry


def _parse_set(token: str) -> frozenset[str] | None:
    return None if token == "-" else frozenset(token.split(" "))


def open_table(path: Path) -> IO[str]:
    """Open a table for reading as text: gzipped when its name ends in `.gz`, as a snapshot holds it, and plain otherwise, as run_build writes it."""
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def load_outcomes(path: Path) -> list[str]:
    """Read only the outcome of each rule of a settlement table, in rule order: the cell its input settles to, the third column from the end."""
    outcomes: list[str] = []
    with open_table(path) as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("input\t") or not line.strip():
                continue
            outcomes.append(line.rstrip("\n").split("\t")[-3])
    return outcomes


def load_settlement(path: Path, config: str | None = None) -> dict[SettlementKey, SettlementValue]:
    """Read a settlement table, nine columns a row as the crate writes it (`artifacts::settlement_tsv`), into its rows, in rule order. `config` names the configuration the keys carry, which defaults to the one the file's name gives."""
    rows: dict[SettlementKey, SettlementValue] = {}
    config = config if config is not None else _config_from_path(path, "settlement-")
    with open_table(path) as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("input\t") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9:
                raise ValueError(f"{path}: a settlement row has {len(fields)} fields, not 9: {line.rstrip()}")
            input_glyph, backtrack, look1, look2, look3, look4, outcome, joint, provenance = fields
            key = SettlementKey(
                config,
                input_glyph,
                _parse_set(backtrack),
                _parse_set(look1),
                _parse_set(look2),
                _parse_set(look3),
                _parse_set(look4),
            )
            rows[key] = SettlementValue(outcome=outcome, joint=joint == "joint", provenance=provenance)
    return rows


def load_joins(path: Path, config: str | None = None) -> dict[JoinKey, JoinValue]:
    """Read a join table (`table.JOIN_COLUMNS`) into its rows. `config` is as for `load_settlement`."""
    rows: dict[JoinKey, JoinValue] = {}
    config = config if config is not None else _config_from_path(path, "joins-")
    with open_table(path) as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("left\t") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 4:
                raise ValueError(f"{path}: a join row has {len(fields)} fields, not 4: {line.rstrip()}")
            left, right, junction, extension = fields
            rows[JoinKey(config, left, right)] = JoinValue(junction, int(extension))
    return rows


def _config_from_path(path: Path, prefix: str) -> str:
    return Path(path).name.removesuffix(".gz").removesuffix(".tsv").removeprefix(prefix)


def diff_settlement(
    old: dict[SettlementKey, SettlementValue],
    new: dict[SettlementKey, SettlementValue],
    config: str,
) -> list[SettlementDiffEntry]:
    entries: list[SettlementDiffEntry] = []
    removed: list[SettlementDiffEntry] = []
    added: list[SettlementDiffEntry] = []
    for key in old:
        if key in new:
            if old[key] == new[key]:
                continue
            behavior_same = (old[key].outcome, old[key].joint) == (new[key].outcome, new[key].joint)
            entries.append(
                SettlementDiffEntry(
                    bucket="provenance-only" if behavior_same else "changed",
                    config=config,
                    key=key,
                    old=old[key],
                    new=new[key],
                )
            )
        else:
            removed.append(
                SettlementDiffEntry(bucket="removed", config=config, key=key, old=old[key], new=None)
            )
    for key in new:
        if key not in old:
            added.append(SettlementDiffEntry(bucket="added", config=config, key=key, old=None, new=new[key]))

    by_input_removed: dict[str, list[SettlementDiffEntry]] = {}
    for entry in removed:
        by_input_removed.setdefault(entry.key.input, []).append(entry)
    by_input_added: dict[str, list[SettlementDiffEntry]] = {}
    for entry in added:
        by_input_added.setdefault(entry.key.input, []).append(entry)

    paired_inputs = sorted(set(by_input_removed) & set(by_input_added))
    for input_glyph in paired_inputs:
        members = tuple(by_input_removed[input_glyph] + by_input_added[input_glyph])
        entries.append(
            SettlementDiffEntry(
                bucket="regrouped",
                config=config,
                key=members[0].key,
                old=None,
                new=None,
                paired=members,
            )
        )
    for input_glyph, group in by_input_removed.items():
        if input_glyph not in paired_inputs:
            entries.extend(group)
    for input_glyph, group in by_input_added.items():
        if input_glyph not in paired_inputs:
            entries.extend(group)
    return entries


def diff_joins(
    old: dict[JoinKey, JoinValue], new: dict[JoinKey, JoinValue], config: str
) -> list[JoinDiffEntry]:
    entries: list[JoinDiffEntry] = []
    for key in old:
        if key in new:
            if old[key] != new[key]:
                entries.append(
                    JoinDiffEntry(bucket="changed", config=config, key=key, old=old[key], new=new[key])
                )
        else:
            entries.append(JoinDiffEntry(bucket="removed", config=config, key=key, old=old[key], new=None))
    for key in new:
        if key not in old:
            entries.append(JoinDiffEntry(bucket="added", config=config, key=key, old=None, new=new[key]))
    return entries


_COMPARE_CHUNK = 1 << 20


def same_table(first: Path, second: Path) -> bool:
    """Whether two table files hold the same bytes once each is read as `open_table` reads it, gzipped or plain, compared a chunk at a time without parsing a row, so a configuration whose tables did not move costs a read and no diff."""

    def opened(path: Path):
        return gzip.open(path, "rb") if Path(path).suffix == ".gz" else open(path, "rb")

    with opened(first) as left, opened(second) as right:
        while True:
            a = left.read(_COMPARE_CHUNK)
            b = right.read(len(a) or _COMPARE_CHUNK)
            if a != b:
                return False
            if not a:
                return True
