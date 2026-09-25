"""The decision-table and treaty-table data model, and the readers and digests for the table artifacts the crate writes (rebuild/M1-PLAN.md §5, Group 2).

The crate under `rebuild/kernel-rs` builds both tables and nothing here folds. `src/fixpoint.rs` enumerates the windows. `src/fold.rs` and `src/rulefold.rs` hold the prospect-divergence pass, the per-input rule fold, and the treaty fold, and `src/artifacts.rs` writes the settlement TSV, the treaty TSV, and the windows payload. `run_m1.build_tables` reads its rules, reachable cells, and treaty rows back through `read_windows` and `read_treaty_tsv`. `rebuild/test_table.py` replays the crate's rules against its rows on the mini fixture as an independent check of the fold, and `gate:conform` checks that the compiled font agrees with settlement.

The kernel tabulates settlement over every (settled left, rune, right1, right2) window reachable for one feature configuration, by fixpoint over reachable left states, so the table is exact. Windows that formation makes impossible are left out, except that a ligature's component pair is enumerated where the §5.7 late-formation guard blocks the ligature. A window with a formed ligature as its input or at right1 is enumerated only where that ligature's guard does not block. A ZWNJ-locked entry-bearing input is enumerated under its chokepoint twin's name (`model.locked_glyph_name`).

Rows carry two deep slots, `right3` and `right4`. The kernel splits a window by the third raw token only where both nearer slots are letters and the window's outcome can still depend on that token, and by the fourth token likewise one slot deeper. Elsewhere the slot holds `#NA`. The crate's `census.rs` and `liveness.rs` decide which windows are split. In the pinned world only runes with a `prefer` or `resolve` record whose right chain reaches the slot can be split. Under the simulated prospect or the vote slots every rune can be. Under class grain (`kernel_exec.class_grain`) a deep slot holds a class id (`deep_class_id`) standing for the letters that settle identically there, listed in `deep_classes`. Class rows are expanded back to labels before folding (`expanded_transitions` on this side), and the fold, the joint-flag pass, the treaty fold, and the rules all read that expanded stream, so `Rule` objects hold labels only. doc/rebuild-design.md §3.4 describes the deep slots. `_assert_window_arity` checks at import that `Transition` and `Rule` have `model.RIGHT_WINDOW_SLOTS` right slots.

`src/rulefold.rs` specifies how each input's rows are compressed into rules and the order of those rules. The crate checks the rules by replaying the rows against them under first-match-wins (`fold::assert_outcome_partition`).

A row is joint when either §6.1 flag applies: the structural floor broke a ranking tie between candidates that differ in seam, or the optimistic prospect differs from the follower's settled choice. Both TSV artifacts are diff-stable (§8): a deterministic row order, provenance pointers, and deterministic labels.

The windows artifact stores a built table so the conformance sweep does not rebuild from unchanged sources: the rules, the reachable cells, one certificate per rule, and the enumerated windows, stamped with `fingerprint.tables_value` over the sources the fixpoint read. `read_windows` returns the windows as `Window` rows, labels only, which is all a replay reads. The head alone gives the reachable cells and the witness stage's certificates. Neither digest in this module reads the certificates, because they are evidence that the rules can fire, not part of what the rules say.
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
    """Return the content-addressed id for a deep-slot member set: `#C` plus the first 12 hex digits of the SHA-256 of the tab-joined members, which the caller passes sorted. Identical member sets therefore share one id across contexts, configurations, and builds, which keeps cross-configuration artifact comparison meaningful. The `#` prefix keeps ids out of the glyph namespace, and no id is in `BOUNDARYISH`. The crate mints the ids (`fixpoint::deep_class_id`), and `rebuild/test_table.py` checks a kernel-built table's tokens against this function."""
    digest = hashlib.sha256("\t".join(members).encode()).hexdigest()
    return f"{DEEP_CLASS_PREFIX}{digest[:12]}"


class PartitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Window:
    """The label view of one settlement window: the slots that key it and the outcome. This is all a replay reads, so it is all the windows artifact stores and all `read_windows` returns."""

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
    """A window plus the fields only the fold reads: the settled cells the treaty table is folded from, the optimistic prospect the joint flag is scored against, and the provenance pointers the rule fold copies onto rules."""

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
    )  # YAML pointers of every record the engine fired while tabulating this configuration (Engine.fired); the dead-policy check reads them to decide which records are exercised
    deep_classes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    certificates: tuple[tuple[str, ...], ...] = (
        ()
    )  # one token stream per rule, in rule order, built by the crate to make that rule fire (certificate.rs); `witness.check_rule_certificates` settles each and checks that its rule fires
    _cells: frozenset[CellId] = field(default_factory=frozenset)

    def reachable_cells(self) -> frozenset[CellId]:
        return self._cells

    def joint_rows(self) -> frozenset[int]:
        return frozenset(index for index, rule in enumerate(self.rules) if rule.joint)

    def token_members(self, token: str) -> tuple[str, ...]:
        """Return the member labels a deep-slot field stands for: the class's members for a class id, else the label itself (bare labels, boundary labels, and `#NA` included), so a caller can expand any right3 or right4 field the same way."""
        members = self.deep_classes.get(token)
        return members if members is not None else (token,)

    def token_representative(self, token: str) -> str:
        """Return the first member of a class id, else the label itself: one concrete label to put in a deep slot. This is exact for rule-membership tests, because `fold::assert_deep_class_unions` checks that every emitted look class holds all of a token's members or none."""
        members = self.deep_classes.get(token)
        return members[0] if members else token

    def expanded_transitions(self) -> Iterator[Window]:
        """Yield the label-grain row stream: each class row expanded to its full member product at right3 × right4, with boundary labels and `#NA` passed through. Each expanded row copies the class row's outcome, which is valid because a class's members settle identically. Rows come in `Window.key` order with no duplicate keys, because the member sets at one base are disjoint. On a label-grain table this is `transitions` unchanged."""
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
    """Read a treaty TSV back: the inverse of `TreatyTable.write_tsv`. The kernel's `build-tables` subcommand writes the file, and the build reads it back because deriving the treaty table again would repeat the fixpoint. Raises OSError when the file is missing and ValueError when it is not a treaty table in this format."""
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
    """Read the windows artifact back: return the fingerprint of the sources the table was built from, and the table with `Window` rows as its transitions. The crate's `artifacts::write_windows` writes the payload and `run_m1.build_tables` gzips it, so the format is defined on both sides and `WINDOWS_FORMAT` is where a mismatch is caught. `windows=False` stops after the head, so a caller that wants only the rules and the reachable cells reads one line and the rest of the gzip stream is never decompressed. Raises OSError when the file is missing and ValueError when it is not an enumeration in this format. A caller that must decide whether to trust the artifact compares the returned fingerprint itself.

    A path is opened as gzip. An open text stream is read as is, which lets the build read the head of the plain payload the kernel just wrote without compressing it first.
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
    """Return a hash of one configuration's settlement rows: the ordered rules, the deep-class map, and the enumerated windows, in the forms the windows artifact stores them, without the inputs stamp. The stamp changes on any hashed source edit, but this digest changes only when settlement does, so it answers whether an ink-only rune edit changed any window. The class map is hashed because a token's member set is part of what a row says."""
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
    """Return a hash of everything one configuration's build produces: the ordered rules with their provenance and joint flags, every stored window row, the treaty rows, the reachable cells, the cited provenance, and the identity-guard count. Two builds that should agree, such as before and after a port or a refactor, are compared with this digest. `windows_digest` is narrower: it leaves out the treaty, the cells, the cited provenance, and the guard count, so it answers only whether the settlement rows changed. The deep-class map is not hashed separately, because class ids are content-addressed, so a changed member set changes the ids in the rows that cite it."""
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
    """Everything one configuration's fixpoint produces that a fold reads: the key-sorted transition stream, the deep-class map its class tokens resolve through, the provenance pointers the engine fired while tabulating, and the cells the stream settles into. `joint` on these rows is the trace's `joint_floor` alone, because the prospect-divergence pass runs later, in the crate's fold. `cells` is computed at class grain, which equals the expanded set because a class row's members share its settled fields. `kernel_exec.enumerate_transitions` returns one. No build or tool uses this type, because the crate folds the product it already holds; only the rebuild tests read it."""

    config: str
    transitions: tuple[Transition, ...]
    deep_classes: Mapping[str, tuple[str, ...]]
    cited_provenance: frozenset[str]
    cells: frozenset[CellId]
