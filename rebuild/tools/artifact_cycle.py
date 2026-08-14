"""The one-command driver for the commit-time artifact cycle.

It mechanizes the commit-time sequence: snapshot the current review surface (the only recovery copy, since everything under rebuild/out is gitignored), recompile M1.otf and vet it, rebuild the review surface in place, carry prior verdicts forward onto the fresh manifest, merge the carried file into the live autosave (rebuild.tools.merge_verdicts, so the app needs no manual import; --no-merge opts out), land echo-prefill verdicts onto the freshly restamped autosave (rebuild.tools.echo_verdicts writes fill records for the blanks in unanimously-judged echo groups, then a second merge_verdicts pass imports them, so cross-cycle echo blanks fill without a sitting-prep pass), land standing-approval verdicts the same way (rebuild.tools.standing_verdicts fills blanks matching the checked-in rules in rebuild/standing-approvals.yaml, so once-and-for-all decisions never queue again), re-baseline the census pins, and run the six gates — always printing a summary table at the end, even on failure.

The exit-code trap this driver exists to defuse: run_m1.main() SystemExits nonzero whenever any oracle rows are UNMATCHED, which is always true mid-migration. Its exit code is therefore not the gate; the four summary JSONs it writes are. The real gates are defect_errors, the boundary and Manual-pin passes, and multi_matched == 0.

The two artifact-independent gates (js, make-test) run from t=0 in a small thread pool while the build chain runs inline-serial in the main thread. gate:conform (the exhaustive font-vs-settle sweep, run_m1 --conform-only) starts after the run_m1 gate passes, queued behind make-test by default. gate:kernel-differential starts at that same point and queues behind the sweep. gate:rebuild is submitted later, only once the build lane's census step has landed its verdict — the pins are part of the suite's input closure, so submitting earlier means running against pins the same cycle is about to judge or rewrite. That ordering carries two rules. On a pass without --update-pins, a census outcome of STALE (live or replayed) defers the gate outright (skip: "deferred", remedy --update-pins) instead of running it: census-pinned failures under known-stale pins are foregone, so the long suite run could report nothing but hints and could never record green. On an --update-pins pass, the suite always reads the pins the census step just rewrote, so a census-module failure is a real failure, the old start-before-update race and its amnesty are gone, and the pass records rebuild-gate-green.json itself. Under the default queue policy gate:rebuild parks at the tail of the make-test -> conform -> kernel-differential chain, so only one heavy gate pool is hot at a time — the build chain (census included) rides alongside whichever one that is at half width rather than serial (see stage_job_budget), which is why the late submission costs no wall time. Co-resident, the two heavy pools oversubscribe the cores roughly 2:1, and measured that contention roughly tripled gate:rebuild's wall time — a worse critical path than running the same work in sequence. --rebuild-pool overlap restores full co-residency (gate:rebuild still waits for the census step). gate:kernel-harness is submitted alongside gate:rebuild and extends that chain by one: it parks behind the rebuild suite, so it runs last of all. It is the cycle's longest pole by a wide margin, it reads nothing the build lane writes, and nothing parks on it, so holding it until every shorter gate has had the box costs the critical path nothing.

gate:kernel-differential is the standing dual-run behind the Rust port of the M1 table build. rebuild.tools.kernel_gate builds the crate, streams every acceptance config out of the kernel in one child, folds each stream through the same table.assemble_tables the Python engine uses, and byte-compares the three artifacts — windows, settlement, treaties — plus the full table digest against what this cycle's run_m1 already wrote under rebuild/out/m1. No second Python fixpoint is ever paid, which is what makes the proof cheap enough to stand on every cycle: the Python side of the comparison is the artifacts themselves. The gate never rebuilds any of them — a windows head or a table-digests record stamped for other inputs is a stale gate, red, with the artifact cycle itself as the remedy, and so is a table set `run_m1 --engine rust` wrote, because a differential fed the kernel's own fold would read identical by construction — so it can only ever compare the two engines over one Python-built set of sources. Its key is narrow by the same reasoning as tables_value: the spec inputs, the kernel's Python half (table, settle, model, specificity, kernel_io, kernel_exec), the gate's own executable, and rebuild/kernel-rs/'s sources and lockfile. The built binary is deliberately outside it — the key says "these sources", and the gate's own cargo build is what makes the binary match them.

gate:kernel-harness is that gate's deep counterpart, and the two split the evidence between them along the line their keys draw. rebuild.tools.kernel_harness_gate re-runs the three landing harnesses the port was accepted on — the exhaustive kernel_liveness sweep, kernel_fixpoint over its three worlds, and kernel_differential — in five arms, stopping at the first that fails and writing kernel_harness_summary.json. At roughly an hour it can only ever be an occasional gate, so its key is the alphabet's *structure* (trace_memo.spec_structure_digest: the roster, the ligature sequences, the class and group memberships) together with both engines' kernel sources, and deliberately not the rune ink every sibling key carries. That is the whole design: the ink edits of an ordinary look-edit-look pass leave this gate proved and gate:kernel-differential — which stands on every cycle over exactly those edits — carrying the per-edit proof, while a migration-shaped change (a family joining the roster, a kernel source moving, a rustc upgrade) arms the deep sweep again.

gate:make-test is auto-skipped when its input closure is provably unchanged since the last green run. The closure is every tracked or untracked-unignored file outside rebuild/, glyph_data/runes/, doc/, tmp/, .claude/, and Markdown — nothing `make test` executes (make all -> build_font over glyph_data/*.yaml non-recursively, typst, pyright over tools/ test/ conftest.py, pytest test/ site/) reads those trees, so a diff confined to them cannot move the gate's outcome and re-running its ≈15 CPU-minutes would verify nothing. The last green fingerprint lives in rebuild/out/make-test-green.json, written by rebuild.tools.make_test_gate — the `make test` entry point — on every green run, so interactive greens and cycle greens share one record and `make test` itself self-skips on the same test. cycle_summary.json still records the fingerprint the cycle ran (or validly skipped) against, and prior_make_test_fingerprint falls back to it when the shared record is absent. The fingerprint sees file content only — a system-toolchain change (a typst upgrade, say; pyright and pytest are pinned through uv.lock, which is in the closure) is invisible to it. --force-make-test runs the gate regardless (as does `make test FORCE=1` inside the wrapper).

The verdict plumbing — snapshot, carry, merge, echo-fill and its merge, standing-fill and its merge, complaints — is guarded the same way, by rebuild/out/plumbing-green.json. Every one of those steps is a pure function of the surface, the verdicts master, the live store, the checked-in standing approvals, and its own code, so the key is (the surface's inputs fingerprint and stamp, the master's path and bytes, the autosave's bytes, standing-approvals' bytes, all of rebuild/tools/ plus review/serve.py). Two of those components are there because a narrower key looked sufficient and was not. The master, because it is the one input the autosave's hash cannot see: an export dropped at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code, because every sibling key folds in its own stage's executable and this chain's lives in a tree no other fingerprint reads — without it a fix to a fill's matcher or the carry's ink fallback would be skipped as already proven, silently never running.

The key is captured the moment the chain closes, not at the end of the pass, so a store write landing while the census runs cannot be absorbed into a fixpoint nothing verified; the record itself is written later, once complaints has also succeeded. And the fixpoint is claimed only when the chain can witness it. The steps feed forward — the carry's merge gives echo-fill new agreement to read, and echo-fill only removes blanks, so it can never hand standing-fill work it did not already have — but standing-fill runs last with nothing to re-read it, and a standing fill can make an echo group unanimous while a blank sibling remains. So a green is recorded only when the standing merge moved nothing, which is exactly when that cascade is closed; a pass whose standing fills did land leaves the next pass to finish it. That ordering is also why complaints is not deferrable: deferring it would keep a settled pass from ever recording the fixpoint it reached, trading ≈3s once for the whole chain's ≈23s on every pass after.

The skip demands that the surface build be skipping too, which is what makes the stamp knowable before the pass runs, and it takes the snapshot with it: the snapshot exists to survive this cycle's surface rewrite and to feed this cycle's carry, and a pass doing neither needs no copy. Such a pass also leaves the snapshot pile alone rather than pruning it to the copy it never made, so the stamp-aligned snapshot the last refreshing pass left stays on disk as the recovery source describe_carry_source points at. A flag that names a carry output or a snapshot directory refuses the skip outright, since honoring it would mean writing neither.

The same provably-unchanged principle guards every other heavy stage, each keyed by a content fingerprint over that stage's full input closure and a green record written only after that exact content passed live: run_m1 skips on rebuild/out/run-m1-green.json (the Stage A fingerprint components plus the oracle's subset tables and uv.lock) and re-evaluates its gate from the four summary JSONs already on disk; gate:conform skips on conform-green.json (the run_m1 key plus the M1.otf bytes and the sweep horizon); gate:kernel-differential skips on kernel-differential-green.json (the spec inputs, the kernel's Python and Rust sources, and the gate's own executable); gate:kernel-harness skips on kernel-harness-green.json (the alphabet's structure, both engines' kernel sources, the harness tooling, and the rustc identity — the same shape one grain coarser, which is what keeps the hour off every ink edit); gate:rebuild skips on rebuild-gate-green.json (the suite's repo closure under rebuild/ and glyph_data/ plus the out/m1 artifacts, site fonts, baselines, conftest.py, pyproject.toml, and uv.lock — also written by rebuild.tools.rebuild_gate, the `make test-rebuild` entry point, so interactive suite greens and cycle greens share one record); surface-build skips when the manifest's recorded inputs fingerprint already equals the one a build would stamp now (a rebuild would be byte-identical, mtime-floored generated_at included, so the autosave stays aligned); and the census check skips on census-result.json, which — unlike the green records — is written after stale checks too: the check is informational (staleness never fails a cycle) and deterministic over its fingerprinted inputs, so a pass whose key matches a recorded stale outcome replays the recorded mismatch lines instead of re-running the check. Pins go stale on every rune edit and stay stale until --update-pins, so without the stale record the converging loop re-paid the full census — three parses of the divergence audit plus a serial ink re-shape of every pre-merge unit — on every pass. The surface, conform, rebuild, and census skips engage only on cycles where run_m1 itself skipped, so a live M1 rebuild can never invalidate a key mid-cycle; green records are written only when the key still matches after the work ran, and a red result whose key matches its record deletes the record (for the census that deletion covers only a check with no verdict to record — a crash or a missing pins file). --fresh runs everything regardless.

--defer-gates, which `make review-cycle` passes, turns the cycle from a one-pass verification into a converging loop. On a *refreshing* pass — one where run_m1 or the surface build has real work — the five heavy gates (rebuild, conform, kernel-differential, kernel-harness, make-test) are recorded pending instead of run, so a rune edit costs only the artifact chain and the letters are on screen in a fraction of the time. The census rides the same deferral: it is informational, no gate reads it, and the one step whose scheduling depends on it — gate:rebuild, submitted only after the census lands a verdict — is itself deferred on any refreshing pass, so leaving it for the converging pass takes a minute off the time to letters-on-screen without changing what any pass verifies. An --update-pins pass never defers it, since refreshing the pins is that pass's whole point. Only a gate that would otherwise run live is deferred: one an auto-skip already proved stays proved, so a pass that merely restamps the review UI can never turn a green gate pending. The next pass has no artifact work left, every stage auto-skips, and the pending gates run against settled artifacts; the pass after that skips those too and costs seconds. Deferral is never a waiver — a deferred gate rides `skip: "deferred"` into the cycle summary, which rebuild.review.status counts as unverified, so `make verdict-ready` and the app banner both stay NOT READY until the loop converges. --no-defer-gates runs them in the one pass, which is what `make artifact-cycle` does at commit time, and --fresh and --force-make-test likewise override deferral for the gates they force. Rehearsal mode (--review-out) never defers: it writes its surface somewhere else, so there is no live surface to see sooner, and its surface build is unskippable by construction — every rehearsal pass would look refreshing and the loop would never converge.

Which passes cost the reviewer their letters is decided here rather than by the caller, because only the resolved plan knows. Two of the things a cycle writes belong to the running app — the surface it serves, where livereload watches every shard and a restamped manifest orphans the tab's store, and the verdict store, which merge_verdicts refuses to touch under a live server because an open tab would flush its own copy back over the merge. A pass whose plan skips both writes neither, so a listening server is left alone and the letters stay on screen for the whole run: that is the gate pass, whose half hour of verification the deferred gates exist to move off the look-edit-look path, and which used to black the app out for every minute of it. A pass that does write under the app still needs the port to itself, and --stop-server (which `make review-cycle` passes) is permission to take it — terminate the server and wait out the port — where a bare run still refuses and says how. Retention is the third writer: the app appends to the journal as you verdict, and a compaction rewrites the file around a read, so with a server up the journal and the stash sweep that indexes off it are both left for a later pass.

A green finish ends with a retention pass over the cycle's own disk piles, all of them regenerable or journal-covered: every tmp/review-pre-* snapshot except this cycle's is deleted (a snapshot is read once, by its own cycle's carry, and never again), root verdicts-carried-*.json files not stamped for the live surface are deleted (only the stamp-aligned frontier is ever read; the tracked copy under rebuild/evidence/ is never touched), verdicts-autosave-* stashes not referenced by a journal event at or after the last base event are deleted (the journal, not the stashes, is the sanctioned recovery path — and the reference index is the test because a stash's mtime predates the event that created it), and the journal itself is compacted to the newest base event older than RETENTION_WINDOW_DAYS, keeping at least that many days of --restore-as-of history. Failed, interrupted, first-run, and rehearsal cycles never prune; --keep-history opts out entirely; a retention error warns and never turns a green cycle red.

Run as: uv run python rebuild/tools/artifact_cycle.py — the carry source is auto-resolved from the autosave and the verdicts-*.json exports; pass --verdicts to name one explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if TYPE_CHECKING:
    from rebuild.tools.cycle_timings import CycleTimings
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
CENSUS_PINS = ROOT / "rebuild" / "review-census-pins.json"
CARRY_TOOL = ROOT / "rebuild" / "tools" / "carry_verdicts.py"
ECHO_TOOL = ROOT / "rebuild" / "tools" / "echo_verdicts.py"
ECHO_FILL = ROOT / "verdicts-echo-fill.json"
STANDING_TOOL = ROOT / "rebuild" / "tools" / "standing_verdicts.py"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
CYCLE_SUMMARY = ROOT / "rebuild" / "out" / "cycle_summary.json"
CYCLE_TIMINGS = ROOT / "rebuild" / "out" / "cycle-timings.ndjson"
MAKE_TEST_GREEN = ROOT / "rebuild" / "out" / "make-test-green.json"
RUN_M1_GREEN = ROOT / "rebuild" / "out" / "run-m1-green.json"
CONFORM_GREEN = ROOT / "rebuild" / "out" / "conform-green.json"
KERNEL_DIFFERENTIAL_GREEN = ROOT / "rebuild" / "out" / "kernel-differential-green.json"
KERNEL_HARNESS_GREEN = ROOT / "rebuild" / "out" / "kernel-harness-green.json"
REBUILD_GATE_GREEN = ROOT / "rebuild" / "out" / "rebuild-gate-green.json"
CENSUS_RESULT = ROOT / "rebuild" / "out" / "census-result.json"
PLUMBING_GREEN = ROOT / "rebuild" / "out" / "plumbing-green.json"
JSTEST_DIR = ROOT / "rebuild" / "review" / "jstests"
REVIEW_PORT = 7294

POOL_POLICIES = ("queue", "overlap")
REBUILD_POOL_POLICY_DEFAULT = "queue"
DEFERRABLE_GATES = ("rebuild", "conform", "kernel-differential", "kernel-harness", "make-test")
DEFER_NOTE = "surface refreshed this pass; run the cycle again to run it"
PLUMBING_SKIP_NOTE = "surface, verdicts master, live store, and standing approvals unchanged since the last complete plumbing pass; --fresh overrides"
STALE_CENSUS_DEFER_NOTE = "stale census pins; re-run with --update-pins to refresh them first"
SERVER_STAYS_UP_NOTE = "writes neither the surface the app serves nor the verdict store it holds"
SERVER_STOP_PATTERN = r"rebuild\.review\.serve"
SERVER_STOP_TIMEOUT = 15.0
_GATE_POOL_WORKERS = 7
_CONFORM_JOBS_CAP = 8
CONFORM_HORIZON_DEFAULT = 5
KERNEL_THREADS_DEFAULT = 3
RETENTION_WINDOW_DAYS = 7

M1_SUMMARY_FILES = {
    "pipeline": M1_OUT / "pipeline_summary.json",
    "boundary": M1_OUT / "boundary_equivalence_summary.json",
    "manual_pins": M1_OUT / "manual_pins_summary.json",
    "oracle": M1_OUT / "oracle_summary.json",
}
CONFORM_SUMMARY = M1_OUT / "conform_summary.json"
KERNEL_DIFFERENTIAL_SUMMARY = M1_OUT / "kernel_differential_summary.json"
KERNEL_HARNESS_SUMMARY = M1_OUT / "kernel_harness_summary.json"

BASELINE_REBUILD_FAILURES = frozenset({"rebuild/test_surface.py::test_real_cell_bindings_all_match"})

CENSUS_HINT_MODULES = frozenset(
    {
        "test_review_audit",
        "test_review_build",
        "test_review_families",
        "test_review_ink",
    }
)

REBUILD_PYTEST_ARGV = [
    "uv",
    "run",
    "pytest",
    "rebuild/",
    "-n",
    "auto",
    "--dist",
    "worksteal",
    "-q",
    "--tb=no",
    "-rfE",
]

MAKE_TEST_EXEMPT_PREFIXES = (
    "rebuild/",
    "glyph_data/runes/",
    "doc/",
    "tmp/",
    ".claude/",
    "bench-the-rebuild/",
)


def make_test_exempt(path: str) -> bool:
    """Whether a repo-relative path is provably outside gate:make-test's input closure. The exempt trees are safe because nothing the gate executes reads them: build_font globs glyph_data/*.yaml non-recursively (never glyph_data/runes/), and test/, site/, tools/, conftest.py contain no reference to rebuild/ or the rune files; Markdown is never an input to any gate. bench-the-rebuild/ is measurement scaffolding that only ever reads the tree — nothing under test/, site/, tools/ or conftest.py imports it, and pytest never collects it (testpaths is test/ and site/)."""
    return path.endswith(".md") or any(path.startswith(prefix) for prefix in MAKE_TEST_EXEMPT_PREFIXES)


def make_test_closure_files(root: Path) -> list[str] | None:
    """Every tracked or untracked-unignored file that could affect gate:make-test, repo-relative and sorted. None when git is unavailable, in which case the caller must run the gate unconditionally."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    paths = {entry for entry in result.stdout.split("\0") if entry}
    return sorted(path for path in paths if not make_test_exempt(path))


def make_test_closure_fingerprint(root: Path = ROOT) -> str | None:
    """Content hash of gate:make-test's input closure, read from the worktree (not the index) so uncommitted edits count. A deleted-but-tracked file hashes as absent, so deletions move the fingerprint too."""
    files = make_test_closure_files(root)
    if files is None:
        return None
    digest = hashlib.sha256()
    for rel in files:
        path = root / rel
        try:
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            file_hash = "absent"
        digest.update(f"{rel}\t{file_hash}\n".encode())
    return digest.hexdigest()


def read_green_record(path: Path) -> dict | None:
    """A gate's last-green record ({fingerprint, finished_at}); None when absent or malformed."""
    try:
        record = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if isinstance(record, dict) and isinstance(record.get("fingerprint"), str):
        return record
    return None


def _record_outcome(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"format": f"ams-{path.stem}/1", **payload, "finished_at": stamp}) + "\n")
    os.replace(tmp, path)


def record_green(path: Path, fingerprint: str, files: dict[str, str] | None = None) -> None:
    """`files` is the per-file `label -> digest` map behind the fingerprint, when the caller has it: stored beside the key so a later skip miss can name exactly which input moved instead of reporting only that some digest did."""
    payload: dict = {"fingerprint": fingerprint}
    if files is not None:
        payload["files"] = files
    _record_outcome(path, payload)


def clear_contradicted_green(path: Path, fingerprint: str | None) -> None:
    """A red result over content whose fingerprint still matches the recorded green contradicts the record; delete it so no later cycle can skip on a falsified green."""
    record = read_green_record(path)
    if fingerprint is not None and record is not None and record["fingerprint"] == fingerprint:
        path.unlink(missing_ok=True)


def record_census_result(path: Path, fingerprint: str, status: str, mismatches: list[str]) -> None:
    """The census check's outcome record, written after stale checks as well as clean ones: the check is informational and deterministic over the inputs its fingerprint hashes, so a stale outcome over unchanged inputs is as replayable as a clean one."""
    _record_outcome(path, {"fingerprint": fingerprint, "status": status, "mismatches": mismatches})


def read_census_result(path: Path) -> dict | None:
    """The recorded census outcome ({fingerprint, status, mismatches}); None when absent or malformed, missing outcome fields included."""
    record = read_green_record(path)
    if (
        record is not None
        and record.get("status") in ("clean", "stale")
        and isinstance(record.get("mismatches"), list)
        and all(isinstance(line, str) for line in record["mismatches"])
    ):
        return record
    return None


def record_plumbing_green(fingerprint: str, carry_out: Path | None, path: Path | None = None) -> None:
    """The verdict plumbing's last-green record. It carries the carried-verdicts file the recorded pass wrote as well as the key, so a later pass that skips the chain can still name the live frontier in its summary instead of reporting no carry at all."""
    _record_outcome(
        path if path is not None else PLUMBING_GREEN,
        {"fingerprint": fingerprint, "carry_out": None if carry_out is None else str(carry_out)},
    )


def read_make_test_green(path: Path | None = None) -> dict | None:
    """The shared last-green record for `make test`, written by rebuild.tools.make_test_gate on every green run — interactive or as gate:make-test."""
    return read_green_record(path if path is not None else MAKE_TEST_GREEN)


def record_make_test_green(fingerprint: str, path: Path | None = None) -> None:
    record_green(path if path is not None else MAKE_TEST_GREEN, fingerprint)


def prior_make_test_fingerprint(
    summary_path: Path | None = None, green_path: Path | None = None
) -> str | None:
    """The closure fingerprint of the last green `make test` run: the shared green record when present (always at least as fresh, since every green run rewrites it), else the fingerprint the previous cycle recorded green or validly carried forward."""
    record = read_make_test_green(green_path)
    if record is not None:
        return record["fingerprint"]
    try:
        summary = json.loads((summary_path if summary_path is not None else CYCLE_SUMMARY).read_text())
    except OSError, ValueError:
        return None
    value = summary.get("make_test_fingerprint") if isinstance(summary, dict) else None
    return value if isinstance(value, str) else None


M1_ARTIFACT_NAMES = ("M1.otf", "divergence-audit.tsv", "inputs_fingerprint.json")
REBUILD_GATE_EXEMPT_PREFIXES = ("rebuild/evidence/", "rebuild/review/jstests/")


def _sha256_path(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def _closure_digest(root: Path, rel: str) -> str:
    """Rune YAMLs hash by their prose-blind digest (fingerprint.rune_file_digest) so a documentation edit does not re-run the gate; the two spec_load tests that read the live rune files assert only field presence, never prose, which is what keeps the exclusion sound."""
    from rebuild.pipeline import fingerprint

    if rel.startswith("glyph_data/runes/") and rel.endswith(".yaml"):
        try:
            return fingerprint.rune_file_digest(root / rel)
        except OSError:
            return "absent"
    return _sha256_path(root / rel)


def _digest_lines(lines: list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()


def _subset_tables(root: Path) -> list[Path]:
    return sorted((root / "rebuild" / "out" / "m1").glob("baseline-*.subset.tsv.gz"))


def run_m1_skip_lines(root: Path = ROOT) -> list[str]:
    """The per-file `label\\tdigest` lines behind `run_m1_skip_fingerprint`: every data input and pipeline module individually (rune files prose-blind), the full baselines as one value, the oracle's subset tables, and uv.lock. Stored in the green record so a skip miss can name exactly which input moved."""
    from rebuild.pipeline import fingerprint

    lines = fingerprint.data_lines(root)
    lines.append(f"baselines\t{fingerprint.baselines_value(root)}")
    lines += fingerprint.path_lines(root, fingerprint.pipeline_code_paths(root))
    lines += [f"{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
    lines.append(f"uv.lock\t{_sha256_path(root / 'uv.lock')}")
    return lines


def _files_of(lines: list[str]) -> dict[str, str]:
    return dict(line.split("\t", 1) for line in lines)


def run_m1_skip_files(root: Path = ROOT) -> dict[str, str]:
    return _files_of(run_m1_skip_lines(root))


def run_m1_skip_fingerprint(root: Path = ROOT) -> str:
    """Content key over everything a full run_m1 reads: the data inputs and pipeline code per file, the full baselines, the oracle's subset tables — which the `baselines` line covers only by proxy — and uv.lock for the pinned toolchain. Matching the recorded green means a rerun would reproduce rebuild/out/m1 byte for byte."""
    return _digest_lines(run_m1_skip_lines(root))


def moved_inputs_note(record: dict | None, current: dict[str, str], limit: int = 8) -> str | None:
    """Which inputs moved since a green record that stored its per-file lines — the skip-miss diagnostic. None when the record is absent, predates the `files` payload, or (fingerprint notwithstanding) no stored line actually differs."""
    if record is None or not isinstance(record.get("files"), dict):
        return None
    stored = {name: value for name, value in record["files"].items() if isinstance(value, str)}
    moved = [
        f"{name} (changed)"
        for name in sorted(stored.keys() & current.keys())
        if stored[name] != current[name]
    ]
    moved += [f"{name} (new)" for name in sorted(current.keys() - stored.keys())]
    moved += [f"{name} (gone)" for name in sorted(stored.keys() - current.keys())]
    if not moved:
        return None
    shown = ", ".join(moved[:limit])
    return f"{shown} and {len(moved) - limit} more" if len(moved) > limit else shown


def m1_artifacts_present(root: Path = ROOT) -> bool:
    """Whether rebuild/out/m1 still holds everything a skipped run_m1 must leave behind: the four gate summaries, the artifacts the surface build consumes, and the digest record gate:kernel-differential compares against. table-digests.json is checked here rather than through M1_ARTIFACT_NAMES because that constant also feeds gate:rebuild's key, which has no business moving when the digest record does; without the check, losing the record leaves run_m1 skipping while the kernel gate reds on its absence with a remedy — run the cycle — that reproduces the same skip."""
    m1 = root / "rebuild" / "out" / "m1"
    names = [path.name for path in M1_SUMMARY_FILES.values()] + list(M1_ARTIFACT_NAMES)
    names.append("table-digests.json")
    return all((m1 / name).exists() for name in names)


def conform_skip_lines(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> list[str]:
    lines = run_m1_skip_lines(root)
    lines.append(f"M1.otf\t{_sha256_path(root / 'rebuild' / 'out' / 'm1' / 'M1.otf')}")
    lines.append(f"horizon\t{horizon}")
    return lines


def conform_skip_files(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> dict[str, str]:
    return _files_of(conform_skip_lines(root, horizon))


def conform_skip_fingerprint(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> str:
    """The run_m1 lines plus the compiled font's bytes and the sweep horizon — exactly what gate:conform sweeps. The horizon is in the key so a green at a shallower horizon can never satisfy a deeper gate."""
    return _digest_lines(conform_skip_lines(root, horizon))


KERNEL_PIPELINE_SOURCES = (
    "table.py",
    "settle.py",
    "model.py",
    "specificity.py",
    "kernel_io.py",
    "kernel_exec.py",
)
KERNEL_GATE_SOURCES = ("kernel_gate.py", "kernel_fixpoint.py", "kernel_differential.py")


def kernel_crate_paths(root: Path = ROOT) -> list[Path]:
    """The Rust half of the kernel, enumerated tree by tree rather than globbed over the crate directory: rebuild/kernel-rs/target/ is gitignored, runs to gigabytes, and holds the built binary itself, so a directory glob would hash the artifact the gate builds instead of the sources it builds it from. Cargo.lock is here because a dependency bump can move the kernel's answers without any *.rs byte moving."""
    crate = root / "rebuild" / "kernel-rs"
    return [
        crate / "Cargo.toml",
        crate / "Cargo.lock",
        *sorted((crate / "src").rglob("*.rs")),
        *sorted((crate / "tests").rglob("*.rs")),
    ]


def _rustc_identity() -> str:
    """The toolchain that would compile the crate, folded into gate:kernel-differential's key because the binary under test is sources times compiler: a rustc upgrade can move codegen with no hashed byte moving, and a key blind to it would keep auto-skipping the gate as proved over a binary nobody has compared since the upgrade. `absent` when there is no rustc to ask — the gate itself then reds with the toolchain remedy rather than skipping."""
    try:
        finished = subprocess.run(["rustc", "--version", "--verbose"], capture_output=True, timeout=30)
    except OSError, subprocess.TimeoutExpired:
        return "absent"
    if finished.returncode != 0:
        return "absent"
    return hashlib.sha256(finished.stdout).hexdigest()


def kernel_differential_skip_lines(root: Path = ROOT) -> list[str]:
    """The per-file `label\\tdigest` lines behind `kernel_differential_skip_fingerprint`: the spec inputs both engines read (rune files prose-blind), the kernel's Python half, the gate's own executable together with the two harnesses it imports its comparison pieces from, the crate's sources, and the toolchain that compiles them. Stored in the green record so a skip miss can name exactly which input moved."""
    from rebuild.pipeline import fingerprint

    lines = fingerprint.data_lines(root)
    lines += fingerprint.path_lines(
        root, [root / "rebuild" / "pipeline" / name for name in KERNEL_PIPELINE_SOURCES]
    )
    lines += fingerprint.path_lines(root, [root / "rebuild" / "tools" / name for name in KERNEL_GATE_SOURCES])
    lines += fingerprint.path_lines(root, kernel_crate_paths(root))
    lines.append(f"rustc\t{_rustc_identity()}")
    return lines


def kernel_differential_skip_files(root: Path = ROOT) -> dict[str, str]:
    return _files_of(kernel_differential_skip_lines(root))


def kernel_differential_skip_fingerprint(root: Path = ROOT) -> str:
    """Content key over everything that can move the Rust-vs-Python differential's verdict: the spec the two engines both enumerate, the Python modules the kernel is a port of, the gate's own executable, and the crate. Matching the recorded green means both engines would fold the same sources again and land on the same artifacts. Deliberately absent are the baselines, the subset tables, M1.otf and the thread width — none of them feeds a table, and byte identity holds at any width — and the compared artifacts themselves, whose bytes are a function of inputs already in the key; the gate's own staleness guard is what covers the case where rebuild/out/m1 has drifted from them."""
    return _digest_lines(kernel_differential_skip_lines(root))


KERNEL_HARNESS_SOURCES = (
    "kernel_harness_gate.py",
    "kernel_fixpoint.py",
    "kernel_liveness.py",
    "kernel_differential.py",
    "kernel_parity.py",
    "fuzz_settlement_corpus.py",
    "export_settlement_corpus.py",
)


def kernel_harness_skip_lines(root: Path = ROOT) -> list[str]:
    """The per-file `label\\tdigest` lines behind `kernel_harness_skip_fingerprint`: the resolved alphabet structure both engines enumerate over (`trace_memo.spec_structure_digest`, which is ink-blind and moves on the roster, the ligature sequences, and the predicate-class and group memberships), the kernel's Python half, the harness gate's own executable together with the harnesses and corpus tools it drives, the crate's sources, and the toolchain that compiles them. Stored in the green record so a skip miss can name exactly which input moved."""
    from rebuild.pipeline import fingerprint, spec_load, trace_memo

    lines = [f"spec_structure\t{trace_memo.spec_structure_digest(spec_load.load_default_spec())}"]
    lines += fingerprint.path_lines(
        root, [root / "rebuild" / "pipeline" / name for name in KERNEL_PIPELINE_SOURCES]
    )
    lines += fingerprint.path_lines(
        root, [root / "rebuild" / "tools" / name for name in KERNEL_HARNESS_SOURCES]
    )
    lines += fingerprint.path_lines(root, kernel_crate_paths(root))
    lines.append(f"rustc\t{_rustc_identity()}")
    return lines


def kernel_harness_skip_files(root: Path = ROOT) -> dict[str, str]:
    return _files_of(kernel_harness_skip_lines(root))


def kernel_harness_skip_fingerprint(root: Path = ROOT) -> str:
    """Content key over everything that can move the three landing harnesses' verdicts at the grain this gate re-proves them: the shape of the alphabet the harnesses sweep, both engines' kernel sources, the harness tooling itself, and the crate's toolchain. Matching the recorded green means the ≈55 minutes of exhaustive liveness, pinned and shipping fixpoints, and differential would re-run the same sweep over the same two engines. The rune ink is deliberately absent — `fingerprint.data_lines`, which every sibling key carries, would restart the sweep on every geometry edit, and per-edit equivalence is already gate:kernel-differential's standing job; this gate is its deep counterpart and arms once per migration-shaped change instead. Also absent: `baseline_subset.M1_ALPHABET` (the conformance subset is the sweep's input, not its structure, and the spec digest already moves when a family joins the roster), the built binary (the key says "these sources", and the gate's own cargo build is what makes the binary match them), the artifacts the differential arm compares (a function of inputs already in the key), and the thread width the arms run at (the answers are byte-identical at any width)."""
    return _digest_lines(kernel_harness_skip_lines(root))


def rebuild_gate_closure_files(root: Path) -> list[str] | None:
    """Every tracked or untracked-unignored file the rebuild pytest suite can read from the repo: rebuild/ and glyph_data/ (minus Markdown, the carried-verdict evidence, and the JS-only jstests) plus the root conftest.py, pyproject.toml, and uv.lock. None when git is unavailable, in which case the caller must run the gate unconditionally."""
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "rebuild/",
                "glyph_data/",
                "conftest.py",
                "pyproject.toml",
                "uv.lock",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    paths = {entry for entry in result.stdout.split("\0") if entry}
    return sorted(
        path
        for path in paths
        if not path.endswith(".md")
        and not any(path.startswith(prefix) for prefix in REBUILD_GATE_EXEMPT_PREFIXES)
    )


def rebuild_gate_skip_fingerprint(root: Path = ROOT) -> str | None:
    """Content key over gate:rebuild's full input closure: the repo files from rebuild_gate_closure_files plus the out/m1 artifacts the suite reads and the site fonts and baselines it shapes against. The verdict store is deliberately absent — the suite exercises it only through fixtures — which is what lets verdict-only cycles skip the gate."""
    from rebuild.pipeline import fingerprint

    files = rebuild_gate_closure_files(root)
    if files is None:
        return None
    m1 = root / "rebuild" / "out" / "m1"
    lines = [f"{rel}\t{_closure_digest(root, rel)}" for rel in files]
    lines += [f"m1/{name}\t{_sha256_path(m1 / name)}" for name in M1_ARTIFACT_NAMES]
    lines += [f"m1/{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
    lines.append(f"fonts\t{fingerprint.hash_paths(root, fingerprint.font_paths(root))}")
    lines.append(f"baselines\t{fingerprint.baselines_value(root)}")
    return _digest_lines(lines)


def surface_build_skippable(root: Path = ROOT, review_out: Path | None = None) -> bool:
    """Whether rebuilding the review surface would reproduce its content byte for byte, so the build can be skipped with the autosave still aligned. True only when the manifest's recorded inputs fingerprint equals the one a build would stamp now (Stage A as recorded by run_m1, Stage B recomputed) and every shard the manifest names is still present. generated_at is mtime-derived, so a rebuild after pure mtime churn (git checkout, touch) could restamp it even with identical content — skipping deliberately keeps the existing stamp instead, which preserves the manifest-autosave alignment the stamp exists to key."""
    from rebuild.pipeline import fingerprint

    surface = review_out if review_out is not None else REVIEW_OUT
    try:
        manifest = json.loads((surface / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    recorded = manifest.get("inputs_fingerprint")
    if not isinstance(recorded, dict):
        return False
    stage_a = fingerprint.read_stage_a(root / "rebuild" / "out" / "m1")
    if stage_a is None:
        return False
    before_font, junior_font = fingerprint.font_paths(root)
    expected = {**stage_a, **fingerprint.stage_b(root, before_font, junior_font)}
    if recorded != expected:
        return False
    try:
        shards = [meta["shard"] for meta in manifest["classes"] if meta.get("shard")]
    except KeyError, TypeError:
        return False
    return all((surface / shard).exists() for shard in shards)


def census_skip_fingerprint(root: Path = ROOT, surface: Path | None = None) -> str | None:
    """Content key over the census check's inputs: the surface identity (its recorded fingerprint and stamp), the checked-in pins, and the source artifacts the ink and family groups re-shape (the audit, the compiled font, and the subset tables; the site fonts and spec ride inside the manifest fingerprint). None when the surface has no fingerprinted manifest."""
    surface_dir = surface if surface is not None else REVIEW_OUT
    try:
        manifest = json.loads((surface_dir / "manifest.json").read_text())
    except OSError, ValueError:
        return None
    fp = manifest.get("inputs_fingerprint")
    if not isinstance(fp, dict):
        return None
    m1 = root / "rebuild" / "out" / "m1"
    lines = [
        f"manifest\t{json.dumps(fp, sort_keys=True)}",
        f"generated_at\t{manifest.get('generated_at')}",
        f"pins\t{_sha256_path(root / 'rebuild' / 'review-census-pins.json')}",
        f"M1.otf\t{_sha256_path(m1 / 'M1.otf')}",
        f"audit\t{_sha256_path(m1 / 'divergence-audit.tsv')}",
    ]
    lines += [f"m1/{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
    return _digest_lines(lines)


def plumbing_skip_fingerprint(
    root: Path = ROOT, surface: Path | None = None, master: Path | None = None
) -> str | None:
    """Content key over everything the verdict plumbing reads: the surface it resolves unit ids against, the verdicts master it carries forward, the live store it merges into, the checked-in standing approvals, and the chain's own code. Carry, merge, both fills with their merges, and the complaint docket are pure functions of exactly those, and the chain is idempotent once it has run — so a key matching the record a *complete* chain left behind proves re-running it would write nothing new. The master is in the key because it is the one input the autosave's hash cannot see: an export dropped at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code is in it for the same reason every sibling key carries its own stage's executable — a fix to a fill's matcher or to the carry's fallback must run rather than be skipped as proven — and it is the whole of rebuild/tools/ plus review/serve.py: this driver builds the chain's argv and merge_verdicts reads the store through serve.py, while review/'s other modules already ride inside the manifest fingerprint's review_code. None when the surface has no fingerprinted manifest or no master was resolved."""
    if master is None:
        return None
    surface_dir = surface if surface is not None else REVIEW_OUT
    try:
        manifest = json.loads((surface_dir / "manifest.json").read_text())
    except OSError, ValueError:
        return None
    fp = manifest.get("inputs_fingerprint")
    if not isinstance(fp, dict):
        return None
    from rebuild.pipeline import fingerprint

    lines = [
        f"manifest\t{json.dumps(fp, sort_keys=True)}",
        f"generated_at\t{manifest.get('generated_at')}",
        f"master\t{master}\t{_sha256_path(Path(master))}",
        f"autosave\t{_sha256_path(root / 'verdicts-autosave.json')}",
        f"standing\t{_sha256_path(root / 'rebuild' / 'standing-approvals.yaml')}",
        f"tools_code\t{fingerprint.hash_paths(root, sorted((root / 'rebuild' / 'tools').glob('*.py')))}",
        f"serve\t{_sha256_path(root / 'rebuild' / 'review' / 'serve.py')}",
    ]
    return _digest_lines(lines)


def resolve_snapshot_dir(tmp_dir: Path, short_id: str) -> Path:
    """A free name for this pass's surface snapshot. The short id names the commit, but a snapshot names one run: two cycles at an unmoved HEAD — every look-edit-look pass, and every retry after a cycle that stopped early — would otherwise land on the same directory, and the driver refuses to overwrite one because an unfinished cycle's snapshot can be the only copy of a surface it already clobbered. So take the first free `-2`, `-3`, … suffix instead, and let unfinished_cycle_snapshot spare the copy that refusal was protecting. They cannot pile up otherwise: prune_snapshots globs `review-pre-*` and keeps only the current pass's. The carried-verdicts filename keeps the bare short id, since that one is deliberately commit-stamped."""
    base = tmp_dir / f"review-pre-{short_id}"
    if not base.exists():
        return base
    suffix = 2
    while (candidate := tmp_dir / f"review-pre-{short_id}-{suffix}").exists():
        suffix += 1
    return candidate


def deferred_gates(*, defer: bool, refreshing: bool, would_run: dict[str, bool]) -> frozenset[str]:
    """Which heavy gates this pass records pending instead of running. Two conditions, both necessary. The pass must be *refreshing* — run_m1 or the surface build has real work — because that is the pass whose whole point is to get the letters on screen, and a pass with no artifact work is the one that should be spending its time on verification instead. And the gate must be one that would otherwise run live, so a gate a green record already proved stays proved rather than being demoted to pending; without that, a review-UI edit (which restamps the surface but moves nothing the heavy gates read) would throw away three greens it had no quarrel with. gate:js is never deferrable — it is one node process."""
    if not defer or not refreshing:
        return frozenset()
    return frozenset(name for name in DEFERRABLE_GATES if would_run.get(name))


def unfinished_cycle_snapshot(summary_path: Path | None = None) -> Path | None:
    """The snapshot of the last cycle that did not finish green, when it is still on disk. Such a cycle can have rewritten the live surface and then stopped, which leaves its snapshot the only copy of what the surface held beforehand — so this pass must neither take that name nor let its own retention sweep it away. A green cycle's snapshot needs no such protection: its own carry already read it, and nothing reads a snapshot twice."""
    try:
        summary = json.loads((summary_path if summary_path is not None else CYCLE_SUMMARY).read_text())
    except OSError, ValueError:
        return None
    if not isinstance(summary, dict) or summary.get("exit") == "ok":
        return None
    recorded = summary.get("snapshot_dir")
    if not isinstance(recorded, str):
        return None
    path = Path(recorded)
    return path if path.is_dir() else None


def snapshot_surface(src: Path, dst: Path) -> str:
    """Snapshot the surface as an APFS clone when possible (cp -c uses clonefile(2), sharing blocks copy-on-write, so the ≈130MB recovery copy costs neither wall time nor real disk); shutil.copytree remains the portable fallback."""
    if sys.platform == "darwin":
        result = subprocess.run(["cp", "-Rc", str(src), str(dst)], capture_output=True, text=True)
        if result.returncode == 0:
            return "cloned"
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return "copied"


@dataclass
class GateOutcome:
    ok: bool
    failures: list[str]
    unmatched: int | None
    multi_matched: int | None


def evaluate_run_m1_gate(pipeline: dict, boundary: dict, manual_pins: dict, oracle: dict) -> GateOutcome:
    """Decide whether the M1 build passed from its four summary JSONs. run_m1's own exit code is not usable — it fails on any UNMATCHED oracle rows, always present mid-migration — so this reads defect_errors, the boundary/Manual-pin passes, and multi_matched instead, and records UNMATCHED only as informational."""
    failures: list[str] = []

    defect_errors = pipeline.get("defect_errors") or []
    if defect_errors:
        failures.append(f"{len(defect_errors)} defect-gate error(s): {defect_errors[0]}")

    if not boundary.get("pass"):
        failures.append(f"boundary-equals-text-edge gate failed ({boundary.get('divergences')} divergences)")

    if not manual_pins.get("pass"):
        failures.append(
            f"Manual-pin gate failed ({len(manual_pins.get('disagreements') or [])} disagreements)"
        )

    multi_matched = oracle.get("multi_matched")
    if multi_matched is not None and multi_matched > 0:
        failures.append(f"oracle multi_matched = {multi_matched} (must be 0)")

    return GateOutcome(
        ok=not failures,
        failures=failures,
        unmatched=oracle.get("unmatched"),
        multi_matched=multi_matched,
    )


def evaluate_conform_gate(summary: dict | None) -> tuple[str, list[str]]:
    """Judge gate:conform from conform_summary.json's contents (None = the subprocess never wrote one). `pass` is the verdict; the detail lines name what broke — shaping divergences are compiler defects by definition, and nonzero uncovered counts mean dead generated rules or transitions."""
    if summary is None:
        return "FAILED (no conform_summary.json)", ["conform gate: run_m1 --conform-only wrote no summary"]
    failures: list[str] = []
    if summary.get("divergences"):
        failures.append(f"conform gate: {summary['divergences']} font-vs-settle divergence(s)")
    if summary.get("uncovered_rules"):
        failures.append(f"conform gate: {summary['uncovered_rules']} dead settlement rule(s)")
    if summary.get("uncovered_transitions"):
        failures.append(f"conform gate: {summary['uncovered_transitions']} dead decision-table transition(s)")
    if not summary.get("pass") and not failures:
        failures.append("conform gate: pass is false")
    if failures:
        return "FAILED", failures
    return "green", []


def conform_gate_argv(jobs: int, horizon: int = CONFORM_HORIZON_DEFAULT) -> list[str]:
    argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--conform-only"]
    if jobs > 1:
        argv += ["--jobs", str(jobs)]
    if horizon != CONFORM_HORIZON_DEFAULT:
        argv += ["--conform-horizon", str(horizon)]
    return argv


def evaluate_kernel_differential_gate(summary: dict | None) -> tuple[str, list[str]]:
    """Judge gate:kernel-differential from kernel_differential_summary.json's contents (None = the subprocess never wrote one). Four ways to be red, and they are different failures: `error` is the run itself falling over (no cargo, a build failure, a kernel that exited nonzero), `stale` is rebuild/out/m1 not describing the inputs the gate just enumerated — nothing was compared, and the remedy is a cycle rather than a fix — `divergences` is the count the tool reports, and the per-config arms are what name the artifact that moved. An empty `configs` map is red too: a summary that compared nothing has proved nothing."""
    if summary is None:
        return (
            "FAILED (no kernel_differential_summary.json)",
            ["kernel-differential gate: rebuild.tools.kernel_gate wrote no summary"],
        )
    failures: list[str] = []
    error = summary.get("error")
    if error:
        failures.append(f"kernel-differential gate: {error}")
    stale = summary.get("stale") or []
    if stale:
        failures.append(
            f"kernel-differential gate: {len(stale)} stale artifact(s) under rebuild/out/m1 ({stale[0]}) — run the artifact cycle"
        )
    if summary.get("divergences"):
        failures.append(f"kernel-differential gate: {summary['divergences']} Rust-vs-Python divergence(s)")
    configs = summary.get("configs")
    if not isinstance(configs, dict) or not configs:
        failures.append("kernel-differential gate: the summary compared no configs")
    else:
        for config in sorted(configs):
            arms = configs[config]
            if not isinstance(arms, dict):
                failures.append(f"kernel-differential gate: {config} reported no arms")
                continue
            differing = sorted(name for name, verdict in arms.items() if verdict != "identical")
            if differing:
                failures.append(f"kernel-differential gate: {config} differs on {', '.join(differing)}")
    if failures:
        return "FAILED", failures
    return "green", []


def kernel_differential_argv(threads: int = KERNEL_THREADS_DEFAULT) -> list[str]:
    return ["uv", "run", "python", "-m", "rebuild.tools.kernel_gate", "--threads", str(threads)]


def evaluate_kernel_harness_gate(summary: dict | None) -> tuple[str, list[str]]:
    """Judge gate:kernel-harness from kernel_harness_summary.json's contents (None = the subprocess never wrote one). `error` is the run itself falling over (no cargo, a build failure, a harness that could not start). The arms are the verdict proper: the tool stops at the first failing one, so an arm missing from the map is one the run never reached, and it is named as such rather than passed over — five arms present and exiting zero is the only shape that proves the landing evidence still holds. An empty `arms` map is red for the same reason an empty `configs` map is: a summary that ran nothing has proved nothing."""
    from rebuild.tools.kernel_harness_gate import ARM_NAMES

    if summary is None:
        return (
            "FAILED (no kernel_harness_summary.json)",
            ["kernel-harness gate: rebuild.tools.kernel_harness_gate wrote no summary"],
        )
    failures: list[str] = []
    error = summary.get("error")
    if error:
        failures.append(f"kernel-harness gate: {error}")
    arms = summary.get("arms")
    if not isinstance(arms, dict) or not arms:
        failures.append("kernel-harness gate: the summary ran no arms")
    else:
        for name in ARM_NAMES:
            arm = arms.get(name)
            if not isinstance(arm, dict):
                failures.append(f"kernel-harness gate: {name} never ran (an earlier arm stopped the run)")
                continue
            exit_code = arm.get("exit")
            if exit_code != 0:
                tail = arm.get("tail")
                lines = [line for line in tail if line.strip()] if isinstance(tail, list) else []
                verdict_line = lines[-1] if lines else "no output"
                failures.append(f"kernel-harness gate: {name} exited {exit_code}: {verdict_line}")
    if failures:
        return "FAILED", failures
    return "green", []


def kernel_harness_argv() -> list[str]:
    return ["uv", "run", "python", "-m", "rebuild.tools.kernel_harness_gate"]


def classify_rebuild_failure(test_id: str, update_pins: bool) -> str:
    """Bucket a failing rebuild-suite test id: 'baseline' (the documented always-expected failures in BASELINE_REBUILD_FAILURES), 'census-hint' (a census-pinned review test, expected to go stale after a rune change until --update-pins), or 'hard' (anything unexplained — fails the cycle)."""
    if test_id in BASELINE_REBUILD_FAILURES:
        return "baseline"
    module = test_id.split("::", 1)[0]
    stem = Path(module).stem
    if stem in CENSUS_HINT_MODULES and not update_pins:
        return "census-hint"
    return "hard"


@dataclass
class Step:
    name: str
    argv: list[str] | None
    note: str = ""
    lane: str = ""


@dataclass
class Plan:
    short_id: str
    first_run: bool
    snapshot_dir: Path
    carry_out: Path | None
    verdicts: Path | None
    update_pins: bool
    skip_gates: bool
    do_merge: bool = False
    skip_conform: bool = False
    skip_make_test: bool = False
    make_test_note: str = ""
    make_test_fingerprint: str | None = None
    skip_run_m1: bool = False
    run_m1_note: str = ""
    run_m1_fingerprint: str | None = None
    fresh: bool = False
    skip_surface: bool = False
    surface_note: str = ""
    skip_rebuild_gate: bool = False
    rebuild_gate_note: str = ""
    conform_note: str = ""
    conform_proven: bool = False
    skip_kernel_differential: bool = False
    kernel_differential_note: str = ""
    kernel_differential_proven: bool = False
    skip_kernel_harness: bool = False
    kernel_harness_note: str = ""
    kernel_harness_proven: bool = False
    skip_census: bool = False
    census_skip_note: str = ""
    census_replay: dict | None = None
    defer_census: bool = False
    skip_plumbing: bool = False
    plumbing_note: str = ""
    plumbing_carry_out: Path | None = None
    defer_rebuild_on_stale_census: bool = True
    deferred: frozenset[str] = frozenset()
    preserve_snapshot: Path | None = None
    record_greens: bool = False
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT
    job_budget: int = 1
    conform_jobs: int = 1
    conform_horizon: int = CONFORM_HORIZON_DEFAULT
    kernel_threads: int = KERNEL_THREADS_DEFAULT
    review_out: Path | None = None
    census_surface: Path = REVIEW_OUT
    complaints_note: str = ""
    retention: bool = False
    steps: list[Step] = field(default_factory=list)


def stale_census_known(plan: Plan) -> bool:
    """Whether the pass already knows, before running anything, that the census outcome is stale: a recorded stale result whose key matched, i.e. the replay path. A live --check discovers staleness only mid-cycle, so the plan can promise the deferral only here; _run_cycle applies the same policy to a live STALE at submission time."""
    return (
        plan.defer_rebuild_on_stale_census
        and not plan.update_pins
        and plan.skip_census
        and plan.census_replay is not None
        and plan.census_replay.get("status") == "stale"
    )


def jstest_argv() -> list[str]:
    """The JS suite argv. The *.test.js glob form is required — node v26 rejects the bare-directory form with 'Cannot find module' — and the glob is expanded in Python, never handed to a shell."""
    files = sorted(str(path.relative_to(ROOT)) for path in JSTEST_DIR.glob("*.test.js"))
    return ["node", "--test", *files]


def stage_job_budget(*, skip_gates: bool, skip_make_test: bool = False, ncores: int | None = None) -> int:
    """The --jobs budget the driver hands run_m1 and surface-build. Under a gated cycle `make test`'s full-width pytest pool runs from t=0, so the build stages take half the box rather than all of it — but not one core: everything downstream (the surface, the carry, and both queued heavy gates) waits on run_m1, so serializing the build chain put it on the critical path of every gated cycle. Half-width bounds the oversubscription at 3:2 against whichever full-width pytest pool is hot — make-test from t=0, then under the queue policy at most one heavy gate pool behind it; the shape the queue policy exists to avoid is the 2:1 of two full-width pools. The cores open up entirely whenever make-test isn't actually going to run: --skip-gates, the closure-unchanged auto-skip, or deferral. gate:js still runs from t=0 in every case, but it's a single node process, not a pool."""
    n = ncores or (os.cpu_count() or 1)
    return n if skip_gates or skip_make_test else max(1, n // 2)


def build_plan(
    *,
    verdicts: Path | None,
    no_carry: bool,
    carry_out: Path | None,
    snapshot_dir: Path | None,
    update_pins: bool,
    skip_gates: bool,
    first_run: bool,
    short_id: str,
    no_merge: bool = False,
    skip_conform: bool = False,
    skip_make_test: bool = False,
    make_test_note: str = "",
    make_test_fingerprint: str | None = None,
    conform_horizon: int = CONFORM_HORIZON_DEFAULT,
    kernel_threads: int = KERNEL_THREADS_DEFAULT,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    review_out: Path | None = None,
    ncores: int | None = None,
    skip_run_m1: bool = False,
    run_m1_note: str = "",
    run_m1_fingerprint: str | None = None,
    fresh: bool = False,
    skip_surface: bool = False,
    surface_note: str = "",
    skip_rebuild_gate: bool = False,
    rebuild_gate_note: str = "",
    conform_note: str = "",
    conform_proven: bool = False,
    skip_kernel_differential: bool = False,
    kernel_differential_note: str = "",
    kernel_differential_proven: bool = False,
    skip_kernel_harness: bool = False,
    kernel_harness_note: str = "",
    kernel_harness_proven: bool = False,
    skip_census: bool = False,
    census_skip_note: str = "",
    census_replay: dict | None = None,
    defer_census: bool = False,
    skip_plumbing: bool = False,
    plumbing_note: str = "",
    plumbing_carry_out: Path | None = None,
    defer_rebuild_on_stale_census: bool = True,
    deferred: frozenset[str] = frozenset(),
    preserve_snapshot: Path | None = None,
    record_greens: bool = False,
    keep_history: bool = False,
) -> Plan:
    resolved_snapshot = (
        snapshot_dir if snapshot_dir is not None else resolve_snapshot_dir(ROOT / "tmp", short_id)
    )
    do_carry = not no_carry and not first_run and not skip_plumbing
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = (
            carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"
        )

    job_budget = stage_job_budget(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test or "make-test" in deferred,
        ncores=ncores,
    )
    conform_jobs = min(_CONFORM_JOBS_CAP, ncores or (os.cpu_count() or 1))
    census_surface = review_out if review_out is not None else REVIEW_OUT
    do_merge = do_carry and not no_merge and review_out is None
    do_retention = not keep_history and not first_run and review_out is None

    plan = Plan(
        short_id=short_id,
        first_run=first_run,
        snapshot_dir=resolved_snapshot,
        carry_out=resolved_carry_out,
        verdicts=verdicts,
        update_pins=update_pins,
        skip_gates=skip_gates,
        do_merge=do_merge,
        skip_conform=skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fingerprint,
        skip_run_m1=skip_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fingerprint,
        fresh=fresh,
        skip_surface=skip_surface,
        surface_note=surface_note,
        skip_rebuild_gate=skip_rebuild_gate,
        rebuild_gate_note=rebuild_gate_note,
        conform_note=conform_note,
        conform_proven=conform_proven,
        skip_kernel_differential=skip_kernel_differential,
        kernel_differential_note=kernel_differential_note,
        kernel_differential_proven=kernel_differential_proven,
        skip_kernel_harness=skip_kernel_harness,
        kernel_harness_note=kernel_harness_note,
        kernel_harness_proven=kernel_harness_proven,
        skip_census=skip_census,
        census_skip_note=census_skip_note,
        census_replay=census_replay,
        defer_census=defer_census,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        plumbing_carry_out=plumbing_carry_out,
        defer_rebuild_on_stale_census=defer_rebuild_on_stale_census,
        deferred=deferred,
        preserve_snapshot=preserve_snapshot,
        record_greens=record_greens,
        retention=do_retention,
        pool_policy=pool_policy,
        job_budget=job_budget,
        conform_jobs=conform_jobs,
        conform_horizon=conform_horizon,
        kernel_threads=kernel_threads,
        review_out=review_out,
        census_surface=census_surface,
    )

    if first_run:
        plan.steps.append(
            Step("snapshot", None, "SKIPPED (first run: no existing surface to snapshot)", lane="build")
        )
    elif skip_plumbing:
        plan.steps.append(
            Step(
                "snapshot",
                None,
                f"SKIPPED ({plumbing_note}); no carry reads it and no surface write threatens the live copy",
                lane="build",
            )
        )
    else:
        plan.steps.append(
            Step(
                "snapshot",
                None,
                f"snapshot {REVIEW_OUT} -> {resolved_snapshot} (APFS clone when supported)",
                lane="build",
            )
        )

    if skip_run_m1:
        plan.steps.append(
            Step(
                "run_m1",
                None,
                f"SKIPPED ({run_m1_note}); gate re-evaluated from the recorded summaries",
                lane="build",
            )
        )
    else:
        run_m1_argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
        if job_budget > 1:
            run_m1_argv += ["--jobs", str(job_budget)]
        plan.steps.append(Step("run_m1", run_m1_argv, lane="build"))

    if skip_surface:
        plan.steps.append(Step("surface-build", None, f"SKIPPED ({surface_note})", lane="build"))
    else:
        surface_argv = ["uv", "run", "python", "-m", "rebuild.review.build"]
        if job_budget > 1:
            surface_argv += ["--jobs", str(job_budget)]
        if review_out is not None:
            surface_argv += ["--out", str(review_out)]
        if fresh:
            surface_argv += ["--fresh-unit-cache"]
        plan.steps.append(Step("surface-build", surface_argv, lane="build"))

    if do_carry:
        assert resolved_carry_out is not None
        carry_argv = [
            "uv",
            "run",
            "python",
            str(CARRY_TOOL),
            "--source",
            str(resolved_snapshot),
            str(verdicts),
            "--out",
            str(resolved_carry_out),
        ]
        if review_out is not None:
            carry_argv += ["--current-surface", str(review_out)]
        plan.steps.append(Step("carry", carry_argv, lane="build"))
    elif skip_plumbing:
        plan.steps.append(Step("carry", None, f"SKIPPED ({plumbing_note})", lane="build"))
    elif first_run:
        plan.steps.append(Step("carry", None, "SKIPPED (first run)", lane="build"))
    else:
        plan.steps.append(Step("carry", None, "SKIPPED (--no-carry)", lane="build"))

    if do_merge:
        assert resolved_carry_out is not None
        plan.steps.append(
            Step(
                "merge",
                ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(resolved_carry_out)],
                lane="build",
            )
        )
    elif do_carry and review_out is not None:
        plan.steps.append(
            Step("merge", None, "SKIPPED (rehearsal: the live autosave is never written)", lane="build")
        )
    elif do_carry:
        plan.steps.append(Step("merge", None, "SKIPPED (--no-merge)", lane="build"))
    elif skip_plumbing:
        plan.steps.append(Step("merge", None, f"SKIPPED ({plumbing_note})", lane="build"))
    elif first_run:
        plan.steps.append(Step("merge", None, "SKIPPED (first run)", lane="build"))
    else:
        plan.steps.append(Step("merge", None, "SKIPPED (--no-carry)", lane="build"))

    if do_merge:
        plan.steps.append(
            Step("echo-fill", ["uv", "run", "python", str(ECHO_TOOL), str(AUTOSAVE)], lane="build")
        )
        plan.steps.append(
            Step(
                "echo-merge",
                ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(ECHO_FILL)],
                lane="build",
            )
        )
        plan.steps.append(
            Step("standing-fill", ["uv", "run", "python", str(STANDING_TOOL), str(AUTOSAVE)], lane="build")
        )
        plan.steps.append(
            Step(
                "standing-merge",
                ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(STANDING_FILL)],
                lane="build",
            )
        )
    else:
        if do_carry and review_out is not None:
            echo_note = "SKIPPED (rehearsal: the live autosave is never written)"
        elif do_carry:
            echo_note = "SKIPPED (--no-merge)"
        elif skip_plumbing:
            echo_note = f"SKIPPED ({plumbing_note})"
        elif first_run:
            echo_note = "SKIPPED (first run)"
        else:
            echo_note = "SKIPPED (--no-carry)"
        plan.steps.append(Step("echo-fill", None, echo_note, lane="build"))
        plan.steps.append(Step("echo-merge", None, echo_note, lane="build"))
        plan.steps.append(Step("standing-fill", None, echo_note, lane="build"))
        plan.steps.append(Step("standing-merge", None, echo_note, lane="build"))

    if defer_census:
        plan.steps.append(Step("census", None, f"DEFERRED ({DEFER_NOTE})", lane="build"))
    elif skip_census:
        plan.steps.append(Step("census", None, f"SKIPPED ({census_skip_note})", lane="build"))
    else:
        census_mode = "--update" if update_pins else "--check"
        plan.steps.append(
            Step(
                "census",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "rebuild.review.census",
                    census_mode,
                    "--surface",
                    str(census_surface),
                ],
                (
                    "then `git diff -- rebuild/review-census-pins.json`, printed in full"
                    if update_pins
                    else "staleness reported informationally"
                ),
                lane="build",
            )
        )

    if review_out is not None:
        plan.complaints_note = "rehearsal: reads the live autosave"
    elif first_run:
        plan.complaints_note = "first run: no verdicts to cluster"
    elif skip_plumbing:
        plan.complaints_note = plumbing_note
    elif not AUTOSAVE.exists():
        plan.complaints_note = "no verdicts store"
    if plan.complaints_note:
        plan.steps.append(Step("complaints", None, f"SKIPPED ({plan.complaints_note})", lane="build"))
    else:
        plan.steps.append(
            Step(
                "complaints",
                ["uv", "run", "python", "-m", "rebuild.tools.complaint_docket", str(AUTOSAVE)],
                "informational, non-gating",
                lane="build",
            )
        )

    if skip_gates:
        plan.steps.append(Step("gates", None, "SKIPPED (--skip-gates)"))
    else:
        plan.steps.append(Step("gate:js", jstest_argv(), lane="t0"))
        if skip_conform:
            plan.steps.append(
                Step("gate:conform", None, f"SKIPPED ({conform_note or '--skip-conform'})", lane="conform")
            )
        elif "conform" in deferred:
            plan.steps.append(Step("gate:conform", None, f"DEFERRED ({DEFER_NOTE})", lane="conform"))
        else:
            plan.steps.append(
                Step("gate:conform", conform_gate_argv(conform_jobs, conform_horizon), lane="conform")
            )
        if skip_kernel_differential:
            plan.steps.append(
                Step(
                    "gate:kernel-differential",
                    None,
                    f"SKIPPED ({kernel_differential_note or '--skip-kernel-differential'})",
                    lane="kernel",
                )
            )
        elif "kernel-differential" in deferred:
            plan.steps.append(
                Step("gate:kernel-differential", None, f"DEFERRED ({DEFER_NOTE})", lane="kernel")
            )
        else:
            plan.steps.append(
                Step("gate:kernel-differential", kernel_differential_argv(kernel_threads), lane="kernel")
            )
        if skip_rebuild_gate:
            plan.steps.append(Step("gate:rebuild", None, f"SKIPPED ({rebuild_gate_note})", lane="rebuild"))
        elif "rebuild" in deferred:
            plan.steps.append(Step("gate:rebuild", None, f"DEFERRED ({DEFER_NOTE})", lane="rebuild"))
        elif stale_census_known(plan):
            plan.steps.append(
                Step("gate:rebuild", None, f"DEFERRED ({STALE_CENSUS_DEFER_NOTE})", lane="rebuild")
            )
        else:
            plan.steps.append(
                Step(
                    "gate:rebuild",
                    list(REBUILD_PYTEST_ARGV),
                    (
                        "submitted once the census step has rewritten the pins"
                        if update_pins
                        else "submitted after the census step; a STALE outcome defers it instead"
                    ),
                    lane="rebuild",
                )
            )
        if skip_kernel_harness:
            plan.steps.append(
                Step(
                    "gate:kernel-harness",
                    None,
                    f"SKIPPED ({kernel_harness_note or '--skip-kernel-harness'})",
                    lane="harness",
                )
            )
        elif "kernel-harness" in deferred:
            plan.steps.append(Step("gate:kernel-harness", None, f"DEFERRED ({DEFER_NOTE})", lane="harness"))
        else:
            plan.steps.append(
                Step(
                    "gate:kernel-harness",
                    kernel_harness_argv(),
                    "submitted with gate:rebuild, once the census step has landed; parks behind it",
                    lane="harness",
                )
            )
        if skip_make_test:
            plan.steps.append(Step("gate:make-test", None, f"SKIPPED ({make_test_note})", lane="t0"))
        elif "make-test" in deferred:
            plan.steps.append(Step("gate:make-test", None, f"DEFERRED ({DEFER_NOTE})", lane="t0"))
        else:
            plan.steps.append(Step("gate:make-test", ["make", "test"], lane="t0"))

    if do_retention:
        plan.steps.append(
            Step(
                "retention",
                None,
                f"on green finish: keep only this cycle's tmp/review-pre-* snapshot and the stamp-aligned verdicts-carried-*.json, drop verdicts-autosave-* stashes older than the journal's last base event, compact the journal to a {RETENTION_WINDOW_DAYS}-day restore floor; --keep-history skips",
            )
        )
    elif keep_history:
        plan.steps.append(Step("retention", None, "SKIPPED (--keep-history)"))
    elif first_run:
        plan.steps.append(Step("retention", None, "SKIPPED (first run: nothing accumulated yet)"))
    else:
        plan.steps.append(
            Step("retention", None, "SKIPPED (rehearsal: the live piles are not this cycle's to prune)")
        )

    return plan


def resolve_carry_source() -> dict | None:
    from rebuild.review import status

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        stamp = None
    return status.resolve_carry_source(ROOT, stamp, AUTOSAVE)


def describe_carry_source(resolved: dict, root: Path) -> str:
    try:
        shown = resolved["path"].relative_to(root)
    except ValueError:
        shown = resolved["path"]
    if resolved["aligned"]:
        return (
            f"Auto-resolved carry source: {shown} ({resolved['count']} effective verdicts, stamped for the served surface). "
            "Pass --verdicts to override."
        )
    return (
        f"ERROR: the best carry source, {shown} ({resolved['count']} effective verdicts), is stamped {resolved['stamp']}, not the served surface. "
        "Its verdicts were recorded against a surface rebuild/out/review no longer holds — review.build ran outside a cycle, or a cycle died between its surface build and its merge — and pairing them with a snapshot of the live directory would resolve their unit ids onto the wrong windows, which carry_verdicts now refuses outright. "
        "Recover first: carry the file onto the live surface from its stamp-matching tmp/review-pre-* snapshot (uv run python rebuild/tools/carry_verdicts.py --source <snapshot> <verdicts>, then rebuild.tools.merge_verdicts), or rerun with --no-carry to proceed without these verdicts, or --verdicts to name a different master."
    )


def resolve_short_id() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        head = result.stdout.strip()
        if head:
            return head
    except OSError, subprocess.SubprocessError:
        pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def server_listening(port: int = REVIEW_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def server_may_stay_up(*, skip_surface: bool, skip_plumbing: bool) -> bool:
    """Whether a live review server can run right through this pass. Two things a cycle writes are the app's own: the surface it serves — livereload watches every shard, and a restamped manifest orphans the tab's store — and the verdict store, which merge_verdicts refuses to touch under a live server anyway, since an open tab would flush its copy back over the merge. A pass whose plan skips both writes neither, so the letters can stay on screen for its whole run; that is exactly the shape of the gate pass the deferred gates exist to produce. Everything else the cycle writes is either outside the served tree (the census pins, the m1 summaries) or read by the app only as status, where landing fresh mid-pass is the point rather than a hazard."""
    return skip_surface and skip_plumbing


def stop_review_server(timeout: float = SERVER_STOP_TIMEOUT) -> bool:
    """Terminate the review server and wait for port 7294 to come free, so the surface rewrite that follows cannot race a live reader. False when something is still listening at the deadline — a server started some other way, or one wedged mid-shutdown — which the caller reports rather than building over."""
    subprocess.run(["pkill", "-f", SERVER_STOP_PATTERN], check=False, capture_output=True)
    deadline = time.monotonic() + timeout
    while server_listening():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def _render_concurrency(plan: Plan) -> list[str]:
    if plan.skip_gates:
        return [
            "",
            "  Concurrency (--skip-gates):",
            f"    Lane build only; no gates; --jobs budget: {plan.job_budget}",
        ]
    defer_rebuild = "rebuild" in plan.deferred
    defer_conform = "conform" in plan.deferred
    defer_kernel = "kernel-differential" in plan.deferred
    defer_harness = "kernel-harness" in plan.deferred
    defer_make_test = "make-test" in plan.deferred
    no_make_test = plan.skip_make_test or defer_make_test
    no_conform = plan.skip_conform or defer_conform
    no_kernel = plan.skip_kernel_differential or defer_kernel
    no_rebuild = plan.skip_rebuild_gate or defer_rebuild or stale_census_known(plan)
    t0_lane = "gate:js" if no_make_test else "gate:js, gate:make-test"
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        f"    Lane t0   [from t=0, background]  : {t0_lane}",
        "    Lane build[serial, main thread]  : snapshot -> run_m1 -> surface-build -> carry -> merge -> census -> submit gate:rebuild, gate:kernel-harness",
    ]
    if plan.skip_conform:
        lines.append("    Lane conform                     : SKIPPED (--skip-conform)")
    elif defer_conform:
        lines.append(f"    Lane conform                     : DEFERRED ({DEFER_NOTE})")
    elif plan.pool_policy == "overlap":
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; CO-RESIDENT with the pytest pools (--jobs {plan.conform_jobs})"
        )
    elif not no_make_test:
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; QUEUED behind gate:make-test (queue policy — one heavy pool at a time) (--jobs {plan.conform_jobs})"
        )
    else:
        lines.append(
            f"    Lane conform                     : starts when run_m1's four JSONs pass; gate:make-test not running, so no queueing (--jobs {plan.conform_jobs})"
        )
    if plan.skip_kernel_differential:
        lines.append(
            f"    Lane kernel                      : SKIPPED ({plan.kernel_differential_note or '--skip-kernel-differential'})"
        )
    elif defer_kernel:
        lines.append(f"    Lane kernel                      : DEFERRED ({DEFER_NOTE})")
    elif plan.pool_policy == "overlap":
        lines.append(
            f"    Lane kernel                      : starts when run_m1's four JSONs pass; CO-RESIDENT with the pytest pools (--threads {plan.kernel_threads})"
        )
    elif not no_conform:
        lines.append(
            f"    Lane kernel                      : starts when run_m1's four JSONs pass; QUEUED behind gate:conform (queue policy — one heavy pool at a time) (--threads {plan.kernel_threads})"
        )
    elif not no_make_test:
        lines.append(
            f"    Lane kernel                      : starts when run_m1's four JSONs pass; QUEUED behind gate:make-test (queue policy; gate:conform not running) (--threads {plan.kernel_threads})"
        )
    else:
        lines.append(
            f"    Lane kernel                      : starts when run_m1's four JSONs pass; no heavy pool ahead of it, so no queueing (--threads {plan.kernel_threads})"
        )
    if plan.skip_rebuild_gate:
        lines.append(
            "    Lane rebuild                     : SKIPPED (inputs unchanged since its last green run)"
        )
    elif defer_rebuild:
        lines.append(f"    Lane rebuild                     : DEFERRED ({DEFER_NOTE})")
    elif stale_census_known(plan):
        lines.append(f"    Lane rebuild                     : DEFERRED ({STALE_CENSUS_DEFER_NOTE})")
    else:
        lines.append(
            "    Lane rebuild                     : submitted after the census step lands its verdict;"
        )
        if plan.pool_policy == "overlap":
            lines.append(
                "                                       CO-RESIDENT with the other pools (overlap policy)"
            )
        elif not no_kernel:
            lines.append(
                "                                       QUEUED behind gate:kernel-differential (queue policy — one heavy pool at a time)"
            )
        elif not no_conform:
            lines.append(
                "                                       QUEUED behind gate:conform (queue policy; gate:kernel-differential not running)"
            )
        elif not no_make_test:
            lines.append(
                "                                       QUEUED behind gate:make-test (queue policy; gate:conform not running)"
            )
        else:
            lines.append("                                       no other heavy pool running, so no queueing")
    if plan.skip_kernel_harness:
        lines.append(
            f"    Lane harness                     : SKIPPED ({plan.kernel_harness_note or '--skip-kernel-harness'})"
        )
    elif defer_harness:
        lines.append(f"    Lane harness                     : DEFERRED ({DEFER_NOTE})")
    elif plan.pool_policy == "overlap":
        lines.append(
            "    Lane harness                     : submitted with gate:rebuild, after the census step; CO-RESIDENT with the other pools (overlap policy)"
        )
    elif not no_rebuild:
        lines.append(
            "    Lane harness                     : submitted with gate:rebuild, after the census step; QUEUED behind it (queue policy — the longest pole, and nothing queues behind it)"
        )
    else:
        lines.append(
            "    Lane harness                     : submitted with gate:rebuild, after the census step; gate:rebuild not running, so no queueing"
        )
    if plan.skip_make_test:
        budget_reason = "gate:make-test skipped, so the build stages fan out"
    elif defer_make_test:
        budget_reason = "gate:make-test deferred, so the build stages fan out"
    else:
        budget_reason = "half the cores, sharing the box with gate:make-test's full-width pytest pool"
    lines.append(f"    build-stage --jobs budget        : {plan.job_budget}  ({budget_reason})")
    pending = ["gate:" + name for name in sorted(plan.deferred)] + (["census"] if plan.defer_census else [])
    if pending:
        lines.append(f"    deferred to the next pass        : {', '.join(pending)}")
    return lines


def render_plan(plan: Plan) -> str:
    lines = ["Artifact-cycle plan (resolved, nothing executed):", ""]
    lines.append(f"  git short id : {plan.short_id}")
    lines.append(f"  first run    : {plan.first_run}")
    lines.append(f"  snapshot dir : {plan.snapshot_dir}")
    lines.append(f"  verdicts     : {plan.verdicts if plan.verdicts is not None else '(none)'}")
    lines.append(f"  carry output : {plan.carry_out if plan.carry_out is not None else '(no carry)'}")
    if plan.review_out is not None:
        lines.append(
            f"  rehearsal    : surface writes redirected to {plan.review_out}; the live surface at rebuild/out/review is never written."
        )
    lines.append("")
    lines.append("  Steps:")
    for index, step in enumerate(plan.steps, start=1):
        if step.argv is not None:
            lines.append(f"    {index}. {step.name}: {' '.join(step.argv)}")
            if step.note:
                lines.append(f"       ({step.note})")
        else:
            lines.append(f"    {index}. {step.name}: {step.note}")
    lines.extend(_render_concurrency(plan))
    return "\n".join(lines)


@dataclass
class CycleReport:
    snapshot_dir: Path | None = None
    unmatched: int | None = None
    multi_matched: int | None = None
    boundary_pass: bool | None = None
    pins_pass: bool | None = None
    surface_units: int | None = None
    surface_rows: int | None = None
    surface_batches: int | None = None
    echo_groups: int | None = None
    carry_out: Path | None = None
    carry_lines: list[str] = field(default_factory=list)
    merge_status: str = "not run"
    merge_lines: list[str] = field(default_factory=list)
    echo_fill_status: str = "not run"
    echo_fill_lines: list[str] = field(default_factory=list)
    echo_merge_status: str = "not run"
    echo_merge_lines: list[str] = field(default_factory=list)
    standing_fill_status: str = "not run"
    standing_fill_lines: list[str] = field(default_factory=list)
    standing_merge_status: str = "not run"
    standing_merge_lines: list[str] = field(default_factory=list)
    census_status: str = "not run"
    complaints_status: str = "not run"
    gate_js: str = "not run"
    gate_rebuild: str = "not run"
    gate_conform: str = "not run"
    gate_kernel_differential: str = "not run"
    gate_kernel_harness: str = "not run"
    gate_make_test: str = "not run"
    rebuild_recordable: bool = False
    rebuild_stale_deferred: bool = False
    interrupted: bool = False


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text())


class _Emitter:
    """Whole-line-atomic, lock-serialized stdout. Every write in the concurrent region routes through here so overlapping children never splice mid-line; cross-line interleave is expected and disambiguated by the [name] prefix."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def emit(self, text: str) -> None:
        with self._lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def emit_block(self, lines: list[str]) -> None:
        with self._lock:
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()


class _ChildRegistry:
    """Thread-safe set of live subprocesses, so a KeyboardInterrupt can reap every child (no orphaned pytest army survives)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: set[subprocess.Popen] = set()
        self._closed = False
        self.killed_count = 0

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def add(self, proc: subprocess.Popen) -> bool:
        """Track a live child. Returns False once terminate_all has torn the registry down, so a worker that unblocks after a KeyboardInterrupt (the queue-mode gate tasks parked on an earlier gate's future — rebuild on make-test, conform on rebuild — are the case) never leaves a fresh subprocess untracked — the caller reaps it instead of spawning an orphaned pytest army."""
        with self._lock:
            if self._closed:
                return False
            self._children.add(proc)
            return True

    def remove(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._children.discard(proc)

    def terminate_all(self) -> None:
        with self._lock:
            self._closed = True
            children = list(self._children)
            self._children.clear()
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            self.killed_count += 1


@dataclass
class _StepResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    elapsed: float


def _terminate_child(proc: subprocess.Popen) -> None:
    """Terminate one child promptly (SIGTERM, 3s grace, then SIGKILL) and drain its pipes. Used only for the narrow race where the registry is torn down between a Popen and its registry.add."""
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def _run_step(
    name: str, argv: list[str], *, emit: _Emitter, registry: _ChildRegistry, stream: bool
) -> _StepResult:
    if registry.closed:
        return _StepResult(name, 130, "", "", 0.0)
    emit.emit(f"\n$ {' '.join(argv)}")
    start = time.perf_counter()
    proc = subprocess.Popen(
        argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1
    )
    if not registry.add(proc):
        _terminate_child(proc)
        return _StepResult(name, 130, "", "", 0.0)
    out_buf: list[str] = []
    err_buf: list[str] = []

    def pump(pipe, buf: list[str]) -> None:
        for line in pipe:
            line = line.rstrip("\r\n")
            buf.append(line)
            if stream:
                emit.emit(f"[{name}] {line}")
        pipe.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, out_buf)),
        threading.Thread(target=pump, args=(proc.stderr, err_buf)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    returncode = proc.wait()
    registry.remove(proc)
    elapsed = time.perf_counter() - start
    emit.emit(f"[t] {name} {elapsed:.1f}s")
    return _StepResult(name, returncode, "\n".join(out_buf), "\n".join(err_buf), elapsed)


def _dump_captured(emit: _Emitter, result: _StepResult) -> None:
    lines: list[str] = []
    if result.stdout:
        lines.extend(result.stdout.splitlines())
    if result.stderr:
        lines.extend(result.stderr.splitlines())
    if lines:
        emit.emit_block(lines)


def _parse_surface_build(stderr: str) -> tuple[int, int, int] | None:
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("Wrote ") and "units," in stripped and "batches)" in stripped:
            inner = stripped[stripped.index("(") + 1 : stripped.rindex(")")]
            numbers = []
            for chunk in inner.split(","):
                token = chunk.strip().split(" ", 1)[0]
                numbers.append(int(token))
            if len(numbers) == 3:
                return numbers[0], numbers[1], numbers[2]
    return None


@dataclass
class RebuildOutcome:
    status: str
    failures: list[str]
    hard_ids: list[str]
    recordable: bool = False
    baseline_ids: list[str] = field(default_factory=list)


_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_STALE_PINS_NOTE = "stale census pins? (re-run with --update-pins)"


def _rebuild_verdict(
    baseline: list[str], census: list[str], hard: list[str], census_note: str
) -> RebuildOutcome:
    failures: list[str] = []
    if hard:
        status = f"FAILED ({len(hard)} unexplained)"
        failures.append(f"rebuild suite: {len(hard)} unexplained failure(s)")
    else:
        parts = []
        if baseline:
            parts.append(f"{len(baseline)} documented baseline")
        if census:
            parts.append(f"{len(census)} {census_note}")
        status = "green" if not parts else "green (" + ", ".join(parts) + ")"
    return RebuildOutcome(
        status=status,
        failures=failures,
        hard_ids=list(hard),
        recordable=not hard and not census,
        baseline_ids=list(baseline),
    )


def classify_rebuild_output(stdout: str, returncode: int, update_pins: bool) -> RebuildOutcome:
    """Bucket the rebuild suite's FAILED/ERROR summary lines into baseline / census-hint / hard and turn them into a gate verdict — the one judgment of the suite's output, shared by the cycle's gate:rebuild and the interactive wrapper (rebuild.tools.rebuild_gate). pytest emits ANSI color whenever FORCE_COLOR is set (as it is under the agent harness), wrapping each summary line in escape codes, so strip those first — otherwise no line begins with a literal "FAILED "/"ERROR ", the documented baseline can't be subtracted, and every colored run reads as an unexplained hard failure. On an --update-pins run the census-pinned ids stay hard: the cycle submits the suite only after the census step has rewritten the pins, so a census-module failure is judged against the pins the suite actually read."""
    lines = [_ANSI_SGR.sub("", line) for line in stdout.splitlines()]
    failed_ids = [line.split(None, 2)[1] for line in lines if line.startswith("FAILED ")]
    error_ids = [line.split(None, 2)[1] for line in lines if line.startswith("ERROR ")]
    buckets: dict[str, list[str]] = {"baseline": [], "census-hint": [], "hard": []}
    for test_id in failed_ids:
        buckets[classify_rebuild_failure(test_id, update_pins)].append(test_id)
    buckets["hard"].extend(error_ids)
    if returncode != 0 and not failed_ids and not error_ids:
        buckets["hard"].append(f"pytest exited {returncode} with no parsed FAILED/ERROR lines")
    return _rebuild_verdict(buckets["baseline"], buckets["census-hint"], buckets["hard"], _STALE_PINS_NOTE)


def _do_run_m1(
    report: CycleReport,
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    budget: int,
    skip: bool = False,
    skip_note: str = "",
    record: bool = False,
    fingerprint: str | None = None,
    fresh_memo: bool = False,
) -> GateOutcome | None:
    """Run (or, when `skip` is set, reuse) the M1 build and judge its gate from the four summary JSONs. The skip path leaves rebuild/out/m1 untouched and re-evaluates the recorded summaries, which is sound because run_m1's outputs are deterministic and timestamp-free over the fingerprinted inputs. A live green records the fingerprint only if it still matches — an input edited mid-run means the tested content is no longer on disk — and a live red matching the record deletes it. `fresh_memo` (a --fresh pass) makes the build distrust its persisted trace memo and re-trace every window."""
    if skip:
        emit.emit(f"\nrun_m1: SKIPPED — {skip_note}; evaluating the gate from the recorded summaries.")
    else:
        for path in M1_SUMMARY_FILES.values():
            path.unlink(missing_ok=True)
        argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
        if budget > 1:
            argv += ["--jobs", str(budget)]
        if fresh_memo:
            argv.append("--fresh-trace-memo")
        spawn("run_m1", argv, emit=emit, registry=registry, stream=True)
    missing = [name for name, path in M1_SUMMARY_FILES.items() if not path.exists()]
    if missing:
        for name in missing:
            emit.emit(
                f"run_m1 gate failure: missing {name} summary ({M1_SUMMARY_FILES[name]}) — run_m1 did not complete"
            )
        return None
    summaries = {name: _load_summary(path) for name, path in M1_SUMMARY_FILES.items()}
    gate = evaluate_run_m1_gate(
        summaries["pipeline"], summaries["boundary"], summaries["manual_pins"], summaries["oracle"]
    )
    report.unmatched = gate.unmatched
    report.multi_matched = gate.multi_matched
    report.boundary_pass = bool(summaries["boundary"].get("pass"))
    report.pins_pass = bool(summaries["manual_pins"].get("pass"))
    if record and fingerprint is not None:
        if not gate.ok:
            clear_contradicted_green(RUN_M1_GREEN, fingerprint)
        elif not skip:
            if run_m1_skip_fingerprint(ROOT) == fingerprint:
                record_green(RUN_M1_GREEN, fingerprint, files=run_m1_skip_files(ROOT))
            else:
                emit.emit("run_m1 green, but its inputs changed while it ran — green not recorded")
    return gate


def _run_m1_reasons(gate: GateOutcome | None) -> list[str]:
    if gate is None:
        return ["run_m1 did not write all four summary files"]
    return list(gate.failures)


def _do_surface_build(
    report: CycleReport,
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    review_out: Path | None,
    budget: int,
    skip: bool = False,
    skip_note: str = "",
    fresh: bool = False,
) -> bool:
    if skip:
        surface_dir = review_out if review_out is not None else REVIEW_OUT
        try:
            manifest = json.loads((surface_dir / "manifest.json").read_text())
        except OSError, ValueError:
            emit.emit("ERROR: surface-build skip: the manifest vanished mid-cycle; rerun with --fresh.")
            return False
        totals = manifest.get("totals") or {}
        report.surface_units = totals.get("units")
        report.surface_rows = totals.get("rows")
        report.surface_batches = totals.get("batches")
        report.echo_groups = totals.get("echo_groups")
        emit.emit(f"\nsurface-build: SKIPPED — {skip_note}.")
        return True
    argv = ["uv", "run", "python", "-m", "rebuild.review.build"]
    if budget > 1:
        argv += ["--jobs", str(budget)]
    if review_out is not None:
        argv += ["--out", str(review_out)]
    if fresh:
        argv += ["--fresh-unit-cache"]
    result = spawn("surface-build", argv, emit=emit, registry=registry, stream=True)
    parsed = _parse_surface_build(result.stderr) if result.returncode == 0 else None
    if result.returncode != 0 or parsed is None:
        emit.emit(
            "ERROR: review.build did not complete cleanly (no 'Wrote ... (N units, R rows, B batches)' line)."
        )
        return False
    report.surface_units, report.surface_rows, report.surface_batches = parsed
    surface_dir = review_out if review_out is not None else REVIEW_OUT
    manifest = json.loads((surface_dir / "manifest.json").read_text())
    report.echo_groups = manifest.get("totals", {}).get("echo_groups")
    return True


def _do_carry(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = [
        "uv",
        "run",
        "python",
        str(CARRY_TOOL),
        "--source",
        str(plan.snapshot_dir),
        str(plan.verdicts),
        "--out",
        str(plan.carry_out),
    ]
    if plan.review_out is not None:
        argv += ["--current-surface", str(plan.review_out)]
    result = spawn("carry", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    report.carry_out = plan.carry_out
    for line in result.stdout.splitlines():
        if any(word in line for word in ("carried", "kinds", "queue", "fallback")):
            report.carry_lines.append(line.strip())
    return result.returncode == 0


def _do_merge(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(plan.carry_out)]
    result = spawn("merge", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("merged ", "nothing changed", "stashed ")):
            report.merge_lines.append(stripped)
    report.merge_status = "merged" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_echo_fill(
    report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan
) -> bool:
    argv = ["uv", "run", "python", str(ECHO_TOOL), str(AUTOSAVE)]
    result = spawn("echo-fill", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("wrote ") and "echo-fill verdicts" in stripped:
            report.echo_fill_lines.append(stripped)
    report.echo_fill_status = "filled" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_echo_merge(
    report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan
) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(ECHO_FILL)]
    result = spawn("echo-merge", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("merged ", "nothing changed", "stashed ")):
            report.echo_merge_lines.append(stripped)
    report.echo_merge_status = "merged" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_standing_fill(
    report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan
) -> bool:
    argv = ["uv", "run", "python", str(STANDING_TOOL), str(AUTOSAVE)]
    result = spawn("standing-fill", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("wrote ") and "standing-approval verdicts" in stripped:
            report.standing_fill_lines.append(stripped)
        elif stripped.endswith("held for review by except_left"):
            report.standing_fill_lines.append(stripped)
    report.standing_fill_status = "filled" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    return result.returncode == 0


def _do_standing_merge(
    report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan
) -> bool:
    argv = ["uv", "run", "python", "-m", "rebuild.tools.merge_verdicts", str(STANDING_FILL)]
    result = spawn("standing-merge", argv, emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith(("merged ", "nothing changed", "stashed ")):
            report.standing_merge_lines.append(stripped)
    report.standing_merge_status = (
        "merged" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    )
    return result.returncode == 0


_CENSUS_STALE_HEADER = "census pins are stale:"


def census_mismatch_lines(stderr: str) -> list[str]:
    """The per-key mismatch lines of a stale census check, parsed back out of its stderr: the indented block under the "census pins are stale:" header. Empty when the header is absent — a crash or a missing pins file, outcomes with no replayable verdict."""
    lines = stderr.splitlines()
    if _CENSUS_STALE_HEADER not in lines:
        return []
    out: list[str] = []
    for line in lines[lines.index(_CENSUS_STALE_HEADER) + 1 :]:
        if not line.startswith("  "):
            break
        out.append(line.strip())
    return out


def _do_census(
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    update_pins: bool,
    surface: Path,
    record: bool = False,
) -> str:
    """Check (or re-baseline) the census pins, and record the outcome in census-result.json so an unchanged check never re-runs: a clean check records status clean under the key it checked, a stale check records status stale with its mismatch lines (staleness is deterministic over the fingerprinted inputs and non-gating, so it is as replayable as a clean result — and it is the steady state between a rune edit and the next --update-pins), --update records clean over the pins it just wrote (they are current by construction), and a check with no verdict to record — a crash, a missing pins file, an unparseable report — records nothing and deletes a record its key contradicts. The key is computed before a --check spawn (the check mutates nothing) but after an --update (which rewrites the pins the key hashes). This step finishes before gate:rebuild is submitted: on an --update-pins pass the suite therefore always reads the pins just rewritten here, and on a --check pass a STALE verdict defers that gate instead of letting it run against pins it could only re-report as stale."""
    if update_pins:
        census = spawn(
            "census",
            ["uv", "run", "python", "-m", "rebuild.review.census", "--update", "--surface", str(surface)],
            emit=emit,
            registry=registry,
            stream=False,
        )
        _dump_captured(emit, census)
        diff = spawn(
            "git-diff",
            ["git", "diff", "--", "rebuild/review-census-pins.json"],
            emit=emit,
            registry=registry,
            stream=False,
        )
        _dump_captured(emit, diff)
        if census.returncode != 0:
            return "update FAILED"
        if record:
            key = census_skip_fingerprint(ROOT, surface)
            if key is not None:
                record_census_result(CENSUS_RESULT, key, "clean", [])
        if diff.stdout.strip():
            return "updated (diff shown above — review every moved number)"
        return "updated (no change)"
    key = census_skip_fingerprint(ROOT, surface) if record else None
    census = spawn(
        "census",
        ["uv", "run", "python", "-m", "rebuild.review.census", "--check", "--surface", str(surface)],
        emit=emit,
        registry=registry,
        stream=False,
    )
    _dump_captured(emit, census)
    if census.returncode == 0:
        if key is not None:
            record_census_result(CENSUS_RESULT, key, "clean", [])
        return "clean"
    mismatches = census_mismatch_lines(census.stderr)
    if key is not None and mismatches:
        record_census_result(CENSUS_RESULT, key, "stale", mismatches)
    else:
        clear_contradicted_green(CENSUS_RESULT, key)
    return "STALE (informational — re-run with --update-pins or edit by hand)"


def _replay_census(plan: Plan, emit: _Emitter) -> str:
    """The census step's skip path. A recorded clean outcome reads as an ordinary skip; a recorded stale outcome replays its mismatch lines and keeps the STALE status in the summary, so every pass shows what is stale without re-paying the check."""
    replay = plan.census_replay
    if replay is None or replay["status"] == "clean":
        return f"skipped ({plan.census_skip_note})"
    emit.emit("census pins are stale (recorded outcome replayed; the check's inputs have not changed):")
    for line in replay["mismatches"]:
        emit.emit(f"  {line}")
    return "STALE (recorded outcome replayed — informational; re-run with --update-pins or edit by hand)"


def _skip_plumbing(report: CycleReport, plan: Plan, emit: _Emitter) -> None:
    """The verdict plumbing's skip path. Nothing ran, so the summary says so for every step of the chain — and the carried file the recorded pass wrote is still the stamp-aligned frontier (the surface it was carried onto has not moved), so the report keeps naming it rather than reading as a pass with no carry at all."""
    emit.emit(f"\nverdict plumbing: SKIPPED — {plan.plumbing_note}.")
    note = f"skipped ({plan.plumbing_note})"
    report.carry_out = plan.plumbing_carry_out
    report.merge_status = note
    report.echo_fill_status = note
    report.echo_merge_status = note
    report.standing_fill_status = note
    report.standing_merge_status = note


def _do_complaints(*, spawn, emit: _Emitter, registry: _ChildRegistry) -> str:
    result = spawn(
        "complaints",
        ["uv", "run", "python", "-m", "rebuild.tools.complaint_docket", str(AUTOSAVE)],
        emit=emit,
        registry=registry,
        stream=False,
    )
    _dump_captured(emit, result)
    if result.returncode != 0:
        return f"FAILED (exit {result.returncode}) — informational"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped == "no open complaints":
            return stripped
        if stripped.startswith("wrote ") and ": " in stripped:
            return stripped.split(": ", 1)[1]
    return "done"


def _gate_js_task(spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:js", jstest_argv(), emit=emit, registry=registry, stream=False)


def _gate_make_test_task(spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:make-test", ["make", "test"], emit=emit, registry=registry, stream=True)


def _gate_conform_task(
    pool_policy: str,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> tuple[str, list[str]]:
    """gate:conform shapes the exhaustive font-vs-settle sweep against the fresh M1.otf via run_m1 --conform-only. Under the queue policy it queues behind gate:make-test, and gate:rebuild in turn parks behind this sweep, so only one heavy pool is ever hot: co-resident, two heavy pools oversubscribe the box roughly 2:1, and measured that contention roughly tripled gate:rebuild's wall time — a worse critical path than the same work in sequence. Conform runs ahead of rebuild in the chain because rebuild's submission waits on the build lane's census step, and the sweep is long enough to hide that wait entirely. The stale conform_summary.json is unlinked here, just before the sweep spawns, so the verdict can only come from this cycle's subprocess (an auto-skipped gate never runs this task and never reads the file)."""
    CONFORM_SUMMARY.unlink(missing_ok=True)
    if pool_policy == "queue" and make_fut is not None:
        try:
            make_fut.result()
        except Exception:
            pass
    result = spawn("gate:conform", argv, emit=emit, registry=registry, stream=False)
    summary = None
    if CONFORM_SUMMARY.exists():
        try:
            summary = json.loads(CONFORM_SUMMARY.read_text())
        except ValueError:
            summary = None
    status, failures = evaluate_conform_gate(summary)
    if result.returncode != 0 and not failures:
        status = f"FAILED (exit {result.returncode})"
        failures = [f"conform gate: exited {result.returncode} despite a passing summary"]
    return status, failures


def _gate_kernel_differential_task(
    pool_policy: str,
    conform_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> tuple[str, list[str]]:
    """gate:kernel-differential re-runs this cycle's own table build through the Rust kernel and byte-compares the result against the artifacts run_m1 just wrote. It is submitted where gate:conform is — the artifacts it reads are final once the run_m1 gate passes — and under the queue policy it parks behind gate:make-test and then gate:conform, so the heavy chain stays make-test -> conform -> kernel-differential -> rebuild and only one pool is ever hot. The stale kernel_differential_summary.json is unlinked here, just before the child spawns, so the verdict can only come from this cycle's run (an auto-skipped gate never runs this task and never reads the file)."""
    KERNEL_DIFFERENTIAL_SUMMARY.unlink(missing_ok=True)
    if pool_policy == "queue":
        for fut in (make_fut, conform_fut):
            if fut is not None:
                try:
                    fut.result()
                except Exception:
                    pass
    result = spawn("gate:kernel-differential", argv, emit=emit, registry=registry, stream=False)
    summary = None
    if KERNEL_DIFFERENTIAL_SUMMARY.exists():
        try:
            summary = json.loads(KERNEL_DIFFERENTIAL_SUMMARY.read_text())
        except ValueError:
            summary = None
    status, failures = evaluate_kernel_differential_gate(summary)
    if result.returncode != 0 and not failures:
        status = f"FAILED (exit {result.returncode})"
        failures = [f"kernel-differential gate: exited {result.returncode} despite a passing summary"]
    return status, failures


def _gate_kernel_harness_task(
    pool_policy: str,
    rebuild_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> tuple[str, list[str]]:
    """gate:kernel-harness re-runs the three landing harnesses behind the Rust port — the exhaustive liveness sweep, the three fixpoint worlds, and the differential — whenever the alphabet's structure or either engine's kernel sources move. It is the deep counterpart to gate:kernel-differential, which stands on every cycle over the ink edits this one is blind to, and at roughly an hour it is the cycle's longest pole: it is submitted with gate:rebuild, once the census step has landed, and under the queue policy it parks at the tail of the whole heavy chain, since parking on gate:rebuild transitively waits out make-test and conform too. Nothing parks on it. The stale kernel_harness_summary.json is unlinked here, just before the child spawns, so the verdict can only come from this cycle's run (an auto-skipped gate never runs this task and never reads the file)."""
    KERNEL_HARNESS_SUMMARY.unlink(missing_ok=True)
    if pool_policy == "queue" and rebuild_fut is not None:
        try:
            rebuild_fut.result()
        except Exception:
            pass
    result = spawn("gate:kernel-harness", argv, emit=emit, registry=registry, stream=False)
    summary = None
    if KERNEL_HARNESS_SUMMARY.exists():
        try:
            summary = json.loads(KERNEL_HARNESS_SUMMARY.read_text())
        except ValueError:
            summary = None
    status, failures = evaluate_kernel_harness_gate(summary)
    if result.returncode != 0 and not failures:
        status = f"FAILED (exit {result.returncode})"
        failures = [f"kernel-harness gate: exited {result.returncode} despite a passing summary"]
    return status, failures


def _gate_rebuild_task(
    pool_policy: str,
    kernel_fut: Future | None,
    conform_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    update_pins: bool,
) -> RebuildOutcome:
    """The rebuild pytest suite, submitted by the build lane only after the census step lands its verdict: an --update-pins pass therefore always runs it against the freshly rewritten pins, and a STALE verdict on a --check pass deferred the gate before this task could exist. Under the queue policy it parks at the tail of the make-test -> conform -> kernel-differential chain so only one heavy pool is hot at a time."""
    if pool_policy == "queue":
        for fut in (kernel_fut, conform_fut, make_fut):
            if fut is not None:
                try:
                    fut.result()
                except Exception:
                    pass
    result = spawn("gate:rebuild", REBUILD_PYTEST_ARGV, emit=emit, registry=registry, stream=False)
    return classify_rebuild_output(result.stdout, result.returncode, update_pins)


def _gate_result(fut: Future, name: str, failures: list[str]):
    try:
        return fut.result()
    except Exception as exc:
        failures.append(f"{name} raised: {exc!r}")
        return None


def _join_gates(
    report: CycleReport,
    failures: list[str],
    js_fut: Future | None,
    rebuild_fut: Future | None,
    conform_fut: Future | None,
    kernel_fut: Future | None,
    kernel_harness_fut: Future | None,
    make_fut: Future | None,
    emit: _Emitter,
) -> None:
    if js_fut is not None:
        js = _gate_result(js_fut, "gate:js", failures)
        if js is None:
            report.gate_js = "FAILED (exception)"
        else:
            report.gate_js = "green" if js.returncode == 0 else f"FAILED (exit {js.returncode})"
            if js.returncode != 0:
                failures.append("JS suite failed")
    if rebuild_fut is not None:
        outcome = _gate_result(rebuild_fut, "gate:rebuild", failures)
        if outcome is None:
            report.gate_rebuild = "FAILED (exception)"
        else:
            report.gate_rebuild = outcome.status
            report.rebuild_recordable = outcome.recordable
            for test_id in outcome.hard_ids:
                emit.emit(f"  hard rebuild failure: {test_id}")
            failures.extend(outcome.failures)
    if conform_fut is not None:
        conform = _gate_result(conform_fut, "gate:conform", failures)
        if conform is None:
            report.gate_conform = "FAILED (exception)"
        else:
            status, conform_failures = conform
            report.gate_conform = status
            failures.extend(conform_failures)
    if kernel_fut is not None:
        kernel = _gate_result(kernel_fut, "gate:kernel-differential", failures)
        if kernel is None:
            report.gate_kernel_differential = "FAILED (exception)"
        else:
            status, kernel_failures = kernel
            report.gate_kernel_differential = status
            failures.extend(kernel_failures)
    if kernel_harness_fut is not None:
        harness = _gate_result(kernel_harness_fut, "gate:kernel-harness", failures)
        if harness is None:
            report.gate_kernel_harness = "FAILED (exception)"
        else:
            status, harness_failures = harness
            report.gate_kernel_harness = status
            failures.extend(harness_failures)
    if make_fut is not None:
        make = _gate_result(make_fut, "gate:make-test", failures)
        if make is None:
            report.gate_make_test = "FAILED (exception)"
        else:
            report.gate_make_test = "green" if make.returncode == 0 else f"FAILED (exit {make.returncode})"
            if make.returncode != 0:
                failures.append("make test failed")


def _plumbing_settled(report: CycleReport) -> bool:
    """Whether the chain closed at a fixpoint, which is what the plumbing green claims and only the last step can witness. Each step feeds the next — the carry's merge gives echo-fill new agreement to read, and echo-fill only ever removes blanks, so it can never hand standing-fill work it did not already have — but standing-fill runs last and nothing re-reads it: a standing fill landing on one unit can make its echo group unanimous and leave a blank sibling that echo-fill would have taken on the following pass. So the fixpoint is provable exactly when the standing merge moved nothing, and a pass whose standing fills did land records no green and lets the next pass close the cascade."""
    return any(line.startswith("nothing changed") for line in report.standing_merge_lines)


def _record_gate_greens(report: CycleReport, plan: Plan, gate_keys: dict[str, str], emit: _Emitter) -> None:
    """Persist the concurrent gates' green records after they joined. gate:conform's and gate:kernel-differential's keys were snapshotted right after run_m1 finished (the sources and artifacts they hash are final from then on); gate:rebuild's and gate:kernel-harness's at their later submission, after the census step, so on an --update-pins pass gate:rebuild hashes the pins the suite actually read. Each is recomputed here before recording, so a source file edited while the gates ran — content the gates never tested — can never be recorded green. A red gate whose key still matches its record deletes the falsified record."""
    key = gate_keys.get("conform")
    if key:
        if report.gate_conform == "green":
            if conform_skip_fingerprint(ROOT, plan.conform_horizon) == key:
                record_green(CONFORM_GREEN, key, files=conform_skip_files(ROOT, plan.conform_horizon))
            else:
                emit.emit(
                    "gate:conform green, but its inputs changed while the cycle ran — green not recorded"
                )
        elif report.gate_conform.startswith("FAILED"):
            clear_contradicted_green(CONFORM_GREEN, key)
    key = gate_keys.get("kernel-differential")
    if key:
        if report.gate_kernel_differential == "green":
            if kernel_differential_skip_fingerprint(ROOT) == key:
                record_green(KERNEL_DIFFERENTIAL_GREEN, key, files=kernel_differential_skip_files(ROOT))
            else:
                emit.emit(
                    "gate:kernel-differential green, but its inputs changed while the cycle ran — green not recorded"
                )
        elif report.gate_kernel_differential.startswith("FAILED"):
            clear_contradicted_green(KERNEL_DIFFERENTIAL_GREEN, key)
    key = gate_keys.get("kernel-harness")
    if key:
        if report.gate_kernel_harness == "green":
            if kernel_harness_skip_fingerprint(ROOT) == key:
                record_green(KERNEL_HARNESS_GREEN, key, files=kernel_harness_skip_files(ROOT))
            else:
                emit.emit(
                    "gate:kernel-harness green, but its inputs changed while the cycle ran — green not recorded"
                )
        elif report.gate_kernel_harness.startswith("FAILED"):
            clear_contradicted_green(KERNEL_HARNESS_GREEN, key)
    key = gate_keys.get("rebuild")
    if key:
        if report.gate_rebuild.startswith("green") and report.rebuild_recordable:
            if rebuild_gate_skip_fingerprint(ROOT) == key:
                record_green(REBUILD_GATE_GREEN, key)
            else:
                emit.emit(
                    "gate:rebuild green, but its input closure changed while the cycle ran — green not recorded"
                )
        elif report.gate_rebuild.startswith("FAILED"):
            clear_contradicted_green(REBUILD_GATE_GREEN, key)


def _run_cycle(
    plan: Plan,
    report: CycleReport,
    emit: _Emitter,
    registry: _ChildRegistry,
    spawn=_run_step,
    timings: CycleTimings | None = None,
) -> int:
    if timings is not None:
        spawn = timings.wrap_spawn(spawn)
    pool = ThreadPoolExecutor(max_workers=_GATE_POOL_WORKERS)
    failures: list[str] = []
    try:
        defer_rebuild = "rebuild" in plan.deferred
        defer_conform = "conform" in plan.deferred
        defer_kernel = "kernel-differential" in plan.deferred
        defer_harness = "kernel-harness" in plan.deferred
        defer_make_test = "make-test" in plan.deferred
        js_fut = None if plan.skip_gates else pool.submit(_gate_js_task, spawn, emit, registry)
        make_fut = (
            None
            if plan.skip_gates or plan.skip_make_test or defer_make_test
            else pool.submit(_gate_make_test_task, spawn, emit, registry)
        )
        rebuild_fut: Future | None = None
        conform_fut: Future | None = None
        kernel_fut: Future | None = None
        kernel_harness_fut: Future | None = None
        gate_keys: dict[str, str] = {}
        if not plan.skip_gates and plan.skip_conform:
            report.gate_conform = f"skipped ({plan.conform_note or '--skip-conform'})"
        elif defer_conform:
            report.gate_conform = f"deferred ({DEFER_NOTE})"
        if not plan.skip_gates and plan.skip_kernel_differential:
            report.gate_kernel_differential = (
                f"skipped ({plan.kernel_differential_note or '--skip-kernel-differential'})"
            )
        elif defer_kernel:
            report.gate_kernel_differential = f"deferred ({DEFER_NOTE})"
        if not plan.skip_gates and plan.skip_kernel_harness:
            report.gate_kernel_harness = f"skipped ({plan.kernel_harness_note or '--skip-kernel-harness'})"
        elif defer_harness:
            report.gate_kernel_harness = f"deferred ({DEFER_NOTE})"
        if not plan.skip_gates and plan.skip_rebuild_gate:
            report.gate_rebuild = f"skipped ({plan.rebuild_gate_note})"
        elif defer_rebuild:
            report.gate_rebuild = f"deferred ({DEFER_NOTE})"
        if not plan.skip_gates and plan.skip_make_test:
            report.gate_make_test = f"skipped ({plan.make_test_note})"
        elif defer_make_test:
            report.gate_make_test = f"deferred ({DEFER_NOTE})"

        gate = _do_run_m1(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            budget=plan.job_budget,
            skip=plan.skip_run_m1,
            skip_note=plan.run_m1_note,
            record=plan.record_greens,
            fingerprint=plan.run_m1_fingerprint,
            fresh_memo=plan.fresh,
        )
        if gate is None or not gate.ok:
            failures.extend(_run_m1_reasons(gate))
            if (plan.skip_gates or not plan.skip_rebuild_gate) and not defer_rebuild:
                report.gate_rebuild = "not run (run_m1 gate failed)"
            if not plan.skip_gates and not plan.skip_conform and not defer_conform:
                report.gate_conform = "not run (run_m1 gate failed)"
            if not plan.skip_gates and not plan.skip_kernel_differential and not defer_kernel:
                report.gate_kernel_differential = "not run (run_m1 gate failed)"
            if not plan.skip_gates and not plan.skip_kernel_harness and not defer_harness:
                report.gate_kernel_harness = "not run (run_m1 gate failed)"
            _join_gates(report, failures, js_fut, None, None, None, None, make_fut, emit)
            return _finish(report, failures, plan, timings)

        if not plan.skip_gates and not plan.skip_conform and not defer_conform:
            if plan.record_greens:
                gate_keys["conform"] = conform_skip_fingerprint(ROOT, plan.conform_horizon)
            conform_fut = pool.submit(
                _gate_conform_task,
                plan.pool_policy,
                make_fut,
                spawn,
                emit,
                registry,
                conform_gate_argv(plan.conform_jobs, plan.conform_horizon),
            )

        if not plan.skip_gates and not plan.skip_kernel_differential and not defer_kernel:
            if plan.record_greens:
                gate_keys["kernel-differential"] = kernel_differential_skip_fingerprint(ROOT)
            kernel_fut = pool.submit(
                _gate_kernel_differential_task,
                plan.pool_policy,
                conform_fut,
                make_fut,
                spawn,
                emit,
                registry,
                kernel_differential_argv(plan.kernel_threads),
            )

        if not _do_surface_build(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            review_out=plan.review_out,
            budget=plan.job_budget,
            skip=plan.skip_surface,
            skip_note=plan.surface_note,
            fresh=plan.fresh,
        ):
            failures.append("surface rebuild failed")
            if not plan.skip_gates and not plan.skip_rebuild_gate and not defer_rebuild:
                report.gate_rebuild = "not run (surface build failed)"
            if not plan.skip_gates and not plan.skip_kernel_harness and not defer_harness:
                report.gate_kernel_harness = "not run (surface build failed)"
            _join_gates(report, failures, js_fut, None, conform_fut, kernel_fut, None, make_fut, emit)
            _record_gate_greens(report, plan, gate_keys, emit)
            return _finish(report, failures, plan, timings)

        plumbing_key: str | None = None
        if plan.skip_plumbing:
            _skip_plumbing(report, plan, emit)
        elif plan.carry_out is not None:
            carried = _do_carry(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
            if not carried:
                failures.append("carry_verdicts failed")
            if plan.do_merge:
                if not carried:
                    report.merge_status = "not run (carry failed)"
                    report.echo_fill_status = "not run (carry failed)"
                    report.echo_merge_status = "not run (carry failed)"
                    report.standing_fill_status = "not run (carry failed)"
                    report.standing_merge_status = "not run (carry failed)"
                elif not _do_merge(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("verdict merge failed")
                    report.echo_fill_status = "not run (merge failed)"
                    report.echo_merge_status = "not run (merge failed)"
                    report.standing_fill_status = "not run (merge failed)"
                    report.standing_merge_status = "not run (merge failed)"
                elif not _do_echo_fill(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("echo-fill failed")
                    report.echo_merge_status = "not run (echo-fill failed)"
                    report.standing_fill_status = "not run (echo-fill failed)"
                    report.standing_merge_status = "not run (echo-fill failed)"
                elif not _do_echo_merge(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("echo-merge failed")
                    report.standing_fill_status = "not run (echo-merge failed)"
                    report.standing_merge_status = "not run (echo-merge failed)"
                elif not _do_standing_fill(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("standing-fill failed")
                    report.standing_merge_status = "not run (standing-fill failed)"
                elif not _do_standing_merge(report, spawn=spawn, emit=emit, registry=registry, plan=plan):
                    failures.append("standing-merge failed")
                elif _plumbing_settled(report):
                    plumbing_key = plumbing_skip_fingerprint(ROOT, REVIEW_OUT, plan.verdicts)
        if plan.defer_census:
            report.census_status = f"deferred ({DEFER_NOTE})"
            emit.emit(f"\ncensus: DEFERRED — {DEFER_NOTE}.")
        elif plan.skip_census:
            report.census_status = _replay_census(plan, emit)
        else:
            report.census_status = _do_census(
                spawn=spawn,
                emit=emit,
                registry=registry,
                update_pins=plan.update_pins,
                surface=plan.census_surface,
                record=plan.record_greens and plan.review_out is None,
            )
        if not plan.skip_gates and not plan.skip_rebuild_gate and not defer_rebuild:
            if (
                plan.defer_rebuild_on_stale_census
                and not plan.update_pins
                and plan.review_out is None
                and report.census_status.startswith("STALE")
            ):
                report.rebuild_stale_deferred = True
                report.gate_rebuild = f"deferred ({STALE_CENSUS_DEFER_NOTE})"
                emit.emit(f"gate:rebuild deferred: {STALE_CENSUS_DEFER_NOTE}")
            else:
                if plan.record_greens:
                    gate_keys["rebuild"] = rebuild_gate_skip_fingerprint(ROOT) or ""
                rebuild_fut = pool.submit(
                    _gate_rebuild_task,
                    plan.pool_policy,
                    kernel_fut,
                    conform_fut,
                    make_fut,
                    spawn,
                    emit,
                    registry,
                    plan.update_pins,
                )
        if not plan.skip_gates and not plan.skip_kernel_harness and not defer_harness:
            if plan.record_greens:
                gate_keys["kernel-harness"] = kernel_harness_skip_fingerprint(ROOT)
            kernel_harness_fut = pool.submit(
                _gate_kernel_harness_task,
                plan.pool_policy,
                rebuild_fut,
                spawn,
                emit,
                registry,
                kernel_harness_argv(),
            )
        complaints_ran = False
        if plan.complaints_note:
            report.complaints_status = f"skipped ({plan.complaints_note})"
        else:
            report.complaints_status = _do_complaints(spawn=spawn, emit=emit, registry=registry)
            complaints_ran = not report.complaints_status.startswith("FAILED")
        if plumbing_key and complaints_ran and plan.record_greens and plan.review_out is None:
            record_plumbing_green(plumbing_key, plan.carry_out)

        _join_gates(
            report, failures, js_fut, rebuild_fut, conform_fut, kernel_fut, kernel_harness_fut, make_fut, emit
        )
        _record_gate_greens(report, plan, gate_keys, emit)
        return _finish(report, failures, plan, timings)
    except KeyboardInterrupt:
        registry.terminate_all()
        pool.shutdown(wait=False, cancel_futures=True)
        report.interrupted = True
        return _finish_interrupted(report, failures, registry.killed_count, plan, timings)
    finally:
        pool.shutdown(wait=True)


def _print_summary(report: CycleReport) -> None:
    def show(value: object) -> str:
        return "—" if value is None else str(value)

    print("\n" + "=" * 68)
    print("ARTIFACT CYCLE SUMMARY")
    print("=" * 68)
    print(f"  snapshot dir       : {show(report.snapshot_dir)}")
    print(f"  oracle UNMATCHED   : {show(report.unmatched)} (informational)")
    print(f"  oracle multi_match : {show(report.multi_matched)}")
    print(f"  boundary gate      : {'pass' if report.boundary_pass else show(report.boundary_pass)}")
    print(f"  Manual-pin gate    : {'pass' if report.pins_pass else show(report.pins_pass)}")
    print(f"  surface units      : {show(report.surface_units)}")
    print(f"  surface rows       : {show(report.surface_rows)}")
    print(f"  surface batches    : {show(report.surface_batches)}")
    print(f"  echo groups        : {show(report.echo_groups)}")
    print(f"  carry output       : {show(report.carry_out)}")
    for line in report.carry_lines:
        print(f"      {line}")
    print(f"  merge -> autosave  : {report.merge_status}")
    for line in report.merge_lines:
        print(f"      {line}")
    print(f"  echo-fill          : {report.echo_fill_status}")
    for line in report.echo_fill_lines:
        print(f"      {line}")
    print(f"  echo-merge         : {report.echo_merge_status}")
    for line in report.echo_merge_lines:
        print(f"      {line}")
    print(f"  standing-fill      : {report.standing_fill_status}")
    for line in report.standing_fill_lines:
        print(f"      {line}")
    print(f"  standing-merge     : {report.standing_merge_status}")
    for line in report.standing_merge_lines:
        print(f"      {line}")
    print(f"  census pins        : {report.census_status}")
    print(f"  complaint groups   : {report.complaints_status}")
    print(f"  gate: JS suite     : {report.gate_js}")
    print(f"  gate: rebuild      : {report.gate_rebuild}")
    print(f"  gate: conform      : {report.gate_conform}")
    print(f"  gate: kernel-diff  : {report.gate_kernel_differential}")
    print(f"  gate: harness      : {report.gate_kernel_harness}")
    print(f"  gate: make test    : {report.gate_make_test}")
    print("  run_m1 summaries   :")
    for path in M1_SUMMARY_FILES.values():
        print(f"      {path}")
    print(f"      {CONFORM_SUMMARY}")
    print(f"      {KERNEL_DIFFERENTIAL_SUMMARY}")
    print(f"      {KERNEL_HARNESS_SUMMARY}")
    print("=" * 68)


def _as_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _gate_entry(status: str, skip: str | None = None) -> dict:
    """`skip` is why the gate did not run, and it is the discriminator the readiness checker needs: "proved" means a matching green record already showed this exact content passing, so the state is verified; "forced" means a flag suppressed the gate and nothing proved anything; "deferred" means this pass chose the surface over the verification and left the gate for the next one, which is likewise unproven but has a one-command remedy. The status prose cannot carry that — every kind reads as some flavor of "skipped" — and a reader that cannot tell them apart is what once let --skip-conform report READY."""
    return {"status": status, "green": status.startswith("green"), "skip": skip}


def _skip_kind(*, proved: bool, deferred: bool, forced: bool = False) -> str | None:
    """Most-informative first. A flag outranks deferral because a gate the caller switched off is not waiting on anything — the two never co-occur (a forced gate is never a candidate for deferral), but the order says which reading wins if they ever do."""
    if proved:
        return "proved"
    if forced:
        return "forced"
    return "deferred" if deferred else None


def _surface_block(surface_dir: Path) -> dict:
    block: dict = {"dir": str(surface_dir), "generated_at": None, "inputs_fingerprint": None}
    try:
        manifest = json.loads((surface_dir / "manifest.json").read_text())
        block["generated_at"] = manifest.get("generated_at")
        block["inputs_fingerprint"] = manifest.get("inputs_fingerprint")
    except Exception:
        pass
    return block


def cycle_summary_payload(report: CycleReport, failures: list[str], plan: Plan, exit_kind: str) -> dict:
    return {
        "format": "ams-cycle-summary/1",
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "exit": exit_kind,
        "failures": list(failures),
        "gates": {
            "js": _gate_entry(report.gate_js),
            "rebuild": _gate_entry(
                report.gate_rebuild,
                _skip_kind(
                    proved=plan.skip_rebuild_gate,
                    deferred="rebuild" in plan.deferred or report.rebuild_stale_deferred,
                ),
            ),
            "conform": _gate_entry(
                report.gate_conform,
                _skip_kind(
                    proved=plan.conform_proven,
                    deferred="conform" in plan.deferred,
                    forced=plan.skip_conform,
                ),
            ),
            "kernel_differential": _gate_entry(
                report.gate_kernel_differential,
                _skip_kind(
                    proved=plan.kernel_differential_proven,
                    deferred="kernel-differential" in plan.deferred,
                    forced=plan.skip_kernel_differential,
                ),
            ),
            "kernel_harness": _gate_entry(
                report.gate_kernel_harness,
                _skip_kind(
                    proved=plan.kernel_harness_proven,
                    deferred="kernel-harness" in plan.deferred,
                    forced=plan.skip_kernel_harness,
                ),
            ),
            "make_test": _gate_entry(
                report.gate_make_test,
                _skip_kind(proved=plan.skip_make_test, deferred="make-test" in plan.deferred),
            ),
        },
        "make_test_fingerprint": (
            plan.make_test_fingerprint
            if report.gate_make_test.startswith("green") or plan.skip_make_test
            else None
        ),
        "unmatched": report.unmatched,
        "multi_matched": report.multi_matched,
        "boundary_pass": report.boundary_pass,
        "pins_pass": report.pins_pass,
        "surface_units": report.surface_units,
        "surface_rows": report.surface_rows,
        "surface_batches": report.surface_batches,
        "echo_groups": report.echo_groups,
        "carry_out": _as_str(report.carry_out),
        "carry_lines": list(report.carry_lines),
        "merge_status": report.merge_status,
        "merge_lines": list(report.merge_lines),
        "echo_fill_status": report.echo_fill_status,
        "echo_fill_lines": list(report.echo_fill_lines),
        "echo_merge_status": report.echo_merge_status,
        "echo_merge_lines": list(report.echo_merge_lines),
        "standing_fill_status": report.standing_fill_status,
        "standing_fill_lines": list(report.standing_fill_lines),
        "standing_merge_status": report.standing_merge_status,
        "standing_merge_lines": list(report.standing_merge_lines),
        "census_status": report.census_status,
        "complaints_status": report.complaints_status,
        "snapshot_dir": _as_str(report.snapshot_dir),
        "interrupted": report.interrupted,
        "plan": {
            "verdicts": _as_str(plan.verdicts),
            "carry_out": _as_str(plan.carry_out),
            "do_merge": plan.do_merge,
            "conform_horizon": plan.conform_horizon,
            "kernel_threads": plan.kernel_threads,
            "pool_policy": plan.pool_policy,
            "skip_gates": plan.skip_gates,
            "skip_conform": plan.skip_conform,
            "skip_kernel_differential": plan.skip_kernel_differential,
            "skip_kernel_harness": plan.skip_kernel_harness,
            "skip_run_m1": plan.skip_run_m1,
            "skip_surface": plan.skip_surface,
            "skip_rebuild_gate": plan.skip_rebuild_gate,
            "skip_census": plan.skip_census,
            "defer_census": plan.defer_census,
            "skip_plumbing": plan.skip_plumbing,
            "deferred": sorted(plan.deferred),
            "update_pins": plan.update_pins,
            "review_out": _as_str(plan.review_out),
            "first_run": plan.first_run,
            "short_id": plan.short_id,
        },
        "argv": list(sys.argv),
        "surface": _surface_block(plan.census_surface),
    }


def write_cycle_summary(payload: dict) -> None:
    target = CYCLE_SUMMARY
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, target)


def _emit_cycle_summary(
    report: CycleReport,
    failures: list[str],
    plan: Plan,
    exit_kind: str,
    timings: CycleTimings | None = None,
) -> None:
    payload = cycle_summary_payload(report, failures, plan, exit_kind)
    try:
        write_cycle_summary(payload)
    except Exception as exc:
        print(f"warning: failed to write {CYCLE_SUMMARY}: {exc!r}", file=sys.stderr)
    if timings is not None:
        timings.finish(payload)


def _preflight(args: argparse.Namespace, *, may_stay_up: bool = False) -> bool:
    if args.review_out is not None:
        print(
            f"Rehearsal mode: surface writes redirected to {args.review_out}; the live surface at rebuild/out/review is never written."
        )
        return True
    if not server_listening():
        return True
    if may_stay_up:
        print(f"The review server stays up: this pass {SERVER_STAYS_UP_NOTE}.")
        return True
    if args.stop_server:
        print("Stopping the review server: this pass writes the surface or the verdict store under it.")
        if stop_review_server():
            return True
        print("=" * 68)
        print(
            f"REFUSING TO RUN: something is still listening on 127.0.0.1:{REVIEW_PORT} "
            f"{SERVER_STOP_TIMEOUT:.0f}s after the stop."
        )
        print("Stop it by hand and re-run.")
        print("=" * 68)
        return False
    if args.yes:
        print("=" * 68)
        print("WARNING: a review server is listening on 127.0.0.1:7294.")
        print("Proceeding with --yes. The in-place surface rebuild will restamp the")
        print("manifest and rewrite the shards under it, stranding the live verdicting")
        print("session. AFTER this cycle you MUST:")
        print("  1. restart the review server:  uv run python -m rebuild.review.serve")
        print("  2. reload the app (the carried verdicts are merged into the autosave automatically).")
        print("=" * 68)
        return True
    print("=" * 68)
    print("REFUSING TO RUN: a review server is listening on 127.0.0.1:7294.")
    print("The in-place surface rebuild would strand your live verdicting session")
    print("(livereload rewrites the shards and the manifest restamp orphans the")
    print("autosave). Before re-running:")
    print("  1. in the review app, export or confirm the autosave of your verdicts")
    print(r"  2. stop the review server:  pkill -f 'rebuild\.review\.serve'")
    print("     (or pass --stop-server and let this command stop it for you)")
    print("  3. re-run this command (or pass --yes to override at your own risk)")
    print("  (or pass --review-out <dir> to rehearse without touching the live surface)")
    print("=" * 68)
    return False


def prune_snapshots(tmp_dir: Path, keep: Path, preserve: Path | None = None) -> list[Path]:
    """Delete every surface snapshot but this pass's. `preserve` spares one more: the snapshot of a cycle that never finished, which can be the only copy of a surface that cycle had already begun rewriting."""
    spared = {keep.resolve()}
    if preserve is not None:
        spared.add(preserve.resolve())
    removed: list[Path] = []
    for path in sorted(tmp_dir.glob("review-pre-*")):
        if not path.is_dir() or path.resolve() in spared:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


def prune_carried(root: Path, stamp: str | None, keep: Path | None) -> tuple[list[Path], list[Path]]:
    """Delete root-level carried files not stamped for the live surface. Only stamp-aligned files are ever read again (status.pick_frontier keys on manifest_generated_at, never on filename or mtime), and the tracked evidence copy lives under rebuild/evidence/, outside this glob. Unreadable files are kept and reported rather than deleted."""
    removed: list[Path] = []
    unreadable: list[Path] = []
    if stamp is None:
        return removed, unreadable
    for path in sorted(root.glob("verdicts-carried-*.json")):
        if keep is not None and path.resolve() == keep.resolve():
            continue
        try:
            data = json.loads(path.read_text())
        except OSError, ValueError:
            unreadable.append(path)
            continue
        if isinstance(data, dict) and data.get("manifest_generated_at") == stamp:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed, unreadable


def prune_stashes(root: Path, journal_path: Path) -> list[Path] | None:
    """Delete verdicts-autosave-* stashes not referenced by a journal event at or after the last base event. The reference index, not mtime, is the test: os.replace preserves the displaced store's mtime, so the stash the latest base itself created predates that base on disk. Everything deleted is replayable via --restore-as-of. Returns None (nothing touched) when the journal holds no base to anchor on."""
    from rebuild.review import journal

    events = list(journal.iter_events(journal_path))
    last_base_at = None
    for event in events:
        if event.get("base"):
            last_base_at = event.get("at") or ""
    if last_base_at is None:
        return None
    keep_names = {
        event["stashed"]
        for event in events
        if event.get("stashed") and (event.get("at") or "") >= last_base_at
    }
    removed: list[Path] = []
    for path in sorted(root.glob("verdicts-autosave-*.json")):
        if path.name in keep_names:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def retention_cutoff(now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_WINDOW_DAYS)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_retention(plan: Plan) -> None:
    from rebuild.review import journal

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    print("\nRetention (skip with --keep-history):")

    if plan.skip_plumbing:
        print(
            "  snapshots : left intact (this pass took none, so pruning to it would delete the last recovery copy)"
        )
    else:
        removed = prune_snapshots(ROOT / "tmp", plan.snapshot_dir, plan.preserve_snapshot)
        if removed:
            print(
                f"  snapshots : removed {len(removed)} ({', '.join(rel(path) for path in removed)}); kept {rel(plan.snapshot_dir)}"
            )
        else:
            print(f"  snapshots : nothing to remove; kept {rel(plan.snapshot_dir)}")

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        stamp = None
    if stamp is None:
        print("  carried   : left intact (no surface manifest to align against)")
    else:
        removed, unreadable = prune_carried(ROOT, stamp, plan.carry_out)
        print(
            f"  carried   : removed {len(removed)} stale verdicts-carried-*.json; kept the stamp-aligned frontier"
        )
        for path in unreadable:
            print(f"              kept {rel(path)} (unreadable, not pruning it)")

    journal_path = ROOT / journal.JOURNAL_NAME
    if server_listening():
        print(
            "  stashes   : left intact (the review server is up, and the index of which ones are still referenced comes from the journal this pass is leaving alone)"
        )
        print(
            "  journal   : left intact (the review server is up: the app appends to the journal as you verdict, and a compaction rewrites the whole file around a read, so anything landing in between would be dropped)"
        )
        return

    removed_stashes = prune_stashes(ROOT, journal_path)
    if removed_stashes is None:
        print("  stashes   : left intact (the journal holds no base event to anchor on)")
    else:
        print(
            f"  stashes   : removed {len(removed_stashes)} verdicts-autosave-* stashes older than the journal's last base"
        )

    result = journal.compact(journal_path, cutoff=retention_cutoff())
    if result["compacted"]:
        total = result["dropped_lines"] + result["kept_lines"]
        print(
            f"  journal   : compacted {total} -> {result['kept_lines']} lines (restore floor now {result['floor_at']})"
        )
    else:
        print(f"  journal   : left intact (no base event older than {RETENTION_WINDOW_DAYS} days)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the commit-time artifact cycle: snapshot, run_m1, surface rebuild, carry, census pins, gates."
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        help="prior verdicts master to carry forward (default: auto-resolve the best candidate among the autosave and the verdicts-*.json files at the repo root and under rebuild/evidence)",
    )
    parser.add_argument("--no-carry", action="store_true", help="skip the verdict carry-forward step")
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="leave verdicts-autosave.json untouched after the carry (skip the automatic merge into the live store)",
    )
    parser.add_argument(
        "--carry-out",
        type=Path,
        help="carried-forward output path (default: verdicts-carried-<short hash>.json at the repo root)",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="where to snapshot the current surface (default: tmp/review-pre-<short hash>, or the first free -2, -3 name when a pass at this commit already took it)",
    )
    parser.add_argument(
        "--update-pins",
        action="store_true",
        help="re-baseline the census pins and print their git diff (default: check only, report staleness)",
    )
    parser.add_argument(
        "--skip-gates",
        action="store_true",
        help="skip the six post-build gates (JS suite, rebuild suite, conformance sweep, kernel differential, kernel harness, make test)",
    )
    parser.add_argument(
        "--skip-conform",
        action="store_true",
        help="skip gate:conform (the exhaustive font-vs-settle sweep) while keeping the other gates",
    )
    parser.add_argument(
        "--skip-kernel-differential",
        action="store_true",
        help="skip gate:kernel-differential (the Rust-vs-Python differential over this cycle's own table artifacts) while keeping the other gates",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        default=KERNEL_THREADS_DEFAULT,
        help="how many configs the Rust kernel enumerates at once inside gate:kernel-differential; the answers are byte-identical at any width, so this trades wall time against memory",
    )
    parser.add_argument(
        "--skip-kernel-harness",
        action="store_true",
        help="skip gate:kernel-harness (the Rust port's three landing harnesses, re-run whenever the alphabet's structure or either engine's kernel sources move) while keeping the other gates",
    )
    parser.add_argument(
        "--force-make-test",
        action="store_true",
        help="run gate:make-test even when its input closure is unchanged since its last green run (the auto-skip)",
    )
    parser.add_argument(
        "--defer-gates",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="on a pass that rebuilds M1 or the surface, record the heavy gates (rebuild, conform, kernel-differential, kernel-harness, make-test) pending instead of running them, so the letters are on screen sooner; the next pass has no artifact work and runs them. `make review-cycle` passes this; a deferred gate is unproven, so readiness stays NOT READY until a later pass clears it",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="run every stage and gate even when a green record proves its inputs unchanged since the last green run (disables all auto-skips, gate:make-test's included)",
    )
    parser.add_argument(
        "--conform-horizon",
        type=int,
        default=CONFORM_HORIZON_DEFAULT,
        help="exhaustive sweep length for gate:conform, passed through to run_m1 --conform-only; drop below 5 when the sweep becomes the cycle's long pole — witness top-ups keep rule/transition coverage exact at any horizon",
    )
    parser.add_argument(
        "--rebuild-pool",
        choices=POOL_POLICIES,
        default=REBUILD_POOL_POLICY_DEFAULT,
        help="how the heavy gates share cores: 'queue' (one pool at a time — make-test, then rebuild, then conform; default) or 'overlap' (co-resident)",
    )
    parser.add_argument(
        "--review-out",
        type=Path,
        default=None,
        help="rehearsal mode: redirect the surface write to this dir so the cycle can run while the live server is up",
    )
    parser.add_argument(
        "--keep-history",
        action="store_true",
        help="skip the green-finish retention pass (old snapshots, stale carried files and stashes, and the journal's pre-window history all stay on disk)",
    )
    parser.add_argument("--yes", action="store_true", help="override the running-review-server refusal")
    parser.add_argument(
        "--stop-server",
        action="store_true",
        help="stop a listening review server instead of refusing, but only when this pass writes under it — the surface it serves or the verdict store it holds. A pass that writes neither leaves the server up whether or not this is passed, so the letters stay on screen through it; `make review-cycle` passes this, which is what makes a gate-only pass background verification rather than a lockout",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved step plan and exit without executing anything",
    )
    args = parser.parse_args(argv)
    if args.fresh:
        args.force_make_test = True

    first_run = not (REVIEW_OUT / "manifest.json").exists()

    skip_make_test = False
    make_test_note = ""
    make_test_fp: str | None = None
    if not args.skip_gates:
        make_test_fp = make_test_closure_fingerprint(ROOT)
        if (
            not args.force_make_test
            and make_test_fp is not None
            and make_test_fp == prior_make_test_fingerprint()
        ):
            skip_make_test = True
            make_test_note = "closure unchanged since its last green run; --force-make-test overrides"
            print(f"gate:make-test auto-skipped: {make_test_note}")

    run_m1_fp = run_m1_skip_fingerprint(ROOT)
    skip_run_m1 = False
    run_m1_note = ""
    skip_surface = False
    surface_note = ""
    skip_rebuild_gate = False
    rebuild_gate_note = ""
    conform_note = ""
    auto_skip_conform = False
    kernel_differential_note = ""
    auto_skip_kernel_differential = False
    kernel_harness_note = ""
    auto_skip_kernel_harness = False
    skip_census = False
    census_skip_note = ""
    census_replay: dict | None = None
    if not args.fresh:
        green = read_green_record(RUN_M1_GREEN)
        if green is not None and green["fingerprint"] == run_m1_fp and m1_artifacts_present(ROOT):
            skip_run_m1 = True
            run_m1_note = "build inputs unchanged since the last green M1 build; --fresh overrides"
            print(f"run_m1 auto-skipped: {run_m1_note}")
        elif green is not None and isinstance(green.get("files"), dict):
            note = moved_inputs_note(green, run_m1_skip_files(ROOT))
            if note is not None:
                print(f"run_m1 will rebuild — inputs moved since its last green: {note}")
    if skip_run_m1:
        if args.review_out is None and not first_run and surface_build_skippable(ROOT):
            skip_surface = True
            surface_note = (
                "the surface already reflects these inputs byte for byte, stamp included; --fresh overrides"
            )
            print(f"surface-build auto-skipped: {surface_note}")
        if not args.skip_gates and not args.skip_conform:
            green = read_green_record(CONFORM_GREEN)
            if green is not None and green["fingerprint"] == conform_skip_fingerprint(
                ROOT, args.conform_horizon
            ):
                auto_skip_conform = True
                conform_note = "font and sweep inputs unchanged since its last green sweep; --fresh overrides"
                print(f"gate:conform auto-skipped: {conform_note}")
        if not args.skip_gates and not args.skip_kernel_differential:
            green = read_green_record(KERNEL_DIFFERENTIAL_GREEN)
            if green is not None and green["fingerprint"] == kernel_differential_skip_fingerprint(ROOT):
                auto_skip_kernel_differential = True
                kernel_differential_note = "spec inputs and both engines' sources unchanged since its last green differential; --fresh overrides"
                print(f"gate:kernel-differential auto-skipped: {kernel_differential_note}")
        if not args.skip_gates and not args.skip_kernel_harness:
            green = read_green_record(KERNEL_HARNESS_GREEN)
            if green is not None and green["fingerprint"] == kernel_harness_skip_fingerprint(ROOT):
                auto_skip_kernel_harness = True
                kernel_harness_note = "alphabet structure and both engines' sources unchanged since its last green harness run; --fresh overrides"
                print(f"gate:kernel-harness auto-skipped: {kernel_harness_note}")
        if not args.skip_gates:
            rebuild_key = rebuild_gate_skip_fingerprint(ROOT)
            green = read_green_record(REBUILD_GATE_GREEN)
            if rebuild_key is not None and green is not None and green["fingerprint"] == rebuild_key:
                skip_rebuild_gate = True
                rebuild_gate_note = "input closure unchanged since its last green run; --fresh overrides"
                print(f"gate:rebuild auto-skipped: {rebuild_gate_note}")
        if skip_surface and not args.update_pins:
            census_key = census_skip_fingerprint(ROOT)
            result = read_census_result(CENSUS_RESULT)
            if census_key is not None and result is not None and result["fingerprint"] == census_key:
                skip_census = True
                census_replay = result
                census_skip_note = f"surface, pins, and source inputs unchanged since the last {result['status']} check; --fresh overrides"
                print(f"census auto-skipped: {census_skip_note}")

    preserve_snapshot = unfinished_cycle_snapshot()
    if preserve_snapshot is not None:
        print(
            f"The last cycle did not finish green; keeping its snapshot at {preserve_snapshot} as well as this pass's."
        )

    defer_active = args.defer_gates and not args.fresh and args.review_out is None
    refreshing = not skip_run_m1 or not skip_surface
    deferred = deferred_gates(
        defer=defer_active,
        refreshing=refreshing,
        would_run={
            "rebuild": not args.skip_gates and not skip_rebuild_gate,
            "conform": not args.skip_gates and not args.skip_conform and not auto_skip_conform,
            "kernel-differential": (
                not args.skip_gates
                and not args.skip_kernel_differential
                and not auto_skip_kernel_differential
            ),
            "kernel-harness": (
                not args.skip_gates and not args.skip_kernel_harness and not auto_skip_kernel_harness
            ),
            "make-test": not args.skip_gates and not skip_make_test and not args.force_make_test,
        },
    )
    if deferred:
        print(
            "Heavy gates deferred to the next pass: "
            + ", ".join("gate:" + name for name in sorted(deferred))
            + f" — {DEFER_NOTE}; --no-defer-gates runs them in this one."
        )
    defer_census = defer_active and refreshing and not args.update_pins
    if defer_census:
        print(f"Census deferred to the next pass — {DEFER_NOTE}; nothing in this pass reads it.")

    if not args.no_carry and args.verdicts is None and not first_run:
        resolved = resolve_carry_source()
        if resolved is None:
            args.no_carry = True
            print(
                "No carryable verdicts found (neither the autosave nor any verdicts-*.json at the repo root or under rebuild/evidence holds an effective verdict); proceeding without carry. Pass --verdicts to name a master explicitly."
            )
        else:
            print(describe_carry_source(resolved, ROOT))
            if not resolved["aligned"]:
                return 2
            args.verdicts = resolved["path"]

    skip_plumbing = False
    plumbing_note = ""
    plumbing_carry_out: Path | None = None
    if (
        skip_surface
        and not args.fresh
        and not first_run
        and args.review_out is None
        and not args.no_carry
        and not args.no_merge
        and args.carry_out is None
        and args.snapshot_dir is None
    ):
        plumbing_key = plumbing_skip_fingerprint(ROOT, REVIEW_OUT, args.verdicts)
        record = read_green_record(PLUMBING_GREEN)
        if plumbing_key is not None and record is not None and record["fingerprint"] == plumbing_key:
            skip_plumbing = True
            plumbing_note = PLUMBING_SKIP_NOTE
            recorded_carry = record.get("carry_out")
            if isinstance(recorded_carry, str) and Path(recorded_carry).exists():
                plumbing_carry_out = Path(recorded_carry)
            print(f"verdict plumbing auto-skipped: {plumbing_note}")

    if args.dry_run:
        plan = build_plan(
            verdicts=args.verdicts,
            no_carry=args.no_carry,
            carry_out=args.carry_out,
            snapshot_dir=args.snapshot_dir,
            update_pins=args.update_pins,
            skip_gates=args.skip_gates,
            first_run=first_run,
            short_id=resolve_short_id(),
            no_merge=args.no_merge,
            skip_conform=args.skip_conform or auto_skip_conform,
            skip_make_test=skip_make_test,
            make_test_note=make_test_note,
            make_test_fingerprint=make_test_fp,
            conform_horizon=args.conform_horizon,
            kernel_threads=args.kernel_threads,
            pool_policy=args.rebuild_pool,
            review_out=args.review_out,
            skip_run_m1=skip_run_m1,
            run_m1_note=run_m1_note,
            run_m1_fingerprint=run_m1_fp,
            fresh=args.fresh,
            skip_surface=skip_surface,
            surface_note=surface_note,
            skip_rebuild_gate=skip_rebuild_gate,
            rebuild_gate_note=rebuild_gate_note,
            conform_note=conform_note,
            conform_proven=auto_skip_conform,
            skip_kernel_differential=args.skip_kernel_differential or auto_skip_kernel_differential,
            kernel_differential_note=kernel_differential_note,
            kernel_differential_proven=auto_skip_kernel_differential,
            skip_kernel_harness=args.skip_kernel_harness or auto_skip_kernel_harness,
            kernel_harness_note=kernel_harness_note,
            kernel_harness_proven=auto_skip_kernel_harness,
            skip_census=skip_census,
            census_skip_note=census_skip_note,
            census_replay=census_replay,
            defer_census=defer_census,
            skip_plumbing=skip_plumbing,
            plumbing_note=plumbing_note,
            plumbing_carry_out=plumbing_carry_out,
            deferred=deferred,
            preserve_snapshot=preserve_snapshot,
            keep_history=args.keep_history,
        )
        print(render_plan(plan))
        return 0

    if not _preflight(
        args, may_stay_up=server_may_stay_up(skip_surface=skip_surface, skip_plumbing=skip_plumbing)
    ):
        return 2

    if first_run:
        print("First-run mode: no existing surface at rebuild/out/review — skipping snapshot and carry.")

    plan = build_plan(
        verdicts=args.verdicts,
        no_carry=args.no_carry,
        carry_out=args.carry_out,
        snapshot_dir=args.snapshot_dir,
        update_pins=args.update_pins,
        skip_gates=args.skip_gates,
        first_run=first_run,
        short_id=resolve_short_id(),
        no_merge=args.no_merge,
        skip_conform=args.skip_conform or auto_skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fp,
        conform_horizon=args.conform_horizon,
        kernel_threads=args.kernel_threads,
        pool_policy=args.rebuild_pool,
        review_out=args.review_out,
        skip_run_m1=skip_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fp,
        fresh=args.fresh,
        skip_surface=skip_surface,
        surface_note=surface_note,
        skip_rebuild_gate=skip_rebuild_gate,
        rebuild_gate_note=rebuild_gate_note,
        conform_note=conform_note,
        conform_proven=auto_skip_conform,
        skip_kernel_differential=args.skip_kernel_differential or auto_skip_kernel_differential,
        kernel_differential_note=kernel_differential_note,
        kernel_differential_proven=auto_skip_kernel_differential,
        skip_kernel_harness=args.skip_kernel_harness or auto_skip_kernel_harness,
        kernel_harness_note=kernel_harness_note,
        kernel_harness_proven=auto_skip_kernel_harness,
        skip_census=skip_census,
        census_skip_note=census_skip_note,
        census_replay=census_replay,
        defer_census=defer_census,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        plumbing_carry_out=plumbing_carry_out,
        defer_rebuild_on_stale_census=not args.fresh,
        deferred=deferred,
        preserve_snapshot=preserve_snapshot,
        record_greens=True,
        keep_history=args.keep_history,
    )

    report = CycleReport()
    from rebuild.tools.cycle_timings import CycleTimings

    timings = CycleTimings(CYCLE_TIMINGS)

    if not first_run and not plan.skip_plumbing:
        if plan.snapshot_dir.exists():
            print(f"ERROR: snapshot dir already exists: {plan.snapshot_dir}")
            print(
                "Refusing to overwrite the only recovery copy. Remove it or point --snapshot-dir elsewhere."
            )
            return 2
        how = snapshot_surface(REVIEW_OUT, plan.snapshot_dir)
        report.snapshot_dir = plan.snapshot_dir
        print(f"Snapshotted {REVIEW_OUT} -> {plan.snapshot_dir} ({how})")

    emit = _Emitter()
    registry = _ChildRegistry()
    return _run_cycle(plan, report, emit, registry, timings=timings)


def _finish(report: CycleReport, failures: list[str], plan: Plan, timings: CycleTimings | None = None) -> int:
    _print_summary(report)
    _emit_cycle_summary(report, failures, plan, "failed" if failures else "ok", timings)
    if failures:
        print("\nCYCLE FAILED:")
        for reason in failures:
            print(f"  - {reason}")
        return 1
    if plan.retention and plan.record_greens:
        try:
            run_retention(plan)
        except Exception as exc:
            print(f"warning: retention pass failed: {exc!r}", file=sys.stderr)
    if report.rebuild_stale_deferred:
        print("\nCycle complete — but gate:rebuild was deferred: the census pins are stale, and a suite")
        print("  run under pins already known stale can never record green. Refresh them first —")
        print("  `make review-cycle ARGS='--update-pins'` — and that pass runs the suite against the")
        print("  fresh pins. `make verdict-ready` stays NOT READY until it is green.")
        return 0
    if plan.deferred:
        names = ", ".join("gate:" + name for name in sorted(plan.deferred))
        print("\nCycle complete — the surface is refreshed and the verdicts are carried onto it.")
        print(f"  Deferred, and so far unverified on this content: {names}.")
        print("  Look at the letters now; run `make review-cycle` again to run them — nothing else")
        print("  needs to change, and that pass skips straight past the artifact chain to the gates.")
        print("  `make verdict-ready` stays NOT READY until they are green.")
        return 0
    print("\nCycle complete.")
    return 0


def _finish_interrupted(
    report: CycleReport,
    failures: list[str],
    killed_count: int,
    plan: Plan,
    timings: CycleTimings | None = None,
) -> int:
    _print_summary(report)
    _emit_cycle_summary(report, failures, plan, "interrupted", timings)
    print(f"\nCYCLE INTERRUPTED (SIGINT): terminated {killed_count} child process(es).")
    return 130


if __name__ == "__main__":
    sys.exit(main())
