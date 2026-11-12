"""The decision-table and join-table data model, and the readers and digests for the table artifacts the crate writes (rebuild/M1-PLAN.md §5, Group 2).

The crate under `rebuild/kernel-rs` builds both tables and nothing here folds. `src/fixpoint.rs` enumerates the windows. `src/fold.rs` and `src/rulefold.rs` hold the prospect-divergence pass, the per-input rule fold, and the join fold, and `src/artifacts.rs` writes the settlement TSV, the join TSV, and the windows payload. `run_m1.build_tables` reads its rules, reachable cells, and join rows back through `read_windows` and `read_join_tsv`. `rebuild/test_table.py` replays the crate's rules against its rows on the mini fixture as an independent check of the fold, and `gate:conform` checks that the compiled font agrees with settlement.

The kernel tabulates settlement over every (settled left, rune, right1, right2) window reachable for one feature configuration, by fixpoint over reachable left states, so the table is exact. Windows that formation makes impossible are left out, except that a ligature's component pair is enumerated where the §5.7 late-formation guard blocks the ligature. A window with a formed ligature as its input or at right1 is enumerated only where that ligature's guard does not block. A ZWNJ-locked entry-bearing input is enumerated under its locked copy's name (`model.locked_glyph_name`).

Rows carry two deep slots, `right3` and `right4`. The kernel splits a window by the third raw token only where both nearer slots are letters and the window's outcome can still depend on that token, and by the fourth token likewise one slot deeper. Elsewhere the slot holds `#NA`. The crate's `deep_slots.rs` and `liveness.rs` decide which windows are split. In the pinned mode set only runes whose window reads a `prefer` or `resolve` record whose right chain reaches the slot can be split: the rune's own records, and the resolve records of any rune whose `against:` names it. Under the simulated prospect or the follower prefer slots every rune can be. Under class grain (`kernel_exec.class_grain`) a deep slot holds a class id (`deep_class_id`) standing for the letters that settle identically there, listed in `deep_classes`. Class rows are expanded back to labels before folding (`expanded_transitions` on this side), and the fold, the joint-flag pass, the join fold, and the rules all read that expanded stream, so `Rule` objects hold labels only. doc/rebuild-design.md §3.4 describes the deep slots. `_assert_window_arity` checks at import that `Transition` and `Rule` have `model.RIGHT_WINDOW_SLOTS` right slots.

`src/rulefold.rs` specifies how each input's rows are compressed into rules and the order of those rules. The crate checks the rules by replaying the rows against them under first-match-wins (`fold::assert_outcome_partition`).

A row is joint when either §6.1 flag applies: the final tiebreak broke a ranking tie between candidates that differ in junction, or the optimistic prospect differs from the follower's settled choice. Both TSV artifacts are diff-stable (§8): a deterministic row order, provenance pointers, and deterministic labels.

The windows artifact stores a built table so the conformance sweep does not rebuild from unchanged sources: the rules, the reachable cells, one certificate per rule, and the enumerated windows, stamped with `fingerprint.tables_value` over the sources the fixpoint read. `read_windows` returns the windows as `Window` rows, labels only, which is all a replay reads. The head alone gives the reachable cells, the witness stage's certificates, the windows the table imported from other configurations, which the string replay's narrowing reads, and where the crate's exchange expects the emitter to ship each rule (`fold_order`, `buckets`), which `emit_gsub._fold_rules` checks against the order it ships. Neither digest in this module reads the certificates, the imports or that expectation, because they are evidence about the rules, not part of what the rules say.

Each configuration's windows artifact is held in two files and the segment format (`rebuild/pipeline/row_delta.py`), all written by `run_m1.build_tables`:

- The head file, `windows-<config>.head.json.gz` (`windows_head_file`): the payload's head line exactly as the crate writes it, the marker, a tab, the JSON head and a newline, gzipped as a base is, so `read_windows` reads it with `windows=False` as it reads a whole payload.
- The rows, the output `windows-<config>.tsv.gz` (`windows_output`): a head, `windows-<config>.tsv.gz.head` (`windows_head_path`), which merges its parts by key (`row_delta.MERGE_KEY`), lists a base, a whole payload as the crate wrote it once, its inline head line included, and the segments of the rows that differ that later builds wrote over it. Its stamps are the build's `inputs`, the crate's `table_digest` over the table's own rows and the head file's name, size and sha256, so the head is the single commit point for both files.

`read_windows_head` is the one reader of a configuration's head: it checks the head file against the head's stamps before it parses it, so a head file a build left without committing its head reads as a missing build, never as a current stamp over old rows. Every reader of the head (`run_m1.serialized_tables` and through it the conformance sweep, `--gates-only`, the artifact cycle's stamp checks and the deep tools, `artifact_cycle.tables_imports_digest` and `deep_sweep.targeted_lines`) goes through it, and `rebuild/test_windows.py`'s contract test fails on any other. The materialized form of the rows (`materialized_windows`) is the head file's line, the column line and the base and segments merged by key: the whole payload the crate would write for the same table, byte for byte (#618's decision 48), which `read_materialized_windows` reads, and `materialized_table_digest` hashes as it streams for `perf_fingerprint` without building the rows. A tree from before the segment format holds the whole payload gzipped in the single file `windows-<config>.tsv.gz` (`windows_path`), which the next build adopts as its output's first base.
"""

from __future__ import annotations

import errno
import gzip
import hashlib
import io
import itertools
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Generator, Iterable, Iterator, Mapping, Protocol

from rebuild.pipeline import row_delta
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
    """A window plus the fields only the fold reads: the settled cells the join table is folded from, the optimistic prospect the joint flag is scored against, and the provenance pointers the rule fold copies onto rules."""

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
class JoinRow:
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
    imports: tuple[tuple[str, ...], ...] = (
        ()
    )  # the windows the crate imported from other configurations (crossconfig.rs), in key order: six labels, the outcome, and the configuration each is live in
    fold_order: tuple[str, ...] = (
        ()
    )  # the build's configurations in the order the crate's exchange (crossconfig.rs) expects `emit_gsub._fold_rules` to visit them; the emitter checks it
    buckets: tuple[int, ...] = (
        ()
    )  # each rule's place in `emit_gsub._ordered_settle_rules`' sort as the crate's exchange computes it (crossconfig.rs `bucket`), in rule order; the emitter checks each
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
        """Return the first member of a class id, else the label itself: one concrete label to put in a deep slot. This is exact for rule-membership tests, because `fold::assert_deep_class_unions` checks that every emitted lookahead class holds all of a token's members or none."""
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
class JoinTable:
    config: str
    rows: tuple[JoinRow, ...] = ()

    def write_tsv(self, path: Path) -> None:
        lines = [f"# join table, config {self.config}", "left\tright\tjunction\textension\tkern"]
        for row in self.rows:
            lines.append("\t".join((row.left, row.right, row.junction, str(row.extension), str(row.kern))))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")


JOIN_COLUMNS = ("left", "right", "junction", "extension", "kern")


def read_join_tsv(path: Path) -> JoinTable:
    """Read a join TSV back: the inverse of `JoinTable.write_tsv`. The kernel's `build-tables` subcommand writes the file, and the build reads it back because deriving the join table again would repeat the fixpoint. Raises OSError when the file is missing and ValueError when it is not a join table in this format."""
    lines = path.read_text().splitlines()
    if not lines or not lines[0].startswith("# join table, config "):
        raise ValueError(f"{path}: not a join table")
    if len(lines) < 2 or tuple(lines[1].split("\t")) != JOIN_COLUMNS:
        raise ValueError(f"{path}: join columns are not {JOIN_COLUMNS}")
    rows = []
    for number, line in enumerate(lines[2:], 3):
        fields = line.split("\t")
        if len(fields) != len(JOIN_COLUMNS):
            raise ValueError(f"{path}: line {number} has {len(fields)} fields, expected {len(JOIN_COLUMNS)}")
        left, right, junction, extension, kern = fields
        rows.append(JoinRow(left, right, junction, int(extension), int(kern)))
    return JoinTable(config=lines[0].removeprefix("# join table, config "), rows=tuple(rows))


WINDOWS_FORMAT = "ams-m1-windows/3"
WINDOWS_COLUMNS = ("input", "left", "lookahead1", "lookahead2", "lookahead3", "lookahead4", "outcome")
WINDOWS_COLUMN_LINE = ("\t".join(WINDOWS_COLUMNS) + "\n").encode()
WINDOWS_MARKER = f"# {WINDOWS_FORMAT}\t".encode()
# The stamps a windows output's head carries (`read_windows_head`).
INPUTS_STAMP = "inputs"
TABLE_DIGEST_STAMP = "table_digest"
HEAD_FILE_STAMP = "head_file"


def windows_output(config: str) -> str:
    """The name of one configuration's windows rows as an output in the segment format (`row_delta`), whose head is `<name>.head` and whose parts are `<name>.<gen>.base` and `<name>.<gen>.<n>.delta`; also the name of the single file a tree from before the segment format holds (`windows_path`)."""
    return f"windows-{config}.tsv.gz"


def windows_path(out_dir: Path, config: str) -> Path:
    """The single file, the whole payload gzipped, that a tree from before the segment format holds for one configuration, and that the next build adopts as its output's first base."""
    return Path(out_dir) / windows_output(config)


def windows_head_path(out_dir: Path, config: str) -> Path:
    """One configuration's windows output's head, the commit point for its rows and its head file."""
    return row_delta.head_path(Path(out_dir), windows_output(config))


def windows_head_file(out_dir: Path, config: str) -> Path:
    """One configuration's head file, its payload's head line gzipped, which the head's `head_file` stamp names."""
    return Path(out_dir) / f"windows-{config}.head.json.gz"


def windows_head(out_dir: Path, config: str) -> row_delta.Head:
    """Read one configuration's windows output's head. Raises `FileNotFoundError` when there is none, and `row_delta.HeadError`, a `ValueError`, when it does not read or does not merge by key."""
    path = windows_head_path(out_dir, config)
    head = row_delta.read_head(Path(out_dir), windows_output(config))
    if head is None:
        raise FileNotFoundError(errno.ENOENT, "no windows head", str(path))
    if head.merge != row_delta.MERGE_KEY:
        raise row_delta.HeadError(f"{path.name} does not merge its parts by key")
    return head


def _head_file_bytes(out_dir: Path, config: str, head: row_delta.Head) -> bytes:
    """Read the head file `head`'s stamps name and check it against them: its name, size and sha256. A head file that differs raises `ValueError`, since the build that wrote it never committed a head over it."""
    recorded = head.stamps.get(HEAD_FILE_STAMP)
    path = windows_head_file(out_dir, config)
    if not isinstance(recorded, dict) or recorded.get("name") != path.name:
        raise ValueError(f"{windows_head_path(out_dir, config).name} names no head file {path.name}")
    raw = path.read_bytes()
    if len(raw) != recorded.get("size") or hashlib.sha256(raw).hexdigest() != recorded.get("sha256"):
        raise ValueError(
            f"{path.name} is not the head file its head records: the build that wrote it never committed"
        )
    return raw


def read_windows_head(out_dir: Path, config: str) -> tuple[str, DecisionTable]:
    """Read one configuration's table from its head file, without the window rows, as `read_windows` reads a payload with `windows=False`: the fingerprint of the sources it was built from and the decision table. The one reader of a configuration's head: it reads the windows output's head and checks the head file against the name, size and sha256 its stamps record before it parses it. Raises OSError when the head or the head file is missing, and ValueError when either does not read, the head file is not the one the head records, or it is not a head line in this format, so a head file a build left without committing reads as a missing build."""
    head = windows_head(out_dir, config)
    path = windows_head_file(out_dir, config)
    raw = _head_file_bytes(out_dir, config, head)
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
        line = stream.readline()
    stamp, decision = _windows_of(_Lines(iter((line,))), False, str(path))
    if stamp != head.stamps.get(INPUTS_STAMP):
        raise ValueError(
            f"{path.name} is stamped {stamp!r} where its head records {head.stamps.get(INPUTS_STAMP)!r}"
        )
    return stamp, decision


_HEADER_SKIP_BYTES = 1 << 16


def check_windows_header(stream: IO[bytes] | gzip.GzipFile, name: str) -> None:
    """Read past a whole payload's head line and column line in `stream`, checking that the first begins with this format's marker and that the second is the column line. The head line, megabytes of JSON, is skipped in reads of at most 64 KiB and never held whole, since no caller needs it and a large line's pieces would stay in the reading process's footprint. A payload that fails either check raises `ValueError`."""
    if stream.read(len(WINDOWS_MARKER)) != WINDOWS_MARKER:
        raise ValueError(f"{name}: not a {WINDOWS_FORMAT} enumeration")
    while (piece := stream.readline(_HEADER_SKIP_BYTES)) and not piece.endswith(b"\n"):
        continue
    if stream.readline(len(WINDOWS_COLUMN_LINE) + 1) != WINDOWS_COLUMN_LINE:
        raise ValueError(f"{name}: window columns are not {WINDOWS_COLUMNS}")


def window_key(row: bytes) -> list[bytes]:
    """A window row's key, its six leading fields, as the list `row_delta.merge_keyed` compares field by field."""
    return row[: row.rindex(b"\t")].split(b"\t")


def _tombstone_key(payload: bytes) -> list[bytes]:
    return payload.rstrip(b"\n").split(b"\t")


def materialized_windows(out_dir: Path, config: str, *, sha256: bool = False) -> Generator[bytes, None, None]:
    """Return one configuration's windows output's materialized form as its lines: the head file's line, the column line, and the base's rows merged by key with its segments' (`row_delta.merge_keyed`), which is the whole payload the crate writes for the same table, byte for byte. A head with no segment streams the base's rows after the head file's line. The head is read and every part it lists opened before this returns (`row_delta.open_parts`, which checks each part's size, and with `sha256` its digest too), so a later build's deletes cannot reach the parts; the returned generator closes them when it ends or is closed. Raises what `windows_head` and `read_windows_head` raise, and `row_delta.HeadError` for a part that is missing or differs from its head."""
    head = windows_head(out_dir, config)
    head_line = gzip.decompress(_head_file_bytes(out_dir, config, head))
    opened = row_delta.open_parts(Path(out_dir), head, sha256=sha256)
    output = windows_output(config)

    def lines() -> Generator[bytes, None, None]:
        try:
            yield head_line
            yield WINDOWS_COLUMN_LINE
            with gzip.GzipFile(fileobj=opened.handles[0], mode="rb") as base:
                check_windows_header(base, head.base.name)
                segments = [
                    row_delta.keyed_segment_rows(handle, output, head.generation, number)
                    for number, handle in enumerate(opened.handles[1:], start=1)
                ]
                yield from row_delta.merge_keyed(base, segments, window_key, _tombstone_key)
        finally:
            opened.close()

    return lines()


def read_materialized_windows(
    out_dir: Path, config: str, *, sha256: bool = False
) -> tuple[str, DecisionTable]:
    """Read one configuration's table with its window rows from its windows output's materialized form (`materialized_windows`), as `read_windows` reads a whole payload."""
    return _windows_of(
        _Lines(materialized_windows(out_dir, config, sha256=sha256)),
        True,
        str(windows_head_path(out_dir, config)),
    )


class _LineSource(Protocol):
    def readline(self) -> str: ...

    def __iter__(self) -> Iterator[str]: ...


class _Lines:
    """Byte lines read as the text stream `_windows_of` reads: `readline`, then iteration over the rest."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = iter(lines)

    def readline(self) -> str:
        return next(self._lines, b"").decode("utf-8")

    def __iter__(self) -> Iterator[str]:
        return (line.decode("utf-8") for line in self._lines)


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
    """Read a whole windows payload back: return the fingerprint of the sources the table was built from, and the table with `Window` rows as its transitions. The crate's `artifacts::write_windows` writes the payload and `run_m1.build_tables` gzips it, so the format is defined on both sides and `WINDOWS_FORMAT` is where a mismatch is caught. `windows=False` stops after the head, so a caller that wants only the rules and the reachable cells reads one line and the rest of the gzip stream is never decompressed; the head file `run_m1.build_tables` keeps is that one line, gzipped. Raises OSError when the file is missing and ValueError when it is not an enumeration in this format. A caller that must decide whether to trust the artifact compares the returned fingerprint itself. A configuration's table on disk is read through `read_windows_head` or `read_materialized_windows`, which read its head and check what it records.

    A path is opened as gzip. An open text stream is read as is, which lets the build read the head line it kept of the plain payload the kernel wrote without compressing it first.
    """
    if isinstance(source, Path):
        with gzip.open(source, "rt") as handle:
            return _windows_of(handle, windows, str(source))
    return _windows_of(source, windows, str(getattr(source, "name", source)))


def _windows_of(handle: IO[str] | _LineSource, windows: bool, name: str) -> tuple[str, DecisionTable]:
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
        imports=tuple(tuple(fields) for fields in head.get("imports", ())),
        fold_order=tuple(head.get("fold_order", ())),
        buckets=tuple(head.get("buckets", ())),
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


def table_digest(decision: DecisionTable, joins: JoinTable) -> str:
    """Return a hash of everything one configuration's build produces: the ordered rules with their provenance and joint flags, every stored window row, the join rows, the reachable cells, the cited provenance, and the identity-guard count. Two builds that should agree, such as before and after a port or a refactor, are compared with this digest. `windows_digest` is narrower: it leaves out the join rows, the cells, the cited provenance, and the guard count, so it answers only whether the settlement rows changed. The deep-class map is not hashed separately, because class ids are content-addressed, so a changed member set changes the ids in the rows that cite it."""
    rows = (
        "\t".join(
            (row.input_glyph, row.left, row.right1, row.right2, row.right3, row.right4, row.outcome)
        ).encode()
        + b"\n"
        for row in decision.transitions
    )
    h = hashlib.sha256()
    for part in _table_digest_parts(decision, joins, rows):
        h.update(part)
    return h.hexdigest()


def materialized_table_digest(out_dir: Path, config: str, joins: JoinTable) -> str:
    """Return `table_digest` of one configuration's table as its windows output's materialized form holds it (`materialized_windows`, every part's size and sha256 checked against its head), without building its `Window` rows: the rules, cells, cited provenance and guard count from the head line, as `read_windows` reads them, and each row line hashed as it streams, checked to hold the seven fields `_windows_of` splits, since a row's digest line is those fields joined by tabs. Raises what `materialized_windows` raises, and `ValueError` for a head line or column line `_windows_of` refuses and for a row of another width."""
    name = str(windows_head_path(out_dir, config))
    lines = materialized_windows(out_dir, config, sha256=True)
    try:
        _, decision = _windows_of(_Lines(itertools.islice(lines, 1)), False, name)
        if tuple(next(lines, b"").decode("utf-8").rstrip("\n").split("\t")) != WINDOWS_COLUMNS:
            raise ValueError(f"{name}: window columns are not {WINDOWS_COLUMNS}")
        h = hashlib.sha256()
        for part in _table_digest_parts(decision, joins, _row_digest_lines(lines, name)):
            h.update(part)
        return h.hexdigest()
    finally:
        lines.close()


def _row_digest_lines(lines: Iterable[bytes], name: str) -> Iterator[bytes]:
    """Yield each window row line of a materialized payload as `table_digest` hashes the row `_windows_of` makes of it: its seven tab-separated fields, decoded as `_windows_of` decodes them, joined by tabs and ended with a newline."""
    width = len(WINDOWS_COLUMNS)
    for line in lines:
        fields = line.decode("utf-8").rstrip("\n").split("\t")
        if len(fields) != width:
            raise ValueError(f"{name}: a window row has {len(fields)} fields, expected {width}")
        yield "\t".join(fields).encode() + b"\n"


def _table_digest_parts(decision: DecisionTable, joins: JoinTable, rows: Iterable[bytes]) -> Iterator[bytes]:
    """Yield what `table_digest` hashes, in order, with `rows` as the window rows' lines."""
    yield f"config\t{decision.config}\n".encode()
    for rule in decision.rules:
        yield (
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
    yield b"--windows--\n"
    yield from rows
    yield b"--joins--\n"
    for join_row in joins.rows:
        yield (
            "\t".join(
                (
                    join_row.left,
                    join_row.right,
                    join_row.junction,
                    str(join_row.extension),
                    str(join_row.kern),
                )
            ).encode()
            + b"\n"
        )
    yield b"--cells--\n"
    for cell in sorted(decision.reachable_cells(), key=_cell_key):
        yield f"{cell.rune}\t{cell.stance}\t{cell.entry}\t{cell.exit}\t{cell.adjustments}\n".encode()
    yield b"--provenance--\n"
    for pointer in sorted(decision.cited_provenance):
        yield pointer.encode() + b"\n"
    yield f"--guards--\t{decision.identity_guard_rules}\n".encode()


@dataclass(frozen=True)
class FixpointProduct:
    """Everything one configuration's fixpoint produces that a fold reads: the key-sorted transition stream, the deep-class map its class tokens resolve through, the provenance pointers the engine fired while tabulating, and the cells the stream settles into. `joint` on these rows is the trace's `joint_tiebreak` alone, because the prospect-divergence pass runs later, in the crate's fold. `cells` is computed at class grain, which equals the expanded set because a class row's members share its settled fields. `kernel_exec.enumerate_transitions` returns one. No build or tool uses this type, because the crate folds the product it already holds; only the rebuild tests read it."""

    config: str
    transitions: tuple[Transition, ...]
    deep_classes: Mapping[str, tuple[str, ...]]
    cited_provenance: frozenset[str]
    cells: frozenset[CellId]
