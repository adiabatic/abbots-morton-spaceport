"""Decision-table and treaty-table builders (M1-PLAN section 5, Group 2), promoted from prototype/table.py per the Recon B promotion map.

`build_tables(spec, features)` tabulates the settlement kernel over every (settled-left state, rune, raw-right-1, raw-right-2) window reachable under settlement for one feature configuration, by fixpoint over reachable left states rather than string enumeration, so the table is exact. Windows that formation makes impossible (an adjacent ligature pair surviving unformed) are excluded. ZWNJ-locked entry-bearing inputs enumerate under the chokepoint twin's glyph name (`model.locked_glyph_name`, the `<raw>.noentry` shape the emitter's chokepoint actually produces), locked before settlement — which keeps each plain input's boundary-left outcomes in a single block, exactly as the prototype encoded it.

Outcome-partition compression is DFA-style per input and per slot: two fillers land in one class iff their full outcome signatures over the other slots are identical. `assert_outcome_partition` re-derives the partitions and replays every reachable transition against the ordered rules under first-match-wins semantics — the hard build invariant of prototype follow-up 1. Rule ordering per input follows the proven discipline: boundary-outcome rows with `uni200C` explicit in the class first, two-lookahead-slot rows before one-slot rows, identity rows omitted, the slot-dropped fallback last, plus ZWNJ backtrack-slot coverage guards for never-locked inputs.

Joint rows combine both section 6.1 flags: ranking ties broken by the structural floor between candidates differing in seam realization, and windows whose deliberately optimistic prospect diverges from the follower's actual settled choice. Both TSV artifacts are diff-stable (section 8): sorted rows, provenance pointers, deterministic labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from rebuild.pipeline.model import (
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
    outcome: str
    settled: Settled
    left_settled: Settled | None
    joint: bool
    prospect: int
    provenance: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.input_glyph, self.left, self.right1, self.right2)

    @property
    def is_identity(self) -> bool:
        return self.outcome == self.input_glyph


@dataclass(frozen=True)
class Rule:
    input_glyph: str
    backtrack: tuple[str, ...] | None
    look1: tuple[str, ...] | None
    look2: tuple[str, ...] | None
    outcome: str
    provenance: tuple[str, ...]
    joint: bool


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
        by_input: dict[str, dict[tuple[str, str, str], Transition]] = {}
        for row in self.transitions:
            by_input.setdefault(row.input_glyph, {})[(row.left, row.right1, row.right2)] = row
        for input_glyph, rows in by_input.items():
            lefts = sorted({left for left, _r1, _r2 in rows})
            r1s = sorted({r1 for _left, r1, _r2 in rows})
            r2s = sorted({r2 for _left, _r1, r2 in rows})

            def outcome(left: str, r1: str, r2: str) -> str | None:
                row = rows.get((left, r1, r2))
                return row.outcome if row is not None else None

            blocks = _signature_blocks(
                lefts, lambda left: frozenset(((r1, r2), outcome(left, r1, r2)) for r1 in r1s for r2 in r2s)
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
            "input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance",
        ]
        for rule in self.rules:
            lines.append(
                "\t".join(
                    (
                        rule.input_glyph,
                        " ".join(rule.backtrack) if rule.backtrack else "-",
                        " ".join(rule.look1) if rule.look1 else "-",
                        " ".join(rule.look2) if rule.look2 else "-",
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


def _formation_pairs(spec: ResolvedSpec) -> frozenset[tuple[str, str]]:
    pairs = set()
    for rune in spec.runes.values():
        if rune.sequence:
            for lead, trail in zip(rune.sequence, rune.sequence[1:]):
                pairs.add((lead, trail))
    return frozenset(pairs)


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

    transitions: dict[tuple[str, str, str, str], Transition] = {}
    seen: set[tuple] = set()
    # A worklist item is (left state, input rune, right1 constraint): a settled left state is reachable only alongside the right1 that was the producing window's right2 (an entry refusal or unlock conditioned on the follower makes other combinations contradictory — the left would never have committed there), so the fixpoint is exact, not merely sound. None = all right1 options (the boundary-left seeds).
    worklist: list[tuple[LeftContext, str, RightToken | None]] = []
    for kind in ("edge", "space", "zwnj", "namer-dot"):
        for name in letters:
            worklist.append((LeftContext(kind), name, None))

    while worklist:
        left, rune_name, right1_constraint = worklist.pop()
        left_key = (left.kind, left.settled)
        if (left_key, rune_name, right1_constraint) in seen:
            continue
        seen.add((left_key, rune_name, right1_constraint))
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
            if right1.kind == "letter" and (rune_name, right1.rune) in formation_pairs:
                continue
            if right1.kind == "letter":
                right2_options = [
                    r
                    for r in right_boundaries + right_letters
                    if not (r.kind == "letter" and (right1.rune, r.rune) in formation_pairs)
                ]
            else:
                right2_options = [EDGE]
            for right2 in right2_options:
                trace = engine.transition_trace(left, token, right1, right2)
                row = Transition(
                    input_glyph=input_label,
                    left=left_label,
                    right1=right_label(right1),
                    right2=right_label(right2) if right1.kind == "letter" else NA_LABEL,
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
                    worklist.append((LeftContext("letter", trace.settled), right1.rune, right2))

    rows = _flag_prospect_joints(sorted(transitions.values(), key=lambda t: t.key))

    rules: list[Rule] = []
    identity_guards = 0
    by_input: dict[str, dict[tuple[str, str, str], Transition]] = {}
    for row in rows:
        by_input.setdefault(row.input_glyph, {})[(row.left, row.right1, row.right2)] = row
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
    input_glyph: str, rows: dict[tuple[str, str, str], Transition], never_locked: bool
) -> tuple[list[Rule], int]:
    lefts = sorted({left for left, _r1, _r2 in rows})
    r1s = sorted({r1 for _left, r1, _r2 in rows})
    r2s = sorted({r2 for _left, _r1, r2 in rows})

    def outcome(left: str, r1: str, r2: str) -> str | None:
        row = rows.get((left, r1, r2))
        return row.outcome if row is not None else None

    left_blocks = _signature_blocks(
        lefts, lambda left: frozenset(((r1, r2), outcome(left, r1, r2)) for r1 in r1s for r2 in r2s)
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
        group_rows = {(r1, r2): row for (left, r1, r2), row in rows.items() if left == representative}
        group_r1s = sorted({r1 for r1, _r2 in group_rows})

        r1_blocks = _signature_blocks(
            group_r1s, lambda r1: frozenset((r2, outcome(representative, r1, r2)) for r2 in r2s)
        )

        boundary_block = next((block for block in r1_blocks if set(block) & BOUNDARYISH), None)
        fallback_outcome = input_glyph
        boundary_rules: list[Rule] = []
        fallback_rules: list[Rule] = []
        if boundary_block is not None:
            samples = {
                group_rows[(r1, NA_LABEL)].outcome for r1 in boundary_block if (r1, NA_LABEL) in group_rows
            }
            if len(samples) != 1:
                raise PartitionError(f"{input_glyph}: boundary lookaheads disagree: {samples}")
            sample = next(group_rows[(r1, NA_LABEL)] for r1 in boundary_block if (r1, NA_LABEL) in group_rows)
            fallback_outcome = sample.outcome
            if fallback_outcome != input_glyph:
                boundary_rules.append(
                    Rule(
                        input_glyph,
                        backtrack,
                        BOUNDARY_LOOKAHEAD_CLASS,
                        None,
                        fallback_outcome,
                        sample.provenance,
                        sample.joint,
                    )
                )
                fallback_rules.append(
                    Rule(
                        input_glyph, backtrack, None, None, fallback_outcome, sample.provenance, sample.joint
                    )
                )

        letter_rules: list[Rule] = []
        for r1_block in r1_blocks:
            if r1_block == boundary_block:
                continue
            letters = tuple(label for label in r1_block if label not in BOUNDARYISH)
            if set(r1_block) - set(letters):
                raise PartitionError(f"{input_glyph}: mixed letter/boundary lookahead block {r1_block}")
            block_r2s = sorted({r2 for (r1, r2) in group_rows if r1 == r1_block[0]})
            r2_blocks = _signature_blocks(
                block_r2s, lambda r2: frozenset((r1, outcome(representative, r1, r2)) for r1 in r1_block)
            )
            distinct_outcomes = {group_rows[(r1_block[0], block[0])].outcome for block in r2_blocks}
            block_joint = any(row.joint for (r1, _r2), row in group_rows.items() if r1 in r1_block)
            if len(distinct_outcomes) == 1:
                sample = group_rows[(r1_block[0], block_r2s[0])]
                out = sample.outcome
                if out == fallback_outcome:
                    continue
                if out == input_glyph:
                    if fallback_outcome != input_glyph:
                        identity_guards += 1
                        letter_rules.append(
                            Rule(input_glyph, backtrack, letters, None, out, sample.provenance, block_joint)
                        )
                    continue
                letter_rules.append(
                    Rule(input_glyph, backtrack, letters, None, out, sample.provenance, block_joint)
                )
                continue
            # Outcome depends on the second lookahead slot. Order inside the split: the boundary row (uni200C explicit at the slot) first, so no later row of this window can match across a skipped ZWNJ; then letter-constrained two-slot rows, where an identity outcome becomes an identity guard whenever a slot-dropped fallback follows; then the fallback, which catches the run edge — a positive lookahead class cannot match end-of-buffer.
            slot_fallback: Rule | None = None
            boundary_slot_rule: Rule | None = None
            two_slot_rules: list[Rule] = []
            for r2_block in r2_blocks:
                sample = group_rows[(r1_block[0], r2_block[0])]
                out = sample.outcome
                r2_letters = tuple(label for label in r2_block if label not in BOUNDARYISH)
                if set(r2_block) & BOUNDARYISH:
                    if set(r2_block) - set(r2_letters) - BOUNDARYISH:
                        raise PartitionError(f"{input_glyph}: unexpected labels in r2 block {r2_block}")
                    if out != input_glyph:
                        boundary_slot_rule = Rule(
                            input_glyph,
                            backtrack,
                            letters,
                            BOUNDARY_LOOKAHEAD_CLASS,
                            out,
                            sample.provenance,
                            block_joint,
                        )
                        slot_fallback = Rule(
                            input_glyph, backtrack, letters, None, out, sample.provenance, block_joint
                        )
                    continue
                two_slot_rules.append(
                    Rule(input_glyph, backtrack, letters, r2_letters, out, sample.provenance, block_joint)
                )
            if boundary_slot_rule is not None:
                letter_rules.append(boundary_slot_rule)
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
                input_glyph,
                ("ZWNJ backtrack-slot identity guard",),
                False,
            )
        )
    return zwnj_backtrack_guards + committed_rules + default_rules, identity_guards
