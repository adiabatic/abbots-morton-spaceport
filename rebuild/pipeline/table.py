"""Decision-table and treaty-table data model and readers (M1-PLAN section 5, Group 2), promoted from prototype/table.py per the Recon B promotion map.

Both halves of the table build run in the crate under `rebuild/kernel-rs`: the fixpoint since issue 40's port landed and issue 78 left it the only one there is, and the fold since the crate grew its `build-tables` verb. Nothing here folds anything — `src/fold.rs` and `src/rulefold.rs` carry the prospect-divergence pass, the per-input rule fold and the treaty fold, `src/artifacts.rs` writes the settlement TSV, the treaty TSV and the windows payload, and `src/fold.rs` states this module as the contract it was transcribed from. What stays here is the vocabulary the rest of the rebuild speaks and the reading end of those artifacts: the window, rule and table data model; `read_windows` off the payload the kernel wrote and `read_treaty_tsv` off the treaty artifact, which is how `run_m1.build_tables` gets its rules, its reachable cells and its treaty rows back; and the digests table identity is stated at. What the rest of this docstring states is the semantics of the enumeration those artifacts record; the crate is where those rules execute, `rebuild/test_table.py` replays the crate's ordered rules against the crate's own rows on the mini fixture as an independent second opinion on the fold, and `gate:conform` is the standing independent check that settlement executes as described.

The kernel tabulates settlement over every (settled-left state, rune, raw-right-1, raw-right-2) window reachable under settlement for one feature configuration, by fixpoint over reachable left states rather than string enumeration, so the table is exact. Windows that formation makes impossible are excluded — but a ligature pair survives unformed exactly where the section 5.7 late-formation guard fires, so pair windows are enumerated under precisely the guard-firing follower contexts: the lead's window is admitted per guard-firing right2, and the trail's window inherits the matching allowed-right2 set through the worklist, keeping the fixpoint exact. The mirror facet holds for formed-ligature tokens at any slot: a ligature input's window, and any window with a ligature at right1, is admitted only where that ligature's own guard does NOT fire over the raw tokens its post-formation neighbors stand for, existentially over the beyond-window slot. ZWNJ-locked entry-bearing inputs enumerate under the chokepoint twin's glyph name (`model.locked_glyph_name`, the `<raw>.noentry` shape the emitter's chokepoint actually produces), locked before settlement — which keeps each plain input's boundary-left outcomes in a single block, exactly as the prototype encoded it.

Outcome-partition compression is DFA-style per input and per slot: two fillers land in one class iff their full outcome signatures over the other slots are identical. The crate replays reachable transitions against the ordered rules under first-match-wins semantics as it folds — the hard build invariant of prototype follow-up 1 — over one left per signature block, which `fold::assert_outcome_partition` argues is the same claim as replaying them all. The fold, the joint-flag pass, the treaty fold, the replay, and every serialized-rules consumer read the expanded label-grain row stream (`DecisionTable.expanded_transitions` on this side): a class-grain enumeration expands each row to its full member product before anything downstream runs, so those consumers are byte-identical to a label-grain build by construction, and `Rule` objects carry label vocabulary only — no class id ever reaches the rule fold, `write_tsv`, or a serialized rules head. Rule ordering per input follows the proven discipline: boundary-outcome rows with `uni200C` explicit in the class first, three-lookahead-slot rows before two-slot rows before one-slot rows, identity rows omitted, the slot-dropped fallback last, plus ZWNJ backtrack-slot coverage guards for never-locked inputs.

Rows carry a fourth window slot, `right3`, enumerated lazily and only where live: an input the kernel's own census admits — in the pinned candidacy world, exactly the runes carrying a prefer or resolve record whose right condition chains two hops; under the simulated prospect or the shifted vote slots, every rune, because any input's third join-count term can then read the slot through its follower's replayed cascade — gets its windows split by the raw third lookahead, only where both nearer slots are letters, and only where the kernel's liveness verdict still finds the window undecided over them: some own-rune depth-3 chain unknown over (right1, right2), or some candidate shape's simulated follower choice or some follower vote's verdict moved by the third token. A window judged definite settles identically under every third token, so everywhere else the slot stays `#NA`, mirroring the established convention that no record peeks past a boundary. An enumerated window's settled left state is reachable only alongside right2 equal to that window's right3, so the worklist pins the successor's allowed-right2 set to that singleton — the same exactness plumbing the late-formation guard already rides — and the right3 options replay the right2 filters shifted one slot (formation-impossible adjacent pairs, guard-firing follower sets, the formed-ligature guard with the second slot now pinned). The fifth slot, `right4`, repeats the pattern one deeper: only an input whose chain reaches that far (again, every rune under the deep-reading modes) with letters at all three nearer slots, and only where the same verdict finds the window live over those three slots, enumerates it. Where it does enumerate, its options replay the same filters shifted once more, and the worklist pins the successor's right3 to the producing window's right4. Under those deep-reading modes with class grain asked for (`kernel_exec.DEEP_CLASSES_DEFAULT`, and `kernel_exec.class_grain` for the rule that decides it), both deep slots enumerate at class grain (issue 26): the same option lists, their letters split by the kernel's outcome fibers — the liveness verdicts themselves are untouched and the #NA biconditional keeps its exact statement over tokens — one row per (base, fiber pair) holding a content-addressed member set (`deep_classes`, `deep_class_id`), the successor pins carrying the admitted member sets instead of singletons, and `expanded_transitions` restoring the label-grain stream for everything downstream. `_assert_window_arity` ties the Transition/Rule slot count to `model.RIGHT_WINDOW_SLOTS` at import, so the chain cap and the table can only widen together.

Joint rows combine both section 6.1 flags: ranking ties broken by the structural floor between candidates differing in seam realization, and windows whose deliberately optimistic prospect diverges from the follower's actual settled choice. Both TSV artifacts are diff-stable (section 8): sorted rows, provenance pointers, deterministic labels.

The windows artifact the kernel writes and `read_windows` reads back persists a built table so the font-vs-settle sweep never rebuilds what the same sources already produced: the rules, the reachable cells, one realizing certificate per rule and the enumerated windows, stamped with `fingerprint.tables_value` over the sources the fixpoint read. The windows come back as `Window` rows — labels only, which is everything a replay consults — so the file is a fraction of the resident table and the head alone answers "which cells are reachable" and hands the build's witness stage its certificates. Neither digest below reads the certificates: they are evidence the rules are realizable, closed off the rows' own producer chains in the crate's `certificate.rs`, not part of what the rules say.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Iterator, Mapping

from rebuild.pipeline.model import RIGHT_WINDOW_SLOTS, CellId, Settled

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


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
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
    certificates: tuple[tuple[str, ...], ...] = (
        ()
    )  # one realizing token stream per rule, in rule order, closed by the crate off the shortest chain of rows that produces a row the rule first-matches (certificate.rs); `conform.check_rule_certificates` settles each and asserts its rule fires
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


TREATY_COLUMNS = ("left", "right", "junction", "extension", "kern")


def read_treaty_tsv(path: Path) -> TreatyTable:
    """The `TreatyTable.write_tsv` inverse. The kernel's `build-tables` verb writes the artifact and the build reads it straight back, because the treaty table the defect gates want is a few thousand rows and re-deriving it would cost the fixpoint that produced it. Raises OSError when the file is absent and ValueError when it is not a treaty table this build understands."""
    lines = path.read_text().splitlines()
    if not lines or not lines[0].startswith("# treaty table, config "):
        raise ValueError(f"{path}: not a treaty table")
    if len(lines) < 2 or tuple(lines[1].split("\t")) != TREATY_COLUMNS:
        raise ValueError(f"{path}: treaty columns are not {TREATY_COLUMNS}")
    rows = []
    for number, line in enumerate(lines[2:], 3):
        fields = line.split("\t")
        if len(fields) != len(TREATY_COLUMNS):
            raise ValueError(
                f"{path}: line {number} has {len(fields)} fields, expected {len(TREATY_COLUMNS)}"
            )
        left, right, junction, extension, kern = fields
        rows.append(TreatyRow(left, right, junction, int(extension), int(kern)))
    return TreatyTable(config=lines[0].removeprefix("# treaty table, config "), rows=tuple(rows))


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


def read_windows(source: Path | IO[str], windows: bool = True) -> tuple[str, DecisionTable]:
    """The windows artifact read back: the fingerprint of the sources the table was built from, and the table itself with `Window` rows for transitions. The writer is the kernel's — `artifacts::write_windows` in the crate, whose payload `run_m1.build_tables` packs into the `.gz` this reads — so the format lives on both sides of the boundary and `WINDOWS_FORMAT` is what a drift is caught at. `windows=False` stops after the head, so a caller that wants only the rules and the reachable cells pays for one line — gzip streams, so the enumeration is never decompressed. Raises OSError when the file is absent and ValueError when it is not an enumeration this build understands; a caller deciding whether to trust the artifact compares the returned fingerprint itself.

    A path is opened as the gzip the persisted artifact wears; an already-open text stream is read as it stands, which is how the build reads back the head of the plain payload the kernel just wrote without first packing hundreds of megabytes into a shape the reader would only unpack again.
    """
    if isinstance(source, Path):
        with gzip.open(source, "rt") as handle:
            return _windows_of(handle, windows, str(source))
    return _windows_of(source, windows, str(getattr(source, "name", source)))


def _windows_of(handle: IO[str], windows: bool, name: str) -> tuple[str, DecisionTable]:
    marker, _, payload = handle.readline().rstrip("\n").partition("\t")
    if marker != f"# {WINDOWS_FORMAT}":
        raise ValueError(f"{name}: not a {WINDOWS_FORMAT} enumeration")
    head = json.loads(payload)
    rows: tuple[Window, ...] = ()
    if windows:
        if tuple(handle.readline().rstrip("\n").split("\t")) != WINDOWS_COLUMNS:
            raise ValueError(f"{name}: window columns are not {WINDOWS_COLUMNS}")
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
        certificates=tuple(tuple(tokens) for tokens in head.get("certificates", ())),
        _cells=frozenset(
            CellId(rune, stance, entry, exit_, tuple(adjustments))
            for rune, stance, entry, exit_, adjustments in head["cells"]
        ),
    )
    return head["inputs"], decision


def windows_digest(decision: DecisionTable) -> str:
    """Content hash of one configuration's settlement rows: the ordered rules, the deep-class map, and the enumerated windows, in exactly the forms the windows artifact serializes them, but without the inputs stamp. The stamp moves on any hashed source edit; this digest moves only when settlement itself does, which is what makes it the answer to "did the ink-only rune edit change any window at all". The class map is hashed between the rules and the rows, so a moved map moves the digest — a token's member set is part of what a row says."""
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


@dataclass(frozen=True)
class FixpointProduct:
    """Everything one configuration's fixpoint produces and nothing it consulted: the key-sorted enriched transition stream, the deep-class map its class tokens resolve through, the provenance pointers the engine fired while tabulating, and the cells the stream settles into. `joint` on these rows is the trace's own `joint_floor` alone — the prospect-divergence pass runs in the crate's fold, over the expanded stream — and `cells` is stated at class grain, which equals the expanded set because a class row's members share its settled fields. This value is the kernel boundary — `kernel_exec.enumerate_transitions` is where one comes from — and it carries everything a fold reads and nothing else the engine touched, which is what makes it readable at the enumeration's own grain rather than the grain a table settles for. No build takes this path and no tool asks for it: the crate folds the product it still holds, so the stream survives as the boundary's other half, exercised by the rebuild suite alone."""

    config: str
    transitions: tuple[Transition, ...]
    deep_classes: Mapping[str, tuple[str, ...]]
    cited_provenance: frozenset[str]
    cells: frozenset[CellId]
