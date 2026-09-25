"""Unit enrichment for the review surface (rebuild/REVIEW-PLAN.md §2.2): letter-name notation, old seams from the §13.1 baseline subsets, the settle and explain results (new seams, extensions, eliminations, explain text), divergent positions computed against the alias map, and highlight x-ranges in font units. The judged pair and the secondary seams are placed on positions whose ink differs when there are any, so a position that only renames a glyph stays in the divergent positions without moving them. The highlight x-ranges come from kern-neutral shaping of both fonts, matching the app's `font-kerning: none` rendering, because the baseline subset rows were extracted with the old font's kerning on."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import batched
from pathlib import Path
from typing import Callable, Protocol

from rebuild.pipeline import kernel_exec, spec_load
from rebuild.pipeline.explain import ExplainReport, explain_many
from rebuild.pipeline.labels import BOUNDARY_GLYPH_NAMES, features_for_config, load_alias_map
from rebuild.pipeline.model import CellId, ResolvedSpec, Settled, isolated_overlay_active
from rebuild.pipeline.settle import form_ligatures, is_boundary_settled, tokens_from_codepoints
from rebuild.review.audit import ACCEPTANCE_CONFIGS, Unit
from rebuild.review.ink import OutlineCache, OutlineIntern, kern_neutral
from rebuild.review.subset_pack import SubsetPack, SubsetRow, ensure_pack, table_digests
from rebuild.validation.shaping import SENIOR_FONT, Shaper

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

LETTERS: dict[int, str] = {
    0xE650: "qsPea",
    0xE651: "qsBay",
    0xE652: "qsTea",
    0xE653: "qsDay",
    0xE654: "qsKey",
    0xE655: "qsGay",
    0xE656: "qsThaw",
    0xE657: "qsThey",
    0xE658: "qsFee",
    0xE659: "qsVie",
    0xE65A: "qsSee",
    0xE65B: "qsZoo",
    0xE65C: "qsShe",
    0xE65D: "qsJai",
    0xE65E: "qsCheer",
    0xE65F: "qsJay",
    0xE660: "qsYe",
    0xE661: "qsWay",
    0xE662: "qsHe",
    0xE663: "qsWhy",
    0xE664: "qsIng",
    0xE665: "qsMay",
    0xE666: "qsNo",
    0xE667: "qsLow",
    0xE668: "qsRoe",
    0xE669: "qsLoch",
    0xE66A: "qsLlan",
    0xE66B: "qsExcite",
    0xE66C: "qsExam",
    0xE670: "qsIt",
    0xE671: "qsEat",
    0xE672: "qsEt",
    0xE673: "qsEight",
    0xE674: "qsAt",
    0xE675: "qsI",
    0xE676: "qsAh",
    0xE677: "qsAwe",
    0xE678: "qsOx",
    0xE679: "qsOy",
    0xE67A: "qsUtter",
    0xE67B: "qsOut",
    0xE67C: "qsOwe",
    0xE67D: "qsFoot",
    0xE67E: "qsOoze",
}

SPACE = 0x0020
NAMER_DOT = 0x00B7
ZWNJ = 0x200C
BOUNDARIES = {SPACE: "space", NAMER_DOT: "namer-dot", ZWNJ: "zwnj"}
EXPLAIN_UNIT_BATCH_SIZE = 8192

_SPECIAL_DISPLAY = {"qsIng": "·-ing", "qsJai": "·J’ai"}
_BOUNDARY_NOTATION = {SPACE: "␣", NAMER_DOT: "·", ZWNJ: "◊ZWNJ"}

_STAGE_PHRASES = {
    "only-candidate": "the only surviving candidate",
    "absolute-prefer": "an absolute prefer",
    "join-count": "join-count rank",
    "yielding-prefer": "a yielding prefer",
    "order": "declaration order",
    "floor": "the structural floor",
}
_HEIGHT_PHRASES = {0: "at the baseline", 5: "at the x-height", 8: "at the top"}
_BOUNDARY_SUMMARY_NAMES = {"space": "the space", "zwnj": "◊ZWNJ", "namer-dot": "the namer dot"}


def letter_display(family: str) -> str:
    return _SPECIAL_DISPLAY.get(family, "·" + family[2:])


def rune_display(rune: str) -> str:
    """A settled cell's rune in prose notation: ·May, ·Tea+Oy for ligature runes, and the boundary tokens by name."""
    if rune in _BOUNDARY_SUMMARY_NAMES:
        return _BOUNDARY_SUMMARY_NAMES[rune]
    if not rune.startswith("qs"):
        return rune
    parts = rune.split("_")
    display = letter_display(parts[0])
    for part in parts[1:]:
        display += "+" + letter_display(part).removeprefix("·")
    return display


def _seam_phrase(token: str) -> str:
    y = int(token[1:])
    return _HEIGHT_PHRASES.get(y, f"at y={y}")


def _short_provenance(pointer: str) -> str:
    return pointer.rsplit("/", 1)[-1].replace(":", " ", 1)


def _decided_by(provenance: tuple[str, ...], stage: str | None) -> str:
    phrase = _STAGE_PHRASES.get(stage, stage) if stage else None
    if provenance:
        suffix = f"decided by {_short_provenance(provenance[0])}"
        return f"{suffix} ({phrase})" if phrase else suffix
    if phrase:
        return f"decided by {phrase} (no policy record involved)"
    return "no policy record involved"


def notation(codepoint_values: tuple[int, ...]) -> str:
    """The caption form: letters concatenate (·Tea·Oy), boundary tokens are space-separated (◊ZWNJ ·Tea·Oy, ␣, ·)."""
    parts: list[str] = []
    previous_was_letter = False
    for value in codepoint_values:
        if value in LETTERS:
            token = letter_display(LETTERS[value])
            parts.append(token if previous_was_letter else (" " + token if parts else token))
            previous_was_letter = True
        else:
            token = _BOUNDARY_NOTATION.get(value, f"U+{value:04X}")
            parts.append((" " if parts else "") + token)
            previous_was_letter = False
    return "".join(parts)


def notation_tokens(codepoint_values: tuple[int, ...]) -> tuple[str, ...]:
    """Display tokens aligned one-to-one with codepoint positions: letter names (·May) and the boundary tokens (◊ZWNJ, ␣, ·) as `notation` renders them, so joining them with `notation`'s spacing rule reproduces the caption string."""
    return tuple(
        (
            letter_display(LETTERS[value])
            if value in LETTERS
            else _BOUNDARY_NOTATION.get(value, f"U+{value:04X}")
        )
        for value in codepoint_values
    )


def text_entities(codepoint_values: tuple[int, ...]) -> str:
    return "".join(f"&#x{value:04X};" for value in codepoint_values)


def load_spec(repo_root: Path = REPO_ROOT) -> ResolvedSpec:
    return spec_load.load_spec(
        repo_root / "glyph_data" / "runes",
        repo_root / "rebuild" / "script.yaml",
        repo_root / "rebuild" / "schema",
    )


def parse_entry_extension(adjustments: tuple[str, ...]) -> int:
    total = 0
    for token in adjustments:
        if token.startswith("en-ext-"):
            total += int(token.rsplit("-", 1)[1])
        elif token.startswith("en-con-"):
            total -= int(token.rsplit("-", 1)[1])
    return total


def cell_token(cell: CellId) -> str:
    return f"{cell.rune}/{cell.stance}/{cell.entry}/{cell.exit}/{'+'.join(cell.adjustments)}"


@dataclass
class SecondarySeam:
    """One divergent adjacency beyond a unit's primary pair: the (left, right) after-cell indices and per-side highlight rects like the primary band's. Once homes are resolved, `home` is the id of the unit where this behavior is the primary pair, or None when no such unit exists; `suppressed` is set instead when the home is ink- or picture-identical, since nothing is visible to judge and no marker is emitted."""

    pair: tuple[int, int]
    highlight_before: dict
    highlight_after: dict
    home: str | None = None
    suppressed: bool = False


@dataclass(frozen=True)
class TracePosition:
    """One settled position as the drafters read it: the cell that settled there and the stage that decided it."""

    settled: Settled
    decided_stage: str


@dataclass(frozen=True)
class TraceReport:
    """The part of an `ExplainReport` an enriched unit keeps: one `TracePosition` per position, which is all the drafters read. `enrich` renders the explain text and writes the summary from the full report and then drops it, since the report is the largest thing an enrichment produces."""

    positions: tuple[TracePosition, ...]


@dataclass(slots=True)
class EnrichedUnit:
    unit: Unit
    notation: str
    text_entities: str
    before_glyphs: tuple[str, ...]
    before_seams: tuple[str, ...]
    after_cells: tuple[str, ...]
    after_seams: tuple[str, ...]
    after_extensions: tuple[int, ...]
    diff_positions: tuple[int, ...]
    pair: tuple[int, int] | None
    highlight_before: dict
    highlight_after: dict
    boundary_marks: tuple[dict, ...]
    explain_text: str
    provenance: tuple[str, ...]
    report: TraceReport
    summary: str = ""
    notes: tuple[str, ...] = ()
    after_spans: tuple[tuple[int, int], ...] = ()
    before_spans: tuple[tuple[int, int], ...] = ()
    secondary_seams: tuple[SecondarySeam, ...] = ()
    pair_codepoints: tuple[int, int] | None = None
    notation_tokens: tuple[str, ...] = ()


def _pen_positions(positions: tuple[tuple[int, int, int], ...]) -> list[int]:
    pens = [0]
    for _x, _y, advance in positions:
        pens.append(pens[-1] + advance)
    return pens


def _spans_from_clusters(clusters: tuple[int, ...], length: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for index, start in enumerate(clusters):
        end = clusters[index + 1] if index + 1 < len(clusters) else length
        spans.append((start, max(end, start + 1)))
    return spans


def _covering(spans: list[tuple[int, int]], position: int) -> int:
    for index, (start, end) in enumerate(spans):
        if start <= position < end:
            return index
    return len(spans) - 1


def _highlight(
    pens: list[int],
    spans: list[tuple[int, int]],
    cp_start: int,
    cp_end: int,
) -> dict:
    if any(later < earlier for earlier, later in zip(pens, pens[1:])):
        raise ValueError(f"a highlight rect wants non-decreasing pen positions, got {pens}")
    first = _covering(spans, cp_start)
    last = _covering(spans, cp_end)
    return {"x_min": pens[first], "x_max": pens[last + 1], "advance_total": pens[-1]}


def _advance_drift_cell(before_pens: list[int], after_pens: list[int], cell_count: int) -> int | None:
    """The first cell whose kern-neutral advance differs between the two fonts, or None. It locates a position-only divergence, which has no cell or seam difference to form a pair from, such as a one-pixel advance change on a letter next to a boundary. The change sits at the word break beside the letter, so the caller marks the nearest boundary token (◊ZWNJ, ␣, or ·), when there is one, instead of the letter and draws no sample band."""
    limit = min(cell_count, len(before_pens) - 1, len(after_pens) - 1)
    for index in range(limit):
        if before_pens[index + 1] - before_pens[index] != after_pens[index + 1] - after_pens[index]:
            return index
    return None


class Enricher:
    """Holds the loaded spec, the packed baseline subset tables, a shaper per font, and the alias map; `enrich` computes every precomputed shard field for one unit under its first config. The pack is `subset_pack` when given (the build passes the one it wrote before starting its pool); otherwise it is the one `ensure_pack` keeps beside the tables under `subset_dir` over `ACCEPTANCE_CONFIGS`, written on demand. The pack is opened, and written if needed, at the first `subset_row` call in each process: construction hashes and writes nothing, and a spawned worker maps the parent's file read-only instead of holding its own tables."""

    def __init__(
        self,
        spec: ResolvedSpec,
        subset_dir: Path,
        after_font: Path,
        alias_path: Path | None = None,
        repo_root: Path = REPO_ROOT,
        before_font: Path = SENIOR_FONT,
        shaper_factory: Callable = Shaper,
        subset_pack: Path | None = None,
    ):
        self.spec = spec
        self.subset_dir = Path(subset_dir)
        self.subset_pack = Path(subset_pack) if subset_pack is not None else None
        self.after_shaper = shaper_factory(after_font)
        self.before_shaper = shaper_factory(before_font)
        self._intern = OutlineIntern()
        self._outlines = {
            "before": OutlineCache(before_font, self._intern),
            "after": OutlineCache(after_font, self._intern),
        }
        self.aliases = load_alias_map(alias_path or repo_root / "rebuild" / "m1-aliases.yaml")
        self._pack: SubsetPack | None = None
        self._guard_verdicts: kernel_exec.FormationGuard | None = None
        self.mismatches: list[str] = []

    def subset_row(self, config: str, codepoints: str) -> SubsetRow | None:
        """One window's baseline row under `config`, read from the pack. The first call hashes the tables on disk, runs `ensure_pack` if no caller named a pack, and opens the pack against those digests."""
        if self._pack is None:
            digests = table_digests(self.subset_dir, ACCEPTANCE_CONFIGS)
            if self.subset_pack is None:
                self.subset_pack = ensure_pack(self.subset_dir, ACCEPTANCE_CONFIGS, digests)
            self._pack = SubsetPack.open(self.subset_pack, digests)
        return self._pack.row(config, codepoints)

    def subset_pack_census(self) -> tuple[int, int]:
        """The pack's rows and mapped bytes for the pile tally, or (0, 0) before it is opened."""
        return self._pack.census() if self._pack is not None else (0, 0)

    def formed_spans(self, codepoint_values: tuple[int, ...]) -> list[tuple[int, int]]:
        tokens = tokens_from_codepoints(self.spec, codepoint_values)
        if self._guard_verdicts is None:
            self._guard_verdicts = kernel_exec.guard_sweep(self.spec)
        formed = form_ligatures(self.spec, tokens, self._guard_verdicts)
        spans: list[tuple[int, int]] = []
        consumed = 0
        for token in formed:
            width = 1
            if token.kind == "letter":
                rune = self.spec.runes.get(token.rune or "")
                if rune is not None and rune.sequence:
                    width = len(rune.sequence)
            spans.append((consumed, consumed + width))
            consumed += width
        if consumed != len(codepoint_values):
            raise ValueError(f"formation spans cover {consumed} of {len(codepoint_values)} codepoints")
        return spans

    def seam_token(self, seam, overlay: bool) -> str:
        if overlay or seam is None:
            return "break"
        return f"y{self.spec.registry.y_of(seam)}"

    def explain_units(self, units: Sequence[Unit]) -> list[ExplainReport]:
        """Settle one unit batch through the Rust kernel before the enrichment loop consumes its reports."""
        if self._guard_verdicts is None:
            self._guard_verdicts = kernel_exec.guard_sweep(self.spec)
        return explain_many(
            self.spec,
            [(unit.codepoint_values, features_for_config(unit.configs[0])) for unit in units],
            self._guard_verdicts,
        )

    def enrich_many(self, units: Sequence[Unit]) -> list[EnrichedUnit]:
        """Enrich an already-classified batch with one shared settlement pass."""
        enriched = []
        for unit_batch, reports in self.explain_unit_batches(units):
            enriched.extend(self.enrich(unit, report) for unit, report in zip(unit_batch, reports))
        return enriched

    def explain_unit_batches(
        self, units: Sequence[Unit]
    ) -> Iterator[tuple[tuple[Unit, ...], list[ExplainReport]]]:
        """Settle the units in batches of `EXPLAIN_UNIT_BATCH_SIZE`, so the full reports for all units are never held at once."""
        for unit_batch in batched(units, EXPLAIN_UNIT_BATCH_SIZE):
            yield unit_batch, self.explain_units(unit_batch)

    def enrich(self, unit: Unit, report: ExplainReport | None = None) -> EnrichedUnit:
        values = unit.codepoint_values
        config = unit.configs[0]
        features = features_for_config(config)
        overlay = isolated_overlay_active(self.spec, features)
        if report is None:
            report = self.explain_units([unit])[0]
        settled = list(report.settled)

        derived_cells = tuple(cell_token(item.cell) for item in settled)
        # The audit's `new` column holds the settled cell tokens for cell and seam rows, but per-slot position diagnostics for position-only rows. Compare the re-settlement with the audit only when `new` holds cell tokens, and always take `after_cells` from the re-derived cells so they line up with `after_seams`.
        if all("/" in token for token in unit.new) and derived_cells != unit.new:
            self.mismatches.append(
                f"{config} {unit.codepoints}: derived cells {derived_cells} != audit {unit.new}"
            )

        after_spans = (
            [(index, index + 1) for index in range(len(values))] if overlay else self.formed_spans(values)
        )
        after_seams = tuple(
            self.seam_token(settled[index].seam, overlay) for index in range(len(settled) - 1)
        )
        after_extensions = tuple(
            (
                0
                if overlay
                else settled[index].extension + parse_entry_extension(settled[index + 1].cell.adjustments)
            )
            for index in range(len(settled) - 1)
        )

        row = self.subset_row(config, unit.codepoints)
        if row is None:
            raise ValueError(f"no baseline subset row for {config} {unit.codepoints}")
        before_spans = _spans_from_clusters(row.clusters, len(values))
        before_seams = tuple(row.seams[row.clusters[index + 1] - 1] for index in range(len(row.glyphs) - 1))
        # The per-glyph seams can be read from the per-codepoint row in two ways: the seam at each cluster's last codepoint, or the row's seams without the `lig` seams inside clusters. The assert checks that the two agree.
        assert before_seams == tuple(
            seam for seam in row.seams if seam != "lig"
        ), f"{config} {unit.codepoints}: before seams {before_seams} disagree with the lig-filtered row seams"

        diff_cp, divergent_gaps = self._diff_codepoints(values, row, before_spans, settled, after_spans)
        diff_positions = tuple(sorted({_covering(after_spans, cp) for cp in diff_cp}))

        hb_features = kern_neutral(dict.fromkeys(features, True))
        shaped = self.after_shaper.shape("".join(chr(value) for value in values), hb_features)
        after_pens = _pen_positions(shaped.positions)
        after_cluster_spans = _spans_from_clusters(shaped.clusters, len(values))
        # The subset row's positions were extracted with the old font's kerning on, so the before pens come from a kern-neutral shaping, whose glyph names are checked against the subset row.
        before_shaped = self.before_shaper.shape("".join(chr(value) for value in values), hb_features)
        if before_shaped.names != tuple(row.glyphs):
            self.mismatches.append(
                f"{config} {unit.codepoints}: kern-neutral before glyphs {before_shaped.names} != subset row {tuple(row.glyphs)}"
            )
        before_pens = _pen_positions(before_shaped.positions)

        ink_positions = self._ink_visible_positions(
            diff_positions,
            after_spans,
            shaped,
            after_cluster_spans,
            after_pens,
            before_shaped,
            _spans_from_clusters(before_shaped.clusters, len(values)),
            before_pens,
        )
        anchor_positions = ink_positions if (ink_positions or divergent_gaps) else diff_positions
        pair = self._pick_pair(divergent_gaps, anchor_positions, after_seams, len(settled))

        pair_codepoints = (after_spans[pair[0]][0], after_spans[pair[1]][1] - 1) if pair is not None else None
        if pair is None and not diff_positions:
            drifted = _advance_drift_cell(before_pens, after_pens, len(settled))
            if drifted is not None:
                boundaries = [i for i in range(len(settled)) if values[after_spans[i][0]] in BOUNDARIES]
                mark = min(boundaries, key=lambda i: (abs(i - drifted), i)) if boundaries else drifted
                pair_codepoints = (after_spans[mark][0], after_spans[mark][1] - 1)
        if pair is not None:
            assert pair_codepoints is not None
            cp_start, cp_end = pair_codepoints
        elif diff_positions:
            cp_start = after_spans[diff_positions[0]][0]
            cp_end = after_spans[diff_positions[-1]][1] - 1
        else:
            cp_start, cp_end = 0, len(values) - 1
        highlight_after = _highlight(after_pens, after_cluster_spans, cp_start, cp_end)
        highlight_before = _highlight(before_pens, before_spans, cp_start, cp_end)

        secondary_seams: list[SecondarySeam] = []
        if pair is not None and not (unit.ink_identical or unit.picture_identical):
            for left, right in _secondary_pairs(
                pair, divergent_gaps, anchor_positions, after_seams, len(settled)
            ):
                seam_start = after_spans[left][0]
                seam_end = after_spans[right][1] - 1
                secondary_seams.append(
                    SecondarySeam(
                        pair=(left, right),
                        highlight_before=_highlight(before_pens, before_spans, seam_start, seam_end),
                        highlight_after=_highlight(after_pens, after_cluster_spans, seam_start, seam_end),
                    )
                )

        boundary_marks = tuple(
            {
                "index": index,
                "kind": BOUNDARIES[values[after_spans[index][0]]],
                "x": after_pens[_covering(after_cluster_spans, after_spans[index][0])],
            }
            for index in range(len(settled))
            if values[after_spans[index][0]] in BOUNDARIES
        )

        diff_traces = tuple(
            report.positions[index].trace
            for index in diff_positions
            if index < len(report.positions)
            and not is_boundary_settled(report.positions[index].trace.settled)
        )
        provenance = _collect_provenance(diff_traces)
        # A slim unit (`audit.slim_fragment`) ships no explain text, so none is rendered for it; the report still feeds its summary line.
        explain_text = "" if unit.slim_fragment else _filter_explain(report.render(), diff_positions)
        summary = _summarize(
            settled=settled,
            after_spans=after_spans,
            after_seams=after_seams,
            before_glyphs=tuple(row.glyphs),
            before_spans=before_spans,
            before_seams=before_seams,
            diff_positions=ink_positions or diff_positions,
            pair=pair,
            report=report,
            provenance=provenance,
        )

        return EnrichedUnit(
            unit=unit,
            notation=notation(values),
            notation_tokens=notation_tokens(values),
            text_entities=text_entities(values),
            before_glyphs=tuple(row.glyphs),
            before_seams=before_seams,
            after_cells=derived_cells,
            after_seams=after_seams,
            after_extensions=after_extensions,
            diff_positions=diff_positions,
            pair=pair,
            pair_codepoints=pair_codepoints,
            highlight_before=highlight_before,
            highlight_after=highlight_after,
            boundary_marks=boundary_marks,
            explain_text=explain_text,
            provenance=provenance,
            report=TraceReport(
                positions=tuple(
                    TracePosition(settled=position.trace.settled, decided_stage=position.trace.decided_stage)
                    for position in report.positions
                )
            ),
            summary=summary,
            after_spans=tuple(after_spans),
            before_spans=tuple(before_spans),
            secondary_seams=tuple(secondary_seams),
        )

    def _diff_codepoints(
        self,
        values: tuple[int, ...],
        row: SubsetRow,
        before_spans: list[tuple[int, int]],
        settled: list[Settled],
        after_spans: list[tuple[int, int]],
    ) -> tuple[set[int], list[tuple[int, int]]]:
        """Divergent codepoint positions (covering-structure or alias-vs-cell mismatch) and divergent inter-cell gaps as (left cell, right cell) pairs in after indices."""
        diff: set[int] = set()
        for position in range(len(values)):
            before_index = _covering(before_spans, position)
            after_index = _covering(after_spans, position)
            if before_spans[before_index] != after_spans[after_index]:
                diff.add(position)
                continue
            old_name = row.glyphs[before_index]
            cell = settled[after_index].cell
            if old_name in BOUNDARY_GLYPH_NAMES:
                continue
            alias = self.aliases.get(old_name)
            if alias is None or isinstance(alias, str) or alias != cell:
                diff.add(position)

        gaps: list[tuple[int, int]] = []
        for gap in range(len(values) - 1):
            left_after = _covering(after_spans, gap)
            right_after = _covering(after_spans, gap + 1)
            after_seam = "lig" if left_after == right_after else self._after_seam_at(settled, left_after)
            before_seam = row.seams[gap]
            if before_seam != after_seam:
                if left_after != right_after:
                    gaps.append((left_after, right_after))
                else:
                    diff.add(gap)
                    diff.add(gap + 1)
        return diff, gaps

    def _after_seam_at(self, settled: list[Settled], index: int) -> str:
        return self.seam_token(settled[index].seam, False) if settled[index].seam is not None else "break"

    def _segment_pieces(self, side: str, shaped, pens: list[int], spans, cp_start: int, cp_end: int) -> tuple:
        """The placed ink of one font's shaped glyphs covering codepoints [cp_start, cp_end), as sorted (shape key, x, y) pieces from the intern both fonts share, translated together so the segment's leftmost ink is at x=0 (the `config_diff` normalization). The pieces are aligned on the ink and not on a pen position, because the two fonts reach the same placement through different advances and offsets, and divergent ink elsewhere in the window moves the pens apart without changing this segment.

        Each shape's canonical frame puts its leftmost, lowest point at (0, 0), so a piece's translated outline is its canonical outline moved by (x - x0, y), where x0 is the segment's leftmost ink. Two pieces therefore compare equal exactly when their translated outlines would, across fonts too, since both fonts use one intern. No outline is built, which matters because `_ink_visible_positions` calls this twice per divergent position. A shape with no points is recorded at (0, 0), since translation does not change it.
        """
        outlines = self._outlines[side]
        intern = self._intern
        placed = []
        for index, (start, end) in enumerate(spans):
            if start < cp_end and cp_start < end:
                x_offset, y_offset, _advance = shaped.positions[index]
                key, origin_x, origin_y = outlines.shape_key(shaped.names[index])
                if key:
                    placed.append((key, pens[index] + x_offset + origin_x, y_offset + origin_y))
        xs = [x for key, x, _y in placed if intern.draws(key)]
        if not xs:
            return ()
        x0 = min(xs)
        return tuple(sorted((key, x - x0, y) if intern.draws(key) else (key, 0, 0) for key, x, y in placed))

    def _ink_visible_positions(
        self,
        diff_positions: tuple[int, ...],
        after_spans: list[tuple[int, int]],
        shaped,
        after_cluster_spans: list[tuple[int, int]],
        after_pens: list[int],
        before_shaped,
        before_cluster_spans: list[tuple[int, int]],
        before_pens: list[int],
    ) -> tuple[int, ...]:
        """The divergent positions whose divergence is visible in ink: the glyphs covering the position's codepoint span place different outlines in the two fonts. A position whose segments match differs only in its name, such as bare qsNo against qsNo/loop/None/x-height/ where the base drawing already carries the join. It stays in `diff_positions` but does not anchor the judged pair or produce a secondary seam."""
        visible = []
        for position in diff_positions:
            cp_start, cp_end = after_spans[position]
            before = self._segment_pieces(
                "before", before_shaped, before_pens, before_cluster_spans, cp_start, cp_end
            )
            after = self._segment_pieces("after", shaped, after_pens, after_cluster_spans, cp_start, cp_end)
            if before != after:
                visible.append(position)
        return tuple(visible)

    @staticmethod
    def _pick_pair(
        divergent_gaps: list[tuple[int, int]],
        anchor_positions: tuple[int, ...],
        after_seams: tuple[str, ...],
        cell_count: int,
    ) -> tuple[int, int] | None:
        """The unit's judged pair: the first divergent gap, else the first two adjacent anchor positions, else a lone anchor position paired with the neighbor it joins toward. The caller passes the ink-visible divergent positions as anchors, or all divergent positions when none is ink-visible and there is no divergent gap, so a rename-only position does not move the pair off the ink."""
        if divergent_gaps:
            return divergent_gaps[0]
        if not anchor_positions or cell_count < 2:
            return None
        for left, right in zip(anchor_positions, anchor_positions[1:]):
            if right == left + 1:
                return (left, right)
        position = anchor_positions[0]
        joins_right = position + 1 < cell_count and after_seams[position] != "break"
        joins_left = position > 0 and after_seams[position - 1] != "break"
        if joins_right or (position + 1 < cell_count and not joins_left):
            return (position, position + 1)
        return (position - 1, position)


def _secondary_pairs(
    primary: tuple[int, int],
    divergent_gaps: list[tuple[int, int]],
    anchor_positions: tuple[int, ...],
    after_seams: tuple[str, ...],
    cell_count: int,
) -> tuple[tuple[int, int], ...]:
    """Every divergent adjacency beyond the primary pair, in left-index order: the remaining divergent gaps, plus a neighbor seam for each anchor position the primary and the gaps do not cover, chosen as `_pick_pair` chooses (adjacent anchors first, then the join direction). The caller passes the same anchors `_pick_pair` used, so a rename-only position gets no marker."""
    pairs: list[tuple[int, int]] = []

    def add(candidate: tuple[int, int]) -> None:
        if candidate != primary and candidate not in pairs:
            pairs.append(candidate)

    for gap in divergent_gaps:
        add(gap)
    covered = {primary[0], primary[1]}
    for left, right in divergent_gaps:
        covered.update((left, right))
    remaining = [position for position in anchor_positions if position not in covered]
    index = 0
    while index < len(remaining):
        position = remaining[index]
        if index + 1 < len(remaining) and remaining[index + 1] == position + 1:
            add((position, position + 1))
            index += 2
            continue
        joins_right = position + 1 < cell_count and after_seams[position] != "break"
        joins_left = position > 0 and after_seams[position - 1] != "break"
        if joins_right or (position + 1 < cell_count and not joins_left):
            add((position, position + 1))
        elif position > 0:
            add((position - 1, position))
        index += 1
    return tuple(sorted(pairs))


@dataclass(frozen=True)
class SeamHomeUnit:
    """The fields of an `EnrichedUnit` that the secondary-home search reads, as a small picklable record without the trace, the explain text, or the highlight rects. Surface workers return these to the parent, which runs the search over the whole corpus."""

    unit_id: str
    codepoint_values: tuple[int, ...]
    ink_identical: bool
    picture_identical: bool
    pair: tuple[int, int] | None
    after_spans: tuple[tuple[int, int], ...]
    after_cells: tuple[str, ...]
    after_seams: tuple[str, ...]
    before_spans: tuple[tuple[int, int], ...]
    before_glyphs: tuple[str, ...]
    before_seams: tuple[str, ...]
    seam_pairs: tuple[tuple[int, int], ...]


def seam_home_projection(enriched: EnrichedUnit) -> SeamHomeUnit:
    return SeamHomeUnit(
        unit_id=enriched.unit.unit_id,
        codepoint_values=enriched.unit.codepoint_values,
        ink_identical=enriched.unit.ink_identical,
        picture_identical=enriched.unit.picture_identical,
        pair=enriched.pair,
        after_spans=enriched.after_spans,
        after_cells=enriched.after_cells,
        after_seams=enriched.after_seams,
        before_spans=enriched.before_spans,
        before_glyphs=enriched.before_glyphs,
        before_seams=enriched.before_seams,
        seam_pairs=tuple(seam.pair for seam in enriched.secondary_seams),
    )


class SeamHomeSource(Protocol):
    """The corpus as the secondary-home search reads it, by ordinal. Most units are read only for their window and their seam count, so a source that stores units as columns and builds a `SeamHomeUnit` on demand saves the parent a live object per unit. `_ListSource` wraps a list of projections, and the packed unit store is the other source. The protocol is declared here so that `unit_store` imports `enrich` and not the reverse.

    Every method takes the unit's ordinal, a dense index over `0 … len(source)`, and every parameter is positional. `id_word` gives the order ties break on and must rank units as their `unit_id` strings do; for a corpus id that is the integer the base58 id encodes, since `unit_cache.base58_64` is fixed width over an ASCII-ordered alphabet. `invisible` is `ink_identical or picture_identical`, asked only of a resolved home. The search passes `set_homes` home ordinals (or None), and a source needs to accept only those. The search reads identity from the ordinal and `id_word`, never from a projection's `unit_id`, so a source may leave that field empty.
    """

    def __len__(self) -> int: ...

    def windows(self) -> Iterable[tuple[int, tuple[int, ...]]]: ...

    def seam_count(self, ordinal: int, /) -> int: ...

    def projection(self, ordinal: int, /) -> SeamHomeUnit: ...

    def id_word(self, ordinal: int, /) -> int: ...

    def invisible(self, ordinal: int, /) -> bool: ...

    def set_homes(self, ordinal: int, seam_assign: Sequence[tuple[int | None, bool]], /) -> None: ...


class _ListSource:
    """A list of projections as a `SeamHomeSource`, for callers that already hold the objects: `resolve_secondary_homes` and the tests. The ordinal is the list index, and the id word is the id string read as a big-endian integer, which orders correctly for ids of one width, as every `unit_cache.unit_id_for` id is. `set_homes` translates home ordinals back into ids in `assignments`, the dict `apply_home_assignments` reads.

    A unit with no secondary seam gets no entry, and readers treat a missing entry as an empty list.
    """

    __slots__ = ("projections", "assignments")

    def __init__(self, projections: list[SeamHomeUnit]) -> None:
        self.projections = projections
        self.assignments: dict[str, list[tuple[str | None, bool]]] = {}

    def __len__(self) -> int:
        return len(self.projections)

    def windows(self) -> Iterator[tuple[int, tuple[int, ...]]]:
        for ordinal, item in enumerate(self.projections):
            yield ordinal, item.codepoint_values

    def seam_count(self, ordinal: int, /) -> int:
        return len(self.projections[ordinal].seam_pairs)

    def projection(self, ordinal: int, /) -> SeamHomeUnit:
        return self.projections[ordinal]

    def id_word(self, ordinal: int, /) -> int:
        return int.from_bytes(self.projections[ordinal].unit_id.encode(), "big")

    def invisible(self, ordinal: int, /) -> bool:
        item = self.projections[ordinal]
        return item.ink_identical or item.picture_identical

    def set_homes(self, ordinal: int, seam_assign: Sequence[tuple[int | None, bool]], /) -> None:
        if not seam_assign:
            return
        self.assignments[self.projections[ordinal].unit_id] = [
            (None if home is None else self.projections[home].unit_id, suppressed)
            for home, suppressed in seam_assign
        ]


def _seam_outcomes_match(
    item: SeamHomeUnit, left: int, right: int, candidate: SeamHomeUnit, offset: int
) -> bool:
    """Whether `candidate`, found at codepoint `offset` inside `item`, has the same before and after outcomes at the seam between item's after cells `left` and `right` (the same covering spans after the offset, glyph and cell names, and seam tokens) and has that seam as its own primary pair."""
    span_left = item.after_spans[left]
    span_right = item.after_spans[right]
    shifted_left = (span_left[0] - offset, span_left[1] - offset)
    shifted_right = (span_right[0] - offset, span_right[1] - offset)
    try:
        candidate_left = candidate.after_spans.index(shifted_left)
    except ValueError:
        return False
    candidate_right = candidate_left + 1
    if candidate_right >= len(candidate.after_spans):
        return False
    if candidate.after_spans[candidate_right] != shifted_right:
        return False
    if candidate.after_cells[candidate_left] != item.after_cells[left]:
        return False
    if candidate.after_cells[candidate_right] != item.after_cells[right]:
        return False
    if candidate.after_seams[candidate_left] != item.after_seams[left]:
        return False
    gap = span_left[1] - 1
    mine_left = _covering(list(item.before_spans), gap)
    mine_right = _covering(list(item.before_spans), gap + 1)
    theirs_left = _covering(list(candidate.before_spans), gap - offset)
    theirs_right = _covering(list(candidate.before_spans), gap + 1 - offset)
    for mine, theirs in ((mine_left, theirs_left), (mine_right, theirs_right)):
        their_span = candidate.before_spans[theirs]
        if (their_span[0] + offset, their_span[1] + offset) != tuple(item.before_spans[mine]):
            return False
        if candidate.before_glyphs[theirs] != item.before_glyphs[mine]:
            return False
    if mine_left != mine_right and candidate.before_seams[theirs_left] != item.before_seams[mine_left]:
        return False
    return candidate.pair == (candidate_left, candidate_right)


def _find_home(
    item: SeamHomeUnit,
    ordinal: int,
    pair: tuple[int, int],
    by_codepoints: dict[tuple[int, ...], list[int]],
    source: SeamHomeSource,
    held: dict[int, SeamHomeUnit],
) -> int | None:
    """The ordinal of the seam's home: the shortest other unit whose codepoint string is a substring of `item`'s containing the seam's two cells, with the same before and after outcomes at the seam and that seam as its primary pair. Ties break to the lowest unit id, ranked by `source.id_word`. None when no unit qualifies. The self-skip compares ordinals because a corpus has one unit per id, which the store's index enforces. `held` caches the candidates already materialized for this item, since one candidate can match several lengths, offsets, and seams; the caller discards it after each item."""
    values = item.codepoint_values
    left, right = pair
    minimum = item.after_spans[right][1] - item.after_spans[left][0]
    for length in range(minimum, len(values) + 1):
        matches: list[int] = []
        first_offset = max(0, item.after_spans[right][1] - length)
        last_offset = min(item.after_spans[left][0], len(values) - length)
        for offset in range(first_offset, last_offset + 1):
            window = values[offset : offset + length]
            for candidate in by_codepoints.get(window, ()):
                if candidate == ordinal:
                    continue
                projection = held.get(candidate)
                if projection is None:
                    projection = held[candidate] = source.projection(candidate)
                if _seam_outcomes_match(item, left, right, projection, offset):
                    matches.append(candidate)
        if matches:
            return min(matches, key=source.id_word)
    return None


def resolve_home_assignments(
    source: SeamHomeSource | list[SeamHomeUnit],
) -> tuple[dict[str, list[tuple[str | None, bool]]], dict[str, int]]:
    """Resolve the home of every secondary seam in the corpus. For each unit with secondary seams, each seam gets (home or None, suppressed) in seam order, written back through `source.set_homes`, and the census is counted. A seam whose home is ink- or picture-identical is suppressed: the divergence is an invisible name-grain rename, so it gets no marker. A seam with no home keeps home None and stays visible, so it is never left unmarked.

    The window index holds ordinals, and a unit is materialized only when it has a seam or is a candidate. A list of projections is wrapped in `_ListSource`, and only then is the returned dict filled, keyed by unit id for `apply_home_assignments`. A source that stores its own homes, such as the unit store, gets them through `set_homes`, and the dict is empty. The census has the same four counts either way.
    """
    if isinstance(source, list):
        adapter = _ListSource(source)
        reduced: SeamHomeSource = adapter
    else:
        adapter = None
        reduced = source
    by_codepoints: dict[tuple[int, ...], list[int]] = {}
    for ordinal, window in reduced.windows():
        by_codepoints.setdefault(window, []).append(ordinal)
    census = {
        "units_with_markers": 0,
        "seams_homed": 0,
        "seams_homeless": 0,
        "seams_suppressed_invisible": 0,
    }
    for ordinal in range(len(reduced)):
        if not reduced.seam_count(ordinal):
            continue
        item = reduced.projection(ordinal)
        held: dict[int, SeamHomeUnit] = {}
        visible = 0
        seam_assign: list[tuple[int | None, bool]] = []
        for pair in item.seam_pairs:
            home = _find_home(item, ordinal, pair, by_codepoints, reduced, held)
            if home is None:
                census["seams_homeless"] += 1
                visible += 1
                seam_assign.append((None, False))
            elif reduced.invisible(home):
                census["seams_suppressed_invisible"] += 1
                seam_assign.append((None, True))
            else:
                census["seams_homed"] += 1
                visible += 1
                seam_assign.append((home, False))
        reduced.set_homes(ordinal, seam_assign)
        if visible:
            census["units_with_markers"] += 1
    return (adapter.assignments if adapter is not None else {}), census


def apply_home_assignments(
    enriched_units: list[EnrichedUnit], assignments: dict[str, list[tuple[str | None, bool]]]
) -> None:
    """Write a `resolve_home_assignments` result onto each unit's secondary seams in place. A unit with no secondary seam has no entry and is left unchanged."""
    for item in enriched_units:
        for seam, (home, suppressed) in zip(item.secondary_seams, assignments.get(item.unit.unit_id, ())):
            seam.home = home
            seam.suppressed = suppressed


def resolve_secondary_homes(enriched_units: list[EnrichedUnit]) -> dict[str, int]:
    """Resolve the home of every secondary seam across the given units, set it on the seams in place, and return the census (`resolve_home_assignments` has the rules)."""
    projections = [seam_home_projection(item) for item in enriched_units]
    assignments, census = resolve_home_assignments(projections)
    apply_home_assignments(enriched_units, assignments)
    return census


def _summarize(
    *,
    settled: list[Settled],
    after_spans: list[tuple[int, int]],
    after_seams: tuple[str, ...],
    before_glyphs: tuple[str, ...],
    before_spans: list[tuple[int, int]],
    before_seams: tuple[str, ...],
    diff_positions: tuple[int, ...],
    pair: tuple[int, int] | None,
    report: ExplainReport,
    provenance: tuple[str, ...],
) -> str:
    """The one-line summary shown on every unit: what the new pipeline chose at the primary divergence and the first record in its provenance, e.g. "New: ·May joins ·It at the baseline (the old pipeline broke there) — decided by qsMay.yaml policy.extend[3] (join-count rank).". The caller passes the ink-visible divergent positions when any exist, so the summary describes the position the judged pair is placed on and not a rename-only position earlier in the window."""
    position = None
    for index in diff_positions:
        if index < len(report.positions) and not is_boundary_settled(report.positions[index].trace.settled):
            position = index
            break
    stage = report.positions[position].trace.decided_stage if position is not None else None
    clause = _summary_clause(
        settled, after_spans, after_seams, before_glyphs, before_spans, before_seams, pair, position
    )
    return f"New: {clause} — {_decided_by(provenance, stage)}."


def _before_seam_at_codepoint_gap(
    before_spans: list[tuple[int, int]], before_seams: tuple[str, ...], gap: int
) -> str | None:
    for index in range(len(before_spans) - 1):
        if before_spans[index + 1][0] == gap + 1:
            return before_seams[index]
    return None


def _cell_description(cell: CellId) -> str:
    bits = [cell.stance, f"entry {cell.entry or 'none'}", f"exit {cell.exit or 'none'}"]
    if cell.adjustments:
        bits.append("adjustments " + "+".join(cell.adjustments))
    return ", ".join(bits)


def _summary_clause(
    settled: list[Settled],
    after_spans: list[tuple[int, int]],
    after_seams: tuple[str, ...],
    before_glyphs: tuple[str, ...],
    before_spans: list[tuple[int, int]],
    before_seams: tuple[str, ...],
    pair: tuple[int, int] | None,
    position: int | None,
) -> str:
    if position is not None:
        span = after_spans[position]
        if span[1] - span[0] > 1 and span not in before_spans:
            return (
                f"{rune_display(settled[position].cell.rune)} now forms as one ligature "
                "(the old pipeline rendered the letters separately)"
            )
    for index, span in enumerate(before_spans):
        if span[1] - span[0] > 1 and span not in after_spans:
            base = before_glyphs[index].split(".")[0]
            return f"the {rune_display(base)} ligature no longer forms; the letters render separately"
    if pair is not None:
        left = rune_display(settled[pair[0]].cell.rune)
        right = rune_display(settled[pair[1]].cell.rune)
        after_seam = after_seams[pair[0]]
        gap = after_spans[pair[0]][1] - 1
        before_seam = _before_seam_at_codepoint_gap(before_spans, before_seams, gap)
        if after_seam.startswith("y") and before_seam in (None, "break"):
            return f"{left} joins {right} {_seam_phrase(after_seam)} (the old pipeline broke there)"
        if after_seam == "break" and before_seam is not None and before_seam.startswith("y"):
            return f"{left} no longer joins {right} (the old pipeline joined {_seam_phrase(before_seam)})"
        if (
            after_seam.startswith("y")
            and before_seam is not None
            and before_seam.startswith("y")
            and after_seam != before_seam
        ):
            return f"{left} joins {right} {_seam_phrase(after_seam)} instead of {_seam_phrase(before_seam)}"
    if position is not None:
        cell = settled[position].cell
        return (
            f"{rune_display(cell.rune)} keeps the same seams but settles as a different cell "
            f"({_cell_description(cell)})"
        )
    return "only the boundary marker's glyph changed; every letter cell and seam is unchanged"


def _collect_provenance(traces) -> tuple[str, ...]:
    pointers: list[str] = []
    for trace in traces:
        for elimination in trace.eliminations:
            if elimination.provenance is not None:
                pointer = str(elimination.provenance)
                if pointer not in pointers:
                    pointers.append(pointer)
        for note in trace.notes:
            marker = note.find("glyph_data/")
            if marker >= 0:
                pointer = note[marker:]
                if pointer not in pointers:
                    pointers.append(pointer)
    return tuple(pointers)


def _filter_explain(rendered: str, diff_positions: tuple[int, ...]) -> str:
    """Keep the header lines of an `ExplainReport.render()` and only the divergent positions' blocks, or every block when there are no divergent positions."""
    blocks = rendered.split("\n\nposition ")
    if len(blocks) == 1:
        return blocks[0]
    wanted = set(diff_positions)
    kept = [blocks[0]]
    for block in blocks[1:]:
        index = int(block.split(":", 1)[0].split()[0])
        if not wanted or index in wanted:
            kept.append(block)
    return "\n\nposition ".join(kept)
