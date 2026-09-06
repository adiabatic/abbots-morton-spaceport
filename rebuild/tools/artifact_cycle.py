"""The one-command driver for the commit-time artifact cycle.

It mechanizes the commit-time sequence: snapshot the current review surface (the only recovery copy, since everything under rebuild/out is gitignored), recompile M1.otf and vet it, rebuild the review surface in place, run the verdict plumbing over it, refresh the census pins from the surface's census sidecar and print their git diff (the checked-in pins are the last accepted census, so reviewing that diff at commit time is what accepts a new one), run the five gates, and — once they have joined and their pytest controllers have stamped this pass's own per-worker peaks into the timings journal — hold the checked-in per-unit peaks against what this box actually measured (rebuild.tools.calibrate_budgets --check). Always ending on a summary table, even on failure. What the terminal shows is a digest — one banner per step carrying the description of what that step is for, the phases and counters its child speaks, every warning, and a closing line — while the whole of every child's output lands under tmp/build-logs/<stamp>-<short sha>/: one log per step with stdout and stderr merged in arrival order, beside plan.txt and a byte copy of the terminal, with tmp/build-logs/latest pointing at the newest run and a failed step replaying its own log verbatim under its banner. rebuild.tools.console owns both halves of that — the line protocol a child speaks and the renderer that reads it.

That last step gates nothing, by the same argument the census pins are not a gate: a divisor that has gone stale makes a pool the wrong width, which is a cost rather than a defect, so it is reported loudly and never fails a pass whose artifacts are green. Committing the re-seeded constant is the acceptance, and when the check trips the driver diffs the three files that hold those constants so a working tree where one has already moved says so.

The plumbing is one step and one child process, rebuild.tools.verdict_chain: carry prior verdicts forward onto the fresh manifest, merge the carried file into the live autosave (so the app needs no manual import; --no-merge opts out), land echo-prefill verdicts for the blanks in unanimously-judged echo groups, land standing-approval verdicts matching the checked-in rules in rebuild/standing-approvals.yaml, merge each fill as it lands, run the echo pass again to witness that the cascade has closed, and cluster the open complaints. It was seven children until each of them separately parsed 1.9 GB of unit shards to reach a few slim fields per unit; they read the build's per-unit index sidecar now, and one process holds one copy of it for the whole chain. The chain opens each of its steps with a `[phase] <step>` line and closes it with `[t] <step>`, so the digest pairs the two into one line per step while the cycle-timings journal still reads that step's cost off the same `[t]`; its `[chain] fixpoint:` and `[chain] failed:` lines are results rather than phases and keep that prefix, which is what plumbing_sections splits the child's output on.

run_m1's exit status is its own gate's verdict, but this driver judges from the three summary JSONs it writes rather than from the exit code, so a build that died before its judge is reported by what it left behind. The real gates are defect_errors, the Manual-pin verdict (scope included, so a gate that replayed nothing cannot pass), and multi_matched == 0.

That trap is also why this process, and not the children it spawns, is what files each check's verdict in the timings journal. Every judged check here — run_m1, conform, both rebuild lanes, make-test, js — appends one kind:"check" line tagged with this run, carrying the verdict the judge reached rather than the exit code the process returned; `make cycle-timings ARGS='--by-outcome'` is what reads them back. Two of those checks have interactive entry points that record their own line when a human runs them, so this driver puts its run id in the environment as AMS_CYCLE_RUN (cycle_timings.CYCLE_RUN_ENV) and every child inherits it, which is their signal to stand down. One invocation, one line, and the count in that report is a count of checks rather than of processes that happened to have an opinion.

The two artifact-independent gates (js, make-test) run from t=0 in a small thread pool while the build chain runs inline-serial in the main thread. gate:conform (the exhaustive font-vs-settle sweep at the per-edit horizon, run_m1 --conform-only) starts after the run_m1 gate passes, queued behind make-test by default; its periodic deep form is `make conform-deep`, which the cycle never runs and only reports on — one line in the summary saying whether the emitted lookup has grown a shape the last deep run never shaped. The rebuild suite runs as two gates over two lanes (rebuild/conftest.py is the authority on which test is which): gate:rebuild-contracts is every test whose fixture closure holds no live build artifact, at the box's full xdist width, and gate:rebuild-validators is the rest — the readers of rebuild/out, the review surface and the fixture caches — at the narrower width rebuild/conftest.py derives for that lane, because each of those workers carries a live fixture's working set. Both are submitted once the surface build settles. For validators that is a correctness requirement: its census-module fixture prefers the provably-fresh live surface and must never observe one mid-rewrite, where the manifest has landed but review.build has not yet written the sidecar beside it. For contracts it is only courtesy — the lane reads no artifact at all — but a full-width pool must not share the box with the M1 or surface build, and waiting costs it nothing, since it parks behind conform anyway and on the common gate pass every upstream stage auto-skips, so it starts at t=0. From there on neither lane reads anything the build lane writes, the census pins included, so nothing downstream has to land before they can start. Under the default queue policy the chain is make-test -> conform -> rebuild-contracts -> rebuild-validators, so only one heavy gate pool is hot at a time — the build chain rides alongside whichever one that is rather than serial, at the widths sweep_job_budget and surface_job_budget resolve (the sweeps take one process per acceptance configuration; the surface build takes the box minus whatever make-test is holding). Contracts goes ahead of validators because it is the short lane and fails fast on a code error before the long one starts. Co-resident, two heavy pools oversubscribe the cores roughly 2:1, and measured that contention roughly tripled the rebuild suite's wall time — a worse critical path than running the same work in sequence. --rebuild-pool overlap restores full co-residency.

The cycle runs no cross-language check, because there is no second implementation to check against: the kernel crate is the only engine that enumerates and the only one that settles, so neither the tables nor a window's outcome can drift from a twin. What the cycle does prove about settlement is empirical — gate:conform shapes the compiled font through HarfBuzz and compares it against a re-settle of every swept text, window by window, through the crate's own settle-cases verb, with the memo keyed on the raw window so the sweep stays independent of the crate's enumeration and fold. `make kernel-gate` is the on-demand instrument to reach for around a kernel-semantics change: the crate's own gate, seconds once the crate is built. The spec-ingest parity is a contracts test now and rides gate:rebuild-contracts every cycle.

gate:make-test is auto-skipped when its input closure is provably unchanged since the last green run. The closure is every tracked or untracked-unignored file outside what make_test_exempt exempts — the exempt trees, the exempt files, Markdown, and the Makefile itself beyond what `make -n all` and `make -n test` print — that function being the authority and arguing each exemption from what the gate executes (make all -> build_font over glyph_data/*.yaml non-recursively, typst, pyright over tools/ test/ conftest.py, pytest test/ site/), none of which reads any of it, so a diff confined there cannot move the gate's outcome and re-running its ≈15 CPU-minutes would verify nothing. The last green fingerprint lives in rebuild/out/make-test-green.json, written by rebuild.tools.make_test_gate — the `make test` entry point — on every green run, so interactive greens and cycle greens share one record and `make test` itself self-skips on the same test. cycle_summary.json still records the fingerprint the cycle ran (or validly skipped) against, for display only — the skip decision reads the shared green record alone, so a contradicted green that make_test_gate deleted can never be resurrected out of an older summary. The fingerprint sees file content only — a system-toolchain change (a typst upgrade, say; pyright and pytest are pinned through uv.lock, which is in the closure) is invisible to it. --force-make-test runs the gate regardless (as does `make test FORCE=1` inside the wrapper).

The verdict plumbing is guarded the same way, by rebuild/out/plumbing-green.json. Every step of it is a pure function of the surface, the verdicts master, the live store, the checked-in standing approvals, and its own code, so the key is (the surface's inputs fingerprint and stamp, the master's path and bytes, the autosave's bytes, standing-approvals' bytes, the chain's own import closure plus review/serve.py). Two of those components are there because a narrower key looked sufficient and was not. The master, because it is the one input the autosave's hash cannot see: an export dropped at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code, because every sibling key folds in its own stage's executable and this chain's lives in a tree no other fingerprint reads — without it a fix to a fill's matcher or the carry's ink fallback would be skipped as already proven, silently never running. That component is the named closure `plumbing_code_paths` rather than the whole of rebuild/tools/, and rebuild/test_plumbing_closure.py walks the entry points' import graph on every contracts run to prove the name still covers what runs.

The key is captured the moment the chain closes, not at the end of the pass, so a store write landing while the census runs cannot be absorbed into a fixpoint nothing verified; the record itself is written later, once complaints has also succeeded. And the fixpoint is claimed only when the chain has witnessed it. The steps feed forward — the carry's merge gives echo-fill new agreement to read, and echo-fill only removes blanks, so it can never hand standing-fill work it did not already have — but standing-fill runs last, and a standing fill can make an echo group unanimous while a blank sibling remains. Refusing the green whenever the standing merge moved anything would cost a whole extra pass to close the cascade. In one process another echo pass costs a second, so the chain runs the cascade to a standstill itself and the green rests on a re-run that demonstrably wrote nothing.

The skip demands that the surface build be skipping too, which is what makes the stamp knowable before the pass runs, and it takes the snapshot with it: the snapshot exists to survive this cycle's surface rewrite and to feed this cycle's carry, and a pass doing neither needs no copy. Such a pass also leaves the snapshot pile alone rather than pruning it to the copy it never made, so the stamp-aligned snapshot the last refreshing pass left stays on disk as the recovery source describe_carry_source points at. A flag that names a carry output or a snapshot directory refuses the skip outright, since honoring it would mean writing neither.

The same provably-unchanged principle guards every other heavy stage, each keyed by a content fingerprint over that stage's full input closure and a green record written only after that exact content passed live: run_m1 skips on rebuild/out/run-m1-green.json (the Stage A fingerprint components plus the contact allow-list, the oracle's subset tables and uv.lock) and re-evaluates its gate from the summary JSONs already on disk; gate:conform skips on conform-green.json, keyed on the sweep's own closure rather than on run_m1's — the spec and build-side code the tables' stamp covers, the engine's semantics tokens, the M1.otf bytes, uv.lock for the shaper, and the sweep horizon — so a comparison-side edit that leaves the font byte-identical leaves that key unmoved; each rebuild lane skips on its own record (rebuild-contracts-green.json, rebuild-validators-green.json), keyed by rebuild_lane_fingerprint over that lane's own closure — both hold the suite's repo closure under rebuild/ and glyph_data/ plus conftest.py, pyproject.toml, uv.lock and the site fonts, and validators adds the out/m1 artifacts and the baselines it shapes against, which is exactly why the contracts key holds no artifact and the contracts lane can skip whether or not run_m1 rebuilt: a live M1 rebuild writes only under rebuild/out, which that closure does not contain. The contracts record also carries a per-test input closure beside its key, so a pass whose contracts key moved runs only the tests whose closure the diff reaches, a rune edit re-proving the tests that load the spec and nothing else; rebuild.tools.contracts_closure is the authority on what a closure holds and when a test may be kept off, and every doubt there runs the test. Both records are also written by rebuild.tools.rebuild_gate, the `make test-rebuild` entry point, so interactive suite greens and cycle greens share them; surface-build skips when the manifest's recorded inputs fingerprint already equals the one a build would stamp now (a rebuild would be byte-identical, mtime-floored generated_at included, so the autosave stays aligned). The census step is neither keyed nor skipped: it reads the surface build's census-facts.json sidecar and rewrites one small checked-in file in milliseconds, so it simply runs every pass. The surface skip engages only on cycles where run_m1 itself skipped, and on the gates-only route when the Stage A record on disk is already what that pass will rewrite (`m1_stage_a_current`), since the surface reads nothing else the pass touches — which is the contact-allow bless, the one comparison-side edit outside every Stage A component. Conform's skip and the validators lane's are decided after run_m1 has finished instead, each over the key the artifacts it left actually carry: a route that leaves the font byte-identical skips the sweep, and one that leaves the out/m1 artifacts and the rest of the lane's closure unmoved skips the lane, whether run_m1 skipped, re-adjudicated, or rebuilt — so a contact-allow bless costs the gates-only re-adjudication and not the lane — and either skip is recorded as proved because a matching green is proof about this exact content. Taking those two keys only once run_m1 is over is also what keeps a live M1 rebuild from invalidating one mid-cycle: there is no key to invalidate until the artifacts have stopped moving. The preflight still answers both ahead of the pass on the one route whose artifacts it can already see — run_m1 skipped, so nothing is about to move — and that is the route --dry-run can predict; on the reuse and rebuild routes the printed plan shows the conform and validators lanes as undecided (`run?`), because only a finished run_m1 knows what the artifacts came out as, so a plan that promises either may be answered by a pass that proves it unnecessary. Green records are written only when the key still matches after the work ran, and a red result whose key matches its record deletes the record. --fresh runs everything regardless.

Between the run_m1 skip and a full rebuild there is a third route. When the per-file diff against the run_m1 green is confined to comparison-side inputs — the alias map, the divergence ledger, the contact allow-list, the kern sidecar, the oracle's own module, the baselines and their subsets, every one of them outside the tables' stamp (`comparison_side_label` is the roster and argues each member) — and the tables on disk still carry that stamp and the artifacts are all present, the cycle spawns `run_m1 --gates-only` instead of a build: the defect gate, the Manual-pin gate and the oracle re-run over the tables and font already there, the ledgers' verdicts are re-adjudicated, and nothing is enumerated. The green that pass records covers the new inputs, so the next cycle skips run_m1 outright. `uv.lock` is deliberately not comparison-side — a fontTools or uharfbuzz bump can move the font's bytes and what the shaper makes of them — so a toolchain bump still rebuilds.

Which passes cost the reviewer their letters is decided here rather than by the caller, because only the resolved plan knows. Two of the things a cycle writes belong to the running app — the surface it serves, where livereload watches every shard and a restamped manifest orphans the tab's store, and the verdict store, which merge_verdicts refuses to touch under a live server because an open tab would flush its own copy back over the merge. A pass whose plan skips both writes neither, so a listening server is left alone and the letters stay on screen for the whole run: that is the pass with no artifact work, whose long verification would otherwise black the app out for every minute of it. A pass whose surface did not move but whose store did takes a shape of its own: the carry there is provably the identity — the snapshot it would read is a clone of the same surface, every content key resolves to itself, and the carry preserves each record's `at`, which the merge compares strictly — so the snapshot and the carry are skipped and the master is merged straight in, which is the one thing the store's own hash cannot see. That pass still writes the store, so it is a port-taking one. An edit confined to rebuild/review/static/ has a shape of its own as well: the copied app assets are the one surface input no unit can feel, so instead of rebuilding, the pass copies them over the served copy and restamps that single fingerprint component (`assets-refresh`), which leaves every shard, both sidecars, the unit-cache store and `generated_at` exactly where they were — nothing under the app moves that the tab is keyed on, so the server stays up and livereload reloads it onto the new shell. A pass that does write under the app needs the port to itself, and --stop-server (which `make review-cycle` passes) is permission to take it — terminate the server and wait out the port — where a bare run still refuses and says how. Retention is the third writer: the app appends to the journal as you verdict, and a compaction rewrites the file around a read, so with a server up the journal and the stash sweep that indexes off it are both left for a later pass.

A green finish ends with a retention pass over the cycle's own disk piles, all of them regenerable or journal-covered: every tmp/review-pre-* snapshot except this cycle's is deleted (a snapshot is read once, by its own cycle's carry, and never again), root verdicts-carried-*.json files not stamped for the live surface are deleted (only the stamp-aligned frontier is ever read; the tracked copy under rebuild/evidence/ is never touched), verdicts-autosave-* stashes not referenced by a journal event at or after the last base event are deleted (the journal, not the stashes, is the sanctioned recovery path — and the reference index is the test because a stash's mtime predates the event that created it), and the journal itself is compacted to the newest base event older than RETENTION_WINDOW_DAYS, keeping at least that many days of --restore-as-of history. Failed, interrupted, first-run, and rehearsal cycles never prune; --keep-history opts out entirely; a retention error warns and never turns a green cycle red.

Run as: uv run python rebuild/tools/artifact_cycle.py — the carry source is auto-resolved from the autosave and the verdicts-*.json exports; pass --verdicts to name one explicitly.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import functools
import hashlib
import json
import os
import posixpath
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
from rebuild.review import app_index, unit_index  # noqa: E402
from rebuild.tools import console  # noqa: E402
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV, CheckVerdict  # noqa: E402
from rebuild.tools.peak_rss import reap_peak_rss_bytes  # noqa: E402
from rebuild.tools.review_server import REVIEW_PORT, server_listening  # noqa: E402

if TYPE_CHECKING:
    from rebuild.tools.cycle_timings import CycleTimings
REVIEW_OUT = ROOT / "rebuild" / "out" / "review"
AUTOSAVE = ROOT / "verdicts-autosave.json"
M1_OUT = ROOT / "rebuild" / "out" / "m1"
ECHO_FILL = ROOT / "verdicts-echo-fill.json"
STANDING_FILL = ROOT / "verdicts-standing-fill.json"
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

POOL_POLICIES = ("queue", "overlap")
REBUILD_POOL_POLICY_DEFAULT = "queue"
PLUMBING_SKIP_NOTE = "surface, verdicts master, live store, and standing approvals unchanged since the last complete plumbing pass; --fresh overrides"
CONFORM_SKIP_NOTE = "font and sweep inputs unchanged since its last green sweep; --fresh overrides"
CONFORM_MAYBE_NOTE = "runs unless run_m1 leaves the font and sweep inputs under the key of its last green sweep, in which case it is re-skipped after run_m1"
VALIDATORS_SKIP_NOTE = "input closure unchanged since its last green run; --fresh overrides"
VALIDATORS_MAYBE_NOTE = "submitted once the surface build settles, unless run_m1 leaves the out/m1 artifacts and the rest of the lane's closure under the key of its last green run, in which case it is re-skipped after run_m1"
UNDECIDED_UNTIL_RUN_M1 = {
    "gate:conform": CONFORM_MAYBE_NOTE,
    "gate:rebuild-validators": VALIDATORS_MAYBE_NOTE,
}
ASSETS_REFRESH_NOTE = "only the review UI assets moved since the surface was stamped; they are copied over the served copy and the manifest's static component restamped in place — no shard, sidecar or generated_at moves; --fresh overrides"
SERVER_STAYS_UP_NOTE = "rewrites no unit shard, moves no manifest stamp, and leaves the verdict store alone"
SERVER_STOP_PATTERN = r"rebuild\.review\.serve"
SERVER_STOP_TIMEOUT = 15.0
# The gate pool's seats, sized to the tasks the chain submits rather than to the box or to the work actually in flight: under the queue policy a parked task holds its worker for the whole wait — conform on make-test, contracts on both, validators on all three — so every gate task has to be seatable at once, with slack on top of that. A seat short of the task count would serialize a wait behind an unrelated task's completion, which is the queueing this pool exists not to do, and a width taken from the cores in hand would put a small box exactly there. `test_the_gate_pool_seats_every_gate_task_at_once` in rebuild/test_artifact_cycle.py is what holds this and the chain's task list in step.
_GATE_POOL_WORKERS = 7
# What one surface worker holds at its peak, and so the divisor a box divides itself by to reach this build's fan-out width. A worker is a persistent spawn process that enriches and drafts a contiguous slice of the corpus, spooling each fragment to disk as it is drafted so that no EnrichedUnit outlives its batch (rebuild/review/build.py's `_FragmentSpool`), so what it holds is its own interpreter and shapers (whose shape memo is released at every unit batch boundary, `rebuild.review.ink.release_shape_memos`, so it holds one batch's windows rather than the slice's — a bound `_MemoizedShaper`'s docstring records as measured rather than assumed, since unreleased the memo alone outgrew every other pile of a serial build), one baseline subset table for every configuration its slice reaches — a slice of three units in a rare configuration pulls in a whole one, and each table is rebuild/review/enrich.py's SubsetRow projection of the three fields the enricher reads, glyph and seam strings interned, rather than every parsed rowmodel.Row — the slice's spool addresses, and the slice's projections until its phase-1 reply is pickled, which is the one term left that scales with the slice: half the corpus at width two against an eighth at width eight. That scaling is why the figure is seeded at width two, the narrowest pool the arithmetic ever starts and so the widest a worker ever gets, so that the constant is an upper bound at every pooled width. The seed is the first width-two pool on the spooling tree, whose two workers read 4.54 and 4.89 GB at their peaks, and whose pile tally (`AMS_SURFACE_PILE_TALLY=1`, rebuild/tools/pile_tally.py) attributed each to its subset tables and its half-corpus of projections in roughly equal parts, with the spool addresses a small fraction of either; the pre-spooling seed of 16 GB priced a worker that retained its slice's EnrichedUnits from phase 1 until phase 2 and its fragments until the parent pulled them, and held full Rows in its tables, none of which a worker holds now. It rounds up past the wider of the two observed for the same reason kernel_exec.CONFIG_PEAK_BYTES rounds up past its own measurement, since a per-unit cost that errs low is what puts a box into swap while one that errs high only narrows a pool. `make job-costs`' surface-worker row is where it stays honest: rebuild/review/build.py files one kind:"pool" record per pooled build, so the constant is priced against the workers that actually ran — and a row that is quiet on a box is not a box this width has sent serial any more, since both machines in the fleet now start a pool. It is a reading to keep current, never a contract.
SURFACE_WORKER_BYTES = 5_500_000_000
# What the surface build's parent holds while that pool is live, and so what comes off the box before the division rather than into it: the parent holds the whole workload and every unit's state and projection, the ink-signature store, the unit store's records and located addresses on a served build or the spool addresses on a cold one, the content keys, and of the fragments only the one in hand — each is read back by address out of the build's fresh spool, or a served one out of the previous surface, as the shard that takes it is being written, patched there, and released once the shard, the checker and the sidecar spools have had it. It is flat in the width, which is exactly what makes it a co-resident term and not a divisor. The measurement is the `surface-build` step peak the cycle already stamps on every pass, which is honest for this term and only this one: peak_rss.reap_peak_rss_bytes maxes over the tree instead of summing it, and the widest single process under this step is the parent, the workers holding near-even slices of a corpus the parent holds whole. The seed is the levered tree of issue #155: the served builds of the first cycles on it read 7.63 GB and a cold width-two pooled build read 7.00 GB, where the fragment-shaped pile had reached 20.72 GB and the streaming-but-retaining one 17.10 GB on the same corpus, and it rounds up past the higher of them. The pile tally on that pooled build (rebuild/tools/pile_tally.py) attributes what remains to the per-unit states, the Units themselves, the signature store and the spool addresses, in that order — structural per-unit objects, none of which a further lever shrinks by an order — which is the reading issue #160's on-disk workload was sized against and closed as not needed on: the residual fits beside a pooled fan-out on the smaller box in the fleet. The pile is still corpus-shaped — every migrated letter moves it — so it stays a constant to re-seed as the alphabet migrates, off the surface-parent row of `make job-costs`, which holds a seed only against rows measured after the commit that set it, so the pre-streaming high-water marks still in the journal never read loud against this one. One more reading moved it after that: with every store record carrying its fragment's address (issue #169), a served build's step peak lands in the cache phase, after the pool has closed and the parent alone is writing the store — 9.24 GB on the same corpus against 6.68 GB while the pool was live — and the seed sits above it because this row reads the step peak; so it overstates the pool-live pile by a few hundred megabytes, which costs neither box in the fleet a worker, rather than reading loud on every served pass.
SURFACE_PARENT_BYTES = 9_500_000_000
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
# Where a pass keeps everything the terminal did not show, and how many such runs survive the green-finish retention pass. The root is a module constant so the rebuild suite can point it under a temp root — every other cycle write is redirected that way, and a run directory minted into the live repo by a test that drives main is the same kind of litter. Ten is a working week of passes: enough that a question about "the run before last" is still answerable, few enough that the pile stays a pile rather than an archive, and the whole of any one run is regenerable by running it again.
BUILD_LOGS_ROOT = ROOT / "tmp" / "build-logs"
BUILD_LOGS_KEEP = 10

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
    """One lane of the rebuild suite. `--lane` is the rebuild conftest's own option, and it also decides the pool width: the contracts lane's `-n auto` resolves to the cores this process may actually run on, since none of its workers holds a live build artifact, while the validators lane takes the narrower width `rebuild/conftest.py` derives from what one live-fixture worker costs. Every run prints its twenty-five slowest tests, so the lane's own record says where its minutes went and a cost survey needs no special invocation. The contracts lane also names the two closure files beside its green record: the selection file the caller writes just before the spawn, naming the tests the record proves unaffected, and the sidecar the suite writes at session end with every test's recorded closure — both resolved at call time off `rebuild_lane_green`, so a test that redirects the record redirects them with it."""
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
    """Whether a repo-relative path is provably outside gate:make-test's input closure, argued file by file from what the gate actually executes (make all, typst, pyright, pytest test/ site/). The exempt trees are safe because nothing it executes reads them: build_font globs glyph_data/*.yaml non-recursively (never glyph_data/runes/), and test/, site/, tools/ and conftest.py reference no rune file at all and reach into rebuild/ only where the root conftest imports three helpers inside its own callers — peak_rss for the summary line, memory_budget for the pool width, fingerprint for the font-path list — none of which can change what the suite asserts, and each of which the rebuild suite's own lanes gate; Markdown is never an input to any gate. .vscode/ is editor configuration, and its schema files reach no import: tools/quikscript_ir.py names them only inside the error messages it raises. .github/ is CI, which spawns the suite rather than being read by it. reference/csur/ and the reference PDFs and PNGs are human reference material nothing compiles, while reference/DepartureMono-Regular.otf stays inside the closure because tools/build_font.py bundles it and test/test_mono_matches_departure_mono.py shapes against it. site/icons/, site/*.svg, site/gear-menu.js and site/shared.css are browser assets the served pages load: the only Python that names them is tools/build_check_html.py, which emits them as string references and which `make check-html-after` alone runs — while site/shared.js stays because test/test_shared.py executes it under node, and site/print.typ stays because `make all` compiles it. The dotfile configs and the OFL text are read by git, the linters and the formatters, never by the suite. The name globs are anchored to their own directory, so site/*.svg never reaches a nested directory and reference/*.pdf never reaches reference/csur/. The Makefile is exempt as a file because the two rules the suite executes are keyed by their `make -n` expansion instead (see make_test_recipe_lines), so a comment or a cycle/kernel target cannot re-arm the gate while an edit to `all` or `test` — or one that stops the file parsing — still does."""
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
    """One hash line per rule the suite executes, standing in for the Makefile's bytes. What `make -n <target>` prints is the recipe the suite will run once every variable and function has expanded, so hashing that output keys the gate on the `all` and `test` rules and on nothing else in the file. stderr and the return code ride into the same digest, so a Makefile that has stopped parsing moves the key rather than silently hashing an empty recipe. The probe has to answer the same way whoever asked, and GNU make gives a caller's override two routes into this child: MAKEFLAGS and MFLAGS, which carry -j and the command-line assignments down to a sub-make — both the cycle's gate and `make test`'s own wrapper reach this as sub-makes — and so come off the environment first; and the plain exported variable, which stripping those flags cannot reach, since `make test FORCE=1` also puts FORCE=1 in the recipe's own environment. So every variable the executed rules read is pinned on the probe's own command line (MAKE_TEST_RECIPE_PINS), where a command-line assignment outranks both the environment and MAKEFLAGS. Otherwise a forced run would record a fingerprint no bare run ever matches — re-arming the whole suite on the very override that exists to run it once — and a forced red would leave standing a green record it had just contradicted. None when make is missing, which is the same run-unconditionally path git's absence takes; a nonzero exit is never None, because a failing make is content."""
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
    """Content hash of gate:make-test's input closure: every file make_test_exempt leaves in, read from the worktree (not the index) so uncommitted edits count, then the two executed Makefile rules as make_test_recipe_lines hashes them. A deleted-but-tracked file hashes as absent, so deletions move the fingerprint too. None when either half is unavailable — no git, or no make — in which case the caller must run the gate unconditionally."""
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


def record_green(
    path: Path, fingerprint: str, files: dict[str, str] | None = None, closures: dict | None = None
) -> None:
    """`files` is the per-file `label -> digest` map behind the fingerprint, when the caller has it: stored beside the key so a later skip miss can name exactly which input moved instead of reporting only that some digest did. `closures` is the contracts lane's per-test input closure over those same labels (`rebuild.tools.contracts_closure`), which is what lets a skip miss run only the tests the moved inputs can reach."""
    payload: dict = {"fingerprint": fingerprint}
    if files is not None:
        payload["files"] = files
    if closures is not None:
        payload["closures"] = closures
    _record_outcome(path, payload)


def clear_contradicted_green(path: Path, fingerprint: str | None) -> None:
    """A red result over content whose fingerprint still matches the recorded green contradicts the record; delete it so no later cycle can skip on a falsified green."""
    record = read_green_record(path)
    if fingerprint is not None and record is not None and record["fingerprint"] == fingerprint:
        path.unlink(missing_ok=True)


def record_plumbing_green(fingerprint: str, path: Path | None = None) -> None:
    """The verdict plumbing's last-green record: the key alone, like every sibling record read_green_record parses. A pass that wants to name the live frontier derives it from disk (frontier_carry_out) instead of reading a remembered copy here, which a later export could silently outrank."""
    record_green(path if path is not None else PLUMBING_GREEN, fingerprint)


def frontier_carry_out() -> Path | None:
    """The stamp-aligned frontier file a summary can name for a pass that wrote no carry of its own, derived from disk the same way every consumer derives it (status.pick_frontier) rather than remembered in a green record a later export could outrank."""
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
    """The shared last-green record for `make test`, written by rebuild.tools.make_test_gate on every green run — interactive or as gate:make-test."""
    return read_green_record(path if path is not None else MAKE_TEST_GREEN)


def record_make_test_green(fingerprint: str, path: Path | None = None) -> None:
    record_green(path if path is not None else MAKE_TEST_GREEN, fingerprint)


def prior_make_test_fingerprint(green_path: Path | None = None) -> str | None:
    """The closure fingerprint of the last green `make test` run, from the shared green record alone: every green run rewrites it and clear_contradicted_green deletes it, so any copy elsewhere (the cycle summary keeps one for display) could only resurrect a fingerprint whose last observed run was red."""
    record = read_make_test_green(green_path)
    return record["fingerprint"] if record is not None else None


M1_ARTIFACT_NAMES = ("M1.otf", "divergence-audit.tsv", "inputs_fingerprint.json")
REBUILD_GATE_EXEMPT_PREFIXES = (
    "rebuild/evidence/",
    "rebuild/review/jstests/",
    "rebuild/review-census-pins.json",
    "rebuild/m1-contact-allow.yaml",
)
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
VALIDATORS_EXEMPT_PREFIXES = (
    "rebuild/fixtures/",
    "rebuild/review/fixtures/units/",
    "rebuild/review/static/",
)


def _sha256_path(path: Path) -> str:
    """The streamed read matters here more than anywhere: divergence-audit.tsv is hundreds of megabytes and rides the validators-lane key, which a driver pass recomputes three times. Spelled out rather than borrowing fingerprint.file_sha256 because this module defers every rebuild.pipeline import into the function that needs it, and this one is called per file in a loop."""
    try:
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return "absent"


def _closure_digest(root: Path, rel: str) -> str:
    """Rune YAMLs, the divergence ledger and the standing approvals hash by their prose-blind digests (`fingerprint.rune_file_digest`, `divergence_ledger_digest`, `standing_approvals_digest`) so a documentation edit does not re-run the gate. What keeps the exclusion sound is what the lanes actually read out of those files: the contracts tests that load the live runes assert structure, settlement outcomes and round-trip identity, and the ones that load the live ledgers take ids, `no_verdict`, `match` and the exemplar keys — never a `why` or a `note`, whose only live readers are the surface's explain panel and the standing fill. Both of those are keyed elsewhere: the ledger's `why` on the Stage B `explain_prose` component, and the fill's quoting of a rule's `note` on `plumbing_skip_fingerprint`, which stays raw for exactly that reason."""
    from rebuild.pipeline import fingerprint

    prose_blind = {
        fingerprint.DIVERGENCE_LEDGER_LABEL: fingerprint.divergence_ledger_digest,
        fingerprint.STANDING_APPROVALS_LABEL: fingerprint.standing_approvals_digest,
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


def _digest_lines(lines: list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()


def _subset_tables(root: Path) -> list[Path]:
    return sorted((root / "rebuild" / "out" / "m1").glob("baseline-*.subset.tsv.gz"))


def run_m1_skip_lines(root: Path = ROOT) -> list[str]:
    """The per-file `label\\tdigest` lines behind `run_m1_skip_fingerprint`: every data input and pipeline module individually (rune files and the divergence ledger prose-blind), the contact allow-list by its own prose-blind digest, the full baselines as one value, the oracle's subset tables, and uv.lock. Stored in the green record so a skip miss can name exactly which input moved — and so this pass can ask whether every label that moved is comparison-side (`comparison_side_label`) and re-adjudicate over the artifacts on disk instead of rebuilding them.

    The allow-list is here and in no fingerprint component at all: the defect gate is the only stage that reads it, so a bless has to move this key and has no business moving the surface's stamp or dropping the unit cache. A missing allow-list contributes no line, the way `path_lines` drops a missing file.
    """
    from rebuild.pipeline import fingerprint

    lines = fingerprint.data_lines(root)
    allow = root / fingerprint.CONTACT_ALLOW_LABEL
    if allow.is_file():
        lines.append(f"{fingerprint.CONTACT_ALLOW_LABEL}\t{fingerprint.contact_allow_digest(allow)}")
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
    """Content key over everything a full run_m1 reads: the data inputs and pipeline code per file, the contact allow-list the defect gate reads, the full baselines, the oracle's subset tables — which the `baselines` line covers only by proxy — and uv.lock for the pinned toolchain. Matching the recorded green means a rerun would reproduce rebuild/out/m1 byte for byte. Missing it says only that something moved; which lines moved is what decides whether the remedy is a rebuild or a re-adjudication (`gates_only_reuse`)."""
    return _digest_lines(run_m1_skip_lines(root))


def capped_labels(entries: list[str], limit: int = 8) -> str:
    """A label list said out loud for one line of a report, with a tail past `limit` counted instead of printed. One spelling of the cap, because a note that reports the moved inputs and a note that reports the reused ones are the same sentence with a different verb in front of it."""
    shown = ", ".join(entries[:limit])
    return f"{shown} and {len(entries) - limit} more" if len(entries) > limit else shown


def _moved_inputs(record: dict | None, current: dict[str, str]) -> list[tuple[str, str]] | None:
    """Every input that moved since a green record that stored its per-file lines, as `(label, how)` pairs — the changed ones, then the new, then the gone, each group sorted. None when the record is absent, predates the `files` payload, or (fingerprint notwithstanding) no stored line actually differs. The two public readers want different halves of this: the note wants how each one moved, the reuse predicate wants the bare labels, and neither may recompute the diff for itself."""
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
    """The bare labels of the inputs that moved since a green record, in the order `_moved_inputs` groups them. Bare because what reads this matches each label against `comparison_side_label`, and a label carrying a `(changed)` suffix would silently match nothing at all. None when there is no record to compare against or nothing differs."""
    moved = _moved_inputs(record, current)
    return None if moved is None else [name for name, _how in moved]


def comparison_side_label(label: str) -> bool:
    """Whether one `run_m1_skip_lines` label names an input the comparison reads and the build does not — the roster that lets a cycle re-adjudicate over the tables and font on disk (`run_m1 --gates-only`) instead of paying a kernel fan-out for artifacts that would come back byte-identical. Four kinds qualify, and what they share is that `fingerprint.tables_value` covers none of them, so an enumeration on disk stays exactly as fresh as it was. The ledgers and the kern sidecar (`fingerprint.NON_TABLE_DATA_LABELS`) are read to name, classify and position divergences over rows the fixpoint has already decided. The contact allow-list (`fingerprint.CONTACT_ALLOW_LABEL`) is the defect gate's, and the defect gate reads minted glyphs rather than making any. The oracle's own module (`fingerprint.COMPARISON_CODE_MODULES`) is the classifier those files feed, and rebuild/test_build_code_closure.py is what holds the build to never importing it. The `baselines` line and the `baseline-<config>.subset.tsv.gz` lines are the before side of the comparison, which no table stage and no emitter reads.

    `uv.lock` is deliberately not on the roster, though the tables' stamp misses it too: it pins fontTools and uharfbuzz, so a bump there can move the compiled font's bytes and what HarfBuzz makes of them, and standing on a font a different toolchain built is the one reuse this must never license.
    """
    from rebuild.pipeline import fingerprint

    if label in fingerprint.NON_TABLE_DATA_LABELS or label == fingerprint.CONTACT_ALLOW_LABEL:
        return True
    if label == "baselines" or (label.startswith("baseline-") and label.endswith(".subset.tsv.gz")):
        return True
    return label in {f"rebuild/pipeline/{name}" for name in fingerprint.COMPARISON_CODE_MODULES}


def gates_only_reuse(record: dict | None, current: dict[str, str]) -> list[str] | None:
    """The labels that moved since the last green M1 build when every one of them is comparison-side; None otherwise. This is the licensing predicate for the gates-only route, shared by the cycle that plans it and by the pass itself when it decides whether it may record a green. None covers three situations on purpose and all three mean "no reuse": there is no green record to stand on, nothing moved at all (which is the plain skip's case, not this one), or something build-side moved and the tables have to be rebuilt.

    What makes the reuse sound is the pair of checks, not this one alone: the prior green is the proof that the artifacts on disk came from a completed build over every build-side input, and `m1_tables_stamped` is the proof that none of those inputs has moved since. Both are asked before the route is taken, and the green a gates-only pass records is recorded on the same pair.
    """
    moved = moved_input_labels(record, current)
    if moved is None:
        return None
    return moved if all(comparison_side_label(label) for label in moved) else None


def moved_inputs_note(record: dict | None, current: dict[str, str], limit: int = 8) -> str | None:
    """Which inputs moved since a green record that stored its per-file lines, said out loud — the skip-miss diagnostic. None on the same three conditions `_moved_inputs` returns None on."""
    moved = _moved_inputs(record, current)
    if moved is None:
        return None
    return capped_labels([f"{name} ({how})" for name, how in moved], limit)


def oracle_cache_note(moved: str | None, root: Path = ROOT) -> str | None:
    """What the inputs a run_m1 skip-miss just named will cost the oracle's per-row verdict store, which is the other thing that note is predicting. The store invalidates at three grains and they look nothing alike in the timings: a rune file moves one family key and re-derives only the rows that can reach that letter; anything in the comparison's own code closure — or any other input the whole-store stamp folds — drops every row of every configuration; and an input only the position stamp folds (the oracle's own module, the kern sidecar, the toolchain lock) keeps every row verdict and re-shapes every position. Naming the second and third is the point, because a zero-served oracle after a legitimate class-membership or pipeline edit is the expected outcome and would otherwise read as a broken cache. Both sides of the comparison are repo-relative labels, the form `moved_inputs_note` reports in: matched against basenames this answers nothing at all, and answers it silently. The rune verdict is withheld when the note was truncated, since the inputs it did not list could be anything."""
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
    """Whether rebuild/out/m1 still holds everything a skipped run_m1 must leave behind: the three gate summaries and the artifacts the surface build consumes."""
    m1 = root / "rebuild" / "out" / "m1"
    names = [path.name for path in M1_SUMMARY_FILES.values()] + list(M1_ARTIFACT_NAMES)
    return all((m1 / name).exists() for name in names)


def m1_tables_stamped() -> bool:
    """Whether the serialized window enumerations under rebuild/out/m1 were produced from exactly the sources on disk — `run_m1.serialized_tables` against `run_m1.tables_inputs`, the same stamp the sweep and a gates-only pass refuse on. Artifact identity, never a receipt of a past run: it is what says the M1.otf beside those tables is the font the runes on disk describe, which is the second half of what licenses the gates-only route (`gates_only_reuse` is the first). No root parameter, like `deep_sweep.tables_stamped`: the stamp is cut over the live repo, so a caller naming another tree would compare that tree's tables against this one's sources."""
    from rebuild.pipeline import run_m1

    return run_m1.serialized_tables(run_m1.OUT_DIR, run_m1.tables_inputs()) is not None


def m1_stage_a_current(root: Path = ROOT) -> bool:
    """Whether the Stage A record run_m1 left under rebuild/out/m1 is already what a pass over the sources on disk would write. On the run_m1 skip route this is true by construction; on the gates-only route it is the question that decides whether the surface can skip ahead of the pass, because that pass rewrites the record from these same sources and the surface build reads nothing else the pass writes — the audit and the subset tables can only move when a Stage A component does."""
    from rebuild.pipeline import fingerprint

    recorded = fingerprint.read_stage_a(root / "rebuild" / "out" / "m1")
    return recorded is not None and recorded == fingerprint.stage_a(root)


def conform_skip_lines(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> list[str]:
    from rebuild.pipeline import fingerprint, kernel_exec

    lines = fingerprint.table_data_lines(root)
    lines += fingerprint.path_lines(root, fingerprint.table_code_paths(root))
    lines.append("semantics\t" + "+".join(kernel_exec.enumeration_tokens()))
    lines.append(f"M1.otf\t{_sha256_path(root / 'rebuild' / 'out' / 'm1' / 'M1.otf')}")
    lines.append(f"uv.lock\t{_sha256_path(root / 'uv.lock')}")
    lines.append(f"horizon\t{horizon}")
    return lines


def conform_skip_files(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> dict[str, str]:
    return _files_of(conform_skip_lines(root, horizon))


def conform_skip_fingerprint(root: Path = ROOT, horizon: int = CONFORM_HORIZON_DEFAULT) -> str:
    """Content key over exactly the sweep's own closure: the spec and build-side code the tables' stamp covers (`fingerprint.table_data_lines` and `table_code_paths`), the engine's semantics tokens, the compiled font's bytes, the pinned toolchain, and the horizon. The sweep shapes M1.otf through HarfBuzz and re-settles every swept window through the crate, so those are the five things that can change its answer. The horizon is in the key so a green at a shallower horizon can never satisfy a deeper gate, and `uv.lock` is in it because uharfbuzz is what does the shaping.

    Deliberately narrower than the run_m1 key: the ledgers, the allow list, the kern sidecar, the baselines, the subset tables and the oracle's module are all outside it, because the sweep reads none of them. So a comparison-side edit that leaves the font byte-identical leaves this key unmoved, and the gate skips on the green the same font already earned — whether run_m1 skipped, re-adjudicated, or rebuilt.
    """
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
    """Every tracked or untracked-unignored file the rebuild pytest suite can read from the repo, and the shared half of both lanes' input closures: rebuild/ and glyph_data/ (minus Markdown and the exempt paths in REBUILD_GATE_EXEMPT_PREFIXES: the carried-verdict evidence, the JS-only jstests, the census pins, and the contact allow-list), the root conftest.py, pyproject.toml and uv.lock, and the harness roster REBUILD_GATE_HARNESS_PATHS, which is what the suite reads outside those trees and why the Markdown filter has an exception: that filter is there for prose no test opens, and doc/glyph-names.md is a fixture rebuild/test_review_enrich.py holds the surface's letter table against. The roster is measured rather than inferred from the tree — issue #127 audited every file both lanes actually open over a green run of each — and every entry has a named reader: rebuild/validation/pins.py collects its pin runs from the three site corpora and puts test/ on sys.path to import test/test_shaping.py, which reads postscript_glyph_names.yaml at the repo root and pulls the tools/ compile modules in with it; rebuild/review/drafts.build_corpus_index reads the same three corpora; the unit-cache environment stamp hashes tools/*.py whole, which is why the roster is the tree rather than the compile modules alone; and rebuild/test_review_build.py pins the review surface's feature descriptions to README.md's stylistic-set list. Until they were keyed, editing any of them left both lanes' greens standing over code and data the suite had just been reading. The pins are out because the suite no longer reads them and the census step rewrites them mid-pass — they are the cycle's own diff artifact, so leaving them in would invalidate the key of every pass that refreshes them. The allow-list is out because no test in either lane reads the live file — only a fake repo writes one — so a bless would re-run the whole suite to prove nothing. The other two human-reviewed ledgers stay in, since tests in both lanes do read them, and buy the same saving a different way: `_closure_digest` hashes them prose-blind, so rewording a `why` or a `note` moves neither key while a structural edit still moves both. None when git is unavailable, in which case the caller must run the gate unconditionally."""
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
        and not any(path.startswith(prefix) for prefix in REBUILD_GATE_EXEMPT_PREFIXES)
    )


def rebuild_lane_fingerprint(root: Path, lane: str) -> str | None:
    """Content key over one lane's full input closure: `rebuild_lane_closure`'s digest, whose per-label lines are the authority on what the key covers."""
    return rebuild_lane_closure(root, lane)[0]


def rebuild_lane_closure(root: Path, lane: str) -> tuple[str | None, dict[str, str] | None]:
    """One lane's input closure as the key and the per-label digest map the key is a digest of — one pass over the files answers both, and keeping the map the key's only source is what makes the contracts lane's per-test selection sound: every input the key can move on is a label the selection can see move. Content key over one lane's full input closure, and the two closures are what make the lanes separately skippable. Contracts covers the repo files from rebuild_gate_closure_files, which has already dropped the exempt paths, so a bless of a contact signature moves neither lane's key — plus the site fonts, which are `make all` output its shaping tests measure against, moved by no rune edit, and whose Senior sha every baseline header records and `baseline_subset.prove_font_provenance` holds the oracle's tables to; it deliberately contains no build artifact at all, so a verdict-only or artifact-only cycle re-runs nothing here, and a live M1 rebuild — which writes only under rebuild/out — cannot invalidate the key mid-pass. Validators adds exactly what that lane reads on top: the out/m1 artifacts, the oracle's subset tables, and the baselines. It drops three trees more than the shared exemptions, too, each of them checked-in source no arm of that lane opens: rebuild/review/static/, the copied app shell — the only tests that read the shell (the index-html sanity check and the `node --check` pass in rebuild/test_review_build.py) read it at its source and sit in the contracts lane, whose closure keeps it — and the fixture piles rebuild/fixtures/ and rebuild/review/fixtures/units/, whose readers sit in contracts as well. The rest of rebuild/review/fixtures/ stays in both keys, because rebuild/conftest.py imports the mini bundle's pin module at module scope, so every process of either lane reads it. The harness roster rides both lanes for the same reason the audit found it: collecting rebuild/ imports test/test_shaping.py in every process of both, and the tools/ modules come with it. Only the corpora and the two prose fixtures are over-inclusive there, their readers all sitting in contracts, and they are too few and too rarely edited to be worth a second exemption list. Both contain the rune files and both human-reviewed ledgers that are still in the closure — the divergence ledger and the standing approvals — all three prose-blind, because several contracts tests load the live spec and the live ledgers, and every one of them reads structure rather than prose. The verdict store is absent from both — the suite exercises it only through fixtures — which is what lets a verdict-only cycle skip the suite entirely. None when git is unavailable, in which case the caller must run the lane unconditionally."""
    from rebuild.pipeline import fingerprint

    files = rebuild_gate_closure_files(root)
    if files is None:
        return None, None
    if lane == "validators":
        files = [
            rel for rel in files if not any(rel.startswith(prefix) for prefix in VALIDATORS_EXEMPT_PREFIXES)
        ]
    labels = {rel: _closure_digest(root, rel) for rel in files}
    labels["fonts"] = fingerprint.hash_paths(root, fingerprint.font_paths(root))
    if lane == "validators":
        m1 = root / "rebuild" / "out" / "m1"
        labels.update({f"m1/{name}": _sha256_path(m1 / name) for name in M1_ARTIFACT_NAMES})
        labels.update({f"m1/{path.name}": _sha256_path(path) for path in _subset_tables(root)})
        labels["baselines"] = fingerprint.baselines_value(root)
    return _digest_lines([f"{label}\t{digest}" for label, digest in labels.items()]), labels


def surface_build_skippable(
    root: Path = ROOT, review_out: Path | None = None, ignore: tuple[str, ...] = ()
) -> bool:
    """Whether rebuilding the review surface would reproduce its content byte for byte, so the build can be skipped with the autosave still aligned. True only when the manifest's recorded inputs fingerprint equals the one a build would stamp now (Stage A as recorded by run_m1, Stage B recomputed) and every shard the manifest names is still present. generated_at is mtime-derived, so a rebuild after pure mtime churn (git checkout, touch) could restamp it even with identical content — skipping deliberately keeps the existing stamp instead, which preserves the manifest-autosave alignment the stamp exists to key.

    The three files the manifest does not name are checked too — the per-unit index and both app sidecars — and each has to be stamped for the manifest beside it rather than merely present. They are written after the manifest and outside it, so a build interrupted between the two, or a manifest rewritten by anything that does not rewrite them, leaves a surface whose shards are all there and whose sidecars address a surface that no longer exists. Skipping on shard existence alone would then serve that surface forever; asking `unit_index.index_is_current` and `app_index.artifact_is_current` makes the skip say what it means, which is that a rebuild would reproduce this surface's content whole.

    The after font is held against the file on disk for a reason no fingerprint component can cover: the key hashes the font's inputs and the two site fonts, never rebuild/out/m1/M1.otf itself, so a run_m1 that landed since this surface was built moves nothing the comparison above can see while the letters the surface ships are last build's. The build asserts at copy time that the font it ships is the font it hashed at load, so the manifest's recorded after-font sha is a true statement about the bytes under fonts/after.otf — and comparing that sha with M1.otf as it stands now is what says the skip is not stepping over a newer font.

    `ignore` names fingerprint components exempted from the comparison, for a caller asking a narrower question than byte identity — the same hard/warn split status._freshness_check draws. The cycle asks both questions in turn rather than one: the strict one first, since a surface that reproduces byte for byte needs nothing done to it at all; and when only an ASSET_COMPONENTS member differs, it copies those assets over the served surface and restamps that one component (`assets-refresh`) instead of rebuilding units that cannot have moved. A component missing from either side still refuses: only a recorded-and-expected pair is ever waved through.
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


# The chain's own code, named module by module rather than as the whole of rebuild/tools/: the closure of rebuild.tools.verdict_chain, which runs every step, held to the walked import graph by rebuild/test_plumbing_closure.py on every contracts run. This driver is not an entry point, because every argv it hands the chain names an input the key already hashes — the surface, a snapshot of that same surface, the master, the store — or a flag that disables the skip outright, and the chain's own flag parsing lives in verdict_chain; the two width yardsticks the pipeline takes from this tree (memory_budget and peak_rss, reached only through kernel_exec) are the pipeline_code component's coverage question, which the key carries whole through its manifest line, so the walk stops at that component's boundary rather than dragging a fan-out width and a cost reading into a verdict's closure.
PLUMBING_ENTRY_POINTS = ("rebuild.tools.verdict_chain",)
PLUMBING_TOOL_MODULES = (
    "carry_verdicts",
    "complaint_docket",
    "console",
    "echo_verdicts",
    "merge_verdicts",
    "review_docket",
    "review_server",
    "standing_verdicts",
    "verdict_chain",
    "verdict_notes",
)


def plumbing_code_paths(root: Path = ROOT) -> list[Path]:
    return [Path(root) / "rebuild" / "tools" / f"{name}.py" for name in PLUMBING_TOOL_MODULES]


def plumbing_skip_fingerprint(
    root: Path = ROOT, surface: Path | None = None, master: Path | None = None
) -> str | None:
    """Content key over everything the verdict plumbing reads: the surface it resolves unit ids against, the verdicts master it carries forward, the live store it merges into, the checked-in standing approvals, and the chain's own code. The standing approvals ride this key by raw bytes, alone among the files the rebuild lanes now hash prose-blind: `standing_verdicts` quotes each rule's `note` verbatim into the verdict note of every fill it writes, so a reword changes what the chain writes and has to re-run it. Carry, merge, both fills with their merges, and the complaint docket are pure functions of exactly those, and the chain is idempotent once it has run — so a key matching the record a *complete* chain left behind proves re-running it would write nothing new. The master is in the key because it is the one input the autosave's hash cannot see: an export dropped at the repo root can outrank the autosave in the auto-resolution and carry verdicts the store has never held. The code is in it for the same reason every sibling key carries its own stage's executable — a fix to a fill's matcher or to the carry's fallback must run rather than be skipped as proven — and it is the chain's real import closure (`plumbing_code_paths`, which a contracts test holds against the chain's import graph) plus the review/ modules the chain runs that the surface build does not — serve.py, which merge_verdicts reads the store through, and status.py and journal.py, which the merge and the readiness check run; review/'s build-side modules ride inside the manifest fingerprint's review_code. The manifest line drops `unit_index.ASSET_COMPONENTS`, because no step of the chain reads the copied app shell — and an assets refresh rewrites exactly that field, which must not re-run a chain every one of whose real inputs is unmoved. None when the surface has no fingerprinted manifest or no master was resolved."""
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
        f"status\t{_sha256_path(root / 'rebuild' / 'review' / 'status.py')}",
        f"journal\t{_sha256_path(root / 'rebuild' / 'review' / 'journal.py')}",
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


def evaluate_run_m1_gate(pipeline: dict, manual_pins: dict, oracle: dict) -> CheckVerdict:
    """Decide whether the M1 build passed from its three summary JSONs: defect_errors, the Manual-pin verdict, and multi_matched. UNMATCHED oracle rows are never a failure — they are the mid-migration steady state, verdict-gated on the review surface. UNMATCHED and multi_matched no longer ride out of here as informational fields: both callers hold the oracle summary this read them from and take them straight off it, so the verdict answers for the judgment alone rather than doubling as a courier for two numbers its caller already has. The pin verdict is run_m1's own (`manual_pin_gate_failure`), scope included, so a gate that replayed nothing cannot pass here either."""
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
    """Judge gate:conform from conform_summary.json's contents (None = the subprocess never wrote one). `pass` is the verdict, and the belt has exactly one way to fail: a font-vs-settle divergence, which is a compiler defect by definition. Whether the font holds every rule the build planned is read-back's claim, re-proved inside run_m1 on every build, and dead generated rules are rebuild/test_rule_witnesses.py's — neither reaches this summary. The sweep fails as a belt rather than as a list of named cases, so there are no failed ids to carry: what a divergence names is a window, and the audit beside the summary is where those are read."""
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
    argv = ["uv", "run", "python", "-m", "rebuild.pipeline.run_m1", "--conform-only"]
    if jobs > 1:
        argv += ["--jobs", str(jobs)]
    if horizon != CONFORM_HORIZON_DEFAULT:
        argv += ["--conform-horizon", str(horizon)]
    return argv


STEP_DESCRIPTIONS = {
    "snapshot": "Copies the review surface that is currently served to tmp/review-pre-<sha> before anything overwrites it. The carry reads this copy to bring your verdicts forward onto the new surface.",
    "run_m1": "Builds the M1 tables for every acceptance configuration in the Rust kernel, mints the glyphs, emits GSUB and GPOS, compiles the font, and reads it back. Then runs the defect gates, the Manual-pin gate, and the oracle over what it built.",
    "run_m1:gates-only": "Re-adjudicates the tables and font already on disk with the defect gates, the Manual-pin gate, and the oracle, rebuilding nothing. Taken when only comparison-side inputs moved since the last green build.",
    "surface-build": "Rebuilds the review surface: every unit the tables reach is drafted, enriched, and checked, with cache-served units re-verified by content key. Writes the shards, manifest, and census sidecar that the app and the verdict plumbing read.",
    "assets-refresh": "Overwrites the served copy of the review app's JS, CSS, and HTML and restamps only the manifest's static component. No shard or sidecar moves, so the open tab's store stays aligned.",
    "plumbing": "Carries the previous surface's verdicts onto the new one, merges them into the store, and runs the echo and standing fills to their fixpoint. Ends by writing the complaint docket of what still needs a human.",
    "census": "Rewrites rebuild/review-census-pins.json from the census sidecar the surface build emitted and prints its git diff in full. Committing that diff is how the census is accepted.",
    "gates": "The five post-build gates, skipped together under --skip-gates.",
    "gate:js": "Runs the review app's node test suite over its JavaScript. Fast, and independent of every build artifact.",
    "gate:conform": "Shapes the compiled font with HarfBuzz over the swept texts and checks it against a fresh re-settlement window by window, the split-buffer check at horizon 4 included. The end-to-end proof that the font does what the tables say.",
    "gate:rebuild-contracts": "Runs the rebuild suite's contracts lane: every test whose subject is the code, over checked-in fixtures and the hermetic mini bundle. Reads no live build artifact.",
    "gate:rebuild-validators": "Runs the rebuild suite's validators lane: the per-configuration rule-witness arms over the tables this pass built. Refuses a window enumeration stamped from other sources than the ones on disk.",
    "gate:make-test": "Runs the main font suite and pyright over the whole tree, the same make test you run by hand. Skips when its input closure is unchanged since its last green run.",
    "job-costs": "Checks the recorded per-worker peaks against the memory-budget constants that size every fan-out. A drift here means a width somewhere is priced on stale numbers.",
    "retention": "Prunes the regenerable piles a green cycle leaves behind: old snapshots, stale carried files, stashes the journal already replays, the journal past its 7-day floor, and build logs beyond the last 10.",
}


def step_description(name: str) -> str:
    """What a step's banner says that step is for, out of the table above: two sentences per step, printed on every run. They are keyed by step name and live beside the step definitions rather than in a document, because the one moment a reader wants to know what gate:conform proves is the moment they are watching it run, and a description that has to be looked up somewhere else is one nobody looks up. Two of those keys name variants rather than steps of their own — `run_m1:gates-only` is the re-adjudication route, which spawns under that name and reports under run_m1's row, and `gates` is the placeholder --skip-gates leaves in place of five. `_run_step` reaches this rather than the plan because it is handed a name and not a plan, and because two of the things it spawns are children of steps rather than steps — the census diff and the job-costs diff — for which the empty answer is the right one."""
    return STEP_DESCRIPTIONS.get(name, "")


SUBSTEP_PARENTS = {"git-diff": "census", "job-costs-diff": "job-costs"}


@dataclass
class Step:
    """One row of the plan. `skipped` is stated rather than read off `argv`, because the two answer different questions: `argv is None` also describes the snapshot and the retention pass, which do real work in this process, and the `gates` placeholder, which stands in for five steps at once. The run/skip column and the counts line derive from `skipped`, so a step that runs without spawning anything still reads as one that will run.

    Two spawns are not rows here at all: the census's git diff and the job-costs diff are children of steps rather than steps of the plan, and SUBSTEP_PARENTS above is what names them. Registering one with the digest files its output in the parent's log and surfaces its lines under the parent's column, and keeps `_run_step` from opening a second banner for a step that is already open.
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
    reuse_run_m1: bool = False
    run_m1_note: str = ""
    run_m1_fingerprint: str | None = None
    fresh: bool = False
    skip_surface: bool = False
    refresh_assets: bool = False
    surface_note: str = ""
    skip_contracts: bool = False
    contracts_note: str = ""
    contracts_skip: list[str] = field(default_factory=list)
    contracts_files: dict[str, str] | None = None
    skip_validators: bool = False
    validators_note: str = ""
    conform_note: str = ""
    conform_proven: bool = False
    skip_plumbing: bool = False
    plumbing_note: str = ""
    plumbing_store_only: bool = False
    takes_snapshot: bool = False
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
        """What the named step's banner says, for the two steps that run in this process and so never reach `_run_step`."""
        step = self.step(name)
        return "" if step is None else step.describe

    def note_for(self, name: str) -> str:
        step = self.step(name)
        return "" if step is None else step.note

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
    """The kernel fan-out's width for this cycle, named by the cycle rather than inherited silently, because it is the one width here that memory binds: a live configuration holds its whole working set until it emits, so the width is the box divided by one of them. What makes it the cycle's own rather than a re-export of `kernel_exec.KERNEL_THREADS_DEFAULT` is that a cycle is not a box to itself — gate:make-test's pytest pool is hot from t=0 and stays hot right across the table build — so that pool comes off the box before the division: FONT_SUITE_WORKER_BYTES apiece for as many workers as `make_test_pool_width` says it will have, which is the same figure the cycle hands the child, so what is reserved and what runs are one number by construction rather than two that happen to agree. A pass whose gate is skipped subtracts nothing, there being no pool to subtract, and --skip-gates is that same case with the caller saying so.

    The arithmetic underneath stays `kernel_exec.kernel_threads_default`'s: the reserve policy applied exactly once, and AMS_KERNEL_THREADS short-circuiting ahead of all of it, so a stated width wins here exactly as it does for a bare run_m1 and this reservation can never narrow one. What comes back is the memory answer before the configuration count and the cores this process may actually run on narrow it. That narrowing lives in exactly one place, `run_m1.build_tables`'s own `min()`, and is deliberately not repeated here: a second copy on this side would be a second thing to keep in agreement with it, and what not having one costs is only that a box roomier than the configuration count reads a plan line naming a width the run will go on to narrow. `ncores` and `total_bytes` are keywords for the reason every budget here takes its box as one — an assertion about a machine the suite is not running on has to be a pure function over an invented one.
    """
    from rebuild.pipeline.kernel_exec import kernel_threads_default

    coresident = 0 if skip_make_test else _font_suite_worker_bytes() * make_test_pool_width(ncores=ncores)
    return kernel_threads_default(coresident_bytes=coresident, total_bytes=total_bytes)


def sweep_job_budget(ncores: int | None = None) -> int:
    """The --jobs budget for the post-build sweeps — run_m1's Manual-pin/oracle shards and gate:conform's belt — which is one process per acceptance configuration and no more, because that is all `run_m1._spawn_pool` will start. This is a CPU budget, not a memory one: a sweep worker holds its shaper, its window memo, one config's rows and, for the oracle, one config's row store as a decompressed blob with two small age arrays beside it, a fraction of a gigabyte in all, so a whole `ACCEPTANCE_CONFIGS`-wide pool of them fits beside anything else the cycle runs. run_m1's memory ceiling lives entirely in the table build, whose width is --kernel-threads and which these jobs never reach."""
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
    """The --jobs budget for the review-surface build, and the third memory-derived fan-out in this tree: the box less its reserve less what the build holds flat, divided by what one more worker costs, under a cap that is the box's cores and SURFACE_JOBS_CAP together. The two halves are separate constants because this build's flat half is of the same order as its divided half — SURFACE_PARENT_BYTES is the parent, which holds the whole workload and every projection and state, plus the one fragment in hand (the fragments are read back out of the spools by address and stream through it into the shards rather than piling up in it until manifest+check), and which is there at any width — so it is subtracted before the division exactly as gate:make-test's pool is, rather than smeared through a divisor. SURFACE_WORKER_BYTES is the divisor: a worker's own interpreter and shapers, a baseline subset table for every configuration its slice reaches, and the slice's projections until they are pickled back. The same number also sizes the signature pool `_resolve_signature_digests` starts, whose workers are one comparator apiece and an order of magnitude cheaper, so the surface worker is the binding unit and the one this prices.

    What the core clamp this replaces got wrong is worth writing down, because the evidence for it is still in the journal and still reads the same way. That argument was that the peak "barely moves with the width", from two `surface-build` step peaks — 13.25 GB at ten jobs against 13.77 GB at two. Both figures are true and neither is the build's footprint: `peak_rss.reap_peak_rss_bytes` maxes over a child's process tree instead of summing it, so a step peak is the widest single process under that step, and under this step that is the parent. A reading that can only ever see one process was flat in the width because the process it saw is flat in the width, and the pool beside it was never in the number at all. The 2026-08-27 full-fresh pass is where the gap became visible — 17.76 GB read at eight workers, against a per-term measurement of the same tree that put parent and workers together at roughly twice a 34 GB box — and this arithmetic is the shape those terms actually decompose into.

    One approximation is left, and it is stated rather than hidden: a worker's slice-shaped piles shrink as the pool widens, so no single divisor is true at every width — this one is seeded at width two, the narrowest pool the arithmetic ever starts and so the widest a worker ever gets, which makes it an upper bound at every pooled width and too steep at the wide end. Erring steep is the deliberate direction, because the two ends are not symmetric: a divisor seeded wide is what walked a width-two pool into this box's reserve, while one seeded narrow only hands a roomy box fewer workers than its true cost curve would allow. A box the pooled shape does not fit at all floors at one, which is not a refusal but the serial shape: at width one there is no pool, every fragment exists once instead of twice, and the build is the cheapest it can be on a box that has outgrown it. The lever that bought the width back was the parent's pile, priced in SURFACE_PARENT_BYTES rather than in a width so that shrinking it widens this fan-out on every box at once: with phase 2 streaming into the shards and the worker spooling rather than retaining, both constants are seeded off the levered tree, and both machines in the fleet come off the floor with memory, not the cap, binding their width. What the parent still holds is structural per unit — the Unit, its state, its key and its address — and issue #160's on-disk workload was sized against that residual and closed as not needed.

    Under a gated cycle `make test`'s pytest pool is hot from t=0, so it comes off the box twice over: two cores out of the cap, as it always has, and FONT_SUITE_WORKER_BYTES apiece for as many workers as `make_test_pool_width` says it will have — the same figure the cycle hands that child, so what is reserved and what runs are one number by construction, the way `kernel_threads_budget` already does it. --skip-gates and the closure-unchanged auto-skip each give both back. gate:js runs from t=0 in every case, but it is a single node process, not a pool. `ncores` and `total_bytes` are keywords for the reason every budget here takes its box as one — an assertion about a machine the suite is not running on has to be a pure function over an invented one — and the cores come from `memory_budget.usable_cores()` rather than `os.cpu_count()`, so an affinity mask or a cgroup quota narrows this width the way it narrows every other one.
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
    reuse_run_m1: bool = False,
    run_m1_note: str = "",
    run_m1_fingerprint: str | None = None,
    fresh: bool = False,
    skip_surface: bool = False,
    refresh_assets: bool = False,
    surface_note: str = "",
    skip_contracts: bool = False,
    contracts_note: str = "",
    contracts_skip: list[str] | None = None,
    contracts_files: dict[str, str] | None = None,
    skip_validators: bool = False,
    validators_note: str = "",
    conform_note: str = "",
    conform_proven: bool = False,
    skip_plumbing: bool = False,
    plumbing_note: str = "",
    store_only: bool = False,
    preserve_snapshot: Path | None = None,
    record_greens: bool = False,
    keep_history: bool = False,
    recipe_serves: bool = False,
) -> Plan:
    resolved_snapshot = (
        snapshot_dir if snapshot_dir is not None else resolve_snapshot_dir(ROOT / "tmp", short_id)
    )
    do_carry = not no_carry and not first_run and not skip_plumbing and not store_only
    # The snapshot exists to survive this cycle's surface rewrite and to feed this cycle's carry; a pass doing neither takes no copy, unless the caller named a directory explicitly.
    takes_snapshot = (
        not first_run
        and not skip_plumbing
        and not store_only
        and (not skip_surface or do_carry or snapshot_dir is not None)
    )
    resolved_carry_out: Path | None = None
    if do_carry:
        resolved_carry_out = (
            carry_out if carry_out is not None else ROOT / f"verdicts-carried-{short_id}.json"
        )

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
        takes_snapshot=takes_snapshot,
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
        surface_note=surface_note,
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        contracts_skip=list(contracts_skip or []),
        contracts_files=contracts_files,
        skip_validators=skip_validators,
        validators_note=validators_note,
        conform_note=conform_note,
        conform_proven=conform_proven,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        plumbing_store_only=store_only,
        preserve_snapshot=preserve_snapshot,
        record_greens=record_greens,
        retention=do_retention,
        recipe_serves=recipe_serves,
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
            Step(
                "snapshot",
                None,
                "SKIPPED (first run: no existing surface to snapshot)",
                lane="build",
                skipped=True,
            )
        )
    elif skip_plumbing:
        plan.steps.append(
            Step(
                "snapshot",
                None,
                f"SKIPPED ({plumbing_note}); no carry reads it and no surface write threatens the live copy",
                lane="build",
                skipped=True,
            )
        )
    elif store_only:
        plan.steps.append(
            Step(
                "snapshot",
                None,
                "SKIPPED (the surface did not move, so there is no carry to feed and nothing to survive)",
                lane="build",
                skipped=True,
            )
        )
    elif not takes_snapshot:
        plan.steps.append(
            Step(
                "snapshot",
                None,
                "SKIPPED (the surface is not rewritten and no carry runs, so there is nothing to survive and nothing to feed)",
                lane="build",
                skipped=True,
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
        run_m1_argv += ["--kernel-threads", str(kernel_threads)]
        if fresh:
            run_m1_argv += ["--fresh-oracle-cache"]
        plan.steps.append(Step("run_m1", run_m1_argv, run_m1_note, lane="build"))

    if skip_surface:
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
        if fresh:
            plumbing_argv += ["--fresh-standing-memo"]
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
                "then `git diff -- rebuild/review-census-pins.json`, printed in full — the pins are the last accepted census; review the diff at commit time",
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
                    "submitted once the surface build settles; queued ahead of the validators lane"
                    + (f"; {contracts_note}" if contracts_note else ""),
                    lane="contracts",
                )
            )
        if skip_validators:
            plan.steps.append(
                Step(
                    "gate:rebuild-validators",
                    None,
                    f"SKIPPED ({validators_note})",
                    lane="validators",
                    skipped=True,
                )
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
            plan.steps.append(
                Step("gate:make-test", None, f"SKIPPED ({make_test_note})", lane="t0", skipped=True)
            )
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


def server_may_stay_up(*, skip_surface: bool, writes_store: bool) -> bool:
    """Whether a live review server can run right through this pass. Two things a cycle writes are the app's own: the surface's units and stamp — livereload watches every shard, and a restamped manifest orphans the tab's store — and the verdict store, which merge_verdicts refuses to touch under a live server anyway, since an open tab would flush its copy back over the merge. So the answer comes from the plan's writes, not from any skip flag standing proxy for them: a pass that rewrites no units and merges nothing into the store (a --no-carry pass, a --no-merge carry over an unmoved surface, a pass with no artifact work left to do) writes neither and the letters stay on screen for its whole run. An assets refresh is one of those passes rather than an exception to them: it rewrites no shard and leaves `generated_at` where it was, so the tab's store cannot be orphaned, and livereload — which already watches the served *.js, *.css, *.html and *.json — simply reloads the tab onto the new shell. Everything else the cycle writes is either outside the served tree (the census pins, the m1 summaries, the carried file) or read by the app only as status, where landing fresh mid-pass is the point rather than a hazard."""
    return skip_surface and not writes_store


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
            f"    Lane build only; no gates; run_m1 sweeps --jobs {plan.sweep_jobs} at --kernel-threads {'not passed (gates-only route)' if plan.reuse_run_m1 else plan.kernel_threads}, surface-build --jobs {plan.surface_jobs} ({plan.surface_reason})",
        ]
    t0_lane = "gate:js" if plan.skip_make_test else "gate:js, gate:make-test"
    lines = [
        "",
        f"  Concurrency (pool policy: {plan.pool_policy}):",
        f"    Lane t0   [from t=0, background]  : {t0_lane}",
        "    Lane build[serial, main thread]  : snapshot -> run_m1 -> surface-build -> submit gate:rebuild-contracts, gate:rebuild-validators -> plumbing -> census",
    ]
    if plan.skip_conform:
        lines.append(
            f"    Lane conform                     : SKIPPED ({plan.conform_note or '--skip-conform'})"
        )
    elif plan.pool_policy == "overlap":
        lines.append(
            f"    Lane conform                     : starts when run_m1's three JSONs pass; CO-RESIDENT with the pytest pools (--jobs {plan.conform_jobs})"
        )
    elif not plan.skip_make_test:
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
    else:
        lines.append("    Lane rebuild-contracts           : submitted once the surface build settles;")
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
    if plan.skip_validators:
        lines.append(
            "    Lane rebuild-validators          : SKIPPED (inputs unchanged since its last green run)"
        )
    else:
        lines.append("    Lane rebuild-validators          : submitted once the surface build settles;")
        if plan.pool_policy == "overlap":
            lines.append(
                "                                       CO-RESIDENT with the other pools (overlap policy)"
            )
        elif not plan.skip_contracts:
            lines.append(
                "                                       QUEUED behind gate:rebuild-contracts, whose chain already waits on gate:conform and gate:make-test"
            )
        elif not plan.skip_conform:
            lines.append(
                "                                       QUEUED behind gate:conform (queue policy; the contracts lane is not running)"
            )
        elif not plan.skip_make_test:
            lines.append(
                "                                       QUEUED behind gate:make-test (queue policy; neither gate:conform nor the contracts lane is running)"
            )
        else:
            lines.append("                                       no other heavy pool running, so no queueing")
    workers = f"{plan.make_test_workers} worker" + ("" if plan.make_test_workers == 1 else "s")
    if plan.skip_make_test:
        kernel_reason = "the table build's memory ceiling, the one width RAM binds"
    else:
        kernel_reason = f"the table build's memory ceiling, less gate:make-test's {workers}"
    lines.append(
        f"    run_m1 sweeps --jobs             : {plan.sweep_jobs}  (one process per acceptance configuration)"
    )
    if plan.reuse_run_m1:
        lines.append(
            "    run_m1 --kernel-threads          : not passed (the gates-only route enumerates nothing, so there is no fan-out to size)"
        )
    else:
        lines.append(f"    run_m1 --kernel-threads          : {plan.kernel_threads}  ({kernel_reason})")
    lines.append(f"    surface-build --jobs             : {plan.surface_jobs}  ({plan.surface_reason})")
    return lines


def plan_rows(plan: Plan) -> list[console.PlanRow]:
    """The plan's steps as the digest's rows. gate:conform and gate:rebuild-validators are the two steps whose fate the plan cannot settle — each one's key is taken over the artifacts run_m1 leaves, so a pass that plans the sweep or the lane may still prove it unnecessary once the build has finished — and they are the only rows that can read `run?`, which is what puts the range in the counts line. Such a row states the condition it turns on (`UNDECIDED_UNTIL_RUN_M1` holds the note for each), because every other row's note says why it will or will not run and a `run?` with nothing beside it is the one row a reader cannot resolve.

    A pass that skips run_m1 outright is the exception: nothing rebuilds, so the key the mid-run re-decision would compare is the one `main` has already compared and found no green for, and the sweep or the lane will certainly run. So is a `--fresh` pass, which reads no green at all and so has nothing to prove either unnecessary with. Those rows read `run`, and the counts line is a flat number rather than a range it could never reach the top of.
    """
    rows: list[console.PlanRow] = []
    for index, step in enumerate(plan.steps, start=1):
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
                number=index,
                status=status,
                name=step.name,
                note=UNDECIDED_UNTIL_RUN_M1[step.name] if undecided else step.note,
                argv="" if step.argv is None else " ".join(step.argv),
            )
        )
    return rows


def render_plan(plan: Plan) -> list[str]:
    """The plan block, as the lines the digest prints before step 1 and writes to plan.txt. It answers the four questions a reader has at that moment — which commit, where the logs are, how many steps there are and how many of them this pass will actually do, and what each one will run — and then hands off to the paths this pass resolved and to the concurrency block, which answers how the steps share the box. The header goes straight into the count and the rows because that is what a reader came for; the paths follow them rather than splitting the header from its arithmetic."""
    stamp = plan.stamp or "(--dry-run: nothing executed)"
    lines = [f"artifact cycle {stamp}  sha {plan.short_id}  host {socket.gethostname()}"]
    if plan.log_dir is not None:
        lines.append(f"logs {plan.log_dir}")
    rows = plan_rows(plan)
    lines.extend(["", console.counts_line(rows), *console.plan_lines(rows), ""])
    lines.append(f"  first run    : {plan.first_run}")
    lines.append(f"  snapshot dir : {plan.snapshot_dir}")
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
    """The pass's running record. Every `*_status` string here is display-only prose for the summary — it exists to be read by a human, and its wording is free to change. The booleans beside them (`gate_*_green`, `complaints_ok`) are the machine judgment, set at the moment the outcome is judged and read by every decision that follows; greenness is never re-derived from the status strings. A gate that never joined — skipped or never submitted — leaves its boolean None, which is neither green nor red.

    `step_seconds` and `step_returncodes` are the summary table's other two columns, filled from the driver's own measurement as each spawn returns rather than from the digest's clocks, so a row's seconds and the seconds the timings journal records for that same step are one number. Both are keyed by the plan's step name through STEP_ALIASES, so the reuse route's child files under the run_m1 row it reports under rather than under a name the table has no row for. `run_m1_failed` is the one outcome no return code states: that build's gate is judged from the three summary JSONs it left behind, so a child that exited zero with a failed Manual-pin gate is a failed step and nothing but this says so.
    """

    snapshot_dir: Path | None = None
    unmatched: int | None = None
    multi_matched: int | None = None
    pins_pass: bool | None = None
    surface_units: int | None = None
    surface_rows: int | None = None
    surface_batches: int | None = None
    echo_groups: int | None = None
    assets_status: str = "not run"
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
    conform_proven: bool = False
    validators_proven: bool = False
    interrupted: bool = False
    run_m1_failed: bool = False
    retention_figure: str = ""
    step_seconds: dict[str, float] = field(default_factory=dict)
    step_returncodes: dict[str, int] = field(default_factory=dict)


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text())


_Emitter = console.Digest


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
    emit: console.Digest,
    registry: _ChildRegistry,
    stream: bool,
    env: dict[str, str] | None = None,
) -> _StepResult:
    """One child, run to completion with both pipes drained into the digest — which logs every line it is handed and surfaces the ones worth surfacing. The banner is opened here and the closing line is not: a step closes with its own headline figure, and the figure only exists once the caller has read what the child left behind — the three summary JSONs, the manifest's totals, the chain's sections — so `_close_step` is called by the stage that knows it. `env` is what this process's environment is overlaid with for this child alone, and its default of None is the inheritance every other step wants; a step that states one gets that copy and nothing else in the cycle sees it.

    `stream` says this child's unparsed lines belong on the terminal verbatim as well as in its log, which is true of the two diffs a step prints for a human to act on — the census pins', whose whole point is that it is read and committed, and the constants', on the pass where the job-costs check trips and asks whether one has already been re-seeded. Everything else a child says reaches the terminal as an event or not at all, and reaches the log either way, so a failure has its whole output replayed under its own banner rather than nothing at all.

    A step whose spawn the registry refused opens no banner: a torn-down registry means a SIGINT already landed, and a banner for a child that never started would report a step this pass did not run.
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
    """Turn the rebuild suite's FAILED/ERROR summary lines into a gate verdict — the one judgment of the suite's output, lane-blind and so shared by both of the cycle's rebuild gates and by the interactive wrapper (rebuild.tools.rebuild_gate). `check` names whose invocation this is ("rebuild-contracts" / "rebuild-validators") and does nothing else: it rides into the verdict so the record says which suite ran, while every line of the judgment above it stays blind to the lane. pytest emits ANSI color whenever FORCE_COLOR is set (as it is under the agent harness), wrapping each summary line in escape codes, so strip those first — otherwise no line begins with a literal "FAILED "/"ERROR " and a colored run reports the exit-code placeholder instead of naming its failures. Every failure is unexplained by definition — the suite carries no documented-baseline amnesty — and every green is recordable."""
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


# Why a run that wrote no summaries failed, in the one spelling both the cycle's own failure list and the check line it files take. Two copies of it would be one too many now that the sentence is also history a later reader groups on.
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
    """Run (or, when `skip` is set, reuse) the M1 build and judge its gate from the three summary JSONs. The skip path leaves rebuild/out/m1 untouched and re-evaluates the recorded summaries, which is sound because run_m1's outputs are deterministic and timestamp-free over the fingerprinted inputs. A live green records the fingerprint only if it still matches — an input edited mid-run means the tested content is no longer on disk — and a live red matching the record deletes it.

    `reuse` is the middle route: the child is `run_m1 --gates-only` over the tables and font on disk, so the build's own summary is the one input the pass needs and the one file that must survive the spawn — it rewrites the defect fields in place and refuses outright without it, where the two gate summaries are its own output and go the way they go on a full build. Everything after the spawn is the full build's path unchanged, the green recording included: what earns that green is the pair the route was planned on (`gates_only_reuse` and `m1_tables_stamped`), which the child re-checks for itself before recording one of its own. That child spawns under its own step name, `RUN_M1_REUSE_STEP`, rather than the build's: `make cycle-timings ARGS='--by-step'` buckets on the step name and the host alone, so a seconds-long re-adjudication filed as `run_m1` would land in the row that is supposed to say what a full M1 build costs on this box, and `latest` would report it as the most recent cost of a build a reader is about to size a timeout from.

    Both paths file a check line, the skip included, because a skip is a judgment the cycle reached and stands behind rather than a check that did not happen — the summaries it read are this build's, and a reader asking how run_m1 has come out on this box wants the passes that reused a proof alongside the ones that made one. The child that did the work files nothing of its own: it inherits CYCLE_RUN_ENV and stands down, so one invocation is one line here. A build that wrote no summaries never reached a judge at all, and the red recorded for it carries the same sentence the cycle's own summary rolls up.
    """
    step = RUN_M1_REUSE_STEP if reuse else "run_m1"
    result: _StepResult | None = None
    if skip:
        emit.step_skipped("run_m1", f"{skip_note}; evaluating the gate from the recorded summaries")
    else:
        for name, path in M1_SUMMARY_FILES.items():
            if reuse and name == "pipeline":
                continue
            path.unlink(missing_ok=True)
        result = spawn(step, argv, emit=emit, registry=registry, stream=False)
    missing = [name for name, path in M1_SUMMARY_FILES.items() if not path.exists()]
    if missing:
        for name in missing:
            emit.note(
                "run_m1",
                f"run_m1 gate failure: missing {name} summary ({M1_SUMMARY_FILES[name]}) — run_m1 did not complete",
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
    summaries = {name: _load_summary(path) for name, path in M1_SUMMARY_FILES.items()}
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
            clear_contradicted_green(RUN_M1_GREEN, fingerprint)
        elif not skip:
            if run_m1_skip_fingerprint(ROOT) == fingerprint:
                record_green(RUN_M1_GREEN, fingerprint, files=run_m1_skip_files(ROOT))
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
    """Copy the review app's static files over the served surface and restamp the manifest's assets component, on the pass where that component is the only input that moved. It stands where the surface build would have stood, and everything downstream treats the pass as the skip it is: no unit can have changed, no shard, sidecar or `generated_at` moves, and so the carry is the identity, the snapshot has nothing to survive, and a listening server keeps its letters — livereload sees the copied files and reloads the tab onto the new shell."""
    result = spawn("assets-refresh", plan.argv("assets-refresh"), emit=emit, registry=registry, stream=False)
    if result.returncode != 0:
        emit.note("assets-refresh", f"ERROR: review.build refresh-assets exited {result.returncode}.")
        report.assets_status = f"FAILED (exit {result.returncode})"
        _close_step(emit, report, "assets-refresh", result)
        return False
    report.assets_status = "refreshed in place (units, sidecars and generated_at unmoved)"
    _close_step(emit, report, "assets-refresh", result)
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
    """Rebuild (or, when `skip` is set, reuse) the review surface. Both paths take the four totals from the surface's own manifest.json — review.build's validated output, whose totals build.check_shards holds to the shards it wrote — rather than scraping them back out of the build's stderr, so the numbers the summary reports are the ones the surface on disk actually carries."""
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
    """The chain's output split at the `[phase] <step>` line each of its steps opens with. One subprocess prints for seven steps, and this is what lets the summary keep a line per step: the driver reads each step's own lines out of the stream rather than out of its own process table. The chain's two result lines keep the `[chain] ` prefix rather than the phase one — they are what the cascade came to, not work starting — so they close the open section instead of opening one, which is how a `failed:` line stays out of the complaints body. Later rounds of the echo pass fold into the first round's section, since they are the same step of the cascade run again."""
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
    """Which of the standing fill's tally lines the summary keeps: the wrote-line, the tripwire's WARNING (so an over-broad rule is read off cycle_summary.json rather than only out of the child's dump), every per-rule line (linear in the rule count, and what lets a just-landed rule be quoted from the summary even at 0 filled), and only the composed-pair lines that actually filled or held something — the steady-state pairs grow quadratically in the rule set and their full roll call is standing_probe --coverage's job, not the summary's. The reach rollup's REACHED NOTHING lines and the except_left vocabulary line stay out: a rule reaching nothing now fails the step outright under `--require-reach`, so the summary carries the red rather than a line to notice, and the vocabulary line is informational. The already-verdicted column is optional because the chain runs the fill in its --open-only form, which drops it, while a dry run over the whole domain still prints it."""
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
    """Run the whole verdict chain as one child and rebuild the per-step report from its output. Returns the failure messages the cycle should carry, one per failed step, worded as that step's own."""
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
    report.standing_fill_lines = _scrape(sections.get("standing-fill", []), _standing_fill_news)

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


def _do_census(
    report: CycleReport, *, spawn, emit: console.Digest, registry: _ChildRegistry, plan: Plan
) -> None:
    """Rewrite the census pins from the surface's census-facts.json sidecar and print their git diff. The checked-in pins are the last accepted census, so that diff is exactly what a commit would be accepting: volatile totals that move with every letter added or reshaped, and an invariant block whose movement deserves a closer look. Nothing here gates — the step records no green and never fails the cycle — and a refresh that fails (a surface predating the sidecar, say) is reported and left alone, since the next pass that rebuilds the surface heals it."""
    census = spawn("census", plan.argv("census"), emit=emit, registry=registry, stream=False)
    if census.returncode != 0:
        report.census_status = f"update FAILED (exit {census.returncode}) — informational"
        _close_step(emit, report, "census", census, "ok")
        return
    emit.substep(SUBSTEP_PARENTS["git-diff"], "git-diff")
    diff = spawn(
        "git-diff",
        ["git", "diff", "--", "rebuild/review-census-pins.json"],
        emit=emit,
        registry=registry,
        stream=True,
    )
    if diff.stdout.strip():
        report.census_status = (
            "updated (diff vs the last accepted census shown above — review it at commit time)"
        )
    else:
        report.census_status = "updated (matches the last accepted census)"
    _close_step(emit, report, "census", census, "ok")


def _do_job_costs(
    report: CycleReport, *, spawn, emit: console.Digest, registry: _ChildRegistry, plan: Plan
) -> None:
    """Hold the checked-in per-unit peaks against what this box has actually measured. Several widths in this tree are the box divided by one of those constants, and the constants are only ever as true as the last measurement anybody compared them to — so the cycle compares them, on a journal this pass's own pools have just appended to, which is why the step sits after the gate join rather than beside the census.

    Nothing here gates, and that is deliberate rather than an oversight: a divisor that has gone stale makes a pool the wrong width, which costs wall time or swap, but it cannot make an artifact wrong — so it must never red a pass whose artifacts are green. The loudness is the summary line and `job_costs_ok`, and the acceptance is a human's commit of the re-seeded constant, exactly as the census pins are accepted by committing their diff. A tool that cannot run at all is reported as informational too: a broken check is the check's problem, and the cycle has nothing to say about the constants either way.

    The diff is conditional where the census's is unconditional, because the two files are nothing alike. rebuild/review-census-pins.json exists only to hold the census, so its whole diff is the acceptance and printing it every pass costs a reader nothing. The four files that hold these constants hold a great deal besides them, so an unconditional diff would print unrelated work on every pass and train a reader to skip the one pass where it mattered. When the check trips it answers the single question worth asking then: has the constant already been re-seeded in this working tree, so the commit in hand is already the acceptance?
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
    """The verdict plumbing's skip path. Nothing ran, so the summary says so for every step of the chain — and the carried file the recorded pass wrote is still the stamp-aligned frontier (the surface it was carried onto has not moved), so the report keeps naming it rather than reading as a pass with no carry at all."""
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
    """Whether the font suite's wrapper decided for itself that it had nothing to run. It exits zero either way, so without reading its output the cycle closes the row `ok` with no figure and the terminal says the suite ran — the one reading a watcher must not be given, because the whole point of that auto-skip is that this pass tested nothing there."""
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
    """One child's environment, carried on that child's own spawn callable rather than added to the argument list every gate task shares. The alternative is os.environ, and it is the wrong one: run_m1, the surface build and both rebuild lanes spawn from this same process, so a width set there for gate:make-test would pin their `-n auto` pools to it too — the contracts lane wants the whole box and the validators lane its own narrower answer. Wrapping instead of widening the protocol also leaves the task signature alone, which is what keeps the plan the only writer of what a child runs: this adds to the child's environment, never to its argv."""

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
    verdict = evaluate_conform_gate(summary)
    if result.returncode != 0 and not verdict.failures:
        # The one place an exit code outranks a summary, and only in this direction: a sweep whose own JSON says it passed while its process did not is a sweep that stopped somewhere the summary cannot describe, so the judged verdict is replaced rather than annotated.
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
    emit: console.Digest,
    registry: _ChildRegistry,
    argv: list[str],
) -> CheckVerdict:
    """The rebuild suite's contracts lane — every test whose fixture closure holds no live build artifact, run at the box's full xdist width. It reads nothing the build lane writes, yet it is still submitted once the surface build settles, for two reasons that have nothing to do with correctness: a full-width pool must not share the box with the M1 build or the surface build, whose peaks are what the repo's parallelism defaults are sized against, and waiting costs it nothing, since under the queue policy it parks behind conform anyway and on the common gate pass every stage upstream auto-skips, so it starts at t=0 regardless. Under the queue policy it parks at the tail of the make-test -> conform chain so only one heavy pool is hot at a time, and it goes ahead of the validators lane because it is the short one and fails fast on a code error before the long lane starts."""
    if pool_policy == "queue":
        _await_gate_futures(conform_fut, make_fut)
    result = spawn("gate:rebuild-contracts", argv, emit=emit, registry=registry, stream=False)
    verdict = classify_rebuild_output(result.stdout, result.returncode, "rebuild-contracts")
    _close_gate(emit, "gate:rebuild-contracts", result, verdict)
    return verdict


def _gate_validators_task(
    pool_policy: str,
    conform_fut: Future | None,
    contracts_fut: Future | None,
    make_fut: Future | None,
    spawn,
    emit: console.Digest,
    registry: _ChildRegistry,
    argv: list[str],
) -> CheckVerdict:
    """The rebuild suite's validators lane — the tests that read rebuild/out, the review surface and the fixture caches, at the narrower width rebuild/conftest.py derives from what one of them costs, because each of them carries a live fixture's working set. Submitted once the surface build settles, which for this lane is a correctness requirement rather than a courtesy: its session fixture reads the live surface whenever surface_build_skippable calls it provably fresh, so a lane started against one mid-rewrite would either observe a fresh manifest beside a sidecar review.build has not written yet, or decide the surface is not fresh and waste a whole duplicate build inside the suite. Nothing later in the build lane is an input to it, the census pins included. Under the queue policy it parks at the tail of the whole chain, contracts included."""
    if pool_policy == "queue":
        _await_gate_futures(conform_fut, contracts_fut, make_fut)
    result = spawn("gate:rebuild-validators", argv, emit=emit, registry=registry, stream=False)
    verdict = classify_rebuild_output(result.stdout, result.returncode, "rebuild-validators")
    _close_gate(emit, "gate:rebuild-validators", result, verdict)
    return verdict


def _gate_result(fut: Future, name: str, failures: list[str]):
    try:
        return fut.result()
    except Exception as exc:
        failures.append(f"{name} raised: {exc!r}")
        return None


def _rc_verdict(check: str, returncode: int, failure: str) -> CheckVerdict:
    """The verdict for a gate whose whole judgment is its exit code, in the two spellings the summary has always printed. It names no failed ids, and that is the honest answer rather than a gap: neither suite's output is parsed here, so what a red one knows is that something failed and not which case did."""
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
    """Fold one lane's outcome into the report, and file it under this run. The classifier is lane-blind, so the only per-lane thing here is which three fields the verdict lands in — the verdict already carries which lane it judged. A task that raised is not a judgment and files nothing: what "FAILED (exception)" describes is the pool rather than the suite, and a red check line for it would put a failure on a lane's record that the lane never returned."""
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
    emit: console.Digest,
    timings: CycleTimings | None = None,
) -> None:
    """Fold every gate that ran into the report, and file each one's verdict under this run. Two of the five have no judge of their own — the JS suite and `make test` are pass/fail by exit code and always were — so their verdicts are built here rather than imported, which is what puts all five in the journal in one shape without inventing a judgment either of them does not make. gate:make-test's own wrapper stands down on CYCLE_RUN_ENV precisely so this line is the only one, and `make test`'s rc is honest for the font suite in a way run_m1's is not."""
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
    if validators_fut is not None:
        _join_rebuild_lane(report, failures, validators_fut, "validators", emit, timings)
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
    """Whether the chain closed at a fixpoint, which is what the plumbing green claims. Inferring it from the standing merge writing nothing would not do — a standing fill landing on one unit can make its echo group unanimous and leave a blank sibling that only another echo fill would take, so a pass whose fills landed would have to hand the cascade on. Holding the index in one process makes another echo pass cost a second, so the chain runs the cascade to a standstill itself and says so: the green rests on a witnessed re-run that wrote nothing rather than on an ordering argument."""
    return report.plumbing_fixpoint


def _record_gate_greens(
    report: CycleReport, plan: Plan, gate_keys: dict[str, str], emit: console.Digest
) -> None:
    """Persist the concurrent gates' green records after they joined. gate:conform's and gate:rebuild-validators' keys are snapshotted right after run_m1 finished, where each one's skip is decided, and the contracts lane's right after the surface build settles, which is where both lanes are submitted — the surface build writes only under rebuild/out/review, which neither lane's closure holds, and the census pins are exempt from the rebuild closure, so neither the build between a snapshot and its submission nor the refresh later in the pass can invalidate a key. Each is recomputed here before recording, so a source file edited while the gates ran — content the gates never tested — can never be recorded green. A red gate whose key still matches its record deletes the falsified record."""
    key = gate_keys.get("conform")
    if key:
        if report.gate_conform_green is True:
            if conform_skip_fingerprint(ROOT, plan.conform_horizon) == key:
                record_green(CONFORM_GREEN, key, files=conform_skip_files(ROOT, plan.conform_horizon))
            else:
                emit.note(
                    "gate:conform",
                    "gate:conform green, but its inputs changed while the cycle ran — green not recorded",
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
    """The selection file the contracts spawn reads, written from the ids the plan resolved, and the previous run's sidecar cleared so a suite that dies before session end cannot leave a stale one to be merged as this run's."""
    from rebuild.tools import contracts_closure

    record = rebuild_lane_green("contracts")
    contracts_closure.write_selection(contracts_closure.selection_path(record), plan.contracts_skip)
    contracts_closure.sidecar_path(record).unlink(missing_ok=True)


def _contracts_payload(plan: Plan, roster: dict[str, str] | None):
    """What a green contracts gate records beside its key, or None when a label the selection was taken over has moved since — the same drift check the key makes, extended to the paths a recorded closure names outside the lane's roster."""
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
    """Every child's wall time and exit status onto the report as it returns, keyed by the plan step it belongs to, which is what fills the summary table's last two columns. The key goes through STEP_ALIASES so a spawn under a route's own name — the seconds-long `run_m1 --gates-only` re-adjudication — files under the row the plan showed and the table reads it as the run_m1 that ran, rather than leaving that row blank and `not run`. It wraps rather than being folded into `_run_step` because the tests drive the cycle with their own spawn callables, and a table that only had times for real subprocesses would be a table whose columns changed shape with the fake."""

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
        validators_fut: Future | None = None
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
        if not plan.skip_gates and plan.skip_validators:
            report.gate_validators = f"skipped ({plan.validators_note})"
            emit.step_skipped("gate:rebuild-validators", plan.validators_note)
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
            if plan.skip_gates or not plan.skip_validators:
                report.gate_validators = "not run (run_m1 gate failed)"
                emit.step_not_run("gate:rebuild-validators", "run_m1 gate failed")
            if not plan.skip_gates and not plan.skip_conform:
                report.gate_conform = "not run (run_m1 gate failed)"
                emit.step_not_run("gate:conform", "run_m1 gate failed")
            _join_gates(report, failures, js_fut, None, None, None, make_fut, emit, timings)
            return _finish(report, failures, plan, timings, emit)

        if not plan.skip_gates and not plan.skip_conform:
            conform_key = conform_skip_fingerprint(ROOT, plan.conform_horizon)
            green = None if plan.fresh else read_green_record(CONFORM_GREEN)
            if green is not None and green["fingerprint"] == conform_key:
                report.conform_proven = True
                report.gate_conform = f"skipped ({CONFORM_SKIP_NOTE})"
                emit.step_skipped(
                    "gate:conform",
                    f"SKIPPED after run_m1 — {CONFORM_SKIP_NOTE}. The artifacts this pass leaves carry the key its last green sweep was taken over, so the sweep would shape the same font over the same windows.",
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

        validators_pending = not plan.skip_gates and not plan.skip_validators
        if validators_pending:
            validators_key = rebuild_lane_fingerprint(ROOT, "validators")
            green = None if plan.fresh else read_green_record(REBUILD_VALIDATORS_GREEN)
            if validators_key is not None and green is not None and green["fingerprint"] == validators_key:
                validators_pending = False
                report.validators_proven = True
                report.gate_validators = f"skipped ({VALIDATORS_SKIP_NOTE})"
                emit.step_skipped(
                    "gate:rebuild-validators",
                    f"SKIPPED after run_m1 — {VALIDATORS_SKIP_NOTE}. The out/m1 artifacts this pass leaves and the rest of the lane's closure carry the key its last green run was taken over, so the lane would read the same tables and font against the same sources.",
                )
            elif plan.record_greens:
                gate_keys["validators"] = validators_key or ""

        if plan.runs("assets-refresh") and not _do_assets_refresh(
            report, spawn=spawn, emit=emit, registry=registry, plan=plan
        ):
            failures.append("assets refresh failed")
            if not plan.skip_gates and not plan.skip_contracts:
                report.gate_contracts = "not run (assets refresh failed)"
                emit.step_not_run("gate:rebuild-contracts", "assets refresh failed")
            if validators_pending:
                report.gate_validators = "not run (assets refresh failed)"
                emit.step_not_run("gate:rebuild-validators", "assets refresh failed")
            _join_gates(report, failures, js_fut, None, None, conform_fut, make_fut, emit, timings)
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
            if not plan.skip_gates and not plan.skip_contracts:
                report.gate_contracts = "not run (surface build failed)"
                emit.step_not_run("gate:rebuild-contracts", "surface build failed")
            if validators_pending:
                report.gate_validators = "not run (surface build failed)"
                emit.step_not_run("gate:rebuild-validators", "surface build failed")
            _join_gates(report, failures, js_fut, None, None, conform_fut, make_fut, emit, timings)
            _record_gate_greens(report, plan, gate_keys, emit)
            return _finish(report, failures, plan, timings, emit)

        if not plan.skip_gates and not plan.skip_contracts:
            if plan.record_greens:
                gate_keys["contracts"] = rebuild_lane_fingerprint(ROOT, "contracts") or ""
            _write_contracts_selection(plan)
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
        if validators_pending:
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
            emit.step_skipped("census", "rehearsal: the checked-in pins track the live surface")
        else:
            _do_census(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        if plumbing_key and report.complaints_ok is True and plan.record_greens and plan.review_out is None:
            record_plumbing_green(plumbing_key)

        _join_gates(
            report, failures, js_fut, contracts_fut, validators_fut, conform_fut, make_fut, emit, timings
        )
        _record_gate_greens(report, plan, gate_keys, emit)
        _do_job_costs(report, spawn=spawn, emit=emit, registry=registry, plan=plan)
        return _finish(report, failures, plan, timings, emit)
    except KeyboardInterrupt:
        registry.terminate_all()
        pool.shutdown(wait=False, cancel_futures=True)
        report.interrupted = True
        return _finish_interrupted(report, failures, registry.killed_count, plan, timings, emit)
    finally:
        pool.shutdown(wait=True)


def _deep_sweep_report(root: Path = ROOT) -> tuple[str, str]:
    """`deep_sweep_status` for the summary, and never a reason for a pass to fail: the deep sweep is an out-of-band instrument the cycle only reports on, so anything that goes wrong reading its record reads as unknown."""
    try:
        return deep_sweep_status(root)
    except Exception as exc:
        return "unknown", f"could not be read ({exc!r})"


INFORMATIONAL_STEPS = ("census", "job-costs")

_CARRY_WROTE = re.compile(r"^wrote \S+: (\d+) carried onto manifest")
_CARRY_QUEUE = re.compile(r"^human queue: (\d+) -> (\d+)")


def carry_figure(lines: list[str]) -> str:
    """What the carry came to, read back out of its own two headline lines: how many verdicts landed on the new surface, and how much of the human queue that left. It is scraped rather than reported because the chain runs as one child and the carry is a step inside it, so its counts reach this process only as the lines it printed. A carry that printed neither line — the store-only route, a rehearsal, a chain that failed before it — answers with the empty string, and the caller says what it has instead."""
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
    "gate:rebuild-validators": "gate_validators",
    "gate:conform": "gate_conform",
    "gate:make-test": "gate_make_test",
}


def step_figure(report: CycleReport, name: str) -> str:
    """One step's headline number, in whatever unit that step counts in — what a reader wants beside the outcome when they are scanning the table for the one row that moved, and what the step's own closing line carries as it finishes. A step with nothing to count answers with the empty string, which the table then leaves blank rather than padding with a dash. What a step that did not run has to say is its reason instead, which the outcome column and the plan block have both already given — so `summary_rows` drops the figure on a `skipped` or `not run` row rather than reporting the last build's unmatched count as though this pass had counted it.

    A gate's figure is the status prose its own judge worded — "green", "FAILED (3 unexplained)", "skipped (…)" — which `_GATE_STATUS_FIELDS` above maps to the plan step that carries it, so the table reads a gate's outcome and its figure off one string rather than re-deriving greenness from a second source. A plain "green" is dropped: the outcome column has already said it, and anything else the judge worded — a failure count, an annotation — is what the figure is for.
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
    """A step's figure with whatever the outcome column has already said taken out of it. A failed gate words its own status — `FAILED (exit 1)`, `FAILED (3 unexplained)` — so a row that printed both read `FAILED  FAILED (exit 1)`, spending the table's widest column on the word in the column beside it. What is left is the part the outcome never carried: `exit 1`, `3 unexplained`. A figure that says nothing else at all drops out entirely."""
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
    """Close a spawned step from the stage that knows how it came out, carrying that step's own figure. `outcome` defaults to what the child's exit status says, and a stage that judges by something else — run_m1 by its summaries, the two informational steps by the fact that they gate nothing — states its own. A figure that only repeats the outcome is dropped rather than printed twice."""
    verdict = outcome
    if verdict is None:
        verdict = "ok" if result is None or result.returncode == 0 else f"FAILED (exit {result.returncode})"
    figure = step_figure(report, STEP_ALIASES.get(name, name))
    emit.step_end(name, result, verdict, _figure_beside(verdict, figure))


def _close_gate(
    emit: console.Digest, name: str, result: _StepResult, verdict: CheckVerdict | None = None
) -> None:
    """Close a gate on the judgment its own task has just reached. It cannot go through `_close_step`, because a gate is judged inside its thread and folded into the report only when the joiner reaches it — so at the moment the step closes, the report still says the gate never ran. The figure follows the same rule the table's gate rows do: a plain green is already the outcome column's word, and anything else the judge worded is the figure."""
    if verdict is None:
        status = "green" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
        passed = result.returncode == 0
    else:
        status, passed = verdict.status, verdict.ok
    outcome = "ok" if passed else "FAILED"
    emit.step_end(name, result, outcome, _figure_beside(outcome, "" if status == "green" else status))


def _step_outcome(report: CycleReport, plan: Plan, step: Step, *, retention_ran: bool) -> str:
    """The table's outcome column: one of four words, so the column stays a column. What actually happened in prose is the figure's business, and why a step did not run is the plan block's.

    A step that spawned a child answers with what that child came to rather than with the bare fact that it ran: a row filled from the seconds the step cost would read `ok` for a run_m1 whose Manual pins failed, a surface build whose child died, or a chain that exited nonzero, because every step that ran cost some. The two informational steps are the exception in the other direction — the census and the job-costs check gate nothing by design, and each already says what went wrong in its own figure, so a nonzero exit there is a note rather than a failed row.

    Retention is the one step whose row can read all three words on a plan that meant to run it: it happens inside `_finish`, so a failure or a SIGINT anywhere upstream stops the pass before it, and that row is `not run`. `skipped` is reserved for the plan having ruled it out — `--keep-history`, a first run, a rehearsal — which is a different fact and the one the plan block explains.
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
    if step.name == "snapshot":
        return "ok" if report.snapshot_dir is not None else "skipped"
    if step.skipped:
        return "skipped"
    if step.name == "run_m1" and report.run_m1_failed:
        return "FAILED"
    returncode = report.step_returncodes.get(step.name)
    if returncode and step.name not in INFORMATIONAL_STEPS:
        return "FAILED"
    return "ok" if step.name in report.step_seconds else "not run"


def summary_rows(report: CycleReport, plan: Plan, *, retention_ran: bool) -> list[console.SummaryRow]:
    """The table, a row per planned step. A step that did not run carries no figure at all — a skipped run_m1 still has the last build's unmatched count on the report and a skipped surface build its totals, and printing those beside `skipped` would report this pass's numbers as facts about a stage this pass never ran."""
    rows: list[console.SummaryRow] = []
    for index, step in enumerate(plan.steps, start=1):
        outcome = _step_outcome(report, plan, step, retention_ran=retention_ran)
        ran = outcome not in ("skipped", "not run")
        rows.append(
            console.SummaryRow(
                number=index,
                name=step.name,
                outcome=outcome,
                figure=_figure_beside(outcome, step_figure(report, step.name)) if ran else "",
                seconds=report.step_seconds.get(step.name),
            )
        )
    return rows


def summary_cycle_lines(report: CycleReport, plan: Plan, retention_lines: list[str]) -> list[str]:
    """What the table cannot hold: the paths a reader goes to next, the verdict chain's step-by-step outcome, the out-of-band deep sweep's standing, and what retention pruned. There is no instruction here: a green pass follows these lines with the readiness checklist itself (`readiness_block`), and a red pass's next command is whatever the failure block names.

    The chain's own news is indented under the two lines it belongs to — what the carry landed and how much queue it left, then what each fill and merge wrote. Those are the lines a reader came for after a sitting: which rule filled what, and whether the queue moved. They reach this process only as the chain's printed output, and `_standing_fill_news` and the scrapes beside it have already cut them down to a handful, so the summary carries them rather than sending a reader to `cycle_summary.json` and the plumbing step's own log for the one number that says whether the pass was worth running.
    """

    def show(value: object) -> str:
        return "—" if value is None else str(value)

    def news(lines: list[str]) -> list[str]:
        return [f"      {line}" for line in lines]

    deep_status, deep_note = _deep_sweep_report()
    lines = [
        f"  snapshot dir     : {show(report.snapshot_dir)}",
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
        f"  job costs        : {report.job_costs_status}",
        f"  deep sweep       : {deep_status} ({deep_note})",
        "  run_m1 summaries :",
        *(f"      {path}" for path in M1_SUMMARY_FILES.values()),
        f"      {CONFORM_SUMMARY}",
    ]
    if plan.log_dir is not None:
        lines.append(f"  logs             : {plan.log_dir}")
    if retention_lines:
        lines.extend(["", *retention_lines])
    return lines


def _as_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _gate_entry(status: str, green: bool | None, skip: str | None = None) -> dict:
    """`green` is the judgment the gate recorded when it joined — True exactly when it ran in this pass and passed — never a re-reading of `status`, whose prose is for the human summary. A gate that never joined carries None and publishes False, since nothing was verified.

    `skip` is why the gate did not run, and it is the discriminator the readiness checker needs: "proved" means a matching green record already showed this exact content passing, so the state is verified; "forced" means a flag suppressed the gate and nothing proved anything. The status prose cannot carry that — both kinds read as some flavor of "skipped" — and a reader that cannot tell them apart is what once let --skip-conform report READY.
    """
    return {"status": status, "green": green is True, "skip": skip}


def _skip_kind(*, proved: bool, forced: bool = False) -> str | None:
    """Most-informative first. A green record outranks a flag because it says something about the content rather than about the caller — the two never co-occur (a gate the caller switched off never reaches its own auto-skip), but the order says which reading wins if they ever do."""
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
            "rebuild_validators": _gate_entry(
                report.gate_validators,
                report.gate_validators_green,
                _skip_kind(proved=plan.skip_validators or report.validators_proven),
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
        "log_dir": _as_str(plan.log_dir),
        "interrupted": report.interrupted,
        "plan": {
            "verdicts": _as_str(plan.verdicts),
            "carry_out": _as_str(plan.carry_out),
            "do_merge": plan.do_merge,
            "conform_horizon": plan.conform_horizon,
            "kernel_threads": None if plan.reuse_run_m1 else plan.kernel_threads,
            "pool_policy": plan.pool_policy,
            "skip_gates": plan.skip_gates,
            "skip_conform": plan.skip_conform,
            "skip_run_m1": plan.skip_run_m1,
            "reuse_run_m1": plan.reuse_run_m1,
            "skip_surface": plan.skip_surface,
            "refresh_assets": plan.refresh_assets,
            "skip_contracts": plan.skip_contracts,
            "skip_validators": plan.skip_validators,
            "skip_plumbing": plan.skip_plumbing,
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


def prune_build_logs(root: Path, keep: int) -> list[Path]:
    """Delete every run directory but the newest `keep`. The names are `<UTC stamp>-<short sha>`, so a lexical sort is a chronological one and no mtime is consulted — a directory copied or touched out of band keeps its place in the run order. The `latest` symlink beside them is never a candidate: it is a pointer rather than a run, and the run it points at is the newest one, which is always kept."""
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
    """What a retention pass came to: the block of lines the summary prints, and the one line the step's own row and closing line carry. The figure is the counts themselves rather than a gloss on them, because what a reader scanning the table wants to know is whether the pass swept anything at all and whether the journal moved."""

    lines: list[str]
    figure: str


def _retention_figure(removed: list[str], intact: list[str], journal_state: str) -> str:
    """The retention row's one line: what the pass swept, which piles it deliberately left where they were, and where the journal came out. A pile left intact is named rather than counted as zero, because the two are different facts — nothing to remove, against a sweep this pass had no business running."""
    clauses = ["removed " + (", ".join(removed) if removed else "nothing")]
    if intact:
        named = intact[0] if len(intact) == 1 else f"{', '.join(intact[:-1])} and {intact[-1]}"
        clauses.append(f"{named} left intact")
    if journal_state:
        clauses.append(journal_state)
    return "; ".join(clauses)


def run_retention(plan: Plan) -> RetentionResult:
    """Prune the piles a green pass leaves behind, and answer with the lines the summary prints and the figure the step's row carries. It reports rather than prints because the summary is one block written through the digest: a pass that printed its retention results as it went would put them above the table that says whether the pass was green at all."""
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

    if not plan.takes_snapshot:
        lines.append(
            "  snapshots : left intact (this pass took none, so pruning to it would delete the last recovery copy)"
        )
        intact.append("snapshots")
    else:
        removed = prune_snapshots(ROOT / "tmp", plan.snapshot_dir, plan.preserve_snapshot)
        removed_counts.append(swept(len(removed), "snapshot", "snapshots"))
        if removed:
            lines.append(
                f"  snapshots : removed {console.fmt_count(len(removed))} ({', '.join(rel(path) for path in removed)}); kept {rel(plan.snapshot_dir)}"
            )
        else:
            lines.append(f"  snapshots : nothing to remove; kept {rel(plan.snapshot_dir)}")

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

    dropped_logs = prune_build_logs(BUILD_LOGS_ROOT, BUILD_LOGS_KEEP)
    removed_counts.append(swept(len(dropped_logs), "build log", "build logs"))
    lines.append(
        f"  build logs: removed {console.fmt_count(len(dropped_logs))}; kept the last {BUILD_LOGS_KEEP} runs under {rel(BUILD_LOGS_ROOT)}"
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

    run_m1_fp = run_m1_skip_fingerprint(ROOT)
    skip_run_m1 = False
    reuse_run_m1 = False
    run_m1_note = ""
    skip_surface = False
    refresh_assets = False
    surface_note = ""
    skip_contracts = False
    contracts_note = ""
    skip_validators = False
    validators_note = ""
    conform_note = ""
    auto_skip_conform = False
    contracts_skip: list[str] = []
    contracts_files: dict[str, str] | None = None
    if not args.fresh and not args.skip_gates:
        from rebuild.tools import contracts_closure

        contracts_key, contracts_roster = rebuild_lane_closure(ROOT, "contracts")
        green = read_green_record(REBUILD_CONTRACTS_GREEN)
        if contracts_key is not None and green is not None and green["fingerprint"] == contracts_key:
            skip_contracts = True
            contracts_note = "input closure unchanged since its last green run; --fresh overrides"
        elif contracts_roster is not None:
            contracts_files = contracts_closure.current_files(ROOT, contracts_roster, green)
            selection = contracts_closure.select(green, contracts_files)
            contracts_skip = sorted(selection.skip)
            contracts_note = selection.describe()
    if not args.fresh:
        green = read_green_record(RUN_M1_GREEN)
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
    if skip_run_m1:
        if not args.skip_gates and not args.skip_conform:
            green = read_green_record(CONFORM_GREEN)
            if green is not None and green["fingerprint"] == conform_skip_fingerprint(
                ROOT, args.conform_horizon
            ):
                auto_skip_conform = True
                conform_note = CONFORM_SKIP_NOTE
        if not args.skip_gates:
            validators_key = rebuild_lane_fingerprint(ROOT, "validators")
            green = read_green_record(REBUILD_VALIDATORS_GREEN)
            if validators_key is not None and green is not None and green["fingerprint"] == validators_key:
                skip_validators = True
                validators_note = VALIDATORS_SKIP_NOTE

    preamble: list[str] = []

    def announce(text: str) -> None:
        """Something the reader needs before the plan is even resolved, and so before there is a digest to catch it. The terminal sees it now and terminal.log receives it the moment the digest opens, because a copy of the terminal that is missing the lines the pass opened with is not a copy."""
        preamble.append(text)
        print(text)

    preserve_snapshot = unfinished_cycle_snapshot()
    if preserve_snapshot is not None:
        announce(
            f"The last cycle did not finish green; keeping its snapshot at {preserve_snapshot} as well as this pass's."
        )

    if not args.no_carry and args.verdicts is None and not first_run:
        resolved = resolve_carry_source()
        if resolved is None:
            args.no_carry = True
            announce(
                "No carryable verdicts found (neither the autosave nor any verdicts-*.json at the repo root or under rebuild/evidence holds an effective verdict); proceeding without carry. Pass --verdicts to name a master explicitly."
            )
        else:
            announce(describe_carry_source(resolved, ROOT))
            if not resolved["aligned"]:
                return 2
            args.verdicts = resolved["path"]

    skip_plumbing = False
    store_only = False
    plumbing_note = ""
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
        elif plumbing_key is not None and args.verdicts is not None:
            # The surface has not moved, so the carry would resolve every unit against itself: the snapshot is a clone of this same surface, the content keys are equal, and the carry preserves each record's `at`, which the merge compares strictly — so its re-prefixed notes could never land. Only the store moved, and the one input the store's own hash cannot see is the master, so merging that directly is the whole of what the carry was for.
            store_only = True

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
        reuse_run_m1=reuse_run_m1,
        run_m1_note=run_m1_note,
        run_m1_fingerprint=run_m1_fp,
        fresh=args.fresh,
        skip_surface=skip_surface,
        refresh_assets=refresh_assets,
        surface_note=surface_note,
        skip_contracts=skip_contracts,
        contracts_note=contracts_note,
        contracts_skip=contracts_skip,
        contracts_files=contracts_files,
        skip_validators=skip_validators,
        validators_note=validators_note,
        conform_note=conform_note,
        conform_proven=auto_skip_conform,
        skip_plumbing=skip_plumbing,
        plumbing_note=plumbing_note,
        store_only=store_only,
        preserve_snapshot=preserve_snapshot,
        record_greens=not args.dry_run,
        keep_history=args.keep_history,
        recipe_serves=args.stop_server,
    )

    if args.dry_run:
        print("\n".join(render_plan(plan)))
        return 0

    plan.stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan.log_dir = BUILD_LOGS_ROOT / f"{plan.stamp}-{plan.short_id}"
    digest = console.Digest(
        steps=[step.name for step in plan.steps], log_dir=plan.log_dir, aliases=STEP_ALIASES
    )
    with digest:
        digest.replay(preamble)
        digest.plan_block(render_plan(plan))
        if not _preflight(
            args, may_stay_up=server_may_stay_up(skip_surface=skip_surface, writes_store=plan.do_merge)
        ):
            return 2

        if first_run:
            print("First-run mode: no existing surface at rebuild/out/review — skipping snapshot and carry.")

        report = CycleReport()
        from rebuild.tools.cycle_timings import CycleTimings

        timings = CycleTimings(CYCLE_TIMINGS)
        # Every child this pass spawns inherits this, and the two that judge a check of their own — gate:make-test's wrapper and run_m1's CLI — read it as "a cycle is recording on your behalf" and file nothing. The suppression has to be inherited rather than passed, because it must reach a grandchild too: `make test` is a Make recipe around the wrapper, and an argument this process could add to a child's argv would stop at the recipe.
        os.environ[CYCLE_RUN_ENV] = timings.run_id

        if plan.takes_snapshot:
            digest.step_start("snapshot", None, plan.describe("snapshot"))
            if plan.snapshot_dir.exists():
                digest.note("snapshot", f"ERROR: snapshot dir already exists: {plan.snapshot_dir}")
                digest.note(
                    "snapshot",
                    "Refusing to overwrite the only recovery copy. Remove it or point --snapshot-dir elsewhere.",
                )
                digest.step_end("snapshot", None, "FAILED")
                return 2
            started = time.perf_counter()
            how = snapshot_surface(REVIEW_OUT, plan.snapshot_dir)
            report.snapshot_dir = plan.snapshot_dir
            report.step_seconds["snapshot"] = time.perf_counter() - started
            digest.step_end("snapshot", None, "ok", f"{how} -> {plan.snapshot_dir}")
        else:
            digest.step_skipped("snapshot", plan.note_for("snapshot"))

        registry = _ChildRegistry()
        return _run_cycle(plan, report, digest, registry, timings=timings)


def readiness_block(plan: Plan) -> list[str]:
    """The checklist `make verdict-ready` prints, computed here so a green pass ends on the answer rather than on an instruction to go and ask for it. It reads the cycle summary this pass has just written, so it runs after `_emit_cycle_summary`. A rehearsal pass prints nothing, since its surface is never the one served. Under `make review-cycle` the server row is left out: the recipe stops the server ahead of a pass that writes under it and answers for it on the line after this one — restarting it, or saying it was left down — so a row read here would call a server the recipe is about to start absent. The suite stubs this to nothing beside `run_retention`, because the real thing reads the live surface, which a contracts-lane test may not."""
    if plan.review_out is not None:
        return []
    from rebuild.tools import verdict_ready

    try:
        result, ready = verdict_ready.readiness(
            with_server=not plan.recipe_serves,
            repo_root=ROOT,
            review_dir=plan.surface_dir,
            m1_out=M1_OUT,
            autosave_path=AUTOSAVE,
            cycle_summary_path=CYCLE_SUMMARY,
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
    """Close the pass: run retention when a green finish has earned it, then write the one summary block. Retention goes first so its row has an outcome, a figure and lines with somewhere to land — printing them after the table would put the pass's last word below the verdict it belongs to. A retention pass that answers with nothing still leaves the row an outcome: that is the suite's stub, which is what keeps a test reaching a green finish from sweeping the live repo. A green pass closes on the readiness checklist, read after the cycle summary it reads has landed."""
    digest = console.Digest() if emit is None else emit
    retention_lines: list[str] = []
    retention_ran = False
    if not failures and plan.retention and plan.record_greens:
        digest.step_start("retention", None, plan.describe("retention"))
        started = time.perf_counter()
        try:
            pruned = run_retention(plan) or RetentionResult([], "")
            retention_lines = list(pruned.lines)
            report.retention_figure = pruned.figure
            retention_ran = True
        except Exception as exc:
            retention_lines = [f"warning: retention pass failed: {exc!r}"]
        report.step_seconds["retention"] = time.perf_counter() - started
        digest.step_end("retention", None, "ok" if retention_ran else "FAILED", report.retention_figure)
    _emit_cycle_summary(report, failures, plan, "failed" if failures else "ok", timings)
    readiness = [] if failures else readiness_block(plan)
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
) -> int:
    digest = console.Digest() if emit is None else emit
    _emit_cycle_summary(report, failures, plan, "interrupted", timings)
    digest.summary(
        summary_rows(report, plan, retention_ran=False),
        summary_cycle_lines(report, plan, []),
        console.VERDICT_INTERRUPTED,
        [*failures, f"SIGINT: terminated {killed_count} child process(es)"],
    )
    return 130


if __name__ == "__main__":
    sys.exit(main())
