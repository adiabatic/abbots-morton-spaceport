"""Decision-table builder for the prototype (prototype/PLAN.md section 1, table.py).

`build_table(spec, features)` tabulates the settlement kernel (`settle.transition`) over every (settled-left context, rune, raw-right-1, raw-right-2) window reachable under settlement, by fixpoint over reachable left states rather than by string enumeration, so the table is exact. Rows are keyed by glyph labels: settled glyph names in the backtrack slot, raw glyph names (bare runes, the ss03 marker twin, the formed ligature) or boundary tokens in the lookahead slots. The ss03 feature folds into the marker glyph, and the builder asserts the fold is conflict-free, which is what lets one feature-agnostic settlement lookup serve both configurations.

Outcome-partition compression is DFA-style per input rune and per slot: two fillers land in one class iff their full outcome signatures over the other slots are identical (unreachable windows count as a distinct absent value, which can only split classes, never corrupt them — sound, possibly suboptimal). The builder machine-checks the section 6.1 invariants: `E-STRANDED` (every committed exit reaches an acceptor — the fixpoint walks every committed seam into the next position and `transition` raises on a violation) and the step-4.2 joint-flag check (rows whose ranking was tie-broken by the structural floor between candidates differing in seam realization are flagged `joint` in the TSV).

Rule ordering per input rune follows PLAN.md section 4: committed-backtrack groups before the no-seam group; within a group, boundary-outcome rows with `uni200C` explicit in the class first, then letter-window rows (two-lookahead-slot before one-slot), then the bare run-edge fallback. Letter rows whose outcome equals the group's fallback outcome are omitted (fall-through reaches the fallback), and identity rows are simply not emitted — absence of a rule is the encoding of "stay bare".

Run as a script to write prototype/out/settlement.tsv and print the table summary.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from settle import EDGE, SPACE, ZWNJ, LeftContext, RightToken, transition
from spec import SPEC, SubsetSpec

EDGE_LABEL = "#EDGE"
NA_LABEL = "#NA"
BOUNDARY_LABELS = {"space": "space", "zwnj": "uni200C", "edge": EDGE_LABEL}
BOUNDARYISH = {EDGE_LABEL, NA_LABEL, "space", "uni200C"}


def raw_glyph_name(family: str, features: frozenset[str], locked: bool, spec: SubsetSpec = SPEC) -> str:
    name = family
    marker = spec.marker_families.get(family)
    if marker and "ss03" in features:
        name = marker
    if locked:
        name = f"{name}.noentry"
    return name


@dataclass(frozen=True)
class Transition:
    input_glyph: str
    left: str
    right1: str
    right2: str
    outcome: str
    seam: int | None
    joint: bool
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


@dataclass
class DecisionTable:
    transitions: list[Transition] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    reachable_glyphs: set[str] = field(default_factory=set)
    identity_guard_rules: int = 0
    ignore_guards_needed: int = 0

    @property
    def raw_rule_count(self) -> int:
        return sum(1 for t in self.transitions if not t.is_identity)

    def write_tsv(self, path: Path) -> None:
        lines = ["input\tbacktrack\tlookahead1\tlookahead2\toutcome\tjoint\tprovenance"]
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


def _left_key(left: LeftContext) -> tuple:
    return (left.kind, left.family, left.committed, left.extended, left.glyph_name)


def _left_label(left: LeftContext) -> str:
    if left.kind == "letter":
        return left.glyph_name
    return BOUNDARY_LABELS[left.kind]


def _right_label(token: RightToken, features: frozenset[str], spec: SubsetSpec) -> str:
    if token.kind == "letter":
        return raw_glyph_name(token.family, features, False, spec)
    return BOUNDARY_LABELS[token.kind]


def _enumerate_config(features: frozenset[str], spec: SubsetSpec) -> list[Transition]:
    letter_families = tuple(spec.families)
    right_letters = [RightToken("letter", family) for family in letter_families]
    right_boundaries = [EDGE, SPACE, ZWNJ]
    transitions: dict[tuple, Transition] = {}
    seen_items: set[tuple] = set()
    worklist: list[tuple[LeftContext, str, bool]] = []

    for kind in ("edge", "space", "zwnj"):
        left = LeftContext(kind)
        for family in letter_families:
            locked = kind == "zwnj" and family in spec.entry_bearing_families
            worklist.append((left, family, locked))

    while worklist:
        left, family, locked = worklist.pop()
        item_key = (_left_key(left), family, locked)
        if item_key in seen_items:
            continue
        seen_items.add(item_key)
        input_glyph = raw_glyph_name(family, features, locked, spec)
        for right1 in right_boundaries + right_letters:
            right2_options = right_boundaries + right_letters if right1.kind == "letter" else [EDGE]
            for right2 in right2_options:
                settled, joint, notes = transition(left, family, locked, features, right1, right2, spec)
                row = Transition(
                    input_glyph=input_glyph,
                    left=_left_label(left),
                    right1=_right_label(right1, features, spec),
                    right2=_right_label(right2, features, spec) if right1.kind == "letter" else NA_LABEL,
                    outcome=settled.glyph_name,
                    seam=settled.seam_toward_next,
                    joint=joint,
                    provenance=notes + (spec.glyphs[settled.glyph_name].provenance,),
                )
                existing = transitions.get(row.key)
                if existing is not None and existing.outcome != row.outcome:
                    raise RuntimeError(
                        f"window {row.key} settles inconsistently: {existing.outcome} vs {row.outcome}"
                    )
                transitions[row.key] = row
                if right1.kind == "letter":
                    successor = LeftContext(
                        "letter",
                        family=family,
                        committed=settled.seam_toward_next,
                        extended="ex-ext-1" in settled.modifiers,
                        glyph_name=settled.glyph_name,
                    )
                    worklist.append((successor, right1.family, False))
    return list(transitions.values())


def _signature_blocks(values: list[str], signature_of) -> list[tuple[str, ...]]:
    groups: dict[frozenset, list[str]] = {}
    for value in values:
        groups.setdefault(signature_of(value), []).append(value)
    return sorted((tuple(sorted(members)) for members in groups.values()))


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
        raise RuntimeError(
            f"{input_glyph}: boundary left contexts split across outcome blocks: {default_blocks}"
        )

    identity_guards = 0

    def emit_group(members: tuple[str, ...], backtrack: tuple[str, ...] | None, rules: list[Rule]) -> None:
        nonlocal identity_guards
        representative = members[0]
        group_rows = {(r1, r2): row for (left, r1, r2), row in rows.items() if left == representative}
        group_r1s = sorted({r1 for r1, _r2 in group_rows})

        r1_blocks = _signature_blocks(
            group_r1s,
            lambda r1: frozenset((r2, outcome(representative, r1, r2)) for r2 in r2s),
        )

        boundary_block = next(
            (block for block in r1_blocks if set(block) & {EDGE_LABEL, "space", "uni200C"}), None
        )
        fallback_outcome = input_glyph
        boundary_rules: list[Rule] = []
        fallback_rules: list[Rule] = []
        if boundary_block is not None:
            samples = {group_rows[(r1, NA_LABEL)].outcome for r1 in boundary_block}
            if len(samples) != 1:
                raise RuntimeError(f"{input_glyph}: boundary lookaheads disagree: {samples}")
            sample = group_rows[(boundary_block[0], NA_LABEL)]
            fallback_outcome = sample.outcome
            if fallback_outcome != input_glyph:
                boundary_rules.append(
                    Rule(
                        input_glyph,
                        backtrack,
                        ("uni200C", "space"),
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
                raise RuntimeError(f"{input_glyph}: mixed letter/boundary lookahead block {r1_block}")
            block_r2s = sorted({r2 for (r1, r2) in group_rows if r1 == r1_block[0]})
            r2_blocks = _signature_blocks(
                block_r2s,
                lambda r2: frozenset((r1, outcome(representative, r1, r2)) for r1 in r1_block),
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
            # Outcome depends on the second lookahead slot. Order inside the split: the boundary row (uni200C explicit at the slot, the section 7 mandate) first, so no later row of this window can match across a skipped ZWNJ; then letter-constrained two-slot rows, where an identity outcome becomes an identity guard whenever a slot-dropped fallback follows (otherwise the fallback would steal its windows); then the fallback, which is what catches the run edge — a positive lookahead class cannot match end-of-buffer.
            slot_fallback: Rule | None = None
            boundary_slot_rule: Rule | None = None
            two_slot_rules: list[Rule] = []
            for r2_block in r2_blocks:
                sample = group_rows[(r1_block[0], r2_block[0])]
                out = sample.outcome
                r2_letters = tuple(label for label in r2_block if label not in BOUNDARYISH)
                if set(r2_block) & BOUNDARYISH:
                    if set(r2_block) - set(r2_letters) - BOUNDARYISH:
                        raise RuntimeError(f"{input_glyph}: unexpected labels in r2 block {r2_block}")
                    if out != input_glyph:
                        boundary_slot_rule = Rule(
                            input_glyph,
                            backtrack,
                            letters,
                            ("uni200C", "space"),
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

    # ZWNJ coverage at the backtrack slot (section 7's fourth slot): an input glyph the chokepoint never locks can sit immediately after ZWNJ as its raw self, and a backtrack-classed rule on it could match across the skipped ZWNJ to the glyph before the break. Defense: replicate the boundary-left behavior with uni200C explicit in the backtrack slot, ordered ahead of every backtrack-classed rule — the rule-shaped clones first, then an identity catch-all that shields any remaining window. Lockable inputs need none of this: after ZWNJ they are locked twins, whose rules are backtrack-free by construction (the entry is severed, so no left context can matter).
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
                ("ZWNJ backtrack-slot identity guard (PLAN.md deviation 13, probe 2)",),
                False,
            )
        )
    return zwnj_backtrack_guards + committed_rules + default_rules, identity_guards


def build_table(
    spec: SubsetSpec = SPEC, features: tuple[frozenset[str], ...] = SPEC.feature_configurations
) -> DecisionTable:
    merged: dict[tuple, Transition] = {}
    for config in features:
        for row in _enumerate_config(config, spec):
            existing = merged.get(row.key)
            if existing is not None and existing.outcome != row.outcome:
                raise RuntimeError(
                    f"feature fold conflict at {row.key}: {existing.outcome} vs {row.outcome} — the marker encoding cannot express this"
                )
            merged[row.key] = row

    table = DecisionTable(transitions=sorted(merged.values(), key=lambda t: t.key))

    by_input: dict[str, dict[tuple[str, str, str], Transition]] = {}
    for row in table.transitions:
        by_input.setdefault(row.input_glyph, {})[(row.left, row.right1, row.right2)] = row

    for input_glyph in sorted(by_input):
        never_locked = input_glyph.split(".")[0] not in spec.entry_bearing_families
        rules, guards = _rules_for_input(input_glyph, by_input[input_glyph], never_locked)
        table.rules.extend(rules)
        table.identity_guard_rules += guards

    for row in table.transitions:
        table.reachable_glyphs.add(row.input_glyph)
        table.reachable_glyphs.add(row.outcome)
    table.reachable_glyphs.update(("space", "uni200C"))
    for lead, trail, ligature in spec.formation:
        table.reachable_glyphs.update((lead, trail, ligature))

    unknown = table.reachable_glyphs - set(spec.glyphs)
    if unknown:
        raise RuntimeError(f"reachable glyphs missing from spec.GLYPHS: {sorted(unknown)}")
    _validate_rules(table)
    return table


def _validate_rules(table: DecisionTable) -> None:
    """Replay every reachable transition against the ordered rule list under first-match-wins semantics (the within-lookup behavior under cross-shaper test). A divergence means the compression or the rule ordering broke the table."""
    rules_by_input: dict[str, list[Rule]] = {}
    for rule in table.rules:
        rules_by_input.setdefault(rule.input_glyph, []).append(rule)
    failures = []
    for row in table.transitions:
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
        for key, expected, predicted in failures[:20]:
            print(f"rule replay mismatch at {key}: settlement says {expected}, rules say {predicted}")
        raise RuntimeError(f"{len(failures)} rule replay mismatches")


def main() -> None:
    table = build_table()
    out_dir = Path(__file__).resolve().parent / "out"
    table.write_tsv(out_dir / "settlement.tsv")
    minted = set(SPEC.glyphs)
    joint_rows = sum(1 for rule in table.rules if rule.joint)
    two_slot = sum(1 for rule in table.rules if rule.look2)
    print(f"transitions (reachable windows): {len(table.transitions)}")
    print(f"non-identity transitions (pre-partition rows): {table.raw_rule_count}")
    print(
        f"compressed rules: {len(table.rules)} (two-lookahead-slot rules: {two_slot}, joint-flagged: {joint_rows})"
    )
    print(
        f"identity guard rules: {table.identity_guard_rules}; reachable glyphs: {len(table.reachable_glyphs)} of {len(minted)} minted"
    )
    unreached = sorted(minted - table.reachable_glyphs)
    if unreached:
        print(f"minted but unreachable: {unreached}")
    print(f"settlement table written to {out_dir / 'settlement.tsv'}")


if __name__ == "__main__":
    main()
