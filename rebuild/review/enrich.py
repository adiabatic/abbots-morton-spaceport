"""Unit enrichment for the review surface (rebuild/REVIEW-PLAN.md §2.2): rune-name notation, old seams from the §13.1 baseline subsets, the settle/explain precompute (new seams, extensions, eliminations, render text), divergent-position computation against the alias map, and highlight x-ranges in font units from real kern-neutral shaping of both fonts (the baseline subset rows were extracted with the old font's kerning on, so highlight pens come from live `kern: False` shaping instead — matching the app's `font-kerning: none` rendering)."""

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
from rebuild.review.ink import kern_neutral
from rebuild.validation.rowmodel import Row, iter_rows
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
    """Display tokens aligned one-to-one with codepoint positions: letter names (·May) and the boundary tokens (◊ZWNJ, ␣, ·) exactly as `notation` renders them, so joining them with `notation`'s spacing rule reproduces the caption string."""
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
class SecondarySeam:
    """One divergent adjacency beyond a unit's primary pair: the (left, right) after-cell indices, the same per-side highlight rects the primary band uses, and — after `resolve_secondary_homes` — either the home unit id where this behavior is the primary judgment, None when no home exists, or `suppressed` when the home is ink-identical (nothing visible to judge, so no marker is emitted)."""

    pair: tuple[int, int]
    highlight_before: dict
    highlight_after: dict
    home: str | None = None
    suppressed: bool = False


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
    summary: str = ""
    diff_traces: tuple = ()
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
    first = _covering(spans, cp_start)
    last = _covering(spans, cp_end)
    return {"x_min": pens[first], "x_max": pens[last + 1], "advance_total": pens[-1]}


def _advance_drift_cell(before_pens: list[int], after_pens: list[int], cell_count: int) -> int | None:
    """The first cell whose kern-neutral advance differs between the two fonts, or None. Locates a position-only divergence with no cell- or seam-grain pair — the kern-channel-out-of-scope residue, an advance-only one-pixel drift on the boundary-adjacent letter. The drift's gap is the word break beside it, so the caller marks the nearest boundary token (the ◊ZWNJ / ␣ / · that brackets the gap), not the letter, and never lights up a sample band."""
    limit = min(cell_count, len(before_pens) - 1, len(after_pens) - 1)
    for index in range(limit):
        if before_pens[index + 1] - before_pens[index] != after_pens[index + 1] - after_pens[index]:
            return index
    return None


class Enricher:
    """Holds the loaded spec, the per-config baseline subset tables, a kern-neutral shaper per font, and the alias map; `enrich` computes every precomputed shard field for one unit under its first config."""

    def __init__(
        self,
        spec: ResolvedSpec,
        subset_dir: Path,
        after_font: Path,
        alias_path: Path | None = None,
        repo_root: Path = REPO_ROOT,
        before_font: Path = SENIOR_FONT,
    ):
        self.spec = spec
        self.subset_dir = Path(subset_dir)
        self.after_shaper = Shaper(after_font)
        self.before_shaper = Shaper(before_font)
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
        # The audit's `new` column is overloaded: for cell/seam rows it is the settled cell tokens, but for position-only rows (the kern-channel-out-of-scope residue) it carries per-slot position diagnostics, never cell tokens. Compare the re-settlement against the audit only when `new` is cell-shaped, and always render `after_cells` from the re-derived cells so they parallel `after_seams`.
        if all("/" in token for token in unit.new) and derived_cells != unit.new:
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

        hb_features = kern_neutral(dict.fromkeys(features, True))
        shaped = self.after_shaper.shape("".join(chr(value) for value in values), hb_features)
        after_pens = _pen_positions(shaped.positions)
        after_cluster_spans = _spans_from_clusters(shaped.clusters, len(values))
        # The subset row's positions were extracted with the old font's kerning on; the before pens come from a live kern-neutral re-shape instead, with the glyph identities checked against the audited row.
        before_shaped = self.before_shaper.shape("".join(chr(value) for value in values), hb_features)
        if before_shaped.names != tuple(row.glyphs):
            self.mismatches.append(
                f"{config} {unit.codepoints}: kern-neutral before glyphs {before_shaped.names} != subset row {tuple(row.glyphs)}"
            )
        before_pens = _pen_positions(before_shaped.positions)

        pair_codepoints = (after_spans[pair[0]][0], after_spans[pair[1]][1] - 1) if pair is not None else None
        if pair is None and not diff_positions:
            drifted = _advance_drift_cell(before_pens, after_pens, len(settled))
            if drifted is not None:
                boundaries = [i for i in range(len(settled)) if values[after_spans[i][0]] in BOUNDARIES]
                mark = min(boundaries, key=lambda i: (abs(i - drifted), i)) if boundaries else drifted
                pair_codepoints = (after_spans[mark][0], after_spans[mark][1] - 1)
        if pair is not None:
            cp_start, cp_end = pair_codepoints
        elif diff_positions:
            cp_start = after_spans[diff_positions[0]][0]
            cp_end = after_spans[diff_positions[-1]][1] - 1
        else:
            cp_start, cp_end = 0, len(values) - 1
        highlight_after = _highlight(after_pens, after_cluster_spans, cp_start, cp_end)
        highlight_before = _highlight(before_pens, before_spans, cp_start, cp_end)

        secondary_seams: list[SecondarySeam] = []
        if pair is not None and not unit.ink_identical:
            for left, right in _secondary_pairs(
                pair, divergent_gaps, diff_positions, after_seams, len(settled)
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
        explain_text = _filter_explain(report.render(), diff_positions)
        summary = _summarize(
            settled=settled,
            after_spans=after_spans,
            after_seams=after_seams,
            before_glyphs=tuple(row.glyphs),
            before_spans=before_spans,
            before_seams=before_seams,
            diff_positions=diff_positions,
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
            report=report,
            summary=summary,
            diff_traces=diff_traces,
            after_spans=tuple(after_spans),
            before_spans=tuple(before_spans),
            secondary_seams=tuple(secondary_seams),
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


def _secondary_pairs(
    primary: tuple[int, int],
    divergent_gaps: list[tuple[int, int]],
    diff_positions: tuple[int, ...],
    after_seams: tuple[str, ...],
    cell_count: int,
) -> tuple[tuple[int, int], ...]:
    """Every divergent adjacency beyond the primary pair, in left-index order: the remaining divergent gaps, plus a derived neighbor seam for each divergent position not already covered by the primary or a gap (mirroring `_pick_pair`'s adjacency-then-join-direction fallback)."""
    pairs: list[tuple[int, int]] = []

    def add(candidate: tuple[int, int]) -> None:
        if candidate != primary and candidate not in pairs:
            pairs.append(candidate)

    for gap in divergent_gaps:
        add(gap)
    covered = {primary[0], primary[1]}
    for left, right in divergent_gaps:
        covered.update((left, right))
    remaining = [position for position in diff_positions if position not in covered]
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


def _seam_outcomes_match(
    item: EnrichedUnit, left: int, right: int, candidate: EnrichedUnit, offset: int
) -> bool:
    """Whether `candidate`, occurring at codepoint `offset` inside `item`, has the same before AND after outcomes at the seam (item's after cells `left`/`right`) — identical covering spans after offset adjustment, identical glyph/cell identities, identical seam tokens — and judges that seam as its own primary pair."""
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
    item: EnrichedUnit,
    seam: SecondarySeam,
    by_codepoints: dict[tuple[int, ...], list[EnrichedUnit]],
) -> EnrichedUnit | None:
    """The seam's home: the shortest unit in the universe whose codepoint string is a substring of `item`'s containing the seam's two cells, with matching before/after outcomes at the seam and that seam as its primary pair. Shortest substring length wins; ties break to the lowest unit id. None when no unit qualifies."""
    values = item.unit.codepoint_values
    left, right = seam.pair
    minimum = item.after_spans[right][1] - item.after_spans[left][0]
    for length in range(minimum, len(values) + 1):
        matches: list[EnrichedUnit] = []
        first_offset = max(0, item.after_spans[right][1] - length)
        last_offset = min(item.after_spans[left][0], len(values) - length)
        for offset in range(first_offset, last_offset + 1):
            window = values[offset : offset + length]
            for candidate in by_codepoints.get(window, ()):
                if candidate.unit.unit_id == item.unit.unit_id:
                    continue
                if _seam_outcomes_match(item, left, right, candidate, offset):
                    matches.append(candidate)
        if matches:
            return min(matches, key=lambda match: match.unit.unit_id)
    return None


def resolve_secondary_homes(enriched_units: list[EnrichedUnit]) -> dict[str, int]:
    """Resolve every secondary seam's home unit across the whole universe, mutating the seams in place, and return the census. A seam whose home is ink-identical is suppressed (the divergence is an invisible name-grain rename, so no marker is emitted); a seam with no home keeps `home: None` and is still emitted so it is never silently unmarked."""
    by_codepoints: dict[tuple[int, ...], list[EnrichedUnit]] = {}
    for item in enriched_units:
        by_codepoints.setdefault(item.unit.codepoint_values, []).append(item)
    census = {
        "units_with_markers": 0,
        "seams_homed": 0,
        "seams_homeless": 0,
        "seams_suppressed_invisible": 0,
    }
    for item in enriched_units:
        visible = 0
        for seam in item.secondary_seams:
            home = _find_home(item, seam, by_codepoints)
            if home is None:
                census["seams_homeless"] += 1
                visible += 1
            elif home.unit.ink_identical:
                seam.suppressed = True
                census["seams_suppressed_invisible"] += 1
            else:
                seam.home = home.unit.unit_id
                census["seams_homed"] += 1
                visible += 1
        if visible:
            census["units_with_markers"] += 1
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
    """The always-visible one-line prose summary: what the new pipeline chose at the primary divergence and the single deciding record, e.g. "New: ·May joins ·It at the baseline (the old pipeline broke there) — decided by qsMay.yaml policy.extend[3] (join-count rank)."."""
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
