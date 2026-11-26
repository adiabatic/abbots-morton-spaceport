"""The visible changes a table diff makes, and the review units that show them (rebuild/REVIEW-PLAN.md §2.3).

A settlement row is a class-compressed rule over hundreds of windows, and one join change re-folds hundreds of rules, so a changed row (`tablediff`) is evidence and never a unit. A unit is one visible change, folded across the configurations it holds under:

- A settlement unit is one cell change (`CellChange`): a letter that the accepted build settles to one cell and the new build to another, folded across the left neighbors it does so after. A cell's label already names its entry height and adjustments, so the left glyph adds nothing visible: the left glyphs each substitution was seen after (`Substitution`) are evidence the explain panel lists, not part of what the unit is.
- A join unit is one changed join row (`JoinChange`): its left and right cells, and the junction and extension each table gives the pair, None on a side whose table lacks the pair.
- A quiet unit stands for a letter whose settlement rows changed under a configuration where no candidate text shows it settled differently. It is machine-approved when every candidate draws alike in both fonts or differs only where another glyph's name differs, which is that glyph's own change; otherwise it is a human unit over the shortest text whose ink differs with every glyph name alike.
- A provenance unit stands for a letter whose rows differ only in the records they credit, and takes no verdict.

Finding the texts, one configuration at a time (`UnitFinder.add_config`): a letter whose rows changed there (by its spelling, so a `.noentry` input is its letter, `spelling`) takes as candidates the certificates of its old rules (from the snapshot) and of its new rules (from the new windows head), and every text of at most three letters, boundary tokens among them, that holds it (the input sweep, `sweep_texts`). Each candidate is shaped in both fonts with kern off under the configuration, shortest first, and every position where the glyph that starts a cluster differs gives a substitution (`substitutions`), whichever letter is there. A join row takes the shortest text whose runs hold its two cells side by side in the font of each side the row exists on, from the shaped candidates, the kerning inventory's texts, the shortest candidate prefix that settles its left cell followed by its right letter and one more token or none (`UnitFinder._extended_texts`), and a bounded search: the two letters with a letter, a ZWNJ, a space, a namer dot or nothing on each side (`pair_texts`). A row no text is found for is a unit with no text. A context that needs more than three letters can be missed; diffing the window rows themselves is the sound fix.

Folding (`UnitFinder.units`): a unit's configurations are those its one text shows its change under. Of the texts the unit's configurations found, the one that shows the change under the most of them is taken, the shortest first on a tie, and the configurations it leaves take another text the same way, so every unit's text shows its change under every configuration it lists. Then the join and settlement units that show the same thing become one (`merge`): units with the same text under the same configurations, which shape to the same runs in both fonts, as a removed join row, its added twin and the substitution at that junction do, are one unit that carries all their kinds and rows and stands in the `joins` class when any of them is a join row (`KIND_PRECEDENCE`). Units whose configurations differ stay apart.

`fragment` writes a unit as the corpus's shard JSON (rebuild/REVIEW-PLAN.md §7.2): the before and after runs, the pair band and highlight from HarfBuzz's clusters, the machine checks (`ink.InkComparator`), the cluster, a digest of the ink deltas the unit's configurations show, so units that show the same pixels share a Queue card, and the id, `unit_cache.unit_id_for` of a digest of what the unit is (`unit_identity`). Shaping is kern-neutral (`ink.kern_neutral`), as everywhere in the corpus.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rebuild.pipeline.labels import spec_alphabet
from rebuild.pipeline.model import ResolvedSpec
from rebuild.review import tablediff, unit_cache
from rebuild.review.enrich import LETTERS, letter_display, notation, notation_tokens, text_entities
from rebuild.review.ink import (
    InkComparator,
    OutlineCache,
    OutlineIntern,
    delta_digest,
    features_for,
    kern_neutral,
)
from rebuild.validation.classify import JunctionClassifier
from rebuild.validation.shaping import ShapeResult, Shaper, first_glyph_covering, last_glyph_covering

EDGE = "#EDGE"
BOUNDARY_GLYPHS = frozenset({"space", "uni200C", "periodcentered"})
BOUNDARY_KINDS = {0x0020: "space", 0x00B7: "namer-dot", 0x200C: "zwnj"}
# Letters of context around the letter a sweep text holds: with the letter itself, at most three.
SWEEP_CONTEXT = 2
# How many of a change's shortest texts the fold tries before it takes the best of them.
FOLD_CANDIDATES = 16
# How many changed rows of one letter and configuration an explain panel lists before it counts the rest, and how many labels of one context slot it writes.
EXPLAIN_ROWS = 12
EXPLAIN_SLOT_LABELS = 6
# How many of a cell change's left glyphs the explain panel names before it counts the rest.
EXPLAIN_LEFTS = 12
IDENTITY_FORMAT = "table-diff/1"

SETTLEMENT = "settlement"
QUIET = "quiet"
JOIN = "join"
PROVENANCE = "provenance"

FAMILIES = frozenset(LETTERS.values())
# The kind a substitution unit carries when its letter's own rows did not change, so the change comes from its context.
CONTEXT_KIND = "context"
KINDS = (*tablediff.DIFF_BUCKETS, CONTEXT_KIND)


@dataclass(frozen=True)
class DiffClass:
    """One table-diff class: the sidebar's kind of change."""

    id: str
    title: str
    summary: str
    why: str
    no_verdict: bool = False


JOINS_CLASS = DiffClass(
    "joins",
    "Joins",
    "Two settled cells meet differently: a pair the accepted build joins or breaks is gone, a new pair appears, or a pair's junction or extension moves.",
    "A join row is the junction and extension the tables give two adjacent cells. Each unit is one changed row with a text that holds the pair side by side in the font of each side the row exists on.",
)
SETTLEMENT_CLASS = DiffClass(
    "settlement",
    "Settlement",
    "A letter settles to a different cell than in the accepted build after the same neighbor, or its rules changed with nothing visible.",
    "Each unit is one substitution, a letter after its left neighbor settling to another cell, found by shaping the certificates of the letter's rules and every text of at most three letters that holds it in both fonts. A letter whose rules changed and that no such text shows differently is a unit of its own, machine-approved when every candidate draws alike.",
)
PROVENANCE_CLASS = DiffClass(
    "provenance-only",
    "Provenance only",
    "A letter's rules credit other records and settle as before; nothing to judge.",
    "These letters' settlement rows keep their keys, outcomes and joint flags and differ only in the records they name, so no text renders differently.",
    no_verdict=True,
)
# The classes in the sidebar's order. `glyphs` (a glyph drawn differently with the tables unchanged) and `kerning` (a changed kern value, which takes no verdict) are reserved for the glyph and kern buckets, which this build does not emit yet.
CLASSES = (JOINS_CLASS, SETTLEMENT_CLASS, PROVENANCE_CLASS)
RESERVED_CLASS_IDS = ("glyphs", "kerning")
CLASS_IDS = ("joins", "settlement", "glyphs", "kerning", "provenance-only")
NO_VERDICT_CLASS_IDS = frozenset({"kerning", "provenance-only"})
CLASS_OF_KIND = {SETTLEMENT: "settlement", QUIET: "settlement", JOIN: "joins", PROVENANCE: "provenance-only"}


def base_label(name: str) -> str:
    """A glyph or label's rune part: `qsDay_qsUtter` of `qsDay_qsUtter.full.ex-y5`, `qsTea` of `qsTea.noentry`."""
    return name.split(".", 1)[0]


def label_families(name: str) -> tuple[str, ...]:
    """The letter families a glyph or label names, a ligature's in order, none for a boundary glyph or the run edge."""
    base = base_label(name)
    if base in BOUNDARY_GLYPHS or base.startswith("#"):
        return ()
    return tuple(part for part in base.split("_") if part in FAMILIES)


def spelling(spec: ResolvedSpec, label: str) -> tuple[int, ...] | None:
    """The code points a settlement input or cell label spells: its rune's, a ligature's components', or None for a label naming no rune with code points."""
    base = base_label(label)
    rune = spec.runes.get(base)
    if rune is None:
        return None
    values: list[int] = []
    for part in rune.sequence or (base,):
        component = spec.runes.get(part)
        if component is None or component.codepoint is None:
            return None
        values.append(component.codepoint)
    return tuple(values)


def sweep_texts(alphabet: Sequence[str], spelled: str) -> Iterator[str]:
    """Every text that holds `spelled` with at most `SWEEP_CONTEXT` letters of the alphabet around it, boundary tokens among them, shortest first."""
    for total in range(SWEEP_CONTEXT + 1):
        for left in range(total + 1):
            for before in itertools.product(alphabet, repeat=left):
                for after in itertools.product(alphabet, repeat=total - left):
                    yield "".join(before) + spelled + "".join(after)


def pair_texts(alphabet: Sequence[str], left: str, right: str) -> Iterator[str]:
    """The bounded search's texts for two letters: alone, then with a letter, a ZWNJ, a space or a namer dot before, after, or on both sides, shortest first."""
    core = left + right
    yield core
    for token in alphabet:
        yield token + core
    for token in alphabet:
        yield core + token
    for before in alphabet:
        for after in alphabet:
            yield before + core + after


def _cluster_starts(result: ShapeResult, length: int) -> dict[int, tuple[int, int]]:
    """Map each code point position a cluster starts at to its first glyph and the number of code points the cluster covers."""
    starts: dict[int, tuple[int, int]] = {}
    clusters = result.clusters
    for index, cluster in enumerate(clusters):
        if cluster in starts:
            continue
        following = next((value for value in clusters[index + 1 :] if value != cluster), length)
        starts[cluster] = (index, following - cluster)
    return starts


@dataclass(frozen=True, order=True)
class Substitution:
    """One visible substitution: the rune at a position (the after glyph's, or the before glyph's when only the before font forms a ligature there), the glyph to its left in each font (`EDGE` at the run's start), and the glyph each font draws there."""

    rune: str
    left_before: str
    left_after: str
    old: str
    new: str


@dataclass(frozen=True, order=True)
class CellChange:
    """What a settlement unit is: a rune and the cell each font settles it to, whatever its left neighbor."""

    rune: str
    old: str
    new: str

    @classmethod
    def of(cls, substitution: Substitution) -> CellChange:
        return cls(substitution.rune, substitution.old, substitution.new)


def substitutions(text: str, before: ShapeResult, after: ShapeResult) -> list[tuple[int, Substitution]]:
    """Every position of `text` where both runs start a cluster and the two glyphs differ, with its substitution, in position order."""
    if before.names == after.names:
        return []
    length = len(text)
    starts_before = _cluster_starts(before, length)
    starts_after = _cluster_starts(after, length)
    found: list[tuple[int, Substitution]] = []
    for position in sorted(set(starts_before) & set(starts_after)):
        glyph_before, span_before = starts_before[position]
        glyph_after, span_after = starts_after[position]
        old, new = before.names[glyph_before], after.names[glyph_after]
        if old == new and span_before == span_after:
            continue
        rune = base_label(new if span_after >= span_before else old)
        found.append(
            (
                position,
                Substitution(
                    rune,
                    before.names[glyph_before - 1] if glyph_before else EDGE,
                    after.names[glyph_after - 1] if glyph_after else EDGE,
                    old,
                    new,
                ),
            )
        )
    return found


def _pairs(result: ShapeResult) -> set[tuple[str, str]]:
    return set(zip(result.names, result.names[1:]))


JoinValueTuple = tuple[str, int]


@dataclass(frozen=True, order=True)
class JoinChange:
    """One changed join row: the two cells and the (junction, extension) each table gives them, None on the side whose table lacks the pair."""

    left: str
    right: str
    old: JoinValueTuple | None
    new: JoinValueTuple | None

    @property
    def bucket(self) -> str:
        if self.old is None:
            return "added"
        if self.new is None:
            return "removed"
        return "changed"


def _join_value(value: tablediff.JoinValue | None) -> JoinValueTuple | None:
    return None if value is None else (value.junction, value.extension)


def join_value_text(value: JoinValueTuple | None) -> str:
    if value is None:
        return "no such pair"
    junction, extension = value
    if junction == "break":
        return "a break"
    return f"a join at the {junction}" + (f", extended {extension}" if extension else "")


class Fonts:
    """The two fonts a diff shapes in, the snapshot's (`before`) and the new build's (`after`), with their outlines, junction classifiers and ink comparator. `runs` shapes one text in both under one configuration, kern-neutral, and counts what it shaped."""

    def __init__(self, before: Path, after: Path) -> None:
        self.paths = (Path(before), Path(after))
        self.before = Shaper(before)
        self.after = Shaper(after)
        intern = OutlineIntern()
        self._outlines = (OutlineCache(before, intern), OutlineCache(after, intern))
        self._alike: dict[str, bool] = {}
        self.comparator = InkComparator(before, after)
        self.classifiers = (_classifier(before), _classifier(after))
        self.shaped = 0
        self.seconds = 0.0

    def runs(self, text: str, config: str) -> tuple[ShapeResult, ShapeResult]:
        features = kern_neutral(features_for(config))
        start = time.perf_counter()
        runs = self.before.shape(text, features), self.after.shape(text, features)
        self.seconds += time.perf_counter() - start
        self.shaped += 2
        return runs

    def drawn_alike(self, name: str) -> bool:
        """Whether a glyph both fonts have draws the same outline at the same origin in both."""
        alike = self._alike.get(name)
        if alike is None:
            alike = self._alike[name] = self._outlines[0].shape_key(name) == self._outlines[1].shape_key(name)
        return alike

    def runs_alike(self, before: ShapeResult, after: ShapeResult) -> bool:
        """Whether two runs of one text with the same glyph names put down the same ink: the same placements and every glyph drawn alike."""
        return before.positions == after.positions and all(self.drawn_alike(name) for name in before.names)


def _classifier(path: Path) -> JunctionClassifier | None:
    try:
        return JunctionClassifier(path)
    except ValueError, KeyError, AttributeError:
        return None


@dataclass
class _Shortest:
    """The shortest texts one change was seen in under one configuration, and their length."""

    length: int
    texts: set[str]

    def offer(self, text: str) -> None:
        if len(text) < self.length:
            self.length, self.texts = len(text), {text}
        elif len(text) == self.length:
            self.texts.add(text)


def _offer(table: dict, key, config: str, text: str) -> None:
    per_config = table.setdefault(key, {})
    shortest = per_config.get(config)
    if shortest is None:
        per_config[config] = _Shortest(len(text), {text})
    else:
        shortest.offer(text)


@dataclass(frozen=True)
class Unit:
    """One review unit before it is written: its kind (`SETTLEMENT`, `QUIET`, `JOIN` or `PROVENANCE`), what it is (`subject`, a `CellChange`, a `JoinChange` or a rune name), the configurations it holds under in the universe's order, its text (None for a join row no text was found for), its `kinds`, how the text was found, and the changed rows it stands for, by configuration. A settlement unit also carries the (before, after) left glyphs its substitutions were seen after (`lefts`) and the after font's left glyph in its own text (`left`). A unit `merge` made of several carries them as `members`, the first of which, by `KIND_PRECEDENCE`, gives its kind and subject."""

    kind: str
    subject: CellChange | JoinChange | str
    configs: tuple[str, ...]
    text: str | None
    kinds: tuple[str, ...]
    found_by: str
    rows: Mapping[str, tuple[tablediff.DiffEntry, ...]] = field(default_factory=dict)
    unattributed: bool = False
    lefts: tuple[tuple[str, str], ...] = ()
    left: str | None = None
    members: tuple[Unit, ...] = ()

    @property
    def parts(self) -> tuple[Unit, ...]:
        """The units this one shows: its members, or itself."""
        return self.members or (self,)


# The order a merged unit's members take, whose first gives the unit its kind and class: a join row before a substitution.
KIND_PRECEDENCE = {JOIN: 0, SETTLEMENT: 1}
MERGED_KINDS = frozenset(KIND_PRECEDENCE)


def _entry_key(entry: tablediff.DiffEntry) -> tuple[str, str, str]:
    return (entry.table, entry.bucket, entry.key.label())


def merge(units: Sequence[Unit]) -> list[Unit]:
    """Make the join and settlement units with the same text under the same configurations one unit each: the same text shaped under the same configurations gives the same runs in both fonts, so they show the same thing. The merged unit's members are sorted by `KIND_PRECEDENCE` and what they are, it takes the first's kind, subject and how its text was found, and it carries every member's kinds and rows. A unit with no text, a quiet unit and a provenance unit are never merged, and units whose configurations differ stay apart."""
    groups: dict[tuple, list[Unit]] = {}
    for index, unit in enumerate(units):
        key = (unit.text, unit.configs) if unit.text is not None and unit.kind in MERGED_KINDS else (index,)
        groups.setdefault(key, []).append(unit)
    merged: list[Unit] = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        group.sort(key=lambda unit: (KIND_PRECEDENCE[unit.kind], _subject_key(unit.subject)))
        rows: dict[str, dict[tuple[str, str, str], tablediff.DiffEntry]] = {}
        for unit in group:
            for config, entries in unit.rows.items():
                for entry in entries:
                    rows.setdefault(config, {}).setdefault(_entry_key(entry), entry)
        primary = group[0]
        merged.append(
            Unit(
                primary.kind,
                primary.subject,
                primary.configs,
                primary.text,
                tuple(sorted({kind for unit in group for kind in unit.kinds})),
                primary.found_by,
                {config: tuple(entries.values()) for config, entries in rows.items()},
                left=next((unit.left for unit in group if unit.left is not None), None),
                members=tuple(group),
            )
        )
    return merged


def _subject_key(subject: CellChange | JoinChange | str) -> list:
    """What a unit is, as its id and the merge order read it."""
    if isinstance(subject, CellChange):
        return [SETTLEMENT, subject.rune, subject.old, subject.new]
    if isinstance(subject, JoinChange):
        return [JOIN, subject.left, subject.right, subject.old, subject.new]
    return ["letter", subject]


@dataclass
class _QuietRecord:
    unattributed: str | None = None
    quiet_certificate: str | None = None
    quiet: str | None = None
    certificate: str | None = None


@dataclass
class ConfigCounts:
    """What one configuration's diff held and cost: changed rows by table and bucket (a regrouped entry counts each row it pairs), the letters whose rows changed, the texts shaped in both fonts and the seconds spent."""

    settlement: dict[str, int] = field(default_factory=dict)
    joins: dict[str, int] = field(default_factory=dict)
    letters: int = 0
    texts: int = 0
    seconds: float = 0.0

    def record(self) -> dict:
        return {
            "settlement": dict(sorted(self.settlement.items())),
            "joins": dict(sorted(self.joins.items())),
            "letters": self.letters,
            "texts": self.texts,
        }


class UnitFinder:
    """Collect one diff's visible changes a configuration at a time (`add_config`) and fold them into units (`units`). `universe` is every configuration the corpus can list, in the order units list theirs. `inventory` maps a configuration to the kerning inventory's texts under it, each as its text and its glyph names in the new font."""

    def __init__(
        self,
        spec: ResolvedSpec,
        fonts: Fonts,
        universe: Sequence[str],
        inventory: Mapping[str, Sequence[tuple[str, tuple[str, ...]]]] | None = None,
    ) -> None:
        self.spec = spec
        self.fonts = fonts
        self.universe = tuple(universe)
        self.alphabet = spec_alphabet(spec)
        self.inventory = inventory or {}
        self.counts: dict[str, ConfigCounts] = {}
        self._substitutions: dict[CellChange, dict[str, _Shortest]] = {}
        self._lefts: dict[CellChange, set[tuple[str, str]]] = {}
        self._quiet: dict[str, dict[str, _QuietRecord]] = {}
        self._provenance: dict[str, dict[str, str | None]] = {}
        self._joins: dict[JoinChange, dict[str, tuple[str | None, str]]] = {}
        self._rows: dict[str, dict[str, list[tablediff.DiffEntry]]] = {}
        self._bucket_kinds: dict[str, dict[str, set[str]]] = {}
        self._join_rows: dict[JoinChange, dict[str, tablediff.JoinDiffEntry]] = {}
        self._shows: dict[tuple[str, str], list[Substitution]] = {}
        self._holds: dict[tuple[str, str], tuple[set, set]] = {}

    def _spelled(self, label: str) -> str | None:
        values = spelling(self.spec, label)
        return None if values is None else "".join(map(chr, values))

    def add_config(
        self,
        config: str,
        settlement: Sequence[tablediff.SettlementDiffEntry],
        joins: Sequence[tablediff.JoinDiffEntry],
        old_certificates: Iterable[tuple[str, tuple[int, ...] | None]] = (),
        new_certificates: Iterable[tuple[str, tuple[int, ...] | None]] = (),
    ) -> ConfigCounts:
        """Find the changes of one configuration's row diff. `old_certificates` and `new_certificates` are each table's (input label, certificate text) pairs, one per rule."""
        start = time.perf_counter()
        shaped = self.fonts.shaped
        counts = self.counts.setdefault(config, ConfigCounts())
        for entry in settlement:
            counts.settlement[entry.bucket] = counts.settlement.get(entry.bucket, 0) + max(
                len(entry.paired), 1
            )
        for entry in joins:
            counts.joins[entry.bucket] = counts.joins.get(entry.bucket, 0) + 1

        changed: dict[str, set[str]] = {}
        provenance: set[str] = set()
        for entry in settlement:
            rune = base_label(entry.key.input)
            self._rows.setdefault(rune, {}).setdefault(config, []).append(entry)
            if entry.bucket == "provenance-only":
                provenance.add(rune)
            else:
                changed.setdefault(rune, set()).add(entry.bucket)
        for rune, buckets in changed.items():
            self._bucket_kinds.setdefault(rune, {})[config] = buckets
        counts.letters = len(changed)

        certificates: dict[str, set[str]] = {}
        new_shortest: dict[str, str] = {}
        for side, pairs in (("old", old_certificates), ("new", new_certificates)):
            for label, values in pairs:
                if not values:
                    continue
                rune = base_label(label)
                text = "".join(map(chr, values))
                if rune in changed:
                    certificates.setdefault(rune, set()).add(text)
                if side == "new" and rune in provenance:
                    best = new_shortest.get(rune)
                    if best is None or (len(text), text) < (len(best), best):
                        new_shortest[rune] = text

        words = {rune: word for rune in changed if (word := self._spelled(rune)) is not None}
        texts: set[str] = set()
        owners: dict[str, set[str]] = {}
        for rune, values in certificates.items():
            texts.update(values)
            for text in values:
                owners.setdefault(text, set()).add(rune)
        for word in words.values():
            texts.update(sweep_texts(self.alphabet, word))

        wanted_before = {(entry.key.left, entry.key.right) for entry in joins if entry.old is not None}
        wanted_after = {(entry.key.left, entry.key.right) for entry in joins if entry.new is not None}
        seen_before: dict[tuple[str, str], str] = {}
        seen_after: dict[tuple[str, str], str] = {}
        seen_both: dict[tuple[str, str], str] = {}
        lefts = ({left for left, _right in wanted_before}, {left for left, _right in wanted_after})
        prefixes: tuple[dict[str, str], dict[str, str]] = ({}, {})

        quiet = {rune: _QuietRecord() for rune in changed}
        pending_quiet = set(words)
        substituted: set[str] = set()
        for rune, values in certificates.items():
            quiet[rune].certificate = min(values, key=lambda text: (len(text), text))

        for text in sorted(texts, key=lambda text: (len(text), text)):
            before, after = self.fonts.runs(text, config)
            found = substitutions(text, before, after)
            for _position, substitution in found:
                cell = CellChange.of(substitution)
                _offer(self._substitutions, cell, config, text)
                self._lefts.setdefault(cell, set()).add((substitution.left_before, substitution.left_after))
                substituted.add(base_label(substitution.old))
                substituted.add(base_label(substitution.new))
            if before.names == after.names:
                if self.fonts.runs_alike(before, after):
                    for rune in owners.get(text, ()):
                        if quiet[rune].quiet_certificate is None:
                            quiet[rune].quiet_certificate = text
                    for rune in [rune for rune in pending_quiet if words[rune] in text]:
                        quiet[rune].quiet = text
                        pending_quiet.discard(rune)
                else:
                    for rune, word in words.items():
                        if quiet[rune].unattributed is None and word in text:
                            quiet[rune].unattributed = text
            for side, run in enumerate((before, after)):
                for index, name in enumerate(run.names):
                    if name in lefts[side]:
                        prefix = text[: _glyph_span(run, index, len(text))[1] + 1]
                        known = prefixes[side].get(name)
                        if known is None or (len(prefix), prefix) < (len(known), known):
                            prefixes[side][name] = prefix
            if wanted_before or wanted_after:
                pairs_before = _pairs(before) if wanted_before else set()
                pairs_after = _pairs(after) if wanted_after else set()
                for pair in pairs_before & wanted_before:
                    seen_before.setdefault(pair, text)
                for pair in pairs_after & wanted_after:
                    seen_after.setdefault(pair, text)
                for pair in pairs_before & pairs_after:
                    seen_both.setdefault(pair, text)

        for rune in changed:
            if rune in substituted:
                continue
            self._quiet.setdefault(rune, {})[config] = quiet[rune]
        for rune in sorted(provenance - set(changed)):
            self._provenance.setdefault(rune, {})[config] = new_shortest.get(rune) or self._spelled(rune)

        inventory_pairs = self._inventory_pairs(config, wanted_after)
        for entry in joins:
            change = JoinChange(
                entry.key.left, entry.key.right, _join_value(entry.old), _join_value(entry.new)
            )
            self._join_rows.setdefault(change, {})[config] = entry
            pair = (entry.key.left, entry.key.right)
            if change.old is not None and change.new is not None:
                found_text, source = seen_both.get(pair), "a candidate text"
            elif change.new is not None:
                found_text, source = seen_after.get(pair), "a candidate text"
            else:
                found_text, source = seen_before.get(pair), "a candidate text"
            extended = self._extended_texts(change, prefixes)
            for text, origin in (
                *(((listed,), "the kerning inventory") for listed in [inventory_pairs.get(pair)] if listed),
                (extended, "a text that settles the left cell, then the right letter"),
            ):
                for candidate in text:
                    if found_text is not None and (len(candidate), candidate) >= (
                        len(found_text),
                        found_text,
                    ):
                        break
                    if self._pair_holds(candidate, config, change):
                        found_text, source = candidate, origin
                        break
            searched = self._search_pair(change, config, None if found_text is None else len(found_text))
            if searched is not None:
                found_text, source = searched, "the bounded pair search"
            self._joins.setdefault(change, {})[config] = (found_text, source)

        counts.seconds += time.perf_counter() - start
        counts.texts += (self.fonts.shaped - shaped) // 2
        return counts

    def _inventory_pairs(self, config: str, wanted: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
        best: dict[tuple[str, str], str] = {}
        if not wanted:
            return best
        for text, names in self.inventory.get(config, ()):
            for pair in set(zip(names, names[1:])) & wanted:
                current = best.get(pair)
                if current is None or (len(text), text) < (len(current), current):
                    best[pair] = text
        return best

    def _pair_holds(self, text: str, config: str, change: JoinChange) -> bool:
        """Whether `text`'s runs under `config` hold the change's two cells side by side in the before font where the old table has the row, and in the after font where the new one does."""
        key = (text, config)
        held = self._holds.get(key)
        if held is None:
            before, after = self.fonts.runs(text, config)
            held = self._holds[key] = (_pairs(before), _pairs(after))
        pair = (change.left, change.right)
        return (change.old is None or pair in held[0]) and (change.new is None or pair in held[1])

    def _extended_texts(
        self, change: JoinChange, prefixes: tuple[Mapping[str, str], Mapping[str, str]]
    ) -> list[str]:
        """Texts that put the change's right letter, then nothing or one more token, after the shortest candidate prefix that ends in its left cell in the font of each side the row exists on, shortest first. They reach a left cell the bounded search's one token of context cannot, a ligature's or one settled by a longer word."""
        right = self._spelled(change.right)
        if right is None:
            return []
        starts = {
            prefix
            for side, value in ((0, change.old), (1, change.new))
            if value is not None
            for prefix in [prefixes[side].get(change.left)]
            if prefix is not None
        }
        texts = {start + right + token for start in starts for token in ("", *self.alphabet)}
        return sorted(texts, key=lambda text: (len(text), text))

    def _search_pair(self, change: JoinChange, config: str, shorter_than: int | None) -> str | None:
        """The first bounded-search text, shortest first, that holds the change's pair and is shorter than `shorter_than`, or None."""
        left, right = self._spelled(change.left), self._spelled(change.right)
        if left is None or right is None:
            return None
        for text in pair_texts(self.alphabet, left, right):
            if shorter_than is not None and len(text) >= shorter_than:
                return None
            if self._pair_holds(text, config, change):
                return text
        return None

    def _substitutions_of(self, text: str, config: str) -> list[Substitution]:
        key = (text, config)
        found = self._shows.get(key)
        if found is None:
            before, after = self.fonts.runs(text, config)
            found = self._shows[key] = [
                substitution for _position, substitution in substitutions(text, before, after)
            ]
        return found

    def _ordered(self, configs: Iterable[str]) -> tuple[str, ...]:
        chosen = set(configs)
        return tuple(config for config in self.universe if config in chosen) + tuple(
            sorted(chosen - set(self.universe))
        )

    def _cover(self, per_config: Mapping[str, set[str]], shows) -> Iterator[tuple[str, frozenset[str]]]:
        """Fold one change's texts across configurations: yield (text, the configurations it shows the change under) until every configuration in `per_config` is covered. `shows(text, config)` answers for a configuration whose own shortest texts do not hold the text."""
        remaining = set(per_config)
        while remaining:
            candidates = sorted(
                {text for config in remaining for text in per_config[config]},
                key=lambda text: (len(text), text),
            )[:FOLD_CANDIDATES]
            best: tuple[str, frozenset[str]] | None = None
            for text in candidates:
                covered = frozenset(
                    config for config in remaining if text in per_config[config] or shows(text, config)
                )
                if best is None or len(covered) > len(best[1]):
                    best = (text, covered)
            assert best is not None and best[1]
            remaining -= best[1]
            yield best

    def _rows_of(
        self, runes: Iterable[str], configs: Iterable[str]
    ) -> dict[str, tuple[tablediff.DiffEntry, ...]]:
        rows: dict[str, tuple[tablediff.DiffEntry, ...]] = {}
        for config in configs:
            entries = [
                entry for rune in dict.fromkeys(runes) for entry in self._rows.get(rune, {}).get(config, ())
            ]
            if entries:
                rows[config] = tuple(entries)
        return rows

    def _kinds_of(self, runes: Iterable[str], configs: Iterable[str]) -> tuple[str, ...]:
        kinds = {
            bucket
            for rune in runes
            for config in configs
            for bucket in self._bucket_kinds.get(rune, {}).get(config, ())
        }
        return tuple(sorted(kinds)) or (CONTEXT_KIND,)

    def units(self) -> list[Unit]:
        """Fold every change found into units, in a fixed order."""
        units: list[Unit] = []
        for cell in sorted(self._substitutions):
            per_config = {config: shortest.texts for config, shortest in self._substitutions[cell].items()}
            runes = (base_label(cell.old), base_label(cell.new))
            for text, covered in self._cover(
                per_config,
                lambda text, config, wanted=cell: any(
                    CellChange.of(substitution) == wanted
                    for substitution in self._substitutions_of(text, config)
                ),
            ):
                configs = self._ordered(covered)
                units.append(
                    Unit(
                        SETTLEMENT,
                        cell,
                        configs,
                        text,
                        self._kinds_of(runes, configs),
                        "the shortest candidate text that shows it",
                        self._rows_of(runes, configs),
                        lefts=tuple(sorted(self._lefts[cell])),
                        left=next(
                            substitution.left_after
                            for substitution in self._substitutions_of(text, configs[0])
                            if CellChange.of(substitution) == cell
                        ),
                    )
                )
        for rune in sorted(self._quiet):
            groups: dict[tuple[bool, str | None], list[str]] = {}
            for config, record in self._quiet[rune].items():
                if record.unattributed is not None:
                    key = (True, record.unattributed)
                else:
                    key = (
                        False,
                        record.quiet_certificate or record.quiet or record.certificate or self._spelled(rune),
                    )
                groups.setdefault(key, []).append(config)
            for (unattributed, text), configs in sorted(
                groups.items(), key=lambda item: (item[0][0], item[0][1] or "")
            ):
                ordered = self._ordered(configs)
                units.append(
                    Unit(
                        QUIET,
                        rune,
                        ordered,
                        text,
                        self._kinds_of((rune,), ordered),
                        (
                            "the shortest text whose ink differs with every glyph name alike"
                            if unattributed
                            else "the shortest candidate that draws alike in both fonts"
                        ),
                        self._rows_of((rune,), ordered),
                        unattributed,
                    )
                )
        for rune in sorted(self._provenance):
            groups_by_text: dict[str | None, list[str]] = {}
            for config, text in self._provenance[rune].items():
                groups_by_text.setdefault(text, []).append(config)
            for text, configs in sorted(groups_by_text.items(), key=lambda item: item[0] or ""):
                ordered = self._ordered(configs)
                units.append(
                    Unit(
                        PROVENANCE,
                        rune,
                        ordered,
                        text,
                        ("provenance-only",),
                        "the shortest certificate of its new rules",
                        self._rows_of((rune,), ordered),
                    )
                )
        for change in sorted(self._joins, key=_join_sort_key):
            found = self._joins[change]
            with_text = {config: {text} for config, (text, _source) in found.items() if text is not None}
            sources = {text: source for text, source in found.values() if text is not None}
            entries = self._join_rows[change]
            for text, covered in self._cover(
                with_text, lambda text, config, wanted=change: self._pair_holds(text, config, wanted)
            ):
                configs = self._ordered(covered)
                units.append(
                    Unit(
                        JOIN,
                        change,
                        configs,
                        text,
                        (change.bucket,),
                        sources.get(text, "a candidate text"),
                        {config: (entries[config],) for config in configs},
                    )
                )
            missing = self._ordered(config for config, (text, _source) in found.items() if text is None)
            if missing:
                units.append(
                    Unit(
                        JOIN,
                        change,
                        missing,
                        None,
                        (change.bucket,),
                        "no text: neither a candidate nor the bounded search holds the pair",
                        {config: (entries[config],) for config in missing},
                    )
                )
        return merge(units)


def _join_sort_key(change: JoinChange) -> tuple:
    return (change.left, change.right, repr(change.old), repr(change.new))


# --- writing a unit ------------------------------------------------------------------


_ADJUSTMENT = re.compile(r"\.(en|ex)-(ext|con)-(\d+)")


def _adjustment(name: str, side: str) -> int:
    total = 0
    for match in _ADJUSTMENT.finditer(name):
        if match.group(1) == side:
            total += int(match.group(3)) * (1 if match.group(2) == "ext" else -1)
    return total


def _junctions(result: ShapeResult, classifier: JunctionClassifier | None) -> list[str]:
    junctions: list[str] = []
    for left, right in zip(result.names, result.names[1:]):
        token = classifier.classify(left, right) if classifier is not None else "break"
        junctions.append(token.split("+", 1)[0])
    return junctions


def _pens(result: ShapeResult) -> list[int]:
    pens = [0]
    for _x_offset, _y_offset, advance in result.positions:
        pens.append(pens[-1] + advance)
    return pens


def _rect(result: ShapeResult, start: int, end: int) -> dict[str, int]:
    pens = _pens(result)
    first = first_glyph_covering(result.clusters, start)
    last = last_glyph_covering(result.clusters, end)
    return {"x_min": pens[first], "x_max": pens[last + 1], "advance_total": pens[-1]}


def _glyph_span(result: ShapeResult, glyph: int, length: int) -> tuple[int, int]:
    """The code point positions, first and last, of the cluster `glyph` belongs to."""
    cluster = result.clusters[glyph]
    following = next((value for value in result.clusters[glyph + 1 :] if value != cluster), length)
    return cluster, following - 1


def _focus(unit: Unit, text: str, before: ShapeResult, after: ShapeResult) -> tuple[int, int] | None:
    """The code point span of the change a unit shows in its text: the cell change's cluster, the join row's two cells, or the letter a quiet or provenance unit stands for; a merged unit's is its first member's."""
    length = len(text)
    if isinstance(unit.subject, CellChange):
        for position, substitution in substitutions(text, before, after):
            if CellChange.of(substitution) == unit.subject:
                index = _cluster_starts(after, length)[position][0]
                return _glyph_span(after, index, length)
        return None
    if isinstance(unit.subject, JoinChange):
        pair = (unit.subject.left, unit.subject.right)
        run = after if unit.subject.new is not None else before
        for index, adjacent in enumerate(zip(run.names, run.names[1:])):
            if adjacent == pair:
                return _glyph_span(run, index, length)[0], _glyph_span(run, index + 1, length)[1]
        return None
    spelled = spelling_text(unit.subject)
    if spelled and spelled in text:
        start = text.index(spelled)
        return start, start + len(spelled) - 1
    return None


_SPELLED: dict[str, str] = {}


def spelling_text(rune: str) -> str:
    """A rune name's letters as text, from the family code points (a ligature's from its components)."""
    found = _SPELLED.get(rune)
    if found is None:
        codepoints = {name: value for value, name in LETTERS.items()}
        found = _SPELLED[rune] = "".join(
            chr(codepoints[part]) for part in rune.split("_") if part in codepoints
        )
    return found


def _pair_of(after: ShapeResult, focus: tuple[int, int], kind: str) -> tuple[int, int] | None:
    """The after run's glyph indices the pair band covers: a join's two cells, or a letter and its left neighbor (its right one at the run's start)."""
    left = first_glyph_covering(after.clusters, focus[0])
    right = last_glyph_covering(after.clusters, focus[1])
    if right > left:
        return left, right
    if kind == JOIN:
        return None
    if left > 0:
        return left - 1, left
    if left + 1 < len(after.names):
        return left, left + 1
    return None


def _group(unit: Unit) -> str:
    """The unit's family pair as the app's group folds and family filter read it: the two families that meet at a join, or at a cell change in the unit's own text, or the letter alone after a boundary or the run's start; a merged unit's is its first member's."""
    subject = unit.subject
    if isinstance(subject, JoinChange):
        left, right = label_families(subject.left), label_families(subject.right)
        parts = ([left[-1]] if left else []) + ([right[0]] if right else [])
    elif isinstance(subject, CellChange):
        left = label_families(unit.left or EDGE)
        right = label_families(subject.new) or label_families(subject.old)
        parts = ([left[-1]] if left else []) + ([right[0]] if right else [])
    else:
        parts = list(label_families(subject)[:1])
    return ":".join(parts) or base_label(str(getattr(subject, "rune", subject)))


def join_value_label(value: JoinValueTuple | None) -> str:
    """A join value as the row key writes it: the junction and its signed extension (`x-height+1`), or `none` for a side without the pair."""
    return "none" if value is None else f"{value[0]}{value[1]:+d}"


def _part_label(unit: Unit) -> str:
    subject = unit.subject
    if isinstance(subject, CellChange):
        return f"settlement {subject.rune}: {subject.old} -> {subject.new}"
    if isinstance(subject, JoinChange):
        return f"join {subject.left} + {subject.right}: {join_value_label(subject.old)} -> {join_value_label(subject.new)}"
    return f"{unit.kind} {subject}"


def _label(unit: Unit) -> str:
    """The unit's row key: what it is, in the tables' own names, each member's in turn for a merged unit, joined by `; `."""
    return "; ".join(_part_label(part) for part in unit.parts)


def _display(name: str) -> str:
    families = label_families(name)
    if not families:
        return {
            "#EDGE": "the start of the text",
            "space": "a space",
            "uni200C": "a ZWNJ",
            "periodcentered": "a namer dot",
        }.get(base_label(name), name)
    return "+".join(
        letter_display(family) if index == 0 else letter_display(family)[1:]
        for index, family in enumerate(families)
    )


def _summary(unit: Unit) -> str:
    """The unit's one line: its first member's change, and for a merged unit how many changes the picture shows."""
    line = _part_summary(unit)
    if len(unit.parts) > 1:
        joins = sum(1 for part in unit.parts if part.kind == JOIN)
        cells = len(unit.parts) - joins
        counted = [
            f"{count} {noun}{'' if count == 1 else 's'}"
            for count, noun in ((joins, "join row"), (cells, "cell change"))
            if count
        ]
        line += f" (one picture for {' and '.join(counted)})"
    return line


def _left_phrase(lefts: Sequence[tuple[str, str]]) -> str:
    names = sorted({left for _before, left in lefts})
    return f"after {len(names)} left glyph{'' if len(names) == 1 else 's'}"


def _part_summary(unit: Unit) -> str:
    subject = unit.subject
    if isinstance(subject, CellChange):
        return (
            f"{_display(subject.new)} settles to {subject.new}, where the accepted build settles it to "
            f"{subject.old}, {_left_phrase(unit.lefts)}"
        )
    if isinstance(subject, JoinChange):
        return (
            f"{_display(subject.left)} meets {_display(subject.right)} ({subject.left} + {subject.right}) in "
            f"{join_value_text(subject.new)}, where the accepted build has {join_value_text(subject.old)}"
        )
    if unit.kind == PROVENANCE:
        return f"{_display(subject)}'s rules credit other records and settle as before"
    if unit.unattributed:
        return f"{_display(subject)}'s rules changed, and this text draws differently with every glyph name alike"
    return f"{_display(subject)}'s rules changed, and no candidate text shows it settled differently"


def _slot(values: frozenset[str] | None) -> str:
    """One context slot of a settlement row as the explain panel writes it: `-` for any label, else its labels sorted, cut to `EXPLAIN_SLOT_LABELS` with a count of the rest."""
    if values is None:
        return "-"
    labels = sorted(values)
    shown = " ".join(labels[:EXPLAIN_SLOT_LABELS])
    return shown if len(labels) <= EXPLAIN_SLOT_LABELS else f"{shown} … ({len(labels)} in all)"


def _row_label(key: tablediff.SettlementKey) -> str:
    slots = [key.backtrack, key.look1, key.look2] + [
        slot for slot in (key.look3, key.look4) if slot is not None
    ]
    return " / ".join([key.input, *(_slot(slot) for slot in slots)])


def _shows_cell_change(
    member: tablediff.SettlementDiffEntry, cell: CellChange, lefts: frozenset[str]
) -> bool:
    """Whether a settlement row is one a cell change can come from: it settles to one of the two cells, and its backtrack holds one of the change's left glyphs or any label."""
    outcomes = {value.outcome for value in (member.old, member.new) if value is not None}
    backtrack = member.key.backtrack
    return bool(outcomes & {cell.old, cell.new}) and (backtrack is None or bool(backtrack & lefts))


def _entry_lines(
    entry: tablediff.DiffEntry, cell: CellChange | None = None, lefts: frozenset[str] = frozenset()
) -> Iterator[str]:
    if isinstance(entry, tablediff.JoinDiffEntry):
        yield f"    {entry.bucket:<10} {entry.key.label()}: {join_value_text(_join_value(entry.old))} -> {join_value_text(_join_value(entry.new))}"
        return
    for member in entry.paired or (entry,):
        if cell is not None and not _shows_cell_change(member, cell, lefts):
            continue
        old = "-" if member.old is None else member.old.outcome + (" [joint]" if member.old.joint else "")
        new = "-" if member.new is None else member.new.outcome + (" [joint]" if member.new.joint else "")
        yield f"    {entry.bucket:<10} {_row_label(member.key)}: {old} -> {new}"


def _explain(unit: Unit) -> str:
    """The explain panel's text: the unit's class and kinds, its configurations and its text, then each change it shows with the changed rows it stands for, each configuration's list once for every configuration that has the same one. A cell change names the left glyphs it was seen after, cut to `EXPLAIN_LEFTS`, and lists the rows that settle to one of its two cells after one of them."""
    lines = [f"{CLASS_OF_KIND[unit.kind]} ({', '.join(unit.kinds)}): {_summary(unit)}."]
    lines.append(f"  configurations: {', '.join(unit.configs)}")
    lines.append(f"  text: {notation(tuple(map(ord, unit.text))) if unit.text else 'none'} ({unit.found_by})")
    for part in unit.parts:
        subject = part.subject
        lines.append(f"{_part_label(part)}:")
        cell: CellChange | None = None
        lefts: frozenset[str] = frozenset()
        if isinstance(subject, CellChange):
            cell = subject
            lefts = frozenset(name for pair in part.lefts for name in pair)
            names = sorted({left for _before, left in part.lefts})
            shown = ", ".join(names[:EXPLAIN_LEFTS])
            more = f" … ({len(names)} in all)" if len(names) > EXPLAIN_LEFTS else ""
            lines.append(f"  seen after: {shown}{more}")
        elif isinstance(subject, JoinChange):
            lines.append(f"  before: {join_value_text(subject.old)}")
            lines.append(f"  after:  {join_value_text(subject.new)}")
        listed: dict[tuple[str, ...], list[str]] = {}
        for config, entries in part.rows.items():
            rows = [line for entry in entries for line in _entry_lines(entry, cell, lefts)]
            if cell is not None and not rows:
                rows = [line for entry in entries for line in _entry_lines(entry)]
            listed.setdefault(tuple(rows), []).append(config)
        if listed:
            lines.append("  changed rows:")
            for rows, configs in listed.items():
                lines.append(f"  {', '.join(configs)}:")
                lines.extend(rows[:EXPLAIN_ROWS])
                if len(rows) > EXPLAIN_ROWS:
                    lines.append(f"    … and {len(rows) - EXPLAIN_ROWS} more")
    return "\n".join(lines)


def _provenance(unit: Unit) -> list[str]:
    """The records the unit's rows credit: every pointer on the changed rows whose outcome is a cell a cell change shows, or on every changed row of a quiet or provenance unit, over every member of a merged unit."""
    pointers: set[str] = set()
    for part in unit.parts:
        wanted = {part.subject.old, part.subject.new} if isinstance(part.subject, CellChange) else None
        for entries in part.rows.values():
            for entry in entries:
                if isinstance(entry, tablediff.JoinDiffEntry):
                    continue
                for member in entry.paired or (entry,):
                    for value in (member.old, member.new):
                        if value is None or (wanted is not None and value.outcome not in wanted):
                            continue
                        pointers.update(
                            pointer.strip() for pointer in value.provenance.split(";") if pointer.strip()
                        )
    return sorted(pointers)


def unit_identity(
    unit: Unit, codepoints: str | None, before_names: Sequence[str], after_names: Sequence[str]
) -> str:
    """The sha256 a table-diff unit's id is made from: `IDENTITY_FORMAT`, the set of its members' kinds, what each member is with its old and new values (`_subject_key`), its configurations, its code points, and the glyph names of its text's runs under its first configuration in each font. The left glyphs a cell change was seen after, how its text was found and the ink are left out, so an edit that redraws a glyph or adds a left neighbor changes no unit's id."""
    content = [
        IDENTITY_FORMAT,
        sorted({part.kind for part in unit.parts}),
        sorted(_subject_key(part.subject) for part in unit.parts),
        list(unit.configs),
        codepoints,
        list(before_names),
        list(after_names),
    ]
    return hashlib.sha256(json.dumps(content, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def cluster_id(digests: Iterable[str]) -> str:
    """A Queue card's id: `c-` and the first 8 hex digits of the sha1 of the sorted distinct ink-delta digests, so units that show the same pixels share a card whatever their configurations or kind."""
    return "c-" + hashlib.sha1(repr(tuple(sorted(set(digests)))).encode()).hexdigest()[:8]


def structural_cluster(unit: Unit) -> str:
    """The cluster of a unit with no text: what it is, its old and new values, without its configurations."""
    subject = unit.subject
    shape = (
        (unit.kind, subject.old, subject.new)
        if isinstance(subject, (CellChange, JoinChange))
        else (unit.kind, subject)
    )
    return "c-" + hashlib.sha1(repr(shape).encode()).hexdigest()[:8]


def fragment(unit: Unit, fonts: Fonts, universe: Sequence[str], badge) -> dict:
    """Write one unit as its shard fragment. `badge(configs, universe)` is `build.config_badge`."""
    gate, note = badge(unit.configs, universe)
    no_verdict = unit.kind == PROVENANCE
    text = unit.text
    before_names: list[str] = []
    after_names: list[str] = []
    before_record: dict = {"glyphs": [], "junctions": []}
    after_record: dict = {"cells": [], "junctions": [], "extensions": []}
    codepoints: str | None = None
    pair = pair_codepoints = highlight = None
    boundary_marks: list[dict] = []
    diff_positions: list[int] = []
    render_groups = [{"configs": list(unit.configs)}]
    ink_identical = picture_identical = False
    cluster: str | None = None
    if text:
        values = tuple(map(ord, text))
        codepoints = ":".join(f"{value:04X}" for value in values)
        runs = {config: fonts.runs(text, config) for config in unit.configs}
        before, after = runs[unit.configs[0]]
        before_names, after_names = list(before.names), list(after.names)
        before_record = {"glyphs": before_names, "junctions": _junctions(before, fonts.classifiers[0])}
        after_junctions = _junctions(after, fonts.classifiers[1])
        after_record = {
            "cells": after_names,
            "junctions": after_junctions,
            "extensions": [
                0 if junction == "break" else _adjustment(left, "ex") + _adjustment(right, "en")
                for junction, left, right in zip(after_junctions, after.names, after.names[1:])
            ],
        }
        groups: dict[tuple, list[str]] = {}
        for config in unit.configs:
            run_before, run_after = runs[config]
            groups.setdefault(
                (run_before.names, run_before.positions, run_after.names, run_after.positions), []
            ).append(config)
        render_groups = [{"configs": configs} for configs in groups.values()]
        focus = _focus(unit, text, before, after)
        if focus is not None:
            chosen = _pair_of(after, focus, unit.kind)
            if chosen is not None:
                pair = {"left": chosen[0], "right": chosen[1]}
                start = _glyph_span(after, chosen[0], len(text))[0]
                end = _glyph_span(after, chosen[1], len(text))[1]
                pair_codepoints = [start, end]
                highlight = {"before": _rect(before, start, end), "after": _rect(after, start, end)}
        found = substitutions(text, before, after)
        starts = _cluster_starts(after, len(text))
        diff_positions = sorted({starts[position][0] for position, _substitution in found})
        if not diff_positions and pair is not None:
            diff_positions = [pair["left"], pair["right"]]
        pens = _pens(after)
        seen: set[int] = set()
        for index, cluster_value in enumerate(after.clusters):
            if cluster_value in seen:
                continue
            seen.add(cluster_value)
            kind = BOUNDARY_KINDS.get(values[cluster_value])
            if kind is not None:
                boundary_marks.append({"index": index, "kind": kind, "x": pens[index]})
        if not no_verdict:
            ink_identical = fonts.comparator.ink_identical(text, unit.configs)
            picture_identical = not ink_identical and fonts.comparator.picture_identical(text, unit.configs)
            if unit.unattributed:
                ink_identical = picture_identical = False
        if not (ink_identical or picture_identical or no_verdict):
            cluster = cluster_id(
                delta_digest(fonts.comparator.config_diff(text, config)) for config in unit.configs
            )
    elif not no_verdict:
        cluster = structural_cluster(unit)
    content_key = unit_identity(unit, codepoints, before_names, after_names)
    return {
        "id": unit_cache.unit_id_for(content_key),
        "content_key": content_key,
        "ink_identical": ink_identical,
        "picture_identical": picture_identical,
        "junior_equivalent": False,
        "no_verdict": no_verdict,
        "duplicate_group": None,
        "cluster": cluster,
        "class": CLASS_OF_KIND[unit.kind],
        "group": _group(unit),
        "row_key": _label(unit),
        "codepoints": codepoints,
        "text_entities": text_entities(tuple(map(ord, text))) if text else None,
        "notation": notation(tuple(map(ord, text))) if text else _label(unit),
        "notation_tokens": list(notation_tokens(tuple(map(ord, text)))) if text else None,
        "configs": list(unit.configs),
        "config_note": note,
        "config_gate": gate,
        "render_groups": render_groups,
        "kinds": list(unit.kinds),
        "exemplar": False,
        "before": before_record,
        "after": after_record,
        "diff_positions": diff_positions,
        "pair": pair,
        "pair_codepoints": pair_codepoints,
        "highlight": highlight,
        "boundary_marks": boundary_marks,
        "summary": _summary(unit),
        "explain": _explain(unit),
        "provenance": _provenance(unit),
        "drafts": {"pin": None, "policy": None, "any_of": None},
    }


def inventory_texts(path: Path) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """The kerning inventory's texts by configuration (`rebuild/pipeline/kern_inventory.py`'s `texts`), each as its text and the glyph names its run holds, or none when there is no readable inventory at `path`."""
    try:
        inventory = json.loads(Path(path).read_text(encoding="utf-8"))
        configs = inventory["configs"]
        glyphs = [glyph["name"] for glyph in inventory["glyphs"]]
        found: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        for entry in inventory["texts"]:
            text = "".join(map(chr, entry["cp"]))
            found.setdefault(configs[entry["config"]], []).append(
                (text, tuple(glyphs[index] for index in entry["glyphs"]))
            )
        return found
    except OSError, ValueError, KeyError, IndexError, TypeError:
        return {}
