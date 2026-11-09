"""The segment format: an output held as a base and segments under a head, so a pass writes the rows it changes instead of the whole file. The review corpus's unit store (`rebuild/review/unit_cache.py`) and each settlement configuration's windows rows (`windows-<config>.tsv.gz`, `run_m1.build_tables`, read through `table.read_windows_head` and `table.materialized_windows`) take it, and #618's changes 30 to 32 take it for the table side's other outputs.

For an output named `<name>`, its file name (`unit-cache.ndjson.gz`), the directory holds three kinds of file, each written through `output_sink.replace_output`:

- The head, `<name>.head`, one JSON document: the output's generation, its content generation, its stamps, and every part with its size, its sha256, its rows and its row bytes (the rows' uncompressed bytes). It is renamed into place last, so it is the single commit point. Parts are referenced only from the head: a part no head lists, left by a failed pass, is ignored by every reader and deleted by the next writer (`remove_unlisted`), and a generation the head no longer names is deleted once the head that replaced it is in place (`commit`). A staging file a writer killed mid-write leaves under a part's name, which no later part takes, is deleted by the windows rows' next writer before it writes (`remove_staging`).
- The base, `<name>.<gen>.base`: the output's own file byte for byte, its inline header and stamps included. The head's stamps override the inline ones, so a pass that moves a stamp and changes no row writes the head alone.
- Segments, `<name>.<gen>.<n>.delta`, numbered from 1 within their generation, at most one a writer step. A segment is gzipped lines: a header naming the format, the output, the generation, the segment's number and, for an output that merges by key, `"merge": "key"` (`segment_header`), then rows, each an upsert or a tombstone of one row in the base's own row encoding. A row in a later part supersedes every earlier part's row at its place or key: an upsert replaces it, and a tombstone removes it.

An output merges its parts one of two ways, which its head names (`Head.merge`) and every segment header repeats, so a reader refuses a segment written for the other (`HeadError`).

By place, the unit store's, since its base need not be in key order (it is in triage order). A writer names every row it supersedes by its place: a row whose content changed is an upsert at its own place, a row removed is a tombstone at its place, and a row that moves is a tombstone at its old place and an upsert at its new one (`write_segment`). Every row has a place (`Place`): base row `i` is `(i, "")`, and a segment row names the base row it follows and a fraction after it, `(i, f)` with `f` above `""`, or `(-1, f)` before the first. `places_between` cuts as many places as a run of rows needs between two others, and a segment's rows are sorted by place. A place indexes its generation's base, so a segment is a positional encoding: its header names its generation and number, and a reader refuses a segment whose generation is not the head's or whose number is not its place in the head's list (`HeadError`), so a reader that pairs an old head with a refolded base fails loudly instead of misplacing rows.

Readers (`merge_placed`) merge the base and the segments as streams by place, and never hold a merged view: each part is read once, front to back, so a reader holds a buffer a part. At each place the row of the latest part that has one there is live, unless it is a tombstone.

By key (`MERGE_KEY`), the windows rows', whose base is in key order with one row a key, as the crate writes it. A segment's rows are in key order too: `+\t<row>` upserts a row at its key and `-\t<key>` removes the row at a key, where a key is a row's leading fields, as the output defines them. The crate's `build-tables` merges a configuration's rows against its previous parts and writes only the rows that differ, and its `replay-emitted` walks the merged rows (`rebuild/kernel-rs/src/row_merge.rs`); here `merge_keyed` merges them for a materialized form. At each key the row of the latest part that has one there is live, unless it is a tombstone, and a part whose keys do not strictly ascend is refused. A reader opens every part the head lists when it reads the head (`open_parts`), so a later writer's deletes cannot reach it, since an open descriptor survives the unlink. `open_parts` checks each part's size against the head, as a reader that seeks needs, and with `sha256=True` its digest too, as the writer that merges a part and `perf_fingerprint` do, since they read every byte anyway.

The materialized form of an output is its head, base and segments merged and re-serialized into the base's layout under the head's stamps: what a writer of the whole file would have written at this content. A fold writes it as the next generation's base, and the acceptance bar's fingerprint reads it (#618's decision 48).

Two rules decide when a writer writes a new base instead of a segment. The share rule (`writes_base`): a segment that would pass half its base's row bytes, or a pass whose stamps say every row is re-derived, as an output names them (the unit store names a structure-stamp move and a letter batch), writes a new base directly. The fold (`fold_due`): when a head's segments' row bytes reach the base's or their count reaches the output's cap (`SEGMENT_CAP`), the next writer writes the materialized form as the next generation's base. Either way no segment follows the new base in that pass. The windows rows take the fold alone and neither half of the share rule (#618's decisions 74 and 75): a table build re-derives every row cold, so it writes a segment of the rows that differ on every pass, a letter batch and a structure-stamp move included, whatever its size, and its route is decided from the head before the crate starts, never from a count of what the crate writes.

At most one output folds a pass (`claim_fold`), so the largest fold a pass writes is one base. The pass is the artifact cycle's run (`CYCLE_RUN_ENV`, which every process of a pass inherits), whose claim is one small file in the cycle's directory (`cycle_paths.CYCLE_VAR`, `cycle_paths.FOLD_CLAIM_NAME`) holding the run and the output that holds it, or, outside a cycle, the process, which holds its claim in memory, one for each cycle directory it names, so a test's own directory starts with none. An output whose fold is due and that does not hold the claim writes a segment and waits for a later pass, up to `FOLD_OVERRUN` segments past its cap (`fold_forced`), where it folds whether or not another output folded that pass.

The head's `content` is the output's identity for a reader that takes it from the file: a writer moves it when the rows move and keeps it when it writes the head alone, or when a fold writes a base whose rows are the ones the head already served (`Head.identity`).
"""

from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import os
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, Protocol

from rebuild.pipeline import output_sink
from rebuild.tools import cycle_paths

HEAD_FORMAT = "ams-row-delta-head/1"
SEGMENT_FORMAT = "ams-row-delta-segment/1"
MERGE_PLACE = "place"
MERGE_KEY = "key"
# The segment cap for every output in this format but the audit, which takes 32 (#618's decision 53), the unit store and the windows rows among them: a reader holds one stream a segment, and at 16 the folds add 0.160 to 0.399 GB to a rune edit at 45 runes, where caps of 8 would add 0.270 to 0.433.
SEGMENT_CAP = 16
# How many segments past its cap an output whose fold is due waits for the pass's one fold before it folds anyway (#618's decision 53): six outputs reaching the cap on one pass fold on six successive passes, the last at its cap plus five.
FOLD_OVERRUN = 5
# `cycle_timings.CYCLE_RUN_ENV`, restated because importing cycle_timings would put its imports into every closure that holds this module, as `spec_load.CYCLE_RUN_ENV` restates it; rebuild/test_row_delta.py ties the two.
CYCLE_RUN_ENV = "AMS_CYCLE_RUN"
UPSERT = b"+"
TOMBSTONE = b"-"
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_CHUNK = 1 << 20

Place = tuple[int, str]


class Sink(Protocol):
    """What a part's rows are written to: the gzip stream `write_part` opens."""

    def write(self, data: bytes, /) -> int: ...


class HeadError(ValueError):
    """Raised when a head or one of its parts cannot be trusted: a head that does not parse or names another output, a part whose size or sha256 is not the head's, or a segment whose generation or number is not the place the head gives it."""


@dataclass(frozen=True)
class Part:
    """One part as the head lists it: its file name, size and sha256, and its rows and their uncompressed bytes, which the share rule and the fold compare."""

    name: str
    size: int
    sha256: str
    rows: int
    rows_bytes: int

    def document(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "rows": self.rows,
            "rows_bytes": self.rows_bytes,
        }

    @classmethod
    def parse(cls, document: object) -> Part:
        if not isinstance(document, dict):
            raise HeadError(f"a head lists a part as {document!r}")
        try:
            return cls(
                str(document["name"]),
                int(document["size"]),
                str(document["sha256"]),
                int(document["rows"]),
                int(document["rows_bytes"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HeadError(f"a head lists a part it does not describe: {error!r}") from None


@dataclass(frozen=True)
class Head:
    """One output's head: see the module docstring. `merge` is how its parts merge, `MERGE_PLACE` or `MERGE_KEY`, written into the head only for an output that merges by key, so a head by place reads as it did before outputs merged by key."""

    output: str
    generation: int
    content: int
    stamps: dict
    base: Part
    segments: tuple[Part, ...] = ()
    merge: str = MERGE_PLACE

    @property
    def parts(self) -> tuple[Part, ...]:
        return (self.base, *self.segments)

    @property
    def segment_rows_bytes(self) -> int:
        return sum(part.rows_bytes for part in self.segments)

    @property
    def identity(self) -> str:
        return f"{self.output}@{self.content}"

    def document(self) -> dict:
        document = {
            "format": HEAD_FORMAT,
            "output": self.output,
            "generation": self.generation,
            "content": self.content,
            "stamps": self.stamps,
            "base": self.base.document(),
            "segments": [part.document() for part in self.segments],
        }
        if self.merge != MERGE_PLACE:
            document["merge"] = self.merge
        return document


def head_name(output: str) -> str:
    return f"{output}.head"


def base_name(output: str, generation: int) -> str:
    return f"{output}.{generation}.base"


def segment_name(output: str, generation: int, number: int) -> str:
    return f"{output}.{generation}.{number}.delta"


def head_path(directory: Path, output: str) -> Path:
    return Path(directory) / head_name(output)


def read_head(directory: Path, output: str) -> Head | None:
    """Return the head of `output` under `directory`, or None when there is none. A head that does not parse, or names another output or format, raises `HeadError`."""
    path = head_path(directory, output)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise HeadError(f"{path.name} does not parse: {error}") from None
    if not isinstance(document, dict) or document.get("format") != HEAD_FORMAT:
        raise HeadError(f"{path.name} is not an {HEAD_FORMAT} head")
    if document.get("output") != output:
        raise HeadError(f"{path.name} names the output {document.get('output')!r}")
    stamps = document.get("stamps")
    segments = document.get("segments")
    if not isinstance(stamps, dict) or not isinstance(segments, list):
        raise HeadError(f"{path.name} holds no stamps or no segment list")
    try:
        generation, content = int(document["generation"]), int(document["content"])
    except KeyError, TypeError, ValueError:
        raise HeadError(f"{path.name} names no generation") from None
    merge = document.get("merge", MERGE_PLACE)
    if merge not in (MERGE_PLACE, MERGE_KEY):
        raise HeadError(f"{path.name} merges its parts by {merge!r}")
    head = Head(
        output,
        generation,
        content,
        stamps,
        Part.parse(document.get("base")),
        tuple(Part.parse(part) for part in segments),
        merge,
    )
    if head.base.name != base_name(output, generation):
        raise HeadError(f"{path.name} lists {head.base.name} as generation {generation}'s base")
    for number, part in enumerate(head.segments, start=1):
        if part.name != segment_name(output, generation, number):
            raise HeadError(f"{path.name} lists {part.name} as segment {number} of generation {generation}")
    return head


def file_sha256(path: Path) -> str:
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass
class OpenParts:
    """The parts one head lists, each open for reading: `handles[0]` is the base and `handles[n]` segment `n`. Close it when done."""

    head: Head
    handles: list[BinaryIO] = field(default_factory=list)

    def close(self) -> None:
        handles, self.handles = self.handles, []
        for handle in handles:
            handle.close()

    def __enter__(self) -> OpenParts:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def open_parts(directory: Path, head: Head, *, sha256: bool = False) -> OpenParts:
    """Open every part `head` lists and check each against it: its size always, and its sha256 when `sha256` is true, reading the part through once and returning to its start. A part that is missing or differs raises `HeadError`, with every part opened so far closed."""
    opened = OpenParts(head)
    try:
        for part in head.parts:
            try:
                handle = open(Path(directory) / part.name, "rb")
            except FileNotFoundError:
                raise HeadError(f"{part.name} is missing") from None
            opened.handles.append(handle)
            if os.fstat(handle.fileno()).st_size != part.size:
                raise HeadError(f"{part.name} is not the {part.size} bytes its head records")
            if sha256:
                if hashlib.file_digest(handle, "sha256").hexdigest() != part.sha256:
                    raise HeadError(f"{part.name} does not hash to the sha256 its head records")
                handle.seek(0)
        return opened
    except BaseException:
        opened.close()
        raise


class _Counting:
    """A binary sink that hashes and counts what passes through it into `handle`, so a writer learns its part's size and sha256 without reading the part back, which a part routed to the null device could not give."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self._digest = hashlib.sha256()
        self.size = 0

    def write(self, data) -> int:
        self._handle.write(data)
        self._digest.update(data)
        self.size += len(data)
        return len(data)

    def flush(self) -> None:
        self._handle.flush()

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def write_part(
    directory: Path, name: str, fill: Callable[[Sink], tuple[int, int]], *, gzip_name: str | None = None
) -> Part:
    """Write one part, `name` under `directory`, through `output_sink.replace_output` and return its `Part`. `fill` writes the part's rows into a gzip stream at level 1 with a fixed mtime and returns the rows and their uncompressed bytes. The gzip header names `gzip_name`, by default the part's own name, never the staging file's; an empty `gzip_name` names no file, as a windows base does."""
    written: dict[str, Part] = {}

    def staged(staging: Path) -> None:
        with open(staging, "wb") as raw:
            counting = _Counting(raw)
            with gzip.GzipFile(
                filename=name if gzip_name is None else gzip_name,
                fileobj=counting,
                mode="wb",
                mtime=0,
                compresslevel=1,
            ) as stream:
                rows, rows_bytes = fill(stream)
        written["part"] = Part(name, counting.size, counting.hexdigest(), rows, rows_bytes)

    output_sink.replace_output(Path(directory) / name, writer=staged)
    return written["part"]


@dataclass(frozen=True, slots=True)
class Row:
    """One segment row: its operation (`UPSERT` or `TOMBSTONE`), its place, and its payload, a row in the base's encoding with its newline (a tombstone's payload need only name the row it removes, since the merge reads its place)."""

    op: bytes
    place: Place
    payload: bytes


def row_line(row: Row) -> bytes:
    """Return a segment row as its line: the operation, the place's base row and fraction, and the payload, tab-separated. The payload is the base's row unchanged, which holds no raw tab in either format this module serves."""
    anchor, fraction = row.place
    return row.op + b"\t" + str(anchor).encode() + b"\t" + fraction.encode() + b"\t" + row.payload


def row_length(op: bytes, place: Place, payload_length: int) -> int:
    """Return the length of a segment row's line (`row_line`) from its payload's length, without building the line."""
    return len(op) + 3 + len(str(place[0])) + len(place[1]) + payload_length


def _parse_row(line: bytes) -> Row:
    op, anchor, fraction, payload = line.split(b"\t", 3)
    if op not in (UPSERT, TOMBSTONE):
        raise ValueError(f"a segment row's operation is {op!r}")
    return Row(op, (int(anchor), fraction.decode("ascii")), payload)


def segment_header(output: str, generation: int, number: int, merge: str = MERGE_PLACE) -> bytes:
    """Return a segment's header line: its format, output, generation and number, and `"merge": "key"` for a segment that merges by key."""
    header: dict = {"format": SEGMENT_FORMAT, "output": output, "generation": generation, "segment": number}
    if merge != MERGE_PLACE:
        header["merge"] = merge
    return (json.dumps(header) + "\n").encode()


def check_segment_header(line: bytes, output: str, generation: int, number: int, merge: str) -> None:
    """Check a segment's header line against the head that lists it: its format, its output, generation and number, and how it merges. A header that names another raises `HeadError`."""
    try:
        header = json.loads(line)
    except ValueError as error:
        raise HeadError(f"segment {number} has no header: {error}") from None
    if not isinstance(header, dict) or header.get("format") != SEGMENT_FORMAT:
        raise HeadError(f"segment {number} is not an {SEGMENT_FORMAT} segment")
    if (header.get("output"), header.get("generation"), header.get("segment")) != (
        output,
        generation,
        number,
    ):
        raise HeadError(
            f"{output} lists segment {number} of generation {generation}, whose header names segment "
            f"{header.get('segment')} of generation {header.get('generation')}"
        )
    if header.get("merge", MERGE_PLACE) != merge:
        raise HeadError(
            f"{output} merges by {merge}, and segment {number}'s header says it merges by {header.get('merge', MERGE_PLACE)}"
        )


def write_segment(directory: Path, output: str, generation: int, number: int, rows: Iterable[Row]) -> Part:
    """Write segment `number` of `generation` from `rows`, which must arrive in place order, one row a place, and return its `Part`. Its header names the generation and number, which a reader checks against the head."""
    name = segment_name(output, generation, number)

    def fill(stream: Sink) -> tuple[int, int]:
        stream.write(segment_header(output, generation, number))
        count = total = 0
        previous: Place | None = None
        for row in rows:
            if previous is not None and row.place <= previous:
                raise ValueError(f"segment rows arrive out of place order: {row.place} after {previous}")
            previous = row.place
            line = row_line(row)
            stream.write(line)
            count += 1
            total += len(line)
        return count, total

    return write_part(directory, name, fill)


def segment_rows(handle: BinaryIO, output: str, generation: int, number: int) -> Iterator[Row]:
    """Yield one segment's rows from an open handle, from its start, after checking its header names `output`, `generation` and `number` and a merge by place. A header that names another raises `HeadError`."""
    handle.seek(0)
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        check_segment_header(stream.readline(), output, generation, number, MERGE_PLACE)
        for line in stream:
            yield _parse_row(line)


def keyed_segment_rows(
    handle: BinaryIO, output: str, generation: int, number: int
) -> Iterator[tuple[bytes, bytes]]:
    """Yield one segment's rows of a merge by key from an open handle, from its start, each as its operation and its payload with its newline (a row for an upsert, a key for a tombstone), after checking its header names `output`, `generation`, `number` and a merge by key. A header that names another raises `HeadError`."""
    handle.seek(0)
    with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
        check_segment_header(stream.readline(), output, generation, number, MERGE_KEY)
        for line in stream:
            op, tab, payload = line.partition(b"\t")
            if op not in (UPSERT, TOMBSTONE) or not tab:
                raise ValueError(
                    f"segment {number} of {output} holds a row that is not an upsert or a tombstone"
                )
            yield op, payload


def merge_placed(
    base_rows: Callable[[], Iterator[bytes]], segments: Sequence[Callable[[], Iterator[Row]]]
) -> Iterator[tuple[Place, bytes]]:
    """Yield the live rows of a base and its segments in place order, each with its place: the materialized order. `base_rows` yields the base's rows without its header line, and each of `segments` opens one segment's rows from its start, oldest first. Each part is read once, as a stream. At each place the row of the latest part that has one there is live, and a tombstone there yields nothing."""

    def base() -> Iterator[tuple[Place, int, bytes, bytes]]:
        for index, payload in enumerate(base_rows()):
            yield (index, ""), 0, UPSERT, payload

    def segment(number: int, rows: Iterator[Row]) -> Iterator[tuple[Place, int, bytes, bytes]]:
        for row in rows:
            yield row.place, number, row.op, row.payload

    streams = [base(), *(segment(number, rows()) for number, rows in enumerate(segments, start=1))]
    held: tuple[Place, int, bytes, bytes] | None = None
    for item in heapq.merge(*streams):
        if held is not None and held[0] != item[0] and held[2] == UPSERT:
            yield held[0], held[3]
        held = item
    if held is not None and held[2] == UPSERT:
        yield held[0], held[3]


def merge_keyed(
    base_rows: Iterable[bytes],
    segments: Sequence[Iterable[tuple[bytes, bytes]]],
    key: Callable[[bytes], list[bytes]],
    tombstone_key: Callable[[bytes], list[bytes]],
) -> Iterator[bytes]:
    """Yield the live rows of a base and its segments merged by key, in key order: the materialized rows. `base_rows` yields the base's rows without its header lines, each of `segments` yields one segment's `(operation, payload)` rows (`keyed_segment_rows`), oldest first, `key` gives a row's key and `tombstone_key` a tombstone's, each as the list of its fields, which compare field by field. The segments are merged among themselves first, the latest winning at each key, and the base streams past them, so a base row costs one key comparison, and the base's rows past the last change stream through untouched. A segment whose keys do not strictly ascend, or a base whose keys do not up to its last change, raises `ValueError`; the crate's merge checks every row of every part (`rebuild/kernel-rs/src/row_merge.rs`)."""

    def keyed(
        number: int, rows: Iterable[tuple[bytes, bytes]]
    ) -> Iterator[tuple[list[bytes], int, bytes, bytes]]:
        previous: list[bytes] | None = None
        for op, payload in rows:
            at = key(payload) if op == UPSERT else tombstone_key(payload)
            if previous is not None and at <= previous:
                raise ValueError(f"segment {number} is out of key order at {at!r}")
            previous = at
            yield at, -number, op, payload

    def latest() -> Iterator[tuple[list[bytes], bytes, bytes]]:
        held: tuple[list[bytes], int, bytes, bytes] | None = None
        for item in heapq.merge(*(keyed(number, rows) for number, rows in enumerate(segments, start=1))):
            if held is None or item[0] != held[0]:
                if held is not None:
                    yield held[0], held[2], held[3]
                held = item
        if held is not None:
            yield held[0], held[2], held[3]

    changes = latest()
    change = next(changes, None)
    previous: list[bytes] | None = None
    base = iter(base_rows)
    for row in base:
        if change is None:
            yield row
            yield from base
            return
        at = key(row)
        if previous is not None and at <= previous:
            raise ValueError(f"the base is out of key order at {at!r}")
        previous = at
        while change is not None and change[0] < at:
            if change[1] == UPSERT:
                yield change[2]
            change = next(changes, None)
        if change is not None and change[0] == at:
            if change[1] == UPSERT:
                yield change[2]
            change = next(changes, None)
        else:
            yield row
    while change is not None:
        if change[1] == UPSERT:
            yield change[2]
        change = next(changes, None)


def _digits(value: int, width: int) -> str:
    out = []
    for _ in range(width):
        value, remainder = divmod(value, 36)
        out.append(_DIGITS[remainder])
    return "".join(reversed(out))


def places_between(low: Place | None, high: Place | None, count: int) -> list[Place]:
    """Return `count` places strictly between `low` and `high`, ascending; `low` None means before the first base row and `high` None after the last. A fraction is base-36 digits read as a fraction, with no trailing zero, so fractions sort as strings in the order of their values and any two leave room between them. The fractions are spaced evenly over the gap, so a run of rows takes fractions a few digits longer than its bounds', whatever its length."""
    if count <= 0:
        return []
    anchor, below = low if low is not None else (-1, "")
    above = high[1] if high is not None and high[0] == anchor else None
    if high is not None and high <= (anchor, below):
        raise ValueError(f"no place lies between {low} and {high}")
    width = max(len(below), len(above or "")) + len(_digits(count + 1, 8).lstrip("0")) + 1
    floor = int(below.ljust(width, "0"), 36) if below else 0
    ceiling = int(above.ljust(width, "0"), 36) if above is not None else 36**width
    step = (ceiling - floor) // (count + 1)
    if step < 1:
        raise ValueError(f"no room for {count} places between {low} and {high}")
    return [(anchor, _digits(floor + step * index, width).rstrip("0")) for index in range(1, count + 1)]


def writes_base(segment_rows_bytes: int, base_rows_bytes: int, *, every_row_rederived: bool) -> bool:
    """The share rule: whether a writer writes a new base instead of its segment."""
    return every_row_rederived or 2 * segment_rows_bytes > base_rows_bytes


def fold_due(head: Head, cap: int | None = None) -> bool:
    """The fold: whether the writer that finds `head` writes the materialized form as the next generation's base, once it holds the pass's fold (`claim_fold`). `cap` is the output's segment cap, `SEGMENT_CAP` unless the output names another."""
    return bool(head.segments) and (
        head.segment_rows_bytes >= head.base.rows_bytes
        or len(head.segments) >= (SEGMENT_CAP if cap is None else cap)
    )


def fold_forced(head: Head, cap: int | None = None) -> bool:
    """Whether `head` lists `FOLD_OVERRUN` segments past its cap, so its writer folds whether or not another output holds the pass's fold."""
    return len(head.segments) >= (SEGMENT_CAP if cap is None else cap) + FOLD_OVERRUN


_process_claims: dict[Path, str] = {}


def claim_fold(output: str) -> bool:
    """Claim the pass's one fold for `output` and return whether `output` holds it: True when no other output claimed it this pass, or `output` did, and False when another did. Under a cycle the claim is the file `cycle_paths.FOLD_CLAIM_NAME` in `cycle_paths.CYCLE_VAR`, holding the run (`CYCLE_RUN_ENV`) and the output, written through `output_sink.replace_output`; a claim another run left counts as none. Outside a cycle the process holds it, one claim for each cycle directory, which the rebuild suite points into each test's own directory (`rebuild/conftest.py`). Each writer claims once it knows its fold is due and before it writes, the windows rows before the crate starts (`run_m1.build_tables`) and the unit store at its write (`unit_cache.update_store`)."""
    directory = cycle_paths.CYCLE_VAR
    run = os.environ.get(CYCLE_RUN_ENV)
    if not run:
        holder = _process_claims.setdefault(directory, output)
        return holder == output
    path = directory / cycle_paths.FOLD_CLAIM_NAME
    try:
        held = json.loads(path.read_bytes())
    except FileNotFoundError, ValueError:
        held = None
    if isinstance(held, dict) and held.get("run") == run:
        return held.get("output") == output
    output_sink.replace_output(path, json.dumps({"run": run, "output": output}) + "\n")
    return True


def _part_pattern(output: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(output)}\.\d+\.(?:base|\d+\.delta)")


def remove_unlisted(directory: Path, output: str, head: Head | None) -> list[str]:
    """Delete every base and segment of `output` under `directory` that `head` does not list (all of them when `head` is None) and return their names. A staging file is not a part and is left to the writer that owns its name."""
    listed = {part.name for part in head.parts} if head is not None else set()
    pattern = _part_pattern(output)
    removed = []
    for path in sorted(Path(directory).iterdir()):
        if pattern.fullmatch(path.name) and path.name not in listed:
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def remove_staging(directory: Path, output: str) -> list[str]:
    """Delete every staging file of `output`'s parts and head under `directory` (the name with `output_sink.STAGING_SUFFIX` added) and return their names. Only a writer killed while it filled one leaves it, and a part's name carries its generation and number, so no later part may take that name and replace it. A writer calls this before it writes anything of `output`, while no other writer of `output` runs, as the windows rows' plan does (`run_m1._route_of`)."""
    pattern = re.compile(
        rf"(?:{_part_pattern(output).pattern}|{re.escape(head_name(output))}){re.escape(output_sink.STAGING_SUFFIX)}"
    )
    removed = []
    for path in sorted(Path(directory).iterdir()):
        if pattern.fullmatch(path.name):
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def commit(directory: Path, head: Head) -> list[str]:
    """Put `head` in place, the commit point, and then delete the parts it does not list: an older generation's, and any a failed pass left. Returns the names deleted."""
    output_sink.replace_output(
        head_path(directory, head.output), json.dumps(head.document(), indent=1) + "\n", keep_if_equal=True
    )
    return remove_unlisted(directory, head.output, head)


def with_segment(head: Head, part: Part, stamps: dict) -> Head:
    """Return `head` with `part` as its next segment, its stamps replaced and its content moved."""
    return replace(head, stamps=stamps, segments=(*head.segments, part), content=head.content + 1)
