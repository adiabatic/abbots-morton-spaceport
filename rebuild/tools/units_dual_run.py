"""Run two corpus units engines over the same units and compare them unit by unit, for the port of the units recompute to Rust (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § The dual run).

The sources are `units_stage_fixtures`' (`open_source`): the mini bundle with its `ss10` copies, or a live selection of the last cycle's corpus (`--per-config K`, `--batches N` or `--all`). The source's handouts are cut again at `build._handout_width` for the units handed out and the wider side's width, as `_RecomputeRunner` cuts a build's queue, and both sides take those same handouts. A Python side is a pool of real `build._corpus_worker` processes over real pipes, spawned as `_RecomputeRunner.__init__` spawns them, with one handout in flight per worker, as `_RecomputeRunner._drive_phase1` drives them. A Rust side is one `units_exec.UnitsProcess` at `--threads` threads, with one handout in flight per thread. `--engines` names the two sides, `python,rust` by default, the Python side at `--python-jobs` workers; `--engines python,python` runs two Python sides at once, the first at `--python-jobs` workers and the second at one more, and the second takes the handouts in reverse. `--engines rust` (or `python`) runs one engine alone, which compares nothing: each handout is checked to be answered unit for unit, with every fragment inflating under `_FragmentArena.read`'s checks, and the verification sample is checked against the side's own phase-1 stamps and deltas.

A mini run defaults to two Python workers and one Rust thread. A live run defaults each engine's width to `artifact_cycle.corpus_job_budget` of a hand build under that engine over the box's memory shared equally among the run's sides, half of it when both engines run at once (`default_width`); `--threads max` names that width for the Rust side of any run.

`--memory` measures the Rust engine's memory: with a comma-separated `--threads` list it runs the whole selection once per width, writes each run's report, and fits a process figure and a per-thread figure to the Rust side's peak footprints by least squares, which `artifact_cycle.CORPUS_UNITS_RS_PROCESS_BYTES` and `CORPUS_UNITS_RS_THREAD_BYTES` are set from. On macOS it runs the engines with the allocator's large cache off (`kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT`), as the cycle's corpus-build step runs the build and every engine it starts, so the readings are the ones a cycle's engine reaches.

No side runs more than `_window` handouts past the first handout not yet compared: the first side takes the lowest handout left in that window and a reversed side the highest. Each handout is compared once both sides have answered it and then dropped, so the harness holds a bounded number of handouts per side whatever the run's size. A comparison takes every `_UnitProjection` field but `packed` by ordinal, type-strictly (a tuple is not a list, a bool is not an int); inflates each side's fragment with the build's dictionary under `_FragmentArena.read`'s checks and compares the bytes; and compares the handout's contract errors as lists. Once phase 1 is in, a deterministic sample of the units goes through each side's `verify`, with injections built from the first side's projections as the build's whole-corpus passes build them, and both sides' keys must agree with each other and with the first side's phase-1 stamp and deltas, as the build's stale check requires.

The report is JSON under `var/rust-port/dual-run/` (`--report` names another path): the units compared and the loaded corpus's unit total, each field's disagreement count with its first examples, and each side's wall time and peak footprint. Under `--memory` each run's report takes its width after the report's stem (`-threads<N>`), and the fit goes beside them (`-memory`). Any disagreement, or a compared count short of the units handed out, exits 1. `--plant FIELD` corrupts the second side's value of one projection field on the first unit of the first handout, that unit's fragment (`fragment`), or that handout's contract errors (`contract_errors`), to show that the comparator reports it.

A live `--all` run is a heavy pass: detach it, watch its log (`doc/running-long-steps.md`), and run nothing else beside it.

Run from the repository root:

    uv run python -m rebuild.tools.units_dual_run --source mini --threads 1
    uv run python -m rebuild.tools.units_dual_run --source live --per-config 1 --threads 1
    uv run python -m rebuild.tools.units_dual_run --source mini --engines python,python
    uv run python -m rebuild.tools.units_dual_run --source live --per-config 1 --batches 24 --engines rust --threads 1,4,max --memory
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import multiprocessing
import multiprocessing.connection
import os
import queue
import random
import sys
import threading
import time
import zlib
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import batched
from pathlib import Path

from rebuild.pipeline.kernel_exec import KERNEL_ALLOCATOR_ENVIRONMENT
from rebuild.review import build, unit_cache
from rebuild.review.audit import Unit
from rebuild.review.build import _UnitProjection
from rebuild.review.enrich import PrimaryUnitProjection, resolve_primary_unit_assignments
from rebuild.review.units_exec import RustProjection, UnitsProcess, encode
from rebuild.tools.peak_rss import format_gb
from rebuild.tools.units_stage_fixtures import Source, _add_source_arguments, open_source, same

REPORT_FORMAT = "ams-review-units-dual-run/1"
MEMORY_FORMAT = "ams-review-units-memory/1"
REPORT_DIR = build.REPO_ROOT / "var" / "rust-port" / "dual-run"
ENGINES = ("python", "rust")
VERIFY_SEED = "units-dual-run"
EXAMPLES_KEPT = 5
PROJECTION_FIELDS = tuple(item.name for item in dataclasses.fields(_UnitProjection) if item.name != "packed")
FIELDS = ("units",) + PROJECTION_FIELDS + ("fragment", "contract_errors", "verify")
PLANTABLE = PROJECTION_FIELDS + ("fragment", "contract_errors")


def identical(left: object, right: object) -> bool:
    """Whether two values are equal and of one kind all the way down, as `units_stage_fixtures.same` decides it, without encoding values that are plainly alike."""
    if type(left) is not type(right):
        return False
    if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
        return len(left) == len(right) and all(map(identical, left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return len(left) == len(right) and all(
            identical(left_key, right_key) and identical(left_value, right_value)
            for (left_key, left_value), (right_key, right_value) in zip(left.items(), right.items())
        )
    if left is None or isinstance(left, (bool, int, str, bytes)):
        return left == right
    if dataclasses.is_dataclass(left) and not isinstance(left, type):
        return all(
            identical(getattr(left, item.name), getattr(right, item.name))
            for item in dataclasses.fields(left)
        )
    return same(left, right)


# --- the report ---------------------------------------------------------------------------------


@dataclass
class FieldTally:
    disagreements: int = 0
    examples: list[dict] = field(default_factory=list)


@dataclass
class Report:
    """What the comparator found, by field (`FIELDS`), and the counts the run's verdict reads."""

    fields: dict[str, FieldTally] = field(default_factory=lambda: {name: FieldTally() for name in FIELDS})
    handed_out: int = 0
    compared: int = 0
    verify_sample: int = 0
    verify_agreed: int = 0

    def disagree(self, name: str, example: dict) -> None:
        tally = self.fields[name]
        tally.disagreements += 1
        if len(tally.examples) < EXAMPLES_KEPT:
            tally.examples.append(example)

    def ok(self) -> bool:
        return (
            not any(tally.disagreements for tally in self.fields.values())
            and self.compared == self.handed_out
            and self.verify_agreed == self.verify_sample
        )


# --- one engine's side --------------------------------------------------------------------------


@dataclass
class _Handout:
    """One handout of the run: its index among the run's handouts, its ordinals in queue order, each unit's codepoints and configurations for the report while it is pending, each side's answer once it arrives, and whether it has been compared."""

    index: int
    ordinals: array
    units: dict[int, tuple[str, list[str]]] = field(default_factory=dict)
    answers: dict[int, tuple[list[_UnitProjection], list[str]]] = field(default_factory=dict)
    compared: bool = False


class _Side:
    """One engine's side of the run, its connections (`conns`) the slots a handout is sent through, each with at most one in flight. `unhanded` holds the handouts this side has not been sent, `parked` the slots waiting for the window to move, and `finishing` the slots sent the end marker."""

    engine = ""

    def __init__(self, label: str, jobs: int, reverse: bool, handouts: int) -> None:
        self.label = label
        self.jobs = jobs
        self.reverse = reverse
        self.started = time.perf_counter()
        self.phase1_seconds = 0.0
        self.verify_seconds = 0.0
        self.peaks: dict[str, int] = {}
        self.unhanded: set[int] = set(range(handouts))
        self.in_flight: dict[object, int] = {}
        self.parked: list = []
        self.finishing: set = set()
        self.conns: list = []

    def describe(self) -> dict:
        return {
            "label": self.label,
            "engine": self.engine,
            "width": self.jobs,
            "order": "reversed" if self.reverse else "forward",
            "phase1_seconds": round(self.phase1_seconds, 3),
            "verify_seconds": round(self.verify_seconds, 3),
            "peak_footprint_bytes": sum(self.peaks.values()),
            "worker_peaks": self.peaks,
        }

    def next_handout(self, low: int, window: int) -> int | None:
        """Take the lowest handout left below `low + window`, or the highest on a reversed side."""
        candidates = [index for index in self.unhanded if index < low + window]
        if not candidates:
            return None
        index = max(candidates) if self.reverse else min(candidates)
        self.unhanded.discard(index)
        return index

    def receive(self, conn) -> tuple:
        raise NotImplementedError

    def send_phase1(self, conn, index: int, units: tuple[Unit, ...]) -> None:
        raise NotImplementedError

    def finish(self, conn) -> None:
        raise NotImplementedError

    def verify(self, items: Sequence[tuple[Unit, tuple]]) -> dict[str, tuple]:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def kill(self) -> None:
        raise NotImplementedError


class PythonSide(_Side):
    """A pool of real `build._corpus_worker` processes, spawned and driven as `_RecomputeRunner` spawns and drives them, one slot per worker."""

    engine = "python"

    def __init__(self, label: str, init: dict, jobs: int, reverse: bool, handouts: int) -> None:
        super().__init__(label, jobs, reverse, handouts)
        self.names: dict[object, str] = {}
        self._procs: list = []
        ctx = multiprocessing.get_context("spawn")
        for index in range(jobs):
            parent_conn, child_conn = ctx.Pipe()
            proc = ctx.Process(target=build._corpus_worker, args=(child_conn, init))
            proc.start()
            child_conn.close()
            self._procs.append(proc)
            self.conns.append(parent_conn)
            self.names[parent_conn] = f"w{index}"

    def receive(self, conn) -> tuple:
        try:
            reply = conn.recv()
        except EOFError:
            raise RuntimeError(
                f"{self.label}: worker {self.names[conn]} closed its pipe without replying"
            ) from None
        if reply[0] == "error":
            raise RuntimeError(f"{self.label}: corpus worker {self.names[conn]} failed:\n{reply[1]}")
        return reply

    def send_phase1(self, conn, index: int, units: tuple[Unit, ...]) -> None:
        conn.send(("phase1", units, self.names[conn]))
        self.in_flight[conn] = index

    def finish(self, conn) -> None:
        conn.send(("phase1-done",))
        self.finishing.add(conn)

    def verify(self, items: Sequence[tuple[Unit, tuple]]) -> dict[str, tuple]:
        """Send the sample to every worker in contiguous shares, as `_RecomputeRunner.verify` does, and return the merged keys."""
        started = time.perf_counter()
        shares = batched(items, max(1, math.ceil(len(items) / len(self.conns))))
        for conn in self.conns:
            conn.send(("verify", list(next(shares, ()))))
        keys: dict[str, tuple] = {}
        for conn in self.conns:
            keys.update(self.receive(conn)[1])
        self.verify_seconds = time.perf_counter() - started
        return keys

    def stop(self) -> None:
        for conn in self.conns:
            conn.send(("stop",))
            reply = self.receive(conn)
            if reply[0] != "peak":
                raise RuntimeError(f"{self.label}: worker {self.names[conn]} answered stop with {reply[0]!r}")
            self.peaks[self.names[conn]] = int(reply[1])
            conn.close()
        for proc in self._procs:
            proc.join(timeout=5)
        self.conns = []

    def kill(self) -> None:
        for conn in self.conns:
            try:
                conn.close()
            except OSError:
                pass
        for proc in self._procs:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=5)
        self.conns = []


class RustSide(_Side):
    """The Rust engine as one `units_exec.UnitsProcess` at `threads` threads, one slot per thread. A reader thread reads the engine's replies and hands each `batch` reply, its projections in a Python worker's shapes (`_unit_projection`), to the slot that sent the batch, through a queue and a byte on the slot's pipe, which `multiprocessing.connection.wait` watches as it watches a Python worker's connection; the `verify` and `stopped` replies go to a queue of their own. A failure the reader meets is handed to every slot and to that queue, so the run raises it wherever it waits."""

    engine = "rust"

    def __init__(self, label: str, init: dict, threads: int, reverse: bool, handouts: int) -> None:
        super().__init__(label, threads, reverse, handouts)
        self.process = UnitsProcess(init, threads)
        self._writers: dict[object, multiprocessing.connection.Connection] = {}
        self._replies: dict[object, queue.SimpleQueue] = {}
        for _slot in range(threads):
            reader, writer = multiprocessing.Pipe(duplex=False)
            self.conns.append(reader)
            self._writers[reader] = writer
            self._replies[reader] = queue.SimpleQueue()
        self._sent_by: dict[int, object] = {}
        self._others: queue.SimpleQueue = queue.SimpleQueue()
        self._reader = threading.Thread(target=self._read, name=f"{label} replies", daemon=True)
        self._reader.start()

    def _deliver(self, conn, reply: tuple) -> None:
        self._replies[conn].put(reply)
        self._writers[conn].send_bytes(b"!")

    def _read(self) -> None:
        try:
            while True:
                reply = self.process.read_reply()
                if reply[0] == "batch":
                    projections = [_unit_projection(projection) for projection in reply[2]]
                    self._deliver(self._sent_by.pop(reply[1]), ("batch", projections, reply[3]))
                    continue
                self._others.put(reply)
                if reply[0] == "stopped":
                    return
        except Exception as error:
            failure = ("error", f"{type(error).__name__}: {error}")
            self._others.put(failure)
            for conn in list(self.conns):
                try:
                    self._deliver(conn, failure)
                except OSError:
                    pass

    def _raise(self, reply: tuple) -> tuple:
        if reply[0] == "error":
            raise RuntimeError(f"{self.label}: the units engine failed:\n{reply[1]}")
        return reply

    def receive(self, conn) -> tuple:
        conn.recv_bytes()
        return self._raise(self._replies[conn].get())

    def send_phase1(self, conn, index: int, units: tuple[Unit, ...]) -> None:
        self._sent_by[index] = conn
        self.in_flight[conn] = index
        self.process.send_batch(index, units)

    def finish(self, conn) -> None:
        self.finishing.add(conn)
        self._deliver(conn, ("ok",))

    def verify(self, items: Sequence[tuple[Unit, tuple]]) -> dict[str, tuple]:
        """Send the whole sample in one `verify` frame and return the keys."""
        started = time.perf_counter()
        self.process.send_verify(items)
        reply = self._raise(self._others.get())
        if reply[0] != "verify":
            raise RuntimeError(f"{self.label}: the units engine answered verify with {reply[0]!r}")
        self.verify_seconds = time.perf_counter() - started
        return reply[1]

    def stop(self) -> None:
        self.process.send_stop()
        reply = self._raise(self._others.get())
        if reply[0] != "stopped":
            raise RuntimeError(f"{self.label}: the units engine answered stop with {reply[0]!r}")
        self.peaks["engine"] = int(reply[1])
        self._reader.join()

    def kill(self) -> None:
        self.process.close()
        for conn in self.conns:
            conn.close()
            self._writers[conn].close()
        self.conns = []


SIDES = {"python": PythonSide, "rust": RustSide}


def _unit_projection(projection: RustProjection) -> _UnitProjection:
    """Return the Rust engine's projection, which holds the parsed reply's lists and dicts, as the `_UnitProjection` a Python worker sends, every list a tuple and every pair a two-tuple, so the type-strict comparison holds both engines to one shape. A pair or span that is not two long raises."""
    rows = projection.primary_unit_projection

    def pair(value: Sequence[int]) -> tuple[int, int]:
        start, end = value
        return (start, end)

    return _UnitProjection(
        unit_id=projection.unit_id,
        input_key=projection.input_key,
        content_key=projection.content_key,
        ink_identical=projection.ink_identical,
        picture_identical=projection.picture_identical,
        junior_equivalent=projection.junior_equivalent,
        ink_deltas=tuple(projection.ink_deltas),
        diffs_digest=projection.diffs_digest,
        cluster=projection.cluster,
        change_group=projection.change_group,
        ledger_class=projection.ledger_class,
        pair_codepoints=None if projection.pair_codepoints is None else pair(projection.pair_codepoints),
        primary_unit_projection=PrimaryUnitProjection(
            unit_id=projection.unit_id,
            codepoint_values=rows.codepoint_values,
            ink_identical=rows.ink_identical,
            picture_identical=rows.picture_identical,
            pair=None if rows.pair is None else pair(rows.pair),
            after_spans=tuple(pair(span) for span in rows.after_spans),
            after_cells=tuple(rows.after_cells),
            after_junctions=tuple(rows.after_junctions),
            before_spans=tuple(pair(span) for span in rows.before_spans),
            before_glyphs=tuple(rows.before_glyphs),
            before_junctions=tuple(rows.before_junctions),
            junction_pairs=tuple(pair(junction) for junction in rows.junction_pairs),
        ),
        junction_rects=tuple(
            (pair(junction), before, after) for junction, before, after in projection.junction_rects
        ),
        mismatches=tuple(projection.mismatches),
        ordinal=projection.ordinal,
        packed=projection.packed,
        raw_length=projection.raw_length,
    )


# --- the comparison -----------------------------------------------------------------------------


def _inflate(projection: _UnitProjection, dictionary: bytes) -> tuple[bytes | None, str]:
    """Return a projection's fragment inflated, checked as `_FragmentArena.read` checks a span (one zlib stream under `dictionary` that ends where the span ends, holding this unit under this stamp) and against `raw_length`, or None and why it was refused."""
    inflater = zlib.decompressobj(zdict=dictionary)
    try:
        body = inflater.decompress(projection.packed)
    except zlib.error as error:
        return None, f"it does not inflate ({error})"
    if not inflater.eof or inflater.unused_data:
        return None, "its deflated stream does not end where the span ends"
    if len(body) != projection.raw_length:
        return None, f"it inflates to {len(body)} bytes, not its raw length {projection.raw_length}"
    fragment = json.loads(body)
    if (
        not isinstance(fragment, dict)
        or fragment.get("id") != projection.unit_id
        or fragment.get("content_key") != projection.content_key
    ):
        return None, "it holds another unit or stamp"
    return body, ""


def _excerpt(body: bytes, offset: int) -> str:
    return body[max(0, offset - 60) : offset + 60].decode("ascii", "replace")


def _unit_example(handout: _Handout, ordinal: int) -> dict:
    codepoints, configs = handout.units.get(ordinal, ("", []))
    return {"ordinal": ordinal, "codepoints": codepoints, "configs": configs}


def compare_handout(handout: _Handout, dictionary: bytes, report: Report) -> None:
    """Compare the first side's answer to one handout with the second side's and tally every disagreement in `report`."""
    (left, left_errors), (right, right_errors) = handout.answers[0], handout.answers[1]
    left_by = {projection.ordinal: projection for projection in left}
    right_by = {projection.ordinal: projection for projection in right}
    if len(left) != len(left_by) or len(right) != len(right_by):
        report.disagree(
            "units",
            {
                "handout": handout.index,
                "left": len(left) - len(left_by),
                "right": len(right) - len(right_by),
                "why": "a side answered a unit twice",
            },
        )
    for ordinal in handout.ordinals:
        a, b = left_by.pop(ordinal, None), right_by.pop(ordinal, None)
        if a is None or b is None:
            report.disagree(
                "units",
                _unit_example(handout, ordinal)
                | {"left": a is not None, "right": b is not None, "why": "a side did not answer the unit"},
            )
            continue
        report.compared += 1
        for name in PROJECTION_FIELDS:
            left_value, right_value = getattr(a, name), getattr(b, name)
            if not identical(left_value, right_value):
                report.disagree(
                    name,
                    _unit_example(handout, ordinal)
                    | {"left": encode(left_value), "right": encode(right_value)},
                )
        left_body, left_why = _inflate(a, dictionary)
        right_body, right_why = _inflate(b, dictionary)
        if left_body is None or right_body is None:
            report.disagree(
                "fragment", _unit_example(handout, ordinal) | {"left": left_why, "right": right_why}
            )
        elif left_body != right_body:
            offset = next(
                (index for index, (x, y) in enumerate(zip(left_body, right_body)) if x != y),
                min(len(left_body), len(right_body)),
            )
            report.disagree(
                "fragment",
                _unit_example(handout, ordinal)
                | {
                    "offset": offset,
                    "left": _excerpt(left_body, offset),
                    "right": _excerpt(right_body, offset),
                },
            )
    for extra in sorted(set(left_by) | set(right_by)):
        report.disagree(
            "units",
            {
                "ordinal": extra,
                "left": extra in left_by,
                "right": extra in right_by,
                "why": "a side answered a unit outside the handout",
            },
        )
    if not identical(left_errors, right_errors):
        report.disagree(
            "contract_errors", {"handout": handout.index, "left": left_errors, "right": right_errors}
        )


def check_handout(handout: _Handout, dictionary: bytes, report: Report) -> None:
    """Check the one side's answer to one handout in a run of one engine: every unit of the handout answered once and nothing else, each answered unit counted as compared, and every fragment inflating under `_inflate`'s checks."""
    projections, _errors = handout.answers[0]
    answered: dict[int, _UnitProjection] = {}
    for projection in projections:
        if projection.ordinal in answered:
            report.disagree(
                "units",
                _unit_example(handout, projection.ordinal) | {"why": "the side answered the unit twice"},
            )
        answered[projection.ordinal] = projection
    for ordinal in handout.ordinals:
        projection = answered.pop(ordinal, None)
        if projection is None:
            report.disagree(
                "units", _unit_example(handout, ordinal) | {"why": "the side did not answer the unit"}
            )
            continue
        report.compared += 1
        body, why = _inflate(projection, dictionary)
        if body is None:
            report.disagree("fragment", _unit_example(handout, ordinal) | {"why": why})
    for extra in sorted(answered):
        report.disagree("units", {"ordinal": extra, "why": "the side answered a unit outside the handout"})


def _corrupted(value: object) -> object:
    """Return `value` changed but of its own kind, for `--plant`."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return value + "-planted"
    if value is None:
        return "planted"
    if isinstance(value, tuple):
        return value + (("planted", "planted"),)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        first = dataclasses.fields(value)[0].name
        return dataclasses.replace(value, **{first: _corrupted(getattr(value, first))})
    raise TypeError(f"--plant has no corruption for a {type(value).__qualname__}")


def _plant(
    name: str, ordinal: int, projections: list[_UnitProjection], errors: list[str], dictionary: bytes
) -> None:
    """Corrupt one side's answer in place: field `name` of the unit at `ordinal`, its fragment's bytes, or the handout's contract errors."""
    if name == "contract_errors":
        errors.append("planted contract error")
        return
    for position, projection in enumerate(projections):
        if projection.ordinal != ordinal:
            continue
        if name == "fragment":
            body = zlib.decompressobj(zdict=dictionary).decompress(projection.packed)
            deflater = zlib.compressobj(1, zdict=dictionary)
            packed = deflater.compress(body.replace(b" ", b"\t", 1)) + deflater.flush()
            projections[position] = dataclasses.replace(projection, packed=packed)
        else:
            projections[position] = dataclasses.replace(
                projection, **{name: _corrupted(getattr(projection, name))}
            )
        return
    raise RuntimeError(f"the planted unit {ordinal} is not in its handout's answer")


def _injections(sample: Sequence[tuple[Unit, _UnitProjection]]) -> list[tuple[Unit, tuple]]:
    """Build the verification items from the first side's projections: a copy of each sampled unit as the build materializes a cached one (its id and machine flags from the store), with the duplicate group, cluster, final class and primary units the build's whole-corpus passes inject (`_build_m1`). The primary units are resolved over the sample alone, since the harness holds no other projection."""
    assignments, _counts = resolve_primary_unit_assignments(
        [projection.primary_unit_projection for _unit, projection in sample]
    )
    items = []
    for original, projection in sample:
        unit = dataclasses.replace(original)
        final_class = projection.change_group or unit.class_id
        duplicate_group, cluster = unit.duplicate_group, unit.cluster
        flags = (projection.ink_identical, projection.picture_identical, projection.junior_equivalent)
        if not any(flags) and not unit.no_verdict:
            pair = None
            if projection.pair_codepoints:
                values = unit.codepoint_values
                pair = (values[projection.pair_codepoints[0]], values[projection.pair_codepoints[1]])
            key = (unit.configs, pair, final_class, projection.diffs_digest)
            duplicate_group = unit_cache.duplicate_group_id_for(repr(key))
            cluster = projection.cluster
        unit.unit_id = projection.unit_id
        unit.ink_identical, unit.picture_identical, unit.junior_equivalent = flags
        primary_units = tuple(tuple(entry) for entry in assignments.get(projection.unit_id, ()))
        items.append((unit, (duplicate_group, cluster, final_class, primary_units)))
    return items


def _compare_verify(
    sample: Sequence[tuple[Unit, _UnitProjection]], answers: Sequence[dict[str, tuple]], report: Report
) -> None:
    """Compare every side's verify keys with the first side's phase-1 stamp and deltas, and so with each other, counting the sampled units on which they all agree."""
    for unit, projection in sample:
        expected = (projection.content_key, projection.ink_deltas)
        keys = [answer.get(projection.unit_id) for answer in answers]
        if all(key is not None and identical(key, expected) for key in keys):
            report.verify_agreed += 1
            continue
        report.disagree(
            "verify",
            {
                "ordinal": unit.ordinal,
                "codepoints": unit.codepoints,
                "configs": list(unit.configs),
                "id": projection.unit_id,
                "phase1": encode(expected),
            }
            | {side: None if key is None else encode(key) for side, key in zip(("left", "right"), keys)},
        )
    sampled_ids = {projection.unit_id for _unit, projection in sample}
    for extra in sorted(set().union(*answers) - sampled_ids):
        report.disagree("verify", {"id": extra, "why": "a side verified a unit outside the sample"})


# --- the run ------------------------------------------------------------------------------------


def _window(sides: Sequence[_Side]) -> int:
    """How many handouts past the first one not yet compared a side may run: two per worker of both sides, so every worker can stay busy while the other side catches up."""
    return 2 * sum(side.jobs for side in sides)


def _widths(engines: tuple[str, ...], python_jobs: int, threads: int) -> tuple[int, ...]:
    """Each side's width: `threads` for a Rust side, `python_jobs` for the first Python side and one more for a second."""
    if engines == ("python", "python"):
        return python_jobs, python_jobs + 1
    width = {"python": python_jobs, "rust": threads}
    return tuple(width[engine] for engine in engines)


def default_width(engine: str, sides: int) -> int:
    """Return a live run's default width for one engine's side: `artifact_cycle.corpus_job_budget` of a hand build under that engine, over the box's memory shared equally among the run's `sides`, since they all run at once beside the harness, which holds the loaded corpus as a build's parent does."""
    from rebuild.tools import artifact_cycle, memory_budget

    return artifact_cycle.corpus_job_budget(
        skip_gates=True, engine=engine, total_bytes=memory_budget.total_memory_bytes() // sides
    )


def fit_memory(points: Sequence[tuple[int, int]]) -> tuple[int, int]:
    """Fit `peak = process + threads × thread` to `(threads, peak footprint)` points by least squares and return the two figures in whole bytes. The widths must not all be equal."""
    count = len(points)
    mean_threads = Fraction(sum(threads for threads, _peak in points), count)
    mean_peak = Fraction(sum(peak for _threads, peak in points), count)
    spread = sum((threads - mean_threads) ** 2 for threads, _peak in points)
    if not spread:
        raise ValueError("a memory fit needs at least two distinct widths")
    slope = sum((threads - mean_threads) * (peak - mean_peak) for threads, peak in points) / spread
    return round(mean_peak - slope * mean_threads), round(slope)


def run(
    source: Source,
    engines: tuple[str, ...],
    python_jobs: int,
    threads: int,
    plant: str | None,
    report: Report,
) -> tuple[list[_Side], int]:
    """Run every side over the source's handouts, compare each handout as all answers arrive (or check it, in a run of one engine), then run the verification sample through every side, filling `report`. Returns the stopped sides and the planted unit's ordinal."""
    for engine in engines:
        if engine not in ENGINES:
            raise SystemExit(
                f"engine {engine!r} is not one the harness runs; the engines are {', '.join(ENGINES)}"
            )
    dictionary = source.init["dictionary"]
    widths = _widths(engines, python_jobs, threads)
    reverse = tuple(label == 1 and engines[0] == engines[1] for label in range(len(engines)))
    width = build._handout_width(source.unit_count(), max(widths))
    pieces = [array("I", piece) for handout in source.handouts for piece in batched(handout, width)]
    handouts = [_Handout(index, ordinals) for index, ordinals in enumerate(pieces)]
    if not handouts:
        raise SystemExit("the source hands out no units")
    report.handed_out = sum(len(handout.ordinals) for handout in handouts)
    planted = handouts[0].ordinals[0]
    ordinals = [ordinal for handout in handouts for ordinal in handout.ordinals]
    sampled = set(random.Random(VERIFY_SEED).sample(ordinals, min(build.VERIFICATION_SAMPLE, len(ordinals))))
    report.verify_sample = len(sampled)
    kept: dict[int, _UnitProjection] = {}
    print(
        f"units_dual_run: {report.handed_out:,} units in {len(handouts)} handouts of {width}, "
        + " against ".join(f"{engine} at {jobs}" for engine, jobs in zip(engines, widths))
        + (", alone" if len(engines) == 1 else "")
        + (", the second in reverse" if any(reverse) else ""),
        file=sys.stderr,
    )
    sides: list[_Side] = []
    try:
        for label, (engine, jobs, backwards) in enumerate(zip(engines, widths, reverse)):
            sides.append(
                SIDES[engine](f"side {label} ({engine})", source.init, jobs, backwards, len(handouts))
            )
        window = _window(sides)
        low = 0
        side_of = {conn: label for label, side in enumerate(sides) for conn in side.conns}

        def hand(label: int, conn) -> None:
            side = sides[label]
            index = side.next_handout(low, window)
            if index is None:
                if side.unhanded:
                    side.parked.append(conn)
                else:
                    side.finish(conn)
                return
            handout = handouts[index]
            units = source.units(handout.ordinals)
            if not handout.units:
                handout.units = {unit.ordinal: (unit.codepoints, list(unit.configs)) for unit in units}
            side.send_phase1(conn, index, units)

        for label, side in enumerate(sides):
            for conn in side.conns:
                hand(label, conn)
        waiting = [conn for side in sides for conn in side.conns]
        done = 0
        while waiting:
            for conn in multiprocessing.connection.wait(waiting):
                label = side_of[conn]
                side = sides[label]
                reply = side.receive(conn)
                if reply[0] == "ok" and conn in side.finishing:
                    waiting.remove(conn)
                    if not any(other in waiting for other in side.conns):
                        side.phase1_seconds = time.perf_counter() - side.started
                    continue
                if reply[0] != "batch":
                    raise RuntimeError(f"{side.label}: unexpected reply {reply[0]!r} in phase 1")
                handout = handouts[side.in_flight.pop(conn)]
                projections: list[_UnitProjection] = list(reply[1])
                errors: list[str] = list(reply[2])
                del reply
                if label == 1 and plant is not None and handout.index == 0:
                    _plant(plant, planted, projections, errors, dictionary)
                if label == 0:
                    kept.update(
                        (projection.ordinal, projection)
                        for projection in projections
                        if projection.ordinal in sampled
                    )
                handout.answers[label] = (projections, errors)
                del projections, errors
                if len(handout.answers) == len(sides):
                    (compare_handout if len(sides) == 2 else check_handout)(handout, dictionary, report)
                    done += len(handout.ordinals)
                    handout.answers.clear()
                    handout.units.clear()
                    handout.compared = True
                    while low < len(handouts) and handouts[low].compared:
                        low += 1
                    print(
                        f"units_dual_run: compared {done:,} of {report.handed_out:,} units", file=sys.stderr
                    )
                hand(label, conn)
                for other_label, other in enumerate(sides):
                    parked, other.parked = other.parked, []
                    for idle in parked:
                        hand(other_label, idle)
        position = {ordinal: index for index, ordinal in enumerate(ordinals)}
        order = array("I", sorted(kept, key=position.__getitem__))
        sample = [(unit, kept[unit.ordinal]) for unit in source.units(order)]
        answers = [side.verify(_injections(sample)) for side in sides]
        _compare_verify(sample, answers, report)
        for side in sides:
            side.stop()
    finally:
        for side in sides:
            side.kill()
    return sides, planted


def write_report(
    path: Path,
    source: Source,
    engines: tuple[str, ...],
    sides: Sequence[_Side],
    plant: str | None,
    planted: int,
    report: Report,
) -> None:
    payload = {
        "format": REPORT_FORMAT,
        "ok": report.ok(),
        "source": source.describe(),
        "engines": list(engines),
        "units_handed_out": report.handed_out,
        "units_compared": report.compared,
        "corpus_units": source.corpus_units,
        "verify": {"sample": report.verify_sample, "agreed": report.verify_agreed},
        "planted": None if plant is None else {"field": plant, "ordinal": planted},
        "fields": {
            name: {"disagreements": tally.disagreements, "examples": tally.examples}
            for name, tally in report.fields.items()
        },
        "sides": [side.describe() for side in sides],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _print_run(
    source: Source, sides: Sequence[_Side], plant: str | None, planted: int, report: Report, path: Path
) -> bool:
    """Print one run's sides, its plant, its disagreements and its verdict, and return whether it passed."""
    for side in sides:
        print(
            f"{side.label}: {side.jobs} {'threads' if side.engine == 'rust' else 'workers'}, phase 1 {side.phase1_seconds:.1f} s, verify {side.verify_seconds:.1f} s, "
            f"peak footprint {sum(side.peaks.values()) / 1e9:.2f} GB",
            file=sys.stderr,
        )
    if plant == "contract_errors":
        print("planted: contract_errors on the second side's answer to handout 0", file=sys.stderr)
    elif plant:
        print(f"planted: {plant} on the second side's unit at ordinal {planted}", file=sys.stderr)
    for field_name, tally in report.fields.items():
        if tally.disagreements:
            first = tally.examples[0]
            if "ordinal" in first:
                where = f"unit at ordinal {first['ordinal']} ({first.get('codepoints', '')} {first.get('configs', '')})"
            elif "handout" in first:
                where = f"handout {first['handout']}"
            else:
                where = f"unit {first.get('id')}"
            noun = "disagreement" if tally.disagreements == 1 else "disagreements"
            print(f"{field_name}: {tally.disagreements:,} {noun}, the first at the {where}", file=sys.stderr)
    print(
        f"compared {report.compared:,} of {report.handed_out:,} units handed out "
        f"({source.corpus_units:,} in the loaded corpus), verify {report.verify_agreed} of {report.verify_sample} "
        f"agreed; report {path}",
        file=sys.stderr,
    )
    alone = len(sides) == 1
    if report.ok():
        print(
            "units_dual_run: "
            + ("the engine answered every unit" if alone else "the engines agree on every unit"),
            file=sys.stderr,
        )
        return True
    print(
        "units_dual_run: " + ("the engine's answers fail their checks" if alone else "the engines disagree"),
        file=sys.stderr,
    )
    return False


def write_memory_report(
    path: Path, source: Source, engines: tuple[str, ...], runs: Sequence[dict], fit: tuple[int, int]
) -> None:
    process, thread = fit
    payload = {
        "format": MEMORY_FORMAT,
        "source": source.describe(),
        "engines": list(engines),
        "runs": list(runs),
        "fit": {"process_bytes": process, "thread_bytes": thread},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _thread_widths(text: str | None, live: bool, engines: tuple[str, ...]) -> list[int]:
    """Read `--threads`: a comma-separated list of widths, each a positive count or `max`, the Rust side's default width for a live run (`default_width`). Left out, it is that default on a live run and one thread on the mini."""
    if text is None:
        return [default_width("rust", len(engines)) if live else 1]
    widths = []
    for item in text.split(","):
        if item == "max":
            widths.append(default_width("rust", len(engines)))
        elif item.isdigit() and int(item) >= 1:
            widths.append(int(item))
        else:
            raise SystemExit(f"--threads takes positive counts and max, not {item!r}")
    return widths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="units_dual_run", description=(__doc__ or "").split("\n\n")[0])
    _add_source_arguments(parser)
    parser.add_argument(
        "--engines",
        default="python,rust",
        help="the two engines, comma-separated, or one engine to run alone (default: python,rust)",
    )
    parser.add_argument(
        "--python-jobs",
        type=int,
        metavar="N",
        help="the first Python side's workers (default: 2 on the mini, `default_width` on a live run)",
    )
    parser.add_argument(
        "--threads",
        metavar="N[,N...]",
        help="the Rust side's threads, `max` for a live run's default; a list is for --memory (default: 1 on the mini, `default_width` on a live run)",
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        help="run once per --threads width and fit a process and a per-thread figure to the Rust side's peak footprints",
    )
    parser.add_argument("--plant", choices=PLANTABLE, metavar="FIELD", help="corrupt one second-side value")
    parser.add_argument("--report", type=Path, help="where to write the JSON report")
    args = parser.parse_args(argv)
    engines = tuple(args.engines.split(","))
    if len(engines) not in (1, 2):
        parser.error("--engines names two engines, or one to run alone")
    if args.plant and len(engines) != 2:
        parser.error("--plant corrupts the second side, so it needs two engines")
    if args.python_jobs is not None and args.python_jobs < 1:
        parser.error("--python-jobs is at least 1")
    live = args.source == "live"
    python_jobs = args.python_jobs or (default_width("python", len(engines)) if live else 2)
    widths = _thread_widths(args.threads, live, engines)
    if args.memory:
        if "rust" not in engines:
            parser.error("--memory measures the Rust side, so --engines names rust")
        if len(set(widths)) < 2:
            parser.error("--memory fits two figures, so --threads names at least two distinct widths")
    elif len(widths) != 1:
        parser.error("a --threads list is for --memory")
    if args.memory and sys.platform == "darwin":
        os.environ.update(KERNEL_ALLOCATOR_ENVIRONMENT)
    source = open_source(args.source, args.per_config, args.batches, args.everything)
    name = "-".join((source.name, *engines)) + (f"-plant-{args.plant}" if args.plant else "")
    base = args.report or REPORT_DIR / f"{name}.json"
    passed = True
    runs: list[dict] = []
    for threads in widths:
        report = Report()
        sides, planted = run(source, engines, python_jobs, threads, args.plant, report)
        path = base.with_name(f"{base.stem}-threads{threads}.json") if args.memory else base
        write_report(path, source, engines, sides, args.plant, planted, report)
        passed = _print_run(source, sides, args.plant, planted, report, path) and passed
        rust = next(side for side in sides if side.engine == "rust") if "rust" in engines else None
        if rust is not None:
            runs.append({"threads": threads, "peak_footprint_bytes": rust.peaks["engine"], "ok": report.ok()})
    if args.memory:
        fit = fit_memory([(entry["threads"], entry["peak_footprint_bytes"]) for entry in runs])
        path = base.with_name(f"{base.stem}-memory.json")
        write_memory_report(path, source, engines, runs, fit)
        for entry in runs:
            print(
                f"memory: {entry['threads']} threads, peak footprint {format_gb(entry['peak_footprint_bytes'])} GB",
                file=sys.stderr,
            )
        print(
            f"memory: fitted {format_gb(fit[0])} GB a process and {format_gb(fit[1])} GB a thread "
            f"({fit[0]:,} and {fit[1]:,} bytes); report {path}",
            file=sys.stderr,
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
