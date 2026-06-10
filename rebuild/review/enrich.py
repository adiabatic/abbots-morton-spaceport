"""Unit enrichment for the review surface (rebuild/REVIEW-PLAN.md §2.2): rune-name notation, old seams from the §13.1 baseline subsets, the settle/explain precompute (new seams, extensions, eliminations, render text), divergent-position computation against the alias map, and highlight x-ranges in font units from real shaping of both fonts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rebuild.pipeline import spec_load
from rebuild.pipeline.conform import (
    BOUNDARY_GLYPH_NAMES,
    features_for_config,
    isolated_overlay_active,
    load_alias_map,
)
from rebuild.pipeline.explain import ExplainReport, explain
from rebuild.pipeline.model import CellId, ResolvedSpec, Settled
from rebuild.pipeline.settle import form_ligatures, is_boundary_settled, tokens_from_codepoints
from rebuild.review.audit import Unit
from rebuild.validation.rowmodel import Row, iter_rows
from rebuild.validation.shaping import Shaper

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

_SPECIAL_DISPLAY = {"qsIng": "·-ing", "qsJai": "·J’ai"}
_BOUNDARY_NOTATION = {SPACE: "␣", NAMER_DOT: "·", ZWNJ: "◊ZWNJ"}


def letter_display(family: str) -> str:
    return _SPECIAL_DISPLAY.get(family, "·" + family[2:])


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


def overlay_settled(spec: ResolvedSpec, settled: list[Settled]) -> list[Settled]:
    """The conform.py overlay transformation for `overlay: isolated` taste sets (ss10): every letter cell renders as its rune's anchor-free default-stance cell and every seam is visually a break."""
    out: list[Settled] = []
    for item in settled:
        cell = item.cell
        if isinstance(cell, CellId) and cell.rune in spec.runes and cell.stance != "boundary":
            out.append(
                Settled(
                    cell=CellId(cell.rune, spec.runes[cell.rune].default_stance, None, None, ()),
                    seam=None,
                    extension=0,
                )
            )
        else:
            out.append(Settled(cell=cell, seam=None, extension=0))
    return out


def cell_token(cell: CellId) -> str:
    return f"{cell.rune}/{cell.stance}/{cell.entry}/{cell.exit}/{'+'.join(cell.adjustments)}"


@dataclass
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
    report: ExplainReport
    diff_traces: tuple = ()
    notes: tuple[str, ...] = ()
    after_spans: tuple[tuple[int, int], ...] = ()
    before_spans: tuple[tuple[int, int], ...] = ()


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
    first = _covering(spans, cp_start)
    last = _covering(spans, cp_end)
    return {"x_min": pens[first], "x_max": pens[last + 1], "advance_total": pens[-1]}


class Enricher:
    """Holds the loaded spec, the per-config baseline subset tables, the after-font shaper, and the alias map; `enrich` computes every precomputed shard field for one unit under its first config."""

    def __init__(
        self,
        spec: ResolvedSpec,
        subset_dir: Path,
        after_font: Path,
        alias_path: Path | None = None,
        repo_root: Path = REPO_ROOT,
    ):
        self.spec = spec
        self.subset_dir = Path(subset_dir)
        self.after_shaper = Shaper(after_font)
        self.aliases = load_alias_map(alias_path or repo_root / "rebuild" / "m1-aliases.yaml")
        self._subset_rows: dict[str, dict[str, Row]] = {}
        self.mismatches: list[str] = []

    def subset_row(self, config: str, codepoints: str) -> Row | None:
        if config not in self._subset_rows:
            path = self.subset_dir / f"baseline-{config}.subset.tsv.gz"
            table: dict[str, Row] = {}
            if path.exists():
                for row in iter_rows(path):
                    table[":".join(f"{cp:04X}" for cp in row.codepoints)] = row
            self._subset_rows[config] = table
        return self._subset_rows[config].get(codepoints)

    def formed_spans(self, codepoint_values: tuple[int, ...]) -> list[tuple[int, int]]:
        tokens = tokens_from_codepoints(self.spec, codepoint_values)
        formed = form_ligatures(self.spec, tokens)
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

    def enrich(self, unit: Unit) -> EnrichedUnit:
        values = unit.codepoint_values
        config = unit.configs[0]
        features = features_for_config(config)
        overlay = isolated_overlay_active(self.spec, features)
        report = explain(self.spec, list(values), features)
        settled = list(report.settled)
        if overlay:
            settled = overlay_settled(self.spec, settled)

        derived_cells = tuple(cell_token(item.cell) for item in settled)
        if derived_cells != unit.new:
            self.mismatches.append(
                f"{config} {unit.codepoints}: derived cells {derived_cells} != audit {unit.new}"
            )

        after_spans = self.formed_spans(values)
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

        diff_cp, divergent_gaps = self._diff_codepoints(values, row, before_spans, settled, after_spans)
        diff_positions = tuple(sorted({_covering(after_spans, cp) for cp in diff_cp}))
        pair = self._pick_pair(divergent_gaps, diff_positions, after_seams, len(settled))

        shaped = self.after_shaper.shape(
            "".join(chr(value) for value in values), dict.fromkeys(features, True) or None
        )
        after_pens = _pen_positions(shaped.positions)
        after_cluster_spans = _spans_from_clusters(shaped.clusters, len(values))
        before_pens = _pen_positions(row.positions)

        if pair is not None:
            cp_start = after_spans[pair[0]][0]
            cp_end = after_spans[pair[1]][1] - 1
        elif diff_positions:
            cp_start = after_spans[diff_positions[0]][0]
            cp_end = after_spans[diff_positions[-1]][1] - 1
        else:
            cp_start, cp_end = 0, len(values) - 1
        highlight_after = _highlight(after_pens, after_cluster_spans, cp_start, cp_end)
        highlight_before = _highlight(before_pens, before_spans, cp_start, cp_end)

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
        explain_text = _filter_explain(report.render(), diff_positions)

        return EnrichedUnit(
            unit=unit,
            notation=notation(values),
            text_entities=text_entities(values),
            before_glyphs=tuple(row.glyphs),
            before_seams=before_seams,
            after_cells=unit.new,
            after_seams=after_seams,
            after_extensions=after_extensions,
            diff_positions=diff_positions,
            pair=pair,
            highlight_before=highlight_before,
            highlight_after=highlight_after,
            boundary_marks=boundary_marks,
            explain_text=explain_text,
            provenance=provenance,
            report=report,
            diff_traces=diff_traces,
            after_spans=tuple(after_spans),
            before_spans=tuple(before_spans),
        )

    def _diff_codepoints(
        self,
        values: tuple[int, ...],
        row: Row,
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

    @staticmethod
    def _pick_pair(
        divergent_gaps: list[tuple[int, int]],
        diff_positions: tuple[int, ...],
        after_seams: tuple[str, ...],
        cell_count: int,
    ) -> tuple[int, int] | None:
        if divergent_gaps:
            return divergent_gaps[0]
        if not diff_positions or cell_count < 2:
            return None
        for left, right in zip(diff_positions, diff_positions[1:]):
            if right == left + 1:
                return (left, right)
        position = diff_positions[0]
        joins_right = position + 1 < cell_count and after_seams[position] != "break"
        joins_left = position > 0 and after_seams[position - 1] != "break"
        if joins_right or (position + 1 < cell_count and not joins_left):
            return (position, position + 1)
        return (position - 1, position)


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
    """Keep the header lines and only the divergent positions' blocks of an ExplainReport.render()."""
    blocks = rendered.split("\n\nposition ")
    if len(blocks) == 1:
        return rendered
    wanted = set(diff_positions)
    kept = [blocks[0]]
    for block in blocks[1:]:
        index = int(block.split(":", 1)[0].split()[0])
        if not wanted or index in wanted:
            kept.append(block)
    return "\n\nposition ".join(kept)
