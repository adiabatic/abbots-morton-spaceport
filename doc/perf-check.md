# The speed-up check

`make perf-check` answers the three questions #584 asks of a speed-up candidate:

- **Same output:** every pass the candidate shortens still writes the same tables, fonts, review corpus, verdict store and complaint list.
- **Same detection:** every injected fault whose catching gate the candidate touches is still caught.
- **Proven speed:** on each edit-to-READY scenario, the candidate's runs beat the base's runs by more than the spread between runs.

The module docstring of `rebuild/tools/perf_check.py` is the authority on what a check does; `rebuild/tools/perf_clone.py` documents the scratch clones and the hooks hyperfine runs around each timed run, and `rebuild/tools/perf_fingerprint.py` the output fingerprint. This runbook says how to run a check and how to read what it reports.

## Commands

| Command                                                       | What it does                                                                                                                                        |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `make perf-check ARGS='--dry-run'`                            | Prints the plan, the probes, which scenario patches apply at the base, the fault kit's selection and the estimated wall time; builds nothing        |
| `make perf-check`                                             | Checks this checkout's uncommitted diff against HEAD                                                                                                |
| `make perf-check ARGS='--candidate A..B'`                     | Checks a committed candidate, `git diff A B`, against A                                                                                             |
| `make perf-check ARGS='--scenario NAME'`                      | Runs only the named scenarios (repeatable); `--scenario none` runs only the fault kit                                                               |
| `make perf-check ARGS='--faults all'`                         | Widens the fault kit to every row (`none` skips it, `01,05` names rows; `auto`, the default, picks by path, and takes every row under `--baseline`) |
| `make perf-check ARGS='--baseline'`                           | Measures HEAD against itself (the A/A check) and rewrites this machine's rows in `rebuild/perf/baselines/`                                          |
| `make perf-check ARGS='--baseline --base COMMIT --seed FILE'` | The same at another commit, starting from a kept verdict-store seed                                                                                 |
| `make perf-check ARGS='--write-patch NAME'`                   | Rewrites `rebuild/perf/scenarios/NAME.patch` in place from the scenario's `edit:`                                                                   |
| `make perf-check ARGS='--clean'`                              | Deletes `var/perf/` (no other flag; refuses while a check or a clone's pass holds its lock); the next check bootstraps its clones again             |

A check takes many hours. `--dry-run` prints the estimate for this machine: each scenario's pass time is the median of this checkout's timings journal for its declared shape, and a fingerprint, a cold first pass, the fault kit and a shape the journal lacks are planning figures that `perf_check.estimate` names. Read the total there rather than from a number written down anywhere.

## Running a check

- Run it detached (`doc/running-long-steps.md`), on AC power, with the lid open or in clamshell mode: the driver keeps the machine from idle sleep with `caffeinate -i` for its own lifetime, which does not stop lid-close sleep.
- The live loop waits for it. A cycle pass, a gate, `make test`, a hand `run_m1`, or an agent edit whose hook runs pyright in this checkout makes the check wait before its next run, and a run they overlapped is discarded. So while a check runs, nothing heavy runs in this checkout or anywhere else on the machine.
- The review server on port 7294 keeps running through a check, and the check never stops it (§ Isolation).
- The candidate is captured once, at the start, into the check's record. Untracked, unignored files make the check refuse and name them, because `git diff` would leave them out; `git add -N <path>` takes one in.
- Unset `AMS_TOTAL_MEMORY_BYTES` first. The clone passes run without it, so their run lines carry the machine's own total, and the check refuses to start while it is set.

## The scenarios

`rebuild/perf/scenarios.yaml` is the authority: each scenario's name, the kind of edit it stands for, its form, its edit (a commit and paths), its checked-in patch and its declared shape.

- **Forms.** A `revert` scenario starts from the tree with its edit reversed (`git apply -R`), and the timed edit re-applies it, which lands exactly on the tree. A `forward` scenario starts from its own fixed `base` and applies its patch. The `none` scenario times a pass over the tree itself, and its declared shape is also the settle rule (§ The clones).
- **The moving base.** Every check runs at B, the candidate's own base, and measures B and B plus the candidate side by side. Before anything runs, the revert-form patches are checked at B on a temporary index: each must reverse, they must compose, and each must re-apply on the result. A scenario that fails there is reported `not applicable at B: re-aim`; one that the candidate's own changes stop from reversing is `not applicable: the candidate touches its edit`. The rest run.
- **The composite roots.** Each side's root is its composite, the tree with every revert-form edit reversed. Each scenario's start state is warmed from that root, so no start state's caches have seen the tree its own edit lands on.
- **The letter migration.** No `Add ·X` batch reverts cleanly at a later head, so that scenario replays forward at its own parent. It takes a candidate only when the candidate's paths, less the harness's own files and Markdown, are unchanged between that parent and B, and the candidate less those files applies there; otherwise it is reported `not measured`. Its candidate side is that parent plus the candidate less those files. When the next `Add ·X` batch lands, the scenario moves onto B: set `form: revert`, point `edit:` at that batch, drop `base:` and `seed:`, and run `--write-patch letter-migration`.
- **Re-aiming.** When a patch stops reversing at B, or a base batch misses its declared shape (`shape drifted: re-aim`), pick the newest first-parent commit of the same kind whose edit reverts cleanly at B, update the scenario's `edit:` in `scenarios.yaml`, and run `--write-patch NAME`. It rewrites the patch in place and refuses when the edit does not resolve, so a history rewrite never replaces a checked-in patch. `--dry-run` shows which patches fit at B.

## The timed span and how a run counts

A timed run starts when hyperfine launches `make -C <clone> artifact-cycle ARGS=--stop-server` under a whitelisted environment, with the edit already applied by the prepare hook, and ends when that process exits. The run counts only when its harvested `terminal.log` ends READY: a `READY - adjudicate at` line followed by `Cycle complete.`. make exits 2 for any failed recipe, so the log decides:

| The log shows                          | The run is  | What happens                                                          |
| -------------------------------------- | ----------- | --------------------------------------------------------------------- |
| READY, then `Cycle complete.`          | ready       | It counts                                                             |
| `REFUSING TO RUN`                      | refused     | The batch is discarded and rerun once the probes clear                |
| `CYCLE INTERRUPTED:`                   | interrupted | The batch is discarded and rerun, unless the check itself is stopping |
| `CYCLE FAILED:`, or no READY on exit 0 | failed      | The scenario fails, and the report quotes the log's last lines        |
| none of these, on a nonzero exit       | harness     | The scenario fails (make, uv or the interpreter failed first)         |

`--stop-server` drops the readiness checklist's server row, which the main checkout's server would fail in a clone; against a server from another checkout it does nothing else.

## The clones

A check works only in scratch clones under `var/perf/`: `shared/` for every scenario at B, and one clone named after the letter migration's base. A clone is bootstrapped once (clone, `uv sync --frozen`, `make kernel-build`, each timed in `var/keep/perf/bootstrap-<clone>-<stamp>.json`), and its first pass is a cold first-run pass.

- **Preparing a side's root:** reset to the composite, `make all` so the site fonts are this side's, bring the old-font baselines in line with them (kept, copied from this checkout's `rebuild/out` when their headers name the same font, or extracted), seed the verdict store (base side only), warm until settled, delete every stash, and snapshot. The base side's root carries over from check to check under `var/perf/shared.snap/root/`. The report lists each root warm's passes with their carry counts and orphan share, and the disk each clone and snapshot directory takes.
- **The seed.** The base side's store is this checkout's store as of B's committer time, rebuilt from the verdict journal with `merge_verdicts --restore-as-of` (read-only; it writes only into the clone's `tmp/`). The restore runs in the clone at this checkout's HEAD commit, so no uncommitted code reads the live journal. It is scrubbed of every fill whose leading `[standing: …]` marker names a rule that B's `rebuild/standing-approvals.yaml` has and the composite's lacks or holds differently, and of every duplicate fill copied from a scrubbed unit, because a fill is never retracted and a timed standing-approval edit would otherwise fill nothing; the root warm's standing fill refills what the composite's rules match. The root warm's carry then orphans every verdict on a unit only a post-edit tree has. The letter migration's seed and each `--baseline`'s are kept as `var/keep/perf/seeds/<sha256>.json`; a paired check's seed is scratch, deleted once the base side's root is prepared. The letter migration's seed is fixed: once its first bootstrap has made one, record its `as_of` and `sha256` as the scenario's `seed:` in `scenarios.yaml`, and later bootstraps use that file.
- **Settling.** A state is warm once a pass over it is READY and its run line holds the `none` scenario's declared shape. A start state settles in two passes and a cold root in three (a first-run pass, the carry, then the settling pass), and each limit, `perf_check.START_WARM_PASSES` and `ROOT_WARM_PASSES`, allows one more. A green pass that is not yet READY, as a first-run pass is, counts toward the limit. A refused or interrupted pass is the machine's doing: it is run again once the probes clear, at most `perf_check.BATCH_RETRIES` times, and does not count. A failed pass, or a start state that never settles, fails that scenario with `its start tree does not build green at B: re-aim`; a root that does not warm stops the check.
- **Snapshots** hold `rebuild/out/` but its timings journal, the clone's `var/` and `tmp/`, the site fonts, the verdict store and its stashes, and the verdict journal. Each restore puts them back with APFS clonefile copies, and puts the verdict journal back on its own inode, so the retention pass never rescans it; a run whose restore had to replace the journal's inode is counted as a rescan in the report.
- **Cleanup.** After a scenario's pair its start snapshots are deleted; at the end of a check only each clone's root snapshot is kept, and each clone is left at its base root. A clone whose bootstrap failed or was stopped is bootstrapped again. `--clean` deletes all of `var/perf/`.

### Isolation

- Every path the cycle uses derives from its own file's location, so a pass in a clone reads and writes only the clone: its pass lock, green records, journals, standing socket, build logs and verdict store.
- The one write outside a clone is the `tempfile` scratch `run_m1`, `kernel_exec` and `run_m1_gates` make under the inherited `TMPDIR`; a killed batch can leave some there. `TMPDIR` stays where it is, because a Unix socket path a child makes under a deep directory in this checkout could pass macOS's path limit.
- The review server: a clone's pass probes it as a server for another checkout and leaves it alone. Its `--stop-server` flag would stop review servers machine-wide with `pkill` only for a server from before the land protocol or one rooted in the clone, so every pass is preceded by the clone's own server probe, run after the edit is applied so it runs the pass's own code, and the check waits unless that answers `foreign` or `none`. The `pkill` a clone pass finds first on its `PATH` is a stand-in under `var/perf/bin/` that stops nothing, so a stop the pass's own probe asks for times out and the pass refuses. A clone pass sends the server no reload (`cycle_paths.REVIEW_RELOAD_OFF_ENV`), even one started after the pass planned; the letter migration's clone, at a parent that predates that switch, can send one only to a server started during one of its passes while none listened when it planned.
- Every clone command runs under `env -i` with `HOME`, `USER`, `LOGNAME`, `LANG`, `TMPDIR`, `PATH` behind `var/perf/bin/`, `UV_CACHE_DIR` pointing at this checkout's uv cache, and the reload switch, which drops every variable that changes outputs, widths or what the journal records. The fault kit passes the same names through. Every git command the check and the kit run drops the caller's `GIT_*` variables, so an inherited `GIT_DIR` or `GIT_INDEX_FILE` cannot point it at this checkout's repository or index.
- The only writes to this checkout's tree are `rebuild/perf/baselines/*.json` on `--baseline` and `rebuild/perf/scenarios/*.patch` on `--write-patch`, beside `var/perf/` and `var/keep/perf/`.

## Probes and waits

The probes run before every pass the driver starts and around every timed run, and each result is appended to the record's `system.ndjson`.

| Probe                                                                                                                          | Refuses                                     | Waits, or marks a finished run disturbed             |
| ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------- | ---------------------------------------------------- |
| Low Power Mode (`pmset -g`)                                                                                                    | —                                           | `powermode 1`                                        |
| Thermal state (`pmset -g therm`)                                                                                               | —                                           | any line but the "No … has been recorded" notes      |
| Memory pressure (`kern.memorystatus_vm_pressure_level`)                                                                        | —                                           | a level other than 1                                 |
| Power source (`pmset -g batt`)                                                                                                 | —                                           | not on AC power                                      |
| Other heavy work: this checkout's pass lock, the standing daemon, a machine-wide process scan, this checkout's timings journal | —                                           | any of them, or the journal growing during a run     |
| Any of `pmset -g`, `pmset -g therm` and `ps` that cannot run                                                                   | —                                           | always, until it runs                                |
| The clone's review-server probe                                                                                                | —                                           | anything but `foreign` or `none` (before a run only) |
| Free disk                                                                                                                      | under `perf_check.DISK_FLOOR_BYTES`         | —                                                    |
| Tools: hyperfine, rustc, uv, git, typst, the OS build                                                                          | a missing tool, or hyperfine other than 2.x | —                                                    |

A refusing probe stops the check with exit 2, before it measures anything or partway through. A waiting one makes the check poll every `perf_check.POLL_S` until it clears, and a wait longer than `perf_check.WAIT_LIMIT_S` stops the check with exit 2, naming the condition. After a run, the same conditions mark it disturbed, and its batch is discarded and rerun, at most `perf_check.BATCH_RETRIES` times. The process scan leaves out the check's own processes, and it is machine-wide on purpose: another project's tests take the same cores. It matches a process by its executable (`perf_check.HEAVY_EXECUTABLES`) or by a Python interpreter's module or script (`HEAVY_MODULES`), never by a name in its arguments, so a search, a shell command or an editor that only names a heavy tool does not count. The load average is recorded only. A `rustc` or OS build that changes inside a pair leaves that scenario with no verdict. What the probes cannot see is a heavy process that starts and ends inside one run and writes nothing to this checkout's journal; the pairing and the A/A spread bound what it can do to a verdict.

## Statistics and the comparison rule

Each batch is one warm-up and `--runs` timed runs, never fewer than `perf_check.MIN_RUNS`. It is reported as median ± MAD (min–max), the MAD unscaled. The two batches of a scenario run back to back, base first for odd-numbered scenarios and the candidate first for even ones, and the verdict compares only those two:

- **proven faster:** every candidate run beats every base run;
- **proven slower:** every base run beats every candidate run;
- **within spread:** anything else;
- **no verdict:** the two batches ran under different `rustc` or OS builds.

Complete separation of 5 runs against 5 is the exact one-sided rank test at level 1/252. A pair whose only failure is a separation that fails the check, `proven slower` or either separation under `--baseline`, is measured once more from the same start states, and the second pair's result stands, with the first pair's verdict beside it, so a chance separation across many scenarios fails a check only by repeating. The report gives the saving in the hyperfine medians and in the journal's `wall_s` medians, so a saving confined to planning shows as such, beside each side's critical path (`cycle_timings.critical_path`).

## The journal check and the pass shape

Each timed run must match exactly one run line in the clone's timings journal, inside the run's window, with `exit: ok`, `--stop-server` in its argv, a plan that is not a first run and this machine's tag. The gap between hyperfine's time and the line's `wall_s` covers make and uv startup, planning and the readiness checklist, so it must be positive and stay within `perf_check.GAP_RANGE_S` across a batch. The first `--baseline` reports every scenario's observed gap range, and `GAP_RANGE_S` is set from the largest of them.

`perf_check.pass_shape` splits a run line's plan three ways: the stage flags, each gate's `green` and `skip`, and the carry counts are compared; the pool widths are reported and never fail (a candidate that re-measures a `*_BYTES` constant moves them); the path and HEAD keys are ignored. A plan key in none of the lists is reported as new. The shape must not vary within a batch, the base batch must hold the scenario's declared shape, and the candidate's compared part must equal the base's, so a candidate that saves time by skipping a step it should run shows as a named difference.

## The output fingerprint

Each side is fingerprinted after its last run by `rebuild/tools/perf_fingerprint.py`, run with the clone's own interpreter and code: every configuration's `table_digest`, the compiled and site fonts by content digest, the review corpus file by file without its stamps, the verdict store's records, the complaint list, and the review-facts pins the pass rewrote. The two stores (`unit-cache`, `ink-signatures`) are reported apart: a difference there is an intermediate format differing, not a failure, except under `--baseline`, whose two sides run the same code. The script's docstring names the projected keys; the first `--baseline` confirms them, since its two sides must fingerprint identically. Every revert-form scenario lands on the same tree as `nothing-changed`, so their fingerprints should match too; a difference is a missed stamp or a determinism bug.

## The fault kit

`rebuild/perf/faults.yaml` is the authority on the kit's rows: each row's patch, the gates that catch it, and the steps that reach them at the current head. `rebuild/tools/fault_kit.py` runs it; a check calls it on the candidate side's tree in the shared clone, with `--rows auto` selecting every row whose catching gates (their files, their modules' import closure, and the crate when a gate rests on it) the candidate's paths touch. `make perf-check ARGS='--baseline --scenario none'` re-injects every row at HEAD with no candidate and writes no baseline; with a candidate, `--scenario none --faults all` re-injects every row on the candidate's tree.

| Result        | Meaning                                                                                                |
| ------------- | ------------------------------------------------------------------------------------------------------ |
| `caught`      | Every step met its expected exit and its required text                                                 |
| `inert`       | The fault did nothing on this tree (exit 0, effect files unchanged)                                    |
| `missed`      | The fault took effect and its gate let it through                                                      |
| `wrong-catch` | Another exit, a timeout, or a missing required text                                                    |
| `stale-patch` | The row's patch does not apply                                                                         |
| `lost`        | The base catches it and the candidate does not: the candidate lost or changed the catch                |
| `re-author`   | The base catches it and the candidate moved the injection site: re-author the patch in the same change |
| `drifted`     | The base does not catch it either: the row no longer fits the head, whatever the candidate did         |

The first step that misses its expectation decides a row's result, since a later step can run on outputs an earlier one never rewrote. To re-author a row, apply the old patch's intent by hand in a clone of the tree it should fit, then write `git diff` over the row's patch file.

No row reaches the witness stage or the fold's certificate check. Row 01 stops at the string replay, which `run_m1` raises before the witness stage starts, and row 03 stops in the enumeration before the fold runs. So a change that touches either gate's code is reported `unreached: witness` or `unreached: fold` beside the rows it runs; `doc/rebuild-design.md` §10 records the gap.

## What a check keeps

- **Committed:** `rebuild/perf/scenarios.yaml` and its patches, `rebuild/perf/faults.yaml` and its patches, each scenario's `rebuild/perf/baselines/<name>.json`, the code, the tests and this runbook.
- **Kept under `var/keep/perf/`, never committed:** one `<stamp>-<id>/` per check, holding `report.json` and `report.txt`, `system.ndjson`, the captured candidate, the logs of every untimed pass, the fault kit's `faults.json` and logs, and per scenario and side `hyperfine.json`, `fingerprint.json` and `runs/<k>/` (the run's stamp, its build logs and its journal lines), with a confirming pair under `<scenario>.confirm/`; the bootstrap records; and the kept seeds.
- **Scratch:** `var/perf/`, the clones and their snapshots.

A baseline row holds the machine tag (`cpu_count` and `mem_total_bytes`, printed `cpus=… ram=…GB` as `doc/fleet.md` matches machines), the toolchain, the numbers, the compared pass shape, and each probe's outcome over the batch, `ok` or `waited`. Process names, host names, raw probe output and paths stay in the record under `var/keep/perf/`.

## Another machine

Another machine adds its own rows with the same scenarios and patches: copy this machine's kept seed for the base being measured into its `var/keep/perf/seeds/`, then run `make perf-check ARGS='--baseline --base <commit> --seed <file>'` there. `--baseline` compares each new fingerprint with every other machine's row at the same base before it writes. With the same seed the fingerprints must be equal, stores included; without it, the verdict store and complaint list are left out of the comparison and everything else must still match. A difference is a determinism finding: the check names it and writes no baseline file. Otherwise it replaces only that machine's rows.

## Reading a clone's journal

Each clone keeps its own timings journal, which the main checkout's `make job-costs` never reads. Read one with `make cycle-timings ARGS='--journal var/perf/shared/rebuild/out/cycle-timings.ndjson'`.

## Stopping a check

Stop a check with the group signal from `doc/running-long-steps.md` § Stopping a pass. A check launched under `nohup` keeps SIGHUP ignored, so it outlives its shell. The driver lets the current step end (a cycle pass in the group ends interrupted, and a wait for the machine to be quiet ends at its next poll), waits until no land holds a clone's pass lock, since a clone's land runs in its own session and no signal reaches it, writes what it has to the report, and exits 128 plus the signal's number. Don't look for a clone's land with `pgrep -f "rebuild.review.landin[g]"`: that also matches this checkout's land. The next check restores every clone from its snapshots, so a stopped check leaves nothing to clean by hand.
