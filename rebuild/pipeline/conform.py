"""Conformance gates (M1-PLAN sections 5 and 6, Group 3): HarfBuzz vs the settlement function, and the settlement function vs the section 13.1 baseline oracle.

`run_conformance` promotes prototype/conform.py: the Shaper (MONOTONE_CHARACTERS cluster level; names via TTFont, never HarfBuzz's truncating API), the exhaustive length-1..horizon enumeration per settlement configuration (the per-edit belt, horizon 4 by default), split-buffer equivalence, gap-0 pen positions, and the font-vs-settle oracle diff, which takes no ledger: any divergence is a compiler defect by definition. The isolated-overlay configuration (ss10, `OVERLAY_CONFIGS`) has no settlement to compare against and takes a shorter arm of its own at `OVERLAY_HORIZON`: read-back proves per build that the pre-empt covers every letter cmap glyph and that no twin sits in any formation sequence, marker line, chokepoint class or settlement input, so the expected rendering of any text is per-letter twins at their `hmtx` advances with nothing formed and nothing attached, and one letter (each maps to its twin) plus every pair (no pair forms, joins or moves) is the whole of what HarfBuzz can still be asked. Coverage is deliberately not this sweep's job: read-back (rebuild/pipeline/readback.py) proves per build that the compiled font holds every emitted rule at its planned position, and the dead-rule alarm is split between the crate's fold, which refuses at table-build time any rule no replayed row first-matches (`fold::assert_outcome_partition`), and the build's witness stage (`check_rule_certificates`, run by `run_m1` over the certificates the crate wrote beside the rules), which keeps the realizability half — a string that fires the rule, settled rather than searched for. Enumeration completeness — whether a live raw window a string reaches is one the fixpoint enumerated with its pins satisfied, or one it left at `#NA` or never reached so the font answers it with a wildcard or a default rule — is the crate's `replay-strings` verb's (`rebuild/kernel-rs/src/replay.rs`, `run_m1.run_replay_strings`): `_SettledWindowWalk` and `_first_matching_rule` transcribed over the persisted rules instead of the font, run on every build at `run_m1.REPLAY_HORIZON`, whole-universe on a code or structure change and only over the texts naming an edited family on a rune edit. So the sweep's remaining unique charter is what only shaping the real binary can test — HarfBuzz's application semantics (lookup interaction across features, backtrack-sees-settled across subtable breaks, default-ignorable skipping, class matching, Extension indirection) and the sufficiency of the 6-slot window abstraction itself, which witness-constructed strings structurally cannot probe because witnesses are built from that abstraction. The deep form of the same sweep runs at horizon 5 or deeper on demand (`make conform-deep`, rebuild/tools/deep_sweep.py), armed by the behavior-class enumeration `emit_gsub.behavior_classes` plus the font-compilation code and the uharfbuzz version, so a rune edit that introduces no novel rule shape never stales it. The split-buffer check rides the belt itself, on the texts it can say anything about, which is where the standalone horizon-5 boundary gate's charter now lives: proven per build at the belt's horizon and periodically deeper by `make conform-deep`. The ZWNJ slot's own structure — zero advance, no ink — is read-back's static boundary-glyphs stage now, proven off the font bytes once per build rather than at every shaped slot. Settlement rides `_SettledWindowWalk`'s per-config window memo, so a distinct raw window costs one batched crate answer and every recurrence across the sweep's texts costs a dict probe; the oracle's rows are these same texts, and the two phases share that memo through one file per configuration under rebuild/out/m1 (`SettleMemoFile`), keyed per family the way the oracle row cache is, so a window either of them settles is settled once per configuration until a rune it names moves.

The section 6 oracle gate itself lives in rebuild/pipeline/oracle.py (`compare_against_baseline`, the ledger classifier, the position channel), which is the comparison side the enumeration's stamp leaves out. What stays here is its producer: `_compare_row` compares one baseline row's ligation (clusters), per-seam classification, and cell identity against the settled stream through the hand-written alias map and answers the `DivergentRow` the oracle classifies; `_cached_verdict` and `_served_verdict` are the codec between that answer and the oracle row cache's record, and `_verify_served_sample` re-derives a pass's served sample against the store. Those, with the walk, are the two entry points `oracle_cache.ORACLE_ROW_CODE_PATHS` is cut from, which is why they and not the classifier live in this file.

Settlement itself is the crate's, reached through `kernel_exec`: `_SettledWindowWalk` batches whole waves of distinct raw windows into `kernel_exec.settle_windows`, the certificate check and the sweep each hoist one `kernel_exec.guard_sweep` and thread its verdict surface through every formation call below it, and nothing here re-derives a settled cell. The lazy `table` imports inside the entry points that read a decision table no longer keep it out of the shaping half — importing this module imports `kernel_exec`, which imports `table` — and what they still buy is locality: each entry point names the label constants it reads where it reads them.
"""

from __future__ import annotations

import functools
import gzip
import itertools
import json
import operator
import os
import pickle
import sys
import time
from array import array
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, Sequence

import yaml

from rebuild.pipeline import geometry, kernel_exec, oracle_cache, settle
from rebuild.pipeline.model import (
    CellId,
    GlyphRecord,
    ResolvedSpec,
    Settled,
    feature_config_token,
    isolated_overlay_active,
    marker_glyph_name,
    relevant_marker_features,
    ss10_twin_name,
)
from rebuild.validation.rowmodel import CONFIGS, Row, format_codepoints, iter_rows

if TYPE_CHECKING:
    from rebuild.pipeline.emit_gsub import _FoldedRule
    from rebuild.pipeline.table import Rule

ZWNJ = "\u200c"
ZWNJ_SENTINEL = "<zwnj>"
BOUNDARY_GLYPH_NAMES = {"space", "uni200C", "periodcentered", "periodcentered.lowered"}
# The configurations letters settle under: one settlement table, treaty table, window enumeration, settle memo and rule-witness arm each, enumerated by the kernel one process apiece.
SETTLEMENT_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05")
# The isolated-overlay taste configurations: no table, because nothing settles under them (`model.isolated_overlay_active`); swept at `OVERLAY_HORIZON` behind read-back's isolation proof and oracled against the bare stream. `rebuild/test_conform.py` holds this roster to the registry's `overlay: isolated` features.
OVERLAY_CONFIGS = ("ss10",)
# Every configuration the font is accepted under: what the belt shapes, the oracle compares and stores rows for, the Manual pins replay against and the review surface lists.
ACCEPTANCE_CONFIGS = SETTLEMENT_CONFIGS + OVERLAY_CONFIGS
# How many of a belt bucket's texts one walk holds at a time. The bucket itself is streamed, never materialized: horizon 5's length-5 bucket is millions of texts, while a chunk's states cost tens of megabytes whatever the horizon.
TEXT_CHUNK = 65536
BELT_HORIZON = 4
# The overlay arm's horizon, whatever the belt's: one letter proves each cmap glyph maps to its twin, and every pair proves no pair forms, joins or moves, which with read-back's isolation proof is the whole claim.
OVERLAY_HORIZON = 2
SETTLE_MEMO_FORMAT = "ams-settle-memo/2"
# Windows per block of the settle memo file; each block is its own pickle, so a writer streams the memo out and a reader decodes it in with this many entries in flight rather than the whole memo twice over.
SETTLE_MEMO_BLOCK = 65536
_SETTLE_MEMO_READ_ERRORS = (
    OSError,
    EOFError,
    pickle.UnpicklingError,
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
)


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
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.divergences

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
            "pass": self.passed,
            "notes": self.notes,
        }
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

    def advance(self, glyph_name: str) -> int:
        """The glyph's `hmtx` advance, which is where a slot's pen moves when nothing positions it — the overlay arm's whole expectation for every slot."""
        return self.tt["hmtx"][glyph_name][0]

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


_BOUNDARY_KIND_LABELS = {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}


def isolated_overlay_labels(spec: ResolvedSpec, tokens: Sequence[settle.RightToken]) -> list[str]:
    """The glyph names an `overlay: isolated` taste set renders for raw tokens: every letter its anchor-free `.ss10` twin, every boundary token its own glyph. One name per raw token, because the pre-empt substitutes the twins before formation and no ligature ever forms — the 2026-07-04 ratification that join suppression also means ligation suppression."""
    return [
        ss10_twin_name(token.letter) if token.kind == "letter" else _BOUNDARY_KIND_LABELS[token.kind]
        for token in tokens
    ]


def isolated_overlay_tokens(spec: ResolvedSpec, text: str) -> list[settle.RightToken]:
    return settle.tokens_from_codepoints(spec, [ord(ch) for ch in text])


class IsolatedOverlayWalk:
    """The overlay configuration's stand-in for `_SettledWindowWalk`: the same `walk_many` shape over the same texts, answered from the registry alone — `settle.isolated_overlay_settled` for the stream, `isolated_overlay_labels` for the names — with no crate, no memo and nothing to save. It exists so the oracle's per-configuration compare hands both kinds of configuration the same loop."""

    single_settles = 0

    def __init__(self, spec: ResolvedSpec):
        self.spec = spec

    def walk_many(self, texts: Sequence[str]) -> list[tuple[list[Settled], list[str]]]:
        answers: list[tuple[list[Settled], list[str]]] = []
        for text in texts:
            tokens = isolated_overlay_tokens(self.spec, text)
            answers.append(
                (
                    settle.isolated_overlay_settled(self.spec, tokens),
                    isolated_overlay_labels(self.spec, tokens),
                )
            )
        return answers

    def walk(self, text: str) -> tuple[list[Settled], list[str]]:
        return self.walk_many([text])[0]

    def save_memo(self) -> bool:
        return False

    def memo_line(self, config: str, written: bool) -> str | None:
        return None


class IsolatedOverlayShaper:
    """What HarfBuzz answers under the overlay, computed instead of asked: every letter its twin, every boundary character its glyph, each slot at zero offset with its `hmtx` advance. It is the position channel's shaper for the overlay configuration, which the belt's overlay arm licenses — that arm holds every shaped slot of every text up to `OVERLAY_HORIZON` to exactly this shape, and cursive attachment is pairwise, so a glyph no pair moves is moved by no text. The namer dot is the one slot the overlay does not name outright: the font lowers it before a Short twin, so both dot glyphs must carry one advance for the pen to be a function of the text alone, and the constructor refuses a font where they differ."""

    def __init__(self, font_path: Path, spec: ResolvedSpec):
        from fontTools.ttLib import TTFont

        self.spec = spec
        self.font_path = Path(font_path)
        self.tt = TTFont(str(font_path))
        self._advances = {name: metrics[0] for name, metrics in self.tt["hmtx"].metrics.items()}
        dot, lowered = "periodcentered", "periodcentered.lowered"
        if lowered in self._advances and self._advances[lowered] != self._advances.get(dot):
            raise ValueError(
                f"{font_path}: {dot} advances {self._advances.get(dot)} but {lowered} advances {self._advances[lowered]}, so the overlay's pen positions are not a function of the text alone"
            )

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        labels = isolated_overlay_labels(self.spec, isolated_overlay_tokens(self.spec, text))
        return [
            {
                "name": name,
                "gid": self.tt.getGlyphID(name),
                "cluster": cluster,
                "x_advance": self._advances[name],
                "x_offset": 0,
                "y_offset": 0,
            }
            for cluster, name in enumerate(labels)
        ]


def check_isolated_positions(text, config, shaper: Shaper, shaped, divergences) -> None:
    """Every shaped slot at zero offset with its glyph's `hmtx` advance, which is what a font that attaches nothing under the overlay must answer; a ZWNJ slot, which HarfBuzz hides behind the space glyph at zero advance, is held to zero. The check is what licenses `IsolatedOverlayShaper` to stand in for HarfBuzz on the oracle's side."""
    hidden = zwnj_slots(text, shaped)
    for index, glyph in enumerate(shaped):
        want = (0, 0, 0 if index in hidden else shaper.advance(glyph["name"]))
        got = (glyph["x_offset"], glyph["y_offset"], glyph["x_advance"])
        if got != want:
            divergences.append(
                Divergence(
                    text,
                    config,
                    index,
                    f"offset (0, 0) advance {want[2]} ({glyph['name']})",
                    f"offset ({got[0]}, {got[1]}) advance {got[2]}",
                    "overlay-position",
                )
            )
            return


def raw_labels(
    spec: ResolvedSpec, text: str, features: frozenset[str], guard_verdicts: settle.FormationGuard
) -> list[str]:
    """The raw GSUB pipeline replay: formation (delegated to settle.form_ligatures, so the section 5.7 late-formation guard applies here exactly as in the kernel and the emitted lookup), marker fold, ZWNJ chokepoint — the labels the settlement lookup sees. `guard_verdicts` is the crate's complete verdict surface for this spec (`kernel_exec.guard_sweep`), which the caller hoists once rather than sweeping per text."""
    by_codepoint = {
        info.codepoint: name for name, info in spec.registry.families.items() if info.codepoint is not None
    }
    boundary_by_codepoint = {token.codepoint: name for name, token in spec.registry.boundary_tokens.items()}
    tokens: list[settle.RightToken] = []
    for ch in text:
        cp = ord(ch)
        if cp in boundary_by_codepoint:
            tokens.append(settle.RightToken(boundary_by_codepoint[cp]))
        elif cp in by_codepoint:
            tokens.append(settle.RightToken("letter", by_codepoint[cp]))
        else:
            raise ValueError(f"U+{cp:04X} outside the spec alphabet")
    return formed_labels(spec, settle.form_ligatures(spec, tokens, guard_verdicts), features)


def formed_labels(spec: ResolvedSpec, formed: list[settle.RightToken], features: frozenset[str]) -> list[str]:
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


_WINDOW_BOUNDARIES = frozenset({"space", "uni200C", "periodcentered"})
_EDGE_LABEL = "#EDGE"
_NA_LABEL = "#NA"


def _window_rights(labels: list[str], index: int) -> tuple[str, str, str, str]:
    """The raw settlement window at `index`, out to the fourth slot: each slot is the next label along, `#EDGE` past the end of the buffer, and `#NA` the moment the slot before it is a boundary, the edge, or itself `#NA` — the standing convention that no record peeks past a boundary. The table's deep-slot structure plays no part here, and needs none: a rule that dropped a slot matches any token at it (`_first_matching_rule`), so a raw token standing at a slot the enumeration never split matches exactly the slot-dropped rule HarfBuzz would match. Keying the settle memo this finely is sound with no relevance oracle at all, because one window's settlement is a function of exactly these slots and nothing beyond them — the crate reads the four raw slots a case row carries and no more — and it is also the faster of the two, measured on the live alphabet at the belt's horizon: the probes that decided which slots to blank cost far more than the answers the blanking saved. Shared by `_matched_windows` and `_SettledWindowWalk` so the replay and the memo key read one window."""
    right1 = labels[index + 1] if index + 1 < len(labels) else _EDGE_LABEL
    right2 = (
        _NA_LABEL
        if right1 in _WINDOW_BOUNDARIES or right1 == _EDGE_LABEL
        else (labels[index + 2] if index + 2 < len(labels) else _EDGE_LABEL)
    )
    right3 = (
        _NA_LABEL
        if right2 in _WINDOW_BOUNDARIES or right2 in (_EDGE_LABEL, _NA_LABEL)
        else (labels[index + 3] if index + 3 < len(labels) else _EDGE_LABEL)
    )
    right4 = (
        _NA_LABEL
        if right3 in _WINDOW_BOUNDARIES or right3 in (_EDGE_LABEL, _NA_LABEL)
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


def _matched_windows(spec, text, features, guard_verdicts, expected, rules_by_input, deep_index=None):
    """Replay the settlement lookup's view of one string: yield (position, window key, first-matching rule index or None) per letter slot, with labels and rules in the config's renamed (marker-folded) space and the left slot read from the settled stream — the exact first-match-wins semantics the emitted FEA compiles to. The window slots are the raw ones `_window_rights` reads; nothing here consults which slots the table chose to split, because a rule that dropped one matches whatever stands at it. `deep_index` is the table's `_DeepTokenIndex`; token resolution is a separate step strictly after `_window_rights`, which reads raw labels, and needs the settled left this loop holds — with no index the deep slots stay raw labels, which on a class-grain table realize no row."""
    try:
        labels = raw_labels(spec, text, features, guard_verdicts)
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
        right1, right2, right3, right4 = _window_rights(labels, index)
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
    """The per-config transport of a table's deep-slot class tokens into the walk and the replay (issue 26). Two levels, because r4 fibers are per (base, r3 token), never per base alone: `{(renamed input, settled left, renamed r1, renamed r2) -> {renamed member label -> r3 token}}` and the same keyed one deeper on the resolved r3 token — the class id verbatim when the row's r3 is a class, otherwise the bare r3 in renamed space, because that is exactly what `resolve`'s r3 step hands back for each shape (a class token never renames; a bare label reaches `resolve` already marker-folded). Built once per config from `decision.transitions` + `decision.deep_classes` + the rename map; `resolve` runs in the callers that hold the settled left, strictly after `_window_rights` has read the raw labels, so a class id never stands where a raw one is expected. A boundary label passes through, and a live-but-unindexed member falls back to the raw label, which then matches no row — today's exact behavior for a window the table lacks, which the enumeration's exactness precludes. `representatives` maps each class token to its renamed first member for the rule-membership tests, exact rather than heuristic because the build asserts every emitted look class holds a token's members all-in or all-out."""

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


_Window = tuple[str, str, str, str, str, str]
_Outcome = tuple[Settled, str, str]


@dataclass
class _WalkState:
    """One text mid-walk: its tokens and labels, the settled stream and names built so far, the resolved left, and the position the walk has reached. A state is either finished (`index` past the last token) or parked on a letter position whose window the memo does not yet hold."""

    text: str
    tokens: list[settle.RightToken]
    labels: list[str]
    settled: list[Settled]
    names: list[str]
    lefts: list[str]
    left: settle.LeftContext
    index: int = 0


@dataclass(frozen=True)
class _RefusedWindow:
    """A window the crate would not settle, memoized where its outcome would have gone. Only a walk built with `on_error="drop"` ever records one, and recording it is what keeps a tolerant prefill going past a window nothing may ever read; the walk that later reaches this key is where the refusal finally surfaces."""

    message: str


@dataclass(frozen=True)
class SettleMemoFile:
    """Where one configuration's settle memo lives between phases and what it must be keyed with to be read. The belt and the oracle each hold a walk over the same texts, so whichever runs first writes the file and the other loads it instead of settling. `stamp` is the whole-memo stamp (`oracle_cache.settle_memo_stamp`: the walk's code closure, the non-rune data, the resolved spec structure and capability-feature universe, the engine's settlement flags, the configuration) and `family_keys` the per-family rune keys (`oracle_cache.settle_family_keys`), on the oracle row cache's own two-grained argument: a window's settlement is a function of the rune files its six slots name — every ligature rune whose components all appear among them included — and of nothing another rune file holds, so a file that carries another stamp is treated as absent, and a file under the same stamp serves every entry naming no moved family and drops the rest."""

    path: Path
    stamp: str
    family_keys: Mapping[str, str] = field(default_factory=dict)


def settle_memo_files(
    out_dir: Path, spec: ResolvedSpec, inputs: oracle_cache.SettleMemoInputs | None
) -> dict[str, SettleMemoFile]:
    """One `SettleMemoFile` per settlement configuration under `out_dir` (the overlay configuration settles nothing and has none), keyed off `inputs` — the disk-derived half a caller snapshotted before loading `spec` — and off `spec` itself. Empty for a caller with no inputs, which shares nothing."""
    if inputs is None:
        return {}
    keys = oracle_cache.settle_family_keys(inputs, spec)
    return {
        config: SettleMemoFile(
            Path(out_dir) / f"settle-memo-{config}.gz",
            oracle_cache.settle_memo_stamp(inputs, spec, config, features_for_config(config)).value,
            keys,
        )
        for config in SETTLEMENT_CONFIGS
    }


class _SettledWindowWalk:
    """The memoized settle walk one conformance config runs over every swept text: a left-to-right pass computes each letter slot's raw window key — exactly `_matched_windows`' slots, with the left read from the just-settled stream — and resolves it through `windows`, a window -> (Settled, glyph name, left label) memo; only a miss reaches the crate. The memo is a pure speed device and nothing else: it records no coverage, and the sweep's verdict is the same whether every window misses or every window hits. Sound because every memoized outcome is a pure function of the window as keyed: the left label is the settled cell's display name (`geometry.display_name`, injective over every CellId field), and the right slots are the raw tokens a case row carries, all of them and none beyond. The key never reads the glyph inventory: a walk with minted names and a walk with none key alike and differ only in the name each hands back, which is what lets the oracle and the belt share one memo file. That last point about the right slots is why the walk needs no liveness oracle at all: blanking the deep slots wherever the table's relevance filters prove nothing could read them costs more in probes than the blanking saves. `windows` is deliberately unbounded; the interned labels plus deduplicated outcome tuples keep the residual cost to the key tuples themselves. The walk-equivalence sweeps in rebuild/test_conform.py are the standing alarm on all of it.

    `memo` names the file this walk shares with the other phase's walk over the same texts. It is read lazily, on the first wave that would otherwise reach the crate, so a walk that settles nothing — an oracle pass whose rows are all served — never pays to decode it; and `save_memo` writes it back only when this walk settled at least one window the file did not hold, so the second phase over a complete file rewrites nothing. The file is a gzip stream of pickles: a header carrying the format, the stamp and the per-family keys, then blocks of `SETTLE_MEMO_BLOCK` windows, each block the labels and outcomes it introduces plus its keys as columns of indexes into them. Writer and reader both work one block at a time — the memo is never in memory twice — and the outcome objects are shared with the memo dict itself, so a loaded memo costs what the same windows would have cost to settle: the key tuples and nothing else. A family whose key moved since the file was written retires every entry whose window names it (`oracle_cache.StaleMask` at label grain, the ligature clause included), and the retirement is priced per block over the label columns rather than per key: a bit per label, six column folds in C, one comprehension over the masks.

    Loaded entries sit in `_cold` until a walk first reaches them, and move into `windows` on that first hit, so the two dicts together are the memo and their split is what this walk has touched. That split is what `save_memo(prune=True)` writes on: the belt walks the whole universe every pass, so an entry it never reached is a window no text produces any more — its left slot named a settlement an edit has since moved — and carrying it forward would grow the file by a slice per rune edit forever. The oracle prunes nothing, since a served row is a window it never reached.

    Batching is what makes the crate affordable here. `settle-cases` answers independent windows, but a text's next left is the previous window's answer, so `_run` advances a whole pile of texts in waves: every state runs forward to its first memo miss, the misses contribute one case row each — deduplicated by memo key, since a key that two states reach in the same wave is one question — and one `kernel_exec.settle_windows` invocation answers up to `batch` of them before every state advances again. A wave collects at most `batch` new keys and parks the rest for the next one, so a caller's chunk size bounds its own resident cost rather than the invocation's. `walk` is the same loop over a single text, which means a miss there spends a whole kernel spawn on one window; `single_settles` counts those, so a caller that forgot to `prefill` can see what it is paying.

    A refusal is the one thing the memo can hold that is not an outcome. `on_error="raise"`, the default, lets it out of the batch that met it, as every settlement caller has always done. `on_error="drop"` splits the timing instead: a refusal met during `prefill` is memoized as a `_RefusedWindow`, the text carrying it stops advancing, and the rest of the pile finishes — while `walk` and `walk_many` raise `settle.SettleError` the moment they reach such a key. That pairing is what lets a caller prefill a pile of strings and report each refusal against the string that carried it (the certificate check reads every certificate and names the rule whose certificate the crate refused) without one refusal aborting the whole pile.

    `audit_dedupe` is the standing argument for the dedupe made checkable: with it on, every distinct raw case row a memo key carries beyond the representative is settled too and asserted equal to the memoized outcome, which is the claim `_window_rights`' `#NA` cascade makes — that two raw windows keyed alike settle alike.
    """

    def __init__(
        self,
        spec: ResolvedSpec,
        features: frozenset[str],
        glyph_names: Mapping[CellId, str],
        guard_verdicts: settle.FormationGuard,
        *,
        batch: int = kernel_exec.SETTLE_WINDOW_BATCH,
        audit_dedupe: bool = False,
        on_error: str = "raise",
        memo: SettleMemoFile | None = None,
    ):
        self.spec = spec
        self.features = features
        self.glyph_names = glyph_names
        self.guard_verdicts = guard_verdicts
        self.batch = max(1, batch)
        self.audit_dedupe = audit_dedupe
        self.on_error = on_error
        self.memo = memo
        self.windows: dict[_Window, _Outcome | _RefusedWindow] = {}
        self._cold: dict[_Window, _Outcome] = {}
        self.single_settles = 0
        self.audit_extra_rows = 0
        self.audit_multi_keys: set[_Window] = set()
        self.memo_windows = 0
        self.stale_windows = 0
        self.pruned_windows = 0
        self.fresh_windows = 0
        self.memo_seconds = 0.0
        self._memo_loaded = False
        self._refused = 0
        self._outcomes: dict[Settled, _Outcome] = {}
        self._settle_calls = 0
        self._audit_seen: set[tuple[settle.LeftContext, settle.RightToken, tuple[settle.RightToken, ...]]] = (
            set()
        )
        self._audit_pending: list[tuple[_Window, dict]] = []

    def walk(self, text: str) -> tuple[list[Settled], list[str]]:
        """Settle one text through the memo. Returns (settled items, their glyph names). Every miss along the way is its own kernel invocation, counted in `single_settles` — `prefill` is what a caller with a pile of texts reaches for instead."""
        before = self._settle_calls
        settled, names = self._run([text], collect=True)[0]
        self.single_settles += self._settle_calls - before
        return settled, names

    def walk_many(self, texts: Sequence[str]) -> list[tuple[list[Settled], list[str]]]:
        """Settle a whole chunk of texts in waves, answering one (settled, names) pair per text in the order asked."""
        return self._run(texts, collect=True)

    def prefill(self, texts: Sequence[str]) -> None:
        """Fill the memo from a pile of texts and keep nothing else, so a caller that will walk them one at a time later pays waves rather than spawns. Under `on_error="drop"` this is the tolerant half of the pair: a window the crate refuses is memoized as a refusal and the text carrying it simply stops advancing, so the prefill finishes and the refusal waits for a `walk` that reaches it."""
        self._run(texts, collect=False)

    def _state(self, text: str) -> _WalkState:
        spec = self.spec
        tokens = settle.form_ligatures(
            spec,
            settle.tokens_from_codepoints(spec, [ord(ch) for ch in text]),
            self.guard_verdicts,
        )
        return _WalkState(
            text=text,
            tokens=tokens,
            labels=formed_labels(spec, tokens, self.features),
            settled=[],
            names=[],
            lefts=[],
            left=settle.LeftContext("edge"),
        )

    def _window(self, state: _WalkState) -> _Window:
        labels, index = state.labels, state.index
        if index == 0:
            left = _EDGE_LABEL
        elif labels[index - 1] in _WINDOW_BOUNDARIES:
            left = labels[index - 1]
        else:
            left = state.lefts[index - 1]
        return (labels[index], left, *_window_rights(labels, index))

    def _rights(self, state: _WalkState) -> tuple[settle.RightToken, ...]:
        tokens, index = state.tokens, state.index
        return tuple(
            tokens[slot] if slot < len(tokens) else settle.EDGE for slot in range(index + 1, index + 5)
        )

    def _commit(self, state: _WalkState, outcome: _Outcome) -> None:
        item, name, left = outcome
        state.settled.append(item)
        state.names.append(name)
        state.lefts.append(left)
        state.left = settle.LeftContext("letter", item)
        state.index += 1

    def _advance(self, state: _WalkState, tolerant: bool = False) -> bool:
        """Run one state forward until it needs an answer this walk does not have. Boundary positions settle to their model constant here and never reach the kernel. True means the state is parked on a memo miss. A memoized refusal raises unless `tolerant`, in which case the state stops where it stands and its partial stream is discarded with it."""
        while state.index < len(state.tokens):
            token = state.tokens[state.index]
            if token.kind != "letter":
                state.settled.append(settle.boundary_settled(token.kind))
                state.names.append(_BOUNDARY_KIND_LABELS[token.kind])
                state.lefts.append(_BOUNDARY_KIND_LABELS[token.kind])
                state.left = settle.LeftContext(token.kind)
                state.index += 1
                continue
            window = self._window(state)
            outcome = self.windows.get(window)
            if outcome is None:
                cold = self._cold
                if cold:
                    outcome = cold.pop(window, None)
                    if outcome is not None:
                        self.windows[window] = outcome
                if outcome is None:
                    return True
            if isinstance(outcome, _RefusedWindow):
                if tolerant:
                    return False
                raise settle.SettleError(outcome.message)
            if self.audit_dedupe:
                self._note_raw(window, state)
            self._commit(state, outcome)
        return False

    def _record(self, window: _Window, item: Settled | None, text: str) -> None:
        self.fresh_windows += 1
        if item is None:
            self._refused += 1
            self.windows[window] = _RefusedWindow(
                f"the kernel refused the window {window!r}, reached in {text!r}"
            )
            return
        self.windows[window] = self._outcome(item)

    def _outcome(self, item: Settled) -> _Outcome:
        """The one memo value standing for `item` in this walk: the settled item, the name this walk's inventory gives it, and the display name every walk keys the next window's left slot on."""
        outcome = self._outcomes.get(item)
        if outcome is None:
            left = sys.intern(geometry.display_name(self.spec, item.cell))
            name = self.glyph_names.get(item.cell)
            outcome = (item, sys.intern(name) if name else left, left)
            self._outcomes[item] = outcome
        return outcome

    def _load_memo(self) -> None:
        """Read the shared memo file into `_cold`, once, on the first wave that would otherwise reach the crate. A file that is missing, carries another stamp, or will not decode loads nothing — or as many whole blocks as decoded before it broke, every one of which is a valid memo entry on its own — and the walk settles the rest as it always has. A file under this stamp whose family keys moved loads every block minus the entries naming a moved family, counted in `stale_windows`; a moved family the registry cannot place stales the whole file."""
        self._memo_loaded = True
        if self.memo is None:
            return
        started = time.perf_counter()
        labels: list[str] = []
        bits: list[int] = []
        outcomes: list[_Outcome] = []
        loaded = 0
        stale = 0
        try:
            with gzip.open(self.memo.path, "rb") as handle:
                header = pickle.load(handle)
                if (
                    not isinstance(header, dict)
                    or header.get("format") != SETTLE_MEMO_FORMAT
                    or header.get("stamp") != self.memo.stamp
                ):
                    return
                recorded = header.get("family_keys")
                if not isinstance(recorded, dict):
                    return
                mask = oracle_cache.StaleMask(
                    self.spec, oracle_cache.moved_families(recorded, self.memo.family_keys)
                )
                if mask.everything:
                    return
                retiring = bool(mask.moved)
                while True:
                    try:
                        new_labels, new_items, columns, values = pickle.load(handle)
                    except EOFError:
                        break
                    labels.extend(map(sys.intern, new_labels))
                    if retiring:
                        bits.extend(mask.bit_of(_label_family(label)) for label in new_labels)
                    outcomes.extend(self._outcome(item) for item in new_items)
                    entries = zip(
                        zip(*(map(labels.__getitem__, column) for column in columns)),
                        map(outcomes.__getitem__, values),
                    )
                    if retiring:
                        masks = functools.reduce(
                            lambda left, right: map(operator.or_, left, right),
                            (map(bits.__getitem__, column) for column in columns),
                        )
                        keep = [not mask.stale(window_mask) for window_mask in masks]
                        stale += len(keep) - sum(keep)
                        entries = itertools.compress(entries, keep)
                    self._cold.update(entries)
                    loaded += len(values)
        except FileNotFoundError:
            return
        except _SETTLE_MEMO_READ_ERRORS as error:
            print(
                f"[warn] settle memo: {self.memo.path} stopped reading after {loaded} windows ({error}); the rest are settled again",
                file=sys.stderr,
                flush=True,
            )
        finally:
            self.memo_windows = loaded
            self.stale_windows = stale
            self.memo_seconds += time.perf_counter() - started

    def save_memo(self, prune: bool = False) -> bool:
        """Write the memo to the shared file when this walk settled a window the file did not hold, replacing the file atomically so a reader in another process sees either the old file or the new one. Refusals are not outcomes and are not written; a walk that reaches one of those windows asks the crate again. `prune` drops the loaded entries this walk never reached instead of carrying them forward, counted in `pruned_windows`, and is only honest for a walk over the whole universe — the belt's. True when a file was written."""
        if self.memo is None:
            return False
        if prune:
            self.pruned_windows = len(self._cold)
        if not self.fresh_windows and not (prune and self._cold):
            return False
        started = time.perf_counter()
        path = self.memo.path
        staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        items: Iterable[tuple[_Window, _Outcome | _RefusedWindow]] = self.windows.items()
        if self._refused:
            items = (entry for entry in items if not isinstance(entry[1], _RefusedWindow))
        if not prune:
            items = itertools.chain(items, self._cold.items())
        items = iter(items)
        label_index: dict[str, int] = {}
        outcome_index: dict[int, int] = {}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(staged, "wb", compresslevel=1) as handle:
                pickle.dump(
                    {
                        "format": SETTLE_MEMO_FORMAT,
                        "stamp": self.memo.stamp,
                        "family_keys": dict(self.memo.family_keys),
                    },
                    handle,
                    protocol=5,
                )
                while True:
                    rows = list(itertools.islice(items, SETTLE_MEMO_BLOCK))
                    if not rows:
                        break
                    keys, values = zip(*rows)
                    columns = list(zip(*keys))
                    new_labels: list[str] = []
                    for label in dict.fromkeys(itertools.chain.from_iterable(columns)):
                        if label not in label_index:
                            label_index[label] = len(label_index)
                            new_labels.append(label)
                    new_items: list[Settled] = []
                    for outcome in dict.fromkeys(values):
                        if id(outcome) not in outcome_index:
                            outcome_index[id(outcome)] = len(outcome_index)
                            new_items.append(outcome[0])
                    block = (
                        new_labels,
                        new_items,
                        [array("I", map(label_index.__getitem__, column)) for column in columns],
                        array("I", map(outcome_index.__getitem__, map(id, values))),
                    )
                    pickle.dump(block, handle, protocol=5)
            os.replace(staged, path)
        except OSError as error:
            with suppress(OSError):
                staged.unlink()
            print(f"[warn] settle memo: {path} not written ({error})", file=sys.stderr, flush=True)
            return False
        finally:
            self.memo_seconds += time.perf_counter() - started
        return True

    def memo_line(self, config: str, written: bool) -> str | None:
        """The `[t]` line a phase prints for its share of the memo file, or None for a walk that has none."""
        if self.memo is None:
            return None
        return f"[t] settle_memo {config} {self.memo_seconds:.2f}s loaded={self.memo_windows} stale={self.stale_windows} fresh={self.fresh_windows} pruned={self.pruned_windows} written={'yes' if written else 'no'}"

    def _settle(self, cases: list[dict]) -> list[Settled | None]:
        self._settle_calls += 1
        return kernel_exec.settle_windows(
            self.spec, cases, self.features, batch=self.batch, on_error=self.on_error
        )

    def _note_raw(self, window: _Window, state: _WalkState) -> None:
        """Queue a raw case row this memo key has not been asked under before. The first such row per key is the representative the wave already asked; every later one is a distinct question the dedupe claims has the same answer, and `_drain_audit` is where that claim is settled."""
        raw = (state.left, state.tokens[state.index], self._rights(state))
        if raw in self._audit_seen:
            return
        self._audit_seen.add(raw)
        self.audit_multi_keys.add(window)
        self._audit_pending.append((window, kernel_exec.case_row(*raw)))

    def _drain_audit(self) -> None:
        """Settle the raw case rows a memo key carries beyond its representative and hold each to the memoized outcome — the dedupe's own premise, checked rather than assumed."""
        pending, self._audit_pending = self._audit_pending, []
        self.audit_extra_rows += len(pending)
        for start in range(0, len(pending), self.batch):
            chunk = pending[start : start + self.batch]
            for (window, _case), item in zip(chunk, self._settle([case for _window, case in chunk])):
                memoized = self.windows[window]
                assert not isinstance(memoized, _RefusedWindow), (window, memoized)
                assert item == memoized[0], (window, item, memoized[0])

    def _run(self, texts: Sequence[str], collect: bool) -> list[tuple[list[Settled], list[str]]]:
        tolerant = self.on_error == "drop" and not collect
        states = [self._state(text) for text in texts]
        pending = [state for state in states if self._advance(state, tolerant)]
        if pending and not self._memo_loaded:
            self._load_memo()
            pending = [state for state in pending if self._advance(state, tolerant)]
        while pending:
            keys: list[_Window] = []
            reached_in: list[str] = []
            cases: list[dict] = []
            asked: set[_Window] = set()
            for state in pending:
                window = self._window(state)
                if window in asked:
                    if self.audit_dedupe:
                        self._note_raw(window, state)
                    continue
                if len(cases) >= self.batch:
                    continue
                asked.add(window)
                keys.append(window)
                reached_in.append(state.text)
                cases.append(kernel_exec.case_row(state.left, state.tokens[state.index], self._rights(state)))
                if self.audit_dedupe:
                    self._audit_seen.add((state.left, state.tokens[state.index], self._rights(state)))
            for window, text, item in zip(keys, reached_in, self._settle(cases)):
                self._record(window, item, text)
            pending = [state for state in pending if self._advance(state, tolerant)]
            if self._audit_pending:
                self._drain_audit()
        if self._audit_pending:
            self._drain_audit()
        return [(state.settled, state.names) for state in states] if collect else []


def _token_text(spec: ResolvedSpec, tokens: Iterable[str]) -> str:
    """Render a certificate's token stream (rune family, ligature-rune, or boundary-label tokens) back to codepoints; ligature runes expand to their component sequence, so raw_labels' greedy formation re-folds them to the intended labels."""
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


class WitnessError(Exception):
    """A settlement rule whose certificate does not realize it: the build's own realizing string, settled through the crate, fires some other rule or none at the input it names, which means either the fold's pins or the fold's ordering is wrong."""


@dataclass
class WitnessReport:
    """One configuration's certificate check: how many rules the table carries, the certificate text each verified rule fired in, one sentence per rule whose certificate did not fire it, and how the walk's windows were paid for — `served` off the shared settle memo, `fresh` settled by the crate for this check."""

    config: str
    rules: int
    witnessed: dict[int, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    served: int = 0
    fresh: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures and len(self.witnessed) == self.rules


def check_rule_certificates(
    spec, features, decision, guard_verdicts=None, memo: SettleMemoFile | None = None
) -> WitnessReport:
    """The realizability half of rule coverage, settled rather than searched: every rule the table carries arrived with a certificate — the token stream the crate closed off the shortest producer chain of a row the rule first-matches (`certificate.rs`) — and this settles each certificate's text through the crate and asserts the rule first-matches at some position of it, under the exact first-match-wins the emitted lookup compiles to (`_matched_windows`). The sibling claim, that no rule sits behind another and can never win a window, is the crate's fold: `fold::assert_outcome_partition` refuses a table with a never-first rule before any certificate is closed.

    What the settle proves is the pins. A certificate's prefix is the chain of rows whose outcomes put the rule's left state in place, and the fixpoint only ever pinned those rows' slots — a settled left is reachable alongside the right1 that was the producing window's right2, and the deeper slots ride the allowed-sets. Settling the text from the run edge re-derives that left from nothing, so a pin the worklist got wrong settles the certificate to some other left, which first-matches some other rule, and the rule is reported. A table whose certificates do not cover its rules — a count that differs from the rule count — fails every rule, since nothing vouches for them.

    O(rules) settles and no search: the texts prefill one `_SettledWindowWalk` in waves, and `memo` is the configuration's shared settle memo (`settle_memo_files`), keyed per family the way the oracle row cache is, so a window the belt or the oracle has already settled since the runes it names last moved costs a dict probe and the windows this check settles are handed on to them. That key is where the window-locality theorem reaches the certificates: a certificate names a handful of families, its windows survive exactly as long as those families' keys do, and a rune edit re-settles only the certificates naming an edited family.
    """
    if guard_verdicts is None:
        guard_verdicts = kernel_exec.guard_sweep(spec)
    features = frozenset(features)
    report = WitnessReport(config=decision.config, rules=len(decision.rules))
    certificates = tuple(getattr(decision, "certificates", ()))
    if len(certificates) != len(decision.rules):
        report.failures.append(
            f"{decision.config}: the table carries {len(certificates)} certificate(s) for {len(decision.rules)} rule(s), so nothing vouches for its rules — a build-tables run that folded the rules writes one certificate per rule beside them"
        )
        return report
    glyph_names = {cell: settle.cell_label(spec, cell) for cell in decision.reachable_cells()}
    rules_by_input = _renamed_rules_by_input(spec, features, decision)
    walker = _SettledWindowWalk(spec, features, glyph_names, guard_verdicts, on_error="drop", memo=memo)
    texts: list[str | None] = []
    for index, tokens in enumerate(certificates):
        try:
            texts.append(_token_text(spec, tokens))
        except (KeyError, ValueError) as error:
            texts.append(None)
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): its certificate {list(tokens)} does not render as text ({error})"
            )
    with suppress(settle.SettleError):
        walker.prefill(sorted({text for text in texts if text}))
    for index, text in enumerate(texts):
        if text is None:
            continue
        try:
            _settled, names = walker.walk(text)
        except settle.SettleError as error:
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): the crate refused its certificate {text!r} ({error})"
            )
            continue
        fired = [
            matched
            for _position, _window, matched in _matched_windows(
                spec, text, features, guard_verdicts, names, rules_by_input
            )
        ]
        if index in fired:
            report.witnessed[index] = text
        else:
            report.failures.append(
                f"{decision.config} rule {index} ({rule_signature(decision.rules[index])}): its certificate {text!r} fires rules {fired} and never this one"
            )
    if memo is not None:
        walker.save_memo()
    report.served = walker.memo_windows
    report.fresh = walker.fresh_windows
    return report


def run_conformance(
    font_path: Path,
    spec: ResolvedSpec,
    configs: Iterable[str] = ACCEPTANCE_CONFIGS,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    max_length: int = 4,
    out_dir: Path | None = None,
    summary_name: str = "conform_summary.json",
    settle_memos: Mapping[str, SettleMemoFile] | None = None,
) -> ConformReport:
    """The serial conformance entry point: one shared Shaper, each config's belt run in turn through `_conformance_config`, results merged by `merge_conformance_results`. The per-config fan-out lives in run_m1.run_font_conformance, which submits `conformance_config_worker` per config instead. No decision table reaches this sweep at all — it shapes the font and settles the same texts through the kernel, and read-back owns the claim that the font holds the planned rules. `summary_name` is the file written under `out_dir`, which the deep sweep names differently so its own run never overwrites the belt's record. `settle_memos` names each config's shared settle memo file; a config with none walks from scratch."""
    shaper = Shaper(Path(font_path))
    alphabet = spec_alphabet(spec)
    splitters = splitting_boundary_chars(spec)
    glyph_names = {cell: record.name for cell, record in (glyphs or {}).items()}
    glyphs_by_name = {record.name: record for record in (glyphs or {}).values()}
    anchors_of = anchors_in_font_units(glyphs_by_name) if glyphs else None
    guard_verdicts = kernel_exec.guard_sweep(spec)

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
            guard_verdicts,
            settle_memo=(settle_memos or {}).get(config),
        )
        for config in configs
    ]
    report = merge_conformance_results(Path(font_path), results)
    if out_dir is not None:
        report.write(Path(out_dir) / summary_name)
    return report


@dataclass
class ConformanceConfigResult:
    config: str
    sequences: int = 0
    shaping_runs: int = 0
    divergences: list[Divergence] = field(default_factory=list)
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
    guard_verdicts: settle.FormationGuard | None = None,
    settle_memo: SettleMemoFile | None = None,
) -> ConformanceConfigResult:
    """One config's belt run: every string of length 1..max_length over the alphabet, shaped against the font and diffed against the settled stream, with split-buffer equivalence and gap-0 pen positions riding along. Configs share nothing, so this is the unit both the serial wrapper and the process-pool worker call. An overlay config takes the overlay arm instead, whatever `max_length` says: every string of length 1..`OVERLAY_HORIZON`, its expected names `isolated_overlay_labels` over the raw tokens, no walk and no memo, and every slot held to zero offset at its `hmtx` advance (`check_isolated_positions`) — the split-buffer check rides it as it rides the belt. Settlement rides `_SettledWindowWalk`'s per-config memo, which is a speed device only — the sweep's verdict does not depend on which windows it has already seen — and `settle_memo` is where that memo is shared with the oracle's walk over the same texts: loaded on the first miss, written back at the end when this sweep settled anything the file lacked, pruned of every entry no text reached — this sweep walks the whole universe, so it is the one walk that can say which windows still exist. Each length's texts are streamed through the walk `TEXT_CHUNK` at a time rather than enumerated whole, because a bucket at any interesting horizon is millions of strings and only the chunk in flight need be resident; the swept order is the product's own either way. The one structural check runs here on the texts it can say anything about — a splitter-free text is trivially identical to its own single segment — which is the whole of its coverage now that the standalone horizon-5 boundary pass has gone; the deep sweep takes it past this horizon on its own arming key. The ZWNJ slot's own structure — zero advance, no ink — is read-back's static boundary-glyphs stage, proven off the font bytes once per build."""
    features = features_for_config(config)
    result = ConformanceConfigResult(config=config)
    modes: set[str] = set()
    if isolated_overlay_active(spec, features):
        for length in range(1, OVERLAY_HORIZON + 1):
            for combo in itertools.product(alphabet, repeat=length):
                text = "".join(combo)
                result.sequences += 1
                shaped = shaper.shape(text, features)
                result.shaping_runs += 1
                if set(text) & splitters:
                    check_split_buffer(text, config, features, shaper, shaped, result.divergences, splitters)
                expected = isolated_overlay_labels(spec, isolated_overlay_tokens(spec, text))
                check_oracle(text, config, shaped, expected, result.divergences, modes)
                check_isolated_positions(text, config, shaper, shaped, result.divergences)
        result.modes = sorted(modes)
        return result

    if guard_verdicts is None:
        guard_verdicts = kernel_exec.guard_sweep(spec)
    walker = _SettledWindowWalk(spec, features, glyph_names, guard_verdicts, memo=settle_memo)

    def sweep_text(text: str, names: list[str]) -> None:
        shaped = shaper.shape(text, features)
        result.shaping_runs += 1
        if set(text) & splitters:
            check_split_buffer(text, config, features, shaper, shaped, result.divergences, splitters)
        check_oracle(text, config, shaped, names, result.divergences, modes)
        if anchors_of is not None:
            check_join_gaps(text, config, shaper, shaped, anchors_of, result.divergences)

    for length in range(1, max_length + 1):
        stream = itertools.product(alphabet, repeat=length)
        while True:
            chunk = ["".join(combo) for combo in itertools.islice(stream, TEXT_CHUNK)]
            if not chunk:
                break
            result.sequences += len(chunk)
            for text, (_settled, names) in zip(chunk, walker.walk_many(chunk)):
                sweep_text(text, names)

    memo_line = walker.memo_line(config, walker.save_memo(prune=True))
    if memo_line is not None:
        print(memo_line, file=sys.stderr, flush=True)
    result.modes = sorted(modes)
    return result


def conformance_config_worker(
    spec: ResolvedSpec,
    font_path: Path,
    config: str,
    max_length: int = 4,
    glyphs: Mapping[CellId, GlyphRecord] | None = None,
    guard_verdicts: settle.FormationGuard | None = None,
    settle_memo: SettleMemoFile | None = None,
) -> ConformanceConfigResult:
    """One config's sweep in its own process, everything it needs rebuilt here from the spec and the font. The section 5.7 verdict surface is one of those things: a fan-out hands each worker its own spec, so each sweeps once for itself unless the caller has one to pass down — a fifth of a second against a sweep that runs for a minute — and an overlay config, which forms nothing, never sweeps it. `settle_memo` rides the submission the same way; it is a path and a stamp, and the worker is where the file is read and written."""
    shaper = Shaper(Path(font_path))
    alphabet = spec_alphabet(spec)
    splitters = splitting_boundary_chars(spec)
    glyph_names = {cell: record.name for cell, record in (glyphs or {}).items()}
    glyphs_by_name = {record.name: record for record in (glyphs or {}).values()}
    anchors_of = anchors_in_font_units(glyphs_by_name) if glyphs else None
    if guard_verdicts is None and not isolated_overlay_active(spec, features_for_config(config)):
        guard_verdicts = kernel_exec.guard_sweep(spec)
    return _conformance_config(
        shaper,
        spec,
        config,
        alphabet,
        splitters,
        glyph_names,
        anchors_of,
        max_length,
        guard_verdicts,
        settle_memo=settle_memo,
    )


def merge_conformance_results(font_path: Path, results: Iterable[ConformanceConfigResult]) -> ConformReport:
    """Fold per-config results into one ConformReport. `sequences` comes from the first result — every settlement config sweeps the identical sequence set, and the overlay arm's shorter one is counted in the shaping runs — while the shaping runs sum and the divergences/notes concatenate in the caller's config order; the oracle modes are unioned and appended sorted, so the report is the same whichever config finished first."""
    report = ConformReport(font=str(font_path))
    results = list(results)
    report.sequences = results[0].sequences if results else 0
    modes: set[str] = set()
    for result in results:
        report.shaping_runs += result.shaping_runs
        report.divergences.extend(result.divergences)
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


def load_alias_map(path: Path) -> dict[str, CellId | str]:
    """rebuild/m1-aliases.yaml: old compiled glyph name -> CellId fields, or the literal strings "boundary" / "ignore" / "pending" (an acknowledged not-yet-authored entry: the completeness gate lets it through, but the comparison still treats the name as unaliased)."""
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


def _cached_verdict(divergent: DivergentRow | None) -> oracle_cache.CachedRow | None:
    """A fresh comparison's answer as the store holds it: the five fields the subset table cannot supply, and none of the provenance that would make two equal verdicts compare unequal."""
    if divergent is None:
        return None
    return oracle_cache.CachedRow(
        kinds=divergent.kinds,
        position=divergent.position,
        new_cells=divergent.new_cells,
        new_seams=divergent.new_seams,
        phenomena=divergent.phenomena,
    )


def _served_verdict(config: str, row: Row, cached: oracle_cache.CachedRow) -> DivergentRow:
    """A stored verdict back in the shape everything downstream reads, with `config` and the three baseline fields taken from the table the row was just streamed out of rather than from the store. Everything from `_match_ledger` on cannot tell this row from a freshly compared one, which is the byte-identity claim `rebuild/test_conform.py` pins."""
    return DivergentRow(
        config=config,
        codepoints=format_codepoints(row.codepoints),
        kinds=cached.kinds,
        position=cached.position,
        baseline_glyphs=tuple(row.glyphs),
        baseline_seams=tuple(row.seams),
        new_cells=cached.new_cells,
        new_seams=cached.new_seams,
        phenomena=cached.phenomena,
    )


def _verify_served_sample(
    spec: ResolvedSpec,
    aliases,
    config: str,
    features: frozenset[str],
    walker: "_SettledWindowWalk | IsolatedOverlayWalk",
    table_path: Path,
    store: "oracle_cache.RowStore",
    sample: "oracle_cache.VerificationSample",
) -> None:
    """Re-derive the pass's stratified sample of served rows and prove each against the record it was served from. Every family that served a row contributes rows here, so a family-wide poisoning — the shape a rune edited mid-run produces — is caught with probability one rather than with probability sample-over-served, and the seed carries the pass ordinal so the covered slice rotates instead of re-proving the same fraction of a percent every pass. The rows are re-read in a second streaming pass over the same table rather than held from the first: the draw is only final once the last row has been offered, and a couple of hundred rows are cheap to find again where tens of thousands of live `Row` objects would not be cheap to keep. A mismatch is a hard stop, not a miss — the store is describing verdicts this build does not produce, and `divergence-audit.tsv` is a fingerprinted artifact the surface build's manifest is stamped against."""
    wanted = set(sample.indexes())
    if not wanted:
        return
    picked = [(index, row) for index, row in enumerate(iter_rows(table_path)) if index in wanted]
    walked = walker.walk_many([row.text for _, row in picked])
    for (index, row), (settled, _names) in zip(picked, walked):
        fresh = _cached_verdict(_compare_row(spec, aliases, config, features, row, settled))
        recorded = store.serve(index, row.codepoints).row
        if fresh != recorded:
            raise SystemExit(
                f"the oracle row cache served a stale verdict for {config} {format_codepoints(row.codepoints)}: it holds {recorded}, and comparing the row again gives {fresh} — nothing this store holds can be trusted, so rerun with --fresh-oracle-cache and treat the difference as a staleness bug in the key"
            )


def _compare_row(
    spec,
    aliases,
    config: str,
    features: frozenset[str],
    row: Row,
    settled: Sequence[Settled],
) -> DivergentRow | None:
    """One baseline row against the settlement its text already produced. `settled` is that stream, handed in by the caller's walk rather than fetched here, so a row is settled exactly once; under the overlay configuration it is `IsolatedOverlayWalk`'s bare stream — every letter its default-stance cell with no seam, the alias map's bare-name denotation, one per raw token so a window whose pair formed in the old font diverges at ligation grain."""
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
            if alias is None or alias == "pending":
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
        if token == "en-ext-1":
            if index > 0 and "ex-ext-1" in old_glyphs[index - 1]:
                out.add("-en-ext-1:same-seam")
            else:
                out.add(f"-en-ext-1:{cell.rune}")
        elif token == "en-ext-2" and index > 0 and "ex-ext-2" in old_glyphs[index - 1]:
            out.add("-en-ext-2:same-seam")
        else:
            out.add(f"-{token}")
    if ".noentry" in old_glyphs[index]:
        out.add("old-noentry")
    return out


def _cell_token(cell, item) -> str:
    if cell is None:
        return getattr(item, "glyph_name", None) or str(item)
    return f"{cell.rune}/{cell.stance}/{cell.entry}/{cell.exit}/{'+'.join(cell.adjustments)}"
