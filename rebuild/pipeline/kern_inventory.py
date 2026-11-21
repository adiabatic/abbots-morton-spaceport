"""The kern inventory, `kern-inventory.json` (rebuild/KERN-PLAN.md sections 4 and 6): what the review app's kerning view (`#view=kern`) draws. The view shapes nothing. It draws each witness as squares at the pen positions this module computes, and a kern value is a whole-pixel shift of everything after its junction, which the view applies itself. So the inventory holds no kern value: everything in it follows from the tables and the drawings, a `kern:` edit changes none of it, and the view takes the values from the review server's `GET /kern` (`rebuild/review/kern_routes.py`).

`run_m1` writes it once read-back has passed, beside `kern-classes.json` (`write_inventory`), and only on a build: a `--kern-only` pass runs the glyph chain without it, and its staged publish leaves the inventory under `rebuild/out/m1` in place. The file is stamped with the tables' stamp (`run_m1.tables_inputs`, which hashes the rune files without their `kern` blocks and the pipeline's code, this module included) and the digest of the Manual pages its words come from (`MANUAL_PAGES`). A build whose stamp matches the file's leaves the file as it is and settles nothing; any other writes the file through a staging file renamed over it (`output_sink.replace_output`), so the review server, which reads it from this directory, never reads a torn file. `artifact_cycle.M1_ARTIFACT_NAMES` lists it, so a build directory without it is no build a cycle skips. The Manual pages are in no build key, so a page edit reaches the inventory with the next build that runs.

**Witnesses.** Every facing pair and every joined group gets a witness: a real text whose settlement puts one of its member cell pairs side by side, settled through the kernel (`kernel_settle`), so no text is in the inventory that the kernel has not settled to the cells it shows. `find_witnesses` tries, in order:

1. The Manual's words, every run of Quikscript letters in `MANUAL_PAGES` whose letters the alphabet has (`manual_words`), settled under `default`. Every such word is in the inventory whether or not it witnesses anything, because the view lists the Manual's words that hold a pair.
2. The two letters alone, in each configuration that reaches the pair. Under `default` every rune pair's two letters are settled, because a rune pair's representative facing pair is the one its two letters settle to under `default` when they break there (`Witnesses.representative`), drawn as the two cells they settle to, and otherwise the one with the most member cell pairs. Two rune pairs can be typed with the same letters (·Out before ·Tea+Oy and ·Out+Tea before ·Oy), and each reads that one settlement for itself.
3. One letter of context on each side, from the configuration's join rows: a letter whose cell comes before the leading cell, a letter whose cell follows the following one, or both, in widening rounds (`CONTEXT_WIDTHS`) over the first members of each pair still waiting (`CONTEXT_MEMBERS`).
4. For a pair a ZWNJ reaches and the steps above did not witness, a word that ends in the leading cell before a ZWNJ and a word that starts with the following cell after one, found the same way, then settled again as one text. A ZWNJ hides each side from the other, so the composed text settles to both.
5. For a pair the ss10 overlay reaches, the two letters under ss10, which settle nothing: each letter is its copy.
6. Otherwise none: the view draws the pair's first member cells side by side and marks the pair as having no witness text. A pair that a break mid-word reaches takes a witness across a ZWNJ or under ss10 only when the earlier steps found it none.

**Positions.** `place` gives each glyph's pen position from the glyph records alone: a join puts the next glyph at the left glyph's exit anchor less the right glyph's entry anchor, a break at the left glyph's compiled advance (`kern.advance`), and a ZWNJ advances nothing. These are the offsets `defects._check_contact` places a pair at. When the build hands it the compiled font, `check_positions` shapes every text with HarfBuzz with `kern` off and checks each glyph's name and pen position against the inventory, and a disagreement raises `KernInventoryError`. HarfBuzz draws a ZWNJ as the `space` glyph, so either name passes there.

**The document** (`inventory_document`; `check_document` is the authority on its shape and the build runs it before writing):

- `format`, `stamp` (`tables`, `manual`), `ink_x` (the blank pixels left of a glyph's drawing, `geometry.INK_X_OFFSET`), and `configs`, which other entries name by index.
- `runes`: every rune in code-point order with each ligature after its lead (`kern.rune_order_key`), the grid's order, each with its `name`, its `label` in letter-name notation and its `letters`.
- `glyphs`: every minted cell's glyph and the overlay's source cells: `name`, `rune` (an index), `rows` (the bitmap, top row first), `y` (the bottom row's glyph-space y) and `adv` (the compiled advance in pixels).
- `sides`: every side of every rune (`kern.Side`): its `facing`, `name`, `rune`, the own-side keys `stance`, `state` and `bitmap` that name it, `mixed`, and its `cells` as glyph indexes.
- `pairs`: the facing pairs in `kern.tsv` order: `lead` and `follow` (side indexes), `configs`, `reach` (some of `word`, `zwnj` and `ss10`: a break mid-word, a break across a ZWNJ, the overlay), `members` (the count of member cell pairs), `member` (the member the witness shows, or the first one), `witness` and `at`, the text and the gap in it, both null for a pair with no witness text, and, for a pair with a `mixed` side, `drawings`: one member for each distinct pair of facing edges among its members (`distinct_drawings`), so the view can draw each and measure the closest ink over all of them.
- `joined`: the joined groups, the join rows of one stance of the leading rune and one of the following rune at one height (`joined_groups`), with the same `configs`, `members`, `member`, `witness` and `at`.
- `texts`: `cp` (the code points), `config`, `glyphs` (glyph indexes, letters only), `x` (each glyph's pen position in pixels), `gaps` (for each gap between two glyphs, the facing pair at a break and -1 at a join), `joins` (for each gap, the joined group at a join and null at a break), `zwnj` (the gaps that hold a ZWNJ), `manual`, and `notation` (letter-name notation with `data-expect`'s connectors).
- `rune_pairs`: every rune pair with a facing pair or a joined group: `lead` and `follow` (rune indexes), `pairs`, `rep` (the representative facing pair, null with none), `rep_member` (the two glyphs the two letters settle to alone where they break, or else the representative's `member`; null with no representative), `joined`, and `manual` (the Manual texts in which the two runes meet).

Run `uv run python -m rebuild.pipeline.kern_inventory --out PATH` to build it over the tables and the font under `rebuild/out/m1` without a build, for development. It stamps the inventory with the tables' own stamp, since an edit to this module moves the stamp the sources on disk give.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rebuild.pipeline import geometry, kern, output_sink
from rebuild.pipeline.labels import features_for_config
from rebuild.pipeline.model import CellId, GlyphRecord, ResolvedSpec
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.table import DecisionTable

REPO_ROOT = Path(__file__).resolve().parents[2]
KERN_INVENTORY_JSON = "kern-inventory.json"
INVENTORY_FORMAT = "ams-m1-kern-inventory/1"
# The pages whose Quikscript words the inventory settles: The Manual, and the site's front page, which quotes it.
MANUAL_PAGES = ("site/the-manual.html", "site/index.html")
ZWNJ = 0x200C
ZWNJ_LABEL = "uni200C"
# HarfBuzz hides a ZWNJ as the font's space glyph, so a shaped ZWNJ carries either name.
ZWNJ_SHAPED_NAMES = frozenset({ZWNJ_LABEL, "space"})
WORD = "word"
ACROSS_ZWNJ = "zwnj"
OVERLAY = "ss10"
REACHES = (WORD, ACROSS_ZWNJ, OVERLAY)
# The widths of the context rounds (step 3 of the ladder): how many runes before the leading cell and after the following cell each round tries.
CONTEXT_WIDTHS = (2, 6, 20)
# How many of a waiting pair's member cell pairs each context round tries.
CONTEXT_MEMBERS = 4
# How many runes a word that ends or starts at a ZWNJ may take as its one letter of context (step 4).
ZWNJ_CONTEXT_WIDTH = 20
# How many of a pair's members across a ZWNJ step 4 tries.
ZWNJ_MEMBERS = 8
JOIN_CONNECTORS = {"baseline": "~b~", "x-height": "~x~", "y6": "~6~", "top": "~t~"}
SPECIAL_LETTER_LABELS = {"qsIng": "·-ing", "qsJai": "·J’ai"}
# The stance traits letter-name notation writes after a glyph, as `data-expect` does.
NOTATION_TRAITS = ("alt", "half")
_ENTITY = re.compile(r"&#[xX]([0-9a-fA-F]+);|&#([0-9]+);")
_QUIKSCRIPT_RUN = re.compile("[-‌]+")

Slot = CellId | str
"""One settled position of a text: a letter's cell, or a boundary's label (`settle.cell_label`)."""
Settle = Callable[[Sequence[tuple[int, ...]], str], list[list[Slot] | None]]
"""Settles texts (code points) under one configuration, each to its slots, or None for a text the kernel refuses."""
CellPair = tuple[CellId, CellId]


class KernInventoryError(Exception):
    """The inventory cannot be built as the module docstring says: a settled text breaks between two cells no facing pair holds, or joins two no joined group holds, a text holds a boundary other than a ZWNJ, the document fails `check_document`, or HarfBuzz shapes a text to other glyphs or other pen positions than the inventory gives (`check_positions`)."""


def rune_label(rune: str) -> str:
    """Return a rune's name in letter-name notation: ·No, ·-ing, ·J’ai, and ·Day+Utter for a ligature."""
    parts = rune.split("_")

    def letter(name: str) -> str:
        return SPECIAL_LETTER_LABELS.get(name, "·" + name.removeprefix("qs"))

    return letter(parts[0]) + "".join("+" + letter(part).removeprefix("·") for part in parts[1:])


def rune_letters(spec: ResolvedSpec, rune: str) -> tuple[int, ...]:
    """Return the code points a rune is typed with: its own, or its components' for a ligature."""
    found = spec.runes[rune]
    if found.sequence:
        return tuple(_codepoint(spec, part) for part in found.sequence)
    return (_codepoint(spec, rune),)


def _codepoint(spec: ResolvedSpec, rune: str) -> int:
    codepoint = spec.runes[rune].codepoint
    if codepoint is None:
        raise KernInventoryError(f"{rune} has no code point, so no text can be typed with it")
    return codepoint


def read_manual_pages(repo_root: Path = REPO_ROOT) -> dict[str, bytes | None]:
    """Return each of `MANUAL_PAGES`' bytes by its repo-relative name, or None for a page that does not exist."""
    pages: dict[str, bytes | None] = {}
    for name in MANUAL_PAGES:
        try:
            pages[name] = (Path(repo_root) / name).read_bytes()
        except FileNotFoundError:
            pages[name] = None
    return pages


def manual_digest(pages: Mapping[str, bytes | None]) -> str:
    """Return the digest the inventory's stamp carries for the Manual pages: each page's name with its sha256, or `missing`."""
    lines = [
        f"{name}\t{'missing' if pages.get(name) is None else hashlib.sha256(pages[name] or b'').hexdigest()}"
        for name in MANUAL_PAGES
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def manual_words(pages: Mapping[str, bytes | None], letters: Iterable[int]) -> list[tuple[int, ...]]:
    """Return the Manual's words: every run of Quikscript letters and ZWNJs in the pages, numeric character references decoded, with any ZWNJ at either end dropped, whose letters `letters` (the alphabet's code points) all holds, without repeats and sorted. A run with a letter the alphabet lacks is left out whole rather than cut around it."""
    alphabet = frozenset(letters)
    words: set[tuple[int, ...]] = set()
    for name in MANUAL_PAGES:
        page = pages.get(name)
        if page is None:
            continue
        text = _ENTITY.sub(
            lambda match: chr(int(match[1], 16)) if match[1] else chr(int(match[2])),
            page.decode("utf-8"),
        )
        for run in _QUIKSCRIPT_RUN.findall(text):
            word = tuple(ord(char) for char in run.strip("‌"))
            if word and all(codepoint == ZWNJ or codepoint in alphabet for codepoint in word):
                words.add(word)
    return sorted(words)


def inventory_stamp(tables_inputs: str, pages: Mapping[str, bytes | None]) -> dict:
    """Return the stamp an inventory built over tables stamped `tables_inputs` and the Manual pages `pages` carries."""
    return {"tables": tables_inputs, "manual": manual_digest(pages)}


def place(slots: Sequence[Slot], records: Mapping[CellId, GlyphRecord]) -> list[int]:
    """Return each slot's pen position in pixels, as the module docstring says: a join advances by the left glyph's exit anchor less the right glyph's entry anchor, a break by the left glyph's compiled advance, and a ZWNJ by nothing. A join is a pair whose left cell has a live exit, and its right cell then has a live entry (rebuild/KERN-PLAN.md section 2); a pair that breaks the invariant, or a boundary other than a ZWNJ, raises `KernInventoryError`."""
    positions: list[int] = []
    pen = 0
    for index, slot in enumerate(slots):
        positions.append(pen)
        if index + 1 == len(slots):
            break
        following = slots[index + 1]
        if isinstance(slot, str):
            if slot != ZWNJ_LABEL:
                raise KernInventoryError(f"the inventory places letters and ZWNJs, not {slot}")
            continue
        record = records[slot]
        if isinstance(following, str):
            pen += kern.advance(record)
            continue
        joined = slot.exit is not None
        if joined != (following.entry is not None):
            raise KernInventoryError(
                f"{record.name} | {records[following].name}: a pair joins exactly when its left cell has a live exit and its right cell a live entry"
            )
        if not joined:
            pen += kern.advance(record)
            continue
        exit_anchor = record.exit
        entry_anchor = records[following].entry
        if exit_anchor is None or entry_anchor is None:
            raise KernInventoryError(
                f"{record.name} | {records[following].name}: a join with no anchor to place it by"
            )
        pen += exit_anchor[0] - entry_anchor[0]
    return positions


def kernel_settle(spec: ResolvedSpec) -> Settle:
    """Return a `Settle` that tokenizes each text, forms its ligatures against the guard verdicts and settles it through the kernel in one batch per configuration (`kernel_exec.settle_sequences`), dropping a text the kernel refuses."""
    from rebuild.pipeline import kernel_exec
    from rebuild.pipeline import settle as settle_module

    guard = kernel_exec.guard_sweep(spec)

    def run(texts: Sequence[tuple[int, ...]], config: str) -> list[list[Slot] | None]:
        features = features_for_config(config)
        requests = []
        asked: list[int] = []
        for index, codepoints in enumerate(texts):
            try:
                tokens = settle_module.form_ligatures(
                    spec, settle_module.tokens_from_codepoints(spec, codepoints), guard
                )
            except settle_module.SettleError, KeyError:
                continue
            requests.append((tokens, features))
            asked.append(index)
        results: list[list[Slot] | None] = [None] * len(texts)
        if not requests:
            return results
        for index, traces in zip(asked, kernel_exec.settle_sequences(spec, requests, on_error="drop")):
            if traces is None:
                continue
            results[index] = [
                (
                    cell_label(spec, trace.settled.cell)
                    if settle_module.is_boundary_settled(trace.settled)
                    else trace.settled.cell
                )
                for trace in traces
            ]
        return results

    return run


@dataclass(frozen=True)
class JoinedGroup:
    """The join rows of one stance of the leading rune and one stance of the following rune at one height: the configurations they occur in and their member cell pairs, sorted by label."""

    lead: str
    lead_stance: str
    follow: str
    follow_stance: str
    height: str
    configs: tuple[str, ...]
    members: tuple[CellPair, ...]


def _join_rows(entry: object) -> Iterable:
    joins = entry[1] if isinstance(entry, (tuple, list)) else entry
    return getattr(joins, "rows", ())


def joined_groups(
    spec: ResolvedSpec, tables: Mapping[str, object], cells_by_label: Mapping[str, CellId]
) -> tuple[JoinedGroup, ...]:
    """Group the join rows of every configuration that join, as `JoinedGroup` says, in rune order (`kern.rune_order_key`), then stance and height."""
    found: dict[tuple[str, str, str, str, str], tuple[list[str], set[CellPair]]] = {}
    for config, entry in tables.items():
        for row in _join_rows(entry):
            if row.junction == kern.BREAK:
                continue
            left = cells_by_label[row.left]
            right = cells_by_label[row.right]
            configs, members = found.setdefault(
                (left.rune, left.stance, right.rune, right.stance, row.junction), ([], set())
            )
            if config not in configs:
                configs.append(config)
            members.add((left, right))
    return tuple(
        JoinedGroup(
            lead,
            lead_stance,
            follow,
            follow_stance,
            height,
            tuple(configs),
            tuple(sorted(members, key=lambda pair: (cell_label(spec, pair[0]), cell_label(spec, pair[1])))),
        )
        for (lead, lead_stance, follow, follow_stance, height), (configs, members) in sorted(
            found.items(),
            key=lambda item: (
                kern.rune_order_key(spec, item[0][0]),
                item[0][1],
                kern.rune_order_key(spec, item[0][2]),
                item[0][3],
                item[0][4],
            ),
        )
    )


@dataclass
class WitnessText:
    """A settled text the inventory holds: its code points, the configuration it was settled under, its slots, and whether it is one of the Manual's words. `source` names the step of the ladder that found it."""

    codepoints: tuple[int, ...]
    config: str
    slots: tuple[Slot, ...]
    manual: bool = False
    source: str = ""


@dataclass
class Witnesses:
    """`find_witnesses`' result: the texts, each facing pair's and each joined group's witness as (text, gap), each rune pair's representative facing pair with the member its two letters settle to, where they settle to a break under `default`, each facing pair's reaches, and the joined groups."""

    texts: list[WitnessText] = field(default_factory=list)
    pairs: dict[int, tuple[int, int]] = field(default_factory=dict)
    groups: dict[int, tuple[int, int]] = field(default_factory=dict)
    representative: dict[tuple[str, str], tuple[int, CellPair]] = field(default_factory=dict)
    reach: list[tuple[str, ...]] = field(default_factory=list)
    joined: tuple[JoinedGroup, ...] = ()

    def sources(self) -> dict[str, int]:
        """Return how many facing pairs each step of the ladder witnessed, `bare` for those with no witness text."""
        counts: dict[str, int] = {}
        for index in range(len(self.reach)):
            witness = self.pairs.get(index)
            source = "bare" if witness is None else self.texts[witness[0]].source
            counts[source] = counts.get(source, 0) + 1
        return counts


def _letter_gaps(slots: Sequence[Slot]) -> list[tuple[CellId, CellId, bool]]:
    """Return each gap between two letters of a text, the two cells and whether a ZWNJ sits between them. A boundary other than a ZWNJ raises `KernInventoryError`."""
    gaps: list[tuple[CellId, CellId, bool]] = []
    previous: CellId | None = None
    across = False
    for slot in slots:
        if isinstance(slot, str):
            if slot != ZWNJ_LABEL:
                raise KernInventoryError(f"a witness text holds the boundary {slot}; only a ZWNJ may")
            across = True
            continue
        if previous is not None:
            gaps.append((previous, slot, across))
        previous = slot
        across = False
    return gaps


class _Search:
    """The state of `find_witnesses`: which member cell pair is in which facing pair or joined group, the configurations each break and join occurs in, and what has been witnessed."""

    def __init__(
        self,
        spec: ResolvedSpec,
        resolution: kern.KernResolution,
        tables: Mapping[str, object],
        cell_glyphs: Mapping[CellId, GlyphRecord],
        ss10_copies: Mapping[str, str] | None,
    ) -> None:
        self.spec = spec
        self.resolution = resolution
        cells_by_label = {record.name: cell for cell, record in cell_glyphs.items()}
        self.word = kern.check_join_rows(tables, cells_by_label)
        self.zwnj = kern.zwnj_breaks(tables, cells_by_label, kern.zwnj_reachable(spec))
        self.overlay, _sources = kern.ss10_breaks(spec, ss10_copies or {})
        self.pair_of: dict[CellPair, int] = {}
        for index, pair in enumerate(resolution.pairs):
            for member in pair.members:
                self.pair_of[member] = index
        self.reach: list[tuple[str, ...]] = []
        for pair in resolution.pairs:
            found = {
                WORD: any(member in self.word for member in pair.members),
                ACROSS_ZWNJ: any(member in self.zwnj for member in pair.members),
                OVERLAY: any(member in self.overlay for member in pair.members),
            }
            self.reach.append(tuple(reach for reach in REACHES if found[reach]))
        self.groups = joined_groups(spec, tables, cells_by_label)
        self.group_of: dict[CellPair, int] = {}
        for index, group in enumerate(self.groups):
            for member in group.members:
                self.group_of[member] = index
        self.joins: dict[CellPair, set[str]] = {}
        self.preds: dict[tuple[str, CellId], list[str]] = {}
        self.succs: dict[tuple[str, CellId], list[str]] = {}
        for config, entry in tables.items():
            for row in _join_rows(entry):
                left = cells_by_label[row.left]
                right = cells_by_label[row.right]
                if row.junction != kern.BREAK:
                    self.joins.setdefault((left, right), set()).add(config)
                before = self.preds.setdefault((config, right), [])
                if left.rune not in before:
                    before.append(left.rune)
                after = self.succs.setdefault((config, left), [])
                if right.rune not in after:
                    after.append(right.rune)
        self.result = Witnesses(reach=list(self.reach), joined=self.groups)

    def letters(self, rune: str) -> tuple[int, ...]:
        return rune_letters(self.spec, rune)

    def _accepts(self, index: int, member: CellPair, across: bool, config: str, phase: str) -> bool:
        if not across:
            return config in self.word.get(member, ())
        if config not in self.zwnj.get(member, ()):
            return False
        return phase == ACROSS_ZWNJ or WORD not in self.reach[index]

    def offer(self, text: WitnessText, phase: str, always: bool = False) -> None:
        """Record what `text` witnesses that nothing has yet: each break between two cells whose facing pair accepts a gap of its kind in the text's configuration (a break mid-word for a pair a break mid-word reaches, and a break across a ZWNJ otherwise or in the ZWNJ step), and each join of a joined group in that configuration. The text joins the inventory when it witnesses anything, or always with `always`."""
        index_of_text: int | None = None
        if always:
            self.result.texts.append(text)
            index_of_text = len(self.result.texts) - 1
        for gap, (left, right, across) in enumerate(_letter_gaps(text.slots)):
            member = (left, right)
            if left.exit is not None:
                group = self.group_of.get(member)
                if group is None or across or text.config not in self.joins.get(member, ()):
                    raise KernInventoryError(
                        f"{self._text_name(text)} joins {cell_label(self.spec, left)} to {cell_label(self.spec, right)}, which no join row of {text.config} holds"
                    )
                if group in self.result.groups:
                    continue
                if index_of_text is None:
                    self.result.texts.append(text)
                    index_of_text = len(self.result.texts) - 1
                self.result.groups[group] = (index_of_text, gap)
                continue
            pair = self.pair_of.get(member)
            if pair is None:
                raise KernInventoryError(
                    f"{self._text_name(text)} breaks between {cell_label(self.spec, left)} and {cell_label(self.spec, right)} under {text.config}, which no facing pair holds"
                )
            if pair in self.result.pairs or not self._accepts(pair, member, across, text.config, phase):
                continue
            if index_of_text is None:
                self.result.texts.append(text)
                index_of_text = len(self.result.texts) - 1
            self.result.pairs[pair] = (index_of_text, gap)

    def _text_name(self, text: WitnessText) -> str:
        return ":".join(f"{codepoint:04X}" for codepoint in text.codepoints)

    def word_waiting(self, config: str) -> list[int]:
        return [
            index
            for index, pair in enumerate(self.resolution.pairs)
            if index not in self.result.pairs
            and WORD in self.reach[index]
            and any(config in self.word.get(member, ()) for member in pair.members)
        ]

    def groups_waiting(self, config: str) -> list[int]:
        return [
            index
            for index, group in enumerate(self.groups)
            if index not in self.result.groups and config in group.configs
        ]

    def word_members(self, index: int, config: str) -> list[CellPair]:
        return [
            member for member in self.resolution.pairs[index].members if config in self.word.get(member, ())
        ]

    def group_members(self, index: int, config: str) -> list[CellPair]:
        return [member for member in self.groups[index].members if config in self.joins.get(member, ())]


def _settled(
    settle: Settle, texts: Iterable[tuple[int, ...]], config: str
) -> list[tuple[tuple[int, ...], list[Slot]]]:
    asked = sorted(set(texts))
    if not asked:
        return []
    return [
        (codepoints, slots) for codepoints, slots in zip(asked, settle(asked, config)) if slots is not None
    ]


def find_witnesses(
    spec: ResolvedSpec,
    resolution: kern.KernResolution,
    tables: Mapping[str, object],
    cell_glyphs: Mapping[CellId, GlyphRecord],
    ss10_copies: Mapping[str, str] | None,
    words: Sequence[tuple[int, ...]],
    settle: Settle,
) -> Witnesses:
    """Find every facing pair's and joined group's witness by the ladder the module docstring gives, settling candidate texts through `settle`, one batch per configuration and step."""
    search = _Search(spec, resolution, tables, cell_glyphs, ss10_copies)
    configs = list(tables)
    default = configs[0] if configs else None

    if default is not None and words:
        for codepoints, slots in _settled(settle, words, default):
            search.offer(
                WitnessText(codepoints, default, tuple(slots), manual=True, source="manual"),
                WORD,
                always=True,
            )

    rune_pairs = sorted(
        {(pair.leading.rune, pair.following.rune) for pair in resolution.pairs}
        | {(group.lead, group.follow) for group in search.groups}
    )
    for config in configs:
        if config == default:
            wanted = rune_pairs
        else:
            wanted = sorted(
                {
                    (resolution.pairs[index].leading.rune, resolution.pairs[index].following.rune)
                    for index in search.word_waiting(config)
                }
                | {
                    (search.groups[index].lead, search.groups[index].follow)
                    for index in search.groups_waiting(config)
                }
            )
        typed = {runes: search.letters(runes[0]) + search.letters(runes[1]) for runes in wanted}
        settled = dict(_settled(settle, typed.values(), config))
        for codepoints, slots in settled.items():
            search.offer(WitnessText(codepoints, config, tuple(slots), source="letters"), WORD)
        if config == default:
            # Two rune pairs can be typed with the same letters (·Out before ·Tea+Oy, ·Out+Tea before ·Oy), so each rune pair reads the one settlement of its text for itself.
            for runes, codepoints in typed.items():
                if codepoints in settled:
                    _note_representative(search, runes, settled[codepoints])

    for width in CONTEXT_WIDTHS:
        for config in configs:
            candidates: set[tuple[int, ...]] = set()
            members = [
                member
                for index in search.word_waiting(config)
                for member in search.word_members(index, config)[:CONTEXT_MEMBERS]
            ]
            members += [
                member
                for index in search.groups_waiting(config)
                for member in search.group_members(index, config)[:CONTEXT_MEMBERS]
            ]
            for left, right in members:
                core = search.letters(left.rune) + search.letters(right.rune)
                before = [()] + [
                    search.letters(rune) for rune in search.preds.get((config, left), [])[:width]
                ]
                after = [()] + [
                    search.letters(rune) for rune in search.succs.get((config, right), [])[:width]
                ]
                candidates.update(head + core + tail for head in before for tail in after)
            for codepoints, slots in _settled(settle, candidates, config):
                search.offer(WitnessText(codepoints, config, tuple(slots), source="context"), WORD)

    for config in configs:
        _witness_across_zwnj(search, settle, config)

    overlay_config = next(iter(kern.overlay_configs(spec)), None)
    if overlay_config is not None:
        for index, pair in enumerate(resolution.pairs):
            if index in search.result.pairs or OVERLAY not in search.reach[index]:
                continue
            left, right = next(member for member in pair.members if member in search.overlay)
            text = WitnessText(
                search.letters(left.rune) + search.letters(right.rune),
                overlay_config,
                (left, right),
                source="ss10",
            )
            search.result.texts.append(text)
            search.result.pairs[index] = (len(search.result.texts) - 1, 0)
    return search.result


def _note_representative(search: _Search, runes: tuple[str, str], slots: Sequence[Slot]) -> None:
    if len(slots) != 2 or any(isinstance(slot, str) for slot in slots):
        return
    left, right = slots
    if not isinstance(left, CellId) or not isinstance(right, CellId):
        return
    if (left.rune, right.rune) != runes or left.exit is not None:
        return
    index = search.pair_of.get((left, right))
    if index is not None:
        search.result.representative[runes] = (index, (left, right))


def _witness_across_zwnj(search: _Search, settle: Settle, config: str) -> None:
    """Step 4 of the ladder in one configuration: for each pair a ZWNJ reaches and nothing has witnessed, find a word that ends in a member's leading cell before a ZWNJ and one that starts with its following cell after one, then settle the two composed and offer the result."""
    waiting = [
        index
        for index, pair in enumerate(search.resolution.pairs)
        if index not in search.result.pairs
        and ACROSS_ZWNJ in search.reach[index]
        and any(config in search.zwnj.get(member, ()) for member in pair.members)
    ]
    if not waiting:
        return
    members = {
        index: [
            member
            for member in search.resolution.pairs[index].members
            if config in search.zwnj.get(member, ())
        ][:ZWNJ_MEMBERS]
        for index in waiting
    }
    lefts = {left for chosen in members.values() for left, _right in chosen}
    rights = {right for chosen in members.values() for _left, right in chosen}
    endings: dict[CellId, tuple[int, ...]] = {}
    starts: dict[CellId, tuple[int, ...]] = {}
    ending_candidates: set[tuple[int, ...]] = set()
    for cell in lefts:
        ending_candidates.add(search.letters(cell.rune) + (ZWNJ,))
        ending_candidates.update(
            search.letters(rune) + search.letters(cell.rune) + (ZWNJ,)
            for rune in search.preds.get((config, cell), [])[:ZWNJ_CONTEXT_WIDTH]
        )
    for codepoints, slots in _settled(settle, ending_candidates, config):
        letters = [slot for slot in slots if not isinstance(slot, str)]
        if letters and isinstance(slots[-1], str) and letters[-1] in lefts:
            found = codepoints[:-1]
            if letters[-1] not in endings or len(found) < len(endings[letters[-1]]):
                endings[letters[-1]] = found
    start_candidates: set[tuple[int, ...]] = set()
    for cell in rights:
        start_candidates.add((ZWNJ,) + search.letters(cell.rune))
        start_candidates.update(
            (ZWNJ,) + search.letters(cell.rune) + search.letters(rune)
            for rune in search.succs.get((config, cell), [])[:ZWNJ_CONTEXT_WIDTH]
        )
    for codepoints, slots in _settled(settle, start_candidates, config):
        letters = [slot for slot in slots if not isinstance(slot, str)]
        if letters and isinstance(slots[0], str) and letters[0] in rights:
            found = codepoints[1:]
            if letters[0] not in starts or len(found) < len(starts[letters[0]]):
                starts[letters[0]] = found
    composed: set[tuple[int, ...]] = set()
    for index in waiting:
        for left, right in members[index]:
            if left in endings and right in starts:
                composed.add(endings[left] + (ZWNJ,) + starts[right])
                break
    for codepoints, slots in _settled(settle, composed, config):
        search.offer(WitnessText(codepoints, config, tuple(slots), source="zwnj"), ACROSS_ZWNJ)


def text_notation(spec: ResolvedSpec, slots: Sequence[Slot]) -> str:
    """Return a text in letter-name notation with `data-expect`'s connectors: each glyph's rune (`rune_label`) with its stance's `alt` or `half` trait after it, `~b~`, `~x~`, `~6~` or `~t~` at a join, `|` at a break, and `| ◊ZWNJ |` across a ZWNJ."""
    cells = [slot for slot in slots if not isinstance(slot, str)]
    if not cells:
        return ""

    def token(cell: CellId) -> str:
        traits = spec.runes[cell.rune].stances[cell.stance].traits
        return rune_label(cell.rune) + "".join(f".{trait}" for trait in NOTATION_TRAITS if trait in traits)

    parts = [token(cells[0])]
    for left, right, across in _letter_gaps(slots):
        if across:
            parts.append(" | ◊ZWNJ | ")
        elif left.exit is None:
            parts.append(" | ")
        else:
            parts.append(f" {JOIN_CONNECTORS.get(left.exit, '~' + left.exit + '~')} ")
        parts.append(token(right))
    return "".join(parts)


def distinct_drawings(pair: kern.FacingPair, records: Mapping[CellId, GlyphRecord]) -> list[CellPair]:
    """Return one member of the facing pair for each distinct pair of facing edges among its members (`kern.leading_edge`, `kern.following_edge`), the first in label order: the drawings a side `kern.refine_sides` flags `mixed` puts at one junction, which no record can tell apart (rebuild/KERN-PLAN.md section 3). A pair with no mixed side has one."""
    seen: dict[tuple, CellPair] = {}
    for left, right in pair.members:
        edges = (kern.leading_edge(records[left]), kern.following_edge(records[right]))
        seen.setdefault(edges, (left, right))
    return list(seen.values())


def inventory_document(
    spec: ResolvedSpec,
    resolution: kern.KernResolution,
    cell_glyphs: Mapping[CellId, GlyphRecord],
    witnesses: Witnesses,
    stamp: Mapping[str, str],
    configs: Sequence[str],
) -> dict:
    """Return `kern-inventory.json`'s document, laid out as the module docstring says."""
    records = {**cell_glyphs, **resolution.realized}
    runes = sorted(spec.runes, key=lambda rune: (kern.rune_order_key(spec, rune), rune))
    rune_index = {rune: index for index, rune in enumerate(runes)}
    ordered_cells = sorted(records, key=lambda cell: records[cell].name)
    glyph_index = {cell: index for index, cell in enumerate(ordered_cells)}
    config_index = {config: index for index, config in enumerate(configs)}

    side_cells: dict[kern.Side, list[int]] = {}
    for mapping in (resolution.leading, resolution.following):
        for cell, side in mapping.items():
            side_cells.setdefault(side, []).append(glyph_index[cell])
    sides = sorted(
        side_cells,
        key=lambda side: (side.facing != kern.LEADING, kern.rune_order_key(spec, side.rune), side.name),
    )
    side_index = {side: index for index, side in enumerate(sides)}

    def witness_fields(found: tuple[int, int] | None, members: Sequence[CellPair]) -> dict:
        if found is None:
            member = members[0]
            return {"member": [glyph_index[member[0]], glyph_index[member[1]]], "witness": None, "at": None}
        text_index, gap = found
        left, right, _across = _letter_gaps(witnesses.texts[text_index].slots)[gap]
        return {"member": [glyph_index[left], glyph_index[right]], "witness": text_index, "at": gap}

    def drawing_fields(pair: kern.FacingPair) -> dict:
        if not (pair.leading.mixed or pair.following.mixed):
            return {}
        return {
            "drawings": [
                [glyph_index[left], glyph_index[right]] for left, right in distinct_drawings(pair, records)
            ]
        }

    pairs = [
        {
            "lead": side_index[pair.leading],
            "follow": side_index[pair.following],
            "configs": [config_index[config] for config in pair.configs],
            "reach": list(witnesses.reach[index]),
            "members": len(pair.members),
            **witness_fields(witnesses.pairs.get(index), pair.members),
            **drawing_fields(pair),
        }
        for index, pair in enumerate(resolution.pairs)
    ]
    joined = [
        {
            "lead": rune_index[group.lead],
            "lead_stance": group.lead_stance,
            "follow": rune_index[group.follow],
            "follow_stance": group.follow_stance,
            "height": group.height,
            "configs": [config_index[config] for config in group.configs],
            "members": len(group.members),
            **witness_fields(witnesses.groups.get(index), group.members),
        }
        for index, group in enumerate(witnesses.joined)
    ]

    pair_of = {member: index for index, pair in enumerate(resolution.pairs) for member in pair.members}
    group_of = {member: index for index, group in enumerate(witnesses.joined) for member in group.members}
    texts = []
    for text in witnesses.texts:
        cells = [slot for slot in text.slots if not isinstance(slot, str)]
        positions = place(text.slots, records)
        xs = [position for slot, position in zip(text.slots, positions) if not isinstance(slot, str)]
        gaps = []
        joins = []
        across_gaps = []
        for gap, (left, right, across) in enumerate(_letter_gaps(text.slots)):
            joined_here = left.exit is not None
            gaps.append(-1 if joined_here else pair_of[(left, right)])
            joins.append(group_of[(left, right)] if joined_here else None)
            if across:
                across_gaps.append(gap)
        texts.append(
            {
                "cp": list(text.codepoints),
                "config": config_index[text.config],
                "glyphs": [glyph_index[cell] for cell in cells],
                "x": xs,
                "gaps": gaps,
                "joins": joins,
                "zwnj": across_gaps,
                "manual": text.manual,
                "notation": text_notation(spec, text.slots),
            }
        )

    entries: dict[tuple[int, int], dict] = {}

    def entry(lead: str, follow: str) -> dict:
        key = (rune_index[lead], rune_index[follow])
        if key not in entries:
            entries[key] = {
                "lead": key[0],
                "follow": key[1],
                "pairs": [],
                "rep": None,
                "rep_member": None,
                "joined": [],
                "manual": [],
            }
        return entries[key]

    for index, pair in enumerate(resolution.pairs):
        entry(pair.leading.rune, pair.following.rune)["pairs"].append(index)
    for index, group in enumerate(witnesses.joined):
        entry(group.lead, group.follow)["joined"].append(index)
    for (lead, follow), (index, (left, right)) in witnesses.representative.items():
        found = entry(lead, follow)
        found["rep"] = index
        found["rep_member"] = [glyph_index[left], glyph_index[right]]
    for found in entries.values():
        if found["rep"] is None and found["pairs"]:
            found["rep"] = max(
                found["pairs"], key=lambda index: (len(resolution.pairs[index].members), -index)
            )
            found["rep_member"] = pairs[found["rep"]]["member"]
    for text_index, text in enumerate(witnesses.texts):
        if not text.manual:
            continue
        for left, right, _across in _letter_gaps(text.slots):
            key = (rune_index[left.rune], rune_index[right.rune])
            if key in entries and text_index not in entries[key]["manual"]:
                entries[key]["manual"].append(text_index)

    return {
        "format": INVENTORY_FORMAT,
        "stamp": dict(stamp),
        "ink_x": geometry.INK_X_OFFSET,
        "configs": list(configs),
        "runes": [
            {"name": rune, "label": rune_label(rune), "letters": list(rune_letters(spec, rune))}
            for rune in runes
        ],
        "glyphs": [
            {
                "name": records[cell].name,
                "rune": rune_index[cell.rune],
                "rows": list(records[cell].bitmap),
                "y": records[cell].y_offset,
                "adv": kern.advance(records[cell]),
            }
            for cell in ordered_cells
        ],
        "sides": [
            {
                "facing": side.facing,
                "name": side.name,
                "rune": rune_index[side.rune],
                "stance": side.stance,
                "state": side.state,
                "bitmap": side.bitmap,
                "mixed": side.mixed,
                "cells": sorted(side_cells[side]),
            }
            for side in sides
        ],
        "pairs": pairs,
        "joined": joined,
        "texts": texts,
        "rune_pairs": [entries[key] for key in sorted(entries)],
    }


def check_document(document: object) -> list[str]:
    """Return every way `document` breaks the inventory's shape, as the module docstring lays it out: its keys, the indexes each entry names, the lengths that must agree, and that each witness's gap holds its own pair (a facing pair at a break, -1 at a joined group's join). An empty list means the document holds."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["the inventory is not a JSON object"]
    if document.get("format") != INVENTORY_FORMAT:
        errors.append(f"format is {document.get('format')!r}, not {INVENTORY_FORMAT!r}")
    stamp = document.get("stamp")
    if not isinstance(stamp, dict) or not all(
        isinstance(stamp.get(key), str) for key in ("tables", "manual")
    ):
        errors.append("stamp must name the tables and the Manual pages")
    if not isinstance(document.get("ink_x"), int):
        errors.append("ink_x must be an integer")
    lists = {}
    for key in ("configs", "runes", "glyphs", "sides", "pairs", "joined", "texts", "rune_pairs"):
        value = document.get(key)
        if not isinstance(value, list):
            errors.append(f"{key} must be a list")
            value = []
        lists[key] = value
    if errors:
        return errors
    counts = {key: len(value) for key, value in lists.items()}

    def index_ok(value: object, key: str) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < counts[key]

    for number, rune in enumerate(lists["runes"]):
        if (
            not isinstance(rune, dict)
            or not isinstance(rune.get("name"), str)
            or not isinstance(rune.get("label"), str)
        ):
            errors.append(f"runes[{number}] must have a name and a label")
    for number, glyph in enumerate(lists["glyphs"]):
        if not isinstance(glyph, dict):
            errors.append(f"glyphs[{number}] is not an object")
            continue
        rows = glyph.get("rows")
        if not index_ok(glyph.get("rune"), "runes"):
            errors.append(f"glyphs[{number}] names no rune")
        if not isinstance(rows, list) or not all(
            isinstance(row, str) and set(row) <= {"#", " "} for row in rows
        ):
            errors.append(f"glyphs[{number}].rows must be strings of `#` and blanks")
        if not isinstance(glyph.get("y"), int) or not isinstance(glyph.get("adv"), int):
            errors.append(f"glyphs[{number}] must have an integer y and adv")
    for number, side in enumerate(lists["sides"]):
        if not isinstance(side, dict) or side.get("facing") not in (kern.LEADING, kern.FOLLOWING):
            errors.append(f"sides[{number}] must face leading or following")
            continue
        if not index_ok(side.get("rune"), "runes"):
            errors.append(f"sides[{number}] names no rune")
        if not all(index_ok(cell, "glyphs") for cell in side.get("cells") or [None]):
            errors.append(f"sides[{number}].cells must name glyphs")

    def check_witness(where: str, item: dict, join: bool, owner: int) -> None:
        member = item.get("member")
        if (
            not isinstance(member, list)
            or len(member) != 2
            or not all(index_ok(cell, "glyphs") for cell in member)
        ):
            errors.append(f"{where}.member must name two glyphs")
        witness, at = item.get("witness"), item.get("at")
        if witness is None:
            if at is not None:
                errors.append(f"{where} has a gap but no witness text")
            return
        if not index_ok(witness, "texts"):
            errors.append(f"{where}.witness names no text")
            return
        text = lists["texts"][witness]
        gaps = text.get("gaps") if isinstance(text, dict) else None
        if not isinstance(gaps, list) or not isinstance(at, int) or not 0 <= at < len(gaps):
            errors.append(f"{where}.at is no gap of its witness")
            return
        if gaps[at] != (-1 if join else owner):
            errors.append(f"{where}'s witness does not hold it at gap {at}")
        glyphs = text.get("glyphs")
        if isinstance(member, list) and isinstance(glyphs, list) and glyphs[at : at + 2] != member:
            errors.append(f"{where}.member is not the two glyphs at its witness's gap")

    for number, pair in enumerate(lists["pairs"]):
        where = f"pairs[{number}]"
        if not isinstance(pair, dict):
            errors.append(f"{where} is not an object")
            continue
        lead, follow = pair.get("lead"), pair.get("follow")
        if not index_ok(lead, "sides") or lists["sides"][lead].get("facing") != kern.LEADING:
            errors.append(f"{where}.lead names no leading side")
        if not index_ok(follow, "sides") or lists["sides"][follow].get("facing") != kern.FOLLOWING:
            errors.append(f"{where}.follow names no following side")
        if not pair.get("configs") or not all(
            index_ok(config, "configs") for config in pair.get("configs", [])
        ):
            errors.append(f"{where}.configs must name configurations")
        reach = pair.get("reach")
        if not reach or not isinstance(reach, list) or not set(reach) <= set(REACHES):
            errors.append(f"{where}.reach must be some of {', '.join(REACHES)}")
        if not isinstance(pair.get("members"), int) or pair["members"] < 1:
            errors.append(f"{where}.members must count at least one member")
        check_witness(where, pair, False, number)
        if "drawings" in pair:
            drawings = pair["drawings"]
            mixed = any(
                index_ok(pair.get(key), "sides") and lists["sides"][pair[key]].get("mixed")
                for key in ("lead", "follow")
            )
            if not mixed:
                errors.append(f"{where} carries drawings but no mixed side")
            if not (
                isinstance(drawings, list)
                and drawings
                and all(
                    isinstance(drawing, list)
                    and len(drawing) == 2
                    and all(index_ok(glyph, "glyphs") for glyph in drawing)
                    for drawing in drawings
                )
            ):
                errors.append(f"{where}.drawings must list members, two glyphs each")
    for number, group in enumerate(lists["joined"]):
        where = f"joined[{number}]"
        if not isinstance(group, dict):
            errors.append(f"{where} is not an object")
            continue
        if not index_ok(group.get("lead"), "runes") or not index_ok(group.get("follow"), "runes"):
            errors.append(f"{where} must name two runes")
        if not isinstance(group.get("height"), str):
            errors.append(f"{where}.height must name a height")
        check_witness(where, group, True, number)
    for number, text in enumerate(lists["texts"]):
        where = f"texts[{number}]"
        if not isinstance(text, dict):
            errors.append(f"{where} is not an object")
            continue
        glyphs, xs, gaps = text.get("glyphs"), text.get("x"), text.get("gaps")
        if (
            not isinstance(glyphs, list)
            or not glyphs
            or not all(index_ok(glyph, "glyphs") for glyph in glyphs)
        ):
            errors.append(f"{where}.glyphs must name glyphs")
            continue
        if not isinstance(xs, list) or len(xs) != len(glyphs) or not all(isinstance(x, int) for x in xs):
            errors.append(f"{where}.x must give one integer position per glyph")
        if not isinstance(gaps, list) or len(gaps) != len(glyphs) - 1:
            errors.append(f"{where}.gaps must give one entry per gap")
        elif not all(gap == -1 or index_ok(gap, "pairs") for gap in gaps):
            errors.append(f"{where}.gaps must name facing pairs or -1")
        else:
            joins = text.get("joins")
            if (
                not isinstance(joins, list)
                or len(joins) != len(gaps)
                or not all(
                    (gap == -1 and index_ok(group, "joined")) or (gap != -1 and group is None)
                    for gap, group in zip(gaps, joins)
                )
            ):
                errors.append(f"{where}.joins must name a joined group at each join and null at each break")
        if not all(isinstance(gap, int) and 0 <= gap < len(glyphs) - 1 for gap in text.get("zwnj", [])):
            errors.append(f"{where}.zwnj must name gaps")
        if not index_ok(text.get("config"), "configs"):
            errors.append(f"{where}.config names no configuration")
        if not isinstance(text.get("cp"), list) or not isinstance(text.get("notation"), str):
            errors.append(f"{where} must have its code points and its notation")
    for number, found in enumerate(lists["rune_pairs"]):
        where = f"rune_pairs[{number}]"
        if (
            not isinstance(found, dict)
            or not index_ok(found.get("lead"), "runes")
            or not index_ok(found.get("follow"), "runes")
        ):
            errors.append(f"{where} must name two runes")
            continue
        pair_ids = found.get("pairs", [])
        if not all(index_ok(index, "pairs") for index in pair_ids):
            errors.append(f"{where}.pairs must name facing pairs")
            continue
        for index in pair_ids:
            pair = lists["pairs"][index]
            if (
                not isinstance(pair, dict)
                or not index_ok(pair.get("lead"), "sides")
                or not index_ok(pair.get("follow"), "sides")
            ):
                continue
            runes = (lists["sides"][pair["lead"]].get("rune"), lists["sides"][pair["follow"]].get("rune"))
            if runes != (found["lead"], found["follow"]):
                errors.append(f"{where} lists pairs[{index}], which faces other runes")
        rep = found.get("rep")
        if (rep is None) != (not pair_ids) or (rep is not None and rep not in pair_ids):
            errors.append(f"{where}.rep must be one of its pairs, and null only when it has none")
        member = found.get("rep_member")
        if rep is None:
            if member is not None:
                errors.append(f"{where}.rep_member must be null with no representative")
        elif not (
            isinstance(member, list)
            and len(member) == 2
            and all(index_ok(glyph, "glyphs") for glyph in member)
        ):
            errors.append(f"{where}.rep_member must name two glyphs")
        elif (lists["glyphs"][member[0]].get("rune"), lists["glyphs"][member[1]].get("rune")) != (
            found["lead"],
            found["follow"],
        ):
            errors.append(f"{where}.rep_member must be two glyphs of its runes")
        if not all(index_ok(index, "joined") for index in found.get("joined", [])):
            errors.append(f"{where}.joined must name joined groups")
        if not all(
            index_ok(index, "texts") and lists["texts"][index].get("manual")
            for index in found.get("manual", [])
        ):
            errors.append(f"{where}.manual must name the Manual's texts")
    return errors


def check_positions(
    document: Mapping,
    shape: Callable[[str, frozenset[str]], list[dict]],
    copies: Mapping[str, str],
    overlay: Iterable[str],
    pixel: int = geometry.PIXEL,
) -> list[str]:
    """Shape every text of `document` through `shape` (HarfBuzz over the compiled font with `kern` off) and return each text whose glyphs or pen positions differ from the inventory's: a letter's glyph is its cell's, or under an overlay configuration (`overlay`, `kern.overlay_configs`) its letter's copy (`copies`), and a ZWNJ's is `uni200C` or `space`. Positions are in font units, `pixel` to a pixel."""
    problems: list[str] = []
    glyphs = document["glyphs"]
    runes = document["runes"]
    configs = document["configs"]
    overlays = frozenset(overlay)
    for number, text in enumerate(document["texts"]):
        config = configs[text["config"]]
        features = features_for_config(config)
        expected: list[tuple[frozenset[str], int]] = []
        across = set(text["zwnj"])
        for position, (glyph, x) in enumerate(zip(text["glyphs"], text["x"])):
            name = glyphs[glyph]["name"]
            if config in overlays:
                name = copies.get(runes[glyphs[glyph]["rune"]]["name"], name)
            expected.append((frozenset({name}), x))
            if position in across:
                expected.append((ZWNJ_SHAPED_NAMES, text["x"][position + 1]))
        shaped = shape("".join(chr(codepoint) for codepoint in text["cp"]), features)
        pen = 0
        found: list[tuple[str, int, int]] = []
        for slot in shaped:
            found.append((slot["name"], pen + slot["x_offset"], slot["y_offset"]))
            pen += slot["x_advance"]
        mismatch = len(found) != len(expected) or any(
            name not in names or x != want * pixel or y != 0
            for (name, x, y), (names, want) in zip(found, expected)
        )
        if mismatch:
            problems.append(
                f"texts[{number}] {text['notation']} under {config}: HarfBuzz gives {[(name, x // pixel) for name, x, _y in found]}, the inventory {[(sorted(names), want) for names, want in expected]}"
            )
    return problems


@dataclass(frozen=True)
class InventoryOutcome:
    """What `write_inventory` did: kept the file whose stamp matched, or built it, with the counts its timing line reports."""

    kept: bool
    texts: int = 0
    pairs: int = 0
    sources: Mapping[str, int] = field(default_factory=dict)
    settled: int = 0

    def tokens(self) -> str:
        if self.kept:
            return "kept=true"
        sources = " ".join(f"{name}={count}" for name, count in sorted(self.sources.items()))
        return f"facing_pairs={self.pairs} texts={self.texts} settled={self.settled} {sources}"


def stamp_of(path: Path) -> dict | None:
    """Return the stamp of the inventory at `path`, or None when it is missing, does not parse, or is another format's."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(document, dict) or document.get("format") != INVENTORY_FORMAT:
        return None
    stamp = document.get("stamp")
    return stamp if isinstance(stamp, dict) else None


def write_inventory(
    spec: ResolvedSpec,
    tables: Mapping[str, object],
    cell_glyphs: Mapping[CellId, GlyphRecord],
    resolution: kern.KernResolution,
    ss10_copies: Mapping[str, str] | None,
    out_dir: Path,
    tables_inputs: str,
    font_path: Path | None = None,
    *,
    repo_root: Path = REPO_ROOT,
    settle: Settle | None = None,
) -> InventoryOutcome:
    """Write `kern-inventory.json` under `out_dir` for the tables stamped `tables_inputs`, unless the file there already carries this stamp, and return what it did. It finds the witnesses (`find_witnesses`, through `settle`, the kernel by default), assembles the document, checks it (`check_document`) and, given the compiled font, checks every text against HarfBuzz with `kern` off (`check_positions`), raising `KernInventoryError` on either before it writes."""
    pages = read_manual_pages(repo_root)
    stamp = inventory_stamp(tables_inputs, pages)
    path = Path(out_dir) / KERN_INVENTORY_JSON
    if stamp_of(path) == stamp:
        return InventoryOutcome(kept=True)
    counted = [0]
    run = settle or kernel_settle(spec)

    def counting(texts: Sequence[tuple[int, ...]], config: str) -> list[list[Slot] | None]:
        counted[0] += len(texts)
        return run(texts, config)

    letters = [rune.codepoint for rune in spec.runes.values() if rune.codepoint is not None]
    witnesses = find_witnesses(
        spec, resolution, tables, cell_glyphs, ss10_copies, manual_words(pages, letters), counting
    )
    configs = list(dict.fromkeys([*tables, *kern.overlay_configs(spec)]))
    document = inventory_document(spec, resolution, cell_glyphs, witnesses, stamp, configs)
    errors = check_document(document)
    if errors:
        raise KernInventoryError("the kern inventory breaks its own shape: " + "; ".join(errors[:5]))
    if font_path is not None:
        from rebuild.pipeline.shapers import Shaper

        shaper = Shaper(Path(font_path))
        problems = check_positions(
            document,
            lambda text, features: shaper.shape(text, features, kern=False),
            ss10_copies or {},
            kern.overlay_configs(spec),
        )
        if problems:
            raise KernInventoryError(
                f"{len(problems)} kern inventory text(s) disagree with HarfBuzz on {font_path} with kern off: "
                + "; ".join(problems[:3])
            )
    output_sink.replace_output(
        path, json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", keep_if_equal=True
    )
    return InventoryOutcome(
        kept=False,
        texts=len(witnesses.texts),
        pairs=len(resolution.pairs),
        sources=witnesses.sources(),
        settled=counted[0],
    )


def mint_cells(
    spec: ResolvedSpec, tables: Mapping[str, DecisionTable | tuple[DecisionTable, ...]]
) -> dict[CellId, GlyphRecord]:
    """Return the glyph record of every cell the tables reach that the spec has, named by its label, as `run_m1.mint_cell_glyphs` mints them for the build; the command line needs them without the build's driver, which this module may not import (rebuild/test_build_code_closure.py), and rebuild/test_kern_inventory.py checks that the two agree."""
    from rebuild.pipeline import surface

    cells: set[CellId] = set()
    for entry in tables.values():
        decision = entry[0] if isinstance(entry, (tuple, list)) else entry
        cells.update(cell for cell in decision.reachable_cells() if cell.rune in spec.runes)
    return {
        cell: geometry.realize(spec, surface.resolve_cell(spec, cell), name=cell_label(spec, cell))
        for cell in sorted(cells, key=lambda one: cell_label(spec, one))
    }


def letter_copies(spec: ResolvedSpec) -> dict[str, str]:
    """Return each letter's ss10 copy name, for the letters `run_m1.mint_raw_glyphs` gives a copy: the runes with a code point, which leaves out the ligatures."""
    from rebuild.pipeline.model import ss10_copy_name

    return {
        name: ss10_copy_name(name)
        for name, rune in spec.runes.items()
        if not rune.sequence and rune.codepoint is not None
    }


def main(argv: list[str] | None = None) -> None:
    """Build the inventory over the tables and the font under a build directory into `--out`, for development, as the module docstring says. The inventory is stamped with the tables' own stamp, so the review server reports it as built over those tables whether or not the sources on disk still give them."""
    from rebuild.pipeline import conform, spec_load
    from rebuild.pipeline import table as table_module

    parser = argparse.ArgumentParser(
        prog="python -m rebuild.pipeline.kern_inventory", description=main.__doc__
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="the directory to write kern-inventory.json into"
    )
    parser.add_argument(
        "--tables",
        type=Path,
        default=REPO_ROOT / "rebuild" / "out" / "m1",
        help="the build directory to read the tables and the font from",
    )
    args = parser.parse_args(argv)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", spec_load.SpecWarning)
        spec = spec_load.load_default_spec()
    tables: dict[str, tuple] = {}
    inputs: str | None = None
    for config in conform.SETTLEMENT_CONFIGS:
        stamp, decision = table_module.read_windows_head(args.tables, config, cells_only=True)
        if inputs is None:
            inputs = stamp
        if stamp != inputs:
            raise SystemExit(f"the tables under {args.tables} carry more than one stamp; run the M1 build")
        tables[config] = (decision, table_module.read_join_tsv(args.tables / f"joins-{config}.tsv"))
    assert inputs is not None
    start = time.perf_counter()
    cell_glyphs = mint_cells(spec, tables)
    copies = letter_copies(spec)
    resolution = kern.resolve(spec, tables, cell_glyphs, copies)
    args.out.mkdir(parents=True, exist_ok=True)
    outcome = write_inventory(
        spec, tables, cell_glyphs, resolution, copies, args.out, inputs, args.tables / "M1.otf"
    )
    print(f"kern_inventory {time.perf_counter() - start:.1f}s {outcome.tokens()}")


if __name__ == "__main__":
    main()
