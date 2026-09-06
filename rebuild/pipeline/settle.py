"""The settlement vocabulary of design section 6.1: the types a window is stated in, the boundary semantics that split runs, the tokenizer that turns codepoints into tokens, and the type-4 formation that stages before settlement. Settlement itself belongs to the crate under `rebuild/kernel-rs`, reached through `rebuild.pipeline.kernel_exec`; nothing here calls it, and this module imports nothing from it.

The types are the shape of a window as both sides of that boundary state it. A `RightToken` is one raw lookahead slot; a `LeftContext` is the resolved neighbor to the left; a `Candidate` is the pair being ranked at a position (the cell of rune i, and the seam state toward i+1); `RankedCandidate` and `Elimination` are the ladder and the graveyard an author-facing trace carries; a `TransitionTrace` is the whole answer for one position. `kernel_exec` decodes the crate's answers into exactly these, so an explain report, a review unit, and a conform window all read one set of objects.

Boundary semantics: space and ZWNJ split runs and derive word position; the namer dot does not split runs but is addressable as `is: namer-dot` and, having no join surface, breaks adjacency naturally. A boundary position settles to `boundary_settled` — a model constant, answered here rather than asked of the kernel — and `word_position` derives the design section 3.4 position from the splitting kinds alone.

Formation stages before everything else, markers included: `form_ligatures` walks the modeled ligature runes greedily left to right, longest sequence first, and every match yields per window to the section 5.7 late-formation guard over the two raw tokens past the sequence. Those verdicts are the crate's, swept whole by `kernel_exec.guard_sweep`, so the complete surface is an argument here and `guard_blocks` is the single place that reads it.
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
    """A window that could not settle. `bucket` is the crate's own raise identity — `E-INCOMPARABLE`, `E-AMBIGUOUS`, `E-UNREACHABLE` — carried across by `kernel_exec` so a caller can sort a refusal without parsing its sentence, while the message stays the crate's sentence verbatim. This module's own raises are about a codepoint the registry does not model rather than about settlement, and carry no bucket."""

    def __init__(self, message: str, bucket: str | None = None) -> None:
        super().__init__(message)
        self.bucket = bucket


class RightToken(NamedTuple):
    kind: str  # "edge" | "space" | "zwnj" | "namer-dot" | "letter" | "unknown"
    rune: str | None = None

    @property
    def letter(self) -> str:
        """The rune name, for the `kind == "letter"` reads that have already established there is one."""
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
    """The stream an `overlay: isolated` taste set (ss10) renders for raw tokens: every letter its rune's default-stance cell with no entry, no exit, and no seam, every boundary token its boundary cell. Nothing settles under the overlay because the emitted font's pre-empt lookup has replaced every letter by its anchor-free twin before formation could see the buffer (the twins sit in no formation sequence, marker line, chokepoint class, or settlement input, which read-back proves per build), so the stream is a function of the tokens and the registry alone, and formation is never applied — a ligature's components stand as separate letters."""
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
    """`isolated_overlay_settled` dressed as one trace per position, decided by `ISOLATED_OVERLAY_STAGE` with no candidates and no eliminations, so an explain report or a review unit reads the overlay as what it is rather than as a settlement nobody rendered."""
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
    """A deterministic textual form of a CellId for the diff-stable TSV artifacts and explain output. Not the compiled display name (that is geometry's, with the 63-byte cap); same shape on purpose so the alias map reads naturally."""
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
    """Whether the ZWNJ chokepoint locks this rune: it has at least one selectable declared entry row, or any entry unlock, on any stance. Feature-agnostic, like the chokepoint itself."""
    rune = spec.runes[rune_name]
    for stance in rune.stances.values():
        if any(row.selectable for row in stance.surface.entries.values()):
            return True
        if any(unlock.entry is not None for unlock in stance.surface.unlocks):
            return True
    return False


def word_position(left_kind: str, right1_kind: str) -> str | None:
    """Word position derived from run-splitting boundaries only (design section 3.4): the namer dot does not split, so it leaves position medial on both sides. None when the right token is unknown."""
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
    """Whether the section 5.7 guard withholds `liga` where the two raw slots past its sequence are `right1` and `right2`. A non-letter first slot never blocks — the guard exists to keep a ligature from stranding a follower, and a boundary is no follower — and every other triple is an indexed read of the crate's sweep, so a surface that does not cover the window raises `KeyError` here instead of quietly reading as free."""
    if right1.kind != "letter":
        return False
    return verdicts[(liga, right1, right2)]


# The modeled ligature runes' sequences in the order formation tries them, keyed by the rune a sequence opens on and held per spec identity. Formation asks for the order at every position of every text, and a sweep or a surface build forms texts by the hundred thousand under one spec, so the sort is paid once and a position reads only the sequences its own rune can open; an entry holds the spec strongly so its id is never recycled underneath it, and the table keeps the last few specs a process formed under.
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
    """Type-4 formation over the modeled ligature runes, greedy left to right, longest sequence first — staged before everything else, markers included, each match yielding per window to the section 5.7 late-formation guard over the two raw tokens past the sequence (design section 5.7). `guard_verdicts` is the crate's complete verdict surface for this spec, which `kernel_exec.guard_sweep` answers in one invocation. It is required rather than optional because a caller with no sweep in hand would otherwise form every ligature the emitted lookup withholds, and form it silently."""
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
