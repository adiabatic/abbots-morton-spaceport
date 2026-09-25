# Testing

This file maps each kind of change to the gate that checks it, and says what each gate fails on. Each rule names its executable authority; read that for the details.

## Which gate to run

| Change                                                     | Gate                                                                       |
| ---------------------------------------------------------- | -------------------------------------------------------------------------- |
| Glyph data or code in the main tree                        | `make test`                                                                |
| Anything under `rebuild/`                                  | `make test-rebuild`                                                        |
| Settlement or proof-fold semantics in `rebuild/kernel-rs/` | `make kernel-gate`, then `make test-rebuild`                               |
| A rune or pipeline edit, to rebuild and recheck the tables | `uv run python -m rebuild.pipeline.run_m1` (or a `make review-cycle` pass) |
| Commit time                                                | `make artifact-cycle`; `make review-cycle` when a sitting follows          |
| Is a sitting ready?                                        | `make verdict-ready`                                                       |
| Periodic, by hand or overnight                             | `make test-rebuild-slow`, `make conform-deep`, `make replay-deep`          |

Never single-thread a broad run; `doc/parallelism.md` has the width rules. Run anything that includes the heavy gates detached; `doc/running-long-steps.md` has the recipe.

## `make test`

- Skips in about a second when nothing it reads has changed since its last passing run. `make_test_exempt` in `rebuild/tools/artifact_cycle.py` defines what is outside its input closure. A change there cannot affect this gate; the rebuild suite or the artifact cycle checks it, so don't force a run for one.
- `make test FORCE=1` runs it regardless.
- Pyright runs in the same invocation over the whole tree. `[tool.pyright] include` in `pyproject.toml` defines what it checks, so every invocation is a bare `uv run pyright` with no paths.
  - Pyright skips on its own green record (`rebuild/out/pyright-green.json`) when nothing it can read has changed since its last passing run. `rebuild/tools/pyright_gate.py` defines that closure: the sources under `[tool.pyright]`'s `include`, `extraPaths`, and `stubPath`, plus `pyproject.toml` and `uv.lock`. Every file is hashed raw except the lock, which is hashed without the project's own block (`rebuild/tools/lock_digest.py`), so a dependency change invalidates the record and the lock's copy of the project version does not.
  - `FORCE=1` runs pyright along with the suite.

## `make test-rebuild`

- Runs the rebuild suite as one lane, `contracts`, with one green record (`rebuild/out/rebuild-contracts-green.json`, shared with the artifact cycle's gate:rebuild-contracts).
- No test under `rebuild/` reads live build output. `rebuild/conftest.py`'s audit guard fails a test that reads a live tree (`rebuild/out/`, `tmp/`, `var/`, the root verdict stores) with `ContractsLaneViolation`. Write a new test against a synthetic root or `tmp_path`. A check on a live artifact belongs in the build that makes the artifact.
- The suite reads only checked-in inputs, including the hermetic mini bundle under `rebuild/review/fixtures/mini/`, which holds the review surface's worked examples, and runs at full width.
- The lane usually runs only some of its tests. Its green record stores a per-test input closure: what each test read, imported, and spawned, recorded by the same audit guard while the test ran. When the lane's key has changed, the run selects only the tests whose closure contains a changed file and prints what it left out; the artifact cycle's plan prints the same line for `gate:rebuild-contracts`. `rebuild/tools/contracts_closure.py` defines what a closure contains and when a test may be left out. Any doubt runs the test, so these always run:
  - a test with no recorded closure, which covers a new or renamed test id;
  - a test that spawned a child the guard cannot follow, with two exceptions: a `git` command that reads only the object store, and the M1 kernel or its `cargo build`, whose closure is the crate's tracked sources;
  - every test, when an input was added or removed, or when a global input such as `uv.lock` or the site fonts changed.
- `make test-rebuild FORCE=1` runs the whole lane and re-records every closure. The green record a narrowed run writes covers the whole lane, because the tests it left out passed against inputs whose bytes have not changed.
- A narrowed run still pays the lane's fixed startup cost, because every worker collects the whole suite before the selection deselects anything. Selection saves only the test time above that cost; `make cycle-timings ARGS='--by-step'` shows it.
- Both conftests are global inputs to every test's closure, because `closure_of` folds their static import closures into each test. Both are therefore limited to leaf imports and reach neither `rebuild/pipeline/` nor `rebuild/review/`, which is what lets an edit to a pipeline or review module leave the cheap unit tests out of the run.
  - `rebuild/tools/cycle_paths.py` and `rebuild/tools/closure_record.py` are the leaf modules the suite patches and records through.
  - Fixtures that need the review tree load it through `announced_import` in `rebuild/conftest.py`.
  - `rebuild/test_contracts_closure.py` pins the import edges of both conftests, so a module-scope import cannot widen them.
- The build checks everything it can about its own artifacts, so the suite does not:
  - The review surface's per-unit checks, `check_unit` and `check_shards` in `rebuild/review/build.py`. A build runs `check_unit` in the worker that drafts a fragment and again in the parent that writes it, each time over the fields settled by then, and runs `check_shards` over the surface read back from disk.
  - The rule certificates the crate writes beside every table's rules. `run_m1`'s witness stage (`run_rule_witnesses`) settles each certificate and asserts that its rule is the first to match at some position in it. It runs on the table-only branch beside the glyph chain, which is joined before the run_m1 gate is decided. `rebuild/test_rule_witnesses.py` tests the witness code on the mini fixture, and `rebuild/test_run_m1_tail.py` tests the branch's join and its error order.
  - The order the settlement lookup ships its rules in. The shipped-order walk on the same branch replays it against every configuration's rows (`run_emitted_order`, over the crate's `replay-emitted` subcommand); `rebuild/test_emitted_order.py` tests it on the same fixture.
  - Every ligature stance's declared outgoing mapping, settled against the formed rune before the tables are built (`run_m1.run_ligature_outgoing` over `rebuild/pipeline/ligature_outgoing_check.py`). `rebuild/test_ligature_outgoing_check.py` tests it on the synthetic fixture, and `rebuild/test_ligature_outgoing.py` tests the loader's inheritance rules. A ligature-local record that loses or moves a join its trailing letter permits fails the build instead of showing up in a sitting.
- No suite test can fail on a stale artifact, because none reads one. After a rune or pipeline edit, run the M1 build to recheck the tables.
- `make test-rebuild` is also the pyright gate for `rebuild/`: `make_test_exempt` exempts that whole tree, so `make test` never checks it.
  - Pyright skips on its own green record here as it does under `make test`, so a run narrowed to a rune edit's tests starts no pyright.
  - When it does start, it runs beside the suite and is joined at session end, so a type error fails the run after the tests finish. The run then exits nonzero with no `FAILED`/`ERROR` summary line, which the wrapper's classifier treats as a hard failure, and the `pyright:` line prints below the pytest summary.
  - A run stopped with Ctrl-C abandons the check without a result, so a pyright killed by the same signal neither counts as a failure nor clears a green record that is still valid.
- Test collection under `rebuild/` skips the crate and the build output (`collect_ignore` in `rebuild/conftest.py`). No test file lives in either, and their trees are nearly everything the collection would otherwise visit in every worker.
- The suite skips slow-marked tests. `make test-rebuild-slow` runs them, at the width its Makefile recipe sets and explains.
- The real-record grammar pins in `rebuild/test_spec_load.py` identify chain-bearing policy records by index, as their provenance paths do. Append new policy records when their semantics allow the existing order. Inserting one changes those indexes even when the older records behave the same.
- Codex's macOS sandbox blocks `sysctl -n hw.memsize`, so `rebuild/test_memory_budget.py::TestTheLiveProbe::test_the_portable_probe_is_byte_identical_to_hw_memsize_on_darwin` fails there even when the probe is correct. If it is the suite's only failure, rerun it outside the sandbox. Never weaken the probe or the test.
- The same sandbox blocks Unix socket binding in `rebuild/test_standing_daemon.py`. A daemon-start traceback ending in `standing_client.bind` with `PermissionError: [Errno 1] Operation not permitted` needs a rerun outside the sandbox. When these sandbox restrictions fail the contracts lane, rerun `make test-rebuild` outside the sandbox so the gate records the result.

## Re-adjudicating without a build

The comparison-side inputs (the alias map, the divergence ledger, the contact allow-list, the kern sidecar, the oracle's two modules, and the baselines) are not part of the tables' stamp, so a change to one of them needs no new enumeration:

```zsh
uv run python -m rebuild.pipeline.run_m1 --gates-only
```

That reruns the defect gate, the Manual-pin gate, and the oracle over the tables and font already on disk, and fails if the tables' stamp is stale. The artifact cycle takes this route itself when everything that changed since the last passing build is comparison-side (`comparison_side_label` in `rebuild/tools/artifact_cycle.py` lists what counts), and the green record it writes lets the next pass skip the build entirely.

The oracle serves what it can from the per-row stores that `rebuild/pipeline/oracle_cache.py` keeps beside the tables. Each row has two verdicts under two keys: the settlement comparison, keyed by the row's rune keys, and the shaped-position verdict, keyed by those plus the font's per-family glyph digests, the kern sidecar, and the position channel's module (`rebuild/pipeline/oracle_positions.py`).

- A ledger, alias, or classifier (`oracle.py`) edit serves both verdicts from the store.
- A kern-sidecar or `oracle_positions.py` edit serves the row verdicts and reshapes the positions.
- A rune edit re-derives only the rows that reach an edited family.

The settle memo that the oracle shares with `gate:conform` and the witness stage is keyed the same way. The string replay fills it on every whole-universe walk, from the window memo the crate already holds (`run_m1.run_replay_strings`, over `conform.absorb_replay_memo`). A rune edit therefore re-settles only the windows naming an edited family, and a cold pass (a code, crate, or comparison-side edit that changes the memo stamp) is cold in the replay, not in the oracle.

The file is laid out for `mmap` and has one writer, `conform._write_settle_memo`, which replaces the whole file through a staging copy, so a reader sees either the old file or the new one. Every walk maps it read-only (`conform._MemoStore`), so each configuration's memo is in the page cache once per machine, not once per worker. A walk that may not replace the file (each row range of the pooled oracle, and the witness stage) writes the windows it settled fresh to a gzip part, which is folded back into the file later (`conform.absorb_settle_memo_parts`). The witness stage's parts are folded in before it returns and the oracle's only after that, because the oracle starts after the string replay alone and runs beside the witness stage (`run_m1.TableGates`).

The run's log reports what each pass served:

- The `[t] oracle` and `[t] settle_memo` lines report what each pass served, retired, and pruned. Above `--jobs 1` there is one pair per row range, labeled `<config> k/n` when a configuration is cut into ranges (`oracle.oracle_shard_plan`). The parent joins the ranges back in row order, so the summary, the audit, and every store match what a `--jobs 1` run writes.
- `oracle_summary.json` records `positions_served` beside `positions_compared`.
- The witness stage's walk loads only the rows its certificate texts can ask for (`conform._SettledWindowWalk.load_only_asked_by`). Its `[t] rule_witnesses[<config>]` line carries `served=`, `unasked=`, and `fresh=`, and `witness_summary.json` records the same three per configuration. `served=` counts the rows the load kept, including stale rows: rows that name a family whose key changed, which the walk never serves. `unasked=` counts the rows the load dropped.

## The kernel's own gate

No cycle gate runs the crate's test suite, so run `make kernel-gate` around any change to kernel semantics. It is the crate's fmt, clippy, and test gate (`make kernel-check`), and takes seconds once the crate is built.

- The proof-fold tests compare the production successor search with the per-producer breadth-first reference for identical parents and distances. They compare the indexed outcome partition with the string reference for identical winning seats, retained rows, refusal text, and certificates. Both references compile only in tests; certificate tail closure keeps the production string matcher as its independent check. [The proof-search issue comment](https://github.com/adiabatic/abbots-morton-spaceport/issues/303#issuecomment-5785733925) maps the acceptance evidence.
- The gate checks the trace memo's byte identity (`rebuild/kernel-rs/src/memo.rs`). A configuration enumerated as a delta over `default`'s memo, and a build seeded from the previous build's memo files behind the edited runes, must each write the same memo bytes as a from-scratch enumeration. The crate's own tests check this, and so does `tests/cli.rs` through the binary. `rebuild/test_kernel_exec.py` checks the same identity on the mini fixture through `run_m1.build_tables`, including the packed memos and the stamp.
- A build writes its memo files beside its tables as `rebuild/out/m1/memo-<config>.tsv.gz`. The next build reads them only while `run_m1.memo_stamp` still matches. The crate is told which runes' content and which predicate classes' membership changed, and it rejects each memoized window whose settlement read one of them. These files are never an input to any gate.

Three more checks compare the crate's tables with the rest of the build:

- The string replay checks the crate's tables against the crate's engine on every M1 build, right after the tables are written (`run_m1.run_replay_strings` over the crate's `replay-strings` subcommand). It walks the persisted rules over the string universe and compares each result with a fresh settlement keyed on the raw window.
  - It walks the whole universe after a code or structure change, and only the texts naming an edited family after a rune edit.
  - It also walks the whole universe whenever a configuration's settle memo file is missing or carries another stamp, because the walk's window memo fills `rebuild/out/m1/settle-memo-<config>.bin` for the witness stage, the oracle, and `gate:conform` to map.
  - `rebuild/out/m1/replay_summary.json` is the record the next build computes its delta against.
- The shipped-order walk (`run_m1.run_emitted_order` over the crate's `replay-emitted` subcommand) checks the emitter's one shipped lookup against the crate's tables. On every build it tries every configuration's rows, first match wins, against the order the lookup ships.
- `gate:conform` checks the tables against the font: a HarfBuzz sweep of the compiled font against the same fresh settlement. `make conform-deep` is its periodic deeper form. The ss10 overlay case sweeps texts of up to two letters, relies on read-back's isolation check, and never goes deeper.

`make replay-deep` (`rebuild/tools/deep_replay.py`) extends the replay one letter past the build's horizon: the same `replay-strings` subcommand at horizon 5, over the texts naming the runes whose content changed since its last recorded walk. It is the cheaper form of the deep sweep's check, and the one gate that sees a fault confined to the deep lookahead slots. It checks rune edits and is never a cycle gate. The artifact cycle reports it `armed` beside the deep sweep once a rune changes, and its module docstring states the cost that keeps it off the per-edit path. A passing `make conform-deep` at horizon 5 or deeper also refreshes its record.

`doc/rebuild-design.md` §10 describes the test tiers and has the fault-coverage table, which records which of these gates failed for each injected fault. §14.1 describes the crate.
