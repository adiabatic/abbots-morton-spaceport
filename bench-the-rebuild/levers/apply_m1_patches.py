"""Apply pure-Python M1 levers to a COPY of rebuild/ under bench-the-rebuild/levers/.

Never touches a tracked file: it edits only the variant tree it is handed.

  apply_m1_patches.py <variant_root> p1 p2 p3 p4 p5
"""

from __future__ import annotations

import sys
from pathlib import Path


def sub(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"patch {label}: expected exactly 1 occurrence, found {text.count(old)}")
    return text.replace(old, new)


# --- P1: memoize the per-rune stance order index (kills a per-call list rebuild + O(n^2) list.index) ---

P1_OLD = """        out: list[Candidate] = []
        order = list(rune.policy.order) or list(rune.stances)
        for stance_name in rune.stances:
            if stance_name not in order:
                order.append(stance_name)
        for stance_name, stance in rune.stances.items():
            order_index = order.index(stance_name)
"""
P1_NEW = """        out: list[Candidate] = []
        order_index_by_stance = self._order_index_cache.get(rune_name)
        if order_index_by_stance is None:
            order = list(rune.policy.order) or list(rune.stances)
            for stance_name in rune.stances:
                if stance_name not in order:
                    order.append(stance_name)
            order_index_by_stance = self._order_index_cache[rune_name] = {
                stance_name: order.index(stance_name) for stance_name in rune.stances
            }
        for stance_name, stance in rune.stances.items():
            order_index = order_index_by_stance[stance_name]
"""

# --- P2: memoize _exit_sources per stance, replaying its unlock firings ---

P2_OLD = """    def _exit_sources(self, stance: Stance) -> list[tuple[Height, SurfaceRow | None, Unlock | None, int]]:
        sources: list[tuple[Height, SurfaceRow | None, Unlock | None, int]] = []
"""
P2_NEW = """    def _exit_sources(self, stance: Stance) -> list[tuple[Height, SurfaceRow | None, Unlock | None, int]]:
        cached = self._exit_sources_cache.get(id(stance))
        if cached is not None:
            _keep, sources, fired = cached
            for provenance in fired:
                self._record_fired(provenance)
            return sources
        sources, fired = self._exit_sources_uncached(stance)
        self._exit_sources_cache[id(stance)] = (stance, sources, fired)
        for provenance in fired:
            self._record_fired(provenance)
        return sources

    def _exit_sources_uncached(self, stance: Stance):
        fired: list[Provenance] = []
        sources: list[tuple[Height, SurfaceRow | None, Unlock | None, int]] = []
"""
P2_OLD2 = """            if unlock.exit is not None and unlock.exit not in declared and unlock.feature in self.features:
                self._record_fired(unlock.provenance)
                sources.append((unlock.exit, None, unlock, offset))
                offset += 1
        return sources
"""
P2_NEW2 = """            if unlock.exit is not None and unlock.exit not in declared and unlock.feature in self.features:
                fired.append(unlock.provenance)
                sources.append((unlock.exit, None, unlock, offset))
                offset += 1
        return sources, fired
"""

# --- P3: precompute a stance's pairing sets once instead of scanning lists per call ---

P3_OLD = """    @staticmethod
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
"""
P3_NEW = """    @staticmethod
    def _pairing_allowed(
        stance: Stance, entry_state: str, exit_state: str, unlocked: list[tuple[str, str]]
    ) -> bool:
        pair = (entry_state, exit_state)
        if pair in unlocked:
            return True
        sets = _PAIRING_SETS.get(id(stance))
        if sets is None:
            pairings = stance.surface.pairings
            sets = _PAIRING_SETS[id(stance)] = (
                stance,
                frozenset((p.entry, p.exit) for p in pairings.never),
                None if pairings.only is None else frozenset((p.entry, p.exit) for p in pairings.only),
            )
        _keep, never, only = sets
        if pair in never:
            return False
        if only is not None:
            return pair in only
        return True
"""

# --- P4: memoize the virtual left context (three frozen-dataclass constructions per call) ---

P4_OLD = """    def _virtual_left(self, rune_name: str, candidate: Candidate) -> LeftContext:
        cell = CellId(
"""
P4_NEW = """    def _virtual_left(self, rune_name: str, candidate: Candidate) -> LeftContext:
        key = (rune_name, candidate)
        hit = self._virtual_left_cache.get(key)
        if hit is not None:
            return hit
        hit = self._virtual_left_cache[key] = self._virtual_left_uncached(rune_name, candidate)
        return hit

    def _virtual_left_uncached(self, rune_name: str, candidate: Candidate) -> LeftContext:
        cell = CellId(
"""

# --- P5: RightToken as a NamedTuple (C-level construct/hash/eq) instead of a frozen dataclass ---

P5_OLD = """@dataclass(frozen=True)
class RightToken:
    kind: str  # "edge" | "space" | "zwnj" | "namer-dot" | "letter" | "unknown"
    rune: str | None = None
"""
P5_NEW = """class RightToken(NamedTuple):
    kind: str  # "edge" | "space" | "zwnj" | "namer-dot" | "letter" | "unknown"
    rune: str | None = None
"""

# --- P6: memoize `candidates` on the collapsed left key, replaying its fired pointers and eliminations ---

P6_OLD = """    def candidates(
        self,
        left: LeftContext,
        rune_name: str,
        right1: RightToken,
        right2: RightToken,
        eliminations: list[Elimination] | None = None,
    ) -> list[Candidate]:
        rune = self.spec.runes[rune_name]
"""
P6_NEW = """    def candidates(
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
        hit = self._candidates_cache.get(key)
        if hit is None:
            local: list[Elimination] = []
            self._begin_capture()
            try:
                out = self._candidates_uncached(left, rune_name, right1, right2, local)
            except BaseException:
                self._abort_capture()
                raise
            hit = self._candidates_cache[key] = (out, tuple(local), self._end_capture())
        else:
            self._replay_fired(hit[2])
        if eliminations is not None:
            eliminations.extend(hit[1])
        return hit[0]

    def _candidates_uncached(
        self,
        left: LeftContext,
        rune_name: str,
        right1: RightToken,
        right2: RightToken,
        eliminations: list[Elimination] | None = None,
    ) -> list[Candidate]:
        rune = self.spec.runes[rune_name]
"""

# --- P7: memoize `_prefer_favors` on the collapsed left key plus the record identity ---

P7_OLD = """    ) -> bool | None:
        \"\"\"Whether a prefer record speaks for this candidate."""
P7_NEW = """    ) -> bool | None:
        if self._fired_log is None:
            return self._prefer_favors_uncached(
                owner, record, rune_name, candidate, left, right1, right2, right3, right4
            )
        settled = left.settled
        key = (
            id(record),
            owner,
            rune_name,
            candidate,
            left.kind,
            settled.cell.rune if settled is not None else None,
            settled.cell.stance if settled is not None else None,
            settled.seam if settled is not None else None,
            right1,
            right2,
            right3,
            right4,
        )
        hit = self._prefer_cache.get(key)
        if hit is not None:
            self._replay_fired(hit[1])
            return hit[0]
        self._begin_capture()
        try:
            verdict = self._prefer_favors_uncached(
                owner, record, rune_name, candidate, left, right1, right2, right3, right4
            )
        except BaseException:
            self._abort_capture()
            raise
        self._prefer_cache[key] = (verdict, self._end_capture())
        self._prefer_keepalive.append(record)
        return verdict

    def _prefer_favors_uncached(
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
        \"\"\"Whether a prefer record speaks for this candidate."""

INIT_OLD = "        self._closure_cache: dict[tuple, bool] = {}\n"
INIT_NEW = (
    "        self._closure_cache: dict[tuple, bool] = {}\n"
    "        self._order_index_cache: dict[str, dict[str, int]] = {}\n"
    "        self._exit_sources_cache: dict[int, tuple] = {}\n"
    "        self._virtual_left_cache: dict[tuple, LeftContext] = {}\n"
    "        self._candidates_cache: dict[tuple, tuple] = {}\n"
    "        self._prefer_cache: dict[tuple, tuple] = {}\n"
    "        self._prefer_keepalive: list = []\n"
)


# --- P8: memoize the fixpoint's right2/right3/right4 option lists (rebuilt per worklist item today) ---

T8_OLD_R2 = """            if right1.kind == "letter":
                right2_options = [
                    r
                    for r in right_boundaries + right_letters
                    if not (
                        r.kind == "letter"
                        and (right1.letter, r.letter) in formation_pairs
                        and (right1.letter, r.letter) not in survivable
                    )
                ]
                if follower_map is not None:
                    right2_options = [
                        r for r in right2_options if r.kind == "letter" and r.letter in follower_map
                    ]
                if right2_allowed is not None:
                    right2_options = [r for r in right2_options if r in right2_allowed]
                if rune_name in liga_sequences:
                    right2_options = [r for r in right2_options if liga_formed_before(rune_name, right1, r)]
                if right1.letter in liga_sequences:
                    right2_options = [r for r in right2_options if liga_formed_before(right1.letter, r, None)]
            else:
                right2_options = [EDGE]
"""

T8_NEW_R2 = """            if right1.kind == "letter":
                _r2key = (rune_name, right1, right2_allowed)
                right2_options = _r2_options_cache.get(_r2key)
                if right2_options is None:
                    right2_options = [
                        r
                        for r in right_boundaries + right_letters
                        if not (
                            r.kind == "letter"
                            and (right1.letter, r.letter) in formation_pairs
                            and (right1.letter, r.letter) not in survivable
                        )
                    ]
                    if follower_map is not None:
                        right2_options = [
                            r for r in right2_options if r.kind == "letter" and r.letter in follower_map
                        ]
                    if right2_allowed is not None:
                        right2_options = [r for r in right2_options if r in right2_allowed]
                    if rune_name in liga_sequences:
                        right2_options = [
                            r for r in right2_options if liga_formed_before(rune_name, right1, r)
                        ]
                    if right1.letter in liga_sequences:
                        right2_options = [
                            r for r in right2_options if liga_formed_before(right1.letter, r, None)
                        ]
                    _r2_options_cache[_r2key] = right2_options
            else:
                right2_options = [EDGE]
"""

T8_OLD_SEED = """    while worklist:
        left, rune_name, right1_constraint, right2_allowed, right3_allowed = worklist.pop()
"""
T8_NEW_SEED = """    _r2_options_cache: dict[tuple, list] = {}
    _r3_options_cache: dict[tuple, list] = {}
    _r4_options_cache: dict[tuple, list] = {}
    while worklist:
        left, rune_name, right1_constraint, right2_allowed, right3_allowed = worklist.pop()
"""


def main() -> int:
    root = Path(sys.argv[1])
    levers = set(sys.argv[2:])
    path = root / "rebuild" / "pipeline" / "settle.py"
    text = path.read_text()

    text = sub(text, INIT_OLD, INIT_NEW, "engine-caches")
    text = sub(
        text,
        "from typing import Protocol",
        "from typing import NamedTuple, Protocol\n\n_PAIRING_SETS: dict[int, tuple] = {}",
        "imports",
    )

    if "p1" in levers:
        text = sub(text, P1_OLD, P1_NEW, "p1")
    if "p2" in levers:
        text = sub(text, P2_OLD, P2_NEW, "p2a")
        text = sub(text, P2_OLD2, P2_NEW2, "p2b")
    if "p3" in levers:
        text = sub(text, P3_OLD, P3_NEW, "p3")
    if "p4" in levers:
        text = sub(text, P4_OLD, P4_NEW, "p4")
    if "p5" in levers:
        text = sub(text, P5_OLD, P5_NEW, "p5")
    if "p6" in levers:
        text = sub(text, P6_OLD, P6_NEW, "p6")
    if "p7" in levers:
        text = sub(text, P7_OLD, P7_NEW, "p7")

    if "p8" in levers:
        tpath = root / "rebuild" / "pipeline" / "table.py"
        ttext = tpath.read_text()
        ttext = sub(ttext, T8_OLD_SEED, T8_NEW_SEED, "p8-seed")
        ttext = sub(ttext, T8_OLD_R2, T8_NEW_R2, "p8-r2")
        tpath.write_text(ttext)

    path.write_text(text)
    print(f"patched {path} with {sorted(levers) or ['none']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
