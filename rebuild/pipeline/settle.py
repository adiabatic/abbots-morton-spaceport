"""The settlement vocabulary of doc/rebuild-design.md §6.1: the types a window and its trace are stated in, boundary semantics, the tokenizer from codepoints to tokens, and ligature formation, which runs before settlement. Settlement itself runs in the crate under `rebuild/kernel-rs`, reached through `rebuild.pipeline.kernel_exec`. This module does not import `kernel_exec`.

A `RightToken` is one raw lookahead slot. A `LeftContext` is the settled neighbor to the left. A `Candidate` is one option ranked at a position: a stance of the rune there, with its entry and the seam toward the next letter. `RankedCandidate` and `Elimination` are the ranked and eliminated candidates an explain trace lists, and a `TransitionTrace` is the full result for one position. `kernel_exec` decodes the crate's results into these types, so explain reports, review units, and conform windows all read the same objects.

Space and ZWNJ split runs and determine word position. The namer dot does not split runs. A condition can address it as `is: namer-dot`, and because it has no join surface it breaks adjacency. A boundary position settles to `boundary_settled`, which this module computes without asking the kernel. `word_position` derives the §3.4 word position from the splitting kinds alone.

`form_ligatures` runs before every other stage, the stylistic-set markers included. It matches the modeled ligature runes greedily left to right, longest sequence first, and each match is subject to the §5.7 late-formation guard over the two raw tokens after the sequence. The guard verdicts come from the crate through `kernel_exec.guard_sweep`, are passed in as an argument, and are read only in `guard_blocks`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from rebuild.pipeline.model import CellId, Height, Provenance, ResolvedSpec, Settled

SPLITTING_KINDS = ("edge", "space", "zwnj")
BOUNDARY_KINDS = ("edge", "space", "zwnj", "namer-dot")
BOUNDARY_STANCE = "boundary"

_NO_EXIT_INDEX = 9999


class SettleError(Exception):
    """A window that could not settle. `bucket` is the crate's error code (`E-INCOMPARABLE`, `E-AMBIGUOUS`, or `E-UNREACHABLE`), passed through by `kernel_exec` so a caller can sort refusals without parsing the message, which is the crate's message verbatim. The errors this module raises are for codepoints the registry or the spec does not model, and have no bucket."""

    def __init__(self, message: str, bucket: str | None = None) -> None:
        super().__init__(message)
        self.bucket = bucket


class RightToken(NamedTuple):
    kind: str  # "edge" | "space" | "zwnj" | "namer-dot" | "letter" | "unknown"
    rune: str | None = None

    @property
    def letter(self) -> str:
        """Return the rune name, for callers that have already checked `kind == "letter"`. Raises ValueError when there is none."""
        if self.rune is None:
            raise ValueError(f"{self.kind} token has no rune")
        return self.rune


EDGE = RightToken("edge")
SPACE = RightToken("space")
ZWNJ = RightToken("zwnj")
NAMER_DOT = RightToken("namer-dot")
UNKNOWN = RightToken("unknown")

FormationGuard = dict[tuple[str, RightToken, RightToken], bool]


@dataclass(frozen=True, slots=True)
class LeftContext:
    kind: str  # "edge" | "space" | "zwnj" | "namer-dot" | "letter"
    settled: Settled | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    stance: str
    entry: Height | None
    seam: Height | None  # the joining exit height; None = no join (exit withdrawn or never offered)
    order_index: int
    exit_index: int = _NO_EXIT_INDEX


@dataclass(frozen=True, slots=True)
class Elimination:
    stage: str
    description: str
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: Candidate
    join_count: int
    prospect: int


@dataclass(frozen=True, slots=True)
class TransitionTrace:
    settled: Settled
    joint_floor: bool
    prospect: int
    ranked: tuple[RankedCandidate, ...]
    eliminations: tuple[Elimination, ...]
    decided_stage: str
    runner_up: Candidate | None
    notes: tuple[str, ...]


def boundary_cell(kind: str) -> CellId:
    return CellId(rune=kind, stance=BOUNDARY_STANCE, entry=None, exit=None, adjustments=())


def boundary_settled(kind: str) -> Settled:
    return Settled(cell=boundary_cell(kind), seam=None, extension=0)


def is_boundary_settled(settled: Settled) -> bool:
    return settled.cell.stance == BOUNDARY_STANCE


ISOLATED_OVERLAY_STAGE = "isolated-overlay"


def isolated_overlay_settled(spec: ResolvedSpec, tokens: Sequence[RightToken]) -> list[Settled]:
    """Return the stream the `overlay: isolated` stylistic set (ss10) renders for raw tokens: each letter as its rune's default-stance cell with no entry, exit, or seam, and each boundary token as its boundary cell. Nothing settles under the overlay, because the emitted font's pre-empt lookup replaces every letter with its anchor-free twin before formation runs. Read-back checks on every build that no twin appears in a formation sequence, marker line, chokepoint class, or settlement input. So the stream depends only on the tokens and the spec, and no ligature forms: a ligature's components stay separate letters."""
    stream: list[Settled] = []
    for token in tokens:
        if token.kind != "letter":
            stream.append(boundary_settled(token.kind))
            continue
        rune = spec.runes[token.letter]
        stream.append(
            Settled(cell=CellId(token.letter, rune.default_stance, None, None, ()), seam=None, extension=0)
        )
    return stream


def isolated_overlay_traces(spec: ResolvedSpec, tokens: Sequence[RightToken]) -> list[TransitionTrace]:
    """Return `isolated_overlay_settled` as one trace per position, decided by `ISOLATED_OVERLAY_STAGE` (or `boundary` at a boundary) with no candidates or eliminations, so explain reports and review units do not show the overlay as a settlement."""
    return [
        TransitionTrace(
            settled=settled,
            joint_floor=False,
            prospect=0,
            ranked=(),
            eliminations=(),
            decided_stage="boundary" if is_boundary_settled(settled) else ISOLATED_OVERLAY_STAGE,
            runner_up=None,
            notes=(),
        )
        for settled in isolated_overlay_settled(spec, tokens)
    ]


def cell_label(spec: ResolvedSpec, cell: CellId) -> str:
    """Return a deterministic text form of a CellId, used in the diff-stable TSV artifacts and explain output. `run_m1.mint_cell_glyphs` also names each settled cell's compiled glyph with it and fails on a label over `geometry.MAX_GLYPH_NAME_BYTES`. `geometry.display_name` is a different form, which `geometry.realize` uses only when no name is passed."""
    if cell.stance == BOUNDARY_STANCE:
        return {"space": "space", "zwnj": "uni200C", "namer-dot": "periodcentered"}[cell.rune]
    parts = [cell.rune, cell.stance]
    if cell.entry is not None:
        parts.append(f"en-y{spec.registry.y_of(cell.entry)}")
    if cell.exit is not None:
        parts.append(f"ex-y{spec.registry.y_of(cell.exit)}")
    parts.extend(cell.adjustments)
    return ".".join(parts)


def is_entry_bearing(spec: ResolvedSpec, rune_name: str) -> bool:
    """Whether the ZWNJ chokepoint locks this rune: some stance has a selectable declared entry row or an entry unlock. Features are ignored, as they are by the chokepoint."""
    rune = spec.runes[rune_name]
    for stance in rune.stances.values():
        if any(row.selectable for row in stance.surface.entries.values()):
            return True
        if any(unlock.entry is not None for unlock in stance.surface.unlocks):
            return True
    return False


def word_position(left_kind: str, right1_kind: str) -> str | None:
    """Return the word position (design §3.4) derived from run-splitting boundaries only. The namer dot does not split runs, so it leaves the position medial on both sides. Returns None when the right token is unknown."""
    initial = left_kind in SPLITTING_KINDS
    if right1_kind == "unknown":
        return None
    final = right1_kind in SPLITTING_KINDS
    if initial and final:
        return "isolated"
    if initial:
        return "initial"
    if final:
        return "final"
    return "medial"


def tokens_from_codepoints(spec: ResolvedSpec, codepoints: Sequence[int]) -> list[RightToken]:
    boundary_by_codepoint = {token.codepoint: name for name, token in spec.registry.boundary_tokens.items()}
    family_by_codepoint = {
        info.codepoint: name for name, info in spec.registry.families.items() if info.codepoint is not None
    }
    tokens: list[RightToken] = []
    for codepoint in codepoints:
        boundary = boundary_by_codepoint.get(codepoint)
        if boundary is not None:
            tokens.append(RightToken(boundary))
            continue
        family = family_by_codepoint.get(codepoint)
        if family is None:
            raise SettleError(f"U+{codepoint:04X} is not in the registry")
        if family not in spec.runes:
            raise SettleError(f"U+{codepoint:04X} ({family}) is registered but not modeled in this spec")
        tokens.append(RightToken("letter", family))
    return tokens


def guard_blocks(verdicts: FormationGuard, liga: str, right1: RightToken, right2: RightToken) -> bool:
    """Whether the §5.7 guard blocks `liga` where the two raw slots after its sequence are `right1` and `right2`. A non-letter first slot never blocks, because the guard keeps formation from costing the following letter a join, and a boundary is not a letter. Every other case is looked up in the crate's verdicts, so a window the verdicts do not cover raises `KeyError` instead of reading as unblocked."""
    if right1.kind != "letter":
        return False
    return verdicts[(liga, right1, right2)]


# The modeled ligature runes' sequences in the order formation tries them (longest first), grouped by first component and cached per spec identity. Formation reads the order at every position of every text, and a sweep or a surface build forms many texts under one spec, so the sort runs once per spec and a position reads only the sequences its own rune can start. Each entry holds the spec itself so its id cannot be reused while cached. The cache clears when it reaches `_LIGATURE_ORDERS_CAP` specs.
_LIGATURE_ORDERS: dict[int, tuple[ResolvedSpec, dict[str, list[tuple[Sequence[str], str]]]]] = {}
_LIGATURE_ORDERS_CAP = 4


def _ligature_order(spec: ResolvedSpec) -> dict[str, list[tuple[Sequence[str], str]]]:
    held = _LIGATURE_ORDERS.get(id(spec))
    if held is not None and held[0] is spec:
        return held[1]
    sequences = sorted(
        ((rune.sequence, name) for name, rune in spec.runes.items() if rune.sequence),
        key=lambda item: -len(item[0]),
    )
    by_lead: dict[str, list[tuple[Sequence[str], str]]] = {}
    for sequence, name in sequences:
        by_lead.setdefault(sequence[0], []).append((sequence, name))
    if len(_LIGATURE_ORDERS) >= _LIGATURE_ORDERS_CAP:
        _LIGATURE_ORDERS.clear()
    _LIGATURE_ORDERS[id(spec)] = (spec, by_lead)
    return by_lead


def form_ligatures(
    spec: ResolvedSpec, tokens: list[RightToken], guard_verdicts: FormationGuard
) -> list[RightToken]:
    """Apply type-4 formation over the modeled ligature runes, greedy left to right, longest sequence first. Each match is subject to the §5.7 late-formation guard over the two raw tokens after the sequence. `guard_verdicts` is the crate's full verdict map for this spec, from one `kernel_exec.guard_sweep` call. It is required because a caller without it would form, with no error, every ligature the emitted lookup blocks."""
    by_lead = _ligature_order(spec)
    formed: list[RightToken] = []
    i = 0
    while i < len(tokens):
        match = None
        if tokens[i].kind == "letter":
            for sequence, name in by_lead.get(tokens[i].letter, ()):
                end = i + len(sequence)
                if end <= len(tokens) and all(
                    tokens[i + k].kind == "letter" and tokens[i + k].rune == part
                    for k, part in enumerate(sequence)
                ):
                    right1 = tokens[end] if end < len(tokens) else EDGE
                    right2 = tokens[end + 1] if end + 1 < len(tokens) else EDGE
                    if guard_blocks(guard_verdicts, name, right1, right2):
                        continue
                    match = (name, len(sequence))
                    break
        if match is not None:
            formed.append(RightToken("letter", match[0]))
            i += match[1]
        else:
            formed.append(tokens[i])
            i += 1
    return formed
