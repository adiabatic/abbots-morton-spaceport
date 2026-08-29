"""The one-command driver for the commit-time artifact cycle.

It mechanizes the commit-time sequence: snapshot the current review surface (the only recovery copy, since everything under rebuild/out is gitignored), recompile M1.otf and vet it, rebuild the review surface in place, run the verdict plumbing over it, refresh the census pins from the surface's census sidecar and print their git diff (the checked-in pins are the last accepted census, so reviewing that diff at commit time is what accepts a new one), run the five gates, and — once they have joined and their pytest controllers have stamped this pass's own per-worker peaks into the timings journal — hold the checked-in per-unit peaks against what this box actually measured (rebuild.tools.calibrate_budgets --check). Always printing a summary table at the end, even on failure.

That last step gates nothing, by the same argument the census pins are not a gate: a divisor that has gone stale makes a pool the wrong width, which is a cost rather than a defect, so it is reported loudly and never fails a pass whose artifacts are green. Committing the re-seeded constant is the acceptance, and when the check trips the driver diffs the three files that hold those constants so a working tree where one has already moved says so.

The plumbing is one step and one child process, rebuild.tools.verdict_chain: carry prior verdicts forward onto the fresh manifest, merge the carried file into the live autosave (so the app needs no manual import; --no-merge opts out), land echo-prefill verdicts for the blanks in unanimously-judged echo groups, land standing-approval verdicts matching the checked-in rules in rebuild/standing-approvals.yaml, merge each fill as it lands, run the echo pass again to witness that the cascade has closed, and cluster the open complaints. It was seven children until each of them separately parsed 1.9 GB of unit shards to reach a few slim fields per unit; they read the build's per-unit index sidecar now, and one process holds one copy of it for the whole chain. The chain prints a `[chain] <step>` banner around each step and a `[t] <step>` line after it, so the summary below and the cycle-timings journal still read a line per step.

The exit-code trap this driver exists to defuse: run_m1.main() SystemExits nonzero whenever any oracle rows are UNMATCHED, which is always true mid-migration. Its exit code is therefore not the gate; the three summary JSONs it writes are. The real gates are defect_errors, the Manual-pin verdict (scope included, so a gate that replayed nothing cannot pass), and multi_matched == 0.

The two artifact-independent gates (js, make-test) run from t=0 in a small thread pool while the build chain runs inline-serial in the main thread. gate:conform (the exhaustive font-vs-settle sweep at the per-edit horizon, run_m1 --conform-only) starts after the run_m1 gate passes, queued behind make-test by default; its periodic deep form is `make conform-deep`, which the cycle never runs and only reports on — one line in the summary saying whether the emitted lookup has grown a shape the last deep run never shaped. The rebuild suite runs as two gates over two lanes (rebuild/conftest.py is the authority on which test is which): gate:rebuild-contracts is every test whose fixture closure holds no live build artifact, at the box's full xdist width, and gate:rebuild-validators is the rest — the readers of rebuild/out, the review surface and the fixture caches — at the narrower width rebuild/conftest.py derives for that lane, because each of those workers carries a live fixture's working set. Both are submitted once the surface build settles. For validators that is a correctness requirement: its census-module fixture prefers the provably-fresh live surface and must never observe one mid-rewrite, where the manifest has landed but review.build has not yet written the sidecar beside it. For contracts it is only courtesy — the lane reads no artifact at all — but a full-width pool must not share the box with the M1 or surface build, and waiting costs it nothing, since it parks behind conform anyway and on the common gate pass every upstream stage auto-skips, so it starts at t=0. From there on neither lane reads anything the build lane writes, the census pins included, so nothing downstream has to land before they can start. Under the default queue policy the chain is make-test -> conform -> rebuild-contracts -> rebuild-validators, so only one heavy gate pool is hot at a time — the build chain rides alongside whichever one that is rather than serial, at the widths sweep_job_budget and surface_job_budget resolve (the sweeps take one process per acceptance configuration; the surface build takes the box minus whatever make-test is holding). Contracts goes ahead of validators because it is the short lane and fails fast on a code error before the half-hour one starts. Co-resident, two heavy pools oversubscribe the cores roughly 2:1, and measured that contention roughly tripled the rebuild suite's wall time — a worse critical path than running the same work in sequence. --rebuild-pool overlap restores full co-residency.

The cycle runs no cross-language check, because there is no second implementation to check against: the kernel crate is the only engine that enumerates and the only one that settles, so neither the tables nor a window's outcome can drift from a twin. What the cycle does prove about settlement is empirical — gate:conform shapes the compiled font through HarfBuzz and compares it against a re-settle of every swept text, window by window, through the crate's own settle-cases verb, with the memo keyed on the raw window so the sweep stays independent of the crate's enumeration and fold. `make kernel-gate` is the on-demand instrument to reach for around a kernel-semantics change: the crate's own gate and the spec-ingest parity, seconds once the crate is built.

gate:make-test is auto-skipped when its input closure is provably unchanged since the last green run. The closure is every tracked or untracked-unignored file outside the exempt trees (MAKE_TEST_EXEMPT_PREFIXES; make_test_exempt is the authority) and Markdown — nothing `make test` executes (make all -> build_font over glyph_data/*.yaml non-recursively, typst, pyright over tools/ test/ conftest.py, pytest test/ site/) reads those trees, so a diff confined to them cannot move the gate's outcome and re-running its ≈15 CPU-minutes would verify nothing. The last green fingerprint lives in rebuild/out/make-test-green.json, written by rebuild.tools.make_test_gate — the `make test` entry point — on every green run, so interactive greens and cycle greens share one record and `make test` itself self-skips on the same test. cycle_summary.json still records the fingerprint the cycle ran (or validly skipped) against, and prior_make_test_fingerprint falls back to it when the shared record is absent. The fingerprint sees file content only — a system-toolchain change (a typst upgrade, say; pyright and pytest are pinned through uv.lock, which is in the closure) is invisible to it. --force-make-test runs the gate regardless (as does `make test FORCE=1` inside the wrapper).

The verdict plumbing is guarded the same way, by rebuild/out/plumbing-green.json. Every step of it is a pure function of the surface, the verdicts master, the live store, the checked-in standing approvals, and its own code, so the key is (the surface's inputs fingerprint and stamp, the master's path and bytes, the autosave's bytes, standing-approvals' bytes, the chain's own import closure plus review/serve.py). Two of those components are there because a narrower key looked sufficient and was not. The master, because it is the one input the autosave's hash cannot see: an export dropped at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code, because every sibling key folds in its own stage's executable and this chain's lives in a tree no other fingerprint reads — without it a fix to a fill's matcher or the carry's ink fallback would be skipped as already proven, silently never running. That component is the named closure `plumbing_code_paths` rather than the whole of rebuild/tools/, and rebuild/test_plumbing_closure.py walks the entry points' import graph on every contracts run to prove the name still covers what runs.

The key is captured the moment the chain closes, not at the end of the pass, so a store write landing while the census runs cannot be absorbed into a fixpoint nothing verified; the record itself is written later, once complaints has also succeeded. And the fixpoint is claimed only when the chain has witnessed it. The steps feed forward — the carry's merge gives echo-fill new agreement to read, and echo-fill only removes blanks, so it can never hand standing-fill work it did not already have — but standing-fill runs last, and a standing fill can make an echo group unanimous while a blank sibling remains. That used to cost a whole extra pass: the green was refused whenever the standing merge moved anything, and the next cycle closed the cascade. In one process another echo pass costs a second, so the chain runs the cascade to a standstill itself and the green rests on a re-run that demonstrably wrote nothing.

The skip demands that the surface build be skipping too, which is what makes the stamp knowable before the pass runs, and it takes the snapshot with it: the snapshot exists to survive this cycle's surface rewrite and to feed this cycle's carry, and a pass doing neither needs no copy. Such a pass also leaves the snapshot pile alone rather than pruning it to the copy it never made, so the stamp-aligned snapshot the last refreshing pass left stays on disk as the recovery source describe_carry_source points at. A flag that names a carry output or a snapshot directory refuses the skip outright, since honoring it would mean writing neither.

The same provably-unchanged principle guards every other heavy stage, each keyed by a content fingerprint over that stage's full input closure and a green record written only after that exact content passed live: run_m1 skips on rebuild/out/run-m1-green.json (the Stage A fingerprint components plus the oracle's subset tables and uv.lock) and re-evaluates its gate from the four summary JSONs already on disk; gate:conform skips on conform-green.json (the run_m1 key plus the M1.otf bytes and the sweep horizon); each rebuild lane skips on its own record (rebuild-contracts-green.json, rebuild-validators-green.json), keyed by rebuild_lane_fingerprint over that lane's own closure — both hold the suite's repo closure under rebuild/ and glyph_data/ plus conftest.py, pyproject.toml, uv.lock and the site fonts, and validators adds the out/m1 artifacts and the baselines it shapes against, which is exactly why the contracts key holds no artifact and the contracts lane can skip whether or not run_m1 rebuilt: a live M1 rebuild writes only under rebuild/out, which that closure does not contain. Both records are also written by rebuild.tools.rebuild_gate, the `make test-rebuild` entry point, so interactive suite greens and cycle greens share them; surface-build skips when the manifest's recorded inputs fingerprint already equals the one a build would stamp now (a rebuild would be byte-identical, mtime-floored generated_at included, so the autosave stays aligned). The census step is neither keyed nor skipped: it reads the surface build's census-facts.json sidecar and rewrites one small checked-in file in milliseconds, so it simply runs every pass. The surface, conform, and rebuild-validators skips engage only on cycles where run_m1 itself skipped, so a live M1 rebuild can never invalidate a key mid-cycle; green records are written only when the key still matches after the work ran, and a red result whose key matches its record deletes the record. --fresh runs everything regardless.

--defer-gates, which `make review-cycle` passes, turns the cycle from a one-pass verification into a converging loop. On a *refreshing* pass — one where run_m1 or the surface build has real work — the four heavy gates (rebuild-contracts, rebuild-validators, conform, make-test) are recorded pending instead of run, so a rune edit costs only the artifact chain and the letters are on screen in a fraction of the time. Only a gate that would otherwise run live is deferred: one an auto-skip already proved stays proved, so a pass that merely restamps the review UI can never turn a green gate pending. The next pass has no artifact work left, every stage auto-skips, and the pending gates run against settled artifacts; the pass after that skips those too and costs seconds. Deferral is never a waiver — a deferred gate rides `skip: "deferred"` into the cycle summary, which rebuild.review.status counts as unverified, so `make verdict-ready` and the app banner both stay NOT READY until the loop converges. --no-defer-gates runs them in the one pass, which is what `make artifact-cycle` does at commit time, and --fresh and --force-make-test likewise override deferral for the gates they force. Rehearsal mode (--review-out) never defers: it writes its surface somewhere else, so there is no live surface to see sooner, and its surface build is unskippable by construction — every rehearsal pass would look refreshing and the loop would never converge.

Which passes cost the reviewer their letters is decided here rather than by the caller, because only the resolved plan knows. Two of the things a cycle writes belong to the running app — the surface it serves, where livereload watches every shard and a restamped manifest orphans the tab's store, and the verdict store, which merge_verdicts refuses to touch under a live server because an open tab would flush its own copy back over the merge. A pass whose plan skips both writes neither, so a listening server is left alone and the letters stay on screen for the whole run: that is the gate pass, whose long verification the deferred gates exist to move off the look-edit-look path, and which used to black the app out for every minute of it. A pass whose surface did not move but whose store did takes a shape of its own: the carry there is provably the identity — the snapshot it would read is a clone of the same surface, every content key resolves to itself, and the carry preserves each record's `at`, which the merge compares strictly — so the snapshot and the carry are skipped and the master is merged straight in, which is the one thing the store's own hash cannot see. That pass still writes the store, so it is a port-taking one. A pass that does write under the app needs the port to itself, and --stop-server (which `make review-cycle` passes) is permission to take it — terminate the server and wait out the port — where a bare run still refuses and says how. Retention is the third writer: the app appends to the journal as you verdict, and a compaction rewrites the file around a read, so with a server up the journal and the stash sweep that indexes off it are both left for a later pass.

A green finish ends with a retention pass over the cycle's own disk piles, all of them regenerable or journal-covered: every tmp/review-pre-* snapshot except this cycle's is deleted (a snapshot is read once, by its own cycle's carry, and never again), root verdicts-carried-*.json files not stamped for the live surface are deleted (only the stamp-aligned frontier is ever read; the tracked copy under rebuild/evidence/ is never touched), verdicts-autosave-* stashes not referenced by a journal event at or after the last base event are deleted (the journal, not the stashes, is the sanctioned recovery path — and the reference index is the test because a stash's mtime predates the event that created it), and the journal itself is compacted to the newest base event older than RETENTION_WINDOW_DAYS, keeping at least that many days of --restore-as-of history. Failed, interrupted, first-run, and rehearsal cycles never prune; --keep-history opts out entirely; a retention error warns and never turns a green cycle red.

Run as: uv run python rebuild/tools/artifact_cycle.py — the carry source is auto-resolved from the autosave and the verdicts-*.json exports; pass --verdicts to name one explicitly.
"""

from __future__ import annotations

import argparse
import ast
import functools
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
from rebuild.review import unit_index  # noqa: E402
from rebuild.tools.peak_rss import reap_peak_rss_bytes, rss_token  # noqa: E402

if TYPE_CHECKING:
    from rebuild.tools.cycle_timings import CycleTimings
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
ECHO_FILL = ROOT / "verdicts-echo-fill.json"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
# The banners rebuild.tools.verdict_chain prints around each step of the chain, which is how one child's output still reads as seven steps here.
CHAIN_BANNER = "[chain] "
CHAIN_FAILED = CHAIN_BANNER + "failed: "
CHAIN_FIXPOINT = CHAIN_BANNER + "fixpoint: "
CYCLE_SUMMARY = ROOT / "rebuild" / "out" / "cycle_summary.json"
CYCLE_TIMINGS = ROOT / "rebuild" / "out" / "cycle-timings.ndjson"
MAKE_TEST_GREEN = ROOT / "rebuild" / "out" / "make-test-green.json"
RUN_M1_GREEN = ROOT / "rebuild" / "out" / "run-m1-green.json"
CONFORM_GREEN = ROOT / "rebuild" / "out" / "conform-green.json"
DEEP_SWEEP_GREEN = ROOT / "rebuild" / "out" / "deep-sweep-green.json"
BEHAVIOR_CLASSES = M1_OUT / "behavior_classes.json"
REBUILD_CONTRACTS_GREEN = ROOT / "rebuild" / "out" / "rebuild-contracts-green.json"
REBUILD_VALIDATORS_GREEN = ROOT / "rebuild" / "out" / "rebuild-validators-green.json"
PLUMBING_GREEN = ROOT / "rebuild" / "out" / "plumbing-green.json"
JSTEST_DIR = ROOT / "rebuild" / "review" / "jstests"
REVIEW_PORT = 7294

POOL_POLICIES = ("queue", "overlap")
REBUILD_POOL_POLICY_DEFAULT = "queue"
DEFERRABLE_GATES = ("rebuild-contracts", "rebuild-validators", "conform", "make-test")
DEFER_NOTE = "surface refreshed this pass; run the cycle again to run it"
PLUMBING_SKIP_NOTE = "surface, verdicts master, live store, and standing approvals unchanged since the last complete plumbing pass; --fresh overrides"
SERVER_STAYS_UP_NOTE = "writes neither the surface the app serves nor the verdict store it holds"
SERVER_STOP_PATTERN = r"rebuild\.review\.serve"
SERVER_STOP_TIMEOUT = 15.0
# The gate pool's seats, sized to the tasks the chain submits rather than to the box or to the work actually in flight: under the queue policy a parked task holds its worker for the whole wait — conform on make-test, contracts on both, validators on all three — so every gate task has to be seatable at once, with slack on top of that. A seat short of the task count would serialize a wait behind an unrelated task's completion, which is the queueing this pool exists not to do, and a width taken from the cores in hand would put a small box exactly there. `test_the_gate_pool_seats_every_gate_task_at_once` in rebuild/test_artifact_cycle.py is what holds this and the chain's task list in step.
_GATE_POOL_WORKERS = 7
# What one surface worker holds at its peak, and so the divisor a box divides itself by to reach this build's fan-out width. A worker is a persistent spawn process that enriches a contiguous slice of the corpus and retains every EnrichedUnit across phase 1 so that phase 2 can emit shard JSON from it, so what it holds is its own interpreter and shapers, one baseline subset table for every configuration its slice reaches — 0.95 GB apiece, and a slice of three units in a rare configuration pulls in a whole one — and then the slice's own retained units and fragments, which scale with the slice: half the corpus at width two against an eighth at width eight. That scaling is why the figure is seeded at width two, the narrowest pool the arithmetic ever starts and so the widest a worker ever gets: the first width-two pool's own records (2026-08-28, this corpus) read 14.66 and 14.77 GB per worker, where the 2026-08-27 eight-wide term-by-term measurement (844,512 units: a fixed base of 0.05 GB, four to six tables, 0.87 GB retained, 1.49 GB of fragments, a 0.43 GB pickle buffer in flight) put a typical worker near 7 GB — so a constant seeded narrow is an upper bound at every pooled width, where the eight-wide seed it replaces was outrun by 64% the first time a narrow pool ran. It rounds up past the widest worker observed for the same reason kernel_exec.CONFIG_PEAK_BYTES rounds up past its own measurement, since a per-unit cost that errs low is what puts a box into swap while one that errs high only narrows a pool. `make job-costs`' surface-worker row is where it stays honest: rebuild/review/build.py files one kind:"pool" record per pooled build, so the constant is priced against the workers that actually ran — a row that goes legitimately quiet on a box this width has sent serial, since a serial build starts no pool. It is a reading to keep current, never a contract.
SURFACE_WORKER_BYTES = 16_000_000_000
# What the surface build's parent holds while that pool is live, and so what comes off the box before the division rather than into it: the parent holds the whole workload, every unit's projection and state, and — from the moment phase 2 starts returning — every unit's shard fragment, none of it released before manifest+check. It is flat in the width, which is exactly what makes it a co-resident term and not a divisor. The measurement is the `surface-build` step peak the cycle already stamps on every pass, which is honest for this term and only this one: peak_rss.reap_peak_rss_bytes maxes over the tree instead of summing it, and the widest single process under this step is the parent, the workers holding near-even slices of a corpus the parent holds whole. It read 17.76 GB on the 2026-08-27 full-fresh build and this rounds up past that. The pile is corpus-shaped at roughly 14 KB per surface unit, so it is the fastest-drifting constant in this tree — expect to re-seed it as the alphabet migrates, off the surface-parent row of `make job-costs`, and expect the streaming-phase-2 lever WHATNEXT records to be what finally moves it down.
SURFACE_PARENT_BYTES = 20_000_000_000
# The non-memory bound on the same width, and the half of this build's argument that arithmetic cannot supply: past eight workers the build stops scaling, so widening buys duplicated subset tables and nothing else.
SURFACE_JOBS_CAP = 8
# How wide gate:make-test's pytest pool is allowed to be under a cycle, and the one number that makes the reservation beside it honest: surface_job_budget hands two cores to that pool and takes its bytes off the box beside them, so two workers is what the cycle hands the pool back. Left to itself the pool takes `-n auto`, which the root conftest.py answers for the font suite with the whole box — a pool sized as though nothing else were running, beside a build sized as though it were.
MAKE_TEST_POOL_WORKERS = 2
CONFORM_HORIZON_DEFAULT = 4
DEEP_SWEEP_HORIZON_DEFAULT = 5
COMPILE_CODE_FILES = (
    "rebuild/pipeline/emit_gsub.py",
    "rebuild/pipeline/emit_gpos.py",
    "rebuild/pipeline/pack_gsub.py",
    "rebuild/pipeline/compile_font.py",
)
RETENTION_WINDOW_DAYS = 7

M1_SUMMARY_FILES = {
    "pipeline": M1_OUT / "pipeline_summary.json",
    "manual_pins": M1_OUT / "manual_pins_summary.json",
    "oracle": M1_OUT / "oracle_summary.json",
}
CONFORM_SUMMARY = M1_OUT / "conform_summary.json"

REBUILD_LANES = ("contracts", "validators")


def rebuild_lane_green(lane: str) -> Path:
    """Where a lane's green record lives, read off the module at call time rather than captured, because the rebuild suite's own conftest redirects both constants under tmp_path so a test driving the cycle cannot leave a record in rebuild/out that the next real pass reads as proof."""
    return {"contracts": REBUILD_CONTRACTS_GREEN, "validators": REBUILD_VALIDATORS_GREEN}[lane]


def rebuild_lane_argv(lane: str) -> list[str]:
    """One lane of the rebuild suite. `--lane` is the rebuild conftest's own option, and it also decides the pool width: the contracts lane's `-n auto` resolves to the cores this process may actually run on, since none of its workers holds a live build artifact, while the validators lane takes the narrower width `rebuild/conftest.py` derives from what one live-fixture worker costs. Every run prints its twenty-five slowest tests, so the lane's own record says where its minutes went and a cost survey needs no special invocation."""
    return [
        "uv",
        "run",
        "pytest",
        "rebuild/",
        "--lane",
        lane,
        "-n",
        "auto",
        "--dist",
        "worksteal",
        "-q",
        "--tb=no",
        "-rfE",
        "--durations=25",
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
    """Whether a repo-relative path is provably outside gate:make-test's input closure. The exempt trees are safe because nothing the gate executes reads them: build_font globs glyph_data/*.yaml non-recursively (never glyph_data/runes/), and test/, site/, tools/ and conftest.py reference no rune file at all and reach into rebuild/ only where the root conftest imports three helpers inside its own callers — peak_rss for the summary line, memory_budget for the pool width, fingerprint for the font-path list — none of which can change what the suite asserts, and each of which the rebuild suite's own lanes gate; Markdown is never an input to any gate. bench-the-rebuild/ is measurement scaffolding that only ever reads the tree — nothing under test/, site/, tools/ or conftest.py imports it, and pytest never collects it (testpaths is test/ and site/)."""
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
        digest.update(f"{rel}\t{_sha256_path(root / rel)}\n".encode())
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
REBUILD_GATE_EXEMPT_PREFIXES = (
    "rebuild/evidence/",
    "rebuild/review/jstests/",
    "rebuild/review-census-pins.json",
)


def _sha256_path(path: Path) -> str:
    """The streamed read matters here more than anywhere: divergence-audit.tsv is hundreds of megabytes and rides the validators-lane key, which a driver pass recomputes three times. Spelled out rather than borrowing fingerprint.file_sha256 because this module defers every rebuild.pipeline import into the function that needs it, and this one is called per file in a loop."""
    try:
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
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


def oracle_cache_note(moved: str | None, root: Path = ROOT) -> str | None:
    """What the inputs a run_m1 skip-miss just named will cost the oracle's per-row verdict store, which is the other thing that note is predicting. The store invalidates at two grains and they look nothing alike in the timings: a rune file moves one family key and re-derives only the rows that can reach that letter, while anything in the comparison's own code closure — or any other input the whole-store stamp folds — drops every row of every configuration. Naming the second one is the point, because a zero-served oracle after a legitimate class-membership or pipeline edit is the expected outcome and would otherwise read as a broken cache. Both sides of the comparison are repo-relative labels, the form `moved_inputs_note` reports in: matched against basenames this answers nothing at all, and answers it silently. The rune verdict is withheld when the note was truncated, since the inputs it did not list could be anything."""
    if not moved:
        return None
    from rebuild.pipeline import fingerprint, oracle_cache

    def label(path: Path) -> str:
        try:
            return path.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            return path.name

    stamped = set(oracle_cache.ORACLE_ROW_CODE_PATHS)
    stamped |= {label(path) for path in oracle_cache.oracle_code_paths(root)}
    stamped |= {label(path) for path in oracle_cache.stamped_data_paths(root)}
    runes = {label(path) for path in fingerprint.rune_paths(root)}
    names = [entry.rsplit(" (", 1)[0] for entry in moved.split(", ")]
    whole_store = sorted({name for name in names if name in stamped})
    if whole_store:
        return f"the oracle row cache drops whole: {', '.join(whole_store)} is inside its stamp"
    if not moved.endswith(" more") and names and all(name in runes for name in names):
        return "the oracle row cache re-derives only the rows reaching those runes"
    return None


def m1_artifacts_present(root: Path = ROOT) -> bool:
    """Whether rebuild/out/m1 still holds everything a skipped run_m1 must leave behind: the three gate summaries and the artifacts the surface build consumes."""
    m1 = root / "rebuild" / "out" / "m1"
    names = [path.name for path in M1_SUMMARY_FILES.values()] + list(M1_ARTIFACT_NAMES)
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


def deep_sweep_skip_lines(root: Path = ROOT) -> list[str] | None:
    """The deep sweep's arming key: the behavior-class set the build enumerated (rebuild/out/m1/behavior_classes.json, written by emit_gsub.behavior_classes), the font-compilation code that turns a plan into bytes, and the shaper version the sweep shapes through. None when no build has left a sidecar to read, which is the caller's cue to run the cycle before asking whether the deep sweep is armed.

    Deliberately not the rune digests and not M1.otf's bytes: a rune edit moves both on every pass, and the deep sweep exists to sample HarfBuzz behavior at a depth the belt cannot reach. What it samples is the set of shapes the emitted lookup asks the shaper to handle, so an edit that mints no new shape leaves nothing for a deeper run to find, and its green legitimately survives. There is also no horizon line: the deep sweep is "5 or deeper", so the depth a green record proved rides in the record's payload and is compared with >=, where folding it into the key would make a horizon-6 green fail to satisfy a horizon-5 question.

    Each class is its own line label rather than a shared `class` label with the token as its value, so that the per-file map behind the key (`_files_of`, stored in the green record) holds one entry per token and `moved_inputs_note` can name the shape that appeared — which is the whole content of the "armed" report.
    """
    import importlib.metadata

    from rebuild.pipeline.emit_gsub import BEHAVIOR_CLASSES_FORMAT

    try:
        payload = json.loads((root / BEHAVIOR_CLASSES.relative_to(ROOT)).read_text())
    except OSError, ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("format") != BEHAVIOR_CLASSES_FORMAT:
        return None
    classes = payload.get("classes")
    if not isinstance(classes, list) or not all(isinstance(token, str) for token in classes):
        return None
    lines = [f"class:{token}\tpresent" for token in classes]
    lines += [f"{rel}\t{_sha256_path(root / rel)}" for rel in COMPILE_CODE_FILES]
    lines.append(f"uharfbuzz\t{importlib.metadata.version('uharfbuzz')}")
    return lines


def deep_sweep_skip_files(root: Path = ROOT) -> dict[str, str] | None:
    lines = deep_sweep_skip_lines(root)
    return None if lines is None else _files_of(lines)


def deep_sweep_skip_fingerprint(root: Path = ROOT) -> str | None:
    lines = deep_sweep_skip_lines(root)
    return None if lines is None else _digest_lines(lines)


def record_deep_sweep_green(
    fingerprint: str, horizon: int, files: dict[str, str] | None = None, path: Path | None = None
) -> None:
    """The deep sweep's last-green record. It carries the horizon the recorded run actually swept as well as the key, because the arming key is depth-blind on purpose: a green is a claim about a depth, and `deep_sweep_status` reads it back to answer whether an already-proved run went deep enough for the depth being asked about."""
    _record_outcome(
        path if path is not None else DEEP_SWEEP_GREEN,
        {"fingerprint": fingerprint, "horizon": horizon, "files": files},
    )


def deep_sweep_status(root: Path = ROOT, horizon: int = DEEP_SWEEP_HORIZON_DEFAULT) -> tuple[str, str]:
    """Whether the periodic deep sweep still stands for what the build now emits, as (status, note) for the cycle's one-line report. `current` means a green record matches the arming key at this depth or deeper; `armed` means something the deep sweep samples for has moved (a novel rule shape, the compilation path, the shaper) or the recorded run was shallower than asked, and `make conform-deep` is the remedy; `never-run` means no record at all; `unknown` means no build has left a behavior-class sidecar to key on. Reporting only — the deep sweep is never a cycle gate."""
    fingerprint = deep_sweep_skip_fingerprint(root)
    if fingerprint is None:
        return "unknown", "no behavior-class sidecar yet; it lands with the next M1 build"
    record = read_green_record(DEEP_SWEEP_GREEN)
    if record is None:
        return "never-run", "no deep sweep has been recorded; run `make conform-deep`"
    if record["fingerprint"] != fingerprint:
        files = deep_sweep_skip_files(root)
        moved = moved_inputs_note(record, files) if files is not None else None
        detail = f"{moved}; " if moved else ""
        return (
            "armed",
            f"{detail}the build emits shapes the last deep sweep never saw; run `make conform-deep`",
        )
    recorded = record.get("horizon")
    if not isinstance(recorded, int) or recorded < horizon:
        return (
            "armed",
            f"the recorded deep sweep reached horizon {recorded}, shallower than {horizon}; run `make conform-deep`",
        )
    return "current", f"horizon {recorded}"


def rebuild_gate_closure_files(root: Path) -> list[str] | None:
    """Every tracked or untracked-unignored file the rebuild pytest suite can read from the repo, and the shared half of both lanes' input closures: rebuild/ and glyph_data/ (minus Markdown, the carried-verdict evidence, the JS-only jstests, and the census pins) plus the root conftest.py, pyproject.toml, and uv.lock. The pins are out because the suite no longer reads them and the census step rewrites them mid-pass — they are the cycle's own diff artifact, so leaving them in would invalidate the key of every pass that refreshes them. None when git is unavailable, in which case the caller must run the gate unconditionally."""
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


def rebuild_lane_fingerprint(root: Path, lane: str) -> str | None:
    """Content key over one lane's full input closure, and the two closures are what make the lanes separately skippable. Contracts covers the repo files from rebuild_gate_closure_files plus the site fonts, which its shaping tests measure against and which are the frozen old font, unmoved by any rune edit; it deliberately contains no build artifact at all, so a verdict-only or artifact-only cycle re-runs nothing here, and a live M1 rebuild — which writes only under rebuild/out — cannot invalidate the key mid-pass. Validators adds exactly what that lane reads on top: the out/m1 artifacts, the oracle's subset tables, and the baselines. Both contain the rune files, prose-blind, because several contracts tests load the live spec. The verdict store is absent from both — the suite exercises it only through fixtures — which is what lets a verdict-only cycle skip the suite entirely. None when git is unavailable, in which case the caller must run the lane unconditionally."""
    from rebuild.pipeline import fingerprint

    files = rebuild_gate_closure_files(root)
    if files is None:
        return None
    lines = [f"{rel}\t{_closure_digest(root, rel)}" for rel in files]
    lines.append(f"fonts\t{fingerprint.hash_paths(root, fingerprint.font_paths(root))}")
    if lane == "validators":
        m1 = root / "rebuild" / "out" / "m1"
        lines += [f"m1/{name}\t{_sha256_path(m1 / name)}" for name in M1_ARTIFACT_NAMES]
        lines += [f"m1/{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
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
        shards = [part for meta in manifest["classes"] for part in unit_index.class_shards(meta)]
    except KeyError, TypeError, AttributeError:
        return False
    return all((surface / shard).exists() for shard in shards)


# The chain's own code, named module by module rather than as the whole of rebuild/tools/: the closure of rebuild.tools.verdict_chain (which runs every step) plus this driver (which builds its argv) and the two the driver imports to journal a run. rebuild/test_plumbing_closure.py walks the import graph from those entry points on every contracts run and fails if anything reachable in this repo is outside the union of this list, the manifest fingerprint's review_code and pipeline_code, and serve.py — so the list cannot go stale the way a hand-written one otherwise would, and hashing twenty-three unrelated tools to be safe is no longer the price of the guarantee.
PLUMBING_ENTRY_POINTS = ("rebuild.tools.verdict_chain", "rebuild.tools.artifact_cycle")
PLUMBING_TOOL_MODULES = (
    "artifact_cycle",
    "carry_verdicts",
    "complaint_docket",
    "cycle_timings",
    "echo_verdicts",
    "memory_budget",
    "merge_verdicts",
    "peak_rss",
    "review_docket",
    "standing_verdicts",
    "verdict_chain",
    "verdict_notes",
)


def plumbing_code_paths(root: Path = ROOT) -> list[Path]:
    return [Path(root) / "rebuild" / "tools" / f"{name}.py" for name in PLUMBING_TOOL_MODULES]


def plumbing_skip_fingerprint(
    root: Path = ROOT, surface: Path | None = None, master: Path | None = None
) -> str | None:
    """Content key over everything the verdict plumbing reads: the surface it resolves unit ids against, the verdicts master it carries forward, the live store it merges into, the checked-in standing approvals, and the chain's own code. Carry, merge, both fills with their merges, and the complaint docket are pure functions of exactly those, and the chain is idempotent once it has run — so a key matching the record a *complete* chain left behind proves re-running it would write nothing new. The master is in the key because it is the one input the autosave's hash cannot see: an export dropped at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code is in it for the same reason every sibling key carries its own stage's executable — a fix to a fill's matcher or to the carry's fallback must run rather than be skipped as proven — and it is the chain's real import closure (`plumbing_code_paths`, which a contracts test holds against the entry points' import graph) plus review/serve.py, which merge_verdicts reads the store through; review/'s other modules already ride inside the manifest fingerprint's review_code. None when the surface has no fingerprinted manifest or no master was resolved."""
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
        f"tools_code\t{fingerprint.hash_paths(root, plumbing_code_paths(root))}",
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
    """Which heavy gates this pass records pending instead of running. Two conditions, both necessary. The pass must be *refreshing* — run_m1 or the surface build has real work — because that is the pass whose whole point is to get the letters on screen, and a pass with no artifact work is the one that should be spending its time on verification instead. And the gate must be one that would otherwise run live, so a gate a green record already proved stays proved rather than being demoted to pending; without that, a review-UI edit (which restamps the surface but moves nothing the heavy gates read) would throw away greens it had no quarrel with. gate:js is never deferrable — it is one node process."""
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


def evaluate_run_m1_gate(pipeline: dict, manual_pins: dict, oracle: dict) -> GateOutcome:
    """Decide whether the M1 build passed from its three summary JSONs. run_m1's own exit code is not usable — it fails on any UNMATCHED oracle rows, always present mid-migration — so this reads defect_errors, the Manual-pin verdict, and multi_matched instead, and records UNMATCHED only as informational. The pin verdict is run_m1's own (`manual_pin_gate_failure`), scope included, so a gate that replayed nothing cannot pass here either."""
    from rebuild.pipeline.run_m1 import manual_pin_gate_failure

    failures: list[str] = []

    defect_errors = pipeline.get("defect_errors") or []
    if defect_errors:
        failures.append(f"{len(defect_errors)} defect-gate error(s): {defect_errors[0]}")

    pin_failure = manual_pin_gate_failure(manual_pins)
    if pin_failure is not None:
        failures.append(pin_failure)

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
    """Judge gate:conform from conform_summary.json's contents (None = the subprocess never wrote one). `pass` is the verdict, and the belt has exactly one way to fail: a font-vs-settle divergence, which is a compiler defect by definition. Whether the font holds every rule the build planned is read-back's claim, re-proved inside run_m1 on every build, and dead generated rules are rebuild/test_rule_witnesses.py's — neither reaches this summary."""
    if summary is None:
        return "FAILED (no conform_summary.json)", ["conform gate: run_m1 --conform-only wrote no summary"]
    failures: list[str] = []
    if summary.get("divergences"):
        failures.append(f"conform gate: {summary['divergences']} font-vs-settle divergence(s)")
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
    skip_contracts: bool = False
    contracts_note: str = ""
    skip_validators: bool = False
    validators_note: str = ""
    conform_note: str = ""
    conform_proven: bool = False
    skip_plumbing: bool = False
    plumbing_note: str = ""
    plumbing_carry_out: Path | None = None
    plumbing_store_only: bool = False
    deferred: frozenset[str] = frozenset()
    preserve_snapshot: Path | None = None
    record_greens: bool = False
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT
    surface_jobs: int = 1
    surface_reason: str = ""
    sweep_jobs: int = 1
    kernel_threads: int = 1
    make_test_workers: int = 1
    conform_jobs: int = 1
    conform_horizon: int = CONFORM_HORIZON_DEFAULT
    review_out: Path | None = None
    surface_dir: Path = REVIEW_OUT
    complaints_note: str = ""
    retention: bool = False
    steps: list[Step] = field(default_factory=list)

    def runs(self, name: str) -> bool:
        """Whether the named step has a command line to run at all — a step the plan skipped carries a note instead."""
        return any(step.name == name and step.argv is not None for step in self.steps)

    def argv(self, name: str) -> list[str]:
        """The named step's command line. build_plan is the only writer of step argvs and the executor runs exactly what the plan printed, so a step's command line can never fork between the plan and the run."""
        for step in self.steps:
            if step.name == name:
                if step.argv is None:
                    raise ValueError(f"plan step {name!r} runs nothing: {step.note}")
                return step.argv
        raise KeyError(name)


def jstest_argv() -> list[str]:
    """The JS suite argv. The *.test.js glob form is required — node v26 rejects the bare-directory form with 'Cannot find module' — and the glob is expanded in Python, never handed to a shell."""
    files = sorted(str(path.relative_to(ROOT)) for path in JSTEST_DIR.glob("*.test.js"))
    return ["node", "--test", *files]


@functools.cache
def _font_suite_worker_bytes() -> int:
    """What one of gate:make-test's pytest workers holds at its peak: the root conftest.py's FONT_SUITE_WORKER_BYTES, read out of that file's source rather than imported. There is no importable handle on it from here — pytest loads every conftest under the plain name `conftest`, so in any run collected under rebuild/, which is every run of the suite that tests this module, `sys.modules["conftest"]` is rebuild/conftest.py and a plain `import conftest` answers the wrong file, while `import rebuild.conftest` would execute a second copy of one pytest has already loaded and armed its lane-audit hook in. `ast` answers the question without executing anything, which also keeps a build tool from importing pytest and from inheriting that file's sys.path edits. The constant stays where it was put, beside the branch that prices it, and a rename of it fails loudly here rather than quietly costing the cycle its reservation. The path is this file's own tree rather than the module's ROOT, because what is wanted is the pytest that ships beside this code — a test pointing the cycle at an invented root is naming where a cycle's artifacts go, never whose test suite the gate would run."""
    tree = ast.parse((Path(__file__).resolve().parents[2] / "conftest.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "FONT_SUITE_WORKER_BYTES" for target in node.targets
        ):
            return int(ast.literal_eval(node.value))
    raise RuntimeError(
        "the root conftest.py defines no FONT_SUITE_WORKER_BYTES: the cycle prices gate:make-test's pytest pool from that constant, and it cannot reserve for a pool it cannot cost."
    )


def make_test_pool_width(*, ncores: int | None = None) -> int:
    """How wide gate:make-test's pytest pool will actually be — the number the cycle hands that child and the number it reserves for, derived once so the two cannot drift apart. Left alone the pool is `-n auto` and the root conftest.py answers it with the whole box, which is a pool sized as though nothing else were running beside a build sized as though it were; the cycle states MAKE_TEST_POOL_WORKERS instead, held down to the cores this process may actually run on — `memory_budget.usable_cores`' answer, the same count the root hook itself holds the pool to — so a one-core box states a pool of one. PYTEST_XDIST_AUTO_NUM_WORKERS wins ahead of all of that, and not as a courtesy: the child inherits this process's environment, so a width already stated here is the width that pool is going to take, and reserving by anything else would be reserving for a pool that is not the one about to start. It is read the way the root hook reads it, an unparseable value included — that value is fatal there too, and failing while the plan resolves costs a second, where failing inside the gate costs everything the cycle ran ahead of it."""
    from rebuild.tools import memory_budget

    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return max(1, int(stated))
    return max(1, min(MAKE_TEST_POOL_WORKERS, ncores or memory_budget.usable_cores()))


def kernel_threads_budget(
    *, skip_make_test: bool = False, ncores: int | None = None, total_bytes: int | None = None
) -> int:
    """The kernel fan-out's width for this cycle, named by the cycle rather than inherited silently, because it is the one width here that memory binds: a live configuration holds its whole working set until it emits, so the width is the box divided by one of them. What makes it the cycle's own rather than a re-export of `kernel_exec.KERNEL_THREADS_DEFAULT` is that a cycle is not a box to itself — gate:make-test's pytest pool is hot from t=0 and stays hot right across the table build — so that pool comes off the box before the division: FONT_SUITE_WORKER_BYTES apiece for as many workers as `make_test_pool_width` says it will have, which is the same figure the cycle hands the child, so what is reserved and what runs are one number by construction rather than two that happen to agree. A pass whose gate is skipped or deferred subtracts nothing, there being no pool to subtract, and --skip-gates is that same case with the caller saying so.

    The arithmetic underneath stays `kernel_exec.kernel_threads_default`'s: the reserve policy applied exactly once, and AMS_KERNEL_THREADS short-circuiting ahead of all of it, so a stated width wins here exactly as it does for a bare run_m1 and this reservation can never narrow one. What comes back is the memory answer before the configuration count and the cores this process may actually run on narrow it. That narrowing lives in exactly one place, `run_m1.build_tables`'s own `min()`, and is deliberately not repeated here: a second copy on this side would be a second thing to keep in agreement with it, and what not having one costs is only that a box roomier than the configuration count reads a plan line naming a width the run will go on to narrow. `ncores` and `total_bytes` are keywords for the reason every budget here takes its box as one — an assertion about a machine the suite is not running on has to be a pure function over an invented one.
    """
    from rebuild.pipeline.kernel_exec import kernel_threads_default

    coresident = 0 if skip_make_test else _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)
    return kernel_threads_default(coresident_bytes=coresident, total_bytes=total_bytes)


def sweep_job_budget(ncores: int | None = None) -> int:
    """The --jobs budget for the post-build sweeps — run_m1's Manual-pin/oracle shards and gate:conform's belt — which is one process per acceptance configuration and no more, because that is all `run_m1._spawn_pool` will start. This is a CPU budget, not a memory one: a sweep worker holds its shaper, its window memo and one config's rows, a fraction of a gigabyte, so a whole `ACCEPTANCE_CONFIGS`-wide pool of them fits beside anything else the cycle runs. run_m1's memory ceiling lives entirely in the table build, whose width is --kernel-threads and which these jobs never reach."""
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS
    from rebuild.tools import memory_budget

    return max(1, min(len(ACCEPTANCE_CONFIGS), ncores or memory_budget.usable_cores()))


def _surface_fit_terms(*, skip_gates: bool, skip_make_test: bool, ncores: int | None) -> tuple[int, int, int]:
    """The three arguments this width's arithmetic takes — the per-worker divisor, what comes off the box before the division, and the non-memory cap — derived once, so the width and the clause that explains it are two readings of one derivation rather than two derivations that happen to agree. `surface_job_budget` is `how_many_fit` over exactly this tuple and `surface_job_derivation` is `describe_fit` over it."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    coresident = SURFACE_PARENT_BYTES
    if not (skip_gates or skip_make_test):
        cores -= 2
        coresident += _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)
    return SURFACE_WORKER_BYTES, coresident, min(cores, SURFACE_JOBS_CAP)


def surface_job_budget(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """The --jobs budget for the review-surface build, and the third memory-derived fan-out in this tree: the box less its reserve less what the build holds flat, divided by what one more worker costs, under a cap that is the box's cores and SURFACE_JOBS_CAP together. The two halves are separate constants because this build's flat half is as large as its divided half — SURFACE_PARENT_BYTES is the parent, which holds the whole workload, every projection and state, and every unit's fragment from the moment phase 2 starts returning, none of it released before manifest+check, and which is there at any width — so it is subtracted before the division exactly as gate:make-test's pool is, rather than smeared through a divisor. SURFACE_WORKER_BYTES is the divisor: a worker's own interpreter and shapers, a baseline subset table for every configuration its slice reaches, and the slice's own retained units and fragments. The same number also sizes the signature pool `_resolve_signature_digests` starts, whose workers are one comparator apiece and an order of magnitude cheaper, so the surface worker is the binding unit and the one this prices.

    What the core clamp this replaces got wrong is worth writing down, because the evidence for it is still in the journal and still reads the same way. That argument was that the peak "barely moves with the width", from two `surface-build` step peaks — 13.25 GB at ten jobs against 13.77 GB at two. Both figures are true and neither is the build's footprint: `peak_rss.reap_peak_rss_bytes` maxes over a child's process tree instead of summing it, so a step peak is the widest single process under that step, and under this step that is the parent. A reading that can only ever see one process was flat in the width because the process it saw is flat in the width, and the pool beside it was never in the number at all. The 2026-08-27 full-fresh pass is where the gap became visible — 17.76 GB read at eight workers, against a per-term measurement of the same tree that put parent and workers together at roughly twice a 34 GB box — and this arithmetic is the shape those terms actually decompose into.

    One approximation is left, and it is stated rather than hidden: a worker's slice-shaped piles shrink as the pool widens, so no single divisor is true at every width — this one is seeded at width two, the narrowest pool the arithmetic ever starts and so the widest a worker ever gets, which makes it an upper bound at every pooled width and too steep at the wide end. Erring steep is the deliberate direction, because the two ends are not symmetric: a divisor seeded wide is what walked a width-two pool into this box's reserve, while one seeded narrow only hands a roomy box fewer workers than its true cost curve would allow. A box the pooled shape does not fit at all floors at one, which is not a refusal but the serial shape: at width one there is no pool, every fragment exists once instead of twice, and the build is the cheapest it can be on a box that has outgrown it. The lever that buys the width back is streaming phase 2 into shards instead of materializing every fragment in the parent, which WHATNEXT records; it is priced in SURFACE_PARENT_BYTES rather than in a width, so taking it widens this fan-out on every box at once.

    Under a gated cycle `make test`'s pytest pool is hot from t=0, so it comes off the box twice over: two cores out of the cap, as it always has, and FONT_SUITE_WORKER_BYTES apiece for as many workers as `make_test_pool_width` says it will have — the same figure the cycle hands that child, so what is reserved and what runs are one number by construction, the way `kernel_threads_budget` already does it. --skip-gates, the closure-unchanged auto-skip and deferral all give both back. gate:js runs from t=0 in every case, but it is a single node process, not a pool. `ncores` and `total_bytes` are keywords for the reason every budget here takes its box as one — an assertion about a machine the suite is not running on has to be a pure function over an invented one — and the cores come from `memory_budget.usable_cores()` rather than `os.cpu_count()`, so an affinity mask or a cgroup quota narrows this width the way it narrows every other one.
    """
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _surface_fit_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores
    )
    return memory_budget.how_many_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def surface_job_derivation(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """The same width said out loud, for the plan line and for the `--jobs` help — `memory_budget.describe_fit` over the terms `surface_job_budget` divides, so a reader surprised by a width can audit its derivation instead of trusting it."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _surface_fit_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores
    )
    return memory_budget.describe_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def build_plan(
    *,
    verdicts: Path | None,
    no_carry: bool,
    carry_out: Path | None,
    snapshot_dir: Path | None,
    skip_gates: bool,
    first_run: bool,
    short_id: str,
    no_merge: bool = False,
    skip_conform: bool = False,
    skip_make_test: bool = False,
    make_test_note: str = "",
    make_test_fingerprint: str | None = None,
    conform_horizon: int = CONFORM_HORIZON_DEFAULT,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    review_out: Path | None = None,
    ncores: int | None = None,
    total_bytes: int | None = None,
    skip_run_m1: bool = False,
    run_m1_note: str = "",
    run_m1_fingerprint: str | None = None,
    fresh: bool = False,
    skip_surface: bool = False,
    surface_note: str = "",
    skip_contracts: bool = False,
    contracts_note: str = "",
    skip_validators: bool = False,
    validators_note: str = "",
    conform_note: str = "",
    conform_proven: bool = False,
    skip_plumbing: bool = False,
    plumbing_note: str = "",
    plumbing_carry_out: Path | None = None,
    store_only: bool = False,
    deferred: frozenset[str] = frozenset(),
    preserve_snapshot: Path | None = None,
    record_greens: bool = False,
    keep_history: bool = False,
) -> Plan:
    resolved_snapshot = (
        snapshot_dir if snapshot_dir is not None else resolve_snapshot_dir(ROOT / "tmp", short_id)
    )
    do_carry = not no_carry and not first_run and not skip_plumbing and not store_only
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = (
            carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"
        )

    no_make_test = skip_gates or skip_make_test or "make-test" in deferred
    surface_no_pool = skip_make_test or "make-test" in deferred
    make_test_workers = make_test_pool_width(ncores=ncores)
    surface_jobs = surface_job_budget(
        skip_gates=skip_gates, skip_make_test=surface_no_pool, ncores=ncores, total_bytes=total_bytes
    )
    workers = f"{make_test_workers} worker" + ("" if make_test_workers == 1 else "s")
    if skip_gates:
        surface_head = "--skip-gates, so the surface build takes the whole box"
    elif skip_make_test:
        surface_head = "gate:make-test skipped, so the surface build takes the whole box"
    elif "make-test" in deferred:
        surface_head = "gate:make-test deferred, so the surface build takes the whole box"
    else:
        surface_head = f"gate:make-test's pytest pool held to {workers} — its cores reserved here and its bytes off the box beside the build's own parent"
    surface_reason = f"{surface_head}; " + surface_job_derivation(
        skip_gates=skip_gates, skip_make_test=surface_no_pool, ncores=ncores, total_bytes=total_bytes
    )
    sweep_jobs = sweep_job_budget(ncores)
    conform_jobs = sweep_jobs
    kernel_threads = kernel_threads_budget(
        skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    surface_dir = review_out if review_out is not None else REVIEW_OUT
    do_merge = (do_carry or store_only) and not no_merge and review_out is None
    do_retention = not keep_history and not first_run and review_out is None

    plan = Plan(
        short_id=short_id,
        first_run=first_run,
        snapshot_dir=resolved_snapshot,
        carry_out=resolved_carry_out,
        verdicts=verdicts,
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
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        skip_validators=skip_validators,
        validators_note=validators_note,
        conform_note=conform_note,
        conform_proven=conform_proven,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        plumbing_carry_out=plumbing_carry_out,
        plumbing_store_only=store_only,
        deferred=deferred,
        preserve_snapshot=preserve_snapshot,
        record_greens=record_greens,
        retention=do_retention,
        pool_policy=pool_policy,
        surface_jobs=surface_jobs,
        surface_reason=surface_reason,
        sweep_jobs=sweep_jobs,
        kernel_threads=kernel_threads,
        make_test_workers=make_test_workers,
        conform_jobs=conform_jobs,
        conform_horizon=conform_horizon,
        review_out=review_out,
        surface_dir=surface_dir,
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
    elif store_only:
        plan.steps.append(
            Step(
                "snapshot",
                None,
                "SKIPPED (the surface did not move, so there is no carry to feed and nothing to survive)",
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
        if sweep_jobs > 1:
            run_m1_argv += ["--jobs", str(sweep_jobs)]
        run_m1_argv += ["--kernel-threads", str(kernel_threads)]
        if fresh:
            run_m1_argv += ["--fresh-oracle-cache"]
        plan.steps.append(Step("run_m1", run_m1_argv, lane="build"))

    if skip_surface:
        plan.steps.append(Step("surface-build", None, f"SKIPPED ({surface_note})", lane="build"))
    else:
        surface_argv = ["uv", "run", "python", "-m", "rebuild.review.build", "--jobs", str(surface_jobs)]
        if review_out is not None:
            surface_argv += ["--out", str(review_out)]
        if fresh:
            surface_argv += ["--fresh-unit-cache"]
        plan.steps.append(Step("surface-build", surface_argv, lane="build"))

    if review_out is not None:
        plan.complaints_note = "rehearsal: reads the live autosave"
    elif first_run:
        plan.complaints_note = "first run: no verdicts to cluster"
    elif skip_plumbing:
        plan.complaints_note = plumbing_note
    elif not AUTOSAVE.exists():
        plan.complaints_note = "no verdicts store"

    if skip_plumbing:
        plumbing_step_note = f"SKIPPED ({plumbing_note})"
    elif first_run:
        plumbing_step_note = "SKIPPED (first run)"
    elif not do_carry and not store_only:
        plumbing_step_note = "SKIPPED (--no-carry)"
    else:
        plumbing_step_note = ""
    if plumbing_step_note:
        plan.steps.append(Step("plumbing", None, plumbing_step_note, lane="build"))
    else:
        plumbing_argv = [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.tools.verdict_chain",
            "--surface",
            str(surface_dir),
        ]
        if do_carry:
            assert resolved_carry_out is not None
            plumbing_argv += [
                "--source",
                str(resolved_snapshot),
                str(verdicts),
                "--carry-out",
                str(resolved_carry_out),
            ]
        else:
            plumbing_argv += ["--merge-master", str(verdicts)]
        if not do_merge:
            plumbing_argv += ["--no-merge"]
        if plan.complaints_note:
            plumbing_argv += ["--no-complaints"]
        if do_carry and not do_merge:
            note = (
                "carry only (rehearsal: the live autosave is never written)"
                if review_out is not None
                else "carry only (--no-merge)"
            )
        elif store_only:
            note = (
                "the surface did not move, so the carry is the identity: merge the master, then the fills "
                "and the docket"
            )
        else:
            note = "carry -> merge -> echo fill -> standing fill -> the fills' fixpoint -> complaint docket, in one process"
        plan.steps.append(Step("plumbing", plumbing_argv, note, lane="build"))

    if review_out is not None:
        plan.steps.append(
            Step(
                "census",
                None,
                "SKIPPED (rehearsal: the checked-in pins track the live surface)",
                lane="build",
            )
        )
    else:
        plan.steps.append(
            Step(
                "census",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "rebuild.review.census",
                    "--update",
                    "--surface",
                    str(REVIEW_OUT),
                ],
                "then `git diff -- rebuild/review-census-pins.json`, printed in full — the pins are the last accepted census; review the diff at commit time",
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
        if skip_contracts:
            plan.steps.append(
                Step("gate:rebuild-contracts", None, f"SKIPPED ({contracts_note})", lane="contracts")
            )
        elif "rebuild-contracts" in deferred:
            plan.steps.append(
                Step("gate:rebuild-contracts", None, f"DEFERRED ({DEFER_NOTE})", lane="contracts")
            )
        else:
            plan.steps.append(
                Step(
                    "gate:rebuild-contracts",
                    rebuild_lane_argv("contracts"),
                    "submitted once the surface build settles; queued ahead of the validators lane",
                    lane="contracts",
                )
            )
        if skip_validators:
            plan.steps.append(
                Step("gate:rebuild-validators", None, f"SKIPPED ({validators_note})", lane="validators")
            )
        elif "rebuild-validators" in deferred:
            plan.steps.append(
                Step("gate:rebuild-validators", None, f"DEFERRED ({DEFER_NOTE})", lane="validators")
            )
        else:
            plan.steps.append(
                Step(
                    "gate:rebuild-validators",
                    rebuild_lane_argv("validators"),
                    "submitted once the surface build settles",
                    lane="validators",
                )
            )
        if skip_make_test:
            plan.steps.append(Step("gate:make-test", None, f"SKIPPED ({make_test_note})", lane="t0"))
        elif "make-test" in deferred:
            plan.steps.append(Step("gate:make-test", None, f"DEFERRED ({DEFER_NOTE})", lane="t0"))
        else:
            plan.steps.append(Step("gate:make-test", ["make", "test"], lane="t0"))

    plan.steps.append(
        Step(
            "job-costs",
            ["uv", "run", "python", "-m", "rebuild.tools.calibrate_budgets", "--check"],
            "the checked-in per-unit peaks against what this box measured, once the gates have joined and this pass's own pool records are in the journal — a file read; committing a re-seeded constant is the acceptance, exactly as the census pins work",
        )
    )

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
            f"    Lane build only; no gates; run_m1 sweeps --jobs {plan.sweep_jobs} at --kernel-threads {plan.kernel_threads}, surface-build --jobs {plan.surface_jobs} ({plan.surface_reason})",
        ]
    defer_contracts = "rebuild-contracts" in plan.deferred
    defer_validators = "rebuild-validators" in plan.deferred
    defer_conform = "conform" in plan.deferred
    defer_make_test = "make-test" in plan.deferred
    no_make_test = plan.skip_make_test or defer_make_test
    no_conform = plan.skip_conform or defer_conform
    no_contracts = plan.skip_contracts or defer_contracts
    t0_lane = "gate:js" if no_make_test else "gate:js, gate:make-test"
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        f"    Lane t0   [from t=0, background]  : {t0_lane}",
        "    Lane build[serial, main thread]  : snapshot -> run_m1 -> surface-build -> submit gate:rebuild-contracts, gate:rebuild-validators -> plumbing -> census",
    ]
    if plan.skip_conform:
        lines.append("    Lane conform                     : SKIPPED (--skip-conform)")
    elif defer_conform:
        lines.append(f"    Lane conform                     : DEFERRED ({DEFER_NOTE})")
    elif plan.pool_policy == "overlap":
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; CO-RESIDENT with the pytest pools (--jobs {plan.conform_jobs})"
        )
    elif not no_make_test:
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; QUEUED behind gate:make-test (queue policy — one heavy pool at a time) (--jobs {plan.conform_jobs})"
        )
    else:
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; gate:make-test not running, so no queueing (--jobs {plan.conform_jobs})"
        )
    if plan.skip_contracts:
        lines.append(
            "    Lane rebuild-contracts           : SKIPPED (inputs unchanged since its last green run)"
        )
    elif defer_contracts:
        lines.append(f"    Lane rebuild-contracts           : DEFERRED ({DEFER_NOTE})")
    else:
        lines.append("    Lane rebuild-contracts           : submitted once the surface build settles;")
        if plan.pool_policy == "overlap":
            lines.append(
                "                                       CO-RESIDENT with the other pools (overlap policy)"
            )
        elif not no_conform:
            lines.append(
                "                                       QUEUED behind gate:conform (queue policy — one heavy pool at a time)"
            )
        elif not no_make_test:
            lines.append(
                "                                       QUEUED behind gate:make-test (queue policy; gate:conform not running)"
            )
        else:
            lines.append("                                       no other heavy pool running, so no queueing")
    if plan.skip_validators:
        lines.append(
            "    Lane rebuild-validators          : SKIPPED (inputs unchanged since its last green run)"
        )
    elif defer_validators:
        lines.append(f"    Lane rebuild-validators          : DEFERRED ({DEFER_NOTE})")
    else:
        lines.append("    Lane rebuild-validators          : submitted once the surface build settles;")
        if plan.pool_policy == "overlap":
            lines.append(
                "                                       CO-RESIDENT with the other pools (overlap policy)"
            )
        elif not no_contracts:
            lines.append(
                "                                       QUEUED behind gate:rebuild-contracts, whose chain already waits on gate:conform and gate:make-test"
            )
        elif not no_conform:
            lines.append(
                "                                       QUEUED behind gate:conform (queue policy; the contracts lane is not running)"
            )
        elif not no_make_test:
            lines.append(
                "                                       QUEUED behind gate:make-test (queue policy; neither gate:conform nor the contracts lane is running)"
            )
        else:
            lines.append("                                       no other heavy pool running, so no queueing")
    workers = f"{plan.make_test_workers} worker" + ("" if plan.make_test_workers == 1 else "s")
    if no_make_test:
        kernel_reason = "the table build's memory ceiling, the one width RAM binds"
    else:
        kernel_reason = f"the table build's memory ceiling, less gate:make-test's {workers}"
    lines.append(
        f"    run_m1 sweeps --jobs             : {plan.sweep_jobs}  (one process per acceptance configuration)"
    )
    lines.append(f"    run_m1 --kernel-threads          : {plan.kernel_threads}  ({kernel_reason})")
    lines.append(f"    surface-build --jobs             : {plan.surface_jobs}  ({plan.surface_reason})")
    pending = ["gate:" + name for name in sorted(plan.deferred)]
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
    """The pass's running record. Every `*_status` string here is display-only prose for the summary — it exists to be read by a human, and its wording is free to change. The booleans beside them (`gate_*_green`, `complaints_ok`) are the machine judgment, set at the moment the outcome is judged and read by every decision that follows; greenness is never re-derived from the status strings. A gate that never joined — skipped, deferred, or never submitted — leaves its boolean None, which is neither green nor red."""

    snapshot_dir: Path | None = None
    unmatched: int | None = None
    multi_matched: int | None = None
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
    plumbing_fixpoint: bool = False
    census_status: str = "not run"
    job_costs_status: str = "not run"
    job_costs_ok: bool | None = None
    complaints_status: str = "not run"
    complaints_ok: bool | None = None
    gate_js: str = "not run"
    gate_js_green: bool | None = None
    gate_contracts: str = "not run"
    gate_contracts_green: bool | None = None
    gate_validators: str = "not run"
    gate_validators_green: bool | None = None
    gate_conform: str = "not run"
    gate_conform_green: bool | None = None
    gate_make_test: str = "not run"
    gate_make_test_green: bool | None = None
    contracts_recordable: bool = False
    validators_recordable: bool = False
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
        """Track a live child. Returns False once terminate_all has torn the registry down, so a worker that unblocks after a KeyboardInterrupt (the queue-mode gate tasks parked on an earlier gate's future — conform on make-test, the rebuild lanes on conform and on each other — are the case) never leaves a fresh subprocess untracked — the caller reaps it instead of spawning an orphaned pytest army."""
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
    peak_rss_bytes: int | None = None


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
    name: str,
    argv: list[str],
    *,
    emit: _Emitter,
    registry: _ChildRegistry,
    stream: bool,
    env: dict[str, str] | None = None,
) -> _StepResult:
    """One child, run to completion with both pipes drained. `env` is what this process's environment is overlaid with for this child alone, and its default of None is the inheritance every other step wants; a step that states one gets that copy and nothing else in the cycle sees it."""
    if registry.closed:
        return _StepResult(name, 130, "", "", 0.0)
    emit.emit(f"\n$ {' '.join(argv)}")
    start = time.perf_counter()
    proc = subprocess.Popen(
        argv,
        cwd=ROOT,
        env=None if env is None else {**os.environ, **env},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
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
    peak_rss = reap_peak_rss_bytes(proc)
    returncode = proc.wait()
    registry.remove(proc)
    elapsed = time.perf_counter() - start
    rss_suffix = "" if peak_rss is None else f" {rss_token(peak_rss)}"
    emit.emit(f"[t] {name} {elapsed:.1f}s{rss_suffix}")
    return _StepResult(name, returncode, "\n".join(out_buf), "\n".join(err_buf), elapsed, peak_rss)


def _dump_captured(emit: _Emitter, result: _StepResult) -> None:
    lines: list[str] = []
    if result.stdout:
        lines.extend(result.stdout.splitlines())
    if result.stderr:
        lines.extend(result.stderr.splitlines())
    if lines:
        emit.emit_block(lines)


@dataclass
class RebuildOutcome:
    status: str
    failures: list[str]
    hard_ids: list[str]
    recordable: bool = False


_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _rebuild_verdict(hard: list[str]) -> RebuildOutcome:
    failures: list[str] = []
    if hard:
        status = f"FAILED ({len(hard)} unexplained)"
        failures.append(f"rebuild suite: {len(hard)} unexplained failure(s)")
    else:
        status = "green"
    return RebuildOutcome(status=status, failures=failures, hard_ids=list(hard), recordable=not hard)


def classify_rebuild_output(stdout: str, returncode: int) -> RebuildOutcome:
    """Turn the rebuild suite's FAILED/ERROR summary lines into a gate verdict — the one judgment of the suite's output, lane-blind and so shared by both of the cycle's rebuild gates and by the interactive wrapper (rebuild.tools.rebuild_gate). pytest emits ANSI color whenever FORCE_COLOR is set (as it is under the agent harness), wrapping each summary line in escape codes, so strip those first — otherwise no line begins with a literal "FAILED "/"ERROR " and a colored run reports the exit-code placeholder instead of naming its failures. Every failure is unexplained by definition — the suite carries no documented-baseline amnesty — and every green is recordable."""
    lines = [_ANSI_SGR.sub("", line) for line in stdout.splitlines()]
    failed_ids = [line.split(None, 2)[1] for line in lines if line.startswith("FAILED ")]
    error_ids = [line.split(None, 2)[1] for line in lines if line.startswith("ERROR ")]
    hard = failed_ids + error_ids
    if returncode != 0 and not hard:
        hard.append(f"pytest exited {returncode} with no parsed FAILED/ERROR lines")
    return _rebuild_verdict(hard)


def _do_run_m1(
    report: CycleReport,
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str] | None = None,
    skip: bool = False,
    skip_note: str = "",
    record: bool = False,
    fingerprint: str | None = None,
) -> GateOutcome | None:
    """Run (or, when `skip` is set, reuse) the M1 build and judge its gate from the three summary JSONs. The skip path leaves rebuild/out/m1 untouched and re-evaluates the recorded summaries, which is sound because run_m1's outputs are deterministic and timestamp-free over the fingerprinted inputs. A live green records the fingerprint only if it still matches — an input edited mid-run means the tested content is no longer on disk — and a live red matching the record deletes it."""
    if skip:
        emit.emit(f"\nrun_m1: SKIPPED — {skip_note}; evaluating the gate from the recorded summaries.")
    else:
        for path in M1_SUMMARY_FILES.values():
            path.unlink(missing_ok=True)
        spawn("run_m1", argv, emit=emit, registry=registry, stream=True)
    missing = [name for name, path in M1_SUMMARY_FILES.items() if not path.exists()]
    if missing:
        for name in missing:
            emit.emit(
                f"run_m1 gate failure: missing {name} summary ({M1_SUMMARY_FILES[name]}) — run_m1 did not complete"
            )
        return None
    summaries = {name: _load_summary(path) for name, path in M1_SUMMARY_FILES.items()}
    gate = evaluate_run_m1_gate(summaries["pipeline"], summaries["manual_pins"], summaries["oracle"])
    report.unmatched = gate.unmatched
    report.multi_matched = gate.multi_matched
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
        return ["run_m1 did not write all three summary files"]
    return list(gate.failures)


def _read_surface_totals(report: CycleReport, surface_dir: Path) -> bool:
    try:
        manifest = json.loads((surface_dir / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    totals = manifest.get("totals") or {}
    report.surface_units = totals.get("units")
    report.surface_rows = totals.get("rows")
    report.surface_batches = totals.get("batches")
    report.echo_groups = totals.get("echo_groups")
    return True


def _do_surface_build(
    report: CycleReport,
    *,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    review_out: Path | None,
    argv: list[str] | None = None,
    skip: bool = False,
    skip_note: str = "",
) -> bool:
    """Rebuild (or, when `skip` is set, reuse) the review surface. Both paths take the four totals from the surface's own manifest.json — review.build's validated output, whose integer totals build.check_manifest enforces — rather than scraping them back out of the build's stderr, so the numbers the summary reports are the ones the surface on disk actually carries."""
    surface_dir = review_out if review_out is not None else REVIEW_OUT
    if skip:
        if not _read_surface_totals(report, surface_dir):
            emit.emit("ERROR: surface-build skip: the manifest vanished mid-cycle; rerun with --fresh.")
            return False
        emit.emit(f"\nsurface-build: SKIPPED — {skip_note}.")
        return True
    result = spawn("surface-build", argv, emit=emit, registry=registry, stream=True)
    if result.returncode != 0:
        emit.emit(f"ERROR: review.build exited {result.returncode}.")
        return False
    if not _read_surface_totals(report, surface_dir):
        emit.emit("ERROR: review.build exited 0 but left no readable manifest.json.")
        return False
    return True


_PLUMBING_FAILURES = {
    "carry": "carry_verdicts failed",
    "merge": "verdict merge failed",
    "echo-fill": "echo-fill failed",
    "echo-merge": "echo-merge failed",
    "standing-fill": "standing-fill failed",
    "standing-merge": "standing-merge failed",
}


def plumbing_sections(text: str) -> dict[str, list[str]]:
    """The chain's output split at its `[chain] <step>` banners. One subprocess prints what seven used to, and this is what lets the summary keep a line per step: the driver reads each step's own lines out of the stream rather than out of its own process table. Later rounds of the echo pass fold into the first round's section, since they are the same step of the cascade run again."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith(CHAIN_BANNER):
            name = line[len(CHAIN_BANNER) :].strip()
            if name.startswith(("fixpoint:", "failed:")):
                current = None
                continue
            current = re.sub(r"-\d+$", "", name)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _scrape(lines: list[str], keep) -> list[str]:
    return [line.strip() for line in lines if keep(line.strip())]


def _do_plumbing(
    report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan
) -> list[str]:
    """Run the whole verdict chain as one child and rebuild the per-step report from its output. Returns the failure messages the cycle should carry, which are the same ones the seven separate steps used to append."""
    result = spawn("plumbing", plan.argv("plumbing"), emit=emit, registry=registry, stream=False)
    _dump_captured(emit, result)
    report.carry_out = plan.carry_out if plan.carry_out is not None else plan.plumbing_carry_out
    sections = plumbing_sections(result.stdout)
    failed = ""
    for line in result.stdout.splitlines():
        if line.startswith(CHAIN_FAILED):
            failed = line[len(CHAIN_FAILED) :].split(" ", 1)[0]
            failed = re.sub(r"-\d+$", "", failed)
    report.plumbing_fixpoint = any(
        line.startswith(CHAIN_FIXPOINT + "witnessed") for line in result.stdout.splitlines()
    )

    report.carry_lines = _scrape(
        sections.get("carry", []),
        lambda line: any(word in line for word in ("carried", "kinds", "queue", "fallback")),
    )
    for name in ("merge", "echo-merge", "standing-merge"):
        setattr(
            report,
            name.replace("-", "_") + "_lines",
            _scrape(
                sections.get(name, []),
                lambda line: line.startswith(("merged ", "nothing changed", "stashed ")),
            ),
        )
    report.echo_fill_lines = _scrape(
        sections.get("echo-fill", []),
        lambda line: line.startswith("wrote ") and "echo-fill verdicts" in line,
    )
    report.standing_fill_lines = _scrape(
        sections.get("standing-fill", []),
        lambda line: (line.startswith("wrote ") and "standing-approval verdicts" in line)
        or line.endswith("held for review by except_left"),
    )

    # A step at or after the one that failed either is it or never ran; a step before it ran, and says what it did.
    done = (
        ("merge", "merged"),
        ("echo-fill", "filled"),
        ("echo-merge", "merged"),
        ("standing-fill", "filled"),
        ("standing-merge", "merged"),
    )
    order = ["carry", *(name for name, _word in done)]
    blocked = order.index(failed) if failed in order else len(order)
    for name, word in done:
        if order.index(name) > blocked:
            status = f"not run ({failed} failed)"
        elif name == failed:
            status = f"FAILED (exit {result.returncode})"
        elif name in sections:
            status = word
        else:
            status = "not run"
        setattr(report, name.replace("-", "_") + "_status", status)
    failures: list[str] = []
    if failed in _PLUMBING_FAILURES:
        failures.append(_PLUMBING_FAILURES[failed])
    elif result.returncode != 0 and failed != "complaints":
        failures.append(f"the verdict chain failed (exit {result.returncode})")

    if "complaints" in sections:
        _read_complaints(report, sections["complaints"], result.returncode if failed == "complaints" else 0)
    return failures


def _read_complaints(report: CycleReport, lines: list[str], returncode: int) -> None:
    if returncode != 0:
        report.complaints_status = f"FAILED (exit {returncode}) — informational"
        report.complaints_ok = False
        return
    report.complaints_ok = True
    for line in lines:
        stripped = line.strip()
        if stripped == "no open complaints":
            report.complaints_status = stripped
            return
        if stripped.startswith("wrote ") and ": " in stripped:
            report.complaints_status = stripped.split(": ", 1)[1]
            return
    report.complaints_status = "done"


def _do_census(report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan) -> None:
    """Rewrite the census pins from the surface's census-facts.json sidecar and print their git diff. The checked-in pins are the last accepted census, so that diff is exactly what a commit would be accepting: volatile totals that move with every letter added or reshaped, and an invariant block whose movement deserves a closer look. Nothing here gates — the step records no green and never fails the cycle — and a refresh that fails (a surface predating the sidecar, say) is reported and left alone, since the next pass that rebuilds the surface heals it."""
    census = spawn("census", plan.argv("census"), emit=emit, registry=registry, stream=False)
    _dump_captured(emit, census)
    if census.returncode != 0:
        report.census_status = f"update FAILED (exit {census.returncode}) — informational"
        return
    diff = spawn(
        "git-diff",
        ["git", "diff", "--", "rebuild/review-census-pins.json"],
        emit=emit,
        registry=registry,
        stream=False,
    )
    _dump_captured(emit, diff)
    if diff.stdout.strip():
        report.census_status = (
            "updated (diff vs the last accepted census shown above — review it at commit time)"
        )
    else:
        report.census_status = "updated (matches the last accepted census)"


def _do_job_costs(
    report: CycleReport, *, spawn, emit: _Emitter, registry: _ChildRegistry, plan: Plan
) -> None:
    """Hold the checked-in per-unit peaks against what this box has actually measured. Several widths in this tree are the box divided by one of those constants, and the constants are only ever as true as the last measurement anybody compared them to — so the cycle compares them, on a journal this pass's own pools have just appended to, which is why the step sits after the gate join rather than beside the census.

    Nothing here gates, and that is deliberate rather than an oversight: a divisor that has gone stale makes a pool the wrong width, which costs wall time or swap, but it cannot make an artifact wrong — so it must never red a pass whose artifacts are green. The loudness is the summary line and `job_costs_ok`, and the acceptance is a human's commit of the re-seeded constant, exactly as the census pins are accepted by committing their diff. A tool that cannot run at all is reported as informational too: a broken check is the check's problem, and the cycle has nothing to say about the constants either way.

    The diff is conditional where the census's is unconditional, because the two files are nothing alike. rebuild/review-census-pins.json exists only to hold the census, so its whole diff is the acceptance and printing it every pass costs a reader nothing. The four files that hold these constants hold a great deal besides them, so an unconditional diff would print unrelated work on every pass and train a reader to skip the one pass where it mattered. When the check trips it answers the single question worth asking then: has the constant already been re-seeded in this working tree, so the commit in hand is already the acceptance?
    """
    check = spawn("job-costs", plan.argv("job-costs"), emit=emit, registry=registry, stream=False)
    _dump_captured(emit, check)
    if check.returncode == 0:
        report.job_costs_status = "checked (every measured unit's peak fits its checked-in constant)"
        report.job_costs_ok = True
        return
    if check.returncode != 1:
        report.job_costs_status = f"check FAILED (exit {check.returncode}) — informational"
        report.job_costs_ok = None
        return
    diff = spawn(
        "job-costs-diff",
        [
            "git",
            "diff",
            "--",
            "conftest.py",
            "rebuild/conftest.py",
            "rebuild/pipeline/kernel_exec.py",
            "rebuild/tools/artifact_cycle.py",
        ],
        emit=emit,
        registry=registry,
        stream=False,
    )
    _dump_captured(emit, diff)
    status = (
        "OVERRUN (a measured peak outruns its checked-in constant — see above; re-seed the constant and "
        "commit it, and that commit is the acceptance)"
    )
    if diff.stdout.strip():
        status += " — a constant has already moved in the working tree"
    report.job_costs_status = status
    report.job_costs_ok = False


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


def _gate_js_task(argv: list[str], spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:js", argv, emit=emit, registry=registry, stream=False)


def _gate_make_test_task(argv: list[str], spawn, emit: _Emitter, registry: _ChildRegistry) -> _StepResult:
    return spawn("gate:make-test", argv, emit=emit, registry=registry, stream=True)


def _spawn_with_env(spawn, env: dict[str, str]):
    """One child's environment, carried on that child's own spawn callable rather than added to the argument list every gate task shares. The alternative is os.environ, and it is the wrong one: run_m1, the surface build and both rebuild lanes spawn from this same process, so a width set there for gate:make-test would pin their `-n auto` pools to it too — the contracts lane wants the whole box and the validators lane its own narrower answer. Wrapping instead of widening the protocol also leaves the task signature alone, which is what keeps the plan the only writer of what a child runs: this adds to the child's environment, never to its argv."""

    def spawn_with_env(name, argv, *, emit, registry, stream):
        return spawn(name, argv, emit=emit, registry=registry, stream=stream, env=env)

    return spawn_with_env


def _gate_conform_task(
    pool_policy: str,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> tuple[str, list[str]]:
    """gate:conform shapes the exhaustive font-vs-settle sweep against the fresh M1.otf via run_m1 --conform-only. Under the queue policy it queues behind gate:make-test, and both rebuild lanes in turn park behind this sweep, so only one heavy pool is ever hot: co-resident, two heavy pools oversubscribe the box roughly 2:1, and measured that contention roughly tripled the rebuild suite's wall time — a worse critical path than the same work in sequence. Conform runs ahead of the rebuild lanes in the chain because the sweep needs only the fresh M1.otf, while their submission waits on the surface build settling. The stale conform_summary.json is unlinked here, just before the sweep spawns, so the verdict can only come from this cycle's subprocess (an auto-skipped gate never runs this task and never reads the file)."""
    CONFORM_SUMMARY.unlink(missing_ok=True)
    if pool_policy == "queue":
        _await_gate_futures(make_fut)
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


def _await_gate_futures(*futures: Future | None) -> None:
    """Park until each named gate has finished, caring only that it is done and never how it went — a gate that raised is the joiner's problem, and a queued lane still gets its turn at the box."""
    for fut in futures:
        if fut is not None:
            try:
                fut.result()
            except Exception:
                pass


def _gate_contracts_task(
    pool_policy: str,
    conform_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> RebuildOutcome:
    """The rebuild suite's contracts lane — every test whose fixture closure holds no live build artifact, run at the box's full xdist width. It reads nothing the build lane writes, yet it is still submitted once the surface build settles, for two reasons that have nothing to do with correctness: a full-width pool must not share the box with the M1 build or the surface build, whose peaks are what the repo's parallelism defaults are sized against, and waiting costs it nothing, since under the queue policy it parks behind conform anyway and on the common gate pass every stage upstream auto-skips, so it starts at t=0 regardless. Under the queue policy it parks at the tail of the make-test -> conform chain so only one heavy pool is hot at a time, and it goes ahead of the validators lane because it is the short one and fails fast on a code error before the half-hour lane starts."""
    if pool_policy == "queue":
        _await_gate_futures(conform_fut, make_fut)
    result = spawn("gate:rebuild-contracts", argv, emit=emit, registry=registry, stream=False)
    return classify_rebuild_output(result.stdout, result.returncode)


def _gate_validators_task(
    pool_policy: str,
    conform_fut: Future | None,
    contracts_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: _Emitter,
    registry: _ChildRegistry,
    argv: list[str],
) -> RebuildOutcome:
    """The rebuild suite's validators lane — the tests that read rebuild/out, the review surface and the fixture caches, at the narrower width rebuild/conftest.py derives from what one of them costs, because each of them carries a live fixture's working set. Submitted once the surface build settles, which for this lane is a correctness requirement rather than a courtesy: its session fixture reads the live surface whenever surface_build_skippable calls it provably fresh, so a lane started against one mid-rewrite would either observe a fresh manifest beside a sidecar review.build has not written yet, or decide the surface is not fresh and waste a whole duplicate build inside the suite. Nothing later in the build lane is an input to it, the census pins included. Under the queue policy it parks at the tail of the whole chain, contracts included."""
    if pool_policy == "queue":
        _await_gate_futures(conform_fut, contracts_fut, make_fut)
    result = spawn("gate:rebuild-validators", argv, emit=emit, registry=registry, stream=False)
    return classify_rebuild_output(result.stdout, result.returncode)


def _gate_result(fut: Future, name: str, failures: list[str]):
    try:
        return fut.result()
    except Exception as exc:
        failures.append(f"{name} raised: {exc!r}")
        return None


def _join_rebuild_lane(
    report: CycleReport,
    failures: list[str],
    fut: Future,
    lane: str,
    emit: _Emitter,
) -> None:
    """Fold one lane's outcome into the report. The classifier is lane-blind, so the only per-lane thing here is which three fields the verdict lands in."""
    outcome = _gate_result(fut, f"gate:rebuild-{lane}", failures)
    if outcome is None:
        status, green, recordable = "FAILED (exception)", False, False
    else:
        status, green, recordable = outcome.status, not outcome.failures, outcome.recordable
        for test_id in outcome.hard_ids:
            emit.emit(f"  hard rebuild failure ({lane}): {test_id}")
        failures.extend(outcome.failures)
    if lane == "contracts":
        report.gate_contracts, report.gate_contracts_green = status, green
        report.contracts_recordable = recordable
    else:
        report.gate_validators, report.gate_validators_green = status, green
        report.validators_recordable = recordable


def _join_gates(
    report: CycleReport,
    failures: list[str],
    js_fut: Future | None,
    contracts_fut: Future | None,
    validators_fut: Future | None,
    conform_fut: Future | None,
    make_fut: Future | None,
    emit: _Emitter,
) -> None:
    if js_fut is not None:
        js = _gate_result(js_fut, "gate:js", failures)
        if js is None:
            report.gate_js = "FAILED (exception)"
            report.gate_js_green = False
        else:
            report.gate_js_green = js.returncode == 0
            report.gate_js = "green" if js.returncode == 0 else f"FAILED (exit {js.returncode})"
            if js.returncode != 0:
                failures.append("JS suite failed")
    if contracts_fut is not None:
        _join_rebuild_lane(report, failures, contracts_fut, "contracts", emit)
    if validators_fut is not None:
        _join_rebuild_lane(report, failures, validators_fut, "validators", emit)
    if conform_fut is not None:
        conform = _gate_result(conform_fut, "gate:conform", failures)
        if conform is None:
            report.gate_conform = "FAILED (exception)"
            report.gate_conform_green = False
        else:
            status, conform_failures = conform
            report.gate_conform = status
            report.gate_conform_green = not conform_failures
            failures.extend(conform_failures)
    if make_fut is not None:
        make = _gate_result(make_fut, "gate:make-test", failures)
        if make is None:
            report.gate_make_test = "FAILED (exception)"
            report.gate_make_test_green = False
        else:
            report.gate_make_test_green = make.returncode == 0
            report.gate_make_test = "green" if make.returncode == 0 else f"FAILED (exit {make.returncode})"
            if make.returncode != 0:
                failures.append("make test failed")


def _plumbing_settled(report: CycleReport) -> bool:
    """Whether the chain closed at a fixpoint, which is what the plumbing green claims. It used to be inferred from the standing merge writing nothing — a standing fill landing on one unit can make its echo group unanimous and leave a blank sibling that only the next pass's echo fill would take, so a pass whose fills landed had to hand the cascade on. Holding the index in one process makes another echo pass cost a second, so the chain runs the cascade to a standstill itself and says so: the green now rests on a witnessed re-run that wrote nothing rather than on an ordering argument."""
    return report.plumbing_fixpoint


def _record_gate_greens(report: CycleReport, plan: Plan, gate_keys: dict[str, str], emit: _Emitter) -> None:
    """Persist the concurrent gates' green records after they joined. gate:conform's key is snapshotted right after run_m1 finished; both rebuild lanes' keys right after the surface build settles, which is where those gates are submitted — and the census pins are exempt from the rebuild closure, so the refresh later in the pass cannot invalidate either key. Each is recomputed here before recording, so a source file edited while the gates ran — content the gates never tested — can never be recorded green. A red gate whose key still matches its record deletes the falsified record."""
    key = gate_keys.get("conform")
    if key:
        if report.gate_conform_green is True:
            if conform_skip_fingerprint(ROOT, plan.conform_horizon) == key:
                record_green(CONFORM_GREEN, key, files=conform_skip_files(ROOT, plan.conform_horizon))
            else:
                emit.emit(
                    "gate:conform green, but its inputs changed while the cycle ran — green not recorded"
                )
        elif report.gate_conform_green is False:
            clear_contradicted_green(CONFORM_GREEN, key)
    for lane, recordable, green in (
        ("contracts", report.contracts_recordable, report.gate_contracts_green),
        ("validators", report.validators_recordable, report.gate_validators_green),
    ):
        key = gate_keys.get(lane)
        if not key:
            continue
        record = rebuild_lane_green(lane)
        if recordable:
            if rebuild_lane_fingerprint(ROOT, lane) == key:
                record_green(record, key)
            else:
                emit.emit(
                    f"gate:rebuild-{lane} green, but its input closure changed while the cycle ran — green not recorded"
                )
        elif green is False:
            clear_contradicted_green(record, key)


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
        defer_contracts = "rebuild-contracts" in plan.deferred
        defer_validators = "rebuild-validators" in plan.deferred
        defer_conform = "conform" in plan.deferred
        defer_make_test = "make-test" in plan.deferred
        js_fut = (
            None
            if plan.skip_gates
            else pool.submit(_gate_js_task, plan.argv("gate:js"), spawn, emit, registry)
        )
        make_fut = (
            None
            if plan.skip_gates or plan.skip_make_test or defer_make_test
            else pool.submit(
                _gate_make_test_task,
                plan.argv("gate:make-test"),
                _spawn_with_env(spawn, {"PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.make_test_workers)}),
                emit,
                registry,
            )
        )
        contracts_fut: Future | None = None
        validators_fut: Future | None = None
        conform_fut: Future | None = None
        gate_keys: dict[str, str] = {}
        if not plan.skip_gates and plan.skip_conform:
            report.gate_conform = f"skipped ({plan.conform_note or '--skip-conform'})"
        elif defer_conform:
            report.gate_conform = f"deferred ({DEFER_NOTE})"
        if not plan.skip_gates and plan.skip_contracts:
            report.gate_contracts = f"skipped ({plan.contracts_note})"
        elif defer_contracts:
            report.gate_contracts = f"deferred ({DEFER_NOTE})"
        if not plan.skip_gates and plan.skip_validators:
            report.gate_validators = f"skipped ({plan.validators_note})"
        elif defer_validators:
            report.gate_validators = f"deferred ({DEFER_NOTE})"
        if not plan.skip_gates and plan.skip_make_test:
            report.gate_make_test = f"skipped ({plan.make_test_note})"
        elif defer_make_test:
            report.gate_make_test = f"deferred ({DEFER_NOTE})"

        gate = _do_run_m1(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            argv=None if plan.skip_run_m1 else plan.argv("run_m1"),
            skip=plan.skip_run_m1,
            skip_note=plan.run_m1_note,
            record=plan.record_greens,
            fingerprint=plan.run_m1_fingerprint,
        )
        if gate is None or not gate.ok:
            failures.extend(_run_m1_reasons(gate))
            if (plan.skip_gates or not plan.skip_contracts) and not defer_contracts:
                report.gate_contracts = "not run (run_m1 gate failed)"
            if (plan.skip_gates or not plan.skip_validators) and not defer_validators:
                report.gate_validators = "not run (run_m1 gate failed)"
            if not plan.skip_gates and not plan.skip_conform and not defer_conform:
                report.gate_conform = "not run (run_m1 gate failed)"
            _join_gates(report, failures, js_fut, None, None, None, make_fut, emit)
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
                plan.argv("gate:conform"),
            )

        if not _do_surface_build(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            review_out=plan.review_out,
            argv=None if plan.skip_surface else plan.argv("surface-build"),
            skip=plan.skip_surface,
            skip_note=plan.surface_note,
        ):
            failures.append("surface rebuild failed")
            if not plan.skip_gates and not plan.skip_contracts and not defer_contracts:
                report.gate_contracts = "not run (surface build failed)"
            if not plan.skip_gates and not plan.skip_validators and not defer_validators:
                report.gate_validators = "not run (surface build failed)"
            _join_gates(report, failures, js_fut, None, None, conform_fut, make_fut, emit)
            _record_gate_greens(report, plan, gate_keys, emit)
            return _finish(report, failures, plan, timings)

        if not plan.skip_gates and not plan.skip_contracts and not defer_contracts:
            if plan.record_greens:
                gate_keys["contracts"] = rebuild_lane_fingerprint(ROOT, "contracts") or ""
            contracts_fut = pool.submit(
                _gate_contracts_task,
                plan.pool_policy,
                conform_fut,
                make_fut,
                _spawn_with_env(spawn, {"AMS_POOL_UNIT": "rebuild-contracts"}),
                emit,
                registry,
                plan.argv("gate:rebuild-contracts"),
            )
        if not plan.skip_gates and not plan.skip_validators and not defer_validators:
            if plan.record_greens:
                gate_keys["validators"] = rebuild_lane_fingerprint(ROOT, "validators") or ""
            validators_fut = pool.submit(
                _gate_validators_task,
                plan.pool_policy,
                conform_fut,
                contracts_fut,
                make_fut,
                _spawn_with_env(spawn, {"AMS_POOL_UNIT": "rebuild-validators"}),
                emit,
                registry,
                plan.argv("gate:rebuild-validators"),
            )

        plumbing_key: str | None = None
        if plan.skip_plumbing:
            _skip_plumbing(report, plan, emit)
        elif plan.runs("plumbing"):
            chain_failures = _do_plumbing(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
            failures.extend(chain_failures)
            if not chain_failures and plan.do_merge and _plumbing_settled(report):
                plumbing_key = plumbing_skip_fingerprint(ROOT, REVIEW_OUT, plan.verdicts)
        if plan.complaints_note:
            report.complaints_status = f"skipped ({plan.complaints_note})"
        if plan.review_out is not None:
            report.census_status = "skipped (rehearsal: the checked-in pins track the live surface)"
        else:
            _do_census(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        if plumbing_key and report.complaints_ok is True and plan.record_greens and plan.review_out is None:
            record_plumbing_green(plumbing_key, plan.carry_out or plan.plumbing_carry_out)

        _join_gates(report, failures, js_fut, contracts_fut, validators_fut, conform_fut, make_fut, emit)
        _record_gate_greens(report, plan, gate_keys, emit)
        _do_job_costs(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        return _finish(report, failures, plan, timings)
    except KeyboardInterrupt:
        registry.terminate_all()
        pool.shutdown(wait=False, cancel_futures=True)
        report.interrupted = True
        return _finish_interrupted(report, failures, registry.killed_count, plan, timings)
    finally:
        pool.shutdown(wait=True)


def _deep_sweep_report(root: Path = ROOT) -> tuple[str, str]:
    """`deep_sweep_status` for the summary, and never a reason for a pass to fail: the deep sweep is an out-of-band instrument the cycle only reports on, so anything that goes wrong reading its record reads as unknown."""
    try:
        return deep_sweep_status(root)
    except Exception as exc:
        return "unknown", f"could not be read ({exc!r})"


def _print_summary(report: CycleReport) -> None:
    def show(value: object) -> str:
        return "—" if value is None else str(value)

    print("\n" + "=" * 68)
    print("ARTIFACT CYCLE SUMMARY")
    print("=" * 68)
    print(f"  snapshot dir            : {show(report.snapshot_dir)}")
    print(f"  oracle UNMATCHED        : {show(report.unmatched)} (informational)")
    print(f"  oracle multi_match      : {show(report.multi_matched)}")
    print(f"  Manual-pin gate         : {'pass' if report.pins_pass else show(report.pins_pass)}")
    print(f"  surface units           : {show(report.surface_units)}")
    print(f"  surface rows            : {show(report.surface_rows)}")
    print(f"  surface batches         : {show(report.surface_batches)}")
    print(f"  echo groups             : {show(report.echo_groups)}")
    print(f"  carry output            : {show(report.carry_out)}")
    for line in report.carry_lines:
        print(f"      {line}")
    print(f"  merge -> autosave       : {report.merge_status}")
    for line in report.merge_lines:
        print(f"      {line}")
    print(f"  echo-fill               : {report.echo_fill_status}")
    for line in report.echo_fill_lines:
        print(f"      {line}")
    print(f"  echo-merge              : {report.echo_merge_status}")
    for line in report.echo_merge_lines:
        print(f"      {line}")
    print(f"  standing-fill           : {report.standing_fill_status}")
    for line in report.standing_fill_lines:
        print(f"      {line}")
    print(f"  standing-merge          : {report.standing_merge_status}")
    for line in report.standing_merge_lines:
        print(f"      {line}")
    print(f"  census pins             : {report.census_status}")
    print(f"  job costs               : {report.job_costs_status}")
    print(f"  complaint groups        : {report.complaints_status}")
    print(f"  gate: JS suite          : {report.gate_js}")
    print(f"  gate: rebuild contracts : {report.gate_contracts}")
    print(f"  gate: rebuild validators: {report.gate_validators}")
    print(f"  gate: conform           : {report.gate_conform}")
    print(f"  gate: make test         : {report.gate_make_test}")
    deep_status, deep_note = _deep_sweep_report()
    print(f"  deep sweep              : {deep_status} ({deep_note})")
    print("  run_m1 summaries        :")
    for path in M1_SUMMARY_FILES.values():
        print(f"      {path}")
    print(f"      {CONFORM_SUMMARY}")
    print("=" * 68)


def _as_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _gate_entry(status: str, green: bool | None, skip: str | None = None) -> dict:
    """`green` is the judgment the gate recorded when it joined — True exactly when it ran in this pass and passed — never a re-reading of `status`, whose prose is for the human summary. A gate that never joined carries None and publishes False, since nothing was verified.

    `skip` is why the gate did not run, and it is the discriminator the readiness checker needs: "proved" means a matching green record already showed this exact content passing, so the state is verified; "forced" means a flag suppressed the gate and nothing proved anything; "deferred" means this pass chose the surface over the verification and left the gate for the next one, which is likewise unproven but has a one-command remedy. The status prose cannot carry that — every kind reads as some flavor of "skipped" — and a reader that cannot tell them apart is what once let --skip-conform report READY.
    """
    return {"status": status, "green": green is True, "skip": skip}


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
    deep_status, deep_note = _deep_sweep_report()
    return {
        "format": "ams-cycle-summary/1",
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "exit": exit_kind,
        "failures": list(failures),
        "gates": {
            "js": _gate_entry(report.gate_js, report.gate_js_green),
            "rebuild_contracts": _gate_entry(
                report.gate_contracts,
                report.gate_contracts_green,
                _skip_kind(proved=plan.skip_contracts, deferred="rebuild-contracts" in plan.deferred),
            ),
            "rebuild_validators": _gate_entry(
                report.gate_validators,
                report.gate_validators_green,
                _skip_kind(proved=plan.skip_validators, deferred="rebuild-validators" in plan.deferred),
            ),
            "conform": _gate_entry(
                report.gate_conform,
                report.gate_conform_green,
                _skip_kind(
                    proved=plan.conform_proven,
                    deferred="conform" in plan.deferred,
                    forced=plan.skip_conform,
                ),
            ),
            "make_test": _gate_entry(
                report.gate_make_test,
                report.gate_make_test_green,
                _skip_kind(proved=plan.skip_make_test, deferred="make-test" in plan.deferred),
            ),
        },
        "deep_sweep": {"status": deep_status, "note": deep_note},
        "make_test_fingerprint": (
            plan.make_test_fingerprint if report.gate_make_test_green is True or plan.skip_make_test else None
        ),
        "unmatched": report.unmatched,
        "multi_matched": report.multi_matched,
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
        "job_costs_status": report.job_costs_status,
        "job_costs_ok": report.job_costs_ok,
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
            "skip_run_m1": plan.skip_run_m1,
            "skip_surface": plan.skip_surface,
            "skip_contracts": plan.skip_contracts,
            "skip_validators": plan.skip_validators,
            "skip_plumbing": plan.skip_plumbing,
            "deferred": sorted(plan.deferred),
            "review_out": _as_str(plan.review_out),
            "first_run": plan.first_run,
            "short_id": plan.short_id,
        },
        "argv": list(sys.argv),
        "surface": _surface_block(plan.surface_dir),
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
        "--skip-gates",
        action="store_true",
        help="skip the five post-build gates (JS suite, the rebuild suite's contracts and validators lanes, conformance sweep, make test)",
    )
    parser.add_argument(
        "--skip-conform",
        action="store_true",
        help="skip gate:conform (the exhaustive font-vs-settle sweep) while keeping the other gates",
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
        help="on a pass that rebuilds M1 or the surface, record the heavy gates (rebuild-contracts, rebuild-validators, conform, make-test) pending instead of running them, so the letters are on screen sooner; the next pass has no artifact work and runs them. `make review-cycle` passes this; a deferred gate is unproven, so readiness stays NOT READY until a later pass clears it",
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
        help=f"exhaustive sweep length for gate:conform, passed through to run_m1 --conform-only (default {CONFORM_HORIZON_DEFAULT}, the per-edit belt); going deeper here is `make conform-deep`'s job, which runs out of band and keys its own green on the emitted lookup's behavior classes",
    )
    parser.add_argument(
        "--rebuild-pool",
        choices=POOL_POLICIES,
        default=REBUILD_POOL_POLICY_DEFAULT,
        help="how the heavy gates share cores: 'queue' (one pool at a time — make-test, then conform, then the rebuild suite's contracts lane, then its validators lane; default) or 'overlap' (co-resident)",
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
    skip_contracts = False
    contracts_note = ""
    skip_validators = False
    validators_note = ""
    conform_note = ""
    auto_skip_conform = False
    if not args.fresh and not args.skip_gates:
        contracts_key = rebuild_lane_fingerprint(ROOT, "contracts")
        green = read_green_record(REBUILD_CONTRACTS_GREEN)
        if contracts_key is not None and green is not None and green["fingerprint"] == contracts_key:
            skip_contracts = True
            contracts_note = "input closure unchanged since its last green run; --fresh overrides"
            print(f"gate:rebuild-contracts auto-skipped: {contracts_note}")
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
                cache_note = oracle_cache_note(note)
                if cache_note is not None:
                    print(f"  {cache_note}")
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
        if not args.skip_gates:
            validators_key = rebuild_lane_fingerprint(ROOT, "validators")
            green = read_green_record(REBUILD_VALIDATORS_GREEN)
            if validators_key is not None and green is not None and green["fingerprint"] == validators_key:
                skip_validators = True
                validators_note = "input closure unchanged since its last green run; --fresh overrides"
                print(f"gate:rebuild-validators auto-skipped: {validators_note}")

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
            "rebuild-contracts": not args.skip_gates and not skip_contracts,
            "rebuild-validators": not args.skip_gates and not skip_validators,
            "conform": not args.skip_gates and not args.skip_conform and not auto_skip_conform,
            "make-test": not args.skip_gates and not skip_make_test and not args.force_make_test,
        },
    )
    if deferred:
        print(
            "Heavy gates deferred to the next pass: "
            + ", ".join("gate:" + name for name in sorted(deferred))
            + f" — {DEFER_NOTE}; --no-defer-gates runs them in this one."
        )

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
    store_only = False
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
        recorded_carry = (record or {}).get("carry_out")
        if isinstance(recorded_carry, str) and Path(recorded_carry).exists():
            plumbing_carry_out = Path(recorded_carry)
        if plumbing_key is not None and record is not None and record["fingerprint"] == plumbing_key:
            skip_plumbing = True
            plumbing_note = PLUMBING_SKIP_NOTE
            print(f"verdict plumbing auto-skipped: {plumbing_note}")
        elif plumbing_key is not None and args.verdicts is not None:
            # The surface has not moved, so the carry would resolve every unit against itself: the snapshot is a clone of this same surface, the content keys are equal, and the carry preserves each record's `at`, which the merge compares strictly — so its re-prefixed notes could never land. Only the store moved, and the one input the store's own hash cannot see is the master, so merging that directly is the whole of what the carry was for.
            store_only = True
            print(
                "verdict plumbing: the surface did not move, so the carry is the identity — merging the "
                "master straight in, then the fills and the docket."
            )

    plan = build_plan(
        verdicts=args.verdicts,
        no_carry=args.no_carry,
        carry_out=args.carry_out,
        snapshot_dir=args.snapshot_dir,
        skip_gates=args.skip_gates,
        first_run=first_run,
        short_id=resolve_short_id(),
        no_merge=args.no_merge,
        skip_conform=args.skip_conform or auto_skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fp,
        conform_horizon=args.conform_horizon,
        pool_policy=args.rebuild_pool,
        review_out=args.review_out,
        skip_run_m1=skip_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fp,
        fresh=args.fresh,
        skip_surface=skip_surface,
        surface_note=surface_note,
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        skip_validators=skip_validators,
        validators_note=validators_note,
        conform_note=conform_note,
        conform_proven=auto_skip_conform,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        plumbing_carry_out=plumbing_carry_out,
        store_only=store_only,
        deferred=deferred,
        preserve_snapshot=preserve_snapshot,
        record_greens=not args.dry_run,
        keep_history=args.keep_history,
    )

    if args.dry_run:
        print(render_plan(plan))
        return 0

    if not _preflight(
        args, may_stay_up=server_may_stay_up(skip_surface=skip_surface, skip_plumbing=skip_plumbing)
    ):
        return 2

    if first_run:
        print("First-run mode: no existing surface at rebuild/out/review — skipping snapshot and carry.")

    report = CycleReport()
    from rebuild.tools.cycle_timings import CycleTimings

    timings = CycleTimings(CYCLE_TIMINGS)

    if not first_run and not plan.skip_plumbing and not plan.plumbing_store_only:
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
