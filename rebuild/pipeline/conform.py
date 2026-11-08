"""Conformance gates (M1-PLAN sections 5 and 6, Group 3): HarfBuzz against the settlement function, and the settlement function against the section 13.1 baseline oracle.

`run_conformance` runs the conformance sweep. For each settlement configuration it shapes every text of length 1 to the maximum length (`SWEEP_MAX_LENGTH`, 4 by default) over the alphabet and compares the result with settlement: glyph names (`check_oracle`), split-buffer equivalence (`check_split_buffer`), and zero gaps at joins (`check_join_gaps`). This comparison uses no ledger, so any divergence is a compiler defect. `Shaper` (rebuild/pipeline/shapers.py) shapes at the MONOTONE_CHARACTERS cluster level and reads glyph names through TTFont, because HarfBuzz's name API truncates them.

The isolated-overlay configuration (ss10, `OVERLAY_CONFIGS`) has no settlement to compare against. Read-back checks on every build that the ss10 input substitution covers every letter cmap glyph and that no copy appears in any formation sequence, marker line, ZWNJ lock class, or settlement input. So under ss10 every letter renders as its copy at its `hmtx` advance, and nothing forms or attaches. The conformance sweep checks this at `OVERLAY_MAX_LENGTH`: single letters show that each letter maps to its copy, and pairs show that no pair forms, joins, or moves.

The conformance sweep does not check rule coverage; other stages do. Read-back (rebuild/pipeline/readback.py) checks that the compiled font holds every emitted rule at its planned position. The crate's fold fails the table build on any rule that no replayed row first-matches (`fold::assert_outcome_partition`). The witness stage (`witness.check_rule_certificates`, run by `run_m1` over the certificates the crate writes beside the rules) settles a string that fires each rule. The crate's `replay-strings` subcommand (`rebuild/kernel-rs/src/replay.rs`, `run_m1.run_replay_strings`) checks enumeration completeness: whether each live raw window a string reaches is one the fixpoint enumerated with its slot restrictions satisfied, or one it left at `#NA` or never reached, which the font handles with a wildcard or default rule. It replays `_SettledWindowWalk` and `witness._first_matching_rule` over the persisted rules at `run_m1.REPLAY_MAX_LENGTH` on every build, over every text after a code or structure change and, after a rune edit, over the texts naming an edited rune or a rune whose records read one (`run_m1.replay_families`).

What only the conformance sweep checks is what needs the real binary: HarfBuzz's application semantics (lookup interaction across features, backtrack reading settled glyphs across subtable breaks, default-ignorable skipping, class matching, Extension indirection) and whether the 6-slot window abstraction is sufficient. Certificates cannot test the second, because they are built from that abstraction. `make conform-deep` (rebuild/tools/deep_sweep.py) runs the same sweep, split-buffer check included, at maximum length 5 or more on demand, less the texts a configuration other than `default` settles and shapes as `default` does. It becomes due when `emit_gsub.behavior_classes`, the font-compilation code, or the uharfbuzz version changes, so a rune edit that adds no new rule shape does not make it due. Read-back's boundary-glyphs stage checks the ZWNJ glyph's zero advance and empty outline once per build, so the conformance sweep does not check them per shaped slot.

Settlement goes through `_SettledWindowWalk`'s per-configuration window memo: the crate settles each distinct raw window once, in a batch, and every recurrence is a lookup. The oracle's rows are the conformance sweep's texts, so the two share the memo through one file per configuration under rebuild/out/m1 (`SettleMemoFile`), keyed per family like the oracle row cache. The string replay fills that file on every full replay (`read_replay_memo`, from the window memo the `replay-strings` subcommand writes into a FIFO). So a window is settled once per configuration until a rune it names changes, and a cold pass does its settling in the replay instead of in the oracle.

The section 6 oracle gate is in rebuild/pipeline/oracle.py (`compare_against_baseline`, the ledger classifier) and rebuild/pipeline/oracle_positions.py (the position comparison), which the enumeration's stamp leaves out. This module holds what they consume. `_compare_row` compares one baseline row's ligation (clusters), per-junction classification, and cell identity with the settled stream through the alias map, and returns the `DivergentRow` the oracle classifies. `_cached_verdict` and `_served_verdict` convert between that result and the oracle row cache's record, and `_verify_served_sample` re-derives a pass's sample of served rows and checks them against the store. `_compare_row` and the walk are the two entry points `oracle_cache.ORACLE_ROW_CODE_PATHS` is derived from, which is why they live here and the classifier does not.

The crate does all settlement, through `kernel_exec`. `_SettledWindowWalk` sends waves of distinct raw windows to `kernel_exec.settle_windows`. The certificate check (`witness.check_rule_certificates`) and the conformance sweep each call `kernel_exec.guard_sweep` once and pass its verdicts to every formation call below them. Nothing here re-derives a settled cell.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import mmap
import operator
import os
import pickle
import struct
import sys
import time
import zlib
from array import array
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Callable, Iterable, Iterator, Literal, Mapping, Protocol, Sequence, cast

from rebuild.pipeline import geometry, kernel_exec, oracle_cache, output_sink, settle
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
)
from rebuild.pipeline.shapers import Shaper, isolated_overlay_labels, isolated_overlay_tokens
from rebuild.validation.rowmodel import Row, format_codepoints

ZWNJ = "\u200c"
ZWNJ_SENTINEL = "<zwnj>"
# The configurations letters settle under. Each has its own settlement table, join table, window enumeration, settle memo, and rule-witness run. One crate `build-tables` process enumerates them all, `default` first and the others as deltas over it or from scratch beside it.
SETTLEMENT_CONFIGS = ("default", "ss03", "ss04", "ss05", "ss03+ss05", "ss08")
# The isolated-overlay taste configurations (`model.isolated_overlay_active`). Nothing settles under them, so they have no table. The conformance sweep covers them at `OVERLAY_MAX_LENGTH`, relying on read-back's isolation check, and the oracle compares them against the bare stream. `rebuild/test_conform.py` checks that this tuple matches the registry's `overlay: isolated` features.
OVERLAY_CONFIGS = ("ss10",)
# Every configuration the font is accepted under: what the conformance sweep shapes and the Manual pins replay against. The oracle reads `ORACLE_CONFIGS` instead, which the review corpus restates as `audit.ACCEPTANCE_CONFIGS`.
ACCEPTANCE_CONFIGS = SETTLEMENT_CONFIGS + OVERLAY_CONFIGS
# The settlement configurations the shipped font has no set for, so they have no baseline table and the oracle never compares them.
M1_ONLY_CONFIGS = ("ss08",)
# The configurations the oracle compares against the shipped font's baseline tables and stores rows for.
ORACLE_CONFIGS = tuple(config for config in ACCEPTANCE_CONFIGS if config not in M1_ONLY_CONFIGS)
# How many texts of one length the conformance sweep walks at a time. Each length's texts are streamed instead of listed, because at maximum length 5 the length-5 texts number in the millions, while one chunk's walk states cost tens of megabytes at any maximum length.
TEXT_CHUNK = 65536
SWEEP_MAX_LENGTH = 4
# The overlay sweep's length, whatever the conformance sweep's maximum length. Single letters show that each cmap glyph maps to its copy, and pairs show that no pair forms, joins, or moves. Together with read-back's isolation check, that covers every text.
OVERLAY_MAX_LENGTH = 2
# How many divergences a configuration's result and the merged report keep in full. Every divergence is counted by kind, but a red sweep at a deep maximum length can diverge on millions of texts, and the summaries print only this many.
EXEMPLAR_LIMIT = 20
SETTLE_MEMO_FORMAT = "ams-settle-memo/3"
# Windows per block when a walk encodes its memo entries, for a whole-file save or for the fresh windows it hands back (`FreshMemo`). Each fresh block is compressed on its own, so a walk encodes its fresh windows, and an absorb decodes them, this many at a time instead of holding them twice.
SETTLE_MEMO_BLOCK = 65536
# Rows per block when the replay's streaming absorb reads the crate's window memo (`read_replay_memo`): 0.46 MB of a narrow memo and 0.92 MB of a wide one, so each block and its remapped copies stay under a megabyte, the size of block the spawn worker's allocator reuses from one block to the next.
REPLAY_MEMO_BLOCK_ROWS = 1 << 15
# The longest head, label or record line the streaming absorb reads before it refuses the memo as malformed.
_REPLAY_MEMO_LINE_CAP = 1 << 16
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


class DivergenceSink(Protocol):
    """What the check functions record a divergence into: a list, or a `DivergenceTally`."""

    def append(self, divergence: Divergence, /) -> None: ...


@dataclass
class DivergenceExemplar:
    """A divergence kept in full, with its text's length and its text's rank among the texts of that length in `itertools.product` order over the alphabet, so exemplars from separate sweeps can be put back in sweep order."""

    divergence: Divergence
    length: int
    rank: int


@dataclass
class ConformReport:
    font: str
    sequences: int = 0
    shaping_runs: int = 0
    divergences_by_kind: dict[str, int] = field(default_factory=dict)
    exemplars: list[DivergenceExemplar] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def divergence_count(self) -> int:
        return sum(self.divergences_by_kind.values())

    @property
    def passed(self) -> bool:
        return self.divergence_count == 0

    def write(self, path: Path) -> None:
        summary: dict[str, object] = {
            "font": self.font,
            "sequences": self.sequences,
            "shaping_runs": self.shaping_runs,
            "divergences": self.divergence_count,
            "divergences_by_kind": dict(self.divergences_by_kind),
            "pass": self.passed,
            "notes": self.notes,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        output_sink.write_output(path, json.dumps(summary, indent=2) + "\n", keep_if_equal=True)


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


def check_oracle(text, config, shaped, expected, divergences: DivergenceSink, modes) -> None:
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
    text,
    config,
    features,
    shaper: Shaper,
    shaped,
    divergences: DivergenceSink,
    splitters: frozenset[str] = frozenset({ZWNJ}),
) -> None:
    """Check that the shaped buffer, with its splitter slots dropped, matches its splitter-separated segments shaped alone. Slots are compared on outline, advance, and offsets, not names, because the locked copies have the same bitmaps as the bare runes."""
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
    text,
    config,
    shaper: Shaper,
    shaped,
    anchors_of: Callable[[str], dict | None],
    divergences: DivergenceSink,
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
                    f"gap 0 at junction (exit {exit_point})",
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


class IsolatedOverlayWalk:
    """The overlay configuration's replacement for `_SettledWindowWalk`, with the same `walk_many` interface. It computes each text from the registry alone (`settle.isolated_overlay_settled` for the stream, `isolated_overlay_labels` for the names), with no crate and no memo, so the oracle can run one loop for both kinds of configuration."""

    single_settles = 0
    promoted_hits = 0
    kept_hits = 0

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

    fresh_memo = None

    def save_memo(self) -> bool:
        return False

    def memo_line(self, config: str, written: bool) -> str | None:
        return None


def check_isolated_positions(text, config, shaper: Shaper, shaped, divergences: DivergenceSink) -> None:
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
    """The labels the settlement lookup sees for `text`, after formation, the marker renaming, and the ZWNJ lock. Formation goes through `settle.form_ligatures`, so the section 5.7 late-formation guard applies here as it does in the kernel and the emitted lookup. `guard_verdicts` is the crate's verdicts for this spec (`kernel_exec.guard_sweep`), computed once by the caller."""
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

    The table's deep-slot structure plays no part here. A rule that dropped a slot matches any token at it (`witness._first_matching_rule`), so a raw token at a slot the enumeration never split matches the same rule HarfBuzz would. Keying the settle memo on all four raw slots is sound without any relevance check, because the crate reads exactly these slots of a case line. It is also faster: measured on the live alphabet at the conformance sweep's maximum length, the probes that decided which slots to blank cost far more than the blanking saved. `witness._matched_windows` and `_SettledWindowWalk` both call this, so the replay and the memo key read the same window.
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

    It has two levels, because fourth-slot fibers depend on the third-slot token as well as the base: `{(renamed input, settled left, renamed r1, renamed r2) -> {renamed member label -> r3 token}}`, and the same keyed one level deeper on the resolved r3 token. That token is the class id when the row's r3 is a class, and otherwise the bare r3 in renamed space, which is what `resolve` returns in each case (a class token is never renamed, and a bare label reaches `resolve` already marker-renamed).

    It is built once per configuration from `decision.transitions`, `decision.deep_classes`, and the rename map. Callers run `resolve` where they hold the settled left, after `_window_rights` has read the raw labels, so a class id never stands where a raw label is expected. A boundary label passes through unchanged. A member the index lacks stays a raw label and then matches no row, as for any window the table lacks; the enumeration is exact, so this should not happen. `representatives` maps each class token to its renamed first member for rule matching. That is exact because the build asserts that every emitted lookahead class holds either all of a token's members or none of them.
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
_MemoColumn = array | memoryview
_MEMO_TYPECODE_OF_WIDTH: dict[int, _MemoTypecode] = {2: "H", 4: "I"}


def _memo_typecode(count: int) -> _MemoTypecode:
    """The array typecode for an id column: `H` while the table it indexes fits in sixteen bits, otherwise `I`."""
    return "H" if count <= 1 << 16 else "I"


def _memo_aligned(offset: int) -> int:
    """The first eight-byte boundary at or after `offset`. Every section of a settle memo file starts on one, so a mapped column can be cast in place whatever its typecode."""
    return (offset + 7) & ~7


def _memo_slots(columns: Sequence[Iterable[int]], slots: int) -> Iterator[int]:
    """The starting index slot of each row of `columns`, in row order, by the hash `_MemoStore.probe` uses: each id times its column's constant in `_MEMO_MIX`, summed modulo 2^64, keeping the top bits that address `slots`. It is built from C-level maps, so a pass over millions of rows runs no Python-level loop."""
    mask = (1 << _MEMO_HASH_BITS) - 1
    shift = _MEMO_HASH_BITS - (slots.bit_length() - 1)
    total = map(operator.mul, columns[0], itertools.repeat(_MEMO_MIX[0]))
    for column, mix in zip(columns[1:], _MEMO_MIX[1:]):
        total = map(operator.add, total, map(operator.mul, column, itertools.repeat(mix)))
    return map(operator.rshift, map(mask.__and__, total), itertools.repeat(shift))


def _memo_index(
    columns: Sequence[_MemoColumn],
    values: _MemoColumn,
    index: array | None = None,
    indexed: int = 0,
    distinct: int | None = None,
) -> tuple[array, bytearray | None]:
    """Build the open-addressed index over `columns` and return it with a keep mask. The index has the smallest power-of-two slot count at least 2N, each slot holding row + 1 or 0 for empty, probed linearly from `_memo_slots`. N is the row count, or `distinct` when the caller passes it: the count of distinct keys among the rows, from a caller that knows which of its rows repeat a key (`_SettledWindowWalk.save_memo`), so the index is sized for the rows it keeps. A row whose key an earlier row already holds gives that row its value and is dropped, as a later dict entry replaces an earlier one in place. The mask is None when every key is distinct, and otherwise flags the rows to keep.

    `index` and `indexed` pass an index that already holds the first `indexed` rows at these row numbers (a standing file's index, when no row ahead of the new ones changed). Only the later rows are then inserted, provided the index stays at most half full with N rows; otherwise it is rebuilt.
    """
    rows = len(values)
    keys = rows if distinct is None else distinct
    if index is None or len(index) < 2 * keys:
        slots = 1 << (2 * keys - 1).bit_length() if keys else 0
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


def _memo_view(column: _MemoColumn) -> memoryview:
    """`column` as the little-endian bytes the file stores: a view over the column itself on a little-endian host, so a write copies nothing, and a byteswapped copy on a big-endian one."""
    view = memoryview(column)
    if sys.byteorder == "little":
        return view.cast("B")
    copied = array(_MEMO_TYPECODE_OF_WIDTH[view.itemsize])
    copied.frombytes(view.cast("B"))
    copied.byteswap()
    return memoryview(copied).cast("B")


def _zeroed_column(typecode: _MemoTypecode, rows: int) -> memoryview:
    """A column of `rows` zero ids of `typecode` in anonymous memory, which the system maps zeroed a page at a time as the column is first written, so a column preallocated from a memo's head holds no page before its rows arrive, and goes back to the system whole once its last view is released."""
    size = rows * array(typecode).itemsize
    if not size:
        return memoryview(array(typecode))
    return memoryview(mmap.mmap(-1, size)).cast(typecode)


class _MemoStore:
    """One configuration's settle memo file, mapped read-only: the part of a walk's memo loaded from disk.

    `load` maps the file at `memo.path` and keeps the mapping and its file object until `close`. It reads the two tables into Python: `labels` (the file's label table, interned, in file order), `label_ids` (its inverse), and `outcomes` (each of the file's outcomes passed through the walk's `_outcome`, so they are the same objects `windows` holds). The six id columns (`columns`), the value column (`values`), and the probe index (`index`) are `memoryview.cast` slices over the mapping, so they cost the worker no heap. They are pages of one file, held once per machine in the page cache however many walks map it, and the kernel can evict them under memory pressure.

    Two byte arrays belong to each walk. `dead` flags every row this walk does not serve: a row naming a family whose key changed since the file was written (`oracle_cache.StaleMask` at label grain, computed over the six columns with C-level maps and one reduce), or, for a walk restricted by `load_only_asked_by`, a row whose five settlement-independent slots are outside its asks. `reached` flags every row a probe has returned. `live` counts the rows `dead` does not flag, `loaded` the live rows plus the stale ones (the `loaded=` of the `[t] settle_memo` line), `stale` the stale rows, and `unasked` the rows the restriction dropped.

    A probe maps the window's six labels through `label_ids`, and a label the table lacks is a miss before any hashing. It hashes the six ids as the writer did: each id times its slot's odd 64-bit constant in `_MEMO_MIX`, summed modulo 2^64 and shifted right by 64 - k for 2^k slots, then linear probing from there. The hash is written out so the file does not depend on Python's tuple hash, and `_memo_slots` is the writer's vectorized form of it. It sums per-column products so that the ids never have to share one word: six ids at the `H` columns' 16-bit width need 96 bits, a multiply-shift over a 64-bit word that drops two of them clusters badly on the live ids, and the sum keeps chains near one probe at the writer's half-full sizing. The file's keys are distinct (`_write_settle_memo` keeps one row per key), so a probe stops at the first row whose ids match: a dead row is a miss, and a live row is marked reached and returned.

    The load reads the header and the two tables, and reads the columns only for the stale fold or the ask restriction, one pass each. It does not scan for corruption. A valid index is at least half empty, so every chain reaches a row or an empty slot within N + 1 reads for N rows, the last read of a miss landing on the empty slot. An id past a table or a chain longer than N + 1 reads occurs only in a corrupt file, and the probe that meets it retires every row (`_retire`), so the walk settles everything it asks from then on.

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
        "retired",
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
        self.retired = False
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

    def identity(self) -> tuple[int, int, int, int] | None:
        """The device, inode, size, and modification time of the file this store maps, or None when it maps nothing. A store a probe retired still maps its file and keeps its identity; `retired` says it found the file corrupt. Two stores with the same identity mapped the same version of the file, so their `reached` flags index the same rows (`absorb_settle_memo`'s `SweepPrune`)."""
        if self._handle is None:
            return None
        stat = os.fstat(self._handle.fileno())
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

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
        self.retired = True
        print(
            f"[warn] settle memo: {self._path} retired ({problem}); every window is settled again from here",
            file=sys.stderr,
            flush=True,
        )

    def probe(self, window: _Window) -> _Outcome | None:
        """The outcome the store holds for `window`, marking its row reached, or None. A label the file lacks is a miss before any hashing, and a dead row is a miss. A valid index holds one occupied slot per row and at least as many empty ones, so a run of occupied slots is at most the row count N long and the chain ends within N + 1 reads. A chain longer than that, a slot naming a row past the columns, or a value past the outcome table retires the store."""
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
        left = len(self.dead) + 1
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
        """Append the rows `selector(reached)` chooses to `columns` and `values` in file order, straight from the mapped columns without a key tuple per row. This store's tables are appended to `labels` and `outcomes`, and every id is shifted past what those tables held before. Copying every live row into empty tables and columns of the file's own typecodes is one C-level copy per column (`_memo_copy`). Any other copy shifts and filters the ids element by element. The ids are copied unchecked, so a corrupt id reaches the new file and is found there by the probe that reaches it. `_write_settle_memo` merges the labels the two tables share."""
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
    """Where one configuration's settle memo file lives between phases, and the keys a walk must match to read it. The string replay fills the file on every full replay from the crate's window memo (`read_replay_memo`). The witness stage, the oracle, and the conformance sweep each map it, settle only what it lacks, and write back what they added.

    `stamp` is the whole-file stamp (`oracle_cache.settle_memo_stamp`: the walk's code closure, the non-rune data, the resolved spec structure, the capability features, the engine's settlement flags, and the configuration). `family_keys` holds the per-family rune keys (`oracle_cache.settle_family_keys`). The two keys follow the oracle row cache's reasoning: a window's settlement depends only on the rune files its six slots name (a formed ligature label names its rune directly, and every ligature rune whose components all appear among the slots counts too). So a file with another stamp is treated as absent, and a file with the same stamp serves every entry that names no changed family and drops the rest.

    `returns_fresh` says a walk keyed with this file may not replace it. Such a walk still reads `path`, but `save_memo` encodes only the windows it settled fresh, in memory (`FreshMemo`, on `_SettledWindowWalk.fresh_memo`), and its caller hands them to `absorb_settle_memo`, which merges them into `path`. Every row range of the pooled oracle returns its fresh windows, whether or not its configuration is split, because a range that replaced the shared file would drop what the other ranges settled, or what the witness stage merged in after the range read the file. Every settlement unit of the pooled conformance sweep returns them for the same reason, beside which rows it reached, so the parent can prune what no unit reached (`MemoReach`). The witness stage's walk returns them too: it loads only the rows its certificate texts can ask for (`_SettledWindowWalk.load_only_asked_by`), so replacing the file would drop every row the load skipped. `run_m1.run_rule_witnesses` absorbs them in its own process before the stage returns, and the oracle's absorbs wait for that.
    """

    path: Path
    stamp: str
    family_keys: Mapping[str, str] = field(default_factory=dict)
    returns_fresh: bool = False


@dataclass(frozen=True)
class FreshMemo:
    """The windows a walk keyed with `SettleMemoFile.returns_fresh` settled fresh, as it hands them to `absorb_settle_memo`. `blocks` holds two zlib streams for each of `_memo_blocks`' blocks, compressed one block at a time at level 1: its head, a pickle of the labels and settled records its rows are the first to name and of its row count, and then its rows, the six key columns and the value column, each `array("I")` in native byte order, end to end. A block's ids index every label and record its part's blocks have named so far, so a part's blocks read in order. `rows`, `labels` and `records` count what the blocks hold in all and travel beside them when the blocks are shipped, so a part's size is known without reading a block (the memory series' absorbs lines sum `rows`); the absorb reads every block head itself and takes its tables and typecodes from them.

    A walk holds its part's blocks as bytes. A pool worker ships them into a POSIX shared memory segment and returns the counts with `blocks` empty, and the absorb's worker maps the segment and hands the absorb the mapped pieces as `blocks`, with `release`, which closes the mapping, called once the absorb has read the last of them (`run_m1_gates._absorb_memo`). A part with no rows, every fresh window of which the crate refused, has no blocks and still counts as a part, so its configuration's file is rewritten as it is when any part arrives.
    """

    blocks: Sequence[bytes | memoryview]
    rows: int
    labels: int
    records: int
    release: Callable[[], None] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class MemoReach:
    """What one settlement unit of the pooled conformance sweep did with its configuration's settle memo, returned to the parent on its result (`ConformanceConfigResult.memo_reach`) so the parent can prune the file once every unit has finished (`absorb_settle_memo` with a `SweepPrune`).

    `identity` is the mapped file's `_MemoStore.identity`, or None when the walk mapped nothing (the file absent, under another stamp, or unreadable). `retired` says a probe found the file corrupt and retired the store, which zeroed its `reached` flags. `rows` counts the mapped file's rows, dead ones included, `live` the rows the walk could serve, and `loaded` and `stale` are the walk's `memo_windows` and `stale_windows`. `reached` is the store's `reached` flags, one byte per row, zlib-compressed: a unit reaches only the rows its own texts ask for, so the flags compress well. A reached row is never dead (`_MemoStore.selector`). `fresh` holds the windows the unit settled fresh (`FreshMemo`), or None when it settled none; the pool worker ships them to the parent's absorb in shared memory and returns the reach without them (`run_m1_gates._conformance_unit`). `seconds` is its `memo_seconds`.
    """

    identity: tuple[int, int, int, int] | None
    retired: bool
    rows: int
    live: int
    loaded: int
    stale: int
    reached: bytes
    fresh: FreshMemo | None
    seconds: float


def settle_memo_line(
    config: str,
    seconds: float,
    loaded: int,
    stale: int,
    fresh: int,
    pruned: int,
    written: bool,
    absorbed: MemoAbsorb | None = None,
) -> str:
    """The `[t] settle_memo` line a phase prints about one configuration's use of its settle memo file: the serial conformance sweep and the oracle through `_SettledWindowWalk.memo_line`, and the pooled conformance sweep's parent once per configuration over its units (`run_m1_gates.run_font_conformance`). Neither restricts its load, so the line has no `unasked=`; the witness stage reports `unasked_windows` on its own `[t] rule_witnesses[<config>]` line. The pooled sweep's parent passes its configuration's absorb as `absorbed` when one laid a file out, and the line then ends with the absorb's path and row counts (`MemoAbsorb.tokens`, less the `fresh=` the line already holds)."""
    line = f"[t] settle_memo {config} {seconds:.2f}s loaded={loaded} stale={stale} fresh={fresh} pruned={pruned} written={'yes' if written else 'no'}"
    if absorbed is not None and absorbed.index is not None:
        line += " " + absorbed.tokens(fresh=False)
    return line


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


def _settle_memo_header(memo: SettleMemoFile) -> dict | None:
    """The header of the file at `memo.path` when it is one a walk keyed with `memo` would read (present, in this format, and under this stamp), or None. It reads only the header, never a table or a column."""
    try:
        with open(memo.path, "rb") as handle:
            prefix = handle.read(_MEMO_PREFIX.size)
            if len(prefix) < _MEMO_PREFIX.size:
                return None
            (header_length,) = _MEMO_PREFIX.unpack(prefix)
            if header_length > _MEMO_HEADER_CAP:
                return None
            header = pickle.loads(handle.read(header_length))
    except _SETTLE_MEMO_READ_ERRORS:
        return None
    if (
        isinstance(header, dict)
        and header.get("format") == SETTLE_MEMO_FORMAT
        and header.get("stamp") == memo.stamp
        and isinstance(header.get("family_keys"), dict)
    ):
        return header
    return None


def settle_memo_standing(memo: SettleMemoFile) -> bool:
    """Whether the file at `memo.path` is one a walk keyed with `memo` would read: present, in this format, and under this stamp. It reads only the header, never a table or a column. Family keys are not checked, because a file whose keys changed still serves every entry naming no changed family (`_MemoStore.load`), and only a rune edit changes them. `run_m1.run_replay_strings` uses this to decide whether the replay must walk every text to refill the file."""
    return _settle_memo_header(memo) is not None


@dataclass(frozen=True)
class _MemoTables:
    """The two tables a settle memo file holds and how the caller's ids map onto them. `table` lists each label once and `items` each outcome once; `canon` maps each id of the caller's label list to its label's first id, and `canon_outcomes` each id of its outcome list to its outcome's first equal. `typecode` and `value_typecode` are the column typecodes the two tables need."""

    table: list[str]
    canon: list[int]
    items: list[Settled]
    canon_outcomes: list[int]
    typecode: _MemoTypecode
    value_typecode: _MemoTypecode

    @property
    def merges_labels(self) -> bool:
        return len(self.table) < len(self.canon)

    @property
    def merges_outcomes(self) -> bool:
        return len(self.items) < len(self.canon_outcomes)


def _memo_tables(labels: Sequence[str], outcomes: Sequence[Settled]) -> _MemoTables:
    """Merge each repeated label onto its first id and each repeated outcome onto its first equal (`_MemoTables`)."""
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
    return _MemoTables(
        table, canon, items, canon_outcomes, _memo_typecode(len(table)), _memo_typecode(len(items))
    )


@dataclass(frozen=True)
class _MemoPieces:
    """A section of a settle memo file laid out a piece at a time instead of held whole: `nbytes` is its length, and `pieces`, each time it is called, yields its columns or views in order. `absorb_settle_memo` lays a column out this way when it copies the standing rows straight from the mapping, widened or filtered a block at a time, ahead of the fresh rows, and its values and index when it patches or renumbers them a block at a time."""

    nbytes: int
    pieces: Callable[[], Iterator[_MemoColumn]]


@dataclass(frozen=True)
class FinishedSettleMemo:
    """A settle memo file finished in memory and not yet written: its header and table pickles and its sections, the six id columns, the value column and the probe index, in `_write_settle_memo`'s layout, with the row count the header states. A section is a column or a `_MemoPieces`. `pieces` yields its bytes in file order with the padding between sections, each section a view over its column or over each of its pieces, so writing or hashing it copies no column. `stage` writes it beside the file it will replace, and `digest` hashes it."""

    header: bytes
    tables: bytes
    sections: tuple[_MemoColumn | _MemoPieces, ...]
    rows: int

    def pieces(self) -> Iterator[bytes | memoryview]:
        yield _MEMO_PREFIX.pack(len(self.header))
        yield self.header
        yield bytes(-(_MEMO_PREFIX.size + len(self.header)) % 8)
        yield self.tables
        yield bytes(-len(self.tables) % 8)
        for section in self.sections:
            if isinstance(section, _MemoPieces):
                length = 0
                for piece in section.pieces():
                    view = _memo_view(piece)
                    length += view.nbytes
                    yield view
                if length != section.nbytes:
                    raise ValueError(
                        f"a section laid out {length} bytes where its header counts {section.nbytes}"
                    )
            else:
                view = _memo_view(section)
                length = view.nbytes
                yield view
            yield bytes(-length % 8)

    def stage(self, path: Path) -> Path:
        """Write the file to `<path>.<pid>.tmp` beside `path`, the name `_write_settle_memo` stages under, and return that path. An error removes what it wrote and propagates."""
        staged = Path(path).with_name(f"{Path(path).name}.{os.getpid()}.tmp")
        try:
            staged.parent.mkdir(parents=True, exist_ok=True)
            with open(staged, "wb") as handle:
                for piece in self.pieces():
                    handle.write(piece)
        except BaseException:
            with suppress(OSError):
                staged.unlink()
            raise
        return staged

    def digest(self) -> tuple[int, str]:
        """The file's size in bytes and the sha256 of its bytes in order, read from heap without writing them."""
        hasher = hashlib.sha256()
        size = 0
        for piece in self.pieces():
            hasher.update(piece)
            size += memoryview(piece).nbytes
        return size, hasher.hexdigest()


def _settle_memo_head(
    memo: SettleMemoFile, tables: _MemoTables, rows: int, slots: int
) -> tuple[bytes, bytes]:
    """The header and table pickles of a file of `rows` rows over `tables` with an index of `slots` slots, under `memo`'s stamp and family keys: every writer of the layout states its header through this."""
    pickled = pickle.dumps((tables.table, tables.items), protocol=5)
    header = pickle.dumps(
        {
            "format": SETTLE_MEMO_FORMAT,
            "stamp": memo.stamp,
            "family_keys": dict(memo.family_keys),
            "rows": rows,
            "labels": len(tables.table),
            "outcomes": len(tables.items),
            "slots": slots,
            "typecode": tables.typecode,
            "value_typecode": tables.value_typecode,
            "tables": len(pickled),
        },
        protocol=5,
    )
    return header, pickled


def _finish_settle_memo(
    memo: SettleMemoFile,
    tables: _MemoTables,
    columns: Sequence[_MemoColumn],
    values: _MemoColumn,
    index: array | None = None,
    indexed: int = 0,
    distinct: int | None = None,
) -> FinishedSettleMemo:
    """Index `columns` and `values`, already in `tables`' id space and typecodes, and lay them out as a file under `memo`'s stamp and family keys: `_memo_index` builds the index and merges a repeated key onto its first row with the later row's value, the merged rows are dropped and the index renumbered, and the header and tables are pickled. `index` and `indexed` pass a standing file's index, and `distinct` the count of distinct keys among the rows, as `_memo_index` takes them."""
    index, keep = _memo_index(columns, values, index, indexed, distinct)
    if keep is not None:
        columns = [array(tables.typecode, itertools.compress(column, keep)) for column in columns]
        values = array(tables.value_typecode, itertools.compress(values, keep))
        renumber = array("I", [0])
        renumber.extend(itertools.accumulate(keep))
        index = array("I", map(renumber.__getitem__, index))
    header, pickled = _settle_memo_head(memo, tables, len(values), len(index))
    return FinishedSettleMemo(header, pickled, (*columns, values, index), len(values))


def _settled_memo(
    memo: SettleMemoFile,
    labels: Sequence[str],
    outcomes: Sequence[Settled],
    columns: Sequence[array],
    values: array,
    standing: tuple[array, int, int, int] | None = None,
    distinct: int | None = None,
) -> FinishedSettleMemo:
    """`_write_settle_memo`'s file, finished in memory and not yet written."""
    tables = _memo_tables(labels, outcomes)
    typecode = tables.typecode
    if tables.merges_labels:
        folded = [array(typecode, map(tables.canon.__getitem__, column)) for column in columns]
    else:
        folded = [column if column.typecode == typecode else array(typecode, column) for column in columns]
    folded_values = array(tables.value_typecode, map(tables.canon_outcomes.__getitem__, values))
    reused: array | None = None
    indexed = 0
    if standing is not None:
        reused, indexed, standing_labels, standing_outcomes = standing
        if (
            indexed > len(folded_values)
            or tables.canon[:standing_labels] != list(range(standing_labels))
            or tables.canon_outcomes[:standing_outcomes] != list(range(standing_outcomes))
        ):
            reused, indexed = None, 0
    return _finish_settle_memo(memo, tables, folded, folded_values, reused, indexed, distinct)


def _put_settle_memo(finished: FinishedSettleMemo, path: Path) -> bool:
    """Stage `finished` beside `path` (`FinishedSettleMemo.stage`) and move it into place with `os.replace`, and return whether it was written. If the filesystem refuses the write, this prints a warning and returns False without failing the build, because every reader settles what the file lacks."""
    path = Path(path)
    try:
        os.replace(finished.stage(path), path)
    except OSError as error:
        with suppress(OSError):
            path.with_name(f"{path.name}.{os.getpid()}.tmp").unlink()
        print(f"[warn] settle memo: {path} not written ({error})", file=sys.stderr, flush=True)
        return False
    return True


def _write_settle_memo(
    memo: SettleMemoFile,
    labels: Sequence[str],
    outcomes: Sequence[Settled],
    columns: Sequence[array],
    values: array,
    path: Path | None = None,
    standing: tuple[array, int, int, int] | None = None,
    distinct: int | None = None,
) -> bool:
    """Write a settle memo file in the layout `_MemoStore` maps, and return whether it was written. This, `read_replay_memo`'s streaming absorb and `absorb_settle_memo` are the writers of that layout, and all three lay it out through `_settle_memo_head` and `FinishedSettleMemo`.

    The layout is: an eight-byte little-endian length, then the header pickle (the format, `memo.stamp`, `memo.family_keys`, the row count `rows`, the table sizes `labels` and `outcomes`, the index size `slots`, the column typecodes `typecode` and `value_typecode`, and the byte length `tables` of the next pickle), then the label and outcome tables as one pickle, then the six id columns, the value column, and the probe index as fixed-width little-endian arrays. Every section starts on an eight-byte boundary.

    `labels` and `outcomes` are the tables that the caller's `columns` and `values` index, and they may contain repeats. Each repeated label is merged onto its first id and each repeated outcome onto its first equal, and the columns are remapped to match, so the file's tables list each entry once (`_memo_tables`). An id column uses `H` while its table fits in sixteen bits and `I` otherwise. `_memo_index` then builds the index and merges a repeated key onto its first row, with the later entry's outcome, so the file holds one row per window. `distinct`, from a caller that knows how many of its rows repeat a window, is the count of distinct windows among them, so the index is sized for the rows the file keeps and not for every row passed (`_memo_index`).

    With `H` columns a window costs 12 key bytes, 2 value bytes, and 4 bytes per index slot over the index's 2N to 4N slots: 22 to 30 bytes uncompressed on disk, depending on where the row count falls below its power of two. The `[t] settle_memo` lines count the windows, and `du` on `rebuild/out/m1/settle-memo-*.bin` reports the file sizes; both grow with the alphabet. While writing, the heap holds the caller's columns, the remapped copies, and the index at once, roughly the file's size plus the columns'. The serial conformance sweep's save and a `--jobs 1` oracle's save hold that much once per configuration. A `--jobs 1` oracle's save whose walk left a whole-text hit in the store holds more: its columns also carry a copy of each row the walk copied into `windows` (`_SettledWindowWalk.save_memo`), and merging those copies away adds a keep flag a row, compacted copies of the columns, a renumbering of 4 bytes a row, and a renumbered copy of the index beside the first (`_finish_settle_memo`). `absorb_settle_memo` copies the standing rows straight from the mapping and holds only the fresh rows and the index, and the replay's streaming absorb holds the remapped columns alone, since it remaps each block of rows as it arrives (`read_replay_memo`).

    `standing` is a standing file's index with its row, label, and outcome counts. It says that the first rows of `columns`, up to that row count, are that file's live rows in its own id space with none dropped before them. The index is then copied and only the later rows are inserted. The writer checks that its merges leave those ids unchanged and that the index has room, and builds a new index otherwise. No pipeline caller passes it: it is the form of the carry-and-append absorb that `rebuild/test_conform.py` and the memory series' absorbs holder prove `absorb_settle_memo`'s bytes against.

    The file is written to `<path>.<pid>.tmp` (`FinishedSettleMemo.stage`), each section as one write of a view over its array, and moved into place with `os.replace` (`_put_settle_memo`). A reader that mapped the old file keeps it unchanged until it closes, and a reader that opens afterward maps the whole new file, so no reader sees a partial file. `path` defaults to `memo.path`. If the filesystem refuses the write, this prints a warning and returns False without failing the build, because every reader settles what the file lacks.
    """
    return _put_settle_memo(
        _settled_memo(memo, labels, outcomes, columns, values, standing, distinct),
        memo.path if path is None else Path(path),
    )


_SettleMemoBlock = tuple[list[str], list[Settled], list[array], array]


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


def _fresh_memo(items: Iterable[tuple[_Window, _Outcome]]) -> FreshMemo:
    """A walk's fresh windows as the part it hands its absorb (`FreshMemo`): each of `_memo_blocks`' blocks as its head and its rows, compressed one block at a time."""
    blocks: list[bytes] = []
    rows = labels = records = 0
    for new_labels, new_items, columns, values in _memo_blocks(items):
        count = len(values)
        blocks.append(zlib.compress(pickle.dumps((new_labels, new_items, count), protocol=5), 1))
        packer = zlib.compressobj(1)
        packed = [packer.compress(column) for column in (*columns, values)]
        packed.append(packer.flush())
        blocks.append(b"".join(packed))
        rows += count
        labels += len(new_labels)
        records += len(new_items)
    return FreshMemo(blocks, rows, labels, records)


# Rows a piece when `absorb_settle_memo` copies, widens, filters, patches or renumbers a section a block at a time: a quarter of a megabyte of `I` ids, so each piece stays well under the size of block the spawn worker's allocator reuses from one block to the next.
_ABSORB_BLOCK_ROWS = 1 << 16
_FRESH_MEMO_ERRORS = (
    zlib.error,
    pickle.UnpicklingError,
    EOFError,
    ValueError,
    TypeError,
    IndexError,
    OverflowError,
)


@dataclass(frozen=True)
class SweepPrune:
    """What the pooled conformance sweep's parent tells its configuration's absorb about the units' use of the file (`absorb_settle_memo`): `identity`, the file version every unit mapped (`_MemoStore.identity`); `reached`, the zlib-compressed union of the units' `MemoReach.reached` flags, one byte per row; `prune`, False when the units disagree about the version they mapped; and `discard`, True when a unit's probe retired its store (`MemoReach.retired`)."""

    identity: tuple[int, int, int, int] | None
    reached: bytes
    prune: bool
    discard: bool


@dataclass(frozen=True)
class MemoAbsorb:
    """What `absorb_settle_memo` did with one configuration's file. `written` says whether it wrote one, `carried` counts the standing rows it kept, `pruned` the live rows the sweep's prune or discard dropped, and `fresh` the rows the parts added to the file once each window that an earlier row already holds merged onto that row, or the rows the parts held when nothing was written. `standing` counts the standing file's rows, dead ones included (0 when it loaded nothing), `parts` the rows the parts held, `patched` the rows that restated a window an earlier row holds and gave that row their outcome instead of taking a row of their own, `index` whether the standing index was copied and extended (`reused`) or built anew (`rebuilt`), None when no file was laid out, and `slots` its slot count."""

    written: bool
    carried: int
    pruned: int
    fresh: int
    standing: int = 0
    parts: int = 0
    patched: int = 0
    index: str | None = None
    slots: int = 0

    @property
    def dropped(self) -> int:
        """The standing rows not carried: the stale ones, and those the sweep's prune or discard dropped."""
        return self.standing - self.carried

    def tokens(self, fresh: bool = True) -> str:
        """The path and counts a `[t]` line reports: `index=`, then the standing, kept, fresh (left out with `fresh` False, for a line that already has one), patched and dropped rows."""
        counts = [("standing", self.standing), ("kept", self.carried)]
        if fresh:
            counts.append(("fresh", self.fresh))
        counts += [("patched", self.patched), ("dropped", self.dropped)]
        return " ".join([f"index={self.index}", *(f"{name}={count}" for name, count in counts)])


def absorb_settle_memo(
    memo: SettleMemoFile,
    spec: ResolvedSpec,
    parts: Sequence[FreshMemo],
    sweep: SweepPrune | None = None,
    emit: Callable[[FinishedSettleMemo], bool] | None = None,
) -> MemoAbsorb:
    """Merge `parts`, the fresh windows of the walks that may not replace the shared file (`SettleMemoFile.returns_fresh`: the pooled oracle's row ranges in range order, the pooled conformance sweep's units in unit-slot order, or the witness stage's walk), into one configuration's settle memo file, and return what it did (`MemoAbsorb`). This is the one absorb of fresh windows: `run_m1_gates.run_oracle` and `run_m1_gates.run_font_conformance` run it in a fresh pool process a configuration, and `run_m1.run_rule_witnesses` in the witness stage's own process.

    It maps the standing file read-only and unrestricted (`_MemoStore.load`), so a file that is absent, under another stamp or unreadable contributes nothing, and a row naming a family whose key changed is dead. With `sweep`, the pooled conformance sweep's parent says which rows its units reached: together the units walk every text of the configuration, so a live row none of them reached is a window no text produces any more, and it is dropped, as the serial sweep's `save_memo(prune=True)` drops it. When the file on disk is not the version the units mapped, or its row count differs from the flags', the flags index another version's rows, so this warns and keeps every live row, as it does when `sweep.prune` is False; rows are never lost, only the prune waits for the next sweep. `sweep.discard`, after a unit's probe retired its store, drops every live row under the same version check and keeps only the parts, as the serial sweep's pruning save does after a retirement. With no part and nothing dropped this writes nothing, so the file's bytes and modification time stand.

    It reads every part's block heads first, before any row: the labels and records each block introduces, and its row count. So it merges the tables (`_memo_tables` over the standing tables and every part's, in order), knows the file's typecodes, and preallocates the fresh rows' seven columns from the counts (`_zeroed_column`) before decoding a row, and then decodes each block's rows once, into the file's id space and typecodes, a block at a time, calling each part's `release` once its last block is read. A block that will not decode, or names an id past its part's tables, warns and ends its part there, and the blocks before it stand; the tables are merged again without the blocks it dropped, so the file holds what it would hold had the part ended there.

    It builds the final index in heap before it writes anything, at the slot count `_memo_index` takes from the row count before repeated windows merge, so the index equals the one `_write_settle_memo` builds over the same rows. With no standing row dropped (no dead row, nothing pruned) and room for every row, it copies the standing index and inserts the fresh rows after the standing ones (`index=reused`). Otherwise it inserts the kept standing rows, in file order, and then the fresh rows, over the row numbers they will have (`index=rebuilt`), reading a kept standing row's key through the mapped columns by way of a map of the kept rows' standing row numbers, 4 bytes a kept row. A row whose window an earlier row holds, standing or fresh, takes no slot and gives that row its outcome (a later row's outcome wins, as in a dict), as `_memo_index` merges it. Standing outcomes are patched from a list as their section is written, fresh ones in place.

    Then it writes the file once, in order, staged beside it and moved into place (`_put_settle_memo`, or `emit` in its place for a reader that hashes the file instead), through `_settle_memo_head` and `FinishedSettleMemo`, so its bytes are those `_write_settle_memo` writes over the standing file's carried rows and the parts' blocks appended with their ids shifted past what precedes them: each key column as the standing column's kept rows straight from the mapping (widened, or filtered through the kept map, a block at a time where they need it) followed by the fresh rows' column, the values with the patches applied a block at a time, and the index last, renumbered a block at a time past a merged row. It copies standing ids unchecked, so a corrupt standing id reaches the new file and is found there by the probe that reaches it, which retires the store (`_MemoStore._retire`), instead of raising `IndexError` here.

    It holds the standing file's `dead` and `reached` flags and its selector, 3 bytes a standing row, the kept map, the fresh rows at 14 bytes a row (`H` columns), the index at 4 bytes a slot, and each part's compressed blocks until they are read; the mapped standing pages are the page cache's, and count in its resident set as it reads them. Once a row merges onto an earlier one it also holds the merge's state over every row from the first merged one on, in the order before merging (`_Merged`): a keep flag a row, 1 byte on the heap, and, while the index is written, the renumbering's running counts, 4 bytes a row of anonymous memory it fills whole (`_index_pieces`), and from the first patch two patch columns, 8 bytes a patch as it fills them. A repeated window is decoded into the fresh columns and counted into the slot count before it merges, so windows that two parts both settled, as an all-fresh pass's ranges and units do, add 14 bytes a repeat to the fresh rows and can double the index.
    """
    store = _MemoStore()
    store.load(memo, spec, None, lambda item: (item, "", ""))
    released: set[int] = set()

    def release(part: FreshMemo) -> None:
        if id(part) not in released:
            released.add(id(part))
            if part.release is not None:
                part.release()

    try:
        return _absorb(memo, store, parts, sweep, emit, release)
    finally:
        store.close()
        for part in parts:
            release(part)


def _sweep_drops(memo: SettleMemoFile, store: _MemoStore, sweep: SweepPrune | None) -> int:
    """Mark the rows `sweep` keeps as reached in `store` and return the live rows that drops (`absorb_settle_memo`)."""
    if sweep is None or not sweep.prune:
        return 0
    flags = zlib.decompress(sweep.reached)
    if store.identity() != sweep.identity or len(flags) != len(store.reached):
        print(
            f"[warn] settle memo: {memo.path} changed after the conformance sweep's units mapped it; every live row is kept and the prune waits for the next sweep",
            file=sys.stderr,
            flush=True,
        )
        return 0
    if sweep.discard:
        return len(store)
    store.reached[:] = flags
    return len(store) - store.reached_count()


def _kept_rows(selector: bytearray, kept: int) -> memoryview:
    """The standing row number of each row `selector` keeps, in order, `I` ids in anonymous memory filled a block at a time."""
    rows = _zeroed_column("I", kept)
    at = 0
    for start in range(0, len(selector), _ABSORB_BLOCK_ROWS):
        block = array(
            "I",
            itertools.compress(
                range(start, start + _ABSORB_BLOCK_ROWS), selector[start : start + _ABSORB_BLOCK_ROWS]
            ),
        )
        rows[at : at + len(block)] = block
        at += len(block)
    return rows


class _FreshRows:
    """The parts' rows as `absorb_settle_memo` decodes them: each part's block heads (`heads`), the tables merged over the standing file's and every part's (`tables`), and the six key columns and the value column in those tables' id space and typecodes (`columns`, `values`), `rows` long."""

    def __init__(
        self,
        memo: SettleMemoFile,
        store: _MemoStore,
        parts: Sequence[FreshMemo],
        release: Callable[[FreshMemo], None],
    ) -> None:
        self._memo = memo
        self._labels = store.labels
        self._items = [outcome[0] for outcome in store.outcomes]
        self._parts = parts
        self._release = release
        self.heads = [self._read_heads(part) for part in parts]
        self.tables = self._merge()
        total = sum(count for heads in self.heads for _labels, _items, count in heads)
        self.columns = [_zeroed_column(self.tables.typecode, total) for _ in range(6)]
        self.values = _zeroed_column(self.tables.value_typecode, total)
        self.rows = 0
        self._decode()

    def _warn(self, rows: int, error: object) -> None:
        print(
            f"[warn] settle memo: a part for {self._memo.path} stopped reading after {rows} windows ({error}); the rest are settled again",
            file=sys.stderr,
            flush=True,
        )

    def _read_heads(self, part: FreshMemo) -> list[tuple[list[str], list[Settled], int]]:
        heads: list[tuple[list[str], list[Settled], int]] = []
        blocks = part.blocks
        rows = 0
        for at in range(0, len(blocks), 2):
            try:
                if at + 1 >= len(blocks):
                    raise ValueError("a block's head with no rows behind it")
                new_labels, new_items, count = pickle.loads(zlib.decompress(blocks[at]))
                if (
                    not isinstance(new_labels, list)
                    or not isinstance(new_items, list)
                    or not isinstance(count, int)
                    or count < 0
                ):
                    raise ValueError("a malformed block head")
            except _FRESH_MEMO_ERRORS as error:
                self._warn(rows, f"{error}" or type(error).__name__)
                break
            heads.append((new_labels, new_items, count))
            rows += count
        return heads

    def _merge(self) -> _MemoTables:
        labels = list(self._labels)
        items = list(self._items)
        for heads in self.heads:
            for new_labels, new_items, _count in heads:
                labels.extend(new_labels)
                items.extend(new_items)
        return _memo_tables(labels, items)

    def _decode(self) -> None:
        label_base, item_base = len(self._labels), len(self._items)
        for part, heads in zip(self._parts, self.heads):
            part_labels = sum(len(new_labels) for new_labels, _items, _count in heads)
            part_items = sum(len(new_items) for _labels, new_items, _count in heads)
            canon = self.tables.canon[label_base : label_base + part_labels]
            canon_outcomes = self.tables.canon_outcomes[item_base : item_base + part_items]
            started = self.rows
            for block, (_labels, _items, count) in enumerate(heads):
                try:
                    self._decode_block(part.blocks[2 * block + 1], count, canon, canon_outcomes)
                except _FRESH_MEMO_ERRORS as error:
                    self._warn(self.rows - started, f"{error}" or type(error).__name__)
                    del heads[block:]
                    self._remerge()
                    part_labels = sum(len(new_labels) for new_labels, _items, _count in heads)
                    part_items = sum(len(new_items) for _labels, new_items, _count in heads)
                    break
                self.rows += count
            label_base += part_labels
            item_base += part_items
            self._release(part)

    def _decode_block(
        self, piece: bytes | memoryview, count: int, canon: Sequence[int], canon_outcomes: Sequence[int]
    ) -> None:
        raw = zlib.decompress(piece)
        width = array("I").itemsize
        if len(raw) != 7 * count * width:
            raise ValueError(f"a block of {len(raw)} row bytes where its head counts {count} rows")
        flat = memoryview(raw).cast("I")
        at = self.rows
        typecode, value_typecode = self.tables.typecode, self.tables.value_typecode
        for slot, column in enumerate(self.columns):
            column[at : at + count] = array(
                typecode, map(canon.__getitem__, flat[slot * count : (slot + 1) * count])
            )
        self.values[at : at + count] = array(
            value_typecode, map(canon_outcomes.__getitem__, flat[6 * count :])
        )

    def _remerge(self) -> None:
        """Merge the tables again after a part ended early, and move the rows decoded so far into the new typecodes when the merge narrowed them. A row decoded so far names only labels and records listed before the part's dropped blocks, whose ids the merge leaves as they were."""
        before = self.tables
        self.tables = self._merge()
        total = sum(count for heads in self.heads for _labels, _items, count in heads)
        if self.tables.typecode != before.typecode:
            self.columns = [_moved(column, self.tables.typecode, self.rows, total) for column in self.columns]
        if self.tables.value_typecode != before.value_typecode:
            self.values = _moved(self.values, self.tables.value_typecode, self.rows, total)


def _moved(column: memoryview, typecode: _MemoTypecode, rows: int, total: int) -> memoryview:
    moved = _zeroed_column(typecode, total)
    for start in range(0, rows, _ABSORB_BLOCK_ROWS):
        stop = min(start + _ABSORB_BLOCK_ROWS, rows)
        moved[start:stop] = array(typecode, column[start:stop])
    return moved


@dataclass
class _Merged:
    """The rows `absorb_settle_memo`'s index merged onto an earlier row: `first`, the row number before merging of the first one, `keep`, a flag a row from `first` on, cleared for each merged row, and the patches to standing outcomes, each an earlier standing row and the outcome it takes, in the order they were made."""

    rows: int
    first: int | None = None
    keep: bytearray = field(default_factory=bytearray)
    patch_rows: memoryview | None = None
    patch_values: memoryview | None = None
    patches: int = 0
    merged: int = 0

    def drop(self, row: int) -> None:
        if self.first is None:
            self.first = row
            self.keep = bytearray(b"\x01") * (self.rows - row)
        self.keep[row - self.first] = 0
        self.merged += 1

    def patch(self, row: int, value: int) -> None:
        if self.patch_rows is None or self.patch_values is None:
            self.patch_rows = _zeroed_column("I", self.rows)
            self.patch_values = _zeroed_column("I", self.rows)
        self.patch_rows[self.patches] = row
        self.patch_values[self.patches] = value
        self.patches += 1

    def kept(self, block: Sequence[int], typecode: _MemoTypecode, start: int) -> Sequence[int]:
        """`block`, the rows from `start` in the order before merging, without its merged rows."""
        first = self.first
        if first is None or start + len(block) <= first:
            return block
        lead = max(first - start, 0)
        flags = self.keep[start + lead - first : start + len(block) - first]
        return array(typecode, itertools.chain(block[:lead], itertools.compress(block[lead:], flags)))


def _absorb(
    memo: SettleMemoFile,
    store: _MemoStore,
    parts: Sequence[FreshMemo],
    sweep: SweepPrune | None,
    emit: Callable[[FinishedSettleMemo], bool] | None,
    release: Callable[[FreshMemo], None],
) -> MemoAbsorb:
    standing = len(store.dead)
    pruned = _sweep_drops(memo, store, sweep)
    if not parts and not pruned:
        return MemoAbsorb(False, 0, 0, 0, standing=standing)
    selector = store.selector(True if pruned else None)
    carried = selector.count(1)
    kept = None if carried == standing else _kept_rows(selector, carried)
    del selector
    fresh = _FreshRows(memo, store, parts, release)
    tables = fresh.tables
    outcomes = len(store.outcomes)
    value_canon = (
        None
        if tables.canon_outcomes[:outcomes] == list(range(outcomes))
        else tables.canon_outcomes[:outcomes]
    )
    rows = carried + fresh.rows
    reuse = not pruned and kept is None and value_canon is None and len(store.index) >= 2 * rows
    slots = len(store.index) if reuse else (1 << (2 * rows - 1).bit_length() if rows else 0)
    index = _zeroed_column("I", slots)
    if reuse and slots:
        index[:] = cast(_MemoColumn, store.index)
    merged = _Merged(rows)
    standing_columns = store.columns or [array("I")] * 6
    fresh_columns = [column[: fresh.rows] for column in fresh.columns]
    fresh_values = fresh.values[: fresh.rows]
    _absorb_index(
        index,
        standing_columns,
        store.values,
        kept,
        carried,
        fresh_columns,
        fresh_values,
        value_canon,
        merged,
        reuse,
    )
    final = rows - merged.merged
    header, pickled = _settle_memo_head(memo, tables, final, slots)
    typecode, value_typecode = tables.typecode, tables.value_typecode
    key_width, value_width = array(typecode).itemsize, array(value_typecode).itemsize
    sections: list[_MemoColumn | _MemoPieces] = [
        _MemoPieces(
            final * key_width,
            functools.partial(_column_pieces, standing_column, kept, carried, fresh_column, typecode, merged),
        )
        for standing_column, fresh_column in zip(standing_columns, fresh_columns)
    ]
    sections.append(
        _MemoPieces(
            final * value_width,
            functools.partial(
                _value_pieces, store.values, kept, carried, fresh_values, value_typecode, value_canon, merged
            ),
        )
    )
    sections.append(
        index
        if merged.first is None
        else _MemoPieces(slots * 4, functools.partial(_index_pieces, index, merged))
    )
    finished = FinishedSettleMemo(header, pickled, tuple(sections), final)
    written = (emit or functools.partial(_put_settle_memo, path=memo.path))(finished)
    return MemoAbsorb(
        written,
        carried,
        pruned,
        final - carried if written else fresh.rows,
        standing=standing,
        parts=fresh.rows,
        patched=merged.merged,
        index="reused" if reuse else "rebuilt",
        slots=slots,
    )


def _absorb_index(
    index: memoryview,
    standing: Sequence[Sequence[int]],
    standing_values: Sequence[int],
    kept: Sequence[int] | None,
    carried: int,
    fresh: Sequence[Sequence[int]],
    fresh_values: memoryview,
    value_canon: Sequence[int] | None,
    merged: _Merged,
    reused: bool,
) -> None:
    """Insert the rows into `index` as `_memo_index` inserts them: the kept standing rows first, unless `reused` says `index` already holds them at their row numbers, and then the fresh rows, each probed linearly from its `_memo_slots` slot. A row whose key an earlier row holds takes no slot; its outcome patches that row's (`_Merged`)."""
    slots = len(index)
    mask = slots - 1
    s0, s1, s2, s3, s4, s5 = standing
    f0, f1, f2, f3, f4, f5 = fresh
    if not reused and carried:
        sources = standing if kept is None else [map(column.__getitem__, kept) for column in standing]
        for row, slot in enumerate(_memo_slots(sources, slots)):
            while True:
                other = index[slot]
                if not other:
                    index[slot] = row + 1
                    break
                other -= 1
                at = other if kept is None else kept[other]
                here = row if kept is None else kept[row]
                if (
                    s0[at] == s0[here]
                    and s1[at] == s1[here]
                    and s2[at] == s2[here]
                    and s3[at] == s3[here]
                    and s4[at] == s4[here]
                    and s5[at] == s5[here]
                ):
                    value = standing_values[here]
                    if value_canon is not None and value < len(value_canon):
                        value = value_canon[value]
                    merged.patch(other, value)
                    merged.drop(row)
                    break
                slot = (slot + 1) & mask
    for offset, slot in enumerate(_memo_slots(fresh, slots)):
        row = carried + offset
        while True:
            other = index[slot]
            if not other:
                index[slot] = row + 1
                break
            other -= 1
            if other < carried:
                at = other if kept is None else kept[other]
                if (
                    s0[at] == f0[offset]
                    and s1[at] == f1[offset]
                    and s2[at] == f2[offset]
                    and s3[at] == f3[offset]
                    and s4[at] == f4[offset]
                    and s5[at] == f5[offset]
                ):
                    merged.patch(other, fresh_values[offset])
                    merged.drop(row)
                    break
            else:
                at = other - carried
                if (
                    f0[at] == f0[offset]
                    and f1[at] == f1[offset]
                    and f2[at] == f2[offset]
                    and f3[at] == f3[offset]
                    and f4[at] == f4[offset]
                    and f5[at] == f5[offset]
                ):
                    fresh_values[at] = fresh_values[offset]
                    merged.drop(row)
                    break
            slot = (slot + 1) & mask


def _typecode_of(column: Sequence[int]) -> str:
    return column.format if isinstance(column, memoryview) else cast(array, column).typecode


def _column_pieces(
    standing: Sequence[int],
    kept: Sequence[int] | None,
    carried: int,
    fresh: Sequence[int],
    typecode: _MemoTypecode,
    merged: _Merged,
) -> Iterator[_MemoColumn]:
    """One key column's section: the standing column's kept rows straight from the mapping, whole where no row of it is dropped, merged or widened, and otherwise a block at a time; then the fresh rows' column, whole up to the first merged row and filtered a block at a time from there."""
    standing_whole = (
        kept is None
        and (merged.first is None or merged.first >= carried)
        and _typecode_of(standing) == typecode
    )
    if standing_whole:
        if carried:
            yield cast(_MemoColumn, standing[:carried])
    else:
        for start in range(0, carried, _ABSORB_BLOCK_ROWS):
            stop = min(start + _ABSORB_BLOCK_ROWS, carried)
            if kept is None:
                block = array(typecode, standing[start:stop])
            else:
                block = array(typecode, map(standing.__getitem__, kept[start:stop]))
            yield cast(_MemoColumn, merged.kept(block, typecode, start))
    yield from _fresh_pieces(fresh, carried, typecode, merged)


def _fresh_pieces(
    fresh: Sequence[int], carried: int, typecode: _MemoTypecode, merged: _Merged
) -> Iterator[_MemoColumn]:
    first = merged.first
    rows = len(fresh)
    lead = rows if first is None else max(min(first - carried, rows), 0)
    if lead:
        yield cast(_MemoColumn, fresh[:lead])
    for start in range(lead, rows, _ABSORB_BLOCK_ROWS):
        stop = min(start + _ABSORB_BLOCK_ROWS, rows)
        yield cast(_MemoColumn, merged.kept(fresh[start:stop], typecode, carried + start))


def _value_pieces(
    standing: Sequence[int],
    kept: Sequence[int] | None,
    carried: int,
    fresh: Sequence[int],
    typecode: _MemoTypecode,
    value_canon: Sequence[int] | None,
    merged: _Merged,
) -> Iterator[_MemoColumn]:
    """The value column's section: the standing outcomes as `_column_pieces` lays a key column out, with each patch applied in the order it was made, so a later patch to a row wins, and the fresh outcomes, already patched in place."""
    patches = merged.patches
    rows, values = merged.patch_rows, merged.patch_values
    order = sorted(range(patches), key=cast(memoryview, rows).__getitem__) if patches else []
    if not patches and value_canon is None:
        yield from _column_pieces(standing, kept, carried, fresh, typecode, merged)
        return
    at = 0
    for start in range(0, carried, _ABSORB_BLOCK_ROWS):
        stop = min(start + _ABSORB_BLOCK_ROWS, carried)
        source = standing[start:stop] if kept is None else map(standing.__getitem__, kept[start:stop])
        if value_canon is not None:
            source = (value_canon[value] if value < len(value_canon) else value for value in source)
        block = array(typecode, source)
        while at < len(order) and cast(memoryview, rows)[order[at]] < stop:
            block[cast(memoryview, rows)[order[at]] - start] = cast(memoryview, values)[order[at]]
            at += 1
        yield cast(_MemoColumn, merged.kept(block, typecode, start))
    yield from _fresh_pieces(fresh, carried, typecode, merged)


def _index_pieces(index: memoryview, merged: _Merged) -> Iterator[_MemoColumn]:
    """The index renumbered past the merged rows a block at a time: a slot naming a row before the first merged one keeps it, and one naming a later row takes that row's place among the rows kept (`_Merged.keep` counted up to it)."""
    first = cast(int, merged.first)
    counts = _zeroed_column("I", len(merged.keep) + 1)
    total = 0
    for start in range(0, len(merged.keep), _ABSORB_BLOCK_ROWS):
        block = array(
            "I", itertools.accumulate(merged.keep[start : start + _ABSORB_BLOCK_ROWS], initial=total)
        )
        counts[start : start + len(block)] = block
        total = block[-1]
    for start in range(0, len(index), _ABSORB_BLOCK_ROWS):
        block = index[start : start + _ABSORB_BLOCK_ROWS]
        yield array(
            "I",
            map(
                operator.add,
                map(min, block, itertools.repeat(first)),
                map(
                    counts.__getitem__,
                    map(operator.sub, map(max, block, itertools.repeat(first)), itertools.repeat(first)),
                ),
            ),
        )


def _ambiguous_ids(spelling: Sequence[str], used: Iterable[int]) -> set[int]:
    """Among the ids in `used`, every id whose label another id in `used` shares. Only through these ids can two distinct crate keys become one walk key."""
    first: dict[str, int] = {}
    ambiguous: set[int] = set()
    for label_id in used:
        other = first.setdefault(spelling[label_id], label_id)
        if other != label_id:
            ambiguous.update((label_id, other))
    return ambiguous


def _replay_memo_head(
    head_line: bytes, name: object, config: str
) -> tuple[int, int, int, int, _MemoTypecode]:
    """A window memo's head line, its newline stripped, checked and read as the row, label and record counts, the column width and its typecode. Another format token, a malformed head or another configuration raises `KernelRunError`."""
    marker, _, head_json = head_line.decode(errors="replace").partition("\t")
    if marker != f"# {kernel_exec.REPLAY_MEMO_FORMAT}":
        raise kernel_exec.KernelRunError(
            f"{name} is not a {kernel_exec.REPLAY_MEMO_FORMAT} window memo: {marker!r}"
        )
    try:
        head = json.loads(head_json)
    except ValueError as error:
        raise kernel_exec.KernelRunError(f"{name} carries a malformed head: {error}") from None
    if (
        not isinstance(head, dict)
        or {"config", "max_length", "rows", "labels", "records", "width"} - head.keys()
    ):
        raise kernel_exec.KernelRunError(f"{name} carries a malformed head: {head!r}")
    if head["config"] != config:
        raise kernel_exec.KernelRunError(f"{name} names {head['config']!r} where {config!r} was expected")
    try:
        rows, label_count, record_count, width = (
            int(head[key]) for key in ("rows", "labels", "records", "width")
        )
    except TypeError, ValueError:
        raise kernel_exec.KernelRunError(f"{name} carries a malformed head: {head!r}") from None
    typecode = _MEMO_TYPECODE_OF_WIDTH.get(width)
    if typecode is None or array(typecode).itemsize != width or min(rows, label_count, record_count) < 0:
        raise kernel_exec.KernelRunError(f"{name} carries a malformed head: {head!r}")
    return rows, label_count, record_count, width, typecode


def _replay_memo_tables(
    label_lines: Sequence[bytes], record_lines: Sequence[bytes], spec: ResolvedSpec, config: str, name: object
) -> tuple[list[str], list[Settled]]:
    """A window memo's label table and records in the walk's id space: each label renamed through `model.raw_rename_map` once, then each record's `geometry.display_name`, which is the id space the memo's left column indexes, beside the records decoded through `kernel_exec.settled_of_row`."""
    renames = raw_rename_map(spec, features_for_config(config))
    try:
        labels = [renames.get(label, label) for label in (line.decode() for line in label_lines)]
        records = [kernel_exec.settled_of_row(json.loads(line)) for line in record_lines]
        labels += [geometry.display_name(spec, item.cell) for item in records]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as error:
        raise kernel_exec.KernelRunError(f"{name} carries a table this spec cannot read: {error}") from None
    return labels, records


def _check_respelled_rows(
    columns: Sequence[Sequence[int]],
    values: Sequence[int],
    labels: Sequence[str],
    ambiguous_lefts: set[int],
    ambiguous_slots: set[int],
    name: object,
) -> None:
    """Raise `KernelRunError` when two rows that use an ambiguous id (`_ambiguous_ids`), a left in `ambiguous_lefts` or another slot in `ambiguous_slots`, spell the same walk key and settle differently."""
    seen: dict[tuple[str, ...], int] = {}
    for row in zip(*columns, values):
        if row[1] in ambiguous_lefts or any(row[slot] in ambiguous_slots for slot in (0, 2, 3, 4, 5)):
            key = tuple(map(labels.__getitem__, row[:6]))
            if seen.setdefault(key, row[6]) != row[6]:
                raise kernel_exec.KernelRunError(
                    f"{name} settles the window {key!r} two ways once respelled; nothing in it can be trusted"
                )


def absorb_replay_memo(dump: Path, memo: SettleMemoFile, spec: ResolvedSpec, config: str) -> int:
    """Write the crate's window memo for `config`, a dump on disk that `replay-strings --memo-dir` wrote, as the configuration's settle memo file under `memo`'s stamp and family keys, and return the row count. This is the in-memory form of the absorb: it reads the whole dump, slices its seven columns out of it, and writes them through `_write_settle_memo`. The build's replay reads each memo through a FIFO as it arrives instead (`read_replay_memo`), and gives the same file's bytes from the same dump.

    The dump names every window in the crate's form: the input as the raw rune name or its `.noentry` locked copy, the rights as raw labels, and the left as a boundary label or an index into its record table. This function converts that to the `formed_labels` form. Each label is renamed through `model.raw_rename_map` once, each record is decoded through `kernel_exec.settled_of_row` once, and each record's left label is its `geometry.display_name` (`_replay_memo_tables`). The conversion touches only a few thousand strings, and the rows pass through as integer columns: the walk's label table is the renamed label table followed by the records' display names, which is the id space the dump's left column already indexes.

    A dump with another format token, a malformed head, another configuration, or a row byte count that does not match its head raises `KernelRunError` and writes nothing. Two crate keys can become one walk key only through two ids with the same label, so the row-level check runs only over rows that use such an id (`_check_respelled_rows`). If two such rows settle differently, this raises instead of writing the file, because a wrong memo would give wrong results, while a missing one only makes the readers settle the windows themselves.
    """
    data = dump.read_bytes()
    head_line, _, _ = data.partition(b"\n")
    rows, label_count, record_count, width, typecode = _replay_memo_head(head_line, dump, config)
    parts = data.split(b"\n", 1 + label_count + record_count)
    body = parts[-1] if len(parts) == 2 + label_count + record_count else b""
    if len(body) != rows * 7 * width:
        raise kernel_exec.KernelRunError(
            f"{dump} holds {len(body)} row bytes where its head counts {rows} rows of {7 * width}"
        )
    labels, records = _replay_memo_tables(
        parts[1 : 1 + label_count],
        parts[1 + label_count : 1 + label_count + record_count],
        spec,
        config,
        dump,
    )
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
            raise kernel_exec.KernelRunError(f"{dump} indexes a record past its table")
        ambiguous_lefts = _ambiguous_ids(labels, set(columns[1]))
        ambiguous_slots = _ambiguous_ids(
            labels, set().union(*(set(columns[slot]) for slot in (0, 2, 3, 4, 5)))
        )
        if ambiguous_lefts or ambiguous_slots:
            _check_respelled_rows(columns[:6], columns[6], labels, ambiguous_lefts, ambiguous_slots, dump)

    if not _write_settle_memo(memo, labels, records, columns[:6], columns[6]):
        raise kernel_exec.KernelRunError(f"{memo.path} could not be written")
    return rows


@dataclass(frozen=True)
class ReplayMemoRows:
    """One configuration's window memo read into memory by `read_replay_memo`: the settle memo file's merged tables (`_MemoTables`), its six key columns and its value column, already in those tables' id space and typecodes, each in anonymous memory of its own (`_zeroed_column`), and the memo's row count. `finish` indexes them and lays the file out (`_finish_settle_memo`)."""

    tables: _MemoTables
    columns: tuple[memoryview, ...]
    values: memoryview
    rows: int

    def finish(self, memo: SettleMemoFile) -> FinishedSettleMemo:
        return _finish_settle_memo(memo, self.tables, self.columns, self.values)


def _memo_line(stream: IO[bytes], name: object) -> bytes:
    line = stream.readline(_REPLAY_MEMO_LINE_CAP)
    if not line.endswith(b"\n"):
        raise kernel_exec.KernelRunError(f"{name} ends or overruns a line inside its head or tables")
    return line[:-1]


def _remapped(source: memoryview, canon: Sequence[int] | None, typecode: _MemoTypecode) -> memoryview:
    """One block's slice of a column in the file's id space and typecode: `source` mapped through `canon` when the merge moves an id, else `source` itself when its typecode is the file's, else a converted copy."""
    if canon is not None:
        return memoryview(array(typecode, map(canon.__getitem__, source)))
    if source.format == typecode:
        return source
    return memoryview(array(typecode, source))


def read_replay_memo(stream: IO[bytes], spec: ResolvedSpec, config: str, name: object) -> ReplayMemoRows:
    """Read the crate's window memo for `config` from `stream` as it arrives, a FIFO the crate writes in the build's replay (`run_m1.run_replay_strings`), into the columns of its settle memo file, and return them (`ReplayMemoRows`). `name` names the memo in a refusal. It reads the head and the two tables first, so before any row it knows the row count, both typecodes, the label merge (`canon`), the outcome merge and the slot count (`_memo_tables`; `absorb_replay_memo` says how the tables convert). It preallocates the seven columns from the head's row count in anonymous memory (`_zeroed_column`), whose pages the system maps only as the rows that fill them arrive, and reads the rows `REPLAY_MEMO_BLOCK_ROWS` at a time, checking each block's ids against the tables and de-interleaving it into the columns, the six key columns through `canon` and the value column through the outcome merge wherever `_write_settle_memo` would move an id, so no column grows, no row passes through Python one at a time, and the rows the crate frees a chunk at a time as it writes them are held about once between the two processes, never twice.

    The tables alone repeat record display names, so whether two crate keys can become one walk key is decided by the ids the rows use, by role, lefts apart from the other slots (`_ambiguous_ids`). Once every row is in heap, and before anything is laid out, that check runs; a hit sends the rows through `absorb_replay_memo`'s row check over the same columns (`_check_respelled_rows`), and `ReplayMemoRows.finish` then merges any repeated walk key as `_write_settle_memo` does. So the file this gives is the one `absorb_replay_memo` writes from the same dump, byte for byte. A stream with another format token, a malformed head, another configuration, an id past its table, fewer or more row bytes than its head counts, or two rows that settle one walk key differently raises `KernelRunError` and leaves nothing. A reader that raises closes the stream, which breaks the crate's pipe at its next write.
    """
    rows, label_count, record_count, width, dump_typecode = _replay_memo_head(
        _memo_line(stream, name), name, config
    )
    lines = [_memo_line(stream, name) for _ in range(label_count + record_count)]
    labels, records = _replay_memo_tables(lines[:label_count], lines[label_count:], spec, config, name)
    del lines
    tables = _memo_tables(labels, records)
    columns = tuple(_zeroed_column(tables.typecode, rows) for _ in range(6))
    values = _zeroed_column(tables.value_typecode, rows)
    targets = [*columns, values]
    label_canon = tables.canon if tables.merges_labels else None
    outcome_canon = tables.canon_outcomes if tables.merges_outcomes else None
    track_lefts = len(set(labels)) < len(labels)
    track_slots = len(set(labels[:label_count])) < label_count
    used_lefts: set[int] = set()
    used_slots: set[int] = set()
    row_bytes = 7 * width
    done = 0
    while done < rows:
        count = min(rows - done, REPLAY_MEMO_BLOCK_ROWS)
        size = count * row_bytes
        block = stream.read(size)
        if len(block) != size:
            raise kernel_exec.KernelRunError(
                f"{name} holds {done * row_bytes + len(block)} row bytes where its head counts {rows} rows of {row_bytes}"
            )
        if sys.byteorder == "little":
            flat = memoryview(block).cast(dump_typecode)
        else:
            swapped = array(dump_typecode)
            swapped.frombytes(block)
            swapped.byteswap()
            flat = memoryview(swapped)
        slots = [flat[slot::7] for slot in range(7)]
        if max(max(slots[slot]) for slot in (0, 2, 3, 4, 5)) >= label_count:
            raise kernel_exec.KernelRunError(f"{name} indexes a label past its table")
        if max(slots[1]) >= len(labels) or max(slots[6]) >= record_count:
            raise kernel_exec.KernelRunError(f"{name} indexes a record past its table")
        if track_lefts:
            used_lefts.update(slots[1])
        if track_slots:
            for slot in (0, 2, 3, 4, 5):
                used_slots.update(slots[slot])
        for slot in range(6):
            targets[slot][done : done + count] = _remapped(slots[slot], label_canon, tables.typecode)
        targets[6][done : done + count] = _remapped(slots[6], outcome_canon, tables.value_typecode)
        done += count
    if stream.read(1):
        raise kernel_exec.KernelRunError(
            f"{name} holds more row bytes than its head counts, {rows} rows of {row_bytes}"
        )
    ambiguous_lefts = _ambiguous_ids(labels, used_lefts)
    ambiguous_slots = _ambiguous_ids(labels, used_slots)
    if ambiguous_lefts or ambiguous_slots:
        canon = tables.canon
        _check_respelled_rows(
            columns,
            values,
            tables.table,
            {canon[label_id] for label_id in ambiguous_lefts},
            {canon[label_id] for label_id in ambiguous_slots},
            name,
        )
    return ReplayMemoRows(tables, columns, values, rows)


class _SettledWindowWalk:
    """The memoized settlement walk one configuration runs over its texts. A left-to-right pass computes each letter slot's raw window key (the slots `witness._matched_windows` reads, with the left taken from the stream settled so far) and looks it up in `windows`, a window -> (Settled, glyph name, left label) memo. Only a miss reaches the crate.

    The memo only saves time. It records no coverage, and the sweep's result is the same whether every window misses or every window hits. It is sound because every memoized outcome depends only on the window as keyed: the left label is the settled cell's display name (`geometry.display_name`, injective over every CellId field), and the right slots are exactly the raw tokens a case line carries. The key never reads the glyph inventory, so a walk with minted names and a walk without them build the same keys and differ only in the names they return, which is what lets the oracle and the conformance sweep share one memo file. Because the key holds all the raw right slots, the walk needs no liveness check; blanking the deep slots where the table's relevance filters show nothing reads them costs more in probes than it saves. Interned labels and shared outcome tuples limit the memo's cost to its key tuples. The walk-equivalence sweeps in rebuild/test_conform.py test all of this.

    Without `horizon`, `windows` has no size bound: it keeps every window the walk settles. A walk with no memo file and no dedupe audit may name `horizon`, the length in characters of its longest text, and then `windows` keeps every window except the pinned ones. A window is pinned when its right slots reach the text's last token, the slot after that token is `#EDGE` or the text is at the horizon, and its index is 0, or 1 with the text at the horizon (`_is_pinned`; positions count tokens after ligature formation). Its key then spells out its whole text, because the left slot is the edge, a boundary, or a display name that names the letter before. The conformance sweep walks each text once, so only the state that asked for a pinned window reaches it, and a text walked again would settle its pinned windows again. A pinned outcome goes into `_pinned`, which `_advance` reads after `windows` and the cold store, and `_run` clears it once the wave's states have advanced.

    A horizon walk may also name `max_windows`, a ceiling on `windows`. Before a wave whose settles could take `windows` past it, `_run` empties `windows` and counts a release in `releases`, and the walk continues, settling again any window it meets after that. Each state carries its own settled stream, so a release costs only the settles of windows met again, counted in `fresh_windows` with the rest. The ceiling is counted in windows, not bytes, so a release happens at the same point on every machine. It also caps `batch`, so one wave never settles more than the ceiling. A ceiling at the most entries a dict holds before it doubles keeps `windows` in one table, so the walk's peak has a bound that does not depend on how many texts it walks.

    `memo` names the file this walk shares with the other walks over the same texts. The string replay fills it on a full replay (`read_replay_memo`), and the witness stage, the oracle, and the conformance sweep each map it and settle what it lacks. It is mapped on the first wave that would otherwise reach the crate, so a walk that settles nothing (an oracle pass whose rows are all served) never opens it. `save_memo` writes it back only when this walk settled a window the file lacked, or pruned one. `_write_settle_memo` describes the file layout, and `_MemoStore` how a walk maps it: the tables go on the heap, and the columns and index stay pages of the mapping, shared in the page cache by every walk that maps the file. An entry whose window names a family whose key changed since the file was written is dropped at load (`oracle_cache.StaleMask` at label grain, including the ligature clause).

    Loaded entries stay in `_cold`, the `_MemoStore` over the mapping, which marks each row it returns as reached. With `promote` (a constructor keyword, on by default) a hit is also copied into `windows`, so the witness stage, which prefills its texts and then walks each one again, runs the store's probe once per window and a dict lookup after that. The oracle's walk turns off `promote_pinned`, a constructor keyword also on by default: a hit whose window spells out its whole text (`_is_pinned`) stays in the store, because that text alone reads it, so a copy would cost its key tuple and save no later probe. A text walked again probes the store again and hits, so nothing settles again. Every other hit is copied, and every window the walk settles fresh still goes into `windows`. `promoted_hits` counts the hits copied and `kept_hits` the hits left in the store. The conformance sweep runs with `promote=False` and serves every window from the columns: it reaches nearly every loaded window, so promoted copies would cost the key tuples the columns exist to avoid. `save_memo(prune=True)` keeps only the reached rows. The conformance sweep walks every text every pass, so an entry it never reached is a window no text produces any more (its left slot named a settlement an edit has since changed), and keeping it would grow the file with every rune edit. The serial sweep prunes in its own save. The pooled sweep's units each walk a share of the texts, so none of them may prune: each returns its fresh windows and its `reached` flags (`memo_reach`), and the parent prunes through `absorb_settle_memo` over the union of those flags. A pruning save writes the fresh windows first and then the reached rows in file order, a permutation of what a promoting walk would write; no reader can tell the difference, because a load does not depend on row order and the file holds one row per window. The oracle does not prune, because it does not walk the rows its row cache serves, so their windows are never reached. Its whole-file save, on the `--jobs 1` path, writes `windows` in the order the walk met them and then every other live row in file order. When its walk left a hit in the store it carries every live row, and `_memo_index` merges the carried copy of each row the walk copied into `windows` onto that entry, so the file still holds one row per window, with the same windows and outcomes, and an index of the same slot count, as a walk that copies every hit would write.

    A walk over a set of texts fixed before it runs can restrict the load to the windows those texts can ask for (`load_only_asked_by`). Only a window's left slot depends on settlement, so the other five slots of every window the texts reach can be computed in advance. A file row outside that set is marked dead at load, counted in `unasked_windows`, and never served. Such a walk cannot tell a dropped row from a window no text reaches, so it never prunes and never replaces the shared file. Its memo is `returns_fresh`: it encodes only the windows it settled fresh, in memory (`FreshMemo`), and its caller hands them to `absorb_settle_memo`, which merges them into the file.

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
        promote_pinned: bool = True,
        horizon: int | None = None,
        max_windows: int | None = None,
    ):
        assert horizon is None or (
            memo is None and not audit_dedupe
        ), "a horizon walk has no memo file and no dedupe audit"
        assert max_windows is None or (
            horizon is not None and max_windows >= 1
        ), "a window ceiling is for a horizon walk, and holds at least one window"
        self.spec = spec
        self.features = features
        self.glyph_names = glyph_names
        self.guard_verdicts = guard_verdicts
        self.batch = max(1, batch if max_windows is None else min(batch, max_windows))
        self.audit_dedupe = audit_dedupe
        self.on_error = on_error
        self.memo = memo
        self._promote = promote
        self._promote_pinned = promote_pinned
        self.horizon = horizon
        self.max_windows = max_windows
        self.releases = 0
        self.windows: dict[_Window, _Outcome | _RefusedWindow] = {}
        self._pinned: dict[_Window, _Outcome | _RefusedWindow] = {}
        self._cold = _MemoStore()
        self.single_settles = 0
        self.audit_extra_rows = 0
        self.audit_multi_keys: set[_Window] = set()
        self.memo_windows = 0
        self.stale_windows = 0
        self.unasked_windows = 0
        self.pruned_windows = 0
        self.fresh_windows = 0
        self.promoted_hits = 0
        self.kept_hits = 0
        self.memo_seconds = 0.0
        self._memo_loaded = False
        self._asks: set[_Ask] | None = None
        self._refused = 0
        self._outcomes: dict[Settled, _Outcome] = {}
        self._fresh: list[_Window] = []
        self.fresh_memo: FreshMemo | None = None
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
        """Restrict the memo load to the windows `texts` can ask for, and return that ask set. It holds, for every letter position of every text, the window's input and right slots (`_Ask`, the five settlement-independent slots of a `_Window`), computed through the same `_state` and `_window_rights` path `_window` uses. So it covers everything `prefill` and `walk` over these texts can reach: the left slot is the only one settlement decides, and a boundary position never reaches the memo. A row outside the set is dropped at load (`_load_memo`). A dropped row then looks the same as a window no text reaches, so a restricted walk returns its fresh windows instead of replacing the file (`save_memo`), and its memo must be `returns_fresh`. Call this before the first wave loads the file."""
        assert not self._memo_loaded, "the memo is already loaded"
        assert (
            self.memo is None or self.memo.returns_fresh
        ), "a restricted walk returns its fresh windows, never the file"
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
        assert self.horizon is None or len(text) <= self.horizon, (text, self.horizon)
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
                        if self._promote_pinned or not self._is_pinned(window, state):
                            self.windows[window] = outcome
                            self.promoted_hits += 1
                        else:
                            self.kept_hits += 1
                if outcome is None:
                    outcome = self._pinned.get(window)
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

    def _record(self, window: _Window, item: Settled | None, state: _WalkState) -> None:
        self.fresh_windows += 1
        if self.memo is not None and self.memo.returns_fresh:
            self._fresh.append(window)
        store = self._pinned if self.horizon is not None and self._is_pinned(window, state) else self.windows
        if item is None:
            self._refused += 1
            store[window] = _RefusedWindow(
                f"the kernel refused the window {window!r}, reached in {state.text!r}"
            )
            return
        store[window] = self._outcome(item)

    def _is_pinned(self, window: _Window, state: _WalkState) -> bool:
        """Whether `window`, which `state` asked for at its current position, spells out the whole text: its right slots reach the text's last token, the slot after that token is `#EDGE` or the text is at the horizon, and it sits at index 0, or at index 1 of a text at the horizon. Positions count tokens after ligature formation, and the horizon counts characters. A horizon walk asks it of each window it settles fresh, and a walk built with `promote_pinned=False` of each store hit. A walk with no horizon has no text at the horizon, so for it the test holds only at index 0, for a text of at most four tokens with no boundary inside it, when the slot after the last token is `#EDGE`."""
        index, labels = state.index, state.labels
        last = len(labels) - 1
        at_horizon = len(state.text) == self.horizon
        if index > 1 or (index == 1 and not at_horizon) or last > index + 4:
            return False
        if not _WINDOW_BOUNDARIES.isdisjoint(labels[index + 1 : last]):
            return False
        return at_horizon or (last < index + 4 and window[2 + last - index] == _EDGE_LABEL)

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

        The file holds `windows`, less its refusals, and then the loaded rows `windows` may lack. A walk that copied every hit into `windows` carries only the live rows it never reached, in file order. A walk that promotes nothing (`promote=False`), or left a hit whose window spells out its whole text in the store (`kept_hits`, under `promote_pinned=False`), carries every live row in file order, and `_memo_index` merges the carried copy of each row `windows` already holds onto that entry, so the file holds one row per window either way. Only the order differs, and a load does not depend on order. The carried copies are exactly the rows the walk copied into `windows` (`promoted_hits`) while its store still serves rows (a store a probe retired, or an earlier save closed, carries nothing), so the writer sizes the index from the rows less those copies, and the file's index has the slot count a walk that copies every hit would write.

        `prune` drops the loaded entries this walk never reached, counted in `pruned_windows`. It is only correct for a walk over every text, which is the serial conformance sweep's; a pooled sweep's unit walks a share of them and leaves the prune to `absorb_settle_memo`. A pruning walk carries only the reached rows, and only when `windows` may lack one: a pruning walk that promotes nothing writes its fresh windows and then the reached rows in file order. Rows are copied into the new file straight from the mapped columns, with their ids shifted past the fresh windows' labels, without a key tuple per row.

        A walk whose memo is `returns_fresh` writes nothing: it encodes the windows it settled fresh, in the order it settled them and less its refusals, as `fresh_memo` (`FreshMemo`), returns True when it settled any, and leaves the shared file to its caller's `absorb_settle_memo`. It keeps its mapping open for `memo_reach`. A walk that restricted its load (`load_only_asked_by`) is always of this kind and never prunes, because a row its load dropped looks the same as a window it never reached.
        """
        if prune:
            assert self._asks is None, "a restricted walk never prunes"
        if self.memo is None:
            return False
        if self.memo.returns_fresh:
            if not self.fresh_windows:
                return False
            started = time.perf_counter()
            fresh = (
                (window, outcome)
                for window in self._fresh
                if not isinstance(outcome := self.windows[window], _RefusedWindow)
            )
            try:
                self.fresh_memo = _fresh_memo(fresh)
                self._fresh = []
                return True
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
        holds_every_reached = self._promote and not self.kept_hits
        if prune:
            if not holds_every_reached:
                store.carry_into(labels, outcomes, columns, values, reached=True)
        else:
            store.carry_into(
                labels, outcomes, columns, values, reached=False if holds_every_reached else None
            )
        copies = self.promoted_hits if store.live and not holds_every_reached else 0
        try:
            return _write_settle_memo(
                self.memo, labels, outcomes, columns, values, distinct=len(values) - copies
            )
        finally:
            store.close()
            self.memo_seconds += time.perf_counter() - started

    def memo_line(self, config: str, written: bool) -> str | None:
        """This walk's `[t] settle_memo` line (`settle_memo_line`), or None for a walk with no memo."""
        if self.memo is None:
            return None
        return settle_memo_line(
            config,
            self.memo_seconds,
            self.memo_windows,
            self.stale_windows,
            self.fresh_windows,
            self.pruned_windows,
            written,
        )

    def memo_reach(self) -> MemoReach | None:
        """What this walk did with its memo file, for a unit of the pooled conformance sweep, which returns its fresh windows (`save_memo`, `fresh_memo`) and leaves the prune to the parent (`MemoReach`, `absorb_settle_memo`), or None for a walk with no memo. It then closes the mapping, which `save_memo` leaves open for a walk that returns its fresh windows."""
        if self.memo is None:
            return None
        store = self._cold
        try:
            return MemoReach(
                identity=store.identity(),
                retired=store.retired,
                rows=len(store.reached),
                live=len(store),
                loaded=self.memo_windows,
                stale=self.stale_windows,
                reached=zlib.compress(bytes(store.reached), 1),
                fresh=self.fresh_memo,
                seconds=self.memo_seconds,
            )
        finally:
            store.close()

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
            reached_in: list[_WalkState] = []
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
                reached_in.append(state)
                cases.append(
                    kernel_exec.case_line(state.left, state.tokens[state.index], self._rights(state))
                )
                if self.audit_dedupe:
                    self._audit_seen.add((state.left, state.tokens[state.index], self._rights(state)))
            if self.max_windows is not None and len(self.windows) + len(keys) > self.max_windows:
                self.windows.clear()
                self.releases += 1
            for window, asker, item in zip(keys, reached_in, self._settle(cases)):
                self._record(window, item, asker)
            pending = [state for state in pending if self._advance(state, tolerant)]
            self._pinned.clear()
            if self._audit_pending:
                self._drain_audit()
        if self._audit_pending:
            self._drain_audit()
        return [(state.settled, state.names) for state in states] if collect else []


class WitnessError(Exception):
    """A settlement rule whose certificate does not fire it: the certificate text, settled through the crate, fires some other rule or none at the input it names. That means the worklist's slot restrictions or the fold's rule order is wrong."""


@dataclass
class WitnessReport:
    """One configuration's certificate check: how many rules the table has, the certificate text each verified rule fired in, one message per rule whose certificate did not fire it, and how many of the witnessed rules are guard rules, whose certificates settle under the configuration that keeps their window live (`witness.GUARD_MARKER`). Three fields count the walk's windows: `served` the settle memo rows the load kept, stale rows included (`memo_windows`), `unasked` the rows it dropped as outside what the certificates can ask for, and `fresh` the windows the crate settled for this check. `fresh_memo` holds those windows as the walk hands them to `absorb_settle_memo` (`FreshMemo`), or None when it had no memo or settled none."""

    config: str
    rules: int
    witnessed: dict[int, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    guards: int = 0
    served: int = 0
    unasked: int = 0
    fresh: int = 0
    fresh_memo: FreshMemo | None = None

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
    """Run the conformance sweep serially: one shared Shaper, each configuration in turn through `_conformance_config`, and the results merged by `merge_conformance_results`. The parallel form is `run_m1_gates.run_font_conformance`, which submits `conformance_config_worker` once per unit of each configuration (the ss10 overlay whole, and each settlement configuration once per final symbol) and prunes the memo in the parent. The sweep reads no decision table: it shapes the font and settles the same texts through the kernel, and read-back checks that the font holds the planned rules. `summary_name` is the file written under `out_dir`, so a caller can keep its report apart from the per-edit sweep's. `settle_memos` names each configuration's shared settle memo file, and a configuration without one settles every window itself."""
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
    divergences_by_kind: dict[str, int] = field(default_factory=dict)
    exemplars: list[DivergenceExemplar] = field(default_factory=list)
    first_seen: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    memo_reach: MemoReach | None = None


class DivergenceTally:
    """The sink `_conformance_config` hands the checks. It counts every divergence by kind into `result`, in the order each kind first appears, and keeps the first `EXEMPLAR_LIMIT` in full as `DivergenceExemplar`s ranked over `alphabet`, so a red sweep's result stays the same size however many texts diverge. It also records in `result.first_seen` where each kind first appears, as the text's length, its product-order rank, and the divergence's index among its text's divergences, so results that split one configuration's texts between them can be merged back into the kind order of a single walk over them."""

    def __init__(self, result: ConformanceConfigResult, alphabet: Sequence[str]):
        self.result = result
        self._index = {symbol: position for position, symbol in enumerate(alphabet)}
        self._text: str | None = None
        self._within = 0

    def _rank(self, text: str) -> int:
        rank = 0
        for symbol in text:
            rank = rank * len(self._index) + self._index[symbol]
        return rank

    def append(self, divergence: Divergence, /) -> None:
        text = divergence.text
        if text == self._text:
            self._within += 1
        else:
            self._text = text
            self._within = 0
        result = self.result
        by_kind = result.divergences_by_kind
        fresh_kind = divergence.kind not in by_kind
        by_kind[divergence.kind] = by_kind.get(divergence.kind, 0) + 1
        keep = len(result.exemplars) < EXEMPLAR_LIMIT
        if not (fresh_kind or keep):
            return
        rank = self._rank(text)
        if fresh_kind:
            result.first_seen[divergence.kind] = (len(text), rank, self._within)
        if keep:
            result.exemplars.append(DivergenceExemplar(divergence, len(text), rank))


def sweep_texts(
    alphabet: Sequence[str], length: int, last: str | None = None, triggers: frozenset[str] = frozenset()
) -> Iterator[str]:
    """Yield the conformance sweep's texts of `length` characters over `alphabet` in `itertools.product` order, or, with `last`, only the ones that end in `last`, in the same order. With `triggers`, only the ones that contain one of its characters are yielded, in the same order."""
    if last is None:
        texts = ("".join(combo) for combo in itertools.product(alphabet, repeat=length))
    else:
        texts = ("".join(combo) + last for combo in itertools.product(alphabet, repeat=length - 1))
    if not triggers or last in triggers:
        return texts
    return (text for text in texts if not triggers.isdisjoint(text))


def renamed_runes(spec: ResolvedSpec, config: str) -> frozenset[str]:
    """Return the runes `config`'s marker renaming renames (`raw_rename_map`): each rune with a marker copy for one of its features. A text without one of them reaches the font's settlement lookup with the glyphs `default` gives it, since the stylistic sets' lookups rename only these runes. The set is empty for `default` and for an overlay configuration."""
    return frozenset(name for name in raw_rename_map(spec, features_for_config(config)) if name in spec.runes)


def trigger_letters(spec: ResolvedSpec, runes: Iterable[str]) -> frozenset[str]:
    """Return the characters a text must contain to name one of `runes`: a rune's code point, and each character of a ligature rune's sequence, since a text forms the ligature only where it spells that sequence."""
    letters: set[str] = set()
    for name in runes:
        rune = spec.runes[name]
        members = rune.sequence or (name,)
        letters.update(
            chr(codepoint) for member in members if (codepoint := spec.runes[member].codepoint) is not None
        )
    return frozenset(letters)


def unconfined_feature_records(spec: ResolvedSpec, config: str) -> tuple[str, ...]:
    """Return the policy records gated on one of `config`'s features (`when: feature:`) that restrict no slot of their window to the runes their feature renames (`raw_rename_map`), each named by its provenance. A record restricts a slot when it belongs to such a rune, which the text then names, or when its `left:` condition names families and each of them is such a rune, or, for a refuse, extend or contract record, when its `right:` condition does. The left slot holds the left neighbor's settled rune and the right slot the raw letter after the position, and in a text without such a rune neither slot can hold one. A prefer or resolve record's `right:` does not count, because the simulated prospect settles a deeper letter with its right slot `UNKNOWN`, which leaves the condition undecided, and those two kinds treat an undecided condition as firing, while the other three need a definite match. A class or a `then:` hop does not count either: a class's members come from the registry or a rune's group, and a hop past a boundary reads `#NA`, which the engine resolves permissively.

    Unlock rows need no check, since a rune with an unlock row for a feature is renamed under it. So when this returns nothing, no record or unlock row that reads one of `config`'s features fires on a window of a text that names none of `renamed_runes`, and that text settles as it does under `default`.
    """
    features = features_for_config(config)
    unconfined: list[str] = []
    for name, rune in spec.runes.items():
        for kind in ("refuse", "prefer", "extend", "contract", "resolve"):
            for index, record in enumerate(getattr(rune.policy, kind)):
                feature = record.when.feature
                if feature is None or feature not in features:
                    continue
                renamed = frozenset(raw_rename_map(spec, frozenset({feature})))
                conditions = (
                    (record.when.left,)
                    if kind in ("prefer", "resolve")
                    else (record.when.left, record.when.right)
                )
                if name in renamed or any(
                    condition is not None and condition.family and renamed.issuperset(condition.family)
                    for condition in conditions
                ):
                    continue
                unconfined.append(
                    str(record.provenance) if record.provenance else f"{name}:policy.{kind}[{index}]"
                )
    return tuple(unconfined)


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
    progress: Callable[[int], None] | None = None,
    last: str | None = None,
    triggers: frozenset[str] = frozenset(),
    max_windows: int | None = None,
) -> ConformanceConfigResult:
    """One configuration's conformance-sweep run: every string of length 1 to `max_length` over the alphabet, shaped with the font and compared with the settled stream, plus the split-buffer and zero-gap checks. Configurations share nothing, so both the serial `run_conformance` and the process-pool worker call this.

    With `last`, the run sweeps one unit of the configuration: only the texts that end in `last` (`sweep_texts`), in the same order. The units of every symbol in the alphabet partition the configuration's texts, and `merge_unit_results` merges their results into this function's result without `last`. A unit's `settle_memo`, when it has one, is `returns_fresh`: the unit prints no `[t] settle_memo` line and returns its use of the file, with the windows it settled fresh, as `result.memo_reach` (`_SettledWindowWalk.memo_reach`), from which the parent prunes the file and merges every unit's fresh windows into it once every unit of the configuration has finished (`absorb_settle_memo`). A unit that replaced the shared file would leave it holding only its own share of the rows, so a memo that is not `returns_fresh` fails the unit's assertion.

    With `triggers`, a settlement configuration's run shapes only the texts that contain one of its characters (`sweep_texts`), in the same order, which is how the deep sweep skips the texts a configuration settles and shapes as `default` does (`rebuild/tools/deep_sweep.py`). Such a run shares no settle memo, since a memo pruned of every window its texts did not reach would lose the windows of the texts it skipped, and the assertion refuses one.

    An overlay configuration is swept to `OVERLAY_MAX_LENGTH` instead, whatever `max_length` is. Its expected names are `isolated_overlay_labels` over the raw tokens, it uses no walk and no memo, and every slot must sit at zero offset with its `hmtx` advance (`check_isolated_positions`). The split-buffer check runs there too.

    Settlement goes through `_SettledWindowWalk`'s memo, which only saves time. `settle_memo` shares that memo with the other walks over the same texts: it is loaded on the first miss, and a run without `last` writes it back at the end if this sweep settled anything the file lacked, pruned of every entry no text reached. This sweep walks every text, so it is the only walk that can tell which windows still exist. Without `settle_memo`, the walk takes `horizon=max_length` and does not memoize pinned windows, and `max_windows`, when given, is its ceiling on the windows it holds (`_SettledWindowWalk`), which the deep sweep sets so that a unit's memory has a bound that does not depend on the alphabet. Each length's texts go through the walk `TEXT_CHUNK` at a time, because at larger maximum lengths a length has millions of texts and only the current chunk needs to be in memory. The split-buffer check runs only on texts that contain a splitter, since a text without one is its own single segment.

    `progress`, when given, is called with the count of texts shaped so far after each chunk, and after each length for an overlay configuration, so a caller can report a long sweep's progress from another process without the sweep printing anything or paying for a call per text.
    """
    assert (
        last is None or settle_memo is None or settle_memo.returns_fresh
    ), "a unit sweep returns its fresh windows"
    assert not triggers or settle_memo is None, "a sweep that skips texts shares no settle memo"
    assert max_windows is None or settle_memo is None, "a window ceiling is for a walk with no settle memo"
    features = features_for_config(config)
    result = ConformanceConfigResult(config=config)
    divergences = DivergenceTally(result, alphabet)
    modes: set[str] = set()
    if isolated_overlay_active(spec, features):
        for length in range(1, OVERLAY_MAX_LENGTH + 1):
            for text in sweep_texts(alphabet, length, last):
                result.sequences += 1
                shaped = shaper.shape(text, features)
                result.shaping_runs += 1
                if set(text) & splitters:
                    check_split_buffer(text, config, features, shaper, shaped, divergences, splitters)
                expected = isolated_overlay_labels(spec, isolated_overlay_tokens(spec, text))
                check_oracle(text, config, shaped, expected, divergences, modes)
                check_isolated_positions(text, config, shaper, shaped, divergences)
            if progress is not None:
                progress(result.sequences)
        result.modes = sorted(modes)
        return result

    if guard_verdicts is None:
        guard_verdicts = kernel_exec.guard_sweep(spec)
    walker = _SettledWindowWalk(
        spec,
        features,
        glyph_names,
        guard_verdicts,
        memo=settle_memo,
        promote=False,
        horizon=max_length if settle_memo is None else None,
        max_windows=max_windows,
    )

    def sweep_text(text: str, names: list[str]) -> None:
        shaped = shaper.shape(text, features)
        result.shaping_runs += 1
        if set(text) & splitters:
            check_split_buffer(text, config, features, shaper, shaped, divergences, splitters)
        check_oracle(text, config, shaped, names, divergences, modes)
        if anchors_of is not None:
            check_join_gaps(text, config, shaper, shaped, anchors_of, divergences)

    for length in range(1, max_length + 1):
        stream = sweep_texts(alphabet, length, last, triggers)
        while True:
            chunk = list(itertools.islice(stream, TEXT_CHUNK))
            if not chunk:
                break
            result.sequences += len(chunk)
            for text, (_settled, names) in zip(chunk, walker.walk_many(chunk)):
                sweep_text(text, names)
            if progress is not None:
                progress(result.sequences)

    if last is not None and settle_memo is not None:
        walker.save_memo()
        result.memo_reach = walker.memo_reach()
    else:
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
    progress: Callable[[int], None] | None = None,
    last: str | None = None,
    triggers: frozenset[str] = frozenset(),
    max_windows: int | None = None,
) -> ConformanceConfigResult:
    """One configuration's conformance-sweep run in its own process, building what it needs from the spec and the font. That includes the section 5.7 guard verdicts when the caller passes none (a fifth of a second against a sweep that runs for a minute); an overlay configuration forms nothing and skips them. `settle_memo` is only a path and keys, so the worker reads and writes the file itself, or, for a unit (`last`), reads it and returns its fresh windows on its result's reach. `progress`, `last`, `triggers` and `max_windows` are `_conformance_config`'s."""
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
        progress=progress,
        last=last,
        triggers=triggers,
        max_windows=max_windows,
    )


def merge_unit_results(config: str, results: Iterable[ConformanceConfigResult]) -> ConformanceConfigResult:
    """Merge the results of `config`'s units (`_conformance_config` with `last`), which split its texts between them, into the result one run over all of those texts returns. Sequences and shaping runs are summed. Divergence counts are summed by kind, in the order of each kind's earliest `first_seen`, which is the order a single run meets them. The exemplars are ordered by their text's length, then its product-order rank, then the order the checks found them, which is the order a single run records them, and the first `EXEMPLAR_LIMIT` are kept: each unit keeps its own first `EXEMPLAR_LIMIT`, so no divergence among the first `EXEMPLAR_LIMIT` overall is missing. Notes are concatenated in the caller's order and oracle modes merged, so the result does not depend on which unit finished first when the caller passes them in a fixed order. The merged result carries no `memo_reach`: the caller reads each unit's before merging."""
    merged = ConformanceConfigResult(config=config)
    counts: dict[str, int] = {}
    first_seen: dict[str, tuple[int, int, int]] = {}
    exemplars: list[DivergenceExemplar] = []
    modes: set[str] = set()
    for result in results:
        assert result.config == config, (result.config, config)
        merged.sequences += result.sequences
        merged.shaping_runs += result.shaping_runs
        for kind, count in result.divergences_by_kind.items():
            counts[kind] = counts.get(kind, 0) + count
        for kind, seen in result.first_seen.items():
            first_seen[kind] = min(first_seen.get(kind, seen), seen)
        exemplars.extend(result.exemplars)
        merged.notes.extend(result.notes)
        modes.update(result.modes)
    order = sorted(counts, key=first_seen.__getitem__)
    merged.divergences_by_kind = {kind: counts[kind] for kind in order}
    merged.first_seen = {kind: first_seen[kind] for kind in order}
    exemplars.sort(key=lambda exemplar: (exemplar.length, exemplar.rank))
    merged.exemplars = exemplars[:EXEMPLAR_LIMIT]
    merged.modes = sorted(modes)
    return merged


def merge_conformance_results(font_path: Path, results: Iterable[ConformanceConfigResult]) -> ConformReport:
    """Merge per-configuration results into one ConformReport. `sequences` comes from the first result, which sweeps every text: the per-edit sweep sweeps the same texts in every settlement configuration, and the deep sweep, which sweeps some configurations only over the texts that contain their trigger letters (`sweep_texts`), passes `default`'s result first, in `ACCEPTANCE_CONFIGS` order. The overlay's shorter sweep and the texts a configuration skips show only in the shaping runs. Shaping runs are summed, divergence counts are summed by kind in the order each kind first appears, and exemplars and notes are concatenated in the caller's configuration order, the exemplars up to `EXEMPLAR_LIMIT`. Each result keeps its own first `EXEMPLAR_LIMIT` divergences, so the merged exemplars are the first `EXEMPLAR_LIMIT` of every configuration's divergences in that order. The oracle modes are merged and appended in sorted order, so the report does not depend on which configuration finished first."""
    report = ConformReport(font=str(font_path))
    results = list(results)
    report.sequences = results[0].sequences if results else 0
    modes: set[str] = set()
    for result in results:
        report.shaping_runs += result.shaping_runs
        for kind, count in result.divergences_by_kind.items():
            report.divergences_by_kind[kind] = report.divergences_by_kind.get(kind, 0) + count
        report.exemplars.extend(result.exemplars[: EXEMPLAR_LIMIT - len(report.exemplars)])
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
    baseline_junctions: tuple[str, ...]
    new_cells: tuple[str, ...]
    new_junctions: tuple[str, ...]
    divergence_tags: tuple[str, ...] = ()


def _junction_token(spec: ResolvedSpec, junction) -> str:
    if junction is None:
        return "break"
    if isinstance(junction, int):
        return f"y{junction}"
    return f"y{spec.registry.y_of(junction)}"


def _cached_verdict(divergent: DivergentRow | None) -> oracle_cache.CachedRow | None:
    """A fresh comparison's result in the form the row cache stores: the five fields the subset table cannot supply, without the provenance that would make two equal verdicts compare unequal."""
    if divergent is None:
        return None
    return oracle_cache.CachedRow(
        kinds=divergent.kinds,
        position=divergent.position,
        new_cells=divergent.new_cells,
        new_junctions=divergent.new_junctions,
        divergence_tags=divergent.divergence_tags,
    )


def _served_verdict(config: str, row: Row, cached: oracle_cache.CachedRow) -> DivergentRow:
    """A stored verdict converted back to a `DivergentRow`, with `config` and the three baseline fields taken from the table row instead of the store. From `_match_compiled` on, this row is indistinguishable from a freshly compared one; `rebuild/test_conform.py` checks that a served oracle pass writes the same audit as a cold one."""
    return DivergentRow(
        config=config,
        codepoints=format_codepoints(row.codepoints),
        kinds=cached.kinds,
        position=cached.position,
        baseline_glyphs=tuple(row.glyphs),
        baseline_junctions=tuple(row.junctions),
        new_cells=cached.new_cells,
        new_junctions=cached.new_junctions,
        divergence_tags=cached.divergence_tags,
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
    """Re-derive the pass's stratified sample of served rows and check each against the record it was served from. Every family that served a row contributes rows, so a whole family of wrong records (which a rune edited during a run produces) is always caught, not just with the probability of the sample size over the rows served. The seed includes the pass ordinal, so each pass checks a different slice. The rows come from the sample itself, which keeps at most `VERIFICATION_SAMPLE_PER_FAMILY` per family, so nothing here re-reads the table. A mismatch raises `SystemExit` instead of being treated as a cache miss, because the store holds verdicts this build does not produce, and the corpus build stamps its manifest with the input fingerprint of the run that wrote `divergence-audit.tsv`, which would then vouch for rows those inputs do not produce."""
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
    """Compare one baseline row with the settlement of its text, and return the `DivergentRow`, or None when they agree. The caller's walk passes in `settled`, so each row is settled once. Under the overlay configuration it is `IsolatedOverlayWalk`'s bare stream: each letter's default-stance cell with no junction, which is what the alias map means by a bare name, one per raw token, so a window whose pair formed a ligature in the old font diverges as `ligation`."""
    new_cells: list[str] = []
    new_junctions: list[str] = []
    for index, item in enumerate(settled):
        cell = getattr(item, "cell", None)
        new_cells.append(_cell_token(cell, item))
        if index < len(settled) - 1:
            new_junctions.append(_junction_token(spec, getattr(item, "junction", None)))
    kinds: list[str] = []
    position = -1
    tags: set[str] = set()

    if len(row.glyphs) != len(settled):
        kinds.append("ligation")
        tags.add("ligation")
    else:
        for index, (old_name, item) in enumerate(zip(row.glyphs, settled)):
            if old_name in BOUNDARY_GLYPH_NAMES:
                continue
            alias = aliases.get(old_name)
            if alias is None or alias == "pending":
                if "unaliased" not in kinds:
                    kinds.append("unaliased")
                    position = index
                tags.add(f"unaliased:{old_name}")
                continue
            if isinstance(alias, str):
                continue
            cell = getattr(item, "cell", None)
            if cell == alias or not isinstance(cell, CellId):
                continue
            if "cell" not in kinds:
                kinds.append("cell")
                position = index
            tags |= _cell_deltas(alias, cell, row.glyphs, index)
        baseline_junctions = tuple(junction for junction in row.junctions if junction != "lig")
        if baseline_junctions != tuple(new_junctions):
            kinds.append("junction")
            for junction_index, (old_junction, new_junction) in enumerate(
                zip(baseline_junctions, new_junctions)
            ):
                if old_junction == new_junction:
                    continue
                if old_junction == "break":
                    cell = getattr(settled[junction_index], "cell", None)
                    left = getattr(cell, "rune", "?")
                    tags.add(f"junction-gain:{left}")
                    if left == "qsIt" and getattr(cell, "entry", None) is None:
                        tags.add("junction-gain-unentered:qsIt")
                elif new_junction == "break":
                    tags.add("junction-loss")
                else:
                    tags.add("junction-moved")

    if not kinds:
        return None
    return DivergentRow(
        config=config,
        codepoints=":".join(f"{cp:04X}" for cp in row.codepoints),
        kinds=tuple(dict.fromkeys(kinds)),
        position=position,
        baseline_glyphs=tuple(row.glyphs),
        baseline_junctions=tuple(row.junctions),
        new_cells=tuple(new_cells),
        new_junctions=tuple(new_junctions),
        divergence_tags=tuple(sorted(tags)),
    )


def _cell_deltas(alias: CellId, cell: CellId, old_glyphs, index: int) -> set[str]:
    """The individual differences between the cell an old name stands for and the cell settlement chose, as divergence tags for `classify_divergence`."""
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
                out.add("-en-ext-1:same-junction")
            else:
                out.add(f"-en-ext-1:{cell.rune}")
        elif token == "en-ext-2" and index > 0 and "ex-ext-2" in old_glyphs[index - 1]:
            out.add("-en-ext-2:same-junction")
        else:
            out.add(f"-{token}")
    if ".noentry" in old_glyphs[index]:
        out.add("old-noentry")
    return out


def _cell_token(cell, item) -> str:
    if cell is None:
        return getattr(item, "glyph_name", None) or str(item)
    return f"{cell.rune}/{cell.stance}/{cell.entry}/{cell.exit}/{'+'.join(cell.adjustments)}"
