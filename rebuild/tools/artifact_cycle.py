"""Run the commit-time artifact cycle in one command.

The cycle recompiles M1.otf and checks it, rebuilds the review corpus beside the served one, runs the verdict update over it into a scratch copy of the verdict store, lands the corpus and the store together, and refreshes the review-facts pins from the corpus's review-facts sidecar, naming what moved in their invariant block since the last accepted review facts. The checked-in pins are those facts, so committing the rewritten file accepts new ones. It then runs the gates. Once they have joined and their pytest controllers have written this pass's per-worker peaks to the timings journal, it compares the checked-in per-unit peaks with what this machine measured (`rebuild.tools.calibrate_budgets --check`). It always ends with a summary table, even on failure.

The terminal shows one banner per step with that step's description, the phases and counters its child prints, every warning, and a closing line. All child output is written under var/build-logs/<stamp>-<short sha>/: one log per step with stdout and stderr merged in arrival order, plan.txt, and a copy of the terminal output. var/build-logs/latest points at the newest run, and a failed step's log is replayed under its banner. rebuild.tools.console defines the line protocol children print and the renderer that reads it.

The job-costs step never fails the pass, for the same reason the review-facts pins are not a gate: a stale constant makes a pool the wrong width, which costs time but makes no artifact wrong. It is reported, and committing the re-measured constant accepts it. When the check reports an overrun, the OVERRUN status quotes each tripped row's proposal (the value its constant's rule sets from the peak and the width that value gives here, or for the kernel-build row which constants to re-measure), and the driver asks `calibrate_budgets --moved` which checked constants differ from their values at `HEAD`, so a constant already re-measured shows up by name.

The verdict update is one step run by one child process, rebuild.tools.verdict_update. It carries prior verdicts forward onto the fresh manifest, merges the carried file into the store (--no-merge opts out), runs duplicate-fill, standing-fill from the rules in rebuild/standing-approvals.yaml, and duplicate-fill again, merges each fill as it is written, and clusters the open complaints. The verdict update reads the build's per-unit index sidecar, and its one process holds one copy of it. Each of the verdict update's steps opens with a `[phase] <step>` line and closes with `[t] <step>`. The cycle console pairs the two into one line per step, and the cycle-timings journal reads the step's cost from the `[t]` line. The `[verdict-update] complete:` and `[verdict-update] failed:` lines are results, not phases: `verdict_update_sections` starts a section at each `[phase]` line and closes it at either `[verdict-update]` line.

run_m1's exit status is its own gate's result, but this driver evaluates the three summary JSONs it writes, so a build that died before its evaluator is reported by what it left behind. The gates are defect_errors, the Manual-pin gate (including its scope, so a gate that replayed nothing cannot pass), and multi_matched == 0.

This process, not its children, records each check's result in the timings journal. Every evaluated check (run_m1, conform, rebuild-contracts, make-test, js) appends one kind:"check" line tagged with this run, carrying the evaluator's outcome, not the process's exit code; `make cycle-timings ARGS='--by-outcome'` reads them. run_m1's CLI and make_test_gate record their own line when run by hand, so this driver sets its run id in the environment as AMS_CYCLE_RUN (cycle_timings.CYCLE_RUN_ENV), and those children record nothing when they inherit it. Each check invocation gets one line.

gate:js and gate:make-test depend on no build artifact, so they start at t=0 in a small thread pool while the build steps run in sequence in the main thread. gate:conform (the exhaustive font-versus-settlement sweep at the per-edit maximum length, `run_m1 --conform-only`) starts after the run_m1 gate passes, beside make-test under the default overlap policy and behind it under the queue policy. Its deeper form, `make conform-deep`, never runs in the cycle; the summary has one line saying whether the emitted lookup has a shape the last deep run did not shape. gate:rebuild-contracts runs every test under rebuild/, and none of them reads a live build artifact (rebuild/conftest.py's audit hook enforces this). A hand run uses every core. Under a cycle the suite is submitted with the conform lane, right after the run_m1 gate passes, and runs beside the corpus build at `contracts_pool_width`, set on that one child as PYTEST_XDIST_AUTO_NUM_WORKERS: the cores less the build's parent and its `corpus_job_budget` workers, and, under the overlap policy on a pass that runs gate:make-test, less that gate's pool. The conformance sweep's core cap (`_conform_core_cap`) makes the same subtraction, floored at the acceptance-configuration count, and neither width subtracts the other, so under the overlap policy the sweep and the suite share the cores those processes leave while both run. The suite reads nothing the build lane writes, the review-facts pins included, so it waits for nothing downstream, and on a pass where every upstream stage skips it starts at t=0.

While a gated pass's build lane runs (run_m1, then the corpus build, then the verdict update), the cycle splits the usable cores between that lane and the gate lane (`memory_budget.split_cores`, which gives the build lane the odd core): the corpus build's cap is the build lane's share less its parent, and gate:make-test's pool is the gate lane's share (`make_test_pool_width`), so the two lanes' longest-running pools never book the same core. When the gate lane has nothing left to run as the corpus build's units pool starts (gate:make-test, gate:conform and gate:rebuild-contracts each skipped or finished), the pool takes every core less its parent instead (`corpus_gates_idle_job_budget`): the driver writes var/cycle/gates-idle at that moment (`_GateLaneWatch`), and the build reads it after its load and plan phases. A pass that runs neither run_m1 nor the corpus build gives gate:make-test's pool every core. The burst pools, run_m1's oracle, the corpus build's ink-signature pool and the standing fill's refill pool, are capped at every core on every pass rather than at a lane's share, so each shares the cores with gate:make-test's pool while both run, which costs contention time rather than memory; the refill pool subtracts gate:make-test's bytes, and the oracle's pool leaves them to the reserve (`sweep_job_budget`). Memory is not split: each pool's width subtracts the memory of the pools the plan runs beside it.

`--rebuild-pool` sets how the heavy gate pools share the machine. Under the default overlap policy, gate:conform and gate:rebuild-contracts start as soon as the run_m1 gate passes and run beside gate:make-test's pool, each other, and the build lane. Under the queue policy the gates run make-test, then conform, then rebuild-contracts, so only one heavy gate pool runs at a time, and the build steps run beside whichever one it is. Each pool's width comes from its budget function: the oracle (`sweep_job_budget`) splits its tables into row ranges and uses the cores within the memory limit ORACLE_SHARD_BYTES sets, less what the settle-memo absorbs hold beyond it (ORACLE_ABSORB_BYTES); the conformance sweep (`conform_job_budget`) runs its units (each settlement configuration once per final symbol, and the ss10 overlay whole) one process each, as many at once as CONFORM_SWEEP_UNIT_BYTES fits beside the build lane, capped at the cores the corpus build leaves and never below the acceptance-configuration count; and the corpus build (`corpus_job_budget`) gets the build lane's share less its parent, with gate:make-test's memory off the machine when that gate runs, and every core less its parent for its units pool when the gate lane is idle as that pool starts. Under the overlap policy, on a pass that runs gate:make-test, the conformance sweep's memory budget and core cap and the rebuild suite's width also subtract that gate's pool, which already holds the gate lane, so beside a corpus build at its lane share the suite falls to its floor of one worker and the sweep to its floor of one process per acceptance configuration on every machine. Under the queue policy neither subtracts gate:make-test's pool, which has finished by the time they start. Neither of those two widths subtracts the other's pool. `--dry-run` prints every width with its derivation.

The overlap policy is the default because of whole-pass timings of `--fresh` passes under both policies on the 18-core 48 GiB machine (`doc/fleet.md`), which bare `make cycle-timings` on that machine lists step by step. With its pool at the gate lane's share, gate:make-test finishes on those passes before run_m1 does, so under either policy the conformance sweep and the rebuild suite start beside the corpus build and finish before the build lane, and the build lane ends the pass. The overlap policy's pass is no slower than the queue policy's, so the timings give no reason to switch. Timed by hand there with no corpus build or gate:make-test running, a narrowed conformance sweep and the full-width rebuild suite also finished sooner side by side than one after the other (issue #468 records the runs). Under the overlap policy the sweep and the suite subtract gate:make-test's pool even so, because the plan cannot tell whether it will be running when they start, which leaves the suite one worker; that time is off the critical path on those passes. The signal to time both policies again is an overlap-policy pass with gate:make-test's pool at the gate lane's share (its run line's `plan.make_test_workers`) that a `gate:` step ends. `make cycle-timings ARGS='--critical-path'` counts the passes each step ends, but its row for a pass shape also counts passes run at other gate:make-test widths, so such a pass shows there as growth in a `gate:` step's count.

The comparison behind the default covers `--fresh` passes on the 18-core machine only; `make cycle-timings` shows which machines and which kinds of pass each policy has run on. On the twelve-core machine the same pools share fewer cores. On a pass that runs the corpus build and skips gate:make-test (which skips when its input closure is unchanged since its last green run, as after only rune or rebuild-code edits, which `make_test_exempt` exempts), the two policies give every pool the same width, and under the overlap policy the sweep and the suite share the gate lane's cores while both run.

The cycle runs no cross-language check, because the kernel crate is the only engine that enumerates and the only one that settles. gate:conform checks settlement empirically: it shapes the compiled font through HarfBuzz and compares the result, window by window, with a re-settlement of every swept text through the crate's `settle-cases` subcommand, with the memo keyed on the raw window so the sweep does not depend on the crate's enumeration and fold. `make kernel-gate` is the crate's own gate, to run around a kernel-semantics change; it takes seconds once the crate is built. The spec-ingest parity check is a contracts test (rebuild/test_kernel_io.py) and runs in gate:rebuild-contracts on every cycle.

gate:make-test is skipped when its input closure is unchanged since its last green run. The closure is every tracked or untracked-unignored file that `make_test_exempt` does not exempt; that function's docstring argues each exemption from what the gate runs (make all, which runs build_font over glyph_data/*.yaml non-recursively, typst, pyright over tools/ test/ conftest.py, and pytest test/ site/). The Makefile itself is represented by what `make -n all` and `make -n test` print. Re-running the gate over an unchanged closure would repeat the whole font suite (`make cycle-timings ARGS='--by-step'` reports what a run costs) and check nothing. The last green fingerprint is in rebuild/out/make-test-green.json, written by rebuild.tools.make_test_gate (the `make test` entry point) on every green run, so interactive and cycle greens share one record and `make test` skips on the same test. cycle_summary.json also records the fingerprint the cycle ran or skipped against, for display only. The skip reads only the shared green record, so a green that make_test_gate deleted after a red run cannot come back from an older summary. The fingerprint covers file content only, so a system toolchain change such as a typst upgrade does not move it (pyright and pytest are pinned in uv.lock, which is in the closure). --force-make-test and --fresh spawn `make test FORCE=1` (`make_test_gate_argv`), because the wrapper decides its own skip with the predicate the plan uses (`make_test_skippable`), and a plain `make test` would skip on the closure the flag forced. The plan reserves the gate's cores and memory beside the corpus build only when that predicate says the gate runs.

The verdict update skips the same way, on rebuild/out/verdict-update-green.json. Each step of the verdict update is a pure function of the corpus, the verdicts master, the live store, the checked-in standing approvals, and its own code, so the key covers the corpus's inputs fingerprint and stamp, the master (named `autosave` when it is the live store, otherwise by its path and bytes), the live store's records, standing-approvals' bytes, and the verdict update's code (`verdict_update_code_paths` plus the review/ modules the verdict update runs). The master is in the key because the autosave's hash cannot see it: an export at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code is in the key because no other fingerprint reads the verdict update's modules, and without it a fix to a fill's matcher or to the carry's join would be skipped. `verdict_update_code_paths` lists the verdict update's modules instead of all of rebuild/tools/, and rebuild/test_verdict_update_closure.py checks on every contracts run that the list covers the verdict update's import graph.

The key is taken right after the land, over the store the land wrote (`landing.LANDED_NAME`), and only when the land laid no save made during the pass over the prepared store: a verdict the fills never saw then changes the next plan's key, so the next pass runs the fills again, and a save made after the land changes the live store the next plan hashes. The record is written later, after the complaint list step has also succeeded. Each duplicate pass fills every blank in each unanimously judged duplicate group; the scalar groups are disjoint, so a duplicate fill cannot seed another group. Standing-fill runs once and can seed a blank duplicate sibling, which the final duplicate pass fills. The prepared store has one writer, so this fixed schedule needs no further duplicate pass. The completion line reports successful fills and merges and, when enabled, complaints; it does not claim every proposed verdict was accepted, because a newer tombstone can win a merge. The green requires both that completion line and a successful child.

The verdict-update skip also requires the corpus build to skip, which is what makes the stamp known before the pass runs. A flag that names a carry output disables the skip, since skipping would write nothing to that output.

Every other heavy stage skips on the same principle: a content fingerprint over the stage's input closure, and a green record written only after that content passed.

- run_m1 skips on rebuild/out/run-m1-green.json (the Stage A fingerprint components plus the contact allow-list, the oracle's subset tables and uv.lock's dependency pins) and re-evaluates its gate from the summary JSONs on disk.
- gate:conform skips on conform-green.json, keyed on what the conformance sweep tests for, not on run_m1's closure: the emitted lookup's behavior classes, the font-compilation code and its tools/ closure, the uharfbuzz version, and the sweep's maximum length (`conform_skip_fingerprint`). A rune edit that creates no new rule shape leaves that key unchanged, because the crate's string replay inside run_m1 has already checked the new tables against the engine over every string.
- The rebuild suite skips on rebuild-contracts-green.json, keyed by `contracts_fingerprint` over its closure: the repo files under rebuild/ and glyph_data/, the harness files in REBUILD_GATE_HARNESS_PATHS, conftest.py, pyproject.toml, uv.lock by its dependency pins, and the site fonts without their head and name tables. The closure contains no build artifact, so the suite can skip whether or not run_m1 rebuilt: an M1 rebuild writes only under rebuild/out, which the closure does not include. The record also stores each test's input closure and digests of the extra paths it names; a skip requires those paths to match too. The shared contracts lifecycle prepares the plan without writes, refreshes the selection after any queue wait, and checks that runtime snapshot before publishing. A pass whose inputs changed runs only the tests whose closure the diff reaches; a rune edit reruns the tests that load the spec and nothing else. rebuild.tools.contracts_closure defines what a closure holds and when a test may be skipped, and runs the test whenever it cannot tell. rebuild.tools.rebuild_gate (`make test-rebuild`) writes the same record, so interactive and cycle greens share it.
- corpus-build skips when the manifest's recorded inputs fingerprint equals the one a build would stamp now. A rebuild would then reproduce its content byte for byte, but `generated_at` is the latest input mtime, floored, so a rebuild after an mtime-only change (a checkout, a touch) could restamp it; skipping keeps the stamp, so the autosave stays aligned. When the live corpus does not match but a corpus built beside it does (a complete rebuild/out/review.next a stopped pass left, the last cycle summary's `plan.review_out`, or var/staged-review), the land swaps that directory in with its stores instead of a rebuild (`promotable_corpus` checks the preconditions). Every stamp inside a corpus depends only on content relative to its manifest, and the swap keeps the `generated_at` a rebuild could reset.
- The review-facts step has no key and never skips: it reads the corpus build's review-facts.json sidecar and rewrites one small checked-in file in milliseconds.

The corpus skip applies only on passes where run_m1 skipped, and on a gates-only rerun when the Stage A record on disk already matches what that pass will write (`m1_stage_a_current`), because the corpus reads nothing else the pass writes. That happens on a contact-allow bless, the only comparison-side edit outside every Stage A component.

Conform's skip is decided after run_m1 finishes, from the key the artifacts it left carry. A mode that leaves the emitted lookup's shapes, the compile code and the shaper at the last green key skips the sweep, whether run_m1 skipped, reran only its gates, or rebuilt, and the skip is recorded as proved because a matching green covers this exact content. Computing the key only after run_m1 has finished also means an M1 rebuild cannot invalidate it during the cycle. The preflight can decide it before the pass only in the mode where run_m1 skipped, so nothing will change; that is the mode --dry-run can predict. On a gates-only rerun or a rebuild the printed plan shows the conform lane as undecided (`run?`), because only a finished run_m1 knows what the artifacts are, so a plan that shows the sweep may end in a pass that skips it.

Green records are written only when the key still matches after the work ran, and a red result whose key matches its record deletes the record. --fresh runs everything regardless.

Between the run_m1 skip and a full rebuild there is a third mode, the gates-only rerun. When the per-file diff against the run_m1 green is confined to comparison-side inputs (the alias map, the divergence ledger, the contact allow-list, the kern sidecar, the oracle's two modules, and the baselines and their subsets, all outside the tables' stamp; `comparison_side_label` lists them and argues each), the tables on disk still carry that stamp, and all the artifacts are present, the cycle spawns `run_m1 --gates-only` instead of a build. It re-runs the defect gate, the Manual-pin gate and the oracle over the tables and font on disk, matches the oracle's rows against the ledgers again, and enumerates nothing. The green that pass records covers the new inputs, so the next cycle skips run_m1. `uv.lock` is not comparison-side, because a fontTools or uharfbuzz bump can change the font's bytes and what the shaper does with them, so a toolchain bump rebuilds.

The review server keeps running through every pass. Two things a cycle writes belong to the running app: the corpus it serves (a rebuild rewrites every shard the tab reads by byte range, and restamps the manifest the tab's saves are keyed on) and the verdict store it saves into. So no pass writes either in place. A corpus-changing pass seeds rebuild/out/review.next with a copy-on-write clone of the served corpus (`corpus-seed`, `landing.clone_tree`), builds there (`--out`), copies the live store into its scratch directory under the store's lock (`store-snapshot`), and runs the verdict update against that copy (`--autosave`, `--journal`). The land (`rebuild.review.landing`) then swaps the new corpus in and puts the prepared store in place in one short section under the store's lock, laying the saves made during the pass over the prepared store, and runs as a child in its own session that a stop signal does not reach and the driver waits for (`_ChildRegistry`). A land killed inside that section is finished by the next holder of the lock: the review server's next request, the next pass (`recover_land`), or any writer that takes the lock through `landing.locked_store`. After the land the pass sends `ams:corpus/<generated_at>` through livereload's /forcereload, and the open tabs save, wait while the reader types, and reload onto the new corpus. Only the first run builds in place, since no server can serve a corpus that does not exist yet.

The cycle keeps a listening server only when its /capabilities says it serves this checkout with the working tree's land-protocol code (`probe_server`); a server from this checkout running other code is restarted first (`restart_review_server`), and one serving another checkout is left alone and sent no reload. A server from before the land protocol answers no /capabilities, and it takes the old rule: a pass that writes neither the corpus nor the store keeps it running (`server_can_keep_running`), and any other pass stops it once with --stop-server (which `make review-cycle` passes), runs beside it with --yes, or refuses.

A pass whose corpus did not change but whose store did has its own mode, the direct merge. The carry there maps every unit id to itself and keeps each record's `at`, which the merge compares strictly, so the carry is skipped and the master is merged straight in; the master is the one input the store's own hash cannot see. That pass lands the store alone. The direct merge needs the master stamped for the served corpus, as the merge requires of every input. A master stamped for another corpus, which a pass stopped between the corpus build and the carry leaves behind, takes the full carry instead. The carry source's resolution says which of the two an auto-resolved master is, and `master_stamped_for_corpus` says it for a --verdicts one. When the master resolved to the live store, the verdict update carries or merges the snapshot in its place; any other master is carried beside the snapshot, so the store's own verdicts are never left in a stash.

An edit confined to rebuild/review/static/ also has its own mode. The copied app assets are the one corpus input no unit depends on, so the pass copies them over the served copy and restamps that one fingerprint component (`assets-refresh`), each file renamed into place. Every shard, both sidecars, the unit-cache store and `generated_at` stay as they were, so nothing the tab is keyed on changes. Once the files are in place the pass sends `ams:assets/<static hash>`, and the open tab reloads onto the new assets when the reader is not typing. The server itself reloads no tab on a file change (`rebuild.review.serve.register_dormant_watch`).

A corpus promotion is the opposite case under the same skip: the land swaps the staged corpus in, and the stamp changes with it. Both the verdict-update skip and the direct merge are off, because both assume the corpus did not change, and here the store's verdicts must be carried onto the promoted units by id, so the verdict update reads the staged corpus before the land.

Retention holds the store's lock only for the tail of the journal appended since it read it, the deletions, and the journal's replacement (`run_retention`), and the server answers a save made in that window with the same retryable 503 it gives during a land.

Every pass holds the pass lock (`pass_lock`, var/cycle/pass.lock) from before it recovers a superseded corpus and resolves its plan until it ends, so a second pass waits for the first instead of planning against a tree the first is still writing. The land inherits the lock, so a driver killed while its land runs still holds the next pass back until the land has finished. A staging pass holds it too, because a live pass's promotion reads the corpus a staging pass writes. A dry run takes it without waiting, and when another pass holds it the dry run skips the recovery, which could rename a tree that pass is landing. Each non-staging pass gets a scratch directory under var/cycle/ named like its build-log run directory and deletes it when it ends; the next pass deletes any that a killed pass left (`sweep_run_dirs`). A staging pass and a dry run get no scratch directory.

A green finish ends with a retention pass over the cycle's own files, all of them regenerable or covered by the journal. Root verdicts-carried-*.json files not stamped for the live corpus are deleted, since `status.pick_fullest_verdicts` reads only files stamped for the live corpus, and the tracked copy under rebuild/evidence/ is never touched. verdicts-autosave-* stashes not referenced by a journal event at or after the last event that moved the stamp or wrote a base are deleted. The journal, not the stashes, is the supported recovery path, and the check uses the journal's references because a stash's mtime predates the event that created it. The journal is compacted to the newest base event older than RETENTION_WINDOW_DAYS, keeping at least that many days of --restore-as-of history, and build-log run directories beyond the newest `cycle_paths.BUILD_LOGS_KEEP` are deleted. Both journal steps share one scan, which resumes from where the last pass's scan stopped (`journal_scan_path`). Failed, interrupted, first-run, and staging passes never prune, --keep-history turns retention off, and a retention error prints a warning and never turns a green cycle red.

Run as: uv run python rebuild/tools/artifact_cycle.py. The carry source is resolved from the autosave and the verdicts-*.json exports; pass --verdicts to name one.
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
from collections.abc import Collection, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from rebuild.review import app_index, facts, journal, landing, store_lock, unit_index  # noqa: E402
from rebuild.review.audit import load_ledger  # noqa: E402
from rebuild.tools import console, cycle_paths  # noqa: E402
from rebuild.tools import contracts_closure as contracts  # noqa: E402
from rebuild.tools.green_record import (  # noqa: E402
    _digest_lines,
    _record_outcome,
    _sha256_path,
    clear_contradicted_green,
    read_green_record,
    record_green,
    settle_green,
)
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV, CheckResult  # noqa: E402
from rebuild.tools.peak_rss import reap_peak_rss_bytes  # noqa: E402
from rebuild.tools.review_server import (  # noqa: E402
    NO_CAPABILITIES,
    REVIEW_PORT,
    capabilities,
    force_reload,
    server_listening,
)

if TYPE_CHECKING:
    from rebuild.tools.cycle_timings import CycleTimings
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
DUPLICATE_FILL = ROOT / "verdicts-duplicate-fill.json"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
FACTS_PINS = ROOT / "rebuild" / "review-facts-pins.json"
DIVERGENCE_LEDGER = ROOT / "rebuild" / "m1-divergences.yaml"
CYCLE_TIMINGS = ROOT / "rebuild" / "out" / "cycle-timings.ndjson"
BEHAVIOR_CLASSES = cycle_paths.M1_OUT / "behavior_classes.json"
JSTEST_DIR = ROOT / "rebuild" / "review" / "jstests"

POOL_POLICIES = ("queue", "overlap")
REBUILD_POOL_POLICY_DEFAULT = "overlap"
VERDICT_UPDATE_SKIP_NOTE = "corpus, verdicts master, live store, and standing approvals unchanged since the last complete verdict-update pass; --fresh overrides"
DIRECT_MERGE_DECLINED_NOTE = "The corpus build is skipped, but the verdicts master is not stamped for the served corpus and the merge would refuse it, so the verdict update carries it onto the served corpus by unit id rather than merging it straight in."
CONFORM_SKIP_NOTE = "no new rule shape, compile code or shaper since its last green sweep; --fresh overrides"
CONFORM_MAYBE_NOTE = "runs unless run_m1 leaves the emitted lookup's behavior classes, the compile code and the shaper under the key of its last green sweep, in which case it is re-skipped after run_m1"
UNDECIDED_UNTIL_RUN_M1 = {
    "gate:conform": CONFORM_MAYBE_NOTE,
}
ASSETS_REFRESH_NOTE = "only the review UI assets moved since the corpus was stamped; they are copied over the served copy and the manifest's static component restamped in place — no shard, sidecar or generated_at moves; --fresh overrides"
CORPUS_PROMOTE_NOTE = "a staging pass already built the corpus these inputs produce, byte for byte, unit store and signature store beside it; the land swaps that directory in instead of a rebuild; --fresh overrides"
SERVER_KEEPS_RUNNING_NOTE = (
    "rewrites no unit shard, moves no manifest stamp, and leaves the verdict store alone"
)
SERVER_STOP_PATTERN = r"rebuild\.review\.serve"
SERVER_STOP_TIMEOUT = 15.0
SERVER_START_TIMEOUT = 30.0
CAPABILITIES_ATTEMPTS = 3
CAPABILITIES_RETRY_S = 1.0
# The gate thread pool's worker count, sized to the tasks the chain submits, not to the cores. Under the queue policy a waiting task holds its worker for the whole wait (conform waits on make-test, and contracts on both), so every gate task needs a worker at the same time, with spare workers on top. With fewer workers a waiting task could sit behind an unrelated task's completion, and a width taken from the cores would cause that on a small machine. `test_the_gate_pool_runs_every_gate_task_at_once` in rebuild/test_artifact_cycle.py checks that this equals the gate task count plus two.
_GATE_POOL_WORKERS = 6
# The peak memory of one corpus-build worker, the divisor of the build's width (`corpus_job_budget`). A worker is a persistent spawn process that takes unit batches from the parent's hand-out queue (`_handout_width` in rebuild/review/build.py: the enricher's settlement batch, or a smaller spread of few recomputed units), enriches and drafts each unit, spools each fragment to disk as it is drafted so no EnrichedUnit outlives its batch (`_FragmentSpool`), and replies with the batch's projections and spool addresses. Nothing it holds grows with the corpus, the alphabet or the width. It holds its interpreter and shapers, whose shape memo is released at every batch boundary (`rebuild.review.ink.release_shape_memos`; `_MemoizedShaper`'s docstring records the measurement that showed an unreleased memo outgrowing everything else in a serial build). It holds one batch's units, projections and addresses, bounded by `PHASE1_HANDOUT_UNITS`, from the hand-out until the reply is pickled; the `SubsetRow`s for one batch's units; and the pages it touches of the baseline subset pack (rebuild/review/subset_pack.py), which the parent writes once before the pool starts and every worker maps read-only, so the page cache holds one copy per machine. The parent hands units out in configuration order (`_configuration_order` in rebuild/review/build.py), so a worker's touched pages are mostly one configuration's key range.
# The measurement set is every width-eight pool on the mapped pack since the previous measurement set, on the 32 GiB machine and the 18-core 48 GiB machine (`doc/fleet.md`). On passes that draft units, workers peak at up to 0.525 GB on the 32 GiB machine over the qsYe corpus, and each pass's largest worker reads 0.42 to 0.48 GB on the 48 GiB machine. A cached pass's workers read under 0.1 GB. For comparison, a width-six pool over the 32-letter corpus that held its subset tables in Python read 1.16 to 1.95 GB. The constant is the largest mapped-pack reading plus a quarter, rounded up to the hundredth, for the reason kernel_exec.DELTA_SLOT_BYTES errs high: a cost that is too low pushes the machine into swap, while one that is too high only narrows the pool. A worker's peak depends on the batches it draws, not on the width, so a wider pool repeats the same reading. A new letter changes it only through what a batch holds (a window's rows and shapes), not through the tables, so under a gated cycle every fleet machine stays at the build lane's share of its cores less the build's parent (`corpus_job_budget`) as letters are added (`test_the_shipped_corpus_divisor_holds_the_fleet_at_its_lane_share_by_division` in rebuild/test_memory_budget.py checks the division, and `test_both_fleet_machines_keep_a_pooled_build_under_a_gated_cycle` in rebuild/test_artifact_cycle.py the width). The corpus-worker row of `make job-costs` checks the constant against the kind:"pool" record rebuild/review/build.py writes for each pooled build. Every fleet machine runs the build pooled, so a machine with no rows has not been made serial by this width.
CORPUS_WORKER_BYTES = 660_000_000
# The peak memory of the corpus build's parent while its pool runs, subtracted from the machine's memory before dividing by CORPUS_WORKER_BYTES. The parent holds the workload table (`audit.UnitTable` in rebuild/review/audit.py: each unit's class, group, unmatched group, duplicate group and cluster as ids into one string table, its config set, kinds, render groups and per-config class map as ids into pools of a few dozen values, its window parsed once into a `u16` side column, its run of audit rows and its triage position, all as fixed-width `array` columns over the unit's ordinal, well under 100 bytes a unit); the packed unit store (rebuild/review/unit_store.py) over the same ordinal and string table; the write's folded checker (`build._CorpusCheck`); the review facts' pre-merge snapshot (`facts.PremergeSnapshot`, columns over the pre-merge rows at 18 bytes a row, 23 after `rebase`); one fragment or one materialized `audit.Unit`; one batch of materialized records while a hand-out is being sent; and one batch reply in flight per worker. The hand-out and the replies are the only terms the width changes. The parent never holds a list of unit records at any phase (`UnitTable.unit` materializes one for a reader that needs it, and `units` is for the review-facts CLI and the tests).
# The unit store holds every per-unit product of phase 1 from the plan boundary to the cache write (the machine flags and ink deltas, the diff and cluster digests, the primary-unit projection with its spans, names and rects, the primary-unit assignments, the fragment's spool or prior address, the shard address the write returns, the content and input keys, the policy file and the config note) as fixed-width `array` columns plus one string table over the vocabulary, not the corpus (the `[tally] … unit_store` line measures the table beside the columns). It materializes one unit at a time for a whole-corpus pass or a store line. A slotted state record, a spool-address object and a dict keyed by id hold the same facts in about six to twenty times the bytes, collection by collection: the `[tally]` lines of a pass under `AMS_CORPUS_MEMORY_TALLY=1` show the walked and packed bytes of each collection side by side, and that comparison is why the store uses columns. The table's `[tally] … workload.units` line compares the same way against 645.6 bytes a unit for a list of records.
# Neither ink-signature table is in the steady-state memory after the load phase. The window-keyed table the ink-duplicate merge reads (`signatures` in rebuild/review/build.py) is released when that merge returns, in the load phase. The store's records (`signature_entries`) are held through the plan phase and released when the store is written: inline as the units phase opens on a serial build, and a few seconds into the units phase on a pooled build, when the `_SignatureWrite` thread that `build_m1` starts at that boundary finishes. So the records overlap the pool only for the length of that write. The load boundary's `signatures` tally line includes the table's strings, and the digest strings are shared with the records, so the release frees less than the sum of the two lines.
# This figure covers the whole corpus-build step, not one phase. On a fully recomputed pass the load phase holds the row columns (`audit.RowColumns`, five flat arrays over the audit's rows with their tuple pool, about 17 bytes a row), the table, the snapshot and both signature tables. The steady-state memory after the load phase holds the table, the store's columns and the snapshot, with the name tuples released at the units boundary (`UnitTable.release_names`), and from the write on the write's folded checker. The `rss_now_gb=` token beside `rss_gb=` on each `[t] review.build` line gives a phase's own resident set, separate from the step's peak (`_phase_timing` in rebuild/review/build.py). On fully recomputed and cached passes alike the load boundary sets the peak: `rss_gb=` reaches the step's figure on the load line and does not rise after it, the load's `rss_now_gb=` is the pass's highest resident reading, and the boundaries after the load phase read below it, by about 1.5 GB on a fully recomputed pass and by more than 0.5 GB at the cached pass's units boundary. So the row columns and both signature tables beside the table set the step's peak, not the store the build holds after the load phase.
# A cached pass's plan phase streams the store (`unit_cache.stream_store`) and loads each record into the columns as it is parsed. Beside the table and the store's columns, the plan holds the map from input key to ordinal (built from the store's key column and released at the plan's `del named`), one `unit_cache.ParsedCachedUnit`, whose projection tuples are pooled within the record (`unit_cache._parsed_cached_unit`), and the addressless records: records the store returns without an address, buffered until one pass over the previous corpus's shards places them (the `[tally] plan unit_cache.unplaced` line measures them). A store this code wrote has none, so a cached pass reads at or below a fully recomputed one, with its plan boundary well below its load. A store whose every record is addressless (every part resized under it) buffers every record, and that plan reads like a parse of the whole store; streaming limits the spike to that case but does not remove it. Fragments stream by address into the shards and are released after the checker and sidecar spools consume them. Before the workload loads, the same process packs the baseline subset tables (rebuild/review/subset_pack.py; `write_pack` holds every table's keys and row indices beside the pool of distinct rows), a transient well below the pool-time peak that this constant covers without a term of its own.
# The peak grows with the alphabet. The corpus-parent row of `make job-costs` measures it through the corpus-build step's peak: `peak_rss.reap_peak_rss_bytes` takes the largest process peak in the tree, and the parent holds the corpus while each worker holds one batch. The measurement set is two untallied width-eight passes over the qsYe corpus on the 32 GiB machine (`doc/fleet.md`), with nothing edited between them, the workload held as the table's columns and the cached plan loading each record as it is parsed. The fully recomputed pass, run by `make artifact-cycle`, peaks at 4.01 GB, and the cached pass, run by hand over the corpus the first one wrote, peaks at 4.00 GB. Both peaks occur at the load boundary (the load's `rss_now_gb=` reads 3.79 and 3.78, and the cached plan boundary's 2.90). The constant is the higher reading plus a quarter, rounded up to the next whole gigabyte, as STANDING_FILL_PARENT_BYTES is: a cost that is too low pushes the machine into swap, one that is too high only narrows the pool, and the peak grows with the corpus, so every new letter raises it before the row shows it.
# Beyond the columns, the per-unit objects the parent holds after the write are its folded checker's: every unit's id, each human unit's grouping keys, and the identity of each unit in a secondary-junction relation (the `[tally] … checker.identity` line). The window is the one per-unit column held twice: the primary-unit projection's codepoint values and the table's `u16` side column. The one per-unit transient beside them is the triage sort's (`audit._triage_permutation`, run once at the load and once at the units boundary): one integer a row, with its class, group, window and id terms packed into it, a few dozen bytes each, in a list sorted in place and dropped when the permutation returns. No `[tally]` line measures it, and a units-boundary `rss_now_gb=` shows it only as memory the allocator has not yet returned. A tallied pass reads higher than the same pass untallied, because it rebuilds the plan's input-key map at every boundary and keeps the addressless records past the `del` that frees them, so re-measure from the row's step peak, never from a tally line. The row counts only measurements after the commit that sets the constant. A hand build writes pool records only, so read its step peak from its `[t] review.build` lines. Re-measure as the alphabet grows.
CORPUS_PARENT_BYTES = 6_000_000_000
# The peak memory of one worker in the corpus build's write (phase 2, `_write_corpus` in rebuild/review/build.py), the divisor of that pool's width (`corpus_write_job_budget`). A write worker is a spawn process that loads the parent's workload table and unit store once (`build._dump_columns`), without the store columns the write never reads (`unit_store.WRITE_UNREAD_COLUMNS`), which leaves about 0.29 GB of the two objects' 0.54 GB of columns at 44 runes. It then writes whole runs of classes (`build._write_run`): it reads, patches and serializes one fragment at a time, and for the run it is writing keeps each unit's shard address, config note, policy file and id, the grouping keys of each human unit, and the identity of each unit in a secondary-junction relation, until it replies with the run. So what it holds grows with the corpus and with the largest run, which at 44 runes is the largest class alone. The measurement set is the write pools of a fully recomputed hand build, the cached hand build over it, and a fully recomputed `make artifact-cycle` pass, on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) at 44 runes, three workers each: 0.46 to 0.55 GB, the highest in the worker that wrote the run holding the largest class, `bare-name-live-join` (686,299 of 1,559,696 units). The constant is the highest reading plus a quarter, rounded up to the hundredth, as CORPUS_WORKER_BYTES is. The corpus-write-worker row of `make job-costs` checks it against the kind:"pool" records the write's pool files, so re-measure it from that row as the alphabet grows.
CORPUS_WRITE_WORKER_BYTES = 690_000_000
# The peak memory of one worker in the standing fill's refill pool, the divisor of that pool's width (`standing_fill_jobs`). A worker is a spawn process holding its interpreter, the rules, a `SlideContext` over the corpus's font pair (two shapers), and one chunk of `_STANDING_POOL_CHUNK` units (rebuild/tools/standing_verdicts.py) with that chunk's shape and walk memos and its alignment cache. All three are emptied after every chunk, so the peak depends on the chunk, not on the pool's share of the units. The measurement set is three hand refills in the verdict update's form (`--open-only --require-reach`) with the memo dropped (`--fresh-memo`), each worker's resident set sampled every 0.1 s on the 18-core 48 GiB machine (`doc/fleet.md`). Two ran eighteen wide, and their thirty-six workers read 95 to 104 MB. One ran two wide, and its workers, which decided about fifty-eight chunks each, read 99 MB, so a worker's peak does not grow with the chunks it decides. The constant is more than three times the highest reading, because no journal row measures this worker: `make job-costs` has no standing-fill-worker row, since writing pool records from the fill would put cycle_timings and peak_rss into the memo's code stamp (`MEMO_CODE_MODULES`) and drop the memo on every width change. Re-measure by hand at the chunk width when the chunk size or what a decision holds changes. At this figure the cores, not memory, limit this pool on every fleet machine.
STANDING_FILL_WORKER_BYTES = 350_000_000
# The peak memory of the verdict update's process while the standing fill's pool runs, subtracted from the machine's memory before dividing by STANDING_FILL_WORKER_BYTES. The parent holds every corpus id, the human id/duplicate-group/notation projection, and the fill's rules, primed keys, decisions and memo. Full human records stream through the verdict update's steps. Refill misses go to a temporary gzipped spool, with at most one pool round of records in memory, bounded by the width times `_STANDING_POOL_CHUNK` (rebuild/tools/standing_verdicts.py). The complaint list keeps compact grouping projections. A serial refill, and the memo check `_prefill` runs on every served unit, hold one unit's `SlideContext` memos and alignment-cache entries at a time, because `Decider._release` empties both after each unit.
# The standing-fill-parent row of `make job-costs` reads the whole verdict-update step through `peak_rss.reap_peak_rss_bytes`. A standalone fill over every unit puts the in-flight round at about 18 MB a worker: its parent reads 1.17 GB two wide and 1.45 GB eighteen wide. The measurement set is the highest step reading since the previous measurement set: 5.21 GB on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) at 43 runes, over a carry with three duplicate-fill rounds (run 6fcc0f470e0b). On that machine passes with two rounds read 2.25 to 3.79 GB and passes with three 3.65 to 5.21 GB, and at 44 runes 2.59 to 3.40 GB and 3.90 to 4.35 GB. The third round holds no more than the rounds before it; it raises the peak by adding to the memory the process keeps between phases. Measured by hand at 44 runes over copies of the store, sampling every 20 ms (`var/keep/issue-495/verdict-update/`), a carried pass with two rounds peaks at 2.70 GB in the standing fill. One with three, made by blanking twenty duplicate groups whose verdicts the standing fill gives back, peaks at 3.88 GB in the complaint list, which starts from 3.12 GB: from the standing merge on, each phase parses the store, frees it and leaves the resident set 0.4 to 0.75 GB higher than it found it, the third round's merge and fill 0.98 GB between them. Under `tracemalloc` the same pass holds 0.27 GB of live Python objects at the end of every phase (the corpus ids and the duplicate projection), and no phase's own peak passes 1.3 GB of them, so what grows is freed memory the allocator keeps, not objects a phase leaves reachable. The constant is the highest reading plus a quarter, rounded up to the next whole gigabyte. No fleet width changes at this figure: the cores limit the refill pool and the conformance sweep beside it. Ids and decisions grow with the alphabet, and each extra round raises the floor the complaint list starts from, so watch the row as letters are added.
STANDING_FILL_PARENT_BYTES = 7_000_000_000
# The peak memory of the land (`rebuild.review.landing`), the build-lane step after the verdict update that moves the corpus and the store into place. It holds three parsed stores at once, the snapshot, the prepared store and the live one, then the new corpus's human unit ids and the serialized result. The measurement set is the land step's peaks in the timings journal on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) at 44 runes, with the store at up to 243,082 records in 101.5 MB: 1.65 to 1.96 GB, the highest on run 3a3c8e5d239e. The peak follows the store, which every letter batch adds to: the same machine's land read 1.61 GB on 2026-09-30, with about 210,000 records. The parsed stores are the largest term: three parses of a 209,645-record store in one process read 0.41 GB a store. The constant is the highest reading plus a quarter, rounded up to the next whole gigabyte, as the other build-lane parents are. It is below both CORPUS_PARENT_BYTES and STANDING_FILL_PARENT_BYTES, which `test_the_land_holds_less_than_either_build_lane_step_before_it` checks, and the land runs after the corpus build and the verdict update in the same lane, so the build lane's reservation (`_conform_build_lane`), which covers the larger of those two, covers it too, and no width depends on it. The land row of `make job-costs` checks it against the land step's peak, so re-measure it from that row as the store grows.
LAND_BYTES = 3_000_000_000
# The peak memory of one oracle row-range worker, the divisor of the oracle's width (`sweep_job_budget`). A worker is a spawn process. It holds its interpreter, a HarfBuzz shaper over M1.otf, and the crate's guard verdict map. It holds the records of its own row range of its configuration's row store: one buffer of the range's record bytes with three packed arrays beside it (an offset and two ages a record), loaded without scanning the whole member (`oracle_cache.load_store`), so it holds its range's rows and not the configuration's. It holds them on a pass whose row stamp moved too: that pass walks every row, as a pass with the row cache dropped does, and serves each stored position whose settled digest still matches (`oracle_cache.RowStore.position_servable`), so it holds the records and the dropped pass's walk at once. It maps its configuration's settle memo read-only on the first wave that reaches the crate, which every pass does, since the scheduled re-derivation covers one row in `oracle_cache.MAX_RECORD_AGE`. `conform._MemoStore` reads the file's own layout: the six id columns, the value column and the 2^k >= 2N-slot probe index are views over the mapping, 69.8 MB for a live memo of 2.6M windows (the `[t] settle_memo` lines count them). Those pages belong to the page cache, resident once per machine however many workers map the file, and count in a worker's resident set as its probes touch them: a walk with the row cache filled probes the one row in twenty the cache does not serve, and a walk with the row cache dropped probes nearly every row. On a pass after a family changed, the retirement fold reads the six id columns whole once (36.2 MB of the mapping, `conform._MemoStore.load` over `mask.moved`); the measurement set's passes ran on an unchanged tree, every `[t] settle_memo` line at stale=0, so that fold is outside the measurement set and inside the headroom. The worker's own heap holds the file's interned label and outcome tables, a dead byte and a reached byte a row, 0.005 GB at the load. Last, it holds the walk's state over the range: the chunk of rows in flight (`oracle.ORACLE_ROW_CHUNK`), the waves of windows the crate settles for it, and `windows`, the dict of entries the walk promotes from the mapping or settles fresh. On a pass that serves from its store it also holds each chunk's offers to the two verification samples (`oracle_cache.VerificationSample.offer_many`), and while a range on one segment of a joined store loads, that segment's compressed bytes beside its inflated records (`oracle_cache._indexed_records`). The cyclic collector is held off for the range (`oracle._collector_held`), so the few reference cycles a range builds stay until it ends.
# No range writes the memo file. Every range, whether or not its configuration is split, writes the windows it settled fresh as a part (`run_m1._shard_settle_memo`). The parent's absorb, one task per settlement configuration on this same pool, runs once every range has finished and the witness stage has returned (`run_m1.run_oracle`'s `memo_ready`). It holds the existing rows, the parts, the existing index and the writer's folded copies at once (`conform._write_settle_memo`), roughly the file's size plus the columns'. Its reading is recorded in the pool record beside the ranges' as `<config> absorb`. It is a process peak like the rest, so it reads at or above the range its worker ran before it, and it can read above every range of its record, because it holds its configuration's settle memo file, which grows with the alphabet and not with the width: six of the journal's records from hand M1 builds on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`) at 44 runes have absorbs at 0.64 to 0.65 GB where their ranges read at most 0.55 to 0.58 GB, and at 45 runes a pass that settles every window fresh, because a statement added to a module of `oracle_cache.ORACLE_ROW_CODE_PATHS` moved both the row stamp and the settle memo's, has absorbs at 0.78 to 0.79 GB where its ranges read at most 0.68 GB at width eighteen, and at 0.92 to 0.93 GB where they read at most 0.86 GB at width twelve. The absorbs are outside this constant's measurement set; ORACLE_ABSORB_BYTES prices them.
# The staged measurement, taken on the 10-core M1 Pro 32 GiB MacBook Pro before ·Ye, is one process running `oracle.oracle_config_worker` over one shard of `oracle.oracle_shard_plan`, with its row cache filled (`read_dir` the out dir) or dropped (`read_dir=None`), recording `resource.getrusage`'s `ru_maxrss` and the resident set from `ps` at the interpreter, after the imports, spec, `kernel_exec.guard_sweep` and shaper, around `oracle.open_row_cache`, around `_SettledWindowWalk._load_memo` with the mapping's size and the store's heap sizes printed, and at return. The probe and both its logs are under `var/keep/rung263-4/stage-probe/`. Over the 708,015-row `default 1/3` of the width-twelve plan the terms read: the interpreter with its imports, spec, guard verdict map and shaper 0.07 GB; the range's store records 0.05 GB resident behind a 0.16 GB peak at the load with the row cache filled, and nothing with it dropped; the first chunk and its wave, before the memo maps, 0.14 GB with the row cache filled and 0.15 GB with it dropped; the memo 0.005 GB of heap at the load, filled and dropped alike, over a 69.8 MB mapping of 36.2 MB of columns and 33.6 MB of index; and the walk from there to the process's peak, including the mapping's touched pages, 0.27 GB with the row cache filled (71,448 windows promoted) and 0.33 GB with it dropped (1,171,462 promoted). The peak is 0.52 GB filled and 0.55 GB dropped, which is what the pool records read for that range.
# The largest term is the walk over the range's rows, 0.41 to 0.48 GB from the first chunk to the peak: more than fifty times the memo's heap, and more than four times the whole mapping even with every mapped page subtracted from the low end. In the measurement set below, the memo-less ss10 ranges, whose overlay walk maps nothing, read 0.34 to 0.51 GB where they run first in their worker, against 0.37 to 0.93 GB for the ranges that map a memo. A pass whose row stamp moved reads highest, because it holds its range's records beside the walk a dropped pass makes, and both grow with the range: at width twelve the 981,516-row settlement ranges read 0.88 to 0.93 GB with the row stamp moved and the settle memo already rebuilt under it, 0.84 to 0.86 GB on the pass that rebuilds it, against 0.70 to 0.72 GB with the row cache dropped and 0.65 to 0.67 GB with it filled. At that width each range reads a width-eighteen store whole, so its records sit in one buffer that grew as it read them; a range whose bounds are a store segment's inflates that segment into a buffer of its exact size.
# Read a pool record for the shape across the machine, never for one range's cost. The oracle calls `run_m1._spawn_pool` without `max_tasks_per_child`, and a worker reports its process peak (`peak_rss.peak_rss_self_bytes`), so a range that runs second in a reused worker reads at or above the peak the previous range left, whatever its own row count. In the measurement set's width-twelve record with the row cache filled the 178,458-row memo-less `ss10 1/2` reads 0.65 GB, the peak of the range its worker ran first, while its sibling `ss10 2/2` at 1,963,032 rows reads 0.44 GB, and with the row stamp moved they read 0.90 and 0.51 GB over a rebuilt memo and 0.69 and 0.50 GB on the pass that rebuilds it. The record with the row cache dropped does not show this carry-over; there both read 0.34 GB.
# The measurement set is the eleven `run_m1 --gates-only` passes at 45 runes on the 18-core M5 Pro 48 GiB MacBook Pro whose pool records finished between 2026-10-09T04:15:40Z and 05:18:03Z, with the row cache filled, with the row stamp moved by a statement added to a module of `oracle_cache.ORACLE_ROW_CODE_PATHS`, and with the row cache dropped (`--fresh-oracle-cache`), each at its own width, `--jobs 18` over twenty-three ranges, and at a stated `--jobs 12` over seventeen, which is the plan the 12-core M4 Pro Mac mini (48 GiB) chooses. The statement moves the settle memo's stamp too, so at each width one pass with the row stamp moved settles every window fresh and the others find the memo rebuilt under it. The ranges' highest readings are 0.62 GB filled, 0.74 GB with the row stamp moved (0.68 GB on the pass that rebuilds the memo) and 0.58 GB dropped at width eighteen, and 0.67 GB filled, 0.93 GB with the row stamp moved (0.86 GB on the pass that rebuilds the memo) and 0.72 GB dropped at width twelve (the pool records and the passes' logs are under `var/keep/issue-517/`). The constant is the highest range reading plus a quarter, rounded up to the tenth, because a cost that is too low pushes the machine into swap, and because the walk's state grows with the alphabet. The oracle-shard row of `make job-costs` checks it: `run_m1.run_oracle` writes one kind:"pool" record per fan-out, one observation per row range that ran and one per absorb, which the oracle-absorb row reads instead, and the cycle's job-costs step runs the check. The rebuild suite checks the constant only against the cores, which cannot catch a figure that is too low. At this figure the cores, not memory, limit this pool on every fleet machine.
ORACLE_SHARD_BYTES = 1_200_000_000
# The peak memory of one settle-memo absorb on the oracle's pool (`run_m1._absorb_settle_memo_parts`), which merges one settlement configuration's range parts into its file and holds the existing rows, the parts, the existing index and the writer's folded copies at once (`conform._write_settle_memo`), roughly the file's size plus the columns'. Its cost follows the configuration's settle memo file, which grows with the alphabet, and not the oracle's width. The absorbs run once every range has finished, one task per settlement configuration on the same pool, so no more than the configuration count run at once at any width, and `sweep_job_budget` prices that many slots at this figure in place of ORACLE_SHARD_BYTES: it takes their excess over the range slots they run in off the machine's memory before it divides. A worker reports its process peak and the pool reuses its workers, so an absorb reads at or above the range its worker ran before it: a reading at a range's level may be that range's peak, and a reading above every range of its record is the absorb's own. The measurement set is every absorb reading in the oracle's pool records since 2026-09-16. At 44 runes on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), the twenty-five run_m1 passes from the ·Way batch to 2026-10-05T13:49:47Z, four of them the cycle's and the rest hand M1 builds, read 0.34 to 0.65 GB, the highest `ss04`'s in a hand build on 2026-10-04T09:11:28Z, above every range of its record (0.55 GB). The four `run_m1 --gates-only` passes of 2026-10-07 at 44 runes, with the row cache filled and dropped at widths eighteen and twelve, read 0.34 to 0.66 GB, none above its record's highest range. At 45 runes on the same machine, the eleven `run_m1 --gates-only` passes that measure ORACLE_SHARD_BYTES read 0.34 to 0.93 GB. The absorbs read highest on a pass that settles every window fresh, because a statement added to a module of `oracle_cache.ORACLE_ROW_CODE_PATHS` moved both the row stamp and the settle memo's, so every range writes a part of every window it settled and each absorb writes its whole file from them: `default`, `ss03` and `ss05` read 0.78 to 0.79 GB at width eighteen, above every range of their record (0.68 GB, 2026-10-09T04:19:05Z), and `ss03` and `ss03+ss05` 0.92 and 0.93 GB at a stated width twelve, above every range of theirs (0.86 GB, 05:18:03Z). That `ss03+ss05` reading, 0.93 GB, is the highest. The other passes of those eleven read at most 0.91 GB, none above its record's highest range. On the 10-core M1 Pro 32 GiB MacBook Pro the highest reading is 0.76 GB, `ss03+ss05`'s at 2026-09-17T20:07:25Z, during the ·Ye batch, beside `default`'s 0.73 GB, where the record's ranges read at most 0.64 GB. The same machine's absorbs after it read up to 0.69 GB through 2026-09-19, the highest `ss04`'s 0.685 GB at 2026-09-19T04:35:59Z, above every range of its record (0.61 GB). The constant is the highest reading plus a quarter, rounded up to the tenth, as ORACLE_SHARD_BYTES is. The oracle-absorb row of `make job-costs` checks it against the records' `<config> absorb` readings. At this figure an absorb's slot is the size of a range's, ORACLE_SHARD_BYTES, so the absorbs fit in the range slots they run in, nothing comes off the oracle's width for them, and the cores still limit the oracle on every fleet machine.
ORACLE_ABSORB_BYTES = 1_200_000_000
# The peak memory of one conformance-sweep unit, the divisor of gate:conform's width (`conform_job_budget`). A unit is a spawn process running `conform.conformance_config_worker` over one settlement configuration's texts that end in one symbol, or over the ss10 overlay whole (`run_m1.sweep_units`), and the pool runs one unit per process (`max_tasks_per_child=1`). A settlement unit holds its interpreter, a HarfBuzz `Shaper` over M1.otf, the spec's alphabet, splitters, glyph names and anchors, the section 5.7 guard verdict map the parent passes down, a `conform.TEXT_CHUNK` of texts in flight, and its configuration's settle memo, mapped read-only (`conform._MemoStore`) and walked with `promote=False`: the file's label and outcome tables and a dead byte and a reached byte a row on the heap, and the columns and probe index as page-cache pages shared by every unit that maps the file. It also holds `windows`, the dict of windows it settles fresh: empty with the memo current, and every window its texts reach, those that name the edge included, with the memo dropped. The same pool runs each configuration's absorb (`run_m1._absorb_sweep_memo`), which holds the configuration's live rows, the windows its units' parts add, their remapped copies, and the index while it writes the file, so the constant covers the absorb too. Readings are peak footprints (`peak_rss.peak_footprint_bytes`), which leave out the clean pages of the mapped memo file. The page cache holds those once per machine however many units map the file, they are reclaimable, and they come out of the reserve rather than this constant.
# Measured on the 18-core M5 Pro 48 GiB machine (`doc/fleet.md`) with nothing else running, reading footprint, from the `conform-sweep` pool records of five hand `run_m1 --conform-only` passes at width 18 at a2879878's alphabet: three with the memo current (the first of them pruning the rows the full build before it had left), one with every settle memo file removed, and one with rows no text reaches added to each file (`var/keep/483/make_stale.py`). The records finished between 2026-09-28T17:49:58Z and 17:55:26Z; they are copied to `var/keep/483/after/conform-sweep-pool-records.jsonl`, with the runs' logs beside them. A settlement configuration's highest unit reads 0.117 to 0.120 GB with the memo current or stale and 0.134 to 0.136 GB with it removed, and the ss10 overlay reads 0.047 to 0.048 GB. An absorb that only prunes reads 0.166 to 0.168 GB, and one that writes a removed memo back from its units' parts reads 0.406 to 0.414 GB, three times any unit's reading, so that absorb sets the constant; it runs in one of the pool's slots, so the constant covers it. The constant is the highest reading plus a quarter, rounded up to the tenth, because a cost that is too low pushes the machine into swap and the removed-memo absorb grows with the window count. At this figure memory limits no fleet machine's width: a hand run is as wide as the cores, and a cycle's width is `_conform_core_cap`'s. The conform-sweep row of `make job-costs` checks it: `run_m1.run_font_conformance` writes one kind:"pool" record per pooled conformance sweep at `conform.SWEEP_MAX_LENGTH`, one observation per configuration (its highest unit) and one per absorb. The gate:conform step peak is not used, because `peak_rss.reap_peak_rss_bytes` takes the maximum over the tree and so reads one process.
CONFORM_SWEEP_UNIT_BYTES = 600_000_000
CONFORM_MAX_LENGTH_DEFAULT = 4
DEEP_SWEEP_MAX_LENGTH_DEFAULT = 5
# One letter past the build's own replay (`run_m1.REPLAY_MAX_LENGTH`), the depth at which a text first puts a letter in the third lookahead slot behind a letter on the left. The deep replay checks that one extra letter, and `make replay-deep ARGS='--max-length 6'` goes deeper on demand.
DEEP_REPLAY_MAX_LENGTH_DEFAULT = 5
COMPILE_CODE_FILES = (
    "rebuild/pipeline/emit_gsub.py",
    "rebuild/pipeline/emit_gpos.py",
    "rebuild/pipeline/pack_gsub.py",
    "rebuild/pipeline/compile_font.py",
)
RETENTION_WINDOW_DAYS = 7
# How long retention waits for the verdict store's lock before it leaves the stashes or the journal for a later pass. A merge of the whole store holds the lock for seconds, and a server POST for milliseconds.
RETENTION_LOCK_TIMEOUT_S = 60.0


def contracts_green() -> Path:
    """Return the contracts green-record path from `cycle_paths` at call time, so the rebuild conftest's synthetic-root redirect reaches both callers."""
    return cycle_paths.REBUILD_CONTRACTS_GREEN


def contracts_argv() -> list[str]:
    """Return the contracts suite's argv with its selection and sidecar paths beside the green record. `-n auto` uses the cores available to this process or the cycle's explicit worker override; the suite reports its slowest tests in its own log."""
    argv = [
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
    "rebuild/",
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
    "site/gear-menu.js",
    "site/shared.css",
)
MAKE_TEST_EXEMPT_NAME_GLOBS = (("reference", "*.pdf"), ("reference", "*.png"), ("site", "*.svg"))
MAKE_TEST_RECIPES = ("all", "test")
MAKE_TEST_RECIPE_PINS = ("FORCE=",)


def make_test_exempt(path: str) -> bool:
    """Return whether a repo-relative path is outside gate:make-test's input closure. Each exemption follows from what the gate runs: make all, typst, pyright, and pytest test/ site/.

    Nothing the gate runs reads the exempt trees. build_font globs glyph_data/*.yaml non-recursively, so it never reads glyph_data/runes/. test/, site/, tools/ and conftest.py read no rune file, and reach rebuild/ only through the root conftest's imports of rebuild/tools/ leaf modules (`CONFTEST_EDGES` in rebuild/test_contracts_closure.py lists them): peak_rss for the summary line, cycle_timings for the pool record, memory_budget for the pool width, site_fonts for the font-path list, and pyright_gate for the type check's own skip. None of them can change what the suite asserts, and the rebuild suite tests each of them. Markdown is not an input to any gate. .vscode/ is editor configuration, and no import reaches its schema files: tools/quikscript_ir.py names them only in the error messages it raises. .github/ is CI, which runs the suite and is not read by it.

    reference/csur/ and the reference PDFs and PNGs are human reference material that nothing compiles. reference/DepartureMono-Regular.otf stays in the closure, because tools/build_font.py bundles it and test/test_mono_matches_departure_mono.py shapes against it. site/icons/, site/*.svg, site/gear-menu.js and site/shared.css are browser assets the served pages load. The only Python that names them is tools/build_check_html.py, which writes them as string references and which only `make check-html-after` runs. site/shared.js stays, because test/test_shared.py runs it under node, and site/print.typ stays, because `make all` compiles it. The dotfile configs and the OFL text are read by git, the linters and the formatters, not by the suite.

    The name globs match only in their own directory, so site/*.svg does not match in a nested directory and reference/*.pdf does not match in reference/csur/. The Makefile is exempt as a file because the two rules the suite runs are keyed by their `make -n` output instead (`make_test_recipe_lines`), so a comment or a cycle or kernel target does not trigger the gate, while an edit to `all` or `test`, or one that stops the file parsing, still does.
    """
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
    """Return one hash line per Makefile rule the suite runs, in place of the Makefile's bytes. `make -n <target>` prints the recipe with every variable and function expanded, so hashing its output keys the gate on the `all` and `test` rules and nothing else in the file. stderr and the return code are hashed too, so a Makefile that no longer parses changes the key instead of hashing an empty recipe.

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


def make_test_closure_fingerprint(root: Path = ROOT) -> str | None:
    """Return the content hash of gate:make-test's input closure: every file `make_test_exempt` keeps, read from the worktree (not the index) so uncommitted edits count, then the two Makefile rules as `make_test_recipe_lines` hashes them. A deleted tracked file hashes as absent, so deletions change the fingerprint too. None when git or make is unavailable, in which case the caller must run the gate."""
    files = make_test_closure_files(root)
    recipes = make_test_recipe_lines(root)
    if files is None or recipes is None:
        return None
    digest = hashlib.sha256()
    for rel in files:
        digest.update(f"{rel}\t{_sha256_path(root / rel)}\n".encode())
    for line in recipes:
        digest.update(f"{line}\n".encode())
    return digest.hexdigest()


def record_verdict_update_green(fingerprint: str, path: Path | None = None) -> None:
    """Write the verdict update's green record: the key alone, like every record `read_green_record` parses. A pass that needs the fullest verdicts file derives it from disk (`fullest_verdicts_carry_out`), because a later export could outrank a copy remembered here."""
    record_green(path if path is not None else cycle_paths.VERDICT_UPDATE_GREEN, fingerprint)


def fullest_verdicts_carry_out() -> Path | None:
    """Return the stamp-aligned fullest verdicts file, for the summary of a pass that wrote no carry of its own. It is derived from disk the way every consumer derives it (`status.pick_fullest_verdicts`), not remembered in a green record a later export could outrank."""
    from rebuild.review.status import pick_fullest_verdicts

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        return None
    if not isinstance(stamp, str):
        return None
    hit = pick_fullest_verdicts(ROOT, stamp)
    return hit[0] if hit else None


def read_make_test_green(path: Path | None = None) -> dict | None:
    """Return the shared green record for `make test`, which rebuild.tools.make_test_gate writes on every green run, interactive or as gate:make-test."""
    return read_green_record(path if path is not None else cycle_paths.MAKE_TEST_GREEN)


def record_make_test_green(fingerprint: str, path: Path | None = None) -> None:
    record_green(path if path is not None else cycle_paths.MAKE_TEST_GREEN, fingerprint)


def prior_make_test_fingerprint(green_path: Path | None = None) -> str | None:
    """Return the closure fingerprint of the last green `make test` run, from the shared green record only. Every green run rewrites that record and `clear_contradicted_green` deletes it after a red one, so a copy anywhere else (the cycle summary keeps one for display) could bring back a fingerprint whose last run was red."""
    record = read_make_test_green(green_path)
    return record["fingerprint"] if record is not None else None


def make_test_skippable(fingerprint: str | None, recorded: str | None, *, force: bool) -> bool:
    """Return whether `make test` would check nothing: the pass is not forced and the closure's fingerprint equals the one in the shared green record. A fingerprint of None (no git or no make) never skips. The cycle's plan and rebuild.tools.make_test_gate both use this predicate. The plan reserves gate:make-test's cores and memory beside the corpus build only when it returns False, and its argv (`make_test_gate_argv`) passes the wrapper --force only when the plan was forced. So both decisions come from one fingerprint and one record, and no pass reserves cores for a gate that then skips."""
    return not force and fingerprint is not None and fingerprint == recorded


def make_test_gate_argv(*, force: bool) -> list[str]:
    """Return gate:make-test's argv: `make test`, with FORCE=1 on a forced pass (--fresh or --force-make-test). The wrapper reads the green record itself and would otherwise skip on the closure the flag forced. FORCE=1 reaches it as --force and also forces the type check inside it, as `make test FORCE=1` does by hand."""
    return ["make", "test", "FORCE=1"] if force else ["make", "test"]


M1_ARTIFACT_NAMES = ("M1.otf", "divergence-audit.tsv", "inputs_fingerprint.json")
REBUILD_GATE_HARNESS_PATHS = (
    "README.md",
    "doc/glyph-names.md",
    "postscript_glyph_names.yaml",
    "site/extra-senior-words.html",
    "site/index.html",
    "site/the-manual.html",
    "test/test_shaping.py",
    "tools/audit_anchor_geometry.py",
    "tools/build_check_html.py",
    "tools/build_font.py",
    "tools/build_kerning_context_pairs.py",
    "tools/departure_mono_import.py",
    "tools/extract_glyph.py",
    "tools/glyph_compiler.py",
    "tools/inspect_join.py",
    "tools/leak_classify.py",
    "tools/leak_neighbor_filter_report.py",
    "tools/leak_snapshot.py",
    "tools/leak_static_analysis.py",
    "tools/leak_verdict_reconcile.py",
    "tools/quikscript_fea.py",
    "tools/quikscript_ir.py",
    "tools/quikscript_join_analysis.py",
    "tools/reflow_yaml.py",
    "tools/review_scoped_anchor_selectors.py",
    "tools/serve.py",
    "tools/shape_sequences.py",
    "tools/suggest_scoped_anchor_selectors.py",
)


def _closure_digest(root: Path, rel: str) -> str:
    """Return one file's digest for the rebuild lane's closure. Rune YAMLs, the divergence ledger and the standing approvals get prose-insensitive hashes (`fingerprint.rune_file_digest`, `divergence_ledger_digest`, `standing_approvals_digest`), so a documentation edit does not re-run the gate. uv.lock is hashed by its dependency pins (`fingerprint.lock_digest`), so a version bump does not re-run it either while a changed pin does; no test reads the project's own block, and the pinned packages decide the environment the lane runs in. Leaving the prose out is safe because of what the tests read from those files. The contracts tests that load the live runes check structure, settlement outcomes and round-trip identity, and those that load the live ledgers read ids, `no_verdict`, `match` and the exemplar keys, never a `why` or a `note`. The only live readers of those fields are the corpus's explain panel and the standing fill, and both are keyed elsewhere: the ledger's `why` in the Stage B `explain_prose` component, and the fill's copy of a rule's `note` in `verdict_update_skip_fingerprint`, which hashes the file raw for that reason."""
    from rebuild.pipeline import fingerprint

    prose_insensitive = {
        fingerprint.DIVERGENCE_LEDGER_LABEL: fingerprint.divergence_ledger_digest,
        fingerprint.STANDING_APPROVALS_LABEL: fingerprint.standing_approvals_digest,
        "uv.lock": fingerprint.lock_digest,
    }
    digest = prose_insensitive.get(rel)
    if digest is None and rel.startswith("glyph_data/runes/") and rel.endswith(".yaml"):
        digest = fingerprint.rune_file_digest
    if digest is None:
        return _sha256_path(root / rel)
    try:
        return digest(root / rel)
    except OSError:
        return "absent"


def _subset_tables(root: Path) -> list[Path]:
    return sorted((root / "rebuild" / "out" / "m1").glob("baseline-*.subset.tsv.gz"))


def run_m1_skip_lines(root: Path = ROOT) -> list[str]:
    """Return the per-file `label\\tdigest` lines behind `run_m1_skip_fingerprint`: every data input and pipeline module individually (each data input by its prose-insensitive digest, `fingerprint.data_lines`), the contact allow-list by its own prose-insensitive digest, the full baselines as one value, the oracle's subset tables, and uv.lock by its dependency pins (`fingerprint.lock_digest`, which ignores the project's own version block, so a version bump leaves the line unchanged and a fontTools or uharfbuzz bump changes it). The green record stores these lines, so a skip miss can name which input changed, and the pass can check whether every changed label is comparison-side (`comparison_side_label`) and rerun the gates over the artifacts on disk instead of rebuilding them.

    The allow-list is here and in no fingerprint component. Only the defect gate reads it, so a bless must change this key and should not change the corpus's stamp or drop the unit cache. A missing allow-list contributes no line, as `path_lines` drops a missing file.
    """
    from rebuild.pipeline import fingerprint

    lines = fingerprint.data_lines(root)
    allow = root / fingerprint.CONTACT_ALLOW_LABEL
    if allow.is_file():
        lines.append(f"{fingerprint.CONTACT_ALLOW_LABEL}\t{fingerprint.contact_allow_digest(allow)}")
    lines.append(f"baselines\t{fingerprint.baselines_value(root)}")
    lines += fingerprint.path_lines(root, fingerprint.pipeline_code_paths(root))
    lines += [f"{path.name}\t{_sha256_path(path)}" for path in _subset_tables(root)]
    lines.append(f"uv.lock\t{_lock_line_digest(root / 'uv.lock')}")
    return lines


def _lock_line_digest(path: Path) -> str:
    """Return `fingerprint.lock_digest`, or `_sha256_path`'s `absent` for a missing lock, which is what the fake repos without one record."""
    from rebuild.pipeline import fingerprint

    try:
        return fingerprint.lock_digest(path)
    except OSError:
        return "absent"


def _files_of(lines: list[str]) -> dict[str, str]:
    return dict(line.split("\t", 1) for line in lines)


def run_m1_skip_files(root: Path = ROOT) -> dict[str, str]:
    return _files_of(run_m1_skip_lines(root))


def run_m1_skip_fingerprint(root: Path = ROOT) -> str:
    """Return the content key over everything a full run_m1 reads: the data inputs and pipeline code per file, the contact allow-list the defect gate reads, the full baselines, the oracle's subset tables (which the `baselines` line covers only indirectly), and uv.lock's dependency pins. A key equal to the recorded green means a rerun would reproduce rebuild/out/m1 byte for byte. A different key says only that something changed; which lines changed decides whether the pass rebuilds or reruns only the gates (`gates_only_rerun`)."""
    return _digest_lines(run_m1_skip_lines(root))


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
    """Return whether one `run_m1_skip_lines` label names an input the comparison reads and the build does not. When only such inputs changed, a cycle can rerun the gates over the tables and font on disk (`run_m1 --gates-only`) instead of running a kernel fan-out that would produce byte-identical artifacts. Four kinds qualify, and `fingerprint.tables_value` covers none of them, so an enumeration on disk stays current. The alias map, the divergence ledger and the kern sidecar (`fingerprint.NON_TABLE_DATA_LABELS`) are read to name, classify and position divergences over rows the fixpoint has already decided. The contact allow-list (`fingerprint.CONTACT_ALLOW_LABEL`) is read by the defect gate, which reads minted glyphs and mints none. The oracle's two modules (`fingerprint.COMPARISON_CODE_MODULES`) are the classifier those files feed and the position comparison that shapes the rows it calls ink-identical, and rebuild/test_build_code_closure.py checks that the build imports neither. The `baselines` line and the `baseline-<config>.subset.tsv.gz` lines are the before side of the comparison, which no table stage or emitter reads.

    `uv.lock` is not comparison-side, although the tables' stamp does not cover it either. It pins fontTools and uharfbuzz, so a bump can change the compiled font's bytes and what HarfBuzz does with them, and this must never permit reusing a font a different toolchain built. Its line covers only the dependency pins (`fingerprint.lock_digest`), so the project's own version bump leaves the line unchanged and never reaches this question.
    """
    from rebuild.pipeline import fingerprint

    if label in fingerprint.NON_TABLE_DATA_LABELS or label == fingerprint.CONTACT_ALLOW_LABEL:
        return True
    if label == "baselines" or (label.startswith("baseline-") and label.endswith(".subset.tsv.gz")):
        return True
    return label in {f"rebuild/pipeline/{name}" for name in fingerprint.COMPARISON_CODE_MODULES}


def gates_only_rerun(record: dict | None, current: dict[str, str]) -> list[str] | None:
    """Return the labels that changed since the last green M1 build when every one is comparison-side, and None otherwise. The cycle uses this to plan a gates-only rerun, and the pass uses it to decide whether it may record a green. None means no gates-only rerun in three cases: there is no green record, nothing changed (the plain skip's case), or a build-side input changed and the tables must be rebuilt.

    The rerun is sound only with a second check. The prior green shows the artifacts on disk came from a completed build over every build-side input, and `m1_tables_stamped` shows none of those inputs has changed since. Both are checked before the rerun is planned, and a gates-only pass records its green on the same pair.
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


def oracle_cache_note(moved: str | None, root: Path = ROOT) -> str | None:
    """Return what the inputs a run_m1 skip miss named will cost the oracle's per-row verdict store. The store invalidates at four levels, which look different in the timings. A rune file changes one family key and re-derives only the rows that can reach that letter. An input only the whole-store row stamp hashes (the comparison's code outside the position comparison's closure, the crate, the other data inputs) re-derives every row of every configuration and keeps each position whose settled cells did not change. An input only the position stamp hashes (the position comparison's module, the kern sidecar, the toolchain lock) keeps every row verdict and re-shapes every position. Inputs that move both stamps, such as a module in both closures, drop the store whole. An edit confined to the classifier's module, rebuild/pipeline/oracle.py, returns None, as a divergence-ledger edit does: the classifier is in neither stamp and re-runs over served verdicts, so the store costs nothing. The note names the stamp cases because an oracle that serves no rows after a legitimate class-membership or pipeline edit is expected and would otherwise look like a broken cache. Both sides of the comparison are repo-relative labels, the form `moved_inputs_note` reports in; matched against basenames, nothing would match and no error would show. No rune verdict is given when the note was truncated, since the inputs it left out could be anything."""
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
    positions = set(oracle_cache.POSITION_CODE_PATHS)
    positions |= {oracle_cache.TOOLCHAIN_LOCK, "glyph_data/senior_quikscript_kerning.yaml"}
    runes = {label(path) for path in fingerprint.rune_paths(root)}
    names = [entry.rsplit(" (", 1)[0] for entry in moved.split(", ")]
    row_store = sorted({name for name in names if name in stamped})
    position_store = sorted({name for name in names if name in positions})
    if row_store and position_store:
        return f"the oracle row cache drops whole: {', '.join(sorted(set(row_store) | set(position_store)))} is inside its row and position stamps"
    if row_store:
        return f"the oracle row cache re-derives every row and keeps each position whose settled cells did not change: {', '.join(row_store)} is inside its row stamp"
    if position_store:
        return f"the oracle row cache keeps its rows and re-shapes every position: {', '.join(position_store)} is inside its position stamp"
    if not moved.endswith(" more") and names and all(name in runes for name in names):
        return "the oracle row cache re-derives only the rows reaching those runes"
    return None


def m1_artifacts_present(root: Path = ROOT) -> bool:
    """Return whether rebuild/out/m1 still holds everything a skipped run_m1 must leave: the three gate summaries and the artifacts the corpus build reads."""
    m1 = root / "rebuild" / "out" / "m1"
    names = [path.name for path in cycle_paths.M1_SUMMARY_FILES.values()] + list(M1_ARTIFACT_NAMES)
    return all((m1 / name).exists() for name in names)


def m1_tables_stamped() -> bool:
    """Return whether the serialized window enumerations under rebuild/out/m1 were built from the sources on disk: `run_m1.serialized_tables` against `run_m1.tables_inputs`, the stamp the sweep and a gates-only pass check. It checks the artifacts themselves, not a record of a past run, and shows that the M1.otf beside those tables is the font the runes on disk describe. It is the second condition for a gates-only rerun; `gates_only_rerun` is the first. It takes no root parameter, like `deep_sweep.tables_stamped`, because the stamp is computed over the live repo, and a caller naming another tree would compare that tree's tables against this one's sources."""
    from rebuild.pipeline import run_m1

    return run_m1.serialized_tables(run_m1.OUT_DIR, run_m1.tables_inputs()) is not None


def m1_stage_a_current(root: Path = ROOT) -> bool:
    """Return whether the Stage A record under rebuild/out/m1 already matches what a pass over the sources on disk would write. When run_m1 skips it always does. On a gates-only rerun it decides whether the corpus can skip before the pass runs: that pass rewrites the record from the same sources, and the corpus build reads nothing else the pass writes, since the audit and the subset tables change only when a Stage A component does."""
    from rebuild.pipeline import fingerprint

    recorded = fingerprint.read_stage_a(root / "rebuild" / "out" / "m1")
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
    """Return the content key over what the per-edit sweep tests for, matching the deep sweep's key: the deep sweep's lines (`deep_sweep_skip_lines`: the behavior-class set the build enumerated from the emitted lookup, the font-compilation code in `COMPILE_CODE_FILES` and the tools/ closure the compile runs, and the uharfbuzz version) plus the maximum length, so a green at a shorter maximum length cannot satisfy a longer one. A build that left no behavior-class sidecar contributes `CONFORM_NO_SIDECAR_LINE` instead of the classes, a key no sweep records a green under, since every sweep runs over a build that wrote one.

    The key leaves out the rune digests, the tables' stamp and M1.otf's bytes, because a rune edit changes all three on every pass. After the crate's string replay (`run_m1.run_replay_strings`, on every build), what the conformance sweep still checks is how HarfBuzz applies the shapes the emitted lookup gives it, which depends on a rule shape, the code that turns a plan into bytes, and the shaper. An edit that creates no new shape gives the conformance sweep nothing it has not already shaped, so its green stays valid. The class enumeration fails closed (`emit_gsub.behavior_classes`), so a new shape always changes the key. The accepted cost is that a disagreement only HarfBuzz can see waits for the next code change or deep sweep instead of the next rune edit. The set of texts the conformance sweep shapes is unchanged.
    """
    return _digest_lines(conform_skip_lines(root, max_length))


def deep_sweep_skip_lines(root: Path = ROOT) -> list[str] | None:
    """Return the deep sweep's record key lines: the behavior-class set the build enumerated (rebuild/out/m1/behavior_classes.json, written by `emit_gsub.behavior_classes`), the font-compilation code that turns a plan into bytes (the pipeline modules in `COMPILE_CODE_FILES` and the tools/ closure compile_font passes the mini font to, `fingerprint.font_compile_tool_paths`, since an edit to the glyph compiler or the FEA emitter changes M1.otf's bytes and must make this sweep and the per-edit sweep due together), and the shaper version. Both halves of the code are hashed through `fingerprint.path_lines`, so rewording a docstring or a comment in one leaves both sweeps' greens valid. None when no build has left a sidecar, in which case the caller should run the cycle before asking whether the deep sweep is due.

    The key leaves out the rune digests and M1.otf's bytes, because a rune edit changes both on every pass, and the deep sweep tests HarfBuzz behavior at a depth the per-edit sweep cannot reach. It tests the set of shapes the emitted lookup gives the shaper, so an edit that creates no new shape leaves nothing new for a deeper run to find, and its green stays valid. There is no maximum-length line: a deep sweep runs at any maximum length from the per-edit sweep's 4 up (5 by default), so the depth a green covered is stored in the record's payload and compared with >=. Hashing it into the key would make a length-6 green fail a length-5 question.

    Each class is its own line label, not a shared `class` label with the token as its value, so the per-file map behind the key (`_files_of`, stored in the green record) has one entry per token and `moved_inputs_note` can name the new shape, which is all the "due" report says.
    """
    import importlib.metadata

    from rebuild.pipeline import fingerprint
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
    lines += fingerprint.path_lines(root, [root / rel for rel in COMPILE_CODE_FILES])
    lines += fingerprint.path_lines(root, fingerprint.font_compile_tool_paths(root))
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
    """Write the deep replay's green record (`rebuild.tools.deep_replay`) and return the maximum length it records. The record holds every rune's prose-insensitive digest as a walk covered it (under `files`, so `moved_inputs_note` can name what changed since), one maximum length for all of them, the replay structure stamp, the walked tables' imported windows (`imports`, `tables_imports_digest`), and a fingerprint over the rune lines so `read_green_record` reads it like every other record. A rune the record does not have counts as changed.

    `walked` holds the runes whose texts this walk settled at `max_length`, at the digests the walk read. `carry` names runes the walk did not cover whose claims in the record on disk the new record keeps, at the record's digests. A walked rune's depth is `max_length`, raised to the recorded maximum length when its digest, the structure stamp and the imported windows all match the record's; a carried rune's depth is the recorded maximum length. The record stores the least depth over its runes, so it never claims a depth some rune lacks (a length-6 family walk beside runes walked only to 5 records 5), and never drops a depth every rune still has (a length-5 walk or deep sweep over runes the record holds at 6 records 6). The structure stamp enters only here: it never widens a walk (`rebuild.tools.deep_replay` says why), so a structure change keeps the carried runes' claims and makes a walk record its own depth for the runes it covers. The imported windows do widen a walk, because they can reshape a configuration's rules for texts that name no edited rune (`doc/rebuild-design.md` §10, "Imported windows"), so a record whose imported windows differ from `imports` carries no claim. Nothing is carried from a record without a maximum length or a digest map.
    """
    path = path if path is not None else cycle_paths.DEEP_REPLAY_GREEN
    existing = read_green_record(path)
    recorded = recorded_max_length(existing) if existing is not None else None
    prior = existing.get("files") if existing is not None else None
    if not isinstance(recorded, int) or not isinstance(prior, dict):
        recorded, prior = max_length, {}
    same_imports = existing is not None and existing.get("imports") == imports
    if not same_imports:
        prior = {}
    same_structure = same_imports and existing is not None and existing.get("structure") == structure
    carried = {name: prior[name] for name in carry if name in prior and name not in walked}
    depths = [recorded] if carried else []
    depths += [
        max(max_length, recorded) if same_structure and prior.get(name) == digest else max_length
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
    """Return `run_m1.imports_digest` over the windows heads a tree's M1 build left under `rebuild/out/m1`, the windows each configuration's table imported from the others (`rebuild/kernel-rs/src/crossconfig.rs`), or None when some settlement configuration's head is missing or unreadable. The deep replay's record stores it, and a walk whose tables import other windows covers every text (`record_deep_replay_green`). Only the heads are read. The root defaults at call time, as in `deep_replay_green_path`."""
    from rebuild.pipeline import conform, run_m1
    from rebuild.pipeline import table as table_module

    out_dir = Path(ROOT if root is None else root) / "rebuild" / "out" / "m1"
    tables = {}
    for config in conform.SETTLEMENT_CONFIGS:
        try:
            _stamp, tables[config] = table_module.read_windows(
                table_module.windows_path(out_dir, config), windows=False
            )
        except OSError, ValueError:
            return None
    return run_m1.imports_digest(tables)


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
    return Path(root) / "rebuild" / "out" / "deep-replay-green.json"


def _deep_replay_command(max_length: int, every_text: bool = False) -> str:
    """Return the `make replay-deep` invocation that brings the deep replay current at `max_length`: with `--all` when `every_text`, and with `--max-length` when `max_length` is past the walk's default."""
    args = ["--all"] if every_text else []
    if max_length > DEEP_REPLAY_MAX_LENGTH_DEFAULT:
        args.append(f"--max-length {max_length}")
    return f"`make replay-deep ARGS='{' '.join(args)}'`" if args else "`make replay-deep`"


def deep_replay_status(
    root: Path | None = None, max_length: int = DEEP_REPLAY_MAX_LENGTH_DEFAULT
) -> tuple[str, str]:
    """Return whether the deep replay is current for the runes and tables on disk, as (status, note) for the cycle's one-line report beside the deep sweep's. `current` means the record has every rune at its current digest, was walked over the tables' current imported windows (`tables_imports_digest`), and reached this depth or deeper. `due` names what moved since the recorded walk: the imported windows, the runes whose content changed, or the shallower depth it reached, and the `make replay-deep` that clears it at this depth. That is the bare walk at this depth, which walks every text when the imported windows moved or the record is shallower than it, since a walk over the moved runes alone carries the rest at the depth the record holds (`record_deep_replay_green`). `never-run` means there is no record, and names a walk over every text, since a bare walk needs a record to cut its delta against. This only reports: the deep replay is never a cycle gate, for the cost `rebuild/tools/deep_replay.py` states."""
    from rebuild.pipeline import fingerprint

    root = ROOT if root is None else root
    record = read_green_record(deep_replay_green_path(root))
    if record is None:
        return (
            "never-run",
            f"no deep replay has been recorded; run {_deep_replay_command(max_length, every_text=True)} once, overnight",
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
        return "unknown", "no behavior-class sidecar yet; it lands with the next M1 build"
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


def rebuild_gate_closure_files(root: Path) -> list[str] | None:
    """Return every tracked or untracked-unignored repo file the rebuild pytest suite can read, the base of the suite's input closure. That is rebuild/ and glyph_data/ (without Markdown and the paths in `cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES`: the carried-verdict evidence, the JS-only jstests, the review-facts pins, and the contact allow-list), the root conftest.py, pyproject.toml and uv.lock, and the harness list REBUILD_GATE_HARNESS_PATHS. The harness list is what the suite reads outside those trees, and it is why the Markdown filter has an exception: the filter drops prose no test opens, but rebuild/test_review_enrich.py checks the corpus's letter table against doc/glyph-names.md.

    The harness list comes from an audit of every file the suite opens over a green run, not from the tree, and every entry has a named reader. rebuild/validation/pins.py collects its pin runs from the three site corpora and puts test/ on sys.path to import test/test_shaping.py, which reads postscript_glyph_names.yaml at the repo root and imports the tools/ compile modules. rebuild/review/drafts.build_corpus_index reads the same three corpora. The unit-cache environment stamp hashes tools/*.py whole, which is why the list has every tools/*.py file and not only the compile modules. rebuild/test_review_build.py checks the review corpus's feature descriptions against README.md's stylistic-set list.

    The review-facts pins are exempt because the suite does not read them and the review-facts step rewrites them during the pass; including them would change the key of every pass that refreshes them. The allow-list is exempt because no test reads the live file (only a fake repo writes one), so a bless would re-run the whole suite for nothing. The divergence ledger and the standing approvals stay in, since tests read them, and `_closure_digest` gives them prose-insensitive hashes, so rewording a `why` or a `note` does not change the key while a structural edit does. None when git is unavailable, in which case the caller must run the gate.
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
                "rebuild/",
                "glyph_data/",
                "conftest.py",
                "pyproject.toml",
                "uv.lock",
                *REBUILD_GATE_HARNESS_PATHS,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    paths = {entry for entry in result.stdout.split("\0") if entry}
    harness = set(REBUILD_GATE_HARNESS_PATHS)
    return sorted(
        path
        for path in paths
        if (path in harness or not path.endswith(".md"))
        and not any(path.startswith(prefix) for prefix in cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES)
    )


def contracts_fingerprint(root: Path) -> str | None:
    """Return the contracts roster key from `contracts_closure`, whose per-label digests define what the key covers."""
    return contracts_closure(root)[0]


def contracts_closure(root: Path) -> tuple[str | None, dict[str, str] | None]:
    """Return the rebuild suite's input closure as the key and the per-label digest map the key is computed from. One pass over the files produces both, and because the map is the key's only source, every input that can change the key is a label the per-test selection can see change. The closure covers the repo files from `rebuild_gate_closure_files`, which has already dropped the exempt paths, so a contact-signature bless changes nothing here. It also covers the site fonts, hashed as one `fonts` label through `fingerprint.fonts_value` without their `head` and `name` tables. They are `make all` output that the shaping tests measure against, no rune edit changes them, every baseline header records the Senior font's sha, and `baseline_subset.check_font_provenance` checks the oracle's tables against it. Leaving out those two tables means the `make all` a version bump runs changes nothing here, while a glyph, anchor or layout change does. The closure contains no build artifact, so a verdict-only or artifact-only cycle re-runs nothing here, and an M1 rebuild, which writes only under rebuild/out, cannot change the key during a pass. The harness list is included because collecting rebuild/ imports test/test_shaping.py in every process, and that imports the tools/ modules. The rune files, the divergence ledger and the standing approvals get prose-insensitive hashes, because the tests that load the live spec and ledgers read structure, not prose. The verdict store is not in the closure (the suite uses it only through fixtures), so a verdict-only cycle skips the suite. None when git is unavailable, in which case the caller must run the suite."""
    from rebuild.pipeline import fingerprint

    files = rebuild_gate_closure_files(root)
    if files is None:
        return None, None
    labels = {rel: _closure_digest(root, rel) for rel in files}
    labels["fonts"] = fingerprint.fonts_value(root, fingerprint.font_paths(root))
    return _digest_lines([f"{label}\t{digest}" for label, digest in labels.items()]), labels


def corpus_build_skippable(
    root: Path = ROOT, review_out: Path | None = None, ignore: tuple[str, ...] = ()
) -> bool:
    """Return whether rebuilding the review corpus would reproduce its content byte for byte, so the build can be skipped and the autosave stays aligned. True only when the manifest's recorded inputs fingerprint equals the one a build would stamp now (Stage A as run_m1 recorded it, Stage B recomputed) and every shard the manifest names is present. `generated_at` is derived from mtimes, so a rebuild after mtime-only changes (git checkout, touch) could restamp it with identical content. Skipping keeps the existing stamp, and with it the manifest's alignment with the autosave.

    The three files the manifest does not name, the per-unit index and both app sidecars, must each be stamped for the manifest beside them, not merely present. They are written after the manifest and outside it, so a build interrupted between the two, or a manifest rewritten by something that does not rewrite them, leaves every shard present and sidecars that describe a corpus that no longer exists. A skip on shard existence alone would then serve that corpus indefinitely. `unit_index.index_is_current` and `app_index.artifact_is_current` check the stamps, so a skip means a rebuild would reproduce this corpus's content in full.

    The after font is compared with the file on disk because no fingerprint component covers it: the key hashes the font's inputs and the two site fonts, never rebuild/out/m1/M1.otf itself, so a run_m1 that finished after this corpus was built changes nothing the comparison above sees, while the corpus still ships the previous build's font. The build asserts at copy time that the font it ships is the font it hashed at load, so the manifest's after-font sha describes fonts/after.otf, and comparing that sha with the current M1.otf shows the skip is not passing over a newer font.

    `ignore` names fingerprint components left out of the comparison, for a caller asking a narrower question than byte identity, with the same hard/warn split `status._freshness_check` uses. The cycle asks three questions in turn. The strict one comes first, since a corpus that reproduces byte for byte needs nothing done. When only an ASSET_COMPONENTS member differs, the cycle copies those assets over the served corpus and restamps that one component (`assets-refresh`) instead of rebuilding units that cannot have changed. When the live corpus fails both, `promotable_corpus` asks the strict question of a corpus built beside it, and the pass's land swaps that directory in when it matches. A component missing from either side still fails the comparison: only a component present in both the recorded and the expected set can be ignored.
    """
    from rebuild.pipeline import fingerprint

    corpus = review_out if review_out is not None else REVIEW_OUT
    try:
        manifest = json.loads((corpus / "manifest.json").read_text())
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
    ignored = set(ignore) & set(recorded) & set(expected)
    if {key: value for key, value in recorded.items() if key not in ignored} != {
        key: value for key, value in expected.items() if key not in ignored
    }:
        return False
    try:
        shards = [part for meta in manifest["classes"] for part in unit_index.class_shards(meta)]
    except KeyError, TypeError, AttributeError:
        return False
    if not all((corpus / shard).exists() for shard in shards):
        return False
    try:
        after_sha = manifest["fonts"]["after"]["sha256"]
    except KeyError, TypeError:
        return False
    if not isinstance(after_sha, str):
        return False
    if after_sha != _sha256_path(root / "rebuild" / "out" / "m1" / "M1.otf"):
        return False
    return unit_index.index_is_current(corpus) and all(
        app_index.artifact_is_current(corpus, name, fmt) for name, fmt in app_index.ARTIFACTS
    )


def _manifest_stamp_at(corpus: Path) -> str | None:
    try:
        stamp = json.loads((corpus / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError, AttributeError:
        return None
    return stamp if isinstance(stamp, str) else None


def next_corpus_dir(live: Path | None = None) -> Path:
    """Return where a pass builds its corpus beside the served one: `review.next` beside rebuild/out/review. The land swaps it in, so it is on the served corpus's filesystem."""
    live_dir = live if live is not None else REVIEW_OUT
    return live_dir.with_name(f"{live_dir.name}.next")


def corpus_complete(corpus: Path) -> bool:
    """Return whether the corpus at `corpus` is one a build finished: its manifest parses, and its per-unit index and both app sidecars, which a build writes last, are stamped for that manifest (the checks `corpus_build_skippable` makes, without the comparison with today's inputs)."""
    try:
        manifest = json.loads((corpus / "manifest.json").read_text())
    except OSError, ValueError:
        return False
    if not isinstance(manifest, dict):
        return False
    return unit_index.index_is_current(corpus) and all(
        app_index.artifact_is_current(corpus, name, fmt) for name, fmt in app_index.ARTIFACTS
    )


def recover_next_corpus(live: Path | None = None, *, delete: bool = True) -> str | None:
    """Keep a `review.next` an earlier pass left only when it is complete (`corpus_complete`), and delete anything else there: a build that died leaves a partial tree, which must not seed or be promoted. A complete one stays, to be promoted when it reproduces this pass's inputs (`promotable_corpus`) or to seed the build when a rune moved since it was built, so the units it already built are cache hits. The `.discard` trees a land's delete did not finish (`landing.discard_paths`) are deleted too. `delete=False` is the dry run's form, which deletes nothing. Returns the line to print, or None when there is no `review.next`."""
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
    """Return the corpus built beside the live one that a live pass can land instead of rebuilding, or None. Candidates are checked in order: `review.next` beside the live corpus (`next_corpus_dir`), which a pass stopped after its build leaves complete; the `plan.review_out` the last cycle summary recorded (a repo-relative string, resolved against `root`), which a staging pass (`--review-out`) wrote as a whole corpus (shards, sidecars, unit store and signature store) where the live pass never reads; then `var/staged-review` under `root`, the conventional staging directory. Every staging path derives from `root`, so a scratch repo never reads the live staged corpus. The caller asks only on a pass whose run_m1 skipped or reproduced its Stage A, because the check compares against the Stage A run_m1 recorded on disk, so a corpus built before a rune edit is never landed over it.

    A candidate is promotable when it is a directory other than the live one, on the live directory's filesystem (the land swaps the two trees, which cannot cross filesystems, and a plan must never print a move it cannot make), when `corpus_build_skippable` returns True for it (that function defines "reproduces these inputs byte for byte", including the after font and the three stamped sidecars), and when its `generated_at` is not older than the live corpus's. The byte-identity check cannot supply the stamp condition: `generated_at` is the latest input mtime, not a build time (`_generated_at` in rebuild/review/build.py), so a staged corpus can have a stamp older than the corpus it would replace, and merge_verdicts refuses a store stamped newer than the corpus it merges onto. A backwards promotion would fail the verdict update. An unreadable manifest rules the candidate out, which costs only a rebuild.
    """
    live_dir = live if live is not None else REVIEW_OUT
    summary = summary_path if summary_path is not None else root / "rebuild" / "out" / "cycle_summary.json"
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


# The verdict update's code, listed by module instead of all of rebuild/tools/: the import closure of rebuild.tools.verdict_update, which runs every step. rebuild/test_verdict_update_closure.py checks the list against the walked import graph on every contracts run. This driver is not an entry point, because every argument it passes the verdict update names an input the key already hashes (the corpus, the master, the store), a flag that disables the skip, or a width (`--standing-fill-jobs`) that cannot change the verdict update's output, and the verdict update parses its own flags in verdict_update. The walk stops at the modules in `fingerprint.pipeline_code_paths`, because the key includes the pipeline_code component whole through its manifest line. The rebuild/tools/ modules the pipeline imports (memory_budget, peak_rss, lock_digest, site_fonts and others) are outside this list, which keeps fan-out widths and peak-memory measurements out of the verdict key.
VERDICT_UPDATE_ENTRY_POINTS = ("rebuild.tools.verdict_update",)
VERDICT_UPDATE_TOOL_MODULES = (
    "carry_verdicts",
    "complaint_list",
    "console",
    "duplicate_verdicts",
    "merge_verdicts",
    "review_queue",
    "review_server",
    "standing_client",
    "standing_verdicts",
    "verdict_update",
    "verdict_notes",
)


def verdict_update_code_paths(root: Path = ROOT) -> list[Path]:
    return [Path(root) / "rebuild" / "tools" / f"{name}.py" for name in VERDICT_UPDATE_TOOL_MODULES]


def verdicts_records_digest(path: Path) -> str:
    """Return a digest of a verdicts document's content: every top-level field but `exported_at`, and the records sorted by their canonical JSON. The review server rewrites the store with a new `exported_at` on every save, and the merge and the server lay records out differently, so neither a save that changes no record nor a rewrite in another layout moves it. A file that is not a JSON object with a `verdicts` list is hashed by its bytes, and one that cannot be read is "absent", as `_sha256_path` reports it. The parse holds the whole store in memory once, in the cycle's own process. `verdict_update_skip_fingerprint` parses the store once per call, and the cycle calls it at plan time, before any gate or pool starts, and after the verdict update exits, inside the memory the build lane reserved for that step (`_conform_build_lane`)."""
    try:
        with open(path, "rb") as handle:
            document = json.load(handle)
    except OSError:
        return "absent"
    except ValueError:
        return _sha256_path(path)
    if not isinstance(document, dict) or not isinstance(document.get("verdicts"), list):
        return _sha256_path(path)
    records = document.pop("verdicts")
    document.pop("exported_at", None)
    digest = hashlib.sha256()
    digest.update(json.dumps(document, sort_keys=True, ensure_ascii=False).encode() + b"\n")
    for line in sorted(json.dumps(record, sort_keys=True, ensure_ascii=False) for record in records):
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()


def _master_key_line(root: Path, master: Path, autosave_digest: str) -> str:
    """Return the verdict-update key's line for its master. A master that is the live store is named `autosave` and carries the store's records digest (`autosave_digest`), as the store's own line does, so the key does not depend on how the path was spelled or on a save that changed no record. Any other master is named by its path and hashed by its bytes."""
    if Path(master).resolve() == (root / "verdicts-autosave.json").resolve():
        return f"master\tautosave\t{autosave_digest}"
    return f"master\t{master}\t{_sha256_path(Path(master))}"


def verdict_update_skip_fingerprint(
    root: Path = ROOT, corpus: Path | None = None, master: Path | None = None, store: Path | None = None
) -> str | None:
    """Return the content key over everything the verdict update reads: the corpus it resolves unit ids against, the verdicts master it carries forward, the live store it merges into, the checked-in standing approvals, and the verdict update's own code. The standing approvals are hashed by raw bytes, unlike the prose-insensitive hash the rebuild lane uses: `standing_verdicts` copies each rule's `note` into the verdict note of every fill it writes, so rewording a note changes the verdict update's output and must re-run it. Carry, merge, both fills with their merges, and the complaint list are pure functions of these inputs, and the verdict update is idempotent once it has run, so a key matching the record a complete verdict update left means re-running it would write nothing new. The master is in the key because the autosave's hash cannot see it: an export at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The live store is hashed by its records (`verdicts_records_digest`), not its bytes, because the review server rewrites the file with a new `exported_at` on every save, and when the master resolved to the store, the master line names it `autosave` instead of by its path (`_master_key_line`). The code is in the key for the same reason every other key includes its stage's code: a fix to a fill's matcher or to the carry's join must run, not be skipped. It is the verdict update's import closure (`verdict_update_code_paths`, which a contracts test checks against the verdict update's import graph) plus the review/ modules the verdict update runs and the corpus build does not: serve.py and verdict_store.py, through which merge_verdicts reads the store, status.py and journal.py, which the merge and the readiness check run, store_lock.py, which the merge holds around its write, and landing.py, which puts the store the verdict update prepared in place. review/'s build-side modules are covered by the manifest fingerprint's review_code. The manifest line leaves out `unit_index.ASSET_COMPONENTS`, because no step of the verdict update reads the copied app assets, and an assets refresh rewrites that field and must not re-run a verdict update whose real inputs are unchanged. `store` is the store whose records stand for the live store, the live store when None: after a pass the cycle keys the record on the store its land wrote (`landing.LANDED_NAME`), so the next plan, which reads the live store, finds the same key only when no save has changed the store since. None when the corpus has no fingerprinted manifest or no master was resolved."""
    if master is None:
        return None
    corpus_dir = corpus if corpus is not None else REVIEW_OUT
    try:
        manifest = json.loads((corpus_dir / "manifest.json").read_text())
    except OSError, ValueError:
        return None
    fp = manifest.get("inputs_fingerprint")
    if not isinstance(fp, dict):
        return None
    from rebuild.pipeline import fingerprint

    autosave_digest = verdicts_records_digest(store if store is not None else root / "verdicts-autosave.json")
    lines = [
        "manifest\t"
        + json.dumps(
            {key: value for key, value in fp.items() if key not in unit_index.ASSET_COMPONENTS},
            sort_keys=True,
        ),
        f"generated_at\t{manifest.get('generated_at')}",
        _master_key_line(root, master, autosave_digest),
        f"autosave\t{autosave_digest}",
        f"standing\t{_sha256_path(root / 'rebuild' / 'standing-approvals.yaml')}",
        f"tools_code\t{fingerprint.hash_paths(root, verdict_update_code_paths(root))}",
        f"serve\t{_sha256_path(root / 'rebuild' / 'review' / 'serve.py')}",
        f"verdict_store\t{_sha256_path(root / 'rebuild' / 'review' / 'verdict_store.py')}",
        f"status\t{_sha256_path(root / 'rebuild' / 'review' / 'status.py')}",
        f"journal\t{_sha256_path(root / 'rebuild' / 'review' / 'journal.py')}",
        f"store_lock\t{_sha256_path(root / 'rebuild' / 'review' / 'store_lock.py')}",
        f"landing\t{_sha256_path(root / 'rebuild' / 'review' / 'landing.py')}",
    ]
    return _digest_lines(lines)


def evaluate_run_m1_gate(pipeline: dict, manual_pins: dict, oracle: dict) -> CheckResult:
    """Decide whether the M1 build passed from its three summary JSONs: defect_errors, the Manual-pin gate, and multi_matched. UNMATCHED oracle rows are never a failure; they are expected during the migration and are judged on the review corpus. The result carries no UNMATCHED or multi_matched counts, because both callers already hold the oracle summary. The pin gate is run_m1's own (`manual_pin_gate_failure`), including its scope, so a gate that replayed nothing cannot pass here either."""
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

    return CheckResult(
        check="run_m1",
        outcome="red" if failures else "green",
        status="FAILED" if failures else "green",
        failures=failures,
        failed_ids=[],
    )


def evaluate_conform_gate(summary: dict | None) -> CheckResult:
    """Evaluate gate:conform from conform_summary.json's contents (None when the subprocess wrote none). `pass` is the outcome, and the conformance sweep fails in only one way: a font-versus-settlement divergence, which is a compiler defect. Whether the font holds every rule the build planned is checked by read-back inside run_m1 on every build, and dead generated rules by the build's witness stage (`run_m1.run_rule_witnesses`, over the certificates the crate writes beside the rules); neither reaches this summary. The sweep reports a count of divergences, not named cases, so there are no failed ids: a divergence names a window, and the audit beside the summary lists them."""
    if summary is None:
        return CheckResult(
            check="conform",
            outcome="red",
            status="FAILED (no conform_summary.json)",
            failures=["conform gate: run_m1 --conform-only wrote no summary"],
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
    argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--conform-only", "--jobs", str(jobs)]
    if max_length != CONFORM_MAX_LENGTH_DEFAULT:
        argv += ["--conform-max-length", str(max_length)]
    return argv


STEP_DESCRIPTIONS = {
    "run_m1": "Builds the M1 tables for every settlement configuration in the Rust kernel (the ss10 overlay settles nothing and gets none), mints the glyphs, emits GSUB and GPOS, compiles the font, and reads it back. Then runs the defect gates, the Manual-pin gate, and the oracle over what it built.",
    "run_m1:gates-only": "Reruns the defect gates, the Manual-pin gate, and the oracle over the tables and font already on disk, rebuilding nothing. Taken when only comparison-side inputs moved since the last green build.",
    "corpus-seed": "Clones the served corpus to rebuild/out/review.next, copy-on-write, so the build beside it starts from the served corpus's shards and stores without writing a byte the open tab reads. A complete review.next a stopped pass left is kept as the seed instead.",
    "corpus-build": "Rebuilds the review corpus: every unit the tables reach is drafted, enriched, and checked, with cached units re-verified by content key. Writes the shards, manifest, and review-facts sidecar that the app and the verdict update read, into rebuild/out/review.next beside the served corpus (in place only on a first run).",
    "assets-refresh": "Overwrites the served copy of the review app's JS, CSS, and HTML and restamps only the manifest's static component. No shard or sidecar moves, so the open tab's store stays aligned.",
    "store-snapshot": "Copies the live verdict store into this pass's scratch directory under the store's lock, as the snapshot the land compares against and the store the verdict update prepares. Takes milliseconds; the review server keeps saving into the live store.",
    "verdict-update": "Carries the verdicts master onto the new corpus by unit id, merges it into a scratch copy of the store, and runs duplicate-fill, standing-fill, and duplicate-fill again, merging each fill. Ends by writing the complaint list of what still needs a human.",
    "land": "Moves the new corpus and the prepared store into place together, under the verdict store's lock, in one child that a stop signal does not interrupt: verdicts saved during the pass are laid over the prepared store, the corpus is swapped in with one rename, and the change is journaled. The open tabs then move onto it.",
    "review-facts": "Rewrites rebuild/review-facts-pins.json from the review-facts sidecar the corpus build emitted, names what moved in its invariant block against the last accepted review facts (diffing that block alone when it did), and holds the ledger's declarations against the classes the corpus reached. Committing the rewritten pins is how the review facts are accepted.",
    "gates": "The four post-build gates, skipped together under --skip-gates.",
    "gate:js": "Runs the review app's node test suite over its JavaScript. Fast, and independent of every build artifact.",
    "gate:conform": "Shapes the compiled font with HarfBuzz over the swept texts and checks it against a fresh re-settlement window by window, the split-buffer check at maximum length 4 included; the ss10 overlay runs its own two-letter case against the bare rendering. The check that HarfBuzz does what the tables say over every rule shape the lookup emits; that the tables are complete over the same texts is run_m1's string replay, on every build.",
    "gate:rebuild-contracts": "Runs the rebuild suite: every test whose subject is the code, over checked-in fixtures and the hermetic mini bundle. Reads no live build artifact.",
    "gate:make-test": "Runs the main font suite and pyright over the whole tree, the same make test you run by hand. Skips when its input closure is unchanged since its last green run.",
    "job-costs": "Checks the recorded per-worker peaks against the memory-budget constants that size every fan-out. A drift here means a width somewhere is derived from stale numbers.",
    "retention": "Prunes the regenerable files a green cycle leaves behind: stale carried files, stashes the journal already replays, the journal past its 7-day floor, and build logs beyond the last 10.",
}


def step_description(name: str) -> str:
    """Return a step's banner description from `STEP_DESCRIPTIONS`, printed on every run. The descriptions are kept beside the step definitions, not in a document, because a reader wants to know what a step checks while watching it run. Two keys are variants, not steps: `run_m1:gates-only` is the gates-only rerun, which spawns under that name and reports under run_m1's row, and `gates` is the placeholder --skip-gates leaves in place of the four gates. `_run_step` calls this instead of reading the plan because it receives a name, not a plan, and because one of the things it spawns, the job-costs diff, is a child of a step, for which the empty string is correct."""
    return STEP_DESCRIPTIONS.get(name, "")


SUBSTEP_PARENTS = {"invariant-diff": "review-facts", "job-costs-diff": "job-costs"}


@dataclass
class Step:
    """One row of the plan. `skipped` is set explicitly instead of derived from `argv`, because `argv is None` also describes the corpus-seed, store-snapshot and retention steps, which do real work in this process, and the `gates` placeholder, which stands for four steps. The run/skip column and the counts line use `skipped`, so a step that runs without spawning anything still shows as running.

    The review-facts step's invariant diff, which the driver prints itself, and the job-costs diff it spawns are children of steps, not rows of the plan, and SUBSTEP_PARENTS names them. Registering one with the console writes its output to the parent's log, shows its lines under the parent's column, and keeps `_run_step` from opening a second banner for a step that is already open.
    """

    name: str
    argv: list[str] | None
    note: str = ""
    lane: str = ""
    describe: str = ""
    skipped: bool = False


@dataclass
class Plan:
    """The resolved pass. `corpus_dir` is the corpus the pass serves when it ends (the staging directory on a staging pass), `update_corpus` the one the verdict update reads, and `next_corpus` the tree the land swaps in for the served one (`review.next` on a building pass, the staged directory on a promotion, None when the corpus does not move). `land` says whether the pass lands a corpus or a store (`rebuild.review.landing`), with its scratch files under `scratch_dir`, and `broadcast` whether it tells the listening review server's tabs what moved, which it does not for a server serving another checkout. `legacy_server` says the listening server predates the land protocol, so it writes the store and the journal without the store's lock and retention leaves both alone while it listens."""

    short_id: str
    first_run: bool
    carry_out: Path | None
    verdicts: Path | None
    skip_gates: bool
    do_merge: bool = False
    skip_conform: bool = False
    skip_make_test: bool = False
    make_test_note: str = ""
    make_test_fingerprint: str | None = None
    skip_run_m1: bool = False
    rerun_gates_only: bool = False
    run_m1_note: str = ""
    run_m1_fingerprint: str | None = None
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
    skip_verdict_update: bool = False
    verdict_update_note: str = ""
    verdict_update_direct_merge: bool = False
    record_greens: bool = False
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT
    corpus_jobs: int = 1
    corpus_reason: str = ""
    corpus_gates_idle_jobs: int | None = None
    corpus_gates_idle_reason: str = ""
    gates_idle_marker: Path | None = None
    signature_jobs: int = 1
    signature_reason: str = ""
    standing_fill_jobs: int = 1
    standing_fill_reason: str = ""
    sweep_jobs: int = 1
    sweep_reason: str = ""
    kernel_threads: int = 1
    kernel_reason: str = ""
    overlap_memo_writes: bool = False
    scratch_beside_default: int = 0
    replay_threads: int = 1
    replay_reason: str = ""
    make_test_workers: int = 1
    contracts_workers: int = 1
    contracts_reason: str = ""
    conform_jobs: int = 1
    conform_reason: str = ""
    conform_max_length: int = CONFORM_MAX_LENGTH_DEFAULT
    review_out: Path | None = None
    corpus_dir: Path = REVIEW_OUT
    update_corpus: Path = REVIEW_OUT
    next_corpus: Path | None = None
    seed_kept: bool = False
    land: bool = False
    broadcast: bool = True
    legacy_server: bool = False
    complaints_note: str = ""
    retention: bool = False
    recipe_serves: bool = False
    stamp: str = ""
    log_dir: Path | None = None
    scratch_dir: Path | None = None
    steps: list[Step] = field(default_factory=list)

    def step(self, name: str) -> Step | None:
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def describe(self, name: str) -> str:
        """Return the named step's banner text, for the steps that run in this process and never reach `_run_step` (corpus-seed, store-snapshot and retention)."""
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
    """Return the root conftest.py's FONT_SUITE_WORKER_BYTES, the peak memory of one gate:make-test pytest worker, parsed from that file's source with `ast` instead of imported. pytest loads every conftest under the module name `conftest`, so in a run collected under rebuild/ (every run that tests this module) `import conftest` gets rebuild/conftest.py, and `import rebuild.conftest` would execute a second copy of a conftest pytest has already loaded, with its lane-audit hook. Parsing also keeps this build tool from importing pytest or inheriting that file's sys.path edits. A renamed constant raises here instead of silently dropping the memory the cycle sets aside for that pool. The path is resolved from this file, not from ROOT, because a test that points the cycle at an invented root changes where artifacts go, not which test suite the gate runs."""
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
    """Return the width of gate:make-test's pytest pool under a cycle. The cycle passes this number to the child and reserves memory for it, so both use one value. Without it the pool would be `-n auto`, which the root conftest.py resolves to every core, while the builds beside it are sized to leave room for it. On a pass whose build lane runs (run_m1 or the corpus build), the width is the gate lane's share of `memory_budget.usable_cores()`, the count the root hook uses, as `memory_budget.split_cores` divides it: the corpus build's cap is the build lane's share (`_corpus_fit_terms`), so the two lanes partition the cores. On a pass that runs neither, the pool takes every core, and the widths that subtract it (the rebuild suite's and the conformance sweep's cap under the overlap policy, and the standing fill's memory term) subtract that whole width, which is why each of them takes `build_lane_runs` too. PYTEST_XDIST_AUTO_NUM_WORKERS overrides both, because the child inherits this environment and will run at that width. It is parsed as the root hook parses it, so an unparseable value raises here, while the plan resolves, instead of inside the gate after the rest of the cycle has run."""
    from rebuild.tools import memory_budget

    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return max(1, int(stated))
    cores = ncores or memory_budget.usable_cores()
    return memory_budget.split_cores(cores)[1] if build_lane_runs else cores


def _make_test_pool_bytes(*, skip_make_test: bool, ncores: int | None) -> float:
    """Return the memory gate:make-test's pytest pool holds beside the run_m1 step: FONT_SUITE_WORKER_BYTES times `make_test_pool_width` at the gate lane's share of the cores, the width the pool runs at while run_m1 holds the build lane, or zero when the gate is skipped. The table build's width and the string replay's both subtract it."""
    return 0 if skip_make_test else _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)


def _wave_fit_terms(*, skip_make_test: bool, ncores: int | None) -> tuple[float, int]:
    """Return the co-resident bytes and the width cap that `kernel_threads_budget`, `memo_writes_overlap_budget`, `scratch_beside_default_budget`, `replay_threads_budget` and `replay_threads_derivation` share. The co-resident term is gate:make-test's pytest pool (`_make_test_pool_bytes`) only: `kernel_exec.kernel_threads_default` adds `default`'s retained memo and the parked fold products itself, and the replay builds its engines after the table build's process has exited. The cap is the smaller of the configuration count and the usable cores, the same bounds `run_m1._table_build_threads` and `run_m1._replay_threads` apply."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.tools import memory_budget

    coresident = _make_test_pool_bytes(skip_make_test=skip_make_test, ncores=ncores)
    return coresident, min(len(SETTLEMENT_CONFIGS), ncores or memory_budget.usable_cores())


def kernel_threads_budget(
    *, skip_make_test: bool = False, ncores: int | None = None, total_bytes: int | None = None
) -> int:
    """Return the kernel fan-out's width for this cycle, the `--kernel-threads` the plan passes run_m1. Memory limits this width because a live delta holds its whole working set until it emits, and every configuration's prepared fold product stays parked until the cross-configuration exchange ends. `kernel_exec.kernel_threads_default` does the arithmetic over the settlement configurations: the widest width whose booking (`kernel_exec.table_build_booking_bytes`: the memo snapshots `default` keeps alive for the wave, one parked product per configuration, what each delta in flight adds beyond its own, and `default`'s fold preparation in a slot of its own once every delta has one) fits the machine's memory less the reserve. On the fleet (`doc/fleet.md`) this gives the whole wave, every configuration at once, on both 48 GiB machines with or without gate:make-test's pool subtracted. The cycle subtracts that pool too, because it runs from t=0 through the whole table build: FONT_SUITE_WORKER_BYTES for each of the `make_test_pool_width` workers the cycle passes that child, the gate lane's share of the cores (`memory_budget.split_cores`), since run_m1 holds the build lane. A pass that skips the gate, including --skip-gates, subtracts nothing. AMS_KERNEL_THREADS overrides the arithmetic, as it does for a bare run_m1, so the memory set aside for the pytest pool never narrows a stated width.

    The result is capped at the configuration count and the usable cores, as in `replay_threads_budget`. The memory result never exceeds the configuration count, but a stated AMS_KERNEL_THREADS can, and a machine can have fewer cores than configurations, so without the cap the plan line could name a width the build never takes. With it, the plan line, the argv and `cycle_summary.json`'s `plan.kernel_threads` show the real width, and `run_m1._table_build_threads`'s own `min()` changes nothing. A stated AMS_KERNEL_THREADS above the configuration count is cut to it here, as `run_m1._table_build_threads` would cut it. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import kernel_threads_default

    coresident, cap = _wave_fit_terms(skip_make_test=skip_make_test, ncores=ncores)
    derived = kernel_threads_default(
        configs=len(SETTLEMENT_CONFIGS), coresident_bytes=coresident, total_bytes=total_bytes
    )
    return max(1, min(derived, cap))


def memo_writes_overlap_budget(
    kernel_threads: int,
    *,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> bool:
    """Return whether this cycle's table build writes its memo files beside the delta wave and the folds, the `--overlap-memo-writes` or `--no-overlap-memo-writes` the plan passes run_m1: `kernel_exec.memo_writes_overlap` at `kernel_threads`, the width `kernel_threads_budget` gave, with gate:make-test's pytest pool off the machine as that width has it (`_wave_fit_terms`). run_m1 cannot derive this itself under a cycle, because it does not know that pool is running beside it. `ncores` and `total_bytes` are keywords so a test can compute the choice for an invented machine."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import memo_writes_overlap

    coresident, _cap = _wave_fit_terms(skip_make_test=skip_make_test, ncores=ncores)
    return memo_writes_overlap(
        kernel_threads, configs=len(SETTLEMENT_CONFIGS), coresident_bytes=coresident, total_bytes=total_bytes
    )


def scratch_beside_default_budget(
    kernel_threads: int,
    *,
    overlap_memo_writes: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return at most how many of the heaviest deltas this cycle's table build enumerates from scratch beside `default`, the `--scratch-beside-default` the plan passes run_m1, which the crate takes in whole tiers of equal unlocking-rune count: `kernel_exec.deltas_from_scratch` at `kernel_threads` and `overlap_memo_writes`, the width and memo-write order `kernel_threads_budget` and `memo_writes_overlap_budget` gave, with gate:make-test's pytest pool off the machine as they have it (`_wave_fit_terms`). run_m1 cannot derive this itself under a cycle, for the reason `memo_writes_overlap_budget` gives. `ncores` and `total_bytes` are keywords so a test can compute the count for an invented machine."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import deltas_from_scratch

    coresident, _cap = _wave_fit_terms(skip_make_test=skip_make_test, ncores=ncores)
    return deltas_from_scratch(
        kernel_threads,
        configs=len(SETTLEMENT_CONFIGS),
        overlap=overlap_memo_writes,
        coresident_bytes=coresident,
        total_bytes=total_bytes,
    )


def replay_threads_budget(
    *, skip_make_test: bool = False, ncores: int | None = None, total_bytes: int | None = None
) -> int:
    """Return the string replay's width for this cycle, the `--replay-threads` the plan passes run_m1: memory, less the reserve and gate:make-test's pytest pool, divided by `kernel_exec.REPLAY_PEAK_BYTES` (one configuration's length-4 walk). The pool is the one `kernel_threads_budget` subtracts, because it runs for the whole run_m1 step and the replay runs inside that step; a pass that skips the gate subtracts nothing. `kernel_exec.replay_threads_default` does the arithmetic, and `AMS_REPLAY_THREADS` overrides it, as it does for a bare run_m1.

    The result is capped at the configuration count and the usable cores, as in `kernel_threads_budget`. On every fleet machine the memory result exceeds the configuration count with or without the pool subtracted, so the subtraction matters only on a smaller machine, and without the cap the plan line would name a width the run never takes. With it, the plan line and the argv show the real width, and `run_m1._replay_threads`'s own `min()` changes nothing. A stated AMS_REPLAY_THREADS above the configuration count is cut to it here, as the crate and `run_m1._replay_threads` would cut it. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from rebuild.pipeline.kernel_exec import replay_threads_default

    coresident, cap = _wave_fit_terms(skip_make_test=skip_make_test, ncores=ncores)
    return max(1, min(replay_threads_default(coresident_bytes=coresident, total_bytes=total_bytes), cap))


def replay_threads_derivation(
    *, skip_make_test: bool = False, ncores: int | None = None, total_bytes: int | None = None
) -> str:
    """Return `replay_threads_budget`'s width as a clause for the plan line, computed from the same terms so the clause always explains the printed width. It gives the number of waves the width makes of the configuration count, then either the AMS_REPLAY_THREADS value the width came from (with the cap or the floor of one, when either changed it) or `memory_budget.describe_fit` over the budget's terms and cap. A stated width is described as stated, because arithmetic that gives a different number would contradict the width printed beside it."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.pipeline.kernel_exec import REPLAY_PEAK_BYTES
    from rebuild.tools import memory_budget

    coresident, cap = _wave_fit_terms(skip_make_test=skip_make_test, ncores=ncores)
    width = replay_threads_budget(skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes)
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
    fit = memory_budget.describe_fit(
        REPLAY_PEAK_BYTES, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes
    )
    return f"{waves}; {fit}"


def _oracle_absorb_excess_bytes() -> int:
    """Return what the oracle's settle-memo absorbs hold beyond the range slots they run in: one absorb per settlement configuration at once, each at ORACLE_ABSORB_BYTES in place of ORACLE_SHARD_BYTES, and nothing when an absorb fits in a range's slot."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS

    return len(SETTLEMENT_CONFIGS) * max(0, ORACLE_ABSORB_BYTES - ORACLE_SHARD_BYTES)


def sweep_job_budget(ncores: int | None = None, total_bytes: int | None = None) -> int:
    """Return the `--jobs` width for run_m1's oracle, whose unit is a row range of one configuration's table: memory, less the reserve and the settle-memo absorbs' excess over the range slots they run in (`_oracle_absorb_excess_bytes`), divided by one range worker's peak (ORACLE_SHARD_BYTES), capped at the usable cores. gate:conform's sweep has its own budget, `conform_job_budget`, because its unit holds different data and runs beside the corpus build. Nothing is subtracted for gate:make-test's pytest pool, which can still be running when the oracle starts on a non-staging pass: at the gate lane's share of the cores that pool fits inside the reserve on every fleet machine, and reserving for it would narrow this phase on every pass for the length of the overlap. Its cores are not subtracted either: the oracle is a burst on the build lane's critical path, and the two pools share the cores while both run, which costs contention time rather than memory. Nothing is subtracted for run_m1's table-only branch either, whose witness stage and shipped-order walks can still be running when the pool starts (`doc/parallelism.md` describes this overlap), because what they hold is far below the reserve. run_m1's peak memory is in the table build, whose width is --kernel-threads, and these jobs never reach it. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    return memory_budget.how_many_fit(
        ORACLE_SHARD_BYTES, coresident_bytes=_oracle_absorb_excess_bytes(), cap=cores, total_bytes=total_bytes
    )


def sweep_job_derivation(ncores: int | None = None, total_bytes: int | None = None) -> str:
    """Return `sweep_job_budget`'s width as a clause for the plan line: `memory_budget.describe_fit` over the same terms."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    return memory_budget.describe_fit(
        ORACLE_SHARD_BYTES, coresident_bytes=_oracle_absorb_excess_bytes(), cap=cores, total_bytes=total_bytes
    )


def _corpus_fit_terms(*, skip_gates: bool, skip_make_test: bool, ncores: int | None) -> tuple[int, int, int]:
    """Return the three arguments of the corpus build's width: the per-worker divisor, the co-resident bytes subtracted before the division, and the non-memory cap. `corpus_job_budget` passes them to `how_many_fit` and `corpus_job_derivation` to `describe_fit`, so the width and its explanation come from one derivation. The cap is the cores the build's lane holds, less one for its parent: every usable core under `skip_gates` (a hand build or `--skip-gates`), and the build lane's share (`memory_budget.split_cores`) on any gated pass, whether or not gate:make-test runs, because gate:rebuild-contracts, and gate:conform when it runs, take the gate lane beside the build. A gated build's units pool takes the `skip_gates` terms instead when the gate lane is idle as the pool starts (`corpus_gates_idle_job_budget`). The co-resident bytes are the parent's, plus gate:make-test's pool when that gate runs."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    lane = cores if skip_gates else memory_budget.split_cores(cores)[0]
    coresident = CORPUS_PARENT_BYTES
    if not (skip_gates or skip_make_test):
        coresident += _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)
    return CORPUS_WORKER_BYTES, coresident, max(1, lane - 1)


def corpus_job_budget(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the review-corpus build's `--jobs` width: memory, less the reserve, less what the build's parent holds, divided by one worker's peak, capped at the cores the build's lane holds less the parent (`_corpus_fit_terms`), and floored at one. The parent and the workers are separate constants because the parent's share is about as large as the pool's. CORPUS_PARENT_BYTES is the parent, which holds the workload table (`audit.UnitTable`) and the packed unit store (rebuild/review/unit_store.py) at any width, so it is subtracted before the division, as gate:make-test's pool is. CORPUS_WORKER_BYTES is the divisor. A worker's peak does not grow with the width, because the parent hands out one batch at a time, or with the alphabet, because the tables sit in the baseline subset pack it maps read-only (rebuild/review/subset_pack.py). The two constants' comments have the measurements. The ink-signature pool that `_resolve_signature_digests` starts before the units phase is not counted: each of its workers holds one comparator, about a tenth of a gigabyte, and the pool runs at `signature_job_budget`'s core-count width.

    A `corpus-build` step peak does not measure this build's footprint. `peak_rss.reap_peak_rss_bytes` takes the largest single process in the child's tree, which here is the parent, so the step peak barely moves with the width (13.25 GB at ten jobs, 13.77 GB at two) and never includes the pool. The 2026-08-27 fully recomputed pass read 17.76 GB at eight workers, while a per-term measurement of the same tree put parent and workers together at roughly twice a 34 GB machine.

    Err high on the divisor. One that is too low pushes the pool into the reserve; one that is too high only gives a large machine fewer workers than it has room for. A machine the pooled build does not fit gets a width of one, which is the serial build: there is no pool, and each fragment exists once instead of twice. What the parent holds grows per unit (one row of the workload table and one of the unit store), so shrinking CORPUS_PARENT_BYTES widens the pool on every machine. Issue #160, an on-disk workload, was closed as not needed at that size.

    The cap comes from the cores, not from a measured limit on the parent. One parent process hands out the batches and merges every reply, and on the 18-core 48 GiB machine (`doc/fleet.md`) a fully recomputed hand build's units phase ran faster at seventeen workers than at eight, so the parent does not stop the pool scaling before the cores run out. A hand build and --skip-gates take every core less the parent. A gated pass splits the cores between the build lane and the gate lane (`memory_budget.split_cores`) and takes the build lane's share less the parent, so the build's processes and gate:make-test's pool, which holds the gate lane's share (`make_test_pool_width`), together fill the machine without overlapping; on a pass that skips gate:make-test the gate lane holds gate:rebuild-contracts and gate:conform instead. Once all three gates have skipped or finished, the gate lane is idle, and a units pool that starts then takes the hand build's width (`corpus_gates_idle_job_budget`). gate:make-test's pool is also subtracted from memory when that gate runs: FONT_SUITE_WORKER_BYTES for each of its workers, as in `kernel_threads_budget`. gate:js also runs from t=0, but it is one node process. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine. The cores come from `memory_budget.usable_cores()`, so an affinity mask or a cgroup quota narrows this width as it narrows the others.
    """
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _corpus_fit_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores
    )
    return memory_budget.how_many_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def corpus_write_job_budget(units_jobs: int) -> int:
    """Return the most workers the corpus build's write (phase 2, `build._write_corpus`) may start: as many as fit, at CORPUS_WRITE_WORKER_BYTES each, in the memory `units_jobs` units workers of CORPUS_WORKER_BYTES hold, floored at one. The units pool has stopped by the time the write's pool starts (`build._RecomputeRunner.stop_workers`), so the write's pool holds no more than a units pool would, and the corpus-build step stays within the memory every width that sizes a pool beside it subtracts for the build (`_corpus_fit_terms`); nothing else is reserved for it. The build passes the units width it would choose as the write starts (`build._units_pool_width`): the build lane's share less the parent while the gate lane still has work, and the hand build's width (`corpus_gates_idle_job_budget`) once gate:make-test, gate:conform and gate:rebuild-contracts have each skipped or finished, which is the more common case by then, since the write starts a units phase later than the units pool. The build narrows this further to the classes' sizes (`build._write_width`)."""
    return max(1, units_jobs * CORPUS_WORKER_BYTES // CORPUS_WRITE_WORKER_BYTES)


def corpus_job_derivation(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `corpus_job_budget`'s width as a clause for the plan line and the corpus build's `--jobs` help: `memory_budget.describe_fit` over the same terms, so a reader can check where the width came from."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _corpus_fit_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores
    )
    return memory_budget.describe_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def gates_idle_marker_path() -> Path:
    """Return the file the driver writes once the gate lane has nothing left to run, which a gated pass's corpus build reads when its units pool starts (`corpus_gates_idle_job_budget`). The pass lock serializes passes, so one path serves every pass, and the driver deletes the file before it spawns the build and after the build exits."""
    return cycle_paths.CYCLE_VAR / "gates-idle"


def corpus_gates_idle_job_budget(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int | None:
    """Return the width a gated pass's corpus build gives its units pool in place of `corpus_job_budget`'s when, as the pool starts, gate:make-test, gate:conform and gate:rebuild-contracts have each skipped or finished, or None when the plan has no wider width to offer. The driver writes `gates_idle_marker_path()` at that moment (`_GateLaneWatch`), and the build reads it after its load and plan phases, just before it starts the pool (`--gates-idle-jobs`, `--gates-idle-marker`). The width is a hand build's: every usable core less the parent, with only the parent's bytes off the machine's memory, because gate:make-test's pool has exited (`corpus_job_budget` with `skip_gates=True`). gate:js can still be running, as one node process.

    The build lane ends the passes that run the corpus build (`make cycle-timings ARGS='--critical-path'`), and the units phase grows with the corpus, so the gate lane's half of the cores held idle beside it lengthens those passes. The decision is made when the pool starts rather than when the driver spawns the step, because gate:rebuild-contracts usually finishes during the build's load and plan phases, so many more passes find the gate lane idle at the pool's start than at the step's. A pass whose gate:conform or gate:make-test is still running then keeps the build lane's share. Issue #499 has the measurements: the timings journal's passes, and a pair of hand builds on the 18-core 48 GiB machine (`doc/fleet.md`) whose units phase ran faster at every core less the parent than at the lane share.

    The widths planned beside the build stay correct. gate:rebuild-contracts' width and gate:conform's core cap subtract the build's processes at the lane share (`contracts_pool_width`, `_conform_core_cap`), and gate:conform's memory term subtracts the build's bytes at that width (`_conform_build_lane`), but the pool widens only after both gates have finished or were skipped, and under the queue policy no gate starts once the gate lane is idle. The verdict update's refill pool runs after the build.

    It is None under `--skip-gates`, where `corpus_job_budget` already gives every core less the parent, and wherever the widened width is no wider than the lane's, as on a machine whose memory holds both to one worker.
    """
    if skip_gates:
        return None
    lane = corpus_job_budget(
        skip_gates=False, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
    )
    wide = corpus_job_budget(skip_gates=True, ncores=ncores, total_bytes=total_bytes)
    return wide if wide > lane else None


def corpus_gates_idle_derivation(*, ncores: int | None = None, total_bytes: int | None = None) -> str:
    """Return `corpus_gates_idle_job_budget`'s width as a clause for the plan line: the condition that selects it, then `corpus_job_derivation` at a hand build's terms."""
    condition = f"taken in place of --jobs when, as the units pool starts, gate:make-test, gate:conform and gate:rebuild-contracts have each skipped or finished, which the driver signals by writing {gates_idle_marker_path()}"
    return f"{condition}; every usable core less its parent; " + corpus_job_derivation(
        skip_gates=True, ncores=ncores, total_bytes=total_bytes
    )


def signature_job_budget(*, ncores: int | None = None) -> int:
    """Return the `--signature-jobs` width the cycle passes the corpus build, for the pool that shapes the ink-signature store's misses, and the default a hand build takes. Memory does not limit it. A signature worker is a spawn process holding one `InkComparator` over the two fonts with plain shapers and nothing else (no subset pack, units, or projections), and its resident set stays about a tenth of a gigabyte however many signatures it shapes (the `signature` pool records in `rebuild/out/cycle-timings.ndjson`). The width is `memory_budget.usable_cores()`, the whole machine, under a gated cycle and in a hand run alike. gate:make-test's pool is not subtracted, although a pass that skips run_m1 starts this phase at t=0 beside that pool: the two share the cores while both run, which costs contention time rather than memory. The corpus build's cap (`_corpus_fit_terms`) does not apply, so a machine where memory holds the corpus build to one worker, or a gated pass that gives it the build lane's share, still shapes its signatures on every core. Below `build._SIGNATURE_POOL_THRESHOLD` misses the phase runs serially at any width, so a pass with the signature store filled starts no pool."""
    from rebuild.tools import memory_budget

    return max(1, ncores or memory_budget.usable_cores())


def signature_job_derivation(*, ncores: int | None = None) -> str:
    """Return `signature_job_budget`'s width as a clause for the plan line and the `--signature-jobs` help. It does not use `memory_budget.describe_fit`, whose clause would call the worker unmeasured: the width is a count of cores, and the clause says whose cores they are."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    width = signature_job_budget(ncores=cores)
    return f"{width} of {cores} cores, the whole machine, shared with gate:make-test's pool on a cycle pass that runs that gate"


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
    """Return the three terms `contracts_pool_width` and `contracts_pool_derivation` share, which `_conform_core_cap` also subtracts: the usable cores; the corpus build's process count (its parent plus `corpus_job_budget`'s workers, or zero when the build does not run); and gate:make-test's pool width under the overlap policy on a pass that runs that gate. Under the queue policy the suite and the conformance sweep wait for gate:make-test to finish, so nothing is subtracted for that pool."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    corpus = (
        0
        if skip_corpus
        else 1
        + corpus_job_budget(
            skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
        )
    )
    make_test = (
        make_test_pool_width(build_lane_runs=build_lane_runs, ncores=ncores)
        if pool_policy == "overlap" and not (skip_gates or skip_make_test)
        else 0
    )
    return cores, corpus, make_test


def contracts_pool_width(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    skip_corpus: bool = False,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the width of gate:rebuild-contracts' pytest pool under a cycle, which the cycle sets on that child as PYTEST_XDIST_AUTO_NUM_WORKERS. Memory does not limit it: no contracts worker holds a live artifact, so no constant measures one, and the width is a count of cores. The suite runs beside the corpus build, so the width is the usable cores, less the build's parent and its `corpus_job_budget` workers, less gate:make-test's pool under the overlap policy on a pass that runs that gate (`_contracts_pool_terms`), floored at one. A pass whose corpus build does not run (skipped, promoted, or assets-refreshed) subtracts nothing for the build, so the suite gets every core unless gate:make-test's pool is subtracted.

    Each subtraction leaves its cores to the pool that holds them, and the conformance sweep's cap (`_conform_core_cap`) makes the same two subtractions for the same reasons. The corpus build's processes are subtracted because the build lane, with the corpus build in it, ends a pass that runs the build and skips gate:make-test (`make cycle-timings ARGS='--critical-path'` shows it) and the `--fresh` passes behind the default, which run both (the module docstring), so the build keeps the build lane's share. Under the overlap policy gate:make-test's pool is subtracted as well: the plan cannot tell whether that pool will still be running when the suite and the sweep start, and while it runs it holds the gate lane's share, so the subtraction keeps a core from being booked twice. Neither width subtracts the other pool, which is off the critical path on the `--fresh` passes behind the default (the module docstring), so under that policy the sweep and the suite share the cores the two subtractions leave while both run, an unbudgeted overlap that `doc/parallelism.md` lists. On those passes both finished before the build lane did, so neither the slowdown each took from the sharing nor the suite's single worker lengthened the pass. The suite's controller is not subtracted: it idles while its workers run, and subtracting it would cost the suite a worker on every pass, so the suite runs one process more than its width. The pool's memory comes out of `memory_budget`'s reserve, not out of `corpus_job_budget`'s co-resident term, as `_standing_fill_terms` also assumes for this pool; subtracting it there would narrow the build on every pass for a worker no constant measures (`calibrate_budgets.UNITS`).

    Beside a corpus build at its cap, the build's parent and workers hold the build lane's share of the cores (`memory_budget.split_cores`), so the arithmetic leaves the suite the gate lane's share. Under the queue policy, and on a pass that skips gate:make-test, that share is the suite's width. Under the overlap policy on a pass that runs gate:make-test, that gate's pool already holds the gate lane, so nothing is left and the floor gives the suite one worker on every machine, and the plan line says so. On a pass that runs neither run_m1 nor the corpus build, gate:make-test's pool holds every core (`make_test_pool_width`, which is why this function takes `build_lane_runs`), and the overlap policy floors the suite at one there too. The suite usually is not on the pass's critical path: under a cycle it runs only the closure-selected tests. A pass that reruns the whole suite at one worker can outlast the rest of the pass. That cost is accepted until a one-worker `gate:rebuild-contracts` row in `rebuild/out/cycle-timings.ndjson` ends after both the build lane and every other gate of its pass (`make cycle-timings ARGS='--critical-path'` counts the passes each step ends, and the pass's run line records its `contracts_workers`), which is the signal to revisit the floor.

    The overlap policy has no higher floor. Where the floor binds, the conformance sweep, held at its own floor of one process per acceptance configuration (`_conform_core_cap`), is left no cores either, so the machine already runs more processes than it has cores while the gate pools overlap, and a higher floor would add more. The queue policy avoids that oversubscription by running the suite after gate:make-test and the sweep, at the gate lane's share. PYTEST_XDIST_AUTO_NUM_WORKERS overrides all of this, because the child inherits this environment and will run at that width, as in `make_test_pool_width`. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
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
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `contracts_pool_width`'s width as a clause for the plan's lane line. Like `signature_job_derivation`, it does not use `memory_budget.describe_fit`: the width is a count of cores, and the clause says which cores are left out. A width set through PYTEST_XDIST_AUTO_NUM_WORKERS is reported as set."""
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
        workers = f"{corpus - 1} worker" + ("" if corpus - 1 == 1 else "s")
        clause = f"{width} of {cores} cores, less the corpus build's parent and its {workers}"
    if make_test:
        clause += f", less gate:make-test's {make_test} (overlap policy)"
    return clause if cores - corpus - make_test >= 1 else clause + ", floored at one"


def contracts_submission_note(*, skip_corpus: bool) -> str:
    """Return where in the build lane the rebuild suite is submitted, for the plan's step note and lane line: beside the corpus build when one runs, and otherwise at the same point, once the run_m1 gate has passed. The second wording keeps a pass whose corpus-build row reads SKIPPED from claiming the suite runs beside it."""
    if skip_corpus:
        return "submitted once the run_m1 gate passes (no corpus build this pass)"
    return "submitted beside the corpus build"


def _conform_build_lane(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_corpus: bool,
    verdict_update_runs: bool,
    build_lane_runs: bool = True,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[str, int]:
    """Return the build-lane step the conformance sweep runs beside, by plan step name, and the memory that step holds. The candidates are the corpus build (its parent plus `corpus_job_budget`'s workers), and the verdict-update step (the verdict update's process plus `standing_fill_jobs`' refill pool). The land that follows them is not a candidate: it holds less than either (LAND_BYTES), which a test checks. The one this pass runs that holds more is returned, the corpus build on a tie, and `("", 0)` when the pass runs neither. Each figure comes from its own budget, so the memory the conformance sweep sets aside for that step matches the step's width. The conformance sweep can run beside either step: its lane is submitted when run_m1's gate passes, so it starts beside the corpus build, and the verdict-update step follows the build in the same lane, so a conformance sweep still running then, or one the queue policy starts late behind make-test, runs beside the verdict-update step."""
    steps: list[tuple[str, int]] = []
    if not skip_corpus:
        corpus_jobs = corpus_job_budget(
            skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
        )
        steps.append(("corpus-build", CORPUS_PARENT_BYTES + CORPUS_WORKER_BYTES * corpus_jobs))
    if verdict_update_runs:
        fill_jobs = standing_fill_jobs(
            skip_gates=skip_gates,
            skip_make_test=skip_make_test,
            build_lane_runs=build_lane_runs,
            ncores=ncores,
            total_bytes=total_bytes,
        )
        steps.append(("verdict-update", STANDING_FILL_PARENT_BYTES + STANDING_FILL_WORKER_BYTES * fill_jobs))
    return max(steps, key=lambda step: step[1], default=("", 0))


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
    """Return the conformance sweep's cap on its width and a clause saying which bound set it. The cap is the usable cores less the corpus build's processes (its parent and `corpus_job_budget`'s workers) when the pass runs the build, and less gate:make-test's pool under the overlap policy on a pass that runs that gate (`_contracts_pool_terms`). These are the two subtractions the rebuild suite's width makes, and each leaves its cores to the pool that holds them: the corpus build keeps the build lane's share, and gate:make-test's pool, which may still hold the gate lane when the sweep starts, keeps its own width. The rebuild suite's pool is not subtracted, and the suite's width does not subtract the sweep, because each is off the critical path on the `--fresh` passes behind the default (the module docstring); under that policy the two share the cores left while both run, an unbudgeted overlap that `doc/parallelism.md` lists. `contracts_pool_width` gives these reasons in full for both widths. The cap is floored at the acceptance-configuration count, or at the cores where there are fewer, which is the width one process per configuration gives, so a cycle never runs the sweep narrower than that; where the floor binds beside the corpus build, the sweep and the build oversubscribe the cores while they overlap, an unbudgeted overlap that `doc/parallelism.md` lists. The verdict-update step is not subtracted here; its memory is (`_conform_build_lane`). Its refill pool, capped at the cores, can overlap the sweep's tail on cores, an unbudgeted overlap that `doc/parallelism.md` lists. A hand run (`skip_gates=True, skip_corpus=True`) subtracts nothing and gets every core."""
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS

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
        workers = f"{corpus - 1} worker" + ("" if corpus - 1 == 1 else "s")
        parts.append(f"less the corpus build's parent and its {workers}")
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
    verdict_update_runs: bool,
    pool_policy: str,
    build_lane_runs: bool = True,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[int, int, int]:
    """Return the three arguments of the conformance sweep's width, so `conform_job_budget` and `conform_job_derivation` use one derivation: the per-unit divisor (CONFORM_SWEEP_UNIT_BYTES), the co-resident bytes, and the cap. The co-resident bytes are the build-lane step `_conform_build_lane` names, plus gate:make-test's pool under the overlap policy on a pass that runs that gate. The queue policy makes the conformance sweep wait for make-test to finish, so nothing is subtracted for that pool there. The cap is `_conform_core_cap`'s. The budget does not load the spec, so it does not count the units; `run_m1._spawn_pool` caps the pool at the unit count at run time, and every fleet machine has fewer cores than units."""
    _step, build_lane = _conform_build_lane(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        verdict_update_runs=verdict_update_runs,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    make_test = (
        _font_suite_worker_bytes() * make_test_pool_width(build_lane_runs=build_lane_runs, ncores=ncores)
        if pool_policy == "overlap" and not (skip_gates or skip_make_test)
        else 0
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
    verdict_update_runs: bool = False,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the `--jobs` the cycle passes gate:conform: how many conformance-sweep units run at once, one spawn process each (`run_m1.run_font_conformance`, `run_m1.sweep_units`). It is memory, less the reserve, less what runs beside the conformance sweep, divided by CONFORM_SWEEP_UNIT_BYTES, capped at the cores less the corpus build's processes, and less gate:make-test's pool under the overlap policy on a pass that runs that gate, but never below the acceptance-configuration count (`_conform_core_cap`), and floored at one (`_conform_fit_terms`); memory can still narrow it below that count. `run_m1._spawn_pool` caps it at the unit count at run time. What runs beside it is whichever of the corpus build and the verdict-update step this pass runs that holds more (`_conform_build_lane`). The verdict-update step's own width leaves the conformance sweep out; this subtraction accounts for that step's memory but not its cores, so a refill pool that overlaps the sweep oversubscribes the cores (`doc/parallelism.md` lists the overlap). `verdict_update_runs` defaults to False because only the cycle's plan runs a verdict-update step. gate:make-test's pool is subtracted only under the overlap policy on a pass that runs that gate, since the queue policy makes the conformance sweep wait for make-test. Two things share the machine with no memory estimate, and their bytes come out of the reserve: gate:rebuild-contracts' pool under the overlap policy when the suite runs, which is limited by cores and measured by no constant (`calibrate_budgets.UNITS`), and the build lane's other steps.

    CONFORM_SWEEP_UNIT_BYTES is measured at the per-edit maximum length (`conform.SWEEP_MAX_LENGTH`) only. A cycle run with a deeper `--conform-max-length` holds its windows in process and shares no memo, so the constant is not a measurement for it; deeper sweeps belong to `make conform-deep`. A hand `run_m1 --conform-only` uses the idle case (`skip_gates=True, skip_corpus=True`, no verdict-update step). A width of one runs the serial conformance sweep (`conform.run_conformance`). `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _conform_fit_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        verdict_update_runs=verdict_update_runs,
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
    verdict_update_runs: bool = False,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `conform_job_budget`'s width as a clause for the plan's Lane conform line and run_m1's `--jobs` help: `memory_budget.describe_fit` over the same terms, so the clause opens with the width it explains, then which bound set the cap (`_conform_core_cap`)."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _conform_fit_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        verdict_update_runs=verdict_update_runs,
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


def _standing_fill_terms(
    *, skip_gates: bool, skip_make_test: bool, build_lane_runs: bool = True, ncores: int | None
) -> tuple[int, int, int]:
    """Return the standing fill pool's three terms for `standing_fill_jobs` and `standing_fill_derivation`: STANDING_FILL_WORKER_BYTES is the divisor, STANDING_FILL_PARENT_BYTES is subtracted first, and the cap is the cores, because no width is known past which this pool stops getting faster. Under a gated cycle gate:make-test's pool comes off memory, its bytes subtracted as they are for the corpus build at the width that pool runs at (`make_test_pool_width`, which is why this takes `build_lane_runs`), but not off the cap: the two pools share the cores while both run, which costs contention time, not swap. The verdict-update step also starts beside gate:rebuild-contracts, whose pool no constant measures (`calibrate_budgets.UNITS`), so that pool is left out, and a pass that drops the standing-fill memo overuses memory for the pool's few tens of seconds. gate:conform's sweep can also run beside this step. That overlap's memory is subtracted on the conformance sweep's side (`_conform_build_lane`), so this width leaves the conformance sweep out; neither side subtracts the other's cores, so the two pools briefly oversubscribe the cores when they overlap."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    coresident = STANDING_FILL_PARENT_BYTES
    if not (skip_gates or skip_make_test):
        coresident += _font_suite_worker_bytes() * make_test_pool_width(
            build_lane_runs=build_lane_runs, ncores=ncores
        )
    return STANDING_FILL_WORKER_BYTES, coresident, cores


def standing_fill_jobs(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the `--standing-fill-jobs` the cycle passes the verdict update, which forwards it to the standing fill as `--jobs`: memory, less the reserve and the verdict update's process, divided by one refill worker, capped at the cores (`_standing_fill_terms`). The width is computed here and nowhere else for two reasons. The fill's code is part of its memo's stamp, so arithmetic inside it would drop the memo on every edit to that arithmetic. And only the cycle knows which gates run beside the verdict-update step. A served pass stays under the fill's pool threshold and starts no pool at any width, so this number costs it nothing."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _standing_fill_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, build_lane_runs=build_lane_runs, ncores=ncores
    )
    return memory_budget.how_many_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def standing_fill_derivation(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    build_lane_runs: bool = True,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `standing_fill_jobs`' width as a clause for the plan: `memory_budget.describe_fit` over the same terms."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _standing_fill_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, build_lane_runs=build_lane_runs, ncores=ncores
    )
    return memory_budget.describe_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def build_plan(
    *,
    verdicts: Path | None,
    no_carry: bool,
    carry_out: Path | None,
    skip_gates: bool,
    first_run: bool,
    short_id: str,
    no_merge: bool = False,
    skip_conform: bool = False,
    skip_make_test: bool = False,
    make_test_note: str = "",
    make_test_fingerprint: str | None = None,
    force_make_test: bool = False,
    conform_max_length: int = CONFORM_MAX_LENGTH_DEFAULT,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    review_out: Path | None = None,
    ncores: int | None = None,
    total_bytes: int | None = None,
    skip_run_m1: bool = False,
    rerun_gates_only: bool = False,
    run_m1_note: str = "",
    run_m1_fingerprint: str | None = None,
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
    skip_verdict_update: bool = False,
    verdict_update_note: str = "",
    direct_merge: bool = False,
    record_greens: bool = False,
    keep_history: bool = False,
    recipe_serves: bool = False,
    scratch_dir: Path | None = None,
    seed_kept: bool = False,
) -> Plan:
    do_carry = not no_carry and not first_run and not skip_verdict_update and not direct_merge
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = (
            carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"
        )
    if skip_verdict_update:
        verdict_update_step_note = f"SKIPPED ({verdict_update_note})"
    elif first_run:
        verdict_update_step_note = "SKIPPED (first run)"
    elif not do_carry and not direct_merge:
        verdict_update_step_note = "SKIPPED (--no-carry)"
    else:
        verdict_update_step_note = ""
    verdict_update_runs = not verdict_update_step_note

    no_make_test = skip_gates or skip_make_test
    build_lane_runs = not (skip_run_m1 and skip_corpus)
    make_test_workers = make_test_pool_width(build_lane_runs=build_lane_runs, ncores=ncores)
    corpus_jobs = corpus_job_budget(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
    )
    if skip_gates:
        corpus_head = "--skip-gates, so the corpus build is capped at every core less its parent"
    else:
        from rebuild.tools import memory_budget

        cores = ncores or memory_budget.usable_cores()
        build_lane, _gate_lane = memory_budget.split_cores(cores)
        lane_head = f"the build lane holds {build_lane} of {cores} cores (memory_budget.split_cores), so the corpus build is capped at {build_lane} less its parent"
        if skip_make_test:
            corpus_head = f"{lane_head}; gate:make-test skipped, so the other gates hold the gate lane and no pytest pool's bytes come off the machine's memory"
        else:
            beside = make_test_pool_width(ncores=ncores)
            beside_workers = f"{beside} worker" + ("" if beside == 1 else "s")
            corpus_head = f"{lane_head}; gate:make-test's pool, {beside_workers}, holds the gate lane and its bytes come off the machine's memory beside the build's own parent"
    corpus_reason = f"{corpus_head}; " + corpus_job_derivation(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
    )
    corpus_gates_idle_jobs = corpus_gates_idle_job_budget(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
    )
    corpus_gates_idle_reason = (
        corpus_gates_idle_derivation(ncores=ncores, total_bytes=total_bytes)
        if corpus_gates_idle_jobs is not None
        else ""
    )
    gates_idle_marker = gates_idle_marker_path() if corpus_gates_idle_jobs is not None else None
    signature_jobs = signature_job_budget(ncores=ncores)
    signature_reason = (
        "the ink-signature phase's shaping pool, cores-bound since a signature worker holds one comparator and no memory constant sizes it; "
        + signature_job_derivation(ncores=ncores)
    )
    fill_jobs = standing_fill_jobs(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    fill_reason = (
        "the standing fill's refill pool on a memo-drop pass, beside the verdict update's process; "
        + (
            standing_fill_derivation(
                skip_gates=skip_gates,
                skip_make_test=skip_make_test,
                build_lane_runs=build_lane_runs,
                ncores=ncores,
                total_bytes=total_bytes,
            )
        )
    )
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
    sweep_jobs = sweep_job_budget(ncores, total_bytes=total_bytes)
    sweep_reason = "the oracle's row-range workers, " + sweep_job_derivation(ncores, total_bytes=total_bytes)
    conform_jobs = conform_job_budget(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        verdict_update_runs=verdict_update_runs,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    conform_beside, _lane_bytes = _conform_build_lane(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_corpus=skip_corpus,
        verdict_update_runs=verdict_update_runs,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    make_test_beside_conform = pool_policy == "overlap" and not no_make_test
    if conform_beside == "corpus-build":
        corpus_workers = f"{corpus_jobs} worker" + ("" if corpus_jobs == 1 else "s")
        conform_head = f"CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, beside the corpus build's parent and its {corpus_workers}"
    elif conform_beside == "verdict-update":
        fill_workers = f"{fill_jobs} refill worker" + ("" if fill_jobs == 1 else "s")
        conform_head = (
            "CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, "
            + (
                "the corpus build not running this pass, so "
                if skip_corpus
                else "the verdict-update step outweighing the corpus build, so "
            )
            + f"beside the verdict update's process and its {fill_workers}"
        )
    else:
        conform_head = (
            "CONFORM_SWEEP_UNIT_BYTES a conformance-sweep unit, neither the corpus build nor the verdict-update step running this pass, so "
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
        verdict_update_runs=verdict_update_runs,
        pool_policy=pool_policy,
        build_lane_runs=build_lane_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    kernel_threads = kernel_threads_budget(
        skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    if skip_make_test:
        kernel_reason = "the table build's memory ceiling, the one width RAM binds"
    else:
        kernel_workers = make_test_pool_width(ncores=ncores)
        kernel_reason = f"the table build's memory ceiling, less gate:make-test's {kernel_workers} worker" + (
            "" if kernel_workers == 1 else "s"
        )
    kernel_reason += ", capped at the configuration count and the cores"
    overlap_memo_writes = memo_writes_overlap_budget(
        kernel_threads, skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    scratch_beside_default = scratch_beside_default_budget(
        kernel_threads,
        overlap_memo_writes=overlap_memo_writes,
        skip_make_test=no_make_test,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    replay_threads = replay_threads_budget(
        skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    replay_reason = replay_threads_derivation(
        skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    corpus_dir = review_out if review_out is not None else REVIEW_OUT
    do_merge = (do_carry or direct_merge) and not no_merge and review_out is None
    do_retention = not keep_history and not first_run and review_out is None
    live_pass = review_out is None and not first_run
    next_corpus: Path | None = None
    if live_pass and promote_corpus is not None:
        next_corpus = promote_corpus
    elif live_pass and not skip_corpus:
        next_corpus = next_corpus_dir()
    do_land = live_pass and (next_corpus is not None or do_merge)
    land_dir: Path | None = None
    if do_land:
        land_dir = scratch_dir if scratch_dir is not None else cycle_paths.CYCLE_VAR / short_id
        scratch_dir = land_dir
    update_corpus = review_out if review_out is not None else (next_corpus or REVIEW_OUT)

    plan = Plan(
        short_id=short_id,
        first_run=first_run,
        carry_out=resolved_carry_out,
        verdicts=verdicts,
        skip_gates=skip_gates,
        do_merge=do_merge,
        skip_conform=skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fingerprint,
        skip_run_m1=skip_run_m1,
        rerun_gates_only=rerun_gates_only and not skip_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fingerprint,
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
        skip_verdict_update=skip_verdict_update,
        verdict_update_note=verdict_update_note,
        verdict_update_direct_merge=direct_merge,
        record_greens=record_greens,
        retention=do_retention,
        recipe_serves=recipe_serves,
        pool_policy=pool_policy,
        corpus_jobs=corpus_jobs,
        corpus_reason=corpus_reason,
        corpus_gates_idle_jobs=corpus_gates_idle_jobs,
        corpus_gates_idle_reason=corpus_gates_idle_reason,
        gates_idle_marker=gates_idle_marker,
        signature_jobs=signature_jobs,
        signature_reason=signature_reason,
        standing_fill_jobs=fill_jobs,
        standing_fill_reason=fill_reason,
        sweep_jobs=sweep_jobs,
        sweep_reason=sweep_reason,
        kernel_threads=kernel_threads,
        kernel_reason=kernel_reason,
        overlap_memo_writes=overlap_memo_writes,
        scratch_beside_default=scratch_beside_default,
        replay_threads=replay_threads,
        replay_reason=replay_reason,
        make_test_workers=make_test_workers,
        contracts_workers=contracts_workers,
        contracts_reason=contracts_reason,
        conform_jobs=conform_jobs,
        conform_reason=conform_reason,
        conform_max_length=conform_max_length,
        review_out=review_out,
        corpus_dir=corpus_dir,
        update_corpus=update_corpus,
        next_corpus=next_corpus,
        seed_kept=seed_kept,
        land=do_land,
        scratch_dir=scratch_dir,
    )

    if skip_run_m1:
        plan.steps.append(
            Step(
                "run_m1",
                None,
                f"SKIPPED ({run_m1_note}); gate re-evaluated from the recorded summaries",
                lane="build",
                skipped=True,
            )
        )
    elif plan.rerun_gates_only:
        rerun_argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--gates-only"]
        if sweep_jobs > 1:
            rerun_argv += ["--jobs", str(sweep_jobs)]
        if fresh:
            rerun_argv += ["--fresh-oracle-cache"]
        plan.steps.append(
            Step(
                "run_m1",
                rerun_argv,
                run_m1_note,
                lane="build",
                describe=step_description(RUN_M1_GATES_ONLY_STEP),
            )
        )
    else:
        run_m1_argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
        if sweep_jobs > 1:
            run_m1_argv += ["--jobs", str(sweep_jobs)]
        run_m1_argv += [
            "--kernel-threads",
            str(kernel_threads),
            "--overlap-memo-writes" if overlap_memo_writes else "--no-overlap-memo-writes",
            "--scratch-beside-default",
            str(scratch_beside_default),
            "--replay-threads",
            str(replay_threads),
        ]
        if fresh:
            run_m1_argv += ["--fresh-oracle-cache"]
        plan.steps.append(Step("run_m1", run_m1_argv, run_m1_note, lane="build"))

    if live_pass and not skip_corpus:
        seed_note = (
            f"keeps {next_corpus}, the complete corpus an earlier pass built beside the served one, as the build's seed"
            if seed_kept
            else f"clones rebuild/out/review to {next_corpus}, copy-on-write, as the build's seed"
        )
        plan.steps.append(Step("corpus-seed", None, seed_note, lane="build"))
    if skip_corpus:
        plan.steps.append(Step("corpus-build", None, f"SKIPPED ({corpus_note})", lane="build", skipped=True))
        if refresh_assets:
            plan.steps.append(
                Step(
                    "assets-refresh",
                    ["uv", "run", "python", "-m", "rebuild.review.build", "refresh-assets"],
                    "copy rebuild/review/static/ over the served copy and restamp the manifest's static component; the units and the sidecars are untouched, so the autosave stays aligned",
                    lane="build",
                )
            )
    else:
        corpus_argv = ["uv", "run", "python", "-m", "rebuild.review.build"]
        if not first_run or review_out is not None:
            corpus_argv += ["--out", str(update_corpus)]
        if corpus_gates_idle_jobs is not None:
            corpus_argv += [
                "--gates-idle-jobs",
                str(corpus_gates_idle_jobs),
                "--gates-idle-marker",
                str(gates_idle_marker),
            ]
        corpus_argv += ["--jobs", str(corpus_jobs), "--signature-jobs", str(signature_jobs)]
        if fresh:
            corpus_argv += ["--recompute-all-units"]
        plan.steps.append(Step("corpus-build", corpus_argv, lane="build"))

    if review_out is not None:
        plan.complaints_note = "staging: reads the live autosave"
    elif first_run:
        plan.complaints_note = "first run: no verdicts to cluster"
    elif skip_verdict_update:
        plan.complaints_note = verdict_update_note
    elif not AUTOSAVE.exists():
        plan.complaints_note = "no verdicts store"

    snapshot = land_dir / landing.SNAPSHOT_NAME if land_dir is not None else None
    prepared = land_dir / landing.PREPARED_NAME if land_dir is not None and do_merge else None
    if do_land:
        plan.steps.append(
            Step(
                "store-snapshot",
                None,
                f"copies the live store to {snapshot} under the store's lock, the point the land compares the saves made during the pass against"
                + ("; the verdict update prepares a copy of it" if do_merge else ""),
                lane="build",
            )
        )
    if verdict_update_step_note:
        plan.steps.append(Step("verdict-update", None, verdict_update_step_note, lane="build", skipped=True))
    else:
        verdict_update_argv = [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.tools.verdict_update",
            "--corpus",
            str(update_corpus),
        ]
        master_is_store = verdicts is not None and Path(verdicts).resolve() == AUTOSAVE.resolve()
        master_input = snapshot if snapshot is not None and master_is_store else verdicts
        if do_carry:
            assert resolved_carry_out is not None
            verdict_update_argv += ["--verdicts", str(master_input)]
            if snapshot is not None and not master_is_store and AUTOSAVE.exists():
                verdict_update_argv += ["--verdicts", str(snapshot)]
            verdict_update_argv += ["--carry-out", str(resolved_carry_out)]
        else:
            verdict_update_argv += ["--merge-master", str(master_input)]
        if prepared is not None:
            verdict_update_argv += [
                "--autosave",
                str(prepared),
                "--journal",
                str(prepared.parent / "journal.ndjson"),
            ]
        if do_merge and update_corpus.parent != REVIEW_OUT.parent:
            from rebuild.tools.standing_verdicts import MEMO_NAME

            verdict_update_argv += ["--standing-memo", str(REVIEW_OUT.parent / MEMO_NAME)]
        if not do_merge:
            verdict_update_argv += ["--no-merge"]
        if plan.complaints_note:
            verdict_update_argv += ["--no-complaints"]
        if fresh:
            verdict_update_argv += ["--fresh-standing-memo"]
        verdict_update_argv += ["--standing-fill-jobs", str(fill_jobs)]
        if do_carry and not do_merge:
            note = (
                "carry only (staging: the live autosave is never written)"
                if review_out is not None
                else "carry only (--no-merge)"
            )
        elif direct_merge:
            note = (
                "the corpus did not move, so the carry is the identity — merging the master straight in, "
                "then duplicate fill, standing fill, duplicate fill, with each fill merged, and the complaint list"
            )
        else:
            note = "carry -> merge -> duplicate fill -> standing fill -> duplicate fill -> complaint list, with each fill merged, in one process"
        if prepared is not None:
            note += f", into {prepared}, a copy of the store; the land puts it in place"
        plan.steps.append(Step("verdict-update", verdict_update_argv, note, lane="build"))

    if land_dir is not None:
        land_argv = [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.review.landing",
            "--autosave",
            str(AUTOSAVE),
            "--journal",
            str(AUTOSAVE.with_name(journal.JOURNAL_NAME)),
            "--run-dir",
            str(land_dir),
            "--corpus",
            str(next_corpus or REVIEW_OUT),
        ]
        if prepared is not None:
            land_argv += ["--prepared", str(prepared)]
        if next_corpus is not None:
            land_argv += ["--staged", str(next_corpus), "--live", str(REVIEW_OUT), "--keep-discard"]
        land_argv += ["--tombstone-cutoff", retention_cutoff(), "--scan-state", str(journal_scan_path())]
        if next_corpus is None:
            land_note = "puts the prepared store in place under the verdict store's lock, with the saves made during the pass laid over it"
        else:
            store_part = (
                "the prepared store"
                if do_merge
                else "an empty store stamped for it (the old one is stashed, as this pass carries nothing)"
            )
            land_note = f"swaps {next_corpus} in for rebuild/out/review and puts {store_part} in place with it, under the verdict store's lock, with the saves made during the pass laid over it; the review server keeps running"
        plan.steps.append(Step("land", land_argv, land_note, lane="build"))

    if review_out is not None:
        plan.steps.append(
            Step(
                "review-facts",
                None,
                "SKIPPED (staging: the checked-in pins track the live corpus)",
                lane="build",
                skipped=True,
            )
        )
    else:
        plan.steps.append(
            Step(
                "review-facts",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "rebuild.review.facts",
                    "--update",
                    "--corpus",
                    str(REVIEW_OUT),
                ],
                "then names what moved in the invariant block against the index copy, diffing that block alone — the pins are the last accepted review facts; commit them to accept these",
                lane="build",
            )
        )

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
                    "gate:rebuild-contracts",
                    None,
                    f"SKIPPED ({contracts_note})",
                    lane="contracts",
                    skipped=True,
                )
            )
        else:
            plan.steps.append(
                Step(
                    "gate:rebuild-contracts",
                    contracts_argv(),
                    contracts_submission_note(skip_corpus=skip_corpus)
                    + (f"; {contracts_note}" if contracts_note else ""),
                    lane="contracts",
                )
            )
        if skip_make_test:
            plan.steps.append(
                Step("gate:make-test", None, f"SKIPPED ({make_test_note})", lane="t0", skipped=True)
            )
        elif force_make_test or fresh:
            plan.steps.append(
                Step(
                    "gate:make-test",
                    make_test_gate_argv(force=True),
                    "forced: the suite and pyright run even where their green records answer for the closure",
                    lane="t0",
                )
            )
        else:
            plan.steps.append(Step("gate:make-test", make_test_gate_argv(force=False), lane="t0"))

    plan.steps.append(
        Step(
            "job-costs",
            ["uv", "run", "python", "-m", "rebuild.tools.calibrate_budgets", "--check"],
            "the checked-in per-unit peaks against what this machine measured, once the gates have joined and this pass's own pool records are in the journal — a file read; committing a re-measured constant is the acceptance, exactly as the review-facts pins work",
        )
    )

    if do_retention:
        plan.steps.append(
            Step(
                "retention",
                None,
                f"on green finish: keep only the stamp-aligned verdicts-carried-*.json, drop verdicts-autosave-* stashes older than the journal's last stamp change or base event, compact the journal to a {RETENTION_WINDOW_DAYS}-day restore floor; --keep-history skips",
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


def resolve_carry_source() -> dict | None:
    from rebuild.review import status

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        stamp = None
    return status.resolve_carry_source(ROOT, stamp, AUTOSAVE)


def describe_carry_source(resolved: dict, root: Path, *, promoting: bool = False) -> str:
    """Return the line that names the master the carry resolved to. A master stamped for an older corpus than the served one is named as older and carried anyway: a verdict names its unit by content id, so it reaches the unit with that id on the live corpus or none, never the wrong window. On a promoting pass the aligned master is stamped for the corpus the pass is about to replace, because the resolution reads the live manifest before the move, and the line says so."""
    try:
        shown = resolved["path"].relative_to(root)
    except ValueError:
        shown = resolved["path"]
    if resolved["aligned"]:
        stamped = (
            "stamped for the corpus this pass replaces; its verdicts land by unit id"
            if promoting
            else "stamped for the served corpus"
        )
    else:
        replaced = "the one this pass replaces" if promoting else "the served one"
        stamped = (
            f"stamped {resolved['stamp']}, an older corpus than {replaced}; its verdicts land by unit id"
        )
    return f"Auto-resolved carry source: {shown} ({resolved['count']} effective verdicts, {stamped}). Pass --verdicts to override."


def master_stamped_for_corpus(master: Path, corpus: Path) -> bool:
    """Return whether the verdicts master carries the corpus's own stamp, read as merge_verdicts reads both: the master parsed whole as an ams-review-verdicts/1 document, and the stamp from the manifest's `generated_at`. This is the direct merge's precondition, because the direct merge passes the master to the merge unchanged and the merge refuses any input stamped for another corpus. `main` calls it for a master named by --verdicts. For an auto-resolved master the resolution's `aligned` already holds the answer, computed by `status.resolve_carry_source` from the same parse and stamp, so that master is not parsed twice. If a pass stops after the corpus build writes a new corpus and before the verdict update carries the store onto it, the store stays stamped for the previous corpus and the next pass skips the build as unchanged. This returns False for that master, so the pass takes the full carry by unit id. An unreadable master also returns False, and the carry reports it."""
    from rebuild.review.serve import parse_autosave_payload

    stamp = _manifest_stamp_at(corpus)
    try:
        payload = parse_autosave_payload(Path(master).read_bytes())
    except OSError:
        return False
    return stamp is not None and payload is not None and payload["manifest_generated_at"] == stamp


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
    """Return whether a review server that predates the land protocol (`probe_server`'s `legacy`: no /capabilities) can keep running through this pass. Such a server takes no part in the land, so the answer depends on what the plan writes under it: the corpus's units and stamp, which the land swaps, and the verdict store the app saves into. A pass that moves no corpus and merges nothing into the store (a --no-carry pass, a --no-merge carry over an unchanged corpus, a pass with no artifact work, an assets refresh, which rewrites no shard and leaves `generated_at` unchanged) writes neither, so that server keeps running. A promotion sets the corpus skip flag but swaps in the staged corpus, so `promotes_corpus` requires stopping the server even when the store is untouched. A server that speaks the land protocol keeps running through every pass (`_preflight`)."""
    return skip_corpus and not promotes_corpus and not writes_store


@dataclass(frozen=True)
class ServerProbe:
    """What listens on the review server's port, as `probe_server` classifies it. `kind` is `none` (nothing listens), `current` (a server from this checkout whose land-protocol code is the working tree's), `stale` (a server from this checkout running other code), `foreign` (a server from another checkout, whose root is `root`), `legacy` (a server that answers /capabilities with 404, from before the land protocol), or `unanswered` (a listener that gave no usable /capabilities answer in `CAPABILITIES_ATTEMPTS` tries, such as a current server stalled on a long request)."""

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
        ROOT / "rebuild" / "review"
    ):
        return ServerProbe("stale", root)
    return ServerProbe("current", root)


def start_review_server() -> None:
    """Start the review server detached from this pass, as `make review-cycle SERVE=bg` does: its own session, so it outlives the pass and the shell that started it, with its output in tmp/review-serve.log."""
    log = ROOT / "tmp" / "review-serve.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as out:
        subprocess.Popen(
            ["uv", "run", "python", "-m", "rebuild.review.serve"],
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


def _overlap_note(pools: list[str]) -> str:
    """Return what a gate's lane line says runs beside it under the overlap policy: the heavy gate pools named in `pools`, or that none runs."""
    if not pools:
        return "no other heavy gate pool runs this pass"
    return f"CO-RESIDENT with {' and '.join(pools)} (overlap policy)"


def _render_concurrency(plan: Plan) -> list[str]:
    if plan.skip_gates:
        return [
            "",
            "  Concurrency (--skip-gates):",
            f"    Lane build only; no gates; run_m1 sweeps --jobs {plan.sweep_jobs} ({plan.sweep_reason}) at --kernel-threads {'not passed (gates-only rerun)' if plan.rerun_gates_only else plan.kernel_threads} and --replay-threads {'not passed (gates-only rerun)' if plan.rerun_gates_only else plan.replay_threads}, corpus-build --jobs {plan.corpus_jobs} ({plan.corpus_reason}), corpus-build --signature-jobs {plan.signature_jobs} ({plan.signature_reason}), verdict-update --standing-fill-jobs {plan.standing_fill_jobs} ({plan.standing_fill_reason})",
        ]
    t0_lane = "gate:js" if plan.skip_make_test else "gate:js, gate:make-test"
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        f"    Lane t0   [from t=0, background]  : {t0_lane}",
        "    Lane build[serial, main thread]  : run_m1 -> submit gate:rebuild-contracts -> corpus-seed -> corpus-build -> store-snapshot -> verdict-update -> land -> review-facts",
    ]
    if plan.skip_conform:
        lines.append(
            f"    Lane conform                     : SKIPPED ({plan.conform_note or '--skip-conform'})"
        )
    elif plan.pool_policy == "overlap":
        beside = _overlap_note(
            [
                pool
                for pool, runs in (
                    ("gate:make-test's pool", not plan.skip_make_test),
                    ("gate:rebuild-contracts' pool", not plan.skip_contracts),
                )
                if runs
            ]
        )
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; {beside} (--jobs {plan.conform_jobs}; {plan.conform_reason})"
        )
    elif not plan.skip_make_test:
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; QUEUED behind gate:make-test (queue policy — one heavy pool at a time) (--jobs {plan.conform_jobs}; {plan.conform_reason})"
        )
    else:
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; gate:make-test not running, so no queueing (--jobs {plan.conform_jobs}; {plan.conform_reason})"
        )
    if plan.skip_contracts:
        lines.append(
            "    Lane rebuild-contracts           : SKIPPED (inputs unchanged since its last green run)"
        )
    else:
        lines.append(
            f"    Lane rebuild-contracts           : {contracts_submission_note(skip_corpus=plan.skip_corpus)}, -n {plan.contracts_workers} ({plan.contracts_reason});"
        )
        if plan.pool_policy == "overlap":
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
                "                                       QUEUED behind gate:conform (queue policy — one heavy pool at a time)"
            )
        elif not plan.skip_make_test:
            lines.append(
                "                                       QUEUED behind gate:make-test (queue policy; gate:conform not running)"
            )
        else:
            lines.append("                                       no other heavy pool running, so no queueing")
    lines.append(f"    run_m1 sweeps --jobs             : {plan.sweep_jobs}  ({plan.sweep_reason})")
    if plan.rerun_gates_only:
        lines.append(
            "    run_m1 --kernel-threads          : not passed (a gates-only rerun enumerates nothing, so there is no fan-out to size)"
        )
    else:
        lines.append(f"    run_m1 --kernel-threads          : {plan.kernel_threads}  ({plan.kernel_reason})")
        lines.append(
            f"    run_m1 --overlap-memo-writes     : {'on' if plan.overlap_memo_writes else 'off'}  (the table build's booking at that width, with MEMO_WRITE_OVERLAP_BYTES added for the memo writers beside the wave, {'fits the memory that width is sized from' if plan.overlap_memo_writes else 'does not fit the memory that width is sized from, so each memo file is written ahead of the work that follows it'})"
        )
        lines.append(
            f"    run_m1 --scratch-beside-default  : {plan.scratch_beside_default}  (at most this many of the heaviest deltas enumerated from scratch beside default, in whole tiers of equal unlocking-rune count, as many as the table build's booking at that width and memo-write order still fits with each booked at SCRATCH_PEAK_BYTES{'' if plan.scratch_beside_default else '; none fits, so every delta waits for default and reads its memo'})"
        )
    if plan.rerun_gates_only:
        lines.append(
            "    run_m1 --replay-threads          : not passed (a gates-only rerun replays nothing, so there is no wave to size)"
        )
    else:
        lines.append(
            f"    run_m1 --replay-threads          : {plan.replay_threads}  (the string replay's own ceiling, {plan.replay_reason})"
        )
    lines.append(f"    corpus-build --jobs              : {plan.corpus_jobs}  ({plan.corpus_reason})")
    if plan.corpus_gates_idle_jobs is not None:
        lines.append(
            f"    corpus-build --gates-idle-jobs   : {plan.corpus_gates_idle_jobs}  ({plan.corpus_gates_idle_reason})"
        )
    lines.append(f"    corpus-build --signature-jobs    : {plan.signature_jobs}  ({plan.signature_reason})")
    lines.append(
        f"    verdict-update --standing-fill-jobs : {plan.standing_fill_jobs}  ({plan.standing_fill_reason})"
    )
    return lines


def plan_rows(plan: Plan) -> list[console.PlanRow]:
    """Return the plan's steps as the console's rows. Only gate:conform can show `run?`, which makes the counts line a range: its skip key covers the artifacts run_m1 writes, so a pass that plans the sweep may still skip it once the build finishes. That row's note comes from `UNDECIDED_UNTIL_RUN_M1` and states the condition.

    A pass that skips run_m1 shows `run` instead, because nothing is rebuilt and `main` has already compared the same key and found no matching green record. A `--fresh` pass also shows `run`, because it reads no green record.
    """
    rows: list[console.PlanRow] = []
    for step in plan.steps:
        undecided = (
            step.name in UNDECIDED_UNTIL_RUN_M1
            and not step.skipped
            and not plan.skip_run_m1
            and not plan.fresh
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
                note=UNDECIDED_UNTIL_RUN_M1[step.name] if undecided else step.note,
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
    lines.append(f"  verdicts     : {plan.verdicts if plan.verdicts is not None else '(none)'}")
    lines.append(f"  carry output : {plan.carry_out if plan.carry_out is not None else '(no carry)'}")
    if plan.review_out is not None:
        lines.append(
            f"  staging      : corpus writes redirected to {plan.review_out}; the live corpus at rebuild/out/review is never written."
        )
    lines.extend(_render_concurrency(plan))
    return lines


@dataclass
class CycleReport:
    """The pass's running record. The `*_status` strings are prose for the summary. The gate strings (`gate_js` and the others) are prose too, but `_step_outcome` reads their leading words (`not run`, `skipped`, `self-skipped`, `green`) to fill the table's outcome column. Decisions read the booleans (`gate_*_green`, `complaints_ok`), which are set when each outcome is decided. A gate that was never joined, because it was skipped or never submitted, leaves its boolean None, which is neither green nor red.

    `step_seconds` and `step_returncodes` fill the summary table's last two columns. They come from the same `_StepResult` the timings journal records, so the table and the journal agree, and they are keyed through STEP_ALIASES, so the gates-only child fills the run_m1 row. `run_m1_failed` records the cycle's own evaluation of the run_m1 gate from the three summary JSONs, which `_step_outcome` reads before the return code. `retention_outcome` is how `_finish`'s retention attempt ended (`ok` or `FAILED`, empty until the attempt ends), so the table reports it even when a stop signal lands after retention.
    """

    unmatched: int | None = None
    multi_matched: int | None = None
    pins_pass: bool | None = None
    corpus_units: int | None = None
    corpus_rows: int | None = None
    corpus_batches: int | None = None
    duplicate_groups: int | None = None
    assets_status: str = "not run"
    seed_status: str = "not run"
    snapshot_status: str = "not run"
    land_status: str = "not run"
    land: dict | None = None
    land_started: float | None = None
    carry_out: Path | None = None
    carry_lines: list[str] = field(default_factory=list)
    carry_counts: dict[str, int] | None = None
    merge_status: str = "not run"
    merge_lines: list[str] = field(default_factory=list)
    duplicate_fill_status: str = "not run"
    duplicate_fill_lines: list[str] = field(default_factory=list)
    duplicate_merge_status: str = "not run"
    duplicate_merge_lines: list[str] = field(default_factory=list)
    standing_fill_status: str = "not run"
    standing_fill_lines: list[str] = field(default_factory=list)
    standing_merge_status: str = "not run"
    standing_merge_lines: list[str] = field(default_factory=list)
    verdict_update_complete: bool = False
    facts_status: str = "not run"
    ledger_coverage: str = "not run"
    ledger_coverage_sets: dict | None = None
    job_costs_status: str = "not run"
    job_costs_ok: bool | None = None
    complaints_status: str = "not run"
    complaints_ok: bool | None = None
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
    run_m1_failed: bool = False
    retention_detail: str = ""
    retention_outcome: str = ""
    step_seconds: dict[str, float] = field(default_factory=dict)
    step_returncodes: dict[str, int] = field(default_factory=dict)


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text())


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


def classify_rebuild_output(stdout: str, returncode: int, check: str) -> CheckResult:
    """Return the rebuild suite's gate result from pytest's FAILED and ERROR summary lines. The cycle's rebuild gate and `rebuild.tools.rebuild_gate` both use it. `check` ("rebuild-contracts") is only copied into the result to name the suite. ANSI escape codes are stripped first, because pytest colors its output whenever FORCE_COLOR is set (the agent harness sets it), and a colored line does not start with "FAILED " or "ERROR ". A nonzero exit with no parsed failure lines is red. Every failure counts as unexplained, because the suite has no list of accepted failures, and every green result is recordable."""
    lines = [_ANSI_SGR.sub("", line) for line in stdout.splitlines()]
    failed_ids = [line.split(None, 2)[1] for line in lines if line.startswith("FAILED ")]
    error_ids = [line.split(None, 2)[1] for line in lines if line.startswith("ERROR ")]
    hard = failed_ids + error_ids
    if returncode != 0 and not hard:
        hard.append(f"pytest exited {returncode} with no parsed FAILED/ERROR lines")
    return CheckResult(
        check=check,
        outcome="red" if hard else "green",
        status=f"FAILED ({len(hard)} unexplained)" if hard else "green",
        failures=[f"rebuild suite: {len(hard)} unexplained failure(s)"] if hard else [],
        failed_ids=hard,
        recordable=not hard,
    )


# The failure reason for a run_m1 that wrote no summaries. The cycle's failure list and the timings journal's check line both use it, so the two record the same text.
_NO_SUMMARIES_REASONS = ("run_m1 did not write all three summary files",)

RUN_M1_GATES_ONLY_STEP = "run_m1:gates-only"

STEP_ALIASES = {RUN_M1_GATES_ONLY_STEP: "run_m1"}


def _do_run_m1(
    report: CycleReport,
    *,
    spawn,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    argv: list[str] | None = None,
    skip: bool = False,
    skip_note: str = "",
    gates_only: bool = False,
    record: bool = False,
    fingerprint: str | None = None,
    timings: CycleTimings | None = None,
) -> CheckResult | None:
    """Run the M1 build, or reuse it when `skip` is set, and evaluate its gate from the three summary JSONs. The skip path leaves rebuild/out/m1 untouched and re-evaluates the summaries on disk, which is sound because run_m1's outputs are deterministic and carry no timestamps. A live green is recorded only if the fingerprint still matches after the run, because an input edited mid-run means the tested content is no longer on disk. A live red whose fingerprint matches the green record deletes the record.

    With `gates_only`, the child is `run_m1 --gates-only` over the tables and font on disk. It rewrites the defect fields of `pipeline_summary.json` in place and exits with an error when that file is missing, so it is the one summary not deleted before the spawn. Its green rests on a prior completed build with only comparison-side changes (`gates_only_rerun`) and tables stamped before and after the child runs (`m1_tables_stamped`). The cycle is the sole green-record writer for its children, and a nonzero exit overrides passing summaries. The child spawns as `RUN_M1_GATES_ONLY_STEP`, not `run_m1`, because `make cycle-timings ARGS='--by-step'` groups rows by step name and host, and a gates-only run of a few seconds recorded as `run_m1` would distort the figures for a full M1 build.

    Every path, the skip included, records a check line in the timings journal, because a skip is an evaluation of this build's summaries. The child records none of its own, because it inherits CYCLE_RUN_ENV. A build that wrote no summaries is recorded red with `_NO_SUMMARIES_REASONS`, the reason the cycle's failure list also gets.
    """
    step = RUN_M1_GATES_ONLY_STEP if gates_only else "run_m1"
    result: _StepResult | None = None
    gates_only_eligible = False
    if skip:
        emit.step_skipped("run_m1", f"{skip_note}; evaluating the gate from the recorded summaries")
    else:
        if gates_only and record and fingerprint is not None:
            prior = read_green_record(cycle_paths.RUN_M1_GREEN)
            current = run_m1_skip_files(ROOT)
            gates_only_eligible = gates_only_rerun(prior, current) is not None and m1_tables_stamped()
        for name, path in cycle_paths.M1_SUMMARY_FILES.items():
            if gates_only and name == "pipeline":
                continue
            path.unlink(missing_ok=True)
        result = spawn(step, argv, emit=emit, registry=registry, stream=False)
    missing = [name for name, path in cycle_paths.M1_SUMMARY_FILES.items() if not path.exists()]
    if missing:
        for name in missing:
            emit.note(
                "run_m1",
                f"run_m1 gate failure: missing {name} summary ({cycle_paths.M1_SUMMARY_FILES[name]}) — run_m1 did not complete",
            )
        if result is not None:
            _close_step(emit, report, step, result, "FAILED (no summaries)")
        if record and fingerprint is not None:
            settle_green(cycle_paths.RUN_M1_GREEN, fingerprint, False, lambda: run_m1_skip_fingerprint(ROOT))
        if timings is not None:
            timings.record_check(
                CheckResult(
                    check="run_m1",
                    outcome="red",
                    status="FAILED (no summaries)",
                    failures=list(_NO_SUMMARIES_REASONS),
                    failed_ids=[],
                )
            )
        return None
    summaries = {name: _load_summary(path) for name, path in cycle_paths.M1_SUMMARY_FILES.items()}
    gate = evaluate_run_m1_gate(summaries["pipeline"], summaries["manual_pins"], summaries["oracle"])
    if result is not None and result.returncode != 0 and gate.ok:
        gate = CheckResult(
            check="run_m1",
            outcome="red",
            status=f"FAILED (exit {result.returncode})",
            failures=[f"run_m1 gate: exited {result.returncode} despite passing summaries"],
            failed_ids=[],
        )
    if timings is not None:
        timings.record_check(gate)
    report.unmatched = summaries["oracle"].get("unmatched")
    report.multi_matched = summaries["oracle"].get("multi_matched")
    report.pins_pass = bool(summaries["manual_pins"].get("pass"))
    if result is not None:
        _close_step(emit, report, step, result, "ok" if gate.ok else "FAILED")
    if record and fingerprint is not None:
        if not gate.ok:
            settle_green(cycle_paths.RUN_M1_GREEN, fingerprint, False, lambda: run_m1_skip_fingerprint(ROOT))
        elif not skip:
            if gates_only and (not gates_only_eligible or not m1_tables_stamped()):
                emit.note(
                    "run_m1",
                    "run_m1 gates-only green, but the prior build or tables stamp does not permit reusing these artifacts — green not recorded",
                )
            elif not settle_green(
                cycle_paths.RUN_M1_GREEN,
                fingerprint,
                True,
                lambda: run_m1_skip_fingerprint(ROOT),
                files_of=lambda: run_m1_skip_files(ROOT),
            ):
                emit.note("run_m1", "run_m1 green, but its inputs changed while it ran — green not recorded")
    return gate


def _run_m1_reasons(gate: CheckResult | None) -> list[str]:
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
    report.duplicate_groups = totals.get("duplicate_groups")
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
    """Tell the tabs open on a listening review server that what it serves changed, with the served manifest's value for `kind`: `ams:corpus/<generated_at>` once a pass's land has moved the served corpus and its store together, so the tabs move onto a corpus whose store is already on it, and `ams:assets/<static hash>` after an assets refresh. The app sends its unsaved changes and reloads when the reader is not typing (rebuild/review/static/reload.js). With no server listening, or a manifest that does not name the value, nothing is sent, and a tab moves on its next save or status check instead. The rebuild suite switches it off with `cycle_paths.REVIEW_RELOAD_ENABLED`, because it goes to the live port."""
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


def _do_corpus_seed(report: CycleReport, *, emit: console.CycleConsole, plan: Plan) -> bool:
    """Give the corpus build its seed at `review.next`: the complete tree an earlier pass left there when the plan kept it, and otherwise a copy-on-write clone of the served corpus (`landing.clone_tree`). The build then reads its prior shards, unit store and signature store from the seed and writes only the clone's blocks, so nothing the open tab reads changes until the land."""
    assert plan.next_corpus is not None

    def seed() -> str:
        target = plan.next_corpus
        assert target is not None
        if plan.seed_kept and corpus_complete(target):
            return f"kept {target}, the corpus an earlier pass built, as the seed"
        shutil.rmtree(target, ignore_errors=True)
        how = landing.clone_tree(REVIEW_OUT, target)
        return f"{'cloned' if how == 'clone' else 'copied'} rebuild/out/review to {target}"

    detail = _in_process_step(report, emit, plan, "corpus-seed", seed)
    report.seed_status = detail if detail is not None else "FAILED"
    return detail is not None


def _do_store_snapshot(report: CycleReport, *, emit: console.CycleConsole, plan: Plan) -> bool:
    """Copy the live store into the pass's scratch directory as the snapshot and the store the verdict update prepares (`landing.snapshot`), holding the store's lock for the copy."""
    assert plan.scratch_dir is not None
    scratch = plan.scratch_dir

    def take() -> str:
        stamp = landing.snapshot(AUTOSAVE, scratch)
        return "no verdict store yet" if stamp is None else f"snapshot of the store on {stamp}"

    detail = _in_process_step(report, emit, plan, "store-snapshot", take)
    report.snapshot_status = detail if detail is not None else "FAILED"
    return detail is not None


def _land_report(plan: Plan) -> dict | None:
    if plan.scratch_dir is None:
        return None
    try:
        data = json.loads((plan.scratch_dir / landing.REPORT_NAME).read_text())
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def land_detail(data: dict) -> str:
    """Return the land row's detail from the report the land wrote: the stamp it landed and what it laid over, orphaned and dropped."""
    parts = [f"landed {console.fmt_count(data.get('records') or 0)} verdicts on {data.get('new_stamp')}"]
    for key, label in (
        ("overlaid", "laid over from saves made during the pass"),
        ("orphaned", "orphaned"),
        ("skips_dropped", "skips dropped"),
    ):
        if data.get(key):
            parts.append(f"{console.fmt_count(data[key])} {label}")
    if data.get("locked_s") is not None:
        parts.append(f"store locked {data['locked_s']:.1f} s")
    return ", ".join(parts)


def _do_land(
    report: CycleReport, *, spawn, emit: console.CycleConsole, registry: _ChildRegistry, plan: Plan
) -> dict | None:
    """Run the land as its own child (`rebuild.review.landing`), which neither a Ctrl-C nor a signal to the cycle's group reaches, and which the registry waits for rather than signals. Returns the report the land wrote when it landed, and None when it aborted or failed. A land that died inside its locked section leaves an intent file; the cycle finishes that land at once, holding the store's lock, so the pass never ends with a half-moved corpus or store (`landing.finish_interrupted_land`)."""
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
    if landing.intent_path_for(AUTOSAVE).exists():
        recovery = landing.finish_interrupted_land(AUTOSAVE, quiet=True)
        if recovery is not None:
            emit.note("land", f"{recovery.message}, under the verdict store's lock")
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
    """Rebuild the review corpus, or reuse it when `skip` is set. Both paths read the four totals from the corpus's manifest.json, whose unit and row totals the build checks against the shards it wrote, so the summary reports what the corpus on disk contains."""
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


_VERDICT_UPDATE_FAILURES = {
    "carry": "carry_verdicts failed",
    "merge": "verdict merge failed",
    "duplicate-fill": "duplicate-fill failed",
    "duplicate-merge": "duplicate-merge failed",
    "standing-fill": "standing-fill failed",
    "standing-merge": "standing-merge failed",
}


def verdict_update_sections(text: str) -> dict[str, list[str]]:
    """Split the verdict update's output into sections at the `[phase] <step>` line each step starts with, so the summary can report each of the verdict update's steps although one subprocess runs them all. Its `[verdict-update] complete:` and `[verdict-update] failed:` result lines close the open section without opening one, which keeps result lines out of the complaints section. The second duplicate pass (`duplicate-fill-2` and `duplicate-merge-2`) is merged into the first pass's sections."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith(console.COMPLETE_LINE) or line.startswith(console.FAILED_LINE):
            current = None
            continue
        if line.startswith(console.PHASE):
            current = re.sub(r"-\d+$", "", line[len(console.PHASE) :].strip())
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _scrape(lines: list[str], keep) -> list[str]:
    return [line.strip() for line in lines if keep(line.strip())]


def _standing_fill_news(line: str) -> bool:
    """Return whether the summary keeps this line of the standing fill's output. It keeps the `wrote` line, the disputed-match warning (so an over-broad rule shows in cycle_summary.json), every per-rule line (so a newly added rule shows even at 0 filled), and the combined-match lines that filled or blocked something. Combined-match lines grow quadratically with the rule count, so the rest are left to `standing_probe --coverage`. The REACHED NOTHING lines are left out because `--require-reach` fails the step on such a rule, and the except_left vocabulary line is informational. The already-verdicted column is optional because the verdict update runs the fill with `--open-only`, which omits it, while a dry run over the whole domain prints it."""
    if line.startswith("wrote ") and "standing-approval verdicts" in line:
        return True
    if line.startswith("WARNING:"):
        return True
    if not line.endswith("blocked by except_left, left for review"):
        return False
    head, _, tail = line.partition(": ")
    if " + " not in head:
        return True
    match = re.match(r"(\d+) filled, (?:\d+ already verdicted, )?(\d+) blocked", tail)
    return match is not None and (int(match.group(1)) > 0 or int(match.group(2)) > 0)


def _do_verdict_update(
    report: CycleReport, *, spawn, emit: console.CycleConsole, registry: _ChildRegistry, plan: Plan
) -> list[str]:
    """Run the verdict update as one child and fill the per-step report from its output. Return the failure messages for the cycle's failure list, one per failed step.

    A failure in the second duplicate pass (`duplicate-fill-2` or `duplicate-merge-2`) comes after the first duplicate pass and the standing fill and merge have run. Those steps keep their done words, and the failing step's status adds the round, as in `filled, round 2 FAILED (exit 1)`. Only a first-pass failure marks the steps after it as not run.
    """
    result = spawn("verdict-update", plan.argv("verdict-update"), emit=emit, registry=registry, stream=False)
    report.carry_out = plan.carry_out if plan.carry_out is not None else fullest_verdicts_carry_out()
    sections = verdict_update_sections(result.stdout)
    failed = ""
    failed_round = 1
    for line in result.stdout.splitlines():
        if line.startswith(console.FAILED_LINE):
            failed = line[len(console.FAILED_LINE) :].split(" ", 1)[0]
            failed_round = 1
            if match := re.fullmatch(r"(.+)-(\d+)", failed):
                failed, failed_round = match.group(1), int(match.group(2))
    later_round = failed_round > 1
    report.verdict_update_complete = (
        result.returncode == 0
        and not failed
        and any(
            line == console.COMPLETE_LINE + "duplicate, standing, duplicate"
            for line in result.stdout.splitlines()
        )
    )

    report.carry_lines = _scrape(
        sections.get("carry", []),
        lambda line: any(word in line for word in ("carried", "kinds", "queue", "fallback", "carry counts")),
    )
    report.carry_counts = carry_counts(report.carry_lines)
    for name in ("merge", "duplicate-merge", "standing-merge"):
        setattr(
            report,
            name.replace("-", "_") + "_lines",
            _scrape(
                sections.get(name, []),
                lambda line: line.startswith(("merged ", "nothing changed", "stashed ")),
            ),
        )
    report.duplicate_fill_lines = _scrape(
        sections.get("duplicate-fill", []),
        lambda line: line.startswith("wrote ") and "duplicate-fill verdicts" in line,
    )
    report.standing_fill_lines = _scrape(sections.get("standing-fill", []), _standing_fill_news)

    # After a first-round failure, the steps after the failed one never ran. Steps before it ran and report what they did.
    done = (
        ("merge", "merged"),
        ("duplicate-fill", "filled"),
        ("duplicate-merge", "merged"),
        ("standing-fill", "filled"),
        ("standing-merge", "merged"),
    )
    order = ["carry", *(name for name, _word in done)]
    blocked = order.index(failed) if failed in order and not later_round else len(order)
    for name, word in done:
        if order.index(name) > blocked:
            status = f"not run ({failed} failed)"
        elif name == failed and not later_round:
            status = f"FAILED (exit {result.returncode})"
        elif name in sections:
            status = word
        else:
            status = "not run"
        if name == failed and later_round:
            status += f", round {failed_round} FAILED (exit {result.returncode})"
        setattr(report, name.replace("-", "_") + "_status", status)
    failures: list[str] = []
    if failed in _VERDICT_UPDATE_FAILURES and later_round:
        failures.append(f"{failed} round {failed_round} failed")
    elif failed in _VERDICT_UPDATE_FAILURES:
        failures.append(_VERDICT_UPDATE_FAILURES[failed])
    elif result.returncode != 0 and failed != "complaints":
        failures.append(f"the verdict update failed (exit {result.returncode})")

    if "complaints" in sections:
        _read_complaints(report, sections["complaints"], result.returncode if failed == "complaints" else 0)
    _close_step(emit, report, "verdict-update", result)
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


def accepted_facts() -> dict | None:
    """Return the last accepted review facts: the git index copy of the pins, which the working tree's rewrite is compared with at commit time. Return None when the file is not in the index or git is unavailable."""
    try:
        shown = subprocess.run(
            ["git", "show", f":{FACTS_PINS.relative_to(ROOT).as_posix()}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if shown.returncode != 0 or not shown.stdout.strip():
        return None
    return json.loads(shown.stdout)


def _do_review_facts(
    report: CycleReport, *, spawn, emit: console.CycleConsole, registry: _ChildRegistry, plan: Plan
) -> None:
    """Rewrite the review-facts pins from the corpus's review-facts.json sidecar and report what changed against the last accepted review facts (`accepted_facts`). When only the volatile block changed, the status says the invariant is unchanged. When the invariant block changed, the status lists the changes (`facts.invariant_delta`) and the invariant block's diff is printed under the banner without the volatile hunks. The volatile block changes with nearly every letter, so a full diff on every pass would teach a reader to ignore it.

    The step also reports ledger coverage: the ledger's `ink_identical` and `no_verdict` declarations compared with the classes the corpus reached and machine-approved. Neither the ledger nor the pins shows on its own that a declared class went unreached or that an undeclared class started approving units.

    The step records no green and never fails the cycle. A failed refresh (for example over a corpus built before the sidecar existed) is reported and left for the next pass that rebuilds the corpus.
    """
    refresh = spawn("review-facts", plan.argv("review-facts"), emit=emit, registry=registry, stream=False)
    if refresh.returncode != 0:
        report.facts_status = f"update FAILED (exit {refresh.returncode}) — informational"
        report.ledger_coverage = "not computed (the refresh failed)"
        _close_step(emit, report, "review-facts", refresh, "ok")
        return
    current = json.loads(FACTS_PINS.read_text(encoding="utf-8"))
    coverage = facts.ledger_coverage(load_ledger(DIVERGENCE_LEDGER), current["invariant"])
    report.ledger_coverage = coverage.describe()
    report.ledger_coverage_sets = coverage.as_json()
    accepted = accepted_facts()
    if accepted is None:
        report.facts_status = (
            "updated (no accepted review facts to compare against: the pins are not in the index)"
        )
        _close_step(emit, report, "review-facts", refresh, "ok")
        return
    findings = facts.invariant_delta(accepted.get("invariant", {}), current["invariant"])
    if findings:
        emit.substep(SUBSTEP_PARENTS["invariant-diff"], "invariant-diff")
        for line in facts.invariant_diff(accepted.get("invariant", {}), current["invariant"]):
            emit.child_line("invariant-diff", console.STDOUT, line)
            emit.emit(line)
        emit.substep_end("invariant-diff")
        report.facts_status = (
            f"invariant moved: {'; '.join(findings)} — its diff is shown above; review it at commit time"
        )
    elif accepted.get("volatile") != current.get("volatile"):
        report.facts_status = (
            "invariant unchanged (only the volatile totals moved; cycle_summary.json carries the corpus's)"
        )
    else:
        report.facts_status = "updated (matches the last accepted review facts)"
    _close_step(emit, report, "review-facts", refresh, "ok")


_MOVED_CONSTANT = re.compile(r"^([A-Z][A-Z0-9_]*): ")
_TRIPPED_PROPOSAL = re.compile(r"^  proposal  : (?!.*, headroom used, not yet an overrun;)(.+)$")


def _do_job_costs(
    report: CycleReport, *, spawn, emit: console.CycleConsole, registry: _ChildRegistry, plan: Plan
) -> None:
    """Compare the checked-in per-unit memory peaks with what this machine has measured (`calibrate_budgets --check`). Several pool widths are the machine's memory divided by one of these constants, so a stale constant makes a pool the wrong width. The step runs after the gates join because it reads the timings journal, which this pass's pools have just appended to.

    It never fails the pass: a wrong width costs wall-clock time or swap but cannot make an artifact wrong. The summary line and `job_costs_ok` report an overrun, and committing the re-measured constant accepts it, as committing the review-facts pins accepts the review facts. A check that cannot run is reported as informational too.

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
        ["uv", "run", "python", "-m", "rebuild.tools.calibrate_budgets", "--moved"],
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


def _skip_verdict_update(report: CycleReport, plan: Plan, emit: console.CycleConsole) -> None:
    """Report the verdict-update step as skipped, marking each of its steps skipped. The carried file the last recorded pass wrote is still the stamp-aligned fullest verdicts file, because the corpus it was carried onto has not changed, so the report still names it."""
    emit.step_skipped("verdict-update", plan.verdict_update_note)
    note = f"skipped ({plan.verdict_update_note})"
    report.carry_out = fullest_verdicts_carry_out()
    report.merge_status = note
    report.duplicate_fill_status = note
    report.duplicate_merge_status = note
    report.standing_fill_status = note
    report.standing_merge_status = note


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
    """Return a spawn callable that overlays `env` on one child's environment. Setting the variables in os.environ would pass them to every child this process spawns (run_m1, the corpus build, the rebuild suite), so one gate's pytest width would also apply to the others' `-n auto` pools. The wrapper changes only the environment, never the argv, so the plan stays the only source of each child's command."""

    def spawn_with_env(name, argv, *, emit, registry, stream):
        return spawn(name, argv, emit=emit, registry=registry, stream=stream, env=env)

    return spawn_with_env


def _gate_conform_task(
    pool_policy: str,
    make_fut: Future | None,
    spawn,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    argv: list[str],
) -> CheckResult:
    """Run gate:conform, the exhaustive font-versus-settlement sweep over the fresh M1.otf (`run_m1 --conform-only`), and return its result. Under the default overlap policy it starts as soon as it is submitted, once the run_m1 gate has passed, and runs beside whichever of gate:make-test and the rebuild suite the pass runs. Under the queue policy it waits for gate:make-test, and the rebuild suite waits for it, so only one heavy pool runs at a time. The module docstring gives the reason the overlap policy is the default. The previous conform_summary.json is deleted just before the sweep starts, so the result can only come from this pass's sweep. A skipped gate never runs this task and leaves the file alone."""
    cycle_paths.CONFORM_SUMMARY.unlink(missing_ok=True)
    if pool_policy == "queue":
        _await_gate_futures(make_fut)
    result = spawn("gate:conform", argv, emit=emit, registry=registry, stream=False)
    summary = None
    if cycle_paths.CONFORM_SUMMARY.exists():
        try:
            summary = json.loads(cycle_paths.CONFORM_SUMMARY.read_text())
        except ValueError:
            summary = None
    gate = evaluate_conform_gate(summary)
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


class _GateLaneWatch:
    """Write `marker` once every gate pool the pass submitted (gate:make-test, gate:conform and gate:rebuild-contracts; None for a gate that was skipped) has finished, however it finished, so the corpus build can read the gate lane's state when its units pool starts (`corpus_gates_idle_job_budget`). It is armed just before the build is spawned, after every gate the pass runs has been submitted, so a gate still queued behind another under the queue policy holds the marker back too. The marker is deleted first, so one an earlier pass left cannot widen this build, and `close` deletes it again and disarms the watch, so a gate that finishes after the build has exited writes nothing."""

    def __init__(self, marker: Path, futures: Iterable[Future | None]) -> None:
        self._marker = marker
        self._lock = threading.Lock()
        self._armed = True
        self._pending = [fut for fut in futures if fut is not None]
        marker.unlink(missing_ok=True)
        marker.parent.mkdir(parents=True, exist_ok=True)
        if not self._pending:
            self._write()
        for fut in self._pending:
            fut.add_done_callback(self._finished)

    def _finished(self, _fut: Future) -> None:
        if all(fut.done() for fut in self._pending):
            self._write()

    def _write(self) -> None:
        with self._lock:
            if self._armed:
                self._marker.touch()

    def close(self) -> None:
        with self._lock:
            self._armed = False
            self._marker.unlink(missing_ok=True)


def _gate_contracts_task(
    pool_policy: str,
    conform_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: console.CycleConsole,
    registry: _ChildRegistry,
    argv: list[str],
    force: bool = False,
    record: bool = False,
) -> CheckResult:
    """Run gate:rebuild-contracts, the rebuild suite (every test under rebuild/, none of which reads a live build artifact), and return its result. Because it reads no build output, it is submitted right after gate:conform, once the run_m1 gate has passed, and runs beside the corpus build at the width `contracts_pool_width` sets on its environment: the cores the build's parent and workers leave free, less gate:make-test's pool under the overlap policy on a pass that runs that gate. Under the overlap policy it shares those cores with gate:conform's sweep. Under the queue policy it also waits for gate:make-test and gate:conform, so only one heavy gate pool runs at a time."""
    if pool_policy == "queue":
        _await_gate_futures(conform_fut, make_fut)
    run = contracts.prepare_run(ROOT, force)
    if run.skippable:
        emit.step_skipped("gate:rebuild-contracts", "input closure unchanged since its last green run")
        return CheckResult(
            check="rebuild-contracts",
            outcome="skipped",
            status="skipped (input closure unchanged since its last green run)",
            failures=[],
            failed_ids=[],
        )
    emit.note("gate:rebuild-contracts", run.selection.describe())
    contracts.start_run(run)
    try:
        result = spawn("gate:rebuild-contracts", argv, emit=emit, registry=registry, stream=False)
    except Exception:
        if record:
            contracts.finish_run(ROOT, run, False)
        raise
    gate = classify_rebuild_output(result.stdout, result.returncode, "rebuild-contracts")
    if record:
        finished = contracts.finish_run(ROOT, run, gate.ok)
        if finished.status == "drifted":
            emit.note(
                "gate:rebuild-contracts",
                "gate:rebuild-contracts green, but its input closure changed while the cycle ran — green not recorded",
            )
        elif finished.status == "unavailable":
            emit.note(
                "gate:rebuild-contracts", "closure fingerprint unavailable without git — green not recorded"
            )
    _close_gate(emit, "gate:rebuild-contracts", result, gate)
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
    gate = _gate_result(fut, "gate:rebuild-contracts", failures)
    if gate is None:
        status, green = "FAILED (exception)", False
    else:
        status, green = gate.status, None if gate.outcome == "skipped" else gate.ok
        report.contracts_proven = gate.outcome == "skipped"
        for test_id in gate.failed_ids:
            emit.note("gate:rebuild-contracts", f"hard rebuild failure (contracts): {test_id}")
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


def _verdict_update_completed(report: CycleReport) -> bool:
    """Return whether the verdict update successfully completed its fixed duplicate, standing, duplicate schedule. The parsed completion requires both the child's successful exit and its completion line, with no failed phase. Each duplicate pass fills disjoint scalar groups fully, and the second fills blank siblings the standing pass seeds. Completion allows merges to keep newer tombstones instead of accepting every proposed fill."""
    return report.verdict_update_complete


def _record_conform_green(
    report: CycleReport, plan: Plan, key: str | None, emit: console.CycleConsole
) -> None:
    """Publish the joined conformance result against the key captured after run_m1. A passing sweep records only while its key still matches, and a failure clears only a record for that key. The contracts task finalizes its own input snapshot through the shared contracts lifecycle."""
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
    """Wrap `spawn` so each child's wall-clock time and exit status are recorded on the report under its plan step, for the summary table's last two columns. The name goes through STEP_ALIASES, so `run_m1 --gates-only` fills the run_m1 row. It is a wrapper, not part of `_run_step`, because the tests drive the cycle with their own spawn callables and the table must be filled for them too."""

    def recorded(name: str, argv, *, emit, registry, stream, **passthrough):
        result = spawn(name, argv, emit=emit, registry=registry, stream=stream, **passthrough)
        step = STEP_ALIASES.get(name, name)
        report.step_seconds[step] = result.elapsed
        report.step_returncodes[step] = result.returncode
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
        make_fut = (
            None
            if plan.skip_gates or plan.skip_make_test
            else pool.submit(
                _gate_make_test_task,
                plan.argv("gate:make-test"),
                _spawn_with_env(spawn, {"PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.make_test_workers)}),
                emit,
                registry,
            )
        )
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
            emit.step_skipped("gate:rebuild-contracts", plan.contracts_note)
        if not plan.skip_gates and plan.skip_make_test:
            report.gate_make_test = f"skipped ({plan.make_test_note})"
            emit.step_skipped("gate:make-test", plan.make_test_note)

        gate = _do_run_m1(
            report,
            spawn=spawn,
            emit=emit,
            registry=registry,
            argv=None if plan.skip_run_m1 else plan.argv("run_m1"),
            skip=plan.skip_run_m1,
            skip_note=plan.run_m1_note,
            gates_only=plan.rerun_gates_only,
            record=plan.record_greens,
            fingerprint=plan.run_m1_fingerprint,
            timings=timings,
        )
        if gate is None or not gate.ok:
            failures.extend(_run_m1_reasons(gate))
            report.run_m1_failed = True
            if plan.skip_gates or not plan.skip_contracts:
                report.gate_contracts = "not run (run_m1 gate failed)"
                emit.step_not_run("gate:rebuild-contracts", "run_m1 gate failed")
            if not plan.skip_gates and not plan.skip_conform:
                report.gate_conform = "not run (run_m1 gate failed)"
                emit.step_not_run("gate:conform", "run_m1 gate failed")
            _join_gates(report, failures, js_fut, None, None, make_fut, emit, timings)
            return _finish(report, failures, plan, timings, emit)

        if not plan.skip_gates and not plan.skip_conform:
            current_conform_key = conform_skip_fingerprint(ROOT, plan.conform_max_length)
            green = None if plan.fresh else read_green_record(cycle_paths.CONFORM_GREEN)
            if green is not None and green["fingerprint"] == current_conform_key:
                report.conform_proven = True
                report.gate_conform = f"skipped ({CONFORM_SKIP_NOTE})"
                emit.step_skipped(
                    "gate:conform",
                    f"SKIPPED after run_m1 — {CONFORM_SKIP_NOTE}. The lookup this pass emits asks HarfBuzz for no shape its last green sweep did not shape, and run_m1's string replay has held the tables to the engine over every swept text.",
                )
            else:
                if plan.record_greens:
                    conform_key = current_conform_key
                conform_fut = pool.submit(
                    _gate_conform_task,
                    plan.pool_policy,
                    make_fut,
                    spawn,
                    emit,
                    registry,
                    plan.argv("gate:conform"),
                )

        if not plan.skip_gates and (not plan.skip_contracts or plan.contracts_run is not None):
            contracts_fut = pool.submit(
                _gate_contracts_task,
                plan.pool_policy,
                conform_fut,
                make_fut,
                _spawn_with_env(
                    spawn,
                    {
                        "AMS_POOL_UNIT": "rebuild-contracts",
                        "PYTEST_XDIST_AUTO_NUM_WORKERS": str(plan.contracts_workers),
                    },
                ),
                emit,
                registry,
                contracts_argv() if plan.skip_contracts else plan.argv("gate:rebuild-contracts"),
                plan.fresh,
                plan.record_greens,
            )

        def stop_here(failure: str) -> int:
            failures.append(failure)
            _join_gates(report, failures, js_fut, contracts_fut, conform_fut, make_fut, emit, timings)
            _record_conform_green(report, plan, conform_key, emit)
            return _finish(report, failures, plan, timings, emit)

        served_before = _served_generated_at() if plan.review_out is None else None
        if plan.includes("corpus-seed") and not _do_corpus_seed(report, emit=emit, plan=plan):
            return stop_here("corpus seed failed")

        if plan.runs("assets-refresh") and not _do_assets_refresh(
            report, spawn=spawn, emit=emit, registry=registry, plan=plan
        ):
            return stop_here("assets refresh failed")

        gate_lane_watch = (
            _GateLaneWatch(plan.gates_idle_marker, (make_fut, conform_fut, contracts_fut))
            if plan.gates_idle_marker is not None and not plan.skip_corpus
            else None
        )
        try:
            built = _do_corpus_build(
                report,
                spawn=spawn,
                emit=emit,
                registry=registry,
                review_out=plan.update_corpus,
                argv=None if plan.skip_corpus else plan.argv("corpus-build"),
                skip=plan.skip_corpus,
                skip_note=plan.corpus_note,
            )
        finally:
            if gate_lane_watch is not None:
                gate_lane_watch.close()
        if not built:
            return stop_here("corpus rebuild failed")

        if plan.includes("store-snapshot") and not _do_store_snapshot(report, emit=emit, plan=plan):
            return stop_here("store snapshot failed")

        verdict_update_failures: list[str] = []
        if plan.skip_verdict_update:
            _skip_verdict_update(report, plan, emit)
        elif plan.runs("verdict-update"):
            verdict_update_failures = _do_verdict_update(
                report, spawn=spawn, emit=emit, registry=registry, plan=plan
            )
            failures.extend(verdict_update_failures)
        landed: dict | None = None
        if plan.runs("land"):
            if verdict_update_failures:
                report.land_status = "not run (the verdict update failed)"
                emit.step_not_run("land", "the verdict update failed, so nothing moves")
            else:
                landed = _do_land(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
                if landed is None:
                    failures.append("land failed")
        if plan.review_out is None and plan.broadcast and _served_generated_at() != served_before:
            _broadcast_review_reload(emit, "land" if plan.runs("land") else "corpus-build", "corpus")
            served_before = _served_generated_at()
        _delete_discard(landed)
        verdict_update_key: str | None = None
        if (
            plan.runs("verdict-update")
            and not verdict_update_failures
            and plan.do_merge
            and _verdict_update_completed(report)
            and landed is not None
            and not landed.get("overlaid")
            and plan.scratch_dir is not None
        ):
            verdict_update_key = verdict_update_skip_fingerprint(
                ROOT, REVIEW_OUT, plan.verdicts, store=plan.scratch_dir / landing.LANDED_NAME
            )
        if plan.complaints_note:
            report.complaints_status = f"skipped ({plan.complaints_note})"
        if plan.review_out is not None:
            report.facts_status = "skipped (staging: the checked-in pins track the live corpus)"
            report.ledger_coverage = "skipped (staging)"
            emit.step_skipped("review-facts", "staging: the checked-in pins track the live corpus")
        elif plan.next_corpus is not None and landed is None:
            report.facts_status = (
                "not run (the new corpus did not land, so the pins keep the served corpus's review facts)"
            )
            report.ledger_coverage = "not computed (the new corpus did not land)"
            emit.step_not_run(
                "review-facts", "the new corpus did not land, so the served corpus's review facts stand"
            )
        else:
            _do_review_facts(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        if (
            verdict_update_key
            and report.complaints_ok is True
            and plan.record_greens
            and plan.review_out is None
        ):
            record_verdict_update_green(verdict_update_key)

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


INFORMATIONAL_STEPS = ("review-facts", "job-costs")

_CARRY_WROTE = re.compile(r"^wrote \S+: (\d+) carried onto manifest")
_CARRY_QUEUE = re.compile(r"^human queue: (\d+) -> (\d+)")
_CARRY_COUNTS = re.compile(r"^carry counts: human=(\d+) matched=(\d+) unmatched=(\d+) orphaned=(\d+)$")


def carry_counts(lines: list[str]) -> dict[str, int] | None:
    """Parse the carry's `carry counts:` line: the human units on the new corpus, how many a prior verdict matched, how many none matched, and how many prior verdicts matched no unit. The counts are written to the cycle summary and to the run line in the timings journal. Returns None when the carry printed no such line (a direct merge, a staging pass, or a verdict update that failed before the carry)."""
    for line in lines:
        match = _CARRY_COUNTS.match(line)
        if match is not None:
            return dict(zip(("human", "matched", "unmatched", "orphaned"), map(int, match.groups())))
    return None


def carry_detail(lines: list[str]) -> str:
    """Summarize the carry from its two headline lines: how many verdicts it carried onto the new corpus, and the human queue before and after. The verdict update runs as one child with the carry as a step inside it, so these counts reach this process only as printed lines. Returns the empty string when the carry printed neither line (a direct merge, a staging pass, or a verdict update that failed before the carry)."""
    carried = ""
    queue = ""
    for line in lines:
        wrote = _CARRY_WROTE.match(line)
        if wrote is not None:
            carried = f"{console.fmt_count(int(wrote.group(1)))} carried"
        pending = _CARRY_QUEUE.match(line)
        if pending is not None:
            queue = (
                f"queue {console.fmt_count(int(pending.group(1)))} -> "
                f"{console.fmt_count(int(pending.group(2)))}"
            )
    return ", ".join(part for part in (carried, queue) if part)


_GATE_STATUS_FIELDS = {
    "gate:js": "gate_js",
    "gate:rebuild-contracts": "gate_contracts",
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

    if name == "run_m1":
        parts = [f"{count(report.unmatched)} unmatched" if report.unmatched is not None else ""]
        if report.pins_pass is not None:
            parts.append("pins pass" if report.pins_pass else "PINS FAILED")
        return ", ".join(part for part in parts if part)
    if name == "corpus-build":
        parts = []
        if report.corpus_units is not None:
            parts.append(f"{count(report.corpus_units)} units")
        if report.corpus_rows is not None:
            parts.append(f"{count(report.corpus_rows)} rows")
        return ", ".join(parts)
    if name == "assets-refresh":
        return prose(report.assets_status)
    if name == "corpus-seed":
        return prose(report.seed_status)
    if name == "store-snapshot":
        return prose(report.snapshot_status)
    if name == "land":
        return prose(report.land_status)
    if name == "verdict-update":
        head = carry_detail(report.carry_lines)
        if not head:
            merged = prose(report.merge_status)
            head = f"merge {merged}" if merged else ""
        return f"{head}; {report.complaints_status}" if head else ""
    if name == "review-facts":
        return prose(report.facts_status)
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
    """Print a spawned step's closing line with its detail. `outcome` defaults to one derived from the child's exit status; callers that decide it otherwise pass their own (run_m1 decides from its summaries, and review-facts and job-costs always pass `ok` because they gate nothing)."""
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

    A gate's outcome comes from its status string. Any other step that ran takes its outcome from run_m1's failure flag or the child's exit code, since having a recorded time only shows that it ran. A nonzero exit from review-facts or job-costs (`INFORMATIONAL_STEPS`) is not a failure, because they gate nothing and their details report the problem.

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
    if step.name == "run_m1" and report.run_m1_failed:
        return "FAILED"
    returncode = report.step_returncodes.get(step.name)
    if returncode and step.name not in INFORMATIONAL_STEPS:
        return "FAILED"
    return "ok" if step.name in report.step_seconds else "not run"


def summary_rows(report: CycleReport, plan: Plan, *, retention_ran: bool) -> list[console.SummaryRow]:
    """Return one summary-table row per planned step. A step that did not run gets no detail, because the report can still hold the previous build's counts for it (a skipped run_m1's unmatched count, a skipped corpus build's totals)."""
    rows: list[console.SummaryRow] = []
    for step in plan.steps:
        outcome = _step_outcome(report, plan, step, retention_ran=retention_ran)
        ran = outcome not in ("skipped", "not run")
        rows.append(
            console.SummaryRow(
                number=None,
                name=step.name,
                outcome=outcome,
                detail=_detail_beside(outcome, step_detail(report, step.name)) if ran else "",
                seconds=report.step_seconds.get(step.name),
            )
        )
    return rows


def summary_cycle_lines(report: CycleReport, plan: Plan, retention_lines: list[str]) -> list[str]:
    """Return the summary lines below the table: output paths, the verdict update's per-step status, the deep sweep and deep replay status, and what retention pruned.

    The lines scraped from the verdict update's output (by `_do_verdict_update`, with `_standing_fill_news` for the standing fill) are indented under the carry and verdict-update lines, so the summary shows what each carry, fill, and merge wrote and how the human queue changed.
    """

    def show(value: object) -> str:
        return "—" if value is None else str(value)

    def news(lines: list[str]) -> list[str]:
        return [f"      {line}" for line in lines]

    deep_status, deep_note = _deep_sweep_report()
    replay_status, replay_note = _deep_replay_report()
    lines = [
        f"  carry output     : {show(report.carry_out)}",
        *news(report.carry_lines),
        f"  verdict update   : merge {report.merge_status}; duplicate-fill {report.duplicate_fill_status}; duplicate-merge {report.duplicate_merge_status}; standing-fill {report.standing_fill_status}; standing-merge {report.standing_merge_status}",
        *news(
            report.merge_lines
            + report.duplicate_fill_lines
            + report.duplicate_merge_lines
            + report.standing_fill_lines
            + report.standing_merge_lines
        ),
        f"  complaint groups : {report.complaints_status}",
        f"  review facts     : {report.facts_status}",
        f"  ledger coverage  : {report.ledger_coverage}",
        f"  job costs        : {report.job_costs_status}",
        f"  deep sweep       : {deep_status} ({deep_note})",
        f"  deep replay      : {replay_status} ({replay_note})",
        "  run_m1 summaries :",
        *(f"      {path}" for path in cycle_paths.M1_SUMMARY_FILES.values()),
        f"      {cycle_paths.CONFORM_SUMMARY}",
    ]
    if plan.log_dir is not None:
        lines.append(f"  logs             : {plan.log_dir}")
    if retention_lines:
        lines.extend(["", *retention_lines])
    return lines


def _as_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _gate_entry(status: str, green: bool | None, skip: str | None = None) -> dict:
    """Return a gate's cycle-summary entry. `green` is the result the gate recorded when it was joined, True only when it ran in this pass and passed; a gate that was never joined passes None and is written as False. `status` is prose for people and is not parsed for greenness.

    `skip` says why the gate did not run, and the readiness check in `rebuild/review/status.py` reads it: "proved" means a matching green record already showed this content passing, and "forced" means a flag suppressed the gate. Both kinds of status string start with "skipped", so the status alone cannot tell them apart.
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
            "rebuild_contracts": _gate_entry(
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
        "unmatched": report.unmatched,
        "multi_matched": report.multi_matched,
        "pins_pass": report.pins_pass,
        "corpus_units": report.corpus_units,
        "corpus_rows": report.corpus_rows,
        "corpus_batches": report.corpus_batches,
        "assets_status": report.assets_status,
        "seed_status": report.seed_status,
        "snapshot_status": report.snapshot_status,
        "land_status": report.land_status,
        "land": report.land,
        "duplicate_groups": report.duplicate_groups,
        "carry_out": _as_str(report.carry_out),
        "carry_lines": list(report.carry_lines),
        "carry": report.carry_counts,
        "merge_status": report.merge_status,
        "merge_lines": list(report.merge_lines),
        "duplicate_fill_status": report.duplicate_fill_status,
        "duplicate_fill_lines": list(report.duplicate_fill_lines),
        "duplicate_merge_status": report.duplicate_merge_status,
        "duplicate_merge_lines": list(report.duplicate_merge_lines),
        "standing_fill_status": report.standing_fill_status,
        "standing_fill_lines": list(report.standing_fill_lines),
        "standing_merge_status": report.standing_merge_status,
        "standing_merge_lines": list(report.standing_merge_lines),
        "facts_status": report.facts_status,
        "ledger_coverage": report.ledger_coverage,
        "ledger_coverage_sets": report.ledger_coverage_sets,
        "job_costs_status": report.job_costs_status,
        "job_costs_ok": report.job_costs_ok,
        "complaints_status": report.complaints_status,
        "log_dir": _as_str(plan.log_dir),
        "interrupted": report.interrupted,
        "plan": {
            "verdicts": _as_str(plan.verdicts),
            "carry_out": _as_str(plan.carry_out),
            "do_merge": plan.do_merge,
            "conform_max_length": plan.conform_max_length,
            "kernel_threads": None if plan.rerun_gates_only else plan.kernel_threads,
            "overlap_memo_writes": None if plan.rerun_gates_only else plan.overlap_memo_writes,
            "scratch_beside_default": None if plan.rerun_gates_only else plan.scratch_beside_default,
            "replay_threads": None if plan.rerun_gates_only else plan.replay_threads,
            "sweep_jobs": plan.sweep_jobs if plan.runs("run_m1") else None,
            "corpus_jobs": plan.corpus_jobs if plan.runs("corpus-build") else None,
            "corpus_gates_idle_jobs": plan.corpus_gates_idle_jobs if plan.runs("corpus-build") else None,
            "signature_jobs": plan.signature_jobs if plan.runs("corpus-build") else None,
            "standing_fill_jobs": plan.standing_fill_jobs if plan.runs("verdict-update") else None,
            "make_test_workers": plan.make_test_workers if plan.runs("gate:make-test") else None,
            "contracts_workers": (
                plan.contracts_workers
                if report.gate_contracts_green is not None
                or (plan.runs("gate:rebuild-contracts") and not report.contracts_proven)
                else None
            ),
            "conform_jobs": (
                plan.conform_jobs if plan.runs("gate:conform") and not report.conform_proven else None
            ),
            "pool_policy": plan.pool_policy,
            "skip_gates": plan.skip_gates,
            "skip_conform": plan.skip_conform,
            "skip_run_m1": plan.skip_run_m1,
            "rerun_gates_only": plan.rerun_gates_only,
            "skip_corpus": plan.skip_corpus,
            "refresh_assets": plan.refresh_assets,
            "promote_corpus": _as_str(plan.promote_corpus),
            "next_corpus": _as_str(plan.next_corpus),
            "land": plan.land,
            "skip_contracts": report.contracts_proven
            or (plan.skip_contracts and report.gate_contracts_green is None),
            "skip_verdict_update": plan.skip_verdict_update,
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
        timings.finish(payload)


def _preflight(
    args: argparse.Namespace, *, can_keep_running: bool = False, probe: ServerProbe | None = None
) -> bool:
    """Decide what happens to a listening review server before the pass, and return whether the pass may run. A staging pass writes nothing the server reads and skips the check. A server from this checkout that speaks the land protocol with the working tree's code keeps running through every pass, and one running other code is restarted on the tree's code first (`restart_review_server`) when the pass writes under it, and otherwise keeps running. A server serving another checkout is left alone. A listener that gave no /capabilities answer (`unanswered`) is never stopped: a pass that writes nothing under it runs beside it, and any other refuses, since it cannot tell a stalled current server from anything else. `can_keep_running` (`server_can_keep_running`) says whether the pass writes under the server. A server from before the land protocol keeps running only when it is set; otherwise `--stop-server` stops it once, `--yes` runs beside it, and a bare run refuses. `probe` is `probe_server`'s answer when the caller already has it."""
    if args.review_out is not None:
        print(
            f"Staging pass: corpus writes redirected to {args.review_out}; the live corpus at rebuild/out/review is never written."
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
        print("  1. restart the review server:  uv run python -m rebuild.review.serve")
        print("  2. open tabs move onto the new corpus by themselves on their next")
        print("     save or status check.")
        print("=" * 68)
        return True
    print("=" * 68)
    print("REFUSING TO RUN: a review server from before the land protocol is listening")
    print("on 127.0.0.1:7294, and this pass swaps the corpus and the verdict store")
    print("under it. Restart it once on this checkout's code and it keeps running")
    print("through every later pass. Before re-running:")
    print(r"  1. stop the review server:  pkill -f 'rebuild\.review\.serve'")
    print("     (or pass --stop-server and let this command stop it for you)")
    print("  2. re-run this command (or pass --yes to override at your own risk)")
    print("  (or pass --review-out <dir> for a staging pass that never touches the live corpus)")
    print("=" * 68)
    return False


def prune_carried(root: Path, stamp: str | None, keep: Path | None) -> tuple[list[Path], list[Path]]:
    """Delete the repo-root `verdicts-carried-*.json` files whose `manifest_generated_at` is not `stamp`, sparing `keep`. `status.pick_fullest_verdicts` considers only files stamped for the live corpus, and the tracked copy under `rebuild/evidence/` is outside this glob. Returns the deleted paths and the unreadable ones, which are kept. Deletes nothing when `stamp` is None."""
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


def prune_stashes(
    root: Path,
    journal_path: Path,
    *,
    lock: contextlib.AbstractContextManager | None = None,
    scan: journal.JournalScan | None = None,
) -> list[Path] | None:
    """Delete the `verdicts-autosave-*` stashes that no journal event at or after the anchor references, and return them. The anchor is the last event that is a base or moves the stamp (its stamp differs from the event's before it), so it falls on the latest land that moved the stamp whether that land was journaled as a base or as sets and clears (`journal.base_due`), and the sweep keeps the stash that land named and those named since. Anchoring on bases alone would keep every stash named since the last base, about one per land between two bases. Returns None and deletes nothing when the journal has no anchor. The test uses journal references because mtime is wrong here: `os.replace` keeps the displaced store's mtime, so the stash the latest stamp change created looks older than that change. `merge_verdicts --restore-as-of` can rebuild a deleted stash's state from the journal back to the journal's compaction floor.

    The journal is read in two phases (`journal.scan`): first without `lock`, the verdict store's lock (the caller's `scan`, or a scan of the whole file made here when the caller passes none), then under it only the tail appended since, together with the glob and the deletions. Every writer that stashes a store and journals it holds that lock, so no stash can appear between the check and the deletion without its event being in the tail.
    """
    first = scan if scan is not None else journal.scan(journal_path)
    with lock if lock is not None else contextlib.nullcontext():
        events = journal.scan(journal_path, resume=first.state).events
        anchor_at = None
        previous = None
        for event in events:
            if event.base or (previous is not None and event.stamp != previous.stamp):
                anchor_at = event.at or ""
            previous = event
        if anchor_at is None:
            return None
        keep_names = {event.stashed for event in events if event.stashed and (event.at or "") >= anchor_at}
        removed: list[Path] = []
        for path in sorted(root.glob("verdicts-autosave-*.json")):
            if path.name in keep_names:
                continue
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def journal_scan_path() -> Path:
    """The scan state retention saves after each pass (`journal.save_scan_state`), from which the next pass's scan of the journal resumes, and the land's read of it too (`--scan-state`)."""
    return cycle_paths.CYCLE_VAR / "journal-scan.json"


def retention_cutoff(now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_WINDOW_DAYS)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _retention_detail(removed: list[str], intact: list[str], journal_state: str) -> str:
    """Format the retention detail: the removal counts, the kinds of file left intact, and the journal's state. A kind left intact is named instead of counted as zero, because "nothing to remove" and "not pruned on this pass" are different facts."""
    clauses = ["removed " + (", ".join(removed) if removed else "nothing")]
    if intact:
        named = intact[0] if len(intact) == 1 else f"{', '.join(intact[:-1])} and {intact[-1]}"
        clauses.append(f"{named} left intact")
    if journal_state:
        clauses.append(journal_state)
    return "; ".join(clauses)


def run_retention(plan: Plan) -> RetentionResult:
    """Prune stale carried files, build logs, autosave stashes, and old journal history after a green pass, and return the summary lines and detail. It returns the lines instead of printing them so they appear in the summary block below the table. The stash sweep and the compaction share one scan of the journal, made without the verdict store's lock and resumed from the state the last pass saved (`journal_scan_path`), so a pass parses only what was appended since the last one. They take the lock only for the tail appended since, the deletions, and the journal's replacement (`prune_stashes`, `journal.compact_prepare`, `journal.compact_finish`), so another writer (the review server, a merge, a re-key) waits for at most that long; the server answers a save made in that window with a retryable 503, as it does during a land. Every append holds the lock, so no line can fall between the scan and the replacement. A lock still held after `RETENTION_LOCK_TIMEOUT_S` leaves that part for a later pass. A review server from before the land protocol (`Plan.legacy_server`) appends to the journal and moves stashes without the lock, so while one listens the stashes and the journal are left for a later pass."""

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    def swept(count: int, singular: str, plural: str) -> str:
        return f"{console.fmt_count(count)} {singular if count == 1 else plural}"

    lines = ["Retention (skip with --keep-history):"]
    removed_counts: list[str] = []
    intact: list[str] = []

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        stamp = None
    if stamp is None:
        lines.append("  carried   : left intact (no corpus manifest to align against)")
        intact.append("carried files")
    else:
        removed, unreadable = prune_carried(ROOT, stamp, plan.carry_out)
        removed_counts.append(swept(len(removed), "carried", "carried"))
        lines.append(
            f"  carried   : removed {console.fmt_count(len(removed))} stale verdicts-carried-*.json; kept the stamp-aligned fullest verdicts file"
        )
        for path in unreadable:
            lines.append(f"              kept {rel(path)} (unreadable, not pruning it)")

    dropped_logs = prune_build_logs(cycle_paths.BUILD_LOGS_ROOT, cycle_paths.BUILD_LOGS_KEEP)
    removed_counts.append(swept(len(dropped_logs), "build log", "build logs"))
    lines.append(
        f"  build logs: removed {console.fmt_count(len(dropped_logs))}; kept the last {cycle_paths.BUILD_LOGS_KEEP} runs under {rel(cycle_paths.BUILD_LOGS_ROOT)}"
    )

    journal_path = ROOT / journal.JOURNAL_NAME
    if plan.legacy_server and server_listening():
        lines.append(
            "  stashes   : left intact (a review server from before the land protocol is listening, and it moves stashes and appends to the journal without the verdict store's lock)"
        )
        lines.append(
            "  journal   : left intact (the same server's appends could fall between the compaction's tail copy and its replacement of the journal)"
        )
        intact.extend(["stashes", "journal"])
        return RetentionResult(lines, _retention_detail(removed_counts, intact, ""))
    store = ROOT / "verdicts-autosave.json"

    def locked() -> contextlib.AbstractContextManager:
        return landing.locked_store(store, timeout=RETENTION_LOCK_TIMEOUT_S, quiet=True)

    busy = f"left intact (the verdict store stayed locked for {RETENTION_LOCK_TIMEOUT_S:g} s)"
    state_path = journal_scan_path()
    scan = journal.scan(journal_path, resume=journal.load_scan_state(state_path))
    try:
        removed_stashes = prune_stashes(ROOT, journal_path, lock=locked(), scan=scan)
    except store_lock.LockBusy:
        lines.append(f"  stashes   : {busy}")
        intact.append("stashes")
    else:
        if removed_stashes is None:
            lines.append(
                "  stashes   : left intact (the journal holds no base event or stamp change to anchor on)"
            )
            intact.append("stashes")
        else:
            removed_counts.append(swept(len(removed_stashes), "stash", "stashes"))
            lines.append(
                f"  stashes   : removed {console.fmt_count(len(removed_stashes))} verdicts-autosave-* stashes older than the journal's last stamp change or base"
            )

    prepared = journal.compact_prepare(journal_path, cutoff=retention_cutoff(), resume=scan.state)
    try:
        result = journal.compact_finish(prepared, lock=locked())
    except store_lock.LockBusy:
        journal.save_scan_state(state_path, prepared.read)
        lines.append(f"  journal   : {busy}")
        return RetentionResult(lines, _retention_detail(removed_counts, intact, "journal intact"))
    journal.save_scan_state(state_path, result["resume"])
    if result["compacted"]:
        total = result["dropped_lines"] + result["kept_lines"]
        lines.append(
            f"  journal   : compacted {console.fmt_count(total)} -> {console.fmt_count(result['kept_lines'])} lines (restore floor now {result['floor_at']})"
        )
        journal_state = f"journal compacted to {console.fmt_count(result['kept_lines'])} lines"
    elif result.get("replaced"):
        lines.append("  journal   : left intact (another writer replaced it while it was being compacted)")
        journal_state = "journal intact"
    else:
        lines.append(f"  journal   : left intact (no base event older than {RETENTION_WINDOW_DAYS} days)")
        journal_state = "journal intact"
    return RetentionResult(lines, _retention_detail(removed_counts, intact, journal_state))


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


def recover_land(*, dry_run: bool = False) -> str | None:
    """Finish or drop the land a killed pass left (`landing.finish_interrupted_land`), holding the verdict store's lock, and return the line to print, or None when no land was left. It runs before `sweep_run_dirs`, because an unfinished land's result waits in its run directory. The review server finishes such a land on its next request too, so usually nothing is left by the next pass. A dry run changes nothing and says what the next real pass will do."""
    if not landing.intent_path_for(AUTOSAVE).exists():
        return None
    if dry_run:
        return f"Left the land a stopped pass began ({landing.intent_path_for(AUTOSAVE)}) for the next real pass or the review server to finish."
    recovery = landing.finish_interrupted_land(AUTOSAVE, quiet=True)
    if recovery is None:
        return None
    return f"Recovered a stopped pass's land: {recovery.message}."


def sweep_run_dirs() -> list[Path]:
    """Delete the per-run scratch directories under `cycle_paths.CYCLE_VAR` that earlier passes left, and return them. Each pass deletes its own when it ends, so only a pass killed outright leaves one. It runs under the pass lock, so no running pass owns any of them, and after `recover_land`, so no unfinished land still needs one."""
    root = cycle_paths.CYCLE_VAR
    if not root.is_dir():
        return []
    doomed = sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink())
    for path in doomed:
        shutil.rmtree(path, ignore_errors=True)
    return doomed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the commit-time artifact cycle: run_m1, corpus rebuild, carry, review-facts pins, gates."
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
        help="carry only, merging neither the carry nor the fills into the store; a pass that moves the corpus still lands it, with an empty store stamped for it in place of verdicts-autosave.json, which is kept as verdicts-autosave-<old stamp>.json, so the store is left untouched only on a pass whose corpus does not move",
    )
    parser.add_argument(
        "--carry-out",
        type=Path,
        help="carried-forward output path (default: verdicts-carried-<short hash>.json at the repo root)",
    )
    parser.add_argument(
        "--skip-gates",
        action="store_true",
        help="skip the four post-build gates (JS suite, the rebuild suite, conformance sweep, make test)",
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
        help="run every stage and gate even when a green record shows its inputs unchanged since the last green run (disables all auto-skips, gate:make-test's included)",
    )
    parser.add_argument(
        "--conform-max-length",
        "--conform-horizon",
        type=int,
        default=CONFORM_MAX_LENGTH_DEFAULT,
        help=f"exhaustive sweep length for gate:conform, passed through to run_m1 --conform-only (default {CONFORM_MAX_LENGTH_DEFAULT}, the per-edit sweep); going deeper here is `make conform-deep`'s job, which runs out of band and keys its own green on the emitted lookup's behavior classes",
    )
    parser.add_argument(
        "--rebuild-pool",
        choices=POOL_POLICIES,
        default=REBUILD_POOL_POLICY_DEFAULT,
        help="how the heavy gates share cores: 'overlap' (default; conform and the rebuild suite start once run_m1's gate passes and run beside make-test and each other, and when make-test runs their widths also leave it its pool) or 'queue' (one pool at a time — make-test, then conform, then the rebuild suite)",
    )
    parser.add_argument(
        "--review-out",
        type=Path,
        default=None,
        help="staging pass: redirect the corpus write to this dir, carry the verdicts onto it without writing the store, and land nothing; the next live pass lands this dir in place of rebuild/out/review, instead of rebuilding, when it still reproduces the inputs byte for byte (promotable_corpus)",
    )
    parser.add_argument(
        "--keep-history",
        action="store_true",
        help="skip the green-finish retention pass (stale carried files and stashes, and the journal's pre-window history all stay on disk)",
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
    """Resolve the plan for the parsed arguments and run it, or print it on a dry run. `main` runs it under the pass lock (`pass_lock`), except a dry run that found the lock held, which passes `recover=False` and leaves what earlier passes left to the pass that holds it. Recovery runs first, in order: a land a killed pass left (`recover_land`), which may still need its run directory, then a `.superseded` tree (`recover_superseded_corpus`), then the run directories (`sweep_run_dirs`), then an unfinished `review.next` (`recover_next_corpus`)."""
    landed = recover_land(dry_run=args.dry_run) if recover else None
    recovered = recover_superseded_corpus(delete=not args.dry_run) if recover else None
    if recover and not args.dry_run:
        sweep_run_dirs()
    kept_next = recover_next_corpus(delete=not args.dry_run) if recover else None
    first_run = not (REVIEW_OUT / "manifest.json").exists()

    skip_make_test = False
    make_test_note = ""
    make_test_fp: str | None = None
    if not args.skip_gates:
        make_test_fp = make_test_closure_fingerprint(ROOT)
        if make_test_skippable(make_test_fp, prior_make_test_fingerprint(), force=args.force_make_test):
            skip_make_test = True
            make_test_note = "closure unchanged since its last green run; --force-make-test overrides"

    run_m1_fp = run_m1_skip_fingerprint(ROOT)
    skip_run_m1 = False
    rerun_gates_only = False
    run_m1_note = ""
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
        green = read_green_record(cycle_paths.RUN_M1_GREEN)
        if green is not None and green["fingerprint"] == run_m1_fp and m1_artifacts_present(ROOT):
            skip_run_m1 = True
            run_m1_note = "build inputs unchanged since the last green M1 build; --fresh overrides"
        elif green is not None:
            current = run_m1_skip_files(ROOT)
            rerunnable = gates_only_rerun(green, current)
            if rerunnable is not None and m1_artifacts_present(ROOT) and m1_tables_stamped():
                rerun_gates_only = True
                run_m1_note = (
                    f"only comparison-side inputs moved since the last green M1 build ({capped_labels(rerunnable)}); "
                    "the tables and font are reused and the gates re-run over them; --fresh overrides"
                )
            else:
                note = moved_inputs_note(green, current)
                if note is not None:
                    run_m1_note = f"inputs moved since its last green: {note}"
                    cache_note = oracle_cache_note(note)
                    if cache_note is not None:
                        run_m1_note = f"{run_m1_note}; {cache_note}"
    if skip_run_m1 or (rerun_gates_only and m1_stage_a_current(ROOT)):
        if args.review_out is None and not first_run:
            if corpus_build_skippable(ROOT):
                skip_corpus = True
                corpus_note = "the corpus already reflects these inputs byte for byte, stamp included; --fresh overrides"
            elif corpus_build_skippable(ROOT, ignore=unit_index.ASSET_COMPONENTS):
                skip_corpus = True
                refresh_assets = True
                corpus_note = ASSETS_REFRESH_NOTE
            else:
                promote_from = promotable_corpus(ROOT)
                if promote_from is not None:
                    skip_corpus = True
                    corpus_note = CORPUS_PROMOTE_NOTE
    if skip_run_m1:
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

    for line in (landed, recovered, kept_next):
        if line is not None:
            announce(line)

    master_aligned: bool | None = None
    if not args.no_carry and args.verdicts is None and not first_run:
        resolved = resolve_carry_source()
        if resolved is None:
            args.no_carry = True
            announce(
                "No carryable verdicts found (neither the autosave nor any verdicts-*.json at the repo root or under rebuild/evidence holds an effective verdict); proceeding without carry. Pass --verdicts to name a master explicitly."
            )
        else:
            announce(describe_carry_source(resolved, ROOT, promoting=promote_from is not None))
            args.verdicts = resolved["path"]
            master_aligned = resolved["aligned"]

    skip_verdict_update = False
    direct_merge = False
    verdict_update_note = ""
    if (
        skip_corpus
        and promote_from is None
        and not args.fresh
        and not first_run
        and args.review_out is None
        and not args.no_carry
        and not args.no_merge
        and args.carry_out is None
    ):
        verdict_update_key = verdict_update_skip_fingerprint(ROOT, REVIEW_OUT, args.verdicts)
        record = read_green_record(cycle_paths.VERDICT_UPDATE_GREEN)
        if (
            verdict_update_key is not None
            and record is not None
            and record["fingerprint"] == verdict_update_key
        ):
            skip_verdict_update = True
            verdict_update_note = VERDICT_UPDATE_SKIP_NOTE
        elif verdict_update_key is not None and args.verdicts is not None:
            if master_aligned is None:
                master_aligned = master_stamped_for_corpus(args.verdicts, REVIEW_OUT)
                if not master_aligned:
                    announce(DIRECT_MERGE_DECLINED_NOTE)
            # A master stamped for the served corpus needs no carry: every unit id maps to itself, and the carry keeps each record's `at`, so the merge (which takes only a strictly newer `at`) would drop its re-prefixed notes. Merging the master directly into the store gives the same result.
            direct_merge = master_aligned

    short_id = resolve_short_id()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan = build_plan(
        verdicts=args.verdicts,
        no_carry=args.no_carry,
        carry_out=args.carry_out,
        skip_gates=args.skip_gates,
        first_run=first_run,
        short_id=short_id,
        no_merge=args.no_merge,
        skip_conform=args.skip_conform or auto_skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fp,
        force_make_test=args.force_make_test,
        conform_max_length=args.conform_max_length,
        pool_policy=args.rebuild_pool,
        review_out=args.review_out,
        skip_run_m1=skip_run_m1,
        rerun_gates_only=rerun_gates_only,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fp,
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
        skip_verdict_update=skip_verdict_update,
        verdict_update_note=verdict_update_note,
        direct_merge=direct_merge,
        record_greens=not args.dry_run,
        keep_history=args.keep_history,
        recipe_serves=args.stop_server,
        scratch_dir=cycle_paths.CYCLE_VAR / f"{stamp}-{short_id}" if args.review_out is None else None,
        seed_kept=corpus_complete(next_corpus_dir()),
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
        plan.legacy_server = probe.kind == "legacy"
        if not _preflight(
            args,
            can_keep_running=server_can_keep_running(
                skip_corpus=skip_corpus,
                writes_store=plan.do_merge,
                promotes_corpus=plan.promote_corpus is not None,
            ),
            probe=probe,
        ):
            return 2

        if first_run:
            print("First-run mode: no existing corpus at rebuild/out/review — skipping the carry.")

        report = CycleReport()
        from rebuild.tools.cycle_timings import CycleTimings

        timings = CycleTimings(CYCLE_TIMINGS)
        # run_m1's CLI and the make_test_gate wrapper skip writing their own check line to the timings journal when this is set, because the cycle records it. It goes in the environment because the wrapper runs as a grandchild, under the `make test` recipe, where an argv flag would not reach it.
        os.environ[CYCLE_RUN_ENV] = timings.run_id

        registry = _ChildRegistry()
        try:
            with stop_signals():
                return _run_cycle(plan, report, cycle_console, registry, timings=timings)
        finally:
            if plan.scratch_dir is not None and not landing.intent_path_for(AUTOSAVE).exists():
                shutil.rmtree(plan.scratch_dir, ignore_errors=True)


def readiness_block(plan: Plan) -> list[str]:
    """Return the checklist `make verdict-ready` prints, so a green pass ends with it. It reads the cycle summary, so it must run after `_emit_cycle_summary`. A staging pass returns nothing, since its corpus is not the served one. When `--stop-server` is passed (as `make review-cycle` does), the server row is left out, because the recipe starts the server after the pass, or reports that it left it stopped. The rebuild suite switches this off with `cycle_paths.READINESS_ENABLED`, because it reads the live corpus."""
    if plan.review_out is not None:
        return []
    from rebuild.tools import verdict_ready

    try:
        result, ready = verdict_ready.readiness(
            with_server=not plan.recipe_serves,
            repo_root=ROOT,
            review_dir=plan.corpus_dir,
            m1_out=cycle_paths.M1_OUT,
            autosave_path=AUTOSAVE,
            cycle_summary_path=cycle_paths.CYCLE_SUMMARY,
        )
    except Exception as exc:
        return [f"readiness: the checklist could not be computed ({exc!r})"]
    return verdict_ready.checklist(result, ready)


def _finish(
    report: CycleReport,
    failures: list[str],
    plan: Plan,
    timings: CycleTimings | None = None,
    emit: console.CycleConsole | None = None,
) -> int:
    """Finish the pass and return its exit status: run retention on a green pass, write the cycle summary, then print the summary block, ending with the readiness checklist on a green pass. Retention runs before the table is printed so its row has an outcome and detail. The rebuild suite sets `cycle_paths.RETENTION_ENABLED` False so a test that reaches a green finish does not prune the live repo; retention then counts as run with no lines. `cycle_paths.READINESS_ENABLED` switches the checklist off the same way."""
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
    readiness = [] if failures or not cycle_paths.READINESS_ENABLED else readiness_block(plan)
    cycle_console.summary(
        summary_rows(report, plan, retention_ran=retention_ran),
        summary_cycle_lines(report, plan, retention_lines) + (["", *readiness] if readiness else []),
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
