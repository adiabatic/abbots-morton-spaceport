"""The three verdict-export drafters (rebuild/REVIEW-PLAN.md §4.3): the approve pin (whole-word data-expect, syntax-checked with the repo's real parser and semantics-checked against the after font through the rebuild-side shaping harness), the reject policy edit (the smallest one-line refuse/contract/prefer counter-lever naming the provenance records that decided the new outcome, or no draft when a name-grain divergence has no one-line counter-lever), and the fine-either-way any-of record (both behaviors as full expect strings)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

from rebuild.pipeline.conform import load_alias_map
from rebuild.pipeline.model import CellId
from rebuild.pipeline.settle import is_boundary_settled
from rebuild.pipeline.spec_load import _SchemaChecker
from rebuild.review.enrich import (
    LETTERS,
    NAMER_DOT,
    SPACE,
    ZWNJ,
    EnrichedUnit,
    letter_display,
)
from rebuild.review.ink import kern_neutral
from rebuild.validation.classify import SeamClassifier
from rebuild.validation.pins import ReplayReport, _check_interpretation
from rebuild.validation.rowmodel import config_token_for_features
from rebuild.validation.shaping import Shaper, row_for

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_FILES = ("site/index.html", "site/the-manual.html", "site/extra-senior-words.html")
SUGGESTED_HOME = "site/the-manual.html"

CONNECTORS = {"break": " | ", "y0": " ~b~ ", "y5": " ~x~ ", "y6": " ~6~ ", "y8": " ~t~ "}
_BOUNDARY_EXPECT = {SPACE: "◊space", ZWNJ: "◊ZWNJ", NAMER_DOT: "\\·"}
_BOUNDARY_QS_TEXT = {SPACE: "space", ZWNJ: "ZWNJ", NAMER_DOT: "·"}

_test_shaping: Any = None


def _import_test_shaping() -> Any:
    global _test_shaping
    if _test_shaping is None:
        test_dir = str(REPO_ROOT / "test")
        if test_dir not in sys.path:
            sys.path.insert(0, test_dir)
        import test_shaping

        _test_shaping = test_shaping
    return _test_shaping


@dataclass(frozen=True)
class PinDraft:
    expect: str
    attribute: str
    stylistic_set: str | None
    syntax: str
    semantics_after_font: str
    duplicate_of: str | None
    suggested_home: str = SUGGESTED_HOME

    def to_json(self) -> dict:
        return {
            "expect": self.expect,
            "attribute": self.attribute,
            "stylistic_set": self.stylistic_set,
            "syntax": self.syntax,
            "semantics_after_font": self.semantics_after_font,
            "duplicate_of": self.duplicate_of,
            "suggested_home": self.suggested_home,
        }


@dataclass(frozen=True)
class PolicyDraft:
    file: str
    keypath: str
    suggested_record: str
    names_provenance: tuple[str, ...]
    decided_stage: str
    schema_valid: bool
    why_stub: str

    def to_json(self) -> dict:
        return {
            "file": self.file,
            "keypath": self.keypath,
            "suggested_record": self.suggested_record,
            "names_provenance": list(self.names_provenance),
            "decided_stage": self.decided_stage,
            "schema_valid": self.schema_valid,
            "why_stub": self.why_stub,
        }


@dataclass(frozen=True)
class AnyOfDraft:
    text: str
    features: dict
    candidates: tuple[str, ...]

    def to_json(self) -> dict:
        return {"text": self.text, "features": self.features, "candidates": list(self.candidates)}


def stylistic_set_value(configs: tuple[str, ...]) -> str | None:
    """The pin's data-stylistic-set attribute value: null when the unit holds under the default configuration, else the first config as zero-padded space-separated set numbers ("ss02+ss03" → "02 03")."""
    if "default" in configs:
        return None
    return " ".join(tag.removeprefix("ss") for tag in configs[0].split("+"))


def features_dict(configs: tuple[str, ...]) -> dict[str, bool]:
    if "default" in configs:
        return {}
    return {tag: True for tag in configs[0].split("+")}


def _token_for_span(codepoint_values: tuple[int, ...], span: tuple[int, int]) -> str:
    start, end = span
    if end - start > 1:
        parts = [letter_display(LETTERS[codepoint_values[index]]) for index in range(start, end)]
        return parts[0] + "+" + "".join(part.lstrip("·") for part in parts[1:])
    value = codepoint_values[start]
    if value in LETTERS:
        return letter_display(LETTERS[value])
    return _BOUNDARY_EXPECT[value]


def expect_string(
    codepoint_values: tuple[int, ...],
    spans: tuple[tuple[int, int], ...],
    seams: tuple[str, ...],
) -> str:
    """A whole-word expect string at glyph grain: bare letter tokens (no variant assertions), ◊space/◊ZWNJ/\\· boundary tokens, +-joined ligature tokens, and the seam-to-connector map (break → |, y5 → ~x~, y0 → ~b~, y6 → ~6~, y8 → ~t~)."""
    parts = [_token_for_span(codepoint_values, spans[0])]
    for index, seam in enumerate(seams):
        parts.append(CONNECTORS[seam])
        parts.append(_token_for_span(codepoint_values, spans[index + 1]))
    return "".join(parts)


class _PinCollector(HTMLParser):
    """A light data-expect cell scanner for duplicate discipline: records (text content, attribute kind, cell stylistic_set, line) for every td/span/dd carrying either expect attribute. Coarser than the test suite's run-aware collector on purpose — duplicate detection needs text plus cell-grain feature context only."""

    _TAGS = {"td", "span", "dd"}

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[tuple[str, str, str | None, int]] = []
        self._stack: list[tuple[str, bool]] = []
        self._depth_active = 0
        self._text: list[str] = []
        self._attribute = ""
        self._stylistic_set: str | None = None
        self._line = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._TAGS:
            return
        attr_dict = dict(attrs)
        has_pin = "data-expect" in attr_dict or "data-expect-noncanonically" in attr_dict
        if has_pin and self._depth_active == 0:
            self._attribute = "data-expect" if "data-expect" in attr_dict else "data-expect-noncanonically"
            self._stylistic_set = attr_dict.get("data-stylistic-set")
            self._line = self.getpos()[0]
            self._text = []
            self._stack.append((tag, True))
            self._depth_active = 1
        elif self._depth_active:
            self._stack.append((tag, False))
            self._depth_active += 1

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._TAGS or not self._depth_active or not self._stack:
            return
        open_tag, is_cell = self._stack.pop()
        self._depth_active -= 1
        if open_tag == tag and is_cell and self._depth_active == 0:
            text = "".join(self._text).strip()
            if text:
                self.cells.append((text, self._attribute, self._stylistic_set, self._line))

    def handle_data(self, data: str) -> None:
        if self._depth_active:
            self._text.append(data)


def build_corpus_index(repo_root: Path = REPO_ROOT, files: tuple[str, ...] = CORPUS_FILES) -> dict:
    """(text, config token) → {"source": "file:line", "attribute": ...} for every pinned corpus cell."""
    index: dict[tuple[str, str], dict] = {}
    for rel in files:
        path = repo_root / rel
        if not path.exists():
            continue
        collector = _PinCollector()
        collector.feed(path.read_text(encoding="utf-8"))
        for text, attribute, stylistic_set, line in collector.cells:
            features = {f"ss{ss.zfill(2)}": True for ss in stylistic_set.split()} if stylistic_set else {}
            token = config_token_for_features(features) or "non-covered"
            index.setdefault((text, token), {"source": f"{rel}:{line}", "attribute": attribute})
    return index


class Drafter:
    def __init__(
        self,
        after_font: Path,
        schema_path: Path | None = None,
        corpus_index: dict | None = None,
        repo_root: Path = REPO_ROOT,
        alias_path: Path | None = None,
    ):
        self.after_shaper = Shaper(after_font)
        self.after_classifier = SeamClassifier(after_font)
        self.corpus_index = corpus_index if corpus_index is not None else build_corpus_index(repo_root)
        self.aliases = load_alias_map(alias_path or repo_root / "rebuild" / "m1-aliases.yaml")
        schema = json.loads(
            (schema_path or repo_root / "rebuild" / "schema" / "rune.schema.json").read_text()
        )
        defs = schema.get("$defs", {})
        self._refuse_checker = _SchemaChecker(
            {"$ref": "#/$defs/refuseRecord", "$defs": defs}, "rune.schema.json"
        )
        self._prefer_checker = _SchemaChecker(
            {"$ref": "#/$defs/preferRecord", "$defs": defs}, "rune.schema.json"
        )
        self._contract_checker = _SchemaChecker(
            {"$ref": "#/$defs/contractRecord", "$defs": defs}, "rune.schema.json"
        )

    # --- the approve pin -----------------------------------------------------

    def draft_pin(self, enriched: EnrichedUnit) -> PinDraft:
        unit = enriched.unit
        values = unit.codepoint_values
        expect = expect_string(values, enriched.after_spans, enriched.after_seams)
        ts = _import_test_shaping()
        syntax = "pass"
        try:
            ts.parse_expect(expect)
        except ValueError as error:
            syntax = f"fail: {error}"
        text = "".join(chr(value) for value in values)
        config_token = unit.configs[0]
        semantics = self.validate_semantics(text, expect, features_dict(unit.configs) or None)
        hit = self.corpus_index.get((text, config_token))
        canonical = hit is not None and hit["attribute"] == "data-expect"
        return PinDraft(
            expect=expect,
            attribute="data-expect" if canonical else "data-expect-noncanonically",
            stylistic_set=stylistic_set_value(unit.configs),
            syntax=syntax,
            semantics_after_font=semantics,
            duplicate_of=hit["source"] if hit is not None else None,
        )

    def validate_semantics(self, text: str, expect: str, features: dict | None) -> str:
        """Shape `text` against the after font and replay the expect string's assertions through the validation suite's interpretation checker. The old corpus convention maps ◊ZWNJ to the `space` glyph; the rebuild font gives U+200C its own `uni200C` glyph, so shaped uni200C slots are normalized to `space` before the check."""
        ts = _import_test_shaping()
        try:
            tokens, connections = ts.parse_expect(expect)
        except ValueError as error:
            return f"fail: unparseable: {error}"
        row = row_for(self.after_shaper, self.after_classifier, text, kern_neutral(features))
        row = replace(row, glyphs=tuple("space" if g == "uni200C" else g for g in row.glyphs))
        report = ReplayReport()
        errors: list[str] = []
        for interp_tokens, interp_connections in ts._expand_maybe_ligatures(list(tokens), list(connections)):
            error, _seams, _identity, _skips = _check_interpretation(
                text, interp_tokens, interp_connections, row, report
            )
            if error is None:
                return "pass"
            errors.append(error)
        return "fail: " + " // ".join(errors)

    # --- the reject policy edit ----------------------------------------------

    def draft_policy(self, enriched: EnrichedUnit, note: str = "") -> PolicyDraft | None:
        unit = enriched.unit
        position = self._policy_position(enriched)
        if position is None:
            return None
        settled = enriched.report.positions[position].trace.settled
        cell = settled.cell
        rune = cell.rune
        why = f"Reviewer rejected the M1 outcome for {unit.codepoints} ({enriched.notation})"
        if note:
            why += f": {note}"

        new_join_side = self._new_join_side(enriched, position)
        extension_side = self._gained_extension_side(enriched, position)
        if new_join_side is not None and enriched.provenance:
            anchor = cell.exit if new_join_side == "exit" else cell.entry
            record: dict = {new_join_side: anchor}
            record["when"] = self._window_for_side(enriched, position, new_join_side)
            record["why"] = why
            keypath = "policy.refuse[+]"
            schema_valid = not self._refuse_checker.check(record)
        elif extension_side is not None and any("policy.extend" in p for p in enriched.provenance):
            side, height, amount = extension_side
            when = self._window_for_side(enriched, position, side)
            record = {side: height, "by": amount, "when": when, "why": why}
            keypath = "policy.contract[+]"
            schema_valid = not self._contract_checker.check(record)
        elif enriched.provenance and self._seam_identical(enriched):
            pinned = self._baseline_cell_pin(enriched, position, cell, why)
            if pinned is None:
                return None
            record = pinned
            keypath = "policy.prefer[+]"
            schema_valid = not self._prefer_checker.check(record)
        elif enriched.provenance:
            record = {}
            if cell.exit is not None:
                record["exit"] = cell.exit
            else:
                record["stance"] = cell.stance
            record["when"] = self._window_when(enriched, position)
            record["why"] = why
            keypath = "policy.refuse[+]"
            schema_valid = not self._refuse_checker.check(record)
        else:
            baseline_exit = self._baseline_exit(enriched, position)
            when = self._window_when(enriched, position)
            record = {"cell": {"exit": baseline_exit}, "when": when, "why": why}
            keypath = "policy.prefer[+]"
            schema_valid = not self._prefer_checker.check(record)

        return PolicyDraft(
            file=f"glyph_data/runes/{rune}.yaml",
            keypath=keypath,
            suggested_record=yaml.safe_dump(
                record, default_flow_style=True, width=10**6, allow_unicode=True, sort_keys=False
            ).strip(),
            names_provenance=enriched.provenance,
            decided_stage=enriched.report.positions[position].trace.decided_stage,
            schema_valid=schema_valid,
            why_stub=why,
        )

    def _new_join_side(self, enriched: EnrichedUnit, position: int) -> str | None:
        """When a gap adjacent to the divergent cell is joined in the new behavior but was a break in the baseline, the smallest counter-lever is a refuse on the anchor that reaches across that gap (REVIEW-PLAN §4.3: positive-record outcomes get a refuse) — a contract could only shrink an extension, never restore the break. Returns "exit" or "entry" for the side carrying the new join, or None."""
        cell = enriched.report.positions[position].trace.settled.cell
        if (
            position < len(enriched.after_seams)
            and enriched.after_seams[position] != "break"
            and cell.exit is not None
            and self._before_seam_at_gap(enriched, enriched.after_spans[position][1] - 1) == "break"
        ):
            return "exit"
        if (
            position > 0
            and enriched.after_seams[position - 1] != "break"
            and cell.entry is not None
            and self._before_seam_at_gap(enriched, enriched.after_spans[position][0] - 1) == "break"
        ):
            return "entry"
        return None

    def _seam_identical(self, enriched: EnrichedUnit) -> bool:
        """A name-grain divergence: both behaviors group the codepoints identically and agree on every seam, so the units differ only in which cell renders at some position."""
        return len(enriched.before_glyphs) == len(enriched.after_cells) and tuple(
            enriched.before_seams
        ) == tuple(enriched.after_seams)

    def _baseline_cell_pin(
        self, enriched: EnrichedUnit, position: int, cell: CellId, why: str
    ) -> dict | None:
        """The prefer record pinning the baseline cell on a seam-identical name-grain divergence (a refuse here would break a join both fonts share). Expressible when the alias map's cell for the baseline glyph differs from the new cell in entry/exit anchors or stance; adjustment-grain differences (locked twins, bind pullbacks, suppressed extensions) have no one-line counter-lever and yield no draft."""
        span_start = enriched.after_spans[position][0]
        before_index = 0
        for index, (start, end) in enumerate(enriched.before_spans):
            if start <= span_start < end:
                before_index = index
        alias = self.aliases.get(enriched.before_glyphs[before_index])
        if not isinstance(alias, CellId):
            return None
        pin: dict = {}
        over: dict = {}
        for anchor in ("entry", "exit"):
            old_value = getattr(alias, anchor)
            new_value = getattr(cell, anchor)
            if old_value != new_value:
                pin[anchor] = old_value or "none"
                over[anchor] = new_value or "none"
        if pin:
            side = "exit" if "exit" in pin else "entry"
            when = self._window_for_side(enriched, position, side)
            return {"cell": pin, "over": over, "mode": "absolute", "when": when, "why": why}
        if alias.stance != cell.stance:
            when = self._window_when(enriched, position)
            return {"stance": alias.stance, "mode": "absolute", "when": when, "why": why}
        return None

    @staticmethod
    def _gained_extension_side(enriched: EnrichedUnit, position: int) -> tuple[str, str, int] | None:
        """When the divergent cell carries an en-ext/ex-ext adjustment the baseline glyph at the same position lacks, the smallest counter-lever is a contract record on that side, not a refuse: returns (side keyword, height name, pixels). The caller checks `_new_join_side` first — an extension riding a join the baseline didn't have needs a refuse, because contracting it would keep the unwanted join."""
        settled = enriched.report.positions[position].trace.settled
        cell = settled.cell
        span_start = enriched.after_spans[position][0]
        before_index = 0
        for index, (start, end) in enumerate(enriched.before_spans):
            if start <= span_start < end:
                before_index = index
        before_parts = set(enriched.before_glyphs[before_index].split(".")[1:])
        for token in cell.adjustments:
            if token in before_parts:
                continue
            if token.startswith("en-ext-") and cell.entry is not None:
                return ("entry", cell.entry, int(token.rsplit("-", 1)[1]))
            if token.startswith("ex-ext-") and cell.exit is not None:
                return ("exit", cell.exit, int(token.rsplit("-", 1)[1]))
        return None

    def _policy_position(self, enriched: EnrichedUnit) -> int | None:
        positions = enriched.report.positions
        for index in enriched.diff_positions:
            if index < len(positions) and not is_boundary_settled(positions[index].trace.settled):
                return index
        for index, position in enumerate(positions):
            if not is_boundary_settled(position.trace.settled):
                return index
        return None

    def _window_for_side(self, enriched: EnrichedUnit, position: int, side: str) -> dict:
        """The window across the gap the record targets: an exit-side lever scopes to the right neighbor, an entry-side lever to the left, falling back to `_window_when` when that neighbor is a boundary."""
        positions = enriched.report.positions
        if side == "exit" and position + 1 < len(positions):
            right = positions[position + 1].trace.settled
            if not is_boundary_settled(right):
                return {"right": {"family": [right.cell.rune]}}
        if side == "entry" and position > 0:
            left = positions[position - 1].trace.settled
            if not is_boundary_settled(left):
                return {"left": {"family": [left.cell.rune]}}
        return self._window_when(enriched, position)

    def _window_when(self, enriched: EnrichedUnit, position: int) -> dict:
        positions = enriched.report.positions
        if position + 1 < len(positions):
            right = positions[position + 1].trace.settled
            if not is_boundary_settled(right):
                return {"right": {"family": [right.cell.rune]}}
        if position > 0:
            left = positions[position - 1].trace.settled
            if not is_boundary_settled(left):
                return {"left": {"family": [left.cell.rune]}}
        return {"word": "isolated"}

    def _baseline_exit(self, enriched: EnrichedUnit, position: int) -> str:
        """The before behavior's exit state at the divergent position, read from the before seam at the codepoint gap that follows the divergent cell ("none" when the baseline broke there too)."""
        gap = enriched.after_spans[position][1] - 1
        if gap < len(enriched.unit.codepoint_values) - 1:
            seam = self._before_seam_at_gap(enriched, gap)
            if seam is not None and seam.startswith("y"):
                return {"y0": "baseline", "y5": "x-height", "y6": "y6", "y8": "top"}.get(seam, "none")
        return "none"

    @staticmethod
    def _before_seam_at_gap(enriched: EnrichedUnit, gap: int) -> str | None:
        for index in range(len(enriched.before_spans) - 1):
            if enriched.before_spans[index + 1][0] - 1 == gap:
                return enriched.before_seams[index]
        return None

    # --- the fine-either-way any-of record -------------------------------------

    def draft_any_of(self, enriched: EnrichedUnit) -> AnyOfDraft:
        unit = enriched.unit
        values = unit.codepoint_values
        after = expect_string(values, enriched.after_spans, enriched.after_seams)
        before = expect_string(values, enriched.before_spans, enriched.before_seams)
        candidates: list[str] = [after]
        if before != after:
            candidates.append(before)
        text_tokens = [LETTERS[value] if value in LETTERS else _BOUNDARY_QS_TEXT[value] for value in values]
        return AnyOfDraft(
            text=" ".join(text_tokens),
            features=features_dict(unit.configs),
            candidates=tuple(candidates),
        )
