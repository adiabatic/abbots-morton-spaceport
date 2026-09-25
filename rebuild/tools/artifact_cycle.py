"""Run the commit-time artifact cycle in one command.

The cycle recompiles M1.otf and checks it, rebuilds the review surface in place, runs the verdict plumbing over it, and refreshes the census pins from the surface's census sidecar, naming what moved in their invariant block since the last accepted census. The checked-in pins are that census, so committing the rewritten file accepts a new one. It then runs the gates. Once they have joined and their pytest controllers have written this pass's per-worker peaks to the timings journal, it compares the checked-in per-unit peaks with what this machine measured (`rebuild.tools.calibrate_budgets --check`). It always ends with a summary table, even on failure.

The terminal shows a digest: one banner per step with that step's description, the phases and counters its child prints, every warning, and a closing line. All child output is written under var/build-logs/<stamp>-<short sha>/: one log per step with stdout and stderr merged in arrival order, plan.txt, and a copy of the terminal output. var/build-logs/latest points at the newest run, and a failed step's log is replayed under its banner. rebuild.tools.console defines the line protocol children print and the renderer that reads it.

The job-costs step never fails the pass, for the same reason the census pins are not a gate: a stale constant makes a pool the wrong width, which costs time but makes no artifact wrong. It is reported, and committing the re-seeded constant accepts it. When the check reports an overrun, the driver prints the working tree's `git diff` of the files that hold those constants, so a constant already re-seeded shows up.

The plumbing is one step run by one child process, rebuild.tools.verdict_chain. It carries prior verdicts forward onto the fresh manifest, merges the carried file into the live autosave (--no-merge opts out), writes echo-fill verdicts for the blanks in unanimously judged echo groups, writes standing-approval verdicts from the rules in rebuild/standing-approvals.yaml, merges each fill as it is written, repeats the echo pass until it writes nothing, and clusters the open complaints. The chain reads the build's per-unit index sidecar, and its one process holds one copy of it. Each chain step opens with a `[phase] <step>` line and closes with `[t] <step>`. The digest pairs the two into one line per step, and the cycle-timings journal reads the step's cost from the `[t]` line. The `[chain] fixpoint:` and `[chain] failed:` lines are results, not phases: `plumbing_sections` starts a section at each `[phase]` line and closes it at either `[chain]` line.

run_m1's exit status is its own gate's verdict, but this driver judges from the three summary JSONs it writes, so a build that died before its judge is reported by what it left behind. The gates are defect_errors, the Manual-pin verdict (including its scope, so a gate that replayed nothing cannot pass), and multi_matched == 0.

This process, not its children, records each check's verdict in the timings journal. Every judged check (run_m1, conform, rebuild-contracts, make-test, js) appends one kind:"check" line tagged with this run, carrying the judge's verdict, not the process's exit code; `make cycle-timings ARGS='--by-outcome'` reads them. run_m1's CLI and make_test_gate record their own line when run by hand, so this driver sets its run id in the environment as AMS_CYCLE_RUN (cycle_timings.CYCLE_RUN_ENV), and those children record nothing when they inherit it. Each check invocation gets one line.

gate:js and gate:make-test depend on no build artifact, so they start at t=0 in a small thread pool while the build steps run in sequence in the main thread. gate:conform (the exhaustive font-versus-settlement sweep at the per-edit horizon, `run_m1 --conform-only`) starts after the run_m1 gate passes, queued behind make-test by default. Its deeper form, `make conform-deep`, never runs in the cycle; the summary has one line saying whether the emitted lookup has a shape the last deep run did not shape. gate:rebuild-contracts runs every test under rebuild/, and none of them reads a live build artifact (rebuild/conftest.py's audit hook enforces this). A hand run uses every core. Under a cycle the suite is submitted with the conform lane, right after the run_m1 gate passes, and runs beside the surface build at `contracts_pool_width`: the cores less the build's parent and its `surface_job_budget` workers, set on that one child as PYTEST_XDIST_AUTO_NUM_WORKERS, so the pool and the build together run about one process per core. The suite reads nothing the build lane writes, the census pins included, so it waits for nothing downstream, and on a pass where every upstream stage skips it starts at t=0.

Under the default queue policy the gates run make-test, then conform, then rebuild-contracts, so only one heavy gate pool runs at a time, and the build steps run beside whichever one it is. Each pool's width comes from its budget function: the oracle (`sweep_job_budget`) splits its tables into row ranges and uses the cores within the memory limit ORACLE_SHARD_BYTES sets; the belt (`conform_job_budget`) runs one process per acceptance configuration wherever CONFORM_BELT_BYTES fits that many beside the build lane; and the surface build (`surface_job_budget`) gets the machine less what make-test holds. Two heavy pools side by side oversubscribe the cores roughly 2:1, and that contention was measured to roughly triple the rebuild suite's wall-clock time (`_gate_conform_task`'s docstring, commit b5881022), which is worse than running the same work in sequence. `--rebuild-pool overlap` runs the pools side by side anyway. The suite's width then also subtracts gate:make-test's pool, so it is no wider than under the queue policy: one worker on a ten- or twelve-core machine beside a cap-width build.

The cycle runs no cross-language check, because the kernel crate is the only engine that enumerates and the only one that settles. gate:conform checks settlement empirically: it shapes the compiled font through HarfBuzz and compares the result, window by window, with a re-settlement of every swept text through the crate's `settle-cases` subcommand, with the memo keyed on the raw window so the sweep does not depend on the crate's enumeration and fold. `make kernel-gate` is the crate's own gate, to run around a kernel-semantics change; it takes seconds once the crate is built. The spec-ingest parity check is a contracts test (rebuild/test_kernel_io.py) and runs in gate:rebuild-contracts on every cycle.

gate:make-test is skipped when its input closure is unchanged since its last green run. The closure is every tracked or untracked-unignored file that `make_test_exempt` does not exempt; that function's docstring argues each exemption from what the gate runs (make all, which runs build_font over glyph_data/*.yaml non-recursively, typst, pyright over tools/ test/ conftest.py, and pytest test/ site/). The Makefile itself is represented by what `make -n all` and `make -n test` print. Re-running the gate over an unchanged closure would cost about 15 CPU-minutes and check nothing. The last green fingerprint is in rebuild/out/make-test-green.json, written by rebuild.tools.make_test_gate (the `make test` entry point) on every green run, so interactive and cycle greens share one record and `make test` skips on the same test. cycle_summary.json also records the fingerprint the cycle ran or skipped against, for display only. The skip reads only the shared green record, so a green that make_test_gate deleted after a red run cannot come back from an older summary. The fingerprint covers file content only, so a system toolchain change such as a typst upgrade does not move it (pyright and pytest are pinned in uv.lock, which is in the closure). --force-make-test and --fresh spawn `make test FORCE=1` (`make_test_gate_argv`), because the wrapper decides its own skip with the predicate the plan uses (`make_test_skippable`), and a plain `make test` would skip on the closure the flag forced. The plan reserves the gate's cores and memory beside the surface build only when that predicate says the gate runs.

The verdict plumbing skips the same way, on rebuild/out/plumbing-green.json. Every plumbing step is a pure function of the surface, the verdicts master, the live store, the checked-in standing approvals, and its own code, so the key covers the surface's inputs fingerprint and stamp, the master's path and bytes, the autosave's bytes, standing-approvals' bytes, and the chain's code (`plumbing_code_paths` plus the review/ modules the chain runs). The master is in the key because the autosave's hash cannot see it: an export at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code is in the key because no other fingerprint reads the chain's modules, and without it a fix to a fill's matcher or to the carry's join would be skipped. `plumbing_code_paths` lists the chain's modules instead of all of rebuild/tools/, and rebuild/test_plumbing_closure.py checks on every contracts run that the list covers the chain's import graph.

The key is captured when the chain finishes, not at the end of the pass, so a store write during the census cannot be counted as part of a fixpoint nothing verified. The record is written later, after the complaint docket step has also succeeded. The fixpoint is claimed only when the chain has observed it. The carry's merge gives echo-fill new agreement to read, and echo-fill only removes blanks, so it never creates work for standing-fill. But standing-fill runs last, and a standing fill can make an echo group unanimous while a blank member remains. Refusing the green whenever the standing merge changed anything would cost another full pass. In one process another echo pass costs about a second, so the chain repeats it until a pass writes nothing, and the green is recorded only after that pass.

The plumbing skip also requires the surface build to skip, which is what makes the stamp known before the pass runs. A flag that names a carry output disables the skip, since skipping would write nothing to that output.

Every other heavy stage skips on the same principle: a content fingerprint over the stage's input closure, and a green record written only after that content passed.

- run_m1 skips on rebuild/out/run-m1-green.json (the Stage A fingerprint components plus the contact allow-list, the oracle's subset tables and uv.lock's dependency pins) and re-evaluates its gate from the summary JSONs on disk.
- gate:conform skips on conform-green.json, keyed on what the belt tests for, not on run_m1's closure: the emitted lookup's behavior classes, the font-compilation code and its tools/ closure, the uharfbuzz version, and the sweep horizon (`conform_skip_fingerprint`). A rune edit that creates no new rule shape leaves that key unchanged, because the crate's string replay inside run_m1 has already checked the new tables against the engine over every string.
- The rebuild suite skips on rebuild-contracts-green.json, keyed by `rebuild_lane_fingerprint` over its closure: the repo files under rebuild/ and glyph_data/, the harness files in REBUILD_GATE_HARNESS_PATHS, conftest.py, pyproject.toml, uv.lock by its dependency pins, and the site fonts without their head and name tables. The closure contains no build artifact, so the suite can skip whether or not run_m1 rebuilt: an M1 rebuild writes only under rebuild/out, which the closure does not include. The record also stores each test's input closure, so a pass whose key changed runs only the tests whose closure the diff reaches; a rune edit reruns the tests that load the spec and nothing else. rebuild.tools.contracts_closure defines what a closure holds and when a test may be skipped, and runs the test whenever it cannot tell. rebuild.tools.rebuild_gate (`make test-rebuild`) writes the same record, so interactive and cycle greens share it.
- surface-build skips when the manifest's recorded inputs fingerprint equals the one a build would stamp now. A rebuild would then be byte-identical, including `generated_at` (the latest input mtime, floored), so the autosave stays aligned. When the live surface does not match but a rehearsal's directory does (the last cycle summary's `plan.review_out`, or var/rehearsal-review), that directory is moved into rebuild/out/review with its stores instead (`surface-promote`; `promotable_surface` checks the preconditions). Every stamp inside a surface depends only on content relative to its manifest, and the move keeps the `generated_at` a rebuild would reset.
- The census step has no key and never skips: it reads the surface build's census-facts.json sidecar and rewrites one small checked-in file in milliseconds.

The surface skip applies only on passes where run_m1 skipped, and on the gates-only route when the Stage A record on disk already matches what that pass will write (`m1_stage_a_current`), because the surface reads nothing else the pass writes. That happens on a contact-allow bless, the only comparison-side edit outside every Stage A component.

Conform's skip is decided after run_m1 finishes, from the key the artifacts it left carry. A route that leaves the emitted lookup's shapes, the compile code and the shaper at the last green key skips the sweep, whether run_m1 skipped, re-adjudicated, or rebuilt, and the skip is recorded as proved because a matching green covers this exact content. Computing the key only after run_m1 has finished also means an M1 rebuild cannot invalidate it during the cycle. The preflight can decide it before the pass only on the route where run_m1 skipped, so nothing will change; that is the route --dry-run can predict. On the reuse and rebuild routes the printed plan shows the conform lane as undecided (`run?`), because only a finished run_m1 knows what the artifacts are, so a plan that shows the sweep may end in a pass that skips it.

Green records are written only when the key still matches after the work ran, and a red result whose key matches its record deletes the record. --fresh runs everything regardless.

Between the run_m1 skip and a full rebuild there is a third route. When the per-file diff against the run_m1 green is confined to comparison-side inputs (the alias map, the divergence ledger, the contact allow-list, the kern sidecar, the oracle's two modules, and the baselines and their subsets, all outside the tables' stamp; `comparison_side_label` lists them and argues each), the tables on disk still carry that stamp, and all the artifacts are present, the cycle spawns `run_m1 --gates-only` instead of a build. It re-runs the defect gate, the Manual-pin gate and the oracle over the tables and font on disk, re-adjudicates the ledgers' verdicts, and enumerates nothing. The green that pass records covers the new inputs, so the next cycle skips run_m1. `uv.lock` is not comparison-side, because a fontTools or uharfbuzz bump can change the font's bytes and what the shaper does with them, so a toolchain bump rebuilds.

This module, not the caller, decides which passes stop the review server, because only the resolved plan knows. Two things a cycle writes belong to the running app: the surface it serves (livereload watches every shard, and a restamped manifest orphans the tab's store) and the verdict store, which merge_verdicts will not touch under a live server because an open tab would write its own copy back over the merge. A pass whose plan skips both writes neither, so a listening server is left alone and the open tab keeps working for the whole run. That is the pass with no artifact work, whose long verification would otherwise take the app down for its whole length.

A pass whose surface did not change but whose store did has its own route. The carry there maps every unit id to itself and keeps each record's `at`, which the merge compares strictly, so the carry is skipped and the master is merged straight in; the master is the one input the store's own hash cannot see. That pass still writes the store, so it takes the port. The route needs the master stamped for the served surface, as the merge requires of every input. A master stamped for another surface, which a pass stopped between the surface build and the carry leaves behind, takes the full carry instead. The carry source's resolution says which of the two an auto-resolved master is, and `master_stamped_for_surface` says it for a --verdicts one.

An edit confined to rebuild/review/static/ also has its own route. The copied app assets are the one surface input no unit depends on, so the pass copies them over the served copy and restamps that one fingerprint component (`assets-refresh`). Every shard, both sidecars, the unit-cache store and `generated_at` stay as they were, so nothing the tab is keyed on changes, the server stays up, and livereload reloads the tab with the new assets.

A surface promotion is the opposite case under the same skip. The whole tree under the app is replaced by the rehearsal's and the stamp changes with it, so the pass takes the port. Both the plumbing skip and the store-only route are off, because both assume the surface did not change, and here the store's verdicts must be carried onto the promoted units by id.

A pass that writes under the app needs the port to itself. --stop-server (which `make review-cycle` passes) lets it terminate the server and wait until the port is free; without it the pass stops and prints how to proceed. Retention also writes: the app appends to the journal as verdicts are recorded, and a compaction rewrites the file around a read, so while a server is up the journal and the stash sweep that depends on it are left for a later pass.

A green finish ends with a retention pass over the cycle's own files, all of them regenerable or covered by the journal. Root verdicts-carried-*.json files not stamped for the live surface are deleted, since `status.pick_frontier` reads only files stamped for the live surface, and the tracked copy under rebuild/evidence/ is never touched. verdicts-autosave-* stashes not referenced by a journal event at or after the last base event are deleted. The journal, not the stashes, is the supported recovery path, and the check uses the journal's references because a stash's mtime predates the event that created it. The journal is compacted to the newest base event older than RETENTION_WINDOW_DAYS, keeping at least that many days of --restore-as-of history, and build-log run directories beyond the newest `cycle_paths.BUILD_LOGS_KEEP` are deleted. Failed, interrupted, first-run, and rehearsal cycles never prune, --keep-history turns retention off, and a retention error prints a warning and never turns a green cycle red.

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
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from rebuild.review import app_index, census, unit_index  # noqa: E402
from rebuild.review.audit import load_ledger  # noqa: E402
from rebuild.tools import console, cycle_paths  # noqa: E402
from rebuild.tools.green_record import (  # noqa: E402
    _digest_lines,
    _record_outcome,
    _sha256_path,
    clear_contradicted_green,
    read_green_record,
    record_green,
)
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV, CheckVerdict  # noqa: E402
from rebuild.tools.peak_rss import reap_peak_rss_bytes  # noqa: E402
from rebuild.tools.review_server import REVIEW_PORT, server_listening  # noqa: E402

if TYPE_CHECKING:
    from rebuild.tools.cycle_timings import CycleTimings
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
ECHO_FILL = ROOT / "verdicts-echo-fill.json"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
CENSUS_PINS = ROOT / "rebuild" / "review-census-pins.json"
DIVERGENCE_LEDGER = ROOT / "rebuild" / "m1-divergences.yaml"
CYCLE_TIMINGS = ROOT / "rebuild" / "out" / "cycle-timings.ndjson"
BEHAVIOR_CLASSES = cycle_paths.M1_OUT / "behavior_classes.json"
JSTEST_DIR = ROOT / "rebuild" / "review" / "jstests"

POOL_POLICIES = ("queue", "overlap")
REBUILD_POOL_POLICY_DEFAULT = "queue"
PLUMBING_SKIP_NOTE = "surface, verdicts master, live store, and standing approvals unchanged since the last complete plumbing pass; --fresh overrides"
STORE_ONLY_DECLINED_NOTE = "The surface build is skipped, but the verdicts master is not stamped for the served surface and the merge would refuse it, so the plumbing carries it onto the served surface by unit id rather than merging it straight in."
CONFORM_SKIP_NOTE = "no new rule shape, compile code or shaper since its last green sweep; --fresh overrides"
CONFORM_MAYBE_NOTE = "runs unless run_m1 leaves the emitted lookup's behavior classes, the compile code and the shaper under the key of its last green sweep, in which case it is re-skipped after run_m1"
UNDECIDED_UNTIL_RUN_M1 = {
    "gate:conform": CONFORM_MAYBE_NOTE,
}
ASSETS_REFRESH_NOTE = "only the review UI assets moved since the surface was stamped; they are copied over the served copy and the manifest's static component restamped in place — no shard, sidecar or generated_at moves; --fresh overrides"
SURFACE_PROMOTE_NOTE = "a rehearsal already built the surface these inputs produce, byte for byte, unit store and signature store beside it; that directory is moved into place instead of being rebuilt; --fresh overrides"
SERVER_STAYS_UP_NOTE = "rewrites no unit shard, moves no manifest stamp, and leaves the verdict store alone"
SERVER_STOP_PATTERN = r"rebuild\.review\.serve"
SERVER_STOP_TIMEOUT = 15.0
# The gate thread pool's worker count, sized to the tasks the chain submits, not to the cores. Under the queue policy a waiting task holds its worker for the whole wait (conform waits on make-test, and contracts on both), so every gate task needs a worker at the same time, with spare workers on top. With fewer workers a waiting task could sit behind an unrelated task's completion, and a width taken from the cores would cause that on a small machine. `test_the_gate_pool_seats_every_gate_task_at_once` in rebuild/test_artifact_cycle.py checks that this equals the gate task count plus two.
_GATE_POOL_WORKERS = 6
# The peak memory of one surface-build worker, the divisor of the build's width (`surface_job_budget`). A worker is a persistent spawn process that takes unit batches from the parent's hand-out queue (`_handout_width` in rebuild/review/build.py: the enricher's settlement batch, or a smaller spread of a small pile), enriches and drafts each unit, spools each fragment to disk as it is drafted so no EnrichedUnit outlives its batch (`_FragmentSpool`), and replies with the batch's projections and spool addresses. Nothing it holds grows with the corpus, the alphabet or the width. It holds its interpreter and shapers, whose shape memo is released at every batch boundary (`rebuild.review.ink.release_shape_memos`; `_MemoizedShaper`'s docstring records the measurement that showed an unreleased memo outgrowing everything else in a serial build). It holds one batch's units, projections and addresses, bounded by `PHASE1_HANDOUT_UNITS`, from the hand-out until the reply is pickled; the `SubsetRow`s for one batch's units; and the pages it touches of the baseline subset pack (rebuild/review/subset_pack.py), which the parent writes once before the pool starts and every worker maps read-only, so the page cache holds one copy per machine. The parent hands units out in configuration order (`_configuration_order` in rebuild/review/build.py), so a worker's touched pages are mostly one configuration's key range.
# The seed is every width-eight pool on the mapped pack since the previous seed, on the 32 GiB machine and the 18-core 48 GiB machine (`doc/fleet.md`). On passes that draft units, workers peak at up to 0.52 GB on the 32 GiB machine over the qsYe corpus, and each pass's largest worker reads 0.42 to 0.48 GB on the 48 GiB machine. A served pass's workers read under 0.1 GB. For comparison, a width-six pool over the 32-letter corpus that held its subset tables in Python read 1.16 to 1.95 GB. The constant is the largest mapped-pack reading plus a quarter, rounded up to the hundredth, for the reason kernel_exec.DELTA_PEAK_BYTES rounds up: a cost that is too low pushes the machine into swap, while one that is too high only narrows the pool. A worker's peak depends on the batches it draws, not on the width, so a wider pool repeats the same reading. A new letter changes it only through what a batch holds (a window's rows and shapes), not through the tables, so every fleet machine stays at `SURFACE_JOBS_CAP` as letters are added (`test_the_shipped_surface_divisor_holds_the_32_gib_box_at_the_cap_by_division` in rebuild/test_memory_budget.py checks this for the 32 GiB machine, and `test_both_fleet_boxes_keep_a_pooled_build_under_a_gated_cycle` in rebuild/test_artifact_cycle.py for the 48 GiB one). On a ten-core machine the cap-width pool leaves gate:rebuild-contracts one worker (`contracts_pool_width` discusses this). The surface-worker row of `make job-costs` checks the constant against the kind:"pool" record rebuild/review/build.py writes for each pooled build. Every fleet machine runs the build pooled, so a machine with no rows has not been made serial by this width.
SURFACE_WORKER_BYTES = 660_000_000
# The peak memory of the surface build's parent while its pool runs, subtracted from the machine's memory before dividing by SURFACE_WORKER_BYTES. The parent holds the workload table (`audit.UnitTable` in rebuild/review/audit.py: each unit's class, group, family, echo and cluster as ids into one string table, its config set, kinds, render groups and per-config class map as ids into pools of a few dozen values, its window parsed once into a `u16` side column, its run of audit rows and its triage position, all as fixed-width `array` columns over the unit's ordinal, well under 100 bytes a unit); the packed unit store (rebuild/review/unit_store.py) over the same ordinal and string table; the checker's identity dict; the census's pre-merge snapshot (`census.PremergeSnapshot`, columns over the pre-fold rows at 18 bytes a row, 23 after `rebase`); one fragment or one materialized `audit.Unit`; one batch of materialized records while a hand-out is being sent; and one batch reply in flight per worker. The hand-out and the replies are the only terms the width changes. The parent never holds a list of unit records at any phase (`UnitTable.unit` materializes one for a reader that needs it, and `units` is for the census CLI and the tests).
# The unit store holds every per-unit product of phase 1 from the plan boundary to the cache write (the machine flags and ink deltas, the diff and cluster digests, the seam-home projection with its spans, names and rects, the secondary-home assignments, the fragment's spool or prior address, the shard address the write returns, the content and input keys, the policy file and the config note) as fixed-width `array` columns plus one string table over the vocabulary, not the corpus (the `[tally] … unit_store` line measures the table beside the columns). It materializes one unit at a time for the reduce step or a store line. A slotted state record, a spool-address object and a dict keyed by id hold the same facts in about six to twenty times the bytes, pile by pile: the `[tally]` lines of a pass under `AMS_SURFACE_PILE_TALLY=1` show the walked and packed bytes of each pile side by side, and that comparison is why the store uses columns. The table's `[tally] … workload.units` line compares the same way against 645.6 bytes a unit for a list of records.
# Neither ink-signature table is in the plateau. The window-keyed table the ink-duplicate merge reads (`signatures` in rebuild/review/build.py) is released when that merge returns, in the load phase. The store's records (`signature_entries`) are held through the plan phase and released when the store is written: inline as the units phase opens on a serial build, and a few seconds into the units phase on a pooled build, when the `_SignatureWrite` thread that `build_m1` starts at that boundary finishes. So the records overlap the pool only for the length of that write. The load boundary's `signatures` tally line includes the table's strings, and the digest strings are shared with the records, so the release frees less than the sum of the two lines.
# This figure covers the whole surface-build step, not one phase. On a full-fresh pass the load phase holds the row columns (`audit.RowColumns`, five flat arrays over the audit's rows with their tuple pool, about 17 bytes a row), the table, the snapshot and both signature tables. The plateau holds the table, the store's columns, the identity dict and the snapshot, with the name tuples released at the units boundary (`UnitTable.release_names`). The `rss_now_gb=` token beside `rss_gb=` on each `[t] review.build` line gives a phase's own resident set, separate from the step's peak (`_phase_timing` in rebuild/review/build.py). On full-fresh and served passes alike the load boundary sets the peak: `rss_gb=` reaches the step's figure on the load line and does not rise after it, the load's `rss_now_gb=` is the pass's highest resident reading, and the plateau's boundaries read below it, by about 1.5 GB on a full-fresh pass and by more than 0.5 GB at the served units boundary. So the row columns and both signature tables beside the table set the step's peak, not the plateau's store.
# A served pass's plan phase streams the store (`unit_cache.stream_store`) and folds each record into the columns as it is parsed. Beside the table and the store's columns, the plan holds the map from input key to ordinal (built from the store's key column and released at the plan's `del named`), one `unit_cache.ServedUnit`, whose projection tuples are pooled within the record (`unit_cache._served_unit`), and the addressless records: records the store returns without an address, buffered until one pass over the previous surface's shards places them (the `[tally] plan unit_cache.unplaced` line measures them). A store this code wrote has none, so a served pass reads at or below a full-fresh one, with its plan boundary well below its load. A store whose every record is addressless (every part resized under it) buffers every record, and that plan reads like a parse of the whole store; streaming limits the spike to that case but does not remove it. Fragments stream by address into the shards and are released after the checker and sidecar spools consume them. Before the workload loads, the same process packs the baseline subset tables (rebuild/review/subset_pack.py; `write_pack` holds every table's keys and row indices beside the pool of distinct rows), a transient well below the pool-time peak that this constant covers without a term of its own.
# The peak grows with the alphabet. The surface-parent row of `make job-costs` measures it through the surface-build step's peak: `peak_rss.reap_peak_rss_bytes` takes the largest process peak in the tree, and the parent holds the corpus while each worker holds one batch. The seed is two untallied width-eight passes over the qsYe corpus on the 32 GiB machine (`doc/fleet.md`), with nothing edited between them, the workload held as the table's columns and the served plan folding each record as it is parsed. The full-fresh pass, run by `make artifact-cycle`, peaks at 4.01 GB, and the served pass, run by hand over the surface the first one wrote, peaks at 4.00 GB. Both peaks occur at the load boundary (the load's `rss_now_gb=` reads 3.79 and 3.78, and the served plan boundary's 2.90). The constant is the higher reading plus a quarter, rounded up to the next whole gigabyte, as STANDING_FILL_PARENT_BYTES is: a cost that is too low pushes the machine into swap, one that is too high only narrows the pool, and the peak grows with the corpus, so every new letter raises it before the row shows it.
# Beyond the columns, the plateau holds only the checker's identity dict as per-unit objects (the `[tally] … checker.identity` line: 214.8 bytes a unit walked against 9.2 packed on the qsYe corpus). The window is the one per-unit column held twice: the store's seam-home codepoint values and the table's `u16` side column. The one per-unit transient beside them is the triage sort's (`audit._triage_permutation`, run once at the load and once at the units boundary): one integer a row, with its class, group, window and id terms packed into it, a few dozen bytes each, in a list sorted in place and dropped when the permutation returns. No `[tally]` line measures it, and a units-boundary `rss_now_gb=` shows it only as memory the allocator has not yet returned. A tallied pass reads higher than the same pass untallied, because it rebuilds the plan's input-key map at every boundary and keeps the addressless records past the `del` that frees them, so re-seed from the row's step peak, never from a tally line. The row counts only measurements after the commit that sets the constant. A hand build writes pool records only, so read its step peak from its `[t] review.build` lines. Re-measure as the alphabet grows.
SURFACE_PARENT_BYTES = 6_000_000_000
# The surface build's limit other than memory. One parent process hands out the batches and merges every reply, and eight is the widest pool measured: on the 32 GiB machine the units phase's wall-clock time at width eight is its width-six time scaled by the ratio of the widths, over the same corpus (`make cycle-timings ARGS='--inner'` reports the `review.build units` phase), so the pool scales linearly up to the cap. Nothing past eight has been measured, so raising the cap needs a measurement first.
SURFACE_JOBS_CAP = 8
# The peak memory of one worker in the standing fill's refill pool, the divisor of that pool's width (`standing_fill_jobs`). A worker is a spawn process holding its interpreter, the rules, a `SlideContext` over the surface's font pair (two shapers), and one chunk of `_STANDING_POOL_CHUNK` units (rebuild/tools/standing_verdicts.py) with that chunk's shape and walk memos and its alignment cache. All three are emptied after every chunk, so the peak depends on the chunk, not on the pool's share of the units. The seed is three hand refills in the chain's form (`--open-only --require-reach`) with the memo dropped (`--fresh-memo`), each worker's resident set sampled every 0.1 s on the 18-core 48 GiB machine (`doc/fleet.md`). Two ran eighteen wide, and their thirty-six workers read 95 to 104 MB. One ran two wide, and its workers, which decided about fifty-eight chunks each, read 99 MB, so a worker's peak does not grow with the chunks it decides. The constant is more than three times the highest reading, because no journal row measures this worker: `make job-costs` has no standing-fill-worker row, since writing pool records from the fill would put cycle_timings and peak_rss into the memo's code stamp (`MEMO_CODE_MODULES`) and drop the memo on every width change. Re-measure by hand at the chunk width when the chunk size or what a decision holds changes. At this figure the cores, not memory, limit this pool on every fleet machine.
STANDING_FILL_WORKER_BYTES = 350_000_000
# The peak memory of the plumbing step's parent while the standing fill's pool runs, subtracted from the machine's memory before dividing by STANDING_FILL_WORKER_BYTES. The parent holds every surface id, the human id/echo/notation projection, and the fill's rules, primed keys, decisions and memo. Full human records stream through the chain's steps. Refill misses go to a temporary gzipped spool, with at most one pool round of records in memory, bounded by the width times `_STANDING_POOL_CHUNK` (rebuild/tools/standing_verdicts.py). The complaint docket keeps compact grouping projections. A serial refill holds one unit's `SlideContext` memos at a time, because `Decider.decide` releases them after each unit. The module-level alignment cache in rebuild/tools/standing_verdicts.py (`_alignment_cache`) is not emptied on the serial path, so it holds a reference to every unit record the parent checks for the rest of the process.
# The standing-fill-parent row of `make job-costs` reads the whole plumbing step through `peak_rss.reap_peak_rss_bytes`, and the chain reaches that peak after the fill, not during its pool: sampled every 0.1 s over two served passes that merge straight in on the 18-core 48 GiB machine (`doc/fleet.md`), the chain holds at most 2.06 GB during the standing fill and 2.74 GB in the complaint docket. A standalone fill over every unit puts the in-flight round at about 18 MB a worker: its parent reads 1.17 GB two wide and 1.45 GB eighteen wide. The seed is the highest step reading any fleet machine has recorded since the previous seed: 4.36 GB on the 32 GiB machine, over a carry across a rune edit (run 2ca8c2192122 at cb205186, with 709 verdicts stranded and three echo rounds). The 18-core 48 GiB machine reads 3.10 to 3.11 GB on carried served passes, 2.74 GB on served passes that merge straight in (6444755ee9aa, 169f78e7d20d, 376f72a5e4e7), 3.28 GB on a rules commit whose 1,219 misses are decided serially below `_STANDING_POOL_THRESHOLD` (8a407a3d83f4, 08a24101190c), and 2.19 and 2.33 GB on memo-drop passes pooled eighteen wide (6ec8760f21c2) and sixteen wide (27aa6ac52892). The constant is the seed plus more than a quarter, rounded up to the next whole gigabyte. No fleet width changes at this figure: the cores limit the refill pool, and the belt stays at its configuration count. Ids and decisions grow with the alphabet, and no rune edit has run through the chain on that 48 GiB machine yet, so watch the row as letters are added.
STANDING_FILL_PARENT_BYTES = 6_000_000_000
# The peak memory of one oracle row-range worker, the divisor of the oracle's width (`sweep_job_budget`). A worker is a spawn process. It holds its interpreter, a HarfBuzz shaper over M1.otf, and the crate's formation surface. It holds the records of its own row range of its configuration's row store: one buffer of the range's record bytes with three packed arrays beside it (an offset and two ages a record), loaded without scanning the whole member (`oracle_cache.load_store`), so it holds its range's rows and not the configuration's. It maps its configuration's settle memo read-only on the first wave that reaches the crate, which every pass does, since the renewal slice re-derives one row in `oracle_cache.MAX_RECORD_AGE`. `conform._MemoStore` reads the file's own layout: the six id columns, the value column and the 2^k >= 2N-slot probe index are views over the mapping, 69.8 MB for a live memo of 2.6M windows (the `[t] settle_memo` lines count them). Those pages belong to the page cache, resident once per machine however many workers map the file, and count in a worker's resident set as its probes touch them: a store-warm walk probes the one row in twenty the store does not serve, and a store-cold walk probes nearly every row. On a pass after a family changed, the retirement fold reads the six id columns whole once (36.2 MB of the mapping, `conform._MemoStore.load` over `mask.moved`); the seed's passes ran on an unchanged tree, every `[t] settle_memo` line at stale=0, so that fold is outside the seed and inside the headroom. The worker's own heap holds the file's interned label and outcome tables, a dead byte and a reached byte a row, 0.005 GB at the load. Last, it holds the walk's state over the range: the chunk of rows in flight (`oracle.ORACLE_ROW_CHUNK`), the waves of windows the crate settles for it, and `windows`, the dict of entries the walk promotes from the mapping or settles fresh.
# No range writes the memo file. Every range, whether or not its configuration is split, writes the windows it settled fresh as a part (`run_m1._shard_settle_memo`). The parent's absorb, one task per settlement configuration on this same pool, runs once every range has finished and the witness stage has returned (`run_m1.run_oracle`'s `memo_ready`). It holds the existing rows, the parts, the existing index and the writer's folded copies at once (`conform._write_settle_memo`), roughly the file's size plus the columns'. Its reading is recorded in the pool record beside the ranges' as `<config> absorb`. It is a process peak like the rest, so it reads at or above the range its worker ran before it. In every seed record, each of which has an absorb for every settlement configuration, it reads at a range's peak and never above the record's highest range.
# The staged measurement is one process running `oracle.oracle_config_worker` over one shard of `oracle.oracle_shard_plan`, with its row cache opened store-warm (`read_dir` the out dir) or store-cold (`read_dir=None`), recording `resource.getrusage`'s `ru_maxrss` and the resident set from `ps` at the interpreter, after the imports, spec, `kernel_exec.guard_sweep` and shaper, around `oracle.open_row_cache`, around `_SettledWindowWalk._load_memo` with the mapping's size and the store's heap sizes printed, and at return. The probe and both its logs are under `var/keep/rung263-4/stage-probe/`. Over the 708,015-row `default 1/3` of the width-twelve plan the terms read: the interpreter with its imports, spec, formation surface and shaper 0.07 GB; the range's store records 0.05 GB resident behind a 0.16 GB peak at the load store-warm, and nothing store-cold; the first chunk and its wave, before the memo maps, 0.14 GB warm and 0.15 GB cold; the memo 0.005 GB of heap at the load, warm and cold alike, over a 69.8 MB mapping of 36.2 MB of columns and 33.6 MB of index; and the walk from there to the process's peak, including the mapping's touched pages, 0.27 GB store-warm (71,448 windows promoted) and 0.33 GB store-cold (1,171,462 promoted). The peak is 0.52 GB warm and 0.55 GB cold, which is what the pool records read for that range.
# The largest term is the walk over the range's rows, 0.41 to 0.48 GB from the first chunk to the peak: more than fifty times the memo's heap, and more than four times the whole mapping even with every mapped page subtracted from the low end. The memo-less ss10 ranges, whose overlay walk maps nothing, read 0.32 to 0.33 GB store-cold, against 0.38 to 0.67 GB for the ranges that map a memo. The 0.38 is the width-ten record's 154,476-row `ss03 1/3`, which ran second in the worker that had run the memo-less `ss10`, so it reads near its own cost, while the records' other small ranges read at a larger range's peak (the 77,238-row `ss03+ss05 3/3` at 0.55 there, and the width-twelve record's 128,730-row `default 3/3` at 0.52). A cold pass's largest ranges read highest because that state grows with the range: at width ten the 849,618-row ranges read 0.64 to 0.67 GB cold, against 0.38 to 0.55 GB for the nine other memo-holding ranges.
# Read a pool record for the shape across the machine, never for one range's cost. `run_m1._spawn_pool` sets no `maxtasksperchild`, and a worker reports its process peak (`peak_rss.peak_rss_self_bytes`), so a range that runs second in a reused worker reads at or above the peak the previous range left, whatever its own row count. In the store-warm width-twelve seed record the 128,730-row memo-less `ss10 1/2` reads 0.52 GB, the peak of the range its worker ran first, while its sibling `ss10 2/2` at 1,416,030 rows reads 0.43 GB. At width ten the five smallest ranges, which are the five that run second, read 0.53 to 0.56 GB store-warm against 0.55 to 0.56 GB for the first-round 849,618-row ranges, and the record's maximum, 0.56, is shared by the 849,618-row `ss03 2/3` and the 77,238-row `ss03+ss05 3/3`. The store-cold records do not show this carry-over; there `ss10 1/2` reads 0.32 GB, its sibling's peak.
# The seed is four `run_m1 --gates-only` passes on the 32 GiB machine (`doc/fleet.md`), store-cold (`--fresh-oracle-cache`) and store-warm, at `--jobs 10` over fifteen ranges and at a stated `--jobs 12` over seventeen, which is the plan the 12-core M4 Pro Mac mini (48 GiB) chooses. Every worker holds its own range's records beside its mapped memo, and the five absorbs are recorded beside the ranges. The highest readings are 0.55 GB store-cold and 0.53 GB store-warm at width twelve, and 0.67 GB store-cold and 0.56 GB store-warm at width ten (the pool records finished between 2026-09-16T09:17:38Z and 09:20:58Z, their logs under `var/keep/rung263-4/`). The constant is the highest reading plus a quarter, rounded up to the tenth, because a cost that is too low pushes the machine into swap, and because the walk's state grows with the alphabet. The oracle-shard row of `make job-costs` checks it: `run_m1.run_oracle` writes one kind:"pool" record per fan-out, one observation per row range that ran, and the cycle's job-costs step runs the check. The rebuild suite checks the constant only against the cores, which cannot catch a figure that is too low. At this figure the cores, not memory, limit this pool on every fleet machine.
ORACLE_SHARD_BYTES = 900_000_000
# The peak memory of one belt worker, the divisor of gate:conform's width (`conform_job_budget`). A worker is a spawn process running `conform.conformance_config_worker` for one acceptance configuration. It holds its interpreter, a HarfBuzz `Shaper` over M1.otf, the spec's alphabet, splitters, glyph names and anchors, the section 5.7 verdict surface the parent passes down, and its configuration's settle memo, mapped read-only (`conform._MemoStore`) and walked with `promote=False`. The mapping's columns and probe index are page-cache pages, resident once per machine and counted in a worker's resident set as its probes touch them, which over a belt is nearly all of them. The heap holds the file's label and outcome tables plus a dead byte and a reached byte a row. The worker also holds `windows`, the dict of windows the walk settles fresh: empty on a warm memo, every window on a cold one, and the term that grows. The `ss10` overlay worker maps no memo and reads under a tenth of the constant. The belt's controller reads under a quarter of the constant and is covered by the reserve, not by a term of its own.
# The seed is the `conform-belt` pool records of two machines in `doc/fleet.md`. On the 10-core M1 Pro 32 GiB MacBook Pro, three width-six hand `run_m1 --conform-only` belts finished between 2026-09-20T08:45:15Z and 09:28:24Z, their logs under `var/keep/issue-273/`: warm settlement workers read 0.383 to 0.388 GB, cold ones (memo files moved aside, so every window settles fresh) 0.911 to 0.916 GB, the `ss10` worker 0.061 to 0.063 GB, and the controller 0.28 GB. On the 18-core M5 Pro 48 GiB MacBook Pro, a cycle's gate:conform beside a live surface build and four hand belts finished between 2026-09-23T01:18:26Z and 01:43:38Z, their logs under `var/keep/issue-273/m5pro-48gib/`. The cycle's settlement workers, whose walks pruned the windows the witness stage had added to each memo and rewrote the file, read 0.382 to 0.384 GB. Warm hand workers at widths six and two read 0.231 to 0.237 GB. Cold ones read 0.904 to 0.933 GB at width six and 0.904 to 0.913 GB at width two, where a reused worker runs three configurations and keeps what the earlier ones left, yet reads no higher than a fresh one. The `ss10` worker reads 0.060 to 0.063 GB at width six, and the controller 0.28 GB. The constant is the highest reading, 0.933 GB, plus a quarter, rounded up to the tenth, because a cost that is too low pushes the machine into swap, and because the fresh-window dict grows with the window count, which `rebuild/scaling-ladder.txt` fits against letters and runes, so each new letter raises the cold reading. The conform-belt row of `make job-costs` checks it: `run_m1.run_font_conformance` writes one kind:"pool" record per pooled belt at `conform.BELT_HORIZON`, one observation per configuration. The gate:conform step peak is not used, because `peak_rss.reap_peak_rss_bytes` takes the maximum over the tree and so reads one process. Beside the build lane's larger step (the surface build's parent and workers, or the plumbing step's chain parent and refill pool, `_conform_build_lane`), memory does not limit this pool on any fleet machine at this figure; the acceptance-configuration count does.
CONFORM_BELT_BYTES = 1_200_000_000
# The width of gate:make-test's pytest pool under a cycle, which the cycle passes to that child and reserves for: `surface_job_budget` subtracts two cores and this pool's memory. Without it the pool runs at `-n auto`, which the root conftest.py resolves to every core, while the build beside it is sized as if the pool held only its reservation.
MAKE_TEST_POOL_WORKERS = 2
CONFORM_HORIZON_DEFAULT = 4
DEEP_SWEEP_HORIZON_DEFAULT = 5
# One letter past the build's own replay (`run_m1.REPLAY_HORIZON`), the depth at which a text first exercises a letter third slot behind a letter left. The deep replay checks that one extra letter, and `make replay-deep ARGS='--horizon 6'` goes deeper on demand.
DEEP_REPLAY_HORIZON_DEFAULT = 5
COMPILE_CODE_FILES = (
    "rebuild/pipeline/emit_gsub.py",
    "rebuild/pipeline/emit_gpos.py",
    "rebuild/pipeline/pack_gsub.py",
    "rebuild/pipeline/compile_font.py",
)
RETENTION_WINDOW_DAYS = 7
REBUILD_LANES = ("contracts",)


def rebuild_lane_green(lane: str) -> Path:
    """Return the path of the lane's green record. It is read from `cycle_paths` at call time because the rebuild conftest redirects the constant under tmp_path, so a test that drives the cycle cannot leave a record in rebuild/out that the next real pass would trust."""
    return {"contracts": cycle_paths.REBUILD_CONTRACTS_GREEN}[lane]


def rebuild_lane_argv(lane: str) -> list[str]:
    """Return the argv for one rebuild-suite lane. `--lane` is the rebuild conftest's option and also sets the pool width: under the contracts lane `-n auto` resolves to the cores this process may run on, since no worker holds a live build artifact, and under a cycle the width the plan sets in the child's environment (`contracts_pool_width`) narrows it to the cores the surface build leaves. Every run prints its twenty-five slowest tests, so the lane's own log shows where its time went. The argv also names the two closure files beside the green record: the selection file the caller writes just before the spawn, naming the tests the record shows are unaffected, and the sidecar the suite writes at session end with every test's recorded closure. Both are resolved from `rebuild_lane_green` at call time, so a test that redirects the record redirects them too."""
    argv = [
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
    if lane == "contracts":
        from rebuild.tools import contracts_closure

        record = rebuild_lane_green(lane)
        argv += [
            "--closure-skip",
            str(contracts_closure.selection_path(record)),
            "--closure-record",
            str(contracts_closure.sidecar_path(record)),
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


def record_plumbing_green(fingerprint: str, path: Path | None = None) -> None:
    """Write the verdict plumbing's green record: the key alone, like every record `read_green_record` parses. A pass that needs the frontier derives it from disk (`frontier_carry_out`), because a later export could outrank a copy remembered here."""
    record_green(path if path is not None else cycle_paths.PLUMBING_GREEN, fingerprint)


def frontier_carry_out() -> Path | None:
    """Return the stamp-aligned frontier file, for the summary of a pass that wrote no carry of its own. It is derived from disk the way every consumer derives it (`status.pick_frontier`), not remembered in a green record a later export could outrank."""
    from rebuild.review.status import pick_frontier

    try:
        stamp = json.loads((REVIEW_OUT / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError:
        return None
    if not isinstance(stamp, str):
        return None
    hit = pick_frontier(ROOT, stamp)
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
    """Return whether `make test` would check nothing: the pass is not forced and the closure's fingerprint equals the one in the shared green record. A fingerprint of None (no git or no make) never skips. The cycle's plan and rebuild.tools.make_test_gate both use this predicate. The plan reserves gate:make-test's cores and memory beside the surface build only when it returns False, and its argv (`make_test_gate_argv`) passes the wrapper --force only when the plan was forced. So both decisions come from one fingerprint and one record, and no pass reserves cores for a gate that then skips."""
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
    "tools/build_kerning_hardcases.py",
    "tools/departure_mono_import.py",
    "tools/derived_demote_oracle.py",
    "tools/extract_glyph.py",
    "tools/glyph_compiler.py",
    "tools/inspect_join.py",
    "tools/leak_classify.py",
    "tools/leak_contract_report.py",
    "tools/leak_emergent_families.py",
    "tools/leak_enforcement_oracle.py",
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
    """Return one file's digest for the rebuild lane's closure. Rune YAMLs, the divergence ledger and the standing approvals are hashed prose-blind (`fingerprint.rune_file_digest`, `divergence_ledger_digest`, `standing_approvals_digest`), so a documentation edit does not re-run the gate. uv.lock is hashed by its dependency pins (`fingerprint.lock_digest`), so a version bump does not re-run it either while a changed pin does; no test reads the project's own block, and the pinned packages decide the interpreter the lane runs under. Leaving the prose out is safe because of what the tests read from those files. The contracts tests that load the live runes check structure, settlement outcomes and round-trip identity, and those that load the live ledgers read ids, `no_verdict`, `match` and the exemplar keys, never a `why` or a `note`. The only live readers of those fields are the surface's explain panel and the standing fill, and both are keyed elsewhere: the ledger's `why` in the Stage B `explain_prose` component, and the fill's copy of a rule's `note` in `plumbing_skip_fingerprint`, which hashes the file raw for that reason."""
    from rebuild.pipeline import fingerprint

    prose_blind = {
        fingerprint.DIVERGENCE_LEDGER_LABEL: fingerprint.divergence_ledger_digest,
        fingerprint.STANDING_APPROVALS_LABEL: fingerprint.standing_approvals_digest,
        "uv.lock": fingerprint.lock_digest,
    }
    digest = prose_blind.get(rel)
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
    """Return the per-file `label\\tdigest` lines behind `run_m1_skip_fingerprint`: every data input and pipeline module individually (rune files and the divergence ledger prose-blind), the contact allow-list by its own prose-blind digest, the full baselines as one value, the oracle's subset tables, and uv.lock by its dependency pins (`fingerprint.lock_digest`, which ignores the project's own version block, so a version bump leaves the line unchanged and a fontTools or uharfbuzz bump changes it). The green record stores these lines, so a skip miss can name which input changed, and the pass can check whether every changed label is comparison-side (`comparison_side_label`) and re-adjudicate over the artifacts on disk instead of rebuilding them.

    The allow-list is here and in no fingerprint component. Only the defect gate reads it, so a bless must change this key and should not change the surface's stamp or drop the unit cache. A missing allow-list contributes no line, as `path_lines` drops a missing file.
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
    """Return the content key over everything a full run_m1 reads: the data inputs and pipeline code per file, the contact allow-list the defect gate reads, the full baselines, the oracle's subset tables (which the `baselines` line covers only indirectly), and uv.lock's dependency pins. A key equal to the recorded green means a rerun would reproduce rebuild/out/m1 byte for byte. A different key says only that something changed; which lines changed decides whether the pass rebuilds or re-adjudicates (`gates_only_reuse`)."""
    return _digest_lines(run_m1_skip_lines(root))


def capped_labels(entries: list[str], limit: int = 8) -> str:
    """Return a label list for one line of a report, with the entries past `limit` counted instead of printed. The moved-inputs note and the reused-inputs note share it, so both cap the list the same way."""
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
    """Return whether one `run_m1_skip_lines` label names an input the comparison reads and the build does not. When only such inputs changed, a cycle can re-adjudicate over the tables and font on disk (`run_m1 --gates-only`) instead of running a kernel fan-out that would produce byte-identical artifacts. Four kinds qualify, and `fingerprint.tables_value` covers none of them, so an enumeration on disk stays current. The alias map, the divergence ledger and the kern sidecar (`fingerprint.NON_TABLE_DATA_LABELS`) are read to name, classify and position divergences over rows the fixpoint has already decided. The contact allow-list (`fingerprint.CONTACT_ALLOW_LABEL`) is read by the defect gate, which reads minted glyphs and mints none. The oracle's two modules (`fingerprint.COMPARISON_CODE_MODULES`) are the classifier those files feed and the position channel that shapes the rows it calls ink-identical, and rebuild/test_build_code_closure.py checks that the build imports neither. The `baselines` line and the `baseline-<config>.subset.tsv.gz` lines are the before side of the comparison, which no table stage or emitter reads.

    `uv.lock` is not comparison-side, although the tables' stamp does not cover it either. It pins fontTools and uharfbuzz, so a bump can change the compiled font's bytes and what HarfBuzz does with them, and this must never permit reusing a font a different toolchain built. Its line covers only the dependency pins (`fingerprint.lock_digest`), so the project's own version bump leaves the line unchanged and never reaches this question.
    """
    from rebuild.pipeline import fingerprint

    if label in fingerprint.NON_TABLE_DATA_LABELS or label == fingerprint.CONTACT_ALLOW_LABEL:
        return True
    if label == "baselines" or (label.startswith("baseline-") and label.endswith(".subset.tsv.gz")):
        return True
    return label in {f"rebuild/pipeline/{name}" for name in fingerprint.COMPARISON_CODE_MODULES}


def gates_only_reuse(record: dict | None, current: dict[str, str]) -> list[str] | None:
    """Return the labels that changed since the last green M1 build when every one is comparison-side, and None otherwise. The cycle uses this to plan the gates-only route, and the pass uses it to decide whether it may record a green. None means no reuse in three cases: there is no green record, nothing changed (the plain skip's case), or a build-side input changed and the tables must be rebuilt.

    The reuse is sound only with a second check. The prior green shows the artifacts on disk came from a completed build over every build-side input, and `m1_tables_stamped` shows none of those inputs has changed since. Both are checked before the route is taken, and a gates-only pass records its green on the same pair.
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
    """Return what the inputs a run_m1 skip miss named will cost the oracle's per-row verdict store. The store invalidates at three levels, which look different in the timings. A rune file changes one family key and re-derives only the rows that can reach that letter. Anything in the comparison's code closure, or any other input the whole-store stamp hashes, drops every row of every configuration. An input only the position stamp hashes (the position channel's module, the kern sidecar, the toolchain lock) keeps every row verdict and re-shapes every position. An edit confined to the classifier's module, rebuild/pipeline/oracle.py, returns None, as a divergence-ledger edit does: the classifier is in neither stamp and re-runs over served verdicts, so the store costs nothing. The note names the second and third cases because an oracle that serves no rows after a legitimate class-membership or pipeline edit is expected and would otherwise look like a broken cache. Both sides of the comparison are repo-relative labels, the form `moved_inputs_note` reports in; matched against basenames, nothing would match and no error would show. No rune verdict is given when the note was truncated, since the inputs it left out could be anything."""
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
    whole_store = sorted({name for name in names if name in stamped})
    if whole_store:
        return f"the oracle row cache drops whole: {', '.join(whole_store)} is inside its stamp"
    position_store = sorted({name for name in names if name in positions})
    if position_store:
        return f"the oracle row cache keeps its rows and re-shapes every position: {', '.join(position_store)} is inside its position stamp"
    if not moved.endswith(" more") and names and all(name in runes for name in names):
        return "the oracle row cache re-derives only the rows reaching those runes"
    return None


def m1_artifacts_present(root: Path = ROOT) -> bool:
    """Return whether rebuild/out/m1 still holds everything a skipped run_m1 must leave: the three gate summaries and the artifacts the surface build reads."""
    m1 = root / "rebuild" / "out" / "m1"
    names = [path.name for path in cycle_paths.M1_SUMMARY_FILES.values()] + list(M1_ARTIFACT_NAMES)
    return all((m1 / name).exists() for name in names)


def m1_tables_stamped() -> bool:
    """Return whether the serialized window enumerations under rebuild/out/m1 were built from the sources on disk: `run_m1.serialized_tables` against `run_m1.tables_inputs`, the stamp the sweep and a gates-only pass check. It checks the artifacts themselves, not a record of a past run, and shows that the M1.otf beside those tables is the font the runes on disk describe. It is the second condition for the gates-only route; `gates_only_reuse` is the first. It takes no root parameter, like `deep_sweep.tables_stamped`, because the stamp is computed over the live repo, and a caller naming another tree would compare that tree's tables against this one's sources."""
    from rebuild.pipeline import run_m1

    return run_m1.serialized_tables(run_m1.OUT_DIR, run_m1.tables_inputs()) is not None


def m1_stage_a_current(root: Path = ROOT) -> bool:
    """Return whether the Stage A record under rebuild/out/m1 already matches what a pass over the sources on disk would write. On the run_m1 skip route it always does. On the gates-only route it decides whether the surface can skip before the pass runs: that pass rewrites the record from the same sources, and the surface build reads nothing else the pass writes, since the audit and the subset tables change only when a Stage A component does."""
    from rebuild.pipeline import fingerprint

    recorded = fingerprint.read_stage_a(root / "rebuild" / "out" / "m1")
    return recorded is not None and recorded == fingerprint.stage_a(root)


CONFORM_NO_SIDECAR_LINE = "behavior_classes\tabsent"


def conform_skip_lines(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> list[str]:
    lines = deep_sweep_skip_lines(root)
    if lines is None:
        lines = [CONFORM_NO_SIDECAR_LINE]
    lines.append(f"horizon\t{horizon}")
    return lines


def conform_skip_files(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> dict[str, str]:
    return _files_of(conform_skip_lines(root, horizon))


def conform_skip_fingerprint(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> str:
    """Return the content key over what the belt tests for, matching the deep sweep's key: the deep sweep's lines (`deep_sweep_skip_lines`: the behavior-class set the build enumerated from the emitted lookup, the font-compilation code in `COMPILE_CODE_FILES` and the tools/ closure the compile runs, and the uharfbuzz version) plus the horizon, so a green at a shallower horizon cannot satisfy a deeper gate. A build that left no behavior-class sidecar contributes `CONFORM_NO_SIDECAR_LINE` instead of the classes, a key no sweep records a green under, since every sweep runs over a build that wrote one.

    The key leaves out the rune digests, the tables' stamp and M1.otf's bytes, because a rune edit changes all three on every pass. After the crate's string replay (`run_m1.run_replay_strings`, on every build), what the belt still checks is how HarfBuzz applies the shapes the emitted lookup gives it, which depends on a rule shape, the code that turns a plan into bytes, and the shaper. An edit that creates no new shape gives the belt nothing it has not already shaped, so its green stays valid. The class enumeration fails closed (`emit_gsub.behavior_classes`), so a new shape always changes the key. The accepted cost is that a disagreement only HarfBuzz can see waits for the next code change or deep sweep instead of the next rune edit. The set of texts the belt sweeps is unchanged.
    """
    return _digest_lines(conform_skip_lines(root, horizon))


def deep_sweep_skip_lines(root: Path = ROOT) -> list[str] | None:
    """Return the deep sweep's arming key lines: the behavior-class set the build enumerated (rebuild/out/m1/behavior_classes.json, written by `emit_gsub.behavior_classes`), the font-compilation code that turns a plan into bytes (the pipeline modules in `COMPILE_CODE_FILES` and the tools/ closure compile_font passes the mini font to, `fingerprint.font_compile_tool_paths`, since an edit to the glyph compiler or the FEA emitter changes M1.otf's bytes and must arm this sweep and the belt together), and the shaper version. None when no build has left a sidecar, in which case the caller should run the cycle before asking whether the deep sweep is armed.

    The key leaves out the rune digests and M1.otf's bytes, because a rune edit changes both on every pass, and the deep sweep tests HarfBuzz behavior at a depth the belt cannot reach. It tests the set of shapes the emitted lookup gives the shaper, so an edit that creates no new shape leaves nothing new for a deeper run to find, and its green stays valid. There is no horizon line: a deep sweep runs at any horizon from the belt's 4 up (5 by default), so the depth a green covered is stored in the record's payload and compared with >=. Hashing it into the key would make a horizon-6 green fail a horizon-5 question.

    Each class is its own line label, not a shared `class` label with the token as its value, so the per-file map behind the key (`_files_of`, stored in the green record) has one entry per token and `moved_inputs_note` can name the new shape, which is all the "armed" report says.
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
    lines += [f"{rel}\t{_sha256_path(root / rel)}" for rel in COMPILE_CODE_FILES]
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
    fingerprint: str, horizon: int, files: dict[str, str] | None = None, path: Path | None = None
) -> None:
    """Write the deep sweep's green record. It stores the horizon the run swept as well as the key, because the arming key ignores depth: `deep_sweep_status` reads the horizon back to decide whether a run went deep enough for the depth asked about."""
    _record_outcome(
        path if path is not None else cycle_paths.DEEP_SWEEP_GREEN,
        {"fingerprint": fingerprint, "horizon": horizon, "files": files},
    )


def record_deep_replay_green(
    runes: dict[str, str], horizon: int, structure: str | None, path: Path | None = None
) -> None:
    """Write the deep replay's green record (`rebuild.tools.deep_replay`): the horizon the walk reached, every rune's prose-blind digest as the walk covered it (under `files`, so `moved_inputs_note` can name what changed since), the replay structure stamp, and a fingerprint over the rune lines so `read_green_record` reads it like every other record. A rune the record does not have counts as changed."""
    lines = [f"{name}\t{digest}" for name, digest in sorted(runes.items())]
    _record_outcome(
        path if path is not None else cycle_paths.DEEP_REPLAY_GREEN,
        {"fingerprint": _digest_lines(lines), "horizon": horizon, "structure": structure, "files": runes},
    )


def deep_replay_moved(record: dict | None, runes: dict[str, str]) -> list[str]:
    """Return the runes whose texts the next deep replay has to walk, sorted: every rune whose current prose-blind digest differs from the record's, including runes the record never walked. Every rune when there is no record. A rune in the record that no longer exists is ignored, since no text names it."""
    if record is None or not isinstance(record.get("files"), dict):
        return sorted(runes)
    recorded = record["files"]
    return sorted(name for name, digest in runes.items() if recorded.get(name) != digest)


def deep_replay_green_path(root: Path | None = None) -> Path:
    """Return where a tree keeps the deep replay's record: `cycle_paths.DEEP_REPLAY_GREEN` for the live repo, and the same relative place under any other root, so a status for an invented tree never opens the live record. The root defaults at call time, so a test that re-roots the module re-roots this too."""
    if root is None or Path(root).resolve() == ROOT.resolve():
        return cycle_paths.DEEP_REPLAY_GREEN
    return Path(root) / "rebuild" / "out" / "deep-replay-green.json"


def deep_replay_status(
    root: Path | None = None, horizon: int = DEEP_REPLAY_HORIZON_DEFAULT
) -> tuple[str, str]:
    """Return whether the deep replay is current for the runes on disk, as (status, note) for the cycle's one-line report beside the deep sweep's. `current` means the record has every rune at its current digest and reached this depth or deeper. `armed` names the runes whose content changed since the recorded walk, or the shallower depth it reached, and `make replay-deep` is the fix. `never-run` means there is no record. This only reports: the deep replay is never a cycle gate, for the cost `rebuild/tools/deep_replay.py` states."""
    from rebuild.pipeline import fingerprint

    root = ROOT if root is None else root
    record = read_green_record(deep_replay_green_path(root))
    if record is None:
        return (
            "never-run",
            "no deep replay has been recorded; run `make replay-deep ARGS='--all'` once, overnight",
        )
    moved = deep_replay_moved(record, fingerprint.rune_digests(root))
    if moved:
        return (
            "armed",
            f"{capped_labels(moved)} moved since the last horizon-{record.get('horizon')} walk; run `make replay-deep`",
        )
    recorded = record.get("horizon")
    if not isinstance(recorded, int) or recorded < horizon:
        return (
            "armed",
            f"the recorded deep replay reached horizon {recorded}, shallower than {horizon}; run `make replay-deep`",
        )
    return "current", f"horizon {recorded}"


def deep_sweep_status(root: Path = ROOT, horizon: int = DEEP_SWEEP_HORIZON_DEFAULT) -> tuple[str, str]:
    """Return whether the periodic deep sweep is current for what the build emits, as (status, note) for the cycle's one-line report. `current` means a green record matches the arming key at this depth or deeper. `armed` means something the deep sweep tests for has changed (a new rule shape, the compilation path, the shaper) or the recorded run was shallower than asked, and `make conform-deep` is the fix. `never-run` means there is no record, and `unknown` means no build has left a behavior-class sidecar to key on. This only reports: the deep sweep is never a cycle gate."""
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
    """Return every tracked or untracked-unignored repo file the rebuild pytest suite can read, the base of the suite's input closure. That is rebuild/ and glyph_data/ (without Markdown and the paths in `cycle_paths.REBUILD_GATE_EXEMPT_PREFIXES`: the carried-verdict evidence, the JS-only jstests, the census pins, and the contact allow-list), the root conftest.py, pyproject.toml and uv.lock, and the harness list REBUILD_GATE_HARNESS_PATHS. The harness list is what the suite reads outside those trees, and it is why the Markdown filter has an exception: the filter drops prose no test opens, but rebuild/test_review_enrich.py checks the surface's letter table against doc/glyph-names.md.

    The harness list comes from an audit of every file the suite opens over a green run, not from the tree, and every entry has a named reader. rebuild/validation/pins.py collects its pin runs from the three site corpora and puts test/ on sys.path to import test/test_shaping.py, which reads postscript_glyph_names.yaml at the repo root and imports the tools/ compile modules. rebuild/review/drafts.build_corpus_index reads the same three corpora. The unit-cache environment stamp hashes tools/*.py whole, which is why the list has every tools/*.py file and not only the compile modules. rebuild/test_review_build.py checks the review surface's feature descriptions against README.md's stylistic-set list.

    The census pins are exempt because the suite does not read them and the census step rewrites them during the pass; including them would change the key of every pass that refreshes them. The allow-list is exempt because no test reads the live file (only a fake repo writes one), so a bless would re-run the whole suite for nothing. The divergence ledger and the standing approvals stay in, since tests read them, and `_closure_digest` hashes them prose-blind, so rewording a `why` or a `note` does not change the key while a structural edit does. None when git is unavailable, in which case the caller must run the gate.
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


def rebuild_lane_fingerprint(root: Path, lane: str) -> str | None:
    """Return the content key over one lane's input closure: the digest `rebuild_lane_closure` computes, whose per-label lines define what the key covers."""
    return rebuild_lane_closure(root, lane)[0]


def rebuild_lane_closure(root: Path, lane: str) -> tuple[str | None, dict[str, str] | None]:
    """Return the rebuild suite's input closure as the key and the per-label digest map the key is computed from. One pass over the files produces both, and because the map is the key's only source, every input that can change the key is a label the per-test selection can see change. The closure covers the repo files from `rebuild_gate_closure_files`, which has already dropped the exempt paths, so a contact-signature bless changes nothing here. It also covers the site fonts, hashed as one `fonts` label through `fingerprint.fonts_value` without their `head` and `name` tables. They are `make all` output that the shaping tests measure against, no rune edit changes them, every baseline header records the Senior font's sha, and `baseline_subset.prove_font_provenance` checks the oracle's tables against it. Leaving out those two tables means the `make all` a version bump runs changes nothing here, while a glyph, anchor or layout change does. The closure contains no build artifact, so a verdict-only or artifact-only cycle re-runs nothing here, and an M1 rebuild, which writes only under rebuild/out, cannot change the key during a pass. The harness list is included because collecting rebuild/ imports test/test_shaping.py in every process, and that imports the tools/ modules. The rune files, the divergence ledger and the standing approvals are hashed prose-blind, because the tests that load the live spec and ledgers read structure, not prose. The verdict store is not in the closure (the suite uses it only through fixtures), so a verdict-only cycle skips the suite. None when git is unavailable, in which case the caller must run the suite."""
    from rebuild.pipeline import fingerprint

    if lane not in REBUILD_LANES:
        raise ValueError(f"{lane!r} is not a lane of the rebuild suite; the lanes are {REBUILD_LANES}")
    files = rebuild_gate_closure_files(root)
    if files is None:
        return None, None
    labels = {rel: _closure_digest(root, rel) for rel in files}
    labels["fonts"] = fingerprint.fonts_value(root, fingerprint.font_paths(root))
    return _digest_lines([f"{label}\t{digest}" for label, digest in labels.items()]), labels


def surface_build_skippable(
    root: Path = ROOT, review_out: Path | None = None, ignore: tuple[str, ...] = ()
) -> bool:
    """Return whether rebuilding the review surface would reproduce its content byte for byte, so the build can be skipped and the autosave stays aligned. True only when the manifest's recorded inputs fingerprint equals the one a build would stamp now (Stage A as run_m1 recorded it, Stage B recomputed) and every shard the manifest names is present. `generated_at` is derived from mtimes, so a rebuild after mtime-only changes (git checkout, touch) could restamp it with identical content. Skipping keeps the existing stamp, and with it the manifest's alignment with the autosave.

    The three files the manifest does not name, the per-unit index and both app sidecars, must each be stamped for the manifest beside them, not merely present. They are written after the manifest and outside it, so a build interrupted between the two, or a manifest rewritten by something that does not rewrite them, leaves every shard present and sidecars that describe a surface that no longer exists. A skip on shard existence alone would then serve that surface indefinitely. `unit_index.index_is_current` and `app_index.artifact_is_current` check the stamps, so a skip means a rebuild would reproduce this surface's content in full.

    The after font is compared with the file on disk because no fingerprint component covers it: the key hashes the font's inputs and the two site fonts, never rebuild/out/m1/M1.otf itself, so a run_m1 that finished after this surface was built changes nothing the comparison above sees, while the surface still ships the previous build's font. The build asserts at copy time that the font it ships is the font it hashed at load, so the manifest's after-font sha describes fonts/after.otf, and comparing that sha with the current M1.otf shows the skip is not passing over a newer font.

    `ignore` names fingerprint components left out of the comparison, for a caller asking a narrower question than byte identity, with the same hard/warn split `status._freshness_check` uses. The cycle asks three questions in turn. The strict one comes first, since a surface that reproduces byte for byte needs nothing done. When only an ASSET_COMPONENTS member differs, the cycle copies those assets over the served surface and restamps that one component (`assets-refresh`) instead of rebuilding units that cannot have changed. When the live surface fails both, `promotable_surface` asks the strict question of a rehearsal's directory, and the pass moves that directory into place when it matches (`surface-promote`). A component missing from either side still fails the comparison: only a component present in both the recorded and the expected set can be ignored.
    """
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
    ignored = set(ignore) & set(recorded) & set(expected)
    if {key: value for key, value in recorded.items() if key not in ignored} != {
        key: value for key, value in expected.items() if key not in ignored
    }:
        return False
    try:
        shards = [part for meta in manifest["classes"] for part in unit_index.class_shards(meta)]
    except KeyError, TypeError, AttributeError:
        return False
    if not all((surface / shard).exists() for shard in shards):
        return False
    try:
        after_sha = manifest["fonts"]["after"]["sha256"]
    except KeyError, TypeError:
        return False
    if not isinstance(after_sha, str):
        return False
    if after_sha != _sha256_path(root / "rebuild" / "out" / "m1" / "M1.otf"):
        return False
    return unit_index.index_is_current(surface) and all(
        app_index.artifact_is_current(surface, name, fmt) for name, fmt in app_index.ARTIFACTS
    )


def _manifest_stamp_at(surface: Path) -> str | None:
    try:
        stamp = json.loads((surface / "manifest.json").read_text()).get("generated_at")
    except OSError, ValueError, AttributeError:
        return None
    return stamp if isinstance(stamp, str) else None


def promotable_surface(
    root: Path = ROOT, summary_path: Path | None = None, live: Path | None = None
) -> Path | None:
    """Return the rehearsal directory a live pass can move into rebuild/out/review instead of rebuilding, or None. A rehearsal (`--review-out`) writes a whole surface (shards, sidecars, unit store and signature store) where the live pass never reads, and the next live pass would otherwise rebuild the same bytes cold, because its own store is stamped for the pre-rehearsal environment. Candidates are checked in order: the `plan.review_out` the last cycle summary recorded (a repo-relative string, resolved against `root`), then `var/rehearsal-review` under `root`, the conventional directory (`--review-out` takes any path, but a rehearsal is expected to use that one). Every path derives from `root`, so a scratch repo never reads the live rehearsal.

    A candidate is promotable when it is a directory other than the live one, on the live directory's filesystem (`os.replace` cannot cross filesystems, and a plan must never print a move it cannot make), when `surface_build_skippable` returns True for it (that function defines "reproduces these inputs byte for byte", including the after font and the three stamped sidecars), and when its `generated_at` is not older than the live surface's. The byte-identity check cannot supply the stamp condition: `generated_at` is the latest input mtime, not a build time (`_generated_at` in rebuild/review/build.py), so a rehearsal can have a stamp older than the surface it would replace, and merge_verdicts refuses a store stamped newer than the surface it merges onto. A backwards promotion would fail the plumbing step after the tree had already moved. An unreadable manifest rules the candidate out, which costs only a rebuild.
    """
    live_dir = live if live is not None else REVIEW_OUT
    summary = summary_path if summary_path is not None else root / "rebuild" / "out" / "cycle_summary.json"
    candidates: list[Path] = []
    try:
        recorded = json.loads(summary.read_text()).get("plan", {}).get("review_out")
    except OSError, ValueError, AttributeError:
        recorded = None
    if isinstance(recorded, str) and recorded:
        candidates.append(root / recorded if not Path(recorded).is_absolute() else Path(recorded))
    candidates.append(root / "var" / "rehearsal-review")
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
        if surface_build_skippable(root, review_out=candidate):
            return candidate
    return None


def promote_surface(source: Path, live: Path | None = None) -> None:
    """Move a rehearsal's surface into place as the live one. It uses two renames instead of a removal and a move: the live tree is renamed to `.superseded`, the source takes its place, and only then is the old tree deleted. So a surface is on disk throughout the seconds a 3.5 GB rmtree takes, and if the second rename fails the live tree is put back. Deleting the old tree is best effort: once the second rename has returned, the promotion is done, so a tree that will not delete is left for `recover_superseded_surface` at the next real pass's start instead of being reported as a failed move. That function also handles a pass that died between the two renames, when the `.superseded` tree is the only surface on disk; the rmtree at the start here clears a leftover beside a live tree.

    Moving is safe where reconstructing would not be. Every stamp inside a surface depends only on content, through `unit_index.manifest_sha256` (the per-unit index, both app sidecars, the unit store's header and the signature store's), the manifest records no output path, and a rebuild would restamp `generated_at`, which the autosave alignment depends on. So the promoted directory satisfies `surface_build_skippable` as it did where it was built, and both stores arrive warm. The one manifest field the move leaves stale is `repo_head`, the commit the rehearsal ran at: the app banner and `make verdict-ready` show it, and a commit outside every fingerprint component changes HEAD without changing whether the surface is promotable. Nothing the cycle keys on reads it.
    """
    live_dir = live if live is not None else REVIEW_OUT
    superseded = live_dir.with_name(f"{live_dir.name}.superseded")
    shutil.rmtree(superseded, ignore_errors=True)
    os.replace(live_dir, superseded)
    try:
        os.replace(source, live_dir)
    except OSError:
        os.replace(superseded, live_dir)
        raise
    shutil.rmtree(superseded, ignore_errors=True)


def recover_superseded_surface(live: Path | None = None, *, delete: bool = True) -> str | None:
    """Handle whatever a promotion left under the `.superseded` name, before a pass checks whether it has a surface. Beside a live tree it is the old surface whose delete did not finish, and it is deleted. Alone, it is the live surface that a pass which died between the two renames had moved aside, and one rename puts it back, so the next pass reads the surface it had instead of starting as a first run. It runs at the start of every pass because the green-finish retention never runs on the failed or first-run passes that leave the tree behind. `delete=False` is the dry run's form: the delete cannot be undone and no plan question reads the tree it removes, so the tree stays for the next real pass, while the put-back still runs, because every plan question reads the live surface and a dry run's plan must be the one a real pass follows. Returns the line to print, or None when there was nothing to do."""
    live_dir = live if live is not None else REVIEW_OUT
    superseded = live_dir.with_name(f"{live_dir.name}.superseded")
    if not superseded.exists():
        return None
    if live_dir.exists():
        if not delete:
            return f"Left {superseded}, the surface a promotion replaced, for the next real pass to delete."
        shutil.rmtree(superseded, ignore_errors=True)
        return f"Deleted {superseded}, the surface a promotion replaced."
    os.replace(superseded, live_dir)
    return f"Put {superseded} back as the live surface; the promotion it stepped aside for did not finish."


# The chain's code, listed by module instead of all of rebuild/tools/: the import closure of rebuild.tools.verdict_chain, which runs every step. rebuild/test_plumbing_closure.py checks the list against the walked import graph on every contracts run. This driver is not an entry point, because every argument it passes the chain names an input the key already hashes (the surface, the master, the store), a flag that disables the skip, or a width (`--standing-fill-jobs`) that cannot change the chain's output, and the chain parses its own flags in verdict_chain. The walk stops at the modules in `fingerprint.pipeline_code_paths`, because the key includes the pipeline_code component whole through its manifest line. The rebuild/tools/ modules the pipeline imports (memory_budget, peak_rss, lock_digest, site_fonts and others) are outside this list, which keeps fan-out widths and cost readings out of the verdict key.
PLUMBING_ENTRY_POINTS = ("rebuild.tools.verdict_chain",)
PLUMBING_TOOL_MODULES = (
    "carry_verdicts",
    "complaint_docket",
    "console",
    "echo_verdicts",
    "merge_verdicts",
    "review_docket",
    "review_server",
    "standing_client",
    "standing_verdicts",
    "verdict_chain",
    "verdict_notes",
)


def plumbing_code_paths(root: Path = ROOT) -> list[Path]:
    return [Path(root) / "rebuild" / "tools" / f"{name}.py" for name in PLUMBING_TOOL_MODULES]


def plumbing_skip_fingerprint(
    root: Path = ROOT, surface: Path | None = None, master: Path | None = None
) -> str | None:
    """Return the content key over everything the verdict plumbing reads: the surface it resolves unit ids against, the verdicts master it carries forward, the live store it merges into, the checked-in standing approvals, and the chain's own code. The standing approvals are hashed by raw bytes, unlike the prose-blind hash the rebuild lane uses: `standing_verdicts` copies each rule's `note` into the verdict note of every fill it writes, so rewording a note changes the chain's output and must re-run it. Carry, merge, both fills with their merges, and the complaint docket are pure functions of these inputs, and the chain is idempotent once it has run, so a key matching the record a complete chain left means re-running it would write nothing new. The master is in the key because the autosave's hash cannot see it: an export at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code is in the key for the same reason every other key includes its stage's code: a fix to a fill's matcher or to the carry's join must run, not be skipped. It is the chain's import closure (`plumbing_code_paths`, which a contracts test checks against the chain's import graph) plus the review/ modules the chain runs and the surface build does not: serve.py and verdict_store.py, through which merge_verdicts reads the store, and status.py and journal.py, which the merge and the readiness check run. review/'s build-side modules are covered by the manifest fingerprint's review_code. The manifest line leaves out `unit_index.ASSET_COMPONENTS`, because no chain step reads the copied app assets, and an assets refresh rewrites that field and must not re-run a chain whose real inputs are unchanged. None when the surface has no fingerprinted manifest or no master was resolved."""
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
        "manifest\t"
        + json.dumps(
            {key: value for key, value in fp.items() if key not in unit_index.ASSET_COMPONENTS},
            sort_keys=True,
        ),
        f"generated_at\t{manifest.get('generated_at')}",
        f"master\t{master}\t{_sha256_path(Path(master))}",
        f"autosave\t{_sha256_path(root / 'verdicts-autosave.json')}",
        f"standing\t{_sha256_path(root / 'rebuild' / 'standing-approvals.yaml')}",
        f"tools_code\t{fingerprint.hash_paths(root, plumbing_code_paths(root))}",
        f"serve\t{_sha256_path(root / 'rebuild' / 'review' / 'serve.py')}",
        f"verdict_store\t{_sha256_path(root / 'rebuild' / 'review' / 'verdict_store.py')}",
        f"status\t{_sha256_path(root / 'rebuild' / 'review' / 'status.py')}",
        f"journal\t{_sha256_path(root / 'rebuild' / 'review' / 'journal.py')}",
    ]
    return _digest_lines(lines)


def evaluate_run_m1_gate(pipeline: dict, manual_pins: dict, oracle: dict) -> CheckVerdict:
    """Decide whether the M1 build passed from its three summary JSONs: defect_errors, the Manual-pin verdict, and multi_matched. UNMATCHED oracle rows are never a failure; they are expected during the migration and are judged on the review surface. The verdict carries no UNMATCHED or multi_matched counts, because both callers already hold the oracle summary. The pin verdict is run_m1's own (`manual_pin_gate_failure`), including its scope, so a gate that replayed nothing cannot pass here either."""
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

    return CheckVerdict(
        check="run_m1",
        verdict="red" if failures else "green",
        status="FAILED" if failures else "green",
        failures=failures,
        failed_ids=[],
    )


def evaluate_conform_gate(summary: dict | None) -> CheckVerdict:
    """Judge gate:conform from conform_summary.json's contents (None when the subprocess wrote none). `pass` is the verdict, and the belt fails in only one way: a font-versus-settlement divergence, which is a compiler defect. Whether the font holds every rule the build planned is checked by read-back inside run_m1 on every build, and dead generated rules by the build's witness stage (`run_m1.run_rule_witnesses`, over the certificates the crate writes beside the rules); neither reaches this summary. The sweep reports a count of divergences, not named cases, so there are no failed ids: a divergence names a window, and the audit beside the summary lists them."""
    if summary is None:
        return CheckVerdict(
            check="conform",
            verdict="red",
            status="FAILED (no conform_summary.json)",
            failures=["conform gate: run_m1 --conform-only wrote no summary"],
            failed_ids=[],
        )
    failures: list[str] = []
    if summary.get("divergences"):
        failures.append(f"conform gate: {summary['divergences']} font-vs-settle divergence(s)")
    if not summary.get("pass") and not failures:
        failures.append("conform gate: pass is false")
    return CheckVerdict(
        check="conform",
        verdict="red" if failures else "green",
        status="FAILED" if failures else "green",
        failures=failures,
        failed_ids=[],
    )


def conform_gate_argv(jobs: int, horizon: int = CONFORM_HORIZON_DEFAULT) -> list[str]:
    argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--conform-only", "--jobs", str(jobs)]
    if horizon != CONFORM_HORIZON_DEFAULT:
        argv += ["--conform-horizon", str(horizon)]
    return argv


STEP_DESCRIPTIONS = {
    "run_m1": "Builds the M1 tables for every settlement configuration in the Rust kernel (the ss10 overlay settles nothing and gets none), mints the glyphs, emits GSUB and GPOS, compiles the font, and reads it back. Then runs the defect gates, the Manual-pin gate, and the oracle over what it built.",
    "run_m1:gates-only": "Re-adjudicates the tables and font already on disk with the defect gates, the Manual-pin gate, and the oracle, rebuilding nothing. Taken when only comparison-side inputs moved since the last green build.",
    "surface-build": "Rebuilds the review surface: every unit the tables reach is drafted, enriched, and checked, with cache-served units re-verified by content key. Writes the shards, manifest, and census sidecar that the app and the verdict plumbing read.",
    "assets-refresh": "Overwrites the served copy of the review app's JS, CSS, and HTML and restamps only the manifest's static component. No shard or sidecar moves, so the open tab's store stays aligned.",
    "surface-promote": "Moves the surface a rehearsal already built for these exact inputs into rebuild/out/review, unit store and signature store with it, and deletes the surface it replaces. Two renames in this process; no unit is drafted, enriched, or checked.",
    "plumbing": "Carries the verdicts master onto the new surface by unit id, merges it into the store, and runs the echo and standing fills to their fixpoint. Ends by writing the complaint docket of what still needs a human.",
    "census": "Rewrites rebuild/review-census-pins.json from the census sidecar the surface build emitted, names what moved in its invariant block against the last accepted census (diffing that block alone when it did), and holds the ledger's declarations against the classes the corpus reached. Committing the rewritten pins is how the census is accepted.",
    "gates": "The four post-build gates, skipped together under --skip-gates.",
    "gate:js": "Runs the review app's node test suite over its JavaScript. Fast, and independent of every build artifact.",
    "gate:conform": "Shapes the compiled font with HarfBuzz over the swept texts and checks it against a fresh re-settlement window by window, the split-buffer check at horizon 4 included; the ss10 overlay takes its own two-letter arm against the bare rendering. The proof that HarfBuzz does what the tables say over every rule shape the lookup emits; that the tables are complete over the same texts is run_m1's string replay, on every build.",
    "gate:rebuild-contracts": "Runs the rebuild suite: every test whose subject is the code, over checked-in fixtures and the hermetic mini bundle. Reads no live build artifact.",
    "gate:make-test": "Runs the main font suite and pyright over the whole tree, the same make test you run by hand. Skips when its input closure is unchanged since its last green run.",
    "job-costs": "Checks the recorded per-worker peaks against the memory-budget constants that size every fan-out. A drift here means a width somewhere is priced on stale numbers.",
    "retention": "Prunes the regenerable piles a green cycle leaves behind: stale carried files, stashes the journal already replays, the journal past its 7-day floor, and build logs beyond the last 10.",
}


def step_description(name: str) -> str:
    """Return a step's banner description from `STEP_DESCRIPTIONS`, printed on every run. The descriptions are kept beside the step definitions, not in a document, because a reader wants to know what a step checks while watching it run. Two keys are variants, not steps: `run_m1:gates-only` is the re-adjudication route, which spawns under that name and reports under run_m1's row, and `gates` is the placeholder --skip-gates leaves in place of the four gates. `_run_step` calls this instead of reading the plan because it receives a name, not a plan, and because one of the things it spawns, the job-costs diff, is a child of a step, for which the empty string is correct."""
    return STEP_DESCRIPTIONS.get(name, "")


SUBSTEP_PARENTS = {"invariant-diff": "census", "job-costs-diff": "job-costs"}


@dataclass
class Step:
    """One row of the plan. `skipped` is set explicitly instead of derived from `argv`, because `argv is None` also describes the retention and surface-promote steps, which do real work in this process, and the `gates` placeholder, which stands for four steps. The run/skip column and the counts line use `skipped`, so a step that runs without spawning anything still shows as running.

    The census's invariant diff, which the driver prints itself, and the job-costs diff it spawns are children of steps, not rows of the plan, and SUBSTEP_PARENTS names them. Registering one with the digest writes its output to the parent's log, shows its lines under the parent's column, and keeps `_run_step` from opening a second banner for a step that is already open.
    """

    name: str
    argv: list[str] | None
    note: str = ""
    lane: str = ""
    describe: str = ""
    skipped: bool = False


@dataclass
class Plan:
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
    reuse_run_m1: bool = False
    run_m1_note: str = ""
    run_m1_fingerprint: str | None = None
    fresh: bool = False
    skip_surface: bool = False
    refresh_assets: bool = False
    promote_surface: Path | None = None
    surface_note: str = ""
    skip_contracts: bool = False
    contracts_note: str = ""
    contracts_skip: list[str] = field(default_factory=list)
    contracts_files: dict[str, str] | None = None
    conform_note: str = ""
    conform_proven: bool = False
    skip_plumbing: bool = False
    plumbing_note: str = ""
    plumbing_store_only: bool = False
    record_greens: bool = False
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT
    surface_jobs: int = 1
    surface_reason: str = ""
    signature_jobs: int = 1
    signature_reason: str = ""
    standing_fill_jobs: int = 1
    standing_fill_reason: str = ""
    sweep_jobs: int = 1
    sweep_reason: str = ""
    kernel_threads: int = 1
    replay_threads: int = 1
    replay_reason: str = ""
    make_test_workers: int = 1
    contracts_workers: int = 1
    contracts_reason: str = ""
    conform_jobs: int = 1
    conform_reason: str = ""
    conform_horizon: int = CONFORM_HORIZON_DEFAULT
    review_out: Path | None = None
    surface_dir: Path = REVIEW_OUT
    complaints_note: str = ""
    retention: bool = False
    recipe_serves: bool = False
    stamp: str = ""
    log_dir: Path | None = None
    steps: list[Step] = field(default_factory=list)

    def step(self, name: str) -> Step | None:
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def describe(self, name: str) -> str:
        """Return the named step's banner text, for the two steps (surface-promote and retention) that run in this process and never reach `_run_step`."""
        step = self.step(name)
        return "" if step is None else step.describe

    def note_for(self, name: str) -> str:
        step = self.step(name)
        return "" if step is None else step.note

    def runs(self, name: str) -> bool:
        """Return whether the named step has a command line; a step the plan skipped has only a note."""
        return any(step.name == name and step.argv is not None for step in self.steps)

    def argv(self, name: str) -> list[str]:
        """Return the named step's command line, raising ValueError for a step the plan skipped. `build_plan` is the only writer of step argvs, so the executor runs the command line the plan printed."""
        for step in self.steps:
            if step.name == name:
                if step.argv is None:
                    raise ValueError(f"plan step {name!r} runs nothing: {step.note}")
                return step.argv
        raise KeyError(name)


def jstest_argv() -> list[str]:
    """Return the JS suite's argv. node v26 rejects the bare-directory form with 'Cannot find module', so the files are listed by the `*.test.js` glob, which is expanded here in Python because no shell runs the command."""
    files = sorted(str(path.relative_to(ROOT)) for path in JSTEST_DIR.glob("*.test.js"))
    return ["node", "--test", *files]


@functools.cache
def _font_suite_worker_bytes() -> int:
    """Return the root conftest.py's FONT_SUITE_WORKER_BYTES, the peak memory of one gate:make-test pytest worker, parsed from that file's source with `ast` instead of imported. pytest loads every conftest under the module name `conftest`, so in a run collected under rebuild/ (every run that tests this module) `import conftest` gets rebuild/conftest.py, and `import rebuild.conftest` would execute a second copy of a conftest pytest has already loaded, with its lane-audit hook. Parsing also keeps this build tool from importing pytest or inheriting that file's sys.path edits. A renamed constant raises here instead of silently dropping the cycle's reservation. The path is resolved from this file, not from ROOT, because a test that points the cycle at an invented root changes where artifacts go, not which test suite the gate runs."""
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
    """Return the width of gate:make-test's pytest pool under a cycle. The cycle passes this number to the child and reserves memory for it, so both use one value. Without it the pool would be `-n auto`, which the root conftest.py resolves to every core, while the builds beside it are sized to leave room for it. The width is MAKE_TEST_POOL_WORKERS, capped at `memory_budget.usable_cores()`, the count the root hook uses. PYTEST_XDIST_AUTO_NUM_WORKERS overrides both, because the child inherits this environment and will run at that width. It is parsed as the root hook parses it, so an unparseable value raises here, while the plan resolves, instead of inside the gate after the rest of the cycle has run."""
    from rebuild.tools import memory_budget

    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return max(1, int(stated))
    return max(1, min(MAKE_TEST_POOL_WORKERS, ncores or memory_budget.usable_cores()))


def _make_test_pool_bytes(*, skip_make_test: bool, ncores: int | None) -> float:
    """Return the memory gate:make-test's pytest pool holds beside the run_m1 step: FONT_SUITE_WORKER_BYTES times `make_test_pool_width`, or zero when the gate is skipped. The table build's width and the string replay's both subtract it."""
    return 0 if skip_make_test else _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)


def _wave_fit_terms(*, skip_make_test: bool, ncores: int | None) -> tuple[float, int]:
    """Return the co-resident bytes and the width cap that `kernel_threads_budget`, `replay_threads_budget` and `replay_threads_derivation` share. The co-resident term is gate:make-test's pytest pool (`_make_test_pool_bytes`) only: `kernel_exec.kernel_threads_default` adds `default`'s retained memo itself, and the replay builds its engines after the table build's process has exited. The cap is the smaller of the configuration count and the usable cores, the same bounds `run_m1._table_build_threads` and `run_m1._replay_threads` apply."""
    from rebuild.pipeline.conform import SETTLEMENT_CONFIGS
    from rebuild.tools import memory_budget

    coresident = _make_test_pool_bytes(skip_make_test=skip_make_test, ncores=ncores)
    return coresident, min(len(SETTLEMENT_CONFIGS), ncores or memory_budget.usable_cores())


def kernel_threads_budget(
    *, skip_make_test: bool = False, ncores: int | None = None, total_bytes: int | None = None
) -> int:
    """Return the kernel fan-out's width for this cycle, the `--kernel-threads` the plan passes run_m1. Memory limits this width because a live delta holds its whole working set until it emits. `kernel_exec.kernel_threads_default` does the arithmetic: memory, less the reserve, less the memo snapshot `default` keeps alive for the wave (`kernel_exec.DEFAULT_MEMO_BYTES`), divided by one delta (`kernel_exec.DELTA_PEAK_BYTES`). On the fleet this gives the whole wave on the 48 GiB machines and three deltas at once on the 32 GiB one, with or without gate:make-test's pool subtracted. The cycle subtracts that pool too, because it runs from t=0 through the whole table build: FONT_SUITE_WORKER_BYTES for each of the `make_test_pool_width` workers the cycle passes that child. A pass that skips the gate, including --skip-gates, subtracts nothing. AMS_KERNEL_THREADS overrides the arithmetic, as it does for a bare run_m1, so this reservation never narrows a stated width.

    The result is capped at the configuration count and the usable cores, as in `replay_threads_budget`. On the 48 GiB machines (`doc/fleet.md`) the memory result exceeds the configuration count, so without the cap the plan line would name a width the build never takes. With it, the plan line, the argv and `cycle_summary.json`'s `plan.kernel_threads` show the real width, and `run_m1._table_build_threads`'s own `min()` changes nothing. A stated AMS_KERNEL_THREADS above the configuration count is cut to it here, as `run_m1._table_build_threads` would cut it. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from rebuild.pipeline.kernel_exec import kernel_threads_default

    coresident, cap = _wave_fit_terms(skip_make_test=skip_make_test, ncores=ncores)
    return max(1, min(kernel_threads_default(coresident_bytes=coresident, total_bytes=total_bytes), cap))


def replay_threads_budget(
    *, skip_make_test: bool = False, ncores: int | None = None, total_bytes: int | None = None
) -> int:
    """Return the string replay's width for this cycle, the `--replay-threads` the plan passes run_m1: memory, less the reserve and gate:make-test's pytest pool, divided by `kernel_exec.REPLAY_PEAK_BYTES` (one configuration's horizon-4 walk). The pool is the one `kernel_threads_budget` subtracts, because it runs for the whole run_m1 step and the replay runs inside that step; a pass that skips the gate subtracts nothing. `kernel_exec.replay_threads_default` does the arithmetic, and `AMS_REPLAY_THREADS` overrides it, as it does for a bare run_m1.

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


def sweep_job_budget(ncores: int | None = None, total_bytes: int | None = None) -> int:
    """Return the `--jobs` width for run_m1's oracle, whose unit is a row range of one configuration's table: memory, less the reserve, divided by one range worker's peak (ORACLE_SHARD_BYTES), capped at the usable cores. `make conform-deep` (rebuild/tools/deep_sweep.py) uses it as its default too. gate:conform's belt has its own budget, `conform_job_budget`, because its worker holds different data and runs beside the surface build. Nothing is subtracted for gate:make-test's pytest pool, which can still be running when the oracle starts on a non-rehearsal pass: at this divisor that pool fits inside the reserve on every fleet machine, and reserving for it would narrow this phase on every pass for a few seconds of overlap. Nothing is subtracted for run_m1's table-only branch either, whose witness stage and shipped-order walks can still be running when the pool starts (`doc/parallelism.md` describes this overlap), because what they hold is far below the reserve. run_m1's peak memory is in the table build, whose width is --kernel-threads, and these jobs never reach it. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    return memory_budget.how_many_fit(ORACLE_SHARD_BYTES, cap=cores, total_bytes=total_bytes)


def sweep_job_derivation(ncores: int | None = None, total_bytes: int | None = None) -> str:
    """Return `sweep_job_budget`'s width as a clause for the plan line: `memory_budget.describe_fit` over the same terms."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    return memory_budget.describe_fit(ORACLE_SHARD_BYTES, cap=cores, total_bytes=total_bytes)


def _surface_fit_terms(*, skip_gates: bool, skip_make_test: bool, ncores: int | None) -> tuple[int, int, int]:
    """Return the three arguments of the surface build's width: the per-worker divisor, the co-resident bytes subtracted before the division, and the non-memory cap. `surface_job_budget` passes them to `how_many_fit` and `surface_job_derivation` to `describe_fit`, so the width and its explanation come from one derivation."""
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
    """Return the review-surface build's `--jobs` width: memory, less the reserve, less what the build's parent holds, divided by one worker's peak, capped at the usable cores and SURFACE_JOBS_CAP, and floored at one. The parent and the workers are separate constants because the parent's share is about as large as the pool's. SURFACE_PARENT_BYTES is the parent, which holds the workload table (`audit.UnitTable`) and the packed unit store (rebuild/review/unit_store.py) at any width, so it is subtracted before the division, as gate:make-test's pool is. SURFACE_WORKER_BYTES is the divisor. A worker's peak does not grow with the width, because the parent hands out one batch at a time, or with the alphabet, because the tables sit in the baseline subset pack it maps read-only (rebuild/review/subset_pack.py). The two constants' comments have the measurements. The ink-signature pool that `_resolve_signature_digests` starts before the units phase is not counted: each of its workers holds one comparator, about a tenth of a gigabyte, and the pool runs at `signature_job_budget`'s core-count width.

    A `surface-build` step peak does not measure this build's footprint. `peak_rss.reap_peak_rss_bytes` takes the largest single process in the child's tree, which here is the parent, so the step peak barely moves with the width (13.25 GB at ten jobs, 13.77 GB at two) and never includes the pool. The 2026-08-27 full-fresh pass read 17.76 GB at eight workers, while a per-term measurement of the same tree put parent and workers together at roughly twice a 34 GB machine.

    Err high on the divisor. One that is too low pushes the pool into the reserve; one that is too high only gives a large machine fewer workers than it has room for. A machine the pooled build does not fit gets a width of one, which is the serial build: there is no pool, and each fragment exists once instead of twice. What the parent holds grows per unit (one row of the workload table and one of the unit store), so shrinking SURFACE_PARENT_BYTES widens the pool on every machine. Issue #160, an on-disk workload, was closed as not needed at that size.

    Under a gated cycle gate:make-test's pytest pool runs from t=0, so it is subtracted twice: two cores from the cap, and FONT_SUITE_WORKER_BYTES for each of the `make_test_pool_width` workers the cycle passes that child, as in `kernel_threads_budget`. --skip-gates and the closure-unchanged skip of gate:make-test subtract neither. gate:js also runs from t=0, but it is one node process. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine. The cores come from `memory_budget.usable_cores()`, so an affinity mask or a cgroup quota narrows this width as it narrows the others.
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
    """Return `surface_job_budget`'s width as a clause for the plan line and the surface build's `--jobs` help: `memory_budget.describe_fit` over the same terms, so a reader can check where the width came from."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _surface_fit_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores
    )
    return memory_budget.describe_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def signature_job_budget(*, skip_gates: bool, skip_make_test: bool = False, ncores: int | None = None) -> int:
    """Return the `--signature-jobs` width the cycle passes the surface build, for the pool that shapes the ink-signature store's misses. Memory does not limit it. A signature worker is a spawn process holding one `InkComparator` over the two fonts with plain shapers and nothing else (no subset pack, units, or projections), and its resident set stays about a tenth of a gigabyte however many signatures it shapes (the `signature` pool records in `rebuild/out/cycle-timings.ndjson`). The width is `memory_budget.usable_cores()`, less gate:make-test's two cores under a gated cycle, floored at one. The two cores are subtracted as in `_surface_fit_terms`, because a pass that skips run_m1 starts this phase at t=0 beside that pool. SURFACE_JOBS_CAP does not apply, since it is where the unit worker stops scaling, so a machine where the surface build falls to one worker still shapes its signatures on every core. Below `build._SIGNATURE_POOL_THRESHOLD` misses the phase runs serially at any width, so a warm store starts no pool."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    if not (skip_gates or skip_make_test):
        cores -= 2
    return max(1, cores)


def signature_job_derivation(
    *, skip_gates: bool, skip_make_test: bool = False, ncores: int | None = None
) -> str:
    """Return `signature_job_budget`'s width as a clause for the plan line and the `--signature-jobs` help. It does not use `memory_budget.describe_fit`, whose clause would call the worker unmeasured: the width is a count of cores, and the clause says which cores are left out."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    width = signature_job_budget(skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=cores)
    if skip_gates or skip_make_test:
        return f"{width} of {cores} cores, the whole box"
    clause = f"{width} of {cores} cores, less gate:make-test's two"
    return clause if cores - 2 >= 1 else clause + ", floored at one"


def _contracts_pool_terms(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_surface: bool,
    pool_policy: str,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[int, int, int]:
    """Return the three terms `contracts_pool_width` and `contracts_pool_derivation` share: the usable cores; the surface build's process count (its parent plus `surface_job_budget`'s workers, or zero when the build does not run); and gate:make-test's pool width under the overlap policy. Under the queue policy the suite waits for gate:make-test to finish, so nothing is subtracted for that pool."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    surface = (
        0
        if skip_surface
        else 1
        + surface_job_budget(
            skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
        )
    )
    make_test = (
        make_test_pool_width(ncores=ncores)
        if pool_policy == "overlap" and not (skip_gates or skip_make_test)
        else 0
    )
    return cores, surface, make_test


def contracts_pool_width(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    skip_surface: bool = False,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the width of gate:rebuild-contracts' pytest pool under a cycle, which the cycle sets on that child as PYTEST_XDIST_AUTO_NUM_WORKERS. Memory does not limit it: no contracts worker holds a live artifact, so no constant measures one, and the width is a count of cores. The suite runs beside the surface build, so the width is the usable cores, less the build's parent and its `surface_job_budget` workers, less gate:make-test's pool under the overlap policy (`_contracts_pool_terms`), floored at one. A pass whose surface build does not run (skipped, promoted, or assets-refreshed) gives the suite every core.

    The cap keeps the process count near the core count. Two full-width pools side by side oversubscribe the cores roughly 2:1, which was measured to roughly triple the suite's wall-clock time (`_gate_conform_task`'s docstring, commit b5881022). The suite's controller is not subtracted: it idles while its workers run, and subtracting it would cost the suite a worker on every pass, so the process count exceeds the cores by that one process. The pool's memory comes out of `memory_budget`'s reserve, not out of `surface_job_budget`'s co-resident term, as `_standing_fill_terms` also assumes for this pool; subtracting it there would narrow the build on every pass for a worker no constant measures (`calibrate_budgets.UNITS`).

    On a ten-core machine beside a surface build at SURFACE_JOBS_CAP workers the width is one under either policy (ten cores less the parent and those workers), so the suite runs serially, and the plan line says so. The two cores the suite gives up go to the build's units phase, which is on the lane's critical path. The suite usually is not: under a cycle it runs only the closure-selected tests, and its one-worker `gate:rebuild-contracts` rows in `rebuild/out/cycle-timings.ndjson` end before their runs' `surface-build` rows. A pass that reruns the whole lane at one worker can outlast the build. That cost is accepted until a one-worker row ends after its run's `surface-build` row, which is the signal to revisit the floor.

    The overlap policy has no higher floor. On a twelve-core machine beside a cap-width build and gate:make-test's pool, the arithmetic gives one (twelve cores less the parent, SURFACE_JOBS_CAP workers, and that pool's two), and that one worker fills the machine. A floor of two would add a thirteenth process, and on the ten-core machine would make overlap wider than the queue policy, to shorten a step that is not on the critical path. PYTEST_XDIST_AUTO_NUM_WORKERS overrides all of this, because the child inherits this environment and will run at that width, as in `make_test_pool_width`. `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return max(1, int(stated))
    cores, surface, make_test = _contracts_pool_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    return max(1, cores - surface - make_test)


def contracts_pool_derivation(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    skip_surface: bool = False,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `contracts_pool_width`'s width as a clause for the plan's lane line. Like `signature_job_derivation`, it does not use `memory_budget.describe_fit`: the width is a count of cores, and the clause says which cores are left out. A width set through PYTEST_XDIST_AUTO_NUM_WORKERS is reported as set."""
    stated = os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS")
    if stated:
        return f"PYTEST_XDIST_AUTO_NUM_WORKERS states {stated.strip()}"
    cores, surface, make_test = _contracts_pool_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    width = contracts_pool_width(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    if surface == 0:
        box = "the box" if make_test else "the whole box"
        clause = f"{width} of {cores} cores, {box} (no surface build to share it with)"
    else:
        workers = f"{surface - 1} worker" + ("" if surface - 1 == 1 else "s")
        clause = f"{width} of {cores} cores, less the surface build's parent and its {workers}"
    if make_test:
        clause += f", less gate:make-test's {make_test} (overlap policy)"
    return clause if cores - surface - make_test >= 1 else clause + ", floored at one"


def contracts_submission_note(*, skip_surface: bool) -> str:
    """Return where in the build lane the rebuild suite is submitted, for the plan's step note and lane line: beside the surface build when one runs, and otherwise at the same point, once the run_m1 gate has passed. The second wording keeps a pass whose surface-build row reads SKIPPED from claiming the suite runs beside it."""
    if skip_surface:
        return "submitted once the run_m1 gate passes (no surface build this pass)"
    return "submitted beside the surface build"


def _conform_build_lane(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_surface: bool,
    plumbing_runs: bool,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[str, int]:
    """Return the build-lane step the belt runs beside, by plan step name, and the memory that step holds. The candidates are the surface build (its parent plus `surface_job_budget`'s workers) and the plumbing step (its chain parent plus `standing_fill_jobs`' refill pool). The one this pass runs that holds more is returned, the surface build on a tie, and `("", 0)` when the pass runs neither. Each figure comes from its own budget, so the belt's reservation matches the step's width. The belt can run beside either step: its lane is submitted when run_m1's gate passes, so it starts beside the surface build, and the plumbing step follows the build in the same lane, so a belt still running then, or one the queue policy starts late behind make-test, runs beside the plumbing step."""
    steps: list[tuple[str, int]] = []
    if not skip_surface:
        surface_jobs = surface_job_budget(
            skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
        )
        steps.append(("surface-build", SURFACE_PARENT_BYTES + SURFACE_WORKER_BYTES * surface_jobs))
    if plumbing_runs:
        fill_jobs = standing_fill_jobs(
            skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
        )
        steps.append(("plumbing", STANDING_FILL_PARENT_BYTES + STANDING_FILL_WORKER_BYTES * fill_jobs))
    return max(steps, key=lambda step: step[1], default=("", 0))


def _conform_fit_terms(
    *,
    skip_gates: bool,
    skip_make_test: bool,
    skip_surface: bool,
    plumbing_runs: bool,
    pool_policy: str,
    ncores: int | None,
    total_bytes: int | None,
) -> tuple[int, int, int]:
    """Return the three arguments of the belt's width, so `conform_job_budget` and `conform_job_derivation` use one derivation: the per-worker divisor (CONFORM_BELT_BYTES), the co-resident bytes, and the cap. The co-resident bytes are the build-lane step `_conform_build_lane` names, plus gate:make-test's pool under the overlap policy. The queue policy makes the belt wait for make-test to finish, so nothing is subtracted for that pool there. The cap is the smaller of the acceptance configuration count (one worker each) and the usable cores."""
    from rebuild.pipeline.conform import ACCEPTANCE_CONFIGS
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    _step, build_lane = _conform_build_lane(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        plumbing_runs=plumbing_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    make_test = (
        _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)
        if pool_policy == "overlap" and not (skip_gates or skip_make_test)
        else 0
    )
    return CONFORM_BELT_BYTES, build_lane + make_test, min(cores, len(ACCEPTANCE_CONFIGS))


def conform_job_budget(
    *,
    skip_gates: bool = False,
    skip_make_test: bool = False,
    skip_surface: bool = False,
    plumbing_runs: bool = False,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the `--jobs` the cycle passes gate:conform: how many belt workers, one spawn process per acceptance configuration (`run_m1.run_font_conformance`), run at once. It is memory, less the reserve, less what runs beside the belt, divided by CONFORM_BELT_BYTES, capped at the acceptance configurations and the cores, and floored at one (`_conform_fit_terms`). What runs beside it is whichever of the surface build and the plumbing step this pass runs that holds more (`_conform_build_lane`). The plumbing step's own width leaves the belt out, because this subtraction accounts for that overlap. `plumbing_runs` defaults to False because only the cycle's plan runs a plumbing step. gate:make-test's pool is subtracted under the overlap policy only, since the queue policy makes the belt wait for make-test. Two things share the machine with no memory estimate, and their bytes come out of the reserve: gate:rebuild-contracts' pool under the overlap policy, which is limited by cores and measured by no constant (`calibrate_budgets.UNITS`), and the build lane's other steps.

    CONFORM_BELT_BYTES is measured at the per-edit horizon (`conform.BELT_HORIZON`) only. A cycle run with a deeper `--conform-horizon` holds its windows in process and shares no memo, so the constant is not a measurement for it; deeper sweeps belong to `make conform-deep`. A hand `run_m1 --conform-only` uses the idle case (`skip_gates=True, skip_surface=True`, no plumbing step). A width of one runs the serial belt (`conform.run_conformance`). `ncores` and `total_bytes` are keywords so a test can compute the width for an invented machine.
    """
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _conform_fit_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        plumbing_runs=plumbing_runs,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    return memory_budget.how_many_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def conform_job_derivation(
    *,
    skip_gates: bool = False,
    skip_make_test: bool = False,
    skip_surface: bool = False,
    plumbing_runs: bool = False,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `conform_job_budget`'s width as a clause for the plan's Lane conform line and run_m1's `--jobs` help: `memory_budget.describe_fit` over the same terms, so the clause opens with the width it explains."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _conform_fit_terms(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        plumbing_runs=plumbing_runs,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    return memory_budget.describe_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def _standing_fill_terms(
    *, skip_gates: bool, skip_make_test: bool, ncores: int | None
) -> tuple[int, int, int]:
    """Return the standing fill pool's three terms for `standing_fill_jobs` and `standing_fill_derivation`: STANDING_FILL_WORKER_BYTES is the divisor, STANDING_FILL_PARENT_BYTES is subtracted first, and the cap is the cores, because no width is known past which this pool stops getting faster. Under a gated cycle gate:make-test's pool is subtracted as it is for the surface build: its bytes from memory and two cores from the cap. The plumbing step also starts beside gate:rebuild-contracts, whose pool no constant measures (`calibrate_budgets.UNITS`), so that pool is left out, and a pass that drops the standing-fill memo overuses memory for the pool's few tens of seconds. gate:conform's belt can also run beside this step. That overlap is subtracted on the belt's side (`_conform_build_lane`), so this width leaves the belt out."""
    from rebuild.tools import memory_budget

    cores = ncores or memory_budget.usable_cores()
    coresident = STANDING_FILL_PARENT_BYTES
    if not (skip_gates or skip_make_test):
        cores -= 2
        coresident += _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)
    return STANDING_FILL_WORKER_BYTES, coresident, cores


def standing_fill_jobs(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> int:
    """Return the `--standing-fill-jobs` the cycle passes the verdict chain, which forwards it to the standing fill as `--jobs`: memory, less the reserve and the chain parent, divided by one refill worker, capped at the cores (`_standing_fill_terms`). The width is computed here and nowhere else for two reasons. The fill's code is part of its memo's stamp, so arithmetic inside it would drop the memo on every edit to that arithmetic. And only the cycle knows which gates run beside the plumbing step. A served pass stays under the fill's pool threshold and starts no pool at any width, so this number costs it nothing."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _standing_fill_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores
    )
    return memory_budget.how_many_fit(per_unit, coresident_bytes=coresident, cap=cap, total_bytes=total_bytes)


def standing_fill_derivation(
    *,
    skip_gates: bool,
    skip_make_test: bool = False,
    ncores: int | None = None,
    total_bytes: int | None = None,
) -> str:
    """Return `standing_fill_jobs`' width as a clause for the plan: `memory_budget.describe_fit` over the same terms."""
    from rebuild.tools import memory_budget

    per_unit, coresident, cap = _standing_fill_terms(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores
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
    conform_horizon: int = CONFORM_HORIZON_DEFAULT,
    pool_policy: str = REBUILD_POOL_POLICY_DEFAULT,
    review_out: Path | None = None,
    ncores: int | None = None,
    total_bytes: int | None = None,
    skip_run_m1: bool = False,
    reuse_run_m1: bool = False,
    run_m1_note: str = "",
    run_m1_fingerprint: str | None = None,
    fresh: bool = False,
    skip_surface: bool = False,
    refresh_assets: bool = False,
    promote_surface: Path | None = None,
    surface_note: str = "",
    skip_contracts: bool = False,
    contracts_note: str = "",
    contracts_skip: list[str] | None = None,
    contracts_files: dict[str, str] | None = None,
    conform_note: str = "",
    conform_proven: bool = False,
    skip_plumbing: bool = False,
    plumbing_note: str = "",
    store_only: bool = False,
    record_greens: bool = False,
    keep_history: bool = False,
    recipe_serves: bool = False,
) -> Plan:
    do_carry = not no_carry and not first_run and not skip_plumbing and not store_only
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = (
            carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"
        )
    if skip_plumbing:
        plumbing_step_note = f"SKIPPED ({plumbing_note})"
    elif first_run:
        plumbing_step_note = "SKIPPED (first run)"
    elif not do_carry and not store_only:
        plumbing_step_note = "SKIPPED (--no-carry)"
    else:
        plumbing_step_note = ""
    plumbing_runs = not plumbing_step_note

    no_make_test = skip_gates or skip_make_test
    make_test_workers = make_test_pool_width(ncores=ncores)
    surface_jobs = surface_job_budget(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
    )
    workers = f"{make_test_workers} worker" + ("" if make_test_workers == 1 else "s")
    if skip_gates:
        surface_head = "--skip-gates, so the surface build takes the whole box"
    elif skip_make_test:
        surface_head = "gate:make-test skipped, so the surface build takes the whole box"
    else:
        surface_head = f"gate:make-test's pytest pool held to {workers} — its cores reserved here and its bytes off the box beside the build's own parent"
    surface_reason = f"{surface_head}; " + surface_job_derivation(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
    )
    signature_jobs = signature_job_budget(skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores)
    signature_reason = (
        "the ink-signature phase's shaping pool, cores-bound since a signature worker holds one comparator and nothing memory prices; "
        + signature_job_derivation(skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores)
    )
    fill_jobs = standing_fill_jobs(
        skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
    )
    fill_reason = "the standing fill's refill pool on a memo-drop pass, beside the chain parent; " + (
        standing_fill_derivation(
            skip_gates=skip_gates, skip_make_test=skip_make_test, ncores=ncores, total_bytes=total_bytes
        )
    )
    contracts_workers = contracts_pool_width(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    contracts_reason = contracts_pool_derivation(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    sweep_jobs = sweep_job_budget(ncores, total_bytes=total_bytes)
    sweep_reason = "the oracle's row-range workers, " + sweep_job_derivation(ncores, total_bytes=total_bytes)
    conform_jobs = conform_job_budget(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        plumbing_runs=plumbing_runs,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    belt_beside, _lane_bytes = _conform_build_lane(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        plumbing_runs=plumbing_runs,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    make_test_beside_belt = pool_policy == "overlap" and not no_make_test
    if belt_beside == "surface-build":
        surface_workers = f"{surface_jobs} worker" + ("" if surface_jobs == 1 else "s")
        conform_head = (
            f"CONFORM_BELT_BYTES a belt worker, beside the surface build's parent and its {surface_workers}"
        )
    elif belt_beside == "plumbing":
        fill_workers = f"{fill_jobs} refill worker" + ("" if fill_jobs == 1 else "s")
        conform_head = (
            "CONFORM_BELT_BYTES a belt worker, "
            + (
                "the surface build not running this pass, so "
                if skip_surface
                else "the plumbing step outweighing the surface build, so "
            )
            + f"beside the plumbing step's chain parent and its {fill_workers}"
        )
    else:
        conform_head = (
            "CONFORM_BELT_BYTES a belt worker, neither the surface build nor the plumbing step running this pass, so "
            + ("only gate:make-test's pool co-resident" if make_test_beside_belt else "nothing co-resident")
        )
    if belt_beside and make_test_beside_belt:
        conform_head += " and gate:make-test's pool"
    conform_reason = f"{conform_head}; " + conform_job_derivation(
        skip_gates=skip_gates,
        skip_make_test=skip_make_test,
        skip_surface=skip_surface,
        plumbing_runs=plumbing_runs,
        pool_policy=pool_policy,
        ncores=ncores,
        total_bytes=total_bytes,
    )
    kernel_threads = kernel_threads_budget(
        skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    replay_threads = replay_threads_budget(
        skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    replay_reason = replay_threads_derivation(
        skip_make_test=no_make_test, ncores=ncores, total_bytes=total_bytes
    )
    surface_dir = review_out if review_out is not None else REVIEW_OUT
    do_merge = (do_carry or store_only) and not no_merge and review_out is None
    do_retention = not keep_history and not first_run and review_out is None

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
        reuse_run_m1=reuse_run_m1 and not skip_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fingerprint,
        fresh=fresh,
        skip_surface=skip_surface,
        refresh_assets=refresh_assets,
        promote_surface=promote_surface,
        surface_note=surface_note,
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        contracts_skip=list(contracts_skip or []),
        contracts_files=contracts_files,
        conform_note=conform_note,
        conform_proven=conform_proven,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        plumbing_store_only=store_only,
        record_greens=record_greens,
        retention=do_retention,
        recipe_serves=recipe_serves,
        pool_policy=pool_policy,
        surface_jobs=surface_jobs,
        surface_reason=surface_reason,
        signature_jobs=signature_jobs,
        signature_reason=signature_reason,
        standing_fill_jobs=fill_jobs,
        standing_fill_reason=fill_reason,
        sweep_jobs=sweep_jobs,
        sweep_reason=sweep_reason,
        kernel_threads=kernel_threads,
        replay_threads=replay_threads,
        replay_reason=replay_reason,
        make_test_workers=make_test_workers,
        contracts_workers=contracts_workers,
        contracts_reason=contracts_reason,
        conform_jobs=conform_jobs,
        conform_reason=conform_reason,
        conform_horizon=conform_horizon,
        review_out=review_out,
        surface_dir=surface_dir,
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
    elif plan.reuse_run_m1:
        reuse_argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--gates-only"]
        if sweep_jobs > 1:
            reuse_argv += ["--jobs", str(sweep_jobs)]
        if fresh:
            reuse_argv += ["--fresh-oracle-cache"]
        plan.steps.append(
            Step(
                "run_m1",
                reuse_argv,
                run_m1_note,
                lane="build",
                describe=step_description(RUN_M1_REUSE_STEP),
            )
        )
    else:
        run_m1_argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1"]
        if sweep_jobs > 1:
            run_m1_argv += ["--jobs", str(sweep_jobs)]
        run_m1_argv += ["--kernel-threads", str(kernel_threads), "--replay-threads", str(replay_threads)]
        if fresh:
            run_m1_argv += ["--fresh-oracle-cache"]
        plan.steps.append(Step("run_m1", run_m1_argv, run_m1_note, lane="build"))

    if skip_surface:
        if promote_surface is not None:
            plan.steps.append(
                Step(
                    "surface-promote",
                    None,
                    f"moves {promote_surface} into rebuild/out/review and deletes the surface it replaces; the stores inside it arrive warm",
                    lane="build",
                )
            )
        plan.steps.append(
            Step("surface-build", None, f"SKIPPED ({surface_note})", lane="build", skipped=True)
        )
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
        surface_argv = [
            "uv",
            "run",
            "python",
            "-m",
            "rebuild.review.build",
            "--jobs",
            str(surface_jobs),
            "--signature-jobs",
            str(signature_jobs),
        ]
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

    if plumbing_step_note:
        plan.steps.append(Step("plumbing", None, plumbing_step_note, lane="build", skipped=True))
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
            plumbing_argv += ["--verdicts", str(verdicts), "--carry-out", str(resolved_carry_out)]
        else:
            plumbing_argv += ["--merge-master", str(verdicts)]
        if not do_merge:
            plumbing_argv += ["--no-merge"]
        if plan.complaints_note:
            plumbing_argv += ["--no-complaints"]
        if fresh:
            plumbing_argv += ["--fresh-standing-memo"]
        plumbing_argv += ["--standing-fill-jobs", str(fill_jobs)]
        if do_carry and not do_merge:
            note = (
                "carry only (rehearsal: the live autosave is never written)"
                if review_out is not None
                else "carry only (--no-merge)"
            )
        elif store_only:
            note = (
                "the surface did not move, so the carry is the identity — merging the master straight in, "
                "then the fills and the docket"
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
                skipped=True,
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
                "then names what moved in the invariant block against the index copy, diffing that block alone — the pins are the last accepted census; commit them to accept this one",
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
                Step("gate:conform", conform_gate_argv(conform_jobs, conform_horizon), lane="conform")
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
                    rebuild_lane_argv("contracts"),
                    contracts_submission_note(skip_surface=skip_surface)
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
            "the checked-in per-unit peaks against what this box measured, once the gates have joined and this pass's own pool records are in the journal — a file read; committing a re-seeded constant is the acceptance, exactly as the census pins work",
        )
    )

    if do_retention:
        plan.steps.append(
            Step(
                "retention",
                None,
                f"on green finish: keep only the stamp-aligned verdicts-carried-*.json, drop verdicts-autosave-* stashes older than the journal's last base event, compact the journal to a {RETENTION_WINDOW_DAYS}-day restore floor; --keep-history skips",
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
                "SKIPPED (rehearsal: the live piles are not this cycle's to prune)",
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
    """Return the line that names the master the carry resolved to. A master stamped for an older surface than the served one is named as older and carried anyway: a verdict names its unit by content id, so it reaches the unit with that id on the live surface or none, never the wrong window. On a promoting pass the aligned master is stamped for the surface the pass is about to replace, because the resolution reads the live manifest before the move, and the line says so."""
    try:
        shown = resolved["path"].relative_to(root)
    except ValueError:
        shown = resolved["path"]
    if resolved["aligned"]:
        stamped = (
            "stamped for the surface this pass replaces; its verdicts land by unit id"
            if promoting
            else "stamped for the served surface"
        )
    else:
        replaced = "the one this pass replaces" if promoting else "the served one"
        stamped = (
            f"stamped {resolved['stamp']}, an older surface than {replaced}; its verdicts land by unit id"
        )
    return f"Auto-resolved carry source: {shown} ({resolved['count']} effective verdicts, {stamped}). Pass --verdicts to override."


def master_stamped_for_surface(master: Path, surface: Path) -> bool:
    """Return whether the verdicts master carries the surface's own stamp, read as merge_verdicts reads both: the master parsed whole as an ams-review-verdicts/1 document, and the stamp from the manifest's `generated_at`. This is the store-only route's precondition, because that route passes the master to the merge unchanged and the merge refuses any input stamped for another surface. `main` calls it for a master named by --verdicts. For an auto-resolved master the resolution's `aligned` already holds the answer, computed by `status.resolve_carry_source` from the same parse and stamp, so that master is not parsed twice. If a pass stops after the surface build writes a new surface and before the plumbing carries the store onto it, the store stays stamped for the previous surface and the next pass skips the build as unchanged. This returns False for that master, so the pass takes the full carry by unit id. An unreadable master also returns False, and the carry reports it."""
    from rebuild.review.serve import parse_autosave_payload

    stamp = _manifest_stamp_at(surface)
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


def server_may_stay_up(*, skip_surface: bool, writes_store: bool, promotes_surface: bool = False) -> bool:
    """Return whether a live review server can keep running through this pass. The app owns two things a cycle writes: the surface's units and stamp (livereload watches every shard, and a restamped manifest orphans the tab's store) and the verdict store (merge_verdicts refuses to write it under a live server, because an open tab would write its copy back over the merge). So the answer depends on the plan's writes, not on a skip flag. A pass that rewrites no units and merges nothing into the store (a --no-carry pass, a --no-merge carry over an unchanged surface, a pass with no artifact work) writes neither, so the review server keeps running and the open tab keeps working for the whole run. An assets refresh is such a pass: it rewrites no shard and leaves `generated_at` unchanged, so the tab's store stays aligned, and livereload reloads the tab onto the new app files. A surface promotion sets the same skip flag but replaces every shard and the stamp in one rename, so `promotes_surface` requires the port even when the store is untouched. Everything else the cycle writes is outside the served tree (the census pins, the m1 summaries, the carried file) or is read by the app only as status, which is meant to update during a pass."""
    return skip_surface and not promotes_surface and not writes_store


def stop_review_server(timeout: float = SERVER_STOP_TIMEOUT) -> bool:
    """Stop the review server and wait for port 7294 to come free, so the surface rewrite that follows cannot race a live reader. Returns False when something is still listening at the deadline (a server started another way, or one stuck in shutdown); the caller then reports it and does not build."""
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
            f"    Lane build only; no gates; run_m1 sweeps --jobs {plan.sweep_jobs} ({plan.sweep_reason}) at --kernel-threads {'not passed (gates-only route)' if plan.reuse_run_m1 else plan.kernel_threads} and --replay-threads {'not passed (gates-only route)' if plan.reuse_run_m1 else plan.replay_threads}, surface-build --jobs {plan.surface_jobs} ({plan.surface_reason}), surface-build --signature-jobs {plan.signature_jobs} ({plan.signature_reason}), plumbing --standing-fill-jobs {plan.standing_fill_jobs} ({plan.standing_fill_reason})",
        ]
    t0_lane = "gate:js" if plan.skip_make_test else "gate:js, gate:make-test"
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        f"    Lane t0   [from t=0, background]  : {t0_lane}",
        "    Lane build[serial, main thread]  : run_m1 -> submit gate:rebuild-contracts -> surface-build -> plumbing -> census",
    ]
    if plan.skip_conform:
        lines.append(
            f"    Lane conform                     : SKIPPED ({plan.conform_note or '--skip-conform'})"
        )
    elif plan.pool_policy == "overlap":
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; CO-RESIDENT with the pytest pools (--jobs {plan.conform_jobs}; {plan.conform_reason})"
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
            f"    Lane rebuild-contracts           : {contracts_submission_note(skip_surface=plan.skip_surface)}, -n {plan.contracts_workers} ({plan.contracts_reason});"
        )
        if plan.pool_policy == "overlap":
            lines.append(
                "                                       CO-RESIDENT with the other pools (overlap policy)"
            )
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
    workers = f"{plan.make_test_workers} worker" + ("" if plan.make_test_workers == 1 else "s")
    if plan.skip_make_test:
        kernel_reason = "the table build's memory ceiling, the one width RAM binds"
    else:
        kernel_reason = f"the table build's memory ceiling, less gate:make-test's {workers}"
    kernel_reason += ", capped at the configuration count and the cores"
    lines.append(f"    run_m1 sweeps --jobs             : {plan.sweep_jobs}  ({plan.sweep_reason})")
    if plan.reuse_run_m1:
        lines.append(
            "    run_m1 --kernel-threads          : not passed (the gates-only route enumerates nothing, so there is no fan-out to size)"
        )
    else:
        lines.append(f"    run_m1 --kernel-threads          : {plan.kernel_threads}  ({kernel_reason})")
    if plan.reuse_run_m1:
        lines.append(
            "    run_m1 --replay-threads          : not passed (the gates-only route replays nothing, so there is no wave to size)"
        )
    else:
        lines.append(
            f"    run_m1 --replay-threads          : {plan.replay_threads}  (the string replay's own ceiling, {plan.replay_reason})"
        )
    lines.append(f"    surface-build --jobs             : {plan.surface_jobs}  ({plan.surface_reason})")
    lines.append(f"    surface-build --signature-jobs   : {plan.signature_jobs}  ({plan.signature_reason})")
    lines.append(
        f"    plumbing --standing-fill-jobs    : {plan.standing_fill_jobs}  ({plan.standing_fill_reason})"
    )
    return lines


def plan_rows(plan: Plan) -> list[console.PlanRow]:
    """Return the plan's steps as the digest's rows. Only gate:conform can show `run?`, which makes the counts line a range: its skip key covers the artifacts run_m1 writes, so a pass that plans the sweep may still skip it once the build finishes. That row's note comes from `UNDECIDED_UNTIL_RUN_M1` and states the condition.

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
    """Return the plan block, the lines the digest prints before step 1 and writes to plan.txt: the commit, the log directory, the step counts and each step's command, then the paths this pass resolved and the concurrency block."""
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
            f"  rehearsal    : surface writes redirected to {plan.review_out}; the live surface at rebuild/out/review is never written."
        )
    lines.extend(_render_concurrency(plan))
    return lines


@dataclass
class CycleReport:
    """The pass's running record. The `*_status` strings are prose for the summary. The gate strings (`gate_js` and the others) are prose too, but `_step_outcome` reads their leading words (`not run`, `skipped`, `self-skipped`, `green`) to fill the table's outcome column. Decisions read the booleans (`gate_*_green`, `complaints_ok`), which are set when each outcome is judged. A gate that was never joined, because it was skipped or never submitted, leaves its boolean None, which is neither green nor red.

    `step_seconds` and `step_returncodes` fill the summary table's last two columns. They come from the same `_StepResult` the timings journal records, so the table and the journal agree, and they are keyed through STEP_ALIASES, so the gates-only child fills the run_m1 row. `run_m1_failed` records the cycle's own judgment of the run_m1 gate from the three summary JSONs, which `_step_outcome` reads before the return code.
    """

    unmatched: int | None = None
    multi_matched: int | None = None
    pins_pass: bool | None = None
    surface_units: int | None = None
    surface_rows: int | None = None
    surface_batches: int | None = None
    echo_groups: int | None = None
    assets_status: str = "not run"
    promote_status: str = "not run"
    carry_out: Path | None = None
    carry_lines: list[str] = field(default_factory=list)
    carry_figures: dict[str, int] | None = None
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
    census_reach: str = "not run"
    census_reach_sets: dict | None = None
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
    contracts_recordable: bool = False
    conform_proven: bool = False
    interrupted: bool = False
    run_m1_failed: bool = False
    retention_figure: str = ""
    step_seconds: dict[str, float] = field(default_factory=dict)
    step_returncodes: dict[str, int] = field(default_factory=dict)


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text())


_Emitter = console.Digest


class _ChildRegistry:
    """Thread-safe set of live subprocesses, so a Ctrl-C or any signal `stop_signals` catches can terminate and reap every child."""

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
        """Track a live child. Return False once terminate_all has run, so a thread that unblocks after a stop (a queue-policy gate task waiting on an earlier gate's future) never leaves a new subprocess untracked. The caller terminates that child instead."""
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


STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class CycleStopped(KeyboardInterrupt):
    """A stop signal the pass caught, raised in the main thread wherever it was waiting, so `_run_cycle` handles it as it handles a Ctrl-C: terminate and reap every child, then write the interrupted summary naming the signal. It subclasses KeyboardInterrupt so that no `except Exception` catches it."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@contextlib.contextmanager
def stop_signals() -> Iterator[None]:
    """Turn SIGINT, SIGTERM and SIGHUP into `CycleStopped` for the length of a pass. Every child stays in the cycle's process group, so a signal sent to the group reaches them directly. The handler is for a signal that reaches only the driver, such as `kill` on `make`, which passes SIGTERM to its recipe, whose `uv run` passes it on. Without the handler the driver would exit and leave its children running.

    Only the first signal raises and later ones are ignored, because a group signal arrives twice (once directly and once forwarded by `uv run`) and the second must not interrupt the cleanup the first started. A signal that was ignored when the pass started stays ignored, so a pass under `nohup` outlives its shell. Only the main thread can install handlers, so on any other thread this installs none. The previous handlers are restored on exit.
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


def _terminate_child(proc: subprocess.Popen) -> None:
    """Terminate one child (SIGTERM, 3 s grace, then SIGKILL) and close its pipes. This handles the race where the registry is torn down between a `Popen` and its `registry.add`."""
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
    emit: console.Digest,
    registry: _ChildRegistry,
    stream: bool,
    env: dict[str, str] | None = None,
) -> _StepResult:
    """Run one child to completion, passing every line of both pipes to the digest, which logs each line and shows the ones that matter. This opens the step's banner but does not close it: the caller closes it with `_close_step` once it has read the step's headline figure from the files the child wrote. `env`, when given, is overlaid on this process's environment for this child only.

    `stream` also sends the child's unparsed lines to the terminal. Only the job-costs diff uses it, because it is the one child output a person must read to act on. Other child output reaches the terminal only as digest events, and every line reaches the log, so a failed step's full output is replayed under its banner.

    When the registry has been torn down, a stop signal has already arrived, so the child is not started (or is terminated at once), no banner opens, and the result's return code is 130. The child stays in the cycle's process group, so a signal sent to that group reaches it and everything it spawns.
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
    )
    if not registry.add(proc):
        _terminate_child(proc)
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


def classify_rebuild_output(stdout: str, returncode: int, check: str) -> CheckVerdict:
    """Return the rebuild suite's gate verdict from pytest's FAILED and ERROR summary lines. The cycle's rebuild gate and `rebuild.tools.rebuild_gate` both use it. `check` ("rebuild-contracts") is only copied into the verdict to name the suite. ANSI escape codes are stripped first, because pytest colors its output whenever FORCE_COLOR is set (the agent harness sets it), and a colored line does not start with "FAILED " or "ERROR ". A nonzero exit with no parsed failure lines is red. Every failure counts as unexplained, because the suite has no list of accepted failures, and every green verdict is recordable."""
    lines = [_ANSI_SGR.sub("", line) for line in stdout.splitlines()]
    failed_ids = [line.split(None, 2)[1] for line in lines if line.startswith("FAILED ")]
    error_ids = [line.split(None, 2)[1] for line in lines if line.startswith("ERROR ")]
    hard = failed_ids + error_ids
    if returncode != 0 and not hard:
        hard.append(f"pytest exited {returncode} with no parsed FAILED/ERROR lines")
    return CheckVerdict(
        check=check,
        verdict="red" if hard else "green",
        status=f"FAILED ({len(hard)} unexplained)" if hard else "green",
        failures=[f"rebuild suite: {len(hard)} unexplained failure(s)"] if hard else [],
        failed_ids=hard,
        recordable=not hard,
    )


# The failure reason for a run_m1 that wrote no summaries. The cycle's failure list and the timings journal's check line both use it, so the two record the same text.
_NO_SUMMARIES_REASONS = ("run_m1 did not write all three summary files",)

RUN_M1_REUSE_STEP = "run_m1:gates-only"

STEP_ALIASES = {RUN_M1_REUSE_STEP: "run_m1"}


def _do_run_m1(
    report: CycleReport,
    *,
    spawn,
    emit: console.Digest,
    registry: _ChildRegistry,
    argv: list[str] | None = None,
    skip: bool = False,
    skip_note: str = "",
    reuse: bool = False,
    record: bool = False,
    fingerprint: str | None = None,
    timings: CycleTimings | None = None,
) -> CheckVerdict | None:
    """Run the M1 build, or reuse it when `skip` is set, and judge its gate from the three summary JSONs. The skip path leaves rebuild/out/m1 untouched and re-evaluates the summaries on disk, which is sound because run_m1's outputs are deterministic and carry no timestamps. A live green is recorded only if the fingerprint still matches after the run, because an input edited mid-run means the tested content is no longer on disk. A live red whose fingerprint matches the green record deletes the record.

    With `reuse`, the child is `run_m1 --gates-only` over the tables and font on disk. It rewrites the defect fields of `pipeline_summary.json` in place and exits with an error when that file is missing, so it is the one summary not deleted before the spawn. Everything after the spawn follows the full build's path, green recording included. That green rests on the conditions the route was planned on (`gates_only_reuse` and `m1_tables_stamped`), which the child checks again before recording its own green. The child spawns as `RUN_M1_REUSE_STEP`, not `run_m1`, because `make cycle-timings ARGS='--by-step'` groups rows by step name and host, and a gates-only run of a few seconds recorded as `run_m1` would distort the figures for a full M1 build.

    Every path, the skip included, records a check line in the timings journal, because a skip is a judgment on this build's summaries. The child records none of its own, because it inherits CYCLE_RUN_ENV. A build that wrote no summaries is recorded red with `_NO_SUMMARIES_REASONS`, the reason the cycle's failure list also gets.
    """
    step = RUN_M1_REUSE_STEP if reuse else "run_m1"
    result: _StepResult | None = None
    if skip:
        emit.step_skipped("run_m1", f"{skip_note}; evaluating the gate from the recorded summaries")
    else:
        for name, path in cycle_paths.M1_SUMMARY_FILES.items():
            if reuse and name == "pipeline":
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
        if timings is not None:
            timings.record_check(
                CheckVerdict(
                    check="run_m1",
                    verdict="red",
                    status="FAILED (no summaries)",
                    failures=list(_NO_SUMMARIES_REASONS),
                    failed_ids=[],
                )
            )
        return None
    summaries = {name: _load_summary(path) for name, path in cycle_paths.M1_SUMMARY_FILES.items()}
    gate = evaluate_run_m1_gate(summaries["pipeline"], summaries["manual_pins"], summaries["oracle"])
    if timings is not None:
        timings.record_check(gate)
    report.unmatched = summaries["oracle"].get("unmatched")
    report.multi_matched = summaries["oracle"].get("multi_matched")
    report.pins_pass = bool(summaries["manual_pins"].get("pass"))
    if result is not None:
        _close_step(emit, report, step, result, "ok" if gate.ok else "FAILED")
    if record and fingerprint is not None:
        if not gate.ok:
            clear_contradicted_green(cycle_paths.RUN_M1_GREEN, fingerprint)
        elif not skip:
            if run_m1_skip_fingerprint(ROOT) == fingerprint:
                record_green(cycle_paths.RUN_M1_GREEN, fingerprint, files=run_m1_skip_files(ROOT))
            else:
                emit.note("run_m1", "run_m1 green, but its inputs changed while it ran — green not recorded")
    return gate


def _run_m1_reasons(gate: CheckVerdict | None) -> list[str]:
    if gate is None:
        return list(_NO_SUMMARIES_REASONS)
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


def _do_assets_refresh(
    report: CycleReport, *, spawn, emit: console.Digest, registry: _ChildRegistry, plan: Plan
) -> bool:
    """Copy the review app's static files over the served surface and restamp the manifest's `static` component, on a pass where that component is the only input that changed. It runs in place of the surface build, and later steps treat the pass as a surface skip: no unit, shard, sidecar or `generated_at` changes, so the carry is the identity and the review server keeps running. Livereload sees the copied files and reloads the open tab."""
    result = spawn("assets-refresh", plan.argv("assets-refresh"), emit=emit, registry=registry, stream=False)
    if result.returncode != 0:
        emit.note("assets-refresh", f"ERROR: review.build refresh-assets exited {result.returncode}.")
        report.assets_status = f"FAILED (exit {result.returncode})"
        _close_step(emit, report, "assets-refresh", result)
        return False
    report.assets_status = "refreshed in place (units, sidecars and generated_at unmoved)"
    _close_step(emit, report, "assets-refresh", result)
    return True


def _do_promote_surface(report: CycleReport, *, emit: console.Digest, plan: Plan) -> bool:
    """Move a rehearsal's surface into place, in this process, on a pass whose plan found one that reproduces these inputs byte for byte. It runs in place of the surface build, which then reports itself skipped over the promoted manifest. The move replaces every shard and the manifest stamp, so a promoting pass is never one the review server may stay up through."""
    assert plan.promote_surface is not None
    emit.step_start("surface-promote", None, plan.describe("surface-promote"))
    started = time.perf_counter()
    try:
        promote_surface(plan.promote_surface, REVIEW_OUT)
    except Exception as exc:
        report.step_seconds["surface-promote"] = time.perf_counter() - started
        report.step_returncodes["surface-promote"] = 1
        report.promote_status = f"FAILED ({exc!r})"
        emit.note("surface-promote", f"ERROR: could not move {plan.promote_surface} into place: {exc!r}")
        emit.step_end("surface-promote", None, "FAILED", "")
        return False
    report.step_seconds["surface-promote"] = time.perf_counter() - started
    report.promote_status = (
        f"moved {plan.promote_surface} into place (stores warm, generated_at the rehearsal's)"
    )
    emit.step_end("surface-promote", None, "ok", report.promote_status)
    return True


def _do_surface_build(
    report: CycleReport,
    *,
    spawn,
    emit: console.Digest,
    registry: _ChildRegistry,
    review_out: Path | None,
    argv: list[str] | None = None,
    skip: bool = False,
    skip_note: str = "",
) -> bool:
    """Rebuild the review surface, or reuse it when `skip` is set. Both paths read the four totals from the surface's manifest.json, whose unit and row totals the build checks against the shards it wrote, so the summary reports what the surface on disk contains."""
    surface_dir = review_out if review_out is not None else REVIEW_OUT
    if skip:
        if not _read_surface_totals(report, surface_dir):
            emit.note(
                "surface-build",
                "ERROR: surface-build skip: the manifest vanished mid-cycle; rerun with --fresh.",
            )
            return False
        emit.step_skipped("surface-build", skip_note)
        return True
    result = spawn("surface-build", argv, emit=emit, registry=registry, stream=False)
    if result.returncode != 0:
        emit.note("surface-build", f"ERROR: review.build exited {result.returncode}.")
        _close_step(emit, report, "surface-build", result)
        return False
    if not _read_surface_totals(report, surface_dir):
        emit.note("surface-build", "ERROR: review.build exited 0 but left no readable manifest.json.")
        _close_step(emit, report, "surface-build", result, "FAILED (no manifest)")
        return False
    _close_step(emit, report, "surface-build", result)
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
    """Split the verdict chain's output into sections at the `[phase] <step>` line each step starts with, so the summary can report each of the chain's steps although one subprocess runs them all. The chain's `[chain] fixpoint:` and `[chain] failed:` result lines close the open section without opening one, which keeps a `failed:` line out of the complaints section. Later echo rounds (`echo-fill-2` and so on) are merged into the first round's section."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith(console.FIXPOINT_LINE) or line.startswith(console.FAILED_LINE):
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
    """Return whether the summary keeps this line of the standing fill's output. It keeps the `wrote` line, the tripwire's WARNING (so an over-broad rule shows in cycle_summary.json), every per-rule line (so a newly added rule shows even at 0 filled), and the composed-pair lines that filled or held something. Composed-pair lines grow quadratically with the rule count, so the rest are left to `standing_probe --coverage`. The REACHED NOTHING lines are left out because `--require-reach` fails the step on such a rule, and the except_left vocabulary line is informational. The already-verdicted column is optional because the chain runs the fill with `--open-only`, which omits it, while a dry run over the whole domain prints it."""
    if line.startswith("wrote ") and "standing-approval verdicts" in line:
        return True
    if line.startswith("WARNING:"):
        return True
    if not line.endswith("held for review by except_left"):
        return False
    head, _, tail = line.partition(": ")
    if " + " not in head:
        return True
    match = re.match(r"(\d+) filled, (?:\d+ already verdicted, )?(\d+) held", tail)
    return match is not None and (int(match.group(1)) > 0 or int(match.group(2)) > 0)


def _do_plumbing(
    report: CycleReport, *, spawn, emit: console.Digest, registry: _ChildRegistry, plan: Plan
) -> list[str]:
    """Run the verdict chain as one child and fill the per-step report from its output. Return the failure messages for the cycle's failure list, one per failed step."""
    result = spawn("plumbing", plan.argv("plumbing"), emit=emit, registry=registry, stream=False)
    report.carry_out = plan.carry_out if plan.carry_out is not None else frontier_carry_out()
    sections = plumbing_sections(result.stdout)
    failed = ""
    for line in result.stdout.splitlines():
        if line.startswith(console.FAILED_LINE):
            failed = line[len(console.FAILED_LINE) :].split(" ", 1)[0]
            failed = re.sub(r"-\d+$", "", failed)
    report.plumbing_fixpoint = any(
        line.startswith(console.FIXPOINT_LINE + "witnessed") for line in result.stdout.splitlines()
    )

    report.carry_lines = _scrape(
        sections.get("carry", []),
        lambda line: any(word in line for word in ("carried", "kinds", "queue", "fallback", "figures")),
    )
    report.carry_figures = carry_figures(report.carry_lines)
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
    report.standing_fill_lines = _scrape(sections.get("standing-fill", []), _standing_fill_news)

    # Steps after the failed one never ran. Steps before it ran and report what they did.
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
    _close_step(emit, report, "plumbing", result)
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


def accepted_census() -> dict | None:
    """Return the last accepted census: the git index copy of the pins, which the working tree's rewrite is compared with at commit time. Return None when the file is not in the index or git is unavailable."""
    try:
        shown = subprocess.run(
            ["git", "show", f":{CENSUS_PINS.relative_to(ROOT).as_posix()}"],
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


def _do_census(
    report: CycleReport, *, spawn, emit: console.Digest, registry: _ChildRegistry, plan: Plan
) -> None:
    """Rewrite the census pins from the surface's census-facts.json sidecar and report what changed against the last accepted census (`accepted_census`). When only the volatile block changed, the status says the invariant is unchanged. When the invariant block changed, the status lists the changes (`census.invariant_delta`) and the invariant block's diff is printed under the banner without the volatile hunks. The volatile block changes with nearly every letter, so a full diff on every pass would teach a reader to ignore it.

    The step also reports reach: the ledger's `ink_identical` and `no_verdict` declarations compared with the classes the corpus reached and machine-approved. Neither the ledger nor the pins shows on its own that a declared class went unreached or that an undeclared class started approving units.

    The step records no green and never fails the cycle. A failed refresh (for example over a surface built before the sidecar existed) is reported and left for the next pass that rebuilds the surface.
    """
    refresh = spawn("census", plan.argv("census"), emit=emit, registry=registry, stream=False)
    if refresh.returncode != 0:
        report.census_status = f"update FAILED (exit {refresh.returncode}) — informational"
        report.census_reach = "not computed (the refresh failed)"
        _close_step(emit, report, "census", refresh, "ok")
        return
    current = json.loads(CENSUS_PINS.read_text(encoding="utf-8"))
    reached = census.reach(load_ledger(DIVERGENCE_LEDGER), current["invariant"])
    report.census_reach = reached.describe()
    report.census_reach_sets = reached.as_json()
    accepted = accepted_census()
    if accepted is None:
        report.census_status = (
            "updated (no accepted census to compare against: the pins are not in the index)"
        )
        _close_step(emit, report, "census", refresh, "ok")
        return
    findings = census.invariant_delta(accepted.get("invariant", {}), current["invariant"])
    if findings:
        emit.substep(SUBSTEP_PARENTS["invariant-diff"], "invariant-diff")
        for line in census.invariant_diff(accepted.get("invariant", {}), current["invariant"]):
            emit.child_line("invariant-diff", console.STDOUT, line)
            emit.emit(line)
        emit.substep_end("invariant-diff")
        report.census_status = (
            f"invariant moved: {'; '.join(findings)} — its diff is shown above; review it at commit time"
        )
    elif accepted.get("volatile") != current.get("volatile"):
        report.census_status = (
            "invariant unchanged (only the volatile totals moved; cycle_summary.json carries the surface's)"
        )
    else:
        report.census_status = "updated (matches the last accepted census)"
    _close_step(emit, report, "census", refresh, "ok")


def _do_job_costs(
    report: CycleReport, *, spawn, emit: console.Digest, registry: _ChildRegistry, plan: Plan
) -> None:
    """Compare the checked-in per-unit memory peaks with what this machine has measured (`calibrate_budgets --check`). Several pool widths are the machine's memory divided by one of these constants, so a stale constant makes a pool the wrong width. The step runs after the gates join because it reads the timings journal, which this pass's pools have just appended to.

    It never fails the pass: a wrong width costs wall-clock time or swap but cannot make an artifact wrong. The summary line and `job_costs_ok` report an overrun, and committing the re-seeded constant accepts it, as committing the census pins accepts a census. A check that cannot run is reported as informational too.

    On an overrun the step prints the working tree's `git diff` of the files that hold the constants, which shows whether a constant has already been re-seeded. The diff is printed only then because those files hold much else, and a diff printed on every pass would teach a reader to ignore it.
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
        stream=True,
    )
    status = (
        "OVERRUN (a measured peak outruns its checked-in constant — see above; re-seed the constant and "
        "commit it, and that commit is the acceptance)"
    )
    if diff.stdout.strip():
        status += " — a constant has already moved in the working tree"
    report.job_costs_status = status
    report.job_costs_ok = False
    _close_step(emit, report, "job-costs", check, "ok")


def _skip_plumbing(report: CycleReport, plan: Plan, emit: console.Digest) -> None:
    """Report the plumbing step as skipped, marking every chain step skipped. The carried file the last recorded pass wrote is still the stamp-aligned frontier, because the surface it was carried onto has not changed, so the report still names it."""
    emit.step_skipped("plumbing", plan.plumbing_note)
    note = f"skipped ({plan.plumbing_note})"
    report.carry_out = frontier_carry_out()
    report.merge_status = note
    report.echo_fill_status = note
    report.echo_merge_status = note
    report.standing_fill_status = note
    report.standing_merge_status = note


def _gate_js_task(argv: list[str], spawn, emit: console.Digest, registry: _ChildRegistry) -> _StepResult:
    result = spawn("gate:js", argv, emit=emit, registry=registry, stream=False)
    _close_gate(emit, "gate:js", result)
    return result


MAKE_TEST_SELF_SKIP = "make test: SKIPPED —"
MAKE_TEST_SELF_SKIP_STATUS = "self-skipped (input closure unchanged since its last green run)"


def make_test_self_skipped(stdout: str) -> bool:
    """Return whether the font suite's wrapper skipped itself because its input closure was unchanged. The wrapper exits zero either way, so without this check the cycle would report that the suite ran on a pass that tested nothing there."""
    return any(line.startswith(MAKE_TEST_SELF_SKIP) for line in stdout.splitlines())


def _gate_make_test_task(
    argv: list[str], spawn, emit: console.Digest, registry: _ChildRegistry
) -> _StepResult:
    result = spawn("gate:make-test", argv, emit=emit, registry=registry, stream=False)
    if result.returncode == 0 and make_test_self_skipped(result.stdout):
        emit.step_end("gate:make-test", result, "ok", MAKE_TEST_SELF_SKIP_STATUS)
    else:
        _close_gate(emit, "gate:make-test", result)
    return result


def _spawn_with_env(spawn, env: dict[str, str]):
    """Return a spawn callable that overlays `env` on one child's environment. Setting the variables in os.environ would pass them to every child this process spawns (run_m1, the surface build, the rebuild suite), so one gate's pytest width would also apply to the others' `-n auto` pools. The wrapper changes only the environment, never the argv, so the plan stays the only source of each child's command."""

    def spawn_with_env(name, argv, *, emit, registry, stream):
        return spawn(name, argv, emit=emit, registry=registry, stream=stream, env=env)

    return spawn_with_env


def _gate_conform_task(
    pool_policy: str,
    make_fut: Future | None,
    spawn,
    emit: console.Digest,
    registry: _ChildRegistry,
    argv: list[str],
) -> CheckVerdict:
    """Run gate:conform, the exhaustive font-versus-settlement sweep over the fresh M1.otf (`run_m1 --conform-only`), and return its verdict. Under the queue policy it waits for gate:make-test, and the rebuild suite waits for it, so only one heavy pool runs at a time. Run at the same time, the sweep and the rebuild suite oversubscribed the cores roughly 2:1, and that contention was measured to roughly triple the suite's wall-clock time (commit b5881022), which is worse than running them in sequence. The previous conform_summary.json is deleted just before the sweep starts, so the verdict can only come from this pass's sweep. A skipped gate never runs this task and leaves the file alone."""
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
    verdict = evaluate_conform_gate(summary)
    if result.returncode != 0 and not verdict.failures:
        # A sweep whose summary passed but whose process exited nonzero stopped somewhere the summary does not describe, so the exit code overrides the summary's verdict.
        verdict = CheckVerdict(
            check="conform",
            verdict="red",
            status=f"FAILED (exit {result.returncode})",
            failures=[f"conform gate: exited {result.returncode} despite a passing summary"],
            failed_ids=[],
        )
    _close_gate(emit, "gate:conform", result, verdict)
    return verdict


def _await_gate_futures(*futures: Future | None) -> None:
    """Wait until each given gate has finished, however it finished. A gate that raised is reported when `_join_gates` collects it, and the waiting gate still runs."""
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
    emit: console.Digest,
    registry: _ChildRegistry,
    argv: list[str],
) -> CheckVerdict:
    """Run gate:rebuild-contracts, the rebuild suite (every test under rebuild/, none of which reads a live build artifact), and return its verdict. Because it reads no build output, it is submitted right after gate:conform, once the run_m1 gate has passed, and runs beside the surface build at the width `contracts_pool_width` sets on its environment: the cores the build's parent and workers leave free. Under the queue policy it also waits for gate:make-test and gate:conform, so only one heavy gate pool runs at a time."""
    if pool_policy == "queue":
        _await_gate_futures(conform_fut, make_fut)
    result = spawn("gate:rebuild-contracts", argv, emit=emit, registry=registry, stream=False)
    verdict = classify_rebuild_output(result.stdout, result.returncode, "rebuild-contracts")
    _close_gate(emit, "gate:rebuild-contracts", result, verdict)
    return verdict


def _gate_result(fut: Future, name: str, failures: list[str]):
    try:
        return fut.result()
    except Exception as exc:
        failures.append(f"{name} raised: {exc!r}")
        return None


def _rc_verdict(check: str, returncode: int, failure: str) -> CheckVerdict:
    """Return the verdict for a gate judged by its exit code alone, with the summary's status strings. It names no failed ids because neither suite's output is parsed."""
    return CheckVerdict(
        check=check,
        verdict="green" if returncode == 0 else "red",
        status="green" if returncode == 0 else f"FAILED (exit {returncode})",
        failures=[] if returncode == 0 else [failure],
        failed_ids=[],
    )


def _join_rebuild_lane(
    report: CycleReport,
    failures: list[str],
    fut: Future,
    lane: str,
    emit: console.Digest,
    timings: CycleTimings | None = None,
) -> None:
    """Record one rebuild lane's outcome in the report and its verdict in the timings journal. A task that raised records no check line, because the exception is a failure of the thread pool, not a verdict from the suite; the report shows "FAILED (exception)"."""
    verdict = _gate_result(fut, f"gate:rebuild-{lane}", failures)
    if verdict is None:
        status, green, recordable = "FAILED (exception)", False, False
    else:
        status, green, recordable = verdict.status, not verdict.failures, verdict.recordable
        for test_id in verdict.failed_ids:
            emit.note(f"gate:rebuild-{lane}", f"hard rebuild failure ({lane}): {test_id}")
        failures.extend(verdict.failures)
        if timings is not None:
            timings.record_check(verdict)
    report.gate_contracts, report.gate_contracts_green = status, green
    report.contracts_recordable = recordable


def _join_gates(
    report: CycleReport,
    failures: list[str],
    js_fut: Future | None,
    contracts_fut: Future | None,
    conform_fut: Future | None,
    make_fut: Future | None,
    emit: console.Digest,
    timings: CycleTimings | None = None,
) -> None:
    """Record every gate that ran in the report, and each gate's verdict in the timings journal. The JS suite and `make test` are judged by exit code alone, so `_rc_verdict` builds their verdicts here. gate:make-test's wrapper records no check line when CYCLE_RUN_ENV is set, so the line recorded here is the only one for it."""
    if js_fut is not None:
        js = _gate_result(js_fut, "gate:js", failures)
        if js is None:
            report.gate_js = "FAILED (exception)"
            report.gate_js_green = False
        else:
            verdict = _rc_verdict("js", js.returncode, "JS suite failed")
            report.gate_js_green = verdict.ok
            report.gate_js = verdict.status
            failures.extend(verdict.failures)
            if timings is not None:
                timings.record_check(verdict)
    if contracts_fut is not None:
        _join_rebuild_lane(report, failures, contracts_fut, "contracts", emit, timings)
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
            verdict = _rc_verdict("make-test", make.returncode, "make test failed")
            report.gate_make_test_green = verdict.ok
            report.gate_make_test = (
                MAKE_TEST_SELF_SKIP_STATUS
                if verdict.ok and make_test_self_skipped(make.stdout)
                else verdict.status
            )
            failures.extend(verdict.failures)
            if timings is not None:
                timings.record_check(verdict)


def _plumbing_settled(report: CycleReport) -> bool:
    """Return whether the chain reached a fixpoint, which the plumbing green record claims. The chain prints its `fixpoint: witnessed` line only after an echo round writes nothing new. A standing merge that writes nothing would not be enough: a standing fill on one unit can make its echo group unanimous and leave a blank member that only another echo fill would fill."""
    return report.plumbing_fixpoint


def _record_gate_greens(
    report: CycleReport, plan: Plan, gate_keys: dict[str, str], emit: console.Digest
) -> None:
    """Write the green records of the gates that ran beside the build, after they joined. gate:conform's key is taken right after run_m1 finishes, where its skip is decided, and the rebuild suite's just before the surface build, where the suite is submitted. Later steps cannot change either key: the surface build writes only review output, which the suite's closure excludes, and the census pins are exempt from that closure. Each key is recomputed here before recording, so a source file edited while the gates ran is never recorded green. A red gate whose key matches its existing record deletes that record."""
    key = gate_keys.get("conform")
    if key:
        if report.gate_conform_green is True:
            if conform_skip_fingerprint(ROOT, plan.conform_horizon) == key:
                record_green(
                    cycle_paths.CONFORM_GREEN, key, files=conform_skip_files(ROOT, plan.conform_horizon)
                )
            else:
                emit.note(
                    "gate:conform",
                    "gate:conform green, but its inputs changed while the cycle ran — green not recorded",
                )
        elif report.gate_conform_green is False:
            clear_contradicted_green(cycle_paths.CONFORM_GREEN, key)
    for lane, recordable, green in (("contracts", report.contracts_recordable, report.gate_contracts_green),):
        key = gate_keys.get(lane)
        if not key:
            continue
        record = rebuild_lane_green(lane)
        if recordable:
            drifted = f"gate:rebuild-{lane} green, but its input closure changed while the cycle ran — green not recorded"
            if lane == "contracts":
                now, roster = rebuild_lane_closure(ROOT, lane)
                payload = _contracts_payload(plan, roster) if now == key else None
                if payload is None:
                    emit.note(f"gate:rebuild-{lane}", drifted)
                else:
                    record_green(record, key, files=payload.files, closures=payload.closures)
            elif rebuild_lane_fingerprint(ROOT, lane) == key:
                record_green(record, key)
            else:
                emit.note(f"gate:rebuild-{lane}", drifted)
        elif green is False:
            clear_contradicted_green(record, key)


def _write_contracts_selection(plan: Plan) -> None:
    """Write the selection file the contracts spawn reads from the ids the plan resolved, and delete the previous run's sidecar so that a suite that dies before session end cannot leave a stale one to be merged as this run's."""
    from rebuild.tools import contracts_closure

    record = rebuild_lane_green("contracts")
    contracts_closure.write_selection(contracts_closure.selection_path(record), plan.contracts_skip)
    contracts_closure.sidecar_path(record).unlink(missing_ok=True)


def _contracts_payload(plan: Plan, roster: dict[str, str] | None):
    """Return what a green contracts gate records beside its key, or None when the roster is unavailable or a label the selection was taken over has changed since (`contracts_closure.record_payload`)."""
    from rebuild.tools import contracts_closure

    if roster is None:
        return None
    record = rebuild_lane_green("contracts")
    payload = contracts_closure.record_payload(
        ROOT,
        plan.contracts_files or {},
        roster,
        read_green_record(record),
        contracts_closure.sidecar_path(record),
    )
    return None if payload.moved else payload


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
    emit: console.Digest,
    registry: _ChildRegistry,
    spawn=_run_step,
    timings: CycleTimings | None = None,
) -> int:
    if timings is not None:
        spawn = timings.wrap_spawn(spawn)
    spawn = _timed_spawn(spawn, report)
    pool = ThreadPoolExecutor(max_workers=_GATE_POOL_WORKERS)
    failures: list[str] = []
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
        gate_keys: dict[str, str] = {}
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
            reuse=plan.reuse_run_m1,
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
            conform_key = conform_skip_fingerprint(ROOT, plan.conform_horizon)
            green = None if plan.fresh else read_green_record(cycle_paths.CONFORM_GREEN)
            if green is not None and green["fingerprint"] == conform_key:
                report.conform_proven = True
                report.gate_conform = f"skipped ({CONFORM_SKIP_NOTE})"
                emit.step_skipped(
                    "gate:conform",
                    f"SKIPPED after run_m1 — {CONFORM_SKIP_NOTE}. The lookup this pass emits asks HarfBuzz for no shape its last green sweep did not shape, and run_m1's string replay has held the tables to the engine over every swept text.",
                )
            else:
                if plan.record_greens:
                    gate_keys["conform"] = conform_key
                conform_fut = pool.submit(
                    _gate_conform_task,
                    plan.pool_policy,
                    make_fut,
                    spawn,
                    emit,
                    registry,
                    plan.argv("gate:conform"),
                )

        if not plan.skip_gates and not plan.skip_contracts:
            if plan.record_greens:
                gate_keys["contracts"] = rebuild_lane_fingerprint(ROOT, "contracts") or ""
            _write_contracts_selection(plan)
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
                plan.argv("gate:rebuild-contracts"),
            )

        if plan.promote_surface is not None and not _do_promote_surface(report, emit=emit, plan=plan):
            failures.append("surface promotion failed")
            _join_gates(report, failures, js_fut, contracts_fut, conform_fut, make_fut, emit, timings)
            _record_gate_greens(report, plan, gate_keys, emit)
            return _finish(report, failures, plan, timings, emit)

        if plan.runs("assets-refresh") and not _do_assets_refresh(
            report, spawn=spawn, emit=emit, registry=registry, plan=plan
        ):
            failures.append("assets refresh failed")
            _join_gates(report, failures, js_fut, contracts_fut, conform_fut, make_fut, emit, timings)
            _record_gate_greens(report, plan, gate_keys, emit)
            return _finish(report, failures, plan, timings, emit)

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
            _join_gates(report, failures, js_fut, contracts_fut, conform_fut, make_fut, emit, timings)
            _record_gate_greens(report, plan, gate_keys, emit)
            return _finish(report, failures, plan, timings, emit)

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
            report.census_reach = "skipped (rehearsal)"
            emit.step_skipped("census", "rehearsal: the checked-in pins track the live surface")
        else:
            _do_census(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        if plumbing_key and report.complaints_ok is True and plan.record_greens and plan.review_out is None:
            record_plumbing_green(plumbing_key)

        _join_gates(report, failures, js_fut, contracts_fut, conform_fut, make_fut, emit, timings)
        _record_gate_greens(report, plan, gate_keys, emit)
        _do_job_costs(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        return _finish(report, failures, plan, timings, emit)
    except KeyboardInterrupt as stop:
        registry.terminate_all()
        pool.shutdown(wait=False, cancel_futures=True)
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


INFORMATIONAL_STEPS = ("census", "job-costs")

_CARRY_WROTE = re.compile(r"^wrote \S+: (\d+) carried onto manifest")
_CARRY_QUEUE = re.compile(r"^human queue: (\d+) -> (\d+)")
_CARRY_FIGURES = re.compile(r"^carry figures: human=(\d+) key_hits=(\d+) unhit=(\d+) stranded=(\d+)$")


def carry_figures(lines: list[str]) -> dict[str, int] | None:
    """Parse the carry's `carry figures:` line: the human units on the new surface, how many a prior verdict matched, how many none matched, and how many prior verdicts matched no unit. The figures are written to the cycle summary and to the run line in the timings journal. Returns None when the carry printed no such line (the store-only route, a rehearsal, or a chain that failed before the carry)."""
    for line in lines:
        match = _CARRY_FIGURES.match(line)
        if match is not None:
            return dict(zip(("human", "key_hits", "unhit", "stranded"), map(int, match.groups())))
    return None


def carry_figure(lines: list[str]) -> str:
    """Summarize the carry from its two headline lines: how many verdicts it carried onto the new surface, and the human queue before and after. The chain runs as one child with the carry as a step inside it, so these counts reach this process only as printed lines. Returns the empty string when the carry printed neither line (the store-only route, a rehearsal, or a chain that failed before the carry)."""
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


def step_figure(report: CycleReport, name: str) -> str:
    """Return one step's figure for the summary table and the step's closing line, or the empty string when the step has nothing to report.

    `summary_rows` drops the figure on a `skipped` or `not run` row, because the report can still hold the previous build's numbers for a step this pass did not run. A gate's figure is the status string its judge wrote (the report field `_GATE_STATUS_FIELDS` names), with a plain "green" dropped because the outcome column already says it.
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
    if name == "surface-build":
        parts = []
        if report.surface_units is not None:
            parts.append(f"{count(report.surface_units)} units")
        if report.surface_rows is not None:
            parts.append(f"{count(report.surface_rows)} rows")
        return ", ".join(parts)
    if name == "assets-refresh":
        return prose(report.assets_status)
    if name == "surface-promote":
        return prose(report.promote_status)
    if name == "plumbing":
        head = carry_figure(report.carry_lines)
        if not head:
            merged = prose(report.merge_status)
            head = f"merge {merged}" if merged else ""
        return f"{head}; {report.complaints_status}" if head else ""
    if name == "census":
        return prose(report.census_status)
    if name == "job-costs":
        return prose(report.job_costs_status)
    if name == "retention":
        return report.retention_figure
    status = _GATE_STATUS_FIELDS.get(name)
    if status is not None:
        judged = prose(str(getattr(report, status)))
        return "" if judged == "green" else judged
    return ""


def _figure_beside(outcome: str, figure: str) -> str:
    """Return `figure` with the leading `outcome` word removed, so `FAILED (exit 1)` beside the outcome `FAILED` becomes `exit 1`. Returns the empty string when the figure only repeats the outcome."""
    if not figure or figure == outcome:
        return ""
    if figure.startswith(outcome):
        rest = figure[len(outcome) :].strip()
        return rest[1:-1].strip() if rest.startswith("(") and rest.endswith(")") else rest
    return figure


def _close_step(
    emit: console.Digest,
    report: CycleReport,
    name: str,
    result: _StepResult | None,
    outcome: str | None = None,
) -> None:
    """Print a spawned step's closing line with its figure. `outcome` defaults to one derived from the child's exit status; callers that judge otherwise pass their own (run_m1 judges by its summaries, and census and job-costs always pass `ok` because they gate nothing)."""
    verdict = outcome
    if verdict is None:
        verdict = "ok" if result is None or result.returncode == 0 else f"FAILED (exit {result.returncode})"
    figure = step_figure(report, STEP_ALIASES.get(name, name))
    emit.step_end(name, result, verdict, _figure_beside(verdict, figure))


def _close_gate(
    emit: console.Digest, name: str, result: _StepResult, verdict: CheckVerdict | None = None
) -> None:
    """Print a gate's closing line from the verdict its task just reached, or from the exit status when there is no verdict. `_close_step` would read the report, which `_join_gates` fills in only later, so at this point it still says the gate has not run. A plain green status is dropped from the figure, as in the table."""
    if verdict is None:
        status = "green" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
        passed = result.returncode == 0
    else:
        status, passed = verdict.status, verdict.ok
    outcome = "ok" if passed else "FAILED"
    emit.step_end(name, result, outcome, _figure_beside(outcome, "" if status == "green" else status))


def _step_outcome(report: CycleReport, plan: Plan, step: Step, *, retention_ran: bool) -> str:
    """Return the table's outcome for one step: `ok`, `FAILED`, `skipped`, or `not run`. The figure carries any detail, and the plan block says why a step did not run.

    A gate's outcome comes from its status string. Any other step that ran is judged by run_m1's failure flag or the child's exit code, since having a recorded time only shows that it ran. A nonzero exit from census or job-costs (`INFORMATIONAL_STEPS`) is not a failure, because they gate nothing and their figures report the problem.

    Retention runs inside `_finish`, so it reads `not run` when an upstream failure or a stop signal ended the pass first, and also when retention itself raised. It reads `skipped` only when the plan ruled it out (`--keep-history`, a first run, or a rehearsal).
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
    """Return one summary-table row per planned step. A step that did not run gets no figure, because the report can still hold the previous build's counts for it (a skipped run_m1's unmatched count, a skipped surface build's totals)."""
    rows: list[console.SummaryRow] = []
    for step in plan.steps:
        outcome = _step_outcome(report, plan, step, retention_ran=retention_ran)
        ran = outcome not in ("skipped", "not run")
        rows.append(
            console.SummaryRow(
                number=None,
                name=step.name,
                outcome=outcome,
                figure=_figure_beside(outcome, step_figure(report, step.name)) if ran else "",
                seconds=report.step_seconds.get(step.name),
            )
        )
    return rows


def summary_cycle_lines(report: CycleReport, plan: Plan, retention_lines: list[str]) -> list[str]:
    """Return the summary lines below the table: output paths, the verdict chain's per-step status, the deep sweep and deep replay status, and what retention pruned.

    The lines scraped from the chain's output (by `_do_plumbing`, with `_standing_fill_news` for the standing fill) are indented under the carry and plumbing lines, so the summary shows what each carry, fill, and merge wrote and how the human queue changed.
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
        f"  verdict plumbing : merge {report.merge_status}; echo-fill {report.echo_fill_status}; echo-merge {report.echo_merge_status}; standing-fill {report.standing_fill_status}; standing-merge {report.standing_merge_status}",
        *news(
            report.merge_lines
            + report.echo_fill_lines
            + report.echo_merge_lines
            + report.standing_fill_lines
            + report.standing_merge_lines
        ),
        f"  complaint groups : {report.complaints_status}",
        f"  census pins      : {report.census_status}",
        f"  census reach     : {report.census_reach}",
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
                _skip_kind(proved=plan.skip_contracts),
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
        "surface_units": report.surface_units,
        "surface_rows": report.surface_rows,
        "surface_batches": report.surface_batches,
        "assets_status": report.assets_status,
        "promote_status": report.promote_status,
        "echo_groups": report.echo_groups,
        "carry_out": _as_str(report.carry_out),
        "carry_lines": list(report.carry_lines),
        "carry": report.carry_figures,
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
        "census_reach": report.census_reach,
        "census_reach_sets": report.census_reach_sets,
        "job_costs_status": report.job_costs_status,
        "job_costs_ok": report.job_costs_ok,
        "complaints_status": report.complaints_status,
        "log_dir": _as_str(plan.log_dir),
        "interrupted": report.interrupted,
        "plan": {
            "verdicts": _as_str(plan.verdicts),
            "carry_out": _as_str(plan.carry_out),
            "do_merge": plan.do_merge,
            "conform_horizon": plan.conform_horizon,
            "kernel_threads": None if plan.reuse_run_m1 else plan.kernel_threads,
            "replay_threads": None if plan.reuse_run_m1 else plan.replay_threads,
            "pool_policy": plan.pool_policy,
            "skip_gates": plan.skip_gates,
            "skip_conform": plan.skip_conform,
            "skip_run_m1": plan.skip_run_m1,
            "reuse_run_m1": plan.reuse_run_m1,
            "skip_surface": plan.skip_surface,
            "refresh_assets": plan.refresh_assets,
            "promote_surface": _as_str(plan.promote_surface),
            "skip_contracts": plan.skip_contracts,
            "skip_plumbing": plan.skip_plumbing,
            "review_out": _as_str(plan.review_out),
            "first_run": plan.first_run,
            "short_id": plan.short_id,
        },
        "argv": list(sys.argv),
        "surface": _surface_block(plan.surface_dir),
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


def prune_carried(root: Path, stamp: str | None, keep: Path | None) -> tuple[list[Path], list[Path]]:
    """Delete the repo-root `verdicts-carried-*.json` files whose `manifest_generated_at` is not `stamp`, sparing `keep`. `status.pick_frontier` considers only files stamped for the live surface, and the tracked copy under `rebuild/evidence/` is outside this glob. Returns the deleted paths and the unreadable ones, which are kept. Deletes nothing when `stamp` is None."""
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
    """Delete the `verdicts-autosave-*` stashes that no journal event at or after the last base event references, and return them. Returns None and deletes nothing when the journal has no base event. The test uses journal references because mtime is wrong here: `os.replace` keeps the displaced store's mtime, so the stash the latest base created looks older than that base. `merge_verdicts --restore-as-of` can rebuild a deleted stash's state from the journal back to the journal's compaction floor."""
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
    """A retention pass's result: `lines` for the summary block, and `figure` for the retention row and closing line."""

    lines: list[str]
    figure: str


def _retention_figure(removed: list[str], intact: list[str], journal_state: str) -> str:
    """Format the retention figure: the removal counts, the kinds of file left intact, and the journal's state. A kind left intact is named instead of counted as zero, because "nothing to remove" and "not pruned on this pass" are different facts."""
    clauses = ["removed " + (", ".join(removed) if removed else "nothing")]
    if intact:
        named = intact[0] if len(intact) == 1 else f"{', '.join(intact[:-1])} and {intact[-1]}"
        clauses.append(f"{named} left intact")
    if journal_state:
        clauses.append(journal_state)
    return "; ".join(clauses)


def run_retention(plan: Plan) -> RetentionResult:
    """Prune stale carried files, build logs, autosave stashes, and old journal history after a green pass, and return the summary lines and figure. It returns the lines instead of printing them so they appear in the summary block below the table. Stashes and the journal are left alone while the review server is listening, because the app appends to the journal."""
    from rebuild.review import journal

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
        lines.append("  carried   : left intact (no surface manifest to align against)")
        intact.append("carried files")
    else:
        removed, unreadable = prune_carried(ROOT, stamp, plan.carry_out)
        removed_counts.append(swept(len(removed), "carried", "carried"))
        lines.append(
            f"  carried   : removed {console.fmt_count(len(removed))} stale verdicts-carried-*.json; kept the stamp-aligned frontier"
        )
        for path in unreadable:
            lines.append(f"              kept {rel(path)} (unreadable, not pruning it)")

    dropped_logs = prune_build_logs(cycle_paths.BUILD_LOGS_ROOT, cycle_paths.BUILD_LOGS_KEEP)
    removed_counts.append(swept(len(dropped_logs), "build log", "build logs"))
    lines.append(
        f"  build logs: removed {console.fmt_count(len(dropped_logs))}; kept the last {cycle_paths.BUILD_LOGS_KEEP} runs under {rel(cycle_paths.BUILD_LOGS_ROOT)}"
    )

    journal_path = ROOT / journal.JOURNAL_NAME
    if server_listening():
        lines.append(
            "  stashes   : left intact (the review server is up, and the index of which ones are still referenced comes from the journal this pass is leaving alone)"
        )
        lines.append(
            "  journal   : left intact (the review server is up: the app appends to the journal as you verdict, and a compaction rewrites the whole file around a read, so anything landing in between would be dropped)"
        )
        intact.extend(["stashes", "journal"])
        return RetentionResult(lines, _retention_figure(removed_counts, intact, ""))

    removed_stashes = prune_stashes(ROOT, journal_path)
    if removed_stashes is None:
        lines.append("  stashes   : left intact (the journal holds no base event to anchor on)")
        intact.append("stashes")
    else:
        removed_counts.append(swept(len(removed_stashes), "stash", "stashes"))
        lines.append(
            f"  stashes   : removed {console.fmt_count(len(removed_stashes))} verdicts-autosave-* stashes older than the journal's last base"
        )

    result = journal.compact(journal_path, cutoff=retention_cutoff())
    if result["compacted"]:
        total = result["dropped_lines"] + result["kept_lines"]
        lines.append(
            f"  journal   : compacted {console.fmt_count(total)} -> {console.fmt_count(result['kept_lines'])} lines (restore floor now {result['floor_at']})"
        )
        journal_state = f"journal compacted to {console.fmt_count(result['kept_lines'])} lines"
    else:
        lines.append(f"  journal   : left intact (no base event older than {RETENTION_WINDOW_DAYS} days)")
        journal_state = "journal intact"
    return RetentionResult(lines, _retention_figure(removed_counts, intact, journal_state))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the commit-time artifact cycle: run_m1, surface rebuild, carry, census pins, gates."
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
        help="how the heavy gates share cores: 'queue' (one pool at a time — make-test, then conform, then the rebuild suite; default) or 'overlap' (co-resident, the rebuild suite narrowed by make-test's pool as well as the surface build's)",
    )
    parser.add_argument(
        "--review-out",
        type=Path,
        default=None,
        help="rehearsal mode: redirect the surface write to this dir so the cycle can run while the live server is up; the next live pass moves this dir into rebuild/out/review and consumes it when it still reproduces the inputs byte for byte (promotable_surface)",
    )
    parser.add_argument(
        "--keep-history",
        action="store_true",
        help="skip the green-finish retention pass (stale carried files and stashes, and the journal's pre-window history all stay on disk)",
    )
    parser.add_argument("--yes", action="store_true", help="override the running-review-server refusal")
    parser.add_argument(
        "--stop-server",
        action="store_true",
        help="stop a listening review server instead of refusing, but only when this pass writes under it — the served surface's units or stamp, or the verdict store it holds. A pass that writes neither leaves the server up whether or not this is passed, so the letters stay on screen through it — an assets refresh is such a pass, since it moves no unit and no stamp and livereload simply reloads the tab onto the new shell; `make review-cycle` passes this, which is what makes a pass with no artifact work background verification rather than a lockout. It also says the recipe answers the server question after the pass, so the readiness checklist a green finish prints leaves the server row to it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved step plan and exit without executing anything",
    )
    args = parser.parse_args(argv)
    if args.fresh:
        args.force_make_test = True

    recovered = recover_superseded_surface(delete=not args.dry_run)
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
    reuse_run_m1 = False
    run_m1_note = ""
    skip_surface = False
    refresh_assets = False
    promote_from: Path | None = None
    surface_note = ""
    skip_contracts = False
    contracts_note = ""
    conform_note = ""
    auto_skip_conform = False
    contracts_skip: list[str] = []
    contracts_files: dict[str, str] | None = None
    if not args.fresh and not args.skip_gates:
        from rebuild.tools import contracts_closure

        contracts_key, contracts_roster = rebuild_lane_closure(ROOT, "contracts")
        green = read_green_record(cycle_paths.REBUILD_CONTRACTS_GREEN)
        if contracts_key is not None and green is not None and green["fingerprint"] == contracts_key:
            skip_contracts = True
            contracts_note = "input closure unchanged since its last green run; --fresh overrides"
        elif contracts_roster is not None:
            contracts_files = contracts_closure.current_files(ROOT, contracts_roster, green)
            selection = contracts_closure.select(green, contracts_files)
            contracts_skip = sorted(selection.skip)
            contracts_note = selection.describe()
    if not args.fresh:
        green = read_green_record(cycle_paths.RUN_M1_GREEN)
        if green is not None and green["fingerprint"] == run_m1_fp and m1_artifacts_present(ROOT):
            skip_run_m1 = True
            run_m1_note = "build inputs unchanged since the last green M1 build; --fresh overrides"
        elif green is not None:
            current = run_m1_skip_files(ROOT)
            reusable = gates_only_reuse(green, current)
            if reusable is not None and m1_artifacts_present(ROOT) and m1_tables_stamped():
                reuse_run_m1 = True
                run_m1_note = (
                    f"only comparison-side inputs moved since the last green M1 build ({capped_labels(reusable)}); "
                    "the tables and font are reused and the gates re-run over them; --fresh overrides"
                )
            else:
                note = moved_inputs_note(green, current)
                if note is not None:
                    run_m1_note = f"inputs moved since its last green: {note}"
                    cache_note = oracle_cache_note(note)
                    if cache_note is not None:
                        run_m1_note = f"{run_m1_note}; {cache_note}"
    if skip_run_m1 or (reuse_run_m1 and m1_stage_a_current(ROOT)):
        if args.review_out is None and not first_run:
            if surface_build_skippable(ROOT):
                skip_surface = True
                surface_note = "the surface already reflects these inputs byte for byte, stamp included; --fresh overrides"
            elif surface_build_skippable(ROOT, ignore=unit_index.ASSET_COMPONENTS):
                skip_surface = True
                refresh_assets = True
                surface_note = ASSETS_REFRESH_NOTE
            else:
                promote_from = promotable_surface(ROOT)
                if promote_from is not None:
                    skip_surface = True
                    surface_note = SURFACE_PROMOTE_NOTE
    if skip_run_m1:
        if not args.skip_gates and not args.skip_conform:
            green = read_green_record(cycle_paths.CONFORM_GREEN)
            if green is not None and green["fingerprint"] == conform_skip_fingerprint(
                ROOT, args.conform_horizon
            ):
                auto_skip_conform = True
                conform_note = CONFORM_SKIP_NOTE

    preamble: list[str] = []

    def announce(text: str) -> None:
        """Print a line before the digest exists, and queue it for `digest.replay` so terminal.log also gets it."""
        preamble.append(text)
        print(text)

    if recovered is not None:
        announce(recovered)

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

    skip_plumbing = False
    store_only = False
    plumbing_note = ""
    if (
        skip_surface
        and promote_from is None
        and not args.fresh
        and not first_run
        and args.review_out is None
        and not args.no_carry
        and not args.no_merge
        and args.carry_out is None
    ):
        plumbing_key = plumbing_skip_fingerprint(ROOT, REVIEW_OUT, args.verdicts)
        record = read_green_record(cycle_paths.PLUMBING_GREEN)
        if plumbing_key is not None and record is not None and record["fingerprint"] == plumbing_key:
            skip_plumbing = True
            plumbing_note = PLUMBING_SKIP_NOTE
        elif plumbing_key is not None and args.verdicts is not None:
            if master_aligned is None:
                master_aligned = master_stamped_for_surface(args.verdicts, REVIEW_OUT)
                if not master_aligned:
                    announce(STORE_ONLY_DECLINED_NOTE)
            # A master stamped for the served surface needs no carry: every unit id maps to itself, and the carry keeps each record's `at`, so the merge (which takes only a strictly newer `at`) would drop its re-prefixed notes. Merging the master directly into the store gives the same result.
            store_only = master_aligned

    plan = build_plan(
        verdicts=args.verdicts,
        no_carry=args.no_carry,
        carry_out=args.carry_out,
        skip_gates=args.skip_gates,
        first_run=first_run,
        short_id=resolve_short_id(),
        no_merge=args.no_merge,
        skip_conform=args.skip_conform or auto_skip_conform,
        skip_make_test=skip_make_test,
        make_test_note=make_test_note,
        make_test_fingerprint=make_test_fp,
        force_make_test=args.force_make_test,
        conform_horizon=args.conform_horizon,
        pool_policy=args.rebuild_pool,
        review_out=args.review_out,
        skip_run_m1=skip_run_m1,
        reuse_run_m1=reuse_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fp,
        fresh=args.fresh,
        skip_surface=skip_surface,
        refresh_assets=refresh_assets,
        promote_surface=promote_from,
        surface_note=surface_note,
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        contracts_skip=contracts_skip,
        contracts_files=contracts_files,
        conform_note=conform_note,
        conform_proven=auto_skip_conform,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        store_only=store_only,
        record_greens=not args.dry_run,
        keep_history=args.keep_history,
        recipe_serves=args.stop_server,
    )

    if args.dry_run:
        print("\n".join(render_plan(plan)))
        return 0

    plan.stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan.log_dir = cycle_paths.BUILD_LOGS_ROOT / f"{plan.stamp}-{plan.short_id}"
    digest = console.Digest(
        steps=[step.name for step in plan.steps], log_dir=plan.log_dir, aliases=STEP_ALIASES
    )
    with digest:
        digest.replay(preamble)
        digest.plan_block(render_plan(plan))
        if not _preflight(
            args,
            may_stay_up=server_may_stay_up(
                skip_surface=skip_surface,
                writes_store=plan.do_merge,
                promotes_surface=plan.promote_surface is not None,
            ),
        ):
            return 2

        if first_run:
            print("First-run mode: no existing surface at rebuild/out/review — skipping the carry.")

        report = CycleReport()
        from rebuild.tools.cycle_timings import CycleTimings

        timings = CycleTimings(CYCLE_TIMINGS)
        # run_m1's CLI and the make_test_gate wrapper skip writing their own check line to the timings journal when this is set, because the cycle records it. It goes in the environment because the wrapper runs as a grandchild, under the `make test` recipe, where an argv flag would not reach it.
        os.environ[CYCLE_RUN_ENV] = timings.run_id

        registry = _ChildRegistry()
        with stop_signals():
            return _run_cycle(plan, report, digest, registry, timings=timings)


def readiness_block(plan: Plan) -> list[str]:
    """Return the checklist `make verdict-ready` prints, so a green pass ends with it. It reads the cycle summary, so it must run after `_emit_cycle_summary`. A rehearsal returns nothing, since its surface is not the served one. When `--stop-server` is passed (as `make review-cycle` does), the server row is left out, because the recipe starts the server after the pass, or reports that it left it stopped. The rebuild suite switches this off with `cycle_paths.READINESS_ENABLED`, because it reads the live surface."""
    if plan.review_out is not None:
        return []
    from rebuild.tools import verdict_ready

    try:
        result, ready = verdict_ready.readiness(
            with_server=not plan.recipe_serves,
            repo_root=ROOT,
            review_dir=plan.surface_dir,
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
    emit: console.Digest | None = None,
) -> int:
    """Finish the pass and return its exit status: run retention on a green pass, write the cycle summary, then print the summary block, ending with the readiness checklist on a green pass. Retention runs before the table is printed so its row has an outcome and figure. The rebuild suite sets `cycle_paths.RETENTION_ENABLED` False so a test that reaches a green finish does not prune the live repo; retention then counts as run with no lines. `cycle_paths.READINESS_ENABLED` switches the checklist off the same way."""
    digest = console.Digest() if emit is None else emit
    retention_lines: list[str] = []
    retention_ran = False
    if not failures and plan.retention and plan.record_greens:
        digest.step_start("retention", None, plan.describe("retention"))
        started = time.perf_counter()
        try:
            pruned = run_retention(plan) if cycle_paths.RETENTION_ENABLED else RetentionResult([], "")
            retention_lines = list(pruned.lines)
            report.retention_figure = pruned.figure
            retention_ran = True
        except Exception as exc:
            retention_lines = [f"warning: retention pass failed: {exc!r}"]
        report.step_seconds["retention"] = time.perf_counter() - started
        digest.step_end("retention", None, "ok" if retention_ran else "FAILED", report.retention_figure)
    _emit_cycle_summary(report, failures, plan, "failed" if failures else "ok", timings)
    readiness = [] if failures or not cycle_paths.READINESS_ENABLED else readiness_block(plan)
    digest.summary(
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
    emit: console.Digest | None = None,
    *,
    signum: int = signal.SIGINT,
) -> int:
    """Finish a pass that a stop signal ended, after its children were terminated. The cycle summary and timings journal record it as interrupted, the summary block names the signal and the number of children killed, and the exit status is 128 plus the signal number, as a shell reports it."""
    digest = console.Digest() if emit is None else emit
    _emit_cycle_summary(report, failures, plan, "interrupted", timings)
    digest.summary(
        summary_rows(report, plan, retention_ran=False),
        summary_cycle_lines(report, plan, []),
        console.VERDICT_INTERRUPTED,
        [*failures, f"{signal.Signals(signum).name}: terminated {killed_count} child process(es)"],
    )
    return 128 + signum


if __name__ == "__main__":
    sys.exit(main())
