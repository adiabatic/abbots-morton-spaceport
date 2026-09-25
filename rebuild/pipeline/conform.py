"""Conformance gates (M1-PLAN sections 5 and 6, Group 3): HarfBuzz against the settlement function, and the settlement function against the section 13.1 baseline oracle.

`run_conformance` runs the belt. For each settlement configuration it shapes every text of length 1 to the horizon (`BELT_HORIZON`, 4 by default) over the alphabet and compares the result with settlement: glyph names (`check_oracle`), split-buffer equivalence (`check_split_buffer`), and zero gaps at joins (`check_join_gaps`). This comparison uses no ledger, so any divergence is a compiler defect. `Shaper` shapes at the MONOTONE_CHARACTERS cluster level and reads glyph names through TTFont, because HarfBuzz's name API truncates them.

The isolated-overlay configuration (ss10, `OVERLAY_CONFIGS`) has no settlement to compare against. Read-back checks on every build that the ss10 pre-empt covers every letter cmap glyph and that no twin appears in any formation sequence, marker line, chokepoint class, or settlement input. So under ss10 every letter renders as its twin at its `hmtx` advance, and nothing forms or attaches. The belt checks this at `OVERLAY_HORIZON`: single letters show that each letter maps to its twin, and pairs show that no pair forms, joins, or moves.

The belt does not check rule coverage; other stages do. Read-back (rebuild/pipeline/readback.py) checks that the compiled font holds every emitted rule at its planned position. The crate's fold fails the table build on any rule that no replayed row first-matches (`fold::assert_outcome_partition`). The witness stage (`witness.check_rule_certificates`, run by `run_m1` over the certificates the crate writes beside the rules) settles a string that fires each rule. The crate's `replay-strings` subcommand (`rebuild/kernel-rs/src/replay.rs`, `run_m1.run_replay_strings`) checks enumeration completeness: whether each live raw window a string reaches is one the fixpoint enumerated with its pins satisfied, or one it left at `#NA` or never reached, which the font handles with a wildcard or default rule. It replays `_SettledWindowWalk` and `witness._first_matching_rule` over the persisted rules at `run_m1.REPLAY_HORIZON` on every build, over the whole universe after a code or structure change and, after a rune edit, over the texts naming an edited rune or a rune whose records read one (`run_m1.replay_families`).

What only the belt checks is what needs the real binary: HarfBuzz's application semantics (lookup interaction across features, backtrack reading settled glyphs across subtable breaks, default-ignorable skipping, class matching, Extension indirection) and whether the 6-slot window abstraction is sufficient. Certificates cannot test the second, because they are built from that abstraction. `make conform-deep` (rebuild/tools/deep_sweep.py) runs the same sweep, split-buffer check included, at horizon 5 or deeper on demand. It becomes due when `emit_gsub.behavior_classes`, the font-compilation code, or the uharfbuzz version changes, so a rune edit that adds no new rule shape does not make it due. Read-back's boundary-glyphs stage checks the ZWNJ glyph's zero advance and empty outline once per build, so the belt does not check them per shaped slot.

Settlement goes through `_SettledWindowWalk`'s per-configuration window memo: the crate settles each distinct raw window once, in a batch, and every recurrence is a lookup. The oracle's rows are the belt's texts, so the two share the memo through one file per configuration under rebuild/out/m1 (`SettleMemoFile`), keyed per family like the oracle row cache. The string replay fills that file on every whole-universe walk (`absorb_replay_memo`, from the window memo the `replay-strings` subcommand writes). So a window is settled once per configuration until a rune it names changes, and a cold pass does its settling in the replay instead of in the oracle.

The section 6 oracle gate is in rebuild/pipeline/oracle.py (`compare_against_baseline`, the ledger classifier) and rebuild/pipeline/oracle_positions.py (the position channel), which the enumeration's stamp leaves out. This module holds what they consume. `_compare_row` compares one baseline row's ligation (clusters), per-seam classification, and cell identity with the settled stream through the alias map, and returns the `DivergentRow` the oracle classifies. `_cached_verdict` and `_served_verdict` convert between that result and the oracle row cache's record, and `_verify_served_sample` re-derives a pass's sample of served rows and checks them against the store. `_compare_row` and the walk are the two entry points `oracle_cache.ORACLE_ROW_CODE_PATHS` is derived from, which is why they live here and the classifier does not.

The crate does all settlement, through `kernel_exec`. `_SettledWindowWalk` sends waves of distinct raw windows to `kernel_exec.settle_windows`. The certificate check (`witness.check_rule_certificates`) and the belt each call `kernel_exec.guard_sweep` once and pass its verdicts to every formation call below them. Nothing here re-derives a settled cell.
"""

from __future__ import annotations

import functools
import gzip
import itertools
import json
import mmap
import operator
import os
import pickle
import struct
import sys
import time
from array import array
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Callable, Iterable, Iterator, Literal, Mapping, Sequence, cast

from rebuild.pipeline import geometry, kernel_exec, oracle_cache, settle
from rebuild.pipeline.labels import (
    BOUNDARY_GLYPH_NAMES,
    _BOUNDARY_KIND_LABELS,
    features_for_config,
    formed_labels,
    spec_alphabet,
)
from rebuild.pipeline.model import (
    CellId,
    GlyphRecord,
    ResolvedSpec,
    Settled,
    feature_config_token,
    isolated_overlay_active,
    raw_rename_map,
    ss10_twin_name,
)
from rebuild.validation.rowmodel import Row, format_codepoints

ZWNJ = "\u200c"
ZWNJ_SENTINEL = "<zwnj>"
# The configurations letters settle under. Each has its own settlement table, treaty table, window enumeration, settle memo, and rule-witness run. One crate `build-tables` process enumerates them all, `default` first and the others as deltas over it.
SETTLEMENT_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05")
# The isolated-overlay taste configurations (`model.isolated_overlay_active`). Nothing settles under them, so they have no table. The belt sweeps them at `OVERLAY_HORIZON`, relying on read-back's isolation check, and the oracle compares them against the bare stream. `rebuild/test_conform.py` checks that this tuple matches the registry's `overlay: isolated` features.
OVERLAY_CONFIGS = ("ss10",)
# Every configuration the font is accepted under: what the belt shapes, the oracle compares and stores rows for, the Manual pins replay against, and the review surface lists.
ACCEPTANCE_CONFIGS = SETTLEMENT_CONFIGS + OVERLAY_CONFIGS
# How many texts of one length the belt walks at a time. Each length's texts are streamed instead of listed, because at horizon 5 the length-5 texts number in the millions, while one chunk's walk states cost tens of megabytes at any horizon.
TEXT_CHUNK = 65536
BELT_HORIZON = 4
# The overlay sweep's length, whatever the belt's horizon. Single letters show that each cmap glyph maps to its twin, and pairs show that no pair forms, joins, or moves. Together with read-back's isolation check, that covers every text.
OVERLAY_HORIZON = 2
SETTLE_MEMO_FORMAT = "ams-settle-memo/3"
SETTLE_MEMO_PART_FORMAT = "ams-settle-memo-part/1"
# Windows per block when a walk encodes its memo entries, for a part or for a whole-file save. Each part block is its own pickle, so a walk writes its fresh windows out, and an absorb reads them in, this many at a time instead of holding the whole part twice.
SETTLE_MEMO_BLOCK = 65536
_SETTLE_MEMO_READ_ERRORS = (
    OSError,
    struct.error,
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
        self._buffer = hb.Buffer()

    def _shaped(self, text: str, features: frozenset[str]):
        """Shape `text` into this shaper's one reused buffer and return it. `shape` and `positions` both read from here, so they see the same slots. Because the buffer is reused, a shaper must not be shared across threads, and each caller copies what it needs before the next call clears the buffer."""
        hb = self._hb
        buf = self._buffer
        buf.clear_contents()
        # MONOTONE_CHARACTERS keeps each input character in its own cluster, so the ZWNJ slot stays identifiable.
        buf.cluster_level = hb.BufferClusterLevel.MONOTONE_CHARACTERS
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {tag: True for tag in features})
        return buf

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        buf = self._shaped(text, features)
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

    def positions(self, text: str, features: frozenset[str]) -> list[tuple[int, int, int]]:
        """Each slot's `(x_offset, y_offset, x_advance)` from the same shaping `shape` performs. The position channel reads nothing else, and skipping the per-slot fontTools name lookup is what makes this cheaper than `shape`."""
        buf = self._shaped(text, features)
        return [(pos.x_offset, pos.y_offset, pos.x_advance) for pos in buf.glyph_positions]

    def advance(self, glyph_name: str) -> int:
        """The glyph's `hmtx` advance: how far the pen moves at a slot nothing positions, which is what the overlay sweep expects at every slot."""
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


def zwnj_slots(text: str, shaped: list[dict]) -> set[int]:
    return {
        index
        for index, glyph in enumerate(shaped)
        if glyph["cluster"] < len(text) and text[glyph["cluster"]] == ZWNJ
    }


def splitting_boundary_chars(spec: ResolvedSpec) -> frozenset[str]:
    """The characters of every boundary token with `splits_runs` set in the registry: space and ZWNJ. The namer dot does not split runs."""
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
    """The glyph name for each settled item. An item's own `glyph_name` is used first. Otherwise a boundary cell becomes its token glyph, and a rune cell is looked up in `glyph_names` or falls back to its display name."""
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
    """Check that the shaped buffer, with its splitter slots dropped, matches its splitter-separated segments shaped alone. Slots are compared on outline, advance, and offsets, not names, because the locked twins have the same bitmaps as the bare runes."""
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


def isolated_overlay_labels(spec: ResolvedSpec, tokens: Sequence[settle.RightToken]) -> list[str]:
    """The glyph names an `overlay: isolated` taste set renders for raw tokens: each letter's anchor-free `.ss10` twin, and each boundary token's own glyph. There is one name per raw token, because the pre-empt substitutes the twins before formation, so no ligature forms."""
    return [
        ss10_twin_name(token.letter) if token.kind == "letter" else _BOUNDARY_KIND_LABELS[token.kind]
        for token in tokens
    ]


def isolated_overlay_tokens(spec: ResolvedSpec, text: str) -> list[settle.RightToken]:
    return settle.tokens_from_codepoints(spec, [ord(ch) for ch in text])


class IsolatedOverlayWalk:
    """The overlay configuration's replacement for `_SettledWindowWalk`, with the same `walk_many` interface. It computes each text from the registry alone (`settle.isolated_overlay_settled` for the stream, `isolated_overlay_labels` for the names), with no crate and no memo, so the oracle can run one loop for both kinds of configuration."""

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
    """HarfBuzz's output under the overlay, computed without shaping: each letter becomes its twin, each boundary character its glyph, and each slot sits at zero offset with its `hmtx` advance. The position channel uses it for the overlay configuration. That is valid because the belt's overlay sweep checks every text up to `OVERLAY_HORIZON` against this output, and cursive attachment is pairwise, so a glyph no pair moves is moved by no text. The font lowers the namer dot before a Short twin, but this class always names it `periodcentered`. The constructor therefore raises when the two dot glyphs have different advances, since pen positions would then depend on more than the text."""

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

    def _labels(self, text: str) -> list[str]:
        """The glyph label of each slot of `text` under the overlay, which `shape` and `positions` both use."""
        return isolated_overlay_labels(self.spec, isolated_overlay_tokens(self.spec, text))

    def shape(self, text: str, features: frozenset[str]) -> list[dict]:
        labels = self._labels(text)
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

    def positions(self, text: str, features: frozenset[str]) -> list[tuple[int, int, int]]:
        """Each slot's position from the same labels `shape` uses: zero offset and the glyph's `hmtx` advance."""
        return [(0, 0, self._advances[name]) for name in self._labels(text)]


def check_isolated_positions(text, config, shaper: Shaper, shaped, divergences) -> None:
    """Check that every shaped slot sits at zero offset with its glyph's `hmtx` advance, as it must when nothing attaches under the overlay. A ZWNJ slot, which HarfBuzz shows as the space glyph at zero advance, must have zero advance. This check is what allows `IsolatedOverlayShaper` to replace HarfBuzz on the oracle's side."""
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
    """The labels the settlement lookup sees for `text`, after formation, the marker fold, and the ZWNJ chokepoint. Formation goes through `settle.form_ligatures`, so the section 5.7 late-formation guard applies here as it does in the kernel and the emitted lookup. `guard_verdicts` is the crate's verdicts for this spec (`kernel_exec.guard_sweep`), computed once by the caller."""
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


_WINDOW_BOUNDARIES = frozenset({"space", "uni200C", "periodcentered"})
_EDGE_LABEL = "#EDGE"
_NA_LABEL = "#NA"


def _window_rights(labels: list[str], index: int) -> tuple[str, str, str, str]:
    """The four right slots of the raw settlement window at `index`. Each slot is the next label along, `#EDGE` past the end of the buffer, or `#NA` once the slot before it is a boundary, the edge, or `#NA`, because no record reads past a boundary.

    The table's deep-slot structure plays no part here. A rule that dropped a slot matches any token at it (`witness._first_matching_rule`), so a raw token at a slot the enumeration never split matches the same rule HarfBuzz would. Keying the settle memo on all four raw slots is sound without any relevance check, because the crate reads exactly these slots of a case line. It is also faster: measured on the live alphabet at the belt's horizon, the probes that decided which slots to blank cost far more than the blanking saved. `witness._matched_windows` and `_SettledWindowWalk` both call this, so the replay and the memo key read the same window.
    """
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


def _label_family(label: str) -> str:
    return label.split(".")[0]


def _token_members(decision, label: str) -> tuple[str, ...]:
    """The raw member labels a table's deep-slot field stands for: the class map's entry for a class id, or else the label itself."""
    deep = getattr(decision, "deep_classes", None)
    if deep:
        members = deep.get(label)
        if members:
            return members
    return (label,)


def _token_representative(decision, label: str) -> str:
    return _token_members(decision, label)[0]


class _DeepTokenIndex:
    """Maps a window's raw third and fourth right slots to one configuration's deep-slot class tokens, for the rule replay (`witness._matched_windows` takes one as `deep_index`). `_SettledWindowWalk` does not use it.

    It has two levels, because fourth-slot fibers depend on the third-slot token as well as the base: `{(renamed input, settled left, renamed r1, renamed r2) -> {renamed member label -> r3 token}}`, and the same keyed one level deeper on the resolved r3 token. That token is the class id when the row's r3 is a class, and otherwise the bare r3 in renamed space, which is what `resolve` returns in each case (a class token is never renamed, and a bare label reaches `resolve` already marker-folded).

    It is built once per configuration from `decision.transitions`, `decision.deep_classes`, and the rename map. Callers run `resolve` where they hold the settled left, after `_window_rights` has read the raw labels, so a class id never stands where a raw label is expected. A boundary label passes through unchanged. A member the index lacks stays a raw label and then matches no row, as for any window the table lacks; the enumeration is exact, so this should not happen. `representatives` maps each class token to its renamed first member for rule matching. That is exact because the build asserts that every emitted look class holds either all of a token's members or none of them.
    """

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
_Ask = tuple[str, str, str, str, str]
_Outcome = tuple[Settled, str, str]


@dataclass
class _WalkState:
    """One text partway through a walk: its tokens and labels, the settled stream and names so far, the current left context, and the position reached. Between waves a state is finished (`index` past the last token), waiting at a letter position whose window the memo does not yet hold, or, during a `prefill` under `on_error="drop"`, stopped at a refused window."""

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
    """A window the crate would not settle, stored in the memo in place of its outcome. Only a walk built with `on_error="drop"` records one, so that a prefill can continue past it. A later `walk` or `walk_many` that reaches this window raises the refusal."""

    message: str


_MEMO_PREFIX = struct.Struct("<Q")
_MEMO_MIX = (
    0x9E3779B97F4A7C15,
    0xBF58476D1CE4E5B9,
    0x94D049BB133111EB,
    0xD6E8FEB86659FD93,
    0xA0761D6478BD642F,
    0xE7037ED1A0B428DB,
)
_MEMO_HASH_BITS = 64
_MEMO_HEADER_CAP = 1 << 26
_MemoTypecode = Literal["H", "I"]


def _memo_typecode(count: int) -> _MemoTypecode:
    """The array typecode for an id column: `H` while the table it indexes fits in sixteen bits, otherwise `I`."""
    return "H" if count <= 1 << 16 else "I"


def _memo_aligned(offset: int) -> int:
    """The first eight-byte boundary at or after `offset`. Every section of a settle memo file starts on one, so a mapped column can be cast in place whatever its typecode."""
    return (offset + 7) & ~7


def _memo_slots(columns: Sequence[Sequence[int]], slots: int) -> Iterator[int]:
    """The starting index slot of each row of `columns`, in row order, by the hash `_MemoStore.probe` uses: each id times its column's constant in `_MEMO_MIX`, summed modulo 2^64, keeping the top bits that address `slots`. It is built from C-level maps, so a pass over millions of rows runs no Python-level loop."""
    mask = (1 << _MEMO_HASH_BITS) - 1
    shift = _MEMO_HASH_BITS - (slots.bit_length() - 1)
    total = map(operator.mul, columns[0], itertools.repeat(_MEMO_MIX[0]))
    for column, mix in zip(columns[1:], _MEMO_MIX[1:]):
        total = map(operator.add, total, map(operator.mul, column, itertools.repeat(mix)))
    return map(operator.rshift, map(mask.__and__, total), itertools.repeat(shift))


def _memo_index(
    columns: Sequence[array], values: array, index: array | None = None, indexed: int = 0
) -> tuple[array, bytearray | None]:
    """Build the open-addressed index over `columns` and return it with a keep mask. The index has the smallest power-of-two slot count at least 2N, each slot holding row + 1 or 0 for empty, probed linearly from `_memo_slots`. A row whose key an earlier row already holds gives that row its value and is dropped, as a later dict entry replaces an earlier one in place. The mask is None when every key is distinct, and otherwise flags the rows to keep.

    `index` and `indexed` pass an index that already holds the first `indexed` rows at these row numbers (a standing file's index, when no row ahead of the new ones changed). Only the later rows are then inserted, provided the index stays at most half full with every row; otherwise it is rebuilt.
    """
    rows = len(values)
    if index is None or len(index) < 2 * rows:
        slots = 1 << (2 * rows - 1).bit_length() if rows else 0
        index = array("I", [0]) * slots
        indexed = 0
    slots = len(index)
    keep: bytearray | None = None
    if not rows:
        return index, keep
    mask = slots - 1
    c0, c1, c2, c3, c4, c5 = columns
    fresh = [column[indexed:] for column in columns] if indexed else columns
    for row, slot in enumerate(_memo_slots(fresh, slots), start=indexed):
        while True:
            other = index[slot]
            if not other:
                index[slot] = row + 1
                break
            other -= 1
            if (
                c0[other] == c0[row]
                and c1[other] == c1[row]
                and c2[other] == c2[row]
                and c3[other] == c3[row]
                and c4[other] == c4[row]
                and c5[other] == c5[row]
            ):
                values[other] = values[row]
                if keep is None:
                    keep = bytearray(b"\x01") * rows
                keep[row] = 0
                break
            slot = (slot + 1) & mask
    return index, keep


def _memo_column(view: memoryview, start: int, length: int, typecode: _MemoTypecode) -> Sequence[int]:
    """`length` bytes of the mapping at `start` as a column of `typecode`. The file's columns are always little-endian, so this is a cast over the mapping on a little-endian host and a byteswapped copy on a big-endian one."""
    section = view[start : start + length]
    if sys.byteorder == "little":
        return section.cast(typecode)
    copied = array(typecode)
    copied.frombytes(section)
    copied.byteswap()
    return copied


def _memo_copy(column: array, source: Sequence[int]) -> bool:
    """Append all of `source`, a column as `_memo_column` returns it, to `column` in one C-level copy and return True. Return False and leave `column` unchanged when the typecodes differ."""
    if isinstance(source, memoryview):
        if source.format != column.typecode:
            return False
        column.frombytes(source.cast("B"))
        return True
    if not isinstance(source, array) or source.typecode != column.typecode:
        return False
    column.extend(source)
    return True


def _memo_bytes(column: array) -> bytes:
    """`column` as the little-endian bytes the file stores."""
    if sys.byteorder == "little":
        return column.tobytes()
    copied = array(column.typecode, column)
    copied.byteswap()
    return copied.tobytes()


class _MemoStore:
    """One configuration's settle memo file, mapped read-only: the part of a walk's memo loaded from disk.

    `load` maps the file at `memo.path` and keeps the mapping and its file object until `close`. It reads the two tables into Python: `labels` (the file's label table, interned, in file order), `label_ids` (its inverse), and `outcomes` (each of the file's outcomes passed through the walk's `_outcome`, so they are the same objects `windows` holds). The six id columns (`columns`), the value column (`values`), and the probe index (`index`) are `memoryview.cast` slices over the mapping, so they cost the worker no heap. They are pages of one file, held once per machine in the page cache however many walks map it, and the kernel can evict them under memory pressure.

    Two byte arrays belong to each walk. `dead` flags every row this walk does not serve: a row naming a family whose key changed since the file was written (`oracle_cache.StaleMask` at label grain, computed over the six columns with C-level maps and one reduce), or, for a walk restricted by `load_only_asked_by`, a row whose five settlement-independent slots are outside its asks. `reached` flags every row a probe has returned. `live` counts the rows `dead` does not flag, `loaded` the live rows plus the stale ones (the `loaded=` of the `[t] settle_memo` line), `stale` the stale rows, and `unasked` the rows the restriction dropped.

    A probe maps the window's six labels through `label_ids`, and a label the table lacks is a miss before any hashing. It hashes the six ids as the writer did: each id times its slot's odd 64-bit constant in `_MEMO_MIX`, summed modulo 2^64 and shifted right by 64 - k for 2^k slots, then linear probing from there. The hash is written out so the file does not depend on Python's tuple hash, and `_memo_slots` is the writer's vectorized form of it. It sums per-column products because a multiply-shift over the six ids packed into one word clusters on the live ids, which are small and structured, while the sum keeps chains near one probe at the writer's half-full sizing. The file's keys are distinct (`_write_settle_memo` keeps one row per key), so a probe stops at the first row whose ids match: a dead row is a miss, and a live row is marked reached and returned.

    The load reads the header and the two tables, and reads the columns only for the stale fold or the ask restriction, one pass each. It does not scan for corruption. An id past a table or a probe chain longer than the row count occurs only in a corrupt file, and the probe that meets it retires every row (`_retire`), so the walk settles everything it asks from then on.

    `items` yields the live rows as (window, outcome) pairs in file order: all of them, only the reached ones, or only the unreached ones. `selector` makes the same choice as one flag per row, which `carry_into` uses to copy rows into a new file straight from the columns without building a key tuple per row. `close` drops the views and the mapping. A walk that replaced the file reads the old inode until then, because a mapping outlives the directory entry it was opened through. A mapping that an `items` iterator still reads stays open until that iterator is released.
    """

    __slots__ = (
        "labels",
        "label_ids",
        "outcomes",
        "columns",
        "values",
        "index",
        "dead",
        "reached",
        "live",
        "loaded",
        "stale",
        "unasked",
        "_shift",
        "_mask",
        "_mapping",
        "_handle",
        "_path",
    )

    def __init__(self) -> None:
        self.labels: list[str] = []
        self.label_ids: dict[str, int] = {}
        self.outcomes: list[_Outcome] = []
        self.columns: list[Sequence[int]] = []
        self.values: Sequence[int] = array("I")
        self.index: Sequence[int] = array("I")
        self.dead = bytearray()
        self.reached = bytearray()
        self.live = 0
        self.loaded = 0
        self.stale = 0
        self.unasked = 0
        self._shift = _MEMO_HASH_BITS
        self._mask = 0
        self._mapping: mmap.mmap | None = None
        self._handle: IO[bytes] | None = None
        self._path: Path | None = None

    def load(
        self,
        memo: SettleMemoFile,
        spec: ResolvedSpec,
        asks: set[_Ask] | None,
        outcome_of: Callable[[Settled], _Outcome],
    ) -> None:
        """Map the file at `memo.path` and keep what a walk keyed with `memo` may serve. A file that is missing, has another stamp, or has a moved family the registry cannot place loads nothing, silently. A file that cannot be read (short or torn, a header or table that will not unpickle, a layout that runs past the end of the file) loads nothing and prints a warning. The memo only saves time, so the walk then settles everything itself. An id past a table or an index chain longer than the row count is left for the probe that reaches it, which retires every row, so the load need not scan every page. A file under this stamp whose family keys changed serves every row except those naming a changed family, counted in `stale`. A load restricted to `asks` drops the rows outside them, counted in `unasked`."""
        assert self._mapping is None, "the store is already loaded"
        try:
            handle = open(memo.path, "rb")
        except FileNotFoundError:
            return
        except OSError as error:
            self._refuse(memo, error)
            return
        mapping: mmap.mmap | None = None
        problem: str | None = None
        mapped = False
        try:
            mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            mapped = self._map(mapping, memo, spec, asks, outcome_of)
        except _SETTLE_MEMO_READ_ERRORS as error:
            problem = f"{error}" or type(error).__name__
        if mapped:
            self._mapping, self._handle, self._path = mapping, handle, memo.path
            return
        if mapping is not None:
            mapping.close()
        handle.close()
        if problem is not None:
            self._refuse(memo, problem)

    @staticmethod
    def _refuse(memo: SettleMemoFile, problem: object) -> None:
        print(
            f"[warn] settle memo: {memo.path} not loaded ({problem}); every window is settled again",
            file=sys.stderr,
            flush=True,
        )

    def _map(
        self,
        mapping: mmap.mmap,
        memo: SettleMemoFile,
        spec: ResolvedSpec,
        asks: set[_Ask] | None,
        outcome_of: Callable[[Settled], _Outcome],
    ) -> bool:
        view = memoryview(mapping)
        size = len(view)
        if size < _MEMO_PREFIX.size:
            raise ValueError("shorter than its header prefix")
        (header_length,) = _MEMO_PREFIX.unpack_from(view)
        if header_length > _MEMO_HEADER_CAP or _MEMO_PREFIX.size + header_length > size:
            raise ValueError("header prefix out of range")
        header = pickle.loads(view[_MEMO_PREFIX.size : _MEMO_PREFIX.size + header_length])
        if (
            not isinstance(header, dict)
            or header.get("format") != SETTLE_MEMO_FORMAT
            or header.get("stamp") != memo.stamp
        ):
            return False
        recorded = header.get("family_keys")
        if not isinstance(recorded, dict):
            return False
        rows, label_count, outcome_count, slots, tables_length = (
            int(header[key]) for key in ("rows", "labels", "outcomes", "slots", "tables")
        )
        typecode, value_typecode = header["typecode"], header["value_typecode"]
        if (
            typecode not in ("H", "I")
            or value_typecode not in ("H", "I")
            or min(rows, label_count, outcome_count, slots, tables_length) < 0
            or (rows and (slots & (slots - 1) or slots < 2 * rows))
            or (not rows and slots)
        ):
            raise ValueError("malformed header")
        offset = _memo_aligned(_MEMO_PREFIX.size + header_length)
        if offset + tables_length > size:
            raise ValueError("truncated before its tables")
        labels, items = pickle.loads(view[offset : offset + tables_length])
        if (
            not isinstance(labels, list)
            or not isinstance(items, list)
            or len(labels) != label_count
            or len(items) != outcome_count
        ):
            raise ValueError("tables disagree with the header")
        offset = _memo_aligned(offset + tables_length)
        itemsize = array(typecode).itemsize
        spans = [rows * itemsize] * 6 + [rows * array(value_typecode).itemsize, slots * 4]
        starts: list[int] = []
        for span in spans:
            starts.append(offset)
            offset = _memo_aligned(offset + span)
        if offset > size:
            raise ValueError(f"{size} bytes where its header lays out {offset}")
        columns = [_memo_column(view, start, span, typecode) for start, span in zip(starts[:6], spans[:6])]
        values = _memo_column(view, starts[6], spans[6], value_typecode)
        index = _memo_column(view, starts[7], spans[7], "I")
        mask = oracle_cache.StaleMask(spec, oracle_cache.moved_families(recorded, memo.family_keys))
        if mask.everything:
            return False
        interned = list(map(sys.intern, labels))
        label_ids: dict[str, int] = {}
        for label_id, label in enumerate(interned):
            label_ids.setdefault(label, label_id)
        if len(label_ids) != len(interned):
            raise ValueError("names a spelling twice")
        outcomes = [outcome_of(item) for item in items]
        dead = bytearray(rows)
        stale = 0
        if mask.moved:
            bits = [mask.bit_of(_label_family(label)) for label in interned]
            masks = functools.reduce(
                lambda left, right: map(operator.or_, left, right),
                (map(bits.__getitem__, column) for column in columns),
            )
            dead = bytearray(map(mask.stale, masks))
            stale = dead.count(1)
        unasked = 0
        if asks is not None:
            allowed: set[tuple[int, ...]] = set()
            for ask in asks:
                with suppress(KeyError):
                    allowed.add(tuple(map(label_ids.__getitem__, ask)))
            kept = map(allowed.__contains__, zip(columns[0], columns[2], columns[3], columns[4], columns[5]))
            dropped = bytearray(map(operator.not_, kept))
            dead = bytearray(map(operator.or_, dead, dropped)) if stale else dropped
            unasked = dead.count(1) - stale
        self.labels = interned
        self.label_ids = label_ids
        self.outcomes = outcomes
        self.columns = columns
        self.values = values
        self.index = index
        self.dead = dead
        self.reached = bytearray(rows)
        self.live = rows - dead.count(1)
        self.loaded = rows - unasked
        self.stale = stale
        self.unasked = unasked
        self._shift = _MEMO_HASH_BITS - (slots.bit_length() - 1)
        self._mask = slots - 1
        return True

    def index_copy(self) -> array:
        """A heap copy of the probe index, for a writer that keeps every row of this store at its row number (`_write_settle_memo`'s `standing`)."""
        index = array("I")
        if isinstance(self.index, memoryview):
            index.frombytes(self.index.cast("B"))
        else:
            index.extend(self.index)
        return index

    def close(self) -> None:
        """Drop the views and the mapping, so the old inode is released once nothing reads it. Closing a store that was never loaded, or is already closed, does nothing. If an `items` iterator still holds a view, `mmap.close` raises `BufferError`, which is ignored, and the mapping is freed when that iterator is."""
        self.columns = []
        self.values = array("I")
        self.index = array("I")
        self.dead = bytearray()
        self.reached = bytearray()
        self.live = 0
        mapping, handle = self._mapping, self._handle
        self._mapping = self._handle = None
        if mapping is not None:
            with suppress(BufferError):
                mapping.close()
        if handle is not None:
            handle.close()

    def _retire(self, problem: str) -> None:
        """Mark every row dead and warn, after a probe finds the file corrupt. From then on every probe is a miss, `selector` chooses nothing, and the walk settles everything it asks, as it would if the file had never loaded."""
        rows = len(self.dead)
        self.dead = bytearray(b"\x01") * rows
        self.reached = bytearray(rows)
        self.live = 0
        print(
            f"[warn] settle memo: {self._path} retired ({problem}); every window is settled again from here",
            file=sys.stderr,
            flush=True,
        )

    def probe(self, window: _Window) -> _Outcome | None:
        """The outcome the store holds for `window`, marking its row reached, or None. A label the file lacks is a miss before any hashing, and a dead row is a miss. A valid index holds one occupied slot per row, so the chain is bounded by the row count. A chain longer than that, a slot naming a row past the columns, or a value past the outcome table retires the store."""
        if not self.live:
            return None
        try:
            key = tuple(map(self.label_ids.__getitem__, window))
        except KeyError:
            return None
        m0, m1, m2, m3, m4, m5 = _MEMO_MIX
        total = key[0] * m0 + key[1] * m1 + key[2] * m2 + key[3] * m3 + key[4] * m4 + key[5] * m5
        slot = (total & ((1 << _MEMO_HASH_BITS) - 1)) >> self._shift
        index = self.index
        mask = self._mask
        c0, c1, c2, c3, c4, c5 = self.columns
        left = len(self.dead)
        try:
            while left:
                row = index[slot]
                if not row:
                    return None
                row -= 1
                if (c0[row], c1[row], c2[row], c3[row], c4[row], c5[row]) == key:
                    if self.dead[row]:
                        return None
                    self.reached[row] = 1
                    return self.outcomes[self.values[row]]
                slot = (slot + 1) & mask
                left -= 1
        except IndexError:
            self._retire("an id past its tables")
            return None
        self._retire("a probe chain past its rows")
        return None

    def __len__(self) -> int:
        return self.live

    def reached_count(self) -> int:
        return self.reached.count(1)

    def selector(self, reached: bool | None = None) -> bytearray:
        """One flag per row, set on the live rows (`reached=None`), the reached rows (True), or the live unreached rows (False). A reached row is never dead, because a probe returns a miss on a dead row before marking it."""
        if reached is None:
            return bytearray(map(operator.not_, self.dead))
        if reached:
            return bytearray(self.reached)
        return bytearray(map(operator.not_, map(operator.or_, self.reached, self.dead)))

    def carry_into(
        self,
        labels: list[str],
        outcomes: list[Settled],
        columns: Sequence[array],
        values: array,
        reached: bool | None = None,
    ) -> None:
        """Append the rows `selector(reached)` chooses to `columns` and `values` in file order, straight from the mapped columns without a key tuple per row. This store's tables are appended to `labels` and `outcomes`, and every id is shifted past what those tables held before. Copying every live row into empty tables and columns of the file's own typecodes is one C-level copy per column (`_memo_copy`), which is the case in `absorb_settle_memo_parts`. Any other copy shifts and filters the ids element by element. The ids are copied unchecked, so a corrupt id reaches the new file and is found there by the probe that reaches it. `_write_settle_memo` merges the labels the two tables share."""
        base_labels, base_outcomes = len(labels), len(outcomes)
        labels.extend(self.labels)
        outcomes.extend(outcome[0] for outcome in self.outcomes)
        carried = self.selector(reached)
        whole = not base_labels and not base_outcomes and carried.count(0) == 0
        for column, source in zip(columns, self.columns):
            if not (whole and _memo_copy(column, source)):
                column.extend(map(base_labels.__add__, itertools.compress(source, carried)))
        if not (whole and _memo_copy(values, self.values)):
            values.extend(map(base_outcomes.__add__, itertools.compress(self.values, carried)))

    def items(self, reached: bool | None = None) -> Iterator[tuple[_Window, _Outcome]]:
        """The rows `selector(reached)` chooses, as (window, outcome) pairs in file order."""
        labels = self.labels
        pairs = zip(
            zip(*(map(labels.__getitem__, column) for column in self.columns)),
            map(self.outcomes.__getitem__, self.values),
        )
        return cast(Iterator[tuple[_Window, _Outcome]], itertools.compress(pairs, self.selector(reached)))


@dataclass(frozen=True)
class SettleMemoFile:
    """Where one configuration's settle memo file lives between phases, and the keys a walk must match to read it. The string replay fills the file on every whole-universe walk from the crate's window memo (`absorb_replay_memo`). The witness stage, the oracle, and the belt each map it, settle only what it lacks, and write back what they added.

    `stamp` is the whole-file stamp (`oracle_cache.settle_memo_stamp`: the walk's code closure, the non-rune data, the resolved spec structure, the capability features, the engine's settlement flags, and the configuration). `family_keys` holds the per-family rune keys (`oracle_cache.settle_family_keys`). The two keys follow the oracle row cache's reasoning: a window's settlement depends only on the rune files its six slots name (a formed ligature label names its rune directly, and every ligature rune whose components all appear among the slots counts too). So a file with another stamp is treated as absent, and a file with the same stamp serves every entry that names no changed family and drops the rest.
    """

    path: Path
    stamp: str
    family_keys: Mapping[str, str] = field(default_factory=dict)
    write_path: Path | None = None

    @property
    def writes_part(self) -> bool:
        """Whether a walk keyed with this file writes a part at `write_path` instead of replacing the file. The walk still reads `path`, but writes only the windows it settled fresh, and `absorb_settle_memo_parts` later merges the part into `path`.

        Every row range of the pooled oracle writes a part, whether or not its configuration is split, because a range that replaced the shared file would drop what the other ranges settled, or what the witness stage merged in after the range read the file. The witness stage also writes a part: its walk loads only the rows its certificate texts can ask for (`_SettledWindowWalk.load_only_asked_by`), so replacing the file would drop every row the load skipped. `run_m1.run_rule_witnesses` absorbs its part before the stage returns, and the oracle's absorbs wait for that.
        """
        return self.write_path is not None


def settle_memo_files(
    out_dir: Path, spec: ResolvedSpec, inputs: oracle_cache.SettleMemoInputs | None
) -> dict[str, SettleMemoFile]:
    """One `SettleMemoFile` per settlement configuration under `out_dir`, keyed from `inputs` (read from disk by the caller before loading `spec`) and from `spec`. The overlay configuration settles nothing and gets none. Returns an empty mapping when `inputs` is None."""
    if inputs is None:
        return {}
    keys = oracle_cache.settle_family_keys(inputs, spec)
    return {
        config: SettleMemoFile(
            Path(out_dir) / f"settle-memo-{config}.bin",
            oracle_cache.settle_memo_stamp(inputs, spec, config, features_for_config(config)).value,
            keys,
        )
        for config in SETTLEMENT_CONFIGS
    }


def settle_memo_standing(memo: SettleMemoFile) -> bool:
    """Whether the file at `memo.path` is one a walk keyed with `memo` would read: present, in this format, and under this stamp. It reads only the header, never a table or a column. Family keys are not checked, because a file whose keys changed still serves every entry naming no changed family (`_MemoStore.load`), and only a rune edit changes them. `run_m1.run_replay_strings` uses this to decide whether the replay must walk the whole universe to refill the file."""
    try:
        with open(memo.path, "rb") as handle:
            prefix = handle.read(_MEMO_PREFIX.size)
            if len(prefix) < _MEMO_PREFIX.size:
                return False
            (header_length,) = _MEMO_PREFIX.unpack(prefix)
            if header_length > _MEMO_HEADER_CAP:
                return False
            header = pickle.loads(handle.read(header_length))
    except _SETTLE_MEMO_READ_ERRORS:
        return False
    return (
        isinstance(header, dict)
        and header.get("format") == SETTLE_MEMO_FORMAT
        and header.get("stamp") == memo.stamp
        and isinstance(header.get("family_keys"), dict)
    )


def _write_settle_memo(
    memo: SettleMemoFile,
    labels: Sequence[str],
    outcomes: Sequence[Settled],
    columns: Sequence[array],
    values: array,
    path: Path | None = None,
    standing: tuple[array, int, int, int] | None = None,
) -> bool:
    """Write a settle memo file in the layout `_MemoStore` maps, and return whether it was written. This is the only writer of that layout.

    The layout is: an eight-byte little-endian length, then the header pickle (the format, `memo.stamp`, `memo.family_keys`, the row count `rows`, the table sizes `labels` and `outcomes`, the index size `slots`, the column typecodes `typecode` and `value_typecode`, and the byte length `tables` of the next pickle), then the label and outcome tables as one pickle, then the six id columns, the value column, and the probe index as fixed-width little-endian arrays. Every section starts on an eight-byte boundary.

    `labels` and `outcomes` are the tables that the caller's `columns` and `values` index, and they may contain repeats. Each repeated label is merged onto its first id and each repeated outcome onto its first equal, and the columns are remapped to match, so the file's tables list each entry once. An id column uses `H` while its table fits in sixteen bits and `I` otherwise. `_memo_index` then builds the index and merges a repeated key onto its first row, with the later entry's outcome, so the file holds one row per window.

    With `H` columns a window costs 12 key bytes, 2 value bytes, and 4 bytes per index slot over the index's 2N to 4N slots: 22 to 30 bytes uncompressed on disk, depending on where the row count falls below its power of two. The `[t] settle_memo` lines count the windows, and `du` on `rebuild/out/m1/settle-memo-*.bin` reports the file sizes; both grow with the alphabet. While writing, the heap holds the caller's columns, the remapped copies, and the index at once, roughly the file's size plus the columns'. The belt's whole-file save, a `--jobs 1` oracle's save, the replay's absorb, and each part absorb hold that much once per configuration.

    `standing` is a standing file's index with its row, label, and outcome counts. It says that the first rows of `columns`, up to that row count, are that file's live rows in its own id space with none dropped before them. The index is then copied and only the later rows are inserted (`absorb_settle_memo_parts` merging parts into a file with no stale rows). The writer checks that its merges leave those ids unchanged and that the index has room, and builds a new index otherwise.

    The file is written to `<path>.<pid>.tmp` and moved into place with `os.replace`. A reader that mapped the old file keeps it unchanged until it closes, and a reader that opens afterward maps the whole new file, so no reader sees a partial file. `path` defaults to `memo.path`. If the filesystem refuses the write, this prints a warning and returns False without failing the build, because every reader settles what the file lacks.
    """
    path = memo.path if path is None else Path(path)
    label_ids: dict[str, int] = {}
    table: list[str] = []
    canon: list[int] = []
    for label in labels:
        label_id = label_ids.setdefault(label, len(table))
        if label_id == len(table):
            table.append(label)
        canon.append(label_id)
    outcome_ids: dict[Settled, int] = {}
    items: list[Settled] = []
    canon_outcomes: list[int] = []
    for outcome in outcomes:
        outcome_id = outcome_ids.setdefault(outcome, len(items))
        if outcome_id == len(items):
            items.append(outcome)
        canon_outcomes.append(outcome_id)
    typecode = _memo_typecode(len(table))
    value_typecode = _memo_typecode(len(items))
    if len(table) < len(canon):
        folded = [array(typecode, map(canon.__getitem__, column)) for column in columns]
    else:
        folded = [column if column.typecode == typecode else array(typecode, column) for column in columns]
    folded_values = array(value_typecode, map(canon_outcomes.__getitem__, values))
    reused: array | None = None
    indexed = 0
    if standing is not None:
        reused, indexed, standing_labels, standing_outcomes = standing
        if (
            indexed > len(folded_values)
            or canon[:standing_labels] != list(range(standing_labels))
            or canon_outcomes[:standing_outcomes] != list(range(standing_outcomes))
        ):
            reused, indexed = None, 0
    index, keep = _memo_index(folded, folded_values, reused, indexed)
    if keep is not None:
        folded = [array(typecode, itertools.compress(column, keep)) for column in folded]
        folded_values = array(value_typecode, itertools.compress(folded_values, keep))
        renumber = array("I", [0])
        renumber.extend(itertools.accumulate(keep))
        index = array("I", map(renumber.__getitem__, index))
    tables = pickle.dumps((table, items), protocol=5)
    header = pickle.dumps(
        {
            "format": SETTLE_MEMO_FORMAT,
            "stamp": memo.stamp,
            "family_keys": dict(memo.family_keys),
            "rows": len(folded_values),
            "labels": len(table),
            "outcomes": len(items),
            "slots": len(index),
            "typecode": typecode,
            "value_typecode": value_typecode,
            "tables": len(tables),
        },
        protocol=5,
    )
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(staged, "wb") as handle:
            handle.write(_MEMO_PREFIX.pack(len(header)))
            handle.write(header)
            handle.write(bytes(-(_MEMO_PREFIX.size + len(header)) % 8))
            handle.write(tables)
            handle.write(bytes(-len(tables) % 8))
            for section in (*folded, folded_values, index):
                blob = _memo_bytes(section)
                handle.write(blob)
                handle.write(bytes(-len(blob) % 8))
        os.replace(staged, path)
    except OSError as error:
        with suppress(OSError):
            staged.unlink()
        print(f"[warn] settle memo: {path} not written ({error})", file=sys.stderr, flush=True)
        return False
    return True


_SettleMemoBlock = tuple[list[str], list[Settled], list[array], array]


def _write_settle_memo_part(memo: SettleMemoFile, blocks: Iterable[_SettleMemoBlock], path: Path) -> bool:
    """Write a settle memo part to `path` and return whether it was written (`SettleMemoFile.writes_part`). A part is a gzip stream of pickles: a header with `SETTLE_MEMO_PART_FORMAT`, `memo.stamp`, and `memo.family_keys`, then one pickle per block from `_memo_blocks`. It is written to a temporary file beside `path` and moved into place. A part holds only fresh windows, so it is written one block at a time and does not need the mapped layout; `absorb_settle_memo_parts` reads it back one block at a time through `_read_settle_memo`. If the filesystem refuses the write, this prints a warning and returns False."""
    path = Path(path)
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(staged, "wb", compresslevel=1) as handle:
            pickle.dump(
                {
                    "format": SETTLE_MEMO_PART_FORMAT,
                    "stamp": memo.stamp,
                    "family_keys": dict(memo.family_keys),
                },
                handle,
                protocol=5,
            )
            for block in blocks:
                pickle.dump(block, handle, protocol=5)
        os.replace(staged, path)
    except OSError as error:
        with suppress(OSError):
            staged.unlink()
        print(f"[warn] settle memo: {path} not written ({error})", file=sys.stderr, flush=True)
        return False
    return True


def _memo_blocks(items: Iterable[tuple[_Window, _Outcome]]) -> Iterable[_SettleMemoBlock]:
    """A walk's memo entries encoded `SETTLE_MEMO_BLOCK` at a time. Each block lists the labels and settled records its rows are the first to name, then six key columns and a value column that index every row into all the labels and records listed so far."""
    items = iter(items)
    label_index: dict[str, int] = {}
    outcome_index: dict[int, int] = {}
    while True:
        rows = list(itertools.islice(items, SETTLE_MEMO_BLOCK))
        if not rows:
            return
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
        yield (
            new_labels,
            new_items,
            [array("I", map(label_index.__getitem__, column)) for column in columns],
            array("I", map(outcome_index.__getitem__, map(id, values))),
        )


def _memo_columns(
    items: Iterable[tuple[_Window, _Outcome]],
) -> tuple[list[str], list[Settled], list[array], array]:
    """A walk's memo entries as the tables and columns `_write_settle_memo` takes: the labels and settled records in the order the rows first name them, and six `array("I")` key columns and a value column indexing them. The entries are encoded `SETTLE_MEMO_BLOCK` at a time, so the key tuples of a whole memo are never held beside their columns."""
    labels: list[str] = []
    outcomes: list[Settled] = []
    columns = [array("I") for _ in range(6)]
    values = array("I")
    for new_labels, new_items, block_columns, block_values in _memo_blocks(items):
        labels.extend(new_labels)
        outcomes.extend(new_items)
        for column, block in zip(columns, block_columns):
            column.extend(block)
        values.extend(block_values)
    return labels, outcomes, columns, values


def _read_settle_memo(memo: SettleMemoFile, spec: ResolvedSpec) -> Iterator[tuple[_SettleMemoBlock, int]]:
    """The blocks of the part at `memo.path`, each with the count of stale entries removed from it. A part that is missing, has another stamp, or has an unreadable header yields nothing. A part under this stamp whose family keys changed yields every block minus the entries naming a changed family: by a letter's label, by a formed ligature's label, or by all components of a changed ligature rune (`oracle_cache.StaleMask` at label grain). A changed family the registry cannot place makes the whole part stale, so it yields nothing. A block that will not decode ends the read with a warning, and the blocks before it are still valid, because a block's labels and records are indexed only by that block and later ones. The stale entries are found per block over the label columns, with one bit per label and C-level maps, and removed from the columns."""
    bits: list[int] = []
    loaded = 0
    try:
        with gzip.open(memo.path, "rb") as handle:
            header = pickle.load(handle)
            if (
                not isinstance(header, dict)
                or header.get("format") != SETTLE_MEMO_PART_FORMAT
                or header.get("stamp") != memo.stamp
            ):
                return
            recorded = header.get("family_keys")
            if not isinstance(recorded, dict):
                return
            mask = oracle_cache.StaleMask(spec, oracle_cache.moved_families(recorded, memo.family_keys))
            if mask.everything:
                return
            retiring = bool(mask.moved)
            while True:
                try:
                    new_labels, new_items, columns, values = pickle.load(handle)
                except EOFError:
                    break
                retired = 0
                if retiring:
                    bits.extend(mask.bit_of(_label_family(label)) for label in new_labels)
                    masks = functools.reduce(
                        lambda left, right: map(operator.or_, left, right),
                        (map(bits.__getitem__, column) for column in columns),
                    )
                    keep = [not mask.stale(window_mask) for window_mask in masks]
                    retired = len(keep) - sum(keep)
                    if retired:
                        columns = [array("I", itertools.compress(column, keep)) for column in columns]
                        values = array("I", itertools.compress(values, keep))
                loaded += len(values) + retired
                yield (new_labels, new_items, columns, values), retired
    except FileNotFoundError:
        return
    except _SETTLE_MEMO_READ_ERRORS as error:
        print(
            f"[warn] settle memo: {memo.path} stopped reading after {loaded} windows ({error}); the rest are settled again",
            file=sys.stderr,
            flush=True,
        )


def absorb_settle_memo_parts(memo: SettleMemoFile, parts: Sequence[Path], spec: ResolvedSpec) -> bool:
    """Merge `parts` into one configuration's shared settle memo file, and return True when a file was written. The parts come from the walks that may not replace the file: every row range of the pooled oracle, and the witness stage.

    The new file holds the standing file's live rows as `_MemoStore.load` serves them (under the stamp, minus the entries naming a changed family), then each part's blocks with their ids shifted past the labels and records before them. It is written through `_write_settle_memo`, which merges the labels a part repeats onto the file's ids. When no standing row is stale, it extends the standing index with the parts' rows instead of rebuilding it. The parts are read first, so the columns get the typecodes the whole file needs and the standing rows can be copied whole (`_MemoStore.carry_into`). The file is written under the current family keys, so its header never covers a stale entry, and a reader in another process sees either the old file or the new one.

    With no parts on disk this does nothing and returns False. A standing file that is absent or has another stamp contributes nothing, and the parts become the file. A window can arrive twice: two ranges may both settle it, or a range that mapped the file before the witness stage's merge may settle a window that merge has since added. Either way the later entry replaces the earlier one at the earlier row, as in a dict, so the file holds the window once.
    """
    present = [Path(part) for part in parts if Path(part).is_file()]
    if not present:
        return False
    read = [[block for block, _ in _read_settle_memo(replace(memo, path=part), spec)] for part in present]
    introduced_labels = sum(len(block[0]) for blocks in read for block in blocks)
    introduced_outcomes = sum(len(block[1]) for blocks in read for block in blocks)
    store = _MemoStore()
    store.load(memo, spec, None, lambda item: (item, "", ""))
    labels: list[str] = []
    outcomes: list[Settled] = []
    columns = [array(_memo_typecode(len(store.labels) + introduced_labels)) for _ in range(6)]
    values = array(_memo_typecode(len(store.outcomes) + introduced_outcomes))
    store.carry_into(labels, outcomes, columns, values)
    standing = (
        (store.index_copy(), len(values), len(labels), len(outcomes)) if store.dead.count(1) == 0 else None
    )
    store.close()
    for blocks in read:
        base_labels, base_outcomes = len(labels), len(outcomes)
        for new_labels, new_items, block_columns, block_values in blocks:
            labels.extend(new_labels)
            outcomes.extend(new_items)
            for column, block in zip(columns, block_columns):
                column.extend(map(base_labels.__add__, block))
            values.extend(map(base_outcomes.__add__, block_values))
    return _write_settle_memo(memo, labels, outcomes, columns, values, standing=standing)


def _ambiguous_ids(spelling: Sequence[str], used: Iterable[int]) -> set[int]:
    """Among the ids in `used`, every id whose label another id in `used` shares. Only through these ids can two distinct crate keys become one walk key."""
    first: dict[str, int] = {}
    ambiguous: set[int] = set()
    for label_id in used:
        other = first.setdefault(spelling[label_id], label_id)
        if other != label_id:
            ambiguous.update((label_id, other))
    return ambiguous


def absorb_replay_memo(dump: Path, memo: SettleMemoFile, spec: ResolvedSpec, config: str) -> int:
    """Write the crate's window memo for `config` (`kernel_exec.replay_memo_dump`, written by the `replay-strings` subcommand) as the configuration's settle memo file under `memo`'s stamp and family keys, and return the row count.

    The dump names every window in the crate's form: the input as the raw rune name or its `.noentry` twin, the rights as raw labels, and the left as a boundary label or an index into its record table. This function converts that to the `formed_labels` form. Each label is renamed through `model.raw_rename_map` once, each record is decoded through `kernel_exec.settled_of_row` once, and each record's left label is its `geometry.display_name`. The conversion touches only a few thousand strings, and the rows pass through as integer columns: the walk's label table is the renamed label table followed by the records' display names, which is the id space the dump's left column already indexes.

    A dump with another format token, a malformed head, another configuration, or a row byte count that does not match its head raises `KernelRunError` and writes nothing. Two crate keys can become one walk key only through two ids with the same label, so the row-level check runs only over rows that use such an id. If two such rows settle differently, this raises instead of writing the file, because a wrong memo would give wrong results, while a missing one only makes the readers settle the windows themselves.
    """
    features = features_for_config(config)
    data = dump.read_bytes()
    head_line, _, _ = data.partition(b"\n")
    marker, _, head_json = head_line.decode(errors="replace").partition("\t")
    if marker != f"# {kernel_exec.REPLAY_MEMO_FORMAT}":
        raise kernel_exec.KernelRunError(
            f"{dump} is not a {kernel_exec.REPLAY_MEMO_FORMAT} window memo: {marker!r}"
        )
    try:
        head = json.loads(head_json)
    except ValueError as error:
        raise kernel_exec.KernelRunError(f"{dump} carries a malformed head: {error}") from None
    if (
        not isinstance(head, dict)
        or {"config", "horizon", "rows", "labels", "records", "width"} - head.keys()
    ):
        raise kernel_exec.KernelRunError(f"{dump} carries a malformed head: {head!r}")
    if head["config"] != config:
        raise kernel_exec.KernelRunError(f"{dump} names {head['config']!r} where {config!r} was expected")
    try:
        rows, label_count, record_count, width = (
            int(head[key]) for key in ("rows", "labels", "records", "width")
        )
    except TypeError, ValueError:
        raise kernel_exec.KernelRunError(f"{dump} carries a malformed head: {head!r}") from None
    typecode = {2: "H", 4: "I"}.get(width)
    if typecode is None or array(typecode).itemsize != width or min(rows, label_count, record_count) < 0:
        raise kernel_exec.KernelRunError(f"{dump} carries a malformed head: {head!r}")
    parts = data.split(b"\n", 1 + label_count + record_count)
    body = parts[-1] if len(parts) == 2 + label_count + record_count else b""
    if len(body) != rows * 7 * width:
        raise kernel_exec.KernelRunError(
            f"{dump} holds {len(body)} row bytes where its head counts {rows} rows of {7 * width}"
        )
    renames = raw_rename_map(spec, features)
    try:
        labels = [
            renames.get(label, label) for label in (line.decode() for line in parts[1 : 1 + label_count])
        ]
        records = [
            kernel_exec.settled_of_row(json.loads(line))
            for line in parts[1 + label_count : 1 + label_count + record_count]
        ]
        labels += [geometry.display_name(spec, item.cell) for item in records]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as error:
        raise kernel_exec.KernelRunError(f"{dump} carries a table this spec cannot read: {error}") from None
    flat = array(typecode)
    flat.frombytes(body)
    if sys.byteorder == "big":
        flat.byteswap()
    columns = [flat[slot::7] for slot in range(7)]
    del flat, body, parts, data
    if rows:
        if max(max(columns[slot]) for slot in (0, 2, 3, 4, 5)) >= label_count:
            raise kernel_exec.KernelRunError(f"{dump} indexes a label past its table")
        if max(columns[1]) >= len(labels) or max(columns[6]) >= record_count:
            raise kernel_exec.KernelRunError(f"{dump} indexes a seat past its table")
        ambiguous_lefts = _ambiguous_ids(labels, set(columns[1]))
        ambiguous_slots = _ambiguous_ids(
            labels, set().union(*(set(columns[slot]) for slot in (0, 2, 3, 4, 5)))
        )
        if ambiguous_lefts or ambiguous_slots:
            seen: dict[tuple[str, ...], int] = {}
            for row in zip(*columns):
                if row[1] in ambiguous_lefts or any(row[slot] in ambiguous_slots for slot in (0, 2, 3, 4, 5)):
                    key = tuple(labels[label_id] for label_id in row[:6])
                    if seen.setdefault(key, row[6]) != row[6]:
                        raise kernel_exec.KernelRunError(
                            f"{dump} settles the window {key!r} two ways once respelled; nothing in it can be trusted"
                        )

    if not _write_settle_memo(memo, labels, records, columns[:6], columns[6]):
        raise kernel_exec.KernelRunError(f"{memo.path} could not be written")
    return rows


class _SettledWindowWalk:
    """The memoized settlement walk one configuration runs over its texts. A left-to-right pass computes each letter slot's raw window key (the slots `witness._matched_windows` reads, with the left taken from the stream settled so far) and looks it up in `windows`, a window -> (Settled, glyph name, left label) memo. Only a miss reaches the crate.

    The memo only saves time. It records no coverage, and the sweep's result is the same whether every window misses or every window hits. It is sound because every memoized outcome depends only on the window as keyed: the left label is the settled cell's display name (`geometry.display_name`, injective over every CellId field), and the right slots are exactly the raw tokens a case line carries. The key never reads the glyph inventory, so a walk with minted names and a walk without them build the same keys and differ only in the names they return, which is what lets the oracle and the belt share one memo file. Because the key holds all the raw right slots, the walk needs no liveness check; blanking the deep slots where the table's relevance filters show nothing reads them costs more in probes than it saves. `windows` has no size bound; interned labels and shared outcome tuples limit its cost to the key tuples. The walk-equivalence sweeps in rebuild/test_conform.py test all of this.

    `memo` names the file this walk shares with the other walks over the same texts. The string replay fills it on a whole-universe walk (`absorb_replay_memo`), and the witness stage, the oracle, and the belt each map it and settle what it lacks. It is mapped on the first wave that would otherwise reach the crate, so a walk that settles nothing (an oracle pass whose rows are all served) never opens it. `save_memo` writes it back only when this walk settled a window the file lacked, or pruned one. `_write_settle_memo` describes the file layout, and `_MemoStore` how a walk maps it: the tables go on the heap, and the columns and index stay pages of the mapping, shared in the page cache by every walk that maps the file. An entry whose window names a family whose key changed since the file was written is dropped at load (`oracle_cache.StaleMask` at label grain, including the ligature clause).

    Loaded entries stay in `_cold`, the `_MemoStore` over the mapping, which marks each row it returns as reached. With `promote` (a constructor keyword, on by default) a hit is also copied into `windows`, so the oracle and the witness stage run the store's probe once per window and a dict lookup after that. The belt runs with `promote=False` and serves every window from the columns: it reaches nearly every loaded window, so promoted copies would cost the key tuples the columns exist to avoid. `save_memo(prune=True)` keeps only the reached rows. The belt walks the whole universe every pass, so an entry it never reached is a window no text produces any more (its left slot named a settlement an edit has since changed), and keeping it would grow the file with every rune edit. A pruning save writes the fresh windows first and then the reached rows in file order, a permutation of what a promoting walk would write; no reader can tell the difference, because a load does not depend on row order and the file holds one row per window. The oracle does not prune, because it does not walk the rows its row cache serves, so their windows are never reached. Its whole-file save writes `windows` and then, in file order, every live row `windows` does not hold.

    A walk over a set of texts fixed before it runs can restrict the load to the windows those texts can ask for (`load_only_asked_by`). Only a window's left slot depends on settlement, so the other five slots of every window the texts reach can be computed in advance. A file row outside that set is marked dead at load, counted in `unasked_windows`, and never served. Such a walk cannot tell a dropped row from a window no text reaches, so it never prunes and never replaces the shared file. Its memo names a `write_path`, it writes only the windows it settled fresh, as a part (`_write_settle_memo_part`), and `absorb_settle_memo_parts` merges the part into the file.

    Batching is what makes the crate affordable here. `settle-cases` settles independent windows, but a text's next left is the previous window's outcome, so `_run` advances all its texts in waves. Each state runs forward to its first memo miss, each distinct missed key contributes one case line (a key two states reach in the same wave is asked once), and one `kernel_exec.settle_windows` call settles up to `batch` of them before every state advances again. A wave collects at most `batch` new keys and leaves the rest for the next wave, so a caller's chunk size bounds its own memory use and not the call's. `walk` runs the same loop over a single text, so each miss there costs a whole kernel spawn for one window. `single_settles` counts those, so a caller that forgot to `prefill` can see the cost.

    A refusal is the only thing the memo holds that is not an outcome. With `on_error="raise"`, the default, a refusal raises from the batch that met it. With `on_error="drop"`, a refusal met during `prefill` is memoized as a `_RefusedWindow`, the text containing it stops advancing, and the other texts finish, while `walk` and `walk_many` raise `settle.SettleError` when they reach that key. This lets a caller prefill a set of strings and report each refusal against the string that contained it, without one refusal aborting the rest. The certificate check uses this to name the rule whose certificate the crate refused.

    `audit_dedupe` checks the dedupe: every distinct raw case line a memo key covers beyond the first one asked is also settled and asserted equal to the memoized outcome. That tests the assumption behind `_window_rights`' `#NA` slots, that two raw windows with the same key settle the same way.
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
        promote: bool = True,
    ):
        self.spec = spec
        self.features = features
        self.glyph_names = glyph_names
        self.guard_verdicts = guard_verdicts
        self.batch = max(1, batch)
        self.audit_dedupe = audit_dedupe
        self.on_error = on_error
        self.memo = memo
        self._promote = promote
        self.windows: dict[_Window, _Outcome | _RefusedWindow] = {}
        self._cold = _MemoStore()
        self.single_settles = 0
        self.audit_extra_rows = 0
        self.audit_multi_keys: set[_Window] = set()
        self.memo_windows = 0
        self.stale_windows = 0
        self.unasked_windows = 0
        self.pruned_windows = 0
        self.fresh_windows = 0
        self.memo_seconds = 0.0
        self._memo_loaded = False
        self._asks: set[_Ask] | None = None
        self._refused = 0
        self._outcomes: dict[Settled, _Outcome] = {}
        self._fresh: list[_Window] = []
        self._settle_calls = 0
        self._audit_seen: set[tuple[settle.LeftContext, settle.RightToken, tuple[settle.RightToken, ...]]] = (
            set()
        )
        self._audit_pending: list[tuple[_Window, str]] = []

    def walk(self, text: str) -> tuple[list[Settled], list[str]]:
        """Settle one text through the memo and return (settled items, their glyph names). Every miss is its own kernel call, counted in `single_settles`; a caller with many texts should `prefill` them first."""
        before = self._settle_calls
        settled, names = self._run([text], collect=True)[0]
        self.single_settles += self._settle_calls - before
        return settled, names

    def walk_many(self, texts: Sequence[str]) -> list[tuple[list[Settled], list[str]]]:
        """Settle `texts` in waves and return one (settled, names) pair per text, in order."""
        return self._run(texts, collect=True)

    def prefill(self, texts: Sequence[str]) -> None:
        """Fill the memo from `texts` in waves and return nothing, so that walking them one at a time later needs no kernel calls. Under `on_error="drop"`, a window the crate refuses is memoized as a refusal and its text stops advancing, so the prefill finishes and the refusal is raised by a later `walk` that reaches it."""
        self._run(texts, collect=False)

    def load_only_asked_by(self, texts: Sequence[str]) -> set[_Ask]:
        """Restrict the memo load to the windows `texts` can ask for, and return that ask set. It holds, for every letter position of every text, the window's input and right slots (`_Ask`, the five settlement-independent slots of a `_Window`), computed through the same `_state` and `_window_rights` path `_window` uses. So it covers everything `prefill` and `walk` over these texts can reach: the left slot is the only one settlement decides, and a boundary position never reaches the memo. A row outside the set is dropped at load (`_load_memo`). A dropped row then looks the same as a window no text reaches, so a restricted walk writes a part instead of the file (`save_memo`), and its memo must name a `write_path`. Call this before the first wave loads the file."""
        assert not self._memo_loaded, "the memo is already loaded"
        assert self.memo is None or self.memo.writes_part, "a restricted walk files a part, never the file"
        asks: set[_Ask] = set()
        for text in texts:
            state = self._state(text)
            labels = state.labels
            for index, token in enumerate(state.tokens):
                if token.kind == "letter":
                    asks.add((labels[index], *_window_rights(labels, index)))
        self._asks = asks
        return asks

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
        """Run one state forward until it needs an outcome this walk does not have, and return True if it stopped at a memo miss. Boundary positions settle to their model constant here and never reach the kernel. A memoized refusal raises unless `tolerant`, in which case the state stops where it is and its partial stream is discarded."""
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
                store = self._cold
                if store.live:
                    outcome = store.probe(window)
                    if outcome is not None and self._promote:
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
        if self.memo is not None and self.memo.writes_part:
            self._fresh.append(window)
        if item is None:
            self._refused += 1
            self.windows[window] = _RefusedWindow(
                f"the kernel refused the window {window!r}, reached in {text!r}"
            )
            return
        self.windows[window] = self._outcome(item)

    def _outcome(self, item: Settled) -> _Outcome:
        """The shared memo value for `item` in this walk: the settled item, the name this walk's inventory gives it, and the display name every walk uses as the next window's left slot."""
        outcome = self._outcomes.get(item)
        if outcome is None:
            left = sys.intern(geometry.display_name(self.spec, item.cell))
            name = self.glyph_names.get(item.cell)
            outcome = (item, sys.intern(name) if name else left, left)
            self._outcomes[item] = outcome
        return outcome

    def _load_memo(self) -> None:
        """Map the shared memo file into `_cold`, the walk's `_MemoStore`, once, on the first wave that would otherwise reach the crate. A file that is missing, has another stamp, or cannot be read loads nothing, and the walk settles everything itself. A file under this stamp whose family keys changed serves every row except those naming a changed family (by a letter's label, by a formed ligature's label, or by all components of a changed ligature rune), counted in `stale_windows`; a changed family the registry cannot place makes the whole file stale. A walk restricted by `load_only_asked_by` serves only the rows whose five settlement-independent slots are in its ask set, and counts the rest in `unasked_windows`. `memo_windows` counts the rows the walk can serve plus the stale ones."""
        self._memo_loaded = True
        if self.memo is None:
            return
        started = time.perf_counter()
        store = self._cold
        try:
            store.load(self.memo, self.spec, self._asks, self._outcome)
        finally:
            self.memo_windows = store.loaded
            self.stale_windows = store.stale
            self.unasked_windows = store.unasked
            self.memo_seconds += time.perf_counter() - started

    def save_memo(self, prune: bool = False) -> bool:
        """Write the memo back when this walk settled a window the file lacked or pruned a row, and return True when a file was written. The write goes through `_write_settle_memo`, which replaces the file atomically, and then this walk's mapping of the old file is closed. Refusals are not written, so a later walk that reaches one of those windows asks the crate again.

        `prune` drops the loaded entries this walk never reached, counted in `pruned_windows`. It is only correct for a walk over the whole universe, which is the belt's. A pruning walk that promotes nothing writes its fresh windows and then the reached rows in file order. Rows are copied into the new file straight from the mapped columns, with their ids shifted past the fresh windows' labels, without a key tuple per row.

        A walk whose memo names a `write_path` writes only the windows it settled fresh, as a part at that path (`_write_settle_memo_part`), and leaves the shared file to `absorb_settle_memo_parts`. A walk that restricted its load (`load_only_asked_by`) always writes a part and never prunes, because a row its load dropped looks the same as a window it never reached.
        """
        if prune:
            assert self._asks is None, "a restricted walk never prunes"
        if self.memo is None:
            return False
        if self.memo.writes_part:
            if not self.fresh_windows:
                return False
            started = time.perf_counter()
            fresh = (
                (window, outcome)
                for window in self._fresh
                if not isinstance(outcome := self.windows[window], _RefusedWindow)
            )
            try:
                return _write_settle_memo_part(
                    self.memo, _memo_blocks(fresh), cast(Path, self.memo.write_path)
                )
            finally:
                self.memo_seconds += time.perf_counter() - started
        store = self._cold
        if prune:
            self.pruned_windows = len(store) - store.reached_count()
        if not self.fresh_windows and not (prune and self.pruned_windows):
            return False
        started = time.perf_counter()
        entries = self.windows.items()
        items: Iterable[tuple[_Window, _Outcome]]
        if self._refused:
            items = (
                (window, outcome) for window, outcome in entries if not isinstance(outcome, _RefusedWindow)
            )
        else:
            items = cast(Iterable[tuple[_Window, _Outcome]], entries)
        labels, outcomes, columns, values = _memo_columns(items)
        if prune:
            if not self._promote:
                store.carry_into(labels, outcomes, columns, values, reached=True)
        else:
            store.carry_into(labels, outcomes, columns, values, reached=False if self._promote else None)
        try:
            return _write_settle_memo(self.memo, labels, outcomes, columns, values)
        finally:
            store.close()
            self.memo_seconds += time.perf_counter() - started

    def memo_line(self, config: str, written: bool) -> str | None:
        """The `[t] settle_memo` line a phase prints about its use of the memo file, or None for a walk with no memo. The belt and the oracle print it, and neither restricts its load, so the line has no `unasked=`; the witness stage reports `unasked_windows` on its own `[t] rule_witnesses[<config>]` line."""
        if self.memo is None:
            return None
        return f"[t] settle_memo {config} {self.memo_seconds:.2f}s loaded={self.memo_windows} stale={self.stale_windows} fresh={self.fresh_windows} pruned={self.pruned_windows} written={'yes' if written else 'no'}"

    def _settle(self, cases: list[str]) -> list[Settled | None]:
        self._settle_calls += 1
        return kernel_exec.settle_windows(
            self.spec, cases, self.features, batch=self.batch, on_error=self.on_error
        )

    def _note_raw(self, window: _Window, state: _WalkState) -> None:
        """Queue a raw case line not yet seen for this memo key. The first line per key is the one the wave already asked. Every later one is a distinct case the dedupe assumes has the same outcome, and `_drain_audit` checks that."""
        raw = (state.left, state.tokens[state.index], self._rights(state))
        if raw in self._audit_seen:
            return
        self._audit_seen.add(raw)
        self.audit_multi_keys.add(window)
        self._audit_pending.append((window, kernel_exec.case_line(*raw)))

    def _drain_audit(self) -> None:
        """Settle the queued raw case lines and assert that each matches its key's memoized outcome."""
        pending, self._audit_pending = self._audit_pending, []
        self.audit_extra_rows += len(pending)
        for start in range(0, len(pending), self.batch):
            chunk = pending[start : start + self.batch]
            for (window, _case), item in zip(chunk, self._settle([case for _window, case in chunk])):
                memoized = self.windows.get(window)
                if memoized is None:
                    memoized = self._cold.probe(window)
                assert memoized is not None and not isinstance(memoized, _RefusedWindow), (window, memoized)
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
            cases: list[str] = []
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
                cases.append(
                    kernel_exec.case_line(state.left, state.tokens[state.index], self._rights(state))
                )
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


class WitnessError(Exception):
    """A settlement rule whose certificate does not fire it: the certificate text, settled through the crate, fires some other rule or none at the input it names. That means the fold's pins or its rule order is wrong."""


@dataclass
class WitnessReport:
    """One configuration's certificate check: how many rules the table has, the certificate text each verified rule fired in, and one message per rule whose certificate did not fire it. The last three fields count the walk's windows: `served` the settle memo rows the load kept, stale rows included (`memo_windows`), `unasked` the rows it dropped as outside what the certificates can ask for, and `fresh` the windows the crate settled for this check."""

    config: str
    rules: int
    witnessed: dict[int, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    served: int = 0
    unasked: int = 0
    fresh: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures and len(self.witnessed) == self.rules


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
    """Run the belt serially: one shared Shaper, each configuration in turn through `_conformance_config`, and the results merged by `merge_conformance_results`. The parallel form is `run_m1.run_font_conformance`, which submits `conformance_config_worker` per configuration. The sweep reads no decision table: it shapes the font and settles the same texts through the kernel, and read-back checks that the font holds the planned rules. `summary_name` is the file written under `out_dir`; the deep sweep passes its own name so it does not overwrite the belt's record. `settle_memos` names each configuration's shared settle memo file, and a configuration without one settles every window itself."""
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
    """One configuration's belt run: every string of length 1 to `max_length` over the alphabet, shaped with the font and compared with the settled stream, plus the split-buffer and zero-gap checks. Configurations share nothing, so both the serial `run_conformance` and the process-pool worker call this.

    An overlay configuration is swept to `OVERLAY_HORIZON` instead, whatever `max_length` is. Its expected names are `isolated_overlay_labels` over the raw tokens, it uses no walk and no memo, and every slot must sit at zero offset with its `hmtx` advance (`check_isolated_positions`). The split-buffer check runs there too.

    Settlement goes through `_SettledWindowWalk`'s memo, which only saves time. `settle_memo` shares that memo with the other walks over the same texts: it is loaded on the first miss, written back at the end if this sweep settled anything the file lacked, and pruned of every entry no text reached. This sweep walks the whole universe, so it is the only walk that can tell which windows still exist. Each length's texts go through the walk `TEXT_CHUNK` at a time, because at larger horizons a length has millions of texts and only the current chunk needs to be in memory. The split-buffer check runs only on texts that contain a splitter, since a text without one is its own single segment.
    """
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
    walker = _SettledWindowWalk(spec, features, glyph_names, guard_verdicts, memo=settle_memo, promote=False)

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
    """One configuration's belt run in its own process, building what it needs from the spec and the font. That includes the section 5.7 guard verdicts when the caller passes none (a fifth of a second against a sweep that runs for a minute); an overlay configuration forms nothing and skips them. `settle_memo` is only a path and keys, so the worker reads and writes the file itself."""
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
    """Merge per-configuration results into one ConformReport. `sequences` comes from the first result, because every settlement configuration sweeps the same texts; the overlay's shorter sweep shows only in the shaping runs. Shaping runs are summed, and divergences and notes are concatenated in the caller's configuration order. The oracle modes are merged and appended in sorted order, so the report does not depend on which configuration finished first."""
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


def _seam_token(spec: ResolvedSpec, seam) -> str:
    if seam is None:
        return "break"
    if isinstance(seam, int):
        return f"y{seam}"
    return f"y{spec.registry.y_of(seam)}"


def _cached_verdict(divergent: DivergentRow | None) -> oracle_cache.CachedRow | None:
    """A fresh comparison's result in the form the row cache stores: the five fields the subset table cannot supply, without the provenance that would make two equal verdicts compare unequal."""
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
    """A stored verdict converted back to a `DivergentRow`, with `config` and the three baseline fields taken from the table row instead of the store. From `_match_compiled` on, this row is indistinguishable from a freshly compared one; `rebuild/test_conform.py` checks that a served oracle pass writes the same audit as a cold one."""
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
    store: "oracle_cache.RowStore",
    sample: "oracle_cache.VerificationSample",
) -> None:
    """Re-derive the pass's stratified sample of served rows and check each against the record it was served from. Every family that served a row contributes rows, so a whole family of wrong records (which a rune edited during a run produces) is always caught, not just with the probability of the sample size over the rows served. The seed includes the pass ordinal, so each pass checks a different slice. The rows come from the sample itself, which keeps at most `VERIFICATION_SAMPLE_PER_FAMILY` per family, so nothing here re-reads the table. A mismatch raises `SystemExit` instead of being treated as a cache miss, because the store holds verdicts this build does not produce, and `divergence-audit.tsv` is a fingerprinted artifact the surface build's manifest is stamped against."""
    picked = sample.sampled_rows()
    if not picked:
        return
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
    """Compare one baseline row with the settlement of its text, and return the `DivergentRow`, or None when they agree. The caller's walk passes in `settled`, so each row is settled once. Under the overlay configuration it is `IsolatedOverlayWalk`'s bare stream: each letter's default-stance cell with no seam, which is what the alias map means by a bare name, one per raw token, so a window whose pair formed a ligature in the old font diverges as `ligation`."""
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
    """The individual differences between the cell an old name stands for and the cell settlement chose, as phenomenon tokens for `classify_divergence`."""
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
