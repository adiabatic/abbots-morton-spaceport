"""The corpus build interface: the Python side of the review crate's corpus build under `rebuild/review-rs/`, the Rust engine of the review corpus build's parent (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § Boundary). It owns every format that crosses to the build, as `units_exec` owns the units engine's: nothing pickled crosses, only files and compact JSON this module writes.

`engine()` is the one place the engine a build runs is resolved: the switch, `AMS_CORPUS_BUILD_ENGINE`, read at call time, names it when it is set, and when it is unset the build is `rust` unless the process asks by name for something only the Python build serves (`python_only_requests`), which runs `python`. `ENGINES` holds the engines the build runs; under `rust` `build.build_m1` runs the m1-audit build in the crate (`build._rust_build_m1`), and the Python build stays as the reference the dual runs compare it with. `write_input` writes the build input (`INPUT_FORMAT`, the plan's § The build input) from the values `build._rust_inputs` computes, with the audit's held descriptors when the process inherits them, and `INPUT_FIELDS` is its fields in order. The module-level constants the ported code reads cross in the input's vocabulary (`VOCABULARY`, read by `vocabulary` at call time), each in the typed encoding (`units_exec.encode`). `run_build` runs the build, `ams-review build`, over a written input and returns its result, raising a refusal as `SystemExit` and any other failure as `BuildRunError`.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from rebuild.review import units_exec
from rebuild.review.units_exec import compact, encode, ensure_built, spawn
from rebuild.tools import memory_tally
from rebuild.tools.cycle_timings import record_pool
from rebuild.tools.peak_rss import peak_footprint_self_bytes

INPUT_FORMAT = "ams-review-build/1"
ENGINE_VARIABLE = "AMS_CORPUS_BUILD_ENGINE"
SWITCH_VALUES = ("python", "rust")
ENGINES = ("python", "rust")

# The build input's fields in the order `write_input` writes them (the plan's § The build input).
INPUT_FIELDS = (
    "format",
    "units_init",
    "out_dir",
    "scratch",
    "audit",
    "ledger",
    "family_of",
    "family_keys",
    "family_keys_digest",
    "change_groups",
    "environment",
    "signature_environment",
    "manifest",
    "fonts",
    "static",
    "repo_root",
    "recompute_all",
    "widths",
    "cycle",
    "vocabulary",
)

# The module-level constants the ported parent reads, each by its dotted name, which is also its name in the input. `vocabulary` reads them at call time, and the build refuses an input that lacks a name it reads.
VOCABULARY = (
    "rebuild.pipeline.row_delta.AUDIT_SEGMENT_CAP",
    "rebuild.pipeline.row_delta.CONTENT_TIME_STAMP",
    "rebuild.pipeline.row_delta.FOLD_OVERRUN",
    "rebuild.pipeline.row_delta.SEGMENT_CAP",
    "rebuild.review.app_index.APP_INDEX_FORMAT",
    "rebuild.review.app_index.APP_INDEX_NAME",
    "rebuild.review.app_index.LOCATOR_BLOCK_ROWS",
    "rebuild.review.app_index.LOCATOR_FORMAT",
    "rebuild.review.app_index.LOCATOR_NAME",
    "rebuild.review.app_index.LOCATOR_ROWS_NAME",
    "rebuild.review.audit.ACCEPTANCE_CONFIGS",
    "rebuild.review.audit.AUDIT_HEADER",
    "rebuild.review.audit.BATCH_SIZE",
    "rebuild.review.audit.INPUT_KEY_BYTES",
    "rebuild.review.audit.LEDGER_STATUSES",
    "rebuild.review.audit.MACHINE_CHECKS",
    "rebuild.review.audit.RULED_STATUSES",
    "rebuild.review.audit.SECTIONS",
    "rebuild.review.audit.UNMATCHED_CLASS",
    "rebuild.review.build.CONTRACT_ERRORS_SHOWN",
    "rebuild.review.build.KEY_ONLY_PROCESSES",
    "rebuild.review.build.MANIFEST_FORMAT",
    "rebuild.review.build.PHASE1_HANDOUT_UNITS",
    "rebuild.review.build.VERIFICATION_SAMPLE",
    "rebuild.review.facts.CLASS_UNIT_COUNT_KEYS",
    "rebuild.review.facts.FACTS_FILENAME",
    "rebuild.review.facts.FACTS_FORMAT",
    "rebuild.review.facts.REFERENCE_WINDOW_CODEPOINTS",
    "rebuild.review.unit_cache.MANIFEST_MOVED_NOTE",
    "rebuild.review.unit_cache.NO_STORE_NOTE",
    "rebuild.review.unit_cache.REDERIVING_LABELS",
    "rebuild.review.unit_cache.SIGNATURE_STORE_FORMAT",
    "rebuild.review.unit_cache.SIGNATURE_STORE_NAME",
    "rebuild.review.unit_cache.STORE_FORMAT",
    "rebuild.review.unit_cache.STORE_NAME",
    "rebuild.review.unit_cache.UNREADABLE_NOTE",
    "rebuild.review.unit_index.ASSET_COMPONENTS",
    "rebuild.review.unit_index.INDEX_DEFLATE_THREADS",
    "rebuild.review.unit_index.INDEX_FORMAT",
    "rebuild.review.unit_index.INDEX_MEMBER_BYTES",
    "rebuild.review.unit_index.INDEX_NAME",
    "rebuild.review.unit_index.PART_CLOSING",
    "rebuild.review.unit_index.SHARD_PART_BYTES",
)


class BuildRunError(RuntimeError):
    """The Rust build failed other than by a refusal the Python build raises as `SystemExit`: an `error` result, a panic in any thread among them, or an ending with no result line."""


def switch() -> str | None:
    """`AMS_CORPUS_BUILD_ENGINE` as this process sets it, read at call time, or None when it is unset. A value outside `SWITCH_VALUES` raises ValueError."""
    value = os.environ.get(ENGINE_VARIABLE)
    if value is not None and value not in SWITCH_VALUES:
        raise ValueError(
            f"{ENGINE_VARIABLE}={value!r}; the corpus build engines are {', '.join(SWITCH_VALUES)}"
        )
    return value


def python_only_requests() -> list[str]:
    """The settings this process makes, read at call time, that only the Python build serves, each as `VARIABLE=value`: `AMS_CORPUS_UNITS_ENGINE=python`, whose units workers are the Python build's, and `AMS_CORPUS_MEMORY_TALLY=1`, which tallies the Python build's objects."""
    requests = []
    if units_exec.engine() == "python":
        requests.append(f"{units_exec.ENGINE_VARIABLE}=python")
    if memory_tally.enabled():
        requests.append(f"{memory_tally.TALLY_ENV}=1")
    return requests


def engine() -> str:
    """The corpus build engine a build in this process runs: the switch's value when it is set (`switch`); when it is unset, `python` if the process makes a request only the Python build serves (`python_only_requests`), since it asked for that by name, and `rust` otherwise. Under an explicit `rust` the build refuses such a request (`build._rust_build_m1`). A switch value outside `SWITCH_VALUES` raises ValueError."""
    value = switch()
    if value is not None:
        return value
    return "python" if python_only_requests() else "rust"


def vocabulary() -> dict[str, object]:
    """Every `VOCABULARY` constant's value by its dotted name, read now."""
    values: dict[str, object] = {}
    for name in VOCABULARY:
        module, attribute = name.rsplit(".", 1)
        values[name] = getattr(importlib.import_module(module), attribute)
    return values


def input_document(values: Mapping[str, Any], descriptors: Sequence[int] | None = None) -> dict[str, Any]:
    """The build input's JSON document for the values `build._rust_inputs` computes, one entry a field of `INPUT_FIELDS` in order, with the audit's `descriptors`, base first, or null when no process inherits them. A ledger entry's exemplar keys are sorted, a stamp's detail and the change groups' prose are pairs in order, a code point's family is a `[code point, family]` pair, and the vocabulary's values are in the typed encoding. Values with a missing or an extra field raise ValueError."""
    if tuple(values) != INPUT_FIELDS:
        raise ValueError(f"the build input's fields are {', '.join(INPUT_FIELDS)}, not {', '.join(values)}")
    held = values["audit"]
    groups = values["change_groups"]
    static = values["static"]
    document = dict(values)
    document["audit"] = {
        "path": held["path"],
        "descriptors": None if descriptors is None else list(descriptors),
        "head": held["head"],
        "members": [
            (
                None
                if members is None
                else [[member.section, member.length, member.inflated] for member in members]
            )
            for members in held["members"]
        ],
        "identity": list(held["identity"]),
        "segment_rows": held["segment_rows"],
        "compressed": held["compressed"],
    }
    document["ledger"] = {
        "path": values["ledger"]["path"],
        "entries": [
            {
                "id": entry.id,
                "status": entry.status,
                "why": entry.why,
                "ink_identical": entry.ink_identical,
                "no_verdict": entry.no_verdict,
                "exemplar_keys": sorted([config, codepoints] for config, codepoints in entry.exemplar_keys),
                "title": entry.title,
                "summary": entry.summary,
                "note": entry.note,
            }
            for entry in values["ledger"]["entries"]
        ],
    }
    document["family_of"] = [[codepoint, family] for codepoint, family in values["family_of"].items()]
    document["change_groups"] = {
        "order": list(groups["order"]),
        "prose": [
            [group, {"title": prose.title, "summary": prose.summary, "why": prose.why}]
            for group, prose in groups["prose"].items()
        ],
    }
    for name in ("environment", "signature_environment"):
        stamp = values[name]
        document[name] = {
            "value": stamp["value"],
            "lines": list(stamp["lines"]),
            "detail": [[label, list(lines)] for label, lines in stamp["detail"]],
            "header": stamp["header"],
        }
    document["static"] = {
        "files": [[relative, source] for relative, source in static["files"]],
        "fallback_index": static["fallback_index"],
    }
    document["vocabulary"] = [[name, encode(value)] for name, value in values["vocabulary"].items()]
    return document


def write_input(values: Mapping[str, Any], path: Path, descriptors: Sequence[int] | None = None) -> Path:
    """Write the build input for `values` to `path` as one line of compact ASCII JSON (`input_document`), with the audit's held `descriptors` when the process inherits them, and return the path."""
    path = Path(path)
    path.write_text(compact(input_document(values, descriptors)) + "\n", encoding="ascii")
    return path


def run_build(input_path: Path, descriptors: Sequence[int]) -> dict[str, Any]:
    """Run the Rust build, `ams-review build`, over the build input at `input_path`, and return its `done` result once it exits 0 (the plan's § The process and § The result). The crate is built first (`units_exec.ensure_built`). The process inherits the audit's held `descriptors` and this process's standard error, which carries its console lines, and its standard input is a pipe held open until it exits and never written, so a stop that kills this process ends the build too. A `done` result writes the `corpus-build-rs` pool record to the timings journal `cycle_timings.JOURNAL` names at call time, the route every pool record of the Python build takes, so a test's or a harness's redirect of the journal takes it too: the width is the build's threads, its one worker the process at its peak footprint, and this process's own footprint the controller's. An `exit` result raises `SystemExit` with its message, an `error` result raises `BuildRunError` with its message, and any other ending, a signal or no result line, raises `BuildRunError` naming the exit status."""
    ensure_built()
    sys.stderr.flush()
    process = spawn(
        ["build", f"--input={Path(input_path).absolute()}"],
        stdin=subprocess.PIPE,
        stderr=None,
        pass_fds=descriptors,
    )
    try:
        assert process.stdout is not None
        output = process.stdout.read()
        returncode = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except BrokenPipeError:
                    pass
    lines = output.decode("utf-8", errors="replace").splitlines()
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    if not isinstance(result, dict):
        raise BuildRunError(f"the Rust build exited {returncode} without a result line")
    op = result.get("op")
    if op == "done" and returncode == 0:
        record_pool(
            "corpus-build-rs",
            width=result["threads"],
            worker_peaks={"build": result["peak_footprint"]},
            controller_peak_bytes=peak_footprint_self_bytes(),
            worker_reading="footprint",
        )
        return result
    if op == "exit" and returncode == 1:
        raise SystemExit(result["message"])
    if op == "error" and returncode == 1:
        raise BuildRunError(f"the Rust build failed: {result['message']}")
    raise BuildRunError(f"the Rust build exited {returncode} after the result line {lines[-1][:200]!r}")
