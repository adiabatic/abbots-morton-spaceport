"""Decision-table and treaty-table builders (M1-PLAN section 5, Group 2), promoted from prototype/table.py per the Recon B promotion map.

`build_tables(spec, features)` tabulates the settlement kernel over every (settled-left state, rune, raw-right-1, raw-right-2) window reachable under settlement for one feature configuration, by fixpoint over reachable left states rather than string enumeration, so the table is exact. Windows that formation makes impossible are excluded — but a ligature pair survives unformed exactly where the section 5.7 late-formation guard fires, so pair windows are enumerated under precisely the guard-firing follower contexts (`_survivable_formation_windows`): the lead's window is admitted per guard-firing right2, and the trail's window inherits the matching allowed-right2 set through the worklist, keeping the fixpoint exact. The mirror facet holds for formed-ligature tokens at any slot: a ligature input's window, and any window with a ligature at right1, is admitted only where that ligature's own guard does NOT fire over the raw tokens its post-formation neighbors stand for (`liga_formed_before`), existentially over the beyond-window slot. ZWNJ-locked entry-bearing inputs enumerate under the chokepoint twin's glyph name (`model.locked_glyph_name`, the `<raw>.noentry` shape the emitter's chokepoint actually produces), locked before settlement — which keeps each plain input's boundary-left outcomes in a single block, exactly as the prototype encoded it.

Outcome-partition compression is DFA-style per input and per slot: two fillers land in one class iff their full outcome signatures over the other slots are identical. `assert_outcome_partition` re-derives the partitions and replays every reachable transition against the ordered rules under first-match-wins semantics — the hard build invariant of prototype follow-up 1. Rule ordering per input follows the proven discipline: boundary-outcome rows with `uni200C` explicit in the class first, three-lookahead-slot rows before two-slot rows before one-slot rows, identity rows omitted, the slot-dropped fallback last, plus ZWNJ backtrack-slot coverage guards for never-locked inputs.

Rows carry a fourth window slot, `right3`, enumerated lazily: only an input whose own rune carries a depth-3 prefer record (`depth3_inputs`) gets its windows split by the raw third lookahead, and only where both nearer slots are letters — everywhere else the slot stays `#NA`, mirroring the established convention that no record peeks past a boundary. An enumerated window's settled left state is reachable only alongside right2 equal to that window's right3, so the worklist pins the successor's allowed-right2 set to that singleton — the same exactness plumbing the late-formation guard already rides — and the right3 options replay the right2 filters shifted one slot (formation-impossible adjacent pairs, guard-firing follower sets, `liga_formed_before` with the second slot now pinned). The fifth slot, `right4`, repeats the pattern one deeper: only a depth-4 input (`depth4_inputs`) with letters at all three nearer slots enumerates it, its options replay the same filters shifted once more, and the worklist pins the successor's right3 to the producing window's right4. `_assert_window_arity` ties the Transition/Rule slot count to `model.RIGHT_WINDOW_SLOTS` at import, so the chain cap and the table can only widen together.

Joint rows combine both section 6.1 flags: ranking ties broken by the structural floor between candidates differing in seam realization, and windows whose deliberately optimistic prospect diverges from the follower's actual settled choice. Both TSV artifacts are diff-stable (section 8): sorted rows, provenance pointers, deterministic labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from rebuild.pipeline.model import (
    RIGHT_WINDOW_SLOTS,
    CellId,
    ResolvedSpec,
    Settled,
    feature_config_token,
    locked_glyph_name,
    parse_adjustment,
)
from rebuild.pipeline.settle import (
    EDGE,
    NAMER_DOT,
    SPACE,
    ZWNJ,
    Engine,
    LeftContext,
    RightToken,
    cell_label,
    is_entry_bearing,
)

EDGE_LABEL = "#EDGE"
NA_LABEL = "#NA"
BOUNDARY_LEFT_LABELS = {
    "edge": EDGE_LABEL,
    "space": "space",
    "zwnj": "uni200C",
    "namer-dot": "periodcentered",
}
BOUNDARYISH = {EDGE_LABEL, NA_LABEL, "space", "uni200C", "periodcentered"}
BOUNDARY_LOOKAHEAD_CLASS = ("uni200C", "space", "periodcentered")


class PartitionError(RuntimeError):
    pass


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
    left_settled: Settled | None
    joint: bool
    prospect: int
    provenance: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (self.input_glyph, self.left, self.right1, self.right2, self.right3, self.right4)

    @property
    def is_identity(self) -> bool:
        return self.outcome == self.input_glyph


@dataclass(frozen=True)
class Rule:
    input_glyph: str
    backtrack: tuple[str, ...] | None
    look1: tuple[str, ...] | None
    look2: tuple[str, ...] | None
    look3: tuple[str, ...] | None
    look4: tuple[str, ...] | None
    outcome: str
    provenance: tuple[str, ...]
    joint: bool


def _assert_window_arity(expected: int) -> None:
    transition_slots = sum(
        1 for name in Transition.__dataclass_fields__ if name.startswith("right") and name[5:].isdigit()
    )
    rule_slots = sum(
        1 for name in Rule.__dataclass_fields__ if name.startswith("look") and name[4:].isdigit()
    )
    if transition_slots != expected or rule_slots != expected:
        raise AssertionError(
            f"model.RIGHT_WINDOW_SLOTS = {expected} but table.Transition carries {transition_slots} right slots and table.Rule {rule_slots} look slots — a chain-cap raise without the matching table widening would bake records past the window in silently; widen table/settle/emit_gsub/conform/tablediff together with the constant"
        )


_assert_window_arity(RIGHT_WINDOW_SLOTS)


@dataclass(frozen=True)
class TreatyRow:
    left: str
    right: str
    junction: str  # a height name or "break"
    extension: int
    kern: int = 0


@dataclass
class DecisionTable:
    config: str
    transitions: tuple[Transition, ...] = ()
    rules: tuple[Rule, ...] = ()
    identity_guard_rules: int = 0
    cited_provenance: frozenset[str] = (
        frozenset()
    )  # YAML pointers of every authored record the engine fired while tabulating this configuration (Engine.fired); the dead-policy gate's exercised-ness channel
    _cells: frozenset[CellId] = field(default_factory=frozenset)

    def reachable_cells(self) -> frozenset[CellId]:
        return self._cells

    def joint_rows(self) -> frozenset[int]:
        return frozenset(index for index, rule in enumerate(self.rules) if rule.joint)

    def assert_outcome_partition(self) -> None:
        """The hard build invariant (prototype follow-up 1): recompute the per-slot signature partitions and verify disjoint cover, then replay every reachable transition against the ordered rules under first-match-wins semantics."""
        by_input: dict[str, dict[tuple[str, str, str, str, str], Transition]] = {}
        for row in self.transitions:
            by_input.setdefault(row.input_glyph, {})[
                (row.left, row.right1, row.right2, row.right3, row.right4)
            ] = row
        for input_glyph, rows in by_input.items():
            lefts = sorted({left for left, _r1, _r2, _r3, _r4 in rows})
            r1s = sorted({r1 for _left, r1, _r2, _r3, _r4 in rows})
            r2s = sorted({r2 for _left, _r1, r2, _r3, _r4 in rows})
            r3s = sorted({r3 for _left, _r1, _r2, r3, _r4 in rows})
            r4s = sorted({r4 for _left, _r1, _r2, _r3, r4 in rows})

            def outcome(left: str, r1: str, r2: str, r3: str, r4: str) -> str | None:
                row = rows.get((left, r1, r2, r3, r4))
                return row.outcome if row is not None else None

            blocks = _signature_blocks(
                lefts,
                lambda left: frozenset(
                    ((r1, r2, r3, r4), outcome(left, r1, r2, r3, r4))
                    for r1 in r1s
                    for r2 in r2s
                    for r3 in r3s
                    for r4 in r4s
                ),
            )
            covered: set[str] = set()
            for block in blocks:
                if covered & set(block):
                    raise PartitionError(
                        f"{input_glyph}: left-slot classes are not a partition: {block} overlaps {sorted(covered)}"
                    )
                covered.update(block)
            if covered != set(lefts):
                raise PartitionError(f"{input_glyph}: left-slot classes do not cover all observed labels")
        self._replay()

    def _replay(self) -> None:
        rules_by_input: dict[str, list[Rule]] = {}
        for rule in self.rules:
            rules_by_input.setdefault(rule.input_glyph, []).append(rule)
        failures = []
        for row in self.transitions:
            predicted = row.input_glyph
            for rule in rules_by_input.get(row.input_glyph, ()):
                if rule.backtrack is not None and row.left not in rule.backtrack:
                    continue
                if rule.look1 is not None and row.right1 not in rule.look1:
                    continue
                if rule.look2 is not None and row.right2 not in rule.look2:
                    continue
                if rule.look3 is not None and row.right3 not in rule.look3:
                    continue
                if rule.look4 is not None and row.right4 not in rule.look4:
                    continue
                predicted = rule.outcome
                break
            if predicted != row.outcome:
                failures.append((row.key, row.outcome, predicted))
        if failures:
            sample = "; ".join(
                f"{key}: settlement says {expected}, rules say {predicted}"
                for key, expected, predicted in failures[:5]
            )
            raise PartitionError(f"{len(failures)} first-match-wins replay mismatches: {sample}")

    def assert_e_stranded(self) -> None:
        """Every committed exit in the table has at least one transition settling the follower — the fixpoint enqueues every successor and the kernel raises E-STRANDED on a violation, so this re-walk is a belt-and-suspenders assertion."""
        keys = {(row.left, row.input_glyph) for row in self.transitions}
        for row in self.transitions:
            if row.settled.seam is None or row.right1 in BOUNDARYISH:
                continue
            successor = (row.outcome, row.right1)
            if successor not in keys:
                raise PartitionError(
                    f"E-STRANDED at table level: committed seam {row.settled.seam} from {row.outcome} into {row.right1} has no successor transition"
                )

    def write_tsv(self, path: Path) -> None:
        lines = [
            f"# settlement table, config {self.config}",
            "input\tbacktrack\tlookahead1\tlookahead2\tlookahead3\tlookahead4\toutcome\tjoint\tprovenance",
        ]
        for rule in self.rules:
            lines.append(
                "\t".join(
                    (
                        rule.input_glyph,
                        " ".join(rule.backtrack) if rule.backtrack else "-",
                        " ".join(rule.look1) if rule.look1 else "-",
                        " ".join(rule.look2) if rule.look2 else "-",
                        " ".join(rule.look3) if rule.look3 else "-",
                        " ".join(rule.look4) if rule.look4 else "-",
                        rule.outcome,
                        "joint" if rule.joint else "-",
                        "; ".join(dict.fromkeys(p for p in rule.provenance if p)),
                    )
                )
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")


@dataclass
class TreatyTable:
    config: str
    rows: tuple[TreatyRow, ...] = ()

    def write_tsv(self, path: Path) -> None:
        lines = [f"# treaty table, config {self.config}", "left\tright\tjunction\textension\tkern"]
        for row in self.rows:
            lines.append("\t".join((row.left, row.right, row.junction, str(row.extension), str(row.kern))))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")


def right_chain_reach(cond) -> int:
    """How many raw slots past its own a right condition's then: chains read: a then: hop advances one slot, and an except: entry tests its parent's slot, so its hops count from there. Mirrors spec_load's raw-dict lint over the resolved Condition."""
    reach = 0
    if cond.then is not None:
        reach = max(reach, 1 + right_chain_reach(cond.then))
    for ex in cond.except_:
        reach = max(reach, right_chain_reach(ex))
    return reach


def depth3_inputs(spec: ResolvedSpec) -> frozenset[str]:
    """The rune names whose windows the raw third lookahead can decide: only an own-rune prefer record ever receives the real right3 (settle's `_prefer_favors` discipline), so exactly the runes carrying a prefer whose right condition chains two hops."""
    return _deep_inputs(spec, 2)


def depth4_inputs(spec: ResolvedSpec) -> frozenset[str]:
    """The rune names whose windows the raw fourth lookahead can decide — a prefer whose right condition chains three hops. Always a subset of `depth3_inputs`; both gates apply, each opening its own slot."""
    return _deep_inputs(spec, 3)


def _deep_inputs(spec: ResolvedSpec, reach: int) -> frozenset[str]:
    out = set()
    for name, rune in spec.runes.items():
        for record in rune.policy.prefer:
            right = record.when.right
            if right is not None and right_chain_reach(right) >= reach:
                out.add(name)
    return frozenset(out)


def _formation_pairs(spec: ResolvedSpec) -> frozenset[tuple[str, str]]:
    pairs = set()
    for rune in spec.runes.values():
        if rune.sequence:
            for lead, trail in zip(rune.sequence, rune.sequence[1:]):
                pairs.add((lead, trail))
    return frozenset(pairs)


def _survivable_formation_windows(
    spec: ResolvedSpec, right_letters: list[RightToken], right_boundaries: list[RightToken]
) -> dict[tuple[str, str], dict[str, frozenset[RightToken] | None]]:
    """The section 5.7 late-formation guard translated into the table's post-formation label space: for each formation (lead, trail) pair, the right2 options under which the pair survives unformed, each mapped to the allowed right2 tokens of the trail's own subsequent window (None = unrestricted, the case where the follower is itself a formed ligature that swallowed both guard slots). The guard reads raw slots, so a ligature label at either slot is queried through its raw components."""
    from rebuild.pipeline import settle as settle_module

    def raw_of(token: RightToken) -> RightToken:
        if token.kind != "letter":
            return token
        sequence = spec.runes[token.rune].sequence
        return RightToken("letter", sequence[0]) if sequence else token

    out: dict[tuple[str, str], dict[str, frozenset[RightToken] | None]] = {}
    for name, rune in spec.runes.items():
        if not rune.sequence:
            continue
        pair = (rune.sequence[-2], rune.sequence[-1])
        follower_map: dict[str, frozenset[RightToken] | None] = {}
        for follower in right_letters:
            follower_sequence = spec.runes[follower.rune].sequence
            if follower_sequence:
                lead_token = RightToken("letter", follower_sequence[-2])
                trail_token = RightToken("letter", follower_sequence[-1])
                if settle_module.formation_blocked(spec, name, lead_token, trail_token):
                    follower_map[follower.rune] = None
                continue
            allowed = frozenset(
                option
                for option in right_boundaries + right_letters
                if settle_module.formation_blocked(spec, name, follower, raw_of(option))
            )
            if allowed:
                follower_map[follower.rune] = allowed
        if follower_map:
            out[pair] = follower_map
    return out


def _entry_extension(settled: Settled) -> int:
    total = 0
    for token in settled.cell.adjustments:
        op, side, argument = parse_adjustment(token)
        if side == "en" and isinstance(argument, int):
            if op == "ext":
                total += argument
            elif op == "con":
                total -= argument
    return total


def build_tables(spec: ResolvedSpec, features: frozenset[str]) -> tuple[DecisionTable, TreatyTable]:
    engine = Engine(spec, features)
    config = feature_config_token(features)
    letters = sorted(spec.runes)
    formation_pairs = _formation_pairs(spec)
    right_letters = [RightToken("letter", name) for name in letters]
    right_boundaries = [EDGE, SPACE, ZWNJ, NAMER_DOT]

    def right_label(token: RightToken) -> str:
        if token.kind == "letter":
            return token.rune
        return BOUNDARY_LEFT_LABELS[token.kind]

    survivable = _survivable_formation_windows(spec, right_letters, right_boundaries)
    deep_inputs = depth3_inputs(spec)
    deep4_inputs = depth4_inputs(spec)

    from rebuild.pipeline import settle as settle_module

    liga_sequences = {name: rune.sequence for name, rune in spec.runes.items() if rune.sequence}
    raw_second_options = right_boundaries + [t for t in right_letters if t.rune not in liga_sequences]

    def liga_formed_before(name: str, next1: RightToken, next2: RightToken | None) -> bool:
        """Whether a formed `name` ligature can immediately precede (next1, next2) in a post-formation stream: its own guard, read over the raw tokens those post-formation neighbors stand for, must not fire. `next2 = None` means the second guard slot lies beyond the window, so the verdict is existential over the raw options."""
        if next1.kind != "letter":
            return True
        sequence = liga_sequences.get(next1.rune)
        if sequence:
            first: RightToken = RightToken("letter", sequence[0])
            second: RightToken | None = RightToken("letter", sequence[1])
        else:
            first = next1
            if next2 is None:
                second = None
            elif next2.kind == "letter" and (next2_sequence := liga_sequences.get(next2.rune)):
                second = RightToken("letter", next2_sequence[0])
            else:
                second = next2
        if second is not None:
            return not settle_module.formation_blocked(spec, name, first, second)
        return any(
            not settle_module.formation_blocked(spec, name, first, option) for option in raw_second_options
        )

    transitions: dict[tuple[str, str, str, str, str, str], Transition] = {}
    seen: set[tuple] = set()
    # A worklist item is (left state, input rune, right1 constraint, right2 allowed-set, right3 allowed-set): a settled left state is reachable only alongside the right1 that was the producing window's right2 (an entry refusal or unlock conditioned on the follower makes other combinations contradictory — the left would never have committed there), so the fixpoint is exact, not merely sound. None = all right1 options (the boundary-left seeds). The right2 allowed-set carries the late-formation guard's second slot onto a surviving pair's trail window; None = unrestricted. The right3 allowed-set carries a producing window's enumerated right4 the same way, pinning a depth-4-decided left's successor windows to the third lookahead that was actually behind them.
    worklist: list[
        tuple[LeftContext, str, RightToken | None, frozenset[RightToken] | None, frozenset[RightToken] | None]
    ] = []
    for kind in ("edge", "space", "zwnj", "namer-dot"):
        for name in letters:
            worklist.append((LeftContext(kind), name, None, None, None))

    while worklist:
        left, rune_name, right1_constraint, right2_allowed, right3_allowed = worklist.pop()
        left_key = (left.kind, left.settled)
        if (left_key, rune_name, right1_constraint, right2_allowed, right3_allowed) in seen:
            continue
        seen.add((left_key, rune_name, right1_constraint, right2_allowed, right3_allowed))
        locked = left.kind == "zwnj" and is_entry_bearing(spec, rune_name)
        input_label = locked_glyph_name(rune_name) if locked else rune_name
        left_label = (
            BOUNDARY_LEFT_LABELS[left.kind] if left.kind != "letter" else cell_label(spec, left.settled.cell)
        )
        token = RightToken("letter", rune_name)
        right1_options = (
            [right1_constraint] if right1_constraint is not None else right_boundaries + right_letters
        )
        for right1 in right1_options:
            follower_map = None
            if right1.kind == "letter" and (rune_name, right1.rune) in formation_pairs:
                follower_map = survivable.get((rune_name, right1.rune))
                if follower_map is None:
                    continue
            if right1.kind == "letter":
                right2_options = [
                    r
                    for r in right_boundaries + right_letters
                    if not (
                        r.kind == "letter"
                        and (right1.rune, r.rune) in formation_pairs
                        and (right1.rune, r.rune) not in survivable
                    )
                ]
                if follower_map is not None:
                    right2_options = [
                        r for r in right2_options if r.kind == "letter" and r.rune in follower_map
                    ]
                if right2_allowed is not None:
                    right2_options = [r for r in right2_options if r in right2_allowed]
                if rune_name in liga_sequences:
                    right2_options = [r for r in right2_options if liga_formed_before(rune_name, right1, r)]
                if right1.rune in liga_sequences:
                    right2_options = [r for r in right2_options if liga_formed_before(right1.rune, r, None)]
            else:
                right2_options = [EDGE]
            for right2 in right2_options:
                if rune_name in deep_inputs and right1.kind == "letter" and right2.kind == "letter":
                    right3_options: list[RightToken | None] = [
                        r
                        for r in right_boundaries + right_letters
                        if not (
                            r.kind == "letter"
                            and (right2.rune, r.rune) in formation_pairs
                            and (right2.rune, r.rune) not in survivable
                        )
                    ]
                    if follower_map is not None:
                        trail_allowed = follower_map.get(right2.rune)
                        if trail_allowed is not None:
                            right3_options = [r for r in right3_options if r in trail_allowed]
                    if (right1.rune, right2.rune) in formation_pairs:
                        pair_map = survivable.get((right1.rune, right2.rune)) or {}
                        right3_options = [
                            r for r in right3_options if r.kind == "letter" and r.rune in pair_map
                        ]
                    if right1.rune in liga_sequences:
                        right3_options = [
                            r for r in right3_options if liga_formed_before(right1.rune, right2, r)
                        ]
                    if right2.rune in liga_sequences:
                        right3_options = [
                            r for r in right3_options if liga_formed_before(right2.rune, r, None)
                        ]
                    if right3_allowed is not None:
                        right3_options = [r for r in right3_options if r in right3_allowed]
                else:
                    right3_options = [None]
                for right3 in right3_options:
                    if rune_name in deep4_inputs and right3 is not None and right3.kind == "letter":
                        right4_options: list[RightToken | None] = [
                            r
                            for r in right_boundaries + right_letters
                            if not (
                                r.kind == "letter"
                                and (right3.rune, r.rune) in formation_pairs
                                and (right3.rune, r.rune) not in survivable
                            )
                        ]
                        if (right1.rune, right2.rune) in formation_pairs:
                            pair_map = survivable.get((right1.rune, right2.rune)) or {}
                            trail_allowed4 = pair_map.get(right3.rune)
                            if trail_allowed4 is not None:
                                right4_options = [r for r in right4_options if r in trail_allowed4]
                        if (right2.rune, right3.rune) in formation_pairs:
                            pair_map2 = survivable.get((right2.rune, right3.rune)) or {}
                            right4_options = [
                                r for r in right4_options if r.kind == "letter" and r.rune in pair_map2
                            ]
                        if right2.rune in liga_sequences:
                            right4_options = [
                                r for r in right4_options if liga_formed_before(right2.rune, right3, r)
                            ]
                        if right3.rune in liga_sequences:
                            right4_options = [
                                r for r in right4_options if liga_formed_before(right3.rune, r, None)
                            ]
                    else:
                        right4_options = [None]
                    for right4 in right4_options:
                        trace = engine.transition_trace(
                            left,
                            token,
                            right1,
                            right2,
                            right3 if right3 is not None else EDGE,
                            right4 if right4 is not None else EDGE,
                        )
                        row = Transition(
                            input_glyph=input_label,
                            left=left_label,
                            right1=right_label(right1),
                            right2=right_label(right2) if right1.kind == "letter" else NA_LABEL,
                            right3=right_label(right3) if right3 is not None else NA_LABEL,
                            right4=right_label(right4) if right4 is not None else NA_LABEL,
                            outcome=cell_label(spec, trace.settled.cell),
                            settled=trace.settled,
                            left_settled=left.settled,
                            joint=trace.joint_floor,
                            prospect=trace.prospect,
                            provenance=tuple(trace.notes),
                        )
                        existing = transitions.get(row.key)
                        if existing is not None and existing.outcome != row.outcome:
                            raise PartitionError(
                                f"window {row.key} settles inconsistently: {existing.outcome} vs {row.outcome}"
                            )
                        transitions[row.key] = row
                        if right1.kind == "letter":
                            if right3 is not None:
                                successor_allowed = frozenset({right3})
                            else:
                                successor_allowed = (
                                    follower_map.get(right2.rune) if follower_map is not None else None
                                )
                                # A right3_allowed pin that this window could not enumerate (the input is not deep) still names the raw token one past its window, which is the successor's right2 — forward it, or a depth-4-decided left leaks unreachable follower windows the conform transition gate then reports as dead.
                                if right3_allowed is not None:
                                    successor_allowed = (
                                        right3_allowed
                                        if successor_allowed is None
                                        else successor_allowed & right3_allowed
                                    )
                            successor_r3 = frozenset({right4}) if right4 is not None else None
                            worklist.append(
                                (
                                    LeftContext("letter", trace.settled),
                                    right1.rune,
                                    right2,
                                    successor_allowed,
                                    successor_r3,
                                )
                            )

    rows = _flag_prospect_joints(sorted(transitions.values(), key=lambda t: t.key))

    rules: list[Rule] = []
    identity_guards = 0
    by_input: dict[str, dict[tuple[str, str, str, str, str], Transition]] = {}
    for row in rows:
        by_input.setdefault(row.input_glyph, {})[
            (row.left, row.right1, row.right2, row.right3, row.right4)
        ] = row
    for input_glyph in sorted(by_input):
        never_locked = not is_entry_bearing(spec, input_glyph.split(".")[0])
        input_rules, guards = _rules_for_input(input_glyph, by_input[input_glyph], never_locked)
        rules.extend(input_rules)
        identity_guards += guards

    cells = {row.settled.cell for row in rows}
    decision = DecisionTable(
        config=config,
        transitions=tuple(rows),
        rules=tuple(rules),
        identity_guard_rules=identity_guards,
        cited_provenance=frozenset(engine.fired),
        _cells=frozenset(cells),
    )

    treaty_rows = sorted(
        {
            TreatyRow(
                left=row.left,
                right=row.outcome,
                junction=row.left_settled.seam if row.left_settled.seam is not None else "break",
                extension=(
                    (row.left_settled.extension + _entry_extension(row.settled))
                    if row.left_settled.seam is not None
                    else 0
                ),
            )
            for row in rows
            if row.left_settled is not None
        },
        key=lambda r: (r.left, r.right, r.junction),
    )
    return decision, TreatyTable(config=config, rows=tuple(treaty_rows))


def _flag_prospect_joints(rows: list[Transition]) -> list[Transition]:
    """Compare every row's optimistic prospect against the follower's actual settled choice and flag divergent rows joint (design section 6.1 step 4.2)."""
    successors: dict[tuple[str, str], list[Transition]] = {}
    for row in rows:
        successors.setdefault((row.left, row.input_glyph), []).append(row)
    flagged: list[Transition] = []
    for row in rows:
        joint = row.joint
        if not joint and row.right1 not in BOUNDARYISH and row.right2 not in BOUNDARYISH:
            for successor in successors.get((row.outcome, row.right1), ()):
                if successor.right1 != row.right2:
                    continue
                if row.right3 != NA_LABEL and successor.right2 != row.right3:
                    continue
                if row.right4 != NA_LABEL and successor.right3 != row.right4:
                    continue
                realized = 1 if successor.settled.seam is not None else 0
                if realized != row.prospect:
                    joint = True
                    break
        flagged.append(row if joint == row.joint else replace(row, joint=joint))
    return flagged


def _signature_blocks(values, signature_of) -> list[tuple[str, ...]]:
    groups: dict[frozenset, list[str]] = {}
    for value in values:
        groups.setdefault(signature_of(value), []).append(value)
    return sorted(tuple(sorted(members)) for members in groups.values())


def _rules_for_input(
    input_glyph: str, rows: dict[tuple[str, str, str, str, str], Transition], never_locked: bool
) -> tuple[list[Rule], int]:
    lefts = sorted({left for left, _r1, _r2, _r3, _r4 in rows})
    r1s = sorted({r1 for _left, r1, _r2, _r3, _r4 in rows})
    r2s = sorted({r2 for _left, _r1, r2, _r3, _r4 in rows})
    r3s = sorted({r3 for _left, _r1, _r2, r3, _r4 in rows})
    r4s = sorted({r4 for _left, _r1, _r2, _r3, r4 in rows})

    def outcome(left: str, r1: str, r2: str, r3: str, r4: str) -> str | None:
        row = rows.get((left, r1, r2, r3, r4))
        return row.outcome if row is not None else None

    left_blocks = _signature_blocks(
        lefts,
        lambda left: frozenset(
            ((r1, r2, r3, r4), outcome(left, r1, r2, r3, r4))
            for r1 in r1s
            for r2 in r2s
            for r3 in r3s
            for r4 in r4s
        ),
    )
    default_blocks = [block for block in left_blocks if set(block) & BOUNDARYISH]
    committed_blocks = [block for block in left_blocks if not set(block) & BOUNDARYISH]
    if len(default_blocks) > 1:
        raise PartitionError(
            f"{input_glyph}: boundary left contexts split across outcome blocks: {default_blocks}"
        )

    identity_guards = 0

    def emit_group(members: tuple[str, ...], backtrack: tuple[str, ...] | None, rules: list[Rule]) -> None:
        nonlocal identity_guards
        representative = members[0]
        group_rows = {
            (r1, r2, r3, r4): row for (left, r1, r2, r3, r4), row in rows.items() if left == representative
        }
        group_r1s = sorted({r1 for r1, _r2, _r3, _r4 in group_rows})

        r1_blocks = _signature_blocks(
            group_r1s,
            lambda r1: frozenset(
                ((r2, r3, r4), outcome(representative, r1, r2, r3, r4))
                for r2 in r2s
                for r3 in r3s
                for r4 in r4s
            ),
        )

        boundary_block = next((block for block in r1_blocks if set(block) & BOUNDARYISH), None)
        fallback_outcome = input_glyph
        boundary_rules: list[Rule] = []
        fallback_rules: list[Rule] = []
        if boundary_block is not None:
            samples = {
                group_rows[(r1, NA_LABEL, NA_LABEL, NA_LABEL)].outcome
                for r1 in boundary_block
                if (r1, NA_LABEL, NA_LABEL, NA_LABEL) in group_rows
            }
            if len(samples) != 1:
                raise PartitionError(f"{input_glyph}: boundary lookaheads disagree: {samples}")
            sample = next(
                group_rows[(r1, NA_LABEL, NA_LABEL, NA_LABEL)]
                for r1 in boundary_block
                if (r1, NA_LABEL, NA_LABEL, NA_LABEL) in group_rows
            )
            fallback_outcome = sample.outcome
            if fallback_outcome != input_glyph:
                boundary_rules.append(
                    Rule(
                        input_glyph,
                        backtrack,
                        BOUNDARY_LOOKAHEAD_CLASS,
                        None,
                        None,
                        None,
                        fallback_outcome,
                        sample.provenance,
                        sample.joint,
                    )
                )
                fallback_rules.append(
                    Rule(
                        input_glyph,
                        backtrack,
                        None,
                        None,
                        None,
                        None,
                        fallback_outcome,
                        sample.provenance,
                        sample.joint,
                    )
                )

        letter_rules: list[Rule] = []
        for r1_block in r1_blocks:
            if r1_block == boundary_block:
                continue
            letters = tuple(label for label in r1_block if label not in BOUNDARYISH)
            if set(r1_block) - set(letters):
                raise PartitionError(f"{input_glyph}: mixed letter/boundary lookahead block {r1_block}")
            block_r2s = sorted({r2 for (r1, r2, _r3, _r4) in group_rows if r1 == r1_block[0]})
            r2_blocks = _signature_blocks(
                block_r2s,
                lambda r2: frozenset(
                    ((r1, r3, r4), outcome(representative, r1, r2, r3, r4))
                    for r1 in r1_block
                    for r3 in r3s
                    for r4 in r4s
                ),
            )
            distinct_outcomes = {
                row.outcome for (r1, _r2, _r3, _r4), row in group_rows.items() if r1 in r1_block
            }
            block_joint = any(row.joint for (r1, _r2, _r3, _r4), row in group_rows.items() if r1 in r1_block)
            if len(distinct_outcomes) == 1:
                sample = next(
                    row
                    for (r1, r2, _r3, _r4), row in sorted(group_rows.items())
                    if r1 == r1_block[0] and r2 == block_r2s[0]
                )
                out = sample.outcome
                if out == fallback_outcome:
                    continue
                if out == input_glyph:
                    if fallback_outcome != input_glyph:
                        identity_guards += 1
                        letter_rules.append(
                            Rule(
                                input_glyph,
                                backtrack,
                                letters,
                                None,
                                None,
                                None,
                                out,
                                sample.provenance,
                                block_joint,
                            )
                        )
                    continue
                letter_rules.append(
                    Rule(
                        input_glyph, backtrack, letters, None, None, None, out, sample.provenance, block_joint
                    )
                )
                continue
            # Outcome depends on a later lookahead slot. Order inside the split: the boundary row (uni200C explicit at the slot) first, so no later row of this window can match across a skipped ZWNJ; then the third-slot bundles (each replaying the same discipline one slot over: boundary row, letter-constrained three-slot rules, the slot-dropped two-slot fallback), so three-slot rows precede every two-slot row; a third-slot block that itself splits by the fourth slot nests the same bundle once more (boundary row, four-slot rules, slot-dropped three-slot fallback), deduped only within its own bundle because its fallback screens it from the outer ones; then letter-constrained two-slot rules, where an identity outcome becomes an identity guard whenever a slot-dropped fallback follows; then the fallback, which catches the run edge — a positive lookahead class cannot match end-of-buffer.
            slot_fallback: Rule | None = None
            boundary_slot_rule: Rule | None = None
            deep_rules: list[Rule] = []
            two_slot_rules: list[Rule] = []
            for r2_block in r2_blocks:
                r2_letters = tuple(label for label in r2_block if label not in BOUNDARYISH)
                block_r3s = sorted(
                    {r3 for (r1, r2, r3, _r4) in group_rows if r1 == r1_block[0] and r2 == r2_block[0]}
                )
                block_outcomes = {
                    row.outcome
                    for (r1, r2, _r3, _r4), row in group_rows.items()
                    if r1 == r1_block[0] and r2 == r2_block[0]
                }
                if len(block_outcomes) == 1:
                    sample = next(
                        row
                        for (r1, r2, r3, _r4), row in sorted(group_rows.items())
                        if r1 == r1_block[0] and r2 == r2_block[0] and r3 == block_r3s[0]
                    )
                    out = sample.outcome
                    if set(r2_block) & BOUNDARYISH:
                        if set(r2_block) - set(r2_letters) - BOUNDARYISH:
                            raise PartitionError(f"{input_glyph}: unexpected labels in r2 block {r2_block}")
                        if out != input_glyph:
                            boundary_slot_rule = Rule(
                                input_glyph,
                                backtrack,
                                letters,
                                BOUNDARY_LOOKAHEAD_CLASS,
                                None,
                                None,
                                out,
                                sample.provenance,
                                block_joint,
                            )
                            slot_fallback = Rule(
                                input_glyph,
                                backtrack,
                                letters,
                                None,
                                None,
                                None,
                                out,
                                sample.provenance,
                                block_joint,
                            )
                        continue
                    two_slot_rules.append(
                        Rule(
                            input_glyph,
                            backtrack,
                            letters,
                            r2_letters,
                            None,
                            None,
                            out,
                            sample.provenance,
                            block_joint,
                        )
                    )
                    continue
                if set(r2_block) & BOUNDARYISH:
                    raise PartitionError(
                        f"{input_glyph}: boundary second-slot block {r2_block} splits by the third slot"
                    )
                r3_blocks = _signature_blocks(
                    block_r3s,
                    lambda r3: frozenset(
                        ((r1, r2, r4), outcome(representative, r1, r2, r3, r4))
                        for r1 in r1_block
                        for r2 in r2_block
                        for r4 in r4s
                    ),
                )
                slot3_fallback: Rule | None = None
                boundary_slot3_rule: Rule | None = None
                three_slot_rules: list[Rule] = []
                for r3_block in r3_blocks:
                    r3_letters = tuple(label for label in r3_block if label not in BOUNDARYISH)
                    block_r4s = sorted(
                        {
                            r4
                            for (r1, r2, r3, r4) in group_rows
                            if r1 == r1_block[0] and r2 == r2_block[0] and r3 == r3_block[0]
                        }
                    )
                    block4_outcomes = {
                        outcome(representative, r1_block[0], r2_block[0], r3_block[0], r4) for r4 in block_r4s
                    }
                    if len(block4_outcomes) == 1:
                        sample = group_rows[(r1_block[0], r2_block[0], r3_block[0], block_r4s[0])]
                        out = sample.outcome
                        if set(r3_block) & BOUNDARYISH:
                            if set(r3_block) - set(r3_letters) - BOUNDARYISH:
                                raise PartitionError(
                                    f"{input_glyph}: unexpected labels in r3 block {r3_block}"
                                )
                            if out != input_glyph:
                                boundary_slot3_rule = Rule(
                                    input_glyph,
                                    backtrack,
                                    letters,
                                    r2_letters,
                                    BOUNDARY_LOOKAHEAD_CLASS,
                                    None,
                                    out,
                                    sample.provenance,
                                    block_joint,
                                )
                                slot3_fallback = Rule(
                                    input_glyph,
                                    backtrack,
                                    letters,
                                    r2_letters,
                                    None,
                                    None,
                                    out,
                                    sample.provenance,
                                    block_joint,
                                )
                            continue
                        three_slot_rules.append(
                            Rule(
                                input_glyph,
                                backtrack,
                                letters,
                                r2_letters,
                                r3_letters,
                                None,
                                out,
                                sample.provenance,
                                block_joint,
                            )
                        )
                        continue
                    if set(r3_block) & BOUNDARYISH:
                        raise PartitionError(
                            f"{input_glyph}: boundary third-slot block {r3_block} splits by the fourth slot"
                        )
                    r4_blocks = _signature_blocks(
                        block_r4s,
                        lambda r4: frozenset(
                            ((r1, r2, r3), outcome(representative, r1, r2, r3, r4))
                            for r1 in r1_block
                            for r2 in r2_block
                            for r3 in r3_block
                        ),
                    )
                    slot4_fallback: Rule | None = None
                    boundary_slot4_rule: Rule | None = None
                    four_slot_rules: list[Rule] = []
                    for r4_block in r4_blocks:
                        sample = group_rows[(r1_block[0], r2_block[0], r3_block[0], r4_block[0])]
                        out = sample.outcome
                        r4_letters = tuple(label for label in r4_block if label not in BOUNDARYISH)
                        if set(r4_block) & BOUNDARYISH:
                            if set(r4_block) - set(r4_letters) - BOUNDARYISH:
                                raise PartitionError(
                                    f"{input_glyph}: unexpected labels in r4 block {r4_block}"
                                )
                            if out != input_glyph:
                                boundary_slot4_rule = Rule(
                                    input_glyph,
                                    backtrack,
                                    letters,
                                    r2_letters,
                                    r3_letters,
                                    BOUNDARY_LOOKAHEAD_CLASS,
                                    out,
                                    sample.provenance,
                                    block_joint,
                                )
                                slot4_fallback = Rule(
                                    input_glyph,
                                    backtrack,
                                    letters,
                                    r2_letters,
                                    r3_letters,
                                    None,
                                    out,
                                    sample.provenance,
                                    block_joint,
                                )
                            continue
                        four_slot_rules.append(
                            Rule(
                                input_glyph,
                                backtrack,
                                letters,
                                r2_letters,
                                r3_letters,
                                r4_letters,
                                out,
                                sample.provenance,
                                block_joint,
                            )
                        )
                    if boundary_slot4_rule is not None:
                        deep_rules.append(boundary_slot4_rule)
                    for rule in four_slot_rules:
                        if rule.outcome == input_glyph:
                            if slot4_fallback is None:
                                continue
                            identity_guards += 1
                        elif slot4_fallback is not None and rule.outcome == slot4_fallback.outcome:
                            continue
                        deep_rules.append(rule)
                    if slot4_fallback is not None:
                        deep_rules.append(slot4_fallback)
                if boundary_slot3_rule is not None:
                    deep_rules.append(boundary_slot3_rule)
                for rule in three_slot_rules:
                    if rule.outcome == input_glyph:
                        if slot3_fallback is None:
                            continue
                        identity_guards += 1
                    elif slot3_fallback is not None and rule.outcome == slot3_fallback.outcome:
                        continue
                    deep_rules.append(rule)
                if slot3_fallback is not None:
                    deep_rules.append(slot3_fallback)
            if boundary_slot_rule is not None:
                letter_rules.append(boundary_slot_rule)
            letter_rules.extend(deep_rules)
            for rule in two_slot_rules:
                if rule.outcome == input_glyph:
                    if slot_fallback is None:
                        continue
                    identity_guards += 1
                elif slot_fallback is not None and rule.outcome == slot_fallback.outcome:
                    continue
                letter_rules.append(rule)
            if slot_fallback is not None:
                letter_rules.append(slot_fallback)

        rules.extend(boundary_rules)
        rules.extend(letter_rules)
        rules.extend(fallback_rules)

    committed_rules: list[Rule] = []
    default_rules: list[Rule] = []
    for block in committed_blocks:
        emit_group(block, block, committed_rules)
    for block in default_blocks:
        emit_group(block, None, default_rules)

    # ZWNJ coverage at the backtrack slot: an input the chokepoint never locks can sit immediately after ZWNJ as its raw self, and a backtrack-classed rule could match across the skipped ZWNJ. Defense: replicate the boundary-left behavior with uni200C explicit in the backtrack slot, ordered ahead of every backtrack-classed rule, then an identity catch-all. Lockable inputs need none of this: after ZWNJ they are locked twins whose rows enumerate under the twin's own input label.
    zwnj_backtrack_guards: list[Rule] = []
    if never_locked and any(rule.backtrack for rule in committed_rules):
        for rule in default_rules:
            zwnj_backtrack_guards.append(
                Rule(
                    input_glyph,
                    ("uni200C",),
                    rule.look1,
                    rule.look2,
                    rule.look3,
                    rule.look4,
                    rule.outcome,
                    rule.provenance + ("ZWNJ backtrack-slot coverage row",),
                    rule.joint,
                )
            )
        identity_guards += 1
        zwnj_backtrack_guards.append(
            Rule(
                input_glyph,
                ("uni200C",),
                None,
                None,
                None,
                None,
                input_glyph,
                ("ZWNJ backtrack-slot identity guard",),
                False,
            )
        )
    return zwnj_backtrack_guards + committed_rules + default_rules, identity_guards
