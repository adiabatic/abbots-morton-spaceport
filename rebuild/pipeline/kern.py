"""The kern stage (rebuild/KERN-PLAN.md sections 2 to 4, `doc/rebuild-design.md` §12): it resolves the runes' `kern:` records over the pairs of settled cells that do not join, and writes the coverage table `kern.tsv`. `emit_gpos.kern_plan` turns the resolution into the font's `kern` lookup, and the defect gates place each kerned break where the font draws it (`member_values`).

`run_m1` calls `run_stage` once the tables exist and the cell glyphs are minted, before the defect gates. Its inputs:

- The spec's kern records (`model.KernBlock`), which `spec_load` parses and checks, and which the kernel's spec dump leaves out.
- The join tables of every settlement configuration, as the build parsed them from `joins-<config>.tsv`. `check_join_rows` asserts over every row the invariant the design rests on: a row is a `break` exactly when its left cell has no live exit and its right cell has no live entry. A violation raises `KernInternalError` naming the row.
- The pairs a ZWNJ separates, which no join row records. Each configuration's windows head lists the cells settled immediately before a ZWNJ and the cells settled immediately after one (`table.DecisionTable.zwnj_before` and `zwnj_after`, from the crate's `fold::zwnj_cells`), and every cell of the first set meets every cell of the second across a ZWNJ in some text, so `zwnj_breaks` takes their product as that configuration's breaks. A cell after a ZWNJ is the `.locked` copy of a word-initial cell (or, for a rune with no entry on any stance, the word-initial cell itself), drawn and spaced as that cell is, so it lands on the following side its twin is on whenever both meet a break. A head that does not list the ZWNJ cells, or lists none on one side although the spec can settle a letter beside a ZWNJ (`zwnj_reachable`), or a cell before a ZWNJ with a live exit or after one with a live entry, raises `KernInternalError`.
- The letters' ss10 copies (`run_m1.mint_raw_glyphs`). Under the isolated overlay nothing forms or joins, so every ordered pair of letters is a break between their copies (`ss10_breaks`). A copy is drawn from its letter's isolated cell (`geometry.isolated_cell`) with its anchors stripped, so the stage treats the copy as that cell: it is a member of the cell's sides, records match it as they match the cell, and its pairs carry each overlay configuration (`overlay_configs`). A copy whose isolated cell has a live exit would break that rule, and raises `KernInternalError`; no rune draws one.
- The realized glyph records, which give each cell its ink and its advance as `tools/build_font.py` compiles them: the drawing's width plus `INK_X_OFFSET` blank pixels on each side, less the Senior tightening's pixel (`SENIOR_TIGHTENING`).

**Sides** (KERN-PLAN §3). A rune's leading cells are those on the left of some break, and its following cells those on the right of one. A leading cell's facing edge is, for each bitmap row with ink, the distance from its rightmost ink to its advance edge; a following cell's is, for each such row, the x of its leftmost ink, left bearing included (`leading_edge`, `following_edge`). `refine_sides` splits one rune's cells on one side top down, so every side is named by the record grammar: when every cell has one edge, the rune is one side; otherwise each stance whose cells share an edge is a side; otherwise each far-side state of the stance (the entry state for a leading cell, the exit state for a following cell, a height or `none`) whose cells share an edge is a side; otherwise each bound drawing of that state (the drawing the cell's last `bind` adjustment names, a `padded-left-N` copy among them, or none) is a side. A side whose cells still differ after the last split differs only in an extension, contraction or trim amount, which no record can name, and is flagged `mixed`.

**Side names** spell the keys a record would use, in brackets after the rune: `qsNo` for the rune as a whole, `qsNo[flipped]` for a stance, `qsGay[sole entry=baseline]` or `qsGay[sole exit=none]` for a state of a stance on the leading or the following side, and `qsTea[half exit=x-height bitmap=padded-left-1]` for a bound drawing. A side split off at the last level with no bound drawing keeps its state's name, so `qsOut_qsTea[sole entry=baseline]` beside `qsOut_qsTea[sole entry=baseline bitmap=low-bend]` is the state's cells less the bound ones.

**Facing pairs.** A facing pair is a leading side and a following side with at least one reachable break between their cells, in any settlement configuration, across a ZWNJ, or under the overlay. `resolve` keeps each one's configurations and its member cell pairs.

**Matching and precedence** (KERN-PLAN §2). A record in rune R's block matches a break when the left cell is R's and fits the record's `stance`, `entry` and `bitmap`, and the right cell fits `right`: its `family` and `class` (a predicate class, or a group of the record's own rune, by the loader's resolved membership), its settled `stance`, its `exit` state and its bound drawing, and none of its `except` entries. A right-facing family is literal, so a ligature follower is named explicitly. A record's match set is every break it matches, the breaks it reaches through an inheriting ligature included. Among the records that match a break, the one whose match set lies inside every other's decides, and values never add. Two matching records with different values whose match sets are equal, or neither of which lies inside the other, are `E-KERN-INCOMPARABLE`; two with one value never conflict, and either may be reported as the decider.

**Ligature inheritance.** A ligature stance in its `KernBlock.inherits` (the stances `outgoing.stance` maps, explicitly or by default) takes the kern records of the trailing component that name that component stance or no stance and set neither `entry` nor `bitmap`. They apply to the ligature stance's own leading cells and keep their `right`. Every record of the ligature's own block outranks every inherited one: inherited records are consulted at a break only when no own record matches it. A stance that opts out with `outgoing: {exception: …}` inherits nothing. The trailing component's own block is the one read, so a ligature does not pass on what it inherits.

**Errors.** A record that matches no break is `E-KERN-UNMATCHED`, unless none of the runes its `right` can name is in the spec, which only a narrowed alphabet (`spec_load.narrowed`) produces. Both errors are collected and raised together as one `KernError`. A facing pair whose member cell pairs resolve to different values is `E-KERN-NARROWER`, raised as a `KernError` of its own once those two are clear: a record that tells apart cells sharing one facing edge is narrower than a facing pair, which KERN-PLAN §3 rules out.

**`kern.tsv`** has a comment line, a column line, then one row per facing pair: the leading rune, the leading side's name, the following rune, the following side's name, `by` (0 when no record matches), the deciding record's provenance (empty when none, with ` (inherited)` after a record a ligature inherits, and comma-separated when one value comes from several records), the configurations that reach the pair (settlement configurations in table order, then the overlay), and which of the two sides is `mixed` (`leading`, `following`, both joined by a comma, or empty). Rows are sorted by the leading rune in code-point order with each ligature after its lead (`rune_order_key`), then the leading side's name, the following rune and the following side's name. The file is written only when its bytes change (`output_sink.write_output`).

**`kern-classes.json`** (`classes_document`, written by `write_classes` once read-back has passed) is the assignment the font was compiled from, in the form the conformance sweep reads: each glyph's leading side and following side by name, and every facing pair with its value, 0 included, as `[leading side, following side, by]`. The sweep checks every shaped break against it (`conform.KernSides`): a break between two glyphs whose sides form no facing pair there is a break the stage never reached, even when each glyph has its sides from other breaks.

`KernResolution` holds what a GPOS class assignment and the editor's inventory need: each settled cell's leading and following side, each `.ss10` copy's source cell, every facing pair with its value, deciders, configurations and members, and the records `resolve` realized for overlay source cells no settlement reaches. `glyph_sides` maps glyph names to sides, copies included, and `member_values` gives each member break of a kerned facing pair its value, which the defect gates place the pair at (`defects.run_gates`). `emit_gpos.kern_plan` builds the GPOS lookup's classes from them.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rebuild.pipeline import geometry, output_sink, surface
from rebuild.pipeline.model import (
    NONE_STATE,
    CellId,
    GlyphRecord,
    KernFollower,
    KernRecord,
    ResolvedSpec,
    feature_config_token,
    parse_adjustment,
)
from rebuild.pipeline.settle import cell_label

KERN_TSV = "kern.tsv"
KERN_CLASSES_JSON = "kern-classes.json"
# The registry's name for the ZWNJ boundary token (rebuild/script.yaml), which `labels` turns into the `uni200C` label the crate's ZWNJ cells are found by.
ZWNJ_TOKEN = "zwnj"
KERN_CLASSES_FORMAT = "ams-m1-kern-classes/2"
TSV_COMMENT = "# kern table: one row per facing pair (rebuild/KERN-PLAN.md section 3)"
TSV_COLUMNS = ("leading", "leading_side", "following", "following_side", "by", "record", "configs", "mixed")

LEADING = "leading"
FOLLOWING = "following"
BREAK = "break"
INHERITED_MARK = " (inherited)"

INK_X_OFFSET = geometry.INK_X_OFFSET
SENIOR_TIGHTENING = 1  # tools/build_font.py trims this many pixels from a Quikscript letter's computed advance (rebuild/KERN-PLAN.md section 1)

Edge = tuple[tuple[int, int], ...]
CellPair = tuple[CellId, CellId]


class KernError(Exception):
    """The kern stage's authoring errors, `E-KERN-UNMATCHED`, `E-KERN-INCOMPARABLE` and `E-KERN-NARROWER`, collected over the whole spec. `issues` holds each one as a `(code, message)` pair, in the order they are reported."""

    def __init__(self, issues: Sequence[tuple[str, str]]):
        self.issues = tuple(issues)
        super().__init__("\n".join(f"{code}: {message}" for code, message in self.issues))


class KernInternalError(AssertionError):
    """An invariant the kern stage rests on does not hold: a join row that breaks while a facing side is live, or joins while one is not; a join row or a ZWNJ cell naming a cell no glyph was minted for; a windows head that lists no ZWNJ cells; a cell before a ZWNJ with a live exit or after one with a live entry; or an ss10 copy drawn from a cell with a live side."""


@dataclass(frozen=True)
class Side:
    """One side of a rune (`refine_sides`): its `facing` (`LEADING` or `FOLLOWING`) and the own-side keys that name it, each None where the side does not narrow it. `state` is the far-side state, the entry state of a leading side and the exit state of a following one. `mixed` marks a side whose cells differ in edge although no record can tell them apart."""

    rune: str
    facing: str
    stance: str | None = None
    state: str | None = None
    bitmap: str | None = None
    mixed: bool = field(default=False, compare=False)

    @property
    def name(self) -> str:
        keys = [] if self.stance is None else [self.stance]
        if self.state is not None:
            keys.append(f"{'entry' if self.facing == LEADING else 'exit'}={self.state}")
        if self.bitmap is not None:
            keys.append(f"bitmap={self.bitmap}")
        return f"{self.rune}[{' '.join(keys)}]" if keys else self.rune


@dataclass(frozen=True)
class Decider:
    """A record that decides a break's value, and whether the break's leading cell takes it from its ligature's trailing component."""

    record: KernRecord
    inherited: bool = False

    def __str__(self) -> str:
        return f"{self.record.provenance}{INHERITED_MARK if self.inherited else ''}"


@dataclass(frozen=True)
class FacingPair:
    """A leading side and a following side with at least one reachable break between their cells: the value its member breaks resolve to, the records that decide it (none when no record matches), the configurations that reach it, and its member cell pairs, sorted by label."""

    leading: Side
    following: Side
    by: int
    deciders: tuple[Decider, ...]
    configs: tuple[str, ...]
    members: tuple[CellPair, ...]

    @property
    def mixed(self) -> str:
        sides = [side.facing for side in (self.leading, self.following) if side.mixed]
        return ",".join(sides)


@dataclass(frozen=True)
class KernResolution:
    """The stage's result: each cell's leading and following side (a cell is on a side only when it meets a break from that side), each overlay copy's source cell by the copy's glyph name, and every facing pair in `kern.tsv` order."""

    leading: Mapping[CellId, Side]
    following: Mapping[CellId, Side]
    ss10_sources: Mapping[str, CellId]
    pairs: tuple[FacingPair, ...]
    realized: Mapping[CellId, GlyphRecord] = field(default_factory=dict)

    def glyph_sides(self, glyphs: Mapping[CellId, GlyphRecord]) -> tuple[dict[str, Side], dict[str, Side]]:
        """Return each glyph name's leading side and following side: the settled cells' glyphs under `glyphs`, and each `.ss10` copy under the sides of the cell it copies."""
        names = {cell: glyphs[cell].name for cell in glyphs}
        leading = {names[cell]: side for cell, side in self.leading.items() if cell in names}
        following = {names[cell]: side for cell, side in self.following.items() if cell in names}
        for copy, source in self.ss10_sources.items():
            if source in self.leading:
                leading[copy] = self.leading[source]
            if source in self.following:
                following[copy] = self.following[source]
        return leading, following

    def member_values(self) -> dict[CellPair, int]:
        """Return each member break of every facing pair whose value is not 0, with that value."""
        return {member: pair.by for pair in self.pairs if pair.by for member in pair.members}


def _ink_rows(record: GlyphRecord) -> list[tuple[int, int, int]]:
    """Return (y, leftmost ink x, rightmost ink x) for each bitmap row of the record that has ink, in glyph-space y."""
    rows = []
    height = len(record.bitmap)
    for index, row in enumerate(record.bitmap):
        columns = [x for x, cell in enumerate(row) if cell == "#"]
        if columns:
            rows.append((record.y_offset + height - 1 - index, min(columns), max(columns)))
    return sorted(rows)


def advance(record: GlyphRecord) -> int:
    """Return the record's compiled advance in pixels: what tools/build_font.py gives a Quikscript letter with no explicit advance, its widest row plus `INK_X_OFFSET` blank pixels on each side, less `SENIOR_TIGHTENING`. Settled cells never carry an explicit advance."""
    if record.advance_width is not None:
        raise KernInternalError(
            f"{record.name} carries an explicit advance; a settled cell takes its advance from its drawing"
        )
    width = max((len(row) for row in record.bitmap), default=0)
    return width + 2 * INK_X_OFFSET - SENIOR_TIGHTENING


def leading_edge(record: GlyphRecord) -> Edge:
    """Return the record's right-hand edge as a leading cell: for each row with ink, the pixels from its rightmost ink to the advance."""
    width = advance(record)
    return tuple((y, width - (INK_X_OFFSET + right + 1)) for y, _left, right in _ink_rows(record))


def following_edge(record: GlyphRecord) -> Edge:
    """Return the record's left-hand edge as a following cell: for each row with ink, the x of its leftmost ink, left bearing included."""
    return tuple((y, INK_X_OFFSET + left) for y, left, _right in _ink_rows(record))


def bound_drawing(cell: CellId) -> str | None:
    """Return the drawing the cell's last `bind` adjustment names (a `bitmaps:` sibling, or a `padded-left-N` copy), or None when it has none."""
    bound = None
    for token in cell.adjustments:
        parsed = parse_adjustment(token)
        if parsed[0] == "bind":
            bound = parsed[2]
    return bound


def far_state(cell: CellId, facing: str) -> str:
    """Return the state of the cell's far side: its entry for a leading cell and its exit for a following one, a height or `none`."""
    height = cell.entry if facing == LEADING else cell.exit
    return NONE_STATE if height is None else height


def _grouped(cells: Iterable[CellId], key: Callable[[CellId], str | None]) -> dict[str | None, list[CellId]]:
    groups: dict[str | None, list[CellId]] = {}
    for cell in cells:
        groups.setdefault(key(cell), []).append(cell)
    return groups


def refine_sides(rune: str, facing: str, edges: Mapping[CellId, Edge]) -> dict[CellId, Side]:
    """Split one rune's cells on one side (`edges` maps each to its facing edge) into sides, top down as the module docstring says, and return each cell's side."""
    cells = sorted(edges, key=_cell_key)

    def uniform(group: list[CellId]) -> bool:
        return len({edges[cell] for cell in group}) == 1

    if uniform(cells):
        return {cell: Side(rune, facing) for cell in cells}
    sides: dict[CellId, Side] = {}
    for stance, stance_cells in _grouped(cells, lambda cell: cell.stance).items():
        if uniform(stance_cells):
            sides.update({cell: Side(rune, facing, stance) for cell in stance_cells})
            continue
        for state, state_cells in _grouped(stance_cells, lambda cell: far_state(cell, facing)).items():
            if uniform(state_cells):
                sides.update({cell: Side(rune, facing, stance, state) for cell in state_cells})
                continue
            for bitmap, leaf in _grouped(state_cells, bound_drawing).items():
                side = Side(rune, facing, stance, state, bitmap, mixed=not uniform(leaf))
                sides.update({cell: side for cell in leaf})
    return sides


def _cell_key(cell: CellId) -> tuple:
    return (cell.rune, cell.stance, cell.entry or "", cell.exit or "", cell.adjustments)


def _rows(joins: object) -> Iterable:
    return getattr(joins, "rows", ())


def _joins_of(entry: object) -> object:
    return entry[1] if isinstance(entry, (tuple, list)) else entry


def _decision_of(entry: object) -> object | None:
    return entry[0] if isinstance(entry, (tuple, list)) else None


def check_join_rows(
    tables: Mapping[str, object], cells_by_label: Mapping[str, CellId]
) -> dict[CellPair, list[str]]:
    """Assert the break invariant over every row of every configuration's join table and return the breaks, each cell pair with the configurations it breaks in, in table order. `tables` maps a configuration to its join table or to its `(decision, joins)` pair, as `run_m1.build_tables` returns them."""
    breaks: dict[CellPair, list[str]] = {}
    for config, entry in tables.items():
        for row in _rows(_joins_of(entry)):
            left = cells_by_label.get(row.left)
            right = cells_by_label.get(row.right)
            if left is None or right is None:
                missing = row.left if left is None else row.right
                raise KernInternalError(
                    f"{config} join row {row.left} | {row.right}: no minted glyph is the cell {missing}"
                )
            is_break = row.junction == BREAK
            exit_live = left.exit is not None
            entry_live = right.entry is not None
            if is_break == exit_live or is_break == entry_live:
                raise KernInternalError(
                    f"{config} join row {row.left}\t{row.right}\t{row.junction}: a row is a break exactly when its left cell has no live exit and its right cell has no live entry (rebuild/KERN-PLAN.md section 2)"
                )
            if is_break:
                configs = breaks.setdefault((left, right), [])
                if config not in configs:
                    configs.append(config)
    return breaks


def zwnj_reachable(spec: ResolvedSpec) -> bool:
    """Return whether the spec can settle a letter beside a ZWNJ: its registry has the ZWNJ boundary token (`ZWNJ_TOKEN`) and its alphabet has a letter. A text of one letter and a ZWNJ then settles a cell before the ZWNJ, and a ZWNJ and one letter a cell after it, so every configuration's head lists at least one of each. Only a registry without the token, such as a test alphabet's, gives a spec that cannot."""
    return ZWNJ_TOKEN in spec.registry.boundary_tokens and any(
        rune.codepoint is not None for rune in spec.runes.values()
    )


def zwnj_breaks(
    tables: Mapping[str, object], cells_by_label: Mapping[str, CellId], reachable: bool = True
) -> dict[CellPair, list[str]]:
    """Return the breaks a ZWNJ separates, each cell pair with the configurations it occurs in: per configuration, every cell its windows head lists before a ZWNJ against every cell it lists after one (the module docstring says why the product is exact). A configuration handed a join table alone, with no decision table, contributes none. `reachable` says whether the spec can settle a letter beside a ZWNJ at all (`zwnj_reachable`); when it can, every head lists cells on both sides. Raises `KernInternalError` for a decision table whose head does not list the ZWNJ cells, or lists none on one side when `reachable` is set, a label no minted glyph carries, a cell before a ZWNJ with a live exit, or a cell after one with a live entry."""
    breaks: dict[CellPair, list[str]] = {}
    for config, entry in tables.items():
        decision = _decision_of(entry)
        if decision is None:
            continue
        before_labels = getattr(decision, "zwnj_before", None)
        after_labels = getattr(decision, "zwnj_after", None)
        if before_labels is None or after_labels is None:
            raise KernInternalError(
                f"{config}'s windows head lists no ZWNJ cells; the tables were written by a crate that predates them, so rebuild them (run_m1 --fresh-tables)"
            )
        if reachable and not (before_labels and after_labels):
            empty = " and ".join(
                where for where, labels in (("before", before_labels), ("after", after_labels)) if not labels
            )
            raise KernInternalError(
                f"{config}'s windows head lists no cell {empty} a ZWNJ, though the registry has the ZWNJ token and the alphabet has letters, so some letter settles beside one; the crate's ZWNJ cells (fold::zwnj_cells) missed them, and every pair across a ZWNJ would go unkerned"
            )
        sides: list[list[CellId]] = []
        for where, labels in (("before", before_labels), ("after", after_labels)):
            cells = []
            for label in sorted(labels):
                cell = cells_by_label.get(label)
                if cell is None:
                    raise KernInternalError(
                        f"{config}: the cell {label} settles {where} a ZWNJ, but no minted glyph is that cell"
                    )
                live = cell.exit if where == "before" else cell.entry
                if live is not None:
                    raise KernInternalError(
                        f"{config}: {label} settles {where} a ZWNJ with a live {'exit' if where == 'before' else 'entry'}; a ZWNJ is a boundary, so a cell beside one has no live side facing it"
                    )
                cells.append(cell)
            sides.append(cells)
        for left in sides[0]:
            for right in sides[1]:
                configs = breaks.setdefault((left, right), [])
                if config not in configs:
                    configs.append(config)
    return breaks


def overlay_configs(spec: ResolvedSpec) -> tuple[str, ...]:
    """Return the configuration token of each feature the registry marks `overlay: isolated` (`model.isolated_overlay_active`), sorted: the configurations under which every pair of letters is a break between their ss10 copies. `conform.OVERLAY_CONFIGS` restates the same set, and `rebuild/test_conform.py` checks it against the registry."""
    return tuple(
        sorted(
            feature_config_token({tag})
            for tag, info in spec.registry.features.items()
            if info.overlay == "isolated"
        )
    )


def ss10_breaks(
    spec: ResolvedSpec, ss10_copies: Mapping[str, str]
) -> tuple[dict[CellPair, list[str]], dict[str, CellId]]:
    """Return the breaks the overlay reaches, every ordered pair of the letters `ss10_copies` maps to a copy (`run_m1.mint_raw_glyphs`: each letter with a code point) as a pair of their isolated cells with the overlay configurations, and each copy's source cell by the copy's glyph name. A source cell with a live exit raises `KernInternalError`, because its copy keeps the exit's ink with no exit to join."""
    configs = list(overlay_configs(spec))
    sources: dict[str, CellId] = {}
    letters: list[CellId] = []
    for rune in spec.runes:
        copy = ss10_copies.get(rune)
        if copy is None:
            continue
        source = geometry.isolated_cell(spec, rune)
        if source.entry is not None or source.exit is not None:
            raise KernInternalError(
                f"{copy} copies {cell_label(spec, source)}, which has a live side; an ss10 copy kerns as its source cell only when that cell has neither"
            )
        sources[copy] = source
        letters.append(source)
    breaks = {(left, right): list(configs) for left in letters for right in letters} if configs else {}
    return breaks, sources


class _Matcher:
    """Decides whether a kern record fits a cell pair, with class membership as the loader resolved it."""

    def __init__(self, spec: ResolvedSpec):
        self.spec = spec
        self.runes = frozenset(spec.runes)

    def members(self, record: KernRecord, klass: str) -> frozenset[str]:
        members = self.spec.registry.predicate_classes.get(klass)
        if members is None:
            owner = self.spec.runes.get(record.rune)
            members = owner.policy.groups.get(klass) if owner is not None else None
        return members or frozenset()

    def _atom_fits(self, record: KernRecord, follower: KernFollower, cell: CellId) -> bool:
        if follower.family and cell.rune not in follower.family:
            return False
        if follower.klass is not None and cell.rune not in self.members(record, follower.klass):
            return False
        if follower.stance is not None and cell.stance != follower.stance:
            return False
        if follower.exit is not None and far_state(cell, FOLLOWING) != follower.exit:
            return False
        return follower.bitmap is None or bound_drawing(cell) == follower.bitmap

    def right_fits(self, record: KernRecord, cell: CellId) -> bool:
        right = record.right
        return self._atom_fits(record, right, cell) and not any(
            self._atom_fits(record, atom, cell) for atom in right.except_
        )

    def own_fits(self, record: KernRecord, cell: CellId) -> bool:
        if record.stance is not None and cell.stance != record.stance:
            return False
        if record.entry is not None and far_state(cell, LEADING) != record.entry:
            return False
        return record.bitmap is None or bound_drawing(cell) == record.bitmap

    def nameable(self, record: KernRecord) -> frozenset[str]:
        """The runes the record's `right` can name: its families, the members of its class, or every rune when it names neither."""
        named = self.runes if not record.right.family else frozenset(record.right.family)
        if record.right.klass is not None:
            named = named & self.members(record, record.right.klass)
        return named


def describe(record: KernRecord) -> str:
    """Return the record as one flow mapping in its keys' order, `by` last and `why` left out: the form the stage's errors quote and the review server's kern routes show beside a value (`rebuild/review/kern_routes.py`)."""

    def follower(atom: KernFollower) -> str:
        keys = []
        if atom.family:
            keys.append(
                f"family: {atom.family[0] if len(atom.family) == 1 else '[' + ', '.join(atom.family) + ']'}"
            )
        for key, value in (
            ("class", atom.klass),
            ("stance", atom.stance),
            ("exit", atom.exit),
            ("bitmap", atom.bitmap),
        ):
            if value is not None:
                keys.append(f"{key}: {value}")
        if atom.except_:
            keys.append(f"except: [{', '.join(follower(item) for item in atom.except_)}]")
        return "{" + ", ".join(keys) + "}"

    keys = [
        f"{key}: {value}"
        for key, value in (("stance", record.stance), ("entry", record.entry), ("bitmap", record.bitmap))
        if value is not None
    ]
    keys += [f"right: {follower(record.right)}", f"by: {record.by}"]
    return "{" + ", ".join(keys) + "}"


def rune_order_key(spec: ResolvedSpec, rune: str) -> tuple[int, ...]:
    """Return the code points of the rune's components, so the runes sort in code-point order with each ligature after the bare letter it leads. A component with no code point sorts after every one that has one."""
    sequence = spec.runes[rune].sequence if rune in spec.runes else None
    names = sequence or (rune,)
    key = []
    for name in names:
        info = spec.registry.families.get(name)
        codepoint = info.codepoint if info is not None else None
        if codepoint is None and name in spec.runes:
            codepoint = spec.runes[name].codepoint
        key.append(codepoint if codepoint is not None else 0x110000)
    return tuple(key)


def resolve(
    spec: ResolvedSpec,
    tables: Mapping[str, object],
    glyphs: Mapping[CellId, GlyphRecord],
    ss10_copies: Mapping[str, str] | None = None,
) -> KernResolution:
    """Resolve the spec's kern records over the breaks of `tables` (the join rows' breaks and the pairs a ZWNJ separates) and the overlay's pairs of `ss10_copies`, as the module docstring says, and return the sides and facing pairs. Raises `KernError` with every `E-KERN-UNMATCHED` and `E-KERN-INCOMPARABLE` found, or with every `E-KERN-NARROWER` once those are clear, and `KernInternalError` when an invariant fails."""
    records_by_cell = dict(glyphs)
    cells_by_label = {record.name: cell for cell, record in glyphs.items()}
    breaks = check_join_rows(tables, cells_by_label)
    overlay, ss10_sources = ss10_breaks(spec, ss10_copies or {})
    for extra in (zwnj_breaks(tables, cells_by_label, zwnj_reachable(spec)), overlay):
        for pair, configs in extra.items():
            known = breaks.setdefault(pair, [])
            known.extend(config for config in configs if config not in known)
    realized: dict[CellId, GlyphRecord] = {}
    for source in ss10_sources.values():
        if source not in records_by_cell:
            realized[source] = records_by_cell[source] = geometry.realize(
                spec, surface.resolve_cell(spec, source), name=cell_label(spec, source)
            )

    leading, following = _sides(breaks, records_by_cell)
    ordered = sorted(breaks, key=lambda pair: (cell_label(spec, pair[0]), cell_label(spec, pair[1])))
    values, issues = _resolve_breaks(spec, ordered, breaks)
    if issues:
        raise KernError(issues)
    config_order = list(dict.fromkeys([*tables, *overlay_configs(spec)]))
    return KernResolution(
        leading=leading,
        following=following,
        ss10_sources=ss10_sources,
        pairs=_facing_pairs(spec, ordered, breaks, leading, following, values, config_order),
        realized=realized,
    )


def _sides(
    breaks: Mapping[CellPair, list[str]], records: Mapping[CellId, GlyphRecord]
) -> tuple[dict[CellId, Side], dict[CellId, Side]]:
    leading_edges: dict[str, dict[CellId, Edge]] = {}
    following_edges: dict[str, dict[CellId, Edge]] = {}
    for left, right in breaks:
        leading_edges.setdefault(left.rune, {})[left] = leading_edge(records[left])
        following_edges.setdefault(right.rune, {})[right] = following_edge(records[right])
    leading: dict[CellId, Side] = {}
    following: dict[CellId, Side] = {}
    for rune, edges in leading_edges.items():
        leading.update(refine_sides(rune, LEADING, edges))
    for rune, edges in following_edges.items():
        following.update(refine_sides(rune, FOLLOWING, edges))
    return leading, following


@dataclass
class _Value:
    by: int = 0
    deciders: tuple[Decider, ...] = ()


def _resolve_breaks(
    spec: ResolvedSpec, ordered: Sequence[CellPair], breaks: Mapping[CellPair, list[str]]
) -> tuple[dict[CellPair, _Value], list[tuple[str, str]]]:
    """Match every record against every break and resolve each break's value. Returns the values and the errors found, `E-KERN-INCOMPARABLE` first, each kind in a stable order."""
    matcher = _Matcher(spec)
    order = {record: position for position, record in enumerate(_all_records(spec))}
    matches: dict[KernRecord, set[CellPair]] = {record: set() for record in order}
    own: dict[CellPair, list[KernRecord]] = {}
    inherited: dict[CellPair, list[KernRecord]] = {}
    taken: dict[tuple[str, str], list[KernRecord]] = {}
    for pair in ordered:
        left, right = pair
        owner = spec.runes.get(left.rune)
        if owner is None:
            continue
        for record in owner.kern.records:
            if matcher.own_fits(record, left) and matcher.right_fits(record, right):
                matches[record].add(pair)
                own.setdefault(pair, []).append(record)
        stance_key = (left.rune, left.stance)
        if stance_key not in taken:
            taken[stance_key] = _inherited_records(spec, left.rune, left.stance)
        for record in taken[stance_key]:
            if matcher.right_fits(record, right):
                matches[record].add(pair)
                inherited.setdefault(pair, []).append(record)

    inside: dict[tuple[KernRecord, KernRecord], bool] = {}

    def lies_inside(narrow: KernRecord, broad: KernRecord) -> bool:
        key = (narrow, broad)
        if key not in inside:
            inside[key] = matches[narrow] < matches[broad]
        return inside[key]

    incomparable: dict[tuple[KernRecord, KernRecord], str] = {}
    checked: set[tuple[KernRecord, KernRecord]] = set()
    minimal_of: dict[tuple[KernRecord, ...], tuple[KernRecord, ...]] = {}
    values: dict[CellPair, _Value] = {}
    for pair in ordered:
        candidates, from_component = (own[pair], False) if pair in own else (inherited.get(pair, []), True)
        if not candidates:
            values[pair] = _Value()
            continue
        for first, second in itertools.combinations(sorted(candidates, key=order.__getitem__), 2):
            if (first, second) in checked:
                continue
            checked.add((first, second))
            if first.by == second.by or lies_inside(first, second) or lies_inside(second, first):
                continue
            how = (
                "they match exactly the same breaks"
                if matches[first] == matches[second]
                else "neither one's breaks lie inside the other's"
            )
            incomparable[(first, second)] = (
                f"{first.provenance} {describe(first)} and {second.provenance} {describe(second)} both match "
                f"{_pair_text(spec, pair, breaks)}{' through ligature inheritance' if from_component else ''} with different values, and {how}; "
                "narrow one so its breaks lie inside the other's, give both the same value, or remove one (rebuild/KERN-PLAN.md section 2)"
            )
        key = tuple(sorted(candidates, key=order.__getitem__))
        if key not in minimal_of:
            minimal_of[key] = tuple(
                record for record in key if not any(lies_inside(other, record) for other in key)
            )
        minimal = minimal_of[key]
        values[pair] = _Value(
            by=minimal[0].by,
            deciders=tuple(Decider(record, inherited=from_component) for record in minimal),
        )

    issues = [("E-KERN-INCOMPARABLE", message) for message in incomparable.values()]
    for record in order:
        if matches[record] or not (matcher.nameable(record) & matcher.runes):
            continue
        through = _inheritors(spec, record)
        reach = (
            f", nor through the ligature stances that inherit it ({', '.join(through)})" if through else ""
        )
        issues.append(
            (
                "E-KERN-UNMATCHED",
                f"{record.provenance} {describe(record)} matches no break in any configuration{reach}: every pair it names joins, or the two never meet; remove it or correct what it names (rebuild/KERN-PLAN.md section 2)",
            )
        )
    return values, issues


def _all_records(spec: ResolvedSpec) -> list[KernRecord]:
    return [record for rune in spec.runes.values() for record in rune.kern.records]


def inheritable(record: KernRecord, source_stance: str) -> bool:
    """Return whether a ligature stance mapped to the trailing component's `source_stance` takes this record of the component's block: it names that stance or none, and sets neither `entry` nor `bitmap`."""
    return record.entry is None and record.bitmap is None and record.stance in (None, source_stance)


def _inherited_records(spec: ResolvedSpec, rune_name: str, stance: str) -> list[KernRecord]:
    """Return the records a cell of `rune_name` in `stance` takes from its trailing component, none when the rune is not a ligature or the stance is not in its `KernBlock.inherits`."""
    rune = spec.runes.get(rune_name)
    source_stance = rune.kern.inherits.get(stance) if rune is not None else None
    if rune is None or source_stance is None or not rune.sequence:
        return []
    component = spec.runes.get(rune.sequence[-1])
    if component is None:
        return []
    return [record for record in component.kern.records if inheritable(record, source_stance)]


def _inheritors(spec: ResolvedSpec, record: KernRecord) -> list[str]:
    """Return the `rune.stance` of every ligature stance that inherits the record."""
    return [
        f"{rune.name}.{stance}"
        for rune in spec.runes.values()
        if rune.sequence and rune.sequence[-1] == record.rune
        for stance, source_stance in rune.kern.inherits.items()
        if inheritable(record, source_stance)
    ]


def _pair_text(spec: ResolvedSpec, pair: CellPair, breaks: Mapping[CellPair, list[str]]) -> str:
    return f"{cell_label(spec, pair[0])} | {cell_label(spec, pair[1])} ({', '.join(breaks[pair])})"


def _facing_pairs(
    spec: ResolvedSpec,
    ordered: Sequence[CellPair],
    breaks: Mapping[CellPair, list[str]],
    leading: Mapping[CellId, Side],
    following: Mapping[CellId, Side],
    values: Mapping[CellPair, _Value],
    config_order: Sequence[str],
) -> tuple[FacingPair, ...]:
    """Group the breaks into facing pairs, raise `E-KERN-NARROWER` for each one whose member breaks resolve to different values, and return them in `kern.tsv` order, each with its configurations in `config_order`."""
    position = {config: index for index, config in enumerate(config_order)}
    grouped: dict[tuple[Side, Side], list[CellPair]] = {}
    for pair in ordered:
        grouped.setdefault((leading[pair[0]], following[pair[1]]), []).append(pair)
    pairs = []
    narrower: list[tuple[str, str]] = []
    for (lead, follow), members in grouped.items():
        found = {values[pair].by for pair in members}
        if len(found) > 1:
            shown = "; ".join(
                f"{_pair_text(spec, pair, breaks)} = {values[pair].by} by {', '.join(str(decider) for decider in values[pair].deciders) or 'no record'}"
                for pair in members
                if values[pair].by != values[members[0]].by or pair == members[0]
            )
            narrower.append(
                (
                    "E-KERN-NARROWER",
                    f"the facing pair {lead.name} | {follow.name} resolves to different values across its member breaks ({shown}): a kern record tells apart cells that share one facing edge, and a record may be broader than a facing pair but never narrower; drop the key that tells them apart (rebuild/KERN-PLAN.md section 3)",
                )
            )
            continue
        deciders = tuple(sorted({decider for pair in members for decider in values[pair].deciders}, key=str))
        configs = {config for pair in members for config in breaks[pair]}
        pairs.append(
            FacingPair(
                leading=lead,
                following=follow,
                by=found.pop(),
                deciders=deciders,
                configs=tuple(
                    sorted(configs, key=lambda config: (position.get(config, len(position)), config))
                ),
                members=tuple(members),
            )
        )
    if narrower:
        raise KernError(narrower)
    return tuple(
        sorted(
            pairs,
            key=lambda pair: (
                rune_order_key(spec, pair.leading.rune),
                pair.leading.name,
                rune_order_key(spec, pair.following.rune),
                pair.following.name,
            ),
        )
    )


def kern_tsv(resolution: KernResolution) -> str:
    """Return `kern.tsv`'s text, laid out as the module docstring says."""
    lines = [TSV_COMMENT, "\t".join(TSV_COLUMNS)]
    for pair in resolution.pairs:
        lines.append(
            "\t".join(
                (
                    pair.leading.rune,
                    pair.leading.name,
                    pair.following.rune,
                    pair.following.name,
                    str(pair.by),
                    ", ".join(str(decider) for decider in pair.deciders),
                    ",".join(pair.configs),
                    pair.mixed,
                )
            )
        )
    return "\n".join(lines) + "\n"


def classes_document(resolution: KernResolution, glyphs: Mapping[CellId, GlyphRecord]) -> dict:
    """Return `kern-classes.json`'s document, as the module docstring says, over the settled cells' glyphs `glyphs` and the `.ss10` copies."""
    leading, following = resolution.glyph_sides(glyphs)
    return {
        "format": KERN_CLASSES_FORMAT,
        "leading": {name: side.name for name, side in sorted(leading.items())},
        "following": {name: side.name for name, side in sorted(following.items())},
        "values": [[pair.leading.name, pair.following.name, pair.by] for pair in resolution.pairs],
    }


def write_classes(resolution: KernResolution, glyphs: Mapping[CellId, GlyphRecord], out_dir: Path) -> None:
    """Write `kern-classes.json` under `out_dir`, leaving the file in place when its bytes come out the same."""
    text = json.dumps(classes_document(resolution, glyphs), indent=1) + "\n"
    output_sink.write_output(Path(out_dir) / KERN_CLASSES_JSON, text, keep_if_equal=True)


def run_stage(
    spec: ResolvedSpec,
    tables: Mapping[str, object],
    glyphs: Mapping[CellId, GlyphRecord],
    out_dir: Path,
    ss10_copies: Mapping[str, str] | None = None,
) -> KernResolution:
    """Resolve the kern records (`resolve`) and write `kern.tsv` under `out_dir`, leaving the file in place when its bytes come out the same. Raises before writing anything when `resolve` does."""
    resolution = resolve(spec, tables, glyphs, ss10_copies)
    output_sink.write_output(Path(out_dir) / KERN_TSV, kern_tsv(resolution), keep_if_equal=True)
    return resolution
