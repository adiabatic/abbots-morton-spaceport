"""Conformance gates (M1-PLAN sections 5 and 6, Group 3): HarfBuzz vs the settlement function, and the settlement function vs the section 13.1 baseline oracle.

`run_conformance` promotes prototype/conform.py: the Shaper (MONOTONE_CHARACTERS cluster level; names via TTFont, never HarfBuzz's truncating API), the exhaustive length-1..5 enumeration per acceptance configuration, the ZWNJ structural checks (zero advance, no ink), split-buffer equivalence, gap-0 pen positions, and rule coverage (every emitted settlement rule fired). The section 10 tier-3 per-transition gate rides on the same sweep: at M1 scale the exhaustive enumeration covers every decision-table transition whose shortest window fits in five tokens — ZWNJ-interleaved variants included, because ZWNJ is in the enumerated alphabet — and any transition left uncovered is reported with a BFS-derived shortest example sequence, shaped, and diffed as a top-up. The font-vs-settle diff takes no ledger: any divergence is a compiler defect by definition.

`compare_against_baseline` is the section 6 oracle gate: stream the filtered sub-tables, run `settle` per row, compare ligation (clusters), per-seam classification, and cell identity through the hand-written alias map; every divergent row must match exactly one ledger entry (zero matches fails conformance, two-plus fails the ledger). When a font path is supplied, the gate also compares positions old-vs-new (M1-PLAN section 6 step 3d): each row is shaped against the new font and its per-slot (x_offset, y_offset, x_advance) triples are compared against the baseline's, with sidecar kerns normalized out of the old advances via `KernEvaluator` (the new font emits no kerning). Position equality is enforced on every row whose seam topology and ligation match the baseline and whose cell-grain divergence class (if any) claims ink identity (`ink_identical: true` in the ledger); rows whose matched class legitimately redraws ink (extensions restored or suppressed, withdrawal bindings) are excluded and counted, because their advances move with the ink by design.

Group 2's `settle` and `table` modules are imported lazily inside the entry points, so this module loads (and its helpers unit-test) before Group 2 lands.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping

import yaml

from rebuild.pipeline import geometry
from rebuild.pipeline.model import (
    CellId,
    GlyphRecord,
    ResolvedSpec,
    Settled,
    feature_config_token,
    marker_glyph_name,
    relevant_marker_features,
)
from rebuild.validation.rowmodel import CONFIGS, Row, iter_rows

ZWNJ = "\u200c"
ZWNJ_SENTINEL = "<zwnj>"
BOUNDARY_GLYPH_NAMES = {"space", "uni200C", "periodcentered", "periodcentered.lowered"}
ACCEPTANCE_CONFIGS = ("default", "ss02", "ss03", "ss04", "ss05", "ss02+ss03", "ss02+ss03+ss05", "ss10")


@dataclass
class Divergence:
    text: str
    config: str
    position: int
    expected: str
    got: str
    kind: str


@dataclass
class ConformReport:
    font: str
    sequences: int = 0
    shaping_runs: int = 0
    divergences: list[Divergence] = field(default_factory=list)
    uncovered_rules: int = 0
    uncovered_transitions: int = 0
    topped_up_sequences: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.divergences and self.uncovered_rules == 0 and self.uncovered_transitions == 0

    def write(self, path: Path) -> None:
        by_kind: dict[str, int] = {}
        for divergence in self.divergences:
            by_kind[divergence.kind] = by_kind.get(divergence.kind, 0) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "font": self.font,
                    "sequences": self.sequences,
                    "shaping_runs": self.shaping_runs,
                    "divergences": len(self.divergences),
                    "divergences_by_kind": by_kind,
                    "uncovered_rules": self.uncovered_rules,
                    "uncovered_transitions": self.uncovered_transitions,
                    "topped_up_sequences": self.topped_up_sequences,
                    "pass": self.passed,
                    "notes": self.notes,
                },
                indent=2,
            )
            + "\n"
        )


class Shaper:
    def __init__(self, font_path: Path):
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont

        self._hb = hb
        self.font_path = Path(font_path)
        self.tt = TTFont(str(font_path))
        self.hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(font_path))))
        self.glyph_set = self.tt.getGlyphSet()
        self._ink_cache: dict[str, bool] = {}
        self._outline_cache: dict[str, tuple] = {}

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        hb = self._hb
        buf = hb.Buffer()
        # MONOTONE_CHARACTERS keeps each input character in its own cluster, so the ZWNJ slot stays identifiable.
        buf.cluster_level = hb.BufferClusterLevel.MONOTONE_CHARACTERS
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {tag: True for tag in features})
        return [
            {
                "name": self.tt.getGlyphName(info.codepoint),
                "gid": info.codepoint,
                "cluster": info.cluster,
                "x_advance": pos.x_advance,
                "x_offset": pos.x_offset,
                "y_offset": pos.y_offset,
            }
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)
        ]

    def has_ink(self, glyph_name: str) -> bool:
        cached = self._ink_cache.get(glyph_name)
        if cached is None:
            from fontTools.pens.boundsPen import BoundsPen

            pen = BoundsPen(self.glyph_set)
            self.glyph_set[glyph_name].draw(pen)
            cached = pen.bounds is not None
            self._ink_cache[glyph_name] = cached
        return cached

    def outline_signature(self, glyph_name: str) -> tuple:
        cached = self._outline_cache.get(glyph_name)
        if cached is None:
            from fontTools.pens.recordingPen import RecordingPen

            pen = RecordingPen()
            self.glyph_set[glyph_name].draw(pen)
            cached = tuple(pen.value)
            self._outline_cache[glyph_name] = cached
        return cached


def spec_alphabet(spec: ResolvedSpec) -> tuple[str, ...]:
    codepoints = sorted(
        [rune.codepoint for rune in spec.runes.values() if rune.codepoint is not None]
        + [token.codepoint for token in spec.registry.boundary_tokens.values()]
    )
    return tuple(chr(cp) for cp in codepoints)


def features_for_config(config: str) -> frozenset[str]:
    return frozenset(tag for tag, on in CONFIGS[config].items() if on)


def zwnj_slots(text: str, shaped: list[dict]) -> set[int]:
    return {
        index
        for index, glyph in enumerate(shaped)
        if glyph["cluster"] < len(text) and text[glyph["cluster"]] == ZWNJ
    }


def splitting_boundary_chars(spec: ResolvedSpec) -> frozenset[str]:
    """The characters of every run-splitting boundary token (space and ZWNJ today; the namer dot deliberately does not split runs and is excluded)."""
    return frozenset(
        chr(token.codepoint) for token in spec.registry.boundary_tokens.values() if token.splits_runs
    )


def normalize_actual(text: str, shaped: list[dict]) -> list[str]:
    slots = zwnj_slots(text, shaped)
    return [
        (
            ZWNJ_SENTINEL
            if index in slots
            else ("periodcentered" if glyph["name"] == "periodcentered.lowered" else glyph["name"])
        )
        for index, glyph in enumerate(shaped)
    ]


def normalize_expected(names: list[str]) -> list[str]:
    return [ZWNJ_SENTINEL if name in ("uni200C", "zwnj", ZWNJ) else name for name in names]


def settled_names(
    spec: ResolvedSpec, settled: Iterable, glyph_names: Mapping[CellId, str] | None = None
) -> list[str]:
    """Tolerant Settled-to-name adapter: an item exposing `glyph_name` wins; otherwise the cell maps through the supplied inventory or the generated display name; boundary items render as their token glyph."""
    names: list[str] = []
    for item in settled:
        direct = getattr(item, "glyph_name", None)
        if isinstance(direct, str):
            names.append(direct)
            continue
        cell = getattr(item, "cell", None)
        if cell is None:
            names.append(str(item))
            continue
        if isinstance(cell, CellId) and getattr(cell, "stance", None) == "boundary":
            names.append(
                {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}.get(cell.rune, cell.rune)
            )
            continue
        if isinstance(cell, CellId) and cell.rune in spec.runes:
            if glyph_names and cell in glyph_names:
                names.append(glyph_names[cell])
            else:
                names.append(geometry.display_name(spec, cell))
        else:
            names.append(getattr(cell, "rune", str(cell)))
    return names


def check_oracle(text, config, shaped, expected, divergences, modes) -> None:
    actual = normalize_actual(text, shaped)
    expected = normalize_expected(expected)
    if len(actual) != len(expected):
        actual_dropped = [name for name in actual if name != ZWNJ_SENTINEL]
        expected_dropped = [name for name in expected if name != ZWNJ_SENTINEL]
        if len(actual_dropped) == len(expected_dropped):
            modes.add("oracle omits ZWNJ slots; comparing with ZWNJ slots dropped")
            actual, expected = actual_dropped, expected_dropped
        else:
            divergences.append(
                Divergence(text, config, -1, f"{len(expected)} glyphs", f"{len(actual)} glyphs", "length")
            )
            return
    for index, (want, got) in enumerate(zip(expected, actual)):
        if want != got:
            divergences.append(Divergence(text, config, index, want, got, "name"))
            return


def check_zwnj_structure(text, config, shaper: Shaper, shaped, divergences) -> None:
    for index in sorted(zwnj_slots(text, shaped)):
        glyph = shaped[index]
        if glyph["x_advance"] != 0:
            divergences.append(
                Divergence(
                    text,
                    config,
                    index,
                    "x_advance 0 at ZWNJ slot",
                    f"x_advance {glyph['x_advance']} ({glyph['name']})",
                    "zwnj-advance",
                )
            )
        if shaper.has_ink(glyph["name"]):
            divergences.append(
                Divergence(
                    text, config, index, "no ink at ZWNJ slot", f"inked glyph {glyph['name']}", "zwnj-ink"
                )
            )


def _slot_signature(shaper: Shaper, glyph: dict) -> tuple:
    return (shaper.outline_signature(glyph["name"]), glyph["x_advance"], glyph["x_offset"], glyph["y_offset"])


def check_split_buffer(
    text, config, features, shaper: Shaper, shaped, divergences, splitters: frozenset[str] = frozenset({ZWNJ})
) -> None:
    """Run-splitting-boundary split-buffer equivalence: with every splitter slot dropped, the buffer must match its splitter-separated segments shaped alone, compared per slot on (outline, advance, offsets) — name-blind, because locked twins are bitmap-identical to the bare runes by design."""
    slots = {
        index
        for index, glyph in enumerate(shaped)
        if glyph["cluster"] < len(text) and text[glyph["cluster"]] in splitters
    }
    full = [glyph for index, glyph in enumerate(shaped) if index not in slots]
    segments, current = [], []
    for ch in text:
        if ch in splitters:
            if current:
                segments.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        segments.append("".join(current))
    split: list[dict] = []
    for segment in segments:
        split.extend(shaper.shape(segment, features))
    if len(full) != len(split):
        divergences.append(
            Divergence(
                text, config, -1, f"{len(split)} glyphs (split)", f"{len(full)} glyphs (full)", "split-length"
            )
        )
        return
    for index, (full_glyph, split_glyph) in enumerate(zip(full, split)):
        if _slot_signature(shaper, full_glyph) != _slot_signature(shaper, split_glyph):
            divergences.append(
                Divergence(
                    text,
                    config,
                    index,
                    f"{split_glyph['name']} (split halves)",
                    f"{full_glyph['name']} (full)",
                    "split",
                )
            )
            return


def check_join_gaps(
    text, config, shaper: Shaper, shaped, anchors_of: Callable[[str], dict | None], divergences
) -> None:
    pen = 0
    origins = []
    for glyph in shaped:
        origins.append((pen + glyph["x_offset"], glyph["y_offset"]))
        pen += glyph["x_advance"]
    for index in range(len(shaped) - 1):
        left, right = shaped[index], shaped[index + 1]
        left_anchors = anchors_of(left["name"]) or {}
        right_anchors = anchors_of(right["name"]) or {}
        exit_anchor = left_anchors.get("exit")
        entry_anchor = right_anchors.get("entry")
        if exit_anchor is None or entry_anchor is None:
            continue
        exit_point = (origins[index][0] + exit_anchor[0], origins[index][1] + exit_anchor[1])
        entry_point = (origins[index + 1][0] + entry_anchor[0], origins[index + 1][1] + entry_anchor[1])
        if exit_point[1] == entry_point[1] and exit_point[0] != entry_point[0]:
            divergences.append(
                Divergence(
                    text,
                    config,
                    index,
                    f"gap 0 at seam (exit {exit_point})",
                    f"entry {entry_point} ({left['name']} -> {right['name']})",
                    "gap",
                )
            )
            return


def anchors_in_font_units(glyphs_by_name: Mapping[str, GlyphRecord]) -> Callable[[str], dict | None]:
    pixel = geometry.PIXEL
    offset = geometry.INK_X_OFFSET

    def lookup(glyph_name: str) -> dict | None:
        record = glyphs_by_name.get(glyph_name)
        if record is None:
            return None

        def convert(anchor):
            if anchor is None:
                return None
            return ((anchor[0] + offset) * pixel, anchor[1] * pixel)

        return {"entry": convert(record.entry), "exit": convert(record.exit)}

    return lookup


def isolated_overlay_active(spec: ResolvedSpec, features: frozenset[str]) -> bool:
    return any(
        spec.registry.features.get(feature) is not None
        and spec.registry.features[feature].overlay == "isolated"
        for feature in features
    )


def isolated_overlay_names(spec: ResolvedSpec, settled: Iterable) -> list[str]:
    """The expected rendering under an `overlay: isolated` taste set: settlement is unchanged, but every letter cell renders as its rune's raw cmap glyph (the isolated drawing, which carries no curs anchors), so every seam is visually a break — exactly today's ss10."""
    names: list[str] = []
    for item in settled:
        cell = getattr(item, "cell", None)
        if cell is not None and isinstance(cell, CellId) and cell.rune in spec.runes:
            names.append(cell.rune)
        else:
            names.extend(settled_names(spec, [item]))
    return names


def raw_labels(spec: ResolvedSpec, text: str, features: frozenset[str]) -> list[str]:
    """The raw GSUB pipeline replay: formation, marker fold, ZWNJ chokepoint — the labels the settlement lookup sees."""
    by_codepoint = {
        info.codepoint: name for name, info in spec.registry.families.items() if info.codepoint is not None
    }
    boundary_by_codepoint = {token.codepoint: name for name, token in spec.registry.boundary_tokens.items()}
    tokens: list[str] = []
    for ch in text:
        cp = ord(ch)
        if cp in boundary_by_codepoint:
            tokens.append(f"<{boundary_by_codepoint[cp]}>")
        elif cp in by_codepoint:
            tokens.append(by_codepoint[cp])
        else:
            raise ValueError(f"U+{cp:04X} outside the spec alphabet")
    formation = {
        rune.sequence: name for name, rune in spec.runes.items() if rune.sequence and len(rune.sequence) == 2
    }
    formed: list[str] = []
    index = 0
    while index < len(tokens):
        pair = (tokens[index], tokens[index + 1]) if index + 1 < len(tokens) else None
        if pair in formation:
            formed.append(formation[pair])
            index += 2
        else:
            formed.append(tokens[index])
            index += 1
    labels: list[str] = []
    for position, token in enumerate(formed):
        if token == "<space>":
            labels.append("space")
        elif token == "<zwnj>":
            labels.append("uni200C")
        elif token == "<namer-dot>":
            labels.append("periodcentered")
        else:
            rune = spec.runes.get(token)
            label = token
            if rune is not None:
                relevant = frozenset(relevant_marker_features(rune)) & features
                label = marker_glyph_name(token, relevant)
            if (
                position > 0
                and formed[position - 1] == "<zwnj>"
                and rune is not None
                and any(stance.surface.entries for stance in rune.stances.values())
            ):
                label = f"{label}.noentry"
            labels.append(label)
    return labels


def run_boundary_equivalence(
    font_path: Path,
    spec: ResolvedSpec,
    configs: Iterable[str] = ACCEPTANCE_CONFIGS,
    max_length: int = 5,
    out_dir: Path | None = None,
) -> ConformReport:
    """The boundary-equals-text-edge vetting gate: every length-1..max_length sequence containing at least one run-splitting boundary (space or ZWNJ) must shape slot-for-slot identically (outline, advance, offsets) to its boundary-split segments shaped alone (check_split_buffer), and every ZWNJ slot must be zero-advance and inkless (check_zwnj_structure). Font-internal only — no settlement, no oracle, no ledger — so it runs on every build and any divergence is a defect by definition. This is the permanent, exhaustive form of the rule the transitional boundary-echo ledger class rides on. The namer dot is deliberately outside the invariant: it does not split runs and stays addressable as `is: namer-dot`."""
    shaper = Shaper(Path(font_path))
    alphabet = spec_alphabet(spec)
    splitters = splitting_boundary_chars(spec)
    report = ConformReport(font=str(font_path))
    features_by_config = {config: features_for_config(config) for config in configs}
    for length in range(1, max_length + 1):
        for combo in itertools.product(alphabet, repeat=length):
            text = "".join(combo)
            if not (set(text) & splitters):
                continue
            report.sequences += 1
            for config, features in features_by_config.items():
                shaped = shaper.shape(text, features)
                report.shaping_runs += 1
                check_zwnj_structure(text, config, shaper, shaped, report.divergences)
                check_split_buffer(text, config, features, shaper, shaped, report.divergences, splitters)
    if out_dir is not None:
        report.write(Path(out_dir) / "boundary_equivalence_summary.json")
    return report


def run_conformance(
    font_path: Path,
    spec: ResolvedSpec,
    configs: Iterable[str] = ACCEPTANCE_CONFIGS,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    max_length: int = 5,
    out_dir: Path | None = None,
) -> ConformReport:
    from rebuild.pipeline import settle as settle_module
    from rebuild.pipeline import table as table_module

    shaper = Shaper(Path(font_path))
    alphabet = spec_alphabet(spec)
    glyph_names = {cell: record.name for cell, record in (glyphs or {}).items()}
    glyphs_by_name = {record.name: record for record in (glyphs or {}).values()}
    anchors_of = anchors_in_font_units(glyphs_by_name) if glyphs else None

    report = ConformReport(font=str(font_path))
    modes: set[str] = set()

    tables = {}
    rules_hit: dict[str, set[int]] = {}
    for config in configs:
        features = features_for_config(config)
        tables[config] = table_module.build_tables(spec, features)
        rules_hit[config] = set()

    sequences = [
        "".join(combo)
        for length in range(1, max_length + 1)
        for combo in itertools.product(alphabet, repeat=length)
    ]
    report.sequences = len(sequences)

    splitters = splitting_boundary_chars(spec)
    for text in sequences:
        codepoints = [ord(ch) for ch in text]
        for config in configs:
            features = features_for_config(config)
            shaped = shaper.shape(text, features)
            report.shaping_runs += 1
            check_zwnj_structure(text, config, shaper, shaped, report.divergences)
            if set(text) & splitters:
                check_split_buffer(text, config, features, shaper, shaped, report.divergences, splitters)
            settled = settle_module.settle(spec, codepoints, features)
            expected_cells = settled_names(spec, settled, glyph_names)
            if isolated_overlay_active(spec, features):
                expected = isolated_overlay_names(spec, settled)
            else:
                expected = expected_cells
            check_oracle(text, config, shaped, expected, report.divergences, modes)
            if anchors_of is not None:
                check_join_gaps(text, config, shaper, shaped, anchors_of, report.divergences)
            _record_rule_hits(spec, text, features, expected_cells, tables[config], rules_hit[config])

    uncovered_total = 0
    for config in configs:
        decision = tables[config][0] if isinstance(tables[config], (tuple, list)) else tables[config]
        rules = list(getattr(decision, "rules", ()))
        uncovered = [index for index in range(len(rules)) if index not in rules_hit[config]]
        uncovered_total += len(uncovered)
        if uncovered:
            report.notes.append(f"{config}: {len(uncovered)} settlement rules never exercised by the sweep")
    report.uncovered_rules = uncovered_total
    report.notes.extend(sorted(modes))
    if out_dir is not None:
        report.write(Path(out_dir) / "conform_summary.json")
    return report


def _record_rule_hits(spec, text, features, expected, tables, hit: set[int]) -> None:
    from rebuild.pipeline.emit_gsub import _raw_rename_map, _renamed

    decision = tables[0] if isinstance(tables, (tuple, list)) else tables
    renames = _raw_rename_map(spec, frozenset(features))
    rules = [_renamed(rule, renames) for rule in getattr(decision, "rules", ())]
    rules_by_input: dict[str, list[tuple[int, object]]] = {}
    for index, rule in enumerate(rules):
        rules_by_input.setdefault(rule.input_glyph, []).append((index, rule))
    try:
        labels = raw_labels(spec, text, features)
    except ValueError:
        return
    settled = normalize_expected(list(expected))
    if len(labels) != len(settled):
        return
    boundaries = {"space", "uni200C", "periodcentered"}
    edge = "#EDGE"
    na = "#NA"
    for index, label in enumerate(labels):
        if label in boundaries:
            continue
        if index == 0:
            left = edge
        elif labels[index - 1] in boundaries:
            left = labels[index - 1]
        else:
            left = settled[index - 1]
        right1 = labels[index + 1] if index + 1 < len(labels) else edge
        right2 = (
            na
            if right1 in boundaries or right1 == edge
            else (labels[index + 2] if index + 2 < len(labels) else edge)
        )
        for rule_index, rule in rules_by_input.get(label, ()):
            if rule.backtrack is not None and left not in rule.backtrack:
                continue
            if rule.look1 is not None and right1 not in rule.look1:
                continue
            if rule.look2 is not None and right2 not in rule.look2:
                continue
            hit.add(rule_index)
            break


@dataclass
class DivergentRow:
    config: str
    codepoints: str
    kinds: tuple[str, ...]
    position: int
    baseline_glyphs: tuple[str, ...]
    baseline_seams: tuple[str, ...]
    new_cells: tuple[str, ...]
    new_seams: tuple[str, ...]
    phenomena: tuple[str, ...] = ()


@dataclass
class BaselineReport:
    rows_compared: int = 0
    divergent_rows: int = 0
    positions_compared: int = 0
    positions_excluded: int = (
        0  # rows skipped by the position channel: seam/ligation divergence, or a matched class that legitimately redraws ink
    )
    counts_by_entry: dict[str, int] = field(default_factory=dict)
    unmatched: list[DivergentRow] = field(default_factory=list)
    multi_matched: list[tuple[DivergentRow, tuple[str, ...]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.unmatched and not self.multi_matched


def load_alias_map(path: Path) -> dict[str, CellId | str]:
    """rebuild/m1-aliases.yaml: old compiled glyph name -> CellId fields, or the literal strings "boundary" / "ignore"."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    aliases: dict[str, CellId | str] = {}
    for old_name, value in raw.items():
        if isinstance(value, str):
            aliases[old_name] = value
            continue
        aliases[old_name] = CellId(
            rune=value["rune"],
            stance=value["stance"],
            entry=value.get("entry"),
            exit=value.get("exit"),
            adjustments=tuple(value.get("adjustments", ())),
        )
    return aliases


def _seam_token(spec: ResolvedSpec, seam) -> str:
    if seam is None:
        return "break"
    if isinstance(seam, int):
        return f"y{seam}"
    return f"y{spec.registry.y_of(seam)}"


def classify_divergence(row: DivergentRow) -> str | None:
    """Assign a divergent row to exactly one ledger class from its phenomenon set (computed by `_compare_row` against the alias map). The set is a partition by construction: each row gets the single highest-precedence class, with the precedence documented in rebuild/m1-divergences.yaml. None = unexplained, which fails conformance."""
    phenomena = set(row.phenomena)
    if not phenomena or any(item.startswith("unaliased") for item in phenomena):
        return None
    if any(item.startswith("position") for item in phenomena):
        # Position drift never rides a cell-grain class (the ink-identity claim it would hide is exactly what the position channel tests); position-only rows go through the kern-attribution predicate instead.
        return None
    if {"0020", "200C"} & set(row.codepoints.split(":")):
        # The ratified boundary-equals-word-boundary rule (design section 3.4, extended to space 2026-07-03): the new font renders every segment of a window containing a run-splitting boundary (space or ZWNJ) identically to that segment standing alone — enforced per build by run_boundary_equivalence — so a boundary row can only diverge from the baseline where the old font was itself inconsistent across the boundary, and every segment-internal divergence resurfaces on the segment's own enumerated row. Boundary rows therefore carry no adjudicable information and are absorbed wholesale, ahead of every other cell/seam-grain class.
        return "boundary-echo"
    if "ligation" in phenomena:
        if "E652:E679" in row.codepoints and ("200C" in row.codepoints or "ss03" in row.config):
            return "marker-staging-ligature-formation"
        # The qsDay_qsUtter ligature forms unconditionally in the old font too (bare E653:E67A renders as the ligature in every config), so only the post-marker windows diverge: the old pipeline renames the lead to .noentry / leaks a bare name after a ZWNJ or the namer dot, and never forms the ligature there. Same staging phenomenon as ·Tea·Oy.
        if "E653:E67A" in row.codepoints and ("200C" in row.codepoints or "00B7" in row.codepoints):
            return "marker-staging-ligature-formation"
        return None
    gains = {item for item in phenomena if item.startswith("seam-gain:")}
    if "seam-moved" in phenomena:
        # A pure seam move (no gain, no loss) on a row whose old glyph was a post-ZWNJ .noentry shadow is the word-initial unification choosing a different seam height than the shadow stance drew: the old .noentry shadow joined its follower at one height, but settling the post-ZWNJ letter as word-initial (identical to its post-space form) lands the join elsewhere. Routed only when the sole seam change is the move, so a post-ZWNJ row that also gains or loses a seam still falls through to its own class.
        if "old-noentry" in phenomena and not gains and "seam-loss" not in phenomena:
            return "zwnj-word-initial-seam-moved"
        return None
    if "seam-loss" in phenomena:
        if gains:
            return "regrouping-floor-drift"
        return None
    if gains:
        gain_runes = {item.split(":", 1)[1] for item in gains}
        unentered_it_gain = "seam-gain-unentered:qsIt" in phenomena
        if "old-noentry" in phenomena:
            return "zwnj-follower-exit-restored"
        if "E652:E679" in row.codepoints:
            return "pre-ligature-cleanup-regularized"
        if "ss03" in row.config and (gain_runes & {"qsTea", "qsMay"} or unentered_it_gain):
            return "ss03-chain-join-gains"
        if "qsIt" in gain_runes and not unentered_it_gain:
            return "entered-it-baseline-join-gain"
        if gain_runes <= {"qsPea"}:
            return "pea-chain-regularized"
        return None
    if "+en-ext-1" in phenomena:
        return "halves-entry-extension-restored"
    if "-en-ext-1:same-seam" in phenomena:
        return "same-seam-extension-non-summing"
    if any(item.startswith("+ex-bind-") for item in phenomena) or "-ex-ext-1" in phenomena:
        return "may-exit-withdrawal-generalized"
    if "+locked" in phenomena or "old-noentry" in phenomena:
        return "zwnj-word-initial-unification"
    if "entry-dropped" in phenomena or "exit-dropped" in phenomena:
        return "dangling-anchor-dropped"
    if phenomena & {"entry-added", "exit-added", "entry-moved", "exit-moved", "stance"}:
        return "bare-name-live-join"
    return None


PREDICATES: dict[str, Callable[[DivergentRow], bool]] = {}


def predicate(name: str):
    def register(function):
        PREDICATES[name] = function
        return function

    return register


def _class_predicate(class_id: str) -> Callable[[DivergentRow], bool]:
    def matches(row: DivergentRow) -> bool:
        return classify_divergence(row) == class_id

    return matches


for _class_id in (
    "boundary-echo",
    "marker-staging-ligature-formation",
    "regrouping-floor-drift",
    "zwnj-word-initial-seam-moved",
    "zwnj-follower-exit-restored",
    "pre-ligature-cleanup-regularized",
    "ss03-chain-join-gains",
    "entered-it-baseline-join-gain",
    "pea-chain-regularized",
    "halves-entry-extension-restored",
    "same-seam-extension-non-summing",
    "may-exit-withdrawal-generalized",
    "zwnj-word-initial-unification",
    "dangling-anchor-dropped",
    "bare-name-live-join",
):
    PREDICATES[_class_id.replace("-", "_")] = _class_predicate(_class_id)


@predicate("kern_channel_out_of_scope")
def _kern_channel_out_of_scope(row: DivergentRow) -> bool:
    """Position-only rows whose drifted slot the comparison marked kern-attributable (the old pair carries a nonzero sidecar kern, or the drift sits on a ZWNJ adjacency). Everything else position-shaped stays unmatched and fails — non-kern position drift is chased to ground, never absorbed here."""
    return row.kinds == ("position",) and "position-kern-attributable" in row.phenomena


# The runes this M1 batch added whose joins the old shipped font never wired into the ss10 isolated overlay, so the old font keeps drawing their cursive joins under ss10 while the new model isolates every letter by design.
SS10_UNCOVERED_BY_OLD_FONT = frozenset({"qsDay", "qsNo", "qsUtter", "qsDay_qsUtter"})


@predicate("ss10_isolation_completed")
def _ss10_isolation_completed(row: DivergentRow) -> bool:
    """Under ss10 the new model renders every position bare (the overlay forces the default stance with no seam), so a join the old font still drew there reads as a seam-loss. The old font's ss10 overlay was authored before qsDay/qsNo/qsUtter and never isolates them, so it keeps joining the new letters under ss10; the new font's complete isolation is the intended correction. Matches ss10 rows whose only seam change is losses, each on a seam touching one of those new runes (an existing|existing seam never joins under the old ss10, so it can never reach here). Space and ZWNJ rows are excluded so the boundary-echo blanket keeps the partition exact."""
    if {"0020", "200C"} & set(row.codepoints.split(":")):
        return False
    if row.config != "ss10" or "seam" not in row.kinds:
        return False
    runes = [token.split("/", 1)[0] for token in row.new_cells]
    saw_loss = False
    for index, (old_seam, new_seam) in enumerate(zip(row.baseline_seams, row.new_seams)):
        if old_seam == new_seam:
            continue
        if new_seam != "break":
            return False
        if old_seam in ("break", "lig"):
            continue
        neighbors = {
            runes[index] if index < len(runes) else "?",
            runes[index + 1] if index + 1 < len(runes) else "?",
        }
        if not (neighbors & SS10_UNCOVERED_BY_OLD_FONT):
            return False
        saw_loss = True
    return saw_loss


ZWNJ_CODEPOINT = 0x200C


def _kern_normalized_positions(
    kern: "KernEvaluator | None", row: Row, pixel: int
) -> tuple[tuple[tuple[int, int, int], ...], tuple[bool, ...]]:
    """The baseline row's per-slot position triples with sidecar kerns subtracted from the old advances (the new font emits no kerning), plus a per-slot kern-attribution mask: True where the slot's old advance carried a nonzero sidecar kern or sits on a ZWNJ adjacency. The kern partner of a slot is the next non-ZWNJ glyph: uni200C is default-ignorable, so HarfBuzz's GPOS pair matching skips it and the old font kerns straight across a ZWNJ (verified against the baseline — ·Oy ZWNJ ·Pea carries the ·Oy·Pea kern)."""

    def slot_is_zwnj(index: int) -> bool:
        return row.codepoints[row.clusters[index]] == ZWNJ_CODEPOINT

    expected: list[tuple[int, int, int]] = []
    attributable: list[bool] = []
    for index, (glyph, (x, y, advance)) in enumerate(zip(row.glyphs, row.positions)):
        kern_value = 0
        zwnj_adjacent = False
        if not slot_is_zwnj(index):
            partner = index + 1
            while partner < len(row.glyphs) and slot_is_zwnj(partner):
                zwnj_adjacent = True
                partner += 1
            if kern is not None and partner < len(row.glyphs):
                kern_value = kern.value_for(glyph, row.glyphs[partner]) * pixel
        else:
            zwnj_adjacent = True
        expected.append((x, y, advance - kern_value))
        attributable.append(bool(kern_value) or zwnj_adjacent)
    return tuple(expected), tuple(attributable)


def _position_drift(
    shaper: Shaper, kern: "KernEvaluator | None", features: frozenset[str], row: Row
) -> tuple[tuple[str, ...], bool] | None:
    """Shape the row against the new font and diff drawn positions against the kern-normalized baseline. The comparison is visual, not encoding-level: per-slot glyph origins (pen + x_offset, y_offset) plus the run's total advance, because the two fonts legitimately decompose a seam differently between the left glyph's advance and the right glyph's x_offset while drawing the identical join. Returns (drift descriptions, kern-attributable) or None when every slot and the total match."""
    shaped = shaper.shape(row.text, features)
    if len(shaped) != len(row.glyphs):
        return ((f"slot-count {len(row.glyphs)} (old) vs {len(shaped)} (new)",), False)
    expected, attributable = _kern_normalized_positions(kern, row, geometry.PIXEL)
    drifts: list[str] = []
    kern_attributable = True
    pen_old = 0
    pen_new = 0
    upstream_attributable = False
    for index, ((x, y, advance), glyph) in enumerate(zip(expected, shaped)):
        want = (pen_old + x, y)
        got = (pen_new + glyph["x_offset"], glyph["y_offset"])
        if got != want:
            drifts.append(f"slot {index} ({row.glyphs[index]}): origin want {want}, got {got}")
            kern_attributable = kern_attributable and upstream_attributable
        pen_old += advance
        pen_new += glyph["x_advance"]
        upstream_attributable = upstream_attributable or attributable[index]
    if pen_old != pen_new:
        drifts.append(f"total advance: want {pen_old}, got {pen_new}")
        kern_attributable = kern_attributable and upstream_attributable
    if not drifts:
        return None
    return (tuple(drifts), kern_attributable)


def compare_against_baseline(
    spec: ResolvedSpec,
    subset_tables_dir: Path,
    alias_path: Path,
    ledger_path: Path,
    configs: Iterable[str] = ACCEPTANCE_CONFIGS,
    out_dir: Path | None = None,
    font_path: Path | None = None,
    kern_sidecar_path: Path | None = None,
) -> BaselineReport:
    from rebuild.pipeline import settle as settle_module

    aliases = load_alias_map(alias_path)
    ledger = yaml.safe_load(Path(ledger_path).read_text()) or []
    ink_identical_ids = {entry.get("id") for entry in ledger if entry.get("ink_identical")}
    shaper = Shaper(Path(font_path)) if font_path is not None else None
    kern = KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    report = BaselineReport()
    audit_lines = ["config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew"]

    for config in configs:
        table_path = Path(subset_tables_dir) / f"baseline-{config}.subset.tsv.gz"
        if not table_path.exists():
            report.notes.append(f"{config}: subset table missing at {table_path}")
            continue
        features = features_for_config(config)
        for row in iter_rows(table_path):
            report.rows_compared += 1
            divergent = _compare_row(spec, settle_module, aliases, config, features, row)
            matches = _match_ledger(ledger, divergent) if divergent is not None else []
            if shaper is not None:
                topology_clean = divergent is None or not ({"ligation", "seam"} & set(divergent.kinds))
                class_claims_ink_identity = divergent is None or (
                    len(matches) == 1 and matches[0] in ink_identical_ids
                )
                if topology_clean and class_claims_ink_identity:
                    drift = _position_drift(shaper, kern, features, row)
                    report.positions_compared += 1
                    if drift is not None:
                        drift_notes, kern_attributable = drift
                        phenomena = ("position-kern-attributable",) if kern_attributable else ()
                        prior_ink_match = matches[0] if len(matches) == 1 else None
                        if divergent is None:
                            divergent = DivergentRow(
                                config=config,
                                codepoints=":".join(f"{cp:04X}" for cp in row.codepoints),
                                kinds=("position",),
                                position=-1,
                                baseline_glyphs=tuple(row.glyphs),
                                baseline_seams=tuple(row.seams),
                                new_cells=tuple(glyph for glyph in drift_notes),
                                new_seams=(),
                                phenomena=phenomena + ("position-drift",),
                            )
                        else:
                            divergent = replace(
                                divergent,
                                kinds=divergent.kinds + ("position",),
                                phenomena=divergent.phenomena + phenomena + ("position-drift",),
                            )
                        rematch = _match_ledger(ledger, divergent)
                        # A kern-attributable position residue is out of scope (the kern channel), so it never demotes a cell-grain row that already matched a single ink-identical class — that row's ink-identity claim survives the kern bookkeeping. A non-kern-attributable drift is a genuine ink shift and is allowed to override the prior match (so the position channel can chase it to ground).
                        if not rematch and kern_attributable and prior_ink_match is not None:
                            matches = [prior_ink_match]
                        else:
                            matches = rematch
                else:
                    report.positions_excluded += 1
            if divergent is None:
                continue
            report.divergent_rows += 1
            if len(matches) == 1:
                entry_id = matches[0]
                report.counts_by_entry[entry_id] = report.counts_by_entry.get(entry_id, 0) + 1
            elif not matches:
                report.unmatched.append(divergent)
            else:
                report.multi_matched.append((divergent, tuple(matches)))
            audit_lines.append(
                "\t".join(
                    (
                        config,
                        divergent.codepoints,
                        ",".join(divergent.kinds),
                        (
                            matches[0]
                            if len(matches) == 1
                            else ("UNMATCHED" if not matches else "+".join(matches))
                        ),
                        "|".join(divergent.baseline_glyphs),
                        "|".join(divergent.new_cells),
                    )
                )
            )

    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "divergence-audit.tsv").write_text("\n".join(audit_lines) + "\n")
    return report


def _compare_row(
    spec, settle_module, aliases, config: str, features: frozenset[str], row: Row
) -> DivergentRow | None:
    settled = settle_module.settle(spec, list(row.codepoints), features)
    if isolated_overlay_active(spec, features):
        # The overlay renders the raw cmap glyph at every letter position; in cell terms that is the anchor-free boundary cell of the default stance (the alias map's bare-name denotation), with every seam visually a break.
        settled = [
            (
                Settled(
                    cell=CellId(item.cell.rune, spec.runes[item.cell.rune].default_stance, None, None, ()),
                    seam=None,
                    extension=0,
                )
                if isinstance(getattr(item, "cell", None), CellId) and item.cell.rune in spec.runes
                else item
            )
            for item in settled
        ]
    new_cells: list[str] = []
    new_seams: list[str] = []
    for index, item in enumerate(settled):
        cell = getattr(item, "cell", None)
        new_cells.append(_cell_token(cell, item))
        if index < len(settled) - 1:
            new_seams.append(_seam_token(spec, getattr(item, "seam", None)))
    kinds: list[str] = []
    position = -1
    phenomena: set[str] = set()

    if len(row.glyphs) != len(settled):
        kinds.append("ligation")
        phenomena.add("ligation")
    else:
        for index, (old_name, item) in enumerate(zip(row.glyphs, settled)):
            if old_name in BOUNDARY_GLYPH_NAMES:
                continue
            alias = aliases.get(old_name)
            if alias is None:
                if "unaliased" not in kinds:
                    kinds.append("unaliased")
                    position = index
                phenomena.add(f"unaliased:{old_name}")
                continue
            if isinstance(alias, str):
                continue
            cell = getattr(item, "cell", None)
            if cell == alias or not isinstance(cell, CellId):
                continue
            if "cell" not in kinds:
                kinds.append("cell")
                position = index
            phenomena |= _cell_deltas(alias, cell, row.glyphs, index)
        baseline_seams = tuple(seam for seam in row.seams if seam != "lig")
        if baseline_seams != tuple(new_seams):
            kinds.append("seam")
            for seam_index, (old_seam, new_seam) in enumerate(zip(baseline_seams, new_seams)):
                if old_seam == new_seam:
                    continue
                if old_seam == "break":
                    cell = getattr(settled[seam_index], "cell", None)
                    left = getattr(cell, "rune", "?")
                    phenomena.add(f"seam-gain:{left}")
                    if left == "qsIt" and getattr(cell, "entry", None) is None:
                        phenomena.add("seam-gain-unentered:qsIt")
                elif new_seam == "break":
                    phenomena.add("seam-loss")
                else:
                    phenomena.add("seam-moved")

    if not kinds:
        return None
    return DivergentRow(
        config=config,
        codepoints=":".join(f"{cp:04X}" for cp in row.codepoints),
        kinds=tuple(dict.fromkeys(kinds)),
        position=position,
        baseline_glyphs=tuple(row.glyphs),
        baseline_seams=tuple(row.seams),
        new_cells=tuple(new_cells),
        new_seams=tuple(new_seams),
        phenomena=tuple(sorted(phenomena)),
    )


def _cell_deltas(alias: CellId, cell: CellId, old_glyphs, index: int) -> set[str]:
    """The atomic differences between the cell an old name denotes and the cell settlement chose, as phenomenon tokens for `classify_divergence`."""
    out: set[str] = set()
    if alias.stance != cell.stance:
        out.add("stance")
    if alias.entry != cell.entry:
        out.add(
            "entry-dropped"
            if cell.entry is None
            else ("entry-added" if alias.entry is None else "entry-moved")
        )
    if alias.exit != cell.exit:
        out.add(
            "exit-dropped" if cell.exit is None else ("exit-added" if alias.exit is None else "exit-moved")
        )
    old_tokens, new_tokens = set(alias.adjustments), set(cell.adjustments)
    for token in new_tokens - old_tokens:
        out.add(f"+{token}")
    for token in old_tokens - new_tokens:
        if token == "en-ext-1" and index > 0 and "ex-ext-1" in old_glyphs[index - 1]:
            out.add("-en-ext-1:same-seam")
        else:
            out.add(f"-{token}")
    if ".noentry" in old_glyphs[index]:
        out.add("old-noentry")
    return out


def _cell_token(cell, item) -> str:
    if cell is None:
        return getattr(item, "glyph_name", None) or str(item)
    return f"{cell.rune}/{cell.stance}/{cell.entry}/{cell.exit}/{'+'.join(cell.adjustments)}"


def _match_ledger(ledger: list[dict], row: DivergentRow) -> list[str]:
    matches: list[str] = []
    for entry in ledger:
        match = entry.get("match", {})
        entry_configs = match.get("configs", "all")
        if entry_configs != "all" and row.config not in entry_configs:
            continue
        predicate_name = match.get("predicate")
        if predicate_name is not None:
            function = PREDICATES.get(predicate_name)
            if function is None or not function(row):
                continue
        else:
            window = match.get("window")
            if window is not None and window not in row.codepoints:
                continue
            seam_change = match.get("seam_change")
            if seam_change is not None and "seam" not in row.kinds:
                continue
        matches.append(entry.get("id", "<unnamed>"))
    return matches


class KernEvaluator:
    """Read-only evaluation of glyph_data/senior_quikscript_kerning.yaml over old-name glyph pairs, for adding sidecar kerns back before any baseline position diff. Family keys expand by name prefix against the supplied pair, mirroring the sidecar's documented expansion."""

    def __init__(self, sidecar_path: Path):
        documents = [
            document
            for document in yaml.safe_load_all(Path(sidecar_path).read_text())
            if isinstance(document, dict)
        ]
        self.global_value = 0
        self.rules: list[dict] = []
        for document in documents:
            if "global" in document:
                self.global_value += document["global"].get("value", 0)
            else:
                self.rules.append(document)

    @staticmethod
    def _side_matches(glyph: str, names: list[str] | None, kind: str) -> bool:
        if names is None:
            return True
        for name in names:
            if kind == "exact" and glyph == name:
                return True
            if kind in ("family", "stance") and (glyph == name or glyph.startswith(name + ".")):
                return True
        return False

    def value_for(self, left_glyph: str, right_glyph: str) -> int:
        total = self.global_value
        for rule in self.rules:
            left_ok = (
                self._side_matches(left_glyph, rule.get("left_family"), "family")
                if "left_family" in rule
                else (
                    self._side_matches(left_glyph, rule.get("left_stance"), "stance")
                    if "left_stance" in rule
                    else self._side_matches(left_glyph, rule.get("left"), "exact") if "left" in rule else True
                )
            )
            if not left_ok:
                continue
            for prefix in rule.get("except_left", ()):
                if left_glyph == prefix or left_glyph.startswith(prefix + "."):
                    left_ok = False
            if not left_ok:
                continue
            if "right_group" in rule:
                right_ok = rule["right_group"] == "noentry" and right_glyph.endswith(".noentry")
            elif "right_family" in rule:
                right_ok = self._side_matches(right_glyph, rule["right_family"], "family")
            elif "right_stance" in rule:
                right_ok = self._side_matches(right_glyph, rule["right_stance"], "stance")
            elif "right" in rule:
                right_ok = self._side_matches(right_glyph, rule["right"], "exact")
            else:
                right_ok = True
            for prefix in rule.get("except_right", ()):
                if right_glyph == prefix or right_glyph.startswith(prefix + "."):
                    right_ok = False
            if right_ok:
                total += rule.get("value", 0)
        return total


def assert_subset_identity(subset_dir: Path, config: str, reference: str = "default") -> None:
    """The ss06/ss07/ss06+ss07 gate: the filtered sub-table must be row-identical to the reference configuration's."""
    left = list(iter_rows(Path(subset_dir) / f"baseline-{config}.subset.tsv.gz"))
    right = list(iter_rows(Path(subset_dir) / f"baseline-{reference}.subset.tsv.gz"))
    if left != right:
        first = next(
            (pair for pair in zip(left, right) if pair[0] != pair[1]),
            (None, None),
        )
        raise AssertionError(
            f"subset table {config} is not row-identical to {reference}: first differing pair {first}"
        )
