"""Run the commit-time artifact cycle in one command.

The cycle recompiles Senior-Regular.otf and checks it, builds the table-diff review corpus beside the served one (`engine.review.build`: the accepted snapshot of the build, which the tracked engine/review-snapshot.json names and var/keep/review-snapshot/ holds, against engine/out/build), and lands it, carrying the verdict store onto it by unit id (`engine.review.landing`). It then runs the gates. Once they have joined and their pytest controllers have written this pass's per-worker peaks to the timings journal, it compares the checked-in per-unit peaks with what this machine measured (`engine.tools.calibrate_budgets --check`). It always ends with a summary table, even on failure, and a green pass that is not a staging pass ends with the readiness checklist, above which a `review diff` line counts the corpus's unaccepted changes since the snapshot (`readiness_block`, `verdict_ready.diff_summary_line`). The diff is a row and a line, never a red gate: a pass whose corpus holds unaccepted changes ends green and READY, and `make review-accept ARGS=--apply` accepts them.

The terminal shows one banner per step with that step's description, the phases and counters its child prints, every warning, and a closing line. All child output is written under var/build-logs/<stamp>-<short sha>/: one log per step with stdout and stderr merged in arrival order, plan.txt, and a copy of the terminal output. var/build-logs/latest points at the newest run, and a failed step's log is replayed under its banner. engine.tools.console defines the line protocol children print and the renderer that reads it.

The job-costs step never fails the pass: a stale constant makes a pool the wrong width, which costs time but makes no artifact wrong. It is reported, and committing the re-measured constant accepts it. When the check reports an overrun, the OVERRUN status quotes each tripped row's proposal (the value its constant's rule sets from the peak and the width that value gives here, or for the kernel-build row which constants to re-measure), and the driver asks `calibrate_budgets --moved` which checked constants differ from their values at `HEAD`, so a constant already re-measured shows up by name.

A pass refuses before it parses its command line while the alphabet switch (`spec_load.RUNES_ENV`) is set outside the fault kit (`spec_load.small_alphabet_refusal`), so a shell that leaks it cannot build a small alphabet into the live tree.

run_build's exit status is its own gate's result, but this driver evaluates the two summary JSONs it writes, so a build that died before its evaluator is reported by what it left behind. The gates are defect_errors and the Manual-pin gate (including its scope, so a gate that replayed nothing cannot pass).

The pass reads the write meter (`engine.tools.write_meter`) as it starts, once `_preflight` has settled the review server, at each child's spawn and reap, and after every step has joined, with the review server's pid named when the server is this checkout's (`review_server_pid`). `cycle_timings.CycleTimings` puts each child's window on its step line and the pass's total, its basis and its write class on the run line; the summary table shows each window and the summary one line for the pass, with a warning when the pass wrote more than its class's ceiling (`cycle_timings.PASS_WRITE_CEILING_BYTES`). A reading never fails the pass. The driver also names a counts file for the pass in the environment (`cycle_timings.OUTPUT_COUNTS_ENV`), which every child inherits, so each write through `output_sink`'s helper counts what it wrote or left in place, and the run line's `written` block carries the sum as `outputs`; the file is removed as the pass ends, or by the next pass when the driver was killed outright (`sweep_run_dirs`).

This process, not its children, records each check's result in the timings journal. Every evaluated check (run_build, conform, engine-contracts, make-test, js) appends one kind:"check" line tagged with this run, carrying the evaluator's outcome, not the process's exit code; `make cycle-timings ARGS='--by-outcome'` reads them. run_build's CLI and make_test_gate record their own line when run by hand, so this driver sets its run id in the environment as AMS_CYCLE_RUN (cycle_timings.CYCLE_RUN_ENV), and those children record nothing when they inherit it. run_build's CLI prints the result on a `[check]` line instead (`console.CHECK`), which this driver files when the summaries a run_build or gate:conform child left do not say why it stopped (`_reported_red`). Each check invocation gets one line.

gate:js depends on no build artifact, so it starts at t=0 in a small thread pool while the build steps run in sequence in the main thread. gate:make-test checks the fonts its session publishes into site/, the Senior among them copied from the build run_build leaves (engine/tools/build_fonts.py), so it is submitted to that pool once the build gate passes, and not run when the gate fails. gate:conform (the exhaustive font-versus-settlement sweep at the per-edit maximum length, `run_build --conform-only`) starts after the build gate passes too, beside make-test under the default overlap policy, and behind it under the queue policy and on a pass with no build lane (`gates_queue`). Its deeper form, `make conform-deep`, never runs in the cycle; the summary has one line saying whether the emitted lookup has a shape the last deep run did not shape. gate:engine-contracts runs every test under engine/, and none of them reads a live build artifact (engine/conftest.py's audit hook enforces this). A hand run uses every core. Under a cycle the suite is submitted with the conform lane, right after the build gate passes, and runs at `contracts_pool_width`, set on that one child as PYTEST_XDIST_AUTO_NUM_WORKERS: the cores less the corpus build's one process, and, under the overlap policy on a pass that runs gate:make-test beside the build lane, less that gate's pool. It starts beside the corpus build. The conformance sweep's core cap (`_conform_core_cap`) makes the same subtraction, floored at the acceptance-configuration count, and neither width subtracts the other, so under the overlap policy the sweep and the suite share the cores those processes leave while both run. The suite reads nothing the build lane writes, so it waits for no build output. A suite that is not queued starts at t=0 on a pass where every upstream stage skips.

While a gated pass's build lane runs (run_build, then the corpus build, then the land), the cycle splits the usable cores between that lane and the gate lane (`memory_budget.split_cores`, which gives the build lane the odd core), and gate:make-test's pool takes the gate lane's share (`make_test_pool_width`). gate:make-test starts once the build gate has passed, so the build lane beside it holds the corpus build, one Python process (CORPUS_BUILD_BYTES), and then the land, and gate:conform's sweep and gate:engine-contracts' pool take the cores that process and gate:make-test's pool leave (`_contracts_pool_terms`). A pass that runs neither run_build nor the corpus build gives gate:make-test's pool every core, and when it runs that gate it runs the heavy gates one at a time under either policy (`gates_queue`). Memory is not split: each pool's width subtracts the memory of the pools the plan runs beside it.

`--pool-policy` sets how the heavy gate pools share the machine. Under the default overlap policy, gate:conform and gate:engine-contracts start as soon as the build gate passes and run beside gate:make-test's pool, each other, and the build lane. Under the queue policy the gates run make-test, then conform, then engine-contracts, so only one heavy gate pool runs at a time, and the build steps run beside whichever one it is. A pass that runs gate:make-test and neither run_build nor the corpus build queues them so under either policy (`gates_queue`). Each pool's width comes from its budget function: the conformance sweep (`conform_job_budget`) runs its units (each settlement configuration once per final symbol, and the ss10 overlay whole) one process each, as many at once as CONFORM_SWEEP_UNIT_BYTES fits beside the build lane, capped at the cores the corpus build leaves and never below the acceptance-configuration count; and the corpus build is one Python process, which the sweep's memory budget books at CORPUS_BUILD_BYTES and the sweep's and the suite's core counts at one core. Under the overlap policy, on a pass that runs gate:make-test beside the build lane, the conformance sweep's memory budget and core cap and the engine suite's width also subtract that gate's pool, which holds the gate lane, so the suite and the sweep share the build lane's share less the corpus build's process, the sweep floored at one process per acceptance configuration. When the gates queue, neither subtracts gate:make-test's pool, which has finished by the time they start. Neither of those two widths subtracts the other's pool. `--dry-run` prints every width with its derivation.

The overlap policy is the default because of whole-pass timings of `--fresh` passes under both policies on the 18-core 48 GiB machine (`doc/fleet.md`), which bare `make cycle-timings` on that machine lists step by step. Those passes ran gate:make-test from t=0, beside run_build, and it finished before run_build did, so under either policy the conformance sweep and the engine suite started beside the corpus build and finished before the build lane, and the build lane ended the pass. gate:make-test starts after the build gate, beside the corpus build and the other two heavy gates, so those timings predate the schedule they argue for, and the comparison is due again. The overlap policy's pass is no slower than the queue policy's, so the timings give no reason to switch. Timed by hand there with no corpus build or gate:make-test running, a narrowed conformance sweep and the full-width engine suite also finished sooner side by side than one after the other (issue #468 records the runs). Under the overlap policy the sweep and the suite subtract gate:make-test's pool even so, because the plan cannot tell whether it will be running when they start, which leaves the suite one worker; that time is off the critical path on those passes. The signal to time both policies again is an overlap-policy pass with gate:make-test's pool at the gate lane's share (its run line's `plan.make_test_workers`) that a `gate:` step ends. `make cycle-timings ARGS='--critical-path'` counts the passes each step ends, but its row for a pass shape also counts passes run at other gate:make-test widths, so such a pass shows there as growth in a `gate:` step's count.

The comparison behind the default covers `--fresh` passes on the 18-core machine only; `make cycle-timings` shows which machines and which kinds of pass each policy has run on. On the twelve-core machine the same pools share fewer cores. On a pass that runs the corpus build and skips gate:make-test (which skips when its input closure is unchanged since its last green run, as after only review-side or comparison-side code edits, which leave the fonts' inputs and the rest of its closure as they were), the two policies give every pool the same width, and under the overlap policy the sweep and the suite share the gate lane's cores while both run.

The cycle runs no cross-language check, because the kernel crate is the only engine that enumerates and the only one that settles. gate:conform checks settlement empirically: it shapes the compiled font through HarfBuzz and compares the result, window by window, with a re-settlement of every swept text through the crate's `settle-cases` subcommand, with the memo keyed on the raw window so the sweep does not depend on the crate's enumeration and fold. `make kernel-gate` is the crate's own gate, to run around a kernel-semantics change; it takes seconds once the crate is built. The spec-ingest parity check is a contracts test (engine/test_kernel_io.py) and runs in gate:engine-contracts on every cycle.

gate:make-test is skipped when its input closure is unchanged since its last green run. The closure is every tracked or untracked-unignored file that `make_test_exempt` does not exempt; that function's docstring argues each exemption from what the gate runs (pyright over tools/ test/ conftest.py, and pytest test/ site/, whose session publishes the fonts it checks into site/). The Makefile itself is represented by what `make -n test` prints, and the published fonts by what they are published from (`make_test_font_lines`), so a pass whose run_build writes the Senior decides the skip only after run_build (`Plan.make_test_undecided`). Re-running the gate over an unchanged closure would repeat the whole font suite (`make cycle-timings ARGS='--by-step'` reports what a run costs) and check nothing. The last green fingerprint is in engine/out/make-test-green.json, written by engine.tools.make_test_gate (the `make test` entry point) on every green run, so interactive and cycle greens share one record and `make test` skips on the same test. cycle_summary.json also records the fingerprint the cycle ran or skipped against, for display only. The skip reads only the shared green record, so a green that make_test_gate deleted after a red run cannot come back from an older summary. The fingerprint covers file content only, so a system toolchain change does not move it (pyright and pytest are pinned in uv.lock, which is in the closure). --force-make-test and --fresh spawn `make test FORCE=1` (`make_test_gate_argv`), because the wrapper decides its own skip with the predicate the plan uses (`make_test_skippable`), and a plain `make test` would skip on the closure the flag forced. The plan reserves the gate's cores and memory beside the corpus build only when that predicate says the gate runs.

Every other heavy stage skips on the same principle: a content fingerprint over the stage's input closure, and a green record written only after that content passed.

- run_build skips on engine/out/build-green.json (the Stage A fingerprint components plus each rune's kern line, the contact allow-list and uv.lock's dependency pins) while the Stage A record beside its artifacts matches the sources on disk (`stage_a_current`), and re-evaluates its gate from the summary JSONs on disk.
- gate:conform skips on conform-green.json, keyed on what the conformance sweep tests for, not on run_build's closure: the emitted lookup's behavior classes, the font-compilation code, the uharfbuzz version, and the sweep's maximum length (`conform_skip_fingerprint`). A rune edit that creates no new rule shape leaves that key unchanged, because the crate's string replay inside run_build has already checked the new tables against the engine over every string.
- The engine suite skips on engine-contracts-green.json, keyed by `contracts_fingerprint` over its closure: the repo files under engine/ and glyph_data/, the harness files in ENGINE_GATE_HARNESS_PATHS, conftest.py, pyproject.toml and uv.lock by its dependency pins. The closure contains no build artifact, so the suite can skip whether or not run_build rebuilt: a rebuild writes only under engine/out, which the closure does not include. The record also stores each test's input closure, so a pass whose inputs changed runs only the tests the diff reaches and those it cannot rule out, and a skip also requires the extra paths the recorded closures name to keep their bytes (`contracts_closure.prepare_run`); engine.tools.contracts_closure owns that selection and the run lifecycle. engine.tools.engine_gate (`make test-engine`) writes the same record, so interactive and cycle greens share it.
- corpus-build skips when the served corpus is a table-diff corpus whose recorded inputs fingerprint equals the one a build would stamp now and whose after font is engine/out/build/Senior-Regular.otf byte for byte (`corpus_build_skippable`). A rebuild would then reproduce its content byte for byte, but `generated_at` is the latest input mtime, floored, so a rebuild after an mtime-only change (a checkout, a touch) could restamp it; skipping keeps the stamp, so the store stays aligned. When the live corpus does not match but a corpus built beside it does (a complete engine/out/review.next a stopped pass left, the last cycle summary's `plan.review_out`, or var/staged-review), the land swaps that directory in instead of a rebuild (`promotable_corpus` checks the preconditions). Every stamp inside a corpus depends only on content relative to its manifest, and the swap keeps the `generated_at` a rebuild could reset.

The corpus skip applies only on passes where run_build skipped, and on a gates-only rerun when the Stage A record on disk already matches the sources on disk (`stage_a_current`). A run writes that record only once its gates pass, so a matching record sits beside the tables and font a passing run over these Stage A inputs wrote, and the rerun reproduces them and writes back the same record (`stage_a_current` says why, and names the two gaps where it does not): the corpus reads nothing the pass changes. That happens on a contact-allow bless, since the allow-list is in no Stage A component. A kern-only rebuild writes a new Senior-Regular.otf and kern.tsv, which the corpus's after font and kerning units show, so it always rebuilds the corpus.

Conform's skip is decided after run_build finishes, from the key the artifacts it left carry. A mode that leaves the emitted lookup's shapes, the compile code and the shaper at the last green key skips the sweep, whether run_build skipped, reran only its gates, or rebuilt, and the skip is recorded as proved because a matching green covers this exact content. Computing the key only after run_build has finished also means a rebuild cannot invalidate it during the cycle. The preflight can decide it before the pass only in the mode where run_build skipped, so nothing will change; that is the mode --dry-run can predict. On a gates-only rerun or a rebuild the printed plan shows the conform lane as undecided (`run?`), because only a finished run_build knows what the artifacts are, so a plan that shows the sweep may end in a pass that skips it.

Green records are written only when the key still matches after the work ran, and a red result whose key matches its record deletes the record. --fresh runs everything regardless.

Between the run_build skip and a full rebuild there is a third mode, the gates-only rerun. When the per-file diff against the run_build green is confined to comparison-side inputs (the contact allow-list and the code `fingerprint.COMPARISON_CODE_MODULES` names, both outside the tables' stamp; `comparison_side_label` lists them and argues each) and engine/out/build holds the build the rerun reads (`gates_only_base_present`, the check `run_build_gates.gates_only_base` makes: the tables carrying that stamp, Senior-Regular.otf, pipeline_summary.json and the join tables), the cycle spawns `run_build --gates-only` instead of a build. It re-runs the defect gate and the Manual-pin gate over the tables and font on disk, and enumerates nothing. That pass records a green over the new inputs, so the next cycle skips run_build, though what it checks does not show that the tables and Senior-Regular.otf on disk are a passing build's (`run_build_gates.run_gates_only` names the gaps). `uv.lock` is not comparison-side, because a fontTools or uharfbuzz bump can change the font's bytes and what the shaper does with them, so a toolchain bump rebuilds.

A fourth mode is the kern-only rebuild. When the diff against the run_build green is confined to runes' kern lines and the font's non-Quikscript data (`font_only_label`: a rune's `kern` block is in the run_build skip key through `fingerprint.kern_lines` and in no other key, and the Latin, Shavian and symbol glyphs, the metadata, the Latin kern rules and the glyph-name map through `fingerprint.font_data_lines`), engine/out/build holds the build a gates-only rerun would read, and its Stage A record matches the sources on disk (`stage_a_current`), the cycle spawns `run_build --kern-only`. It runs the glyph chain alone over the tables on disk (`run_build.kern_only_run`): the kern stage, the defect gates at the kerned offsets, the GSUB and GPOS emission, the compile and read-back. The string replay, the witness stage, the shipped-order walk and the Manual-pin gate read nothing a kern block or a Latin glyph feeds, so their summaries stand, and the pass records a green over the new inputs as a gates-only rerun does (`kern_only_rebuild`). The matching Stage A record is what shows those summaries are a passing run's over these inputs, so without it the cycle rebuilds. The child carries that record forward and publishes its files only once read-back passes, so a kern-only child that fails leaves the prior build, its summaries and the record in place, and the next pass plans a kern-only rebuild again.

The review server keeps running through every pass. Two things a cycle writes belong to the running app: the corpus it serves (a rebuild rewrites the shards the tab reads by byte range and restamps the manifest the tab's saves are keyed on) and the verdict store it saves into. So no pass writes the corpus in place. A corpus-changing pass builds the table-diff corpus at engine/out/review.next, which it empties first, and the land (`engine.review.landing`) then swaps the corpus in and, when the stamp moves, carries the store onto it by unit id (`verdict_store.carry`) in one short section under the store's lock: a verdict whose unit the new corpus still has, its content unchanged and so its id, stays, a skip is dropped, and the rest go to var/verdict-orphans/, which no pass refuses over. The store is small and the review server writes it whole on every save, so the land reads every save made before it takes the lock, and a save made while it holds the lock gets a retryable 503. The land runs as a child in its own session that a stop signal does not reach and the driver waits for (`_ChildRegistry`). A land killed between its swap and its store write leaves a store on the old stamp, which the next save stamped for the served corpus, or the next pass's land, carries onto the new one. After the land the pass sends `ams:corpus/<generated_at>` through livereload's /forcereload, and the open tabs save, wait while the reader types, and reload onto the new corpus. Only the first run builds in place, since no server can serve a corpus that does not exist yet.

The cycle keeps a listening server only when its /capabilities says it serves this checkout with the working tree's land-protocol and freshness code (`probe_server`, `landing.code_digest`); a server from this checkout running other code is restarted first (`restart_review_server`), and one serving another checkout is left alone and sent no reload. A server from before the land protocol answers no /capabilities, and it takes the old rule: a pass that writes neither the corpus nor the store keeps it running (`server_can_keep_running`), and any other pass stops it once with --stop-server (which `make review-cycle` passes), runs beside it with --yes, or refuses.

An edit confined to engine/review/static/ also has its own mode. The copied app assets are the one corpus input no unit depends on, so the pass copies them over the served copy and restamps that one fingerprint component (`assets-refresh`), each file renamed into place. Every shard, both sidecars and `generated_at` stay as they were, so nothing the tab is keyed on changes. Once the files are in place the pass sends `ams:assets/<static hash>`, and the open tab reloads onto the new assets when the reader is not typing. The server itself reloads no tab on a file change (`engine.review.serve.register_dormant_watch`).

A corpus promotion is the opposite case under the same skip: the land swaps the staged corpus in, the stamp changes with it, and the land carries the store onto it by unit id.

Every pass holds the pass lock (`pass_lock`, var/cycle/pass.lock) from before it recovers a superseded corpus and resolves its plan until it ends, so a second pass waits for the first instead of planning against a tree the first is still writing. The land inherits the lock, so a driver killed while its land runs still holds the next pass back until the land has finished. A staging pass holds it too, because a live pass's promotion reads the corpus a staging pass writes. A dry run takes it without waiting, and when another pass holds it the dry run skips the recovery, which could rename a tree that pass is landing. Each non-staging pass gets a scratch directory under var/cycle/ named like its build-log run directory and deletes it when it ends; the next pass deletes any that a killed pass left (`sweep_run_dirs`). A staging pass and a dry run get no scratch directory.

A green finish ends with a retention pass, which deletes the build-log run directories beyond the newest `cycle_paths.BUILD_LOGS_KEEP` (`run_retention`). Failed, interrupted, first-run, and staging passes never prune, --keep-history turns retention off, and a retention error prints a warning and never turns a green cycle red.

Run as: uv run python engine/tools/artifact_cycle.py.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import functools
import hashlib
import json
import os
import posixpath
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Collection, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from engine.review import app_index, landing, shards, store_lock  # noqa: E402
from engine.tools import console, cycle_paths  # noqa: E402
from engine.tools import contracts_closure as contracts  # noqa: E402
from engine.tools.green_record import (  # noqa: E402
    _digest_lines,
    _record_outcome,
    _sha256_path,
    clear_contradicted_green,
    read_green_record,
    record_green,
    settle_green,
)
from engine.tools.cycle_timings import (  # noqa: E402
    CYCLE_RUN_ENV,
    OUTPUT_COUNTS_ENV,
    UNCLASSIFIED,
    CheckResult,
)
from engine.tools.peak_rss import reap_peak_rss_bytes  # noqa: E402
from engine.tools.review_server import (  # noqa: E402
    NO_CAPABILITIES,
    REVIEW_PORT,
    capabilities,
    force_reload,
    server_listening,
)

if TYPE_CHECKING:
    from engine.pipeline.model import ResolvedSpec
    from engine.tools.cycle_timings import CycleTimings, PassWrites
REVIEW_OUT = ROOT / "engine" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
CYCLE_TIMINGS = ROOT / "engine" / "out" / "cycle-timings.ndjson"
BEHAVIOR_CLASSES = cycle_paths.BUILD_OUT / "behavior_classes.json"
JSTEST_DIR = ROOT / "engine" / "review" / "jstests"

POOL_POLICIES = ("queue", "overlap")
POOL_POLICY_DEFAULT = "overlap"
CONFORM_SKIP_NOTE = "no new rule shape, compile code or shaper since its last green sweep; --fresh overrides"
CONFORM_MAYBE_NOTE = "runs unless run_build leaves the emitted lookup's behavior classes, the compile code and the shaper under the key of its last green sweep, in which case it is re-skipped after run_build"
MAKE_TEST_MAYBE_NOTE = "runs after run_build unless the built Senior it checks the pins against comes out, with the rest of its closure, under the key of its last green run, in which case its wrapper skips itself"
UNDECIDED_UNTIL_BUILD = {
    "gate:conform": CONFORM_MAYBE_NOTE,
    "gate:make-test": MAKE_TEST_MAYBE_NOTE,
}
ASSETS_REFRESH_NOTE = "only the review UI assets moved since the corpus was stamped; they are copied over the served copy and the manifest's static component restamped in place — no shard, sidecar or generated_at moves; --fresh overrides"
CORPUS_PROMOTE_NOTE = "a staging pass already built the corpus these inputs produce, byte for byte; the land swaps that directory in instead of a rebuild; --fresh overrides"
SERVER_KEEPS_RUNNING_NOTE = (
    "rewrites no unit shard, moves no manifest stamp, and leaves the verdict store alone"
)
SERVER_STOP_PATTERN = r"engine\.review\.serve"
SERVER_STOP_TIMEOUT = 15.0
SERVER_START_TIMEOUT = 30.0
CAPABILITIES_ATTEMPTS = 3
CAPABILITIES_RETRY_S = 1.0
# The gate thread pool's worker count, sized to the tasks the chain submits, not to the cores. When the heavy gates queue (`gates_queue`: under the queue policy, and on a pass that runs gate:make-test with no build lane) a waiting task holds its worker for the whole wait (conform waits on make-test, and contracts on both), so every gate task needs a worker at the same time, with spare workers on top. With fewer workers a waiting task could sit behind an unrelated task's completion, and a width taken from the cores would cause that on a small machine. `test_the_gate_pool_runs_every_gate_task_at_once` in engine/test_artifact_cycle.py checks that this equals the gate task count plus two.
_GATE_POOL_WORKERS = 6
# The peak memory of the corpus build (`engine.review.build`), one Python process that diffs the accepted snapshot's tables with engine/out/build's, shapes each change's candidate texts in both fonts, and holds the corpus's fragments until it writes them, so its peak grows with the units an edit makes. It runs in the build lane beside gate:conform's sweep, whose memory budget books it (`_conform_build_lane`). The constant comes from the timings journal's corpus-build steps on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) at 56 runes: 0.106 GB peak resident on an empty diff, and 0.413 GB on cycle pass e2ee368a3483, taken on 2026-11-26, whose corpus held the 15 units of a one-letter extend, plus a quarter, rounded up to the next tenth of a gigabyte, 0.6. A diff with more units holds more; the corpus-build row of `make job-costs` checks the constant against every pass's step peak.
CORPUS_BUILD_BYTES = 600_000_000
# How the plan lines name the corpus build where a width subtracts its one core.
CORPUS_BUILD_PROCESS = "the corpus build's process"
# The peak memory of the land (`engine.review.landing`), the build-lane step after the corpus build that moves the corpus and the store into place. It holds the store whole, which lasts from one accept to the next and holds the verdicts made since, a few hundred records at most, and the new corpus's human unit ids, so its peak is its interpreter's. The constant comes from the land step of cycle pass 65de92ca6254 at 55 runes on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), taken on 2026-11-22, when the land streamed stores of up to 332,250 records beside a journal: 0.218 GB peak resident, plus a quarter, rounded up to the next tenth of a gigabyte, 0.3. It is below the corpus build's CORPUS_BUILD_BYTES, which `test_the_land_holds_less_than_the_build_lane_step_before_it` checks, and the land runs after the corpus build in the same lane, so the build lane's reservation (`_conform_build_lane`), which covers the build, covers it too, and no width depends on it. The land row of `make job-costs` checks it against the land step's peak.
LAND_BYTES = 300_000_000
# The peak memory of one conformance-sweep unit, the divisor of gate:conform's width (`conform_job_budget`). A unit is a spawn process running `conform.conformance_config_worker` over one settlement configuration's texts that end in one symbol, or over the ss10 overlay whole (`run_build_gates.sweep_units`), and the pool runs one unit per process (`max_tasks_per_child=1`). A settlement unit holds its interpreter, a HarfBuzz `Shaper` over Senior-Regular.otf, the spec's alphabet, splitters, glyph names and anchors, the section 5.7 guard verdict map the parent passes down, a `conform.TEXT_CHUNK` of texts in flight, and its configuration's settle memo, its base and every segment its head lists mapped read-only (`conform._MemoStore`) and walked with `promote=False`: the parts' label and outcome tables and a dead byte and a reached byte a row over the segments' rows beside the base's on the heap, and the columns, probe indexes and segments' Bloom filters, about 0.5 MB a segment at 45 runes, as page-cache pages shared by every unit that maps the memo. It also holds `windows`, the dict of windows it settles fresh: empty with the memo current, and every window its texts reach, those that name the edge included, with the memo dropped. The same pool runs each configuration's absorb (`run_build_gates._absorb_memo`, over `conform.absorb_settle_memo`) in a fresh process in the slot a unit frees, as soon as the configuration's units have finished and their counts check, on the route planned before the first absorb was submitted: on the segment route it holds the standing memo's flags, selector and names, about 4 bytes a standing row, the fresh windows its units shipped, decoded at 14 bytes a row, each unit's compressed blocks until it has read them, and the segment's index over the fresh rows, its Bloom filter and its dead bitmaps while it writes the segment, less than a whole file's absorb holds; on a base route, a fold among them, it holds the whole-file absorb's terms, the standing flags and selector, 3 bytes a standing row, the kept-row map when the prune drops rows, the fresh windows, the new index while it writes the file, once a row merges onto an earlier one the merge's state over every row from the first merged one on, 5 bytes a row, and on a fold the rows it carries copied out of the parts, 14 bytes a carried row, beside flags over every part's rows, 3 bytes a row (`conform.absorb_settle_memo`); a fold is the heaviest route, and the measurement set below holds one, beside two segment-route absorbs whose every row was fresh, so the constant covers the absorb too. With the memo dropped the units walk with no memo shared between them, so a window two units reach is settled by both, decoded at 14 bytes and counted into the slot count before it merges; no reading counts those repeats. The conform-sweep row reads peak footprints (`peak_rss.peak_footprint_bytes`), which leave out the clean pages of the mapped memo parts. The page cache holds those once per machine however many units map them, they are reclaimable, and they come out of the reserve rather than this constant.
# The measurement set is the conform-sweep row's readings on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`): at 48 runes, the eleven conform-sweep pool records from 2026-11-14T09:56:39Z to 14:30:45Z, in `pool-readings.txt` in var/keep/ram-inventory/v4/fold48-readings/, whose `README.md` says how the lines were taken, with that directory's `fresh-sweep` probe lines beside them, and at 49 runes the pool record of 2026-11-17T08:40:27Z, a `run_build --conform-only` sweep after a rune edit. At 48 runes a unit reads 0.160 to 0.268 GB peak footprint, `ss08`'s the highest, at 11:32:01Z and 11:51:00Z, and lowest on the first pass over a memo just folded into a single base; the ss10 overlay reads 0.063 to 0.066 GB. Those records' absorbs are all `ss08`'s: 0.097 to 0.192 GB on the segment route, and 0.301 GB on the pass whose absorb folded its memo, at 12:07:13Z. The `fresh-sweep` lines, no standing memo and every row fresh in the `default` units' parts, read 0.170 GB peak footprint and 0.182 GB peak resident. At 49 runes the `ss04` and `ss08` memos were refused on load (`[warn] settle memo: … not loaded`), so those configurations' units settled every window fresh and each absorb wrote all of its configuration's 5.48 million windows as one segment: 0.492 GB for `ss04` and 0.489 GB for `ss08`, the highest in the set. That record's units read 0.182 to 0.257 GB, `ss03+ss05`'s the highest, and the ss10 overlay 0.066 GB. The constant is the smallest tenth that covers, with a quarter to spare, every peak footprint in the set: `ss04`'s all-fresh absorb at 0.492 GB, and a quarter on it, 0.615, rounds up to 0.7. Peak resident is left out: the fold lines in the same directory read 0.125 GB more resident than footprint, the clean pages of the memo's parts the fold copies its rows out of, which are the pages every unit of the configuration maps, held once by the page cache and covered by the reserve, as the units' mapped pages are. The set counts none of the repeats an all-fresh sweep's units settle, which the fresh-sweep lines' parts never hold. At this figure memory limits no fleet machine's width: a hand run is as wide as the cores, and a cycle's width is `_conform_core_cap`'s. The conform-sweep row of `make job-costs` checks it: `run_build_gates.run_font_conformance` writes one kind:"pool" record per pooled conformance sweep at `conform.SWEEP_MAX_LENGTH`, one observation per configuration (its highest unit) and one per absorb. The gate:conform step peak is not used, because `peak_rss.reap_peak_rss_bytes` takes the maximum over the tree and so reads one process.
CONFORM_SWEEP_UNIT_BYTES = 700_000_000
CONFORM_MAX_LENGTH_DEFAULT = 4
DEEP_SWEEP_MAX_LENGTH_DEFAULT = 5
# One letter past the build's own replay (`run_build.REPLAY_MAX_LENGTH`), the depth at which a text first puts a letter in the third lookahead slot behind a letter on the left. The deep replay checks that one extra letter, and `make replay-deep ARGS='--max-length 6'` goes deeper on demand.
DEEP_REPLAY_MAX_LENGTH_DEFAULT = 5
COMPILE_CODE_FILES = (
    "engine/pipeline/emit_gsub.py",
    "engine/pipeline/emit_gpos.py",
    "engine/pipeline/pack_gsub.py",
    "engine/pipeline/compile_font.py",
    "engine/pipeline/font_assembly.py",
    "engine/pipeline/glyph_records.py",
    "engine/pipeline/departure_mono_import.py",
)


def contracts_green() -> Path:
    """Return the contracts green-record path from `cycle_paths` at call time, so the engine conftest's synthetic-root redirect reaches both callers."""
    return cycle_paths.ENGINE_CONTRACTS_GREEN


def contracts_argv() -> list[str]:
    """Return the contracts suite's argv with its selection and sidecar paths beside the green record. `-n auto` uses the cores available to this process or the cycle's explicit worker override; the suite reports its slowest tests in its own log. `--tb=short` keeps each failure's test name, worker and exception in the step log, and `--show-capture=no` keeps out what the test printed, because the step's output goes through line readers (`console.CycleConsole` and `classify_engine_suite_output`) that judge a line by how it starts, so a captured line would pass for their own. `--code-highlight=no` keeps pygments out of the traceback, because under `FORCE_COLOR` its deprecation warnings on Python 3.15 flood the log."""
    argv = [
        "uv",
        "run",
        "pytest",
        "engine/",
        "-n",
        "auto",
        "--dist",
        "worksteal",
        "-q",
        "--tb=short",
        "--show-capture=no",
        "--code-highlight=no",
        "-rfE",
        "--durations=25",
    ]
    record = contracts_green()
    argv += [
        "--closure-skip",
        str(contracts.selection_path(record)),
        "--closure-record",
        str(contracts.sidecar_path(record)),
    ]
    return argv


MAKE_TEST_EXEMPT_PREFIXES = (
    "engine/",
    "glyph_data/runes/",
    "doc/",
    "tmp/",
    "var/",
    ".claude/",
    ".vscode/",
    ".github/",
    "reference/csur/",
    "site/icons/",
)
MAKE_TEST_EXEMPT_FILES = (
    "Makefile",
    ".gitignore",
    ".markdownlint-cli2.yaml",
    ".pre-commit-config.yaml",
    ".prettierrc",
    ".git-blame-ignore-revs",
    "LICENSE-OFL-1.1.txt",
    "glyph_data/script.yaml",
    "site/gear-menu.js",
    "site/print.typ",
    "site/shared.css",
)
MAKE_TEST_EXEMPT_NAME_GLOBS = (("reference", "*.pdf"), ("reference", "*.png"), ("site", "*.svg"))
# The files under engine/ the font suite runs to check the `data-expect` pins and the any-of regression checks: the root conftest's imports under engine/validation/ and their import closure there (the grammar, the corpus collector, the checker, the shaper and the junction classifier), and the names of the fonts it checks (engine/tools/suite_fonts.py). `make_test_exempt` keeps them in the closure; engine/test_artifact_cycle.py holds the list to the root conftest's import closure.
MAKE_TEST_ENGINE_FILES = (
    "engine/tools/suite_fonts.py",
    "engine/validation/__init__.py",
    "engine/validation/check.py",
    "engine/validation/classify.py",
    "engine/validation/corpus.py",
    "engine/validation/expect.py",
    "engine/validation/shaping.py",
)
# The modules whose import closure under engine/ decides the bytes of the Mono and Junior the session publishes (`build_fonts.build` over `build_family.build_mono_and_junior`); `make_test_font_lines` hashes that closure.
MAKE_TEST_FAMILY_CODE_ROOTS = ("engine/pipeline/build_family.py", "engine/tools/build_fonts.py")
MAKE_TEST_RECIPES = ("test",)
MAKE_TEST_RECIPE_PINS = ("FORCE=",)


def make_test_exempt(path: str) -> bool:
    """Return whether a repo-relative path is outside gate:make-test's input closure. Each exemption follows from what the gate runs: pyright, and pytest test/ site/, whose session publishes the six fonts into site/ before it collects (engine/tools/build_fonts.py).

    Nothing the gate runs reads the exempt trees as files, apart from `MAKE_TEST_ENGINE_FILES`, which the exemption leaves in the closure, and the inputs of the published fonts, which `make_test_font_lines` covers. test/, site/, tools/ and conftest.py read neither a rune file nor the registry (glyph_data/script.yaml), and reach the rest of engine/ through the root conftest's imports of engine/tools/ leaf modules (`CONFTEST_EDGES` in engine/test_contracts_closure.py lists them) and through the publish, which runs in a child. The leaf modules are peak_rss for the summary line, cycle_timings for the pool record, memory_budget for the pool width, suite_fonts for the font paths, and pyright_gate for the type check's own skip; none of them can change what the suite asserts, and the engine suite tests each of them. The publish reads the runes and the pipeline, but the fonts it writes change only with what `make_test_font_lines` keys on (the build's Senior, and Mono's and Junior's code and data), so every other edit under engine/ or glyph_data/runes/ or to the registry stays outside the closure; `make_test_font_lines` keys the registry by its parsed document, so a comment in it does not trigger the gate. Markdown is not an input to any gate. .vscode/ is editor configuration, which no test reads. .github/ is CI, which runs the suite and is not read by it.

    reference/csur/ and the reference PDFs and PNGs are human reference material that nothing compiles. reference/DepartureMono-Regular.otf stays in the closure, because engine/pipeline/font_assembly.py bundles it into Mono and test/test_mono_matches_departure_mono.py shapes against it. site/icons/, site/*.svg, site/gear-menu.js and site/shared.css are browser assets the served pages load, and site/print.typ is the source `make all` typesets; nothing the gate runs reads any of them. site/shared.js stays, because test/test_shared.py runs it under node. The dotfile configs and the OFL text are read by git, the linters and the formatters, not by the suite.

    The name globs match only in their own directory, so site/*.svg does not match in a nested directory and reference/*.pdf does not match in reference/csur/. The Makefile is exempt as a file because the rule the suite runs is keyed by its `make -n` output instead (`make_test_recipe_lines`), so a comment, `all`, or a cycle or kernel target does not trigger the gate, while an edit to `test`, or one that stops the file parsing, still does.
    """
    if path in MAKE_TEST_ENGINE_FILES:
        return False
    if path.endswith(".md") or path in MAKE_TEST_EXEMPT_FILES:
        return True
    if any(path.startswith(prefix) for prefix in MAKE_TEST_EXEMPT_PREFIXES):
        return True
    directory, name = posixpath.dirname(path), posixpath.basename(path)
    return any(
        directory == parent and fnmatch.fnmatchcase(name, pattern)
        for parent, pattern in MAKE_TEST_EXEMPT_NAME_GLOBS
    )


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


def make_test_recipe_lines(root: Path) -> list[str] | None:
    """Return one hash line per Makefile rule the suite runs, in place of the Makefile's bytes. `make -n <target>` prints the recipe with every variable and function expanded, so hashing its output keys the gate on the `test` rule and nothing else in the file. stderr and the return code are hashed too, so a Makefile that no longer parses changes the key instead of hashing an empty recipe.

    The probe must return the same result whoever calls it. GNU make passes a caller's overrides to this child two ways. MAKEFLAGS and MFLAGS carry -j and command-line assignments to a sub-make, and both the cycle's gate and `make test`'s wrapper reach this as sub-makes, so those two variables are removed from the environment. The second way is the plain exported variable, which removing the flags does not reach: `make test FORCE=1` also puts FORCE=1 in the recipe's environment. So every variable the executed rules read is set on the probe's command line (MAKE_TEST_RECIPE_PINS), where an assignment overrides both the environment and MAKEFLAGS. Otherwise a forced run would record a fingerprint no plain run ever matches, so the override meant to run the suite once would make every later run re-run it, and a forced red would leave in place a green record it had just contradicted.

    Returns None when make is missing, which makes the caller run the gate, as a missing git does. A nonzero exit is hashed like any other output, not treated as None.
    """
    env = {key: value for key, value in os.environ.items() if key not in ("MAKEFLAGS", "MFLAGS")}
    lines = []
    for target in MAKE_TEST_RECIPES:
        try:
            result = subprocess.run(
                ["make", "-n", target, *MAKE_TEST_RECIPE_PINS],
                cwd=root,
                capture_output=True,
                check=False,
                env=env,
            )
        except OSError:
            return None
        digest = hashlib.sha256(
            b"\0".join((result.stdout, result.stderr, str(result.returncode).encode()))
        ).hexdigest()
        lines.append(f"make -n {target}\t{digest}")
    return lines


def make_test_font_lines(root: Path = ROOT) -> list[str]:
    """Return the lines gate:make-test's key carries for the six fonts the session publishes into site/ before it collects (`engine.tools.build_fonts`), so their bytes are not on disk to hash when the key is taken:

    - the Senior the session copies: engine/out/build/Senior-Regular.otf and Senior-Bold.otf by `fingerprint.font_content_digest`, which leaves out the version tables no shaper reads, and the glyph-facts sidecar beside them, which the checker reads in place, raw;
    - whether that Senior is current (`build_fonts.senior_staleness`), since the session refuses a stale one, so a key taken over a stale Senior never matches a green one;
    - what Mono and Junior are built from: the import closure under engine/ of `MAKE_TEST_FAMILY_CODE_ROOTS` by `fingerprint.path_lines`, and the spec's data (the rune files, the registry and the schemas) by `fingerprint.data_path_lines`, both prose-insensitive. Their other inputs, the other glyph_data/*.yaml files, postscript_glyph_names.yaml, reference/DepartureMono-Regular.otf and tools/, are in the closure as files.

    A rune or pipeline edit therefore moves the key only through the fonts the suite checks: an edit that leaves the Senior, its sidecar and Mono's and Junior's inputs as they were, as a comparison-side or review-side edit does, leaves the key alone.
    """
    from engine.pipeline import fingerprint, glyph_facts
    from engine.tools import build_fonts

    build_out = root / build_fonts.BUILD_OUT
    graph = contracts.ImportGraph(root)
    code = sorted(
        {
            rel
            for start in MAKE_TEST_FAMILY_CODE_ROOTS
            for rel in graph.closure(start)
            if rel.startswith("engine/") and rel.endswith(".py")
        }
    )
    data = [
        *fingerprint.rune_paths(root),
        *sorted((root / "engine" / "schema").glob("*.json")),
        fingerprint.registry_path(root),
    ]
    lines = []
    for style, name in build_fonts.SENIOR_SOURCES.items():
        senior = build_out / name
        lines.append(
            f"senior {style}\t{fingerprint.font_content_digest(senior) if senior.is_file() else 'missing'}"
        )
    lines += [
        f"senior_glyph_facts\t{_sha256_path(build_out / glyph_facts.FILE_NAME)}",
        f"senior_current\t{build_fonts.senior_staleness(root) or 'current'}",
    ]
    lines += [f"family_code {line}" for line in fingerprint.path_lines(root, [root / rel for rel in code])]
    lines += [f"family_data {line}" for line in fingerprint.data_path_lines(root, data)]
    return lines


def make_test_closure_fingerprint(root: Path = ROOT) -> str | None:
    """Return the content hash of gate:make-test's input closure: every file `make_test_exempt` keeps, read from the worktree (not the index) so uncommitted edits count, then the Makefile rule as `make_test_recipe_lines` hashes it, then the published fonts' lines (`make_test_font_lines`). A deleted tracked file hashes as absent, so deletions change the fingerprint too. None when git or make is unavailable, in which case the caller must run the gate."""
    files = make_test_closure_files(root)
    recipes = make_test_recipe_lines(root)
    if files is None or recipes is None:
        return None
    digest = hashlib.sha256()
    for rel in files:
        digest.update(f"{rel}\t{_sha256_path(root / rel)}\n".encode())
    for line in recipes:
        digest.update(f"{line}\n".encode())
    for line in make_test_font_lines(root):
        digest.update(f"{line}\n".encode())
    return digest.hexdigest()


def read_make_test_green(path: Path | None = None) -> dict | None:
    """Return the shared green record for `make test`, which engine.tools.make_test_gate writes on every green run, interactive or as gate:make-test."""
    return read_green_record(path if path is not None else cycle_paths.MAKE_TEST_GREEN)


def record_make_test_green(fingerprint: str, path: Path | None = None) -> None:
    record_green(path if path is not None else cycle_paths.MAKE_TEST_GREEN, fingerprint)


def prior_make_test_fingerprint(green_path: Path | None = None) -> str | None:
    """Return the closure fingerprint of the last green `make test` run, from the shared green record only. Every green run rewrites that record and `clear_contradicted_green` deletes it after a red one, so a copy anywhere else (the cycle summary keeps one for display) could bring back a fingerprint whose last run was red."""
    record = read_make_test_green(green_path)
    return record["fingerprint"] if record is not None else None


def make_test_skippable(fingerprint: str | None, recorded: str | None, *, force: bool) -> bool:
    """Return whether `make test` would check nothing: the pass is not forced and the closure's fingerprint equals the one in the shared green record. A fingerprint of None (no git or no make) never skips. The cycle's plan and engine.tools.make_test_gate both use this predicate. The plan reserves gate:make-test's cores and memory beside the corpus build only when it returns False, and its argv (`make_test_gate_argv`) passes the wrapper --force only when the plan was forced. So both decisions come from one fingerprint and one record, and no pass reserves cores for a gate that then skips."""
    return not force and fingerprint is not None and fingerprint == recorded


def make_test_gate_argv(*, force: bool) -> list[str]:
    """Return gate:make-test's argv: `make test`, with FORCE=1 on a forced pass (--fresh or --force-make-test). The wrapper reads the green record itself and would otherwise skip on the closure the flag forced. FORCE=1 reaches it as --force and also forces the type check inside it, as `make test FORCE=1` does by hand."""
    return ["make", "test", "FORCE=1"] if force else ["make", "test"]


BUILD_ARTIFACT_NAMES = (
    "Senior-Regular.otf",
    "Senior-Bold.otf",
    "Senior.glyph-facts.json",
    "kern-classes.json",
    "kern-inventory.json",
    "inputs_fingerprint.json",
)
ENGINE_GATE_HARNESS_PATHS = (
    ".github/workflows/deploy.yml",
    "README.md",
    "doc/glyph-names.md",
    "postscript_glyph_names.yaml",
    "reference/DepartureMono-Regular.otf",
    "site/extra-senior-words.html",
    "site/index.html",
    "site/the-manual.html",
    "tools/reflow_yaml.py",
)


def _closure_digest(root: Path, rel: str) -> str:
    """Return one file's digest for the engine suite's closure. Rune YAMLs get prose-insensitive hashes (`fingerprint.rune_document_digest`, which keeps the `kern` block's records), so a documentation edit does not re-run the gate. uv.lock is hashed by its dependency pins (`fingerprint.lock_digest`), so a changed pin re-runs the lane and the lock's copy of the project version does not; no test reads the project's own block, and the pinned packages decide the environment the lane runs in. A version bump still re-runs the whole lane, because it rewrites pyproject.toml, a global label (`contracts_closure.GLOBAL_LABELS`) hashed raw. Leaving the prose out is safe because of what the tests read from those files. The contracts tests that load the live runes check structure, settlement outcomes and round-trip identity, never the text of a `why`."""
    from engine.pipeline import fingerprint

    prose_insensitive = {
        "uv.lock": fingerprint.lock_digest,
    }
    digest = prose_insensitive.get(rel)
    if digest is None and rel.startswith("glyph_data/runes/") and rel.endswith(".yaml"):
        digest = fingerprint.rune_document_digest
    if digest is None:
        return _sha256_path(root / rel)
    try:
        return digest(root / rel)
    except OSError:
        return "absent"


def run_build_skip_lines(root: Path = ROOT) -> list[str]:
    """Return the per-file `label\\tdigest` lines behind `run_build_skip_fingerprint`: every data input and pipeline module individually (each data input by its prose-insensitive digest, `fingerprint.data_lines`, which leaves out every rune's `kern` block), one line per rune for its `kern` block alone (`fingerprint.kern_lines`, labeled `<rune label>#kern`), one line per file the full font compile reads for its non-Quikscript part (`fingerprint.font_data_lines`, labeled `<label>#font`), the contact allow-list by its own prose-insensitive digest, and uv.lock by its dependency pins (`fingerprint.lock_digest`, which ignores the project's own version block, so a version bump leaves the line unchanged and a fontTools or uharfbuzz bump changes it). The green record stores these lines, so a skip miss can name which input changed, and the pass can check whether every changed label is comparison-side (`comparison_side_label`) and rerun the gates over the artifacts on disk instead of rebuilding them.

    The allow-list is here and in no fingerprint component. Only the defect gate reads it, so a bless must change this key and should not change the corpus's stamp. A missing allow-list contributes no line, as `path_lines` drops a missing file.

    While the alphabet switch is set, the lines end with its token (`spec_load.alphabet_lines`), so a green over the whole alphabet never covers a small one, nor the reverse; the label is build-side (`comparison_side_label`), so a moved token rebuilds. With the switch unset no line is added.
    """
    from engine.pipeline import fingerprint, spec_load

    lines = fingerprint.data_lines(root)
    lines += fingerprint.kern_lines(root)
    lines += fingerprint.font_data_lines(root)
    allow = root / fingerprint.CONTACT_ALLOW_LABEL
    if allow.is_file():
        lines.append(f"{fingerprint.CONTACT_ALLOW_LABEL}\t{fingerprint.contact_allow_digest(allow)}")
    lines += fingerprint.path_lines(root, fingerprint.pipeline_code_paths(root))
    lines.append(f"uv.lock\t{_lock_line_digest(root / 'uv.lock')}")
    lines += spec_load.alphabet_lines()
    return lines


def _lock_line_digest(path: Path) -> str:
    """Return `fingerprint.lock_digest`, or `_sha256_path`'s `absent` for a missing lock, which is what the fake repos without one record."""
    from engine.pipeline import fingerprint

    try:
        return fingerprint.lock_digest(path)
    except OSError:
        return "absent"


def _files_of(lines: list[str]) -> dict[str, str]:
    return dict(line.split("\t", 1) for line in lines)


def run_build_skip_files(root: Path = ROOT) -> dict[str, str]:
    return _files_of(run_build_skip_lines(root))


def run_build_skip_fingerprint(root: Path = ROOT) -> str:
    """Return the content key over everything a full run_build reads: the data inputs and pipeline code per file, the contact allow-list the defect gate reads, and uv.lock's dependency pins. A key equal to the recorded green means a rerun would reproduce engine/out/build byte for byte. A different key says only that something changed; which lines changed decides whether the pass rebuilds or reruns only the gates (`gates_only_rerun`)."""
    return _digest_lines(run_build_skip_lines(root))


def capped_labels(entries: list[str], limit: int = 8) -> str:
    """Return a label list for one line of a report, with the entries past `limit` counted instead of printed. The moved-inputs note and the gates-only rerun note share it, so both cap the list the same way."""
    shown = ", ".join(entries[:limit])
    return f"{shown} and {len(entries) - limit} more" if len(entries) > limit else shown


def _moved_inputs(record: dict | None, current: dict[str, str]) -> list[tuple[str, str]] | None:
    """Return every input that changed since a green record that stored its per-file lines, as `(label, how)` pairs: changed, then new, then gone, each group sorted. None when the record is absent, has no `files` payload, or has no stored line that differs even though the fingerprint does. `moved_inputs_note` uses the `how` and `moved_input_labels` the bare labels, so neither computes the diff itself."""
    if record is None or not isinstance(record.get("files"), dict):
        return None
    stored = {name: value for name, value in record["files"].items() if isinstance(value, str)}
    moved = [
        (name, "changed") for name in sorted(stored.keys() & current.keys()) if stored[name] != current[name]
    ]
    moved += [(name, "new") for name in sorted(current.keys() - stored.keys())]
    moved += [(name, "gone") for name in sorted(stored.keys() - current.keys())]
    return moved or None


def moved_input_labels(record: dict | None, current: dict[str, str]) -> list[str] | None:
    """Return the bare labels of the inputs that changed since a green record, in `_moved_inputs` order. They are bare because the caller matches each one against `comparison_side_label`, and a label with a `(changed)` suffix would match nothing. None when there is no record to compare against or nothing differs."""
    moved = _moved_inputs(record, current)
    return None if moved is None else [name for name, _how in moved]


def comparison_side_label(label: str) -> bool:
    """Return whether one `run_build_skip_lines` label names an input the run_build gates read and the build does not. When only such inputs changed, a cycle can rerun the gates over the tables and font on disk (`run_build --gates-only`) instead of running a kernel fan-out that would produce byte-identical artifacts. Two kinds qualify, and `fingerprint.tables_value` covers neither, so an enumeration on disk stays current. The contact allow-list (`fingerprint.CONTACT_ALLOW_LABEL`) is read by the defect gate, which reads minted glyphs and mints none. The code `fingerprint.COMPARISON_CODE_MODULES` names (the comment beside it says what each entry is) never runs in the table build or the font compile, and engine/test_build_code_closure.py checks that the build never reaches it.

    `uv.lock` is not comparison-side, although the tables' stamp does not cover it either. It pins fontTools and uharfbuzz, so a bump can change the compiled font's bytes and what HarfBuzz does with them, and this must never permit reusing a font a different toolchain built. Its line covers only the dependency pins (`fingerprint.lock_digest`), so the project's own version bump leaves the line unchanged and never reaches this question.
    """
    from engine.pipeline import fingerprint

    if label == fingerprint.CONTACT_ALLOW_LABEL:
        return True
    return label in fingerprint.COMPARISON_CODE_MODULES


def kern_side_label(label: str) -> bool:
    """Return whether one `run_build_skip_lines` label is a rune's kern line (`fingerprint.kern_lines`): the one input that changes the font's `kern` lookup and nothing the tables, the settlement or the review corpus reads. It is not comparison-side, because the font must be compiled again."""
    from engine.pipeline import fingerprint

    return label.endswith(fingerprint.KERN_LINE_SUFFIX)


def font_data_label(label: str) -> bool:
    """Return whether one `run_build_skip_lines` label is a font-data line (`fingerprint.font_data_lines`): a file the full font compile reads for its Latin, Shavian, punctuation and symbol glyphs, its metadata or its Latin kern rules, which no table, settlement or review unit reads. It is not comparison-side, because the font must be compiled again."""
    from engine.pipeline import fingerprint

    return label.endswith(fingerprint.FONT_LINE_SUFFIX)


def font_only_label(label: str) -> bool:
    """Return whether one `run_build_skip_lines` label reaches the font alone: a kern line (`kern_side_label`) or a font-data line (`font_data_label`)."""
    return kern_side_label(label) or font_data_label(label)


def kern_only_rebuild(record: dict | None, current: dict[str, str]) -> list[str] | None:
    """Return the labels that changed since the last green build when every one reaches the font alone (`font_only_label`: a kern line or a font-data line), and None otherwise: no green record, nothing changed, or another input moved. The cycle then plans `run_build --kern-only`, which recompiles the font over the tables on disk, and the pass uses this to decide whether it may record a green. The kern blocks feed no table, no settlement and no review unit (`fingerprint.rune_file_digest` leaves them out), and the font data feeds only the non-Quikscript glyphs and the Latin features, which no Quikscript text reaches, so a prior green over every other input vouches for everything but the font, which the child rebuilds and reads back. The same second check as a gates-only rerun's applies: the tables on disk must carry the stamp of the sources on disk (`tables_stamped`), checked before and after the child. A third is its own: the Stage A record must match the sources on disk (`stage_a_current`), before the plan and before and after the child, because the child keeps the Manual-pin summary it finds, and only a matching record shows it is a passing run's. A red gates-only rerun or a declined Stage A record leaves none, and the cycle then rebuilds instead of re-evaluating what is there."""
    moved = moved_input_labels(record, current)
    if moved is None:
        return None
    return moved if all(font_only_label(label) for label in moved) else None


def gates_only_rerun(record: dict | None, current: dict[str, str]) -> list[str] | None:
    """Return the labels that changed since the last green build when every one is comparison-side, and None otherwise. The cycle uses this to plan a gates-only rerun, and the pass uses it to decide whether it may record a green. None means no gates-only rerun in three cases: there is no green record, nothing changed (the plain skip's case), or a build-side input changed and the tables must be rebuilt.

    The rerun is sound only with a second check. The prior green shows a completed build over every build-side input its key names passed, and the tables' stamp shows each enumeration's head names the current sources and semantics tokens. Together they are a necessary condition for the tables and Senior-Regular.otf on disk to be that build's, not a sufficient one: the green's key leaves out the semantics tokens, the stamp check reads only each enumeration's head, and neither covers the font's bytes, nor does the Stage A record, so a build that stops without settling the green can leave tables or an Senior-Regular.otf no passing build wrote (`run_build_gates.run_gates_only` says how). Only a green record vouches for a build: a failed run never vouches for the artifacts it left, even one that went red only at a comparison gate over tables stamped with its own inputs. Nothing `run_build_gates.gates_only_base` reads tells that red apart from a hand build that stamped its tables and then died in the emission, the compile or the read-back, leaving a stale or unverified Senior-Regular.otf beside the previous build's pipeline_summary.json, and the Stage A record cannot tell them apart either: a build and a `--gates-only` pass each delete it before rewriting anything and write it back only once their gates pass, so both leave none. So the build-side inputs a rerun stands on are always the last green's, and when one has moved since, the cycle rebuilds. The plan checks the stamp with the rest of what the rerun reads (`gates_only_base_present`). The cycle records a gates-only child's green on the prior green and on the stamp, checked before and after the child (`tables_stamped` in `_do_run_build`), and a standalone `--gates-only` pass checks the stamp through `run_build_gates.gates_only_base`.
    """
    moved = moved_input_labels(record, current)
    if moved is None:
        return None
    return moved if all(comparison_side_label(label) for label in moved) else None


def moved_inputs_note(record: dict | None, current: dict[str, str], limit: int = 8) -> str | None:
    """Return which inputs changed since a green record that stored its per-file lines, for the skip-miss message. None in the cases where `_moved_inputs` returns None."""
    moved = _moved_inputs(record, current)
    if moved is None:
        return None
    return capped_labels([f"{name} ({how})" for name, how in moved], limit)


def table_head_names() -> list[str]:
    """Every settlement configuration's windows head and head file (`table.windows_head_path`, `table.windows_head_file`), which `build_artifacts_present` requires beside `BUILD_ARTIFACT_NAMES`, so a configuration whose head is missing reads as a missing build."""
    from engine.pipeline import conform
    from engine.pipeline import table as table_module

    return [
        path.name
        for config in conform.SETTLEMENT_CONFIGS
        for path in (
            table_module.windows_head_path(Path(), config),
            table_module.windows_head_file(Path(), config),
        )
    ]


def build_artifacts_present(root: Path = ROOT) -> bool:
    """Return whether engine/out/build still holds everything a skipped run_build must leave: the two gate summaries the skip re-evaluates the gate from, the artifacts the corpus build reads, the kern assignment gate:conform checks the font against (`kern-classes.json`), the review app's kern inventory (`kern-inventory.json`, which only a build writes, so a build directory without it is rebuilt rather than skipped), and every settlement configuration's windows head and head file (`table_head_names`). It is the plain skip's predicate only. manual_pins_summary.json and the Stage A record are files a gates-only rerun writes without reading, so it is planned on what it reads instead (`gates_only_base_present`). A build and a gates-only pass each delete the Stage A record immediately before their first write here and write it back only once their gates pass, so after one that wrote here and then stopped or failed this is False and the plain skip is refused; the pass then reruns the gates or rebuilds, as `gates_only_rerun` decides."""
    build_out = root / "engine" / "out" / "build"
    names = [
        *(path.name for path in cycle_paths.BUILD_SUMMARY_FILES.values()),
        *BUILD_ARTIFACT_NAMES,
        *table_head_names(),
    ]
    return all((build_out / name).exists() for name in names)


def tables_stamped() -> bool:
    """Return whether the serialized window enumerations under engine/out/build were built from the sources on disk: `run_build.serialized_tables` against `run_build.tables_inputs`, the stamp the sweep and a gates-only pass check. It checks the artifacts themselves, not a record of a past run. The stamp shows which sources those tables came from, not which build wrote the Senior-Regular.otf beside them. A match is a necessary condition for a gates-only green; that green also needs a prior green over the same build-side inputs (`gates_only_rerun` says what each shows), because a failed run never vouches for the artifacts it left, and the build is deterministic, so the passing build need not be the one that stamped the tables. Even both together do not show that the tables or Senior-Regular.otf on disk are a passing build's (`run_build_gates.run_gates_only` names the gaps). `_do_run_build` checks it before and after a gates-only child to decide whether that child's green may be recorded, and the plan checks the stamp through `gates_only_base_present`. It takes no root parameter, like `deep_sweep.tables_stamped`, because the stamp is computed over the live repo, and a caller naming another tree would compare that tree's tables against this one's sources."""
    from engine.pipeline import run_build

    return run_build.serialized_tables(run_build.OUT_DIR, run_build.tables_inputs()) is not None


def gates_only_base_present() -> bool:
    """Return whether engine/out/build holds the build a gates-only rerun reads, by the check `run_build --gates-only` refuses on (`run_build_gates.gates_only_base`): the stamped tables, Senior-Regular.otf, a readable pipeline_summary.json, and the join tables. Sharing that check means a planned rerun is never one the child refuses over the build on disk, which would otherwise be planned again on every pass until `--fresh`. It leaves out what the rerun only writes, so after a rerun that went red at the defect or pin gate, the next pass re-runs the gates instead of rebuilding the tables. It is the second condition for a gates-only rerun; `gates_only_rerun` is the first. It takes no root parameter, like `tables_stamped`, because the stamp is computed over the live repo."""
    from engine.pipeline import run_build, run_build_gates

    return not isinstance(run_build_gates.gates_only_base(run_build.OUT_DIR, run_build.tables_inputs()), str)


def stage_a_current(root: Path = ROOT) -> bool:
    """Return whether the Stage A record under engine/out/build matches what a pass over the sources on disk would write. A kern-only rebuild runs only over a matching record and carries it forward, since it rewrites nothing the record vouches for but the font's `kern` lookup, which the corpus does not read (`run_build.kern_only_run`). A build and a gates-only pass each delete the record before they rewrite anything there, and write back the values they captured before reading their inputs only once their gates pass and those values still hold (`run_build_gates._settle_stage_a`); a gates-only pass also needs a prior green with no build-side input moved since it (`run_build_gates.run_gates_only`). So a matching record shows that the Manual-pin summary beside it was written by a run whose gates passed over these Stage A inputs (unless one was edited and reverted while that run ran, which `run_build_gates._settle_stage_a` cannot see), and that the tables and Senior-Regular.otf are the ones that run built, or checked over a green build's build-side inputs (`run_build_gates.run_gates_only` names the gaps in what such a check shows).

    The plain run_build skip requires it, so when run_build skips it always holds. On a gates-only rerun it decides before the pass runs whether the corpus can skip. The rerun reads the same Stage A inputs as the run that wrote the record, with that exception, and the same tables and font, since every run that rewrites those deletes the record first. Every build-side input the green's key names (`run_build_skip_lines`) is in a Stage A component except uv.lock, so the rerun also reads them over that run's build-side inputs, but for uv.lock. A gates-only pass writes the record only under the uv.lock of the green it stands on, and a build that records its green built under the uv.lock that green names. A passing build whose green is declined still writes the record, though: a hand build declines it when its key moves while it runs (uv.lock put back after the build began, say). The last green is then an older one, and when that build ran under another uv.lock than the older green's, a gates-only rerun standing on that green finds the record current: the corpus skips, and the rerun checks, under the green's uv.lock, a font another uv.lock built. Of the comparison-side inputs (`comparison_side_label`), the contact allow-list, which only the defect gate reads, is outside every Stage A component. So, outside that uv.lock gap and an edit reverted while the record's run ran, the rerun writes the same record.
    """
    from engine.pipeline import fingerprint

    recorded = fingerprint.read_stage_a(root / "engine" / "out" / "build")
    return recorded is not None and recorded == fingerprint.stage_a(root)


CONFORM_NO_SIDECAR_LINE = "behavior_classes\tabsent"


def conform_skip_lines(root: Path = ROOT, max_length: int = CONFORM_MAX_LENGTH_DEFAULT) -> list[str]:
    lines = deep_sweep_skip_lines(root)
    if lines is None:
        lines = [CONFORM_NO_SIDECAR_LINE]
    lines.append(f"max_length\t{max_length}")
    return lines


def conform_skip_files(root: Path = ROOT, max_length: int = CONFORM_MAX_LENGTH_DEFAULT) -> dict[str, str]:
    return _files_of(conform_skip_lines(root, max_length))


def conform_skip_fingerprint(root: Path = ROOT, max_length: int = CONFORM_MAX_LENGTH_DEFAULT) -> str:
    """Return the content key over what the per-edit sweep tests for, matching the deep sweep's key: the deep sweep's lines (`deep_sweep_skip_lines`: the behavior-class set the build enumerated from the emitted lookup, the font-compilation code in `COMPILE_CODE_FILES`, and the uharfbuzz version) plus the maximum length, so a green at a shorter maximum length cannot satisfy a longer one. A build that left no behavior-class sidecar contributes `CONFORM_NO_SIDECAR_LINE` instead of the classes, a key no sweep records a green under, since every sweep runs over a build that wrote one.

    The key leaves out the rune digests, the tables' stamp and Senior-Regular.otf's bytes, because a rune edit changes all three on every pass. After the crate's string replay (`run_build.run_replay_strings`, on every build), what the conformance sweep still checks is how HarfBuzz applies the shapes the emitted lookup gives it, which depends on a rule shape, the code that turns a plan into bytes, and the shaper. An edit that creates no new shape gives the conformance sweep nothing it has not already shaped, so its green stays valid. The class enumeration fails closed (`emit_gsub.behavior_classes`), so a new shape always changes the key. The accepted cost is that a disagreement only HarfBuzz can see waits for the next code change or deep sweep instead of the next rune edit. The set of texts the conformance sweep shapes is unchanged.
    """
    return _digest_lines(conform_skip_lines(root, max_length))


def deep_sweep_skip_lines(root: Path = ROOT) -> list[str] | None:
    """Return the deep sweep's record key lines: the behavior-class set the build enumerated (engine/out/build/behavior_classes.json, written by `emit_gsub.behavior_classes`), the font-compilation code that turns a plan into bytes (the pipeline modules in `COMPILE_CODE_FILES`, the FEA emitters, the repack, the compile and the font assembly it passes the font to, since an edit to the glyph compiler or the FEA emitter changes Senior-Regular.otf's bytes and must make this sweep and the per-edit sweep due together), and the shaper version. The code is hashed through `fingerprint.path_lines`, so rewording a docstring or a comment leaves both sweeps' greens valid. None when no build has left a sidecar, in which case the caller should run the cycle before asking whether the deep sweep is due.

    The key leaves out the rune digests and Senior-Regular.otf's bytes, because a rune edit changes both on every pass, and the deep sweep tests HarfBuzz behavior at a depth the per-edit sweep cannot reach. It tests the set of shapes the emitted lookup gives the shaper, so an edit that creates no new shape leaves nothing new for a deeper run to find, and its green stays valid. There is no maximum-length line: a deep sweep runs at any maximum length from the per-edit sweep's 4 up (5 by default), so the depth a green covered is stored in the record's payload and compared with >=. Hashing it into the key would make a length-6 green fail a length-5 question.

    Each class is its own line label, not a shared `class` label with the token as its value, so the per-file map behind the key (`_files_of`, stored in the green record) has one entry per token and `moved_inputs_note` can name the new shape, which is all the "due" report says.
    """
    import importlib.metadata

    from engine.pipeline import fingerprint
    from engine.pipeline.emit_gsub import BEHAVIOR_CLASSES_FORMAT

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
    lines += fingerprint.path_lines(root, [root / rel for rel in COMPILE_CODE_FILES])
    lines.append(f"uharfbuzz\t{importlib.metadata.version('uharfbuzz')}")
    return lines


def deep_sweep_skip_files(root: Path = ROOT) -> dict[str, str] | None:
    lines = deep_sweep_skip_lines(root)
    return None if lines is None else _files_of(lines)


def deep_sweep_skip_fingerprint(root: Path = ROOT) -> str | None:
    lines = deep_sweep_skip_lines(root)
    return None if lines is None else _digest_lines(lines)


def record_deep_sweep_green(
    fingerprint: str, max_length: int, files: dict[str, str] | None = None, path: Path | None = None
) -> int:
    """Write the deep sweep's green record and return the maximum length it records. It stores the maximum length the run swept as well as the key, because the record key ignores depth: `deep_sweep_status` reads the maximum length back to decide whether a run went deep enough for the depth asked about. When the record on disk is a green under the same key at a greater maximum length, the new record keeps that length, so a shallower run over the same shapes (a debugging `--max-length 4`) cannot make a deeper green read as due, and the caller's green line names the length kept. A green under a different key is replaced whatever its depth."""
    path = path if path is not None else cycle_paths.DEEP_SWEEP_GREEN
    existing = read_green_record(path)
    if existing is not None and existing["fingerprint"] == fingerprint:
        recorded = recorded_max_length(existing)
        if isinstance(recorded, int) and recorded > max_length:
            max_length = recorded
    _record_outcome(path, {"fingerprint": fingerprint, "max_length": max_length, "files": files})
    return max_length


def record_deep_replay_green(
    walked: dict[str, str],
    max_length: int,
    structure: str | None,
    carry: Collection[str] = (),
    path: Path | None = None,
    imports: str | None = None,
) -> int:
    """Write the deep replay's green record (`engine.tools.deep_replay`) and return the maximum length it records. The record holds every rune's prose-insensitive digest as a walk covered it (under `files`, so `moved_inputs_note` can name what changed since), one maximum length for all of them, the structure stamp (`structure`, `deep_replay_structure_stamp`), the walked tables' imported windows (`imports`, `tables_imports_digest`), and a fingerprint over the rune lines so `read_green_record` reads it like every other record. A rune the record does not have counts as changed.

    `walked` holds the runes whose texts this walk settled at `max_length`, at the digests the walk read. `carry` names runes the walk did not cover whose claims in the record on disk the new record keeps, at the record's digests. A walked rune's depth is `max_length`, raised to the recorded maximum length when its digest matches the record's; a carried rune's depth is the recorded maximum length. The record stores the least depth over its runes, so it never claims a depth some rune lacks (a length-6 family walk beside runes walked only to 5 records 5), and never drops a depth every rune still has (a length-5 walk or deep sweep over runes the record holds at 6 records 6). The structure stamp and the imported windows are the record's identity beside the rune digests, so a record under another structure stamp or other imported windows carries no claim and lends no depth: a change to the crate, the semantics tokens, the cross-rune structure or the capability features can change any text's answer, and the imported windows can reshape a configuration's rules for texts that name no edited rune (`doc/engine-design.md` §10, "Imported windows"). Nothing is carried from a record without a maximum length or a digest map.
    """
    path = path if path is not None else cycle_paths.DEEP_REPLAY_GREEN
    existing = read_green_record(path)
    recorded = recorded_max_length(existing) if existing is not None else None
    prior = existing.get("files") if existing is not None else None
    if not isinstance(recorded, int) or not isinstance(prior, dict):
        recorded, prior = max_length, {}
    if existing is None or existing.get("imports") != imports or existing.get("structure") != structure:
        prior = {}
    carried = {name: prior[name] for name in carry if name in prior and name not in walked}
    depths = [recorded] if carried else []
    depths += [
        max(max_length, recorded) if prior.get(name) == digest else max_length
        for name, digest in walked.items()
    ]
    depth = min(depths, default=max_length)
    runes = {**carried, **walked}
    lines = [f"{name}\t{digest}" for name, digest in sorted(runes.items())]
    _record_outcome(
        path,
        {
            "fingerprint": _digest_lines(lines),
            "max_length": depth,
            "structure": structure,
            "imports": imports,
            "files": runes,
        },
    )
    return depth


def tables_imports_digest(root: Path | None = None) -> str | None:
    """Return `run_build.imports_digest` over the table heads a tree's build left under `engine/out/build`, the windows each configuration's table imported from the others (`engine/kernel-rs/src/crossconfig.rs`), or None when some settlement configuration's head or head file is missing, unreadable or not what its head records (`table.read_windows_head`). The deep replay's record stores it, and a walk whose tables import other windows covers every text (`record_deep_replay_green`). Only the head files are read. The root defaults at call time, as in `deep_replay_green_path`."""
    from engine.pipeline import conform, run_build
    from engine.pipeline import table as table_module

    out_dir = Path(ROOT if root is None else root) / "engine" / "out" / "build"
    tables = {}
    for config in conform.SETTLEMENT_CONFIGS:
        try:
            _stamp, tables[config] = table_module.read_windows_head(out_dir, config)
        except OSError, ValueError:
            return None
    return run_build.imports_digest(tables)


def deep_replay_structure_stamp(root: Path | None = None, spec: ResolvedSpec | None = None) -> str:
    """Return the deep replay record's structure stamp: the hash of the crate's manifest, lock and sources (the crate's members of `fingerprint.table_code_paths`, through `fingerprint.path_lines`, whose `code_file_digest` drops the Rust sources' whole-line comments but hashes Cargo.toml and Cargo.lock as raw bytes, so a comment-only manifest edit moves the stamp), the semantics tokens (`kernel_exec.enumeration_tokens`), the cross-rune structure (`spec_load.spec_structure_digest`) and the capability features (`spec_load.capability_features`). `spec` is loaded from the tree at `root` when not given, and the root defaults at call time, as in `deep_replay_green_path`. It leaves out the build-side Python and the non-rune data files, which `run_build.locality_lines` hashes for the build's own replay, so an edit there leaves the deep replay current unless it moves a rune digest, the tables' imported windows or something this stamp hashes. A record under another stamp carries no claim (`record_deep_replay_green`), so `deep_replay_status` reports it due and a walk without `--families` covers every text."""
    from engine.pipeline import fingerprint, kernel_exec, spec_load

    root = Path(ROOT if root is None else root)
    if spec is None:
        spec = spec_load.load_spec(
            root / spec_load.DEFAULT_RUNES_DIR.relative_to(spec_load.REPO_ROOT),
            root / spec_load.DEFAULT_REGISTRY_PATH.relative_to(spec_load.REPO_ROOT),
            root / spec_load.DEFAULT_SCHEMA_DIR.relative_to(spec_load.REPO_ROOT),
        )
    kernel = root / "engine" / "kernel-rs"
    lines = fingerprint.path_lines(
        root, [path for path in fingerprint.table_code_paths(root) if kernel in path.parents]
    )
    lines += [
        "semantics\t" + "+".join(kernel_exec.enumeration_tokens()),
        f"structure\t{spec_load.spec_structure_digest(spec)}",
        "capabilities\t" + ",".join(spec_load.capability_features(spec)),
    ]
    return _digest_lines(lines)


def recorded_max_length(record: dict) -> object:
    """Return the maximum length a deep sweep or deep replay green record reached: its `max_length` field, or the `horizon` field an older record stores it under."""
    return record.get("max_length", record.get("horizon"))


def deep_replay_moved(record: dict | None, runes: dict[str, str]) -> list[str]:
    """Return the runes whose texts the next deep replay has to walk, sorted: every rune whose current prose-insensitive digest differs from the record's, including runes the record never walked. Every rune when there is no record. A rune in the record that no longer exists is ignored, since no text names it."""
    if record is None or not isinstance(record.get("files"), dict):
        return sorted(runes)
    recorded = record["files"]
    return sorted(name for name, digest in runes.items() if recorded.get(name) != digest)


def deep_replay_green_path(root: Path | None = None) -> Path:
    """Return where a tree keeps the deep replay's record: `cycle_paths.DEEP_REPLAY_GREEN` for the live repo, and the same relative place under any other root, so a status for an invented tree never opens the live record. The root defaults at call time, so a test that re-roots the module re-roots this too."""
    if root is None or Path(root).resolve() == ROOT.resolve():
        return cycle_paths.DEEP_REPLAY_GREEN
    return Path(root) / "engine" / "out" / "deep-replay-green.json"


def _deep_replay_command(max_length: int) -> str:
    """Return the bare `make replay-deep` invocation that brings the deep replay current at `max_length`, with `--max-length` when `max_length` is past the walk's default."""
    if max_length > DEEP_REPLAY_MAX_LENGTH_DEFAULT:
        return f"`make replay-deep ARGS='--max-length {max_length}'`"
    return "`make replay-deep`"


def deep_replay_status(
    root: Path | None = None, max_length: int = DEEP_REPLAY_MAX_LENGTH_DEFAULT
) -> tuple[str, str]:
    """Return whether the deep replay is current for the runes and tables on disk, as (status, note) for the cycle's one-line report beside the deep sweep's. `current` means the record has every rune at its current digest, was walked over the tables' current imported windows (`tables_imports_digest`) under the current structure stamp (`deep_replay_structure_stamp`), and reached this depth or deeper. `due` names what moved since the recorded walk: the imported windows, what the structure stamp hashes, the runes whose content changed, or the shallower depth it reached, and the `make replay-deep` that clears it at this depth. That is the bare walk at this depth, which walks every text when the imported windows or the structure stamp moved, since the record then carries no claim, or when the record is shallower than it, since a walk over the moved runes alone carries the rest at the depth the record holds (`record_deep_replay_green`). `never-run` means there is no record `read_green_record` can read, and names the same bare walk at this depth, which walks every text because there is no last walk to cut a delta against. This only reports: the deep replay is never a cycle gate, for the cost `engine/tools/deep_replay.py` states."""
    from engine.pipeline import fingerprint

    root = ROOT if root is None else root
    record = read_green_record(deep_replay_green_path(root))
    if record is None:
        return (
            "never-run",
            f"no deep replay has been recorded; run {_deep_replay_command(max_length)}, which walks every text",
        )
    moved = deep_replay_moved(record, fingerprint.rune_digests(root))
    recorded = recorded_max_length(record)
    shallow = not isinstance(recorded, int) or recorded < max_length
    command = _deep_replay_command(max_length)
    if record.get("imports") != tables_imports_digest(root):
        return (
            "due",
            f"the windows the tables import from one another moved since the last length-{recorded} walk, which can reshape rules for texts that name no edited rune; run {command}, which walks every text",
        )
    if record.get("structure") != deep_replay_structure_stamp(root):
        return (
            "due",
            f"the crate, the semantics tokens, the cross-rune structure or the capability features moved since the last length-{recorded} walk; run {command}, which walks every text",
        )
    if moved:
        return (
            "due",
            f"{capped_labels(moved)} moved since the last length-{recorded} walk; run {command}",
        )
    if shallow:
        return (
            "due",
            f"the recorded deep replay reached maximum length {recorded}, shorter than {max_length}, which only a walk over every text raises; run {command}",
        )
    return "current", f"maximum length {recorded}"


def deep_sweep_status(root: Path = ROOT, max_length: int = DEEP_SWEEP_MAX_LENGTH_DEFAULT) -> tuple[str, str]:
    """Return whether the periodic deep sweep is current for what the build emits, as (status, note) for the cycle's one-line report. `current` means a green record matches the record key at this depth or deeper. `due` means something the deep sweep tests for has changed (a new rule shape, the compilation path, the shaper) or the recorded run was shallower than asked, and `make conform-deep` is the fix. `never-run` means there is no record, and `unknown` means no build has left a behavior-class sidecar to key on. This only reports: the deep sweep is never a cycle gate."""
    fingerprint = deep_sweep_skip_fingerprint(root)
    if fingerprint is None:
        return "unknown", "no behavior-class sidecar yet; it lands with the next build"
    record = read_green_record(cycle_paths.DEEP_SWEEP_GREEN)
    if record is None:
        return "never-run", "no deep sweep has been recorded; run `make conform-deep`"
    if record["fingerprint"] != fingerprint:
        files = deep_sweep_skip_files(root)
        moved = moved_inputs_note(record, files) if files is not None else None
        detail = f"{moved}; " if moved else ""
        return (
            "due",
            f"{detail}the build emits shapes the last deep sweep never saw; run `make conform-deep`",
        )
    recorded = recorded_max_length(record)
    if not isinstance(recorded, int) or recorded < max_length:
        return (
            "due",
            f"the recorded deep sweep reached maximum length {recorded}, shorter than {max_length}; run `make conform-deep`",
        )
    return "current", f"maximum length {recorded}"


def engine_gate_closure_files(root: Path) -> list[str] | None:
    """Return every tracked or untracked-unignored repo file on disk that the engine pytest suite can read, the base of the suite's input closure; a tracked file deleted in the working tree has nothing to read, so it is left out. That is engine/ and glyph_data/ (without Markdown and the paths in `cycle_paths.ENGINE_GATE_EXEMPT_PREFIXES`: the JS-only jstests and the contact allow-list), the root conftest.py, pyproject.toml and uv.lock, and the harness list ENGINE_GATE_HARNESS_PATHS. The harness list is what the suite reads outside those trees, and it is why the Markdown filter has an exception: the filter drops prose no test opens, but engine/test_review_enrich.py checks the corpus's letter table against doc/glyph-names.md.

    The harness list comes from an audit of every file the suite opens over a green run, not from the tree, and every entry has a named reader. engine/pipeline/manual_pins.py and the checker's tests collect pins from the three site corpora through engine/validation/corpus.py, and the grammar (engine/validation/expect.py) reads postscript_glyph_names.yaml at the repo root when it is imported. Its one tools/ entry is the runes' reflow, which engine/test_review_kern_writer.py imports to hold the kern editor's form to it; the font compile's modules are under engine/pipeline/, which the tree covers. The Mono builds in engine/test_build_family.py read reference/DepartureMono-Regular.otf, whose glyphs and layout tables engine/pipeline/font_assembly.py copies into Mono. engine/test_review_build.py checks the review corpus's feature descriptions against README.md's stylistic-set list, and engine/test_release_fonts.py holds .github/workflows/deploy.yml's calls of the release upload tool (`engine/tools/release_fonts.py`) to the names the tool uses.

    The allow-list is exempt because no test reads the live file (only a fake repo writes one), so a bless would re-run the whole suite for nothing. None when git is unavailable, in which case the caller must run the gate.
    """
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
                "engine/",
                "glyph_data/",
                "conftest.py",
                "pyproject.toml",
                "uv.lock",
                *ENGINE_GATE_HARNESS_PATHS,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    paths = {entry for entry in result.stdout.split("\0") if entry}
    harness = set(ENGINE_GATE_HARNESS_PATHS)
    return sorted(
        path
        for path in paths
        if (path in harness or not path.endswith(".md"))
        and not any(path.startswith(prefix) for prefix in cycle_paths.ENGINE_GATE_EXEMPT_PREFIXES)
        and (Path(root) / path).exists()
    )


def contracts_fingerprint(root: Path) -> str | None:
    """Return the contracts roster key from `contracts_closure`, whose per-label digests define what the key covers."""
    return contracts_closure(root)[0]


def contracts_closure(root: Path) -> tuple[str | None, dict[str, str] | None]:
    """Return the engine suite's input closure as the key and the per-label digest map the key is computed from. One pass over the files produces both, and because the map is the key's only source, every input that can change the key is a label the per-test selection can see change. The closure covers the repo files from `engine_gate_closure_files`, which has already dropped the exempt paths, so a contact-signature bless changes nothing here. The closure contains no build artifact (the audit guard in engine/conftest.py keeps every test off the site fonts too), so an artifact-only cycle re-runs nothing here, and a rebuild, which writes only under engine/out, cannot change the key during a pass. A version bump re-runs the whole lane through pyproject.toml, which is hashed raw. The harness list is included because the suite reads those files outside engine/ and glyph_data/. The rune files get prose-insensitive hashes, because the tests that load the live spec read structure, not prose. The verdict store is not in the closure (the suite uses it only through fixtures), so a verdict leaves the suite's key as it was. None when git is unavailable, in which case the caller must run the suite."""

    files = engine_gate_closure_files(root)
    if files is None:
        return None, None
    labels = {rel: _closure_digest(root, rel) for rel in files}
    return _digest_lines([f"{label}\t{digest}" for label, digest in labels.items()]), labels


def corpus_build_skippable(
    root: Path = ROOT, review_out: Path | None = None, ignore: tuple[str, ...] = ()
) -> bool:
    """Return whether rebuilding the review corpus would reproduce its content byte for byte, so the build can be skipped and the verdict store keeps its stamp. The cycle builds the table-diff corpus (`engine.review.build`), so True only for a corpus of that mode whose manifest's recorded inputs fingerprint equals the one a build would stamp now (`status.table_diff_inputs` over the current snapshot under `root` and the tables' stamp of engine/out/build, which a build copies, `snapshot.tables_stamp`), every shard the manifest names present, engine/out/build/Senior-Regular.otf byte for byte the after font the manifest records, as the readiness check's freshness row compares it, and the corpus complete (`corpus_complete`). A corpus of another mode never is, so the first pass of a tree that serves one builds the table-diff corpus. `generated_at` is derived from the inputs' mtimes, so a rebuild after mtime-only changes (git checkout, touch) could restamp it with identical content; skipping keeps the stamp, and with it the store's alignment with the corpus.

    The after font is compared whole, `kern` lookup included, because the corpus's kerning units show that lookup and its kern bucket reads engine/out/build/kern.tsv, which a kern edit rewrites with the font; the `font_inputs` component hashes the runes' kern blocks too. So a kern-only rebuild rebuilds the corpus.

    `ignore` names fingerprint components left out of the comparison, for a caller asking a narrower question than byte identity, with the same hard/warn split the readiness check's freshness row uses. The cycle asks three questions in turn. The strict one comes first, since a corpus that reproduces byte for byte needs nothing done. When only an ASSET_COMPONENTS member differs, the cycle copies those assets over the served corpus and restamps that one component (`assets-refresh`) instead of rebuilding units that cannot have changed. When the live corpus fails both, `promotable_corpus` asks the strict question of a corpus built beside it, and the pass's land swaps that directory in when it matches. A component missing from either side still fails the comparison: only a component present in both the recorded and the expected set can be ignored.
    """
    from engine.pipeline import fingerprint
    from engine.review import status

    corpus = review_out if review_out is not None else REVIEW_OUT
    try:
        manifest = json.loads((corpus / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    if not isinstance(manifest, dict) or manifest.get("mode") != "table-diff":
        return False
    recorded = manifest.get("inputs_fingerprint")
    if not isinstance(recorded, dict):
        return False
    build_out = root / "engine" / "out" / "build"
    expected = status.table_diff_inputs(root, build_out=build_out)
    ignored = set(ignore) & set(recorded) & set(expected)
    if {key: value for key, value in recorded.items() if key not in ignored} != {
        key: value for key, value in expected.items() if key not in ignored
    }:
        return False
    try:
        parts = [part for meta in manifest["classes"] for part in shards.class_shards(meta)]
    except KeyError, TypeError, AttributeError:
        return False
    if not all((corpus / part).exists() for part in parts):
        return False
    try:
        after_sha = manifest["fonts"]["after"]["sha256"]
    except KeyError, TypeError:
        return False
    if not isinstance(after_sha, str):
        return False
    try:
        if fingerprint.file_sha256(build_out / "Senior-Regular.otf") != after_sha:
            return False
    except OSError:
        return False
    return corpus_complete(corpus)


def _manifest_stamp_at(corpus: Path) -> str | None:
    try:
        stamp = json.loads((corpus / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError, AttributeError:
        return None
    return stamp if isinstance(stamp, str) else None


def next_corpus_dir(live: Path | None = None) -> Path:
    """Return where a pass builds its corpus beside the served one: `review.next` beside engine/out/review. The land swaps it in, so it is on the served corpus's filesystem."""
    live_dir = live if live is not None else REVIEW_OUT
    return live_dir.with_name(f"{live_dir.name}.next")


def corpus_complete(corpus: Path) -> bool:
    """Return whether the corpus at `corpus` is one a build finished: its manifest parses, and the files a build writes after it, both app sidecars, are stamped for that manifest (`app_index.outputs_not_current`, the one check of a finished corpus, which `status`'s freshness check takes too; with the comparison with today's inputs, these are the checks `corpus_build_skippable` makes)."""
    try:
        manifest = json.loads((corpus / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    if not isinstance(manifest, dict):
        return False
    return not app_index.outputs_not_current(corpus)


def recover_next_corpus(live: Path | None = None, *, delete: bool = True) -> str | None:
    """Keep a `review.next` an earlier pass left only when it is complete (`corpus_complete`), and delete anything else there: a build that died leaves a partial tree, which must not be promoted. A complete one stays, to be promoted when it reproduces this pass's inputs (`promotable_corpus`); otherwise the pass's corpus build deletes it before it builds there (`_run_cycle`), since the table-diff build starts from an empty directory. The `.discard` trees a land's delete did not finish (`landing.discard_paths`) are deleted too. `delete=False` is the dry run's form, which deletes nothing. Returns the line to print, or None when there is no `review.next`."""
    if delete:
        for discard in landing.discard_paths(live if live is not None else REVIEW_OUT):
            shutil.rmtree(discard, ignore_errors=True)
    target = next_corpus_dir(live)
    if not target.exists():
        return None
    if corpus_complete(target):
        return f"Kept {target}, the complete corpus an earlier pass built beside the served one."
    if not delete:
        return f"Left {target}, a corpus an earlier pass did not finish, for the next real pass to delete."
    shutil.rmtree(target, ignore_errors=True)
    return f"Deleted {target}, a corpus an earlier pass did not finish."


def promotable_corpus(
    root: Path = ROOT, summary_path: Path | None = None, live: Path | None = None
) -> Path | None:
    """Return the corpus built beside the live one that a live pass can land instead of rebuilding, or None. Candidates are checked in order: `review.next` beside the live corpus (`next_corpus_dir`), which a pass stopped after its build leaves complete; the `plan.review_out` the last cycle summary recorded (a repo-relative string, resolved against `root`), which a staging pass (`--review-out`) wrote as a whole corpus (shards and sidecars) where the live pass never reads; then `var/staged-review` under `root`, the conventional staging directory. Every staging path derives from `root`, so a scratch repo never reads the live staged corpus. The caller asks only on a pass whose run_build skipped or reproduced its Stage A, because the check compares against the Stage A run_build recorded on disk, so a corpus built before a rune edit is never landed over it.

    A candidate is promotable when it is a directory other than the live one, on the live directory's filesystem (the land swaps the two trees, which cannot cross filesystems, and a plan must never print a move it cannot make), when `corpus_build_skippable` returns True for it (that function defines "reproduces these inputs byte for byte", including the after font, the three stamped sidecars and the unit store), and when its `generated_at` is not older than the live corpus's. The byte-identity check cannot supply the stamp condition: `generated_at` is the latest input mtime, not a build time (`_generated_at` in engine/review/build.py), so a staged corpus can have a stamp older than the corpus it would replace, and a backwards promotion would land a corpus older than the one the store was carried onto. An unreadable manifest rules the candidate out, which costs only a rebuild.
    """
    live_dir = live if live is not None else REVIEW_OUT
    summary = summary_path if summary_path is not None else root / "engine" / "out" / "cycle_summary.json"
    candidates: list[Path] = [next_corpus_dir(live_dir)]
    try:
        recorded = json.loads(summary.read_text()).get("plan", {}).get("review_out")
    except OSError, ValueError, AttributeError:
        recorded = None
    if isinstance(recorded, str) and recorded:
        candidates.append(root / recorded if not Path(recorded).is_absolute() else Path(recorded))
    candidates.append(root / "var" / "staged-review")
    live_stamp = _manifest_stamp_at(live_dir)
    if live_stamp is None:
        return None
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        try:
            if candidate.resolve() == live_dir.resolve():
                continue
            if candidate.stat().st_dev != live_dir.parent.stat().st_dev:
                continue
        except OSError:
            continue
        stamp = _manifest_stamp_at(candidate)
        if stamp is None or stamp < live_stamp:
            continue
        if corpus_build_skippable(root, review_out=candidate):
            return candidate
    return None


def recover_superseded_corpus(live: Path | None = None, *, delete: bool = True) -> str | None:
    """Handle whatever a land's three-rename fallback left under the `.superseded` name (`landing.exchange_dirs`), before a pass checks whether it has a corpus. Beside a live tree it is an old corpus whose move did not finish, and it is deleted. Alone, it is the live corpus that a land which died between the renames had moved aside, and one rename puts it back, so the next pass reads the corpus it had instead of starting as a first run. It runs at the start of every pass, after the land recovery, because the green-finish retention never runs on the failed or first-run passes that leave the tree behind. `delete=False` is the dry run's form: the delete cannot be undone and no plan question reads the tree it removes, so the tree stays for the next real pass, while the put-back still runs, because every plan question reads the live corpus and a dry run's plan must be the one a real pass follows. Returns the line to print, or None when there was nothing to do."""
    live_dir = live if live is not None else REVIEW_OUT
    superseded = live_dir.with_name(f"{live_dir.name}.superseded")
    if not superseded.exists():
        return None
    if live_dir.exists():
        if not delete:
            return f"Left {superseded}, the corpus a land replaced, for the next real pass to delete."
        shutil.rmtree(superseded, ignore_errors=True)
        return f"Deleted {superseded}, the corpus a land replaced."
    os.replace(superseded, live_dir)
    return f"Put {superseded} back as the live corpus; the land it stepped aside for did not finish."


def _defect_gate_failure(pipeline: dict) -> str | None:
    """Return the defect gate's failure from pipeline_summary.json's contents, or None when it passed."""
    defect_errors = pipeline.get("defect_errors") or []
    if not defect_errors:
        return None
    return f"{len(defect_errors)} defect-gate error(s): {defect_errors[0]}"


def evaluate_run_build_gate(pipeline: dict, manual_pins: dict) -> CheckResult:
    """Decide whether the build passed from its two summary JSONs: defect_errors and the Manual-pin gate. The pin gate is run_build_gates's own (`manual_pin_gate_failure`), including its scope, so a gate that replayed nothing cannot pass here either."""
    from engine.pipeline.run_build_gates import manual_pin_gate_failure

    failures: list[str] = []

    defect_failure = _defect_gate_failure(pipeline)
    if defect_failure is not None:
        failures.append(defect_failure)

    pin_failure = manual_pin_gate_failure(manual_pins)
    if pin_failure is not None:
        failures.append(pin_failure)

    return CheckResult(
        check="build",
        outcome="red" if failures else "green",
        status="FAILED" if failures else "green",
        failures=failures,
        failed_ids=[],
    )


def evaluate_conform_gate(summary: dict | None) -> CheckResult:
    """Evaluate gate:conform from conform_summary.json's contents (None when the subprocess wrote none). `pass` is the outcome, and the conformance sweep fails in only one way: a font-versus-settlement divergence, which is a compiler defect. Whether the font holds every rule the build planned is checked by read-back inside run_build on every build, and dead generated rules by the build's witness stage (`run_build.run_rule_witnesses`, over the certificates the crate writes beside the rules); neither reaches this summary. The sweep reports a count of divergences, not named cases, so there are no failed ids: a divergence names a window, and the summary's exemplars list the first of them."""
    if summary is None:
        return CheckResult(
            check="conform",
            outcome="red",
            status="FAILED (no conform_summary.json)",
            failures=["conform gate: run_build --conform-only wrote no summary"],
            failed_ids=[],
        )
    failures: list[str] = []
    if summary.get("divergences"):
        failures.append(f"conform gate: {summary['divergences']} font-vs-settle divergence(s)")
    if not summary.get("pass") and not failures:
        failures.append("conform gate: pass is false")
    return CheckResult(
        check="conform",
        outcome="red" if failures else "green",
        status="FAILED" if failures else "green",
        failures=failures,
        failed_ids=[],
    )


def conform_gate_argv(jobs: int, max_length: int = CONFORM_MAX_LENGTH_DEFAULT) -> list[str]:
    argv = ["uv", "run", "python", "-m", "engine.pipeline.run_build", "--conform-only", "--jobs", str(jobs)]
    if max_length != CONFORM_MAX_LENGTH_DEFAULT:
        argv += ["--conform-max-length", str(max_length)]
    return argv


STEP_DESCRIPTIONS = {
    "build": "Builds the tables for every settlement configuration in the Rust kernel (the ss10 overlay settles nothing and gets none), mints the glyphs, emits GSUB and GPOS, compiles the font, and reads it back. Then runs the defect gates and the Manual-pin gate over what it built.",
    "build:gates-only": "Reruns the defect gates and the Manual-pin gate over the tables and font already on disk, rebuilding nothing. Taken when only comparison-side inputs moved since the last green build.",
    "build:kern-only": "Recompiles the font over the tables already on disk after a kern edit or an edit to the font's non-Quikscript data: the kern stage, the defect gates, the GSUB and GPOS emission, the compile and read-back. Taken when only runes' kern blocks or the font data moved since the last green build, which no table or settlement reads.",
    "corpus-build": "Builds the table-diff corpus: the accepted snapshot of the build (engine/review-snapshot.json names it, var/keep/review-snapshot/ holds it) against engine/out/build, each unit one visible change, into engine/out/review.next beside the served corpus (in place only on a first run).",
    "assets-refresh": "Overwrites each served copy of the review app's JS, CSS, and HTML whose bytes changed, leaving the others in place, and restamps only the manifest's static component. No shard or sidecar moves, so the open tab's store stays aligned.",
    "land": "Moves the new corpus into place and carries the verdict store onto it by unit id, under the verdict store's lock, in one child that a stop signal does not interrupt: a verdict whose unit the new corpus still has stays, a skip is dropped, the rest go to var/verdict-orphans/, and the corpus is swapped in with one rename. The open tabs then move onto it.",
    "gates": "The four post-build gates, skipped together under --skip-gates.",
    "gate:js": "Runs the review app's node test suite over its JavaScript. Fast, and independent of every build artifact.",
    "gate:conform": "Shapes the compiled font with HarfBuzz over the swept texts, up to the pass's maximum length (`CONFORM_MAX_LENGTH_DEFAULT` unless `--conform-max-length` sets it), and checks it against a fresh re-settlement window by window, the split-buffer check included; the ss10 overlay runs its own short-text case (`conform.OVERLAY_MAX_LENGTH`) against the bare rendering. The check that HarfBuzz does what the tables say over every rule shape the lookup emits; that the tables are complete over the same texts is run_build's string replay, on every build.",
    "gate:engine-contracts": "Runs the engine suite: every test whose subject is the code, over checked-in fixtures and the hermetic mini bundle. Reads no live build artifact.",
    "gate:make-test": "Runs the main font suite and pyright over the whole tree, the same make test you run by hand, once the build gate has passed: its session publishes the six fonts into site/, the Senior that build leaves among them, and checks them. Skips when its input closure, the fonts' inputs among it, is unchanged since its last green run.",
    "job-costs": "Checks the recorded per-worker peaks against the memory-budget constants that size every fan-out. A drift here means a width somewhere is derived from stale numbers.",
    "retention": f"Prunes the build logs a green cycle leaves behind beyond the last {cycle_paths.BUILD_LOGS_KEEP}.",
}


def step_description(name: str) -> str:
    """Return a step's banner description from `STEP_DESCRIPTIONS`, printed on every run. The descriptions are kept beside the step definitions, not in a document, because a reader wants to know what a step checks while watching it run. Two keys are variants, not steps: `run_build:gates-only` is the gates-only rerun, which spawns under that name and reports under run_build's row, and `gates` is the placeholder --skip-gates leaves in place of the four gates. `_run_step` calls this instead of reading the plan because it receives a name, not a plan, and because one of the things it spawns, the job-costs diff, is a child of a step, for which the empty string is correct."""
    return STEP_DESCRIPTIONS.get(name, "")


SUBSTEP_PARENTS = {"job-costs-diff": "job-costs"}


@dataclass
class Step:
    """One row of the plan. `skipped` is set explicitly instead of derived from `argv`, because `argv is None` also describes the retention step, which do real work in this process, and the `gates` placeholder, which stands for four steps. The run/skip column and the counts line use `skipped`, so a step that runs without spawning anything still shows as running.

    The job-costs diff the job-costs step spawns is a child of a step, not a row of the plan, and SUBSTEP_PARENTS names it. Registering one with the console writes its output to the parent's log, shows its lines under the parent's column, and keeps `_run_step` from opening a second banner for a step that is already open.
    """

    name: str
    argv: list[str] | None
    note: str = ""
    lane: str = ""
    describe: str = ""
    skipped: bool = False


@dataclass
class Plan:
    """The resolved pass. `corpus_dir` is the corpus the pass serves when it ends (the staging directory on a staging pass), `update_corpus` the one the corpus build writes, and `next_corpus` the tree the land swaps in for the served one (`review.next` on a building pass, the staged directory on a promotion, None when the corpus does not move). `land` says whether the pass lands a corpus, carrying the verdict store onto it (`engine.review.landing`), with its scratch files under `scratch_dir`, and `broadcast` whether it tells the listening review server's tabs what moved, which it does not for a server serving another checkout."""

    short_id: str
    first_run: bool
    skip_gates: bool
    skip_conform: bool = False
    skip_make_test: bool = False
    make_test_note: str = ""
    make_test_fingerprint: str | None = None
    skip_run_build: bool = False
    rerun_gates_only: bool = False
    rerun_kern_only: bool = False
    run_build_note: str = ""
    run_build_fingerprint: str | None = None
    fresh: bool = False
    skip_corpus: bool = False
    refresh_assets: bool = False
    promote_corpus: Path | None = None
    corpus_note: str = ""
    skip_contracts: bool = False
    contracts_note: str = ""
    contracts_run: contracts.ContractsRun | None = None
    conform_note: str = ""
    conform_proven: bool = False
    record_greens: bool = False
    pool_policy: str = POOL_POLICY_DEFAULT
    kernel_threads: int = 1
    kernel_reason: str = ""
    scratch_beside_default: int = 0
    replay_threads: int = 1
    replay_reason: str = ""
    make_test_workers: int = 1
    make_test_reason: str = ""
    contracts_workers: int = 1
    contracts_reason: str = ""
    conform_jobs: int = 1
    conform_reason: str = ""
    conform_max_length: int = CONFORM_MAX_LENGTH_DEFAULT
    review_out: Path | None = None
    corpus_dir: Path = REVIEW_OUT
    update_corpus: Path = REVIEW_OUT
    next_corpus: Path | None = None
    land: bool = False
    broadcast: bool = True
    retention: bool = False
    recipe_serves: bool = False
    stamp: str = ""
    log_dir: Path | None = None
    scratch_dir: Path | None = None
    steps: list[Step] = field(default_factory=list)

    @property
    def gates_queue(self) -> bool:
        """Return whether this pass runs its heavy gate pools one at a time (`gates_queue`), which a pass running gate:make-test and neither run_build nor the corpus build does under either policy."""
        return gates_queue(
            self.pool_policy,
            make_test_runs=not (self.skip_gates or self.skip_make_test),
            build_lane_runs=not (self.skip_run_build and self.skip_corpus),
        )

    def step(self, name: str) -> Step | None:
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def describe(self, name: str) -> str:
        """Return the named step's banner text, for the steps that run in this process and never reach `_run_step` (retention)."""
        step = self.step(name)
        return "" if step is None else step.describe

    def note_for(self, name: str) -> str:
        step = self.step(name)
        return "" if step is None else step.note

    def runs(self, name: str) -> bool:
        """Return whether the named step has a command line; a step the plan skipped has only a note."""
        return any(step.name == name and step.argv is not None for step in self.steps)

    def includes(self, name: str) -> bool:
        """Return whether the named step is in the plan and not skipped, for the steps that run in this process without a command line."""
        return any(step.name == name and not step.skipped for step in self.steps)

    def conform_undecided(self) -> bool:
        """Return whether gate:conform is decided only after run_build, the pass `plan_rows` shows as `run?`."""
        return not (self.skip_gates or self.skip_conform or self.skip_run_build or self.fresh)

    def make_test_undecided(self) -> bool:
        """Return whether gate:make-test is decided only after run_build, by its wrapper: a pass whose run_build rewrites the built Senior (a full or kern-only build), which `plan_rows` shows as `run?`. A forced pass runs the gate whatever run_build leaves."""
        step = self.step("gate:make-test")
        forced = step is not None and step.argv == make_test_gate_argv(force=True)
        return not (
            self.skip_gates or self.skip_make_test or self.skip_run_build or self.rerun_gates_only or forced
        )

    def in_scratch(self, path: Path) -> bool:
        """Return whether `path` lies in this pass's scratch directory, which the pass deletes when it ends."""
        return self.scratch_dir is not None and path.is_relative_to(self.scratch_dir)

    def argv(self, name: str) -> list[str]:
        """Return the named step's command line, raising ValueError for a step the plan skipped. `build_plan` is the only writer of step argvs, so the executor runs the command line the plan printed."""
        for step in self.steps:
            if step.name == name:
                if step.argv is None:
                    raise ValueError(f"plan step {name!r} runs nothing: {step.note}")
                return step.argv
        raise KeyError(name)


def jstest_argv() -> list[str]:
    """Return the JS suite's argv. Some node releases, 22 among them, reject the bare-directory form with 'Cannot find module', so the files are listed by the `*.test.js` glob, which is expanded here in Python because no shell runs the command."""
    files = sorted(str(path.relative_to(ROOT)) for path in JSTEST_DIR.glob("*.test.js"))
    return ["node", "--test", *files]


@functools.cache
def _font_suite_worker_bytes() -> int:
    """Return the root conftest.py's FONT_SUITE_WORKER_BYTES, the peak memory of one gate:make-test pytest worker, parsed from that file's source with `ast` instead of imported. pytest loads every conftest under the module name `conftest`, so in a run collected under engine/ (every run that tests this module) `import conftest` gets engine/conftest.py, and `import engine.conftest` would execute a second copy of a conftest pytest has already loaded, with its lane-audit hook. Parsing also keeps this build tool from importing pytest or inheriting that file's sys.path edits. A renamed constant raises here instead of silently dropping the memory the cycle sets aside for that pool. The path is resolved from this file, not from ROOT, because a test that points the cycle at an invented root changes where artifacts go, not which test suite the gate runs."""
    tree = ast.parse((Path(__file__).resolve().parents[2] / "conftest.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "FONT_SUITE_WORKER_BYTES" for target in node.targets
        ):
            return int(ast.literal_eval(node.value))
    raise RuntimeError(
        "the root conftest.py defines no FONT_SUITE_WORKER_BYTES: the cycle estimates the peak memory of gate:make-test's pytest pool from that constant, and it cannot reserve memory for a pool whose peak it cannot estimate."
    )


def make_test_pool_width(*, build_lane_runs: bool = True, ncores: int | None = None) -> int:
    """Return the width of gate:make-test's pytest pool under a cycle. The cycle passes this number to the child and reserves memory for it, so both use one value. Without it the pool would be `-n auto`, which the root conftest.py resolves to every core, while the builds beside it are sized to leave room for it. On a pass whose build lane runs (run_build or the corpus build), the width is the gate lane's share of `memory_budget.usable_cores()`, the count the root hook uses, as `memory_budget.split_cores` divides it, and the build lane's share is left to the corpus build's one process and, beside it, to gate:conform's sweep and gate:engine-contracts' pool (`_contracts_pool_terms`). On a pass that runs neither, the pool takes every core, and the heavy gates run one at a time under either policy (`gates_queue`), so the engine suite and the conformance sweep start after it and subtract nothing for it. PYTEST_XDIST_AUTO_NUM_WORKERS overrides both, because the child inherits this environment and will run at that width. It is parsed as the root hook parses it, so an unparseable value raises here, while the plan resolves, instead of inside the gate after the rest of the cycle has run."""
    from engine.tools import memory_budget

    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return max(1, int(stated))
    cores = ncores or memory_budget.usable_cores()
    return memory_budget.split_cores(cores)[1] if build_lane_runs else cores


def make_test_pool_derivation(*, build_lane_runs: bool = True, ncores: int | None = None) -> str:
    """Return `make_test_pool_width`'s width as a clause for the plan's t0 lane line, which names which share of the cores the pool holds. A width set through PYTEST_XDIST_AUTO_NUM_WORKERS is reported as set."""
    from engine.tools import memory_budget

    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return f"PYTEST_XDIST_AUTO_NUM_WORKERS states {stated.strip()}"
    cores = ncores or memory_budget.usable_cores()
    if build_lane_runs:
        return f"the gate lane's {memory_budget.split_cores(cores)[1]} of {cores} cores (memory_budget.split_cores), beside the build lane"
    return f"all {cores} cores, since neither run_build nor the corpus build runs this pass"


def gates_queue(pool_policy: str, *, make_test_runs: bool = True, build_lane_runs: bool = True) -> bool:
    """Return whether the heavy gate pools run one at a time on this pass, gate:make-test, then gate:conform, then gate:engine-contracts, so that no gate's width subtracts another gate's pool. The queue policy always runs them so. The overlap policy does too on a pass that runs gate:make-test and neither run_build nor the corpus build, such as a version bump's. There gate:make-test's pool holds every core (`make_test_pool_width`) and the gates end the pass, so the suite or the sweep beside that pool would either book its cores twice or fall to its floor on the pass's critical path. That reason does not hold on a pass with no build lane that skips gate:make-test, so there the overlap policy runs the sweep and the suite side by side, as it does elsewhere: neither width subtracts the other, and the module docstring gives the measurements behind that default."""
    return pool_policy == "queue" or (make_test_runs and not build_lane_runs)


def _make_test_pool_bytes(*, skip_make_test: bool, ncores: int | None) -> float:
    """Return the memory gate:make-test's pytest pool holds: FONT_SUITE_WORKER_BYTES times `make_test_pool_width` at the gate lane's share of the cores, the width the pool runs at beside the build lane, or zero when the gate is skipped. The conformance sweep's and the engine suite's widths subtract it; the table build's and the string replay's do not, since gate:make-test starts once the build gate has passed."""
    return 0 if skip_make_test else _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)


def _wave_cap(ncores: int | None) -> int:
    """Return the width cap that `kernel_threads_budget` and `replay_threads_budget` share: the smaller of the configuration count and the usable cores, the same bounds `run_build._table_build_threads` and `run_build._replay_threads` apply."""
    from engine.pipeline.conform import SETTLEMENT_CONFIGS
    from engine.tools import memory_budget

    return min(len(SETTLEMENT_CONFIGS), ncores or memory_budget.usable_cores())


def kernel_threads_budget(*, ncores: int | None = None, total_bytes: int | None = None) -> int:
    """Return the kernel fan-out's width for this cycle, the `--kernel-threads` the plan passes run_build. Memory limits this width because a live delta holds its whole working set until it emits, and every configuration's prepared fold product stays resident, parked and compact until every configuration has parked and then read back whole through the cross-configuration exchange. `kernel_exec.kernel_threads_default` does the arithmetic over the settlement configurations: the widest width whose booking (`kernel_exec.table_build_booking_bytes`: the memo snapshot `default` keeps alive for the wave, one parked product per configuration, what each delta in flight adds beyond its own, and `default`'s fold preparation in a slot of its own once every delta has one, and no less than every configuration's product read back at the rendezvous) fits the machine's memory less the reserve. On the fleet (`doc/fleet.md`) this gives the whole wave, every configuration at once, on both 48 GiB machines. Nothing else is subtracted: gate:make-test starts only once the build gate has passed, so no pytest pool runs beside the table build. AMS_KERNEL_THREADS overrides the arithmetic, as it does for a bare run_build.

    The result is capped at the configuration count and the usable cores, as in `replay_threads_budget`. The memory result never exceeds the configuration count, but a stated AMS_KERNEL_THREADS can, and a machine can have fewer cores than configurations, so without the cap the plan line could name a width the build never takes. With it, the plan line, the argv and `cycle_summary.json`'s `plan.kernel_threads` show the real width, and `run_build._table_build_threads`'s own `min()` changes nothing. A stated AMS_KERNEL_THREADS above the configuration count is cut to it here, as `run_build._table_build_threads` would cut it. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from engine.pipeline.conform import SETTLEMENT_CONFIGS
    from engine.pipeline.kernel_exec import kernel_threads_default

    derived = kernel_threads_default(configs=len(SETTLEMENT_CONFIGS), total_bytes=total_bytes)
    return max(1, min(derived, _wave_cap(ncores)))


def scratch_beside_default_budget(kernel_threads: int, *, total_bytes: int | None = None) -> int:
    """Return at most how many of the heaviest deltas this cycle's table build enumerates from scratch beside `default`, the `--scratch-beside-default` the plan passes run_build, which the crate takes in whole tiers of equal unlocking-rune count: `kernel_exec.deltas_from_scratch` at `kernel_threads`, the width `kernel_threads_budget` gave, with nothing beside the build, since no pytest pool runs beside run_build. `total_bytes` is a keyword so a test can compute the count for an invented machine."""
    from engine.pipeline.conform import SETTLEMENT_CONFIGS
    from engine.pipeline.kernel_exec import deltas_from_scratch

    return deltas_from_scratch(kernel_threads, configs=len(SETTLEMENT_CONFIGS), total_bytes=total_bytes)


def replay_threads_budget(*, ncores: int | None = None, total_bytes: int | None = None) -> int:
    """Return the string replay's width for this cycle, the `--replay-threads` the plan passes run_build: memory, less the reserve, divided by `kernel_exec.REPLAY_PEAK_BYTES` (one configuration's length-4 walk and the absorb of its window memo beside it), the number of walks that run at once. Nothing else is subtracted, since gate:make-test starts only once the build gate has passed, after the replay inside run_build has finished. `kernel_exec.replay_threads_default` does the arithmetic, and `AMS_REPLAY_THREADS` overrides it, as it does for a bare run_build.

    The result is capped at the configuration count and the usable cores, as in `kernel_threads_budget`. On every fleet machine the memory result exceeds the configuration count, so memory binds only on a smaller machine, and without the cap the plan line would name a width the run never takes. With it, the plan line and the argv show the real width, and `run_build._replay_threads`'s own `min()` changes nothing. A stated AMS_REPLAY_THREADS above the configuration count is cut to it here, as the crate and `run_build._replay_threads` would cut it. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from engine.pipeline.kernel_exec import replay_threads_default

    return max(1, min(replay_threads_default(total_bytes=total_bytes), _wave_cap(ncores)))


def replay_threads_derivation(*, ncores: int | None = None, total_bytes: int | None = None) -> str:
    """Return `replay_threads_budget`'s width as a clause for the plan line, computed from the same terms so the clause always explains the printed width. It gives the number of waves of walks, each walk with its absorb beside it, the width makes of the configuration count, then either the AMS_REPLAY_THREADS value the width came from (with the cap or the floor of one, when either changed it) or `memory_budget.describe_fit` over the budget's terms and cap. A stated width is described as stated, because arithmetic that gives a different number would contradict the width printed beside it."""
    from engine.pipeline.conform import SETTLEMENT_CONFIGS
    from engine.pipeline.kernel_exec import REPLAY_PEAK_BYTES
    from engine.tools import memory_budget

    cap = _wave_cap(ncores)
    width = replay_threads_budget(ncores=ncores, total_bytes=total_bytes)
    count = len(SETTLEMENT_CONFIGS)
    waves = (
        "every settlement configuration in one wave"
        if width >= count
        else f"{-(-count // width)} waves over {count} settlement configurations"
    )
    stated = os.environ.get("AMS_REPLAY_THREADS")
    if stated is not None:
        asked = int(stated)
        moved = ", floored at one" if asked < 1 else f", cut to the cap of {cap}" if asked > width else ""
        return f"{waves}; AMS_REPLAY_THREADS states {stated.strip()}{moved}"
    fit = memory_budget.describe_fit(REPLAY_PEAK_BYTES, cap=cap, total_bytes=total_bytes)
    return f"{waves}; {fit}"


def _contracts_pool_terms(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_corpus: bool,
    pool_policy: str,
    build_lane_runs: bool = True,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[int, int, int]:
    """Return the three terms `contracts_pool_width` and `contracts_pool_derivation` share, which `_conform_core_cap` also subtracts: the usable cores; the corpus build's process count (one, the build's one Python process, or zero when the build does not run); and gate:make-test's pool width on a pass that runs that gate when the heavy gates overlap, which they do under the overlap policy on a pass whose build lane runs. When they queue (`gates_queue`), the suite and the conformance sweep wait for gate:make-test to finish, so nothing is subtracted for that pool."""
    from engine.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    corpus = 0 if skip_corpus else 1
    make_test = (
        0
        if skip_gates or skip_make_test or gates_queue(pool_policy, build_lane_runs=build_lane_runs)
        else make_test_pool_width(ncores=ncores)
    )
    return cores, corpus, make_test


def contracts_pool_width(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    skip_corpus: bool = False,
    pool_policy: str = POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the width of gate:engine-contracts' pytest pool under a cycle, which the cycle sets on that child as PYTEST_XDIST_AUTO_NUM_WORKERS. Memory does not limit it: no contracts worker holds a live artifact, so no constant measures one, and the width is a count of cores. The suite runs beside the corpus build, so the width is the usable cores, less the build's one process, less gate:make-test's pool on a pass that runs that gate while the heavy gates overlap, as they do under the overlap policy while the build lane runs (`_contracts_pool_terms`, `gates_queue`), floored at one. A pass whose corpus build does not run (skipped, promoted, or assets-refreshed) subtracts nothing for the build, so the suite gets every core unless gate:make-test's pool is subtracted.

    Each subtraction leaves its cores to the pool that holds them, and the conformance sweep's cap (`_conform_core_cap`) makes the same two subtractions for the same reasons. Under the overlap policy, while the build lane runs, gate:make-test's pool is subtracted: the plan cannot tell whether that pool will still be running when the suite and the sweep start, and while it runs it holds the gate lane's share (`memory_budget.split_cores`), so the subtraction keeps a core from being booked twice. Neither width subtracts the other pool, so under that policy the sweep and the suite share the cores the two subtractions leave while both run, an unbudgeted overlap that `doc/parallelism.md` lists. The suite's controller is not subtracted: it idles while its workers run, and subtracting it would cost the suite a worker on every pass, so the suite runs one process more than its width. The pool's memory comes out of `memory_budget`'s reserve, since no constant measures a worker (`calibrate_budgets.UNITS`).

    Beside the corpus build under the overlap policy, on a pass that runs gate:make-test, the arithmetic leaves the suite the build lane's share less the build's process. Under the queue policy, and on a pass that skips gate:make-test, the suite takes every core less the build's process.

    A pass that runs gate:make-test and neither run_build nor the corpus build, such as a version bump's, queues the heavy gates under either policy (`gates_queue`, which is why this function takes `build_lane_runs`), so the suite starts once gate:make-test has finished and takes every core. Under the overlap policy the suite would start beside that gate's pool at t=0, and that pool holds every core there (`make_test_pool_width`), so the subtraction would floor the suite at one worker. A version bump edits pyproject.toml and uv.lock, which are global labels (`contracts_closure.GLOBAL_LABELS`), so it reruns the suite's whole lane. On the 18-core machine a version-bump pass with the suite beside gate:make-test at one worker ran the suite well past that gate's end, and the same edit with the gates queued ran it green at every core once that gate had finished; `make cycle-timings` on that machine records both passes.

    PYTEST_XDIST_AUTO_NUM_WORKERS overrides all of this, because the child inherits this environment and will run at that width, as in `make_test_pool_width`. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return max(1, int(stated))
    cores, corpus, make_test = _contracts_pool_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    return max(1, cores - corpus - make_test)


def contracts_pool_derivation(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    skip_corpus: bool = False,
    pool_policy: str = POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `contracts_pool_width`'s width as a clause for the plan's lane line. It does not use `memory_budget.describe_fit`: the width is a count of cores, and the clause says which cores are left out. A width set through PYTEST_XDIST_AUTO_NUM_WORKERS is reported as set."""
    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return f"PYTEST_XDIST_AUTO_NUM_WORKERS states {stated.strip()}"
    cores, corpus, make_test = _contracts_pool_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    width = contracts_pool_width(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    if corpus == 0:
        machine = "the machine" if make_test else "the whole machine"
        clause = f"{width} of {cores} cores, {machine} (no corpus build to share it with)"
    else:
        clause = f"{width} of {cores} cores, less {CORPUS_BUILD_PROCESS}"
    if make_test:
        clause += f", less gate:make-test's {make_test} (overlap policy)"
    return clause if cores - corpus - make_test >= 1 else clause + ", floored at one"


def contracts_submission_note(*, skip_corpus: bool) -> str:
    """Return where in the build lane the engine suite is submitted, for the plan's step note and lane line: beside the corpus build when one runs, and otherwise at the same point, once the build gate has passed. The second wording keeps a pass whose corpus-build row reads SKIPPED from claiming the suite runs beside it."""
    if skip_corpus:
        return "submitted once the build gate passes (no corpus build this pass)"
    return "submitted beside the corpus build"


def _conform_build_lane(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_corpus: bool,
    build_lane_runs: bool = True,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[str, int]:
    """Return the build-lane step the conformance sweep runs beside, by plan step name, and the memory that step holds. The one candidate is the corpus build, one Python process at CORPUS_BUILD_BYTES, and `("", 0)` when the pass does not run it. The land that follows it is not a candidate: it holds less (LAND_BYTES), which a test checks, and the lane runs it after the corpus build has exited. The conformance sweep's lane is submitted when run_build's gate passes, so it starts beside the corpus build."""
    return ("", 0) if skip_corpus else ("corpus-build", CORPUS_BUILD_BYTES)


def _conform_core_cap(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_corpus: bool,
    pool_policy: str,
    build_lane_runs: bool = True,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[int, str]:
    """Return the conformance sweep's cap on its width and a clause saying which bound set it. The cap is the usable cores less the corpus build's one process when the pass runs the build, and less gate:make-test's pool on a pass that runs that gate while the heavy gates overlap (`_contracts_pool_terms`, `gates_queue`). These are the two subtractions the engine suite's width makes, and each leaves its cores to the pool that holds them: the corpus build keeps its core, and gate:make-test's pool, which may still hold the gate lane when the sweep starts, keeps its own width. The engine suite's pool is not subtracted, and the suite's width does not subtract the sweep, because each is off the critical path on the `--fresh` passes behind the default (the module docstring); under that policy the two share the cores left while both run, an unbudgeted overlap that `doc/parallelism.md` lists. `contracts_pool_width` gives these reasons in full for both widths. The cap is floored at the acceptance-configuration count, or at the cores where there are fewer, which is the width one process per configuration gives, so a cycle never runs the sweep narrower than that; where the floor binds beside gate:make-test's pool, the sweep and that pool oversubscribe the cores while they overlap, an unbudgeted overlap that `doc/parallelism.md` lists. A hand run (`skip_gates=True, skip_corpus=True`) subtracts nothing and gets every core."""
    from engine.pipeline.conform import ACCEPTANCE_CONFIGS

    cores, corpus, make_test = _contracts_pool_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    if not corpus and not make_test:
        return cores, f"the cap is the {cores} usable cores"
    left = cores - corpus - make_test
    parts = []
    if corpus:
        parts.append(f"less {CORPUS_BUILD_PROCESS}")
    if make_test:
        parts.append(f"less gate:make-test's {make_test} (overlap policy)")
    lane = f"{cores} cores " + ", ".join(parts)
    floor = min(cores, len(ACCEPTANCE_CONFIGS))
    if left >= floor:
        return left, f"the cap is {lane}"
    bound = "acceptance configurations" if floor == len(ACCEPTANCE_CONFIGS) else "usable cores"
    return (
        floor,
        f"the cap is the {floor} {bound}, its floor, since {lane} leave {left if left > 0 else 'none'}",
    )


def _conform_fit_terms(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_corpus: bool,
    pool_policy: str,
    build_lane_runs: bool = True,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[int, int, int]:
    """Return the three arguments of the conformance sweep's width, so `conform_job_budget` and `conform_job_derivation` use one derivation: the per-unit divisor (CONFORM_SWEEP_UNIT_BYTES), the co-resident bytes, and the cap. The co-resident bytes are the build-lane step `_conform_build_lane` names, plus gate:make-test's pool on a pass that runs that gate while the heavy gates overlap. When they queue (`gates_queue`), the conformance sweep waits for make-test to finish, so nothing is subtracted for that pool. The cap is `_conform_core_cap`'s. The budget does not load the spec, so it does not count the units; `run_build._spawn_pool` caps the pool at the unit count at run time, and every fleet machine has fewer cores than units."""
    _step, build_lane = _conform_build_lane(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    make_test = (
        0
        if skip_gates or skip_make_test or gates_queue(pool_policy, build_lane_runs=build_lane_runs)
        else _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)
    )
    cap, _bound = _conform_core_cap(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    return CONFORM_SWEEP_UNIT_BYTES, build_lane + make_test, cap


def conform_job_budget(
    *,
    skip_gates: bool = False,
    skip_make_test: bool = False,
    skip_corpus: bool = False,
    pool_policy: str = POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the `--jobs` the cycle passes gate:conform: how many conformance-sweep units run at once, one spawn process each (`run_build_gates.run_font_conformance`, `run_build_gates.sweep_units`). It is memory, less the reserve, less what runs beside the conformance sweep, divided by CONFORM_SWEEP_UNIT_BYTES, capped at the cores less the corpus build's process, and less gate:make-test's pool on a pass that runs that gate while the heavy gates overlap (`gates_queue`), but never below the acceptance-configuration count (`_conform_core_cap`), and floored at one (`_conform_fit_terms`); memory can still narrow it below that count. `run_build._spawn_pool` caps it at the unit count at run time. What runs beside it is the corpus build when this pass runs it (`_conform_build_lane`). gate:make-test's pool is subtracted only on a pass that runs that gate while the heavy gates overlap, since queued gates make the conformance sweep wait for make-test (`gates_queue`). Two things share the machine with no memory estimate, and their bytes come out of the reserve: gate:engine-contracts' pool while the heavy gates overlap and the suite runs, which is limited by cores and measured by no constant (`calibrate_budgets.UNITS`), and the build lane's other steps.

    CONFORM_SWEEP_UNIT_BYTES is measured at the per-edit maximum length (`conform.SWEEP_MAX_LENGTH`) only. A cycle run with a deeper `--conform-max-length` holds its windows in process and shares no memo, so the constant is not a measurement for it; deeper sweeps belong to `make conform-deep`. A hand `run_build --conform-only` uses the idle case (`skip_gates=True, skip_corpus=True`). A width of one runs the serial conformance sweep (`conform.run_conformance`). `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from engine.tools import memory_budget

    per_unit, coresident, cap = _conform_fit_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    return memory_budget.how_many_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def conform_job_derivation(
    *,
    skip_gates: bool = False,
    skip_make_test: bool = False,
    skip_corpus: bool = False,
    pool_policy: str = POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `conform_job_budget`'s width as a clause for the plan's Lane conform line and run_build's `--jobs` help: `memory_budget.describe_fit` over the same terms, so the clause opens with the width it explains, then which bound set the cap (`_conform_core_cap`)."""
    from engine.tools import memory_budget

    per_unit, coresident, cap = _conform_fit_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    _cap, bound = _conform_core_cap(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    fit = memory_budget.describe_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)
    return f"{fit}; {bound}"


def build_plan(
    *,
    skip_gates: bool,
    first_run: bool,
    short_id: str,
    skip_conform: bool = False,
    skip_make_test: bool = False,
    make_test_note: str = "",
    make_test_fingerprint: str | None = None,
    force_make_test: bool = False,
    conform_max_length: int = CONFORM_MAX_LENGTH_DEFAULT,
    pool_policy: str = POOL_POLICY_DEFAULT,
    review_out: Path | None = None,
    ncores: int | None = None,
    total_bytes: int | None = None,
    skip_run_build: bool = False,
    rerun_gates_only: bool = False,
    rerun_kern_only: bool = False,
    run_build_note: str = "",
    run_build_fingerprint: str | None = None,
    fresh: bool = False,
    skip_corpus: bool = False,
    refresh_assets: bool = False,
    promote_corpus: Path | None = None,
    corpus_note: str = "",
    skip_contracts: bool = False,
    contracts_note: str = "",
    contracts_run: contracts.ContractsRun | None = None,
    conform_note: str = "",
    conform_proven: bool = False,
    record_greens: bool = False,
    keep_history: bool = False,
    recipe_serves: bool = False,
    scratch_dir: Path | None = None,
) -> Plan:
    """Resolve the pass's plan: run_build as a skip, a full build, a gates-only rerun or a kern-only rebuild, the table-diff corpus build into `review.next` beside the served corpus (in place on a first run, into `review_out` on a staging pass), the land that swaps it in and carries the verdict store onto it by unit id, the four gates, the job-costs check and retention."""
    no_make_test = skip_gates or skip_make_test
    build_lane_runs = not (skip_run_build and skip_corpus)
    make_test_workers = make_test_pool_width(build_lane_runs=build_lane_runs, ncores=ncores)
    make_test_reason = make_test_pool_derivation(build_lane_runs=build_lane_runs, ncores=ncores)
    contracts_workers = contracts_pool_width(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    contracts_reason = contracts_pool_derivation(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    conform_jobs = conform_job_budget(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    conform_beside, _lane_bytes = _conform_build_lane(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    make_test_beside_conform = not (no_make_test or gates_queue(pool_policy, build_lane_runs=build_lane_runs))
    if conform_beside == "corpus-build":
        conform_head = f"CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, beside {CORPUS_BUILD_PROCESS}"
    else:
        conform_head = (
            "CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, the corpus build not running this pass, so "
            + (
                "only gate:make-test's pool co-resident"
                if make_test_beside_conform
                else "nothing co-resident"
            )
        )
    if conform_beside and make_test_beside_conform:
        conform_head += " and gate:make-test's pool"
    conform_reason = f"{conform_head}; " + conform_job_derivation(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    # gate:make-test starts once the build gate has passed (`_run_cycle`), so no pytest pool runs beside the table build or the string replay, and their widths subtract none.
    kernel_threads = kernel_threads_budget(ncores=ncores, total_bytes=total_bytes)
    kernel_reason = "the table build's memory ceiling, the one width RAM binds, capped at the configuration count and the cores"
    scratch_beside_default = scratch_beside_default_budget(kernel_threads, total_bytes=total_bytes)
    replay_threads = replay_threads_budget(ncores=ncores, total_bytes=total_bytes)
    replay_reason = replay_threads_derivation(ncores=ncores, total_bytes=total_bytes)
    corpus_dir = review_out if review_out is not None else REVIEW_OUT
    do_retention = not keep_history and not first_run and review_out is None
    live_pass = review_out is None and not first_run
    next_corpus: Path | None = None
    if live_pass and promote_corpus is not None:
        next_corpus = promote_corpus
    elif live_pass and not skip_corpus:
        next_corpus = next_corpus_dir()
    do_land = live_pass and next_corpus is not None
    land_dir: Path | None = None
    if do_land:
        land_dir = scratch_dir if scratch_dir is not None else cycle_paths.CYCLE_VAR / short_id
        scratch_dir = land_dir
    update_corpus = review_out if review_out is not None else (next_corpus or REVIEW_OUT)

    plan = Plan(
        short_id=short_id,
        first_run=first_run,
        skip_gates=skip_gates,
        skip_conform=skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fingerprint,
        skip_run_build=skip_run_build,
        rerun_gates_only=rerun_gates_only and not skip_run_build,
        rerun_kern_only=rerun_kern_only and not skip_run_build and not rerun_gates_only,
        run_build_note=run_build_note,
        run_build_fingerprint=run_build_fingerprint,
        fresh=fresh,
        skip_corpus=skip_corpus,
        refresh_assets=refresh_assets,
        promote_corpus=promote_corpus,
        corpus_note=corpus_note,
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        contracts_run=contracts_run,
        conform_note=conform_note,
        conform_proven=conform_proven,
        record_greens=record_greens,
        retention=do_retention,
        recipe_serves=recipe_serves,
        pool_policy=pool_policy,
        kernel_threads=kernel_threads,
        kernel_reason=kernel_reason,
        scratch_beside_default=scratch_beside_default,
        replay_threads=replay_threads,
        replay_reason=replay_reason,
        make_test_workers=make_test_workers,
        make_test_reason=make_test_reason,
        contracts_workers=contracts_workers,
        contracts_reason=contracts_reason,
        conform_jobs=conform_jobs,
        conform_reason=conform_reason,
        conform_max_length=conform_max_length,
        review_out=review_out,
        corpus_dir=corpus_dir,
        update_corpus=update_corpus,
        next_corpus=next_corpus,
        land=do_land,
        scratch_dir=scratch_dir,
    )

    if skip_run_build:
        plan.steps.append(
            Step(
                "build",
                None,
                f"SKIPPED ({run_build_note}); gate re-evaluated from the recorded summaries",
                lane="build",
                skipped=True,
            )
        )
    elif plan.rerun_gates_only:
        rerun_argv = ["uv", "run", "python", "-m", "engine.pipeline.run_build", "--gates-only"]
        plan.steps.append(
            Step(
                "build",
                rerun_argv,
                run_build_note,
                lane="build",
                describe=step_description(BUILD_GATES_ONLY_STEP),
            )
        )
    elif plan.rerun_kern_only:
        plan.steps.append(
            Step(
                "build",
                ["uv", "run", "python", "-m", "engine.pipeline.run_build", "--kern-only"],
                run_build_note,
                lane="build",
                describe=step_description(BUILD_KERN_ONLY_STEP),
            )
        )
    else:
        run_build_argv = ["uv", "run", "python", "-m", "engine.pipeline.run_build"]
        run_build_argv += [
            "--kernel-threads",
            str(kernel_threads),
            "--scratch-beside-default",
            str(scratch_beside_default),
            "--replay-threads",
            str(replay_threads),
        ]
        if fresh:
            run_build_argv += ["--fresh-tables"]
        plan.steps.append(Step("build", run_build_argv, run_build_note, lane="build"))

    if skip_corpus:
        plan.steps.append(Step("corpus-build", None, f"SKIPPED ({corpus_note})", lane="build", skipped=True))
        if refresh_assets:
            plan.steps.append(
                Step(
                    "assets-refresh",
                    ["uv", "run", "python", "-m", "engine.review.build", "refresh-assets"],
                    "copy engine/review/static/ over the served copy and restamp the manifest's static component; the units and the sidecars are untouched, so the autosave stays aligned",
                    lane="build",
                )
            )
    else:
        corpus_argv = ["uv", "run", "python", "-m", "engine.review.build"]
        if not first_run or review_out is not None:
            corpus_argv += ["--out", str(update_corpus)]
        corpus_note_text = f"the accepted snapshot against engine/out/build, into {update_corpus}" + (
            ", which it empties first" if next_corpus is not None else ""
        )
        plan.steps.append(Step("corpus-build", corpus_argv, corpus_note_text, lane="build"))

    if land_dir is not None:
        land_argv = [
            "uv",
            "run",
            "python",
            "-m",
            "engine.review.landing",
            "--autosave",
            str(AUTOSAVE),
            "--run-dir",
            str(land_dir),
            "--corpus",
            str(next_corpus or REVIEW_OUT),
        ]
        if next_corpus is not None:
            land_argv += ["--staged", str(next_corpus), "--live", str(REVIEW_OUT), "--keep-discard"]
        land_note = f"swaps {next_corpus} in for engine/out/review and, when the stamp moves, carries the verdict store onto it by unit id under the verdict store's lock: a verdict whose unit the new corpus still has stays, a skip is dropped, and the rest go to var/verdict-orphans/; the review server keeps running"
        plan.steps.append(Step("land", land_argv, land_note, lane="build"))

    if skip_gates:
        plan.steps.append(Step("gates", None, "SKIPPED (--skip-gates)", skipped=True))
    else:
        plan.steps.append(Step("gate:js", jstest_argv(), lane="t0"))
        if skip_conform:
            plan.steps.append(
                Step(
                    "gate:conform",
                    None,
                    f"SKIPPED ({conform_note or '--skip-conform'})",
                    lane="conform",
                    skipped=True,
                )
            )
        else:
            plan.steps.append(
                Step("gate:conform", conform_gate_argv(conform_jobs, conform_max_length), lane="conform")
            )
        if skip_contracts:
            plan.steps.append(
                Step(
                    "gate:engine-contracts",
                    None,
                    f"SKIPPED ({contracts_note})",
                    lane="contracts",
                    skipped=True,
                )
            )
        else:
            plan.steps.append(
                Step(
                    "gate:engine-contracts",
                    contracts_argv(),
                    contracts_submission_note(skip_corpus=skip_corpus)
                    + (f"; {contracts_note}" if contracts_note else ""),
                    lane="contracts",
                )
            )
        if skip_make_test:
            plan.steps.append(
                Step("gate:make-test", None, f"SKIPPED ({make_test_note})", lane="make-test", skipped=True)
            )
        elif force_make_test or fresh:
            plan.steps.append(
                Step(
                    "gate:make-test",
                    make_test_gate_argv(force=True),
                    "forced: the suite and pyright run even where their green records answer for the closure",
                    lane="make-test",
                )
            )
        else:
            plan.steps.append(Step("gate:make-test", make_test_gate_argv(force=False), lane="make-test"))

    plan.steps.append(
        Step(
            "job-costs",
            ["uv", "run", "python", "-m", "engine.tools.calibrate_budgets", "--check"],
            "the checked-in per-unit peaks against what this machine measured, once the gates have joined and this pass's own pool records are in the journal — a file read; committing a re-measured constant is the acceptance",
        )
    )

    if do_retention:
        plan.steps.append(
            Step(
                "retention",
                None,
                f"on green finish: keep the newest {cycle_paths.BUILD_LOGS_KEEP} build-log runs and delete the older ones; --keep-history skips",
            )
        )
    elif keep_history:
        plan.steps.append(Step("retention", None, "SKIPPED (--keep-history)", skipped=True))
    elif first_run:
        plan.steps.append(
            Step("retention", None, "SKIPPED (first run: nothing accumulated yet)", skipped=True)
        )
    else:
        plan.steps.append(
            Step(
                "retention",
                None,
                "SKIPPED (staging: the live files are not this cycle's to prune)",
                skipped=True,
            )
        )

    for step in plan.steps:
        if not step.describe:
            step.describe = step_description(step.name)

    return plan


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


def server_can_keep_running(*, skip_corpus: bool, writes_store: bool, promotes_corpus: bool = False) -> bool:
    """Return whether a review server that predates the land protocol (`probe_server`'s `legacy`: no /capabilities) can keep running through this pass. Such a server takes no part in the land, so the answer depends on what the plan writes under it: the corpus's units and stamp, which the land swaps, and the verdict store the app saves into. A pass that moves no corpus (a pass with no artifact work, or an assets refresh, which rewrites no shard and leaves `generated_at` unchanged) writes neither, so that server keeps running. A promotion sets the corpus skip flag but swaps in the staged corpus, so `promotes_corpus` requires stopping the server even when the store is untouched. A server that speaks the land protocol keeps running through every pass (`_preflight`)."""
    return skip_corpus and not promotes_corpus and not writes_store


@dataclass(frozen=True)
class ServerProbe:
    """What listens on the review server's port, as `probe_server` classifies it. `kind` is `none` (nothing listens), `current` (a server from this checkout whose land-protocol and freshness code (`landing.code_digest`) is the working tree's), `stale` (a server from this checkout running other code), `foreign` (a server from another checkout, whose root is `root`), `legacy` (a server that answers /capabilities with 404, from before the land protocol), or `unanswered` (a listener that gave no usable /capabilities answer in `CAPABILITIES_ATTEMPTS` tries, such as a current server stalled on a long request)."""

    kind: str
    root: str | None = None


def probe_server() -> ServerProbe:
    """Classify what listens on the review server's port from its /capabilities answer (`review_server.capabilities`), comparing its root with this checkout's and its code digest with the working tree's (`landing.code_digest`). A server whose digest differs was started from other code, possibly an intermediate commit, so the cycle never trusts it to run through a pass. Only a 404 marks a server from before the land protocol; a listener that does not answer is asked again, `CAPABILITIES_RETRY_S` apart, and is `unanswered` after `CAPABILITIES_ATTEMPTS` tries, because a current server can stall for seconds on a request that rewrites or sends the whole store."""
    if not server_listening():
        return ServerProbe("none")
    answer = capabilities()
    for _attempt in range(CAPABILITIES_ATTEMPTS - 1):
        if answer is not None:
            break
        time.sleep(CAPABILITIES_RETRY_S)
        answer = capabilities()
    if answer == NO_CAPABILITIES:
        return ServerProbe("legacy")
    if not isinstance(answer, dict) or not isinstance(answer.get("land_protocol"), int):
        return ServerProbe("unanswered")
    root = answer.get("root")
    if not isinstance(root, str) or Path(root).resolve() != ROOT.resolve():
        return ServerProbe("foreign", root if isinstance(root, str) else None)
    if answer["land_protocol"] != landing.LAND_PROTOCOL or answer.get("code") != landing.code_digest(
        ROOT / "engine" / "review"
    ):
        return ServerProbe("stale", root)
    return ServerProbe("current", root)


def review_server_pid() -> int | None:
    """Return the pid of the process listening on the review server's port, which the write meter reports apart (`write_meter.read`'s `named`), or None when `lsof` names no single listener or cannot run. `_run_pass` asks only once `probe_server` has placed the server in this checkout and `_preflight` has restarted it if it had to, so the pid is this checkout's live server and never another checkout's."""
    try:
        reply = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{REVIEW_PORT}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    pids = {int(token) for token in reply.stdout.split() if token.isdigit()}
    return pids.pop() if len(pids) == 1 else None


def start_review_server() -> None:
    """Start the review server detached from this pass, as `make review-cycle SERVE=bg` does: its own session, so it outlives the pass and the shell that started it, with its output in tmp/review-serve.log."""
    log = ROOT / "tmp" / "review-serve.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as out:
        subprocess.Popen(
            ["uv", "run", "python", "-m", "engine.review.serve"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def restart_review_server(timeout: float | None = None) -> bool:
    """Stop a review server that runs older code and start one on the working tree's (`start_review_server`), then wait up to `timeout` seconds (`SERVER_START_TIMEOUT` by default) for its /capabilities to answer as `current`. Open tabs keep their unconfirmed saves in their outbox and send them again once it answers. Returns False when the old server would not stop or the new one did not come up."""
    if not stop_review_server():
        return False
    start_review_server()
    deadline = time.monotonic() + (SERVER_START_TIMEOUT if timeout is None else timeout)
    while time.monotonic() < deadline:
        if probe_server().kind == "current":
            return True
        time.sleep(0.5)
    return False


def stop_review_server(timeout: float = SERVER_STOP_TIMEOUT) -> bool:
    """Stop the review server and wait for port 7294 to come free: before a pass that writes under a server that predates the land protocol, and before `restart_review_server` starts one on the working tree's code. Returns False when something is still listening at the deadline (a server started another way, or one stuck in shutdown); the caller then reports it and does not run the pass."""
    subprocess.run(["pkill", "-f", SERVER_STOP_PATTERN], check=False, capture_output=True)
    deadline = time.monotonic() + timeout
    while server_listening():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def _queue_reason(plan: Plan) -> str:
    """Return why the heavy gates queue on this pass, for the lane lines: the queue policy, or under the overlap policy a pass that runs gate:make-test and whose build lane does not run (`gates_queue`)."""
    return "queue policy" if plan.pool_policy == "queue" else "no build lane this pass"


def _overlap_note(pools: list[str]) -> str:
    """Return what a gate's lane line says runs beside it under the overlap policy: the heavy gate pools named in `pools`, or that none runs."""
    if not pools:
        return "no other heavy gate pool runs this pass"
    return f"CO-RESIDENT with {' and '.join(pools)} (overlap policy)"


def _no_fanout(plan: Plan) -> str:
    """The note that stands in for the table build's and the string replay's widths on a pass whose run_build enumerates and replays nothing, or the empty string."""
    if plan.rerun_gates_only:
        return "not passed (gates-only rerun)"
    if plan.rerun_kern_only:
        return "not passed (kern-only rebuild)"
    return ""


def _render_concurrency(plan: Plan) -> list[str]:
    if plan.skip_gates:
        return [
            "",
            "  Concurrency (--skip-gates):",
            f"    Lane build only; no gates; run_build at --kernel-threads {_no_fanout(plan) or plan.kernel_threads} and --replay-threads {_no_fanout(plan) or plan.replay_threads}",
        ]
    make_test_lane = (
        f"SKIPPED ({plan.make_test_note})"
        if plan.skip_make_test
        else f"starts when run_build's two JSONs pass, since its session checks the pins against the built Senior run_build leaves; -n {plan.make_test_workers} ({plan.make_test_reason})"
    )
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        "    Lane t0   [from t=0, background]  : gate:js",
        "    Lane build[main thread]          : build -> submit gate:engine-contracts -> corpus-build -> land",
        f"    Lane make-test                   : {make_test_lane}",
    ]
    if plan.skip_conform:
        lines.append(
            f"    Lane conform                     : SKIPPED ({plan.conform_note or '--skip-conform'})"
        )
    elif not plan.gates_queue:
        beside = _overlap_note(
            [
                pool
                for pool, runs in (
                    ("gate:make-test's pool", not plan.skip_make_test),
                    ("gate:engine-contracts' pool", not plan.skip_contracts),
                )
                if runs
            ]
        )
        lines.append(
            f"    Lane conform                     : starts when run_build's two JSONs pass; {beside} (--jobs {plan.conform_jobs}; {plan.conform_reason})"
        )
    elif not plan.skip_make_test:
        lines.append(
            f"    Lane conform                     : starts when run_build's two JSONs pass; QUEUED behind gate:make-test ({_queue_reason(plan)} — one heavy pool at a time) (--jobs {plan.conform_jobs}; {plan.conform_reason})"
        )
    else:
        lines.append(
            f"    Lane conform                     : starts when run_build's two JSONs pass; gate:make-test not running, so no queueing (--jobs {plan.conform_jobs}; {plan.conform_reason})"
        )
    if plan.skip_contracts:
        lines.append(
            "    Lane engine-contracts           : SKIPPED (inputs unchanged since its last green run)"
        )
    else:
        lines.append(
            f"    Lane engine-contracts           : {contracts_submission_note(skip_corpus=plan.skip_corpus)}, -n {plan.contracts_workers} ({plan.contracts_reason});"
        )
        if not plan.gates_queue:
            beside = _overlap_note(
                [
                    pool
                    for pool, runs in (
                        ("gate:make-test's pool", not plan.skip_make_test),
                        ("gate:conform's sweep", not plan.skip_conform),
                    )
                    if runs
                ]
            )
            lines.append(f"                                       {beside}")
        elif not plan.skip_conform:
            lines.append(
                f"                                       QUEUED behind gate:conform ({_queue_reason(plan)} — one heavy pool at a time)"
            )
        elif not plan.skip_make_test:
            lines.append(
                f"                                       QUEUED behind gate:make-test ({_queue_reason(plan)}; gate:conform not running)"
            )
        else:
            lines.append("                                       no other heavy pool running, so no queueing")
    if plan.rerun_gates_only or plan.rerun_kern_only:
        lines.append(
            f"    run_build --kernel-threads          : not passed (a {'gates-only rerun' if plan.rerun_gates_only else 'kern-only rebuild'} enumerates nothing, so there is no fan-out to size)"
        )
    else:
        lines.append(
            f"    run_build --kernel-threads          : {plan.kernel_threads}  ({plan.kernel_reason})"
        )
        lines.append(
            f"    run_build --scratch-beside-default  : {plan.scratch_beside_default}  (at most this many of the heaviest deltas enumerated from scratch beside default, in whole tiers of equal unlocking-rune count, as many as the table build's booking at that width still fits with each booked at SCRATCH_PEAK_BYTES{'' if plan.scratch_beside_default else '; none fits, so every delta waits for default and reads its memo'})"
        )
    if plan.rerun_gates_only or plan.rerun_kern_only:
        lines.append(
            f"    run_build --replay-threads          : not passed (a {'gates-only rerun' if plan.rerun_gates_only else 'kern-only rebuild'} replays nothing, so there is no wave to size)"
        )
    else:
        lines.append(
            f"    run_build --replay-threads          : {plan.replay_threads}  (the string replay's own ceiling, {plan.replay_reason})"
        )
    return lines


def plan_rows(plan: Plan) -> list[console.PlanRow]:
    """Return the plan's steps as the console's rows. Only gate:conform and gate:make-test can show `run?`, which makes the counts line a range: each skip key covers artifacts run_build writes, so a pass that plans the gate may still skip it once the build finishes (`Plan.conform_undecided`, `Plan.make_test_undecided`). That row's note comes from `UNDECIDED_UNTIL_BUILD` and states the condition.

    A pass that skips run_build shows `run` instead, because nothing is rebuilt and `main` has already compared the same key and found no matching green record. A `--fresh` pass also shows `run`, because it reads no green record.
    """
    rows: list[console.PlanRow] = []
    for step in plan.steps:
        undecided = (
            step.name in UNDECIDED_UNTIL_BUILD
            and not step.skipped
            and (plan.make_test_undecided() if step.name == "gate:make-test" else plan.conform_undecided())
        )
        status = (
            console.STATUS_SKIP
            if step.skipped
            else (console.STATUS_MAYBE if undecided else console.STATUS_RUN)
        )
        rows.append(
            console.PlanRow(
                status=status,
                name=step.name,
                note=UNDECIDED_UNTIL_BUILD[step.name] if undecided else step.note,
                argv="" if step.argv is None else " ".join(step.argv),
            )
        )
    return rows


def render_plan(plan: Plan) -> list[str]:
    """Return the plan block, the lines the console prints before step 1 and writes to plan.txt: the commit, the log directory, the step counts and each step's command, then the paths this pass resolved and the concurrency block."""
    stamp = plan.stamp or "(--dry-run: nothing executed)"
    lines = [f"artifact cycle {stamp}  sha {plan.short_id}  host {socket.gethostname()}"]
    if plan.log_dir is not None:
        lines.append(f"logs {plan.log_dir}")
    rows = plan_rows(plan)
    lines.extend(["", console.counts_line(rows), *console.plan_lines(rows), ""])
    lines.append(f"  first run    : {plan.first_run}")
    lines.append(f"  corpus       : {plan.update_corpus}")
    if plan.review_out is not None:
        lines.append(
            f"  staging      : corpus writes redirected to {plan.review_out}; the live corpus at engine/out/review is never written."
        )
    lines.extend(_render_concurrency(plan))
    return lines


@dataclass
class CycleReport:
    """The pass's running record. The `*_status` strings are prose for the summary. The gate strings (`gate_js` and the others) are prose too, but `_step_outcome` reads their leading words (`not run`, `skipped`, `self-skipped`, `green`) to fill the table's outcome column. Decisions read the booleans (`gate_*_green`), which are set when each outcome is decided. A gate that was never joined, because it was skipped or never submitted, leaves its boolean None, which is neither green nor red.

    `step_seconds` and `step_returncodes` fill the summary table's time and outcome columns, and `step_written` its written column: the write meter's figure over the step's window and whether another step's window overlapped it. They come from the same `_StepResult` the timings journal records, so the table and the journal agree, and they are keyed through STEP_ALIASES, so the gates-only child fills the run_build row. `written` is what the meter read over the whole pass (`cycle_timings.PassWrites`), set as the run line is written, for the summary's written line. `run_build_failed` records the cycle's own evaluation of the build gate from the two summary JSONs, which `_step_outcome` reads before the return code. `retention_outcome` is how `_finish`'s retention attempt ended (`ok` or `FAILED`, empty until the attempt ends), so the table reports it even when a stop signal lands after retention.
    """

    pins_pass: bool | None = None
    corpus_units: int | None = None
    corpus_rows: int | None = None
    corpus_batches: int | None = None
    assets_status: str = "not run"
    land_status: str = "not run"
    land: dict | None = None
    land_started: float | None = None
    job_costs_status: str = "not run"
    job_costs_ok: bool | None = None
    gate_js: str = "not run"
    gate_js_green: bool | None = None
    gate_contracts: str = "not run"
    gate_contracts_green: bool | None = None
    gate_conform: str = "not run"
    gate_conform_green: bool | None = None
    gate_make_test: str = "not run"
    gate_make_test_green: bool | None = None
    contracts_proven: bool = False
    conform_proven: bool = False
    interrupted: bool = False
    run_build_failed: bool = False
    retention_detail: str = ""
    retention_outcome: str = ""
    step_seconds: dict[str, float] = field(default_factory=dict)
    step_returncodes: dict[str, int] = field(default_factory=dict)
    step_written: dict[str, tuple[int, bool]] = field(default_factory=dict)
    written: PassWrites | None = None


_Emitter = console.CycleConsole


class _ChildRegistry:
    """Thread-safe set of live subprocesses, so a Ctrl-C or any signal `stop_signals` catches can terminate and reap every child. A child added as uninterruptible (the land, which moves the corpus and the store together) is never signaled: `terminate_all` waits for it to finish, however long that takes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: set[subprocess.Popen] = set()
        self._uninterruptible: set[subprocess.Popen] = set()
        self._closed = False
        self.killed_count = 0

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def add(self, proc: subprocess.Popen, *, uninterruptible: bool = False) -> bool:
        """Track a live child. Return False once terminate_all has run, so a thread that unblocks after a stop (a queue-policy gate task waiting on an earlier gate's future) never leaves a new subprocess untracked. The caller terminates that child instead, or waits for an uninterruptible one."""
        with self._lock:
            if self._closed:
                return False
            (self._uninterruptible if uninterruptible else self._children).add(proc)
            return True

    def remove(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._children.discard(proc)
            self._uninterruptible.discard(proc)

    def waits_for_uninterruptible(self) -> bool:
        """Return whether an uninterruptible child is still running, so the caller can say it is waiting for it before `terminate_all` does."""
        with self._lock:
            return any(proc.poll() is None for proc in self._uninterruptible)

    def terminate_all(self) -> None:
        with self._lock:
            self._closed = True
            children = list(self._children)
            steady = list(self._uninterruptible)
            self._children.clear()
            self._uninterruptible.clear()
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
        for proc in steady:
            proc.wait()


STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class CycleStopped(KeyboardInterrupt):
    """A stop signal the pass caught, raised in the main thread wherever it was waiting, so `_run_cycle` handles it as it handles a Ctrl-C: terminate and reap every child, then write the interrupted summary naming the signal. It subclasses KeyboardInterrupt so that no `except Exception` catches it."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@contextlib.contextmanager
def stop_signals() -> Iterator[None]:
    """Turn SIGINT, SIGTERM and SIGHUP into `CycleStopped` for the length of a pass. Every child but the land stays in the cycle's process group, so a signal sent to the group reaches them directly; the land runs in its own session and is waited for (`_ChildRegistry`). The handler is for a signal that reaches only the driver, such as `kill` on `make`, which passes SIGTERM to its recipe, whose `uv run` passes it on. Without the handler the driver would exit and leave its children running.

    Only the first signal raises and later ones are ignored, because a group signal can arrive more than once (directly, and again as `uv run` forwards it, which it does for every signal but a first SIGINT) and a repeat must not interrupt the cleanup the first started. A signal that was ignored when the pass started stays ignored, so a pass under `nohup` outlives its shell. Only the main thread can install handlers, so on any other thread this installs none. The previous handlers are restored on exit.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    caught: list[int] = []

    def stop(signum: int, _frame) -> None:
        if caught:
            return
        caught.append(signum)
        raise CycleStopped(signum)

    previous = {}
    for signum in STOP_SIGNALS:
        handler = signal.getsignal(signum)
        if handler == signal.SIG_IGN:
            continue
        previous[signum] = signal.SIG_DFL if handler is None else handler
        signal.signal(signum, stop)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@dataclass
class _StepResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    elapsed: float
    peak_rss_bytes: int | None = None
    written_bytes: int | None = None
    written_overlap: tuple[str, ...] = ()


def _terminate_child(proc: subprocess.Popen, *, uninterruptible: bool = False) -> None:
    """Terminate one child (SIGTERM, 3 s grace, then SIGKILL) and close its pipes, or, for an uninterruptible child, wait for it to finish. This handles the race where the registry is torn down between a `Popen` and its `registry.add`."""
    if uninterruptible:
        proc.wait()
    else:
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
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    stream: bool,
    env: dict[str, str] | None = None,
    uninterruptible: bool = False,
) -> _StepResult:
    """Run one child to completion, passing every line of both pipes to the console, which logs each line and shows the ones that matter. This opens the step's banner but does not close it: the caller closes it with `_close_step` once it has read the step's detail from the files the child wrote. `env`, when given, is overlaid on this process's environment for this child only.

    `stream` also sends the child's unparsed lines to the terminal. Only the job-costs diff uses it, because it is the one child output a person must read to act on. Other child output reaches the terminal only as console events, and every line reaches the log, so a failed step's full output is replayed under its banner.

    When the registry has been torn down, a stop signal has already arrived, so the child is not started (or is terminated at once), no banner opens, and the result's return code is 130. The child stays in the cycle's process group, so a signal sent to that group reaches it and everything it spawns. An `uninterruptible` child (the land) starts its own session instead, so neither a Ctrl-C at the terminal nor a signal to the group reaches it, and the registry waits for it rather than signaling it. It also inherits the pass lock's descriptor when this process holds the lock (`pass_lock`), so a driver that dies while its land runs does not let the next pass start before the land has finished.
    """
    if registry.closed:
        return _StepResult(name, 130, "", "", 0.0)
    start = time.perf_counter()
    proc = subprocess.Popen(
        argv,
        cwd=ROOT,
        env=None if env is None else {**os.environ, **env},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        start_new_session=uninterruptible,
        pass_fds=tuple(_held_pass_lock) if uninterruptible else (),
    )
    if not registry.add(proc, uninterruptible=uninterruptible):
        _terminate_child(proc, uninterruptible=uninterruptible)
        return _StepResult(name, 130, "", "", 0.0)
    substep_of = SUBSTEP_PARENTS.get(name)
    if substep_of is None:
        emit.step_start(name, argv, step_description(name), verbatim=stream)
    else:
        emit.note(name, f"$ {' '.join(argv)}")
    out_buf: list[str] = []
    err_buf: list[str] = []

    def pump(pipe, buf: list[str], which: str) -> None:
        for line in pipe:
            line = line.rstrip("\r\n")
            buf.append(line)
            event = emit.child_line(name, which, line)
            if event is None and stream and substep_of is not None:
                emit.emit(line)
        pipe.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, out_buf, console.STDOUT)),
        threading.Thread(target=pump, args=(proc.stderr, err_buf, console.STDERR)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    peak_rss = reap_peak_rss_bytes(proc)
    returncode = proc.wait()
    registry.remove(proc)
    elapsed = time.perf_counter() - start
    result = _StepResult(name, returncode, "\n".join(out_buf), "\n".join(err_buf), elapsed, peak_rss)
    if substep_of is None:
        if returncode != 0:
            emit.failure_dump(name)
    else:
        outcome = "ok" if returncode == 0 else f"FAILED (exit {returncode})"
        emit.note(name, f"{name} {outcome}  {console.fmt_duration(elapsed)}")
        emit.substep_end(name)
    return result


_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PYTEST_SUMMARY_LINE = re.compile(r"(?:FAILED|ERROR) ([^\s\[]+|\S+?\[.*?\])(?: - |$)")


def _summary_line_id(line: str) -> str:
    """Return the test id on a pytest FAILED or ERROR summary line, or the line itself, marked as unparsed, when it fits no id shape."""
    match = _PYTEST_SUMMARY_LINE.match(line)
    return match[1] if match else f"unparsed summary line: {line}"


def classify_engine_suite_output(stdout: str, returncode: int, check: str) -> CheckResult:
    """Return the engine suite's gate result from pytest's FAILED and ERROR summary lines. The cycle's engine gate and `engine.tools.engine_gate` both use it. `check` ("engine-contracts") is only copied into the result to name the suite. ANSI escape codes are stripped first, because pytest colors its output whenever FORCE_COLOR is set (the agent harness sets it), and a colored line does not start with "FAILED " or "ERROR ". An id ends at the ` - ` that opens its line's message or at the end of the line, and a parametrized id at the first `]` followed by either, because a parameter can hold spaces, a ` - ` or a `]` of its own; a parameter that holds `] - ` is cut there, because the line alone cannot tell it from a message. A line that fits no id shape is a failure too, filed under its own text so the run stays red and the line stays readable. A nonzero exit with no parsed failure lines is red. Every failure counts as unexplained, because the suite has no list of accepted failures, and every green result is recordable."""
    lines = [_ANSI_SGR.sub("", line) for line in stdout.splitlines()]
    failed_ids = [_summary_line_id(line) for line in lines if line.startswith("FAILED ")]
    error_ids = [_summary_line_id(line) for line in lines if line.startswith("ERROR ")]
    hard = failed_ids + error_ids
    if returncode != 0 and not hard:
        hard.append(f"pytest exited {returncode} with no parsed FAILED/ERROR lines")
    return CheckResult(
        check=check,
        outcome="red" if hard else "green",
        status=f"FAILED ({len(hard)} unexplained)" if hard else "green",
        failures=[f"engine suite: {len(hard)} unexplained failure(s)"] if hard else [],
        failed_ids=hard,
        recordable=not hard,
    )


# The failure reason for a run_build that left a summary missing, except a full build whose records name the gate that stopped it (`_gate_that_stopped_a_full_build`) and a child that reported its red (`_reported_red`). The cycle's failure list and the timings journal's check line both use it, so the two record the same text.
_NO_SUMMARIES_REASONS = ("run_build did not write both summary files",)

BUILD_GATES_ONLY_STEP = "build:gates-only"
BUILD_KERN_ONLY_STEP = "build:kern-only"

STEP_ALIASES = {BUILD_GATES_ONLY_STEP: "build", BUILD_KERN_ONLY_STEP: "build"}


def _summary_object(path: Path) -> dict | None:
    try:
        summary = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    return summary if isinstance(summary, dict) else None


def _unreadable_summary_red(check: str, paths: list[Path]) -> CheckResult:
    """Return the red of a `check` whose summaries at `paths` exist but do not parse to an object (`_summary_object`), as a child killed while it wrote one or a full disk leaves them, naming each file."""
    return CheckResult(
        check=check,
        outcome="red",
        status="FAILED (unreadable summary)",
        failures=[f"{check} gate: {path} is unreadable or not a JSON object" for path in paths],
        failed_ids=[],
    )


def _reported_red(stdout: str, check: str) -> CheckResult | None:
    """Return the red a `check` child reported on the last `console.CHECK` line of `stdout` (`run_build_gates._record_cli_check` prints one under `CYCLE_RUN_ENV`), or None when it printed none, as a child killed before it reported does, or when that line is not a red of `check` with its status and at least one failure."""
    lines = [line for line in stdout.splitlines() if line.startswith(console.CHECK)]
    if not lines:
        return None
    try:
        reported = json.loads(lines[-1][len(console.CHECK) :])
    except ValueError:
        return None
    if not isinstance(reported, dict) or reported.get("check") != check or reported.get("outcome") != "red":
        return None
    status = reported.get("status")
    failures = reported.get("failures")
    if not isinstance(status, str) or not isinstance(failures, list) or not failures:
        return None
    if not all(isinstance(failure, str) for failure in failures):
        return None
    return CheckResult(check=check, outcome="red", status=status, failures=list(failures), failed_ids=[])


def _replay_record_mtime() -> int | None:
    """Return the modification time of the string replay's record, which run_build writes beside its summaries (`run_build.REPLAY_SUMMARY`), or None when there is none."""
    from engine.pipeline import run_build

    try:
        return (
            cycle_paths.BUILD_SUMMARY_FILES["pipeline"].with_name(run_build.REPLAY_SUMMARY).stat().st_mtime_ns
        )
    except OSError:
        return None


def _gate_that_stopped_a_full_build(missing: list[str], replay_before: int | None) -> CheckResult | None:
    """Return the red of the gate that stopped a full build the cycle spawned, read from the records it wrote, or None when they do not say what stopped it: the string replay's record did not move during the spawn or does not parse with a boolean `pass` (the next paragraph says why either returns None even when the summaries name a gate), the summaries name no gate, or a summary it reads does not parse to an object, as a child killed mid-write leaves it. `missing` lists the summaries the build did not write, and `replay_before` is `_replay_record_mtime` from before the spawn.

    A string replay that found the tables incomplete fails the build at the join after the Manual-pin gate (`run_build.TableGates.join`), and the child reports it in place of any glyph-chain failure (`run_build.run`) and any defect or pin failure (`run_build.full_run`). When the pin gate has written its summary by then, no summary is missing, and the cycle files the red the child reported (`_reported_red`) from its nonzero exit. The replay writes its record whether it passed or failed, and the record outlives the build, since the next build narrows its walk against it, so a record is this build's own only when its modification time moved during the spawn. One that moved and carries `pass` false is filed under the child's message for it. A replay that returns always writes its record, because a full build passes it the tables' stamp (`run_build.run_replay_strings`), and a child that exits on its own after starting the table-only branch has waited for that branch to end (`run_build.run`, `run_build.full_run`). So a record that did not move, or one that moved but does not parse with a boolean `pass`, tells nothing about this build's replay: the child stopped before its tables were built, which leaves no pipeline summary; or its replay raised before or during the record's write, and the child exited with that error, or with the packing's failure ahead of it, which no record holds; or the child was killed before that write finished. Either way, however the child exited, this returns None, so the cycle files the red the child reported, if it reported one (`_reported_red`), rather than name a gate the child did not report.

    Otherwise a full build writes pipeline_summary.json after the compile and the read-back, and exits on defect errors before the Manual-pin gate writes manual_pins_summary.json, so a missing pin summary beside a pipeline summary names the defect gate. The cycle deleted both before the spawn, so every summary read here is that build's own. A gates-only child keeps the build's pipeline_summary.json, whose defect fields a refusal before its defect gate leaves stale, so its summaries are never read this way, and the cycle files the red the child reported instead. The table-only branch's other failures (the packing, the witnesses, the shipped order) leave no record this reads; when one comes ahead of a red this names in serial order (`run_build.TableGates.first_red`: the packing ahead of the replay, any of them ahead of a defect or pin red), the child exits with that failure instead, which its replayed log shows.
    """
    from engine.pipeline import run_build
    from engine.pipeline.run_build_gates import manual_pin_gate_failure

    files = cycle_paths.BUILD_SUMMARY_FILES
    if _replay_record_mtime() in (None, replay_before):
        return None
    replay = run_build.read_replay_record(files["pipeline"].parent)
    if replay is None or not isinstance(replay.get("pass"), bool):
        return None
    if not replay["pass"]:
        return CheckResult(
            check="build",
            outcome="red",
            status="FAILED",
            failures=[f"the string replay found the tables incomplete: {replay.get('error')}"],
            failed_ids=[],
        )
    pipeline = None if "pipeline" in missing else _summary_object(files["pipeline"])
    if pipeline is None:
        return None
    failure = _defect_gate_failure(pipeline)
    if failure is None and "manual_pins" not in missing:
        manual_pins = _summary_object(files["manual_pins"])
        if manual_pins is None:
            return None
        failure = manual_pin_gate_failure(manual_pins)
    if failure is None:
        return None
    return CheckResult(check="build", outcome="red", status="FAILED", failures=[failure], failed_ids=[])


def _do_run_build(
    report: CycleReport,
    *,
    spawn,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    argv: list[str] | None = None,
    skip: bool = False,
    skip_note: str = "",
    gates_only: bool = False,
    kern_only: bool = False,
    record: bool = False,
    fingerprint: str | None = None,
    timings: CycleTimings | None = None,
) -> CheckResult | None:
    """Run the build, or reuse it when `skip` is set, and evaluate its gate from the two summary JSONs. The skip path leaves engine/out/build untouched and re-evaluates the summaries on disk, which is sound because run_build's outputs are deterministic and carry no timestamps. A live green is recorded only if the fingerprint still matches after the run, because an input edited mid-run means the tested content is no longer on disk. A live red whose fingerprint matches the green record deletes the record.

    With `gates_only`, the child is `run_build --gates-only` over the tables and font on disk. It rewrites the defect fields of `pipeline_summary.json` in place and exits with an error when that file is missing, so it is the one summary not deleted before the spawn. Its green rests on a prior green build with only comparison-side changes since (`gates_only_rerun`; a failed run never vouches for the artifacts it left) and tables stamped before and after the child runs (`tables_stamped`). Its stop is filed from the red it reported (`_reported_red`), never from that summary, which a refusal before the defect gate leaves as the build wrote it. The cycle is the sole green-record writer for its children, and a nonzero exit overrides passing summaries. The child spawns as `BUILD_GATES_ONLY_STEP`, not `run_build`, because `make cycle-timings ARGS='--by-step'` groups rows by step name and host, and a gates-only run of a few seconds recorded as `run_build` would distort the figures for a full build.

    With `kern_only`, the child is `run_build --kern-only`, which recompiles the font over the tables on disk after an edit that moved only runes' kern blocks. No summary is deleted before the spawn: the child runs its glyph chain beside engine/out/build and replaces the font, the kern files and `pipeline_summary.json` only once read-back has passed, deleting that summary first and writing it last (`run_build._publish_kern_only`), and it keeps the Manual-pin summary, whose inputs the edit did not move. So a child that fails leaves the prior font beside the summaries that describe it, and its nonzero exit files a red whatever those summaries say, while the next pass can plan a kern-only rebuild again; a child killed while it publishes leaves no pipeline summary, which is filed as a missing summary and makes the next pass rebuild. Its green rests on a prior green build with only kern lines moved since (`kern_only_rebuild`), tables stamped before and after the child runs, as a gates-only rerun's does, and a Stage A record matching the sources before and after it (`stage_a_current`). It spawns as `BUILD_KERN_ONLY_STEP`.

    Every path, the skip included, records a check line in the timings journal, because a skip is an evaluation of this build's summaries. The child records none of its own, because it inherits CYCLE_RUN_ENV; it prints the result it would have recorded on a `console.CHECK` line instead. A full build whose records name what stopped it (a string replay that found the tables incomplete, the defect gate or the Manual-pin gate; `_gate_that_stopped_a_full_build` says when they do) is recorded red with that failure. Any other child that left a summary missing, or exited nonzero despite passing summaries, is recorded with the red it reported (`_reported_red`), a refusal before any gate included, and one that reported none with `_NO_SUMMARIES_REASONS` or its exit code. When every summary is present, one that does not parse to an object is recorded red naming the file (`_unreadable_summary_red`), on the skip path too. A run that left a summary missing is filed as above even when another summary does not parse, so a gates-only child killed while it rewrote pipeline_summary.json keeps `_NO_SUMMARIES_REASONS`. The cycle's failure list gets the same text in every case.
    """
    step = BUILD_GATES_ONLY_STEP if gates_only else BUILD_KERN_ONLY_STEP if kern_only else "build"
    result: _StepResult | None = None
    replay_before: int | None = None
    gates_only_eligible = False
    if skip:
        emit.step_skipped("build", f"{skip_note}; evaluating the gate from the recorded summaries")
    else:
        if (gates_only or kern_only) and record and fingerprint is not None:
            prior = read_green_record(cycle_paths.BUILD_GREEN)
            current = run_build_skip_files(ROOT)
            rerun = gates_only_rerun if gates_only else kern_only_rebuild
            gates_only_eligible = (
                rerun(prior, current) is not None
                and tables_stamped()
                and (gates_only or stage_a_current(ROOT))
            )
        if not gates_only and not kern_only:
            replay_before = _replay_record_mtime()
        for name, path in cycle_paths.BUILD_SUMMARY_FILES.items():
            if kern_only or (gates_only and name == "pipeline"):
                continue
            path.unlink(missing_ok=True)
        result = spawn(step, argv, emit=emit, registry=registry, stream=False)
    missing = [name for name, path in cycle_paths.BUILD_SUMMARY_FILES.items() if not path.exists()]
    summaries = (
        {}
        if missing
        else {name: _summary_object(path) for name, path in cycle_paths.BUILD_SUMMARY_FILES.items()}
    )
    unreadable = [
        cycle_paths.BUILD_SUMMARY_FILES[name] for name, summary in summaries.items() if summary is None
    ]
    if missing or unreadable:
        stopped: CheckResult | None = None
        if unreadable:
            stopped = _unreadable_summary_red("build", unreadable)
        elif result is not None:
            if not gates_only and not kern_only:
                stopped = _gate_that_stopped_a_full_build(missing, replay_before)
            if stopped is None:
                stopped = _reported_red(result.stdout, "build")
        if stopped is None:
            for name in missing:
                emit.note(
                    "build",
                    f"build gate failure: missing {name} summary ({cycle_paths.BUILD_SUMMARY_FILES[name]}) — run_build did not complete",
                )
        if result is not None:
            _close_step(emit, report, step, result, "FAILED (no summaries)" if stopped is None else "FAILED")
        if record and fingerprint is not None:
            settle_green(
                cycle_paths.BUILD_GREEN, fingerprint, False, lambda: run_build_skip_fingerprint(ROOT)
            )
        if timings is not None:
            timings.record_check(
                stopped
                if stopped is not None
                else CheckResult(
                    check="build",
                    outcome="red",
                    status="FAILED (no summaries)",
                    failures=list(_NO_SUMMARIES_REASONS),
                    failed_ids=[],
                )
            )
        return stopped
    loaded = {name: summary for name, summary in summaries.items() if summary is not None}
    gate = evaluate_run_build_gate(loaded["pipeline"], loaded["manual_pins"])
    if result is not None and result.returncode != 0 and gate.ok:
        gate = _reported_red(result.stdout, "build") or CheckResult(
            check="build",
            outcome="red",
            status=f"FAILED (exit {result.returncode})",
            failures=[f"build gate: exited {result.returncode} despite passing summaries"],
            failed_ids=[],
        )
    if timings is not None:
        timings.record_check(gate)
    report.pins_pass = bool(loaded["manual_pins"].get("pass"))
    if result is not None:
        _close_step(emit, report, step, result, "ok" if gate.ok else "FAILED")
    if record and fingerprint is not None:
        if not gate.ok:
            settle_green(
                cycle_paths.BUILD_GREEN, fingerprint, False, lambda: run_build_skip_fingerprint(ROOT)
            )
        elif not skip:
            if (gates_only or kern_only) and (
                not gates_only_eligible or not tables_stamped() or (kern_only and not stage_a_current(ROOT))
            ):
                emit.note(
                    "build",
                    f"run_build {'gates-only' if gates_only else 'kern-only'} green, but the prior build or tables stamp does not permit reusing these artifacts — green not recorded",
                )
            elif not settle_green(
                cycle_paths.BUILD_GREEN,
                fingerprint,
                True,
                lambda: run_build_skip_fingerprint(ROOT),
                files_of=lambda: run_build_skip_files(ROOT),
            ):
                emit.note(
                    "build", "run_build green, but its inputs changed while it ran — green not recorded"
                )
    return gate


def _run_build_reasons(gate: CheckResult | None) -> list[str]:
    if gate is None:
        return list(_NO_SUMMARIES_REASONS)
    return list(gate.failures)


def _read_corpus_totals(report: CycleReport, corpus_dir: Path) -> bool:
    try:
        manifest = json.loads((corpus_dir / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    totals = manifest.get("totals") or {}
    report.corpus_units = totals.get("units")
    report.corpus_rows = totals.get("rows")
    report.corpus_batches = totals.get("batches")
    return True


def _served_manifest() -> dict | None:
    try:
        manifest = json.loads((REVIEW_OUT / "manifest.json").read_text())
    except OSError, ValueError:
        return None
    return manifest if isinstance(manifest, dict) else None


def _served_generated_at() -> str | None:
    value = (_served_manifest() or {}).get("generated_at")
    return value if isinstance(value, str) else None


def _broadcast_review_reload(emit: console.CycleConsole, step: str, kind: str) -> None:
    """Tell the tabs open on a listening review server that what it serves changed, with the served manifest's value for `kind`: `ams:corpus/<generated_at>` once a pass's land has moved the served corpus and its store together, so the tabs move onto a corpus whose store is already on it, and `ams:assets/<static hash>` after an assets refresh. The app sends its unsaved changes and reloads when the reader is not typing (engine/review/static/reload.js). With no server listening, or a manifest that does not name the value, nothing is sent, and a tab moves on its next save or status check instead. The engine suite switches it off with `cycle_paths.REVIEW_RELOAD_ENABLED`, because it goes to the live port."""
    if not cycle_paths.REVIEW_RELOAD_ENABLED:
        return
    manifest = _served_manifest()
    if manifest is None:
        return
    if kind == "corpus":
        value = manifest.get("generated_at")
    else:
        value = (manifest.get("inputs_fingerprint") or {}).get("static")
    if not isinstance(value, str) or not value:
        return
    if force_reload(f"ams:{kind}/{value}"):
        what = "corpus" if kind == "corpus" else "app files"
        emit.note(step, f"told the open review tabs to move onto the new {what}")


def _do_assets_refresh(
    report: CycleReport, *, spawn, emit: console.CycleConsole, registry: _ChildRegistry, plan: Plan
) -> bool:
    """Copy the review app's static files over the served corpus and restamp the manifest's `static` component, on a pass where that component is the only input that changed. It runs in place of the corpus build, and later steps treat the pass as a corpus skip: no unit, shard, sidecar or `generated_at` changes, so the carry is the identity and the review server keeps running. Once every file is in place, the open tabs are told to reload onto them (`_broadcast_review_reload`)."""
    result = spawn("assets-refresh", plan.argv("assets-refresh"), emit=emit, registry=registry, stream=False)
    if result.returncode != 0:
        emit.note("assets-refresh", f"ERROR: review.build refresh-assets exited {result.returncode}.")
        report.assets_status = f"FAILED (exit {result.returncode})"
        _close_step(emit, report, "assets-refresh", result)
        return False
    report.assets_status = "refreshed in place (units, sidecars and generated_at unmoved)"
    if plan.broadcast:
        _broadcast_review_reload(emit, "assets-refresh", "assets")
    _close_step(emit, report, "assets-refresh", result)
    return True


def _in_process_step(
    report: CycleReport, emit: console.CycleConsole, plan: Plan, name: str, work
) -> str | None:
    """Run one in-process step between its banner and its closing line, and return its detail, or None when `work` raised, which is reported as the step's failure. The step's seconds and return code are recorded as a spawned step's are."""
    emit.step_start(name, None, plan.describe(name))
    started = time.perf_counter()
    try:
        detail = work()
    except Exception as exc:
        report.step_seconds[name] = time.perf_counter() - started
        report.step_returncodes[name] = 1
        emit.note(name, f"ERROR: {exc!r}")
        emit.step_end(name, None, "FAILED", repr(exc))
        return None
    report.step_seconds[name] = time.perf_counter() - started
    emit.step_end(name, None, "ok", detail)
    return detail


def _land_report(plan: Plan) -> dict | None:
    if plan.scratch_dir is None:
        return None
    try:
        data = json.loads((plan.scratch_dir / landing.REPORT_NAME).read_text())
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def land_detail(data: dict) -> str:
    """Return the land row's detail from the report the land wrote: the stamp it landed and the verdicts the store holds after it, whether it carried the store onto that stamp with what it orphaned and dropped, and how long it held the store's lock."""
    records = console.fmt_count(data.get("records") or 0)
    if data.get("carried"):
        parts = [f"carried {records} verdicts onto {data.get('new_stamp')}"]
    else:
        parts = [f"landed {data.get('new_stamp')}, the store's {records} verdicts as they were"]
    for key, label in (("orphaned", "orphaned"), ("skips_dropped", "skips dropped")):
        if data.get(key):
            parts.append(f"{console.fmt_count(data[key])} {label}")
    if data.get("locked_s") is not None:
        parts.append(f"store locked {data['locked_s']:.1f} s")
    return ", ".join(parts)


def _do_land(
    report: CycleReport, *, spawn, emit: console.CycleConsole, registry: _ChildRegistry, plan: Plan
) -> dict | None:
    """Run the land as its own child (`engine.review.landing`), which neither a Ctrl-C nor a signal to the cycle's group reaches, and which the registry waits for rather than signals. Returns the report the land wrote when it landed, and None when it aborted or failed."""
    report.land_started = time.monotonic()
    result = spawn(
        "land", plan.argv("land"), emit=emit, registry=registry, stream=False, uninterruptible=True
    )
    data = _land_report(plan)
    if result.returncode == 0 and data is not None and data.get("landed"):
        report.land = data
        report.land_status = land_detail(data)
        _close_step(emit, report, "land", result)
        return data
    reason = (data or {}).get("reason") or f"exit {result.returncode}"
    report.land_status = f"FAILED ({reason})"
    emit.note("land", f"ERROR: the land did not complete: {reason}")
    _close_step(emit, report, "land", result, "FAILED")
    return None


def _delete_discard(landed: dict | None) -> None:
    """Delete the tree a land swapped out (`landing.land`'s `keep_discard`), once the open tabs have been told to move off it. A delete cut short leaves it under its discard name, which the next pass's `recover_next_corpus` deletes."""
    discard = (landed or {}).get("discard")
    if isinstance(discard, str) and discard:
        shutil.rmtree(discard, ignore_errors=True)


def _settle_interrupted_land(
    report: CycleReport, plan: Plan, emit: console.CycleConsole, served_before: str | None
) -> None:
    """Finish what a pass owes a land that a stop signal did not reach, once the registry has waited for it: record it on the report when it landed, so the summary reads it as run, tell the open tabs to move when the served corpus changed and no broadcast has gone out, and delete the tree it swapped out."""
    if report.land_started is None:
        return
    if report.land is None:
        data = _land_report(plan)
        if data is not None and data.get("landed"):
            report.land = data
            report.land_status = land_detail(data)
            report.step_seconds["land"] = time.monotonic() - report.land_started
            report.step_returncodes["land"] = 0
    if plan.review_out is None and plan.broadcast and _served_generated_at() != served_before:
        _broadcast_review_reload(emit, "land", "corpus")
    _delete_discard(report.land)


def _do_corpus_build(
    report: CycleReport,
    *,
    spawn,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    review_out: Path | None,
    argv: list[str] | None = None,
    skip: bool = False,
    skip_note: str = "",
) -> bool:
    """Rebuild the review corpus, or reuse it when `skip` is set. Both paths read the totals from the corpus's manifest.json, whose unit and row totals the build checks against the shards it wrote, so the summary reports what the corpus on disk contains. A nonzero exit reads no totals, since the manifest beside it is the previous pass's."""
    corpus_dir = review_out if review_out is not None else REVIEW_OUT
    if skip:
        if not _read_corpus_totals(report, corpus_dir):
            emit.note(
                "corpus-build",
                "ERROR: corpus-build skip: the manifest vanished mid-cycle; rerun with --fresh.",
            )
            return False
        emit.step_skipped("corpus-build", skip_note)
        return True
    result = spawn("corpus-build", argv, emit=emit, registry=registry, stream=False)
    if result.returncode != 0:
        emit.note("corpus-build", f"ERROR: review.build exited {result.returncode}.")
        _close_step(emit, report, "corpus-build", result)
        return False
    if not _read_corpus_totals(report, corpus_dir):
        emit.note("corpus-build", "ERROR: review.build exited 0 but left no readable manifest.json.")
        _close_step(emit, report, "corpus-build", result, "FAILED (no manifest)")
        return False
    _close_step(emit, report, "corpus-build", result)
    return True


_MOVED_CONSTANT = re.compile(r"^([A-Z][A-Z0-9_]*): ")
_TRIPPED_PROPOSAL = re.compile(r"^  proposal  : (?!.*, headroom used, not yet an overrun;)(.+)$")


def _do_job_costs(
    report: CycleReport, *, spawn, emit: console.CycleConsole, registry: _ChildRegistry, plan: Plan
) -> None:
    """Compare the checked-in per-unit memory peaks with what this machine has measured (`calibrate_budgets --check`). Several pool widths are the machine's memory divided by one of these constants, so a stale constant makes a pool the wrong width. The step runs after the gates join because it reads the timings journal, which this pass's pools have just appended to.

    It never fails the pass: a wrong width costs wall-clock time or swap but cannot make an artifact wrong. The summary line and `job_costs_ok` report an overrun, and committing the re-measured constant accepts it, A check that cannot run is reported as informational too.

    On an overrun the status quotes each tripped row's proposal line from the check's report: the value the constant's own rule sets from the peak, and the width that value gives here, or for the kernel-build row which constants to re-measure instead. A row that has only used its headroom also prints a proposal, which the status leaves out, because that row did not trip the check. The step then runs `calibrate_budgets --moved`, which prints each checked constant whose working-tree value differs from its value at `HEAD`, and the status names those constants as already moved. The comparison is of values, not of the files' text, so an uncommitted edit elsewhere in a constant's file moves nothing. It runs only on an overrun because only then does the reader need to know whether the acceptance is already drafted.
    """
    check = spawn("job-costs", plan.argv("job-costs"), emit=emit, registry=registry, stream=False)
    if check.returncode == 0:
        report.job_costs_status = "checked (every measured unit's peak fits its checked-in constant)"
        report.job_costs_ok = True
        _close_step(emit, report, "job-costs", check, "ok")
        return
    if check.returncode != 1:
        report.job_costs_status = f"check FAILED (exit {check.returncode}) — informational"
        report.job_costs_ok = None
        _close_step(emit, report, "job-costs", check, "ok")
        return
    emit.substep(SUBSTEP_PARENTS["job-costs-diff"], "job-costs-diff")
    diff = spawn(
        "job-costs-diff",
        ["uv", "run", "python", "-m", "engine.tools.calibrate_budgets", "--moved"],
        emit=emit,
        registry=registry,
        stream=True,
    )
    status = (
        "OVERRUN (a measured peak outruns its checked-in constant — see above; re-measure the constant and "
        "commit it, and that commit is the acceptance)"
    )
    for line in check.stdout.splitlines():
        if proposal := _TRIPPED_PROPOSAL.match(line):
            status += f" — proposal: {proposal[1]}"
    moved = [match[1] for line in diff.stdout.splitlines() if (match := _MOVED_CONSTANT.match(line))]
    if diff.returncode == 0 and moved:
        status += f" — {' and '.join(moved)} {'has' if len(moved) == 1 else 'have'} already moved in the working tree"
    report.job_costs_status = status
    report.job_costs_ok = False
    _close_step(emit, report, "job-costs", check, "ok")


def _gate_js_task(
    argv: list[str], spawn, emit: console.CycleConsole, registry: _ChildRegistry
) -> _StepResult:
    result = spawn("gate:js", argv, emit=emit, registry=registry, stream=False)
    _close_gate(emit, "gate:js", result)
    return result


MAKE_TEST_SELF_SKIP = "make test: SKIPPED —"
MAKE_TEST_SELF_SKIP_STATUS = "self-skipped (input closure unchanged since its last green run)"


def make_test_self_skipped(stdout: str) -> bool:
    """Return whether the font suite's wrapper skipped itself because its input closure was unchanged. The wrapper exits zero either way, so without this check the cycle would report that the suite ran on a pass that tested nothing there."""
    return any(line.startswith(MAKE_TEST_SELF_SKIP) for line in stdout.splitlines())


def _gate_make_test_task(
    argv: list[str], spawn, emit: console.CycleConsole, registry: _ChildRegistry
) -> _StepResult:
    result = spawn("gate:make-test", argv, emit=emit, registry=registry, stream=False)
    if result.returncode == 0 and make_test_self_skipped(result.stdout):
        emit.step_end("gate:make-test", result, "ok", MAKE_TEST_SELF_SKIP_STATUS)
    else:
        _close_gate(emit, "gate:make-test", result)
    return result


def _spawn_with_env(spawn, env: dict[str, str]):
    """Return a spawn callable that overlays `env` on one child's environment. Setting the variables in os.environ would pass them to every child this process spawns (run_build, the corpus build, the engine suite), so one gate's pytest width would also apply to the others' `-n auto` pools. The wrapper changes only the environment, never the argv, so the plan stays the only source of each child's command, and it passes any other keyword through, so a line hook (`_spawn_with_line_hook`) can wrap it."""

    def spawn_with_env(name, argv, *, emit, registry, stream, **passthrough):
        return spawn(name, argv, emit=emit, registry=registry, stream=stream, env=env, **passthrough)

    return spawn_with_env


def _gate_conform_task(
    queue: bool,
    make_fut: Future | None,
    spawn,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    argv: list[str],
) -> CheckResult:
    """Run gate:conform, the exhaustive font-versus-settlement sweep over the fresh Senior-Regular.otf (`run_build --conform-only`), and return its result. Under the default overlap policy it starts as soon as it is submitted, once the build gate has passed, and runs beside whichever of gate:make-test and the engine suite the pass runs. When the heavy gates queue (`queue`: the queue policy, or a pass that runs gate:make-test with no build lane, `gates_queue`), it waits for gate:make-test, and the engine suite waits for it, so only one heavy pool runs at a time. The module docstring gives the reason the overlap policy is the default. The previous conform_summary.json is deleted just before the sweep starts, so the result can only come from this pass's sweep. A child that left no summary that parses to an object is filed under the red it reported (`_reported_red`), such as its refusal of tables stamped from other sources. One that reported none is filed red naming the file when it left one that does not parse (`_unreadable_summary_red`), and under `evaluate_conform_gate`'s missing-summary red when it left none. A skipped gate never runs this task and leaves the file alone."""
    cycle_paths.CONFORM_SUMMARY.unlink(missing_ok=True)
    if queue:
        _await_gate_futures(make_fut)
    result = spawn("gate:conform", argv, emit=emit, registry=registry, stream=False)
    summary = _summary_object(cycle_paths.CONFORM_SUMMARY)
    if summary is not None:
        gate = evaluate_conform_gate(summary)
    else:
        gate = _reported_red(result.stdout, "conform") or (
            _unreadable_summary_red("conform", [cycle_paths.CONFORM_SUMMARY])
            if cycle_paths.CONFORM_SUMMARY.exists()
            else evaluate_conform_gate(None)
        )
    if result.returncode != 0 and not gate.failures:
        # A sweep whose summary passed but whose process exited nonzero stopped somewhere the summary does not describe, so the exit code overrides the summary's outcome.
        gate = CheckResult(
            check="conform",
            outcome="red",
            status=f"FAILED (exit {result.returncode})",
            failures=[f"conform gate: exited {result.returncode} despite a passing summary"],
            failed_ids=[],
        )
    _close_gate(emit, "gate:conform", result, gate)
    return gate


def _await_gate_futures(*futures: Future | None) -> None:
    """Wait until each given gate has finished, however it finished. A gate that raised is reported when `_join_gates` collects it, and the waiting gate still runs."""
    for fut in futures:
        if fut is not None:
            try:
                fut.result()
            except Exception:
                pass


def _gate_contracts_task(
    queue: bool,
    conform_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    argv: list[str],
    force: bool = False,
    record: bool = False,
) -> CheckResult:
    """Run gate:engine-contracts, the engine suite (every test under engine/, none of which reads a live build artifact), and return its result. Because it reads no build output, it is submitted right after gate:conform, once the build gate has passed, and runs beside the corpus build at the width `contracts_pool_width` sets on its environment: the cores the build's process leaves free, less gate:make-test's pool on a pass that runs that gate while the heavy gates overlap. Under the overlap policy it shares those cores with gate:conform's sweep. When the heavy gates queue (`queue`: the queue policy, or a pass that runs gate:make-test with no build lane, `gates_queue`), it also waits for gate:make-test and gate:conform, so only one heavy gate pool runs at a time."""
    if queue:
        _await_gate_futures(conform_fut, make_fut)
    run = contracts.prepare_run(ROOT, force)
    if run.skippable:
        emit.step_skipped("gate:engine-contracts", "input closure unchanged since its last green run")
        return CheckResult(
            check="engine-contracts",
            outcome="skipped",
            status="skipped (input closure unchanged since its last green run)",
            failures=[],
            failed_ids=[],
        )
    emit.note("gate:engine-contracts", run.selection.describe())
    contracts.start_run(run)
    try:
        result = spawn("gate:engine-contracts", argv, emit=emit, registry=registry, stream=False)
    except Exception:
        if record:
            contracts.finish_run(ROOT, run, False)
        raise
    gate = classify_engine_suite_output(result.stdout, result.returncode, "engine-contracts")
    if record:
        finished = contracts.finish_run(ROOT, run, gate.ok)
        if finished.status == "drifted":
            emit.note(
                "gate:engine-contracts",
                "gate:engine-contracts green, but its input closure changed while the cycle ran — green not recorded",
            )
        elif finished.status == "unavailable":
            emit.note(
                "gate:engine-contracts", "closure fingerprint unavailable without git — green not recorded"
            )
    _close_gate(emit, "gate:engine-contracts", result, gate)
    return gate


def _gate_result(fut: Future, name: str, failures: list[str]):
    try:
        return fut.result()
    except Exception as exc:
        failures.append(f"{name} raised: {exc!r}")
        return None


def _rc_result(check: str, returncode: int, failure: str) -> CheckResult:
    """Return the result for a gate evaluated by its exit code alone, with the summary's status strings. It names no failed ids because neither suite's output is parsed."""
    return CheckResult(
        check=check,
        outcome="green" if returncode == 0 else "red",
        status="green" if returncode == 0 else f"FAILED (exit {returncode})",
        failures=[] if returncode == 0 else [failure],
        failed_ids=[],
    )


def _join_contracts(
    report: CycleReport,
    failures: list[str],
    fut: Future,
    emit: console.CycleConsole,
    timings: CycleTimings | None = None,
) -> None:
    """Record the contracts outcome and its timings check. A task that raised records no check line, because the exception is a failure of the thread pool, not a suite result."""
    gate = _gate_result(fut, "gate:engine-contracts", failures)
    if gate is None:
        status, green = "FAILED (exception)", False
    else:
        status, green = gate.status, None if gate.outcome == "skipped" else gate.ok
        report.contracts_proven = gate.outcome == "skipped"
        for test_id in gate.failed_ids:
            emit.note("gate:engine-contracts", f"hard engine-suite failure (contracts): {test_id}")
        failures.extend(gate.failures)
        if timings is not None:
            timings.record_check(gate)
    report.gate_contracts, report.gate_contracts_green = status, green


def _join_gates(
    report: CycleReport,
    failures: list[str],
    js_fut: Future | None,
    contracts_fut: Future | None,
    conform_fut: Future | None,
    make_fut: Future | None,
    emit: console.CycleConsole,
    timings: CycleTimings | None = None,
) -> None:
    """Record every gate that ran in the report, and each gate's check result in the timings journal. The JS suite and `make test` are evaluated by exit code alone, so `_rc_result` builds their results here. gate:make-test's wrapper records no check line when CYCLE_RUN_ENV is set, so the line recorded here is the only one for it."""
    if js_fut is not None:
        js = _gate_result(js_fut, "gate:js", failures)
        if js is None:
            report.gate_js = "FAILED (exception)"
            report.gate_js_green = False
        else:
            gate = _rc_result("js", js.returncode, "JS suite failed")
            report.gate_js_green = gate.ok
            report.gate_js = gate.status
            failures.extend(gate.failures)
            if timings is not None:
                timings.record_check(gate)
    if contracts_fut is not None:
        _join_contracts(report, failures, contracts_fut, emit, timings)
    if conform_fut is not None:
        conform = _gate_result(conform_fut, "gate:conform", failures)
        if conform is None:
            report.gate_conform = "FAILED (exception)"
            report.gate_conform_green = False
        else:
            report.gate_conform = conform.status
            report.gate_conform_green = not conform.failures
            failures.extend(conform.failures)
            if timings is not None:
                timings.record_check(conform)
    if make_fut is not None:
        make = _gate_result(make_fut, "gate:make-test", failures)
        if make is None:
            report.gate_make_test = "FAILED (exception)"
            report.gate_make_test_green = False
        else:
            gate = _rc_result("make-test", make.returncode, "make test failed")
            report.gate_make_test_green = gate.ok
            report.gate_make_test = (
                MAKE_TEST_SELF_SKIP_STATUS if gate.ok and make_test_self_skipped(make.stdout) else gate.status
            )
            failures.extend(gate.failures)
            if timings is not None:
                timings.record_check(gate)


def _record_conform_green(
    report: CycleReport, plan: Plan, key: str | None, emit: console.CycleConsole
) -> None:
    """Publish the joined conformance result against the key captured after run_build. A passing sweep records only while its key still matches, and a failure clears only a record for that key. The contracts task finalizes its own input snapshot through the shared contracts lifecycle."""
    if key and report.gate_conform_green is not None:
        settled = settle_green(
            cycle_paths.CONFORM_GREEN,
            key,
            report.gate_conform_green,
            lambda: conform_skip_fingerprint(ROOT, plan.conform_max_length),
            files_of=lambda: conform_skip_files(ROOT, plan.conform_max_length),
        )
        if report.gate_conform_green and not settled:
            emit.note(
                "gate:conform",
                "gate:conform green, but its inputs changed while the cycle ran — green not recorded",
            )


def _timed_spawn(spawn, report: CycleReport):
    """Wrap `spawn` so each child's wall-clock time, exit status and write-meter window (`written_bytes`, which `CycleTimings.wrap_spawn` sets, and whether another step's window overlapped it) are recorded on the report under its plan step, for the summary table's time, outcome and written columns. The name goes through STEP_ALIASES, so `run_build --gates-only` fills the run_build row. It is a wrapper, not part of `_run_step`, because the tests drive the cycle with their own spawn callables and the table must be filled for them too."""

    def recorded(name: str, argv, *, emit, registry, stream, **passthrough):
        result = spawn(name, argv, emit=emit, registry=registry, stream=stream, **passthrough)
        step = STEP_ALIASES.get(name, name)
        report.step_seconds[step] = result.elapsed
        report.step_returncodes[step] = result.returncode
        written = getattr(result, "written_bytes", None)
        if written is not None:
            report.step_written[step] = (written, bool(getattr(result, "written_overlap", ())))
        return result

    return recorded


def _run_cycle(
    plan: Plan,
    report: CycleReport,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    spawn=_run_step,
    timings: CycleTimings | None = None,
) -> int:
    if timings is not None:
        spawn = timings.wrap_spawn(spawn)
    spawn = _timed_spawn(spawn, report)
    pool = ThreadPoolExecutor(max_workers=_GATE_POOL_WORKERS)
    failures: list[str] = []
    served_before: str | None = None
    try:
        js_fut = (
            None
            if plan.skip_gates
            else pool.submit(_gate_js_task, plan.argv("gate:js"), spawn, emit, registry)
        )
        make_fut: Future | None = None
        contracts_fut: Future | None = None
        conform_fut: Future | None = None
        conform_key: str | None = None
        if plan.skip_gates:
            emit.step_skipped("gates", "--skip-gates")
        if not plan.skip_gates and plan.skip_conform:
            report.gate_conform = f"skipped ({plan.conform_note or '--skip-conform'})"
            emit.step_skipped("gate:conform", plan.conform_note or "--skip-conform")
        if not plan.skip_gates and plan.skip_contracts:
            report.gate_contracts = f"skipped ({plan.contracts_note})"
            emit.step_skipped("gate:engine-contracts", plan.contracts_note)
        if not plan.skip_gates and plan.skip_make_test:
            report.gate_make_test = f"skipped ({plan.make_test_note})"
            emit.step_skipped("gate:make-test", plan.make_test_note)

        gate = _do_run_build(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            argv=None if plan.skip_run_build else plan.argv("build"),
            skip=plan.skip_run_build,
            skip_note=plan.run_build_note,
            gates_only=plan.rerun_gates_only,
            kern_only=plan.rerun_kern_only,
            record=plan.record_greens,
            fingerprint=plan.run_build_fingerprint,
            timings=timings,
        )
        if gate is None or not gate.ok:
            failures.extend(_run_build_reasons(gate))
            report.run_build_failed = True
            if plan.skip_gates or not plan.skip_contracts:
                report.gate_contracts = "not run (build gate failed)"
                emit.step_not_run("gate:engine-contracts", "build gate failed")
            if not plan.skip_gates and not plan.skip_conform:
                report.gate_conform = "not run (build gate failed)"
                emit.step_not_run("gate:conform", "build gate failed")
            if not plan.skip_gates and not plan.skip_make_test:
                report.gate_make_test = "not run (build gate failed)"
                emit.step_not_run("gate:make-test", "build gate failed")
            _join_gates(report, failures, js_fut, None, None, None, emit, timings)
            return _finish(report, failures, plan, timings, emit)

        # The font suite checks the data-expect pins against the built Senior, which its session copies from the build run_build has just left, so it starts here and not at t=0.
        if not (plan.skip_gates or plan.skip_make_test):
            make_fut = pool.submit(
                _gate_make_test_task,
                plan.argv("gate:make-test"),
                _spawn_with_env(spawn, {"PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.make_test_workers)}),
                emit,
                registry,
            )

        if not plan.skip_gates and not plan.skip_conform:
            current_conform_key = conform_skip_fingerprint(ROOT, plan.conform_max_length)
            green = None if plan.fresh else read_green_record(cycle_paths.CONFORM_GREEN)
            if green is not None and green["fingerprint"] == current_conform_key:
                report.conform_proven = True
                report.gate_conform = f"skipped ({CONFORM_SKIP_NOTE})"
                emit.step_skipped(
                    "gate:conform",
                    f"SKIPPED after run_build — {CONFORM_SKIP_NOTE}. The lookup this pass emits asks HarfBuzz for no shape its last green sweep did not shape, and run_build's string replay has held the tables to the engine over every swept text.",
                )
            else:
                if plan.record_greens:
                    conform_key = current_conform_key
                conform_fut = pool.submit(
                    _gate_conform_task,
                    plan.gates_queue,
                    make_fut,
                    spawn,
                    emit,
                    registry,
                    plan.argv("gate:conform"),
                )

        if not plan.skip_gates and (not plan.skip_contracts or plan.contracts_run is not None):
            contracts_fut = pool.submit(
                _gate_contracts_task,
                plan.gates_queue,
                conform_fut,
                make_fut,
                _spawn_with_env(
                    spawn,
                    {
                        "AMS_POOL_UNIT": "engine-contracts",
                        "PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.contracts_workers),
                    },
                ),
                emit,
                registry,
                contracts_argv() if plan.skip_contracts else plan.argv("gate:engine-contracts"),
                plan.fresh,
                plan.record_greens,
            )

        def stop_here(failure: str) -> int:
            failures.append(failure)
            _join_gates(report, failures, js_fut, contracts_fut, conform_fut, make_fut, emit, timings)
            _record_conform_green(report, plan, conform_key, emit)
            return _finish(report, failures, plan, timings, emit)

        served_before = _served_generated_at() if plan.review_out is None else None
        if plan.runs("corpus-build") and plan.next_corpus is not None and plan.promote_corpus is None:
            # The table-diff build starts from an empty directory, so a `review.next` an earlier pass left (`recover_next_corpus`) that this pass does not promote goes before it.
            shutil.rmtree(plan.next_corpus, ignore_errors=True)

        if plan.runs("assets-refresh") and not _do_assets_refresh(
            report, spawn=spawn, emit=emit, registry=registry, plan=plan
        ):
            return stop_here("assets refresh failed")

        if not _do_corpus_build(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            review_out=plan.update_corpus,
            argv=None if plan.skip_corpus else plan.argv("corpus-build"),
            skip=plan.skip_corpus,
            skip_note=plan.corpus_note,
        ):
            return stop_here("corpus rebuild failed")

        landed: dict | None = None
        if plan.runs("land"):
            landed = _do_land(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
            if landed is None:
                failures.append("land failed")
        if plan.review_out is None and plan.broadcast and _served_generated_at() != served_before:
            _broadcast_review_reload(emit, "land" if plan.runs("land") else "corpus-build", "corpus")
            served_before = _served_generated_at()
        _delete_discard(landed)

        _join_gates(report, failures, js_fut, contracts_fut, conform_fut, make_fut, emit, timings)
        _record_conform_green(report, plan, conform_key, emit)
        _do_job_costs(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        return _finish(report, failures, plan, timings, emit)
    except KeyboardInterrupt as stop:
        if registry.waits_for_uninterruptible():
            emit.note(
                "land",
                "waiting for the land to finish: it moves the corpus and the verdict store together and is never stopped halfway",
            )
        registry.terminate_all()
        pool.shutdown(wait=False, cancel_futures=True)
        _settle_interrupted_land(report, plan, emit, served_before)
        report.interrupted = True
        signum = stop.signum if isinstance(stop, CycleStopped) else signal.SIGINT
        return _finish_interrupted(
            report, failures, registry.killed_count, plan, timings, emit, signum=signum
        )
    finally:
        pool.shutdown(wait=True)


def _deep_sweep_report(root: Path = ROOT) -> tuple[str, str]:
    """Return `deep_sweep_status`, or `unknown` with the error when its record cannot be read. The deep sweep runs outside the cycle, which only reports it, so a read error must not fail the pass."""
    try:
        return deep_sweep_status(root)
    except Exception as exc:
        return "unknown", f"could not be read ({exc!r})"


def _deep_replay_report(root: Path | None = None) -> tuple[str, str]:
    """Return `deep_replay_status`, or `unknown` with the error, as `_deep_sweep_report` does."""
    try:
        return deep_replay_status(root)
    except Exception as exc:
        return "unknown", f"could not be read ({exc!r})"


INFORMATIONAL_STEPS = ("job-costs",)

_GATE_STATUS_FIELDS = {
    "gate:js": "gate_js",
    "gate:engine-contracts": "gate_contracts",
    "gate:conform": "gate_conform",
    "gate:make-test": "gate_make_test",
}


def step_detail(report: CycleReport, name: str) -> str:
    """Return one step's detail for the summary table and the step's closing line, or the empty string when the step has nothing to report.

    `summary_rows` drops the detail on a `skipped` or `not run` row, because the report can still hold the previous build's numbers for a step this pass did not run. A gate's detail is the status string its evaluator wrote (the report field `_GATE_STATUS_FIELDS` names), with a plain "green" dropped because the outcome column already says it.
    """

    def count(value: int | None) -> str:
        return "" if value is None else console.fmt_count(value)

    def prose(value: str) -> str:
        return "" if value.startswith(("skipped", "not run")) else value

    if name == "build":
        if report.pins_pass is None:
            return ""
        return "pins pass" if report.pins_pass else "PINS FAILED"
    if name == "corpus-build":
        parts = []
        if report.corpus_units is not None:
            parts.append(f"{count(report.corpus_units)} units")
        if report.corpus_rows is not None:
            parts.append(f"{count(report.corpus_rows)} rows")
        return ", ".join(parts)
    if name == "assets-refresh":
        return prose(report.assets_status)
    if name == "land":
        return prose(report.land_status)
    if name == "job-costs":
        return prose(report.job_costs_status)
    if name == "retention":
        return report.retention_detail
    status = _GATE_STATUS_FIELDS.get(name)
    if status is not None:
        shown = prose(str(getattr(report, status)))
        return "" if shown == "green" else shown
    return ""


def _detail_beside(outcome: str, detail: str) -> str:
    """Return `detail` with the leading `outcome` word removed, so `FAILED (exit 1)` beside the outcome `FAILED` becomes `exit 1`. Returns the empty string when the detail only repeats the outcome."""
    if not detail or detail == outcome:
        return ""
    if detail.startswith(outcome):
        rest = detail[len(outcome) :].strip()
        return rest[1:-1].strip() if rest.startswith("(") and rest.endswith(")") else rest
    return detail


def _close_step(
    emit: console.CycleConsole,
    report: CycleReport,
    name: str,
    result: _StepResult | None,
    outcome: str | None = None,
) -> None:
    """Print a spawned step's closing line with its detail. `outcome` defaults to one derived from the child's exit status; callers that decide it otherwise pass their own (run_build decides from its summaries, and job-costs always passes `ok` because it gates nothing)."""
    if outcome is None:
        outcome = "ok" if result is None or result.returncode == 0 else f"FAILED (exit {result.returncode})"
    detail = step_detail(report, STEP_ALIASES.get(name, name))
    emit.step_end(name, result, outcome, _detail_beside(outcome, detail))


def _close_gate(
    emit: console.CycleConsole, name: str, result: _StepResult, gate: CheckResult | None = None
) -> None:
    """Print a gate's closing line from the result its task just reached, or from the exit status when there is no result. `_close_step` would read the report, which `_join_gates` fills in only later, so at this point it still says the gate has not run. A plain green status is dropped from the detail, as in the table."""
    if gate is None:
        status = "green" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
        passed = result.returncode == 0
    else:
        status, passed = gate.status, gate.ok
    outcome = "ok" if passed else "FAILED"
    emit.step_end(name, result, outcome, _detail_beside(outcome, "" if status == "green" else status))


def _step_outcome(report: CycleReport, plan: Plan, step: Step, *, retention_ran: bool) -> str:
    """Return the table's outcome for one step: `ok`, `FAILED`, `skipped`, or `not run`. The detail column carries anything more, and the plan block says why a step did not run.

    A gate's outcome comes from its status string. Any other step that ran takes its outcome from run_build's failure flag or the child's exit code, since having a recorded time only shows that it ran. A nonzero exit from job-costs (`INFORMATIONAL_STEPS`) is not a failure, because it gates nothing and its detail reports the problem.

    Retention runs inside `_finish`, so it reads `not run` when an upstream failure ended the pass before retention, or a stop signal ended it before retention finished. It reads `FAILED` when retention raised, which `_finish` records in `retention_outcome`; the pass verdict stays green. A stop signal that lands after retention finished leaves the row reading as retention ended (`ok` or `FAILED`), because `_finish_interrupted` reads the same field. It reads `skipped` only when the plan ruled it out (`--keep-history`, a first run, or a staging pass).
    """
    status = _GATE_STATUS_FIELDS.get(step.name)
    if status is not None:
        prose = str(getattr(report, status))
        if prose.startswith("not run"):
            return "not run"
        if prose.startswith("skipped"):
            return "skipped"
        if prose.startswith("self-skipped"):
            return "ok"
        return "ok" if prose == "green" or prose.startswith("green ") else "FAILED"
    if step.name == "retention":
        if retention_ran:
            return "ok"
        if report.retention_outcome == "FAILED":
            return "FAILED"
        return "skipped" if step.skipped else "not run"
    if step.skipped:
        return "skipped"
    if step.name == "build" and report.run_build_failed:
        return "FAILED"
    returncode = report.step_returncodes.get(step.name)
    if returncode and step.name not in INFORMATIONAL_STEPS:
        return "FAILED"
    return "ok" if step.name in report.step_seconds else "not run"


def summary_rows(report: CycleReport, plan: Plan, *, retention_ran: bool) -> list[console.SummaryRow]:
    """Return one summary-table row per planned step. A step that did not run gets no detail, because the report can still hold the previous build's counts for it (a skipped run_build's pin outcome, a skipped corpus build's totals)."""
    rows: list[console.SummaryRow] = []
    for step in plan.steps:
        outcome = _step_outcome(report, plan, step, retention_ran=retention_ran)
        ran = outcome not in ("skipped", "not run")
        written, overlapped = report.step_written.get(step.name, (None, False))
        rows.append(
            console.SummaryRow(
                number=None,
                name=step.name,
                outcome=outcome,
                detail=_detail_beside(outcome, step_detail(report, step.name)) if ran else "",
                seconds=report.step_seconds.get(step.name),
                written=written,
                overlapped=overlapped,
            )
        )
    return rows


def summary_cycle_lines(
    report: CycleReport, plan: Plan, retention_lines: list[str], diff_line: str | None = None
) -> list[str]:
    """Return the summary lines below the table: the table-diff corpus's unaccepted changes (`diff_line`, `verdict_ready.diff_summary_line`, which a green pass's `readiness_block` gives), the job-costs status, what the pass wrote, the deep sweep and deep replay status, the output paths, and what retention pruned."""
    deep_status, deep_note = _deep_sweep_report()
    replay_status, replay_note = _deep_replay_report()
    lines = []
    if diff_line is not None:
        lines.append(f"  review diff      : {diff_line.removeprefix('review diff: ')}")
    lines += [
        f"  job costs        : {report.job_costs_status}",
        *written_lines(report.written),
        f"  deep sweep       : {deep_status} ({deep_note})",
        f"  deep replay      : {replay_status} ({replay_note})",
        "  run_build summaries :",
        *(f"      {path}" for path in cycle_paths.BUILD_SUMMARY_FILES.values()),
        f"      {cycle_paths.CONFORM_SUMMARY}",
    ]
    if plan.log_dir is not None:
        lines.append(f"  logs             : {plan.log_dir}")
    if retention_lines:
        lines.extend(["", *retention_lines])
    return lines


def written_lines(writes: PassWrites | None) -> list[str]:
    """Return the summary's lines on what the pass wrote, nothing for a pass without a meter (`timings` None, as in the driver's tests). One line gives the write meter's figure and its basis, the review server's own writes, the device's and swap's machine-wide counts, and the pass's write class with its ceiling on this host, which an unclassified pass never has. A `rewritten whole: <output>` line follows for each output held as a base and segments that the pass rewrote whole outside a fold, the share rule or an alphabet change (the `outputs` block's `rewritten`, `cycle_timings.sum_output_counts`), a finding to explain, and a last line when the pass wrote more than its class's ceiling. A pass that lost its first or last reading says so instead."""
    if writes is None:
        return []
    block = writes.block
    if block is None:
        return [
            "  written          : not measured (the write meter's first or last reading failed; see the [warn] line)"
        ]
    total = console.fmt_bytes(block.get("bytes")) or "unread"
    basis = block.get("basis")
    if basis == "own":
        parts = [f"{total} (the pass's own coalition)"]
    elif basis == "shared":
        others = console.fmt_bytes(block.get("outsiders_bytes")) or "0 B"
        parts = [
            f"{total} at most (the shared coalition, less the {others} other processes wrote, with {block.get('outsiders_unread')} processes' writes unread)"
        ]
    else:
        parts = [f"{total} (the device's machine-wide count, since the coalition read failed)"]
    server = block.get("review_server_bytes")
    if server is not None:
        parts.append(f"review server {console.fmt_bytes(server)}")
    device, swap = block.get("device_bytes"), block.get("swap_bytes")
    if device is not None or swap is not None:
        parts.append(
            f"machine-wide, the device took {console.fmt_bytes(device) or 'unread'} and swap {console.fmt_bytes(swap) or 'unread'}"
        )
    named = writes.write_class or UNCLASSIFIED
    if writes.ceiling is not None:
        ceiling = f"ceiling {console.fmt_bytes(writes.ceiling)}"
    else:
        ceiling = "no ceiling" if writes.write_class is None else "no ceiling yet"
    parts.append(f"class {named}, {ceiling}")
    lines = [f"  written          : {'; '.join(parts)}"]
    outputs = block.get("outputs")
    for output in outputs.get("rewritten", ()) if isinstance(outputs, dict) else ():
        lines.append(f"  rewritten whole: {output}")
    if writes.over:
        lines.append(
            f"  write ceiling    : OVER, {total} against the {named} class's {console.fmt_bytes(writes.ceiling)} (make cycle-timings ARGS='--writes')"
        )
    return lines


def _as_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _gate_entry(status: str, green: bool | None, skip: str | None = None) -> dict:
    """Return a gate's cycle-summary entry. `green` is the result the gate recorded when it was joined, True only when it ran in this pass and passed; a gate that was never joined passes None and is written as False. `status` is prose for people and is not parsed for greenness.

    `skip` says why the gate did not run, and the readiness check in `engine/review/status.py` reads it: "proved" means a matching green record already showed this content passing, and "forced" means a flag suppressed the gate. Both kinds of status string start with "skipped", so the status alone cannot tell them apart.
    """
    return {"status": status, "green": green is True, "skip": skip}


def _skip_kind(*, proved: bool, forced: bool = False) -> str | None:
    """Return "proved", "forced", or None. "proved" wins when both are set, which happens when `main` auto-skips gate:conform on a green record: the plan then has `skip_conform` and `conform_proven` both True."""
    if proved:
        return "proved"
    if forced:
        return "forced"
    return None


def _corpus_block(corpus_dir: Path) -> dict:
    block: dict = {"dir": str(corpus_dir), "generated_at": None, "inputs_fingerprint": None}
    try:
        manifest = json.loads((corpus_dir / "manifest.json").read_text())
        block["generated_at"] = manifest.get("generated_at")
        block["inputs_fingerprint"] = manifest.get("inputs_fingerprint")
    except Exception:
        pass
    return block


def cycle_summary_payload(report: CycleReport, failures: list[str], plan: Plan, exit_kind: str) -> dict:
    deep_status, deep_note = _deep_sweep_report()
    replay_status, replay_note = _deep_replay_report()
    return {
        "format": "ams-cycle-summary/1",
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "exit": exit_kind,
        "failures": list(failures),
        "gates": {
            "js": _gate_entry(report.gate_js, report.gate_js_green),
            "engine_contracts": _gate_entry(
                report.gate_contracts,
                report.gate_contracts_green,
                _skip_kind(
                    proved=report.contracts_proven
                    or (plan.skip_contracts and report.gate_contracts_green is None)
                ),
            ),
            "conform": _gate_entry(
                report.gate_conform,
                report.gate_conform_green,
                _skip_kind(proved=plan.conform_proven or report.conform_proven, forced=plan.skip_conform),
            ),
            "make_test": _gate_entry(
                report.gate_make_test,
                report.gate_make_test_green,
                _skip_kind(proved=plan.skip_make_test),
            ),
        },
        "deep_sweep": {"status": deep_status, "note": deep_note},
        "deep_replay": {"status": replay_status, "note": replay_note},
        "make_test_fingerprint": (
            plan.make_test_fingerprint if report.gate_make_test_green is True or plan.skip_make_test else None
        ),
        "pins_pass": report.pins_pass,
        "corpus_units": report.corpus_units,
        "corpus_rows": report.corpus_rows,
        "corpus_batches": report.corpus_batches,
        "assets_status": report.assets_status,
        "land_status": report.land_status,
        "land": report.land,
        "job_costs_status": report.job_costs_status,
        "job_costs_ok": report.job_costs_ok,
        "log_dir": _as_str(plan.log_dir),
        "interrupted": report.interrupted,
        "plan": {
            "conform_max_length": plan.conform_max_length,
            "kernel_threads": None if _no_fanout(plan) else plan.kernel_threads,
            "scratch_beside_default": None if _no_fanout(plan) else plan.scratch_beside_default,
            "replay_threads": None if _no_fanout(plan) else plan.replay_threads,
            "make_test_workers": plan.make_test_workers if plan.runs("gate:make-test") else None,
            "contracts_workers": (
                plan.contracts_workers
                if report.gate_contracts_green is not None
                or (plan.runs("gate:engine-contracts") and not report.contracts_proven)
                else None
            ),
            "conform_jobs": (
                plan.conform_jobs if plan.runs("gate:conform") and not report.conform_proven else None
            ),
            "pool_policy": plan.pool_policy,
            "skip_gates": plan.skip_gates,
            "skip_conform": plan.skip_conform,
            "skip_run_build": plan.skip_run_build,
            "rerun_gates_only": plan.rerun_gates_only,
            "rerun_kern_only": plan.rerun_kern_only,
            "skip_corpus": plan.skip_corpus,
            "refresh_assets": plan.refresh_assets,
            "promote_corpus": _as_str(plan.promote_corpus),
            "next_corpus": _as_str(plan.next_corpus),
            "land": plan.land,
            "skip_contracts": report.contracts_proven
            or (plan.skip_contracts and report.gate_contracts_green is None),
            "review_out": _as_str(plan.review_out),
            "first_run": plan.first_run,
            "short_id": plan.short_id,
        },
        "argv": list(sys.argv),
        "corpus": _corpus_block(plan.corpus_dir),
    }


def write_cycle_summary(payload: dict) -> None:
    target = cycle_paths.CYCLE_SUMMARY
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
        print(f"warning: failed to write {cycle_paths.CYCLE_SUMMARY}: {exc!r}", file=sys.stderr)
    if timings is not None:
        report.written = timings.finish(payload)


def _preflight(
    args: argparse.Namespace, *, can_keep_running: bool = False, probe: ServerProbe | None = None
) -> bool:
    """Decide what happens to a listening review server before the pass, and return whether the pass may run. A staging pass writes nothing the server reads and skips the check. A server from this checkout that speaks the land protocol with the working tree's code keeps running through every pass, and one running other code is restarted on the tree's code first (`restart_review_server`) when the pass writes under it, and otherwise keeps running. A server serving another checkout is left alone. A listener that gave no /capabilities answer (`unanswered`) is never stopped: a pass that writes nothing under it runs beside it, and any other refuses, since it cannot tell a stalled current server from anything else. `can_keep_running` (`server_can_keep_running`) says whether the pass writes under the server. A server from before the land protocol keeps running only when it is set; otherwise `--stop-server` stops it once, `--yes` runs beside it, and a bare run refuses. `probe` is `probe_server`'s answer when the caller already has it."""
    if args.review_out is not None:
        print(
            f"Staging pass: corpus writes redirected to {args.review_out}; the live corpus at engine/out/review is never written."
        )
        return True
    if probe is None:
        probe = probe_server()
    if probe.kind == "none":
        return True
    if probe.kind == "current":
        print(
            "The review server keeps running: it lands this pass's corpus and verdict store with it, and the open tabs move onto them by themselves."
        )
        return True
    if probe.kind == "foreign":
        print(
            f"A review server for another checkout ({probe.root}) is listening on 127.0.0.1:{REVIEW_PORT}; this pass leaves it alone and sends its tabs nothing."
        )
        return True
    if probe.kind == "unanswered":
        if can_keep_running:
            print(
                f"The review server keeps running: it gave no /capabilities answer, and this pass {SERVER_KEEPS_RUNNING_NOTE}."
            )
            return True
        print("=" * 68)
        print(
            f"REFUSING TO RUN: something listens on 127.0.0.1:{REVIEW_PORT} but gave no /capabilities answer "
            f"in {CAPABILITIES_ATTEMPTS} tries, so this pass cannot tell whether it may keep running and "
            "stops nothing."
        )
        print(
            "Re-run once it answers: a review server busy with a long request answers again within seconds."
        )
        print("=" * 68)
        return False
    if probe.kind == "stale":
        if can_keep_running:
            print(
                f"The review server runs older code than this checkout and keeps running: this pass {SERVER_KEEPS_RUNNING_NOTE}. The next pass that writes under it restarts it on this tree's code."
            )
            return True
        print("The review server runs older code than this checkout; restarting it on this tree's code.")
        if restart_review_server():
            print("The review server is back on this tree's code and keeps running through the pass.")
            return True
        print("=" * 68)
        print(
            f"REFUSING TO RUN: the review server on 127.0.0.1:{REVIEW_PORT} did not come back on this "
            f"tree's code within {SERVER_START_TIMEOUT:.0f}s (log: tmp/review-serve.log)."
        )
        print("Start it by hand (make review-serve) and re-run.")
        print("=" * 68)
        return False
    if can_keep_running:
        print(f"The review server keeps running: this pass {SERVER_KEEPS_RUNNING_NOTE}.")
        return True
    if args.stop_server:
        print(
            "Stopping the review server: it predates the land protocol, and this pass writes the corpus or the verdict store under it. A server started from this checkout keeps running through every pass, so this is the last stop a pass needs."
        )
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
        print("WARNING: a review server from before the land protocol is listening on")
        print("127.0.0.1:7294. Proceeding with --yes. This pass swaps the corpus and the")
        print("verdict store under the open review tabs. AFTER this cycle:")
        print("  1. restart the review server:  uv run python -m engine.review.serve")
        print("  2. open tabs move onto the new corpus by themselves on their next")
        print("     save or status check.")
        print("=" * 68)
        return True
    print("=" * 68)
    print("REFUSING TO RUN: a review server from before the land protocol is listening")
    print("on 127.0.0.1:7294, and this pass swaps the corpus and the verdict store")
    print("under it. Restart it once on this checkout's code and it keeps running")
    print("through every later pass. Before re-running:")
    print(r"  1. stop the review server:  pkill -f 'engine\.review\.serve'")
    print("     (or pass --stop-server and let this command stop it for you)")
    print("  2. re-run this command (or pass --yes to override at your own risk)")
    print("  (or pass --review-out <dir> for a staging pass that never touches the live corpus)")
    print("=" * 68)
    return False


def prune_build_logs(root: Path, keep: int) -> list[Path]:
    """Delete every run directory under `root` except the newest `keep`, and return the deleted paths. Run directories are named `<UTC stamp>-<short sha>`, so a lexical sort is chronological and mtime is not read. The `latest` symlink is skipped; it points at the newest run, which is kept."""
    if not root.is_dir():
        return []
    runs = sorted(
        path for path in root.iterdir() if path.is_dir() and not path.is_symlink() and path.name[:1].isdigit()
    )
    doomed = runs if keep <= 0 else runs[: max(0, len(runs) - keep)]
    for path in doomed:
        shutil.rmtree(path, ignore_errors=True)
    return doomed


@dataclass(frozen=True)
class RetentionResult:
    """A retention pass's result: `lines` for the summary block, and `detail` for the retention row and closing line."""

    lines: list[str]
    detail: str


def run_retention(plan: Plan) -> RetentionResult:
    """Prune the build logs after a green pass, keeping the newest `cycle_paths.BUILD_LOGS_KEEP` runs, and return the summary lines and detail. It returns the lines instead of printing them so they appear in the summary block below the table."""
    try:
        shown = str(cycle_paths.BUILD_LOGS_ROOT.relative_to(ROOT))
    except ValueError:
        shown = str(cycle_paths.BUILD_LOGS_ROOT)
    dropped = prune_build_logs(cycle_paths.BUILD_LOGS_ROOT, cycle_paths.BUILD_LOGS_KEEP)
    count = console.fmt_count(len(dropped))
    lines = [
        "Retention (skip with --keep-history):",
        f"  build logs: removed {count}; kept the last {cycle_paths.BUILD_LOGS_KEEP} runs under {shown}",
    ]
    return RetentionResult(lines, f"removed {count} build log{'' if len(dropped) == 1 else 's'}")


def pass_lock_path() -> Path:
    return cycle_paths.CYCLE_VAR / "pass.lock"


_held_pass_lock: list[int] = []


@contextlib.contextmanager
def pass_lock(*, blocking: bool = True) -> Iterator[None]:
    """Hold the pass lock, an exclusive `flock` on `pass_lock_path()` whose file holds the holder's pid, for a whole pass, from before the superseded-corpus recovery and the plan to the pass's end. A second pass prints the holder's pid once and waits; Ctrl-C stops the wait. With `blocking` false it raises `store_lock.LockBusy` instead of waiting. The land inherits the lock's descriptor (`_run_step`), and a `flock` belongs to the open file description, so the lock stays held until both the driver and the land have exited: a driver killed while its land still runs leaves the next pass waiting for the land. The kernel releases the lock when the last holder exits, `kill -9` included.

    A staging pass holds it too: its recovery can rename a superseded corpus back into place, and a live pass's promotion reads the staged corpus it writes, so the two must not overlap. A dry run holds it without waiting (`main`), because its recovery can also rename a tree back; when a pass holds it, the dry run skips the recovery.
    """

    def waiting(fd: int) -> None:
        holder = os.pread(fd, 32, 0).decode("utf-8", "replace").strip() or "of unknown pid"
        print(
            f"waiting for pass {holder} to finish ({pass_lock_path()} is held; Ctrl-C stops waiting)",
            flush=True,
        )

    with store_lock.hold_flock(pass_lock_path(), blocking=blocking, on_wait=waiting) as fd:
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        _held_pass_lock.append(fd)
        try:
            yield
        finally:
            _held_pass_lock.remove(fd)


def sweep_run_dirs() -> list[Path]:
    """Delete the per-run scratch directories and the counts files (`cycle_timings.CycleTimings.open_output_counts`) under `cycle_paths.CYCLE_VAR` that earlier passes left, and return them. Each pass deletes its own when it ends, so only a pass killed outright leaves one. It runs under the pass lock and before this pass opens its counts file, so no running pass owns any of them, and after `recover_land`, so no unfinished land still needs one. A child that outlived the killed pass appends nothing once its counts file is gone, since `output_sink` never creates one."""
    root = cycle_paths.CYCLE_VAR
    if not root.is_dir():
        return []
    doomed = sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink())
    for path in doomed:
        shutil.rmtree(path, ignore_errors=True)
    counts = sorted(
        path for path in root.glob("output-counts-*.tsv") if path.is_file() and not path.is_symlink()
    )
    for path in counts:
        path.unlink(missing_ok=True)
    return doomed + counts


def main(argv: list[str] | None = None) -> int:
    from engine.pipeline import spec_load

    refusal = spec_load.small_alphabet_refusal("the artifact cycle")
    if refusal is not None:
        raise SystemExit(refusal)
    parser = argparse.ArgumentParser(
        description="Drive the commit-time artifact cycle: run_build, the table-diff corpus build and its land, gates."
    )
    parser.add_argument(
        "--skip-gates",
        action="store_true",
        help="skip the four post-build gates (JS suite, the engine suite, conformance sweep, make test)",
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
        "--fresh",
        action="store_true",
        help="run every stage and gate even when a green record shows its inputs unchanged since the last green run (disables all auto-skips, gate:make-test's included, and has run_build run the crate's table build rather than reuse the tables on disk, through its --fresh-tables)",
    )
    parser.add_argument(
        "--conform-max-length",
        "--conform-horizon",
        type=int,
        default=CONFORM_MAX_LENGTH_DEFAULT,
        help=f"exhaustive sweep length for gate:conform, passed through to run_build --conform-only (default {CONFORM_MAX_LENGTH_DEFAULT}, the per-edit sweep); going deeper here is `make conform-deep`'s job, which runs out of band and keys its own green on the emitted lookup's behavior classes",
    )
    parser.add_argument(
        "--pool-policy",
        choices=POOL_POLICIES,
        default=POOL_POLICY_DEFAULT,
        help="how the heavy gates share cores: 'overlap' (default; conform and the engine suite start once run_build's gate passes and run beside make-test and each other, and when make-test runs their widths also leave it its pool) or 'queue' (one pool at a time — make-test, then conform, then the engine suite, as a pass that runs make-test and neither run_build nor the corpus build does under either policy)",
    )
    parser.add_argument(
        "--review-out",
        type=Path,
        default=None,
        help="staging pass: redirect the corpus write to this dir and land nothing; the next live pass lands this dir in place of engine/out/review, instead of rebuilding, when it still reproduces the inputs byte for byte (promotable_corpus)",
    )
    parser.add_argument(
        "--keep-history",
        action="store_true",
        help="skip the green-finish retention pass (every build log stays on disk)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="run beside a listening review server from before the land protocol (one with no /capabilities) instead of refusing",
    )
    parser.add_argument(
        "--stop-server",
        action="store_true",
        help="stop a listening review server from before the land protocol (one with no /capabilities) instead of refusing, when this pass writes under it — the served corpus's units or stamp, or the verdict store it holds; a server from this checkout that answers /capabilities keeps running through every pass whether or not this is passed, and one running older code is restarted on the tree's code. `make review-cycle` passes this. It also says the recipe answers the server question after the pass, so the readiness checklist a green finish prints leaves the server row to it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved step plan and exit without executing anything",
    )
    args = parser.parse_args(argv)
    if args.fresh:
        args.force_make_test = True
    if args.dry_run:
        with contextlib.ExitStack() as held:
            try:
                held.enter_context(pass_lock(blocking=False))
            except store_lock.LockBusy:
                print(
                    f"A pass holds {pass_lock_path()}, so this dry run leaves any superseded corpus to it and plans against the corpus on disk now."
                )
                return _run_pass(args, recover=False)
            return _run_pass(args)
    with pass_lock():
        return _run_pass(args)


def _run_pass(args: argparse.Namespace, *, recover: bool = True) -> int:
    """Resolve the plan for the parsed arguments and run it, or print it on a dry run. `main` runs it under the pass lock (`pass_lock`), except a dry run that found the lock held, which passes `recover=False` and leaves what earlier passes left to the pass that holds it. Recovery runs first, in order: a `.superseded` tree (`recover_superseded_corpus`), then the run directories and counts files (`sweep_run_dirs`), then an unfinished `review.next` (`recover_next_corpus`)."""
    recovered = recover_superseded_corpus(delete=not args.dry_run) if recover else None
    if recover and not args.dry_run:
        sweep_run_dirs()
    kept_next = recover_next_corpus(delete=not args.dry_run) if recover else None
    first_run = not (REVIEW_OUT / "manifest.json").exists()

    skip_make_test = False
    make_test_note = ""
    make_test_fp: str | None = None

    run_build_fp = run_build_skip_fingerprint(ROOT)
    skip_run_build = False
    rerun_gates_only = False
    rerun_kern_only = False
    run_build_note = ""
    skip_corpus = False
    refresh_assets = False
    promote_from: Path | None = None
    corpus_note = ""
    skip_contracts = False
    contracts_note = ""
    conform_note = ""
    auto_skip_conform = False
    contracts_run = None
    if not args.skip_gates:
        contracts_run = contracts.prepare_run(ROOT, args.fresh)
        if contracts_run.skippable:
            skip_contracts = True
            contracts_note = "input closure unchanged since its last green run; --fresh overrides"
        else:
            contracts_note = contracts_run.selection.describe()
    if not args.fresh:
        green = read_green_record(cycle_paths.BUILD_GREEN)
        if (
            green is not None
            and green["fingerprint"] == run_build_fp
            and build_artifacts_present(ROOT)
            and stage_a_current(ROOT)
        ):
            skip_run_build = True
            run_build_note = "build inputs unchanged since the last green build; --fresh overrides"
        elif green is not None:
            current = run_build_skip_files(ROOT)
            rerunnable = gates_only_rerun(green, current)
            kerned = kern_only_rebuild(green, current)
            if rerunnable is not None and gates_only_base_present():
                rerun_gates_only = True
                run_build_note = (
                    f"only comparison-side inputs moved since the last green build ({capped_labels(rerunnable)}); "
                    "the tables and font are reused and the gates re-run over them; --fresh overrides"
                )
            elif kerned is not None and gates_only_base_present() and stage_a_current(ROOT):
                rerun_kern_only = True
                run_build_note = (
                    f"only kern blocks or font data moved since the last green build ({capped_labels(kerned)}); "
                    "the font is recompiled over the tables on disk, and every other gate's green stands; --fresh overrides"
                )
            else:
                note = moved_inputs_note(green, current)
                if note is not None:
                    run_build_note = f"inputs moved since its last green: {note}"
    # gate:make-test's key covers the built Senior (`make_test_font_lines`), which a full or kern-only build rewrites, so the plan can skip the gate only on a pass whose run_build writes no font; on any other the wrapper decides once run_build has finished.
    if not args.skip_gates:
        make_test_fp = make_test_closure_fingerprint(ROOT)
        if (skip_run_build or rerun_gates_only) and make_test_skippable(
            make_test_fp, prior_make_test_fingerprint(), force=args.force_make_test
        ):
            skip_make_test = True
            make_test_note = "closure unchanged since its last green run; --force-make-test overrides"

    # A kern-only rebuild rewrites Senior-Regular.otf and kern.tsv, which the table-diff corpus's after font and kerning units show, so only a pass whose run_build writes no font may skip the corpus build.
    if skip_run_build or (rerun_gates_only and stage_a_current(ROOT)):
        if args.review_out is None and not first_run:
            if corpus_build_skippable(ROOT):
                skip_corpus = True
                corpus_note = "the corpus already reflects these inputs byte for byte, stamp included; --fresh overrides"
            elif corpus_build_skippable(ROOT, ignore=shards.ASSET_COMPONENTS):
                skip_corpus = True
                refresh_assets = True
                corpus_note = ASSETS_REFRESH_NOTE
            else:
                promote_from = promotable_corpus(ROOT)
                if promote_from is not None:
                    skip_corpus = True
                    corpus_note = CORPUS_PROMOTE_NOTE
    if skip_run_build:
        if not args.skip_gates and not args.skip_conform:
            green = read_green_record(cycle_paths.CONFORM_GREEN)
            if green is not None and green["fingerprint"] == conform_skip_fingerprint(
                ROOT, args.conform_max_length
            ):
                auto_skip_conform = True
                conform_note = CONFORM_SKIP_NOTE

    preamble: list[str] = []

    def announce(text: str) -> None:
        """Print a line before the console exists, and queue it for `cycle_console.replay` so terminal.log also gets it."""
        preamble.append(text)
        print(text)

    for line in (recovered, kept_next):
        if line is not None:
            announce(line)

    short_id = resolve_short_id()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan = build_plan(
        skip_gates=args.skip_gates,
        first_run=first_run,
        short_id=short_id,
        skip_conform=args.skip_conform or auto_skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fp,
        force_make_test=args.force_make_test,
        conform_max_length=args.conform_max_length,
        pool_policy=args.pool_policy,
        review_out=args.review_out,
        skip_run_build=skip_run_build,
        rerun_gates_only=rerun_gates_only,
        rerun_kern_only=rerun_kern_only,
        run_build_note=run_build_note,
        run_build_fingerprint=run_build_fp,
        fresh=args.fresh,
        skip_corpus=skip_corpus,
        refresh_assets=refresh_assets,
        promote_corpus=promote_from,
        corpus_note=corpus_note,
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        contracts_run=contracts_run,
        conform_note=conform_note,
        conform_proven=auto_skip_conform,
        record_greens=not args.dry_run,
        keep_history=args.keep_history,
        recipe_serves=args.stop_server,
        scratch_dir=cycle_paths.CYCLE_VAR / f"{stamp}-{short_id}" if args.review_out is None else None,
    )

    if args.dry_run:
        print("\n".join(render_plan(plan)))
        return 0

    plan.stamp = stamp
    plan.log_dir = cycle_paths.BUILD_LOGS_ROOT / f"{plan.stamp}-{plan.short_id}"
    if plan.scratch_dir is not None:
        plan.scratch_dir.mkdir(parents=True, exist_ok=True)
    cycle_console = console.CycleConsole(
        steps=[step.name for step in plan.steps], log_dir=plan.log_dir, aliases=STEP_ALIASES
    )
    with cycle_console:
        cycle_console.replay(preamble)
        cycle_console.plan_block(render_plan(plan))
        probe = probe_server() if args.review_out is None else ServerProbe("none")
        plan.broadcast = probe.kind != "foreign"
        if not _preflight(
            args,
            can_keep_running=server_can_keep_running(
                skip_corpus=skip_corpus,
                writes_store=False,
                promotes_corpus=plan.promote_corpus is not None,
            ),
            probe=probe,
        ):
            return 2

        if first_run:
            print(
                "First-run mode: no existing corpus at engine/out/review — the corpus is built in place and lands no store."
            )

        report = CycleReport()
        from engine.tools import write_meter
        from engine.pipeline.alphabet import ALPHABET
        from engine.tools.cycle_timings import CycleTimings

        server_pid = review_server_pid() if probe.kind in ("current", "stale") else None
        timings = CycleTimings(
            CYCLE_TIMINGS,
            meter=functools.partial(write_meter.read, named=server_pid),
            alphabet=ALPHABET,
        )
        # run_build's CLI and the make_test_gate wrapper skip writing their own check line to the timings journal when this is set, because the cycle records it. It goes in the environment because the wrapper runs as a grandchild, under the `make test` recipe, where an argv flag would not reach it.
        os.environ[CYCLE_RUN_ENV] = timings.run_id
        os.environ[OUTPUT_COUNTS_ENV] = str(timings.open_output_counts(cycle_paths.CYCLE_VAR))

        registry = _ChildRegistry()
        try:
            with stop_signals():
                return _run_cycle(plan, report, cycle_console, registry, timings=timings)
        finally:
            timings.drop_output_counts()
            if plan.scratch_dir is not None:
                shutil.rmtree(plan.scratch_dir, ignore_errors=True)


def readiness_block(plan: Plan) -> tuple[list[str], str | None]:
    """Return the checklist `make verdict-ready` prints, so a green pass ends with it, and the summary's line counting the table-diff corpus's unaccepted changes (`verdict_ready.diff_summary_line`), None for another corpus. It reads the cycle summary, so it must run after `_emit_cycle_summary`. A staging pass returns neither, since its corpus is not the served one. When `--stop-server` is passed (as `make review-cycle` does), the server row is left out, because the recipe starts the server after the pass, or reports that it left it stopped. The diff is a row and a line, never a red gate: a pass whose corpus holds unaccepted changes ends green and READY. The engine suite switches this off with `cycle_paths.READINESS_ENABLED`, because it reads the live corpus."""
    if plan.review_out is not None:
        return [], None
    from engine.tools import verdict_ready

    try:
        result, ready = verdict_ready.readiness(
            with_server=not plan.recipe_serves,
            repo_root=ROOT,
            review_dir=plan.corpus_dir,
            build_out=cycle_paths.BUILD_OUT,
            autosave_path=AUTOSAVE,
            cycle_summary_path=cycle_paths.CYCLE_SUMMARY,
        )
    except Exception as exc:
        return [f"readiness: the checklist could not be computed ({exc!r})"], None
    return verdict_ready.checklist(result, ready), verdict_ready.diff_summary_line(result)


def _finish(
    report: CycleReport,
    failures: list[str],
    plan: Plan,
    timings: CycleTimings | None = None,
    emit: console.CycleConsole | None = None,
) -> int:
    """Finish the pass and return its exit status: run retention on a green pass, write the cycle summary, then print the summary block, with the table-diff corpus's diff line and ending with the readiness checklist on a green pass (`readiness_block`). Retention runs before the table is printed so its row has an outcome and detail. The engine suite sets `cycle_paths.RETENTION_ENABLED` False so a test that reaches a green finish does not prune the live repo; retention then counts as run with no lines. `cycle_paths.READINESS_ENABLED` switches the checklist off the same way."""
    cycle_console = console.CycleConsole() if emit is None else emit
    retention_lines: list[str] = []
    retention_ran = False
    if not failures and plan.retention and plan.record_greens:
        cycle_console.step_start("retention", None, plan.describe("retention"))
        started = time.perf_counter()
        try:
            pruned = run_retention(plan) if cycle_paths.RETENTION_ENABLED else RetentionResult([], "")
            retention_lines = list(pruned.lines)
            report.retention_detail = pruned.detail
            retention_ran = True
        except Exception as exc:
            retention_lines = [f"warning: retention pass failed: {exc!r}"]
        report.step_seconds["retention"] = time.perf_counter() - started
        report.retention_outcome = "ok" if retention_ran else "FAILED"
        cycle_console.step_end("retention", None, report.retention_outcome, report.retention_detail)
    _emit_cycle_summary(report, failures, plan, "failed" if failures else "ok", timings)
    readiness, diff_line = (
        ([], None) if failures or not cycle_paths.READINESS_ENABLED else readiness_block(plan)
    )
    cycle_console.summary(
        summary_rows(report, plan, retention_ran=retention_ran),
        summary_cycle_lines(report, plan, retention_lines, diff_line)
        + (["", *readiness] if readiness else []),
        console.VERDICT_FAILED if failures else console.VERDICT_OK,
        failures,
    )
    return 1 if failures else 0


def _finish_interrupted(
    report: CycleReport,
    failures: list[str],
    killed_count: int,
    plan: Plan,
    timings: CycleTimings | None = None,
    emit: console.CycleConsole | None = None,
    *,
    signum: int = signal.SIGINT,
) -> int:
    """Finish a pass that a stop signal ended, after its children were terminated. The cycle summary and timings journal record it as interrupted, the summary block names the signal and the number of children killed, and the exit status is 128 plus the signal number, as a shell reports it."""
    cycle_console = console.CycleConsole() if emit is None else emit
    _emit_cycle_summary(report, failures, plan, "interrupted", timings)
    cycle_console.summary(
        summary_rows(report, plan, retention_ran=report.retention_outcome == "ok"),
        summary_cycle_lines(report, plan, []),
        console.VERDICT_INTERRUPTED,
        [*failures, f"{signal.Signals(signum).name}: terminated {killed_count} child process(es)"],
    )
    return 128 + signum


if __name__ == "__main__":
    sys.exit(main())
