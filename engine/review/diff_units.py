"""The visible changes a table diff makes, and the review units that show them (doc/review-design.md §2.3).

A settlement row is a class-compressed rule over hundreds of windows, and one join change re-folds hundreds of rules, so a changed row (`tablediff`) is evidence and never a unit. A unit is one visible change, folded across the configurations it holds under:

- A settlement unit is one cell change (`CellChange`): a letter that the accepted build settles to one cell and the new build to another, folded across the left neighbors it does so after. A cell's label already names its entry height and adjustments, so the left glyph adds nothing visible: the left glyphs each substitution was seen after (`Substitution`) are evidence the explain panel lists, not part of what the unit is.
- A join unit is one changed join row (`JoinChange`): its left and right cells, and the junction and extension each table gives the pair, None on a side whose table lacks the pair.
- A quiet unit stands for a letter whose settlement rows changed under a configuration where no candidate text shows it settled differently. It is machine-approved when every candidate draws alike in both fonts or differs only where another glyph's name differs, which is that glyph's own change; otherwise it is a human unit over the shortest text whose ink differs with every glyph name alike.
- A provenance unit stands for a letter whose rows differ only in the records they credit, and takes no verdict.
- A glyph unit is one glyph both fonts have that draws differently (`GlyphChange`, `glyph_changes`): its decomposed outline, its advance and left sidebearing, or its cursive anchors (`fingerprint.glyph_facts`, the anchors without the lookup each sits in). A bitmap, anchor or advance edit leaves the settlement and join tables as they were, so only this bucket shows it. A glyph only one font has is the row diff's business: a stance rename shows there as removed and added rows whose drawings match, which the ink check approves. The glyphs outside Quikscript count too, a Latin glyph's edit being as visible as a letter's.
- A kern unit is one facing pair whose kern value moved (`KernChange`, `kern_changes`), from the snapshot's `kern.tsv` against the new build's by (leading side, following side), where a pair one table lacks has no value. It is drawn with kerning on (`Unit.kerning`, the fragment's `kerning`) and takes no verdict: the kerning view (`#view=kern`) is where kern values are authored and judged.

Finding the texts, one configuration at a time (`UnitFinder.add_config`): a letter whose rows changed there (by its spelling, so a `.noentry` input is its letter, `spelling`) takes as candidates the certificates of its old rules (from the snapshot) and of its new rules (from the new windows head), and every text of at most three letters, boundary tokens among them, that holds it (the input sweep, `sweep_texts`, at `SWEEP_CONTEXT` letters of context), beside the texts the accept's four-letter sweep found under the configuration, when its record applies (`extra_texts`, `engine/review/sweep_record.py`). Each candidate is shaped in both fonts with kern off under the configuration, shortest first, and every position where the glyph that starts a cluster differs gives a substitution (`substitutions`), whichever letter is there. A join row takes the shortest text whose runs hold its two cells side by side in the font of each side the row exists on, from the shaped candidates, the kerning inventory's texts, the shortest candidate prefix that settles its left cell followed by its right letter and one more token or none (`UnitFinder._extended_texts`), and a bounded search: the two letters with a letter, a ZWNJ, a space, a namer dot or nothing on each side (`pair_texts`). A row no text is found for is a unit with no text.

The accept's sweep (`engine/review/accept_sweep.py`) runs one task a configuration and changed letter (`sweep_finds`): the letter's certificates and every text of at most four letters that holds it (`ACCEPT_SWEEP_CONTEXT`), shaped as above, keeping each cell change only a text longer than the corpus build's reach shows and the shortest such text, and the shortest longer text whose ink differs with every glyph name alike. The merge (`merge_sweep_finds`) keeps, by configuration, what no task's corpus-build candidates show, so a record with finds names changes the corpus does not show. A change that only a context longer than four letters shows can still be missed; diffing the window rows themselves is the sound fix.

A glyph unit's text (`glyph_units`) is the shortest certificate, of either table, whose rule settles to the glyph, shaped in the font of its side; else a code point the font maps to the glyph, alone, the one text a glyph outside Quikscript has; else the shortest kerning-inventory text that holds it. Each is taken only once a run of it holds the glyph, and the unit lists every configuration under which a run of it does. A glyph none of them holds is a unit with no text. A kern unit's text is the kerning inventory's witness of its pair (`pairs`, in `kern.tsv` order), or for a pair the new table lacks, the two letters alone.

Folding (`UnitFinder.units`): a unit's configurations are those its one text shows its change under. Of the texts the unit's configurations found, the one that shows the change under the most of them is taken, the shortest first on a tie, and the configurations it leaves take another text the same way, so every unit's text shows its change under every configuration it lists. Then the units that show the same thing become one (`merge`): join, settlement and glyph units with the same text under the same configurations, which shape to the same runs in both fonts, as a removed join row, its added twin, the substitution at that junction and a glyph redrawn in it do, are one unit that carries all their kinds and rows and stands in the class of its first member by `KIND_PRECEDENCE`: `joins` when any of them is a join row, else `settlement` when any is a cell change, else `glyphs`. Kern units with the same text under the same configurations fold the same way among themselves and with no other kind, since they are drawn with kerning on. Units whose configurations differ stay apart.

`fragment` writes a unit as the corpus's shard JSON (doc/review-design.md §7.2): the before and after runs, the pair band and highlight from HarfBuzz's clusters, the machine checks (`ink.InkComparator`), the cluster, a digest of the ink deltas the unit's configurations show, so units that show the same pixels share a Queue card, and the id, `unit_ids.unit_id_for` of a digest of what the unit is (`unit_identity`). Shaping is kern-neutral (`ink.kern_neutral`), as everywhere in the corpus, but for a kern unit's, which shapes, compares and draws with kerning on in both fonts (`KernedInkComparator`).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import time
from collections.abc import Container, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.ttLib import TTFont

from engine.pipeline import fingerprint
from engine.pipeline.labels import spec_alphabet
from engine.pipeline.model import ResolvedSpec
from engine.review import tablediff, unit_ids
from engine.review.enrich import LETTERS, letter_display, notation, notation_tokens, text_entities
from engine.review.ink import (
    InkComparator,
    OutlineCache,
    OutlineIntern,
    delta_digest,
    features_for,
    kern_neutral,
)
from engine.validation.classify import JunctionClassifier
from engine.validation.shaping import ShapeResult, Shaper, first_glyph_covering, last_glyph_covering

EDGE = "#EDGE"
BOUNDARY_GLYPHS = frozenset({"space", "uni200C", "periodcentered"})
BOUNDARY_KINDS = {0x0020: "space", 0x00B7: "namer-dot", 0x200C: "zwnj"}
# Letters of context around the letter a sweep text holds (`sweep_texts`): the corpus build's sweep, at most three letters with the letter itself, and the accept's sweep (`engine/review/accept_sweep.py`), at most four, which looks only for what the corpus build's cannot show (`sweep_finds`).
SWEEP_CONTEXT = 2
ACCEPT_SWEEP_CONTEXT = 3
# How many texts one accept-sweep task returns whose ink differs with every glyph name alike (`sweep_finds`): the corpus build takes only the first such text that holds a letter whose rules changed as that letter's evidence (`UnitFinder.add_config`).
ACCEPT_SWEEP_QUIET_TEXTS = 1
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
GLYPH = "glyph"
KERN = "kern"

FAMILIES = frozenset(LETTERS.values())
# The kind a substitution unit carries when its letter's own rows did not change, so the change comes from its context.
CONTEXT_KIND = "context"
# What a glyph unit's glyph changed in: its decomposed outline, its advance and left sidebearing, or its cursive anchors.
GLYPH_KINDS = ("outline", "metrics", "anchors")
KINDS = (*tablediff.DIFF_BUCKETS, CONTEXT_KIND, *GLYPH_KINDS)
# The group of a glyph unit whose glyph is in no Quikscript family (Latin, Shavian, punctuation, symbols, the boundary glyphs), so the batch list folds them together and the family filter offers them as one entry.
NON_QUIKSCRIPT_GROUP = "non-quikscript"
# How many of a glyph's shortest certificates are shaped before the next kind of text is tried.
GLYPH_CERTIFICATE_TRIES = 8
# The overlay configuration, which settles nothing: each letter is its own copy (`engine/pipeline/kern_inventory.py`). A glyph only it reaches, a letter's ss10 copy, is a glyph unit listed under it alone.
OVERLAY_CONFIG = "ss10"


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
GLYPHS_CLASS = DiffClass(
    "glyphs",
    "Glyphs",
    "A glyph draws differently than in the accepted build: its outline, its advance and sidebearing, or its cursive anchors moved, with the tables unchanged.",
    "Each unit is one glyph both fonts have whose drawing differs, shown in the shortest certificate that settles to it, else its code point alone, else a kerning-inventory text that holds it. Glyphs outside Quikscript are listed under the group non-quikscript.",
)
KERNING_CLASS = DiffClass(
    "kerning",
    "Kerning",
    "A facing pair is kerned by another value than in the accepted build; judged in the kerning view.",
    "Each unit is one facing pair whose kern value moved, drawn with kerning on in its kerning-inventory text. Kern values are authored and judged in the kerning view (the header's Kerning button), so these take no verdict here.",
    no_verdict=True,
)
PROVENANCE_CLASS = DiffClass(
    "provenance-only",
    "Provenance only",
    "A letter's rules credit other records and settle as before; nothing to judge.",
    "These letters' settlement rows keep their keys, outcomes and joint flags and differ only in the records they name, so no text renders differently.",
    no_verdict=True,
)
# The classes in the sidebar's order within each section.
CLASSES = (JOINS_CLASS, SETTLEMENT_CLASS, GLYPHS_CLASS, KERNING_CLASS, PROVENANCE_CLASS)
CLASS_IDS = tuple(cls.id for cls in CLASSES)
NO_VERDICT_CLASS_IDS = frozenset(cls.id for cls in CLASSES if cls.no_verdict)
CLASS_OF_KIND = {
    SETTLEMENT: "settlement",
    QUIET: "settlement",
    JOIN: "joins",
    PROVENANCE: "provenance-only",
    GLYPH: "glyphs",
    KERN: "kerning",
}
# The kinds whose units take no verdict.
NO_VERDICT_KINDS = frozenset({PROVENANCE, KERN})


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


def sweep_layers(
    alphabet: Sequence[str], spelled: str, context: int = SWEEP_CONTEXT
) -> Iterator[tuple[int, str]]:
    """Every text that holds `spelled` with at most `context` letters of the alphabet around it, boundary tokens among them, with how many it holds, fewest first."""
    for total in range(context + 1):
        for left in range(total + 1):
            for before in itertools.product(alphabet, repeat=left):
                for after in itertools.product(alphabet, repeat=total - left):
                    yield total, "".join(before) + spelled + "".join(after)


def sweep_texts(alphabet: Sequence[str], spelled: str, context: int = SWEEP_CONTEXT) -> Iterator[str]:
    """Every text that holds `spelled` with at most `context` letters of the alphabet around it, boundary tokens among them, shortest first (`sweep_layers`)."""
    for _total, text in sweep_layers(alphabet, spelled, context):
        yield text


def sweep_size(symbols: int, context: int = SWEEP_CONTEXT) -> int:
    """How many texts `sweep_layers` yields over an alphabet of `symbols` tokens: `total + 1` placements of each of the `symbols ** total` contexts of each size up to `context`, a text two placements spell alike counted twice."""
    return sum((total + 1) * symbols**total for total in range(context + 1))


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


@dataclass(frozen=True, order=True)
class GlyphChange:
    """One glyph both fonts have that draws differently: its name, the digest of its facts in each font (`facts_digest`), and what differs, in `GLYPH_KINDS` order."""

    name: str
    old: str
    new: str
    differs: tuple[str, ...]


def _anchor_pairs(records) -> tuple:
    """A glyph's cursive anchors as `fingerprint.cursive_anchor_map` gives them, without the lookup each record sits in, sorted: a lookup added or moved elsewhere in the font renumbers every glyph's records and moves no anchor."""
    pairs = {
        (None if entry is None else tuple(entry), None if exit_ is None else tuple(exit_))
        for _index, entry, exit_ in records or ()
    }
    return tuple(sorted(pairs, key=repr))


def facts_digest(facts: tuple) -> str:
    """The sha256 of one glyph's facts (`fingerprint.glyph_facts`) with its anchors as `_anchor_pairs` reads them."""
    outline, metrics, anchors = facts
    return hashlib.sha256(repr((outline, metrics, _anchor_pairs(anchors))).encode()).hexdigest()


def glyph_changes(before: Path, after: Path) -> list[GlyphChange]:
    """Every glyph both fonts name whose facts differ (`fingerprint.glyph_facts`): its outline, its metrics, or its anchors without their lookups (`_anchor_pairs`), sorted by name. Two fonts with the same bytes have none."""
    if fingerprint.file_sha256(Path(before)) == fingerprint.file_sha256(Path(after)):
        return []
    fonts = (TTFont(str(before), lazy=True), TTFont(str(after), lazy=True))
    try:
        names = sorted(set(fonts[0].getGlyphOrder()) & set(fonts[1].getGlyphOrder()))
        old_facts, new_facts = (fingerprint.glyph_facts(font, names) for font in fonts)
    finally:
        for font in fonts:
            font.close()
    changes: list[GlyphChange] = []
    for name in names:
        old, new = old_facts[name], new_facts[name]
        differs = tuple(
            kind
            for kind, first, second in (
                ("outline", old[0], new[0]),
                ("metrics", old[1], new[1]),
                ("anchors", _anchor_pairs(old[2]), _anchor_pairs(new[2])),
            )
            if first != second
        )
        if differs:
            changes.append(GlyphChange(name, facts_digest(old), facts_digest(new), differs))
    return changes


@dataclass(frozen=True)
class KernRow:
    """One row of a `kern.tsv` (doc/kerning-design.md §3): the two runes, the two sides that face each other, the value in pixels, the record that sets it, and the configurations that reach the pair."""

    leading: str
    leading_side: str
    following: str
    following_side: str
    by: int
    record: str
    configs: tuple[str, ...]


def load_kern(path: Path) -> list[KernRow]:
    """Read a `kern.tsv`, as run_build writes it or gzipped, as a snapshot holds it (`tablediff.open_table`), into its rows in table order."""
    rows: list[KernRow] = []
    with tablediff.open_table(path) as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("leading\t") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            leading, leading_side, following, following_side, by, record = fields[:6]
            configs = tuple(config for config in (fields[6] if len(fields) > 6 else "").split(",") if config)
            try:
                value = int(by)
            except ValueError:
                value = 0
            rows.append(KernRow(leading, leading_side, following, following_side, value, record, configs))
    return rows


@dataclass(frozen=True, order=True)
class KernChange:
    """One facing pair whose kern value moved: its two sides as `kern.tsv` names them, their runes, and the value each table gives the pair, None in the table that lacks it."""

    leading_side: str
    following_side: str
    leading: str
    following: str
    old: int | None
    new: int | None

    @property
    def bucket(self) -> str:
        if self.old is None:
            return "added"
        if self.new is None:
            return "removed"
        return "changed"


def kern_changes(
    old: Sequence[KernRow], new: Sequence[KernRow]
) -> list[tuple[KernChange, KernRow, int | None]]:
    """Diff two kern tables by (leading side, following side): every pair whose value differs, and every pair one table lacks whose value in the other is not 0, since a pair kerned by 0 draws as one the table lacks. Each comes with the row it stands for, the new table's where it has the pair, and that row's index in the new table, which indexes the kerning inventory's `pairs`; None for a pair only the old table has."""
    old_rows = {(row.leading_side, row.following_side): row for row in old}
    new_rows = {(row.leading_side, row.following_side): (index, row) for index, row in enumerate(new)}
    changes: list[tuple[KernChange, KernRow, int | None]] = []
    for key in sorted(set(old_rows) | set(new_rows)):
        before = old_rows.get(key)
        index, after = new_rows.get(key, (None, None))
        old_value = None if before is None else before.by
        new_value = None if after is None else after.by
        if (old_value or 0) == (new_value or 0):
            continue
        row = after if after is not None else before
        assert row is not None
        changes.append(
            (KernChange(key[0], key[1], row.leading, row.following, old_value, new_value), row, index)
        )
    return changes


def kern_value_text(value: int | None) -> str:
    if value is None:
        return "no such pair"
    return f"kerned {value:+d}" if value else "not kerned"


class KernedInkComparator(InkComparator):
    """An `InkComparator` whose runs shape with `kern` on in both fonts, for the kern units, which show a kern value. `named_run` is `InkComparator.named_run` with the features' `kern` set on, where that one turns it off (`ink.kern_neutral`); every reading of the comparator goes through it."""

    def named_run(self, side: str, text: str, features: dict[str, bool]) -> tuple[tuple[str, ...], list]:
        shaper, outlines = self._sides[side]
        result = shaper.shape(text, {**features, "kern": True})
        pieces = []
        pen_x = 0
        for name, (x_offset, y_offset, x_advance) in zip(result.names, result.positions):
            key, origin_x, origin_y = outlines.shape_key(name)
            if key:
                pieces.append((name, key, pen_x + x_offset + origin_x, y_offset + origin_y, origin_x))
            pen_x += x_advance
        return result.names, pieces


class Fonts:
    """The two fonts a diff shapes in, the snapshot's (`before`) and the new build's (`after`), with their outlines, junction classifiers and ink comparators. `runs` shapes one text in both under one configuration, kern-neutral unless asked for kerning, and counts what it shaped."""

    def __init__(self, before: Path, after: Path) -> None:
        self.paths = (Path(before), Path(after))
        self.before = Shaper(before)
        self.after = Shaper(after)
        intern = OutlineIntern()
        self._outlines = (OutlineCache(before, intern), OutlineCache(after, intern))
        self._alike: dict[str, bool] = {}
        self.comparator = InkComparator(before, after)
        self._kerned: KernedInkComparator | None = None
        self.classifiers = (_classifier(before), _classifier(after))
        self.shaped = 0
        self.seconds = 0.0

    def comparator_for(self, kerning: bool) -> InkComparator:
        """The kern-neutral comparator, or with `kerning` the one that shapes with kerning on, made the first time it is asked for."""
        if not kerning:
            return self.comparator
        if self._kerned is None:
            self._kerned = KernedInkComparator(*self.paths)
        return self._kerned

    def runs(self, text: str, config: str, kerning: bool = False) -> tuple[ShapeResult, ShapeResult]:
        features = {**features_for(config), "kern": True} if kerning else kern_neutral(features_for(config))
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
    """One review unit before it is written: its kind (`SETTLEMENT`, `QUIET`, `JOIN`, `PROVENANCE`, `GLYPH` or `KERN`), what it is (`subject`, a `CellChange`, a `JoinChange`, a `GlyphChange`, a `KernChange` or a rune name), the configurations it holds under in the universe's order, its text (None for a change no text was found for), its `kinds`, how the text was found, and the changed rows it stands for, by configuration. A settlement unit also carries the (before, after) left glyphs its substitutions were seen after (`lefts`) and the after font's left glyph in its own text (`left`), and a kern unit the record that sets its value (`records`). A unit `merge` made of several carries them as `members`, the first of which, by `KIND_PRECEDENCE`, gives its kind and subject."""

    kind: str
    subject: CellChange | JoinChange | GlyphChange | KernChange | str
    configs: tuple[str, ...]
    text: str | None
    kinds: tuple[str, ...]
    found_by: str
    rows: Mapping[str, tuple[tablediff.DiffEntry, ...]] = field(default_factory=dict)
    unattributed: bool = False
    lefts: tuple[tuple[str, str], ...] = ()
    left: str | None = None
    members: tuple[Unit, ...] = ()
    records: tuple[str, ...] = ()

    @property
    def parts(self) -> tuple[Unit, ...]:
        """The units this one shows: its members, or itself."""
        return self.members or (self,)

    @property
    def kerning(self) -> bool:
        """Whether the unit is shaped, compared and drawn with kerning on: a kern unit, which shows a kern value."""
        return self.kind == KERN


# The order a merged unit's members take, whose first gives the unit its kind and class: a join row before a substitution, and a substitution before a redrawn glyph. Kern units fold only with each other.
KIND_PRECEDENCE = {JOIN: 0, SETTLEMENT: 1, GLYPH: 2, KERN: 3}
MERGED_KINDS = frozenset(KIND_PRECEDENCE)


def _entry_key(entry: tablediff.DiffEntry) -> tuple[str, str, str]:
    return (entry.table, entry.bucket, entry.key.label())


def merge(units: Sequence[Unit]) -> list[Unit]:
    """Make the join, settlement and glyph units with the same text under the same configurations one unit each, and the kern units likewise among themselves: the same text shaped under the same configurations gives the same runs in both fonts, so they show the same thing, while a kern unit's runs are shaped with kerning on. The merged unit's members are sorted by `KIND_PRECEDENCE` and what they are, it takes the first's kind, subject and how its text was found, and it carries every member's kinds, rows and records. A unit with no text, a quiet unit and a provenance unit are never merged, and units whose configurations differ stay apart."""
    groups: dict[tuple, list[Unit]] = {}
    for index, unit in enumerate(units):
        key = (
            (unit.text, unit.configs, unit.kerning)
            if unit.text is not None and unit.kind in MERGED_KINDS
            else (index,)
        )
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
                records=tuple(dict.fromkeys(record for unit in group for record in unit.records)),
            )
        )
    return merged


def _subject_key(subject: CellChange | JoinChange | GlyphChange | KernChange | str) -> list:
    """What a unit is, as its id and the merge order read it."""
    if isinstance(subject, CellChange):
        return [SETTLEMENT, subject.rune, subject.old, subject.new]
    if isinstance(subject, JoinChange):
        return [JOIN, subject.left, subject.right, subject.old, subject.new]
    if isinstance(subject, GlyphChange):
        return [GLYPH, subject.name, subject.old, subject.new]
    if isinstance(subject, KernChange):
        return [KERN, subject.leading_side, subject.following_side, subject.old, subject.new]
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


def changed_letters(
    settlement: Iterable[tablediff.SettlementDiffEntry],
) -> tuple[dict[str, set[str]], set[str]]:
    """The letters one configuration's settlement row diff changed, by rune (the input's `base_label`, so a `.noentry` input is its letter), each with the buckets its rows changed in, and the letters with a row that changed only the records it credits, some of which may have other changed rows too. `UnitFinder.add_config` finds the units of these letters, and the accept's sweep (`engine/review/accept_sweep.py`) sweeps the same letters further."""
    changed: dict[str, set[str]] = {}
    provenance: set[str] = set()
    for entry in settlement:
        rune = base_label(entry.key.input)
        if entry.bucket == "provenance-only":
            provenance.add(rune)
        else:
            changed.setdefault(rune, set()).add(entry.bucket)
    return changed, provenance


def rune_certificates(
    runes: Container[str], *tables: Iterable[tuple[str, tuple[int, ...] | None]]
) -> dict[str, set[str]]:
    """The certificate texts of every rule whose input is one of `runes`, by rune, from each table's (input label, certificate text) pairs, a rule with an empty certificate left out."""
    found: dict[str, set[str]] = {}
    for pairs in tables:
        for label, values in pairs:
            rune = base_label(label)
            if values and rune in runes:
                found.setdefault(rune, set()).add("".join(map(chr, values)))
    return found


class UnitFinder:
    """Collect one diff's visible changes a configuration at a time (`add_config`) and fold them into units (`units`). `universe` is every configuration the corpus can list, in the order units list theirs. `inventory` maps a configuration to the kerning inventory's texts under it, each as its text and its glyph names in the new font. `context` is how many letters of context the input sweep puts around a changed letter (`sweep_texts`)."""

    def __init__(
        self,
        spec: ResolvedSpec,
        fonts: Fonts,
        universe: Sequence[str],
        inventory: Mapping[str, Sequence[tuple[str, tuple[str, ...]]]] | None = None,
        context: int = SWEEP_CONTEXT,
    ) -> None:
        self.spec = spec
        self.fonts = fonts
        self.universe = tuple(universe)
        self.alphabet = spec_alphabet(spec)
        self.inventory = inventory or {}
        self.context = context
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
        old_certificates: Sequence[tuple[str, tuple[int, ...] | None]] = (),
        new_certificates: Sequence[tuple[str, tuple[int, ...] | None]] = (),
        extra_texts: Iterable[str] = (),
    ) -> ConfigCounts:
        """Find the changes of one configuration's row diff. `old_certificates` and `new_certificates` are each table's (input label, certificate text) pairs, one per rule. `extra_texts` are candidates beside the certificates and the input sweep: the texts the accept's sweep found under this configuration (`engine/review/sweep_record.py`), which show changes the input sweep's context is too short for."""
        start = time.perf_counter()
        shaped = self.fonts.shaped
        counts = self.counts.setdefault(config, ConfigCounts())
        for entry in settlement:
            counts.settlement[entry.bucket] = counts.settlement.get(entry.bucket, 0) + max(
                len(entry.paired), 1
            )
        for entry in joins:
            counts.joins[entry.bucket] = counts.joins.get(entry.bucket, 0) + 1

        changed, provenance = changed_letters(settlement)
        for entry in settlement:
            self._rows.setdefault(base_label(entry.key.input), {}).setdefault(config, []).append(entry)
        for rune, buckets in changed.items():
            self._bucket_kinds.setdefault(rune, {})[config] = buckets
        counts.letters = len(changed)

        certificates = rune_certificates(changed, old_certificates, new_certificates)
        new_shortest: dict[str, str] = {}
        for label, values in new_certificates:
            rune = base_label(label)
            if values and rune in provenance:
                text = "".join(map(chr, values))
                best = new_shortest.get(rune)
                if best is None or (len(text), text) < (len(best), best):
                    new_shortest[rune] = text

        words = {rune: word for rune in changed if (word := self._spelled(rune)) is not None}
        texts: set[str] = set(extra_texts)
        owners: dict[str, set[str]] = {}
        for rune, values in certificates.items():
            texts.update(values)
            for text in values:
                owners.setdefault(text, set()).add(rune)
        for word in words.values():
            texts.update(sweep_texts(self.alphabet, word, self.context))

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

    def units(self, extra: Sequence[Unit] = ()) -> list[Unit]:
        """Fold every change found into units, in a fixed order, and merge them with `extra`, the glyph and kern units, which come after them (`merge`)."""
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
        return merge([*units, *extra])


def _join_sort_key(change: JoinChange) -> tuple:
    return (change.left, change.right, repr(change.old), repr(change.new))


# --- the accept's sweep ---------------------------------------------------------------


@dataclass(frozen=True)
class SweepFinds:
    """What one task of the accept's sweep saw (`sweep_finds`): its configuration, its letter's rune and spelling, and how many texts it shaped; of the candidates the corpus build shapes for the letter too, the cell changes they show (`seen`), the runes their substitutions name (`substituted`), and the spellings of the configuration's changed letters that a candidate whose ink differs with every glyph name alike holds (`unattributed`); then, of the longer texts, each cell change no such candidate of the task shows, with the shortest text that shows it (`cells`), and the shortest whose ink differs with every glyph name alike (`quiet`, at most `ACCEPT_SWEEP_QUIET_TEXTS`). `peak_bytes` is the peak footprint of the process that ran it, 0 when unread."""

    config: str
    rune: str
    word: str
    texts: int
    seen: frozenset[CellChange]
    substituted: frozenset[str]
    unattributed: frozenset[str]
    cells: Mapping[CellChange, str]
    quiet: tuple[str, ...]
    peak_bytes: int = 0


def sweep_finds(
    fonts: Fonts,
    config: str,
    rune: str,
    word: str,
    alphabet: Sequence[str],
    *,
    words: Iterable[str] = (),
    certificates: Iterable[str] = (),
    ordinary_context: int = SWEEP_CONTEXT,
    context: int = ACCEPT_SWEEP_CONTEXT,
) -> SweepFinds:
    """One task of the accept's sweep: shape in both fonts under `config`, kern-neutral, the letter's certificates and every text that holds its spelling `word` with at most `context` letters of the alphabet around it, fewest first (`sweep_layers`), and return what they show (`SweepFinds`). The certificates and the texts with at most `ordinary_context` letters of context are the corpus build's candidates for the letter (`UnitFinder.add_config`), and the longer texts are the ones its sweep cannot reach. `words` are the spellings of every letter whose rules changed under the configuration, the letters the corpus build credits a text whose ink differs with every glyph name alike to. The texts are made as they are shaped and never held as a list, so a task holds two shapers, their outlines and what it found."""
    seen: set[CellChange] = set()
    substituted: set[str] = set()
    unattributed: set[str] = set()
    longer: dict[CellChange, str] = {}
    quiet: list[str] = []
    attributable = tuple(dict.fromkeys(words))
    shaped = 0

    def candidates() -> Iterator[tuple[bool, str]]:
        for text in certificates:
            yield True, text
        for total, text in sweep_layers(alphabet, word, context):
            yield total <= ordinary_context, text

    for ordinary, text in candidates():
        before, after = fonts.runs(text, config)
        shaped += 1
        for _position, substitution in substitutions(text, before, after):
            cell = CellChange.of(substitution)
            if ordinary:
                seen.add(cell)
                substituted.update((base_label(substitution.old), base_label(substitution.new)))
                continue
            known = longer.get(cell)
            if known is None or (len(text), text) < (len(known), known):
                longer[cell] = text
        if before.names == after.names and not fonts.runs_alike(before, after):
            if ordinary:
                unattributed.update(spelled for spelled in attributable if spelled in text)
            elif text not in quiet:
                quiet = sorted([*quiet, text], key=lambda found: (len(found), found))[
                    :ACCEPT_SWEEP_QUIET_TEXTS
                ]
    return SweepFinds(
        config,
        rune,
        word,
        shaped,
        frozenset(seen),
        frozenset(substituted),
        frozenset(unattributed),
        {cell: text for cell, text in sorted(longer.items()) if cell not in seen},
        tuple(quiet),
    )


@dataclass(frozen=True, order=True)
class SweepFind:
    """One change the accept's sweep found under one configuration that no candidate of the corpus build shows there: the shortest text that shows it, and what it is, as a row key names it: `settlement <rune>: <old> -> <new>` for a cell change, `quiet <rune>` for a letter that text draws with different ink and every glyph name alike."""

    text: str
    change: str


def merge_sweep_finds(finds: Iterable[SweepFinds]) -> dict[str, list[SweepFind]]:
    """Merge the accept sweep's tasks into what the corpus build's candidates do not show, by configuration, each configuration's finds shortest text first, then in code-point order, then by change, whatever order the tasks finished in. A cell change that some task's corpus-build candidates show under the configuration is shown already; each other one takes the shortest text any task found it in. A letter's quiet text stands only where no corpus-build candidate shows a substitution of the letter or holds its spelling in a text that draws with different ink and every glyph name alike, either of which the corpus build would take as the letter's evidence, and no cell change found here substitutes the letter."""
    by_config: dict[str, list[SweepFinds]] = {}
    for found in finds:
        by_config.setdefault(found.config, []).append(found)
    merged: dict[str, list[SweepFind]] = {}
    for config, group in sorted(by_config.items()):
        seen = frozenset().union(*(found.seen for found in group))
        substituted = frozenset().union(*(found.substituted for found in group))
        unattributed = frozenset().union(*(found.unattributed for found in group))
        cells: dict[CellChange, str] = {}
        for found in group:
            for cell, text in found.cells.items():
                known = cells.get(cell)
                if cell not in seen and (known is None or (len(text), text) < (len(known), known)):
                    cells[cell] = text
        moved = {base_label(name) for cell in cells for name in (cell.old, cell.new)}
        entries = {
            SweepFind(text, f"settlement {cell.rune}: {cell.old} -> {cell.new}")
            for cell, text in cells.items()
        }
        for found in group:
            if found.rune in substituted or found.rune in moved or found.word in unattributed:
                continue
            entries.update(SweepFind(text, f"quiet {found.rune}") for text in found.quiet)
        merged[config] = sorted(entries, key=lambda entry: (len(entry.text), entry.text, entry.change))
    return merged


# --- the glyph and kern buckets ------------------------------------------------------


@dataclass(frozen=True)
class OutcomeText:
    """A certificate as the glyph bucket reads it: the cell its rule settles to, its text, the configuration it is in, and which font settles it, `before` (the snapshot's table) or `after` (the new one)."""

    outcome: str
    text: str
    config: str
    side: str


def certificate_witnesses(
    outcomes: Mapping[str, Sequence[tuple[str, tuple[int, ...] | None]]], side: str
) -> list[OutcomeText]:
    """The certificates of one side's tables as `OutcomeText`s, from each configuration's (outcome, certificate text) pairs, one per rule in rule order, leaving out a rule whose certificate is empty."""
    found: list[OutcomeText] = []
    for config, pairs in outcomes.items():
        for outcome, values in pairs:
            if values:
                found.append(OutcomeText(outcome, "".join(map(chr, values)), config, side))
    return found


def _cmap_texts(fonts: Fonts) -> dict[str, list[str]]:
    """Each glyph name either font's cmap maps a code point to, with those code points as one-character texts, the lowest first."""
    found: dict[str, set[int]] = {}
    for path in fonts.paths:
        font = TTFont(str(path), lazy=True)
        try:
            for codepoint, name in (font.getBestCmap() or {}).items():
                found.setdefault(name, set()).add(codepoint)
        finally:
            font.close()
    return {name: [chr(value) for value in sorted(values)] for name, values in found.items()}


def _glyph_configs(fonts: Fonts, text: str, name: str, universe: Sequence[str]) -> tuple[str, ...]:
    """The configurations of `universe` under which a run of `text`, in either font, holds the glyph `name`."""
    held: list[str] = []
    for config in universe:
        before, after = fonts.runs(text, config)
        if name in before.names or name in after.names:
            held.append(config)
    return tuple(held)


def glyph_units(
    changes: Sequence[GlyphChange],
    fonts: Fonts,
    universe: Sequence[str],
    certificates: Sequence[OutcomeText] = (),
    inventory: Mapping[str, Sequence[tuple[str, tuple[str, ...]]]] | None = None,
) -> list[Unit]:
    """One unit for each changed glyph, over the first text of these that holds it: the shortest of `certificates` whose rule settles to the glyph, a run of it in its side's font holding the glyph, trying `GLYPH_CERTIFICATE_TRIES` of them; then a code point either font maps to the glyph, alone, the lowest first; then the shortest of the kerning inventory's texts (`inventory_texts`) whose run holds it. The unit lists every configuration of `universe` under which a run of its text in either font holds the glyph (`_glyph_configs`). Then the glyph's rune's letters alone under the overlay (`OVERLAY_CONFIG`), which reach a letter's ss10 copy, listed under the overlay alone. A glyph none of them holds, such as a letter's bare glyph, which settlement always replaces and which only a text with `calt` off draws, is a unit with no text listed under every configuration of `universe`; its cluster is its group's (`structural_cluster`), so one edit's textless glyphs of one letter share a Queue card. `merge` folds the units with one text."""
    by_outcome: dict[str, list[OutcomeText]] = {}
    wanted = {change.name for change in changes}
    for certificate in certificates:
        if certificate.outcome in wanted:
            by_outcome.setdefault(certificate.outcome, []).append(certificate)
    order = {config: index for index, config in enumerate(universe)}
    cmap = _cmap_texts(fonts) if changes else {}
    units: list[Unit] = []
    for change in changes:
        name = change.name
        found: tuple[str, str] | None = None
        candidates = sorted(
            by_outcome.get(name, ()),
            key=lambda item: (len(item.text), item.text, order.get(item.config, len(order)), item.side),
        )
        for certificate in candidates[:GLYPH_CERTIFICATE_TRIES]:
            before, after = fonts.runs(certificate.text, certificate.config)
            if name in (before.names if certificate.side == "before" else after.names):
                found = (
                    certificate.text,
                    f"the shortest certificate of the {'accepted' if certificate.side == 'before' else 'new'} {certificate.config} table that settles to it",
                )
                break
        if found is None:
            for text in cmap.get(name, ()):
                if _glyph_configs(fonts, text, name, universe):
                    found = (text, "its code point alone")
                    break
        if found is None:
            listed = sorted(
                {text for texts in (inventory or {}).values() for text, names in texts if name in names},
                key=lambda text: (len(text), text),
            )
            for text in listed[:GLYPH_CERTIFICATE_TRIES]:
                if _glyph_configs(fonts, text, name, universe):
                    found = (text, "the shortest kerning-inventory text that holds it")
                    break
        if found is None:
            letters = spelling_text(base_label(name))
            if letters:
                before, after = fonts.runs(letters, OVERLAY_CONFIG)
                if name in before.names or name in after.names:
                    units.append(
                        Unit(
                            GLYPH,
                            change,
                            (OVERLAY_CONFIG,),
                            letters,
                            change.differs,
                            "its letters alone under the overlay",
                        )
                    )
                    continue
            units.append(
                Unit(
                    GLYPH,
                    change,
                    tuple(universe),
                    None,
                    change.differs,
                    "no text: no certificate, code point, kerning-inventory text or overlay copy holds it",
                )
            )
            continue
        text, found_by = found
        configs = _glyph_configs(fonts, text, name, universe) or tuple(universe)
        units.append(Unit(GLYPH, change, configs, text, change.differs, found_by))
    return units


def kern_units(
    changes: Sequence[tuple[KernChange, KernRow, int | None]],
    universe: Sequence[str],
    inventory: Mapping | None = None,
    new_rows: Sequence[KernRow] = (),
) -> list[Unit]:
    """One unit for each changed facing pair (`kern_changes`), over the kerning inventory's witness of the pair, the text and the configuration its `pairs` entry at the row's index in the new table names, when the inventory's pairs are the new table's rows, side for side (`inventory_matches`); otherwise, and for a pair only the old table has, over the two letters alone. The unit lists the configurations of `universe` the pair's row names, in the universe's order, or `default` when it names none of them, and carries the row's record. `merge` folds the units with one text."""
    witnesses = inventory if inventory is not None and inventory_matches(inventory, new_rows) else None
    units: list[Unit] = []
    for change, row, index in changes:
        text: str | None = None
        found_by = "the two letters alone"
        if witnesses is not None and index is not None:
            pair = witnesses["pairs"][index]
            witness = pair.get("witness")
            if witness is not None:
                text = "".join(map(chr, witnesses["texts"][witness]["cp"]))
                found_by = "the kerning inventory's witness of the pair"
        if text is None:
            text = spelling_text(change.leading) + spelling_text(change.following) or None
        configs = tuple(config for config in universe if config in row.configs) or ("default",)
        units.append(
            Unit(
                KERN,
                change,
                configs,
                text,
                (change.bucket,),
                found_by if text is not None else "no text: neither rune spells out",
                records=(row.record,) if row.record else (),
            )
        )
    return units


def load_inventory(path: Path) -> dict | None:
    """The kerning inventory at `path` (`engine/pipeline/kern_inventory.py`), or None when there is none that reads."""
    try:
        inventory = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return inventory if isinstance(inventory, dict) else None


def inventory_matches(inventory: Mapping, rows: Sequence[KernRow]) -> bool:
    """Whether the inventory's `pairs` are `rows`, the new `kern.tsv`'s, in order, side for side, as a build writes the two beside each other; a kern-only rebuild keeps both, since a kern value moves no facing pair."""
    try:
        sides = inventory["sides"]
        pairs = inventory["pairs"]
        return len(pairs) == len(rows) and all(
            sides[pair["lead"]]["name"] == row.leading_side
            and sides[pair["follow"]]["name"] == row.following_side
            for pair, row in zip(pairs, rows)
        )
    except KeyError, IndexError, TypeError:
        return False


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
    """The code point span of the change a unit shows in its text: the cell change's cluster, the join row's two cells, the redrawn glyph's cluster, the first two glyphs of the kern pair's runes that meet, or the letter a quiet or provenance unit stands for; a merged unit's is its first member's."""
    length = len(text)
    subject = unit.subject
    if isinstance(subject, CellChange):
        for position, substitution in substitutions(text, before, after):
            if CellChange.of(substitution) == subject:
                index = _cluster_starts(after, length)[position][0]
                return _glyph_span(after, index, length)
        return None
    if isinstance(subject, JoinChange):
        pair = (subject.left, subject.right)
        run = after if subject.new is not None else before
        for index, adjacent in enumerate(zip(run.names, run.names[1:])):
            if adjacent == pair:
                return _glyph_span(run, index, length)[0], _glyph_span(run, index + 1, length)[1]
        return None
    if isinstance(subject, GlyphChange):
        for run in (after, before):
            if subject.name in run.names:
                return _glyph_span(run, run.names.index(subject.name), length)
        return None
    if isinstance(subject, KernChange):
        run = after if subject.new is not None else before
        for index, (left, right) in enumerate(zip(run.names, run.names[1:])):
            if base_label(left) == subject.leading and base_label(right) == subject.following:
                return _glyph_span(run, index, length)[0], _glyph_span(run, index + 1, length)[1]
        return None
    spelled = spelling_text(subject)
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
    """The after run's glyph indices the pair band covers: a join's or a kern pair's two glyphs, or a letter or glyph and its left neighbor (its right one at the run's start)."""
    left = first_glyph_covering(after.clusters, focus[0])
    right = last_glyph_covering(after.clusters, focus[1])
    if right > left:
        return left, right
    if kind in (JOIN, KERN):
        return None
    if left > 0:
        return left - 1, left
    if left + 1 < len(after.names):
        return left, left + 1
    return None


def _group(unit: Unit) -> str:
    """The unit's family pair as the app's group folds and family filter read it: the two families that meet at a join or a kern pair, or at a cell change in the unit's own text, or the letter alone after a boundary or the run's start, or a redrawn glyph's family, `NON_QUIKSCRIPT_GROUP` for a glyph in none; a merged unit's is its first member's."""
    subject = unit.subject
    if isinstance(subject, GlyphChange):
        return ":".join(label_families(subject.name)[:1]) or NON_QUIKSCRIPT_GROUP
    if isinstance(subject, (JoinChange, KernChange)):
        left_name, right_name = (
            (subject.left, subject.right)
            if isinstance(subject, JoinChange)
            else (subject.leading, subject.following)
        )
        left, right = label_families(left_name), label_families(right_name)
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
    if isinstance(subject, GlyphChange):
        return f"glyph {subject.name}: {', '.join(subject.differs)}"
    if isinstance(subject, KernChange):
        return f"kern {subject.leading_side} + {subject.following_side}: {kern_value_label(subject.old)} -> {kern_value_label(subject.new)}"
    return f"{unit.kind} {subject}"


def kern_value_label(value: int | None) -> str:
    """A kern value as the row key writes it: signed pixels, or `none` for a table without the pair."""
    return "none" if value is None else f"{value:+d}"


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
        counted = [
            f"{count} {noun}{'' if count == 1 else 's'}"
            for kind, noun in PART_NOUNS.items()
            if (count := sum(1 for part in unit.parts if part.kind == kind))
        ]
        line += f" (one picture for {' and '.join(counted)})"
    return line


# What a merged unit's summary calls each kind of member it counts.
PART_NOUNS = {JOIN: "join row", SETTLEMENT: "cell change", GLYPH: "redrawn glyph", KERN: "kern pair"}
GLYPH_KIND_TEXT = {"outline": "outline", "metrics": "advance or sidebearing", "anchors": "cursive anchors"}


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
    if isinstance(subject, GlyphChange):
        changed = " and ".join(GLYPH_KIND_TEXT[kind] for kind in subject.differs)
        shown = _display(subject.name)
        named = "" if shown == subject.name else f" ({subject.name})"
        return f"{shown}{named} is drawn differently than in the accepted build: its {changed} changed"
    if isinstance(subject, KernChange):
        return (
            f"{_display(subject.leading)} before {_display(subject.following)} ({subject.leading_side} | "
            f"{subject.following_side}) is {kern_value_text(subject.new)}, where the accepted build has it "
            f"{kern_value_text(subject.old)}"
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
    """The explain panel's text: the unit's class and kinds, its configurations and its text, then each change it shows with the changed rows it stands for, each configuration's list once for every configuration that has the same one. A cell change names the left glyphs it was seen after, cut to `EXPLAIN_LEFTS`, and lists the rows that settle to one of its two cells after one of them; a redrawn glyph names what changed and its facts' digests, and a kern pair its two values and the record that sets the new one."""
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
        elif isinstance(subject, GlyphChange):
            lines.append(f"  changed: {', '.join(GLYPH_KIND_TEXT[kind] for kind in subject.differs)}")
            lines.append(f"  facts:   {subject.old[:12]} -> {subject.new[:12]}")
        elif isinstance(subject, KernChange):
            lines.append(f"  before: {kern_value_text(subject.old)}")
            lines.append(f"  after:  {kern_value_text(subject.new)}")
            if part.records:
                lines.append(f"  set by: {', '.join(part.records)}")
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
    """The records the unit's rows credit: every pointer on the changed rows whose outcome is a cell a cell change shows, or on every changed row of a quiet or provenance unit, and the record that sets a kern pair's value, over every member of a merged unit."""
    pointers: set[str] = set()
    for part in unit.parts:
        pointers.update(part.records)
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
    """The cluster of a unit with no text: what it is, its old and new values, without its configurations; for a redrawn glyph, which the reviewer judges from its explain alone, its group, so the textless glyphs of one letter share a card."""
    subject = unit.subject
    if isinstance(subject, GlyphChange):
        shape: tuple = (unit.kind, _group(unit), "no text")
    elif isinstance(subject, (CellChange, JoinChange, KernChange)):
        shape = (unit.kind, subject.old, subject.new)
    else:
        shape = (unit.kind, subject)
    return "c-" + hashlib.sha1(repr(shape).encode()).hexdigest()[:8]


def fragment(unit: Unit, fonts: Fonts, universe: Sequence[str], badge) -> dict:
    """Write one unit as its shard fragment. `badge(configs, universe)` is `build.config_badge`. A kern unit is shaped, checked and drawn with kerning on (`kerning`), and a provenance unit takes no ink check, having nothing that draws differently."""
    gate, note = badge(unit.configs, universe)
    no_verdict = unit.kind in NO_VERDICT_KINDS
    kerning = unit.kerning
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
        runs = {config: fonts.runs(text, config, kerning) for config in unit.configs}
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
        comparator = fonts.comparator_for(kerning)
        if unit.kind != PROVENANCE:
            ink_identical = comparator.ink_identical(text, unit.configs)
            picture_identical = not ink_identical and comparator.picture_identical(text, unit.configs)
            if unit.unattributed:
                ink_identical = picture_identical = False
        if not (ink_identical or picture_identical or no_verdict):
            cluster = cluster_id(
                delta_digest(comparator.config_diff(text, config)) for config in unit.configs
            )
    elif not no_verdict:
        cluster = structural_cluster(unit)
    content_key = unit_identity(unit, codepoints, before_names, after_names)
    return {
        "id": unit_ids.unit_id_for(content_key),
        "content_key": content_key,
        "ink_identical": ink_identical,
        "picture_identical": picture_identical,
        "no_verdict": no_verdict,
        "kerning": kerning,
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
    }


def inventory_texts(inventory: Mapping | Path | None) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """The kerning inventory's texts by configuration (`engine/pipeline/kern_inventory.py`'s `texts`), each as its text and the glyph names its run holds, from the inventory or the path of one (`load_inventory`), or none when there is no readable inventory."""
    if inventory is None or isinstance(inventory, Path):
        inventory = load_inventory(inventory) if inventory is not None else None
        if inventory is None:
            return {}
    try:
        configs = inventory["configs"]
        glyphs = [glyph["name"] for glyph in inventory["glyphs"]]
        found: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        for entry in inventory["texts"]:
            text = "".join(map(chr, entry["cp"]))
            found.setdefault(configs[entry["config"]], []).append(
                (text, tuple(glyphs[index] for index in entry["glyphs"]))
            )
        return found
    except KeyError, IndexError, TypeError:
        return {}
