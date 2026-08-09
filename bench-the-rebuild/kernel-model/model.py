"""k1-meso: the M1 settlement fixpoint, modelled idiomatically as this repo writes it.

Structure mirrors rebuild/pipeline/settle.py + rebuild/pipeline/table.py:

  build_tables               <- table.build_tables (worklist fixpoint over (left state x rune) x r1 x r2 x r3? x r4?)
  third_matters/fourth_matters <- table.third_slot_filter / fourth_slot_filter and their `matters` closures
  ProspectLiveness           <- table._ProspectLiveness (third_live / fourth_live and their probe ladders)
  Engine.candidates          <- settle.Engine.candidates
  Engine.prospect            <- settle.Engine._prospect (recursive simulated follower transition, cached)
  Engine.prefer_favors       <- settle.Engine._prefer_favors (own branch + shifted-slot vote branch)
  Engine.transition_trace    <- settle.Engine.transition_trace (memo on a 10-tuple of str|None + 4 RightTokens)

Frozen dataclasses, interned strings, dict memo keyed on tuples, predicate ranking flow: the point of this file is
to pay the same per-call costs CPython pays in the real kernel, so the Rust and Go ports have an honest target.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

# --- token alphabet ---------------------------------------------------------
# 19 options per right slot: 15 letters + EDGE/SPACE/ZWNJ/NAMER_DOT. UNKNOWN is the beyond-window optimism token.
NONE_STATE = "none"
BOUNDARY_STANCE = "@boundary"
HEIGHT_NAMES = ("baseline", "x-height", "y6", "top")
HEIGHT_Y = (0, 5, 6, 8)
FEATURE_NAMES = ("ss03", "ss04", "ss05", "ss10")
SPLITTING_KINDS = frozenset({"edge", "space", "zwnj"})


@dataclass(frozen=True)
class RightToken:
    kind: str
    rune: str | None = None

    @property
    def letter(self) -> str:
        if self.rune is None:
            raise ValueError("non-letter token has no rune")
        return self.rune


EDGE = RightToken("edge")
SPACE = RightToken("space")
ZWNJ = RightToken("zwnj")
NAMER_DOT = RightToken("namer-dot")
UNKNOWN = RightToken("unknown")
BOUNDARIES = (EDGE, SPACE, ZWNJ, NAMER_DOT)
BOUNDARY_LABELS = {"edge": "edge", "space": "space", "zwnj": "zwnj", "namer-dot": "namer-dot"}


@dataclass(frozen=True)
class CellId:
    rune: str
    stance: str
    entry: str | None
    exit: str | None
    adjustments: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settled:
    cell: CellId
    seam: str | None
    extension: int


@dataclass(frozen=True)
class LeftContext:
    kind: str
    settled: Settled | None = None


@dataclass(frozen=True)
class Candidate:
    stance: str
    entry: str | None
    seam: str | None
    order_index: int
    exit_index: int = -1


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    join_count: int
    prospect: int


@dataclass(frozen=True)
class Elimination:
    stage: str
    description: str


@dataclass(frozen=True)
class TransitionTrace:
    settled: Settled
    joint_floor: bool
    prospect: int
    ranked: tuple[RankedCandidate, ...]
    eliminations: tuple[Elimination, ...]
    decided_stage: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Condition:
    family: frozenset[str] = frozenset()
    klass: tuple[str, ...] = ()
    stance: frozenset[str] = frozenset()
    joined_at: str | None = None
    stroke: str | None = None
    is_token: str | None = None
    except_: tuple["Condition", ...] = ()
    then: "Condition | None" = None


@dataclass(frozen=True)
class When:
    left: Condition | None = None
    right: Condition | None = None
    self_entry: str | None = None
    self_exit: str | None = None
    word: str | None = None
    feature: str | None = None


@dataclass(frozen=True)
class SurfaceRow:
    height: str
    selectable: bool
    scope: tuple[Condition, ...]
    prov: str = ""


@dataclass(frozen=True)
class Unlock:
    feature: str
    entry: str | None
    exit: str | None
    pairing: tuple[str, str] | None
    when: When | None
    prov: str = ""


@dataclass(frozen=True)
class Stance:
    name: str
    entries: tuple[SurfaceRow, ...]
    exits: tuple[SurfaceRow, ...]
    never: tuple[tuple[str, str], ...]
    only: tuple[tuple[str, str], ...] | None
    unlocks: tuple[Unlock, ...]
    require_entry: bool
    require_exit: bool


@dataclass(frozen=True)
class PolicyRecord:
    kind: str
    when: When
    stance: str | None
    entry: str | None
    exit: str | None
    has_entry: bool
    has_exit: bool
    cell: tuple[str | None, str | None] | None
    over: tuple[str | None, str | None] | None
    absolute: bool
    by: int
    ident: int
    weight: int
    prov: str


@dataclass(frozen=True)
class Rune:
    name: str
    sequence: tuple[str, str] | None
    stances: tuple[Stance, ...]
    order: tuple[str, ...]
    refuse: tuple[PolicyRecord, ...]
    prefer: tuple[PolicyRecord, ...]
    extend: tuple[PolicyRecord, ...]
    contract: tuple[PolicyRecord, ...]
    entry_strokes: frozenset[str]
    entry_bearing: bool
    feature_mask: int  # the features this rune can feel: its unlocks and its feature-gated policy records


@dataclass(frozen=True)
class Spec:
    n_letters: int
    letters: tuple[str, ...]
    runes: dict[str, Rune]
    order: tuple[str, ...]
    classes: dict[str, frozenset[str]]


# --- spec loading -----------------------------------------------------------


def _rune_name(i: int) -> str:
    return f"qs{i:02d}"


def _stance_name(i: int) -> str:
    return f"st{i}"


def _height(i: int) -> str | None:
    return HEIGHT_NAMES[i] if i >= 0 else None


def _state(i: int) -> str:
    return HEIGHT_NAMES[i] if i >= 0 else NONE_STATE


def load_spec(path: str, n_letters: int = 15) -> Spec:
    with open(path) as fh:
        text = fh.read()
    conds: dict[int, Condition] = {}
    whens: dict[int, When] = {}
    classes: dict[str, frozenset[str]] = {}
    liga: dict[str, tuple[str, str]] = {}
    n_stances: dict[str, int] = {}
    orders: dict[str, tuple[str, ...]] = {}
    strokes: dict[str, frozenset[str]] = {}
    stances: dict[str, list[Stance]] = {}
    records: dict[str, dict[str, list[PolicyRecord]]] = {}
    rune_order: list[str] = []
    total_runes = 0
    ident = 0

    def cond_of(i: int) -> Condition | None:
        return conds[i] if i >= 0 else None

    def fam(mask: int) -> frozenset[str]:
        return frozenset(_rune_name(b) for b in range(64) if (mask >> b) & 1 and b < n_letters)

    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        p = [int(v) for v in parts[1:]]
        i = 0

        def nx() -> int:
            nonlocal i
            v = p[i]
            i += 1
            return v

        if key == "header":
            total_runes = nx()
            nx()
        elif key == "class":
            idx = nx()
            classes[f"c{idx}"] = fam(nx())
        elif key == "rune":
            idx = nx()
            name = _rune_name(idx)
            isliga = nx()
            a, b = nx(), nx()
            n = nx()
            if isliga:
                liga[name] = (_rune_name(a), _rune_name(b))
            n_stances[name] = n
            rune_order.append(name)
        elif key == "order":
            name = _rune_name(nx())
            orders[name] = tuple(_stance_name(nx()) for _ in range(n_stances[name]))
        elif key == "strokes":
            name = _rune_name(nx())
            mask = nx()
            strokes[name] = frozenset(f"s{b}" for b in range(8) if (mask >> b) & 1)
        elif key == "cond":
            idx = nx()
            family = fam(nx())
            kl = tuple(f"c{nx()}" for _ in range(nx()))
            smask = nx()
            stance_set = frozenset(_stance_name(b) for b in range(8) if (smask >> b) & 1)
            ja, st, it = nx(), nx(), nx()
            ex = tuple(conds[nx()] for _ in range(nx()))
            th = nx()
            conds[idx] = Condition(
                family,
                kl,
                stance_set,
                (_state(ja) if ja != -2 else None),
                (f"s{st}" if st >= 0 else None),
                (("boundary", "space", "zwnj", "namer-dot")[it] if it >= 0 else None) if it < 4 else None,
                ex,
                cond_of(th),
            )
        elif key == "when":
            idx = nx()
            left, right = nx(), nx()
            se, sx, wd, ft = nx(), nx(), nx(), nx()
            whens[idx] = When(
                cond_of(left),
                cond_of(right),
                ("live" if se == 1 else NONE_STATE) if se >= 0 else None,
                ("live" if sx == 1 else NONE_STATE) if sx >= 0 else None,
                ("initial", "medial", "final", "isolated")[wd] if wd >= 0 else None,
                FEATURE_NAMES[ft] if ft >= 0 else None,
            )
        elif key == "stance":
            name = _rune_name(nx())
            sname = _stance_name(nx())
            req_e, req_x = nx(), nx()
            entries = []
            for _ in range(nx()):
                h = HEIGHT_NAMES[nx()]
                sel = nx()
                scope = tuple(conds[nx()] for _ in range(nx()))
                entries.append(SurfaceRow(h, bool(sel), scope, f"{name}.yaml:stances.{sname}.entries.{h}"))
            exits = []
            for _ in range(nx()):
                h = HEIGHT_NAMES[nx()]
                scope = tuple(conds[nx()] for _ in range(nx()))
                exits.append(SurfaceRow(h, True, scope, f"{name}.yaml:stances.{sname}.exits.{h}"))
            never = tuple((_state(nx()), _state(nx())) for _ in range(nx()))
            has_only = nx()
            only_rows = tuple((_state(nx()), _state(nx())) for _ in range(nx()))
            unlocks = []
            for _ in range(nx()):
                feat, en, ex_, hp, pe, px, w = (nx() for _ in range(7))
                unlocks.append(
                    Unlock(
                        FEATURE_NAMES[feat],
                        _height(en),
                        _height(ex_),
                        (_state(pe), _state(px)) if hp else None,
                        whens[w] if w >= 0 else None,
                        f"{name}.yaml:stances.{sname}.unlocks[{len(unlocks)}]",
                    )
                )
            stances.setdefault(name, []).append(
                Stance(
                    sname,
                    tuple(entries),
                    tuple(exits),
                    never,
                    only_rows if has_only else None,
                    tuple(unlocks),
                    bool(req_e),
                    bool(req_x),
                )
            )
        elif key == "record":
            name = _rune_name(nx())
            kind = ("refuse", "prefer", "extend", "contract")[nx()]
            w = whens[nx()]
            s = nx()
            entry_raw, exit_raw = nx(), nx()
            hc = nx()
            ce, cx = nx(), nx()
            ho = nx()
            oe, ox = nx(), nx()
            absolute = nx()
            by = nx()
            weight = 0
            if w.left is not None:
                weight += 2 + len(w.left.family)
            if w.right is not None:
                weight += 2 + len(w.right.family)
            for flag in (w.self_entry, w.self_exit, w.word, w.feature):
                if flag is not None:
                    weight += 1
            if s >= 0:
                weight += 1
            if hc:
                weight += 1
            records.setdefault(name, {"refuse": [], "prefer": [], "extend": [], "contract": []})
            records[name][kind].append(
                PolicyRecord(
                    kind,
                    w,
                    _stance_name(s) if s >= 0 else None,
                    _state(entry_raw) if entry_raw != -2 else None,
                    _state(exit_raw) if exit_raw != -2 else None,
                    entry_raw != -2,
                    exit_raw != -2,
                    (_state(ce), _state(cx)) if hc else None,
                    (_state(oe), _state(ox)) if ho else None,
                    bool(absolute),
                    by,
                    ident,
                    weight,
                    f"{name}.yaml:policy.{kind}[{len(records[name][kind])}]",
                )
            )
            ident += 1

    letters = tuple(_rune_name(i) for i in range(n_letters))
    keep = set(letters)
    for name, seq in liga.items():
        if seq[0] in keep and seq[1] in keep:
            keep.add(name)
    ordered = tuple(n for n in rune_order if n in keep)

    runes: dict[str, Rune] = {}
    for name in ordered:
        buckets = records.get(name, {"refuse": [], "prefer": [], "extend": [], "contract": []})
        st = tuple(stances[name])
        bearing = any(
            any(row.selectable for row in s.entries) or any(u.entry is not None for u in s.unlocks) for s in st
        )
        fmask = 0
        for stance_rec in st:
            for u in stance_rec.unlocks:
                fmask |= 1 << FEATURE_NAMES.index(u.feature)
        for pool_name in ("refuse", "prefer", "extend", "contract"):
            for rec in buckets[pool_name]:
                if rec.when.feature is not None:
                    fmask |= 1 << FEATURE_NAMES.index(rec.when.feature)
        runes[name] = Rune(
            name,
            liga.get(name),
            st,
            orders[name],
            tuple(buckets["refuse"]),
            tuple(buckets["prefer"]),
            tuple(buckets["extend"]),
            tuple(buckets["contract"]),
            strokes[name],
            bearing,
            fmask,
        )
    assert total_runes >= len(runes)
    return Spec(n_letters, letters, runes, ordered, classes)


# --- errors -----------------------------------------------------------------


class SettleError(Exception):
    pass


class EStrandedError(SettleError):
    pass


class ERaisedError(Exception):
    pass


# --- engine -----------------------------------------------------------------


class Engine:
    __slots__ = (
        "spec",
        "features",
        "trace_cache",
        "closure_cache",
        "prospect_cache",
        "share",
        "share_delta",
        "fired",
        "_fired_log",
        "_capture_starts",
        "_pointer_intern",
        "closure_fired",
        "prospect_fired",
        "trace_fired",
        "n_candidates",
        "n_prospect",
        "n_trace",
        "n_favors",
    )

    def __init__(
        self, spec: Spec, features: frozenset[str], share: dict | None = None, share_delta: int = 0
    ) -> None:
        self.spec = spec
        self.features = features
        self.trace_cache: dict[tuple, TransitionTrace] = {}
        self.closure_cache: dict[tuple, bool] = {}
        self.prospect_cache: dict[tuple, int] = {}
        self.share = share
        self.share_delta = share_delta
        self.fired: set[str] = set()
        self._fired_log: list[str] = []
        self._capture_starts: list[int] = []
        self._pointer_intern: dict[str, str] = {}
        self.closure_fired: dict[tuple, tuple[str, ...]] = {}
        self.prospect_fired: dict[tuple, tuple[str, ...]] = {}
        self.trace_fired: dict[tuple, tuple[str, ...]] = {}
        self.n_candidates = 0
        self.n_prospect = 0
        self.n_trace = 0
        self.n_favors = 0

    def _record_fired(self, provenance: str) -> None:
        pointer = self._pointer_intern.setdefault(provenance, provenance)
        if self._capture_starts:
            self._fired_log.append(pointer)
        self.fired.add(pointer)

    def _replay_fired(self, delta: tuple[str, ...]) -> None:
        if delta:
            self.fired.update(delta)
            if self._capture_starts:
                self._fired_log.extend(delta)

    def _begin_capture(self) -> None:
        self._capture_starts.append(len(self._fired_log))

    def _end_capture(self) -> tuple[str, ...]:
        start = self._capture_starts.pop()
        delta = tuple(dict.fromkeys(self._fired_log[start:]))
        if not self._capture_starts:
            del self._fired_log[:]
        return delta

    def _abort_capture(self) -> None:
        self._capture_starts.pop()
        if not self._capture_starts:
            del self._fired_log[:]

    # --- condition matching -------------------------------------------------

    def _left_exit_stroke(self, left: LeftContext) -> str | None:
        if left.kind != "letter" or left.settled is None:
            return None
        strokes = self.spec.runes[left.settled.cell.rune].entry_strokes
        return min(strokes) if strokes else None

    def cond_matches_left(self, cond: Condition, left: LeftContext, seam: str | None) -> bool:
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
                if cell.rune not in self.spec.classes[klass]:
                    return False
            if cond.stance and cell.stance not in cond.stance:
                return False
            if cond.joined_at is not None:
                state = seam if seam is not None else NONE_STATE
                if cond.joined_at != state:
                    return False
            if cond.stroke is not None and self._left_exit_stroke(left) != cond.stroke:
                return False
        for ex in cond.except_:
            if self.cond_matches_left(ex, left, seam):
                return False
        return True

    def cond_matches_right(self, cond: Condition, tokens: tuple[RightToken, ...]) -> bool | None:
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
        needs_letter = bool(cond.family or cond.klass or cond.stroke is not None)
        if needs_letter:
            if token.kind == "unknown":
                unknown = True
            elif token.kind != "letter":
                return False
            else:
                name = token.rune
                if cond.family and name not in cond.family:
                    return False
                for klass in cond.klass:
                    if name not in self.spec.classes[klass]:
                        return False
                if cond.stroke is not None and cond.stroke not in self.spec.runes[name].entry_strokes:
                    return False
        for ex in cond.except_:
            sub = self.cond_matches_right(ex, tokens)
            if sub is True:
                return False
            if sub is None:
                unknown = True
        if cond.then is not None:
            sub = self.cond_matches_right(cond.then, tail)
            if sub is False:
                return False
            if sub is None:
                unknown = True
        return None if unknown else True

    def when_matches(
        self,
        when: When,
        left: LeftContext,
        entry: str | None,
        seam: str | None,
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
        if when.left is not None and not self.cond_matches_left(when.left, left, entry):
            return False
        if when.right is not None:
            verdict = self.cond_matches_right(when.right, (right1, right2, right3, right4))
            if verdict is False:
                return False
            if verdict is None:
                unknown = True
        return None if unknown else True

    # --- capability ---------------------------------------------------------

    def _entry_available(
        self,
        rune: Rune,
        stance: Stance,
        height: str,
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> bool:
        for row in stance.entries:
            if row.height != height:
                continue
            if not row.selectable:
                break
            if not row.scope:
                return True
            for cond in row.scope:
                if self.cond_matches_left(cond, left, height):
                    self._record_fired(row.prov)
                    return True
            break
        for unlock in stance.unlocks:
            if unlock.entry != height or unlock.feature not in self.features:
                continue
            if unlock.when is None:
                self._record_fired(unlock.prov)
                return True
            if self.when_matches(unlock.when, left, height, None, right1, right2) is not False:
                self._record_fired(unlock.prov)
                return True
        return False

    def _exit_sources(self, stance: Stance) -> list[tuple[str, SurfaceRow | None, int]]:
        sources: list[tuple[str, SurfaceRow | None, int]] = []
        for index, row in enumerate(stance.exits):
            sources.append((row.height, row, index))
        offset = len(sources)
        for unlock in stance.unlocks:
            if unlock.exit is not None and unlock.feature in self.features:
                if all(row.height != unlock.exit for row in stance.exits):
                    self._record_fired(unlock.prov)
                    sources.append((unlock.exit, None, offset))
                    offset += 1
        return sources

    def _active_pairing_unlocks(
        self, stance: Stance, left: LeftContext, entry: str | None, right1: RightToken, right2: RightToken
    ) -> list[tuple[str, str]]:
        active: list[tuple[str, str]] = []
        for unlock in stance.unlocks:
            if unlock.pairing is None or unlock.feature not in self.features:
                continue
            if unlock.when is not None:
                if self.when_matches(unlock.when, left, entry, None, right1, right2) is False:
                    continue
            self._record_fired(unlock.prov)
            active.append(unlock.pairing)
        return active

    @staticmethod
    def _pairing_allowed(stance: Stance, entry_state: str, exit_state: str, unlocked) -> bool:
        pair = (entry_state, exit_state)
        if pair in unlocked:
            return True
        if pair in stance.never:
            return False
        if stance.only is not None:
            return pair in stance.only
        return True

    def _refusal_hit(
        self, rune: Rune, candidate: Candidate, left: LeftContext, right1: RightToken, right2: RightToken
    ) -> PolicyRecord | None:
        for record in rune.refuse:
            if record.stance is not None and record.stance != candidate.stance:
                continue
            if record.has_entry and record.entry != (
                candidate.entry if candidate.entry is not None else NONE_STATE
            ):
                continue
            if record.has_exit and record.exit != (
                candidate.seam if candidate.seam is not None else NONE_STATE
            ):
                continue
            if (
                record.stance is None
                and not record.has_entry
                and not record.has_exit
                and candidate.seam is None
            ):
                continue
            if self.when_matches(record.when, left, candidate.entry, candidate.seam, right1, right2) is True:
                self._record_fired(record.prov)
                return record
        return None

    # --- candidate enumeration ---------------------------------------------

    def candidates(
        self,
        left: LeftContext,
        rune_name: str,
        right1: RightToken,
        right2: RightToken,
        eliminations: list[Elimination] | None = None,
    ) -> list[Candidate]:
        self.n_candidates += 1
        rune = self.spec.runes[rune_name]
        committed = left.settled.seam if (left.kind == "letter" and left.settled is not None) else None
        out: list[Candidate] = []
        order = list(rune.order)
        for stance in rune.stances:
            if stance.name not in order:
                order.append(stance.name)
        right1_is_letter = right1.kind == "letter"
        for stance in rune.stances:
            order_index = order.index(stance.name)
            entry: str | None = None
            if committed is not None:
                if not self._entry_available(rune, stance, committed, left, right1, right2):
                    if eliminations is not None:
                        eliminations.append(
                            Elimination(
                                "entry-binding",
                                f"{rune_name}.{stance.name}: no available entry row at {committed} against the committed seam",
                            )
                        )
                    continue
                entry = committed
            if stance.require_entry and entry is None:
                if eliminations is not None:
                    eliminations.append(
                        Elimination("require", f"{rune_name}.{stance.name}: requires a live entry")
                    )
                continue
            unlocked = self._active_pairing_unlocks(stance, left, entry, right1, right2)
            entry_state = entry if entry is not None else NONE_STATE
            if right1_is_letter:
                for height, row, exit_index in self._exit_sources(stance):
                    candidate = Candidate(stance.name, entry, height, order_index, exit_index)
                    if not self._pairing_allowed(stance, entry_state, height, unlocked):
                        if eliminations is not None:
                            eliminations.append(
                                Elimination(
                                    "pairings",
                                    f"{rune_name}.{stance.name}: pairing ({entry_state}, {height}) not allowed",
                                )
                            )
                        continue
                    if row is not None and row.scope:
                        scoped = False
                        for cond in row.scope:
                            verdict = self.cond_matches_right(cond, (right1, right2, UNKNOWN, UNKNOWN))
                            if verdict is True:
                                self._record_fired(row.prov)
                            if verdict is not False:
                                scoped = True
                                break
                        if not scoped:
                            if eliminations is not None:
                                eliminations.append(
                                    Elimination(
                                        "row-scope",
                                        f"{rune_name}.{stance.name}: exit {height} toward-scope does not admit {right1.rune}",
                                    )
                                )
                            continue
                    if not self._acceptor_exists(candidate, rune_name, right1, right2):
                        if eliminations is not None:
                            eliminations.append(
                                Elimination(
                                    "lookahead-closure",
                                    f"{rune_name}.{stance.name}: exit {height} has no refusal-aware acceptor cell on {right1.rune}",
                                )
                            )
                        continue
                    hit = self._refusal_hit(rune, candidate, left, right1, right2)
                    if hit is not None:
                        if eliminations is not None:
                            eliminations.append(
                                Elimination(
                                    "refuse", f"{rune_name}.{stance.name}: exit {height} refused by #{hit.ident}"
                                )
                            )
                        continue
                    out.append(candidate)
            if stance.require_exit:
                continue
            non_joining = Candidate(stance.name, entry, None, order_index)
            if not self._pairing_allowed(stance, entry_state, NONE_STATE, unlocked):
                if eliminations is not None:
                    eliminations.append(
                        Elimination(
                            "pairings", f"{rune_name}.{stance.name}: pairing ({entry_state}, none) not allowed"
                        )
                    )
                continue
            hit = self._refusal_hit(rune, non_joining, left, right1, right2)
            if hit is not None:
                if eliminations is not None:
                    eliminations.append(
                        Elimination("refuse", f"{rune_name}.{stance.name}: non-joining cell refused")
                    )
                continue
            out.append(non_joining)
        return out

    def _virtual_left(self, rune_name: str, candidate: Candidate) -> LeftContext:
        cell = CellId(rune_name, candidate.stance, candidate.entry, candidate.seam, ())
        return LeftContext("letter", Settled(cell, candidate.seam, 0))

    def _acceptor_exists(
        self, candidate: Candidate, rune_name: str, right1: RightToken, right2: RightToken
    ) -> bool:
        if right1.kind != "letter":
            return False
        key = (rune_name, candidate.stance, candidate.entry, candidate.seam, right1.rune, right2)
        cached = self.closure_cache.get(key)
        if cached is not None:
            self._replay_fired(self.closure_fired.get(key, ()))
            return cached
        self._begin_capture()
        try:
            result = bool(
                self.candidates(self._virtual_left(rune_name, candidate), right1.letter, right2, UNKNOWN)
            )
        except BaseException:
            self._abort_capture()
            raise
        self.closure_fired[key] = self._end_capture()
        self.closure_cache[key] = result
        return result

    # --- prospect -----------------------------------------------------------

    def prospect(
        self,
        rune_name: str,
        candidate: Candidate,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken = UNKNOWN,
        right4: RightToken = UNKNOWN,
    ) -> int:
        self.n_prospect += 1
        if right1.kind != "letter" or right2.kind != "letter":
            return 0
        key = (
            rune_name,
            candidate.stance,
            candidate.entry,
            candidate.seam,
            right1.rune,
            right2,
            right3,
            right4,
        )
        cached = self.prospect_cache.get(key)
        if cached is not None:
            self._replay_fired(self.prospect_fired.get(key, ()))
            return cached
        self._begin_capture()
        try:
            virtual = self._virtual_left(rune_name, candidate)
            try:
                trace = self.transition_trace(virtual, right1, right2, right3, right4, UNKNOWN)
                result = 1 if trace.settled.seam is not None else 0
            except (SettleError, ERaisedError):
                follower_cells = self.candidates(virtual, right1.letter, right2, UNKNOWN)
                result = 1 if any(cell.seam is not None for cell in follower_cells) else 0
        except BaseException:
            self._abort_capture()
            raise
        self.prospect_fired[key] = self._end_capture()
        self.prospect_cache[key] = result
        return result

    # --- prefers ------------------------------------------------------------

    @staticmethod
    def _cell_pattern_matches(pattern, candidate: Candidate) -> bool:
        entry_state = candidate.entry if candidate.entry is not None else NONE_STATE
        exit_state = candidate.seam if candidate.seam is not None else NONE_STATE
        if pattern[0] is not None and pattern[0] != entry_state:
            return False
        if pattern[1] is not None and pattern[1] != exit_state:
            return False
        return True

    def prefer_favors(
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
        self.n_favors += 1
        if owner == rune_name:
            verdict = self.when_matches(
                record.when, left, candidate.entry, candidate.seam, right1, right2, right3, right4
            )
            if verdict is False:
                return None
            if record.cell is not None:
                favored = self._cell_pattern_matches(record.cell, candidate)
                if (
                    record.over is not None
                    and not favored
                    and not self._cell_pattern_matches(record.over, candidate)
                ):
                    return None
                return favored
            if record.stance is not None:
                return candidate.stance == record.stance
            return None
        if right1.kind != "letter" or right1.rune != owner:
            return None
        virtual = self._virtual_left(rune_name, candidate)
        vote_right2, vote_right3 = right3, right4
        follower_cells = self.candidates(virtual, owner, right2, vote_right2)
        relevant = False
        for cell in follower_cells:
            verdict = self.when_matches(
                record.when, virtual, cell.entry, cell.seam, right2, vote_right2, vote_right3, UNKNOWN
            )
            if verdict is False:
                continue
            relevant = True
            if record.stance is not None and cell.stance == record.stance:
                return True
            if record.cell is not None and self._cell_pattern_matches(record.cell, cell):
                return True
        return False if relevant else None

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
    ) -> list[Candidate]:
        if len(survivors) <= 1:
            return survivors
        gathered: list[tuple[str, PolicyRecord]] = []
        owners = [rune_name]
        if right1.kind == "letter" and right1.rune != rune_name and right1.rune in self.spec.runes:
            owners.append(right1.letter)
        for owner in owners:
            for record in self.spec.runes[owner].prefer:
                if record.absolute != mode_absolute:
                    continue
                gathered.append((owner, record))
        if not gathered:
            return survivors
        applicable: list[tuple[str, PolicyRecord, frozenset[Candidate]]] = []
        for owner, record in gathered:
            favored = set()
            relevant = False
            for candidate in survivors:
                vote = self.prefer_favors(
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
                if i != j and _outranks(applicable[j][1], applicable[i][1], applicable[j][0], applicable[i][0])
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
                self._record_fired(record.prov)
                continue
            for prev_owner, prev_record in applied:
                if _outranks(prev_record, record, prev_owner, owner) or _outranks(
                    record, prev_record, owner, prev_owner
                ):
                    continue
                raise ERaisedError("E-INCOMPARABLE")
        return current

    # --- the memoized kernel ------------------------------------------------

    def transition_trace(
        self,
        left: LeftContext,
        token: RightToken,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken = UNKNOWN,
        right4: RightToken = UNKNOWN,
    ) -> TransitionTrace:
        self.n_trace += 1
        if token.kind != "letter":
            return TransitionTrace(
                Settled(CellId(token.kind, BOUNDARY_STANCE, None, None, ()), None, 0),
                False,
                0,
                (),
                (),
                "boundary",
                (),
            )
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
        trace = self.trace_cache.get(key)
        if trace is not None:
            self._replay_fired(self.trace_fired.get(key, ()))
            return trace
        share = self.share
        if share is not None and self._share_blind(left, token, right1, right2, right3, right4):
            entry = share.get(key)
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
        self.trace_fired[key] = self._end_capture()
        self.trace_cache[key] = trace
        return trace

    def _share_blind(self, left, token, right1, right2, right3, right4) -> bool:
        """The TraceShare rule (rebuild/pipeline/trace_memo.TraceShare.reader_for): a donor trace may be served
        only when no rune named in the key can feel this configuration's feature delta."""
        delta = self.share_delta
        runes = self.spec.runes
        if left.kind == "letter" and left.settled is not None:
            if runes[left.settled.cell.rune].feature_mask & delta:
                return False
        if runes[token.rune].feature_mask & delta:
            return False
        for tok in (right1, right2, right3, right4):
            if tok.kind == "letter" and runes[tok.rune].feature_mask & delta:
                return False
        return True

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
        rune = self.spec.runes[rune_name]
        committed = left.settled.seam if (left.kind == "letter" and left.settled is not None) else None
        locked = left.kind == "zwnj" and rune.entry_bearing
        notes: list[str] = []
        eliminations: list[Elimination] = []

        survivors = self.candidates(left, rune_name, right1, right2, eliminations)
        for elimination in eliminations:
            if elimination.stage == "refuse":
                pointer = elimination.description
                if pointer not in notes:
                    notes.append(pointer)
        if not survivors:
            if committed is not None:
                raise EStrandedError(
                    f"E-STRANDED: {cell_label(left.settled.cell)} committed an exit at {committed} but {rune_name} has no acceptor cell"
                )
            raise SettleError(f"{rune_name} has no candidate cells at all in this window")

        ranked = {
            candidate: RankedCandidate(
                candidate,
                self._score(rune_name, candidate, committed, right1, right2, right3, right4),
                self.prospect(rune_name, candidate, right1, right2, right3, right4),
            )
            for candidate in survivors
        }
        decided_stage = "only-candidate"
        n_ranked = len(ranked)

        survivors = self._apply_prefers(True, rune_name, survivors, left, right1, right2, right3, right4)
        if len(survivors) == 1 and decided_stage == "only-candidate" and n_ranked > 1:
            decided_stage = "absolute-prefer"

        if len(survivors) > 1:
            best = max(ranked[c].join_count for c in survivors)
            narrowed = [c for c in survivors if ranked[c].join_count == best]
            if len(narrowed) < len(survivors) and len(narrowed) == 1:
                decided_stage = "join-count"
            survivors = narrowed

        if len(survivors) > 1:
            survivors = self._apply_prefers(False, rune_name, survivors, left, right1, right2, right3, right4)
            if len(survivors) == 1:
                decided_stage = "yielding-prefer"

        if len(survivors) > 1:
            best_order = min(c.order_index for c in survivors)
            narrowed = [c for c in survivors if c.order_index == best_order]
            if len(narrowed) == 1:
                decided_stage = "order"
            survivors = narrowed

        joint_floor = False
        if len(survivors) > 1:
            ordered = sorted(survivors, key=_floor_key)
            decided_stage = "floor"
            joint_floor = (ordered[0].seam is None) != (ordered[1].seam is None)
            survivors = [ordered[0]]

        winner = survivors[0]
        settled = self._commit(rune, winner, locked, left, right1, right2, notes)
        return TransitionTrace(
            settled,
            joint_floor,
            ranked[winner].prospect,
            tuple(
                sorted(
                    ranked.values(),
                    key=lambda r: (-r.join_count, r.candidate.order_index, r.candidate.exit_index),
                )
            ),
            tuple(eliminations),
            decided_stage,
            tuple(notes),
        )

    def _score(
        self,
        rune_name: str,
        candidate: Candidate,
        committed: str | None,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken,
        right4: RightToken,
    ) -> int:
        left_term = 1 if committed is not None else 0
        own_term = 1 if candidate.seam is not None else 0
        return left_term + own_term + self.prospect(rune_name, candidate, right1, right2, right3, right4)

    def _pick_adjustment(
        self,
        kind: str,
        rune: Rune,
        winner: Candidate,
        side: str,
        height: str,
        left: LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> PolicyRecord | None:
        pool = rune.extend if kind == "extend" else rune.contract
        best: PolicyRecord | None = None
        for record in pool:
            if record.stance is not None and record.stance != winner.stance:
                continue
            if side == "entry":
                if not record.has_entry or record.entry != height:
                    continue
            else:
                if not record.has_exit or record.exit != height:
                    continue
            if self.when_matches(record.when, left, winner.entry, winner.seam, right1, right2) is not True:
                continue
            if best is None or record.ident < best.ident:
                best = record
        return best

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
        adjustments: list[str] = []
        if locked:
            adjustments.append("locked")
        if winner.entry is not None:
            for stance in rune.stances:
                if stance.name == winner.stance:
                    if self._entry_available(rune, stance, winner.entry, left, right1, right2):
                        note = f"entry live at {winner.entry}"
                        if note not in notes:
                            notes.append(note)
                    break
            extend = self._pick_adjustment("extend", rune, winner, "entry", winner.entry, left, right1, right2)
            contract = self._pick_adjustment(
                "contract", rune, winner, "entry", winner.entry, left, right1, right2
            )
            if extend is not None and left.settled is not None and left.settled.extension > 0:
                extend = None
            if extend is not None:
                self._record_fired(extend.prov)
                adjustments.append(f"en-ext-{extend.by}")
            if contract is not None:
                self._record_fired(contract.prov)
                adjustments.append(f"en-con-{contract.by}")
        extension = 0
        if winner.seam is not None:
            extend = self._pick_adjustment("extend", rune, winner, "exit", winner.seam, left, right1, right2)
            contract = self._pick_adjustment(
                "contract", rune, winner, "exit", winner.seam, left, right1, right2
            )
            if extend is not None:
                self._record_fired(extend.prov)
                extension += extend.by
                adjustments.append(f"ex-ext-{extend.by}")
            if contract is not None:
                self._record_fired(contract.prov)
                extension -= contract.by
                adjustments.append(f"ex-con-{contract.by}")
        cell = CellId(rune.name, winner.stance, winner.entry, winner.seam, tuple(adjustments))
        return Settled(cell, winner.seam, extension)


_HEIGHT_INDEX = {name: i for i, name in enumerate(HEIGHT_NAMES)}


def _floor_key(candidate: Candidate) -> tuple:
    seam_y = HEIGHT_Y[_HEIGHT_INDEX[candidate.seam]] if candidate.seam is not None else 10**6
    return (0 if candidate.seam is not None else 1, seam_y, candidate.exit_index)


def _outranks(a: PolicyRecord, b: PolicyRecord, a_owner: str, b_owner: str) -> bool:
    """Stand-in for specificity.outranks: the more constrained record wins, owner name breaks ties."""
    if a.weight != b.weight:
        return a.weight > b.weight
    return a_owner < b_owner


def word_position(left_kind: str, right1_kind: str) -> str | None:
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


def cell_label(cell: CellId) -> str:
    if cell.stance == BOUNDARY_STANCE:
        return cell.rune
    parts = [cell.rune, cell.stance]
    if cell.entry is not None:
        parts.append(f"en-y{HEIGHT_Y[_HEIGHT_INDEX[cell.entry]]}")
    if cell.exit is not None:
        parts.append(f"ex-y{HEIGHT_Y[_HEIGHT_INDEX[cell.exit]]}")
    parts.extend(cell.adjustments)
    return ".".join(parts)


# --- deep-slot liveness -----------------------------------------------------

SEAT_RAISED = "@raised"
SEAT_UNREACHABLE = "@unreachable"


class ProspectLiveness:
    __slots__ = ("spec", "engine", "third", "fourth", "sigs", "shapes", "left_classes", "left_conds", "probes")

    def __init__(self, spec: Spec, engine: Engine) -> None:
        self.spec = spec
        self.engine = engine
        self.third: dict[tuple, bool] = {}
        self.fourth: dict[tuple, bool] = {}
        self.sigs: dict[tuple, tuple] = {}
        self.shapes: dict[str, tuple] = {}
        self.left_classes: dict[str, tuple] = {}
        self.left_conds: dict[str, tuple] = {}
        self.probes = tuple(RightToken("letter", name) for name in spec.letters)

    def _input_shapes(self, family: str) -> tuple:
        cached = self.shapes.get(family)
        if cached is not None:
            return cached
        out = []
        for stance in self.spec.runes[family].stances:
            seams: list[str | None] = [None]
            for row in stance.exits:
                seams.append(row.height)
            for unlock in stance.unlocks:
                if unlock.exit is not None:
                    seams.append(unlock.exit)
            for seam in dict.fromkeys(seams):
                out.append((stance.name, seam))
        result = tuple(out)
        self.shapes[family] = result
        return result

    def _left_conditions(self, follower: str) -> tuple:
        cached = self.left_conds.get(follower)
        if cached is not None:
            return cached
        out: list[Condition] = []
        rune = self.spec.runes[follower]
        for stance in rune.stances:
            for row in stance.entries:
                out.extend(row.scope)
        for pool in (rune.refuse, rune.prefer):
            for record in pool:
                if record.when.left is not None:
                    out.append(record.when.left)
        result = tuple(out)
        self.left_conds[follower] = result
        return result

    def _virtual(self, family: str, stance: str, seam: str | None) -> LeftContext:
        return LeftContext("letter", Settled(CellId(family, stance, None, seam, ()), seam, 0))

    def _signature(self, follower: str, family: str, stance: str, seam: str | None) -> tuple:
        key = (follower, family, stance, seam)
        sig = self.sigs.get(key)
        if sig is None:
            virtual = self._virtual(family, stance, seam)
            sig = (
                seam,
                tuple(
                    self.engine.cond_matches_left(cond, virtual, seam)
                    for cond in self._left_conditions(follower)
                ),
            )
            self.sigs[key] = sig
        return sig

    def third_live(self, family: str, right1: str, right2: str) -> bool:
        r1tok, r2tok = RightToken("letter", right1), RightToken("letter", right2)
        stage_one = self._prospect_varies_third(
            family, right1, right2, r1tok, r2tok
        ) or self._vote_varies_third(family, right1, right2, r1tok, r2tok)
        if stage_one:
            key = ("seat3", family, right1, right2)
            verdict = self.third.get(key)
            if verdict is None:
                verdict = self._seat_varies(family, r1tok, r2tok, None)
                self.third[key] = verdict
            if verdict:
                return True
        key = ("joint34", family, right1, right2)
        verdict = self.third.get(key)
        if verdict is None:
            verdict = any(self.fourth_live(family, right1, right2, token.letter) for token in self.probes)
            self.third[key] = verdict
        return verdict

    def _prospect_varies_third(self, family, right1, right2, r1tok, r2tok) -> bool:
        for stance, seam in self._input_shapes(family):
            key = (right1, right2, self._signature(right1, family, stance, seam))
            verdict = self.third.get(key)
            if verdict is None:
                verdict = self._third_class_live(family, stance, seam, r1tok, r2tok)
                self.third[key] = verdict
            if verdict:
                return True
        return False

    def _third_class_live(self, family, stance, seam, r1tok, r2tok) -> bool:
        candidate = Candidate(stance, None, seam, 0)
        baseline = self.engine.prospect(family, candidate, r1tok, r2tok, EDGE, EDGE)
        for token in self.probes:
            edge4 = self.engine.prospect(family, candidate, r1tok, r2tok, token, EDGE)
            if edge4 != baseline:
                return True
            if self.engine.prospect(family, candidate, r1tok, r2tok, token, UNKNOWN) != edge4:
                return True
        return False

    def fourth_live(self, family: str, right1: str, right2: str, right3: str) -> bool:
        r1tok = RightToken("letter", right1)
        r2tok = RightToken("letter", right2)
        r3tok = RightToken("letter", right3)
        stage_one = self._prospect_varies_fourth(
            family, right1, right2, right3, r1tok, r2tok, r3tok
        ) or self._vote_varies_fourth(family, right1, right2, right3, r1tok, r2tok, r3tok)
        if not stage_one:
            return False
        key = ("seat4", family, right1, right2, right3)
        verdict = self.fourth.get(key)
        if verdict is None:
            verdict = self._seat_varies(family, r1tok, r2tok, r3tok)
            self.fourth[key] = verdict
        return verdict

    def _prospect_varies_fourth(self, family, right1, right2, right3, r1tok, r2tok, r3tok) -> bool:
        for stance, seam in self._input_shapes(family):
            key = (right1, right2, right3, self._signature(right1, family, stance, seam))
            verdict = self.fourth.get(key)
            if verdict is None:
                verdict = self._fourth_class_live(family, stance, seam, r1tok, r2tok, r3tok)
                self.fourth[key] = verdict
            if verdict:
                return True
        return False

    def _fourth_class_live(self, family, stance, seam, r1tok, r2tok, r3tok) -> bool:
        candidate = Candidate(stance, None, seam, 0)
        baseline = self.engine.prospect(family, candidate, r1tok, r2tok, r3tok, EDGE)
        for token in self.probes:
            if self.engine.prospect(family, candidate, r1tok, r2tok, r3tok, token) != baseline:
                return True
        return False

    def _vote_varies_third(self, family, right1, right2, r1tok, r2tok) -> bool:
        if right1 == family or not self.spec.runes[right1].prefer:
            return False
        for stance, seam in self._input_shapes(family):
            key = ("vote3", right1, right2, self._signature(right1, family, stance, seam))
            verdict = self.third.get(key)
            if verdict is None:
                verdict = self._vote_class_live(family, stance, seam, r1tok, r2tok, None)
                self.third[key] = verdict
            if verdict:
                return True
        return False

    def _vote_varies_fourth(self, family, right1, right2, right3, r1tok, r2tok, r3tok) -> bool:
        if right1 == family or not self.spec.runes[right1].prefer:
            return False
        for stance, seam in self._input_shapes(family):
            key = ("vote4", right1, right2, right3, self._signature(right1, family, stance, seam))
            verdict = self.fourth.get(key)
            if verdict is None:
                verdict = self._vote_class_live(family, stance, seam, r1tok, r2tok, r3tok)
                self.fourth[key] = verdict
            if verdict:
                return True
        return False

    def _vote_class_live(self, family, stance, seam, r1tok, r2tok, r3tok) -> bool:
        candidate = Candidate(stance, None, seam, 0)
        owner = r1tok.letter
        edge_left = LeftContext("edge")
        engine = self.engine
        for record in self.spec.runes[owner].prefer:
            if r3tok is None:
                baseline = engine.prefer_favors(
                    owner, record, family, candidate, edge_left, r1tok, r2tok, EDGE, EDGE
                )
                for token in self.probes:
                    edge4 = engine.prefer_favors(
                        owner, record, family, candidate, edge_left, r1tok, r2tok, token, EDGE
                    )
                    if edge4 != baseline:
                        return True
                    if (
                        engine.prefer_favors(
                            owner, record, family, candidate, edge_left, r1tok, r2tok, token, UNKNOWN
                        )
                        != edge4
                    ):
                        return True
            else:
                baseline = engine.prefer_favors(
                    owner, record, family, candidate, edge_left, r1tok, r2tok, r3tok, EDGE
                )
                for token in self.probes:
                    if (
                        engine.prefer_favors(
                            owner, record, family, candidate, edge_left, r1tok, r2tok, r3tok, token
                        )
                        != baseline
                    ):
                        return True
        return False

    def _seat_left_classes(self, family: str) -> tuple:
        reps = self.left_classes.get(family)
        if reps is not None:
            return reps
        out = [LeftContext("edge"), LeftContext("space"), LeftContext("zwnj"), LeftContext("namer-dot")]
        seen: set[tuple] = set()
        for left_family in self.spec.order:
            for stance, seam in self._input_shapes(left_family):
                sig = self._signature(family, left_family, stance, seam)
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(self._virtual(left_family, stance, seam))
        reps = tuple(out)
        self.left_classes[family] = reps
        return reps

    def _seat_varies(self, family: str, r1tok, r2tok, r3tok) -> bool:
        token = RightToken("letter", family)
        for left in self._seat_left_classes(family):
            if r3tok is None:
                baseline = self._seat_outcome(left, token, r1tok, r2tok, EDGE, EDGE)
            else:
                baseline = self._seat_outcome(left, token, r1tok, r2tok, r3tok, EDGE)
            if baseline is SEAT_RAISED:
                return True
            if baseline is SEAT_UNREACHABLE:
                continue
            for probe_token in self.probes:
                if r3tok is None:
                    edge4 = self._seat_outcome(left, token, r1tok, r2tok, probe_token, EDGE)
                    if edge4 is SEAT_RAISED or edge4 is SEAT_UNREACHABLE or edge4 != baseline:
                        return True
                    unknown4 = self._seat_outcome(left, token, r1tok, r2tok, probe_token, UNKNOWN)
                    if unknown4 is SEAT_RAISED or unknown4 is SEAT_UNREACHABLE or unknown4 != edge4:
                        return True
                else:
                    varied = self._seat_outcome(left, token, r1tok, r2tok, r3tok, probe_token)
                    if varied is SEAT_RAISED or varied is SEAT_UNREACHABLE or varied != baseline:
                        return True
        return False

    def _seat_outcome(self, left, token, r1tok, r2tok, r3tok, r4tok):
        try:
            return self.engine.transition_trace(left, token, r1tok, r2tok, r3tok, r4tok).settled.cell
        except ERaisedError:
            return SEAT_RAISED
        except SettleError:
            return SEAT_UNREACHABLE


# --- the fixpoint -----------------------------------------------------------

NA_LABEL = "#NA"


@dataclass(frozen=True)
class Transition:
    input_glyph: str
    left: str
    right1: str
    right2: str
    right3: str
    right4: str
    outcome: str
    settled: Settled
    joint: bool
    prospect: int


def _chain_reach(cond: Condition) -> int:
    reach = 0
    if cond.then is not None:
        reach = max(reach, 1 + _chain_reach(cond.then))
    for ex in cond.except_:
        reach = max(reach, _chain_reach(ex))
    return reach


def build_tables(spec: Spec, features: frozenset[str], share: dict | None = None, share_delta: int = 0):
    engine = Engine(spec, features, share=share, share_delta=share_delta)
    liveness = ProspectLiveness(spec, engine)
    right_letters = [RightToken("letter", name) for name in spec.letters]
    right_options = list(BOUNDARIES) + right_letters
    liga_sequences = {name: r.sequence for name, r in spec.runes.items() if r.sequence is not None}
    formation_pairs = frozenset(liga_sequences.values())

    third_verdicts: dict[tuple, bool] = {}
    fourth_verdicts: dict[tuple, bool] = {}
    chains3 = {
        name: tuple(
            r.when.right
            for r in rune.prefer
            if r.when.right is not None and _chain_reach(r.when.right) >= 2
        )
        for name, rune in spec.runes.items()
    }
    chains4 = {
        name: tuple(
            r.when.right
            for r in rune.prefer
            if r.when.right is not None and _chain_reach(r.when.right) >= 3
        )
        for name, rune in spec.runes.items()
    }

    def third_matters(input_family: str, right1: str, right2: str) -> bool:
        key = (input_family, right1, right2)
        cached = third_verdicts.get(key)
        if cached is None:
            window = (RightToken("letter", right1), RightToken("letter", right2), UNKNOWN, UNKNOWN)
            cached = any(engine.cond_matches_right(chain, window) is None for chain in chains3[input_family])
            if not cached:
                cached = liveness.third_live(input_family, right1, right2)
            third_verdicts[key] = cached
        return cached

    def fourth_matters(input_family: str, right1: str, right2: str, right3: str) -> bool:
        key = (input_family, right1, right2, right3)
        cached = fourth_verdicts.get(key)
        if cached is None:
            window = (
                RightToken("letter", right1),
                RightToken("letter", right2),
                RightToken("letter", right3),
                UNKNOWN,
            )
            cached = any(engine.cond_matches_right(chain, window) is None for chain in chains4[input_family])
            if not cached:
                cached = liveness.fourth_live(input_family, right1, right2, right3)
            fourth_verdicts[key] = cached
        return cached

    transitions: dict[tuple, Transition] = {}
    seen: set[tuple] = set()
    worklist: list[tuple] = []
    for kind in ("edge", "space", "zwnj", "namer-dot"):
        for name in spec.order:
            worklist.append((LeftContext(kind), name, None, None, None))

    stranded = 0
    nocand = 0
    raised = 0
    while worklist:
        left, rune_name, r1_constraint, r2_allowed, r3_allowed = worklist.pop()
        item_key = ((left.kind, left.settled), rune_name, r1_constraint, r2_allowed, r3_allowed)
        if item_key in seen:
            continue
        seen.add(item_key)
        rune = spec.runes[rune_name]
        locked = left.kind == "zwnj" and rune.entry_bearing
        input_label = f"{rune_name}.noentry" if locked else rune_name
        if left.kind == "letter":
            left_label = cell_label(left.settled.cell)
        else:
            left_label = BOUNDARY_LABELS[left.kind]
        token = RightToken("letter", rune_name)
        r1_options = [r1_constraint] if r1_constraint is not None else right_options

        for right1 in r1_options:
            if right1.kind == "letter":
                right2_options = [
                    r
                    for r in right_options
                    if not (r.kind == "letter" and (right1.rune, r.rune) in formation_pairs)
                ]
                if r2_allowed is not None:
                    right2_options = [r for r in right2_options if r in r2_allowed]
                if rune.sequence is not None:
                    right2_options = [
                        r for r in right2_options if r.kind != "letter" or r.rune != rune.sequence[1]
                    ]
            else:
                right2_options = [EDGE]
            for right2 in right2_options:
                if (
                    right1.kind == "letter"
                    and right2.kind == "letter"
                    and third_matters(rune_name, right1.letter, right2.letter)
                ):
                    right3_slots: list[RightToken | None] = [
                        r
                        for r in right_options
                        if not (r.kind == "letter" and (right2.rune, r.rune) in formation_pairs)
                    ]
                    if r3_allowed is not None:
                        right3_slots = [r for r in right3_slots if r in r3_allowed]
                else:
                    right3_slots = [None]
                for right3 in right3_slots:
                    if (
                        right3 is not None
                        and right3.kind == "letter"
                        and fourth_matters(rune_name, right1.letter, right2.letter, right3.letter)
                    ):
                        right4_slots: list[RightToken | None] = [
                            r
                            for r in right_options
                            if not (r.kind == "letter" and (right3.rune, r.rune) in formation_pairs)
                        ]
                    else:
                        right4_slots = [None]
                    for right4 in right4_slots:
                        window_key = (
                            input_label,
                            left_label,
                            _label(right1),
                            _label(right2) if right1.kind == "letter" else NA_LABEL,
                            _label(right3) if right3 is not None else NA_LABEL,
                            _label(right4) if right4 is not None else NA_LABEL,
                        )
                        existing = transitions.get(window_key)
                        if existing is not None:
                            settled = existing.settled
                        else:
                            try:
                                trace = engine.transition_trace(
                                    left,
                                    token,
                                    right1,
                                    right2,
                                    right3 if right3 is not None else EDGE,
                                    right4 if right4 is not None else EDGE,
                                )
                            except EStrandedError:
                                stranded += 1
                                continue
                            except SettleError:
                                nocand += 1
                                continue
                            except ERaisedError:
                                raised += 1
                                continue
                            settled = trace.settled
                            transitions[window_key] = Transition(
                                input_label,
                                left_label,
                                window_key[2],
                                window_key[3],
                                window_key[4],
                                window_key[5],
                                cell_label(trace.settled.cell),
                                trace.settled,
                                trace.joint_floor,
                                trace.prospect,
                            )
                        if right1.kind == "letter":
                            successor_allowed = frozenset({right3}) if right3 is not None else r3_allowed
                            successor_r3 = frozenset({right4}) if right4 is not None else None
                            worklist.append(
                                (
                                    LeftContext("letter", settled),
                                    right1.letter,
                                    right2,
                                    successor_allowed,
                                    successor_r3,
                                )
                            )

    rows = sorted(transitions.values(), key=lambda t: (t.input_glyph, t.left, t.right1, t.right2, t.right3, t.right4))
    checksum = 0xCBF29CE484222325
    for row in rows:
        line = "\t".join(
            (
                row.input_glyph,
                row.left,
                row.right1,
                row.right2,
                row.right3,
                row.right4,
                row.outcome,
                "1" if row.joint else "0",
                str(row.prospect),
                str(row.settled.extension),
            )
        )
        for byte in line.encode():
            checksum = ((checksum ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        checksum = ((checksum ^ 10) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF

    cells = {row.settled.cell for row in rows}
    return {
        "windows": len(rows),
        "cells": len(cells),
        "checksum": checksum,
        "stranded": stranded,
        "nocand": nocand,
        "raised": raised,
        "candidates": engine.n_candidates,
        "prospect": engine.n_prospect,
        "trace": engine.n_trace,
        "favors": engine.n_favors,
        "memo_entries": len(engine.trace_cache),
        "fired": len(engine.fired),
        "engine": engine,
    }


def _label(token: RightToken) -> str:
    if token.kind == "letter":
        return token.letter
    return BOUNDARY_LABELS[token.kind]


CONFIG_FEATURES = [
    frozenset(),
    frozenset({"ss03"}),
    frozenset({"ss04"}),
    frozenset({"ss05"}),
    frozenset({"ss03", "ss05"}),
    frozenset({"ss10"}),
]
CONFIG_NAMES = ["default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10"]


def main() -> None:
    import json
    import resource
    import time

    spec_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "one"
    n_letters = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    spec = load_spec(spec_path, n_letters)
    results = []
    t0 = time.perf_counter()
    c0 = time.process_time()
    if mode == "one":
        r = build_tables(spec, CONFIG_FEATURES[0], None)
        r.pop("engine")
        results.append(dict(r, config="default"))
    elif mode == "six-noshare":
        for i, features in enumerate(CONFIG_FEATURES):
            r = build_tables(spec, features, None, 0)
            r.pop("engine")
            results.append(dict(r, config=CONFIG_NAMES[i]))
    else:
        share: dict = {}
        for i, features in enumerate(CONFIG_FEATURES):
            delta = 0
            for f in features:
                delta |= 1 << FEATURE_NAMES.index(f)
            r = build_tables(spec, features, share if i > 0 else None, delta)
            engine = r.pop("engine")
            if i == 0:
                share.update({k: (v, engine.trace_fired.get(k, ())) for k, v in engine.trace_cache.items()})
            results.append(dict(r, config=CONFIG_NAMES[i]))
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    print(
        json.dumps(
            {
                "impl": "python-baseline",
                "mode": mode,
                "letters": n_letters,
                "wall_seconds": wall,
                "cpu_seconds": cpu,
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "configs": results,
            }
        )
    )


if __name__ == "__main__":
    main()
