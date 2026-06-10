"""The section 6.1 settlement function over a ResolvedSpec (doc/rebuild-design.md), promoted from prototype/settle.py per the Recon B promotion map.

Per run (boundary to boundary), after unconditional type-4 formation, left to right: at each position the unit being ranked is the pair candidate (cell of rune i, seam state toward i+1). The kernel implements entry binding with the bilateral-commitment rule and the E-STRANDED raise, the refusal-aware lookahead closure (mutuality is definitional: an exit with no refusal-aware acceptor is never a candidate), refusals from both seam runes at all three grains with except carve-outs, and the strictly lexicographic ranking: absolute prefers (most-specific first) -> window join-count with the deliberately optimistic third term -> yielding prefers -> the runes' declared order: -> the structural floor (lower seam height, row declaration order, none last) -> the weak lead preference (unreachable in practice because the floor is total; kept as the documented final stage). Extensions and contracts apply per (seam, side) by section 6.2 most-specific-wins and never sum on one side; a follower's entry extension is suppressed when the predecessor's exit already carries the seam's pixels (the same-seam non-summing rule, prototype divergence 3).

Boundary semantics: space and ZWNJ split runs and derive word position; the namer dot does not split runs but is addressable as `is: namer-dot` and, having no join surface, breaks adjacency naturally. Post-ZWNJ letters with a live entry surface settle as locked twins (the `locked` adjustment) with the entry side severed — post-ZWNJ behaves word-initial by definition.

Withdrawal is candidate semantics, not a fixup: a join that does not realize mid-word leaves the cell's exit state none, and when the declined exit row binds a named withdrawal bitmap the cell carries an `ex-bind-<bitmap>` adjustment (the model's closed adjustments grammar) so the withdrawn drawing is part of the cell's identity; `withdrawal: safe` rows collapse to the plain exit-none cell. At a boundary the exit was never declined, so the base drawing stands.

`transition` keeps the plan's contract signature and returns Settled; `transition_trace` is the additive rich form the table builder and the explain CLI consume.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from rebuild.pipeline import specificity
from rebuild.pipeline.model import (
    NONE_STATE,
    WITHDRAWN_SUFFIX,
    CellId,
    Condition,
    Height,
    PolicyRecord,
    Provenance,
    ResolvedSpec,
    Rune,
    Settled,
    Stance,
    SurfaceRow,
    Unlock,
    When,
)
from rebuild.pipeline.specificity import EAmbiguousError, EIncomparableError

SPLITTING_KINDS = ("edge", "space", "zwnj")
BOUNDARY_KINDS = ("edge", "space", "zwnj", "namer-dot")
BOUNDARY_STANCE = "boundary"

_NO_EXIT_INDEX = 9999


class SettleError(Exception):
    pass


class EStrandedError(SettleError):
    """A committed exit found no acceptor row at the next position — the lookahead closure should make this unreachable in real settlement; reaching it means a spec or kernel bug."""


@dataclass(frozen=True)
class RightToken:
    kind: str  # "edge" | "space" | "zwnj" | "namer-dot" | "letter" | "unknown"
    rune: str | None = None


EDGE = RightToken("edge")
SPACE = RightToken("space")
ZWNJ = RightToken("zwnj")
NAMER_DOT = RightToken("namer-dot")
UNKNOWN = RightToken("unknown")


@dataclass(frozen=True)
class LeftContext:
    kind: str  # "edge" | "space" | "zwnj" | "namer-dot" | "letter"
    settled: Settled | None = None


@dataclass(frozen=True)
class Candidate:
    stance: str
    entry: Height | None
    seam: Height | None  # the joining exit height; None = no join (exit withdrawn or never offered)
    order_index: int
    exit_index: int = _NO_EXIT_INDEX


@dataclass(frozen=True)
class Elimination:
    stage: str
    description: str
    provenance: Provenance | None = None


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    join_count: int
    prospect: int


@dataclass(frozen=True)
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


class Engine:
    """One settlement engine per (spec, feature configuration); caches candidate enumerations so the table builder's fixpoint stays fast."""

    def __init__(self, spec: ResolvedSpec, features: frozenset[str]):
        self.spec = spec
        self.features = frozenset(features)
        self._closure_cache: dict[tuple, bool] = {}
        self._prospect_cache: dict[tuple, int] = {}
        # YAML provenance of every authored record that demonstrably fired during settlement under this configuration: refusals that killed a candidate, unlocks that granted capability, row scopes that admitted a side, and extends/contracts/prefers that shaped a committed cell. Closure and prospect evaluations count — a refusal firing inside the lookahead closure is load-bearing for the window that consulted it. The dead-policy gate reads this through DecisionTable.cited_provenance.
        self.fired: set[str] = set()

    def _record_fired(self, provenance: Provenance | None) -> None:
        if provenance is not None:
            self.fired.add(str(provenance))

    # --- condition matching -------------------------------------------------

    def _members(self, name: str, owner: str | None) -> frozenset[str]:
        return specificity.class_members(self.spec, name, owner)

    def _left_exit_stroke(self, left: LeftContext) -> str | None:
        if left.kind != "letter" or left.settled is None or left.settled.seam is None:
            return None
        cell = left.settled.cell
        rune = self.spec.runes.get(cell.rune)
        if rune is None:
            return None
        row = rune.stances[cell.stance].surface.exits.get(left.settled.seam)
        return row.stroke if row is not None else None

    def cond_matches_left(
        self, owner: str | None, cond: Condition, left: LeftContext, seam: Height | None
    ) -> bool:
        """`seam` is the height of the join being decided between the left neighbor and the current position — the candidate's entry, or None when unentered. `joined_at` and from-scope conditions read it."""
        if cond.is_token is not None:
            if cond.is_token == "boundary":
                if left.kind == "letter":
                    return False
            elif left.kind != cond.is_token:
                return False
        needs_letter = bool(
            cond.family or cond.klass or cond.stance or cond.joined_at is not None or cond.stroke is not None
        )
        if needs_letter:
            if left.kind != "letter" or left.settled is None:
                return False
            cell = left.settled.cell
            if cond.family and cell.rune not in cond.family:
                return False
            for klass in cond.klass:
                if cell.rune not in self._members(klass, owner):
                    return False
            if cond.stance and cell.stance not in cond.stance:
                return False
            if cond.joined_at is not None:
                state = seam if seam is not None else NONE_STATE
                if cond.joined_at != state:
                    return False
            if cond.stroke is not None and self._left_exit_stroke(left) != cond.stroke:
                return False
        if cond.then is not None:
            raise SettleError("left conditions cannot carry then: (window depth, design section 3.4)")
        for ex in cond.except_:
            if self.cond_matches_left(owner, ex, left, seam):
                return False
        return True

    def _rune_entry_strokes(self, rune_name: str) -> frozenset[str]:
        rune = self.spec.runes.get(rune_name)
        if rune is None:
            return frozenset()
        strokes = set()
        for stance in rune.stances.values():
            for row in stance.surface.entries.values():
                if row.selectable and row.stroke is not None:
                    strokes.add(row.stroke)
        return frozenset(strokes)

    def cond_matches_right(
        self, owner: str | None, cond: Condition, token: RightToken, then_token: RightToken
    ) -> bool | None:
        """Static raw-right matching. Returns None when the verdict depends on a token outside the evaluated window (the `unknown` kind) — callers treat None optimistically for refusals and unlocks, which is the deliberate optimism of the closure and the prospect term."""
        unknown = False
        if cond.is_token is not None:
            if token.kind == "unknown":
                unknown = True
            elif cond.is_token == "boundary":
                if token.kind == "letter":
                    return False
            elif token.kind != cond.is_token:
                return False
        if cond.stance or cond.joined_at is not None:
            raise SettleError(
                "right conditions are raw: stance/joined_at are left-only axes (design section 3.4)"
            )
        needs_letter = bool(cond.family or cond.klass or cond.stroke is not None)
        if needs_letter:
            if token.kind == "unknown":
                unknown = True
            elif token.kind != "letter":
                return False
            else:
                if cond.family and token.rune not in cond.family:
                    return False
                for klass in cond.klass:
                    if token.rune not in self._members(klass, owner):
                        return False
                if cond.stroke is not None and cond.stroke not in self._rune_entry_strokes(token.rune):
                    return False
        for ex in cond.except_:
            sub = self.cond_matches_right(owner, ex, token, UNKNOWN)
            if sub is True:
                return False
            if sub is None:
                unknown = True
        if cond.then is not None:
            sub = self.cond_matches_right(owner, cond.then, then_token, UNKNOWN)
            if sub is False:
                return False
            if sub is None:
                unknown = True
        return None if unknown else True

    def when_matches(
        self,
        owner: str | None,
        when: When,
        *,
        left: LeftContext,
        entry: Height | None,
        seam: Height | None,
        right1: RightToken,
        right2: RightToken,
    ) -> bool | None:
        if when.feature is not None and when.feature not in self.features:
            return False
        if when.self_entry is not None and when.self_entry != ("live" if entry is not None else NONE_STATE):
            return False
        if when.self_exit is not None and when.self_exit != ("live" if seam is not None else NONE_STATE):
            return False
        unknown = False
        if when.word is not None:
            position = word_position(left.kind, right1.kind)
            if position is None:
                unknown = True
            elif position != when.word:
                return False
        if when.left is not None and not self.cond_matches_left(owner, when.left, left, entry):
            return False
        if when.right is not None:
            verdict = self.cond_matches_right(owner, when.right, right1, right2)
            if verdict is False:
                return False
            if verdict is None:
                unknown = True
        return None if unknown else True

    # --- capability ----------------------------------------------------------

    def _entry_available(
        self,
        rune: Rune,
        stance: Stance,
        height: Height,
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> tuple[bool, str | None]:
        """Whether this stance offers a live entry at `height` against `left`: a declared selectable row whose from-scope admits the left, or an active unlock row. Returns (available, note)."""
        row = stance.surface.entries.get(height)
        if row is not None and row.selectable:
            if not row.scope:
                return True, None
            if any(self.cond_matches_left(rune.name, cond, left, height) for cond in row.scope):
                self._record_fired(row.provenance)
                return True, None
        for unlock in stance.surface.unlocks:
            if unlock.entry != height or unlock.feature not in self.features:
                continue
            if unlock.when is None:
                self._record_fired(unlock.provenance)
                return True, f"unlocked by {unlock.feature}"
            verdict = self.when_matches(
                rune.name, unlock.when, left=left, entry=height, seam=None, right1=right1, right2=right2
            )
            if verdict is not False:
                self._record_fired(unlock.provenance)
                return True, f"unlocked by {unlock.feature}"
        return False, None

    def _exit_sources(self, stance: Stance) -> list[tuple[Height, SurfaceRow | None, Unlock | None, int]]:
        sources: list[tuple[Height, SurfaceRow | None, Unlock | None, int]] = []
        for index, (height, row) in enumerate(stance.surface.exits.items()):
            sources.append((height, row, None, index))
        declared = set(stance.surface.exits)
        offset = len(sources)
        for unlock in stance.surface.unlocks:
            if unlock.exit is not None and unlock.exit not in declared and unlock.feature in self.features:
                self._record_fired(unlock.provenance)
                sources.append((unlock.exit, None, unlock, offset))
                offset += 1
        return sources

    def _active_pairing_unlocks(
        self,
        rune: Rune,
        stance: Stance,
        left: LeftContext,
        entry: Height | None,
        right1: RightToken,
        right2: RightToken,
    ) -> list[tuple[str, str]]:
        active: list[tuple[str, str]] = []
        for unlock in stance.surface.unlocks:
            if unlock.pairing is None or unlock.feature not in self.features:
                continue
            if unlock.when is not None:
                verdict = self.when_matches(
                    rune.name, unlock.when, left=left, entry=entry, seam=None, right1=right1, right2=right2
                )
                if verdict is False:
                    continue
            self._record_fired(unlock.provenance)
            active.append((unlock.pairing.entry, unlock.pairing.exit))
        return active

    @staticmethod
    def _pairing_allowed(
        stance: Stance, entry_state: str, exit_state: str, unlocked: list[tuple[str, str]]
    ) -> bool:
        pair = (entry_state, exit_state)
        if pair in unlocked:
            return True
        pairings = stance.surface.pairings
        if any((p.entry, p.exit) == pair for p in pairings.never):
            return False
        if pairings.only is not None:
            return any((p.entry, p.exit) == pair for p in pairings.only)
        return True

    # --- refusals -------------------------------------------------------------

    def _refusal_hit(
        self, rune: Rune, candidate: Candidate, left: LeftContext, right1: RightToken, right2: RightToken
    ) -> tuple[PolicyRecord, bool] | None:
        """The first refuse record on this rune that kills the candidate, with whether the verdict was definite (False = optimistic non-fire on an unknown slot never reaches here). Grains: whole-join (no targets — kills joining candidates), stance, and surface-row (entry/exit height)."""
        for record in rune.policy.refuse:
            if record.stance is not None and record.stance != candidate.stance:
                continue
            if record.entry is not None and record.entry != candidate.entry:
                continue
            if record.exit is not None and record.exit != candidate.seam:
                continue
            if (
                record.stance is None
                and record.entry is None
                and record.exit is None
                and candidate.seam is None
            ):
                continue
            verdict = self.when_matches(
                rune.name,
                record.when,
                left=left,
                entry=candidate.entry,
                seam=candidate.seam,
                right1=right1,
                right2=right2,
            )
            if verdict is True:
                self._record_fired(record.provenance)
                return record, True
        return None

    # --- candidate enumeration -------------------------------------------------

    def candidates(
        self,
        left: LeftContext,
        rune_name: str,
        right1: RightToken,
        right2: RightToken,
        eliminations: list[Elimination] | None = None,
    ) -> list[Candidate]:
        rune = self.spec.runes[rune_name]
        committed = left.settled.seam if (left.kind == "letter" and left.settled is not None) else None
        out: list[Candidate] = []
        order = list(rune.policy.order) or list(rune.stances)
        for stance_name in rune.stances:
            if stance_name not in order:
                order.append(stance_name)
        for stance_name, stance in rune.stances.items():
            order_index = order.index(stance_name)
            entry: Height | None = None
            if committed is not None:
                available, note = self._entry_available(rune, stance, committed, left, right1, right2)
                if not available:
                    if eliminations is not None:
                        eliminations.append(
                            Elimination(
                                "entry-binding",
                                f"{rune_name}.{stance_name}: no available entry row at {committed} against the committed seam",
                            )
                        )
                    continue
                entry = committed
            if "entry" in stance.surface.require and entry is None:
                if eliminations is not None:
                    eliminations.append(
                        Elimination("require", f"{rune_name}.{stance_name}: requires a live entry")
                    )
                continue
            unlocked_pairings = self._active_pairing_unlocks(rune, stance, left, entry, right1, right2)
            entry_state = entry if entry is not None else NONE_STATE
            if right1.kind == "letter":
                for height, row, unlock, exit_index in self._exit_sources(stance):
                    candidate = Candidate(stance_name, entry, height, order_index, exit_index)
                    if not self._pairing_allowed(stance, entry_state, height, unlocked_pairings):
                        if eliminations is not None:
                            eliminations.append(
                                Elimination(
                                    "pairings",
                                    f"{rune_name}.{stance_name}: pairing ({entry_state}, {height}) not allowed",
                                )
                            )
                        continue
                    if row is not None and row.scope:
                        verdicts = [
                            self.cond_matches_right(rune_name, cond, right1, right2) for cond in row.scope
                        ]
                        scoped = any(verdict is not False for verdict in verdicts)
                        if any(verdict is True for verdict in verdicts):
                            self._record_fired(row.provenance)
                        if not scoped:
                            if eliminations is not None:
                                eliminations.append(
                                    Elimination(
                                        "row-scope",
                                        f"{rune_name}.{stance_name}: exit {height} toward-scope does not admit {right1.rune}",
                                        row.provenance,
                                    )
                                )
                            continue
                    if not self._acceptor_exists(candidate, rune_name, right1, right2):
                        if eliminations is not None:
                            eliminations.append(
                                Elimination(
                                    "lookahead-closure",
                                    f"{rune_name}.{stance_name}: exit {height} has no refusal-aware acceptor cell on {right1.rune}",
                                )
                            )
                        continue
                    hit = self._refusal_hit(rune, candidate, left, right1, right2)
                    if hit is not None:
                        if eliminations is not None:
                            record = hit[0]
                            eliminations.append(
                                Elimination(
                                    "refuse",
                                    f"{rune_name}.{stance_name}: exit {height} refused"
                                    + (f" — {record.why}" if record.why else ""),
                                    record.provenance,
                                )
                            )
                        continue
                    out.append(candidate)
            if "exit" in stance.surface.require:
                continue
            non_joining = Candidate(stance_name, entry, None, order_index)
            if not self._pairing_allowed(stance, entry_state, NONE_STATE, unlocked_pairings):
                if eliminations is not None:
                    eliminations.append(
                        Elimination(
                            "pairings",
                            f"{rune_name}.{stance_name}: pairing ({entry_state}, none) not allowed",
                        )
                    )
                continue
            hit = self._refusal_hit(rune, non_joining, left, right1, right2)
            if hit is not None:
                if eliminations is not None:
                    record = hit[0]
                    eliminations.append(
                        Elimination(
                            "refuse",
                            f"{rune_name}.{stance_name}: non-joining cell refused",
                            record.provenance,
                        )
                    )
                continue
            out.append(non_joining)
        return out

    def _virtual_left(self, rune_name: str, candidate: Candidate) -> LeftContext:
        cell = CellId(
            rune=rune_name,
            stance=candidate.stance,
            entry=candidate.entry,
            exit=candidate.seam,
            adjustments=(),
        )
        return LeftContext("letter", Settled(cell=cell, seam=candidate.seam, extension=0))

    def _acceptor_exists(
        self, candidate: Candidate, rune_name: str, right1: RightToken, right2: RightToken
    ) -> bool:
        """Step 2's lookahead closure: some cell of the follower survives its own pairings, require, unlocks, row scopes, and every window-decidable refuse, evaluated with our candidate as the follower's resolved left and the raw right2 as its right. Beyond-window slots are optimistic by construction (UNKNOWN)."""
        if right1.kind != "letter" or right1.rune not in self.spec.runes:
            return False
        key = (rune_name, candidate.stance, candidate.entry, candidate.seam, right1.rune, right2)
        cached = self._closure_cache.get(key)
        if cached is not None:
            return cached
        virtual = self._virtual_left(rune_name, candidate)
        result = bool(self.candidates(virtual, right1.rune, right2, UNKNOWN))
        self._closure_cache[key] = result
        return result

    def _prospect(self, rune_name: str, candidate: Candidate, right1: RightToken, right2: RightToken) -> int:
        """The deliberately optimistic third join-count term: the best refusal-aware static prospect of the (i+1, i+2) seam given this candidate — computed over the follower's surviving cells against the raw right2, optimistic with respect to the follower's own prefers and ordering."""
        if right1.kind != "letter" or right2.kind != "letter":
            return 0
        key = (rune_name, candidate.stance, candidate.entry, candidate.seam, right1.rune, right2.rune)
        cached = self._prospect_cache.get(key)
        if cached is not None:
            return cached
        virtual = self._virtual_left(rune_name, candidate)
        follower_cells = self.candidates(virtual, right1.rune, right2, UNKNOWN)
        result = 1 if any(cell.seam is not None for cell in follower_cells) else 0
        self._prospect_cache[key] = result
        return result

    # --- prefers ----------------------------------------------------------------

    def _prefer_favors(
        self,
        owner: str,
        record: PolicyRecord,
        rune_name: str,
        candidate: Candidate,
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> bool | None:
        """Whether a prefer record speaks for this candidate. Our own rune's record targets the candidate's stance/cell directly; a follower's record votes for candidates under which its preferred continuation is refusal-aware admissible (design section 5.9), with joined_at bound to the candidate's seam. Returns None when the record's when does not match this window at all."""
        if owner == rune_name:
            verdict = self.when_matches(
                owner,
                record.when,
                left=left,
                entry=candidate.entry,
                seam=candidate.seam,
                right1=right1,
                right2=right2,
            )
            if verdict is False:
                return None
            if record.stance is not None:
                return candidate.stance == record.stance
            if record.cell is not None:
                favored = self._cell_pattern_matches(record.cell, candidate)
                if (
                    record.over is not None
                    and not favored
                    and not self._cell_pattern_matches(record.over, candidate)
                ):
                    return None
                return favored
            return None
        if right1.kind != "letter" or right1.rune != owner:
            return None
        virtual = self._virtual_left(rune_name, candidate)
        follower_cells = self.candidates(virtual, owner, right2, UNKNOWN)
        relevant = False
        for cell in follower_cells:
            verdict = self.when_matches(
                owner,
                record.when,
                left=virtual,
                entry=cell.entry,
                seam=cell.seam,
                right1=right2,
                right2=UNKNOWN,
            )
            if verdict is False:
                continue
            relevant = True
            if record.stance is not None and cell.stance == record.stance:
                return True
            if record.cell is not None and self._cell_pattern_matches(record.cell, cell):
                return True
        return False if relevant else None

    @staticmethod
    def _cell_pattern_matches(pattern, candidate: Candidate) -> bool:
        entry_state = candidate.entry if candidate.entry is not None else NONE_STATE
        exit_state = candidate.seam if candidate.seam is not None else NONE_STATE
        wanted_entry = pattern.get("entry")
        wanted_exit = pattern.get("exit")
        if wanted_entry is not None and wanted_entry != entry_state:
            return False
        if wanted_exit is not None and wanted_exit != exit_state:
            return False
        return True

    def _apply_prefers(
        self,
        mode_absolute: bool,
        rune_name: str,
        survivors: list[Candidate],
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
        notes: list[str],
    ) -> list[Candidate]:
        """One prefer stage (absolute or yielding), with records from both seam runes, most-specific first. Nested conflicts resolve silently; equal-or-incomparable records demanding disjoint candidate sets are E-INCOMPARABLE across runes and E-AMBIGUOUS within one."""
        if len(survivors) <= 1:
            return survivors
        gathered: list[tuple[str, PolicyRecord]] = []
        for owner in (
            rune_name,
            right1.rune if right1.kind == "letter" and right1.rune in self.spec.runes else None,
        ):
            if owner is None:
                continue
            for record in self.spec.runes[owner].policy.prefer:
                is_absolute = record.mode == "absolute"
                if is_absolute != mode_absolute:
                    continue
                gathered.append((owner, record))
        if not gathered:
            return survivors
        applicable: list[tuple[str, PolicyRecord, frozenset[Candidate]]] = []
        for owner, record in gathered:
            favored = set()
            relevant = False
            for candidate in survivors:
                vote = self._prefer_favors(owner, record, rune_name, candidate, left, right1, right2)
                if vote is None:
                    continue
                relevant = True
                if vote:
                    favored.add(candidate)
            if relevant and favored and len(favored) < len(survivors):
                applicable.append((owner, record, frozenset(favored)))
        if not applicable:
            return survivors
        ordered = sorted(
            range(len(applicable)),
            key=lambda i: sum(
                1
                for j in range(len(applicable))
                if i != j
                and specificity.outranks(
                    self.spec, applicable[j][1], applicable[i][1], applicable[j][0], applicable[i][0]
                )
                is specificity.Ordering.A_OUTRANKS
            ),
        )
        current = list(survivors)
        applied: list[tuple[str, PolicyRecord]] = []
        for index in ordered:
            owner, record, favored = applicable[index]
            narrowed = [candidate for candidate in current if candidate in favored]
            if narrowed:
                current = narrowed
                applied.append((owner, record))
                self._record_fired(record.provenance)
                notes.append(f"prefer applied: {record.provenance}")
                continue
            for prev_owner, prev_record in applied:
                rank = specificity.outranks(self.spec, prev_record, record, prev_owner, owner)
                if rank in (specificity.Ordering.EQUAL, specificity.Ordering.INCOMPARABLE):
                    message = f"prefer records demand different outcomes at non-nested specificity: {prev_record.provenance} vs {record.provenance}"
                    if prev_owner != owner:
                        raise EIncomparableError(f"E-INCOMPARABLE: {message}")
                    raise EAmbiguousError(f"E-AMBIGUOUS: {message}")
        return current

    # --- extensions ----------------------------------------------------------------

    def _pick_adjustment(
        self,
        kind: str,
        rune: Rune,
        candidate: Candidate,
        side: str,
        height: Height,
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> PolicyRecord | None:
        records = rune.policy.extend if kind == "extend" else rune.policy.contract
        matching = []
        for record in records:
            target_height = record.entry if side == "entry" else record.exit
            other_height = record.exit if side == "entry" else record.entry
            if target_height != height or other_height is not None:
                continue
            if record.stance is not None and record.stance != candidate.stance:
                continue
            verdict = self.when_matches(
                rune.name,
                record.when,
                left=left,
                entry=candidate.entry,
                seam=candidate.seam,
                right1=right1,
                right2=right2,
            )
            if verdict is True:
                matching.append(record)
        if not matching:
            return None
        if len(matching) == 1:
            chosen = matching[0]
        else:
            chosen = specificity.pick_most_specific(self.spec, matching, owners=[rune.name] * len(matching))
        self._record_fired(chosen.provenance)
        return chosen

    @staticmethod
    def _adjustment_tokens(
        prefix: str, extend: PolicyRecord | None, contract: PolicyRecord | None
    ) -> list[str]:
        tokens: list[str] = []
        if extend is not None and extend.by:
            tokens.append(f"{prefix}-ext-{extend.by}")
        if contract is not None:
            if contract.bind is not None:
                tokens.append(f"{prefix}-bind-{contract.bind}")
            if contract.trim is not None:
                tokens.append(f"{prefix}-trim-{contract.trim}")
            if contract.by is not None and contract.bind is None and contract.trim is None:
                tokens.append(f"{prefix}-con-{contract.by}")
        return tokens

    def _withdrawal_tokens(self, stance: Stance, entry: Height | None) -> list[str]:
        """A declined exit row mid-word renders with its withdrawal binding; the bound bitmap is part of the cell's identity, carried as an `ex-bind-<bitmap>` token within the model's closed adjustments grammar. An explicit cells: composition for (entry-state, height-withdrawn) overrides the row binding."""
        entry_state = entry if entry is not None else NONE_STATE
        tokens: list[str] = []
        for height, row in stance.surface.exits.items():
            if row.withdrawal is None or row.withdrawal == "safe":
                continue
            bitmap = row.withdrawal
            for binding in stance.surface.cells:
                if binding.entry == entry_state and binding.exit == f"{height}{WITHDRAWN_SUFFIX}":
                    bitmap = binding.bitmap
            tokens.append(f"ex-bind-{bitmap}")
        return tokens

    # --- the kernel -------------------------------------------------------------------

    def transition_trace(
        self, left: LeftContext, token: RightToken, right1: RightToken, right2: RightToken
    ) -> TransitionTrace:
        if token.kind != "letter":
            return TransitionTrace(boundary_settled(token.kind), False, 0, (), (), "boundary", None, ())
        rune_name = token.rune
        if rune_name not in self.spec.runes:
            raise SettleError(f"{rune_name} is not a modeled rune")
        rune = self.spec.runes[rune_name]
        committed = left.settled.seam if (left.kind == "letter" and left.settled is not None) else None
        locked = left.kind == "zwnj" and is_entry_bearing(self.spec, rune_name)
        notes: list[str] = []
        eliminations: list[Elimination] = []

        survivors = self.candidates(left, rune_name, right1, right2, eliminations)
        # Section 6.3 compensation (b): the YAML pointer of every record that eliminated a candidate in this window rides the notes, so the decision-rule TSVs and the emitted FEA carry per-rule provenance comments.
        for elimination in eliminations:
            if elimination.provenance is not None:
                pointer = str(elimination.provenance)
                if pointer not in notes:
                    notes.append(pointer)
        if not survivors:
            if committed is not None:
                left_label = cell_label(self.spec, left.settled.cell)
                raise EStrandedError(
                    f"E-STRANDED: {left_label} committed an exit at {committed} but {rune_name} has no acceptor cell (the lookahead closure should have prevented this commitment)"
                )
            raise SettleError(f"{rune_name} has no candidate cells at all in this window")

        ranked = {
            candidate: RankedCandidate(
                candidate,
                self._score(rune_name, candidate, committed, right1, right2),
                self._prospect(rune_name, candidate, right1, right2),
            )
            for candidate in survivors
        }
        decided_stage = "only-candidate"
        runner_up: Candidate | None = None

        survivors = self._apply_prefers(True, rune_name, survivors, left, right1, right2, notes)
        if len(survivors) == 1 and decided_stage == "only-candidate" and len(ranked) > 1:
            decided_stage = "absolute-prefer"

        if len(survivors) > 1:
            best = max(ranked[c].join_count for c in survivors)
            narrowed = [c for c in survivors if ranked[c].join_count == best]
            if len(narrowed) < len(survivors):
                losers = [c for c in survivors if c not in narrowed]
                runner_up = losers[0]
                if len(narrowed) == 1:
                    decided_stage = "join-count"
            survivors = narrowed

        if len(survivors) > 1:
            before = list(survivors)
            survivors = self._apply_prefers(False, rune_name, survivors, left, right1, right2, notes)
            if len(survivors) == 1:
                decided_stage = "yielding-prefer"
                runner_up = next(c for c in before if c not in survivors)

        if len(survivors) > 1:
            best_order = min(c.order_index for c in survivors)
            narrowed = [c for c in survivors if c.order_index == best_order]
            if len(narrowed) == 1 and len(survivors) > 1:
                decided_stage = "order"
                runner_up = next(c for c in survivors if c not in narrowed)
            survivors = narrowed

        joint_floor = False
        if len(survivors) > 1:
            heights = self.spec.registry.heights

            def floor_key(candidate: Candidate) -> tuple:
                # Realize-the-left-seam is constant across candidates (entry binding is bilateral), so the floor here is: lower seam height, then exit row declaration order, none last.
                seam_y = heights[candidate.seam] if candidate.seam is not None else 10**6
                return (0 if candidate.seam is not None else 1, seam_y, candidate.exit_index)

            ordered = sorted(survivors, key=floor_key)
            decided_stage = "floor"
            runner_up = ordered[1]
            joint_floor = (ordered[0].seam is None) != (ordered[1].seam is None)
            survivors = [ordered[0]]

        winner = survivors[0]
        settled = self._commit(rune, winner, locked, left, right1, right2, notes)
        return TransitionTrace(
            settled=settled,
            joint_floor=joint_floor,
            prospect=ranked[winner].prospect,
            ranked=tuple(
                sorted(
                    ranked.values(),
                    key=lambda r: (-r.join_count, r.candidate.order_index, r.candidate.exit_index),
                )
            ),
            eliminations=tuple(eliminations),
            decided_stage=decided_stage,
            runner_up=runner_up,
            notes=tuple(notes),
        )

    def _score(
        self,
        rune_name: str,
        candidate: Candidate,
        committed: Height | None,
        right1: RightToken,
        right2: RightToken,
    ) -> int:
        left_term = 1 if committed is not None else 0
        own_term = 1 if candidate.seam is not None else 0
        return left_term + own_term + self._prospect(rune_name, candidate, right1, right2)

    def _commit(
        self,
        rune: Rune,
        winner: Candidate,
        locked: bool,
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
        notes: list[str],
    ) -> Settled:
        stance = rune.stances[winner.stance]
        adjustments: list[str] = []

        def note_applied(record: PolicyRecord | None) -> None:
            if record is not None and record.provenance is not None:
                pointer = str(record.provenance)
                if pointer not in notes:
                    notes.append(pointer)

        if locked:
            adjustments.append("locked")
        if winner.entry is not None:
            available, unlock_note = self._entry_available(rune, stance, winner.entry, left, right1, right2)
            if available and unlock_note is not None and unlock_note not in notes:
                notes.append(unlock_note)
            extend = self._pick_adjustment(
                "extend", rune, winner, "entry", winner.entry, left, right1, right2
            )
            contract = self._pick_adjustment(
                "contract", rune, winner, "entry", winner.entry, left, right1, right2
            )
            note_applied(extend)
            note_applied(contract)
            if extend is not None and left.settled is not None and left.settled.extension > 0:
                notes.append(
                    "entry extension suppressed: the predecessor's exit already carries the seam's connector pixels (same-seam non-summing)"
                )
                extend = None
            adjustments.extend(self._adjustment_tokens("en", extend, contract))
        extension = 0
        if winner.seam is not None:
            extend = self._pick_adjustment("extend", rune, winner, "exit", winner.seam, left, right1, right2)
            contract = self._pick_adjustment(
                "contract", rune, winner, "exit", winner.seam, left, right1, right2
            )
            note_applied(extend)
            note_applied(contract)
            if extend is not None and extend.by:
                extension += extend.by
            if contract is not None and contract.by and contract.bind is None and contract.trim is None:
                extension -= contract.by
            adjustments.extend(self._adjustment_tokens("ex", extend, contract))
        elif right1.kind == "letter":
            adjustments.extend(self._withdrawal_tokens(stance, winner.entry))
        cell = CellId(
            rune=rune.name,
            stance=winner.stance,
            entry=winner.entry,
            exit=winner.seam,
            adjustments=tuple(adjustments),
        )
        return Settled(cell=cell, seam=winner.seam, extension=extension)


# --- tokenization, formation, the fold ----------------------------------------------------


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


def form_ligatures(spec: ResolvedSpec, tokens: list[RightToken]) -> list[RightToken]:
    """Unconditional type-4 formation over the modeled ligature runes, greedy left to right, longest sequence first — staged before everything else, markers included (design section 5.7)."""
    sequences = sorted(
        ((rune.sequence, name) for name, rune in spec.runes.items() if rune.sequence),
        key=lambda item: -len(item[0]),
    )
    formed: list[RightToken] = []
    i = 0
    while i < len(tokens):
        match = None
        if tokens[i].kind == "letter":
            for sequence, name in sequences:
                end = i + len(sequence)
                if end <= len(tokens) and all(
                    tokens[i + k].kind == "letter" and tokens[i + k].rune == part
                    for k, part in enumerate(sequence)
                ):
                    match = (name, len(sequence))
                    break
        if match is not None:
            formed.append(RightToken("letter", match[0]))
            i += match[1]
        else:
            formed.append(tokens[i])
            i += 1
    return formed


def transition(
    spec: ResolvedSpec,
    left: LeftContext,
    token: RightToken,
    right1: RightToken | None,
    right2: RightToken | None,
    features: frozenset[str],
) -> Settled:
    engine = Engine(spec, features)
    return engine.transition_trace(left, token, right1 or EDGE, right2 or EDGE).settled


def settle(
    spec: ResolvedSpec, codepoints: Sequence[int], features: frozenset[str] = frozenset()
) -> list[Settled]:
    engine = Engine(spec, frozenset(features))
    return settle_with_engine(engine, codepoints)


def settle_with_engine(engine: Engine, codepoints: Sequence[int]) -> list[Settled]:
    return [trace.settled for trace in settle_traces(engine, codepoints)]


def settle_traces(engine: Engine, codepoints: Sequence[int]) -> list[TransitionTrace]:
    tokens = form_ligatures(engine.spec, tokens_from_codepoints(engine.spec, codepoints))

    def at(index: int) -> RightToken:
        return tokens[index] if index < len(tokens) else EDGE

    out: list[TransitionTrace] = []
    left = LeftContext("edge")
    for i, token in enumerate(tokens):
        if token.kind != "letter":
            out.append(TransitionTrace(boundary_settled(token.kind), False, 0, (), (), "boundary", None, ()))
            left = LeftContext(token.kind)
            continue
        trace = engine.transition_trace(left, token, at(i + 1), at(i + 2))
        out.append(trace)
        left = LeftContext("letter", trace.settled)
    return out
