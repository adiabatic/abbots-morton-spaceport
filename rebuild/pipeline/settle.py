"""The section 6.1 settlement function over a ResolvedSpec (doc/rebuild-design.md), promoted from prototype/settle.py per the Recon B promotion map.

Per run (boundary to boundary), after guarded type-4 formation (the section 5.7 late-formation guard: a ligature yields per window when leaving its components unformed would realize a seam toward the follower that the formed ligature cannot realize under any capability configuration), left to right: at each position the unit being ranked is the pair candidate (cell of rune i, seam state toward i+1). The kernel implements entry binding with the bilateral-commitment rule and the E-STRANDED raise, the refusal-aware lookahead closure (mutuality is definitional: an exit with no refusal-aware acceptor is never a candidate), refusals from both seam runes at all three grains with except carve-outs, and the strictly lexicographic ranking: absolute prefers (most-specific first) -> window join-count whose third term is the follower's actual simulated choice (issue 28; `SIMULATED_PROSPECT_DEFAULT`, on by default, with the pre-issue-28 optimistic candidacy estimate kept as the AMS_SIMULATED_PROSPECT=0 comparison state) -> yielding prefers -> the runes' declared order: -> the structural floor (lower seam height, row declaration order, none last) -> the weak lead preference (unreachable in practice because the floor is total; kept as the documented final stage). Extensions and contracts apply per (seam, side) by section 6.2 most-specific-wins and never sum on one side; a follower's entry extension is suppressed when the predecessor's exit already carries the seam's pixels (the same-seam non-summing rule, prototype divergence 3).

Boundary semantics: space and ZWNJ split runs and derive word position; the namer dot does not split runs but is addressable as `is: namer-dot` and, having no join surface, breaks adjacency naturally. Post-ZWNJ letters with a live entry surface settle as locked twins (the `locked` adjustment) with the entry side severed — post-ZWNJ behaves word-initial by definition.

Withdrawal is candidate semantics, not a fixup: a join that does not realize mid-word leaves the cell's exit state none, and when the declined exit row binds a named withdrawal bitmap the cell carries an `ex-bind-<bitmap>` adjustment (the model's closed adjustments grammar) so the withdrawn drawing is part of the cell's identity; `withdrawal: safe` rows collapse to the plain exit-none cell. At a boundary the exit was never declined, so the base drawing stands.

`transition` keeps the plan's contract signature and returns Settled; `transition_trace` is the additive rich form the Python verifier and differential oracle consume, mirrored by the crate's `settle-cases` trace for the author-facing explain path and extended with raw third and fourth lookahead slots (`right3` / `right4`, default UNKNOWN) that only an own-rune prefer record's `then:` chain can reach — see `_prefer_favors` for the discipline that keeps every other consumer UNKNOWN-optimistic at those slots.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import NamedTuple

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

# The issue-28 flag, default on since stage 3: engines score the third join-count term by the follower's simulated transition instead of seam-bearing candidacy (see Engine._prospect). Module-level so the default is one edit and a comparison run_m1 can opt out across its spawn-pool workers via AMS_SIMULATED_PROSPECT=0; Engine reads it at construction, so tests may monkeypatch it.
SIMULATED_PROSPECT_DEFAULT = os.environ.get("AMS_SIMULATED_PROSPECT", "1") != "0"

# The issue-28 stage-4b companion flag, default on: follower votes are evaluated over the seat's real shifted slots (vote right1 = seat right2, right2 = seat right3, right3 = seat right4) instead of pinning everything past the vote's own right1 to UNKNOWN, so a chained vote resolves inside the window instead of firing optimistically wherever its then: hop read the pin. Same plumbing contract as SIMULATED_PROSPECT_DEFAULT: module-level, consulted at Engine construction, AMS_VOTE_SLOTS=0 is the comparison state.
VOTE_SLOTS_DEFAULT = os.environ.get("AMS_VOTE_SLOTS", "1") != "0"

_NO_EXIT_INDEX = 9999

_PAIRING_SETS: OrderedDict[
    int, tuple[Stance, frozenset[tuple[str, str]], frozenset[tuple[str, str]] | None]
] = OrderedDict()
_PAIRING_SETS_CAP = 8


class SettleError(Exception):
    pass


class EStrandedError(SettleError):
    """A committed exit found no acceptor row at the next position — the lookahead closure should make this unreachable in real settlement; reaching it means a spec or kernel bug."""


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

    def __init__(
        self,
        spec: ResolvedSpec,
        features: frozenset[str],
        vote_deep_slot: RightToken | None = None,
        simulated_prospect: bool | None = None,
        vote_slots: bool | None = None,
        trace_memo: bool = False,
    ):
        self.spec = spec
        self.features = frozenset(features)
        # The follower-vote's beyond-right1 slot when `vote_slots` is off (design section 5.9): UNKNOWN-optimistic in that comparison state to confine deep-window behavior to own-rune records, and bound to the window edge by the section 5.7 guard's dedicated engines — a guard verdict is a function of two raw slots, so a vote that would need deeper text to fire definitively must not flip a formation verdict. With `vote_slots` on (the stage-4b companion) real settlement hands the vote its shifted real slots instead and this pin only ever carries the guard's EDGE.
        self._vote_deep_slot = vote_deep_slot if vote_deep_slot is not None else UNKNOWN
        # None consults the module default at construction time (not import time), so a test can monkeypatch SIMULATED_PROSPECT_DEFAULT and every engine built afterward follows.
        self.simulated_prospect = (
            SIMULATED_PROSPECT_DEFAULT if simulated_prospect is None else simulated_prospect
        )
        self.vote_slots = VOTE_SLOTS_DEFAULT if vote_slots is None else vote_slots
        # How often a simulated prospect's counterfactual cascade raised and fell back to the candidacy-grain estimate; diagnostic only.
        self.simulated_prospect_fallbacks = 0
        self._closure_cache: dict[tuple, tuple[bool, tuple[str, ...]]] = {}
        self._order_index_cache: dict[str, dict[str, int]] = {}
        self._exit_sources_cache: dict[
            int,
            tuple[
                Stance,
                list[tuple[Height, SurfaceRow | None, Unlock | None, int]],
                tuple[Provenance | None, ...],
            ],
        ] = {}
        self._virtual_left_cache: dict[tuple[str, Candidate], LeftContext] = {}
        self._candidates_cache: dict[
            tuple, tuple[list[Candidate], tuple[Elimination, ...], tuple[str, ...]]
        ] = {}
        self._prospect_cache: dict[tuple, tuple[int, tuple[str, ...]]] = {}
        self._trace_cache: dict[tuple, tuple[TransitionTrace, tuple[str, ...]]] | None = (
            {} if trace_memo else None
        )
        # YAML provenance of every authored record that demonstrably fired during settlement under this configuration: refusals that killed a candidate, unlocks that granted capability, row scopes that admitted a side, and extends/contracts/prefers that shaped a committed cell. Closure and prospect evaluations count — a refusal firing inside the lookahead closure is load-bearing for the window that consulted it. The dead-policy gate reads this through DecisionTable.cited_provenance.
        self.fired: set[str] = set()
        # Under `trace_memo`, every fired pointer is also journaled so each memoized evaluation can record its own fired-closure delta (`_begin_capture` / `_end_capture`). The fire-dependent caches would otherwise confound attribution — the first evaluation to consult a sub-result fires its records, later consumers hit the cache silently — so every cache hit replays its stored delta into the journal, making each entry's delta order-independent. Persisting a trace without its delta would starve `fired` of exactly the pointers only served windows exercise, and the dead-policy gate would read live records as dead.
        self._fired_log: list[str] | None = [] if trace_memo else None
        self._capture_starts: list[int] = []
        self._pointer_intern: dict[str, str] = {}
        # Value-interning pools: settlement mints the same few hundred distinct committed cells, settleds, and fired-delta tuples millions of times over, and the memos retain them all — one shared instance per value keeps that retention flat (issue 53).
        self._cell_intern: dict[CellId, CellId] = {}
        self._settled_intern: dict[Settled, Settled] = {}
        self._delta_intern: dict[tuple[str, ...], tuple[str, ...]] = {}

    def _record_fired(self, provenance: Provenance | None) -> None:
        if provenance is not None:
            pointer = str(provenance)
            log = self._fired_log
            if log is not None:
                pointer = self._pointer_intern.setdefault(pointer, pointer)
                if self._capture_starts:
                    log.append(pointer)
            self.fired.add(pointer)

    def _replay_fired(self, delta: tuple[str, ...]) -> None:
        if delta:
            self.fired.update(delta)
            if self._capture_starts:
                log = self._fired_log
                assert log is not None
                log.extend(delta)

    def _begin_capture(self) -> None:
        log = self._fired_log
        assert log is not None
        self._capture_starts.append(len(log))

    def _end_capture(self) -> tuple[str, ...]:
        log = self._fired_log
        assert log is not None
        start = self._capture_starts.pop()
        delta = tuple(dict.fromkeys(log[start:]))
        delta = self._delta_intern.setdefault(delta, delta)
        if not self._capture_starts:
            del log[:]
        return delta

    def _abort_capture(self) -> None:
        # A raising evaluation records no delta, matching the never-cache-raising-windows rule, but its firings stay journaled for any enclosing capture — they demonstrably fired during that evaluation and a fresh replay would fire them again.
        assert self._fired_log is not None
        self._capture_starts.pop()
        if not self._capture_starts:
            del self._fired_log[:]

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
        self, owner: str | None, cond: Condition, tokens: tuple[RightToken, ...]
    ) -> bool | None:
        """Static raw-right matching over the window's remaining raw slots: `tokens[0]` is the slot this condition tests, a `then:` hop recurses on the tail, and an `except:` entry tests the same slot with its own `then:` hops walking the same tail — so a chain reads one raw token per hop and exhausts to UNKNOWN past the supplied window. Returns None when the verdict depends on a token outside the evaluated window (the `unknown` kind) — callers treat None optimistically for refusals and unlocks, which is the deliberate optimism of the closure and the prospect term."""
        token = tokens[0]
        tail = tokens[1:] if len(tokens) > 1 else (UNKNOWN,)
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
                if cond.family and token.letter not in cond.family:
                    return False
                for klass in cond.klass:
                    if token.letter not in self._members(klass, owner):
                        return False
                if cond.stroke is not None and cond.stroke not in self._rune_entry_strokes(token.letter):
                    return False
        for ex in cond.except_:
            sub = self.cond_matches_right(owner, ex, tokens)
            if sub is True:
                return False
            if sub is None:
                unknown = True
        if cond.then is not None:
            sub = self.cond_matches_right(owner, cond.then, tail)
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
        right3: RightToken = UNKNOWN,
        right4: RightToken = UNKNOWN,
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
            verdict = self.cond_matches_right(owner, when.right, (right1, right2, right3, right4))
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
        cached = self._exit_sources_cache.get(id(stance))
        if cached is not None and cached[0] is stance:
            for provenance in cached[2]:
                self._record_fired(provenance)
            return cached[1]
        sources, fired = self._exit_sources_uncached(stance)
        self._exit_sources_cache[id(stance)] = (stance, sources, fired)
        for provenance in fired:
            self._record_fired(provenance)
        return sources

    def _exit_sources_uncached(
        self, stance: Stance
    ) -> tuple[list[tuple[Height, SurfaceRow | None, Unlock | None, int]], tuple[Provenance | None, ...]]:
        fired: list[Provenance | None] = []
        sources: list[tuple[Height, SurfaceRow | None, Unlock | None, int]] = []
        for index, (height, row) in enumerate(stance.surface.exits.items()):
            sources.append((height, row, None, index))
        declared = set(stance.surface.exits)
        offset = len(sources)
        for unlock in stance.surface.unlocks:
            if unlock.exit is not None and unlock.exit not in declared and unlock.feature in self.features:
                fired.append(unlock.provenance)
                sources.append((unlock.exit, None, unlock, offset))
                offset += 1
        return sources, tuple(fired)

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
        sets = _PAIRING_SETS.get(id(stance))
        if sets is None or sets[0] is not stance:
            pairings = stance.surface.pairings
            sets = (
                stance,
                frozenset((p.entry, p.exit) for p in pairings.never),
                None if pairings.only is None else frozenset((p.entry, p.exit) for p in pairings.only),
            )
            _PAIRING_SETS[id(stance)] = sets
            while len(_PAIRING_SETS) > _PAIRING_SETS_CAP:
                _PAIRING_SETS.popitem(last=False)
        else:
            _PAIRING_SETS.move_to_end(id(stance))
        _keep, never, only = sets
        if pair in never:
            return False
        if only is not None:
            return pair in only
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
        if self._fired_log is None:
            return self._candidates_uncached(left, rune_name, right1, right2, eliminations)
        settled = left.settled
        key = (
            left.kind,
            settled.cell.rune if settled is not None else None,
            settled.cell.stance if settled is not None else None,
            settled.seam if settled is not None else None,
            rune_name,
            right1,
            right2,
        )
        cached = self._candidates_cache.get(key)
        if cached is None:
            local_eliminations: list[Elimination] = []
            self._begin_capture()
            try:
                out = self._candidates_uncached(left, rune_name, right1, right2, local_eliminations)
            except BaseException:
                self._abort_capture()
                raise
            cached = (out, tuple(local_eliminations), self._end_capture())
            self._candidates_cache[key] = cached
        else:
            self._replay_fired(cached[2])
        if eliminations is not None:
            eliminations.extend(cached[1])
        return cached[0]

    def _candidates_uncached(
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
        order_index_by_stance = self._order_index_cache.get(rune_name)
        if order_index_by_stance is None:
            order = list(rune.policy.order) or list(rune.stances)
            for stance_name in rune.stances:
                if stance_name not in order:
                    order.append(stance_name)
            order_index_by_stance = {stance_name: order.index(stance_name) for stance_name in rune.stances}
            self._order_index_cache[rune_name] = order_index_by_stance
        for stance_name, stance in rune.stances.items():
            order_index = order_index_by_stance[stance_name]
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
                            self.cond_matches_right(rune_name, cond, (right1, right2)) for cond in row.scope
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
        key = (rune_name, candidate)
        cached = self._virtual_left_cache.get(key)
        if cached is not None:
            return cached
        cached = self._virtual_left_uncached(rune_name, candidate)
        self._virtual_left_cache[key] = cached
        return cached

    @staticmethod
    def _virtual_left_uncached(rune_name: str, candidate: Candidate) -> LeftContext:
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
        key = (rune_name, candidate.stance, candidate.entry, candidate.seam, right1.letter, right2)
        cached = self._closure_cache.get(key)
        if cached is not None:
            result, delta = cached
            if self._fired_log is not None:
                self._replay_fired(delta)
            return result
        if self._fired_log is None:
            result = bool(
                self.candidates(self._virtual_left(rune_name, candidate), right1.letter, right2, UNKNOWN)
            )
            self._closure_cache[key] = (result, ())
            return result
        self._begin_capture()
        try:
            result = bool(
                self.candidates(self._virtual_left(rune_name, candidate), right1.letter, right2, UNKNOWN)
            )
        except BaseException:
            self._abort_capture()
            raise
        self._closure_cache[key] = (result, self._end_capture())
        return result

    def _prospect(
        self,
        rune_name: str,
        candidate: Candidate,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken = UNKNOWN,
        right4: RightToken = UNKNOWN,
    ) -> int:
        """The third join-count term: the prospect of the (i+1, i+2) seam given this candidate. With `simulated_prospect` on (the shipping default since issue 28's stage 3), the term is the follower's actual simulated transition: the full cascade with the window shifted one right (virtual left = the candidate, slots = right2/right3/right4/UNKNOWN), 1 iff the simulated winner carries a seam. The recursion the inner cascade's own prospect term opens only moves rightward with strictly shrinking slots and bottoms out at the window edge, where non-letter slots return 0 — today's epistemic state, kept on purpose, so beyond-window text stays exactly as unknowable as it is now. A counterfactual cascade can raise where real settlement never would (a prefer conflict or a definitively-firing unlock scope in a window whose candidate never wins); those evaluations fall back to the candidacy-grain estimate — the honest cannot-rank state — and count in `simulated_prospect_fallbacks`. With `simulated_prospect` off (the section 5.7 guard engines' pin, and the AMS_SIMULATED_PROSPECT=0 comparison state), the term is the pre-issue-28 optimistic candidacy estimate — 1 iff any seam-bearing follower cell survives `candidates()`, refusal-aware but blind to the follower's prefers and ordering."""
        if right1.kind != "letter" or right2.kind != "letter":
            return 0
        if not self.simulated_prospect:
            key = (rune_name, candidate.stance, candidate.entry, candidate.seam, right1.letter, right2.letter)
            cached = self._prospect_cache.get(key)
            if cached is not None:
                result, delta = cached
                if self._fired_log is not None:
                    self._replay_fired(delta)
                return result
            capturing = self._fired_log is not None
            if capturing:
                self._begin_capture()
            try:
                virtual = self._virtual_left(rune_name, candidate)
                follower_cells = self.candidates(virtual, right1.letter, right2, UNKNOWN)
                result = 1 if any(cell.seam is not None for cell in follower_cells) else 0
            except BaseException:
                if capturing:
                    self._abort_capture()
                raise
            self._prospect_cache[key] = (result, self._end_capture() if capturing else ())
            return result
        key = (
            rune_name,
            candidate.stance,
            candidate.entry,
            candidate.seam,
            right1.letter,
            right2,
            right3,
            right4,
        )
        cached = self._prospect_cache.get(key)
        if cached is not None:
            result, delta = cached
            if self._fired_log is not None:
                self._replay_fired(delta)
            return result
        capturing = self._fired_log is not None
        if capturing:
            self._begin_capture()
        try:
            virtual = self._virtual_left(rune_name, candidate)
            try:
                trace = self.transition_trace(virtual, right1, right2, right3, right4, UNKNOWN)
                result = 1 if trace.settled.seam is not None else 0
            except EIncomparableError, EAmbiguousError, SettleError:
                self.simulated_prospect_fallbacks += 1
                follower_cells = self.candidates(virtual, right1.letter, right2, UNKNOWN)
                result = 1 if any(cell.seam is not None for cell in follower_cells) else 0
        except BaseException:
            if capturing:
                self._abort_capture()
            raise
        self._prospect_cache[key] = (result, self._end_capture() if capturing else ())
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
        right3: RightToken,
        right4: RightToken,
    ) -> bool | None:
        """Whether a prefer record speaks for this candidate. Our own rune's record targets the candidate's stance/cell directly; a follower's record votes for candidates under which its preferred continuation is refusal-aware admissible (design section 5.9), with joined_at bound to the candidate's seam. Returns None when the record's when does not match this window at all. The own-rune branch reads the raw third and fourth slots directly; the vote branch is evaluated one position over, where those slots are its right1/right2/right3, so with `vote_slots` on (the issue-28 stage-4b companion) it is handed the seat's slots shifted once — right2/right3/right4 — and its own fourth slot is the window edge's honest UNKNOWN. With `vote_slots` off everything past the vote's right1 is pinned to `_vote_deep_slot`, whose None verdicts count as firing — the pre-stage-4b optimism that forced a deep-chained fact to be restated on every possible left rune instead of living once on the rune that owns it. Refusals, unlocks, and the closure keep their deep slots UNKNOWN-optimistic in every mode."""
        if owner == rune_name:
            verdict = self.when_matches(
                owner,
                record.when,
                left=left,
                entry=candidate.entry,
                seam=candidate.seam,
                right1=right1,
                right2=right2,
                right3=right3,
                right4=right4,
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
        if self.vote_slots:
            vote_right2, vote_right3 = right3, right4
        else:
            vote_right2, vote_right3 = self._vote_deep_slot, UNKNOWN
        follower_cells = self.candidates(virtual, owner, right2, vote_right2)
        relevant = False
        for cell in follower_cells:
            verdict = self.when_matches(
                owner,
                record.when,
                left=virtual,
                entry=cell.entry,
                seam=cell.seam,
                right1=right2,
                right2=vote_right2,
                right3=vote_right3,
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
        right3: RightToken,
        right4: RightToken,
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
                vote = self._prefer_favors(
                    owner, record, rune_name, candidate, left, right1, right2, right3, right4
                )
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
                if rank not in (specificity.Ordering.EQUAL, specificity.Ordering.INCOMPARABLE):
                    continue
                if prev_owner == owner:
                    raise EAmbiguousError(
                        f"E-AMBIGUOUS: prefer records demand different outcomes at non-nested specificity: {prev_record.provenance} vs {record.provenance}"
                    )
                resolved = self._apply_resolution(
                    prev_owner,
                    prev_record,
                    owner,
                    record,
                    survivors,
                    left,
                    right1,
                    right2,
                    right3,
                    right4,
                    notes,
                )
                if resolved is not None:
                    current = resolved
                    applied.append((owner, record))
                    break
                raise EIncomparableError(
                    self._incomparable_message(
                        prev_owner, prev_record, owner, record, rune_name, survivors, left, right1, right2
                    )
                )
        return current

    def _apply_resolution(
        self,
        a_owner: str,
        a_record: PolicyRecord,
        b_owner: str,
        b_record: PolicyRecord,
        survivors: list[Candidate],
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken,
        right4: RightToken,
        notes: list[str],
    ) -> list[Candidate] | None:
        """The section 5.8 against-a-named-record slice: a crossing between two runes' prefers resolves without an error when a resolve on either rune names the other record in `against:` and its `when:` matches the window (unknown deep slots count as matching, the refusal-and-unlock optimism). The `pick:` pattern filters the stage's full survivor set — the resolve overrides both colliding records, not just the later one — and its provenance lands in the fired set and the notes so explain and the dead-policy gate see it. Matching resolves that disagree on the pick, or a pick admitting no survivor, stay hard errors."""
        matches: list[tuple[str, PolicyRecord]] = []
        for holder, other_owner, other_record in (
            (a_owner, b_owner, b_record),
            (b_owner, a_owner, a_record),
        ):
            holder_rune = self.spec.runes.get(holder)
            if holder_rune is None:
                continue
            for res in holder_rune.policy.resolve:
                if res.against is None or res.pick is None:
                    continue
                target_name, target_id = res.against
                if target_name != other_owner:
                    continue
                if target_id is not None and target_id != other_record.id:
                    continue
                verdict = self.when_matches(
                    holder,
                    res.when,
                    left=left,
                    entry=None,
                    seam=None,
                    right1=right1,
                    right2=right2,
                    right3=right3,
                    right4=right4,
                )
                if verdict is False:
                    continue
                matches.append((holder, res))
        if not matches:
            return None
        picks = {tuple(sorted(res.pick.items())) for _, res in matches if res.pick is not None}
        if len(picks) > 1:
            described = "; ".join(str(res.provenance) for _, res in matches)
            raise EIncomparableError(
                f"E-INCOMPARABLE: conflicting resolve records match one window: {described}"
            )
        _, res = matches[0]
        assert res.pick is not None
        picked = [c for c in survivors if self._resolve_pick_matches(res.pick, c)]
        if not picked:
            raise EIncomparableError(
                f"E-INCOMPARABLE: resolve {res.provenance} matched but its pick admits no surviving candidate"
            )
        self._record_fired(res.provenance)
        notes.append(f"resolve applied: {res.provenance}")
        return picked

    @staticmethod
    def _resolve_pick_matches(pick, candidate: Candidate) -> bool:
        wanted_stance = pick.get("stance")
        if wanted_stance is not None and candidate.stance != wanted_stance:
            return False
        cell_pattern = {key: value for key, value in pick.items() if key in ("entry", "exit")}
        return Engine._cell_pattern_matches(cell_pattern, candidate) if cell_pattern else True

    def _incomparable_message(
        self,
        a_owner: str,
        a_record: PolicyRecord,
        b_owner: str,
        b_record: PolicyRecord,
        rune_name: str,
        survivors: list[Candidate],
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> str:
        left_name = left.settled.cell.rune if left.kind == "letter" and left.settled is not None else None
        tokens = [left_name, rune_name] + [t.rune for t in (right1, right2) if t.kind == "letter"]
        example = " ".join(name for name in tokens if name)
        cells = ", ".join(
            f"({c.stance}, entry {c.entry or 'none'}, exit {c.seam or 'none'})" for c in survivors
        )
        other_owner, other_record = (b_owner, b_record) if a_owner == rune_name else (a_owner, a_record)
        against_id = other_record.id or "<give that record an id: first>"
        when_clause = ""
        if right1.kind == "letter":
            inner = f"family: {right1.rune}"
            if right2.kind == "letter":
                inner += f", then: {{family: {right2.rune}}}"
            when_clause = f"    when: {{right: {{{inner}}}}}\n"
        stub = (
            f"  - against: {{rune: {other_owner}, id: {against_id}}}\n"
            f"{when_clause}"
            "    pick: {exit: <the winning cell>}\n"
            "    why: <author rationale, mandatory>"
        )
        return (
            f"E-INCOMPARABLE: prefer records demand different outcomes at non-nested specificity: {a_record.provenance} vs {b_record.provenance}.\n"
            f"  example window: {example}\n"
            f"  conflicted candidates on {rune_name}: {cells}\n"
            f"  paste-ready resolve for glyph_data/runes/{rune_name}.yaml policy.resolve (design section 5.8):\n{stub}"
        )

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
        self,
        left: LeftContext,
        token: RightToken,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken = UNKNOWN,
        right4: RightToken = UNKNOWN,
    ) -> TransitionTrace:
        """Under `trace_memo` — the table fixpoint's engines, where lefts arrive as fully settled cells — results are memoized over the collapsed left key (kind, and the settled cell's rune, stance, seam, extension): every left read in the kernel goes through those fields — condition matching consults the cell's rune and stance, `_left_exit_stroke` the committed seam, scoring the seam's presence, and the same-seam non-summing suppression the extension — never the left cell's entry or adjustments, so settled lefts differing only there trace identically and share one entry. Raising windows are not cached: the E-STRANDED message reads the full left label, and the liveness probes that trip settlement errors memoize their own verdicts above this. A cache hit replays its journaled fired-pointer delta so `fired` fills exactly as a recomputation would. Everywhere else the memo stays off — the conform walker already memoizes at window grain above this call, so a second cache underneath would hold a full trace per window in memory and never be read."""
        if token.kind != "letter":
            return TransitionTrace(boundary_settled(token.kind), False, 0, (), (), "boundary", None, ())
        cache = self._trace_cache
        if cache is None:
            return self._transition_trace_uncached(left, token, right1, right2, right3, right4)
        settled = left.settled
        key = (
            left.kind,
            settled.cell.rune if settled is not None else None,
            settled.cell.stance if settled is not None else None,
            settled.seam if settled is not None else None,
            settled.extension if settled is not None else 0,
            token.rune,
            right1,
            right2,
            right3,
            right4,
        )
        entry = cache.get(key)
        if entry is not None:
            trace, delta = entry
            self._replay_fired(delta)
            return trace
        self._begin_capture()
        try:
            trace = self._transition_trace_uncached(left, token, right1, right2, right3, right4)
        except BaseException:
            self._abort_capture()
            raise
        cache[key] = (trace, self._end_capture())
        return trace

    def _transition_trace_uncached(
        self,
        left: LeftContext,
        token: RightToken,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken,
        right4: RightToken,
    ) -> TransitionTrace:
        rune_name = token.letter
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
                assert left.settled is not None
                left_label = cell_label(self.spec, left.settled.cell)
                raise EStrandedError(
                    f"E-STRANDED: {left_label} committed an exit at {committed} but {rune_name} has no acceptor cell (the lookahead closure should have prevented this commitment)"
                )
            raise SettleError(f"{rune_name} has no candidate cells at all in this window")

        ranked = {
            candidate: RankedCandidate(
                candidate,
                self._score(rune_name, candidate, committed, right1, right2, right3, right4),
                self._prospect(rune_name, candidate, right1, right2, right3, right4),
            )
            for candidate in survivors
        }
        decided_stage = "only-candidate"
        runner_up: Candidate | None = None

        survivors = self._apply_prefers(
            True, rune_name, survivors, left, right1, right2, right3, right4, notes
        )
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
            survivors = self._apply_prefers(
                False, rune_name, survivors, left, right1, right2, right3, right4, notes
            )
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
        right3: RightToken = UNKNOWN,
        right4: RightToken = UNKNOWN,
    ) -> int:
        left_term = 1 if committed is not None else 0
        own_term = 1 if candidate.seam is not None else 0
        return left_term + own_term + self._prospect(rune_name, candidate, right1, right2, right3, right4)

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
        cell = self._cell_intern.setdefault(cell, cell)
        settled = Settled(cell=cell, seam=winner.seam, extension=extension)
        return self._settled_intern.setdefault(settled, settled)


# --- late formation (design section 5.7) ----------------------------------------------------

# Guard state per spec, shared across every Engine so the conform sweep's per-text engines reuse the verdicts. Entries hold the spec strongly, so an id can never be reused while its entry lives; the small LRU cap keeps test runs (which load many specs) bounded.
_GUARD_STATES: OrderedDict[int, tuple[ResolvedSpec, dict]] = OrderedDict()
_GUARD_STATES_CAP = 8


def _guard_state(spec: ResolvedSpec) -> dict:
    entry = _GUARD_STATES.get(id(spec))
    if entry is not None and entry[0] is spec:
        _GUARD_STATES.move_to_end(id(spec))
        return entry[1]
    capability_features = sorted(
        {
            unlock.feature
            for rune in spec.runes.values()
            for stance in rune.stances.values()
            for unlock in stance.surface.unlocks
        }
    )
    # simulated_prospect and vote_slots stay pinned off here regardless of the module defaults: a section 5.7 verdict is a config-blind pure function of two raw slots compiled into the formation lookup, and letting the issue-28 estimator (or the stage-4b shifted vote slots — which would trade the deliberate EDGE pin for the trace's own EDGE-bound deep slots and quietly widen what a vote's then: chain can read) change what the trail's ranking-grain trace scores would flip formation verdicts as a side effect of a settlement-scoring change — if the guard is ever to follow either, that is its own reviewed change with its own flip inventory.
    engines = tuple(
        Engine(spec, frozenset(combo), vote_deep_slot=EDGE, simulated_prospect=False, vote_slots=False)
        for size in range(len(capability_features) + 1)
        for combo in combinations(capability_features, size)
    )
    state = {"engines": engines, "verdicts": {}}
    _GUARD_STATES[id(spec)] = (spec, state)
    while len(_GUARD_STATES) > _GUARD_STATES_CAP:
        _GUARD_STATES.popitem(last=False)
    return state


def _follower_formation(spec: ResolvedSpec, right1: RightToken, right2: RightToken) -> str | None:
    """The ligature the guard's two raw slots will themselves have formed by the time the guarded rule's own window settles: the modeled rune whose sequence is exactly (right1, right2) and whose own guard, evaluated with its slots unknown-optimistic, does not block. None when the slots are not a forming pair."""
    if right1.kind != "letter" or right2.kind != "letter":
        return None
    for name, rune in spec.runes.items():
        if (
            rune.sequence is not None
            and tuple(rune.sequence) == (right1.rune, right2.rune)
            and not formation_blocked(spec, name, UNKNOWN, UNKNOWN)
        ):
            return name
    return None


def _blocked_under(engine: Engine, liga_name: str, right1: RightToken, right2: RightToken) -> bool:
    rune = engine.spec.runes[liga_name]
    assert rune.sequence is not None
    lead, trail = rune.sequence[-2], rune.sequence[-1]
    formed = _follower_formation(engine.spec, right1, right2)
    if formed is not None:
        right1, right2 = RightToken("letter", formed), UNKNOWN
    virtual = LeftContext(
        "letter",
        Settled(
            cell=CellId(
                rune=lead,
                stance=engine.spec.runes[lead].default_stance,
                entry=None,
                exit=None,
                adjustments=(),
            ),
            seam=None,
            extension=0,
        ),
    )
    if not any(c.seam is not None for c in engine.candidates(virtual, trail, right1, right2)):
        return False
    if (
        engine.transition_trace(virtual, RightToken("letter", trail), right1, right2, EDGE, EDGE).settled.seam
        is None
    ):
        return False
    return not any(
        c.seam is not None for c in engine.candidates(LeftContext("edge"), liga_name, right1, right2)
    )


def formation_blocked(spec: ResolvedSpec, liga_name: str, right1: RightToken, right2: RightToken) -> bool:
    """The section 5.7 late-formation guard: the ligature yields to its components in this window iff the trailing component, left unformed, would realize a seam toward the follower while the formed ligature could realize none — the trail side settled at ranking grain (a full `transition_trace` with the lead's default unjoined stance as its left, so follower votes and the runes' prefers count, not just candidacy: ·See's grounded prefer withholds the unformed ·Utter's reach before ·See·Low, and the ligature forms exactly as the shipped font does), the ligature side kept generously at candidacy grain with the run edge as its left. The guard's dedicated engines bind every slot past the two the verdict is keyed on to the window edge — `vote_deep_slot=EDGE`, plus EDGE third/fourth slots on the trace — so a vote or prefer whose condition would need deeper raw text to fire definitively never flips a formation verdict (·Day·Utter·Utter·Tea stays blocked: the vote that withholds the trail's reach there rides an unknown-slot chain, unlike ·See's grounded prefer, which fires inside the window). The verdict is quantified over the powerset of capability-unlock features and fires only when every configuration agrees, because the emitted formation lookup stages before the ss marker substitutions and is therefore config-blind by design. `right1`/`right2` are the raw tokens after the ligature's sequence — the same slots the emitted lookup reads — so the guard never depends on state formation cannot see. One refinement over the raw slots: when they are themselves a forming ligature pair (`_follower_formation`), both tests face that ligature, not the bare first slot — a follower whose entry the pair's own formation is about to consume must not count as reachable, else the guard un-forms the left ligature in service of a seam the settled world cannot contain (·Day·Utter·See·Utter is the worked case: the old font forms both ligatures, and the raw-slot reading kept ·Day·Utter apart to serve a ·See that qsSee_qsUtter swallows). The verdict stays a pure function of the two raw slots, so it still compiles to the same lookahead classes the emitted lookup reads."""
    if right1.kind != "letter":
        return False
    state = _guard_state(spec)
    key = (liga_name, right1, right2)
    verdict = state["verdicts"].get(key)
    if verdict is None:
        verdict = all(_blocked_under(engine, liga_name, right1, right2) for engine in state["engines"])
        state["verdicts"][key] = verdict
    return verdict


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


def form_ligatures(
    spec: ResolvedSpec,
    tokens: list[RightToken],
    guard_verdicts: Mapping[tuple[str, RightToken, RightToken], bool] | None = None,
) -> list[RightToken]:
    """Type-4 formation over the modeled ligature runes, greedy left to right, longest sequence first — staged before everything else, markers included, each match yielding to the section 5.7 late-formation guard over the two raw tokens past the sequence (design section 5.7). `guard_verdicts` supplies the crate's complete guard sweep to author-facing callers; the independent Python guard remains the default for conformance and the differential oracle."""
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
                    right1 = tokens[end] if end < len(tokens) else EDGE
                    right2 = tokens[end + 1] if end + 1 < len(tokens) else EDGE
                    blocked = (
                        formation_blocked(spec, name, right1, right2)
                        if guard_verdicts is None
                        else right1.kind == "letter" and guard_verdicts[(name, right1, right2)]
                    )
                    if blocked:
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
        trace = engine.transition_trace(left, token, at(i + 1), at(i + 2), at(i + 3), at(i + 4))
        out.append(trace)
        left = LeftContext("letter", trace.settled)
    return out
