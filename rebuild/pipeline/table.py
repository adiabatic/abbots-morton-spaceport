"""Decision-table and treaty-table builders (M1-PLAN section 5, Group 2), promoted from prototype/table.py per the Recon B promotion map.

`build_tables(spec, features)` tabulates the settlement kernel over every (settled-left state, rune, raw-right-1, raw-right-2) window reachable under settlement for one feature configuration, by fixpoint over reachable left states rather than string enumeration, so the table is exact. Windows that formation makes impossible are excluded — but a ligature pair survives unformed exactly where the section 5.7 late-formation guard fires, so pair windows are enumerated under precisely the guard-firing follower contexts (`_survivable_formation_windows`): the lead's window is admitted per guard-firing right2, and the trail's window inherits the matching allowed-right2 set through the worklist, keeping the fixpoint exact. The mirror facet holds for formed-ligature tokens at any slot: a ligature input's window, and any window with a ligature at right1, is admitted only where that ligature's own guard does NOT fire over the raw tokens its post-formation neighbors stand for (`liga_formed_before`), existentially over the beyond-window slot. ZWNJ-locked entry-bearing inputs enumerate under the chokepoint twin's glyph name (`model.locked_glyph_name`, the `<raw>.noentry` shape the emitter's chokepoint actually produces), locked before settlement — which keeps each plain input's boundary-left outcomes in a single block, exactly as the prototype encoded it.

Outcome-partition compression is DFA-style per input and per slot: two fillers land in one class iff their full outcome signatures over the other slots are identical. `assert_outcome_partition` re-derives the partitions and replays every reachable transition against the ordered rules under first-match-wins semantics — the hard build invariant of prototype follow-up 1. The fold, the joint-flag pass, the treaty fold, the replay, and every serialized-rules consumer read the expanded label-grain row stream (`DecisionTable.expanded_transitions`): a class-grain enumeration expands each row to its full member product before anything downstream runs, so those consumers are byte-identical to a label-grain build by construction, and `Rule` objects carry label vocabulary only — no class id ever reaches `_rules_for_input`, `write_tsv`, or a serialized rules head. Rule ordering per input follows the proven discipline: boundary-outcome rows with `uni200C` explicit in the class first, three-lookahead-slot rows before two-slot rows before one-slot rows, identity rows omitted, the slot-dropped fallback last, plus ZWNJ backtrack-slot coverage guards for never-locked inputs.

Rows carry a fourth window slot, `right3`, enumerated lazily and only where live: an input admitted by `third_slot_inputs` (the depth-3 chain census `depth3_inputs` under the candidacy-grain prospect; every rune under the simulated prospect, where any input's third join-count term can read the slot through its follower's replayed cascade) gets its windows split by the raw third lookahead, only where both nearer slots are letters, and only where `third_slot_filter` judges the window live — some own-rune depth-3 prefer chain still unknown over (right1, right2), or, flag-on, some candidate shape's simulated follower choice moved by the third token (`_ProspectLiveness`) — a window judged definite settles identically under every third token, so everywhere else the slot stays `#NA`, mirroring the established convention that no record peeks past a boundary. An enumerated window's settled left state is reachable only alongside right2 equal to that window's right3, so the worklist pins the successor's allowed-right2 set to that singleton — the same exactness plumbing the late-formation guard already rides — and the right3 options replay the right2 filters shifted one slot (formation-impossible adjacent pairs, guard-firing follower sets, `liga_formed_before` with the second slot now pinned). The fifth slot, `right4`, repeats the pattern one deeper: only a `fourth_slot_inputs` input with letters at all three nearer slots, and only where `fourth_slot_filter` finds the window live over those three slots, enumerates it. Where it does enumerate, its options replay the same filters shifted once more, and the worklist pins the successor's right3 to the producing window's right4. Under `_deep_world` with `DEEP_CLASSES_DEFAULT` on, both deep slots enumerate at class grain (issue 26): the same option lists, their letters split by `_DeepFiberDeriver`'s outcome fibers — the filters themselves are untouched and the #NA biconditional keeps its exact statement over tokens — one row per (base, fiber pair) holding a content-addressed member set (`deep_classes`, `deep_class_id`), the successor pins carrying the admitted member sets instead of singletons, and `expanded_transitions` restoring the label-grain stream for everything downstream; `_assert_deep_slot_partition` and the per-build echo check are the standing guards. `_assert_window_arity` ties the Transition/Rule slot count to `model.RIGHT_WINDOW_SLOTS` at import, so the chain cap and the table can only widen together.

Joint rows combine both section 6.1 flags: ranking ties broken by the structural floor between candidates differing in seam realization, and windows whose deliberately optimistic prospect diverges from the follower's actual settled choice. Both TSV artifacts are diff-stable (section 8): sorted rows, provenance pointers, deterministic labels.

`build_tables` is a composition across the kernel boundary: `enumerate_transitions` runs the fixpoint and hands back a `FixpointProduct` — the key-sorted enriched transition stream, the deep-class map, the fired provenance, the reachable cells — and `assemble_tables` folds that product, plus one entry-bearing verdict from the spec and nothing else the engine saw, into the two tables. The seam sits exactly where the engine stops being consulted, so the product is the value a kernel port is measured at, and everything downstream of it stays Python. Only the class-grain assertions straddle it, and they run in `build_tables` because they replay the assembled table against enumeration scaffolding the product deliberately drops.

`write_windows` / `read_windows` persist a built table so the font-vs-settle sweep never rebuilds what the same sources already produced: the rules, the reachable cells and the enumerated windows, stamped with `fingerprint.tables_value` over the sources the fixpoint read. The windows come back as `Window` rows — labels only, which is everything a replay consults — so the file is a fraction of the resident table and the head alone answers "which cells are reachable".
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterator, Mapping

from rebuild.pipeline import settle as settle_module
from rebuild.pipeline.model import (
    RIGHT_WINDOW_SLOTS,
    CellId,
    Height,
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
    UNKNOWN,
    ZWNJ,
    Candidate,
    Engine,
    LeftContext,
    RightToken,
    SettleError,
    cell_label,
    is_entry_bearing,
)
from rebuild.pipeline.specificity import EAmbiguousError, EIncomparableError

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

# The issue-26 flag, default on wherever `_deep_world` is true: deep window slots enumerate at class grain (one row per outcome fiber, expanded back to labels for every fold-side consumer). Same plumbing contract as settle's semantics flags: module-level, consulted at build time, AMS_DEEP_CLASSES=0 is the label-grain comparison state; in the pinned candidacy world there is no `_ProspectLiveness` instance and hence no fiber source, so enumeration there stays label-grain regardless.
DEEP_CLASSES_DEFAULT = os.environ.get("AMS_DEEP_CLASSES", "1") != "0"
DEEP_CLASS_PREFIX = "#C"


def deep_class_id(members: tuple[str, ...]) -> str:
    """Content-addressed id for a deep-slot member set: `#C` plus the first 12 hex digits of sha256 over the sorted member tuple. Identical member sets therefore share one id across contexts, across configurations, and across builds — which is what keeps cross-config artifact comparison and the ss04 row-identity pin meaningful — and the `#` prefix keeps ids outside the glyph namespace; ids are never members of BOUNDARYISH."""
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
        """The label-grain row stream every fold-side consumer reads (the issue-26 expansion boundary): each class row expanded to the full member product at right3 x right4 — boundary labels and #NA pass through — with every expanded row carrying the class row's settled fields verbatim, legitimate because the fiber key makes them member-uniform (the row's `joint` is the OR over its members, so per-member flags live only inside the build's own fold input). Yields in `Window.key` order with no duplicate keys — member sets at one base are disjoint (`_assert_deep_slot_partition`) — so a consumer that sorts label-grain rows by key today reads the identical stream; on a label-grain table this is exactly `transitions`."""
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
    """Content hash of everything a witness hunt reads from one configuration's table: the ordered rules, the deep-class map, and the enumerated windows, in exactly the forms `write_windows` serializes, but without the inputs stamp. The stamp moves on any hashed source edit; the table moves only when settlement itself does — so a cache keyed on this digest survives the ink-only rune edits that dominate glyph work, and staleness the digest cannot see (a rename map or deep-slot filter moving while the raw windows stay put) is safe by construction, because a recorded witness is only ever tried first and re-verified, never trusted. The class map is hashed between the rules and the rows: a moved map moves the digest and cold-starts the witness caches, which is correct — a token's member set is part of what a recorded window witness realized."""
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
    """The canonical differential digest, at full contract grain: one scalar saying whether two builds of one configuration agree on the ordered rules with their provenance and joint flags, every enumerated window row as stored, the treaty rows, the reachable cells, the cited provenance and the identity-guard count. That is the whole observable product of `build_tables`, so a port, a lever or a refactor that claims to change nothing is checked against this and nothing narrower. `windows_digest` stays the narrower row-level check the witness caches key on — it omits the treaty, the cells, the provenance and the guards on purpose, because a witness only ever replays a window. The deep-class map needs no section of its own here: class ids are content-addressed over their member sets, so a moved map moves the row fields that cite it.

    `bench-the-rebuild/levers/m1_all_configs.py` carries a byte-for-byte copy for the comparison trees it measures, which predate this function; keep the two algorithms in lockstep, and `rebuild/test_table_digest.py` proves they agree.
    """
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


def right_chain_reach(cond) -> int:
    """How many raw slots past its own a right condition's then: chains read: a then: hop advances one slot, and an except: entry tests its parent's slot, so its hops count from there. Mirrors spec_load's raw-dict lint over the resolved Condition."""
    reach = 0
    if cond.then is not None:
        reach = max(reach, 1 + right_chain_reach(cond.then))
    for ex in cond.except_:
        reach = max(reach, right_chain_reach(ex))
    return reach


def depth3_inputs(spec: ResolvedSpec) -> frozenset[str]:
    """The rune names whose windows the raw third lookahead can decide: only an own-rune prefer or resolve record ever receives the real right3 (settle's `_prefer_favors` / `_apply_resolution` discipline), so exactly the runes carrying such a record whose right condition chains two hops."""
    return _deep_inputs(spec, 2)


def depth4_inputs(spec: ResolvedSpec) -> frozenset[str]:
    """The rune names whose windows the raw fourth lookahead can decide — a prefer or resolve whose right condition chains three hops. Always a subset of `depth3_inputs`; both gates apply, each opening its own slot."""
    return _deep_inputs(spec, 3)


def _deep_inputs(spec: ResolvedSpec, reach: int) -> frozenset[str]:
    out = set()
    for name, rune in spec.runes.items():
        for record in tuple(rune.policy.prefer) + tuple(rune.policy.resolve):
            right = record.when.right
            if right is not None and right_chain_reach(right) >= reach:
                out.add(name)
    return frozenset(out)


def _deep_world(engine: Engine | None) -> bool:
    """Whether the process's engines read deep slots beyond the own-rune chain arm: the simulated prospect replays the follower's cascade over them (issue 28 stage 2), and shifted vote slots hand them to the seam's follower votes (stage 4b). Either one makes any input's window deep-slot-decidable, so the pre-gates widen to every rune and the per-window probes do the pruning. `engine=None` follows the module defaults the way Engine construction does, so the fallback paths that build their own filters stay consistent with the process's engines."""
    if engine is not None:
        return engine.simulated_prospect or engine.vote_slots
    return settle_module.SIMULATED_PROSPECT_DEFAULT or settle_module.VOTE_SLOTS_DEFAULT


def third_slot_inputs(spec: ResolvedSpec, engine: Engine | None = None) -> frozenset[str]:
    """The inputs whose windows can carry a live third slot — the pre-gate `enumerate_transitions` and conform's window replay share ahead of the per-window `third_slot_filter` verdict. In the pinned candidacy world only an own-rune depth-3 prefer chain ever reads the slot, so this is exactly `depth3_inputs`; with the simulated prospect or shifted vote slots on (`_deep_world`), the raw third token can decide any input's window, so every rune is admitted and the per-window probe does all the pruning."""
    return frozenset(spec.runes) if _deep_world(engine) else depth3_inputs(spec)


def fourth_slot_inputs(spec: ResolvedSpec, engine: Engine | None = None) -> frozenset[str]:
    """`third_slot_inputs` one slot deeper: the depth-4 chain census in the pinned world, every rune under `_deep_world`."""
    return frozenset(spec.runes) if _deep_world(engine) else depth4_inputs(spec)


class _ProspectLiveness:
    """The simulated-prospect arm of the deep-slot filters (issue 28 stage 2): whether a raw deep token can move the settled outcome of some reachable window at (input, right1, right2). Two value-level stages, because every cheaper grain fails in a measured way — consultation-level tracking over-opens catastrophically (the recursion consults beyond-window slots almost everywhere), and stopping at follower-prospect variance still over-opens 15-fold (measured on the real spec: 1,543 of the consulted triples carry a token-movable prospect but only 103 ever move a seat outcome), enough to push the emitted settlement lookup through the budget gate's headroom floor. Stage one is the cheap prefilter: for each (stance, seam) shape the input can commit — the virtual left's entry is never read, so entry states collapse — the follower's simulated prospect is evaluated per concrete token and compared against the EDGE the table bakes for a dead slot; no variance anywhere means no channel into the seat's ranking (deep tokens reach the flag-on kernel only through prospect values and own-rune chains, and the chain arm runs before this probe), so the slot is definitely dead. Stage two, only where stage one fires, probes at outcome grain: the seat's own transition is replayed per token over the collapsed left-classes — every (family, stance, seam) virtual left plus the four boundary kinds, collapsed by the input-frame signature (committed seam, left kind, and the verdict vector of the input's own left-reading conditions: entry-row from-scopes and refuse/prefer/resolve/unlock left conditions — extend and contract records shape adjustments only, and neither the extension nor the left cell's entry interacts with a deep token, so reachable settled lefts are covered by the enumerated shapes) — and the slot is live only if some class's settled cell varies. Left-classes the fixpoint can never reach raise E-STRANDED in the replay and are skipped; a prefer conflict raising E-INCOMPARABLE/E-AMBIGUOUS marks the slot live so the enumeration surfaces it properly. The third-slot probes also compare each token's unknown-fourth evaluation against its EDGE-fourth one, and `third_live` additionally ORs in `fourth_live` over every concrete letter third — a live fourth slot hanging off an unenumerated third would otherwise never be consulted, and the EDGE/UNKNOWN-fourth comparisons alone cannot see a seat that moves only under a specific (third, fourth) letter pair, because unknown-optimism bottoms the recursion identically for both. With shifted vote slots on (stage 4b) stage one grows a vote arm beside the prospect arm: `_vote_class_live` probes `_prefer_favors`' vote branch itself per deep token, because a vote reads the deep slots both through its record's shifted when: chain and through the follower-cell enumeration the vote runs over the shifted window; a same-family seam is skipped (the own branch shadows the vote there and the chain arm models it), and stage two prunes vote-verdict variance that never moves the seat. Verdict caches key on the probed window and instances cache per engine (`_liveness_probe`), so both filters and every consultation share one memo, and the conform gate remains the standing alarm for any residual under-opening. Under class grain (issue 26) this probe machinery is also the fiber source: `_DeepFiberDeriver` reads `_seat_left_classes` and `_probe_tokens` to compute the outcome-probed fiber key over every left class and a bounded coordinate set — the {EDGE, UNKNOWN} pair where the fourth slot is dead, the full probe alphabet plus UNKNOWN exactly where `fourth_slot_matters` is true, which is what absorbs the joint34 counterexample below at fiber grain — while the liveness verdicts themselves keep their exact code and are never redefined as fiber projections (a chain-arm-live context derives fibers too, whether or not the probe's own verdict was consulted)."""

    def __init__(self, spec: ResolvedSpec, engine: Engine):
        self.spec = spec
        self.engine = engine
        self._conds: dict[str, tuple] = {}
        self._votes: dict[str, tuple] = {}
        self._shapes: dict[str, tuple[tuple[str, Height | None], ...]] = {}
        self._sigs: dict[tuple[str, str, str, Height | None], tuple] = {}
        self._third: dict[tuple, bool] = {}
        self._fourth: dict[tuple, bool] = {}
        self._tokens: list[RightToken] | None = None
        self._left_classes: dict[str, tuple[LeftContext, ...]] = {}

    def _probe_tokens(self) -> list[RightToken]:
        if self._tokens is None:
            self._tokens = [EDGE, SPACE, ZWNJ, NAMER_DOT] + [
                RightToken("letter", name) for name in sorted(self.spec.runes)
            ]
        return self._tokens

    def _input_shapes(self, family: str) -> tuple[tuple[str, Height | None], ...]:
        shapes = self._shapes.get(family)
        if shapes is None:
            out: list[tuple[str, Height | None]] = []
            for stance_name, stance in self.spec.runes[family].stances.items():
                seams: list[Height | None] = [] if "exit" in stance.surface.require else [None]
                seams.extend(stance.surface.exits)
                seams.extend(
                    unlock.exit
                    for unlock in stance.surface.unlocks
                    if unlock.exit is not None and unlock.exit not in stance.surface.exits
                )
                out.extend((stance_name, seam) for seam in dict.fromkeys(seams))
            shapes = tuple(out)
            self._shapes[family] = shapes
        return shapes

    def _left_conditions(self, follower: str) -> tuple:
        conds = self._conds.get(follower)
        if conds is None:
            rune = self.spec.runes[follower]
            gathered = []
            for stance in rune.stances.values():
                for row in stance.surface.entries.values():
                    gathered.extend(row.scope)
                for unlock in stance.surface.unlocks:
                    if unlock.when is not None and unlock.when.left is not None:
                        gathered.append(unlock.when.left)
            for record in tuple(rune.policy.refuse) + tuple(rune.policy.prefer) + tuple(rune.policy.resolve):
                if record.when is not None and record.when.left is not None:
                    gathered.append(record.when.left)
            conds = tuple(gathered)
            self._conds[follower] = conds
        return conds

    def _virtual(self, family: str, stance: str, seam: Height | None) -> LeftContext:
        cell = CellId(rune=family, stance=stance, entry=None, exit=seam, adjustments=())
        return LeftContext("letter", Settled(cell=cell, seam=seam, extension=0))

    def _signature(self, follower: str, family: str, stance: str, seam: Height | None) -> tuple:
        key = (follower, family, stance, seam)
        sig = self._sigs.get(key)
        if sig is None:
            virtual = self._virtual(family, stance, seam)
            sig = (
                seam,
                tuple(
                    self.engine.cond_matches_left(follower, cond, virtual, seam)
                    for cond in self._left_conditions(follower)
                ),
            )
            self._sigs[key] = sig
        return sig

    def third_live(self, family: str, right1: str, right2: str) -> bool:
        r1tok, r2tok = RightToken("letter", right1), RightToken("letter", right2)
        stage_one = (
            self.engine.simulated_prospect
            and self._prospect_varies_third(family, right1, right2, r1tok, r2tok)
        ) or (self.engine.vote_slots and self._vote_varies_third(family, right1, right2, r1tok, r2tok))
        if stage_one:
            key = ("seat3", family, right1, right2)
            verdict = self._third.get(key)
            if verdict is None:
                verdict = self._seat_varies(family, r1tok, r2tok, None)
                self._third[key] = verdict
            if verdict:
                return True
        key = ("joint34", family, right1, right2)
        verdict = self._third.get(key)
        if verdict is None:
            # A live fourth slot at a concrete third must force the third open, or the enumeration never consults it: the per-token probes above compare only EDGE- and UNKNOWN-fourths, and unknown-optimism bottoms the recursion identically for both, so a seat whose outcome moves only under a specific (third, fourth) letter pair — ·See·No·No·Roe·No·Oy, where the fourth-slot ·Oy flips the seat through two simulation levels while every EDGE/UNKNOWN-fourth agrees — reads dead at this grain alone. Witness-coverage in rebuild/test_rule_witnesses.py is the alarm that caught the hang.
            verdict = any(
                self.fourth_live(family, right1, right2, token.letter)
                for token in self._probe_tokens()
                if token.kind == "letter"
            )
            self._third[key] = verdict
        return verdict

    def _prospect_varies_third(
        self, family: str, right1: str, right2: str, r1tok: RightToken, r2tok: RightToken
    ) -> bool:
        for stance, seam in self._input_shapes(family):
            key = (right1, right2, self._signature(right1, family, stance, seam))
            verdict = self._third.get(key)
            if verdict is None:
                verdict = self._third_class_live(family, stance, seam, r1tok, r2tok)
                self._third[key] = verdict
            if verdict:
                return True
        return False

    def _third_class_live(
        self, family: str, stance: str, seam: Height | None, r1tok: RightToken, r2tok: RightToken
    ) -> bool:
        candidate = Candidate(stance, None, seam, 0)
        baseline = self.engine._prospect(family, candidate, r1tok, r2tok, EDGE, EDGE)
        for token in self._probe_tokens():
            edge4 = self.engine._prospect(family, candidate, r1tok, r2tok, token, EDGE)
            if edge4 != baseline:
                return True
            if self.engine._prospect(family, candidate, r1tok, r2tok, token, UNKNOWN) != edge4:
                return True
        return False

    def fourth_live(self, family: str, right1: str, right2: str, right3: str) -> bool:
        r1tok, r2tok = RightToken("letter", right1), RightToken("letter", right2)
        r3tok = RightToken("letter", right3)
        stage_one = (
            self.engine.simulated_prospect
            and self._prospect_varies_fourth(family, right1, right2, right3, r1tok, r2tok, r3tok)
        ) or (
            self.engine.vote_slots
            and self._vote_varies_fourth(family, right1, right2, right3, r1tok, r2tok, r3tok)
        )
        if not stage_one:
            return False
        key = ("seat4", family, right1, right2, right3)
        verdict = self._fourth.get(key)
        if verdict is None:
            verdict = self._seat_varies(family, r1tok, r2tok, r3tok)
            self._fourth[key] = verdict
        return verdict

    def _prospect_varies_fourth(
        self,
        family: str,
        right1: str,
        right2: str,
        right3: str,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
    ) -> bool:
        for stance, seam in self._input_shapes(family):
            key = (right1, right2, right3, self._signature(right1, family, stance, seam))
            verdict = self._fourth.get(key)
            if verdict is None:
                verdict = self._fourth_class_live(family, stance, seam, r1tok, r2tok, r3tok)
                self._fourth[key] = verdict
            if verdict:
                return True
        return False

    def _fourth_class_live(
        self,
        family: str,
        stance: str,
        seam: Height | None,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
    ) -> bool:
        candidate = Candidate(stance, None, seam, 0)
        baseline = self.engine._prospect(family, candidate, r1tok, r2tok, r3tok, EDGE)
        return any(
            self.engine._prospect(family, candidate, r1tok, r2tok, r3tok, token) != baseline
            for token in self._probe_tokens()
        )

    def _vote_records(self, follower: str) -> tuple:
        records = self._votes.get(follower)
        if records is None:
            records = tuple(self.spec.runes[follower].policy.prefer)
            self._votes[follower] = records
        return records

    def _vote_varies_third(
        self, family: str, right1: str, right2: str, r1tok: RightToken, r2tok: RightToken
    ) -> bool:
        # A same-family seam never votes: _apply_prefers' second gather duplicates the owner string and _prefer_favors takes the own branch, whose real slots the chain arm already models.
        if right1 == family or not self._vote_records(right1):
            return False
        for stance, seam in self._input_shapes(family):
            key = ("vote3", right1, right2, self._signature(right1, family, stance, seam))
            verdict = self._third.get(key)
            if verdict is None:
                verdict = self._vote_class_live(family, stance, seam, r1tok, r2tok, None)
                self._third[key] = verdict
            if verdict:
                return True
        return False

    def _vote_varies_fourth(
        self,
        family: str,
        right1: str,
        right2: str,
        right3: str,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
    ) -> bool:
        if right1 == family or not self._vote_records(right1):
            return False
        for stance, seam in self._input_shapes(family):
            key = ("vote4", right1, right2, right3, self._signature(right1, family, stance, seam))
            verdict = self._fourth.get(key)
            if verdict is None:
                verdict = self._vote_class_live(family, stance, seam, r1tok, r2tok, r3tok)
                self._fourth[key] = verdict
            if verdict:
                return True
        return False

    def _vote_class_live(
        self,
        family: str,
        stance: str,
        seam: Height | None,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken | None,
    ) -> bool:
        """Whether some follower vote's verdict at this seat moves with the probed deep token — the stage-4b analogue of `_third_class_live`/`_fourth_class_live`, probing `_prefer_favors`' vote branch itself because a vote reads the deep slots two ways at once: its record's shifted when: chain, and the follower-cell enumeration `candidates()` runs over the shifted window (a row scope or closure verdict changing with the token changes which continuations the vote can favor). `r3tok=None` probes the third slot (right4 held to EDGE and to UNKNOWN, the same belt the prospect probes wear); a concrete `r3tok` probes the fourth at that third."""
        candidate = Candidate(stance, None, seam, 0)
        owner = r1tok.letter
        edge_left = LeftContext("edge")
        for record in self._vote_records(owner):
            if r3tok is None:
                baseline = self.engine._prefer_favors(
                    owner, record, family, candidate, edge_left, r1tok, r2tok, EDGE, EDGE
                )
                for token in self._probe_tokens():
                    edge4 = self.engine._prefer_favors(
                        owner, record, family, candidate, edge_left, r1tok, r2tok, token, EDGE
                    )
                    if edge4 != baseline:
                        return True
                    if (
                        self.engine._prefer_favors(
                            owner, record, family, candidate, edge_left, r1tok, r2tok, token, UNKNOWN
                        )
                        != edge4
                    ):
                        return True
            else:
                baseline = self.engine._prefer_favors(
                    owner, record, family, candidate, edge_left, r1tok, r2tok, r3tok, EDGE
                )
                for token in self._probe_tokens():
                    if (
                        self.engine._prefer_favors(
                            owner, record, family, candidate, edge_left, r1tok, r2tok, r3tok, token
                        )
                        != baseline
                    ):
                        return True
        return False

    def _seat_left_classes(self, family: str) -> tuple[LeftContext, ...]:
        reps = self._left_classes.get(family)
        if reps is None:
            out: list[LeftContext] = [
                LeftContext("edge"),
                LeftContext("space"),
                LeftContext("zwnj"),
                LeftContext("namer-dot"),
            ]
            seen: set[tuple] = set()
            for left_family in self.spec.runes:
                for stance, seam in self._input_shapes(left_family):
                    sig = self._signature(family, left_family, stance, seam)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    out.append(self._virtual(left_family, stance, seam))
            reps = tuple(out)
            self._left_classes[family] = reps
        return reps

    def _seat_varies(
        self, family: str, r1tok: RightToken, r2tok: RightToken, r3tok: RightToken | None
    ) -> bool:
        token = RightToken("letter", family)
        for left in self._seat_left_classes(family):
            if r3tok is None:
                baseline = self._seat_outcome(left, token, r1tok, r2tok, EDGE, EDGE)
            else:
                baseline = self._seat_outcome(left, token, r1tok, r2tok, r3tok, EDGE)
            if baseline is _SEAT_RAISED:
                return True
            if baseline is _SEAT_UNREACHABLE:
                continue
            for probe_token in self._probe_tokens():
                if r3tok is None:
                    edge4 = self._seat_outcome(left, token, r1tok, r2tok, probe_token, EDGE)
                    if edge4 is _SEAT_RAISED or edge4 is _SEAT_UNREACHABLE or edge4 != baseline:
                        return True
                    unknown4 = self._seat_outcome(left, token, r1tok, r2tok, probe_token, UNKNOWN)
                    if unknown4 is _SEAT_RAISED or unknown4 is _SEAT_UNREACHABLE or unknown4 != edge4:
                        return True
                else:
                    varied = self._seat_outcome(left, token, r1tok, r2tok, r3tok, probe_token)
                    if varied is _SEAT_RAISED or varied is _SEAT_UNREACHABLE or varied != baseline:
                        return True
        return False

    def _seat_outcome(
        self,
        left: LeftContext,
        token: RightToken,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
        r4tok: RightToken,
    ):
        try:
            return self.engine.transition_trace(left, token, r1tok, r2tok, r3tok, r4tok).settled.cell
        except EIncomparableError, EAmbiguousError:
            return _SEAT_RAISED
        except SettleError:
            return _SEAT_UNREACHABLE


_SEAT_RAISED = object()
_SEAT_UNREACHABLE = object()

_LIVENESS_PROBES: OrderedDict[int, tuple[Engine, _ProspectLiveness]] = OrderedDict()
_LIVENESS_PROBES_CAP = 8


def _liveness_probe(spec: ResolvedSpec, engine: Engine) -> _ProspectLiveness:
    entry = _LIVENESS_PROBES.get(id(engine))
    if entry is not None and entry[0] is engine:
        _LIVENESS_PROBES.move_to_end(id(engine))
        return entry[1]
    probe = _ProspectLiveness(spec, engine)
    _LIVENESS_PROBES[id(engine)] = (engine, probe)
    while len(_LIVENESS_PROBES) > _LIVENESS_PROBES_CAP:
        _LIVENESS_PROBES.popitem(last=False)
    return probe


def third_slot_filter(
    spec: ResolvedSpec, features: frozenset[str], engine: Engine | None = None
) -> Callable[[str, str, str], bool]:
    """Whether the raw third slot can decide an input's window, keyed by rune-family names: (input family, right1 family, right2 family) -> bool. The chain arm is true exactly when some depth-3-reach prefer or resolve chain on the input's own rune evaluates to unknown over (right1, right2, UNKNOWN, UNKNOWN) — resolve records receive all four raw slots in `_apply_resolution`, so their chains are censused alongside the prefers — `cond_matches_right` returns None whenever a consulted constraint touched a beyond-window token, and a definite True/False verdict never consulted one, so a window judged definite here settles identically under every third token and its right3 stays #NA. When the probing engine scores the simulated prospect (issue 28) or hands votes their shifted slots (stage 4b), the `_ProspectLiveness` arm is ORed in: the slot also opens where some candidate shape's simulated follower choice, or some follower vote's verdict, moves with the third token — together with the chain arm those are the only ways any kernel mode reads it (seat-side refusals and unlocks are never handed the deep slots). `fourth_slot_filter` is the same gate one slot deeper; a window this filter judges definite is definite for it too — reach-3 chains are reach-2 chains, and the liveness arm's `third_live` ORs in `fourth_live` over every concrete letter third, so a dead third slot never hides a live fourth by construction. Shared by `enumerate_transitions` (enumeration gate) and conform's window replay, which must agree on which windows carry a live third slot."""
    probe = engine if engine is not None else Engine(spec, features)
    chains = {
        name: tuple(
            record.when.right
            for record in tuple(spec.runes[name].policy.prefer) + tuple(spec.runes[name].policy.resolve)
            if record.when.right is not None and right_chain_reach(record.when.right) >= 2
        )
        for name in depth3_inputs(spec)
    }
    liveness = _liveness_probe(spec, probe) if probe.simulated_prospect or probe.vote_slots else None
    verdicts: dict[tuple[str, str, str], bool] = {}

    def matters(input_family: str, right1: str, right2: str) -> bool:
        key = (input_family, right1, right2)
        cached = verdicts.get(key)
        if cached is None:
            window = (
                RightToken("letter", right1),
                RightToken("letter", right2),
                UNKNOWN,
                UNKNOWN,
            )
            cached = any(
                probe.cond_matches_right(input_family, chain, window) is None
                for chain in chains.get(input_family, ())
            )
            if not cached and liveness is not None:
                cached = liveness.third_live(input_family, right1, right2)
            verdicts[key] = cached
        return cached

    return matters


def fourth_slot_filter(
    spec: ResolvedSpec, features: frozenset[str], engine: Engine | None = None
) -> Callable[[str, str, str, str], bool]:
    """Whether the raw fourth slot can decide an input's window, keyed by rune-family names: (input family, right1 family, right2 family, right3 family) -> bool. The chain arm is true exactly when some depth-4-reach prefer or resolve chain on the input's own rune evaluates to unknown over (right1, right2, right3, UNKNOWN) — `cond_matches_right` returns None whenever a consulted constraint touched the fourth token, and a definite True/False verdict never consulted it, so a window judged definite here settles identically under every fourth token and its right4 stays #NA. When the probing engine scores the simulated prospect (issue 28) or hands votes their shifted slots (stage 4b), the `_ProspectLiveness` arm is ORed in: the slot also opens where some candidate shape's simulated follower choice, or some follower vote's verdict, moves with the fourth token at this concrete third. Shared by `enumerate_transitions` (enumeration gate) and conform's window replay, which must agree on which windows carry a live fourth slot."""
    probe = engine if engine is not None else Engine(spec, features)
    chains = {
        name: tuple(
            record.when.right
            for record in tuple(spec.runes[name].policy.prefer) + tuple(spec.runes[name].policy.resolve)
            if record.when.right is not None and right_chain_reach(record.when.right) >= 3
        )
        for name in depth4_inputs(spec)
    }
    liveness = _liveness_probe(spec, probe) if probe.simulated_prospect or probe.vote_slots else None
    verdicts: dict[tuple[str, str, str, str], bool] = {}

    def matters(input_family: str, right1: str, right2: str, right3: str) -> bool:
        key = (input_family, right1, right2, right3)
        cached = verdicts.get(key)
        if cached is None:
            window = (
                RightToken("letter", right1),
                RightToken("letter", right2),
                RightToken("letter", right3),
                UNKNOWN,
            )
            cached = any(
                probe.cond_matches_right(input_family, chain, window) is None
                for chain in chains.get(input_family, ())
            )
            if not cached and liveness is not None:
                cached = liveness.fourth_live(input_family, right1, right2, right3)
            verdicts[key] = cached
        return cached

    return matters


def _formation_pairs(spec: ResolvedSpec) -> frozenset[tuple[str, str]]:
    pairs = set()
    sequences = {name: rune.sequence for name, rune in spec.runes.items() if rune.sequence}
    for sequence in sequences.values():
        for lead, trail in zip(sequence, sequence[1:]):
            pairs.add((lead, trail))
            # The via-lead twin: a formed ligature token whose first component is this pair's trail stands for that trail in a post-formation stream, so a bare lead directly before it is the same formation-impossible adjacency wearing the follower's ligature name (bare ·Out before qsTea_qsOy spells raw ·Out·Tea·Oy, where greedy formation forms qsOut_qsTea first).
            for liga_name, liga_sequence in sequences.items():
                if liga_sequence[0] == trail:
                    pairs.add((lead, liga_name))
    return frozenset(pairs)


def _survivable_formation_windows(
    spec: ResolvedSpec, right_letters: list[RightToken], right_boundaries: list[RightToken]
) -> dict[tuple[str, str], dict[str, frozenset[RightToken] | None]]:
    """The section 5.7 late-formation guard translated into the table's post-formation label space: for each formation (lead, trail) pair, the right2 options under which the pair survives unformed, each mapped to the allowed right2 tokens of the trail's own subsequent window (None = unrestricted, the case where the follower is itself a formed ligature that swallowed both guard slots). The guard reads raw slots, so a ligature label at either slot is queried through its raw components."""
    from rebuild.pipeline import settle as settle_module

    def raw_of(token: RightToken) -> RightToken:
        if token.kind != "letter":
            return token
        sequence = spec.runes[token.letter].sequence
        return RightToken("letter", sequence[0]) if sequence else token

    out: dict[tuple[str, str], dict[str, frozenset[RightToken] | None]] = {}
    for name, rune in spec.runes.items():
        if not rune.sequence:
            continue
        pair = (rune.sequence[-2], rune.sequence[-1])
        follower_map: dict[str, frozenset[RightToken] | None] = {}
        for follower in right_letters:
            follower_sequence = spec.runes[follower.letter].sequence
            if follower_sequence:
                lead_token = RightToken("letter", follower_sequence[-2])
                trail_token = RightToken("letter", follower_sequence[-1])
                if settle_module.formation_blocked(spec, name, lead_token, trail_token):
                    follower_map[follower.letter] = None
                continue
            allowed = frozenset(
                option
                for option in right_boundaries + right_letters
                if settle_module.formation_blocked(spec, name, follower, raw_of(option))
            )
            if allowed:
                follower_map[follower.letter] = allowed
        if follower_map:
            out[pair] = follower_map
        # The via-lead keys: for a follower ligature whose first component is this pair's trail, a bare lead survives directly before the formed follower only where this pair's own formation is blocked reading the follower's second component as its first guard slot (raw lead·trail·second·F). The deeper slot restricts nothing — the guard's two slots are fully consumed — so entries carry None, matching the formed-ligature-follower convention above. A survivable-before-boundary verdict is inexpressible in the letters-keyed map, so it asserts instead of silently narrowing.
        for liga_name, liga_rune in spec.runes.items():
            liga_sequence = liga_rune.sequence
            if not liga_sequence or liga_sequence[0] != pair[1] or liga_name == name:
                continue
            second_token = RightToken("letter", liga_sequence[1])
            via_map: dict[str, frozenset[RightToken] | None] = {}
            for follower in right_letters:
                if settle_module.formation_blocked(spec, name, second_token, raw_of(follower)):
                    via_map[follower.letter] = None
            for boundary in right_boundaries:
                assert not settle_module.formation_blocked(
                    spec, name, second_token, boundary
                ), f"{name} survives before ({liga_name}, {boundary.kind}); the survivable map cannot key a boundary follower"
            if via_map:
                out[(pair[0], liga_name)] = via_map
    return out


class _WindowOptions:
    """The per-build static structures behind the right-slot option pipelines: formation pairs, the section 5.7 survivable-window maps, the ligature sequences, and the r3/r4 option pipelines themselves. One implementation, shared by the enumeration loop, `_DeepFiberDeriver` (whose fiber key records the computed r4 option list, so a filter added to the pipeline without a key update fails `_assert_deep_slot_partition` loudly instead of silently splitting a fiber), and the partition assertion — the option list a fiber key records is computed by exactly the code the enumeration runs."""

    def __init__(self, spec: ResolvedSpec):
        self.spec = spec
        self.letters = sorted(spec.runes)
        self.right_letters = [RightToken("letter", name) for name in self.letters]
        self.right_boundaries = [EDGE, SPACE, ZWNJ, NAMER_DOT]
        self.formation_pairs = _formation_pairs(spec)
        self.survivable = _survivable_formation_windows(spec, self.right_letters, self.right_boundaries)
        self.liga_sequences = {name: rune.sequence for name, rune in spec.runes.items() if rune.sequence}
        self.raw_second_options = self.right_boundaries + [
            t for t in self.right_letters if t.letter not in self.liga_sequences
        ]

    def liga_formed_before(self, name: str, next1: RightToken, next2: RightToken | None) -> bool:
        """Whether a formed `name` ligature can immediately precede (next1, next2) in a post-formation stream: its own guard, read over the raw tokens those post-formation neighbors stand for, must not fire. `next2 = None` means the second guard slot lies beyond the window, so the verdict is existential over the raw options."""
        if next1.kind != "letter":
            return True
        sequence = self.liga_sequences.get(next1.letter)
        if sequence:
            first: RightToken = RightToken("letter", sequence[0])
            second: RightToken | None = RightToken("letter", sequence[1])
        else:
            first = next1
            if next2 is None:
                second = None
            elif next2.kind == "letter" and (next2_sequence := self.liga_sequences.get(next2.letter)):
                second = RightToken("letter", next2_sequence[0])
            else:
                second = next2
        if second is not None:
            return not settle_module.formation_blocked(self.spec, name, first, second)
        return any(
            not settle_module.formation_blocked(self.spec, name, first, option)
            for option in self.raw_second_options
        )

    def context_follower_map(
        self, rune_name: str, right1: str
    ) -> dict[str, frozenset[RightToken] | None] | None:
        """The late-formation follower map an (input, right1) window inherits — None when the pair is not a formation pair (unrestricted), and the survivable map's entry otherwise; the enumeration never reaches a pair whose entry is absent, because such windows are inadmissible outright."""
        if (rune_name, right1) not in self.formation_pairs:
            return None
        return self.survivable.get((rune_name, right1))

    def right3_options(
        self,
        right1: RightToken,
        right2: RightToken,
        follower_map: dict[str, frozenset[RightToken] | None] | None,
    ) -> list[RightToken]:
        options = [
            r
            for r in self.right_boundaries + self.right_letters
            if not (
                r.kind == "letter"
                and (right2.letter, r.letter) in self.formation_pairs
                and (right2.letter, r.letter) not in self.survivable
            )
        ]
        if follower_map is not None:
            trail_allowed = follower_map.get(right2.letter)
            if trail_allowed is not None:
                options = [r for r in options if r in trail_allowed]
        if (right1.letter, right2.letter) in self.formation_pairs:
            pair_map = self.survivable.get((right1.letter, right2.letter)) or {}
            options = [r for r in options if r.kind == "letter" and r.letter in pair_map]
        if right1.letter in self.liga_sequences:
            options = [r for r in options if self.liga_formed_before(right1.letter, right2, r)]
        if right2.letter in self.liga_sequences:
            options = [r for r in options if self.liga_formed_before(right2.letter, r, None)]
        return options

    def right4_options(self, right1: RightToken, right2: RightToken, right3: RightToken) -> list[RightToken]:
        options = [
            r
            for r in self.right_boundaries + self.right_letters
            if not (
                r.kind == "letter"
                and (right3.letter, r.letter) in self.formation_pairs
                and (right3.letter, r.letter) not in self.survivable
            )
        ]
        if (right1.letter, right2.letter) in self.formation_pairs:
            pair_map = self.survivable.get((right1.letter, right2.letter)) or {}
            trail_allowed4 = pair_map.get(right3.letter)
            if trail_allowed4 is not None:
                options = [r for r in options if r in trail_allowed4]
        if (right2.letter, right3.letter) in self.formation_pairs:
            pair_map2 = self.survivable.get((right2.letter, right3.letter)) or {}
            options = [r for r in options if r.kind == "letter" and r.letter in pair_map2]
        if right2.letter in self.liga_sequences:
            options = [r for r in options if self.liga_formed_before(right2.letter, right3, r)]
        if right3.letter in self.liga_sequences:
            options = [r for r in options if self.liga_formed_before(right3.letter, r, None)]
        return options


_FIBER_RAISE_INCOMPARABLE = "raise:E-INCOMPARABLE"
_FIBER_RAISE_AMBIGUOUS = "raise:E-AMBIGUOUS"
_FIBER_RAISE_UNREACHABLE = "raise:E-UNREACHABLE"


@dataclass(frozen=True)
class _Fiber:
    """One r3 letter fiber of a live context: the member tokens (sorted-letter order, so the first member is the deterministic representative), the member-uniform `fourth_slot_matters` verdict, and — only where that verdict is true — the shared r4 sub-enumeration: `r4_groups` is the computed r4 option list partitioned into boundary singletons and r4 letter fibers, in option-pipeline order."""

    members: tuple[RightToken, ...]
    fourth_matters: bool
    r4_groups: tuple[tuple[RightToken, ...], ...]


@dataclass(frozen=True)
class _ContextFibers:
    boundary_options: tuple[RightToken, ...]
    fibers: tuple[_Fiber, ...]


class _DeepFiberDeriver:
    """The issue-26 fiber source: per live context (input family, right1, right2), partition the static r3 option list's letters into fibers of the outcome-probe function, lazily on first reach and memoized per build. The fiber key per candidate letter t3 is (i) the probe function f(t3) — for every left class in `_ProspectLiveness._seat_left_classes` and every bounded coordinate, the full row-visible probe record: the Settled (cell, seam, extension), prospect, joint_floor, and notes, plus the raise identity as three distinct values — (ii) the `fourth_slot_matters` verdict itself, and (iii) for members whose verdict is true, the computed r4 option list, run through `_WindowOptions.right4_options` per member so every present and future filter in the pipeline is keyed on structurally. The coordinate set is bounded, not the full grid: {EDGE, UNKNOWN} where the fourth slot is dead — an r4-dead member is traced only at EDGE and enqueues no r4 pin, so deeper coordinates are unread for it — widening to the full probe alphabet plus UNKNOWN exactly where `fourth_slot_matters` is true, which is where a seat can move under a specific (third, fourth) pair. Components (ii) and (iii) make an r3 class induce one shared r4 sub-enumeration, whose t4 groups under f(t3)(., t4) restricted to the option list are the r4 fibers — f is indexed by t3, so the r4 partition is per (context, r3 class), never per context alone. The probes run on the build's own tracing engine, so their traces land in the shared memo and their fired pointers in `engine.fired`, exactly as the liveness probes' traces already do. The one imported (not probed) assumption is the left-class collapse `_seat_left_classes` already trusts; it is guarded at real-left grain by the echo check in `enumerate_transitions` and the fiber verification test."""

    def __init__(
        self,
        spec: ResolvedSpec,
        engine: Engine,
        options: _WindowOptions,
        liveness: _ProspectLiveness,
        fourth_slot_matters: Callable[[str, str, str, str], bool],
    ):
        self.spec = spec
        self.engine = engine
        self.options = options
        self.liveness = liveness
        self.fourth_slot_matters = fourth_slot_matters
        self._contexts: dict[tuple[str, str, str], _ContextFibers] = {}

    def _record(
        self,
        left: LeftContext,
        token: RightToken,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
        r4tok: RightToken,
    ):
        try:
            trace = self.engine.transition_trace(left, token, r1tok, r2tok, r3tok, r4tok)
        except EIncomparableError:
            return _FIBER_RAISE_INCOMPARABLE
        except EAmbiguousError:
            return _FIBER_RAISE_AMBIGUOUS
        except SettleError:
            return _FIBER_RAISE_UNREACHABLE
        return (trace.settled, trace.prospect, trace.joint_floor, trace.notes)

    def context(self, family: str, right1: str, right2: str) -> _ContextFibers:
        key = (family, right1, right2)
        cached = self._contexts.get(key)
        if cached is not None:
            return cached
        token = RightToken("letter", family)
        r1tok = RightToken("letter", right1)
        r2tok = RightToken("letter", right2)
        follower_map = self.options.context_follower_map(family, right1)
        static = self.options.right3_options(r1tok, r2tok, follower_map)
        boundaries = tuple(t for t in static if t.kind != "letter")
        lefts = self.liveness._seat_left_classes(family)
        full_coords = tuple(self.liveness._probe_tokens()) + (UNKNOWN,)
        groups: dict[tuple, list[RightToken]] = {}
        for t3 in static:
            if t3.kind != "letter":
                continue
            fourth = bool(self.fourth_slot_matters(family, right1, right2, t3.letter))
            if fourth:
                coords = full_coords
                opts4 = tuple(self.options.right4_options(r1tok, r2tok, t3))
            else:
                coords = (EDGE, UNKNOWN)
                opts4 = ()
            probe = tuple(
                tuple(self._record(left, token, r1tok, r2tok, t3, coord) for coord in coords)
                for left in lefts
            )
            groups.setdefault((fourth, opts4, probe), []).append(t3)
        fibers: list[_Fiber] = []
        for (fourth, opts4, probe), members in groups.items():
            if fourth:
                coord_index = {coord: index for index, coord in enumerate(full_coords)}
                ordered: list[list[RightToken]] = []
                by_column: dict[tuple, list[RightToken]] = {}
                for t4 in opts4:
                    if t4.kind != "letter":
                        ordered.append([t4])
                        continue
                    column = tuple(row[coord_index[t4]] for row in probe)
                    bucket = by_column.get(column)
                    if bucket is None:
                        bucket = []
                        by_column[column] = bucket
                        ordered.append(bucket)
                    bucket.append(t4)
                r4_groups = tuple(tuple(bucket) for bucket in ordered)
            else:
                r4_groups = ()
            fibers.append(_Fiber(members=tuple(members), fourth_matters=fourth, r4_groups=r4_groups))
        result = _ContextFibers(boundary_options=boundaries, fibers=tuple(fibers))
        self._contexts[key] = result
        return result


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


@dataclass
class _PendingDeepRow:
    """One in-flight class-grain row, keyed on (base, fiber identity pair) while the worklist runs: the representative trace's row-visible record, the admitted r3 members accumulating across items (r4 members carry no pins and are full from the first item), and the frame tokens the echo traces replay after the drain."""

    left_context: LeftContext
    left_label: str
    input_label: str
    token: RightToken
    right1: RightToken
    right2: RightToken
    boundary3: RightToken | None
    admitted3: set[RightToken]
    members4: tuple[RightToken, ...] | None
    rep3: RightToken
    rep4: RightToken | None
    settled: Settled
    left_settled: Settled | None
    joint: bool
    prospect: int
    provenance: tuple[str, ...]


def _right_token_label(token: RightToken) -> str:
    return token.letter if token.kind == "letter" else BOUNDARY_LEFT_LABELS[token.kind]


def _assert_deep_slot_partition(
    decision: DecisionTable,
    options: _WindowOptions,
    deriver: _DeepFiberDeriver,
    deep_inputs: frozenset[str],
    deep4_inputs: frozenset[str],
    third_slot_matters: Callable[[str, str, str], bool],
    fourth_slot_matters: Callable[[str, str, str, str], bool],
) -> None:
    """The class-grain hard invariant (issue 26), asserted beside `assert_outcome_partition` on every class-grain build: per base, the observed r3 letter tokens' member sets are pairwise disjoint, each inside the recomputed static option list and inside one fiber of its context's partition (disjointness is per base, not per context, because worklist pins are per left state, so two bases in one context can legitimately admit nested subsets of one fiber); right3 is non-#NA exactly where the pre-gate and `third_slot_matters` say live — the pinned-world biconditional restated over tokens; one slot deeper, r4 member sets are disjoint per (base, r3 token), every member of an r3 token agrees on the `fourth_slot_matters` verdict and induces the identical computed r4 option list (the fiber key's option-list component re-verified against `_WindowOptions.right4_options`, so a filter added to that pipeline without a key update fails loudly instead of silently splitting a fiber); and every class id resolves through the table's map with every map entry used. Cover against the static option list is deliberately not asserted — pins legitimately exclude unreachable members, exactly as label grain excludes their rows."""
    deep = decision.deep_classes
    used: set[str] = set()
    context_cache: dict[tuple[str, str, str], tuple[set[str], dict[str, int]]] = {}
    r4_lists: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
    boundary_slot = BOUNDARYISH - {NA_LABEL}
    seen3: dict[tuple, dict[str, str]] = {}
    seen4: dict[tuple, dict[str, str]] = {}
    for row in decision.transitions:
        family = row.input_glyph.split(".")[0]
        letters_window = row.right1 not in BOUNDARYISH and row.right2 not in BOUNDARYISH
        live = family in deep_inputs and letters_window and third_slot_matters(family, row.right1, row.right2)
        if not live:
            if row.right3 != NA_LABEL:
                raise PartitionError(f"{row.key}: right3 enumerated where the filters say dead")
            continue
        if row.right3 == NA_LABEL:
            raise PartitionError(f"{row.key}: right3 #NA where the filters say live")
        if row.right3.startswith(DEEP_CLASS_PREFIX):
            if row.right3 not in deep:
                raise PartitionError(f"{row.key}: right3 token {row.right3} is not in the class map")
            used.add(row.right3)
        if row.right3 in boundary_slot:
            if row.right4 != NA_LABEL:
                raise PartitionError(f"{row.key}: right4 enumerated past a boundary third slot")
            continue
        context_key = (family, row.right1, row.right2)
        cached = context_cache.get(context_key)
        if cached is None:
            ctxf = deriver.context(family, row.right1, row.right2)
            static_letters: set[str] = set()
            fiber_of: dict[str, int] = {}
            for index, fiber in enumerate(ctxf.fibers):
                for member in fiber.members:
                    static_letters.add(member.letter)
                    fiber_of[member.letter] = index
            cached = (static_letters, fiber_of)
            context_cache[context_key] = cached
        static_letters, fiber_of = cached
        members3 = decision.token_members(row.right3)
        base = (row.input_glyph, row.left, row.right1, row.right2)
        taken3 = seen3.setdefault(base, {})
        for member in members3:
            claimed = taken3.get(member)
            if claimed is not None and claimed != row.right3:
                raise PartitionError(
                    f"{row.key}: r3 member {member} belongs to two tokens at one base: {claimed} and {row.right3}"
                )
            taken3[member] = row.right3
        outside = sorted(set(members3) - static_letters)
        if outside:
            raise PartitionError(f"{row.key}: r3 members outside the static option list: {outside}")
        if len({fiber_of[member] for member in members3}) > 1:
            raise PartitionError(f"{row.key}: r3 members straddle two fibers: {sorted(members3)}")
        verdicts = {bool(fourth_slot_matters(family, row.right1, row.right2, member)) for member in members3}
        if len(verdicts) > 1:
            raise PartitionError(
                f"{row.key}: members disagree on the fourth_slot_matters verdict: {sorted(members3)}"
            )
        fourth = verdicts.pop() and family in deep4_inputs
        if row.right4 == NA_LABEL:
            if fourth:
                raise PartitionError(f"{row.key}: right4 #NA where the filters say live")
            continue
        if not fourth:
            raise PartitionError(f"{row.key}: right4 enumerated where the filters say dead")
        r1tok = RightToken("letter", row.right1)
        r2tok = RightToken("letter", row.right2)
        shared: tuple[str, ...] | None = None
        for member in members3:
            list_key = (family, row.right1, row.right2, member)
            option_list = r4_lists.get(list_key)
            if option_list is None:
                option_list = tuple(
                    _right_token_label(option)
                    for option in options.right4_options(r1tok, r2tok, RightToken("letter", member))
                )
                r4_lists[list_key] = option_list
            if shared is None:
                shared = option_list
            elif option_list != shared:
                raise PartitionError(
                    f"{row.key}: members induce different computed r4 option lists: {members3[0]} vs {member}"
                )
        if row.right4.startswith(DEEP_CLASS_PREFIX):
            if row.right4 not in deep:
                raise PartitionError(f"{row.key}: right4 token {row.right4} is not in the class map")
            used.add(row.right4)
        if row.right4 in boundary_slot:
            continue
        members4 = decision.token_members(row.right4)
        taken4 = seen4.setdefault((base, row.right3), {})
        for member in members4:
            claimed = taken4.get(member)
            if claimed is not None and claimed != row.right4:
                raise PartitionError(
                    f"{row.key}: r4 member {member} belongs to two tokens at one base: {claimed} and {row.right4}"
                )
            taken4[member] = row.right4
        if shared is not None:
            missing = [member for member in members4 if member not in shared]
            if missing:
                raise PartitionError(f"{row.key}: r4 members outside the computed option list: {missing}")
    unused = set(deep) - used
    if unused:
        raise PartitionError(f"unused deep-class map entries: {sorted(unused)}")


@dataclass(frozen=True)
class FixpointProduct:
    """Everything one configuration's fixpoint produces and nothing it consulted: the key-sorted enriched transition stream, the deep-class map its class tokens resolve through, the provenance pointers the engine fired while tabulating, and the cells the stream settles into. `joint` on these rows is the trace's own `joint_floor` alone — the prospect-divergence pass runs in `assemble_tables`, over the expanded stream — and `cells` is stated at class grain, which equals the expanded set because a class row's members share its settled fields. This value is the kernel boundary: `assemble_tables` reads it and nothing else the engine touched, so a product parsed back from a file folds into the identical tables."""

    config: str
    transitions: tuple[Transition, ...]
    deep_classes: Mapping[str, tuple[str, ...]]
    cited_provenance: frozenset[str]
    cells: frozenset[CellId]


@dataclass(frozen=True)
class _FixpointContext:
    """The product plus the enumeration-side objects the class-grain assertions replay against — the fiber deriver, the option pipelines, the deep-input censuses, and the two slot filters. They are proof scaffolding, not product, which is why `enumerate_transitions` hands back the product alone and only `build_tables` sees this."""

    product: FixpointProduct
    options: _WindowOptions
    deriver: _DeepFiberDeriver | None
    deep_inputs: frozenset[str]
    deep4_inputs: frozenset[str]
    third_slot_matters: Callable[[str, str, str], bool]
    fourth_slot_matters: Callable[[str, str, str, str], bool]


def enumerate_transitions(
    spec: ResolvedSpec,
    features: frozenset[str],
) -> FixpointProduct:
    """One configuration's reachable windows, by fixpoint over reachable left states (the worklist comment below is the exactness contract). This is the kernel half of the build — every line that consults the settlement engine, and the half a port replaces wholesale — reduced to the one value `assemble_tables` needs. Wherever `_deep_world` holds and `DEEP_CLASSES_DEFAULT` is on, deep window slots enumerate at class grain (issue 26): the static option lists are computed exactly as at label grain, their letters split by `_DeepFiberDeriver`'s outcome fibers, worklist pins intersect each fiber, and the row for a (base, fiber pair) accumulates the union of admitted members across items — so the expanded member product equals the label-grain row multiset exactly, and everything from the joint-flag pass on consumes that expanded stream (`expanded_transitions`), keeping the fold, the rules, the treaty, and the serialized rules byte-identical by construction. The declared narrowing (issue 7's rule): per-member `transition_trace` at enumeration time becomes representative-plus-echo per (base, fiber pair) — the trace runs once with the first admitted member, and for every multi-member row the last member is additionally traced at the row's real left (the last r4 member at the representative r3 likewise) and its full probe record asserted equal, so the left-class collapse the fibers import is re-checked at real-left, real-entry, real-adjustment grain on every build; members between first and last are covered by the fiber probes at virtual-left grain, alarmed by the fiber verification test, the conform walk's first-divergent-member behavior, and the horizon-limited label-grain sweep. Standing residual: a middle member's real-left trace no longer runs, so a record whose only firing evidence was such a trace reads dead — the dead-policy gate errors on it loudly, and the fix at that point is targeted member tracing for the specific contexts, never a waiver."""
    return _enumerate(spec, features).product


def _enumerate(
    spec: ResolvedSpec,
    features: frozenset[str],
) -> _FixpointContext:
    """`enumerate_transitions` with the proof scaffolding still attached, for the class-grain assertions `build_tables` runs after the fold."""
    engine = Engine(spec, features, trace_memo=True)
    config = feature_config_token(features)
    options = _WindowOptions(spec)
    letters = options.letters
    formation_pairs = options.formation_pairs
    right_letters = options.right_letters
    right_boundaries = options.right_boundaries
    survivable = options.survivable
    liga_sequences = options.liga_sequences
    liga_formed_before = options.liga_formed_before
    right_label = _right_token_label

    deep_inputs = third_slot_inputs(spec, engine)
    deep4_inputs = fourth_slot_inputs(spec, engine)
    third_slot_matters = third_slot_filter(spec, features, engine)
    fourth_slot_matters = fourth_slot_filter(spec, features, engine)
    class_grain = DEEP_CLASSES_DEFAULT and _deep_world(engine)
    deriver = (
        _DeepFiberDeriver(spec, engine, options, _liveness_probe(spec, engine), fourth_slot_matters)
        if class_grain
        else None
    )

    transitions: dict[tuple[str, str, str, str, str, str], Transition] = {}
    deep_pending: dict[tuple, _PendingDeepRow] = {}
    seen: set[tuple] = set()
    # A worklist item is (left state, input rune, right1 constraint, right2 allowed-set, right3 allowed-set): a settled left state is reachable only alongside the right1 that was the producing window's right2 (an entry refusal or unlock conditioned on the follower makes other combinations contradictory — the left would never have committed there), so the fixpoint is exact, not merely sound. None = all right1 options (the boundary-left seeds). The right2 allowed-set carries the late-formation guard's second slot onto a surviving pair's trail window; None = unrestricted. The right3 allowed-set carries a producing window's enumerated right4 the same way, pinning a depth-4-decided left's successor windows to the third lookahead that was actually behind them. At class grain both allowed-sets carry the producing row's admitted member sets rather than singletons; the successor's right2 loop fans back to concrete labels through the same intersection, and a pin's intersection with a fiber is still member-uniform, so the successor row set is identical to label grain's.
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
        if left.kind == "letter":
            assert left.settled is not None
            left_label = cell_label(spec, left.settled.cell)
        else:
            left_label = BOUNDARY_LEFT_LABELS[left.kind]
        token = RightToken("letter", rune_name)
        right1_options = (
            [right1_constraint] if right1_constraint is not None else right_boundaries + right_letters
        )
        for right1 in right1_options:
            follower_map = None
            if right1.kind == "letter" and (rune_name, right1.letter) in formation_pairs:
                follower_map = survivable.get((rune_name, right1.letter))
                if follower_map is None:
                    continue
            if right1.kind == "letter":
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
            for right2 in right2_options:
                deep3_live = (
                    rune_name in deep_inputs
                    and right1.kind == "letter"
                    and right2.kind == "letter"
                    and third_slot_matters(rune_name, right1.letter, right2.letter)
                )
                if deriver is not None and deep3_live:
                    ctxf = deriver.context(rune_name, right1.letter, right2.letter)
                    slot3_entries: list[tuple[RightToken | None, _Fiber | None, tuple[RightToken, ...]]] = []
                    for option in ctxf.boundary_options:
                        if right3_allowed is None or option in right3_allowed:
                            slot3_entries.append((option, None, (option,)))
                    for fiber in ctxf.fibers:
                        admitted = tuple(
                            member
                            for member in fiber.members
                            if right3_allowed is None or member in right3_allowed
                        )
                        if admitted:
                            slot3_entries.append((None, fiber, admitted))
                    for boundary3, fiber3, admitted3 in slot3_entries:
                        slot4_entries: tuple[tuple[RightToken, ...] | None, ...]
                        if fiber3 is not None and rune_name in deep4_inputs and fiber3.fourth_matters:
                            slot4_entries = fiber3.r4_groups
                        else:
                            slot4_entries = (None,)
                        identity3 = (
                            boundary3
                            if boundary3 is not None
                            else fiber3.members if fiber3 is not None else ()
                        )
                        for members4 in slot4_entries:
                            rep3 = admitted3[0]
                            rep4 = members4[0] if members4 is not None else None
                            pending_key = (
                                input_label,
                                left_label,
                                right_label(right1),
                                right_label(right2),
                                identity3,
                                members4,
                            )
                            record = deep_pending.get(pending_key)
                            if record is not None:
                                if record.left_settled != left.settled:
                                    display = (
                                        input_label,
                                        left_label,
                                        right_label(right1),
                                        right_label(right2),
                                        right_label(rep3),
                                        right_label(rep4) if rep4 is not None else NA_LABEL,
                                    )
                                    raise PartitionError(
                                        f"window {display} reached from two left states sharing one label: {record.left_settled} vs {left.settled}"
                                    )
                                record.admitted3.update(admitted3)
                                settled = record.settled
                            else:
                                trace = engine.transition_trace(
                                    left, token, right1, right2, rep3, rep4 if rep4 is not None else EDGE
                                )
                                settled = trace.settled
                                deep_pending[pending_key] = _PendingDeepRow(
                                    left_context=left,
                                    left_label=left_label,
                                    input_label=input_label,
                                    token=token,
                                    right1=right1,
                                    right2=right2,
                                    boundary3=boundary3,
                                    admitted3=set(admitted3),
                                    members4=members4,
                                    rep3=rep3,
                                    rep4=rep4,
                                    settled=trace.settled,
                                    left_settled=left.settled,
                                    joint=trace.joint_floor,
                                    prospect=trace.prospect,
                                    provenance=tuple(trace.notes),
                                )
                            worklist.append(
                                (
                                    LeftContext("letter", settled),
                                    right1.letter,
                                    right2,
                                    frozenset(admitted3),
                                    frozenset(members4) if members4 is not None else None,
                                )
                            )
                    continue
                right3_slots: list[RightToken | None]
                if deep3_live:
                    right3_options = options.right3_options(right1, right2, follower_map)
                    if right3_allowed is not None:
                        right3_options = [r for r in right3_options if r in right3_allowed]
                    right3_slots = list(right3_options)
                else:
                    right3_slots = [None]
                for right3 in right3_slots:
                    right4_slots: list[RightToken | None]
                    if (
                        rune_name in deep4_inputs
                        and right3 is not None
                        and right3.kind == "letter"
                        and fourth_slot_matters(rune_name, right1.letter, right2.letter, right3.letter)
                    ):
                        right4_slots = list(options.right4_options(right1, right2, right3))
                    else:
                        right4_slots = [None]
                    for right4 in right4_slots:
                        # A worklist item with different pins can re-enumerate a window key already recorded; the recorded row's settled state is what a re-trace would return (the left label is injective into the trace's inputs), so a hit skips straight to the successor enqueue, whose pins still differ per item. The left-state comparison below is that premise made executable: it can only fire if cell_label stops being injective over settled lefts (say, two heights aliasing one y), which would otherwise silently merge distinct windows into one row.
                        window_key = (
                            input_label,
                            left_label,
                            right_label(right1),
                            right_label(right2) if right1.kind == "letter" else NA_LABEL,
                            right_label(right3) if right3 is not None else NA_LABEL,
                            right_label(right4) if right4 is not None else NA_LABEL,
                        )
                        existing = transitions.get(window_key)
                        if existing is not None:
                            if existing.left_settled != left.settled:
                                raise PartitionError(
                                    f"window {window_key} reached from two left states sharing one label: {existing.left_settled} vs {left.settled}"
                                )
                            settled = existing.settled
                        else:
                            trace = engine.transition_trace(
                                left,
                                token,
                                right1,
                                right2,
                                right3 if right3 is not None else EDGE,
                                right4 if right4 is not None else EDGE,
                            )
                            settled = trace.settled
                            transitions[window_key] = Transition(
                                input_glyph=input_label,
                                left=left_label,
                                right1=window_key[2],
                                right2=window_key[3],
                                right3=window_key[4],
                                right4=window_key[5],
                                outcome=cell_label(spec, trace.settled.cell),
                                settled=trace.settled,
                                left_settled=left.settled,
                                joint=trace.joint_floor,
                                prospect=trace.prospect,
                                provenance=tuple(trace.notes),
                            )
                        if right1.kind == "letter":
                            if right3 is not None:
                                successor_allowed = frozenset({right3})
                            else:
                                successor_allowed = (
                                    follower_map.get(right2.letter) if follower_map is not None else None
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
                                    LeftContext("letter", settled),
                                    right1.letter,
                                    right2,
                                    successor_allowed,
                                    successor_r3,
                                )
                            )

    deep_classes_map: dict[str, tuple[str, ...]] = {}

    def deep_label(member_letters: tuple[str, ...]) -> str:
        if len(member_letters) == 1:
            return member_letters[0]
        token_id = deep_class_id(member_letters)
        deep_classes_map[token_id] = member_letters
        return token_id

    def echo_mismatch(display: tuple, member: RightToken, expected: tuple, got: tuple) -> PartitionError:
        return PartitionError(
            f"deep-class echo mismatch at {display}: member {member.letter} traces {got} where the representative traced {expected}"
        )

    # The section 2.6 echo check: for every multi-member fiber row, the last member is re-traced at the row's real left (and the last r4 member at the representative r3) and its full row-visible probe record must equal the representative's — the standing real-left, real-entry, real-adjustment guard on the virtual-left fiber collapse, two members deep on every build.
    for pending in deep_pending.values():
        if pending.boundary3 is not None:
            label3 = right_label(pending.boundary3)
            admitted3 = (pending.boundary3,)
        else:
            admitted3 = tuple(sorted(pending.admitted3, key=lambda member: member.letter))
            label3 = deep_label(tuple(member.letter for member in admitted3))
        if pending.members4 is None:
            label4 = NA_LABEL
        elif pending.members4[0].kind != "letter":
            label4 = right_label(pending.members4[0])
        else:
            label4 = deep_label(tuple(member.letter for member in pending.members4))
        window_key = (
            pending.input_label,
            pending.left_label,
            right_label(pending.right1),
            right_label(pending.right2),
            label3,
            label4,
        )
        expected = (pending.settled, pending.prospect, pending.joint, pending.provenance)
        rep4tok = pending.rep4 if pending.rep4 is not None else EDGE
        if pending.boundary3 is None and len(admitted3) > 1:
            last3 = admitted3[-1] if admitted3[-1] != pending.rep3 else admitted3[0]
            echo = engine.transition_trace(
                pending.left_context, pending.token, pending.right1, pending.right2, last3, rep4tok
            )
            got = (echo.settled, echo.prospect, echo.joint_floor, tuple(echo.notes))
            if got != expected:
                raise echo_mismatch(window_key, last3, expected, got)
        if (
            pending.members4 is not None
            and pending.members4[0].kind == "letter"
            and len(pending.members4) > 1
        ):
            last4 = pending.members4[-1]
            echo = engine.transition_trace(
                pending.left_context, pending.token, pending.right1, pending.right2, pending.rep3, last4
            )
            got = (echo.settled, echo.prospect, echo.joint_floor, tuple(echo.notes))
            if got != expected:
                raise echo_mismatch(window_key, last4, expected, got)
        if window_key in transitions:
            raise PartitionError(f"deep-class window {window_key} collides with an existing row")
        transitions[window_key] = Transition(
            input_glyph=pending.input_label,
            left=pending.left_label,
            right1=window_key[2],
            right2=window_key[3],
            right3=label3,
            right4=label4,
            outcome=cell_label(spec, pending.settled.cell),
            settled=pending.settled,
            left_settled=pending.left_settled,
            joint=pending.joint,
            prospect=pending.prospect,
            provenance=pending.provenance,
        )

    # The fixpoint and the liveness probes are done tracing; drop the pile — the engine outlives this build in _LIVENESS_PROBES, so retaining a full trace per window would hold it for nothing.
    if engine._trace_cache is not None:
        engine._trace_cache.clear()

    enumerated = sorted(transitions.values(), key=lambda t: t.key)
    return _FixpointContext(
        product=FixpointProduct(
            config=config,
            transitions=tuple(enumerated),
            deep_classes=deep_classes_map,
            cited_provenance=frozenset(engine.fired),
            cells=frozenset(row.settled.cell for row in enumerated),
        ),
        options=options,
        deriver=deriver,
        deep_inputs=deep_inputs,
        deep4_inputs=deep4_inputs,
        third_slot_matters=third_slot_matters,
        fourth_slot_matters=fourth_slot_matters,
    )


def assemble_tables(spec: ResolvedSpec, product: FixpointProduct) -> tuple[DecisionTable, TreatyTable]:
    """The Python half of the build, folding one fixpoint product into its two tables: the class-grain stream expanded to label grain, the prospect-divergence flag pass over that expansion with every fired flag backfilled onto the class row covering it, the per-input rule fold, and the treaty fold. It reads the product and one verdict from `spec` (`is_entry_bearing`, for the ZWNJ backtrack-slot guards) — nothing else the engine saw — so a product that arrived over the kernel boundary assembles into exactly the tables the enumeration that produced it would have. The reachable cells are the product's; re-deriving them from the fold rows is one cheap loud check that the two grains still agree."""
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


def build_tables(
    spec: ResolvedSpec,
    features: frozenset[str],
) -> tuple[DecisionTable, TreatyTable]:
    """One configuration's decision and treaty tables: `enumerate_transitions` for the fixpoint, `assemble_tables` for the fold. The two class-grain assertions run here rather than inside either half because they are the only consumers wanting both the assembled table and the enumeration's own scaffolding — the fiber deriver, the option pipelines, the slot filters — which the product deliberately does not carry across the boundary."""
    context = _enumerate(spec, features)
    decision, treaty = assemble_tables(spec, context.product)
    if context.deriver is not None:
        _assert_deep_slot_partition(
            decision,
            context.options,
            context.deriver,
            context.deep_inputs,
            context.deep4_inputs,
            context.third_slot_matters,
            context.fourth_slot_matters,
        )
        decision._assert_deep_class_unions()
    return decision, treaty


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
