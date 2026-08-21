"""Decision-table and treaty-table data model and fold (M1-PLAN section 5, Group 2), promoted from prototype/table.py per the Recon B promotion map.

The fixpoint that fills these tables runs in the crate under `rebuild/kernel-rs` — the engine of record since issue 40's port landed, and since issue 78 the only fixpoint there is. This module is the Python half of that build: the window, rule and table data model; `assemble_tables`, which folds one `FixpointProduct` — the kernel boundary value, reached through `kernel_exec.enumerate_transitions` — into a decision table and a treaty table; the per-input rule fold and the treaty fold inside it; the serialized windows artifact; and the digests the rest of the rebuild states table identity at. What the rest of this docstring states is the semantics of the enumeration the fold is written against; the crate is where those rules execute, and `gate:conform` is the standing independent check that they execute as described.

The kernel tabulates settlement over every (settled-left state, rune, raw-right-1, raw-right-2) window reachable under settlement for one feature configuration, by fixpoint over reachable left states rather than string enumeration, so the table is exact. Windows that formation makes impossible are excluded — but a ligature pair survives unformed exactly where the section 5.7 late-formation guard fires, so pair windows are enumerated under precisely the guard-firing follower contexts: the lead's window is admitted per guard-firing right2, and the trail's window inherits the matching allowed-right2 set through the worklist, keeping the fixpoint exact. The mirror facet holds for formed-ligature tokens at any slot: a ligature input's window, and any window with a ligature at right1, is admitted only where that ligature's own guard does NOT fire over the raw tokens its post-formation neighbors stand for, existentially over the beyond-window slot. ZWNJ-locked entry-bearing inputs enumerate under the chokepoint twin's glyph name (`model.locked_glyph_name`, the `<raw>.noentry` shape the emitter's chokepoint actually produces), locked before settlement — which keeps each plain input's boundary-left outcomes in a single block, exactly as the prototype encoded it.

Outcome-partition compression is DFA-style per input and per slot: two fillers land in one class iff their full outcome signatures over the other slots are identical. `assert_outcome_partition` re-derives the partitions and replays every reachable transition against the ordered rules under first-match-wins semantics — the hard build invariant of prototype follow-up 1. The fold, the joint-flag pass, the treaty fold, the replay, and every serialized-rules consumer read the expanded label-grain row stream (`DecisionTable.expanded_transitions`): a class-grain enumeration expands each row to its full member product before anything downstream runs, so those consumers are byte-identical to a label-grain build by construction, and `Rule` objects carry label vocabulary only — no class id ever reaches `_rules_for_input`, `write_tsv`, or a serialized rules head. Rule ordering per input follows the proven discipline: boundary-outcome rows with `uni200C` explicit in the class first, three-lookahead-slot rows before two-slot rows before one-slot rows, identity rows omitted, the slot-dropped fallback last, plus ZWNJ backtrack-slot coverage guards for never-locked inputs.

Rows carry a fourth window slot, `right3`, enumerated lazily and only where live: an input the kernel's own census admits — in the pinned candidacy world, exactly the runes carrying a prefer or resolve record whose right condition chains two hops; under the simulated prospect or the shifted vote slots, every rune, because any input's third join-count term can then read the slot through its follower's replayed cascade — gets its windows split by the raw third lookahead, only where both nearer slots are letters, and only where the kernel's liveness verdict still finds the window undecided over them: some own-rune depth-3 chain unknown over (right1, right2), or some candidate shape's simulated follower choice or some follower vote's verdict moved by the third token. A window judged definite settles identically under every third token, so everywhere else the slot stays `#NA`, mirroring the established convention that no record peeks past a boundary. An enumerated window's settled left state is reachable only alongside right2 equal to that window's right3, so the worklist pins the successor's allowed-right2 set to that singleton — the same exactness plumbing the late-formation guard already rides — and the right3 options replay the right2 filters shifted one slot (formation-impossible adjacent pairs, guard-firing follower sets, the formed-ligature guard with the second slot now pinned). The fifth slot, `right4`, repeats the pattern one deeper: only an input whose chain reaches that far (again, every rune under the deep-reading modes) with letters at all three nearer slots, and only where the same verdict finds the window live over those three slots, enumerates it. Where it does enumerate, its options replay the same filters shifted once more, and the worklist pins the successor's right3 to the producing window's right4. Under those deep-reading modes with class grain asked for (`kernel_exec.DEEP_CLASSES_DEFAULT`, and `kernel_exec.class_grain` for the rule that decides it), both deep slots enumerate at class grain (issue 26): the same option lists, their letters split by the kernel's outcome fibers — the liveness verdicts themselves are untouched and the #NA biconditional keeps its exact statement over tokens — one row per (base, fiber pair) holding a content-addressed member set (`deep_classes`, `deep_class_id`), the successor pins carrying the admitted member sets instead of singletons, and `expanded_transitions` restoring the label-grain stream for everything downstream. `_assert_window_arity` ties the Transition/Rule slot count to `model.RIGHT_WINDOW_SLOTS` at import, so the chain cap and the table can only widen together.

Joint rows combine both section 6.1 flags: ranking ties broken by the structural floor between candidates differing in seam realization, and windows whose deliberately optimistic prospect diverges from the follower's actual settled choice. Both TSV artifacts are diff-stable (section 8): sorted rows, provenance pointers, deterministic labels.

`write_windows` / `read_windows` persist a built table so the font-vs-settle sweep never rebuilds what the same sources already produced: the rules, the reachable cells and the enumerated windows, stamped with `fingerprint.tables_value` over the sources the fixpoint read. The windows come back as `Window` rows — labels only, which is everything a replay consults — so the file is a fraction of the resident table and the head alone answers "which cells are reachable".
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator, Mapping

from rebuild.pipeline.model import (
    RIGHT_WINDOW_SLOTS,
    CellId,
    ResolvedSpec,
    Settled,
    parse_adjustment,
)
from rebuild.pipeline.settle import is_entry_bearing

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

DEEP_CLASS_PREFIX = "#C"


def deep_class_id(members: tuple[str, ...]) -> str:
    """Content-addressed id for a deep-slot member set: `#C` plus the first 12 hex digits of sha256 over the sorted member tuple. Identical member sets therefore share one id across contexts, across configurations, and across builds — which is what keeps cross-config artifact comparison and the ss04 row-identity pin meaningful — and the `#` prefix keeps ids outside the glyph namespace; ids are never members of BOUNDARYISH. The crate mints the ids; this function is the contract it mints them to, and `rebuild/test_table.py` checks the tokens on a kernel-built table against it."""
    digest = hashlib.sha256("\t".join(members).encode()).hexdigest()
    return f"{DEEP_CLASS_PREFIX}{digest[:12]}"


class PartitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Window:
    """The label view of one settlement window: the slots that key it, and what settles there. This is everything a replay consults, so it is all the serialized enumeration keeps and all `read_windows` hands back."""

    input_glyph: str
    left: str
    right1: str
    right2: str
    right3: str
    right4: str
    outcome: str

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (self.input_glyph, self.left, self.right1, self.right2, self.right3, self.right4)

    @property
    def is_identity(self) -> bool:
        return self.outcome == self.input_glyph


@dataclass(frozen=True)
class Transition(Window):
    """A window plus what the fixpoint alone reads: the settled cells the treaty table is folded from, the optimistic prospect the joint flag is scored against, and the provenance the dead-policy gate counts as firing evidence."""

    settled: Settled
    left_settled: Settled | None
    joint: bool
    prospect: int
    provenance: tuple[str, ...]


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
    transitions: tuple[Window, ...] = ()
    rules: tuple[Rule, ...] = ()
    identity_guard_rules: int = 0
    cited_provenance: frozenset[str] = (
        frozenset()
    )  # YAML pointers of every authored record the engine fired while tabulating this configuration (Engine.fired); the dead-policy gate's exercised-ness channel
    deep_classes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _cells: frozenset[CellId] = field(default_factory=frozenset)

    def reachable_cells(self) -> frozenset[CellId]:
        return self._cells

    def joint_rows(self) -> frozenset[int]:
        return frozenset(index for index, rule in enumerate(self.rules) if rule.joint)

    def token_members(self, token: str) -> tuple[str, ...]:
        """The member labels a deep-slot field stands for: the class map's entry for a class id, else the label itself — bare labels, boundary labels, and #NA included, so a caller can expand any right3/right4 field uniformly."""
        members = self.deep_classes.get(token)
        return members if members is not None else (token,)

    def token_representative(self, token: str) -> str:
        """The first member of a class id, else the label itself: the one concrete label a consumer pins a deep slot with. Exact rather than heuristic for rule-membership tests, because `_assert_deep_class_unions` proves every emitted look class holds a token's members all-in or all-out."""
        members = self.deep_classes.get(token)
        return members[0] if members else token

    def expanded_transitions(self) -> Iterator[Window]:
        """The label-grain row stream every fold-side consumer reads (the issue-26 expansion boundary): each class row expanded to the full member product at right3 x right4 — boundary labels and #NA pass through — with every expanded row carrying the class row's settled fields verbatim, legitimate because the fiber key makes them member-uniform (the row's `joint` is the OR over its members, so per-member flags live only inside the build's own fold input). Yields in `Window.key` order with no duplicate keys — member sets at one base are disjoint, which the kernel's own class-grain partition assertion holds it to — so a consumer that sorts label-grain rows by key today reads the identical stream; on a label-grain table this is exactly `transitions`."""
        if not self.deep_classes:
            yield from self.transitions
            return
        expanded: list[Window] = []
        for row in self.transitions:
            members3 = self.deep_classes.get(row.right3)
            members4 = self.deep_classes.get(row.right4)
            if members3 is None and members4 is None:
                expanded.append(row)
                continue
            for member3 in members3 if members3 is not None else (row.right3,):
                if members4 is None:
                    expanded.append(replace(row, right3=member3))
                else:
                    for member4 in members4:
                        expanded.append(replace(row, right3=member3, right4=member4))
        expanded.sort(key=lambda r: r.key)
        yield from expanded

    def assert_outcome_partition(self) -> None:
        """The hard build invariant (prototype follow-up 1): recompute the per-slot signature partitions and verify disjoint cover, then replay every reachable transition against the ordered rules under first-match-wins semantics. The left partition runs over the table's own rows — class tokens are ordinary signature coordinates, and the sparse-signature premise of `_signature_blocks` holds for them because ids are content-addressed, so identical token signatures imply identical member sets — while the replay runs over `expanded_transitions()`, the identical label-grain multiset a label-grain build enumerates, so first-match-wins is checked verbatim, not by analogue."""
        by_input: dict[str, dict[tuple[str, str, str, str, str], Window]] = {}
        for row in self.transitions:
            by_input.setdefault(row.input_glyph, {})[
                (row.left, row.right1, row.right2, row.right3, row.right4)
            ] = row
        for input_glyph, rows in by_input.items():
            lefts = sorted({left for left, _r1, _r2, _r3, _r4 in rows})
            signatures: dict[str, set[tuple[tuple[str, str, str, str], str]]] = {}
            for (left, r1, r2, r3, r4), row in rows.items():
                signatures.setdefault(left, set()).add(((r1, r2, r3, r4), row.outcome))
            blocks = _signature_blocks(lefts, lambda left: frozenset(signatures[left]))
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
        for row in self.expanded_transitions():
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

    def _assert_deep_class_unions(self) -> None:
        """Every emitted look3/look4 letter class holds each class row's member set all-in or all-out within the row's own context — the fold-output assertion that licenses conform's representative-membership tests as exact rather than heuristic. It holds by more than hope: within one left x r1 x r2 signature block, `_signature_blocks` equality fixes the coordinate domain as well as the outcome map (the signature is a set of ((coords), outcome) tuples), so the r3 signature is determined by the (r4-domain, outcome) map at any single (r1, r2) in the block — two fiber co-members agree on both by the fiber key, hence never straddle two blocks; the r4 direction is the same one slot over."""
        if not self.deep_classes:
            return
        rules_by_input: dict[str, list[tuple[frozenset[str] | None, ...]]] = {}
        for rule in self.rules:
            rules_by_input.setdefault(rule.input_glyph, []).append(
                tuple(
                    frozenset(slot) if slot is not None else None
                    for slot in (rule.backtrack, rule.look1, rule.look2, rule.look3, rule.look4)
                )
            )
        for row in self.transitions:
            members3 = self.deep_classes.get(row.right3)
            members4 = self.deep_classes.get(row.right4)
            if members3 is None and members4 is None:
                continue
            set3 = frozenset(members3) if members3 is not None else None
            set4 = frozenset(members4) if members4 is not None else None
            for slots in rules_by_input.get(row.input_glyph, ()):
                backtrack, look1, look2, look3, look4 = slots
                if backtrack is not None and row.left not in backtrack:
                    continue
                if look1 is not None and row.right1 not in look1:
                    continue
                if look2 is not None and row.right2 not in look2:
                    continue
                if set3 is not None and look3 is not None:
                    inside = set3 & look3
                    if inside and inside != set3:
                        raise PartitionError(
                            f"{row.input_glyph}: an emitted look3 class splits deep class {row.right3} at {row.key}: {sorted(inside)} of {sorted(set3)}"
                        )
                if set4 is not None and look4 is not None:
                    if look3 is None or frozenset(set3 if set3 is not None else {row.right3}) & look3:
                        inside4 = set4 & look4
                        if inside4 and inside4 != set4:
                            raise PartitionError(
                                f"{row.input_glyph}: an emitted look4 class splits deep class {row.right4} at {row.key}: {sorted(inside4)} of {sorted(set4)}"
                            )

    def assert_e_stranded(self) -> None:
        """Every committed exit in the table has at least one transition settling the follower — the fixpoint enqueues every successor and the kernel raises E-STRANDED on a violation, so this re-walk is a belt-and-suspenders assertion. It reads the seam each row committed, so it belongs to the build: a table read back through `read_windows` carries the label view, and was proved before it was written."""
        keys = {(row.left, row.input_glyph) for row in self.transitions}
        for row in self.transitions:
            if not isinstance(row, Transition):
                raise PartitionError(
                    "the E-STRANDED re-walk needs the enumerated rows, not a serialized window"
                )
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


WINDOWS_FORMAT = "ams-m1-windows/2"
WINDOWS_COLUMNS = ("input", "left", "lookahead1", "lookahead2", "lookahead3", "lookahead4", "outcome")


def windows_path(out_dir: Path, config: str) -> Path:
    return Path(out_dir) / f"windows-{config}.tsv.gz"


def _cell_key(cell: CellId) -> tuple:
    return (cell.rune, cell.stance, cell.entry or "", cell.exit or "", cell.adjustments)


def _rule_row(rule: Rule) -> list:
    slots = (rule.backtrack, rule.look1, rule.look2, rule.look3, rule.look4)
    return [
        rule.input_glyph,
        *(list(slot) if slot is not None else None for slot in slots),
        rule.outcome,
        list(rule.provenance),
        rule.joint,
    ]


def _rule_of(row: list) -> Rule:
    input_glyph, *slots, outcome, provenance, joint = row
    backtrack, look1, look2, look3, look4 = (tuple(slot) if slot is not None else None for slot in slots)
    return Rule(input_glyph, backtrack, look1, look2, look3, look4, outcome, tuple(provenance), joint)


def write_windows(decision: DecisionTable, path: Path, inputs: str) -> None:
    """Serialize one configuration's decision table beside the build's other artifacts: a head line carrying the fingerprint of the sources it was built from, the reachable cells and the ordered rules, then one row per enumerated window. The fixpoint costs tens of seconds per configuration and the font-vs-settle sweep needs exactly this much of it, so the sweep loads this instead of rebuilding what the same inputs already produced. Diff-stable like the TSVs beside it: sorted cells, rules in emission order, and a zeroed gzip stamp, so two builds of one table are byte-identical."""
    head = {
        "config": decision.config,
        "inputs": inputs,
        "identity_guard_rules": decision.identity_guard_rules,
        "cited_provenance": sorted(decision.cited_provenance),
        "cells": [
            [cell.rune, cell.stance, cell.entry, cell.exit, list(cell.adjustments)]
            for cell in sorted(decision.reachable_cells(), key=_cell_key)
        ],
        "deep_classes": [[token, list(members)] for token, members in sorted(decision.deep_classes.items())],
        "rules": [_rule_row(rule) for rule in decision.rules],
    }
    body = "".join(
        "\t".join((r.input_glyph, r.left, r.right1, r.right2, r.right3, r.right4, r.outcome)) + "\n"
        for r in decision.transitions
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
        handle.write(f"# {WINDOWS_FORMAT}\t{json.dumps(head, separators=(',', ':'))}\n".encode())
        handle.write(("\t".join(WINDOWS_COLUMNS) + "\n").encode())
        handle.write(body.encode())


def read_windows(path: Path, windows: bool = True) -> tuple[str, DecisionTable]:
    """The `write_windows` inverse: the fingerprint of the sources the table was built from, and the table itself with `Window` rows for transitions. `windows=False` stops after the head, so a caller that wants only the rules and the reachable cells pays for one line — gzip streams, so the enumeration is never decompressed. Raises OSError when the file is absent and ValueError when it is not an enumeration this build understands; a caller deciding whether to trust the artifact compares the returned fingerprint itself."""
    with gzip.open(path, "rt") as handle:
        marker, _, payload = handle.readline().rstrip("\n").partition("\t")
        if marker != f"# {WINDOWS_FORMAT}":
            raise ValueError(f"{path}: not a {WINDOWS_FORMAT} enumeration")
        head = json.loads(payload)
        rows: tuple[Window, ...] = ()
        if windows:
            if tuple(handle.readline().rstrip("\n").split("\t")) != WINDOWS_COLUMNS:
                raise ValueError(f"{path}: window columns are not {WINDOWS_COLUMNS}")
            intern = {}
            rows = tuple(
                Window(*(intern.setdefault(label, label) for label in line.rstrip("\n").split("\t")))
                for line in handle
            )
    decision = DecisionTable(
        config=head["config"],
        transitions=rows,
        rules=tuple(_rule_of(row) for row in head["rules"]),
        identity_guard_rules=head["identity_guard_rules"],
        cited_provenance=frozenset(head["cited_provenance"]),
        deep_classes={token: tuple(members) for token, members in head["deep_classes"]},
        _cells=frozenset(
            CellId(rune, stance, entry, exit_, tuple(adjustments))
            for rune, stance, entry, exit_, adjustments in head["cells"]
        ),
    )
    return head["inputs"], decision


def windows_digest(decision: DecisionTable) -> str:
    """Content hash of one configuration's settlement rows: the ordered rules, the deep-class map, and the enumerated windows, in exactly the forms `write_windows` serializes, but without the inputs stamp. The stamp moves on any hashed source edit; this digest moves only when settlement itself does, which is what makes it the answer to "did the ink-only rune edit change any window at all". The class map is hashed between the rules and the rows, so a moved map moves the digest — a token's member set is part of what a row says."""
    digest = hashlib.sha256()
    digest.update(decision.config.encode())
    digest.update(json.dumps([_rule_row(rule) for rule in decision.rules], separators=(",", ":")).encode())
    digest.update(
        json.dumps(
            [[token, list(members)] for token, members in sorted(decision.deep_classes.items())],
            separators=(",", ":"),
        ).encode()
    )
    for row in decision.transitions:
        digest.update(
            "\t".join(
                (row.input_glyph, row.left, row.right1, row.right2, row.right3, row.right4, row.outcome)
            ).encode()
        )
        digest.update(b"\n")
    return digest.hexdigest()


def table_digest(decision: DecisionTable, treaty: TreatyTable) -> str:
    """The canonical differential digest, at full contract grain: one scalar saying whether two builds of one configuration agree on the ordered rules with their provenance and joint flags, every enumerated window row as stored, the treaty rows, the reachable cells, the cited provenance and the identity-guard count. That is the whole observable product of one configuration's build, so a port, a lever or a refactor that claims to change nothing is checked against this and nothing narrower. `windows_digest` stays the narrower row-level check — it omits the treaty, the cells, the provenance and the guards on purpose, so that it answers only whether the settlement rows themselves moved. The deep-class map needs no section of its own here: class ids are content-addressed over their member sets, so a moved map moves the row fields that cite it."""
    h = hashlib.sha256()
    h.update(f"config\t{decision.config}\n".encode())
    for rule in decision.rules:
        h.update(
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
            ).encode()
            + b"\n"
        )
    h.update(b"--windows--\n")
    for row in decision.transitions:
        h.update(
            "\t".join(
                (row.input_glyph, row.left, row.right1, row.right2, row.right3, row.right4, row.outcome)
            ).encode()
            + b"\n"
        )
    h.update(b"--treaty--\n")
    for treaty_row in treaty.rows:
        h.update(
            "\t".join(
                (
                    treaty_row.left,
                    treaty_row.right,
                    treaty_row.junction,
                    str(treaty_row.extension),
                    str(treaty_row.kern),
                )
            ).encode()
            + b"\n"
        )
    h.update(b"--cells--\n")
    for cell in sorted(decision.reachable_cells(), key=_cell_key):
        h.update(f"{cell.rune}\t{cell.stance}\t{cell.entry}\t{cell.exit}\t{cell.adjustments}\n".encode())
    h.update(b"--provenance--\n")
    for pointer in sorted(decision.cited_provenance):
        h.update(pointer.encode() + b"\n")
    h.update(f"--guards--\t{decision.identity_guard_rules}\n".encode())
    return h.hexdigest()


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


@dataclass(frozen=True)
class FixpointProduct:
    """Everything one configuration's fixpoint produces and nothing it consulted: the key-sorted enriched transition stream, the deep-class map its class tokens resolve through, the provenance pointers the engine fired while tabulating, and the cells the stream settles into. `joint` on these rows is the trace's own `joint_floor` alone — the prospect-divergence pass runs in `assemble_tables`, over the expanded stream — and `cells` is stated at class grain, which equals the expanded set because a class row's members share its settled fields. This value is the kernel boundary — `kernel_exec.enumerate_transitions` is where one comes from — and `assemble_tables` reads it and nothing else the engine touched, so a product parsed back from a file folds into the identical tables."""

    config: str
    transitions: tuple[Transition, ...]
    deep_classes: Mapping[str, tuple[str, ...]]
    cited_provenance: frozenset[str]
    cells: frozenset[CellId]


def assemble_tables(spec: ResolvedSpec, product: FixpointProduct) -> tuple[DecisionTable, TreatyTable]:
    """The Python half of the build, folding one fixpoint product into its two tables: the class-grain stream expanded to label grain, the prospect-divergence flag pass over that expansion with every fired flag backfilled onto the class row covering it, the per-input rule fold, and the treaty fold. It reads the product and one verdict from `spec` (`is_entry_bearing`, for the ZWNJ backtrack-slot guards) — nothing else the engine saw — so a product assembles into exactly the tables the enumeration that produced it would have, whether it came straight off the crate or was parsed back out of a stream on disk. The reachable cells are the product's; re-deriving them from the fold rows is one cheap loud check that the two grains still agree."""
    enumerated = list(product.transitions)
    config = product.config
    deep_classes_map = product.deep_classes
    if deep_classes_map:
        expanded_pairs: list[tuple[int, Transition]] = []
        for index, row in enumerate(enumerated):
            for member3 in deep_classes_map.get(row.right3, (row.right3,)):
                for member4 in deep_classes_map.get(row.right4, (row.right4,)):
                    expanded_pairs.append(
                        (
                            index,
                            (
                                row
                                if member3 == row.right3 and member4 == row.right4
                                else replace(row, right3=member3, right4=member4)
                            ),
                        )
                    )
        expanded_pairs.sort(key=lambda pair: pair[1].key)
        fold_rows = _flag_prospect_joints([pair[1] for pair in expanded_pairs])
        class_joint = [row.joint for row in enumerated]
        for (index, _row), flagged in zip(expanded_pairs, fold_rows):
            if flagged.joint:
                class_joint[index] = True
        rows = [
            row if row.joint == class_joint[index] else replace(row, joint=True)
            for index, row in enumerate(enumerated)
        ]
    else:
        rows = _flag_prospect_joints(enumerated)
        fold_rows = rows

    rules: list[Rule] = []
    identity_guards = 0
    by_input: dict[str, dict[tuple[str, str, str, str, str], Transition]] = {}
    for row in fold_rows:
        by_input.setdefault(row.input_glyph, {})[
            (row.left, row.right1, row.right2, row.right3, row.right4)
        ] = row
    for input_glyph in sorted(by_input):
        never_locked = not is_entry_bearing(spec, input_glyph.split(".")[0])
        input_rules, guards = _rules_for_input(input_glyph, by_input[input_glyph], never_locked)
        rules.extend(input_rules)
        identity_guards += guards

    cells = frozenset(row.settled.cell for row in fold_rows)
    if cells != product.cells:
        raise PartitionError(
            f"the product's reachable cells disagree with the fold rows': {sorted(_cell_key(cell) for cell in cells ^ product.cells)}"
        )
    decision = DecisionTable(
        config=config,
        transitions=tuple(rows),
        rules=tuple(rules),
        identity_guard_rules=identity_guards,
        cited_provenance=product.cited_provenance,
        deep_classes=deep_classes_map,
        _cells=product.cells,
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
            for row in fold_rows
            if row.left_settled is not None
        },
        key=lambda r: (r.left, r.right, r.junction),
    )
    return decision, TreatyTable(config=config, rows=tuple(treaty_rows))


def prospect_successor_index(rows: list[Transition]) -> dict[tuple[str, str, str], list[Transition]]:
    """The (left label, input glyph, right1 label) index `prospect_successors` walks — built once per table because the flag pass and the divergence inventory both scan every row against it."""
    successors: dict[tuple[str, str, str], list[Transition]] = {}
    for row in rows:
        successors.setdefault((row.left, row.input_glyph, row.right1), []).append(row)
    return successors


def prospect_successors(index: dict[tuple[str, str, str], list[Transition]], row: Transition):
    """The successor transitions a row's optimistic prospect is scored against (design section 6.1 step 4.2): the follower's windows whose settled left is this row's outcome, whose input is this row's right1, whose right1 is this row's right2 (the index key, so the scan never touches a window the first three slots already rule out), and whose deeper slots agree wherever this row enumerated them. Yields nothing when either lookahead is boundaryish — the prospect term is defined only over letter-letter windows. Shared by `_flag_prospect_joints` and `rebuild.tools.prospect_divergence` so the flag and the inventory can never disagree about what was compared."""
    if row.right1 in BOUNDARYISH or row.right2 in BOUNDARYISH:
        return
    for successor in index.get((row.outcome, row.right1, row.right2), ()):
        if row.right3 != NA_LABEL and successor.right2 != row.right3:
            continue
        if row.right4 != NA_LABEL and successor.right3 != row.right4:
            continue
        yield successor


def _flag_prospect_joints(rows: list[Transition]) -> list[Transition]:
    """Compare every row's optimistic prospect against the follower's actual settled choice and flag divergent rows joint (design section 6.1 step 4.2)."""
    successors = prospect_successor_index(rows)
    flagged: list[Transition] = []
    for row in rows:
        joint = row.joint
        if not joint:
            for successor in prospect_successors(successors, row):
                realized = 1 if successor.settled.seam is not None else 0
                if realized != row.prospect:
                    joint = True
                    break
        flagged.append(row if joint == row.joint else replace(row, joint=joint))
    return flagged


def _signature_blocks(values, signature_of) -> list[tuple[str, ...]]:
    """Callers pass signatures built from present rows only, never the full other-slot label product: every value's product is the same per grouping, so identical present-maps imply identical missing-key sets, and grouping by the sparse signature yields exactly the partition the (missing -> None) product signature would — at O(rows) instead of O(label product), which is what keeps folding from regrowing quartically as depth-3/4 windows are authored. Class tokens are sound signature coordinates for the same reason the premise needs: ids are content-addressed by member set, so identical token signatures imply identical member sets — never two spellings of one set."""
    groups: dict[frozenset, list[str]] = {}
    for value in values:
        groups.setdefault(signature_of(value), []).append(value)
    return sorted(tuple(sorted(members)) for members in groups.values())


def _rules_for_input(
    input_glyph: str, rows: dict[tuple[str, str, str, str, str], Transition], never_locked: bool
) -> tuple[list[Rule], int]:
    lefts = sorted({left for left, _r1, _r2, _r3, _r4 in rows})

    def outcome(left: str, r1: str, r2: str, r3: str, r4: str) -> str | None:
        row = rows.get((left, r1, r2, r3, r4))
        return row.outcome if row is not None else None

    left_signatures: dict[str, set[tuple[tuple[str, str, str, str], str]]] = {}
    for (left, r1, r2, r3, r4), row in rows.items():
        left_signatures.setdefault(left, set()).add(((r1, r2, r3, r4), row.outcome))
    left_blocks = _signature_blocks(lefts, lambda left: frozenset(left_signatures[left]))
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

        r1_signatures: dict[str, set[tuple[tuple[str, str, str], str]]] = {}
        for (r1, r2, r3, r4), row in group_rows.items():
            r1_signatures.setdefault(r1, set()).add(((r2, r3, r4), row.outcome))
        r1_blocks = _signature_blocks(group_r1s, lambda r1: frozenset(r1_signatures[r1]))

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
            r1_members = set(r1_block)
            r2_signatures: dict[str, set[tuple[tuple[str, str, str], str]]] = {}
            for (r1, r2, r3, r4), row in group_rows.items():
                if r1 in r1_members:
                    r2_signatures.setdefault(r2, set()).add(((r1, r3, r4), row.outcome))
            r2_blocks = _signature_blocks(block_r2s, lambda r2: frozenset(r2_signatures[r2]))
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
                r2_members = set(r2_block)
                r3_signatures: dict[str, set[tuple[tuple[str, str, str], str]]] = {}
                for (r1, r2, r3, r4), row in group_rows.items():
                    if r1 in r1_members and r2 in r2_members:
                        r3_signatures.setdefault(r3, set()).add(((r1, r2, r4), row.outcome))
                r3_blocks = _signature_blocks(block_r3s, lambda r3: frozenset(r3_signatures[r3]))
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
                    r3_members = set(r3_block)
                    r4_signatures: dict[str, set[tuple[tuple[str, str, str], str]]] = {}
                    for (r1, r2, r3, r4), row in group_rows.items():
                        if r1 in r1_members and r2 in r2_members and r3 in r3_members:
                            r4_signatures.setdefault(r4, set()).add(((r1, r2, r3), row.outcome))
                    r4_blocks = _signature_blocks(block_r4s, lambda r4: frozenset(r4_signatures[r4]))
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
