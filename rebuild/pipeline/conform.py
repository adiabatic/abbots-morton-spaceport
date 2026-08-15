"""Conformance gates (M1-PLAN sections 5 and 6, Group 3): HarfBuzz vs the settlement function, and the settlement function vs the section 13.1 baseline oracle.

`run_conformance` promotes prototype/conform.py: the Shaper (MONOTONE_CHARACTERS cluster level; names via TTFont, never HarfBuzz's truncating API), the exhaustive length-1..5 enumeration per acceptance configuration, the ZWNJ structural checks (zero advance, no ink), split-buffer equivalence, gap-0 pen positions, and rule coverage (every emitted settlement rule fired). Coverage past the enumeration horizon is completed by generated witnesses: a settlement rule or decision-table transition the exhaustive sweep never exercises gets a shortest realizing string BFS-derived from the decision table's own windows (`_shortest_window_prefixes`), and that string is shaped and diffed as a top-up like any swept sequence — a rule only counts covered once it actually fires against the settled stream, so the table is a guide, never evidence. `uncovered_rules` / `uncovered_transitions` therefore count only rules and transitions with no verifiable witness at any length, which is dead code in the emitted FEA (a generator defect), not a horizon artifact. Where the table enumerates deep slots at class grain (issue 26), the coverage floor is per class row — realized labels resolve to the table's tokens through `_DeepTokenIndex`, one realized member realizes its row, and the summary carries a per-config note stating how many class rows stand for how many label windows — a declared narrowing, sound because the build proves members indiscernible (partition, echo, and fiber verification) and every emitted look class holds a token's members all-in or all-out; the exhaustive horizon sweep stays label-grain over real texts. The worked example that forced this design: `sub qsNo.loop qsMay' qsMay qsMay` needs six tokens (·Day·Tea·No·May·May·May — ·Day takes ·Tea's baseline entry, the baseline-entered ·Tea is exitless, so ·No stays bare before ·May), one past the five-token sweep. The font-vs-settle diff takes no ledger: any divergence is a compiler defect by definition. The top-ups spend nothing twice: candidate texts are deduplicated against everything already swept, each config's winning witnesses persist beside the enumeration (`read_witnesses` / `write_witnesses`, keyed by `table.windows_digest` so an ink-only rune edit keeps them warm) and are re-verified rather than trusted on the next run, and the sweep inherits the ZWNJ/split-buffer checks within the horizon the boundary gate already proved for the same font bytes (`proven_boundary_horizon`).

`compare_against_baseline` is the section 6 oracle gate: stream the filtered sub-tables, run `settle` per row, compare ligation (clusters), per-seam classification, and cell identity through the hand-written alias map; every divergent row must match exactly one ledger entry (zero matches fails conformance, two-plus fails the ledger). When a font path is supplied, the gate also compares positions old-vs-new (M1-PLAN section 6 step 3d): each row is shaped against the new font and its per-slot (x_offset, y_offset, x_advance) triples are compared against the baseline's, with sidecar kerns normalized out of the old advances via `KernEvaluator` (the new font emits no kerning). Position equality is enforced on every row whose seam topology and ligation match the baseline and whose cell-grain divergence class (if any) claims ink identity (`ink_identical: true` in the ledger); rows whose matched class legitimately redraws ink (extensions restored or suppressed, withdrawal bindings) are excluded and counted, because their advances move with the ink by design.

Group 2's `settle` and `table` modules are imported lazily inside the entry points, so this module and its helpers load without them.
"""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Mapping

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
    ss10_twin_name,
)
from rebuild.validation.rowmodel import CONFIGS, Row, iter_rows

if TYPE_CHECKING:
    from rebuild.pipeline.emit_gsub import _FoldedRule
    from rebuild.pipeline.settle import Engine, RightToken
    from rebuild.pipeline.table import Rule, Window

ZWNJ = "\u200c"
ZWNJ_SENTINEL = "<zwnj>"
BOUNDARY_GLYPH_NAMES = {"space", "uni200C", "periodcentered", "periodcentered.lowered"}
ACCEPTANCE_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10")


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
    topped_up_rules: int = 0
    topped_up_sequences: int = 0
    notes: list[str] = field(default_factory=list)
    # Set only by the boundary gate, whose green other gates lean on: the horizon and configs it proved, pinned to the exact font bytes it shaped. The conformance report leaves them None and its summary unchanged.
    max_length: int | None = None
    configs: tuple[str, ...] | None = None
    font_sha256: str | None = None

    @property
    def passed(self) -> bool:
        return not self.divergences and self.uncovered_rules == 0 and self.uncovered_transitions == 0

    def write(self, path: Path) -> None:
        by_kind: dict[str, int] = {}
        for divergence in self.divergences:
            by_kind[divergence.kind] = by_kind.get(divergence.kind, 0) + 1
        summary: dict[str, object] = {
            "font": self.font,
            "sequences": self.sequences,
            "shaping_runs": self.shaping_runs,
            "divergences": len(self.divergences),
            "divergences_by_kind": by_kind,
            "uncovered_rules": self.uncovered_rules,
            "uncovered_transitions": self.uncovered_transitions,
            "topped_up_rules": self.topped_up_rules,
            "topped_up_sequences": self.topped_up_sequences,
            "pass": self.passed,
            "notes": self.notes,
        }
        if self.max_length is not None:
            summary["max_length"] = self.max_length
        if self.configs is not None:
            summary["configs"] = list(self.configs)
        if self.font_sha256 is not None:
            summary["font_sha256"] = self.font_sha256
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2) + "\n")


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
    """The expected rendering under an `overlay: isolated` taste set: settlement is unchanged, but every letter cell renders as its rune's anchor-free `.ss10` twin (drawn identically to the raw cmap glyph), so every seam is visually a break. A ligature-rune cell expands to its components' twins in sequence order — the 2026-07-04 ratification that join suppression also means ligation suppression, realized by the pre-empt lookup substituting the twins before formation, so no ligature ever forms and each letter keeps its own cluster."""
    names: list[str] = []
    for item in settled:
        cell = getattr(item, "cell", None)
        if cell is not None and isinstance(cell, CellId) and cell.rune in spec.runes:
            names.extend(ss10_twin_name(name) for name in spec.runes[cell.rune].sequence or (cell.rune,))
        else:
            names.extend(settled_names(spec, [item]))
    return names


_BOUNDARY_KIND_LABELS = {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}


def raw_labels(spec: ResolvedSpec, text: str, features: frozenset[str]) -> list[str]:
    """The raw GSUB pipeline replay: formation (delegated to settle.form_ligatures, so the section 5.7 late-formation guard applies here exactly as in the kernel and the emitted lookup), marker fold, ZWNJ chokepoint — the labels the settlement lookup sees."""
    from rebuild.pipeline import settle as settle_module

    by_codepoint = {
        info.codepoint: name for name, info in spec.registry.families.items() if info.codepoint is not None
    }
    boundary_by_codepoint = {token.codepoint: name for name, token in spec.registry.boundary_tokens.items()}
    tokens: list[RightToken] = []
    for ch in text:
        cp = ord(ch)
        if cp in boundary_by_codepoint:
            tokens.append(settle_module.RightToken(boundary_by_codepoint[cp]))
        elif cp in by_codepoint:
            tokens.append(settle_module.RightToken("letter", by_codepoint[cp]))
        else:
            raise ValueError(f"U+{cp:04X} outside the spec alphabet")
    return _formed_labels(spec, settle_module.form_ligatures(spec, tokens), features)


def _formed_labels(spec: ResolvedSpec, formed: list[RightToken], features: frozenset[str]) -> list[str]:
    """The post-formation stream's labels in the config's renamed space: marker fold, then the ZWNJ chokepoint's `.noentry` suffix on entry-bearing letters. Interned, so the window keys built from millions of texts share one string object per label instead of holding a fresh fold per text alive."""
    labels: list[str] = []
    for position, token in enumerate(formed):
        if token.kind != "letter":
            labels.append(_BOUNDARY_KIND_LABELS[token.kind])
            continue
        name = token.letter
        rune = spec.runes.get(name)
        label = name
        if rune is not None:
            relevant = frozenset(relevant_marker_features(rune)) & features
            label = marker_glyph_name(name, relevant)
        if (
            position > 0
            and formed[position - 1].kind == "zwnj"
            and rune is not None
            and any(stance.surface.entries for stance in rune.stances.values())
        ):
            label = f"{label}.noentry"
        labels.append(sys.intern(label))
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
    configs = tuple(configs)
    report = ConformReport(
        font=str(font_path),
        max_length=max_length,
        configs=configs,
        font_sha256=_font_sha256(Path(font_path)),
    )
    features_by_config = {config: features_for_config(config) for config in configs}
    secs_by_config = {config: 0.0 for config in features_by_config}
    runs_by_config = {config: 0 for config in features_by_config}
    started = time.perf_counter()
    for length in range(1, max_length + 1):
        for combo in itertools.product(alphabet, repeat=length):
            text = "".join(combo)
            if not (set(text) & splitters):
                continue
            report.sequences += 1
            for config, features in features_by_config.items():
                config_started = time.perf_counter()
                shaped = shaper.shape(text, features)
                report.shaping_runs += 1
                runs_by_config[config] += 1
                check_zwnj_structure(text, config, shaper, shaped, report.divergences)
                check_split_buffer(text, config, features, shaper, shaped, report.divergences, splitters)
                secs_by_config[config] += time.perf_counter() - config_started
    for config in features_by_config:
        print(
            f"[t] boundary {config} {secs_by_config[config]:.2f}s shaping_runs={runs_by_config[config]}",
            file=sys.stderr,
            flush=True,
        )
    print(
        f"[t] boundary total {time.perf_counter() - started:.2f}s sequences={report.sequences} shaping_runs={report.shaping_runs}",
        file=sys.stderr,
        flush=True,
    )
    if out_dir is not None:
        report.write(Path(out_dir) / "boundary_equivalence_summary.json")
    return report


@dataclass
class BoundaryConfigResult:
    config: str
    sequences: int = 0
    shaping_runs: int = 0
    divergences: list[Divergence] = field(default_factory=list)


def _boundary_config(
    shaper: Shaper,
    config: str,
    features: frozenset[str],
    alphabet: tuple[str, ...],
    splitters: frozenset[str],
    max_length: int,
) -> BoundaryConfigResult:
    result = BoundaryConfigResult(config=config)
    for length in range(1, max_length + 1):
        for combo in itertools.product(alphabet, repeat=length):
            text = "".join(combo)
            if not (set(text) & splitters):
                continue
            result.sequences += 1
            shaped = shaper.shape(text, features)
            result.shaping_runs += 1
            check_zwnj_structure(text, config, shaper, shaped, result.divergences)
            check_split_buffer(text, config, features, shaper, shaped, result.divergences, splitters)
    return result


def boundary_config_worker(
    spec: ResolvedSpec, font_path: Path, config: str, max_length: int = 5
) -> BoundaryConfigResult:
    shaper = Shaper(Path(font_path))
    alphabet = spec_alphabet(spec)
    splitters = splitting_boundary_chars(spec)
    features = features_for_config(config)
    return _boundary_config(shaper, config, features, alphabet, splitters, max_length)


def merge_boundary_results(
    font_path: Path, results: Iterable[BoundaryConfigResult], max_length: int | None = None
) -> ConformReport:
    report = ConformReport(font=str(font_path), max_length=max_length)
    results = list(results)
    report.sequences = results[0].sequences if results else 0
    report.configs = tuple(result.config for result in results)
    if Path(font_path).is_file():
        report.font_sha256 = _font_sha256(Path(font_path))
    for result in results:
        report.shaping_runs += result.shaping_runs
        report.divergences.extend(result.divergences)
    return report


def _font_sha256(font_path: Path) -> str:
    return hashlib.sha256(Path(font_path).read_bytes()).hexdigest()


def proven_boundary_horizon(
    font_path: Path, summary_path: Path, configs: Iterable[str] = ACCEPTANCE_CONFIGS
) -> int | None:
    """The exhaustive horizon the boundary gate proved for exactly the font bytes at `font_path`, or None when its summary is missing, red, from another font, or short a requested config. Within this horizon `run_boundary_equivalence` already ran `check_zwnj_structure` and `check_split_buffer` over every sequence containing a run-splitting boundary — and a splitter-free text trivially satisfies both — so a conformance sweep may inherit that green instead of re-proving it text by text. Everything the inheritance leans on is checked here: `pass`, the recorded horizon and configs, and the font hash, so a stale summary (the usual case being a font rebuilt since the boundary gate last ran) simply declines rather than skipping unproven checks."""
    try:
        summary = json.loads(Path(summary_path).read_text())
        font_hash = _font_sha256(Path(font_path))
    except OSError, ValueError:
        return None
    horizon = summary.get("max_length")
    recorded_configs = summary.get("configs")
    if (
        not summary.get("pass")
        or not isinstance(horizon, int)
        or not isinstance(recorded_configs, list)
        or not set(configs) <= set(recorded_configs)
        or summary.get("font_sha256") != font_hash
    ):
        return None
    return horizon


_WINDOW_BOUNDARIES = frozenset({"space", "uni200C", "periodcentered"})
_EDGE_LABEL = "#EDGE"
_NA_LABEL = "#NA"


def _window_rights(
    labels: list[str],
    index: int,
    deep: frozenset[str],
    deep3_live: Callable[[str, str, str], bool],
    deep4: frozenset[str],
    deep4_live: Callable[[str, str, str, str], bool],
) -> tuple[str, str, str, str]:
    """The normalized lookahead slots of the settlement window at `index`: right2 is #NA past a boundary or the edge, and the deep slots open only where the table enumerates them — an input `deep`/`deep4` admits (the table's `third_slot_inputs` / `fourth_slot_inputs`) whose window is still live over the nearer slots (`deep3_live` / `deep4_live`, the table's `third_slot_filter` / `fourth_slot_filter`, whose verdicts carry the simulated-prospect arm whenever the engines do). Shared by `_matched_windows` and `_SettledWindowWalk` so the replay, the memo key, and the table agree on which slots a window carries."""
    label = labels[index]
    right1 = labels[index + 1] if index + 1 < len(labels) else _EDGE_LABEL
    right2 = (
        _NA_LABEL
        if right1 in _WINDOW_BOUNDARIES or right1 == _EDGE_LABEL
        else (labels[index + 2] if index + 2 < len(labels) else _EDGE_LABEL)
    )
    right3 = (
        _NA_LABEL
        if right2 in _WINDOW_BOUNDARIES
        or right2 in (_EDGE_LABEL, _NA_LABEL)
        or _label_family(label) not in deep
        or not deep3_live(_label_family(label), _label_family(right1), _label_family(right2))
        else (labels[index + 3] if index + 3 < len(labels) else _EDGE_LABEL)
    )
    right4 = (
        _NA_LABEL
        if right3 in _WINDOW_BOUNDARIES
        or right3 in (_EDGE_LABEL, _NA_LABEL)
        or _label_family(label) not in deep4
        or not deep4_live(
            _label_family(label), _label_family(right1), _label_family(right2), _label_family(right3)
        )
        else (labels[index + 4] if index + 4 < len(labels) else _EDGE_LABEL)
    )
    return right1, right2, right3, right4


def _first_matching_rule(
    rules_by_input: Mapping[str, list[tuple[int, Rule | _FoldedRule]]],
    label: str,
    left: str,
    right1: str,
    right2: str,
    right3: str,
    right4: str,
    representatives: Mapping[str, str] | None = None,
) -> int | None:
    """First-match-wins over the config's renamed rules for one window — the exact semantics the emitted FEA compiles to. A deep slot holding a class token is tested through its renamed representative member (`representatives`, from the table's `_DeepTokenIndex`): exact, not heuristic, because the build asserts every emitted look class holds a token's members all-in or all-out."""
    if representatives:
        right3 = representatives.get(right3, right3)
        right4 = representatives.get(right4, right4)
    for rule_index, rule in rules_by_input.get(label, ()):
        if rule.backtrack is not None and left not in rule.backtrack:
            continue
        if rule.look1 is not None and right1 not in rule.look1:
            continue
        if rule.look2 is not None and right2 not in rule.look2:
            continue
        look3 = getattr(rule, "look3", None)
        if look3 is not None and right3 not in look3:
            continue
        look4 = getattr(rule, "look4", None)
        if look4 is not None and right4 not in look4:
            continue
        return rule_index
    return None


def _matched_windows(
    spec,
    text,
    features,
    expected,
    rules_by_input,
    deep=None,
    deep3_live=None,
    deep4=None,
    deep4_live=None,
    deep_index=None,
):
    """Replay the settlement lookup's view of one string: yield (position, window key, first-matching rule index or None) per letter slot, with labels and rules in the config's renamed (marker-folded) space and the left slot read from the settled stream — the exact first-match-wins semantics the emitted FEA compiles to. `deep` is the table's `third_slot_inputs` set and `deep4` its `fourth_slot_inputs` set (computed here when not supplied); the third window slot is #NA except where the table enumerates it — an admitted input with letters at both nearer slots, gated by `deep3_live` (the table's `third_slot_filter`, built here when not supplied) — and the fourth repeats that one deeper, gated the same way by `deep4_live` (the table's `fourth_slot_filter`), so the replay and the table agree on which windows carry live deep slots. `deep_index` is the table's `_DeepTokenIndex`; token resolution is a separate step strictly after `_window_rights`' gates, which must see raw labels, and needs the settled left this loop holds — with no index the deep slots stay raw labels, which on a class-grain table realize no row."""
    from rebuild.pipeline import table as table_module

    if deep is None:
        deep = table_module.third_slot_inputs(spec)
    if deep3_live is None:
        deep3_live = table_module.third_slot_filter(spec, frozenset(features))
    if deep4 is None:
        deep4 = table_module.fourth_slot_inputs(spec)
    if deep4_live is None:
        deep4_live = table_module.fourth_slot_filter(spec, frozenset(features))
    try:
        labels = raw_labels(spec, text, features)
    except ValueError:
        return
    settled = normalize_expected(list(expected))
    if len(labels) != len(settled):
        return
    for index, label in enumerate(labels):
        if label in _WINDOW_BOUNDARIES:
            continue
        if index == 0:
            left = _EDGE_LABEL
        elif labels[index - 1] in _WINDOW_BOUNDARIES:
            left = labels[index - 1]
        else:
            left = settled[index - 1]
        right1, right2, right3, right4 = _window_rights(labels, index, deep, deep3_live, deep4, deep4_live)
        if deep_index is not None:
            right3, right4 = deep_index.resolve(label, left, right1, right2, right3, right4)
        matched = _first_matching_rule(
            rules_by_input,
            label,
            left,
            right1,
            right2,
            right3,
            right4,
            representatives=deep_index.representatives if deep_index is not None else None,
        )
        yield index, (label, left, right1, right2, right3, right4), matched


def _renamed_rules_by_input(spec, features, decision) -> dict[str, list[tuple[int, Rule | _FoldedRule]]]:
    from rebuild.pipeline.emit_gsub import _raw_rename_map, _renamed

    renames = _raw_rename_map(spec, frozenset(features))
    rules_by_input: dict[str, list[tuple[int, Rule | _FoldedRule]]] = {}
    for index, rule in enumerate(getattr(decision, "rules", ())):
        renamed = _renamed(rule, renames)
        rules_by_input.setdefault(renamed.input_glyph, []).append((index, renamed))
    return rules_by_input


def _label_family(label: str) -> str:
    return label.split(".")[0]


def _token_members(decision, label: str) -> tuple[str, ...]:
    """The member labels a table's deep-slot field stands for — the class map's entry for a class id, else the label itself (raw label space; renaming is the index's job)."""
    deep = getattr(decision, "deep_classes", None)
    if deep:
        members = deep.get(label)
        if members:
            return members
    return (label,)


def _token_representative(decision, label: str) -> str:
    return _token_members(decision, label)[0]


class _DeepTokenIndex:
    """The per-config transport of a table's deep-slot class tokens into the walk and the replay (issue 26). Two levels, because r4 fibers are per (base, r3 token), never per base alone: `{(renamed input, settled left, renamed r1, renamed r2) -> {renamed member label -> r3 token}}` and the same keyed one deeper on the resolved r3 token — the class id verbatim when the row's r3 is a class, otherwise the bare r3 in renamed space, because that is exactly what `resolve`'s r3 step hands back for each shape (a class token never renames; a bare label reaches `resolve` already marker-folded). Built once per config from `decision.transitions` + `decision.deep_classes` + the rename map; `resolve` runs in the callers that hold the settled left, strictly after `_window_rights`' gates — those gates index `spec.runes` by `_label_family`, so a class id must never reach them. A boundary label passes through, and a live-but-unindexed member falls back to the raw label, which then matches no row — today's exact behavior for a window the table lacks, which the enumeration's exactness precludes. `representatives` maps each class token to its renamed first member for the rule-membership tests, exact rather than heuristic because the build asserts every emitted look class holds a token's members all-in or all-out."""

    def __init__(self, decision, renames: Mapping[str, str]):
        self.representatives: dict[str, str] = {}
        self._by_base: dict[tuple[str, str, str, str], dict[str, str]] = {}
        self._by_base_r3: dict[tuple[tuple[str, str, str, str], str], dict[str, str]] = {}
        deep = getattr(decision, "deep_classes", None) or {}
        for token, members in deep.items():
            self.representatives[token] = renames.get(members[0], members[0])
        for row in decision.transitions:
            members3 = deep.get(row.right3)
            members4 = deep.get(row.right4)
            if members3 is None and members4 is None:
                continue
            base = (
                renames.get(row.input_glyph, row.input_glyph),
                row.left,
                renames.get(row.right1, row.right1),
                renames.get(row.right2, row.right2),
            )
            if members3 is not None:
                bucket = self._by_base.setdefault(base, {})
                for member in members3:
                    bucket[renames.get(member, member)] = row.right3
            if members4 is not None:
                token3 = row.right3 if members3 is not None else renames.get(row.right3, row.right3)
                bucket4 = self._by_base_r3.setdefault((base, token3), {})
                for member in members4:
                    bucket4[renames.get(member, member)] = row.right4

    def resolve(
        self, label: str, left: str, right1: str, right2: str, right3: str, right4: str
    ) -> tuple[str, str]:
        base = (label, left, right1, right2)
        bucket = self._by_base.get(base)
        token3 = bucket.get(right3, right3) if bucket is not None else right3
        bucket4 = self._by_base_r3.get((base, token3))
        token4 = bucket4.get(right4, right4) if bucket4 is not None else right4
        return token3, token4


class _SettledWindowWalk:
    """The memoized settle-and-replay walk one conformance config runs over every swept text: a single left-to-right pass computes each letter slot's normalized window key — exactly `_matched_windows`' slots, with the left read from the just-settled stream — and resolves it through `windows`, a window -> (Settled, glyph name, first-matching rule index) memo, calling `engine.transition_trace` only on a miss; the memoized Settled feeds the next slot's left exactly as `settle_traces` does. Sound because every memoized outcome is a pure function of the normalized window: the left label is the settled cell's glyph name (injective over every CellId field, whether minted by `cell_label` or `geometry.display_name`); the deep slots blank only where the table's own relevance filters prove nothing can read them — no own-rune chain, and no simulated follower choice when the engines carry the issue-28 prospect — the same rules the build's partition gates assert; and where the table enumerated at class grain, the realized deep labels collapse only within the table's own fibers (`_DeepTokenIndex`), probed per member on the same engine at build time. On a miss the kernel still receives the raw tokens of the actual text, so a mis-drawn class surfaces as a settled-stream divergence on the first text that reaches the window with a member whose behavior diverges — the same first-reached-representative alarm shape as the filter-based blanking, with the build's partition, echo, and fiber-verification checks standing in front of it; a live member the index cannot resolve falls back to its raw label and matches no row, today's exact behavior for a window the table lacks. A record shape that read a token through a normalized-away slot (none exists today — no `then` hop follows an `is:` boundary condition) would settle wrongly only via each window's first-reached representative rather than diverging on every text, so the walk-equivalence sweeps in rebuild/test_conform.py are the alarm that must move first. A hit skips `transition_trace` and so stops re-recording into `engine.fired`, which the conform run never reads. `windows` doubles as the sweep's realized-window record — its keys are precisely the distinct windows the walk has settled — so it is deliberately unbounded: coverage bookkeeping needs every key anyway, and the interned labels plus deduplicated outcome tuples keep the residual cost to the key tuples themselves."""

    def __init__(
        self,
        spec: ResolvedSpec,
        engine: Engine,
        features: frozenset[str],
        rules_by_input: Mapping[str, list[tuple[int, Rule | _FoldedRule]]],
        deep: frozenset[str],
        deep3_live: Callable[[str, str, str], bool],
        deep4: frozenset[str],
        deep4_live: Callable[[str, str, str, str], bool],
        glyph_names: Mapping[CellId, str],
        deep_index: _DeepTokenIndex | None = None,
    ):
        self.spec = spec
        self.engine = engine
        self.features = features
        self.rules_by_input = rules_by_input
        self.deep = deep
        self.deep3_live = deep3_live
        self.deep4 = deep4
        self.deep4_live = deep4_live
        self.glyph_names = glyph_names
        self.deep_index = deep_index
        self.windows: dict[tuple[str, str, str, str, str, str], tuple[Settled, str, int | None]] = {}
        self._outcomes: dict[tuple[Settled, str, int | None], tuple[Settled, str, int | None]] = {}

    def walk(self, text: str) -> tuple[list[Settled], list[str], list[int | None]]:
        """Settle one text through the memo. Returns (settled items, their glyph names, the first-matching rule index per slot — None on boundary slots and unmatched windows)."""
        from rebuild.pipeline import settle as settle_module

        spec = self.spec
        tokens = settle_module.form_ligatures(
            spec, settle_module.tokens_from_codepoints(spec, [ord(ch) for ch in text])
        )
        labels = _formed_labels(spec, tokens, self.features)
        settled: list[Settled] = []
        names: list[str] = []
        matched_rules: list[int | None] = []
        left_context = settle_module.LeftContext("edge")
        for index, token in enumerate(tokens):
            if token.kind != "letter":
                settled.append(settle_module.boundary_settled(token.kind))
                names.append(_BOUNDARY_KIND_LABELS[token.kind])
                matched_rules.append(None)
                left_context = settle_module.LeftContext(token.kind)
                continue
            label = labels[index]
            if index == 0:
                left = _EDGE_LABEL
            elif labels[index - 1] in _WINDOW_BOUNDARIES:
                left = labels[index - 1]
            else:
                left = names[index - 1]
            right1, right2, right3, right4 = _window_rights(
                labels, index, self.deep, self.deep3_live, self.deep4, self.deep4_live
            )
            if self.deep_index is not None:
                right3, right4 = self.deep_index.resolve(label, left, right1, right2, right3, right4)
            window = (label, left, right1, right2, right3, right4)
            outcome = self.windows.get(window)
            if outcome is None:
                item = self.engine.transition_trace(
                    left_context,
                    token,
                    tokens[index + 1] if index + 1 < len(tokens) else settle_module.EDGE,
                    tokens[index + 2] if index + 2 < len(tokens) else settle_module.EDGE,
                    tokens[index + 3] if index + 3 < len(tokens) else settle_module.EDGE,
                    tokens[index + 4] if index + 4 < len(tokens) else settle_module.EDGE,
                ).settled
                name = self.glyph_names.get(item.cell) or geometry.display_name(spec, item.cell)
                matched = _first_matching_rule(
                    self.rules_by_input,
                    label,
                    left,
                    right1,
                    right2,
                    right3,
                    right4,
                    representatives=self.deep_index.representatives if self.deep_index is not None else None,
                )
                fresh = (item, name, matched)
                outcome = self._outcomes.setdefault(fresh, fresh)
                self.windows[window] = outcome
            item, name, matched = outcome
            settled.append(item)
            names.append(name)
            matched_rules.append(matched)
            left_context = settle_module.LeftContext("letter", item)
        return settled, names, matched_rules


def _token_text(spec: ResolvedSpec, tokens: Iterable[str]) -> str:
    """Render a witness token stream (rune family, ligature-rune, or boundary-label tokens) back to codepoints; ligature runes expand to their component sequence, so raw_labels' greedy formation re-folds them to the intended labels."""
    boundary_codepoints = {
        {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}[name]: token.codepoint
        for name, token in spec.registry.boundary_tokens.items()
    }
    chars: list[str] = []
    for token in tokens:
        if token in boundary_codepoints:
            chars.append(chr(boundary_codepoints[token]))
            continue
        rune = spec.runes[token]
        for part in rune.sequence or (token,):
            codepoint = spec.runes[part].codepoint
            if codepoint is None:
                raise ValueError(
                    f"ligature rune {token} names {part}, which carries no codepoint — this expansion is one level deep only"
                )
            chars.append(chr(codepoint))
    return "".join(chars)


def _shortest_window_prefixes(decision):
    """BFS the decision table's own windows, mirroring the table builder's worklist at label grain: for each (left label, input label, constrained-right1 label or None-for-boundary-seeds) item, the shortest token prefix that realizes it. The prefix holds the tokens BEFORE the input slot — empty for edge-left items, the boundary token for boundary-left items.

    Returns `(prefixes, by_right3)`. `prefixes` is the shortest prefix per window, used to grow longer chains. `by_right3` maps each window to `{(producing-row-right3, producing-row-right4): shortest-prefix}` — the deep lookaheads the realizing row was pinned to (mirroring the table builder's right3/right4 exactness in `table._flag_prospect_joints`). A backtrack whose withdrawal is decided by a raw deep slot (a depth-3- or depth-4-conditional cell) is realized only under specific (right3, right4) values, so a witness for it must pick the prefix whose deep slots match the target window's own look2/look3; the flat `prefixes` map can only offer the shortest, which may carry deep slots the target rule never takes. Deep pins return to label grain at this bucket boundary: a class-grain producer row registers its prefix under every expanded (member3, member4) pair — the producer identity the buckets ride is that a row's right3 raw label is its successor window's right2 one slot over, and class tokens minted in different contexts must never meet across it — so `by_right3` buckets stay label-grain exactly as at label grain, bounded by the old label-grain row count.
    """
    from collections import deque

    from rebuild.pipeline.table import BOUNDARY_LEFT_LABELS, BOUNDARYISH

    boundary_prefixes: dict[str, tuple[str, ...]] = {
        label: () if kind == "edge" else (label,) for kind, label in BOUNDARY_LEFT_LABELS.items()
    }
    rows_by_item: dict[tuple[str, str], list] = {}
    for row in decision.transitions:
        rows_by_item.setdefault((row.left, row.input_glyph), []).append(row)
    prefixes: dict[tuple[str, str, str | None], tuple[str, ...]] = {}
    by_right3: dict[tuple[str, str, str], dict[tuple[str, str], list[tuple[str, ...]]]] = {}
    queue: deque[tuple[str, str, str | None]] = deque()
    for left, input_label in sorted(rows_by_item):
        if left in boundary_prefixes:
            item = (left, input_label, None)
            prefixes[item] = boundary_prefixes[left]
            queue.append(item)
    while queue:
        item = queue.popleft()
        left, input_label, constraint = item
        extended = prefixes[item] + (_label_family(input_label),)
        for row in rows_by_item.get((left, input_label), ()):
            if constraint is not None and row.right1 != constraint:
                continue
            if row.right1 in BOUNDARYISH:
                continue
            successor = (row.outcome, row.right1, row.right2)
            options = by_right3.setdefault(successor, {})
            for member3 in _token_members(decision, row.right3):
                for member4 in _token_members(decision, row.right4):
                    deep_key = (member3, member4)
                    bucket = options.setdefault(deep_key, [])
                    if extended not in bucket:
                        # Keep a few shortest alternates, not just the winner: distinct producer paths collapse onto one deep-key (both #NA when the producer is not deep), and a deep record further upstream can invalidate the shortest path for specific target windows — the ·Day·Tea·No·Tea route to a ·Tea.en-y0 left is the worked case.
                        bucket.append(extended)
                        bucket.sort(key=lambda p: (len(p), p))
                        del bucket[6:]
            if successor in prefixes:
                continue
            prefixes[successor] = extended
            queue.append(successor)
    return prefixes, by_right3


def _refolds_intact(spec, tokens) -> bool:
    """Whether a witness token stream survives the raw replay unchanged: expand ligature tokens to components, re-run guarded formation, and demand the original stream back. A prefix ligature can be un-formed (its guard slots are the witness's own following tokens) and an adjacent pair can re-form — either way the stream no longer realizes the intended window."""
    from rebuild.pipeline import settle as settle_module

    boundary_by_label = {
        "space": settle_module.SPACE,
        "uni200C": settle_module.ZWNJ,
        "periodcentered": settle_module.NAMER_DOT,
    }
    label_by_kind = {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}
    stream: list = []
    for token in tokens:
        if token in boundary_by_label:
            stream.append(boundary_by_label[token])
            continue
        for part in spec.runes[token].sequence or (token,):
            stream.append(settle_module.RightToken("letter", part))
    formed = settle_module.form_ligatures(spec, stream)
    labels = [label_by_kind[t.kind] if t.kind != "letter" else t.letter for t in formed]
    return labels == list(tokens)


def _window_witness_candidates(spec, prefixes, by_right3, row, decision=None) -> list[tuple[str, ...]]:
    """Assemble candidate token streams that could realize one transition row's window, most-plausible prefix first: the BFS prefixes pinned to the row's own deep slots — a class token trying each member's label-grain bucket in member order — then the unpinned buckets, then the flat shortest prefixes. Several candidates exist because a deep record upstream of the prefix can invalidate one path for this row's specific right context while another path stays realizable; the caller sweeps or settles each until the window is actually realized."""
    from rebuild.pipeline.table import NA_LABEL

    # The backtrack cell's own third and fourth lookaheads are this window's look2 and look3 (row.right2, row.right3), so a backtrack whose withdrawal turns on a raw deep slot must be realized by a prefix pinned to those same values — a flat shortest prefix can carry deep slots this rule never takes (the depth-3-/depth-4-conditional withdrawal cases). Each pinned bucket's premise is that its member label sits at the target's own r3 slot, so the assembly pins that member, not the class representative — an unpinned prefix (the #NA buckets and the flat fallbacks) carries no such premise and assembles with the representative.
    options = by_right3.get((row.left, row.input_glyph, row.right1), {})
    rep3 = _token_representative(decision, getattr(row, "right3", NA_LABEL))
    bucket_keys: list[tuple[tuple[str, str], str]] = []
    for member in _token_members(decision, getattr(row, "right3", NA_LABEL)):
        bucket_keys.append(((row.right2, member), member))
    bucket_keys.append(((row.right2, NA_LABEL), rep3))
    bucket_keys.append(((NA_LABEL, NA_LABEL), rep3))
    ordered_prefixes: list[tuple[tuple[str, ...], str]] = []
    seen_keys: set[tuple[str, str]] = set()
    for bucket_key, member in bucket_keys:
        if bucket_key in seen_keys:
            continue
        seen_keys.add(bucket_key)
        for prefix in options.get(bucket_key, ()):
            if (prefix, member) not in ordered_prefixes:
                ordered_prefixes.append((prefix, member))
    for flat_key in ((row.left, row.input_glyph, row.right1), (row.left, row.input_glyph, None)):
        prefix = prefixes.get(flat_key)
        if prefix is not None and (prefix, rep3) not in ordered_prefixes:
            ordered_prefixes.append((prefix, rep3))
    candidates: list[tuple[str, ...]] = []
    for prefix, member in ordered_prefixes[:6]:
        tokens = _assemble_window_witness(spec, prefix, row, decision, right3_label=member)
        if tokens is not None and tokens not in candidates:
            candidates.append(tokens)
    return candidates


def _guard_follower_slots(spec, settle_module, label):
    """The raw slots a section 5.7 guard reads when the window slot after a formation pair holds `label`: a formed-ligature label stands for its own component sequence in the raw stream, so the guard's two slots are that ligature's lead and trailing components — pinned by the label itself, with no room for a hunted second-slot token between them — while a letter label is its own first slot with the second left open (None) for the caller's hunt. First possible with qsSee_qsUtter, whose lead is itself a member of another ligature's guard class."""
    rune = spec.runes.get(label)
    if rune is not None and rune.sequence:
        return (
            settle_module.RightToken("letter", rune.sequence[0]),
            settle_module.RightToken("letter", rune.sequence[-1]),
        )
    return settle_module.RightToken("letter", label), None


def _formed_liga_guard_repair(spec, settle_module, tokens: list) -> bool:
    """A formed-ligature label anywhere in an assembled stream must satisfy its own section 5.7 guard over the raw tokens that follow it, or the raw replay would leave the pair unformed and the stream would never realize the window. An interior label has its guard slots pinned by the stream, so a blocked verdict just discards the candidate — the same fate `_refolds_intact` would deliver, caught early. A label whose letter follower is the stream's last token reads the text edge at the guard's second slot, and a blocked verdict there is repaired in place by appending a letter under which the guard releases. First needed when the guard learned right2-dependent verdicts: a `(qsDay_qsUtter, qsSee)` lookahead adjacency with nothing deeper pinned is realizable only under a ·Low or ·Utter continuation. Returns False when the stream is unrealizable."""
    from rebuild.pipeline.table import BOUNDARY_LEFT_LABELS

    boundary_kinds = {label: kind for kind, label in BOUNDARY_LEFT_LABELS.items() if kind != "edge"}
    pairs = {
        (rune.sequence[-2], rune.sequence[-1]): name for name, rune in spec.runes.items() if rune.sequence
    }

    def slot(index: int):
        if index >= len(tokens):
            return settle_module.EDGE
        token = tokens[index]
        kind = boundary_kinds.get(token)
        if kind is not None:
            return settle_module.RightToken(kind)
        rune = spec.runes.get(token)
        if rune is not None and rune.sequence:
            return settle_module.RightToken("letter", rune.sequence[0])
        return settle_module.RightToken("letter", token)

    for i in range(len(tokens)):
        rune = spec.runes.get(tokens[i])
        if rune is None or not rune.sequence:
            continue
        follower, pinned_second = (
            _guard_follower_slots(spec, settle_module, tokens[i + 1])
            if i + 1 < len(tokens) and tokens[i + 1] not in boundary_kinds
            else (slot(i + 1), None)
        )
        if follower.kind != "letter":
            continue
        second = pinned_second if pinned_second is not None else slot(i + 2)
        if not settle_module.formation_blocked(spec, tokens[i], follower, second):
            continue
        if pinned_second is not None or i + 2 < len(tokens):
            return False
        appended = next(
            (
                name
                for name in sorted(spec.runes)
                if not spec.runes[name].sequence
                and (tokens[i + 1], name) not in pairs
                and not settle_module.formation_blocked(
                    spec, tokens[i], follower, settle_module.RightToken("letter", name)
                )
            ),
            None,
        )
        if appended is None:
            return False
        tokens.append(appended)
    return True


def _assemble_window_witness(spec, prefix, row, decision=None, right3_label=None) -> tuple[str, ...] | None:
    """One candidate stream from one prefix: the prefix, the input, then just enough right context to pin right1/right2 (a boundary token, a letter, or nothing for the text edge) plus the pinned deep slots — a class token pinned by `right3_label` when the caller's prefix bucket carries a member premise, by its representative member otherwise, and every read below sees the choice because the two locals are resolved at assignment. A window whose input and right1 are a formation pair exists only where the section 5.7 guard fires, and the guard's second slot is the token after right2 — beyond what the window pins — so such a witness is extended with a second-slot letter under which the guard fires. The assembled stream must survive the raw replay unchanged (`_refolds_intact`); a stream the guard would refold differently — a prefix ligature un-formed, an adjacent pair re-formed — is discarded so the caller falls through to the next candidate."""
    from rebuild.pipeline import settle as settle_module
    from rebuild.pipeline.table import BOUNDARY_LEFT_LABELS, EDGE_LABEL, NA_LABEL

    boundary_labels = {label for kind, label in BOUNDARY_LEFT_LABELS.items() if kind != "edge"}
    tokens = list(prefix) + [_label_family(row.input_glyph)]
    if row.right1 == EDGE_LABEL:
        pass
    elif row.right1 in boundary_labels:
        tokens.append(row.right1)
    else:
        tokens.append(_label_family(row.right1))
        if row.right2 in (EDGE_LABEL, NA_LABEL):
            pass
        elif row.right2 in boundary_labels:
            tokens.append(row.right2)
        else:
            tokens.append(_label_family(row.right2))
            pairs = {
                (rune.sequence[-2], rune.sequence[-1]): name
                for name, rune in spec.runes.items()
                if rune.sequence
            }
            right3 = (
                right3_label
                if right3_label is not None
                else _token_representative(decision, getattr(row, "right3", NA_LABEL))
            )
            right4 = _token_representative(decision, getattr(row, "right4", NA_LABEL))
            if right3 not in (EDGE_LABEL, NA_LABEL):
                # A pinned third slot doubles as the guard-firing follower the two search branches in the else-arm would otherwise hunt for a formation pair at (input, right1) — the table's right3 options already replay that guard's filters. A pair at (right1, right2) instead pushes its guard one slot over, onto (right3, right4): with right4 pinned the table's right4 options already replayed that guard too, otherwise its guard-firing token is searched at the fourth slot. A surviving pair at (right2, right3) needs a guard-firing token at whichever of the fourth or fifth slot is first unpinned, and a surviving pair at (right3, right4) pushes its own guard onto the fifth slot.
                if right3 in boundary_labels:
                    tokens.append(right3)
                else:
                    tokens.append(_label_family(right3))
                    right4_letter = right4 not in (EDGE_LABEL, NA_LABEL) and right4 not in boundary_labels
                    if right4 in boundary_labels:
                        tokens.append(right4)
                    elif right4_letter:
                        tokens.append(_label_family(right4))
                    liga_at_lookahead = pairs.get((_label_family(row.right1), _label_family(row.right2)))
                    if liga_at_lookahead is not None and right4 in (EDGE_LABEL, NA_LABEL):
                        follower, pinned_second = _guard_follower_slots(
                            spec, settle_module, _label_family(right3)
                        )
                        if pinned_second is not None:
                            if not settle_module.formation_blocked(
                                spec, liga_at_lookahead, follower, pinned_second
                            ):
                                return None
                        elif not settle_module.formation_blocked(
                            spec, liga_at_lookahead, follower, settle_module.EDGE
                        ):
                            second = next(
                                (
                                    name
                                    for name in sorted(spec.runes)
                                    if not spec.runes[name].sequence
                                    and (_label_family(right3), name) not in pairs
                                    and settle_module.formation_blocked(
                                        spec,
                                        liga_at_lookahead,
                                        follower,
                                        settle_module.RightToken("letter", name),
                                    )
                                ),
                                None,
                            )
                            if second is None:
                                return None
                            tokens.append(second)
                    liga_past = pairs.get((_label_family(row.right2), _label_family(right3)))
                    if liga_past is not None and right4_letter:
                        r4_token, pinned_second = _guard_follower_slots(
                            spec, settle_module, _label_family(right4)
                        )
                        if pinned_second is not None:
                            if not settle_module.formation_blocked(spec, liga_past, r4_token, pinned_second):
                                return None
                        elif not settle_module.formation_blocked(
                            spec, liga_past, r4_token, settle_module.EDGE
                        ):
                            second = next(
                                (
                                    name
                                    for name in sorted(spec.runes)
                                    if not spec.runes[name].sequence
                                    and (_label_family(right4), name) not in pairs
                                    and settle_module.formation_blocked(
                                        spec, liga_past, r4_token, settle_module.RightToken("letter", name)
                                    )
                                ),
                                None,
                            )
                            if second is None:
                                return None
                            tokens.append(second)
                    elif (
                        liga_past is not None
                        and right4 in (EDGE_LABEL, NA_LABEL)
                        and not settle_module.formation_blocked(
                            spec, liga_past, settle_module.EDGE, settle_module.EDGE
                        )
                    ):
                        follower = next(
                            (
                                name
                                for name in sorted(spec.runes)
                                if not spec.runes[name].sequence
                                and (_label_family(right3), name) not in pairs
                                and settle_module.formation_blocked(
                                    spec,
                                    liga_past,
                                    settle_module.RightToken("letter", name),
                                    settle_module.EDGE,
                                )
                            ),
                            None,
                        )
                        if follower is None:
                            return None
                        tokens.append(follower)
                    if right4_letter:
                        liga_next = pairs.get((_label_family(right3), _label_family(right4)))
                        if liga_next is not None and not settle_module.formation_blocked(
                            spec, liga_next, settle_module.EDGE, settle_module.EDGE
                        ):
                            follower = next(
                                (
                                    name
                                    for name in sorted(spec.runes)
                                    if not spec.runes[name].sequence
                                    and (_label_family(right4), name) not in pairs
                                    and settle_module.formation_blocked(
                                        spec,
                                        liga_next,
                                        settle_module.RightToken("letter", name),
                                        settle_module.EDGE,
                                    )
                                ),
                                None,
                            )
                            if follower is None:
                                return None
                            tokens.append(follower)
            else:
                liga = pairs.get((_label_family(row.input_glyph), _label_family(row.right1)))
                if liga is not None:
                    follower, pinned_second = _guard_follower_slots(
                        spec, settle_module, _label_family(row.right2)
                    )
                    if pinned_second is not None:
                        if not settle_module.formation_blocked(spec, liga, follower, pinned_second):
                            return None
                    elif not settle_module.formation_blocked(spec, liga, follower, settle_module.EDGE):
                        second = next(
                            (
                                name
                                for name in sorted(spec.runes)
                                if not spec.runes[name].sequence
                                and (_label_family(row.right2), name) not in pairs
                                and settle_module.formation_blocked(
                                    spec, liga, follower, settle_module.RightToken("letter", name)
                                )
                            ),
                            None,
                        )
                        if second is None:
                            return None
                        tokens.append(second)
                liga_at_lookahead = pairs.get((_label_family(row.right1), _label_family(row.right2)))
                if liga_at_lookahead is not None and not settle_module.formation_blocked(
                    spec, liga_at_lookahead, settle_module.EDGE, settle_module.EDGE
                ):
                    follower = next(
                        (
                            name
                            for name in sorted(spec.runes)
                            if not spec.runes[name].sequence
                            and (_label_family(row.right2), name) not in pairs
                            and settle_module.formation_blocked(
                                spec,
                                liga_at_lookahead,
                                settle_module.RightToken("letter", name),
                                settle_module.EDGE,
                            )
                        ),
                        None,
                    )
                    if follower is None:
                        return None
                    tokens.append(follower)
    if not _formed_liga_guard_repair(spec, settle_module, tokens):
        return None
    if not _refolds_intact(spec, tokens):
        return None
    return tuple(tokens)


def _first_match_rows(decision) -> dict[int, list]:
    """Group the table's transitions by the rule index that first-matches each window, replaying the same first-match-wins semantics assert_outcome_partition proves — the static answer to 'which windows would make rule N fire?'. A deep slot holding a class token is tested through its representative member, exact by the build's union-of-fibers assertion."""
    rules_by_input: dict[str, list[tuple[int, Rule]]] = {}
    for index, rule in enumerate(decision.rules):
        rules_by_input.setdefault(rule.input_glyph, []).append((index, rule))
    rows_by_rule: dict[int, list] = {}
    for row in decision.transitions:
        right3 = _token_representative(decision, getattr(row, "right3", "#NA"))
        right4 = _token_representative(decision, getattr(row, "right4", "#NA"))
        for index, rule in rules_by_input.get(row.input_glyph, ()):
            if rule.backtrack is not None and row.left not in rule.backtrack:
                continue
            if rule.look1 is not None and row.right1 not in rule.look1:
                continue
            if rule.look2 is not None and row.right2 not in rule.look2:
                continue
            look3 = getattr(rule, "look3", None)
            if look3 is not None and right3 not in look3:
                continue
            look4 = getattr(rule, "look4", None)
            if look4 is not None and right4 not in look4:
                continue
            rows_by_rule.setdefault(index, []).append(row)
            break
    return rows_by_rule


def _candidate_witness_tokens(
    spec, prefixes, by_right3, rows, decision=None, limit: int = 10
) -> list[tuple[str, ...]]:
    candidates = {
        tokens
        for row in rows
        for tokens in _window_witness_candidates(spec, prefixes, by_right3, row, decision)
    }
    return sorted(candidates, key=lambda tokens: (len(tokens), tokens))[:limit]


def rule_signature(rule) -> str:
    slots = ", ".join(
        f"{name}={list(value) if value is not None else 'any'}"
        for name, value in (
            ("backtrack", rule.backtrack),
            ("look1", rule.look1),
            ("look2", rule.look2),
            ("look3", getattr(rule, "look3", None)),
            ("look4", getattr(rule, "look4", None)),
        )
    )
    return f"{rule.input_glyph} [{slots}] -> {rule.outcome}"


@dataclass
class WitnessReport:
    config: str
    rules: int
    witnessed: dict[int, str] = field(default_factory=dict)
    unwitnessed: list[int] = field(default_factory=list)


def find_rule_witnesses(spec, features, decision, glyph_names=None) -> WitnessReport:
    """The font-free half of rule coverage: for every settlement rule, derive a shortest realizing string from the table's windows and verify against settle() that the rule actually first-matches somewhere in it. A rule left unwitnessed has no realizing string the table can construct — dead code in the emitted FEA — so this doubles as the always-on generator-defect alarm (rebuild/test_rule_witnesses.py) while run_conformance shapes the same witnesses against the real binary."""
    from rebuild.pipeline import settle as settle_module
    from rebuild.pipeline.settle import cell_label

    if glyph_names is None:
        glyph_names = {cell: cell_label(spec, cell) for cell in decision.reachable_cells()}
    from rebuild.pipeline import table as table_module
    from rebuild.pipeline.emit_gsub import _raw_rename_map

    rules_by_input = _renamed_rules_by_input(spec, features, decision)
    prefixes, by_right3 = _shortest_window_prefixes(decision)
    rows_by_rule = _first_match_rows(decision)
    engine = settle_module.Engine(spec, frozenset(features))
    deep = table_module.third_slot_inputs(spec, engine)
    deep4 = table_module.fourth_slot_inputs(spec, engine)
    deep3_live = table_module.third_slot_filter(spec, frozenset(features), engine)
    deep4_live = table_module.fourth_slot_filter(spec, frozenset(features), engine)
    deep_index = (
        _DeepTokenIndex(decision, _raw_rename_map(spec, frozenset(features)))
        if getattr(decision, "deep_classes", None)
        else None
    )
    report = WitnessReport(config=decision.config, rules=len(decision.rules))
    for index in range(len(decision.rules)):
        witness = None
        for tokens in _candidate_witness_tokens(
            spec, prefixes, by_right3, rows_by_rule.get(index, ()), decision
        ):
            text = _token_text(spec, tokens)
            settled = settle_module.settle_with_engine(engine, [ord(ch) for ch in text])
            expected = settled_names(spec, settled, glyph_names)
            if any(
                matched == index
                for _pos, _window, matched in _matched_windows(
                    spec,
                    text,
                    features,
                    expected,
                    rules_by_input,
                    deep,
                    deep3_live,
                    deep4,
                    deep4_live,
                    deep_index,
                )
            ):
                witness = text
                break
        if witness is None:
            report.unwitnessed.append(index)
        else:
            report.witnessed[index] = witness
    return report


WITNESSES_FORMAT = "ams-m1-witnesses/1"


def witnesses_path(out_dir: Path, config: str) -> Path:
    return Path(out_dir) / f"witnesses-{config}.tsv.gz"


def _text_codepoints(text: str) -> str:
    return ":".join(f"{ord(ch):04X}" for ch in text)


def _codepoint_text(codepoints: str) -> str:
    return "".join(chr(int(part, 16)) for part in codepoints.split(":"))


def read_witnesses(path: Path, digest: str) -> tuple[dict[int, str], dict[tuple[str, ...], str]]:
    """The recorded witness texts of one configuration's last hunt — per rule index and per raw window key — or empty maps when the file is missing, unreadable, or carries another table's `windows_digest`. A loaded text is a head start, never a verdict: the top-up loops sweep it and check realization exactly as they would a freshly assembled candidate, so a recording the digest could not see going stale costs one deduplicated sweep and falls through to the normal hunt."""
    rules: dict[int, str] = {}
    windows: dict[tuple[str, ...], str] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            marker, _, payload = handle.readline().rstrip("\n").partition("\t")
            if marker != f"# {WITNESSES_FORMAT}" or json.loads(payload).get("windows_digest") != digest:
                return {}, {}
            for line in handle:
                kind, *fields = line.rstrip("\n").split("\t")
                if kind == "rule":
                    rules[int(fields[0])] = _codepoint_text(fields[1])
                elif kind == "window":
                    windows[tuple(fields[:-1])] = _codepoint_text(fields[-1])
    except OSError, ValueError, IndexError:
        return {}, {}
    return rules, windows


def write_witnesses(
    path: Path,
    digest: str,
    rule_witnesses: Mapping[int, str],
    window_witnesses: Mapping[tuple[str, ...], str],
) -> None:
    """Serialize a hunt's winning witness texts under the table digest they realized against, diff-stable like the enumeration beside them: sorted entries, colon-joined hex codepoints (witness texts carry spaces and ZWNJ, which no TSV cell should), and a zeroed gzip stamp."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
        head = json.dumps({"windows_digest": digest}, separators=(",", ":"))
        handle.write(f"# {WITNESSES_FORMAT}\t{head}\n".encode())
        for index, text in sorted(rule_witnesses.items()):
            handle.write(f"rule\t{index}\t{_text_codepoints(text)}\n".encode())
        for key, text in sorted(window_witnesses.items()):
            handle.write(("\t".join(("window", *key, _text_codepoints(text))) + "\n").encode())


def run_conformance(
    font_path: Path,
    spec: ResolvedSpec,
    configs: Iterable[str] = ACCEPTANCE_CONFIGS,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    max_length: int = 5,
    out_dir: Path | None = None,
    tables: Mapping[str, tuple] | None = None,
    windows: Mapping[str, Path] | None = None,
    boundary_horizon: int | None = None,
    witness_caches: Mapping[str, Path] | None = None,
) -> ConformReport:
    """The serial conformance entry point: one shared Shaper, each config's sweep run in turn through `_conformance_config`, results merged by `merge_conformance_results`. The per-config fan-out lives in run_m1.run_font_conformance, which submits `conformance_config_worker` per config instead. Either source of decision tables spares this run the fixpoint: `windows` maps a config to the enumeration the build stage serialized (`table.write_windows`), read as its config comes up and released with it so no more than one is ever resident, and `tables` is a caller's in-memory `build_tables` mapping (config -> (decision, treaty)). A config in neither rebuilds. `boundary_horizon` is the boundary gate's proven horizon for this exact font (resolve it with `proven_boundary_horizon`), under which each config's sweep inherits the structural checks instead of re-running them; `witness_caches` maps a config to its recorded-witness file, read and rewritten around the hunt."""
    from rebuild.pipeline import table as table_module

    shaper = Shaper(Path(font_path))
    alphabet = spec_alphabet(spec)
    splitters = splitting_boundary_chars(spec)
    glyph_names = {cell: record.name for cell, record in (glyphs or {}).items()}
    glyphs_by_name = {record.name: record for record in (glyphs or {}).values()}
    anchors_of = anchors_in_font_units(glyphs_by_name) if glyphs else None

    def decision_for(config: str):
        if windows is not None and config in windows:
            return table_module.read_windows(windows[config])[1]
        if tables is not None and config in tables:
            return tables[config][0]
        return None

    results = [
        _conformance_config(
            shaper,
            spec,
            config,
            alphabet,
            splitters,
            glyph_names,
            anchors_of,
            max_length,
            decision=decision_for(config),
            boundary_horizon=boundary_horizon,
            witness_cache=None if witness_caches is None else witness_caches.get(config),
        )
        for config in configs
    ]
    report = merge_conformance_results(Path(font_path), results)
    if out_dir is not None:
        report.write(Path(out_dir) / "conform_summary.json")
    return report


@dataclass
class ConformanceConfigResult:
    config: str
    sequences: int = 0
    shaping_runs: int = 0
    divergences: list[Divergence] = field(default_factory=list)
    uncovered_rules: int = 0
    uncovered_transitions: int = 0
    topped_up_rules: int = 0
    topped_up_sequences: int = 0
    notes: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)


def _conformance_config(
    shaper: Shaper,
    spec: ResolvedSpec,
    config: str,
    alphabet: tuple[str, ...],
    splitters: frozenset[str],
    glyph_names: Mapping[CellId, str],
    anchors_of: Callable[[str], dict | None] | None,
    max_length: int,
    decision=None,
    boundary_horizon: int | None = None,
    witness_cache: Path | None = None,
) -> ConformanceConfigResult:
    """One config's whole conformance run: the exhaustive length-1..max_length sweep, then the witness top-ups for rules and decision-table transitions the sweep never fired. Configs share nothing, so this is the unit both the serial wrapper and the process-pool worker call. Settlement and the rule replay ride `_SettledWindowWalk`'s per-config memo, so the sweep's redundant windows cost dict probes rather than transitions, and the realized-window record is the memo itself. Callers that already hold this config's decision table pass it as `decision`; the fixpoint rebuild here is only the standalone fallback.

    Two more levers keep the top-ups from re-spending what something already paid. A `swept` set deduplicates the hunt: a candidate text at or under the sweep horizon was already swept exhaustively, and a repeat of any text cannot add to `realized` or `rules_hit`, so only genuinely new texts shape — which also makes `topped_up_sequences` count distinct swept texts rather than hunt attempts. `witness_cache` names this config's recorded winners from the last hunt (keyed by `table.windows_digest`, so ink-only rune edits keep it warm); each still-unrealized rule or window tries its recording first and assembles fresh candidates only when that misses, so a warm run skips the candidate assembly — `_shortest_window_prefixes` and `_first_match_rows` build lazily, on the first actual miss — and re-sweeps only the distinct winners. `boundary_horizon` (the boundary gate's proven horizon for this font, `proven_boundary_horizon`) lets the sweep inherit `check_zwnj_structure` and `check_split_buffer` for texts within it, where that gate already proved them; top-up texts run past the horizon and keep their own checks.
    """
    from rebuild.pipeline import settle as settle_module
    from rebuild.pipeline import table as table_module
    from rebuild.pipeline.emit_gsub import _raw_rename_map

    features = features_for_config(config)
    engine = settle_module.Engine(spec, features)
    if decision is None:
        built = table_module.build_tables(spec, features)
        decision = built[0] if isinstance(built, (tuple, list)) else built
    elif not decision.transitions:
        raise ValueError(
            f"{config}: the supplied decision table carries no windows — coverage is measured against them, so the sweep would score clean over nothing"
        )
    renames = _raw_rename_map(spec, frozenset(features))
    rules_by_input = _renamed_rules_by_input(spec, features, decision)

    result = ConformanceConfigResult(config=config)
    deep = table_module.third_slot_inputs(spec, engine)
    deep3_live = table_module.third_slot_filter(spec, features, engine)
    deep4 = table_module.fourth_slot_inputs(spec, engine)
    deep4_live = table_module.fourth_slot_filter(spec, features, engine)
    deep_index = _DeepTokenIndex(decision, renames) if getattr(decision, "deep_classes", None) else None
    modes: set[str] = set()
    rules_hit: set[int] = set()
    overlay = isolated_overlay_active(spec, features)
    walker = _SettledWindowWalk(
        spec,
        engine,
        features,
        rules_by_input,
        deep,
        deep3_live,
        deep4,
        deep4_live,
        glyph_names,
        deep_index,
    )
    realized = walker.windows

    def sweep_text(text: str) -> None:
        shaped = shaper.shape(text, features)
        result.shaping_runs += 1
        if boundary_horizon is None or len(text) > boundary_horizon:
            check_zwnj_structure(text, config, shaper, shaped, result.divergences)
            if set(text) & splitters:
                check_split_buffer(text, config, features, shaper, shaped, result.divergences, splitters)
        settled, expected_cells, matched_rules = walker.walk(text)
        expected = isolated_overlay_names(spec, settled) if overlay else expected_cells
        check_oracle(text, config, shaped, expected, result.divergences, modes)
        if anchors_of is not None:
            check_join_gaps(text, config, shaper, shaped, anchors_of, result.divergences)
        rules_hit.update(index for index in matched_rules if index is not None)

    if boundary_horizon is not None:
        modes.add(
            f"zwnj/split-buffer checks for texts within length {boundary_horizon} inherited from the green boundary gate"
        )

    for length in range(1, max_length + 1):
        for combo in itertools.product(alphabet, repeat=length):
            result.sequences += 1
            sweep_text("".join(combo))

    swept: set[str] = set()

    def sweep_top_up(text: str) -> None:
        if len(text) <= max_length or text in swept:
            return
        swept.add(text)
        sweep_text(text)
        result.topped_up_sequences += 1

    if witness_cache is not None:
        digest = table_module.windows_digest(decision)
        recorded_rules, recorded_windows = read_witnesses(witness_cache, digest)
    else:
        digest = None
        recorded_rules, recorded_windows = {}, {}

    hunt_state: tuple[dict, dict] | None = None

    def hunt_prefixes() -> tuple[dict, dict]:
        nonlocal hunt_state
        if hunt_state is None:
            hunt_state = _shortest_window_prefixes(decision)
        return hunt_state

    rows_by_rule: dict[int, list] | None = None

    def rule_rows(index: int) -> list:
        nonlocal rows_by_rule
        if rows_by_rule is None:
            rows_by_rule = _first_match_rows(decision)
        return rows_by_rule.get(index, [])

    unhit = [index for index in range(len(decision.rules)) if index not in rules_hit]
    witnessed: dict[int, str] = {}
    for index in unhit:
        recorded = recorded_rules.get(index)
        if recorded is not None:
            sweep_top_up(recorded)
            if index in rules_hit:
                witnessed[index] = recorded
                continue
        prefixes, by_right3 = hunt_prefixes()
        for tokens in _candidate_witness_tokens(spec, prefixes, by_right3, rule_rows(index), decision):
            text = _token_text(spec, tokens)
            sweep_top_up(text)
            if index in rules_hit:
                witnessed[index] = text
                break
    result.topped_up_rules = len(witnessed)
    for index, text in sorted(witnessed.items()):
        codepoints = ":".join(f"{ord(ch):04X}" for ch in text)
        result.notes.append(
            f"{config}: rule beyond the length-{max_length} sweep, witnessed by {codepoints}: {rule_signature(decision.rules[index])}"
        )
    dead = [index for index in unhit if index not in rules_hit]
    result.uncovered_rules = len(dead)
    for index in dead:
        result.notes.append(
            f"{config}: settlement rule has no verifiable witness (dead code in the emitted FEA): {rule_signature(decision.rules[index])}"
        )

    def renamed_key(row: Window) -> tuple[str, str, str, str, str, str]:
        return (
            renames.get(row.input_glyph, row.input_glyph),
            row.left,
            renames.get(row.right1, row.right1),
            renames.get(row.right2, row.right2),
            renames.get(row.right3, row.right3),
            renames.get(row.right4, row.right4),
        )

    window_witnesses: dict[tuple[str, ...], str] = {}
    for row in decision.transitions:
        key = renamed_key(row)
        if key in realized:
            continue
        raw_key = (row.input_glyph, row.left, row.right1, row.right2, row.right3, row.right4)
        recorded = recorded_windows.get(raw_key)
        if recorded is not None:
            sweep_top_up(recorded)
            if key in realized:
                window_witnesses[raw_key] = recorded
                continue
        prefixes, by_right3 = hunt_prefixes()
        for tokens in _window_witness_candidates(spec, prefixes, by_right3, row, decision):
            text = _token_text(spec, tokens)
            sweep_top_up(text)
            if key in realized:
                window_witnesses[raw_key] = text
                break
    unrealized = [row for row in decision.transitions if renamed_key(row) not in realized]
    result.uncovered_transitions = len(unrealized)
    if unrealized:
        result.notes.append(
            f"{config}: {len(unrealized)} decision-table transitions never realized; first: {unrealized[0].key}"
        )
    if deep_index is not None:
        deep_map = decision.deep_classes
        class_rows = 0
        label_windows = 0
        for row in decision.transitions:
            members3 = deep_map.get(row.right3)
            members4 = deep_map.get(row.right4)
            if members3 is None and members4 is None:
                continue
            class_rows += 1
            label_windows += len(members3 or (row.right3,)) * len(members4 or (row.right4,))
        if class_rows:
            # The declared coverage narrowing (issue 26, under issue 7's never-silently rule): a class row is realized if any member is, so a dead label combination inside a live class is no longer independently detected; offsetting it, the exhaustive horizon sweep stays label-grain over real texts and pins keep member sets exact.
            result.notes.append(
                f"{config}: deep slots at class grain: {class_rows} class rows stand for {label_windows} label windows; per-member indiscernibility is build-proven (assert_deep_slot_partition + fiber verification + echo)"
            )
    if witness_cache is not None and digest is not None:
        write_witnesses(
            witness_cache,
            digest,
            {**recorded_rules, **witnessed},
            {**recorded_windows, **window_witnesses},
        )

    result.modes = sorted(modes)
    return result


def conformance_config_worker(
    spec: ResolvedSpec,
    font_path: Path,
    config: str,
    max_length: int = 5,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    decision=None,
    windows_path: Path | None = None,
    boundary_horizon: int | None = None,
    witness_cache_path: Path | None = None,
) -> ConformanceConfigResult:
    """One config's sweep in its own process. `windows_path` names the enumeration the build stage serialized for this config, loaded here rather than shipped in: the parent vetted its fingerprint, and a million rows per config is a pickle across the pool boundary that neither side needs to hold. `boundary_horizon` and `witness_cache_path` pass through to `_conformance_config`; the parent resolves the horizon once (`proven_boundary_horizon`) since every config shapes the same font."""
    if decision is None and windows_path is not None:
        from rebuild.pipeline import table as table_module

        decision = table_module.read_windows(windows_path)[1]
    shaper = Shaper(Path(font_path))
    alphabet = spec_alphabet(spec)
    splitters = splitting_boundary_chars(spec)
    glyph_names = {cell: record.name for cell, record in (glyphs or {}).items()}
    glyphs_by_name = {record.name: record for record in (glyphs or {}).values()}
    anchors_of = anchors_in_font_units(glyphs_by_name) if glyphs else None
    return _conformance_config(
        shaper,
        spec,
        config,
        alphabet,
        splitters,
        glyph_names,
        anchors_of,
        max_length,
        decision=decision,
        boundary_horizon=boundary_horizon,
        witness_cache=witness_cache_path,
    )


def merge_conformance_results(font_path: Path, results: Iterable[ConformanceConfigResult]) -> ConformReport:
    """Fold per-config results into one ConformReport. `sequences` comes from the first result — every config sweeps the identical sequence set — while the counters sum and the divergences/notes concatenate in the caller's config order; the oracle modes are unioned and appended sorted, matching what the interleaved serial loop used to produce."""
    report = ConformReport(font=str(font_path))
    results = list(results)
    report.sequences = results[0].sequences if results else 0
    modes: set[str] = set()
    for result in results:
        report.shaping_runs += result.shaping_runs
        report.divergences.extend(result.divergences)
        report.uncovered_rules += result.uncovered_rules
        report.uncovered_transitions += result.uncovered_transitions
        report.topped_up_rules += result.topped_up_rules
        report.topped_up_sequences += result.topped_up_sequences
        report.notes.extend(result.notes)
        modes.update(result.modes)
    report.notes.extend(sorted(modes))
    return report


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
        # The ratified boundary-equals-word-boundary rule (design section 3.4): the new font renders every segment of a window containing a run-splitting boundary (space or ZWNJ) identically to that segment standing alone — enforced per build by run_boundary_equivalence — so a boundary row can only diverge from the baseline where the old font was itself inconsistent across the boundary, and every segment-internal divergence resurfaces on the segment's own enumerated row. Boundary rows therefore carry no adjudicable information and are absorbed wholesale, ahead of every other cell/seam-grain class.
        return "boundary-echo"
    if "ligation" in phenomena:
        # Under the isolated overlay the new font never forms the ligature at all (the ss10 pre-empt replaces every letter before formation) while the old font keeps drawing its own ligature, so the suppression class outranks the marker-staging one (whose 00B7 arm would otherwise swallow the namer-dot ss10 windows).
        if row.config == "ss10" and (
            "E653:E67A" in row.codepoints or "E652:E679" in row.codepoints or "E67B:E652" in row.codepoints
        ):
            return "ss10-ligature-suppressed"
        if "E67B:E652" in row.codepoints and "ss03" in row.config:
            return "ss03-out-tea-ligature-kept"
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
    if "-en-ext-1:qsMay" in phenomena:
        return "may-baseline-entry-extension-dropped"
    if "-en-ext-1:qsNo" in phenomena:
        return "no-xheight-entry-extension-dropped"
    if phenomena & {"-en-ext-1:qsDay", "-en-ext-1:qsDay_qsUtter"}:
        return "day-baseline-entry-extension-dropped"
    if "-en-ext-1:qsVie" in phenomena:
        return "vie-baseline-entry-extension-dropped"
    if (
        "-ex-con-1" in phenomena
        and phenomena <= {"-ex-con-1", "+en-trim-1"}
        and "E65A:E67B" in row.codepoints
    ):
        # The grounded ·See·Out fusion re-spells the old pull-back across the seam: the old pipeline's ex-con-1 tucks ·Out into ·See's still-whole tail (anchor-only, ink kept), while the runes keep the tail's anchor at convention and pull the raked redraw's foot instead, so the composite ink is identical and only the names differ. The subset guard keeps any row where real ink moved elsewhere out of the class.
        return "see-out-fusion-respelled"
    if (
        "+ex-ext-3" in phenomena
        and phenomena <= {"+ex-ext-3", "-ex-ext-1", "exit-dropped"}
        and "E665:E65D" in row.codepoints
    ):
        # The ·May·J'ai seam consolidates the old split extension (May ex-ext-1 + J'ai en-ext-2, +3 total) onto qsMay's single by-3 exit record: the same three connector pixels ride one side, the composite ink and every glyph origin are unchanged, and only the names differ. The subset guard keeps any row where real ink moved elsewhere out of the class.
        return "may-jai-extension-consolidated"
    if phenomena and phenomena <= {"+en-con-1", "+en-con-2"} and "E65D" in row.codepoints:
        # The old pipeline's exit contractions before ·J'ai are tucks — the left keeps its ink and only the anchor moves in, overlapping the follower — which M1 re-spells as ·J'ai's own entry contraction: the crown gives up the overlapped columns and abuts instead, so the placed composite, every origin, and every advance are unchanged and only ·J'ai's cell name gains the con token. The subset guard keeps any row where real ink moved elsewhere out of the class.
        return "jai-entry-contraction-respelled"
    # The may-exit-withdrawal-generalized class retired with qsMay's pulled-back exit; a row resurrecting these phenomena carries an ink delta, so it must surface UNMATCHED rather than fall through to the name-grain classes below.
    if any(item.startswith("+ex-bind-") for item in phenomena) or "-ex-ext-1" in phenomena:
        return None
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
    "ss10-ligature-suppressed",
    "ss03-out-tea-ligature-kept",
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
    "may-baseline-entry-extension-dropped",
    "no-xheight-entry-extension-dropped",
    "day-baseline-entry-extension-dropped",
    "vie-baseline-entry-extension-dropped",
    "zwnj-word-initial-unification",
    "dangling-anchor-dropped",
    "bare-name-live-join",
    "see-out-fusion-respelled",
    "may-jai-extension-consolidated",
    "jai-entry-contraction-respelled",
):
    PREDICATES[_class_id.replace("-", "_")] = _class_predicate(_class_id)


@predicate("kern_channel_out_of_scope")
def _kern_channel_out_of_scope(row: DivergentRow) -> bool:
    """Position-only rows whose drifted slot the comparison marked kern-attributable (the old pair carries a nonzero sidecar kern, or the drift sits on a ZWNJ adjacency). Everything else position-shaped stays unmatched and fails — non-kern position drift is chased to ground, never absorbed here."""
    return row.kinds == ("position",) and "position-kern-attributable" in row.phenomena


# Cell-grain tokens that ride the ink-identical name-grain classes; anything outside this set on a seam-loosened candidate means real ink moved elsewhere in the row, so the row stays unmatched and fails.
_NAME_GRAIN_TOKENS = frozenset(
    {"stance", "entry-added", "entry-moved", "entry-dropped", "exit-added", "exit-moved", "exit-dropped"}
)


@predicate("may_ligature_seam_loosened")
def _may_ligature_seam_loosened(row: DivergentRow) -> bool:
    """The adjudicated ·Day·Utter→·May x-height seam: the old font tucks ·May's x-height entry one pixel into the ligature's exit, the new model seats it at the anchor-aligned column and draws no connector, and the looser seat is the intended design (the may-ligature-seam-loosened ledger entry carries the adjudication). Matches non-kern position drift on rows whose old names carry that exact pair and whose cell-grain residue (if any) is pure name grain."""
    if "position-drift" not in row.phenomena or "position-kern-attributable" in row.phenomena:
        return False
    cell_grain = {item for item in row.phenomena if not item.startswith("position")}
    if not cell_grain <= _NAME_GRAIN_TOKENS:
        return False
    glyphs = row.baseline_glyphs
    return any(
        glyphs[index].startswith("qsDay_qsUtter") and glyphs[index + 1].startswith("qsMay.en-y5")
        for index in range(len(glyphs) - 1)
    )


# The migrated runes whose joins the old shipped font never wired into the ss10 isolated overlay, so the old font keeps drawing their cursive joins under ss10 while the new model isolates every letter by design.
# Membership is not automatic for a newly-migrated rune: qsFee was weighed and deliberately left out, because the old ss10 overlay substitutes every qsFee variant to the bare cmap glyph, which carries no cursive anchors, so the old font already isolates ·Fee correctly and its ss10 seam-loss rows ride the existing ss10_isolation_completed class instead.
# qsAh is a member because its baseline entry and x-height exit anchors ride the base cmap glyph (the ·Pea/·Oy→·Ah and ·Ah→·Day joins are bare-glyph GPOS attachments with no calt variant), so the old ss10 overlay has nothing to substitute away and keeps drawing those joins.
# qsOut is a member on the qsAh precedent, entry side only: its baseline entry anchor rides the bare cmap glyph (E650:E67B stays a y0 join under the old ss10), while its x-height exits live on calt variants the old overlay does substitute away. qsOut_qsTea inherits the same bare-glyph entry from its lead, the qsDay_qsUtter shape.
SS10_UNCOVERED_BY_OLD_FONT = frozenset(
    {"qsAh", "qsDay", "qsNo", "qsLow", "qsUtter", "qsDay_qsUtter", "qsOut", "qsOut_qsTea"}
)


@predicate("ss10_isolation_completed")
def _ss10_isolation_completed(row: DivergentRow) -> bool:
    """Under ss10 the new model renders every position bare (the overlay forces the default stance with no seam), so a join the old font still drew there reads as a seam-loss. The old font's ss10 overlay was authored before the runes in `SS10_UNCOVERED_BY_OLD_FONT` (whose anchors ride the base cmap glyph, so the old overlay keeps their joins too) and never isolates them, so it keeps joining the new letters under ss10; the new font's complete isolation is the intended correction. Matches ss10 rows whose only seam change is losses, each on a seam touching one of those new runes (an existing|existing seam never joins under the old ss10, so it can never reach here). Space and ZWNJ rows are excluded so the boundary-echo blanket keeps the partition exact."""
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


ORACLE_AUDIT_HEADER = "config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew"


@dataclass
class OracleConfigResult:
    config: str
    rows_compared: int = 0
    divergent_rows: int = 0
    positions_compared: int = 0
    positions_excluded: int = 0
    counts_by_entry: dict[str, int] = field(default_factory=dict)
    unmatched: list[DivergentRow] = field(default_factory=list)
    multi_matched: list[tuple[DivergentRow, tuple[str, ...]]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    audit_lines: list[str] = field(default_factory=list)


def _compare_config(
    spec: ResolvedSpec,
    settle_module,
    subset_tables_dir: Path,
    config: str,
    features: frozenset[str],
    aliases,
    ledger,
    ink_identical_ids,
    shaper: "Shaper | None",
    kern: "KernEvaluator | None",
    engine,
) -> OracleConfigResult:
    result = OracleConfigResult(config=config)
    table_path = Path(subset_tables_dir) / f"baseline-{config}.subset.tsv.gz"
    if not table_path.exists():
        result.notes.append(f"{config}: subset table missing at {table_path}")
        return result
    config_started = time.perf_counter()
    for row in iter_rows(table_path):
        result.rows_compared += 1
        divergent = _compare_row(spec, settle_module, aliases, config, features, row, engine=engine)
        matches = _match_ledger(ledger, divergent) if divergent is not None else []
        if shaper is not None:
            topology_clean = divergent is None or not ({"ligation", "seam"} & set(divergent.kinds))
            class_claims_ink_identity = divergent is None or (
                len(matches) == 1 and matches[0] in ink_identical_ids
            )
            if topology_clean and class_claims_ink_identity:
                drift = _position_drift(shaper, kern, features, row)
                result.positions_compared += 1
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
                result.positions_excluded += 1
        if divergent is None:
            continue
        result.divergent_rows += 1
        if len(matches) == 1:
            entry_id = matches[0]
            result.counts_by_entry[entry_id] = result.counts_by_entry.get(entry_id, 0) + 1
        elif not matches:
            result.unmatched.append(divergent)
        else:
            result.multi_matched.append((divergent, tuple(matches)))
        result.audit_lines.append(
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
    print(
        f"[t] oracle {config} {time.perf_counter() - config_started:.2f}s rows={result.rows_compared} positions={result.positions_compared}",
        file=sys.stderr,
        flush=True,
    )
    return result


def oracle_config_worker(
    spec: ResolvedSpec,
    subset_tables_dir: Path,
    alias_path: Path,
    ledger_path: Path,
    config: str,
    font_path: Path | None,
    kern_sidecar_path: Path | None,
) -> OracleConfigResult:
    from rebuild.pipeline import settle as settle_module

    aliases = load_alias_map(alias_path)
    ledger = yaml.safe_load(Path(ledger_path).read_text()) or []
    ink_identical_ids = {entry.get("id") for entry in ledger if entry.get("ink_identical")}
    shaper = Shaper(Path(font_path)) if font_path is not None else None
    kern = KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    features = features_for_config(config)
    engine = settle_module.Engine(spec, features)
    return _compare_config(
        spec,
        settle_module,
        subset_tables_dir,
        config,
        features,
        aliases,
        ledger,
        ink_identical_ids,
        shaper,
        kern,
        engine,
    )


def merge_oracle_results(results: Iterable[OracleConfigResult]) -> tuple[BaselineReport, list[str]]:
    report = BaselineReport()
    audit_lines = [ORACLE_AUDIT_HEADER]
    for result in results:
        report.rows_compared += result.rows_compared
        report.divergent_rows += result.divergent_rows
        report.positions_compared += result.positions_compared
        report.positions_excluded += result.positions_excluded
        for entry_id, count in result.counts_by_entry.items():
            report.counts_by_entry[entry_id] = report.counts_by_entry.get(entry_id, 0) + count
        report.unmatched.extend(result.unmatched)
        report.multi_matched.extend(result.multi_matched)
        report.notes.extend(result.notes)
        audit_lines.extend(result.audit_lines)
    return report, audit_lines


def compare_against_baseline(
    spec: ResolvedSpec,
    subset_tables_dir: Path,
    alias_path: Path,
    ledger_path: Path,
    configs: Iterable[str] = ACCEPTANCE_CONFIGS,
    out_dir: Path | None = None,
    font_path: Path | None = None,
    kern_sidecar_path: Path | None = None,
    hoist: bool = True,
) -> BaselineReport:
    from rebuild.pipeline import settle as settle_module

    aliases = load_alias_map(alias_path)
    ledger = yaml.safe_load(Path(ledger_path).read_text()) or []
    ink_identical_ids = {entry.get("id") for entry in ledger if entry.get("ink_identical")}
    shaper = Shaper(Path(font_path)) if font_path is not None else None
    kern = KernEvaluator(Path(kern_sidecar_path)) if kern_sidecar_path is not None else None
    started = time.perf_counter()

    results: list[OracleConfigResult] = []
    for config in configs:
        features = features_for_config(config)
        engine = settle_module.Engine(spec, features) if hoist else None
        results.append(
            _compare_config(
                spec,
                settle_module,
                subset_tables_dir,
                config,
                features,
                aliases,
                ledger,
                ink_identical_ids,
                shaper,
                kern,
                engine,
            )
        )
    report, audit_lines = merge_oracle_results(results)

    print(
        f"[t] oracle total {time.perf_counter() - started:.2f}s rows_compared={report.rows_compared} positions_compared={report.positions_compared}",
        file=sys.stderr,
        flush=True,
    )
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "divergence-audit.tsv").write_text("\n".join(audit_lines) + "\n")
    return report


def _compare_row(
    spec, settle_module, aliases, config: str, features: frozenset[str], row: Row, engine=None
) -> DivergentRow | None:
    if engine is not None:
        settled = settle_module.settle_with_engine(engine, list(row.codepoints))
    else:
        settled = settle_module.settle(spec, list(row.codepoints), features)
    if isolated_overlay_active(spec, features):
        # The overlay renders the anchor-free isolated drawing at every letter position; in cell terms that is the boundary cell of the default stance (the alias map's bare-name denotation), with every seam visually a break. A ligature-rune cell expands to one such cell per component (the ss10 pre-empt keeps the ligature from ever forming in the buffer), so a window whose pair formed in the old font diverges at ligation grain.
        expanded: list = []
        for item in settled:
            cell = getattr(item, "cell", None)
            if isinstance(cell, CellId) and cell.rune in spec.runes:
                for rune_name in spec.runes[cell.rune].sequence or (cell.rune,):
                    expanded.append(
                        Settled(
                            cell=CellId(rune_name, spec.runes[rune_name].default_stance, None, None, ()),
                            seam=None,
                            extension=0,
                        )
                    )
            else:
                expanded.append(item)
        settled = expanded
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
        if token != "en-ext-1":
            out.add(f"-{token}")
        elif index > 0 and "ex-ext-1" in old_glyphs[index - 1]:
            out.add("-en-ext-1:same-seam")
        else:
            out.add(f"-en-ext-1:{cell.rune}")
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
