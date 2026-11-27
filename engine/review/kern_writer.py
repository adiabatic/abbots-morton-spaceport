"""The kern editor's block writer and journal (engine/KERN-PLAN.md section 6), which the review server's write routes run (`engine/review/kern_routes.py`). Nothing here loads the spec or resolves a value: the routes decide what a block holds, and this module turns records into text, text into records, and keeps the record of every write.

**The block.** A rune file's `kern:` block runs from the line that opens with `kern:` to the end of the file, `kern` being the file's last key (`spec_load`'s lint); `split_file` cuts a file there, and `splice` puts a new block after the bytes before it, which it never reads past, so every byte before the `kern:` line is the file's own by construction. A file with no block gets one appended, and a block whose last record goes takes its `kern:` key with it, so the file is byte for byte what it was before its first record. The writer refuses (`BlockRefused`) a file that does not end on a newline before its block.

**The form.** `render_block` writes the editor's form, the one `tools/reflow_yaml.py` keeps for a rune's `kern:` key: a block list with one flow-style record per line, through ruamel with the reflow tool's settings, so a reflow pass leaves the writer's output as it is. A new string (a `why`) that YAML 1.1, which the spec loader reads, would read as something else than the string written plain (`yes`, `null`, `12`, `a: b`) is written double-quoted (`_scalar`), and every rendered block is read back with the loader's YAML to check that it says what the records say.

**Reading a block back.** `parse_block` reads a block into its records, each a ruamel mapping that keeps its own key order and quoting, so a record the writer does not change is written back byte for byte: a hand-written record with a `class`, a family list, an `except`, a `bitmap` or an `entry` and its `why` among them. It refuses a block it cannot write back exactly, with a message that says what to fix by hand: a comment or a blank line inside the block, a multi-line string, anything after the list, a value the loader's YAML 1.1 reads differently from ruamel's YAML 1.2, and a record not written as one flow mapping on a line of its own in the form `render_block` gives.

**Canonical order.** `canonical` sorts a block's records by follower, then from broader to narrower: a record naming no family (a `class` alone, or nothing) first, then by the code points of the first follower it names, so a ligature comes after its lead (`kern.rune_order_key`); within one follower, by how many keys narrow it (own-side `stance`, `entry` and `bitmap`, follower `stance`, `exit` and `bitmap`, and an `except`), a family list before a single family, and then by the record's own line.

**Scopes.** An edit names one record by its scope (`Scope`): the letters scope of a leading rune and a following rune is the record whose keys are only `right: {family: <following rune>}`, and a facing pair's scope is the record whose own-side keys and `right` keys are exactly the two sides' keys, read from the side names `kern.Side.name` spells (`parse_side`): a leading side's stance, `entry` state and bound drawing, and a following side's rune as `family`, its stance, `exit` state and bound drawing. A record with a `class`, a family list or an `except` is never a scope's record, so the editor never rewrites one. When a facing pair's two sides are whole runes, its scope and the letters scope are one record.

**The journal** (`Journal`) is `var/kern/journal.ndjson` beside `var/kern/journal.lock`, which every append holds. Each line is one event, numbered by `seq`:

- `applied`: a delta written to a rune file, with the rune, its `file`, the whole block text `before` and `after` and their sha256s, the `delta` as the route took it, the time, and `pending` when it was a pending delta's.
- `undo`: the revert of the `applied` event `undoes` names, with the same fields.
- `pending`: a request a running pass kept from the file (`request`, a delta or an undo), with the rune a delta edits.
- `dropped`: a pending request that no longer applied when the pass ended, or whose application raised, with its `reason`; `unchanged` closes a pending request that changed nothing, and `cancelled` one an undo took back before it was written.

`JournalState` folds the events: the pending requests in order, the dropped ones, the `applied` events no `undo` has reverted, the newest of which is the one an undo may revert, and what settled each pending request. The fold reads only what was appended since its last read, and starts over when the file is replaced or cut. An append first ends the file on a newline, and the fold skips a line that is not a whole JSON object, so a torn append loses only itself.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from engine.pipeline import kern
from engine.pipeline.model import KernFollower, KernRecord, Provenance
from engine.review import store_lock

KERN_LINE = "kern:"
JOURNAL_NAME = "journal.ndjson"
JOURNAL_LOCK_NAME = "journal.lock"
JOURNAL_LOCK_TIMEOUT_S = 10.0
DROPPED_KEPT = 20  # the newest dropped requests the fold keeps, which `GET /kern` reports
SETTLING_EVENTS = (
    "applied",
    "undo",
    "dropped",
    "unchanged",
    "cancelled",
)  # the events that close a pending request
OWN_KEYS = ("stance", "entry", "bitmap")
NARROWING_FOLLOWER_KEYS = ("stance", "exit", "bitmap")
LETTERS = "letters"
FACING = "facing"
SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_SIDE_NAME = re.compile(r"^([A-Za-z0-9_]+)(?:\[([^\[\]]*)\])?$")

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 1 << 30
_yaml.indent(mapping=2, sequence=2, offset=0)
# A ruamel `YAML` instance keeps its parser's and emitter's state on itself, so two threads using it at once corrupt each other's work; the writer thread and `GET /kern` (`kern_routes.block_refusal`) both reach it.
_yaml_lock = threading.Lock()


class BlockRefused(ValueError):
    """The writer cannot write this block, or this edit to it, back exactly; the message says what to fix by hand."""


class JournalBusy(RuntimeError):
    """Another process kept the journal's lock past `JOURNAL_LOCK_TIMEOUT_S`."""


def split_file(text: str) -> tuple[str, str]:
    """Return a rune file's text before its `kern:` line and its `kern:` block, the text from that line to the end of the file, which is empty when the file has none."""
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.startswith(KERN_LINE):
            return text[:offset], text[offset:]
        offset += len(line)
    return text, ""


def line_ending(text: str) -> str:
    """Return the line ending a file uses, `\\r\\n` when its first line ends with one and `\\n` otherwise, which the writer gives every line of a block it writes there."""
    index = text.find("\n")
    return "\r\n" if index > 0 and text[index - 1] == "\r" else "\n"


def read_text(path: Path) -> str:
    """Return a file's text as its bytes hold it, with no newline translation, so a CRLF file's bytes before its block are written back as they were."""
    return Path(path).read_bytes().decode("utf-8")


def replace_text(path: Path, text: str, staging_dir: Path) -> None:
    """Put `text` in place of `path` atomically: a staging file in `staging_dir`, outside version control, given the file's mode and renamed over it, so nothing but the rune file itself is ever written under `glyph_data/`."""
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(prefix=f"{Path(path).name}.", suffix=".partial", dir=staging_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staging, stat.S_IMODE(Path(path).stat().st_mode))
        os.replace(staging, path)
    except BaseException:
        Path(staging).unlink(missing_ok=True)
        raise


def block_sha(block: str) -> str:
    return hashlib.sha256(block.encode()).hexdigest()


def splice(prefix: str, block: str, file: str) -> str:
    """Return the file whose bytes before its `kern:` line are `prefix` and whose block is `block` (none when it is empty)."""
    if block and prefix and not prefix.endswith("\n"):
        raise BlockRefused(
            f"{file} does not end on a newline, so a kern block cannot be appended to it; end its last line and try again"
        )
    return prefix + block


def plain(node: Any) -> Any:
    """Return a parsed YAML node as plain Python data: dicts, lists, and the scalars' own types."""
    if isinstance(node, dict):
        return {str(key): plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [plain(item) for item in node]
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, int):
        return int(node)
    if isinstance(node, str):
        return str(node)
    return node


def _has_comments(node: Any) -> bool:
    if isinstance(node, (CommentedMap, CommentedSeq)):
        if node.ca.comment or node.ca.items:
            return True
        children = node.values() if isinstance(node, CommentedMap) else node
        return any(_has_comments(child) for child in children)
    return False


def _multiline(node: Any) -> bool:
    if isinstance(node, dict):
        return any(_multiline(value) for value in node.values())
    if isinstance(node, list):
        return any(_multiline(item) for item in node)
    return isinstance(node, str) and "\n" in node


def _dump(records: Sequence[CommentedMap] | None) -> str:
    top = CommentedMap()
    if records is None:
        top["kern"] = None
    else:
        seq = CommentedSeq(records)
        seq.fa.set_block_style()
        for record in records:
            record.fa.set_flow_style()
        top["kern"] = seq
    buffer = StringIO()
    with _yaml_lock:
        _yaml.dump(top, buffer)
    return buffer.getvalue()


def render_block(records: Sequence[CommentedMap], newline: str = "\n") -> str:
    """Return the block that holds `records` in their order, in the editor's form with each line ending in `newline`, or the empty string for none. The block is read back with the spec loader's YAML, and one that does not say what the records say raises `BlockRefused`."""
    if not records:
        return ""
    text = _dump(records)
    try:
        back = yaml.load(text, Loader=SAFE_LOADER)
    except yaml.YAMLError as error:
        raise BlockRefused(f"the written kern block does not read back ({error})") from None
    if not isinstance(back, dict) or back.get("kern") != [plain(record) for record in records]:
        raise BlockRefused("the written kern block reads back as other records than the ones written")
    return text.replace("\n", newline)


def parse_block(block: str, file: str, newline: str = "\n") -> list[CommentedMap]:
    """Return the records of `block`, whose lines end in `newline`, as ruamel mappings that keep their key order and quoting, or raise `BlockRefused` naming what keeps the writer from writing the block back exactly. An empty block has none."""
    if not block:
        return []
    written = block
    block = block.replace("\r\n", "\n")
    if "\r" in block or block.replace("\n", newline) != written:
        raise BlockRefused(
            f"the kern block of {file} does not end each of its lines the way the file's first line ends; give them one line ending by hand"
        )
    try:
        with _yaml_lock:
            top = _yaml.load(block)
    except YAMLError as error:
        raise BlockRefused(
            f"the kern block of {file} does not parse on its own ({type(error).__name__}); fix it by hand"
        ) from None
    if not isinstance(top, CommentedMap) or list(top) != ["kern"]:
        raise BlockRefused(
            f"{file} has keys after kern:, which must be the file's last key; move them above it"
        )
    raw = top["kern"]
    if raw is not None and not isinstance(raw, CommentedSeq):
        raise BlockRefused(f"the kern: key of {file} is not a list of records; fix it by hand")
    if _has_comments(top):
        raise BlockRefused(
            f"the kern block of {file} holds a comment or a blank line, which the editor cannot keep; move a rationale into the record's why: and delete the comment or the blank line by hand"
        )
    entries = list(raw or ())
    if not all(isinstance(entry, CommentedMap) for entry in entries):
        raise BlockRefused(f"the kern block of {file} holds an entry that is not a record; fix it by hand")
    records: list[CommentedMap] = [entry for entry in entries if isinstance(entry, CommentedMap)]
    if _multiline(plain(records)):
        raise BlockRefused(
            f"the kern block of {file} holds a multi-line string; make each record's why: one line by hand"
        )
    try:
        loaded = yaml.load(block, Loader=SAFE_LOADER)
    except yaml.YAMLError:
        loaded = None
    if not isinstance(loaded, dict) or (loaded.get("kern") or []) != plain(records):
        raise BlockRefused(
            f"the kern block of {file} holds a value the spec loader reads differently from the editor's YAML (a yes, no, on or off, or a number written oddly); quote it by hand"
        )
    if _dump(records if raw is not None else None) != block:
        raise BlockRefused(
            f"the kern block of {file} is not one flow-style record per line; run `uv run python tools/reflow_yaml.py {file}` and check the result"
        )
    return records


def _follower_order(data: Mapping[str, Any], order_key: Callable[[str], tuple[int, ...]]) -> tuple:
    family = data.get("right", {}).get("family")
    families = [family] if isinstance(family, str) else list(family or ())
    if not families:
        return (0,)
    return (1, order_key(families[0]))


def _narrowness(data: Mapping[str, Any]) -> int:
    right = data.get("right", {})
    count = sum(key in data for key in OWN_KEYS)
    count += sum(key in right for key in NARROWING_FOLLOWER_KEYS)
    return count + ("except" in right)


def canonical(
    records: Iterable[CommentedMap], order_key: Callable[[str], tuple[int, ...]]
) -> list[CommentedMap]:
    """Return `records` in the block's canonical order, as the module docstring says. `order_key` gives a rune's code-point key (`kern.rune_order_key`)."""

    def key(record: CommentedMap) -> tuple:
        data = plain(record)
        family = data.get("right", {}).get("family")
        single = isinstance(family, str)
        return (_follower_order(data, order_key), _narrowness(data), single, record_line(record))

    return sorted(records, key=key)


def _scalar(value: str) -> str:
    """Return `value` as the writer puts it in a record: plain, or double-quoted when YAML 1.1 would read it plain as something else."""
    try:
        read = yaml.load(value, Loader=SAFE_LOADER)
    except yaml.YAMLError:
        read = None
    return value if read == value and isinstance(read, str) else DoubleQuotedScalarString(value)


@dataclass(frozen=True)
class Side:
    """One side as a side name spells it (`kern.Side.name`): its rune and the keys that narrow it."""

    rune: str
    stance: str | None = None
    state: str | None = None
    bitmap: str | None = None


def parse_side(name: str, facing: str) -> Side:
    """Read a side name, `qsNo`, `qsNo[flipped]`, `qsGay[sole entry=baseline]`, or `qsTea[half exit=x-height bitmap=padded-left-1]`, on the `facing` (`kern.LEADING` or `kern.FOLLOWING`) side. Raises `ValueError` for a name that is not one."""
    match = _SIDE_NAME.match(name)
    if match is None:
        raise ValueError(f"{name!r} is not a side name")
    rune, inside = match.group(1), match.group(2)
    state_key = "entry" if facing == kern.LEADING else "exit"
    stance = state = bitmap = None
    for index, token in enumerate((inside or "").split()):
        key, has_value, value = token.partition("=")
        if not has_value:
            if index != 0:
                raise ValueError(f"{name!r} names its stance after another key")
            stance = token
        elif key == state_key and state is None:
            state = value
        elif key == "bitmap" and bitmap is None:
            bitmap = value
        else:
            raise ValueError(f"{name!r} has a key a {facing} side does not take ({key})")
    if inside is not None and stance is None and state is None and bitmap is None:
        raise ValueError(f"{name!r} has empty brackets")
    return Side(rune, stance, state, bitmap)


@dataclass(frozen=True)
class Scope:
    """The record an edit names: the leading rune whose file holds it, its own-side keys, and its `right` keys, each only where it is set."""

    kind: str
    rune: str
    own: tuple[tuple[str, str], ...]
    right: tuple[tuple[str, str], ...]

    @property
    def follow_rune(self) -> str:
        return dict(self.right)["family"]

    @property
    def label(self) -> str:
        own = dict(self.own)
        lead = kern.Side(self.rune, kern.LEADING, own.get("stance"), own.get("entry"), own.get("bitmap")).name
        right = dict(self.right)
        follow = kern.Side(
            right["family"], kern.FOLLOWING, right.get("stance"), right.get("exit"), right.get("bitmap")
        ).name
        return f"{lead} | {follow}"

    def matches(self, record: Mapping[str, Any]) -> bool:
        data = plain(record)
        own = {key: data[key] for key in OWN_KEYS if key in data}
        return own == dict(self.own) and data.get("right") == dict(self.right)

    def record(self, by: int, why: str | None) -> CommentedMap:
        """Return a new record of this scope with `by` and, when given, `why`, in the canonical key order."""
        node = CommentedMap()
        for key, value in self.own:
            node[key] = _scalar(value)
        right = CommentedMap()
        for key, value in self.right:
            right[key] = _scalar(value)
        right.fa.set_flow_style()
        node["right"] = right
        node["by"] = by
        if why:
            node["why"] = _scalar(why)
        return node


def letters_scope(lead: str, follow: str) -> Scope:
    return Scope(LETTERS, lead, (), (("family", follow),))


def facing_scope(lead: str, follow: str) -> Scope:
    """Return the scope of the facing pair whose sides are named `lead` and `follow`. Raises `ValueError` when either is not a side name."""
    leading = parse_side(lead, kern.LEADING)
    following = parse_side(follow, kern.FOLLOWING)
    own = tuple(
        (key, value)
        for key, value in (("stance", leading.stance), ("entry", leading.state), ("bitmap", leading.bitmap))
        if value is not None
    )
    right = tuple(
        (key, value)
        for key, value in (
            ("family", following.rune),
            ("stance", following.stance),
            ("exit", following.state),
            ("bitmap", following.bitmap),
        )
        if value is not None
    )
    return Scope(FACING, leading.rune, own, right)


def scope_index(records: Sequence[CommentedMap], scope: Scope, file: str) -> int | None:
    """Return the index of the scope's record in `records`, None when it has none, and raise `BlockRefused` when several records are the scope's."""
    found = [index for index, record in enumerate(records) if scope.matches(record)]
    if len(found) > 1:
        raise BlockRefused(
            f"{file} holds {len(found)} records for {scope.label}; merge them into one by hand"
        )
    return found[0] if found else None


def record_line(record: CommentedMap) -> str:
    """Return the line `render_block` writes for the record, without its newline."""
    return _dump([copy.deepcopy(record)]).splitlines()[1]


def with_value(record: CommentedMap, by: int, why: str | None) -> CommentedMap:
    """Return a copy of `record` with `by` and, when `why` is not None, its `why` set (an empty string removes it), every other key as written."""
    node = copy.deepcopy(record)
    node["by"] = by
    if why is not None:
        if why:
            node["why"] = _scalar(why)
        elif "why" in node:
            del node["why"]
    return node


def _follower(raw: Mapping[str, Any]) -> KernFollower:
    family = raw.get("family")
    return KernFollower(
        family=(family,) if isinstance(family, str) else tuple(family or ()),
        klass=raw.get("class"),
        stance=raw.get("stance"),
        exit=raw.get("exit"),
        bitmap=raw.get("bitmap"),
        except_=tuple(_follower(item) for item in raw.get("except", ())),
    )


def kern_record(rune: str, record: Mapping[str, Any], provenance: Provenance) -> KernRecord:
    """Return the `KernRecord` the spec loader builds from one record of `rune`'s block, so the routes can resolve a block before it is written."""
    data = plain(record)
    return KernRecord(
        rune=rune,
        right=_follower(data["right"]),
        by=data["by"],
        stance=data.get("stance"),
        entry=data.get("entry"),
        bitmap=data.get("bitmap"),
        why=data.get("why"),
        provenance=provenance,
    )


def record_sides(rune: str, record: Mapping[str, Any]) -> list[str] | None:
    """Return the two side names whose facing pair's scope is this record of `rune`'s block, as written, or None for a record with a `class`, a family list (one of one family among them) or an `except`, which is no scope's."""
    data = plain(record)
    right = data.get("right") if isinstance(data, dict) else None
    if (
        not isinstance(right, dict)
        or not isinstance(right.get("family"), str)
        or "class" in right
        or "except" in right
    ):
        return None
    return [
        kern.Side(rune, kern.LEADING, data.get("stance"), data.get("entry"), data.get("bitmap")).name,
        kern.Side(
            right["family"], kern.FOLLOWING, right.get("stance"), right.get("exit"), right.get("bitmap")
        ).name,
    ]


def record_narrow(record: KernRecord) -> bool:
    """Return whether the record names any key that narrows it below the two letters: an own-side `stance`, `entry` or `bitmap`, or a follower's `stance`, `exit` or `bitmap`."""
    right = record.right
    return any(
        value is not None
        for value in (record.stance, record.entry, record.bitmap, right.stance, right.exit, right.bitmap)
    )


def now_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class JournalState:
    """The journal folded: the last `seq`; the pending requests by `seq`, in order; the newest dropped ones; each `applied` event's offset in the file; the `applied` events no `undo` has reverted, oldest first (`stack`); and, for each pending request no longer pending, the event that settled it (`settled`, its kind, its `seq` and a drop's reason)."""

    seq: int = 0
    pending: dict[int, dict] = field(default_factory=dict)
    dropped: list[dict] = field(default_factory=list)
    applied: dict[int, int] = field(default_factory=dict)
    undone: set[int] = field(default_factory=set)
    stack: list[int] = field(default_factory=list)
    settled: dict[int, dict] = field(default_factory=dict)

    def fold(self, event: dict, offset: int) -> None:
        seq = event.get("seq")
        if not isinstance(seq, int):
            return
        self.seq = max(self.seq, seq)
        kind = event.get("event")
        closes = event.get("pending")
        if isinstance(closes, int) and kind in SETTLING_EVENTS:
            self.pending.pop(closes, None)
            self.settled[closes] = {"event": kind, "seq": seq, "reason": event.get("reason")}
        if kind == "applied":
            self.applied[seq] = offset
            self.stack.append(seq)
        elif kind == "undo" and isinstance(event.get("undoes"), int):
            undoes = event["undoes"]
            self.undone.add(undoes)
            if self.stack and self.stack[-1] == undoes:
                self.stack.pop()
            elif undoes in self.stack:
                self.stack.remove(undoes)
        elif kind == "pending":
            self.pending[seq] = event
        elif kind == "dropped":
            self.dropped = [*self.dropped[-(DROPPED_KEPT - 1) :], event]

    def undo_target(self) -> int | None:
        """Return the `seq` of the newest `applied` event no `undo` has reverted, or None."""
        return self.stack[-1] if self.stack else None


class Journal:
    """The kern editor's journal under `directory` (`var/kern/`), as the module docstring says. One instance serves a process; its methods are safe across its threads, and each reads only what was appended since the last read, so its cost does not grow with the journal. `id` names the file the fold read (its inode and the digest of its first line), so a reader that holds a `seq` from a journal since deleted or replaced can tell its `seq`s started over."""

    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / JOURNAL_NAME
        self.lock_path = Path(directory) / JOURNAL_LOCK_NAME
        self._mutex = threading.Lock()
        self._state = JournalState()
        self._read_to = 0
        self._inode: int | None = None
        self._id: str | None = None

    def _refresh(self) -> JournalState:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._state, self._read_to, self._inode, self._id = JournalState(), 0, None, None
            return self._state
        if stat.st_ino != self._inode or stat.st_size < self._read_to:
            self._state, self._read_to, self._inode, self._id = JournalState(), 0, stat.st_ino, None
        if stat.st_size == self._read_to:
            return self._state
        with self.path.open("rb") as handle:
            handle.seek(self._read_to)
            offset = self._read_to
            for line in handle:
                if not line.endswith(b"\n"):
                    break
                if offset == 0:
                    self._id = f"{stat.st_ino}:{hashlib.sha256(line).hexdigest()[:16]}"
                try:
                    event = json.loads(line)
                except ValueError:
                    event = None
                if isinstance(event, dict):
                    self._state.fold(event, offset)
                offset += len(line)
            self._read_to = offset
        return self._state

    def summary(self) -> dict:
        """Return what `GET /kern` reports of the journal: its `id`, its last `seq`, how many requests are pending, and the newest dropped ones, newest first."""
        with self._mutex:
            state = self._refresh()
            return {
                "id": self._id,
                "seq": state.seq,
                "pending": len(state.pending),
                "dropped": [
                    {
                        "seq": event.get("seq"),
                        "time": event.get("time"),
                        "pending": event.get("pending"),
                        "reason": event.get("reason", ""),
                        "request": event.get("request"),
                    }
                    for event in reversed(state.dropped)
                ],
            }

    def pending_items(self) -> list[tuple[int, dict]]:
        """Return the pending requests by `seq`, in order."""
        with self._mutex:
            return list(self._refresh().pending.items())

    def pending_count(self) -> int:
        with self._mutex:
            return len(self._refresh().pending)

    def is_pending(self, seq: int) -> bool:
        with self._mutex:
            return seq in self._refresh().pending

    def settled(self, seq: int) -> dict | None:
        """Return the event that settled the pending request `seq` (its kind, its `seq` and a drop's reason), or None while it is pending or was never one."""
        with self._mutex:
            found = self._refresh().settled.get(seq)
            return dict(found) if found is not None else None

    def undo_target(self) -> int | None:
        with self._mutex:
            return self._refresh().undo_target()

    def applied(self, seq: int) -> dict | None:
        """Return the `applied` event `seq` as written, or None when there is none, and whether an `undo` reverted it under `undone`."""
        with self._mutex:
            state = self._refresh()
            offset = state.applied.get(seq)
            undone = seq in state.undone
        if offset is None:
            return None
        return {**self._event_at(offset), "undone": undone}

    @contextmanager
    def _held(self) -> Iterator[None]:
        try:
            with store_lock.hold_flock(self.lock_path, timeout=JOURNAL_LOCK_TIMEOUT_S):
                yield
        except store_lock.LockBusy:
            raise JournalBusy(f"another process holds {self.lock_path}") from None

    def _event_at(self, offset: int) -> dict:
        with self.path.open("rb") as handle:
            handle.seek(offset)
            return json.loads(handle.readline())

    def append(self, event: dict) -> dict:
        """Append `event` with the next `seq` and the time, under the journal's lock, and return it as written. Raises `JournalBusy` when another holder keeps the lock past `JOURNAL_LOCK_TIMEOUT_S`."""
        with self._mutex, self._held():
            state = self._refresh()
            written = {"seq": state.seq + 1, "time": now_stamp(), **event}
            line = json.dumps(written, ensure_ascii=False).encode() + b"\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as handle:
                if handle.tell() > 0:
                    with self.path.open("rb") as tail:
                        tail.seek(-1, os.SEEK_END)
                        if tail.read(1) != b"\n":
                            handle.write(b"\n")
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self._refresh()
            return written
