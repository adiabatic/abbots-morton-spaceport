"""Decision-table and treaty-table builders (M1-PLAN section 5, Group 2), promoted from prototype/table.py per the Recon B promotion map.

`build_tables(spec, features)` tabulates the settlement kernel over every (settled-left state, rune, raw-right-1, raw-right-2) window reachable under settlement for one feature configuration, by fixpoint over reachable left states rather than string enumeration, so the table is exact. Windows that formation makes impossible are excluded — but a ligature pair survives unformed exactly where the section 5.7 late-formation guard fires, so pair windows are enumerated under precisely the guard-firing follower contexts (`_survivable_formation_windows`): the lead's window is admitted per guard-firing right2, and the trail's window inherits the matching allowed-right2 set through the worklist, keeping the fixpoint exact. The mirror facet holds for formed-ligature tokens at any slot: a ligature input's window, and any window with a ligature at right1, is admitted only where that ligature's own guard does NOT fire over the raw tokens its post-formation neighbors stand for (`liga_formed_before`), existentially over the beyond-window slot. ZWNJ-locked entry-bearing inputs enumerate under the chokepoint twin's glyph name (`model.locked_glyph_name`, the `<raw>.noentry` shape the emitter's chokepoint actually produces), locked before settlement — which keeps each plain input's boundary-left outcomes in a single block, exactly as the prototype encoded it.

Outcome-partition compression is DFA-style per input and per slot: two fillers land in one class iff their full outcome signatures over the other slots are identical. `assert_outcome_partition` re-derives the partitions and replays every reachable transition against the ordered rules under first-match-wins semantics — the hard build invariant of prototype follow-up 1. Rule ordering per input follows the proven discipline: boundary-outcome rows with `uni200C` explicit in the class first, three-lookahead-slot rows before two-slot rows before one-slot rows, identity rows omitted, the slot-dropped fallback last, plus ZWNJ backtrack-slot coverage guards for never-locked inputs.

Rows carry a fourth window slot, `right3`, enumerated lazily and only where live: an input admitted by `third_slot_inputs` (the depth-3 chain census `depth3_inputs` under the candidacy-grain prospect; every rune under the simulated prospect, where any input's third join-count term can read the slot through its follower's replayed cascade) gets its windows split by the raw third lookahead, only where both nearer slots are letters, and only where `third_slot_filter` judges the window live — some own-rune depth-3 prefer chain still unknown over (right1, right2), or, flag-on, some candidate shape's simulated follower choice moved by the third token (`_ProspectLiveness`) — a window judged definite settles identically under every third token, so everywhere else the slot stays `#NA`, mirroring the established convention that no record peeks past a boundary. An enumerated window's settled left state is reachable only alongside right2 equal to that window's right3, so the worklist pins the successor's allowed-right2 set to that singleton — the same exactness plumbing the late-formation guard already rides — and the right3 options replay the right2 filters shifted one slot (formation-impossible adjacent pairs, guard-firing follower sets, `liga_formed_before` with the second slot now pinned). The fifth slot, `right4`, repeats the pattern one deeper: only a `fourth_slot_inputs` input with letters at all three nearer slots, and only where `fourth_slot_filter` finds the window live over those three slots, enumerates it. Where it does enumerate, its options replay the same filters shifted once more, and the worklist pins the successor's right3 to the producing window's right4. `_assert_window_arity` ties the Transition/Rule slot count to `model.RIGHT_WINDOW_SLOTS` at import, so the chain cap and the table can only widen together.

Joint rows combine both section 6.1 flags: ranking ties broken by the structural floor between candidates differing in seam realization, and windows whose deliberately optimistic prospect diverges from the follower's actual settled choice. Both TSV artifacts are diff-stable (section 8): sorted rows, provenance pointers, deterministic labels.

`write_windows` / `read_windows` persist a built table so the font-vs-settle sweep never rebuilds what the same sources already produced: the rules, the reachable cells and the enumerated windows, stamped with `fingerprint.tables_value` over the sources the fixpoint read. The windows come back as `Window` rows — labels only, which is everything a replay consults — so the file is a fraction of the resident table and the head alone answers "which cells are reachable".
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

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
    _cells: frozenset[CellId] = field(default_factory=frozenset)

    def reachable_cells(self) -> frozenset[CellId]:
        return self._cells

    def joint_rows(self) -> frozenset[int]:
        return frozenset(index for index, rule in enumerate(self.rules) if rule.joint)

    def assert_outcome_partition(self) -> None:
        """The hard build invariant (prototype follow-up 1): recompute the per-slot signature partitions and verify disjoint cover, then replay every reachable transition against the ordered rules under first-match-wins semantics."""
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


WINDOWS_FORMAT = "ams-m1-windows/1"
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
        _cells=frozenset(
            CellId(rune, stance, entry, exit_, tuple(adjustments))
            for rune, stance, entry, exit_, adjustments in head["cells"]
        ),
    )
    return head["inputs"], decision


def windows_digest(decision: DecisionTable) -> str:
    """Content hash of everything a witness hunt reads from one configuration's table: the ordered rules and the enumerated windows, in exactly the forms `write_windows` serializes, but without the inputs stamp. The stamp moves on any hashed source edit; the table moves only when settlement itself does — so a cache keyed on this digest survives the ink-only rune edits that dominate glyph work, and staleness the digest cannot see (a rename map or deep-slot filter moving while the raw windows stay put) is safe by construction, because a recorded witness is only ever tried first and re-verified, never trusted."""
    digest = hashlib.sha256()
    digest.update(decision.config.encode())
    digest.update(json.dumps([_rule_row(rule) for rule in decision.rules], separators=(",", ":")).encode())
    for row in decision.transitions:
        digest.update(
            "\t".join(
                (row.input_glyph, row.left, row.right1, row.right2, row.right3, row.right4, row.outcome)
            ).encode()
        )
        digest.update(b"\n")
    return digest.hexdigest()


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


def _deep_world(engine: Engine | None) -> bool:
    """Whether the process's engines read deep slots beyond the own-rune chain arm: the simulated prospect replays the follower's cascade over them (issue 28 stage 2), and shifted vote slots hand them to the seam's follower votes (stage 4b). Either one makes any input's window deep-slot-decidable, so the pre-gates widen to every rune and the per-window probes do the pruning. `engine=None` follows the module defaults the way Engine construction does, so the fallback paths that build their own filters stay consistent with the process's engines."""
    if engine is not None:
        return engine.simulated_prospect or engine.vote_slots
    return settle_module.SIMULATED_PROSPECT_DEFAULT or settle_module.VOTE_SLOTS_DEFAULT


def third_slot_inputs(spec: ResolvedSpec, engine: Engine | None = None) -> frozenset[str]:
    """The inputs whose windows can carry a live third slot — the pre-gate build_tables' enumeration and conform's window replay share ahead of the per-window `third_slot_filter` verdict. In the pinned candidacy world only an own-rune depth-3 prefer chain ever reads the slot, so this is exactly `depth3_inputs`; with the simulated prospect or shifted vote slots on (`_deep_world`), the raw third token can decide any input's window, so every rune is admitted and the per-window probe does all the pruning."""
    return frozenset(spec.runes) if _deep_world(engine) else depth3_inputs(spec)


def fourth_slot_inputs(spec: ResolvedSpec, engine: Engine | None = None) -> frozenset[str]:
    """`third_slot_inputs` one slot deeper: the depth-4 chain census in the pinned world, every rune under `_deep_world`."""
    return frozenset(spec.runes) if _deep_world(engine) else depth4_inputs(spec)


class _ProspectLiveness:
    """The simulated-prospect arm of the deep-slot filters (issue 28 stage 2): whether a raw deep token can move the settled outcome of some reachable window at (input, right1, right2). Two value-level stages, because every cheaper grain fails in a measured way — consultation-level tracking over-opens catastrophically (the recursion consults beyond-window slots almost everywhere), and stopping at follower-prospect variance still over-opens 15-fold (measured on the real spec: 1,543 of the consulted triples carry a token-movable prospect but only 103 ever move a seat outcome), enough to push the emitted settlement lookup through the budget gate's headroom floor. Stage one is the cheap prefilter: for each (stance, seam) shape the input can commit — the virtual left's entry is never read, so entry states collapse — the follower's simulated prospect is evaluated per concrete token and compared against the EDGE the table bakes for a dead slot; no variance anywhere means no channel into the seat's ranking (deep tokens reach the flag-on kernel only through prospect values and own-rune chains, and the chain arm runs before this probe), so the slot is definitely dead. Stage two, only where stage one fires, probes at outcome grain: the seat's own transition is replayed per token over the collapsed left-classes — every (family, stance, seam) virtual left plus the four boundary kinds, collapsed by the input-frame signature (committed seam, left kind, and the verdict vector of the input's own left-reading conditions: entry-row from-scopes and refuse/prefer/unlock left conditions — extend and contract records shape adjustments only, and neither the extension nor the left cell's entry interacts with a deep token, so reachable settled lefts are covered by the enumerated shapes) — and the slot is live only if some class's settled cell varies. Left-classes the fixpoint can never reach raise E-STRANDED in the replay and are skipped; a prefer conflict raising E-INCOMPARABLE/E-AMBIGUOUS marks the slot live so the enumeration surfaces it properly. The third-slot probes also compare each token's unknown-fourth evaluation against its EDGE-fourth one, and `third_live` additionally ORs in `fourth_live` over every concrete letter third — a live fourth slot hanging off an unenumerated third would otherwise never be consulted, and the EDGE/UNKNOWN-fourth comparisons alone cannot see a seat that moves only under a specific (third, fourth) letter pair, because unknown-optimism bottoms the recursion identically for both. With shifted vote slots on (stage 4b) stage one grows a vote arm beside the prospect arm: `_vote_class_live` probes `_prefer_favors`' vote branch itself per deep token, because a vote reads the deep slots both through its record's shifted when: chain and through the follower-cell enumeration the vote runs over the shifted window; a same-family seam is skipped (the own branch shadows the vote there and the chain arm models it), and stage two prunes vote-verdict variance that never moves the seat. Verdict caches key on the probed window and instances cache per engine (`_liveness_probe`), so both filters and every consultation share one memo, and the conform gate remains the standing alarm for any residual under-opening."""

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
            for record in tuple(rune.policy.refuse) + tuple(rune.policy.prefer):
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
    """Whether the raw third slot can decide an input's window, keyed by rune-family names: (input family, right1 family, right2 family) -> bool. The chain arm is true exactly when some depth-3-reach prefer chain on the input's own rune evaluates to unknown over (right1, right2, UNKNOWN, UNKNOWN) — `cond_matches_right` returns None whenever a consulted constraint touched a beyond-window token, and a definite True/False verdict never consulted one, so a window judged definite here settles identically under every third token and its right3 stays #NA. When the probing engine scores the simulated prospect (issue 28) or hands votes their shifted slots (stage 4b), the `_ProspectLiveness` arm is ORed in: the slot also opens where some candidate shape's simulated follower choice, or some follower vote's verdict, moves with the third token — together with the chain arm those are the only ways any kernel mode reads it (seat-side refusals and unlocks are never handed the deep slots). `fourth_slot_filter` is the same gate one slot deeper; a window this filter judges definite is definite for it too — reach-3 chains are reach-2 chains, and the liveness arm's `third_live` ORs in `fourth_live` over every concrete letter third, so a dead third slot never hides a live fourth by construction. Shared by `build_tables` (enumeration gate) and conform's window replay, which must agree on which windows carry a live third slot."""
    probe = engine if engine is not None else Engine(spec, features)
    chains = {
        name: tuple(
            record.when.right
            for record in spec.runes[name].policy.prefer
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
    """Whether the raw fourth slot can decide an input's window, keyed by rune-family names: (input family, right1 family, right2 family, right3 family) -> bool. The chain arm is true exactly when some depth-4-reach prefer chain on the input's own rune evaluates to unknown over (right1, right2, right3, UNKNOWN) — `cond_matches_right` returns None whenever a consulted constraint touched the fourth token, and a definite True/False verdict never consulted it, so a window judged definite here settles identically under every fourth token and its right4 stays #NA. When the probing engine scores the simulated prospect (issue 28) or hands votes their shifted slots (stage 4b), the `_ProspectLiveness` arm is ORed in: the slot also opens where some candidate shape's simulated follower choice, or some follower vote's verdict, moves with the fourth token at this concrete third. Shared by `build_tables` (enumeration gate) and conform's window replay, which must agree on which windows carry a live fourth slot."""
    probe = engine if engine is not None else Engine(spec, features)
    chains = {
        name: tuple(
            record.when.right
            for record in spec.runes[name].policy.prefer
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
    engine = Engine(spec, features, trace_memo=True)
    config = feature_config_token(features)
    letters = sorted(spec.runes)
    formation_pairs = _formation_pairs(spec)
    right_letters = [RightToken("letter", name) for name in letters]
    right_boundaries = [EDGE, SPACE, ZWNJ, NAMER_DOT]

    def right_label(token: RightToken) -> str:
        if token.kind == "letter":
            return token.letter
        return BOUNDARY_LEFT_LABELS[token.kind]

    survivable = _survivable_formation_windows(spec, right_letters, right_boundaries)
    deep_inputs = third_slot_inputs(spec, engine)
    deep4_inputs = fourth_slot_inputs(spec, engine)
    third_slot_matters = third_slot_filter(spec, features, engine)
    fourth_slot_matters = fourth_slot_filter(spec, features, engine)

    from rebuild.pipeline import settle as settle_module

    liga_sequences = {name: rune.sequence for name, rune in spec.runes.items() if rune.sequence}
    raw_second_options = right_boundaries + [t for t in right_letters if t.letter not in liga_sequences]

    def liga_formed_before(name: str, next1: RightToken, next2: RightToken | None) -> bool:
        """Whether a formed `name` ligature can immediately precede (next1, next2) in a post-formation stream: its own guard, read over the raw tokens those post-formation neighbors stand for, must not fire. `next2 = None` means the second guard slot lies beyond the window, so the verdict is existential over the raw options."""
        if next1.kind != "letter":
            return True
        sequence = liga_sequences.get(next1.letter)
        if sequence:
            first: RightToken = RightToken("letter", sequence[0])
            second: RightToken | None = RightToken("letter", sequence[1])
        else:
            first = next1
            if next2 is None:
                second = None
            elif next2.kind == "letter" and (next2_sequence := liga_sequences.get(next2.letter)):
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
                right3_slots: list[RightToken | None]
                if (
                    rune_name in deep_inputs
                    and right1.kind == "letter"
                    and right2.kind == "letter"
                    and third_slot_matters(rune_name, right1.letter, right2.letter)
                ):
                    right3_options = [
                        r
                        for r in right_boundaries + right_letters
                        if not (
                            r.kind == "letter"
                            and (right2.letter, r.letter) in formation_pairs
                            and (right2.letter, r.letter) not in survivable
                        )
                    ]
                    if follower_map is not None:
                        trail_allowed = follower_map.get(right2.letter)
                        if trail_allowed is not None:
                            right3_options = [r for r in right3_options if r in trail_allowed]
                    if (right1.letter, right2.letter) in formation_pairs:
                        pair_map = survivable.get((right1.letter, right2.letter)) or {}
                        right3_options = [
                            r for r in right3_options if r.kind == "letter" and r.letter in pair_map
                        ]
                    if right1.letter in liga_sequences:
                        right3_options = [
                            r for r in right3_options if liga_formed_before(right1.letter, right2, r)
                        ]
                    if right2.letter in liga_sequences:
                        right3_options = [
                            r for r in right3_options if liga_formed_before(right2.letter, r, None)
                        ]
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
                        right4_options = [
                            r
                            for r in right_boundaries + right_letters
                            if not (
                                r.kind == "letter"
                                and (right3.letter, r.letter) in formation_pairs
                                and (right3.letter, r.letter) not in survivable
                            )
                        ]
                        if (right1.letter, right2.letter) in formation_pairs:
                            pair_map = survivable.get((right1.letter, right2.letter)) or {}
                            trail_allowed4 = pair_map.get(right3.letter)
                            if trail_allowed4 is not None:
                                right4_options = [r for r in right4_options if r in trail_allowed4]
                        if (right2.letter, right3.letter) in formation_pairs:
                            pair_map2 = survivable.get((right2.letter, right3.letter)) or {}
                            right4_options = [
                                r for r in right4_options if r.kind == "letter" and r.letter in pair_map2
                            ]
                        if right2.letter in liga_sequences:
                            right4_options = [
                                r for r in right4_options if liga_formed_before(right2.letter, right3, r)
                            ]
                        if right3.letter in liga_sequences:
                            right4_options = [
                                r for r in right4_options if liga_formed_before(right3.letter, r, None)
                            ]
                        right4_slots = list(right4_options)
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

    # The fixpoint and the liveness probes are done tracing; the engine outlives this build in _LIVENESS_PROBES, so drop the memo rather than retain a full trace per window.
    if engine._trace_cache is not None:
        engine._trace_cache.clear()

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
    """Callers pass signatures built from present rows only, never the full other-slot label product: every value's product is the same per grouping, so identical present-maps imply identical missing-key sets, and grouping by the sparse signature yields exactly the partition the (missing -> None) product signature would — at O(rows) instead of O(label product), which is what keeps folding from regrowing quartically as depth-3/4 windows are authored."""
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
